"""SQL чатов Syntony поверх готового курсора. Ни Flask, ни сети.

ПОВТОР СОБЫТИЯ БЕЗОПАСЕН — без журнала обработанных eventId:
* снимок диалога пишется, только если он НОВЕЕ записанного (snapshot_at);
* содержимое сообщения — только новее (content_at) и только если отличается: повтор
  ничего не переписывает и не плодит мёртвых строк;
* статус — только «вперёд» по рангу (rules.STATUS_RANKS), а момент каждого статуса
  (sent_at, delivered_at, read_at, failed_at) запоминается при первом известии о нём,
  в каком бы порядке события ни пришли.

Порядок записи в одной транзакции — сначала диалог, потом сообщение: у всех писателей
один, чтобы встречные транзакции не ловили взаимную блокировку.
"""

from psycopg2.extras import Json

from . import rules

_JSON_COLUMNS = frozenset({'handoff', 'extra', 'media'})

_CONVERSATION_COLUMNS = (
    'id', 'park_id', 'park_name', 'channel_id', 'channel_type', 'channel_provider',
    'channel_name', 'channel_phone', 'contact_id', 'contact_phone', 'contact_name',
    'contact_external_id', 'status', 'assigned_kind', 'assigned_user_id', 'assigned_user_name',
    'message_count', 'created_at', 'last_message_at', 'last_inbound_at', 'closed_at',
    'closed_reason', 'lead_id', 'handoff', 'extra', 'snapshot_at',
)

_MESSAGE_CONTENT = (
    'conversation_id', 'direction', 'sender_type', 'origin', 'source', 'author_id',
    'author_name', 'content_type', 'text', 'media', 'external_message_id',
    'reply_to_message_id', 'created_at', 'edited_at', 'deleted_at', 'delete_scope', 'extra',
)
_MESSAGE_STATUS = (
    'status', 'status_rank', 'status_at', 'error_code', 'error_message',
    'sent_at', 'delivered_at', 'read_at', 'failed_at',
)
_MESSAGE_COLUMNS = ('id',) + _MESSAGE_CONTENT + ('content_at',) + _MESSAGE_STATUS + ('first_seen_via',)
_STUB_COLUMNS = ('id', 'conversation_id') + _MESSAGE_STATUS + ('first_seen_via',)


def _values(row, columns):
    return [Json(row[name]) if name in _JSON_COLUMNS and row[name] is not None else row[name]
            for name in columns]


def _placeholders(columns):
    return ', '.join(['%s'] * len(columns))


_UPSERT_CONVERSATION_SQL = """
    INSERT INTO syntony_chat_conversations AS c ({columns})
    VALUES ({placeholders})
    ON CONFLICT (id) DO UPDATE SET {updates}, updated_at = now()
     WHERE c.snapshot_at IS NULL OR EXCLUDED.snapshot_at > c.snapshot_at
""".format(
    columns=', '.join(_CONVERSATION_COLUMNS),
    placeholders=_placeholders(_CONVERSATION_COLUMNS),
    updates=', '.join('{0} = EXCLUDED.{0}'.format(name)
                      for name in _CONVERSATION_COLUMNS if name != 'id'),
)

# Строка, которую завело событие статуса (content_at IS NULL), заполняется всегда.
# RETURNING (xmax = 0) — вставлена ли строка заново: тогда статус уже в ней, и второй
# запрос не нужен.
_UPSERT_MESSAGE_SQL = """
    INSERT INTO syntony_chat_messages AS m ({columns})
    VALUES ({placeholders})
    ON CONFLICT (id) DO UPDATE SET {updates}, content_at = EXCLUDED.content_at,
                                   updated_at = now()
     WHERE (m.content_at IS NULL OR EXCLUDED.content_at > m.content_at)
       AND ({old}) IS DISTINCT FROM ({new})
    RETURNING (xmax = 0) AS inserted
""".format(
    columns=', '.join(_MESSAGE_COLUMNS),
    placeholders=_placeholders(_MESSAGE_COLUMNS),
    updates=', '.join('{0} = EXCLUDED.{0}'.format(name) for name in _MESSAGE_CONTENT),
    old=', '.join('m.' + name for name in _MESSAGE_CONTENT),
    new=', '.join('EXCLUDED.' + name for name in _MESSAGE_CONTENT),
)

_INSERT_STUB_SQL = """
    INSERT INTO syntony_chat_messages ({columns})
    VALUES ({placeholders})
    ON CONFLICT (id) DO NOTHING
    RETURNING id
""".format(columns=', '.join(_STUB_COLUMNS), placeholders=_placeholders(_STUB_COLUMNS))

