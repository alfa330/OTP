"""Forward only Global events, without making the webhook wait for Syntony.

Delivery is at least once. The stable Idempotency-Key lets the receiver suppress
the rare duplicate after a process dies between HTTP acceptance and its commit.
Neither payloads, URLs nor credentials are written to application logs.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import os
import re
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

import requests


GLOBAL_CHANNEL_IDS = frozenset({
    '99df6893-fb6b-4e1d-a78e-9e6e6b37abb2',
    'a4bccb5e-5d41-483d-b7c1-a1079685577d',
})
EVENT_KEYS = ('messages', 'statuses', 'channelsUpdates')
LEASE_SECONDS = 90
MAX_ATTEMPTS = 256
IDLE_SECONDS = 20
RESOLVE_SECONDS = 30
logger = logging.getLogger(__name__)
_worker_lock = threading.Lock()
_worker = None
_worker_pid = None
_wake_event = threading.Event()
_maintenance_lock = threading.Lock()
_maintenance_at = 0.0


@dataclass(frozen=True)
class Config:
    url: str = field(repr=False)
    key: str = field(repr=False)
    auth_header: str = 'Authorization'
    auth_prefix: str = 'Bearer'


def load_config():
    """Fail closed unless the relay is explicitly enabled and fully configured."""
    if os.getenv('SYNTONY_WEBHOOK_ENABLED', '').strip().lower() != 'true':
        return None
    url = os.getenv('SYNTONY_WEBHOOK_API_URL', '').strip()
    key = os.getenv('SYNTONY_WEBHOOK_API_KEY', '')
    header = os.getenv('SYNTONY_WEBHOOK_AUTH_HEADER', 'Authorization')
    prefix = os.getenv('SYNTONY_WEBHOOK_AUTH_PREFIX', 'Bearer')
    try:
        parsed = urlsplit(url)
        valid_url = (parsed.scheme == 'https' and parsed.hostname
                     and parsed.username is None and parsed.password is None
                     and not parsed.fragment and parsed.port in (None, 443))
    except ValueError:
        return None
    if not valid_url or any(ord(char) < 33 for char in url):
        return None
    if not key or not re.fullmatch(r'[!#$%&\'*+.^_`|~0-9A-Za-z-]+', header):
        return None
    if header.lower() not in ('authorization', 'x-api-key') and not header.lower().startswith('x-'):
        return None
    if header.lower() in ('x-icore-delivery-id', 'x-forwarded-for', 'x-forwarded-host'):
        return None
    if any(ord(char) < 32 or ord(char) > 126 for char in key + prefix):
        return None
    return Config(url, key.strip(), header, prefix.strip()) if key.strip() else None


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'),
                      sort_keys=True, allow_nan=False).encode('utf-8')


def _fingerprint(phase, payload):
    return hashlib.sha256(b'op:' + phase.encode('ascii') + b':' + _dump(payload)).hexdigest()


def _message_mapping(payload, existing=None):
    mapping = dict(existing or {})
    for message in payload.get('messages', []) if isinstance(payload.get('messages'), list) else []:
        if not isinstance(message, dict):
            continue
        mid, channel = message.get('messageId'), message.get('channelId')
        if isinstance(mid, str) and mid and isinstance(channel, str) and channel:
            previous = mapping.get(mid)
            # Conflicting evidence must never route another channel's receipt.
            mapping[mid] = channel if previous in (None, channel) else ''
    return mapping


def filter_payload(payload, channel_by_message=None):
    """Return (Global events, unresolved statuses, can_preserve_raw_body).

    Only three documented, channel-scoped arrays are eligible. Object values
    remain untouched; in particular we never add a channelId to a status.
    """
    if not isinstance(payload, dict):
        return {}, {}, False
    mapping = _message_mapping(payload, channel_by_message)
    ready, pending = {}, {}
    complete = all(key in EVENT_KEYS for key in payload)
    for key in EVENT_KEYS:
        if key not in payload:
            continue
        entries = payload[key]
        if not isinstance(entries, list):
            complete = False
            continue
        selected, unresolved = [], []
        for entry in entries:
            if not isinstance(entry, dict):
                complete = False
                continue
            channel = entry.get('channelId')
            if key == 'statuses' and channel is None:
                mid = entry.get('messageId')
                if isinstance(mid, str) and mid:
                    channel = mapping.get(mid)
                    if channel is None:
                        unresolved.append(entry)
            if isinstance(channel, str) and channel in GLOBAL_CHANNEL_IDS:
                selected.append(entry)
            else:
                complete = False
        if selected:
            ready[key] = selected
        if unresolved:
            pending[key] = unresolved
    if complete and ready:
        # Preserve documented empty arrays as well when the whole envelope fits.
        ready = {key: value for key, value in payload.items()}
    return ready, pending, bool(complete and ready)


def _status_ids(payload):
    statuses = payload.get('statuses') if isinstance(payload, dict) else None
    if not isinstance(statuses, list):
        return []
    return list({item['messageId'] for item in statuses if isinstance(item, dict)
                 and item.get('channelId') is None
                 and isinstance(item.get('messageId'), str) and item['messageId']})


def _lookup(cursor, payload):
    ids = _status_ids(payload)
    if not ids:
        return {}
    cursor.execute("""
        SELECT message_id, channel_id FROM wazzup_messages
        WHERE account = 'op' AND message_id = ANY(%s)
    """, (ids,))
    return {row[0]: row[1] for row in cursor.fetchall()}


def _insert(cursor, phase, payload, raw_body=None):
    body = _dump(payload) if raw_body is None else raw_body
    cursor.execute("""
        INSERT INTO wazzup_syntony_outbox (fingerprint, account, phase, payload)
        VALUES (%s, 'op', %s, %s) ON CONFLICT (fingerprint) DO NOTHING
        RETURNING id
    """, (_fingerprint(phase, payload), phase, body))
    return 1 if cursor.fetchone() else 0


def enqueue(db, payload, raw_body=None, account='op'):
    """Persist matching events before acknowledging Wazzup. Never call HTTP."""
    if account != 'op' or load_config() is None or not isinstance(payload, dict):
        return 0
    ready, pending, _ = filter_payload(payload)
    if not ready and not pending:
        return 0
    # Preserve bytes only after confirming they describe the very same object
    # that was filtered, otherwise use our safe serialization.
    if isinstance(raw_body, str):
        raw_body = raw_body.encode('utf-8')
    if raw_body is not None:
        try:
            raw_body = bytes(raw_body)
            if json.loads(raw_body.decode('utf-8')) != payload:
                raw_body = None
        except (TypeError, ValueError, UnicodeError):
            raw_body = None
    inserted = 0
    with db._get_cursor() as cursor:
        ready, pending, complete = filter_payload(payload, _lookup(cursor, payload))
        if not ready and not pending:
            return 0
        # A replay must keep the original partition even if its unknown statuses
        # became known in the meantime. A hash-only receipt prevents repartition
        # into a new delivery (with a different digest) on a Wazzup retry.
        cursor.execute("""
            INSERT INTO wazzup_syntony_outbox
                (fingerprint, account, phase, payload, completed_at)
            VALUES (%s, 'op', 'receipt', NULL, now())
            ON CONFLICT (fingerprint) DO NOTHING RETURNING id
        """, (_fingerprint('receipt', payload),))
        if not cursor.fetchone():
            return 0
        if ready:
            inserted += _insert(cursor, 'ready', ready, raw_body if complete else None)
        if pending:
            inserted += _insert(cursor, 'resolve', pending)
    start_worker(db)
    wake()
    return inserted


def _claim(db):
    token = str(uuid4())
    with db._get_cursor() as cursor:
        cursor.execute("""
            WITH candidate AS (
                SELECT id FROM wazzup_syntony_outbox
                WHERE account = 'op' AND phase IN ('ready', 'resolve')
                  AND next_attempt_at <= now()
                  AND (lease_until IS NULL OR lease_until < now())
                ORDER BY next_attempt_at, id FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE wazzup_syntony_outbox o
            SET lease_token = %s, lease_until = now() + (%s * interval '1 second')
            FROM candidate c WHERE o.id = c.id
            RETURNING o.id, o.fingerprint, o.phase, o.payload, o.attempts,
                      o.created_at, o.lease_token
        """, (token, LEASE_SECONDS))
        row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(('id', 'fingerprint', 'phase', 'payload', 'attempts',
                     'created_at', 'lease_token'), row))


def _finish(db, item, phase, error=None, delivered=False):
    with db._get_cursor() as cursor:
        cursor.execute("""
            UPDATE wazzup_syntony_outbox
            SET phase = %s, payload = NULL, completed_at = now(), last_error = %s,
                delivered_at = CASE WHEN %s THEN now() ELSE delivered_at END,
                attempts = attempts + %s,
                lease_token = NULL, lease_until = NULL
            WHERE id = %s AND lease_token = %s
        """, (phase, error, delivered, int(delivered), item['id'], str(item['lease_token'])))


def _resolve(db, item):
    try:
        payload = json.loads(bytes(item['payload']))
    except (TypeError, ValueError, UnicodeError):
        _finish(db, item, 'ignored', 'invalid_payload')
        return
    with db._get_cursor() as cursor:
        cursor.execute("""
            SELECT created_at < now() - interval '72 hours'
            FROM wazzup_syntony_outbox WHERE id = %s AND lease_token = %s FOR UPDATE
        """, (item['id'], str(item['lease_token'])))
        row = cursor.fetchone()
        if row is None:
            return
        expired = row[0]
        ready, pending, _ = filter_payload(payload, _lookup(cursor, payload))
        if ready:
            _insert(cursor, 'ready', ready)
        if pending and not expired:
            cursor.execute("""
                UPDATE wazzup_syntony_outbox SET payload = %s,
                    attempts = attempts + 1,
                    next_attempt_at = now() + (%s * interval '1 second'),
                    lease_token = NULL, lease_until = NULL, last_error = 'awaiting_channel'
                WHERE id = %s AND lease_token = %s
            """, (_dump(pending), RESOLVE_SECONDS, item['id'], str(item['lease_token'])))
        else:
            cursor.execute("""
                UPDATE wazzup_syntony_outbox SET phase = %s, payload = NULL,
                    completed_at = now(), last_error = %s,
                    lease_token = NULL, lease_until = NULL
                WHERE id = %s AND lease_token = %s
            """, ('ignored' if pending else 'done',
                  'unresolved_channel_expired' if pending else None,
                  item['id'], str(item['lease_token'])))


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
        if retry_after == retry_after:  # Reject NaN, still bound infinity.
            delay = max(delay, min(3600, max(0, retry_after)))
    return delay


def _retry(db, item, delay, error):
    attempts = item['attempts'] + 1
    with db._get_cursor() as cursor:
        cursor.execute("""
            UPDATE wazzup_syntony_outbox
            SET attempts = %s, phase = CASE WHEN %s >= %s THEN 'failed' ELSE 'ready' END,
                next_attempt_at = now() + (%s * interval '1 second'),
                lease_token = NULL, lease_until = NULL, last_error = %s,
                completed_at = CASE WHEN %s >= %s THEN now() ELSE NULL END
            WHERE id = %s AND lease_token = %s
        """, (attempts, attempts, MAX_ATTEMPTS, delay, error, attempts, MAX_ATTEMPTS,
              item['id'], str(item['lease_token'])))


def _post(config, body, fingerprint, transport=None):
    auth = (config.auth_prefix + ' ' if config.auth_prefix else '') + config.key
    kwargs = dict(data=body, headers={
        'Content-Type': 'application/json',
        config.auth_header: auth,
        'Idempotency-Key': fingerprint,
    }, timeout=(3, 10), allow_redirects=False, stream=True)
    session = None
    response = None
    try:
        if transport is None:
            session = requests.Session()
            session.trust_env = False
            transport = session.post
        response = transport(config.url, **kwargs)
        status = int(response.status_code)
        return 200 <= status < 300, 'http_' + str(status), response
    finally:
        # stream=True means no response body (which may echo credentials or
        # customer content) is downloaded, parsed, or logged.
        if response is not None:
            response.close()
        if session is not None:
            session.close()


def process_once(db, transport=None):
    """Claim and process at most one event. HTTP happens after the lease commits."""
    config = load_config()
    if config is None:
        return False
    item = _claim(db)
    if item is None:
        return False
    if item['phase'] == 'resolve':
        _resolve(db, item)
        return True
    try:
        success, error, response = _post(
            config, bytes(item['payload']), item['fingerprint'], transport)
        delay = _retry_delay(item['attempts'] + 1, response)
    except (requests.RequestException, OSError, ValueError, TypeError):
        success, error, delay = False, 'transport_error', _retry_delay(item['attempts'] + 1)
    if success:
        _finish(db, item, 'done', delivered=True)
    else:
        _retry(db, item, delay, error)
    return True


def _maintenance(db):
    global _maintenance_at
    now = time.monotonic()
    with _maintenance_lock:
        if now < _maintenance_at:
            return
        _maintenance_at = now + 3600
    with db._get_cursor() as cursor:
        # A bounded sweep keeps an unavailable receiver from retaining customer
        # payloads forever. Completed deliveries retain only their digest.
        cursor.execute("""
            DELETE FROM wazzup_syntony_outbox WHERE id IN (
                SELECT id FROM wazzup_syntony_outbox
                WHERE (phase IN ('done', 'ignored') AND completed_at < now() - interval '7 days')
                   OR (created_at < now() - interval '45 days'
                       AND (lease_until IS NULL OR lease_until < now()))
                ORDER BY id LIMIT 2000
            )
        """)


def wake():
    _wake_event.set()


def _run(db):
    while True:
        _wake_event.clear()
        try:
            if load_config() is not None:
                _maintenance(db)
                started = time.monotonic()
                for _ in range(25):
                    if not process_once(db):
                        break
                    if time.monotonic() - started >= 15:
                        # The budget limits a drain, not the delivery latency of
                        # an existing backlog. Yield briefly, then continue.
                        _wake_event.set()
                        break
                else:
                    _wake_event.set()
        except Exception:
            # Exceptions from DB/network libraries can contain connection URLs
            # and SQL parameters. Keep logs deliberately payload-free.
            logger.warning('Syntony relay worker iteration failed')
        if _wake_event.is_set():
            time.sleep(0.1)
        _wake_event.wait(IDLE_SECONDS)


def start_worker(db):
    """One daemon per process; database leases coordinate multiple instances."""
    global _worker, _worker_pid
    if load_config() is None:
        return None
    pid = os.getpid()
    with _worker_lock:
        if _worker is not None and _worker_pid == pid and _worker.is_alive():
            return _worker
        _worker = threading.Thread(target=_run, args=(db,), name='wazzup-syntony', daemon=True)
        _worker_pid = pid
        _worker.start()
        return _worker
