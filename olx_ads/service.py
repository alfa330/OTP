# -*- coding: utf-8 -*-
"""Раздел «Объявления OLX»: синхронизация, запись в OLX, история, откат, ИИ.

Три правила, которым подчинён весь модуль
-----------------------------------------

1. **Сетевой вызов никогда не идёт внутри транзакции.** Поход в OLX занимает
   секунды, прогон по 281 объявлению — минуты. Держать соединение к базе всё это
   время значит съесть пул: ровно эта ошибка разбиралась в olx_amo (таймаут
   amoCRM 60 с × 9 кабинетов держал четверть пула). Поэтому база трогается
   короткими транзакциями МЕЖДУ запросами.

2. **Запись в OLX — read-modify-write, и снимок перечитывается прямо перед ней.**
   У Partner API нет PATCH: `PUT` заменяет объект целиком. Собирать тело из
   кеша, снятого час назад, — значит откатить чужую правку, сделанную в кабинете
   руками. Поэтому перед каждой записью объявление читается заново.

3. **История пишется ВСЕГДА и СРАЗУ после ответа OLX** — и на успех, и на отказ.
   Отдельной транзакцией, а не вместе со снимком: если запись снимка почему-то
   упадёт, факт того, что мы изменили чужую систему, обязан остаться записанным.
   Урок из инцидента #223 (01.09.2026): отметку «сделано» надо ставить вокруг
   внешнего действия, иначе сбой после действия превращает его в потерянное.
"""

import logging
import os
import time
from datetime import datetime, timedelta

from olx_amo import cabinets as olx_cabinets
from olx_amo.olx_client import (OlxAuthError, OlxClient, OlxError, OlxRateLimited)
from olx_amo.service import ensure_access_token

from . import ai, queries, validate

log = logging.getLogger(__name__)

# Пауза между записями в OLX. ТЗ (разд. 6) просит её настраиваемой; смысл не в
# лимите — 4500 запросов за 5 минут мы не выберем и близко, — а в том, чтобы не
# выглядеть пулемётом в глазах антифрода площадки.
APPLY_PAUSE_SECONDS = float(os.getenv('OLX_ADS_APPLY_PAUSE', '0.7'))

# Сколько объявлений разрешено менять за одно нажатие. Предохранитель от
# случайного «выделить все и применить» с невыверенным текстом.
MAX_BULK = int(os.getenv('OLX_ADS_MAX_BULK', '400'))

# OLX отдаёт время В UTC и БЕЗ пометки о зоне, строкой '2026-09-02 16:50:34'.
# Принять это за местное время — ровно тот баг, который стоил #223 семи
# потерянных обращений (02.09.2026). Приводим к алматинским настенным часам, в
# которых живёт весь остальной портал.
_ALMATY_OFFSET = timedelta(hours=5)

_SNAPSHOT_TTL_SECONDS = int(os.getenv('OLX_ADS_SNAPSHOT_TTL', '900'))


class AdsError(RuntimeError):
    """Отказ, который надо показать человеку словами, а не пятисоткой."""

    def __init__(self, message, code='error'):
        super(AdsError, self).__init__(message)
        self.code = code


# ─────────────────────────────────────────────────────────────────────────────
# Справочники OLX: город и категория по id
# ─────────────────────────────────────────────────────────────────────────────
# Значения неизменные, а объявлений с одним городом — десятки. Кешируем на
# процесс: 24 города и 5 категорий превращаются из 284 запросов в 29.

_CITY_CACHE = {}
_CATEGORY_CACHE = {}


def _lookup(client, cache, path, key):
    if key in (None, ''):
        return None
    key = int(key)
    if key in cache:
        return cache[key]
    try:
        data, _, _ = client._request('GET', '%s/%s' % (path, key))
        name = (data or {}).get('name')
    except OlxError:
        # Название — украшение списка, а не условие работы: без него объявление
        # всё равно видно и правится.
        name = None
    cache[key] = name
    return name


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(str(value)[:19], fmt) + _ALMATY_OFFSET
        except ValueError:
            continue
    return None


def _attribute(advert, code):
    for item in advert.get('attributes') or []:
        if item.get('code') == code:
            return item.get('value')
    return None


