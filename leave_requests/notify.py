# -*- coding: utf-8 -*-
"""Telegram по заявкам на отпуск, Б/С и больничный (ТЗ п. 7).

Сообщения копятся в «исходящие» и уходят ПОСЛЕ фиксации транзакции: откат не
должен оставить в мессенджере уведомление о заявке, которой в базе нет.

Кому: тому, чей теперь ход (СВ, глава), и оператору — о каждом изменении его
заявки. Себе не шлём: кто сам нажал, тот и так знает. Колокол живёт отдельно —
он считает заявки по их состоянию (notifications/sources.py: leave_requests).
"""

import html

from . import access, queries, rules


class Outbox:
    """Исходящие Telegram, отправляются после commit (routes)."""

    def __init__(self):
        self.messages = []

    def add(self, chat_id, text, markup=None):
        self.messages.append((int(chat_id), text, markup))


_CREATED_TITLES = {
    'annual_leave': 'Заявка на отпуск',
    'bs': 'Заявка на Б/С',
    'sick_leave': 'Заявка на больничный',
}

# Пересечения считаются по всем видам (queries.colleague_overlaps): рядом с
# отпусками там и больничные, поэтому «отсутствуют», а не «отдыхают».
_OVERLAPS_LINE = '\n⚠️ В эти даты уже отсутствуют: %d'


def _esc(value):
    return html.escape(str(value or ''), quote=False)


def request_link(base_url, request_id):
    base = str(base_url or '').strip().rstrip('/')
    return '%s/?view=work_schedules&leave=%s' % (base, int(request_id)) if base else None


def _markup(link):
    return {'inline_keyboard': [[{'text': 'Открыть в iCore', 'url': link}]]} if link else None


def _summary(raw, today, *, start=None, end=None):
    """«Ежегодный отпуск · 01.07 — 14.07 · 14 дней»."""
    start = start or raw['start_date']
    end = end or raw['end_date']
    return '%s · %s · %s' % (
        rules.KIND_LABELS.get(raw['kind'], raw['kind']),
        rules.format_period(start, end, today),
        rules.days_text(rules.period_days(start, end)),
    )


def _send(cursor, outbox, user_ids, text, *, request_id, base_url, exclude=()):
    if outbox is None:
        return
    skip = {int(x) for x in exclude or () if x}
    targets = [int(u) for u in user_ids or () if u and int(u) not in skip]
    # Пилот: Telegram — только тем, кому заявки открыты (access.py).
    allowed = access.allowed_ids(cursor, targets)
    targets = [u for u in targets if u in allowed]
    chats = queries.telegram_chats(cursor, targets)
    markup = _markup(request_link(base_url, request_id))
    for user_id in dict.fromkeys(targets):
        chat_id = chats.get(user_id)
        if chat_id:
            outbox.add(chat_id, text, markup)


def _comment_line(comment, label='Комментарий'):
    return '\n%s: %s' % (label, _esc(comment)) if comment else ''


def step_actor_ids(cursor, raw):
    """Кто решает заявку на её текущем шаге."""
    if raw['status'] == 'supervisor':
        return [raw['supervisor_id']] if raw['supervisor_id'] else [queries.department_head(cursor, raw['department_id'])]
    if raw['status'] == 'head':
        return [queries.department_head(cursor, raw['department_id'])]
    return []


def on_created(cursor, outbox, raw, *, actor_id, today, overlaps_total, base_url, edited=False):
    title = 'Заявка изменена' if edited else _CREATED_TITLES.get(raw['kind'], 'Заявка')
    text = '<b>%s</b>\n%s\n%s' % (_esc(title), _esc(raw['operator_name']), _esc(_summary(raw, today)))
    text += _comment_line(raw.get('reason'), 'Причина')
    if overlaps_total:
        text += _OVERLAPS_LINE % int(overlaps_total)
    _send(cursor, outbox, step_actor_ids(cursor, raw), text,
          request_id=raw['id'], base_url=base_url, exclude=(actor_id,))


