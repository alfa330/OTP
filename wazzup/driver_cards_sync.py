"""Очередь записи карточек водителя в сделки amoCRM.

Строка карточки (wazzup_driver_cards) и есть задание: сохранение кладёт её с
amo_state='pending' и будит поток, поток забирает её арендой (FOR UPDATE SKIP
LOCKED), находит сделку чата и пишет в неё поля (wazzup/driver_cards_amo.py).
Устройство то же, что у пересылки Global (syntony.py): один поток на процесс,
несколько процессов разводит аренда в базе, сеть — только после коммита аренды.
Итог записи меняет amo_state, триггер отдаёт его в живой поток раздела, и
карточка у всех открывших чат показывает «записано» без перезагрузки.

Порядок. В сделку уходит только ПОСЛЕДНЯЯ версия карточки чата: более ранние,
не успевшие уйти, помечаются «заменена», иначе запоздалая старая версия легла бы
в сделку поверх новой. Пока версия чата в аренде, следующая его версия ждёт:
две записи одного чата в amoCRM параллельно не идут.

Повторы. amoCRM недоступна, 429/5xx, обрыв, отказ во входе — повтор с
нарастающей паузой в пределах суток, дальше «не записано» с причиной; в
карточке есть «Повторить». Сделку не нашли — ещё две попытки за пару минут
(Wazzup заводит сделку нового чата не мгновенно), потом «сделка не найдена».
Отказ amoCRM по существу (поле, значение, нет сделки) — сразу «не записано».

Выключатель для стендов: WAZZUP_DRIVER_CARD_AMO_SYNC=off — карточки
сохраняются, в amoCRM ничего не уходит.
"""
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from . import driver_cards_amo as amo
from .driver_cards import load_options, store_options

# Аренда — с запасом на худший случай одной карточки (вход 2×60 с, поиск
# сделки до десятка запросов по 20 с) и продлевается перед самой записью (renew).
LEASE_SECONDS = 300
IDLE_SECONDS = 30
BATCH = 20
BUDGET_SECONDS = 20
RETRY_DELAYS = (15, 60, 180, 600, 1800, 3600)
GIVE_UP_SECONDS = 24 * 3600
NO_DEAL_DELAYS = (30, 90)
OPTIONS_REFRESH_SECONDS = 3600
OPTIONS_RETRY_SECONDS = 300
OPTIONS_FAILURE_PAUSE = 60
# Новый клиент — за несколько минут до конца жизни токена (~40 мин): повторный
# вход старой сессией amoCRM не работает.
CLIENT_MARGIN_SECONDS = 240
# Пауза перед новым входом после неудачного, растёт до четверти часа.
LOGIN_PAUSES = (60, 120, 300, 600, 900)

logger = logging.getLogger(__name__)
_wake = threading.Event()
_worker_lock = threading.Lock()
_worker = None
_worker_pid = None
_options_lock = threading.Lock()
_options_next_try = [0.0]


def sync_enabled():
    return os.getenv('WAZZUP_DRIVER_CARD_AMO_SYNC', 'on').strip().lower() not in ('off', '0', 'false', 'no')


def amo_configured():
    from amocrm import leads as amo_leads
    return amo_leads.is_configured()


class ClientHolder:
    """Клиент amoCRM одного потока: requests.Session не объявлена потокобезопасной.

    После неудачного входа (сменили пароль, капча, другая форма) новый вход — не
    раньше паузы: иначе каждая карточка очереди снова логинилась бы под общей
    учёткой amoCRM, под которой ходят и другие роботы портала, — сотни неудачных
    входов в сутки ведут к блокировке. Отказ во входе посреди работы (401) —
    тоже неудача: удачный вход с 401 на запросах повторять подряд незачем."""

    def __init__(self, factory=None, clock=time.monotonic):
        self._factory = factory
        self._clock = clock
        self._client = None
        self._failures = 0
        self._retry_at = 0.0

    def _failed(self):
        self._client = None
        self._retry_at = self._clock() + LOGIN_PAUSES[min(self._failures, len(LOGIN_PAUSES) - 1)]
        self._failures += 1

    def get(self):
        client = self._client
        if client is None or time.time() > getattr(client, 'expires_at', 0) - CLIENT_MARGIN_SECONDS:
            if self._clock() < self._retry_at:
                raise amo.AmoTransientError('вход в amoCRM не удался — повторим через несколько минут')
            factory = self._factory
            if factory is None:
                from amocrm.leads import AmoClient as factory
            self._client = None
            try:
                client = factory()
            except Exception as exc:
                self._failed()
                raise amo.AmoTransientError('вход в amoCRM не удался (%s)' % type(exc).__name__)
            self._failures = 0
            self._retry_at = 0.0
            self._client = client
        return client

    def drop(self):
        self._failed()


