# -*- coding: utf-8 -*-
"""Оформительские блоки статьи вики: вводка, плашка, шаги, карточки, чипы.

ЧТО ЭТО. Автор статьи выделяет мысль не только жирным и цитатой: у него есть
шесть блоков — вводка, плашка шести тонов, шаги с пунктиром между номерами,
сетка карточек, чипы и список с галочками. Вся геометрия живёт в
src/components/wiki/wiki-blocks.css, а в теле статьи от блока остаются только
data-атрибуты.

ПОЧЕМУ НА ЭТО НУЖЕН ОТДЕЛЬНЫЙ НАБОР. Блок проходит ПЯТЬ независимых рубежей, и
на каждом ломается МОЛЧА — без ошибки, без записи в логе, без жалобы:

  1. схема редактора (WikiBlockNode.js). Нет узла — TipTap разбирает <div> в
     обычные абзацы, и getHTML() отдаёт их же. Статья теряет оформление ровно
     тогда, когда её пришли поправить. Это не гипотеза: так в разделе до сих
     пор ломаются раскрывающиеся блоки <details> — они разрешены обоими
     санитайзерами, а узла у них нет;
  2. серверный санитайзер (wiki/sanitize.py). Незнакомый data-атрибут
     вырезается без следа;
  3. читательский DOMPurify (WikiArticle.jsx). Списки атрибутов обязаны
     совпадать с серверным;
  4. канонизация ответа ИИ (wiki/ai/authoring.py). Она разворачивает всё, чего
     нет в белом списке, и работает не только при сборке черновика из
     документа, но и при КАЖДОЙ правке статьи по указанию;
  5. таблица стилей. Атрибут доехал, правила под него нет — блок показывается
     безымянным div'ом: без фона, без колонок, без номеров.

Половина проверок здесь читает фронт ТЕКСТОМ. В этом репозитории так сторожат
интерфейсные решения: сборка молча пропускает и отсутствующий узел в схеме, и
селектор, написанный не под тот атрибут.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wiki.ai import authoring  # noqa: E402
from wiki.ai import markup  # noqa: E402
from wiki.sanitize import sanitize_html, to_plain_text  # noqa: E402

WIKI_SRC = ROOT / 'src' / 'components' / 'wiki'
BLOCKS_CSS = (WIKI_SRC / 'wiki-blocks.css').read_text(encoding='utf-8')
BLOCK_NODE = (WIKI_SRC / 'WikiBlockNode.js').read_text(encoding='utf-8')
EDITOR_JSX = (WIKI_SRC / 'WikiEditor.jsx').read_text(encoding='utf-8')
ARTICLE_JSX = (WIKI_SRC / 'WikiArticle.jsx').read_text(encoding='utf-8')
SANITIZE_PY = (ROOT / 'wiki' / 'sanitize.py').read_text(encoding='utf-8')


def strip_comments(source):
    """Исходник без комментариев.

    Проверять КОД по тексту с комментариями нельзя: объяснение «раньше здесь
    стоял data-tone» удовлетворило бы поиск ровно того, чего в коде уже нет.
    Приём и причина взяты из tests/test_wiki_copy_protection.py.
    """
    without_blocks = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return '\n'.join(line for line in without_blocks.splitlines()
                     if not line.lstrip().startswith('//'))


def _split_selectors(prelude):
    """Группу селекторов — на отдельные, по запятым ВЕРХНЕГО уровня.

    Наивный split(',') разрезал бы :is(strong, b) пополам и объявил «b»
    селектором без скоупа. Скобки считаем.
    """
    out, depth, current = [], 0, ''
    for char in prelude:
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        if char == ',' and depth == 0:
            out.append(current.strip())
            current = ''
            continue
        current += char
    out.append(current.strip())
    return [item for item in out if item]


# Ровно тот HTML, который отдаёт renderHTML узла и который уезжает в базу.
# Снят прогоном схемы TipTap, а не написан от руки: тест обязан сторожить то,
# что происходит на самом деле.
ARTICLE_HTML = (
    '<div data-wiki-block="lead"><p>Междугородние поездки по известному маршруту.</p></div>'
    '<h1>Как работает тариф</h1>'
    '<div data-wiki-block="cards" data-cols="3" data-numbered="true">'
    '<div data-wiki-block="card" data-tone="warn"><h4>Как обычную поездку</h4>'
    '<p>Водитель видит тариф сразу.</p></div>'
    '<div data-wiki-block="card"><h4>Выбрать самому</h4><p>Раздел «Межгород».</p></div>'
    '</div>'
    '<div data-wiki-block="note" data-tone="danger"><h4>Руль</h4>'
    '<p>Только левый руль.</p></div>'
    '<ol data-variant="steps"><li><p>Скачать приложение</p></li>'
    '<li><p>Войти по номеру</p></li></ol>'
    '<ul data-variant="chips"><li><p>Алматы</p></li><li><p>Астана</p></li></ul>'
    '<ul data-variant="checks"><li><p>Фотоконтроль пройден</p></li></ul>'
)


# ─────────────────────────────────────────────────────────────────────────────
# РУБЕЖ 2: СЕРВЕРНЫЙ САНИТАЙЗЕР
# ─────────────────────────────────────────────────────────────────────────────
class SanitizeBlocksTest(unittest.TestCase):
    def setUp(self):
        try:
            import nh3  # noqa: F401
        except ImportError:  # pragma: no cover — окружение без зависимости
            self.skipTest('nh3 не установлен')

    def test_every_block_attribute_survives(self):
        """Пропади любой атрибут — блок станет безымянным div'ом."""
        out = sanitize_html(ARTICLE_HTML)
        for expected in ('data-wiki-block="lead"', 'data-wiki-block="cards"',
                         'data-wiki-block="card"', 'data-wiki-block="note"',
                         'data-cols="3"', 'data-numbered="true"',
                         'data-tone="warn"', 'data-tone="danger"',
                         'data-variant="steps"', 'data-variant="chips"',
                         'data-variant="checks"'):
            self.assertIn(expected, out, 'потерян %s' % expected)

    def test_list_variant_survives_on_both_list_kinds(self):
        """data-variant раздаётся ul и ol ОТДЕЛЬНОЙ строкой.

        Общий набор _WIKI_DATA_ATTRS достаётся только div/span/details/summary/
        mark — списки в него не входят, и без явной раздачи вид списка исчезал
        бы при сохранении, а сам список оставался.
        """
        out = sanitize_html('<ol data-variant="steps"><li>раз</li></ol>'
                            '<ul data-variant="chips"><li>два</li></ul>')
        self.assertIn('data-variant="steps"', out)
        self.assertIn('data-variant="chips"', out)

    def test_block_cannot_carry_a_handler(self):
        html = ('<div data-wiki-block="note" onclick="alert(1)" '
                'onmouseover="steal()"><p>текст</p></div>')
        out = sanitize_html(html)
        self.assertIn('data-wiki-block="note"', out)
        self.assertNotIn('onclick', out.lower())
        self.assertNotIn('onmouseover', out.lower())

    def test_block_cannot_cover_the_portal(self):
        """Вид блока держится на CSS, поэтому геометрия из style не нужна.

        И не проходит: position и z-index не в белом списке свойств, а класс,
        начинающийся на fixed, отбрасывается фильтром классов. Проверяем, что
        обе двери закрыты и на блоке тоже.
        """
        html = ('<div data-wiki-block="note" class="fixed z-50" '
                'style="position: fixed; z-index: 99"><p>текст</p></div>')
        out = sanitize_html(html)
        self.assertIn('data-wiki-block="note"', out)
        self.assertNotIn('position', out)
        self.assertNotIn('z-index', out)
        self.assertNotIn('fixed', out)

    def test_block_text_reaches_search(self):
        """Содержимое блока — часть текста статьи, значит его находит поиск.

        Проверка не формальная: номера шагов и карточек рисуются СЧЁТЧИКАМИ
        CSS, то есть в тексте их нет и быть не должно. А вот подписи внутри
        блоков есть, и если бы оформление съело хоть одну, поиск перестал бы
        находить статью по её же словам.
        """
        plain = to_plain_text(sanitize_html(ARTICLE_HTML))
        for needle in ('Междугородние поездки', 'Как обычную поездку',
                       'Только левый руль', 'Скачать приложение', 'Астана',
                       'Фотоконтроль пройден'):
            self.assertIn(needle, plain)


