# -*- coding: utf-8 -*-
"""Материалы «Продакшна» в облачном хранилище: загрузка из браузера и выдача.

КАК ЕДЕТ ФАЙЛ. Ролик весит до 2 ГБ, а сервер у портала один на всех
(Render, один инстанс): пропускать такие файлы через него значило бы держать
гигабайты в памяти и занимать поток на десятки минут. Поэтому сервер только
открывает в GCS «сессию докачки» (resumable upload) и отдаёт её адрес
браузеру, а браузер шлёт файл кусками прямо в хранилище. Оборвалась связь —
браузер спрашивает, сколько байт уже принято, и продолжает с этого места.

ПОЧЕМУ С ORIGIN. Сессию создаёт сервер, а куски шлёт страница с другого сайта
(GitHub Pages). Документация GCS: без заголовка Origin в запросе, который
открывает сессию, хранилище не добавит CORS-заголовки к ответам на
последующие запросы, и браузер их не пропустит. Поэтому сервер передаёт
Origin той страницы, что попросила загрузку, — и тем же Origin подписывает
свои служебные запросы к сессии (статус, отмена).

АДРЕС СЕССИИ — КЛЮЧ НА ПРЕДЪЯВИТЕЛЯ. Им можно дописать ровно этот объект:
путь выбирает сервер, размер зафиксирован при открытии (X-Upload-Content-Length),
перезапись существующего объекта запрещена условием ifGenerationMatch=0.
Отдаётся он только загружающему и только по HTTPS.

ПОДПИСИ. Смотрят материалы тоже прямо из хранилища, по подписанной ссылке —
как фото задач и картинки вики. Подпись v4 кладёт в адрес момент подписания,
поэтому одна и та же обложка без кэша получала бы новый адрес на каждом
обновлении списка и перекачивалась бы заново. Кэш здесь — ради кэша браузера.
"""

import logging
import re
import threading
import time
from datetime import timedelta

import requests

from . import materials

_RANGE_RE = re.compile(r'bytes=0-(\d+)')
_STATUS_TIMEOUT = 20

_SIGNED = {}
_SIGNED_MAX = 2000
_RESIGN_BEFORE_SECONDS = 30 * 60
_LOCK = threading.Lock()


class StorageUnavailable(Exception):
    """Хранилище не настроено или не ответило."""


def _cloud_errors():
    """Классы ошибок облака: занято (429/503), отказ прав, истёкший ключ, сеть.

    Без этого они падали в общий обработчик маршрута и становились «500,
    внутренняя ошибка» — человек не отличил бы «облако занято, повторите» от
    поломки, а докачка не повторила бы шаг.
    """
    found = [requests.RequestException, TimeoutError, ConnectionError]
    try:
        from google.api_core import exceptions as api_exceptions
        found.append(api_exceptions.GoogleAPIError)
    except Exception:  # noqa: BLE001 — без библиотеки остаются сетевые ошибки
        pass
    try:
        from google.auth import exceptions as auth_exceptions
        found.append(auth_exceptions.GoogleAuthError)
    except Exception:  # noqa: BLE001
        pass
    try:
        from google.resumable_media import common as media_common
        found.append(media_common.InvalidResponse)
    except Exception:  # noqa: BLE001
        pass
    return tuple(found)


_CLOUD_ERRORS = _cloud_errors()


def _cloud(fn):
    """Ошибку облака — в StorageUnavailable: маршрут ответит 503, а не 500."""
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except StorageUnavailable:
            raise
        except _CLOUD_ERRORS as exc:
            raise StorageUnavailable('%s: %s' % (exc.__class__.__name__, exc)) from exc
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


def bucket_of(gcs):
    name = gcs['bucket_name']() if gcs and callable(gcs.get('bucket_name')) else None
    if not name:
        raise StorageUnavailable('NO_BUCKET')
    return name


def client_of(gcs):
    factory = gcs.get('client') if gcs else None
    if not callable(factory):
        raise StorageUnavailable('NO_CLIENT')
    try:
        return factory()
    except Exception as exc:  # нет ключа или он битый — для человека это «хранилище недоступно»
        raise StorageUnavailable('CLIENT: %s' % exc.__class__.__name__) from exc


@_cloud
def open_session(client, bucket_name, blob_path, *, content_type, size, origin):
    """Адрес сессии докачки для одного объекта."""
    blob = client.bucket(bucket_name).blob(blob_path)
    # Материал после загрузки не меняется (новая версия — новый объект), так что
    # сутки кэша браузеру безопасны; private — файл не публичный.
    blob.cache_control = 'private, max-age=86400'
    return blob.create_resumable_upload_session(
        content_type=content_type,
        size=int(size),
        origin=origin or None,
        if_generation_match=0,
        timeout=30,
    )


def _session_headers(origin, extra=None):
    headers = {'Content-Length': '0'}
    if origin:
        headers['Origin'] = origin
    headers.update(extra or {})
    return headers


