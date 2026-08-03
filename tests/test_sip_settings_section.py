# -*- coding: utf-8 -*-
"""Раздел «Настройки SIP»: персональные аккаунты операторов + общий автодозвон.

Правила, которые здесь закреплены:
  * SIP-номер нормализуется (пробелы/скобки из копипаста режем, кириллицу не пускаем);
  * один номер на двоих запрещён — иначе звонки привязываются не к тому оператору;
  * пустые персональные поля означают «как у всех»: домен общий, пароль «база + номер»;
  * строка персональных настроек не остаётся пустышкой, а история пишется
    только когда что-то действительно поменялось;
  * доступ к разделу — тот же гейт, что был у панели (админ / глава отдела / СВ ОП),
    со скоупом по отделу.
"""

import ast
import json
import re
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
APP_PATH = ROOT / "src" / "App.jsx"
VIEW_PATH = ROOT / "src" / "components" / "sip" / "SipSettingsView.jsx"

DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
DATABASE_MODULE = ast.parse(DATABASE_SOURCE)
DATABASE_CLASS = next(
    node for node in DATABASE_MODULE.body
    if isinstance(node, ast.ClassDef) and node.name == "Database"
)


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _source_of(node, source):
    text = textwrap.dedent(ast.get_source_segment(source, node))
    # Декораторы (@staticmethod) мешают вызывать метод как обычную функцию.
    return "\n".join(line for line in text.splitlines() if not line.startswith("@"))


def _fake_execute_values(cursor, sql, argslist, template=None):
    cursor.calls.append((" ".join(str(sql).split()), list(argslist), template))


def _database_namespace(method_names):
    """Исполняет методы Database без импорта модуля (он поднимает пул к БД)."""
    ns = {"json": json, "re": re, "Optional": None, "execute_values": _fake_execute_values}
    for node in DATABASE_MODULE.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SIP_IDENTIFIER_RE" for t in node.targets
        ):
            exec(_source_of(node, DATABASE_SOURCE), ns)
        if isinstance(node, ast.FunctionDef) and node.name == "normalize_sip_identifier":
            exec(_source_of(node, DATABASE_SOURCE), ns)
    for node in DATABASE_CLASS.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id.startswith("_SIP_") for t in node.targets
        ):
            exec(_source_of(node, DATABASE_SOURCE), ns)
        if isinstance(node, ast.FunctionDef) and node.name in method_names:
            exec(_source_of(node, DATABASE_SOURCE), ns)
    return ns


class _FakeCursor:
    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows or []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(str(sql).split()), params))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _StubDb:
    """Достаточный кусок Database, чтобы гонять методы без базы."""

    def __init__(self, ns, cursor=None):
        self._ns = ns
        self.cursor = cursor or _FakeCursor()
        for name, value in ns.items():
            if name.startswith("_SIP_"):
                setattr(self, name, value)

    def _get_cursor(self):
        return self.cursor

    def _mask_sip_secret(self, value):
        return self._ns["_mask_sip_secret"](value)

    def _sip_operator_row(self, row):
        return self._ns["_sip_operator_row"](row)

    def call(self, name, *args, **kwargs):
        return self._ns[name](self, *args, **kwargs)


OPERATOR_STATE = {
    "id": 41, "name": "Иван", "role": "operator", "status": "working",
    "department_id": 367, "department_name": "Отдел продаж", "supervisor_id": 9,
    "group_name": "Группа 1",
    "sip_number": "1024", "sip_password": "", "sip_domain": "",
    "autodial_number": "", "autodial_password": "", "autodial_domain": "",
    "updated_at": None, "updated_by_name": None,
}


