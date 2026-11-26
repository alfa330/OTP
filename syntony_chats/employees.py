"""Показатели чатников СЗоВ из Syntony в «Учёте часов» (SZOV-11, с 18.11.2026).

Просьба СЗоВ (Сабыр Азана, 19.11.2026): чаты сотрудника, статусы Syntony со временем в каждом,
первый ответ — подтягивать из Syntony с 18-го «по аналогии с Chat2Desk», а Chat2Desk за 18–19
убрать. Решения владельца 25.11.2026:

* **Чаты и время ответа** — те же цифры, что у сотрудника во вкладке «Чатники» биллинга
  (resource_fte.chat.chat_billing_operator_day_totals): чат засчитан каждому, кто отвечал в эти
  сутки; «Время ответа» — средний первый ответ (у Syntony первая реакция и первый ответ — одно
  событие, решение по SZOV-12). «Ноль такси», который ещё живёт в Chat2Desk, складывается с
  Syntony, как в биллинге. Пишется в chat_manager_daily_metrics — оттуда «Учёт часов» и ЗП
  чат-менеджера. С 18.11 эти две колонки чатникам пишет только Syntony: ночной синк Chat2Desk
  их больше не трогает (split_c2d_daily_metrics), а цифры Chat2Desk, записанные раньше,
  заменяются первым же прогоном. Дальше строка переписывается, лишь когда поменялись сами данные
  (syntony_employee_metric_marks), — ручная правка СВ не откатывается каждые 10 минут.
  Сутки, где у сотрудника нет чатов, во время ответа не идут (решение владельца 26.11.2026,
  day_reply_time): первый ответ у Syntony считается по дню начала диалога, а чат — по дню
  ответа, и «ответил назавтра» давало строку «0 чатов, ответ 25 ч», которая в простом среднем
  месяца поднимала время ответа человека в разы.

* **Статусы** — интервалы /operator-statuses (statuses.py) переносятся в отрезки статусов дня
  (operator_status_segments) тем же путём, что готовые интервалы Binotel: часы, перерывы, тренинг,
  опоздания и штрафы считает общий пересчёт по модели чат-менеджера. Часы — как у Chat2Desk:
  работа — только «Онлайн», «Перерыв» — перерыв, «Тренинг» — тренинг; «Занят», «Техническая
  причина» и «Офлайн работа» видны на таймлайне, но в часы не идут («тех причина», как у
  Chat2Desk, оправдывает опоздание). Переносятся закрытые сутки, которые статусы покрывают
  целиком, — как ночная выгрузка Chat2Desk за вчера: в незакрытом дне общий пересчёт счёл бы
  «ранним уходом» всё время до конца смены. Решение владельца «оставить как есть до статусов»
  действует и по людям: у кого в покрытые сутки есть интервалы Syntony — его сутки целиком по
  Syntony (прежние отрезки Chat2Desk снимаются, ночная выгрузка Chat2Desk его больше не трогает,
  drop_roster_rows); у кого их нет (не выходил в Syntony или Syntony не назвала его так, чтобы
  сопоставить), — сутки остаются по Chat2Desk: иначе ошибка сопоставления в первый же день дала
  бы человеку ноль часов и опоздание на всю смену. Сутки сверяются с базой каждым прогоном и
  переписываются только при расхождении.
"""

import logging
from datetime import timedelta

from . import statuses, szov

logger = logging.getLogger(__name__)

SOURCE = 'syntony'

# Ключ Syntony → ключ отрезка статуса iCORE. Ключи подобраны под профиль модели
# чат-менеджера (db/salary.py, db/core.py: CHAT_MANAGER_*_STATUS_KEYS): «онлайн» — работа,
# «перерыв» — перерыв, «тренинг» — тренинг, «занят» — не в часах (как busy у Chat2Desk),
# «тех причина» — тот же ключ, что у Chat2Desk; «офлайн работа» профилю не знакома и в часы не
# идёт. Подписи таймлайна — по-русски, как в Syntony.
KEY_MAP = {
    'available': 'онлайн',
    'break': 'перерыв',
    'training': 'тренинг',
    'busy': 'занят',
    'technical': 'тех причина',
    'offline_work': 'офлайн работа',
}
# «Нет открытого интервала — не в сети» (документ Syntony). Отрезком не пишется: это опора —
# обычное (не готовое) событие в конце последнего интервала суток. Пересборка отрезков по
# событиям (соседние сутки по Chat2Desk, кнопка «Пересчитать», телефон) берёт последнее событие
# человека до своего окна; без опоры им оказался бы давний статус Chat2Desk — и он растянулся бы
# и на хвост этих суток после последнего интервала Syntony, и на начало следующих. Профилю
# модели ключ не знаком — в часы не идёт.
OFFLINE_KEY = 'не в сети'
CHAT_MANAGER_MODEL = 'chat_manager'

