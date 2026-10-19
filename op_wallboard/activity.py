# -*- coding: utf-8 -*-
"""«Активность операторов» (задача #392): завершённые смены операторов ОП за месяц.

Ретроспектива, а не стена: строка — одна смена одного оператора групп «Основа», «ЯР» и
«Поток», 14 колонок в порядке ТЗ, по строке — журнал статусов этой смены. Источники те
же, что у «Табло ОП», второго расчёта ни у чего нет:

  * статусы, вход и выход — живые события iCORE Phone (`client_event_id IS NOT NULL`);
  * звонки — касания FreePBX (`cdr_touches`), которые приносит мост «Касаний»;
  * группа — членство на дату начала смены, подпись — по модели расчёта группы.

Модуль чистый: ни базы, ни Flask. На вход — события, состав, членства и касания, на выход —
строки отчёта и отрезки журнала. Чтение из базы — в `activity_routes`.

Смена
-----
Смена — это события телефона подряд. Новая начинается после паузы от 4 часов (то же правило,
что у времени входа на табло, `snapshot.SESSION_GAP_HOURS`), но с двумя поправками, без
которых цифры врут:

  * телефон не шлёт событий, пока оператор просто ждёт звонка: «Основа» ночью может четыре
    часа сидеть в «Активном» без единого звонка. Если перед паузой был рабочий статус, а пауза
    короче `QUIET_WORK_GAP` и смена вместе с ней не длиннее `MAX_SHIFT`, это одна смена;
  * «выключен» теряется (известная беда телефона: событие уходит без токена и получает 401),
    и тогда последний статус тянется до следующего события — иногда до утра. Поэтому смена
    кончается на своём ПОСЛЕДНЕМ событии: время после него никуда не идёт, а выход помечается
    «не зафиксирован». Сохранённым отрезкам `operator_status_segments` здесь не доверяем —
    у них такой защиты нет.

Смена принадлежит дню своего начала (общее правило проекта): ночная 20:00–08:00 целиком
лежит в дате 20:00. Завершённой считается смена, которую уже ничто не продлит: последнее
событие — «выключен» или тишина после него длиннее паузы, рвущей смену.

Статусы и колонки
-----------------
Ключи телефона групп ОП (`oktell_guard/phone.py`): «Активный» ('готов') у Основы, «Исход»
('исход', до 05.10.2026 и на старых телефонах — 'перезвон') у ЯР и Потока, «Автодозвон» у
Потока; поверх рабочего статуса телефон сам ставит фазы «Соединение» (набор, только на
исходящих) и «Разговор» ('занят'). Ручные паузы — «Перерыв», «Технический перерыв»,
«Тренинг». «Офлайн» — простой без звонков, куда телефон переводит сам, «выключен» — выход.

  7  «Время на исходе / Активный» — всё время в рабочем статусе, вместе с наборами и
     разговорами внутри него: столько горел этот статус на пилюле телефона;
  8  «Время в ожидании» — часть седьмой колонки без звонка: ни набора, ни разговора.
     Восьмая входит в седьмую, складывать их нельзя (ТЗ: «не суммировать»);
  9, 10 — «Перерыв» и «Технический перерыв»;
  14 «Фактическое отработанное время» — седьмая колонка плюс тренинг; без перерывов, тех.
     перерывов и офлайна. Каждая секунда смены лежит ровно в одном статусе, поэтому двойного
     учёта нет по построению.

Журнал раскладывает те же отрезки в семь статусов ТЗ: «В разговоре», «Активный / Исход»
(набор номера — подпись по рабочему статусу: «Активный», «Исход» или «Автодозвон»),
«Ожидание», «Перерыв», «Технический перерыв», «Тренинг», «Офлайн». Сумма «В разговоре»,
наборов и «Ожидания» — седьмая колонка, сумма «Ожидания» — восьмая.

Звонки
------
Колонки 5, 6, 11, 12 — по касаниям станции, не по телефону: станция видит и непринятые, и
попытки, которых телефон не заметил. ЯР и Поток — только исходящие; Основа — исходящие и
входящие, дошедшие до очереди и поставленные этому оператору, по правилам «Табло ОП»:
«Клиент сбросил» (трубку положили, пока звонил телефон) оператору не в счёт. Разговор —
чистый: `talk_measured_seconds` станции, а без него — billsec, у входящих поправленный по
событиям телефона (у суток до 21.09.2026 billsec входящего включает приветствие и ожидание).
Дозвон — разговор от `TALK_MIN_SECONDS` после соединения. Среднее (5) — общее время (6) на
ВСЕ попытки (11), включая нулевые, как просит ТЗ; усечение, не округление — как на табло.
"""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
import re

