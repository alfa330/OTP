# -*- coding: utf-8 -*-
"""«Смены» в «Чатах ОП»: кто из чат-менеджеров когда работал и как обрабатывал чаты.

Просьба владельца 16.11.2026: «отчёт по активности чат-менеджеров ОП в разделе «Чаты ОП»: у кого
какая смена была и кто как обрабатывал чаты, интенсивность и т. д.». Вкладка «Операторы → Смены»
раздела. Модуль чистый — ни базы, ни Flask: чтение — `shift_report_routes.py`, проверка —
tests/test_wazzup_shift_report.py.

Строка — смена
--------------
Смену верификатор ведёт кнопкой в самом разделе (wazzup/shift.py): «Начать смену» ставит
«Активный», дальше «Перерыв», «Тренинг», «Тех. пауза»; «Закончить смену» — «выключен». Закрытый
портал смену закрывает сторож — моментом последней отметки («Портал закрыт»), а статус, не
менявшийся 16 часов, — на этом пределе. Смена отчёта собирается ТОЛЬКО из событий раздела
(метка `wzws-` в client_event_id): у двух верификаторов в те же сутки идут и события iCORE Phone
(замер 16.11.2026) — это работа на линии, а не смена в чатах.

  * смена начинается любым статусом смены после выхода и кончается выходом;
  * статус меньше чем через 4 часа после выхода — та же смена (портал закрылся и открылся снова):
    промежуток идёт отрезком «Не в разделе», в «Активный» он не входит. Четыре часа — та же пауза,
    что рвёт смену в «Отчёте по сменам» линии (op_wallboard/activity.py);
  * смена относится к дате своего начала: ночная 21:00–09:00 целиком лежит в вечере;
  * незакрытая смена идёт до «сейчас» и помечена идущей.

Как обрабатывал чаты
--------------------
Сообщение менеджера относится к его смене, если отправлено между её началом и концом — с запасом
в минуту до и пять после: время сообщению ставит Wazzup, когда очередь его отправит. Вне смен —
строка «без смены» за этот день: так пишут из самого Wazzup сотрудники «Основы» и «Потока», у
которых кнопки смены нет, — их чаты из отчёта не выпадают.

  * Чаты — разные чаты, где он писал в эту смену (чат, который он вёл в двух сменах, — в каждой);
  * Сообщения — его исходящие;
  * Первый ответ и ответ в чате — правила «Табло ОП · Чат» (op_wallboard/chat.py): диалог —
    переписка без паузы 6 часов; первый ответ засчитан тому, кто ответил первым; ответ в чате —
    среднее его задержек в диалоге, а за смену — среднее по диалогам. Ответ относится к смене, в
    которую дан. Нормы — те же, что у табло (1 и 4 минуты);
  * Чатов в час — чаты ÷ часы в «Активном»: писать клиентам можно только в нём (владелец
    19.10.2026). Меньше четверти часа «Активного» — делить не на что, прочерк.

Суммы ответа (`firstSum`/`firstCount`, `innerSum`/`innerCount`) отдаются как есть: итог за период
и по человеку экран складывает из них, а не усредняет средние.

График
------
Смена из «Графиков работы» (work_shifts), которая пересекается со сменой в разделе; опоздание и
ранний уход — цифры «Учёта часов» (daily_hours), своего правила опоздания здесь нет. Верификатор,
у которого смена по графику началась больше получаса назад, а в разделе его не было и сообщений
нет, — строка «не вышел».
"""

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta

from op_wallboard import chat as chat_mod
from . import shift as shift_mod

SESSION_GAP = timedelta(hours=4)
MESSAGE_LEAD = timedelta(minutes=1)
MESSAGE_TAIL = timedelta(minutes=5)
MAX_OPEN = timedelta(hours=shift_mod.MAX_OPEN_STATUS_HOURS)
RATE_MIN_ACTIVE_SECONDS = 15 * 60
# «Не вышел» — не раньше, чем через полчаса от начала смены по графику: до этого человек ещё
# может быть в пути (тот же запас, что у «Дресс-кода», dress_code/service.py).
ABSENT_GRACE = timedelta(minutes=30)

