# -*- coding: utf-8 -*-
"""Несколько исполнителей у задачи.

Проверяем сами правила, а не текст исходников: методы Database вытаскиваем из
файла AST'ом (конструктор Database лезет в боевую базу), как это уже сделано в
test_task_clarifications. Плюс несколько маркеров там, где правило живёт в SQL и
проверить его без базы нельзя, — иначе копии правила тихо разъедутся.
"""

import ast
import textwrap
import unittest
from collections import defaultdict
from pathlib import Path
from typing import List, Optional

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
BELL_PATH = ROOT / "notifications" / "sources.py"
TASKS_VIEW_PATH = ROOT / "src" / "components" / "tasks" / "TasksView.jsx"
ASSIGNEES_MODULE_PATH = ROOT / "src" / "components" / "tasks" / "taskAssignees.js"
CLI_PATH = ROOT / "scripts" / "task_board.py"

OWNER = 10
ASSIGNEE = 20
CO_ASSIGNEE = 21
BOSS = 30
STRANGER = 40


def _read(path):
    return path.read_text(encoding="utf-8-sig")


DATABASE_SOURCE = _read(DATABASE_PATH)


def _database_members(names):
    """Исходники методов и полей класса Database по именам."""
    module = source_cache.parse(DATABASE_SOURCE)
    database_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    lines = DATABASE_SOURCE.splitlines(keepends=True)
    found = {}
    for node in database_class.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            # Декоратор (@staticmethod) стоит выше `def` — без него метод
            # теряет свою природу и вызов через класс падает.
            first = min([node.lineno] + [item.lineno for item in node.decorator_list])
            found[node.name] = textwrap.dedent("".join(lines[first - 1:node.end_lineno]))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    found[target.id] = textwrap.dedent(
                        ast.get_source_segment(DATABASE_SOURCE, node)
                    )
    missing = set(names) - set(found)
    assert not missing, f"в Database нет: {sorted(missing)}"
    return found


def _module_members(names):
    module = source_cache.parse(DATABASE_SOURCE)
    found = {}
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.Assign)):
            targets = (
                [node.name] if isinstance(node, ast.FunctionDef)
                else [t.id for t in node.targets if isinstance(t, ast.Name)]
            )
            for name in targets:
                if name in names:
                    found[name] = ast.get_source_segment(DATABASE_SOURCE, node)
    missing = set(names) - set(found)
    assert not missing, f"в database.py нет: {sorted(missing)}"
    return found


NAMES = [
    "TASK_MAX_ASSIGNEES",
    "_TASK_ASSIGNEE_EXISTS_SQL",
    "_task_assignee_tuples",
    "_normalize_task_assignees",
    "_sync_task_assignees_tx",
    "_task_assignee_ids_tx",
    "_task_assignee_scope_tx",
    "_task_visible_for_requester",
    "_task_review_authority",
    "_task_can_review",
]


def _build_database():
    # Аннотации в вытащенных функциях ссылаются на typing — кладём его в
    # пространство имён, иначе exec падает на NameError.
    namespace = {"Optional": Optional, "List": List, "defaultdict": defaultdict}
    for source in _module_members(
        ["normalize_role_value", "role_has_min", "ROLE_ALIASES", "ROLE_HIERARCHY", "_UNSET"]
    ).values():
        exec(source, namespace)
    body = _database_members(NAMES)
    class_source = "class FakeDatabase:\n" + "".join(
        textwrap.indent(body[name], "    ") + "\n" for name in NAMES
    )
    exec(class_source, namespace)
    return namespace


# Пространство имён отдаём целиком: из него нужен не только класс, но и
# сторожевой объект _UNSET, которым edit_task помечает незаполненные поля.
DB_NAMESPACE = _build_database()
FakeDatabase = DB_NAMESPACE["FakeDatabase"]
DB = FakeDatabase()
UNSET = DB_NAMESPACE["_UNSET"]