class NormalizeSipIdentifierTests(unittest.TestCase):
    def setUp(self):
        self.normalize = _database_namespace(set())["normalize_sip_identifier"]

    def test_empty_values_become_empty_string(self):
        self.assertEqual("", self.normalize(None))
        self.assertEqual("", self.normalize("   "))

    def test_copy_paste_noise_is_cleaned(self):
        self.assertEqual("1024", self.normalize(" 10 24 "))
        self.assertEqual("+7700123", self.normalize("+7 (700) 123"))

    def test_service_codes_are_allowed(self):
        self.assertEqual("*55", self.normalize("*55", field="Код автодозвона"))
        self.assertEqual("#1_a.b-c", self.normalize("#1_a.b-c"))

    def test_cyrillic_and_too_long_are_rejected(self):
        with self.assertRaises(ValueError):
            self.normalize("номер")
        with self.assertRaises(ValueError):
            self.normalize("1" * 65)

    def test_error_names_the_field(self):
        with self.assertRaises(ValueError) as ctx:
            self.normalize("да", field="Код автодозвона")
        self.assertIn("Код автодозвона", str(ctx.exception))


class SaveUserSipSettingsTests(unittest.TestCase):
    def setUp(self):
        self.ns = _database_namespace({
            "save_user_sip_settings", "_mask_sip_secret", "_sip_operator_row",
            "normalize_sip_domain",
        })
        self.db = _StubDb(self.ns)
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        self.current = dict(OPERATOR_STATE)
        self.owners = {}
        self.checked = []
        self.db.get_sip_operator = lambda user_id: dict(self.current)
        self.db.get_sip_config = lambda: {"sip_server": "SIP.Local", "base_password": "pwd"}

        def _find(entries, exclude_user_ids=None):
            self.checked = list(entries)
            self.excluded = exclude_user_ids
            return self.owners
        self.db.find_sip_number_owners = _find

    def _save(self, payload):
        return self.ns["save_user_sip_settings"](self.db, 41, payload, changed_by=7)

    def _sql(self):
        return [sql for sql, _ in self.db.cursor.calls]

    def test_duplicate_number_is_refused_with_owner_and_domain(self):
        self.owners = {("1088", "sip.local"): {"user_id": 5, "name": "Пётр", "kind": "main"}}
        with self.assertRaises(ValueError) as ctx:
            self._save({"sip_number": "1088"})
        self.assertIn("Пётр", str(ctx.exception))
        self.assertIn("sip.local", str(ctx.exception))
        self.assertEqual([], self.db.cursor.calls)

    def test_occupancy_is_checked_per_domain_not_globally(self):
        """Номер уникален в пределах домена: сверяем пару «номер + домен»."""
        self._save({"sip_number": "1088"})
        self.assertEqual([("1088", "sip.local")], self.checked)   # общий домен, нижний регистр
        self.assertEqual([41], self.excluded)

    def test_same_number_on_another_domain_is_rechecked(self):
        """Номер тот же, а домен новый — на новой АТС он может быть занят."""
        self._save({"sip_domain": "PBX.Other"})
        self.assertEqual([("1024", "pbx.other")], self.checked)

    def test_moving_to_another_domain_does_not_flag_the_old_pair(self):
        self.current.update({"sip_domain": "pbx.other"})
        self._save({"sip_password": "x"})
        self.assertEqual([], self.checked)   # пара не менялась — проверять нечего

    def test_autodial_may_repeat_the_main_number_on_another_domain(self):
        with self.assertRaises(ValueError):
            self._save({"autodial_number": "1024"})
        self._save({"autodial_number": "1024", "autodial_domain": "pbx.other"})
        self.assertTrue(any("INSERT INTO user_sip_settings" in s for s in self._sql()))

    def test_unchanged_payload_writes_nothing(self):
        result = self._save(dict(self.current))
        self.assertEqual([], self.db.cursor.calls)
        self.assertEqual(self.current["sip_number"], result["sip_number"])

    def test_number_change_updates_users_profile_and_history(self):
        self._save({"sip_number": "1088"})
        sql = self._sql()
        self.assertTrue(any("UPDATE users SET sip_number" in s for s in sql))
        self.assertTrue(any("UPDATE operator_profiles SET sip_number" in s for s in sql))
        self.assertTrue(any("INSERT INTO user_history" in s for s in sql))

    def test_personal_params_are_upserted_and_number_kept_in_users(self):
        self._save({"sip_password": "s3cret", "autodial_number": "2024"})
        sql = self._sql()
        upsert = next(s for s in sql if "INSERT INTO user_sip_settings" in s)
        self.assertIn("ON CONFLICT (user_id) DO UPDATE SET", upsert)
        self.assertFalse(any("DELETE FROM user_sip_settings" in s for s in sql))
        # Основной номер не переезжает в user_sip_settings — он остаётся в users.
        self.assertNotIn("sip_number", upsert.split("VALUES", 1)[0])

    def test_clearing_every_override_removes_the_row(self):
        self.current.update({"sip_password": "s3cret", "autodial_number": "2024"})
        self._save({"sip_password": "", "autodial_number": ""})
        self.assertTrue(any("DELETE FROM user_sip_settings" in s for s in self._sql()))

    def test_history_row_is_scoped_to_the_operator_and_masks_password(self):
        self._save({"sip_password": "supersecret"})
        sql, params = next(
            (s, p) for s, p in self.db.cursor.calls if "INSERT INTO sip_config_history" in s
        )
        self.assertIn("target_user_id", sql)
        self.assertEqual(7, params[0])
        self.assertEqual(41, params[1])
        snapshot = json.loads(params[2])
        self.assertNotIn("supersecret", params[2])
        self.assertTrue(snapshot["sip_password"].endswith("et"))


