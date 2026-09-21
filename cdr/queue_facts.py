# -*- coding: utf-8 -*-
"""Факты очереди из журнала станции (`asteriskcdrdb.queuelog`) — вход, ответ, ожидание,
разговор и сторона отбоя. Разбор общий для моста и портала, поэтому живёт в `cdr/`.

Зачем, если есть CDR
--------------------
Надстройка станции с 09.09.2026 отдаёт один звонок одной строкой, и какая именно строка
станет представителем — как повезёт: то приход звонка, то вход в очередь, то начало
удачного дозвона за пару секунд до ответа. Отсюда и вся арифметика табло ОП: длина
автоинформатора, вычисленная из недели касаний, момент ответа из событий телефонов iCORE.
Аудит 17.09.2026 показал, чего это стоит: SL завышался на 3–5 п.п., а у 15 % входящих
ожидание считалось равным двум секундам вместо настоящих двадцати.

Сам Asterisk всё это пишет точно и в одно место — журнал очередей. С 21.09.2026 он нам
доступен: станция ведёт его не файлом, а таблицей `queuelog` в своей базе. Одна строка на
событие, `callid` совпадает с `linkedid` касания.

Что дают события (проверено на живых звонках 21.09.2026)
--------------------------------------------------------
    ENTERQUEUE      вход в очередь — та самая точка отсчёта ожидания; data2 — номер клиента
    CONNECT         ответ агента; data1 — ожидание, с; data3 — сколько звонил телефон
    COMPLETECALLER  разговор закончил КЛИЕНТ;   data1 — ожидание, data2 — разговор, с
    COMPLETEAGENT   разговор закончил ОПЕРАТОР; поля те же
    ABANDON         клиент бросил трубку в очереди; data3 — сколько он ждал
    EXITWITHTIMEOUT очередь сдалась по таймауту; data3 — то же ожидание
    RINGNOANSWER    попытка дозвона до агента; их тысячи в сутки, нам не нужны

Сутки станции 21.09.2026 сходятся: 431 вход = 375 ответов + 56 брошенных.

Чего здесь нет
--------------
Пауз операторов (`PAUSE`/`UNPAUSE`) — их в журнале 99 %, а к звонкам они отношения не
имеют. И попыток дозвона: `RINGNOANSWER` пишется каждые две секунды на каждого агента.
Мост их не читает вовсе, иначе за одними сутками пришлось бы тянуть сотню тысяч строк.
"""

from datetime import datetime

from cdr import touches as touches_mod

# Кого правим: у исходящего очереди нет, и совпадение callid у него означало бы чужой звонок.
INCOMING_TYPES = (touches_mod.TYPE_IN, touches_mod.TYPE_IN_MISSED)

# События, ради которых мост вообще ходит в журнал. Список — часть договора с запросом:
# он же стоит в `IN (...)` у моста, чтобы станция не отдавала лишнего.
WANTED_EVENTS = (
    'ENTERQUEUE',
    'CONNECT',
    'COMPLETECALLER',
    'COMPLETEAGENT',
    'ABANDON',
    'EXITWITHTIMEOUT',
    'EXITEMPTY',
    'EXITWITHKEY',
)

# Чем закончился звонок для того, кто ждал в очереди.
_LOST_EVENTS = ('ABANDON', 'EXITWITHTIMEOUT', 'EXITEMPTY', 'EXITWITHKEY')

HANGUP_CLIENT = 'client'
HANGUP_OPERATOR = 'operator'

# Ожидание дольше этого — не ожидание, а мусор: перепутанный callid или переезд звонка
# между очередями через перевод. Час с лишним в очереди никто не ждёт.
MAX_WAIT_SECONDS = 3600
# Столько же на разговор не ставим: разговоры по часу редки, но бывают (разбор жалобы).
MAX_TALK_SECONDS = 12 * 3600


