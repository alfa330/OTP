# -*- coding: utf-8 -*-
"""Статья вики → черновик новости («опубликовать статью и как новость»,
просьба владельца 23.09.2026).

Модуль чистый: ни базы, ни flask, ни бакета — только разбор разметки. Его
зовёт роут вики (wiki/routes_news.py), а проверяет тест целиком.

ПОЧЕМУ ТЕКСТ КОПИРУЕТСЯ, А НЕ ДАЁТСЯ ССЫЛКОЙ. Новость уходит тем, у кого вики
может не быть вовсе: раздел «Новости» ради этого и вынесен из вики (двери
wiki_enabled и QR-подтверждения). Ссылка «читайте в статье» у такого человека
вела бы в закрытую дверь, а окно Oktell её не откроет в принципе. Поэтому
новость получает сам текст — и дальше живёт своей жизнью: правка статьи её не
меняет, как и правка опубликованной новости не сбрасывает подтверждений.

ЧТО СТАНОВИТСЯ С РАЗМЕТКОЙ. Редактор новости проще редактора статьи, и всё,
чего он не умеет, должно превратиться во что-то читаемое, а не пропасть молча:

  * картинки уходят из текста в КАРУСЕЛЬ новости — единственное место, где
    новость показывает картинки (адреса вики у читателя новости не откроются);
  * кнопка тренажёра уходит из текста в ТРЕНАЖЁР новости — у неё есть свой;
  * блоки оформления (плашки, шаги, карточки) и раскрывашки теряют рамку, но
    сохраняют текст; заголовок раскрывашки становится жирной строкой;
  * заголовки глубже третьего уровня становятся третьим — у новости их три;
  * ссылки на статьи вики и якоря оглавления снимаются, текст остаётся: у
    читателя новости вики может не быть, а окно Oktell их не откроет;
  * цвета, выравнивание, классы и data-атрибуты снимаются — их не умеет ни
    редактор новости, ни окно.
"""

import re

try:
    import nh3
except ImportError:  # pragma: no cover — окружение без зависимости
    nh3 = None

# Столько кадров у новости (news/schema.py: MAX_PHOTOS_PER_POST). Здесь только
# порядок: отбирает и считает пропущенные вызывающий.
_IMG = re.compile(r'<img\b[^>]*>', re.I)
_SRC = re.compile(r'''\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)')''', re.I)

# Кнопка тренажёра (src/components/wiki/trainers/TrainerNode.jsx): пустой по
# смыслу div с ключом сценария и подписью внутри. Вложенных div у неё нет.
_TRAINER_BLOCK = re.compile(r'<div\b[^>]*\bdata-wiki-trainer\s*=[^>]*>.*?</div>', re.I | re.S)
_TRAINER_KEY = re.compile(r'''\bdata-wiki-trainer\s*=\s*["']([a-z0-9][a-z0-9-]*)["']''', re.I)

_DEEP_HEADING_OPEN = re.compile(r'<h[4-6]\b', re.I)
_DEEP_HEADING_CLOSE = re.compile(r'</h[4-6]\s*>', re.I)
_SUMMARY = re.compile(r'<summary\b[^>]*>(.*?)</summary\s*>', re.I | re.S)
_FIGCAPTION = re.compile(r'<figcaption\b[^>]*>.*?</figcaption\s*>', re.I | re.S)
_LINK = re.compile(r'<a\b([^>]*)>(.*?)</a\s*>', re.I | re.S)
_HREF = re.compile(r'''\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')''', re.I)
_EMPTY_BLOCK = re.compile(r'<(p|h[1-3]|li|blockquote)>(?:\s|&nbsp;|<br\s*/?>)*</\1>', re.I)

# Что понимает редактор новости (WikiNews.jsx: StarterKit, Underline, Link,
# Highlight и таблица) и что рисует окно (news-modal.css: .news-body).
NEWS_TAGS = {
    'p', 'br', 'hr', 'h1', 'h2', 'h3',
    'strong', 'b', 'em', 'i', 'u', 's', 'mark', 'code', 'pre',
    'ul', 'ol', 'li', 'blockquote', 'a',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
}
NEWS_ATTRIBUTES = {
    'a': {'href', 'target'},
    'ol': {'start'},
    'th': {'colspan', 'rowspan'},
    'td': {'colspan', 'rowspan'},
}


def _attr(pattern, text):
    found = pattern.search(text or '')
    if not found:
        return ''
    return next((group for group in found.groups() if group is not None), '')


def is_wiki_link(href):
    """Ссылка на статью вики или якорь оглавления — то, что у читателя новости
    никуда не ведёт.

    Правило то же, что у витрины (src/components/wiki/articleLink.js): статья —
    это адрес портала с ?view=wiki&article=<слаг>; относительный он или полный,
    определяет не домен, а параметры.
    """
    value = str(href or '').strip()
    if not value or value.startswith('#'):
        return True
    query = value.split('#', 1)[0]
    return 'article=' in query and ('view=wiki' in query or query.startswith('?'))


def image_sources(html):
    """Адреса картинок статьи в порядке текста. Без повторов: одна и та же
    картинка дважды в карусели — это не два кадра."""
    seen, order = set(), []
    for tag in _IMG.findall(str(html or '')):
        src = _attr(_SRC, tag).strip()
        if src and src not in seen:
            seen.add(src)
            order.append(src)
    return order


def trainer_key(html):
    """Первый тренажёр статьи. У новости тренажёр один — второй уйдёт текстом."""
    found = _TRAINER_KEY.search(str(html or ''))
    return found.group(1).lower() if found else None


def _unwrap_wiki_links(html):
    def replace(match):
        href = _attr(_HREF, match.group(1))
        return match.group(2) if is_wiki_link(href) else match.group(0)
    return _LINK.sub(replace, html)


def news_body(html):
    """Тело статьи → тело новости. Пустая строка, если текста не осталось."""
    if nh3 is None:
        raise RuntimeError('Не установлен nh3 — разметку статьи не очистить')
    text = str(html or '')
    text = _TRAINER_BLOCK.sub('', text)
    text = _IMG.sub('', text)
    text = _FIGCAPTION.sub('', text)
    text = _SUMMARY.sub(lambda m: '<p><strong>%s</strong></p>' % m.group(1), text)
    text = _DEEP_HEADING_OPEN.sub('<h3', text)
    text = _DEEP_HEADING_CLOSE.sub('</h3>', text)
    text = _unwrap_wiki_links(text)
    text = nh3.clean(
        text,
        tags=NEWS_TAGS,
        attributes={tag: set(attrs) for tag, attrs in NEWS_ATTRIBUTES.items()},
        url_schemes={'http', 'https', 'mailto', 'tel'},
        link_rel='noopener noreferrer',
        strip_comments=True,
    )
    # Абзацы, от которых осталась одна рамка: картинка ушла в карусель, а её
    # <p> остался. Два прохода — пустой пункт мог держать пустой абзац.
    for _ in range(2):
        text = _EMPTY_BLOCK.sub('', text)
    return text.strip()


def draft(article):
    """Черновик новости из статьи: заголовок, текст, картинки, тренажёр.

    article — строка wiki.articles.get_article (нужны title и content).
    Картинки — АДРЕСА: переложить их в карусель новости может только
    вызывающий, у которого есть бакет и права на файлы статьи.
    """
    content = (article or {}).get('content') or ''
    return {
        'title': str((article or {}).get('title') or '').strip()[:255],
        'body': news_body(content),
        'images': image_sources(content),
        'trainer_key': trainer_key(content),
    }