# ─────────────────────────────────────────────────────────────────────────────
# РУБЕЖ 3: ПАРИТЕТ БЕЛЫХ СПИСКОВ
# ─────────────────────────────────────────────────────────────────────────────
class WhitelistsAgreeTest(unittest.TestCase):
    """Четыре списка имён обязаны совпадать.

    Разойдись любые два — статья сохранится с блоком, а покажется без него.
    Ошибку не увидит ни автор, ни читатель, ни лог: HTML валиден, ответ 200,
    просто вместо плашки серый абзац.
    """

    def _reader_whitelist(self):
        """Срез SANITIZE_OPTIONS из WikiArticle.jsx. Приём из test_wiki_images."""
        start = ARTICLE_JSX.index('const SANITIZE_OPTIONS')
        end = ARTICLE_JSX.index('const wrapTables', start)
        return ARTICLE_JSX[start:end]

    def test_server_knows_every_block_attribute(self):
        for name in markup.BLOCK_ATTRS:
            self.assertIn("'%s'" % name, SANITIZE_PY,
                          '%s не разрешён серверным санитайзером' % name)

    def test_server_gives_variant_to_both_lists(self):
        for tag in ('ul', 'ol'):
            self.assertIn("ALLOWED_ATTRIBUTES['%s'] |= {'data-variant'}" % tag,
                          SANITIZE_PY)

    def test_reader_knows_every_block_attribute(self):
        reader = self._reader_whitelist()
        for name in tuple(markup.BLOCK_ATTRS) + tuple(markup.LIST_ATTRS):
            self.assertIn("'%s'" % name, reader,
                          '%s не перечислен в SANITIZE_OPTIONS витрины' % name)

    def test_editor_node_knows_every_attribute(self):
        node = strip_comments(BLOCK_NODE)
        for name in tuple(markup.BLOCK_ATTRS) + tuple(markup.LIST_ATTRS):
            self.assertIn("'%s'" % name, node,
                          '%s не читается и не пишется узлом схемы' % name)

    def test_kinds_and_tones_agree_with_the_editor(self):
        """Виды и тона объявлены и на сервере, и в узле — списки одни и те же."""
        node = strip_comments(BLOCK_NODE)
        for kind in markup.BLOCK_KINDS:
            self.assertIn("'%s'" % kind, node, 'вид %s редактору неизвестен' % kind)
        for tone in markup.TONES:
            self.assertIn("value: '%s'" % tone, node,
                          'тон %s редактору неизвестен' % tone)
        for variant in markup.LIST_VARIANTS:
            self.assertIn("value: '%s'" % variant, node,
                          'вид списка %s редактору неизвестен' % variant)


# ─────────────────────────────────────────────────────────────────────────────
# РУБЕЖ 1: СХЕМА РЕДАКТОРА
# ─────────────────────────────────────────────────────────────────────────────
class EditorSchemaTest(unittest.TestCase):
    """Без узла в схеме блок не переживёт «открыл статью → сохранил».

    Проверяем текстом: node --test не умеет импортировать .jsx, а сборка молча
    пропускает и подключённый импорт, которым никто не воспользовался.
    """

    def test_nodes_are_registered_in_the_editor(self):
        source = strip_comments(EDITOR_JSX)
        self.assertIn("from './WikiBlockNode'", source)
        # Оба расширения именно В МАССИВЕ extensions, а не просто импортированы.
        start = source.index('extensions: [')
        end = source.index('],', start)
        registered = source[start:end]
        self.assertIn('WikiBlock', registered)
        self.assertIn('WikiListVariant', registered)

    def test_node_parses_by_attribute_not_by_class(self):
        """Ключ блока — data-атрибут.

        Класс проходит санитайзер, но по классу схему не построить: фильтр
        классов чистит их своим правилом, а MsoNormal из Word от нашего класса
        не отличить. Тот же выбор сделан у кнопки тренажёра.
        """
        node = strip_comments(BLOCK_NODE)
        self.assertIn("tag: 'div[data-wiki-block]'", node)

    def test_block_menu_is_mounted(self):
        source = strip_comments(EDITOR_JSX)
        self.assertIn('<WikiBlockMenu editor={editor} />', source)

    def test_styles_are_imported_by_both_surfaces(self):
        """Витрина и редактор — разные чанки, и импорт нужен в обоих.

        Без импорта в редакторе автор правил бы блок без вида, то есть не то,
        что увидит читатель.
        """
        self.assertIn("import './wiki-blocks.css'", EDITOR_JSX)
        self.assertIn("import './wiki-blocks.css'", ARTICLE_JSX)


