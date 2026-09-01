"""Разбор статьи базы знаний Яндекс Про (pro.yandex.com) в тело статьи вики.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ, А НЕ ВЕТКА В importer.py. Тот разбирает ФАЙЛ, который
человек принёс сам, и по своей шапке сознательно не выходит в сеть. Здесь же
источник — страница в интернете, у которой текст и картинки лежат в разных
местах, а часть содержимого вообще не попадает в отрендеренный HTML. Ветка
'.html' в importer.py такую страницу разберёт ровно наполовину и без картинок.

ГЛАВНОЕ: ТЕКСТ ЛЕЖИТ НЕ В HTML, А В __NEXT_DATA__. Страница собрана на Next.js,
и раскрывашки («На каких направлениях есть „Межгород"», «Полные условия тарифа»)
в разметке представлены ОДНИМИ ЗАГОЛОВКАМИ: тела у них нет до щелчка. Прочитав
HTML, получаешь примерно половину статьи — и без единой ошибки, потому что
пропущенного не видно. Поэтому читаем JSON состояния страницы, по пути
props.initialProps.pageProps.data.article, а отрендеренную разметку не трогаем
вовсе.

ЗДЕСЬ НЕТ НИ СЕТИ, НИ БАЗЫ, НИ FLASK — как в importer.py и migration.py.
Скачивание страницы и картинок, укладка байтов в бакет и запись статьи живут в
wiki/yandex_sync.py; сюда приходит уже скачанный текст, отсюда уходит готовый
HTML. Ровно из-за этого модуль можно гонять тестом без сети и без бакета, а
разбор проверять на слепке страницы.

ЧЕТЫРЕ ЛОВУШКИ ИСТОЧНИКА, каждая молчаливая (страница разберётся, ошибки не
будет, содержимое окажется неверным):

1. КОМПОНЕНТЫ ПЕРЕКРЫВАЮТСЯ. У «Межгорода» текст компонента [1] лежит ЦЕЛИКОМ
   внутри компонента [0], и у обоих is_hide=False. Страница показывает это
   один раз. Без дедупликации статья получает абзац дважды, и никакой флаг от
   этого не спасает — сверять надо сам текст (см. _dedupe_texts).

2. ПРИЗНАК СКРЫТОГО НАПИСАН ТРЕМЯ РАЗНЫМИ СПОСОБАМИ. На 22 разобранных
   страницах встречаются is_hide, isHide и is_hidden — вперемешку, у
   компонентов одного и того же типа. Проверять надо все три: пропустишь
   написание — и в статью уедет то, что источник от читателя спрятал.

3. У КОМПОНЕНТА ЕСТЬ АУДИТОРИЯ. allowed_ids/forbidden_ids — это города и
   регионы вида ['city-12', 'region-107']. В JSON страницы Алматы лежат и
   компоненты, предназначенные другим городам: показывает их фронт Яндекса, а
   не сервер. Утащив их «как есть», статья про Алматы получит условия Астаны.
   Список городов с их номерами лежит в том же JSON (см. _city_audience).

4. ТАБЛИЦА МОЖЕТ БЫТЬ НА ТЫСЯЧИ СТРОК. Классификатор автомобилей — это полный
   перечень марок с годами; в статью его тянуть нельзя (см. MAX_TABLE_ROWS),
   вместо него ставится ссылка на источник.

ЧЕГО В ВИКЕ НЕТ, И ВО ЧТО ЭТО ПРЕВРАЩАЕТСЯ. Видео (VideoInternal,
YandexVideoText) переносить некуда: в белом списке санитайзера нет ни video, ни
iframe (wiki/sanitize.py). Динамические врезки Яндекса (TariffCarClassifier,
TaxiStation) — это его собственные виджеты, данных у первого в JSON нет вовсе.
Всё такое становится ссылкой или таблицей, и о каждой потере пишется замечание:
человек должен узнать о ней от импортёра, а не от читателя статьи.
"""

import hashlib
import json
import re

from .ai import markup as wiki_markup
from .sanitize import sanitize_html, to_plain_text

# Код источника в wiki_article_imports.source. Тот же, что и в migration.py:
# держать два написания одного источника — значит однажды не найти половину
# перенесённого.
SOURCE = 'yandex_pro'

