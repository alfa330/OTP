# -*- coding: utf-8 -*-
"""Отчёт за день (задача HR-3): воронка дня по каналам, итоги по рекрутерам, эффективность и «Другая работа».

Постановка HR-3: «к концу рабочего дня вся информация уже отражена в таблице, и мы можем просто сделать скриншот
готового отчёта и отправить его руководству». Ручной ввод цифр в таблицу владелец исключил: цифры считаются из того,
что рекрутер сделал в разделе. Разрез — его же (23.11.2026): «сколько откликов в хх отработали, сколько таргета, сколько
внесено вручную контактов и отработано (это мы понимаем что либо каспи либо олх либо сами нам позвонили кандидаты),
далее по ним статусы: сколько приглашены на собеседование, сколько на обучение, сколько отказ (наш отказ и нам
отказали), сколько недозвонов, сколько в работе», и «отчёт по эффективности: сколько звонков в день совершено, ср время
диалога». Поле «Другая работа» — текст рекрутера за день о работе не по кандидатам (решение владельца 23.11.2026).

Как считается (день — по времени Алматы, как всё время раздела):

* «Поступило» — новые карточки дня по времени поступления в общий пул; «Вручную» — внесённые рекрутером руками (в
  отчёте одного рекрутера — им самим). Повторный отклик уже открытой карточки — не новая карточка.
* «Отработано» — карточки, с которыми рекрутер в этот день работал: взял в работу, звонил, писал в WhatsApp или оставил
  комментарий, пока кандидат был «В работе» (или в «Не отвечает звонок/чат»), пригласил или оформил отказ. Напоминание
  уже приглашённому — не обработка отклика и в счёт не идёт.
* Строки «из них» — где отработанный кандидат оказался к концу дня, каждый ровно в одной строке, вместе они дают
  «Отработано». Недозвон — последний звонок без ответа, и кандидат после него не писал в WhatsApp («Не отвечает
  звонок/чат» — тоже сюда); в работе — остальные «В работе». Кого руководитель в тот же день вернул в пул, в отчёте нет.
* Конец дня восстанавливается по истории этапов, звонков и переписки: отчёт за прошлый день не меняется от того, что с
  кандидатом было потом (кроме исхода звонка, дописанного в карточке задним числом).
* «Провели собеседование» — собеседование было назначено на этот день, и кандидат пришёл: отмечен «Пришел» или после
  собеседования приглашён на обучение (этот переход без отметки «Пришел» не делается).
* Эффективность — исходящие звонки дня: из телефонии (линия рекрутера в iCore Phone) и отметки звонка с исходом в
  карточке; разговор — ответ и хотя бы секунда; среднее и сумма — по разговорам из телефонии (у отметки длительности
  нет). WhatsApp — скольким кандидатам рекрутер написал в этот день.

Канал — разрез владельца: HeadHunter и «Таргет» — по источнику карточки; «Вручную» — внесённые рекрутером руками с любым
другим источником (Kaspi, OLX, «Вручную», «Приведи друга»); WhatsApp — написали на номер найма сами, не по рекламе; базы
контактов и прочие площадки — своими колонками, только в те дни, когда они есть.
"""

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, time, timedelta
from io import BytesIO

from . import queries, rules

CH_HH, CH_TARGET, CH_WHATSAPP, CH_MANUAL, CH_BASE, CH_OTHER = 'hh', 'target', 'whatsapp', 'manual', 'base', 'other'
CHANNELS = (CH_HH, CH_TARGET, CH_WHATSAPP, CH_MANUAL, CH_BASE, CH_OTHER)
# Три канала владельца и WhatsApp (на проде — каждый день) видны всегда, чтобы таблица дня не меняла вид; базы контактов
# и прочие площадки — только когда в дне что-то есть.
ALWAYS_SHOWN = (CH_HH, CH_TARGET, CH_WHATSAPP, CH_MANUAL)
CHANNEL_LABELS = {CH_HH: 'HeadHunter', CH_TARGET: 'Таргет', CH_WHATSAPP: 'WhatsApp', CH_MANUAL: 'Вручную',
                  CH_BASE: 'Базы контактов', CH_OTHER: 'Другие'}
