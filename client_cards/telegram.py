# -*- coding: utf-8 -*-
"""Как запрос и алерт выглядят в Telegram. Чистые функции без сети.

Запрос в группе другого отдела решает ту же задачу, что обращение СЗоВ
(crm/telegram.py): сотрудник с одного взгляда понимает, о ком речь и что от него
нужно, а его ответ реплаем возвращается в карточку. Поэтому и правила те же: номер —
ссылкой в портал, строки-инструкции «ответьте на это сообщение» нет, расписка за
принятый ответ — реакцией, а не репликой в чат.

Чего нет у обращений: человека, по которому спрашивают, здесь называет не оператор,
а аккаунт Тез — вид, ID и ссылка в админку стоят в сообщении всегда: сотрудник
другого отдела работает в той же админке и открывает аккаунт одним нажатием.
"""

import html

from crm import telegram as crm_telegram

from . import rules

MESSAGE_LIMIT = crm_telegram.MESSAGE_LIMIT
REPLY_REACTION = crm_telegram.REPLY_REACTION

# Общие помощники разбора входящего сообщения — те же, что у «Обращений»: формат
# апдейта Telegram один, и второй разборщик однажды разошёлся бы с первым.
message_text = crm_telegram.message_text
sender_name = crm_telegram.sender_name
extract_attachment = crm_telegram.extract_attachment
mentions_line = crm_telegram.mentions_line


def _clip(text, limit):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def request_number(request_id):
    return '№%d' % int(request_id)


def request_link(request_id):
    """Прямая ссылка на запрос в портале: ?view=client_cards&cc_request=N (читает раздел,
    ClientCardsView.jsx). Метка своя, не request: ту носит «Оплата счетов», и портал стирает
    её из адреса любого другого раздела."""
    base = crm_telegram.WEB_APP_BASE_URL
    number = int(request_id or 0)
    if number <= 0 or not base:
        return ''
    return '%s?view=client_cards&cc_request=%d' % (base, number)


def report_link():
    base = crm_telegram.WEB_APP_BASE_URL
    return '%s?view=client_cards&cc_tab=report' % base if base else ''


def account_line(account, admin_url=rules.DEFAULT_ADMIN_URL):
    """«Водитель: Иванов Иван · ID 12345 · Алматы» — ID ссылкой в админку Тез."""
    if not account:
        return None
    label = rules.KIND_LABELS.get(account.get('kind'), 'Аккаунт')
    parts = []
    name = ' '.join(str(account.get('full_name') or '').split())
    if name:
        parts.append(html.escape(name))
    url = rules.account_url(account.get('kind'), account.get('tez_id'), admin_url)
    id_text = 'ID %s' % account.get('tez_id')
    parts.append('<a href="%s">%s</a>' % (html.escape(url, quote=True), id_text) if url else id_text)
    city = str(account.get('city') or '').strip()
    if city:
        parts.append(html.escape(city))
    return '<b>%s:</b> %s' % (label, ' · '.join(parts))


def build_request_message(*, request_id, destination_title, body, account=None, phone=None,
                          topic_title=None, author_name=None, mentions=None,
                          admin_url=rules.DEFAULT_ADMIN_URL):
    """Исходное сообщение запроса в группу отдела (HTML Telegram).

    Первой строкой — о чём запрос (причина обращения), второй — номер ссылкой и кому
    он адресован. Дальше человек, по которому спрашивают, и текст оператора. Оператор
    и теги ответственных — последними строками и вне обрезки: у длинного текста
    обрезался бы хвост, и запрос ушёл бы без уведомления тому, кому адресован.
    """
    number = request_number(request_id)
    link = request_link(request_id)
    title = ('<a href="%s">Запрос %s</a>' % (html.escape(link, quote=True), number)
             if link else 'Запрос %s' % number)
    lines = []
    if topic_title:
        lines.append('📨 <b>%s</b>' % html.escape(_clip(topic_title, 300)))
        lines.append('%s · %s' % (title, html.escape(str(destination_title or ''))))
    else:
        lines.append('📨 %s · %s' % (title, html.escape(str(destination_title or ''))))

    person = [line for line in (
        account_line(account, admin_url),
        '<b>Телефон:</b> %s' % html.escape(rules.format_phone(phone)) if phone else None,
    ) if line]
    if person:
        lines.append('')
        lines.extend(person)

    if body:
        lines.append('')
        lines.append(html.escape(_clip(body, rules.REQUEST_BODY_LIMIT)))

    tail = [line for line in (crm_telegram.author_line(author_name), mentions_line(mentions)) if line]
    text = '\n'.join(lines)
    if not tail:
        return _clip(text, MESSAGE_LIMIT)
    tail_text = '\n'.join(tail)
    return '%s\n\n%s' % (_clip(text, MESSAGE_LIMIT - len(tail_text) - 2), tail_text)


