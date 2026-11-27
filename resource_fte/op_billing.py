# -*- coding: utf-8 -*-
"""«Расчет ресурсов · Основа ОП» → «Звонки» и «Биллинг» по «Касаниям» (IC-1, #470).

Владелец 27.11.2026: у «Основы» должны быть те же вкладки, что у линии СЗоВ. У линии они
читают Oktell, у отдела продаж своя АТС — её звонки лежат в «Касаниях» (cdr_touches), а
статусы операторов пишет iCORE Phone. Поэтому здесь только СБОР строк — в той же форме,
что строки Oktell, а отчёты строят ТЕ ЖЕ построители линии (_oktell_billing_build_report,
_oktell_billing_build_hourly_report, _oktell_billing_build_operator_report, выгрузки в Excel
из монолита): экран, разрезы, итоги и книги у «Основы» и у линии одни.

Что чем считается (определения — как у линии, источник — «Касания»):
  поступило    — входящие, дошедшие до очереди (принятые и непринятые);
  обслужено    — принятые; SL — принятые с ожиданием в очереди не дольше порога;
  сброс в IVR  — входящие, не дошедшие до очереди (`Входящий (не дошёл до очереди)`);
  разговор     — САМ разговор оператора, без автоинформатора и очереди: у линии здесь звонок
                 целиком, ради сверки с отчётом Oktell для парков (#380) — у «Касаний» такого
                 отчёта нет, и смешивать ожидание с работой незачем;
  ожидание     — журнал очередей станции (wait_seconds);
  таксопарк    — по очереди звонка, у не дошедших до очереди — по номеру линии (cdr/lines.py,
                 те же подписи, что у «Касаний» и «Биллинга Oktell»);
  оценок водителей у отдела продаж нет — их колонки нулевые.
Тестовые номера реестра в отчёты не входят, как и во все остальные расчёты портала.

«Операторы» и «Группировка» берут людей «Основы» по группе на дату (модель op_osnova) и их
время по статусам iCORE Phone — ровно так, как «Отчёт по входу» (cdr/inbound_staffing.py).
Комментарии «Группировки» — своя таблица: общая таблица линии ключевана одной датой и часом,
и комментарий СВ ОП лёг бы в «Группировку» СЗоВ.
"""
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from cdr import inbound_staffing, lines as lines_mod, staffing_queries, touches as touches_mod
from op_wallboard import activity as activity_mod
from resource_fte import op_osnova as op

IN_ANSWERED = 'Входящий'
IN_LOST = 'Входящий (не приняли)'
IN_BEFORE_QUEUE = touches_mod.TYPE_IN_BEFORE_QUEUE
OUTBOUND = touches_mod.TYPE_OUT
OUTBOUND_CONNECTED = op.OUTBOUND_CONNECTED
GROUP_MODEL = inbound_staffing.GROUP_MODEL

COMMENTS_TABLE = op.COMMENTS_TABLE
COMMENT_MAX_LENGTH = 500
# Человек на линии в часе — хотя бы минута на линии (как «онлайн хотя бы минуту» у чата).
FACT_MIN_SECONDS = 60
# Номер → очередь парка — по входящим этого окна, как в «Касаниях» (cdr/routes.py,
# LINE_PARKS_WINDOW_DAYS): подпись парка не зависит ни от периода, ни от окна времени отчёта.
KNOWN_LINES_WINDOW_DAYS = 60
# Отрезки статусов iCORE Phone → поля состояний Oktell. «Пауза» — как State 9 у линии со
# всеми подпричинами: перерыв, технический перерыв и тренинг (op_wallboard/activity.py,
# _PAUSE_KINDS); иначе тренинг выпадал из времени в системе и OCC с UTZ были завышены.
_STATE_KEYS = {
    activity_mod.KIND_TALK: 'talk',
    activity_mod.KIND_DIAL: 'dial_wait_out_seconds',
    activity_mod.KIND_WAIT: 'wait_seconds',
    activity_mod.KIND_BREAK: 'pause_seconds',
    activity_mod.KIND_TECH: 'pause_seconds',
    activity_mod.KIND_TRAINING: 'pause_seconds',
}

# Ожидание до ответа: журнал очередей, а без него — от входа в очередь (или начала) до ответа.
_WAIT_SQL = ("COALESCE(t.wait_seconds, GREATEST(0, EXTRACT(EPOCH FROM "
             "(t.answered_at - COALESCE(t.queued_at, t.started_at))))::int, 0)")
