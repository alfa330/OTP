"""Чтение чатов Syntony для показателей (metrics.py) поверх готового курсора.

Текстов, телефонов и имён водителей запросы не берут: показателям нужны направление,
отправитель, автор ответа и моменты. Номер водителя участвует только внутри SQL —
проверкой по «Реестру тестовых номеров».
"""

from . import metrics, rules

# Ответы сотрудников нужны по ВСЕЙ сети (онлайн — ответ на любом номере), остальное —
# только по номерам СЗоВ. Строки-заготовки статуса (created_at IS NULL) окно не пускает.
_WINDOW_SQL = """
    SELECT m.id, m.conversation_id, m.direction, m.sender_type, m.author_name,
           m.created_at, m.delete_scope,
           c.id, c.channel_id, c.created_at, c.handoff ->> 'requestedAt', {is_test}
      FROM syntony_chat_messages m
      LEFT JOIN syntony_chat_conversations c ON c.id = m.conversation_id
     WHERE m.created_at >= %(start)s AND m.created_at < %(end)s
       AND (c.channel_id = ANY(%(channels)s)
            OR (m.direction = 'outbound' AND m.sender_type = 'human'
                AND m.author_name IS NOT NULL))
"""


def load_window(cursor, *, start, end, channels, is_test_sql=None):
    """Сообщения [start, end) → ({id: metrics.Conversation}, [metrics.Message]).

    is_test_sql(выражение номера) → SQL-условие «номер тестовый»; None — без проверки.
    """
    is_test = is_test_sql('c.contact_phone') if is_test_sql else 'FALSE'
    cursor.execute(_WINDOW_SQL.format(is_test=is_test),
                   {'start': start, 'end': end, 'channels': sorted(channels)})
    conversations = {}
    messages = []
    for (message_id, conversation_id, direction, sender_type, author_name, created_at,
         delete_scope, conv_id, channel_id, conv_created_at, handoff_at, test) in cursor.fetchall():
        messages.append(metrics.Message(
            id=message_id, conversation_id=conversation_id, direction=direction,
            sender_type=sender_type, author_name=author_name, created_at=created_at,
            delete_scope=delete_scope))
        if conv_id and conv_id not in conversations:
            conversations[conv_id] = metrics.Conversation(
                id=conv_id, channel_id=channel_id, created_at=conv_created_at,
                handoff_requested_at=rules.parse_time(handoff_at), is_test=bool(test))
    return conversations, messages


def load_state(cursor):
    """Состояние данных для табло: отметки сторожа API (sync.py) с моментом их записи и
    самое раннее сообщение в базе.

    → {'marks': {имя: (отметка, когда записана)}, 'earliest': время или None}.
    Отметка ленты двигается каждым удачным прогоном сторожа (раз в 3 минуты); самое раннее
    сообщение — с какого момента данные вообще есть (сторож начинает с полуночи, а через
    30 дней чистит старое) — берётся по индексу created_at, без обхода таблицы.
    """
    cursor.execute('SELECT name, mark, updated_at FROM syntony_chat_sync_marks')
    marks = {name: (mark, updated_at) for name, mark, updated_at in cursor.fetchall()}
    cursor.execute('SELECT min(created_at) FROM syntony_chat_messages')
    row = cursor.fetchone()
    return {'marks': marks, 'earliest': row[0] if row else None}
