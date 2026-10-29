# -*- coding: utf-8 -*-
"""Подписки партнёров на события и их доставка вебхуком.

    webhooks.publish(db, scopes.OLX_CHATS, 'message.received', {...})

Партнёр сам подписывается через ручку своей области (например,
PUT /api/olx/webhook): адрес, секрет и список событий. Когда в портале
происходит событие области, строка на каждую подходящую подписку ложится в
очередь, а фоновый работник доставляет её POST'ом. Что здесь решено и почему:

* **Доставка «как минимум один раз», с повторами.** Сеть и чужой сервер
  ненадёжны: не-2xx и таймаут повторяются с задержкой 5 с, удвоением до часа
  (Retry-After при 429/503 уважается), до MAX_ATTEMPTS попыток, потом строка
  помечается failed и видна партнёру в /webhook/deliveries. Повтор может
  прийти дважды — у каждого события свой `id`, он же `Idempotency-Key`.

* **Подпись HMAC.** Каждая доставка подписана секретом подписки:
  `X-Partner-Signature: sha256=hex(hmac(secret, timestamp + "." + body))`,
  timestamp — в `X-Partner-Timestamp`. Партнёр сверяет подпись и отбрасывает
  старые метки. Секрет хранится открыто: им подписывают, хешем подписать нельзя.

* **Только https и без учётных данных в адресе.** Адрес партнёра — его
  публичный приёмник; редиректы не выполняются, тело ответа не читается.

* **Строка очереди арендуется** (lease, SKIP LOCKED): несколько экземпляров
  портала не доставят одно событие дважды одновременно; HTTP идёт вне
  транзакции — соединение с базой не держится на время чужого ответа.

* **Подписка гаснет вместе с ключом**: отозванный ключ и ключ без области — не
  получатели; старые строки очереди дойдут до своего предела и остановятся.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets as secrets_module
import threading
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

LEASE_SECONDS = 90
MAX_ATTEMPTS = 30
IDLE_SECONDS = 20
DONE_KEEP_DAYS = 7
FAILED_KEEP_DAYS = 30
MAX_URL = 500
MIN_SECRET = 16
MAX_SECRET = 200
MAX_EVENT = 64

SIGNATURE_HEADER = 'X-Partner-Signature'
TIMESTAMP_HEADER = 'X-Partner-Timestamp'
EVENT_HEADER = 'X-Partner-Event'
DELIVERY_HEADER = 'X-Partner-Delivery'

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_worker_lock = threading.Lock()
_worker = None
_worker_pid = None
_wake_event = threading.Event()
_maintenance_lock = threading.Lock()
_maintenance_at = 0.0


# ── Правила адреса, секрета и событий ───────────────────────────────────────────

def validate_url(value):
    """(адрес, ошибка). Только https, с хостом, без учётных данных и пробелов."""
    url = str(value or '').strip()
    if not url:
        return None, 'нужен адрес приёмника (поле url)'
    if len(url) > MAX_URL:
        return None, 'адрес длиннее %d знаков' % MAX_URL
    if any(ch.isspace() for ch in url) or not url.isascii():
        return None, 'адрес должен быть ASCII без пробелов'
    parts = urlsplit(url)
    if parts.scheme != 'https':
        return None, 'адрес должен начинаться с https://'
    if not parts.hostname:
        return None, 'в адресе нет хоста'
    if parts.username or parts.password:
        return None, 'учётные данные в адресе не принимаются'
    return url, None


def generate_secret():
    return secrets_module.token_urlsafe(32)


def normalize_secret(value):
    """(секрет, ошибка). None — секрет не прислан (вызывающий выпустит свой)."""
    if value is None or value == '':
        return None, None
    text = str(value).strip()
    if len(text) < MIN_SECRET:
        return None, 'секрет короче %d знаков' % MIN_SECRET
    if len(text) > MAX_SECRET:
        return None, 'секрет длиннее %d знаков' % MAX_SECRET
    if not text.isascii() or not text.isprintable() or any(ch.isspace() for ch in text):
        return None, 'секрет — печатные знаки ASCII без пробелов'
    return text, None


def normalize_events(values, allowed):
    """(список событий, ошибка). Пусто — все события области."""
    if values is None:
        return [], None
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return None, 'events — список названий событий'
    cleaned = []
    for value in values:
        code = str(value or '').strip()
        if not code:
            continue
        if code not in allowed:
            return None, 'неизвестное событие: %s' % code[:MAX_EVENT]
        if code not in cleaned:
            cleaned.append(code)
    return cleaned, None


def sign(secret, timestamp, body):
    """Подпись доставки. body — байты ровно те, что уходят в запросе."""
    message = ('%s.' % timestamp).encode('utf-8') + body
    return 'sha256=' + hmac.new(str(secret).encode('utf-8'), message, hashlib.sha256).hexdigest()


def body_for(event, event_id, created_at, payload):
    """Тело доставки — компактный JSON без экранирования кириллицы."""
    document = {
        'event': event,
        'id': str(event_id),
        'created_at': created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at),
        'data': payload,
    }
    return json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


# ── Подписки ───────────────────────────────────────────────────────────────────

_SUB_FIELDS = ('id', 'key_id', 'scope', 'url', 'secret', 'events', 'enabled', 'created_at',
               'updated_at', 'last_delivered_at', 'last_error', 'last_error_at', 'failures_in_row')
_SUB_COLUMNS = ('id, key_id, scope, url, secret, events, enabled, created_at, updated_at, '
                'last_delivered_at, last_error, last_error_at, failures_in_row')


def _iso(value):
    return value.isoformat() if value is not None and hasattr(value, 'isoformat') else value


def _subscription(row):
    item = dict(zip(_SUB_FIELDS, row))
    for field in ('created_at', 'updated_at', 'last_delivered_at', 'last_error_at'):
        item[field] = _iso(item[field])
    item['events'] = [str(code) for code in (item['events'] or [])]
    return item


def get_subscription(cursor, key_id, scope):
    cursor.execute('SELECT ' + _SUB_COLUMNS + ' FROM partner_webhooks WHERE key_id = %s AND scope = %s',
                   (int(key_id), scope))
    row = cursor.fetchone()
    return _subscription(row) if row else None


def upsert_subscription(cursor, key_id, scope, *, url, secret, events):
    """Создать или переписать подписку ключа на область. Сбросом сбоев: адрес новый."""
    cursor.execute(
        """
        INSERT INTO partner_webhooks (key_id, scope, url, secret, events)
        VALUES (%(key)s, %(scope)s, %(url)s, %(secret)s, %(events)s)
        ON CONFLICT (key_id, scope) DO UPDATE
           SET url = EXCLUDED.url, secret = EXCLUDED.secret, events = EXCLUDED.events,
               enabled = TRUE, updated_at = """ + _NOW + """,
               last_error = NULL, last_error_at = NULL, failures_in_row = 0
        RETURNING """ + _SUB_COLUMNS,
        {'key': int(key_id), 'scope': scope, 'url': url, 'secret': secret, 'events': list(events)},
    )
    return _subscription(cursor.fetchone())


def delete_subscription(cursor, key_id, scope):
    cursor.execute('DELETE FROM partner_webhooks WHERE key_id = %s AND scope = %s RETURNING id',
                   (int(key_id), scope))
    return cursor.fetchone() is not None


def subscriptions_by_key(cursor):
    """{key_id: [подписки без секрета]} — для списка ключей в «Сессиях»."""
    cursor.execute(
        'SELECT key_id, scope, url, enabled, last_delivered_at, failures_in_row, last_error '
        'FROM partner_webhooks ORDER BY key_id, scope')
    result = {}
    for key_id, scope, url, enabled, delivered, failures, error in cursor.fetchall():
        result.setdefault(int(key_id), []).append({
            'scope': scope, 'url': url, 'enabled': bool(enabled),
            'last_delivered_at': _iso(delivered), 'failures_in_row': int(failures or 0),
            'last_error': error,
        })
    return result


_DELIVERY_FIELDS = ('id', 'event', 'event_id', 'state', 'attempts', 'created_at', 'next_attempt_at',
                    'delivered_at', 'completed_at', 'last_status', 'last_error')


def recent_deliveries(cursor, webhook_id, limit=50):
    cursor.execute(
        """
        SELECT id, event, event_id, state, attempts, created_at, next_attempt_at,
               delivered_at, completed_at, last_status, last_error
          FROM partner_webhook_outbox
         WHERE webhook_id = %s
         ORDER BY created_at DESC, id DESC
         LIMIT %s
        """,
        (int(webhook_id), max(1, min(int(limit or 50), 200))),
    )
    items = []
    for row in cursor.fetchall():
        item = dict(zip(_DELIVERY_FIELDS, row))
        for field in ('created_at', 'next_attempt_at', 'delivered_at', 'completed_at'):
            item[field] = _iso(item[field])
        item['event_id'] = str(item['event_id']) if item['event_id'] else None
        items.append(item)
    return items


def delivery_stats(cursor, webhook_id):
    cursor.execute(
        "SELECT count(*) FILTER (WHERE state = 'ready'), count(*) FILTER (WHERE state = 'done'), "
        "count(*) FILTER (WHERE state = 'failed') FROM partner_webhook_outbox WHERE webhook_id = %s",
        (int(webhook_id),))
    row = cursor.fetchone() or (0, 0, 0)
    return {'ready': int(row[0] or 0), 'done': int(row[1] or 0), 'failed': int(row[2] or 0)}


# ── Публикация события ─────────────────────────────────────────────────────────

def publish_with_cursor(cursor, scope, event, payload, *, key_id=None):
    """Строка очереди на каждую подходящую подписку. Возвращает число строк."""
    params = {'scope': scope, 'event': event}
    where = ['w.scope = %(scope)s', 'w.enabled', 'k.revoked_at IS NULL',
             '%(scope)s = ANY(k.scopes)',
             '(cardinality(w.events) = 0 OR %(event)s = ANY(w.events))']
    if key_id is not None:
        where.append('w.key_id = %(key)s')
        params['key'] = int(key_id)
    cursor.execute(
        'SELECT w.id FROM partner_webhooks w JOIN partner_keys k ON k.id = w.key_id WHERE '
        + ' AND '.join(where), params)
    targets = [int(row[0]) for row in cursor.fetchall()]
    if not targets:
        return 0
    body = json.dumps(payload, ensure_ascii=False, default=str)
    for webhook_id in targets:
        cursor.execute(
            'INSERT INTO partner_webhook_outbox (webhook_id, event, event_id, payload) '
            'VALUES (%s, %s, %s, %s)',
            (webhook_id, str(event)[:MAX_EVENT], str(uuid.uuid4()), body))
    return len(targets)


def publish(db, scope, event, payload, *, key_id=None):
    """Как publish_with_cursor, но своей транзакцией и без исключений наружу:
    событие — приставка к делу робота, и падение здесь его не роняет."""
    try:
        with db._get_cursor() as cursor:
            count = publish_with_cursor(cursor, scope, event, payload, key_id=key_id)
    except Exception:                                    # noqa: BLE001
        log.exception('partner_keys: событие %s/%s не положено в очередь', scope, event)
        return 0
    if count:
        wake()
    return count


# ── Доставка ───────────────────────────────────────────────────────────────────

def _claim(db):
    token = str(uuid.uuid4())
    with db._get_cursor() as cursor:
        cursor.execute(
            """
            WITH candidate AS (
                SELECT id FROM partner_webhook_outbox
                 WHERE state = 'ready'
                   AND next_attempt_at <= now()
                   AND (lease_until IS NULL OR lease_until < now())
                 ORDER BY next_attempt_at, id
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1
            )
            UPDATE partner_webhook_outbox o
               SET lease_token = %s, lease_until = now() + (%s * interval '1 second')
              FROM candidate c, partner_webhooks w
             WHERE o.id = c.id AND w.id = o.webhook_id
            RETURNING o.id, o.webhook_id, o.event, o.event_id, o.payload, o.attempts,
                      o.created_at, o.lease_token, w.url, w.secret, w.enabled
            """,
            (token, LEASE_SECONDS),
        )
        row = cursor.fetchone()
    if not row:
        return None
    item = dict(zip(('id', 'webhook_id', 'event', 'event_id', 'payload', 'attempts',
                     'created_at', 'lease_token', 'url', 'secret', 'enabled'), row))
    if isinstance(item['payload'], str):
        try:
            item['payload'] = json.loads(item['payload'])
        except ValueError:
            item['payload'] = {'raw': item['payload']}
    return item


def _finish(db, item, state, *, delivered, status=None, error=None):
    with db._get_cursor() as cursor:
        cursor.execute(
            """
            UPDATE partner_webhook_outbox
               SET state = %s, completed_at = now(), attempts = attempts + 1,
                   delivered_at = CASE WHEN %s THEN now() ELSE delivered_at END,
                   last_status = %s, last_error = %s, lease_token = NULL, lease_until = NULL
             WHERE id = %s AND lease_token = %s
            """,
            (state, bool(delivered), status, error, item['id'], str(item['lease_token'])))
        if delivered:
            cursor.execute(
                'UPDATE partner_webhooks SET last_delivered_at = ' + _NOW
                + ', failures_in_row = 0, last_error = NULL, last_error_at = NULL WHERE id = %s',
                (item['webhook_id'],))
        else:
            cursor.execute(
                'UPDATE partner_webhooks SET failures_in_row = failures_in_row + 1, last_error = %s, '
                'last_error_at = ' + _NOW + ' WHERE id = %s', (error, item['webhook_id']))


def _retry_delay(attempts, response=None):
    delay = min(3600, 5 * 2 ** min(max(int(attempts) - 1, 0), 10))
    if response is not None and response.status_code in (429, 503):
        value = response.headers.get('Retry-After', '')
        try:
            retry_after = float(value)
        except (TypeError, ValueError):
            try:
                moment = parsedate_to_datetime(value)
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                retry_after = (moment - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                retry_after = 0
        if retry_after == retry_after:  # не NaN
            delay = max(delay, min(3600, max(0, retry_after)))
    return delay


def _retry(db, item, delay, *, status=None, error=None):
    attempts = int(item['attempts']) + 1
    failed = attempts >= MAX_ATTEMPTS
    with db._get_cursor() as cursor:
        cursor.execute(
            """
            UPDATE partner_webhook_outbox
               SET attempts = %s, state = %s,
                   next_attempt_at = now() + (%s * interval '1 second'),
                   completed_at = CASE WHEN %s THEN now() ELSE NULL END,
                   last_status = %s, last_error = %s, lease_token = NULL, lease_until = NULL
             WHERE id = %s AND lease_token = %s
            """,
            (attempts, 'failed' if failed else 'ready', delay, failed, status, error,
             item['id'], str(item['lease_token'])))
        cursor.execute(
            'UPDATE partner_webhooks SET failures_in_row = failures_in_row + 1, last_error = %s, '
            'last_error_at = ' + _NOW + ' WHERE id = %s', (error, item['webhook_id']))


def _post(item, transport=None):
    """(успех, статус, ошибка, ответ). Тело ответа не читается."""
    timestamp = str(int(time.time()))
    body = body_for(item['event'], item['event_id'], item['created_at'], item['payload'])
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'iCORE-partner-webhooks/1',
        EVENT_HEADER: item['event'],
        DELIVERY_HEADER: str(item['event_id']),
        'Idempotency-Key': str(item['event_id']),
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: sign(item['secret'], timestamp, body),
    }
    session = None
    response = None
    try:
        if transport is None:
            session = requests.Session()
            session.trust_env = False
            transport = session.post
        response = transport(item['url'], data=body, headers=headers, timeout=(3, 10),
                             allow_redirects=False, stream=True)
        status = int(response.status_code)
        return 200 <= status < 300, status, 'http_%d' % status, response
    finally:
        if response is not None:
            response.close()
        if session is not None:
            session.close()


def process_once(db, transport=None):
    """Взять одну строку и доставить. HTTP — после фиксации аренды, вне транзакции."""
    item = _claim(db)
    if item is None:
        return False
    if not item.get('enabled'):
        # Подписку выключили, пока строка ждала: не слать, не терять аренду зря.
        _finish(db, item, 'failed', delivered=False, error='subscription_disabled')
        return True
    try:
        success, status, error, response = _post(item, transport)
        delay = _retry_delay(item['attempts'] + 1, response)
    except (requests.RequestException, OSError, ValueError, TypeError) as exc:
        success, status, error = False, None, 'transport_error: %s' % type(exc).__name__
        delay = _retry_delay(item['attempts'] + 1)
    if success:
        _finish(db, item, 'done', delivered=True, status=status)
    else:
        _retry(db, item, delay, status=status, error=error)
    return True


def _maintenance(db):
    global _maintenance_at
    now = time.monotonic()
    with _maintenance_lock:
        if now < _maintenance_at:
            return
        _maintenance_at = now + 3600
    with db._get_cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM partner_webhook_outbox WHERE id IN (
                SELECT id FROM partner_webhook_outbox
                 WHERE (state = 'done' AND completed_at < now() - (%s * interval '1 day'))
                    OR (state = 'failed' AND completed_at < now() - (%s * interval '1 day'))
                 ORDER BY id LIMIT 2000
            )
            """,
            (DONE_KEEP_DAYS, FAILED_KEEP_DAYS))


