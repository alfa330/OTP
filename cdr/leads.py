# -*- coding: utf-8 -*-
"""Сделки рядом со звонками: какой звонок к какой сделке относится и что из этого
следует. Чистая логика — ни базы, ни сети.

Раздел «Касания» показывает звонки отдела продаж. Но вопрос руководителя звучит
не «сколько было звонков», а «позвонили ли по этой заявке, с какой попытки
дозвонились и кто». Для этого сделку (заявку водителя из amoCRM, лид «Потока»,
водителя «Платного найма») надо положить рядом с её звонками. С июня 2026 такие
файлы собирались вручную (outputs/kasaniya_leads_*/_build_touches.py), и все
правила ниже оттуда — каждое проверено на живых данных и каждое стоило
отдельного разбора.

Как звонок привязывается к сделке
---------------------------------
По номеру телефона и по времени. Телефон — единственный ключ: сделка и CDR
ничего больше общего не знают. Время нужно, потому что один и тот же водитель
оставляет заявки не раз: у 20% сделок «Основы» телефон повторяется. Окно сделки
начинается за GRACE до её создания и кончается там, где начинается окно
СЛЕДУЮЩЕЙ сделки с тем же номером. Окна стыкуются встык и не перекрываются,
поэтому один звонок никогда не посчитан дважды.

Почему допуск GRACE нужен ВСЕМ сделкам, а не только типу «звонки»
------------------------------------------------------------------
Карточка в CRM часто появляется уже ПОСЛЕ начала звонка: человек звонит,
оператор берёт трубку, сделку заводят через несколько секунд (медиана
опережения 10 с). Окно «строго от момента создания» такие звонки выбрасывало.
Замерено 15.09.2026 на потоке «Основы»: 2 486 → 2 903 касания за три дня,
доля сделок со звонком 67,6% → 79,0%; из добавившихся две трети — входящие, и
большинство закончились разговором. Раньше допуск давали только лидам типа
«звонки» — и у сделок с формы первое касание терялось.

Допуск при этом не может отобрать у ПРЕДЫДУЩЕЙ сделки того же номера её
собственный звонок: если две сделки созданы с разницей меньше допуска, окно
второй начинается не раньше момента создания первой. Звонок, который создал
первую карточку (начался за секунды до неё), остаётся у первой; звонок за
секунды до второй карточки — у второй.

Режим «все звонки по номеру»
----------------------------
Когда файл собирают по одному сотруднику или по узкому списку сделок с
уникальными телефонами, окна по дате только теряют звонки (12 из 383 у одного
оператора за август). Для сделок с уникальным номером окно можно отключить:
один звонок физически не может относиться к двум сделкам. У сделок с дублем
номера окна остаются — иначе звонок попал бы в обе.
"""

from collections import Counter, OrderedDict
from datetime import datetime, timedelta

from .directory import names_match

# Допуск: звонок мог начаться раньше, чем сделку завели в CRM.
GRACE = timedelta(minutes=2)

# Столько касаний раскладывается по колонкам листа «Лиды»; остальные идут текстом
# в последнюю колонку. Двенадцать закрывает 99,9% сделок широких выгрузок.
MAX_BLOCKS = 12

# Когда блоков не больше стольких, в каждый влезает полный адрес записи — его
# можно и кликнуть, и скопировать. В широких файлах 12 таких колонок раздули бы
# лист, поэтому там ссылка остаётся короткой кнопкой.
FULL_URL_BLOCKS = 6

# Ключ источника → как он называется человеку и что здесь строка.
SOURCES = OrderedDict((
    ('amo', {
        'label': 'Основа', 'direction': 'op_osnova', 'subject': 'сделка',
        'subjects': 'сделок', 'system': 'amoCRM, воронка «Отдел продаж»',
        'moment': 'создание сделки',
    }),
    ('crm_paid_hire', {
        'label': 'Платный найм', 'direction': 'op_yandex_reg', 'subject': 'водитель',
        'subjects': 'водителей', 'system': 'СРМ yataxi, лиды платного найма',
        'moment': 'регистрация водителя',
    }),
    ('crm_stream', {
        'label': 'Поток', 'direction': 'op_potok', 'subject': 'лид',
        'subjects': 'лидов', 'system': 'СРМ yataxi, базы «Отток» и «Фокус»',
        'moment': 'взятие лида в работу (или загрузка базы)',
    }),
))

TYPE_OUT = 'Исходящий'
TYPE_IN = 'Входящий'
RESULT_TALK = 'Разговор'


def source_info(source):
    info = SOURCES.get(source)
    if not info:
        raise ValueError('Неизвестный источник сделок: %r' % (source,))
    return info


