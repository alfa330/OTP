# -*- coding: utf-8 -*-
"""Автообзвон Тез КЦ (15.10.2026): ручки (Flask поверх подделки сервиса), правила без базы и
сторож «номер водителя не уходит в телефон» для новых путей. Полный путь с базой —
test_dial_list_tez_postgres.py."""
import inspect
import io
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import routes as dial_routes  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

TEZ, RCC = 560, 1954


def _client(svc, role='operator', heads=(), supervisor_code=None, mirror=None, user_id=7, global_admin=None):
    import flask
    requester = (user_id, 'Сотрудник', 'x', role, None, None, None, f'user{user_id}')
    bp = dial_routes.build_dial_list_blueprint(
        db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (user_id, requester, None), is_admin_role=lambda r: r == 'admin',
        headed_department_ids=lambda rid: list(heads),
        is_supervisor_role=lambda r: r == 'sv', department_code_of=lambda uid: supervisor_code,
        service=svc, tez_leads_mirror=mirror,
        is_global_admin=(lambda rid, r: global_admin) if global_admin is not None else None)
    app = flask.Flask(f'dial_list_tez_{id(svc)}')
    app.register_blueprint(bp)
    return app.test_client()


class _Svc:
    """Подделка сервиса: записывает вызовы; профиль отдела — из profiles."""

    def __init__(self, profiles=None, scope=None):
        self.calls = []
        self.profiles = profiles or {TEZ: 'tez', RCC: 'remote_cc'}
        self.scope = scope

    def manager_scope(self, is_admin, headed, login=None, supervisor_department_code=None, **extra):
        self.calls.append(('scope', is_admin, supervisor_department_code, extra))
        return None if is_admin else self.scope

    def department_profile(self, department_id):
        return self.profiles.get(int(department_id), 'remote_cc')

    # телефон
    def start_call(self, user_id, assignment_id, **extra):
        self.calls.append(('call', assignment_id, extra))
        return {"attempt_id": "att-1"}

    def set_attempt_outcome(self, user_id, attempt_id, outcome_id, comment="", subtype_id=None, **extra):
        self.calls.append(('outcome', attempt_id, outcome_id, extra))
        return {"attempt_id": attempt_id}

    def auto_next(self, user_id, status=None, phone_version=None):
        self.calls.append(('auto_next', status, phone_version))
        return {"next": None, "reason": "wait", "retry_after_sec": 30, "portion": None}

    def send_whatsapp(self, user_id, lead_id, template_id, attempt_id=None):
        self.calls.append(('wa', lead_id, template_id, attempt_id))
        return {"sent": True, "template_title": "Бонус"}

    # руководитель
    def list_departments(self, department_ids=None, profile=None):
        self.calls.append(('list', department_ids, profile))
        return [{"department_id": d} for d, p in sorted(self.profiles.items()) if profile is None or p == profile]

    def candidate_departments(self, profile=None):
        self.calls.append(('candidates', profile))
        return []

    def enroll_department(self, department_id, changed_by=None, **extra):
        self.calls.append(('enroll', department_id, extra))
        return {"department_id": department_id, "profile": extra.get('profile', 'remote_cc')}

    def department_settings(self, department_id):
        return {"department_id": department_id, "profile": self.department_profile(department_id)}

    def save_department_settings(self, department_id, payload, changed_by=None):
        self.calls.append(('settings', department_id, payload))
        return {"department_id": department_id}

    def offers(self, department_id):
        return {"department_id": department_id, "items": []}

    def save_offers(self, department_id, items, changed_by=None):
        self.calls.append(('offers', department_id, items))
        return {"department_id": department_id, "items": items}

    def whatsapp_settings(self, department_id, refresh=False):
        self.calls.append(('wa_settings', department_id, refresh))
        return {"license_id": None}

    def save_whatsapp(self, department_id, payload, changed_by=None):
        self.calls.append(('wa_save', department_id, payload))
        return {"license_id": payload.get('license_id')}

    def import_leads(self, department_id, uploaded_by, file_name, rows, period=None, require_iin=True):
        self.calls.append(('import', department_id, file_name, len(rows), require_iin, period))
        return {"rows_total": len(rows), "rows_new": len(rows), "rows_duplicate": 0, "rows_invalid": 0,
                "period": (period or date(2026, 10, 1)).isoformat()}

    def request_sign_check(self, reason=""):
        self.calls.append(('sign_check', reason))

    def request_trip_check(self, reason=""):
        self.calls.append(('trip_check', reason))
        return True

    @property
    def trip_checker(self):
        class _State:
            def state(self):
                return {"configured": True, "running": True, "last_run": None}
        return _State()

    def overview(self, department_ids, day, **extra):
        self.calls.append(('overview', department_ids, extra))
        return []


