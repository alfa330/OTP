"""Раздел «Задачи» — новый вид «Галерея» (16–17.11.2026).

16.11 владелец посмотрел три варианта вида на стенде и выбрал третий — «Галерею»
(задачи карточками в сетке, как «Заметки» в режиме галереи) с доработками: верх
«Обзора» переделать — люди «Моих задач» лентой аватаров, фильтры тремя меню; на
доске серые колонки — в длину своих задач. 17.11: цветные плитки цифр — «слишком
пёстро», они белые, цвет только у кружка со значком.
Решения ниже найдены на стенде (компьютер 1680×956 и 1024×768, телефон 390×844,
тёмная тема сайта) — без стража они тихо уходят при следующей правке раздела.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'src' / 'components' / 'tasks'
VIEW = (TASKS / 'TasksView.jsx').read_text(encoding='utf-8')
BOARD = (TASKS / 'TaskBoardWorkspace.jsx').read_text(encoding='utf-8')
MOBILE = (TASKS / 'tasks-mobile.css').read_text(encoding='utf-8')
CHROME = (ROOT / 'scripts' / 'dark_theme_chrome.css').read_text(encoding='utf-8')
STYLE = VIEW[VIEW.index('styleTag.textContent = `'):VIEW.index('document.head.appendChild(styleTag);')]
LOOK = STYLE[STYLE.index('/* ═══ Новый вид раздела (16.11.2026)'):STYLE.index('@media (max-width: 680px) {\n    .tv-root { padding: 16px 12px; }')]
ROW = VIEW[VIEW.index('const TaskRow = React.memo('):VIEW.index('/* ─── TaskDrawer')]
DESKTOP_ROW = ROW[ROW.index('  /* Строка списка iOS'):]
GROUPED = VIEW[VIEW.index('const renderGroupedTasks = '):VIEW.index('{/* Top bar */}')]
TASK_LIST = VIEW[VIEW.index('const renderTaskList = '):VIEW.index('const renderGroupedTasks = ')]
TOOLBAR = VIEW[VIEW.index('{/* Toolbar: search + filters */}'):VIEW.index('<div className="tv-results-info">')]
FILTER_MENU = VIEW[VIEW.index('const TaskFilterMenu = '):VIEW.index('const alignPeopleTip = ')]


def css_rules(css):
    text = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    text = re.sub(r'@keyframes\s+[\w-]+\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', '', text)
    return [(sel.strip(), body) for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', text)]


def rule(css, selector):
    """Тело правила с ровно таким селектором (первое вхождение)."""
    for selectors, body in css_rules(css):
        if selectors.split('@media')[-1].strip().endswith(selector) and selectors.strip().endswith(selector):
            return body
    raise AssertionError(f'нет правила {selector}')


class DesktopOnlyTests(unittest.TestCase):
    def test_new_rules_never_reach_the_phone(self):
        """У телефона свой слой (tasks-mobile.css), он опирается на разметку:
        правила нового вида заперты на компьютер."""
        shared = {'.tv-task-row-check', '.tv-task-row-state'}
        for selectors, _ in css_rules(LOOK):
            for selector in selectors.split(','):
                selector = selector.strip()
                if selector.startswith('@media'):
                    selector = selector[selector.index(')') + 1:].strip()
                if any(selector.startswith(name) for name in shared):
                    continue
                self.assertTrue(selector.startswith('body:not(.mobile-shell)'), f'не заперто: {selector}')

    def test_phone_hides_widget_icons(self):
        self.assertIn('body.mobile-shell .tv-root .tv-stat-icon {\n    display: none;', MOBILE)

    def test_narrow_window_keeps_people_and_dates(self):
        """На окне ~900 px подзаголовок переносится, а не режет имена и «Ждёт ответа»."""
        flow = LOOK[LOOK.index('body:not(.mobile-shell) .tv-root .tv-task-row-flow {'):]
        self.assertIn('flex-wrap: wrap;', flow[:200])
        self.assertIn('.tv-task-row-flow .tv-clar-waiting { flex-shrink: 0; }', LOOK)

    def test_list_markup_stays_flat_for_the_phone(self):
        """Карточки встают в сетку из той же плоской разметки, что и на телефоне:
        разделители дат и задачи — прямые дети списка, без обёрток по группам.
        Разделитель на компьютере — во всю строку сетки."""
        self.assertIn('<div className="tv-task-list">', TASK_LIST)
        self.assertIn('<div key={row.key} className="tv-task-date-separator">', TASK_LIST)
        self.assertIn('compact={isMobileShell}', TASK_LIST)
        self.assertIn('grid-column: 1 / -1;', rule(LOOK, 'body:not(.mobile-shell) .tv-root .tv-task-date-separator'))

    def test_people_and_filters_stay_rows_and_chips_on_the_phone(self):
        """Лента аватаров и меню фильтров — только компьютер; на телефоне прежние
        строки людей и капсулы: их вид задаёт tasks-mobile.css."""
        self.assertLess(GROUPED.index('if (!isMobileShell) {'), GROUPED.index('<div className="tv-people"'))
        self.assertIn('className="tv-person-row"', GROUPED[GROUPED.index('<div className="tv-people"'):])
        self.assertLess(TOOLBAR.index('{!isMobileShell ? ('), TOOLBAR.index('<div className="tv-filter-menus">'))
        self.assertIn('<div className="tv-filter-chips">', TOOLBAR[TOOLBAR.index('<div className="tv-filter-menus">'):])


class OverviewTests(unittest.TestCase):
    def test_segments_tiles_and_large_title(self):
        self.assertIn('grid-template-columns: repeat(4, minmax(0, 1fr));', LOOK)
        self.assertIn('font-size: 34px;', LOOK)
        for icon in ('<Layers', '<Timer', '<CheckCircle2', '<Undo2', '<ListTodo'):
            self.assertIn(icon, VIEW[VIEW.index('<div className="tv-stats-strip">'):VIEW.index('{/* My tasks */}')])

    def test_tiles_are_one_low_row(self):
        """«Плитки в один ряд, ниже ростом»: пять в ряд, значок, подпись и число
        в одну строку."""
        self.assertIn('grid-template-columns: repeat(5, minmax(0, 1fr));', rule(LOOK, 'body:not(.mobile-shell) .tv-root .tv-stats-strip'))
        card = rule(LOOK, 'body:not(.mobile-shell) .tv-root .tv-stat-card')
        self.assertIn('grid-template-columns: auto minmax(0, 1fr) auto;', card)
        self.assertIn('min-height: 68px;', card)

    def test_tiles_stack_in_a_narrow_window(self):
        """На 1024 px «Выполнено» наезжало на цифру: уже 1200 px значок и число
        встают в первую строку, подпись — во вторую, ряд остаётся одним."""
        narrow = LOOK[LOOK.index('@media (max-width: 1199px) {'):]
        narrow = narrow[:narrow.index('\n  }\n') + 4]
        self.assertIn('grid-template-rows: auto auto;', narrow)
        self.assertIn('grid-column: 1 / -1; grid-row: 2;', narrow)
        self.assertNotIn('display: none', narrow)
        self.assertNotIn('repeat(', narrow)

    def test_tiles_are_calm(self):
        """Пять цветных градиентов подряд владелец назвал «слишком пёстро» (17.11):
        плитки белые, цвет — только у кружка со значком, цифры нейтральные."""
        tiles = [(s, b) for s, b in css_rules(LOOK) if '.tv-stat' in s]
        self.assertTrue(tiles)
        for selectors, body in tiles:
            self.assertNotIn('gradient', body, selectors)
        card = rule(LOOK, 'body:not(.mobile-shell) .tv-root .tv-stat-card')
        self.assertIn('background: var(--surface);', card)
        self.assertIn('color: var(--ink);', rule(LOOK, 'body:not(.mobile-shell) .tv-root .tv-stat-card .tv-stat-value'))
        self.assertIn('background: var(--tile-tone);', rule(LOOK, 'body:not(.mobile-shell) .tv-root .tv-stat-icon'))

    def test_tile_colours_survive_the_dark_layer(self):
        """Цвета плиток — переменными: литералы генератор тёмного слоя перекрашивал
        с !important; цифры нейтральные и в темноте — старое правило красит «В работе»
        литералом, и его тёмная пара выделяла бы одну плитку оранжевым."""
        for selectors, body in css_rules(LOOK):
            if '.tv-stat-card' not in selectors:
                continue
            for declaration in body.split(';'):
                prop, _, value = declaration.partition(':')
                if prop.strip() in ('background', 'background-color', 'color'):
                    self.assertNotIn('#', value, f'{selectors}: {declaration.strip()}')
        dark = CHROME[CHROME.index('html[data-otp-theme="dark"] body:not(.mobile-shell) .tv-root .tv-stat-card .tv-stat-value {'):]
        self.assertIn('color: var(--ink) !important;', dark[:200])

    def test_people_are_an_avatar_strip(self):
        """Люди — лентой аватаров: число на аватаре — сколько ждут принятия (во
        «Входящих» — красным, как «Ждут вас»), остальное — в подсказке и в подписи
        для скринридера. Лента переносится, а не прокручивается вбок."""
        strip = GROUPED[GROUPED.index('<div className="tv-people"'):GROUPED.index('className="tv-person-row"')]
        self.assertIn("`tv-people-badge ${showNotAccepted ? 'is-alert' : ''}`", strip)
        self.assertIn('{waiting > 0 && (', strip)
        for label in ("'Ожидают принятия'", "'Ещё не приняты'", '<span>В работе</span>', '<span>Выполнено</span>'):
            self.assertIn(label, strip)
        self.assertIn('aria-label={`${group.name}: ${waitingLabel.toLowerCase()} ${waiting}, в работе ${group.active}, выполнено ${group.done}`}', strip)
        self.assertIn('flex-wrap: wrap;', rule(LOOK, 'body:not(.mobile-shell) .tv-root .tv-people'))
        self.assertIn('--people-badge: #e11d48;', LOOK)

    def test_people_tip_stays_on_screen_and_works_from_keyboard(self):
        """Подсказка у края окна прижимается к краю (у левого уходила бы под меню
        портала) и показывается и с клавиатуры."""
        self.assertIn('onMouseEnter={alignPeopleTip}', GROUPED)
        self.assertIn('onFocus={alignPeopleTip}', GROUPED)
        self.assertIn('.tv-people-item[data-tip="start"] .tv-people-tip', LOOK)
        self.assertIn('.tv-people-item[data-tip="end"] .tv-people-tip', LOOK)
        self.assertIn('body:not(.mobile-shell) .tv-root .tv-people-item:focus-visible .tv-people-tip', LOOK)

    def test_filters_are_three_menus(self):
        """Вместо 11 капсул — «Статус», «Тип», «Срочность»: меню с галочкой, первым —
        «Все …», выбранное — в подписи кнопки и мягким голубым, «Обычная» не
        фильтр. Меню — общий IosMenu, а не своё."""
        menus = TOOLBAR[TOOLBAR.index('<div className="tv-filter-menus">'):TOOLBAR.index('<div className="tv-filter-chips">')]
        for title in ('title="Статус"', 'title="Тип"', 'title="Срочность"'):
            self.assertIn(title, menus)
        # Именно в меню, а не у капсул телефона, где то же выражение тоже есть.
        self.assertIn(".filter(([key]) => key !== 'normal')", menus)
        self.assertIn('<IosMenu', FILTER_MENU)
        self.assertIn("{ key: '__all', label: allLabel, checked: !current, onSelect: () => onChange('') },", FILTER_MENU)
        self.assertIn('checked: option.value === value,', FILTER_MENU)
        self.assertIn("className: `tv-filter-menu ${current ? 'is-set' : ''}`", FILTER_MENU)
        is_set = LOOK[LOOK.index('body:not(.mobile-shell) .tv-root .tv-filter-menu.is-set,'):]
        self.assertIn('background: var(--select-bg);', is_set[:400])
        self.assertIn('className="tv-filter-reset"', TOOLBAR)

    def test_person_task_count_is_declined(self):
        """«Админов Админ · 2 задачи», а не «2 задач»."""
        self.assertIn("{pluralRu(selectedTotal, 'задача', 'задачи', 'задач')}", GROUPED)


class TaskCardTests(unittest.TestCase):
    def test_card_grid(self):
        """Задачи — карточками в сетке; сверху кружок и статус, посередине тема и
        люди, внизу дата и метки. Значки, дата и булавка лежат в одной обёртке, она
        раскрыта, и каждый встаёт в свою область."""
        self.assertIn('grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));', rule(LOOK, 'body:not(.mobile-shell) .tv-root .tv-task-list'))
        card = rule(LOOK, 'body:not(.mobile-shell) .tv-root .tv-task-row:not(.is-compact)')
        self.assertIn('"check state state pin"', card)
        self.assertIn('"main main main main"', card)
        self.assertIn('"date date tag prio"', card)
        self.assertIn('body:not(.mobile-shell) .tv-root .tv-task-row-meta { display: contents; }', LOOK)
        self.assertIn('-webkit-line-clamp: 3;', rule(LOOK, 'body:not(.mobile-shell) .tv-root .tv-task-row-subject'))

    def test_cards_have_no_list_separators(self):
        """Разделитель строк списка и скругление только крайних строк группы
        остались бы полосками над карточками — их нет."""
        self.assertNotIn('.tv-task-row:not(.is-compact) + .tv-task-row:not(.is-compact)::before', LOOK)
        self.assertNotIn(':has(+ .tv-task-date-separator)', LOOK)

    def test_date_wraps_instead_of_covering_marks(self):
        """«Предложение» и «Критичная» в самой узкой карточке оставляют дате меньше
        её ширины — она переносится, а не наезжает на метки."""
        self.assertIn('white-space: normal;', rule(LOOK, 'body:not(.mobile-shell) .tv-root .tv-task-row-date'))

    def test_card_markup(self):
        """Кружок статуса (у выполненных и принятых — с галочкой); «Задача» и
        «Обычная» — значения по умолчанию и не пишутся; срок и «Ждёт ответа» — в карточке."""
        self.assertIn('<span className={`tv-task-row-check is-${statusTone}`} aria-hidden="true">', DESKTOP_ROW)
        self.assertIn('{isDone && <Check', DESKTOP_ROW)
        self.assertIn("{task.tag && task.tag !== 'task' && <span", DESKTOP_ROW)
        self.assertIn("{task?.priority && task.priority !== 'normal' && <span", DESKTOP_ROW)
        self.assertIn('tv-deadline-chip', DESKTOP_ROW)
        self.assertIn('<span className="tv-clar-waiting">Ждёт ответа</span>', DESKTOP_ROW)
        self.assertNotIn('tv-task-row-indicator', VIEW)

    def test_pin_stays_reachable(self):
        """Булавка видна при наведении — и с клавиатуры; щелчок по ней не открывает карточку."""
        self.assertIn('body:not(.mobile-shell) .tv-root .tv-row-pin-btn:focus-visible { opacity: 1; }', LOOK)
        pin = DESKTOP_ROW[DESKTOP_ROW.index('className={`tv-row-pin-btn'):]
        self.assertIn('event.stopPropagation();', pin[:500])

    def test_status_word_has_its_colour_in_both_themes(self):
        """«Выставлен» на графите давал 2,5:1: цвета статуса — переменными с тёмными парами."""
        for tone in ('indigo', 'amber', 'violet', 'emerald', 'rose'):
            self.assertIn(f'.tv-task-row-state.is-{tone} {{ color: var(--state-{tone}); }}', LOOK)
            self.assertIn(f'--state-{tone}:', STYLE)
            self.assertIn(f'--state-{tone}:', CHROME)

    def test_no_row_tints_left_for_the_dark_layer(self):
        """Карточку срочной задачи не красим: старое правило с литералом тёмный
        слой красил розовым с !important."""
        self.assertNotRegex(STYLE, r'\.tv-task-row\.is-(urgent|critical)\s*\{')


class SectionTests(unittest.TestCase):
    def test_round_buttons_keep_their_names(self):
        """«Заметки» и «Обновить» стали значками — подписи остались для мыши и скринридера."""
        desktop = VIEW[VIEW.index('{/* Компьютер: «Заметки» и «Обновить»'):VIEW.index('<PlusIcon /> Новая задача')]
        self.assertIn('aria-label="Заметки"', desktop)
        self.assertIn("aria-label={isAnyTasksLoading ? 'Обновляю…' : 'Обновить'}", desktop)

    def test_card_is_grouped_blocks(self):
        self.assertIn('body:not(.mobile-shell) .tv-drawer-body > hr.tv-divider { display: none; }', LOOK)
        # Участники в две колонки: столбиком «История» уходила под нижнюю панель.
        self.assertIn('grid-template-columns: repeat(2, minmax(0, 1fr));', LOOK[LOOK.index('.tv-drawer-body > .tv-participants {'):])
        self.assertIn("{task?.priority && task.priority !== 'normal' && (", VIEW)

    def test_comparison_variants_are_gone(self):
        """Три вида для сравнения (?look=1|2|3) убраны после выбора: на компьютере
        один вид, без переключателя и без правил боковой панели."""
        for leftover in ('data-look', "searchParams.get('look')", 'otp:tasks-look', 'tv-look-', 'sectionLook'):
            self.assertNotIn(leftover, VIEW)
        self.assertNotIn('[data-look', CHROME)


class DarkThemeTests(unittest.TestCase):
    def test_ios_fills_are_tokens_with_dark_values(self):
        """Полупрозрачные системные заливки генератор делал тёмными с прозрачностью
        12% — на тёмном фоне сегменты и поиск пропадали. Они — переменные, а их тёмные
        значения светлее фона и записаны руками."""
        self.assertNotIn('rgba(118, 118, 128', LOOK)
        for token in ('--ios-fill:', '--ios-fill-strong:', '--ios-sep:'):
            self.assertIn(token, STYLE)
            self.assertIn(token, CHROME)
        # В темноте заливка светлее и плотнее светлой (.24 против .12), а не та же.
        self.assertIn('--ios-fill: rgba(118, 118, 128, .24);', CHROME)
        self.assertIn('--ios-fill: rgba(118, 118, 128, .12);', STYLE)

    def test_badges_and_tile_tones_are_variables(self):
        """Красное и серое число на аватаре и цвет кружка плитки — переменными:
        литералы генератор перекрасил бы с !important."""
        badge = rule(LOOK, 'body:not(.mobile-shell) .tv-root .tv-people-badge')
        self.assertIn('background: var(--people-badge);', badge)
        for tone in ('is-total', 'is-amber', 'is-emerald', 'is-returned', 'is-backlog'):
            self.assertIn(f'body:not(.mobile-shell) .tv-root .tv-stat-card.{tone} {{ --tile-tone: #', LOOK)


class BoardTests(unittest.TestCase):
    def test_board_and_backlog_in_the_same_language(self):
        self.assertIn("rounded-[18px] p-2 transition-colors", BOARD)
        self.assertIn("rounded-[14px] bg-white p-2.5 shadow-[", BOARD)
        # Тема карточки — до трёх строк: на 268 px «…отчёт смены №9» иначе обрезался.
        self.assertIn("text-slate-900 line-clamp-3", BOARD)
        # «В работу» — голубая капсула, а не чёрное наведение: выбранное — голубым.
        self.assertIn('tb-promote-btn rounded-full bg-blue-50', BOARD)
        self.assertNotIn('hover:bg-slate-900 hover:text-white', BOARD)

    def test_columns_are_as_long_as_their_tasks(self):
        """«Серые полоски — в длину самих задач» (владелец, 16.11): колонки не
        тянутся за самой длинной; пока карточку несут — тянутся, чтобы бросить можно
        было и ниже последней карточки."""
        self.assertIn("<div className={`flex min-w-max gap-3 ${dragged ? 'items-stretch' : 'items-start'}`}>", BOARD)


if __name__ == '__main__':
    unittest.main()
