"""Voice messages of «Чаты ОП» as text (owner, 29.10.2026: «транскрипция аудио в
текст, Gemini, баланс между качеством и ценой»).

How a voice message gets its text:
* arrival — the webhook wakes the worker, which queues every audio message of
  the last day that has no row yet (the history sync of «Поток» lands the same
  way), so the text is usually ready before an operator opens the chat;
* view — a chat opened in the section asks for the audio messages it shows;
  older ones (before this feature, or a failure) are queued right then;
* retry — «Повторить» under a failed one.

One durable queue (wazzup_voice_transcripts, wazzup/voice_schema.py) and one
daemon per process; database leases keep two processes of a deploy apart. The
download and the paid request run with no transaction open: a deploy's start-up
DDL must never wait behind them. Recognition itself is wazzup/voice_asr.py.

Transcripts are the same class of data as message texts: kept in the database
as long as their message, never written to logs.
"""
import collections
import json
import logging
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from uuid import uuid4

from flask import Blueprint, jsonify, request

from . import voice_asr

logger = logging.getLogger(__name__)

STATUSES = ('pending', 'ready', 'no_speech', 'failed', 'unavailable', 'skipped')
WORKERS = 3
# Longer than the worst attempt: download (30 s) + a listen cut at its timeout and
# asked again, each with Vertex's own retry (voice_asr.request_seconds, up to 180 s).
# A process that dies mid-attempt leaves the message to another after this.
LEASE_SECONDS = 900
MAX_ATTEMPTS = 4
RETRY_DELAYS = (20, 120, 600)
IDLE_SECONDS = 60
# One drain of the queue before the worker looks for new arrivals again.
DRAIN_SECONDS = 120
DISCOVER_HOURS = 24
MAX_IDS = 200
# A person opening chats queues at most this many old voice messages an hour;
# a normal shift opens a few dozen.
USER_HOURLY_LIMIT = 300

_wake_event = threading.Event()
_worker_lock = threading.Lock()
_worker = None
_worker_pid = None
_maintenance_at = 0.0
_limit_warned_day = None
_rate_lock = threading.Lock()
_queued_by_user = collections.OrderedDict()


def enabled():
    return (os.getenv('WAZZUP_VOICE_TRANSCRIPTS', 'true').strip().lower()
            not in ('0', 'false', 'no', 'off'))


def daily_limit():
    """Attempts per Almaty day (one or two paid requests each) before the worker
    pauses: ~10× the usual flow (≈150 voice messages a day), a fuse against a
    loop, not a quota."""
    try:
        return max(0, int(os.getenv('WAZZUP_VOICE_DAILY_LIMIT', '2000')))
    except (TypeError, ValueError):
        return 2000


# ── queue ────────────────────────────────────────────────────────────────────

def discover(db, hours=DISCOVER_HOURS):
    """Queue the recent audio messages that have no row yet. Cheap: the window
    is read by the (dt) index, and an existing row is skipped by its key."""
    with db._get_cursor() as cursor:
        cursor.execute("""
            INSERT INTO wazzup_voice_transcripts (message_id, account, channel_id, chat_id, source)
            SELECT m.message_id, m.account, m.channel_id, m.chat_id, 'arrival'
              FROM wazzup_messages m
             WHERE m.dt >= now() - make_interval(hours => %s)
               AND m.type = 'audio' AND m.content_uri IS NOT NULL AND NOT m.is_deleted
               AND NOT EXISTS (SELECT 1 FROM wazzup_voice_transcripts t
                                WHERE t.message_id = m.message_id)
            ON CONFLICT (message_id) DO NOTHING
        """, (int(hours),))
        return cursor.rowcount


_ALMATY_DAY = "(now() AT TIME ZONE 'Asia/Almaty')::date"


def _paid_today(db):
    with db._get_cursor() as cursor:
        cursor.execute(f"SELECT attempts FROM wazzup_voice_daily WHERE day = {_ALMATY_DAY}")
        row = cursor.fetchone()
        return int(row[0]) if row else 0


