"""Scoped file staging. Only the server turns an attachment ID into a GCS URL.

The upload route never sends a message. Files are immutable, private objects;
Wazzup receives a short-lived direct URL only for a newly claimed outbox entry.
"""
import hashlib
import io
import re
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from flask import jsonify, request
from PIL import Image, UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge

from .attachments import AttachmentError, _rate

MAX_FILE = 10 * 1024 * 1024
MAX_IMAGE = 5 * 1024 * 1024
MAX_BODY = MAX_FILE + 64 * 1024
STAGING_PREFIX = 'wazzup/pilot/uploads/'
_slots = threading.BoundedSemaphore(2)
_cleanup_lock = threading.Lock()
_cleanup_running = threading.Lock()
_cleanup_after = 0
_FIELDS = ('id', 'channel_id', 'chat_id', 'user_id', 'original_name', 'content_type',
           'message_type', 'file_size', 'sha256', 'bucket', 'blob_path', 'expires_at', 'deleted_at')
_SELECT = 'SELECT ' + ','.join(_FIELDS) + " FROM wazzup_pilot_uploads WHERE account='op' "

# Extension and actual bytes must agree. The browser's MIME is not trusted.
_TYPES = {
    'pdf': ('application/pdf', 'document'),
    'jpg': ('image/jpeg', 'image'), 'jpeg': ('image/jpeg', 'image'),
    'png': ('image/png', 'image'), 'webp': ('image/webp', 'image'),
    'doc': ('application/msword', 'document'),
    'xls': ('application/vnd.ms-excel', 'document'),
    'ppt': ('application/vnd.ms-powerpoint', 'document'),
    'docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'document'),
    'xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'document'),
    'pptx': ('application/vnd.openxmlformats-officedocument.presentationml.presentation', 'document'),
    'txt': ('text/plain', 'document'), 'csv': ('text/csv', 'document'),
    'zip': ('application/zip', 'document'),
    'mp4': ('video/mp4', 'video'), '3gp': ('video/3gpp', 'video'),
    'mp3': ('audio/mpeg', 'audio'), 'm4a': ('audio/mp4', 'audio'),
    'aac': ('audio/aac', 'audio'), 'ogg': ('audio/ogg', 'audio'),
    'amr': ('audio/amr', 'audio'),
}


class UploadError(AttachmentError):
    def __init__(self, message, status=400, code='ATTACHMENT_REJECTED'):
        super().__init__(message, status)
        self.code = code


def public_item(item):
    expires = item['expires_at']
    return {'id': str(item['id']), 'name': item['original_name'],
            'size': item['file_size'], 'mime': item['content_type'],
            'expiresAt': expires.isoformat() if isinstance(expires, datetime) else expires}


def _available(item):
    expires = item['expires_at']
    if isinstance(expires, str):
        expires = datetime.fromisoformat(expires.replace('Z', '+00:00'))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if item['deleted_at'] or expires <= datetime.now(timezone.utc):
        raise UploadError('Срок хранения вложения истёк. Выберите файл ещё раз.', 410, 'ATTACHMENT_EXPIRED')


def resolve_upload(db, user_id, channel_id, chat_id, attachment_id):
    """Validate the immutable file before claiming a NEW outbox entry."""
    with db._get_cursor() as cursor:
        cursor.execute(_SELECT + 'AND id=%s AND user_id=%s AND channel_id=%s AND chat_id=%s',
                       (attachment_id, user_id, channel_id, chat_id))
        row = cursor.fetchone()
    if not row:
        raise UploadError('Вложение не найдено в этом чате. Выберите файл ещё раз.', 404, 'ATTACHMENT_NOT_FOUND')
    item = dict(zip(_FIELDS, row))
    _available(item)
    return item