# Сколько суток назад сверять. Показатели: биллинг хранит сутки без срока, но старше месяца
# они уже не меняются. Статусы: интервалы Syntony хранятся 30 дней (sync.keep_days) —
# сутки старше уже не с чем сверять, переносить их заново нельзя (стёрлись бы).
METRICS_LOOKBACK_DAYS = 35
STATUSES_LOOKBACK_DAYS = 25
# Сутки закрыты, когда после полуночи прошло столько: сторож забирает статусы раз в 3 минуты.
CLOSED_GRACE = timedelta(minutes=30)
# Отрезок короче секунды в базу не ляжет (duration_sec > 0) — сверка вечно видела бы разницу.
MIN_PIECE = timedelta(seconds=1)

# Колонки «Учёта часов», которые с перехода пишет Syntony, а не Chat2Desk.
C2D_REPLACED_FIELDS = frozenset({'chats_count', 'avg_response_time_seconds'})

ZERO = {'chats': 0, 'first_sum': 0.0, 'first_n': 0}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def roster_ids(ctx):
    """id чатников СЗоВ из справочника сборки (ctx.roster)."""
    out = set()
    for operator in getattr(ctx, 'roster', None) or []:
        operator_id = _int((operator or {}).get('id'))
        if operator_id is not None:
            out.add(operator_id)
    return out


def _local_today(now):
    return now.astimezone(szov.LOCAL_TZ).date()


def _wall(moment):
    """Время для колонок TIMESTAMP — стенными часами Алматы, как у остальных источников."""
    return moment.astimezone(szov.LOCAL_TZ).replace(tzinfo=None)


# ── Чаты и время ответа ───────────────────────────────────────────────────────────────────

def same_totals(left, right):
    if left is None or right is None:
        return False
    return (int(left['chats']) == int(right['chats'])
            and int(left['first_n']) == int(right['first_n'])
            and abs(float(left['first_sum']) - float(right['first_sum'])) < 0.5)


def average_reply(totals):
    first_n = int(totals.get('first_n') or 0)
    return round(float(totals.get('first_sum') or 0.0) / first_n, 2) if first_n > 0 else None


def day_reply_time(totals):
    """«Время ответа» сотрудника за сутки → средний первый ответ, только если в эти сутки у него
    есть чаты; нет чатов — None.

    Первый ответ Syntony (как во вкладке «Чатники») считается по дню, когда водитель начал
    диалог, а чат засчитывается в день ответа. Ответил назавтра, а в день диалога чатов не было —
    у человека вставала строка «0 чатов, время ответа 25 ч». Месячное время ответа в «Учёте
    часов» — простое среднее дневных, и одна такая строка поднимала его в разы (24.11.2026 у
    двоих чатников 530 → 4242 с и 993 → 10 891 с). Решение владельца 26.11.2026: такие сутки во
    время ответа не идут."""
    return average_reply(totals) if int(totals.get('chats') or 0) > 0 else None


FIELDS_ALL = tuple(sorted(C2D_REPLACED_FIELDS))
FIELDS_CHATS = ('chats_count',)