# ─────────────────────────────────────────────────────────────────────────────
# РУБЕЖ 5: ТАБЛИЦА СТИЛЕЙ
# ─────────────────────────────────────────────────────────────────────────────
class BlocksCssTest(unittest.TestCase):
    def test_every_kind_tone_and_variant_is_drawn(self):
        for kind in markup.BLOCK_KINDS:
            self.assertIn("[data-wiki-block='%s']" % kind, BLOCKS_CSS,
                          'вид %s ничем не нарисован' % kind)
        for tone in markup.TONES:
            self.assertIn("[data-tone='%s']" % tone, BLOCKS_CSS,
                          'тон %s ничем не нарисован' % tone)
        for variant in markup.LIST_VARIANTS:
            self.assertIn("[data-variant='%s']" % variant, BLOCKS_CSS,
                          'вид списка %s ничем не нарисован' % variant)

    def test_rules_are_scoped_to_the_article_body(self):
        """Каждое правило начинается с .wiki-prose или .ProseMirror.

        Скоуп — не аккуратность: раздел живёт внутри общего портала, и голый
        селектор [data-tone] покрасил бы чужие экраны. Исключение только у
        правил редактора: там свой префикс .ProseMirror.
        """
        body = re.sub(r'/\*.*?\*/', '', BLOCKS_CSS, flags=re.S)
        for match in re.finditer(r'([^{}]+)\{', body):
            # Перед селектором остаётся хвост предыдущего правила — берём то,
            # что идёт после последней закрывающей скобки.
            prelude = match.group(1).rsplit('}', 1)[-1].strip()
            if not prelude or prelude.startswith('@'):
                continue
            for one in _split_selectors(prelude):
                self.assertTrue(
                    one.startswith('.wiki-prose') or one.startswith('.ProseMirror'),
                    'селектор без скоупа: %s' % one)

    def test_steps_are_connected_by_a_dashed_line(self):
        """Пунктир между кружками номеров — то, ради чего блок и заводили.

        Сплошная линия читается как «непрерывный процесс» и спорит с левой
        гранью цитат и карточек; пунктир однозначно значит «дальше следующий
        шаг».
        """
        self.assertRegex(BLOCKS_CSS, r"ol\[data-variant='steps'\] > li \{[^}]*"
                                     r"border-left: 2px dashed")
        self.assertRegex(BLOCKS_CSS, r"ol\[data-variant='steps'\] > li::before \{[^}]*"
                                     r"content: counter\(wiki-step\)")

    def test_numbers_are_counters_not_text(self):
        """Номера рисуются счётчиками CSS, а не написаны в тексте статьи.

        Это решение про ПОИСК, а не про вёрстку: написанные цифры попали бы в
        content_plain и в чанки помощника отдельными блоками «1», «2», «3».
        """
        self.assertIn('counter-reset: wiki-step', BLOCKS_CSS)
        self.assertIn('counter-reset: wiki-card', BLOCKS_CSS)
        self.assertIn('counter-increment: wiki-card', BLOCKS_CSS)

    def test_no_dark_theme_branches(self):
        """Тёмной темы в разделе нет по решению владельца.

        Tailwind у нас работает в режиме media, поэтому и класс dark:*, и
        медиазапрос prefers-color-scheme сработали бы от системной темы
        читателя, хотя раздел светлый. data-tone="dark" — это ТОН плашки, а не
        тема, и к этому правилу отношения не имеет.
        """
        self.assertNotIn('prefers-color-scheme', BLOCKS_CSS)
        self.assertNotIn('dark:', BLOCKS_CSS)

    def test_print_keeps_the_dark_note_readable(self):
        """Браузер не печатает фоновые заливки.

        Без правила тёмная плашка «например» вышла бы на бумаге белым по
        белому — то есть исчезла бы вместе с разобранным случаем.
        """
        self.assertIn('@media print', BLOCKS_CSS)
        printed = BLOCKS_CSS[BLOCKS_CSS.index('@media print'):]
        self.assertIn("[data-tone='dark']", printed)
        self.assertIn('break-inside: avoid', printed)

    def test_geometry_lives_here_and_not_in_the_article(self):
        """Проверка, что блок вообще МОЖЕТ быть нарисован.

        В белом списке CSS санитайзера нет ни display, ни grid, ни
        border-radius, ни border-left — инлайновым стилем такой блок не
        нарисовать в принципе, и попытка обошлась бы молчаливой потерей вида.
        """
        from wiki.sanitize import ALLOWED_CSS
        for prop in ('display', 'grid-template-columns', 'border-radius',
                     'border-left', 'gap'):
            self.assertNotIn(prop, ALLOWED_CSS)
        self.assertIn('display: grid', BLOCKS_CSS)
        self.assertIn('grid-template-columns', BLOCKS_CSS)


