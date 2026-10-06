# -*- coding: utf-8 -*-
"""Страж адреса вкладок вики (src/components/wiki/tabLink.js).

Владелец 16.09.2026: «сделай путь к каждому разделу вики, чтобы можно было
кидать ссылкой кому-то». До этого адрес был только у статьи: вкладки «Офисы»,
«Парки», «Помощник», «Журнал» жили состоянием React, и показать коллеге статус
офисов можно было только словами «зайди в вики и нажми Офисы».

Правила ссылки живут в JS-тесте (tests/wiki_tab_link.test.mjs). Здесь то, чего
он не видит, — СВЯЗЬ между модулем ссылки, разделом и порталом. Каждое из этих
мест ломается молча: ссылка открывается, раздел грузится, ошибки нет, а вкладка
не та.

КНОПОК «ССЫЛКА» БОЛЬШЕ НЕТ (владелец, 06.10.2026: «обе кнопки ссылка убрать, они
вообще не нужны») — ни в шапке раздела, ни на странице статьи. Сам адрес при
этом остался: вкладка и открытая статья по-прежнему пишутся в адресную строку и
читаются из неё, на этом стоят переходы из колокола и ссылки в тексте статей.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / 'src' / 'components' / 'wiki'
LINK = (WIKI / 'tabLink.js').read_text(encoding='utf-8')
VIEW = (WIKI / 'WikiView.jsx').read_text(encoding='utf-8')
ARTICLE = (WIKI / 'WikiArticle.jsx').read_text(encoding='utf-8')
LIBRARY = (WIKI / 'WikiLibrary.jsx').read_text(encoding='utf-8')
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')


def link_keys():
    """Ключи из WIKI_TAB_KEYS."""
    body = re.search(r'WIKI_TAB_KEYS\s*=\s*\[(.*?)\]', LINK, re.S)
    assert body, 'в tabLink.js нет WIKI_TAB_KEYS'
    return set(re.findall(r"'([a-z_]+)'", body.group(1)))


def view_tabs():
    """Ключи вкладок из набора WikiView.jsx (const tabs = useMemo(...))."""
    start = VIEW.index('const tabs = useMemo')
    end = VIEW.index('const canAskAssistant', start)
    return set(re.findall(r"\{\s*key:\s*'([a-z_]+)'", VIEW[start:end]))


class KeysTests(unittest.TestCase):

    def test_link_knows_every_tab_of_the_section(self):
        """Набор ключей закрыт, и разъехаться с вкладками он не имеет права.

        Ключа нет в списке — ссылка на вкладку не собирается и не разбирается:
        человек получает «?view=wiki» и открывает главную вместо обещанного.
        """
        self.assertEqual(link_keys(), view_tabs())

    def test_default_tab_is_the_one_section_opens_with(self):
        """Умолчание одно на модуль и на раздел: иначе ссылка на главную вела бы
        на вкладку, с которой раздел не открывается."""
        self.assertIn("WIKI_DEFAULT_TAB = 'library'", LINK)
        self.assertIn('useState(WIKI_DEFAULT_TAB)', VIEW)


class SectionTests(unittest.TestCase):

    def test_section_reads_the_tab_from_the_address(self):
        start = VIEW.index('const [requestedTab, setRequestedTab] = useState')
        chunk = VIEW[start:start + 900]
        self.assertIn('window.location.search', chunk)
        self.assertIn('readWikiTabFromSearch(search)', chunk)

    def test_article_link_beats_the_tab_link(self):
        """Статья живёт на витрине: просьба открыть «Офисы» поверх неё закрыла
        бы текст, ради которого человек перешёл по ссылке."""
        start = VIEW.index('const [requestedTab, setRequestedTab] = useState')
        chunk = VIEW[start:start + 900]
        self.assertLess(chunk.index('readArticleSlugFromSearch(search)'),
                        chunk.index('readWikiTabFromSearch(search)'))

    def test_requested_tab_waits_for_the_rights(self):
        """Вкладку из адреса ставим, только когда она ПОЯВИЛАСЬ в наборе.

        Набор считается из ответа /ping, то есть позже первого рендера: поставь
        мы «Офисы» сразу, эффект «активной вкладки больше нет» вернул бы человека
        на главную раньше, чем приедут права, — ссылка молча не работала бы.
        """
        self.assertRegex(
            VIEW,
            r'if \(tabs\.some\(\(t\) => t\.key === requestedTab\)\) \{\s*'
            r'setTab\(requestedTab\);',
        )

    def test_address_is_not_written_until_the_request_is_done(self):
        """Пока просьба из ссылки не выполнена, адрес не трогаем: первый рендер
        (tab ещё 'library') стёр бы метку, ради которой и перешли по ссылке."""
        sync = VIEW.index('syncWikiTabLink(tab')
        guard = VIEW.rindex('if (requestedTab) return;', 0, sync)
        self.assertLess(sync - guard, 400, 'страховка от преждевременной записи адреса ушла')

    def test_space_goes_into_the_link_only_when_there_are_several(self):
        """Метка пространства нужна там, где вик несколько: вкладки показываются
        по тумблерам пространства, и у одной вики это был бы шум в каждой ссылке."""
        self.assertIn('syncWikiTabLink(tab, spaces.length > 1 ? activeSpace?.id : null)',
                      VIEW)

    def test_link_from_the_address_beats_the_saved_space(self):
        """Присланная ссылка сильнее localStorage: иначе «Офисы» Таксопарков
        открылись бы в той вике, где получатель был в прошлый раз."""
        start = VIEW.index('const [spaceId, setSpaceId] = useState')
        chunk = VIEW[start:start + 700]
        self.assertIn('readWikiSpaceFromSearch', chunk)
        self.assertLess(chunk.index('readWikiSpaceFromSearch'),
                        chunk.index("localStorage.getItem('wiki:space')"))

    def test_address_is_still_written_on_every_switch(self):
        """Кнопку убрали, адрес — нет: по нему перезагрузка возвращает на
        вкладку, и его же копируют из адресной строки. Сторожится весь эффект
        вместе с зависимостями: с пустым их списком адрес писался бы один раз,
        при входе, и на второй же вкладке начинал врать."""
        code = ' '.join(_code(VIEW).split())
        self.assertIn(
            'useEffect(() => { if (requestedTab) return; '
            'syncWikiTabLink(tab, spaces.length > 1 ? activeSpace?.id : null); '
            '}, [tab, requestedTab, spaces.length, activeSpace]);', code)

    def test_open_article_is_still_written_to_the_address(self):
        """После снятия кнопки «Ссылка» адресная строка — единственное место,
        откуда берут ссылку на статью. Метка снимается при закрытии статьи:
        иначе адрес показывал бы то, чего на экране уже нет."""
        code = ' '.join(_code(LIBRARY).split())
        self.assertIn(
            'useEffect(() => { syncArticleDeepLink(openSlug); '
            'return () => syncArticleDeepLink(null); }, [openSlug]);', code)


def _code(source):
    """Исходник без комментариев: объяснение, ПОЧЕМУ кнопки нет, само называет
    её по имени, и проверка «слова нет в файле» ловила бы этот рассказ."""
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return re.sub(r'(?m)^\s*//[^\n]*$', '', source)


class NoCopyButtonsTests(unittest.TestCase):
    """Обе кнопки «Ссылка» убраны и не должны вернуться молча.

    Решение владельца 06.10.2026. Кнопки появились по его же просьбам (27.08 —
    у статьи, 16.09 — у вкладок) и занимали место в шапке и в панели статьи
    рядом с действиями, которыми пользуются каждый день.
    """

    def test_header_has_no_copy_button(self):
        code = _code(VIEW)
        self.assertNotIn('Скопировать ссылку', code)
        self.assertNotIn('copyTabLink', code)
        self.assertNotRegex(code, r'>\s*Ссылка\s*<')

    def test_article_has_no_copy_button(self):
        code = _code(ARTICLE)
        self.assertNotIn('Скопировать ссылку', code)
        self.assertNotIn('copyLink', code)
        self.assertNotRegex(code, r'>\s*Ссылка\s*<')

    def test_nothing_puts_a_link_into_the_clipboard(self):
        """Имя кнопки можно сменить, действие — нет: сторожим саму запись в
        буфер обмена. Значок сам по себе ничего не копирует, и запрещать его
        незачем."""
        for name, source in (('WikiView.jsx', VIEW), ('WikiArticle.jsx', ARTICLE)):
            code = _code(source)
            self.assertNotIn('navigator.clipboard', code, name)
            self.assertNotIn('execCommand', code, name)

    def test_link_module_has_no_dead_builder(self):
        """Собирать ссылку «в буфер» больше некому — функция без вызовов
        осталась бы обещанием кнопки, которой нет."""
        self.assertNotIn('buildWikiTabLink', LINK)
        self.assertNotIn('buildWikiTabLink', VIEW)

    def test_article_links_inside_the_text_still_work(self):
        """Убрана кнопка, а не адрес статьи: блок «Связанные материалы» и
        ссылки в тексте открывают статью по тому же адресу."""
        self.assertIn('href={buildArticleLink(row.slug)}', ARTICLE)
        self.assertIn('readArticleSlugFromHref', ARTICLE)


class PortalTests(unittest.TestCase):

    def test_leaving_wiki_drops_the_marks(self):
        """Обе двери портала снимают метки вкладки и пространства.

        buildAppViewUrl собирает href пунктов меню (Ctrl-клик открывает его в
        новой вкладке), syncAppViewWithUrl переписывает адрес при переходе. Не
        снять метку в любой из них — значит унести tab=offices в адрес «Задач».
        """
        for door in ('const buildAppViewUrl', 'const syncAppViewWithUrl'):
            start = APP.index(door)
            # Функция кончается там, где начинается следующее объявление модуля.
            end = APP.index('\nconst ', start + len(door))
            chunk = APP[start:end]
            self.assertIn('WIKI_TAB_QUERY_PARAM', chunk, door)
            self.assertIn('WIKI_SPACE_QUERY_PARAM', chunk, door)

    def test_marks_are_taken_from_the_module(self):
        """Имена параметров — из tabLink.js, а не переписаны строкой: вторая
        копия 'tab' разъехалась бы с разделом молча."""
        self.assertIn("from './components/wiki/tabLink'", APP)
        self.assertNotIn("const WIKI_TAB_QUERY_PARAM", APP)


if __name__ == '__main__':
    unittest.main()