def metric_writes(totals, marks, existing, roster, reply_without_chats=()):
    """Кому и что записать за одни сутки → [(id, итоги, колонки)] по возрастанию id.

    totals — {id: итоги биллинга}; marks — {id: что записано из Syntony раньше}; existing —
    {id: ручная ли} строки «Учёта часов» за эти сутки (или просто множество id — тогда все не
    ручные). Пишется чатнику СЗоВ, если сутки у него из Syntony ещё не писались — тогда цифры
    Chat2Desk заменяются (в том числе нулями), а у строки, где СВ руками поставил время ответа,
    переписываются только чаты (руками их не ставят), — или если итоги с прошлой записи
    поменялись. Не чатник СЗоВ — не трогается. Чатов в сутках нет и строки нет — заводить её
    незачем: время ответа таким суткам не пишется (day_reply_time). reply_without_chats — id,
    у кого в эти сутки не ручная строка с временем ответа при нуле чатов (записана до правила
    26.11.2026): она переписывается один раз и при прежних итогах.
    """
    if not isinstance(existing, dict):
        existing = {operator_id: False for operator_id in (existing or ())}
    stale = set(reply_without_chats or ())
    out = []
    for operator_id in sorted((set(totals) | set(marks) | set(existing)) & set(roster)):
        target = totals.get(operator_id) or ZERO
        mark = marks.get(operator_id)
        fields = FIELDS_ALL
        if mark is not None:
            if same_totals(mark, target) and operator_id not in stale:
                continue
        elif operator_id not in existing:
            if not target['chats']:
                continue
        elif existing[operator_id]:
            fields = FIELDS_CHATS
        out.append((operator_id, dict(target), fields))
    return out


def metric_item(day, operator_id, totals, fields=FIELDS_ALL):
    """Строка для save_chat_manager_daily_metrics. Ключ времени ответа есть всегда, когда его
    пишем: None стирает прежнее время Chat2Desk (иначе сохранение оставило бы старое значение)."""
    item = {
        'operator_id': int(operator_id),
        'day': day.isoformat(),
        'chats_count': int(totals['chats']),
        'source': SOURCE,
        'first_reply_count': int(totals['first_n']),
    }
    if 'avg_response_time_seconds' in fields:
        item['avg_response_time_seconds'] = day_reply_time(totals)
    else:
        item['manual_reply_time_kept'] = True
    return item


def merge_unmatched(totals, unmatched, resolve, roster):
    """Ответы, которым при сборке суток не нашёлся сотрудник (его тогда не было в справочнике),
    досопоставить по ФИО нынешним справочником → те же totals, дополненные."""
    for (day, name), item in sorted(unmatched.items(), key=lambda pair: (pair[0][0], pair[0][1])):
        operator = resolve(name) if name else None
        operator_id = _int((operator or {}).get('id'))
        if operator_id is None or operator_id not in roster:
            continue
        bucket = totals.setdefault((day, operator_id), dict(ZERO))
        bucket['chats'] += int(item['chats'])
        bucket['first_sum'] += float(item['first_sum'])
        bucket['first_n'] += int(item['first_n'])
    return totals


def c2d_metric_owners(roster, metrics):
    """Кому ночной синк Chat2Desk с перехода не пишет чаты и время ответа: чатникам СЗоВ, а если
    справочник не собрался — никому из пачки (не знаем, кто чатник; лучше пропустить сутки
    Chat2Desk, чем записать их поверх Syntony — отметка их уже не исправила бы)."""
    if roster:
        return set(roster)
    return {operator_id for operator_id in (_int((item or {}).get('operator_id'))
                                            for item in (metrics or [])) if operator_id is not None}


def forget_marks(db, day, operator_ids):
    """Откат на Chat2Desk записал чатникам свои цифры поверх Syntony — отметки этих суток снять:
    вернётся Syntony — перепишет строку, как в первый раз."""
    ids = sorted({operator_id for operator_id in (_int(value) for value in (operator_ids or ()))
                  if operator_id is not None})
    if not ids:
        return 0
    with db._get_cursor() as cursor:
        cursor.execute('DELETE FROM syntony_employee_metric_marks WHERE day = %s AND operator_id = ANY(%s)',
                       (day, ids))
        return cursor.rowcount


def _metric_days(cursor, floor, today):
    """Сутки, которые биллинг Syntony собрал без ошибки, — у прочих данных нет, нулей не пишем."""
    cursor.execute("""
        SELECT day FROM syntony_chat_billing_days
         WHERE day BETWEEN %s AND %s AND error IS NULL
         ORDER BY day
    """, (floor, today))
    return [row[0] for row in cursor.fetchall()]


def _metric_marks(cursor, day_from, day_to):
    cursor.execute("""
        SELECT day, operator_id, chats, first_sum, first_n
          FROM syntony_employee_metric_marks WHERE day BETWEEN %s AND %s
    """, (day_from, day_to))
    out = {}
    for day, operator_id, chats, first_sum, first_n in cursor.fetchall():
        out.setdefault(day, {})[int(operator_id)] = {
            'chats': int(chats), 'first_sum': float(first_sum), 'first_n': int(first_n)}
    return out


