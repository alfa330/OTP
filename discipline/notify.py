# -*- coding: utf-8 -*-
"""Уведомления раздела «Дисциплина» (ТЗ п. 12): колокол iCore и Telegram.

Колокол — всегда: строка в discipline_notifications одному человеку об одном
событии. Telegram — по настройке отдела, событие за событием (вместо почты из
ТЗ: отправки почты в портале нет). Сообщение Telegram копится в «исходящие» и
уходит ПОСЛЕ фиксации транзакции: откат не должен оставить в мессенджере
уведомление о записи, которой в базе нет.

Кому — правило ТЗ: оператору — его запись; «руководителю группы» — СВ группы
человека на сегодня; «руководителю» — глава отдела записи. Себе уведомление
не шлём: кто сам зафиксировал или утвердил, тот и так знает.
"""

import html

from . import catalog, queries


def record_link(base_url, record_id):
    base = str(base_url or '').strip().rstrip('/')
    return '%s/?view=discipline&record=%s' % (base, int(record_id)) if base else None


def _markup(link, text='Открыть запись'):
    return {'inline_keyboard': [[{'text': text, 'url': link}]]} if link else None


class Outbox:
    """Исходящие Telegram, отправляются после commit (routes)."""

    def __init__(self):
        self.messages = []

    def add(self, chat_id, text, markup=None):
        self.messages.append((int(chat_id), text, markup))


def _esc(value):
    return html.escape(str(value or ''), quote=False)


def push(cursor, outbox, *, event, user_ids, settings, title, body=None, record_id=None, control_id=None,
         tone='default', dedupe_key=None, base_url=None, exclude=()):
    """Колокол каждому + Telegram тем, у кого он привязан, если событие включено."""
    excluded = {int(x) for x in exclude or () if x}
    targets = [int(u) for u in user_ids or () if u and int(u) not in excluded]
    created = queries.push_notifications(
        cursor, user_ids=targets, kind=event, title=title, body=body, record_id=record_id,
        control_id=control_id, tone=tone, dedupe_key=dedupe_key)
    if outbox is None or not created:
        return created
    if not (settings or {}).get('telegram', {}).get(event):
        return created
    chats = queries.telegram_ids(cursor, created)
    if not chats:
        return created
    link = record_link(base_url, record_id) if record_id else (
        '%s/?view=discipline&tab=controls' % str(base_url).rstrip('/') if base_url else None)
    text = '<b>%s</b>' % _esc(title)
    if body:
        text += '\n%s' % _esc(body)
    for chat_id in chats.values():
        outbox.add(chat_id, text, _markup(link, 'Открыть в iCore'))
    return created


def describe(record):
    """Запись одной строкой: «Д-2026-0012 · Опоздание к началу смены · 12.10 09:14»."""
    at = record.get('occurred_at')
    when = at.strftime('%d.%m %H:%M') if hasattr(at, 'strftime') else ''
    return ' · '.join(part for part in (record.get('number'), record.get('type_title'), when) if part)


def ordinal_text(info):
    return '%s %s' % (catalog.ordinal_word(info.get('ordinal') or 1).capitalize(), 'нарушение за период')
