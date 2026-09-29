# -*- coding: utf-8 -*-
"""Сценарии раздела «Жалобы», где база встречается с Telegram.

Здесь то, что нужно обеим точкам входа — HTTP-роутам и обработчикам бота:
иначе правило «пришёл ответ водителю → оператору уведомление» жило бы в двух
экземплярах и разошлось.

Порядок работы с соединением тот же, что у обращений (crm/service.py):

    короткий курсор (прочитать) → закрыть → сеть → короткий курсор (записать)

Сеть НИКОГДА не вызывается с открытым курсором: пул делят SSE аукциона и
колокола, и держать соединение, пока Telegram думает, — прямой путь его
исчерпать.
"""

import logging
import re
from datetime import date, datetime, time as day_time

from crm import telegram as crm_telegram
from crm import transport

from . import catalog, queries, telegram


class ComplaintError(Exception):
    """Отказ с понятным человеку текстом и HTTP-кодом для роута."""

    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def _mentions(settings):
    return [{'username': name} for name in (settings or {}).get('mention_usernames') or []]


# ─────────────────────────────────────────────────────────────────────────────
# Отправка в группу и правка сообщения
# ─────────────────────────────────────────────────────────────────────────────

def deliver(db, complaint_id):
    """Отправляет жалобу в группу. Возвращает (ok, error).

    Только для жалоб, которые «требуют обработки»: зафиксированная жалоба
    (Яндекс, часть жалоб на парк) в группу не уходит по ТЗ, и для неё здесь
    просто нечего делать. Повтор защищён: ушедшая жалоба второй раз не летит.
    """
    with db._get_cursor() as cursor:
        complaint = queries.get_complaint(cursor, complaint_id)
        settings = queries.settings(cursor)
    if not complaint:
        return False, 'Жалоба не найдена'
    if not complaint['requires_processing']:
        return True, None
    if complaint['delivery_status'] == 'sent' and complaint['tg_message_id']:
        return True, None
    chat_id = settings.get('chat_id')
    if not chat_id:
        error = 'Telegram-группа для жалоб не выбрана'
        with db._get_cursor() as cursor:
            queries.set_delivery(cursor, complaint_id, status='failed', error=error)
        return False, error

    text = telegram.build_root_message(complaint, mentions=_mentions(settings))
    result, error = transport.send_message(chat_id, text,
                                           reply_markup=telegram.main_keyboard(complaint))
    if result is None:
        with db._get_cursor() as cursor:
            queries.set_delivery(cursor, complaint_id, status='failed', error=error)
            queries.add_event(cursor, complaint_id=complaint_id, kind='send_failed',
                              payload={'error': error})
        return False, error

    message_id = result.get('message_id')
    with db._get_cursor() as cursor:
        queries.set_delivery(cursor, complaint_id, status='sent', chat_id=chat_id,
                             chat_title=settings.get('chat_title'), message_id=message_id)
        # Корень нити — строкой переписки: по нему находится жалоба, на которую
        # ответили в группе, одним запросом по (чат, сообщение).
        queries.add_message(cursor, complaint_id=complaint_id, kind='root',
                            author_user_id=complaint['created_by'],
                            author_name=complaint['created_by_name'],
                            tg_chat_id=chat_id, tg_message_id=message_id)
        payload = {'chat': settings.get('chat_title')}
        if settings.get('mention_usernames'):
            payload['mentions'] = ['@%s' % name for name in settings['mention_usernames']]
        queries.add_event(cursor, complaint_id=complaint_id, kind='sent',
                          actor_user_id=complaint['created_by'],
                          actor_name=complaint['created_by_name'], payload=payload)
    return True, None


def refresh_root(db, complaint_id):
    """Переписать сообщение с жалобой: строку состояния и кнопки.

    Итог, работа с сотрудником, закрытие — всё это видно в самом сообщении, а
    не отдельной репликой на каждое действие: в рабочем чате каждая лишняя
    строка — шум. Отказ правки не ломает ничего, кроме красоты, поэтому только
    пишется в лог.
    """
    with db._get_cursor() as cursor:
        complaint = queries.get_complaint(cursor, complaint_id)
        settings = queries.settings(cursor)
    if not complaint or not complaint.get('tg_message_id') or not complaint.get('tg_chat_id'):
        return
    _, error = transport.edit_message(
        complaint['tg_chat_id'], complaint['tg_message_id'],
        text=telegram.build_root_message(complaint, mentions=_mentions(settings)),
        reply_markup=telegram.main_keyboard(complaint))
    if error:
        logging.warning('complaints: сообщение жалобы %s не обновилось: %s', complaint_id, error)


