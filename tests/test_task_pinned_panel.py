"""Закреплённая задача — плавающая панель «Задач» в стиле iOS/macOS (16.11.2026).

Каждое решение здесь найдено на стенде — на странице и в настоящем окне
Document PiP (так панель живёт у владельца в Chrome) — или разбором с мутациями,
и без стража тихо теряется при следующей правке раздела: сборка проходит, а у
панели снова прямые углы, светлые кнопки на «Тёмной» палитре или меню за краем окна.
"""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = (ROOT / 'src' / 'components' / 'tasks' / 'TasksView.jsx').read_text(encoding='utf-8')
PREVIEW = (ROOT / 'src' / 'components' / 'file_preview' / 'FilePreviewSheet.jsx').read_text(encoding='utf-8')
STYLE = VIEW[VIEW.index('styleTag.textContent = `'):VIEW.index('document.head.appendChild(styleTag);')]
WIDGET = VIEW[VIEW.index('export const PinnedTaskWidget'):VIEW.index('/* ─── Уведомления: задачи, которые ждут действия')]
PALETTES = VIEW[VIEW.index('const PIN_PALETTES = ['):VIEW.index('const PIN_PALETTE_STORAGE_KEY')]
STORAGE = VIEW[VIEW.index('const PIN_PALETTE_STORAGE_KEY'):VIEW.index('const PIN_TONES')]


