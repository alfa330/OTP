# -*- coding: utf-8 -*-
"""«Задачи» рядовому поимённо — решение владельца 24.11.2026 (отдел разработки).

Рядовой отдела-проекта заведён 'operator' — той же строкой, что оператор линии,
которому раздел закрыт, — а меню ему собирает набор «только это» отдела
(«Профиль» и «Мои смены»). Раздел выдан человеку, а не отделу, поэтому допуск
живёт в четырёх местах, и тесты ниже держат их вместе:
  - TASK_EMPLOYEE_USER_GRANTS в bot_schedule2.py — пускает в раздел и колокол;
  - TASK_EMPLOYEE_USER_GRANTS в db/core.py — охват, кому поручить, кандидаты на доску;
  - DEPARTMENT_PERSONAL_EXTRA_VIEWS в departmentViews.js — пункт меню;
  - его зеркало в bot_schedule2.py — колокол о разделе не молчит.

Гард гоняем НАСТОЯЩИЙ (_task_route_guard и вся его цепочка) над подставленной
базой. Кроме id 667 (сам допуск) люди и отделы выдуманы; ФИО в публичный
репозиторий не кладём.
"""

import json
import logging
import re
import shutil
import subprocess
import unittest
from types import SimpleNamespace

from tests.test_it_department_tasks_access import (
    BOT_PATH,
    DB_CORE_PATH,
    DEPARTMENT_VIEWS_PATH,
    GUARD_CHAIN,
    _segment,
)

GRANTED = 667
DEVELOPMENT, SZOV, IT = 2587, 1, 1933
DEPARTMENTS = {DEVELOPMENT: "development", SZOV: "szov", IT: "it"}


class _Db:
    def __init__(self, users, broken=False):
        self._users = users
        self._broken = broken

    def get_user_department_id(self, user_id):
        return self._users.get(int(user_id), (None, None))[1]

    def get_department_by_id(self, department_id):
        code = DEPARTMENTS.get(int(department_id))
        return {"code": code} if code else None

    def get_user_department(self, user_id):
        if self._broken:
            raise RuntimeError("база недоступна")
        department_id = self._users.get(int(user_id), (None, None))[1]
        return department_id, DEPARTMENTS.get(department_id)

    def get_task_access_facts(self, user_ids):
        return {int(uid): (DEPARTMENTS.get(self._users[int(uid)][1]), False) for uid in user_ids}


def _guard_namespace(users, broken_db=False):
    """users — {id: (роль, отдел)}; глав среди них нет."""
    current = {}
    namespace = {
        "g": None,
        "db": _Db(users, broken=broken_db),
        "logging": logging,
        "jsonify": lambda payload: payload,
        "_resolve_requester": lambda: (current["id"], current["row"], None),
        "_headed_department_id": lambda requester_id: None,
    }
    for name in GUARD_CHAIN + ("_task_access_for_people",):
        exec(_segment(name), namespace)

    def enter(user_id):
        role, _department = users[user_id]
        current["id"] = user_id
        current["row"] = (user_id, None, "login", role)
        namespace["g"] = SimpleNamespace()
        return namespace["_task_route_guard"](), namespace["g"]

    return enter, namespace


def _front_extra_views():
    """DEPARTMENT_PERSONAL_EXTRA_VIEWS из departmentViews.js: {код: {id: (разделы…)}}."""
    source = DEPARTMENT_VIEWS_PATH.read_text(encoding="utf-8")
    block = re.search(r"const DEPARTMENT_PERSONAL_EXTRA_VIEWS = \{\n(.*?)\n\};", source, re.S)
    assert block, "DEPARTMENT_PERSONAL_EXTRA_VIEWS не найден в departmentViews.js"
    result, code = {}, None
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        opened = re.fullmatch(r"([a-z_]+): \{", line)
        if opened:
            code = opened.group(1)
            result[code] = {}
            continue
        if line == "},":
            code = None
            continue
        row = re.fullmatch(r"(\d+): \[((?:'[a-z_]+'(?:, )?)*)\],", line)
        assert row and code, "строка набора не разобрана: %r" % line
        result[code][int(row.group(1))] = tuple(re.findall(r"'([a-z_]+)'", row.group(2)))
    return result


def _literal(name, path=BOT_PATH):
    namespace = {}
    exec(_segment(name, path), namespace)
    return namespace[name]


def _run_node(script):
    node = shutil.which("node")
    if not node:
        return None
    out = subprocess.run([node, "--input-type=module"], input=script.encode("utf-8"),
                         capture_output=True, check=True)
    return json.loads(out.stdout.decode("utf-8"))