from cdr import touches as touches_mod

GROUP_MODEL_KEYS = {'op_osnova': 'osnova', 'op_yandex_reg': 'yar', 'op_potok': 'potok'}
GROUP_MODELS = tuple(GROUP_MODEL_KEYS)
GROUP_ORDER = ('osnova', 'yar', 'potok')
GROUP_LABELS = {'osnova': 'Основа', 'yar': 'ЯР', 'potok': 'Поток'}

SESSION_GAP = timedelta(hours=4)
QUIET_WORK_GAP = timedelta(hours=8)
MAX_SHIFT = timedelta(hours=16)
TALK_MIN_SECONDS = 10

# Звонок к смене: начался не раньше минуты до первого события и не позже двух минут после
# последнего. Телефон шлёт статус раньше регистрации линии, а «выключен» — после отбоя.
CALL_LEAD = timedelta(seconds=60)
CALL_TAIL = timedelta(seconds=120)

# Допуски сопоставления входящего с разговором по телефону — те же, что у табло
# (`snapshot.ANSWER_*_TOLERANCE_SECONDS`): «занят» внутри звонка, следующее событие — у его конца.
ANSWER_START_TOLERANCE = timedelta(seconds=2)
ANSWER_END_TOLERANCE_SECONDS = 6

KIND_TALK = 'talk'
KIND_DIAL = 'dial'
KIND_WAIT = 'wait'
KIND_BREAK = 'break'
KIND_TECH = 'tech'
KIND_TRAINING = 'training'
KIND_IDLE = 'idle_offline'
KIND_LOGOUT = 'logout'
KIND_OTHER = 'other'
_BASE = 'base'

BASE_ACTIVE = 'active'
BASE_OUTBOUND = 'outbound'
BASE_AUTODIAL = 'autodial'
BASE_LABELS = {BASE_ACTIVE: 'Активный', BASE_OUTBOUND: 'Исход', BASE_AUTODIAL: 'Автодозвон'}
DEFAULT_BASE = {'osnova': BASE_ACTIVE, 'yar': BASE_OUTBOUND, 'potok': BASE_OUTBOUND}

_BASE_KEYS = {'готов': BASE_ACTIVE, 'исход': BASE_OUTBOUND, 'перезвон': BASE_OUTBOUND,
              'автодозвон': BASE_AUTODIAL}
_TALK_KEYS = frozenset({'занят', 'занята'})
_DIAL_KEYS = frozenset({'соединение'})
_BREAK_KEYS = frozenset({'перерыв'})
_TRAINING_KEYS = frozenset({'тренинг'})
_IDLE_KEY = 'офлайн'
LOGOUT_KEY = 'выключен'
# «тех причина» приезжает и как `tech.break`, и как `тех_причина` — тот же разнобой, что
# разбирают каталог табло и учёт часов.
_TECH_FLAT = frozenset({'техпричина', 'techbreak', 'statustechbreak', 'техническийперерыв',
                        'техпауза', 'техническаяпауза'})

# Статусы, после которых тишина — это ожидание звонка, а не уход: пауза в них рвёт смену
# только длиннее QUIET_WORK_GAP.
_WORK_KINDS = frozenset({_BASE, KIND_TALK, KIND_DIAL})