# ─────────────────────────────────────────────────────────────────────────────
# РУБЕЖ 4: КАНОНИЗАЦИЯ ОТВЕТА ИИ
# ─────────────────────────────────────────────────────────────────────────────
class CanonKeepsBlocksTest(unittest.TestCase):
    def test_blocks_survive_canonicalize(self):
        """Главный рубеж: canonicalize зовётся и при сборке, и при КАЖДОЙ правке.

        Пока div не в белом списке, оформление умирает в той же функции,
        которая его и породила, — и молча.
        """
        out = authoring.canonicalize(ARTICLE_HTML)
        for expected in ('data-wiki-block="lead"', 'data-wiki-block="cards"',
                         'data-wiki-block="card"', 'data-wiki-block="note"',
                         'data-tone="danger"', 'data-cols="3"',
                         'data-numbered="true"', 'data-variant="steps"',
                         'data-variant="chips"', 'data-variant="checks"'):
            self.assertIn(expected, out, 'canonicalize потерял %s' % expected)

    def test_plain_div_is_still_unwrapped(self):
        """Разрешение блоков не открыло дверь мусорным контейнерам из Word."""
        out = authoring.canonicalize(
            '<div class="MsoNormal" style="position:fixed"><p>Текст</p></div>')
        self.assertNotIn('<div', out)
        self.assertIn('Текст', out)

    def test_unknown_kind_is_not_a_block(self):
        out = authoring.canonicalize('<div data-wiki-block="hero"><p>Текст</p></div>')
        self.assertNotIn('<div', out)
        self.assertIn('Текст', out)

    def test_unknown_tone_falls_back(self):
        """Чужой тон не рисует ничего, но пережил бы санитайзер.

        Оставить его — значит навсегда положить в тело статьи мусор, чьё
        происхождение потом уже не установить.
        """
        out = authoring.canonicalize(
            '<div data-wiki-block="note" data-tone="фиолетовый"><p>Текст</p></div>')
        self.assertIn('data-wiki-block="note"', out)
        self.assertNotIn('data-tone', out)

    def test_heading_inside_a_block_becomes_h4(self):
        """h1-h3 внутри блока разорвали бы оглавление статьи.

        Оглавление собирается по h1/h2/h3 (WikiArticle.jsx), и заголовок плашки
        «Важно» встал бы в правой колонке наравне с разделами. Плюс
        _lift_headings поднимает уровни: статья, у которой единственные
        заголовки внутри плашек, целиком превратилась бы в набор разделов.
        """
        out = authoring.canonicalize(
            '<div data-wiki-block="note"><h2>Важно</h2><p>Текст</p></div>'
            '<h2>Раздел</h2>')
        self.assertIn('<h4>Важно</h4>', out)
        self.assertIn('<h1>Раздел</h1>', out)

    def test_h4_outside_a_block_is_still_demoted(self):
        """Снаружи блока правило прежнее: глубже h3 в статье не бывает."""
        out = authoring.canonicalize('<h1>Раздел</h1><h4>Подзаголовок</h4>')
        self.assertIn('<h3>Подзаголовок</h3>', out)

    def test_stray_cards_are_gathered_into_a_grid(self):
        """Самая частая осечка модели — ряд карточек без обёртки.

        Такая карточка не пропадает, но встаёт одна во всю ширину, то есть
        перестаёт быть карточкой. Собрать их дешевле, чем объяснять моделью.
        """
        out = authoring.canonicalize(
            '<div data-wiki-block="card"><p>раз</p></div>'
            '<div data-wiki-block="card"><p>два</p></div>')
        self.assertIn('data-wiki-block="cards"', out)
        self.assertEqual(2, out.count('data-wiki-block="card"'))

    def test_grid_without_cards_is_unwrapped(self):
        out = authoring.canonicalize('<div data-wiki-block="cards"><p>раз</p></div>')
        self.assertNotIn('<div', out)
        self.assertIn('раз', out)

    def test_block_inside_block_is_flattened(self):
        """Вложенность разрешена ровно одна: карточка внутри сетки."""
        out = authoring.canonicalize(
            '<div data-wiki-block="note"><div data-wiki-block="note">'
            '<p>Текст</p></div></div>')
        self.assertEqual(1, out.count('data-wiki-block="note"'))

    def test_empty_block_is_dropped(self):
        out = authoring.canonicalize(
            '<div data-wiki-block="note"><p>  </p></div><p>Текст</p>')
        self.assertNotIn('data-wiki-block', out)
        self.assertIn('Текст', out)

    def test_long_chip_returns_to_a_plain_list(self):
        """Чип — короткое значение. Предложение в чипе — таблетка на всю строку."""
        long_text = 'Очень длинное значение, которое на деле целое предложение'
        out = authoring.canonicalize(
            '<ul data-variant="chips"><li>%s</li></ul>' % long_text)
        self.assertNotIn('data-variant', out)
        self.assertIn(long_text, out)

    def test_steps_only_on_a_numbered_list(self):
        """У <ul> правило шагов не сработает, и человек увидит обычный список."""
        out = authoring.canonicalize('<ul data-variant="steps"><li>раз</li></ul>')
        self.assertNotIn('data-variant', out)


# ─────────────────────────────────────────────────────────────────────────────
# НАСТАВЛЕНИЕ ДЛЯ МОДЕЛИ
# ─────────────────────────────────────────────────────────────────────────────
class MarkupGuideTest(unittest.TestCase):
    def test_guide_reaches_all_three_prompts(self):
        """Разметка описана в одном месте и подставляется во все промпты.

        До этого её описывали трижды и разными словами — добавить блоки в одну
        копию и забыть остальные было самым дешёвым способом получить статью,
        которая красиво собирается из документа и разваливается при первой же
        правке по указанию.
        """
        from wiki.ai import revise
        for prompt in (authoring.SYSTEM_PROMPT, revise.UPDATE_PROMPT,
                       revise.EDIT_PROMPT):
            self.assertIn('ОФОРМИТЕЛЬСКИЕ БЛОКИ', prompt)
            self.assertIn('data-wiki-block="note"', prompt)

    def test_prompts_no_longer_forbid_the_blocks_they_ask_for(self):
        """Запрет на div сужен до ПРОИЗВОЛЬНОГО div.

        Пока в промпте стояло «ЗАПРЕЩЕНО: … div», модель блоки не рисовала —
        никакой белый список на сервере этого не исправил бы.
        """
        from wiki.ai import revise
        for prompt in (authoring.SYSTEM_PROMPT, revise.EDIT_PROMPT):
            forbidden = prompt[prompt.index('ЗАПРЕЩЕНО') if 'ЗАПРЕЩЕНО' in prompt
                               else prompt.index('Запрещено'):][:400]
            self.assertIn('произвольный', forbidden)

    def test_guide_names_every_kind_tone_and_variant(self):
        """Наставление и код обязаны знать один и тот же набор.

        Тон, о котором модели не сказали, она не поставит; тон, которого нет в
        коде, она поставит, а он не нарисуется.
        """
        for tone in markup.TONES:
            self.assertIn(tone, markup.MARKUP_GUIDE)
        for variant in markup.LIST_VARIANTS:
            self.assertIn('data-variant="%s"' % variant, markup.MARKUP_GUIDE)

    def test_guide_says_when_not_to_use_a_block(self):
        """Статью портят не отсутствующие блоки, а блоки не к месту."""
        self.assertIn('КОГДА БЛОК НЕ СТАВЯТ', markup.MARKUP_GUIDE)

    def test_editor_hint_repeats_the_same_rule_to_the_human(self):
        """Автору-человеку говорится то же, что и модели."""
        source = strip_comments(EDITOR_JSX)
        self.assertIn('BLOCK_HINT', source)
        self.assertIn('Три плашки подряд не выделяют ничего', EDITOR_JSX)


