# -*- coding: utf-8 -*-
"""Фотографии вещи в карточке посылки: проверка файла, WebP, бакет.

ЗАЧЕМ РАЗДЕЛУ ФОТО. «Синяя коробка» и «пакет с документами» в описании — это всё,
что оператор СЗоВ мог сказать водителю до сих пор. Снимок отвечает на вопрос
«моё ли это» без переписки с офисом, а дежурному менеджеру закрывает спор
«я оставлял целую» через месяц после приёма.

ЧТО ЗДЕСЬ ЕСТЬ И ЧЕГО ЗДЕСЬ НЕТ. Здесь только байты и бакет: проверка файла,
перевод в WebP, миниатюра, загрузка и удаление блобов. SQL живёт в queries.py,
коды ответов — в routes.py. Разделение то же, что у вики: wiki/storage.py кладёт
файл, wiki/articles.register_file заводит запись.

ПЕРЕВОД В WEBP БЕРЁТСЯ ГОТОВЫЙ — wiki/images.py. Это не «залезли в чужой
пакет»: модуль намеренно сделан листом (импортирует только io/logging/os, ни
Flask, ни базы), и в нём уже решены пять вещей, которые пришлось бы решать
заново и хуже — поворот из EXIF (иначе половина снимков с телефона легла бы
боком), перенос ICC (Display P3 без профиля уезжает в перенасыщение),
прозрачность в палитре PNG, отказ на кадре больше 25 мегапикселей (память
Render) и правило «пережали, а стало тяжелее — значит испортили». Второй
экземпляр этой логики разошёлся бы с первым молча, а правило «в бакете лежит
WebP» не должно зависеть от того, в какую дверь вошли.

МИНИАТЮРА — СВОЯ, и вот почему её нет в вики. Картинку статьи показывают ровно
одну и в полную ширину колонки; фотографии посылки показываются СЕТКОЙ плиток
по 96 пикселей, и десять кадров по 200 КБ ради десяти плиток — это два мегабайта
на каждое открытие карточки. Миниатюра в 480 пикселей весит 15–25 КБ, то есть
вся сетка обходится дешевле одного полного кадра. Полный кадр грузится только
когда фотографию открыли во весь экран.

ОТКАЗ КОНВЕРТЕРА НЕ ОТМЕНЯЕТ ЗАГРУЗКУ, отказ МИНИАТЮРЫ — тем более: без неё
плитка покажет полный кадр (колонка thumb_blob_path пуста, роут отдачи это
понимает). Не пустить фотографию в карточку из-за того, что кодек споткнулся, —
хуже, чем показать её тяжёлым файлом.
"""

import io
import logging
import os
import re
import uuid
from datetime import timedelta

from wiki import images as wiki_images

from .queries import now_almaty

# Что принимаем. Тот же список, что в форме (src/components/parcels/parcelPhoto.js:
# PHOTO_TYPES) — расхождение читалось бы как «форма приняла, а сервер отказал»,
# то есть как поломка, а не как правило.
#
# HEIC с айфона сюда не входит НАМЕРЕННО. Pillow без плагина pillow-heif его не
# открывает, и такой файл лёг бы в бакет мёртвым грузом: ни браузер, ни мы его
# потом не покажем. Форма переводит снимок в WebP ещё до отправки, а если не
# смогла — отказывает с понятной причиной.
PHOTO_TYPES = ('image/jpeg', 'image/png', 'image/webp', 'image/gif')

# Вес одного исходного файла. 20 МБ — кадр с телефона в максимальном качестве с
# запасом; форма ужимает его до сотен килобайт ещё до отправки, так что предел
# сработает только на файле, который прислали мимо интерфейса.
MAX_BYTES = 20 * 1024 * 1024

# Сколько фотографий на одну посылку (решение владельца 01.09.2026). Проверяется
# и здесь, и в форме: правило, живущее только во фронте, держится до первого
# запроса мимо него.
MAX_PER_PARCEL = 10

# Длинная сторона миниатюры. 480, а не 96: плитка показывается и на экране с
# двойной плотностью, и в ряду разной ширины, а пересжать уже уменьшенное нельзя.
THUMB_SIDE = 480

# Миниатюра всегда с потерями и всегда одинаково. Разделения «скриншот против
# фотографии», как в wiki/images.py, здесь нет намеренно: в 480 пикселях мелкий
# текст всё равно не читается, за ним открывают полный кадр.
THUMB_QUALITY = 80