# Подписи каналов-источников берутся из справочника (руководитель может переименовать источник), цвет — тоже.
CHANNEL_SOURCE = {CH_HH: rules.SOURCE_HH, CH_TARGET: rules.SOURCE_TARGET, CH_WHATSAPP: 'whatsapp'}
CHANNEL_HINTS = {
    CH_MANUAL: 'Внесены рекрутером руками: Kaspi, OLX, кандидат сам позвонил',
    CH_WHATSAPP: 'Написали на номер WhatsApp найма сами, не по рекламе',
}
ALL = 'all'

B_OFFICE, B_TRAINING, B_REFUSED_OURS, B_REFUSED_CANDIDATE, B_NO_ANSWER, B_IN_WORK = (
    'office', 'training', 'refused_ours', 'refused_candidate', 'no_answer', 'in_work')
BUCKETS = (B_OFFICE, B_TRAINING, B_REFUSED_OURS, B_REFUSED_CANDIDATE, B_NO_ANSWER, B_IN_WORK)
# Всё, что дальше приглашения на обучение, — тоже «на обучение»: за день кандидат туда почти не доходит, а строк не
# прибавится. Отсев (только с обучения или линии) — туда же: он был приглашён.
TRAINING_STAGES = (rules.INVITED_TRAINING, rules.ON_TRAINING, rules.ON_LINE, rules.HIRED, rules.HIRED_10, rules.DROPPED)
# Работа с откликом идёт, пока кандидат «В работе» или молчит в «Не отвечает звонок/чат»; переходы, которыми она кончается.
PROCESSING_STAGES = (rules.IN_WORK, rules.NO_RESPONSE)
OUTCOME_STAGES = (rules.INVITED_OFFICE, rules.INVITED_TRAINING, rules.REFUSED)
WORK_TOUCHES = (rules.TOUCH_CALL, rules.TOUCH_WHATSAPP, rules.TOUCH_COMMENT)

ROWS = (
    ('arrived', 'Поступило', False,
     'Новые отклики и контакты дня в общем пуле. «Вручную» — внесённые рекрутером руками; в отчёте рекрутера — им самим'),
    ('done', 'Отработано', False,
     'Кандидаты, с которыми рекрутер работал в этот день: взял в работу, звонил, писал в WhatsApp, оставил комментарий, '
     'пригласил или оформил отказ. Строки ниже — где они оказались к концу дня, вместе они дают «Отработано»'),
    (B_OFFICE, 'Приглашены на собеседование', True, None),
    (B_TRAINING, 'Приглашены на обучение', True, None),
    (B_REFUSED_OURS, 'Отказ — наш', True, None),
    (B_REFUSED_CANDIDATE, 'Отказ — кандидата', True, None),
    (B_NO_ANSWER, 'Недозвон', True,
     'Последний звонок — без ответа, и кандидат после него не написал в WhatsApp. Сюда же — «Не отвечает звонок/чат»'),
    (B_IN_WORK, 'В работе', True,
     'На связи, решения пока нет: кандидат думает, договорились созвониться, ждём ответа в WhatsApp. «В работе» карточка '
     'становится, когда рекрутер берёт отклик из пула («Взять в работу») или отвечает кандидату с телефона найма, и когда '
     'молчавший кандидат снова выходит на связь'),
    ('interviews', 'Провели собеседование', False,
     'Собеседование было назначено на этот день, и кандидат пришёл: отмечен «Пришел» или после собеседования '
     'приглашён на обучение'),
)
ROW_KEYS = tuple(key for key, _label, _part, _hint in ROWS)
ROW_LABELS = {key: label for key, label, _part, _hint in ROWS}

