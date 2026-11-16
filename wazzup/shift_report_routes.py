# -*- coding: utf-8 -*-
"""HTTP «Смены» раздела «Чаты ОП» (вкладка «Операторы → Смены»).

    GET /api/wazzup/shift_report?from=ГГГГ-ММ-ДД&to=ГГГГ-ММ-ДД&account=op
        смены чат-менеджеров за период: строки смен, работа без смены, «не вышел»

Расчёт — `shift_report.py`, здесь только чтение базы, период, кэш и права. Доступ — тот же, что у
«Аналитики» раздела (`_verifier_chats_guard` в bot_schedule2): глобальные админы, главы ОП и СЗоВ,
СВ отдела продаж. Группа, люди и вид («По сменам / По сотрудникам») отбираются на экране —
период приезжает целиком.

Переписка за период читается целиком, как у выгрузки «Табло ОП · Чат» (тот же потолок в 31 сутки):
диалогу нужно его начало, поэтому с запасом в 12 часов до периода, а ночной смене последнего дня —
её утро, поэтому с запасом после. Рассылки и боты отсекаются ещё в базе: в отчёт они не идут, а
памяти занимали бы больше, чем вся работа людей.
"""

import gzip
import json
import logging
import threading
import time as time_mod
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, jsonify, request

from op_wallboard import chat as chat_mod
from test_numbers import keys as test_keys
from . import shift as shift_mod, shift_report

log = logging.getLogger(__name__)

_ALMATY = ZoneInfo('Asia/Almaty')

# Переписка хранится 45 суток (cleanup_wazzup_messages); раньше — пустые дни, а не отчёт.
RETENTION_DAYS = 45
# Потолок периода — как у выгрузки «Табло ОП · Чат» (OP_CHAT_EXPORT_MAX_DAYS): месяц переписки
# в памяти процесса — проверенная на проде величина.
MAX_DAYS = 31
# Смены есть только у аккаунта верификаторов: кнопку смены раздел даёт им (wazzup/workspace_routes).
SHIFT_ACCOUNT = 'op'

MESSAGES_BEFORE = timedelta(hours=12)
EVENTS_BEFORE = timedelta(days=1)
# Ночная смена последнего дня кончается утром следующего; статус дольше 16 часов не держится.
AFTER = timedelta(days=1) + shift_report.MAX_OPEN

TODAY_TTL_SECONDS = 60
PAST_TTL_SECONDS = 600
CACHE_LIMIT = 12
GZIP_MIN_BYTES = 2048


def json_response(payload):
    """JSON со сжатием: неделя смен с журналами — сотни килобайт, сжатая — в десять раз меньше."""
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(body) >= GZIP_MIN_BYTES and 'gzip' in (request.headers.get('Accept-Encoding') or '').lower():
        response = Response(gzip.compress(body, compresslevel=6), mimetype='application/json')
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Vary'] = 'Accept-Encoding'
        return response
    return Response(body, mimetype='application/json')


def parse_period(date_from, date_to, today, *, max_days=MAX_DAYS, retention_days=RETENTION_DAYS):
    """(первый день, последний день). ValueError — период не разобрался или не помещается.

    Пусто — последние семь дней: самый частый вопрос «как прошла неделя». Хвост в будущем
    отрезается: завтрашний пустой день читался бы как день, когда никто не вышел."""
    def _day(value, what):
        value = str(value or '').strip()
        if not value:
            return None
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError(f'{what} периода должно быть в формате ГГГГ-ММ-ДД')

    start, end = _day(date_from, 'Начало'), _day(date_to, 'Конец')
    if start is None and end is None:
        start, end = today - timedelta(days=6), today
    start, end = start or end, end or start
    if end < start:
        start, end = end, start
    if start > today:
        raise ValueError('Период начинается в будущем — смен в нём ещё нет')
    end = min(end, today)
    earliest = today - timedelta(days=retention_days - 1)
    if start < earliest:
        raise ValueError(f'Переписка хранится {retention_days} суток — выберите период '
                         f'не раньше {earliest.strftime("%d.%m.%Y")}')
    if (end - start).days + 1 > max_days:
        raise ValueError(f'Период длиннее {max_days} суток — выберите короче')
    return start, end


# ── чтение ──────────────────────────────────────────────────────────────────