# Хост базы знаний. Ссылку на статью человек копирует из браузера, и попасть
# туда может что угодно — от pro.yandex.ru до чужого домена; проверяем явно.
ALLOWED_HOSTS = ('pro.yandex.com', 'pro.yandex.ru', 'pro.yandex.kz')

# Адрес страницы базы знаний: /<локаль>/<город>/knowledge-base/<раздел>/<подраздел>/<статья>.
# Город в адресе есть не всегда — у части статей путь короче на один сегмент.
_URL_RE = re.compile(
    r'^https?://(?P<host>[^/]+)/(?P<locale>[a-z]{2}-[a-z]{2})'
    r'(?:/(?P<city>[a-z0-9-]+))?/knowledge-base'
    r'/(?P<category>[a-z0-9-]+)/(?P<subcategory>[a-z0-9-]+)/(?P<slug>[a-zA-Z0-9._-]+)/?$',
    re.I,
)

# Где в состоянии страницы лежит статья.
_ARTICLE_PATH = ('props', 'initialProps', 'pageProps', 'data')

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S | re.I)

# Строк таблицы, после которых в статью уезжает ссылка вместо таблицы. 60 — это
# уже полторы страницы текста; классификатор автомобилей на 2000 строк
# превратил бы статью в справочник, а поиск вики — в тысячи чанков «Audi 100».
MAX_TABLE_ROWS = 60

# Картинок со страницы, больше которых не берём. Ограничение не про формат, а
# про время и бакет: каждая картинка — это отдельный запрос наружу и отдельный
# файл у нас навсегда (в wiki_files нет ни дедупликации, ни удаления).
MAX_IMAGES = 40

# Ширина картинки в процентах от колонки. Вертикальный скриншот телефона
# (499x1080 у «Межгорода») на всю колонку занимает три экрана статьи — по такой
# инструкции невозможно читать. Значения — те же, что ставит человек в
# редакторе, и в тех же границах (src/components/wiki/imageSize.js: 10..100).
PORTRAIT_WIDTH = 30
WIDE_WIDTH = 100
# Отношение сторон, с которого кадр считается «телефонным».
_PORTRAIT_RATIO = 1.3
# Пикселей ширины, с которых кадр считается широким скриншотом.
_WIDE_PIXELS = 900

# Заголовок раскрывашки становится заголовком РАЗДЕЛА статьи, а не <details>:
# редактор вики раскрывающиеся блоки в импортированном тексте разбирает в
# абзацы при первом же «открыл и сохранил» (известный дефект, см. заметку
# wiki-known-defects). Заголовок надёжнее и лучше для оглавления витрины.
_ACCORDION_HEADING = 'h2'
# Заголовки внутри самого текста источника: у Яндекса это h3, у нас верхний
# уровень тела статьи — h2 (h1 занят названием статьи на витрине).
_HEADING_SHIFT = {'h1': 'h2', 'h2': 'h2', 'h3': 'h3', 'h4': 'h4', 'h5': 'h5', 'h6': 'h6'}
_HEADING_SHIFT_TOP = {'h1': 'h2', 'h2': 'h2', 'h3': 'h2', 'h4': 'h3', 'h5': 'h4', 'h6': 'h4'}


class SourceError(Exception):
    """Со страницей что-то не так, и человеку надо сказать что именно."""


# ── Адрес страницы ───────────────────────────────────────────────────────────

def parse_url(url):
    """Разбор ссылки на статью базы знаний. None — ссылка не оттуда.

    Возвращает dict(host, locale, city, category, subcategory, slug, url), где
    url — канонический адрес без параметров запроса. Параметры отбрасываются
    намеренно: человек копирует ссылку из браузера, и в ней остаётся хвост
    вида '?section=' (именно такой пришёл в постановке задачи #248), а
    страница по нему та же самая. Не отбросив хвост, тот же источник получил
    бы два разных ключа — и вторую статью на повторном прогоне.
    """
    text = str(url or '').strip()
    if not text:
        return None
    text = text.split('#', 1)[0].split('?', 1)[0]
    found = _URL_RE.match(text)
    if not found:
        return None
    parts = found.groupdict()
    host = parts['host'].lower()
    if host not in ALLOWED_HOSTS:
        return None
    parts['host'] = host
    parts['url'] = canonical_url(parts)
    return parts