class BulkUpdateSipOverridesTests(unittest.TestCase):
    """Массовое проставление пароля/домена выбранным (Ctrl-выбор в списке)."""

    def setUp(self):
        self.ns = _database_namespace({
            "bulk_update_user_sip_overrides", "_mask_sip_secret", "normalize_sip_domain",
        })
        # (id, name, sip_password, sip_domain, autodial_number, autodial_password,
        #  autodial_domain, sip_number)
        rows = [
            (1, 'Иван', '', '', '2024', '', '', '1024'),        # автодозвон есть, персональных нет
            (2, 'Пётр', 'own', 'pbx.old', '', '', '', '1088'),  # были персональные значения
            (3, 'Мария', '', 'pbx.new', '', '', '', '1099'),    # уже с нужным доменом — не трогаем
        ]
        self.db = _StubDb(self.ns, _FakeCursor(rows))
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        self.db.get_sip_config = lambda: {"sip_server": "sip.local"}
        self.db.get_sip_operators_by_ids = lambda ids: [{"id": i} for i in ids]
        self.owners = {}
        self.checked = []

        def _find(entries, exclude_user_ids=None):
            self.checked = list(entries)
            self.excluded = exclude_user_ids
            return self.owners
        self.db.find_sip_number_owners = _find

    def _bulk(self, payload, ids=(1, 2, 3)):
        return self.ns["bulk_update_user_sip_overrides"](self.db, list(ids), payload, changed_by=7)

    def _batched(self, marker):
        return next((c for c in self.db.cursor.calls if marker in c[0]), None)

    def test_empty_selection_or_fields_are_refused(self):
        with self.assertRaises(ValueError):
            self._bulk({"sip_domain": "pbx.new"}, ids=())
        with self.assertRaises(ValueError):
            self._bulk({"sip_number": "1024"})

    def test_only_listed_fields_change_and_numbers_survive(self):
        self._bulk({"sip_domain": "pbx.new"})
        _, values, _ = self._batched("INSERT INTO user_sip_settings")
        by_id = {row[0]: row for row in values}
        # Домен применён обоим, у кого он отличался; сотрудник 3 уже такой — пропущен.
        self.assertEqual({1, 2}, set(by_id))
        self.assertEqual("pbx.new", by_id[1][2])
        self.assertEqual("2024", by_id[1][3])   # номер автодозвона не тронут
        self.assertEqual("own", by_id[2][1])    # персональный пароль не тронут

    def test_domain_change_rechecks_numbers_on_the_new_pbx(self):
        """Переезд на другой домен может столкнуть номера с уже занятыми там."""
        self._bulk({"sip_domain": "pbx.new"})
        self.assertIn(("1024", "pbx.new"), self.checked)
        self.assertIn(("2024", "sip.local"), self.checked)   # автодозвон остался на общем
        self.assertEqual([1, 2, 3], self.excluded)           # своих из проверки исключаем

    def test_conflict_on_the_target_domain_blocks_the_whole_batch(self):
        self.owners = {("1024", "pbx.new"): {"user_id": 9, "name": "Чужой", "kind": "main"}}
        with self.assertRaises(ValueError) as ctx:
            self._bulk({"sip_domain": "pbx.new"})
        self.assertIn("Чужой", str(ctx.exception))
        self.assertIn("pbx.new", str(ctx.exception))
        self.assertEqual([], [c for c in self.db.cursor.calls if "INSERT" in c[0]])

    def test_two_selected_landing_on_one_number_and_domain_are_refused(self):
        rows = [
            (1, 'Иван', '', '', '', '', '', '1024'),
            (2, 'Пётр', '', 'pbx.old', '', '', '', '1024'),   # тот же номер, но другая АТС
        ]
        self.db = _StubDb(self.ns, _FakeCursor(rows))
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        self.db.get_sip_config = lambda: {"sip_server": "sip.local"}
        self.db.find_sip_number_owners = lambda entries, exclude_user_ids=None: {}
        with self.assertRaises(ValueError) as ctx:
            self._bulk({"sip_domain": "pbx.new"}, ids=(1, 2))
        self.assertIn("достанется двоим", str(ctx.exception))
        self.assertIn("Иван", str(ctx.exception))
        self.assertIn("Пётр", str(ctx.exception))

    def test_everything_runs_in_batches_not_per_user(self):
        self._bulk({"sip_domain": "pbx.new"})
        inserts = [c for c in self.db.cursor.calls if "INSERT INTO user_sip_settings" in c[0]]
        history = [c for c in self.db.cursor.calls if "INSERT INTO sip_config_history" in c[0]]
        self.assertEqual(1, len(inserts))
        self.assertEqual(1, len(history))
        self.assertEqual(2, len(history[0][1]))

    def test_clearing_the_last_override_removes_the_row(self):
        self._bulk({"sip_password": "", "sip_domain": ""}, ids=(2,))
        deletes = [c for c in self.db.cursor.calls if "DELETE FROM user_sip_settings" in c[0]]
        self.assertEqual(1, len(deletes))
        self.assertEqual(([2],), deletes[0][1])

    def test_row_with_a_number_is_kept_even_with_empty_overrides(self):
        # У сотрудника 1 есть номер автодозвона — строку удалять нельзя.
        self._bulk({"sip_password": "", "sip_domain": ""}, ids=(1,))
        self.assertIsNone(self._batched("DELETE FROM user_sip_settings"))
        self.assertIsNone(self._batched("INSERT INTO user_sip_settings"))

    def test_history_is_scoped_per_operator_and_masks_the_password(self):
        self._bulk({"sip_password": "supersecret"}, ids=(1,))
        _, values, template = self._batched("INSERT INTO sip_config_history")
        changed_by, target_user_id, snapshot = values[0]
        self.assertEqual((7, 1), (changed_by, target_user_id))
        self.assertNotIn("supersecret", snapshot)
        self.assertTrue(json.loads(snapshot)["bulk"])
        self.assertIn("%s::jsonb", template)


