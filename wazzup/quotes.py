"""Цитата у сообщения Wazzup: на какое сообщение ответили «Ответить» в WhatsApp.

Wazzup присылает её в самом ответе, полем `quotedMessage` — и в вебхуке, и в
истории окна чатов. Так приходят все ответы, сделанные не из iCORE: водитель
отвечает на своё же сообщение или на сообщение оператора, оператор отвечает с
телефона или из окна Wazzup. До 28.10.2026 мы это поле не читали, и цитата была
видна только у ответов из iCORE — их цель лежит в wazzup_pilot_outbox
(тест-чат владельца: три ответа с цитатой, ни один не был виден).

В архиве у сообщения лежит id цитируемого — по нему лента берёт оригинал из
того же чата — и снимок цитаты на случай, когда оригинала в архиве нет: старше
срока хранения или пришёл до подключения вебхука.
"""

QUOTE_COLUMNS = ('quoted_message_id', 'quoted_is_echo', 'quoted_type', 'quoted_text',
                 'quoted_author_name')
NO_QUOTE = (None,) * len(QUOTE_COLUMNS)
# Входящее: статус вебхука 'inbound', в окне чатов — 99.
INBOUND_STATUSES = ('inbound', 99)


def quote_values(message):
    """Значения QUOTE_COLUMNS для элемента messages[] вебхука; без цитаты — NO_QUOTE.

    Состав `quotedMessage` документация Wazzup не расписывает («объект с
    параметрами цитируемого сообщения»), а окно чатов называет id цитаты `id`,
    не `messageId`, и входящее отмечает статусом 99 — читаем обе формы.
    Имя автора берём только у исходящего: у входящего Wazzup кладёт туда
    телефон клиента."""
    quoted = message.get('quotedMessage') if isinstance(message, dict) else None
    if not isinstance(quoted, dict):
        return NO_QUOTE
    quoted_id = quoted.get('messageId') or quoted.get('id')
    if not quoted_id:
        return NO_QUOTE
    if isinstance(quoted.get('isEcho'), bool):
        is_echo = quoted['isEcho']
    elif quoted.get('status') is not None:
        is_echo = quoted['status'] not in INBOUND_STATUSES
    else:
        is_echo = None
    kind = quoted.get('type')
    return (str(quoted_id), is_echo, kind if isinstance(kind, str) else None,
            quoted.get('text') or None, (quoted.get('authorName') or None) if is_echo else None)