def load_messages(cursor, since, until, account):
    """Сообщения аккаунта за [since, until) в формате `chat.classify`, без рассылок.

    Границы — наивное время Алматы, `dt` — timestamptz: переводим тем же AT TIME ZONE, что
    табло и «Аналитика». Отсекаем то, что `chat.classify` всё равно выбросит: ботов, исходящие без
    автора («Admin» — автоматика Wazzup, их тысячи в сутки) и шаблоны WhatsApp от авторов без
    привязки (рассылки «ЯР», «Отток группа»). Тестовые номера — вне отчёта, как везде в разделе.
    Без ORDER BY: порядок наводит `chat.split_episodes`, а сортировка месяца ушла бы на диск."""
    cursor.execute("""
        SELECT w.channel_id, w.chat_id, (w.dt AT TIME ZONE 'Asia/Almaty'), w.is_echo,
               map.user_id, COALESCE(map.is_bot, FALSE), w.message_id, w.author_id, w.type
          FROM wazzup_messages w
          LEFT JOIN wazzup_operator_map map ON map.author_id = w.author_id
         WHERE w.account = %s
           AND NOT w.is_deleted
           AND w.dt >= (%s::timestamp AT TIME ZONE 'Asia/Almaty')
           AND w.dt < (%s::timestamp AT TIME ZONE 'Asia/Almaty')
           AND (NOT w.is_echo
                OR (w.author_id IS NOT NULL
                    AND NOT COALESCE(map.is_bot, FALSE)
                    AND NOT (map.user_id IS NULL AND COALESCE(w.type, '') = %s)))
           AND """ + test_keys.sql_not_test(test_keys.wazzup_phone_sql('w'), digits=True) + """
    """, (account, since, until, chat_mod.TEMPLATE_TYPE))
    return [{'channel_id': row[0], 'chat_id': row[1], 'at': row[2], 'is_echo': bool(row[3]),
             'user_id': row[4], 'is_bot': bool(row[5]), 'message_id': row[6],
             'author_id': row[7], 'type': row[8]}
            for row in cursor.fetchall()]


def load_roster(cursor, day_from, day_to):
    """Верификаторы периода — кто хоть день был в группе модели op_verificator.

    Им раздел даёт кнопку смены; по ним же строки «не вышел»."""
    cursor.execute("""
        SELECT DISTINCT m.operator_id
          FROM group_operator_memberships m
          JOIN groups g ON g.id = m.group_id
         WHERE LOWER(COALESCE(g.calculation_model_code, '')) = %s
           AND m.start_date <= %s
           AND (m.end_date IS NULL OR m.end_date >= %s)
    """, (chat_mod.VERIFIER_GROUP_MODEL, day_to, day_from))
    return {int(row[0]) for row in cursor.fetchall()}


def _ids(values):
    return sorted({int(value) for value in values or () if value is not None})


def load_events(cursor, user_ids, since, until):
    """{user_id: [(время, ключ, пометка)]} — события смены РАЗДЕЛА (метка в client_event_id).

    Индекс (operator_id, event_at) есть; события iCORE Phone тех же людей сюда не попадают."""
    ids = _ids(user_ids)
    if not ids:
        return {}
    cursor.execute("""
        SELECT operator_id, event_at, status_key, state_note
          FROM operator_status_events
         WHERE operator_id = ANY(%s)
           AND client_event_id LIKE %s
           AND event_at >= %s AND event_at < %s
           AND COALESCE(event_kind, 'status') <> 'action'
         ORDER BY operator_id, event_at, id
    """, (ids, shift_mod.CLIENT_EVENT_PREFIX + '%', since, until))
    out = {}
    for operator_id, event_at, status_key, note in cursor.fetchall():
        out.setdefault(int(operator_id), []).append((event_at, status_key, note))
    return out


def load_models(cursor, user_ids, day_from, day_to):
    """{(user_id, дата): модель группы} — членство на каждую дату, при пересечении — с самым
    поздним началом (та же лестница, что у табло и учёта часов)."""
    ids = _ids(user_ids)
    if not ids:
        return {}
    cursor.execute("""
        SELECT DISTINCT ON (m.operator_id, d.day) m.operator_id, d.day::date,
               LOWER(COALESCE(g.calculation_model_code, ''))
          FROM generate_series(%s::date, %s::date, interval '1 day') AS d(day)
          JOIN group_operator_memberships m
            ON m.start_date <= d.day::date AND (m.end_date IS NULL OR m.end_date >= d.day::date)
          JOIN groups g ON g.id = m.group_id
         WHERE m.operator_id = ANY(%s)
         ORDER BY m.operator_id, d.day, m.start_date DESC, m.id DESC
    """, (day_from, day_to, ids))
    return {(int(row[0]), row[1]): row[2] or '' for row in cursor.fetchall()}


def load_names(cursor, user_ids):
    ids = _ids(user_ids)
    if not ids:
        return {}
    cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (ids,))
    return {int(row[0]): row[1] or '' for row in cursor.fetchall()}


def load_plans(cursor, user_ids, day_from, day_to):
    """{user_id: [(начало, конец, вид смены)]} — смены «Графиков работы» по времени."""
    ids = _ids(user_ids)
    if not ids:
        return {}
    cursor.execute("""
        SELECT operator_id, shift_date, start_time, end_time, shift_type
          FROM work_shifts
         WHERE operator_id = ANY(%s) AND shift_date BETWEEN %s AND %s
         ORDER BY operator_id, shift_date, start_time
    """, (ids, day_from, day_to))
    out = {}
    for operator_id, shift_date, start_time, end_time, shift_type in cursor.fetchall():
        start, end = shift_report.plan_interval(shift_date, start_time, end_time)
        out.setdefault(int(operator_id), []).append((start, end, shift_type or 'regular'))
    return out


