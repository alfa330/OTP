# -*- coding: utf-8 -*-
"""«CR%» и «ОСС» «Отчёта по входу»: смены по графику против времени на линии.

Правила — владельца, 12.11.2026: «план — это смены, которые проставлены в графиках
работы, а факт — это то, что операторы были на линии или нет. Если статусы
соответствуют графику не меньше чем на 90 %, то факт засчитывается. OCC так же нужен».

Чьи смены
---------
Отчёт — по входящей линии, а её ведёт группа «Основа» (модель расчёта `op_osnova`):
остальные группы ОП работают исходящими. Состав — на дату смены: оператор, которого
в этот день уже перевели в другую группу, в план линии не входит (то же правило
членства, что у «Активности операторов», `activity.group_on`).

CR% — доля отработанных смен
----------------------------
    CR% = смены, отработанные по графику ÷ смены, поставленные на день в «Графиках работы»

Смена отработана, если

    время на линии внутри смены ÷ (длительность смены − перерывы по графику) ≥ 90 %

Перерывы по графику вычитаются из знаменателя: оператор, который отработал смену
день в день и сходил на свои перерывы, был на линии 100 % положенного, а не 83 %.
Больше положенного не бывает — доля срезается на 100 %.

Считаются только обычные смены: «Практика в офисе» и «Смена на телефонах» —
это смены, в которые человека на линии нет по самому графику. Смена, которая ещё не
закончилась, в счёт не идёт ни планом, ни фактом — иначе утром сегодняшний CR был бы
нулём просто потому, что день не прошёл.

ОСС — занятость
---------------
    ОСС = время в разговоре и в наборе номера ÷ всё время на линии

За календарные сутки, по операторам «Основы» этого дня, со сменой в графике или без:
занятость — про время, которое человек реально провёл на линии.

Что такое «на линии»
--------------------
Статусы iCORE Phone, а не сохранённые отрезки «Учёта часов»: у отрезков нет защиты от
потерянного «выключен» (последний статус тянется до утра) и от телефона, который
возвращает статус сам после сна компьютера. Нарезка и чистка — те же, что у отчёта
«Активность операторов» (`op_wallboard.activity`): на линии — ожидание звонка, набор и
разговор; перерыв, технический перерыв, тренинг и офлайн — нет.

Модуль чистый: ни базы, ни Flask.
"""

from bisect import bisect_right
from datetime import datetime, timedelta

from op_wallboard import activity as activity_mod

GROUP_MODEL = 'op_osnova'
GROUP_KEY = activity_mod.GROUP_MODEL_KEYS[GROUP_MODEL]

# «Не меньше чем на 90 %» — порог владельца.
ADHERENCE_MIN = 0.9

# Обычная смена. «Практика в офисе» и «Смена на телефонах» — не про линию.
REGULAR_SHIFT = 'regular'

ON_LINE_KINDS = frozenset({activity_mod.KIND_WAIT, activity_mod.KIND_DIAL, activity_mod.KIND_TALK})
BUSY_KINDS = frozenset({activity_mod.KIND_DIAL, activity_mod.KIND_TALK})

_DAY = timedelta(days=1)


def online_intervals(events, call_moments=()):
    """[(начало, конец, занят звонком)] — когда оператор был на линии, по возрастанию.

    events — [(время, ключ статуса)] живых событий его телефона; call_moments —
    отсортированные начала его звонков на станции (`activity.human_call_moments`):
    по ним видно, что у телефона был человек."""
    out = []
    for shift in activity_mod.split_shifts(events, call_moments):
        for kind, _base, start, end, _seconds in activity_mod.pieces(shift, GROUP_KEY):
            if kind in ON_LINE_KINDS:
                out.append((start, end, kind in BUSY_KINDS))
    return out


def overlap_seconds(intervals, start, end, busy_only=False):
    """Сколько секунд из `intervals` лежит внутри [start, end)."""
    if not intervals or end <= start:
        return 0
    starts = [item[0] for item in intervals]
    total = 0.0
    # Отрезки не пересекаются и идут по времени: начинаем с того, что накрывает `start`.
    for index in range(max(0, bisect_right(starts, start) - 1), len(intervals)):
        begin, finish, busy = intervals[index]
        if begin >= end:
            break
        if finish <= start or (busy_only and not busy):
            continue
        total += (min(finish, end) - max(begin, start)).total_seconds()
    return int(total)