class FindSipNumberOwnersTests(unittest.TestCase):
    """SQL занятости: сравниваем пару «номер + домен», пустой домен = общий."""

    def setUp(self):
        self.ns = _database_namespace({"find_sip_number_owners", "normalize_sip_domain"})
        self.db = _StubDb(self.ns, _FakeCursor([]))
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]

    def _call(self, entries, exclude=None):
        self.ns["find_sip_number_owners"](self.db, entries, exclude_user_ids=exclude)
        return self.db.cursor.calls[0]

    def test_nothing_to_check_makes_no_query(self):
        self.assertEqual({}, self.ns["find_sip_number_owners"](self.db, [('', 'x')]))
        self.assertEqual([], self.db.cursor.calls)

    def test_pairs_are_matched_on_number_and_domain(self):
        sql, params = self._call([('1024', 'PBX.Other'), ('2024', '')], exclude=[41])
        self.assertIn("JOIN taken t ON t.num = w.num AND t.dom = w.dom", sql)
        self.assertEqual(['1024', '2024'], params[0])
        self.assertEqual(['pbx.other', ''], params[1])   # регистр снят, пустое = общий
        self.assertEqual([41], params[2])

    def test_empty_domain_falls_back_to_the_common_server(self):
        sql, _ = self._call([('1024', '')])
        self.assertIn("SELECT LOWER(TRIM(COALESCE(sip_server, ''))) FROM sip_config WHERE id = 1", sql)
        self.assertIn("COALESCE(NULLIF(w.dom, ''), cfg.common)", sql)
        self.assertIn("COALESCE(NULLIF(LOWER(TRIM(COALESCE(s.sip_domain, ''))), ''), cfg.common)", sql)
        self.assertIn("COALESCE(NULLIF(LOWER(TRIM(COALESCE(s.autodial_domain, ''))), ''), cfg.common)", sql)

    def test_both_accounts_are_scanned(self):
        sql, _ = self._call([('1024', '')])
        self.assertIn("'main'::text AS kind", sql)
        self.assertIn("'autodial'::text", sql)


