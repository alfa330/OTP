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
import time
import uuid
from datetime import datetime, timedelta

import psycopg2

from . import access, calls, hh, notices, queries, rules, upload, wa_chat, wa_media, whatsapp

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


READ_ONLY_MESSAGE = 'Карточка открыта вам только для чтения'


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
    def __init__(self, cursor_factory, *, send_telegram=None, web_app_base_url=None, now=None,
                 dial_list=None, storage=None, transcriber=None, spawn=None):
        """dial_list — () -> сервис «Удаленного КЦ» или None: клиент Binotel отдела для истории звонков
        линии рекрутера и ссылки на запись разговора. storage — бакет файлов WhatsApp найма
        (wa_media.Storage), transcriber — (байты, тип) -> расшифровка голосового, spawn — запуск фоновой
        работы (по умолчанию свой поток; тесты делают её сразу)."""
        self._cursor = cursor_factory
        self._send_telegram = send_telegram
        self._base_url = web_app_base_url
        self._now = now or rules.now_almaty
        self._dial_list = dial_list
        self._storage = storage
        self._transcriber = transcriber
        self._spawn = spawn or _spawn_thread
        self._pull_lock = threading.Lock()
        self._calls_lock = threading.Lock()
        # Когда последний раз открывали окно «Чаты»: пока в нём работают, шлюз опрашивает чаще (ответ уходит за
        # секунды, а не за 10 с опроса). В памяти процесса — подсказка скорости, не состояние.
        self._wa_ui_seen = 0.0

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
        """Видит ли человек карточку — то же правило, что queries.scope_clause. Тренер по роли видит все карточки,
        но только читает (владелец 19.11.2026): кто вправе что-то в карточке делать — HiringService.involved."""
        return access.can_read_all(ctx) or HiringService.involved(ctx, card)

    @staticmethod
    def involved(ctx, card):
        """Карточка «своя» — человек в ней действует, а не только читает: руководитель, рекрутер в пуле и в своих,
        тренер в своих группах с приглашения на обучение, глава отдела найма."""
        caps = access.capabilities(ctx)
        if caps['lead']:
            return True
        if caps['recruiter'] and (card['stage'] in rules.POOL_STAGES and not card['is_closed']):
            return True
        # Владелец — только действующий рекрутер: снятая выдача снимает и права на свои карточки
        # (в списках их уже нет — queries.scope_clause, и по card_id не должно быть).
        if caps['recruiter'] and card.get('recruiter_id') and int(card['recruiter_id']) == int(ctx['user_id']):
            return True
        if (card.get('group_id') and int(card['group_id']) in set(caps['trainer_group_ids'])
                and card.get('stage') not in rules.TRAINER_HIDDEN_STAGES):
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
    def _wa_contact_since(cursor, card):
        """С какого момента наше сообщение в WhatsApp засчитывается связью с кандидатом: с начала нынешнего
        круга карточки. Круг начинается с поступления — ответ с телефона, пока карточка ждала в пуле, тоже
        связь (так рекрутер и работает), а переписка до отклика — нет, как и при взятии в работу ответом с
        телефона. Заново круг начинается с возврата в пул и с повторной активации: переписка прошлого
        закрепления новому рекрутеру не засчитывается, как и его звонки (касания считаются от taken_at)."""
        restart = queries.last_cycle_restart(cursor, card['id'])
        moments = [moment for moment in (card.get('received_at'), restart) if moment]
        return max(moments) if moments else None

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
        employee — телефон действующего сотрудника, карточки нет (ТЗ 5.3).
        Источник решает вызывающий: отклик WhatsApp по рекламе приходит уже «Таргетом» (_wa_message)."""
        employee = queries.employee_by_phone_key(cursor, phone[-10:])
        if employee and str(employee['status'] or '').lower() not in ('fired', 'dismissal'):
            return 'employee', None
        open_card = queries.open_card_by_phone(cursor, phone, lock=True)
        if open_card:
            self._repeat(cursor, open_card, source, received_at or now, now, platform_ref, vacancy_text)
            self._target_over_plain_source(cursor, open_card, source)
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
                self._target_over_plain_source(cursor, open_card, source)
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

    @staticmethod
    def _target_over_plain_source(cursor, card, source):
        """Повторный отклик с «Таргета» (WhatsApp по рекламе или вебхук с источником target) у открытой карточки,
        пришедшей в WhatsApp без рекламы или заведённой руками, делает её «Таргетом» (владелец 09.11.2026: «те что
        пришли с таргета нужно автоматом отмечать как Таргет»; с 20.11.2026 это источник, а не метка-канал). Источник
        площадки (hh, OLX, Kaspi) и «Приведи друга» не трогается — это уже канал (rules.SOURCES_WITHOUT_CHANNEL)."""
        if (source or {}).get('code') != rules.SOURCE_TARGET or card.get('source_code') not in rules.SOURCES_WITHOUT_CHANNEL:
            return 0
        return queries.switch_card_source(cursor, card['id'], card['source_id'], source['id'])

    def _repeat(self, cursor, card, source, received_at, now, platform_ref=None, vacancy_text=None):
        body = 'Повторный отклик · %s' % (source.get('name') or '')
        if vacancy_text:
            body += ' · ' + str(vacancy_text)[:120]
        queries.add_touch(cursor, card['id'], rules.TOUCH_REPEAT, source.get('code'), body, None, None, now,
                          meta={'source_id': source['id'], 'received_at': received_at.isoformat(timespec='minutes'),
                                'platform_ref': platform_ref})
        queries.update_card(cursor, card['id'], updated_at=now)
        if card.get('stage') == rules.NO_RESPONSE:
            # Молчавший кандидат откликнулся снова — он на связи: снова «В работе» (владелец 20.11.2026). Отклик пришёл
            # сообщением на номер найма (wa:…) — значит, переписка.
            by_whatsapp = str(platform_ref or '').startswith('wa:')
            self._back_to_work(cursor, card, 'whatsapp' if by_whatsapp else None,
                               'Кандидат написал в WhatsApp' if by_whatsapp else 'Повторный отклик', now)
        notices.repeat_response(cursor, card, source.get('name'), now)

    @staticmethod
    def _back_to_work(cursor, card, substage, reason, now):
        """Из «Не отвечает звонок/чат» — снова «В работе» у того же рекрутера: кандидат ответил (дозвон, сообщение в
        WhatsApp, повторный отклик). Вход в «В работе» начинает круг заново: 72 часа молчания считаются с него."""
        queries.update_card(cursor, card['id'], stage=rules.IN_WORK, substage=substage, no_answer_streak=0, updated_at=now)
        queries.add_stage(cursor, card['id'], rules.IN_WORK, substage, None, 'Система', now)
        queries.add_touch(cursor, card['id'], rules.TOUCH_SYSTEM, 'back_to_work', '%s — снова «В работе»' % reason,
                          None, 'Система', now)

    def archive_silent(self, now=None):
        """Сторож «Не отвечает звонок/чат» (владелец 20.11.2026: «те, кто в работе, не отвечают ни на звонок, ни на
        ватсап в течение 72 часов, уходили в «Не отвечает звонок/чат». Это отдельный список, как архив. И сразу тех,
        кто подходит, отправить туда»): 72 часа с первой попытки связаться без единого ответа — карточка уходит из
        «В работе» и остаётся за рекрутером. Правило перепроверяется под блокировкой карточки: ответ, пришедший во время
        прогона, её оставляет. Возвращает, сколько карточек ушло."""
        now = now or self._now()
        deadline = now - timedelta(hours=rules.NO_RESPONSE_HOURS)
        moved = 0
        with self._cursor() as cursor:
            for card_id in queries.silent_cards(cursor, deadline):
                card = queries.get_card(cursor, card_id, lock=True)
                if not card or card['is_closed'] or card['stage'] != rules.IN_WORK:
                    continue
                if not queries.silent_cards(cursor, deadline, card_id=card_id):
                    continue
                queries.update_card(cursor, card_id, stage=rules.NO_RESPONSE, substage=None, updated_at=now)
                queries.add_stage(cursor, card_id, rules.NO_RESPONSE, None, None, 'Система', now)
                queries.add_touch(cursor, card_id, rules.TOUCH_SYSTEM, 'no_response',
                                  'Не отвечает на звонки и WhatsApp %d часа — в «%s»'
                                  % (rules.NO_RESPONSE_HOURS, rules.STAGE_LABELS[rules.NO_RESPONSE]), None, 'Система', now)
                moved += 1
        if moved:
            log.info('Воронка найма: в «%s» ушло карточек: %d', rules.STAGE_LABELS[rules.NO_RESPONSE], moved)
        return moved

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

    # ─── Мост hh.kz на шлюзе (hiring/hh.py, cdr_bridge/hh_bridge.py) ─────────

    # Паузу моста до следующего опроса называет портал (rules.hh_poll_seconds), случайную: днём 2–5 минут, ночью
    # 45 минут – 2 часа — hh видит по маленькому запросу счётчиков на вакансию, не чаще, чем смотрел бы рекрутер.
    # Сбой входа или сайта дольше этого — тревога руководителям; короткий сбой (hh ответил 502 один раз) — тишина.
    HH_PROBLEM_ALERT_AFTER = timedelta(minutes=10)

    def hh_agent_push(self, payload):
        """Доклад моста hh.kz (владелец 23.11.2026: «по данному сайту нужно автоматизировать передачу откликов в icore
        в воронку найма»): вход в кабинет, вакансии и новые отклики. Отклик принимается, как отклики всех площадок, через
        _intake_one: карточка в «Отклики» пула с источником HeadHunter, телефон открытой карточки — повторный отклик. Тема
        hh принимается один раз (hiring_hh_topics). В ответ — исход по каждой теме и настройки: с какого момента отклики
        нужны и какие вакансии выключены."""
        payload = payload if isinstance(payload, dict) else {}
        session, detail = hh.clean_session(payload)
        raw_items = payload.get('items') if isinstance(payload.get('items'), list) else []
        if len(raw_items) > hh.MAX_ITEMS:
            raise HiringError('HIRING_HH_TOO_MANY', 'Не больше %d откликов за раз' % hh.MAX_ITEMS)
        vacancies = hh.clean_vacancies(payload.get('vacancies'))
        now = self._now()
        results, counts = [], {}
        with self._cursor() as cursor:
            row = queries.hh_lock_integration(cursor, now)
            settings = row.get('settings') or {}
            previous = row.get('cursor') or {}
            since = hh.since(settings)
            source = queries.source_by_code(cursor, rules.SOURCE_HH)
            if not source:
                raise HiringError('HIRING_HH_NO_SOURCE', 'В справочнике нет источника HeadHunter', 503)
            icore_vacancies = queries.hh_vacancy_ids(cursor)
            for raw in raw_items:
                item = hh.clean_item(raw)
                if item is None:
                    topic = str(raw.get('topic_id') or '')[:20] if isinstance(raw, dict) else ''
                    results.append({'topic_id': topic, 'outcome': 'invalid'})
                    counts['invalid'] = counts.get('invalid', 0) + 1
                    continue
                outcome = self._hh_take(cursor, item, settings, since, source, icore_vacancies, now)
                results.append({'topic_id': str(item['topic_id']), 'outcome': outcome})
                counts[outcome] = counts.get(outcome, 0) + 1
            ok = session == hh.SESSION_OK
            # Кабинет не читается — мост отступает на свою паузу, если она длиннее названной: сторож ждёт его не
            # раньше большей из двух.
            next_poll = rules.hh_poll_seconds(now)
            bridge = {'version': str(payload.get('version') or '')[:40] or None, 'session': session, 'detail': detail,
                      'seen_at': now.isoformat(timespec='seconds'), 'expect_sec': max(next_poll, hh.clean_pause(payload))}
            # Начало беды со входом — чтобы тревога была одна на случай, а короткий сбой не будил никого.
            problem_since = None if ok else ((previous.get('bridge') or {}).get('problem_since')
                                             if (previous.get('bridge') or {}).get('session') == session else None)
            if not ok:
                bridge['problem_since'] = problem_since or bridge['seen_at']
            account = hh.clean_account(payload.get('account'))
            report = {'bridge': bridge,
                      'account': account if any(account.values()) else (previous.get('account') or {}),
                      'vacancies': vacancies if vacancies is not None else (previous.get('vacancies') or [])}
            error = None if ok else hh.SESSION_TEXTS[session] + (' (%s)' % detail if detail else '')
            queries.hh_save_report(cursor, now, state='ok' if ok else 'error', error=error, report=report,
                                   received=counts.get('created', 0) + counts.get('repeat', 0), ok=ok)
            if ok:
                queries.wa_alerts_read(cursor, now, kind=notices.KIND_HH)
            else:
                started = hh.parse_moment(bridge['problem_since']) or now
                if now - started >= self.HH_PROBLEM_ALERT_AFTER:
                    notices.hh_alert(cursor, ('hh:%s:%s' % (session, started.isoformat(timespec='minutes')),
                                              'HeadHunter: новые отклики не приходят', error), now)
        taken = {key: value for key, value in counts.items() if key != 'known'}
        if taken:
            log.info('Воронка найма: мост hh принёс откликов %s', taken)
        return {'results': results,
                'settings': {'since': since.replace(tzinfo=rules.ALMATY).isoformat(timespec='seconds'),
                             'off': hh.off_ids(settings),
                             'since_by_vacancy': {key: moment.replace(tzinfo=rules.ALMATY).isoformat(timespec='seconds')
                                                  for key, moment in hh.since_by_vacancy(settings).items()}},
                'next_poll_sec': next_poll}

    def _hh_take(self, cursor, item, settings, since, source, icore_vacancies, now):
        """Одна тема hh → исход. Тема занимается первой: повтор той же темы — «known», без второй карточки."""
        if not queries.hh_claim_topic(cursor, item, now):
            return 'known'
        rule = hh.vacancy_rule(settings, item['vacancy_id'])
        card_id = None
        phone = rules.normalize_phone(item['phone'])
        if not rule['on']:
            outcome = 'vacancy_off'
        elif item['responded_at'] < (rule['since'] or since):
            outcome = 'before_since'
        elif not phone:
            outcome = 'no_phone'
        else:
            outcome, card_id = self._intake_one(
                cursor, phone=phone, kind=rules.KIND_RESPONSE, source=source, now=now, by_name='HeadHunter',
                full_name=item['full_name'], city=item['city'],
                vacancy_id=rule['vacancy_id'] if rule['vacancy_id'] in icore_vacancies else None,
                vacancy_text=item['vacancy_name'], platform_ref=item['resume_url'] or 'hh:topic:%s' % item['topic_id'],
                received_at=item['responded_at'])
        queries.hh_finish_topic(cursor, item['topic_id'], outcome, card_id)
        return outcome

    def hh_watchdog(self):
        """Сторож моста hh.kz (раз в 5 минут): мост молчит дольше, чем положено при названной ему паузе
        (rules.hh_silent_after: днём 20 минут, ночью — полторы паузы), — «не на связи» и одна тревога руководителям
        рекрутинга. Снова на связи — тревоги гасит его же доклад. Мост ни разу не приходил — молчим: интеграция
        «не настроена», а не сломана."""
        now = self._now()
        with self._cursor() as cursor:
            row = queries.hh_lock_integration(cursor, now)
            bridge = (row.get('cursor') or {}).get('bridge') or {}
            seen_at = hh.parse_moment(bridge.get('seen_at'))
            queries.wa_alerts_read(cursor, now, kind=notices.KIND_HH, not_users=queries.alert_user_ids(cursor))
            if seen_at is None or now - seen_at <= rules.hh_silent_after(bridge.get('expect_sec')):
                return None
            text = ('Мост hh.kz на шлюзе не выходит на связь с %s — новые отклики не приходят'
                    % seen_at.strftime('%d.%m %H:%M'))
            queries.hh_mark_state(cursor, now, 'error', text)
            key = 'hh:silent:%s' % seen_at.isoformat(timespec='minutes')
            notices.hh_alert(cursor, (key, 'HeadHunter: мост не на связи', text), now)
            return key

    def hh_settings(self, vacancies_raw):
        """Правка руководителя: какие вакансии hh отслеживать и с какой вакансией iCORE (права проверяет ручка).
        Включённая после выключения — с этой минуты (hh.merge_vacancy_settings)."""
        now = self._now()
        with self._cursor() as cursor:
            row = queries.hh_lock_integration(cursor, now)
            try:
                vacancies = hh.clean_vacancy_settings(vacancies_raw, queries.hh_vacancy_ids(cursor))
            except ValueError as exc:
                raise HiringError('HIRING_BAD_SETTINGS', str(exc))
            vacancies = hh.merge_vacancy_settings((row.get('settings') or {}).get('vacancies'), vacancies, now)
            queries.hh_save_settings(cursor, now, vacancies)
        return vacancies

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
            if not self.involved(ctx, card):
                raise _forbidden(READ_ONLY_MESSAGE)
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
                elif kind == rules.TOUCH_WHATSAPP and card.get('substage') != 'no_answer':
                    # Наше сообщение — ещё не ответ (владелец 09.11.2026: «Недозвон / не ответил» — не ответил ни
                    # на звонок, ни на WhatsApp): статус снимает ответ кандидата (_wa_candidate_answered).
                    fields['substage'] = 'whatsapp'
            queries.update_card(cursor, card_id, **fields)
            if card['stage'] == rules.NO_RESPONSE and kind == rules.TOUCH_CALL and result == 'dial':
                self._back_to_work(cursor, card, 'dial', 'Дозвонились', now)
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
            if card['stage'] == rules.NO_RESPONSE and not card['is_closed'] and result == 'dial':
                self._back_to_work(cursor, card, 'dial', 'Дозвонились', now)
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
        """Поля карточки: ФИО, WhatsApp, город, вакансия, «кто привёл», комментарий, дата следующего контакта,
        источник."""
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
            if 'source_id' in payload:
                source_id = self._card_source(cursor, card, payload.get('source_id'))
                if source_id != card.get('source_id'):
                    # Источник — разрез отчёта «Источники»: у завершённой карточки его правит только руководитель, а
                    # смена видна в ленте карточки — кто и с какого на какой (разбор 20.11.2026).
                    if card['is_closed'] and not access.capabilities(ctx)['lead']:
                        raise _forbidden('Источник завершённой карточки меняет руководитель рекрутинга')
                    fields['source_id'] = source_id
                    queries.add_touch(cursor, card_id, rules.TOUCH_SYSTEM, 'source_changed', 'Источник: %s → %s' % (
                        card.get('source_name') or '—', queries.get_source(cursor, source_id)['name']),
                        ctx['user_id'], ctx['name'], now, meta={'from_source_id': card.get('source_id'), 'to_source_id': source_id})
            queries.update_card(cursor, card_id, **fields)
            return queries.get_card(cursor, card_id)

    @staticmethod
    def _card_source(cursor, card, raw):
        """Источник карточки (владелец 20.11.2026: каналы слиты с источниками — то, что рекрутер ставил меткой-каналом,
        теперь источник). Источник обязателен и того же вида, что карточка: отклику — источник откликов, контакту —
        база. Выключенный руководителем источник не выбирается, а у карточки, где он уже стоит, остаётся."""
        source_id = _int(raw)
        if source_id and card.get('source_id') and source_id == int(card['source_id']):
            return source_id
        source = queries.get_source(cursor, source_id) if source_id else None
        if not source:
            raise HiringError('HIRING_BAD_SOURCE', 'Такого источника нет в справочнике')
        kind = rules.SOURCE_CONTACTS if card.get('kind') == rules.KIND_CONTACT else rules.SOURCE_RESPONSES
        if source['kind'] != kind:
            raise HiringError('HIRING_BAD_SOURCE', 'Отклику подходит источник откликов, контакту — база контактов'
                              if kind == rules.SOURCE_RESPONSES else 'Контакту подходит только база контактов')
        if not source['is_active']:
            raise HiringError('HIRING_BAD_SOURCE', 'Источник «%s» выключен руководителем — выберите другой' % source['name'])
        return source_id

    # ─── Источники (владелец 20.11.2026: каналы слиты с источниками, цвет — у источника) ─────

    @staticmethod
    def _source_fields(payload, *, partial):
        """Имя, вид и цвет источника из запроса: имя — до SOURCE_NAME_MAX знаков без лишних пробелов, вид — отклики или
        контакты, цвет — из палитры (пусто — серый)."""
        fields = {}
        if not partial or 'name' in payload:
            name = ' '.join(str(payload.get('name') or '').split())
            if not name:
                raise HiringError('HIRING_BAD_SOURCE', 'Название источника пустое')
            if len(name) > rules.SOURCE_NAME_MAX:
                raise HiringError('HIRING_BAD_SOURCE', 'Название источника — не длиннее %d знаков' % rules.SOURCE_NAME_MAX)
            fields['name'] = name
        if not partial:
            kind = str(payload.get('kind') or '').strip()
            if kind not in (rules.SOURCE_CONTACTS, rules.SOURCE_RESPONSES):
                raise HiringError('HIRING_BAD_SOURCE', 'Укажите вид источника: отклики или контакты')
            fields['kind'] = kind
        if 'color' in payload:
            color = str(payload.get('color') or '').strip() or None
            if color is not None and color not in rules.SOURCE_COLORS:
                raise HiringError('HIRING_BAD_SOURCE', 'Цвет источника — один из палитры')
            fields['color'] = color
        if partial and 'is_active' in payload:
            fields['is_active'] = bool(payload.get('is_active'))
        return fields

    def add_source(self, ctx, payload):
        """Новый источник руководителя. Имя, которое у источника того же вида уже есть (без учёта регистра), второй
        строкой не заводится: выключенный такой — включается заново с новым цветом."""
        if not access.can_manage(ctx):
            raise _forbidden('Справочники ведёт руководитель рекрутинга')
        fields = self._source_fields(payload or {}, partial=False)
        now = self._now()
        with self._cursor() as cursor:
            cursor.execute("LOCK TABLE hiring_sources IN SHARE ROW EXCLUSIVE MODE")
            same = queries.source_by_name(cursor, fields['name'], fields['kind'])
            if same and same['is_active']:
                raise HiringError('HIRING_DUPLICATE', 'Источник «%s» уже есть' % same['name'], 409)
            if same:
                queries.update_source(cursor, same['id'], name=fields['name'], is_active=True,
                                      **({'color': fields['color']} if 'color' in fields else {}))
            else:
                # Код — для вебхука площадок (source в теле запроса партнёра); свой источник руководителя — без кода.
                code = str((payload or {}).get('code') or '').strip().lower()[:40] or 'custom_%s' % uuid.uuid4().hex[:12]
                if queries.source_by_code(cursor, code):
                    raise HiringError('HIRING_DUPLICATE', 'Источник с таким кодом уже есть', 409)
                queries.create_source(cursor, code, fields['name'], fields['kind'], now, fields.get('color'))
            return queries.list_sources(cursor)

    def edit_source(self, ctx, source_id, payload):
        """Имя, цвет или «выключен». Карточки видят новое имя и цвет сразу — источник у них ссылкой."""
        if not access.can_manage(ctx):
            raise _forbidden('Справочники ведёт руководитель рекрутинга')
        fields = self._source_fields(payload or {}, partial=True)
        with self._cursor() as cursor:
            cursor.execute("LOCK TABLE hiring_sources IN SHARE ROW EXCLUSIVE MODE")
            source = queries.get_source(cursor, source_id)
            if not source:
                raise HiringError('HIRING_NOT_FOUND', 'Источник не найден', 404)
            if 'name' in fields:
                same = queries.source_by_name(cursor, fields['name'], source['kind'], exclude_id=source_id)
                if same:
                    raise HiringError('HIRING_DUPLICATE', 'Источник «%s» уже есть' % same['name'], 409)
            if fields:
                queries.update_source(cursor, source_id, **fields)
            return queries.list_sources(cursor)

    # ─── Группы на обучение (владелец 18.11.2026) ───────────────────────────
    # Группу заводит тренер — сам, без руководителя — и задаёт её вместимость; руководитель рекрутинга
    # ведёт любые группы. Рекрутер, приглашая на обучение, видит свободные места, а в полную группу
    # пригласить нельзя: место считает move() под блокировкой строки группы.

    @staticmethod
    def _groups_of(cursor, ctx):
        """Группы, которые человек ведёт: руководитель — все, тренер — свои, остальным — действующие."""
        if access.is_lead(ctx):
            return queries.list_groups(cursor)
        if access.is_trainer(ctx):
            return queries.list_groups(cursor, trainer_id=ctx['user_id'])
        return queries.list_groups(cursor, active_only=True)

    def groups(self, ctx, pick=False):
        """pick — выбор группы при приглашении на обучение: действующие группы всех тренеров с местами,
        сначала предстоящие (ближний старт сверху), уже начавшиеся — ниже."""
        with self._cursor() as cursor:
            if pick:
                return queries.list_groups(cursor, active_only=True, soonest_first=True, today=self._now().date())
            return self._groups_of(cursor, ctx)

    def _group_fields(self, cursor, ctx, payload, *, creating):
        """Поля группы из формы. Тренера и отдел группы назначает руководитель рекрутинга; тренер заводит
        группы на себя и правит название, старт, вместимость, вакансию и «завершена»."""
        lead = access.is_lead(ctx)
        fields = {}
        if creating or 'name' in payload:
            fields['name'] = _text(payload.get('name'), 120)
            if not fields['name']:
                raise HiringError('HIRING_BAD_GROUP', 'Название группы пустое')
        if creating or 'start_date' in payload:
            fields['start_date'] = _parse_date(payload.get('start_date'), 'дату старта')
            if not fields['start_date']:
                raise HiringError('HIRING_BAD_GROUP', 'Укажите дату старта группы')
        if creating or 'capacity' in payload:
            fields['capacity'] = rules.parse_capacity(payload.get('capacity'))
            if fields['capacity'] is None:
                raise HiringError('HIRING_BAD_CAPACITY', 'Вместимость — целое число от 1 до %d' % rules.GROUP_CAPACITY_MAX)
        if 'vacancy_id' in payload or (creating and not lead):
            fields['vacancy_id'] = _int(payload.get('vacancy_id'))
            if fields['vacancy_id'] and not queries.get_vacancy(cursor, fields['vacancy_id']):
                raise HiringError('HIRING_NOT_FOUND', 'Вакансия не найдена', 404)
            # Группе тренера вакансия обязательна: отдел группе назначает только руководитель, и кандидат без своей
            # вакансии берёт её от группы, а с ней — отдел, чей руководитель подтверждает «Отработал 7 дней».
            # Без неё подтвердить найм было бы некому (разбор 18.11.2026).
            if not fields['vacancy_id'] and not lead:
                raise HiringError('HIRING_NEED_VACANCY', 'Укажите вакансию группы — по ней кандидат попадёт в отдел найма')
        if not creating and 'status' in payload:
            if payload.get('status') not in ('active', 'done'):
                raise HiringError('HIRING_BAD_GROUP', 'Группа бывает только «идёт» или «завершена»')
            fields['status'] = payload['status']
        for key in ('trainer_id', 'department_id'):
            if key not in payload:
                continue
            value = _int(payload.get(key))
            if not lead:
                if key == 'trainer_id' and value == int(ctx['user_id']):
                    continue
                raise _forbidden('Тренера и отдел группы назначает руководитель рекрутинга')
            if key == 'trainer_id' and value and not queries.user_name(cursor, value):
                raise HiringError('HIRING_NOT_FOUND', 'Тренер не найден', 404)
            fields[key] = value
        return fields

    def create_group(self, ctx, payload):
        """Новая группа: название, старт, вместимость (обязательно), вакансия (тренеру обязательна). Тренер заводит
        группу на себя."""
        if not access.can_create_groups(ctx):
            raise _forbidden('Группы заводят тренер и руководитель рекрутинга')
        now = self._now()
        with self._cursor() as cursor:
            fields = self._group_fields(cursor, ctx, payload or {}, creating=True)
            trainer_id = fields.get('trainer_id') if access.is_lead(ctx) else int(ctx['user_id'])
            queries.create_group(cursor, fields['name'], fields['start_date'], trainer_id, fields.get('department_id'),
                                 fields.get('vacancy_id'), ctx['user_id'], now, capacity=fields['capacity'])
            return self._groups_of(cursor, ctx)

    def edit_group(self, ctx, group_id, payload):
        """Правка группы — руководитель любую, тренер свою. Вместимость не меньше занятых мест: уменьшить можно
        до числа кандидатов в группе."""
        now = self._now()
        with self._cursor() as cursor:
            group = queries.get_group(cursor, group_id, lock=True)
            if not group:
                raise HiringError('HIRING_NOT_FOUND', 'Группа не найдена', 404)
            if not access.can_edit_group(ctx, group):
                raise _forbidden('Эту группу ведёт другой тренер')
            fields = self._group_fields(cursor, ctx, payload or {}, creating=False)
            if 'capacity' in fields:
                taken = queries.group_seats_taken(cursor, group['id'])
                if fields['capacity'] < taken:
                    raise HiringError('HIRING_CAPACITY_BELOW_TAKEN',
                                      'В группе уже %d — вместимость не может быть меньше' % taken, 409)
            if fields:
                queries.update_group(cursor, group['id'], now, **fields)
            return self._groups_of(cursor, ctx)

    def move(self, ctx, card_id, target, payload):
        """Переход по этапам (ТЗ 4.2) со всеми следствиями."""
        payload = payload or {}
        if target not in rules.STAGE_CODES:
            raise HiringError('HIRING_BAD_STAGE', 'Такого этапа нет')
        if target == rules.HIRED:
            # «Отработал 7 дней» ставит сама система на седьмой смене из статусов работы (SZOV-10, 23.11.2026: «проверку
            # смен надо сделать автоматической») — в карточке подтверждать нечего.
            raise HiringError('HIRING_HIRED_AUTO', '«Отработал 7 дней» ставится сам — на седьмой смене по статусам работы', 409)
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
            if not has_touch and rules.TRANSITIONS.get((card['stage'], target), {}).get('needs_touch'):
                has_touch = queries.wa_our_message(cursor, [card.get('phone'), card.get('whatsapp')],
                                                   self._wa_contact_since(cursor, card), whatsapp.AUTO_REPLY_GAP)
            try:
                rule = rules.check_transition(card, target, kinds, has_touch=has_touch, payload=payload)
            except rules.TransitionError as exc:
                raise HiringError(exc.code, exc.message, 403 if exc.code == 'HIRING_FORBIDDEN' else 409)
            fields = {'stage': target, 'substage': None, 'updated_at': now}
            if target == rules.INVITED_OFFICE:
                fields['office_visit_at'] = _parse_moment(payload.get('office_visit_at'), 'дату визита')
                fields['substage'] = 'expected'
            elif target == rules.INVITED_TRAINING:
                # Строка группы — под блокировкой: два рекрутера, приглашающих на последнее место, идут по
                # очереди, и второй видит место уже занятым (владелец 18.11.2026: полная группа не принимает).
                group = queries.get_group(cursor, _int(payload.get('group_id')) or 0, lock=True)
                if not group or group['status'] != 'active':
                    raise HiringError('HIRING_BAD_GROUP', 'Такой группы на обучение нет')
                if group.get('capacity') is not None:
                    taken = queries.group_seats_taken(cursor, group['id'], exclude_card_id=card_id)
                    if taken >= int(group['capacity']):
                        raise HiringError('HIRING_GROUP_FULL', 'В группе «%s» мест нет — занято %d из %d. Выберите другую группу' % (
                            group['name'], taken, int(group['capacity'])), 409)
                fields['group_id'] = group['id']
                if not card.get('vacancy_id') and group.get('vacancy_id'):
                    fields['vacancy_id'] = group['vacancy_id']
            elif target == rules.ON_TRAINING:
                fields['training_started_at'] = _parse_date(payload.get('training_started_at')) or card.get('group_start_date') or now.date()
            elif target == rules.ON_LINE:
                fields['line_started_at'] = _parse_date(payload.get('line_started_at'), 'дату выхода на линию')
            elif target in (rules.REFUSED, rules.DROPPED):
                fields.update(self._closing_fields(cursor, card, target, payload, now))
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
                if card['stage'] not in rules.RELEASABLE_STAGES:
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
    # День, где отработано не меньше SHIFT_MIN_HOURS, — смена. Седьмая сама ставит «Отработал 7 дней» (рекрутеру),
    # десятая — «Отработал 10 дней» (тренеру): без подтверждения в карточке (SZOV-10, 23.11.2026).

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
        """Смены одной карточки из daily_hours её сотрудника и зачёт категорий по ним. Возвращает (сотрудник найден,
        смен, засчитанные категории)."""
        employee_id = self._employee_for_card(cursor, card)
        before = int(card.get('shifts_done') or 0)
        if not employee_id or not queries.daily_hours_table_exists(cursor):
            return bool(employee_id), before, []
        since = card.get('line_started_at') or (card['taken_at'].date() if card.get('taken_at') else now.date())
        days = queries.worked_days(cursor, employee_id, since, now.date(), rules.SHIFT_MIN_HOURS)
        total = queries.replace_auto_shifts(cursor, card['id'], days, now)
        if total != before:
            queries.update_card(cursor, card['id'], shifts_done=total, updated_at=now)
        return True, total, self._credit_shifts(cursor, card, total, now)

    def _credit_shifts(self, cursor, card, total, now):
        """«Отработал 7 дней» и «Отработал 10 дней» — одной функцией и одной транзакцией по фактическому числу смен
        (SZOV-10: «одна функция, которая выполняется как единый процесс»). Седьмая смена закрывает найм и засчитывает
        мотивацию закреплённому рекрутеру, десятая отмечает категорию тренера группы. Восемь-девять смен — рекрутеру 7
        засчитаны, тренеру 10 нет; не дошёл до десяти — ничего не отклоняется. Возвращает засчитанные категории."""
        credits = rules.shift_credits(card['stage'], card.get('hired10_at'), total)
        card_id = card['id']
        if rules.HIRED in credits:
            queries.update_card(cursor, card_id, stage=rules.HIRED, substage=None, hired_at=now, hired_by=None,
                                is_closed=True, closed_at=now, updated_at=now)
            queries.add_stage(cursor, card_id, rules.HIRED, None, None, 'Система', now)
            queries.upsert_motivation(cursor, card_id, card.get('recruiter_id'), rules.MOTIVATION_COUNTED, now)
            queries.set_motivation(cursor, card_id, now, confirmed_at=now, confirmed_by=None, status=rules.MOTIVATION_COUNTED)
            notices.motivation_counted(cursor, card, card.get('recruiter_id'), now)
        if rules.HIRED_10 in credits:
            queries.update_card(cursor, card_id, hired10_at=now, updated_at=now)
            queries.add_stage(cursor, card_id, rules.HIRED_10, None, None, 'Система', now)
            notices.trainer_counted(cursor, card, now)
        if credits:
            log.info('hiring: карточка %s, смен %s — засчитано: %s', card_id, total, ', '.join(credits))
        return credits

    def sync_shifts(self, now=None):
        """Джоба раз в полчаса: смены карточек на линии и «Отработал 7 дней» до десятой — из статусов работы, с зачётом
        категорий. Сводка: карточек, с учёткой, смен, засчитано «7 дней» и «10 дней»."""
        now = now or self._now()
        summary = {'cards': 0, 'matched': 0, 'shifts': 0, 'hired': 0, 'hired_10': 0}
        with self._cursor() as cursor:
            cards = queries.shift_cards(cursor)
        for card in cards:
            with self._cursor() as cursor:
                fresh = queries.get_card(cursor, card['id'], lock=True)
                if not fresh or not self._counts_shifts(fresh):
                    continue
                matched, total, credits = self._sync_card_shifts(cursor, fresh, now)
            summary['cards'] += 1
            summary['matched'] += int(matched)
            summary['shifts'] += total
            summary['hired'] += int(rules.HIRED in credits)
            summary['hired_10'] += int(rules.HIRED_10 in credits)
        return summary

    @staticmethod
    def _counts_shifts(card):
        """Карточке ещё считаются смены: на линии — до «7 дней», в «7 днях» — до «10 дней»."""
        if card['stage'] == rules.ON_LINE:
            return not card['is_closed']
        return card['stage'] == rules.HIRED and not card.get('hired10_at')

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
        if card['stage'] == rules.NO_RESPONSE and not card['is_closed'] and calls.talked(call):
            self._back_to_work(cursor, card, 'dial', 'Дозвонились', now)
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
        «Подключить». Тревога руководителям — на входе в «Остановлен» и на возврате после него.
        Подключённому сервису, который это умеет (features), — сообщения без файла: попросить телефон
        прислать их заново (wa_media, файлы старых сообщений)."""
        payload = payload if isinstance(payload, dict) else {}
        state = str(payload.get('state') or '').strip()
        if state not in whatsapp.BRIDGE_STATES:
            raise HiringError('HIRING_WA_BAD_STATE', 'Неизвестное состояние сервиса')
        detail = whatsapp.clean_detail(payload.get('detail'))
        ack = str(payload.get('ack') or '').strip() or None
        features = payload.get('features') if isinstance(payload.get('features'), list) else []
        # Что умеет эта версия сервиса — окно «Чаты» принимает ответы, только если шлюз умеет их отправлять.
        detail['features'] = sorted({str(item) for item in features if str(item) in whatsapp.BRIDGE_FEATURES})
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
        send, outbox_busy = [], False
        try:
            with self._cursor() as cursor:
                send, outbox_busy = self._wa_send_batch(cursor, now, state == whatsapp.CONNECTED and 'send' in features)
        except Exception:  # noqa: BLE001 — состояние и команды сервиса важнее очереди отправки
            log.exception('WhatsApp найма: очередь отправки не выдана шлюзу')
        resend = []
        if state == whatsapp.CONNECTED and 'resend' in features:
            try:
                with self._cursor() as cursor:
                    resend = self._wa_resend_batch(cursor, now)
            except Exception:  # noqa: BLE001 — состояние, команды и пульс сервиса важнее докачки файлов
                log.exception('WhatsApp найма: файлы старых сообщений не выданы шлюзу')
                resend = []
        for chat_id in chats.values():
            self._telegram(chat_id, notices.whatsapp_alert_html(alert[1], alert[2], self._base_url), None)
        busy = command or state in (whatsapp.PAIRING, whatsapp.CONNECTING) or outbox_busy
        reply = {'command': {'id': command['id'], 'action': command['action']} if command else None,
                 'next_poll_sec': 2 if busy else (3 if self._wa_ui_active() else 10)}
        if resend:
            reply['resend'] = resend
        if send:
            reply['send'] = send
        return reply

    def _wa_resend_batch(self, cursor, now):
        """Файлы старых сообщений: что попросить у телефона заново в этом опросе. Попытка засчитывается
        при выдаче: потерянный ответ опроса — та же пауза до следующей просьбы."""
        retry_before = (now - wa_media.RESEND_RETRY).isoformat(timespec='seconds')
        queries.wa_media_resend_exhausted(cursor, retry_before, wa_media.RESEND_ATTEMPTS, now)
        rows = queries.wa_media_resend_due(cursor, wa_media.MAX_BYTES, retry_before, now - wa_media.FETCH_STALE,
                                           wa_media.RESEND_ATTEMPTS, wa_media.RESEND_BATCH)
        queries.wa_media_resend_mark(cursor, [row['id'] for row in rows], now.isoformat(timespec='seconds'))
        items = []
        for row in rows:
            item = {'id': row['wa_id'], 'chat': wa_media.resend_chat(row['jid'], row['phone'], row['lid'], row['attempts']),
                    'from_me': bool(row['from_me'])}
            # Сначала — просьба прислать само сообщение (проверено на проде); не помогла — история чата.
            anchor = wa_media.resend_anchor(row['anchor_id'], row['anchor_from_me'], row['anchor_at'], rules.ALMATY) \
                if row['attempts'] >= wa_media.RESEND_HISTORY_FROM else None
            if anchor:
                item['anchor'] = anchor
            items.append(item)
        return items

    def wa_events(self, payload):
        """Сообщения личных чатов номера от сервиса. Первое входящее чата — отклик в пуле; дальше
        чат пишется в переписку карточки, пока она открыта. Повтор пачки безопасен."""
        events = payload.get('events') if isinstance(payload, dict) else None
        if not isinstance(events, list) or not events or len(events) > whatsapp.EVENTS_MAX:
            raise HiringError('HIRING_WA_BAD_EVENTS', 'Ожидается список событий (1–%d)' % whatsapp.EVENTS_MAX)
        messages = [message for message in (whatsapp.parse_message(item) for item in events) if message]
        failures = [failure for failure in (whatsapp.parse_media_failure(item) for item in events) if failure]
        sent = [event for event in (wa_chat.parse_sent(item) for item in events) if event]
        not_sent = [event for event in (wa_chat.parse_send_failed(item) for item in events) if event]
        receipts = [event for event in (wa_chat.parse_receipt(item) for item in events) if event]
        now = self._now()
        outcomes = {}
        trash = []

        def count(outcome):
            outcomes[outcome] = outcomes.get(outcome, 0) + 1

        with self._cursor() as cursor:
            source = queries.source_by_code(cursor, whatsapp.SOURCE_CODE)
            target = queries.source_by_code(cursor, rules.SOURCE_TARGET)
            for message in messages:
                count(self._wa_message(cursor, message, source, now, trash, target=target))
            for failure in failures:
                if queries.wa_media_failed(cursor, failure['id'], failure['reason'], now):
                    count('media_failed')
            # Отправленное из портала — после сообщений пачки, отметки доставки — после отправленного: отметка может
            # прийти в той же пачке, что и сам ответ.
            for event in sent:
                count(self._wa_sent(cursor, event, now))
            for event in not_sent:
                if self._wa_send_failed(cursor, event, now):
                    count('send_failed')
            for event in receipts:
                if queries.wa_apply_receipt(cursor, event['ids'], event['status']):
                    count('receipt')
            queries.wa_add_received(cursor, outcomes.get('created', 0), now)
        self._wa_drop_files(trash)
        return {'accepted': len(events), 'outcomes': outcomes}

    def _wa_message(self, cursor, message, source, now, trash=None, target=None):
        """Сообщение номера найма. source — источник «WhatsApp»: выключен руководителем — переписка пишется, а карточки
        не заводятся. target — источник «Таргет»: отклик по рекламе приходит с ним (владелец 09.11.2026 «те что пришли
        с таргета — Таргет», 20.11.2026 каналы слиты с источниками); выключен «Таргет» — отклик остаётся «WhatsApp»."""
        key = message['key']
        if message['revoke_of']:
            count, objects = queries.wa_revoke_message(cursor, key, message['revoke_of'], now)
            if trash is not None:
                trash.extend(objects)
            if count:
                queries.wa_recount_outbound(cursor, key, whatsapp.AUTO_REPLY_GAP)
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
            # «Ждут ответа» гасит наш ответ с телефона — не автоответ WhatsApp Business (окно «Чаты», wa_chat.py).
            at = message['at'] or now
            last_in = queries.wa_last_inbound(cursor, key, at)
            if last_in is None or at - last_in >= whatsapp.AUTO_REPLY_GAP:
                queries.wa_mark_outbound(cursor, key, at)
            return self._wa_phone_reply(cursor, chat, message, now) if chat.get('card_id') else 'stored'
        queries.wa_recount_waiting(cursor, key)
        if not message['phone'] or not source or not source['is_active']:
            self._wa_candidate_answered(cursor, chat.get('card_id'), message, now)
            return 'stored'
        if chat.get('card_id'):
            card = queries.get_card(cursor, chat['card_id'])
            if card and not card['is_closed']:
                self._wa_candidate_answered(cursor, card['id'], message, now)
                return 'stored'
        # Пишет кандидат, чью карточку недавно закрыли (владелец 23.11.2026: «чтобы не было повтора, если даже будет —
        # только если кандидат обратно напишет через 72 ч»): раньше 72 часов после закрытия новой карточки «повторно» нет,
        # сообщение остаётся в чате закрытой — там его видно («Ждут ответа») и оттуда отвечают. Открытая карточка номера
        # (пришла с другой площадки) — как раньше: сообщение — повторный отклик в неё (_intake_one).
        if not queries.open_card_by_phone(cursor, message['phone']):
            closed = self._wa_recently_closed(cursor, chat, message['phone'], message['at'] or now)
            if closed:
                if chat.get('card_id') != closed['id']:
                    queries.wa_set_chat_card(cursor, key, closed['id'], now)
                return 'closed_recent'
        intake_source = target if target and target['is_active'] and whatsapp.from_ads(message['ad']) else source
        outcome, card_id = self._intake_one(
            cursor, phone=message['phone'], kind=rules.KIND_RESPONSE, source=intake_source, now=now, by_name='WhatsApp',
            full_name=chat.get('push_name') or '', platform_ref='wa:%s' % message['id'],
            campaign=whatsapp.campaign_of(message['ad']), received_at=message['at'] or now,
            comment=whatsapp.first_message_comment(message))
        if card_id:
            queries.wa_set_chat_card(cursor, key, card_id, now)
        if outcome == 'repeat':
            self._wa_candidate_answered(cursor, card_id, message, now)
        return outcome

    @staticmethod
    def _wa_recently_closed(cursor, chat, phone, at):
        """Закрытая карточка номера, после закрытия которой не прошло 72 часа к моменту сообщения, — иначе None.
        Последняя закрытая: карточка чата или последняя закрытая по номеру — что закрыто позже."""
        cards = [queries.get_card(cursor, chat['card_id']) if chat.get('card_id') else None,
                 queries.last_closed_card_by_phone(cursor, phone)]
        closed = [card for card in cards if card and card['is_closed'] and card.get('closed_at')]
        if not closed:
            return None
        card = max(closed, key=lambda item: item['closed_at'])
        return None if rules.wa_repeat_allowed(card['closed_at'], at) else card

    def _wa_candidate_answered(self, cursor, card_id, message, now):
        """Кандидат написал на номер найма — «Недозвон / не ответил» к нему больше не относится (владелец
        09.11.2026: статус — «не ответил ни на звонок, ни на сообщение в WhatsApp»): подстатус «Переписка в
        WhatsApp», серия недозвонов — с нуля. Сообщение старше нашей последней попытки связаться (шлюз дослал
        его после простоя) статус не снимает: на ту попытку кандидат ещё не ответил."""
        if not card_id:
            return
        card = queries.get_card(cursor, card_id, lock=True)
        if card and not card['is_closed'] and card['stage'] == rules.NO_RESPONSE:
            # Молчавший кандидат написал — снова «В работе» у того же рекрутера (владелец 20.11.2026). Сообщение
            # из прошлого круга (до взятия) — не ответ на наши попытки.
            if not card.get('taken_at') or (message['at'] or now) >= card['taken_at']:
                self._back_to_work(cursor, card, 'whatsapp', 'Кандидат написал в WhatsApp', now)
            return
        if not card or card['is_closed'] or card['stage'] != rules.IN_WORK or card.get('substage') != 'no_answer':
            return
        at = message['at'] or now
        if card.get('last_touch_at') and at < card['last_touch_at']:
            return
        queries.update_card(cursor, card['id'], substage='whatsapp', no_answer_streak=0, updated_at=now)

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
        if queries.wa_whatsapp_touched(cursor, card['id'], day_start, day_start + timedelta(days=1)):
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

    # ─── Окно «Чаты»: переписка из портала (владелец 19.11.2026, wa_chat.py) ──
    # Рекрутер отвечает кандидату прямо в разделе: сообщение встаёт в очередь (hiring_wa_outbox), сервис шлюза
    # забирает его в ответе на опрос и отправляет связанным устройством номера найма, а портал узнаёт итог
    # событием (sent / send_failed) и отметки доставки (receipt). Писать можно только в свою карточку: отклик из
    # пула сначала берут в работу. Чат, куда кандидат ещё не писал, открывает кнопка «WhatsApp» карточки
    # (wa_open_card_chat, владелец 20.11.2026) — первое сообщение тогда наше.

    WA_UI_ACTIVE_SEC = 90
    WA_CHAT_PAGE = 50
    WA_THREAD_PAGE = 60

    def _wa_ui_touch(self):
        self._wa_ui_seen = time.monotonic()

    def wa_live_check(self, ctx):
        """Живой канал окна «Чаты» (wa_live.py) — тем же, кому открыт список чатов."""
        self._wa_chat_reader(ctx)
        self._wa_ui_touch()

    def wa_live_beat(self):
        """Открытое окно держит шлюз в частом опросе — как его запросы списка и ленты."""
        self._wa_ui_touch()

    def _wa_ui_active(self):
        return time.monotonic() - self._wa_ui_seen < self.WA_UI_ACTIVE_SEC

    @staticmethod
    def _wa_connection(cursor, now):
        view = whatsapp.view(queries.get_integration(cursor, whatsapp.INTEGRATION), now)
        return {'state': view['state'], 'label': view['label'], 'tone': view['tone'],
                'connected': view['state'] == whatsapp.CONNECTED, 'send_ready': view['send_ready'],
                'reachout_lock': view['reachout_lock']}

    @staticmethod
    def _wa_chat_reader(ctx):
        caps = access.capabilities(ctx)
        if not (caps['lead'] or caps['recruiter'] or caps['read_all'] or caps['trainer_group_ids'] or caps['headed_ids']):
            raise _forbidden('Переписка с кандидатами открыта рекрутерам и руководителю рекрутинга')
        return caps

    def _wa_chat_access(self, cursor, ctx, key, lock=False):
        """Чат и его карточка, если человеку можно их видеть. Чат без карточки — только руководителю. Адрес — ключ
        чата или 'card:<id>' (у того, кому номер не виден, ключ-номер наружу не уходит)."""
        card_id = wa_chat.card_ref(key)
        if card_id:
            chat = queries.wa_chat_by_card(cursor, card_id, lock=lock)
        else:
            chat = queries.wa_get_chat(cursor, str(key or '')[:48], lock=lock)
        if not chat:
            raise HiringError('HIRING_NOT_FOUND', 'Чат не найден', 404)
        card = queries.get_card(cursor, chat['card_id'], lock=lock) if chat.get('card_id') else None
        if card is None:
            if not access.is_lead(ctx):
                raise HiringError('HIRING_NOT_FOUND', 'Чат не найден', 404)
        elif not self.visible(ctx, card):
            raise HiringError('HIRING_NOT_FOUND', 'Чат не найден', 404)
        return chat, card

    def _wa_send_block(self, ctx, card):
        return wa_chat.send_block(card, involved=bool(card) and self.involved(ctx, card),
                                  owner_or_lead=bool(card) and bool(self._is_owner_or_lead(ctx, card)),
                                  can_take=access.can_take(ctx), connected=True)

    def _wa_write_block(self, ctx, chat, card, connection):
        """Почему в чат сейчас нельзя написать (None — можно): правила карточки, старый сервис на шлюзе, запрет
        WhatsApp писать первым незнакомым — в чат, куда кандидат ещё не писал (открыт из карточки)."""
        block = self._wa_send_block(ctx, card)
        if block is None and not connection['send_ready']:
            block = 'bridge'
        if block is None and not chat.get('last_inbound_at') and connection.get('reachout_lock'):
            block = 'first_lock'
        return block

    @staticmethod
    def _wa_block_text(block, connection):
        if block == 'first_lock' and connection.get('reachout_lock'):
            return connection['reachout_lock'] + ' — кандидат вам ещё не писал, написать первым пока нельзя'
        return wa_chat.SEND_BLOCK_TEXTS.get(block)

    @staticmethod
    def _wa_chat_row(ctx, row):
        hidden = bool(row.get('card_id')) and not access.sees_phone(ctx, row)
        return {
            'key': wa_chat.chat_ref(row, hidden), 'title': wa_chat.chat_title(row, hidden),
            'phone': None if hidden else row.get('phone'),
            'phone_pretty': None if hidden or not row.get('phone') else rules.pretty_phone(row.get('phone')),
            'card_id': row.get('card_id'), 'candidate_no': row.get('candidate_no'), 'stage': row.get('stage'),
            'stage_label': rules.STAGE_LABELS.get(row.get('stage')) if row.get('stage') else None,
            'substage': row.get('substage'), 'recruiter_id': row.get('recruiter_id'),
            'recruiter_name': row.get('recruiter_name'), 'is_closed': bool(row.get('is_closed')),
            'waiting': int(row.get('waiting') or 0), 'waiting_since': row.get('waiting_since'),
            'last_at': row.get('last_at'),
            'last': {'from_me': bool(row.get('last_from_me')), 'kind': row.get('last_kind'),
                     'text': wa_chat.preview(row.get('last_kind'), None if hidden else row.get('last_body'),
                                             bool(row.get('last_deleted'))),
                     'status': row.get('last_status'), 'author': row.get('last_author')},
        }

    def wa_chats(self, ctx, params):
        """Список чатов окна «Чаты»: в пределах видимости карточек, ждущие ответа сверху."""
        caps = self._wa_chat_reader(ctx)
        params = params or {}
        mode = params.get('mode') if params.get('mode') in ('all', 'mine', 'pool') else 'all'
        waiting_only = str(params.get('waiting') or '').strip().lower() in ('1', 'true', 'yes')
        search = str(params.get('q') or '')[:80]
        offset = max(0, _int(params.get('offset')) or 0)
        # Отбор по статусам кандидата (23.11.2026): коды этапов через запятую; незнакомые отбрасываются.
        stages = [code for code in dict.fromkeys(str(params.get('stages') or '').split(',')) if code in rules.STAGE_CODES]
        scope_sql, scope_params = queries.scope_clause(caps, ctx['user_id'])
        remote = access.is_remote(ctx)
        now = self._now()
        with self._cursor() as cursor:
            rows = queries.wa_chat_list(cursor, scope_sql, scope_params, lead=caps['lead'], user_id=ctx['user_id'],
                                        mode=mode, waiting_only=waiting_only, search=search,
                                        limit=self.WA_CHAT_PAGE, offset=offset, phone_own_only=remote, stages=stages)
            totals = queries.wa_chat_totals(cursor, scope_sql, scope_params, lead=caps['lead'], user_id=ctx['user_id'],
                                            mode=mode, search=search, phone_own_only=remote, stages=stages)
            connection = self._wa_connection(cursor, now)
        self._wa_ui_touch()
        return {'items': [self._wa_chat_row(ctx, row) for row in rows], 'totals': totals, 'connection': connection,
                'has_more': len(rows) >= self.WA_CHAT_PAGE, 'offset': offset}

    def wa_chat_summary(self, ctx):
        """Счётчик «ждут ответа» для вкладки раздела — по всем видимым чатам."""
        caps = access.capabilities(ctx)
        if not (caps['lead'] or caps['recruiter'] or caps['read_all'] or caps['trainer_group_ids'] or caps['headed_ids']):
            return {'waiting_chats': 0, 'waiting_messages': 0, 'total': 0}
        scope_sql, scope_params = queries.scope_clause(caps, ctx['user_id'])
        with self._cursor() as cursor:
            return queries.wa_chat_totals(cursor, scope_sql, scope_params, lead=caps['lead'], user_id=ctx['user_id'])

    def wa_open_card_chat(self, ctx, card_id):
        """Кнопка «WhatsApp» карточки — переписка в окне «Чаты» (владелец 20.11.2026: «при нажатии на ватсап … полноценный
        чат, мб просто переход на чаты»). Чат карточки; иначе чат её номера (WhatsApp, потом телефон): ничей или
        завершённой карточки этого номера переходит к открытой — как при новом сообщении кандидата; иначе пустой чат —
        кандидату можно написать первым с номера найма (владелец 20.11.2026). Пустой чат в списке не появляется, пока в
        нём нет сообщений. Возвращает адрес чата для окна."""
        self._wa_chat_reader(ctx)
        now = self._now()
        created = False
        with self._cursor() as cursor:
            card = self._load_visible(cursor, ctx, card_id)
            if not access.sees_phone(ctx, card):
                raise HiringError('HIRING_PHONE_HIDDEN', 'Переписка откроется, когда возьмёте карточку в работу', 403)
            chat = queries.wa_chat_by_card(cursor, card['id'])
            numbers = [number for number in dict.fromkeys((card.get('whatsapp'), card.get('phone'))) if number]
            keys = [key for key in dict.fromkeys(whatsapp.chat_key(number, None) for number in numbers) if key]
            for key in keys if chat is None else ():
                found = queries.wa_get_chat(cursor, key, lock=True)
                if found is None:
                    continue
                owner = queries.get_card(cursor, found['card_id']) if found.get('card_id') else None
                if owner is None or (owner['is_closed'] and not card['is_closed']):
                    queries.wa_set_chat_card(cursor, key, card['id'], now)
                    chat = dict(found, card_id=card['id'])
                    break
                if owner['is_closed'] and self.visible(ctx, owner):
                    chat = found
                    break
                raise HiringError('HIRING_WA_OTHER_CARD', 'Переписка с этим номером — в карточке кандидата №%s'
                                  % (owner.get('candidate_no') or owner['id']), 409)
            if chat is None:
                if not keys:
                    raise HiringError('HIRING_WA_NO_PHONE', 'У карточки нет номера для WhatsApp', 409)
                key = keys[0]
                digits = ''.join(ch for ch in key if ch.isdigit())
                chat = queries.wa_create_chat(cursor, key, digits + '@s.whatsapp.net', key, card['id'], now)
                if chat is None:
                    # Второе нажатие (или первое сообщение кандидата) в ту же секунду уже завело чат — открыть его.
                    chat = queries.wa_get_chat(cursor, key, lock=True)
                    if not chat.get('card_id'):
                        queries.wa_set_chat_card(cursor, key, card['id'], now)
                        chat = dict(chat, card_id=card['id'])
                created = True
        self._wa_ui_touch()
        return {'key': wa_chat.chat_ref(chat, False), 'created': created}

    def _wa_message_view(self, message, sign):
        media = None
        if message['kind'] != 'text' and not message.get('deleted_at'):
            media = wa_media.public_view(message['kind'], message.get('media'), sign)
        return {
            'id': message['id'], 'wa_id': message['wa_id'], 'from_me': bool(message['from_me']),
            'at': message['sent_at'], 'kind': message['kind'],
            'text': None if message.get('deleted_at') else message.get('body'), 'media': media,
            'deleted': bool(message.get('deleted_at')), 'edited': bool(message.get('edited_at')),
            'author': message.get('author_name'), 'status': message.get('status') if message['from_me'] else None,
            'quote_id': message.get('quote_wa_id'),
            'ad_label': whatsapp.campaign_of(message['ad']) if message.get('ad') else None,
        }

    @staticmethod
    def _wa_pending_view(row, ctx=None):
        media = row.get('media') or {}
        mine = bool(ctx) and (int(row.get('author_id') or 0) == int(ctx['user_id']) or access.is_lead(ctx))
        return {'outbox_id': row['id'], 'client_id': row.get('client_id'), 'status': row['status'], 'mine': mine,
                'kind': row['kind'], 'text': row.get('body'), 'quote_id': row.get('quote_wa_id'),
                'file_name': media.get('file_name'), 'size': media.get('size'), 'author': row.get('author_name'),
                'author_id': row.get('author_id'), 'error': row.get('error'), 'at': row.get('created_at')}

    def wa_thread(self, ctx, key, params):
        """Лента открытого чата: сообщения (страницами), ждущие отправки из портала, можно ли писать и почему нет."""
        self._wa_chat_reader(ctx)
        params = params or {}
        before_id, after_id = _int(params.get('before_id')), _int(params.get('after_id'))
        now = self._now()
        hidden = False
        with self._cursor() as cursor:
            chat, card = self._wa_chat_access(cursor, ctx, key)
            if card is not None and not access.sees_phone(ctx, card):
                # Удалённому рекрутеру переписка отклика из пула — после «Взять в работу», как и телефон (ТЗ 2).
                hidden, rows, extra, pending = True, [], [], []
            else:
                rows = queries.wa_thread(cursor, chat['key'], before_id=before_id, after_id=after_id,
                                         limit=self.WA_THREAD_PAGE)
                pending = queries.wa_outbox_pending(cursor, chat['key']) if not before_id else []
                known = {row['wa_id'] for row in rows}
                missing = [row['quote_wa_id'] for row in rows if row.get('quote_wa_id') and row['quote_wa_id'] not in known]
                missing += [row['quote_wa_id'] for row in pending if row.get('quote_wa_id') and row['quote_wa_id'] not in known]
                extra = queries.wa_messages_by_wa_ids(cursor, chat['key'], missing)
            connection = self._wa_connection(cursor, now)
        has_more = False
        if not after_id and len(rows) > self.WA_THREAD_PAGE:
            rows, has_more = rows[1:], True
        sign = self._storage.sign if self._storage is not None else None
        quotes = {row['wa_id']: {'from_me': bool(row['from_me']), 'author': row.get('author_name'),
                                 'text': 'Сообщение удалено' if row.get('deleted') else wa_chat.preview(row['kind'], row.get('body'))}
                  for row in extra}
        for row in rows:
            quotes.setdefault(row['wa_id'], {'from_me': bool(row['from_me']), 'author': row.get('author_name'),
                                             'text': 'Сообщение удалено' if row.get('deleted_at')
                                             else wa_chat.preview(row['kind'], row.get('body'))})
        block = self._wa_write_block(ctx, chat, card, connection)
        if hidden:
            block = 'take' if access.can_take(ctx) else 'read_only'
        self._wa_ui_touch()
        used = {row.get('quote_wa_id') for row in rows} | {row.get('quote_wa_id') for row in pending}
        titled = dict(chat, full_name=(card or {}).get('full_name'), candidate_no=(card or {}).get('candidate_no'))
        waiting = int(chat.get('waiting') or 0)
        return {
            'chat': {'key': wa_chat.chat_ref(titled, hidden), 'title': wa_chat.chat_title(titled, hidden),
                     'push_name': None if hidden else chat.get('push_name'), 'waiting': waiting,
                     'last_inbound_at': chat.get('last_inbound_at')},
            'card': card, 'hidden': hidden,
            'messages': [self._wa_message_view(row, sign) for row in rows],
            'pending': [self._wa_pending_view(row, ctx) for row in pending],
            'quotes': {wa_id: value for wa_id, value in quotes.items() if wa_id in used},
            'has_more': has_more, 'block': block, 'block_text': self._wa_block_text(block, connection),
            # Кандидат в этот чат ещё не писал (открыт из карточки): первое сообщение — наше.
            'first_contact': not hidden and not chat.get('last_inbound_at'),
            'can_handle': wa_chat.can_handle(block, lead=access.is_lead(ctx), hidden=hidden, waiting=waiting),
            'connection': connection,
        }

    def wa_send(self, ctx, key, payload, upload=None):
        """Ответ кандидату из окна «Чаты» — в очередь отправки. upload — (байты, тип, имя файла) или None.
        Файл — в бакет до транзакции (долгая загрузка не держит соединение с базой)."""
        self._wa_chat_reader(ctx)
        payload = payload or {}
        text = wa_chat.clean_text(payload.get('text'))
        if len(text) > wa_chat.MAX_TEXT:
            raise HiringError('HIRING_WA_TOO_LONG', 'Сообщение длиннее %d знаков' % wa_chat.MAX_TEXT)
        client_id = wa_chat.valid_client_id(payload.get('client_id'))
        quote_id = whatsapp.clean_message_id(payload.get('quote_id'))
        if not text and not upload:
            raise HiringError('HIRING_WA_EMPTY', 'Пустое сообщение')
        now = self._now()
        media, kind = None, 'text'
        if upload:
            data, mime, file_name = upload
            if not data:
                raise HiringError('HIRING_WA_EMPTY', 'Пустой файл')
            if len(data) > wa_chat.MAX_FILE:
                raise HiringError('HIRING_WA_FILE_TOO_LARGE', 'Файл больше 16 МБ', 413)
            if self._storage is None or not self._storage.available():
                raise HiringError('HIRING_WA_NO_STORAGE', 'Хранилище файлов не настроено', 503)
            ctype = wa_media.content_type(mime)
            kind = wa_chat.send_kind(ctype)
            if kind == 'audio' and text:
                kind = 'document'      # у аудио в WhatsApp нет подписи — с текстом уходит документом
            name = ' '.join(str(file_name or '').split())[:200] or None
            path = wa_media.object_path(0, ctype, name, now).replace(wa_media.PREFIX + '/', wa_media.PREFIX + '/out/', 1)
            self._storage.upload(path, data, ctype)
            media = {'object': path, 'mime': ctype, 'stored_type': ctype, 'size': len(data), 'stored_size': len(data),
                     'file_name': name}
        try:
            with self._cursor() as cursor:
                chat, card = self._wa_chat_access(cursor, ctx, key, lock=True)
                if card is not None and not access.sees_phone(ctx, card):
                    raise HiringError('HIRING_WA_SEND_BLOCKED', wa_chat.SEND_BLOCK_TEXTS['take'], 409)
                connection = self._wa_connection(cursor, now)
                block = self._wa_write_block(ctx, chat, card, connection)
                if block:
                    raise HiringError('HIRING_WA_SEND_BLOCKED', self._wa_block_text(block, connection),
                                      409 if block in ('take', 'bridge', 'first_lock') else 403)
                if quote_id and not queries.wa_messages_by_wa_ids(cursor, chat['key'], [quote_id]):
                    quote_id = None
                row, fresh = queries.wa_outbox_insert(cursor, {
                    'client_id': client_id, 'chat_key': chat['key'], 'card_id': card['id'], 'author_id': ctx['user_id'],
                    'author_name': ctx.get('name') or '', 'kind': kind, 'body': text or None, 'quote_wa_id': quote_id,
                    'media': media, 'now': now})
        except Exception:
            if media:
                self._wa_drop_files([media['object']])
            raise
        if media and not fresh:
            self._wa_drop_files([media['object']])
        self._wa_ui_touch()
        return {'item': self._wa_pending_view(row, ctx)}

    def _wa_own_outbox(self, cursor, ctx, outbox_id):
        row = queries.wa_outbox_get(cursor, outbox_id, lock=True)
        if not row or (int(row['author_id']) != int(ctx['user_id']) and not access.is_lead(ctx)):
            raise HiringError('HIRING_NOT_FOUND', 'Сообщение не найдено', 404)
        return row

    def wa_outbox_retry(self, ctx, outbox_id):
        """«Повторить» у неотправленного: снова в очередь, если писать в этот чат по-прежнему можно. Отказ, о котором
        сообщил шлюз, — новой строкой (этот номер шлюз закрыл); просрочку и «не подтверждено» — тем же номером: если
        шлюз его всё же держит, второй раз не отправит."""
        now = self._now()
        with self._cursor() as cursor:
            row = self._wa_own_outbox(cursor, ctx, outbox_id)
            if row['status'] != wa_chat.FAILED:
                raise HiringError('HIRING_WA_NOT_FAILED', 'Сообщение не в ошибке', 409)
            chat, card = self._wa_chat_access(cursor, ctx, row['chat_key'], lock=True)
            connection = self._wa_connection(cursor, now)
            block = self._wa_write_block(ctx, chat, card, connection)
            if block:
                raise HiringError('HIRING_WA_SEND_BLOCKED', self._wa_block_text(block, connection), 403)
            if row.get('fail_reason'):
                client_id = row.get('client_id')
                queries.wa_outbox_update(cursor, row['id'], status=wa_chat.DISCARDED, client_id=None, updated_at=now)
                new_id = queries.wa_outbox_clone(cursor, dict(row), now)
                if client_id:
                    queries.wa_outbox_update(cursor, new_id, client_id=client_id)
                row = queries.wa_outbox_get(cursor, new_id)
            else:
                queries.wa_outbox_update(cursor, row['id'], status=wa_chat.QUEUED, attempts=0, handed_at=None, error=None,
                                         created_at=now, updated_at=now)
                row = queries.wa_outbox_get(cursor, row['id'])
        self._wa_ui_touch()
        return {'item': self._wa_pending_view(row, ctx)}

    def wa_outbox_discard(self, ctx, outbox_id):
        """«Убрать» неотправленное. Сообщение, которое шлюз уже взял, не убрать: оно может уйти. Строка остаётся
        (скрытой): если шлюз всё же сообщит, что отправил, сообщение ляжет в переписку."""
        now = self._now()
        with self._cursor() as cursor:
            row = self._wa_own_outbox(cursor, ctx, outbox_id)
            if row['status'] not in (wa_chat.FAILED, wa_chat.QUEUED):
                raise HiringError('HIRING_WA_HANDED', 'Сообщение уже отправляется — убрать его нельзя', 409)
            queries.wa_outbox_update(cursor, row['id'], status=wa_chat.DISCARDED, client_id=None, updated_at=now)
        return {'deleted': True}

    def wa_handled(self, ctx, key):
        """«Ответ не нужен»: чат перестаёт ждать ответа, пока кандидат не напишет снова."""
        self._wa_chat_reader(ctx)
        now = self._now()
        with self._cursor() as cursor:
            chat, card = self._wa_chat_access(cursor, ctx, key, lock=True)
            if card is None and not access.is_lead(ctx):
                raise _forbidden()
            if card is not None and not access.sees_phone(ctx, card):
                raise _forbidden('Отметить чат можно, когда возьмёте отклик в работу')
            if card is not None and self._wa_send_block(ctx, card) not in (None, 'take'):
                raise _forbidden('Отметить чат может закреплённый рекрутер')
            queries.wa_mark_handled(cursor, chat['key'], ctx['user_id'], now)
            chat = queries.wa_get_chat(cursor, chat['key'])
        return {'waiting': int(chat.get('waiting') or 0)}

    def _wa_send_batch(self, cursor, now, can_send):
        """Очередь отправки в ответе на опрос шлюза: просроченное — в «Не отправлено», новое — шлюзу. Возвращает
        (что отдать, есть ли ещё что отправлять — тогда опрос чаще)."""
        queries.wa_outbox_expire(cursor, now - wa_chat.SEND_EXPIRE, now - wa_chat.HANDED_EXPIRE, now,
                                 wa_chat.failure_text('expired'))
        queries.wa_outbox_unconfirmed(cursor, now - wa_chat.HAND_RETRY, wa_chat.HAND_ATTEMPTS, now,
                                      wa_chat.failure_text('unconfirmed'))
        if not can_send:
            return [], False
        rows = queries.wa_outbox_due(cursor, now - wa_chat.HAND_RETRY, wa_chat.HAND_ATTEMPTS, wa_chat.HAND_BATCH)
        queries.wa_outbox_handed(cursor, [row['id'] for row in rows], now)
        return [wa_chat.bridge_item(row, now) for row in rows], bool(rows) or queries.wa_outbox_busy(cursor)

    def _wa_sent(self, cursor, event, now):
        """Шлюз отправил сообщение из портала: в переписку с автором, касание WhatsApp карточке (одно в день), чат
        больше не ждёт ответа."""
        outbox = queries.wa_outbox_get(cursor, event['outbox'], lock=True)
        if not outbox:
            return 'sent_unknown'
        if outbox['status'] == wa_chat.SENT:
            return 'sent_duplicate'
        at = event['at'] or now
        chat = queries.wa_get_chat(cursor, outbox['chat_key'], lock=True)
        _message_id, inserted = queries.wa_insert_outbound(cursor, outbox['chat_key'], event['wa_id'], at, outbox, now)
        queries.wa_outbox_update(cursor, outbox['id'], status=wa_chat.SENT, wa_id=event['wa_id'], sent_at=at, error=None,
                                 updated_at=now)
        if chat and inserted:
            queries.wa_touch_chat(cursor, chat['key'], chat['jid'], chat.get('phone'), chat.get('lid'), None, at, False, now)
        if chat:
            queries.wa_mark_outbound(cursor, chat['key'], at)
        card_id = outbox.get('card_id') or (chat or {}).get('card_id')
        card = queries.get_card(cursor, card_id, lock=True) if card_id else None
        if card and not card['is_closed']:
            start, end = wa_chat.day_bounds(at)
            if not queries.wa_whatsapp_touched(cursor, card['id'], start, end):
                queries.add_touch(cursor, card['id'], rules.TOUCH_WHATSAPP, None,
                                  wa_chat.touch_text(outbox['kind'], outbox.get('body')), outbox['author_id'],
                                  outbox.get('author_name'), at,
                                  meta={'source': wa_chat.PORTAL_TOUCH, 'wa_id': event['wa_id'], 'outbox': outbox['id']})
            fields = {'updated_at': now}
            if not card.get('last_touch_at') or card['last_touch_at'] < at:
                fields['last_touch_at'] = at
            # Как у касания «Написать в WhatsApp»: наше сообщение — переписка, но «не ответил» снимает только ответ
            # кандидата (владелец 09.11.2026).
            if card['stage'] == rules.IN_WORK and not card['is_closed'] and card.get('substage') != 'no_answer':
                fields['substage'] = 'whatsapp'
            queries.update_card(cursor, card['id'], **fields)
        return 'sent'

    @staticmethod
    def _wa_send_failed(cursor, event, now):
        outbox = queries.wa_outbox_get(cursor, event['outbox'], lock=True)
        if not outbox or outbox['status'] in (wa_chat.SENT, wa_chat.DISCARDED):
            return False
        queries.wa_outbox_update(cursor, outbox['id'], status=wa_chat.FAILED, fail_reason=event['reason'],
                                 error=wa_chat.failure_text(event['reason'], event.get('error')), updated_at=now)
        return True

    def wa_outbox_file(self, outbox_id):
        """Файл исходящего сообщения — шлюзу, чтобы отправить его кандидату."""
        if self._storage is None or not self._storage.available():
            raise HiringError('HIRING_WA_NO_STORAGE', 'Хранилище файлов не настроено', 503)
        with self._cursor() as cursor:
            row = queries.wa_outbox_get(cursor, outbox_id)
        media = (row or {}).get('media') or {}
        if not row or not media.get('object'):
            raise HiringError('HIRING_NOT_FOUND', 'Файла нет', 404)
        return self._storage.download(media['object']), media.get('stored_type') or media.get('mime') or 'application/octet-stream'

    # Шаблоны ответов — общие для команды найма.

    def wa_templates_list(self, ctx):
        self._wa_chat_reader(ctx)
        with self._cursor() as cursor:
            return {'items': queries.wa_templates(cursor)}

    @staticmethod
    def _wa_template_editor(ctx):
        if not (access.can_take(ctx) or access.can_manage(ctx)):
            raise _forbidden('Шаблоны ведут рекрутеры и руководитель рекрутинга')

    def wa_template_save(self, ctx, template_id, payload):
        self._wa_template_editor(ctx)
        try:
            clean = wa_chat.clean_template(payload)
        except ValueError as exc:
            raise HiringError('HIRING_WA_TEMPLATE', str(exc))
        now = self._now()
        with self._cursor() as cursor:
            if template_id:
                if not queries.wa_template_update(cursor, template_id, clean['title'], clean['body'], now):
                    raise HiringError('HIRING_NOT_FOUND', 'Шаблон не найден', 404)
            else:
                queries.wa_template_insert(cursor, clean['title'], clean['body'], ctx['user_id'], ctx.get('name') or '', now)
            return {'items': queries.wa_templates(cursor)}

    def wa_template_delete(self, ctx, template_id):
        self._wa_template_editor(ctx)
        now = self._now()
        with self._cursor() as cursor:
            if not queries.wa_template_delete(cursor, template_id, now):
                raise HiringError('HIRING_NOT_FOUND', 'Шаблон не найден', 404)
            return {'items': queries.wa_templates(cursor)}

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
        выходил в WhatsApp — тревога раз в сутки (через 14 дней привязка слетает).

        Он же гасит тревоги, которые больше не про сейчас (владелец 20.11.2026: уведомления «не исчезают» — у тревог
        нет карточки, открытием которой гаснет остальное): номер на связи — «остановлен» и «не на связи» (после новой
        привязки по QR «снова подключён» не приходит), телефон снова в сети — «давно не в сети», «снова подключён» —
        через сутки, у тех, кому тревоги больше не положены (супер-админы без выдачи), — все."""
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
                queries.wa_alerts_read(cursor, now, prefixes=('wa:park:', 'wa:offline:'))
                days = whatsapp.phone_seen_days(detail, now)
                if days is not None and days >= whatsapp.PHONE_ALERT_DAYS:
                    alert = ('wa:phone:%s' % now.date().isoformat(), 'WhatsApp найма: телефон давно не в сети',
                             'Телефон с номером не выходил в WhatsApp %d дн. Откройте на нём WhatsApp Business — '
                             'через 14 дней без телефона привязка слетает' % days)
                    chats = queries.telegram_chats(cursor, notices.whatsapp_alert(cursor, alert, now))
                else:
                    queries.wa_alerts_read(cursor, now, prefixes=('wa:phone:',))
            queries.wa_alerts_read(cursor, now, prefixes=('wa:back:',), before=now - timedelta(days=1))
            queries.wa_alerts_read(cursor, now, not_users=queries.alert_user_ids(cursor))
        for chat_id in chats.values():
            self._telegram(chat_id, notices.whatsapp_alert_html(alert[1], alert[2], self._base_url), None)
        try:
            self._wa_sweep_transcripts(now)
        except Exception:  # noqa: BLE001 — сторож состояния важнее повтора расшифровки
            log.exception('WhatsApp найма: повтор расшифровок не запустился')
        try:
            with self._cursor() as cursor:
                queries.wa_recount_recent(cursor, now - timedelta(minutes=10))
        except Exception:  # noqa: BLE001
            log.exception('WhatsApp найма: «ждут ответа» не пересчитаны')
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