_TALK_SQL = 'COALESCE(t.talk_measured_seconds, t.talk_seconds, 0)'
_MINUTE_SQL = ("(EXTRACT(HOUR FROM t.started_at)::int * 60 + EXTRACT(MINUTE FROM t.started_at)::int) "
               "BETWEEN %(minute_from)s AND %(minute_to)s")


def _not_test(cursor, column='t.phone') -> str:
    return op._not_test_sql(cursor, column)


def known_lines(cursor, day_from: date) -> Dict[str, str]:
    """{наш номер: очередь парка} — правилом «Касаний»: входящие последних двух месяцев,
    а у отчёта о более давнем периоде — и от его первого дня. Из строк самого отчёта карту
    строить нельзя: тогда сброс на приветствии одного и того же дня попадал в разные парки
    (или ни в какой) в зависимости от выбранного периода и окна времени."""
    from cdr import queries as cdr_queries
    since = min(day_from, cdr_queries.today_almaty() - timedelta(days=KNOWN_LINES_WINDOW_DAYS))
    return lines_mod.line_queues(cdr_queries.line_queue_rows(cursor, since))


# ── «Таксопарки», «Номера», «Группировка»: строки день × парк [× номер] или день × час ─────────

def report_rows(cursor, day_from: date, day_to: date, minute_from: int, minute_to: int,
                sl_seconds: int, group_by: str = 'park') -> List[Dict[str, Any]]:
    """Сырые строки в форме Oktell (report_date, taxi_park, line_number, report_hour + счётчики)."""
    cursor.execute(
        f"""
        SELECT t.call_day, EXTRACT(HOUR FROM t.started_at)::int, t.queue, t.line_number,
               COUNT(*) FILTER (WHERE t.call_type IN (%(answered)s, %(lost)s) AND t.queue <> ''),
               COUNT(*) FILTER (WHERE t.call_type = %(answered)s AND t.queue <> ''),
               COUNT(*) FILTER (WHERE t.call_type = %(answered)s AND t.queue <> ''
                                  AND {_WAIT_SQL} <= %(sl)s),
               COUNT(*) FILTER (WHERE t.call_type = %(before)s),
               COALESCE(SUM({_TALK_SQL}) FILTER (WHERE t.call_type = %(answered)s AND t.queue <> ''), 0),
               COALESCE(SUM({_WAIT_SQL}) FILTER (WHERE t.call_type = %(answered)s AND t.queue <> ''), 0),
               COALESCE(SUM({_WAIT_SQL}) FILTER (WHERE t.call_type = %(lost)s AND t.queue <> ''), 0),
               COALESCE(SUM({_TALK_SQL} + {_WAIT_SQL})
                        FILTER (WHERE t.call_type IN (%(answered)s, %(lost)s) AND t.queue <> ''), 0)
          FROM cdr_touches t
         WHERE t.call_day BETWEEN %(day_from)s AND %(day_to)s
           AND t.call_type IN (%(answered)s, %(lost)s, %(before)s)
           AND {_MINUTE_SQL}{_not_test(cursor)}
         GROUP BY 1, 2, 3, 4
        """,
        {'answered': IN_ANSWERED, 'lost': IN_LOST, 'before': IN_BEFORE_QUEUE, 'sl': int(sl_seconds),
         'day_from': day_from, 'day_to': day_to, 'minute_from': int(minute_from), 'minute_to': int(minute_to)},
    )
    raw = cursor.fetchall()
    # Почасовому разрезу («Группировка») парк не нужен — карту не читаем.
    return aggregate_rows(raw, group_by, known_lines(cursor, day_from) if group_by != 'hour' else {})


