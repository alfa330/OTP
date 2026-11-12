# -*- coding: utf-8 -*-
"""Файлы запросов: фото, видео и документы, которые оператор прикладывает к запросу.

Лежат у нас, в бакете, и в группу уходят оттуда. Одним Telegram обойтись нельзя: бот
отдаёт обратно файлы до 20 МБ, а видео с экрана водителя бывает больше — оператор не
смог бы открыть собственное вложение. Файл, который прислали В ОТВЕТЕ из группы, наоборот,
остаётся в Telegram (file_id): его прислали туда, а не нам, и карточка достаёт его по
запросу (routes.request_file).

Здесь нет ни Flask, ни базы: на вход — байты и имя, на выход — ссылка gs://бакет/путь.
Клиент и имя бакета приходят фабрикой (gcs={'bucket_name': fn, 'client': fn}) — тот же
контракт, что у «Обращений» и фотографий посылок.
"""

import logging
import os
import re
import uuid
from datetime import timedelta

from . import rules

STORED_PREFIX = 'gs://'
PREFIX = 'client_cards/requests'
SIGNED_URL_MINUTES = 60

KIND_PHOTO = 'photo'
KIND_VIDEO = 'video'
KIND_DOCUMENT = 'document'

_PHOTO_EXT = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic')
_VIDEO_EXT = ('.mp4', '.mov', '.m4v', '.webm', '.avi', '.mkv', '.3gp')


class StorageError(Exception):
    """Файл не сохранён. Текст — для человека."""


def is_stored(ref):
    """Лежит ли файл у нас (а не в Telegram): у file_id Telegram такого начала не бывает."""
    return str(ref or '').startswith(STORED_PREFIX)


def safe_name(filename, fallback='file'):
    """Имя для человека и для пути в бакете: без путей и управляющих символов, кириллица
    остаётся (secure_filename её выбрасывает — «Скрин.png» → «png»)."""
    base = str(filename or '').replace('\\', '/').split('/')[-1].strip()
    base = re.sub(r'[\x00-\x1f\x7f"<>|*?:]', '', base).strip('. ')
    return (base or fallback)[:200]


def file_kind(filename, content_type):
    """Фото, видео или документ — по типу, а без него по расширению."""
    mime = str(content_type or '').lower()
    ext = os.path.splitext(str(filename or ''))[1].lower()
    if mime.startswith('image/') or (not mime.startswith('video/') and ext in _PHOTO_EXT):
        return KIND_PHOTO
    if mime.startswith('video/') or ext in _VIDEO_EXT:
        return KIND_VIDEO
    return KIND_DOCUMENT


def storage_ready(gcs):
    try:
        return bool(gcs and gcs.get('bucket_name') and gcs['bucket_name']())
    except Exception:  # noqa: BLE001
        return False


def _split(ref):
    if not is_stored(ref):
        return None, None
    bucket, _, blob_path = str(ref)[len(STORED_PREFIX):].partition('/')
    return (bucket, blob_path) if bucket and blob_path else (None, None)


def store(gcs, *, data, filename, content_type=None):
    """Кладёт файл в бакет и возвращает ссылку gs://… StorageError — не лёг: запрос с таким
    файлом отправлять нельзя, иначе он пропал бы молча."""
    if not storage_ready(gcs):
        raise StorageError('Хранилище файлов не настроено')
    bucket = gcs['bucket_name']()
    blob_path = '%s/%s/%s_%s' % (PREFIX, rules.now_almaty().strftime('%Y/%m/%d'),
                                 uuid.uuid4().hex, safe_name(filename))
    try:
        blob = gcs['client']().bucket(bucket).blob(blob_path)
        blob.upload_from_string(data, content_type=content_type or 'application/octet-stream')
    except Exception as error:  # noqa: BLE001
        logging.warning('client_cards: файл запроса не лёг в хранилище: %s', error)
        raise StorageError('Хранилище не приняло файл') from error
    return '%s%s/%s' % (STORED_PREFIX, bucket, blob_path)


def load(gcs, ref):
    """Содержимое файла. None — файла нет или хранилище не ответило."""
    bucket, blob_path = _split(ref)
    if not bucket or not gcs or not gcs.get('client'):
        return None
    try:
        blob = gcs['client']().bucket(bucket).blob(blob_path)
        if not blob.exists():
            return None
        return blob.download_as_bytes()
    except Exception as error:  # noqa: BLE001
        logging.warning('client_cards: файл запроса %s не прочитан: %s', ref, error)
        return None


def signed_url(gcs, ref, *, content_type=None, filename=None, minutes=SIGNED_URL_MINUTES):
    """Временная ссылка на файл — браузер качает его прямо из хранилища (видео с перемоткой).
    None — подписать не вышло."""
    bucket, blob_path = _split(ref)
    if not bucket or not gcs or not gcs.get('client'):
        return None
    try:
        disposition = 'inline'
        if filename:
            # Имя без кавычек и переводов строк; кириллицу подпись хранилища не любит.
            ascii_name = re.sub(r'[^A-Za-z0-9._-]+', '_', safe_name(filename)) or 'file'
            disposition = 'inline; filename="%s"' % ascii_name
        return gcs['client']().bucket(bucket).blob(blob_path).generate_signed_url(
            version='v4', expiration=timedelta(minutes=max(1, int(minutes))), method='GET',
            response_disposition=disposition,
            response_type=content_type or 'application/octet-stream')
    except Exception:  # noqa: BLE001
        logging.warning('client_cards: подпись не собралась для %s', ref, exc_info=True)
        return None


def drop(gcs, refs):
    """Стирает файлы; ошибки только в лог — строк о них в базе уже нет."""
    refs = [ref for ref in (refs or []) if is_stored(ref)]
    if not refs or not gcs or not gcs.get('client'):
        return
    try:
        client = gcs['client']()
    except Exception:  # noqa: BLE001
        logging.warning('client_cards: хранилище недоступно, файлы запроса не стёрты', exc_info=True)
        return
    for ref in refs:
        bucket, blob_path = _split(ref)
        if not bucket:
            continue
        try:
            client.bucket(bucket).blob(blob_path).delete()
        except Exception:  # noqa: BLE001
            logging.warning('client_cards: не удалось стереть %s', ref, exc_info=True)