def _claim(db, limit):
    """Lease up to `limit` due messages. A message whose attempts are spent is
    never claimed again, even if its last outcome could not be stored (the
    maintenance sweep closes it as failed): a paid request must not repeat
    forever because a write failed."""
    token = str(uuid4())
    with db._get_cursor() as cursor:
        cursor.execute("""
            WITH candidate AS (
                SELECT message_id FROM wazzup_voice_transcripts
                 WHERE status = 'pending' AND next_attempt_at <= now() AND attempts < %s
                   AND (lease_until IS NULL OR lease_until < now())
                 ORDER BY next_attempt_at, message_id
                 FOR UPDATE SKIP LOCKED LIMIT %s
            )
            UPDATE wazzup_voice_transcripts t
               SET lease_token = %s, lease_until = now() + make_interval(secs => %s),
                   attempts = t.attempts + 1, updated_at = now()
              FROM candidate c
             WHERE t.message_id = c.message_id
            RETURNING t.message_id, t.attempts,
                      (SELECT m.content_uri FROM wazzup_messages m
                        WHERE m.message_id = t.message_id AND NOT m.is_deleted)
        """, (MAX_ATTEMPTS, int(limit), token, LEASE_SECONDS))
        items = [{'message_id': row[0], 'attempts': row[1], 'content_uri': row[2], 'lease_token': token}
                 for row in cursor.fetchall()]
        if items:
            cursor.execute(f"""
                INSERT INTO wazzup_voice_daily (day, attempts) VALUES ({_ALMATY_DAY}, %s)
                ON CONFLICT (day) DO UPDATE SET attempts = wazzup_voice_daily.attempts + EXCLUDED.attempts
            """, (len(items),))
        return items


def _usage_params(result):
    usage = result.get('usage') or None
    # Two listens of a short recording come summed in `tokens`, their raw blocks in usage.
    tokens = result.get('tokens') or (voice_asr.usage_tokens(usage) if usage else None)
    audio, text, output, thinking = tokens or (None,) * 4
    return (result.get('model'), result.get('model_version'),
            voice_asr.PROMPT_VERSION if result.get('model') else None,
            result.get('audio_bytes'),
            round(result['duration'] * 1000) if result.get('duration') is not None else None,
            audio, text, output, thinking, result.get('cost'), result.get('latency_ms'),
            json.dumps(usage) if usage else None)


_USAGE_SET = """
    model = COALESCE(%s, model), model_version = COALESCE(%s, model_version),
    prompt_version = COALESCE(%s, prompt_version),
    audio_bytes = COALESCE(%s, audio_bytes), audio_duration_ms = COALESCE(%s, audio_duration_ms),
    audio_tokens = COALESCE(%s, audio_tokens), text_tokens = COALESCE(%s, text_tokens),
    output_tokens = COALESCE(%s, output_tokens), thinking_tokens = COALESCE(%s, thinking_tokens),
    -- every paid attempt of the message adds up here
    estimated_cost = CASE WHEN %s::numeric IS NULL THEN estimated_cost
                          ELSE COALESCE(estimated_cost, 0) + %s::numeric END,
    latency_ms = COALESCE(%s, latency_ms), usage = COALESCE(%s::jsonb, usage),
    lease_token = NULL, lease_until = NULL, updated_at = now()
"""


def _usage_values(result):
    params = _usage_params(result)
    # The cost appears twice in _USAGE_SET (the NULL test and the sum).
    return params[:9] + (params[9], params[9]) + params[10:]


def _finish(db, item, result):
    """A final answer: ready, no_speech, unavailable or skipped."""
    with db._get_cursor() as cursor:
        cursor.execute("""
            UPDATE wazzup_voice_transcripts
               SET status = %s, text = %s, language = %s, error = %s, completed_at = now(),
        """ + _USAGE_SET + """
             WHERE message_id = %s AND lease_token = %s
        """, (result['status'], result.get('text'), result.get('language'), result.get('error'),
              *_usage_values(result), item['message_id'], item['lease_token']))


