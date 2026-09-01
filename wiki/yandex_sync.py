"""Живая связь статьи вики со страницей базы знаний Яндекс Про.

Разбор страницы живёт в wiki/yandex_pro.py и не знает ни про сеть, ни про базу.
Здесь — всё остальное: скачать страницу и картинки, уложить кадры в бакет
(в WebP, через единственную дверь wiki/storage.py), собрать тело, создать или
обновить статью и запомнить состояние сверки.

ЗАЧЕМ СВЯЗЬ, А НЕ РАЗОВЫЙ ИМПОРТ. В постановке (задача #248) сказано прямо:
«если информация на сайте Яндекса изменится, эти изменения должны подтягиваться
в существующую статью Wiki без создания дублей». Разовый импорт этого не даёт:
через месяц у Яндекса другой текст, а у нас — прошлогодний, и узнать об этом
неоткуда. Поэтому статья подписывается на адрес страницы (wiki_yandex_pages), и
ночная сверка сравнивает отпечаток содержимого.

ТРИ ИСХОДА СВЕРКИ, И ТРЕТИЙ — САМЫЙ ВАЖНЫЙ:

  * 'ok' — отпечаток тот же, делать нечего. Статью НЕ трогаем вовсе: любой
    UPDATE тела оставляет редакцию в истории версий (wiki/edit.py:
    update_article делает снимок ДО записи), и сверка «на всякий случай»
    засорила бы историю тридцатью пустыми редакциями в месяц;
  * 'changed' — источник изменился, статью с прошлой сверки никто не правил:
    тело переписывается, редакция уходит в историю с понятной подписью;
  * 'conflict' — источник изменился, но статью правили РУКАМИ. Здесь мы не
    перезаписываем ничего. Автоматика, затирающая работу человека, хуже, чем
    устаревшая статья: устаревшую видно, а затёртую — нет. Страница помечается
    'conflict', и решение принимает человек кнопкой «Обновить из источника».

Отличить одно от другого позволяет content_hash — отпечаток тела, каким его
записал импортёр (md5, тот же, что отдаёт wiki/edit.py: current_state).

ЧТО ДЕЛАЕТ ИИ. Оформление — отдельный, необязательный шаг: собранное тело
уходит в wiki.ai.revise.edit_by_instruction с указанием расставить
оформительские блоки, не меняя ни слова текста. Тумблер живёт у страницы
(wiki_yandex_pages.ai_format), а не у прогона: иначе первая сверка вернула бы
статью к неоформленному виду, и это выглядело бы как «ИИ сломал статью».

ГДЕ ЗДЕСЬ СЕТЬ И ПОЧЕМУ ЭТО ВАЖНО. Обработчик вики держит соединение из пула на
40 (wiki/routes.py), и качать из него страницы запрещено (обоснование —
wiki/importer.py). Поэтому ночной обход (sync_all) устроен в три такта: короткий
курсор на чтение списка, скачивание БЕЗ курсора, короткий курсор на запись.
Ручные двери (одна страница по кнопке) работают под курсором роута — там это
одно действие человека с жёсткими таймаутами, а не обход базы знаний.
"""

import logging
import os

from . import edit as wiki_edit
from . import migration as wiki_migration
from . import storage as wiki_storage
from . import yandex_pro
from .ai import providers as ai_providers
from .ai import revise as ai_revise

logger = logging.getLogger(__name__)

SOURCE = yandex_pro.SOURCE

# Заголовок браузера. Без него хранилище картинок Яндекса отвечает не всегда —
# та же история, что с ссылками 2ГИС и с TEZ APP: сервер без User-Agent для
# части чужих хостов выглядит роботом.
USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36')

# Таймауты. Страница базы знаний — это ~80 КБ HTML, картинка — десятки
# килобайт; секунды здесь с большим запасом. Значения маленькие намеренно:
# ручная дверь работает под курсором из пула вики, и «подождём подольше»
# означает «подержим соединение подольше».
PAGE_TIMEOUT = 20
IMAGE_TIMEOUT = 20

# Байтов страницы, больше которых не читаем. Защита не от Яндекса, а от того,
# что по адресу окажется что-то другое: у чужого хоста в ответе может быть
# гигабайт, и распаковывать его в память приложения на Render нельзя.
MAX_PAGE_BYTES = 8 * 1024 * 1024
# Байтов картинки. Тот же предел, что у ручной загрузки в редакторе
# (wiki_importer.MAX_FILE_BYTES = 25 МБ) — незачем иметь два разных.
MAX_IMAGE_BYTES = 25 * 1024 * 1024

# Типы, которые считаем картинкой. Сверять обязательно: по адресу с CDN может
# приехать страница ошибки, и без проверки она уехала бы в бакет «картинкой»
# (store_file не валидирует ничего — это работа двери).
IMAGE_TYPES = {
    'image/png': '.png', 'image/x-png': '.png',
    'image/jpeg': '.jpg', 'image/jpg': '.jpg',
    'image/webp': '.webp', 'image/gif': '.gif',
    'image/bmp': '.bmp', 'image/tiff': '.tiff',
}
# Расширение адреса -> тип. Нужно потому, что ПУСТОЙ content_type отменяет
# перевод в WebP МОЛЧА: wiki/images.py решает по строке типа и байты не
# обнюхивает. Замер на четырёх кадрах «Межгорода»: с типом — 62-89 % от
# исходника, без типа — исходные PNG и JPEG в бакете.
EXT_TYPES = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp',
    '.tif': 'image/tiff', '.tiff': 'image/tiff',
}

