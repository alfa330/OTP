# -*- coding: utf-8 -*-
"""«Звонки Binotel» для партнёров: звонок фронт-офиса → строка calls_api_calls → событие
call.completed партнёру (partner_keys.webhooks, область «Звонки Binotel»).

Два пути звонка, оба кончаются в record():
* вебхук Binotel «API Call Completed» — наблюдатель on_binotel_call в
  dial_list.service.handle_webhook. Binotel шлёт его сразу после конца звонка: так CRM видит
  попытку, исход и разговор через секунды;
* сверка с историей (sync_history, раз в 10 минут) — один запрос stats/list-of-calls-for-period
  за сутки по ключу кабинета удалённого КЦ. Догоняет то, что вебхук не донёс (Binotel повторяет
  вебхук только через 20 минут, а упавший наблюдатель не повторит никто), и заодно узнаёт ID
  кабинета (companyID) — по нему вебхук отличает удалённый КЦ от Тез.

Событие уходит при первом появлении звонка и при его уточнении (другой исход, длиннее
разговор), повтор того же звонка — нет. Ошибка здесь не роняет ни вебхук, ни раздел.
"""

import logging
import threading
from collections import Counter
from datetime import datetime, timedelta

from partner_keys import scopes as partner_scopes
from partner_keys import webhooks as partner_webhooks

from . import queries, rules, schema

log = logging.getLogger(__name__)

SCOPE = partner_scopes.BINOTEL_CALLS
EVENT_COMPLETED = 'call.completed'
EVENT_PING = 'ping'
EVENTS = (EVENT_COMPLETED,)

COMPANY_KEY = 'company_id:%s' % rules.COMPANY
SYNCED_KEY = 'synced_at'
# Вчерашние сутки сверяем ещё полчаса после полуночи: звонок 23:58 доходит до истории не сразу.
YESTERDAY_GRACE = timedelta(minutes=30)
# ID кабинета неизвестен, а сегодня в нём звонков нет (05.11.2026 у удалённого КЦ был один
# звонок за сутки): ищем звонок в прошлых сутках, не дальше стольких дней. Один раз — потом ID
# лежит в calls_api_state, и первый же вебхук из CRM уходит сразу, а не со сверкой.
LEARN_LOOKBACK_DAYS = 7
# Ждать ли очереди к Binotel (секунд): фоновая сверка лишний раз не стоит в очереди.
SYNC_MAX_WAIT = 10
RECORD_MAX_WAIT = 8


class CallsApiError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


