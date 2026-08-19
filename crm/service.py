"""Сценарии раздела «Обращения»: связывают базу и Telegram.

Здесь живёт то, что нужно ОБЕИМ точкам входа — HTTP-роутам раздела и
обработчикам бота. Иначе правило «пришёл ответ → погасить нить, зажечь
уведомление автору» существовало бы в двух экземплярах и разошлось бы.

Порядок работы с соединением одинаков во всех функциях:

    короткий курсор (прочитать) → закрыть → сеть → короткий курсор (записать)

Сеть НИКОГДА не вызывается с открытым курсором. Пул проекта делится с SSE
аукциона и колокола, и он уже голодал однажды: держать соединение те 2–15
секунд, пока Telegram думает, — прямой путь повторить это.
"""

import logging
from datetime import datetime, timedelta

from . import queries, telegram, transport


def _now_text():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Asia/Almaty')).strftime('%d.%m.%Y %H:%M')
    except Exception:
        return datetime.now().strftime('%d.%m.%Y %H:%M')


def _due_text(due_at):
    if not due_at:
        return None
    try:
        return due_at.strftime('%d.%m %H:%M')
    except AttributeError:
        return str(due_at)


def compute_due_at(sla_minutes):
    """Срок ответа = сейчас + SLA очереди. None, если у очереди срока нет."""
    if not sla_minutes:
        return None
    return datetime.now() + timedelta(minutes=int(sla_minutes))


# ─────────────────────────────────────────────────────────────────────────────
# Отправка обращения в группу
# ─────────────────────────────────────────────────────────────────────────────

def deliver_ticket(db, ticket_id, *, attachment=None):
    """Отправляет обращение в Telegram-группу очереди. Возвращает (ok, error).

    Вызывается сразу после создания и повторно кнопкой «Отправить ещё раз».
    Повторная отправка защищена: если сообщение уже ушло, второй раз в группу
    ничего не летит — иначе кнопка «повторить» плодила бы дубли в чужом чате.
    """
    with db._get_cursor() as cursor:
        payload = queries.delivery_payload(cursor, ticket_id)
    if not payload:
        return False, 'Обращение не найдено'
    if payload['delivery_status'] == 'sent' and payload['tg_message_id']:
        return True, None
    if not payload['chat_id']:
        return False, 'У очереди не привязана Telegram-группа'

    text = telegram.build_ticket_message(
        ticket_id=ticket_id,
        subject=payload['subject'],
        body=payload['body'],
        queue_title=payload['queue_title'],
        topic_title=payload['topic_title'],
        priority=payload['priority'],
        author_name=payload['created_by_name'],
        department_name=payload['department_name'],
        client_name=payload['client_name'],
        client_phone=payload['client_phone'],
        created_text=_now_text(),
        due_text=_due_text(payload['due_at']),
    )
    result, error = transport.send_message(
        payload['chat_id'], text,
        reply_markup=telegram.build_keyboard(ticket_id, payload['status']),
    )

    if result is None:
        with db._get_cursor() as cursor:
            queries.set_delivery(cursor, ticket_id, status='failed', error=error)
            queries.add_event(cursor, ticket_id=ticket_id, kind='send_failed',
                              payload={'error': error})
        return False, error

    message_id = result.get('message_id')
    with db._get_cursor() as cursor:
        queries.set_delivery(cursor, ticket_id, status='sent',
                             chat_id=payload['chat_id'], message_id=message_id, error=None)
        # Корневое сообщение ложится в нить строкой: по нему потом находится
        # обращение, на которое ответили в группе.
        queries.add_message(
            cursor, ticket_id=ticket_id, direction='out', body=payload['body'],
            author_user_id=payload['created_by'], author_name=payload['created_by_name'],
            tg_chat_id=payload['chat_id'], tg_message_id=message_id,
        )
        queries.add_event(cursor, ticket_id=ticket_id, kind='sent',
                          actor_user_id=payload['created_by'],
                          actor_name=payload['created_by_name'],
                          payload={'queue': payload['queue_title']})

    if attachment is not None:
        _send_attachment(db, ticket_id, payload['chat_id'], message_id, attachment,
                         author_user_id=payload['created_by'],
                         author_name=payload['created_by_name'])
    return True, None