def canonical_url(parts):
    """Канонический адрес страницы по разобранным частям."""
    chunks = [parts['locale']]
    if parts.get('city'):
        chunks.append(parts['city'])
    chunks += ['knowledge-base', parts['category'], parts['subcategory'], parts['slug']]
    return 'https://%s/%s' % (parts['host'], '/'.join(chunks))


# ── Состояние страницы ───────────────────────────────────────────────────────

def extract_next_data(page):
    """JSON состояния страницы из её HTML (или сам JSON, если он уже есть).

    Страницу отдают gzip'ом, и байты сюда приходить не должны — раскодировать
    их обязан тот, кто качал (иначе ошибка вылезет здесь, вдали от причины).
    """
    if isinstance(page, dict):
        return page
    text = page.decode('utf-8', errors='replace') if isinstance(page, bytes) else str(page or '')
    stripped = text.lstrip()
    if stripped.startswith('{'):
        try:
            return json.loads(stripped)
        except ValueError as error:
            raise SourceError('Страница пришла не в том виде: %s' % error)
    found = _NEXT_DATA_RE.search(text)
    if not found:
        raise SourceError(
            'На странице нет состояния __NEXT_DATA__ — так отвечает либо '
            'заглушка, либо страница входа, либо адрес не из базы знаний')
    try:
        return json.loads(found.group(1))
    except ValueError as error:
        raise SourceError('Состояние страницы не читается: %s' % error)


def _dig(data, path):
    node = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


# ── Аудитория компонента ─────────────────────────────────────────────────────

def _city_audience(data, city_code):
    """Метки аудитории текущего города: {'city-12', 'region-107'}.

    Список городов с номерами лежит в том же состоянии страницы — отдельного
    запроса за справочником не нужно. Пустое множество означает «города в
    адресе нет», и тогда фильтр по аудитории не применяется вовсе: отбросить
    всё адресованное городам было бы хуже, чем взять лишнее.
    """
    if not city_code:
        return set()
    countries = _dig(data, ('props', 'initialState', 'country', 'countries')) or []
    for country in countries:
        for city in (country or {}).get('cities') or []:
            if str(city.get('code') or '').lower() != str(city_code).lower():
                continue
            marks = {'city-%s' % city.get('id')}
            if city.get('region_id'):
                marks.add('region-%s' % city['region_id'])
            return marks
    return set()


def _audience_allows(values, audience):
    """Показывать ли компонент нашему городу.

    Ключи allowed_ids/forbidden_ids приходят либо None, либо списком меток.
    Пока про город ничего не известно (audience пусто), решаем «показывать»:
    иначе статья потеряла бы куски по причине, которой у нас нет.
    """
    allowed = values.get('allowed_ids') or []
    forbidden = values.get('forbidden_ids') or []
    if not audience:
        return True, None
    if forbidden and audience & set(forbidden):
        return False, 'запрещён для города'
    if allowed and not (audience & set(allowed)):
        return False, 'предназначен другим городам'
    return True, None


def _is_hidden(values):
    """Скрыт ли компонент. Три написания одного признака — см. шапку модуля."""
    return bool(values.get('is_hide') or values.get('isHide') or values.get('is_hidden'))


# ── Разбор компонентов в поток блоков ────────────────────────────────────────