JOURNAL_LABELS = {
    KIND_TALK: 'В разговоре',
    KIND_WAIT: 'Ожидание',
    KIND_BREAK: 'Перерыв',
    KIND_TECH: 'Технический перерыв',
    KIND_TRAINING: 'Тренинг',
    KIND_IDLE: 'Офлайн',
    KIND_LOGOUT: 'Офлайн',
}
JOURNAL_NOTES = {
    KIND_DIAL: 'набор номера',
    KIND_IDLE: 'простой без звонков',
    KIND_LOGOUT: 'выход из телефона',
}

_MONTHS_RU = ('Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август',
              'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь')


def normalize_key(value):
    """Ключ статуса как его сравнивают все копии правил: нижний регистр, одиночные пробелы."""
    return ' '.join(str(value or '').strip().lower().split())


def classify(key):
    """(вид, рабочий статус) по ключу события телефона.

    Рабочий статус есть только у самих рабочих статусов: «Активный», «Исход», «Автодозвон».
    Набор и разговор своего не имеют — они идут поверх того, что выбрано."""
    key = normalize_key(key)
    if key in _BASE_KEYS:
        return _BASE, _BASE_KEYS[key]
    if key in _TALK_KEYS:
        return KIND_TALK, None
    if key in _DIAL_KEYS:
        return KIND_DIAL, None
    if key in _BREAK_KEYS:
        return KIND_BREAK, None
    if key in _TRAINING_KEYS:
        return KIND_TRAINING, None
    if key == _IDLE_KEY:
        return KIND_IDLE, None
    if key == LOGOUT_KEY:
        return KIND_LOGOUT, None
    if key == 'тех причина' or re.sub(r'[\s._-]+', '', key) in _TECH_FLAT:
        return KIND_TECH, None
    return KIND_OTHER, None


def parse_moment(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace(' ', 'T'))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def stamp(value):
    return value.strftime('%Y-%m-%dT%H:%M:%S') if value else None


def canonical_events(events):
    """[(время, ключ)] без пустых переходов: из событий одной секунды остаётся последнее,
    повтор того же ключа подряд (телефон переподключился) — не переход."""
    out = []
    for at, key in sorted(((parse_moment(at), normalize_key(key)) for at, key in events or []
                           if parse_moment(at) is not None and normalize_key(key)),
                          key=lambda item: item[0]):
        if out and out[-1][0] == at:
            out[-1] = (at, key)
            if len(out) > 1 and out[-2][1] == key:
                out.pop()
            continue
        if out and out[-1][1] == key:
            continue
        out.append((at, key))
    return out


def _gap_breaks(prev_key, gap):
    """Рвёт ли пауза смену: после рабочего статуса — только долгая, после остального — от 4 ч."""
    kind, _ = classify(prev_key)
    if kind in _WORK_KINDS:
        return gap >= QUIET_WORK_GAP
    return gap >= SESSION_GAP


def split_shifts(events):
    """События одного телефона → смены [[(время, ключ)], ...] по правилам докстринга модуля.

    Сначала режем по паузам от 4 ч, потом сшиваем соседние куски, если перед паузой был
    рабочий статус, пауза короче QUIET_WORK_GAP и сшитая смена не длиннее MAX_SHIFT. Вторая
    проверка нужна потерянному «выключен»: вечерняя смена без выхода и утренняя следующего
    дня иначе слиплись бы в одну на сутки."""
    events = canonical_events(events)
    blocks = []
    for at, key in events:
        if not blocks or at - blocks[-1][-1][0] >= SESSION_GAP:
            blocks.append([])
        blocks[-1].append((at, key))
    merged = []
    for block in blocks:
        if merged:
            prev = merged[-1]
            gap = block[0][0] - prev[-1][0]
            if not _gap_breaks(prev[-1][1], gap) and block[-1][0] - prev[0][0] <= MAX_SHIFT:
                prev.extend(block)
                continue
        merged.append(list(block))
    shifts = []
    for block in merged:
        # Смена начинается со входа, а не с выхода: «выключен» в начале куска — хвост прошлого
        # сеанса или сон компьютера, рабочего времени в нём нет.
        while block and classify(block[0][1])[0] == KIND_LOGOUT:
            block.pop(0)
        if not block:
            continue
        # Одни «Офлайн» и выходы — это не смена: телефон открыли и не работали. Одно событие без
        # продолжения — тоже: у такой «смены» нет ни секунды, и строка нулей была бы выдумкой.
        if not any(classify(key)[0] not in (KIND_IDLE, KIND_LOGOUT) for _, key in block):
            continue
        if len(block) < 2:
            continue
        shifts.append(block)
    return shifts