class GrantMirrorTests(unittest.TestCase):
    """Допуск и пункт меню — в четырёх местах; разъедутся они молча."""

    def test_grants_match_everywhere(self):
        bot = _literal("TASK_EMPLOYEE_USER_GRANTS")
        core = _literal("TASK_EMPLOYEE_USER_GRANTS", DB_CORE_PATH)
        self.assertEqual({"development": frozenset({GRANTED})}, bot)
        self.assertEqual(bot, core)
        # Пункт меню — у тех и только тех, кого пускает сервер: пункт без
        # допуска открывал бы отказ, допуск без пункта — раздел, до которого не дойти.
        front = _front_extra_views()
        with_tasks = {code: frozenset(uid for uid, views in by_id.items() if "tasks" in views)
                      for code, by_id in front.items()}
        self.assertEqual(bot, {code: ids for code, ids in with_tasks.items() if ids})

    def test_extra_views_mirror_for_the_bell(self):
        front = _front_extra_views()
        server = _literal("DEPARTMENT_PERSONAL_EXTRA_VIEWS")
        self.assertEqual(front, {code: {uid: tuple(views) for uid, views in by_id.items()}
                                 for code, by_id in server.items()})
        # ООЗ (23.10.2026) — как было: «Мои смены» двум сотрудницам.
        self.assertEqual({619: ("work_schedules",), 620: ("work_schedules",)},
                         front["request_processing_department"])

    def test_granted_department_keeps_only_set(self):
        # Добавка дописывается к набору отдела: без набора «только это» у отдела
        # ей не к чему дописаться, и пункта меню не будет.
        only = _literal("DEPARTMENT_ONLY_VIEWS")
        for code in _literal("TASK_EMPLOYEE_USER_GRANTS"):
            self.assertIn("operator", only.get(code, {}), code)
            self.assertNotIn("tasks", only[code]["operator"], "отделу целиком раздел не выдан")


class GrantGuardTests(unittest.TestCase):
    USERS = {
        GRANTED: ("operator", DEVELOPMENT),
        668: ("operator", DEVELOPMENT),    # коллега по отделу без допуска
        669: ("operator", SZOV),           # оператор линии
    }

    def setUp(self):
        self.enter, self.namespace = _guard_namespace(self.USERS)

    def test_granted_operator_enters_with_personal_scope(self):
        result, g = self.enter(GRANTED)
        self.assertEqual(GRANTED, result[0])
        self.assertIsNone(result[2])
        # Слой данных знает его под своей ролью — она в личном охвате, как у рядового IT.
        self.assertEqual("operator", g.effective_task_role)
        self.assertTrue(g.task_scope_is_personal)
        self.assertEqual(DEVELOPMENT, g.task_scope_department_id)

    def test_trainee_role_is_granted_too(self):
        enter, _ns = _guard_namespace({GRANTED: ("trainee", DEVELOPMENT)})
        result, g = enter(GRANTED)
        self.assertIsNone(result[2])
        self.assertEqual("trainee", g.effective_task_role)

    def test_colleague_and_line_operator_stay_outside(self):
        for user_id in (668, 669):
            result, _g = self.enter(user_id)
            self.assertEqual(403, result[3], user_id)

    def test_grant_does_not_follow_to_another_department(self):
        # Переведут в отдел с линией — раздел за ним не поедет, как и пункт меню.
        enter, _ns = _guard_namespace({GRANTED: ("operator", SZOV)})
        result, _g = enter(GRANTED)
        self.assertEqual(403, result[3])

    def test_grant_does_not_lift_other_roles(self):
        # Должность бэк-офиса в чужом отделе раздела не получает и поимённо.
        enter, _ns = _guard_namespace({GRANTED: ("hr_manager", DEVELOPMENT)})
        result, _g = enter(GRANTED)
        self.assertEqual(403, result[3])

    def test_database_failure_closes_the_section(self):
        enter, _ns = _guard_namespace(self.USERS, broken_db=True)
        result, _g = enter(GRANTED)
        self.assertEqual(403, result[3])

    def test_bell_and_people_feed_follow_the_guard(self):
        can = self.namespace["_can_access_tasks"]
        self.assertTrue(can("operator", GRANTED))
        self.assertTrue(can("operator", str(GRANTED)), "id из профиля бывает строкой")
        self.assertFalse(can("operator", 668))
        batch = self.namespace["_task_access_for_people"]
        self.assertEqual({GRANTED}, batch({GRANTED: "operator", 668: "operator", 669: "operator"}))

    def test_helper_survives_garbage(self):
        granted = self.namespace["_task_employee_user_granted"]
        self.assertTrue(granted(GRANTED, "development"))
        for user_id, code in ((None, "development"), ("abc", "development"), (GRANTED, None),
                              (GRANTED, "szov"), (GRANTED, "constructor")):
            self.assertFalse(granted(user_id, code), (user_id, code))