def _existing_metric_rows(cursor, day_from, day_to, roster):
    """→ ({сутки: {id: ручная ли строка}}, {сутки: {id не ручных строк с временем ответа при нуле
    чатов}}). Ручная — последней её писала правка дня в «Учёте часов»: в теле время ответа или
    переводы, но нет ни чатов, ни источника (Chat2Desk, отчёт, оценки всегда кладут их). Время
    ответа при нуле чатов — след записи до правила day_reply_time (26.11.2026)."""
    cursor.execute("""
        SELECT day, operator_id,
               (NOT (raw_payload ? 'chats_count') AND NOT (raw_payload ? 'source')
                AND (raw_payload ? 'avg_response_time_seconds' OR raw_payload ? 'transfer_chat_count')),
               (COALESCE(chats_count, 0) = 0 AND avg_response_time_seconds IS NOT NULL)
          FROM chat_manager_daily_metrics
         WHERE day BETWEEN %s AND %s AND operator_id = ANY(%s)
    """, (day_from, day_to, sorted(roster)))
    existing, reply_without_chats = {}, {}
    for day, operator_id, manual, reply_only in cursor.fetchall():
        existing.setdefault(day, {})[int(operator_id)] = bool(manual)
        if reply_only and not manual:
            reply_without_chats.setdefault(day, set()).add(int(operator_id))
    return existing, reply_without_chats


def _unmatched_totals(cursor, day_from, day_to):
    """{(сутки, ФИО из Syntony): итоги} ответов без сотрудника — по правилу «Чатников»."""
    cursor.execute("""
        SELECT day, operator_name,
               COUNT(*) FILTER (WHERE replied)::int,
               COALESCE(SUM(first_reply_seconds), 0)::float,
               COUNT(first_reply_seconds)::int
          FROM syntony_chat_billing_operators
         WHERE day BETWEEN %s AND %s AND operator_id IS NULL AND operator_name IS NOT NULL
         GROUP BY 1, 2
    """, (day_from, day_to))
    return {(day, name): {'chats': int(chats or 0), 'first_sum': float(first_sum or 0.0),
                          'first_n': int(first_n or 0)}
            for day, name, chats, first_sum, first_n in cursor.fetchall()}


def _save_marks(cursor, day, writes):
    for operator_id, totals, _fields in writes:
        cursor.execute("""
            INSERT INTO syntony_employee_metric_marks (day, operator_id, chats, first_sum, first_n)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (day, operator_id) DO UPDATE SET chats = EXCLUDED.chats,
                first_sum = EXCLUDED.first_sum, first_n = EXCLUDED.first_n, written_at = now()
        """, (day, int(operator_id), int(totals['chats']), float(totals['first_sum']),
              int(totals['first_n'])))


def _load_totals(cursor, day_from, day_to, first_day):
    from resource_fte.chat import chat_billing_operator_day_totals
    return chat_billing_operator_day_totals(cursor, day_from, day_to, syntony_from=first_day)


def refresh_metrics(db, ctx, *, first_day, now, load_totals=_load_totals):
    """Чаты и время ответа чатников с first_day → [{'day', 'written'}] по суткам с записью."""
    roster = roster_ids(ctx)
    if not roster:
        return []
    today = _local_today(now)
    floor = max(first_day, today - timedelta(days=METRICS_LOOKBACK_DAYS))
    if floor > today:
        return []
    with db._get_cursor() as cursor:
        days = _metric_days(cursor, floor, today)
        if not days:
            return []
        totals = load_totals(cursor, days[0], days[-1], first_day)
        unmatched = _unmatched_totals(cursor, days[0], days[-1])
        marks = _metric_marks(cursor, days[0], days[-1])
        existing, reply_without_chats = _existing_metric_rows(cursor, days[0], days[-1], roster)
    merge_unmatched(totals, unmatched, ctx.resolve, roster)
    results = []
    for day in days:
        day_totals = {operator_id: item for (total_day, operator_id), item in totals.items()
                      if total_day == day}
        writes = metric_writes(day_totals, marks.get(day) or {}, existing.get(day) or {}, roster,
                               reply_without_chats.get(day) or ())
        if not writes:
            continue
        for fields in sorted({fields for _id, _item, fields in writes}):
            db.save_chat_manager_daily_metrics(
                metrics=[metric_item(day, operator_id, item, fields)
                         for operator_id, item, item_fields in writes if item_fields == fields],
                imported_by=None,
                update_fields=list(fields),
            )
        with db._get_cursor() as cursor:
            _save_marks(cursor, day, writes)
        results.append({'day': day.isoformat(), 'written': len(writes)})
    return results


