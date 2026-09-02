# -*- coding: utf-8 -*-
"""Фотографии объявления: проверка файла, перевод в WebP, бакет, подписи.

СВОЕГО РОУТА ОТДАЧИ У КАДРОВ НЕТ, И ЭТО РЕШЕНИЕ, А НЕ ПРОПУСК.

Тег <img> не отправляет заголовков — значит такой роут пришлось бы авторизовать
кукой. А `_cookie_options` (bot_schedule2.py) понижает мобильному UA SameSite до
Lax и гасит Partitioned: кросс-сайтовый запрос со страницы на GitHub Pages к API
на Render эту куку не приложит. Картинка молча пропала бы ровно у тех, ради кого
раздел и выносили из вики, — у операторов с телефона. Тот же довод уже записан у
тайлов карты (wiki/routes_offices.py).

Поэтому браузер идёт ПРЯМО в GCS по подписи v4, а границей служит то, что
человек эту подпись получил: её выдают только в ответе на запрос, прошедший
AUDIENCE_MATCH_FOR_VIEWER. Честная оговорка: подписанный адрес пересылается —
адресат может отдать его кому угодно на срок подписи. Выбор здесь не между
«безопасно» и «удобно»: тем же ответом уже уезжает весь текст объявления, и
переслать его не сложнее, — а прокси-роут, который якобы это чинит, ломает
доставку всем, кто сидит в вебвью. Срок подписи и есть ширина этого окна, и
поднимать его «на всю смену» можно только с новым замером.

ЧТО ЗДЕСЬ ЕСТЬ И ЧЕГО НЕТ. Только байты и бакет. SQL живёт в queries.py, коды
ответов — в routes.py. Разделение то же, что у посылок и у вики.

ПЕРЕВОД В WEBP БЕРЁТСЯ ГОТОВЫЙ — wiki/images.py, и это не «залезли в чужой
пакет»: модуль намеренно сделан листом (io/logging/os, ни Flask, ни базы), и в
нём уже решены поворот из EXIF, перенос ICC, прозрачность палитры PNG, отказ на
кадре больше 25 мегапикселей и правило «пережали, а стало тяжелее — значит
испортили». А вот wiki.storage сюда тащить НЕЛЬЗЯ: он пишет в wiki_files и
возвращает адрес /api/wiki/file/<id>, то есть ручку за двумя дверями вики.

МИНИАТЮРЫ НЕТ, в отличие от посылок. Там кадры показывают сеткой плиток, и
десять полных кадров ради десяти плиток — это мегабайты на каждое открытие
карточки. Здесь читатель видит кадр ОДИН и во всю ширину окна, а плитки в форме
автора берутся из локального URL.createObjectURL сразу после выбора файла.
Вторая пара блобов и вторая подпись ради одного редкого экрана — плата без
покупателя.

ИЗВЕСТНАЯ МИНА: удаление новости уносит строки каскадом (ON DELETE CASCADE), но
НЕ блобы — обработчик удаления работает внутри курсора и не может снести блоб
после фиксации. Смягчено тем, что удалять можно только ни разу не выпущенное.
Сборщика сирот в проекте нет ни у одного раздела; заводить его в рамках этой
задачи не стали, но записать — записали.
"""

import io
import logging
import os
import re
import uuid
from datetime import datetime, timedelta

from wiki import images as wiki_images

# Своё время, а не импорт из news.queries: тот модуль не лист (тянет psycopg2), а
# этот обязан оставаться проверяемым без базы. Две строки дешевле связи.
_ALMATY_OFFSET = timedelta(hours=5)


def _now():
    return datetime.utcnow() + _ALMATY_OFFSET


# Что принимаем. Тот же список, что в форме (src/components/parcels/parcelPhoto.js:
# PHOTO_TYPES) — расхождение читалось бы как «форма приняла, а сервер отказал».
#
# HEIC с айфона сюда не входит НАМЕРЕННО: Pillow без плагина pillow-heif его не
# открывает, и такой файл лёг бы в бакет мёртвым грузом. Форма переводит снимок
# в WebP ещё до отправки, а не смогла — отказывает с понятной причиной.
PHOTO_TYPES = ('image/jpeg', 'image/png', 'image/webp', 'image/gif')

