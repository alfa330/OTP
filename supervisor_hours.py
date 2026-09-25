"""Часы супервайзеров по отметкам Clockster (задача #352).

СВ не сидят на линии: статусов телефонии у них нет, и операторская формула
«смена × рабочие статусы» даёт им ноль. Поэтому для СВ отделов из
`SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES` (сейчас только отдел продаж)
отработанное время берётся из отметок терминала Clockster: сумма отрезков
«приход → уход» за день минус перерыв СВ, который задаёт РОП (по умолчанию час).

Модуль без базы и без сети: только сведение отметок в часы, чтобы правило
проверялось тестами на живых последовательностях.

Почему пары собираются здесь, а не берутся готовыми `in`/`out` из клетки
Clockster, как в разделе «Отметки»: клетка Clockster — календарный день. У СВ
ночные смены 21:00 → 08:00, и без графика в Clockster такая смена рвётся на
два дня, в каждом из которых нет пары. Отсюда и собственное сведение по всей
ленте отметок человека подряд.

Главная ловушка источника, из-за которой Clockster в августе убрали у
операторов, — «висящие приходы»: терминал один на вход и выход, уход иногда не
отмечен или угадан как приход. Поэтому:
  * уход без прихода и приход без ухода не дают часов — уход не придумываем;
  * второй приход подряд — повторное касание, только если он в пределах
    `REPEAT_TOUCH_SECONDS`; позже — прежний приход был зависшим (или уходом,
    который терминал угадал приходом), и счёт идёт от нового. Иначе лишний
    «приход» вечером съедал бы следующую смену целиком;
  * отрезок длиннее `MAX_SESSION_SECONDS` отбрасывается, а не превращается в
    сутки работы.
"""

from collections import namedtuple
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Almaty")

# Отделы, у СВ которых часы считаются по Clockster. Коды `departments.code`.
SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES = ('op',)
# Перерыв СВ, пока РОП не задал свой.
DEFAULT_BREAK_MINUTES = 60
BREAK_MINUTES_MAX = 240
# Самая длинная правдоподобная смена СВ. Длиннее — зависший приход.
MAX_SESSION_SECONDS = 16 * 3600
# Второй приход раньше этого — то же касание терминала, позже — новый приход.
REPEAT_TOUCH_SECONDS = 30 * 60

MARK_IN = 'in'
MARK_OUT = 'out'

# Отметка ленты: время, приход/уход, откуда (clockster | manual) и id ручной.
Mark = namedtuple('Mark', 'at kind source mark_id')

# Почему отметка не вошла ни в одну пару — показывается РОП в окне дня.
IGNORED_UNPAIRED_IN = 'unpaired_in'      # приход без ухода
IGNORED_UNPAIRED_OUT = 'unpaired_out'    # уход без прихода
IGNORED_REPEAT_TOUCH = 'repeat_touch'    # повторное касание терминала
IGNORED_TOO_LONG = 'too_long'            # отрезок длиннее MAX_SESSION_SECONDS