# ── Справочники ─────────────────────────────────────────────────────────────

def refresh_options(db, holder=None, force=False):
    """Свежие списки из amoCRM в снимок базы. None — не вышло (или пауза после неудачи)."""
    now = time.monotonic()
    with _options_lock:
        if not force and now < _options_next_try[0]:
            return None
        _options_next_try[0] = now + OPTIONS_FAILURE_PAUSE
    try:
        options = amo.fetch_options((holder or ClientHolder()).get())
    except amo.AmoAuthError:
        if holder is not None:
            holder.drop()
        logger.warning('Карточка водителя: amoCRM не приняла вход при чтении справочников')
        return None
    except Exception as exc:
        logger.warning('Карточка водителя: справочники amoCRM недоступны (%s)', type(exc).__name__)
        return None
    with db._get_cursor() as cursor:
        store_options(cursor, options)
    return dict(options, fetchedAt=None)


def current_options(db):
    """Снимок из базы, а без него — сразу из amoCRM (первый запуск после выкладки)."""
    with db._get_cursor() as cursor:
        options = load_options(cursor)
    if options is not None or not amo_configured():
        return options
    return refresh_options(db)


# ── Очередь ─────────────────────────────────────────────────────────────────

_SUPERSEDE_SQL = """
    UPDATE wazzup_driver_cards c
       SET amo_state = 'superseded', amo_lease_token = NULL, amo_lease_until = NULL
     WHERE c.amo_state = 'pending'
       AND (c.amo_lease_until IS NULL OR c.amo_lease_until < now())
       AND EXISTS (SELECT 1 FROM wazzup_driver_cards n
                    WHERE n.account = c.account AND n.channel_id = c.channel_id
                      AND n.chat_id = c.chat_id
                      AND (n.created_at, n.id) > (c.created_at, c.id))
"""

_CLAIM_SQL = """
    WITH candidate AS (
        SELECT c.id FROM wazzup_driver_cards c
         WHERE c.amo_state = 'pending' AND c.amo_next_attempt_at <= now()
           AND (c.amo_lease_until IS NULL OR c.amo_lease_until < now())
           AND NOT EXISTS (
               SELECT 1 FROM wazzup_driver_cards n
                WHERE n.account = c.account AND n.channel_id = c.channel_id
                  AND n.chat_id = c.chat_id AND n.id <> c.id
                  AND ((n.created_at, n.id) > (c.created_at, c.id) OR n.amo_lease_until > now()))
         ORDER BY c.amo_next_attempt_at, c.created_at
         LIMIT 1
         FOR UPDATE SKIP LOCKED
    )
    UPDATE wazzup_driver_cards c
       SET amo_lease_token = %s, amo_lease_until = now() + (%s * interval '1 second')
      FROM candidate
     WHERE c.id = candidate.id
    RETURNING c.id, c.channel_id, c.chat_id, c.driver_id, c.park_enum_id, c.city_enum_id,
              c.amo_attempts, c.amo_no_deal_attempts, c.amo_queued_at, c.amo_lease_token
"""

_ITEM_FIELDS = ('id', 'channel_id', 'chat_id', 'driver_id', 'park_enum_id', 'city_enum_id',
                'attempts', 'no_deal_attempts', 'queued_at', 'lease_token')


class LeaseLost(Exception):
    """Аренду карточки забрали (истекла, карточку заменили) — писать нельзя."""


