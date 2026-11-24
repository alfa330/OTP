"""Статусы операторов Syntony: забор интервалов из API истории чатов и правила присутствия.

Метод /operator-statuses (документ Syntony, прислан владельцем 24.11.2026) отдаёт интервалы
«оператор был в таком-то статусе с … по …»: endedAt null — его текущий статус; нет открытого
интервала — оператор не в сети. Ключ тот же, что у истории чатов (SYNTONY_HISTORY_API_KEY).
Ключи статусов: available — онлайн, busy — занят, technical — техническая причина,
offline_work — офлайн работа, training — тренинг, break — перерыв.

Решение владельца 24.11.2026: линию держит только «Онлайн» (available) — как было с
Chat2Desk; занят, тренинг, перерыв, техническая причина и офлайн-работа в людей на линии не
идут, но на табло видны справа точным статусом.

Сторож (sync.py) зовёт pull() каждым своим прогоном: интервалы с последней отметки (и с начала
самого раннего ещё открытого — пока он открыт, Syntony его переписывает), сутками-срезами, все
страницы среза. Срез дочитан — открытые у нас интервалы этого среза, которых Syntony больше не
отдала, закрываются: иначе ушедший человек висел бы на табло онлайн. Срез без единого интервала
ничего не закрывает: при живой Syntony интервалы, начатые в срезе, приходят всегда, и пустой
ответ — сбой, а не «все ушли». Отметка «дочитано до» назад не ходит (перечитка с давнего
открытого интервала её не трогает), а «статусы есть с» ставится один раз, когда первый забор
догнал «сейчас», — по самому раннему полученному интервалу, а не по началу окна: история у
Syntony может начинаться с выкладки метода, и часы до неё считаются по ответам, а не нулями. Метода нет (404 — так было
24.11.2026: в документе он есть, на сервере ещё нет) — сторож пробует снова через 15 минут, а
табло считает онлайн по ответам за 15 минут, как до статусов.

Правила присутствия — чистые функции ниже: сопоставление с сотрудником iCORE (partnerUserIds —
наш id, иначе ФИО тем же правилом, что ответы), текущий статус человека и секунды «Онлайн» по
часам для графика табло и факта сотрудников биллинга.
"""

from dataclasses import dataclass
from datetime import timedelta

from psycopg2.extras import Json

from . import client, rules, store

LINE_STATUS = 'available'
# Подписи — на случай, когда Syntony не прислала свою (её label показывается как есть).
STATUS_LABELS = {
    'available': 'Онлайн',
    'busy': 'Занят',
    'technical': 'Техническая причина',
    'offline_work': 'Офлайн работа',
    'training': 'Тренинг',
    'break': 'Перерыв',
}
# Несколько открытых интервалов одного человека (разные парки сети) — главным считается
# более «рабочий»: онлайн в одном парке значит, что человек на линии.
STATUS_RANK = {'available': 0, 'busy': 1, 'technical': 2, 'offline_work': 3, 'training': 4,
               'break': 5}
OTHER_RANK = 9

MARK_STATUSES = 'statuses_since'
MARK_STATUSES_FROM = 'statuses_from'      # с какого момента статусы вообще есть (первый забор)

PAGE = 1000
PAGES_PER_SLICE = 20
SLICE = timedelta(days=1)
SLICES_PER_RUN = 8                        # первый забор (неделя назад) — за один прогон
PAUSE_SECONDS = 1.1
OVERLAP = timedelta(minutes=5)
# Первый забор: неделя назад — хватает, чтобы собрать часы на линии биллинга с 18.11, если
# Syntony хранит историю статусов; меньше, если не хранит, — тогда что отдаст.
FIRST_LOOKBACK = timedelta(days=7)
MAX_AGE = timedelta(days=30)              # from старше 31 дня не принимает
# Открытый интервал старше недели — забытый, а не текущий: перечитывать с него не будем.
OPEN_LOOKBACK = timedelta(days=7)
# Отказ метода (нет его, нет прав) — повтор не чаще, чем раз в четверть часа.
UNAVAILABLE_RETRY = timedelta(minutes=15)


# ── Разбор интервала ──────────────────────────────────────────────────────────────────────

INTERVAL_FIELDS = {
    'id': None,
    'operator': {'id': None, 'fullName': None, 'partnerUserIds': None},
    'park': {'id': None, 'name': None},
    'status': {'key': None, 'label': None},
    'startedAt': None, 'endedAt': None, 'durationSeconds': None,
}


