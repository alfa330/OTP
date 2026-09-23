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

Откуда ожидание и момент ответа
-------------------------------
С 21.09.2026 у нас есть журнал очередей самой станции (`asteriskcdrdb.queuelog`), и мост
приносит из него точные `queued_at` (вход в очередь), `answered_at`, `wait_seconds` и
`talk_measured_seconds` — см. `cdr/queue_facts.py`. Если у касания они есть, считается по
ним и больше ни по чему: это то, что записал сам Asterisk.

Всё, что описано ниже, — расчёт для касаний БЕЗ этих полей: прошлые сутки, собранные до
21.09, и любой день, когда журнал не прочитался. Аудит 17.09.2026 показал, чего этот
расчёт стоит: SL завышался на 3–5 п.п., у 15 % входящих ожидание выходило двухсекундным
вместо настоящего. Поэтому порядок предпочтений — станция, потом телефоны iCORE, потом
длина автоинформатора; убирать запасные пути нельзя, пока в базе есть сутки без журнала.

Ожидание тех, кто не дождался, до журнала не считалось вовсе (в CDR у брошенного звонка
длительность строки — это длина приветствия). Теперь оно есть, и живёт отдельной
величиной `avg_abandon_wait_seconds`: в ASA принятых его подмешивать нельзя.

Откуда момент ответа без журнала (и почему не из CDR)
-----------------------------------------------------
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

Откуда вход в очередь без журнала (и почему не `started_at`)
-------------------------------------------------------------
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

from cdr import queue_facts as queue_facts_mod, touches as touches_mod

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
            'talk_seconds': 0, 'talk_measured_seconds': 0, 'talk_measured': 0,
            'wait_seconds': 0, 'waited': 0,
            'abandon_wait_seconds': 0, 'abandon_measured': 0,
            'outgoing': 0, 'outgoing_answered': 0}


