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
        """transform делает лист точкой отсчёта для fixed-подменю внутри:
        сдвинутый угол увёл бы «Учет сотрудников» и «Расчет ресурсов» мимо
        своих пунктов. Поэтому лист в (0,0), а место под бар — отступом."""
        block = css_block(SHELL_CSS, 'body.mobile-shell .sidebar {')
        self.assertIn('top: 0;', block)
        self.assertIn('left: 0;', block)
        self.assertIn('transform: translateY(100%);', block)
        self.assertIn('transform: translateY(0);', css_block(SHELL_CSS, 'body.mobile-shell .sidebar.mobile-open {'))
        inner = css_block(SHELL_CSS, 'body.mobile-shell .sidebar > div {')
        self.assertIn('padding-bottom: calc(var(--mtb-thickness) + env(safe-area-inset-bottom));', inner)

    def test_sheet_closes_on_navigation(self):
        """Иначе выбранный раздел открывается за шторкой, и её приходится
        закрывать вручную — на каждый переход."""
        at = APP.index('const navigateToView = useCallback((nextView) => {')
        self.assertIn('setMobileMenuOpen(false);', APP[at:at + 600])

    def test_logo_survives_on_a_phone(self):
        """У тех, кто ведёт «4 You», вход в раздел только через логотип: пункт
        меню показан лишь читателям (canAccessFourYouSection && !canManage...).
        Спрятать логотип на телефоне значит отнять у них раздел целиком."""
        self.assertIn('.sidebar-logo-full', SHELL_CSS)
        block = css_block(SHELL_CSS, 'body.mobile-shell .sidebar .sidebar-logo-full {')
        self.assertIn('display: flex !important;', block)

    def test_desktop_sidebar_lift_is_off_on_a_phone(self):
        """Подъём сайдбара над окном «Задач» (z 100) на телефоне поднял бы
        шторку и над баром — вход в разделы пропал бы под ней."""
        self.assertIn('body:not(.mobile-shell).sheet-beside-sidebar .sidebar {', STYLES)


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