# ─────────────────────────────────────────────────────────────────────────────
# Приём из Telegram
# ─────────────────────────────────────────────────────────────────────────────

def _sender(message):
    from_user = (message.get('from') if isinstance(message, dict)
                 else getattr(message, 'from_user', None))
    if from_user is None:
        return None, {}
    get = from_user.get if isinstance(from_user, dict) else (
        lambda key, default=None: getattr(from_user, key, default))
    return from_user, {'id': get('id'), 'username': get('username'),
                       'name': crm_telegram.sender_name(from_user)}


def ingest_group_reply(db, *, chat_id, reply_to_message_id, message):
    """Реплай в группе → строка переписки жалобы. None — это не наша нить.

    Что это за реплика, решает то, НА ЧТО ответили:
      на приглашение «Ответ водителю»    → ответ водителю (видит оператор)
      на приглашение «Вопрос оператору»  → вопрос оператору (видит оператор)
      на всё остальное                   → внутреннее обсуждение (не видит)
    """
    body = crm_telegram.message_text(message)
    attachment = crm_telegram.extract_attachment(message)
    if not body and not attachment:
        return None
    _user, sender = _sender(message)
    message_id = (message.get('message_id') if isinstance(message, dict)
                  else getattr(message, 'message_id', None))

    with db._get_cursor() as cursor:
        found = queries.find_by_tg_message(cursor, chat_id, reply_to_message_id)
        if not found:
            return None
        complaint_id = found['complaint_id']
        kind = 'internal'
        if found['kind'] == 'prompt':
            kind = 'answer' if found['prompt_kind'] == telegram.PROMPT_ANSWER else 'question'
        row_id = queries.add_message(
            cursor, complaint_id=complaint_id, kind=kind, body=body,
            author_name=sender.get('name'), tg_chat_id=chat_id, tg_message_id=message_id,
            tg_from_id=sender.get('id'), tg_from_name=sender.get('name'),
            tg_username=sender.get('username'), reply_to_tg_message_id=reply_to_message_id,
            attachment=attachment)
        if row_id is None:
            # Повтор апдейта: реплика уже в нити, оператору звонить второй раз
            # не за что.
            return {'complaint_id': complaint_id, 'kind': kind, 'duplicate': True}
        if kind == 'answer':
            queries.mark_answer(cursor, complaint_id)
            queries.notify_author(cursor, complaint_id, queries.UNREAD_ANSWER)
            queries.add_event(cursor, complaint_id=complaint_id, kind='answer',
                              actor_name=sender.get('name'))
        elif kind == 'question':
            queries.set_question_open(cursor, complaint_id, True)
            queries.notify_author(cursor, complaint_id, queries.UNREAD_QUESTION)
            queries.add_event(cursor, complaint_id=complaint_id, kind='question',
                              actor_name=sender.get('name'))
        else:
            queries.touch_activity(cursor, complaint_id)
    return {'complaint_id': complaint_id, 'kind': kind}