def _seconds(value):
    """Секунды из касания: None значит «неизвестно» и нулём не подменяется."""
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    # Усечение, а не округление: см. докстринг модуля. Разговор — по измеренным,
    # где они есть: у остальных billsec очереди включает ожидание и завышает разговор.
    measured_talk = bucket['talk_measured']
    if measured_talk:
        out['avg_talk_seconds'] = bucket['talk_measured_seconds'] // measured_talk
    else:
        out['avg_talk_seconds'] = bucket['talk_seconds'] // answered if answered else None
    out['avg_wait_seconds'] = (bucket['wait_seconds'] // waited if waited else None)
    # Сколько ждал тот, кто не дождался. Из CDR это не узнать вовсе (у брошенного
    # длительность строки — длина приветствия), поэтому величина появляется только
    # вместе с журналом очередей станции и живёт отдельно от ожидания принятых:
    # смешать их значило бы улучшать ASA чужими неудачами.
    abandoned = bucket['abandon_measured']
    out['avg_abandon_wait_seconds'] = (bucket['abandon_wait_seconds'] // abandoned
                                       if abandoned else None)
    out.pop('waited', None)
    out.pop('talk_measured_seconds', None)
    out.pop('abandon_wait_seconds', None)
    return out


# Приход звонка по linkedid считает `cdr.queue_facts`: то же правило нужно разделу
# «Касания» для длины приветствия, а двум копиям было бы где разойтись.
arrival_from_linkedid = queue_facts_mod.arrival_from_linkedid


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
        if touch.get('queued_at'):
            # Станция назвала вход в очередь сама (журнал очередей) — вычислять нечего.
            out.append(touch)
            continue
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


def _count_incoming(bucket, answered, talk, wait, sl_seconds, talk_measured=None,
                    lost_wait=None):
    """Один входящий в разрез: итоги дня и час считаются одним и тем же правилом."""
    bucket['arrived'] += 1
    if not answered:
        bucket['missed'] += 1
        if lost_wait is not None:
            bucket['abandon_measured'] += 1
            bucket['abandon_wait_seconds'] += max(0, lost_wait)
        return
    bucket['answered'] += 1
    bucket['talk_seconds'] += talk
    if talk_measured is not None:
        bucket['talk_measured'] += 1
        bucket['talk_measured_seconds'] += talk_measured
    if wait is not None:
        bucket['waited'] += 1
        bucket['wait_seconds'] += max(0, wait)
        if talk_measured is None:
            # Разговора без ожидания станция не назвала, но момент ответа известен из
            # событий телефона — а с ними `talk_seconds` касания уже и есть разговор
            # (attach_answer_moments переписал его от «занят» до «готов»).
            bucket['talk_measured'] += 1
            bucket['talk_measured_seconds'] += talk
        if wait <= sl_seconds:
            bucket['served_sl'] += 1


# Дальше этого от начала строки станции момент входа или прихода не уводим: так далеко
# ожидание не длится, и linkedid, значит, чужой.
_HOUR_MOMENT_MAX_DRIFT = timedelta(hours=1)


def _incoming_hour(touch, started):
    """Час входящего в разрезе по часам — час входа в очередь, а не начала строки станции.

    Строка станции у долгого ожидания начинается за пару секунд до ответа (докстринг модуля):
    звонок, пришедший в 09:58 и принятый в 10:01, по ней ушёл бы в десятый час вместе со своим
    ожиданием, и SL часа считался бы не за тот час. Без входа — приход по linkedid, без него —
    начало строки. Момент из других суток (пришёл в 23:59:50, в очередь попал после полуночи)
    остаётся в сутках касания: разрез суточный."""
    moment = _parse(touch.get('queued_at')) or arrival_from_linkedid(touch.get('linkedid')) or started
    if moment.date() != started.date() or abs(moment - started) > _HOUR_MOMENT_MAX_DRIFT:
        moment = started
    return moment.hour


def aggregate(touches, sl_seconds=DEFAULT_SL_SECONDS):
    """Итоги, разрез по часам, счётчики по внутренним номерам.

    Час разреза считается теми же правилами, что итоги дня (AR, SL, ожидание, разговор):
    отбивка шлёт показатели последнего часа рядом с итогами дня (владелец, 17.09.2026).

    Разреза по линиям (очередям станции) нет — решение владельца 16.09.2026: номера
    очередей вида 3010 на стене и в отбивке читались как шум. Поле `queue` касания
    на итоги не влияет."""
    totals = _bucket()
    hourly = [_bucket() for _ in range(24)]
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
        person = by_ext[ext] if ext else None
        if person is not None:
            person['calls'] += 1
            person['last_call_at'] = max(person['last_call_at'], touch.get('started_at') or '')

        if call_type == touches_mod.TYPE_OUT:
            answered = talk > 0
            for bucket in (totals, hourly[started.hour]):
                bucket['outgoing'] += 1
                bucket['outgoing_answered'] += 1 if answered else 0
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
        # Ожидание: сказанное станцией сильнее любого нашего вывода. Нет его — считаем
        # от входа в очередь (queued_at), а без входа — от начала строки, как раньше.
        exact_wait = _seconds(touch.get('wait_seconds'))
        queued = _parse(touch.get('queued_at')) or started
        if answered:
            wait = exact_wait if exact_wait is not None else (
                int((answered_at - queued).total_seconds()) if answered_at else None)
            lost_wait = None
        else:
            wait, lost_wait = None, exact_wait
        for bucket in (totals, hourly[_incoming_hour(touch, started)]):
            _count_incoming(bucket, answered, talk, wait, sl_seconds,
                            talk_measured=_seconds(touch.get('talk_measured_seconds')),
                            lost_wait=lost_wait)
        if person is not None:
            person['answered' if answered else 'missed'] += 1
            person['talk_seconds'] += talk if answered else 0

    return {
        'totals': _finish(totals),
        'hourly': [dict(_finish(bucket), hour=hour) for hour, bucket in enumerate(hourly)],
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
             announce_seconds=None, memberships=None, queue_owners=None, phone_events=None):
    """Снимок целиком. `bridge_state` — строка `cdr_agent_state` (live_at, last_seen_at);
    `announce_seconds` — {очередь: длина автоинформатора}, по которой касаниям уже поставлен
    `queued_at` (отдаётся в снимке для прозрачности: видно, от чего отсчитано ожидание).

    `memberships` — {operator_id: группа} (`group_catalog`), `queue_owners` — {очередь: id группы}
    (`queue_owner_groups`), `phone_events` — события телефонов с вечера прошлых суток: из них
    разрезы по группам и время входа (ТЗ #339). Без них снимок прежний."""
    parts = aggregate(touches, sl_seconds)
    rows = build_people(people, live_statuses, status_entry, parts['by_ext'], resolve_name)
    catalog = group_catalog(memberships)
    attach_groups(rows, memberships, catalog)
    attach_entry_times(rows, phone_events, day, status_entry)
    groups = group_breakdown(touches, rows, people, memberships, catalog, queue_owners, sl_seconds)
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
        'groups': groups,
        'bridge': {
            'connected': bool(bridge_state and bridge_state.get('connected')),
            'last_seen_at': _local_stamp((bridge_state or {}).get('last_seen_at')),
            'live_at': _local_stamp((bridge_state or {}).get('live_at')),
            'live_age_seconds': live_age,
        },
    }


