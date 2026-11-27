# -*- coding: utf-8 -*-
"""Заявка в IT, поставленная задачей на доску IT Helpdesk.

Владелец 27.11.2026: «В разделе тех причины есть возможность создавать тикеты
для айти, его надо полностью перенести в раздел задачи при отметке it helpdesk…
с таким же функционалом, что бы тикеты создавались там же и попадали в их же
доску». Форма заявки (src/components/tasks/HelpdeskTicketForm.jsx) ставит
обычную задачу на доску ITH через POST /api/tasks и кладёт рядом поле
it_ticket — тему, отдел, срочность в шкале ИИ и заполненные поля. Задача — то,
по чему IT работает; журнал it_tickets — данные заявок, как и раньше (по нему
сверяют качество ИИ: тема, срочность, поля), теперь со ссылкой на задачу.

Модуль — лист (json, logging, task_projects): гоняется тестами без базы.
Ошибка журнала задачу не роняет — она уже поставлена, исполнители уведомлены.
"""
import json
import logging

import task_projects

PROFILES = ('op', 'szov')
PRIORITIES = ('low', 'medium', 'high', 'critical')
MAX_FIELDS = 30
MAX_FIELD_KEY = 64
MAX_FIELD_VALUE = 2000
MAX_TOPIC = 255


def _text(value, limit):
    return ' '.join(str(value or '').split())[:limit]


def parse_ticket_meta(raw):
    """JSON поля it_ticket → словарь с проверенными значениями или None.

    Чужое поле: строки обрезаем, профиль и срочность — только из своих наборов,
    полей не больше MAX_FIELDS. Кривой JSON — None (журнал просто не пишется).
    """
    if raw is None or raw == '':
        return None
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    profile = str(data.get('profile') or '').strip().lower()
    priority = str(data.get('priority') or '').strip().lower()
    fields = {}
    raw_fields = data.get('fields')
    if isinstance(raw_fields, dict):
        for key, value in raw_fields.items():
            if len(fields) >= MAX_FIELDS:
                break
            key_text = str(key or '').strip()[:MAX_FIELD_KEY]
            value_text = str(value if value is not None else '').strip()[:MAX_FIELD_VALUE]
            if key_text and value_text:
                fields[key_text] = value_text
    return {
        'profile': profile if profile in PROFILES else 'op',
        'category': _text(data.get('category'), MAX_TOPIC),
        'subcategory': _text(data.get('subcategory'), MAX_TOPIC),
        'priority': priority if priority in PRIORITIES else 'medium',
        'fields': fields,
    }


def is_helpdesk_task(created):
    """Задача поставлена на доску заявок: её ключ — ITH-N."""
    parsed = task_projects.parse_task_key((created or {}).get('key'))
    return bool(parsed) and task_projects.is_helpdesk_code(parsed[0])


def record_task_ticket(db, *, raw_meta, created, subject, description, requester_id, requester_name):
    """Пишет заявку в журнал it_tickets, если задача — с доски заявок.

    Возвращает запись журнала или None (не доска заявок, нет данных, сбой базы).
    Задачу с другой доски с полем it_ticket в журнал не пускаем: поле приходит
    от клиента, а журнал — про заявки в IT.
    """
    if not is_helpdesk_task(created):
        return None
    meta = parse_ticket_meta(raw_meta)
    if meta is None:
        return None
    try:
        return db.record_it_ticket(
            profile=meta['profile'],
            category=meta['category'],
            subcategory=meta['subcategory'],
            priority=meta['priority'],
            title=str(subject or '')[:480],
            body=str(description or subject or '').strip() or '—',
            fields=meta['fields'],
            channel_id=None,
            channel_chat_id=None,
            channel_title=None,
            telegram_message_id=None,
            status='task',
            created_by=requester_id,
            created_by_name=requester_name,
            task_id=(created or {}).get('id'),
        )
    except Exception:
        logging.warning('IT ticket journal write failed for task %s', (created or {}).get('id'), exc_info=True)
        return None
