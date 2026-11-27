"""Стражи дедлайна задачи, которые не исполнить без браузера (19.11.2026).

Проверка «срок в прошлом» живёт в обработчиках с хуками (handleCreate,
submitEditTask, submitQuickForm) — исполнить их в node без DOM нельзя, поэтому
стережём само ветвление: проверка стоит в условии, которое реально прерывает
сохранение. Сама функция deadlineIssues исполняется в task_composer_payload.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = (ROOT / 'src' / 'components' / 'tasks' / 'TasksView.jsx').read_text(encoding='utf-8')


def block(start, end):
    begin = VIEW.index(start)
    return VIEW[begin:VIEW.index(end, begin)]


class DeadlineSubmitGuardTests(unittest.TestCase):
    def test_create_stops_on_past_deadline(self):
        body = block('const handleCreate = async (e) => {', 'const body = new FormData();')
        self.assertRegex(
            body,
            r'if \(createBaseIssues\.length \|\| deadlineIssues\(form\)\.length\) \{\s*'
            r'setCreateIssuesShown\(true\);\s*createIssueNav\.first\(\);\s*return;',
        )

    def test_edit_stops_on_past_deadline(self):
        body = block('const submitEditTask = useCallback(', 'const key = `${editModal.taskId}:edit`;')
        self.assertRegex(
            body,
            r'if \(editBaseIssues\.length \|\| deadlineIssues\(editForm\)\.length\) \{\s*'
            r'setEditIssuesShown\(true\);\s*editIssueNav\.first\(\);\s*return;',
        )

    def test_quick_form_stops_on_past_deadline(self):
        body = block('const submitQuickForm = useCallback(', 'const payload = buildTaskJsonPayload(quickForm);')
        self.assertRegex(
            body,
            r'const \[pastDeadline\] = deadlineIssues\(quickForm\);\s*'
            r'if \(pastDeadline\) \{\s*setQuickFormError\(pastDeadline\.text\);\s*return;\s*\}',
        )

    def test_issue_lists_on_screen_include_the_deadline(self):
        # Красным у поля и стрелки ⌃ ⌄ берут ключи из этих списков. У заявки в IT
        # (доска IT Helpdesk, 27.11.2026) срока нет вовсе: срок, заданный до выбора
        # доски, — скрытое поле, и упираться в него отправка заявки не должна.
        self.assertIn(
            'const createIssues = [...createBaseIssues, ...(createIsHelpdesk ? [] : deadlineIssues(form))];', VIEW)
        self.assertIn('const editIssues = [...editBaseIssues, ...deadlineIssues(editForm)];', VIEW)


class DeadlineLayoutTests(unittest.TestCase):
    def test_day_and_time_share_one_row(self):
        """Пара «день + время» — сеткой в две колонки своим классом (телефонная
        оболочка складывает grid-cols-[…] в одну колонку)."""
        rule = re.search(r'\.tv-due-pair \{([^}]*)\}', VIEW)
        self.assertIsNotNone(rule)
        self.assertIn('display: grid', rule.group(1))
        self.assertIn('grid-template-columns: minmax(0, 1fr) auto', rule.group(1))
        field = (ROOT / 'src' / 'components' / 'tasks' / 'TaskDateTimeField.jsx').read_text(encoding='utf-8')
        form = field[field.index('  form: {'):field.index('};', field.index('  form: {'))]
        # Класс коробки — ровно наш, без grid-cols-[…] (комментарий рядом его упоминает).
        self.assertRegex(form, r"\n\s*box: 'tv-due-pair',")

    def test_day_button_takes_the_dark_layer_like_its_neighbours(self):
        """Тёмный слой сайта перекрашивает у кнопок выбора .bg-white и .text-slate-700
        (так темнеет CustomSelect рядом); без них кнопка дня в тёмной теме была другого
        цвета, чем время и напоминание, а в закреплённой панели на тёмном сайте —
        белой или тёмным текстом по тёмному."""
        field = (ROOT / 'src' / 'components' / 'tasks' / 'TaskDateTimeField.jsx').read_text(encoding='utf-8')
        form = field[field.index('  form: {'):field.index('};', field.index('  form: {'))]
        date = re.search(r"\n\s*date: ('[^']*'(?:\s*\+\s*'[^']*')*)", form)
        self.assertIsNotNone(date)
        self.assertIn(' bg-white ', date.group(1))
        self.assertIn(' text-slate-700 ', date.group(1))


class TimeFieldCommitTests(unittest.TestCase):
    def test_leaving_the_field_goes_through_the_plan(self):
        """Уход из поля времени решает commitPlan (исполняется в time_picker_mask):
        нетронутое значение не округляется к шагу. Стережём, что commit им и решает."""
        picker = (ROOT / 'src' / 'components' / 'ui' / 'TimePicker.jsx').read_text(encoding='utf-8')
        body = picker[picker.index('const commit = () => {'):picker.index('const nudge = ')]
        self.assertIn('const plan = commitPlan(draft, value, allowEmpty);', body)
        self.assertRegex(body, r"if \(plan\.kind === 'keep'\) \{[^}]*return; \}")
        self.assertNotIn('parseTimeInput(', body)



def _select_columns(sql):
    """Колонки выборки по верхнему уровню запятых (подзапросы в скобках — одна колонка)."""
    sql = re.sub(r'--[^\n]*', '', sql)
    columns, depth, current = [], 0, ''
    for char in sql:
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        if char == ',' and depth == 0:
            columns.append(current.strip())
            current = ''
        else:
            current += char
    columns.append(current.strip())
    return columns


class RegulationRepeatCopyTests(unittest.TestCase):
    """Повтор регламента (итерация) не становится вторым шаблоном (владелец, 20.11.2026)."""

    def test_server_never_gives_a_repeat_copy_its_own_schedule(self):
        """Защита на сервере: старая вкладка браузера после выкладки ещё шлёт
        «ежедневно» для повтора — расписание у него остаётся пустым."""
        src = (ROOT / 'db' / 'tasks.py').read_text(encoding='utf-8')
        body = src[src.index('    def edit_task('):src.index('    def create_task_report(')]
        select = body[body.index('SELECT') + len('SELECT'):body.index('FROM tasks t')]
        columns = _select_columns(select)
        guard = re.search(
            r'current_regulation_parent_id = row\[(\d+)\]\s*'
            r'if current_regulation_parent_id is not None:(.*?)is_regulation_new, recurrence_type_new',
            body, re.S)
        self.assertIsNotNone(guard)
        self.assertEqual(columns[int(guard.group(1))], 't.regulation_parent_id')
        self.assertIn('recurrence_type_input = None', guard.group(2))
        self.assertIn('recurrence_interval_input = None', guard.group(2))

    def test_forms_hide_the_schedule_of_a_repeat_copy(self):
        self.assertIn("&& !(section.hiddenForRepeatCopy && isRepeatCopy)", VIEW)
        self.assertIn('const isRepeatCopy = Boolean(values.regulationParentId);', VIEW)
        quick = block('const quickTaskForm = quickFormMode ? (', '\n  ) : null;')
        self.assertRegex(quick, r"\{!quickForm\.regulationParentId && \(\s*<div className=\"tv-soft-block\">")


if __name__ == '__main__':
    unittest.main()
