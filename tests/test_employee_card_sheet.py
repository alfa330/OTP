"""Карточка сотрудника на компьютере — «Учет сотрудников».

Решение владельца 30.09.2026: меню «три точки» в конце строки убрать, нажатие
на строку открывает карточку для чтения, а «Править», «В супервайзеры» и
«История» — кнопки самой карточки; стиль iOS/macOS, плавные переходы и никакой
второй модалки поверх первой. Подробности — в шапке
src/components/employees/EmployeeCardSheet.jsx.

Всё это держится на тексте, который легко вернуть одной правкой: меню «⋮» в
строке, окно правки поверх карточки, window.confirm перед повышением. Сборка
при этом проходит, и разница видна только в браузере.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
EMPLOYEES = ROOT / 'src' / 'components' / 'employees'
SHEET = (EMPLOYEES / 'EmployeeCardSheet.jsx').read_text(encoding='utf-8')
SHEET_CSS = (EMPLOYEES / 'employee-card-sheet.css').read_text(encoding='utf-8')
EDIT = (ROOT / 'src' / 'components' / 'modals' / 'UserEditModal.jsx').read_text(encoding='utf-8-sig')


def without_comments(source):
    source = re.sub(r'\{/\*.*?\*/\}', '', source, flags=re.S)
    return re.sub(r'/\*.*?\*/', '', source, flags=re.S)


SHEET_CODE = without_comments(SHEET)


def directory_source():
    start = APP.index('const renderEmployeeDirectorySection = ({')
    return APP[start:APP.index('const changeSvTable = async', start)]


def employees_desktop_source():
    start = APP.index("{(view === 'manage_users' || view === 'employees') && (\n")
    return APP[start:APP.index("{view === 'manage_admins' && (isSuperAdmin || isEmployeeAccountingManager) && renderEmployeeDirectorySection({", start)]


class RowsOpenTheCardTests(unittest.TestCase):
    def test_no_three_dots_in_the_four_lists(self):
        """Супервайзеры, тренеры, админы — общий renderEmployeeDirectorySection;
        сотрудники — своя таблица. Ни в одной нет меню в конце строки."""
        for name, source in (('directory', directory_source()), ('employees', employees_desktop_source())):
            self.assertNotIn('fa-ellipsis-v', source, name)
            self.assertNotIn('openMenuId', source, name)
            self.assertNotIn('<th className="px-6 py-3"></th>', source, name)
        self.assertNotIn('const openRowActionMenu', APP)
        self.assertNotIn('rowActionMenuPos', APP)

    def test_row_click_and_keyboard_open_the_card(self):
        directory = directory_source()
        self.assertIn('onClick={(event) => openEmployeeCardFromRow(event, employee.id)}', directory)
        self.assertIn('onKeyDown={(event) => handleEmployeeRowKeyDown(event, employee.id)}', directory)
        employees = employees_desktop_source()
        self.assertIn('onClick={(event) => handleManageUserRowClick(event, u.id)}', employees)
        self.assertIn('onKeyDown={(event) => handleEmployeeRowKeyDown(event, u.id)}', employees)
        for source in (directory, employees):
            self.assertIn('tabIndex={0}', source)
            self.assertIn('aria-haspopup="dialog"', source)
            self.assertEqual(source.count('renderEmployeeCardSheet({'), 1)

    def test_ctrl_click_still_selects_for_bulk_edit(self):
        start = APP.index('const handleManageUserRowClick = useCallback((event, userId) => {')
        handler = APP[start:APP.index('}, [openEmployeeCardFromRow, toggleManageUsersSelection]);', start)]
        self.assertLess(handler.index('if (event?.ctrlKey || event?.metaKey) {'),
                        handler.index('openEmployeeCardFromRow(event, userId);'))
        self.assertIn('toggleManageUsersSelection(userId);\n                    return;', handler)

    def test_links_and_selected_text_do_not_open_the_card(self):
        start = APP.index('const openEmployeeCardFromRow = useCallback((event, userId) => {')
        body = APP[start:APP.index('}, []);', start)]
        self.assertIn("closest?.('a, button, input, select, textarea, label')", body)
        self.assertIn('window.getSelection', body)

    def test_footer_spans_one_column_less(self):
        """Колонки меню больше нет — итог строки не должен вылезать за шапку."""
        self.assertIn('colSpan={Math.max(1, columns.length - 1)}', directory_source())
        self.assertIn('colSpan={Math.max(1, manageUsersSectionColumns.length - 1)}', employees_desktop_source())

    def test_card_closes_with_the_section(self):
        self.assertIn('useEffect(() => {\n                setEmployeeCardId(null);\n            }, [view, isMobileShell]);', APP)


class OneWindowTests(unittest.TestCase):
    def test_actions_are_one_list_for_both_layouts(self):
        """Телефон и карточка читают один список действий: разойтись в правах
        (кому повышать, кого увольнять) им негде."""
        self.assertEqual(APP.count('promoteUserToSupervisor(employee, { skipConfirm: true })'), 1)
        self.assertEqual(APP.count('removeSv(employee.id, { skipConfirm: true })'), 1)
        self.assertEqual(APP.count('dismissAdminUser(employee, { skipConfirm: true })'), 1)
        self.assertEqual(APP.count('actionsFor: manageUsersActionsFor,'), 2)
        directory = directory_source()
        self.assertIn('                        actionsFor,\n                    });', directory)
        self.assertIn('                            actionsFor,\n                        })}', directory)

    def test_edit_history_and_demote_are_screens_inside_the_card(self):
        for key in ("page: 'edit'", "page: 'history'", "page: 'demote'"):
            self.assertEqual(APP.count(key), 1, key)
        pages = APP[APP.index('const employeeCardPages = {'):APP.index('const employeeCardDeptFields')]
        self.assertIn('<UserEditModal\n                            embedded', pages)
        self.assertIn('onClose={back}', pages)
        self.assertIn('renderDemotionForm()', pages)
        self.assertEqual(pages.count('guard: true'), 2)

    def test_demotion_window_does_not_open_over_the_card(self):
        self.assertIn('open={!!demotionTarget && employeeCardId == null}', APP)
        self.assertEqual(APP.count('{renderDemotionForm()}'), 2)

    def test_card_itself_opens_no_second_window(self):
        self.assertEqual(SHEET_CODE.count('role="dialog"'), 1)
        self.assertEqual(SHEET_CODE.count('createPortal('), 1)
        for banned in ('window.confirm', 'IosModal', 'MobileActionSheet', 'HistoryModal', 'otp-modal-root fixed'):
            self.assertNotIn(banned, SHEET_CODE)

    def test_questions_unfold_inside_the_card(self):
        self.assertIn('<Reveal open={isOpen}>', SHEET)
        self.assertIn("if (action.page) {\n            pushPage(action.page);", SHEET)
        self.assertIn('setConfirmKey((current) => (current === action.key ? null : action.key));', SHEET)

    def test_card_is_desktop_only(self):
        """На телефоне у раздела своя карточка экраном; метка otp-modal-root
        потребовала бы жеста «назад» (test_mobile_shell)."""
        self.assertNotIn('otp-modal-root', SHEET)
        self.assertIn("lazyWithRetry(() => import('./components/employees/EmployeeCardSheet'))", APP)
        self.assertEqual(APP.count('<EmployeeCardSheet'), 1)


class EmbeddedEditFormTests(unittest.TestCase):
    def test_embedded_form_has_no_backdrop_frame_or_header(self):
        self.assertIn('onOpenSipSettings = null, embedded = false }) => {', EDIT)
        self.assertIn('{!embedded && (\n        <div\n            className="otp-modal-dim', EDIT)
        self.assertIn("className={embedded ? 'uem-embedded' : 'otp-modal-root fixed inset-0", EDIT)
        self.assertIn("role={embedded ? undefined : 'dialog'}", EDIT)
        self.assertIn('{embedded ? null : isMobileShell ? (', EDIT)
        self.assertIn('if (!embedded && e.key === "Escape") {', EDIT)

    def test_escape_leaves_the_photo_crop_to_itself(self):
        """Кадр фото — поверх формы; Escape карточки его не перешагивает."""
        self.assertIn('data-uem-crop=""', EDIT)
        self.assertIn('[data-uem-crop]', SHEET)

    def test_embedded_focus_does_not_scroll_the_sliding_screen(self):
        self.assertIn('nameRef.current?.focus(embedded ? { preventScroll: true } : undefined);', EDIT)


class MotionAndThemeTests(unittest.TestCase):
    def test_dark_layer_repaints_the_tokens(self):
        self.assertIn('html[data-otp-theme="dark"] .ecs {', SHEET_CSS)
        light = SHEET_CSS[SHEET_CSS.index('.ecs {'):SHEET_CSS.index('html[data-otp-theme="dark"] .ecs {')]
        dark = SHEET_CSS[SHEET_CSS.index('html[data-otp-theme="dark"] .ecs {'):]
        dark = dark[:dark.index('}')]
        for token in re.findall(r'(--ecs-[\w-]+):', light):
            if token in ('--ecs-ease', '--ecs-push'):
                continue
            self.assertIn(token + ':', dark, token)

    def test_panel_keeps_no_transform_after_entering(self):
        """Иначе окно стало бы контейнером для fixed-потомков: кадр фото в
        форме правки съёжился бы до размеров окна."""
        self.assertRegex(SHEET_CSS, r'animation: ecs-panel-in [^;]*backwards;')
        self.assertIn('.ecs-page.is-sub.is-in {\n    transform: none;', SHEET_CSS)

    def test_durations_match_the_component(self):
        self.assertIn('const PAGE_MS = 420;', SHEET)
        self.assertIn('--ecs-push: 420ms;', SHEET_CSS)
        self.assertIn('const SHEET_LEAVE_MS = 200;', SHEET)
        self.assertRegex(SHEET_CSS, r'\.ecs\.is-leaving \.ecs-panel \{\n    animation: ecs-panel-out 200ms')

    def test_reduced_motion_is_honoured(self):
        self.assertIn('@media (prefers-reduced-motion: reduce)', SHEET_CSS)

    def test_scrollbar_styling_stays_webkit_only(self):
        """scrollbar-width/color выключили бы в Chrome всю ::-webkit-scrollbar
        стилизацию (tests/test_thin_scroll.py)."""
        self.assertNotRegex(without_comments(SHEET_CSS), r'scrollbar-(width|color)')


if __name__ == '__main__':
    unittest.main()