def shift_finished(shift, now):
    """Смену уже ничто не продлит: последний статус — выход, или тишина после него длиннее
    паузы, которая рвёт смену. Идущая смена в отчёт не попадает — ТЗ про завершённые."""
    last_at, last_key = shift[-1]
    if classify(last_key)[0] == KIND_LOGOUT:
        return True
    return _gap_breaks(last_key, now - last_at)


def pieces(shift, group_key):
    """Отрезки смены [(вид, рабочий статус|None, начало, конец, секунды)] от события до события.

    Последнее событие отрезка не начинает: время после него неизвестно (см. докстринг)."""
    base = DEFAULT_BASE.get(group_key, BASE_OUTBOUND)
    out = []
    for (at, key), (next_at, _next_key) in zip(shift, shift[1:]):
        kind, key_base = classify(key)
        if kind == _BASE:
            base = key_base
            kind = KIND_WAIT
        seconds = int((next_at - at).total_seconds())
        if seconds <= 0:
            continue
        out.append((kind, base if kind in (KIND_TALK, KIND_DIAL, KIND_WAIT) else None,
                    at, next_at, seconds))
    return out


# Что из итогов статусов уходит в строку отчёта: колонки ТЗ и тренинг (он объясняет разницу
# между седьмой и четырнадцатой колонками). Разговор по телефону и офлайн — для проверок.
SHIFT_STATUS_FIELDS = ('active_seconds', 'waiting_seconds', 'break_seconds', 'tech_seconds',
                       'training_seconds', 'worked_seconds')


def status_totals(shift_pieces):
    totals = defaultdict(int)
    for kind, _base, _start, _end, seconds in shift_pieces:
        totals[kind] += seconds
    active = totals[KIND_TALK] + totals[KIND_DIAL] + totals[KIND_WAIT]
    return {
        'active_seconds': active,
        'waiting_seconds': totals[KIND_WAIT],
        'break_seconds': totals[KIND_BREAK],
        'tech_seconds': totals[KIND_TECH],
        'training_seconds': totals[KIND_TRAINING],
        'worked_seconds': active + totals[KIND_TRAINING],
        'phone_talk_seconds': totals[KIND_TALK],
        'offline_seconds': totals[KIND_IDLE] + totals[KIND_LOGOUT],
    }


def journal(shift, group_key):
    """Журнал статусов смены: [{kind, style, label, note, start_at, end_at, seconds}].

    Соседние отрезки одного вида сливаются («Исход» → «Перезвон» — одно ожидание). Выход в
    конце смены — строкой нулевой длины, как в образце ТЗ: видно, что человек вышел сам."""
    rows = []
    for kind, base, start, end, seconds in pieces(shift, group_key):
        style = base if kind == KIND_DIAL else kind
        if kind == KIND_DIAL:
            label = BASE_LABELS.get(base, 'Исход')
        elif kind == KIND_OTHER:
            label = None
        else:
            label = JOURNAL_LABELS[kind]
        if rows and rows[-1]['_key'] == (kind, style) and rows[-1]['_end'] == start:
            rows[-1]['_end'] = end
            rows[-1]['seconds'] += seconds
            continue
        rows.append({'_key': (kind, style), '_end': end, '_start': start, 'kind': kind,
                     'style': style, 'label': label, 'note': JOURNAL_NOTES.get(kind),
                     'seconds': seconds})
    # Незнакомый ключ показываем словом самого телефона: статус мог появиться раньше отчёта.
    other_labels = {}
    for at, key in shift:
        if classify(key)[0] == KIND_OTHER:
            other_labels.setdefault(at, key[:1].upper() + key[1:])
    out = []
    for row in rows:
        label = row['label'] or other_labels.get(row['_start']) or 'Другое'
        out.append({'kind': row['kind'], 'style': row['style'], 'label': label,
                    'note': row['note'], 'start_at': stamp(row['_start']),
                    'end_at': stamp(row['_end']), 'seconds': row['seconds']})
    last_at, last_key = shift[-1]
    if classify(last_key)[0] == KIND_LOGOUT:
        out.append({'kind': KIND_LOGOUT, 'style': KIND_LOGOUT, 'label': JOURNAL_LABELS[KIND_LOGOUT],
                    'note': JOURNAL_NOTES[KIND_LOGOUT], 'start_at': stamp(last_at),
                    'end_at': stamp(last_at), 'seconds': 0})
    return out


