# -*- coding: utf-8 -*-
"""Временный просмотр «Удаленного КЦ», как у админа, но без правки (владелец, 21.11.2026:
«Сотрудникам … в сзов надо открыть админский доступ временный к разделам Удаленный КЦ и
Графики работы чисто на просмотр»).

Что защищаем:

  * человеку из DIAL_LIST_VIEW_ONLY_USER_IDS открыты все отделы «Удаленного КЦ» — и только
    методом GET; любая запись (POST, PUT) — 403 с внятным текстом, до сервиса дело не доходит;
  * отделы автообзвона Тез (другой раздел сайта) в его зону не входят, кандидатов на
    подключение и чужих сотрудников для посадки на линию он не получает;
  * оператор не из списка раздела не получает, как и раньше;
  * своя зона в разделе сильнее просмотра: тот же человек супервайзером СЗоВ работает в
    разделе с полными правами;
  * сайт держит тот же список людей, что и сервер.

Правила и ручки — настоящие (DialListService, build_dial_list_blueprint); подменены только
ответы базы. Люди выдуманы, id — из списка допуска.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import routes as dial_routes  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, 'src', 'App.jsx')

SZOV = 1
LINE_DEPARTMENT = 1954   # удалённый КЦ (профиль remote_cc)
TEZ = 560                # автообзвон Тез (профиль tez) — другой раздел сайта
PROFILES = {LINE_DEPARTMENT: 'remote_cc', TEZ: 'tez'}
VIEWER, OTHER_VIEWER = sorted(dial_service.DIAL_LIST_VIEW_ONLY_USER_IDS)
OPERATOR = 700           # оператор СЗоВ не из списка


class _Svc(dial_service.DialListService):
    """Настоящие правила доступа; вместо базы — отделы раздела и журнал вызовов записи."""

    def __init__(self):
        super().__init__(db=None)
        self.calls = []

    def _heads_department_with_code(self, department_ids, codes):
        return False

    def list_departments(self, department_ids=None, profile=None):
        ids = [d for d in (LINE_DEPARTMENT, TEZ) if profile is None or PROFILES[d] == profile]
        if department_ids is not None:
            ids = [d for d in ids if d in set(department_ids)]
        return [{"department_id": d, "department_name": f"Отдел {d}", "profile": PROFILES[d]} for d in ids]

    def candidate_departments(self, profile=None):
        return [{"department_id": 2008}]

    def department_sip_server(self, department_id):
        return 'sip.example'

    def list_lines(self, department_id):
        return [{"internal_number": "906", "user_id": None}]

    def department_users(self, department_id):
        return [{"id": 10, "name": "Своев Сава"}]

    def line_candidates(self, department_id):
        return [{"id": 77, "name": "Гостев Глеб"}]

    def department_settings(self, department_id):
        return {"department_id": department_id, "configured": True, "enabled": True}

    def department_profile(self, department_id):
        return PROFILES.get(int(department_id), 'remote_cc')

    def overview(self, department_ids, day, tez=False):
        return [{"department_ids": list(department_ids)}]

    def outcomes(self, department_id, with_version=False):
        return [], 'v1'

    # Запись: ни одна не должна дойти сюда у просмотра.
    def assign_line(self, *args, **kwargs):
        self.calls.append(('assign', args))
        return {}

    def release_line(self, *args, **kwargs):
        self.calls.append(('release', args))
        return {}

    def enroll_department(self, *args, **kwargs):
        self.calls.append(('enroll', args))
        return {}

    def save_department_settings(self, *args, **kwargs):
        self.calls.append(('settings', args))
        return {}

    def save_outcomes(self, *args, **kwargs):
        self.calls.append(('outcomes', args))
        return [], 'v2'


class ViewOnlyRoutesTests(unittest.TestCase):
    ROLES = {VIEWER: 'operator', OTHER_VIEWER: 'operator', OPERATOR: 'operator'}
    DEPARTMENTS = {VIEWER: 'szov', OTHER_VIEWER: 'szov', OPERATOR: 'szov'}

    def _client(self, user_id, role=None):
        import flask
        svc = _Svc()
        role = role or self.ROLES[user_id]
        requester = (user_id, None, 'Сотрудник', role, None, None, None, f'user{user_id}')
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (user_id, requester, None),
            is_admin_role=lambda r: r in ('admin', 'super_admin'),
            headed_department_ids=lambda rid: [],
            is_super_admin_role=lambda r: r == 'super_admin',
            is_supervisor_role=lambda r: r in ('sv', 'supervisor'),
            department_code_of=lambda uid: self.DEPARTMENTS.get(uid),
            service=svc)
        app = flask.Flask(f'dial_list_view_only_{user_id}_{role}')
        app.register_blueprint(bp)
        return app.test_client(), svc

    LINES = f'/api/dial_list/departments/{LINE_DEPARTMENT}/lines'

    def test_viewer_sees_every_remote_cc_department_and_nothing_else(self):
        for user_id in (VIEWER, OTHER_VIEWER):
            client, svc = self._client(user_id)
            response = client.get('/api/dial_list/departments')
            self.assertEqual(response.status_code, 200, user_id)
            body = response.get_json()
            self.assertEqual([d["department_id"] for d in body["departments"]], [LINE_DEPARTMENT])
            # «Подключить отдел» — право админа, у просмотра кандидатов нет.
            self.assertEqual(body["candidates"], [])
            self.assertEqual(client.get(self.LINES).status_code, 200)
            self.assertEqual(client.get(f'/api/dial_list/departments/{LINE_DEPARTMENT}/outcomes').status_code, 200)
            overview = client.get('/api/dial_list/overview').get_json()
            self.assertEqual(overview["operators"], [{"department_ids": [LINE_DEPARTMENT]}])
            self.assertEqual(svc.calls, [])

    def test_tez_departments_stay_closed(self):
        client, _svc = self._client(VIEWER)
        self.assertEqual(client.get(f'/api/dial_list/departments/{TEZ}/lines').status_code, 403)
        self.assertEqual(client.get(f'/api/dial_list/overview?department_id={TEZ}').status_code, 403)
        body = client.get('/api/dial_list/departments?profile=tez').get_json()
        self.assertEqual(body["departments"], [])

    def test_viewer_gets_no_one_to_seat_on_a_line(self):
        client, _svc = self._client(VIEWER)
        body = client.get(self.LINES).get_json()
        self.assertIs(body["can_seat_anyone"], False)
        self.assertIs(body["can_seat_robot"], False)
        self.assertEqual(body["candidates"], [])

    def test_every_write_is_refused_before_the_service(self):
        client, svc = self._client(VIEWER)
        writes = [
            ('post', self.LINES + '/assign', {"user_id": 10, "internal_number": "906"}),
            ('post', self.LINES + '/release', {"user_id": 10}),
            ('post', f'/api/dial_list/departments/{LINE_DEPARTMENT}/enroll', {}),
            ('post', f'/api/dial_list/departments/{LINE_DEPARTMENT}/unenroll', {}),
            ('put', f'/api/dial_list/departments/{LINE_DEPARTMENT}/settings', {"enabled": False}),
            ('put', f'/api/dial_list/departments/{LINE_DEPARTMENT}/outcomes', {"items": []}),
            ('put', f'/api/dial_list/departments/{LINE_DEPARTMENT}/script', {"body": "x"}),
            ('put', f'/api/dial_list/departments/{LINE_DEPARTMENT}/batches/focus', {"batch_ids": []}),
            ('put', f'/api/dial_list/departments/{LINE_DEPARTMENT}/leads/pin', {"operator_id": None, "lead_ids": []}),
            ('post', f'/api/dial_list/departments/{LINE_DEPARTMENT}/script/ai', {"mode": "generate"}),
            ('post', f'/api/dial_list/departments/{LINE_DEPARTMENT}/repair/cut-calls', {"apply": True}),
        ]
        for method, url, payload in writes:
            response = getattr(client, method)(url, json=payload)
            self.assertEqual(response.status_code, 403, url)
            self.assertEqual(response.get_json()["error"], dial_service.VIEW_ONLY_DENIED, url)
        self.assertEqual(svc.calls, [], 'до сервиса запись дойти не должна')

    def test_operator_outside_the_list_gets_nothing(self):
        client, _svc = self._client(OPERATOR)
        self.assertEqual(client.get('/api/dial_list/departments').status_code, 403)
        self.assertEqual(client.get(self.LINES).status_code, 403)

    def test_own_zone_wins_over_view_only(self):
        """Тот же человек супервайзером СЗоВ — полные права раздела, а не просмотр."""
        client, svc = self._client(VIEWER, role='sv')
        body = client.get('/api/dial_list/departments').get_json()
        self.assertEqual([d["department_id"] for d in body["departments"]], [LINE_DEPARTMENT])
        self.assertEqual(body["candidates"], [{"department_id": 2008}])
        response = client.post(self.LINES + '/assign', json={"user_id": 10, "internal_number": "906"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(svc.calls), 1)

    def test_pilot_still_closes_the_section(self):
        """Пилот раздела (DIAL_LIST_PILOT_LOGINS) сильнее просмотра: список не обходит его."""
        original = dial_service.DIAL_LIST_PILOT_LOGINS
        try:
            dial_service.DIAL_LIST_PILOT_LOGINS = frozenset({'someone_else'})
            client, _svc = self._client(VIEWER)
            self.assertEqual(client.get(self.LINES).status_code, 403)
        finally:
            dial_service.DIAL_LIST_PILOT_LOGINS = original


class ViewOnlyListTests(unittest.TestCase):
    def test_rule_reads_ids_of_any_shape(self):
        self.assertIs(dial_service.view_only_user(VIEWER), True)
        self.assertIs(dial_service.view_only_user(str(VIEWER)), True)
        for value in (OPERATOR, None, '', 'abc'):
            self.assertIs(dial_service.view_only_user(value), False, value)

    def test_site_holds_the_same_people(self):
        with open(APP_PATH, encoding='utf-8') as fh:
            app = fh.read()
        match = re.search(r'^const DIAL_LIST_VIEW_ONLY_USER_IDS = new Set\(\[([\d,\s]*)\]\);', app, re.M)
        self.assertIsNotNone(match, 'зеркало списка в App.jsx не найдено')
        site = {int(x) for x in match.group(1).replace(' ', '').split(',') if x}
        self.assertEqual(site, set(dial_service.DIAL_LIST_VIEW_ONLY_USER_IDS))


if __name__ == '__main__':
    unittest.main()
