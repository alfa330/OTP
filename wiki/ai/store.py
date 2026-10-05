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
INSERT INTO wiki_ai_chats (user_id, title, space_id)
VALUES (%(user_id)s, %(title)s, %(space_id)s)
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

# gated_by — раздел, со строками которого собран ответ («Списки Байги»): такой
# ответ за пределы разговора автора не передаётся (wiki/questions.py).
_INSERT_MESSAGE = """
INSERT INTO wiki_ai_messages
       (chat_id, seq, role, kind, text, provider, model, elapsed_ms,
        input_tokens, output_tokens, gated_by)
VALUES (%(chat_id)s, %(seq)s, %(role)s, %(kind)s, %(text)s, %(provider)s,
        %(model)s, %(elapsed_ms)s, %(input_tokens)s, %(output_tokens)s, %(gated_by)s)
RETURNING id, created_at
"""

# stale/stale_note ХРАНЯТСЯ, а не считаются при чтении истории. Причина в самой
# природе признака: «архивная статья» вывелась бы из названия и потом, а «срок
# истёк 30.04.2025» — свойство МОМЕНТА ОТВЕТА. Пересчитай его при открытии
# старого чата — и ответ, данный при живой акции, задним числом получил бы
# пометку, которой при выдаче не было. Снимок правдивее.
#
# Источник-справочник (запись вкладки «Офисы»/«Города», wiki/directory.py)
# статьи не имеет: article_id NULL, вместо него — вкладка, запись и
# пространство. chunk_id у него вымышленный (отрицательный) и не хранится.
#
# Источник «Списки Байги» (baiga/assistant.py) — того же рода: статьи нет,
# вкладка 'baiga', неделя в ref_id, водитель в ref_key, пространства нет.
#
# Колонки вставки и чтения названы ОДИН раз, и оба запроса собираются из этих
# списков: колонку нельзя добавить в перечень и забыть в значениях, а чтение идёт
# по именам, а не по номерам позиций (номер ВУ, записанный в «город записи»,
# заметили бы только на проде).
_INSERT_FIELDS = (
    'message_id', 'ord', 'article_id', 'chunk_id', 'chunk_text_hash', 'title', 'slug',
    'heading_path', 'quote', 'quote_ok', 'requires_ack', 'attributed',
    'stale', 'stale_note', 'stale_kind',
    'source_kind', 'tab', 'ref_id', 'ref_city', 'ref_key', 'space_id',
)
_INSERT_SOURCE = 'INSERT INTO wiki_ai_message_sources (%s) VALUES (%s)' % (
    ', '.join(_INSERT_FIELDS), ', '.join('%%(%s)s' % name for name in _INSERT_FIELDS))

_TOUCH_CHAT = """
UPDATE wiki_ai_chats
   SET message_count = (SELECT count(*) FROM wiki_ai_messages WHERE chat_id = %(chat_id)s),
       last_message_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
       updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
       title = CASE WHEN title = '' THEN %(title)s ELSE title END
 WHERE id = %(chat_id)s AND user_id = %(user_id)s
"""

_MESSAGE_FIELDS = ('id', 'seq', 'role', 'kind', 'text', 'provider', 'model', 'elapsed_ms',
                   'feedback', 'created_at', 'gated_by')
_MESSAGES = """
SELECT %s
  FROM wiki_ai_messages
 WHERE chat_id = %%(chat_id)s
 ORDER BY seq
""" % ', '.join(_MESSAGE_FIELDS)

# Доступность источника — джойном на периметр, переданный списком id. Так пометка
# «статья недоступна» появляется сразу после отзыва доступа, без переиндексации.
# У источника-справочника статьи нет (NULL даёт здесь NULL) — его доступность
# решает chat_messages по пространству и тумблеру вкладки на сейчас.
_SOURCE_FIELDS = ('message_id', 'ord', 'article_id', 'title', 'slug', 'heading_path',
                  'quote', 'quote_ok', 'requires_ack', 'attributed',
                  'stale', 'stale_note', 'stale_kind',
                  'source_kind', 'tab', 'ref_id', 'ref_city', 'ref_key', 'space_id')
