# -*- coding: utf-8 -*-
"""История чатов помощника: диалоги, реплики, источники.

Два свойства, которые здесь важнее удобства.

ЧУЖОЙ ЧАТ НЕДОСТУПЕН ПО ПОСТРОЕНИЮ. Во всех запросах стоит
`user_id = %(user_id)s`, и роутов, принимающих чужой user_id, нет вовсе. Это
дешевле любых проверок владения: нет пути, по которому идентификатор чужого
чата что-то открыл бы.

ИСТОЧНИКИ — СНИМОК. При показе истории они НЕ перепроверяются: цитата и путь
заголовков сохранены на момент ответа, а куски к тому времени могли быть
пересобраны (индекс пересоздаёт их целиком). Зато доступность источника СЕЙЧАС
проверяется джойном на периметр: сотрудник, потерявший доступ к статье, видит в
старом ответе пометку «статья недоступна» вместо текста цитаты. Отсутствие
текста в базе от отзыва доступа не защищает — защищает именно джойн.
"""

_CREATE_CHAT = """
INSERT INTO wiki_ai_chats (user_id, title)
VALUES (%(user_id)s, %(title)s)
RETURNING id, title, created_at
"""

_LIST_CHATS = """
SELECT id, title, message_count, last_message_at, created_at
  FROM wiki_ai_chats
 WHERE user_id = %(user_id)s AND deleted_at IS NULL
 ORDER BY coalesce(last_message_at, created_at) DESC, id DESC
 LIMIT %(limit)s OFFSET %(offset)s
"""

_OWNED_CHAT = """
SELECT id, title, message_count FROM wiki_ai_chats
 WHERE id = %(chat_id)s AND user_id = %(user_id)s AND deleted_at IS NULL
"""

_NEXT_SEQ = """
SELECT coalesce(max(seq), 0) + 1 FROM wiki_ai_messages WHERE chat_id = %(chat_id)s
"""

_INSERT_MESSAGE = """
INSERT INTO wiki_ai_messages
       (chat_id, seq, role, kind, text, provider, model, elapsed_ms,
        input_tokens, output_tokens)
VALUES (%(chat_id)s, %(seq)s, %(role)s, %(kind)s, %(text)s, %(provider)s,
        %(model)s, %(elapsed_ms)s, %(input_tokens)s, %(output_tokens)s)
RETURNING id, created_at
"""

_INSERT_SOURCE = """
INSERT INTO wiki_ai_message_sources
       (message_id, ord, article_id, chunk_id, chunk_text_hash, title, slug,
        heading_path, quote, quote_ok, requires_ack)
VALUES (%(message_id)s, %(ord)s, %(article_id)s, %(chunk_id)s,
        %(chunk_text_hash)s, %(title)s, %(slug)s, %(heading_path)s, %(quote)s,
        %(quote_ok)s, %(requires_ack)s)
"""

_TOUCH_CHAT = """
UPDATE wiki_ai_chats
   SET message_count = (SELECT count(*) FROM wiki_ai_messages WHERE chat_id = %(chat_id)s),
       last_message_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
       updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
       title = CASE WHEN title = '' THEN %(title)s ELSE title END
 WHERE id = %(chat_id)s AND user_id = %(user_id)s
"""

_MESSAGES = """
SELECT id, seq, role, kind, text, provider, model, elapsed_ms, feedback, created_at
  FROM wiki_ai_messages
 WHERE chat_id = %(chat_id)s
 ORDER BY seq
"""

# Доступность источника — джойном на периметр, переданный списком id. Так пометка
# «статья недоступна» появляется сразу после отзыва доступа, без переиндексации.
_SOURCES = """
SELECT s.message_id, s.ord, s.article_id, s.title, s.slug, s.heading_path,
       s.quote, s.quote_ok, s.requires_ack,
       (s.article_id = ANY(%(visible)s)) AS available
  FROM wiki_ai_message_sources s
  JOIN wiki_ai_messages m ON m.id = s.message_id
 WHERE m.chat_id = %(chat_id)s
 ORDER BY s.message_id, s.ord
"""

_RENAME = """
UPDATE wiki_ai_chats SET title = %(title)s,
       updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
 WHERE id = %(chat_id)s AND user_id = %(user_id)s AND deleted_at IS NULL
"""

# Мягкое удаление и идемпотентно: повторный вызов тоже успех. Так сделано в
# разборах ИИ после фикса — иначе двойной клик давал пользователю ошибку.
_SOFT_DELETE = """
UPDATE wiki_ai_chats
   SET deleted_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
       deleted_by = %(user_id)s
 WHERE id = %(chat_id)s AND user_id = %(user_id)s AND deleted_at IS NULL
"""

_FEEDBACK = """
UPDATE wiki_ai_messages m
   SET feedback = %(feedback)s
  FROM wiki_ai_chats c
 WHERE m.id = %(message_id)s AND c.id = m.chat_id
   AND c.user_id = %(user_id)s AND c.deleted_at IS NULL
   AND m.role = 'assistant'
"""