# Все правые части UPDATE видят СТАРУЮ строку, поэтому status_rank в CASE — прежний ранг,
# в каком бы порядке ни стояли присваивания.
_APPLY_STATUS_SQL = """
    UPDATE syntony_chat_messages
       SET status        = CASE WHEN %(status_rank)s > status_rank THEN %(status)s ELSE status END,
           status_at     = CASE WHEN %(status_rank)s > status_rank THEN %(status_at)s ELSE status_at END,
           error_code    = CASE WHEN %(status_rank)s > status_rank THEN %(error_code)s ELSE error_code END,
           error_message = CASE WHEN %(status_rank)s > status_rank THEN %(error_message)s ELSE error_message END,
           status_rank   = GREATEST(status_rank, %(status_rank)s),
           sent_at       = COALESCE(sent_at, %(sent_at)s),
           delivered_at  = COALESCE(delivered_at, %(delivered_at)s),
           read_at       = COALESCE(read_at, %(read_at)s),
           failed_at     = COALESCE(failed_at, %(failed_at)s),
           updated_at    = now()
     WHERE id = %(id)s
       AND (%(status_rank)s > status_rank
            OR (sent_at IS NULL AND %(sent_at)s IS NOT NULL)
            OR (delivered_at IS NULL AND %(delivered_at)s IS NOT NULL)
            OR (read_at IS NULL AND %(read_at)s IS NOT NULL)
            OR (failed_at IS NULL AND %(failed_at)s IS NOT NULL))
"""


def upsert_conversation(cursor, row):
    """Снимок диалога. True — записан (новый или новее прежнего)."""
    cursor.execute(_UPSERT_CONVERSATION_SQL, _values(row, _CONVERSATION_COLUMNS))
    return cursor.rowcount > 0


def apply_status(cursor, row):
    """Статус к уже существующей строке сообщения. True — что-то поменялось."""
    cursor.execute(_APPLY_STATUS_SQL, {name: row[name] for name in ('id',) + _MESSAGE_STATUS})
    return cursor.rowcount > 0


def upsert_message(cursor, row):
    """Сообщение целиком (содержимое и статус) → 'inserted' / 'updated' / 'unchanged'."""
    cursor.execute(_UPSERT_MESSAGE_SQL, _values(row, _MESSAGE_COLUMNS))
    result = cursor.fetchone()
    if result is not None and result[0]:
        return 'inserted'
    status_changed = apply_status(cursor, row)
    return 'updated' if (result is not None or status_changed) else 'unchanged'


def apply_message_created(cursor, data, occurred_at):
    """Событие message.created: снимок диалога + сообщение → итог upsert_message."""
    message = rules.message_row(data.get('message'), occurred_at, rules.VIA_WEBHOOK)
    if message is None:
        raise rules.PayloadError('no_message')
    conversation = rules.conversation_row(data.get('conversation'), occurred_at)
    if conversation is not None:
        if not message['conversation_id']:
            message['conversation_id'] = conversation['id']
        upsert_conversation(cursor, conversation)
    return upsert_message(cursor, message)


def get_marks(cursor):
    """Отметки сторожа API: {имя: время}."""
    cursor.execute('SELECT name, mark FROM syntony_chat_sync_marks')
    return {name: mark for name, mark in cursor.fetchall()}


def set_mark(cursor, name, mark):
    cursor.execute("""
        INSERT INTO syntony_chat_sync_marks (name, mark) VALUES (%s, %s)
        ON CONFLICT (name) DO UPDATE SET mark = EXCLUDED.mark, updated_at = now()
    """, (name, mark))


def delete_old_messages(cursor, cutoff, limit):
    """Сообщения старше cutoff, не больше limit за раз → сколько убрано. Заготовка без
    содержимого меряется моментом первого известия о ней."""
    cursor.execute("""
        DELETE FROM syntony_chat_messages
         WHERE id IN (SELECT id FROM syntony_chat_messages
                       WHERE COALESCE(created_at, first_seen_at) < %s
                       LIMIT %s)
    """, (cutoff, int(limit)))
    return cursor.rowcount


def delete_old_conversations(cursor, cutoff, limit):
    """Диалоги, в которых последнее сообщение старше cutoff, → сколько убрано."""
    cursor.execute("""
        DELETE FROM syntony_chat_conversations
         WHERE id IN (SELECT id FROM syntony_chat_conversations
                       WHERE COALESCE(last_message_at, created_at, first_seen_at) < %s
                       LIMIT %s)
    """, (cutoff, int(limit)))
    return cursor.rowcount


def apply_message_status(cursor, data, occurred_at):
    """Событие message.status → 'inserted' (сообщения ещё нет — строка-заготовка со
    статусом, содержимое дольёт message.created) / 'updated' / 'unchanged'."""
    row = rules.status_event_row(data, occurred_at)
    if row is None:
        raise rules.PayloadError('no_message_id')
    cursor.execute(_INSERT_STUB_SQL, _values(row, _STUB_COLUMNS))
    if cursor.fetchone() is not None:
        return 'inserted'
    return 'updated' if apply_status(cursor, row) else 'unchanged'