# Таблицы по рекрутерам: итоги (те же строки «из них») и эффективность. Подписи — одни на экран и Excel.
RESULT_COLUMNS = (
    ('done', 'Отработано', None), (B_OFFICE, 'Собеседование', 'Приглашены на собеседование'),
    (B_TRAINING, 'Обучение', 'Приглашены на обучение'), (B_REFUSED_OURS, 'Отказ наш', None),
    (B_REFUSED_CANDIDATE, 'Отказ кандидата', None), (B_NO_ANSWER, 'Недозвон', None), (B_IN_WORK, 'В работе', None),
)
EFFICIENCY_COLUMNS = (
    ('calls', 'Звонков', 'Исходящие звонки кандидатам: с линии в iCore Phone и отмеченные в карточке вручную'),
    ('talked', 'Дозвонились', 'Разговор состоялся'),
    ('avg_talk_sec', 'Ср. разговор', 'Средняя длительность разговора — по звонкам из телефонии'),
    ('talk_sec', 'В разговорах', 'Сколько всего длились разговоры за день'),
    ('whatsapp', 'WhatsApp', 'Скольким кандидатам рекрутер написал в WhatsApp в этот день'),
)

NOTE_MAX = 2000
WEEKDAYS = ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье')
MONTHS_GEN = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября',
              'декабря')


# ─── Чистые правила ───────────────────────────────────────────────────────────

def channel_of(source_code, created_by=None, batch_id=None):
    """Канал карточки для отчёта. Источник hh и «Таргет» — канал сам по себе (и у внесённых руками: до моста hh отклики
    hh рекрутеры заводили руками); загруженная база — по партии; остальное внесённое человеком — «Вручную»."""
    if source_code == rules.SOURCE_HH:
        return CH_HH
    if source_code == rules.SOURCE_TARGET:
        return CH_TARGET
    if batch_id:
        return CH_BASE
    if created_by:
        return CH_MANUAL
    if source_code == CHANNEL_SOURCE[CH_WHATSAPP]:
        return CH_WHATSAPP
    return CH_OTHER


def end_of_day_bucket(stage, substage=None, last_call_result=None, answered_after_call=False):
    """Строка «из них» для отработанного кандидата по его состоянию на конец дня; None — его в отчёте нет (вернулся
    в пул). last_call_result — исход последнего звонка нынешнего круга «В работе» (None — звонков не было),
    answered_after_call — кандидат написал в WhatsApp после этого звонка."""
    if stage == rules.REFUSED:
        return B_REFUSED_CANDIDATE if substage == 'candidate' else B_REFUSED_OURS
    if stage in TRAINING_STAGES:
        return B_TRAINING
    if stage == rules.INVITED_OFFICE:
        return B_OFFICE
    if stage == rules.NO_RESPONSE:
        return B_NO_ANSWER
    if stage == rules.IN_WORK:
        if last_call_result == 'no_answer' and not answered_after_call:
            return B_NO_ANSWER
        return B_IN_WORK
    return None


def stage_at(history, moment):
    """Этап карточки в момент moment по её истории [(entered_at, stage, substage), …] в порядке времени; None — раньше
    первой записи."""
    index = bisect_right([entry[0] for entry in history], moment)
    return history[index - 1] if index else None