# Вес одного ИСХОДНОГО файла. 20 МБ — кадр с телефона в максимальном качестве с
# запасом; форма ужимает его до сотен килобайт ещё до отправки, так что предел
# сработает только на файле, присланном мимо интерфейса.
MAX_BYTES = 20 * 1024 * 1024

# Что говорим браузеру о сроке годности кадра. Без явного значения GCS отдаёт
# непубличный объект с max-age=0, и десять кадров перекачивались бы на каждый
# тычок канала колокола — даже когда адрес тот же (о том, что он тот же,
# заботится кэш подписей ниже).
_CACHE_CONTROL = 'private, max-age=3600'

# СРОК ПОДПИСИ. Три часа, а не час, как у посылок: карточку посылки открывают и
# закрывают, а обязательное окно новости человек может оставить открытым надолго
# — и на исходе подписи все кадры превратились бы в битые иконки без единой
# ошибки в консоли. Переподписываем за полчаса до конца, а не в упор.
_SIGN_MINUTES = 180
_RESIGN_BEFORE = timedelta(minutes=30)

# Уже выданные этим процессом подписи: {(bucket, blob_path): (адрес, до когда)}.
#
# Кэш здесь не ради экономии криптографии, а ради КЭША БРАУЗЕРА. Подпись v4
# кладёт в адрес момент подписания, поэтому каждый новый вызов даёт ДРУГУЮ
# строку. Без кэша ответ /pending на каждый тычок колокола приносил бы новые
# адреса, <img> считал бы кадры новыми, и карусель отматывалась бы на первый
# кадр ровно тогда, когда человек смотрит пятый.
#
# Заодно снимается и цена: get_gcs_client() НЕ мемоизирован — на каждый вызов
# json.loads учётных данных и разбор приватного ключа RSA.
_SIGNED = {}
_SIGNED_MAX = 4000