def session_offset(session_url, size, *, origin=None):
    """Сколько байт хранилище уже приняло.

    (offset, finished): finished — объект уже собран целиком. None — сессии
    больше нет (истекла через неделю или отменена): загрузку надо начинать
    заново. Запрос статуса — это PUT с пустым телом и Content-Range «*/размер».
    """
    try:
        response = requests.put(
            session_url,
            headers=_session_headers(origin, {'Content-Range': 'bytes */%d' % int(size)}),
            timeout=_STATUS_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageUnavailable(str(exc)) from exc
    if response.status_code in (200, 201):
        return int(size), True
    if response.status_code == 308:
        found = _RANGE_RE.search(response.headers.get('Range') or '')
        return (int(found.group(1)) + 1 if found else 0), False
    if response.status_code in (404, 410):
        return None
    raise StorageUnavailable('STATUS_%d' % response.status_code)


def cancel_session(session_url, *, origin=None):
    """Отменить незаконченную загрузку. Ответ 499 — штатный «отменено»."""
    try:
        requests.delete(session_url, headers=_session_headers(origin), timeout=_STATUS_TIMEOUT)
    except requests.RequestException:
        # Не страшно: сессия сама истечёт через неделю, данных под ней нет.
        logging.info('Продакшн: отмена сессии загрузки не дошла до хранилища', exc_info=True)


@_cloud
def object_info(client, bucket_name, blob_path):
    """Размер, CRC32C и тип собранного объекта; None — объекта нет."""
    blob = client.bucket(bucket_name).get_blob(blob_path, timeout=30)
    if blob is None:
        return None
    return {
        'size': int(blob.size or 0),
        'crc32c': blob.crc32c,
        'content_type': blob.content_type,
    }


@_cloud
def object_head(client, bucket_name, blob_path, length=4096):
    """Первые байты объекта — для проверки подписи формата."""
    blob = client.bucket(bucket_name).blob(blob_path)
    return blob.download_as_bytes(start=0, end=max(0, int(length) - 1), timeout=30)


@_cloud
def put_small(client, bucket_name, blob_path, data, content_type):
    blob = client.bucket(bucket_name).blob(blob_path)
    blob.cache_control = 'private, max-age=86400'
    blob.upload_from_string(data, content_type=content_type, timeout=60)


def delete_objects(client, bucket_name, blob_paths):
    """Удалить объекты; уже удалённые не считаются ошибкой."""
    bucket = client.bucket(bucket_name)
    for path in [p for p in (blob_paths or ()) if p]:
        try:
            bucket.blob(path).delete(timeout=30)
        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ == 'NotFound':
                continue
            logging.warning('Продакшн: не удалось удалить %s из хранилища: %s', path, exc)
    forget_signatures(blob_paths)


def _disposition(kind, name):
    if kind != 'download':
        return 'inline'
    # RFC 6266: имя в кириллице — через filename*, ASCII-замена — для старых клиентов.
    from urllib.parse import quote
    full = name or 'file'
    stem, dot, ext = full.rpartition('.')
    if not dot:
        stem, ext = full, ''
    # Запасное имя — для клиентов без filename*: у кириллического имени латиницы
    # не остаётся, и без «file» файл назывался бы «.mp4» — скрытым на Mac.
    ascii_stem = re.sub(r'[^A-Za-z0-9_-]+', '_', stem).strip('_') or 'file'
    ascii_ext = re.sub(r'[^A-Za-z0-9]+', '', ext)
    ascii_name = ascii_stem + ('.' + ascii_ext if ascii_ext else '')
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(full))


def sign(client, bucket_name, blob_path, *, minutes, purpose='view', name=None, content_type=None):
    """Подписанная ссылка GET. purpose='download' — с Content-Disposition: attachment."""
    key = (bucket_name, blob_path, purpose, name if purpose == 'download' else None)
    now = time.time()
    with _LOCK:
        cached = _SIGNED.get(key)
        if cached and cached[1] - now > _RESIGN_BEFORE_SECONDS:
            return cached[0]
    url = client.bucket(bucket_name).blob(blob_path).generate_signed_url(
        version='v4',
        expiration=timedelta(minutes=max(1, int(minutes))),
        method='GET',
        response_disposition=_disposition(purpose, name),
        response_type=content_type or None,
    )
    with _LOCK:
        if len(_SIGNED) >= _SIGNED_MAX:
            for stale in [k for k, v in _SIGNED.items() if v[1] <= now + _RESIGN_BEFORE_SECONDS]:
                _SIGNED.pop(stale, None)
            if len(_SIGNED) >= _SIGNED_MAX:
                for old in sorted(_SIGNED, key=lambda k: _SIGNED[k][1])[:_SIGNED_MAX // 10]:
                    _SIGNED.pop(old, None)
        _SIGNED[key] = (url, now + max(1, int(minutes)) * 60)
    return url


def forget_signatures(blob_paths):
    paths = set(p for p in (blob_paths or ()) if p)
    if not paths:
        return
    with _LOCK:
        for key in [k for k in _SIGNED if k[1] in paths]:
            _SIGNED.pop(key, None)


def sign_view(client, bucket_name, row):
    """Ссылки показа одного материала: обложка и сам файл (если браузер его покажет)."""
    out = {'poster_url': None, 'view_url': None}
    try:
        if row.get('poster_blob_path'):
            out['poster_url'] = sign(client, bucket_name, row['poster_blob_path'],
                                     minutes=materials.VIEW_SIGN_MINUTES, content_type='image/jpeg')
        if materials.can_preview(row.get('kind'), row.get('content_type')):
            out['view_url'] = sign(client, bucket_name, row['blob_path'],
                                   minutes=materials.VIEW_SIGN_MINUTES, content_type=row.get('content_type'))
    except Exception:  # noqa: BLE001 — без подписи плитка покажет значок вместо кадра
        logging.warning('Продакшн: подпись не собралась для %s', row.get('blob_path'), exc_info=True)
    return out