def lead_moment(lead):
    """Момент, от которого считается окно сделки.

    У «Потока» это взятие лида в работу, а если его не брали — загрузка базы:
    так считает сутки сам источник. У остальных — создание/регистрация.
    """
    return lead.get('taken_at') or lead.get('created_at')


def lead_phones(lead):
    """Все телефоны сделки списком десятизначных строк."""
    raw = lead.get('phones') or ''
    if isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = str(raw).split(',')
    out = []
    for value in values:
        digits = ''.join(ch for ch in str(value or '') if ch.isdigit())[-10:]
        if len(digits) == 10 and digits not in out:
            out.append(digits)
    if not out:
        digits = ''.join(ch for ch in str(lead.get('phone') or '') if ch.isdigit())[-10:]
        if len(digits) == 10:
            out.append(digits)
    return out


def _dt(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:19].replace('T', ' '))
    except ValueError:
        return None


def assign_touches(leads, touches, *, window_to, grace=GRACE, no_window=False):
    """Раздаёт звонки по сделкам. Возвращает {ключ сделки: [касания по времени]}.

    leads — словари с ключами `key`, `phones` (список), `moment` (datetime).
    touches — словари касаний с `phone` (10 цифр) и `started_at` (datetime).
    window_to — правая граница окна последней сделки каждого номера (включительно).

    Попутно каждой сделке проставляются `window_since`, `window_until`, `dup_n`
    (сколько сделок делят её номер) и `dup_i` (какая она по счёту).
    """
    by_phone = {}
    for index, lead in enumerate(leads):
        lead['moment'] = _dt(lead.get('moment'))
        lead['window_since'] = None
        lead['window_until'] = None
        lead['dup_n'], lead['dup_i'] = 1, 1
        for phone in lead.get('phones') or ():
            by_phone.setdefault(phone, []).append(index)

    end = _dt(window_to)
    if end is not None and end.time() == datetime.min.time():
        end = end + timedelta(days=1)          # «по 14.09» значит до конца 14.09

    # Окна считаются на КАЖДЫЙ номер отдельно: сделка с двумя телефонами живёт в
    # двух цепочках, и её звонки собираются с обоих.
    windows = {}    # (phone, lead index) -> (since, until)
    for phone, indexes in by_phone.items():
        chain = sorted(indexes, key=lambda i: leads[i]['moment'] or datetime.min)
        if no_window and len(chain) == 1:
            windows[(phone, chain[0])] = (datetime.min, datetime.max)
            continue
        since = []
        for position, index in enumerate(chain):
            moment = leads[index]['moment'] or datetime.min
            start = moment - grace
            if position:
                previous = leads[chain[position - 1]]['moment']
                if previous and start < previous:
                    start = previous
            since.append(start)
        order = sorted(range(len(chain)), key=lambda p: since[p])
        for rank, position in enumerate(order):
            index = chain[position]
            until = since[order[rank + 1]] if rank + 1 < len(order) else (end or datetime.max)
            windows[(phone, index)] = (since[position], until)
            lead = leads[index]
            lead['dup_n'] = max(lead['dup_n'], len(chain))
            lead['dup_i'] = rank + 1 if len(chain) > 1 else lead['dup_i']
            if lead['window_since'] is None or since[position] < lead['window_since']:
                lead['window_since'] = since[position]
            if lead['window_until'] is None or until > lead['window_until']:
                lead['window_until'] = until

    assigned = {lead['key']: [] for lead in leads}
    for touch in touches:
        phone = touch.get('phone')
        started = _dt(touch.get('started_at'))
        if not phone or started is None or phone not in by_phone:
            continue
        for index in by_phone[phone]:
            since, until = windows.get((phone, index), (None, None))
            if since is not None and since <= started < until:
                assigned[leads[index]['key']].append(touch)
                break
    for items in assigned.values():
        items.sort(key=lambda t: (_dt(t.get('started_at')) or datetime.min, t.get('phone') or ''))
    return assigned


def responsible_called(owner_exts, owner_name, touches):
    """«да» / «нет» / «» — звонил ли закреплённый за сделкой сотрудник.

    Сначала по внутреннему номеру ответственного (надёжно), потом по совпадению
    ФИО со справочником (когда номер у человека в карточке не заполнен). Пусто,
    если ответственного нет или звонков не было: «нет» про пустоту было бы
    неправдой.
    """
    if not touches or not (owner_exts or owner_name):
        return ''
    exts = {str(e) for e in (owner_exts or ()) if e}
    for touch in touches:
        if exts and str(touch.get('ext') or '') in exts:
            return 'да'
        if owner_name and touch.get('operator') and names_match(owner_name, touch['operator']):
            return 'да'
    return 'нет'