def _retry(db, item, error, result=None):
    """A failed attempt: again later, or «failed» once the attempts are spent."""
    final = item['attempts'] >= MAX_ATTEMPTS
    delay = RETRY_DELAYS[min(item['attempts'], len(RETRY_DELAYS)) - 1]
    with db._get_cursor() as cursor:
        cursor.execute("""
            UPDATE wazzup_voice_transcripts
               SET status = %s, error = %s, next_attempt_at = now() + make_interval(secs => %s),
                   completed_at = CASE WHEN %s THEN now() END,
        """ + _USAGE_SET + """
             WHERE message_id = %s AND lease_token = %s
        """, ('failed' if final else 'pending', str(error)[:500], delay, final,
              *_usage_values(result or {}), item['message_id'], item['lease_token']))


def process(item, *, download=None, transcribe=None):
    """(kind, result-or-error) for one claimed message; never raises."""
    if not item.get('content_uri'):
        return 'final', {'status': 'unavailable', 'error': 'сообщения уже нет в архиве'}
    raw = None
    try:
        raw, content_type = (download or voice_asr.download)(item['content_uri'])
        result = (transcribe or voice_asr.transcribe)(raw, content_type)
        return 'final', dict(result, audio_bytes=len(raw))
    except voice_asr.VoiceError as error:
        if error.kind in ('unavailable', 'skipped'):
            return 'final', {'status': error.kind, 'error': str(error),
                             'audio_bytes': len(raw) if raw is not None else None}
        return 'retry', error
    except Exception as error:
        # The message of a library error can carry a URL; the type is enough here.
        logger.warning('Wazzup voice: %s on a transcript attempt', type(error).__name__)
        return 'retry', voice_asr.VoiceError(f'внутренняя ошибка ({type(error).__name__})')


def _store_outcome(db, item, kind, outcome):
    try:
        if kind == 'final':
            try:
                _finish(db, item, outcome)
            except Exception as error:
                # The answer itself does not fit the row: count it as a failed
                # attempt (with its cost), so the attempts run out instead of
                # the lease expiring into the same paid request again.
                logger.warning('Wazzup voice: could not store a transcript (%s)', type(error).__name__)
                _retry(db, item, f'не удалось сохранить расшифровку ({type(error).__name__})',
                       dict(outcome, text=None))
        else:
            _retry(db, item, outcome, getattr(outcome, 'result', None))
    except Exception as error:
        # Never the traceback: driver errors carry the SQL parameters, the text among them.
        logger.warning('Wazzup voice: could not store a transcript outcome (%s)', type(error).__name__)


def drain(db, pool, seconds=DRAIN_SECONDS):
    """Transcribe due messages, up to WORKERS at a time, for about `seconds`.

    A conveyor, not batches: a freed place takes the next message at once, so one
    request hanging on Vertex (seconds to tens of seconds) never holds the others.
    True — time ran out with the queue still being worked (go on after a yield)."""
    in_flight, started, more = {}, time.monotonic(), False
    while True:
        room = WORKERS - len(in_flight)
        if room:
            if time.monotonic() - started >= seconds:
                more = True
            else:
                budget = _budget_left(db)
                for item in (_claim(db, min(room, budget)) if budget else []):
                    in_flight[pool.submit(process, item)] = item
        if not in_flight:
            return more
        done, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
        for future in done:
            item = in_flight.pop(future)
            _store_outcome(db, item, *future.result())