def _partner_ids(value):
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = rules._text(item)
        if text and text not in out:
            out.append(text[:64])
    return out[:20]


def interval_row(item, fetched_at):
    """Интервал из API → строка syntony_operator_statuses. Без id или начала — None."""
    if not isinstance(item, dict):
        return None
    data = rules.clean(item)
    interval_id = rules._text(data.get('id'))
    started_at = rules.parse_time(data.get('startedAt'))
    if not interval_id or started_at is None:
        return None
    operator = rules._obj(data.get('operator'))
    park = rules._obj(data.get('park'))
    status = rules._obj(data.get('status'))
    key = (rules._text(status.get('key')) or '').lower() or None
    return {
        'id': interval_id,
        'operator_id': rules._text(operator.get('id')),
        'operator_name': rules._text(operator.get('fullName')),
        'partner_user_ids': _partner_ids(operator.get('partnerUserIds')),
        'park_id': rules._text(park.get('id')),
        'park_name': rules._text(park.get('name')),
        'status_key': key,
        'status_label': rules._text(status.get('label')),
        'started_at': started_at,
        'ended_at': rules.parse_time(data.get('endedAt')),
        'duration_seconds': rules._int(data.get('durationSeconds')),
        'extra': rules.leftover(data, INTERVAL_FIELDS),
        'fetched_at': fetched_at,
    }


# ── База ──────────────────────────────────────────────────────────────────────────────────

_COLUMNS = ('id', 'operator_id', 'operator_name', 'partner_user_ids', 'park_id', 'park_name',
            'status_key', 'status_label', 'started_at', 'ended_at', 'duration_seconds', 'extra',
            'fetched_at')
_CONTENT = _COLUMNS[1:-1]

# Перезапись только если что-то поменялось: открытый интервал приходит каждые 3 минуты.
_UPSERT_SQL = """
    INSERT INTO syntony_operator_statuses AS s ({columns})
    VALUES ({placeholders})
    ON CONFLICT (id) DO UPDATE SET {updates}, fetched_at = EXCLUDED.fetched_at, updated_at = now()
     WHERE ({old}) IS DISTINCT FROM ({new})
""".format(
    columns=', '.join(_COLUMNS),
    placeholders=', '.join(['%s'] * len(_COLUMNS)),
    updates=', '.join('{0} = EXCLUDED.{0}'.format(name) for name in _CONTENT),
    old=', '.join('s.' + name for name in _CONTENT),
    new=', '.join('EXCLUDED.' + name for name in _CONTENT),
)


def upsert(cursor, row):
    values = [Json(row[name]) if name == 'extra' and row[name] is not None else row[name]
              for name in _COLUMNS]
    cursor.execute(_UPSERT_SQL, values)
    return cursor.rowcount > 0


def write(db, items, fetched_at, seen):
    """Страница интервалов → база одной транзакцией → записано. seen пополняется id."""
    written = 0
    with db._get_cursor() as cursor:
        for item in items:
            row = interval_row(item, fetched_at)
            if row is None:
                continue
            seen.add(row['id'])
            if upsert(cursor, row):
                written += 1
    return written


def close_absent(cursor, start, end, seen, closed_at):
    """Открытые у нас интервалы среза [start, end), которых Syntony в дочитанном срезе не
    отдала, — закрыть моментом прогона: человека в этом статусе уже нет."""
    cursor.execute("""
        UPDATE syntony_operator_statuses
           SET ended_at = %s,
               extra = COALESCE(extra, '{}'::jsonb) || '{"closed_by": "absent"}'::jsonb,
               updated_at = now()
         WHERE ended_at IS NULL AND started_at >= %s AND started_at < %s
           AND NOT (id = ANY(%s))
    """, (closed_at, start, end, sorted(seen)))
    return cursor.rowcount


def earliest_started(cursor):
    cursor.execute('SELECT min(started_at) FROM syntony_operator_statuses')
    row = cursor.fetchone()
    return row[0] if row else None


def oldest_open(cursor, since):
    cursor.execute("""
        SELECT min(started_at) FROM syntony_operator_statuses
         WHERE ended_at IS NULL AND started_at >= %s
    """, (since,))
    row = cursor.fetchone()
    return row[0] if row else None


def delete_old(cursor, cutoff, limit):
    cursor.execute("""
        DELETE FROM syntony_operator_statuses
         WHERE id IN (SELECT id FROM syntony_operator_statuses
                       WHERE ended_at IS NOT NULL AND ended_at < %s LIMIT %s)
    """, (cutoff, limit))
    return cursor.rowcount


