# -*- coding: utf-8 -*-
"""Уведомления раздела (ТЗ 8.3): строки колокола и тексты для Telegram. SQL — через queries.

Событие → получатель, как в таблице ТЗ:

    новый отклик в пуле                     рекрутеры (все активные выдачи)
    повторный отклик по закреплённой        закреплённый рекрутер
    наступила дата следующего контакта      закреплённый рекрутер
    карточка возвращена в пул по сроку      рекрутер и руководитель рекрутинга
    выход на линию                          руководитель отдела, куда был найм
    начисление мотивации                    рекрутер

В Telegram дублируются только нарушения сроков: возврат в пул по сроку и просроченные
задачи (утренняя сводка). Остальное — колокол. Отправка живёт в bot_schedule2
(`_send_telegram_text_message`) и приходит в сервис аргументом; здесь только тексты.
"""

import html

from . import queries, rules

KIND_NEW_RESPONSE = 'new_response'
KIND_REPEAT = 'repeat_response'
KIND_NEXT_CONTACT = 'next_contact'
KIND_RETURNED = 'returned_to_pool'
KIND_ON_LINE = 'on_line'
KIND_MOTIVATION = 'motivation'
KIND_REASSIGNED = 'reassigned'


def _esc(text):
    return html.escape(str(text or ''), quote=False)


def card_title(card):
    name = (card.get('full_name') or '').strip() or 'Без имени'
    number = card.get('candidate_no')
    return ('№%s · %s' % (number, name)) if number else name


def card_link(base_url, card_id):
    base = str(base_url or '').strip().rstrip('/')
    return '%s/?view=hiring&card=%s' % (base, int(card_id)) if base else None


def reply_markup(link):
    return {'inline_keyboard': [[{'text': 'Открыть карточку', 'url': link}]]} if link else None


def new_response(cursor, card, now):
    """Новый отклик в пуле — всем рекрутерам. Один раз на карточку."""
    recipients = queries.recruiter_user_ids(cursor)
    body = ' · '.join(x for x in (card.get('source_name'), card.get('vacancy_name') or card.get('vacancy_text'),
                                   card.get('city')) if x)
    return queries.push_notification(cursor, recipients, KIND_NEW_RESPONSE, 'Новый отклик: %s' % card_title(card),
                                     body or None, card['id'], now, dedupe_key='new:%s' % card['id'])


def repeat_response(cursor, card, source_name, now):
    if not card.get('recruiter_id'):
        return 0
    return queries.push_notification(cursor, [card['recruiter_id']], KIND_REPEAT,
                                     'Повторный отклик: %s' % card_title(card),
                                     'Источник: %s' % (source_name or '—'), card['id'], now)


def next_contact(cursor, card, now):
    return queries.push_notification(cursor, [card['recruiter_id']], KIND_NEXT_CONTACT,
                                     'Пора связаться: %s' % card_title(card),
                                     'Дата следующего контакта наступила', card['id'], now, tone='warning',
                                     dedupe_key='next:%s:%s' % (card['id'], card['next_contact_at'].isoformat(timespec='minutes')))


def returned_to_pool(cursor, card, recruiter_id, now):
    leads = queries.lead_user_ids(cursor)
    recipients = set(leads) | ({int(recruiter_id)} if recruiter_id else set())
    return queries.push_notification(cursor, recipients, KIND_RETURNED,
                                     'Вернулась в пул: %s' % card_title(card),
                                     'Без касаний %s дней' % rules.IDLE_RETURN_DAYS, card['id'], now, tone='warning')


def reassigned(cursor, card, old_recruiter_id, new_recruiter_id, now, comment=None):
    """Переназначение или возврат в пул руководителем: старому — что сняли, новому — что дали."""
    created = 0
    if old_recruiter_id:
        created += queries.push_notification(cursor, [old_recruiter_id], KIND_REASSIGNED,
                                             'Карточка снята: %s' % card_title(card), comment or None, card['id'], now)
    if new_recruiter_id and new_recruiter_id != old_recruiter_id:
        created += queries.push_notification(cursor, [new_recruiter_id], KIND_REASSIGNED,
                                             'Вам передана карточка: %s' % card_title(card), comment or None, card['id'], now)
    return created


def _department_head(cursor, card):
    department_id = card.get('department_id')
    heads = queries.heads_of_departments(cursor, [department_id]) if department_id else {}
    return heads.get(int(department_id)) if department_id else None


def on_line(cursor, card, now):
    """Выход на линию — руководителю отдела, куда был найм: смены считаются из статусов сами, седьмую подтвердить."""
    head = _department_head(cursor, card)
    if not head:
        return 0
    return queries.push_notification(cursor, [head], KIND_ON_LINE, 'Вышел на линию: %s' % card_title(card),
                                     'Смены считаются из статусов работы сами; после седьмой подтвердите «Отработал 7 дней»',
                                     card['id'], now, dedupe_key='line:%s' % card['id'])


def shifts_ready(cursor, card, now):
    """Семь смен по статусам набраны — руководителю отдела подтвердить «Отработал 7 дней»."""
    head = _department_head(cursor, card)
    if not head:
        return 0
    return queries.push_notification(cursor, [head], KIND_ON_LINE, 'Семь смен на линии: %s' % card_title(card),
                                     'Подтвердите «Отработал 7 дней» в карточке', card['id'], now,
                                     dedupe_key='shifts7:%s' % card['id'])


def motivation_counted(cursor, card, recruiter_id, now):
    if not recruiter_id:
        return 0
    return queries.push_notification(cursor, [recruiter_id], KIND_MOTIVATION,
                                     'Мотивация засчитана: %s' % card_title(card),
                                     'Кандидат отработал 7 смен — найм закрыт', card['id'], now,
                                     dedupe_key='motivation:%s' % card['id'])


# ─── Telegram (только нарушения сроков) ───────────────────────────────────────

def returned_html(card, link):
    text = ('⏰ <b>Карточка вернулась в пул</b>\n%s\nБез касаний %s дней — закрепление снято.'
            % (_esc(card_title(card)), rules.IDLE_RETURN_DAYS))
    return text + ('\n' + link if link else '')


def overdue_digest_html(count, base_url):
    base = str(base_url or '').strip().rstrip('/')
    text = ('⏰ <b>Воронка найма: просроченные задачи</b>\nКарточек с пропущенной датой контакта: <b>%d</b>.'
            % int(count))
    return text + ('\n%s/?view=hiring' % base if base else '')
