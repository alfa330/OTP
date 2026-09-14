"""Раздел «Сессии» на телефоне.

Решения здесь найдены на стенде (14.09.2026, iPhone 390×844) и без стража
тихо теряются при следующей правке раздела: сборка проходит, компьютер
выглядит как прежде, а на телефоне снова таблица, которая ездит вбок, или
красная «Прервать все» в каждой строке. Подробности — в шапке
src/components/sessions/sessions-mobile.css и в SessionsMobileView.jsx.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = ROOT / 'src' / 'components' / 'sessions'
VIEW = (SESSIONS / 'SessionsMobileView.jsx').read_text(encoding='utf-8')
MODAL = (SESSIONS / 'SessionUserModal.jsx').read_text(encoding='utf-8')
USER_AGENT = (SESSIONS / 'userAgent.js').read_text(encoding='utf-8')
CSS = (SESSIONS / 'sessions-mobile.css').read_text(encoding='utf-8')
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')

PHONE_PREFIXES = ('body.mobile-shell', 'html[data-otp-theme="dark"] body.mobile-shell')


def without_comments(source):
    """Код без комментариев: в шапке компонента прежний вид описан словами,
    и цитата «Прервать все» из объяснения не должна читаться как кнопка."""
    source = re.sub(r'\{/\*.*?\*/\}', '', source, flags=re.S)
    return re.sub(r'/\*.*?\*/', '', source, flags=re.S)


VIEW_CODE = without_comments(VIEW)


def css_rules(css):
    """Пары (селекторы, тело) без комментариев и @keyframes."""
    text = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    text = re.sub(r'@keyframes\s+[\w-]+\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', '', text)
    return [(sel.strip(), body) for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', text)]


def rule_with(css, selector_part):
    for selectors, body in css_rules(css):
        if selector_part in selectors:
            return selectors, body
    raise AssertionError(f'нет правила с {selector_part}')


def class_tokens(source):
    """Все лексемы классов из className: строкой, шаблоном и выражением."""
    tokens = []
    for plain, template, expr in re.findall(
            r'className=(?:"([^"]*)"|\{`([^`]*)`\}|\{([^}]*)\})', source):
        chunks = [plain] if plain else []
        if template:
            chunks.append(re.sub(r'\$\{[^}]*\}', ' ', template))
            chunks.extend(re.findall(r"'([^']*)'", template))
        if expr:
            chunks.extend(re.findall(r"'([^']*)'", expr))
        for chunk in chunks:
            tokens.extend(chunk.split())
    return tokens


# Подстроки, по которым общий слой разделов mobile-shell.css отбирает элементы
# и переносит ряды, срезает сетки, ужимает ширину.
SHELL_TRAPS = ('-actions', '-filters', '-tabs', 'topbar', 'toolbar', 'dropdown',
               'w-[', 'grid-cols-', 'h-[calc', '-mx-', 'group-hover:', 'max-w-')
LAYOUT_UTILITIES = {'flex', 'inline-flex', 'grid', 'flex-nowrap', 'fixed'}


def shell_trap_violations(source):
    bad = []
    for token in class_tokens(source):
        if token.startswith('mobile-actions'):
            continue  # общий лист действий: его строки и так живут под общим слоем
        if token in LAYOUT_UTILITIES or any(trap in token for trap in SHELL_TRAPS):
            bad.append(token)
    return bad


def panel_source():
    start = APP.index('const SessionsPanel = ({')
    return APP[start:APP.index('const _SkPulse', start)]


class PhoneLayerTests(unittest.TestCase):
    def test_every_rule_is_locked_to_the_phone_shell(self):
        """На компьютере слой не трогает ничего: раздел там остаётся прежним."""
        for selectors, _ in css_rules(CSS):
            for selector in selectors.split(','):
                selector = ' '.join(selector.split())
                self.assertTrue(selector.startswith(PHONE_PREFIXES),
                                f'правило не заперто на телефон: {selector}')

    def test_layer_is_imported_by_both_screens(self):
        """Список и карточка — разные ленивые куски; стили нужны обоим."""
        self.assertIn("import './sessions-mobile.css';", VIEW)
        self.assertIn("import './sessions-mobile.css';", MODAL)

    def test_list_markup_avoids_what_the_shell_layer_catches(self):
        """Общий слой разделов отбирает элементы по подстроке класса. Своя
        разметка списка обязана его не задевать, иначе он молча перенесёт ряд
        или срежет сетку."""
        self.assertEqual(shell_trap_violations(VIEW), [])

    def test_the_trap_guard_catches_a_forgery(self):
        fake = ('<div className="ses-m-toolbar flex">'
                '<button className={`ses-m-seg-btn${on ? \' is-on grid-cols-3\' : \'\'}`} />'
                '<span className={x ? \'inline-flex\' : \'ses-m-ok\'} /></div>')
        self.assertEqual(sorted(shell_trap_violations(fake)),
                         ['flex', 'grid-cols-3', 'inline-flex', 'ses-m-toolbar'])

    def test_title_is_not_a_heading_tag(self):
        """h1–h3 в .main-content общий слой ужимает до 18–22 px, а крупный
        заголовок телефона — 30 px."""
        self.assertIsNone(re.search(r'<h[1-3][\s>]', VIEW))
        self.assertIn('role="heading"', VIEW)

    def test_font_ladder_takes_whole_tokens_only(self):
        """sm:text-[13px] на телефоне не действует и под лестницу попадать не должен."""
        self.assertIn('[class~="text-[13px]"]', CSS)
        self.assertNotIn('[class*="text-[', CSS)


class PanelBranchTests(unittest.TestCase):
    def test_phone_gets_its_own_layout_and_desktop_keeps_the_table(self):
        panel = panel_source()
        self.assertIn('const isMobileShell = useIsMobileShell();', panel)
        branch = panel.index('if (isMobileShell) {')
        self.assertLess(branch, panel.index('<SessionsMobileView'))
        self.assertLess(panel.index('<SessionsMobileView'), panel.index('<table'))

    def test_hook_runs_before_the_early_return(self):
        """Хук после раннего возврата «Загрузка сессий…» менял бы число хуков
        между рендерами — React падает."""
        panel = panel_source()
        self.assertLess(panel.index('useIsMobileShell()'),
                        panel.index('if (isAdminSessionsLoading && people.length === 0'))

    def test_one_employee_card_for_both_layouts(self):
        panel = panel_source()
        self.assertEqual(panel.count('<SessionUserModal'), 1)
        self.assertEqual(panel.count('{detailScreen}'), 2)

    def test_phone_layout_is_a_lazy_chunk(self):
        """Раздел админский: в общий бандл телефонный список не нужен никому."""
        self.assertIn("lazyWithRetry(() => import('./components/sessions/SessionsMobileView'))", APP)


class ListDecisionTests(unittest.TestCase):
    def test_no_revoke_button_in_every_row(self):
        """Прервать все сессии человека — в его карточке. Красная кнопка в
        каждой строке на телефоне задевается при прокрутке."""
        self.assertNotIn('Прервать все', VIEW_CODE)
        self.assertNotIn('onRevokeAll', VIEW_CODE)

    def test_confirmation_and_sort_are_sheets(self):
        self.assertEqual(VIEW.count('<MobileActionSheet'), 2)
        self.assertNotIn('fixed inset-0', VIEW_CODE)
        self.assertNotIn('window.confirm', VIEW_CODE)

    def test_roles_are_one_control(self):
        """На компьютере роли дважды — плитками и вкладками. Здесь один переключатель."""
        self.assertEqual(VIEW.count('role="tablist"'), 1)

    def test_device_chips_hide_when_there_is_nothing_to_choose(self):
        self.assertRegex(VIEW, r"deviceFilter !== 'all'\s*\|\|[^;]*\.length > 1")

    def test_list_loads_more_on_its_own(self):
        """Компонент ленивый: эффект подгрузки в SessionsPanel отрабатывает
        раньше, чем появляется «хвост» списка, и со своей ссылкой он пуст."""
        self.assertIn('new IntersectionObserver', VIEW)
        self.assertIn('ref={sentinelRef}', VIEW)

    def test_last_seen_is_a_pure_helper(self):
        self.assertIn('export function lastSeenLabel', USER_AGENT)
        self.assertIn('lastSeenLabel(person.last_seen_at, now)', VIEW)

    def test_tab_bar_gives_way_while_selecting(self):
        _, body = rule_with(CSS, ':has(.ses-m.is-selecting) .mobile-tabbar')
        self.assertIn('display: none', body)

    def test_row_press_is_a_fill_not_a_dim(self):
        """Общее приглушение нажатия (0.58) гасило бы всю строку списка."""
        _, body = rule_with(CSS, '.ses-m-row:active')
        self.assertIn('opacity: 1 !important', body)
        self.assertIn('var(--sheet-active)', body)


class EmployeeCardTests(unittest.TestCase):
    def test_session_actions_render_once_and_only_move(self):
        self.assertEqual(MODAL.count('{!isNarrow && actions}'), 1)
        self.assertEqual(MODAL.count('{isNarrow && actions}'), 1)
        self.assertLess(MODAL.index('{!isNarrow && actions}'), MODAL.index('{isNarrow && actions}'))

    def test_close_button_is_desktop_only(self):
        footer = MODAL[MODAL.index('footer={'):]
        footer = footer[:footer.index('Закрыть')]
        self.assertIn('{!isNarrow && (', footer)
        self.assertIn('footer={isNarrow && sessionsCount === 0 ? null :', MODAL)

    def test_desktop_markup_is_left_as_it_was(self):
        """Настольные классы — слово в слово прежние: телефонные ветки только
        добавлены рядом."""
        for desktop in (
            'ml-auto flex shrink-0 items-center justify-end gap-1.5',
            'rounded-lg bg-blue-50 px-2.5 py-1.5 text-[12px] font-medium text-blue-600 ring-1 '
            'ring-blue-100 transition hover:bg-blue-600 hover:text-white active:scale-[0.98] disabled:opacity-40',
            'rounded-lg bg-red-50 px-2.5 py-1.5 text-[12px] font-medium text-red-600 ring-1 '
            'ring-red-100 transition hover:bg-red-600 hover:text-white active:scale-[0.98] disabled:opacity-40',
            'grid grid-cols-3 gap-px border-t border-slate-100 bg-slate-100 text-center',
            'rounded-xl bg-red-600 px-4 py-2 text-[13px] font-medium text-white shadow-sm transition '
            'hover:bg-red-500 active:scale-[0.98] disabled:opacity-50',
        ):
            self.assertEqual(MODAL.count(desktop), 1, desktop)
        self.assertIn("`space-y-4${isNarrow ? ' ses-card' : ''}`", MODAL)


if __name__ == '__main__':
    unittest.main()
