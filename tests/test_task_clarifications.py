"""Уточнения по задаче: поведение create_task_message / withdraw_task_info_request.

Проверяем не текст исходников, а сами правила — на поддельном курсоре, без БД
(конструктор Database лезет в боевую базу, поэтому метод достаём из файла AST'ом,
как это уже сделано для save_directions).
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

# Кто есть кто в сценариях ниже.
OWNER = 10        # постановщик (created_by)
ASSIGNEE = 20     # исполнитель
CO_ASSIGNEE = 21  # второй исполнитель: права у них равные
BOSS = 30         # поручитель (requested_by_id) — он же принимает итог
STRANGER = 40


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _database_members(names):
    """Исходники методов класса Database по именам."""
    source = _read(DATABASE_PATH)
    module = source_cache.parse(source)
    database_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    lines = source.splitlines(keepends=True)
    found = {}
    for node in database_class.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            # get_source_segment начинает с `def`, а декоратор (@staticmethod)
            # остаётся выше — без него метод теряет свою природу.
            first = min([node.lineno] + [item.lineno for item in node.decorator_list])
            found[node.name] = textwrap.dedent(''.join(lines[first - 1:node.end_lineno]))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    found[target.id] = textwrap.dedent(ast.get_source_segment(source, node))
    missing = set(names) - set(found)
    assert not missing, f"в Database нет: {sorted(missing)}"
    return found


def _module_members(names):
    source = _read(DATABASE_PATH)
    module = source_cache.parse(source)
    found = {}
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found[node.name] = ast.get_source_segment(source, node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    found[target.id] = ast.get_source_segment(source, node)
    missing = set(names) - set(found)
    assert not missing, f"в database.py нет: {sorted(missing)}"
    return found


class _CursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _FakeCursor:
    """Отвечает на запросы ленты по содержимому SQL, остальное молча глотает."""

    NEW_MESSAGE_ID = 777

    def __init__(self, task_row, message_row=None, assignee_ids=None):
        self.task_row = task_row
        self.message_row = message_row
        # Состав исполнителей: по умолчанию один — тот, что в строке задачи.
        self.assignee_ids = (
            list(assignee_ids) if assignee_ids is not None
            else ([task_row[2]] if task_row[2] else [])
        )
        self.executions = []
        self._next_one = None
        self._next_all = []

    def execute(self, query, params=None):
        flat = " ".join(query.split())
        self.executions.append((flat, params))
        if "FROM task_assignees ta" in flat:
            self._next_all = [(person_id, "operator", None) for person_id in self.assignee_ids]
        elif "FROM tasks t" in flat and "info_request_id" in flat and flat.startswith("SELECT"):
            self._next_one = self.task_row
        elif "INSERT INTO task_messages" in flat:
            self._next_one = (self.NEW_MESSAGE_ID,)
        elif "FROM task_messages m" in flat:
            self._next_one = (
                self.NEW_MESSAGE_ID, self.task_row[0], self.task_row[2], "Кто-то",
                "request", "текст", None, None, None, None, "2026-08-20T10:00:00",
            )
        elif "FROM task_messages WHERE id" in flat:
            self._next_one = self.message_row
        elif "FROM task_attachments a" in flat:
            self._next_all = []
        else:
            self._next_one = None

    def fetchone(self):
        return self._next_one

    def fetchall(self):
        return self._next_all

    def ran(self, needle):
        return [item for item in self.executions if needle in item[0]]


def _build_database():
    names = [
        "create_task_message",
        "withdraw_task_info_request",
        "_normalize_task_message_kind",
        "_task_message_access_tx",
        "_task_message_attachments_tx",
        "_task_answer_authority",
        "_serialize_task_message",
        "_task_visible_for_requester",
        "_task_assignee_tuples",
        "_task_assignee_scope_tx",
        "TASK_MESSAGE_KINDS",
        "_TASK_MESSAGE_SELECT",
    ]
    namespace = {
        "defaultdict": defaultdict,
        "Optional": Optional,
        "List": List,
    }
    for source in _module_members(
        ["normalize_role_value", "role_has_min", "ROLE_ALIASES", "ROLE_HIERARCHY"]
    ).values():
        exec(source, namespace)

    body = _database_members(names)
    class_source = "class FakeDatabase:\n" + "".join(
        textwrap.indent(body[name], "    ") + "\n" for name in names
    )
    exec(class_source, namespace)
    return namespace["FakeDatabase"]


FakeDatabase = _build_database()


def _make_db(cursor):
    class Bound(FakeDatabase):
        def _task_now(self):
            return "2026-08-20 10:00:00"

        def _task_dt_to_iso(self, value):
            return value

        def _get_cursor(self):
            return _CursorContext(cursor)

    return Bound()


def _task_row(status="in_progress", requested_by=None, info_request_id=None,
              created_by=OWNER, assigned_to=ASSIGNEE):
    # (id, created_by, assigned_to, requested_by_id, status, info_request_id)
    # Роль и СВ исполнителя больше не джойнятся сюда: состав читается отдельным
    # запросом к task_assignees, потому что исполнителей может быть несколько.
    return (412, created_by, assigned_to, requested_by, status, info_request_id)


class AskForInformationTests(unittest.TestCase):
    """Кнопка «Не хватает информации» — только у исполнителя и только один раз."""

    def _ask(self, task_row, requester_id=ASSIGNEE, role="operator", body="нет доступа"):
        cursor = _FakeCursor(task_row)
        _make_db(cursor).create_task_message(
            task_id=412, requester_id=requester_id, requester_role=role,
            kind="request", body=body,
        )
        return cursor

    def test_assignee_may_ask_and_the_task_remembers_the_open_request(self):
        cursor = self._ask(_task_row())
        inserted = cursor.ran("INSERT INTO task_messages")
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0][1][2], "request")
        # Метка в задаче — на ней держатся бейдж, колокол и чип карточки.
        marked = cursor.ran("SET info_request_id = %s")
        self.assertEqual(len(marked), 1)
        self.assertEqual(marked[0][1][0], _FakeCursor.NEW_MESSAGE_ID)
        self.assertIsNotNone(marked[0][1][1])

    def test_owner_cannot_ask_himself(self):
        with self.assertRaises(PermissionError) as ctx:
            self._ask(_task_row(), requester_id=OWNER)
        self.assertEqual(str(ctx.exception), "ONLY_ASSIGNEE_ASKS")

    def test_admin_cannot_ask_for_the_assignee(self):
        # Право админа — вести задачу, а не говорить за исполнителя.
        with self.assertRaises(PermissionError):
            self._ask(_task_row(), requester_id=STRANGER, role="admin")

    def test_second_request_is_refused_until_the_first_is_closed(self):
        with self.assertRaises(ValueError) as ctx:
            self._ask(_task_row(info_request_id=555))
        self.assertEqual(str(ctx.exception), "REQUEST_ALREADY_OPEN")

    def test_handed_over_task_is_too_late_to_ask(self):
        # Границы те же, что у причины «ждут вас»: сданная и принятая задачи
        # запрос не принимают, иначе уведомление ушло бы, а бейдж молчал.
        for status in ("completed", "accepted"):
            with self.subTest(status=status):
                with self.assertRaises(ValueError) as ctx:
                    self._ask(_task_row(status=status))
                self.assertEqual(str(ctx.exception), "TASK_CLOSED_FOR_REQUEST")

    def test_own_initiative_has_no_one_to_ask(self):
        # Задача себе без поручителя: спрашивать не у кого, иначе запрос повис бы.
        with self.assertRaises(ValueError) as ctx:
            self._ask(_task_row(created_by=ASSIGNEE))
        self.assertEqual(str(ctx.exception), "NO_ONE_TO_ASK")

    def test_own_task_with_a_requester_still_has_someone_to_ask(self):
        cursor = self._ask(_task_row(created_by=ASSIGNEE, requested_by=BOSS))
        self.assertEqual(len(cursor.ran("INSERT INTO task_messages")), 1)

    def test_text_or_file_is_required(self):
        with self.assertRaises(ValueError) as ctx:
            self._ask(_task_row(), body="   ")
        self.assertEqual(str(ctx.exception), "MESSAGE_BODY_REQUIRED")

    def test_a_file_alone_is_a_valid_clarification(self):
        # Обычный случай постановки: ТЗ прислали файлом, без слов.
        cursor = _FakeCursor(_task_row())
        _make_db(cursor).create_task_message(
            task_id=412, requester_id=OWNER, requester_role="operator",
            kind="note", body="",
            attachments=[{
                "file_name": "доп.docx", "content_type": "application/msword",
                "file_size": 10, "storage_type": "gcs",
                "gcs_bucket": "bucket", "gcs_blob_path": "path/доп.docx",
            }],
        )
        attached = cursor.ran("INSERT INTO task_attachments")
        self.assertEqual(len(attached), 1)
        # Вид вложения не меняли — файл виден выгрузке, CLI и «Файлам задачи»,
        # а message_id говорит карточке показать его внутри уточнения.
        self.assertIn("'initial'", attached[0][0])
        self.assertEqual(attached[0][1][-1], _FakeCursor.NEW_MESSAGE_ID)


class SupplementAndAnswerTests(unittest.TestCase):
    """Постановщик дополняет задачу всегда, отвечает — пока запрос открыт."""

    def _write(self, task_row, kind, requester_id, role="operator"):
        cursor = _FakeCursor(task_row)
        _make_db(cursor).create_task_message(
            task_id=412, requester_id=requester_id, requester_role=role,
            kind=kind, body="вот вводные",
        )
        return cursor

    def test_owner_may_supplement_in_every_status(self):
        for status in ("assigned", "in_progress", "returned", "completed", "accepted"):
            with self.subTest(status=status):
                cursor = self._write(_task_row(status=status), "note", OWNER)
                self.assertEqual(len(cursor.ran("INSERT INTO task_messages")), 1)

    def test_requester_and_admin_may_supplement_too(self):
        cursor = self._write(_task_row(requested_by=BOSS), "note", BOSS)
        self.assertEqual(len(cursor.ran("INSERT INTO task_messages")), 1)
        cursor = self._write(_task_row(), "note", STRANGER, role="admin")
        self.assertEqual(len(cursor.ran("INSERT INTO task_messages")), 1)

    def test_assignee_writes_reports_not_supplements(self):
        with self.assertRaises(PermissionError) as ctx:
            self._write(_task_row(), "note", ASSIGNEE)
        self.assertEqual(str(ctx.exception), "ONLY_TASK_OWNER_ADDS")

    def test_supplement_neither_opens_nor_closes_a_request(self):
        cursor = self._write(_task_row(), "note", OWNER)
        self.assertEqual(len(cursor.ran("SET info_request_id")), 0)
        # Только updated_at: он гасит отметки «просмотрено» и будит колокол.
        self.assertEqual(len(cursor.ran("SET updated_at = %s")), 1)

    def test_supplement_does_not_swallow_an_open_question(self):
        # Постановщик дописал не про то, о чём спрашивали, — вопрос обязан
        # остаться открытым, иначе он молча исчезает из «ждут вас».
        cursor = self._write(_task_row(info_request_id=555), "note", OWNER)
        self.assertEqual(len(cursor.ran("SET info_request_id")), 0)
        self.assertEqual(len(cursor.ran("SET resolved_at")), 0)

    def test_answer_closes_the_open_request(self):
        cursor = self._write(_task_row(info_request_id=555), "answer", OWNER)
        closed = cursor.ran("SET resolved_at = %s, resolved_by = %s")
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0][1][2], 555)
        # Ответ помнит, на что отвечал.
        self.assertEqual(cursor.ran("INSERT INTO task_messages")[0][1][4], 555)
        # И метка снимается — постановщика перестаёт дёргать бейдж.
        self.assertEqual(len(cursor.ran("SET info_request_id = NULL")), 1)

    def test_answer_without_a_request_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self._write(_task_row(), "answer", OWNER)
        self.assertEqual(str(ctx.exception), "NO_OPEN_REQUEST")

    def test_assignee_cannot_answer_his_own_question(self):
        with self.assertRaises(PermissionError) as ctx:
            self._write(_task_row(info_request_id=555), "answer", ASSIGNEE)
        self.assertEqual(str(ctx.exception), "ONLY_TASK_OWNER_ANSWERS")

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self._write(_task_row(), "gossip", OWNER)
        self.assertEqual(str(ctx.exception), "INVALID_MESSAGE_KIND")

    def test_stranger_sees_nothing(self):
        with self.assertRaises(PermissionError) as ctx:
            self._write(_task_row(), "note", STRANGER)
        self.assertEqual(str(ctx.exception), "TASK_FORBIDDEN")


class WithdrawRequestTests(unittest.TestCase):
    """Снять запрос может только тот, кто его отправил."""

    def _withdraw(self, message_row, requester_id, role="operator", task_row=None):
        cursor = _FakeCursor(task_row or _task_row(info_request_id=555), message_row)
        result = _make_db(cursor).withdraw_task_info_request(555, requester_id, role)
        return cursor, result

    def test_author_withdraws_and_the_marker_clears(self):
        cursor, result = self._withdraw((555, 412, ASSIGNEE, "request", None), ASSIGNEE)
        self.assertTrue(result["withdrawn"])
        self.assertEqual(len(cursor.ran("SET resolved_at = %s, resolved_by = %s")), 1)
        cleared = cursor.ran("SET info_request_id = NULL")
        self.assertEqual(len(cleared), 1)
        # Снимаем только свою метку: чужой запрос, успевший её занять, не трогаем.
        self.assertEqual(cleared[0][1][2], 555)

    def test_owner_answers_instead_of_withdrawing(self):
        with self.assertRaises(PermissionError) as ctx:
            self._withdraw((555, 412, ASSIGNEE, "request", None), OWNER)
        self.assertEqual(str(ctx.exception), "ONLY_REQUEST_AUTHOR")

    def test_admin_cannot_withdraw_someone_elses_request(self):
        with self.assertRaises(PermissionError):
            self._withdraw((555, 412, ASSIGNEE, "request", None), STRANGER, role="admin")

    def test_only_requests_can_be_withdrawn(self):
        with self.assertRaises(ValueError) as ctx:
            self._withdraw((555, 412, OWNER, "note", None), OWNER)
        self.assertEqual(str(ctx.exception), "NOT_A_REQUEST")

    def test_missing_message(self):
        with self.assertRaises(ValueError) as ctx:
            self._withdraw(None, ASSIGNEE)
        self.assertEqual(str(ctx.exception), "MESSAGE_NOT_FOUND")

    def test_already_closed_request_still_clears_the_marker(self):
        # Идемпотентность: повторное «снять» не должно падать и не пишет второй раз.
        cursor, _ = self._withdraw((555, 412, ASSIGNEE, "request", "2026-08-20"), ASSIGNEE)
        self.assertEqual(len(cursor.ran("SET resolved_at = %s, resolved_by = %s")), 0)
        self.assertEqual(len(cursor.ran("SET info_request_id = NULL")), 1)


class ConcurrencyTests(unittest.TestCase):
    def test_write_paths_lock_the_task_row(self):
        # Без блокировки двойной клик по «Не хватает информации» открывает два
        # запроса: оба читают info_request_id = NULL и оба проходят проверку.
        cursor = _FakeCursor(_task_row())
        _make_db(cursor).create_task_message(
            task_id=412, requester_id=ASSIGNEE, requester_role="operator",
            kind="request", body="нет доступа",
        )
        self.assertEqual(len(cursor.ran("FOR UPDATE OF t")), 1)


if __name__ == "__main__":
    unittest.main()