def parse_mark_time(value):
    """Время отметки → aware datetime в часах Алматы. Без смещения — местное."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or '').strip()
        if not text:
            return None
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def normalize_marks(raw_marks):
    """Отметки (`{'at', 'kind', 'source'?, 'id'?}`) → [Mark] по порядку.

    Одна и та же отметка Clockster лежит в строках двух соседних дней, когда
    период собирается с запасом, поэтому дубли снимаются по (время, тип,
    источник). Ручную отметку РОП с тем же временем не сливаем с отметкой
    терминала: окно дня должно показать обе."""
    seen = set()
    marks = []
    for mark in raw_marks or []:
        if not isinstance(mark, dict):
            continue
        kind = str(mark.get('kind') or '').strip().lower()
        if kind not in (MARK_IN, MARK_OUT):
            continue
        when = parse_mark_time(mark.get('at'))
        if when is None:
            continue
        source = 'manual' if str(mark.get('source') or '').strip().lower() == 'manual' else 'clockster'
        key = (when, kind, source)
        if key in seen:
            continue
        seen.add(key)
        marks.append(Mark(when, kind, source, mark.get('id') if source == 'manual' else None))
    # При равном времени уход раньше прихода: «ушёл и тут же вернулся» не
    # должно закрыть новую пару нулевым отрезком.
    marks.sort(key=lambda item: (item.at, 0 if item.kind == MARK_OUT else 1))
    return marks


def mark_key(at, kind):
    """Ключ отметки для исправлений РОП: время (по Алматы) и тип."""
    when = parse_mark_time(at)
    return (when, str(kind or '').strip().lower()) if when is not None else None


def drop_replaced(raw_marks, replaced_keys):
    """Убрать отметки Clockster, которые РОП исправил: вместо них в ленте стоит
    ручная отметка-исправление, а исходная в пары не идёт."""
    replaced = set(replaced_keys or ())
    if not replaced:
        return list(raw_marks or [])
    kept = []
    for mark in raw_marks or []:
        if isinstance(mark, dict) and str(mark.get('source') or '').strip().lower() != 'manual':
            if mark_key(mark.get('at'), mark.get('kind')) in replaced:
                continue
        kept.append(mark)
    return kept


def pair_marks(marks, max_session_seconds=MAX_SESSION_SECONDS,
               repeat_touch_seconds=REPEAT_TOUCH_SECONDS):
    """Лента [Mark] → (пары [(i_прихода, i_ухода)], отброшенные [(i, причина)]).

    Второй приход в пределах `repeat_touch_seconds` — повторное касание
    терминала: остаётся первый. Позже — открытый приход был зависшим, счёт
    начинается с нового: так вечерний лишний «приход» не съедает следующую
    смену, а пропущенный уход не превращается в сутки работы."""
    sessions = []
    ignored = []
    opened = None
    for index, mark in enumerate(marks):
        if mark.kind == MARK_IN:
            if opened is None:
                opened = index
            elif (mark.at - marks[opened].at).total_seconds() > repeat_touch_seconds:
                ignored.append((opened, IGNORED_UNPAIRED_IN))
                opened = index
            else:
                ignored.append((index, IGNORED_REPEAT_TOUCH))
            continue
        if opened is None:
            ignored.append((index, IGNORED_UNPAIRED_OUT))
            continue
        span = (mark.at - marks[opened].at).total_seconds()
        if 0 < span <= max_session_seconds:
            sessions.append((opened, index))
        else:
            ignored.append((opened, IGNORED_TOO_LONG))
            ignored.append((index, IGNORED_TOO_LONG))
        opened = None
    if opened is not None:
        ignored.append((opened, IGNORED_UNPAIRED_IN))
    return sessions, ignored


def pair_sessions(marks, max_session_seconds=MAX_SESSION_SECONDS,
                  repeat_touch_seconds=REPEAT_TOUCH_SECONDS):
    """Лента [Mark] → отрезки присутствия [(приход, уход)] (см. pair_marks)."""
    sessions, _ignored = pair_marks(marks, max_session_seconds, repeat_touch_seconds)
    return [(marks[start].at, marks[end].at) for start, end in sessions]


def presence_by_day(sessions):
    """{день прихода: секунд присутствия}. Смена через полночь целиком
    относится ко дню своего начала — как смены в графике проекта."""
    totals = {}
    for start, end in sessions:
        day = start.astimezone(TZ).date()
        totals[day] = totals.get(day, 0) + int((end - start).total_seconds())
    return totals


def normalize_break_minutes(value, default=DEFAULT_BREAK_MINUTES):
    """Минуты перерыва СВ: целое 0..BREAK_MINUTES_MAX, пусто → по умолчанию."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return int(default)
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError('Время перерыва — целое число минут')
    if minutes < 0 or minutes > BREAK_MINUTES_MAX:
        raise ValueError(f'Время перерыва — от 0 до {BREAK_MINUTES_MAX} минут')
    return minutes


def day_hours(presence_seconds, break_minutes):
    """(отработано, вычтенный перерыв) в секундах за день.

    Перерыв вычитается раз в день и не больше присутствия: иначе короткий день
    ушёл бы в минус."""
    presence = max(0, int(presence_seconds or 0))
    break_seconds = min(presence, max(0, int(break_minutes or 0)) * 60)
    return presence - break_seconds, break_seconds


