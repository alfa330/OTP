# -*- coding: utf-8 -*-
"""Файл обращения, которому пока некуда уйти.

Своих файлов раздел не хранит: вложение уходит в Telegram-группу следом за
обращением, а карточка достаёт его оттуда по file_id (routes.crm_ticket_attachment).
У обращения на проверке у супервайзера группы ещё нет — оно в неё не ушло и,
возможно, не уйдёт вовсе (schema.REVIEW_*). Файл, который оператор приложил к
такому обращению, лежит здесь, в бакете, пока супервайзер не решит:

    «Отправить в группу» — файл уходит в Telegram следом за обращением, как у
                           любого другого, и из бакета стирается;
    «Решено»             — остаётся при обращении: это часть того, с чем
                           обратились.

Здесь нет ни Flask, ни базы: на вход — байты и имя, на выход — ссылка. Клиент и
имя бакета приходят фабрикой (gcs={'bucket_name': fn, 'client': fn}) — тот же
контракт, что у файлов «Оплаты счетов» и фотографий посылок.

Ссылка — обычный адрес gs://бакет/путь — кладётся в то же поле, где у остальных
вложений лежит file_id Telegram (crm_ticket_messages.attachment_file_id). По
началу строки и видно, откуда файл доставать: у Telegram file_id с gs:// не
начинается никогда.
"""

import logging
import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

ALMATY = ZoneInfo('Asia/Almaty')

STORED_PREFIX = 'gs://'
PREFIX = 'crm/attachments'


class StorageError(Exception):
    """Файл не сохранён или не прочитан. Текст — для человека."""


def is_stored(file_id):
    """Лежит ли вложение у нас (а не в Telegram)."""
    return str(file_id or '').startswith(STORED_PREFIX)


def safe_name(filename, fallback='file'):
    """Имя для человека и для пути в бакете: без путей и управляющих символов,
    кириллица сохраняется (secure_filename её выбрасывает — «КП.pdf» → «pdf»)."""
    base = str(filename or '').replace('\\', '/').split('/')[-1].strip()
    base = re.sub(r'[\x00-\x1f\x7f"<>|*?:]', '', base).strip('. ')
    return (base or fallback)[:200]


def storage_ready(gcs):
    try:
        return bool(gcs and gcs.get('bucket_name') and gcs['bucket_name']())
    except Exception:  # noqa: BLE001
        return False


def _split(ref):
    """gs://бакет/путь → (бакет, путь). Не наша ссылка — (None, None)."""
    if not is_stored(ref):
        return None, None
    bucket, _, blob_path = str(ref)[len(STORED_PREFIX):].partition('/')
    return (bucket, blob_path) if bucket and blob_path else (None, None)


def store(gcs, *, data, filename, content_type=None):
    """Кладёт файл в бакет и возвращает ссылку gs://…

    StorageError — хранилище не настроено или не приняло файл: обращение с
    таким файлом заводить нельзя, иначе он пропал бы молча.
    """
    if not storage_ready(gcs):
        raise StorageError('Хранилище файлов не настроено')
    bucket = gcs['bucket_name']()
    blob_path = '%s/%s/%s_%s' % (PREFIX, datetime.now(ALMATY).strftime('%Y/%m/%d'),
                                 uuid.uuid4().hex, safe_name(filename))
    try:
        blob = gcs['client']().bucket(bucket).blob(blob_path)
        blob.upload_from_string(data, content_type=content_type or 'application/octet-stream')
    except Exception as error:  # noqa: BLE001
        logging.warning('crm: файл обращения не лёг в хранилище: %s', error)
        raise StorageError('Хранилище не приняло файл') from error
    return '%s%s/%s' % (STORED_PREFIX, bucket, blob_path)


def load(gcs, ref):
    """Содержимое файла по ссылке. None — файла нет или хранилище не ответило."""
    bucket, blob_path = _split(ref)
    if not bucket or not gcs or not gcs.get('client'):
        return None
    try:
        blob = gcs['client']().bucket(bucket).blob(blob_path)
        if not blob.exists():
            return None
        return blob.download_as_bytes()
    except Exception as error:  # noqa: BLE001
        logging.warning('crm: файл обращения %s не прочитан: %s', ref, error)
        return None


def drop(gcs, refs):
    """Стирает файлы; ошибки только в лог — строки о них в базе уже нет."""
    refs = [ref for ref in (refs or []) if is_stored(ref)]
    if not refs or not gcs or not gcs.get('client'):
        return
    try:
        client = gcs['client']()
    except Exception:  # noqa: BLE001
        logging.warning('crm: хранилище недоступно, файлы обращения не стёрты', exc_info=True)
        return
    for ref in refs:
        bucket, blob_path = _split(ref)
        if not bucket:
            continue
        try:
            client.bucket(bucket).blob(blob_path).delete()
        except Exception:  # noqa: BLE001
            logging.warning('crm: не удалось стереть %s', ref, exc_info=True)