def shift_bounds(shift):
    """(начало, конец) смены. `end_time` не позже начала — смена через полночь."""
    start = datetime.combine(shift['day'], shift['start_time'])
    end = datetime.combine(shift['day'], shift['end_time'])
    if end <= start:
        end += _DAY
    return start, end


def planned_seconds(shift):
    """Сколько по графику оператор должен быть на линии: смена без своих перерывов.

    Перерывы лежат минутами от полуночи дня смены; у ночной смены перерыв после
    полуночи записан числом больше 1440, а если записан «по часам» (120 вместо 1560) —
    переносится на следующие сутки. Перерыв режется границами смены."""
    start, end = shift_bounds(shift)
    midnight = datetime.combine(shift['day'], datetime.min.time())
    breaks = 0.0
    for begin_min, finish_min in shift.get('breaks') or ():
        begin = midnight + timedelta(minutes=int(begin_min))
        finish = midnight + timedelta(minutes=int(finish_min))
        if finish <= start:
            begin, finish = begin + _DAY, finish + _DAY
        begin, finish = max(begin, start), min(finish, end)
        if finish > begin:
            breaks += (finish - begin).total_seconds()
    return max(0, int((end - start).total_seconds() - breaks))


def shift_adherence(shift, intervals):
    """Доля положенного времени, которую оператор был на линии: 0…1, None — считать не из чего."""
    planned = planned_seconds(shift)
    if planned <= 0:
        return None
    start, end = shift_bounds(shift)
    return min(1.0, overlap_seconds(intervals, start, end) / planned)


def _in_group(memberships, operator_id, day):
    return activity_mod.group_on(memberships, operator_id, day)[0] == GROUP_KEY


def day_staffing(days, *, shifts, intervals_by_operator, memberships, now,
                 adherence_min=ADHERENCE_MIN):
    """{день: {shifts_plan, shifts_fact, cr, online_seconds, busy_seconds, occ}}.

    shifts — [{operator_id, day, start_time, end_time, shift_type, breaks}] из графиков;
    intervals_by_operator — {operator_id: online_intervals(...)};
    memberships — {operator_id: [{start_date, end_date, group_id, model, id}]};
    now — «сейчас» местным временем: по нему отсекаются незаконченные смены.

    `cr` и `occ` — None, когда делить не на что: смен в графике нет или на линии никого
    не было. Пустая клетка честнее нуля — ноль значил бы «никто не вышел»."""
    wanted = set(days)
    out = {day: {'shifts_plan': 0, 'shifts_fact': 0, 'online_seconds': 0, 'busy_seconds': 0}
           for day in wanted}

    for shift in shifts or ():
        day = shift['day']
        if day not in wanted or shift.get('shift_type', REGULAR_SHIFT) != REGULAR_SHIFT:
            continue
        operator_id = shift['operator_id']
        if not _in_group(memberships, operator_id, day):
            continue
        if shift_bounds(shift)[1] > now:
            continue
        adherence = shift_adherence(shift, (intervals_by_operator or {}).get(operator_id) or ())
        if adherence is None:
            continue
        out[day]['shifts_plan'] += 1
        if adherence >= adherence_min:
            out[day]['shifts_fact'] += 1

    for operator_id, intervals in (intervals_by_operator or {}).items():
        for day in wanted:
            if not _in_group(memberships, operator_id, day):
                continue
            start = datetime.combine(day, datetime.min.time())
            out[day]['online_seconds'] += overlap_seconds(intervals, start, start + _DAY)
            out[day]['busy_seconds'] += overlap_seconds(intervals, start, start + _DAY,
                                                        busy_only=True)

    for stats in out.values():
        stats['cr'] = (stats['shifts_fact'] / stats['shifts_plan']) if stats['shifts_plan'] else None
        stats['occ'] = (stats['busy_seconds'] / stats['online_seconds']
                        if stats['online_seconds'] else None)
    return out
