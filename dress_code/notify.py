# -*- coding: utf-8 -*-
"""Telegram раздела «Дресс-код» (ТЗ п. 6, 8). Уходит ПОСЛЕ фиксации транзакции.

Кому:
* новый спорный случай и повторное нарушение — главе отдела сотрудника
  (руководитель офиса из ТЗ: «очередь спорных фото своего офиса с уведомлением»);
* подтверждённое нарушение и просьба переснять — самому сотруднику.

Фото в Telegram не уходит — только ссылка в iCore: снимок лица в мессенджере
разошёлся бы по пересылкам, а в разделе его видят только те, кому положено.
"""

import html
import logging

from . import AI_LABELS, DECISION_CONFIRMED, DECISION_RETAKE

log = logging.getLogger(__name__)


def _esc(value):
    return html.escape(str(value or ''), quote=False)


def _day(value):
    text = str(value or '')[:10]
    return '%s.%s' % (text[8:10], text[5:7]) if len(text) == 10 else text


def _time(value):
    text = str(value or '')
    return text[11:16] if len(text) >= 16 else ''


def _link(base_url, query):
    base = str(base_url or '').strip().rstrip('/')
    return '%s/?%s' % (base, query) if base else None


def _markup(link, text):
    return {'inline_keyboard': [[{'text': text, 'url': link}]]} if link else None


def reviewer_chats(cursor, check):
    """Telegram главы отдела сотрудника (если это не он сам)."""
    if not check.get('department_id'):
        return []
    cursor.execute("""
        SELECT u.telegram_id
          FROM departments d
          JOIN users u ON u.id = d.head_user_id
         WHERE d.id = %s AND u.telegram_id IS NOT NULL AND u.id <> %s
    """, (check['department_id'], check['user_id']))
    return [row[0] for row in cursor.fetchall() if row[0]]


def employee_chat(cursor, check):
    cursor.execute("SELECT telegram_id FROM users WHERE id = %s", (check['user_id'],))
    row = cursor.fetchone()
    return row[0] if row and row[0] else None


def review_message(check, base_url):
    reasons = [item.get('sign') for item in check.get('ai_reasons') or [] if item.get('sign')]
    lines = ['<b>Дресс-код: нужна проверка</b>',
             _esc(' · '.join(x for x in (check.get('employee'), check.get('city'),
                                         '%s %s' % (_day(check.get('day')), _time(check.get('taken_at')))) if x)),
             'ИИ: %s' % _esc(AI_LABELS.get(check.get('ai_status'), check.get('ai_status')))]
    if reasons:
        lines.append('Признаки: %s' % _esc('; '.join(reasons)))
    elif check.get('ai_note'):
        lines.append(_esc(check['ai_note']))
    link = _link(base_url, 'view=group_late_bot&dc_check=%d' % int(check['id']))
    return '\n'.join(lines), _markup(link, 'Открыть в iCore')


def repeat_message(check, previous, base_url):
    days = ', '.join(_day(item.get('day')) for item in previous or [])
    lines = ['<b>Дресс-код: повторное нарушение за 10 дней</b>',
             _esc('%s — %s' % (check.get('employee') or '', _day(check.get('day'))))]
    if days:
        lines.append('Прежнее нарушение: %s' % _esc(days))
    link = _link(base_url, 'view=group_late_bot&dc_check=%d' % int(check['id']))
    return '\n'.join(lines), _markup(link, 'Открыть в iCore')


def employee_message(check, base_url):
    day = _day(check.get('day'))
    link = _link(base_url, 'view=dress_code')
    if check.get('decision') == DECISION_CONFIRMED:
        reasons = [item.get('sign') for item in check.get('ai_reasons') or [] if item.get('sign')]
        lines = ['<b>Дресс-код за %s: нарушение подтверждено</b>' % _esc(day)]
        if check.get('is_repeat'):
            lines.append('Это повторное нарушение за 10 дней.')
        if reasons:
            lines.append('Причина: %s' % _esc('; '.join(reasons)))
        if check.get('decision_comment'):
            lines.append('Комментарий руководителя: %s' % _esc(check['decision_comment']))
        lines.append('Откройте «Дресс-код» в iCore и нажмите «Ознакомлен». '
                     'Когда исправите, там же сделайте повторное фото.')
        return '\n'.join(lines), _markup(link, 'Открыть «Дресс-код»')
    if check.get('decision') == DECISION_RETAKE:
        lines = ['<b>Дресс-код за %s: руководитель просит переснять фото</b>' % _esc(day)]
        if check.get('decision_comment'):
            lines.append('Комментарий: %s' % _esc(check['decision_comment']))
        lines.append('Откройте «Дресс-код» в iCore и сделайте фото ещё раз.')
        return '\n'.join(lines), _markup(link, 'Переснять фото')
    return None, None


def send_all(send, messages):
    """messages — [(chat_id, text, markup)]. Сбой одной отправки не мешает остальным."""
    sent = 0
    for chat_id, text, markup in messages:
        if not chat_id or not text:
            continue
        try:
            response = send(chat_id, text, reply_markup=markup)
            ok = getattr(response, 'ok', True)
            if ok:
                sent += 1
            else:
                log.warning('Дресс-код: Telegram %s ответил %s', chat_id, getattr(response, 'status_code', '?'))
        except Exception:
            log.warning('Дресс-код: сообщение в Telegram %s не ушло', chat_id, exc_info=True)
    return sent
