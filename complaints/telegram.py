# -*- coding: utf-8 -*-
"""Как жалоба выглядит в Telegram-группе «Жалобы КЦ, регионы, таксопарк».

Чистые функции: текст, кнопки, разбор нажатий. Сеть — в crm.transport, база —
в complaints.queries; здесь только формат, поэтому он проверяется тестами, а
не глазами в рабочем чате (tests/test_complaints_telegram.py).

Главное требование ТЗ к группе — РАЗДЕЛИТЬ два вида переписки:

* внутреннее обсуждение. Сотрудники и руководители отвечают на сообщение,
  обсуждают, пишут внутренние комментарии — и это НЕ уходит оператору;
* ответ для водителя. Только текст, отправленный через отдельное действие, —
  кнопка «Ответ водителю», — поступает оператору в iCORE.

Кнопка сама текст не принимает (у кнопок Telegram нет поля ввода), поэтому она
ставит в группе приглашение «напишите ответ — ответом на это сообщение». Что
пришло ответом на приглашение — ответ водителю; что пришло ответом на всё
остальное — внутреннее обсуждение. Граница проходит по тому, НА КАКОЕ сообщение
ответили, а не по словам или хештегам: ошибиться в ней так же трудно, как
нажать не ту кнопку.

Бот в группе видит только адресованные ему сообщения (privacy mode Telegram),
то есть реплаи на свои сообщения, — ровно ту переписку, которая здесь нужна.
"""

import html

from crm import telegram as crm_telegram

from . import catalog

MESSAGE_LIMIT = crm_telegram.MESSAGE_LIMIT

# Префикс данных кнопок раздела. Короткий: у callback_data предел 64 байта.
CALLBACK_PREFIX = 'cmp'

ACTION_ANSWER = 'a'      # «Ответ водителю» — пригласить написать ответ
ACTION_QUESTION = 'q'    # «Вопрос оператору» — пригласить написать вопрос
ACTION_RESULTS = 'r'     # «Итог проверки» — показать варианты итога
ACTION_SET_RESULT = 's'  # выбран конкретный итог
ACTION_BACK = 'b'        # вернуться к основным кнопкам

PROMPT_ANSWER = 'answer'
PROMPT_QUESTION = 'question'

# Реакция-расписка за принятый ответ или вопрос — та же, что у обращений.
RECEIPT_REACTION = crm_telegram.REPLY_REACTION


def complaint_link(complaint_id):
    """Прямая ссылка на карточку жалобы в iCORE — тем же механизмом, что у
    задач и обращений (?view=…&…_id=…, читает src/App.jsx)."""
    base = crm_telegram.WEB_APP_BASE_URL
    number = int(complaint_id or 0)
    if number <= 0 or not base:
        return ''
    return '%s?view=complaints&complaint_id=%d' % (base, number)


def complaint_number(complaint_id):
    return '№%d' % int(complaint_id)


def _esc(value):
    return html.escape(str(value or ''))


def _clip(text, limit):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _event_text(value, with_time=True):
    """'2026-09-28T14:30:00' → '28.09.2026 14:30'. Не дата — как есть.

    with_time=False — время не называли (complaints.event_time_known): дата без
    него хранится полуночью, и «00:00» в сообщении было бы выдумкой. Настоящая
    полночь при этом показывается честно.
    """
    import re

    found = re.match(r'^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?', str(value or ''))
    if not found:
        return str(value or '')
    day = '%s.%s.%s' % (found.group(3), found.group(2), found.group(1))
    if with_time and found.group(4):
        return '%s %s:%s' % (day, found.group(4), found.group(5))
    return day


def status_lines(complaint):
    """Строки состояния под жалобой. Сообщение бота правится по мере работы:
    по нему в группе видно, чем кончилось, — без отдельной отбивки на каждое
    действие."""
    lines = []
    result = catalog.result_title(complaint.get('result_code'))
    if result:
        lines.append('📌 <b>Итог:</b> %s' % _esc(result))
    state = complaint.get('work_state')
    if state == catalog.WORK_DONE:
        lines.append('👤 <b>Работа с сотрудником:</b> проведена')
    elif state == catalog.WORK_PENDING:
        lines.append('👤 <b>Работа с сотрудником:</b> %s'
                     % ('требуется тренинг' if complaint.get('training_required')
                        else 'ожидает супервайзера'))
    if complaint.get('status') == 'closed':
        lines.append('✅ <b>Жалоба обработана</b>')
    return lines