def handle_callback(db, *, chat_id, message_id, data, from_user=None):
    """Нажатие кнопки под жалобой. Возвращает текст всплывашки или None, если
    кнопка не наша (у бота есть кнопки других разделов).

    Кнопка действует только на СВОЁ сообщение: callback_data можно подделать, и
    без сверки «эта жалоба лежит в этом сообщении этого чата» нажатие под одной
    жалобой меняло бы другую.
    """
    parsed = telegram.parse_callback(data)
    if not parsed:
        return None
    complaint_id = parsed['complaint_id']
    get = (from_user.get if isinstance(from_user, dict)
           else (lambda key, default=None: getattr(from_user, key, default)))
    sender = {'id': get('id') if from_user is not None else None,
              'username': get('username') if from_user is not None else None,
              'name': crm_telegram.sender_name(from_user) if from_user is not None else None}

    with db._get_cursor() as cursor:
        complaint = queries.get_complaint(cursor, complaint_id)
    if (not complaint or complaint.get('tg_chat_id') is None
            or int(complaint['tg_chat_id']) != int(chat_id)
            or int(complaint.get('tg_message_id') or 0) != int(message_id)):
        return 'Эта кнопка относится к другой жалобе'

    action = parsed['action']
    if complaint['status'] == 'closed':
        transport.edit_message(chat_id, message_id,
                               reply_markup=telegram.main_keyboard(complaint))
        return 'Жалоба уже обработана'

    if action in (telegram.ACTION_ANSWER, telegram.ACTION_QUESTION):
        kind = (telegram.PROMPT_ANSWER if action == telegram.ACTION_ANSWER
                else telegram.PROMPT_QUESTION)
        result, error = transport.send_message(
            chat_id,
            telegram.build_prompt(kind, complaint_id, user_id=sender['id'],
                                  user_name=sender['name']),
            reply_to_message_id=message_id,
            reply_markup=telegram.force_reply_markup(kind))
        if result is None:
            logging.warning('complaints: приглашение по жалобе %s не ушло: %s',
                            complaint_id, error)
            return 'Не получилось — попробуйте ещё раз'
        with db._get_cursor() as cursor:
            queries.add_message(cursor, complaint_id=complaint_id, kind='prompt',
                                prompt_kind=kind, author_name=sender['name'],
                                tg_chat_id=chat_id, tg_message_id=result.get('message_id'),
                                tg_from_id=sender['id'], tg_from_name=sender['name'],
                                tg_username=sender['username'],
                                reply_to_tg_message_id=message_id)
        return ('Напишите ответ ответом на сообщение бота' if kind == telegram.PROMPT_ANSWER
                else 'Напишите вопрос ответом на сообщение бота')

    if action == telegram.ACTION_RESULTS:
        transport.edit_message(chat_id, message_id,
                               reply_markup=telegram.results_keyboard(complaint_id))
        return None

    if action == telegram.ACTION_BACK:
        transport.edit_message(chat_id, message_id,
                               reply_markup=telegram.main_keyboard(complaint))
        return None

    # ACTION_SET_RESULT
    code = parsed['value']
    with db._get_cursor() as cursor:
        queries.set_result(cursor, complaint_id, code=code, note=complaint.get('result_note'),
                           actor_id=None, actor_name=sender['name'], via='telegram')
        _, after = queries.recompute_status(cursor, complaint_id)
        queries.add_event(cursor, complaint_id=complaint_id, kind='result',
                          actor_name=sender['name'],
                          payload={'result': code, 'via': 'telegram', 'status': after})
    refresh_root(db, complaint_id)
    return 'Итог записан: %s' % catalog.result_title(code)


# ─────────────────────────────────────────────────────────────────────────────
# Из iCORE в группу
# ─────────────────────────────────────────────────────────────────────────────