class _RecordingCursor:
    """Курсор, который только запоминает запросы и отдаёт заготовленные строки."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.executions = []

    def execute(self, query, params=None):
        self.executions.append((" ".join(query.split()), params))

    def fetchall(self):
        return self.rows

    def ran(self, needle):
        return [item for item in self.executions if needle in item[0]]


class NormalizeAssigneesTests(unittest.TestCase):
    """Из чего угодно — упорядоченный уникальный список id."""

    def test_list_keeps_order_and_drops_duplicates(self):
        self.assertEqual(DB._normalize_task_assignees([20, 21, 20]), [20, 21])
        self.assertEqual(DB._normalize_task_assignees([21, 20]), [21, 20])

    def test_comma_string_from_multipart_form(self):
        # Так состав приезжает в POST /api/tasks: одно поле, id через запятую.
        self.assertEqual(DB._normalize_task_assignees("20,21, 7"), [20, 21, 7])
        self.assertEqual(DB._normalize_task_assignees("20"), [20])

    def test_single_id_still_works(self):
        # Старые вызовы, знающие только одного исполнителя, не должны падать.
        self.assertEqual(DB._normalize_task_assignees(20), [20])
        self.assertEqual(DB._normalize_task_assignees("20"), [20])

    def test_none_means_do_not_touch_the_crew(self):
        """Отличать «не менять» от «снять всех» обязательно.

        Иначе правка одной темы задачи молча снимала бы с неё всех исполнителей.
        """
        self.assertIsNone(DB._normalize_task_assignees(None))
        # _UNSET — тот же смысл: у edit_task так помечены незаполненные поля.
        self.assertIsNone(DB._normalize_task_assignees(UNSET))

    def test_empty_crew_is_refused(self):
        for value in ([], "", "   ", [None], [""], ","):
            with self.assertRaises(ValueError) as ctx:
                DB._normalize_task_assignees(value)
            self.assertEqual(str(ctx.exception), "ASSIGNEE_REQUIRED")

    def test_garbage_is_refused(self):
        for value in (["нет"], [0], [-3], [1.5, "x"], {"id": 1}):
            with self.assertRaises(ValueError):
                DB._normalize_task_assignees(value)

    def test_ceiling_is_enforced(self):
        top = list(range(1, DB.TASK_MAX_ASSIGNEES + 1))
        self.assertEqual(DB._normalize_task_assignees(top), top)
        with self.assertRaises(ValueError) as ctx:
            DB._normalize_task_assignees(top + [999])
        self.assertEqual(str(ctx.exception), "TOO_MANY_ASSIGNEES")

    def test_ceiling_matches_the_frontend(self):
        """Потолок в UI и в API — одно число, иначе форма даёт выбрать то, что API отвергнет."""
        js = _read(ASSIGNEES_MODULE_PATH)
        self.assertIn(f"TASK_MAX_ASSIGNEES = {DB.TASK_MAX_ASSIGNEES};", js)


class SyncAssigneesTests(unittest.TestCase):
    """Состав задачи = ровно переданный список, в его порядке."""

    def test_missing_people_are_removed_and_positions_rewritten(self):
        cursor = _RecordingCursor()
        DB._sync_task_assignees_tx(cursor, 412, [21, 20], added_by=OWNER)

        removed = cursor.ran("DELETE FROM task_assignees")
        self.assertEqual(len(removed), 1)
        # Удаляем всех, кого в новом составе нет.
        self.assertIn("NOT (user_id = ANY(%s))", removed[0][0])
        self.assertEqual(removed[0][1], (412, [21, 20]))

        inserted = cursor.ran("INSERT INTO task_assignees")
        self.assertEqual(len(inserted), 1)
        # Одним запросом на весь состав, а не циклом по людям.
        self.assertIn("WITH ORDINALITY", inserted[0][0])
        self.assertEqual(inserted[0][1], (412, OWNER, [21, 20]))

    def test_existing_rows_keep_their_history(self):
        """У оставшегося исполнителя added_at/added_by не переписываются.

        Иначе «когда его подключили» превратилось бы в дату последней правки.
        """
        cursor = _RecordingCursor()
        DB._sync_task_assignees_tx(cursor, 412, [20], added_by=OWNER)
        inserted = cursor.ran("INSERT INTO task_assignees")[0][0]
        self.assertIn("ON CONFLICT (task_id, user_id) DO UPDATE SET position = EXCLUDED.position",
                      inserted)
        self.assertNotIn("added_at = ", inserted)
        self.assertNotIn("added_by = EXCLUDED", inserted)

    def test_empty_crew_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            DB._sync_task_assignees_tx(_RecordingCursor(), 412, [])
        self.assertEqual(str(ctx.exception), "ASSIGNEE_REQUIRED")


class VisibilityTests(unittest.TestCase):
    """Задачу видит каждый исполнитель, а не только первый."""

    def _visible(self, role, requester_id, assignees, created_by=OWNER, requested_by=None):
        return DB._task_visible_for_requester(role, requester_id, created_by, assignees, requested_by)

    def test_every_assignee_sees_the_task(self):
        crew = [(ASSIGNEE, 'operator', None), (CO_ASSIGNEE, 'operator', None)]
        self.assertTrue(self._visible('operator', ASSIGNEE, crew))
        self.assertTrue(self._visible('operator', CO_ASSIGNEE, crew))
        self.assertFalse(self._visible('operator', STRANGER, crew))

    def test_creator_and_requester_still_see_it(self):
        crew = [(ASSIGNEE, 'operator', None)]
        self.assertTrue(self._visible('operator', OWNER, crew))
        self.assertTrue(self._visible('operator', BOSS, crew, requested_by=BOSS))

    def test_supervisor_sees_it_if_any_assignee_is_his_operator(self):
        """Правило СВ — «хотя бы один», а не «первый».

        Иначе задача, где его оператор стоит вторым, для СВ бы исчезла.
        """
        crew = [(ASSIGNEE, 'operator', 99), (CO_ASSIGNEE, 'operator', BOSS)]
        self.assertTrue(self._visible('sv', BOSS, crew))
        # Чужие операторы СВ не открывают.
        self.assertFalse(self._visible('sv', 77, crew))

    def test_admin_sees_everything(self):
        self.assertTrue(self._visible('admin', STRANGER, [(ASSIGNEE, 'operator', None)]))

    def test_plain_id_list_is_accepted(self):
        """Часть вызывающих знает только id — заглушки-кортежи плодить незачем."""
        self.assertTrue(self._visible('operator', CO_ASSIGNEE, [ASSIGNEE, CO_ASSIGNEE]))
        self.assertTrue(self._visible('operator', ASSIGNEE, ASSIGNEE))
        self.assertFalse(self._visible('operator', STRANGER, None))


class ReviewAuthorityTests(unittest.TestCase):
    """Свою работу не принимает ни один из исполнителей."""

    def test_co_assignee_cannot_accept(self):
        crew = [(ASSIGNEE, 'operator', None), (CO_ASSIGNEE, 'operator', None)]
        self.assertFalse(DB._task_can_review('admin', CO_ASSIGNEE, OWNER, crew, None))
        self.assertFalse(DB._task_can_review('operator', ASSIGNEE, OWNER, crew, None))

    def test_requester_accepts_even_when_he_is_a_co_assignee(self):
        """Поручитель, вписавший себя соисполнителем, приёмку не теряет.

        Иначе задача «сделаем вдвоём» навсегда застряла бы на проверке: принять
        её больше некому.
        """
        crew = [(ASSIGNEE, 'operator', None), (BOSS, 'sv', None)]
        self.assertTrue(DB._task_can_review('sv', BOSS, OWNER, crew, BOSS))

    def test_creator_accepts_when_he_is_outside_the_crew(self):
        crew = [(ASSIGNEE, 'operator', None), (CO_ASSIGNEE, 'operator', None)]
        self.assertTrue(DB._task_can_review('operator', OWNER, OWNER, crew, None))


class SqlCopiesTests(unittest.TestCase):
    """Правило «я исполнитель» живёт в SQL — сверяем, что копии не разъехались."""

    def test_participation_survives_a_task_without_crew_rows(self):
        """Окно деплоя: задача может появиться БЕЗ строк состава.

        Пока новый процесс уже принимает запросы, старый ещё дорабатывает и
        создаёт задачи прежним кодом — без записи в task_assignees. Если бы
        участие считалось только по связи, такая задача молча пропала бы у
        своего исполнителя из «моих задач», доски и колокола до следующего
        перезапуска. Скалярная колонка заполнена всегда, поэтому она и стоит
        вторым основанием.
        """
        predicate = DB._TASK_ASSIGNEE_EXISTS_SQL
        self.assertIn("t.assigned_to = %s", predicate)
        self.assertIn("FROM task_assignees ta_f", predicate)
        # Ровно два плейсхолдера — оба под id одного человека. Разъедется это
        # число, и все выборки задач начнут падать на порядке параметров.
        self.assertEqual(predicate.count("%s"), 2)

    def test_participation_is_exists_not_join(self):
        """JOIN на состав размножил бы задачу: поехали бы COUNT, сводка и страница."""
        self.assertIn("_TASK_ASSIGNEE_EXISTS_SQL", DATABASE_SOURCE)
        self.assertIn("FROM task_assignees ta_f", DATABASE_SOURCE)
        # Ни одна выборка задач не должна джойнить состав ради фильтра.
        self.assertNotIn("JOIN task_assignees ta ON ta.task_id = t.id\n"
                         "                WHERE", DATABASE_SOURCE)

    def test_list_loads_the_crew_in_a_batch(self):
        """Состав — шестым пакетным запросом, как история и чек-лист."""
        start = DATABASE_SOURCE.index("    def get_tasks_for_requester(")
        block = DATABASE_SOURCE[start:DATABASE_SOURCE.index("    def get_tasks_for_export", start)]
        self.assertIn("WHERE ta.task_id = ANY(%s)", block)
        self.assertIn('"assignees": assignee_map.get(task_id, [])', block)
        # Главный запрос остаётся «одна строка = одна задача».
        self.assertIn("LEFT JOIN users assignee ON assignee.id = t.assigned_to", block)

    def test_search_finds_any_assignee_without_duplicating_rows(self):
        start = DATABASE_SOURCE.index("    def get_tasks_for_requester(")
        block = DATABASE_SOURCE[start:DATABASE_SOURCE.index("    def get_tasks_for_export", start)]
        self.assertIn("FROM task_assignees ta_s", block)
        self.assertIn("COALESCE(au.name, '') ILIKE %s", block)

    def test_export_lists_every_assignee(self):
        start = DATABASE_SOURCE.index("    def get_tasks_for_export(")
        block = DATABASE_SOURCE[start:DATABASE_SOURCE.index("    def update_task_status", start)]
        self.assertIn("string_agg(au.name", block)
        self.assertIn("FROM task_assignees ta_x", block)

    def test_regulation_clone_inherits_the_whole_crew(self):
        """Регламент на троих обязан рождать задачу на троих, а не на одного."""
        start = DATABASE_SOURCE.index("    def materialize_due_regulation_tasks(")
        block = DATABASE_SOURCE[start:DATABASE_SOURCE.index("\n    def ", start + 10)]
        self.assertIn("template_assignee_ids = self._task_assignee_ids_tx(cursor, root_id)", block)
        self.assertIn("self._sync_task_assignees_tx(", block)

    def test_reminders_reach_every_assignee(self):
        start = DATABASE_SOURCE.index("    def collect_due_task_reminders(")
        block = DATABASE_SOURCE[start:DATABASE_SOURCE.index("    def mark_task_reminder_sent", start)]
        self.assertIn("JOIN task_assignees ta ON ta.task_id = d.id", block)
        self.assertIn("JOIN users u ON u.id = ta.user_id", block)
        # Отметка «отправлено» — у пары (задача, исполнитель): успех первого не
        # должен закрывать задачу для тех, кому сообщение не ушло.
        self.assertIn("ta.reminder_sent_at IS NULL", block)

    def test_bell_wakes_the_whole_crew_and_the_removed_one(self):
        start = DATABASE_SOURCE.index("    def _init_bell_notify_schema_tx(")
        block = DATABASE_SOURCE[start:DATABASE_SOURCE.index("    def _init_amo_leads_schema_tx", start)]
        self.assertIn("ARRAY(SELECT user_id FROM task_assignees WHERE task_id = NEW.id)", block)
        # Смена состава не трогает саму задачу — нужен свой триггер.
        self.assertIn("'trg_bell_task_assignees'", block)
        # При DELETE доступен только OLD; обращение к NEW уронило бы функцию молча.
        self.assertIn("IF TG_OP = 'DELETE' THEN\n                        targets := ARRAY[OLD.user_id];",
                      block)

    def test_bell_source_counts_participation(self):
        bell = _read(BELL_PATH)
        self.assertNotIn("t.assigned_to", bell)
        self.assertIn("FROM task_assignees ta", bell)

    def test_info_reason_is_addressed_by_the_asker_not_by_the_crew(self):
        """«Просят информацию» отсекает АВТОРА вопроса, а не всех исполнителей.

        Иначе постановщик, вписавший себя соисполнителем, не увидел бы вопрос
        коллеги — а отвечать больше некому.
        """
        for source, path in (
            (DATABASE_SOURCE, 'database.py'),
            (_read(BELL_PATH), 'notifications/sources.py'),
        ):
            self.assertIn("SELECT m.author_id FROM task_messages m", source, path)
        client = _read(ROOT / 'src' / 'components' / 'tasks' / 'taskActionNeeds.js')
        self.assertIn("Number(task.info_request.author_id || 0) !== personId", client)
        cli = _read(CLI_PATH)
        self.assertIn("(task.get('info_request') or {}).get('author_id')", cli)

    def test_reminder_limit_counts_tasks_not_crew_rows(self):
        """Отметка «отправлено» одна на задачу, значит порция обязана быть по задачам.

        Иначе у задачи на пятерых часть людей уходит за границу LIMIT, а отметка
        уже стоит — и своего напоминания они не получат никогда.
        """
        start = DATABASE_SOURCE.index("    def collect_due_task_reminders(")
        block = DATABASE_SOURCE[start:DATABASE_SOURCE.index("    def mark_task_reminder_sent", start)]
        with_pos = block.index("WITH due AS (")
        limit_pos = block.index("LIMIT %s", with_pos)
        join_pos = block.index("JOIN task_assignees ta ON ta.task_id = d.id")
        # Порядок и есть суть правила: сначала порция задач, потом разворот по людям.
        self.assertLess(limit_pos, join_pos)
        # Задача без единого адресата не должна занимать место в порции.
        self.assertIn("FROM task_assignees ta_any", block)

    def test_regulation_notifies_every_assignee(self):
        bot = _read(BOT_PATH)
        self.assertIn("for assignee_chat_id in assignee_chat_ids:", bot)
        start = bot.index("created_regulation_task_ids[:20]")
        block = bot[start:start + 1200]
        self.assertIn("task_ctx.get('assignees')", block)


class RouteContractTests(unittest.TestCase):
    """Проводной контракт: что именно принимают и отдают роуты."""

    BOT = _read(BOT_PATH)

    def test_post_accepts_the_crew_as_a_comma_string(self):
        # POST /api/tasks принимает только multipart, поэтому состав — строкой.
        self.assertIn("assignee_ids_raw = (request.form.get('assignee_ids') or '').strip()", self.BOT)
        self.assertIn("_parse_task_assignee_ids(assignee_ids_raw or assigned_to_raw)", self.BOT)

    def test_patch_accepts_the_crew_as_an_array(self):
        self.assertIn("has_assigned_to = 'assigned_to' in data or 'assignee_ids' in data", self.BOT)
        self.assertIn('edit_kwargs["assignee_ids"] = assignee_ids', self.BOT)

    def test_allowlist_is_checked_for_every_id_once(self):
        """Сверка «кому можно поручать» — одна функция, а не копия в двух роутах."""
        self.assertIn("def _task_assignees_outside_scope(", self.BOT)
        self.assertEqual(self.BOT.count("forbidden = _task_assignees_outside_scope("), 2)

    def test_avatars_are_signed_for_the_whole_crew(self):
        self.assertIn('assignees = task.get("assignees")', self.BOT)
        self.assertIn('person["avatar_url"] = _build_avatar_signed_url(', self.BOT)

    def test_ceiling_error_is_in_russian(self):
        self.assertIn("Исполнителей у задачи не больше", self.BOT)
        self.assertIn("Выберите хотя бы одного исполнителя", self.BOT)


class FrontendContractTests(unittest.TestCase):
    """Форма и карточка: один исполнитель выглядит как раньше."""

    VIEW = _read(TASKS_VIEW_PATH)

    def test_form_state_is_a_list(self):
        self.assertIn("assigneeIds: [],", self.VIEW)
        self.assertIn("assigneeIds: formAssigneeIds(task, fallbackAssignedTo),", self.VIEW)

    def test_payload_carries_both_the_first_and_the_crew(self):
        self.assertIn("assigned_to: Number((values.assigneeIds || [])[0] || 0),", self.VIEW)
        self.assertIn("assignee_ids: (values.assigneeIds || []).map(Number)", self.VIEW)
        self.assertIn("body.append('assignee_ids', payload.assignee_ids.join(','));", self.VIEW)

    def test_label_switches_to_plural_only_with_two_or_more(self):
        self.assertIn("{assigneeIds.length > 1 ? 'Исполнители' : 'Исполнитель'}", self.VIEW)

    def test_card_shows_a_stack_and_the_picker_is_shared(self):
        self.assertIn("<AssigneeStack people={assigneePeople} />", self.VIEW)
        self.assertIn("maxSelected={TASK_MAX_ASSIGNEES}", self.VIEW)

    def test_cli_understands_a_repeated_flag(self):
        cli = _read(CLI_PATH)
        self.assertIn("def _resolve_assignees(client, args):", cli)
        self.assertIn("'assignee_ids': ','.join(str(item) for item in ids),", cli)
        self.assertIn("action='append'", cli)


if __name__ == "__main__":
    unittest.main()
