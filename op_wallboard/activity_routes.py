# -*- coding: utf-8 -*-
"""HTTP «Активность операторов» (задача #392) — вкладка «Отчёт» раздела «Табло ОП».

    GET /api/op_wallboard/activity          ?month=ГГГГ-ММ — завершённые смены месяца, операторы
                                            для фильтра, доступные месяцы, докачка звонков
    GET /api/op_wallboard/activity/journal  ?operator_id=&start=&end= — журнал статусов одной смены
    GET /api/op_wallboard/activity/export   ?month=&group=&operator_ids=&days= — те же строки .xlsx

Расчёт — `activity.py`, здесь только чтение базы, кэш месяца и права. Фильтры группы,
операторов и дней экран применяет сам (месяц приезжает целиком, переключение мгновенное),
а выгрузка проходит их на сервере тем же `activity.filter_shifts` — файл и экран не расходятся.

Доступ — как у табло: глобальные админы, глава отдела продаж и его СВ (`routes.make_guard`), и
поимённо — `EXTRA_ACCESS_USER_IDS` («уполномоченные» из ТЗ: кого — решает владелец, по одной
строке на человека). Табло им при этом не открывается: у вкладки свой guard.
"""

import gzip
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, jsonify, request, send_file

from cdr import queries as cdr_queries, sync as cdr_sync, touches as touches_mod
from test_numbers import keys as test_keys
from . import activity as activity_mod, activity_export as export_mod

log = logging.getLogger(__name__)

_ALMATY = ZoneInfo('Asia/Almaty')

# «Уполномоченные сотрудники» ТЗ — поимённо, как допуски «Касаний» (cdr/access.py). Пока пусто:
# постановщик никого не назвал, а роль и отдел руководителей покрывают уже сами.
EXTRA_ACCESS_USER_IDS = frozenset()

# Месяц считается одним проходом по базе (события, касания, членства) — секунды на слабой
# базе. Текущий месяц меняется (дописываются смены), прошлые — почти нет.
CURRENT_MONTH_TTL_SECONDS = 90
PAST_MONTH_TTL_SECONDS = 600
# Журнал открывают по смене из таблицы: окно длиннее самой длинной смены — не смена.
JOURNAL_MAX_HOURS = 30

# Месяц отдела — тысяча с лишним строк, около мегабайта JSON. Сжатый ответ в десять раз меньше,
# а браузер разжимает его сам; без поддержки gzip у клиента уходит обычный JSON.
GZIP_MIN_BYTES = 2048


def json_response(payload):
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(body) >= GZIP_MIN_BYTES and 'gzip' in (request.headers.get('Accept-Encoding') or '').lower():
        response = Response(gzip.compress(body, compresslevel=6), mimetype='application/json')
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Vary'] = 'Accept-Encoding'
        return response
    return Response(body, mimetype='application/json')


# Запас окна событий вокруг месяца: начало смены 1-го числа решает вечер 31-го (ночная смена
# или тихая пауза перед ней), а конец смены последнего дня лежит в следующих сутках.
EVENTS_BEFORE = timedelta(hours=24)
EVENTS_AFTER = timedelta(days=2)


def make_activity_guard(base_guard, extra_user_ids=EXTRA_ACCESS_USER_IDS):
    """Guard табло плюс поимённый список. Без входа отказ остаётся отказом."""
    extra = frozenset(int(v) for v in extra_user_ids or ())

    def guard():
        requester_id, refusal = base_guard()
        if refusal is not None and requester_id is not None and extra:
            try:
                if int(requester_id) in extra:
                    return requester_id, None
            except (TypeError, ValueError):
                pass
        return requester_id, refusal
    return guard


def load_roster_events(cursor, since, until, day_from, day_to):
    """События телефонов всех, кто в периоде состоял в группе ОП: {operator_id: [(время, ключ)]}.

    Только живые события iCORE Phone (`client_event_id IS NOT NULL`) — в той же таблице лежат
    импорты, а они не про этот телефон. Индекс (operator_id, event_at) есть."""
    cursor.execute("""
        WITH roster AS (
            SELECT DISTINCT m.operator_id
              FROM group_operator_memberships m
              JOIN groups g ON g.id = m.group_id
             WHERE LOWER(COALESCE(g.calculation_model_code, '')) = ANY(%(models)s)
               AND m.start_date <= %(day_to)s
               AND (m.end_date IS NULL OR m.end_date >= %(day_from)s)
        )
        SELECT e.operator_id, e.event_at, e.status_key
          FROM operator_status_events e
          JOIN roster r ON r.operator_id = e.operator_id
         WHERE e.client_event_id IS NOT NULL
           AND e.event_at >= %(since)s AND e.event_at < %(until)s
         ORDER BY e.operator_id, e.event_at, e.id
    """, {'models': list(activity_mod.GROUP_MODELS), 'day_from': day_from, 'day_to': day_to,
          'since': since, 'until': until})
    out = {}
    for operator_id, event_at, status_key in cursor.fetchall():
        out.setdefault(int(operator_id), []).append((event_at, status_key))
    return out