ROW_SHIFT = 'shift'
ROW_NO_SHIFT = 'noShift'
ROW_ABSENT = 'absent'

KIND_ACTIVE = 'active'
KIND_BREAK = 'break'
KIND_TRAINING = 'training'
KIND_TECH = 'tech'
KIND_OFFLINE = 'offline'
KINDS = (KIND_ACTIVE, KIND_BREAK, KIND_TRAINING, KIND_TECH, KIND_OFFLINE)
_TONE_KINDS = {'work': KIND_ACTIVE, 'break': KIND_BREAK, 'training': KIND_TRAINING,
               'tech': KIND_TECH}
# Ключи статусов — из меню смены раздела, своего списка здесь нет.
STATUS_KINDS = {item['key']: _TONE_KINDS[item['tone']] for item in shift_mod.STATUSES}

END_PORTAL = 'portal'
END_LONG = 'long'
_AUTO_ENDS = {shift_mod.AUTO_LOGOUT_NOTE: END_PORTAL, shift_mod.LONG_SHIFT_NOTE: END_LONG}

# Смена графика не «чатовая»: в «Телефонную смену» и «Офисную практику» в чатах не ждут.
PLAN_REGULAR = 'regular'

GROUPS = (
    ('verifier', chat_mod.VERIFIER_GROUP_MODEL, 'Верификаторы'),
    ('osnova', 'op_osnova', 'Основа'),
    ('potok', 'op_potok', 'Поток'),
    ('yar', 'op_yandex_reg', 'ЯР'),
)
GROUP_OTHER = 'other'
GROUP_NONE = 'none'
GROUP_LABELS = {**{key: label for key, _model, label in GROUPS},
                GROUP_OTHER: 'Другие группы', GROUP_NONE: 'Без группы'}
GROUP_ORDER = tuple(key for key, _model, _label in GROUPS) + (GROUP_OTHER, GROUP_NONE)
_GROUP_BY_MODEL = {model: key for key, model, _label in GROUPS}


