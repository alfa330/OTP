# -*- coding: utf-8 -*-
"""Ролики в вики: какие файлы берём, где они лежат и как выглядит загрузка.

ЗАЧЕМ. Владелец 18.11.2026: «в вики нужно реализовать такую возможность,
чтобы можно было загружать ролики как статью». Картинки вики идут через наш
сервер (/api/wiki/upload: до 25 МБ, файл целиком в памяти единственного
инстанса Render), а ролик весит сотни мегабайт. Поэтому ролик едет тем же
путём, что материалы «Продакшна»: сервер открывает в хранилище сессию докачки,
браузер шлёт файл прямо туда, после обрыва продолжает с принятого места, а на
завершении сервер сверяет размер, CRC32C и подпись формата. Второй копии этого
кода нет: хранилищные функции и проверка формата берутся из task_production
(storage.py, materials.py — там же разбор, почему сессия открывается с Origin
и почему её адрес — ключ на предъявителя). Здесь — только своё для вики.

ЧТО ЗНАЧИТ «КАК СТАТЬЮ». Ролик — узел <video> в теле статьи, с постоянным
адресом /api/wiki/file/<id>, как у картинки. Поэтому история версий, права,
привязка файла к статье при сохранении (edit.link_content_files) и подпись
адресов на витрине (file_urls.py) работают для ролика без единой новой строки.
Тип статьи «Ролик» (schema.ARTICLE_TYPES) — подпись в списках, подборка в
оглавлении и фильтр поиска для статей, у которых ролик и есть содержание.

НЕЗАКОНЧЕННАЯ ЗАГРУЗКА. Строка wiki_files заводится при открытии сессии с
upload_state = 'uploading': докачке после закрытой вкладки нужно помнить адрес
сессии, а уборке брошенных загрузок — что убирать. Готовый ролик —
upload_state IS NULL, как все прежние файлы вики. Пока строка «едет», файл не
отдают ни ручка /file/<id>, ни подпись витрины: объекта в бакете ещё нет или он
неполный.

MKV и AVI не берём, хотя «Продакшн» их принимает: там их можно скачать, а в
вики ролик смотрят, и браузер таких не играет вовсе — статья открылась бы с
чёрным прямоугольником вместо ролика.
"""

import datetime
import os
import uuid

from task_production import materials as production_materials

UPLOADING = 'uploading'

# 2 ГБ — тот же предел, что у роликов «Продакшна» (согласовано владельцем
# 16.10.2026). Больше — уже исходник монтажа, а не ролик для просмотра.
MAX_VIDEO_BYTES = production_materials.MAX_MATERIAL_BYTES
MAX_POSTER_BYTES = production_materials.MAX_POSTER_BYTES
CHUNK_BYTES = production_materials.CHUNK_BYTES

# Расширение → тип содержимого. Тип ставим сами по расширению, а не верим
# браузеру: macOS для .mov отдаёт то video/quicktime, то пустую строку.
VIDEO_TYPES = {
    'mp4': 'video/mp4',
    'm4v': 'video/mp4',
    'mov': 'video/quicktime',
    'webm': 'video/webm',
}
UNPLAYABLE = frozenset({'mkv', 'avi', 'wmv', 'flv', 'mpg', 'mpeg', '3gp'})

# Брошенная загрузка: сессия GCS живёт неделю, после неё строку можно убрать.
STALE_DAYS = 8

TYPE_REFUSED = 'WIKI_VIDEO_TYPE'
TYPE_UNPLAYABLE = 'WIKI_VIDEO_UNPLAYABLE'
TOO_LARGE = 'WIKI_VIDEO_TOO_LARGE'
EMPTY = 'WIKI_VIDEO_EMPTY'
BAD_NAME = 'WIKI_VIDEO_NAME'

_MESSAGES = {
    TYPE_REFUSED: 'Это не ролик: подходят MP4, MOV и WebM',
    TYPE_UNPLAYABLE: 'Такой ролик браузер не покажет — сохраните его в MP4 и загрузите ещё раз',
    TOO_LARGE: 'Ролик больше 2 ГБ',
    EMPTY: 'Файл пустой',
    BAD_NAME: 'У файла нет имени',
}

