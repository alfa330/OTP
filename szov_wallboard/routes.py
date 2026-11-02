"""Раздел «szov_wallboard»: ручки /api/szov_wallboard — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_szov_wallboard_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, Response, g, jsonify, request, send_file
from concurrent.futures import ThreadPoolExecutor
from database import db
from datetime import date as dt_date
from datetime import datetime
from datetime import timedelta
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Alignment
from openpyxl.styles import Border
from openpyxl.styles import Font
from openpyxl.styles import PatternFill
from openpyxl.styles import Side
from openpyxl.utils import get_column_letter
from zoneinfo import ZoneInfo
import asyncio
import functools
import logging
import math
import os
import re
import requests


def _szov_broadcast_is_personal_only():
    """Запрос пропущен гардом отбивки только ради личной подписки — групп ему не показываем."""
    return bool(getattr(g, 'szov_broadcast_personal_only', False))

SZOV_BROADCAST_PERSONAL_ONLY_ERROR = "Вам открыта только отбивка лично себе — группы настраивает руководитель отдела"

SZOV_BROADCAST_MODE_ALWAYS = 'always'

_TEZ_BROADCAST_PREVIEW_IMAGES = {'tp_day': 'tez_tp_day.png', 'tp_hour': 'tez_tp_hour.png',
                                  'op': 'tez_op.png'}

_SZOV_CHAT_EXPORT_STATUS_FILL = {
    'online': 'DBEAFE',
    'busy': 'EDE9FE',
    'training': 'D1FAE5',
    'break': 'FFEDD5',
    'tech': 'FFEDD5',
    # «Закрытие чатов» — янтарный, рядом с перерывом, и это не случайность: смысл у них общий
    # («в системе, новых чатов не берёт»), а различает их слово в самой ячейке. Серым, как
    # отсутствие, он был подписан по ошибке — вместе с неверной подписью «Отпуск».
    'holiday': 'FEF3C7',
    'offline': 'F1F5F9',
}

def _szov_chat_export_minutes(seconds, digits=1):
    """Секунды -> минуты числом, в тех же единицах, что на табло. None остаётся None.

    Пропуск от нуля отличаем намеренно: «отвечать было нечего» и «ответили мгновенно» — разные
    вещи, а ноль вместо пропуска молча улучшил бы любое среднее, которое посчитают в Excel.
    Число пишем числом, а не строкой «1,8 мин»: файл берут, чтобы считать по нему дальше."""
    if seconds is None or isinstance(seconds, bool):
        return None
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return round(value / 60.0, digits)

def _szov_chat_export_hour_label(hour):
    """Час — ПРОМЕЖУТОК, а не момент: подписываем «12:00–13:00», как почасовой отчёт в Telegram."""
    return '%02d:00–%02d:00' % (int(hour) % 24, (int(hour) + 1) % 24)

def _szov_chat_export_parse_day(value):
    """«2026-08-18» -> date. None, если строка не разобралась."""
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], '%Y-%m-%d').date()
    except ValueError:
        return None

def _szov_chat_export_day_people(hourly):
    """{ФИО: секунды на линии за день} по почасовым строкам.

    Атом счёта один на всех уровнях — «человек часа» (минуту и больше в этом часу): и день, и
    период складываются из тех же людей, что стоят в подсказке графика. Свой порог на уровне
    дня дал бы в одном файле две правды — сумма по часам не сходилась бы с днём."""
    people = {}
    for row in hourly or []:
        for item in (row.get('operators') or []):
            name = str((item or {}).get('name') or '').strip()
            if not name:
                continue
            people[name] = people.get(name, 0) + int((item or {}).get('seconds') or 0)
    return people

def _szov_chat_export_finish_day(day_str, hourly, totals, *, events_truncated=False,
                                 unmatched_names=(), error=None):
    """День выгрузки одной формы, откуда бы он ни пришёл — из снимка табло или из Chat2Desk."""
    hourly = [row for row in (hourly or []) if isinstance(row, dict)]
    totals = totals or {}
    return {
        'day': day_str,
        'hourly': hourly,
        'chats': int(totals.get('chats') or 0),
        'chats_open': (None if totals.get('chats_open') is None else int(totals['chats_open'])),
        'first_reply_seconds': totals.get('first_reply_seconds'),
        'inner_reply_seconds': totals.get('inner_reply_seconds'),
        'reply_sums': totals.get('reply_sums') or {},
        'people': _szov_chat_export_day_people(hourly),
        'online_seconds': sum(int(row.get('online_seconds') or 0) for row in hourly),
        'peak_online': max((int(row.get('operators_online') or 0) for row in hourly), default=0),
        'events_truncated': bool(events_truncated),
        'unmatched_names': sorted({str(name).strip() for name in (unmatched_names or [])
                                   if str(name or '').strip()}),
        'error': error,
    }

def _szov_chat_export_today_block(snapshot):
    """Сегодня — из снимка табло: те же цифры, что на экране, и без запросов к Chat2Desk."""
    snapshot = snapshot or {}
    today = snapshot.get('today') or {}
    diagnostics = snapshot.get('diagnostics') or {}
    return _szov_chat_export_finish_day(
        snapshot.get('day') or '', snapshot.get('hourly') or [], {
            'chats': today.get('chats'),
            'chats_open': today.get('chats_open'),
            'first_reply_seconds': today.get('first_reply_seconds'),
            'inner_reply_seconds': today.get('inner_reply_seconds'),
            'reply_sums': today.get('reply_sums'),
        },
        events_truncated=diagnostics.get('events_truncated'),
        unmatched_names=diagnostics.get('unmatched_names') or [])

def _szov_chat_export_file_name(payload):
    """Имя файла: одна дата на однодневной выгрузке, обе — на периоде.

    У сегодняшнего дня в имени стоит ещё и момент данных: две выгрузки подряд в пределах кэша
    содержат один и тот же снимок и различаться именами не должны."""
    payload = payload or {}
    day_from = str(payload.get('from') or '').replace('-', '')
    day_to = str(payload.get('to') or '').replace('-', '')
    if day_from and day_to and day_from != day_to:
        return 'szov_wallboard_chat_%s_%s.xlsx' % (day_from, day_to)
    clock = str(payload.get('chat2desk_now') or '')[11:16].replace(':', '')
    stamp = '_'.join(part for part in (day_from or day_to, clock) if part) or 'now'
    return 'szov_wallboard_chat_%s.xlsx' % stamp


def build_szov_wallboard_blueprint(
    *,
    ALLOWED_ORIGINS,
    CHAT2DESK_API_BASE_URL,
    CHAT2DESK_API_MAX_PAGES,
    CHAT2DESK_API_PAGE_LIMIT,
    CHAT2DESK_API_TIMEOUT_SECONDS,
    CHAT2DESK_API_TOKEN,
    CHAT2DESK_AUTH_SCHEME,
    CHAT2DESK_STATISTICS_REPORT_OPERATOR_EVENTS,
    CHAT2DESK_STATISTICS_REPORT_REQUEST_STATS,
    CHAT_HOURLY_REQUEST_TYPE,
    CHAT_HOURLY_TIMEZONE,
    OKTELL_API_TOKEN,
    OKTELL_API_URL,
    OPERATOR_NAME_VENDOR_MARK_RE,
    OP_BROADCAST_LIVE_STALE_SECONDS,
    OP_BROADCAST_MIN_CALLS,
    OP_BROADCAST_SEND_TIMES,
    OP_BROADCAST_SL_MIN_PERCENT,
    OP_BROADCAST_TIMEZONE,
    OP_CHAT_BROADCAST_MIN_CHATS,
    OP_CHAT_BROADCAST_PERSONAL_ONLY_USER_IDS,
    OP_CHAT_BROADCAST_SEND_TIMES,
    ROLE_HIERARCHY,
    SZOV_AR_MAX_PERCENT,
    SZOV_AR_MIN_PERCENT,
    SZOV_BREAK_DIRECTION_CHAT,
    SZOV_BREAK_DIRECTION_LINE,
    SZOV_BREAK_KIND_NOT_PLANNED,
    SZOV_BREAK_KIND_NO_SHIFT,
    SZOV_BREAK_MIN_MINUTES,
    SZOV_BREAK_NOTE_LIMIT,
    SZOV_BREAK_TOLERANCE_MINUTES,
    SZOV_BROADCAST_DIRECTION_CHAT,
    SZOV_BROADCAST_DIRECTION_LINE,
    SZOV_BROADCAST_DIRECTION_OP,
    SZOV_BROADCAST_DIRECTION_OP_CHAT,
    SZOV_BROADCAST_DIRECTION_TEZ,
    SZOV_BROADCAST_HOUR_MIN_CALLS,
    SZOV_BROADCAST_SEND_TIMES,
    SZOV_BROADCAST_TIMEZONE,
    SZOV_CHAT_BROADCAST_MIN_CHATS,
    SZOV_CHAT_BROADCAST_SEND_TIMES,
    SZOV_CHAT_WALLBOARD_EVENT_MAX_PAGES,
    SZOV_CHAT_WALLBOARD_ONLINE_MIN_SECONDS,
    SZOV_CHAT_WALLBOARD_TARGET_SECONDS,
    SZOV_SL_MIN_PERCENT,
    TEZ_AR_TARGET_PERCENT,
    TEZ_BROADCAST_HOUR_MIN_CALLS,
    TEZ_BROADCAST_SEND_TIMES,
    _KZ_TO_RU_FOLD,
    _OP_BROADCAST_TABLE_ROWS,
    _SZOV_BROADCAST_HOURLY,
    _SZOV_CHAT_WALLBOARD_OTHER_KEY,
    _SZOV_CHAT_WALLBOARD_STATUSES,
    _SZOV_FONT_CANDIDATES,
    _SZOV_SHIFT_COLUMN_KEYS,
    _SZOV_TABLE_COLUMNS,
    _bot_event_loop,
    _build_cors_preflight_response,
    _chat2desk_api_base_url,
    _chat2desk_api_error_message,
    _chat2desk_api_token,
    _chat2desk_authorization_header,
    _chat2desk_extract_response_rows,
    _chat2desk_extract_total,
    _chat2desk_row_first,
    _chat_hourly_fetch_requests,
    _chat_hourly_is_open,
    _chat_hourly_number,
    _chat_hourly_operator_name,
    _chat_hourly_request_start,
    _chat_hourly_requests_cache,
    _chat_hourly_response_sums,
    _chat_hourly_response_times,
    _env_int,
    _get_authenticated_requester,
    _get_role_level,
    _has_min_role,
    _headed_department_id,
    _is_admin_role,
    _is_allowed_origin,
    _is_global_admin_requester,
    _is_supervisor_role,
    _normalize_origin,
    _normalize_user_role,
    _oktell_api_ready,
    _op_broadcast_attach_period,
    _op_broadcast_bridge_note,
    _op_broadcast_caption,
    _op_broadcast_collect,
    _op_broadcast_deviations,
    _op_broadcast_duration,
    _op_broadcast_group_view,
    _op_broadcast_key_tiles,
    _op_broadcast_percent,
    _op_broadcast_period_notes,
    _op_broadcast_prepare,
    _op_broadcast_scope,
    _op_broadcast_send_times,
    _op_broadcast_table,
    _op_broadcast_text,
    _op_broadcast_wait,
    _op_chat_broadcast_collect,
    _op_chat_broadcast_deviations,
    _op_chat_broadcast_duration,
    _op_chat_broadcast_notes,
    _op_chat_broadcast_prepare,
    _op_chat_broadcast_send_times,
    _op_chat_broadcast_stream_note,
    _op_chat_broadcast_text,
    _op_render_chat_wallboard_png,
    _op_render_hour_png,
    _op_render_wallboard_png,
    _operator_name_strip_vendor_mark,
    _parse_boolean_setting,
    _status_import_dedupe_operator_infos,
    _status_import_normalize_operator_name,
    _status_import_resolve_operator_matches,
    _szov_break_violation_detail,
    _szov_break_violation_notes,
    _szov_broadcast_ar_notes,
    _szov_broadcast_collect,
    _szov_broadcast_deliver,
    _szov_broadcast_deviations,
    _szov_broadcast_hour_notes,
    _szov_broadcast_notes,
    _szov_broadcast_parse_times,
    _szov_broadcast_send,
    _szov_broadcast_send_times,
    _szov_broadcast_shift_rows,
    _szov_broadcast_sl_note,
    _szov_broadcast_stale_note,
    _szov_broadcast_text,
    _szov_chat_broadcast_collect,
    _szov_chat_broadcast_deviations,
    _szov_chat_broadcast_duration,
    _szov_chat_broadcast_notes,
    _szov_chat_broadcast_prepare,
    _szov_chat_broadcast_send_times,
    _szov_chat_broadcast_text,
    _szov_chat_wallboard_day_seconds,
    _szov_chat_wallboard_display_names,
    _szov_chat_wallboard_events_cache,
    _szov_chat_wallboard_fetch_events,
    _szov_chat_wallboard_hourly,
    _szov_chat_wallboard_online_seconds,
    _szov_chat_wallboard_operator_lookup,
    _szov_chat_wallboard_reply_average,
    _szov_chat_wallboard_reply_sums,
    _szov_chat_wallboard_resolve,
    _szov_chat_wallboard_snapshot,
    _szov_chat_wallboard_status,
    _szov_chat_wallboard_timelines,
    _szov_font,
    _szov_font_paths,
    _szov_font_paths_cache,
    _szov_format_age_ru,
    _szov_format_percent,
    _szov_format_seconds_mmss,
    _szov_plural,
    _szov_render_chat_wallboard_png,
    _szov_render_hour_png,
    _szov_render_hourly_table_png,
    _szov_render_tiles_png,
    _szov_render_wallboard_png,
    _szov_wallboard_department_id,
    _szov_wallboard_int,
    _szov_wallboard_snapshot,
    _tez_broadcast_deviations,
    _tez_broadcast_prepare,
    _tez_broadcast_send_times,
    _wallboard_format_asa,
    executor_pool,
    monolith_namespace,
    require_api_key,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами.

    monolith_namespace — словарь глобалов монолита (его globals()). Резолвер отдела и снимок
    «Табло ОП» монолит присваивает ниже строки регистрации этого пакета и только если табло
    поднялось, поэтому их ищут по имени в момент запроса — как до выноса искали в globals()
    монолита. globals() в этом модуле — уже пространство имён пакета, там их нет."""
    bp = Blueprint('szov_wallboard', __name__, url_prefix='/api')

    def _szov_wallboard_guard():
        """(requester_id, err) для раздела «Табло СЗоВ».

    Доступ: глобальные админы, глава отдела СЗоВ и СВ отдела СЗоВ. Границу отдела держим
    строго, как в _chatapp_guard: главе/СВ чужого отдела нагрузка линии СЗоВ не нужна."""
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return None, (jsonify({"error": message}), status_code)
        role = _normalize_user_role(requester[3])
        if _is_global_admin_requester(role, requester_id):
            return requester_id, None
        department_id = _szov_wallboard_department_id()
        if department_id is not None:
            if _headed_department_id(requester_id) == department_id:
                return requester_id, None
            if _is_supervisor_role(role) and db.get_user_department_id(requester_id) == department_id:
                return requester_id, None
        return requester_id, (jsonify({"error": "forbidden"}), 403)

    def _szov_broadcast_guard():
        """(requester_id, err) для НАСТРОЙКИ отбивки табло — она строже, чем само табло.

    Табло СВ отдела видит, а вот кому и когда уходят уведомления руководству, решает
    только руководитель: глобальные админы и глава отдела СЗоВ. Ту же границу держит
    canManageSzovBroadcastForUser на фронте."""
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return None, (jsonify({"error": message}), status_code)
        role = _normalize_user_role(requester[3])
        if _is_global_admin_requester(role, requester_id):
            return requester_id, None
        # Направление решает, чей глава здесь хозяин: отбивку «Табло ОП» настраивает глава
        # отдела продаж, а не СЗоВ. Неизвестное направление гейт не отвергает сам — это
        # сделает ручка честным 400; здесь оно просто считается «Линией».
        direction = SZOV_BROADCAST_DIRECTION_LINE
        try:
            direction = _szov_broadcast_direction_arg(
                request.get_json(silent=True) if request.method == 'POST' else None)
        except ValueError:
            pass
        if direction in (SZOV_BROADCAST_DIRECTION_OP, SZOV_BROADCAST_DIRECTION_OP_CHAT):
            resolver = monolith_namespace.get('_op_wallboard_department_id')
            department_id = resolver() if resolver else None
        elif direction == SZOV_BROADCAST_DIRECTION_TEZ:
            resolver = monolith_namespace.get('_tez_wallboard_department_id')
            department_id = resolver() if resolver else None
        else:
            department_id = _szov_wallboard_department_id()
        if department_id is not None and _headed_department_id(requester_id) == department_id:
            return requester_id, None
        # Поимённый доступ «только лично себе» (см. OP_CHAT_BROADCAST_PERSONAL_ONLY_USER_IDS). Пометка
        # в g — ручки по ней отказывают во всём, что касается групп, и не показывают их вовсе.
        if (direction == SZOV_BROADCAST_DIRECTION_OP_CHAT
                and int(requester_id) in OP_CHAT_BROADCAST_PERSONAL_ONLY_USER_IDS):
            g.szov_broadcast_personal_only = True
            return requester_id, None
        return requester_id, (jsonify({"error": "forbidden"}), 403)

    SZOV_BROADCAST_DIRECTIONS = (SZOV_BROADCAST_DIRECTION_LINE, SZOV_BROADCAST_DIRECTION_CHAT,
                                 SZOV_BROADCAST_DIRECTION_OP, SZOV_BROADCAST_DIRECTION_TEZ,
                                 SZOV_BROADCAST_DIRECTION_OP_CHAT)

    SZOV_CHAT_EXPORT_MAX_DAYS = _env_int('SZOV_CHAT_EXPORT_MAX_DAYS', 7, minimum=1, maximum=92)

    SZOV_CHAT_EXPORT_WORKERS = _env_int('SZOV_CHAT_EXPORT_WORKERS', 4, minimum=1, maximum=8)

    async def _tez_broadcast_send(chat_id):
        """Собрать и отправить отбивку в один чат — кнопка проверочной отправки.

    Пишет и в спокойный час (картинки есть всегда), а нарушения перерывов прочитанными НЕ
    помечает: проверка связи не должна отменять плановое предупреждение."""
        data, text, media = await _tez_broadcast_prepare(retry_missing_tp=False)
        await _szov_broadcast_deliver(chat_id, text, media)
        return data

    def _tez_broadcast_preview():
        """Предпросмотр отбивки Тез КЦ: подпись и картинки, ничего не отправляя.

    Сбор идёт на цикле бота, как у проверочной отправки: ожидание журнала в нём асинхронное.
    ?image=tp_day|tp_hour|op отдаёт картинку, без параметра — подпись и отклонения."""
        loop = _bot_event_loop()
        if loop is None:
            return jsonify({"error": "Бот не запущен"}), 503
        try:
            data, text, media = asyncio.run_coroutine_threadsafe(
                _tez_broadcast_prepare(retry_missing_tp=False), loop).result(timeout=180)
        except Exception as exc:
            logging.error("Предпросмотр отбивки (Тез КЦ): данные не собрались: %s", exc)
            return jsonify({"error": "Не удалось собрать показатели", "detail": str(exc)[:300]}), 502
        images = dict(media)
        which = (request.args.get('image') or '').strip()
        if which:
            blob = images.get(_TEZ_BROADCAST_PREVIEW_IMAGES.get(which, ''))
            if blob is None:
                return jsonify({"error": "Картинки нет", "images": sorted(images)}), 404
            response = Response(blob, mimetype='image/png')
            response.headers['Cache-Control'] = 'no-store'
            return response
        regular, _bold = _szov_font_paths()
        return jsonify({
            "text": text,
            "deviations": _tez_broadcast_deviations(data),
            "hour": data.get('hour_label'),
            "tp_hour": data.get('tp_hour'),
            "images": sorted(images),
            "font_path": regular,
        })

    def _szov_chat_export_period(date_from, date_to, today):
        """Дни периода по возрастанию. ValueError — если период не разобрался или слишком велик.

    Период не выбрали — выгружаем сегодня: это самый частый случай (глянул на табло и забрал
    цифры), и он не стоит ни одного запроса к Chat2Desk."""
        start = _szov_chat_export_parse_day(date_from)
        end = _szov_chat_export_parse_day(date_to)
        if date_from and start is None:
            raise ValueError('Начало периода должно быть в формате ГГГГ-ММ-ДД')
        if date_to and end is None:
            raise ValueError('Конец периода должен быть в формате ГГГГ-ММ-ДД')
        if start is None and end is None:
            start = end = today
        start = start or end
        end = end or start
        if end < start:
            start, end = end, start
        if start > today:
            raise ValueError('Период начинается в будущем — выгружать нечего')
        # Хвост в будущем просто отрезаем: Chat2Desk отдал бы пустые сутки, а в файле они читались
        # бы как день полного простоя.
        if end > today:
            end = today
        length = (end - start).days + 1
        if length > SZOV_CHAT_EXPORT_MAX_DAYS:
            # «суток», а не «дней»: число подставляется, а «больше 31 дней» — не по-русски.
            raise ValueError('Период больше %d суток: Chat2Desk качается по дню на день'
                             % SZOV_CHAT_EXPORT_MAX_DAYS)
        return [(start + timedelta(days=offset)).strftime('%Y-%m-%d') for offset in range(length)]

    def _szov_chat_export_past_block(day_str, lookup):
        """Прошедший день из Chat2Desk. Сутки закрыты, поэтому часы считаются все 24 и целиком."""
        events_cache = {'day': None, 'rows': {}, 'newest': None, 'truncated': False}
        events = _szov_chat_wallboard_fetch_events(day_str, cache=events_cache)
        request_rows = _chat_hourly_fetch_requests(day_str, cache={'day': None, 'rows': {}})
        timelines, unmatched = _szov_chat_wallboard_timelines(
            events, lookup, truncated=bool(events_cache.get('truncated')))
        first_sum, first_count, inner_sum, inner_count = _chat_hourly_response_sums(request_rows)
        hourly = _szov_chat_wallboard_hourly(
            # now здесь не при чём: сутки закрыты, границу часов задаёт full_day.
            request_rows, timelines, None, full_day=True,
            names=_szov_chat_wallboard_display_names(
                set(timelines) | set(_szov_chat_wallboard_reply_sums(request_rows)), lookup))
        return _szov_chat_export_finish_day(day_str, hourly, {
            'chats': len(request_rows),
            # «Открыто сейчас» у закрытых суток смысла не имеет (обращение либо давно закрыто, либо
            # висит открытым неделю) — оставляем пустым, а не нулём.
            'chats_open': None,
            'first_reply_seconds': (first_sum / first_count) if first_count else None,
            'inner_reply_seconds': (inner_sum / inner_count) if inner_count else None,
            'reply_sums': {'first_sum': first_sum, 'first_count': first_count,
                           'inner_sum': inner_sum, 'inner_count': inner_count},
        }, events_truncated=bool(events_cache.get('truncated')), unmatched_names=unmatched)

    def _szov_chat_export_collect(date_from=None, date_to=None):
        """Данные выгрузки: дни периода, а состав смены — только если в период входит сегодня."""
        now = datetime.now(ZoneInfo(CHAT_HOURLY_TIMEZONE))
        today_str = now.strftime('%Y-%m-%d')
        days = _szov_chat_export_period(date_from, date_to, now.date())
        snapshot = _szov_chat_wallboard_snapshot() if today_str in days else None
        blocks = {}
        if snapshot is not None:
            blocks[today_str] = _szov_chat_export_today_block(snapshot)

        past_days = [day for day in days if day != today_str]
        if past_days:
            # Справочник сотрудников достаём ОДИН раз и в этом потоке: он ходит в БД, а потоки
            # выгрузки к Flask-контексту доступа не имеют.
            lookup, _department_id = _szov_chat_wallboard_operator_lookup()

            def _one(day_str):
                try:
                    return day_str, _szov_chat_export_past_block(day_str, lookup)
                except Exception as exc:
                    # Один день не дался — остальные всё равно нужны: день уходит в файл строкой с
                    # причиной, а не молча нулями, которые прочитаются как простой смены.
                    logging.warning("Табло СЗоВ (чат): день %s не выгрузился: %s", day_str, exc)
                    return day_str, _szov_chat_export_finish_day(day_str, [], {},
                                                                 error=str(exc)[:200])

            workers = max(1, min(SZOV_CHAT_EXPORT_WORKERS, len(past_days)))
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix='szov-chat-export') as pool:
                for day_str, block in pool.map(_one, past_days):
                    blocks[day_str] = block

        return {
            'from': days[0],
            'to': days[-1],
            'today': today_str,
            'days': [blocks[day] for day in days if day in blocks],
            'target_seconds': SZOV_CHAT_WALLBOARD_TARGET_SECONDS,
            'now': (snapshot or {}).get('now'),
            'chat2desk_now': (snapshot or {}).get('chat2desk_now'),
            'stale': bool((snapshot or {}).get('stale')),
            'age_seconds': (snapshot or {}).get('age_seconds'),
            'error': (snapshot or {}).get('error'),
            'generated_at': now.strftime('%Y-%m-%d %H:%M'),
        }

    def _szov_chat_wallboard_workbook(payload):
        """Показатели табло по чатам в .xlsx (bytes).

    Листы: сводка, дни (если период больше суток), часы, два разреза «оператор × час» по
    времени ответа и состав смены (если в период входит сегодня). Один день или неделя —
    раскладка одна: у выгрузки не должно быть двух разных лиц."""
        payload = payload or {}
        days = [day for day in (payload.get('days') or []) if isinstance(day, dict)]
        now_block = payload.get('now') or {}
        target_seconds = int(payload.get('target_seconds') or 0) or SZOV_CHAT_WALLBOARD_TARGET_SECONDS
        target_minutes = _szov_chat_export_minutes(target_seconds)
        multi = len(days) > 1

        thin = Side(style='thin', color='E2E8F0')
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        header_fill = PatternFill('solid', fgColor='1F2937')
        total_fill = PatternFill('solid', fgColor='F1F5F9')
        header_font = Font(bold=True, color='FFFFFF', size=11)
        title_font = Font(bold=True, size=15, color='0F172A')
        group_font = Font(bold=True, size=12, color='0F172A')
        label_font = Font(bold=True, color='334155')
        hint_font = Font(color='94A3B8', size=10)
        note_font = Font(color='B45309')
        total_font = Font(bold=True, color='0F172A')
        good_font = Font(bold=True, color='15803D')
        bad_font = Font(bold=True, color='B91C1C')
        center = Alignment(horizontal='center', vertical='center')
        left = Alignment(vertical='center')
        wrap = Alignment(vertical='top', wrap_text=True)

        def _int(value):
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        def _sum_key(key):
            return sum(float((day.get('reply_sums') or {}).get(key) or 0) for day in days)

        # Средние за период — по ОБРАЩЕНИЯМ всех дней, а не среднее средних по дням.
        first_count, inner_count = int(_sum_key('first_count')), int(_sum_key('inner_count'))
        period_first = (_sum_key('first_sum') / first_count) if first_count else None
        period_inner = (_sum_key('inner_sum') / inner_count) if inner_count else None
        period_inner_minutes = _szov_chat_export_minutes(period_inner)
        period_inner_font = None
        if period_inner is not None:
            period_inner_font = good_font if period_inner <= target_seconds else bad_font

        hour_rows = [(day.get('day') or '', row) for day in days for row in (day.get('hourly') or [])]
        chats_total = sum(_int(day.get('chats')) for day in days)
        online_seconds_total = sum(_int(day.get('online_seconds')) for day in days)
        peak_online = max((_int(day.get('peak_online')) for day in days), default=0)

        # Разные люди на линии за период: из них считается «Людей на линии» в сводке.
        people_seconds = {}
        for day in days:
            for name, seconds in (day.get('people') or {}).items():
                people_seconds[name] = people_seconds.get(name, 0) + _int(seconds)

        # Разрез «оператор × час» под два листа времени ответа. Складываем СУММЫ, а не средние:
        # клетка периода — это один и тот же час РАЗНЫХ дней, и среднее средних разошлось бы и с
        # листом «По часам», и со сводкой. Ровно та же причина, по которой у дней есть reply_sums.
        reply_matrix, reply_totals, reply_hour_chats = {}, {}, {}
        for _day, row in hour_rows:
            hour = _int(row.get('hour'))
            reply_hour_chats[hour] = reply_hour_chats.get(hour, 0) + _int(row.get('chats'))
            for item in (row.get('operators_reply') or []):
                name = str((item or {}).get('name') or '').strip()
                if not name:
                    continue
                # Клетка часа и итог оператора копятся одним проходом: разойтись им нельзя.
                for bucket in (reply_matrix.setdefault(name, {}).setdefault(hour, {}),
                               reply_totals.setdefault(name, {})):
                    for key in ('chats', 'first_count', 'inner_count'):
                        bucket[key] = _int(bucket.get(key)) + _int(item.get(key))
                    for key in ('first_sum', 'inner_sum'):
                        bucket[key] = float(bucket.get(key) or 0.0) + float(item.get(key) or 0.0)
        # Колонки — только часы, в которые чаты вообще приходили. Ночная смена принимает единицы
        # обращений, и восемь заведомо пустых колонок из 24 читаются хуже, чем те же данные без них.
        reply_hours = sorted(hour for hour, chats in reply_hour_chats.items() if chats > 0)
        reply_order = sorted(reply_totals,
                             key=lambda name: (-_int(reply_totals[name].get('chats')), name))
        # Чаты, которые никто не взял (бот закрыл сам): в разрез по людям они не идут, и разница
        # между итогом дня и суммой колонки «Чатов» должна быть подписана, а не оставлена загадкой.
        unassigned_chats = chats_total - sum(_int(sums.get('chats')) for sums in reply_totals.values())

        wb = Workbook()

        # ── Лист 1: сводка ───────────────────────────────────────────────────────────────────────
        ws = wb.active
        ws.title = 'Показатели'
        ws.column_dimensions['A'].width = 34
        # B пошире прежнего: колонки пояснений больше нет, и строки «Внимание» живут теперь в ней.
        # Ширину C не задаём намеренно — пустая соседка даёт длинной оговорке перелиться вправо,
        # вместо того чтобы обрезаться по границе.
        ws.column_dimensions['B'].width = 26
        ws['A1'] = 'Табло СЗоВ · чаты'
        ws['A1'].font = title_font

        period_from, period_to = payload.get('from') or '', payload.get('to') or ''
        head_rows = [
            ('Период' if multi else 'Дата',
             f'{period_from} — {period_to}' if multi else (period_from or period_to)),
        ]
        if payload.get('chat2desk_now'):
            head_rows.append(('Данные Chat2Desk на', payload['chat2desk_now']))
        head_rows.append(('Цель по ответу внутри чата, мин', target_minutes))
        head_rows.append(('Выгружено', payload.get('generated_at') or ''))

        row = 3
        for label, value in head_rows:
            ws.cell(row=row, column=1, value=label).font = label_font
            ws.cell(row=row, column=2, value=value).alignment = left
            row += 1

        # Оговорки о качестве цифр стоят В ФАЙЛЕ, а не только на экране: файл уходит в переписку и
        # живёт своей жизнью, а замерший снимок или обрезанные события меняют смысл этих чисел.
        notes = []
        if payload.get('stale'):
            notes.append('Снимок за сегодня замер: данным %d мин (%s)'
                         % (max(0, _int(payload.get('age_seconds'))) // 60,
                            payload.get('error') or 'источник не отвечает'))
        failed = [day for day in days if day.get('error')]
        for day in failed:
            notes.append('День %s не выгрузился: %s' % (day.get('day'), day.get('error')))
        truncated = [day.get('day') for day in days if day.get('events_truncated')]
        if truncated:
            notes.append('События Chat2Desk обрезаны по лимиту страниц (%s): людей на линии в ранних '
                         'часах этих дней может быть меньше, чем было' % ', '.join(truncated))
        unmatched = sorted({name for day in days for name in (day.get('unmatched_names') or [])})
        if unmatched:
            notes.append('Имена Chat2Desk без сотрудника в OTP (в счётчики не вошли): '
                         + ', '.join(unmatched))
        for text in notes:
            ws.cell(row=row, column=1, value='Внимание').font = label_font
            ws.cell(row=row, column=2, value=text).font = note_font
            row += 1

        def _block(title, entries):
            """Блок «шапка + показатель/значение». Колонки с пояснениями нет по решению владельца
        20.08.2026: подписи показателей говорят сами за себя, а колонка текста рядом с числами
        только удлиняла лист."""
            nonlocal row
            row += 1
            ws.cell(row=row, column=1, value=title).font = group_font
            row += 1
            for column, name in ((1, 'Показатель'), (2, 'Значение')):
                cell = ws.cell(row=row, column=column, value=name)
                cell.fill = header_fill
                cell.font = header_font
                cell.border = border
                cell.alignment = center if column == 2 else left
            row += 1
            for label, value, font in entries:
                ws.cell(row=row, column=1, value=label).border = border
                value_cell = ws.cell(row=row, column=2, value=value)
                value_cell.border = border
                value_cell.alignment = center
                if font is not None:
                    value_cell.font = font
                row += 1

        # Состав смены «сейчас» есть только у сегодняшнего дня — и блок, и лист появляются вместе.
        if now_block:
            # Счётчика «В отпуске» здесь нет: отпуска в Chat2Desk не существует вовсе (его статус
            # `holiday` — это «Закрытие чатов»), и отдельной строкой он не нужен — решение владельца
            # 20.08.2026. Сами люди из этого статуса никуда не деваются: они стоят на листе
            # «Чатники» со своим статусом, как и все остальные.
            _block('Чатники · сейчас', [
                ('Онлайн', _int(now_block.get('operators_online')), None),
                ('Занят', _int(now_block.get('operators_busy')), None),
                ('Тренинг', _int(now_block.get('operators_on_training')), None),
                ('На перерыве', _int(now_block.get('operators_on_break')), None),
                ('В прочих статусах', _int(now_block.get('operators_other')), None),
                ('Не в системе', _int(now_block.get('operators_offline')), None),
                ('Открыто чатов', _int(now_block.get('open_chats')), None),
            ])

        period_rows = [
            ('Чатов', chats_total, None),
        ]
        if len(days) == 1 and days[0].get('chats_open') is not None:
            period_rows.append(('Открыто сейчас', _int(days[0].get('chats_open')), None))
        period_rows.extend([
            ('Ответ внутри чата, мин', period_inner_minutes, period_inner_font),
            ('Первый ответ, мин', _szov_chat_export_minutes(period_first), None),
            ('Людей на линии', len(people_seconds), None),
            ('Максимум людей в часе', peak_online, None),
            ('На линии, мин', _szov_chat_export_minutes(online_seconds_total, 0), None),
        ])
        if multi:
            period_rows.append(('Дней в периоде', len(days), None))
        _block('Показатели за период' if multi else 'Показатели за день', period_rows)

        def _sheet_head(sheet, headers, widths):
            for index, title in enumerate(headers, start=1):
                cell = sheet.cell(row=1, column=index, value=title)
                cell.fill = header_fill
                cell.font = header_font
                cell.border = border
                cell.alignment = center if index > 1 else left
            for index, width in enumerate(widths, start=1):
                sheet.column_dimensions[get_column_letter(index)].width = width
            sheet.freeze_panes = 'A2'

        # ── Лист «По дням» ───────────────────────────────────────────────────────────────────────
        # Только когда дней больше одного: на однодневной выгрузке этот лист повторял бы сводку.
        if multi:
            ws_days = wb.create_sheet('По дням')
            headers = ['День', 'Чатов', 'Ответ внутри чата, мин', 'Первый ответ, мин',
                       'Людей на линии', 'Максимум в часе', 'На линии, мин', 'Примечание']
            _sheet_head(ws_days, headers, [13, 10, 22, 20, 16, 17, 15, 34])
            for offset, day in enumerate(days):
                inner = day.get('inner_reply_seconds')
                note = []
                if day.get('error'):
                    note.append('не выгрузился: %s' % day['error'])
                if day.get('events_truncated'):
                    note.append('события обрезаны')
                values = [
                    day.get('day') or '',
                    _int(day.get('chats')),
                    _szov_chat_export_minutes(inner),
                    _szov_chat_export_minutes(day.get('first_reply_seconds')),
                    len(day.get('people') or {}),
                    _int(day.get('peak_online')),
                    _szov_chat_export_minutes(day.get('online_seconds'), 0),
                    ' · '.join(note),
                ]
                for index, value in enumerate(values, start=1):
                    cell = ws_days.cell(row=2 + offset, column=index, value=value)
                    cell.border = border
                    cell.alignment = center if 1 < index < len(headers) else left
                    if index == 3 and inner is not None:
                        cell.font = good_font if float(inner) <= target_seconds else bad_font
            ws_days.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(headers)), len(days) + 1)

        # ── Лист «По часам» ──────────────────────────────────────────────────────────────────────
        ws_hours = wb.create_sheet('По часам')
        # Колонки «В цели» (да/нет) здесь нет по решению владельца 20.08.2026 — она повторяла
        # словами то, что уже видно цветом самой минуты, и добавляла столбец на ровном месте.
        # Сама цель никуда не делась: по ней по-прежнему красится «Ответ внутри чата».
        headers = ['Час', 'Чатов', 'Ответ внутри чата, мин', 'Первый ответ, мин',
                   'Людей на линии', 'На линии, мин', 'Кто на линии', 'Примечание']
        widths = [15, 10, 22, 20, 16, 15, 62, 26]
        if multi:
            headers.insert(0, 'День')
            widths.insert(0, 13)
        _sheet_head(ws_hours, headers, widths)
        # Колонки ищем по подписи, а не по номеру: у периода слева добавляется «День», и любой
        # арифметикой по индексам красились бы соседние столбцы.
        names_column = headers.index('Кто на линии') + 1
        inner_column = headers.index('Ответ внутри чата, мин') + 1
        for offset, (day_str, hour_row) in enumerate(hour_rows):
            excel_row = 2 + offset
            inner_seconds = hour_row.get('inner_reply_seconds')
            in_target = None if inner_seconds is None else float(inner_seconds) <= target_seconds
            under = _int(hour_row.get('operators_under_minute'))
            note = []
            if hour_row.get('partial'):
                note.append('час идёт')
            if under:
                note.append('ещё %d меньше минуты' % under)
            values = [
                _szov_chat_export_hour_label(hour_row.get('hour')),
                _int(hour_row.get('chats')),
                _szov_chat_export_minutes(inner_seconds),
                _szov_chat_export_minutes(hour_row.get('first_reply_seconds')),
                # Людей считаем головами (решение владельца 19.08.2026): кто был на линии минуту и
                # больше — один человек, дробей в этой колонке нет.
                _int(hour_row.get('operators_online')),
                _szov_chat_export_minutes(hour_row.get('online_seconds'), 0),
                ', '.join(str((item or {}).get('name') or '').strip()
                          for item in (hour_row.get('operators') or [])),
                ' · '.join(note),
            ]
            if multi:
                values.insert(0, day_str)
            for index, value in enumerate(values, start=1):
                cell = ws_hours.cell(row=excel_row, column=index, value=value)
                cell.border = border
                if index == names_column:
                    cell.alignment = wrap
                else:
                    cell.alignment = center if index > (2 if multi else 1) else left
                if in_target is not None and index == inner_column:
                    cell.font = good_font if in_target else bad_font

        last_hour_row = max(1, len(hour_rows) + 1)
        totals = [
            'За период' if multi else 'За сутки',
            chats_total,
            period_inner_minutes,
            _szov_chat_export_minutes(period_first),
            # В итоге по людям стоит максимум в часе, а не сумма голов: складывать головы разных
            # часов нельзя — один и тот же человек попал бы в итог столько раз, сколько отработал.
            peak_online,
            _szov_chat_export_minutes(online_seconds_total, 0),
            'разных людей: %d' % len(people_seconds),
            '',
        ]
        if multi:
            totals.insert(0, '%s — %s' % (period_from, period_to))
        total_row = last_hour_row + 1
        for index, value in enumerate(totals, start=1):
            cell = ws_hours.cell(row=total_row, column=index, value=value)
            cell.border = border
            cell.fill = total_fill
            cell.font = total_font
            cell.alignment = center if index > (2 if multi else 1) else left
        # Фильтр — только по часам: итоговая строка под ним и в отбор попадать не должна.
        ws_hours.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(headers)), last_hour_row)

        # ── Листы «оператор × час» ───────────────────────────────────────────────────────────────
        # Два листа одной раскладки: строка — чатник, колонка — час, в клетке минуты. Первый ответ
        # и ответ внутри чата разведены по разным листам намеренно: это разные показатели, и в
        # одной таблице их пришлось бы либо чередовать колонками (48 столбцов), либо ставить парами
        # в клетку — и то и другое не читается и не считается формулами.
        # Прежнего листа «Люди на линии» здесь больше нет (решение владельца 20.08.2026): минуты на
        # линии видны в «По часам» и в сводке, а времени ответа по людям не было нигде.
        def _reply_sheet(title, kind, *, against_target=False):
            """Лист времени ответа `kind` ('first' | 'inner') в разрезе «оператор × час»."""
            sheet = wb.create_sheet(title)
            headers = ['Сотрудник', 'Чатов', 'Среднее, мин'] + [
                _szov_chat_export_hour_label(hour) for hour in reply_hours]
            _sheet_head(sheet, headers, [34, 10, 15] + [13] * len(reply_hours))

            def _put(excel_row, column, sums, *, bold=False):
                """Клетка со средним. Пусто — «в этот час не отвечал», а не «ответил мгновенно»."""
                seconds = _szov_chat_wallboard_reply_average(sums, kind)
                cell = sheet.cell(row=excel_row, column=column,
                                  value=_szov_chat_export_minutes(seconds))
                cell.border = border
                cell.alignment = center
                if bold:
                    cell.font = total_font
                # Цветом красим только ответ внутри чата: цель задана ему одному, а красить
                # первый ответ той же меркой значило бы придумать ему норму, которой нет.
                if against_target and seconds is not None:
                    cell.font = good_font if seconds <= target_seconds else bad_font
                return cell

            for offset, name in enumerate(reply_order):
                excel_row = 2 + offset
                totals_sums = reply_totals.get(name) or {}
                for index, value in enumerate((name, _int(totals_sums.get('chats'))), start=1):
                    cell = sheet.cell(row=excel_row, column=index, value=value)
                    cell.border = border
                    cell.alignment = center if index > 1 else left
                _put(excel_row, 3, totals_sums)
                hours = reply_matrix.get(name) or {}
                for index, hour in enumerate(reply_hours, start=4):
                    _put(excel_row, index, hours.get(hour))

            # Итоговая строка — сумма колонки, а не отдельный расчёт: обращения без оператора
            # времени ответа не несут вовсе, поэтому эти средние сходятся с листом «По часам»
            # и со сводкой до цифры.
            total_row = len(reply_order) + 2
            total_chats = sum(_int((reply_totals.get(name) or {}).get('chats')) for name in reply_order)
            for index, value in enumerate(('Все чатники', total_chats), start=1):
                cell = sheet.cell(row=total_row, column=index, value=value)
                cell.border = border
                cell.fill = total_fill
                cell.font = total_font
                cell.alignment = center if index > 1 else left

            def _column_sums(hour=None):
                """Сумма клеток колонки по всем чатникам: час периода или весь период целиком."""
                merged = {}
                for name in reply_order:
                    sums = (reply_matrix.get(name) or {}).get(hour) if hour is not None \
                        else reply_totals.get(name)
                    for key, value in (sums or {}).items():
                        merged[key] = (merged.get(key) or 0) + value
                return merged

            _put(total_row, 3, _column_sums(), bold=True).fill = total_fill
            for index, hour in enumerate(reply_hours, start=4):
                _put(total_row, index, _column_sums(hour), bold=True).fill = total_fill
            # Фильтр — только по чатникам: итоговая строка под ним и в отбор попадать не должна.
            sheet.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(headers)),
                                                 max(1, len(reply_order) + 1))
            sheet.freeze_panes = 'D2'  # ФИО, чаты и среднее держим на виду, часы уезжают вбок
            if not reply_order:
                sheet.cell(row=total_row + 1, column=1,
                           value='За период никто не отвечал в чатах').font = hint_font
            elif unassigned_chats > 0:
                # Только цифра, без объяснений (решение владельца 20.08.2026): разницу между итогом
                # дня и суммой колонки «Чатов» подписать надо, а растолковывать — нет.
                # Число ставим ПОСЛЕ слова: «131 чатов» по-русски не сходится ни в одном падеже.
                sheet.cell(row=total_row + 2, column=1,
                           value='Чатов без оператора: %d' % unassigned_chats).font = hint_font

        _reply_sheet('Первый ответ по часам', 'first')
        _reply_sheet('Ответ внутри чата по часам', 'inner', against_target=True)

        # ── Лист «Чатники» (состав смены сейчас) ─────────────────────────────────────────────────
        if now_block:
            ws_shift = wb.create_sheet('Чатники')
            # Лист повторяет правую колонку табло, и повторяет целиком: занятость без качества
            # читается неверно — «7 в работе» у отвечающего за минуту и у держащего клиента десять
            # это разные семь чатов. Время ответа тут за СУТКИ, а не «сейчас», как и на стене.
            headers = ['Сотрудник', 'Статус', 'В статусе, мин', 'Чатов в работе', 'Чатов за сутки',
                       'Первый ответ, мин', 'Ответ внутри чата, мин']
            _sheet_head(ws_shift, headers, [34, 18, 16, 16, 16, 20, 22])
            people = [item for item in (now_block.get('operators') or []) if isinstance(item, dict)]
            for offset, item in enumerate(people):
                # seconds=None — событий за сутки не было: человек в этом статусе с начала суток, и
                # выдавать это числом нельзя, иначе «0 мин» прочитается как «только что зашёл».
                since = item.get('seconds')
                inner_seconds = item.get('inner_reply_seconds')
                values = [
                    item.get('name') or '',
                    item.get('status') or '',
                    'с начала суток' if since is None else _szov_chat_export_minutes(since),
                    _int(item.get('open_chats')),
                    _int(item.get('chats')),
                    _szov_chat_export_minutes(item.get('first_reply_seconds')),
                    _szov_chat_export_minutes(inner_seconds),
                ]
                for index, value in enumerate(values, start=1):
                    cell = ws_shift.cell(row=2 + offset, column=index, value=value)
                    cell.border = border
                    cell.alignment = center if index > 1 else left
                    if index == 2:
                        fill = _SZOV_CHAT_EXPORT_STATUS_FILL.get(item.get('status_key'))
                        if fill:
                            cell.fill = PatternFill('solid', fgColor=fill)
                    if index == len(headers) and inner_seconds is not None:
                        cell.font = good_font if float(inner_seconds) <= target_seconds else bad_font
            ws_shift.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(headers)),
                                                    max(1, len(people) + 1))
            # Вышедшие в список смены не идут (на табло они на своей вкладке «Не в системе»), но
            # их число видно — иначе смена в файле выглядит меньше, чем она есть.
            offline = _int(now_block.get('operators_offline'))
            if offline:
                note = ws_shift.cell(row=len(people) + 3, column=1,
                                     value='Не в системе: %d' % offline)
                note.font = hint_font

        buffer = BytesIO()
        wb.save(buffer)
        return buffer.getvalue()

    async def _szov_chat_broadcast_send(chat_id):
        """Собрать и отправить отбивку чатов в один чат — кнопка «Отправить сейчас»."""
        data, text, media = await _szov_chat_broadcast_prepare()
        await _szov_broadcast_deliver(chat_id, text, media)
        return data

    def _op_broadcast_group_options():
        """[{id, label}] групп табло ОП для выбора в настройках отбивки. Табло недоступно — пусто:
    настройки получателей от этого не ломаются, просто выбрать можно только весь отдел."""
        provider = monolith_namespace.get('_op_wallboard_snapshot')
        if provider is None:
            return []
        try:
            return [{'id': group['id'], 'label': group['label']}
                    for group in (provider().get('groups') or [])]
        except Exception as exc:  # noqa: BLE001
            logging.warning("Отбивка табло ОП: группы для настроек не собрались: %s", exc)
            return []

    def _op_broadcast_preview():
        """Предпросмотр отбивки ОП: текст и картинка, ничего не отправляя. Снимок — из кэша
    табло, так что предпросмотр не стоит ни одного запроса к мосту или станции."""
        try:
            # ?group_id= — предпросмотр отбивки одной группы, ровно как её получит чат группы.
            raw_group = str(request.args.get('group_id') or '').strip()
            data = _op_broadcast_collect(group_id=int(raw_group) if raw_group.isdigit() else None)
        except Exception as exc:
            logging.error("Предпросмотр отбивки (ОП): данные не собрались: %s", exc)
            return jsonify({"error": "Не удалось собрать показатели", "detail": str(exc)[:300]}), 502
        renderers = {'board': _op_render_wallboard_png, 'hour': _op_render_hour_png}
        renderer = renderers.get((request.args.get('image') or '').strip())
        if renderer is not None:
            try:
                blob = renderer(data)
            except Exception as exc:
                return jsonify({"error": "Не удалось нарисовать картинку", "detail": str(exc)[:300]}), 500
            response = Response(blob, mimetype='image/png')
            response.headers['Cache-Control'] = 'no-store'
            return response
        regular, _bold = _szov_font_paths()
        return jsonify({
            # Ровно то, что уйдёт: с картинками — подпись из одних отклонений, без них — полный текст.
            "text": _op_broadcast_caption(data) if regular else _op_broadcast_text(data),
            "font_path": regular,
            "images_available": bool(regular),
            "deviations": _op_broadcast_deviations(data),
            "hour": data.get('hour_label'),
        })

    async def _op_broadcast_send(chat_id):
        """Собрать и отправить отбивку ОП в один чат — кнопка «Отправить сейчас». Группа — из
    настроек этого чата: «Отправить сейчас» обязана прислать то же, что придёт по расписанию."""
        loop = asyncio.get_event_loop()
        chats = await loop.run_in_executor(
            executor_pool, functools.partial(db.get_szov_broadcast_chats, SZOV_BROADCAST_DIRECTION_OP))
        group_id = next((chat.get('group_id') for chat in chats
                         if str(chat.get('chat_id')) == str(chat_id)), None)
        data, text, media = await _op_broadcast_prepare(group_id=group_id)
        await _szov_broadcast_deliver(chat_id, text, media)
        return data

    def _op_chat_broadcast_preview():
        """Предпросмотр отбивки «Чата» ОП: текст и картинка, ничего не отправляя."""
        try:
            data = _op_chat_broadcast_collect()
        except Exception as exc:
            logging.error("Предпросмотр отбивки (ОП, чат): данные не собрались: %s", exc)
            return jsonify({"error": "Не удалось собрать показатели", "detail": str(exc)[:300]}), 502
        if (request.args.get('image') or '').strip() == 'board':
            try:
                blob = _op_render_chat_wallboard_png(data)
            except Exception as exc:
                return jsonify({"error": "Не удалось нарисовать картинку", "detail": str(exc)[:300]}), 500
            response = Response(blob, mimetype='image/png')
            response.headers['Cache-Control'] = 'no-store'
            return response
        regular, _bold = _szov_font_paths()
        return jsonify({
            "text": _op_chat_broadcast_text(data),
            "font_path": regular,
            "images_available": bool(regular),
            "deviations": _op_chat_broadcast_deviations(data),
        })

    async def _op_chat_broadcast_send(chat_id):
        """Собрать и отправить отбивку «Чата» ОП в один чат — кнопка «Отправить сейчас»."""
        data, text, media = await _op_chat_broadcast_prepare()
        await _szov_broadcast_deliver(chat_id, text, media)
        return data

    def _szov_broadcast_direction_arg(payload=None):
        """Направление отбивки из запроса. Пусто = «Линия».

    Пустое значение допускаем намеренно: фронт едет отдельным деплоем (GitHub Pages против
    Render), и бандл, ничего не знающий о втором направлении, обязан продолжать настраивать
    «Линию», а не получать 400."""
        raw = str(request.args.get('direction') or (payload or {}).get('direction') or '').strip()
        direction = raw or SZOV_BROADCAST_DIRECTION_LINE
        if direction not in SZOV_BROADCAST_DIRECTIONS:
            raise ValueError("Неизвестное направление табло")
        return direction

    def _szov_broadcast_direction_times(direction):
        """Расписание направления — то, что форма показывает подписью под заголовком."""
        if direction == SZOV_BROADCAST_DIRECTION_CHAT:
            times = _szov_chat_broadcast_send_times()
        elif direction == SZOV_BROADCAST_DIRECTION_OP:
            times = _op_broadcast_send_times()
        elif direction == SZOV_BROADCAST_DIRECTION_TEZ:
            times = _tez_broadcast_send_times()
        elif direction == SZOV_BROADCAST_DIRECTION_OP_CHAT:
            times = _op_chat_broadcast_send_times()
        else:
            times = _szov_broadcast_send_times()
        return [f"{hour:02d}:{minute:02d}" for hour, minute in times]

    SZOV_BROADCAST_PERSONAL_DIRECTIONS = (SZOV_BROADCAST_DIRECTION_TEZ, SZOV_BROADCAST_DIRECTION_OP_CHAT)

    def _szov_broadcast_personal_owner(direction):
        """Учётка запросившего, если он вправе получать отбивку направления лично; иначе None.

    Лично себе отбивку получают у «Тез КЦ» и у «Табло ОП · Чат», и только админы и
    супер-админы, у которых этот раздел отображается (постановка владельца 23.09.2026):
    супер-админ, админ без отдела и админ — глава отдела табло. Админу — главе чужого отдела
    табло не видно, и гейт отбивки (_szov_broadcast_guard) его сюда уже не пустил; здесь
    остаётся отсечь роль ниже админа — форму открывает любой глава отдела, но лично себе
    получает админ."""
        if direction not in SZOV_BROADCAST_PERSONAL_DIRECTIONS:
            return None
        _requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error or not requester:
            return None
        if _is_admin_role(requester[3]):
            return requester
        # Поимённый доступ «только лично» к отбивке «Чата» ОП — роль там ниже админа намеренно.
        if (direction == SZOV_BROADCAST_DIRECTION_OP_CHAT
                and int(requester[0]) in OP_CHAT_BROADCAST_PERSONAL_ONLY_USER_IDS):
            return requester
        return None

    def _szov_broadcast_personal_state(direction):
        """Строка «лично мне» для формы отбивки. None — строки в форме нет вовсе."""
        requester = _szov_broadcast_personal_owner(direction)
        if requester is None:
            return None
        state = db.get_tez_broadcast_personal(requester[0], direction=direction)
        return {
            "enabled": bool(state.get('enabled')),
            "mode": state.get('mode') or SZOV_BROADCAST_MODE_ALWAYS,
            # Без Telegram бот не знает, куда писать: форма гасит переключатель и объясняет.
            "telegram_connected": bool(requester[1]),
        }

    def _szov_chat_broadcast_preview():
        """Предпросмотр отбивки чатов: текст и картинка, ничего не отправляя.

    Нужен там же, где предпросмотр «Линии»: посмотреть вид перед включением и убедиться,
    что на сервере нашёлся шрифт с кириллицей. Снимок берётся из кэша табло, поэтому
    предпросмотр не тратит квоту Chat2Desk."""
        try:
            data = _szov_chat_broadcast_collect()
        except Exception as exc:
            logging.error("Предпросмотр отбивки (чат): данные не собрались: %s", exc)
            return jsonify({"error": "Не удалось собрать показатели", "detail": str(exc)[:300]}), 502

        if (request.args.get('image') or '').strip() == 'board':
            try:
                blob = _szov_render_chat_wallboard_png(data)
            except Exception as exc:
                return jsonify({"error": "Не удалось нарисовать картинку", "detail": str(exc)[:300]}), 500
            response = Response(blob, mimetype='image/png')
            response.headers['Cache-Control'] = 'no-store'
            return response

        regular, _bold = _szov_font_paths()
        return jsonify({
            "text": _szov_chat_broadcast_text(data),
            "font_path": regular,
            "images_available": bool(regular),
        })

    @bp.route('/szov_wallboard/snapshot', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_szov_wallboard_snapshot():
        """Онлайн-табло входящей линии СЗоВ: очередь и статусы «на сейчас» + итоги за день."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, err = _szov_wallboard_guard()
        if err:
            return err
        if not _oktell_api_ready():
            return jsonify({"error": "Интеграция с Oktell недоступна: OKTELL_IP/OKTELL_API_TOKEN не задан"}), 503
        try:
            return jsonify(_szov_wallboard_snapshot())
        except Exception as exc:
            logging.error("Табло СЗоВ: не удалось получить данные Oktell: %s", exc)
            return jsonify({"error": "Не удалось получить данные Oktell", "detail": str(exc)[:300]}), 502

    @bp.route('/szov_wallboard/break_violations', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_szov_wallboard_break_violations():
        """Журнал выходов на перерыв мимо графика за период (задача #114).

    Только чтение: строки пишет почасовой разбор отбивки, к Oktell эта ручка не ходит.
    Доступ тот же, что у самого табло: нарушения смотрят те же СВ и руководители."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, err = _szov_wallboard_guard()
        if err:
            return err
        today = datetime.now(ZoneInfo(SZOV_BROADCAST_TIMEZONE)).strftime('%Y-%m-%d')
        date_from = (request.args.get('date_from') or '').strip() or today
        date_to = (request.args.get('date_to') or '').strip() or date_from
        if date_to < date_from:
            date_from, date_to = date_to, date_from
        direction = (request.args.get('direction') or '').strip().lower()
        if direction not in (SZOV_BREAK_DIRECTION_LINE, SZOV_BREAK_DIRECTION_CHAT):
            direction = SZOV_BREAK_DIRECTION_LINE
        try:
            violations = db.get_szov_break_violations(date_from, date_to, direction=direction)
            # Дни, когда человек работал, а смены в графике у него не было: сверять было не с чем.
            # Без этой строки пустой журнал читался бы как «нарушений нет», хотя проверка просто
            # не работала — по чату график сейчас почти не заполняют.
            unscheduled = db.get_szov_break_unscheduled_days(direction, date_from, date_to)
        except Exception as exc:
            logging.error("Перерывы вне графика: журнал не прочитан: %s", exc)
            return jsonify({"error": "Не удалось получить журнал", "detail": str(exc)[:300]}), 500
        return jsonify({
            "date_from": date_from,
            "date_to": date_to,
            "direction": direction,
            "violations": violations,
            "unscheduled": {
                "days": len(unscheduled),
                "operators_max": max([item['operators'] for item in unscheduled] or [0]),
                "by_day": unscheduled[:31],
            },
            # Порог и допуск показываем рядом со списком: без них непонятно, почему перерыв
            # в 10 минут от плана в журнал не попал.
            "rules": {
                "min_minutes": SZOV_BREAK_MIN_MINUTES,
                "tolerance_minutes": SZOV_BREAK_TOLERANCE_MINUTES,
            },
        })

    @bp.route('/szov_wallboard/chat_snapshot', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_szov_wallboard_chat_snapshot():
        """Табло СЗоВ, направление «Чат»: статусы чатников сейчас + почасовой срез за сутки."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, err = _szov_wallboard_guard()
        if err:
            return err
        if not _chat2desk_authorization_header():
            return jsonify({"error": "Интеграция с Chat2Desk недоступна: CHAT2DESK_API_TOKEN не задан"}), 503
        try:
            return jsonify(_szov_chat_wallboard_snapshot())
        except Exception as exc:
            logging.error("Табло СЗоВ (чат): снимок недоступен: %s", exc, exc_info=True)
            return jsonify({"error": "Chat2Desk недоступен", "detail": str(exc)[:300]}), 503

    @bp.route('/szov_wallboard/chat_export', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_szov_wallboard_chat_export():
        """Табло СЗоВ, направление «Чат»: показатели за период файлом .xlsx."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, err = _szov_wallboard_guard()
        if err:
            return err
        if not _chat2desk_authorization_header():
            return jsonify({"error": "Интеграция с Chat2Desk недоступна: CHAT2DESK_API_TOKEN не задан"}), 503
        try:
            payload = _szov_chat_export_collect(request.args.get('date_from'),
                                                request.args.get('date_to'))
        except ValueError as exc:
            # Ошибка периода — вина запроса, а не источника: отвечаем 400 и человеческим текстом,
            # который фронт покажет тостом как есть.
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            logging.error("Табло СЗоВ (чат): выгрузка не собралась: %s", exc, exc_info=True)
            return jsonify({"error": "Не удалось собрать выгрузку", "detail": str(exc)[:300]}), 503
        try:
            content = _szov_chat_wallboard_workbook(payload)
        except Exception as exc:
            logging.error("Табло СЗоВ (чат): файл выгрузки не собрался: %s", exc, exc_info=True)
            return jsonify({"error": "Не удалось собрать файл", "detail": str(exc)[:300]}), 500
        return send_file(
            BytesIO(content),
            as_attachment=True,
            download_name=_szov_chat_export_file_name(payload),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @bp.route('/szov_wallboard/broadcast', methods=['GET', 'POST', 'DELETE', 'OPTIONS'])
    @require_api_key
    def api_szov_wallboard_broadcast():
        """Получатели отбивки: кому слать, в каком режиме, история изменений и чаты бота.

    POST добавляет получателя или меняет его режим / включённость (чат — ключ, дублей не
    заводим), DELETE убирает из списка. Всё — в пределах одного направления: тот же чат в
    «Линии» и в «Чате» это две независимые строки.

    POST {personal: true|false, personal_mode: 'always'|'deviations'} — личная отбивка самому
    себе (есть у «Тез КЦ» и «Табло ОП · Чат»), любое из полей по отдельности. В историю «кто менял» она не
    пишется: это настройка человека о себе, а не общий список получателей."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, err = _szov_broadcast_guard()
        if err:
            return err
        payload = request.get_json(silent=True) or {} if request.method in ('POST', 'DELETE') else {}
        try:
            direction = _szov_broadcast_direction_arg(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        personal_only = _szov_broadcast_is_personal_only()
        if personal_only and (request.method == 'DELETE' or (
                request.method == 'POST' and not ('personal' in payload or 'personal_mode' in payload))):
            return jsonify({"error": SZOV_BROADCAST_PERSONAL_ONLY_ERROR}), 403
        if request.method in ('POST', 'DELETE'):
            try:
                if request.method == 'DELETE':
                    db.delete_szov_broadcast_chat(
                        payload.get('chat_id') or request.args.get('chat_id'),
                        user_id=requester_id, direction=direction)
                elif 'personal' in payload or 'personal_mode' in payload:
                    owner = _szov_broadcast_personal_owner(direction)
                    if owner is None:
                        return jsonify({"error": "Лично себе отбивку этого табло получают только админы"}), 403
                    enabled = None
                    if 'personal' in payload:
                        enabled, flag_error = _parse_boolean_setting(payload.get('personal'), 'personal')
                        if flag_error:
                            return jsonify({"error": "Некорректное значение personal"}), 400
                    if enabled and not owner[1]:
                        return jsonify({"error": "К учётной записи не привязан Telegram — отправлять некуда"}), 400
                    saved = db.set_tez_broadcast_personal(requester_id, enabled=enabled,
                                                          mode=payload.get('personal_mode'),
                                                          direction=direction)
                    logging.info("Отбивка %s: личная подписка у %s -> %s", direction, requester_id, saved)
                else:
                    db.save_szov_broadcast_chat(payload, user_id=requester_id, direction=direction)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            except Exception as exc:
                logging.error("Отбивка табло: не удалось сохранить настройку: %s", exc)
                return jsonify({"error": "Не удалось сохранить настройку"}), 500
        if personal_only:
            # Групп такой человек не настраивает — и не видит: ни списка, ни чатов бота, ни истории.
            return jsonify({
                "direction": direction,
                "groups_allowed": False,
                "recipients": [], "history": [], "chats": [], "groups": [],
                "send_times": _szov_broadcast_direction_times(direction),
                "personal": _szov_broadcast_personal_state(direction),
            })
        return jsonify({
            "direction": direction,
            "groups_allowed": True,
            "recipients": db.get_szov_broadcast_chats(direction),
            "history": db.get_szov_broadcast_history(direction=direction),
            "chats": db.list_bot_group_chats(),
            "send_times": _szov_broadcast_direction_times(direction),
            "personal": _szov_broadcast_personal_state(direction),
            # Группы, по которым можно получать отбивку (только у табло ОП): те же подписи и
            # тот же порядок, что у фильтра групп на стене.
            "groups": _op_broadcast_group_options() if direction == SZOV_BROADCAST_DIRECTION_OP else [],
        })

    @bp.route('/szov_wallboard/broadcast_preview', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_szov_wallboard_broadcast_preview():
        """Показать, как будет выглядеть отбивка, НИЧЕГО не отправляя.

    ?image=table|board|hour отдаёт картинку, без параметра — текст и диагностику шрифта.
    ?direction=chat — то же для направления «Чат» (у него одна картинка, board).
    Нужен и владельцу (посмотреть перед включением), и на проде — убедиться, что на
    сервере вообще нашёлся шрифт с кириллицей."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, err = _szov_broadcast_guard()
        if err:
            return err
        try:
            direction = _szov_broadcast_direction_arg()
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if direction == SZOV_BROADCAST_DIRECTION_CHAT:
            return _szov_chat_broadcast_preview()
        if direction == SZOV_BROADCAST_DIRECTION_OP:
            return _op_broadcast_preview()
        if direction == SZOV_BROADCAST_DIRECTION_TEZ:
            return _tez_broadcast_preview()
        if direction == SZOV_BROADCAST_DIRECTION_OP_CHAT:
            return _op_chat_broadcast_preview()
        hour_raw = (request.args.get('hour') or '').strip()
        hour_to = int(hour_raw) if hour_raw.isdigit() and 0 <= int(hour_raw) <= 23 else None

        # Только смены: Oktell не трогаем. Нужно, чтобы сверить план/факт с разделом даже
        # когда прокси Oktell недоступен (а он периодически отваливается с Render).
        if (request.args.get('shifts_only') or '').strip() in ('1', 'true', 'yes'):
            day_iso = (request.args.get('date') or '').strip() \
                or datetime.now(ZoneInfo(SZOV_BROADCAST_TIMEZONE)).strftime('%Y-%m-%d')
            try:
                rows = _szov_broadcast_shift_rows(day_iso)
            except Exception as exc:
                return jsonify({"error": "Не удалось посчитать смены", "detail": str(exc)[:300]}), 500
            return jsonify({"date": day_iso, "shifts": [
                {"hour": hour, **(rows.get(hour) or {})} for hour in sorted(rows)
            ]})

        try:
            data = _szov_broadcast_collect(hour_to)
        except Exception as exc:
            logging.error("Предпросмотр отбивки: данные не собрались: %s", exc)
            return jsonify({"error": "Не удалось собрать показатели", "detail": str(exc)[:300]}), 502

        which = (request.args.get('image') or '').strip()
        renderers = {'table': lambda d: _szov_render_hourly_table_png(d['hourly']),
                     'board': _szov_render_wallboard_png, 'hour': _szov_render_hour_png}
        if which in renderers:
            try:
                blob = renderers[which](data)
            except Exception as exc:
                return jsonify({"error": "Не удалось нарисовать картинку", "detail": str(exc)[:300]}), 500
            response = Response(blob, mimetype='image/png')
            response.headers['Cache-Control'] = 'no-store'
            return response

        regular, _bold = _szov_font_paths()
        return jsonify({
            "text": _szov_broadcast_text(data),
            "hours": len(data['hourly']),
            "hour": (data.get('hour') or {}).get('label'),
            "font_path": regular,
            "images_available": bool(regular),
        })

    @bp.route('/szov_wallboard/broadcast_test', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_szov_wallboard_broadcast_test():
        """Отправить отбивку прямо сейчас — проверить, что чат и картинки настроены.

    Шлём в явно указанный чат и только если он уже в получателях: кнопка проверяет
    настроенную рассылку, а не служит способом написать в произвольную группу.
    Режим «только при отклонениях» здесь не действует — проверяют вид сообщения.

    {personal: true} — проверка личной отбивки: пишем самому нажавшему и никому больше."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, err = _szov_broadcast_guard()
        if err:
            return err
        payload = request.get_json(silent=True) or {}
        try:
            direction = _szov_broadcast_direction_arg(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if payload.get('personal'):
            owner = _szov_broadcast_personal_owner(direction)
            if owner is None:
                return jsonify({"error": "Лично себе отбивку этого табло получают только админы"}), 403
            if not owner[1]:
                return jsonify({"error": "К учётной записи не привязан Telegram — отправлять некуда"}), 400
            chat_id = int(owner[1])
        elif _szov_broadcast_is_personal_only():
            return jsonify({"error": SZOV_BROADCAST_PERSONAL_ONLY_ERROR}), 403
        else:
            raw = payload.get('chat_id')
            recipients = {str(chat['chat_id']): chat
                          for chat in db.get_szov_broadcast_chats(direction)}
            if raw in (None, '') and len(recipients) == 1:
                raw = next(iter(recipients))
            if str(raw) not in recipients:
                return jsonify({"error": "Сначала выберите чат"}), 400
            chat_id = recipients[str(raw)]['chat_id']
        send = {
            SZOV_BROADCAST_DIRECTION_CHAT: _szov_chat_broadcast_send,
            SZOV_BROADCAST_DIRECTION_OP: _op_broadcast_send,
            SZOV_BROADCAST_DIRECTION_TEZ: _tez_broadcast_send,
            SZOV_BROADCAST_DIRECTION_OP_CHAT: _op_chat_broadcast_send,
        }.get(direction, _szov_broadcast_send)
        try:
            loop = _bot_event_loop()
            if loop is None:
                return jsonify({"error": "Бот не запущен"}), 503
            future = asyncio.run_coroutine_threadsafe(send(int(chat_id)), loop)
            future.result(timeout=180)
            return jsonify({"status": "success", "chat_id": chat_id, "direction": direction})
        except Exception as exc:
            logging.error("Отбивка табло: тестовая отправка не удалась: %s", exc, exc_info=True)
            return jsonify({"error": "Не удалось отправить", "detail": str(exc)[:300]}), 502

    return bp