class UserSipAccountTests(unittest.TestCase):
    """Что уезжает в iCORE Phone: пароль «база + номер», домен общий, автодозвон опционален."""

    def setUp(self):
        self.ns = _database_namespace({"get_user_sip_account"})
        self.db = _StubDb(self.ns)
        self.config = {"sip_server": "sip.local", "base_password": "pwd", "autodial_code": "*55"}
        self.row = dict(OPERATOR_STATE)
        self.db.get_sip_config = lambda: dict(self.config)
        self.db.get_sip_operator = lambda user_id: dict(self.row)

    def _account(self):
        return self.ns["get_user_sip_account"](self.db, 41)

    def test_defaults_come_from_common_settings(self):
        main = self._account()["main"]
        self.assertEqual({"username": "1024", "password": "pwd1024",
                          "server": "sip.local", "transport": "UDP"}, main)

    def test_personal_password_and_domain_win(self):
        self.row.update({"sip_password": "own", "sip_domain": "pbx.other"})
        main = self._account()["main"]
        self.assertEqual("own", main["password"])
        self.assertEqual("pbx.other", main["server"])

    def test_autodial_is_absent_without_a_number(self):
        account = self._account()
        self.assertIsNone(account["autodial"])
        self.assertEqual("*55", account["autodial_code"])

    def test_autodial_account_is_built_like_the_main_one(self):
        self.row["autodial_number"] = "2024"
        autodial = self._account()["autodial"]
        self.assertEqual("2024", autodial["username"])
        self.assertEqual("pwd2024", autodial["password"])
        self.assertEqual("sip.local", autodial["server"])

    def test_missing_number_gives_no_account_at_all(self):
        self.row["sip_number"] = ""
        self.assertIsNone(self._account()["main"])


class SipOperatorsScopeTests(unittest.TestCase):
    def setUp(self):
        self.ns = _database_namespace({"get_sip_operators", "_sip_operator_row"})
        self.db = _StubDb(self.ns)

    def _query(self, **kwargs):
        self.ns["get_sip_operators"](self.db, **kwargs)
        return self.db.cursor.calls[0]

    def test_only_operators_and_active_by_default(self):
        sql, params = self._query()
        self.assertIn("u.role = ANY(%s)", sql)
        self.assertEqual(["operator", "trainee"], params[0])
        self.assertIn("LOWER(COALESCE(u.status, '')) <> ALL(%s)", sql)
        self.assertEqual(["fired", "dismissal"], params[1])

    def test_admin_scope_has_no_department_filter(self):
        sql, params = self._query(department_ids=None)
        self.assertNotIn("u.department_id = ANY(%s)", sql)
        self.assertEqual(2, len(params))

    def test_supervisor_also_sees_own_operators_outside_the_department(self):
        sql, params = self._query(department_ids=[367], supervisor_id=9)
        self.assertIn("(u.department_id = ANY(%s) OR u.supervisor_id = %s)", sql)
        self.assertEqual([367], params[2])
        self.assertEqual(9, params[3])

    def test_include_inactive_drops_the_status_filter(self):
        sql, params = self._query(include_inactive=True)
        self.assertNotIn("<> ALL(%s)", sql)
        self.assertEqual(1, len(params))

    def test_list_is_sorted_by_name(self):
        sql, _ = self._query()
        self.assertTrue(sql.rstrip().endswith("ORDER BY u.name"))


