# -*- coding: utf-8 -*-
"""Снимок «Табло ОП»: итоги дня, разрезы и люди — из касаний и статусов телефонов.

Определения — те же, что на табло СЗоВ, иначе одна и та же величина горела бы на
двух стенах разным цветом:

  * входящие («дошедшие») — касания типов «Входящий» и «Входящий (не приняли)»;
  * AR = потеряно / входящих; коридор нормы задаётся снаружи (у СЗоВ 3–5 %);
  * SL = отвечено не позже порога / входящих; порог — снаружи (по умолчанию 20 с);
  * среднее время разговора УСЕКАЕТСЯ, не округляется — иначе расходится с отчётом
    владельца ровно на секунду в каждой строке;
  * «онлайн» = свободные + в разговоре; перерыв, тренинг, тех.причина, исход — не онлайн.

Откуда момент ответа (и почему не из CDR)
-----------------------------------------
SL и среднее ожидание считаются по `answered_at` касания — моменту, когда трубку снял
сотрудник. С обновлением станции 09.09.2026 («один звонок = одна строка») плечо агента из
выдачи CDR исчезло: очередь «отвечает» звонок сама в секунду входа (`duration == billsec`),
и когда подключился человек, станция больше не сообщает — `answered_at` из склейки пуст у
всех входящих. Поэтому момент ответа берётся из iCORE Phone: телефон оператора шлёт событие
«занят» в секунду ответа и «готов» в секунду отбоя (`operator_status_events`), и на живых
данных 15.09.2026 они сошлись с началом и концом звонка по CDR в пределах секунды у 364 из
379 принятых. `attach_answer_moments` подставляет `answered_at` и настоящий разговор
(от «занят» до «готов») тем касаниям, где такая пара нашлась; остальные остаются как есть.

SL по неполному измерению: у части телефонов событий нет (не iCORE Phone, тишина). SL
считается по измеренным и растягивается на всех принятых: доля «в срок» среди измеренных ×
доля принятых среди дошедших. `wait_measured` в итогах говорит, по скольким принятым момент
ответа известен; при нуле измеренных SL и ожидание — None (на экране «—»), а не 0 %: ноль
здесь читался бы как катастрофа на линии.

Откуда вход в очередь (и почему не `started_at`)
------------------------------------------------
Ожидание считается от входа в очередь, а не от прихода звонка на станцию (решение владельца
16.09.2026): перед каждой очередью ОП играет автоинформатор или IVR почти постоянной длины
(Jana → 3034 16 с, iTaxi → 3001 и Ноль Такси → 3041 26 с, Центр регистрации → 3010 7 с,
IVR-5 → 3006 19 с…), и это ожиданием не является. Приход звонка известен всегда: целая часть
`linkedid` — секунда создания канала (с ней же совпадает время в имени файла записи).

Станция отдаёт на звонок одну строку, и `started_at` касания бывает трёх видов (разбор
10–17.09.2026, 2290 входящих, момент ответа по iCORE Phone):
  * приход — `started_at` = приход, автоинформатор внутри длительности строки;
  * вход в очередь — `started_at` = приход + длина автоинформатора;
  * начало удачного дозвона — `started_at` ≈ ответ − 2 с, где угодно после входа. Так станция
    отдаёт звонки с ДОЛГИМ ожиданием (15 % входящих, ожидание у них медиана 22 с, p90 68 с).
Поэтому вход в очередь = приход + длина автоинформатора, а не `started_at` и не max из двух:
с max() третий вид строк получал ожидание ≈ 2 с, и 142 звонка за восемь дней с ожиданием
больше 20 с попали в SL (`attach_queue_entry`).

Длина нигде не хранится и вычисляется из самих касаний: у строк входа в очередь
`started_at − приход` равен ей с точностью до секунды (`announcement_seconds_from_deltas`).
Длина берётся на очередь, хотя вход задаёт номер (DID): в 3000 ведут автоинформатор 15 с и
IVR 19 с, в 3001 — автоинформатор 26 с и IVR 7 с. На данных 12–17.09 это расходится с
расчётом по номеру не больше чем на 0,3 п.п. SL; номера в касаниях нет. Очереди, где длина
не нашлась (единичные звонки), правки не получают — там `started_at` остаётся точкой отсчёта.

Здесь нет ни базы, ни Flask: на вход — списки словарей, на выход — словарь снимка.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from cdr import touches as touches_mod

DEFAULT_SL_SECONDS = 20
DEFAULT_AR_MIN_PERCENT = 3
DEFAULT_AR_MAX_PERCENT = 5

# Ключи оформления статусов, которые считаются «онлайн». Тот же смысл, что у СЗоВ.
ONLINE_STATUS_KEYS = ('free', 'talking')
# Что на табло зовётся «на перерыве» одной цифрой: любой сознательный уход с линии.
PAUSE_STATUS_KEYS = ('break', 'tech', 'training')

_INCOMING_TYPES = (touches_mod.TYPE_IN, touches_mod.TYPE_IN_MISSED)


# Смещение Алматы от UTC — сдвигом, а не ZoneInfo, как в cdr/queries.py: у Казахстана
# с 01.03.2024 одна зона без перевода часов, а tzdata на контейнере может не оказаться.
_ALMATY = timezone(timedelta(hours=5))


def _parse(text):
    """Отметка из базы или моста → наивное время Алматы.

    `cdr_agent_state` хранит TIMESTAMPTZ, а сессия Postgres на Render живёт в UTC, поэтому
    `live_at` приезжает строкой «2026-09-15T20:18:53+00:00». Раньше хвост с поясом просто
    срезался, и UTC сравнивался с алматинским «сейчас»: табло писало «данные на 20:18:53»
    вместо 01:18:53 и «последнее обновление 5:00:10 назад» при живом мосте, а отбивка
    считала мост замолчавшим. Отметки без пояса (касания) уже местные и не трогаются."""
    raw = str(text or '').strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        try:
            value = datetime.strptime(raw[:19].replace('T', ' '), '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
    if value.tzinfo is not None:
        value = value.astimezone(_ALMATY).replace(tzinfo=None)
    return value


def _local_stamp(value):
    """Отметка для экрана: время Алматы без пояса. formatClock на фронте берёт часы
    из строки как есть и ничего не переводит — UTC он показал бы как местное."""
    parsed = _parse(value)
    return parsed.strftime('%Y-%m-%dT%H:%M:%S') if parsed else None


def _ratio(numerator, denominator):
    return (numerator / denominator) if denominator else None


def _bucket():
    return {'arrived': 0, 'answered': 0, 'missed': 0, 'served_sl': 0,
            'talk_seconds': 0, 'talk_measured_seconds': 0, 'wait_seconds': 0, 'waited': 0,
            'outgoing': 0, 'outgoing_answered': 0}


def _finish(bucket):
    """Дописать производные к сырым счётчикам разреза."""
    out = dict(bucket)
    out['ar'] = _ratio(bucket['missed'], bucket['arrived'])
    answered, waited, arrived = bucket['answered'], bucket['waited'], bucket['arrived']
    # SL по измеренным, растянутый на всех принятых (см. докстринг модуля). Принятые без
    # единого измерения — не «никого не обслужили за 20 с», а «момент ответа неизвестен»;
    # при нуле принятых SL честно нулевой — все дошедшие потеряны.
    if waited:
        out['sl'] = (bucket['served_sl'] / waited) * (answered / arrived) if arrived else None
    elif not answered:
        out['sl'] = _ratio(0, arrived)
    else:
        out['sl'] = None
    out['wait_measured'] = waited
    # Усечение, а не округление: см. докстринг модуля. Разговор — по измеренным телефоном,
    # где они есть: у остальных billsec очереди включает ожидание и завышает разговор.
    if waited:
        out['avg_talk_seconds'] = bucket['talk_measured_seconds'] // waited
    else:
        out['avg_talk_seconds'] = bucket['talk_seconds'] // answered if answered else None
    out['avg_wait_seconds'] = (bucket['wait_seconds'] // waited if waited else None)
    out.pop('waited', None)
    out.pop('talk_measured_seconds', None)
    return out


def arrival_from_linkedid(linkedid):
    """Секунда прихода звонка на станцию из linkedid («1789533127.1074887») — наивное время
    Алматы. Хвост после точки — порядковый номер канала, к времени отношения не имеет."""
    head = str(linkedid or '').split('.', 1)[0]
    if not head.isdigit():
        return None
    return datetime.fromtimestamp(int(head), tz=_ALMATY).replace(tzinfo=None)


def _touch_queue(value):
    """Очередь касания для поиска длины автоинформатора; через несколько очередей — первая."""
    parts = [p for p in str(value or '').split(',') if p]
    return min(parts) if parts else ''


# Длина автоинформатора признаётся постоянной, если вокруг самой частой задержки (±1 с)
# собралось не меньше стольких строк и не меньше такой доли всех положительных задержек.
# Доля — 30 %, а не половина: положительные задержки дают и строки «начало дозвона» (ответ − 2 с),
# разбросанные по всему ожиданию, и очереди с двумя входами. При 50 % 3006 (IVR 19 с, 13 из 27)
# и 3000 (15 с и 19 с) длину то получали, то теряли, и приветствие считалось ожиданием.
# Кнопочное меню, где каждый жмёт в своё время, кластера не даёт и порог по-прежнему не проходит.
ANNOUNCEMENT_MIN_SAMPLES = 5
ANNOUNCEMENT_MIN_SHARE = 0.3
ANNOUNCEMENT_MAX_SECONDS = 120


def announcement_seconds_from_deltas(rows, min_samples=ANNOUNCEMENT_MIN_SAMPLES,
                                     min_share=ANNOUNCEMENT_MIN_SHARE):
    """{очередь: длина автоинформатора, с} из троек (очередь, started_at − приход, число строк).

    Считаются только положительные задержки в разумных пределах: ноль — строка прихода,
    а не входа; больше двух минут — повторный вход в очередь после перевода, не автоинформатор."""
    counts = defaultdict(lambda: defaultdict(int))
    for queue, delta, count in rows or []:
        try:
            delta, count = int(delta), int(count)
        except (TypeError, ValueError):
            continue
        if 0 < delta <= ANNOUNCEMENT_MAX_SECONDS and count > 0 and queue:
            counts[str(queue)][delta] += count
    result = {}
    for queue, deltas in counts.items():
        total = sum(deltas.values())
        mode = max(deltas, key=lambda d: (deltas[d], -d))
        cluster = sum(n for d, n in deltas.items() if abs(d - mode) <= 1)
        if cluster >= min_samples and cluster >= min_share * total:
            result[queue] = mode
    return result


def attach_queue_entry(touches, announce_seconds):
    """Подставить входящим `queued_at` — момент входа в очередь (см. докстринг модуля).

    Вход = приход + длина автоинформатора очереди, какая бы строка ни пришла от станции:
    `started_at` позже входа — это начало удачного дозвона, и отсчёт от него съел бы ожидание.
    Если длины у очереди нет или linkedid не разбирается, `queued_at` не ставится, и отсчёт
    остаётся от started_at. Возвращает новые словари, исходные не трогает."""
    out = []
    for touch in touches:
        started = _parse(touch.get('started_at'))
        seconds = (announce_seconds or {}).get(_touch_queue(touch.get('queue')))
        arrival = arrival_from_linkedid(touch.get('linkedid'))
        if (touch.get('call_type') not in _INCOMING_TYPES or started is None
                or seconds is None or arrival is None):
            out.append(touch)
            continue
        queued = arrival + timedelta(seconds=int(seconds))
        span = int(touch.get('dial_seconds') or touch.get('talk_seconds') or 0)
        if queued > started + timedelta(seconds=span):
            # Автоинформатор длиннее самого звонка — так не бывает у дошедшего до очереди;
            # значит, linkedid чужой или длина устарела. Честнее не править вовсе.
            out.append(touch)
            continue
        enriched = dict(touch)
        enriched['queued_at'] = queued.strftime('%Y-%m-%d %H:%M:%S')
        out.append(enriched)
    return out


# Допуски сопоставления касания с событиями телефона. «Занят» ищем от начала звонка (минус
# рассинхрон часов), «готов» обязан лечь на конец звонка по CDR: на живых данных расхождение
# не превышало полутора секунд, шесть — с запасом на очередь событий в телефоне.
ANSWER_START_TOLERANCE_SECONDS = 2
ANSWER_END_TOLERANCE_SECONDS = 6


def attach_answer_moments(touches, phone_events, ext_by_operator, is_talking,
                          start_tolerance=ANSWER_START_TOLERANCE_SECONDS,
                          end_tolerance=ANSWER_END_TOLERANCE_SECONDS):
    """Подставить принятым входящим момент ответа и разговор по событиям iCORE Phone.

    phone_events — [{operator_id, event_at, status_key}] за сутки, только события телефона;
    ext_by_operator — {operator_id: внутренний номер}; is_talking(status_key) — «в разговоре»
    по каталогу статусов. Пара берётся строго: «занят» внутри звонка И следующее событие
    того же телефона у конца звонка по CDR — иначе за ответ можно принять исходящий, который
    оператор успел сделать, пока входящий ждал в очереди. Возвращает новые словари, исходные
    не трогает; касания без пары остаются как были."""
    by_ext = defaultdict(list)
    for event in phone_events or []:
        ext = ext_by_operator.get(event.get('operator_id'))
        at = _parse(event.get('event_at'))
        if not ext or at is None:
            continue
        by_ext[str(ext)].append((at, bool(is_talking(event.get('status_key')))))
    for events in by_ext.values():
        events.sort(key=lambda item: item[0])

    start_slack = timedelta(seconds=start_tolerance)
    end_slack = timedelta(seconds=end_tolerance)
    out = []
    for touch in touches:
        events = by_ext.get(str(touch.get('ext') or ''))
        started = _parse(touch.get('started_at'))
        if (not events or started is None or touch.get('call_type') != touches_mod.TYPE_IN
                or int(touch.get('talk_seconds') or 0) <= 0 or touch.get('answered_at')):
            out.append(touch)
            continue
        span = int(touch.get('dial_seconds') or touch.get('talk_seconds') or 0)
        end = started + timedelta(seconds=span)
        found = None
        for index, (at, talking) in enumerate(events):
            if at < started - start_slack:
                continue
            if at > end + start_slack:
                break
            if not talking or index + 1 >= len(events):
                continue
            next_at, next_talking = events[index + 1]
            if not next_talking and abs((next_at - end).total_seconds()) <= end_tolerance:
                found = (at, next_at)
                break
        if found is None:
            out.append(touch)
            continue
        answered_at, hung_up_at = found
        enriched = dict(touch)
        enriched['answered_at'] = max(answered_at, started).strftime('%Y-%m-%d %H:%M:%S')
        # Разговор — от ответа до отбоя по телефону, усечённый до секунд; ноль не отдаём,
        # иначе принятый звонок превратился бы в потерянный.
        enriched['talk_seconds'] = max(1, int((hung_up_at - answered_at).total_seconds()))
        enriched['answer_source'] = 'phone'
        out.append(enriched)
    return out


def aggregate(touches, sl_seconds=DEFAULT_SL_SECONDS):
    """Итоги, разрез по часам, счётчики по внутренним номерам.

    Разреза по линиям (очередям станции) нет — решение владельца 16.09.2026: номера
    очередей вида 3010 на стене и в отбивке читались как шум. Поле `queue` касания
    на итоги не влияет."""
    totals = _bucket()
    hourly = [dict(hour=h, arrived=0, answered=0, missed=0, outgoing=0) for h in range(24)]
    by_ext = defaultdict(lambda: {'answered': 0, 'missed': 0, 'outgoing': 0,
                                  'outgoing_answered': 0, 'talk_seconds': 0,
                                  'calls': 0, 'last_call_at': ''})

    for touch in touches:
        started = _parse(touch.get('started_at'))
        if started is None:
            continue
        call_type = touch.get('call_type') or ''
        talk = int(touch.get('talk_seconds') or 0)
        ext = str(touch.get('ext') or '')
        hour = hourly[started.hour]
        person = by_ext[ext] if ext else None
        if person is not None:
            person['calls'] += 1
            person['last_call_at'] = max(person['last_call_at'], touch.get('started_at') or '')

        if call_type == touches_mod.TYPE_OUT:
            answered = talk > 0
            totals['outgoing'] += 1
            totals['outgoing_answered'] += 1 if answered else 0
            hour['outgoing'] += 1
            if person is not None:
                person['outgoing'] += 1
                person['outgoing_answered'] += 1 if answered else 0
                person['talk_seconds'] += talk if answered else 0
            continue

        if call_type not in _INCOMING_TYPES:
            continue
        if not str(touch.get('queue') or '').strip():
            # Входящий без очереди — звонок на прямой внутренний номер (DID → 2030 и подобные),
            # до очереди ОП он не доходил. «Дошедшие» табло — только попавшие в очередь, как у
            # СЗоВ; сверка с CDR станции за 15.09.2026 сходится строка в строку лишь так.
            continue
        answered = call_type == touches_mod.TYPE_IN and talk > 0
        answered_at = _parse(touch.get('answered_at'))
        # Ожидание — от входа в очередь (queued_at), а без него — от начала строки.
        queued = _parse(touch.get('queued_at')) or started
        wait = int((answered_at - queued).total_seconds()) if (answered and answered_at) else None
        totals['arrived'] += 1
        if answered:
            totals['answered'] += 1
            totals['talk_seconds'] += talk
            if wait is not None:
                totals['waited'] += 1
                totals['wait_seconds'] += max(0, wait)
                totals['talk_measured_seconds'] += talk
                if wait <= sl_seconds:
                    totals['served_sl'] += 1
        else:
            totals['missed'] += 1
        hour['arrived'] += 1
        hour['answered' if answered else 'missed'] += 1
        if person is not None:
            person['answered' if answered else 'missed'] += 1
            person['talk_seconds'] += talk if answered else 0

    return {
        'totals': _finish(totals),
        'hourly': hourly,
        'by_ext': dict(by_ext),
    }


def _status(entry, live):
    label, key, weight = entry
    return {
        'status_label': label,
        'status_key': key,
        'status_weight': weight,
        'status_seconds': int(live.get('seconds') or 0) if live else None,
        'status_at': str(live.get('event_at') or '') if live else '',
    }


def build_people(people, live_statuses, status_entry, by_ext, resolve_name):
    """Список людей табло: состав отдела с живым статусом и счётчиками дня, плюс
    внутренние номера, которые сегодня звонили, но в составе не нашлись."""
    rows = []
    seen_exts = set()
    for person in people:
        ext = str(person.get('sip_number') or '')
        live = live_statuses.get(person.get('id')) or {}
        entry = status_entry(live.get('status_key'))
        stats = by_ext.get(ext) or {}
        row = {
            'id': person.get('id'), 'name': person.get('name') or '', 'ext': ext,
            'answered': stats.get('answered', 0), 'missed': stats.get('missed', 0),
            'outgoing': stats.get('outgoing', 0),
            'outgoing_answered': stats.get('outgoing_answered', 0),
            'talk_seconds': stats.get('talk_seconds', 0),
            'last_call_at': stats.get('last_call_at', ''),
            'in_roster': True,
        }
        row.update(_status(entry, live))
        rows.append(row)
        if ext:
            seen_exts.add(ext)
    for ext, stats in by_ext.items():
        if not ext or ext in seen_exts:
            continue
        # Звонил, но в составе отдела не числится: показать честно, а не спрятать —
        # это либо чужой номер на линии продаж, либо карточка без sip_number.
        name = resolve_name(ext) if resolve_name else ''
        row = {
            'id': None, 'name': name or ('Номер %s' % ext), 'ext': ext,
            'answered': stats['answered'], 'missed': stats['missed'],
            'outgoing': stats['outgoing'], 'outgoing_answered': stats['outgoing_answered'],
            'talk_seconds': stats['talk_seconds'], 'last_call_at': stats['last_call_at'],
            'in_roster': False,
        }
        row.update(_status(('Нет в составе', 'unknown', 95), {}))
        rows.append(row)
    rows.sort(key=lambda r: (r['status_weight'], r['name']))
    return rows


def count_now(people_rows):
    counts = defaultdict(int)
    for row in people_rows:
        if row.get('in_roster'):
            counts[row['status_key']] += 1
    return {
        'operators_total': sum(1 for r in people_rows if r.get('in_roster')),
        'operators_online': sum(counts[k] for k in ONLINE_STATUS_KEYS),
        'operators_free': counts['free'],
        'operators_talking': counts['talking'],
        'operators_outgoing': counts['outgoing'],
        'operators_on_break': sum(counts[k] for k in PAUSE_STATUS_KEYS),
        'operators_offline': counts['offline'],
        'operators_unknown': counts['unknown'],
        'operators_other': counts['other'],
    }


def assemble(*, day, touches, people, live_statuses, status_entry, resolve_name,
             bridge_state, now, sl_seconds=DEFAULT_SL_SECONDS,
             ar_min_percent=DEFAULT_AR_MIN_PERCENT, ar_max_percent=DEFAULT_AR_MAX_PERCENT,
             announce_seconds=None):
    """Снимок целиком. `bridge_state` — строка `cdr_agent_state` (live_at, last_seen_at);
    `announce_seconds` — {очередь: длина автоинформатора}, по которой касаниям уже поставлен
    `queued_at` (отдаётся в снимке для прозрачности: видно, от чего отсчитано ожидание)."""
    parts = aggregate(touches, sl_seconds)
    rows = build_people(people, live_statuses, status_entry, parts['by_ext'], resolve_name)
    live_at = _parse(bridge_state.get('live_at')) if bridge_state else None
    live_age = int((now - live_at).total_seconds()) if live_at else None
    return {
        'day': day.isoformat(),
        'captured_at': now.strftime('%Y-%m-%dT%H:%M:%S'),
        'sl_threshold_seconds': sl_seconds,
        'announcement_seconds': dict(announce_seconds or {}),
        'ar_min_percent': ar_min_percent,
        'ar_max_percent': ar_max_percent,
        'totals': parts['totals'],
        'hourly': parts['hourly'],
        'now': count_now(rows),
        'operators': rows,
        'bridge': {
            'connected': bool(bridge_state and bridge_state.get('connected')),
            'last_seen_at': _local_stamp((bridge_state or {}).get('last_seen_at')),
            'live_at': _local_stamp((bridge_state or {}).get('live_at')),
            'live_age_seconds': live_age,
        },
    }