class GrantDataLayerTests(unittest.TestCase):

    def _fragment(self, grants_source=None):
        namespace = {}
        for name in ("TASK_EMPLOYEE_DEPARTMENT_CODES", "TASK_EMPLOYEE_ROLES"):
            exec(_segment(name, DB_CORE_PATH), namespace)
        exec(grants_source or _segment("TASK_EMPLOYEE_USER_GRANTS", DB_CORE_PATH), namespace)
        exec(_segment("_TASK_EMPLOYEE_SQL", DB_CORE_PATH), namespace)
        return namespace["_TASK_EMPLOYEE_SQL"]

    def test_fragment_checks_role_department_and_id(self):
        self.assertIn(" OR (LOWER(BTRIM(d.code)) = 'development' AND u.id IN (667))", self._fragment())
        self.assertTrue(self._fragment().startswith("(u.role IN ('operator', 'trainee') AND ("))

    def test_fragment_stays_valid_without_grants(self):
        # Сняли последний допуск — фрагмент сводится к отделам, а не ломает SQL.
        for empty in ("TASK_EMPLOYEE_USER_GRANTS = {}",
                      "TASK_EMPLOYEE_USER_GRANTS = {'development': frozenset()}"):
            self.assertEqual(
                "(u.role IN ('operator', 'trainee') AND (LOWER(BTRIM(d.code)) IN ('it')))",
                self._fragment(empty), empty)


class GrantBellViewsTests(unittest.TestCase):
    """Колокол: набор «только это» с поимённой добавкой — тот же, что у меню."""

    def _views(self, users):
        class _ViewsDb:
            def get_user_department(self, user_id):
                department_id = users.get(int(user_id))
                return department_id, DEPARTMENTS.get(department_id)

        namespace = {"db": _ViewsDb(), "_headed_department_id": lambda requester_id: None}
        for name in ("BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE", "BACK_OFFICE_EMPLOYEE_ROLES",
                     "PERSONAL_VIEW_ALLOWLIST", "PERSONAL_VIEW_BASE_ROLES", "DEPARTMENT_ONLY_VIEWS",
                     "DEPARTMENT_PERSONAL_EXTRA_VIEWS", "_normalize_user_role", "_personal_views_for"):
            exec(_segment(name), namespace)
        return namespace["_personal_views_for"]

    def test_tasks_join_the_department_set(self):
        views = self._views({GRANTED: DEVELOPMENT, 668: DEVELOPMENT, 670: SZOV})
        self.assertEqual(("profile", "work_schedules", "tasks"), views(GRANTED, "operator"))
        self.assertEqual(("profile", "work_schedules"), views(668, "operator"))
        self.assertIsNone(views(670, "operator"))
        from notifications.sources import sources_outside_views
        self.assertNotIn("tasks", sources_outside_views(views(GRANTED, "operator")))
        self.assertIn("tasks", sources_outside_views(views(668, "operator")))

    def test_frontend_reads_the_same_set(self):
        users = [{"id": user_id, "role": role, "department_code": code}
                 for user_id in (GRANTED, str(GRANTED), 668)
                 for code in ("development", " Development ", "szov", "it")
                 for role in ("operator", "trainee", "sv", "hr_manager")]
        front = _run_node("\n".join((
            "import { personalViewsOf, rankAndFileTasksAllowed, firstAllowedView } from '%s';"
            % DEPARTMENT_VIEWS_PATH.as_uri(),
            "const users = %s;" % json.dumps(users),
            "process.stdout.write(JSON.stringify(users.map((u) => ["
            "personalViewsOf(u), rankAndFileTasksAllowed(u), firstAllowedView(u)])));",
        )))
        if front is None:
            self.skipTest("node недоступен")
        code_ids = {"development": DEVELOPMENT, " Development ": DEVELOPMENT, "szov": SZOV, "it": IT}
        for user, (personal, tasks_item, first) in zip(users, front):
            label = (user["id"], user["department_code"], user["role"])
            views = self._views({int(user["id"]): code_ids[user["department_code"]]})
            server = views(user["id"], user["role"])
            self.assertEqual(personal, list(server) if server is not None else None, label)
            granted = (int(user["id"]) == GRANTED and code_ids[user["department_code"]] == DEVELOPMENT
                       and user["role"] in ("operator", "trainee"))
            in_it = code_ids[user["department_code"]] == IT and user["role"] in ("operator", "trainee")
            self.assertEqual(granted or in_it, tasks_item, label)
            if granted:
                # Добавка — в хвост: после входа человек по-прежнему в «Профиле».
                self.assertEqual("profile", first, label)


if __name__ == "__main__":
    unittest.main()
