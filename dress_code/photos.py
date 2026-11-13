# -*- coding: utf-8 -*-
"""Фото дресс-кода: проверка файла, пережатие, бакет, подписанные адреса.

Хранится JPEG без метаданных (EXIF снимается пережатием): фото лица — персональные
данные, лишнего в бакете не держим. Адрес для экрана — подпись v4 прямо в GCS, а не
своя ручка: на телефоне кука до <img> не доезжает (так же сделаны фото новостей и
посылок). Подпись короткая и кэшируется на процесс — иначе каждый ответ приносил бы
новый адрес и браузер перекачивал бы картинку.
"""

import io
import logging
import uuid
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_SIDE = 1600
MIN_SIDE = 240
JPEG_QUALITY = 85
PREFIX = 'dress_code'
_SIGN_MINUTES = 60
_RESIGN_BEFORE = timedelta(minutes=15)
_SIGNED = {}
_SIGNED_MAX = 2000


class PhotoError(Exception):
    def __init__(self, message, code='DRESS_CODE_PHOTO_REJECTED', status=400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def prepare(data):
    """Байты из формы → {'data', 'width', 'height'}: JPEG без EXIF, не больше MAX_SIDE."""
    if not data:
        raise PhotoError('Фото не пришло')
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoError('Фото больше 8 МБ')
    try:
        from PIL import Image, ImageOps
    except ImportError:      # pragma: no cover — окружение без Pillow
        raise PhotoError('Обработка фото сейчас недоступна', code='DRESS_CODE_PHOTO_OFF', status=503)
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ('JPEG', 'PNG', 'WEBP'):
                raise PhotoError('Подойдёт только снимок с камеры')
            if source.width * source.height > 40_000_000:
                raise PhotoError('Снимок слишком большой')
            image = ImageOps.exif_transpose(source)
            image = image.convert('RGB')
    except PhotoError:
        raise
    except Exception:
        raise PhotoError('Файл не открылся как фото')
    if min(image.size) < MIN_SIDE:
        raise PhotoError('Снимок слишком маленький — сфотографируйтесь камерой ещё раз')
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    image.save(out, 'JPEG', quality=JPEG_QUALITY, optimize=True)
    return {'data': out.getvalue(), 'width': image.width, 'height': image.height}


def blob_path(user_id, day):
    return '%s/%s/%s_%s.jpg' % (PREFIX, day.strftime('%Y/%m/%d'), int(user_id), uuid.uuid4().hex)


def upload(gcs, prepared, path):
    bucket = gcs['bucket_name']() if gcs and gcs.get('bucket_name') else None
    if not bucket:
        raise PhotoError('Хранилище фото не настроено', code='DRESS_CODE_STORAGE_OFF', status=503)
    blob = gcs['client']().bucket(bucket).blob(path)
    blob.cache_control = 'private, max-age=3600'
    blob.upload_from_string(prepared['data'], content_type='image/jpeg')
    return bucket


def drop(gcs, refs):
    """Удаляет блобы (best-effort, ПОСЛЕ фиксации транзакции). «Уже нет» — тоже успех."""
    refs = [(b, p) for b, p in refs or () if b and p]
    if not refs or not gcs or not gcs.get('client'):
        return 0
    try:
        client = gcs['client']()
    except Exception:
        log.warning('Дресс-код: клиент хранилища недоступен', exc_info=True)
        return 0
    removed = 0
    for bucket, path in refs:
        try:
            client.bucket(bucket).blob(path).delete()
            removed += 1
        except Exception as error:
            text = str(error).lower()
            if '404' in text or 'not found' in text or 'no such object' in text:
                continue
            log.warning('Дресс-код: блоб %s/%s не удалён', bucket, path, exc_info=True)
    return removed


def signed_url(gcs, bucket, path):
    """Подписанный адрес фото на час (с кэшем). None — подписать не вышло."""
    if not bucket or not path or not gcs or not gcs.get('client'):
        return None
    key = (bucket, path)
    now = datetime.utcnow()
    cached = _SIGNED.get(key)
    if cached and cached[1] - now > _RESIGN_BEFORE:
        return cached[0]
    try:
        url = gcs['client']().bucket(bucket).blob(path).generate_signed_url(
            version='v4', expiration=timedelta(minutes=_SIGN_MINUTES), method='GET',
            response_disposition='inline', response_type='image/jpeg')
    except Exception:
        log.warning('Дресс-код: подпись не собралась для %s/%s', bucket, path, exc_info=True)
        return None
    if len(_SIGNED) >= _SIGNED_MAX:
        _SIGNED.clear()
    _SIGNED[key] = (url, now + timedelta(minutes=_SIGN_MINUTES))
    return url


def read(gcs, bucket, path):
    """Байты фото из бакета — для повторной проверки ИИ фоном."""
    return gcs['client']().bucket(bucket).blob(path).download_as_bytes()
