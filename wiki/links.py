"""Внутренние ссылки между статьями вики: разбор тела статьи.

Зачем отдельный модуль. Ссылку «статья → статья» разбирают ДВЕ стороны:
сохранение (складывает связи в wiki_article_links ради обратных ссылок) и
чтение (собирает блок «Связанные материалы» прямо из тела). Разойдись эти два
разбора — и раздел начнёт врать в обе стороны сразу: в тексте ссылка есть, в
блоке её нет, а у цели в «Сюда ссылаются» висит источник, которого читатель в
тексте не находит. Поэтому правило разбора ровно одно и живёт здесь.

Третья сторона того же правила — фронт: src/components/wiki/articleLink.js,
функция readArticleSlugFromHref. Она решает, открыть ссылку внутри портала или
отдать браузеру. Здесь повторено ЕЁ решение, а не «улучшенное»: стоит серверу
счесть внутренней ссылку, которую фронт откроет наружу (или наоборот), и
пользователь увидит расхождение, которое ничем не объяснить.

Чего разбор НЕ делает намеренно:
  * не ходит в базу — слаг здесь только вынимается, а существует он или нет,
    решает вызывающий (wiki/edit.py при сохранении, wiki/articles.py при чтении);
  * не применяет права — связь есть объективный факт текста, а не мнение того,
    кто нажал «Сохранить». Периметр накладывается на ЧТЕНИИ. Иначе один и тот же
    текст, сохранённый супервайзером и администратором, дал бы разный граф, и
    пересохранение более узким человеком стирало бы чужие связи.
"""

import os
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

# Метки портала в адресе статьи. Должны совпадать с константами фронта
# (WIKI_VIEW, APP_VIEW_QUERY_PARAM, WIKI_ARTICLE_QUERY_PARAM в articleLink.js).
WIKI_VIEW = 'wiki'
VIEW_QUERY_PARAM = 'view'
ARTICLE_QUERY_PARAM = 'article'

# Слаг в базе — VARCHAR(255) (wiki_articles.slug), столько и принимаем.
MAX_SLUG_LENGTH = 255

# Потолок связей на одну статью. Длина тела не ограничена ничем: routes_edit
# принимает content как есть, троттлинга в разделе нет. Без потолка одним PATCH
# можно посадить свой заголовок в «Сюда ссылаются» у всех статей портала разом.
# Обрезаем молча — отказ в сохранении статьи из-за лишних ссылок был бы хуже.
MAX_LINKS_PER_ARTICLE = 200

# Адрес портала снаружи. Нужен, потому что ссылку на статью человек чаще всего
# получает кнопкой «Скопировать ссылку» (buildArticleLink), а она отдаёт
# АБСОЛЮТНЫЙ адрес. Вставленный в редактор, он обязан остаться внутренней
# ссылкой. Переменную читаем при каждом вызове, а не на импорте: тесты и
# локальный стенд подменяют окружение уже после того, как модуль загружен.
_DEFAULT_PUBLIC_BASE = 'https://alfa330.github.io/OTP'


def _public_base():
    """(множество своих хостов, свой путь) — с чем сверять абсолютный адрес."""
    raw = (os.getenv('WIKI_PUBLIC_BASE_URL')
           or os.getenv('TASK_WEB_APP_BASE_URL')
           or _DEFAULT_PUBLIC_BASE).strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        parts = urlsplit(_DEFAULT_PUBLIC_BASE)
    return {parts.netloc.lower()} - {''}, _trim_path(parts.path)


def _trim_path(path):
    """Путь без хвостовых слешей: '/OTP/' и '/OTP' — одна страница."""
    return str(path or '').rstrip('/')


def normalize_slug(value):
    """Слаг статьи или '' — если это не слаг.

    Проверка НЕ по алфавиту: 25 статей из 41 пришли миграцией из старой вики с
    кириллицей в слаге («структура-отделов»), и требование латиницы оставило бы
    почти всю вику без внутренних ссылок. Запрещены служебные символы: слаг
    уходит в путь запроса /api/wiki/articles/<slug>, и '/', '.', '%' там
    недопустимы. То же правило, что в normalizeArticleSlug на фронте.
    """
    slug = str(value or '').strip()
    if not slug or len(slug) > MAX_SLUG_LENGTH:
        return ''
    for char in slug:
        if char not in '-_' and not char.isalnum():
            return ''
    return slug