def load_people(cursor, operator_ids):
    """({id: ФИО}, {id: внутренний номер}). Номер — нынешний: истории номеров у нас нет, но
    звонок всё равно ложится только в смену того, чей телефон в это время был на связи."""
    ids = sorted({int(v) for v in operator_ids or []})
    if not ids:
        return {}, {}
    cursor.execute("SELECT id, name, NULLIF(TRIM(sip_number), '') FROM users WHERE id = ANY(%s)", (ids,))
    names, exts = {}, {}
    for operator_id, name, sip in cursor.fetchall():
        names[int(operator_id)] = name or ''
        if sip:
            exts[int(operator_id)] = str(sip)
    return names, exts


def load_memberships(cursor, operator_ids, day_from, day_to):
    """{operator_id: [{id, start_date, end_date, group_id, model}]} — членства, задевшие период."""
    ids = sorted({int(v) for v in operator_ids or []})
    if not ids:
        return {}
    cursor.execute("""
        SELECT m.operator_id, m.id, m.start_date, m.end_date, g.id,
               LOWER(COALESCE(g.calculation_model_code, ''))
          FROM group_operator_memberships m
          JOIN groups g ON g.id = m.group_id
         WHERE m.operator_id = ANY(%s)
           AND m.start_date <= %s
           AND (m.end_date IS NULL OR m.end_date >= %s)
    """, (ids, day_to, day_from))
    out = {}
    for operator_id, member_id, start, end, group_id, model in cursor.fetchall():
        out.setdefault(int(operator_id), []).append({
            'id': int(member_id), 'start_date': start, 'end_date': end,
            'group_id': int(group_id), 'model': model or ''})
    return out


def load_touches(cursor, exts, day_from, day_to):
    """Касания внутренних номеров состава за сутки периода — одним запросом по call_day.

    Не дошедшие до очереди и тестовые номера отсеяны тем же правилом, что у табло
    (`cdr.queries.day_touches_compact`)."""
    exts = sorted({str(v) for v in exts or [] if v})
    if not exts:
        return []
    cursor.execute("""
        SELECT started_at, answered_at, ext, call_type, result, talk_seconds,
               dial_seconds, queue, talk_measured_seconds
          FROM cdr_touches
         WHERE call_day BETWEEN %s AND %s
           AND ext = ANY(%s)
           AND call_type <> %s
           AND """ + test_keys.sql_not_test('cdr_touches.phone', digits=True) + """
    """, (day_from, day_to, exts, touches_mod.TYPE_IN_BEFORE_QUEUE))
    return [{
        'started_at': row[0], 'answered_at': row[1].strftime('%Y-%m-%d %H:%M:%S') if row[1] else '',
        'ext': row[2] or '', 'call_type': row[3] or '', 'result': row[4] or '',
        'talk_seconds': int(row[5] or 0), 'dial_seconds': int(row[6] or 0), 'queue': row[7] or '',
        'talk_measured_seconds': None if row[8] is None else int(row[8]),
    } for row in cursor.fetchall()]


