"""Файлы заявки: счета, КП, платёжки, акты. Хранение в GCS, как у вложений задач.

Здесь нет Flask и базы: на вход — байты и имя, на выход — куда положили. Клиент
и имя бакета приходят фабрикой (`gcs={'bucket_name': fn, 'client': fn}`) —
тот же контракт, что у фотографий посылок и новостей.

Отдача — через свой роут `/api/payments/attachments/<id>/download` с обычной
авторизацией заголовками (как у вложений задач): счета и платёжки скачивают по
щелчку, миниатюр здесь нет, поэтому подписанные ссылки и их кэш не нужны.

Пределы — как у задач: 10 файлов за раз, 10 МБ каждый. Типы не ограничиваем
списком: счёт приходит PDF, сканом JPG, выгрузкой из 1С в XLSX, а КП — DOCX.
Опасные для браузера типы (html, svg, скрипты) не принимаем: файл потом
скачивают коллеги.
"""

import logging
import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

ALMATY = ZoneInfo('Asia/Almaty')

MAX_FILES = 10
MAX_BYTES = 10 * 1024 * 1024
PREFIX = 'payments/attachments'

_FORBIDDEN_EXT = {'html', 'htm', 'svg', 'js', 'mjs', 'exe', 'bat', 'cmd', 'sh', 'ps1', 'msi', 'com', 'scr'}
_FORBIDDEN_TYPES = {'text/html', 'image/svg+xml', 'application/javascript', 'text/javascript',
                    'application/x-msdownload', 'application/x-sh'}


class FileError(Exception):
    def __init__(self, message, code='PAYMENT_FILE_INVALID', status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def safe_name(filename, fallback='file'):
    """Имя для человека и для пути в бакете: без путей и управляющих символов,
    кириллица СОХРАНЯЕТСЯ (secure_filename её выбрасывает — «счёт.pdf» → «pdf»,
    ровно та ловушка, что у вложений задач)."""
    base = str(filename or '').replace('\\', '/').split('/')[-1].strip()
    base = re.sub(r'[\x00-\x1f\x7f"<>|*?:]', '', base).strip('. ')
    if not base:
        base = fallback
    return base[:200]


def extension(filename):
    name = str(filename or '')
    return name.rsplit('.', 1)[-1].lower() if '.' in name else ''


def check_file(*, filename, content_type, size):
    ext = extension(filename)
    if ext in _FORBIDDEN_EXT or (content_type or '').lower() in _FORBIDDEN_TYPES:
        raise FileError('Файл «%s» такого типа не принимается' % safe_name(filename))
    if size <= 0:
        raise FileError('Файл «%s» пустой' % safe_name(filename))
    if size > MAX_BYTES:
        raise FileError('Файл «%s» больше 10 МБ' % safe_name(filename), code='PAYMENT_FILE_TOO_LARGE')


def blob_path_for(filename):
    stamp = datetime.now(ALMATY).strftime('%Y/%m/%d')
    return '%s/%s/%s_%s' % (PREFIX, stamp, uuid.uuid4().hex, safe_name(filename))


def storage_ready(gcs):
    try:
        return bool(gcs and gcs.get('bucket_name') and gcs['bucket_name']())
    except Exception:  # noqa: BLE001
        return False


def upload(gcs, *, data, filename, content_type):
    """Кладёт файл в бакет. Возвращает (bucket, blob_path)."""
    bucket = gcs['bucket_name']() if gcs and gcs.get('bucket_name') else None
    if not bucket:
        raise FileError('Хранилище файлов не настроено', code='PAYMENT_STORAGE_OFF', status=503)
    client = gcs['client']()
    blob_path = blob_path_for(filename)
    blob = client.bucket(bucket).blob(blob_path)
    blob.upload_from_string(data, content_type=content_type or 'application/octet-stream')
    return bucket, blob_path


def download(gcs, bucket, blob_path):
    client = gcs['client']()
    blob = client.bucket(bucket).blob(blob_path)
    if not blob.exists():
        return None
    return blob.download_as_bytes()


def drop(gcs, refs):
    """Стирает блобы; ошибки только в лог — запись в базе уже удалена."""
    if not refs:
        return
    try:
        client = gcs['client']()
    except Exception:  # noqa: BLE001
        logging.warning('Оплата счетов: клиент хранилища недоступен, файлы не стёрты', exc_info=True)
        return
    for bucket, blob_path in refs:
        try:
            client.bucket(bucket).blob(blob_path).delete()
        except Exception:  # noqa: BLE001
            logging.warning('Оплата счетов: не удалось стереть %s/%s', bucket, blob_path, exc_info=True)