def post_operator_message(db, complaint_id, body, *, author_user_id, author_name,
                          attachment=None):
    """Ответ оператора на вопрос группы (или дополнение) — реплаем в нить.

    Открыт вопрос — отвечаем на него: в группе ответ встаёт под вопросом, а не
    под исходной жалобой, и понятно, на что он. Вопроса нет — под жалобой.
    """
    with db._get_cursor() as cursor:
        complaint = queries.get_complaint(cursor, complaint_id)
        question = (queries.open_question_message(cursor, complaint_id)
                    if complaint and complaint.get('question_open_at') else None)
    if not complaint:
        return False, 'Жалоба не найдена'
    if not complaint.get('tg_message_id') or not complaint.get('tg_chat_id'):
        return False, 'Жалоба не отправлена в группу'
    chat_id = complaint['tg_chat_id']
    reply_to = (question or {}).get('tg_message_id') or complaint['tg_message_id']

    result, error = transport.send_message(
        chat_id,
        telegram.build_operator_reply(complaint_id=complaint_id, author_name=author_name,
                                      body=body, driver_name=complaint.get('driver_name')),
        reply_to_message_id=reply_to)
    if result is None:
        return False, error
    with db._get_cursor() as cursor:
        queries.add_message(cursor, complaint_id=complaint_id, kind='operator_reply',
                            body=body, author_user_id=author_user_id,
                            author_name=author_name, tg_chat_id=chat_id,
                            tg_message_id=result.get('message_id'),
                            reply_to_tg_message_id=reply_to)
        queries.set_question_open(cursor, complaint_id, False)
        queries.add_event(cursor, complaint_id=complaint_id, kind='operator_reply',
                          actor_user_id=author_user_id, actor_name=author_name)

    if attachment is not None:
        sent, attach_error = transport.send_attachment(
            chat_id, file_name=attachment.get('filename') or 'attachment',
            stream=attachment.get('stream'), mimetype=attachment.get('mimetype'),
            reply_to_message_id=result.get('message_id'),
            caption='📎 Вложение к жалобе %s' % telegram.complaint_number(complaint_id))
        if sent is None:
            logging.warning('complaints: вложение к жалобе %s не ушло: %s',
                            complaint_id, attach_error)
        else:
            photo = sent.get('photo')
            file_id = (photo[-1].get('file_id') if isinstance(photo, list) and photo
                       else (sent.get('document') or {}).get('file_id'))
            with db._get_cursor() as cursor:
                queries.add_message(
                    cursor, complaint_id=complaint_id, kind='operator_reply',
                    author_user_id=author_user_id, author_name=author_name,
                    tg_chat_id=chat_id, tg_message_id=sent.get('message_id'),
                    reply_to_tg_message_id=result.get('message_id'),
                    attachment={'kind': 'photo' if photo else 'document', 'file_id': file_id,
                                'name': attachment.get('filename'),
                                'mime': attachment.get('mimetype')})
    return True, None


# ─────────────────────────────────────────────────────────────────────────────
# Разбор в iCORE: сотрудник, итог, работа
# ─────────────────────────────────────────────────────────────────────────────

def resolve_employee(cursor, target_code, employee_id):
    """Сотрудник жалобы → поля для записи. Бросает ComplaintError, если он
    не подходит этой цели: жалоба на КЦ — сотрудник подразделения КЦ,
    на фронт-офис — сотрудник фронт-офиса."""
    person = queries.employee(cursor, employee_id)
    if not person:
        raise ComplaintError('Сотрудник не найден', 404)
    if str(person.get('status') or '') in ('fired', 'dismissal'):
        raise ComplaintError('Сотрудник уволен — выберите работающего')
    department = queries.department_by_id(cursor, person.get('department_id'))
    code = (department or {}).get('code')
    if target_code == catalog.TARGET_CALL_CENTER:
        if code not in catalog.CALL_CENTER_DEPARTMENT_CODES:
            raise ComplaintError('Сотрудник не из подразделения колл-центра')
    elif target_code == catalog.TARGET_FRONT_OFFICE:
        if code != catalog.FRONT_OFFICE_DEPARTMENT_CODE:
            raise ComplaintError('Сотрудник не из фронт-офиса')
    return person, department