# Что говорим браузеру о сроке годности файла. Без явного значения GCS отдаёт
# непубличный объект с max-age=0, и миниатюры перекачиваются при каждом открытии
# карточки — даже когда адрес тот же (о постоянстве адреса заботится кэш подписей).
# Объявлено здесь, рядом с остальными настройками, а не у кэша подписей: читает
# его upload(), который стоит выше.
_CACHE_CONTROL = 'private, max-age=3600'


class PhotoError(Exception):
    """Отказ, который можно показать человеку: текст, код и статус ответа."""

    def __init__(self, message, code='PARCEL_PHOTO_REJECTED', status=400):
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
            return 'Формат HEIC не открывается в браузере — сохраните фото как JPEG'
        return 'Подойдёт фотография JPEG, PNG, WebP или GIF'
    if not size:
        return 'Файл пустой'
    if size > MAX_BYTES:
        return 'Файл больше %d МБ' % (MAX_BYTES // (1024 * 1024))
    if not str(filename or '').strip():
        return 'У файла нет имени'
    return None


def blob_path_for(original_name, *, thumb=False):
    """Путь в бакете. Раскладываем по дате, как это делают вики и LMS.

    Свой префикс `parcels/`, а не `wiki/files/`: по префиксу в бакете видно, чьё
    это и что чистить, если раздел когда-нибудь свернут. Дата — алматинская, как
    и всё остальное в разделе: на Render до 06:00 «сегодня» ещё вчерашнее, и
    папки расходились бы с датами карточек.
    """
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_',
                  os.path.basename(str(original_name or 'photo')))[:80]
    if not safe or safe.startswith('.'):
        safe = 'photo' + (safe or '')
    day = now_almaty().strftime('%Y/%m/%d')
    return 'parcels/photos/%s/%s%s_%s' % (day, uuid.uuid4().hex,
                                          '_thumb' if thumb else '', safe)


def _pil():
    """(Image, ImageOps) или None. Импорт отложенный — по той же причине, что в
    wiki/images.py: пакет подключается в bot_schedule2 внутри try/except, и
    осечка на импорте оставила бы БЕЗ ВСЕГО РАЗДЕЛА, причём молча."""
    try:
        from PIL import Image, ImageOps
    except ImportError:      # pragma: no cover — окружение без Pillow
        return None
    return Image, ImageOps