def _maintenance(db):
    """Hourly: rows of messages the archive no longer keeps go with them; a row
    whose attempts are spent but whose last outcome was never stored is closed
    as failed; the daily counter keeps two months."""
    global _maintenance_at
    now = time.monotonic()
    if now < _maintenance_at:
        return
    _maintenance_at = now + 3600
    with db._get_cursor() as cursor:
        cursor.execute("""
            UPDATE wazzup_voice_transcripts
               SET status = 'failed', error = COALESCE(error, 'попытки исчерпаны'), completed_at = now(),
                   lease_token = NULL, lease_until = NULL, updated_at = now()
             WHERE status = 'pending' AND attempts >= %s
               AND (lease_until IS NULL OR lease_until < now())
        """, (MAX_ATTEMPTS,))
        cursor.execute("DELETE FROM wazzup_voice_daily WHERE day < CURRENT_DATE - 60")
        cursor.execute("""
            DELETE FROM wazzup_voice_transcripts WHERE message_id IN (
                SELECT t.message_id FROM wazzup_voice_transcripts t
                 WHERE t.created_at < now() - interval '1 hour'
                   AND (t.lease_until IS NULL OR t.lease_until < now())
                   AND NOT EXISTS (SELECT 1 FROM wazzup_messages m WHERE m.message_id = t.message_id)
                 LIMIT 5000)
        """)


def _budget_left(db):
    global _limit_warned_day
    limit = daily_limit()
    used = _paid_today(db)
    if used < limit:
        return limit - used
    day = time.strftime('%Y-%m-%d')
    if _limit_warned_day != day:
        _limit_warned_day = day
        logger.warning('Wazzup voice: daily limit of %s paid requests reached, queue paused', limit)
    return 0


# ── worker ───────────────────────────────────────────────────────────────────

def wake():
    _wake_event.set()


def _run(db):
    pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='wazzup-voice')
    discovered_at = 0.0
    while True:
        woken = _wake_event.is_set()
        _wake_event.clear()
        try:
            if enabled():
                _maintenance(db)
                if woken or time.monotonic() - discovered_at >= IDLE_SECONDS:
                    discovered_at = time.monotonic()
                    discover(db)
                if drain(db, pool):
                    _wake_event.set()   # a long backlog: yield, then go on
        except Exception as error:
            logger.warning('Wazzup voice: worker iteration failed (%s)', type(error).__name__)
        _wake_event.wait(IDLE_SECONDS)


def start_worker(db):
    """One daemon per process; the queue's leases coordinate the processes."""
    global _worker, _worker_pid
    if not enabled() or not voice_asr.vertex_configured():
        return None
    pid = os.getpid()
    with _worker_lock:
        if _worker is not None and _worker_pid == pid and _worker.is_alive():
            return _worker
        _worker = threading.Thread(target=_run, args=(db,), name='wazzup-voice', daemon=True)
        _worker_pid = pid
        _worker.start()
        return _worker


def payload_has_audio(payload):
    """Does a webhook carry a voice message (then the worker is woken)."""
    messages = payload.get('messages') if isinstance(payload, dict) else None
    return isinstance(messages, list) and any(
        isinstance(m, dict) and m.get('type') == 'audio' for m in messages)


# ── route ────────────────────────────────────────────────────────────────────

def _allow_queue(user_id, count, now=None):
    """How many of `count` new messages this person may queue now (hourly window)."""
    now = time.monotonic() if now is None else now
    with _rate_lock:
        history = _queued_by_user.setdefault(user_id, collections.deque())
        while history and now - history[0] > 3600:
            history.popleft()
        allowed = max(0, min(count, USER_HOURLY_LIMIT - len(history)))
        history.extend([now] * allowed)
        _queued_by_user.move_to_end(user_id)
        while len(_queued_by_user) > 500:
            _queued_by_user.popitem(last=False)
        return allowed


def _ids(value):
    if not isinstance(value, list) or len(value) > MAX_IDS:
        return None
    if any(not isinstance(v, str) or not 0 < len(v) <= 200 for v in value):
        return None
    return list(dict.fromkeys(value))