# ── Статусы ───────────────────────────────────────────────────────────────────────────────

def status_key(interval):
    """Ключ отрезка iCORE для интервала Syntony: известный — по KEY_MAP, незнакомый — его
    подпись строчными буквами (видна на таймлайне, в часы не идёт)."""
    key = str(interval.key or '').strip().lower()
    if key in KEY_MAP:
        return KEY_MAP[key]
    # Обрезка — до нормализации: ключ в базе хранится нормализованным (без пробела на конце),
    # и сверка суток иначе вечно видела бы разницу.
    label = str(interval.label or interval.key or '').strip().lower()[:120]
    return ' '.join(label.split()) or 'статус syntony'


def _rank(interval):
    return (statuses.STATUS_RANK.get(interval.key, statuses.OTHER_RANK),
            interval.started_at, interval.id or '')


def flatten(intervals, begin, end):
    """Интервалы одного человека → [(начало, конец, ключ iCORE)] без наложений внутри [begin, end).

    Где интервалы разных парков сети перекрываются, берётся более «рабочий» статус
    (statuses.STATUS_RANK — онлайн в одном парке значит, что человек на линии), при равном —
    начавшийся раньше: то же правило, что у текущего статуса на табло. Открытый интервал тянется
    до end. Соседние куски с одним ключом сливаются.
    """
    clipped = []
    points = {begin, end}
    for interval in intervals:
        if interval.started_at is None:
            continue
        left = max(interval.started_at, begin)
        right = min(interval.ended_at if interval.ended_at is not None else end, end)
        if right <= left:
            continue
        clipped.append((left, right, interval))
        points.update((left, right))
    edges = sorted(points)
    pieces = []
    for left, right in zip(edges, edges[1:]):
        active = [interval for start, stop, interval in clipped if start <= left and stop >= right]
        if not active:
            continue
        key = status_key(min(active, key=_rank))
        if pieces and pieces[-1][1] == left and pieces[-1][2] == key:
            pieces[-1] = (pieces[-1][0], right, key)
        else:
            pieces.append((left, right, key))
    return [piece for piece in pieces if piece[1] - piece[0] >= MIN_PIECE]


def day_segments(intervals, match, day, roster):
    """{id чатника: [(начало, конец, ключ)]} суток day стенным временем Алматы."""
    start, end = szov.local_day_bounds(day)
    by_person = {}
    for interval in intervals:
        operator = match(interval)
        if operator is None:
            continue
        operator_id = _int(operator.get('id'))
        if operator_id is None or operator_id not in roster:
            continue
        by_person.setdefault(operator_id, []).append(interval)
    out = {}
    for operator_id, items in by_person.items():
        pieces = [(_wall(left), _wall(right), key) for left, right, key in flatten(items, start, end)]
        if pieces:
            out[operator_id] = pieces
    return out


def import_rows(day, segments_by_person, cleared=()):
    """(события, отрезки) для save_operator_status_import — как у готовых интервалов Binotel,
    плюс опора «не в сети» (OFFLINE_KEY, обычное событие): в конце последнего интервала, если он
    кончился до полуночи, а у снятых — в начале суток."""
    day_start = _wall(szov.local_day_bounds(day)[0])
    day_end = _wall(szov.local_day_bounds(day)[1])
    events, segments = [], []
    for operator_id in sorted(segments_by_person):
        pieces = segments_by_person[operator_id]
        for left, right, key in pieces:
            events.append({'operator_id': operator_id, 'event_at': left, 'status_key': key,
                           'state_note': '', 'event_kind': 'status'})
            segments.append({'operator_id': operator_id, 'status_date': day.isoformat(),
                             'start_at': left, 'end_at': right,
                             'duration_sec': int((right - left).total_seconds()),
                             'status_key': key, 'state_note': ''})
        last_end = max(right for _left, right, _key in pieces)
        if last_end < day_end:
            events.append(_anchor(operator_id, last_end))
    for operator_id in sorted(set(cleared) - set(segments_by_person)):
        events.append(_anchor(operator_id, day_start))
    return events, segments