class MarkupWarningsTest(unittest.TestCase):
    def test_lost_formatting_is_reported(self):
        """Правка по указанию не должна молча разбирать блоки обратно в текст.

        Порядок в revise._finish — canonicalize, потом восстановление таблиц;
        если модель развернёт плашки, статья вернётся целой по смыслу и голой
        по виду. Автор об этом узнает только отсюда.
        """
        before = '<div data-wiki-block="note"><p>раз</p></div>'
        after = '<p>раз</p>'
        found = markup.warnings(before_html=before, after_html=after)
        self.assertTrue(any('потерял оформление' in w for w in found))

    def test_too_many_notes_is_reported(self):
        html = '<div data-wiki-block="note"><p>раз</p></div>' * (markup.NOTE_BUDGET + 1)
        found = markup.warnings(after_html=html)
        self.assertTrue(any('Плашек в статье' in w for w in found))

    def test_normal_article_is_silent(self):
        found = markup.warnings(before_html=ARTICLE_HTML, after_html=ARTICLE_HTML)
        self.assertEqual([], found)

    def test_warning_reaches_the_author(self):
        """Предупреждение обязано доехать до панели, а не остаться в модуле."""
        found = authoring.structure_warnings(
            source_html='<div data-wiki-block="note"><p>раз</p></div>',
            source_text='', result_html='<h1>Раздел</h1><p>раз</p>', lost_tables=[])
        self.assertTrue(any('оформление' in w for w in found))


class EnvelopeTest(unittest.TestCase):
    """Ответ без строки «СТАТЬЯ:» режется по первому тегу.

    Ветка живая: модели регулярно отвечают без конверта. Пока в списке тегов не
    было div, срез приходился на <p> ВНУТРИ блока — в статье оставался висящий
    </div>, а открывающий тег и всё оформление терялись.
    """

    def test_leading_block_is_not_cut_in_half(self):
        reply = ('Вот статья.\n'
                 '<div data-wiki-block="lead"><p>Вводка</p></div><h1>Раздел</h1>')
        _, _, body = authoring._envelope(reply)
        self.assertTrue(body.startswith('<div data-wiki-block="lead"'))

    def test_revise_envelope_too(self):
        from wiki.ai import revise
        reply = ('ИЗМЕНЕНИЯ:\n- поправил\n'
                 '<div data-wiki-block="note"><p>Важно</p></div><h1>Раздел</h1>')
        _, _, body = revise.parse_reply(reply)
        self.assertTrue(body.startswith('<div data-wiki-block="note"'))


class LeadAndTitleTest(unittest.TestCase):
    def test_lead_does_not_shield_a_duplicated_title(self):
        """Статья, начинающаяся с вводки, не теряет защиту от повтора названия.

        drop_leading_title выходит на первом же не-заголовке, и без пропуска
        вводки цикл упирался бы в <div> и до заголовка не доходил.
        """
        html = ('<div data-wiki-block="lead"><p>Коротко</p></div>'
                '<h1>Тариф «Межгород»</h1><p>Текст</p>')
        out = authoring.drop_leading_title(html, 'Тариф «Межгород»')
        self.assertNotIn('<h1>', out)
        self.assertIn('data-wiki-block="lead"', out)


if __name__ == '__main__':
    unittest.main()


# ─────────────────────────────────────────────────────────────────────────────
# КАРТИНКИ: РАЗМЕР И ВЫРАВНИВАНИЕ
# ─────────────────────────────────────────────────────────────────────────────
IMAGE_SIZE_JS = (WIKI_SRC / 'imageSize.js').read_text(encoding='utf-8')

# Картинка ровно в том виде, в каком её пишет узел редактора: размер и в
# data-width, и в style. Дублирование намеренное — по атрибуту редактор
# восстанавливает состояние узла, стилем картинка рисуется читателю.
SIZED_IMAGE = ('<img src="/api/wiki/file/screen.webp" alt="Экран съёмки" '
               'data-width="60" data-align="right" '
               'style="width: 60%; margin-left: auto; margin-right: 0">')


