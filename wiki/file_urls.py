# -*- coding: utf-8 -*-
"""Адреса картинок вики для тега <img>: подпись из бакета вместо ручки раздела.

ЗАЧЕМ ЭТОТ МОДУЛЬ ПОЯВИЛСЯ.

Картинка статьи и логотип парка лежат в бакете, а в теле статьи и в ответах
справочника стоит постоянный адрес `/api/wiki/file/<id>` (wiki/storage.py).
Ручка проверяет доступ и отвечает редиректом на подпись — адрес в статье живёт
годами, подпись выдаётся на каждый запрос. На компьютере это работает: тег
<img> заголовков не отправляет, но КУКУ сессии несёт, и ручка видит, кто пришёл.

На телефоне куки нет. `_cookie_options` (bot_schedule2.py) понижает мобильному
UA SameSite до Lax и гасит Partitioned, а страница отдаётся с GitHub Pages,
API — с Render: сайты разные, и кука с Lax в такой запрос не кладётся — браузер
её в кросс-сайтовом ответе даже не сохранит. Замер на боевом 11.09.2026:

    POST /api/login, UA iPhone → Set-Cookie: ...; Secure; SameSite=Lax
    POST /api/login, UA macOS  → Set-Cookie: ...; Secure; SameSite=None; Partitioned
    GET  /api/wiki/file/<id> без куки и без заголовка → 401 Unauthorized

Отсюда и жалоба «на телефоне не видно НЕКОТОРЫХ фоток»: пропадали ровно те
картинки, что лежат в бакете, — все логотипы парков и всё, что загружено в
статью через редактор или импорт. Картинки, перенесённые из старой вики в виде
base64 (по комментарию в wiki/sanitize.py — 81 % объёма контента), оставались на
месте: они часть текста, и никакого запроса за ними не идёт.

ПОЧЕМУ ЧИНИМ ПОДПИСЬЮ, А НЕ КУКОЙ. Вернуть мобильному UA SameSite=None
недостаточно: Safari режет сторонние куки сам (ITP), а вебвью — отдельная
история, и проверить это можно только на чужих телефонах, то есть никогда.
Подписанный адрес идёт ПРЯМО в GCS и не зависит ни от кук, ни от браузера.
Тот же вывод в проекте уже записан дважды — news/photos.py (фото объявления) и
wiki/routes_offices.py (тайлы карты); аватарки сотрудников так живут с самого
начала (_build_avatar_signed_url).

Честная оговорка та же, что у новостей: подписанный адрес можно переслать, и до
конца срока подписи он откроется у кого угодно. Тем же ответом уже уезжает весь
текст статьи, переслать который не сложнее.

ЧЕГО ЗДЕСЬ НЕТ — ПРОВЕРКИ ДОСТУПА. Модуль подписывает то, что ему дали, и
границу не считает: её обязан посчитать вызывающий — теми же правилами, по
которым отвечает ручка /file/<id> (wiki/routes_articles.py). Это не упрощение:
правило у статьи и у справочника разное (периметр статей против пространства
парка), и второй его экземпляр здесь разошёлся бы с первым молча. Тест
tests/test_wiki_file_urls.py сторожит ровно это — что роут не подписывает файл,
которого читателю не видно.

SQL тоже не здесь (wiki/articles.py: files_for_display) — модуль остаётся
листом: re, time, threading и ничего больше.
"""

import logging
import re
import threading
import time

# Ссылка на файл раздела: /api/wiki/file/<uuid>. ЕДИНСТВЕННОЕ её описание в
# проекте — wiki/edit.py берёт эту же регулярку, чтобы привязка файлов к статье
# и подстановка адресов не разошлись в понимании того, что такое ссылка.
FILE_REF = re.compile(
    r'/api/wiki/file/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
    re.I)

# СРОК ПОДПИСИ. Три часа, как у фотографий новостей, и по той же причине:
# статью с инструкцией держат открытой долго, а на исходе подписи картинки
# превратились бы в пустые рамки без единой ошибки на экране. Час (столько
# берёт ручка /file/<id>) для открытой вкладки мало.
SIGN_MINUTES = 180