class SipEndpointTests(unittest.TestCase):
    """Гейт доступа и скоуп новых эндпоинтов раздела."""

    def setUp(self):
        self.source = _read(BOT_PATH)
        self.module = ast.parse(self.source)

    def _function(self, name):
        node = next(
            n for n in self.module.body
            if isinstance(n, ast.FunctionDef) and n.name == name
        )
        return ast.get_source_segment(self.source, node)

    def test_routes_exist(self):
        self.assertIn("@app.route('/api/sip_config/operators', methods=['GET', 'OPTIONS'])", self.source)
        self.assertIn(
            "@app.route('/api/sip_config/operators/<int:target_user_id>', methods=['PUT', 'OPTIONS'])",
            self.source,
        )

    def test_both_endpoints_reuse_the_panel_gate(self):
        for name in ("sip_config_operators_endpoint", "sip_config_operator_update_endpoint"):
            body = self._function(name)
            self.assertIn("_can_manage_sip_config(requester_id, role)", body)
            self.assertIn("@require_api_key", self.source.split(f"def {name}")[0].rsplit("@app.route", 1)[1])

    def test_bulk_route_does_not_collide_with_the_single_one(self):
        self.assertIn("@app.route('/api/sip_config/operators/bulk', methods=['PUT', 'OPTIONS'])", self.source)
        # int-конвертер не матчит "bulk", поэтому маршруты не конфликтуют.
        self.assertIn("<int:target_user_id>", self.source)

    def test_bulk_endpoint_checks_gate_scope_role_and_existence(self):
        body = self._function("sip_config_operators_bulk_endpoint")
        self.assertIn("_can_manage_sip_config(requester_id, role)", body)
        self.assertIn("_sip_department_scope(requester_id, role)", body)
        self.assertIn("_sip_target_in_scope(t, department_ids, supervisor_id)", body)
        self.assertIn("Часть сотрудников не найдена", body)
        self.assertIn("В выборке есть сотрудники не из вашего отдела", body)
        self.assertIn("операторам и стажёрам", body)

    def test_scope_check_helper_covers_department_and_own_operators(self):
        ns = {}
        exec(self._function("_sip_target_in_scope"), ns)
        check = ns["_sip_target_in_scope"]
        target = {"department_id": 12, "supervisor_id": 9}
        self.assertTrue(check(target, None, None))                    # админ — все
        self.assertTrue(check(target, [12], None))                    # свой отдел
        self.assertFalse(check(target, [367], None))                  # чужой отдел
        self.assertTrue(check(target, [367], 9))                      # свой оператор у СВ
        self.assertFalse(check(target, [367], 5))

    def test_update_endpoint_enforces_department_boundary(self):
        body = self._function("sip_config_operator_update_endpoint")
        self.assertIn("_sip_department_scope(requester_id, role)", body)
        self.assertIn("_sip_target_in_scope(target, department_ids, supervisor_id)", body)
        self.assertIn("Сотрудник не из вашего отдела", body)
        self.assertIn("403", body)

    def test_scope_helper_keeps_head_inside_the_department(self):
        ns = {}
        helper = self._function("_sip_department_scope")
        exec(helper, ns)
        ns["_is_admin_role"] = lambda role: role == "admin"
        ns["_headed_department_ids"] = lambda uid: frozenset({12}) if uid == 2 else frozenset()
        ns["_department_scope_id_for_requester"] = lambda uid: 367

        self.assertEqual((None, None), ns["_sip_department_scope"](1, "admin"))
        self.assertEqual(([12], None), ns["_sip_department_scope"](2, "admin"))
        self.assertEqual(([367], 3), ns["_sip_department_scope"](3, "sv"))

    def test_operator_endpoint_serves_autodial_and_the_shared_code(self):
        body = self._function("operator_sip_settings_endpoint")
        self.assertIn("db.get_user_sip_account(requester_id)", body)
        self.assertIn('"autodial": autodial', body)
        self.assertIn('"autodial_code"', body)
        # Плоские поля основного аккаунта остаются на месте — старый телефон не ломаем.
        self.assertIn("**main", body)