def _walk(components, audience, out, warnings, depth=0):
    for component in components or []:
        kind = str(component.get('type') or '')
        values = component.get('values') or {}
        if _is_hidden(values):
            continue
        allowed, reason = _audience_allows(values, audience)
        if not allowed:
            warnings.append('Пропущен блок «%s»: %s' % (kind, reason))
            continue

        if kind == 'YTextArea':
            out.append({'kind': 'text', 'html': values.get('text') or '', 'depth': depth})
        elif kind == 'ImageSlider':
            for item in values.get('dataList') or []:
                url = str((item or {}).get('url') or '').strip()
                if not url:
                    continue
                out.append({'kind': 'image', 'url': url,
                            'caption': str(item.get('name') or '').strip(),
                            'depth': depth})
        elif kind == 'AccordionStart':
            title = str(values.get('title') or '').strip()
            if title:
                out.append({'kind': 'heading', 'text': title, 'depth': depth})
            _walk(component.get('children'), audience, out, warnings, depth + 1)
        elif kind == 'Table':
            out.append({'kind': 'table', 'head': values.get('head') or [],
                        'body': values.get('body') or [], 'depth': depth})
        elif kind == 'TaxiStation':
            out.append({'kind': 'stations', 'rows': values.get('body') or [], 'depth': depth})
        elif kind == 'LeaveRequest':
            out.append({'kind': 'link', 'url': str(values.get('url') or '').strip(),
                        'title': str(values.get('title') or '').strip(), 'depth': depth})
        elif kind in ('VideoInternal', 'YandexVideoText'):
            out.append({'kind': 'video', 'url': str(values.get('url') or '').strip(),
                        'title': str(values.get('title') or '').strip(), 'depth': depth})
        elif kind == 'TariffCarClassifier':
            # Врезка Яндекса, у которой в JSON нет ни строчки данных: перечень
            # марок подгружает его собственный фронт. Переносить нечего, и
            # молчать об этом нельзя.
            out.append({'kind': 'widget', 'name': 'классификатор автомобилей',
                        'depth': depth})
        else:
            warnings.append('Незнакомый блок источника «%s» — не перенесён' % kind)
        if component.get('children') and kind != 'AccordionStart':
            _walk(component.get('children'), audience, out, warnings, depth + 1)
    return out


_TAG_RE = re.compile(r'<[^>]+>')
_SPACE_RE = re.compile(r'\s+')


def _fold(html):
    """Свёртка текста для сравнения на дубль: без тегов, пробелов и nbsp."""
    plain = _TAG_RE.sub(' ', html or '').replace('\xa0', ' ').replace('&nbsp;', ' ')
    return _SPACE_RE.sub('', plain).lower()


def _dedupe_texts(blocks, warnings):
    """Снять перекрывающиеся текстовые компоненты (ловушка 1 из шапки).

    Сравнивается свёрнутый текст: если он целиком содержится в тексте, который
    уже взяли, компонент лишний. Обратный случай тоже бывает — сначала пришёл
    короткий кусок, потом полный, — поэтому ранее взятый короткий выбрасывается
    из результата, а не остаётся вторым абзацем.
    """
    kept, folded = [], []
    for block in blocks:
        if block['kind'] != 'text':
            kept.append(block)
            continue
        fold = _fold(block['html'])
        if not fold:
            continue
        if any(fold in earlier for earlier in folded):
            warnings.append('Источник повторил абзац — взят один раз')
            continue
        inner = [index for index, earlier in enumerate(folded) if earlier in fold]
        if inner:
            drop = {id(kept[position]) for position in
                    [i for i, item in enumerate(kept)
                     if item['kind'] == 'text' and _fold(item['html']) in fold]}
            kept = [item for item in kept if id(item) not in drop]
            folded = [earlier for earlier in folded if earlier not in fold]
            warnings.append('Источник повторил абзац — взят один раз')
        folded.append(fold)
        kept.append(block)
    return kept


