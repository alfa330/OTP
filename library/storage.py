# -*- coding: utf-8 -*-
"""Файлы «Библиотеки» в бакете: книга, обложка, подписи обложек.

ОБЛОЖКА — ПОДПИСАННЫМ АДРЕСОМ ПРЯМО ИЗ GCS, как фото новостей и посылок
(шапка news/photos.py): тег <img> заголовков не шлёт, а куку на телефоне
кросс-сайтовый запрос с GitHub Pages на Render не приложит.

КНИГА — ЧЕРЕЗ РУЧКУ API, а не подписью. epub.js читает файл запросом fetch,
а не тегом, и ответ из бакета на чужой сайт браузер без CORS-настройки бакета
не отдаст странице. Ручка с заголовком авторизации работает везде, где работает
остальной портал; файл книги не меняется никогда (новая загрузка = новая
книга), поэтому ручка разрешает браузеру держать его в кэше, и повторное
открытие книги в сеть не ходит.

Модуль — лист: SQL живёт в queries.py, коды ответов — в routes.py.
"""

import logging
import os
import re
import uuid
from datetime import datetime, timedelta

_ALMATY_OFFSET = timedelta(hours=5)

# Обложка открывается карточкой каталога и шапкой ридера — час кэша браузера,
# чтобы список не перекачивал два десятка картинок на каждое переключение
# вкладки (адрес тот же благодаря кэшу подписей ниже).
_COVER_CACHE = 'private, max-age=3600'
_SIGN_MINUTES = 180
_RESIGN_BEFORE = timedelta(minutes=30)
_SIGNED = {}
_SIGNED_MAX = 4000


class StorageError(Exception):
    def __init__(self, message, code='LIBRARY_STORAGE_FAILED', status=503):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _now():
    return datetime.utcnow() + _ALMATY_OFFSET


def blob_path_for(kind, original_name):
    """Путь в бакете: свой префикс `library/`, раскладка по дате, как у новостей."""
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(str(original_name or kind)))[:80]
    if not safe or safe.startswith('.'):
        safe = kind + (safe or '')
    day = _now().strftime('%Y/%m/%d')
    return 'library/%s/%s/%s_%s' % (kind, day, uuid.uuid4().hex, safe)


def bucket_name(gcs):
    name = gcs['bucket_name']() if gcs and callable(gcs.get('bucket_name')) else None
    if not name:
        raise StorageError('Хранилище книг не настроено', code='LIBRARY_STORAGE_OFF')
    return name


def upload(gcs, bucket, blob_path, data, content_type, *, cache_control=None):
    client = gcs['client']()
    blob = client.bucket(bucket).blob(blob_path)
    if cache_control:
        blob.cache_control = cache_control
    blob.upload_from_string(data, content_type=content_type or 'application/octet-stream')


def upload_cover(gcs, bucket, blob_path, data, content_type):
    upload(gcs, bucket, blob_path, data, content_type, cache_control=_COVER_CACHE)


def download(gcs, bucket, blob_path):
    """Байты файла или None, если объекта нет."""
    client = gcs['client']()
    try:
        return client.bucket(bucket).blob(blob_path).download_as_bytes()
    except Exception as error:
        text = str(error).lower()
        if '404' in text or 'not found' in text or 'no such object' in text:
            return None
        raise


def drop_blobs(gcs, refs):
    """Убирает блобы после фиксации удаления. Best-effort, как в news/photos.py."""
    refs = [(bucket, path) for bucket, path in (refs or []) if bucket and path]
    if not refs or not gcs or not callable(gcs.get('client')):
        return 0
    try:
        client = gcs['client']()
    except Exception:
        logging.warning('Библиотека: не удалось получить клиент хранилища', exc_info=True)
        return 0
    removed = 0
    for bucket, blob_path in refs:
        try:
            client.bucket(bucket).blob(blob_path).delete()
            removed += 1
        except Exception as error:
            text = str(error).lower()
            if '404' in text or 'not found' in text or 'no such object' in text:
                continue
            logging.warning('Библиотека: блоб %s/%s не удалён', bucket, blob_path, exc_info=True)
    return removed


def cover_urls(gcs, rows, minutes=_SIGN_MINUTES):
    """{id книги: подписанный адрес обложки} для строк с обложкой.

    Клиент хранилища строится один раз на вызов и только если понадобился:
    у списка, все адреса которого уже в кэше, он не нужен вовсе, а построение
    стоит разбора приватного ключа RSA.
    """
    held = []

    def client_of():
        if not held:
            getter = gcs.get('client') if gcs else None
            try:
                held.append(getter() if callable(getter) else None)
            except Exception:
                logging.warning('Библиотека: клиент хранилища недоступен', exc_info=True)
                held.append(None)
        return held[0]

    out = {}
    now = _now()
    for row in rows or []:
        bucket, blob_path = row.get('bucket'), row.get('cover_blob')
        if not bucket or not blob_path:
            continue
        key = (bucket, blob_path)
        cached = _SIGNED.get(key)
        if cached and cached[1] - now > _RESIGN_BEFORE:
            out[row['id']] = cached[0]
            continue
        client = client_of()
        if client is None:
            continue
        try:
            url = client.bucket(bucket).blob(blob_path).generate_signed_url(
                version='v4',
                expiration=timedelta(minutes=minutes),
                method='GET',
                response_disposition='inline',
                response_type=row.get('cover_type') or 'image/webp',
            )
        except Exception:
            logging.warning('Библиотека: подпись обложки не собралась для %s/%s',
                            bucket, blob_path, exc_info=True)
            continue
        if len(_SIGNED) >= _SIGNED_MAX:
            _SIGNED.clear()
        _SIGNED[key] = (url, now + timedelta(minutes=minutes))
        out[row['id']] = url
    return out
