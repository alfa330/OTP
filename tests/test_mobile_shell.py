# -*- coding: utf-8 -*-
"""Страж мобильной оболочки портала: нижний бар разделов вместо бокового меню.

На телефоне навигация устроена иначе, чем на компьютере: четыре верхних раздела
стоят баром у нижней грани, пятая кнопка — аватар — выдвигает шторку со всеми
разделами, колокол висит в правом верхнем углу экрана, а при повороте бар
остаётся у той же грани КОРПУСА, становясь вертикальной полосой у края.

ПОЧЕМУ ЭТО СТЕРЕЖЁТ PYTHON, А НЕ ГЛАЗ. Половина решений здесь невидима на
рабочем месте: разработчик сидит за широким монитором, где оболочка выключена
целиком. Отвалившееся правило заметит только человек с телефоном в руках — и,
как правило, молча, потому что «меню не открывается» люди списывают на связь.

Границы, за которыми правка перестаёт быть косметической.

  * ОДНО УСЛОВИЕ «МЫ НА ТЕЛЕФОНЕ». Признак сложнее ширины окна: телефон боком —
    это 844×390, то есть настольная ширина при телефонной высоте. Условие живёт
    одной строкой в src/utils/mobileShell.js и попадает в CSS классом на <body>.
    Вторая копия (медиазапрос в стилях) разъезжается с первой и даёт либо бар
    без места под него, либо место под бар без бара.

  * ТОЧКА ОТСЧЁТА ШТОРКИ. Внутри сайдбара живут выпадающие подменю с
    посчитанными координатами (position: fixed). transform у шторки делает её
    точкой отсчёта для них: сдвинутый угол увёл бы подменю мимо своих пунктов.
    Поэтому лист прибит к (0,0) экрана, а место под бар вычитается отступом.

  * ОДИН СПИСОК РАЗДЕЛОВ. Шторка показывает ТОТ ЖЕ сайдбар, а не копию меню:
    забытый в одной из копий пункт пропадает у роли молча (см. «Бот опозданий»).

  * МЕСТО ПОД БАР. Оно нужно во всех трёх поворотах — иначе последняя строка
    раздела оказывается под кнопками и до неё нельзя дотянуться.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
SHELL_CSS = (ROOT / 'src' / 'components' / 'common' / 'mobile-shell.css').read_text(encoding='utf-8')
TAB_BAR = (ROOT / 'src' / 'components' / 'common' / 'MobileTabBar.jsx').read_text(encoding='utf-8')
SHELL_JS = (ROOT / 'src' / 'utils' / 'mobileShell.js').read_text(encoding='utf-8')
STYLES = (ROOT / 'src' / 'styles.css').read_text(encoding='utf-8')


def css_block(source, selector, size=600):
    """Кусок правил от селектора — читать целиком незачем, решения короткие."""
    at = source.index(selector)
    return source[at:at + size]


class ShellSwitchTests(unittest.TestCase):
    """Одно условие на разметку, стили и геометрию помощника."""

    def test_media_query_lives_in_one_place(self):
        self.assertIn("MOBILE_SHELL_QUERY = '(max-width: 768px), (orientation: landscape) and (max-height: 540px)'", SHELL_JS)
        # В стилях оболочки медиазапроса по ширине быть не должно: там класс.
        self.assertNotIn('max-width: 768px', SHELL_CSS)
        self.assertIn('body.mobile-shell', SHELL_CSS)

    def test_body_class_is_set_by_the_hook(self):
        """Класс ставит тот же хук, что отдаёт состояние разметке: иначе бар и
        место под него могут разойтись на кадр — заметная дрожь при входе."""
        self.assertIn('body.classList.toggle(MOBILE_SHELL_CLASS, state.shell);', TAB_BAR)
        self.assertIn("body.dataset.tabbarSide = state.shell ? state.side : '';", TAB_BAR)
        self.assertIn('body.classList.remove(MOBILE_SHELL_CLASS);', TAB_BAR)

    def test_rotation_is_watched_separately_from_the_query(self):
        """Поворот меняет сторону бара, не трогая сам факт «мы на телефоне»:
        на один change медиазапроса бар остался бы висеть у прежней грани."""
        self.assertIn("win.addEventListener('orientationchange', handle);", SHELL_JS)
        self.assertIn("win.screen?.orientation?.addEventListener?.('change', handle);", SHELL_JS)
        self.assertIn("win.addEventListener('resize', handle);", SHELL_JS)
        # И снимаются все три — иначе слушатели копятся на каждом входе.
        self.assertIn("win.removeEventListener('orientationchange', handle);", SHELL_JS)
        self.assertIn("win.screen?.orientation?.removeEventListener?.('change', handle);", SHELL_JS)
        self.assertIn("win.removeEventListener('resize', handle);", SHELL_JS)

    def test_legacy_media_listener_is_kept(self):
        """addListener — для Safari старше 14: там addEventListener у
        MediaQueryList ещё нет, а портал открывают и с таких телефонов."""
        self.assertIn('media.addListener', SHELL_JS)


class TabBarTests(unittest.TestCase):
    """Сам бар: где стоит, что показывает, как ведёт себя при повороте."""

    def test_bar_is_a_sibling_of_the_content(self):
        """Внутри main-content на части разделов стоит overflow-hidden, а в вики
        на нём живёт zoom — position: fixed считался бы от него."""
        at = APP.index('<MobileTabBar')
        self.assertLess(APP.index('<AssistantOrb'), APP.index('</div>', at) + 4000)
        self.assertIn('{isMobileShell && (\n                        <MobileTabBar', APP)

    def test_bar_carries_four_sections_and_the_avatar(self):
        self.assertIn('items={mobileTabItems}', APP)
        self.assertIn('side={mobileTabSide}', APP)
        # Пятая кнопка — вход в шторку, и это единственный вход: без неё часть
        # разделов становится недостижимой.
        self.assertIn('mtb-item--profile', TAB_BAR)
        self.assertIn('onClick={onToggleMenu}', TAB_BAR)
        self.assertIn('aria-expanded={menuOpen}', TAB_BAR)

    def test_navigation_goes_through_the_sidebar_handler(self):
        """У перехода в раздел должен быть один путь: у сайдбара он умеет и
        открытие в новой вкладке, и побочные действия отдельных разделов."""
        self.assertIn('onSelect={handleSidebarViewNavigation}', APP)
        self.assertIn('onSelect?.(event, item.view);', TAB_BAR)

    def test_bar_stays_visible_above_the_sheet(self):
        """Шторка выезжает ПОД баром, как в Telegram: бар — постоянная опора."""
        bar_z = int(re.search(r'z-index:\s*(\d+);', css_block(SHELL_CSS, '.mobile-tabbar {')).group(1))
        sheet_z = int(re.search(r'z-index:\s*(\d+);', css_block(SHELL_CSS, 'body.mobile-shell .sidebar {')).group(1))
        overlay_z = int(re.search(r'z-index:\s*(\d+);', css_block(SHELL_CSS, 'body.mobile-shell .sidebar-overlay {')).group(1))
        self.assertGreater(bar_z, sheet_z, 'бар уехал под шторку — навигация пропала')
        self.assertGreater(sheet_z, overlay_z, 'шторка оказалась под собственным затемнением')

    def test_safe_areas_are_respected_in_every_rotation(self):
        for side, inset in (('bottom', 'bottom'), ('right', 'right'), ('left', 'left')):
            block = css_block(SHELL_CSS, f'.mobile-tabbar[data-side="{side}"] {{')
            self.assertIn(f'env(safe-area-inset-{inset})', block,
                          f'бар у грани {side} налезает на безопасную зону')

    def test_content_gets_room_for_the_bar(self):
        """Без отступа последняя строка раздела прячется под кнопками."""
        self.assertIn('padding-bottom: calc(var(--mtb-thickness) + env(safe-area-inset-bottom));',
                      css_block(SHELL_CSS, 'body.mobile-shell .main-content {'))
        self.assertIn('padding-right: calc(var(--mtb-thickness) + env(safe-area-inset-right));',
                      css_block(SHELL_CSS, 'body[data-tabbar-side="right"].mobile-shell .main-content {'))
        self.assertIn('padding-left: calc(var(--mtb-thickness) + env(safe-area-inset-left));',
                      css_block(SHELL_CSS, 'body[data-tabbar-side="left"].mobile-shell .main-content {'))


class SheetTests(unittest.TestCase):
    """Шторка со всеми разделами — тот же сайдбар, выехавший снизу."""

    def test_sheet_is_the_same_sidebar(self):
        """Второй список разделов для телефона завёл бы вторую копию меню —
        пункт, забытый в одной из них, пропадает у роли молча."""
        self.assertEqual(APP.count('<ul ref={sidebarMenuScrollRef}'), 1)
        self.assertNotIn('MobileMenuList', APP)

    def test_sheet_is_anchored_to_the_screen_corner(self):
        """Лист прибит к (0,0), а место под бар вычитается отступом: в открытом
        состоянии у него transform: none, потому что ЛЮБОЙ transform делает его
        точкой отсчёта для position: fixed внутри — а там выпадающие подменю с
        посчитанными координатами."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .sidebar {', 1400)
        self.assertIn('top: 0;', block)
        self.assertIn('left: 0;', block)
        self.assertIn('transform: none;', css_block(SHELL_CSS, 'body.mobile-shell .sidebar.mobile-open {'))
        inner = css_block(SHELL_CSS, 'body.mobile-shell .sidebar > div {')
        self.assertIn('calc(var(--mtb-thickness) + env(safe-area-inset-bottom))', inner)

    def test_sheet_appears_instead_of_flying_from_the_bottom(self):
        """Лист занимает весь экран, и поездка от нижнего края читалась как
        «страница прилетела откуда-то издалека» (решение владельца)."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .sidebar {', 1400)
        self.assertNotIn('translateY(100%)', block)
        self.assertIn('opacity: 0;', block)
        self.assertIn('transform: scale(0.97);', block)

    def test_exit_sits_at_the_very_bottom(self):
        """«Выйти» — под всеми разделами: так его не нажимают случайно,
        разыскивая раздел."""
        exit_at = APP.index('mobile-sheet-row--exit')
        list_end = APP.index('<ul className="sidebar-footer-menu')
        menu_start = APP.index('<ul ref={sidebarMenuScrollRef}')
        self.assertTrue(menu_start < exit_at < list_end, '«Выйти» не в хвосте листа')
        self.assertIn('.mobile-sheet-account--tail', SHELL_CSS)

    def test_sheet_closes_on_navigation(self):
        """Иначе выбранный раздел открывается за шторкой, и её приходится
        закрывать вручную — на каждый переход."""
        at = APP.index('const navigateToView = useCallback((nextView) => {')
        self.assertIn('setMobileMenuOpen(false);', APP[at:at + 600])

    def test_hidden_logo_does_not_take_away_four_you(self):
        """Логотип над портретом убран (решение владельца), а вход в «4 You» на
        компьютере есть ТОЛЬКО через него: пункт меню показан лишь читателям
        (canAccessFourYouSection && !canManage...). Значит тем, кто раздел
        ведёт, нужна своя строка в шапке — иначе раздел пропадает молча."""
        self.assertIn('display: none;', css_block(SHELL_CSS, 'body.mobile-shell .sidebar h1 {', 200))
        at = APP.index('{canManageFourYouSection && (')
        block = APP[at:at + 700]
        self.assertIn("handleSidebarViewNavigation(e, 'four_you')", block)
        self.assertIn('<span>4 You</span>', block)

    def test_orb_is_off_on_a_phone(self):
        """Плавающий помощник на экране 390 px закрывает собой раздел и
        читается как случайно прилипший пузырь."""
        self.assertIn('{!isMobileShell && (\n                    <AssistantOrb', APP)

    def test_desktop_sidebar_lift_is_off_on_a_phone(self):
        """Подъём сайдбара над окном «Задач» (z 100) на телефоне поднял бы
        шторку и над баром — вход в разделы пропал бы под ней."""
        self.assertIn('body:not(.mobile-shell).sheet-beside-sidebar .sidebar {', STYLES)


class SheetAccountTests(unittest.TestCase):
    """Шапка шторки: портрет, имя и действия над своей учёткой."""

    def test_account_actions_live_in_the_header(self):
        """Пункт «Аккаунт» раскрывался ВБОК, а вбок на всю ширину экрана
        раскраваться некуда: на телефоне за сменой пароля пришлось бы
        прокручивать меню до низа и открывать панель, которой не видно."""
        at = APP.index('<div className="mobile-sheet-account">')
        block = APP[at:at + 7000]
        # «Выйти» переехал в хвост листа — его проверяет test_exit_sits_at_the_very_bottom.
        for label in ('Сменить логин', 'Сменить пароль', 'Сменить фотографию'):
            self.assertIn(f'<span>{label}</span>', block, f'действие «{label}» пропало из шапки')
        # Установка портала на телефон — там же, своим пунктом.
        self.assertIn('<InstallAppMenuItem onPicked={() => setMobileMenuOpen(false)} />', block)
        # Смена фото — только тем, кому она разрешена; условие то же, что в меню.
        self.assertIn('{canChangeAccountAvatar && (', block)

    def test_actions_close_the_sheet(self):
        """Формы смены логина и пароля рисуются в разделе, ПОД шторкой: не
        закрыв её, человек нажимает кнопку и не видит никакого ответа."""
        at = APP.index('<div className="mobile-sheet-group">')
        block = APP[at:at + 4000]
        self.assertEqual(block.count('setMobileMenuOpen(false);'), 3)

    def test_footer_is_hidden_and_not_duplicated(self):
        """Два места жизни у одних и тех же действий — это два места, где их
        забудут обновить. Футер сайдбара на телефоне не рисуется вовсе."""
        self.assertIn('display: none;', css_block(SHELL_CSS, 'body.mobile-shell .sidebar-footer-menu {', 120))

    def test_avatar_still_switches_the_dark_theme(self):
        """Тап по портрету — переключатель тёмного режима у тех, кому он выдан;
        на телефоне это единственное место, где он остался."""
        at = APP.index('<div className="mobile-sheet-account">')
        block = APP[at:at + 1500]
        self.assertIn('onClick={darkThemeAllowed ? toggleDarkTheme : undefined}', block)


class SheetLooksTests(unittest.TestCase):
    """Вид шторки — список настроек телефона, а не тёмный сайдбар.

    Разметка пунктов приходит из общего дерева меню и рассчитана на тёмную
    полосу слева (белый текст, синяя подсветка). Перекрашивает её оболочка,
    поэтому проверяем именно те решения, без которых список рассыпается.
    """

    def test_sheet_is_a_light_settings_list(self):
        block = css_block(SHELL_CSS, 'body.mobile-shell .sidebar {', 1500)
        self.assertIn('background-color: var(--sheet-bg) !important;', block)
        self.assertIn('background-image: none !important;', block)
        self.assertIn('color: var(--sheet-text);', block)

    def test_rows_are_grouped_into_cards(self):
        """Группы задают те же <hr>, что и в сайдбаре: своей разметки под
        телефон нет, иначе список пришлось бы поддерживать дважды."""
        self.assertIn('body.mobile-shell .sidebar-menu-scroll > hr + li > button', SHELL_CSS)
        self.assertIn('body.mobile-shell .sidebar-menu-scroll > li:has(+ hr) > button', SHELL_CSS)
        self.assertIn('border-top-left-radius: var(--sheet-radius);', SHELL_CSS)

    def test_icon_tile_wins_over_inline_size(self):
        """Размер значка приходит инлайном (FaIcon ставит width/height в 1em),
        а ширину в сайдбаре держит ещё и flex-basis — без обоих перебиваний
        плитка остаётся 18-пиксельной точкой."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .sidebar-menu-scroll button > svg:first-child,', 900)
        self.assertIn('width: 29px !important;', block)
        self.assertIn('flex: 0 0 29px !important;', block)
        self.assertIn('border-radius: 9px;', block)

    def test_whole_sheet_scrolls(self):
        """Портрет с именем должен уезжать вверх вместе со списком — иначе он
        занимает треть экрана всегда."""
        self.assertIn('overflow-y: auto;', css_block(SHELL_CSS, 'body.mobile-shell .sidebar > div {', 400))
        self.assertIn('overflow: visible !important;', css_block(SHELL_CSS, 'body.mobile-shell .sidebar-menu-scroll {', 300))
        # Подменю позиционируются посчитанными координатами и обязаны ехать следом.
        self.assertEqual(APP.count("sheetScrollEl?.addEventListener('scroll', scheduleUpdatePos, { passive: true });"), 2)
        self.assertEqual(APP.count("sheetScrollEl?.removeEventListener('scroll', scheduleUpdatePos);"), 2)

    def test_every_row_gets_the_same_chevron(self):
        """Собственная стрелка есть только у пунктов с подменю, и рисуется она
        значком в 16 px против уголка в 7 px у остальных строк: двумя разными
        стрелками список выглядит собранным из кусков."""
        self.assertIn('body.mobile-shell .sidebar-menu-scroll button > svg:not(:first-child) {\n    display: none;', SHELL_CSS)
        self.assertIn('body.mobile-shell .sidebar-menu-scroll > li > button::after,', SHELL_CSS)

    def test_dark_theme_has_its_own_palette(self):
        block = css_block(SHELL_CSS, 'html[data-otp-theme="dark"] {', 700)
        self.assertIn('--sheet-bg: #000000;', block)
        self.assertIn('--sheet-card: #1c1c1e;', block)
        self.assertIn('--sheet-text: #ffffff;', block)