def _moment(value):
    """Время события → наивное время Алматы. База станции живёт в местном поясе (+05),
    как и `calldate` в CDR, поэтому переводить ничего не нужно — только привести тип."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:19].replace('T', ' '), '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _seconds(value, limit):
    """Число секунд из поля data. Станция кладёт туда строку, пустую или с мусором."""
    try:
        number = int(str(value or '').strip())
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= limit else None


def _stamp(moment):
    return moment.strftime('%Y-%m-%d %H:%M:%S') if moment else ''


def build_facts(rows):
    """Строки журнала → {callid: факты звонка}.

    Строка — словарь `{time, callid, queuename, agent, event, data1, data2, data3}`
    (ровно колонки `queuelog`). Порядок строк не важен: события разбираются по смыслу.

    Повторные входы одного звонка в очередь (перевод, возврат из IVR) не плодят второй
    факт: берётся ПЕРВЫЙ вход и ПЕРВЫЙ ответ. Клиент ждал с первого входа, и SL считается
    от него — иначе перевод обнулял бы ожидание задним числом.
    """
    facts = {}
    for row in rows or []:
        callid = str((row or {}).get('callid') or '').strip()
        event = str((row or {}).get('event') or '').strip().upper()
        moment = _moment((row or {}).get('time'))
        if not callid or not event or moment is None:
            continue
        fact = facts.setdefault(callid, {
            'callid': callid, 'queue': '', 'queued_at': None, 'answered_at': None,
            'wait_seconds': None, 'talk_seconds': None, 'hangup_side': '',
            'agent': '', 'lost': False,
        })
        queue = str(row.get('queuename') or '').strip()
        if queue and queue != 'NONE' and not fact['queue']:
            fact['queue'] = queue
        agent = str(row.get('agent') or '').strip()

        if event == 'ENTERQUEUE':
            if fact['queued_at'] is None or moment < fact['queued_at']:
                fact['queued_at'] = moment
        elif event == 'CONNECT':
            if fact['answered_at'] is None or moment < fact['answered_at']:
                fact['answered_at'] = moment
                fact['wait_seconds'] = _seconds(row.get('data1'), MAX_WAIT_SECONDS)
                if agent and agent != 'NONE':
                    fact['agent'] = agent
        elif event in ('COMPLETECALLER', 'COMPLETEAGENT'):
            fact['hangup_side'] = HANGUP_CLIENT if event == 'COMPLETECALLER' else HANGUP_OPERATOR
            talk = _seconds(row.get('data2'), MAX_TALK_SECONDS)
            if talk is not None:
                fact['talk_seconds'] = talk
            if agent and agent != 'NONE' and not fact['agent']:
                fact['agent'] = agent
        elif event in _LOST_EVENTS:
            fact['lost'] = True
            if fact['wait_seconds'] is None:
                fact['wait_seconds'] = _seconds(row.get('data3'), MAX_WAIT_SECONDS)
            fact.setdefault('lost_at', moment)

    for fact in facts.values():
        _fill_gaps(fact)
    return facts


def _fill_gaps(fact):
    """Досчитать то, чего станция не положила в data, по временам событий.

    Поля data пустеют редко (на живых сутках — ни разу), но ожидание — главная величина
    табло, и терять её из-за пустой строки нельзя: разность времён даёт то же самое.
    """
    queued = fact.get('queued_at')
    if fact.get('wait_seconds') is None and queued is not None:
        end = fact.get('answered_at') or fact.get('lost_at')
        if end is not None:
            fact['wait_seconds'] = _seconds(int((end - queued).total_seconds()), MAX_WAIT_SECONDS)
    fact.pop('lost_at', None)


def wire(fact):
    """Факт → поля касания, как они едут порталу и ложатся в `cdr_touches`."""
    return {
        'queued_at': _stamp(fact.get('queued_at')),
        'answered_at': _stamp(fact.get('answered_at')),
        'wait_seconds': fact.get('wait_seconds'),
        'talk_measured_seconds': fact.get('talk_seconds'),
        'hangup_side': fact.get('hangup_side') or '',
    }


_MERGE_FIELDS = ('queue', 'queued_at', 'answered_at', 'wait_seconds', 'talk_seconds',
                 'hangup_side', 'agent')


def merge(base, fresh):
    """Накопленные факты + факты нового окна: пустое поле заполняется, заполненное живёт.

    Живой хвост спрашивает журнал только за последние два часа, а касания пересобирает за
    весь день. Без накопления утренний звонок на следующем же цикле «терял» вход в очередь,
    менял отпечаток и уезжал на портал пустым — то есть правка стирала бы сама себя.
    Заполненное не перетирается: у одного звонка вход и ответ случаются по разу, а
    урезанное окно видит только хвост событий."""
    out = {callid: dict(fact) for callid, fact in (base or {}).items()}
    for callid, fact in (fresh or {}).items():
        current = out.get(callid)
        if current is None:
            out[callid] = dict(fact)
            continue
        for field in _MERGE_FIELDS:
            if not current.get(field) and fact.get(field):
                current[field] = fact[field]
    return out


def attach(touches, facts, incoming_types=INCOMING_TYPES):
    """Дописать касаниям точные поля очереди. Возвращает новые словари.

    Сопоставление — по `linkedid` = `callid`. Правятся только входящие: у исходящего
    очереди нет, а совпадение callid у него означало бы чужой звонок.

    `answered_at` ставится только тому, кто разговаривал: у брошенного в очереди ответа
    не было, и момент ответа у него обязан остаться пустым — на нём держится и склейка
    «принят / потерян», и SL.
    """
    if not facts:
        return list(touches or [])
    out = []
    for touch in touches or []:
        fact = facts.get(str(touch.get('linkedid') or ''))
        if not fact or touch.get('call_type') not in incoming_types:
            out.append(touch)
            continue
        enriched = dict(touch)
        values = wire(fact)
        if values['queued_at']:
            enriched['queued_at'] = values['queued_at']
        if values['answered_at'] and int(touch.get('talk_seconds') or 0) > 0:
            enriched['answered_at'] = values['answered_at']
            if values['talk_measured_seconds'] is not None:
                enriched['talk_measured_seconds'] = values['talk_measured_seconds']
        if values['wait_seconds'] is not None:
            enriched['wait_seconds'] = values['wait_seconds']
        if values['hangup_side']:
            enriched['hangup_side'] = values['hangup_side']
        out.append(enriched)
    return out
