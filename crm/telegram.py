"""Как обращение выглядит в Telegram-группе.

Чистые функции без сети: сборка текста и клавиатуры отделена от отправки, чтобы
формат проверялся тестами, а не глазами в рабочем чате (tests/test_crm_telegram.py).

Формат сообщения решает две задачи разом:

1. Сотрудник в группе должен с одного взгляда понять, что от него хотят и
   насколько это срочно.
2. Ответ обязан вернуться в систему. Бот в группе видит только те сообщения,
   что адресованы ему (штатный privacy mode Telegram), поэтому ответ ловится
   ТОЛЬКО реплаем — «ответ отдельным сообщением в чат» до системы не дойдёт.
   Строки-инструкции об этом в сообщении больше нет: владелец убрал её как
   лишнюю (19.08.2026). Сотрудники групп механику уже знают, а под сообщением
   остаются кнопки «Беру в работу» и «Выполнено» — они работают без реплая.
"""

import html
import os

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

# Кнопок «Беру в работу» и «Выполнено» под сообщением больше нет: владелец
# убрал их 19.08.2026 — из группы они выглядели так, будто ничего не делают.
# Нажатие отвечало всплывающей подсказкой на пару секунд и меняло сами кнопки,
# а в чате после него не оставалось ничего. Статус обращения ведут в iCORE.


# Адрес интерфейса для ссылок из группы. Тот же, что у задач: пункт меню и
# карточка живут в одном приложении, и второй переменной под тот же адрес не
# нужно. Отдельная CRM_WEB_APP_BASE_URL оставлена на случай, если раздел когда-то
# переедет.
WEB_APP_BASE_URL = (os.getenv('CRM_WEB_APP_BASE_URL')
                    or os.getenv('TASK_WEB_APP_BASE_URL')
                    or 'https://alfa330.github.io/OTP').strip().rstrip('/')


def ticket_link(ticket_id):
    """Прямая ссылка на карточку обращения в iCORE.

    Ставится на слова «Обращение №N» в сообщении группы: сотруднику из чата
    нужен не раздел, а именно это обращение, и искать его в списке — лишний шаг.
    Параметры те же, что у задач (?view=…&…_id=…), их читает src/App.jsx.
    """
    number = int(ticket_id or 0)
    if number <= 0 or not WEB_APP_BASE_URL:
        return ''
    return '%s?view=crm_tickets&ticket_id=%d' % (WEB_APP_BASE_URL, number)


def ticket_number(ticket_id):
    """Номер обращения в человеческом виде. Одно место правды на весь раздел."""
    return '№%d' % int(ticket_id)


def _clip(text, limit):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def format_body(text):
    """Готовый текст обращения — в разметку Telegram.

    Разметки в самом тексте нет и быть не должно: его собирает crm.scenarios, а
    тот ничего не знает ни про Telegram, ни про HTML — один и тот же текст
    показывается ещё и в карточке обращения. Поэтому подписи выделяются здесь,
    по формату строки: всё до первого «: » — подпись.

    Без этого сообщение читалось одним серым полотном: глазу негде зацепиться,
    и специалист искал ИИН построчно.
    """
    result = []
    for line in str(text or '').split(chr(10)):
        if not line.strip():
            result.append('')
            continue
        head, sep, tail = line.partition(': ')
        # Двоеточие внутри длинной фразы — не подпись, а знак препинания.
        if sep and len(head) <= 64:
            result.append('<b>%s:</b> %s' % (html.escape(head), html.escape(tail)))
        else:
            result.append(html.escape(line))
    return chr(10).join(result)


def build_ticket_message(*, ticket_id, subject, body, queue_title, topic_title=None,
                         priority='normal', client_name=None, client_phone=None,
                         due_text=None, own_wording=False):
    """Текст исходного сообщения обращения (HTML-разметка Telegram).

    own_wording — тематика сформулировала сообщение сама (у группы-получателя
    свой заведённый формат). Тогда тема это ПРОСЬБА обычным текстом, а тело —
    данные, и жирным выделяются они: взгляд должен падать на номер ВУ и город,
    а не на слова «прошу проверить». В остальных тематиках наоборот: тема —
    заголовок, тело — перечень с выделенными подписями.
    """
    number = ticket_number(ticket_id)
    # Номер — ссылка прямо в карточку: сотруднику из чата нужен не раздел, а это
    # обращение. Отдельной строкой «открыть в iCORE» было бы лишнее место.
    link = ticket_link(ticket_id)
    title = ('<a href="%s">Обращение %s</a>' % (html.escape(link, quote=True), number)
             if link else 'Обращение %s' % number)
    lines = ['🎫 <b>%s</b> · %s' % (title, html.escape(str(queue_title or '')))]

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
    clean_subject = html.escape(_clip(subject, 300))
    lines.append(clean_subject if own_wording else '<b>%s</b>' % clean_subject)
    if body:
        lines.append('')
        lines.append('<b>%s</b>' % html.escape(_clip(body, 2500)) if own_wording
                     else format_body(_clip(body, 2500)))

    client = ' · '.join([p for p in (client_name, client_phone) if p])
    if client:
        lines.append('')
        lines.append('👤 <b>Клиент:</b> %s' % html.escape(client))

    # Кто обратился, из какого отдела и когда — в группе лишнее (просьба
    # владельца 19.08.2026). Отвечают не человеку, а обращению; имя, отдел и
    # время видны в карточке, куда ведёт ссылка в шапке.

    return _clip('\n'.join(lines), MESSAGE_LIMIT)


def build_reply_message(*, ticket_id, author_name, body, iin=None):
    """Сообщение оператора в уже открытую нить (уходит реплаем к исходному).

    ИИН стоит в заголовке рядом с номером обращения (просьба СЗоВ 19.08.2026).
    Номер опознаёт обращение для НАС, а специалист в группе работает по
    водителю: без ИИН он на каждое уточнение открывает исходное сообщение и
    ищет его там. Реплай в Telegram при этом сворачивается в одну строку —
    исходное сообщение видно не всегда.
    """
    header = '💬 <b>Уточнение по обращению %s</b>' % ticket_number(ticket_id)
    if iin:
        header += ' · <b>ИИН %s</b>' % html.escape(str(iin).strip())
    lines = [header]
    lines.append('')
    lines.append(html.escape(_clip(body, 3000)))
    if author_name:
        lines.append('')
        lines.append('<i>🙍 %s</i>' % html.escape(str(author_name)))
    return _clip('\n'.join(lines), MESSAGE_LIMIT)


def build_status_notice(*, ticket_id, status, actor_name=None, iin=None):
    """Короткая отбивка в группу о том, что обращение закрыли из системы.

    ИИН здесь по той же причине, что и в уточнении: в группе идут обращения по
    разным водителям, и «Обращение №10 — решено» без ИИН специалисту ничего не
    говорит, пока он не найдёт исходное сообщение.
    """
    label = STATUS_LABELS.get(status, status)
    text = '✅ Обращение %s' % ticket_number(ticket_id)
    if iin:
        text += ' · ИИН %s' % str(iin).strip()
    text += ' — %s' % label.lower()
    if actor_name:
        text += ' (%s)' % actor_name
    return text


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