def wake():
    _wake_event.set()


def _run(db):
    while True:
        _wake_event.clear()
        try:
            _maintenance(db)
            started = time.monotonic()
            for _ in range(25):
                if not process_once(db):
                    break
                if time.monotonic() - started >= 15:
                    _wake_event.set()
                    break
            else:
                _wake_event.set()
        except Exception:                                # noqa: BLE001
            # В исключениях библиотек бывают адреса и параметры SQL — лог без них.
            log.warning('partner_keys: итерация доставки вебхуков упала')
        if _wake_event.is_set():
            time.sleep(0.1)
        _wake_event.wait(IDLE_SECONDS)


def start_worker(db):
    """Один демон на процесс; экземпляры портала координируются арендой строк."""
    global _worker, _worker_pid
    if str(os.getenv('PARTNER_WEBHOOKS_DISABLED') or '').strip().lower() in ('1', 'true', 'yes'):
        log.info('partner_keys: доставка вебхуков выключена переменной PARTNER_WEBHOOKS_DISABLED')
        return None
    pid = os.getpid()
    with _worker_lock:
        if _worker is not None and _worker_pid == pid and _worker.is_alive():
            return _worker
        _worker = threading.Thread(target=_run, args=(db,), name='partner-webhooks', daemon=True)
        _worker_pid = pid
        _worker.start()
        return _worker