class ImageControlsSurviveTest(unittest.TestCase):
    """Размер, выставленный ЧЕЛОВЕКОМ, не должен сбрасываться правкой через ИИ.

    Так и было до 31.08.2026: protect_tables вырезал из тега ровно src и alt,
    и автор, поставивший скриншот на 60 % и прижавший вправо, после любого
    «сократи» получал его во всю ширину слева. Молча — ни предупреждения, ни
    строки в списке изменений.
    """

    def test_size_and_align_survive_the_round_trip(self):
        body, tables, images = authoring.protect_tables('<p>До</p>%s<p>После</p>'
                                                        % SIZED_IMAGE)
        self.assertIn('data-width="60"', images[0])
        self.assertIn('data-align="right"', images[0])
        out, _lost = authoring.restore_tables(body, tables, images)
        self.assertIn('data-width="60"', out)
        self.assertIn('data-align="right"', out)
        self.assertIn('margin-right: 0', out)

    def test_marker_without_a_tail_changes_nothing(self):
        """«Оставить как есть» обязано быть значением по умолчанию.

        Иначе модель, которая просто перенесла маркер, каждый раз сбрасывала бы
        чужие настройки — и отличить это от намеренной правки было бы нельзя.
        """
        _body, tables, images = authoring.protect_tables(SIZED_IMAGE)
        out, _lost = authoring.restore_tables('<p>[[КАРТИНКА-1]]</p>', tables, images)
        self.assertIn('data-width="60"', out)
        self.assertIn('data-align="right"', out)

    def test_model_can_set_size_and_align(self):
        _body, tables, images = authoring.protect_tables(SIZED_IMAGE)
        out, _lost = authoring.restore_tables(
            '<p>[[КАРТИНКА-1 35% по центру]]</p>', tables, images)
        self.assertIn('data-width="35"', out)
        self.assertIn('data-align="center"', out)
        self.assertIn('margin-left: auto; margin-right: auto', out)

    def test_size_only_keeps_the_previous_align(self):
        """Контролы независимы: задал ширину — выравнивание осталось прежним."""
        _body, tables, images = authoring.protect_tables(SIZED_IMAGE)
        out, _lost = authoring.restore_tables('<p>[[КАРТИНКА-1 40%]]</p>', tables, images)
        self.assertIn('data-width="40"', out)
        self.assertIn('data-align="right"', out)

    def test_impossible_size_is_clamped(self):
        """Ширина 300 % или 2 % — это не размер, а поломка вёрстки."""
        _body, tables, images = authoring.protect_tables(SIZED_IMAGE)
        big, _ = authoring.restore_tables('<p>[[КАРТИНКА-1 300%]]</p>', tables, images)
        small, _ = authoring.restore_tables('<p>[[КАРТИНКА-1 2%]]</p>', tables, images)
        self.assertIn('data-width="100"', big)
        self.assertIn('data-width="10"', small)

    def test_marker_with_a_tail_is_still_found(self):
        """Раньше шаблон маркера кончался на \\s*, и маркер с хвостом не находился.

        Последствие было не косметическое: маркер уезжал в статью текстом
        «[[КАРТИНКА-1 60%]]», а сама картинка — в конец, под заголовок «не
        размещённое по разделам».
        """
        _body, tables, images = authoring.protect_tables(SIZED_IMAGE)
        out, _lost = authoring.restore_tables('<p>[[КАРТИНКА-1 60% справа]]</p>',
                                              tables, images)
        self.assertNotIn('КАРТИНКА', out)
        self.assertNotIn('не размещённое', out)


class ImageRemovalTest(unittest.TestCase):
    """Убрать картинку можно, но только ЯВНО и с предупреждением.

    Правило «потерянная картинка возвращается в конец статьи» старше этой
    правки и остаётся: файл уже лежит в бакете, и молча выброшенная ссылка
    оставляет статью без иллюстрации, а хранилище — с мусором. Поэтому у
    удаления есть своя команда, а не «просто не переноси маркер».
    """

    def test_explicit_removal_works(self):
        _body, tables, images = authoring.protect_tables(SIZED_IMAGE)
        out, _lost = authoring.restore_tables(
            '<p>[[КАРТИНКА-1 убрать]]</p><p>Текст</p>', tables, images)
        self.assertNotIn('<img', out)
        self.assertNotIn('не размещённое', out)
        self.assertIn('Текст', out)

    def test_removal_does_not_leave_an_empty_paragraph(self):
        _body, tables, images = authoring.protect_tables(SIZED_IMAGE)
        out, _lost = authoring.restore_tables(
            '<p>[[КАРТИНКА-1 убрать]]</p><p>Текст</p>', tables, images)
        self.assertEqual('<p>Текст</p>', out)

    def test_dropped_marker_still_returns_the_image(self):
        """Молча потерять картинку по-прежнему нельзя."""
        _body, tables, images = authoring.protect_tables(SIZED_IMAGE)
        out, _lost = authoring.restore_tables('<p>Текст</p>', tables, images)
        self.assertIn('<img', out)
        self.assertIn('не размещённое', out)

    def test_removal_is_reported_to_the_author(self):
        numbers = authoring.removed_images('<p>[[КАРТИНКА-2 убрать]]</p>'
                                           '<p>[[КАРТИНКА-1]]</p>')
        self.assertEqual([2], numbers)


class ImageStyleParityTest(unittest.TestCase):
    """Формула стиля продублирована в Python и в JS — они обязаны совпадать.

    Один и тот же размер выставляют двое: человек ручкой в редакторе
    (styleFor из imageSize.js) и ИИ маркером (image_style здесь). Разойдись
    формулы — одинаковые на вид настройки давали бы разную вёрстку, и понять,
    отчего картинка «прыгает», было бы нельзя.
    """

    def test_margins_match_the_editor(self):
        for align, expected in (
                ('left', 'margin-left: 0; margin-right: auto'),
                ('center', 'margin-left: auto; margin-right: auto'),
                ('right', 'margin-left: auto; margin-right: 0')):
            self.assertEqual('width: 35%; ' + expected,
                             markup.image_style(35, align))
            # Тот же набор полей стоит и в JS — сверяем по исходнику.
            self.assertIn(expected.replace('; ', "', '"), IMAGE_SIZE_JS)

    def test_limits_match_the_editor(self):
        self.assertIn('export const MIN_SIZE = %d;' % markup.IMAGE_MIN, IMAGE_SIZE_JS)
        self.assertIn('export const MAX_SIZE = %d;' % markup.IMAGE_MAX, IMAGE_SIZE_JS)

    def test_shorthand_margin_is_never_used(self):
        """Сокращённое margin санитайзер выбрасывает ЦЕЛИКОМ.

        Он сверяет ИМЯ свойства с белым списком, а там только margin-left и
        margin-right. Напиши формула «margin: 0 auto» — выравнивание молча
        пропало бы при сохранении.
        """
        from wiki.sanitize import ALLOWED_CSS
        self.assertNotIn('margin', ALLOWED_CSS)
        self.assertIn('margin-left', ALLOWED_CSS)
        for align in markup.IMAGE_ALIGNS:
            style = markup.image_style(50, align)
            self.assertNotIn('margin:', style)

    def test_tag_survives_the_sanitizer(self):
        try:
            import nh3  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest('nh3 не установлен')
        out = sanitize_html(markup.image_tag('/api/wiki/file/x.webp', 'Экран', 35, 'center'))
        self.assertIn('data-width="35"', out)
        self.assertIn('data-align="center"', out)
        self.assertIn('width: 35%', out)
        self.assertIn('margin-left: auto', out)


