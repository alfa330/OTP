# -*- coding: utf-8 -*-
"""Операции раздела «Воронка найма»: одна операция — одна транзакция.

Сюда приходит контекст человека (queries.load_access_context) и просьба; здесь решается,
можно ли (access + rules), и делается всё разом: карточка, вход в этап, касание, история
закреплений, мотивация, уведомления. Ручки (routes.py) только разбирают запрос и отдают ответ.

Приём контактов и откликов из всех источников идёт через одну точку — `_intake_one`: именно
там правила дублей ТЗ 5.3 (телефон сотрудника, открытая карточка, завершённая карточка).
"""

import logging
import threading
import uuid
from datetime import datetime, timedelta

import psycopg2

from . import access, calls, hh, notices, queries, rules, upload, wa_media, whatsapp

log = logging.getLogger(__name__)

UPLOAD_CHUNK = 500


class HiringError(Exception):
    """Отказ по делу: code — для ответа ручки, message — человеку, status — HTTP."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _forbidden(message='Это действие вам не доступно'):
    return HiringError('HIRING_FORBIDDEN', message, 403)


def _parse_moment(value, field='дата'):
    """ISO-строка ('2026-10-30T14:00' / '2026-10-30 14:00' / '2026-10-30') → наивное время Алматы."""
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip().replace('Z', '')
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise HiringError('HIRING_BAD_DATE', 'Не разобрать %s: %s' % (field, text[:40]))
    if moment.tzinfo is not None:
        moment = moment.astimezone(rules.ALMATY).replace(tzinfo=None)
    return moment


def _parse_date(value, field='дата'):
    moment = _parse_moment(value, field)
    return moment.date() if moment else None


def _int(value):
    if value is None or value == '' or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value, limit):
    text = str(value or '').strip()
    return text[:limit] if text else None


def _spawn_thread(target, *args):
    """Фоновая работа своим потоком, а не в общем пуле бота (4 места на всё; распознавание — до минуты)."""
    threading.Thread(target=target, args=args, name='hiring-wa-media', daemon=True).start()


class HiringService:
    def __init__(self, cursor_factory, *, send_telegram=None, web_app_base_url=None, now=None, hh_transport=None,
                 dial_list=None, storage=None, transcriber=None, spawn=None):
        """dial_list — () -> сервис «Удаленного КЦ» или None: клиент Binotel отдела для истории звонков
        линии рекрутера и ссылки на запись разговора. storage — бакет файлов WhatsApp найма
        (wa_media.Storage), transcriber — (байты, тип) -> расшифровка голосового, spawn — запуск фоновой
        работы (по умолчанию свой поток; тесты делают её сразу)."""
        self._cursor = cursor_factory
        self._send_telegram = send_telegram
        self._base_url = web_app_base_url
        self._now = now or rules.now_almaty
        self._hh_transport = hh_transport
        self._dial_list = dial_list
        self._storage = storage
        self._transcriber = transcriber
        self._spawn = spawn or _spawn_thread
        self._pull_lock = threading.Lock()
        self._calls_lock = threading.Lock()

    def _dial_list_service(self):
        try:
            return self._dial_list() if callable(self._dial_list) else self._dial_list
        except Exception:  # noqa: BLE001
            return None

    # ─── Контекст ────────────────────────────────────────────────────────────

    def context(self, user_id):
        with self._cursor() as cursor:
            return queries.load_access_context(cursor, user_id)

    @staticmethod
    def _caps(ctx):
        return access.capabilities(ctx)

    @staticmethod
    def visible(ctx, card):
        """Видит ли человек карточку — то же правило, что queries.scope_clause."""
        caps = access.capabilities(ctx)
        if caps['lead']:
            return True
        if caps['recruiter'] and (card['stage'] in rules.POOL_STAGES and not card['is_closed']):
            return True
        # Владелец — только действующий рекрутер: снятая выдача снимает и права на свои карточки
        # (в списках их уже нет — queries.scope_clause, и по card_id не должно быть).
        if caps['recruiter'] and card.get('recruiter_id') and int(card['recruiter_id']) == int(ctx['user_id']):
            return True
        if card.get('group_id') and int(card['group_id']) in set(caps['trainer_group_ids']):
            return True
        if card.get('department_id') and int(card['department_id']) in set(caps['headed_ids']):
            return True
        return False

    def _load_visible(self, cursor, ctx, card_id, lock=False):
        card = queries.get_card(cursor, card_id, lock=lock)
        if not card or not self.visible(ctx, card):
            raise HiringError('HIRING_NOT_FOUND', 'Карточка не найдена', 404)
        return card

    @staticmethod
    def _is_owner_or_lead(ctx, card):
        caps = access.capabilities(ctx)
        return caps['lead'] or (caps['recruiter'] and card.get('recruiter_id') and int(card['recruiter_id']) == int(ctx['user_id']))

    # ─── Приём контактов и откликов ──────────────────────────────────────────

    def _intake_one(self, cursor, *, phone, kind, source, now, by=None, by_name=None, full_name='', city=None,
                    vacancy_id=None, vacancy_text=None, platform_ref=None, campaign=None, referred_by=None,
                    received_at=None, comment=None, batch_id=None, whatsapp=None, notify=True):
        """Одна запись из любого источника. Возвращает (исход, card_id):
        created — новая карточка в пуле; repeat — повторный отклик по открытой карточке;
        employee — телефон действующего сотрудника, карточки нет (ТЗ 5.3)."""
        employee = queries.employee_by_phone_key(cursor, phone[-10:])
        if employee and str(employee['status'] or '').lower() not in ('fired', 'dismissal'):
            return 'employee', None
        open_card = queries.open_card_by_phone(cursor, phone, lock=True)
        if open_card:
            self._repeat(cursor, open_card, source, received_at or now, now, platform_ref, vacancy_text)
            return 'repeat', open_card['id']
        closed = queries.last_closed_card_by_phone(cursor, phone)
        fields = {
            'kind': kind, 'full_name': _text(full_name, 200) or '', 'phone': phone,
            'whatsapp': rules.normalize_phone(whatsapp) if whatsapp else None, 'city': _text(city, 120),
            'vacancy_id': vacancy_id, 'vacancy_text': _text(vacancy_text, 200), 'source_id': source['id'],
            'batch_id': batch_id, 'platform_ref': _text(platform_ref, 2000), 'campaign': _text(campaign, 200),
            'referred_by': _text(referred_by, 200), 'received_at': received_at or now,
            'stage': rules.pool_stage_for(kind), 'repeat_of_card_id': closed['id'] if closed else None,
            'comment': _text(comment, 4000), 'created_by': by, 'created_at': now, 'updated_at': now,
        }
        cursor.execute("SAVEPOINT hiring_intake")
        try:
            card_id = queries.insert_card(cursor, fields)
        except psycopg2.errors.UniqueViolation:
            cursor.execute("ROLLBACK TO SAVEPOINT hiring_intake")
            open_card = queries.open_card_by_phone(cursor, phone, lock=True)
            if open_card:
                self._repeat(cursor, open_card, source, received_at or now, now, platform_ref, vacancy_text)
                return 'repeat', open_card['id']
            raise
        cursor.execute("RELEASE SAVEPOINT hiring_intake")
        queries.add_stage(cursor, card_id, fields['stage'], None, by, by_name, now)
        if closed:
            queries.add_touch(cursor, card_id, rules.TOUCH_SYSTEM, 'repeat_of', 'Повторное обращение: есть завершённая карточка №%s'
                              % (closed.get('candidate_no') or closed['id']), by, by_name, now,
                              meta={'repeat_of_card_id': closed['id']})
        elif employee:
            queries.add_touch(cursor, card_id, rules.TOUCH_SYSTEM, 'former_employee',
                              'Повторное обращение: бывший сотрудник', by, by_name, now,
                              meta={'former_employee_id': employee['id']})
        if notify and kind == rules.KIND_RESPONSE:
            card = queries.get_card(cursor, card_id)
            notices.new_response(cursor, card, now)
        return 'created', card_id

    def _repeat(self, cursor, card, source, received_at, now, platform_ref=None, vacancy_text=None):
        body = 'Повторный отклик · %s' % (source.get('name') or '')
        if vacancy_text:
            body += ' · ' + str(vacancy_text)[:120]
        queries.add_touch(cursor, card['id'], rules.TOUCH_REPEAT, source.get('code'), body, None, None, now,
                          meta={'source_id': source['id'], 'received_at': received_at.isoformat(timespec='minutes'),
                                'platform_ref': platform_ref})
        queries.update_card(cursor, card['id'], updated_at=now)
        notices.repeat_response(cursor, card, source.get('name'), now)

    def upload_contacts(self, ctx, raw, file_name, source_id):
        """Загрузка базы (ТЗ 5.1): файл → партия → карточки в «Контакты». Итог: загружено, дублей, ошибок."""
        if not access.can_upload(ctx):
            raise _forbidden('Загружать базы может руководитель рекрутинга')
        now = self._now()
        with self._cursor() as cursor:
            source = queries.get_source(cursor, source_id) if source_id else None
        if not source or not source['is_active'] or source['kind'] != rules.SOURCE_CONTACTS:
            raise HiringError('HIRING_NEED_SOURCE', 'Выберите источник контактов — он один на весь файл')
        try:
            contacts, errors, total = upload.parse(raw, file_name)
        except upload.UploadError as exc:
            raise HiringError('HIRING_BAD_FILE', str(exc))
        if not contacts and not errors:
            raise HiringError('HIRING_EMPTY_FILE', 'В файле не нашлось ни одной строки с телефоном')
        with self._cursor() as cursor:
            batch_id = queries.create_batch(cursor, source['id'], _text(file_name, 255), ctx['user_id'], ctx['name'], now)
        loaded = duplicates = 0
        skipped = []
        try:
            # Порциями по UPLOAD_CHUNK строк — своей транзакцией каждая: длинная открытая транзакция на
            # тысячи строк держала бы деплой; сбой посреди файла оставляет уже записанные порции.
            for start in range(0, len(contacts), UPLOAD_CHUNK):
                chunk_loaded = chunk_duplicates = 0
                chunk_skipped = []
                with self._cursor() as cursor:
                    for item in contacts[start:start + UPLOAD_CHUNK]:
                        outcome, _card_id = self._intake_one(
                            cursor, phone=item['phone'], kind=rules.KIND_CONTACT, source=source, now=now,
                            by=ctx['user_id'], by_name=ctx['name'], full_name=item['full_name'], city=item['city'],
                            comment=item['comment'], batch_id=batch_id, notify=False)
                        if outcome == 'created':
                            chunk_loaded += 1
                        elif outcome == 'repeat':
                            chunk_duplicates += 1
                        else:
                            chunk_skipped.append({'row': item['row'], 'value': item['phone'], 'error': 'Телефон действующего сотрудника'})
                # Счётчики — только за записанную порцию: откатившаяся порция в итог не попадает.
                loaded += chunk_loaded
                duplicates += chunk_duplicates
                skipped.extend(chunk_skipped)
        finally:
            all_errors = sorted(errors + skipped, key=lambda row: row['row'])
            with self._cursor() as cursor:
                queries.finish_batch(cursor, batch_id, total, loaded, duplicates, len(all_errors), all_errors)
        with self._cursor() as cursor:
            batch = queries.get_batch(cursor, batch_id)
        return batch

    def add_response(self, ctx, payload):
        """Отклик вручную (ТЗ 5.2): ФИО, телефон, город, вакансия, источник — источник обязателен."""
        if not access.can_add_response(ctx):
            raise _forbidden()
        payload = payload or {}
        phone = rules.normalize_phone(payload.get('phone'))
        if not phone:
            raise HiringError('HIRING_BAD_PHONE', 'Телефон должен быть казахстанским номером из 10 цифр')
        now = self._now()
        with self._cursor() as cursor:
            source = queries.get_source(cursor, payload.get('source_id')) if _int(payload.get('source_id')) else None
            if not source or not source['is_active']:
                raise HiringError('HIRING_NEED_SOURCE', 'Укажите источник отклика')
            vacancy_id = _int(payload.get('vacancy_id'))
            if vacancy_id and not queries.get_vacancy(cursor, vacancy_id):
                raise HiringError('HIRING_BAD_VACANCY', 'Такой вакансии нет')
            outcome, card_id = self._intake_one(
                cursor, phone=phone, kind=rules.KIND_RESPONSE, source=source, now=now, by=ctx['user_id'],
                by_name=ctx['name'], full_name=payload.get('full_name'), city=payload.get('city'),
                vacancy_id=vacancy_id, vacancy_text=payload.get('vacancy_text'), referred_by=payload.get('referred_by'),
                comment=payload.get('comment'), whatsapp=payload.get('whatsapp'))
            if outcome == 'employee':
                raise HiringError('HIRING_EMPLOYEE_PHONE', 'Это телефон действующего сотрудника — карточка не создаётся', 409)
            card = queries.get_card(cursor, card_id)
        return outcome, card

    def external_intake(self, items, *, source_code, actor_label):
        """Вебхук площадок (Kaspi, таргет и любой интегратор): список откликов → исход по каждому."""
        now = self._now()
        results = []
        with self._cursor() as cursor:
            source = queries.source_by_code(cursor, source_code)
            # Партнёру открыты только источники откликов без своего опроса (Kaspi, таргет, свои):
            # писать от имени HeadHunter, OLX или загруженной базы он не может — иначе отчёт по
            # источникам перестал бы отличать, откуда пришёл отклик.
            if (not source or not source['is_active'] or source['kind'] != rules.SOURCE_RESPONSES
                    or source.get('platform') not in (None, 'webhook')):
                raise HiringError('HIRING_BAD_SOURCE', 'Неизвестный источник: %s' % str(source_code)[:40])
            for item in items:
                item = item if isinstance(item, dict) else {}
                phone = rules.normalize_phone(item.get('phone'))
                if not phone:
                    results.append({'status': 'invalid', 'phone': str(item.get('phone') or '')[:32]})
                    continue
                outcome, card_id = self._intake_one(
                    cursor, phone=phone, kind=rules.KIND_RESPONSE, source=source, now=now, by=None,
                    by_name=actor_label, full_name=item.get('full_name') or item.get('name'), city=item.get('city'),
                    vacancy_text=item.get('vacancy'), platform_ref=item.get('ref') or item.get('url'),
                    campaign=item.get('campaign'), received_at=_safe_moment(item.get('received_at')) or now,
                    comment=item.get('comment'))
                results.append({'status': outcome, 'phone': phone, 'card_id': card_id})
            queries.set_integration(cursor, 'webhook', now, state='ok', ok=True, error=None,
                                    received=sum(1 for r in results if r['status'] in ('created', 'repeat')))
        return results

    # ─── Опрос площадок (планировщик) ────────────────────────────────────────

    def pull_olx(self):
        """Отклики OLX из журнала робота (olx_amo): после закладки, только с номером и только из
        кабинетов «для найма» (настройка руководителя). Кабинеты, подключённые к лидам OLX, —
        таксопарки, их чаты к рекрутерам не попадают (владелец 30.10.2026). Пока список пуст,
        опрос только двигает закладку: включили кабинет — история прошлых недель в пул не льётся."""
        if not self._pull_lock.acquire(blocking=False):
            return None
        try:
            now = self._now()
            with self._cursor() as cursor:
                if not queries.olx_tables_exist(cursor):
                    queries.set_integration(cursor, 'olx', now, state='not_configured', error='Робот OLX не развёрнут')
                    return 0
                state = queries.get_integration(cursor, 'olx') or {}
                cabinets = [str(code) for code in ((state.get('settings') or {}).get('cabinets') or []) if code]
                bookmark = (state.get('cursor') or {}).get('journal_id')
                if bookmark is None or not cabinets:
                    queries.set_integration(
                        cursor, 'olx', now, state='ok' if cabinets else 'not_configured', ok=bool(cabinets),
                        error=None if cabinets else 'Кабинеты OLX для найма не выбраны — чаты кабинетов лидов в воронку не идут',
                        cursor_value={'journal_id': queries.olx_journal_max_id(cursor)})
                    return 0
                source = queries.source_by_code(cursor, 'olx')
                rows = queries.olx_responses_since(cursor, bookmark, cabinets)
                received = 0
                for row in rows:
                    phone = rules.normalize_phone(row['phone_normalized'])
                    if not phone:
                        continue
                    outcome, _card_id = self._intake_one(
                        cursor, phone=phone, kind=rules.KIND_RESPONSE, source=source, now=now,
                        by_name='Робот OLX', full_name=row.get('interlocutor_name') or '',
                        vacancy_text=row.get('advert_title'),
                        platform_ref='olx:%s:%s' % (row['cabinet_code'], row['thread_id']),
                        received_at=row.get('message_at') or row.get('created_at') or now)
                    if outcome in ('created', 'repeat'):
                        received += 1
                    bookmark = row['id']
                queries.set_integration(cursor, 'olx', now, state='ok', ok=True, error=None,
                                        cursor_value={'journal_id': bookmark}, received=received)
                return received
        except Exception as exc:  # noqa: BLE001
            log.exception('hiring: опрос OLX упал')
            try:
                with self._cursor() as cursor:
                    queries.set_integration(cursor, 'olx', self._now(), state='error', error=str(exc)[:500])
            except Exception:  # noqa: BLE001
                pass
            return None
        finally:
            self._pull_lock.release()

    def pull_hh(self, environ=None):
        """Отклики HeadHunter (ТЗ 5.2) — когда в окружении есть токен работодателя.
        Под тем же замком, что OLX: кнопка «Опросить» рядом с планировщиком не должна дважды
        разобрать одни отклики с одной закладкой."""
        if not self._pull_lock.acquire(blocking=False):
            return None
        try:
            return self._pull_hh(environ)
        finally:
            self._pull_lock.release()

    def _pull_hh(self, environ):
        config = hh.config_from_env(environ)
        now = self._now()
        if not config:
            with self._cursor() as cursor:
                queries.set_integration(cursor, 'hh', now, state='not_configured',
                                        error='Не заданы HH_ACCESS_TOKEN и HH_USER_AGENT')
            return 0
        with self._cursor() as cursor:
            state = queries.get_integration(cursor, 'hh') or {}
        try:
            items, new_cursor, problems = hh.fetch_responses(config, state.get('cursor') or {}, transport=self._hh_transport)
        except hh.HHError as exc:
            with self._cursor() as cursor:
                queries.set_integration(cursor, 'hh', now, state='error', error=str(exc)[:500])
            return None
        received = 0
        with self._cursor() as cursor:
            source = queries.source_by_code(cursor, 'hh')
            for item in items:
                phone = rules.normalize_phone(item.get('phone'))
                if not phone:
                    problems.append('без телефона: %s' % (item.get('full_name') or item.get('ref') or '?'))
                    continue
                outcome, _card_id = self._intake_one(
                    cursor, phone=phone, kind=rules.KIND_RESPONSE, source=source, now=now, by_name='HeadHunter',
                    full_name=item.get('full_name'), city=item.get('city'), vacancy_text=item.get('vacancy'),
                    platform_ref=item.get('ref'), received_at=item.get('received_at') or now)
                if outcome in ('created', 'repeat'):
                    received += 1
            queries.set_integration(cursor, 'hh', now, state='ok', ok=True,
                                    error=('; '.join(problems)[:500] or None), cursor_value=new_cursor, received=received)
        return received

    # ─── Работа с карточкой ──────────────────────────────────────────────────

    def take(self, ctx, card_id):
        """«Взять в работу» (ТЗ 7.1): закрепление мгновенное, второй получает «Уже в работе»."""
        if not access.can_take(ctx):
            raise _forbidden('Брать карточки в работу могут рекрутеры')
        now = self._now()
        with self._cursor() as cursor:
            card = queries.get_card(cursor, card_id)
            if not card or card['is_closed'] or card['stage'] not in rules.POOL_STAGES:
                if card and card.get('recruiter_id'):
                    raise HiringError('HIRING_ALREADY_TAKEN', 'Уже в работе', 409)
                raise HiringError('HIRING_NOT_IN_POOL', 'Карточка не в общем пуле', 409)
            taken = queries.claim_card(cursor, card_id, ctx['user_id'], now)
            if not taken:
                raise HiringError('HIRING_ALREADY_TAKEN', 'Уже в работе', 409)
            queries.add_stage(cursor, card_id, rules.IN_WORK, None, ctx['user_id'], ctx['name'], now)
            queries.add_assignment(cursor, card_id, ctx['user_id'], 'take', ctx['user_id'], ctx['name'], now)
            queries.mark_card_seen(cursor, ctx['user_id'], card_id, now)
            return queries.get_card(cursor, card_id)

    def touch(self, ctx, card_id, payload):
        """Касание (ТЗ 5.4, 6.1): звонок, WhatsApp, комментарий; подстатус — сам.

        Звонок без результата — «Позвонить» нажато, исход ждём из телефонии (владелец 30.10.2026:
        исход подтягивается сам, рекрутер дописывает недостающее): подстатус и серия недозвонов
        меняются, когда исход придёт (on_binotel_call) или его поставят руками (edit_touch)."""
        payload = payload or {}
        kind = str(payload.get('kind') or '').strip()
        if kind not in rules.HUMAN_TOUCHES:
            raise HiringError('HIRING_BAD_TOUCH', 'Касание бывает звонком, сообщением в WhatsApp или комментарием')
        result = str(payload.get('result') or '').strip() or None
        if kind == rules.TOUCH_CALL and result is not None and result not in rules.CALL_RESULT_CODES:
            raise HiringError('HIRING_BAD_CALL_RESULT', 'Результат звонка — дозвон или недозвон')
        body = _text(payload.get('body'), 4000)
        if kind == rules.TOUCH_COMMENT and not body:
            raise HiringError('HIRING_EMPTY_COMMENT', 'Комментарий пустой')
        next_contact_at = _parse_moment(payload.get('next_contact_at'), 'дату следующего контакта')
        now = self._now()
        with self._cursor() as cursor:
            card = self._load_visible(cursor, ctx, card_id, lock=True)
            if card['is_closed'] and kind != rules.TOUCH_COMMENT:
                raise HiringError('HIRING_CLOSED', 'Карточка завершена — только комментарий', 409)
            if card['stage'] in rules.POOL_STAGES and not access.is_lead(ctx):
                raise HiringError('HIRING_NOT_TAKEN', 'Сначала возьмите карточку в работу', 409)
            if kind != rules.TOUCH_COMMENT and not self._is_owner_or_lead(ctx, card):
                raise _forbidden('Звонки и переписку фиксирует закреплённый рекрутер')
            queries.add_touch(cursor, card_id, kind, result, body, ctx['user_id'], ctx['name'], now,
                              duration_sec=_int(payload.get('duration_sec')))
            fields = {'last_touch_at': now, 'updated_at': now}
            if next_contact_at is not None:
                fields['next_contact_at'] = next_contact_at
                fields['next_contact_notified'] = None
            if card['stage'] == rules.IN_WORK:
                if kind == rules.TOUCH_CALL and result:
                    fields['substage'] = 'dial' if result == 'dial' else 'no_answer'
                    fields['no_answer_streak'] = 0 if result == 'dial' else int(card['no_answer_streak'] or 0) + 1
                elif kind == rules.TOUCH_WHATSAPP:
                    fields['substage'] = 'whatsapp'
            queries.update_card(cursor, card_id, **fields)
            return queries.get_card(cursor, card_id)

    def edit_touch(self, ctx, card_id, touch_id, payload):
        """Дописать касание (владелец 30.10.2026): комментарий и следующий контакт к звонку; у звонка,
        который телефония не увидела (рекрутер не на линии), — исход руками. Исход, пришедший из
        телефонии, не переписывается."""
        payload = payload or {}
        now = self._now()
        with self._cursor() as cursor:
            card = self._load_visible(cursor, ctx, card_id, lock=True)
            touch = queries.get_touch(cursor, touch_id)
            if not touch or int(touch['card_id']) != int(card_id):
                raise HiringError('HIRING_NOT_FOUND', 'Касание не найдено', 404)
            if not self._is_owner_or_lead(ctx, card):
                raise _forbidden('Касания дописывает закреплённый рекрутер')
            fields = {}
            card_fields = {'updated_at': now}
            if 'body' in payload:
                fields['body'] = _text(payload.get('body'), 4000)
            if 'next_contact_at' in payload:
                card_fields['next_contact_at'] = _parse_moment(payload.get('next_contact_at'), 'дату следующего контакта')
                card_fields['next_contact_notified'] = None
            result = str(payload.get('result') or '').strip() or None
            if result is not None:
                if touch['kind'] != rules.TOUCH_CALL:
                    raise HiringError('HIRING_BAD_TOUCH', 'Исход бывает только у звонка')
                if result not in rules.CALL_RESULT_CODES:
                    raise HiringError('HIRING_BAD_CALL_RESULT', 'Результат звонка — дозвон или недозвон')
                if touch.get('call_id'):
                    raise HiringError('HIRING_TOUCH_FROM_PBX', 'Исход этого звонка пришёл из телефонии — его не переписать', 409)
                fields['result'] = result
                if card['stage'] == rules.IN_WORK and not card['is_closed']:
                    card_fields['substage'] = 'dial' if result == 'dial' else 'no_answer'
                    card_fields['no_answer_streak'] = 0 if result == 'dial' else int(card['no_answer_streak'] or 0) + 1
            if fields:
                queries.update_touch(cursor, touch_id, **fields)
            queries.update_card(cursor, card_id, **card_fields)
            return queries.get_card(cursor, card_id)

    def set_substage(self, ctx, card_id, substage):
        now = self._now()
        with self._cursor() as cursor:
            card = self._load_visible(cursor, ctx, card_id, lock=True)
            if not self._is_owner_or_lead(ctx, card):
                raise _forbidden()
            if card['stage'] not in (rules.IN_WORK, rules.INVITED_OFFICE) or not rules.substage_allowed(card['stage'], substage):
                raise HiringError('HIRING_BAD_SUBSTAGE', 'Такого подстатуса у этапа нет')
            queries.update_card(cursor, card_id, substage=substage, updated_at=now)
            return queries.get_card(cursor, card_id)

    def edit_card(self, ctx, card_id, payload):
        """Поля карточки: ФИО, WhatsApp, город, вакансия, «кто привёл», комментарий, дата следующего контакта."""
        payload = payload or {}
        now = self._now()
        with self._cursor() as cursor:
            card = self._load_visible(cursor, ctx, card_id, lock=True)
            if not self._is_owner_or_lead(ctx, card):
                raise _forbidden('Карточку правит закреплённый рекрутер или руководитель')
            fields = {'updated_at': now}
            if 'full_name' in payload:
                fields['full_name'] = _text(payload.get('full_name'), 200) or ''
            if 'whatsapp' in payload:
                value = payload.get('whatsapp')
                fields['whatsapp'] = rules.normalize_phone(value) if value else None
                if value and not fields['whatsapp']:
                    raise HiringError('HIRING_BAD_PHONE', 'Номер WhatsApp должен быть казахстанским номером из 10 цифр')
            if 'city' in payload:
                fields['city'] = _text(payload.get('city'), 120)
            if 'vacancy_id' in payload:
                vacancy_id = _int(payload.get('vacancy_id'))
                if vacancy_id and not queries.get_vacancy(cursor, vacancy_id):
                    raise HiringError('HIRING_BAD_VACANCY', 'Такой вакансии нет')
                fields['vacancy_id'] = vacancy_id
            if 'referred_by' in payload:
                fields['referred_by'] = _text(payload.get('referred_by'), 200)
            if 'comment' in payload:
                fields['comment'] = _text(payload.get('comment'), 4000)
            if 'next_contact_at' in payload:
                fields['next_contact_at'] = _parse_moment(payload.get('next_contact_at'), 'дату следующего контакта')
                fields['next_contact_notified'] = None
            if 'office_visit_at' in payload and card['stage'] == rules.INVITED_OFFICE:
                moment = _parse_moment(payload.get('office_visit_at'), 'дату визита')
                if not moment:
                    raise HiringError('HIRING_NEED_OFFICE_DATE', 'Укажите дату и время визита')
                fields['office_visit_at'] = moment
                fields['substage'] = 'expected'
            queries.update_card(cursor, card_id, **fields)
            return queries.get_card(cursor, card_id)

    def move(self, ctx, card_id, target, payload):
        """Переход по этапам (ТЗ 4.2) со всеми следствиями."""
        payload = payload or {}
        if target not in rules.STAGE_CODES:
            raise HiringError('HIRING_BAD_STAGE', 'Такого этапа нет')
        if target == rules.IN_WORK:
            # Из пула в работу — только атомарным «Взять в работу» (claim_card): по ЭТАПУ карточки, а не по
            # флагу из тела, иначе любой рекрутер переводил бы чужие контакты в «В работе» без закрепления.
            # Из «Отказа» / «Отсева» — повторная активация (правило с reactivate, только руководитель).
            with self._cursor() as cursor:
                current = self._load_visible(cursor, ctx, card_id)
            if current['stage'] in rules.POOL_STAGES:
                return self.take(ctx, card_id)
        now = self._now()
        caps = self._caps(ctx)
        with self._cursor() as cursor:
            card = self._load_visible(cursor, ctx, card_id, lock=True)
            kinds = rules.actor_kinds(caps, card, ctx['user_id'], ctx.get('trainer_group_ids') or ())
            has_touch = queries.has_human_touch(cursor, card_id, since=card.get('taken_at'))
            check = dict(payload)
            check['shifts_done'] = queries.count_shifts(cursor, card_id)
            try:
                rule = rules.check_transition(card, target, kinds, has_touch=has_touch, payload=check)
            except rules.TransitionError as exc:
                raise HiringError(exc.code, exc.message, 403 if exc.code == 'HIRING_FORBIDDEN' else 409)
            fields = {'stage': target, 'substage': None, 'updated_at': now}
            if target == rules.INVITED_OFFICE:
                fields['office_visit_at'] = _parse_moment(payload.get('office_visit_at'), 'дату визита')
                fields['substage'] = 'expected'
            elif target == rules.INVITED_TRAINING:
                group = queries.get_group(cursor, _int(payload.get('group_id')) or 0)
                if not group or group['status'] != 'active':
                    raise HiringError('HIRING_BAD_GROUP', 'Такой группы на обучение нет')
                fields['group_id'] = group['id']
                if not card.get('vacancy_id') and group.get('vacancy_id'):
                    fields['vacancy_id'] = group['vacancy_id']
            elif target == rules.ON_TRAINING:
                fields['training_started_at'] = _parse_date(payload.get('training_started_at')) or card.get('group_start_date') or now.date()
            elif target == rules.ON_LINE:
                fields['line_started_at'] = _parse_date(payload.get('line_started_at'), 'дату выхода на линию')
            elif target in (rules.REFUSED, rules.DROPPED):
                fields.update(self._closing_fields(cursor, card, target, payload, now))
            elif target == rules.HIRED:
                fields.update({'hired_at': now, 'hired_by': ctx['user_id'], 'is_closed': True, 'closed_at': now,
                               'shifts_done': check['shifts_done']})
            if rule.get('reactivate'):
                fields.update(self._reactivation_fields(cursor, ctx, card, payload, now))
            cursor.execute("SAVEPOINT hiring_move")
            try:
                queries.update_card(cursor, card_id, **fields)
            except psycopg2.errors.UniqueViolation:
                cursor.execute("ROLLBACK TO SAVEPOINT hiring_move")
                raise HiringError('HIRING_PHONE_OPEN', 'По этому телефону уже есть открытая карточка', 409)
            cursor.execute("RELEASE SAVEPOINT hiring_move")
            queries.add_stage(cursor, card_id, target, fields.get('substage'), ctx['user_id'], ctx['name'], now)
            self._after_move(cursor, card, target, fields, ctx, now)
            return queries.get_card(cursor, card_id)

    def _closing_fields(self, cursor, card, target, payload, now):
        initiator = payload.get('initiator')
        reason = queries.get_reason(cursor, _int(payload.get('reason_id')) or 0)
        wanted_kind = rules.REASON_KIND_FOR.get((target, initiator))
        if not reason or reason['kind'] != wanted_kind:
            raise HiringError('HIRING_BAD_REASON', 'Причина не из списка этого инициатора')
        comment = _text(payload.get('comment'), 4000)
        if reason['requires_comment'] and not comment:
            raise HiringError('HIRING_NEED_COMMENT', 'Для «Другое» нужен комментарий')
        fields = {
            'substage': initiator, 'close_initiator': initiator, 'close_reason_id': reason['id'],
            'close_reason_text': reason['name'], 'close_comment': comment, 'is_closed': True, 'closed_at': now,
            'next_contact_at': None,
        }
        if target == rules.DROPPED:
            fields['close_stage'] = rules.drop_stage_for(card['stage'])
            fields['close_day'] = _int(payload.get('day'))
        return fields

    def _reactivation_fields(self, cursor, ctx, card, payload, now):
        """Повторная активация (ТЗ 4.2): закрепление сохраняется или назначается заново."""
        recruiter_id = _int(payload.get('recruiter_id')) or card.get('recruiter_id')
        if not recruiter_id:
            raise HiringError('HIRING_NEED_RECRUITER', 'Укажите рекрутера, за которым закрепить карточку')
        # Та же проверка, что у reassign: закрепить можно только за действующим рекрутером или руководителем.
        if recruiter_id not in set(queries.recruiter_user_ids(cursor)) | set(queries.lead_user_ids(cursor)):
            raise HiringError('HIRING_BAD_RECRUITER', 'Этот человек не рекрутер')
        return {
            'recruiter_id': recruiter_id, 'is_closed': False, 'closed_at': None, 'close_initiator': None,
            'close_reason_id': None, 'close_reason_text': None, 'close_stage': None, 'close_day': None,
            'close_comment': None, 'hired_at': None, 'hired_by': None, 'reactivations': int(card['reactivations'] or 0) + 1,
            'taken_at': now, 'no_answer_streak': 0, 'last_touch_at': None,
        }

    def _after_move(self, cursor, card, target, fields, ctx, now):
        card_id = card['id']
        if target == rules.ON_LINE:
            queries.upsert_motivation(cursor, card_id, card.get('recruiter_id'), rules.MOTIVATION_PENDING, now)
            fresh = queries.get_card(cursor, card_id)
            notices.on_line(cursor, fresh, now)
            # Учётка сотрудника и смены по статусам — сразу, не дожидаясь джобы.
            self._sync_card_shifts(cursor, fresh, now)
        elif target == rules.HIRED:
            queries.upsert_motivation(cursor, card_id, card.get('recruiter_id'), rules.MOTIVATION_COUNTED, now)
            queries.set_motivation(cursor, card_id, now, confirmed_at=now, confirmed_by=ctx['user_id'],
                                   status=rules.MOTIVATION_COUNTED)
            notices.motivation_counted(cursor, card, card.get('recruiter_id'), now)
        elif target == rules.DROPPED:
            if queries.get_motivation(cursor, card_id):
                queries.set_motivation(cursor, card_id, now, status=rules.MOTIVATION_CANCELLED,
                                       cancel_reason='Отсев до 7 смен', cancelled_at=now, cancelled_by=ctx['user_id'])
        if target == rules.IN_WORK and fields.get('recruiter_id'):
            queries.release_assignments(cursor, card_id, now)
            queries.add_assignment(cursor, card_id, fields['recruiter_id'], 'reactivate', ctx['user_id'], ctx['name'], now)
            if queries.get_motivation(cursor, card_id):
                queries.set_motivation(cursor, card_id, now, status=rules.MOTIVATION_PENDING, cancel_reason=None,
                                       cancelled_at=None, cancelled_by=None, confirmed_at=None, confirmed_by=None)

    def reassign(self, ctx, card_id, recruiter_id, comment=None):
        """Переназначить или вернуть в пул — руководитель рекрутинга, с причиной (ТЗ 7.1)."""
        if not access.can_manage(ctx):
            raise _forbidden('Переназначает руководитель рекрутинга')
        comment = _text(comment, 1000)
        if not comment:
            raise HiringError('HIRING_NEED_COMMENT', 'Укажите причину')
        now = self._now()
        with self._cursor() as cursor:
            card = queries.get_card(cursor, card_id, lock=True)
            if not card:
                raise HiringError('HIRING_NOT_FOUND', 'Карточка не найдена', 404)
            if card['is_closed']:
                raise HiringError('HIRING_CLOSED', 'Карточка завершена', 409)
            old = card.get('recruiter_id')
            new = _int(recruiter_id)
            if new and new not in set(queries.recruiter_user_ids(cursor)) | set(queries.lead_user_ids(cursor)):
                raise HiringError('HIRING_BAD_RECRUITER', 'Этот человек не рекрутер')
            queries.release_assignments(cursor, card_id, now)
            if new:
                fields = {'recruiter_id': new, 'updated_at': now}
                if card['stage'] in rules.POOL_STAGES:
                    fields.update({'stage': rules.IN_WORK, 'substage': None, 'taken_at': now})
                    queries.add_stage(cursor, card_id, rules.IN_WORK, None, ctx['user_id'], ctx['name'], now)
                    cursor.execute("UPDATE hiring_cards SET candidate_no = COALESCE(candidate_no, nextval('hiring_candidate_no_seq')) WHERE id = %s",
                                   (int(card_id),))
                queries.update_card(cursor, card_id, **fields)
                queries.add_assignment(cursor, card_id, new, 'reassign', ctx['user_id'], ctx['name'], now, comment)
                # Ожидающее начисление (кандидат на линии) переезжает к новому рекрутеру — мотивация
                # засчитывается тому, за кем карточка закреплена в момент подтверждения 7 смен (ТЗ 7.2).
                motivation = queries.get_motivation(cursor, card_id)
                if motivation and motivation['status'] == rules.MOTIVATION_PENDING:
                    queries.set_motivation(cursor, card_id, now, recruiter_id=new)
            else:
                if card['stage'] not in rules.ASSIGNED_STAGES:
                    raise HiringError('HIRING_NOT_RETURNABLE', 'В пул возвращаются карточки до выхода на обучение', 409)
                pool = rules.pool_stage_for(card['kind'])
                queries.update_card(cursor, card_id, stage=pool, substage=None, recruiter_id=None, next_contact_at=None,
                                    group_id=None, office_visit_at=None, updated_at=now)
                queries.add_stage(cursor, card_id, pool, None, ctx['user_id'], ctx['name'], now)
                queries.add_assignment(cursor, card_id, None, 'release', ctx['user_id'], ctx['name'], now, comment)
            notices.reassigned(cursor, card, old, new, now, comment)
            return queries.get_card(cursor, card_id)

    # ─── Смены на линии — из статусов работы (владелец 30.10.2026) ───────────
    # Руками смены не ведутся: кандидат, вышедший на линию, — это сотрудник в «Учете сотрудников»
    # с тем же телефоном, а его дни по статусам работы считает «Учет часов» (daily_hours.work_time).
    # День, где отработано не меньше SHIFT_MIN_HOURS, — смена; седьмая — колокол руководителю отдела.

    def _employee_for_card(self, cursor, card):
        """Учётка сотрудника, в которого превратился кандидат, — по телефону; найденная запоминается."""
        if card.get('employee_user_id'):
            return int(card['employee_user_id'])
        key = rules.phone_key(card.get('phone'))
        employee = queries.employee_by_phone_key(cursor, key) if key else None
        if not employee:
            return None
        queries.update_card(cursor, card['id'], employee_user_id=employee['id'])
        return int(employee['id'])

    def _sync_card_shifts(self, cursor, card, now):
        """Смены одной карточки на линии из daily_hours её сотрудника. Возвращает (сотрудник найден, смен)."""
        employee_id = self._employee_for_card(cursor, card)
        before = int(card.get('shifts_done') or 0)
        if not employee_id or not queries.daily_hours_table_exists(cursor):
            return bool(employee_id), before
        since = card.get('line_started_at') or (card['taken_at'].date() if card.get('taken_at') else now.date())
        days = queries.worked_days(cursor, employee_id, since, now.date(), rules.SHIFT_MIN_HOURS)
        total = queries.replace_auto_shifts(cursor, card['id'], days, now)
        if total != before:
            queries.update_card(cursor, card['id'], shifts_done=total, updated_at=now)
        if total >= rules.SHIFTS_TO_HIRE and before < rules.SHIFTS_TO_HIRE:
            notices.shifts_ready(cursor, queries.get_card(cursor, card['id']), now)
        return True, total

    def sync_shifts(self, now=None):
        """Джоба: смены всех карточек на линии — из статусов работы. Сводка: карточек, с учёткой, смен."""
        now = now or self._now()
        summary = {'cards': 0, 'matched': 0, 'shifts': 0}
        with self._cursor() as cursor:
            cards = queries.on_line_cards(cursor)
        for card in cards:
            with self._cursor() as cursor:
                fresh = queries.get_card(cursor, card['id'], lock=True)
                if not fresh or fresh['stage'] != rules.ON_LINE or fresh['is_closed']:
                    continue
                matched, total = self._sync_card_shifts(cursor, fresh, now)
            summary['cards'] += 1
            summary['matched'] += int(matched)
            summary['shifts'] += total
        return summary

    def sync_card_shifts(self, ctx, card_id):
        """«Обновить из статусов» в карточке — тому, кто её видит; джоба делает то же раз в полчаса."""
        now = self._now()
        with self._cursor() as cursor:
            card = self._load_visible(cursor, ctx, card_id, lock=True)
            if card['stage'] != rules.ON_LINE:
                raise HiringError('HIRING_NOT_ON_LINE', 'Смены считаются после выхода на линию', 409)
            self._sync_card_shifts(cursor, card, now)
            return queries.get_card(cursor, card_id)

    # ─── Звонки из телефонии Binotel (владелец 30.10.2026) ───────────────────

    def on_binotel_call(self, fields, matched=False):
        """Наблюдатель вебхука «API Call Completed» раздела «Удаленный КЦ»: звонок с линии рекрутера
        (или входящий на неё) ложится в карточку кандидата с исходом, длительностью и записью.
        Звонки обзвона (matched) — не наши."""
        if matched:
            return None
        call = calls.from_webhook(fields)
        if not call:
            return None
        with self._calls_lock:
            with self._cursor() as cursor:
                call_id, _created = self._record_call(cursor, call, source='webhook', now=self._now())
                return call_id

    def _record_call(self, cursor, call, *, source, now):
        """Один звонок → строка hiring_calls и касание в ленте карточки. Возвращает (id звонка, новый ли);
        (None, False) — звонок не к нам: линия не рекрутера, номер без карточки, звонок ещё идёт."""
        if not call.get('external_phone'):
            return None, False
        result = calls.map_result(call.get('disposition'))
        if not result:
            return None, False
        seat = queries.recruiter_by_line(cursor, call['internal_number'])
        if not seat:
            return None, False
        known = queries.get_call_by_general_id(cursor, call['general_call_id'])
        recording = calls.recording_state(call)
        if known:
            # Вебхук дошёл после истории (или наоборот): исход тот же, длительность — большая.
            if known['result'] != result or int(call['billsec']) > int(known['billsec'] or 0):
                queries.update_call_outcome(cursor, known['id'], call['disposition'], result, call['billsec'],
                                            call['waitsec'], recording, now)
                if known.get('touch_id'):
                    queries.update_touch(cursor, known['touch_id'], result=calls.touch_result(call),
                                         body=calls.touch_body(call), duration_sec=int(call['billsec']))
            return known['id'], False
        card = queries.card_for_call(cursor, call['external_phone'], seat['user_id'])
        if not card:
            return None, False
        call_id = queries.insert_call(cursor, call, card['id'], seat['user_id'], seat['department_id'], result, recording,
                                      source, now)
        if not call_id:
            return None, False
        started = call.get('started_at') or now
        meta = {'general_call_id': call['general_call_id'], 'direction': call['direction'], 'line': call['internal_number'],
                'disposition': call['disposition'], 'result': result, 'waitsec': call['waitsec'], 'recording': recording,
                'source': source}
        # Ручная отметка после «Позвонить» рядом по времени — тот же звонок: дописываем её, а не дублируем.
        manual = queries.manual_call_touch(cursor, card['id'], seat['user_id'], started, calls.MERGE_WINDOW_MINUTES)
        if manual:
            body = calls.touch_body(call) + (('\n' + manual['body']) if manual.get('body') else '')
            queries.update_touch(cursor, manual['id'], result=calls.touch_result(call), body=body,
                                 duration_sec=int(call['billsec']), meta=meta)
            touch_id = manual['id']
        else:
            touch_id = queries.add_touch(cursor, card['id'], rules.TOUCH_CALL, calls.touch_result(call),
                                         calls.touch_body(call), seat['user_id'], queries.user_name(cursor, seat['user_id']),
                                         started, duration_sec=int(call['billsec']), meta=meta)
        queries.link_call_touch(cursor, call_id, touch_id)
        fields = {'updated_at': now}
        if not card['is_closed']:
            fields['last_touch_at'] = max(started, card.get('last_touch_at') or started)
            # Ручная отметка с исходом уже решила подстатус; отметка «ждём телефонию» (без исхода) — нет.
            if card['stage'] == rules.IN_WORK and (not manual or not manual.get('result')):
                talked = calls.talked(call)
                fields['substage'] = 'dial' if talked else 'no_answer'
                fields['no_answer_streak'] = 0 if talked else int(card['no_answer_streak'] or 0) + 1
        queries.update_card(cursor, card['id'], **fields)
        return call_id, True

    @staticmethod
    def _keep_recruiter_phones_regular(dial_list, lines):
        """Рекрутер на линии удалённого КЦ — обычная учётка iCore Phone, не обзвон (владелец
        30.10.2026): отдел линии включает режим обзвона всем своим, а персональное «выключено»
        сильнее отдела. Ставится при посадке; здесь — страховка для посаженных раньше."""
        for line in lines:
            try:
                if dial_list.user_setting(line['user_id']) is not False:
                    dial_list.save_user_setting(line['user_id'], False)
                    log.info('hiring: у рекрутера %s на линии %s выключен режим обзвона телефона',
                             line['user_id'], line['internal_number'])
            except Exception:  # noqa: BLE001
                log.exception('hiring: не удалось выключить обзвон рекрутеру %s', line['user_id'])

    def adopt_orphan_pending_calls(self, now=None):
        """Касания «ждём исход», мимо которых телефония завела свою запись (так было до правки слияния
        30.10.2026: искали только отметки рекрутера линии): звонок переезжает в ожидание — исход,
        длительность, запись, комментарий сохраняются, лишняя запись удаляется. Возвращает число."""
        now = now or self._now()
        adopted = 0
        with self._calls_lock:
            with self._cursor() as cursor:
                for row in queries.orphan_pending_calls(cursor, now - timedelta(minutes=2), calls.MERGE_WINDOW_MINUTES):
                    body = (row['body'] or '') + (('\n' + row['pending_body']) if row.get('pending_body') else '')
                    queries.update_touch(cursor, row['pending_id'], result=row['result'], body=body,
                                         duration_sec=row['duration_sec'], meta=row['meta'])
                    queries.link_call_touch(cursor, row['call_id'], row['pending_id'])
                    queries.delete_touch(cursor, row['touch_id'])
                    adopted += 1
        if adopted:
            log.info('hiring: %d ожидающих касаний получили исход из записей телефонии', adopted)
        return adopted

    def sync_call_history(self, now=None):
        """Добор звонков по линиям рекрутеров из истории Binotel (вебхук мог не дойти). Раз в десять
        минут, окно с перекрытием; первый раз — сутки. Возвращает число записанных звонков."""
        now = now or self._now()
        self.adopt_orphan_pending_calls(now)
        dial_list = self._dial_list_service()
        if dial_list is None:
            return 0
        with self._cursor() as cursor:
            lines = queries.recruiter_lines(cursor)
            state = queries.get_integration(cursor, 'binotel') or {}
        if not lines:
            return 0
        self._keep_recruiter_phones_regular(dial_list, lines)
        last = (state.get('cursor') or {}).get('synced_at')
        since = datetime.fromisoformat(last) - timedelta(minutes=calls.HISTORY_OVERLAP_MINUTES) if last \
            else now - timedelta(hours=calls.HISTORY_FIRST_HOURS)
        tz = rules.ALMATY
        start_ts = int(since.replace(tzinfo=tz).timestamp())
        stop_ts = int(now.replace(tzinfo=tz).timestamp())
        recorded = 0
        problems = []
        for line in lines:
            try:
                client = dial_list.client_for_department(line['department_id'])
                history = client.list_calls_by_internal_number(line['internal_number'], start_ts, stop_ts)
            except Exception as exc:  # noqa: BLE001
                problems.append('линия %s: %s' % (line['internal_number'], str(exc)[:120]))
                continue
            with self._calls_lock:
                with self._cursor() as cursor:
                    for raw in history:
                        call = calls.from_history(raw)
                        if call and self._record_call(cursor, call, source='history', now=now)[1]:
                            recorded += 1
        with self._cursor() as cursor:
            queries.set_integration(cursor, 'binotel', now, state='ok' if not problems else 'error', ok=not problems,
                                    error=('; '.join(problems)[:500] or None),
                                    cursor_value={'synced_at': now.isoformat(timespec='seconds')}, received=recorded)
        return recorded

    def recording_url(self, ctx, call_id):
        """Ссылка на запись разговора у Binotel (подписанная, живёт около часа) — тому, кто видит карточку."""
        with self._cursor() as cursor:
            call = queries.get_call(cursor, call_id)
            if not call:
                raise HiringError('HIRING_NOT_FOUND', 'Звонок не найден', 404)
            self._load_visible(cursor, ctx, call['card_id'])
        if call['result'] != 'answered' or int(call['billsec'] or 0) <= 0:
            raise HiringError('HIRING_NO_RECORDING', 'Разговора не было — записи нет', 404)
        dial_list = self._dial_list_service()
        if dial_list is None or not call.get('department_id'):
            raise HiringError('HIRING_LINES_OFF', 'Записи недоступны: раздел «Удаленный КЦ» не подключён', 503)
        try:
            url = dial_list.client_for_department(call['department_id']).get_call_record_url(call['general_call_id'])
        except Exception as exc:  # noqa: BLE001
            log.warning('hiring: запись %s недоступна: %s', call['general_call_id'], exc)
            raise HiringError('HIRING_RECORDING_UNAVAILABLE', 'АТС не отдала запись. Попробуйте позже', 502)
        if not url:
            raise HiringError('HIRING_RECORDING_NOT_READY', 'Запись ещё не готова у АТС. Попробуйте через минуту', 404)
        return {'call_id': call['id'], 'url': url, 'billsec': int(call['billsec'] or 0)}

    def set_motivation(self, ctx, card_id, status, reason=None):
        """Выплачено / отмена с причиной — руководитель рекрутинга."""
        if not access.can_manage(ctx):
            raise _forbidden()
        now = self._now()
        with self._cursor() as cursor:
            row = queries.get_motivation(cursor, card_id)
            if not row:
                raise HiringError('HIRING_NOT_FOUND', 'Начисления по карточке нет', 404)
            if status == rules.MOTIVATION_PAID:
                if row['status'] != rules.MOTIVATION_COUNTED:
                    raise HiringError('HIRING_BAD_MOTIVATION', 'Выплачивается только засчитанное', 409)
                queries.set_motivation(cursor, card_id, now, status=status, paid_at=now, paid_by=ctx['user_id'])
            elif status == rules.MOTIVATION_CANCELLED:
                reason = _text(reason, 500)
                if not reason:
                    raise HiringError('HIRING_NEED_COMMENT', 'Отмена — с причиной')
                queries.set_motivation(cursor, card_id, now, status=status, cancel_reason=reason, cancelled_at=now,
                                       cancelled_by=ctx['user_id'])
            else:
                raise HiringError('HIRING_BAD_MOTIVATION', 'Статус меняется только на «Выплачено» или «Отменено»')
            return queries.get_motivation(cursor, card_id)

    def deactivate_person(self, ctx, user_id):
        """Отключить рекрутера: его открытые карточки — в пул (ТЗ 7.1)."""
        if not access.can_manage(ctx):
            raise _forbidden()
        now = self._now()
        returned = 0
        with self._cursor() as cursor:
            queries.deactivate_person(cursor, user_id, now)
            for card in queries.cards_of_recruiter_open(cursor, user_id):
                pool = rules.pool_stage_for(card['kind'])
                queries.release_assignments(cursor, card['id'], now)
                queries.update_card(cursor, card['id'], stage=pool, substage=None, recruiter_id=None, next_contact_at=None,
                                    group_id=None, office_visit_at=None, updated_at=now)
                queries.add_stage(cursor, card['id'], pool, None, ctx['user_id'], ctx['name'], now)
                queries.add_assignment(cursor, card['id'], None, 'recruiter_off', ctx['user_id'], ctx['name'], now)
                returned += 1
        return returned

    # ─── Планировщик ─────────────────────────────────────────────────────────

    def auto_return(self, now=None):
        """Без касаний месяц — карточка сама возвращается в пул, рекрутер и руководитель узнают (ТЗ 7.1, 8.3)."""
        now = now or self._now()
        deadline = rules.idle_deadline(now)
        telegram = []
        returned = 0
        with self._cursor() as cursor:
            for card in queries.idle_cards(cursor, deadline):
                pool = rules.pool_stage_for(card['kind'])
                recruiter_id = card.get('recruiter_id')
                queries.release_assignments(cursor, card['id'], now)
                queries.update_card(cursor, card['id'], stage=pool, substage=None, recruiter_id=None, next_contact_at=None,
                                    office_visit_at=None, updated_at=now)
                queries.add_stage(cursor, card['id'], pool, None, None, 'Система', now)
                queries.add_assignment(cursor, card['id'], None, 'auto_timeout', None, 'Система', now,
                                       'Без касаний %s дней' % rules.IDLE_RETURN_DAYS)
                notices.returned_to_pool(cursor, card, recruiter_id, now)
                returned += 1
                if recruiter_id:
                    telegram.append((recruiter_id, card))
            chats = queries.telegram_chats(cursor, [recruiter_id for recruiter_id, _ in telegram])
        for recruiter_id, card in telegram:
            chat_id = chats.get(int(recruiter_id))
            if chat_id:
                link = notices.card_link(self._base_url, card['id'])
                self._telegram(chat_id, notices.returned_html(card, link), notices.reply_markup(link))
        return returned

    def notify_next_contacts(self, now=None):
        """Наступила дата следующего контакта — колокол рекрутеру (ТЗ 8.3)."""
        now = now or self._now()
        sent = 0
        with self._cursor() as cursor:
            for card in queries.due_next_contacts(cursor, now):
                notices.next_contact(cursor, card, now)
                queries.update_card(cursor, card['id'], next_contact_notified=now)
                sent += 1
        return sent

    def overdue_digest(self, now=None):
        """Утренняя сводка в Telegram о просроченных задачах — только нарушения сроков (ТЗ 8.3)."""
        now = now or self._now()
        today = datetime(now.year, now.month, now.day)
        with self._cursor() as cursor:
            counts = queries.overdue_cards_by_recruiter(cursor, today)
            chats = queries.telegram_chats(cursor, counts.keys())
        sent = 0
        for recruiter_id, count in counts.items():
            chat_id = chats.get(int(recruiter_id))
            if chat_id and self._telegram(chat_id, notices.overdue_digest_html(count, self._base_url), None):
                sent += 1
        return sent

    # ─── Номер WhatsApp найма (сервис на шлюзе, hiring/whatsapp.py) ──────────

    def wa_poll(self, payload):
        """Опрос сервиса шлюза: его состояние → строка интеграции; в ответ — команда человека.

        Команда держится, пока сервис не подтвердит её номером (ack): потерянный ответ не теряет
        «Подключить». Тревога руководителям — на входе в «Остановлен» и на возврате после него."""
        payload = payload if isinstance(payload, dict) else {}
        state = str(payload.get('state') or '').strip()
        if state not in whatsapp.BRIDGE_STATES:
            raise HiringError('HIRING_WA_BAD_STATE', 'Неизвестное состояние сервиса')
        detail = whatsapp.clean_detail(payload.get('detail'))
        ack = str(payload.get('ack') or '').strip() or None
        now = self._now()
        with self._cursor() as cursor:
            row = queries.wa_lock_integration(cursor, now)
            before = whatsapp.view(row, now)['state']
            if state == whatsapp.PAIRING and detail.get('qr'):
                previous = row.get('cursor') or {}
                same = previous.get('qr') == detail['qr'] and previous.get('qr_at')
                detail['qr_at'] = previous['qr_at'] if same else now.isoformat(timespec='seconds')
            queries.wa_save_state(cursor, state, detail, whatsapp.park_text(detail) if state == whatsapp.PARKED else None,
                                  now, ok=state == whatsapp.CONNECTED)
            command = (row.get('settings') or {}).get('command') or None
            if command and ack and command.get('id') == ack:
                queries.wa_set_command(cursor, None, now)
                command = None
            alert = whatsapp.alert_for(before, state, detail)
            recipients = notices.whatsapp_alert(cursor, alert, now, tone='default' if state == whatsapp.CONNECTED else 'warning') \
                if alert else []
            chats = queries.telegram_chats(cursor, recipients) if alert and state == whatsapp.PARKED else {}
        for chat_id in chats.values():
            self._telegram(chat_id, notices.whatsapp_alert_html(alert[1], alert[2], self._base_url), None)
        busy = command or state in (whatsapp.PAIRING, whatsapp.CONNECTING)
        return {'command': {'id': command['id'], 'action': command['action']} if command else None,
                'next_poll_sec': 2 if busy else 10}

    def wa_events(self, payload):
        """Сообщения личных чатов номера от сервиса. Первое входящее чата — отклик в пуле; дальше
        чат пишется в переписку карточки, пока она открыта. Повтор пачки безопасен."""
        events = payload.get('events') if isinstance(payload, dict) else None
        if not isinstance(events, list) or not events or len(events) > whatsapp.EVENTS_MAX:
            raise HiringError('HIRING_WA_BAD_EVENTS', 'Ожидается список событий (1–%d)' % whatsapp.EVENTS_MAX)
        messages = [message for message in (whatsapp.parse_message(item) for item in events) if message]
        failures = [failure for failure in (whatsapp.parse_media_failure(item) for item in events) if failure]
        now = self._now()
        outcomes = {}
        trash = []
        with self._cursor() as cursor:
            source = queries.source_by_code(cursor, whatsapp.SOURCE_CODE)
            for message in messages:
                outcome = self._wa_message(cursor, message, source, now, trash)
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
            for failure in failures:
                if queries.wa_media_failed(cursor, failure['id'], failure['reason'], now):
                    outcomes['media_failed'] = outcomes.get('media_failed', 0) + 1
            queries.wa_add_received(cursor, outcomes.get('created', 0), now)
        self._wa_drop_files(trash)
        return {'accepted': len(events), 'outcomes': outcomes}

    def _wa_message(self, cursor, message, source, now, trash=None):
        key = message['key']
        if message['revoke_of']:
            count, objects = queries.wa_revoke_message(cursor, key, message['revoke_of'], now)
            if trash is not None:
                trash.extend(objects)
            return 'revoked' if count else 'missing'
        if message['edit_of']:
            return 'edited' if queries.wa_edit_message(cursor, key, message['edit_of'], message['text'], now) else 'missing'
        if queries.wa_insert_message(cursor, key, message, now) is None:
            return 'duplicate'
        inbound = not message['from_me']
        phone = message['phone'] or (('+' + message['phone_digits'])[:16] if message['phone_digits'] else None)
        chat = queries.wa_touch_chat(cursor, key, message['chat'], phone, message['lid'], message['push_name'],
                                     message['at'] or now, inbound, now)
        # Номер стал известен — чат «только LID» доклеивается к ключу номера вместе с перепиской.
        if message['lid'] and not key.startswith('lid:') and queries.wa_chat_by_lid(cursor, message['lid'], lock=True):
            queries.wa_move_chat(cursor, 'lid:' + message['lid'], key, now)
            chat = queries.wa_get_chat(cursor, key)
        if not inbound:
            return self._wa_phone_reply(cursor, chat, message, now) if chat.get('card_id') else 'stored'
        if not message['phone'] or not source or not source['is_active']:
            return 'stored'
        if chat.get('card_id'):
            card = queries.get_card(cursor, chat['card_id'])
            if card and not card['is_closed']:
                return 'stored'
        outcome, card_id = self._intake_one(
            cursor, phone=message['phone'], kind=rules.KIND_RESPONSE, source=source, now=now, by_name='WhatsApp',
            full_name=chat.get('push_name') or '', platform_ref='wa:%s' % message['id'],
            campaign=whatsapp.campaign_of(message['ad']), received_at=message['at'] or now,
            comment=whatsapp.first_message_comment(message))
        if card_id:
            queries.wa_set_chat_card(cursor, key, card_id, now)
        return outcome

    def _wa_phone_reply(self, cursor, chat, message, now):
        """Ответ с телефона найма в чате карточки (владелец 09.11.2026: 58 из 59 откликов WhatsApp висели
        в «Откликах», хотя переписка уже шла). Первый ответ берёт карточку из пула в работу к тому, кто
        отвечает с телефона (настройка номера), — со временем ответа; дальше каждый день переписки —
        касание WhatsApp закреплённого рекрутера. Автоответ WhatsApp Business не считается."""
        at = message['at'] or now
        last_in = queries.wa_last_inbound(cursor, chat['key'], at)
        if last_in is not None and at - last_in < whatsapp.AUTO_REPLY_GAP:
            return 'auto_reply'
        card = queries.get_card(cursor, chat['card_id'], lock=True)
        # Наше сообщение раньше отклика (напоминание тому, кто писал до подключения номера) — не ответ
        # по этой карточке: при прогоне задним числом взяло бы её в работу раньше, чем она появилась.
        if not card or card['is_closed'] or (card.get('received_at') and at < card['received_at']):
            return 'stored'
        outcome = 'stored'
        if card['stage'] in rules.POOL_STAGES:
            recruiter = self._wa_phone_recruiter(cursor)
            if not recruiter or not queries.claim_card(cursor, card['id'], recruiter['id'], at):
                return 'stored'
            queries.add_stage(cursor, card['id'], rules.IN_WORK, None, recruiter['id'], recruiter['name'], at)
            queries.add_assignment(cursor, card['id'], recruiter['id'], 'take', recruiter['id'], recruiter['name'], at,
                                   'Ответ с телефона найма')
            card = queries.get_card(cursor, card['id'])
            outcome = 'taken'
        if not card.get('recruiter_id'):
            return outcome
        day_start = datetime.combine(at.date(), datetime.min.time())
        if queries.wa_phone_touched(cursor, card['id'], day_start, day_start + timedelta(days=1)):
            return outcome
        author = int(card['recruiter_id'])
        queries.add_touch(cursor, card['id'], rules.TOUCH_WHATSAPP, None, whatsapp.phone_reply_text(message), author,
                          queries.user_name(cursor, author), at, meta={'source': whatsapp.PHONE_TOUCH, 'wa_id': message['id']})
        fields = {'updated_at': now}
        if not card.get('last_touch_at') or card['last_touch_at'] < at:
            fields['last_touch_at'] = at
        if card['stage'] == rules.IN_WORK and not card.get('substage'):
            fields['substage'] = 'whatsapp'
        queries.update_card(cursor, card['id'], **fields)
        return outcome if outcome == 'taken' else 'touched'

    def _wa_phone_recruiter(self, cursor):
        """Кто отвечает с телефона найма — если выбран и ещё может вести карточки."""
        row = queries.get_integration(cursor, whatsapp.INTEGRATION) or {}
        user_id = _int((row.get('settings') or {}).get('phone_recruiter_id'))
        if not user_id:
            return None
        names = {int(option['id']): option['name'] for option in queries.wa_recruiter_options(cursor)}
        return {'id': user_id, 'name': names[user_id]} if user_id in names else None

    def wa_settings(self, ctx, payload):
        """«Отвечает с телефона». Выбор применяется и к уже идущей переписке: ответы с телефона
        прогоняются по тем же правилам, как если бы настройка стояла с самого начала (уже отвеченные
        карточки уходят в работу со временем ответа, касания — по дням)."""
        if not access.can_manage(ctx):
            raise _forbidden('Подключение WhatsApp ведёт руководитель рекрутинга')
        raw = (payload or {}).get('phone_recruiter_id')
        user_id = None if raw in (None, '') else _int(raw)
        if raw not in (None, '') and not user_id:
            raise HiringError('HIRING_BAD_RECRUITER', 'Этот человек не рекрутер')
        now = self._now()
        applied = 0
        with self._cursor() as cursor:
            queries.wa_lock_integration(cursor, now)
            if user_id and user_id not in {int(option['id']) for option in queries.wa_recruiter_options(cursor)}:
                raise HiringError('HIRING_BAD_RECRUITER', 'Этот человек не рекрутер')
            queries.wa_save_settings(cursor, {'phone_recruiter_id': user_id}, now)
            if user_id:
                for reply in queries.wa_phone_replies(cursor):
                    message = {'id': reply['wa_id'], 'at': reply['sent_at'], 'kind': reply['kind'], 'text': reply['body']}
                    chat = {'key': reply['chat_key'], 'card_id': reply['card_id']}
                    if self._wa_phone_reply(cursor, chat, message, now) == 'taken':
                        applied += 1
        data = self.wa_status(ctx)
        data['applied'] = applied
        return data

    def wa_status(self, ctx):
        if not access.can_manage(ctx):
            raise _forbidden('Подключение WhatsApp ведёт руководитель рекрутинга')
        now = self._now()
        with self._cursor() as cursor:
            row = queries.get_integration(cursor, whatsapp.INTEGRATION)
            unmatched = queries.wa_unmatched(cursor)
            options = queries.wa_recruiter_options(cursor)
        data = whatsapp.view(row, now)
        data['unmatched'] = unmatched
        data['recruiter_options'] = options
        return data

    def wa_command(self, ctx, action):
        """«Подключить» (QR), «Возобновить» после остановки, «Отвязать» — команда сервису на шлюзе."""
        if not access.can_manage(ctx):
            raise _forbidden('Подключение WhatsApp ведёт руководитель рекрутинга')
        action = str(action or '').strip()
        now = self._now()
        with self._cursor() as cursor:
            row = queries.wa_lock_integration(cursor, now)
            allowed, reason = whatsapp.can_command(action, whatsapp.view(row, now)['state'], row.get('cursor') or {})
            if not allowed:
                raise HiringError('HIRING_WA_COMMAND', reason, 409)
            queries.wa_set_command(cursor, {'id': uuid.uuid4().hex[:16], 'action': action,
                                            'at': now.isoformat(timespec='seconds'), 'by': ctx['user_id']}, now)
        return self.wa_status(ctx)

    def card_whatsapp(self, ctx, card_id):
        """Переписка карточки с номером найма: чаты по телефону и WhatsApp карточки. Удалённому рекрутеру —
        только в своих карточках, как и сам телефон (ТЗ 2)."""
        with self._cursor() as cursor:
            card = self._load_visible(cursor, ctx, card_id)
            if not access.sees_phone(ctx, card):
                raise HiringError('HIRING_PHONE_HIDDEN', 'Переписка откроется, когда возьмёте карточку в работу', 403)
            chats = queries.wa_chats_for_phones(cursor, [card.get('phone'), card.get('whatsapp')])
            messages = queries.wa_messages(cursor, [chat['key'] for chat in chats])
        sign = self._storage.sign if self._storage is not None else None
        for message in messages:
            if message.get('ad'):
                message['ad_label'] = whatsapp.campaign_of(message['ad'])
            if message['kind'] != 'text' and not message.get('deleted_at'):
                message['media'] = wa_media.public_view(message['kind'], message.get('media'), sign)
        return {'chats': chats, 'messages': messages}

    # ─── Файлы сообщений и расшифровка голосовых (владелец 09.11.2026) ───────

    def wa_media(self, wa_id, data):
        """Файл сообщения от шлюза: в бакет, путь — в сообщение; голосовое и аудио — в расшифровку.
        Сообщение ещё не дошло событием — 404, шлюз повторит. Повтор файла безопасен. Бакет — без открытой
        транзакции: долгая загрузка не держит соединение с базой (выкладка ждала бы её на DDL)."""
        wa_id = whatsapp.clean_message_id(wa_id)
        if not wa_id:
            raise HiringError('HIRING_WA_BAD_MEDIA', 'Неверный id сообщения')
        if not data:
            raise HiringError('HIRING_WA_BAD_MEDIA', 'Пустой файл')
        if len(data) > wa_media.MAX_BYTES:
            raise HiringError('HIRING_WA_MEDIA_TOO_LARGE', 'Файл больше 25 МБ', 413)
        if self._storage is None or not self._storage.available():
            raise HiringError('HIRING_WA_NO_STORAGE', 'Хранилище файлов не настроено', 503)
        with self._cursor() as cursor:
            rows = queries.wa_messages_by_id(cursor, wa_id)
        if not rows:
            raise HiringError('HIRING_WA_MEDIA_UNKNOWN', 'Сообщение ещё не пришло', 404)
        targets = [row for row in rows if row['kind'] in wa_media.DOWNLOADABLE and not row.get('deleted_at')
                   and not (row.get('media') or {}).get('object')]
        if not targets:
            return {'stored': False, 'reason': 'already' if any((r.get('media') or {}).get('object') for r in rows) else 'skipped'}
        now = self._now()
        first = targets[0]
        media = first.get('media') or {}
        ctype = wa_media.content_type(media.get('mime'))
        path = wa_media.object_path(first['id'], ctype, media.get('file_name'), now)
        self._storage.upload(path, data, ctype)
        stored = {'object': path, 'stored_type': ctype, 'stored_size': len(data), 'stored_at': now.isoformat(timespec='seconds')}
        transcribe = first['kind'] in wa_media.TRANSCRIBED and self._transcriber is not None
        if transcribe:
            stored['transcript'] = {'status': 'pending', 'attempts': 0}
        saved = []
        with self._cursor() as cursor:
            for target in targets:
                if queries.wa_media_stored(cursor, target['id'], stored):
                    saved.append(target['id'])
        if not saved:
            # Пока файл ехал в бакет, его положил другой запрос или сообщение удалили — копия лишняя.
            self._wa_drop_files([path])
            return {'stored': False, 'reason': 'already'}
        if transcribe:
            for message_id in saved:
                self._spawn(self._wa_transcribe, message_id)
        return {'stored': True}

    def _wa_drop_files(self, paths):
        for path in paths or ():
            try:
                self._storage.delete(path)
            except Exception:  # noqa: BLE001
                log.warning('WhatsApp найма: файл не удалён из бакета', exc_info=True)

    TRANSCRIPT_ATTEMPTS = 3
    TRANSCRIPT_RETRY = timedelta(minutes=2)
    TRANSCRIPT_STALE = timedelta(minutes=15)

    def _wa_transcribe(self, message_id):
        """Расшифровка голосового — своим потоком. Захват строки в базе: два процесса выкладки одно и то же
        не распознают. Файл и запрос к модели — без открытой транзакции."""
        now = self._now()
        stamp = now.isoformat(timespec='seconds')
        try:
            with self._cursor() as cursor:
                row = queries.wa_claim_transcript(cursor, message_id, stamp,
                                                  (now - self.TRANSCRIPT_STALE).isoformat(timespec='seconds'))
        except Exception:  # noqa: BLE001
            log.exception('WhatsApp найма: голосовое %s не взято в расшифровку', message_id)
            return None
        if not row:
            return None
        media = row.get('media') or {}
        attempts = int((media.get('transcript') or {}).get('attempts') or 0) + 1
        try:
            raw = self._storage.download(media['object'])
            record = wa_media.transcript_record(
                self._transcriber(raw, media.get('stored_type') or media.get('mime') or ''), self._now())
        except Exception as exc:  # noqa: BLE001 — VoiceError и сбои бакета: повтор, пока есть попытки
            retry = getattr(exc, 'kind', 'retry') == 'retry' and attempts < self.TRANSCRIPT_ATTEMPTS
            record = {'status': 'pending' if retry else 'failed', 'error': str(exc)[:200], 'attempts': attempts,
                      'next_at': (self._now() + self.TRANSCRIPT_RETRY).isoformat(timespec='seconds')}
            if not retry:
                log.warning('WhatsApp найма: голосовое %s не расшифровано: %s', message_id, str(exc)[:200])
        record['attempts'] = attempts
        with self._cursor() as cursor:
            queries.wa_save_transcript(cursor, message_id, record)
        return record['status']

    def _wa_sweep_transcripts(self, now):
        """Сторож: голосовые, чья расшифровка ждёт повтора или зависла на упавшем процессе."""
        if self._transcriber is None or self._storage is None:
            return 0
        with self._cursor() as cursor:
            due = queries.wa_transcripts_due(cursor, now.isoformat(timespec='seconds'),
                                             (now - self.TRANSCRIPT_STALE).isoformat(timespec='seconds'))
        for message_id in due:
            self._spawn(self._wa_transcribe, message_id)
        return len(due)

    def wa_watchdog(self):
        """Сторож (раз в минуту): сервис замолчал — «не на связи» и тревога; телефон с номером давно не
        выходил в WhatsApp — тревога раз в сутки (через 14 дней привязка слетает)."""
        now = self._now()
        alert, chats = None, {}
        with self._cursor() as cursor:
            row = queries.wa_lock_integration(cursor, now)
            detail = row.get('cursor') or {}
            last_run = row.get('last_run_at')
            if row.get('state') in whatsapp.BRIDGE_STATES and last_run and now - last_run > whatsapp.OFFLINE_AFTER:
                queries.wa_save_state(cursor, whatsapp.OFFLINE, detail, 'Сервис на шлюзе не выходит на связь', now,
                                      heartbeat=False)
                alert = ('wa:offline:%s' % last_run.isoformat(timespec='minutes'),
                         'WhatsApp найма: сервис на шлюзе не на связи',
                         'С %s новые сообщения в воронку не попадают' % last_run.strftime('%d.%m %H:%M'))
                chats = queries.telegram_chats(cursor, notices.whatsapp_alert(cursor, alert, now))
            elif row.get('state') == whatsapp.CONNECTED:
                days = whatsapp.phone_seen_days(detail, now)
                if days is not None and days >= whatsapp.PHONE_ALERT_DAYS:
                    alert = ('wa:phone:%s' % now.date().isoformat(), 'WhatsApp найма: телефон давно не в сети',
                             'Телефон с номером не выходил в WhatsApp %d дн. Откройте на нём WhatsApp Business — '
                             'через 14 дней без телефона привязка слетает' % days)
                    chats = queries.telegram_chats(cursor, notices.whatsapp_alert(cursor, alert, now))
        for chat_id in chats.values():
            self._telegram(chat_id, notices.whatsapp_alert_html(alert[1], alert[2], self._base_url), None)
        try:
            self._wa_sweep_transcripts(now)
        except Exception:  # noqa: BLE001 — сторож состояния важнее повтора расшифровки
            log.exception('WhatsApp найма: повтор расшифровок не запустился')
        return alert[0] if alert else None

    def _telegram(self, chat_id, html_text, reply_markup):
        if not self._send_telegram:
            return False
        try:
            response = self._send_telegram(chat_id, html_text, reply_markup)
            status = getattr(response, 'status_code', 200)
            if status != 200:
                log.warning('hiring: Telegram %s ответил %s', chat_id, status)
                return False
            return True
        except Exception:  # noqa: BLE001
            log.exception('hiring: Telegram не отправлен (%s)', chat_id)
            return False


def _safe_moment(value):
    try:
        return _parse_moment(value)
    except HiringError:
        return None
