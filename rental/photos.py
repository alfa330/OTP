# -*- coding: utf-8 -*-
"""Фотографии машины в карточке «Наличия авто» (владелец 28.10.2026: «карточку с
фотографиями самих авто… возможность добавлять фото автомобиля»).

Здесь только байты и бакет; SQL — в queries.py, коды ответов — в routes.py. Проверка
файла, перевод в WebP, миниатюра, подпись ссылок и удаление блобов — те же помощники,
что у фотографий посылок (parcels/photos.py поверх wiki/images.py): правила «что
подойдёт» и «в бакете лежит WebP» на портале одни, и второй экземпляр разошёлся бы с
первым молча. Своё здесь — префикс в бакете rental/cars/ (по нему видно, чьё это и что
чистить) и предел снимков на карточку. Осечки общих помощников в логе подписаны
«Посылки:» — это они и есть.

Карточка — модель, а не конкретная машина: снимки показывают, как выглядит модель в
парке, и первый из них — обложка карточки.
"""

import logging
import os
import re
import uuid

from parcels import photos as shared

from .queries import now_almaty

# Снимков на карточку: обложка и несколько ракурсов; тридцать кадров одной модели никому
# не нужны. Проверяется и здесь, и в форме.
MAX_PER_CARD = 6

MAX_BYTES = shared.MAX_BYTES
PHOTO_TYPES = shared.PHOTO_TYPES
PhotoError = shared.PhotoError
photo_issue = shared.photo_issue
prepare = shared.prepare
sign_urls = shared.sign_urls
drop_blobs = shared.drop_blobs

# Что говорим браузеру о сроке годности файла — как у посылок: без явного значения
# непубличный объект GCS отдаётся с max-age=0 и перекачивается при каждом открытии.
CACHE_CONTROL = 'private, max-age=3600'


def blob_path_for(original_name, *, thumb=False):
    """Путь в бакете: rental/cars/ГГГГ/ММ/ДД/<uuid>[_thumb]_<имя>. Дата — алматинская."""
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(str(original_name or 'car')))[:80]
    if not safe or safe.startswith('.'):
        safe = 'car' + (safe or '')
    day = now_almaty().strftime('%Y/%m/%d')
    return 'rental/cars/%s/%s%s_%s' % (day, uuid.uuid4().hex, '_thumb' if thumb else '', safe)


def upload(gcs, prepared):
    """Кладёт кадр и миниатюру в бакет. Возвращает (bucket, blob_path, thumb_path).

    Миниатюра — после полного кадра, и её осечка загрузку не отменяет: без миниатюры
    карточка покажет полный кадр, без полного кадра показывать нечего.
    """
    bucket = gcs['bucket_name']() if gcs.get('bucket_name') else None
    if not bucket:
        raise PhotoError('Хранилище файлов не настроено', code='RENTAL_PHOTO_STORAGE_OFF', status=503)
    client = gcs['client']()
    blob_path = blob_path_for(prepared['original_name'])
    blob = client.bucket(bucket).blob(blob_path)
    blob.cache_control = CACHE_CONTROL
    blob.upload_from_string(prepared['data'], content_type=prepared['content_type'] or 'application/octet-stream')
    thumb_path = None
    if prepared.get('thumb'):
        thumb_path = blob_path_for(prepared['original_name'], thumb=True)
        try:
            thumb = client.bucket(bucket).blob(thumb_path)
            thumb.cache_control = CACHE_CONTROL
            thumb.upload_from_string(prepared['thumb'], content_type='image/webp')
        except Exception:  # noqa: BLE001 — см. докстринг
            logging.warning('rental: миниатюра не уехала в бакет', exc_info=True)
            thumb_path = None
    return bucket, blob_path, thumb_path
