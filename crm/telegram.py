"""Как обращение выглядит в Telegram-группе.

Чистые функции без сети: сборка текста и клавиатуры отделена от отправки, чтобы
формат проверялся тестами, а не глазами в рабочем чате (tests/test_crm_telegram.py).

Формат сообщения решает две задачи разом:

1. Сотрудник в группе должен с одного взгляда понять, что от него хотят и
   насколько это срочно.
2. Ответ обязан вернуться в систему. Поэтому в шапке стоит номер обращения, а
   внизу — прямая инструкция ответить реплаем: бот в группе видит только те
   сообщения, что адресованы ему (штатный privacy mode Telegram), и «ответ
   отдельным сообщением в чат» до системы просто не дойдёт.
"""

import html

PRIORITY_LABELS = {
    'low': 'Низкий',
    'normal': 'Обычный',
    'high': 'Высокий',
    'critical': 'Критический',
}

# Цвет — только там, где он несёт смысл. Обычный приоритет не выделяем ничем:
# в группе, куда падают десятки обращений, крашеным должно быть исключение.
PRIORITY_EMOJI = {
    'low': '',
    'normal': '',
    'high': '🟠',
    'critical': '🔴',
}

STATUS_LABELS = {
    'open': 'Новое',
    'in_progress': 'В работе',
    'answered': 'Есть ответ',
    'resolved': 'Решено',
    'cancelled': 'Отменено',
}

# Пределы Telegram: 4096 символов на сообщение и 1024 на подпись к медиа.
# Режем с запасом — HTML-теги тоже считаются.
MESSAGE_LIMIT = 4000

CALLBACK_PREFIX = 'crm'
CALLBACK_TAKE = 'crm:work:'
CALLBACK_DONE = 'crm:done:'


def ticket_number(ticket_id):
    """Номер обращения в человеческом виде. Одно место правды на весь раздел."""
    return '№%d' % int(ticket_id)


def parse_callback(data):
    """('work'|'done', ticket_id) из callback_data кнопки, либо None.

    Отдельной функцией, потому что разбор чужого ввода — самое подходящее место
    для тихой ошибки: кнопка из старого сообщения, обрезанные данные, чужой
    префикс. Всё это должно давать None, а не исключение в обработчике бота.
    """
    text = str(data or '')
    for prefix, action in ((CALLBACK_TAKE, 'work'), (CALLBACK_DONE, 'done')):
        if text.startswith(prefix):
            raw = text[len(prefix):]
            try:
                return action, int(raw)
            except (TypeError, ValueError):
                return None
    return None


def _clip(text, limit):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def build_ticket_message(*, ticket_id, subject, body, queue_title, topic_title=None,
                         priority='normal', author_name=None, department_name=None,
                         client_name=None, client_phone=None, created_text=None,
                         due_text=None):
    """Текст исходного сообщения обращения (HTML-разметка Telegram)."""
    number = ticket_number(ticket_id)
    lines = ['🎫 <b>Обращение %s</b> · %s' % (number, html.escape(str(queue_title or '')))]

    meta = []
    if topic_title:
        meta.append('🗂 <b>Тема:</b> %s' % html.escape(str(topic_title)))
    emoji = PRIORITY_EMOJI.get(priority, '')
    if emoji:
        meta.append('%s <b>Приоритет:</b> %s' % (emoji, PRIORITY_LABELS.get(priority, priority)))
    if due_text:
        meta.append('⏳ <b>Ответ нужен до:</b> %s' % html.escape(str(due_text)))
    if meta:
        lines.append('')
        lines.extend(meta)

    lines.append('')
    lines.append('<b>%s</b>' % html.escape(_clip(subject, 300)))
    if body:
        lines.append('')
        lines.append(html.escape(_clip(body, 2500)))

    client = ' · '.join([p for p in (client_name, client_phone) if p])
    if client:
        lines.append('')
        lines.append('👤 <b>Клиент:</b> %s' % html.escape(client))

    signature = ' · '.join([p for p in (author_name, department_name, created_text) if p])
    if signature:
        lines.append('')
        lines.append('<i>🙍 Обратился: %s</i>' % html.escape(signature))

    # Инструкция последней строкой и без неё нельзя: без реплая ответ не
    # свяжется с обращением, а сотрудник об этом знать не обязан.
    lines.append('')
    lines.append('<i>↩️ Ответьте на это сообщение — ответ вернётся оператору в iCORE.</i>')

    return _clip('\n'.join(lines), MESSAGE_LIMIT)