def build_voice_blueprint(*, db, require_api_key, guard, preflight, account_arg, channel_scope,
                          on_queued=None):
    """POST /api/wazzup/voice-transcripts?account=… {channelId, chatId, messageIds, retry?}

    The audience of the chat feed (guard — the same reader guard as the feed) gets
    the text of the voice messages it shows; the ones without a row are queued,
    the failed ones listed in `retry` are queued again. The answer lists only
    voice messages of that chat: {items: [{messageId, status, text}]}."""
    bp = Blueprint('wazzup_voice', __name__, url_prefix='/api/wazzup/voice-transcripts')
    notify = on_queued or wake

    @bp.route('', methods=['POST', 'OPTIONS'])
    @require_api_key
    def transcripts():
        if request.method == 'OPTIONS':
            return preflight()
        user_id, error = guard()
        if error:
            return error
        account = account_arg()
        if account is None:
            return jsonify(error='unknown account'), 400
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error='Некорректный запрос'), 400
        channel_id, chat_id = body.get('channelId'), body.get('chatId')
        ids, retry = _ids(body.get('messageIds')), _ids(body.get('retry') or [])
        if (not isinstance(channel_id, str) or not isinstance(chat_id, str) or not channel_id
                or not chat_id or len(channel_id) > 100 or len(chat_id) > 200 or ids is None or retry is None):
            return jsonify(error='Некорректный запрос'), 400
        scope = channel_scope()
        if scope is not None and channel_id not in scope:
            return jsonify(error='Этот канал вам не открыт'), 403
        wanted = list(dict.fromkeys(ids + retry))
        if not wanted:
            return jsonify(items=[])
        queued = 0
        # A retry starts the attempts over, so it is paid for like a new one: under the
        # same hourly allowance (a button pressed again and again stays bounded).
        retry = retry[:_allow_queue(user_id, len(retry))] if enabled() and retry else []
        with db._get_cursor() as cursor:
            chat = (account, channel_id, chat_id)
            if retry:
                cursor.execute("""
                    UPDATE wazzup_voice_transcripts
                       SET status = 'pending', attempts = 0, next_attempt_at = now(), error = NULL,
                           completed_at = NULL, source = 'retry', requested_by = %s, updated_at = now()
                     WHERE message_id = ANY(%s) AND account = %s AND channel_id = %s AND chat_id = %s
                       AND status = 'failed'
                """, (user_id, retry, *chat))
                queued += cursor.rowcount
            cursor.execute("""
                SELECT m.message_id, t.status, t.text
                  FROM wazzup_messages m
                  LEFT JOIN wazzup_voice_transcripts t ON t.message_id = m.message_id
                 WHERE m.account = %s AND m.channel_id = %s AND m.chat_id = %s
                   AND m.message_id = ANY(%s) AND m.type = 'audio'
            """, (*chat, wanted))
            rows = cursor.fetchall()
            missing = [row[0] for row in rows if row[1] is None]
            allowed = _allow_queue(user_id, len(missing)) if enabled() and missing else 0
            new = set()
            if allowed:
                cursor.execute("""
                    INSERT INTO wazzup_voice_transcripts
                        (message_id, account, channel_id, chat_id, source, requested_by)
                    SELECT m.message_id, m.account, m.channel_id, m.chat_id, 'view', %s
                      FROM wazzup_messages m
                     WHERE m.message_id = ANY(%s) AND m.account = %s AND m.channel_id = %s
                       AND m.chat_id = %s AND m.type = 'audio' AND m.content_uri IS NOT NULL
                       AND NOT m.is_deleted
                    ON CONFLICT (message_id) DO NOTHING
                    RETURNING message_id
                """, (user_id, missing[:allowed], *chat))
                new = {row[0] for row in cursor.fetchall()}
                queued += len(new)
                # A row another viewer (or the worker's discovery) created between our
                # read and our insert is there now: report what it says, not «no text».
                raced = [message_id for message_id in missing[:allowed] if message_id not in new]
                if raced:
                    cursor.execute("""SELECT message_id, status, text FROM wazzup_voice_transcripts
                                       WHERE message_id = ANY(%s)""", (raced,))
                    late = {row[0]: row for row in cursor.fetchall()}
                    rows = [late.get(row[0], row) for row in rows]
        if queued:
            notify()
        items = []
        for message_id, status, text in rows:
            if status is None and message_id in new:
                status = 'pending'
            if status is None:
                continue
            items.append({'messageId': message_id, 'status': status,
                          'text': text if status == 'ready' else None})
        return jsonify(items=items)

    return bp