def set_employee(db, complaint_id, employee_id, *, ctx):
    """Проставить или поправить сотрудника. None — «сотрудник не определён».

    У жалобы на КЦ подразделение следует за сотрудником: оператор мог выбрать
    не тот отдел, и супервайзер, найдя настоящего сотрудника, поправляет оба
    ответа разом — иначе жалоба висела бы в чужом отделе.
    """
    with db._get_cursor() as cursor:
        complaint = queries.get_complaint(cursor, complaint_id)
        if not complaint:
            raise ComplaintError('Жалоба не найдена', 404)
        if (complaint.get('employee_id') or None) == (employee_id or None):
            return complaint
        target_code = complaint['target']
        unit = {'unit_kind': complaint.get('unit_kind'), 'unit_id': complaint.get('unit_id'),
                'unit_name': complaint.get('unit_name')}
        target_department_id = complaint.get('target_department_id')
        name = responsible = None
        if employee_id:
            person, department = resolve_employee(cursor, target_code, employee_id)
            if int(person['id']) == int(ctx['user_id']):
                raise ComplaintError('Нельзя разбирать жалобу на самого себя', 403)
            name = person['name']
            responsible = person.get('responsible_id')
            target_department_id = person.get('department_id')
            if target_code == catalog.TARGET_CALL_CENTER and department:
                unit = {'unit_kind': catalog.UNIT_DEPARTMENT, 'unit_id': department['id'],
                        'unit_name': catalog.department_label(department['code'],
                                                              department['name'])}
        queries.set_employee(
            cursor, complaint_id, employee_id=employee_id or None, employee_name=name,
            source='supervisor', actor_id=ctx['user_id'], actor_name=ctx.get('name'),
            responsible_id=responsible, target_department_id=target_department_id,
            work_state=catalog.work_state_for(target_code, employee_id, False), **unit)
        _, after = queries.recompute_status(cursor, complaint_id)
        queries.add_event(cursor, complaint_id=complaint_id, kind='employee',
                          actor_user_id=ctx['user_id'], actor_name=ctx.get('name'),
                          payload={'from': complaint.get('employee_name'), 'to': name,
                                   'status': after})
    refresh_root(db, complaint_id)
    with db._get_cursor() as cursor:
        return queries.get_complaint(cursor, complaint_id, ctx['user_id'])


def set_result(db, complaint_id, code, note, *, ctx):
    if code not in catalog.RESULT_BY_CODE:
        raise ComplaintError('Выберите итог проверки')
    note = str(note or '').strip()[:4000] or None
    with db._get_cursor() as cursor:
        if not queries.get_complaint(cursor, complaint_id):
            raise ComplaintError('Жалоба не найдена', 404)
        queries.set_result(cursor, complaint_id, code=code, note=note,
                           actor_id=ctx['user_id'], actor_name=ctx.get('name'), via='icore')
        _, after = queries.recompute_status(cursor, complaint_id)
        queries.add_event(cursor, complaint_id=complaint_id, kind='result',
                          actor_user_id=ctx['user_id'], actor_name=ctx.get('name'),
                          payload={'result': code, 'via': 'icore', 'status': after})
    refresh_root(db, complaint_id)
    with db._get_cursor() as cursor:
        return queries.get_complaint(cursor, complaint_id, ctx['user_id'])


_TIME_RE = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')


def _parse_training(raw, default_reason):
    """Дата, время и вид занятия для записи в «Тренингах». ComplaintError — если
    не хватает чего-то, без чего запись в журнал не ляжет."""
    raw = raw or {}
    try:
        day = date.fromisoformat(str(raw.get('date') or '').strip())
    except ValueError:
        raise ComplaintError('Укажите дату занятия')
    start, end = (str(raw.get(key) or '').strip() for key in ('start', 'end'))
    if not _TIME_RE.match(start) or not _TIME_RE.match(end):
        raise ComplaintError('Укажите время начала и окончания занятия')
    start_time = day_time(*map(int, start.split(':')))
    end_time = day_time(*map(int, end.split(':')))
    if end_time <= start_time:
        raise ComplaintError('Время окончания должно быть позже начала')
    if day > datetime.now().date():
        raise ComplaintError('Занятие ещё не прошло — запишите его после проведения')
    reason = str(raw.get('reason') or default_reason or '').strip()
    if reason not in catalog.TRAINING_REASONS:
        raise ComplaintError('Выберите вид занятия')
    return day, start_time, end_time, reason


def training_comment(complaint, *, action, comment, outcome, need_training):
    """Текст записи в «Тренингах». ТЗ перечисляет, что там должно быть видно:
    что причиной была жалоба, её номер и тема, какая обратная связь проведена,
    проводилось ли доп. обучение и результат. Сотрудник, дата и кто провёл —
    это поля самой записи, в текст они не дублируются."""
    lines = ['Жалоба %s · %s · %s' % (
        telegram.complaint_number(complaint['id']),
        catalog.target_title(complaint['target']),
        catalog.reason_title(complaint['target'], complaint['reason_code']))]
    label = 'Обратная связь' if action == catalog.ACTION_FEEDBACK else 'Тренинг'
    lines.append('%s: %s' % (label, comment))
    if action == catalog.ACTION_TRAINING:
        lines.append('Дополнительное обучение: проведено')
    else:
        lines.append('Дополнительное обучение: %s'
                     % ('назначено' if need_training else 'не требуется'))
    if outcome:
        lines.append('Результат: %s' % outcome)
    return '\n'.join(lines)