class ImageGuideTest(unittest.TestCase):
    def test_guide_reaches_the_request_only_when_there_are_images(self):
        """Наставление уезжает в ЗАПРОС, а не в системный промпт.

        В системном оно доставалось бы и ветке, где модель читает файл сама и
        никаких маркеров не существует, — то есть учило бы синтаксису, которым
        нельзя воспользоваться.
        """
        self.assertEqual('', authoring.images_block([]))
        block = authoring.images_block([SIZED_IMAGE])
        self.assertIn('КАРТИНКИ И ИХ РАЗМЕР', block)
        self.assertNotIn('КАРТИНКИ И ИХ РАЗМЕР', authoring.SYSTEM_PROMPT)

    def test_hint_shows_the_current_state(self):
        """Без текущего размера правило «не трогай чужое» не выполнить.

        Модель не видит ни тега, ни его атрибутов — только маркер.
        """
        hint = authoring.image_hints([SIZED_IMAGE,
                                      '<img src="/a.webp" alt="QR-код">'])
        self.assertIn('Экран съёмки (60 %, справа)', hint)
        self.assertIn('QR-код (размер не задан)', hint)

    def test_guide_names_both_controls_and_the_removal(self):
        for needle in ('60%', 'по центру', 'справа', 'слева', 'убрать'):
            self.assertIn(needle, markup.IMAGE_GUIDE)

    def test_guide_says_not_to_touch_what_a_human_set(self):
        self.assertIn('его выставил', markup.IMAGE_GUIDE)


class ImageRenumberTest(unittest.TestCase):
    """Сдвиг номеров при обновлении документом не должен терять контролы."""

    def test_tail_survives_renumbering(self):
        from wiki.ai import revise
        shifted = revise._MARKER_RE.sub(
            lambda m: '[[%s-%d%s]]' % (m.group(1).upper(), int(m.group(2)) + 2,
                                       m.group(3)),
            '[[КАРТИНКА-1 40% справа]]')
        self.assertEqual('[[КАРТИНКА-3 40% справа]]', shifted)

    def test_marker_with_a_tail_is_humanized(self):
        """Маркер не должен утекать в список изменений как код."""
        from wiki.ai import revise
        self.assertEqual('добавлена картинка 2 в раздел',
                         revise.humanize('добавлена [[КАРТИНКА-2 40% справа]] в раздел'))


# ─────────────────────────────────────────────────────────────────────────────
# ПОКАЗАТЕЛИ, КРЕСТИКИ И НЕЙТРАЛЬНЫЙ ТОН
# ─────────────────────────────────────────────────────────────────────────────
class StatsBlockTest(unittest.TestCase):
    """Сетка показателей устроена как сетка карточек — и обязана ею остаться.

    Пар «сетка → ячейка» теперь две, и весь ремонт разметки ходит по таблице
    GRIDS. Появись у второй пары своя копия правил — расхождение вылезло бы не
    сразу, а на первой же правке чужой статьи через ИИ.
    """

    def test_both_grids_are_declared_in_one_table(self):
        self.assertEqual((('cards', 'card'), ('stats', 'stat')), markup.GRIDS)
        for grid, item in markup.GRIDS:
            self.assertIn(grid, markup.BLOCK_KINDS)
            self.assertIn(item, markup.BLOCK_KINDS)

    def test_stray_stats_are_gathered_into_a_grid(self):
        """Показатели подряд без обёртки — самая частая осечка модели.

        Проверяется через canonicalize, а не прямым вызовом normalize: это тот
        самый путь, которым проходит ответ модели, и мимо него ремонт не
        работает вовсе.
        """
        out = authoring.canonicalize(
            '<div data-wiki-block="stat"><h4>10 минут</h4><p>ожидание</p></div>'
            '<div data-wiki-block="stat"><h4>4,75</h4><p>рейтинг</p></div>')
        self.assertIn('data-wiki-block="stats"', out)
        self.assertEqual(2, out.count('data-wiki-block="stat"'))

    def test_three_cells_get_three_columns(self):
        """Тройка встаёт в три колонки, всё прочее — в две.

        Ряд из четырёх ячеек читается как два ряда по две, из пяти — как 3+2;
        и то и другое лучше, чем четыре узких столбца.
        """
        for count, cols in ((2, '2'), (3, '3'), (4, '2'), (6, '3')):
            out = authoring.canonicalize(
                ''.join('<div data-wiki-block="stat"><p>%d</p></div>' % i
                        for i in range(count)))
            self.assertIn('data-cols="%s"' % cols, out,
                          '%d показателей встали не в те колонки' % count)

    def test_outsider_leaves_the_stats_grid(self):
        out = authoring.canonicalize(
            '<div data-wiki-block="stats">'
            '<div data-wiki-block="stat"><p>3</p></div><p>чужак</p></div>')
        self.assertIn('чужак', out, 'абзац потерян')
        self.assertLess(out.index('</div>'), out.index('чужак'),
                        'чужой абзац остался внутри сетки')

    def test_card_inside_a_stats_grid_moves_to_its_own_grid(self):
        """Вложенность разрешена только своя: карточка в сетке показателей — нет.

        Карточку при этом НЕ разворачивают в голый текст: модель явно хотела
        карточку, и ремонт даёт ей ту сетку, которой она принадлежит. Опустевшая
        сетка показателей уходит сама — сетка без ячеек это пустой прямоугольник.
        """
        out = authoring.canonicalize(
            '<div data-wiki-block="stats">'
            '<div data-wiki-block="card"><p>текст</p></div></div>')
        self.assertIn('текст', out)
        self.assertIn('data-wiki-block="cards"', out)
        self.assertNotIn('data-wiki-block="stats"', out)

    def test_mixed_grid_keeps_both_kinds_of_cell(self):
        """Сетка, в которую модель положила и то и другое, ничего не теряет."""
        out = authoring.canonicalize(
            '<div data-wiki-block="stats">'
            '<div data-wiki-block="stat"><h4>5</h4><p>дней</p></div>'
            '<div data-wiki-block="card"><h4>Заголовок</h4><p>текст</p></div></div>')
        self.assertIn('дней', out)
        self.assertIn('текст', out)
        self.assertIn('data-wiki-block="stat"', out)
        self.assertIn('data-wiki-block="card"', out)

    def test_stat_keeps_its_tone(self):
        """Тон красит значение показателя — «10 000 ₸» зелёным «это хорошо».

        До правки _fix_attrs знал только карточки и плашки, и тон показателя
        срезался вторым проходом ремонта — молча, уже после того, как первый
        его сохранил.
        """
        out = authoring.canonicalize(
            '<div data-wiki-block="stats" data-cols="3">'
            '<div data-wiki-block="stat" data-tone="ok"><h4>5</h4><p>дней</p></div>'
            '</div>')
        self.assertIn('data-tone="ok"', out)

    def test_stats_are_never_numbered(self):
        """Пронумерованные показатели читались бы списком шагов."""
        out = authoring.canonicalize(
            '<div data-wiki-block="stats" data-numbered="true">'
            '<div data-wiki-block="stat"><p>1</p></div></div>')
        self.assertNotIn('data-numbered', out)

    def test_editor_pairs_match_the_server(self):
        """GRID_ITEMS во фронте — те же пары, что GRIDS здесь.

        Разойдись они — кнопка «+» положила бы в сетку показателей карточку, а
        ремонт при первой же правке через ИИ выкинул бы её наружу: с точки
        зрения автора добавленное «выпало» само.
        """
        node = strip_comments(BLOCK_NODE)
        for grid, item in markup.GRIDS:
            self.assertRegex(
                node, r"%s:\s*\{\s*item:\s*'%s'" % (grid, item),
                'пара %s → %s редактору неизвестна' % (grid, item))

    def test_stat_value_is_drawn_large(self):
        """Смысл блока в том, что число берут ВЗГЛЯДОМ, не читая строку."""
        rule = BLOCKS_CSS[BLOCKS_CSS.index("[data-wiki-block='stat'] h4"):][:400]
        self.assertIn('font-size: 1.75rem', rule)
        self.assertIn('tabular-nums', rule)

    def test_stats_default_to_three_columns(self):
        """У показателей по умолчанию три колонки, а не две, как у карточек.

        В показателе одна короткая строка, и вдвоём они растягиваются на
        пол-экрана каждый.
        """
        block = BLOCKS_CSS[BLOCKS_CSS.index(".wiki-prose [data-wiki-block='stats'] {"):][:300]
        self.assertIn('repeat(3, minmax(0, 1fr))', block)