class SipSectionFrontendTests(unittest.TestCase):
    def setUp(self):
        self.app = _read(APP_PATH)

    def test_modal_is_replaced_by_a_real_section(self):
        self.assertFalse((ROOT / "src" / "components" / "modals" / "SipSettingsModal.jsx").exists())
        self.assertNotIn("SipSettingsModal", self.app)
        self.assertIn(
            "const SipSettingsView = lazyWithRetry(() => import('./components/sip/SipSettingsView'));",
            self.app,
        )

    def test_sidebar_navigates_to_the_view_in_both_branches(self):
        self.assertEqual(2, self.app.count("handleSidebarViewNavigation(e, 'sip_settings')"))
        self.assertEqual(2, self.app.count("view === 'sip_settings' ? 'bg-blue-700' : ''"))

    def test_section_renders_behind_the_access_flag(self):
        self.assertIn('view === "sip_settings" && canAccessSipSettings', self.app)

    def test_department_allowlist_guard_lets_the_section_through(self):
        self.assertIn("if (view === 'sip_settings' && canAccessSipSettings) return;", self.app)

    def test_view_is_built_from_shared_ios_primitives(self):
        view = _read(VIEW_PATH)
        self.assertIn("from '../ui/ios'", view)
        for token in ("iosCard", "iosInput", "IosModal", "APPLE_FONT"):
            self.assertIn(token, view)

    def test_view_loads_operators_and_settings_in_one_request(self):
        view = _read(VIEW_PATH)
        self.assertIn("/api/sip_config/operators", view)
        # Список и общие настройки приходят одним ответом: отдельного GET
        # /api/sip_config нет. Всего четыре обращения — список, история
        # (лениво, при открытии вкладки) и два сохранения.
        self.assertIn("setCommonForm({", view)
        self.assertEqual(5, view.count("await fetch("))
        self.assertIn("if (tab === 'history' && !historyLoadedRef.current) fetchHistory();", view)

    def test_duplicates_and_conflicts_are_scoped_to_the_domain(self):
        view = _read(VIEW_PATH)
        self.assertIn("const numberKey = (number, domain) =>", view)
        self.assertIn("const effectiveDomain = (personal, common) =>", view)
        # Подсветка дублей и проверка конфликта считают пару, а не голый номер.
        duplicates = view.split("const duplicateKeys = useMemo(", 1)[1].split("}, [", 1)[0]
        self.assertIn("numberKey(number, domain)", duplicates)
        self.assertIn("effectiveDomain(op.sip_domain, settings.sip_server)", duplicates)
        conflicts = view.split("const conflicts = useMemo(", 1)[1].split("}, [", 1)[0]
        self.assertIn("numberKey(form.sip_number, effective.domain)", conflicts)
        self.assertIn("numberKey(form.autodial_number, effective.autodialDomain)", conflicts)
        self.assertNotIn("duplicateNumbers", view)

    def test_ctrl_and_shift_selection_drives_the_bulk_editor(self):
        view = _read(VIEW_PATH)
        self.assertIn("if (event.ctrlKey || event.metaKey)", view)   # ⌘ на маке
        self.assertIn("if (event.shiftKey)", view)                   # диапазон
        self.assertIn("/api/sip_config/operators/bulk", view)
        self.assertIn("Выбрано: {selected.size}", view)
        # Массово меняются только пароль и домен — номера у каждого свои.
        bulk_fields = view.split("const BULK_FIELDS = [", 1)[1].split("];", 1)[0]
        self.assertIn("sip_domain", bulk_fields)
        self.assertIn("autodial_password", bulk_fields)
        self.assertNotIn("sip_number", bulk_fields)


if __name__ == "__main__":
    unittest.main()