def parse_article(page, url=None):
    """Страница источника -> описание статьи. Тела статьи здесь ещё нет.

    Возвращает dict: title, slug, entity_id, url, last_update, created_at,
    category, subcategory, blocks, images, fingerprint, warnings. Тело собирает
    build_content — ему нужны уже загруженные к нам картинки.
    """
    data = extract_next_data(page)
    payload = _dig(data, _ARTICLE_PATH)
    article = (payload or {}).get('article')
    if not isinstance(article, dict) or not article.get('name'):
        raise SourceError('В состоянии страницы нет статьи базы знаний')

    query = _dig(data, ('query',)) or {}
    parts = parse_url(url) if url else None
    city = (parts or {}).get('city') or query.get('city')
    audience = _city_audience(data, city)

    warnings = []
    blocks = _dedupe_texts(_walk(article.get('text_components'), audience, [], warnings),
                           warnings)
    images, seen = [], set()
    for block in blocks:
        if block['kind'] != 'image' or block['url'] in seen:
            continue
        seen.add(block['url'])
        images.append({'url': block['url'], 'caption': block['caption']})
    if len(images) > MAX_IMAGES:
        warnings.append('Картинок на странице больше %d — взяты первые %d'
                        % (MAX_IMAGES, MAX_IMAGES))
        keep = {item['url'] for item in images[:MAX_IMAGES]}
        blocks = [block for block in blocks
                  if block['kind'] != 'image' or block['url'] in keep]
        images = images[:MAX_IMAGES]

    entity_id = payload.get('entity_id')
    parsed = {
        'title': str(article['name']).strip(),
        'slug': str(article.get('slug') or (parts or {}).get('slug') or '').strip(),
        'entity_id': int(entity_id) if isinstance(entity_id, int) else None,
        'url': (parts or {}).get('url') or (url or '').split('?', 1)[0],
        'last_update': str(article.get('last_update') or '').strip(),
        'created_at': str(article.get('created_at') or '').strip(),
        'category': ((payload.get('category') or {}).get('name') or '').strip(),
        'subcategory': ((payload.get('subcategory') or {}).get('name') or '').strip(),
        'city': city or '',
        'blocks': blocks,
        'images': images,
        'warnings': warnings,
    }
    parsed['fingerprint'] = fingerprint(parsed)
    return parsed


def fingerprint(parsed):
    """Отпечаток СОДЕРЖИМОГО источника — по нему видно «страница изменилась».

    Считается по свёрнутому тексту, адресам картинок и заголовкам, а НЕ по
    байтам страницы: в них меняются buildId, счётчики и меню, и статья
    переписывалась бы у нас каждую ночь без причины. Дата last_update тоже не
    годится — у «Межгорода» она стоит на май 2025 при живых правках текста.
    """
    parcels = [parsed.get('title') or '']
    for block in parsed.get('blocks') or []:
        kind = block['kind']
        if kind == 'text':
            parcels.append('t:' + _fold(block['html']))
        elif kind == 'image':
            parcels.append('i:%s|%s' % (block['url'], _fold(block.get('caption') or '')))
        elif kind == 'heading':
            parcels.append('h:' + _fold(block['text']))
        elif kind == 'table':
            parcels.append('tb:' + _fold(json.dumps([block['head'], block['body']],
                                                    ensure_ascii=False, sort_keys=True)))
        elif kind == 'stations':
            parcels.append('st:' + _fold(json.dumps(block['rows'], ensure_ascii=False,
                                                    sort_keys=True)))
        elif kind == 'link':
            parcels.append('l:%s|%s' % (block['url'], _fold(block['title'])))
        elif kind == 'video':
            parcels.append('v:%s|%s' % (block['url'], _fold(block['title'])))
        elif kind == 'widget':
            parcels.append('w:' + block['name'])
    digest = hashlib.sha256('\n'.join(parcels).encode('utf-8'))
    return digest.hexdigest()


# ── Сборка тела статьи ───────────────────────────────────────────────────────

def image_layout(width, height):
    """Ширина в процентах и выравнивание для картинки источника.

    Правило выведено из самих кадров, а не из вкусов: у Яндекс Про инструкции
    проиллюстрированы скриншотами телефона 499x1080 — вертикальными и узкими.
    Такой кадр во всю колонку не читается, а на 30 % стоит ровно так, как его
    ставит человек (проверено на статье «Тариф „Межгород"», id 715).
    """
    try:
        width = int(width or 0)
        height = int(height or 0)
    except (TypeError, ValueError):
        return None, 'center'
    if width <= 0 or height <= 0:
        return None, 'center'
    if height >= width * _PORTRAIT_RATIO:
        return PORTRAIT_WIDTH, 'center'
    if width >= _WIDE_PIXELS:
        return WIDE_WIDTH, 'center'
    return None, 'center'


_STRIP_ATTRS = re.compile(r'\s+(?:class|id|rel|data-[a-z-]+)="[^"]*"', re.I)
# style снимается отдельно и НЕ у списков: у <ol> Яндекс держит в стиле только
# оформление, а вот атрибут start — это смысл (см. ниже).
_STRIP_STYLE = re.compile(r'\s+style="[^"]*"', re.I)
_HEADING_TAG_RE = re.compile(r'<(/?)(h[1-6])([^>]*)>', re.I)
_HEADING_STRONG_RE = re.compile(
    r'(<h[1-6][^>]*>)\s*<strong[^>]*>(.*?)</strong>\s*(</h[1-6]>)', re.S | re.I)