def _row_from_advert(advert, client):
    return {
        'advert_id': str(advert.get('id')),
        'status': advert.get('status'),
        'title': advert.get('title'),
        'description': advert.get('description'),
        'url': advert.get('url'),
        'category_id': advert.get('category_id'),
        'category_name': _lookup(client, _CATEGORY_CACHE, '/categories',
                                 advert.get('category_id')),
        'city_id': (advert.get('location') or {}).get('city_id'),
        'city_name': _lookup(client, _CITY_CACHE, '/cities',
                             (advert.get('location') or {}).get('city_id')),
        'company_name': _attribute(advert, 'company_name'),
        'phone': (advert.get('contact') or {}).get('phone'),
        'activated_at': _parse_dt(advert.get('activated_at')),
        'valid_to': _parse_dt(advert.get('valid_to')),
        'auto_extend': advert.get('auto_extend_enabled'),
        'payload': advert,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Клиент кабинета
# ─────────────────────────────────────────────────────────────────────────────

def _client_for(db, cabinet):
    """Клиент кабинета с живым токеном. `reserved=True` — это действие человека.

    Раздел работает по нажатию, а не в фоне, поэтому его запросы берутся из
    неприкосновенного остатка бюджета: фоновый опрос чатов не должен оставить
    человека без ответа.
    """
    token, blocked = ensure_access_token(db, cabinet)
    if not token:
        raise AdsError('Кабинет «%s» сейчас недоступен (%s)'
                       % (cabinet.code, blocked or 'не настроен'),
                       code=blocked or 'not_configured')
    return OlxClient(token_provider=lambda: token, reserved=True)


def _cabinet(code):
    cabinet = olx_cabinets.get(code)
    if not cabinet:
        raise AdsError('Кабинета «%s» нет в справочнике' % (code,), code='unknown_cabinet')
    return cabinet


# ─────────────────────────────────────────────────────────────────────────────
# Синхронизация снимка
# ─────────────────────────────────────────────────────────────────────────────

def sync_cabinet(db, cabinet):
    """Перечитать все объявления кабинета в снимок. Возвращает счётчики."""
    client = _client_for(db, cabinet)
    adverts = client.all_adverts()

    rows = [_row_from_advert(advert, client) for advert in adverts]

    # Транзакция открывается ПОСЛЕ похода в OLX и держится только на запись.
    with db._get_cursor() as cursor:
        for row in rows:
            queries.upsert_advert(cursor, cabinet.code, row)
        removed = queries.drop_missing_adverts(
            cursor, cabinet.code, [row['advert_id'] for row in rows])

    active = sum(1 for row in rows if row.get('status') == 'active')
    return {'cabinet': cabinet.code, 'total': len(rows), 'active': active,
            'removed': removed}


def sync_all(db, only=None):
    """Синхронизировать все настроенные кабинеты. Отказ одного не роняет остальные."""
    out, errors = [], []
    for cabinet in olx_cabinets.CABINETS:
        if only and cabinet.code not in only:
            continue
        if not cabinet.is_configured():
            continue
        try:
            out.append(sync_cabinet(db, cabinet))
        except (AdsError, OlxError) as exc:
            log.warning('Объявления OLX: кабинет %s не синхронизирован: %s',
                        cabinet.code, exc)
            errors.append({'cabinet': cabinet.code, 'error': str(exc)})
    return {'cabinets': out, 'errors': errors,
            'total': sum(item['total'] for item in out),
            'active': sum(item['active'] for item in out)}


def snapshot_is_stale(synced_at):
    """Пора ли перечитать снимок. Нужно, чтобы раздел не открывался пустым."""
    if not synced_at:
        return True
    return (queries.now_almaty() - synced_at).total_seconds() > _SNAPSHOT_TTL_SECONDS


# ─────────────────────────────────────────────────────────────────────────────
# Запись в OLX
# ─────────────────────────────────────────────────────────────────────────────

def build_update_body(payload, title, description):
    """Тело PUT из прочитанного объявления: меняем два поля, остальные возвращаем.

    Что здесь важно и проверено на живом 14.09.2026:

    * обязательны title, description, category_id, advertiser_type, contact,
      location, attributes — без любого из них OLX отвечает 400 с разбором полей;
    * `auto_extend_enabled` НЕ передаём вовсе: по документации отсутствие поля
      означает «не менять». Передать его — значит рискнуть автопродлением 281
      объявления ради поля, которое мы и не собирались трогать;
    * у атрибутов отдаём только заполненную половину пары value/values: ключи со
      значением null OLX считает попыткой задать пустое значение;
    * `experience_seeker` и `education` схема категории помечает обязательными, а
      в живых объявлениях их нет ни у одного — и PUT без них ПРОХОДИТ. Поэтому
      ничего не досочиняем: раздел меняет текст, а не заполняет карточку за
      маркетолога.
    """
    payload = payload or {}
    body = {
        'title': title,
        'description': description,
        'category_id': payload.get('category_id'),
        'advertiser_type': payload.get('advertiser_type'),
        'contact': {k: v for k, v in (payload.get('contact') or {}).items()
                    if v is not None},
        'location': {k: v for k, v in (payload.get('location') or {}).items()
                     if v is not None},
        'attributes': [],
    }
    for attribute in payload.get('attributes') or []:
        one = {'code': attribute.get('code')}
        if attribute.get('value') is not None:
            one['value'] = attribute['value']
        if attribute.get('values') is not None:
            one['values'] = attribute['values']
        body['attributes'].append(one)

    if payload.get('salary'):
        body['salary'] = payload['salary']
    if payload.get('price'):
        body['price'] = payload['price']
    if payload.get('images'):
        body['images'] = [
            image if isinstance(image, dict) else {'url': image}
            for image in payload['images']]
    return body


def _olx_problem(exc):
    """Читаемая причина отказа OLX. У ошибок два разных конверта — разбираем оба."""
    payload = getattr(exc, 'payload', None) or {}
    error = payload.get('error') if isinstance(payload, dict) else None
    if isinstance(error, dict):
        validation = error.get('validation')
        if isinstance(validation, list) and validation:
            parts = []
            for item in validation:
                field = item.get('field') or ''
                title = item.get('title') or item.get('detail') or ''
                parts.append(('%s: %s' % (field, title)).strip(': '))
            return '; '.join(parts)
        detail = error.get('detail') or error.get('message') or error.get('title')
        if detail:
            return str(detail)
    return str(exc)


def apply_text(db, cabinet_code, advert_id, title, description, *,
               actor_id=None, actor_name=None, source='single', origin='human',
               run_id=None, skip_validation=False):
    """Заменить текст одного объявления в OLX и записать это в историю.

    Возвращает словарь результата. Исключение наружу НЕ бросается на отказ OLX:
    прогон по сотням объявлений не должен вставать из-за одного, а причина
    попадает и в ответ, и в историю.
    """
    cabinet = _cabinet(cabinet_code)
    title = (title or '').strip()
    description = (description or '').strip()

    client = _client_for(db, cabinet)

    # Перечитываем объявление прямо сейчас: тело PUT собирается из СВЕЖЕГО
    # объекта, иначе мы вернём в OLX поля, снятые в кеш час назад, и затрём
    # чужую правку, сделанную в кабинете руками.
    try:
        fresh = client.advert(advert_id)
    except OlxError as exc:
        return _record_failure(db, cabinet_code, advert_id, None, title, description,
                               _olx_problem(exc), actor_id, actor_name, source,
                               origin, run_id)

    problems = validate.check(title, description, fresh.get('category_id'))
    if not skip_validation and validate.blocking(problems):
        message = '; '.join(p['message'] for p in validate.blocking(problems))
        return _record_failure(db, cabinet_code, advert_id, fresh, title, description,
                               'Текст не проходит правила OLX: %s' % (message,),
                               actor_id, actor_name, source, origin, run_id)

    body = build_update_body(fresh, title, description)

    try:
        updated = client.update_advert(advert_id, body)
    except (OlxAuthError, OlxRateLimited, OlxError) as exc:
        return _record_failure(db, cabinet_code, advert_id, fresh, title, description,
                               _olx_problem(exc), actor_id, actor_name, source,
                               origin, run_id)

    # OLX принял. История — ПЕРВОЙ и отдельной транзакцией: факт изменения чужой
    # системы обязан остаться записанным, даже если обновление снимка упадёт.
    with db._get_cursor() as cursor:
        entry_id = queries.add_history(
            cursor, run_id=run_id, cabinet_code=cabinet_code, advert_id=advert_id,
            advert_url=fresh.get('url'), actor_id=actor_id, actor_name=actor_name,
            source=source, origin=origin,
            old_title=fresh.get('title'), new_title=title,
            old_description=fresh.get('description'), new_description=description,
            result='applied')

    with db._get_cursor() as cursor:
        queries.upsert_advert(cursor, cabinet_code,
                              _row_from_advert(updated or fresh, client))
        draft = queries.get_draft(cursor, cabinet_code, advert_id)
        if draft:
            queries.set_draft_status(cursor, draft['id'], 'applied')

    return {'ok': True, 'cabinet': cabinet_code, 'advert_id': str(advert_id),
            'history_id': entry_id, 'problems': problems}


def _record_failure(db, cabinet_code, advert_id, fresh, title, description,
                    message, actor_id, actor_name, source, origin, run_id):
    fresh = fresh or {}
    with db._get_cursor() as cursor:
        entry_id = queries.add_history(
            cursor, run_id=run_id, cabinet_code=cabinet_code, advert_id=advert_id,
            advert_url=fresh.get('url'), actor_id=actor_id, actor_name=actor_name,
            source=source, origin=origin,
            old_title=fresh.get('title'), new_title=title,
            old_description=fresh.get('description'), new_description=description,
            result='failed', error_text=message[:2000])
    log.warning('Объявления OLX: %s/%s не обновлено: %s',
                cabinet_code, advert_id, message)
    return {'ok': False, 'cabinet': cabinet_code, 'advert_id': str(advert_id),
            'history_id': entry_id, 'error': message}


def apply_many(db, items, *, actor_id=None, actor_name=None, origin='human'):
    """Применить текст пачкой. Одно нажатие — один прогон в журнале.

    `items` — [{cabinet, advert_id, title, description}, ...]. Отказ на одном
    объявлении не останавливает остальные: причина ложится в историю, а прогон
    идёт дальше. Останавливаться имеет смысл только на отказе, который повторится
    у всех, — таких два: кончился токен и площадка включила ограничение частоты.
    """
    items = list(items or [])
    if not items:
        raise AdsError('Не выбрано ни одного объявления', code='empty')
    if len(items) > MAX_BULK:
        raise AdsError('За один раз можно обновить не больше %d объявлений'
                       % (MAX_BULK,), code='too_many')

    with db._get_cursor() as cursor:
        run_id = queries.start_run(cursor, 'bulk', actor_id, actor_name, len(items))

    applied, failed, results, stopped = 0, 0, [], None
    for index, item in enumerate(items):
        try:
            outcome = apply_text(
                db, item['cabinet'], item['advert_id'],
                item.get('title'), item.get('description'),
                actor_id=actor_id, actor_name=actor_name,
                source='bulk', origin=origin, run_id=run_id)
        except AdsError as exc:
            # Кабинет целиком недоступен — дальше по нему идти незачем, но
            # другие кабинеты в пачке ещё могут отработать.
            outcome = {'ok': False, 'cabinet': item.get('cabinet'),
                       'advert_id': str(item.get('advert_id')), 'error': str(exc)}
        except OlxRateLimited as exc:
            stopped = ('Площадка включила ограничение частоты — прогон остановлен, '
                       'повторите позже (%s)' % (exc,))
            break

        results.append(outcome)
        if outcome.get('ok'):
            applied += 1
        else:
            failed += 1

        if APPLY_PAUSE_SECONDS and index + 1 < len(items):
            time.sleep(APPLY_PAUSE_SECONDS)

    with db._get_cursor() as cursor:
        queries.finish_run(cursor, run_id, applied, failed,
                           status='failed' if stopped else 'done',
                           error_text=stopped)

    return {'run_id': run_id, 'total': len(items), 'applied': applied,
            'failed': failed, 'stopped': stopped, 'results': results}


def rollback(db, history_id, *, actor_id=None, actor_name=None):
    """Вернуть текст, который стоял до правки.

    Откат — такая же правка: он идёт тем же путём, проходит те же проверки и сам
    попадает в историю (result='rolled_back'). Прятать его значило бы завести
    изменение объявления, которого в истории нет.
    """
    with db._get_cursor() as cursor:
        entry = queries.get_history_entry(cursor, history_id)
    if not entry:
        raise AdsError('Записи истории не найдено', code='not_found')
    if entry.get('result') != 'applied':
        raise AdsError('Откатывать нечего: эта правка не была применена',
                       code='not_applied')
    if not (entry.get('old_title') and entry.get('old_description')):
        raise AdsError('Прежний текст не сохранён — откатить нечем', code='no_backup')

    outcome = apply_text(
        db, entry['cabinet_code'], entry['advert_id'],
        entry['old_title'], entry['old_description'],
        actor_id=actor_id, actor_name=actor_name,
        source='rollback', origin='human',
        # Прежний текст уже стоял в OLX, значит площадка его принимала. Если
        # правила с тех пор ужесточились, отказ придёт от самого OLX — а вот
        # запретить возврат СВОЕЙ же прошлой версии нашей проверкой было бы
        # неверно: человек чинит аварию, а мы не пускаем.
        skip_validation=True)

    if outcome.get('ok'):
        with db._get_cursor() as cursor:
            cursor.execute(
                "UPDATE olx_ads_history SET result = 'rolled_back' WHERE id = %s",
                (outcome['history_id'],))
    return outcome


# ─────────────────────────────────────────────────────────────────────────────
# ИИ
# ─────────────────────────────────────────────────────────────────────────────

def generate_drafts(db, targets, *, instruction=None, actor_id=None,
                    actor_name=None, generate_fn=None):
    """Сочинить черновики для выбранных объявлений.

    В OLX при этом НИЧЕГО не уходит: черновик живёт в портале, пока человек не
    нажмёт «Применить». Это и есть смысл разделения прав — готовить текст может
    маркетолог, отправлять его в OLX может не каждый.
    """
    targets = list(targets or [])
    if not targets:
        raise AdsError('Не выбрано ни одного объявления', code='empty')

    with db._get_cursor() as cursor:
        adverts = []
        for item in targets:
            advert = queries.get_advert(cursor, item['cabinet'], item['advert_id'])
            if advert:
                adverts.append(advert)
        # У каждого кабинета свой бриф (решение владельца 14.09.2026), поэтому
        # бриф берётся по кабинету объявления, а не один на всю пачку.
        briefs = queries.active_briefs_for(
            cursor, [advert['cabinet_code'] for advert in adverts])

    if not adverts:
        raise AdsError('Выбранных объявлений нет в списке — обновите его',
                       code='not_found')
    if not briefs:
        raise AdsError('У кабинетов выбранных объявлений нет действующего брифа — '
                       'задайте его во вкладке «Бриф месяца»', code='no_brief')

    made, failed, used = [], [], {}
    for index, advert in enumerate(adverts):
        brief = briefs.get(advert['cabinet_code'])
        if not brief:
            # Не роняем пачку из-за одного кабинета без брифа: остальные
            # объявления ИИ напишет, а этим честно скажем, чего не хватило.
            failed.append({'cabinet': advert['cabinet_code'],
                           'advert_id': advert['advert_id'],
                           'error': 'у кабинета нет действующего брифа'})
            continue
        used[brief['id']] = brief.get('title')
        try:
            result = ai.generate_for_advert(advert, brief=brief,
                                            instruction=instruction,
                                            generate_fn=generate_fn)
        except ai.AiUnavailable as exc:
            # ИИ лёг целиком — это не про объявление. Дальше по пачке не идём:
            # каждое следующее ждало бы отказа всех звеньев цепочки заново.
            # Ничего ещё не написано — отказ словами; часть уже написана —
            # отдаём её, а остаток помечаем пропущенным.
            log.warning('Объявления OLX: ИИ недоступен на %s/%s — %s',
                        advert['cabinet_code'], advert['advert_id'], exc)
            reason = 'ИИ сейчас недоступен: %s' % (str(exc)[:200],)
            if not made:
                raise AdsError(reason, code='ai_unavailable')
            failed.append({'cabinet': advert['cabinet_code'],
                           'advert_id': advert['advert_id'], 'error': reason})
            failed.extend({'cabinet': rest['cabinet_code'],
                           'advert_id': rest['advert_id'],
                           'error': 'пропущено: ИИ перестал отвечать'}
                          for rest in adverts[index + 1:])
            break
        except Exception as exc:                             # noqa: BLE001
            log.exception('Объявления OLX: ИИ не справился с %s/%s',
                          advert['cabinet_code'], advert['advert_id'])
            failed.append({'cabinet': advert['cabinet_code'],
                           'advert_id': advert['advert_id'], 'error': str(exc)[:300]})
            continue

        if not result.get('title') or not result.get('description'):
            failed.append({'cabinet': advert['cabinet_code'],
                           'advert_id': advert['advert_id'],
                           'error': 'модель вернула пустой текст'})
            continue

        with db._get_cursor() as cursor:
            draft_id = queries.upsert_draft(
                cursor, advert['cabinet_code'], advert['advert_id'],
                result['title'], result['description'],
                brief_id=brief.get('id'), model=result.get('model'),
                origin='ai', actor_id=actor_id, actor_name=actor_name)

        made.append({'cabinet': advert['cabinet_code'],
                     'advert_id': advert['advert_id'], 'draft_id': draft_id,
                     'title': result['title'], 'description': result['description'],
                     'model': result.get('model'), 'problems': result.get('problems')})

    return {'made': made, 'failed': failed,
            'briefs': [{'id': brief_id, 'title': title}
                       for brief_id, title in used.items()]}