def build_root_message(complaint, *, mentions=None):
    """Текст сообщения с жалобой (HTML-разметка Telegram).

    «В Telegram должно быть понятно, к какому обращению относится сообщение,
    кто его создал, на что поступила жалоба и какие данные предоставил
    водитель» — ровно эти четыре блока, по порядку.
    """
    number = complaint_number(complaint['id'])
    link = complaint_link(complaint['id'])
    title = ('<a href="%s">Жалоба %s</a>' % (html.escape(link, quote=True), number)
             if link else 'Жалоба %s' % number)
    target_title = catalog.target_title(complaint.get('target'))
    reason_title = catalog.reason_title(complaint.get('target'), complaint.get('reason_code'))

    lines = ['⚠️ <b>%s</b> · %s' % (_esc(reason_title), _esc(target_title)), title]

    # На что жалоба.
    about = []
    if complaint.get('unit_name'):
        about.append('<b>%s:</b> %s' % (_unit_label(complaint), _esc(complaint['unit_name'])))
    if (catalog.target(complaint.get('target')) or {}).get('employee'):
        about.append('<b>Сотрудник:</b> %s' % (_esc(complaint['employee_name'])
                                               if complaint.get('employee_name')
                                               else 'не определён'))
    if about:
        lines.append('')
        lines.extend(about)

    # Данные водителя.
    lines.append('')
    driver = [complaint.get('driver_name'), complaint.get('driver_phone')]
    if complaint.get('driver_ref'):
        driver.append('ID / ВУ %s' % complaint['driver_ref'])
    lines.append('<b>Водитель:</b> %s' % _esc(' · '.join(p for p in driver if p)))
    lines.append('<b>Город:</b> %s' % _esc(complaint.get('city')))
    if complaint.get('event_at'):
        lines.append('<b>Когда:</b> %s' % _esc(_event_text(
            complaint['event_at'], with_time=bool(complaint.get('event_time_known', True)))))
    lines.append('')
    lines.append('<b>Что произошло:</b>')
    lines.append(_esc(_clip(complaint.get('description'), 2500)))

    # Кто принял жалобу.
    author = ' · '.join(p for p in (complaint.get('created_by_name'),
                                    complaint.get('creator_department_name')) if p)
    if author:
        lines.append('')
        lines.append('🙍 <b>Принял:</b> %s' % _esc(author))

    status = status_lines(complaint)
    if status:
        lines.append('')
        lines.extend(status)

    # Теги — последними и вне обрезки: обрезанный хвост означал бы сообщение
    # без уведомления тому, кому оно адресовано.
    tail = crm_telegram.mentions_line(mentions)
    if not tail:
        return _clip('\n'.join(lines), MESSAGE_LIMIT)
    return '%s\n\n%s' % (_clip('\n'.join(lines), MESSAGE_LIMIT - len(tail) - 2), tail)


def _unit_label(complaint):
    kind = complaint.get('unit_kind')
    if kind == catalog.UNIT_DEPARTMENT:
        return 'Подразделение'
    if kind == catalog.UNIT_OFFICE:
        return 'Офис'
    if kind == catalog.UNIT_PARK:
        return 'Таксопарк'
    return 'Подразделение'


def callback_data(action, complaint_id, value=None):
    parts = [CALLBACK_PREFIX, action, str(int(complaint_id))]
    if value:
        parts.append(str(value))
    return ':'.join(parts)


def parse_callback(data):
    """'cmp:s:12:confirmed' → {'action': 's', 'complaint_id': 12, 'value': 'confirmed'}.

    Чужие кнопки (другие разделы бота) и битые данные — None: обработчик раздела
    их не трогает.
    """
    parts = str(data or '').split(':')
    if len(parts) < 3 or parts[0] != CALLBACK_PREFIX:
        return None
    action = parts[1]
    if action not in (ACTION_ANSWER, ACTION_QUESTION, ACTION_RESULTS,
                      ACTION_SET_RESULT, ACTION_BACK):
        return None
    try:
        complaint_id = int(parts[2])
    except ValueError:
        return None
    if complaint_id <= 0:
        return None
    value = parts[3] if len(parts) > 3 else None
    if action == ACTION_SET_RESULT and value not in catalog.RESULT_BY_CODE:
        return None
    return {'action': action, 'complaint_id': complaint_id, 'value': value}