def supervisor_day_hours(raw_marks, break_minutes, max_session_seconds=MAX_SESSION_SECONDS):
    """Отметки человека → {день: (отработано сек, перерыв сек, присутствие сек)}."""
    sessions = pair_sessions(normalize_marks(raw_marks), max_session_seconds)
    result = {}
    for day, presence in presence_by_day(sessions).items():
        worked, deducted = day_hours(presence, break_minutes)
        result[day] = (worked, deducted, presence)
    return result


def explain_day(raw_marks, day, break_minutes, replaced_keys=None):
    """Разбор одного дня для окна РОП: какие отметки вошли в пары и почему
    остальные не засчитаны, отрезки дня, присутствие, перерыв и итог.

    Пары строятся по всей переданной ленте (день накануне и следующий нужны:
    уход ночной смены лежит завтра, а утренний уход закрывает смену накануне).
    Показываются отметки самого дня и уход следующего дня, закрывающий смену,
    начатую в этот день. Отметки Clockster, исправленные РОП (replaced_keys),
    показываются со статусом 'replaced' и в пары не идут."""
    replaced = set(replaced_keys or ())
    everything = normalize_marks(raw_marks)
    marks = [m for m in everything if not (m.source == 'clockster' and (m.at, m.kind) in replaced)]
    replaced_rows = [m for m in everything if m.source == 'clockster' and (m.at, m.kind) in replaced]
    sessions, ignored = pair_marks(marks)
    reasons = dict(ignored)
    session_of = {}
    for start, end in sessions:
        session_of[start] = (start, end)
        session_of[end] = (start, end)
    day_sessions = [(start, end) for start, end in sessions if marks[start].at.date() == day]
    shown = {index for index, mark in enumerate(marks) if mark.at.date() == day}
    shown.update(end for _start, end in day_sessions)
    rows = []
    for index in sorted(shown):
        mark = marks[index]
        if index in reasons:
            status = 'ignored'
        elif index in session_of and marks[session_of[index][0]].at.date() != day:
            status = 'previous_day'     # закрывает смену, начатую накануне
        else:
            status = 'used'
        rows.append({
            'at': mark.at.isoformat(),
            'kind': mark.kind,
            'source': mark.source,
            'id': mark.mark_id,
            'status': status,
            'reason': reasons.get(index),
            'next_day': mark.at.date() > day,
        })
    for mark in replaced_rows:
        if mark.at.date() != day:
            continue
        rows.append({
            'at': mark.at.isoformat(), 'kind': mark.kind, 'source': mark.source, 'id': None,
            'status': 'replaced', 'reason': None, 'next_day': False,
        })
    rows.sort(key=lambda row: (row['at'], 0 if row['kind'] == MARK_OUT else 1))
    presence = sum(int((marks[end].at - marks[start].at).total_seconds()) for start, end in day_sessions)
    worked, deducted = day_hours(presence, break_minutes)
    return {
        'marks': rows,
        'sessions': [
            {'start': marks[start].at.isoformat(), 'end': marks[end].at.isoformat(),
             'seconds': int((marks[end].at - marks[start].at).total_seconds())}
            for start, end in day_sessions
        ],
        'presence_seconds': presence,
        'break_seconds': deducted,
        'worked_seconds': worked,
    }


def iter_days(date_from, date_to):
    day = date_from
    while day <= date_to:
        yield day
        day += timedelta(days=1)


__all__ = [
    'SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES', 'DEFAULT_BREAK_MINUTES',
    'BREAK_MINUTES_MAX', 'MAX_SESSION_SECONDS', 'REPEAT_TOUCH_SECONDS', 'parse_mark_time',
    'Mark', 'mark_key', 'drop_replaced', 'normalize_marks', 'pair_marks', 'pair_sessions', 'presence_by_day',
    'normalize_break_minutes', 'day_hours', 'supervisor_day_hours', 'explain_day', 'iter_days',
]