def _anchor(operator_id, moment):
    return {'operator_id': operator_id, 'event_at': moment, 'status_key': OFFLINE_KEY,
            'state_note': '', 'event_kind': 'status', 'is_authoritative': False}


def chat_manager_ids_on(db, roster, day):
    """Кому из справочника переносить статусы за сутки: только с моделью чат-менеджера на дату.
    У другой модели другой профиль часов — там «занят» считается работой, а «онлайн» нет."""
    if not roster:
        return set()
    models = db.get_operator_calculation_models_as_of(sorted(roster), day) or {}
    return {operator_id for operator_id in roster
            if str(models.get(operator_id) or '').strip() == CHAT_MANAGER_MODEL}


def status_day_changes(target, actual, written, live):
    """Кого переписать за сутки → (заменить отрезками Syntony, снять прежние отрезки Syntony).

    target — отрезки Syntony; actual — отрезки в базе; written — у кого в базе отрезки Syntony
    (готовые интервалы, is_authoritative); live — у кого события пришли сами (телефон iCORE
    Phone): их сутки не трогаем — готовые интервалы и живые сессии в одних сутках общий пересчёт
    не сводит. Снимаются только свои прежние отрезки — у кого Syntony за сутки больше ничего не
    отдала; отрезки Chat2Desk человека без интервалов Syntony остаются (см. начало модуля).
    """
    replace = sorted(operator_id for operator_id, pieces in target.items()
                     if operator_id not in live and actual.get(operator_id) != pieces)
    clear = sorted(operator_id for operator_id in set(written) - set(target)
                   if operator_id not in live)
    return replace, clear


def _day_actual(cursor, day, roster):
    """(отрезки в базе по людям, у кого среди них отрезки Syntony — готовые интервалы)."""
    cursor.execute("""
        SELECT operator_id, start_at, end_at, status_key, COALESCE(is_authoritative, FALSE)
          FROM operator_status_segments
         WHERE status_date = %s AND operator_id = ANY(%s)
         ORDER BY operator_id, start_at, end_at, id
    """, (day, sorted(roster)))
    out, written = {}, set()
    for operator_id, left, right, key, authoritative in cursor.fetchall():
        out.setdefault(int(operator_id), []).append(
            (left, right, ' '.join(str(key or '').strip().lower().split())))
        if authoritative:
            written.add(int(operator_id))
    return out, written


def _day_live(cursor, day, roster):
    """У кого в эти сутки события пришли сами (телефон iCORE Phone, client_event_id)."""
    cursor.execute("""
        SELECT DISTINCT operator_id FROM operator_status_events
         WHERE event_date = %s AND operator_id = ANY(%s) AND client_event_id IS NOT NULL
    """, (day, sorted(roster)))
    return {int(row[0]) for row in cursor.fetchall()}


def covered_days(state, days, *, first_day):
    """Какие из этих суток статусы Syntony покрывают целиком (не раньше first_day)."""
    out = set()
    for day in days:
        if day < first_day:
            continue
        start, end = szov.local_day_bounds(day)
        if szov.statuses_cover(state, start, end):
            out.add(day)
    return out


def status_people(db, ctx, days, *, first_day):
    """{сутки: id чатников, чьи сутки пишет Syntony} по покрытым суткам из days — для ночной
    выгрузки Chat2Desk: этих людей в эти сутки она не трогает, остальных пишет, как раньше.
    Это те, у кого есть интервалы Syntony, кроме людей с живым телефоном и без модели
    чат-менеджера на дату (их сутки Syntony не переносит — см. _status_day)."""
    days = list(days or [])
    roster = roster_ids(ctx)
    if not days or not roster:
        return {}
    with db._get_cursor() as cursor:
        state = statuses.state(cursor)
        covered = sorted(covered_days(state, days, first_day=first_day))
        windows = {day: statuses.load_window(cursor, *szov.local_day_bounds(day)) for day in covered}
        lives = {day: _day_live(cursor, day, roster) for day in covered}
    match = statuses.matcher(ctx.roster, ctx.resolve)
    out = {}
    for day, intervals in windows.items():
        day_roster = chat_manager_ids_on(db, roster, day)
        people = set(day_segments(intervals, match, day, day_roster)) - lives[day]
        if people:
            out[day] = people
    return out