# Последней колонкой — «статья в периметре сейчас» (visible).
SOURCE_ROW = _SOURCE_FIELDS + ('visible',)
_SOURCES = """
SELECT %s,
       (s.article_id = ANY(%%(visible)s)) AS visible
  FROM wiki_ai_message_sources s
  JOIN wiki_ai_messages m ON m.id = s.message_id
 WHERE m.chat_id = %%(chat_id)s
 ORDER BY s.message_id, s.ord
""" % ', '.join('s.%s' % name for name in _SOURCE_FIELDS)

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


_RECENT_TURNS = """
SELECT role, kind, text FROM wiki_ai_messages
 WHERE chat_id = %(chat_id)s
 ORDER BY seq DESC
 LIMIT %(limit)s
"""


def recent_turns(cursor, chat_id, *, limit=6):
    """Последние реплики диалога для передачи модели, в прямом порядке.

    Без них уточняющий вопрос был тупиком: помощник спрашивал «какой именно офис
    — Ipartner, Global, Taxi24?», а на ответ «Taxi24» отказывался, потому что не
    помнил собственного вопроса. Реплика приходила в модель одна, без диалога.

    Берём немного (по умолчанию 6): контекст нужен для уточнений, а не для
    пересказа всей переписки — лимит провайдера минутный, и каждая лишняя реплика
    отнимает пропускную способность у следующего оператора.
    """
    cursor.execute(_RECENT_TURNS, {'chat_id': chat_id, 'limit': max(int(limit), 0)})
    rows = list(reversed(cursor.fetchall()))
    return [{'role': row[0], 'kind': row[1], 'text': row[2]} for row in rows]


_RECENT_QUESTIONS = """
SELECT text FROM wiki_ai_messages
 WHERE chat_id = %(chat_id)s AND role = 'user'
 ORDER BY seq DESC
 LIMIT %(limit)s
"""


def recent_questions(cursor, chat_id, *, limit):
    """Последние вопросы человека в этом разговоре, от старого к новому.

    Нужны «Спискам Байги» (baiga/assistant.py): о каком водителе и о какой
    неделе разговор, раздел выводит из самих вопросов, и трёх реплик, которые
    получает модель (recent_turns), на это не хватает.
    """
    cursor.execute(_RECENT_QUESTIONS, {'chat_id': chat_id, 'limit': max(int(limit), 0)})
    return [row[0] or '' for row in reversed(cursor.fetchall())]


def _title_from(question, limit=60):
    text = ' '.join(str(question or '').split())
    return text[:limit] if text else 'Новый вопрос'


def create_chat(cursor, user_id, *, title='', space_id=None):
    cursor.execute(_CREATE_CHAT, {'user_id': user_id, 'title': title[:255],
                                  'space_id': space_id})
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
                   output_tokens=None, sources=(), gated_by=None):
    cursor.execute(_NEXT_SEQ, {'chat_id': chat_id})
    seq = cursor.fetchone()[0]
    cursor.execute(_INSERT_MESSAGE, {
        'chat_id': chat_id, 'seq': seq, 'role': role, 'kind': kind, 'text': text,
        'provider': provider, 'model': model, 'elapsed_ms': elapsed_ms,
        'input_tokens': input_tokens, 'output_tokens': output_tokens,
        'gated_by': (gated_by or None) and str(gated_by)[:16]})
    message_id, created_at = cursor.fetchone()

    for position, source in enumerate(sources):
        kind = source.get('source_kind') or 'article'
        chunk_id = source.get('chunk_id')
        cursor.execute(_INSERT_SOURCE, {
            'message_id': message_id, 'ord': position,
            'article_id': source.get('article_id'),
            'chunk_id': chunk_id if kind == 'article' else None,
            'chunk_text_hash': source.get('chunk_text_hash'),
            'source_kind': kind[:16],
            'tab': (source.get('tab') or None) and str(source['tab'])[:16],
            'ref_id': source.get('ref_id'),
            'ref_city': (source.get('ref_city') or None) and str(source['ref_city'])[:120],
            'ref_key': (source.get('ref_key') or None) and str(source['ref_key'])[:64],
            'space_id': source.get('space_id'),
            'title': (source.get('title') or '')[:255],
            'slug': (source.get('slug') or '')[:255],
            'heading_path': source.get('heading_path') or '',
            'quote': source.get('quote') or '',
            'quote_ok': bool(source.get('ok')),
            'requires_ack': bool(source.get('requires_ack')),
            'attributed': bool(source.get('attributed')),
            'stale': bool(source.get('stale')),
            'stale_note': (source.get('stale_note') or '')[:120],
            'stale_kind': (source.get('stale_kind') or '')[:16]})
    return {'id': message_id, 'seq': seq,
            'created_at': created_at.isoformat() if created_at else None}