# Папка роликов в бакете. WIKI_VIDEO_BLOB_PREFIX — для стенда: его файлы
# ложатся в свою папку и убираются одной командой, не смешиваясь с боевыми.
BLOB_ROOT = 'wiki/videos'


def error_message(code):
    return _MESSAGES.get(code, code)


def classify(name, size):
    """(имя для показа, расширение, тип содержимого) или ValueError с кодом."""
    try:
        clean = production_materials.clean_name(name)
    except ValueError:
        raise ValueError(BAD_NAME)
    ext = production_materials.extension_of(clean)
    if ext in UNPLAYABLE:
        raise ValueError(TYPE_UNPLAYABLE)
    if ext not in VIDEO_TYPES:
        raise ValueError(TYPE_REFUSED)
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise ValueError(EMPTY)
    if size <= 0:
        raise ValueError(EMPTY)
    if size > MAX_VIDEO_BYTES:
        raise ValueError(TOO_LARGE)
    return clean, ext, VIDEO_TYPES[ext]


def blob_path(file_id, ext, *, today=None, root=None):
    """Путь объекта: папка по месяцу и UUID строки. Имя файла в путь не идёт —
    оно бывает кириллическим и с пробелами, а показывается из original_name."""
    root = str(root or os.getenv('WIKI_VIDEO_BLOB_PREFIX') or BLOB_ROOT).strip().strip('/')
    day = today or datetime.date.today()
    return '%s/%04d/%02d/%s.%s' % (root or BLOB_ROOT, day.year, day.month, str(file_id), ext)


def title_from_name(name):
    """Название статьи из имени файла: «Как принять заказ.mp4» → «Как принять заказ».

    Подчёркивания монтажка и телефон ставят вместо пробелов («Как_принять_заказ»),
    их меняем на пробелы. Точки не трогаем: в имени это обычно дата («18.11»).
    """
    stem = str(name or '').rsplit('/', 1)[-1]
    if '.' in stem:
        stem = stem.rsplit('.', 1)[0]
    stem = stem.replace('_', ' ')
    return ' '.join(stem.split())[:200]


def positive_float(value, limit):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0 or number > limit:  # NaN и мусор — «не знаем»
        return None
    return round(number, 3)


def positive_int(value, limit):
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return None
    if number <= 0 or number > limit:
        return None
    return number


def file_url(file_id):
    """Постоянный адрес файла — тот же, что пишет wiki/storage.py для картинок."""
    return '/api/wiki/file/%s' % file_id if file_id else None


# ── Строки wiki_files ───────────────────────────────────────────────────────

_COLUMNS = ('id', 'article_id', 'bucket', 'blob_path', 'original_name', 'content_type',
            'file_size', 'width', 'height', 'uploaded_by', 'upload_state', 'upload_session',
            'upload_origin', 'client_fingerprint', 'duration_seconds', 'poster_file_id')
_SELECT = 'SELECT %s FROM wiki_files' % ', '.join(_COLUMNS)


def _row(values):
    if not values:
        return None
    row = dict(zip(_COLUMNS, values))
    for key in ('id', 'poster_file_id'):
        if row.get(key) is not None:
            row[key] = str(row[key])
    return row