class PhotoError(Exception):
    """Отказ, который можно показать человеку: текст, код и статус ответа."""

    def __init__(self, message, code='NEWS_PHOTO_REJECTED', status=400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def photo_issue(*, filename, content_type, size):
    """Что не так с файлом, или None. Строкой — отказ без причины читается как поломка."""
    kind = str(content_type or '').strip().lower().split(';')[0]
    if not kind:
        return 'Не удалось определить формат файла'
    if kind not in PHOTO_TYPES:
        if kind in ('image/heic', 'image/heif'):
            # Отдельная строка: общий отказ заставил бы человека нести тот же
            # файл второй раз, не понимая, что именно не так.
            return 'Формат HEIC не открывается в браузере — сохраните фото как JPEG'
        return 'Подойдёт фотография JPEG, PNG, WebP или GIF'
    if not size:
        return 'Файл пустой'
    if size > MAX_BYTES:
        return 'Файл больше %d МБ' % (MAX_BYTES // (1024 * 1024))
    if not str(filename or '').strip():
        return 'У файла нет имени'
    return None


def blob_path_for(original_name):
    """Путь в бакете. Раскладываем по дате, как это делают вики, посылки и LMS.

    Свой префикс `news/`: по нему в бакете видно, чьё это и что чистить, если
    раздел когда-нибудь свернут. Дата алматинская — на Render до 06:00 «сегодня»
    ещё вчерашнее, и папки расходились бы с датами публикаций.
    """
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_',
                  os.path.basename(str(original_name or 'photo')))[:80]
    if not safe or safe.startswith('.'):
        safe = 'photo' + (safe or '')
    day = _now().strftime('%Y/%m/%d')
    return 'news/photos/%s/%s_%s' % (day, uuid.uuid4().hex, safe)


def _pil():
    """(Image,) или None. Импорт отложенный — по той же причине, что в
    wiki/images.py: пакет подключается в bot_schedule2 внутри try/except, и
    осечка на импорте оставила бы БЕЗ ВСЕГО РАЗДЕЛА, причём молча."""
    try:
        from PIL import Image
    except ImportError:      # pragma: no cover — окружение без Pillow
        return None
    return (Image,)


def _unreadable_reason(data):
    """Почему кадр не перевёлся — словами, которые человеку что-то говорят.

    Без этой развилки самый частый отказ («сняли гигантский PNG») выглядел бы
    как «файл битый», и человек нёс бы тот же файл второй раз.
    """
    pil = _pil()
    if not pil:
        # Окружение без Pillow. Отдельный текст, потому что это не про файл: с
        # общим сообщением человек бесконечно пересохранял бы снимок.
        return 'Обработка фотографий сейчас недоступна'
    (Image,) = pil
    try:
        with Image.open(io.BytesIO(data)) as src:      # читает только заголовок
            if src.width * src.height > wiki_images.MAX_PIXELS:
                return 'Снимок слишком большой — уменьшите его'
    except Exception:
        return 'Файл не открылся как фотография'
    return 'Не удалось обработать снимок'


def prepare(data, *, filename, content_type):
    """Файл из формы → то, что ляжет в бакет.

    Возвращает словарь без обращений к сети и к базе: так его можно проверить
    тестом целиком. Ключи — ровно колонки таблицы `news_photos`, плюс байты.
    """
    issue = photo_issue(filename=filename, content_type=content_type,
                        size=len(data or b''))
    if issue:
        raise PhotoError(issue)

    kind = str(content_type or '').strip().lower().split(';')[0]

    converted = wiki_images.to_webp(data, kind)
    if not converted:
        # ЗДЕСЬ МЫ РАСХОДИМСЯ С ВИКИ, И НАМЕРЕННО. Там непереведённый файл
        # кладётся как принесли — он вложение статьи, и формат дело десятое.
        # Здесь единственный смысл строки — показать кадр, а байты раздаются
        # прямо из бакета подписанной ссылкой.
        #
        # Кортеж с ИСХОДНЫМИ байтами отказом не считается: он означает «Pillow
        # кадр открыл, но пережимать нечего». А None — «не открылось или не
        # влезло», и только на этом пути в бакет ушёл бы файл, который потом не
        # покажет ни один браузер.
        #
        # Заодно это ЕДИНСТВЕННАЯ настоящая проверка «а картинка ли это»:
        # content_type пишет клиент. Без отказа запрос с заголовком image/jpeg и
        # телом PDF прошёл бы и белый список, и проверку веса, лёг бы в бакет и
        # раздавался бы оттуда подписанной ссылкой — раздел стал бы
        # файлохостингом для всякого, у кого есть право публиковать.
        raise PhotoError(_unreadable_reason(data), code='NEWS_PHOTO_UNREADABLE')

    data, kind, width, height = converted
    # Имя меняем по ФАКТУ формата, а не по факту вызова: конвертер вправе
    # вернуть исходные байты (кадр уже WebP, или пережатие вышло в минус), и
    # назвать такой файл «.webp» значило бы соврать про содержимое.
    if kind == 'image/webp':
        filename = wiki_images.webp_name(filename)

    return {
        'data': data,
        'content_type': kind,
        'original_name': str(filename or 'photo')[:255],
        'file_size': len(data),
        'width': width,
        'height': height,
    }


def upload(gcs, prepared):
    """Кладёт кадр в бакет. Возвращает (bucket, blob_path)."""
    bucket = gcs['bucket_name']() if gcs and gcs.get('bucket_name') else None
    if not bucket:
        raise PhotoError('Хранилище фотографий не настроено',
                         code='NEWS_PHOTO_STORAGE_OFF', status=503)

    client = gcs['client']()
    blob_path = blob_path_for(prepared['original_name'])
    blob = client.bucket(bucket).blob(blob_path)
    blob.cache_control = _CACHE_CONTROL
    blob.upload_from_string(
        prepared['data'],
        content_type=prepared['content_type'] or 'application/octet-stream')
    return bucket, blob_path


def drop_blobs(gcs, refs):
    """Убирает из бакета всё, на что уже не ссылается ни одна запись.

    Best-effort и ПОСЛЕ фиксации транзакции: удалить блоб раньше, чем БД
    подтвердила удаление строки, значило бы получить запись, ссылающуюся в
    пустоту, если транзакция откатится. Обратный порядок оставляет в бакете
    файл-сироту — это стоит копейки и чинится уборкой, а не потерей.
    """
    if not refs or not gcs or not gcs.get('client'):
        return 0
    removed = 0
    try:
        client = gcs['client']()
    except Exception:
        logging.warning('Новости: не удалось получить клиент хранилища', exc_info=True)
        return 0
    for bucket_name, blob_path in refs:
        if not bucket_name or not blob_path:
            continue
        try:
            client.bucket(bucket_name).blob(blob_path).delete()
            removed += 1
        except Exception as error:
            # «Объекта уже нет» — это успех, а не сбой: так отвечает повторное
            # удаление и файл, снятый из консоли руками. Иначе журнал зарастает
            # трассировками, за которыми не видно настоящих отказов.
            text = str(error).lower()
            if '404' in text or 'not found' in text or 'no such object' in text:
                continue
            logging.warning('Новости: блоб %s/%s не удалён', bucket_name, blob_path,
                            exc_info=True)
    return removed


def _signed_url(client_of, bucket_name, blob_path, *, content_type, minutes):
    """Один адрес, через кэш. None — подписать не вышло.

    `client_of` — не сам клиент, а способ его получить: у очереди, все адреса
    которой уже в кэше, клиент не понадобится вовсе, а его построение стоит
    разбора приватного ключа RSA.
    """
    key = (bucket_name, blob_path)
    now = _now()
    cached = _SIGNED.get(key)
    if cached and cached[1] - now > _RESIGN_BEFORE:
        return cached[0]
    client = client_of()
    if client is None:
        return None
    try:
        url = client.bucket(bucket_name).blob(blob_path).generate_signed_url(
            version='v4',
            expiration=timedelta(minutes=max(1, int(minutes))),
            method='GET',
            response_disposition='inline',
            response_type=content_type or 'image/webp',
        )
    except Exception:
        # Свой warning, а не молчание: отличить «нет приватного ключа» от
        # «опечатка в имени бакета» иначе было бы нечем.
        logging.warning('Новости: подпись не собралась для %s/%s',
                        bucket_name, blob_path, exc_info=True)
        return None
    if len(_SIGNED) >= _SIGNED_MAX:
        for stale, (_url, until) in list(_SIGNED.items()):
            if until <= now:
                _SIGNED.pop(stale, None)
        if len(_SIGNED) >= _SIGNED_MAX:
            _SIGNED.clear()
    _SIGNED[key] = (url, now + timedelta(minutes=max(1, int(minutes))))
    return url


def sign_urls(gcs, rows, minutes=_SIGN_MINUTES):
    """Строки из базы → то, что уходит фронту: адреса вместо путей в бакете.

    Наружу собирается НОВЫЙ словарь по белому списку ключей, а не правится
    пришедший: так `bucket` и `blob_path` не могут утечь по забывчивости, даже
    если запрос когда-нибудь начнёт выбирать лишние колонки.

    Кадр, у которого подпись не собралась, из списка ВЫБРАСЫВАЕТСЯ: окно с
    битой картинкой хуже окна без картинки.
    """
    rows = list(rows or [])
    if not rows:
        return []

    # Один клиент на весь вызов и только если он реально понадобился. `held`
    # хранит и неудачу тоже (None), иначе битые учётные данные разбирались бы
    # заново на каждый из десяти кадров.
    held = []

    def client_of():
        if not held:
            getter = gcs.get('client') if gcs else None
            if not callable(getter):
                held.append(None)
            else:
                try:
                    held.append(getter())
                except Exception:
                    logging.warning('Новости: клиент хранилища недоступен', exc_info=True)
                    held.append(None)
        return held[0]

    out = []
    for row in rows:
        if not row or not row.get('bucket') or not row.get('blob_path'):
            continue
        url = _signed_url(client_of, row['bucket'], row['blob_path'],
                          content_type=row.get('content_type'), minutes=minutes)
        if not url:
            continue
        out.append({
            'id': str(row.get('id')),
            'url': url,
            'width': row.get('width'),
            'height': row.get('height'),
            'file_size': row.get('file_size'),
            'sort_order': row.get('sort_order'),
        })
    return out
