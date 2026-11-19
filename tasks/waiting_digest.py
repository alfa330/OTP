"""Сообщение бота сразу после входа: задачи, которые ждут человека.

Уведомление о постановке («🆕 Новая задача») уходит один раз — в момент постановки и
только тем исполнителям, у кого Telegram уже привязан к учётке (POST /api/tasks в
tasks/routes.py). Кто вошёл в бота позже, о поставленном раньше не узнавал ничем:
19.11.2026 так потерялась задача маркетолога — её поставили за 14 минут до входа.
Теперь вход досылает такие задачи одним сообщением (решение владельца 19.11.2026).

Здесь только текст и кнопка — без базы и без Telegram, чтобы проверялось тестом.
Список задач даёт db.get_tasks_waiting_for_assignee, ссылку на задачу — link_for
(в боте это _build_task_deep_link).
"""

import html
from datetime import datetime

# Сколько задач перечислить; остальные — строкой «…и ещё N».
DIGEST_LIMIT = 10

# Запас до предела Telegram в 4096 символов: темы бывают по 255 знаков.
_MAX_MESSAGE_CHARS = 3800

_PRIORITY_MARKS = {
    'urgent': 'срочная',
    'critical': 'критичная',
}


def _escape(text, max_chars=None):
    value = str(text or '').strip()
    if max_chars is not None and len(value) > max_chars:
        value = value[:max_chars - 3].rstrip() + '...'
    return html.escape(value)


def _format_due(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.strftime('%d.%m.%Y %H:%M')
    try:
        return datetime.fromisoformat(str(value)).strftime('%d.%m.%Y %H:%M')
    except ValueError:
        return str(value)


def _item_html(item, link):
    """«PR-7 · Тема — от Постановщика, до 25.11.2026 18:00» — ключ вне ссылки, как в
    уведомлении о постановке: его переписывают в чат как имя задачи."""
    subject = _escape(item.get('subject') or f"Задача #{item.get('id')}", 120)
    if link:
        subject = f'<a href="{html.escape(link)}">{subject}</a>'
    key = item.get('key')
    title = f"{_escape(key, 20)} · {subject}" if key else subject

    details = []
    if item.get('creator_name'):
        details.append(f"от {_escape(item['creator_name'], 60)}")
    due_label = _format_due(item.get('due_at'))
    if due_label:
        details.append(f"до {_escape(due_label)}")
    priority_mark = _PRIORITY_MARKS.get(item.get('priority'))
    if priority_mark:
        details.append(priority_mark)
    if item.get('status') == 'returned':
        details.append('возвращена на доработку')
    return f"{title} — {', '.join(details)}" if details else title


def build_message(digest, link_for):
    """(текст HTML, кнопка или None) — либо None, если ждущих задач нет.

    digest — ответ db.get_tasks_waiting_for_assignee: {"total", "items"}.
    link_for(task_id, key) — ссылка на задачу в портале ('' или None — без ссылки).
    Кнопка «Открыть задачу» — только у единственной задачи, как в уведомлении о
    постановке; у списка ссылка стоит на каждой теме.
    """
    items = list((digest or {}).get('items') or [])
    if not items:
        return None
    total = max(int((digest or {}).get('total') or 0), len(items))

    header = [f"<b>📋 Вас ждут задачи: {total}</b>", ""]
    footer = ["", "<b>Откройте раздел «Задачи», чтобы посмотреть детали.</b>"]
    lines, links = [], []
    for item in items:
        link = link_for(item.get('id'), item.get('key')) or None
        line = f"{len(lines) + 1}. {_item_html(item, link)}"
        # Хвост «…и ещё N» тоже должен влезть — считаем его по худшему случаю.
        tail = f"…и ещё {total} — в разделе «Задачи»."
        if lines and len("\n".join(header + lines + [line, tail] + footer)) > _MAX_MESSAGE_CHARS:
            break
        lines.append(line)
        links.append(link)

    rest = total - len(lines)
    if rest > 0:
        lines.append(f"…и ещё {rest} — в разделе «Задачи».")

    reply_markup = None
    if total == 1 and links[0]:
        reply_markup = {"inline_keyboard": [[{"text": "Открыть задачу", "url": links[0]}]]}
    return "\n".join(header + lines + footer), reply_markup