def record_work(db, complaint_id, *, action, comment, outcome=None, need_training=False,
                training=None, ctx):
    """Запись о работе с сотрудником. ОС и тренинг ложатся ещё и в «Тренинги».

    Всё — одной транзакцией: запись в журнале жалобы без записи в тренингах
    (или наоборот) означала бы, что ТЗ-шная цепочка «жалоба → ОС → тренинг»
    рвётся ровно там, где её потом будут проверять.
    """
    spec = catalog.WORK_ACTION_BY_CODE.get(str(action or ''))
    if not spec:
        raise ComplaintError('Выберите, что сделано')
    comment = str(comment or '').strip()[:4000]
    outcome = str(outcome or '').strip()[:2000] or None
    if not comment:
        raise ComplaintError('Опишите, что сделано')
    need_training = bool(need_training) and spec['ask_training']

    with db._get_cursor() as cursor:
        complaint = queries.get_complaint(cursor, complaint_id)
        if not complaint:
            raise ComplaintError('Жалоба не найдена', 404)
        employee_id = complaint.get('employee_id')
        if not employee_id:
            raise ComplaintError('Сначала определите сотрудника')
        was_done = complaint.get('work_state') == catalog.WORK_DONE
        training_id = None
        if spec['training']:
            day, start, end, reason = _parse_training(training, spec.get('default_reason'))
            if queries.training_slot_taken(cursor, employee_id, day, start, end):
                raise ComplaintError('На это время у сотрудника уже есть тренинг', 409)
            training_id = queries.create_training(
                cursor, operator_id=employee_id, day=day, start=start, end=end,
                reason=reason, created_by=ctx['user_id'],
                comment=training_comment(complaint, action=action, comment=comment,
                                         outcome=outcome, need_training=need_training))
        flags = catalog.next_work_flags(complaint, action, need_training)
        queries.add_work_record(cursor, complaint_id=complaint_id, action=action,
                                comment=comment, outcome=outcome, need_training=need_training,
                                training_id=training_id, employee_id=employee_id,
                                actor_id=ctx['user_id'], actor_name=ctx.get('name'))
        queries.set_work_flags(cursor, complaint_id, flags=flags, actor_id=ctx['user_id'],
                               actor_name=ctx.get('name'))
        _, after = queries.recompute_status(cursor, complaint_id)
        queries.add_event(cursor, complaint_id=complaint_id, kind='work',
                          actor_user_id=ctx['user_id'], actor_name=ctx.get('name'),
                          payload={'action': action, 'training_id': training_id,
                                   'closed': flags['closed'], 'status': after})

    # «Результат внутренней отработки можно автоматически отправлять в
    # Telegram-группу, чтобы руководители видели, что работа с сотрудником
    # действительно проведена» — один раз, когда работа завершилась, и только
    # фактами, без деталей ОС.
    if flags['closed'] and not was_done and complaint.get('tg_message_id'):
        summary = catalog.work_summary(feedback_done=flags['feedback_done'],
                                       training_done=flags['training_done'],
                                       complaint_closed=after == 'closed')
        result, error = transport.send_message(
            complaint['tg_chat_id'], telegram.build_work_notice(complaint_id, summary),
            reply_to_message_id=complaint['tg_message_id'])
        if result is None:
            logging.warning('complaints: отбивка о работе по жалобе %s не ушла: %s',
                            complaint_id, error)
        else:
            with db._get_cursor() as cursor:
                queries.add_message(cursor, complaint_id=complaint_id, kind='notice',
                                    body=summary, tg_chat_id=complaint['tg_chat_id'],
                                    tg_message_id=result.get('message_id'),
                                    reply_to_tg_message_id=complaint['tg_message_id'])
    refresh_root(db, complaint_id)
    with db._get_cursor() as cursor:
        return queries.get_complaint(cursor, complaint_id, ctx['user_id'])