# ── Звонки ───────────────────────────────────────────────────────────────────────────────────


def talk_of(touch):
    """Чистый разговор касания: точное значение станции, а без него — billsec (у входящих
    уже поправленный по телефону, см. `attach_phone_talk`)."""
    measured = touch.get('talk_measured_seconds')
    if measured is not None and measured != '':
        try:
            return max(0, int(measured))
        except (TypeError, ValueError):
            pass
    try:
        return max(0, int(touch.get('talk_seconds') or 0))
    except (TypeError, ValueError):
        return 0


def attach_phone_talk(touches, events_by_ext):
    """Разговор принятых входящих — от «занят» до следующего события телефона.

    То же правило, что `snapshot.attach_answer_moments` у табло, но с поиском по отсортированному
    списку: там проход по событиям суток с начала на каждый звонок, а здесь месяц, и тот же
    проход стал бы квадратным. Касания с точным разговором станции или с моментом ответа не
    трогаются. events_by_ext — {внутренний номер: ([время], [в разговоре?])} по возрастанию."""
    out = []
    for touch in touches:
        if (touch.get('call_type') != touches_mod.TYPE_IN
                or touch.get('talk_measured_seconds') not in (None, '')
                or touch.get('answered_at') or int(touch.get('talk_seconds') or 0) <= 0):
            out.append(touch)
            continue
        found = None
        times, talking = events_by_ext.get(str(touch.get('ext') or ''), ((), ()))
        started = parse_moment(touch.get('started_at'))
        if times and started is not None:
            span = int(touch.get('dial_seconds') or touch.get('talk_seconds') or 0)
            end = started + timedelta(seconds=span)
            index = bisect_left(times, started - ANSWER_START_TOLERANCE)
            while index < len(times) and times[index] <= end + ANSWER_START_TOLERANCE:
                if talking[index] and index + 1 < len(times) and not talking[index + 1]:
                    if abs((times[index + 1] - end).total_seconds()) <= ANSWER_END_TOLERANCE_SECONDS:
                        found = (times[index], times[index + 1])
                        break
                index += 1
        if found is None:
            out.append(touch)
            continue
        enriched = dict(touch)
        # Как у табло: от «занят» до отбоя, усечённое до секунд; ноль не отдаём — иначе
        # принятый звонок превратился бы в непринятый.
        enriched['talk_seconds'] = max(1, int((found[1] - found[0]).total_seconds()))
        out.append(enriched)
    return out


def counted_call(touch, group_key):
    """Идёт ли касание в «Количество звонков» этой группы (см. докстринг модуля)."""
    call_type = touch.get('call_type') or ''
    if call_type == touches_mod.TYPE_OUT:
        return True
    if group_key != 'osnova' or call_type not in (touches_mod.TYPE_IN, touches_mod.TYPE_IN_MISSED):
        return False
    # Входящий без очереди — звонок на прямой внутренний номер, до линии ОП он не доходил;
    # табло его тоже не считает, и цифры двух экранов не должны расходиться.
    if not str(touch.get('queue') or '').strip():
        return False
    return not (call_type == touches_mod.TYPE_IN_MISSED
                and touch.get('result') == touches_mod.RESULT_CLIENT_HUNG_UP)