def claim(db):
    with db._get_cursor() as cursor:
        cursor.execute(_SUPERSEDE_SQL)
        cursor.execute(_CLAIM_SQL, (str(uuid.uuid4()), LEASE_SECONDS))
        row = cursor.fetchone()
    return dict(zip(_ITEM_FIELDS, row)) if row else None


def anchors_of(db, item):
    """Времена последних сообщений чата — по ним ищется его беседа в amoCRM."""
    with db._get_cursor() as cursor:
        cursor.execute("""
            SELECT EXTRACT(EPOCH FROM dt)::bigint, EXTRACT(EPOCH FROM wazzup_dt)::bigint
              FROM wazzup_messages
             WHERE account = 'op' AND channel_id = %s AND chat_id = %s
             ORDER BY dt DESC LIMIT %s
        """, (item['channel_id'], item['chat_id'], amo.ANCHOR_LIMIT))
        rows = cursor.fetchall()
    return [int(value) for row in rows for value in row if value is not None]


def renew(db, item):
    """Продлить свою аренду прямо перед записью в сделку.

    Пока шёл поиск сделки, аренда могла истечь (amoCRM отвечала долго), а
    другой процесс — например, второй экземпляр на время выкладки — пометить
    эту версию заменённой и записать более новую. Тогда запоздалый PATCH
    положил бы старые значения поверх новых. Своя аренда — пишем, чужая — нет."""
    with db._get_cursor() as cursor:
        cursor.execute("""
            UPDATE wazzup_driver_cards
               SET amo_lease_until = now() + (%s * interval '1 second')
             WHERE id = %s AND amo_lease_token = %s AND amo_state = 'pending'
            RETURNING id
        """, (LEASE_SECONDS, str(item['id']), str(item['lease_token'])))
        if cursor.fetchone() is None:
            raise LeaseLost()


def _finish(db, item, state, lead_id=None, error=None):
    with db._get_cursor() as cursor:
        cursor.execute("""
            UPDATE wazzup_driver_cards
               SET amo_state = %s, amo_lead_id = COALESCE(%s, amo_lead_id), amo_error = %s,
                   amo_attempts = amo_attempts + 1,
                   amo_synced_at = CASE WHEN %s = 'synced' THEN now() ELSE amo_synced_at END,
                   amo_lease_token = NULL, amo_lease_until = NULL
             WHERE id = %s AND amo_lease_token = %s
        """, (state, lead_id, error, state, str(item['id']), str(item['lease_token'])))


def _again(db, item, delay, error, lead_id=None, no_deal=False):
    with db._get_cursor() as cursor:
        cursor.execute("""
            UPDATE wazzup_driver_cards
               SET amo_attempts = amo_attempts + 1, amo_error = %s,
                   amo_no_deal_attempts = amo_no_deal_attempts + %s,
                   amo_lead_id = COALESCE(%s, amo_lead_id),
                   amo_next_attempt_at = now() + (%s * interval '1 second'),
                   amo_lease_token = NULL, amo_lease_until = NULL
             WHERE id = %s AND amo_lease_token = %s
        """, (error, int(no_deal), lead_id, int(delay), str(item['id']), str(item['lease_token'])))


def _age_seconds(item):
    """Сколько идут повторы — от сохранения или «Повторить» (amo_queued_at)."""
    queued = item.get('queued_at')
    if not isinstance(queued, datetime):
        return 0
    if queued.tzinfo is None:
        queued = queued.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - queued).total_seconds()


def _retry(db, item, error, lead_id=None):
    # Пауза растёт по сбоям amoCRM, ожидания сделки нового чата сюда не входят.
    failures = int(item['attempts']) - int(item.get('no_deal_attempts') or 0) + 1
    if _age_seconds(item) >= GIVE_UP_SECONDS:
        _finish(db, item, 'failed', lead_id=lead_id, error=error)
        return
    _again(db, item, RETRY_DELAYS[min(max(failures, 1), len(RETRY_DELAYS)) - 1], error, lead_id=lead_id)