def load_lateness(cursor, user_ids, day_from, day_to):
    """{(user_id, дата): (опоздание, ранний уход)} в секундах — цифры «Учёта часов»."""
    ids = _ids(user_ids)
    if not ids:
        return {}
    cursor.execute("""
        SELECT operator_id, day, COALESCE(late_seconds, 0), COALESCE(early_leave_seconds, 0)
          FROM daily_hours
         WHERE operator_id = ANY(%s) AND day BETWEEN %s AND %s
           AND (COALESCE(late_seconds, 0) > 0 OR COALESCE(early_leave_seconds, 0) > 0)
    """, (ids, day_from, day_to))
    return {(int(row[0]), row[1]): (int(row[2]), int(row[3])) for row in cursor.fetchall()}


# ── ручка ───────────────────────────────────────────────────────────────────

def build_shift_report_blueprint(*, db, require_api_key, preflight, guard, account_arg,
                                 max_days=MAX_DAYS,
                                 first_target_seconds=chat_mod.FIRST_TARGET_SECONDS,
                                 inner_target_seconds=chat_mod.INNER_TARGET_SECONDS,
                                 now=None):
    """guard() -> (кто спрашивает, отказ или None); account_arg() -> ключ аккаунта или None."""
    bp = Blueprint('wazzup_shift_report', __name__, url_prefix='/api/wazzup/shift_report')
    cache = {}
    cache_lock = threading.Lock()
    label = 'Чаты ОП · Смены'

    def _now():
        return now() if now else datetime.now(_ALMATY).replace(tzinfo=None)

    def compute(account, day_from, day_to):
        moment = _now()
        start = datetime.combine(day_from, time.min)
        until = min(datetime.combine(day_to, time.min) + AFTER, moment + timedelta(minutes=1))
        include_shifts = account == SHIFT_ACCOUNT
        with db._get_cursor() as cursor:
            rows = load_messages(cursor, start - MESSAGES_BEFORE, until, account)
            writers = {row['user_id'] for row in rows
                       if row['is_echo'] and row['user_id'] is not None and not row['is_bot']}
            roster = load_roster(cursor, day_from - timedelta(days=1), day_to) if include_shifts else set()
            events = (load_events(cursor, roster | writers, start - EVENTS_BEFORE, until)
                      if include_shifts else {})
            people = roster | writers | set(events)
            models = load_models(cursor, people, day_from, day_to)
            names = load_names(cursor, people)
            plans = load_plans(cursor, people, day_from - timedelta(days=1), day_to)
            lateness = load_lateness(cursor, people, day_from, day_to)
        # Расчёт — после того как соединение вернулось в пул: месяц переписки считается секунды,
        # и держать ради этого соединение слабой базы незачем.
        report = shift_report.build_report(
            day_from=day_from, day_to=day_to, now=moment, rows=rows, events=events, plans=plans,
            models=models, names=names, lateness=lateness, include_shifts=include_shifts)
        return {
            'status': 'success',
            'account': account,
            'from': day_from.isoformat(),
            'to': day_to.isoformat(),
            'generatedAt': shift_report.stamp(moment),
            'maxDays': max_days,
            'withShifts': include_shifts,
            'norms': {'firstReplySeconds': first_target_seconds,
                      'innerReplySeconds': inner_target_seconds},
            'rateMinActiveSeconds': shift_report.RATE_MIN_ACTIVE_SECONDS,
            **report,
        }

    def cached_report(account, day_from, day_to):
        today = _now().date()
        key = (account, day_from, day_to)
        ttl = TODAY_TTL_SECONDS if day_to >= today else PAST_TTL_SECONDS
        entry = cache.get(key)
        if entry and time_mod.time() - entry[0] < ttl:
            return entry[1]
        # Один расчёт на всех: второй зритель того же периода ждёт первого, а не читает базу снова.
        with cache_lock:
            entry = cache.get(key)
            if entry and time_mod.time() - entry[0] < ttl:
                return entry[1]
            payload = compute(account, day_from, day_to)
            cache[key] = (time_mod.time(), payload)
            if len(cache) > CACHE_LIMIT:
                for stale in sorted(cache, key=lambda item: cache[item][0])[:len(cache) - CACHE_LIMIT]:
                    cache.pop(stale, None)
            return payload

    @bp.route('', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_shift_report():
        if request.method == 'OPTIONS':
            return preflight()
        _, refusal = guard()
        if refusal is not None:
            return refusal
        account = account_arg()
        if account is None:
            return jsonify({'error': 'unknown account'}), 400
        try:
            day_from, day_to = parse_period(request.args.get('from'), request.args.get('to'),
                                            _now().date(), max_days=max_days)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        try:
            payload = cached_report(account, day_from, day_to)
        except Exception as exc:  # noqa: BLE001
            log.exception('%s: отчёт не собрался', label)
            return jsonify({'error': 'Отчёт сейчас не собирается', 'detail': str(exc)[:300]}), 503
        return json_response(payload)

    bp.report = cached_report
    bp.compute = compute
    return bp