def call_columns(touches, group_key, talk_min=TALK_MIN_SECONDS):
    calls = connected = talk_total = 0
    for touch in touches or []:
        if not counted_call(touch, group_key):
            continue
        talk = talk_of(touch)
        calls += 1
        talk_total += talk
        if talk >= talk_min:
            connected += 1
    return {
        'calls': calls,
        'connected': connected,
        'talk_seconds': talk_total,
        'avg_talk_seconds': (talk_total // calls) if calls else None,
    }


# ── Сборка отчёта ────────────────────────────────────────────────────────────────────────────


def group_on(memberships, operator_id, day):
    """Группа ОП сотрудника на дату: (ключ, id группы) или (None, None).

    Членство — действующее на дату, при пересечении — с самым поздним началом, как у табло.
    Если в этот день он в группе не ОП (перевели), смена в отчёт не идёт."""
    best = None
    for item in (memberships or {}).get(operator_id) or ():
        start, end = item.get('start_date'), item.get('end_date')
        if start is None or start > day or (end is not None and end < day):
            continue
        rank = (start, item.get('id') or 0)
        if best is None or rank > best[0]:
            best = (rank, item)
    if best is None:
        return None, None
    item = best[1]
    return GROUP_MODEL_KEYS.get(str(item.get('model') or '').strip().lower()), item.get('group_id')


def build_report(*, events_by_operator, names, exts, memberships, touches, day_from, day_to,
                 now, talk_min=TALK_MIN_SECONDS):
    """Завершённые смены с началом в [day_from, day_to] и список операторов для фильтра.

    events_by_operator — {operator_id: [(время, ключ)]}; names — {id: ФИО};
    exts — {id: внутренний номер}; memberships — {id: [{start_date, end_date, group_id, model, id}]};
    touches — касания периода с запасом на ночь (`started_at` строкой или datetime)."""
    events_by_ext = {}
    for operator_id, events in (events_by_operator or {}).items():
        ext = str((exts or {}).get(operator_id) or '')
        if not ext:
            continue
        ordered = canonical_events(events)
        times = [at for at, _ in ordered]
        talking = [classify(key)[0] == KIND_TALK for _, key in ordered]
        previous = events_by_ext.get(ext)
        if previous:
            pairs = sorted(zip(list(previous[0]) + times, list(previous[1]) + talking))
            times, talking = [p[0] for p in pairs], [p[1] for p in pairs]
        events_by_ext[ext] = (times, talking)

    touches = attach_phone_talk(touches or [], events_by_ext)
    touches_by_ext = defaultdict(list)
    for touch in touches:
        started = parse_moment(touch.get('started_at'))
        ext = str(touch.get('ext') or '')
        if started is None or not ext:
            continue
        touches_by_ext[ext].append((started, touch))
    for items in touches_by_ext.values():
        items.sort(key=lambda item: item[0])
    starts_by_ext = {ext: [item[0] for item in items] for ext, items in touches_by_ext.items()}

    shifts = []
    operators = {}
    for operator_id, events in (events_by_operator or {}).items():
        for shift in split_shifts(events):
            start_at, end_at = shift[0][0], shift[-1][0]
            day = start_at.date()
            if day < day_from or day > day_to or not shift_finished(shift, now):
                continue
            group_key, _group_id = group_on(memberships, operator_id, day)
            if group_key is None:
                continue
            ext = str((exts or {}).get(operator_id) or '')
            shift_touches = []
            if ext and ext in touches_by_ext:
                starts = starts_by_ext[ext]
                lo = bisect_left(starts, start_at - CALL_LEAD)
                hi = bisect_right(starts, end_at + CALL_TAIL)
                shift_touches = [item[1] for item in touches_by_ext[ext][lo:hi]]
            logout = classify(shift[-1][1])[0] == KIND_LOGOUT
            row = {
                'key': '%s:%s' % (operator_id, stamp(start_at)),
                'date': day.isoformat(),
                'operator_id': operator_id,
                'name': (names or {}).get(operator_id) or '',
                'group_key': group_key,
                'group_label': GROUP_LABELS[group_key],
                'login_at': stamp(start_at),
                # Конец смены — её последнее событие: выход или, если он потерян, то, что было
                # перед ним. По этому же окну экран просит журнал смены.
                'logout_at': stamp(end_at),
                'logout_missing': not logout,
                'logout_next_day': end_at.date() != day,
            }
            row.update(call_columns(shift_touches, group_key, talk_min))
            totals = status_totals(pieces(shift, group_key))
            row.update({key: totals[key] for key in SHIFT_STATUS_FIELDS})
            shifts.append(row)
            entry = operators.setdefault(operator_id, {
                'id': operator_id, 'name': row['name'], 'group_keys': []})
            if group_key not in entry['group_keys']:
                entry['group_keys'].append(group_key)
    order = {key: index for index, key in enumerate(GROUP_ORDER)}
    shifts.sort(key=lambda r: (r['date'], order.get(r['group_key'], 9), r['name'], r['login_at']))
    roster = sorted(operators.values(), key=lambda item: item['name'])
    for item in roster:
        item['group_keys'].sort(key=lambda key: order.get(key, 9))
    return {'shifts': shifts, 'operators': roster}


def filter_shifts(shifts, group_key=None, operator_ids=None, days=None):
    """Те же фильтры, что на экране: группа, операторы, дни. Пустой фильтр — «все»."""
    wanted_ops = {int(v) for v in operator_ids or []}
    wanted_days = set(days or [])
    return [row for row in shifts or []
            if (not group_key or row['group_key'] == group_key)
            and (not wanted_ops or int(row['operator_id']) in wanted_ops)
            and (not wanted_days or row['date'] in wanted_days)]


# ── Период ───────────────────────────────────────────────────────────────────────────────────


def month_bounds(value, today):
    """'2026-10' → (первый день, последний день). Пусто — текущий месяц. ValueError на мусор."""
    raw = str(value or '').strip()
    if not raw:
        year, month = today.year, today.month
    else:
        match = re.fullmatch(r'(\d{4})-(\d{2})', raw)
        if not match or not 1 <= int(match.group(2)) <= 12:
            raise ValueError('Месяц должен быть в формате ГГГГ-ММ')
        year, month = int(match.group(1)), int(match.group(2))
    first = date(year, month, 1)
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    return first, last


def month_label(first):
    return '%s %d' % (_MONTHS_RU[first.month - 1], first.year)


def available_months(today, retention_days):
    """Месяцы, за которые есть сырые события телефона, — от нового к старому.

    События живут `retention_days` суток; месяц, целиком старше этого срока, показал бы «смен
    нет» вместо настоящих смен, а это и есть те выдуманные нули, которые ТЗ запрещает."""
    floor = today - timedelta(days=int(retention_days))
    out = []
    first = date(today.year, today.month, 1)
    while True:
        last = month_bounds('%04d-%02d' % (first.year, first.month), today)[1]
        if last < floor:
            break
        partial = first < floor
        # Месяц, задетый сроком хранения, честно подписан, с какого числа в нём есть статусы.
        label = month_label(first) + (' · с %s' % floor.strftime('%d.%m') if partial else '')
        out.append({'value': '%04d-%02d' % (first.year, first.month), 'label': label, 'partial': partial})
        first = date(first.year - (first.month == 1), (first.month - 2) % 12 + 1, 1)
    return out


def parse_days(value, first, last):
    """'2026-10-01,2026-10-03' → отсортированный список дней месяца. Чужие дни — ValueError."""
    out = set()
    for part in str(value or '').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            day = datetime.strptime(part, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError('День должен быть в формате ГГГГ-ММ-ДД')
        if day < first or day > last:
            raise ValueError('День вне выбранного месяца')
        out.add(day.isoformat())
    return sorted(out)


def parse_ids(value):
    out = []
    for part in str(value or '').split(','):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValueError('Оператор указан неверно')
        out.append(int(part))
    return out