class OperatorRoutesTests(unittest.TestCase):
    def test_call_passes_auto_and_status_only_when_the_phone_sent_them(self):
        svc = _Svc()
        client = _client(svc)
        self.assertEqual(client.post('/api/operator/dial_list/items/asg-1/call').status_code, 200)
        client.post('/api/operator/dial_list/items/asg-2/call', json={"auto": True, "status": "active"})
        client.post('/api/operator/dial_list/items/asg-3/call', json={"auto": "yes", "status": "outbound"})
        self.assertEqual([c for c in svc.calls if c[0] == 'call'], [
            ('call', 'asg-1', {}),
            ('call', 'asg-2', {"auto": True, "operator_status": "active"}),
            # auto — только настоящий true: строка «yes» — ручной звонок.
            ('call', 'asg-3', {"auto": False, "operator_status": "outbound"})])

    def test_outcome_passes_due_and_offers_only_when_sent(self):
        svc = _Svc()
        client = _client(svc)
        client.post('/api/operator/dial_list/attempts/att-1/outcome', json={"outcome_id": "o1"})
        client.post('/api/operator/dial_list/attempts/att-2/outcome',
                    json={"outcome_id": "o2", "due_at": "2026-10-16T13:00:00Z", "offer_ids": []})
        client.post('/api/operator/dial_list/attempts/att-3/outcome',
                    json={"outcome_id": "o3", "due_at": None})
        self.assertEqual([c for c in svc.calls if c[0] == 'outcome'], [
            ('outcome', 'att-1', 'o1', {}),
            ('outcome', 'att-2', 'o2', {"due_at": "2026-10-16T13:00:00Z", "offer_ids": []}),
            ('outcome', 'att-3', 'o3', {"due_at": None})])

    def test_auto_next_and_whatsapp_routes(self):
        svc = _Svc()
        client = _client(svc)
        resp = client.post('/api/operator/dial_list/auto/next', json={"status": "active"},
                           headers={'X-ICore-Phone-Version': '3.22.37'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((resp.get_json()["reason"], resp.get_json()["retry_after_sec"]), ('wait', 30))
        resp = client.post('/api/operator/dial_list/leads/lead-1/whatsapp',
                           json={"template_id": "tpl", "attempt_id": "att-9"})
        self.assertEqual(resp.get_json()["sent"], True)
        self.assertIn(('auto_next', 'active', '3.22.37'), svc.calls)
        self.assertIn(('wa', 'lead-1', 'tpl', 'att-9'), svc.calls)

    def test_service_refusal_reaches_the_phone_with_its_kind(self):
        svc = _Svc()

        def refuse(*args, **kwargs):
            raise dial_service.DialListError(dial_service.AUTO_STATUS_ONLY_ACTIVE, 409,
                                             kind=dial_service.CALL_ERROR_STATUS)
        svc.auto_next = refuse
        resp = _client(svc).post('/api/operator/dial_list/auto/next', json={"status": "break"})
        self.assertEqual((resp.status_code, resp.get_json()), (409, {
            "error": dial_service.AUTO_STATUS_ONLY_ACTIVE, "error_kind": "status"}))


class ManagerRoutesTests(unittest.TestCase):
    def test_departments_are_listed_per_section(self):
        svc = _Svc()
        client = _client(svc, role='admin')
        body = client.get('/api/dial_list/departments').get_json()
        self.assertEqual((body["profile"], [d["department_id"] for d in body["departments"]]), ('remote_cc', [RCC]))
        body = client.get('/api/dial_list/departments?profile=tez').get_json()
        self.assertEqual((body["profile"], [d["department_id"] for d in body["departments"]]), ('tez', [TEZ]))
        self.assertIn(('candidates', 'tez'), svc.calls)
        self.assertEqual(client.get('/api/dial_list/departments?profile=x').status_code, 400)

    def test_enroll_profile_and_profile_change_only_by_admin(self):
        svc = _Svc()
        admin = _client(svc, role='admin')
        admin.post('/api/dial_list/departments/2008/enroll')
        admin.post('/api/dial_list/departments/560/enroll', json={"profile": "tez"})
        self.assertEqual([c for c in svc.calls if c[0] == 'enroll'],
                         [('enroll', 2008, {}), ('enroll', 560, {"profile": "tez"})])
        # Глава отдела: настройки — да, профиль — нет (тот же профиль в форме — можно).
        head = _client(_Svc(scope=[TEZ]), role='head', heads=[TEZ])
        resp = head.put('/api/dial_list/departments/560/settings', json={"profile": "remote_cc"})
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (403, "Профиль отдела меняет администратор"))
        self.assertEqual(head.put('/api/dial_list/departments/560/settings',
                                  json={"profile": "tez", "wrapup_sec": 90}).status_code, 200)
        self.assertEqual(admin.put('/api/dial_list/departments/560/settings',
                                   json={"profile": "remote_cc"}).status_code, 200)

    def test_head_with_admin_role_cannot_move_departments_between_sections(self):
        # Ревью 15.10.2026: у глав отделов роль админская (scope None), но администратор компании —
        # не они. Глава Тез КЦ не должен перевести отдел удалённого КЦ в автообзвон.
        svc = _Svc()
        head = _client(svc, role='admin', heads=[TEZ], global_admin=False)
        body = head.get('/api/dial_list/departments?profile=tez').get_json()
        self.assertEqual(body["candidates"], [])
        self.assertNotIn(('candidates', 'tez'), svc.calls)
        resp = head.post('/api/dial_list/departments/1954/enroll', json={"profile": "tez"})
        self.assertEqual(resp.status_code, 403)
        resp = head.put('/api/dial_list/departments/1954/settings', json={"profile": "tez"})
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (403, "Профиль отдела меняет администратор"))
        self.assertNotIn('settings', [c[0] for c in svc.calls])
        # Свой отдел и прежние права удалённого КЦ — как были.
        self.assertEqual(head.put('/api/dial_list/departments/560/settings', json={"profile": "tez"}).status_code, 200)
        self.assertEqual(head.post('/api/dial_list/departments/2008/enroll').status_code, 200)
        self.assertEqual(head.get('/api/dial_list/departments').get_json()["candidates"], [])
        self.assertIn(('candidates', None), svc.calls)
        # Администратор компании — может.
        admin = _client(_Svc(), role='admin', global_admin=True)
        self.assertEqual(admin.put('/api/dial_list/departments/1954/settings', json={"profile": "tez"}).status_code, 200)
        self.assertEqual(admin.post('/api/dial_list/departments/1954/enroll', json={"profile": "tez"}).status_code, 200)

    def test_tez_settings_routes_are_only_for_tez_departments(self):
        svc = _Svc()
        client = _client(svc, role='admin')
        self.assertEqual(client.get('/api/dial_list/departments/1954/offers').status_code, 409)
        self.assertEqual(client.get('/api/dial_list/departments/1954/whatsapp').status_code, 409)
        self.assertEqual(client.post('/api/dial_list/departments/1954/trips/check').status_code, 409)
        items = [{"title": "Бонус"}]
        self.assertEqual(client.put('/api/dial_list/departments/560/offers', json={"items": items}).get_json()["items"],
                         items)
        client.get('/api/dial_list/departments/560/whatsapp?refresh=1')
        client.put('/api/dial_list/departments/560/whatsapp', json={"license_id": 70651, "allowed": []})
        body = client.post('/api/dial_list/departments/560/trips/check').get_json()
        self.assertEqual((body["started"], body["state"]["running"]), (True, True))
        self.assertIn(('wa_settings', 560, True), svc.calls)
        self.assertIn(('wa_save', 560, {"license_id": 70651, "allowed": []}), svc.calls)
        self.assertIn(('trip_check', 'руководитель'), svc.calls)

    def test_tez_supervisor_scope_gets_its_id(self):
        svc = _Svc(scope=[TEZ])
        _client(svc, role='sv', supervisor_code='tez', user_id=3).get('/api/dial_list/departments?profile=tez')
        self.assertIn(('scope', False, 'tez', {"supervisor_id": 3}), svc.calls)
        # Не СВ — без supervisor_id: подделки старых тестов его не знают, и правило его не требует.
        svc = _Svc(scope=[TEZ])
        _client(svc, role='head', heads=[TEZ]).get('/api/dial_list/departments?profile=tez')
        self.assertIn(('scope', False, None, {}), svc.calls)

    def test_overview_of_a_tez_department_has_tez_columns(self):
        svc = _Svc()
        client = _client(svc, role='admin')
        client.get('/api/dial_list/overview?department_id=560')
        client.get('/api/dial_list/overview?department_id=1954')
        client.get('/api/dial_list/overview')
        self.assertEqual([c for c in svc.calls if c[0] == 'overview'], [
            ('overview', [TEZ], {"tez": True}), ('overview', [RCC], {}), ('overview', None, {})])