def touch_chat(cursor, user_id, chat_id, *, first_question=''):
    cursor.execute(_TOUCH_CHAT, {'chat_id': chat_id, 'user_id': user_id,
                                 'title': _title_from(first_question)})


def _directory_available(kind, tab, space_id, directory_access):
    """Открыт ли источник-справочник человеку СЕЙЧАС: пространство выдано не
    гостем и вкладка в нём включена (directory.access_map). Снимок цитаты без
    этого — лазейка: адрес и телефон из чужой вкладки через старый ответ."""
    feature = tab if tab in ('offices', 'cities') else None
    access = (directory_access or {}).get(space_id) or {}
    return bool(kind != 'article' and feature and access.get(feature))


def chat_messages(cursor, chat_id, *, visible_article_ids=(), directory_access=None,
                  baiga_access=None):
    """baiga_access — () -> bool: открыты ли человеку «Списки Байги» сейчас.
    Спрашивается один раз и только когда такой источник в чате есть."""
    baiga_open = []

    def baiga_available():
        if not baiga_open:
            baiga_open.append(bool(baiga_access and baiga_access()))
        return baiga_open[0]

    cursor.execute(_MESSAGES, {'chat_id': chat_id})
    messages = []
    for row in cursor.fetchall():
        message = dict(zip(_MESSAGE_FIELDS, row))
        message['created_at'] = message['created_at'].isoformat() if message['created_at'] else None
        message['sources'] = []
        messages.append(message)
    by_id = {message['id']: message for message in messages}

    cursor.execute(_SOURCES, {'chat_id': chat_id,
                              'visible': sorted(visible_article_ids) or [-1]})
    for row in cursor.fetchall():
        source = dict(zip(SOURCE_ROW, row))
        message = by_id.get(source['message_id'])
        if message is None:
            continue
        kind = source['source_kind'] or 'article'
        if kind == 'article':
            available = bool(source['visible'])
            closed_title = 'Статья недоступна'
        elif kind == 'baiga':
            # В цитате ФИО и номер ВУ водителя: без раздела и без QR её нет.
            available = baiga_available()
            closed_title = 'Раздел недоступен'
        else:
            available = _directory_available(kind, source['tab'], source['space_id'],
                                             directory_access)
            closed_title = 'Справочник недоступен'

        def shown(name, closed=None):
            return source[name] if available else closed

        message['sources'].append({
            'ord': source['ord'], 'article_id': source['article_id'],
            'title': shown('title', closed_title),
            'slug': shown('slug'),
            'heading_path': shown('heading_path', ''),
            # Цитата закрытой статьи не отдаётся: доступ мог быть отозван после
            # ответа, и снимок не должен становиться лазейкой.
            'quote': shown('quote', ''),
            'quote_ok': bool(source['quote_ok']), 'requires_ack': bool(source['requires_ack']),
            'attributed': bool(source['attributed']), 'available': available,
            'stale': bool(source['stale']), 'stale_note': source['stale_note'] or '',
            'stale_kind': source['stale_kind'] or '',
            'source_kind': kind,
            'tab': shown('tab'),
            'ref_id': shown('ref_id'),
            'ref_city': shown('ref_city'),
            'ref_key': shown('ref_key'),
            'space_id': shown('space_id')})
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