def aggregate_rows(raw: Iterable[Tuple], group_by: str = 'park',
                   known: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Строки базы (день, час, очередь, номер, счётчики…) → строки Oktell по разрезу.

    Чистая функция: парк — по очереди звонка, у не дошедших до очереди — по номеру линии
    (cdr/lines.py), строки без парка в разрезы по паркам не идут, как у Oktell. known —
    карта «номер → очередь» (known_lines); без неё — по самим строкам, для проверок."""
    raw = list(raw)
    if known is None:
        known = lines_mod.line_queues(
            (row[3], row[2], row[4]) for row in raw if row[2] and row[3])
    buckets: Dict[Tuple, Dict[str, Any]] = {}
    for (day, hour, queue, line_number, arrived, served, served_sl, greet, talk,
         wait_ok, wait_lost, total) in raw:
        touch = {'queue': queue or '', 'line_number': line_number or '', 'call_type': IN_ANSWERED}
        park = lines_mod.park(touch, known)
        if group_by == 'hour':
            key = (day, int(hour))
        elif group_by == 'line':
            key = (day, park, line_number or '')
        else:
            key = (day, park)
        if group_by != 'hour' and not park:
            continue  # ни очереди, ни знакомого номера — парку не принадлежит, как у Oktell
        bucket = buckets.setdefault(key, {
            'report_date': day.isoformat(),
            'taxi_park': '' if group_by == 'hour' else park,
            'line_number': (line_number or '') if group_by == 'line' else '',
            'report_hour': int(hour) if group_by == 'hour' else None,
            'arrived': 0, 'served': 0, 'served_sl': 0, 'greet_drop': 0,
            'talk_seconds': 0, 'wait_ok_seconds': 0, 'wait_lost_seconds': 0, 'total_seconds': 0,
            'rating_sum': 0, 'rating_count': 0,
        })
        bucket['arrived'] += int(arrived or 0)
        bucket['served'] += int(served or 0)
        bucket['served_sl'] += int(served_sl or 0)
        bucket['greet_drop'] += int(greet or 0)
        bucket['talk_seconds'] += int(talk or 0)
        bucket['wait_ok_seconds'] += int(wait_ok or 0)
        bucket['wait_lost_seconds'] += int(wait_lost or 0)
        bucket['total_seconds'] += int(total or 0)
    return list(buckets.values())


# ── состав «Основы» и её время по статусам iCORE Phone ───────────────────────────────────────

class Roster:
    """Люди «Основы» за период: состав по группе на дату, номера, ФИО, события телефонов."""

    def __init__(self, cursor, day_from: date, day_to: date, now: datetime):
        self.day_from, self.day_to, self.now = day_from, day_to, now
        self.operator_ids = staffing_queries.roster(cursor, GROUP_MODEL, day_from, day_to)
        self.memberships = staffing_queries.memberships(cursor, self.operator_ids, day_from, day_to) \
            if self.operator_ids else {}
        self.exts = staffing_queries.operator_exts(cursor, self.operator_ids) if self.operator_ids else {}
        self.names = staffing_queries.operator_names(cursor, self.operator_ids) if self.operator_ids else {}
        since = datetime.combine(day_from, datetime.min.time()) - timedelta(days=1)
        until = min(datetime.combine(day_to, datetime.min.time()) + timedelta(days=2),
                    now + timedelta(minutes=1))
        self.events = staffing_queries.phone_events(cursor, self.operator_ids, since, until) \
            if self.operator_ids else {}
        self.moments = staffing_queries.human_call_moments(
            cursor, self.exts.values(), day_from - timedelta(days=1), day_to + timedelta(days=1)) \
            if self.exts else {}

    def in_group(self, operator_id: int, day: date) -> bool:
        group_key, _ = activity_mod.group_on(self.memberships, operator_id, day)
        return group_key == activity_mod.GROUP_MODEL_KEYS[GROUP_MODEL]

    def operator_on_ext(self, ext: str, day: date) -> Optional[int]:
        """Чей внутренний номер в этот день — из людей «Основы» по группе на дату."""
        for operator_id, own in self.exts.items():
            if str(own) == str(ext) and self.in_group(operator_id, day):
                return operator_id
        return None

    def pieces(self, operator_id: int) -> List[Tuple[str, datetime, datetime]]:
        """[(вид, начало, конец)] — нарезка статусов телефона, как в «Активности операторов»."""
        events = self.events.get(operator_id) or ()
        moments = self.moments.get(self.exts.get(operator_id)) or ()
        out = []
        for shift in activity_mod.split_shifts(events, moments):
            for kind, _base, start, end, _seconds in activity_mod.pieces(
                    shift, activity_mod.GROUP_MODEL_KEYS[GROUP_MODEL]):
                out.append((kind, start, end))
        return out


def _clip(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> int:
    return max(0, int((min(end, window_end) - max(start, window_start)).total_seconds()))


def _day_window(day: date, minute_from: int, minute_to: int) -> Tuple[datetime, datetime]:
    midnight = datetime.combine(day, datetime.min.time())
    return midnight + timedelta(minutes=minute_from), midnight + timedelta(minutes=minute_to + 1)


def _days(day_from: date, day_to: date) -> List[date]:
    out, current = [], day_from
    while current <= day_to:
        out.append(current)
        current += timedelta(days=1)
    return out


# ── «Операторы» ───────────────────────────────────────────────────────────────────────────

def operator_rows(cursor, day_from: date, day_to: date, minute_from: int, minute_to: int,
                  now: datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(звонки, состояния) по (день, оператор) в форме Oktell — для общего построителя.

    Звонки: принятые входящие и СОЕДИНЁННЫЕ исходящие с номера оператора — как у линии
    (Oktell берёт только call_result 5): недозвоны в «Звонков» и ATT не идут, иначе средний
    разговор тонул бы в нулях. Разговор — сам разговор (ATT); обработка (AHT) у входящих —
    разговор и ожидание в очереди, у исходящих — звонок целиком, гудки и разговор, как
    total_length линии (dial_seconds «Касаний» — длина звонка ЦЕЛИКОМ, cdr/touches.py).
    Состояния — только по статусам iCORE Phone, как у линии по состояниям Oktell: «Занят» —
    разговор (делится между входом и исходом пропорционально разговорам), «Соединение» —
    «Дозвон» (у линии State 2), ожидание, перерыв. Гудки из «Касаний» в состояния не идут:
    это то же время, что «Соединение», и в OCC оно легло бы дважды."""
    roster = Roster(cursor, day_from, day_to, now)
    if not roster.operator_ids:
        return [], []
    exts = sorted({str(ext) for ext in roster.exts.values() if ext})
    calls: Dict[Tuple[date, int], Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if exts:
        cursor.execute(
            f"""
            SELECT t.call_day, t.ext,
                   COUNT(*) FILTER (WHERE t.call_type = %(answered)s),
                   COALESCE(SUM({_TALK_SQL}) FILTER (WHERE t.call_type = %(answered)s), 0),
                   COALESCE(SUM({_WAIT_SQL}) FILTER (WHERE t.call_type = %(answered)s), 0),
                   COUNT(*) FILTER (WHERE t.call_type = %(out)s AND t.result = %(connected)s),
                   COALESCE(SUM(COALESCE(t.talk_seconds, 0))
                            FILTER (WHERE t.call_type = %(out)s AND t.result = %(connected)s), 0),
                   COALESCE(SUM(GREATEST(COALESCE(t.dial_seconds, 0), COALESCE(t.talk_seconds, 0)))
                            FILTER (WHERE t.call_type = %(out)s AND t.result = %(connected)s), 0)
              FROM cdr_touches t
             WHERE t.call_day BETWEEN %(day_from)s AND %(day_to)s
               AND t.ext = ANY(%(exts)s)
               AND t.call_type IN (%(answered)s, %(out)s)
               AND {_MINUTE_SQL}{_not_test(cursor)}
             GROUP BY 1, 2
            """,
            {'answered': IN_ANSWERED, 'out': OUTBOUND, 'connected': OUTBOUND_CONNECTED,
             'day_from': day_from, 'day_to': day_to,
             'exts': exts, 'minute_from': int(minute_from), 'minute_to': int(minute_to)},
        )
        for day, ext, served_in, talk_in, wait_in, served_out, talk_out, whole_out in cursor.fetchall():
            operator_id = roster.operator_on_ext(ext, day)
            if operator_id is None:
                continue
            item = calls[(day, operator_id)]
            item['served_in'] += int(served_in or 0)
            item['call_in_seconds'] += int(talk_in or 0)
            item['handle_in_seconds'] += int(talk_in or 0) + int(wait_in or 0)
            item['served_out'] += int(served_out or 0)
            item['call_out_seconds'] += int(talk_out or 0)
            item['handle_out_seconds'] += int(whole_out or 0)

    states: Dict[Tuple[date, int], Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    days = _days(day_from, day_to)
    for operator_id in roster.operator_ids:
        pieces = roster.pieces(operator_id)
        if not pieces:
            continue
        for day in days:
            if not roster.in_group(operator_id, day):
                continue
            window_start, window_end = _day_window(day, minute_from, minute_to)
            for kind, start, end in pieces:
                seconds = _clip(start, end, window_start, window_end)
                if seconds <= 0:
                    continue
                key = _STATE_KEYS.get(kind)
                if key:
                    states[(day, operator_id)][key] += seconds

    calls_out, states_out = [], []
    for (day, operator_id), item in calls.items():
        calls_out.append({'report_date': day.isoformat(), 'operator_name': roster.names.get(operator_id) or '',
                          'served_in': item['served_in'], 'served_out': item['served_out'],
                          'call_in_seconds': item['call_in_seconds'], 'call_out_seconds': item['call_out_seconds'],
                          'handle_in_seconds': item['handle_in_seconds'],
                          'handle_out_seconds': item['handle_out_seconds']})
    for (day, operator_id), item in states.items():
        call = calls.get((day, operator_id)) or {}
        talk_in, talk_out = int(call.get('call_in_seconds') or 0), int(call.get('call_out_seconds') or 0)
        talk_state = int(item.get('talk') or 0)
        share_in = talk_in / (talk_in + talk_out) if (talk_in + talk_out) > 0 else 1.0
        states_out.append({
            'report_date': day.isoformat(), 'operator_name': roster.names.get(operator_id) or '',
            'talk_in_seconds': int(round(talk_state * share_in)),
            'talk_out_seconds': talk_state - int(round(talk_state * share_in)),
            'postproc_in_seconds': 0, 'postproc_out_seconds': 0, 'hold_in_seconds': 0, 'hold_out_seconds': 0,
            'dial_in_seconds': 0, 'dial_out_seconds': 0, 'dial_other_seconds': 0,
            'dial_wait_out_seconds': int(item.get('dial_wait_out_seconds') or 0),
            'wait_seconds': int(item.get('wait_seconds') or 0),
            'pause_seconds': int(item.get('pause_seconds') or 0),
        })
    return calls_out, states_out


# ── «Детализация» ──────────────────────────────────────────────────────────────────────────

def _snapshot_moment(snapshot_id: Optional[int]) -> Optional[datetime]:
    if not snapshot_id:
        return None
    try:
        return datetime.strptime(str(int(snapshot_id)), '%Y%m%d%H%M%S')
    except ValueError:
        return None


def _detail_row(row, index: int, known: Dict[str, str]) -> Dict[str, Any]:
    started_at, queue, line_number, phone, call_type, talk = row
    park = lines_mod.park({'queue': queue or '', 'line_number': line_number or '', 'call_type': call_type}, known)
    return {
        'id': index,
        'occurred_at': started_at.strftime('%Y-%m-%d %H:%M:%S') if started_at else '',
        'park': park,
        'line': line_number or '',
        'driver_number': phone or '',
        'ivr_drop': 1 if call_type == IN_BEFORE_QUEUE else 0,
        'queue_drop': 1 if call_type == IN_LOST else 0,
        'talk_seconds': int(talk or 0) if call_type == IN_ANSWERED else 0,
    }


def _detail_sql(cursor, with_snapshot: bool) -> str:
    snapshot = " AND t.started_at <= %(snapshot)s" if with_snapshot else ''
    # Принятые и потерянные — только дошедшие до очереди, как «Поступило» и «Потеряно»
    # разрезов: иначе сумма строк расходилась с итогами «Таксопарков».
    return f"""
        FROM cdr_touches t
       WHERE t.call_day BETWEEN %(day_from)s AND %(day_to)s
         AND t.call_type IN (%(answered)s, %(lost)s, %(before)s)
         AND (t.call_type = %(before)s OR t.queue <> '')
         AND {_MINUTE_SQL}{_not_test(cursor)}{snapshot}
    """


def detail_page(cursor, day_from: date, day_to: date, minute_from: int, minute_to: int,
                page: int, per_page: int, snapshot_id: Optional[int] = None) -> Dict[str, Any]:
    """Одна строка — один входящий звонок, свежие сверху. Снимок — начало последнего звонка
    на первой странице: звонки, начатые позже, страницы не сдвигают. Строгого «замораживания»
    у «Касаний» нет — у них нет порядка записи, как Id у Oktell, а сегодняшние сутки мост
    перезаписывает целиком. Звонок, что шёл во время первой страницы, остаётся на месте своей
    строкой (мост пишет его частично и потом дописывает); сдвинуть страницу на одну строку
    может лишь звонок, который тогда ещё не вышел из автоинформатора."""
    params = {'answered': IN_ANSWERED, 'lost': IN_LOST, 'before': IN_BEFORE_QUEUE,
              'day_from': day_from, 'day_to': day_to,
              'minute_from': int(minute_from), 'minute_to': int(minute_to)}
    snapshot = _snapshot_moment(snapshot_id)
    if snapshot is None:
        cursor.execute("SELECT MAX(t.started_at) " + _detail_sql(cursor, False), params)
        latest = (cursor.fetchone() or [None])[0]
        snapshot = latest
    params['snapshot'] = snapshot
    with_snapshot = snapshot is not None
    cursor.execute("SELECT COUNT(*) " + _detail_sql(cursor, with_snapshot), params)
    total = int((cursor.fetchone() or [0])[0] or 0)
    page, per_page = max(1, int(page)), max(1, int(per_page))
    params.update({'limit': per_page, 'offset': (page - 1) * per_page})
    cursor.execute(
        f"SELECT t.started_at, t.queue, t.line_number, t.phone, t.call_type, {_TALK_SQL} "
        + _detail_sql(cursor, with_snapshot)
        + " ORDER BY t.started_at DESC, t.linkedid DESC LIMIT %(limit)s OFFSET %(offset)s",
        params,
    )
    rows = cursor.fetchall()
    known = known_lines(cursor, day_from) if rows else {}
    offset = (page - 1) * per_page
    return {
        'rows': [_detail_row(row, offset + index + 1, known) for index, row in enumerate(rows)],
        'snapshot_id': int(snapshot.strftime('%Y%m%d%H%M%S')) if snapshot else None,
        'pagination': {'page': page, 'per_page': per_page, 'total': total,
                       'total_pages': (total + per_page - 1) // per_page if total else 0},
    }


def detail_export_rows(cursor, day_from: date, day_to: date, minute_from: int, minute_to: int) -> List[Dict[str, Any]]:
    params = {'answered': IN_ANSWERED, 'lost': IN_LOST, 'before': IN_BEFORE_QUEUE,
              'day_from': day_from, 'day_to': day_to,
              'minute_from': int(minute_from), 'minute_to': int(minute_to)}
    cursor.execute(
        f"SELECT t.started_at, t.queue, t.line_number, t.phone, t.call_type, {_TALK_SQL} "
        + _detail_sql(cursor, False) + " ORDER BY t.started_at DESC, t.linkedid DESC",
        params,
    )
    rows = cursor.fetchall()
    known = known_lines(cursor, day_from) if rows else {}
    return [_detail_row(row, index + 1, known) for index, row in enumerate(rows)]


# ── «Группировка»: прогноз, план и факт людей по часам ───────────────────────────────────

def shift_rows(cursor, day_from: date, day_to: date, minute_from: int, minute_to: int,
               now: datetime) -> Dict[str, Dict[int, Dict[str, Optional[int]]]]:
    """{дата: {час: {forecast, planned, fact}}} — люди «Основы» в часе.

    Прогноз — людей в час по модели «Основы» (те же профили, что на вкладке «Прогнозы»);
    план — люди со сменой в «Графиках работы», хотя бы минута смены без перерывов в часе;
    факт — люди на линии по статусам iCORE Phone хотя бы минуту. Час, который ещё не
    наступил, по факту пустой (None), а не ноль: «данных ещё нет» — не «никто не работал»."""
    days = _days(day_from, day_to)
    rows: Dict[str, Dict[int, Dict[str, Optional[int]]]] = {
        day.isoformat(): {hour: {'forecast': None, 'planned': None, 'fact': None} for hour in range(24)}
        for day in days}

    settings = op.settings_tx(cursor)
    if settings.get('selected_direction_ids'):
        today = op._today_tx(cursor)
        data = op.load_data(cursor, day_from, day_to, settings, today)
        engine = op._engine_forecasts_tx(cursor, day_from, day_to) if settings['inbound_source'] == 'timesfm' else {}
        for day in days:
            profile = op.profile_for_date(data, day, settings, engine)
            for item in profile['hourly_profile']:
                rows[day.isoformat()][int(item['hour'])]['forecast'] = int(item.get('op_agents') or 0)

    roster = Roster(cursor, day_from, day_to, now)
    if not roster.operator_ids:
        return rows
    shifts = staffing_queries.scheduled_shifts(cursor, roster.operator_ids, day_from - timedelta(days=1), day_to)
    planned: Dict[Tuple[str, int], set] = defaultdict(set)
    for shift in shifts:
        if shift.get('shift_type') != inbound_staffing.REGULAR_SHIFT:
            continue
        if not roster.in_group(shift['operator_id'], shift['day']):
            continue
        start, end = inbound_staffing.shift_bounds(shift)
        midnight = datetime.combine(shift['day'], datetime.min.time())
        breaks = [(midnight + timedelta(minutes=b_start), midnight + timedelta(minutes=b_end))
                  for b_start, b_end in shift.get('breaks') or () if b_end > b_start]
        for day in days:
            for hour in range(24):
                hour_start = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)
                hour_end = hour_start + timedelta(hours=1)
                seconds = _clip(start, end, hour_start, hour_end)
                if seconds <= 0:
                    continue
                seconds -= sum(_clip(b_start, b_end, hour_start, hour_end) for b_start, b_end in breaks)
                if seconds >= FACT_MIN_SECONDS:
                    planned[(day.isoformat(), hour)].add(shift['operator_id'])

    fact: Dict[Tuple[str, int], set] = defaultdict(set)
    for operator_id in roster.operator_ids:
        intervals = inbound_staffing.online_intervals(
            roster.events.get(operator_id) or (), roster.moments.get(roster.exts.get(operator_id)) or ())
        if not intervals:
            continue
        for day in days:
            if not roster.in_group(operator_id, day):
                continue
            for hour in range(24):
                hour_start = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)
                if inbound_staffing.overlap_seconds(intervals, hour_start, hour_start + timedelta(hours=1)) >= FACT_MIN_SECONDS:
                    fact[(day.isoformat(), hour)].add(operator_id)

    for day in days:
        day_key = day.isoformat()
        for hour in range(24):
            hour_start = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)
            slot = rows[day_key][hour]
            slot['planned'] = len(planned.get((day_key, hour), ()))
            slot['fact'] = len(fact.get((day_key, hour), ())) if hour_start < now else None
    return rows


# ── комментарии «Группировки» «Основы» ─────────────────────────────────────────────────────

def _comments_ready(cursor) -> bool:
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (COMMENTS_TABLE,))
    row = cursor.fetchone()
    return bool(row and row[0])