def _status_day(db, day, match, roster):
    start, end = szov.local_day_bounds(day)
    roster = chat_manager_ids_on(db, roster, day)
    if not roster:
        return None
    with db._get_cursor() as cursor:
        intervals = statuses.load_window(cursor, start, end)
        actual, written = _day_actual(cursor, day, roster)
        live = _day_live(cursor, day, roster)
    target = day_segments(intervals, match, day, roster)
    replace, clear = status_day_changes(target, actual, written, live)
    if not replace and not clear:
        return None
    events, segments = import_rows(day, {operator_id: target[operator_id] for operator_id in replace},
                                   cleared=clear)
    db.save_operator_status_import(
        events=events,
        segments=segments,
        imported_by=None,
        summary={
            'source_rows': len(intervals),
            'valid_events': len(events),
            'meta': {
                'api': 'syntony_statuses',
                'source': SOURCE,
                'day': day.isoformat(),
                # Интервалы Syntony — готовые начало и конец, как у Binotel: пересборка по
                # точкам переключения растянула бы разрывы между ними.
                'segments_authoritative': True,
            },
        },
        keep_live_events=True,
        clear_operator_days=[(operator_id, day) for operator_id in clear],
    )
    return {'day': day.isoformat(), 'replaced': len(replace), 'cleared': len(clear),
            'segments': len(segments), 'skipped_live': len(live & (set(target) | set(actual)))}


def refresh_statuses(db, ctx, *, first_day, now):
    """Статусы Syntony → отрезки статусов закрытых покрытых суток → [итог по переписанным суткам]."""
    roster = roster_ids(ctx)
    if not roster:
        return []
    with db._get_cursor() as cursor:
        state = statuses.state(cursor)
    if state.get('since') is None or state.get('from') is None:
        return []
    today = _local_today(now)
    floor = max(first_day, today - timedelta(days=STATUSES_LOOKBACK_DAYS))
    days = []
    day = floor
    while day < today:
        if now >= szov.local_day_bounds(day)[1] + CLOSED_GRACE:
            days.append(day)
        day += timedelta(days=1)
    match = statuses.matcher(ctx.roster, ctx.resolve)
    results = []
    for day in sorted(covered_days(state, days, first_day=first_day)):
        try:
            result = _status_day(db, day, match, roster)
        except Exception:  # noqa: BLE001 — одни сутки не держат остальные
            logger.exception('Статусы Syntony в «Учёт часов»: сутки %s не перенеслись', day)
            result = {'day': day.isoformat(), 'error': True}
        if result:
            results.append(result)
    return results


# ── Ночные синки Chat2Desk ────────────────────────────────────────────────────────────────

def split_c2d_daily_metrics(metrics, update_fields, roster):
    """Дневные метрики Chat2Desk → [(метрики, колонки)] пачками для сохранения.

    Чатникам СЗоВ с перехода чаты и время ответа пишет Syntony — у них эти колонки из пачки
    уходят (остальное, например оценки, — как было); прочим — без перемен.
    """
    fields = {str(field) for field in (update_fields or [])}
    roster = set(roster)
    own = [item for item in (metrics or []) if _int((item or {}).get('operator_id')) in roster]
    rest = [item for item in (metrics or []) if _int((item or {}).get('operator_id')) not in roster]
    batches = []
    if rest and fields:
        batches.append((rest, sorted(fields)))
    kept = sorted(fields - C2D_REPLACED_FIELDS)
    if own and kept:
        batches.append((own, kept))
    return batches


def drop_roster_rows(rows, roster):
    """Строки выгрузки статусов Chat2Desk без этих людей (их сутки пишет Syntony)."""
    roster = set(roster)
    return [row for row in (rows or [])
            if not isinstance(row, dict) or _int(row.get('operator_id')) not in roster]


def refresh(db, ctx, *, first_day, now):
    """Прогон: чаты и время ответа, затем статусы → {'metrics': [...], 'statuses': [...]}."""
    summary = {}
    try:
        summary['metrics'] = refresh_metrics(db, ctx, first_day=first_day, now=now)
    except Exception:  # noqa: BLE001
        logger.exception('Показатели чатников по Syntony: прогон упал')
        summary['metrics_error'] = True
    try:
        summary['statuses'] = refresh_statuses(db, ctx, first_day=first_day, now=now)
    except Exception:  # noqa: BLE001
        logger.exception('Статусы Syntony в «Учёт часов»: прогон упал')
        summary['statuses_error'] = True
    return summary