def _send_attachment(db, ticket_id, chat_id, reply_to_message_id, attachment,
                     *, author_user_id=None, author_name=None):
    """Вложение уходит следом за обращением; его отказ не отменяет обращение."""
    result, error = transport.send_attachment(
        chat_id,
        file_name=attachment.get('filename') or 'attachment',
        stream=attachment.get('stream'),
        mimetype=attachment.get('mimetype'),
        reply_to_message_id=reply_to_message_id,
        caption='📎 Вложение к обращению %s' % telegram.ticket_number(ticket_id),
    )
    if result is None:
        logging.warning('crm: вложение к обращению %s не ушло: %s', ticket_id, error)
        return False, error
    with db._get_cursor() as cursor:
        queries.add_message(
            cursor, ticket_id=ticket_id, direction='out',
            author_user_id=author_user_id, author_name=author_name,
            tg_chat_id=chat_id, tg_message_id=result.get('message_id'),
            attachment={
                'kind': 'photo' if result.get('photo') else 'document',
                'file_id': _result_file_id(result),
                'name': attachment.get('filename'),
                'mime': attachment.get('mimetype'),
            },
        )
    return True, None


def _result_file_id(result):
    photo = result.get('photo')
    if isinstance(photo, list) and photo:
        return photo[-1].get('file_id')
    document = result.get('document') or {}
    return document.get('file_id')


# ─────────────────────────────────────────────────────────────────────────────
# Ответ оператора в открытую нить
# ─────────────────────────────────────────────────────────────────────────────

def post_operator_reply(db, ticket_id, body, *, author_user_id, author_name,
                        attachment=None):
    """Пишет из системы в группу реплаем к обращению. Возвращает (ok, error)."""
    with db._get_cursor() as cursor:
        payload = queries.delivery_payload(cursor, ticket_id)
    if not payload:
        return False, 'Обращение не найдено'
    if not payload['chat_id'] or not payload['tg_message_id']:
        return False, 'Обращение ещё не доставлено в группу'

    text = telegram.build_reply_message(
        ticket_id=ticket_id, author_name=author_name, body=body,
        iin=payload.get('iin'),
    )
    result, error = transport.send_message(
        payload['chat_id'], text, reply_to_message_id=payload['tg_message_id'],
    )
    if result is None:
        return False, error

    with db._get_cursor() as cursor:
        queries.add_message(
            cursor, ticket_id=ticket_id, direction='out', body=body,
            author_user_id=author_user_id, author_name=author_name,
            tg_chat_id=payload['chat_id'], tg_message_id=result.get('message_id'),
        )
        queries.touch_outbound(cursor, ticket_id)
        queries.add_event(cursor, ticket_id=ticket_id, kind='reply_sent',
                          actor_user_id=author_user_id, actor_name=author_name)

    if attachment is not None:
        _send_attachment(db, ticket_id, payload['chat_id'], result.get('message_id'),
                         attachment, author_user_id=author_user_id, author_name=author_name)
    return True, None


# ─────────────────────────────────────────────────────────────────────────────
# Приём из Telegram
# ─────────────────────────────────────────────────────────────────────────────

def ingest_group_reply(db, *, chat_id, reply_to_message_id, message):
    """Ответ сотрудника в группе → сообщение в нити + уведомление автору.

    Возвращает {'ticket_id', 'announce'} либо None, если ответ не привязался:
    реплай на постороннее сообщение бота (скажем, на отчёт другого раздела) —
    не ошибка, просто не наше дело.

    announce отвечает на вопрос «отбиваться ли в чат». Расписку бот даёт РОВНО
    ОДИН раз на обращение — на первый ответ. Дальше в группе идёт живое
    обсуждение, и «✅ ответ отправлен оператору» после каждой реплики
    превратилось бы в половину переписки.
    """
    body = telegram.message_text(message)
    attachment = telegram.extract_attachment(message)
    if not body and not attachment:
        return None

    from_user = message.get('from') if isinstance(message, dict) else getattr(message, 'from_user', None)
    author = telegram.sender_name(from_user)
    get = from_user.get if isinstance(from_user, dict) else (
        lambda key, default=None: getattr(from_user, key, default))

    with db._get_cursor() as cursor:
        found = queries.find_ticket_by_tg_message(cursor, chat_id, reply_to_message_id)
        if not found:
            return None
        ticket_id = found['ticket_id']
        # Первый ответ — тот, что застал обращение ещё не отвеченным.
        first_reply = found.get('status') in ('open', 'in_progress')
        message_id = queries.add_message(
            cursor, ticket_id=ticket_id, direction='in', body=body,
            author_name=author,
            tg_chat_id=chat_id,
            tg_message_id=(message.get('message_id') if isinstance(message, dict)
                           else getattr(message, 'message_id', None)),
            tg_from_id=get('id') if from_user is not None else None,
            tg_from_name=author,
            tg_username=get('username') if from_user is not None else None,
            attachment=attachment,
        )
        # Дубль апдейта — молча выходим: нить уже содержит этот ответ, и
        # повторно звонить автору не за что.
        if message_id is None:
            return {'ticket_id': ticket_id, 'announce': False}
        queries.touch_inbound(cursor, ticket_id, unread_kind=queries.UNREAD_REPLY)
        queries.add_event(cursor, ticket_id=ticket_id, kind='reply_received',
                          actor_name=author, payload={'from_id': get('id') if from_user else None})
    return {'ticket_id': ticket_id, 'announce': first_reply}