class CallsApiService(object):
    """dial_list — сервис раздела «Удаленный КЦ» или функция, отдающая его (он создаётся позже):
    через него идут клиент Binotel кабинета и его общий темп запросов."""

    def __init__(self, db, dial_list=None, now=None):
        self.db = db
        self._dial_list = dial_list
        self._now = now or (lambda: datetime.now(rules.ALMATY))
        self._company_id = None
        self._lock = threading.Lock()
        self._sync_lock = threading.Lock()

    # ── опоры ──────────────────────────────────────────────────────────────
    def _dial_list_service(self):
        value = self._dial_list
        return value() if callable(value) and not hasattr(value, 'client_for_department') else value

    def _cursor(self):
        return self.db._get_cursor()

    def ready(self):
        try:
            with self._cursor() as cursor:
                return schema.schema_is_ready(cursor)
        except Exception:                                # noqa: BLE001
            log.exception('calls_api: схема не проверена')
            return False

    def company_id(self):
        """ID кабинета удалённого КЦ у Binotel: память процесса → calls_api_state."""
        with self._lock:
            if self._company_id:
                return self._company_id
        with self._cursor() as cursor:
            value = queries.get_state(cursor, COMPANY_KEY)
        if value:
            with self._lock:
                self._company_id = value
        return value

    def _learn_company(self, calls):
        """ID кабинета — самый частый companyID звонков, полученных его же ключом API."""
        counts = Counter(call.get('company_id') for call in calls if call.get('company_id'))
        if not counts:
            return None
        value = counts.most_common(1)[0][0]
        if value != self.company_id():
            with self._cursor() as cursor:
                queries.set_state(cursor, COMPANY_KEY, value)
            log.info('calls_api: кабинет Binotel %s — companyID %s', rules.COMPANY, value)
        with self._lock:
            self._company_id = value
        return value

    # ── звонок → строка → событие ──────────────────────────────────────────
    def record(self, cursor, call, seat, source):
        """Сохранить звонок области и, если он новый или уточнён, поставить событие в очередь.
        Возвращает 'new' | 'changed' | 'same' | None (звонок не области)."""
        if not seat or not rules.in_scope(seat.get('department_code')):
            return None
        state, row = queries.save_call(cursor, call, seat, source)
        if row is not None:
            partner_webhooks.publish_with_cursor(cursor, SCOPE, EVENT_COMPLETED,
                                                 {'call': rules.document(row)})
        return state

    def on_binotel_call(self, fields, matched=False):
        """Наблюдатель вебхука «API Call Completed» (dial_list.service.call_observers)."""
        if matched:
            return None                                  # попытка обзвона удалённого КЦ
        call = rules.from_webhook(fields)
        if call is None:
            return None
        company = self.company_id()
        if not company or call['company_id'] != company:
            # Чужой кабинет (Тез) — или свой ещё не узнан: тогда звонок догонит сверка.
            return None
        with self._cursor() as cursor:
            seat = queries.seat_at(cursor, rules.COMPANY, call['internal_number'], call['started_at'])
            if not seat or not rules.in_scope(seat.get('department_code')):
                return None
            if queries.dial_list_call_ids(cursor, [call['general_call_id']]):
                return None
            state = self.record(cursor, call, seat, 'webhook')
        if state in ('new', 'changed'):
            partner_webhooks.wake()
        return state

    # ── сверка с историей ──────────────────────────────────────────────────
    def _client(self):
        dial_list = self._dial_list_service()
        if dial_list is None:
            raise CallsApiError('CALLS_API_NO_TELEPHONY', 'Раздел «Удаленный КЦ» не подключён', 503)
        department_id = self._line_department_id()
        if department_id is None:
            raise CallsApiError('CALLS_API_NO_TELEPHONY', 'Нет отдела с линиями кабинета', 503)
        return dial_list.client_for_department(department_id)

    def _line_department_id(self):
        """Отдел, чьи линии в кабинете rules.COMPANY: его ключом API ходим в Binotel."""
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT s.department_id FROM dial_list_department_settings s
                 WHERE COALESCE(NULLIF(TRIM(s.binotel_company), ''), 'remote_cc') = %s
                 ORDER BY s.department_id LIMIT 1
                """, (rules.COMPANY,))
            row = cursor.fetchone()
        return int(row[0]) if row else None

    def sync_history(self, now=None):
        """Догнать звонки области из истории Binotel за сегодня (после полуночи — и вчера).
        Возвращает {'calls': всего у Binotel, 'saved': новых и уточнённых} или None."""
        if not self._sync_lock.acquire(blocking=False):
            return None
        try:
            return self._sync_history(now or self._now())
        finally:
            self._sync_lock.release()

    def _sync_history(self, now):
        if not self.ready():
            return None
        client = self._client()
        local = now.astimezone(rules.ALMATY) if now.tzinfo else now.replace(tzinfo=rules.ALMATY)
        days = [local.date()]
        if local - datetime(local.year, local.month, local.day, tzinfo=rules.ALMATY) < YESTERDAY_GRACE:
            days.insert(0, local.date() - timedelta(days=1))
        raw_calls = []
        for day in days:
            raw_calls.extend(client.list_calls_for_day(day, max_wait=SYNC_MAX_WAIT))
        company = self._learn_company(raw_calls)
        if not company and not self.company_id():
            for back in range(1, LEARN_LOOKBACK_DAYS + 1):
                day = local.date() - timedelta(days=back)
                if day in days:
                    continue
                if self._learn_company(client.list_calls_for_day(day, max_wait=SYNC_MAX_WAIT)):
                    break
            company = self.company_id()
            if not company:
                log.warning('calls_api: ID кабинета %s не узнан — за %d дней в нём нет звонков; '
                            'вебхуки звонков ждут сверки', rules.COMPANY, LEARN_LOOKBACK_DAYS)
        calls = [call for call in (rules.from_history(raw) for raw in raw_calls) if call]
        calls = [call for call in calls if not company or call['company_id'] in ('', company)]
        saved = 0
        if calls:
            since = min(call['started_at'] for call in calls)
            with self._cursor() as cursor:
                seats = [seat for seat in queries.seats_since(cursor, rules.COMPANY, since)
                         if rules.in_scope(seat.get('department_code'))]
                lines = {seat['internal_number'] for seat in seats}
                ours = [call for call in calls if call['internal_number'] in lines]
                skip = queries.dial_list_call_ids(cursor, [call['general_call_id'] for call in ours])
                for call in ours:
                    if call['general_call_id'] in skip:
                        continue
                    seat = queries.pick_seat(seats, call['internal_number'], call['started_at'])
                    if self.record(cursor, call, seat, 'history') in ('new', 'changed'):
                        saved += 1
                queries.set_state(cursor, SYNCED_KEY, now.isoformat())
        else:
            with self._cursor() as cursor:
                queries.set_state(cursor, SYNCED_KEY, now.isoformat())
        if saved:
            partner_webhooks.wake()
            log.info('calls_api: сверка с историей Binotel догнала звонков: %d', saved)
        return {'calls': len(raw_calls), 'saved': saved}

    def cleanup(self):
        if not self.ready():
            return 0
        with self._cursor() as cursor:
            removed = queries.cleanup(cursor, schema.KEEP_DAYS)
        if removed:
            log.info('calls_api: удалено звонков старше %d дней: %d', schema.KEEP_DAYS, removed)
        return removed

    # ── для ручек партнёра ─────────────────────────────────────────────────
    def get_call(self, general_call_id):
        with self._cursor() as cursor:
            row = queries.get_call(cursor, general_call_id)
        return rules.document(row) if row else None

    def list_calls(self, **filters):
        with self._cursor() as cursor:
            rows, total = queries.list_calls(cursor, **filters)
        return [rules.document(row) for row in rows], total

    def operators(self):
        with self._cursor() as cursor:
            return queries.operators(cursor, rules.COMPANY)

    def recording_url(self, general_call_id):
        """Подписанная ссылка Binotel на запись разговора (живёт около часа)."""
        with self._cursor() as cursor:
            row = queries.get_call(cursor, general_call_id)
        if not row:
            raise CallsApiError('CALLS_API_NOT_FOUND', 'Звонок не найден', 404)
        if not rules.has_recording(row):
            raise CallsApiError('CALLS_API_NO_RECORDING', 'Разговора не было — записи нет', 404)
        dial_list = self._dial_list_service()
        department_id = row.get('line_department_id') or self._line_department_id()
        if dial_list is None or department_id is None:
            raise CallsApiError('CALLS_API_NO_TELEPHONY', 'Записи сейчас недоступны', 503)
        from binotel.client import RateLimited
        try:
            url = dial_list.client_for_department(department_id).get_call_record_url(
                row['general_call_id'], max_wait=RECORD_MAX_WAIT)
        except RateLimited:
            raise CallsApiError('CALLS_API_BUSY', 'АТС просит подождать — повторите через несколько секунд', 503)
        except Exception as exc:                         # noqa: BLE001
            log.warning('calls_api: запись %s недоступна: %s', row['general_call_id'], type(exc).__name__)
            raise CallsApiError('CALLS_API_UPSTREAM', 'АТС не отдала запись — повторите позже', 502)
        if not url:
            raise CallsApiError('CALLS_API_RECORDING_NOT_READY',
                                'Запись ещё не готова у АТС — повторите через минуту', 409)
        return {'id': str(row['general_call_id']), 'url': url, 'talk_sec': int(row['billsec'] or 0)}

    def ping(self, key_id):
        """Проверка приёмника — одной подписке, по просьбе самого партнёра."""
        payload = {'note': 'Проверка адреса приёмника вебхука iCORE', 'events': list(EVENTS)}
        return partner_webhooks.publish(self.db, SCOPE, EVENT_PING, payload, key_id=key_id)