def on_cancelled(cursor, outbox, raw, *, previous_status, actor_id, today, base_url):
    previous = dict(raw, status=previous_status)
    if previous_status == 'proposal':
        previous['status'] = 'supervisor'
    text = '<b>Заявка отозвана</b>\n%s\n%s' % (_esc(raw['operator_name']), _esc(_summary(raw, today)))
    _send(cursor, outbox, step_actor_ids(cursor, previous), text,
          request_id=raw['id'], base_url=base_url, exclude=(actor_id,))


def on_supervisor_approved(cursor, outbox, raw, *, actor_id, actor_name, today, overlaps_total, base_url):
    head_text = '<b>Ждёт вашего утверждения</b>\n%s\n%s\nСогласовал(а): %s' % (
        _esc(raw['operator_name']), _esc(_summary(raw, today)), _esc(actor_name))
    if overlaps_total:
        head_text += _OVERLAPS_LINE % int(overlaps_total)
    _send(cursor, outbox, step_actor_ids(cursor, raw), head_text,
          request_id=raw['id'], base_url=base_url, exclude=(actor_id,))
    operator_text = '<b>Супервайзер согласовал заявку</b>\n%s\nТеперь её утверждает руководитель отдела.' % (
        _esc(_summary(raw, today)))
    _send(cursor, outbox, [raw['operator_id']], operator_text,
          request_id=raw['id'], base_url=base_url, exclude=(actor_id,))


def on_proposed(cursor, outbox, raw, *, actor_id, comment, today, base_url):
    text = '<b>Предложены другие даты</b>\nВаша заявка: %s\nПредлагают: %s%s\nОтветьте в «Моих сменах» → «Запросы».' % (
        _esc(_summary(raw, today)),
        _esc(_summary(raw, today, start=raw['proposed_start_date'], end=raw['proposed_end_date'])),
        _comment_line(comment),
    )
    _send(cursor, outbox, [raw['operator_id']], text,
          request_id=raw['id'], base_url=base_url, exclude=(actor_id,))


def on_proposal_answered(cursor, outbox, raw, *, accepted, proposer_id, actor_id, comment, today, base_url):
    if accepted:
        proposer_text = '<b>Оператор согласился с датами</b>\n%s\n%s\nЗаявка ушла руководителю.' % (
            _esc(raw['operator_name']), _esc(_summary(raw, today)))
        _send(cursor, outbox, [proposer_id], proposer_text,
              request_id=raw['id'], base_url=base_url, exclude=(actor_id,))
        head_text = '<b>Ждёт вашего утверждения</b>\n%s\n%s' % (
            _esc(raw['operator_name']), _esc(_summary(raw, today)))
        _send(cursor, outbox, step_actor_ids(cursor, raw), head_text,
              request_id=raw['id'], base_url=base_url, exclude=(actor_id, proposer_id))
        return
    text = '<b>Оператор не согласен с датами</b>\n%s\nОстаётся заявка: %s%s' % (
        _esc(raw['operator_name']), _esc(_summary(raw, today)), _comment_line(comment))
    _send(cursor, outbox, [proposer_id] + step_actor_ids(cursor, raw), text,
          request_id=raw['id'], base_url=base_url, exclude=(actor_id,))


def on_decided(cursor, outbox, raw, *, approved, actor_id, comment, today, base_url):
    if approved:
        text = '<b>Заявка утверждена</b>\n%s\nПериод уже стоит в графике.' % _esc(_summary(raw, today))
    else:
        text = '<b>Заявка отклонена</b>\n%s%s' % (_esc(_summary(raw, today)), _comment_line(comment))
    _send(cursor, outbox, [raw['operator_id']], text,
          request_id=raw['id'], base_url=base_url, exclude=(actor_id,))
    # СВ, который заявку согласовал, узнаёт, чем кончилось у главы.
    if raw.get('supervisor_id'):
        sv_text = '<b>%s</b>\n%s\n%s%s' % (
            'Руководитель утвердил заявку' if approved else 'Руководитель отклонил заявку',
            _esc(raw['operator_name']), _esc(_summary(raw, today)), _comment_line(comment))
        _send(cursor, outbox, [raw['supervisor_id']], sv_text,
              request_id=raw['id'], base_url=base_url, exclude=(actor_id, raw['operator_id']))