# Указание помощнику для оформления принесённого текста.
#
# Своё, а не то же, что у кнопки «Оформить блоками» в панели помощника, и
# ровно из-за картинок. Импортёр УЖЕ задал вертикальным скриншотам телефона
# 30 % по центру, посчитав это по их размерам (yandex_pro.image_layout).
# Указание редактора просит модель привести размеры в порядок и не трогать
# «заданное человеком» — а отличить импортёра от человека модель не может, и
# первая же сверка растянула бы скриншоты 499x1080 на всю колонку. Здесь
# картинки запрещено трогать вовсе.
FORMAT_INSTRUCTION = (
    'Оформи статью, НЕ МЕНЯЯ ни одного слова текста: переставлять абзацы, '
    'сокращать, переписывать и дописывать запрещено. Оберни первый абзац во '
    'вводку, выдели плашками то, что нельзя пропустить, преврати перечни '
    'действий по порядку в шаги, равнозначные куски рядом — в карточки, '
    'перечни коротких значений — в чипы. Там, где блок не даёт читателю '
    'выигрыша, оставь обычный абзац. КАРТИНКИ НЕ ТРОГАЙ СОВСЕМ: ни размер, ни '
    'выравнивание, ни порядок — они уже расставлены по размерам самих кадров. '
    'Ни одной картинки не убирай и ни одной не добавляй.'
)

# Подписи редакций в истории версий. Человек, открывший историю через год,
# должен понять, откуда взялась правка, не заглядывая в журнал.
COMMENT_IMPORT = 'Импорт из базы знаний Яндекс Про'
COMMENT_SYNC = 'Обновление из базы знаний Яндекс Про'

STATUS_OK = 'ok'
STATUS_CHANGED = 'changed'
STATUS_CONFLICT = 'conflict'
STATUS_ERROR = 'error'

# Страниц за один ночной обход. Больше — это уже не сверка, а обход базы
# знаний целиком: у каждой страницы свой запрос наружу, и растягивать его на
# полчаса под ночными задачами незачем.
SYNC_BATCH = 60


class SyncError(Exception):
    """Сверка не удалась, и человеку надо сказать почему."""


def is_configured():
    """Можно ли вообще ходить наружу.

    Отдельный тумблер окружения здесь не нужен: связь заводит человек кнопкой,
    и пока ни одна статья не подписана, обход не делает ни одного запроса.
    Выключатель WIKI_YANDEX_PRO_SYNC оставлен на случай, когда наружу ходить
    нельзя вовсе (например, прод отрезан от интернета).
    """
    if str(os.environ.get('WIKI_YANDEX_PRO_SYNC', '')).strip().lower() in (
            '0', 'false', 'no', 'off', 'нет'):
        return False
    try:
        import requests  # noqa: F401
    except ImportError:
        return False
    return True


# ── Сеть ─────────────────────────────────────────────────────────────────────

def _requests():
    try:
        import requests
    except ImportError:                                   # pragma: no cover
        raise SyncError('На сервере нет библиотеки requests')
    return requests


def fetch_page(url):
    """HTML страницы базы знаний. Строкой, уже раскодированной.

    requests сам разжимает gzip — но только если не читать сырой поток: ловушка
    известная, curl без --compressed отдаёт нечитаемые байты, и точно так же
    ведёт себя ручное чтение raw.
    """
    requests = _requests()
    try:
        response = requests.get(url, timeout=PAGE_TIMEOUT, headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'ru,en;q=0.8',
        })
    except Exception as error:                             # noqa: BLE001
        raise SyncError('Страница не открылась: %s' % str(error)[:200])
    if response.status_code == 404:
        raise SyncError('Страницы по этому адресу в базе знаний нет (404)')
    if response.status_code != 200:
        raise SyncError('Источник ответил %d' % response.status_code)
    if len(response.content or b'') > MAX_PAGE_BYTES:
        raise SyncError('Страница слишком велика (%d байт)' % len(response.content))
    return response.text


def _content_type_of(url, header):
    """Тип картинки: из заголовка, а при его отсутствии — из расширения адреса.

    Пустой тип отменяет перевод в WebP молча (см. EXT_TYPES), поэтому «чего-то
    не хватило» здесь не бывает: либо тип известен, либо картинку не берём.
    """
    kind = str(header or '').split(';')[0].strip().lower()
    if kind in IMAGE_TYPES:
        return kind
    tail = str(url or '').lower().split('?')[0]
    for ext, mime in EXT_TYPES.items():
        if tail.endswith(ext):
            return mime
    return None