def sign_upload(gcs, item):
    """Do not persist this bearer URL in chat history, drafts, logs or errors."""
    if not gcs or not callable(gcs.get('client')):
        raise UploadError('Хранилище вложений недоступно', 503, 'ATTACHMENT_STORAGE_OFF')
    filename = item['original_name']
    ascii_name = re.sub(r'[^A-Za-z0-9._-]', '_', filename) or 'file'
    disposition = 'attachment; filename="' + ascii_name + '"; filename*=UTF-8\'\'' + quote(filename, safe='')
    try:
        return gcs['client']().bucket(item['bucket']).blob(item['blob_path']).generate_signed_url(
            version='v4', expiration=timedelta(minutes=30), method='GET',
            response_disposition=disposition, response_type=item['content_type'])
    except Exception:
        raise UploadError('Не удалось подготовить файл к отправке. Попробуйте ещё раз.',
                          503, 'ATTACHMENT_STORAGE_OFF') from None


def prepare_file(data, filename):
    name = str(filename or '').replace('\\', '/').rsplit('/', 1)[-1].strip()
    if not name or len(name) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise UploadError('Некорректное имя файла')
    extension = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    kind = _TYPES.get(extension)
    if not kind:
        raise UploadError('Этот формат не поддерживается. Выберите документ, фото, видео или аудиофайл.', 415)
    if not data:
        raise UploadError('Файл пустой')
    if len(data) > MAX_FILE:
        raise UploadError('Размер файла не должен превышать 10 МБ', 413)
    mime, message_type = kind
    valid = False
    if message_type == 'image':
        if len(data) > MAX_IMAGE:
            raise UploadError('Размер фотографии не должен превышать 5 МБ', 413)
        try:
            with Image.open(io.BytesIO(data)) as picture:
                expected = {'jpg': 'JPEG', 'jpeg': 'JPEG', 'png': 'PNG', 'webp': 'WEBP'}[extension]
                valid = picture.format == expected and picture.width * picture.height <= 25_000_000
                picture.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            valid = False
    elif extension == 'pdf':
        valid = data.startswith(b'%PDF-')
    elif extension in ('doc', 'xls', 'ppt'):
        valid = data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')
    elif extension in ('docx', 'xlsx', 'pptx', 'zip'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()  # Inspect headers only; never extract content.
                valid = bool(names) and len(names) <= 10000
                if extension != 'zip':
                    marker = {'docx': 'word/document.xml', 'xlsx': 'xl/workbook.xml',
                              'pptx': 'ppt/presentation.xml'}[extension]
                    valid = valid and '[Content_Types].xml' in names and marker in names
        except (zipfile.BadZipFile, OSError, ValueError):
            valid = False
    elif extension in ('txt', 'csv'):
        try:
            decoded = data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
            valid = '\x00' not in decoded
        except UnicodeError:
            valid = False
    elif extension in ('mp4', 'm4a', '3gp'):
        valid = len(data) >= 12 and data[4:8] == b'ftyp'
    elif extension == 'mp3':
        valid = data.startswith(b'ID3') or (len(data) > 2 and data[0] == 255 and data[1] & 224 == 224)
    elif extension == 'aac':
        valid = len(data) > 2 and data[0] == 255 and data[1] & 246 == 240
    elif extension == 'ogg':
        valid = data.startswith(b'OggS')
    elif extension == 'amr':
        valid = data.startswith((b'#!AMR\n', b'#!AMR-WB\n'))
    if not valid:
        raise UploadError('Файл повреждён или его содержимое не соответствует расширению.', 415)
    return dict(original_name=name, content_type=mime, message_type=message_type,
                file_size=len(data), sha256=hashlib.sha256(data).hexdigest())


def cleanup_expired(db, gcs):
    """Bounded staging cleanup; never list a bucket.

    Keep DB metadata as idempotency tombstones. All blob I/O happens outside DB
    transactions. Only this module's generated object prefix can be removed.
    """
    try:
        started = time.monotonic()
        with db._get_cursor() as cursor:
            cursor.execute("SELECT id,bucket,blob_path FROM wazzup_pilot_uploads "
                           "WHERE account='op' AND expires_at < now() - interval '5 minutes' AND deleted_at IS NULL "
                           'ORDER BY expires_at LIMIT 32')
            rows = cursor.fetchall()
        if not rows:
            return
        client = gcs['client']()
        for attachment_id, bucket, path in rows:
            if time.monotonic() - started >= 20:
                break
            if not path.startswith(STAGING_PREFIX):
                continue
            try:
                client.bucket(bucket).blob(path).delete(timeout=10, retry=None)
            except Exception as error:
                if getattr(error, 'code', None) != 404:
                    continue
            with db._get_cursor() as cursor:
                cursor.execute('UPDATE wazzup_pilot_uploads SET deleted_at=now() '
                               'WHERE id=%s AND bucket=%s AND blob_path=%s', (attachment_id, bucket, path))
    except Exception:
        # Cleanup is best effort; neither object addresses nor file names are logged.
        return


def schedule_cleanup(db, gcs):
    # File selection must not wait for old object deletions. Batches can drain
    # faster than the per-user upload limit, without an overlapping job or timer.
    global _cleanup_after
    now = time.monotonic()
    with _cleanup_lock:
        if now < _cleanup_after or not _cleanup_running.acquire(blocking=False):
            return
        _cleanup_after = now + 60
    def run():
        try:
            cleanup_expired(db, gcs)
        finally:
            _cleanup_running.release()
    try:
        threading.Thread(target=run, name='wazzup-upload-cleanup', daemon=True).start()
    except Exception:
        _cleanup_running.release()


def register_upload_routes(bp, actor, require_api_key, preflight, db, excluded_channels=(),
                           *, channels, gcs=None):
    @bp.route('/uploads', methods=['POST', 'OPTIONS'])
    @require_api_key
    def upload():
        if request.method == 'OPTIONS':
            return preflight()
        user, error = actor()
        if error:
            return error
        acquired = False
        uploaded = None
        incoming_files = None
        try:
            if request.content_length is None or request.content_length > MAX_BODY:
                raise UploadError('Размер файла не должен превышать 10 МБ', 413)
            if request.mimetype != 'multipart/form-data':
                raise UploadError('Передайте один файл')
            # Flask 3.0's request.max_content_length is read-only. Bound this
            # parser locally instead of mutating the app-wide limit for other
            # concurrent upload routes.
            parser = request.make_form_data_parser()
            parser.max_content_length = MAX_BODY
            # The decoder retains multipart boundary fragments between 64 KiB
            # chunks, so a 64 KiB memory cap rejects valid binary files.
            parser.max_form_memory_size = 256 * 1024
            parser.max_form_parts = 6
            _, form, incoming_files = parser.parse(request.stream, request.mimetype,
                                                  request.content_length, request.mimetype_params)
            if form.get('account') != 'op':
                raise UploadError('Недоступный аккаунт', 403)
            try:
                cid = str(uuid.UUID(form.get('channelId', '')))
                upload_id = str(uuid.UUID(form.get('clientUploadId', '')))
            except (ValueError, TypeError, AttributeError):
                raise UploadError('Некорректный идентификатор вложения или канала') from None
            chat = form.get('chatId')
            if not isinstance(chat, str) or not chat.strip() or len(chat) > 100:
                raise UploadError('Некорректный чат')
            if cid in excluded_channels:
                raise UploadError('Global обрабатывается отдельно', 403)
            files = list(incoming_files.items(multi=True))
            if len(files) != 1 or files[0][0] != 'file':
                raise UploadError('Выберите один файл для отправки')
            _rate(user[0], 'upload', 12)
            acquired = _slots.acquire(blocking=False)
            if not acquired:
                raise UploadError('Загрузка занята. Повторите через несколько секунд.', 429)
            file = files[0][1]
            data = file.read(MAX_FILE + 1)
            prepared = prepare_file(data, file.filename)
            with db._get_cursor() as cursor:
                cursor.execute(_SELECT + 'AND user_id=%s AND client_upload_id=%s', (user[0], upload_id))
                row = cursor.fetchone()
            if row:
                item = dict(zip(_FIELDS, row))
                _check_replay(item, prepared, cid, chat)
                _available(item)
                return jsonify(attachment=public_item(item)), 200, {'Cache-Control': 'private, no-store'}
            channel = next((c for c in channels('op') if c.get('channelId') == cid), None)
            if not channel or channel.get('state') != 'active' or channel.get('transport') not in ('whatsapp', 'wapi'):
                raise UploadError('Выберите активный канал WhatsApp')
            with db._get_cursor() as cursor:
                cursor.execute("SELECT chat_type FROM wazzup_chats WHERE account='op' AND channel_id=%s AND chat_id=%s",
                               (cid, chat))
                known = cursor.fetchone()
            if not known or known[0] != 'whatsapp':
                raise UploadError('Выберите существующий личный чат WhatsApp')
            bucket = gcs['bucket_name']() if gcs and callable(gcs.get('bucket_name')) else None
            if not bucket or not callable(gcs.get('client')):
                raise UploadError('Хранилище вложений не настроено', 503, 'ATTACHMENT_STORAGE_OFF')
            schedule_cleanup(db, gcs)
            identifier = str(uuid.uuid4())
            extension = prepared['original_name'].rsplit('.', 1)[-1].lower()
            path = STAGING_PREFIX + datetime.now(timezone.utc).strftime('%Y/%m/%d/') + identifier + '.' + extension
            blob = gcs['client']().bucket(bucket).blob(path)
            blob.cache_control = 'private, no-store'
            blob.upload_from_string(data, content_type=prepared['content_type'], timeout=30,
                                    if_generation_match=0)
            # A commit can succeed even if its acknowledgement is lost. Do not
            # delete the object on an ambiguous DB failure after this point.
            # An unregistered orphan is preferable to a committed broken file.
            with db._get_cursor() as cursor:
                cursor.execute("""INSERT INTO wazzup_pilot_uploads
                    (id,account,channel_id,chat_id,user_id,client_upload_id,original_name,
                     content_type,message_type,file_size,sha256,bucket,blob_path)
                    VALUES (%s,'op',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(user_id,client_upload_id) DO NOTHING RETURNING id""",
                    (identifier, cid, chat, user[0], upload_id, prepared['original_name'],
                     prepared['content_type'], prepared['message_type'], prepared['file_size'],
                     prepared['sha256'], bucket, path))
                claimed = cursor.fetchone()
                cursor.execute(_SELECT + 'AND user_id=%s AND client_upload_id=%s', (user[0], upload_id))
                item = dict(zip(_FIELDS, cursor.fetchone()))
            if not claimed:
                uploaded = blob  # This concurrent retry lost to an existing row.
            _check_replay(item, prepared, cid, chat)
            _available(item)
            return jsonify(attachment=public_item(item)), 201 if claimed else 200, {'Cache-Control': 'private, no-store'}
        except UploadError as error:
            return jsonify(error=error.message, code=error.code), error.status
        except AttachmentError as error:
            return jsonify(error=error.message), error.status
        except RequestEntityTooLarge:
            return jsonify(error='Размер файла не должен превышать 10 МБ', code='ATTACHMENT_REJECTED'), 413
        except Exception:
            return jsonify(error='Не удалось загрузить файл. Попробуйте ещё раз.', code='ATTACHMENT_UPLOAD_FAILED'), 503
        finally:
            if uploaded is not None:
                try:
                    uploaded.delete(timeout=10, retry=None)
                except Exception:
                    pass
            if acquired:
                _slots.release()
            if incoming_files is not None:
                for _, file in incoming_files.items(multi=True):
                    file.close()


def _check_replay(item, prepared, channel_id, chat_id):
    if (item['channel_id'] != channel_id or item['chat_id'] != chat_id
            or any(item[key] != prepared[key] for key in
                   ('original_name', 'content_type', 'message_type', 'file_size', 'sha256'))):
        raise UploadError('Этот идентификатор уже использован для другого файла.', 409, 'UPLOAD_CONFLICT')