class UploadTests(unittest.TestCase):
    CSV = 'fio;phone\nИванов Иван;87010000001\nПетров Пётр;87010000002\n'.encode('utf-8')

    def _upload(self, client, department_id, payload=None):
        return client.post(f'/api/dial_list/departments/{department_id}/leads/upload',
                           data={"file": (io.BytesIO(payload or self.CSV), 'tez.csv'), "period": "2026-10"},
                           content_type='multipart/form-data')

    def test_tez_base_needs_no_iin_checks_trips_and_mirrors_to_tez_leads(self):
        svc = _Svc()
        mirrored = []

        def mirror(department_id, requester_id, file_name, raw_bytes, file_ext, period):
            mirrored.append((department_id, requester_id, file_name, raw_bytes, file_ext, period))
            return {"batch_id": "b-1", "rows_new": 2}
        resp = self._upload(_client(svc, role='admin', mirror=mirror), TEZ)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        body = resp.get_json()
        self.assertEqual((body["mirror"], body["trip_check"]), ({"batch_id": "b-1", "rows_new": 2}, 'queued'))
        self.assertIn(('import', TEZ, 'tez.csv', 2, False, date(2026, 10, 1)), svc.calls)
        self.assertIn(('trip_check', 'загрузка базы'), svc.calls)
        self.assertNotIn('sign_check', [c[0] for c in svc.calls])
        self.assertEqual(mirrored, [(TEZ, 7, 'tez.csv', self.CSV, '.csv', date(2026, 10, 1))])

    def test_mirror_failure_does_not_undo_the_upload(self):
        svc = _Svc()

        def broken(*args):
            raise RuntimeError('база лидов недоступна')
        body = self._upload(_client(svc, role='admin', mirror=broken), TEZ).get_json()
        self.assertEqual(body["status"], 'success')
        self.assertIn('загрузите его там вручную', body["mirror"]["error"])

    def test_remote_cc_upload_is_unchanged(self):
        svc = _Svc()
        mirrored = []
        resp = self._upload(_client(svc, role='admin', mirror=lambda *a: mirrored.append(a)), RCC)
        # Без колонки ИИН удалённому КЦ файл не годится — как и раньше.
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(mirrored, [])
        csv = 'fio;phone;iin\nИванов Иван;87010000001;900101300013\n'.encode('utf-8')
        body = self._upload(_client(svc, role='admin', mirror=lambda *a: mirrored.append(a)), RCC, csv).get_json()
        self.assertNotIn('mirror', body)
        self.assertIn(('sign_check', 'загрузка базы'), svc.calls)
        self.assertEqual(mirrored, [])