def insert_upload(cursor, *, file_id, bucket, blob_path, name, content_type, size,
                  session_url, origin, uploaded_by, fingerprint=None):
    cursor.execute(
        """
        INSERT INTO wiki_files (id, article_id, bucket, blob_path, original_name, content_type,
                                file_size, uploaded_by, upload_state, upload_session,
                                upload_origin, client_fingerprint)
        VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (str(file_id), bucket, blob_path, name, content_type, int(size), int(uploaded_by),
         UPLOADING, session_url, origin, fingerprint or None),
    )


def upload_row(cursor, file_id, *, lock=False):
    """Строка ролика (любого состояния) или None. id не UUID — тоже None."""
    try:
        key = str(uuid.UUID(str(file_id)))
    except (TypeError, ValueError, AttributeError):
        return None
    cursor.execute(_SELECT + ' WHERE id = %s' + (' FOR UPDATE' if lock else ''), (key,))
    return _row(cursor.fetchone())


def pending_for(cursor, *, user_id, name, size, fingerprint=None, lock=False):
    """Незаконченная загрузка того же файла тем же человеком — для докачки после
    закрытой вкладки. Файл узнаём по имени, размеру и отпечатку браузера (дате
    изменения), как в «Продакшне»: другой файл с тем же именем и размером
    начнётся заново, а не допишется в чужую сессию.

    lock — строка берётся с блокировкой, и это не перестраховка. Отмена
    загрузки удаляет строку, а фиксирует удаление в конце своего запроса — уже
    после обращения к хранилищу (отменить сессию). Простое чтение в это окно
    видело бы ещё не удалённую строку, и новый старт того же файла продолжил бы
    сессию, которую в эту секунду отменяют (поймано на стенде 18.11.2026: вторая
    загрузка падала на «завершить» с «Загрузка не найдена»). С блокировкой
    старт ждёт, пока отмена договорит, и строки уже не находит.
    """
    cursor.execute(
        _SELECT + """
         WHERE uploaded_by = %s AND upload_state = %s
           AND original_name = %s AND file_size = %s
           AND COALESCE(client_fingerprint, '') = COALESCE(%s, '')
           AND created_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') - INTERVAL '6 days'
         ORDER BY created_at DESC
         LIMIT 1""" + (' FOR UPDATE' if lock else ''),
        (int(user_id), UPLOADING, name, int(size), fingerprint or None),
    )
    return _row(cursor.fetchone())


def mark_ready(cursor, file_id, *, duration, width, height):
    cursor.execute(
        """
        UPDATE wiki_files
           SET upload_state = NULL, upload_session = NULL, upload_origin = NULL,
               duration_seconds = %s, width = %s, height = %s
         WHERE id = %s
        """,
        (duration, width, height, str(file_id)),
    )


def set_poster(cursor, file_id, poster_file_id):
    cursor.execute('UPDATE wiki_files SET poster_file_id = %s WHERE id = %s',
                   (str(poster_file_id) if poster_file_id else None, str(file_id)))


def poster_row(cursor, poster_file_id):
    """Строка обложки — чтобы убрать её вместе с отменённой загрузкой."""
    if not poster_file_id:
        return None
    cursor.execute('SELECT id, bucket, blob_path, article_id FROM wiki_files WHERE id = %s',
                   (str(poster_file_id),))
    found = cursor.fetchone()
    if not found:
        return None
    return {'id': str(found[0]), 'bucket': found[1], 'blob_path': found[2], 'article_id': found[3]}


def delete_rows(cursor, file_ids):
    ids = [str(value) for value in (file_ids or ()) if value]
    if ids:
        cursor.execute('DELETE FROM wiki_files WHERE id = ANY(%s::uuid[])', (ids,))


def stale_uploads(cursor, *, limit=20):
    """Брошенные загрузки старше STALE_DAYS: сессия в хранилище уже истекла."""
    cursor.execute(
        """
        SELECT id, bucket, blob_path, poster_file_id FROM wiki_files
         WHERE upload_state IS NOT NULL
           AND created_at < (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') - (%s * INTERVAL '1 day')
         ORDER BY created_at
         LIMIT %s
        """,
        (int(STALE_DAYS), int(limit)),
    )
    return [{'id': str(r[0]), 'bucket': r[1], 'blob_path': r[2],
             'poster_file_id': str(r[3]) if r[3] else None} for r in cursor.fetchall()]


def describe(row):
    """Ответ о готовом ролике: из этого редактор собирает узел статьи."""
    return {
        'id': row['id'],
        'status': 'uploading' if row.get('upload_state') else 'ready',
        'url': file_url(row['id']),
        'poster_url': file_url(row.get('poster_file_id')),
        'name': row.get('original_name'),
        'title': title_from_name(row.get('original_name')),
        'content_type': row.get('content_type'),
        'size': int(row.get('file_size') or 0),
        'duration': row.get('duration_seconds'),
        'width': row.get('width'),
        'height': row.get('height'),
    }
