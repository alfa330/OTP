# -*- coding: utf-8 -*-
"""Кто положил трубку на исходящем — по журналу событий каналов станции
(`asteriskcdrdb.cel`). Разбор общий для моста и портала, поэтому живёт в `cdr/`.

Зачем
-----
Сторону отбоя входящего называет журнал очередей: COMPLETECALLER или COMPLETEAGENT
(cdr/queue_facts.py). Исходящий в очередь не входит, и до 02.10.2026 сторона у него была
пустой всегда: в журнале оценок «Не определено», модель оценки о ней не знала.

CDR стороны отбоя не несёт. Её несёт CEL: у события HANGUP каждого канала звонка в `extra`
лежит `{"hangupcause":16,"hangupsource":"PJSIP/6687-0006fb1b","dialstatus":"ANSWER"}`, где
hangupsource — канал, с которого пришёл отбой.

Правило
-------
Хвост звонка — события HANGUP не раньше секунды до последнего. В нём по порядку записи
первое событие, чей hangupsource — сторона разговора, и решает:

    PJSIP/6687-0006fb1b               внутренний номер — положил оператор
    PJSIP/+77475777778-0006fb29       транк — положил клиент
    Local/…, dialplan/builtin, пусто  кухня станции (очередь, дозвонщик) — мимо

Почему хвост, а не первое событие звонка: у автообзвона и у входящего через очередь первыми
идут отбои попыток дозвониться до других операторов (`PJSIP/6417-…` с причиной 17
«занято»), к концу разговора они отношения не имеют. Почему первое в хвосте, а не последнее
«положил сам»: транк, который станция кладёт следом за оператором, бывает, пишет источником
себя (29.09.2026, причина 31) — правило «последний сам» назвало бы клиента.

Решаем только по звонку, события которого записаны целиком. Последним станция пишет
LINKEDID_END — когда исчез последний канал звонка. Без него хвоста может ещё не быть, и
отбой попытки очереди выдал бы себя за конец разговора.

Переведённый звонок (BLINDTRANSFER, ATTENDEDTRANSFER) стороны не получает. Каналы того,
кому перевели, живут под тем же linkedid, и конец звонка — уже его разговор, а касание и
запись — того, кто переводил: «положил клиент» про его запись было бы неправдой. Так же
журнал очередей молчит о переведённом входящем — COMPLETE* у него нет. За 29.09–01.10
переводов среди исходящих разговоров не было ни одного, среди входящих — два.

Сверка (02.10.2026, сутки 29.09–01.10)
--------------------------------------
Входящие с разговором — тем же правилом против журнала очередей: 784 из 784 совпали
(оператор 303, клиент 481). Исходящих с разговором 1 731 (41 из них — автообзвон):
LINKEDID_END есть у всех, сторона определилась у всех.
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta

from cdr import touches as touches_mod

HANGUP_CLIENT = 'client'
HANGUP_OPERATOR = 'operator'

# Перевод звонка другому сотруднику: после него сторону отбоя не ставим (см. шапку).
_TRANSFER_EVENTS = ('BLINDTRANSFER', 'ATTENDEDTRANSFER')

# События, ради которых мост ходит в CEL. Список — часть договора с запросом: он же стоит в
# `IN (...)` у моста, чтобы станция не отдавала лишнего (каналы, мосты, приложения).
WANTED_EVENTS = ('HANGUP', 'LINKEDID_END') + _TRANSFER_EVENTS

# Хвост звонка — столько секунд до последнего отбоя. Каналы станция кладёт друг за другом
# за миллисекунды, но время в CEL — с точностью до секунды, и отбой может перейти её границу.
TAIL_SECONDS = 1

# Телефон сотрудника: PJSIP/6687-0006fb1b, PJSIP/931-0006ee69. Транк узнаётся правилом
# склейки (`touches.TRUNK_CHANNEL_RE`) — под него подходит и внутренний номер, поэтому
# сначала проверяется этот.
_EXT_CHANNEL_RE = re.compile(r'^(?:PJSIP|SIP)/\d{3,4}-[0-9a-f]+$')


def party(channel):
    """Чья сторона канал: оператор (телефон сотрудника), клиент (транк) или '' — служебный."""
    text = str(channel or '').strip()
    if _EXT_CHANNEL_RE.match(text):
        return HANGUP_OPERATOR
    if touches_mod.TRUNK_CHANNEL_RE.match(text):
        return HANGUP_CLIENT
    return ''


def _source(extra):
    """hangupsource из поля `extra` события; станция кладёт туда JSON строкой."""
    data = extra
    if not isinstance(data, dict):
        try:
            data = json.loads(str(extra or '') or '{}')
        except ValueError:
            return ''
    return str(data.get('hangupsource') or '').strip() if isinstance(data, dict) else ''


def _moment(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.strptime(str(value or '').strip()[:19].replace('T', ' '),
                                 '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def _order(value, index):
    """Порядок записи события станцией — его id; без id — порядок во входном списке."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return index