def _no_deal(db, item, reason):
    waits = int(item.get('no_deal_attempts') or 0) + 1
    if waits <= len(NO_DEAL_DELAYS):
        # Ожидаемое у нового чата: Wazzup ещё заводит сделку. Ошибку не показываем.
        _again(db, item, NO_DEAL_DELAYS[waits - 1], None, no_deal=True)
        return
    _finish(db, item, 'no_deal', error=reason)


def process_once(db, holder):
    """Забрать и записать одну карточку. False — заданий нет."""
    item = claim(db)
    if item is None:
        return False
    lead_id = None
    try:
        anchors = anchors_of(db, item)
        client = holder.get()
        lead_id = amo.resolve_lead(client, item['chat_id'], anchors)
        renew(db, item)
        amo.write_lead(client, lead_id, item)
    except LeaseLost:
        # Карточку уже ведёт другой процесс или её заменили — итог пишет он.
        return True
    except amo.NoDealError as exc:
        _no_deal(db, item, str(exc))
        return True
    except amo.AmoPermanentError as exc:
        _finish(db, item, 'failed', lead_id=lead_id, error=str(exc))
        return True
    except amo.AmoAuthError as exc:
        holder.drop()
        _retry(db, item, str(exc), lead_id=lead_id)
        return True
    except amo.AmoTransientError as exc:
        _retry(db, item, str(exc), lead_id=lead_id)
        return True
    except Exception as exc:
        # Без значений карточки и номера: в журнале только тип сбоя и id строки.
        logger.warning('Карточка водителя %s: запись в amoCRM упала (%s)', item['id'], type(exc).__name__)
        _retry(db, item, 'внутренняя ошибка (%s)' % type(exc).__name__, lead_id=lead_id)
        return True
    _finish(db, item, 'synced', lead_id=lead_id)
    return True


def wake():
    _wake.set()


def next_due_seconds(db):
    """Через сколько секунд срок ближайшего повтора (None — повторов нет).

    Поток засыпает не дольше этого: иначе повтор через 15 с ждал бы полного
    простоя очереди, а «сделка не найдена» у нового чата приходила бы позже."""
    with db._get_cursor() as cursor:
        cursor.execute("SELECT EXTRACT(EPOCH FROM MIN(GREATEST(amo_next_attempt_at, "
                       "COALESCE(amo_lease_until, amo_next_attempt_at))) - now()) "
                       "FROM wazzup_driver_cards WHERE amo_state = 'pending'")
        row = cursor.fetchone()
    return None if not row or row[0] is None else max(0.0, float(row[0]))


def _run(db, holder):
    options_due = 0.0
    while True:
        _wake.clear()
        try:
            if time.monotonic() >= options_due:
                fresh = refresh_options(db, holder, force=True)
                options_due = time.monotonic() + (OPTIONS_REFRESH_SECONDS if fresh else OPTIONS_RETRY_SECONDS)
            started = time.monotonic()
            for _ in range(BATCH):
                if not process_once(db, holder):
                    break
                if time.monotonic() - started >= BUDGET_SECONDS:
                    _wake.set()
                    break
            else:
                _wake.set()
        except Exception as exc:
            logger.warning('Карточка водителя: проход очереди amoCRM не удался (%s)', type(exc).__name__)
        if _wake.is_set():
            time.sleep(0.1)
        try:
            due = next_due_seconds(db)
        except Exception:
            due = None
        # Повтор, чья аренда ещё не истекла, проснётся по ней же не раньше секунды.
        _wake.wait(IDLE_SECONDS if due is None else min(IDLE_SECONDS, max(1.0, due + 0.5)))


def start_worker(db, client_factory=None):
    """Один поток на процесс; без amoCRM или с выключателем — не запускается."""
    global _worker, _worker_pid
    if not sync_enabled() or not amo_configured():
        return None
    pid = os.getpid()
    with _worker_lock:
        if _worker is not None and _worker_pid == pid and _worker.is_alive():
            return _worker
        _worker = threading.Thread(target=_run, args=(db, ClientHolder(client_factory)),
                                   name='wazzup-driver-cards-amo', daemon=True)
        _worker_pid = pid
        _worker.start()
        return _worker