def css_rules(css):
    text = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    return [(sel.strip(), body) for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', text)]


def rule(selector):
    for selectors, body in css_rules(STYLE):
        if selectors == selector:
            return body
    raise AssertionError(f'нет правила {selector}')


def palette_blocks():
    blocks = re.findall(r"id: '(\w+)',\s*label: '[^']+',\s*vars: \{(.*?)\n    \}", PALETTES, flags=re.S)
    return dict(blocks), [pid for pid, _ in blocks]


def fragment(start, end):
    begin = WIDGET.index(start)
    return WIDGET[begin:WIDGET.index(end, begin)]


class PanelLookTests(unittest.TestCase):
    def test_panel_owns_radius_tokens(self):
        """У панели нет предка .tv-root: без своих --radius-* общие .tv-btn и
        .tv-input получали border-radius: var(--radius-sm) пустым — прямые углы."""
        body = rule('.tv-pin-widget')
        for token in ('--radius:', '--radius-sm:', '--radius-xs:', '--shadow-sm:'):
            self.assertIn(token, body)

    def test_dark_layer_generator_leaves_the_panel_alone(self):
        """Генератор тёмного слоя переписывает (с !important) правила с литералами
        цвета: светлая палитра на тёмном сайте получила бы тёмные кнопки. Спрашиваем
        сам генератор, а не свой список свойств — у него он шире."""
        spec = importlib.util.spec_from_file_location('build_dark_theme', ROOT / 'scripts' / 'build_dark_theme.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        leaked = [selector for _, selector, _ in builder.palette_rules() if 'tv-pin' in selector]
        self.assertEqual(leaked, [])

    def test_every_palette_sets_the_whole_surface(self):
        """В окне «поверх окон» тёмного слоя сайта нет: недостающая переменная
        осталась бы светлой (у прежней «Тёмной» — кнопки и метки)."""
        blocks, order = palette_blocks()
        self.assertEqual(order, ['paper', 'midnight', 'mint', 'focus'])
        base = ('--bg', '--surface', '--ink', '--ink-3', '--accent', '--accent-fg', '--select-bg',
                '--select-ink', '--fill', '--pin-panel', '--pin-bar', '--pin-line', '--pin-link', '--pin-seg-thumb')
        for pid, body in blocks.items():
            for token in base:
                self.assertIn(f"'{token}'", body, f'{pid}: нет {token}')
        # Тёмная палитра перекрывает ВСЁ, что объявлено на корне панели (кроме радиусов).
        declared = re.findall(r'(--[\w-]+):', rule('.tv-pin-widget'))
        missing = [name for name in declared
                   if not name.startswith('--radius') and f"'{name}'" not in blocks['midnight']]
        self.assertEqual(missing, [])
        # Главная кнопка «Светлой» — синяя, как на сайте, а не чёрная.
        self.assertIn("'--accent': '#2563eb'", blocks['paper'])

    def test_dark_segment_thumb_differs_from_the_surface(self):
        """В «Тёмной» выбранный сегмент (Тип, Срочность) был цвета --surface и
        сливался с дорожкой: отношение яркостей 1.06."""
        blocks, _ = palette_blocks()
        thumb = re.search(r"'--pin-seg-thumb': '([^']+)'", blocks['midnight']).group(1)
        surface = re.search(r"'--surface': '([^']+)'", blocks['midnight']).group(1)
        self.assertNotEqual(thumb.lower(), surface.lower())
        self.assertIn('var(--pin-seg-thumb)', rule('.tv-pin-segmented button.is-active'))
        self.assertIn('var(--pin-seg-thumb)', rule('.tv-pin-form .tv-composer-segmented button.is-active'))

    def test_header_links_use_their_own_color(self):
        """Текст-ссылки шапки — --pin-link, темнее акцента у «Мяты» и «Контраста»."""
        self.assertIn('color: var(--pin-link);', rule('.tv-pin-back,\n  .tv-pin-text-btn'))

    def test_choice_is_saved_only_when_made(self):
        """Оформление «как у сайта» не записывается: иначе после смены темы сайта
        панель осталась бы в прежней. Старый ключ прежняя панель заполняла сама
        («Светлая» всем) — ему верим только для не-«Светлой»."""
        self.assertIn("data-otp-theme') === 'dark'", WIDGET)
        self.assertIn('const choosePalette = useCallback((id) => {', WIDGET)
        self.assertIn('onClick={() => choosePalette(palette.id)}', WIDGET)
        # Единственный вызов — щелчок по плитке «Оформления».
        self.assertEqual(WIDGET.count('choosePalette('), 1)
        self.assertEqual(WIDGET.count('localStorage.setItem(PIN_PALETTE_STORAGE_KEY'), 1)
        for effect in re.findall(r'useEffect\(\(\) => \{.*?\n  \}, \[', WIDGET, flags=re.S):
            self.assertNotIn('PIN_PALETTE_STORAGE_KEY', effect)
            self.assertNotIn('choosePalette', effect)
        self.assertIn("const PIN_PALETTE_STORAGE_KEY = 'otp:pinned-task-palette:v2';", STORAGE)
        self.assertIn("legacy !== 'paper'", STORAGE)
        self.assertIn('storage.removeItem(PIN_PALETTE_LEGACY_KEY);', STORAGE)

    def test_files_use_the_viewer_type_badge(self):
        """Строки файлов — с той же цветной меткой типа, что список в окне просмотра;
        в окне PiP файл скачивается — и значок «скачать», а не шеврон «перейти»."""
        self.assertIn('export function TypeBadge(', PREVIEW)
        self.assertIn('<TypeBadge ext={ext} kind={kind} />', WIDGET)
        self.assertIn('? <Download className="tv-pin-chevron"', WIDGET)

    def test_notes_empty_label_keeps_its_style(self):
        """Подпись пустой колонки заметок жила под классом старой панели и потеряла
        бы цвет и размер вместе с ним — и в «Заметках» раздела тоже."""
        self.assertIn('<span className="tv-note-column-empty">{emptyText}</span>', VIEW)
        self.assertIn('color: var(--ink-3);', rule('.tv-note-column-empty'))


class PanelBehaviourTests(unittest.TestCase):
    def test_more_menu_lives_inside_the_panel(self):
        """IosMenu рисуется порталом в документ портала — в окне PiP его бы не было.
        Своё меню — внутри секции панели и без портала."""
        self.assertNotIn('<IosMenu', WIDGET)
        menu = fragment("{moreOpen && view === 'task' && (", '</section>')
        self.assertNotIn('createPortal', menu)
        self.assertIn('ref={moreMenuRef}', menu)
        self.assertIn('role="menu"', menu)
        self.assertIn("const doc = widgetRef.current?.ownerDocument || document;", WIDGET)

    def test_menu_opens_into_a_grown_pip_window(self):
        """В ужатом окне PiP меню уходило за нижний край («Удалить задачу» не
        нажать): сначала окну возвращается высота, страховка — прокрутка меню."""
        toggle = fragment('const toggleMore = (event) => {', '  const runMenuAction')
        self.assertIn('if (pipWindow && !expanded) {', toggle)
        self.assertIn('fitPipWindow(true);', toggle)
        self.assertIn('overflow-y: auto;', rule('.tv-pin-widget.is-detached .tv-pin-menu'))

    def test_keyboard_in_the_menu(self):
        menu_effect = fragment('if (!moreOpen) return undefined;', '}, [moreOpen]);')
        self.assertIn("event.key === 'Escape' || (event.key === 'Tab'", menu_effect)
        self.assertIn('moreFocusFirstRef.current', menu_effect)
        self.assertIn('moreFocusFirstRef.current = event?.detail === 0;', WIDGET)
        self.assertIn('[data-pin-tool="${key}"]', WIDGET)

    def test_header_drags_except_its_buttons(self):
        self.assertIn(".closest?.('button, a, input, textarea, select, [role=\"menu\"]')", WIDGET)
        # Щелчок без сдвига — не перенос: без порога панель вздрагивала тенью.
        self.assertRegex(WIDGET, r'Math\.hypot\([^)]*\) < 3\) return;')

    def test_collapse_shrinks_the_pip_window(self):
        """Свёрнутое содержимое в прежнем окне оставляло пустую серую половину."""
        self.assertIn('const fitPipWindow = useCallback((nextExpanded) => {', WIDGET)
        self.assertIn('win.resizeTo(win.outerWidth, Math.ceil(content + frame));', WIDGET)
        toggle = fragment('const toggleExpanded = useCallback(() => {', '}, [expanded, fitPipWindow')
        self.assertRegex(toggle, r'\}\);\n\s*fitPipWindow\(next\);\n\s*$')

    def test_collapsed_pip_window_is_not_expanded_from_outside(self):
        """Закрепили другую задачу при ужатом окне: панель считалась развёрнутой
        в окне-полоске, и «Развернуть» окно больше не увеличивало."""
        self.assertIn('setExpanded(pipCollapsedRef.current ? false : Boolean(initialExpanded));', WIDGET)
        self.assertIn('if (!(pipWindow && pipCollapsedRef.current)) {', WIDGET)
        self.assertIn('if (!pipCollapsedRef.current) pipExpandedHeightRef.current = win.outerHeight;', WIDGET)

    def test_notes_fill_the_pip_window(self):
        """Окно PiP уже 680px: телефонные правила раздела давали заметкам предел
        высоты и отступ, а сжатый редактор обрезал свою кнопку «Сохранить»."""
        self.assertIn('max-height: none;', rule('.tv-pin-widget .tv-notes-panel.is-fullscreen'))
        self.assertIn('min-height: auto;', rule('.tv-pin-widget .tv-notes-panel.is-fullscreen .tv-notes-editor'))
        self.assertIn('padding: 0;', rule('.tv-pin-widget .tv-notes-panel,\n  .tv-pin-widget .tv-notes-panel.is-compact'))
        self.assertIn('flex-direction: row;', rule('.tv-pin-widget .tv-notes-editor-footer'))
        self.assertIn('flex: none;', rule('.tv-pin-widget .tv-notes-editor-actions .tv-btn'))

    def test_blocks_do_not_shrink_in_a_long_panel(self):
        """Группы со скруглением держат overflow: hidden — во flex-колонке это
        нулевая минимальная высота: на длинной задаче сведения сжимались до полоски."""
        self.assertIn('flex-shrink: 0;', rule('.tv-pin-scroll > *'))
        self.assertIn('overflow-y: auto;', rule('.tv-pin-scroll'))

    def test_page_panel_stays_on_screen_and_docked(self):
        self.assertIn('new ResizeObserver(() => {', WIDGET)
        self.assertIn('max-height: calc(100vh - 24px);', rule('.tv-pin-widget'))
        # Прижатая к низу панель при «Свернуть» остаётся у края, а не повисает.
        self.assertIn('gap <= 32 ? window.innerHeight - gap - height : prev.y', WIDGET)

    def test_normal_priority_is_not_a_tag(self):
        """«Обычная» — отсутствие признака: как в списке задач, капсулы у неё нет."""
        self.assertIn("task?.priority && task.priority !== 'normal'", WIDGET)
        self.assertIn('{priorityTone && <span className={`tv-pin-tag is-${priorityTone}`}>{pm.label}</span>}', WIDGET)

    def test_footer_has_no_empty_phrase(self):
        """Своя задача без шагов по статусу: вместо «быстрых действий нет» —
        «Изменить» (как видимая кнопка у прежней панели); без действий подвала нет."""
        self.assertNotIn('Для текущего статуса быстрых действий нет', WIDGET)
        self.assertIn(') : canEditTask && onEditTask ? (', WIDGET)
        self.assertIn('footer = null;', WIDGET)


class PanelDeadlineTests(unittest.TestCase):
    """Дедлайн в быстрой форме — днём и временем, как в окне задачи (19.11.2026)."""

    def test_quick_form_picks_a_day_not_a_count_of_days(self):
        form = fragment('const quickTaskForm = quickFormMode ? (', '\n  ) : null;')
        self.assertIn('<TaskDeadlineField', form)
        self.assertIn("updateQuickFormField('dueAt', next || '')", form)
        self.assertNotIn('Дедлайн через', form)
        self.assertNotRegex(form, r'placeholder="(Дней|Часов|Минут)"')

    def test_quick_form_refuses_a_deadline_in_the_past(self):
        submit = fragment('const submitQuickForm = useCallback(', 'const payload = buildTaskJsonPayload(quickForm);')
        self.assertIn('deadlineIssues(quickForm)', submit)
        self.assertIn('setQuickFormError(pastDeadline.text);', submit)

    def test_pinned_create_sends_the_day_and_time(self):
        """Новая задача из закреплённой уходит своим сборщиком формы в App.jsx:
        без due_at в нём выбранный в календаре срок молча терялся бы."""
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        start = app.index('const appendPinnedTaskPayloadFormData = useCallback(')
        builder = app[start:app.index('}, []);', start)]
        self.assertIn("if (payload.due_at) body.append('due_at', String(payload.due_at));", builder)
        for stale in ('deadline_days', 'deadline_hours', 'deadline_minutes'):
            self.assertNotIn(stale, builder)


if __name__ == '__main__':
    unittest.main()