_LOAD_SQL = """
    SELECT id, operator_id, operator_name, partner_user_ids, park_name, status_key,
           status_label, started_at, ended_at
      FROM syntony_operator_statuses
     WHERE started_at < %(end)s AND started_at >= %(floor)s
       AND (ended_at IS NULL OR ended_at > %(start)s)
"""


def load_window(cursor, start, end):
    """Интервалы, задевающие [start, end) → [Interval]."""
    cursor.execute(_LOAD_SQL, {'start': start, 'end': end, 'floor': start - OPEN_LOOKBACK})
    return [Interval(id=row[0], operator_id=row[1], operator_name=row[2],
                     partner_ids=tuple(row[3] or ()), park_name=row[4], key=row[5],
                     label=row[6], started_at=row[7], ended_at=row[8])
            for row in cursor.fetchall()]


def load_last_seen(cursor, since):
    """Когда кончился последний интервал каждого оператора Syntony (для «Не в системе с»)."""
    cursor.execute("""
        SELECT DISTINCT ON (operator_id) operator_id, operator_name, partner_user_ids, ended_at
          FROM syntony_operator_statuses
         WHERE ended_at IS NOT NULL AND ended_at >= %s
         ORDER BY operator_id, ended_at DESC
    """, (since,))
    return [Interval(id=None, operator_id=row[0], operator_name=row[1],
                     partner_ids=tuple(row[2] or ()), park_name=None, key=None, label=None,
                     started_at=None, ended_at=row[3])
            for row in cursor.fetchall()]


# ── Забор ─────────────────────────────────────────────────────────────────────────────────

def pull(db, config, *, now, marks, sleep, call, items_of, utcnow, day_start, session=None):
    """Шаг сторожа: интервалы с отметки → (записано, страниц, закрыто отсутствующих).

    call/items_of/utcnow/day_start — обвязка sync.py (повтор после 429, разбор страницы),
    чтобы правила отказов были одни на все методы.
    """
    mark = marks.get(MARK_STATUSES)
    with db._get_cursor() as cursor:
        open_since = oldest_open(cursor, now - OPEN_LOOKBACK)
    start = (mark - OVERLAP) if mark is not None else (day_start(now) - FIRST_LOOKBACK)
    if open_since is not None:
        start = min(start, open_since)
    start = max(start, now - MAX_AGE)
    origin_known = marks.get(MARK_STATUSES_FROM) is not None
    written = pages = closed = slices = 0
    while start < now and slices < SLICES_PER_RUN:
        end = min(start + SLICE, now)
        seen, token, complete = set(), None, False
        for _page in range(PAGES_PER_SLICE):
            if pages:
                sleep(PAUSE_SECONDS)
            fetched_at = utcnow()
            page = call(lambda: client.operator_statuses_page(
                config, start, end, token, PAGE, session=session), sleep)
            pages += 1
            written += write(db, items_of(page), fetched_at, seen)
            token = page.get('nextCursor')
            if not token:
                complete = True
                break
        if not complete:
            # Срез не уместился в потолок страниц — отметку не двигаем, дочитает следующий.
            break
        with db._get_cursor() as cursor:
            if seen:
                closed += close_absent(cursor, start, end, seen, end)
            # Перечитка с давнего открытого интервала идёт срезами из прошлого: отметка остаётся
            # прежней, пока срезы её не догонят, — иначе сбой на втором срезе оставил бы её в
            # прошлом, и табло на минуты потеряло бы статусы.
            if mark is None or end > mark:
                store.set_mark(cursor, MARK_STATUSES, end)
                mark = end
        start = end
        slices += 1
    if not origin_known and start >= now:
        with db._get_cursor() as cursor:
            store.set_mark(cursor, MARK_STATUSES_FROM, earliest_started(cursor) or now)
    return written, pages, closed


# ── Присутствие ───────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Interval:
    id: str
    operator_id: str
    operator_name: str
    partner_ids: tuple
    park_name: str
    key: str
    label: str
    started_at: object
    ended_at: object


def label_of(interval):
    return interval.label or STATUS_LABELS.get(interval.key) or interval.key or 'Статус'


