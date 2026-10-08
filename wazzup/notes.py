"""Authenticated internal comments; this module has no external send path."""
import uuid
from datetime import datetime

from flask import jsonify, request

MAX_TEXT = 4000
PAGE_SIZE = 200
NOTE_COLUMNS = 'id,created_at,text,author_name,author_id'


def note_item(row):
    return dict(zip(('id', 'createdAt', 'text', 'authorName', 'authorId'),
                    [str(row[0]), row[1].isoformat() if isinstance(row[1], datetime)
                     else row[1], row[2], row[3], row[4]]))


def _json(status=200, **values):
    response = jsonify(**values)
    response.status_code = status
    response.headers['Cache-Control'] = 'private, no-store'
    return response


def register_note_routes(bp, actor, require_api_key, preflight, db, excluded_channels=()):
    def scope(values):
        if values.get('account') != 'op':
            return None, _json(403, error='Комментарии доступны в чатах Верификаторов')
        channel, chat = values.get('channelId'), values.get('chatId')
        if (not isinstance(channel, str) or not isinstance(chat, str)
                or not chat.strip() or len(chat) > 100):
            return None, _json(400, error='Укажите канал и чат')
        try:
            channel = str(uuid.UUID(channel))
        except (ValueError, TypeError, AttributeError):
            return None, _json(400, error='Некорректный канал')
        if channel in excluded_channels:
            return None, _json(403, error='Global обрабатывается отдельно')
        return (channel, chat), None

    def exists(cursor, channel, chat):
        cursor.execute("SELECT 1 FROM wazzup_chats WHERE account='op' "
                       "AND channel_id=%s AND chat_id=%s", (channel, chat))
        return cursor.fetchone() is not None

    @bp.route('/notes', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def internal_notes():
        if request.method == 'OPTIONS':
            return preflight()
        user, error = actor()
        if error:
            return error
        if request.method == 'POST' and request.content_length and request.content_length > 64 * 1024:
            return _json(413, error='Комментарий слишком большой')
        values = request.args if request.method == 'GET' else request.get_json(silent=True)
        if values is None or not hasattr(values, 'get'):
            return _json(400, error='Некорректный запрос')
        context, error = scope(values)
        if error:
            return error
        channel, chat = context

        if request.method == 'GET':
            before_id = values.get('beforeId')
            if before_id is not None:
                try:
                    before_id = str(uuid.UUID(before_id))
                except (ValueError, TypeError, AttributeError):
                    return _json(400, error='Некорректная страница комментариев')
            with db._get_cursor() as cursor:
                if not exists(cursor, channel, chat):
                    return _json(404, error='Чат не найден')
                before = None
                if before_id:
                    cursor.execute("SELECT created_at,id FROM wazzup_chat_notes WHERE account='op' "
                                   "AND channel_id=%s AND chat_id=%s AND id=%s", (channel, chat, before_id))
                    before = cursor.fetchone()
                    if before is None:
                        return _json(404, error='Комментарий для этой страницы не найден')
                cursor.execute("SELECT " + NOTE_COLUMNS + " FROM wazzup_chat_notes WHERE account='op' "
                               "AND channel_id=%s AND chat_id=%s "
                               + ("AND (created_at,id)<(%s,%s) " if before else '')
                               + "ORDER BY created_at DESC,id DESC LIMIT %s",
                               (channel, chat) + (tuple(before) if before else ()) + (PAGE_SIZE + 1,))
                rows = cursor.fetchall()
            has_more = len(rows) > PAGE_SIZE
            items = [note_item(row) for row in reversed(rows[:PAGE_SIZE])]
            return _json(items=items, hasMore=has_more,
                         nextBeforeId=items[0]['id'] if has_more else None)

        text = values.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT or '\x00' in text:
            return _json(400, error='Введите комментарий длиной до 4000 символов')
        try:
            client_id = str(uuid.UUID(str(values.get('clientNoteId', ''))))
        except (ValueError, TypeError, AttributeError):
            return _json(400, error='Некорректный идентификатор комментария')
        # Claim the UUID in one transaction before notifying other viewers. A
        # timeout/retry cannot create a duplicate or change its original author.
        with db._get_cursor() as cursor:
            if not exists(cursor, channel, chat):
                return _json(404, error='Чат не найден')
            cursor.execute("INSERT INTO wazzup_chat_notes "
                           "(id,account,channel_id,chat_id,client_note_id,author_id,author_name,text) "
                           "VALUES(%s,'op',%s,%s,%s,%s,%s,%s) "
                           "ON CONFLICT(author_id,client_note_id) DO NOTHING RETURNING " + NOTE_COLUMNS,
                           (str(uuid.uuid4()), channel, chat, client_id, user[0],
                            str(user[2] or user[7] or 'Оператор'), text))
            row = cursor.fetchone()
            if row is not None:
                result, status = note_item(row), 201
            else:
                cursor.execute("SELECT " + NOTE_COLUMNS + ",account,channel_id,chat_id "
                               "FROM wazzup_chat_notes WHERE author_id=%s AND client_note_id=%s",
                               (user[0], client_id))
                row = cursor.fetchone()
                if row is None or tuple(row[5:8]) != ('op', channel, chat) or row[2] != text:
                    return _json(409, error='Этот идентификатор уже использован для другого комментария',
                                 code='REQUEST_CONFLICT')
                result, status = note_item(row), 200
        return _json(status, item=result)
