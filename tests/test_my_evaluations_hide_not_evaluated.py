# -*- coding: utf-8 -*-
"""«Мои оценки»: неоценённые звонки сотруднику не показываются.

Решение владельца 12.11.2026: у операторов в «Моих оценках» не должно быть
неоценённых оценок. Неоценённый звонок (imported_calls, status='not_evaluated') —
очередь проверяющих: в «Журнале оценок» по нему ставят оценку, а у сотрудника он
висел строкой «Не оценено» без балла.

Стережём три места:
  1) метод базы: без неоценённых он не ходит в imported_calls вовсе, а по
     умолчанию отдаёт их как раньше — на этом стоят «Журнал оценок», отчёт
     «По группе» и ИИ-разбор месяца;
  2) ручка /api/call_evaluations выключает их только для СВОИХ оценок: глава
     отдела и наблюдатель «Маркетинга» с ролью operator смотрят ею чужие
     журналы, и там неоценённые нужны, — поэтому условие не по роли;
  3) кнопка бота «Мои оценки📝» — так же, как раздел портала.

Функции гоняются вырезанными из модулей (импорт монолита и db.core в тестах
невозможен: пул к боевой БД, time.tzset на Windows), поэтому их глобальные имена
и всё, что тест подкладывает, сверяются с самими модулями: подложенное тестом
имя не должно прятать NameError.
"""

import ast
import asyncio
import builtins
import inspect
import logging
import symtable
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
CALLS_PATH = ROOT / "db" / "calls.py"

OPERATOR_ID = 7
SUPERVISOR_ID = 3
HEAD_WITH_OPERATOR_ROLE_ID = 9


def _load(path, name, namespace, class_name=None):
    node = source_cache.function_copy(path, name, class_name=class_name)
    node.decorator_list = []
    namespace = dict(namespace)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def _module_bound_names(path):
    table = symtable.symtable(source_cache.read(path), str(path), "exec")
    bound = {
        name for name in table.get_identifiers()
        if table.lookup(name).is_assigned() or table.lookup(name).is_imported()
    }
    return table, bound | set(dir(builtins))


def _function_tables(table, name, class_name=None):
    scope = table
    if class_name is not None:
        scope = next(child for child in table.get_children()
                     if isinstance(child, symtable.Class) and child.get_name() == class_name)
    return [child for child in scope.get_children()
            if isinstance(child, symtable.Function) and child.get_name() == name]


def _unbound_globals(function_table, bound):
    missing = {name for name in function_table.get_globals() if name not in bound}
    for child in function_table.get_children():
        missing |= _unbound_globals(child, bound)
    return missing


def _real_method():
    """Сам метод базы — по его подписи сверяются вызовы ручки и бота."""
    return _load(CALLS_PATH, "get_call_evaluations", {"datetime": datetime}, class_name="CallsMixin")


def _user(user_id, role, supervisor_id=None):
    # Строка users в том порядке, в каком её читает ручка: [0] id, [2] имя,
    # [3] роль, [6] супервайзер.
    return (user_id, None, f"user {user_id}", role, None, None, supervisor_id)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Метод базы
# ─────────────────────────────────────────────────────────────────────────────

class _Cursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, list(params or ())))

    def fetchall(self):
        return []


class _CallsDb:
    def __init__(self):
        self.cursor = _Cursor()

    @contextmanager
    def _get_cursor(self):
        yield self.cursor


class DatabaseMethodTests(unittest.TestCase):
    def setUp(self):
        self.method = _real_method()
        self.db = _CallsDb()

    def _run(self, **kwargs):
        self.assertEqual(self.method(self.db, OPERATOR_ID, **kwargs), [])
        self.assertEqual(len(self.db.cursor.executed), 1)
        sql, params = self.db.cursor.executed[0]
        # Каждому %s — свой параметр: сдвиг параметров при снятой половине
        # запроса был бы главной ошибкой этой правки.
        self.assertEqual(sql.count("%s"), len(params))
        self.assertTrue(sql.rstrip().endswith("ORDER BY created_at DESC"))
        return sql, params

    def test_own_list_does_not_read_the_queue_at_all(self):
        sql, params = self._run(month="2026-11", include_not_evaluated=False)
        self.assertNotIn("imported_calls", sql)
        self.assertNotIn("UNION", sql)
        self.assertEqual(params, [OPERATOR_ID, OPERATOR_ID, OPERATOR_ID, "2026-11"])

    def test_own_list_for_all_months(self):
        sql, params = self._run(include_not_evaluated=False)
        self.assertNotIn("imported_calls", sql)
        self.assertEqual(params, [OPERATOR_ID, OPERATOR_ID, OPERATOR_ID])

    def test_default_keeps_the_queue_for_reviewers(self):
        sql, params = self._run(month="2026-11")
        self.assertIn("UNION ALL", sql)
        self.assertIn("FROM imported_calls ic", sql)
        self.assertIn("ic.status = 'not_evaluated'", sql)
        self.assertIn("AND ic.month = %s", sql)
        self.assertEqual(
            params,
            [OPERATOR_ID, OPERATOR_ID, OPERATOR_ID, "2026-11", OPERATOR_ID, "2026-11"],
        )

    def test_default_without_month_keeps_the_queue_too(self):
        sql, params = self._run()
        self.assertIn("FROM imported_calls ic", sql)
        self.assertNotIn("ic.month = %s", sql)
        self.assertEqual(params, [OPERATOR_ID, OPERATOR_ID, OPERATOR_ID, OPERATOR_ID])


# ─────────────────────────────────────────────────────────────────────────────
# 2. Ручка /api/call_evaluations
# ─────────────────────────────────────────────────────────────────────────────