# Пустые абзацы источника. Их там хватает — между компонентами Яндекс
# оставляет <p></p> и <p>&nbsp;</p>, а у нас это дырка в полстроки посреди
# текста, и заметит её только читатель.
_EMPTY_P_RE = re.compile(r'<p[^>]*>(?:\s|&nbsp;|\xa0|<br\s*/?>)*</p>', re.I)


def _clean_source_html(html, top_level):
    """HTML абзаца источника -> разметка, пригодная для тела статьи вики.

    Классы и инлайновые стили Яндекса всё равно вырезал бы санитайзер, но
    вырезал бы МОЛЧА и уже после того, как они попали в базу; убираем здесь.
    Заголовки сдвигаются к нашей шкале, а <strong> внутри заголовка снимается:
    у Яндекса каждый заголовок обёрнут им, и в нашей вёрстке это даёт двойную
    жирность.
    """
    text = _STRIP_ATTRS.sub('', html or '')
    text = _STRIP_STYLE.sub('', text)
    text = _HEADING_STRONG_RE.sub(lambda m: m.group(1) + m.group(2) + m.group(3), text)
    text = _EMPTY_P_RE.sub('', text)
    table = _HEADING_SHIFT_TOP if top_level else _HEADING_SHIFT

    def shift(found):
        return '<%s%s%s>' % (found.group(1), table.get(found.group(2).lower(),
                                                       found.group(2)), found.group(3))

    return _HEADING_TAG_RE.sub(shift, text)


