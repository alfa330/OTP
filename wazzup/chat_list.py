"""Список чатов раздела (GET /api/wazzup/chats).

Чаты, где клиент ждёт ответа команды (общий счётчик wazzup/unread.py), идут
первыми, дальше — по времени последнего сообщения: ответ в соседнем чате больше
не уводит вниз чат, где клиент ждёт (решение владельца 09.10.2026). Счётчик
отдаётся в строке — список при открытии сразу стоит в этом порядке, не дожидаясь
живого счётчика. Счётчик ведётся только у аккаунта «op»; остальные аккаунты —
по времени, как прежде.
"""

ITEM_FIELDS = ('channelId', 'chatId', 'chatType', 'contactName', 'contactPhone',
               'lastMessageAt', 'lastMessageText', 'lastMessageIsEcho',
               'messagesCount', 'inboundCount', 'outboundCount', 'unreadCount',
               'lastMessageId', 'lastMessageStatus')

# Последнее сообщение чата — тем же правилом, что и превью в сводке
# (database._refresh_wazzup_chat_tx): последнее неудалённое по времени. Его id
# и статус дают галочки в строке списка, а id — сопоставление с живыми
# статусами доставки. Скалярные подзапросы считаются после LIMIT — только для
# строк страницы, по индексу (channel_id, chat_id, dt).
LAST_MESSAGE_SQL = ('(SELECT x.{column} FROM wazzup_messages x WHERE x.channel_id = {alias}.channel_id'
                    ' AND x.chat_id = {alias}.chat_id AND NOT x.is_deleted'
                    ' ORDER BY x.dt DESC, x.message_id DESC LIMIT 1)')


def last_message_columns(alias):
    return ', '.join(LAST_MESSAGE_SQL.format(column=column, alias=alias)
                     for column in ('message_id', 'status'))


def escape_like(value):
    """Метасимволы LIKE — буквально. Без этого '_' в запросе означал «любой один
    символ», а '%' — «что угодно»: поиск «7_78423714» притягивал чужие чаты, и
    переход по ссылке на такой чат решал «не найден» по разбавленной первой
    странице. Инъекции здесь не было (значение всегда шло параметром), а вот
    выдача врала."""
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def chat_list_queries(account, channel_id=None, q='', limit=30, offset=0):
    """((SQL, параметры) счёта, (SQL, параметры) страницы)."""
    where, params = ['c.account = %s'], [account]
    if channel_id:
        where.append('c.channel_id = %s')
        params.append(channel_id)
    if q:
        where.append("(c.contact_name ILIKE %s ESCAPE '\\' OR c.contact_phone ILIKE %s ESCAPE '\\'"
                     " OR c.chat_id ILIKE %s ESCAPE '\\')")
        like = f'%{escape_like(q)}%'
        params.extend([like, like, like])
    condition = ' AND '.join(where)
    if account == 'op':
        waiting = 'COALESCE(s.unread_count, 0)'
        join = (' LEFT JOIN wazzup_chat_read_state s ON s.account = c.account'
                ' AND s.channel_id = c.channel_id AND s.chat_id = c.chat_id')
        order = f'({waiting} > 0) DESC, c.last_message_at DESC NULLS LAST'
    else:
        waiting, join, order = '0', '', 'c.last_message_at DESC NULLS LAST'
    count = (f'SELECT COUNT(*) FROM wazzup_chats c WHERE {condition}', list(params))
    page = (f"""
        SELECT c.channel_id, c.chat_id, c.chat_type, c.contact_name, c.contact_phone,
               c.last_message_at, c.last_message_text, c.last_message_is_echo,
               c.messages_count, c.inbound_count, c.outbound_count, {waiting},
               {last_message_columns('c')}
          FROM wazzup_chats c{join}
         WHERE {condition}
         ORDER BY {order}
         LIMIT %s OFFSET %s""", params + [limit, offset])
    return count, page


def chat_list_item(row):
    item = dict(zip(ITEM_FIELDS, row))
    item['lastMessageAt'] = row[5].isoformat() if row[5] else None
    return item