# Кнопки в группе. Действие → (новый статус, вид уведомления, ответ нажавшему).
_GROUP_ACTIONS = {
    'work': ('in_progress', queries.UNREAD_PROGRESS, 'Взяли в работу — оператор увидит'),
    'done': ('resolved', queries.UNREAD_DONE, 'Отмечено выполненным — оператор уведомлён'),
}


def apply_group_action(db, action, ticket_id, from_user):
    """Нажали кнопку под обращением в группе. Возвращает (текст ответа, ok).

    Именно этот путь даёт оператору «мгновенное уведомление о выполнении»:
    сотрудник в группе нажимает «Выполнено», статус меняется, триггер колокола
    будит вкладку оператора.
    """
    known = _GROUP_ACTIONS.get(action)
    if not known:
        return 'Неизвестное действие', False
    status, unread_kind, reply = known
    actor = telegram.sender_name(from_user)

    with db._get_cursor() as cursor:
        ticket = queries.get_ticket(cursor, ticket_id)
        if not ticket:
            return 'Обращение не найдено', False
        changed = queries.set_status(
            cursor, ticket_id, status, actor_name=actor,
            notify_author=True, unread_kind=unread_kind,
        )
        if not changed:
            return 'Уже отмечено', True
        queries.add_event(cursor, ticket_id=ticket_id, kind='status',
                          actor_name=actor,
                          payload={'status': status, 'via': 'telegram'})
        chat_id, message_id = ticket['tg_chat_id'], ticket['tg_message_id']

    # Кнопки обновляем ПОСЛЕ коммита и молча: сообщение могли удалить из чата,
    # и отказ Telegram не должен отменять уже изменённый статус.
    if chat_id and message_id:
        transport.edit_reply_markup(
            chat_id, message_id, telegram.build_keyboard(ticket_id, status),
        )
    return reply, True


# ─────────────────────────────────────────────────────────────────────────────
# Смена статуса из системы
# ─────────────────────────────────────────────────────────────────────────────

def change_status_from_system(db, ticket_id, status, *, actor_user_id, actor_name,
                              notify_group=True):
    """Оператор закрыл/отменил/вернул обращение в работу.

    В группу уходит короткая отбивка: сотрудники должны понимать, что вопрос
    снят, иначе они продолжат разбираться с уже решённым.
    """
    with db._get_cursor() as cursor:
        ticket = queries.get_ticket(cursor, ticket_id)
        if not ticket:
            return False, 'Обращение не найдено'
        changed = queries.set_status(
            cursor, ticket_id, status,
            actor_user_id=actor_user_id, actor_name=actor_name, notify_author=False,
        )
        if changed:
            queries.add_event(cursor, ticket_id=ticket_id, kind='status',
                              actor_user_id=actor_user_id, actor_name=actor_name,
                              payload={'status': status, 'via': 'icore'})
        # Закрыл сам автор — гасим и его «непрочитано»: он только что всё видел.
        if actor_user_id:
            queries.mark_seen_by_author(cursor, ticket_id, actor_user_id)
        chat_id, message_id = ticket['tg_chat_id'], ticket['tg_message_id']
        iin = (ticket.get('answers') or {}).get('iin')

    if not changed:
        return True, None

    if chat_id and message_id:
        transport.edit_reply_markup(chat_id, message_id,
                                    telegram.build_keyboard(ticket_id, status))
        if notify_group and status in ('resolved', 'cancelled'):
            transport.send_message(
                chat_id,
                telegram.build_status_notice(ticket_id=ticket_id, status=status,
                                             actor_name=actor_name, iin=iin),
                reply_to_message_id=message_id,
            )
    return True, None