def slug_from_href(href):
    """Слаг статьи из адреса ссылки. '' — ссылка внешняя или не на статью.

    Повторяет readArticleSlugFromHref (articleLink.js): свой origin, свой путь,
    параметр article, а параметр view проверяется ТОЛЬКО если задан — на фронте
    условие точно такое же (`if (view && view !== WIKI_VIEW)`), и ужесточить его
    здесь значило бы потерять ссылки, которые портал открывает.
    """
    raw = str(href or '').strip()
    # Якорь внутри страницы ('#glava-2') — переход по оглавлению, не ссылка на
    # статью. На фронте он отсекается первым, здесь тоже.
    if not raw or raw.startswith('#'):
        return ''
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ''

    # Схема. mailto:, tel:, data: — не наши. Пустая схема — относительный адрес.
    if parts.scheme and parts.scheme.lower() not in ('http', 'https'):
        return ''

    # Хост. Пустой — адрес относительный, это наш случай по умолчанию.
    # ВАЖНО: '//evil.example/OTP?view=wiki&article=x' — протокольно-относительный
    # адрес, который проходит санитайзер насквозь (схемы в строке нет, url_schemes
    # её не видит). urlsplit разбирает его с netloc='evil.example', и проверка
    # ниже его отвергает. Без неё чужая ссылка попала бы в «Связанные материалы»
    # как своя.
    hosts, base_path = _public_base()
    if parts.netloc and parts.netloc.lower() not in hosts:
        return ''

    # Путь. Портал — одностраничное приложение, статья живёт в параметрах, а не
    # в пути. Пустой путь у относительной ссылки ('?view=wiki&article=x') —
    # «тот же адрес», он и нужен.
    path = _trim_path(parts.path)
    if path and path != base_path:
        return ''

    query = parse_qs(parts.query)
    view = (query.get(VIEW_QUERY_PARAM) or [''])[0].strip()
    if view and view != WIKI_VIEW:
        return ''
    # parse_qs уже снял проценты: кириллический слаг из buildArticleLink
    # приезжает как '%D1%81...' и разворачивается здесь.
    return normalize_slug((query.get(ARTICLE_QUERY_PARAM) or [''])[0])


class _AnchorCollector(HTMLParser):
    """Собирает href'ы якорей, у которых есть ВИДИМОЕ содержимое.

    Пустой якорь — это канал непрошеной вставки. Санитайзер (проверено запуском)
    пропускает и '<a href="?article=secret"></a>', и текст под
    'font-size:0;color:transparent': font-size и color в белом списке ALLOWED_CSS.
    То есть автор мог бы поставить связь НЕВИДИМО: в его тексте не видно ничего,
    а у чужой статьи в «Сюда ссылаются» появляется его заголовок. Связью считаем
    только то, что читатель видит и может нажать.

    Невидимость через CSS этим не закрывается полностью (шрифт в ноль на
    непустом тексте), но закрывается дешёвый и главный её вариант — пустой якорь.
    """

    def __init__(self):
        # convert_charrefs=True: '&amp;' в href и '&nbsp;' в тексте разворачиваются
        # самим парсером. Без этого href '?view=wiki&amp;article=x' (а он в базе
        # именно такой — санитайзер экранирует амперсанд) не разобрался бы.
        super().__init__(convert_charrefs=True)
        self._open = []   # стек незакрытых <a>: [href, есть ли видимое содержимое]
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self._open.append([dict(attrs).get('href') or '', False])
        elif tag == 'img' and self._open:
            # Картинка-ссылка видима, хотя текста внутри нет.
            self._open[-1][1] = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag != 'a':
            return
        # <a href="…"/> закрывается сразу и содержимого не имеет — видимого тоже.
        self._open.pop()

    def handle_endtag(self, tag):
        if tag != 'a' or not self._open:
            return
        href, visible = self._open.pop()
        if visible:
            self.hrefs.append(href)

    def handle_data(self, data):
        # str.strip() снимает и '\xa0' (&nbsp; считается пробелом), поэтому
        # якорь из одного неразрывного пробела видимым не считается.
        if self._open and data.strip():
            self._open[-1][1] = True


def article_slugs(content):
    """Слаги статей, на которые ссылается тело, в порядке появления, без повторов.

    Порядок сохраняем: «Связанные материалы» читаются как оглавление к тексту, и
    список, переставленный по алфавиту, теряет связь с тем, где ссылка встретилась.
    Повторы снимаем здесь, а не уникальным индексом: в проде внутри одного тела
    42 повторных ссылки на уже упомянутую цель, и без дедупликации INSERT с
    ON CONFLICT DO UPDATE упал бы с «cannot affect row a second time», уронив
    сохранение статьи целиком (транзакция одна на запрос).
    """
    parser = _AnchorCollector()
    try:
        parser.feed(str(content or ''))
        parser.close()
    except Exception:
        # HTMLParser на битой разметке не падает, но ошибка разбора не имеет
        # права уронить сохранение статьи: связи — производная, текст — главное.
        pass

    seen, slugs = set(), []
    for href in parser.hrefs:
        slug = slug_from_href(href)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        slugs.append(slug)
        if len(slugs) >= MAX_LINKS_PER_ARTICLE:
            break
    return slugs