def build_reply_message(*, ticket_id, author_name, body):
    """Сообщение оператора в уже открытую нить (уходит реплаем к исходному)."""
    lines = ['💬 <b>Уточнение по обращению %s</b>' % ticket_number(ticket_id)]
    lines.append('')
    lines.append(html.escape(_clip(body, 3000)))
    if author_name:
        lines.append('')
        lines.append('<i>🙍 %s</i>' % html.escape(str(author_name)))
    return _clip('\n'.join(lines), MESSAGE_LIMIT)


def build_status_notice(*, ticket_id, status, actor_name=None):
    """Короткая отбивка в группу о том, что обращение закрыли из системы."""
    label = STATUS_LABELS.get(status, status)
    text = '✅ Обращение %s — %s' % (ticket_number(ticket_id), label.lower())
    if actor_name:
        text += ' (%s)' % actor_name
    return text


def build_keyboard(ticket_id, status='open'):
    """Кнопки под сообщением обращения — как inline_keyboard для Telegram API.

    Возвращаем готовую структуру (а не объект aiogram): сообщение отправляется
    обычным HTTP-запросом, тем же путём, что и заявки в IT.

    У решённого обращения кнопок нет вовсе: нажимать больше нечего, а «мёртвая»
    кнопка под сообщением — источник лишних кликов и недоумения.
    """
    if status in ('resolved', 'cancelled'):
        return None
    buttons = []
    if status == 'open':
        buttons.append({'text': '👀 Беру в работу', 'callback_data': CALLBACK_TAKE + str(int(ticket_id))})
    buttons.append({'text': '✅ Выполнено', 'callback_data': CALLBACK_DONE + str(int(ticket_id))})
    return {'inline_keyboard': [buttons]}


def sender_name(from_user):
    """Имя автора ответа из Telegram: ФИО, иначе @username, иначе id.

    Берём объект aiogram/словарь — обработчик бота и веб-хук отдают разное.
    """
    if from_user is None:
        return None
    get = from_user.get if isinstance(from_user, dict) else lambda key, default=None: getattr(from_user, key, default)
    parts = [get('first_name'), get('last_name')]
    name = ' '.join([str(p) for p in parts if p]).strip()
    if name:
        return name
    username = get('username')
    if username:
        return '@%s' % username
    user_id = get('id')
    return 'Telegram %s' % user_id if user_id else None


# Что за вложение прислали в ответе. Порядок важен: у видеосообщения есть и
# video_note, и (иногда) thumbnail, поэтому специфичное проверяется раньше.
_ATTACHMENT_FIELDS = ('photo', 'video_note', 'voice', 'audio', 'video', 'animation',
                      'sticker', 'document')


def extract_attachment(message):
    """Вложение ответа: {kind, file_id, name, mime, size} либо None.

    Файл НЕ скачиваем: Telegram хранит его сам и отдаёт по file_id. Раздел
    показывает вложение через прокси, который просит файл в момент открытия —
    так переписка не тянет за собой гигабайты в наше хранилище.
    """
    get = message.get if isinstance(message, dict) else lambda key, default=None: getattr(message, key, default)
    for kind in _ATTACHMENT_FIELDS:
        value = get(kind)
        if not value:
            continue
        # Фото приходит списком размеров — берём самый крупный (он последний).
        item = value[-1] if isinstance(value, (list, tuple)) and value else value
        item_get = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
        file_id = item_get('file_id')
        if not file_id:
            continue
        return {
            'kind': kind,
            'file_id': file_id,
            'name': item_get('file_name') or None,
            'mime': item_get('mime_type') or None,
            'size': item_get('file_size') or None,
        }
    return None


def message_text(message):
    """Текст ответа: обычный текст либо подпись к вложению."""
    get = message.get if isinstance(message, dict) else lambda key, default=None: getattr(message, key, default)
    return get('text') or get('caption') or None