def _title_from(question, limit=60):
    text = ' '.join(str(question or '').split())
    return text[:limit] if text else 'Новый вопрос'


def create_chat(cursor, user_id, *, title=''):
    cursor.execute(_CREATE_CHAT, {'user_id': user_id, 'title': title[:255]})
    chat_id, chat_title, created_at = cursor.fetchone()
    return {'id': chat_id, 'title': chat_title, 'message_count': 0,
            'created_at': created_at.isoformat() if created_at else None}


def list_chats(cursor, user_id, *, limit=30, offset=0):
    cursor.execute(_LIST_CHATS, {'user_id': user_id, 'limit': limit,
                                 'offset': offset})
    return [{'id': row[0], 'title': row[1], 'message_count': row[2],
             'last_message_at': row[3].isoformat() if row[3] else None,
             'created_at': row[4].isoformat() if row[4] else None}
            for row in cursor.fetchall()]


def owned_chat(cursor, user_id, chat_id):
    cursor.execute(_OWNED_CHAT, {'user_id': user_id, 'chat_id': chat_id})
    row = cursor.fetchone()
    return None if not row else {'id': row[0], 'title': row[1],
                                 'message_count': row[2]}


def append_message(cursor, chat_id, *, role, text, kind='answer', provider=None,
                   model=None, elapsed_ms=None, input_tokens=None,
                   output_tokens=None, sources=()):
    cursor.execute(_NEXT_SEQ, {'chat_id': chat_id})
    seq = cursor.fetchone()[0]
    cursor.execute(_INSERT_MESSAGE, {
        'chat_id': chat_id, 'seq': seq, 'role': role, 'kind': kind, 'text': text,
        'provider': provider, 'model': model, 'elapsed_ms': elapsed_ms,
        'input_tokens': input_tokens, 'output_tokens': output_tokens})
    message_id, created_at = cursor.fetchone()

    for position, source in enumerate(sources):
        cursor.execute(_INSERT_SOURCE, {
            'message_id': message_id, 'ord': position,
            'article_id': source.get('article_id'),
            'chunk_id': source.get('chunk_id'),
            'chunk_text_hash': source.get('chunk_text_hash'),
            'title': (source.get('title') or '')[:255],
            'slug': (source.get('slug') or '')[:255],
            'heading_path': source.get('heading_path') or '',
            'quote': source.get('quote') or '',
            'quote_ok': bool(source.get('ok')),
            'requires_ack': bool(source.get('requires_ack'))})
    return {'id': message_id, 'seq': seq,
            'created_at': created_at.isoformat() if created_at else None}


def touch_chat(cursor, user_id, chat_id, *, first_question=''):
    cursor.execute(_TOUCH_CHAT, {'chat_id': chat_id, 'user_id': user_id,
                                 'title': _title_from(first_question)})


def chat_messages(cursor, chat_id, *, visible_article_ids=()):
    cursor.execute(_MESSAGES, {'chat_id': chat_id})
    messages = [{'id': row[0], 'seq': row[1], 'role': row[2], 'kind': row[3],
                 'text': row[4], 'provider': row[5], 'model': row[6],
                 'elapsed_ms': row[7], 'feedback': row[8],
                 'created_at': row[9].isoformat() if row[9] else None,
                 'sources': []}
                for row in cursor.fetchall()]
    by_id = {message['id']: message for message in messages}

    cursor.execute(_SOURCES, {'chat_id': chat_id,
                              'visible': sorted(visible_article_ids) or [-1]})
    for row in cursor.fetchall():
        message = by_id.get(row[0])
        if message is None:
            continue
        available = bool(row[9])
        message['sources'].append({
            'ord': row[1], 'article_id': row[2],
            'title': row[3] if available else 'Статья недоступна',
            'slug': row[4] if available else None,
            'heading_path': row[5] if available else '',
            # Цитата закрытой статьи не отдаётся: доступ мог быть отозван после
            # ответа, и снимок не должен становиться лазейкой.
            'quote': row[6] if available else '',
            'quote_ok': bool(row[7]), 'requires_ack': bool(row[8]),
            'available': available})
    return messages


def rename_chat(cursor, user_id, chat_id, title):
    cursor.execute(_RENAME, {'chat_id': chat_id, 'user_id': user_id,
                             'title': (title or '')[:255]})
    return (cursor.rowcount or 0) > 0


def delete_chat(cursor, user_id, chat_id):
    cursor.execute(_SOFT_DELETE, {'chat_id': chat_id, 'user_id': user_id})
    return (cursor.rowcount or 0) > 0


def set_feedback(cursor, user_id, message_id, feedback):
    cursor.execute(_FEEDBACK, {'message_id': message_id, 'user_id': user_id,
                               'feedback': feedback})
    return (cursor.rowcount or 0) > 0