# ── Группы, время входа и журнал статусов (ТЗ #339) ──────────────────────────────────────────
#
# Фильтр «Все / Основа / ЯР / Поток 1 / Поток 2» пересчитывает всё табло: плитки «сейчас», итоги
# дня, часы и список людей. Разрезы считаются здесь же, в том же снимке, а не отдельным запросом
# на группу: снимок один на всех зрителей и кэшируется, переключение на экране мгновенное, а
# отбивка и виджет по-прежнему читают итоги отдела целиком.

# Подпись группы — по модели расчёта, а не по имени группы: в имени стоит ФИО супервайзера
# («Ешан Алмас группа Основа»), и оно меняется вместе с людьми. «Яндекс Регистрация» — «ЯР», как
# в ТЗ: полное имя на кнопке фильтра не встаёт в ряд с остальными.
GROUP_MODEL_LABELS = {'op_osnova': 'Основа', 'op_yandex_reg': 'ЯР', 'op_potok': 'Поток'}
GROUP_MODEL_ORDER = ('op_osnova', 'op_yandex_reg', 'op_potok')
# Верификаторы работают в чатах Wazzup, на линии их нет: за 14 дней до 17.09.2026 у всех
# четырнадцати ни звонка, ни события телефона. На телефонном табло они были четырнадцатью
# строками «Нет событий», а в фильтре ТЗ их нет.
OFF_BOARD_GROUP_MODELS = ('op_verificator',)

_GROUP_HOURLY_FIELDS = ('hour', 'arrived', 'answered', 'missed', 'outgoing')


def _membership(memberships, operator_id):
    return (memberships or {}).get(operator_id) or {}


def on_board(people, memberships):
    """Состав табло без групп, которых на линии нет (верификаторы)."""
    return [person for person in people or []
            if _membership(memberships, person.get('id')).get('model') not in OFF_BOARD_GROUP_MODELS]


def group_catalog(memberships):
    """Группы табло в порядке показа: [{id, label}].

    У двух групп одной модели — номер по возрастанию id: «Поток 1» — группа 14, «Поток 2» — 38,
    так же их зовёт «Воронка ОП». Незнакомая модель подписывается именем группы и встаёт в конец."""
    groups = {}
    for item in (memberships or {}).values():
        if item.get('group_id') is None or item.get('model') in OFF_BOARD_GROUP_MODELS:
            continue
        groups[int(item['group_id'])] = item
    by_base = defaultdict(list)
    for group_id, item in groups.items():
        base = (GROUP_MODEL_LABELS.get(item.get('model'))
                or str(item.get('group_name') or '').strip() or 'Группа %d' % group_id)
        by_base[base].append(group_id)
    order = {model: index for index, model in enumerate(GROUP_MODEL_ORDER)}
    out = []
    for base, ids in by_base.items():
        ids.sort()
        for number, group_id in enumerate(ids, 1):
            out.append({'id': group_id, 'label': base if len(ids) == 1 else '%s %d' % (base, number),
                        '_order': (order.get(groups[group_id].get('model'), len(order)), base, group_id)})
    out.sort(key=lambda group: group['_order'])
    return [{'id': group['id'], 'label': group['label']} for group in out]


def attach_groups(rows, memberships, catalog):
    """Группа строке списка: id и подпись. Вне каталога (нет группы, «Нет в составе») — пусто."""
    labels = {group['id']: group['label'] for group in catalog}
    for row in rows:
        group_id = _membership(memberships, row.get('id')).get('group_id')
        row['group_id'] = group_id if group_id in labels else None
        row['group_label'] = labels.get(group_id, '')


def group_ext_map(people, memberships, catalog):
    """{внутренний номер: id группы} по составу табло."""
    known = {group['id'] for group in catalog}
    out = {}
    for person in people or []:
        ext = str(person.get('sip_number') or '')
        group_id = _membership(memberships, person.get('id')).get('group_id')
        if ext and group_id in known:
            out[ext] = group_id
    return out


QUEUE_OWNER_LOOKBACK_DAYS = 7