def build_op_activity_blueprint(*, db, require_api_key, build_cors_preflight_response, guard,
                                retention_days=120, talk_min_seconds=activity_mod.TALK_MIN_SECONDS):
    bp = Blueprint('op_activity', __name__, url_prefix='/api/op_wallboard/activity')
    cache = {}
    cache_lock = threading.Lock()
    label = 'Активность операторов'

    def _now():
        return datetime.now(_ALMATY).replace(tzinfo=None)

    def _calls_coverage(cursor, day_from, day_to, today, requested_by):
        """Сутки периода, звонки которых ещё не приехали, — и просьба мосту их принести.

        Та же очередь суток, что у «Касаний». Сегодня мосту не заказываем: его живой хвост и так
        досылает звонки каждые двадцать секунд, а пересборка суток на каждое открытие отчёта
        только гоняла бы станцию."""
        last = min(day_to + timedelta(days=1), today)
        if last < day_from:
            return []
        states = cdr_queries.day_states(cursor, day_from, last)
        ask_until = min(last, today - timedelta(days=1))
        if ask_until >= day_from:
            missing = cdr_sync.days_needing_sync(states, day_from, ask_until, today)
            if missing:
                cdr_queries.enqueue_days(cursor, missing, requested_by)
                states = cdr_queries.day_states(cursor, day_from, last)
        awaiting = cdr_sync.days_awaiting(states, day_from, last, today)
        return [day.isoformat() for day in awaiting if day < today]

    def _compute(month_first, month_last, requested_by):
        now = _now()
        today = now.date()
        since = datetime.combine(month_first, datetime.min.time()) - EVENTS_BEFORE
        until = min(datetime.combine(month_last + timedelta(days=1), datetime.min.time()) + EVENTS_AFTER,
                    now + timedelta(minutes=1))
        with db._get_cursor() as cursor:
            events = load_roster_events(cursor, since, until, month_first, month_last)
            names, exts = load_people(cursor, events.keys())
            memberships = load_memberships(cursor, events.keys(), month_first, month_last)
            touches = load_touches(cursor, exts.values(), month_first - timedelta(days=1),
                                   month_last + timedelta(days=1))
        # Заявка мосту — своей транзакцией: её сбой не должен ни отменять чтение, ни оставлять
        # соединение в оборванной транзакции.
        try:
            with db._get_cursor() as cursor:
                pending = _calls_coverage(cursor, month_first, month_last, today, requested_by)
        except Exception:  # noqa: BLE001
            # Без очереди суток отчёт всё равно верен по тому, что уже приехало; сказать
            # «звонки ещё загружаются» он просто не сможет.
            log.exception('%s: покрытие звонков за %s не посчиталось', label, month_first)
            pending = []
        report = activity_mod.build_report(
            events_by_operator=events, names=names, exts=exts, memberships=memberships,
            touches=touches, day_from=month_first, day_to=month_last, now=now,
            talk_min=talk_min_seconds)
        days_with_shifts = sorted({row['date'] for row in report['shifts']})
        return {
            'month': '%04d-%02d' % (month_first.year, month_first.month),
            'month_label': activity_mod.month_label(month_first),
            'day_from': month_first.isoformat(),
            'day_to': month_last.isoformat(),
            'generated_at': now.strftime('%Y-%m-%dT%H:%M:%S'),
            'months': activity_mod.available_months(today, retention_days),
            'groups': [{'key': key, 'label': activity_mod.GROUP_LABELS[key]}
                       for key in activity_mod.GROUP_ORDER],
            'operators': report['operators'],
            'shifts': report['shifts'],
            'days_with_shifts': days_with_shifts,
            'calls_pending_days': pending,
            'talk_min_seconds': talk_min_seconds,
        }

    def _month_report(month_value, requested_by):
        today = _now().date()
        month_first, month_last = activity_mod.month_bounds(month_value, today)
        if month_first > today:
            raise ValueError('Этот месяц ещё не начался')
        floor = today - timedelta(days=int(retention_days))
        if month_last < floor:
            raise ValueError('Статусы телефонов хранятся %d дней — за этот месяц их уже нет'
                             % int(retention_days))
        key = month_first.isoformat()
        ttl = CURRENT_MONTH_TTL_SECONDS if month_last >= today else PAST_MONTH_TTL_SECONDS
        entry = cache.get(key)
        if entry and time.time() - entry[0] < ttl:
            return entry[1]
        # Один расчёт месяца на всех: второй зритель ждёт первого, а не считает то же самое.
        with cache_lock:
            entry = cache.get(key)
            if entry and time.time() - entry[0] < ttl:
                return entry[1]
            payload = _compute(month_first, month_last, requested_by)
            cache[key] = (time.time(), payload)
            for stale in [k for k, (ts, _) in cache.items() if time.time() - ts > PAST_MONTH_TTL_SECONDS]:
                cache.pop(stale, None)
            return payload

    def _refuse():
        requester_id, refusal = guard()
        return requester_id, refusal

    @bp.route('', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_activity():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        requester_id, refusal = _refuse()
        if refusal is not None:
            return refusal
        try:
            payload = _month_report(request.args.get('month'), requester_id)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            log.exception('%s: отчёт не собрался', label)
            return jsonify({'error': 'Отчёт сейчас не собирается', 'detail': str(exc)[:300]}), 503
        return json_response(payload)

    @bp.route('/journal', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_activity_journal():
        """Журнал статусов одной смены. Открыть можно только оператора группы ОП: ручка не
        должна читать события телефона любого сотрудника по id."""
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        _, refusal = _refuse()
        if refusal is not None:
            return refusal
        try:
            operator_id = int(str(request.args.get('operator_id') or '').strip())
        except ValueError:
            return jsonify({'error': 'Не указан сотрудник'}), 400
        start = activity_mod.parse_moment(request.args.get('start'))
        end = activity_mod.parse_moment(request.args.get('end'))
        if start is None or end is None or end < start:
            return jsonify({'error': 'Не указана смена'}), 400
        if end - start > timedelta(hours=JOURNAL_MAX_HOURS):
            return jsonify({'error': 'Слишком длинное окно для одной смены'}), 400
        today = _now().date()
        if start.date() < today - timedelta(days=int(retention_days)):
            return jsonify({'error': 'Статусы этой смены уже не хранятся'}), 404
        try:
            with db._get_cursor() as cursor:
                memberships = load_memberships(cursor, [operator_id], start.date(), start.date())
                group_key, _group_id = activity_mod.group_on(memberships, operator_id, start.date())
                if group_key is None:
                    return jsonify({'error': 'Сотрудник в этот день не в группе ОП'}), 404
                names, _exts = load_people(cursor, [operator_id])
                cursor.execute("""
                    SELECT event_at, status_key
                      FROM operator_status_events
                     WHERE operator_id = %s
                       AND client_event_id IS NOT NULL
                       AND event_at >= %s AND event_at <= %s
                     ORDER BY event_at, id
                """, (operator_id, start, end))
                events = cursor.fetchall()
        except Exception:  # noqa: BLE001
            log.exception('%s: журнал %s не прочитался', label, operator_id)
            return jsonify({'error': 'Журнал статусов сейчас недоступен'}), 503
        shift = activity_mod.canonical_events(events)
        segments = activity_mod.journal(shift, group_key) if shift else []
        return jsonify({
            'operator_id': operator_id,
            'name': names.get(operator_id, ''),
            'group_key': group_key,
            'group_label': activity_mod.GROUP_LABELS[group_key],
            'start_at': activity_mod.stamp(start),
            'end_at': activity_mod.stamp(end),
            'logout_missing': not (shift and activity_mod.classify(shift[-1][1])[0]
                                   == activity_mod.KIND_LOGOUT),
            'segments': segments,
        })

    @bp.route('/export', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_activity_export():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        requester_id, refusal = _refuse()
        if refusal is not None:
            return refusal
        try:
            payload = _month_report(request.args.get('month'), requester_id)
            first = datetime.strptime(payload['day_from'], '%Y-%m-%d').date()
            last = datetime.strptime(payload['day_to'], '%Y-%m-%d').date()
            group = str(request.args.get('group') or '').strip() or None
            if group and group not in activity_mod.GROUP_LABELS:
                raise ValueError('Неизвестная группа')
            operator_ids = activity_mod.parse_ids(request.args.get('operator_ids'))
            days = activity_mod.parse_days(request.args.get('days'), first, last)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            log.exception('%s: выгрузка не собралась', label)
            return jsonify({'error': 'Не удалось собрать выгрузку', 'detail': str(exc)[:300]}), 503
        rows = activity_mod.filter_shifts(payload['shifts'], group, operator_ids, days)
        try:
            content = export_mod.workbook(
                rows, month_label=payload['month_label'],
                group_label=activity_mod.GROUP_LABELS.get(group) if group else 'Все группы',
                generated_at=_now().strftime('%d.%m.%Y %H:%M'),
                talk_min_seconds=payload['talk_min_seconds'],
                calls_pending_days=payload['calls_pending_days'])
        except Exception as exc:  # noqa: BLE001
            log.exception('%s: файл не собрался', label)
            return jsonify({'error': 'Не удалось собрать выгрузку', 'detail': str(exc)[:300]}), 500
        return send_file(
            BytesIO(content), as_attachment=True,
            download_name=export_mod.file_name(payload['month']),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    bp.month_report = _month_report
    return bp