class SectionLayoutTests(unittest.TestCase):
    """Общий слой, приводящий содержимое разделов к телефонному формату.

    Разделы свёрстаны под окно компьютера: заголовки в 30–36 px, поля по 32 px,
    таблицы на восемь колонок, каркасы «панель слева — рабочая область справа».
    Ловить это в каждом разделе по отдельности — значит пропустить половину.
    """

    def test_page_scrolls_instead_of_the_section(self):
        """Внутренняя прокрутка раздела давала полосу сбоку, не давала браузеру
        прятать адресную строку и обрывала инерцию пальца."""
        self.assertIn('overflow: visible !important;',
                      css_block(SHELL_CSS, 'body.mobile-shell .flex.h-screen.overflow-hidden {', 300))
        # Правил с этим селектором два (место под бар и прокрутка) — ищем по файлу.
        self.assertIn('overflow: visible !important;\n    height: auto !important;', SHELL_CSS)
        self.assertIn('width: 0 !important;', css_block(SHELL_CSS, 'body.mobile-shell ::-webkit-scrollbar {', 200))

    def test_section_frame_is_dropped(self):
        """Карточка раздела на телефоне занимает весь экран, и её обводка
        превращается в лишнюю линию вдоль краёв."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .main-content > div,', 400)
        self.assertIn('border-width: 0 !important;', block)
        self.assertIn('box-shadow: none !important;', block)

    def test_bell_does_not_cover_the_first_row(self):
        """Колокол висит в правом верхнем углу экрана: без отступа он накрывал
        бы заголовок раздела и кнопку рядом с ним."""
        self.assertIn('padding-top: calc(46px + max(10px, env(safe-area-inset-top))) !important;', SHELL_CSS)

    def test_two_column_screens_stack(self):
        """Иначе правая колонка ужимается до восьмидесяти пикселей, а её
        содержимое торчит за экран (планировщик смен, учёт часов)."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .main-content .flex.items-start:has(> .flex-1),', 400)
        self.assertIn('flex-direction: column;', block)

    def test_wide_tables_scroll_inside_their_block(self):
        """Иначе вбок едет весь раздел вместе с шапкой и кнопками."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .main-content *:has(> table) {', 300)
        self.assertIn('overflow-x: auto;', block)

    def test_button_rows_wrap(self):
        """flex-wrap срабатывает только при нехватке места, поэтому правило
        безопасно для рядов, которые и так помещаются."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .main-content .flex[class*="gap-"]', 400)
        self.assertIn('flex-wrap: wrap;', block)

    def test_tab_strips_scroll_instead_of_wrapping(self):
        """Полосу вкладок («Обзор · Бэклог · Доска») переносом рвать нельзя."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .main-content .inline-flex[class*="rounded-"],', 900)
        self.assertIn('overflow-x: auto;', block)
        self.assertIn('flex-wrap: nowrap;', block)

    def test_tab_strips_cover_every_way_they_are_written(self):
        """Один и тот же приём в разделах написан по-разному, и правило,
        знающее лишь про bg-gray, оставляло полосу «Курсов» (bg-slate-100) на
        13 px шире экрана, а вкладки «Мои смены» (белая полоса с обводкой и
        overflow-hidden) переносило на вторую строку."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .main-content .inline-flex[class*="rounded-"],', 900)
        self.assertIn('.flex[class*="rounded-"][class*="bg-slate"]', block)
        self.assertIn('.flex[class*="rounded-"][class*="overflow-hidden"]', block)

    def test_tab_strips_leave_alone_rows_that_ask_for_wrapping(self):
        """Полосам, у которых перенос прописан в разметке, запрещать его
        нельзя: в «Отметках» и «Учете часов» такой ряд из десятка кнопок
        растянулся бы на 790 px — вдвое шире экрана, и половина ушла бы за
        край недосягаемой."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .main-content .inline-flex[class*="rounded-"],', 900)
        for selector in block.split('{')[0].split(','):
            selector = selector.strip()
            if 'bg-gray' in selector or not selector.startswith('body'):
                continue
            if '.flex[class*="rounded-"]' in selector:
                self.assertIn(':not([class*="flex-wrap"])', selector)

    def test_page_clips_sideways_without_becoming_a_scrollport(self):
        """Поперечную прокрутку обрезает старый мобильный блок в styles.css, и
        обрезать он обязан через clip, а не hidden.

        hidden по одной оси заставляет вторую посчитаться как auto, то есть
        делает страницу окном прокрутки, — и это ломало position: sticky во
        ВСЕХ разделах на телефоне: липкая шапка списка («Отделы», «Настройки
        SIP») начинала отсчитывать своё top: 0 от body, а тот ростом со всё
        содержимое и потому не прокручивается. Измерено на стенде: при
        прокрутке на 98 px шапка уезжала с 0 на −42 вместо того, чтобы
        остаться наверху. С clip — остаётся."""
        at = STYLES.index('@media (max-width: 640px) {')
        block = STYLES[at:at + 1400]
        self.assertIn('body, html {', block)
        self.assertIn('overflow-x: clip !important;', block)
        head = block[:block.index('body, html {') + block[block.index('body, html {'):].index('}')]
        self.assertNotIn('overflow-x: hidden', head)

    def test_shell_does_not_add_a_second_clip(self):
        """Двух обрезок быть не должно: разъехавшись, они дают ту же путаницу,
        ради ухода от которой условие «мы на телефоне» держат в одном месте.
        И внутри .main-content обрезки нет — она сделала бы раздел собственным
        окном прокрутки, то есть вернула бы то, от чего уходили страничной
        прокруткой."""
        content = css_block(SHELL_CSS, 'body.mobile-shell .main-content {\n    overflow: visible', 400)
        self.assertNotIn('overflow-x: hidden', content)
        self.assertNotIn('overflow-x: clip', content)
        # Голое правило body.mobile-shell завести можно (в нём живут жесты
        # страницы), но обрезки в нём быть не должно: она и есть та самая
        # вторая копия. Раньше запрет стоял на сам селектор — он оказался шире
        # своей причины и не пускал touch-action.
        if 'body.mobile-shell {' in SHELL_CSS:
            self.assertNotIn('overflow', css_block(SHELL_CSS, 'body.mobile-shell {', 200))

    def test_screen_height_rule_ignores_breakpoint_prefixes(self):
        """Правило «высота в экран → авто» отбирает классы по НАЧАЛУ имени.
        Поиск подстроки ловил и sm:max-h-[calc(...)] — ограничение, которое на
        телефоне не действует вовсе (брейкпоинт sm начинается с 640 px), но в
        атрибуте класса присутствует. Так модальному окну «Запроса на замену»
        сносило height у каркаса flex h-full flex-col: шапка и подвал
        переставали быть липкими, и кнопка «Отправить» уезжала за нижний край
        экрана — с этой жалобы правка и началась."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .main-content [class^="h-[calc"],', 600)
        self.assertIn('[class*=" h-[calc"]', block)
        self.assertIn('[class^="max-h-[calc"]', block)
        self.assertNotIn('[class*="h-[calc"],', block.split('{')[0].replace('[class*=" h-[calc"],', ''))

    def test_modal_covers_the_bar_and_hides_the_bell(self):
        """Окно на телефоне — отдельный экран, а не карточка посреди раздела.
        Настольные z-index'ы (SimpleModal 50, IosModal 90) писались, когда ни
        бара (70), ни углового колокола (71) не было: бар срезал подвал окна с
        главной кнопкой, а колокол висел поверх шапки, ровно на крестике."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .otp-modal-root {', 120)
        modal_z = int(re.search(r'z-index:\s*(\d+);', block).group(1))
        slot_z = int(re.search(r'z-index:\s*(\d+);', css_block(SHELL_CSS, '.mobile-bell-slot {', 400)).group(1))
        bar_z = int(re.search(r'z-index:\s*(\d+);', css_block(SHELL_CSS, '.mobile-tabbar {', 400)).group(1))
        self.assertGreater(modal_z, slot_z)
        self.assertGreater(modal_z, bar_z)
        self.assertIn('body.mobile-shell:has(.otp-modal-root) .mobile-bell-slot', SHELL_CSS)
        # Метку ставят все три примитива окон, иначе правило знает не про все.
        for path in ('src/App.jsx', 'src/components/ui/ios.jsx', 'src/components/common/FullscreenSheet.jsx'):
            self.assertIn('otp-modal-root', (ROOT / path).read_text(encoding='utf-8'), path)

    def test_modal_footer_clears_the_home_bar(self):
        """Подвал окна прижат к нижней грани экрана, и без отступа кнопка
        попадала бы прямо под домашнюю полосу."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .otp-modal-root .otp-modal-footer {', 200)
        self.assertIn('padding-bottom: max(0.75rem, env(safe-area-inset-bottom));', block)
        self.assertIn('otp-modal-footer', APP)

    def test_negative_side_margins_are_zeroed(self):
        """Липкие шапки списков («Отделы», «Настройки SIP») вылезали на 4 px
        за правый край: -mx-1 растягивает их до кромки карточки, а на телефоне
        карточка и так во весь экран."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .main-content [class^="-mx-"],', 250)
        self.assertIn('margin-left: 0 !important;', block)
        self.assertIn('margin-right: 0 !important;', block)

    def test_layer_is_locked_to_the_shell(self):
        """Ни одно правило слоя не должно действовать на компьютере."""
        start = SHELL_CSS.index('/* ── Содержимое разделов под телефонный формат')
        end = SHELL_CSS.index('/* Переключение раздела с бара')
        for rule in SHELL_CSS[start:end].split('}'):
            selector = rule.split('{')[0].strip()
            if not selector or selector.startswith(('/*', '@', '*')):
                continue
            for part in selector.split(','):
                part = part.strip()
                if not part or part.startswith(('/*', '@')):
                    continue
                self.assertTrue(
                    part.startswith('body.mobile-shell') or part.startswith('body[data-tabbar-side'),
                    f'правило «{part}» действует и на компьютере',
                )


class SubmenuTests(unittest.TestCase):
    """Подменю пунктов («Учет сотрудников», «Расчет ресурсов») в шторке."""

    def test_submenu_opens_below_the_item_on_a_phone(self):
        """В шторке пункт занимает ВСЮ ширину экрана, и привычное «вправо от
        пункта» уводит панель за край: снаружи это выглядит как «нажал, а
        ничего не открылось» — с этого и началась правка."""
        for ref in ('sidebarEmployeesRef', 'sidebarResourceRef'):
            at = APP.index(f'const rect = {ref}.current?.getBoundingClientRect();')
            block = APP[at:at + 700]
            self.assertIn('const nextTop = isMobileShell ? rect.bottom + 4 : rect.top;', block, ref)
            self.assertIn('const nextLeft = isMobileShell ? rect.left : rect.right + 8;', block, ref)

    def test_submenu_recalculates_when_the_shell_switches(self):
        """Иначе панель, открытая до поворота, осталась бы висеть по старым
        координатам — за краем экрана."""
        self.assertIn('}, [showSidebarEmployeesDropdown, isEmployeesClosing, isMobileShell]);', APP)
        self.assertIn('}, [showSidebarResourceDropdown, isResourceClosing, isMobileShell]);', APP)

    def test_submenu_width_is_freed_in_the_shell(self):
        """В разметке у панели фиксированные 14rem — размер под выпадение сбоку
        от узкого сайдбара; на телефоне она тянется до края экрана."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .sidebar .animate-dropdown,', 400)
        self.assertIn('width: auto !important;', block)
        self.assertIn('right: max(12px, env(safe-area-inset-right));', block)


class NotificationsSheetTests(unittest.TestCase):
    """Колокол на телефоне — лист у нижнего края, а не выпадающее меню."""

    BELL = (ROOT / 'src' / 'components' / 'notifications' / 'NotificationsBell.jsx').read_text(encoding='utf-8')

    def test_panel_is_a_bottom_sheet(self):
        """Вправо от колокола выпадать некуда, а до списка под самым верхом
        экрана не дотянуться большим пальцем.

        ЛИСТ ОПИРАЕТСЯ НА НИЖНЮЮ ГРАНЬ ЭКРАНА и накрывает бар разделов
        (решение владельца 10.09.2026: прежний вид он назвал плохим). До этого
        лист висел карточкой над баром — со скруглением по всем четырём углам,
        полоской полотна под ним и ярким баром поверх затемнения; читалось это
        как всплывшее сообщение, которое сейчас исчезнет, а не как лист.
        Скругление только сверху — то, чем лист отличается от карточки."""
        block = css_block(SHELL_CSS, '.mobile-bell-slot .notifications-dropdown {', 1200)
        self.assertIn('position: fixed;', block)
        self.assertIn('top: auto;', block)
        self.assertIn('bottom: 0;', block)
        self.assertIn('border-radius: 22px 22px 0 0;', block)
        self.assertIn('padding-bottom: env(safe-area-inset-bottom);', block)
        self.assertIn('backdrop-filter: blur(30px) saturate(180%);', block)

    def test_sheet_leaves_the_same_way_it_arrived(self):
        """Общий animate-dropdown-reverse — это сжатие по вертикали: лист
        складывался бы гармошкой у нижней грани вместо того, чтобы уехать
        вниз, откуда пришёл."""
        self.assertIn('@keyframes mtb-sheet-out', SHELL_CSS)
        block = css_block(SHELL_CSS, '.mobile-bell-slot .notifications-dropdown.animate-dropdown-reverse {', 300)
        self.assertIn('mtb-sheet-out', block)

    def test_mark_read_moves_out_of_the_head_on_a_phone(self):
        """В шапке листа уже стоит «Готово», и вторая ссылка рядом с
        заголовком не помещалась в строку: длинная подпись переносилась на
        второй ряд и рвала высоту шапки. На телефоне она уходит вниз листа
        отдельной строкой — как «Очистить» в системном центре уведомлений."""
        self.assertIn('{!isNarrow && clearable.length > 0 && (', self.BELL)
        self.assertIn('notifications-clear-all', self.BELL)
        self.assertIn('.mobile-bell-slot .notifications-clear-all', SHELL_CSS)

    def test_desktop_dropdown_keeps_its_own_row_markup(self):
        """Правка мобильной оболочки не должна менять настольный вид —
        прямое указание владельца 10.09.2026. Телефонная раскладка строки
        (заголовок в две строки, подпись раздела обычным регистром) живёт под
        isNarrow, а под ним прежняя настольная остаётся дословно."""
        self.assertIn('{isNarrow ? (', self.BELL)
        self.assertIn('notifications-item-title', self.BELL)
        self.assertIn('uppercase tracking-wide text-slate-400', self.BELL)
        self.assertIn('truncate text-[13.5px] font-medium text-slate-900', self.BELL)

    def test_sheet_moves_aside_from_a_side_bar(self):
        """Боком бар занимает край экрана — лист обязан отойти от него."""
        self.assertIn('body[data-tabbar-side="right"] .mobile-bell-slot .notifications-dropdown', SHELL_CSS)
        self.assertIn('body[data-tabbar-side="left"] .mobile-bell-slot .notifications-dropdown', SHELL_CSS)

    def test_backdrop_and_done_button_exist(self):
        """Лист занимает почти весь экран: без подложки непонятно, что портал
        под ним жив, а без «Готово» — как закрыть список."""
        self.assertIn("const backdrop = isNarrow && (open || closing)", self.BELL)
        self.assertIn('createPortal(', self.BELL)
        self.assertIn('className={`notifications-backdrop${open && !closing ? \' is-open\' : \'\'}`}', self.BELL)
        self.assertIn('{isNarrow && (', self.BELL)
        self.assertIn('Готово', self.BELL)

    def test_dark_sheet_repaints_the_light_markup(self):
        """Разметка панели написана под светлую подложку (text-slate-900,
        text-slate-500): на чёрном листе «Всё прочитано» становилось чёрным на
        чёрном."""
        self.assertIn('html[data-otp-theme="dark"] .mobile-bell-slot .notifications-dropdown [class*="text-slate-9"]', SHELL_CSS)
        self.assertIn('color: var(--sheet-text) !important;', SHELL_CSS)
        self.assertIn('color: var(--sheet-muted) !important;', SHELL_CSS)

    def test_bell_can_be_dragged_away(self):
        """Колокол висит ПОВЕРХ содержимого, и правый верхний угол занимает не
        только он: у инструкции аукциона смен там крестик, и закрыть её на
        телефоне было нельзя вовсе — тап приходил колоколу. Поэтому колокол
        двигается; подробности жеста стережёт tests/mobile_bell_drag.test.mjs."""
        self.assertIn('<MobileBellSlot userId={user?.id} side={mobileTabSide}>{bell}</MobileBellSlot>', APP)
        self.assertNotIn('<div className="mobile-bell-slot">', APP)
        # Запрет прокрутки — только под кнопкой: лист уведомлений лежит внутри
        # того же слота, и запрет на слоте убил бы прокрутку списка.
        self.assertIn('.mobile-bell-slot > div > button *', SHELL_CSS)
        self.assertNotIn('touch-action', css_block(SHELL_CSS, '.mobile-bell-slot {', 200))

    def test_backdrop_sits_under_the_sheet_and_over_the_bar(self):
        backdrop_z = int(re.search(r'z-index:\s*(\d+);', css_block(SHELL_CSS, '.notifications-backdrop {', 400)).group(1))
        slot_z = int(re.search(r'z-index:\s*(\d+);', css_block(SHELL_CSS, '.mobile-bell-slot {', 400)).group(1))
        bar_z = int(re.search(r'z-index:\s*(\d+);', css_block(SHELL_CSS, '.mobile-tabbar {', 400)).group(1))
        self.assertLess(backdrop_z, slot_z, 'подложка накрыла бы сам колокол')
        self.assertLess(bar_z, slot_z, 'лист уехал бы под бар разделов')


class ScrollTitleTests(unittest.TestCase):
    """Шапка «Профиля», проявляющаяся при прокрутке (решение владельца
    10.09.2026: «как у телеграмма — прокручиваешь вниз, и твоё имя
    показывается сверху»)."""

    TITLE = (ROOT / 'src' / 'components' / 'common' / 'MobileScrollTitle.jsx').read_text(encoding='utf-8')

    def test_it_watches_instead_of_listening_to_scroll(self):
        """onscroll на телефоне зовётся на каждый кадр движения пальца, и
        setState в нём — это перерисовка раздела шестьдесят раз в секунду.
        Наблюдатель будит нас ровно дважды: имя скрылось и вернулось."""
        self.assertIn('new IntersectionObserver(', self.TITLE)
        self.assertNotIn("addEventListener('scroll'", self.TITLE)

    def test_bar_goes_through_a_portal(self):
        """Внутри .main-content шапке мешают двое: свой overflow-x у раздела
        (обрезал бы размытие по краям) и верхний отступ под колокол — полоса
        встала бы на 56 px ниже, чем нужно."""
        self.assertIn('createPortal(', self.TITLE)
        self.assertIn('document.body,', self.TITLE)

    def test_bar_is_mobile_only(self):
        """На компьютере имя и так на виду, а раздел не прокручивается
        страницей — наблюдать не за чем."""
        self.assertIn('if (!active', self.TITLE)
        self.assertIn('active={isMobileShell}', APP)

    def test_bar_leaves_room_for_the_corner_bell(self):
        """Колокол висит в том же углу: без запаса длинное имя заезжало бы
        прямо под него, а сам колокол обязан оставаться нажимаемым."""
        block = css_block(SHELL_CSS, '.mobile-scroll-title {', 900)
        bar_z = int(re.search(r'z-index:\s*(\d+);', block).group(1))
        slot_z = int(re.search(r'z-index:\s*(\d+);', css_block(SHELL_CSS, '.mobile-bell-slot {', 400)).group(1))
        self.assertLess(bar_z, slot_z)
        self.assertIn('pointer-events: none;', block)
        self.assertIn('62px', block)

    def test_profile_hero_is_the_watched_element(self):
        """Наблюдать надо именно за крупным портретом с именем: шапка
        появляется тогда, когда он уходит ПОД неё."""
        self.assertIn('const profileHeroRef = useRef(null);', APP)
        self.assertIn('watch={profileHeroRef}', APP)
        self.assertIn('<div ref={profileHeroRef} className="flex flex-col sm:flex-row items-center', APP)


class GestureTests(unittest.TestCase):
    """Жесты страницы: приближение выключено, прокрутка ведёт себя как в
    мобильном приложении.

    Обе поломки видны только с телефоном в руках. Наезд на поле при фокусе
    уводит экран в масштаб, из которого он сам не возвращается: бар разделов
    оказывается за краем, колокол повисает посреди содержимого. А фон,
    проезжающий под открытым листом, человек замечает только после закрытия —
    он оказывается в другом месте раздела и не понимает почему."""

    INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')
    BELL = (ROOT / 'src' / 'components' / 'notifications' / 'NotificationsBell.jsx').read_text(encoding='utf-8')
    LOCK = (ROOT / 'src' / 'utils' / 'pageScrollLock.js').read_text(encoding='utf-8')

    def test_page_zoom_is_off(self):
        """Решение владельца 10.09.2026: портал на телефоне работает как
        приложение. maximum-scale снимает наезд iOS на поле при фокусе (в
        портале десятки полей по 12–14px), touch-action — двойной тап."""
        meta = re.search(r'<meta name="viewport" content="([^"]+)"', self.INDEX).group(1)
        self.assertIn('maximum-scale=1', meta)
        self.assertIn('user-scalable=no', meta)
        # Безопасная зона остаётся: без неё содержимое залезет под чёлку.
        self.assertIn('viewport-fit=cover', meta)
        self.assertIn('touch-action: manipulation;', css_block(SHELL_CSS, 'body.mobile-shell {', 200))

    def test_page_is_frozen_under_an_open_sheet(self):
        """Шторка разделов и лист уведомлений — оба."""
        self.assertIn('useEffect(() => holdPageScroll(isMobileShell && mobileMenuOpen), [isMobileShell, mobileMenuOpen]);', APP)
        self.assertIn('useEffect(() => holdPageScroll(isNarrow && (open || closing)), [isNarrow, open, closing]);', self.BELL)

    def test_lock_pins_the_body_and_counts_holders(self):
        """overflow: hidden на <body> в Safari на iOS прокрутку не
        останавливает вовсе — там прокручивается сам документ. И счётчик, а не
        флаг: колокол открывается поверх шторки, и первый же закрывшийся снял
        бы замок у обоих."""
        self.assertIn("style.position = 'fixed';", self.LOCK)
        self.assertIn('style.top = `${-savedScrollY}px`;', self.LOCK)
        self.assertNotIn("overflow = 'hidden'", self.LOCK)
        self.assertIn('depth += 1;', self.LOCK)
        self.assertIn('if (depth > 1) return;', self.LOCK)
        # Вернуть страницу на прежнее место обязательно: иначе закрытие листа
        # выбрасывает человека в начало раздела.
        self.assertIn('window.scrollTo(0, savedScrollY);', self.LOCK)

    def test_scroll_does_not_leak_between_layers(self):
        """Докрутив список листа или таблицу до края, палец не должен качнуть
        то, что под ними, — и не должен выполнить жест «назад» браузера."""
        self.assertIn('overscroll-behavior: contain;', css_block(SHELL_CSS, 'body.mobile-shell .sidebar > div {', 900))
        self.assertIn('overscroll-behavior-x: contain;', SHELL_CSS)


class ViewSwitchTests(unittest.TestCase):
    """Переключение раздела с бара — движение, а не подмена картинки."""

    def test_switch_animation_is_restarted_on_every_change(self):
        at = APP.index("el.classList.remove('mobile-view-switch');")
        block = APP[at:at + 900]
        # Чтение offsetWidth — не мусор: без него снятие и возврат класса
        # схлопываются в один кадр, и анимация не проигрывается заново.
        self.assertIn('void el.offsetWidth;', block)
        self.assertIn("el.classList.add('mobile-view-switch');", block)
        self.assertIn("el.addEventListener('animationend', done, { once: true });", block)
        self.assertIn("el.removeEventListener('animationend', done);", block)

    def test_switch_is_mobile_only(self):
        """На компьютере разделы открывают из сайдбара — содержимое там не
        «переключается», и лишняя анимация выглядела бы дрожью."""
        at = APP.index("previousViewRef.current = view;")
        self.assertIn('if (!isMobileShell) return undefined;', APP[at:at + 200])
        self.assertIn('body.mobile-shell .main-content.mobile-view-switch {', SHELL_CSS)

    def test_reduced_motion_is_respected(self):
        """Системная настройка «меньше движения» отменяет поездки, а не саму
        возможность попасть в раздел."""
        at = SHELL_CSS.index('@media (prefers-reduced-motion: reduce)')
        block = SHELL_CSS[at:at + 500]
        self.assertIn('animation: none;', block)
        self.assertIn('transition: none;', block)


if __name__ == '__main__':
    unittest.main()