def build_reply_message(*, request_id, author_name, body):
    """Сообщение оператора в открытую нить (уходит реплаем на сообщение группы)."""
    lines = ['💬 <b>Уточнение по запросу %s</b>' % request_number(request_id), '',
             html.escape(_clip(body, 3000))]
    if author_name:
        lines.append('')
        lines.append('<i>🙍 %s</i>' % html.escape(str(author_name)))
    return _clip('\n'.join(lines), MESSAGE_LIMIT)


def build_status_notice(*, request_id, status, actor_name=None):
    """Короткая отбивка в группу: запрос закрыли из портала — разбираться больше не нужно."""
    label = rules.REQUEST_STATUS_LABELS.get(status, status)
    text = '✅ Запрос %s — %s' % (request_number(request_id), label.lower())
    if actor_name:
        text += ' (%s)' % actor_name
    return text


def file_caption(request_id):
    return '📎 Вложение к запросу %s' % request_number(request_id)


# ─── Алерт о росте обращений ─────────────────────────────────────────────────

_ALERT_HEAD = {
    rules.ALERT_NEW: 'Рост обращений',
    rules.ALERT_GROWING: 'Обращений становится больше',
    rules.ALERT_STILL: 'Обращений по-прежнему много',
}


def _window_text(window_min):
    minutes = int(window_min)
    if minutes % 60 == 0:
        hours = minutes // 60
        if hours == 1:
            return 'за последний час'
        return 'за последние %d ч' % hours
    return 'за последние %d мин' % minutes


def _usual_text(baseline):
    """«обычно около 3» — без ложной точности; меньше одного — «обычно единичные»."""
    value = float(baseline or 0)
    if value < 0.5:
        return 'обычно таких обращений нет'
    if value < 1.5:
        return 'обычно около одного'
    return 'обычно около %d' % int(round(value))


def _people(count):
    count = int(count)
    tail, tens = count % 10, count % 100
    if tail == 1 and tens != 11:
        return '%d человек' % count
    if 2 <= tail <= 4 and not 12 <= tens <= 14:
        return '%d человека' % count
    return '%d человек' % count


def build_alert_message(*, department_name, items, window_min):
    """Одно сообщение руководителю обо всех причинах, по которым обращений стало больше.

    items — [{'kind', 'title', 'parent_title', 'count', 'baseline', 'cities', 'subtopics'}];
    parent_title — путь до причины по дереву («Запрос → Проблема с заказом»), subtopics —
    её следующий уровень.
    Несколько причин — одним сообщением: общий сбой поднимает сразу несколько, и
    пять сообщений подряд об одном и том же — тот самый спам, которого быть не должно.
    """
    kinds = {item['kind'] for item in items}
    head = _ALERT_HEAD[next(iter(kinds))] if len(kinds) == 1 else _ALERT_HEAD[rules.ALERT_NEW]
    lines = ['⚠️ <b>%s</b> · %s' % (head, html.escape(str(department_name or '')))
             if department_name else '⚠️ <b>%s</b>' % head]
    window = _window_text(window_min)
    for item in items:
        path = item['title'] if not item.get('parent_title') else '%s → %s' % (item['parent_title'], item['title'])
        lines.append('')
        lines.append('<b>%s</b>' % html.escape(_clip(path, 300)))
        lines.append('%s %s, %s' % (_people(item['count']), window, _usual_text(item['baseline'])))
        subtopics = item.get('subtopics') or []
        if subtopics:
            lines.append('Подтемы: ' + ', '.join(
                '%s — %d' % (html.escape(_clip(sub['title'], 80)), sub['count']) for sub in subtopics))
        cities = [city for city in (item.get('cities') or []) if city.get('city')]
        if cities:
            lines.append('Города: ' + ', '.join(
                '%s — %d' % (html.escape(_clip(city['city'], 60)), city['count']) for city in cities))
    link = report_link()
    if link:
        lines.append('')
        lines.append('<a href="%s">Открыть отчёт</a>' % html.escape(link, quote=True))
    return _clip('\n'.join(lines), MESSAGE_LIMIT)
