"""Раздел «Задачи» на телефоне.

Каждое решение здесь найдено замером на стенде (14.09.2026, iPhone 390×844) и
без стража тихо теряется при следующей правке раздела: сборка проходит,
компьютер выглядит как прежде, а на телефоне снова шапка в две строки,
карточка без жеста «назад» или окно под затемнением. Подробности — в шапке
src/components/tasks/tasks-mobile.css и в комментариях у разметки.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'src' / 'components' / 'tasks'
VIEW = (TASKS / 'TasksView.jsx').read_text(encoding='utf-8')
BOARD = (TASKS / 'TaskBoardWorkspace.jsx').read_text(encoding='utf-8')
CSS = (TASKS / 'tasks-mobile.css').read_text(encoding='utf-8')
SHEET = (ROOT / 'src' / 'components' / 'common' / 'FullscreenSheet.jsx').read_text(encoding='utf-8')


def block(source, start, end):
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


def css_rules(css):
    """Пары (селекторы, тело) без комментариев и @keyframes."""
    text = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    text = re.sub(r'@keyframes\s+[\w-]+\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', '', text)
    return [(sel.strip(), body) for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', text)]


def rule_exact(css, selector):
    """Правило ровно с этим списком селекторов: у одного класса их бывает несколько."""
    for selectors, body in css_rules(css):
        if selectors == selector:
            return body
    raise AssertionError(f'нет правила {selector}')


def rule_with(css, selector_part):
    for selectors, body in css_rules(css):
        if selector_part in selectors:
            return selectors, body
    raise AssertionError(f'нет правила с {selector_part}')


class PhoneLayerTests(unittest.TestCase):
    def test_every_rule_is_locked_to_the_phone_shell(self):
        """На компьютере слой не должен трогать ничего: раздел там свёрстан
        отдельно и остаётся слово в слово прежним."""
        for selectors, _ in css_rules(CSS):
            for selector in selectors.split(','):
                selector = selector.strip()
                self.assertTrue(
                    selector.startswith('body.mobile-shell'),
                    f'правило не заперто на телефон: {selector}',
                )

    def test_layer_is_imported_by_the_section(self):
        self.assertIn("import './tasks-mobile.css';", VIEW)

    def test_colors_come_from_section_tokens(self):
        """Тёмный режим подменяет переменные раздела на .tv-root, а хексы из
        этого файла генератор тёмной темы не разбирает — он читает только тег
        <style> в TasksView.jsx."""
        text = re.sub(r'/\*.*?\*/', '', CSS, flags=re.S)
        self.assertIsNone(re.search(r'#[0-9a-fA-F]{3,8}\b', text))

    def test_section_fills_the_screen(self):
        """Короткая вкладка кончалась посреди экрана, ниже шло полотно
        .main-content другого цвета — шов поперёк экрана."""
        _, body = rule_with(CSS, '.main-content:has(> .tv-root)')
        self.assertIn('flex-direction: column', body)
        _, grow = rule_with(CSS, '.main-content > .tv-root')
        self.assertIn('flex: 1 0 auto', grow)


class HeaderAndListTests(unittest.TestCase):
    def test_header_is_icons_next_to_the_title(self):
        """Четыре кнопки шапки на телефоне шли второй строкой над списком."""
        phone = block(VIEW, '          {isMobileShell ? (\n            /* Телефон: круглые значки', '          ) : (')
        self.assertIn('aria-label="Заметки"', phone)
        self.assertIn('aria-label="Новая задача"', phone)
        self.assertNotIn('refreshTasksData', phone)
        body = rule_exact(CSS, 'body.mobile-shell .main-content .tv-root .tv-topbar')
        self.assertIn('display: grid !important', body)

    def test_row_gives_the_subject_two_lines(self):
        """Бейджи в ряд съедали ширину — от темы оставалось «Обновить и…»."""
        row = block(VIEW, 'const TaskRow = React.memo(', '/* ─── TaskDrawer')
        self.assertIn('compact = false', row)
        self.assertIn('if (compact) {', row)
        self.assertIn('compact={isMobileShell}', VIEW)
        self.assertIn('onPin={isMobileShell ? undefined :', VIEW)
        _, body = rule_with(CSS, '.tv-task-row.is-compact .tv-task-row-subject')
        self.assertIn('-webkit-line-clamp: 2', body)

    def test_filters_are_one_scrolling_line(self):
        _, body = rule_with(CSS, '.tv-filter-chips')
        self.assertIn('flex-wrap: nowrap !important', body)
        self.assertIn('overflow-x: auto', body)

    def test_inbox_is_a_bottom_sheet_with_a_back_step(self):
        """Выпадашка «Ждут вас» уезжала за левый край экрана на 215 px."""
        inbox = block(VIEW, 'const TaskInbox = (', '/* ─── Skeleton loading')
        self.assertIn('useScreenBackGesture(compact && open, close);', inbox)
        # Лента `fixed inset-0` — метка открытого окна: оболочка убирает бар и колокол.
        self.assertIn('className="tv-inbox-dim fixed inset-0"', inbox)
        self.assertIn("tv-inbox-pop ${compact ? 'is-sheet' : ''}", inbox)


class TaskCardScreenTests(unittest.TestCase):
    DRAWER = block(VIEW, 'const TaskDrawer = React.memo(', 'export const PinnedTaskWidget')

    def test_card_is_a_screen(self):
        """Выезжающая панель лежала под колоколом и поверх бара разделов."""
        self.assertIn('className="tv-drawer-screen otp-modal-root"', self.DRAWER)
        self.assertIn('className="tv-drawer otp-modal-panel"', self.DRAWER)
        self.assertIn('otp-modal-back tv-screen-back', self.DRAWER)
        self.assertIn('<MobileActionSheet', self.DRAWER)

    def test_back_gesture_closes_the_card(self):
        """Без записи в стеке свайп назад менял раздел под открытой карточкой."""
        self.assertIn('useScreenBackGesture(isMobileShell && Boolean(drawerTask), closeDrawer);', VIEW)

    def test_no_keyboard_hint_on_a_phone(self):
        footer = block(self.DRAWER, 'className="tv-drawer-footer otp-modal-footer"', 'footerBtns.map')
        self.assertIn('{!isMobileShell && (', footer)
        self.assertIn('tv-kbd', footer)

    def test_card_blocks_do_not_shrink(self):
        """У flex-потомка с overflow: hidden минимальная высота нулевая:
        участники и сведения ужимались до полоски в 33 px вместо прокрутки."""
        _, body = rule_with(CSS, '.tv-drawer-body > *')
        self.assertIn('flex-shrink: 0', body)

    def test_phone_sheet_only_unpins(self):
        """Закреплённая задача — настольное плавающее окно: на телефоне оно
        встаёт на пол-экрана поверх бара разделов."""
        actions = block(self.DRAWER, 'const sheetActions = [', '].filter(Boolean);')
        self.assertIn("typeof onTogglePinTask === 'function' && isPinned && {", actions)
        self.assertNotIn('Закрепить задачу', actions)

    def test_layers_keep_the_card_above_the_column_window(self):
        """Окно «Посмотреть ещё» на настольных 90 лежало под затемнением
        оболочки (119) — серым. На телефоне оно 120, карточка поверх него 125,
        окна действий поверх карточки 130."""
        self.assertIn('const COLUMN_BROWSER_MOBILE_Z = 120;', BOARD)
        self.assertIn('z={isMobileShell ? COLUMN_BROWSER_MOBILE_Z : COLUMN_BROWSER_Z}', BOARD)
        drawer = rule_exact(CSS, 'body.mobile-shell .tv-root .otp-modal-root.tv-drawer-screen')
        self.assertIn('z-index: 125', drawer)
        modal = rule_exact(CSS, 'body.mobile-shell .tv-root .otp-modal-root.tv-modal-overlay')
        self.assertIn('z-index: 130', modal)

    def test_participants_do_not_become_180px_tall(self):
        """На узком экране колонка превращала flex-basis 180px в высоту."""
        self.assertIn('.tv-participant { flex: none; }', VIEW)


class AddressTests(unittest.TestCase):
    def test_phone_never_writes_task_id_into_the_address(self):
        """replaceState ложился на запись ПОД экраном карточки, и после пары
        жестов «назад» раздел читал task_id как переход по ссылке и заново
        открывал закрытую карточку (трасса истории на стенде 14.09.2026)."""
        self.assertIn('syncTaskDeepLink(isMobileShell ? null : (drawerTask?.id || null));', VIEW)
        self.assertIn('}, [drawerTask?.id, isMobileShell]);', VIEW)

    def test_shell_flag_is_declared_before_the_address_effect(self):
        """Иначе список зависимостей эффекта читает константу до объявления."""
        declared = VIEW.index('const isMobileShell = useIsMobileShell();')
        used = VIEW.index('syncTaskDeepLink(isMobileShell ?')
        self.assertLess(declared, used)
        self.assertEqual(VIEW.count('const isMobileShell = useIsMobileShell();'), 1)


class WindowsTests(unittest.TestCase):
    def test_every_window_leaves_by_the_chevron(self):
        """Крестик справа читается как «отменить», а «Отмена» в подвале — второй
        способ сделать то же, что шеврон."""
        self.assertEqual(VIEW.count('<ModalCloseButton isMobileShell={isMobileShell}'), 5)
        self.assertEqual(VIEW.count('tv-modal-cancel'), 5)
        selectors, body = rule_with(CSS, '.tv-modal-cancel')
        self.assertIn('display: none', body)

    def test_fullscreen_sheet_has_a_phone_header(self):
        """Крестик «Заметок» падал второй строкой под заголовок, а сквозь 95 %
        подложки проступал раздел."""
        self.assertIn('{isNarrowShell ? (', SHEET)
        self.assertIn('otp-modal-back grid h-9 w-9', SHEET)
        self.assertIn("isNarrowShell ? 'bg-slate-100' : 'bg-slate-100/95 backdrop-blur-sm'", SHEET)


class BoardTests(unittest.TestCase):
    VIEW_BLOCK = block(BOARD, 'const BoardView = ({', '/* ─────────────── Таймлайн')

    def test_phone_shows_one_column_with_a_switch(self):
        """Пять колонок по 268 px — полторы видимых и лента вбок; перетаскивать
        пальцем между ними нельзя вовсе."""
        self.assertIn('if (isMobileShell) {', self.VIEW_BLOCK)
        self.assertIn('className="tb-column-switch"', self.VIEW_BLOCK)
        self.assertIn('renderColumn(activeColumn, true)', self.VIEW_BLOCK)
        # Компьютер — прежняя лента колонок.
        self.assertIn('BOARD_COLUMNS.map((column) => renderColumn(column))', self.VIEW_BLOCK)

    def test_mouse_only_hints_and_controls_are_hidden(self):
        selectors, body = rule_with(CSS, '.tb-drag-hint')
        for part in ('.tb-chunk-select', '.tb-plan-btn', '.tb-timeline-expand'):
            self.assertIn(part, selectors)
        self.assertIn('display: none !important', body)
        self.assertIn('className="tb-drag-hint"', BOARD)

    def test_card_head_stays_a_row(self):
        """Общий слой разделов ставит колонкой любой ряд «items-start + flex-1»
        — точка срочности, тема и значок шли тремя строками."""
        self.assertIn('tb-card-head flex items-start', BOARD)
        _, body = rule_with(CSS, '.tb-card-head')
        self.assertIn('flex-direction: row !important', body)


if __name__ == '__main__':
    unittest.main()