def own_touches(owner_exts, owner_name, touches):
    """Только звонки самого ответственного — режим «файл по одному сотруднику»."""
    exts = {str(e) for e in (owner_exts or ()) if e}
    out = []
    for touch in touches:
        if exts and str(touch.get('ext') or '') in exts:
            out.append(touch)
        elif owner_name and touch.get('operator') and names_match(owner_name, touch['operator']):
            out.append(touch)
    return out


def aggregate(lead, touches):
    """Сводка одной сделки по её касаниям — то, что уходит в колонки «СВОДКА»."""
    moment = _dt(lead.get('moment'))
    outs = [t for t in touches if t.get('call_type') == TYPE_OUT]
    ins = [t for t in touches if str(t.get('call_type') or '').startswith(TYPE_IN)]
    talks = [t for t in touches if int(t.get('talk_seconds') or 0) > 0]
    first_out = _dt(outs[0]['started_at']) if outs else None
    reaction = None
    if first_out is not None and moment is not None:
        reaction = round((first_out - moment).total_seconds() / 60.0, 1)
    before = sum(1 for t in touches if moment and _dt(t['started_at']) and _dt(t['started_at']) < moment)
    who = list(OrderedDict.fromkeys(t.get('operator') for t in touches if t.get('operator')))
    return {
        'touches': len(touches),
        'outgoing': len(outs),
        'incoming': len(ins),
        'talks': len(talks),
        'talk_seconds': sum(int(t.get('talk_seconds') or 0) for t in touches),
        'first_at': touches[0]['started_at'] if touches else None,
        'first_out_at': outs[0]['started_at'] if outs else None,
        'reaction_min': reaction,
        'last_at': touches[-1]['started_at'] if touches else None,
        'operators': who,
        'before_lead': before,
        'with_recording': sum(1 for t in touches if t.get('recording_url')),
    }


def blocks_for(max_touches):
    """Сколько блоков «Касание N» рисовать и писать ли в них полный адрес записи."""
    blocks = max(1, min(MAX_BLOCKS, int(max_touches or 0)))
    return blocks, blocks <= FULL_URL_BLOCKS


def summarize(rows):
    """Итоги по набору сделок. rows — словари с `agg` (из aggregate), `lead_type`,
    `stage`, `park`, `owner_called`, `touches`."""
    total = len(rows)
    with_touches = [r for r in rows if r['agg']['touches']]
    all_touches = [t for r in rows for t in r['touches']]
    reactions = sorted(r['agg']['reaction_min'] for r in rows
                       if r['agg']['reaction_min'] is not None)
    by_type = Counter(r.get('lead_type') or '—' for r in rows)
    by_type_touched = Counter(r.get('lead_type') or '—' for r in with_touches)
    by_stage = Counter(r.get('stage') or '—' for r in rows)
    by_stage_touched = Counter(r.get('stage') or '—' for r in with_touches)
    by_park = Counter(r.get('park') or '—' for r in rows)
    by_park_touched = Counter(r.get('park') or '—' for r in with_touches)
    by_operator = Counter(t.get('operator') or '—' for t in all_touches)
    distribution = Counter(r['agg']['touches'] for r in rows)
    called = Counter(r.get('owner_called') or 'пусто' for r in rows)
    return {
        'leads': total,
        'with_touches': len(with_touches),
        'without_touches': total - len(with_touches),
        'touches': len(all_touches),
        'talks': sum(1 for t in all_touches if int(t.get('talk_seconds') or 0) > 0),
        'talk_seconds': sum(int(t.get('talk_seconds') or 0) for t in all_touches),
        'outgoing': sum(1 for t in all_touches if t.get('call_type') == TYPE_OUT),
        'incoming': sum(1 for t in all_touches if t.get('call_type') == TYPE_IN),
        'incoming_missed': sum(1 for t in all_touches
                               if str(t.get('call_type') or '').startswith(TYPE_IN)
                               and t.get('call_type') != TYPE_IN),
        'with_recording': sum(1 for t in all_touches if t.get('recording_url')),
        'before_lead': sum(r['agg']['before_lead'] for r in rows),
        'median_reaction_min': reactions[len(reactions) // 2] if reactions else None,
        'owner_called': {'yes': called.get('да', 0), 'no': called.get('нет', 0),
                         'blank': called.get('пусто', 0)},
        'by_lead_type': [[k, v, by_type_touched.get(k, 0)] for k, v in by_type.most_common()],
        'by_stage': [[k, v, by_stage_touched.get(k, 0)] for k, v in by_stage.most_common(40)],
        'by_park': [[k, v, by_park_touched.get(k, 0)] for k, v in by_park.most_common(40)],
        'by_operator': by_operator.most_common(),
        'distribution': sorted(distribution.items()),
        'max_touches': max(distribution) if distribution else 0,
    }