def stamp(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%S') if moment is not None else None


def group_key(model):
    """Ключ группы по модели расчёта группы на дату; без членства — «Без группы»."""
    model = str(model or '').strip().lower()
    if not model:
        return GROUP_NONE
    return _GROUP_BY_MODEL.get(model, GROUP_OTHER)


# ── Смена из событий раздела ────────────────────────────────────────────────

def build_shifts(events, now, *, gap=SESSION_GAP, max_open=MAX_OPEN):
    """Смены одного человека: [{start, end, open, end_note, segments}] по времени.

    events — [(время, ключ статуса, пометка)] событий раздела по возрастанию времени. Отрезки
    смены — {kind, start, end, note}: статусы и «Не в разделе» между выходом и возвратом (note —
    почему вышел: 'portal' — закрыл портал, 'long' — статус висел 16 часов, None — сам).
    Незнакомый ключ пропускается: в событиях раздела его быть не должно, а угадывать, работа это
    или пауза, хуже, чем не заметить."""
    shifts = []
    current = None
    running = None   # (вид, с какого момента) — идущий отрезок; None — человек вышел
    for at, raw_key, note in events or ():
        if at is None:
            continue
        key = shift_mod.normalize_key(raw_key)
        kind = STATUS_KINDS.get(key)
        if running is not None and at - running[1] > max_open:
            # Статус висел дольше предела, а выхода сторож не записал: смена кончилась на
            # пределе, как кончил бы её сторож, — следующие сутки к ней не прилипают.
            _close_segment(current, running, running[1] + max_open)
            running = None
            current['end_note'] = END_LONG
        if kind is None:
            if running is not None and shift_mod.is_off_shift_key(key):
                _close_segment(current, running, at)
                running = None
                current['end_note'] = _AUTO_ENDS.get(str(note or '').strip())
            continue
        if current is not None and running is None and at - current['end'] >= gap:
            current = None
        if current is None:
            current = {'start': at, 'end': at, 'open': False, 'end_note': None, 'segments': []}
            shifts.append(current)
            running = (kind, at)
        elif running is None:
            current['segments'].append({'kind': KIND_OFFLINE, 'start': current['end'], 'end': at,
                                        'note': current['end_note']})
            current['end'] = at
            current['end_note'] = None
            running = (kind, at)
        elif running[0] != kind:
            _close_segment(current, running, at)
            running = (kind, at)
    if running is not None:
        # Сторож закрывает статус, не менявшийся 16 часов, ровно на этом пределе; если его
        # проход ещё не случился, отчёт ставит тот же предел сам, а не тянет смену до «сейчас».
        cap = running[1] + max_open
        _close_segment(current, running, min(max(now, running[1]), cap))
        if now < cap:
            current['open'] = True
        else:
            current['end_note'] = END_LONG
    return shifts


def _close_segment(shift, running, at):
    kind, since = running
    if at > since:
        shift['segments'].append({'kind': kind, 'start': since, 'end': at, 'note': None})
    if at > shift['end']:
        shift['end'] = at


def durations(segments):
    """Секунды по видам отрезков: «Активный», перерыв, тренинг, тех. пауза, «Не в разделе»."""
    totals = dict.fromkeys(KINDS, 0.0)
    for segment in segments or ():
        totals[segment['kind']] += max(0.0, (segment['end'] - segment['start']).total_seconds())
    return totals


# ── График ──────────────────────────────────────────────────────────────────

def plan_interval(shift_date, start_time, end_time):
    """Смена графика на дату: (начало, конец). Конец не позже начала — смена через полночь."""
    start = datetime.combine(shift_date, start_time)
    end = datetime.combine(shift_date, end_time)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _plan_view(plan):
    start, end, shift_type = plan
    return {'start': start.strftime('%H:%M'), 'end': end.strftime('%H:%M'),
            'nextDay': end.date() > start.date(),
            'type': shift_type if shift_type and shift_type != PLAN_REGULAR else None}


def _overlapping(plans, start, end):
    return [plan for plan in plans or () if plan[0] < end and plan[1] > start]


# ── Сообщения и ответы по сменам ────────────────────────────────────────────

def _new_work():
    return {'chats': set(), 'messages': 0, 'hours': {}, 'first_sum': 0.0, 'first_count': 0,
            'inner': {}, 'first_at': None, 'last_at': None}


class _Locator:
    """Чья это работа: смена человека, в которую попало время, или его «без смены» за день."""

    def __init__(self, shifts_by_user, day_from, day_to):
        self.index = {}
        for user_id, shifts in shifts_by_user.items():
            ordered = sorted(shifts, key=lambda item: item['start'])
            self.index[user_id] = ([item['start'] - MESSAGE_LEAD for item in ordered], ordered)
        self.day_from, self.day_to = day_from, day_to
        self.loose = {}

    def shift_of(self, user_id, at):
        starts, shifts = self.index.get(user_id, ((), ()))
        position = bisect_right(starts, at) - 1
        if position >= 0 and at <= shifts[position]['end'] + MESSAGE_TAIL:
            return shifts[position]
        return None

    def work(self, user_id, at):
        shift = self.shift_of(user_id, at)
        if shift is not None:
            return shift.setdefault('work', _new_work())
        day = at.date()
        if not (self.day_from <= day <= self.day_to):
            return None
        return self.loose.setdefault((user_id, day), _new_work())


def _add_message(work, chat, at):
    work['chats'].add(chat)
    work['messages'] += 1
    hour = at.replace(minute=0, second=0, microsecond=0)
    slot = work['hours'].setdefault(hour, [set(), 0])
    slot[0].add(chat)
    slot[1] += 1
    work['first_at'] = at if work['first_at'] is None else min(work['first_at'], at)
    work['last_at'] = at if work['last_at'] is None else max(work['last_at'], at)


def _hours(work, start, end):
    """Часы смены подряд, с нулями: на графике час без сообщений — тоже ответ."""
    if start is None or end is None:
        return []
    hour = start.replace(minute=0, second=0, microsecond=0)
    last = end.replace(minute=0, second=0, microsecond=0)
    out = []
    while hour <= last:
        chats, messages = (work or {}).get('hours', {}).get(hour, (set(), 0))
        out.append({'hour': hour.strftime('%Y-%m-%dT%H:00'), 'chats': len(chats),
                    'messages': messages})
        hour += timedelta(hours=1)
    return out


def _work_fields(work):
    work = work or _new_work()
    inner_values = [total / count for total, count in work['inner'].values() if count]
    return {
        'chats': len(work['chats']),
        'messages': work['messages'],
        'firstSum': round(work['first_sum'], 1),
        'firstCount': work['first_count'],
        'innerSum': round(sum(inner_values), 1),
        'innerCount': len(inner_values),
    }


def chats_per_hour(chats, active_seconds, min_active_seconds=RATE_MIN_ACTIVE_SECONDS):
    """Чатов в час «Активного»; None — «Активного» меньше порога, делить не на что."""
    if not active_seconds or active_seconds < min_active_seconds:
        return None
    return round(chats / (active_seconds / 3600.0), 1)


# ── Отчёт ───────────────────────────────────────────────────────────────────

def build_report(*, day_from, day_to, now, rows, events, plans, models, names, lateness=None,
                 include_shifts=True):
    """Строки отчёта за [day_from, day_to] (даты Алматы).

    rows — сообщения аккаунта в формате `chat.classify` (наивное время Алматы) с запасом до
      начала периода (диалогу нужно своё начало) и после конца (ночная смена последнего дня);
    events — {user_id: [(время, ключ, пометка)]} событий смены раздела по времени, с запасом;
    plans — {user_id: [(начало, конец, вид смены)]} — смены графика;
    models — {(user_id, дата): модель группы на дату};
    names — {user_id: ФИО};
    lateness — {(user_id, дата): (опоздание, ранний уход)} в секундах — из «Учёта часов»;
    include_shifts — смены раздела есть только у аккаунта верификаторов: в «Потоке» строк смен
      и «не вышел» нет, только работа без смены.

    Возвращает {'rows': [...], 'groups': [...], 'unlinked': {...}}."""
    lateness = lateness or {}
    writers = {int(row['user_id']) for row in rows or ()
               if row.get('is_echo') and row.get('user_id') is not None and not row.get('is_bot')}
    messages = chat_mod.classify(rows, writers)
    episodes = chat_mod.split_episodes(messages)

    shifts_by_user = {}
    if include_shifts:
        for user_id, user_events in (events or {}).items():
            built = build_shifts(user_events, now)
            if built:
                shifts_by_user[int(user_id)] = built
    locator = _Locator(shifts_by_user, day_from, day_to)

    for message in messages:
        if message['kind'] != chat_mod.KIND_VERIFIER:
            continue
        work = locator.work(message['user_id'], message['at'])
        if work is not None:
            _add_message(work, (message['channel_id'], message['chat_id']), message['at'])

    for index, episode in enumerate(episodes):
        for user_id, at, delta, first in episode['replies']:
            if user_id is None:
                continue
            work = locator.work(user_id, at)
            if work is None:
                continue
            if first:
                work['first_sum'] += delta
                work['first_count'] += 1
            slot = work['inner'].setdefault(index, [0.0, 0])
            slot[0] += delta
            slot[1] += 1

    out = []
    for user_id, shifts in shifts_by_user.items():
        for shift in shifts:
            day = shift['start'].date()
            if day_from <= day <= day_to:
                out.append(_shift_row(user_id, shift, plans.get(user_id), names, models))
    _attach_lateness(out, lateness)

    for (user_id, day), work in locator.loose.items():
        out.append(_loose_row(user_id, day, work, plans.get(user_id), names, models))

    if include_shifts:
        out.extend(_absent_rows(plans, shifts_by_user, locator.loose, models, names,
                                day_from, day_to, now))

    # Свежий день сверху, внутри дня — по времени начала: так читают смены за вчера.
    out.sort(key=lambda row: (row['start'] or '', row['name']))
    out.sort(key=lambda row: row['date'], reverse=True)
    present = {row['groupKey'] for row in out}
    return {
        'rows': out,
        'groups': [{'key': key, 'label': GROUP_LABELS[key]} for key in GROUP_ORDER if key in present],
        'unlinked': _unlinked(rows, day_from, day_to),
    }


def _base_row(kind, user_id, day, names, models, start, end):
    key = group_key(models.get((user_id, day)))
    return {
        'key': f'{kind}:{user_id}:{stamp(start) or day.isoformat()}',
        'kind': kind,
        'userId': user_id,
        'name': names.get(user_id) or f'Сотрудник {user_id}',
        'groupKey': key,
        'groupLabel': GROUP_LABELS[key],
        'date': day.isoformat(),
        'start': stamp(start),
        'end': stamp(end),
        'open': False,
        'endNote': None,
        'plan': [],
        'lateSeconds': 0,
        'earlySeconds': 0,
        **{name: 0 for name in ('active', 'breakTime', 'training', 'tech', 'offline')},
        'chatsPerHour': None,
        'segments': [],
        'hours': [],
    }


def _shift_row(user_id, shift, plans, names, models):
    row = _base_row(ROW_SHIFT, user_id, shift['start'].date(), names, models,
                    shift['start'], shift['end'])
    totals = durations(shift['segments'])
    work = shift.get('work')
    row.update(_work_fields(work))
    row.update({
        'open': shift['open'],
        'endNote': shift['end_note'],
        'plan': [_plan_view(plan) for plan in _overlapping(plans, shift['start'], shift['end'])],
        'active': round(totals[KIND_ACTIVE]),
        'breakTime': round(totals[KIND_BREAK]),
        'training': round(totals[KIND_TRAINING]),
        'tech': round(totals[KIND_TECH]),
        'offline': round(totals[KIND_OFFLINE]),
        'segments': [{'kind': segment['kind'], 'start': stamp(segment['start']),
                      'end': stamp(segment['end']), 'note': segment['note']}
                     for segment in shift['segments']],
        'hours': _hours(work, shift['start'], shift['end']),
    })
    row['chatsPerHour'] = chats_per_hour(row['chats'], row['active'])
    return row


def _loose_row(user_id, day, work, plans, names, models):
    row = _base_row(ROW_NO_SHIFT, user_id, day, names, models, work['first_at'], work['last_at'])
    row.update(_work_fields(work))
    row['plan'] = [_plan_view(plan) for plan in plans or () if plan[0].date() == day]
    row['hours'] = _hours(work, work['first_at'], work['last_at'])
    return row


def _absent_rows(plans, shifts_by_user, loose, models, names, day_from, day_to, now):
    out = []
    for user_id, user_plans in (plans or {}).items():
        shifts = shifts_by_user.get(user_id) or ()
        for plan in user_plans:
            start, end, shift_type = plan
            day = start.date()
            if not (day_from <= day <= day_to) or start + ABSENT_GRACE > now:
                continue
            if shift_type and shift_type != PLAN_REGULAR:
                continue
            if group_key(models.get((user_id, day))) != 'verifier':
                continue
            if (user_id, day) in loose:
                continue
            if any(shift['start'] < end and shift['end'] > start for shift in shifts):
                continue
            row = _base_row(ROW_ABSENT, user_id, day, names, models, start, end)
            row['plan'] = [_plan_view(plan)]
            out.append(row)
    return out


def _attach_lateness(rows, lateness):
    """Опоздание дня — первой смене дня, ранний уход — последней, если она закончилась."""
    by_day = defaultdict(list)
    for row in rows:
        if row['kind'] == ROW_SHIFT:
            by_day[(row['userId'], row['date'])].append(row)
    for (user_id, day), day_rows in by_day.items():
        late, early = lateness.get((user_id, datetime.strptime(day, '%Y-%m-%d').date()), (0, 0))
        day_rows.sort(key=lambda row: row['start'])
        day_rows[0]['lateSeconds'] = int(late or 0)
        if not day_rows[-1]['open']:
            day_rows[-1]['earlySeconds'] = int(early or 0)


def _unlinked(rows, day_from, day_to):
    """Ответы авторов Wazzup, ещё не привязанных к сотруднику: в отчёт они не попали никому —
    экран говорит об этом и ведёт на «Привязку», как «Аналитика»."""
    authors = set()
    count = 0
    for row in rows or ():
        at = row.get('at')
        if (at is None or not row.get('is_echo') or row.get('is_bot') or row.get('user_id') is not None
                or not row.get('author_id') or row.get('type') == chat_mod.TEMPLATE_TYPE):
            continue
        if day_from <= at.date() <= day_to:
            count += 1
            authors.add(row['author_id'])
    return {'authors': len(authors), 'messages': count}
