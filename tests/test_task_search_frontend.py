# -*- coding: utf-8 -*-
"""Поиск по доске в «Задачах» — проводка фронта (владелец 27.11.2026).

«Поиск будет искать по выбранной доске и проекту»: поле стоит в панели доски,
охват запроса — тот же, что у колонок и выгрузки; выдача — карточками «Все
задачи» с подсветкой. Решения ниже проверены на стенде (компьютер 1680×956 и
1024×768, телефон 390×844, тёмная тема) — без стража они тихо уходят при
следующей правке раздела. Поведение чистой части — tests/task_search.test.mjs.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'src' / 'components' / 'tasks'
VIEW = (TASKS / 'TasksView.jsx').read_text(encoding='utf-8')
BOARD = (TASKS / 'TaskBoardWorkspace.jsx').read_text(encoding='utf-8')
HOOK = (TASKS / 'useTaskBoardSearch.js').read_text(encoding='utf-8')
PURE = (TASKS / 'taskSearch.js').read_text(encoding='utf-8')
COLUMNS = (TASKS / 'boardColumns.js').read_text(encoding='utf-8')
MOBILE = (TASKS / 'tasks-mobile.css').read_text(encoding='utf-8')
WORKSPACE = BOARD[BOARD.index('const TaskBoardWorkspace = ('):]
TOOLBAR = WORKSPACE[WORKSPACE.index('<div className={`tb-toolbar'):WORKSPACE.index('{focusPerson && focusStats && (')]
ROW = VIEW[VIEW.index('const TaskRow = React.memo('):VIEW.index('/* ─── TaskDrawer')]
TASK_LIST = VIEW[VIEW.index('const renderTaskList = '):VIEW.index('const renderGroupedTasks = ')]


class BoardScopeTests(unittest.TestCase):
    def test_search_asks_the_same_scope_as_the_board_and_export(self):
        """Доска («Моя / На мне / Все / сотрудник»), проект и раздел — одним
        scopeQueryParams, как у колонок и Excel."""
        self.assertIn('() => scopeQueryParams(scope, projectId, sectionId),', WORKSPACE)
        self.assertIn('scopeKey: `${scope}|${projectId}|${sectionId}`,', WORKSPACE)
        self.assertIn('scopeParams: searchScopeParams,', WORKSPACE)
        self.assertIn('...(scopeParams || {}),', PURE)

    def test_server_does_the_matching(self):
        """Сопоставляет сервер: он видит всю доску, а не загруженную порцию."""
        start = VIEW.index('const searchBoardTasks = useCallback(')
        block = VIEW[start:start + 600]
        self.assertIn('axios.get(`${apiBaseUrl}/api/tasks`, { headers: buildHeaders(), params, signal })', block)
        self.assertIn("layout: data?.search?.layout || ''", block)
        self.assertIn('searchTasks={searchBoardTasks}', VIEW)

    def test_widen_lifts_board_project_and_section(self):
        self.assertIn('onWidenSearch={() => changeProjectFilter(TASK_PROJECT_ALL)}', VIEW)
        widen = WORKSPACE[WORKSPACE.index('const widenSearch = useCallback('):]
        self.assertIn("if (scope !== 'all') changeScope('all');", widen[:300])
        self.assertIn('onWidenSearch();', widen[:300])
        self.assertEqual(BOARD.count('Искать везде'), 3)  # пустая выдача, частичная и комментарий


class ResultsTests(unittest.TestCase):
    def test_results_replace_the_board_views(self):
        for view in ("{!isSearching && mode === 'backlog' && (",
                     "{!isSearching && mode === 'board' && (",
                     "{!isSearching && mode === 'timeline' && (",
                     '{!isBoardMode && !isSearching && ('):
            self.assertIn(view, WORKSPACE)
        self.assertIn('<BoardSearchResults', WORKSPACE)
        # Загрузка доски не прячет поле, пока идёт поиск.
        self.assertIn('if (!isSearching && (isBoardMode ?', WORKSPACE)

    def test_rows_are_the_cards_of_all_tasks(self):
        """Выдача — те же карточки, что «Все задачи»: TaskRow раздела."""
        self.assertIn("renderSearchList={(list, options) => renderTaskList(list, '', '', false, { ...options, board: true })}",
                      VIEW)
        self.assertIn('const rows = isSearch ? withSearchSeparators(list) : withDateSeparators(list);', TASK_LIST)
        self.assertIn("boardState={options.board ? boardSearchStateOf(row.task) : null}", TASK_LIST)

    def test_every_board_column_has_a_label(self):
        ids = re.findall(r"\{ id: '(\w+)',", COLUMNS[COLUMNS.index('export const BOARD_COLUMNS'):])
        state = VIEW[VIEW.index('const BOARD_SEARCH_STATE = {'):VIEW.index('const boardSearchStateOf')]
        self.assertEqual(set(re.findall(r"^  (\w+): \{ label:", state, re.M)), set(ids))

    def test_highlight_and_why_line(self):
        self.assertIn('<SearchMarked text={task.subject} ranges={match?.subject} />', ROW)
        self.assertEqual(ROW.count('<SearchMatchLine match={match} />'), 2)  # компьютер и телефон
        self.assertIn("match?.key ? ' is-search-match' : ''", ROW)
        self.assertIn('data-search-active={isActive', ROW)


class StableToolbarTests(unittest.TestCase):
    """Поле не прыгает под курсором: на 1024 px исчезнувшая подсказка уводила
    его в другую строку панели (замер стенда 27.11)."""

    def test_field_sits_before_export(self):
        side = TOOLBAR[TOOLBAR.index('tb-toolbar-side'):]
        self.assertLess(side.index('<BoardSearchField'), side.index("typeof onExport === 'function'"))

    def test_nothing_leaves_the_toolbar_while_searching(self):
        self.assertIn("tb-drag-hint text-[11.5px] text-slate-400${isSearching ? ' invisible' : ''}", TOOLBAR)
        self.assertNotIn("mode === 'board' && !isSearching", TOOLBAR)
        self.assertEqual(TOOLBAR.count('disabled={isSearching}'), 2)  # порядок и порция

    def test_phone_layout(self):
        self.assertIn('body.mobile-shell .tv-root .tb-search {\n    grid-column: 1 / -1;\n    grid-row: 2;', MOBILE)
        # Меньше 16 px — Safari на iPhone увеличивает страницу при фокусе.
        self.assertIn('body.mobile-shell .tv-root .tb-search input {\n    font-size: 16px;', MOBILE)
        self.assertIn('body.mobile-shell .tv-root .tb-toolbar.is-searching .tb-sort-select {\n    display: none;', MOBILE)


class KeyboardTests(unittest.TestCase):
    def test_shortcut_works_in_russian_layout(self):
        """Ctrl+K на русской раскладке — это «л»: ловим по коду клавиши."""
        self.assertIn("event.code !== 'KeyK'", WORKSPACE)

    def test_arrows_enter_escape(self):
        keys = WORKSPACE[WORKSPACE.index('const handleSearchKeyDown = useCallback('):]
        keys = keys[:keys.index('}, [isTaskOpen, searchResults.length')]
        self.assertIn("event.key === 'ArrowDown' || event.key === 'ArrowUp'", keys)
        self.assertIn('onOpenTask(activeSearchTask);', keys)
        self.assertIn('event.nativeEvent?.isComposing', keys)
        self.assertIn('search.clear();', keys)
        self.assertIn('event.currentTarget.blur();', keys)

    def test_open_task_owns_the_keys(self):
        """Esc открытой карточки закрывает её (обработчик на window), а не стирает
        поиск под ней: при открытой карточке поле клавиши не трогает, а открытие
        из поиска уводит фокус из поля."""
        keys = WORKSPACE[WORKSPACE.index('const handleSearchKeyDown = useCallback('):]
        keys = keys[:keys.index('}, [isTaskOpen, searchResults.length')]
        self.assertLess(keys.index('if (isTaskOpen) return;'), keys.index("event.key === 'ArrowDown'"))
        enter = keys[keys.index("if (event.key === 'Enter')"):]
        self.assertLess(enter.index('event.currentTarget.blur();'), enter.index('onOpenTask(activeSearchTask);'))


class ShortcutAndEnterTests(unittest.TestCase):
    """Разбор 27.11.2026: ⌘K при открытом окне «Новая задача» уводил фокус в
    поле под окном; Enter во время паузы набора открывал находку прежней строки."""

    def test_shortcut_respects_open_windows_and_other_fields(self):
        shortcut = WORKSPACE[WORKSPACE.index('// Ctrl+K / ⌘K — к полю поиска.'):]
        shortcut = shortcut[:shortcut.index('}, [canSearch, isTaskOpen]);')]
        self.assertIn("if (document.querySelector(OPEN_WINDOW_SELECTOR)) return;", shortcut)
        self.assertIn("export const OPEN_WINDOW_SELECTOR = '.otp-modal-root';", PURE)
        self.assertIn("target?.closest?.('input, textarea, select, [contenteditable=\"\"], [contenteditable=\"true\"]')",
                      shortcut)
        self.assertLess(shortcut.index('OPEN_WINDOW_SELECTOR'), shortcut.index('event.preventDefault();'))

    def test_enter_before_the_answer_opens_the_new_first_result(self):
        keys = WORKSPACE[WORKSPACE.index('const handleSearchKeyDown = useCallback('):]
        enter = keys[keys.index("if (event.key === 'Enter')"):keys.index("if (event.key === 'Escape')")]
        self.assertLess(enter.index('if (search.isPending) {'), enter.index('onOpenTask(activeSearchTask);'))
        self.assertIn('openOnArrivalRef.current = true;', enter)
        arrival = WORKSPACE[WORKSPACE.index('// Enter, нажатый до ответа'):]
        arrival = arrival[:arrival.index('}, [search.isPending, searchResults, isTaskOpen, onOpenTask]);')]
        self.assertIn('if (!openOnArrivalRef.current || search.isPending) return;', arrival)
        self.assertIn('onOpenTask(first);', arrival)
        # Дописали строку — прежнее «открыть, когда придёт» снимается.
        change = WORKSPACE[WORKSPACE.index('const changeSearchText = useCallback('):]
        self.assertIn('openOnArrivalRef.current = false;', change[:300])
        self.assertIn('onChange={changeSearchText}', WORKSPACE)

    def test_error_offers_retry(self):
        self.assertIn('<button type="button" className="tb-search-retry" onClick={onRetry}>Повторить</button>', BOARD)
        self.assertIn('onRetry={search.retry}', WORKSPACE)


class RequestHygieneTests(unittest.TestCase):
    """Урок «Сессий»: задержка не сбрасывается от собственных запросов, а
    устаревший ответ не оседает в выдаче."""

    def test_debounce_depends_only_on_the_text(self):
        debounce = HOOK[HOOK.index('// Задержка набора'):HOOK.index('const request = useCallback(')]
        self.assertIn('setTimeout(() => setQuery(trimmed), SEARCH_DEBOUNCE_MS)', debounce)
        self.assertIn('}, [text]);', debounce)

    def test_old_request_is_cancelled_and_ignored(self):
        self.assertIn('controllerRef.current?.abort();', HOOK)
        self.assertIn('return requestRef.current === requestId ? (result || { tasks: [], total: 0 }) : null;', HOOK)
        self.assertIn("error?.code === 'ERR_CANCELED'", HOOK)


if __name__ == '__main__':
    unittest.main()