def fetch_image(url):
    """(байты, тип) картинки источника. SyncError — картинку брать нельзя."""
    requests = _requests()
    try:
        response = requests.get(url, timeout=IMAGE_TIMEOUT,
                                headers={'User-Agent': USER_AGENT})
    except Exception as error:                             # noqa: BLE001
        raise SyncError('Картинка не скачалась: %s' % str(error)[:120])
    if response.status_code != 200:
        raise SyncError('Картинка ответила %d' % response.status_code)
    data = response.content or b''
    if not data:
        raise SyncError('Картинка пустая')
    if len(data) > MAX_IMAGE_BYTES:
        raise SyncError('Картинка тяжелее %d МБ' % (MAX_IMAGE_BYTES // 1024 // 1024))
    kind = _content_type_of(url, response.headers.get('Content-Type'))
    if not kind:
        raise SyncError('По адресу картинки лежит не картинка')
    return data, kind


# ── Состояние связи ──────────────────────────────────────────────────────────

_PAGE_COLUMNS = ('article_id', 'url', 'entity_id', 'source_slug', 'source_title',
                 'source_updated', 'fingerprint', 'content_hash', 'auto_sync',
                 'ai_format', 'linked_by', 'linked_at', 'last_checked_at',
                 'last_changed_at', 'last_status', 'last_error')


def _page_row(row):
    if not row:
        return None
    page = dict(zip(_PAGE_COLUMNS, row))
    for key in ('linked_at', 'last_checked_at', 'last_changed_at'):
        if page.get(key) is not None:
            page[key] = page[key].isoformat(sep=' ', timespec='seconds')
    return page


def page_of_article(cursor, article_id):
    """Связь конкретной статьи с источником или None."""
    cursor.execute('SELECT %s FROM wiki_yandex_pages WHERE article_id = %%s'
                   % ', '.join(_PAGE_COLUMNS), (article_id,))
    return _page_row(cursor.fetchone())


def page_of_url(cursor, url):
    """Уже подписана ли ЖИВАЯ статья на этот адрес.

    Это и есть защита от дубля: ключ — канонический адрес, а не название и не
    слаг. Слаг в приёмнике мог оказаться занят (тогда статья легла бы под
    «-2»), а название источник вправе переименовать.

    Архивная статья адрес НЕ держит — по той же причине, что и в
    already_imported: архив означает «это больше не она», и занятый навсегда
    адрес был бы тупиком.
    """
    cursor.execute(
        'SELECT p.%s FROM wiki_yandex_pages p '
        '  JOIN wiki_articles a ON a.id = p.article_id '
        " WHERE p.url = %%s AND a.status <> 'archived'"
        % ', p.'.join(_PAGE_COLUMNS), (url,))
    return _page_row(cursor.fetchone())


def _release_archived_claim(cursor, entity_id, keep_article_id):
    """Освободить номер страницы, занятый АРХИВНОЙ статьёй.

    Уникальный индекс uq_wiki_article_imports_source не знает про архив: пока
    у убранной копии в провенансе стоит source_id, вторая статья на ту же
    страницу не запишется вовсе — INSERT падёт на нарушении уникальности, и
    человек увидит 500 там, где сделал всё правильно (убрал лишнюю копию в
    архив и связал нужную).

    Сам факт «приехало из Яндекс Про» у архивной копии остаётся — обнуляется
    только номер: он ключ, а не сведение.
    """
    if entity_id is None:
        return
    cursor.execute(
        """
        UPDATE wiki_article_imports i SET source_id = NULL
         WHERE i.source = %(source)s AND i.source_id = %(entity)s
           AND i.article_id <> %(keep)s
           AND EXISTS (SELECT 1 FROM wiki_articles a
                        WHERE a.id = i.article_id AND a.status = 'archived')
        """,
        {'source': SOURCE, 'entity': int(entity_id), 'keep': keep_article_id},
    )


def already_imported(cursor, entity_id):
    """Переносили ли эту страницу в ЖИВУЮ статью. {article_id, slug, title} или None.

    Спрашивается у ПРОВЕНАНСА (wiki_article_imports), а не у связи: связь
    снимается кнопкой, а провенанс живёт со статьёй всегда. Именно он и
    отвечает на вопрос «мы это уже переносили?» после отписки — без него
    отписка означала бы возможность завести вторую статью из той же страницы
    (проверено на живой статье «Межгород»: так и вышло).

    АРХИВНАЯ СТАТЬЯ НЕ СЧИТАЕТСЯ. Архив в вике и означает «это больше не она»:
    неудачную копию убирают в архив и переносят страницу заново или связывают
    с другой статьёй. Учитывай архивные — и страница оказалась бы занята
    навсегда, а выйти из этого было бы неоткуда: ровно та ловушка, из которой
    эта проверка и появилась.
    """
    if entity_id is None:
        return None
    cursor.execute(
        'SELECT i.article_id, a.slug, a.title FROM wiki_article_imports i '
        '  JOIN wiki_articles a ON a.id = i.article_id '
        " WHERE i.source = %s AND i.source_id = %s AND a.status <> 'archived'",
        (SOURCE, int(entity_id)),
    )
    row = cursor.fetchone()
    return {'article_id': row[0], 'slug': row[1], 'title': row[2]} if row else None


def linked_pages(cursor, *, article_ids=None):
    """Связи с источником для показа в интерфейсе.

    article_ids — периметр видимости, посчитанный вызывающим. None означает «без
    ограничения» и годится только для служебных прогонов: витрине сюда всегда
    приходит уже суженный список.
    """
    sql = ('SELECT p.%s, a.slug, a.title, a.status '
           '  FROM wiki_yandex_pages p '
           '  JOIN wiki_articles a ON a.id = p.article_id'
           % ', p.'.join(_PAGE_COLUMNS))
    params = ()
    if article_ids is not None:
        if not article_ids:
            return []
        sql += ' WHERE p.article_id = ANY(%s)'
        params = (sorted(article_ids),)
    sql += ' ORDER BY a.title'
    cursor.execute(sql, params)
    out = []
    for row in cursor.fetchall():
        page = _page_row(row[:len(_PAGE_COLUMNS)])
        page['slug'], page['title'], page['status'] = row[-3:]
        out.append(page)
    return out


def _remember_page(cursor, *, article_id, parsed, content_hash, linked_by,
                   auto_sync=True, ai_format=False, status=STATUS_CHANGED):
    """Записать связь. Повторный вызов обновляет её, а не заводит вторую."""
    cursor.execute(
        """
        INSERT INTO wiki_yandex_pages
               (article_id, url, entity_id, source_slug, source_title,
                source_updated, fingerprint, content_hash, auto_sync, ai_format,
                linked_by, last_checked_at, last_changed_at, last_status, last_error)
        VALUES (%(article_id)s, %(url)s, %(entity_id)s, %(slug)s, %(title)s,
                %(updated)s, %(fingerprint)s, %(content_hash)s, %(auto_sync)s,
                %(ai_format)s, %(linked_by)s,
                (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                %(status)s, NULL)
        ON CONFLICT (article_id) DO UPDATE SET
                url             = EXCLUDED.url,
                entity_id       = EXCLUDED.entity_id,
                source_slug     = EXCLUDED.source_slug,
                source_title    = EXCLUDED.source_title,
                source_updated  = EXCLUDED.source_updated,
                fingerprint     = EXCLUDED.fingerprint,
                content_hash    = EXCLUDED.content_hash,
                auto_sync       = EXCLUDED.auto_sync,
                ai_format       = EXCLUDED.ai_format,
                last_checked_at = EXCLUDED.last_checked_at,
                last_changed_at = EXCLUDED.last_changed_at,
                last_status     = EXCLUDED.last_status,
                last_error      = NULL
        """,
        {'article_id': article_id, 'url': parsed['url'],
         'entity_id': parsed.get('entity_id'),
         'slug': (parsed.get('slug') or None), 'title': parsed['title'][:255],
         'updated': (parsed.get('last_update') or None)[:64] if parsed.get('last_update') else None,
         'fingerprint': parsed['fingerprint'], 'content_hash': content_hash,
         'auto_sync': bool(auto_sync), 'ai_format': bool(ai_format),
         'linked_by': linked_by, 'status': status},
    )


def _mark_checked(cursor, article_id, *, status, error=None, fingerprint=None,
                  source_updated=None):
    """Отметить прогон сверки, не трогая тело статьи."""
    cursor.execute(
        """
        UPDATE wiki_yandex_pages
           SET last_checked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
               last_status     = %(status)s,
               last_error      = %(error)s,
               fingerprint     = COALESCE(%(fingerprint)s, fingerprint),
               source_updated  = COALESCE(%(updated)s, source_updated)
         WHERE article_id = %(article_id)s
        """,
        {'article_id': article_id, 'status': status,
         'error': (str(error)[:500] if error else None),
         'fingerprint': fingerprint, 'updated': source_updated},
    )


def set_auto_sync(cursor, article_id, *, auto_sync=None, ai_format=None):
    """Тумблеры связи. None — не менять."""
    sets, values = [], []
    if auto_sync is not None:
        sets.append('auto_sync = %s')
        values.append(bool(auto_sync))
    if ai_format is not None:
        sets.append('ai_format = %s')
        values.append(bool(ai_format))
    if not sets:
        return False
    values.append(article_id)
    cursor.execute('UPDATE wiki_yandex_pages SET ' + ', '.join(sets)
                   + ' WHERE article_id = %s', values)
    return cursor.rowcount > 0


def unlink(cursor, article_id):
    """Отписать статью от источника. Сама статья остаётся как есть."""
    cursor.execute('DELETE FROM wiki_yandex_pages WHERE article_id = %s', (article_id,))
    return cursor.rowcount > 0


# ── Картинки ─────────────────────────────────────────────────────────────────

def known_images(cursor, urls):
    """Уже загруженные к нам кадры: {адрес источника: {url, width, height}}.

    Без этой карты каждая ночная сверка заливала бы те же скриншоты заново: в
    wiki_files нет ни дедупликации по содержимому, ни удаления, и за месяц
    бакет распух бы на тридцать копий каждой картинки.
    """
    if not urls:
        return {}
    cursor.execute(
        'SELECT i.source_url, i.wiki_url, i.width, i.height '
        '  FROM wiki_yandex_images i '
        '  JOIN wiki_files f ON f.id = i.file_id '
        ' WHERE i.source_url = ANY(%s)',
        (sorted(set(urls)),),
    )
    return {row[0]: {'url': row[1], 'width': row[2], 'height': row[3]}
            for row in cursor.fetchall()}


def _store_image(cursor, gcs, *, source_url, data, content_type, uploaded_by,
                 article_id=None, name_hint=''):
    """Кадр в бакет (в WebP) и в карту соответствия. Возвращает запись карты."""
    tail = str(source_url).split('?')[0].rsplit('/', 1)[-1] or 'image'
    filename = ('%s-%s' % (name_hint, tail))[:80] if name_hint else tail
    file_id, url = wiki_storage.store_file(
        cursor, gcs, data=data, filename=filename, content_type=content_type,
        uploaded_by=uploaded_by, article_id=article_id)
    if not file_id:
        raise SyncError('Хранилище файлов не настроено')
    # Размеры берём из wiki_files: их посчитал конвертер, и второй расчёт
    # (своим Pillow) разошёлся бы с первым на ужатых кадрах (MAX_SIDE).
    cursor.execute('SELECT width, height FROM wiki_files WHERE id = %s', (file_id,))
    row = cursor.fetchone() or (None, None)
    cursor.execute(
        """
        INSERT INTO wiki_yandex_images (source_url, file_id, wiki_url, width, height)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (source_url) DO UPDATE SET
                file_id = EXCLUDED.file_id, wiki_url = EXCLUDED.wiki_url,
                width = EXCLUDED.width, height = EXCLUDED.height
        """,
        (source_url, file_id, url, row[0], row[1]),
    )
    return {'url': url, 'width': row[0], 'height': row[1]}


def _image_map(cursor, gcs, parsed, *, uploaded_by, article_id=None, blobs=None,
               warnings=None, fetch_image_fn=None):
    """Карта «адрес в источнике -> наш адрес» для всех картинок страницы.

    blobs — уже скачанные байты {адрес: (данные, тип)}; ночной обход качает их
    БЕЗ открытого курсора и передаёт сюда. Пусто — качаем сами (ручная дверь).
    """
    warnings = warnings if warnings is not None else []
    urls = [item['url'] for item in parsed.get('images') or []]
    mapping = dict(known_images(cursor, urls))
    fetch = fetch_image_fn or fetch_image
    hint = yandex_pro.parse_url(parsed.get('url')) or {}
    for index, item in enumerate(parsed.get('images') or [], start=1):
        source_url = item['url']
        if source_url in mapping:
            continue
        try:
            if blobs and source_url in blobs:
                data, kind = blobs[source_url]
            else:
                data, kind = fetch(source_url)
            mapping[source_url] = _store_image(
                cursor, gcs, source_url=source_url, data=data, content_type=kind,
                uploaded_by=uploaded_by, article_id=article_id,
                name_hint='%s-%d' % (hint.get('slug') or 'yandex', index))
        except SyncError as error:
            warnings.append('Картинка %d не перенесена: %s' % (index, error))
        except Exception as error:                         # noqa: BLE001
            logger.exception('Яндекс Про: картинка %s не уложилась', source_url)
            warnings.append('Картинка %d не перенесена: %s' % (index, str(error)[:120]))
    return mapping


# ── Оформление помощником ────────────────────────────────────────────────────

def format_with_ai(title, content, *, generate_fn=None):
    """Расставить оформительские блоки. (тело, замечания).

    Отказ помощника НЕ роняет импорт: статья без плашек — это статья, а
    отсутствие статьи — это отсутствие статьи. Поэтому любая осечка модели
    возвращает исходное тело и замечание.
    """
    try:
        result = ai_revise.edit_by_instruction(
            current_title=title, current_html=content,
            instruction=FORMAT_INSTRUCTION,
            generate_fn=generate_fn or ai_providers.generate_article)
    except ai_providers.ProviderError as error:
        return content, ['Помощник не оформил статью: %s' % str(error)[:160]]
    except Exception as error:                             # noqa: BLE001
        logger.exception('Яндекс Про: оформление помощником не удалось')
        return content, ['Помощник не оформил статью: %s' % str(error)[:160]]
    formatted = (result or {}).get('content') or ''
    if not formatted.strip():
        return content, ['Помощник вернул пустую статью — оставлено как принесено']
    return formatted, list((result or {}).get('warnings') or [])


# ── Разбор страницы ──────────────────────────────────────────────────────────

def read_source(url, *, fetch_page_fn=None):
    """Адрес -> разобранная страница. Ошибки — человеческим текстом."""
    parts = yandex_pro.parse_url(url)
    if not parts:
        raise SyncError(
            'Это не адрес статьи базы знаний Яндекс Про. Нужна ссылка вида '
            'https://pro.yandex.com/kz-ru/almaty/knowledge-base/taxi/tariffs/intercity')
    page = (fetch_page_fn or fetch_page)(parts['url'])
    try:
        return yandex_pro.parse_article(page, parts['url'])
    except yandex_pro.SourceError as error:
        raise SyncError(str(error))


def preview(cursor, gcs, *, url, uploaded_by, fetch_page_fn=None,
            fetch_image_fn=None, ai_format=False, generate_fn=None):
    """Разобрать страницу и собрать тело — БЕЗ создания статьи.

    Картинки при этом уже уезжают в бакет: собрать тело со чужими адресами и
    подменить их потом нельзя — тело уйдёт в редактор, человек его сохранит, и
    в статье останутся ссылки на хранилище Яндекса. Файл до сохранения статьи
    виден только загрузившему (wiki/routes_articles.py), так что лишнего мы не
    открываем.
    """
    parsed = read_source(url, fetch_page_fn=fetch_page_fn)
    warnings = []
    mapping = _image_map(cursor, gcs, parsed, uploaded_by=uploaded_by,
                         warnings=warnings, fetch_image_fn=fetch_image_fn)
    content, build_warnings = yandex_pro.build_content(parsed, mapping)
    warnings = build_warnings + warnings
    if ai_format:
        content, ai_warnings = format_with_ai(parsed['title'], content,
                                              generate_fn=generate_fn)
        warnings += ai_warnings
    existing = page_of_url(cursor, parsed['url'])
    # Отдельно от связи: страницу могли перенести и потом отписать. Тогда
    # создавать нечего — надо предложить связать существующую статью, и
    # человек должен увидеть это ДО кнопки «Создать статью».
    imported = already_imported(cursor, parsed.get('entity_id'))
    return {
        'source': {
            'url': parsed['url'], 'title': parsed['title'],
            'entity_id': parsed.get('entity_id'), 'slug': parsed.get('slug'),
            'last_update': parsed.get('last_update'),
            'category': parsed.get('category'), 'subcategory': parsed.get('subcategory'),
            'fingerprint': parsed['fingerprint'],
        },
        'title': parsed['title'],
        'summary': yandex_pro.summary_of(parsed),
        'content': content,
        'images': len(mapping),
        'warnings': warnings,
        'linked_article_id': (existing or {}).get('article_id'),
        'imported': imported,
    }


# ── Создание статьи ──────────────────────────────────────────────────────────

def import_page(cursor, gcs, *, url, section_ids, author_id, space_ids=None,
                slug_taken=None, auto_sync=True, ai_format=False, dedup=None,
                fetch_page_fn=None, fetch_image_fn=None, generate_fn=None,
                article_type='general', tags=None):
    """Создать статью из страницы источника. Черновиком.

    slug_taken — проверка занятости слага (wiki_edit.slug_is_free); передаётся
    снаружи, потому что уникализация слага живёт в роутах, а не в записи
    (wiki/edit.py её не проверяет вообще, и прямой вызов без цикла отравляет
    транзакцию UniqueViolation'ом).

    Повторный вызов для того же адреса статью НЕ создаёт: возвращает
    существующую с created=False. Это и есть «без дублей» — по адресу
    страницы, а не по названию.
    """
    parsed = read_source(url, fetch_page_fn=fetch_page_fn)
    existing = page_of_url(cursor, parsed['url'])
    if existing:
        cursor.execute('SELECT slug FROM wiki_articles WHERE id = %s',
                       (existing['article_id'],))
        row = cursor.fetchone()
        return {'article_id': existing['article_id'],
                'slug': row[0] if row else None,
                'created': False, 'status': 'already_linked',
                'warnings': ['Эта страница источника уже перенесена — '
                             'статья обновляется из неё сверкой']}

    # Связь могли снять кнопкой «Отписать», и тогда строки в wiki_yandex_pages
    # нет — а статья есть. Провенанс её помнит: он живёт в
    # wiki_article_imports и отписку переживает. Без этой проверки отписка
    # ЛОМАЛА главное обещание механизма: повторный импорт той же страницы
    # заводил вторую статью с тем же текстом.
    imported = already_imported(cursor, parsed.get('entity_id'))
    if imported:
        return {'article_id': imported['article_id'], 'slug': imported['slug'],
                'created': False, 'status': 'already_imported',
                'warnings': ['Эта страница уже переносилась в статью «%s». '
                             'Свяжите её с источником, чтобы снова получать '
                             'обновления.' % imported['title']]}

    warnings = []
    mapping = _image_map(cursor, gcs, parsed, uploaded_by=author_id,
                         warnings=warnings, fetch_image_fn=fetch_image_fn)
    content, build_warnings = yandex_pro.build_content(parsed, mapping)
    warnings = build_warnings + warnings
    if ai_format:
        content, ai_warnings = format_with_ai(parsed['title'], content,
                                              generate_fn=generate_fn)
        warnings += ai_warnings

    slug = _free_slug(parsed, slug_taken)
    article_id = wiki_edit.create_article(
        cursor, slug=slug, title=parsed['title'],
        summary=yandex_pro.summary_of(parsed), content=content,
        article_type=article_type, section_ids=section_ids, tags=tags or [],
        author_id=author_id, space_ids=space_ids)

    state = wiki_edit.current_state(cursor, article_id) or {}
    _remember_page(cursor, article_id=article_id, parsed=parsed,
                   content_hash=state.get('content_hash'), linked_by=author_id,
                   auto_sync=auto_sync, ai_format=ai_format)
    # Провенанс — той же таблицей, что и перенос из старой вики: вопрос «это
    # приехало извне или мы сами писали?» задают спустя месяцы, и ответ должен
    # лежать в одном месте на все источники. Заодно статья попадает в очередь
    # «Перенос» и ждёт решения человека.
    _release_archived_claim(cursor, parsed.get('entity_id'), article_id)
    wiki_migration.record(
        cursor, article_id=article_id, source=SOURCE,
        source_id=parsed.get('entity_id'), source_slug=parsed['url'],
        source_title=parsed['title'], source_status='published',
        dedup=dedup, imported_by=author_id)
    return {'article_id': article_id, 'slug': slug, 'created': True,
            'status': STATUS_CHANGED, 'title': parsed['title'],
            'images': len(mapping), 'warnings': warnings,
            'source_url': parsed['url']}


def link_article(cursor, *, article_id, url, linked_by, auto_sync=True,
                 ai_format=False, fetch_page_fn=None):
    """Подписать УЖЕ СУЩЕСТВУЮЩУЮ статью на страницу источника.

    Ровно этого требует постановка: статья «Тариф „Межгород"» в вике уже
    написана руками, и нужна не вторая такая же, а чтобы правки Яндекса
    доезжали в неё.

    content_hash остаётся ПУСТЫМ, и это главное решение здесь. Пустой отпечаток
    означает «тело писали не мы», и первая же сверка отдаст 'conflict', а не
    перепишет статью. Иначе связка руками написанной статьи с источником
    означала бы её уничтожение ближайшей ночью — молча и без спроса. Взять
    текст источника поверх можно, но это отдельное осознанное действие
    (sync_article с force=True).
    """
    parsed = read_source(url, fetch_page_fn=fetch_page_fn)
    taken = page_of_url(cursor, parsed['url'])
    if taken and taken['article_id'] != article_id:
        raise SyncError('На эту страницу источника уже подписана другая статья')
    known = already_imported(cursor, parsed.get('entity_id'))
    if known and known['article_id'] != article_id:
        raise SyncError('Эта страница уже перенесена в статью «%s»' % known['title'])
    _remember_page(cursor, article_id=article_id, parsed=parsed,
                   content_hash=None, linked_by=linked_by, auto_sync=auto_sync,
                   ai_format=ai_format, status=STATUS_OK)
    # Провенанс пишем и здесь, а не только при импорте. Без этого связанная
    # руками статья оставалась НЕИЗВЕСТНОЙ: строка связи снимается кнопкой
    # «Отписать», а больше о странице ничто не помнило — и повторный импорт
    # заводил вторую копию. Проверено на живой статье «Межгород»: после отписки
    # импорт создал статью-двойник.
    _release_archived_claim(cursor, parsed.get('entity_id'), article_id)
    wiki_migration.record(
        cursor, article_id=article_id, source=SOURCE,
        source_id=parsed.get('entity_id'), source_slug=parsed['url'],
        source_title=parsed['title'], source_status='published',
        imported_by=linked_by,
        # Решение по такой строке принимать не надо: статья уже живёт в вике,
        # её писал человек, и в очередь модерации переноса ей не место.
        reviewed='kept', reviewed_by=linked_by)
    return {'article_id': article_id, 'url': parsed['url'],
            'title': parsed['title'], 'entity_id': parsed.get('entity_id'),
            'fingerprint': parsed['fingerprint']}


def _free_slug(parsed, slug_taken):
    """Свободный слаг статьи. За основу — слаг страницы источника.

    Слаг источника ('intercity') читается лучше транслита названия
    ('tarif-mezhgorod') и стабилен: название Яндекс вправе переписать, адрес —
    почти никогда. Префикс нужен, чтобы адрес нашей статьи нельзя было спутать
    с чужой страницей.
    """
    base = 'yandex-%s' % (parsed.get('slug') or 'article')
    base = base[:200]
    if slug_taken is None:
        return base
    slug, suffix = base, 2
    while slug_taken(slug):
        slug = '%s-%d' % (base, suffix)
        suffix += 1
    return slug


# ── Сверка ───────────────────────────────────────────────────────────────────

def sync_article(cursor, gcs, *, article_id, editor_id=None, force=False,
                 page_html=None, blobs=None, fetch_page_fn=None,
                 fetch_image_fn=None, generate_fn=None, reindex=None):
    """Сверить одну статью с источником. Три исхода — см. шапку модуля.

    force=True переписывает тело даже при расхождении отпечатков тела, то есть
    затирает ручные правки. Это осознанное действие человека («Обновить из
    источника»), и по умолчанию его нет.

    reindex — вызываемое, которое обновит индекс помощника (в роуте это
    _sync_ai_index). Ошибка индексации не должна ронять сверку, поэтому она
    здесь и не ловится: вызывающий передаёт функцию, которая уже безопасна.
    """
    page = page_of_article(cursor, article_id)
    if not page:
        raise SyncError('Статья не подписана на страницу базы знаний')

    try:
        if page_html is not None:
            parsed = yandex_pro.parse_article(page_html, page['url'])
        else:
            parsed = read_source(page['url'], fetch_page_fn=fetch_page_fn)
    except (SyncError, yandex_pro.SourceError) as error:
        _mark_checked(cursor, article_id, status=STATUS_ERROR, error=str(error))
        return {'article_id': article_id, 'status': STATUS_ERROR,
                'error': str(error), 'url': page['url']}

    same_source = bool(page['fingerprint']) and page['fingerprint'] == parsed['fingerprint']
    if same_source and not force:
        _mark_checked(cursor, article_id, status=STATUS_OK,
                      source_updated=parsed.get('last_update'))
        return {'article_id': article_id, 'status': STATUS_OK, 'url': page['url'],
                'title': parsed['title']}

    state = wiki_edit.current_state(cursor, article_id) or {}
    # Пустой content_hash — это «тело писали НЕ МЫ»: так подписывается уже
    # существующая статья (link_article). Она тоже считается тронутой, и это
    # главное: связать вручную написанную статью с источником и получить её
    # переписанной первой же ночью — не то, о чём просили.
    touched = state.get('content_hash') != page['content_hash']
    if touched and not force:
        # Статью правили руками. Тело не трогаем — но отпечаток источника
        # запоминаем, иначе каждая ночь повторяла бы одно и то же сообщение.
        _mark_checked(cursor, article_id, status=STATUS_CONFLICT,
                      error='Источник изменился, а статью правили вручную — '
                            'нужно решение человека',
                      fingerprint=parsed['fingerprint'],
                      source_updated=parsed.get('last_update'))
        return {'article_id': article_id, 'status': STATUS_CONFLICT,
                'url': page['url'], 'title': parsed['title']}

    warnings = []
    mapping = _image_map(cursor, gcs, parsed, uploaded_by=editor_id or page['linked_by'],
                         article_id=article_id, blobs=blobs, warnings=warnings,
                         fetch_image_fn=fetch_image_fn)
    content, build_warnings = yandex_pro.build_content(parsed, mapping)
    warnings = build_warnings + warnings
    if page['ai_format']:
        content, ai_warnings = format_with_ai(parsed['title'], content,
                                              generate_fn=generate_fn)
        warnings += ai_warnings

    changed = wiki_edit.update_article(
        cursor, article_id, {'content': content, 'summary': yandex_pro.summary_of(parsed)},
        editor_id=editor_id or page['linked_by'], session_id=None,
        comment=COMMENT_SYNC)
    if changed and reindex:
        reindex(cursor, article_id)

    state = wiki_edit.current_state(cursor, article_id) or {}
    cursor.execute(
        """
        UPDATE wiki_yandex_pages
           SET fingerprint     = %(fingerprint)s,
               content_hash    = %(content_hash)s,
               source_title    = %(title)s,
               source_updated  = %(updated)s,
               last_checked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
               last_changed_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
               last_status     = %(status)s,
               last_error      = NULL
         WHERE article_id = %(article_id)s
        """,
        {'article_id': article_id, 'fingerprint': parsed['fingerprint'],
         'content_hash': state.get('content_hash'), 'title': parsed['title'][:255],
         'updated': (parsed.get('last_update') or None), 'status': STATUS_CHANGED},
    )
    return {'article_id': article_id, 'status': STATUS_CHANGED, 'url': page['url'],
            'title': parsed['title'], 'images': len(mapping),
            'warnings': warnings, 'content_changed': bool(changed)}


def due_pages(cursor, *, limit=SYNC_BATCH):
    """Что сверять этой ночью: подписанные на автообновление, давние первыми."""
    cursor.execute(
        'SELECT p.article_id, p.url FROM wiki_yandex_pages p '
        '  JOIN wiki_articles a ON a.id = p.article_id '
        "  WHERE p.auto_sync AND a.status <> 'archived' "
        ' ORDER BY p.last_checked_at NULLS FIRST, p.article_id '
        ' LIMIT %s', (int(limit),))
    return [{'article_id': row[0], 'url': row[1]} for row in cursor.fetchall()]


def sync_all(db, gcs, *, triggered_by='scheduler', limit=SYNC_BATCH,
             fetch_page_fn=None, fetch_image_fn=None, generate_fn=None,
             reindex=None):
    """Ночной обход. Три такта, чтобы не держать соединение на время сети.

    db — объект с _get_cursor() (тот же, что получает Blueprint вики). Курсор
    здесь берётся ТРИЖДЕ и на короткое время: список, затем на каждую страницу
    отдельная запись. Один курсор на весь обход держал бы соединение из пула
    вики десятки секунд — ровно то, из-за чего в импортёре документов вообще
    запрещены сетевые запросы.
    """
    if not is_configured():
        return {'skipped': 'выключено переменной WIKI_YANDEX_PRO_SYNC'}

    with db._get_cursor() as cursor:
        targets = due_pages(cursor, limit=limit)

    summary = {'checked': 0, 'changed': 0, 'conflicts': 0, 'errors': 0,
               'triggered_by': triggered_by, 'pages': len(targets)}
    for target in targets:
        html, blobs, failure = None, {}, None
        try:
            html = (fetch_page_fn or fetch_page)(target['url'])
            parsed = yandex_pro.parse_article(html, target['url'])
            blobs = _prefetch_images(parsed, fetch_image_fn)
        except (SyncError, yandex_pro.SourceError) as error:
            failure = str(error)
        except Exception as error:                         # noqa: BLE001
            logger.exception('Яндекс Про: страница %s не прочиталась', target['url'])
            failure = str(error)[:200]

        with db._get_cursor() as cursor:
            if failure:
                _mark_checked(cursor, target['article_id'], status=STATUS_ERROR,
                              error=failure)
                summary['errors'] += 1
                continue
            try:
                result = sync_article(
                    cursor, gcs, article_id=target['article_id'], page_html=html,
                    blobs=blobs, fetch_image_fn=fetch_image_fn,
                    generate_fn=generate_fn, reindex=reindex)
            except SyncError as error:
                _mark_checked(cursor, target['article_id'], status=STATUS_ERROR,
                              error=str(error))
                summary['errors'] += 1
                continue
        summary['checked'] += 1
        if result['status'] == STATUS_CHANGED:
            summary['changed'] += 1
        elif result['status'] == STATUS_CONFLICT:
            summary['conflicts'] += 1
        elif result['status'] == STATUS_ERROR:
            summary['errors'] += 1
    return summary


def _prefetch_images(parsed, fetch_image_fn=None):
    """Скачать кадры страницы БЕЗ открытого курсора.

    Качаются все, а не только новые: узнать, каких у нас нет, можно лишь
    заглянув в базу, а заглядывать туда до скачивания означало бы держать
    соединение всю сеть. Обмен сознательный — лишние байты против занятого
    соединения; на статье это единицы кадров.
    """
    fetch = fetch_image_fn or fetch_image
    blobs = {}
    for item in parsed.get('images') or []:
        try:
            blobs[item['url']] = fetch(item['url'])
        except SyncError:
            continue
        except Exception:                                  # noqa: BLE001
            logger.exception('Яндекс Про: кадр %s не скачался', item['url'])
    return blobs
