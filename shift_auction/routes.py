"""Раздел «shift_auction»: ручки /api/shift_auction — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_shift_auction_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, Response, g, jsonify, request, send_file, stream_with_context
from database import SHIFT_AUCTION_MODE_CHAT
from database import SHIFT_AUCTION_MODE_LINE
from database import SHIFT_AUCTION_TEST_EVENT_NOTIFY_CHANNEL
from database import db
from database import normalize_shift_auction_mode
from datetime import datetime
import collections
import hashlib
import json
import logging
import os
import psycopg2
import re
import requests
import select
import threading
import time


SHIFT_AUCTION_EVENT_HEARTBEAT_SECONDS = 15

SHIFT_AUCTION_EVENT_LISTENER_RETRY_SECONDS = 2

SHIFT_AUCTION_EVENT_FETCH_LIMIT = 500

shift_auction_event_condition = threading.Condition()

shift_auction_event_signal_id = 0

shift_auction_event_buffer_max_id = 0

shift_auction_event_buffer_ready = False

shift_auction_event_listener_started = False

shift_auction_event_listener_lock = threading.Lock()

shift_auction_stream_lock = threading.Lock()

shift_auction_active_streams = 0

def _release_shift_auction_stream_slot():
    """Освободить место. Вызывается на закрытии ответа, в том числе при обрыве."""
    global shift_auction_active_streams
    with shift_auction_stream_lock:
        shift_auction_active_streams = max(0, shift_auction_active_streams - 1)

def _shift_auction_active_stream_count():
    with shift_auction_stream_lock:
        return shift_auction_active_streams

def _signal_shift_auction_event_waiters():
    global shift_auction_event_signal_id
    with shift_auction_event_condition:
        shift_auction_event_signal_id += 1
        shift_auction_event_condition.notify_all()

def _get_shift_auction_event_signal_id():
    with shift_auction_event_condition:
        return shift_auction_event_signal_id

def _wait_for_shift_auction_event_signal(last_seen_signal_id, timeout_seconds):
    timeout_seconds = max(0.1, float(timeout_seconds or 0.1))
    with shift_auction_event_condition:
        if shift_auction_event_signal_id <= last_seen_signal_id:
            shift_auction_event_condition.wait(timeout=timeout_seconds)
        return shift_auction_event_signal_id

def _fetch_shift_auction_events_with_cursor(cursor, after_id, limit=SHIFT_AUCTION_EVENT_FETCH_LIMIT):
    """Fetch events after ``after_id`` using a caller-provided cursor.

    Used by the PG listener on its OWN dedicated connection so the event
    fan-out never touches the shared application connection pool.
    """
    cursor.execute(
        """
        SELECT id, event_type, payload, created_at, COALESCE(direction_mode, 'line')
        FROM shift_auction_test_events
        WHERE id > %s
        ORDER BY id
        LIMIT %s
        """,
        (int(after_id or 0), int(limit)),
    )
    return [
        {
            "id": row[0],
            "event_type": row[1],
            "payload": row[2] or {},
            "created_at": row[3].isoformat() if row[3] else None,
            "direction_mode": row[4] if len(row) > 4 else SHIFT_AUCTION_MODE_LINE,
        }
        for row in (cursor.fetchall() or [])
    ]

SHIFT_AUCTION_MANAGER_DEPARTMENT_CODE = 'szov'

def _requested_shift_auction_direction(payload=None):
    """Направление из запроса: тело (для POST/PUT) или строка запроса."""
    if isinstance(payload, dict):
        for key in ('direction', 'direction_mode'):
            if payload.get(key) not in (None, ''):
                return payload.get(key)
    return request.args.get('direction') or request.args.get('direction_mode')

def _parse_shift_auction_time_groups(raw_groups):
    """Week time groups from the manager's form, with datetimes parsed.

    ``None`` (field absent) means "leave the week's groups untouched", while an
    empty list clears them — the distinction matters for clients that save only
    part of the form.
    """
    if raw_groups is None:
        return None
    if not isinstance(raw_groups, list):
        raise ValueError("AUCTION_GROUP_INVALID")
    groups = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            raise ValueError("AUCTION_GROUP_INVALID")
        groups.append({
            **raw_group,
            "starts_at": _parse_shift_auction_test_datetime(raw_group.get('starts_at')),
            "ends_at": _parse_shift_auction_test_datetime(raw_group.get('ends_at')),
        })
    return groups

def _parse_shift_auction_test_datetime(value):
    if value is None:
        return None
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except Exception:
        raise ValueError("INVALID_AUCTION_DATETIME")

def _shift_auction_test_error_response(error):
    code = str(error)
    mapping = {
        "AUCTION_NOT_OPEN": ("Аукцион еще не открыт", 409),
        "AUCTION_NOT_AVAILABLE": ("Аукцион недоступен", 409),
        "AUCTION_NOT_CLOSED": ("Сохранить итоговые графики можно только после завершения аукциона", 409),
        "AUCTION_CANNOT_PAUSE": ("Приостановить можно только открытый аукцион", 409),
        "AUCTION_NOT_PAUSED": ("Аукцион сейчас не приостановлен", 409),
        "AUCTION_ALREADY_CLOSED": ("Аукцион уже завершен", 409),
        "AUCTION_NO_LOTS": ("В аукционе нет смен для публикации", 409),
        "AUCTION_NO_PARTICIPANTS": ("В аукционе нет участников для публикации", 409),
        "AUCTION_PERIOD_ALREADY_PASSED": ("Период аукциона уже прошёл — сохранять его в графики нельзя", 409),
        "AUCTION_NOTHING_CLAIMED": ("В аукционе никто не взял ни одной смены — сохранять в графики нечего", 409),
        "INVALID_AUCTION_CONTROL_ACTION": ("Неизвестное действие управления аукционом", 400),
        "NOT_TEST_PARTICIPANT": ("Вы не включены в тестовую группу аукциона", 403),
        "OPERATOR_NOT_FOUND": ("Оператор не найден", 404),
        "LOT_NOT_FOUND": ("Смена не найдена", 404),
        "LOT_ALREADY_CLAIMED": ("Смену уже забрали", 409),
        "LOT_NOT_CLAIMED": ("Эта смена сейчас не закреплена", 409),
        "LOT_NOT_OWNED": ("Эта смена закреплена за другим оператором", 409),
        "RATE_TOO_LOW": ("Смена недоступна по вашей ставке", 403),
        "SHIFT_RATE_MISMATCH": ("Доступны только смены вашей ставки", 403),
        "SHIFT_NORM_EXCEEDED": ("Эта смена превысит вашу норму часов на период", 409),
        "SHIFT_AUCTION_STATUS_PERIOD_BLOCKED": ("Этот день закрыт статусным периодом оператора", 409),
        "DAY_OFF_SELECTED": ("На этот день выбран выходной", 409),
        "DAY_ALREADY_HAS_SHIFT": ("На этот день уже выбрана смена", 409),
        "DAY_HAS_CLAIMED_SHIFT": ("На этот день уже выбрана смена", 409),
        "SHIFT_OVERLAPS_EXISTING": ("Эта смена пересекается по времени с уже взятой", 409),
        "NIGHT_SHIFT_ALREADY_ADJACENT": ("Две ночные смены подряд брать нельзя — соседняя ночь уже ваша", 409),
        "TOPUP_NOT_ACTIVE": ("Режим добора ещё не включён", 409),
        "TOPUP_ALREADY_ACTIVE": ("Режим добора уже включён", 409),
        "DAY_OFF_LIMIT": ("Лимит выходных уже занят статусными периодами или выбранными выходными", 409),
        "INVALID_AUCTION_DATETIME": ("Некорректная дата запуска аукциона", 400)
    }
    mapping.update({
        "AUCTION_END_BEFORE_START": ("Время завершения должно быть позже старта", 400),
        "AUCTION_GROUP_INVALID": ("Некорректные данные группы времени", 400),
        "AUCTION_GROUP_LIMIT": ("Слишком много групп времени на один период", 400),
        "AUCTION_GROUP_START_REQUIRED": ("У каждой группы должно быть время старта", 400),
        "AUCTION_GROUP_WINDOW_EMPTY": ("Окно группы пустое: завершение должно быть позже её старта", 400),
        "AUCTION_GROUP_WEEK_REQUIRED": ("Сначала выберите период аукциона — группы задаются для него", 400),
        "SELF_SCHEDULE_NOT_ALLOWED": ("Свой график вам не открыт", 403),
        "SELF_SCHEDULE_LIMIT_EXCEEDED": ("Лимит своего графика: не больше нормы плюс 10 часов", 409),
        "AUCTION_PERIOD_NOT_FOUND": ("План графика для аукциона не найден", 404),
        "AUCTION_PERIOD_INVALID": ("У плана некорректный период: дата окончания раньше начала", 409),
        "AUCTION_PERIOD_EMPTY": ("В выбранном плане нет смен", 409),
        "AUCTION_PERIOD_PAST": ("Прошедший период нельзя запустить заново", 409),
        "POST_AUCTION_NOT_ACTIVE": ("Дополнительные смены можно забирать только после сохранения итогов в графики", 409),
        "LOT_NOT_OPEN_FOR_POST_CLAIM": ("Эту смену больше нельзя забрать", 409),
        "SHIFT_ALREADY_STARTED": ("Эта смена уже началась или прошла", 409),
        "INVALID_PARTIAL_SHIFT_RANGE": ("Некорректный интервал частичной смены", 400),
        "PARTIAL_SHIFT_NOT_ALLOWED": ("Эту смену можно взять только целиком", 409),
        "PARTIAL_SHIFT_OUT_OF_RANGE": ("Выбранный интервал должен быть внутри исходной смены", 400),
        "LOT_INVALID": ("Смена недоступна", 409),
        "POST_AUCTION_LOT_NOT_RELEASABLE": ("Эту смену нельзя вернуть — она уже сохранена в графики", 409),
        "SHIFT_OVERLAP": ("Смена пересекается по времени с другой сменой оператора", 409),
        "SHIFT_NOT_FOUND": ("Смена не найдена в плане", 404),
        "MISSING_TARGET": ("Не указан лот или смена", 400),
        "CLAIM_NOT_FOUND": ("Эта смена не найдена среди ваших взятых", 404),
        "CANCEL_WINDOW_EXPIRED": ("Отменить смену можно только в течение 10 минут после того, как вы её взяли", 409),
        "INVALID_DURATION": ("Длина смены не соответствует ставке", 400),
        "INVALID_RATE": ("Некорректная ставка смены", 400),
        "INVALID_DATE": ("Некорректная дата смены", 400),
        "DATE_OUT_OF_RANGE": ("Этот день не входит в текущий период аукциона", 409),
    })
    message, status = mapping.get(code, ("Ошибка аукциона смен", 400))
    return jsonify({"error": message, "code": code}), status


def build_shift_auction_blueprint(
    *,
    ALLOWED_ORIGINS,
    API_TOKEN,
    ROLE_HIERARCHY,
    SHIFT_AUCTION_EVENT_BUFFER_MAXLEN,
    TASK_TELEGRAM_TIMEOUT_SECONDS,
    TELEGRAM_MAX_MESSAGE_CHARS,
    WAITRESS_THREADS,
    _append_server_timing,
    _build_cors_preflight_response,
    _build_postgres_connection_params,
    _env_int,
    _get_authenticated_requester,
    _get_role_level,
    _has_min_role,
    _is_admin_role,
    _is_allowed_origin,
    _is_supervisor_role,
    _normalize_origin,
    _normalize_user_role,
    _record_elapsed_server_timing,
    _send_telegram_text_message,
    _truncate_for_telegram,
    require_api_key,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('shift_auction', __name__, url_prefix='/api')

    shift_auction_event_buffer = collections.deque(maxlen=SHIFT_AUCTION_EVENT_BUFFER_MAXLEN)

    SHIFT_AUCTION_STREAM_LIMIT = _env_int('SHIFT_AUCTION_STREAM_LIMIT', 50, minimum=10, maximum=WAITRESS_THREADS)

    def _try_acquire_shift_auction_stream_slot(limit=None):
        """Занять место под SSE-поток. False — мест нет, звать поток нельзя."""
        global shift_auction_active_streams
        ceiling = int(SHIFT_AUCTION_STREAM_LIMIT if limit is None else limit)
        with shift_auction_stream_lock:
            if shift_auction_active_streams >= ceiling:
                return False
            shift_auction_active_streams += 1
            return True

    def _fetch_recent_shift_auction_events_with_cursor(cursor):
        """Warm a new process with the newest buffer window, not the full history."""
        cursor.execute(
            """
        SELECT id, event_type, payload, created_at, COALESCE(direction_mode, 'line')
        FROM shift_auction_test_events
        ORDER BY id DESC
        LIMIT %s
        """,
            (int(SHIFT_AUCTION_EVENT_BUFFER_MAXLEN),),
        )
        rows = list(cursor.fetchall() or [])
        rows.reverse()
        return [
            {
                "id": row[0],
                "event_type": row[1],
                "payload": row[2] or {},
                "created_at": row[3].isoformat() if row[3] else None,
                "direction_mode": row[4] if len(row) > 4 else SHIFT_AUCTION_MODE_LINE,
            }
            for row in rows
        ]

    def _publish_shift_auction_events(events):
        """Append freshly fetched events to the in-memory buffer and wake waiters once."""
        global shift_auction_event_signal_id, shift_auction_event_buffer_max_id
        if not events:
            return
        with shift_auction_event_condition:
            appended = False
            for event in events:
                try:
                    event_id = int(event.get("id") or 0)
                except Exception:
                    event_id = 0
                if event_id <= shift_auction_event_buffer_max_id:
                    continue
                shift_auction_event_buffer.append(event)
                shift_auction_event_buffer_max_id = event_id
                appended = True
            if appended:
                shift_auction_event_signal_id += 1
                shift_auction_event_condition.notify_all()

    def _read_shift_auction_events_from_buffer(after_id):
        """Return ``(events, covered, signal_id, floor_id)`` after ``after_id``.

    ``covered`` is True when the in-memory buffer is guaranteed to contain the
    full contiguous range after ``after_id`` (so the caller can trust RAM and
    skip the DB). It is False only when the client is further behind than the
    buffer window — then the caller does a one-off DB catch-up.
    """
        after_id = int(after_id or 0)
        with shift_auction_event_condition:
            signal_id = shift_auction_event_signal_id
            if not shift_auction_event_buffer:
                # During listener warm-up, wait for its signal instead of making all
                # newly connected streams hit the shared application DB pool.
                return [], True, signal_id, None
            min_buffered_id = int(shift_auction_event_buffer[0].get("id") or 0)
            covered = (after_id + 1) >= min_buffered_id
            # The buffer is strictly id-ascending, so scan from the tail and stop at
            # the first already-seen id — O(new events) instead of O(buffer length).
            events = []
            for event in reversed(shift_auction_event_buffer):
                if int(event.get("id") or 0) > after_id:
                    events.append(event)
                else:
                    break
            events.reverse()
        return events, covered, signal_id, min_buffered_id

    def _drain_shift_auction_events(cursor):
        """Fetch and broadcast ALL events after the buffer head, paging to exhaustion.

    ``_publish_shift_auction_events`` advances ``shift_auction_event_buffer_max_id``,
    so each iteration re-reads the global and makes monotonic progress. Looping
    until a short page guarantees a large backlog — a reconnect after an outage,
    or a burst of more than ``SHIFT_AUCTION_EVENT_FETCH_LIMIT`` NOTIFYs — is fully
    drained instead of stranding the tail until the next live event arrives.
    """
        while True:
            batch = _fetch_shift_auction_events_with_cursor(cursor, shift_auction_event_buffer_max_id)
            if not batch:
                break
            _publish_shift_auction_events(batch)
            if len(batch) < SHIFT_AUCTION_EVENT_FETCH_LIMIT:
                break

    def _initialize_shift_auction_event_buffer(cursor):
        """Load the recent window once and mark the process-local fan-out ready."""
        global shift_auction_event_buffer_ready, shift_auction_event_signal_id
        _publish_shift_auction_events(_fetch_recent_shift_auction_events_with_cursor(cursor))
        with shift_auction_event_condition:
            shift_auction_event_buffer_ready = True
            # Also wake streams when the table is empty and no event was published.
            shift_auction_event_signal_id += 1
            shift_auction_event_condition.notify_all()

    def _run_shift_auction_pg_listener():
        while True:
            conn = None
            cursor = None
            try:
                conn = psycopg2.connect(**_build_postgres_connection_params())
                conn.set_session(autocommit=True)
                cursor = conn.cursor()
                cursor.execute(f"LISTEN {SHIFT_AUCTION_TEST_EVENT_NOTIFY_CHANNEL}")
                logging.info("Shift auction event listener connected")

                # A new process needs only the recent window. A reconnect keeps its
                # RAM buffer and drains every missed page to the current head.
                if shift_auction_event_buffer_ready:
                    _drain_shift_auction_events(cursor)
                else:
                    _initialize_shift_auction_event_buffer(cursor)

                while True:
                    readable, _, _ = select.select([conn], [], [], SHIFT_AUCTION_EVENT_HEARTBEAT_SECONDS)
                    # Always poll + drain: a NOTIFY can be absorbed into conn.notifies
                    # as a side effect of running the events query below, so we must
                    # not rely on socket readability alone to detect new events.
                    conn.poll()
                    got_notify = False
                    while conn.notifies:
                        conn.notifies.pop(0)
                        got_notify = True
                    if not readable:
                        # Idle heartbeat — keep the connection alive.
                        cursor.execute("SELECT 1")
                        # Executing a query can itself move pending NOTIFYs into
                        # conn.notifies, so poll and drain once more afterwards.
                        conn.poll()
                        while conn.notifies:
                            conn.notifies.pop(0)
                            got_notify = True
                    if got_notify:
                        # Fetch to exhaustion once, regardless of how many NOTIFYs
                        # arrived. This also handles bursts larger than one page.
                        _drain_shift_auction_events(cursor)
            except Exception as error:
                logging.warning("Shift auction event listener reconnecting after error: %s", error, exc_info=True)
                _signal_shift_auction_event_waiters()
                time.sleep(SHIFT_AUCTION_EVENT_LISTENER_RETRY_SECONDS)
            finally:
                try:
                    if cursor is not None:
                        cursor.close()
                except Exception:
                    pass
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass

    def _ensure_shift_auction_event_listener_started():
        global shift_auction_event_listener_started
        if shift_auction_event_listener_started:
            return
        with shift_auction_event_listener_lock:
            if shift_auction_event_listener_started:
                return
            listener_thread = threading.Thread(
                target=_run_shift_auction_pg_listener,
                name="shift-auction-pg-listener",
                daemon=True
            )
            listener_thread.start()
            shift_auction_event_listener_started = True

    def _resolve_shift_auction_direction(requester_id, requester_role, requested=None):
        """Направление аукциона («линия» или «чат») для этого запроса.

    У СВ и выше в разделе есть тумблер, поэтому им параметр направления разрешён.
    У ОПЕРАТОРА направление жёстко своё — из карточки, что бы ни пришло в запросе:
    иначе подменённым параметром можно было бы смотреть и разбирать чужой аукцион.
    """
        role = _normalize_user_role(requester_role)
        if _is_admin_role(role) or _is_supervisor_role(role):
            return normalize_shift_auction_mode(requested)
        try:
            own_mode = db.shift_auction_mode_for_operator(requester_id)
        except Exception:
            own_mode = None
        return own_mode or SHIFT_AUCTION_MODE_LINE

    def _is_shift_auction_manager(requester_id, requester_role) -> bool:
        """Кто может управлять аукционом смен (запуск, контроль, публикация и т.п.).

    Помимо админов — супервайзеры СЗоВ: им открыты те же действия, что и главе
    отдела (admin). Соответствует фронтовому ``canManage`` в ShiftAuctionView.
    """
        role = _normalize_user_role(requester_role)
        if _is_admin_role(role):
            return True
        if _is_supervisor_role(role) and requester_id:
            try:
                _, department_code = db.get_user_department(requester_id)
            except Exception:
                department_code = None
            return str(department_code or '').lower() == SHIFT_AUCTION_MANAGER_DEPARTMENT_CODE
        return False

    def _shift_auction_test_lot_mutation_response(lot_id, action, claim_start_time=None, claim_end_time=None):
        try:
            requester_started_at = time.perf_counter()
            requester_id, requester, auth_error = _get_authenticated_requester()
            _record_elapsed_server_timing("auction-requester", requester_started_at)
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if _normalize_user_role(requester[3]) != 'operator':
                return jsonify({"error": "Only operators can claim shifts"}), 403
            mutation_started_at = time.perf_counter()
            if action == 'release':
                result = db.release_shift_auction_test_lot(requester_id, lot_id)
            else:
                result = db.claim_shift_auction_test_lot(
                    requester_id, lot_id,
                    claim_start_time=claim_start_time,
                    claim_end_time=claim_end_time,
                )
            db_timings = result.pop("_timings", None) if isinstance(result, dict) else None
            _record_elapsed_server_timing("auction-mutation", mutation_started_at)
            if isinstance(db_timings, dict):
                for name, duration_ms in db_timings.items():
                    _append_server_timing(f"auction-{name}", duration_ms)
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction claim API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    def _notify_admins_post_auction_claim(operator_name, lot_payload):
        try:
            recipients = db.get_admin_post_claim_notify_recipients()
        except Exception as error:
            logging.error(f"Failed to load post-auction notify recipients: {error}", exc_info=True)
            return
        if not recipients:
            return

        shift_date_raw = str(lot_payload.get('shift_date') or '').strip()
        try:
            shift_date_obj = datetime.strptime(shift_date_raw, '%Y-%m-%d').date() if shift_date_raw else None
        except Exception:
            shift_date_obj = None
        date_label = shift_date_obj.strftime('%d.%m.%Y') if shift_date_obj else shift_date_raw or '—'
        orig_start = str(lot_payload.get('start_time') or '').strip()
        orig_end = str(lot_payload.get('end_time') or '').strip()
        claim_start = str(lot_payload.get('claim_start_time') or lot_payload.get('post_claim_start_time') or '').strip()
        claim_end = str(lot_payload.get('claim_end_time') or lot_payload.get('post_claim_end_time') or '').strip()
        # Partial добор: operator took only a slice of the original shift window.
        is_partial = bool(
            claim_start and claim_end and orig_start and orig_end
            and (claim_start != orig_start or claim_end != orig_end)
        )
        now_label = datetime.now().strftime('%d.%m.%Y %H:%M')
        operator_label = str(operator_name or '').strip() or 'Оператор'

        if is_partial:
            time_block = (
                f"🕒 Смена: <b>{orig_start}–{orig_end}</b>\n"
                f"✂️ Взял часть: <b>{claim_start}–{claim_end}</b>\n"
            )
        else:
            shown_start = claim_start or orig_start or '—'
            shown_end = claim_end or orig_end or '—'
            time_block = f"🕒 Время: <b>{shown_start}–{shown_end}</b>\n"

        text = (
            "📣 <b>Аукцион смен — дополнительная смена</b>\n\n"
            f"Оператор <b>{operator_label}</b> взял "
            f"{'часть смены (добор)' if is_partial else 'дополнительную смену'}.\n"
            f"📅 Дата: <b>{date_label}</b>\n"
            f"{time_block}"
            f"⏱ Когда взял: {now_label}"
        )
        for recipient in recipients:
            chat_id = recipient.get('telegram_id') if isinstance(recipient, dict) else None
            if not chat_id:
                continue
            try:
                _send_telegram_text_message(int(chat_id), text)
            except Exception as error:
                logging.error(
                    f"Failed to send post-auction claim notification to {chat_id}: {error}",
                    exc_info=True
                )

    @bp.route('/shift_auction/test_access', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_access():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            requester_role = _normalize_user_role(requester[3])
            if request.method == 'GET':
                direction_mode = _resolve_shift_auction_direction(
                    requester_id, requester_role, _requested_shift_auction_direction()
                )
                payload = db.get_shift_auction_test_access(
                    current_user_id=requester_id, direction_mode=direction_mode
                )
                payload["direction_mode"] = direction_mode
                return jsonify({"status": "success", "test_access": payload}), 200

            if not _is_shift_auction_manager(requester_id, requester_role):
                return jsonify({"error": "Only admins and СЗоВ supervisors can manage shift auction test access"}), 403

            payload = request.get_json(silent=True) or {}
            direction_mode = _resolve_shift_auction_direction(
                requester_id, requester_role, _requested_shift_auction_direction(payload)
            )
            updated = db.update_shift_auction_test_access(
                enabled=bool(payload.get('enabled')),
                operator_ids=payload.get('operator_ids') or payload.get('selected_operator_ids') or [],
                launch_note=payload.get('launch_note') or '',
                updated_by=requester_id,
                starts_at=_parse_shift_auction_test_datetime(payload.get('starts_at')),
                ends_at=_parse_shift_auction_test_datetime(payload.get('ends_at')),
                schedule_plan_id=payload.get('schedule_plan_id') or payload.get('selected_schedule_plan_id'),
                time_groups=_parse_shift_auction_time_groups(payload.get('time_groups')),
                direction_mode=direction_mode,
            )
            updated["direction_mode"] = direction_mode
            return jsonify({"status": "success", "test_access": updated}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction test access API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/test_snapshot', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_snapshot():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            requester_role = _normalize_user_role(requester[3])
            is_admin_requester = _is_admin_role(requester_role)
            can_monitor_auction = is_admin_requester or _is_supervisor_role(requester_role)
            direction_mode = _resolve_shift_auction_direction(
                requester_id, requester_role, _requested_shift_auction_direction()
            )
            snapshot = db.get_shift_auction_test_snapshot(
                current_user_id=requester_id,
                include_admin_fields=can_monitor_auction,
                direction_mode=direction_mode
            )
            snapshot["direction_mode"] = direction_mode
            # Тумблер «Линия / Чат» рисуется только тем, кому разрешено переключаться:
            # у оператора направление одно и приходит с сервера.
            snapshot["can_switch_direction"] = bool(can_monitor_auction)
            has_period_history_access = False
            if not (can_monitor_auction or snapshot.get('is_current_user_tester')):
                has_period_history_access = db.has_shift_auction_history_access(requester_id)
            snapshot["has_period_history_access"] = bool(has_period_history_access)
            if not (can_monitor_auction or snapshot.get('is_current_user_tester') or has_period_history_access):
                snapshot["lots"] = []
                snapshot["my_day_offs"] = []
                snapshot["my_blocked_dates"] = []
                snapshot["my_work_shifts"] = []
                snapshot["available_periods"] = []
                snapshot["claim_journal"] = []
                snapshot["participant_workloads"] = []
            elif not can_monitor_auction:
                snapshot["claim_journal"] = []
                snapshot["participant_workloads"] = []
                if has_period_history_access and not snapshot.get('is_current_user_tester'):
                    snapshot["lots"] = []
                    snapshot["my_day_offs"] = []
                    snapshot["my_blocked_dates"] = []
                    snapshot["my_work_shifts"] = []
            snapshot_fingerprint = hashlib.sha1(
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
            ).hexdigest()
            etag = f'W/"shift-auction-{requester_id}-{direction_mode}-{snapshot_fingerprint}"'
            if request.headers.get('If-None-Match', '') == etag:
                response = Response(status=304)
                response.headers['ETag'] = etag
                response.headers['Cache-Control'] = 'no-cache, private'
                return response
            response = jsonify({"status": "success", "snapshot": snapshot})
            response.headers['ETag'] = etag
            response.headers['Cache-Control'] = 'no-cache, private'
            return response, 200
        except Exception as error:
            logging.error(f"Shift auction snapshot API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/lots_for_date', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_lots_for_date():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            requester_role = _normalize_user_role(requester[3])
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role)):
                return jsonify({"error": "Not allowed"}), 403
            date_arg = request.args.get('date')
            if not date_arg:
                return jsonify({"error": "Missing required parameter: date"}), 400
            try:
                target_date = datetime.strptime(date_arg, '%Y-%m-%d').date()
            except Exception:
                return jsonify({"error": "Invalid date format, expected YYYY-MM-DD"}), 400
            payload = db.get_shift_auction_lots_for_planner_date(
                target_date,
                direction_mode=_resolve_shift_auction_direction(
                    requester_id, requester_role, _requested_shift_auction_direction()
                )
            )
            return jsonify({"status": "success", **payload}), 200
        except Exception as error:
            logging.error(f"Shift auction lots_for_date API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/admin/unclaim_shift', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_admin_unclaim_shift():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            requester_role = _normalize_user_role(requester[3])
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role)):
                return jsonify({"error": "Not allowed"}), 403
            payload = request.get_json(silent=True) or {}
            result = db.admin_unclaim_shift(
                admin_id=requester_id,
                lot_id=payload.get('lot_id'),
                plan_id=payload.get('plan_id') or payload.get('schedule_plan_id'),
                source_schedule_shift_id=payload.get('source_schedule_shift_id'),
                claimed_by=payload.get('claimed_by') or payload.get('operator_id'),
            )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"admin_unclaim_shift API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/admin/claim_shift_for_operator', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_admin_claim_for_operator():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            requester_role = _normalize_user_role(requester[3])
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role)):
                return jsonify({"error": "Not allowed"}), 403
            payload = request.get_json(silent=True) or {}
            operator_id = payload.get('operator_id')
            if not operator_id:
                return jsonify({"error": "operator_id is required"}), 400
            result = db.admin_claim_shift_for_operator(
                admin_id=requester_id,
                operator_id=operator_id,
                lot_id=payload.get('lot_id'),
                plan_id=payload.get('plan_id') or payload.get('schedule_plan_id'),
                source_schedule_shift_id=payload.get('source_schedule_shift_id'),
                claim_start_time=payload.get('claim_start_time'),
                claim_end_time=payload.get('claim_end_time'),
            )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"admin_claim_shift_for_operator API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/admin/add_lot', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_admin_add_lot():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            requester_role = _normalize_user_role(requester[3])
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role)):
                return jsonify({"error": "Not allowed"}), 403
            payload = request.get_json(silent=True) or {}
            result = db.admin_add_shift_auction_lot(
                admin_id=requester_id,
                shift_date=payload.get('shift_date') or payload.get('date'),
                start_time=payload.get('start_time'),
                end_time=payload.get('end_time'),
                rate_min=payload.get('rate_min'),
                direction_mode=_resolve_shift_auction_direction(
                    requester_id, requester_role, _requested_shift_auction_direction(payload)
                ),
            )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"admin_add_shift_auction_lot API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/period_preview', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_period_preview():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            requester_role = _normalize_user_role(requester[3])
            can_monitor_auction = _is_admin_role(requester_role) or _is_supervisor_role(requester_role)
            schedule_plan_id = request.args.get('schedule_plan_id') or request.args.get('plan_id')
            if not (can_monitor_auction or db.is_shift_auction_period_participant(requester_id, schedule_plan_id)):
                return jsonify({"error": "Not allowed to view shift auction periods"}), 403
            preview = db.get_shift_auction_period_preview(
                schedule_plan_id=schedule_plan_id,
                current_user_id=requester_id,
                direction_mode=_resolve_shift_auction_direction(
                    requester_id, requester_role, _requested_shift_auction_direction()
                ),
            )
            return jsonify({"status": "success", "preview": preview}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction period preview API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/test_lots/seed', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_lots_seed():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if not _is_admin_role(requester[3]):
                return jsonify({"error": "Only admins can seed test auction lots"}), 403
            payload = request.get_json(silent=True) or {}
            start_date_raw = str(payload.get('start_date') or '').strip()
            start_date = datetime.strptime(start_date_raw, "%Y-%m-%d").date() if start_date_raw else None
            result = db.seed_shift_auction_test_lots(
                updated_by=requester_id,
                start_date=start_date,
                direction_mode=_resolve_shift_auction_direction(
                    requester_id, _normalize_user_role(requester[3]),
                    _requested_shift_auction_direction(payload)
                ),
            )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction seed API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/test_restart', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_restart():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if not _is_shift_auction_manager(requester_id, requester[3]):
                return jsonify({"error": "Only admins and СЗоВ supervisors can restart shift auctions"}), 403
            payload = request.get_json(silent=True) or {}
            result = db.restart_shift_auction_test(
                schedule_plan_id=payload.get('schedule_plan_id') or payload.get('plan_id'),
                updated_by=requester_id,
                direction_mode=_resolve_shift_auction_direction(
                    requester_id, _normalize_user_role(requester[3]),
                    _requested_shift_auction_direction(payload)
                ),
            )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction restart API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/test_control', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_control():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if not _is_shift_auction_manager(requester_id, requester[3]):
                return jsonify({"error": "Only admins and СЗоВ supervisors can control shift auctions"}), 403
            payload = request.get_json(silent=True) or {}
            result = db.control_shift_auction_test(
                action=payload.get('action'),
                updated_by=requester_id,
                direction_mode=_resolve_shift_auction_direction(
                    requester_id, _normalize_user_role(requester[3]),
                    _requested_shift_auction_direction(payload)
                ),
            )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction control API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/test_journal', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_journal():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            requester_role = _normalize_user_role(requester[3])
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role)):
                return jsonify({"error": "Only admins and supervisors can view the auction journal"}), 403
            try:
                page = int(request.args.get('page') or 1)
            except Exception:
                page = 1
            try:
                per_page = int(request.args.get('per_page') or 50)
            except Exception:
                per_page = 50
            result = db.get_shift_auction_test_journal(
                page=page, per_page=per_page,
                direction_mode=_resolve_shift_auction_direction(
                    requester_id, requester_role, _requested_shift_auction_direction()
                ),
            )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction journal API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/test_topup', methods=['POST', 'DELETE', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_topup():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if not _is_shift_auction_manager(requester_id, requester[3]):
                return jsonify({"error": "Only admins and СЗоВ supervisors can toggle shift auction top-up mode"}), 403
            result = db.set_shift_auction_test_topup(
                enabled=(request.method == 'POST'),
                updated_by=requester_id,
                direction_mode=_resolve_shift_auction_direction(
                    requester_id, _normalize_user_role(requester[3]),
                    _requested_shift_auction_direction(request.get_json(silent=True))
                ),
            )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction top-up API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/test_rate_lock', methods=['POST', 'DELETE', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_rate_lock():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if not _is_shift_auction_manager(requester_id, requester[3]):
                return jsonify({"error": "Only admins and СЗоВ supervisors can toggle shift auction rate lock"}), 403
            result = db.set_shift_auction_test_rate_lock(
                enabled=(request.method == 'POST'),
                updated_by=requester_id,
                direction_mode=_resolve_shift_auction_direction(
                    requester_id, _normalize_user_role(requester[3]),
                    _requested_shift_auction_direction(request.get_json(silent=True))
                ),
            )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction rate lock API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/test_export_excel', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_export_excel():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if not _is_shift_auction_manager(requester_id, requester[3]):
                return jsonify({"error": "Only admins and СЗоВ supervisors can export shift auction reports"}), 403

            direction_mode = _resolve_shift_auction_direction(
                requester_id, _normalize_user_role(requester[3]), _requested_shift_auction_direction()
            )
            output, start_date_obj, end_date_obj = db.generate_shift_auction_test_excel_report(
                direction_mode=direction_mode
            )
            direction_slug = 'chat' if direction_mode == SHIFT_AUCTION_MODE_CHAT else 'line'
            filename = (
                f"shift_auction_report_{direction_slug}"
                f"_{start_date_obj.strftime('%Y%m%d')}_{end_date_obj.strftime('%Y%m%d')}.xlsx"
            )
            return send_file(
                output,
                as_attachment=True,
                download_name=filename,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction Excel export API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/test_publish', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_publish():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if not _is_shift_auction_manager(requester_id, requester[3]):
                return jsonify({"error": "Only admins and СЗоВ supervisors can publish shift auctions"}), 403

            result = db.publish_shift_auction_test_to_work_schedules(
                updated_by=requester_id,
                direction_mode=_resolve_shift_auction_direction(
                    requester_id, _normalize_user_role(requester[3]),
                    _requested_shift_auction_direction(request.get_json(silent=True))
                ),
            )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction publish API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/post_claim_lot', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_post_claim_lot():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if _normalize_user_role(requester[3]) != 'operator':
                return jsonify({"error": "Only operators can claim shifts"}), 403

            payload = request.get_json(silent=True) or {}
            source_shift_id = payload.get('source_schedule_shift_id') or payload.get('source_shift_id')
            source_plan_id = payload.get('schedule_plan_id') or payload.get('source_schedule_plan_id')
            claim_start_time = payload.get('claim_start_time') or payload.get('selected_start_time')
            claim_end_time = payload.get('claim_end_time') or payload.get('selected_end_time')
            if source_shift_id and source_plan_id:
                result = db.post_auction_claim_saved_shift(
                    requester_id,
                    int(source_plan_id),
                    int(source_shift_id),
                    claim_start_time=claim_start_time,
                    claim_end_time=claim_end_time
                )
            else:
                try:
                    lot_id = int(payload.get('lot_id') or payload.get('id'))
                except Exception:
                    return jsonify({"error": "Lot id is required"}), 400
                result = db.post_auction_claim_lot(
                    requester_id,
                    lot_id,
                    claim_start_time=claim_start_time,
                    claim_end_time=claim_end_time
                )
            operator_info = result.get('operator') or {}
            operator_name = operator_info.get('name') or requester[1]
            lot_payload = result.get('lot') or {}
            try:
                _notify_admins_post_auction_claim(operator_name, lot_payload)
            except Exception as error:
                logging.error(f"Post-auction claim notification dispatch failed: {error}", exc_info=True)
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Post-auction claim API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/my_post_claims', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_my_post_claims():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if _normalize_user_role(requester[3]) != 'operator':
                return jsonify({"error": "Only operators can view their claimed shifts"}), 403
            claims = db.get_operator_recent_post_auction_claims(requester_id)
            return jsonify({"status": "success", "claims": claims}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"My post-auction claims API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/cancel_post_claim', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_cancel_post_claim():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if _normalize_user_role(requester[3]) != 'operator':
                return jsonify({"error": "Only operators can cancel their claimed shifts"}), 403
            payload = request.get_json(silent=True) or {}
            result = db.operator_cancel_post_auction_claim(
                operator_id=requester_id,
                lot_id=payload.get('lot_id'),
                plan_id=payload.get('plan_id') or payload.get('schedule_plan_id') or payload.get('source_schedule_plan_id'),
                source_schedule_shift_id=payload.get('source_schedule_shift_id') or payload.get('source_shift_id'),
            )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Cancel post-auction claim API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/admin_notify_settings', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_admin_notify_settings():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if not _is_shift_auction_manager(requester_id, requester[3]):
                return jsonify({"error": "Only admins and СЗоВ supervisors can change auction notification settings"}), 403

            if request.method == 'GET':
                enabled = db.get_admin_post_claim_notify_setting(requester_id)
                return jsonify({"status": "success", "auction_post_claim_notify_enabled": bool(enabled)}), 200

            payload = request.get_json(silent=True) or {}
            if 'auction_post_claim_notify_enabled' not in payload:
                return jsonify({"error": "auction_post_claim_notify_enabled is required"}), 400
            raw_value = payload.get('auction_post_claim_notify_enabled')
            if isinstance(raw_value, str):
                enabled_value = raw_value.strip().lower() in ('1', 'true', 'yes', 'on')
            else:
                enabled_value = bool(raw_value)
            saved = db.set_admin_post_claim_notify_setting(requester_id, enabled_value)
            return jsonify({"status": "success", "auction_post_claim_notify_enabled": bool(saved)}), 200
        except Exception as error:
            logging.error(f"Auction admin notify settings API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/test_lots/claim', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_lot_claim_stable():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        payload = request.get_json(silent=True) or {}
        try:
            lot_id = int(payload.get('lot_id') or payload.get('id'))
        except Exception:
            return jsonify({"error": "Lot id is required"}), 400
        action = str(payload.get('action') or 'claim').strip().lower()
        if action not in ('claim', 'release'):
            return jsonify({"error": "Unsupported auction lot action"}), 400
        # Чат разбирает смену частями прямо в ходе аукциона: интервал приходит здесь.
        return _shift_auction_test_lot_mutation_response(
            lot_id,
            action,
            claim_start_time=payload.get('claim_start_time') or payload.get('selected_start_time'),
            claim_end_time=payload.get('claim_end_time') or payload.get('selected_end_time'),
        )

    @bp.route('/shift_auction/test_lots/<int:lot_id>/claim', methods=['POST', 'DELETE', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_lot_claim(lot_id):
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        action = 'release' if request.method == 'DELETE' else 'claim'
        return _shift_auction_test_lot_mutation_response(lot_id, action)

    @bp.route('/shift_auction/self_schedule', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_self_schedule():
        """Operator of a "свой график" group puts a shift on a day themselves."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if _normalize_user_role(requester[3]) != 'operator':
                return jsonify({"error": "Only operators can set their own schedule"}), 403
            payload = request.get_json(silent=True) or {}
            result = db.self_schedule_shift_auction_shift(
                requester_id,
                str(payload.get('shift_date') or payload.get('date') or '').strip(),
                str(payload.get('start_time') or '').strip(),
            )
            result["snapshot"] = db.get_shift_auction_test_snapshot(current_user_id=requester_id)
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction self-schedule API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/test_day_off', methods=['POST', 'DELETE', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_day_off():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if _normalize_user_role(requester[3]) != 'operator':
                return jsonify({"error": "Only operators can choose days off"}), 403
            payload = request.get_json(silent=True) or {}
            day_off_date = str(payload.get('date') or '').strip()
            if not day_off_date:
                return jsonify({"error": "Date is required"}), 400
            result = db.set_shift_auction_test_day_off(
                requester_id,
                day_off_date,
                selected=request.method != 'DELETE'
            )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return _shift_auction_test_error_response(error)
        except Exception as error:
            logging.error(f"Shift auction day off API error: {error}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/shift_auction/test_events', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_shift_auction_test_events():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return jsonify({"error": message}), status_code

        requester_role = _normalize_user_role(requester[3])
        if not (
            _is_admin_role(requester_role)
            or _is_supervisor_role(requester_role)
            or db.is_shift_auction_test_participant(requester_id)
        ):
            return jsonify({"error": "Forbidden"}), 403

        try:
            last_event_id = int(request.args.get('after') or 0)
        except Exception:
            last_event_id = 0

        stream_direction = _resolve_shift_auction_direction(
            requester_id, requester_role, _requested_shift_auction_direction()
        )

        _ensure_shift_auction_event_listener_started()

        # Место занимаем ДО того, как отдан поток: иначе нить waitress уже занята,
        # и считать её поздно. Освобождаем на закрытии ответа — см. call_on_close ниже.
        if not _try_acquire_shift_auction_stream_slot():
            logging.warning(
                "Аукцион: SSE-поток отклонён, занято мест %d из %d",
                _shift_auction_active_stream_count(), SHIFT_AUCTION_STREAM_LIMIT)
            busy = jsonify({"status": "busy"})
            busy.headers['Retry-After'] = '60'
            return busy, 503

        @stream_with_context
        def generate():
            nonlocal last_event_id
            last_signal_id = _get_shift_auction_event_signal_id()
            heartbeat_at = time.time()
            yield f": connected {int(heartbeat_at)}\n\n"
            while True:
                events, covered, last_signal_id, buffer_floor_id = _read_shift_auction_events_from_buffer(last_event_id)
                if not covered:
                    # Client is behind the in-memory window (fresh connect or long
                    # disconnect). One-off DB catch-up, then resume from RAM.
                    events = db.get_shift_auction_test_events_after(last_event_id, limit=SHIFT_AUCTION_EVENT_FETCH_LIMIT)
                    if not events and buffer_floor_id is not None:
                        # Old rows may have been pruned. Advance across the deleted
                        # gap so this stream can resume from the in-memory window.
                        last_event_id = max(last_event_id, int(buffer_floor_id) - 1)
                        continue
                if events:
                    # Курсор двигаем по ВСЕМ событиям, а отдаём только свои: иначе поток
                    # застревал бы на чужом событии и перечитывал его без конца.
                    for event in events:
                        last_event_id = max(last_event_id, int(event.get('id') or 0))
                        if normalize_shift_auction_mode(event.get('direction_mode')) != stream_direction:
                            continue
                        yield f"id: {event['id']}\n"
                        yield f"event: {event['event_type']}\n"
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    heartbeat_at = time.time()
                    continue

                elapsed = time.time() - heartbeat_at
                wait_seconds = max(0.1, SHIFT_AUCTION_EVENT_HEARTBEAT_SECONDS - elapsed)
                last_signal_id = _wait_for_shift_auction_event_signal(last_signal_id, wait_seconds)

                if time.time() - heartbeat_at >= SHIFT_AUCTION_EVENT_HEARTBEAT_SECONDS:
                    yield f": heartbeat {int(time.time())}\n\n"
                    heartbeat_at = time.time()

        response = Response(generate(), mimetype='text/event-stream')
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['X-Accel-Buffering'] = 'no'
        # Единственное место, которое отдаёт слот обратно. Срабатывает и на штатном
        # закрытии, и на обрыве соединения — без него счётчик уполз бы вверх навсегда
        # и через несколько суток раздел перестал бы принимать кого-либо.
        response.call_on_close(_release_shift_auction_stream_slot)
        return response

    return bp