# За сколько до конца переподписываем. Не в упор: между выдачей адреса и
# загрузкой картинки проходит время, а у страницы, открытой перед самым
# истечением, оно ещё и складывается со временем чтения.
_RESIGN_BEFORE_SECONDS = 30 * 60

# Уже выданные этим процессом подписи: {(bucket, blob_path): (адрес, до когда)}.
#
# Кэш тут не ради экономии криптографии, а ради КЭША БРАУЗЕРА и ради клиента
# GCS. Подпись v4 кладёт в адрес момент подписания, поэтому каждый вызов даёт
# ДРУГУЮ строку: без кэша одна и та же картинка перекачивалась бы при каждом
# открытии статьи. А get_gcs_client() не мемоизирован (bot_schedule2.py) — на
# каждый вызов это разбор учётных данных и приватного ключа RSA, и статья с
# двадцатью кадрами галереи платила бы за него двадцать раз.
_SIGNED = {}
_SIGNED_MAX = 4000
_LOCK = threading.Lock()


def ids_in(html):
    """Идентификаторы файлов, на которые ссылается разметка. Без повторов.

    Порядок сохраняем — не ради показа, а ради тестов и журналов: список,
    который меняется от запуска к запуску, нечем сравнивать.
    """
    seen = []
    known = set()
    for found in FILE_REF.findall(str(html or '')):
        key = found.lower()
        if key in known:
            continue
        known.add(key)
        seen.append(key)
    return seen


def _cached(bucket, blob_path, now):
    key = (bucket, blob_path)
    with _LOCK:
        found = _SIGNED.get(key)
        if found and found[1] - now > _RESIGN_BEFORE_SECONDS:
            return found[0]
    return None


def _remember(bucket, blob_path, url, until):
    with _LOCK:
        if len(_SIGNED) >= _SIGNED_MAX:
            now = time.time()
            for stale in [k for k, v in _SIGNED.items() if v[1] <= now]:
                _SIGNED.pop(stale, None)
            if len(_SIGNED) >= _SIGNED_MAX:
                # Протухшего не осталось, а место нужно: чистим самые ранние.
                # Сносить весь кэш ради одного места значило бы уронить кэш
                # браузера у всех читателей разом.
                oldest = sorted(_SIGNED.items(), key=lambda item: item[1][1])
                for key, _value in oldest[:max(1, _SIGNED_MAX // 10)]:
                    _SIGNED.pop(key, None)
        _SIGNED[(bucket, blob_path)] = (url, until)


def sign_files(gcs, rows, minutes=SIGN_MINUTES):
    """Строки файлов → {id файла: подписанный адрес}.

    rows — то, что вернул wiki/articles.py: id, bucket, blob_path, content_type.
    ДОСТУП УЖЕ ПРОВЕРЕН ВЫЗЫВАЮЩИМ (см. шапку модуля).

    Файл, который не подписался (нет бакета, нет ключа, экзотическая ошибка
    клиента), из карты ВЫПАДАЕТ, а не ломает ответ: витрина на такой id
    подставит прежний адрес ручки /file/<id>, то есть станет ровно тем, чем
    была до этой правки, — вместо пустой статьи.
    """
    sign = gcs.get('signed_url') if isinstance(gcs, dict) else None
    if not callable(sign) or not rows:
        return {}

    now = time.time()
    urls = {}
    for row in rows:
        bucket = str((row or {}).get('bucket') or '').strip()
        blob_path = str((row or {}).get('blob_path') or '').strip()
        file_id = str((row or {}).get('id') or '').strip().lower()
        if not bucket or not blob_path or not file_id:
            continue

        url = _cached(bucket, blob_path, now)
        if not url:
            try:
                url = sign(bucket, blob_path,
                           expires_minutes=minutes,
                           response_disposition='inline',
                           response_type=row.get('content_type') or None)
            except Exception:
                # Свой warning, а не молчание: отличить «нет приватного ключа»
                # от «опечатка в имени бакета» иначе будет нечем — на экране
                # оба случая выглядят одинаково пустой рамкой.
                logging.warning('Вики: подпись не собралась для %s/%s',
                                bucket, blob_path, exc_info=True)
                url = None
            if url:
                _remember(bucket, blob_path, url, now + max(1, int(minutes)) * 60)
        if url:
            urls[file_id] = url
    return urls