def main_keyboard(complaint):
    """Кнопки под жалобой — одинаковые и у открытой, и у закрытой.

    Раньше закрытая жалоба теряла кнопки, и это обрывало цикл ТЗ: группа
    ставит итог («предоставлено разъяснение») раньше, чем пишет само
    разъяснение для водителя, — и отправить его было уже нечем (сверка с ТЗ
    29.09.2026). Итог тоже можно поправить: проверка иногда уточняется.
    """
    complaint_id = complaint['id']
    result = catalog.result_title(complaint.get('result_code'))
    return {'inline_keyboard': [
        [
            {'text': '💬 Ответ водителю',
             'callback_data': callback_data(ACTION_ANSWER, complaint_id)},
            {'text': '❓ Вопрос оператору',
             'callback_data': callback_data(ACTION_QUESTION, complaint_id)},
        ],
        [
            {'text': ('📌 Итог: %s' % result) if result else '📌 Итог проверки',
             'callback_data': callback_data(ACTION_RESULTS, complaint_id)},
        ],
    ]}


def results_keyboard(complaint_id):
    """Варианты итога — по одному в строке: подписи длинные («Передано
    ответственному подразделению»), и по два в ряд Telegram их обрезал бы."""
    rows = [[{'text': item['title'],
              'callback_data': callback_data(ACTION_SET_RESULT, complaint_id, item['code'])}]
            for item in catalog.RESULTS]
    rows.append([{'text': '‹ Назад', 'callback_data': callback_data(ACTION_BACK, complaint_id)}])
    return {'inline_keyboard': rows}


def build_prompt(kind, complaint_id, *, user_id=None, user_name=None):
    """Приглашение написать ответ или вопрос — ответом на само приглашение.

    Имя нажавшего — упоминанием: так ForceReply (selective) откроет поле ответа
    именно у него, а не у всей группы, и по уведомлению понятно, кому писать.
    """
    number = complaint_number(complaint_id)
    who = ''
    if user_id:
        who = '<a href="tg://user?id=%d">%s</a>, ' % (int(user_id), _esc(user_name or 'коллега'))
    if kind == PROMPT_QUESTION:
        return ('❓ %sнапишите вопрос оператору по жалобе %s — <b>ответом на это '
                'сообщение</b>. Оператор уточнит у водителя и ответит сюда же.'
                % (who, number))
    return ('💬 %sнапишите ответ для водителя по жалобе %s — <b>ответом на это '
            'сообщение</b>. Его получит оператор, который принял жалобу.' % (who, number))


def force_reply_markup(kind):
    return {
        'force_reply': True,
        'selective': True,
        'input_field_placeholder': ('Вопрос оператору' if kind == PROMPT_QUESTION
                                    else 'Ответ для водителя'),
    }


def build_operator_reply(*, complaint_id, author_name, body, driver_name=None):
    """Сообщение оператора из iCORE в группу — ответ на вопрос или дополнение."""
    header = '💬 <b>Ответ оператора по жалобе %s</b>' % complaint_number(complaint_id)
    if driver_name:
        header += ' · %s' % _esc(driver_name)
    lines = [header, '', _esc(_clip(body, 3000))]
    if author_name:
        lines += ['', '<i>🙍 %s</i>' % _esc(author_name)]
    return _clip('\n'.join(lines), MESSAGE_LIMIT)


def build_work_notice(complaint_id, summary):
    """Отбивка о проведённой работе с сотрудником — только факты (ТЗ)."""
    return '✅ Жалоба %s: %s' % (complaint_number(complaint_id), _esc(summary))


def build_result_notice(complaint_id, result_code, actor_name=None):
    text = '📌 Итог по жалобе %s: %s' % (complaint_number(complaint_id),
                                        _esc(catalog.result_title(result_code)))
    if actor_name:
        text += ' (%s)' % _esc(actor_name)
    return text