def hour_comments(cursor, day_from: date, day_to: date) -> Dict[str, Dict[int, Dict[str, Any]]]:
    """{'ГГГГ-ММ-ДД': {час: {comment, author, updated_at}}} — как у линии."""
    if not _comments_ready(cursor):
        return {}
    cursor.execute(
        f"""
        SELECT c.report_date, c.hour, c.comment, u.name, c.updated_at
          FROM {COMMENTS_TABLE} c
          LEFT JOIN users u ON u.id = c.updated_by
         WHERE c.report_date BETWEEN %s AND %s
         ORDER BY c.report_date, c.hour
        """,
        (day_from, day_to),
    )
    out: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for report_date, hour, comment, author, updated_at in cursor.fetchall():
        out.setdefault(report_date.isoformat(), {})[int(hour)] = {
            'comment': comment or '', 'author': author or '',
            'updated_at': updated_at.isoformat() if updated_at else None,
        }
    return out


def save_hour_comment(db, report_date: date, hour_from: int, hour_to: int, comment: str,
                      updated_by: Optional[int] = None, previous_hour_from: Optional[int] = None,
                      previous_hour_to: Optional[int] = None) -> Dict[int, Dict[str, Any]]:
    """Комментарий на отрезок часов дня; пустой — снять. Правила — как у линии
    (Database.save_oktell_billing_hour_comment), таблица — своя."""
    if not op.ensure_schema_db(db):
        raise RuntimeError('Таблица комментариев «Основы ОП» недоступна — попробуйте позже')
    with db._get_cursor() as cursor:
        if previous_hour_from is not None and previous_hour_to is not None:
            cursor.execute(f"DELETE FROM {COMMENTS_TABLE} WHERE report_date = %s AND hour BETWEEN %s AND %s",
                           (report_date, int(previous_hour_from), int(previous_hour_to)))
        if comment:
            cursor.execute(
                f"""
                INSERT INTO {COMMENTS_TABLE} (report_date, hour, comment, updated_by)
                SELECT %s, h, %s, %s FROM generate_series(%s::int, %s::int) AS h
                ON CONFLICT (report_date, hour) DO UPDATE
                   SET comment = EXCLUDED.comment, updated_by = EXCLUDED.updated_by,
                       updated_at = CURRENT_TIMESTAMP
                """,
                (report_date, comment, updated_by, int(hour_from), int(hour_to)),
            )
        else:
            cursor.execute(f"DELETE FROM {COMMENTS_TABLE} WHERE report_date = %s AND hour BETWEEN %s AND %s",
                           (report_date, int(hour_from), int(hour_to)))
        return hour_comments(cursor, report_date, report_date).get(report_date.isoformat()) or {}


