# -*- coding: utf-8 -*-
"""Файлы раздела «Дисциплина»: доказательства, объяснительные, акты, шаблон акта.

Хранение — GCS, как у вложений задач и «Оплаты счетов»; клиент и имя бакета
приходят фабрикой (`gcs={'bucket_name': fn, 'client': fn}`).

Что требует ТЗ (п. 8.1, 9, 13) и как это сделано:

* **Прямого доступа по ссылке нет** — только своя ручка скачивания с обычной
  авторизацией. Подписанные ссылки (как у фото задач) открывались бы без входа
  и мимо журнала просмотров, а ТЗ п. 14 требует знать, кто открывал файлы.
* **Файл хранится как есть, с хешем SHA-256.** Объяснительная и акт — документы
  с подписью: пережатие в WebP (как у фото задач) меняло бы подлинник.
* **Типы — белый список ТЗ по виду файла**, сверка расширения и сигнатуры.
  Антивируса в инфраструктуре портала нет — отступление от п. 13 названо в
  отчёте; опасные для браузера типы не принимаются вовсе, а отдаётся всё
  вложением с nosniff.
* **Ничего не удаляется**: новая версия того же вида закрывает прежнюю
  (superseded_at), ошибочный файл помечается недействительным с причиной.
"""

import hashlib
import logging
import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from . import catalog

ALMATY = ZoneInfo('Asia/Almaty')
PREFIX = 'discipline'

_CONTENT_TYPES = {
    'pdf': 'application/pdf',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'heic': 'image/heic',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'mp4': 'video/mp4',
}


_STRICT_SIGNATURE = ('pdf', 'jpg', 'jpeg', 'png', 'docx', 'mp4')


class FileError(Exception):
    def __init__(self, message, code='DISCIPLINE_FILE_INVALID', status=400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def safe_name(filename, fallback='file'):
    """Имя для человека и для пути: без путей и управляющих символов, кириллица
    СОХРАНЯЕТСЯ (secure_filename её выбрасывает: «акт.pdf» → «pdf»)."""
    base = str(filename or '').replace('\\', '/').split('/')[-1].strip()
    base = re.sub(r'[\x00-\x1f\x7f"<>|*?:]', '', base).strip('. ')
    return (base or fallback)[:200]


def extension(filename):
    name = str(filename or '')
    return name.rsplit('.', 1)[-1].lower() if '.' in name else ''


def _sniff(data):
    """Расширение по сигнатуре: файл с телефона нередко приходит без расширения."""
    head = bytes(data[:16] or b'')
    if head.startswith(b'%PDF'):
        return 'pdf'
    if head.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if head.startswith(b'\x89PNG'):
        return 'png'
    if head[4:8] == b'ftyp':
        brand = head[8:12]
        if brand in (b'heic', b'heix', b'mif1', b'msf1', b'heim', b'heis'):
            return 'heic'
        return 'mp4'
    if head.startswith(b'\xd0\xcf\x11\xe0'):
        return 'doc'
    if head.startswith(b'PK\x03\x04'):
        return 'docx'
    return ''


def check(kind, *, filename, data):
    """Проверка файла вида kind. Возвращает (имя, расширение, content_type)."""
    spec = catalog.FILE_KINDS.get(kind)
    if not spec:
        raise FileError('Неизвестный вид файла')
    name = safe_name(filename)
    ext = extension(name)
    sniffed = _sniff(data)
    if ext not in spec['ext']:
        if sniffed and sniffed in spec['ext']:
            ext = sniffed
            name = '%s.%s' % (name, ext) if not name.lower().endswith('.' + ext) else name
        else:
            allowed = ', '.join(e.upper() for e in spec['ext'] if e != 'jpeg')
            raise FileError('«%s»: для вида «%s» подходят %s' % (name, spec['label'].lower(), allowed))
    # Сигнатура не должна спорить с расширением: «акт.pdf», внутри которого
    # исполняемый файл, — не акт. У PDF, JPG, PNG, DOCX и MP4 сигнатура есть
    # всегда и обязана совпасть. Старый DOC и HEIC бывают с редкими заголовками —
    # им незнакомая сигнатура прощается, но не исполняемая.
    head = bytes((data or b'')[:4])
    if head.startswith(b'MZ') or head.startswith(b'\x7fELF') or head.startswith(b'#!'):
        raise FileError('«%s»: такой файл не принимается' % name)
    family = {'jpeg': 'jpg'}
    expected = family.get(ext, ext)
    if ext in _STRICT_SIGNATURE:
        if family.get(sniffed, sniffed) != expected:
            raise FileError('«%s»: содержимое не совпадает с расширением' % name)
    elif sniffed and sniffed != expected and not (ext == 'heic' and sniffed == 'mp4'):
        raise FileError('«%s»: содержимое не совпадает с расширением' % name)
    size = len(data or b'')
    if size <= 0:
        raise FileError('Файл «%s» пустой' % name)
    limit = spec.get('video_max_bytes') if ext == 'mp4' else spec['max_bytes']
    if size > limit:
        raise FileError('Файл «%s» больше %d МБ' % (name, limit // catalog.MB), code='DISCIPLINE_FILE_TOO_LARGE')
    return name, ext, _CONTENT_TYPES.get(ext, 'application/octet-stream')


def sha256(data):
    return hashlib.sha256(data or b'').hexdigest()


def blob_path_for(kind, filename):
    stamp = datetime.now(ALMATY).strftime('%Y/%m')
    return '%s/%s/%s/%s_%s' % (PREFIX, kind, stamp, uuid.uuid4().hex, safe_name(filename))


def storage_ready(gcs):
    try:
        return bool(gcs and gcs.get('bucket_name') and gcs['bucket_name']())
    except Exception:  # noqa: BLE001
        return False


def upload(gcs, *, kind, data, filename, content_type):
    """Кладёт файл в бакет. Возвращает (bucket, blob_path)."""
    bucket = gcs['bucket_name']() if gcs and gcs.get('bucket_name') else None
    if not bucket:
        raise FileError('Хранилище файлов не настроено', code='DISCIPLINE_STORAGE_OFF', status=503)
    client = gcs['client']()
    blob_path = blob_path_for(kind, filename)
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
    """Стирает блобы, которые не дошли до базы (откат транзакции после загрузки).
    Ошибки — только в лог."""
    if not refs:
        return
    try:
        client = gcs['client']()
    except Exception:  # noqa: BLE001
        logging.warning('Дисциплина: клиент хранилища недоступен, файлы не стёрты', exc_info=True)
        return
    for bucket, blob_path in refs:
        try:
            client.bucket(bucket).blob(blob_path).delete()
        except Exception:  # noqa: BLE001
            logging.warning('Дисциплина: не удалось стереть %s/%s', bucket, blob_path, exc_info=True)