def parse_day(value, today):
    """«2026-11-23» → date; пусто или мусор — сегодня; будущее — сегодня (отчёта за завтра нет)."""
    try:
        day = datetime.strptime(str(value or '').strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        return today
    return min(day, today)


def day_title(day):
    """«23 ноября 2026, понедельник»."""
    return '%d %s %d, %s' % (day.day, MONTHS_GEN[day.month - 1], day.year, WEEKDAYS[day.weekday()])


def talk_clock(seconds):
    """«1:29» — длительность разговора; None — разговоров не было."""
    if seconds is None:
        return None
    total = max(0, int(round(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return '%d:%02d:%02d' % (hours, minutes, secs) if hours else '%d:%02d' % (minutes, secs)


# ─── Выборки ──────────────────────────────────────────────────────────────────
# Каждая — одним запросом по индексу времени события; история этапов и звонков — разом по карточкам дня. Этап на
# момент касания считается в Python по этой истории, а не подзапросом на каждое касание (оценка коррелированного
# подзапроса на строку поднимала план за порог JIT — так было в пуле «Удаленного КЦ» 18.11.2026).

def _rows(cursor):
    return cursor.fetchall()


def _takes(cursor, start, end, recruiter_id):
    cursor.execute("""
        SELECT a.card_id, a.recruiter_id FROM hiring_assignments a
         WHERE a.reason = 'take' AND a.recruiter_id IS NOT NULL AND a.assigned_at >= %s AND a.assigned_at < %s
           AND (%s::int IS NULL OR a.recruiter_id = %s)
    """, (start, end, recruiter_id, recruiter_id))
    return [(int(card_id), int(user_id)) for card_id, user_id in _rows(cursor)]


def _touches(cursor, start, end, recruiter_id):
    cursor.execute("""
        SELECT t.card_id, t.kind, t.result, t.author_id, t.at, t.call_id FROM hiring_touches t
         WHERE t.kind IN ('call', 'whatsapp', 'comment') AND t.author_id IS NOT NULL AND t.at >= %s AND t.at < %s
           AND (%s::int IS NULL OR t.author_id = %s)
    """, (start, end, recruiter_id, recruiter_id))
    return [{'card_id': int(card_id), 'kind': kind, 'result': result, 'author_id': int(author_id), 'at': at,
             'call_id': call_id} for card_id, kind, result, author_id, at, call_id in _rows(cursor)]


def _moves(cursor, start, end, recruiter_id):
    cursor.execute("""
        SELECT st.card_id, st.by_user_id FROM hiring_card_stages st
         WHERE st.stage = ANY(%s) AND st.by_user_id IS NOT NULL AND st.entered_at >= %s AND st.entered_at < %s
           AND (%s::int IS NULL OR st.by_user_id = %s)
    """, (list(OUTCOME_STAGES), start, end, recruiter_id, recruiter_id))
    return [(int(card_id), int(user_id)) for card_id, user_id in _rows(cursor)]


def _histories(cursor, card_ids, end):
    histories = defaultdict(list)
    if not card_ids:
        return histories
    cursor.execute("""
        SELECT card_id, entered_at, stage, substage FROM hiring_card_stages
         WHERE card_id = ANY(%s) AND entered_at < %s ORDER BY card_id, entered_at, id
    """, (sorted(card_ids), end))
    for card_id, entered_at, stage, substage in _rows(cursor):
        histories[int(card_id)].append((entered_at, stage, substage))
    return histories


def _last_calls(cursor, cycles, end):
    """{card_id: (исход, время)} — последний звонок с исходом в нынешнем круге «В работе» (cycles: {card_id: начало})."""
    out = {}
    if not cycles:
        return out
    cursor.execute("""
        SELECT card_id, result, at FROM hiring_touches
         WHERE card_id = ANY(%s) AND kind = 'call' AND result IS NOT NULL AND at < %s ORDER BY card_id, at, id
    """, (sorted(cycles), end))
    for card_id, result, at in _rows(cursor):
        card_id = int(card_id)
        if at >= cycles[card_id]:
            out[card_id] = (result, at)
    return out


def _last_inbound(cursor, card_ids, end):
    """{card_id: время последнего сообщения кандидата в WhatsApp найма до конца дня}."""
    if not card_ids:
        return {}
    cursor.execute("""
        SELECT ch.card_id, MAX(m.sent_at) FROM hiring_wa_messages m JOIN hiring_wa_chats ch ON ch.key = m.chat_key
         WHERE ch.card_id = ANY(%s) AND NOT m.from_me AND m.deleted_at IS NULL AND m.sent_at < %s
         GROUP BY ch.card_id
    """, (sorted(card_ids), end))
    return {int(card_id): moment for card_id, moment in _rows(cursor) if moment}


def _card_channels(cursor, card_ids):
    if not card_ids:
        return {}
    cursor.execute("""
        SELECT c.id, s.code, c.created_by, c.batch_id FROM hiring_cards c LEFT JOIN hiring_sources s ON s.id = c.source_id
         WHERE c.id = ANY(%s)
    """, (sorted(card_ids),))
    return {int(card_id): channel_of(code, created_by, batch_id) for card_id, code, created_by, batch_id in _rows(cursor)}


def _arrivals(cursor, start, end, recruiter_id):
    """Новые карточки дня: [(id, канал)]. В отчёте рекрутера «Вручную» — только внесённые им: общий пул один на всех,
    а руками каждый вносит своё."""
    cursor.execute("""
        SELECT c.id, s.code, c.created_by, c.batch_id FROM hiring_cards c LEFT JOIN hiring_sources s ON s.id = c.source_id
         WHERE c.received_at >= %s AND c.received_at < %s
    """, (start, end))
    out = []
    for card_id, code, created_by, batch_id in _rows(cursor):
        channel = channel_of(code, created_by, batch_id)
        if channel == CH_MANUAL and recruiter_id and int(created_by or 0) != int(recruiter_id):
            continue
        out.append((int(card_id), channel))
    return out


def _interviews(cursor, start, end, recruiter_id):
    """Собеседования дня, на которые кандидат пришёл: [(id, канал)]."""
    cursor.execute("""
        SELECT c.id, s.code, c.created_by, c.batch_id, c.stage, c.substage,
               EXISTS (SELECT 1 FROM hiring_card_stages st
                        WHERE st.card_id = c.id AND st.stage = 'invited_training' AND st.entered_at >= %(start)s) AS trained
          FROM hiring_cards c LEFT JOIN hiring_sources s ON s.id = c.source_id
         WHERE c.office_visit_at >= %(start)s AND c.office_visit_at < %(end)s
           AND (%(rec)s::int IS NULL OR c.recruiter_id = %(rec)s)
    """, {'start': start, 'end': end, 'rec': recruiter_id})
    out = []
    for card_id, code, created_by, batch_id, stage, substage, trained in _rows(cursor):
        if (stage == rules.INVITED_OFFICE and substage == 'came') or trained:
            out.append((int(card_id), channel_of(code, created_by, batch_id)))
    return out


def _pbx_calls(cursor, start, end, recruiter_id):
    cursor.execute("""
        SELECT hc.recruiter_id, hc.result, hc.billsec FROM hiring_calls hc
         WHERE hc.direction = 'out' AND hc.recruiter_id IS NOT NULL AND hc.started_at >= %s AND hc.started_at < %s
           AND (%s::int IS NULL OR hc.recruiter_id = %s)
    """, (start, end, recruiter_id, recruiter_id))
    return [(int(user_id), result, int(billsec or 0)) for user_id, result, billsec in _rows(cursor)]


def list_notes(cursor, day, recruiter_id=None):
    cursor.execute("""
        SELECT n.user_id, u.name, n.body, n.updated_at FROM hiring_day_notes n LEFT JOIN users u ON u.id = n.user_id
         WHERE n.day = %s AND (%s::int IS NULL OR n.user_id = %s) ORDER BY u.name, n.user_id
    """, (day, recruiter_id, recruiter_id))
    return [{'user_id': int(user_id), 'name': name or '', 'body': body, 'updated_at': updated_at}
            for user_id, name, body, updated_at in _rows(cursor)]


def save_note(cursor, user_id, day, body, now):
    """«Другая работа» за день: текст как есть (до NOTE_MAX знаков), пустой — записи нет. Возвращает запись или None."""
    text = str(body or '')[:NOTE_MAX]
    if not text.strip():
        cursor.execute("DELETE FROM hiring_day_notes WHERE user_id = %s AND day = %s", (int(user_id), day))
        return None
    cursor.execute("""
        INSERT INTO hiring_day_notes (user_id, day, body, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (user_id, day) DO UPDATE SET body = EXCLUDED.body, updated_at = EXCLUDED.updated_at
        RETURNING body, updated_at
    """, (int(user_id), day, text, now, now))
    saved_body, updated_at = cursor.fetchone()
    return {'user_id': int(user_id), 'day': day, 'body': saved_body, 'updated_at': updated_at}


# ─── Сборка ───────────────────────────────────────────────────────────────────

def _processed(takes, touches, moves, histories):
    """{(card_id, рекрутер)} — кто с какой карточкой работал. Касание засчитывается, только если в этот момент кандидат
    был «В работе» или в «Не отвечает звонок/чат»: напоминание приглашённому — не обработка отклика."""
    pairs = set(takes) | set(moves)
    for touch in touches:
        entry = stage_at(histories.get(touch['card_id'], ()), touch['at'])
        if entry and entry[1] in PROCESSING_STAGES:
            pairs.add((touch['card_id'], touch['author_id']))
    return pairs


def _buckets(cursor, card_ids, histories, end):
    """{card_id: строка «из них»} по состоянию на конец дня; вернувшихся в пул нет в словаре."""
    last = {card_id: histories[card_id][-1] for card_id in card_ids if histories.get(card_id)}
    cycles = {card_id: entry[0] for card_id, entry in last.items() if entry[1] == rules.IN_WORK}
    calls = _last_calls(cursor, cycles, end)
    silent = [card_id for card_id, (result, _at) in calls.items() if result == 'no_answer']
    inbound = _last_inbound(cursor, silent, end)
    out = {}
    for card_id, (_entered, stage, substage) in last.items():
        result, called_at = calls.get(card_id, (None, None))
        answered = bool(called_at and inbound.get(card_id) and inbound[card_id] > called_at)
        bucket = end_of_day_bucket(stage, substage, result, answered)
        if bucket:
            out[card_id] = bucket
    return out


def _efficiency(touches, pbx_calls):
    """{рекрутер: звонки, разговоры, время} + WhatsApp — скольким кандидатам писал."""
    stats = defaultdict(lambda: {'calls': 0, 'talked': 0, 'timed': 0, 'talk_sec': 0, 'whatsapp': 0})
    for user_id, result, billsec in pbx_calls:
        row = stats[user_id]
        row['calls'] += 1
        if result == 'answered' and billsec >= 1:
            row['talked'] += 1
            row['timed'] += 1
            row['talk_sec'] += billsec
    wrote = defaultdict(set)
    for touch in touches:
        if touch['kind'] == rules.TOUCH_CALL and not touch['call_id'] and touch['result'] in rules.CALL_RESULT_CODES:
            # Отметка звонка в карточке, который телефония не увидела (звонили не с линии), — с исходом, без длительности.
            row = stats[touch['author_id']]
            row['calls'] += 1
            if touch['result'] == 'dial':
                row['talked'] += 1
        elif touch['kind'] == rules.TOUCH_WHATSAPP:
            wrote[touch['author_id']].add(touch['card_id'])
    for user_id, cards in wrote.items():
        stats[user_id]['whatsapp'] = len(cards)
    return stats


def _efficiency_row(row):
    timed = int(row.get('timed') or 0)
    talk = int(row.get('talk_sec') or 0)
    return {'calls': int(row.get('calls') or 0), 'talked': int(row.get('talked') or 0),
            'talk_sec': talk if timed else None, 'avg_talk_sec': round(talk / timed) if timed else None,
            'whatsapp': int(row.get('whatsapp') or 0)}


def build(cursor, day, *, recruiter_id=None, viewer_id=None, today=None, with_ids=False):
    """Отчёт за день. recruiter_id — один рекрутер (его карточки и работа) или None — вся команда."""
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    recruiter_id = int(recruiter_id) if recruiter_id else None

    takes = _takes(cursor, start, end, recruiter_id)
    touches = _touches(cursor, start, end, recruiter_id)
    moves = _moves(cursor, start, end, recruiter_id)
    histories = _histories(cursor, {card_id for card_id, _ in takes} | {t['card_id'] for t in touches}
                           | {card_id for card_id, _ in moves}, end)
    pairs = _processed(takes, touches, moves, histories)
    worked = {card_id for card_id, _ in pairs}
    buckets = _buckets(cursor, worked, histories, end)
    arrivals = _arrivals(cursor, start, end, recruiter_id)
    interviews = _interviews(cursor, start, end, recruiter_id)
    channels = _card_channels(cursor, worked)
    for card_id, channel in arrivals + interviews:
        channels.setdefault(card_id, channel)

    ids = defaultdict(set)

    def put(row, card_id):
        ids['%s:%s' % (row, channels.get(card_id, CH_OTHER))].add(card_id)
        ids['%s:%s' % (row, ALL)].add(card_id)

    for card_id, _channel in arrivals:
        put('arrived', card_id)
    for card_id, bucket in buckets.items():
        put('done', card_id)
        put(bucket, card_id)
    for card_id, _channel in interviews:
        put('interviews', card_id)

    # По рекрутерам: та же строка «из них» у каждой его карточки (карточку двух рекрутеров за день — у обоих).
    per_user = defaultdict(set)
    for card_id, user_id in pairs:
        if card_id in buckets:
            per_user[user_id].add(card_id)
    for user_id, cards in per_user.items():
        for card_id in cards:
            ids['rec:%d:done' % user_id].add(card_id)
            ids['rec:%d:%s' % (user_id, buckets[card_id])].add(card_id)

    stats = _efficiency(touches, _pbx_calls(cursor, start, end, recruiter_id))
    notes = list_notes(cursor, day, recruiter_id)
    people = set(per_user) | {user_id for user_id, row in stats.items() if any(row.values())} \
        | {note['user_id'] for note in notes}
    if recruiter_id:
        people.add(recruiter_id)
    names = queries.user_names(cursor, people)

    shown = [code for code in CHANNELS if code in ALWAYS_SHOWN
             or any(ids.get('%s:%s' % (row, code)) for row in ROW_KEYS)]
    sources = {row['code']: row for row in queries.list_sources(cursor)}
    channel_list = []
    for code in shown:
        source = sources.get(CHANNEL_SOURCE.get(code)) or {}
        channel_list.append({'code': code, 'label': source.get('name') or CHANNEL_LABELS[code],
                             'color': source.get('color'), 'hint': CHANNEL_HINTS.get(code)})
    rows = []
    for key, label, part, hint in ROWS:
        cells = {code: len(ids.get('%s:%s' % (key, code), ())) for code in shown}
        cells[ALL] = len(ids.get('%s:%s' % (key, ALL), ()))
        rows.append({'key': key, 'label': label, 'part': part, 'hint': hint, 'cells': cells})

    def person(user_id):
        results = {'done': len(ids.get('rec:%d:done' % user_id, ()))}
        for bucket in BUCKETS:
            results[bucket] = len(ids.get('rec:%d:%s' % (user_id, bucket), ()))
        return {'id': user_id, 'name': names.get(user_id) or 'Сотрудник %d' % user_id, **results,
                **_efficiency_row(stats.get(user_id, {}))}

    team = sorted((person(user_id) for user_id in people),
                  key=lambda row: (-row['done'], -row['calls'], -row['whatsapp'], row['name']))
    total_stats = {'calls': 0, 'talked': 0, 'timed': 0, 'talk_sec': 0, 'whatsapp': 0}
    for row in stats.values():
        for field in total_stats:
            total_stats[field] += int(row.get(field) or 0)
    totals = {'done': len(ids.get('done:%s' % ALL, ()))}
    for bucket in BUCKETS:
        totals[bucket] = len(ids.get('%s:%s' % (bucket, ALL), ()))
    totals.update(_efficiency_row(total_stats))

    now = rules.now_almaty()
    today = today or now.date()
    viewer_id = int(viewer_id) if viewer_id else None
    result = {
        'day': day.isoformat(),
        'title': day_title(day),
        'is_today': day == today,
        # Отчёт за сегодня живой — на скриншоте видно, на какой момент цифры.
        'as_of': now.strftime('%H:%M') if day == today else None,
        'recruiter': {'id': recruiter_id, 'name': names.get(recruiter_id) or ''} if recruiter_id else None,
        'channels': channel_list,
        'rows': rows,
        'people': team,
        'totals': totals,
        'result_columns': [{'key': key, 'label': label, 'hint': hint} for key, label, hint in RESULT_COLUMNS],
        'efficiency_columns': [{'key': key, 'label': label, 'hint': hint} for key, label, hint in EFFICIENCY_COLUMNS],
        'notes': [{**note, 'mine': note['user_id'] == viewer_id} for note in notes],
    }
    if with_ids:
        result['ids'] = {key: sorted(value) for key, value in ids.items()}
    return result


def card_ids_for(cursor, day, key, *, recruiter_id=None):
    """Карточки за цифрой отчёта — тот же расчёт."""
    data = build(cursor, day, recruiter_id=recruiter_id, with_ids=True)
    return list(data['ids'].get(str(key or ''), []))


# ─── Excel ────────────────────────────────────────────────────────────────────

def export_day(data):
    """Тот же отчёт листом Excel: заголовок, воронка по каналам, по рекрутерам, эффективность, «Другая работа»."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    header_fill = PatternFill('solid', fgColor='1F2937')
    header_font = Font(bold=True, color='FFFFFF')
    bold = Font(bold=True)
    book = Workbook()
    ws = book.active
    ws.title = 'Отчёт за день'
    scope = (data.get('recruiter') or {}).get('name') or 'Все рекрутеры'
    ws.append(['Воронка найма · отчёт за %s · %s' % (data['title'], scope)])
    ws['A1'].font = Font(bold=True, size=13)

    def header(values):
        ws.append(values)
        for cell in ws[ws.max_row]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

    def section(title):
        ws.append([])
        ws.append([title])
        ws.cell(row=ws.max_row, column=1).font = bold

    channels = data['channels']
    section('Воронка за день')
    header(['Показатель'] + [channel['label'] for channel in channels] + ['Итого'])
    for row in data['rows']:
        label = ('   ' + row['label']) if row.get('part') else row['label']
        ws.append([label] + [row['cells'].get(channel['code'], 0) for channel in channels] + [row['cells'].get(ALL, 0)])
        if row['key'] == 'done':
            ws.cell(row=ws.max_row, column=1).font = bold

    people = data.get('people') or []
    if not data.get('recruiter') and people:
        section('По рекрутерам')
        header(['Рекрутер'] + [column['label'] for column in data['result_columns']])
        for person in people + [dict(data['totals'], name='Итого')]:
            ws.append([person['name']] + [person.get(column['key'], 0) for column in data['result_columns']])

    def efficiency_cells(row):
        cells = []
        for column in data['efficiency_columns']:
            value = row.get(column['key'])
            cells.append(talk_clock(value) if column['key'] in ('avg_talk_sec', 'talk_sec') else value)
        return [cell if cell is not None else '—' for cell in cells]

    section('Эффективность')
    header(['Рекрутер'] + [column['label'] for column in data['efficiency_columns']])
    rows = people if people else [dict(data['totals'], name=scope)]
    for person in rows:
        ws.append([person['name']] + efficiency_cells(person))
    if not data.get('recruiter') and len(people) > 1:
        ws.append(['Итого'] + efficiency_cells(data['totals']))

    notes = [note for note in data.get('notes') or [] if str(note.get('body') or '').strip()]
    if notes:
        section('Другая работа за день')
        for note in notes:
            ws.append([note['name'], note['body']])
            ws.cell(row=ws.max_row, column=2).alignment = Alignment(wrap_text=True, vertical='top')

    ws.column_dimensions['A'].width = 34
    for letter in 'BCDEFGHIJK':
        ws.column_dimensions[letter].width = 15
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()