def queue_owner_groups(rows, ext_group):
    """{очередь: id группы} из троек (очередь, внутренний номер, принятых) за неделю.

    Нужна звонкам без внутреннего номера — брошенным в очереди раньше, чем станция позвала
    кого-то из людей. Сотрудника у такого звонка нет, и к группе его можно отнести только через
    очередь. Очередь принадлежит группе, которая приняла в ней БОЛЬШЕ ПОЛОВИНЫ звонков, принятых
    людьми из состава; иначе очередь ничья, и её потери видны только в «Все». На 17.09.2026 все
    очереди ОП принимала одна «Основа»: ЯР и оба потока работают исходящими."""
    counts = defaultdict(lambda: defaultdict(int))
    for queue, ext, count in rows or []:
        group_id = (ext_group or {}).get(str(ext or ''))
        key = _touch_queue(queue)
        try:
            count = int(count)
        except (TypeError, ValueError):
            continue
        if group_id is None or not key or count <= 0:
            continue
        counts[key][group_id] += count
    owners = {}
    for queue, by_group in counts.items():
        group_id, best = max(by_group.items(), key=lambda item: (item[1], -item[0]))
        if best * 2 > sum(by_group.values()):
            owners[queue] = group_id
    return owners


def touch_group(touch, ext_group, queue_owners):
    """Группа касания: по внутреннему номеру, а без номера — входящий по хозяину очереди.

    Потерянный входящий относится к хозяину очереди ВСЕГДА, даже если номер в касании есть.
    Номер у потерянного — это последний, кому очередь звонила, а не тот, кто за звонок
    отвечает: 23.09.2026 в очередях ОП сидела Перизат Нуржан (вн. 6661) без группы, очередь
    звонила ей 1943 раза за сутки и ни разу не дозвонилась, и шесть потерянных звонков
    «Основы» уходили к ней — то есть ни в одну группу. В фильтре «Основа» потерянных
    становилось меньше, чем во «Все», хотя вход принимает только «Основа».

    Номер есть, но в составе табло не найден — касание ничьё: сотрудника в списке группы нет,
    и его звонки в её итогах читались бы как чужие."""
    ext = str(touch.get('ext') or '')
    if touch.get('call_type') == touches_mod.TYPE_IN_MISSED:
        owner = (queue_owners or {}).get(_touch_queue(touch.get('queue')))
        if owner is not None:
            return owner
    if ext:
        return (ext_group or {}).get(ext)
    if touch.get('call_type') in _INCOMING_TYPES:
        return (queue_owners or {}).get(_touch_queue(touch.get('queue')))
    return None


def touches_of_group(touches, people, memberships, queue_answer_rows, group_id):
    """Касания одной группы — ровно по тем же правилам, что разрез группы на табло.

    Нужна отбивке «по группе»: там нужны итоги И почасовка с AR и SL, а разрез в снимке
    отдаёт час только полями графика. Считать группу вторым путём нельзя — цифра в
    Telegram разошлась бы с цифрой того же фильтра на стене, поэтому состав табло,
    хозяева очередей и отнесение касания берутся теми же функциями."""
    board = on_board(people, memberships)
    catalog = group_catalog(memberships)
    ext_group = group_ext_map(board, memberships, catalog)
    owners = queue_owner_groups(queue_answer_rows, ext_group)
    return [touch for touch in touches or [] if touch_group(touch, ext_group, owners) == group_id]


def group_breakdown(touches, rows, people, memberships, catalog, queue_owners,
                    sl_seconds=DEFAULT_SL_SECONDS):
    """Разрезы по группам: [{id, label, totals, hourly, now}] — те же правила, что у отдела.

    Час отдаётся только полями графика: полный набор показателей на каждую группу и каждый час
    раздувал бы снимок, который опрашивается раз в десять секунд."""
    if not catalog:
        return []
    ext_group = group_ext_map(people, memberships, catalog)
    split = {group['id']: [] for group in catalog}
    for touch in touches:
        group_id = touch_group(touch, ext_group, queue_owners)
        if group_id in split:
            split[group_id].append(touch)
    out = []
    for group in catalog:
        parts = aggregate(split[group['id']], sl_seconds)
        out.append({
            'id': group['id'],
            'label': group['label'],
            'totals': parts['totals'],
            'hourly': [{key: bucket[key] for key in _GROUP_HOURLY_FIELDS} for bucket in parts['hourly']],
            'now': count_now([row for row in rows if row.get('group_id') == group['id']]),
        })
    return out


