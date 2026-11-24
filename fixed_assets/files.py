# -*- coding: utf-8 -*-
"""Файлы раздела «Основные средства»: фото ОС и сканы подписанных актов.

Здесь только байты и бакет; SQL — в queries.py, коды ответов — в routes.py.

ФОТО. Проверка файла, перевод в WebP, миниатюра и подпись ссылок — те же помощники, что у
фотографий посылок и машин аренды (parcels/photos.py поверх wiki/images.py): правило «в
бакете лежит WebP» на портале одно, и второй экземпляр разошёлся бы с первым молча. Своё
здесь — префикс в бакете fixed_assets/photos/ (по нему видно, чьё это). Осечки общих
помощников в логе подписаны «Посылки:» — это они и есть.

СКАН АКТА — только PDF (ТЗ: «Скан подписанного акта (загрузка PDF)»). Тип проверяется по
первым байтам файла, а не по расширению: «акт.pdf» с картинкой внутри не пройдёт. Отдаётся
скан своей ручкой с обычной авторизацией, как вложения «Оплаты счетов».
"""

import logging
import os
import re
import uuid

from parcels import photos as shared
from payments import files as payment_files

from . import rules
from .queries import now_almaty

MAX_PHOTO_BYTES = shared.MAX_BYTES
PHOTO_TYPES = shared.PHOTO_TYPES
PhotoError = shared.PhotoError
sign_urls = shared.sign_urls
drop_blobs = shared.drop_blobs

PHOTO_PREFIX = 'fixed_assets/photos'
SCAN_PREFIX = 'fixed_assets/acts'
CACHE_CONTROL = 'private, max-age=3600'
PDF_TYPE = 'application/pdf'


def storage_ready(gcs):
    return payment_files.storage_ready(gcs)


def _bucket(gcs):
    bucket = gcs['bucket_name']() if gcs and gcs.get('bucket_name') else None
    if not bucket:
        raise rules.FaError('Хранилище файлов не настроено', code='FA_STORAGE_OFF', status=503)
    return bucket


def _photo_path(original_name, *, thumb=False):
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(str(original_name or 'photo')))[:80]
    if not safe or safe.startswith('.'):
        safe = 'photo' + (safe or '')
    day = now_almaty().strftime('%Y/%m/%d')
    return '%s/%s/%s%s_%s' % (PHOTO_PREFIX, day, uuid.uuid4().hex, '_thumb' if thumb else '', safe)


def prepare_photo(data, *, filename, content_type):
    """Файл из формы → WebP и миниатюра (без сети и базы). Отказ — словами для человека."""
    try:
        return shared.prepare(data, filename=filename, content_type=content_type)
    except shared.PhotoError as exc:
        raise rules.FaError('Фото «%s»: %s' % (payment_files.safe_name(filename, 'фото'), exc.message),
                            code='FA_PHOTO_REJECTED', status=exc.status) from exc


def upload_photo(gcs, prepared):
    """Кладёт кадр и миниатюру в бакет; возвращает строку для fa_files.

    Миниатюра — после полного кадра, и её осечка загрузку не отменяет: без миниатюры
    плитка покажет полный кадр."""
    bucket = _bucket(gcs)
    client = gcs['client']()
    blob_path = _photo_path(prepared['original_name'])
    blob = client.bucket(bucket).blob(blob_path)
    blob.cache_control = CACHE_CONTROL
    blob.upload_from_string(prepared['data'], content_type=prepared['content_type'] or 'application/octet-stream')
    thumb_path = None
    if prepared.get('thumb'):
        thumb_path = _photo_path(prepared['original_name'], thumb=True)
        try:
            thumb = client.bucket(bucket).blob(thumb_path)
            thumb.cache_control = CACHE_CONTROL
            thumb.upload_from_string(prepared['thumb'], content_type='image/webp')
        except Exception:  # noqa: BLE001 — см. докстринг
            logging.warning('fixed_assets: миниатюра не уехала в бакет', exc_info=True)
            thumb_path = None
    return {
        'bucket': bucket, 'blob_path': blob_path, 'thumb_blob_path': thumb_path,
        'file_name': prepared['original_name'], 'content_type': prepared['content_type'],
        'file_size': prepared['file_size'], 'width': prepared['width'], 'height': prepared['height'],
        'thumb_width': prepared.get('thumb_width'), 'thumb_height': prepared.get('thumb_height'),
    }


def check_scan(data, *, filename):
    """Скан подписанного акта: PDF до 15 МБ. Проверяем содержимое, а не имя файла."""
    name = payment_files.safe_name(filename, 'акт.pdf')
    if not data:
        raise rules.FaError('Файл «%s» пустой' % name, code='FA_SCAN_INVALID')
    if len(data) > rules.MAX_SCAN_BYTES:
        raise rules.FaError('Скан акта больше %d МБ' % (rules.MAX_SCAN_BYTES // (1024 * 1024)),
                            code='FA_SCAN_TOO_LARGE')
    if not bytes(data[:1024]).lstrip().startswith(b'%PDF-'):
        raise rules.FaError('Скан подписанного акта принимается в PDF. Отсканируйте акт в PDF '
                            '(в телефоне — «Сканировать документ»)', code='FA_SCAN_NOT_PDF')
    return name if name.lower().endswith('.pdf') else name + '.pdf'


def upload_scan(gcs, data, *, filename):
    bucket = _bucket(gcs)
    blob_path = '%s/%s/%s_%s' % (SCAN_PREFIX, now_almaty().strftime('%Y/%m/%d'), uuid.uuid4().hex,
                                 re.sub(r'[^A-Za-z0-9._-]+', '_', filename)[-80:] or 'act.pdf')
    gcs['client']().bucket(bucket).blob(blob_path).upload_from_string(data, content_type=PDF_TYPE)
    return {'bucket': bucket, 'blob_path': blob_path, 'thumb_blob_path': None, 'file_name': filename,
            'content_type': PDF_TYPE, 'file_size': len(data)}


def download(gcs, bucket, blob_path):
    return payment_files.download(gcs, bucket, blob_path)


def photo_views(gcs, rows):
    """Строки fa_files → то, что уходит экрану: подписанные адреса вместо путей бакета."""
    rows = list(rows or [])
    signed = sign_urls(gcs, rows)
    views = []
    for row, view in zip(rows, signed):
        views.append({
            'id': row['id'], 'asset_id': row.get('asset_id'), 'kind': row.get('kind'),
            'url': view['url'], 'thumb_url': view['thumb_url'],
            'width': row.get('width'), 'height': row.get('height'),
            'uploaded_by_name': row.get('uploaded_by_name'), 'created_at': row.get('created_at'),
        })
    return views
