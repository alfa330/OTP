"""Страница сотрудника на компьютере — «Учет сотрудников».

Решения владельца 30.09.2026, в три захода: (1) «три точки» в конце строки
убрать, нажатие на строку — карточка для чтения, «Править», «В супервайзеры»,
«История» — её кнопки, стиль iOS/macOS, без второй модалки; (2) «модалка
слишком маленькая — без модалки, переход как на страницу и кнопка назад, чтобы
там была вся информация»; (3) из пяти макетов — «дашборд», «Связаться» слева,
«Изменить» и история — в том же стиле, а не отдельными экранами; (4) 01.10:
история одна — вкладкой с пагинацией, правка — без прокрутки, разделы
переключателем. Подробности — в шапке src/components/employees/EmployeeCardPage.jsx.

Всё это держится на тексте, который легко вернуть одной правкой: меню «⋮» в
строке, окно поверх списка, window.confirm перед повышением. Сборка при этом
проходит, и разница видна только в браузере.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
EMPLOYEES = ROOT / 'src' / 'components' / 'employees'
PAGE = (EMPLOYEES / 'EmployeeCardPage.jsx').read_text(encoding='utf-8')
PAGE_CSS = (EMPLOYEES / 'employee-card-page.css').read_text(encoding='utf-8')
EDIT = (ROOT / 'src' / 'components' / 'modals' / 'UserEditModal.jsx').read_text(encoding='utf-8-sig')


def without_comments(source):
    source = re.sub(r'\{/\*.*?\*/\}', '', source, flags=re.S)
    return re.sub(r'/\*.*?\*/', '', source, flags=re.S)


PAGE_CODE = without_comments(PAGE)


def css_block(selector):
    text = without_comments(PAGE_CSS)
    start = text.index(selector + ' {')
    return text[start:text.index('}', start)]


def directory_source():
    start = APP.index('const renderEmployeeDirectorySection = ({')
    return APP[start:APP.index('const changeSvTable = async', start)]


def employees_desktop_source():
    start = APP.index("{(view === 'manage_users' || view === 'employees') && (\n")
    return APP[start:APP.index("{view === 'manage_admins' && (isSuperAdmin || isEmployeeAccountingManager) && renderEmployeeDirectorySection({", start)]


class RowsOpenThePageTests(unittest.TestCase):
    def test_no_three_dots_in_the_four_lists(self):
        """Супервайзеры, тренеры, админы — общий renderEmployeeDirectorySection;
        сотрудники — своя таблица. Ни в одной нет меню в конце строки."""
        for name, source in (('directory', directory_source()), ('employees', employees_desktop_source())):
            self.assertNotIn('fa-ellipsis-v', source, name)
            self.assertNotIn('openMenuId', source, name)
            self.assertNotIn('<th className="px-6 py-3"></th>', source, name)
        self.assertNotIn('const openRowActionMenu', APP)
        self.assertNotIn('rowActionMenuPos', APP)

    def test_row_click_and_keyboard_open_the_page(self):
        directory = directory_source()
        self.assertIn('onClick={(event) => openEmployeeCardFromRow(event, employee.id)}', directory)
        self.assertIn('onKeyDown={(event) => handleEmployeeRowKeyDown(event, employee.id)}', directory)
        employees = employees_desktop_source()
        self.assertIn('onClick={(event) => handleManageUserRowClick(event, u.id)}', employees)
        self.assertIn('onKeyDown={(event) => handleEmployeeRowKeyDown(event, u.id)}', employees)
        for source in (directory, employees):
            self.assertIn('tabIndex={0}', source)
            self.assertEqual(source.count('renderEmployeeCardPage({'), 1)

    def test_page_takes_the_place_of_the_list(self):
        """Не окно поверх списка, а страница вместо него."""
        directory = directory_source()
        self.assertLess(directory.index('if (employeeCardId != null) {\n                    return renderEmployeeCardPage({'),
                        directory.index('<table'))
        self.assertIn("backLabel: title,", directory)
        employees = employees_desktop_source()
        self.assertTrue(employees.split('\n', 1)[1].lstrip().startswith(
            '!isMobileShell && (employeeCardId != null ? renderEmployeeCardPage({'))
        self.assertIn("backLabel: 'Сотрудники',", employees)

    def test_ctrl_click_still_selects_for_bulk_edit(self):
        start = APP.index('const handleManageUserRowClick = useCallback((event, userId) => {')
        handler = APP[start:APP.index('}, [openEmployeeCardFromRow, toggleManageUsersSelection]);', start)]
        self.assertLess(handler.index('if (event?.ctrlKey || event?.metaKey) {'),
                        handler.index('openEmployeeCardFromRow(event, userId);'))
        self.assertIn('toggleManageUsersSelection(userId);\n                    return;', handler)

    def test_links_and_selected_text_do_not_open_the_page(self):
        start = APP.index('const openEmployeeCardFromRow = useCallback((event, userId) => {')
        body = APP[start:APP.index('}, [openEmployeeCard]);', start)]
        self.assertIn("closest?.('a, button, input, select, textarea, label')", body)
        self.assertIn('window.getSelection', body)

    def test_footer_spans_one_column_less(self):
        """Колонки меню больше нет — итог строки не должен вылезать за шапку."""
        self.assertIn('colSpan={Math.max(1, columns.length - 1)}', directory_source())
        self.assertIn('colSpan={Math.max(1, manageUsersSectionColumns.length - 1)}', employees_desktop_source())

    def test_back_returns_to_the_same_place_in_the_list(self):
        """Список и страница делят одно прокручиваемое поле (main-content):
        назад — туда, где человек был, с фокусом на его строке."""
        self.assertIn('employeeListScrollRef.current = mainContentRef.current?.scrollTop || 0;', APP)
        self.assertIn('root.scrollTop = employeeListScrollRef.current;', APP)
        self.assertIn('root.querySelector(`tr[data-employee-id="${employeeListFocusIdRef.current}"]`)', APP)
        self.assertIn('data-employee-id={employee.id}', directory_source())
        self.assertIn('data-employee-id={u.id}', employees_desktop_source())
        self.assertIn('getScrollRoot={() => mainContentRef.current}', APP)
        self.assertEqual(APP.count("${employeeListReturn ? ' ecp-in-left' : ''}"), 2)

    def test_page_closes_with_the_section(self):
        self.assertIn(
            'useEffect(() => {\n                setEmployeeCardId(null);\n'
            '                setEmployeeListReturn(false);\n', APP)
        self.assertIn('}, [view, isMobileShell]);', APP)


class NoWindowTests(unittest.TestCase):
    def test_page_opens_no_window(self):
        for banned in ('role="dialog"', 'createPortal', 'window.confirm', 'IosModal',
                       'MobileActionSheet', 'HistoryModal', 'otp-modal-root'):
            self.assertNotIn(banned, PAGE_CODE, banned)
        # aria-modal — только в селекторе «поверх открыто чужое окно», не атрибутом.
        self.assertIsNone(re.search(r'\saria-modal=', PAGE_CODE))
        self.assertNotIn('position: fixed', without_comments(PAGE_CSS))

    def test_actions_are_one_list_for_both_layouts(self):
        """Телефон и страница читают один список действий: разойтись в правах
        (кому повышать, кого увольнять) им негде."""
        self.assertEqual(APP.count('promoteUserToSupervisor(employee, { skipConfirm: true })'), 1)
        self.assertEqual(APP.count('removeSv(employee.id, { skipConfirm: true })'), 1)
        self.assertEqual(APP.count('dismissAdminUser(employee, { skipConfirm: true })'), 1)
        self.assertEqual(APP.count('actionsFor: manageUsersActionsFor,'), 2)

    def test_edit_and_history_stay_on_the_page_demote_goes_deeper(self):
        """Владелец: «Изменить» и история — в том же стиле, а не отдельным.
        Правка — режим самой страницы, история — вкладка большой карточки;
        страницей следующего уровня остаётся только «В операторы»."""
        for key in ("page: 'edit'", "page: 'history'", "page: 'demote'"):
            self.assertEqual(APP.count(key), 1, key)
        pages = APP[APP.index('const employeeCardPages = {'):APP.index('const employeeCardDeptFields')]
        self.assertIn('<UserEditModal\n                            embedded', pages)
        self.assertIn('onClose={back}', pages)
        self.assertIn('actionsPortalNode={actionsNode}', pages)
        self.assertIn('renderDemotionForm()', pages)
        self.assertEqual(pages.count('guard: true'), 1)
        self.assertIn("if (action.page === 'edit') {\n            enterEdit();", PAGE)
        self.assertIn("if (action.page === 'history') {\n            showTab('history');", PAGE)
        self.assertIn("{editing ? renderEdit() : renderDashboard()}", PAGE)
        self.assertIn("<span className=\"ecp-back-label\">{pageKey ? person?.name : backLabel}</span>", PAGE)

    def test_demotion_window_does_not_open_over_the_page(self):
        self.assertIn('open={!!demotionTarget && employeeCardId == null}', APP)
        self.assertEqual(APP.count('{renderDemotionForm()}'), 2)

    def test_questions_unfold_on_the_page(self):
        self.assertIn('<Reveal open={isOpen}>', PAGE)
        self.assertIn("if (action.page) {\n            pushPage(action.page);", PAGE)
        self.assertIn('setConfirmKey((current) => (current === action.key ? null : action.key));', PAGE)

    def test_escape_never_drops_a_form(self):
        self.assertIn("if (mode === 'edit') return;", PAGE)
        self.assertIn('if (pageKey && pageDef(pageKey)?.guard) return;', PAGE)
        self.assertIn('if (isTypingTarget(document.activeElement)) return;', PAGE)

    def test_page_is_desktop_only_and_lazy(self):
        self.assertIn("lazyWithRetry(() => import('./components/employees/EmployeeCardPage'))", APP)
        self.assertEqual(APP.count('<EmployeeCardPage'), 1)
        self.assertNotIn('EmployeeCardSheet', APP)
        self.assertFalse((EMPLOYEES / 'EmployeeCardSheet.jsx').exists())


class ReviewFindingsTests(unittest.TestCase):
    """Находки разбора 30.09.2026 — чтобы не вернулись и на странице."""

    def test_each_visit_to_a_page_is_a_fresh_instance(self):
        """«Отмена» в правке и сразу «Изменить» возвращали отменённый черновик."""
        self.assertIn("key={pageKey ? `page:${pageKey}:${pageSeq}` : `${mode}:${editSeq}`}", PAGE)
        self.assertIn('setPageSeq((seq) => seq + 1);', PAGE)
        self.assertEqual(PAGE.count('setEditSeq((seq) => seq + 1);'), 2)

    def test_late_back_does_not_pop_someone_elses_page(self):
        self.assertIn('back: popPageFrom(pageSeq)', PAGE)
        self.assertIn('if (pageSeqRef.current === seq && pageKeyRef.current) popPage();', PAGE)
        self.assertIn('const leftKey = pageKeyRef.current;', PAGE)
        self.assertIn('back: exitEditFrom(editSeq)', PAGE)
        self.assertIn("if (editSeqRef.current === seq && modeRef.current === 'edit') exitEdit();", PAGE)

    def test_edit_page_refreshes_groups_and_departments(self):
        pages = APP[APP.index('const employeeCardPages = {'):APP.index('const employeeCardDeptFields')]
        onopen = pages[pages.index('onOpen: (employee) => {'):pages.index('render:')]
        for call in ('setUserToEdit(employee);', 'fetchUserModalGroups();', 'fetchDepartments();'):
            self.assertIn(call, onopen)
        self.assertNotIn('setShowUserEditModal', onopen)

    def test_actions_that_would_be_refused_are_hidden(self):
        promote = APP[APP.index('const manageUsersActionsFor = (employee) => ['):]
        promote = promote[:promote.index('].filter(Boolean);')]
        self.assertIn("normalizeRole(employee?.role) === 'operator'", promote)
        self.assertIn('canDismissAdmin: isSuperAdmin,', APP)
        self.assertNotIn('canDismissAdmin: true,', APP)
        self.assertIn("!(role === 'admin' && isEmployeeAccountingManager && !isAdminLikeRoleFn(currentUserRole))", directory_source())

    def test_scroll_root_prop_does_not_retrigger_the_scroll_effect(self):
        """getScrollRoot приходит новой стрелкой на каждый рендер App: в
        зависимостях эффекта она сбрасывала прокрутку и уводила фокус с поля
        формы при каждом тосте (разбор fe43e0e2)."""
        self.assertIn('getScrollRootRef.current = getScrollRoot;', PAGE)
        self.assertIn('document.scrollingElement || document.documentElement,\n        [],\n    );', PAGE)

    def test_escape_leaves_windows_and_sidebar_search_alone(self):
        self.assertIn("if (document.querySelector('[aria-modal=\"true\"]')) return;", PAGE)
        self.assertIn('[aria-expanded="true"][aria-haspopup]:not([data-ecp])', PAGE)

    def test_leaving_by_sidebar_cleans_up_the_level_page(self):
        """Иначе окно «Перевести в операторы» всплывало в другом разделе."""
        self.assertIn('if (key) pagesRef.current?.[key]?.onLeave?.();', PAGE)
        self.assertIn("if (modeRef.current === 'edit') pagesRef.current?.edit?.onLeave?.();", PAGE)

    def test_rows_do_not_promise_a_dialog(self):
        self.assertNotIn('aria-haspopup="dialog"', directory_source())
        self.assertNotIn('aria-haspopup="dialog"', employees_desktop_source())

    def test_history_error_toast_exists_in_app_scope(self):
        start = APP.index('const fetchUserHistory = async (userId) => {')
        body = APP[start:APP.index('};', start)]
        self.assertNotIn('addToast(', body)
        self.assertIn("showToast('Не удалось загрузить историю', 'error');", body)


class EmbeddedEditFormTests(unittest.TestCase):
    def test_embedded_form_has_no_backdrop_frame_or_header(self):
        self.assertIn('onOpenSipSettings = null, embedded = false, actionsPortalNode = null }) => {', EDIT)
        self.assertIn('{!embedded && (\n        <div\n            className="otp-modal-dim', EDIT)
        self.assertIn("className={embedded ? 'uem-embedded' : 'otp-modal-root fixed inset-0", EDIT)
        self.assertIn("role={embedded ? undefined : 'dialog'}", EDIT)
        self.assertIn('{embedded ? null : isMobileShell ? (', EDIT)
        self.assertIn('if (!embedded && e.key === "Escape") {', EDIT)

    def test_escape_closes_the_crop_inside_the_page(self):
        start = EDIT.index('if (!embedded || !avatarCropState) return undefined;')
        effect = EDIT[start:start + 400]
        self.assertIn("if (event.key !== 'Escape') return;", effect)
        self.assertIn('closeAvatarCropEditor();', effect)
        self.assertLess(start, EDIT.index('if (!isOpen) return null;'))
        self.assertIn('data-uem-crop=""', EDIT)
        self.assertIn('[data-uem-crop]', PAGE)

    def test_embedded_form_looks_like_the_page(self):
        """Владелец: «Изменить» — в таком же стиле, а не отдельным (30.09.2026),
        и без прокрутки — «на одной странице, но с селекторами» (01.10.2026).
        Встроенная форма — одна карточка: переключатель разделов как «Сведения /
        История» и поля одного раздела сеткой; «Отмена/Сохранить» — в шапке
        страницы; в отдельном окне разметка не меняется."""
        self.assertIn("embedded ? <div className=\"uem-fields\">{head}{children}</div> : children", EDIT)
        self.assertIn('{!createdCredentials && !embedded && (', EDIT)
        self.assertIn('head={!createdCredentials && embedded && (', EDIT)
        self.assertIn('<div className="uem-seg" role="tablist" aria-label="Раздел формы">', EDIT)
        for block, title in (('data', 'Данные'), ('contacts', 'Контакты'), ('corporate', 'Корпоративное'),
                             ('general', 'Общее'), ('account', 'Аккаунт')):
            self.assertIn(f'{{activeTab === "{block}" && (', EDIT, block)
            self.assertIn(f'<UemSection embedded={{embedded}} id="{block}" title="{title}">', EDIT, block)
        self.assertNotIn('embedded || activeTab', EDIT)
        self.assertIn('embedded && actionsPortalNode ? createPortal(node, actionsPortalNode) : node', EDIT)
        self.assertIn('{!createdCredentials && !isMobileShell && (placeDesktopActions(\n                    <div className="uem-actions flex justify-end items-center gap-3 pt-2">', EDIT)
        self.assertIn("embedded ? 'uem-embedded-body' :", EDIT)
        self.assertIn('<div ref={setActionsNode} className="ecp-bar-actions" />', PAGE)
        css = without_comments(PAGE_CSS)
        self.assertIn('grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));',
                      css_block('.ecp .uem-embedded-body .uem-section-body'))
        self.assertIn('display: contents;', css_block('.ecp .uem-embedded-body .uem-section-body > .grid.grid-cols-1'))
        # Плашки — только прямые дети раздела: у select и input те же классы.
        self.assertIn('.uem-section-body > .rounded-lg,', css)
        self.assertNotRegex(css, r'\.uem-section-body \.rounded-lg')
        self.assertNotIn("tone: 'plain'", APP)

    def test_embedded_focus_does_not_scroll_the_sliding_page(self):
        self.assertIn('nameRef.current?.focus(embedded ? { preventScroll: true } : undefined);', EDIT)


class LayoutAndThemeTests(unittest.TestCase):
    def test_bar_sticks_to_the_very_top_of_the_section(self):
        """Липкость считается от края содержимого main-content (за его
        отступом). Отступ разный: p-8 в браузере, ноль в установленном
        приложении — зашитые 32 px там уводили «‹ Назад» за верх окна
        (снимок владельца 30.09.2026). Страница читает отступ сама."""
        bar = css_block('.ecp-bar')
        self.assertIn('position: sticky;', bar)
        self.assertIn('top: calc(-1 * var(--ecp-pad-top, 32px));', bar)
        root = css_block('.ecp')
        self.assertIn('margin: calc(-1 * var(--ecp-pad-top, 32px)) calc(-1 * var(--ecp-pad-right, 32px)) 0 calc(-1 * var(--ecp-pad-left, 32px));', root)
        self.assertIn('min-height: calc(100vh - var(--ecp-pad-bottom, 32px));', root)
        self.assertIn('overflow-x: clip;', root)
        for side in ('Top', 'Right', 'Bottom', 'Left'):
            self.assertIn(f"node.style.setProperty('--ecp-pad-{side.lower()}', style.padding{side});", PAGE)
        self.assertNotRegex(without_comments(PAGE_CSS), r'-32px')

    def test_back_is_a_visible_button(self):
        back = css_block('.ecp-back')
        self.assertIn('background: var(--ecp-card);', back)
        self.assertIn('border-radius: 999px;', back)

    def test_dark_layer_repaints_the_tokens(self):
        light = PAGE_CSS[PAGE_CSS.index('.ecp {'):PAGE_CSS.index('html[data-otp-theme="dark"] .ecp {')]
        dark = PAGE_CSS[PAGE_CSS.index('html[data-otp-theme="dark"] .ecp {'):]
        dark = dark[:dark.index('}')]
        for token in re.findall(r'(--ecp-[\w-]+):', light):
            if token == '--ecp-ease':
                continue
            self.assertIn(token + ':', dark, token)

    def test_screens_keep_no_transform_after_entering(self):
        """Иначе экран стал бы контейнером для fixed-потомков: кадр фото в
        форме правки съёжился бы до его размеров, а плавающий переключатель
        «Общее/Данные…» списка уезжал бы из угла окна."""
        self.assertIn('animation: ecp-in-forward 380ms var(--ecp-ease) backwards;', css_block('.ecp-screen.is-animated'))
        self.assertIn('animation: ecp-fade-in 300ms ease backwards;', css_block('.ecp-in-left'))
        self.assertNotIn('transform', css_block('.ecp-in-left'))

    def test_pages_change_with_a_view_transition(self):
        """Уходящая и входящая страницы видны разом и только в поле раздела."""
        self.assertIn("runPageTransition('forward', () => setEmployeeCardId(Number(userId)));", APP)
        self.assertIn("runPageTransition('back', () => {", APP)
        self.assertIn("setEmployeeListReturn(!usesViewTransitions());", APP)
        self.assertEqual(PAGE.count("runPageTransition('forward', () => {"), 1)
        self.assertEqual(PAGE.count("runPageTransition('back', () => {"), 1)
        self.assertIn("const screenMotion = usesViewTransitions() ? '' : ` is-animated is-${direction}`;", PAGE)
        self.assertIn('html[data-ecp-nav] .main-content {\n    view-transition-name: ecp-field;', PAGE_CSS)
        self.assertIn('::view-transition-group(ecp-field) {\n    overflow: clip;', PAGE_CSS)
        transition = (EMPLOYEES / 'pageTransition.js').read_text(encoding='utf-8')
        self.assertIn('flushSync(update);', transition)
        self.assertIn("window.matchMedia('(prefers-reduced-motion: reduce)')", transition)

    def test_data_is_readable(self):
        """Владелец 30.09.2026: «серый на фоне белого не очень». Значения —
        тёмные, вторичный серый не светлее #6e6e73 (4,9:1 на белом)."""
        for selector in ('.ecp-info-row dd', '.ecp-contact-row dd'):
            value = css_block(selector)
            self.assertIn('color: var(--ecp-text);', value, selector)
            self.assertIn('font-weight: 500;', value, selector)
        self.assertIn('text-align: right;', css_block('.ecp-info-row dd'))
        self.assertIn('--ecp-muted: #6e6e73;', PAGE_CSS)
        self.assertNotIn('#8a8a8e', without_comments(PAGE_CSS))

    def test_dashboard_contact_block_is_on_the_left(self):
        """Владелец: «блок связаться сделать слева, а не справа»."""
        self.assertIn('grid-template-columns: minmax(280px, 1fr) minmax(0, 2fr);', css_block('.ecp-dash'))
        dash = PAGE_CODE[PAGE_CODE.index('<div className="ecp-dash">'):]
        self.assertLess(dash.index('<aside className="ecp-side">'), dash.index('ecp-panel ecp-main'))
        self.assertLess(dash.index('<h2 className="ecp-panel-title">Связаться</h2>'), dash.index('ecp-panel ecp-main'))
        # Контакты — в «Связаться», в «Сведениях» их второй раз нет.
        self.assertIn("sections.filter((section) => section.key !== 'contacts')", PAGE)

    def test_history_is_one_paged_tab(self):
        """Владелец 01.10.2026: «почему два раза история — оставь
        переключатель и сделай пагинацию». Ленты слева нет, вкладка — по
        десять записей."""
        self.assertIn('role="tablist"', PAGE)
        self.assertNotIn('HistoryPreview', PAGE)
        self.assertNotIn('ecp-feed', PAGE + PAGE_CSS)
        self.assertEqual(PAGE.count('<HistoryList'), 1)
        self.assertIn('const HISTORY_PAGE_SIZE = 10;', PAGE)
        self.assertIn('const current = Math.min(page, pageCount - 1);', PAGE)
        self.assertIn('<nav className="ecp-pager" aria-label="Страницы истории">', PAGE)
        self.assertIn('setQuery(event.target.value); setPage(0);', PAGE)
        # Запись — одной строкой (поле, изменение, кто, время): страница из
        # десяти помещается на экран.
        self.assertIn('grid-template-areas: "field change who time";', css_block('.ecp-hist-item'))
        self.assertIn("if (!pageKey && mainTab === 'history') {\n                showTab('info');", PAGE)

    def test_two_balanced_columns_of_fields(self):
        """Каждая группа — в ту колонку, что короче: CSS-колонки шли по
        порядку и оставляли левую полупустой."""
        self.assertIn('grid-template-columns: repeat(2, minmax(0, 1fr));', css_block('.ecp-info'))
        self.assertIn('const target = columns[0].size <= columns[1].size ? columns[0] : columns[1];', PAGE)
        self.assertIn('className="ecp-info-col"', PAGE)

    def test_reduced_motion_is_honoured(self):
        self.assertIn('@media (prefers-reduced-motion: reduce)', PAGE_CSS)

    def test_scrollbar_styling_stays_webkit_only(self):
        """scrollbar-width/color выключили бы в Chrome всю ::-webkit-scrollbar
        стилизацию (tests/test_thin_scroll.py)."""
        self.assertNotRegex(without_comments(PAGE_CSS), r'scrollbar-(width|color)')


if __name__ == '__main__':
    unittest.main()