def _escape(value):
    return (str(value or '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def _table_html(head, body, source_url):
    """Таблица источника. Слишком длинная заменяется ссылкой — см. MAX_TABLE_ROWS."""
    rows = [row for row in (body or []) if row]
    if len(rows) > MAX_TABLE_ROWS:
        return ('<div data-wiki-block="note" data-tone="info"><p>В источнике здесь '
                'таблица на %d строк — она не перенесена целиком. '
                '<a href="%s" target="_blank">Смотреть в базе знаний Яндекс Про</a></p>'
                '</div>' % (len(rows), _escape(source_url))), True
    out = ['<table>']
    if head:
        out.append('<thead><tr>')
        out += ['<th><p>%s</p></th>' % _escape(cell) for cell in head]
        out.append('</tr></thead>')
    out.append('<tbody>')
    for row in rows:
        out.append('<tr>')
        out += ['<td><p>%s</p></td>' % _escape(cell) for cell in row]
        out.append('</tr>')
    out.append('</tbody></table>')
    return ''.join(out), False


_STATION_COLUMNS = (('name', 'Название'), ('address', 'Адрес'),
                    ('phone', 'Телефон'), ('work_time', 'Время работы'))


def _stations_html(rows):
    """Врезка Яндекса со списком офисов — у нас это обычная таблица."""
    out = ['<table><thead><tr>']
    out += ['<th><p>%s</p></th>' % title for _, title in _STATION_COLUMNS]
    out.append('</tr></thead><tbody>')
    for row in rows or []:
        out.append('<tr>')
        out += ['<td><p>%s</p></td>' % _escape((row or {}).get(key))
                for key, _ in _STATION_COLUMNS]
        out.append('</tr>')
    out.append('</tbody></table>')
    return ''.join(out)


def build_content(parsed, image_map=None, *, source_link=True):
    """Тело статьи вики по разобранной странице.

    image_map — соответствие «адрес картинки в источнике» -> {'url': адрес у
    нас, 'width': .., 'height': ..}. Картинка, которой в карте нет, в тело НЕ
    попадает: чужой адрес yandexcloud в статье означал бы, что кадр живёт на
    чужом хосте, не привяжется к статье (wiki/edit.py: link_content_files) и
    когда-нибудь пропадёт. Вместо неё остаётся замечание.

    Возвращает (html, warnings). HTML уже прогнан через санитайзер вики —
    второй проход в create_article/update_article ничего не изменит, а тело,
    которое кладут в предпросмотр, обязано быть чистым уже здесь.
    """
    image_map = image_map or {}
    warnings = list(parsed.get('warnings') or [])
    parts, lost = [], 0

    # Хлебные крошки источника («Яндекс Такси · Классификатор») в тело НЕ
    # выносятся, хотя они и разобраны: в статье это строка, которая не отвечает
    # ни на один вопрос читателя. Откуда взят материал, сказано один раз — в
    # сноске в самом конце.
    for block in parsed.get('blocks') or []:
        kind = block['kind']
        if kind == 'text':
            parts.append(_clean_source_html(block['html'], block.get('depth', 0) == 0))
        elif kind == 'heading':
            parts.append('<%s>%s</%s>' % (_ACCORDION_HEADING, _escape(block['text']),
                                          _ACCORDION_HEADING))
        elif kind == 'image':
            stored = image_map.get(block['url'])
            if not stored or not stored.get('url'):
                lost += 1
                continue
            size, align = image_layout(stored.get('width'), stored.get('height'))
            parts.append('<p>%s</p>' % wiki_markup.image_tag(
                stored['url'], alt=block.get('caption') or '', size=size, align=align))
            if block.get('caption'):
                parts.append('<p style="text-align: center"><em>%s</em></p>'
                             % _escape(block['caption']))
        elif kind == 'table':
            html, trimmed = _table_html(block['head'], block['body'], parsed.get('url'))
            parts.append(html)
            if trimmed:
                warnings.append('Таблица источника длиннее %d строк — заменена ссылкой'
                                % MAX_TABLE_ROWS)
        elif kind == 'stations':
            parts.append(_stations_html(block['rows']))
        elif kind == 'link':
            if block['url']:
                parts.append('<p><a href="%s" target="_blank">%s</a></p>'
                             % (_escape(block['url']),
                                _escape(block['title'] or block['url'])))
        elif kind == 'video':
            # Видео в вике не живёт (в белом списке нет ни video, ни iframe) —
            # оставляем ссылку и говорим человеку, что это было видео.
            title = block['title'] or 'Видео'
            if block['url']:
                parts.append('<div data-wiki-block="note" data-tone="info"><p>Видео: '
                             '<a href="%s" target="_blank">%s</a></p></div>'
                             % (_escape(block['url']), _escape(title)))
            else:
                parts.append('<div data-wiki-block="note" data-tone="info"><p>В источнике '
                             'здесь видео «%s» — смотрите на странице базы знаний.</p>'
                             '</div>' % _escape(title))
            warnings.append('Видео «%s» перенесено ссылкой: в статье вики видео не живёт'
                            % title)
        elif kind == 'widget':
            parts.append('<div data-wiki-block="note" data-tone="info"><p>В источнике '
                         'здесь %s — он собирается на стороне Яндекса. '
                         '<a href="%s" target="_blank">Смотреть в базе знаний</a></p>'
                         '</div>' % (_escape(block['name']), _escape(parsed.get('url'))))
            warnings.append('Врезка «%s» не перенесена: данных о ней в источнике нет'
                            % block['name'])

    if lost:
        warnings.append('Не перенесено картинок: %d' % lost)

    if source_link and parsed.get('url'):
        updated = (' Последнее изменение в источнике: %s.' % _escape(parsed['last_update'])
                   if parsed.get('last_update') else '')
        parts.append('<div data-wiki-block="note" data-tone="neutral"><p>Источник: '
                     '<a href="%s" target="_blank">%s</a>.%s</p></div>'
                     % (_escape(parsed['url']), _escape(parsed['url']), updated))

    return sanitize_html(''.join(parts)), warnings


def summary_of(parsed, content=None, limit=280):
    """Краткое описание статьи — первый осмысленный абзац источника."""
    if content:
        plain = to_plain_text(content, limit=None)
    else:
        plain = ' '.join(to_plain_text(block['html'], limit=None)
                         for block in parsed.get('blocks') or []
                         if block['kind'] == 'text')
    plain = _SPACE_RE.sub(' ', plain or '').strip()
    if len(plain) <= limit:
        return plain
    cut = plain[:limit]
    space = cut.rfind(' ')
    return (cut[:space] if space > limit // 2 else cut).rstrip(' ,.;:—-') + '…'