# ── «Звонки» и «Обзор»: смены «Основы» по графику в часах дня ──────────────────────────────

def planned_hourly_fte(cursor, direction_ids: Sequence[int], day_from: date, day_to: date) -> Dict[str, List[float]]:
    """{дата: [24 FTE]} — часы смен «Основы» по «Графикам работы» (без перерывов), как
    `_shift_hourly_fte_tx` линии, но одним запросом на весь отрезок: у «Обзора» их месяц."""
    out = {day.isoformat(): [0.0] * 24 for day in _days(day_from, day_to)}
    if not direction_ids:
        return out
    cursor.execute(
        """
        SELECT ws.id, ws.shift_date, ws.start_time, ws.end_time
          FROM work_shifts ws
          JOIN users u ON u.id = ws.operator_id
         WHERE ws.shift_date BETWEEN %s AND %s
           AND u.role = 'operator'
           AND COALESCE(u.status, 'working') = 'working'
           AND u.direction_id = ANY(%s)
        """,
        (day_from - timedelta(days=1), day_to, list(direction_ids)),
    )
    shifts = cursor.fetchall()
    if not shifts:
        return out
    cursor.execute("SELECT shift_id, start_minutes, end_minutes FROM shift_breaks WHERE shift_id = ANY(%s)",
                   ([row[0] for row in shifts],))
    breaks: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
    for shift_id, start_minutes, end_minutes in cursor.fetchall():
        breaks[shift_id].append((int(start_minutes or 0), int(end_minutes or 0)))
    for shift_id, shift_date, start_time, end_time in shifts:
        midnight = datetime.combine(shift_date, datetime.min.time())
        start = midnight + timedelta(hours=start_time.hour, minutes=start_time.minute)
        end = midnight + timedelta(hours=end_time.hour, minutes=end_time.minute)
        if end <= start:
            end += timedelta(days=1)
        shift_breaks = [(midnight + timedelta(minutes=b_start), midnight + timedelta(minutes=b_end))
                        for b_start, b_end in breaks.get(shift_id, ()) if b_end > b_start]
        for day_key, hours in out.items():
            day = datetime.strptime(day_key, '%Y-%m-%d')
            for hour in range(24):
                hour_start = day + timedelta(hours=hour)
                hour_end = hour_start + timedelta(hours=1)
                seconds = _clip(start, end, hour_start, hour_end)
                if seconds <= 0:
                    continue
                seconds -= sum(_clip(b_start, b_end, hour_start, hour_end) for b_start, b_end in shift_breaks)
                hours[hour] += max(0, seconds) / 3600.0
    return out


def day_hour_extras(cursor, day: date) -> Dict[int, Dict[str, float]]:
    """{час: {wait_sum, wait_count, greeting}} — ожидание принятых и сброс до очереди за день."""
    cursor.execute(
        f"""
        SELECT EXTRACT(HOUR FROM t.started_at)::int,
               COALESCE(SUM({_WAIT_SQL}) FILTER (WHERE t.call_type = %(answered)s AND t.queue <> ''), 0),
               COUNT(*) FILTER (WHERE t.call_type = %(answered)s AND t.queue <> ''),
               COUNT(*) FILTER (WHERE t.call_type = %(before)s)
          FROM cdr_touches t
         WHERE t.call_day = %(day)s AND t.call_type IN (%(answered)s, %(before)s){_not_test(cursor)}
         GROUP BY 1
        """,
        {'answered': IN_ANSWERED, 'before': IN_BEFORE_QUEUE, 'day': day},
    )
    return {int(hour): {'wait_sum': float(wait or 0), 'wait_count': float(count or 0), 'greeting': float(greet or 0)}
            for hour, wait, count, greet in cursor.fetchall()}
