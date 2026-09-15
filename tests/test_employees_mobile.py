"""«Учет сотрудников» на телефоне.

Решения найдены на стенде (15.09.2026, iPhone 390×844) и без стража тихо
теряются при следующей правке раздела: сборка проходит, компьютер выглядит как
прежде, а на телефоне снова таблица, которая ездит вбок, шесть меток статусов в
три ряда и плавающий переключатель колонок поверх строк. Подробности — в шапках
src/components/employees/employees-mobile.css и EmployeesMobileView.jsx;
правила списка закреплены tests/employees_phone_list.test.mjs.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMPLOYEES = ROOT / 'src' / 'components' / 'employees'
MODALS = ROOT / 'src' / 'components' / 'modals'
VIEW = (EMPLOYEES / 'EmployeesMobileView.jsx').read_text(encoding='utf-8')
CSS = (EMPLOYEES / 'employees-mobile.css').read_text(encoding='utf-8')
HISTORY = (MODALS / 'HistoryModal.jsx').read_text(encoding='utf-8')
HISTORY_CSS = (MODALS / 'history-mobile.css').read_text(encoding='utf-8')
EDIT = (MODALS / 'UserEditModal.jsx').read_text(encoding='utf-8-sig')
EDIT_CSS = (MODALS / 'user-edit-mobile.css').read_text(encoding='utf-8')
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')

PHONE_PREFIXES = ('body.mobile-shell', 'html[data-otp-theme="dark"] body.mobile-shell')


def without_comments(source):
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


def directory_source():
    start = APP.index('const renderEmployeeDirectorySection = ({')
    return APP[start:APP.index('const changeSvTable = async', start)]


class PhoneLayerTests(unittest.TestCase):
    def test_every_rule_is_locked_to_the_phone_shell(self):
        """На компьютере слои не трогают ничего: раздел там остаётся прежним."""
        for name, css in (('employees', CSS), ('history', HISTORY_CSS), ('user-edit', EDIT_CSS)):
            for selectors, _ in css_rules(css):
                for selector in selectors.split(','):
                    selector = ' '.join(selector.split())
                    self.assertTrue(selector.startswith(PHONE_PREFIXES),
                                    f'{name}: правило не заперто на телефон: {selector}')

    def test_layers_are_imported_by_their_components(self):
        self.assertIn("import './employees-mobile.css';", VIEW)
        self.assertIn("import './history-mobile.css';", HISTORY)
        self.assertIn("import './user-edit-mobile.css';", EDIT)

    def test_list_markup_avoids_what_the_shell_layer_catches(self):
        self.assertEqual(shell_trap_violations(VIEW), [])

    def test_the_trap_guard_catches_a_forgery(self):
        fake = ('<div className="emp-m-toolbar flex">'
                '<button className={`emp-m-chip${on ? \' is-on grid-cols-3\' : \'\'}`} />'
                '<span className={x ? \'inline-flex\' : \'emp-m-ok\'} /></div>')
        self.assertEqual(sorted(shell_trap_violations(fake)),
                         ['emp-m-toolbar', 'flex', 'grid-cols-3', 'inline-flex'])

    def test_title_is_not_a_heading_tag(self):
        """h1–h3 в .main-content общий слой ужимает до 18–22 px, а крупный
        заголовок телефона — 30 px."""
        self.assertIsNone(re.search(r'<h[1-3][\s>]', VIEW))
        self.assertIn('role="heading"', VIEW)

    def test_transparent_select_outweighs_the_motion_layer(self):
        """Общее правило select в mobile-motion.css весит (0,2,2); прозрачному
        полю над строкой отдела нужны вес больше и высота строки, а не 40 px."""
        selectors, body = rule_with(CSS, '.emp-m .emp-m-dept-select')
        self.assertIn('opacity: 0', body)
        self.assertIn('min-height: 0', body)

    def test_card_actions_keep_their_gap(self):
        """`.emp-card-list { margin: 0 }` ниже в файле снимал отступ у группы
        действий при равном весе — они прилипали к последней группе полей."""
        rule_with(CSS, '.emp-card-list.emp-card-acts')

    def test_tab_bar_gives_way_while_selecting(self):
        _, body = rule_with(CSS, ':has(.emp-m.is-selecting) .mobile-tabbar')
        self.assertIn('display: none', body)

    def test_row_press_is_a_fill_not_a_dim(self):
        _, body = rule_with(CSS, '.emp-m-row:active')
        self.assertIn('opacity: 1 !important', body)
        self.assertIn('var(--sheet-active)', body)


class AppBranchTests(unittest.TestCase):
    def test_phone_layout_is_a_lazy_chunk(self):
        self.assertIn("lazyWithRetry(() => import('./components/employees/EmployeesMobileView'))", APP)
        self.assertEqual(APP.count('<EmployeesMobileView'), 1)

    def test_directory_lists_branch_before_the_table(self):
        """Супервайзеры, тренеры, админы: ветка телефона до колонок таблицы."""
        source = directory_source()
        self.assertLess(source.index('if (isMobileShell) {'),
                        source.index("const columns = buildEmployeeSectionColumns(role === 'operator' ? 'operator' : 'staff');"))
        self.assertLess(source.index('return renderEmployeesPhone({'), source.index('<table'))

    def test_employees_section_desktop_block_is_closed_on_a_phone(self):
        anchor = "{(view === 'manage_users' || view === 'employees') && ("
        phone = "{(view === 'manage_users' || view === 'employees') && isMobileShell && renderEmployeesPhone({"
        self.assertLess(APP.index(phone), APP.index(anchor))
        self.assertTrue(APP.split(anchor, 1)[1].lstrip().startswith('!isMobileShell && ('))

    def test_operators_desktop_block_is_closed_on_a_phone(self):
        phone = "{view === 'manage_operators' && isMobileShell && renderEmployeesPhone({"
        desktop = "{view === 'manage_operators' && !isMobileShell && ("
        self.assertLess(APP.index(phone), APP.index(desktop))
        self.assertNotIn("{view === 'manage_operators' && (\n", APP)

    def test_card_reads_all_four_column_sets(self):
        """Переключатель колонок на телефоне не рисуется — все четыре набора
        стоят в карточке разом, и берутся той же функцией, что у таблицы."""
        self.assertIn('cardSectionList={EMPLOYEE_TABLE_SECTIONS}', APP)
        self.assertIn(
            "            const buildEmployeeSectionColumns = (variant = 'operator', deptFields = null) => {\n"
            "                return buildEmployeeColumnsOfSection(employeeTableSection, variant, deptFields);\n"
            "            };",
            APP,
        )
        self.assertEqual(APP.count('renderEmployeeTableSectionSwitcher()'), 3)

    def test_creation_and_editing_have_one_entry_for_both_layouts(self):
        self.assertIn('onClick={openCreateManageUsersEmployee}', APP)
        self.assertIn('onClick={openCreateManagedOperator}', APP)
        self.assertEqual(APP.count('onClick={() => openManagedOperatorEditor(op)}'), 2)


class ConfirmationTests(unittest.TestCase):
    def test_phone_asks_with_a_sheet(self):
        self.assertEqual(VIEW.count('<MobileActionSheet'), 2)
        self.assertNotIn('window.confirm', VIEW_CODE)
        self.assertNotIn('fixed inset-0', VIEW_CODE)
        for call in ('removeSv(employee.id, { skipConfirm: true })',
                     'dismissAdminUser(employee, { skipConfirm: true })',
                     'promoteUserToSupervisor(employee, { skipConfirm: true })'):
            self.assertEqual(APP.count(call), 1, call)

    def test_desktop_keeps_its_confirm(self):
        self.assertEqual(APP.count('options.skipConfirm || window.confirm(`'), 2)
        self.assertIn("if (!options.skipConfirm && !confirm('Are you sure you want to remove this supervisor?')) return;", APP)


class WindowTests(unittest.TestCase):
    def test_edit_form_on_a_phone_has_a_back_arrow_and_one_button(self):
        self.assertIn('className="otp-modal-back uem-back"', EDIT)
        footer = EDIT[EDIT.index('{!createdCredentials && isMobileShell && ('):]
        footer = footer[:footer.index('{!createdCredentials && !isMobileShell && (')]
        self.assertIn('className="uem-save"', footer)
        self.assertNotIn('Отмена', footer)
        self.assertIn('{!createdCredentials && !isMobileShell && <p className="mt-2 text-xs text-gray-400">Нажмите Esc', EDIT)

    def test_edit_form_tabs_are_one_strip(self):
        """Общий слой делает из grid-cols-5 две колонки — вкладки вставали 2×3."""
        _, body = rule_with(EDIT_CSS, '.grid.grid-cols-5 {'.rstrip(' {'))
        self.assertIn('display: flex', body)

    def test_history_does_not_raise_the_keyboard_on_a_phone(self):
        self.assertIn('if (isOpen && !isMobileShell) {', HISTORY)
        branch = HISTORY[HISTORY.index('if (isMobileShell) {'):]
        self.assertIn('className="otp-modal-back hist-m-back"', branch)
        self.assertNotIn('<table', branch[:branch.index('return (\n    <>')])

    def test_report_window_hides_cancel_on_a_phone(self):
        self.assertIn("${isMobileShell ? ' urep-m' : ''}", APP)
        self.assertIn('className="otp-modal-back urep-m-back"', APP)
        cancel = APP.index('onClick={() => setShowUsersReportModal(false)}\n'
                           '                                            disabled={isLoading}\n'
                           '                                            className="inline-flex')
        self.assertIn('{!isMobileShell && (', APP[cancel - 200:cancel])


if __name__ == '__main__':
    unittest.main()