class RulesTests(unittest.TestCase):
    def test_due_at_window_and_zone(self):
        now = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
        parse = dial_service.parse_due_at
        self.assertEqual(parse('2026-10-15T18:00:00', 'callback', now),
                         datetime(2026, 10, 15, 18, 0, tzinfo=dial_service.PERIOD_TZ))
        self.assertEqual(parse('2026-10-15T13:00:00Z', 'online', now), datetime(2026, 10, 15, 13, 0,
                                                                                tzinfo=timezone.utc))
        for bad in (None, '', 'завтра', '2026-10-15T09:00:00Z', '2026-12-01T10:00:00Z'):
            with self.assertRaises(dial_service.DialListError, msg=repr(bad)) as ctx:
                parse(bad, 'callback', now)
            self.assertEqual(ctx.exception.status, 400)
        # Пять минут назад — ещё можно: оператор мог выбрать «сейчас» и задуматься.
        self.assertEqual(parse((now - timedelta(minutes=4)).isoformat(), 'callback', now),
                         now - timedelta(minutes=4))

    def test_retry_schedule(self):
        delay = dial_service.retry_delay_minutes
        self.assertEqual([delay([60, 180, 1440], n) for n in (1, 2, 3, 4, 9)], [60, 180, 1440, 1440, 1440])
        self.assertIsNone(delay([], 1))
        clean = dial_service.clean_retry_schedule
        self.assertEqual(clean([60, 180]), [60, 180])
        for bad in ('60', [0], [1.5], [True], list(range(1, 13))):
            with self.assertRaises(dial_service.DialListError, msg=repr(bad)):
                clean(bad)

    def test_driver_label(self):
        now = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
        label = dial_service.driver_label
        self.assertEqual(label(datetime(2026, 10, 3, 12, tzinfo=timezone.utc), None, now, now), 'Вышел на линию 03.10')
        self.assertEqual(label(None, datetime(2026, 10, 1, 12, tzinfo=timezone.utc), now, now), 'Работал 01.10')
        self.assertEqual(label(None, datetime(2026, 8, 1, 12, tzinfo=timezone.utc), now, now),
                         'Отток · последний заказ 01.08.2026')
        self.assertEqual(label(None, None, now, now), 'Заказов не было')
        self.assertEqual(label(None, None, None, now), '')

    def test_whatsapp_params(self):
        self.assertEqual(dial_service.wa_params(['{first_name}', '{operator}', 'бонус'], 'Иванов Иван Иванович',
                                                'Айгерим'), ['Иван', 'Айгерим', 'бонус'])


class NumberNeverReachesOperatorTezTests(unittest.TestCase):
    def test_tez_operator_paths_do_not_select_phone(self):
        for name in ('auto_next', '_tez_lead_block', '_tez_lead_fields', '_wa_phone_block', '_apply_tez_outcome',
                     '_precheck_call_trips', '_precheck_trips', '_clean_offer_ids'):
            src = inspect.getsource(getattr(dial_service.DialListService, name))
            self.assertNotIn('phone_norm', src, f'{name} выбирает номер — он уедет в телефон')
        for name in ('_AUTO_OWN_DUE_SQL', '_AUTO_ROWS_SQL', '_PORTION_TEZ_COLUMNS'):
            self.assertNotIn('phone_norm', getattr(dial_service.DialListService, name), name)

    def test_whatsapp_reads_the_number_only_for_chatapp(self):
        src = inspect.getsource(dial_service.DialListService.send_whatsapp)
        returned = src[src.rindex('return {'):]
        self.assertNotIn('phone', returned)
        # Текст ошибки ChatApp может нести номер — в outbox и наружу уходит без длинных чисел.
        self.assertIn('scrub_digits(', src)


if __name__ == '__main__':
    unittest.main()
