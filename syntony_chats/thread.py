"""Сообщение Syntony в форме общей ленты чата (ChatThread) — одна на все разделы.

Ленту рисуют «Чаты водителей» (driver_chats/syntony.py) и проверка качества (qa.py: снимки чатов
для «Журнала оценок» и «ИИ-оценки»). Форма — та же, что у сообщения Chat2Desk: type
(from_client / to_client / autoreply / system / comment), text, created — стенные часы Алматы без
пояса, photo / video / audio / attachments, author у ответа сотрудника, label у ИИ-агента.

Правило одно на оба раздела: слева — только водитель; ответ сотрудника — справа с его именем;
ИИ-агент и всё незнакомое — справа серым «автоответом» с подписью (незнакомый отправитель слева
выдал бы слова парка за слова водителя — та же ловушка, что у Chat2Desk с type: null); автоматика
(строки звонков, напоминания) — строкой по центру; не ушедшее (unsent) в ленту не идёт.
"""

from datetime import timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo('Asia/Almaty')

AI_LABEL = 'ИИ-агент'
SYSTEM_CALL_TEXT = 'Звонок'
DELETED_BY_DRIVER_TEXT = 'Водитель удалил сообщение'
DELETED_TEXT = 'Сообщение удалено'


def local_iso(moment):
    """TIMESTAMPTZ → стенные часы Алматы без пояса — так же, как время ленты Chat2Desk."""
    if moment is None:
        return None
    if isinstance(moment, str):
        return moment
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LOCAL_TZ).replace(tzinfo=None).isoformat()


def attach_media(item, content_type, media, media_url):
    """Вложение → поле ленты: фото, видео, голос или файл с именем.

    media_url(id сообщения) → адрес вложения для ленты (или None — без вложения)."""
    if not isinstance(media, dict) or media_url is None:
        return
    link = media_url(item['id'])
    if not link:
        return
    kind = str(content_type or '').lower()
    mime = str(media.get('mimeType') or '').lower()
    name = str(media.get('fileName') or '').strip() or None
    if kind in ('image', 'sticker') or (kind == 'document' and mime.startswith('image/')):
        item['photo'] = link
    elif kind == 'video':
        item['video'] = link
    elif kind == 'audio':
        item['audio'] = link
    else:
        # Документ — файлом с именем, а не безымянным «PDF»: по имени («техпаспорт.pdf»)
        # понятно, что прислал водитель.
        item['attachments'] = [{'name': name or ('PDF' if mime == 'application/pdf' else 'Файл'),
                                'link': link}]


def message_from_row(row, media_url=None):
    """Строка сообщения → сообщение ленты (как chat2desk.normalize_message) или None (unsent).

    row — (id, conversation_id, direction, sender_type, author_name, content_type, text, media,
    created_at, delete_scope, status, channel_id, …): лишние поля в конце не читаются."""
    (message_id, conversation_id, direction, sender_type, author_name, content_type, text,
     media, created_at, delete_scope, status, channel_id) = row[:12]
    scope = str(delete_scope or '').lower()
    if scope == 'unsent':
        return None
    direction = str(direction or '').lower()
    sender = str(sender_type or '').lower()
    if sender == 'system':
        kind = 'system'
    elif direction == 'inbound' and sender in ('', 'contact'):
        kind = 'from_client'
    elif direction == 'outbound' and sender == 'human':
        kind = 'to_client'
    else:
        kind = 'autoreply'
    item = {
        'id': message_id,
        'type': kind,
        'text': text or '',
        'created': local_iso(created_at),
        'photo': None, 'video': None, 'audio': None, 'pdf': None,
        'attachments': [],
        'status': status,
        'requestId': None,
        'dialogId': conversation_id,
        'channelId': channel_id,
    }
    if kind == 'to_client':
        # Ответ с телефона и через API (waba_api) приходит без автора: лучше без подписи,
        # чем чужим именем.
        item['author'] = author_name or None
    elif kind == 'autoreply':
        item['label'] = AI_LABEL if sender == 'ai' else None
    if scope == 'contact':
        item['text'] = DELETED_BY_DRIVER_TEXT
    elif scope == 'everyone':
        item['text'] = DELETED_TEXT
    else:
        attach_media(item, content_type, media, media_url)
    if kind == 'system' and not item['text'] and str(content_type or '').lower() == 'call':
        item['text'] = SYSTEM_CALL_TEXT
    return item