def matcher(roster, resolve):
    """→ match(интервал) → сотрудник iCORE {'id', 'name', 'status'} или None (не чатник СЗоВ).

    partnerUserIds — наш id учётки (его iCORE передаёт в окно чатов): совпал с чатником — это
    он. Иначе — ФИО Syntony тем же сопоставлением, что авторы ответов.
    """
    by_id = {}
    for operator in roster or []:
        try:
            by_id[int(operator['id'])] = operator
        except (KeyError, TypeError, ValueError):
            continue
    names = {}

    def match(interval):
        for partner_id in interval.partner_ids or ():
            try:
                operator = by_id.get(int(str(partner_id).strip()))
            except ValueError:
                continue
            if operator is not None:
                return operator
        name = interval.operator_name
        if not name:
            return None
        if name not in names:
            names[name] = resolve(name)
        return names[name]

    return match


def _open_at(interval, moment):
    return (interval.started_at is not None and interval.started_at <= moment
            and (interval.ended_at is None or interval.ended_at > moment))


def current(intervals, match, now):
    """Текущий статус чатников СЗоВ → {id сотрудника: {'operator', 'key', 'label', 'since', 'park'}}.

    Открытый интервал (или ещё не кончившийся к now). Несколько — по рангу: онлайн в одном
    парке важнее перерыва в другом; при равном ранге — начавшийся раньше (дольше в статусе).
    """
    best = {}
    for interval in intervals:
        if not _open_at(interval, now):
            continue
        operator = match(interval)
        if operator is None:
            continue
        operator_id = int(operator['id'])
        rank = STATUS_RANK.get(interval.key, OTHER_RANK)
        held = best.get(operator_id)
        if held is not None and (held[0], held[1].started_at) <= (rank, interval.started_at):
            continue
        best[operator_id] = (rank, interval, operator)
    return {operator_id: {'operator': operator, 'key': interval.key, 'label': label_of(interval),
                          'since': interval.started_at, 'park': interval.park_name}
            for operator_id, (_rank, interval, operator) in best.items()}


def last_seen(intervals, match):
    """{id сотрудника: когда кончился его последний интервал} — «Не в системе с»."""
    out = {}
    for interval in intervals:
        if interval.ended_at is None:
            continue
        operator = match(interval)
        if operator is None:
            continue
        operator_id = int(operator['id'])
        if operator_id not in out or interval.ended_at > out[operator_id]:
            out[operator_id] = interval.ended_at
    return out


def _merge(spans):
    merged = []
    for begin, end in sorted(spans):
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([begin, end])
    return [(begin, end) for begin, end in merged if end > begin]


def line_seconds_by_hour(intervals, match, day_start, hours, until=None):
    """{час: {id сотрудника: секунды «Онлайн»}} за часы [0, hours) суток day_start.

    Только available — линию держит лишь «Онлайн» (решение владельца 24.11.2026). Интервалы
    одного человека в разных парках сливаются, а не складываются. Открытый интервал тянется до
    until («сейчас»), у закрытых суток until — конец суток.
    """
    window_end = day_start + timedelta(hours=hours)
    limit = min(window_end, until) if until is not None else window_end
    spans = {}
    for interval in intervals:
        if interval.key != LINE_STATUS or interval.started_at is None:
            continue
        operator = match(interval)
        if operator is None:
            continue
        begin = max(interval.started_at, day_start)
        end = min(interval.ended_at if interval.ended_at is not None else limit, limit)
        if end > begin:
            spans.setdefault(int(operator['id']), []).append((begin, end))
    per_hour = {}
    for operator_id, items in spans.items():
        for begin, end in _merge(items):
            for hour in range(hours):
                hour_start = day_start + timedelta(hours=hour)
                hour_end = hour_start + timedelta(hours=1)
                overlap = (min(end, hour_end) - max(begin, hour_start)).total_seconds()
                if overlap > 0:
                    people = per_hour.setdefault(hour, {})
                    people[operator_id] = people.get(operator_id, 0) + int(overlap)
    return per_hour


def state(cursor):
    """Отметки статусов: {'since': (отметка, когда записана) | None, 'from': время | None}."""
    cursor.execute('SELECT name, mark, updated_at FROM syntony_chat_sync_marks WHERE name = ANY(%s)',
                   ([MARK_STATUSES, MARK_STATUSES_FROM],))
    marks = {name: (mark, updated_at) for name, mark, updated_at in cursor.fetchall()}
    since = marks.get(MARK_STATUSES)
    origin = marks.get(MARK_STATUSES_FROM)
    return {'since': since, 'from': origin[0] if origin else None}
