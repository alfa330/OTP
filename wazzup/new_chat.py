"""Новый чат по номеру — кнопка «+» раздела «Чаты ОП» (владелец, 20.10.2026).

Как «+» рядом с поиском в окне Wazzup: человек вводит номер, раздел открывает с
ним чат, и первое сообщение уходит обычной отправкой (/api/wazzup/pilot/send с
newChat). Пока сообщения нет, чата нет и у нас: строку wazzup_chats заводит
сама отправка.

Где можно писать первыми — решение владельца: во всех активных каналах, кроме
Global и каналов, которые сами названы «не писать первыми» («2ГИС - не писать
первыми», «Не отправлять сообщения первым»). Правило одно на сервер и экран:
экран получает готовый список каналов в /api/wazzup/pilot (newChatChannelIds).

В канале WABA (transport 'wapi') первое сообщение — только одобренным шаблоном:
обычный текст WhatsApp вне 24-часового окна клиенту не доставит.

Модуль чистый: ни Flask, ни базы.
"""
import re

SEND_TRANSPORTS = ('whatsapp', 'wapi')
MAX_CONTACT_NAME = 100
_NO_FIRST_MESSAGE = re.compile(r'не\s+(?:писать|отправлять)[\s\S]*перв', re.IGNORECASE)
_GLOBAL = re.compile(r'global', re.IGNORECASE)
# Международный номер без «+»: так WhatsApp-чаты лежат и в Wazzup, и у нас.
_PHONE = re.compile(r'[1-9]\d{9,14}')

FIRST_MESSAGE_TEMPLATE_ONLY = ('В этом канале первое сообщение — только одобренным шаблоном WABA: '
                               'нажмите «/» и выберите шаблон.')


def may_start_chat(channel, excluded_channels=()):
    """Можно ли в канале начать переписку первыми (channel — строка /v3/channels)."""
    if not isinstance(channel, dict):
        return False
    channel_id = channel.get('channelId')
    name = str(channel.get('name') or '')
    return (isinstance(channel_id, str) and bool(channel_id)
            and channel_id not in excluded_channels
            and channel.get('state') == 'active'
            and channel.get('transport') in SEND_TRANSPORTS
            and not _GLOBAL.search(name)
            and not _NO_FIRST_MESSAGE.search(name))


def new_chat_channel_ids(channels, excluded_channels=(), allowed=None):
    """id каналов, где можно начать чат. allowed — каналы, открытые человеку
    (None — все)."""
    return sorted(channel['channelId'] for channel in channels or ()
                  if may_start_chat(channel, excluded_channels)
                  and (allowed is None or channel['channelId'] in allowed))


def normalize_phone(value):
    """Номер нового чата цифрами без «+»; 8XXXXXXXXXX → 7XXXXXXXXXX. '' — не номер."""
    if not isinstance(value, str):
        return ''
    digits = re.sub(r'\D+', '', value)
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    return digits if _PHONE.fullmatch(digits) else ''


def clean_contact_name(value):
    """Имя контакта из формы: пробелы схлопнуты, длина ограничена. None — пусто."""
    if not isinstance(value, str):
        return None
    name = ' '.join(value.split())[:MAX_CONTACT_NAME]
    return name or None


def first_message_error(channel, text, attachment_id=None, reply_to=None):
    """Ошибка для человека или None: чем нельзя начинать новый чат."""
    if attachment_id or reply_to:
        return 'Новый чат начинается с текста. Файл и ответ на сообщение — после первого сообщения.'
    if channel.get('transport') == 'wapi' and not str(text or '').lstrip().lower().startswith('@template:'):
        return FIRST_MESSAGE_TEMPLATE_ONLY
    return None
