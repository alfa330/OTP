# -*- coding: utf-8 -*-
"""Раздел «Удаленный КЦ», вкладка «Линии»: линии робота-автоинформатора серые, посадить на них нельзя.

Просьба владельца 10.11.2026: «У автоинформатора нужно оставить всего 5 линий 902, 903, 907, 912, 913
и сделать их серыми, чтобы другие не могли забрать эти линии».

Что защищаем:

  * какие линии у робота — сообщает сам диспетчер (informer_agent_state.lines через
    InformerService.robot_status); в ответе GET …/lines у такой линии informer: true, у остальных false;
  * «Назначить» на линию робота сервер отклоняет 409 и до сервиса посадки не доходит — даже у тех, кто
    вправе сажать кого угодно (СВ СЗоВ, суперадмин);
  * робот звонит с линий кабинета удалённого КЦ: у отдела другого профиля (Тез — свой кабинет, номера
    линий те же) ничего не помечается и не запрещается, состояние робота там даже не читается;
  * не прочитали состояние робота (или автоинформатор на сервере не подключён) — вкладка работает как
    раньше: из-за соседнего раздела «Линии» не ломаются;
  * снять человека, оказавшегося на линии робота раньше, можно.

Ручки — настоящие (build_dial_list_blueprint), правила доступа — настоящие (DialListService);
подменены ответы базы и Binotel. Фронт — tests/dial_list_line_picker.test.mjs. Люди выдуманы.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dial_list import routes as dial_routes  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

REMOTE_CC = 1954
TEZ = 560
ROBOT = {"902": {"capacity": 2, "active": 0}, "903": {"capacity": 2, "active": 1},
         "907": {"capacity": 2, "active": 0}, "912": {"capacity": 2, "active": 0},
         "913": {"capacity": 2, "active": 0}}
LINES = ("901", "902", "903", "907", "912", "913", "914")


class _Svc(dial_service.DialListService):
    """Настоящие правила доступа; вместо базы и Binotel — профили отделов и линии."""

    PROFILES = {REMOTE_CC: dial_service.PROFILE_REMOTE_CC, TEZ: dial_service.PROFILE_TEZ}

    def __init__(self, holders=None):
        super().__init__(db=None)
        self.calls = []
        self.holders = holders or {}

    def department_profile(self, department_id):
        return self.PROFILES[int(department_id)]

    def list_departments(self, department_ids=None, profile=None):
        ids = [REMOTE_CC, TEZ] if department_ids is None else [i for i in department_ids if i in (REMOTE_CC, TEZ)]
        return [{"department_id": i} for i in ids]

    def department_sip_server(self, department_id):
        return 'sip53.binotel.com'

    def list_lines(self, department_id):
        return [{"internal_number": n, "online": n == "901", "status": "online" if n == "901" else "offline",
                 "tls": False, "was_online_at": None, "binotel_employee": None,
                 "icore_user": self.holders.get(n)} for n in LINES]

    def department_users(self, department_id):
        return [{"id": 10, "name": "Своев Сава", "login": "sava", "sip_number": "", "guest": False}]

    def line_candidates(self, department_id):
        return [{"id": 77, "name": "Гостев Глеб", "login": "gleb", "department_name": "СЗоВ"}]

    def assign_line(self, department_id, user_id, internal_number, changed_by=None, seat_anyone=False):
        self.calls.append(('assign', department_id, user_id, internal_number, seat_anyone))
        return {"user_id": user_id, "internal_number": internal_number, "guest": False}

    def release_line(self, department_id, user_id, changed_by=None, seat_anyone=False):
        self.calls.append(('release', department_id, user_id, seat_anyone))
        return {"user_id": user_id, "internal_number": ""}


class _Informer:
    """InformerService в части, нужной вкладке «Линии»: состояние робота."""

    def __init__(self, lines=None, error=None):
        self.lines = ROBOT if lines is None else lines
        self.error = error
        self.reads = 0

    def robot_status(self):
        self.reads += 1
        if self.error:
            raise self.error
        return {"online": True, "lines": dict(self.lines), "last_seen_at": "2026-11-10T08:16:40+00:00"}


def _client(informer=None, svc=None, role='super_admin', wire_informer=True):
    import flask
    svc = svc or _Svc()
    requester = (2, None, 'Руководитель', role, None, None, None, 'boss')
    kwargs = {"informer_service": informer} if wire_informer else {}
    bp = dial_routes.build_dial_list_blueprint(
        db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (2, requester, None),
        is_admin_role=lambda r: r in ('admin', 'super_admin'),
        headed_department_ids=lambda rid: [],
        is_super_admin_role=lambda r: r == 'super_admin',
        service=svc, **kwargs)
    app = flask.Flask('dial_list_informer_lines')
    app.register_blueprint(bp)
    return app.test_client(), svc


def _lines_url(department_id):
    return f'/api/dial_list/departments/{department_id}/lines'


class RobotLinesListTests(unittest.TestCase):
    def test_robot_lines_are_marked_and_others_are_not(self):
        informer = _Informer()
        client, _svc = _client(informer)
        resp = client.get(_lines_url(REMOTE_CC))
        self.assertEqual(resp.status_code, 200)
        marks = {line["internal_number"]: line["informer"] for line in resp.get_json()["lines"]}
        self.assertEqual(marks, {"901": False, "902": True, "903": True, "907": True, "912": True,
                                 "913": True, "914": False})
        self.assertEqual(informer.reads, 1, 'состояние робота — одним чтением на ответ')

    def test_list_follows_what_the_dispatcher_reports(self):
        """Второго списка в портале нет: сменили линии на шлюзе — помечаются новые."""
        client, _svc = _client(_Informer(lines={"914": {"capacity": 2, "active": 0}}))
        marks = {line["internal_number"]: line["informer"] for line in client.get(_lines_url(REMOTE_CC)).get_json()["lines"]}
        self.assertEqual([n for n, robot in marks.items() if robot], ["914"])

    def test_other_profile_department_is_not_touched(self):
        """Тез — свой кабинет Binotel с теми же номерами линий: робот там не звонит."""
        informer = _Informer()
        client, svc = _client(informer)
        body = client.get(_lines_url(TEZ)).get_json()
        self.assertFalse(any(line["informer"] for line in body["lines"]))
        self.assertEqual(informer.reads, 0, 'у отдела другого профиля состояние робота не читается')
        self.assertEqual(client.post(_lines_url(TEZ) + '/assign', json={"user_id": 10, "internal_number": "902"}).status_code, 200)
        self.assertEqual(svc.calls, [('assign', TEZ, 10, "902", True)])

    def test_unreadable_robot_state_keeps_the_tab_working(self):
        for informer, wire in ((_Informer(error=RuntimeError("schema")), True), (None, True), (None, False)):
            client, svc = _client(informer, wire_informer=wire)
            with self.assertLogs(dial_routes.log, level='ERROR') if informer else _no_logs():
                resp = client.get(_lines_url(REMOTE_CC))
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(any(line["informer"] for line in resp.get_json()["lines"]))
            self.assertEqual(client.post(_lines_url(REMOTE_CC) + '/assign',
                                         json={"user_id": 10, "internal_number": "902"}).status_code, 200)
            self.assertEqual(len(svc.calls), 1)


class _no_logs:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RobotLinesAssignTests(unittest.TestCase):
    def test_robot_line_is_refused_before_the_seating_service(self):
        for number in ("902", " 913 "):
            client, svc = _client(_Informer())
            resp = client.post(_lines_url(REMOTE_CC) + '/assign', json={"user_id": 77, "internal_number": number})
            self.assertEqual(resp.status_code, 409, number)
            self.assertEqual(resp.get_json()["error"],
                             f"С линии {number.strip()} звонит автоинформатор — назначить на неё нельзя")
            self.assertEqual(svc.calls, [], 'до посадки дело дойти не должно')

    def test_free_line_is_assigned_as_before(self):
        client, svc = _client(_Informer())
        resp = client.post(_lines_url(REMOTE_CC) + '/assign', json={"user_id": 77, "internal_number": "914"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(svc.calls, [('assign', REMOTE_CC, 77, "914", True)])

    def test_rights_are_checked_first(self):
        """Не руководитель раздела получает прежний отказ доступа, а не подсказку про робота."""
        client, svc = _client(_Informer(), role='operator')
        resp = client.post(_lines_url(REMOTE_CC) + '/assign', json={"user_id": 77, "internal_number": "902"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(svc.calls, [])

    def test_person_left_on_a_robot_line_can_be_released(self):
        holder = {"id": 10, "name": "Своев Сава", "login": "sava", "guest": False, "department_name": ""}
        client, svc = _client(_Informer(), svc=_Svc(holders={"902": holder}))
        body = client.get(_lines_url(REMOTE_CC)).get_json()
        line = next(line for line in body["lines"] if line["internal_number"] == "902")
        self.assertEqual((line["informer"], line["icore_user"]["id"]), (True, 10))
        self.assertEqual(client.post(_lines_url(REMOTE_CC) + '/release', json={"user_id": 10}).status_code, 200)
        self.assertEqual(svc.calls, [('release', REMOTE_CC, 10, True)])


class WiringTests(unittest.TestCase):
    def test_monolith_passes_the_informer_to_the_section(self):
        from source_cache import monolith_flat_path, read
        wiring = read(str(monolith_flat_path()))
        start = wiring.index('_dial_list_bp = build_dial_list_blueprint(')
        call = wiring[start:wiring.index('app.register_blueprint(_dial_list_bp)', start)]
        self.assertIn('informer_service=_informer_service', call)


if __name__ == '__main__':
    unittest.main()