# Время входа — начало рабочего блока: события телефона подряд без паузы от SESSION_GAP_HOURS. Не «первое
# событие суток»: ночная смена переходит через полночь (Кенжебай 17.09.2026: 19:53 → 08:00), и первое
# событие после 00:00 назвало бы входом середину смены. Смена принадлежит дню своего начала — общее
# правило проекта, — поэтому вход берётся из блока, начатого сегодня, а вчерашний (с пометкой «вчера»
# на экране) — только если сегодняшнего нет: вечерняя смена, кончившаяся в 00:02, не подменяет собой
# утренний вход в 08:28 (Артаев, тот же день).
#
# «Выключен» блок НЕ разрывает: телефон переподключается выходом и входом за пять секунд (тот же
# Кенжебай в 07:01), и вход ночника уехал бы на 07:01. Обед с выходом из аккаунта входа тоже не
# меняет. Четыре часа — по живым данным ОП и Тез КЦ за 10–17.09.2026: паузы внутри работы длиной
# 1–3 ч встречались 112 раз, 3–4 ч — 2, а от 4 ч — это уже промежуток между сменами. Окно назад —
# те же 16 часов, что у живого статуса.
ENTRY_LOOKBACK_HOURS = 16
SESSION_GAP_HOURS = 4


def events_by_operator(phone_events):
    """{operator_id: [(время, ключ статуса)]} по возрастанию времени; порядок базы при равном времени
    сохраняется — сортировка устойчивая."""
    out = defaultdict(list)
    for event in phone_events or []:
        at = _parse(event.get('event_at'))
        if at is None or event.get('operator_id') is None:
            continue
        out[event['operator_id']].append((at, event.get('status_key')))
    for events in out.values():
        events.sort(key=lambda item: item[0])
    return out


def _is_offline(status_entry, key):
    return status_entry(key)[1] == 'offline'


def work_blocks(events, gap_hours=SESSION_GAP_HOURS):
    """Рабочие блоки из событий одного телефона: новый блок — после паузы от `gap_hours`."""
    gap = timedelta(hours=gap_hours)
    blocks = []
    for at, key in events:
        if not blocks or at - blocks[-1][-1][0] >= gap:
            blocks.append([])
        blocks[-1].append((at, key))
    return blocks


def entry_moment(events, day, status_entry, gap_hours=SESSION_GAP_HOURS):
    """Время входа: начало первого блока, начатого в эти сутки, а без него — начало вчерашнего
    блока, в котором человек был на связи уже после полуночи."""
    day_start = datetime.combine(day, datetime.min.time())
    carried = None
    for block in work_blocks(events, gap_hours):
        if not any(at >= day_start and not _is_offline(status_entry, key) for at, key in block):
            continue
        if block[0][0] >= day_start:
            return block[0][0]
        carried = carried or block[0][0]
    return carried


def _stamp(value):
    return value.strftime('%Y-%m-%dT%H:%M:%S') if value else None


def attach_entry_times(rows, phone_events, day, status_entry):
    """`entry_at` строке списка. Нет событий телефона в эти сутки — None, на экране прочерк."""
    by_operator = events_by_operator(phone_events)
    for row in rows:
        events = by_operator.get(row.get('id')) if row.get('id') is not None else None
        row['entry_at'] = _stamp(entry_moment(events, day, status_entry)) if events else None


def status_journal(events, day, now, status_entry, gap_hours=SESSION_GAP_HOURS):
    """(вход, отрезки) — журнал статусов сотрудника за сутки табло.

    Сутки календарные, как у всего табло. Если в полночь человек был на связи (ночная смена),
    первый отрезок начинается в 00:00 со статусом, перешедшим через полночь, — иначе утро ночника
    начиналось бы с середины разговора. Соседние события с одним статусом сливаются: телефон шлёт
    повтор при переподключении, а человек статус не менял. Последний отрезок открыт — `end_at` None,
    секунды до `now` (экран дальше досчитывает их сам)."""
    events = list(events or [])
    day_start = datetime.combine(day, datetime.min.time())
    today = [(at, key) for at, key in events if at >= day_start]
    before = [(at, key) for at, key in events if at < day_start]
    sequence = today
    if before and not _is_offline(status_entry, before[-1][1]):
        next_at = today[0][0] if today else now
        if next_at - before[-1][0] < timedelta(hours=gap_hours):
            sequence = [(day_start, before[-1][1])] + today
    segments = []
    for at, key in sequence:
        label, style_key, _weight = status_entry(key)
        if segments and (segments[-1]['status_key'], segments[-1]['status_label']) == (style_key, label):
            continue
        if segments:
            segments[-1]['end'] = at
        segments.append({'status_key': style_key, 'status_label': label, 'start': at, 'end': None})
    return entry_moment(events, day, status_entry, gap_hours), [{
        'status_key': segment['status_key'],
        'status_label': segment['status_label'],
        'start_at': _stamp(segment['start']),
        'end_at': _stamp(segment['end']),
        'seconds': max(0, int(((segment['end'] or now) - segment['start']).total_seconds())),
    } for segment in segments]
