"""Файлы отправления: накладная, подтверждение передачи и доставки, фото и акт расхождения.

Хранение — бакет, как у «Оплаты счетов» (`gcs={'bucket_name': fn, 'client': fn}`), своя
только папка. Проверка файла и безопасное имя — те же функции, что у «Оплаты счетов»
(payments/files.py): кириллица в имени сохраняется, опасные для браузера типы не
принимаются, 10 МБ на файл. Вторая копия этих правил разошлась бы с первой.
"""

import uuid

from payments import files as payment_files

from . import rules

MAX_FILES = payment_files.MAX_FILES
MAX_BYTES = payment_files.MAX_BYTES
PREFIX = 'mail/files'

safe_name = payment_files.safe_name


def check_file(*, filename, content_type, size):
    try:
        payment_files.check_file(filename=filename, content_type=content_type, size=size)
    except payment_files.FileError as exc:
        raise rules.MailError(str(exc), code='MAIL_FILE_INVALID', status=exc.status) from exc


def storage_ready(gcs):
    return payment_files.storage_ready(gcs)


def upload(gcs, *, data, filename, content_type, now):
    bucket = gcs['bucket_name']() if gcs and gcs.get('bucket_name') else None
    if not bucket:
        raise rules.MailError('Хранилище файлов не настроено', code='MAIL_STORAGE_OFF', status=503)
    blob_path = '%s/%s/%s_%s' % (PREFIX, now.strftime('%Y/%m/%d'), uuid.uuid4().hex, safe_name(filename))
    client = gcs['client']()
    client.bucket(bucket).blob(blob_path).upload_from_string(data, content_type=content_type
                                                             or 'application/octet-stream')
    return bucket, blob_path


def download(gcs, bucket, blob_path):
    return payment_files.download(gcs, bucket, blob_path)


def drop(gcs, refs):
    """Стирает уже загруженное, если запись в базу не удалась — файл без карточки никому не нужен."""
    payment_files.drop(gcs, refs)