class CrossesAndNeutralTest(unittest.TestCase):
    def test_crosses_are_a_bullet_list_like_checks(self):
        """Крестики — вид <ul>, а не свой узел: список обязан остаться списком."""
        node = strip_comments(BLOCK_NODE)
        self.assertRegex(node, r"value: 'crosses',[^}]*type: 'bulletList'")

    def test_crosses_and_checks_are_drawn_differently(self):
        """Пара «да/нет» имеет смысл, только если знаки разные и по цвету тоже."""
        crosses = BLOCKS_CSS[BLOCKS_CSS.index("ul[data-variant='crosses'] > li::before"):][:900]
        checks = BLOCKS_CSS[BLOCKS_CSS.index("ul[data-variant='checks'] > li::before"):][:900]
        self.assertIn('#dc2626', crosses)
        self.assertIn('#059669', checks)

    def test_neutral_tone_has_no_colour(self):
        """«Справочно» не должно выглядеть важным.

        До нейтрального тона сведения без окраски приходилось красить в info,
        то есть в акцент: три справки подряд читались как три уточнения, на
        которые надо обратить внимание.
        """
        rule = BLOCKS_CSS[BLOCKS_CSS.index("[data-wiki-block][data-tone='neutral']"):][:500]
        self.assertIn('--tone-bg: var(--wiki-surface-alt)', rule)
        self.assertIn('--tone-ink: var(--wiki-ink-soft)', rule)

    def test_guide_teaches_the_new_blocks(self):
        for needle in ('data-wiki-block="stats"', 'data-wiki-block="stat"',
                       'data-variant="crosses"', 'neutral'):
            self.assertIn(needle, markup.MARKUP_GUIDE)

    def test_guide_says_a_stat_is_a_number(self):
        """Показатель — число, а не предложение: иначе блок вырождается в плашку."""
        self.assertIn('ЧИСЛО, а не предложение', markup.MARKUP_GUIDE)


class BlockMenuCompletenessTest(unittest.TestCase):
    """Каждый вид блока обязан иметь пункт в меню редактора.

    Забыть пункт — самый тихий способ сломать фичу: блок есть в схеме, в
    санитайзере, в стилях и в наставлении для ИИ, статья с ним открывается
    правильно, но поставить его руками автор не может — и понять почему, не
    читая исходник, нельзя.
    """

    def test_every_kind_and_variant_has_a_menu_item(self):
        node = strip_comments(BLOCK_NODE)
        order = re.search(r'const MENU_ORDER = \[(.*?)\];', node, re.S)
        self.assertIsNotNone(order, 'список пунктов меню не найден')
        keys = set(re.findall(r"'([a-z]+)'", order.group(1)))
        # Сетки в меню представлены собой, ячейки — нет: карточку добавляют
        # кнопкой «+» у самой сетки, отдельного пункта у неё быть не должно.
        cells = {item for _grid, item in markup.GRIDS}
        for kind in markup.BLOCK_KINDS:
            if kind in cells:
                self.assertNotIn(kind, keys, 'ячейка %s не должна быть пунктом меню' % kind)
                continue
            self.assertIn(kind, keys, 'вид %s нельзя вставить из редактора' % kind)
        for variant in markup.LIST_VARIANTS:
            self.assertIn(variant, keys,
                          'вид списка %s нельзя поставить из редактора' % variant)