def _kind(event):
    return str((event or {}).get('eventtype') or '').strip().upper()


def _finished(events):
    return any(_kind(event) == 'LINKEDID_END' for event in events or ())


def side(events):
    """События CEL одного звонка → 'client' | 'operator' | ''.

    Событие — словарь колонок `cel`: {id, eventtype, eventtime, extra}. Пусто — звонок ещё
    не записан целиком (нет LINKEDID_END), его переводили или отбой пришёл не со стороны
    разговора."""
    if not _finished(events) or any(_kind(event) in _TRANSFER_EVENTS for event in events):
        return ''
    hangups = []
    for index, event in enumerate(events or ()):
        if _kind(event) != 'HANGUP':
            continue
        moment = _moment(event.get('eventtime'))
        if moment is not None:
            hangups.append((_order(event.get('id'), index), moment, _source(event.get('extra'))))
    if not hangups:
        return ''
    edge = max(moment for _order_, moment, _source_ in hangups) - timedelta(seconds=TAIL_SECONDS)
    for _order_, moment, source in sorted(hangups, key=lambda item: item[0]):
        found = party(source) if moment >= edge else ''
        if found:
            return found
    return ''


def build_sides(rows):
    """Строки CEL (WANTED_EVENTS, с linkedid) → {linkedid: сторона}.

    В ответе только звонки, записанные целиком; сторона '' — звонок переводили или отбой
    пришёл не со стороны разговора. Звонка без LINKEDID_END в ответе нет вовсе: «станция ещё
    пишет» и «стороны нет» — разные ответы, и о первом стоит спросить снова."""
    by_call = defaultdict(list)
    for row in rows or ():
        linkedid = str((row or {}).get('linkedid') or '').strip()
        if linkedid:
            by_call[linkedid].append(row)
    return {linkedid: side(events) for linkedid, events in by_call.items() if _finished(events)}


def wanted(touch):
    """Нужна ли касанию сторона отсюда: исходящий с разговором, у которого её ещё нет.

    Входящему её называет журнал очередей. У непринятого и у недозвона разговора не было —
    класть трубку посреди него было некому."""
    return (touch.get('call_type') == touches_mod.TYPE_OUT
            and int(touch.get('talk_seconds') or 0) > 0
            and not touch.get('hangup_side'))


def wanted_linkedids(touches):
    """linkedid касаний, которым нужна сторона отбоя, — ровно то, о чём спросить CEL."""
    return sorted({str(touch.get('linkedid') or '') for touch in touches or ()
                   if wanted(touch)} - {''})


def attach(touches, sides):
    """Дописать исходящим с разговором, кто положил трубку. Возвращает новый список;
    касание, которому сторона не нужна или не известна, остаётся как было."""
    sides = sides or {}
    out = []
    for touch in touches or ():
        found = sides.get(str(touch.get('linkedid') or '')) if wanted(touch) else ''
        out.append(dict(touch, hangup_side=found) if found else touch)
    return out