def make_thumb(data, content_type):
    """Миниатюра WebP: (байты, ширина, высота) или None, если не вышло.

    Кадр НЕ увеличивается: у снимка меньше 480 пикселей миниатюрой служит он сам
    (возвращаем None, и роут отдачи покажет полный кадр). Растянутая плитка
    выглядела бы хуже, а весила бы столько же.
    """
    pil = _pil()
    if not pil or not data:
        return None
    Image, ImageOps = pil
    try:
        with Image.open(io.BytesIO(data)) as src:
            # draft — не замена ресайзу: libjpeg распаковывает в 1/2, 1/4, 1/8
            # размера, и на кадре в 4000 пикселей это разница между 64 и 4 МБ в
            # куче. Для остальных форматов вызов ничего не делает.
            src.draft('RGB', (THUMB_SIDE, THUMB_SIDE))
            if src.width * src.height > wiki_images.MAX_PIXELS:
                return None
            if max(src.size) <= THUMB_SIDE:
                return None
            try:
                img = ImageOps.exif_transpose(src, in_place=True) or src
            except TypeError:    # Pillow до 9.5: аргумента in_place нет
                img = ImageOps.exif_transpose(src) or src

            icc = img.info.get('icc_profile')
            if 'transparency' in img.info and img.mode != 'RGBA':
                img = img.convert('RGBA')
            elif img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGBA' if 'A' in img.getbands() else 'RGB')

            scale = float(THUMB_SIDE) / float(max(img.size))
            size = (max(1, int(round(img.width * scale))),
                    max(1, int(round(img.height * scale))))
            resample = getattr(Image, 'Resampling', Image).LANCZOS
            small = img.resize(size, resample)

            out = io.BytesIO()
            options = {'quality': THUMB_QUALITY, 'method': 4}
            if icc:
                options['icc_profile'] = icc
            small.save(out, format='WEBP', **options)
            return out.getvalue(), size[0], size[1]
    except Exception:
        logging.info('Посылки: миниатюра не собралась (%s, %d байт)',
                     content_type or '?', len(data or b''))
        return None


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
    Image, _ImageOps = pil
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
    тестом целиком. Ключи — ровно колонки таблицы `parcel_photos`, плюс байты.
    """
    issue = photo_issue(filename=filename, content_type=content_type, size=len(data or b''))
    if issue:
        raise PhotoError(issue)

    kind = str(content_type or '').strip().lower().split(';')[0]

    converted = wiki_images.to_webp(data, kind)
    if not converted:
        # ЗДЕСЬ МЫ РАСХОДИМСЯ С ВИКИ, И НАМЕРЕННО. Там файл, который не удалось
        # перевести, кладётся как принесли: он вложение статьи, и формат — дело
        # десятое. Здесь единственный смысл строки — показать картинку, а байты
        # раздаются прямо из бакета подписанной ссылкой.
        #
        # Кортеж с ИСХОДНЫМИ байтами отказом не считается: он означает «Pillow
        # кадр открыл, но пережимать нечего» (уже WebP в габаритах, или WebP
        # вышел тяжелее исходника). А None — «не открылось или не влезло», и
        # только на этом пути в бакет ушёл бы файл, который потом не покажет ни
        # один браузер.
        #
        # Заодно это ЕДИНСТВЕННАЯ настоящая проверка «а картинка ли это»:
        # content_type пишет клиент, а to_webp берёт тип из аргумента. Без
        # отказа запрос с заголовком image/jpeg и телом PDF прошёл бы и белый
        # список, и проверку веса, лёг бы в бакет и раздавался бы оттуда —
        # раздел стал бы файлохостингом для всякого, у кого есть право записи.
        raise PhotoError(_unreadable_reason(data), code='PARCEL_PHOTO_UNREADABLE')

    data, kind, width, height = converted
    # Имя меняем по ФАКТУ формата, а не по факту вызова: конвертер вправе
    # вернуть исходные байты (кадр уже WebP, или пережатие вышло в минус).
    if kind == 'image/webp':
        filename = wiki_images.webp_name(filename)

    thumb = make_thumb(data, kind)
    return {
        'data': data,
        'content_type': kind,
        'original_name': str(filename or 'photo')[:255],
        'file_size': len(data),
        'width': width,
        'height': height,
        'thumb': thumb[0] if thumb else None,
        'thumb_width': thumb[1] if thumb else None,
        'thumb_height': thumb[2] if thumb else None,
    }


def upload(gcs, prepared):
    """Кладёт кадр и миниатюру в бакет. Возвращает (bucket, blob_path, thumb_path).

    Миниатюра грузится ПОСЛЕ полного кадра и её осечка не отменяет загрузку:
    без миниатюры плитка покажет полный кадр, без полного кадра показывать
    нечего вовсе.
    """
    bucket = gcs['bucket_name']() if gcs.get('bucket_name') else None
    if not bucket:
        raise PhotoError('Хранилище файлов не настроено',
                         code='PARCEL_PHOTO_STORAGE_OFF', status=503)

    client = gcs['client']()
    blob_path = blob_path_for(prepared['original_name'])
    blob = client.bucket(bucket).blob(blob_path)
    # Без явного Cache-Control GCS отдаёт непубличный объект с max-age=0, и
    # браузер перекачивает миниатюры при каждом открытии карточки — даже когда
    # адрес тот же (о том, что он тот же, заботится кэш подписей ниже).
    blob.cache_control = _CACHE_CONTROL
    blob.upload_from_string(
        prepared['data'], content_type=prepared['content_type'] or 'application/octet-stream')

    thumb_path = None
    if prepared.get('thumb'):
        thumb_path = blob_path_for(prepared['original_name'], thumb=True)
        try:
            thumb = client.bucket(bucket).blob(thumb_path)
            thumb.cache_control = _CACHE_CONTROL
            thumb.upload_from_string(prepared['thumb'], content_type='image/webp')
        except Exception:
            logging.warning('Посылки: миниатюра не загрузилась, покажем полный кадр',
                            exc_info=True)
            thumb_path = None
    return bucket, blob_path, thumb_path


def drop_blobs(gcs, refs):
    """Убирает из бакета всё, на что уже не ссылается ни одна запись.

    Best-effort и ПОСЛЕ фиксации транзакции: удалить блоб раньше, чем БД
    подтвердила удаление строки, значило бы получить запись, которая ссылается
    в пустоту, если транзакция откатится. Обратный порядок оставляет в бакете
    файл-сироту — это стоит копейки и чинится уборкой, а не потерей.
    """
    if not refs or not gcs.get('client'):
        return 0
    removed = 0
    try:
        client = gcs['client']()
    except Exception:
        logging.warning('Посылки: не удалось получить клиент хранилища', exc_info=True)
        return 0
    for bucket_name, blob_path in refs:
        if not bucket_name or not blob_path:
            continue
        try:
            client.bucket(bucket_name).blob(blob_path).delete()
            removed += 1
        except Exception as error:
            # «Объекта уже нет» — это успех, а не сбой: так отвечает повторное
            # удаление и файл, снятый из консоли руками. Тот же приём, что в
            # _lms_delete_blob_refs; иначе журнал зарастает трассировками, за
            # которыми не видно настоящих отказов.
            text = str(error).lower()
            if '404' in text or 'not found' in text or 'no such object' in text:
                continue
            logging.warning('Посылки: блоб %s/%s не удалён', bucket_name, blob_path,
                            exc_info=True)
    return removed


# ─────────────────────────────────────────────────────────────────────────────
# Подписанные адреса
# ─────────────────────────────────────────────────────────────────────────────

# Уже выданные этим процессом подписи: {(bucket, blob_path): (адрес, до когда)}.
#
# Кэш здесь не ради экономии криптографии, а ради КЭША БРАУЗЕРА. Подпись v4
# кладёт в адрес момент подписания, поэтому каждый новый вызов даёт ДРУГУЮ
# строку — и карточка, открытая второй раз, качала бы все миниатюры заново.
# Пока адрес совпадает байт в байт, <img> берёт их из кэша.
#
# Заодно снимается и цена: get_gcs_client() НЕ мемоизирован — на каждый вызов
# json.loads учётных данных, разбор приватного ключа RSA и новый Client(). На
# карточке с десятью фотографиями это было бы до двадцати таких разборов.
# Поэтому здесь клиент строится ОДИН раз на запрос, а _lms_signed_url не
# зовётся вовсе: он делает это на каждую ссылку.
_SIGNED = {}
_SIGNED_MAX = 4000
_RESIGN_BEFORE = timedelta(minutes=10)   # переподписываем заранее, а не в упор


def _signed_url(client_of, bucket_name, blob_path, *, content_type, minutes):
    """Один адрес, через кэш. None — подписать не вышло.

    `client_of` — не сам клиент, а способ его получить: на карточке, все адреса
    которой уже в кэше, клиент не понадобится вовсе, а его построение стоит
    разбора приватного ключа.
    """
    key = (bucket_name, blob_path)
    now = now_almaty()
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
        # Свой warning, а не молчание: _lms_signed_url гасит ЛЮБУЮ ошибку в
        # None без единой строки, и отличить «нет приватного ключа» от
        # «опечатка в имени бакета» было бы нечем.
        logging.warning('Посылки: подпись не собралась для %s/%s', bucket_name, blob_path,
                        exc_info=True)
        return None
    if len(_SIGNED) >= _SIGNED_MAX:
        for stale, (_url, until) in list(_SIGNED.items()):
            if until <= now:
                _SIGNED.pop(stale, None)
        if len(_SIGNED) >= _SIGNED_MAX:
            _SIGNED.clear()
    _SIGNED[key] = (url, now + timedelta(minutes=max(1, int(minutes))))
    return url


def sign_urls(gcs, rows, minutes=60):
    """Строки из базы → то, что уходит фронту: адреса вместо путей в бакете.

    `bucket` и `blob_path` наружу НЕ отдаются: фронту они не нужны, а в ответе
    служили бы подсказкой, что искать.

    `thumb_url` при пустой миниатюре равен полному адресу — плитка тогда
    показывает сам кадр. Отсутствие миниатюры не должно выглядеть как
    отсутствие фотографии.
    """
    rows = list(rows or [])
    if not rows:
        return []

    # Один клиент на весь вызов и только если он реально понадобился. `held`
    # хранит и неудачу тоже (False), иначе битые учётные данные разбирались бы
    # заново на каждую из двадцати ссылок карточки.
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
                    logging.warning('Посылки: клиент хранилища недоступен', exc_info=True)
                    held.append(None)
        return held[0]

    out = []
    for row in rows:
        url = thumb_url = None
        if row.get('bucket') and row.get('blob_path'):
            url = _signed_url(client_of, row['bucket'], row['blob_path'],
                              content_type=row.get('content_type'), minutes=minutes)
            if row.get('thumb_blob_path'):
                thumb_url = _signed_url(client_of, row['bucket'], row['thumb_blob_path'],
                                        content_type='image/webp', minutes=minutes)
        out.append({
            'id': str(row.get('id')),
            'url': url,
            'thumb_url': thumb_url or url,
            'width': row.get('width'),
            'height': row.get('height'),
            'thumb_width': row.get('thumb_width'),
            'thumb_height': row.get('thumb_height'),
            'file_size': row.get('file_size'),
            'sort_order': row.get('sort_order'),
            'created_at': row.get('created_at'),
            'uploaded_by_name': row.get('uploaded_by_name'),
        })
    return out