class _RouteDb:
    def __init__(self):
        self.users = {
            OPERATOR_ID: _user(OPERATOR_ID, "operator", supervisor_id=SUPERVISOR_ID),
            SUPERVISOR_ID: _user(SUPERVISOR_ID, "sv"),
            HEAD_WITH_OPERATOR_ROLE_ID: _user(HEAD_WITH_OPERATOR_ROLE_ID, "operator"),
        }
        self.calls = []

    def get_user(self, id=None, **_):
        return self.users.get(id)

    def get_call_evaluations(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"evaluations": [], "evaluation_target": None}


def _route_namespace(db, requester_id, operator_id):
    return {
        "request": SimpleNamespace(args={"operator_id": str(operator_id), "month": "2026-11"}),
        "g": SimpleNamespace(user_id=requester_id),
        "db": db,
        "jsonify": lambda payload: payload,
        "_authorize_operator_scope": lambda requester, requester_id, operator_id: True,
        "_current_session_id_from_access_token": lambda: None,
        "_is_sensitive_access_unlocked": lambda user_id, session_id: False,
        "_sanitize_evaluations_for_access": (
            lambda evaluations, reveal_sensitive, hide_hidden_operator_comments=False: evaluations),
        "_attach_checkpoints_to_evaluations": lambda evaluations, requester_id, requester: evaluations,
        "logging": logging,
    }


class RouteTests(unittest.TestCase):
    def _request(self, requester_id, operator_id=OPERATOR_ID):
        db = _RouteDb()
        route = _load(BOT_PATH, "get_call_evaluations", _route_namespace(db, requester_id, operator_id))
        response = route()
        # Ручка ловит любое исключение и отвечает 500 — успех проверяется явно.
        self.assertIsInstance(response, dict, response)
        self.assertEqual(response.get("status"), "success")
        self.assertEqual(len(db.calls), 1)
        args, kwargs = db.calls[0]
        # Вызов обязан ложиться на настоящую подпись метода базы.
        bound = inspect.signature(_real_method()).bind(None, *args, **kwargs)
        bound.apply_defaults()
        self.assertEqual(bound.arguments["operator_id"], operator_id)
        self.assertEqual(bound.arguments["month"], "2026-11")
        self.assertTrue(bound.arguments["include_target"])
        return bound.arguments["include_not_evaluated"]

    def test_own_evaluations_come_without_the_queue(self):
        self.assertFalse(self._request(OPERATOR_ID))

    def test_supervisor_journal_keeps_the_queue(self):
        self.assertTrue(self._request(SUPERVISOR_ID))

    def test_head_with_operator_role_keeps_the_queue_in_a_foreign_journal(self):
        self.assertTrue(self._request(HEAD_WITH_OPERATOR_ROLE_ID))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Кнопка бота «Мои оценки📝»
# ─────────────────────────────────────────────────────────────────────────────

class _BotDb:
    def __init__(self):
        self.calls = []

    def get_user(self, telegram_id=None, **_):
        return _user(OPERATOR_ID, "operator") if telegram_id == 555 else None

    def get_call_evaluations(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return [{
            "id": "15", "month": "2026-11", "phone_number": "77011234567",
            "phone_number_masked": "7701***4567", "score": 95.0,
            "evaluation_date": "2026-11-10 12:00", "comment": "", "is_correction": False,
        }]


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append(text)


class _Message:
    def __init__(self):
        self.from_user = SimpleNamespace(id=555)
        self.deleted = False

    async def delete(self):
        self.deleted = True


def _bot_namespace(db, bot):
    return {
        "db": db,
        "bot": bot,
        "datetime": datetime,
        "_mask_phone_number": lambda phone: phone,
        "types": SimpleNamespace(Message=object),
    }


class BotButtonTests(unittest.TestCase):
    def test_bot_lists_only_evaluated_calls(self):
        db, bot, message = _BotDb(), _Bot(), _Message()
        handler = _load(BOT_PATH, "show_operator_evaluations", _bot_namespace(db, bot))
        asyncio.run(handler(message))
        self.assertEqual(len(db.calls), 1)
        args, kwargs = db.calls[0]
        bound = inspect.signature(_real_method()).bind(None, *args, **kwargs)
        bound.apply_defaults()
        self.assertEqual(bound.arguments["operator_id"], OPERATOR_ID)
        self.assertFalse(bound.arguments["include_not_evaluated"])
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("Оценка: <b>95.0</b>", bot.sent[0])
        self.assertTrue(message.deleted)


# ─────────────────────────────────────────────────────────────────────────────
# Имена вырезанных функций — из самих модулей
# ─────────────────────────────────────────────────────────────────────────────

class ModuleNamesTests(unittest.TestCase):
    def test_route_and_bot_button_use_only_names_of_the_monolith(self):
        table, bound = _module_bound_names(BOT_PATH)
        for name, namespace in (
            ("get_call_evaluations", _route_namespace(_RouteDb(), OPERATOR_ID, OPERATOR_ID)),
            ("show_operator_evaluations", _bot_namespace(_BotDb(), _Bot())),
        ):
            with self.subTest(function=name):
                tables = _function_tables(table, name)
                self.assertEqual(len(tables), 1, name)
                self.assertEqual(_unbound_globals(tables[0], bound), set())
                self.assertLessEqual(set(namespace), bound)

    def test_database_method_uses_only_names_of_its_module(self):
        table, bound = _module_bound_names(CALLS_PATH)
        tables = _function_tables(table, "get_call_evaluations", class_name="CallsMixin")
        self.assertEqual(len(tables), 1)
        self.assertEqual(_unbound_globals(tables[0], bound), set())
        self.assertIn("datetime", bound)


if __name__ == "__main__":
    unittest.main()
