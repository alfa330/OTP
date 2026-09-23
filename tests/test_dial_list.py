# -*- coding: utf-8 -*-
"""Раздел «Обзвон из телефона»: правила без базы и инварианты по исходникам.

Главный инвариант раздела — телефон оператора не получает номер водителя. Он
проверяется по исходникам: ни один запрос, собирающий ответ для оператора, не
выбирает phone_norm, а маршруты телефона не читают лид напрямую.
"""
import inspect
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import routes as dial_routes  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402


class DispositionMappingTests(unittest.TestCase):
    def test_final_dispositions(self):
        self.assertEqual(dial_service.map_disposition('ANSWER'), 'answered')
        self.assertEqual(dial_service.map_disposition('answered'), 'answered')
        self.assertEqual(dial_service.map_disposition('VM-SUCCESS'), 'answered')
        self.assertEqual(dial_service.map_disposition('BUSY'), 'busy')
        self.assertEqual(dial_service.map_disposition('NOANSWER'), 'no_answer')
        self.assertEqual(dial_service.map_disposition('CANCEL'), 'no_answer')
        self.assertEqual(dial_service.map_disposition('CONGESTION'), 'other')

    def test_non_final_dispositions_are_empty(self):
        # Пусто — звонок ещё набирается, ONLINE — идёт разговор: строку закрывать рано.
        self.assertEqual(dial_service.map_disposition(''), '')
        self.assertEqual(dial_service.map_disposition(None), '')
        self.assertEqual(dial_service.map_disposition('ONLINE'), '')
        self.assertEqual(dial_service.map_disposition(' online '), '')


class SettingsResolutionTests(unittest.TestCase):
    def test_personal_overrides_department(self):
        self.assertTrue(dial_service.resolve_enabled(True, False))
        self.assertFalse(dial_service.resolve_enabled(False, True))

    def test_department_when_personal_unset(self):
        self.assertTrue(dial_service.resolve_enabled(None, True))
        self.assertFalse(dial_service.resolve_enabled(None, False))

    def test_default_is_off(self):
        self.assertFalse(dial_service.resolve_enabled(None, None))


class SecretsLiveInEnvironmentTests(unittest.TestCase):
    """Решение владельца 23.09.2026: ключи и токены — только в окружении Render."""

    def test_schema_has_no_secret_columns(self):
        ddl = ' '.join(dial_schema.DDL)
        create = ddl[ddl.index('CREATE TABLE IF NOT EXISTS dial_list_department_settings'):ddl.index('CREATE TABLE IF NOT EXISTS dial_list_user_settings')]
        for column in ('binotel_api_key', 'binotel_api_secret', 'webhook_token'):
            self.assertNotIn(column, create)
            self.assertIn(f'DROP COLUMN IF EXISTS {column}', ddl)
        self.assertIn('binotel_company', create)

    def test_save_rejects_secrets_in_payload(self):
        class _DB:
            pass
        svc = dial_service.DialListService(_DB())
        svc.department_settings = lambda dep: {
            "enabled": False, "portion_size": 20, "max_attempts": 3, "retry_after_hours": 24,
            "caller_id_for_employee": "", "binotel_company": "remote_cc",
        }
        for key in ('binotel_api_key', 'binotel_api_secret', 'webhook_token'):
            with self.assertRaises(dial_service.DialListError):
                svc.save_department_settings(1, {key: 'value'})

    def test_env_prefix_per_company(self):
        from binotel import client
        self.assertEqual(client.env_prefix_for('remote_cc'), 'REMOTE_CC_BINOTEL')
        self.assertEqual(client.env_prefix_for('tez'), 'TEZ_BINOTEL')
        self.assertEqual(client.env_prefix_for('new_cc'), 'NEW_CC_BINOTEL')
        self.assertEqual(client.env_prefix_for('NEW_CC_BINOTEL'), 'NEW_CC_BINOTEL')

    def test_webhook_requires_env_token_and_binotel_ip(self):
        class _DB:
            pass
        svc = dial_service.DialListService(_DB())
        token = 'x' * 24
        binotel_ip = next(iter(dial_service.BINOTEL_SERVER_IPS))
        with unittest.mock.patch.dict(os.environ, {dial_service.WEBHOOK_TOKEN_ENV: '', dial_service.WEBHOOK_REQUIRE_BINOTEL_IP_ENV: '1'}):
            self.assertFalse(svc.webhook_allowed(token, binotel_ip))         # токен не задан — вебхук выключен
        with unittest.mock.patch.dict(os.environ, {dial_service.WEBHOOK_TOKEN_ENV: 'short', dial_service.WEBHOOK_REQUIRE_BINOTEL_IP_ENV: '1'}):
            self.assertFalse(svc.webhook_allowed('short', binotel_ip))       # слишком короткий
        with unittest.mock.patch.dict(os.environ, {dial_service.WEBHOOK_TOKEN_ENV: token, dial_service.WEBHOOK_REQUIRE_BINOTEL_IP_ENV: '1'}):
            self.assertTrue(svc.webhook_allowed(token, binotel_ip))
            self.assertFalse(svc.webhook_allowed(token, '8.8.8.8'))          # чужой адрес
            self.assertFalse(svc.webhook_allowed('y' * 24, binotel_ip))      # чужой токен
            self.assertFalse(svc.webhook_allowed('', binotel_ip))
        with unittest.mock.patch.dict(os.environ, {dial_service.WEBHOOK_TOKEN_ENV: token, dial_service.WEBHOOK_REQUIRE_BINOTEL_IP_ENV: '0'}):
            self.assertTrue(svc.webhook_allowed(token, '8.8.8.8'))           # проверку адреса сняли явно

    def test_settings_source_never_reads_secret_columns(self):
        src = inspect.getsource(dial_service.DialListService.department_settings)
        for column in ('binotel_api_key', 'binotel_api_secret', 'webhook_token'):
            self.assertNotIn(column, src)


class NumberNeverReachesOperatorTests(unittest.TestCase):
    """Телефон не должен получить номер водителя ни в одном ответе."""

    def test_operator_facing_queries_do_not_select_phone(self):
        for name in ('_portion_items', '_active_attempt', 'get_state', 'issue_next_portion'):
            src = inspect.getsource(getattr(dial_service.DialListService, name))
            self.assertNotIn('phone_norm', src, f'{name} выбирает номер — он уедет в телефон')

    def test_start_call_returns_no_phone(self):
        src = inspect.getsource(dial_service.DialListService.start_call)
        # phone_norm читается только ради запроса в Binotel и в ответ не попадает.
        returned = src[src.rindex('return {'):]
        self.assertNotIn('phone', returned)

    def test_operator_routes_have_no_lead_access(self):
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        operator_part = src[src.index('# ── телефон оператора'):src.index('# ── руководитель')]
        # Маршруты телефона ходят только через сервис: ни номера, ни таблицы лидов.
        self.assertNotIn('phone_norm', operator_part)
        self.assertNotIn('dial_list_leads', operator_part)
        self.assertNotIn('db.', operator_part)


class LinesTests(unittest.TestCase):
    """Линии Binotel: учётка линии уходит только в SIP-настройки сотрудника, не наружу."""

    EMPLOYEES = {
        "listOfEmployees": {
            "a@x": {"employeeID": "1", "name": "Анна", "email": "a@x", "presenceState": "active",
                    "endpointData": {"internalNumber": "902", "login": "lg902", "password": "pw902",
                                     "encryptedWithTls": "0", "wasOnlineAt": "1",
                                     "status": {"preparedStatus": "offline"}}},
            "notLinkedToUser_5": {"employeeID": 0, "name": "", "email": "",
                                  "endpointData": {"internalNumber": "907", "login": "lg907", "password": "pw907",
                                                   "encryptedWithTls": "1", "status": {"preparedStatus": "online"}}},
        }
    }

    def _service(self):
        saved = {}

        class _DB:
            def get_sip_operator(self, uid):
                return {"id": uid, "department_id": 7, "department_provider": "binotel"} if uid in (10, 11) else None

            def save_user_sip_settings(self, uid, payload, changed_by=None):
                saved[uid] = payload
                return {}

        svc = dial_service.DialListService(_DB())
        svc._binotel_employees = lambda dep: list(self.EMPLOYEES["listOfEmployees"].values())
        svc.department_users = lambda dep: [
            {"id": 10, "name": "Иван", "login": "ivan", "role": "operator", "sip_number": "", "status": ""},
            {"id": 11, "name": "Пётр", "login": "petr", "role": "operator", "sip_number": "902", "status": ""},
        ]
        svc.department_sip_server = lambda dep: "sip52.binotel.com"
        return svc, saved

    def test_list_lines_hides_credentials(self):
        svc, _ = self._service()
        lines = svc.list_lines(7)
        self.assertEqual([l["internal_number"] for l in lines], ["902", "907"])
        text = str(lines)
        self.assertNotIn("lg9", text)
        self.assertNotIn("pw9", text)
        self.assertEqual(lines[0]["icore_user"]["name"], "Пётр")
        self.assertTrue(lines[1]["online"] and lines[1]["tls"])
        self.assertIsNone(lines[1]["icore_user"])

    def test_assign_writes_line_credentials_to_user(self):
        svc, saved = self._service()
        result = svc.assign_line(7, 10, "907", changed_by=1)
        self.assertEqual(saved[10], {"sip_number": "907", "sip_login": "lg907", "sip_password": "pw907"})
        self.assertEqual(result["sip_server"], "sip52.binotel.com")
        self.assertNotIn("password", str(result))

    def test_assign_refuses_taken_or_unknown_line(self):
        svc, saved = self._service()
        with self.assertRaises(dial_service.DialListError):
            svc.assign_line(7, 10, "902")   # занята Петром
        with self.assertRaises(dial_service.DialListError):
            svc.assign_line(7, 10, "999")   # такой линии нет
        with self.assertRaises(dial_service.DialListError):
            svc.assign_line(7, 42, "907")   # сотрудника нет
        self.assertEqual(saved, {})

    def test_lines_route_never_returns_secrets(self):
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        lines_part = src[src.index("def lines(department_id)"):src.index("def lines_assign")]
        self.assertNotIn("password", lines_part)
        self.assertNotIn("login", lines_part.replace("users", ""))  # только svc.department_users


class SchemaTests(unittest.TestCase):
    def test_tables_created_before_indexes(self):
        # Порядок закреплён: 17.08.2026 обратный порядок в другом разделе положил прод.
        kinds = ['index' if 'CREATE UNIQUE INDEX' in s or 'CREATE INDEX' in s else 'table'
                 for s in dial_schema.DDL]
        self.assertEqual(kinds, sorted(kinds, key=lambda k: k == 'index'))

    def test_open_lead_is_unique(self):
        ddl = ' '.join(dial_schema.DDL)
        self.assertIn("ON dial_list_assignments(lead_id) WHERE state = 'issued'", ddl)

    def test_leads_belong_to_department(self):
        ddl = ' '.join(dial_schema.DDL)
        self.assertIn('UNIQUE (department_id, phone_norm)', ddl)
        self.assertNotIn('REFERENCES tez_leads', ddl)


class WiringTests(unittest.TestCase):
    def _read(self, name):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name)
        with open(path, encoding='utf-8') as fh:
            return fh.read()

    def test_schema_is_initialised_in_init_db(self):
        src = self._read('database.py')
        self.assertIn('self._init_dial_list_schema_tx(cursor)', src)
        self.assertIn('def _init_dial_list_schema_tx(self, cursor):', src)

    def test_blueprint_and_phone_settings_are_wired(self):
        src = self._read('bot_schedule2.py')
        self.assertIn('build_dial_list_blueprint(', src)
        self.assertIn('"dial_list": _dial_list_phone_settings(requester_id)', src)

    def test_access_is_not_tied_to_sip_settings_or_tez(self):
        # Раздел — отдельного отдела удалённого КЦ: круг доступа свой, не «Настройки SIP — Tez».
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        self.assertNotIn('can_manage_sip_config', src)
        self.assertNotIn('sip_department_scope', src)
        self.assertIn('manager_scope', src)
        self.assertIn('remote_cc', dial_service.DIAL_LIST_DEPARTMENT_CODES)
        app_src = self._read(os.path.join('src', 'App.jsx'))
        self.assertIn("DIAL_LIST_DEPARTMENT_CODES = new Set(['remote_cc'])", app_src)
        self.assertNotIn('canAccessDialListSection = canAccessSipSettingsTez', app_src)

    def test_binotel_alone_does_not_put_department_into_section(self):
        # Замечание владельца 23.09.2026: Тез КЦ (тоже на Binotel) в разделе быть не должен.
        src = inspect.getsource(dial_service.DialListService.list_departments)
        self.assertNotIn("provider", src.split('where = ')[1].split('\n')[0])
        self.assertIn("s.department_id IS NOT NULL", src)
        cand = inspect.getsource(dial_service.DialListService.candidate_departments)
        self.assertIn("= 'binotel' AND s.department_id IS NULL", cand)

    def test_manager_scope_rules(self):
        class _DB:
            pass
        svc = dial_service.DialListService(_DB())
        svc.list_departments = lambda ids=None: [{"department_id": i} for i in (ids or []) if i != 99]
        pilot = next(iter(dial_service.DIAL_LIST_PILOT_LOGINS))
        self.assertIsNone(svc.manager_scope(True, [], login=pilot))          # админ без отдела — всё
        self.assertEqual(svc.manager_scope(False, [], login=pilot), [])      # обычная роль — ничего
        self.assertEqual(svc.manager_scope(False, [5, 99], login=pilot), [5])  # глава — свои отделы из периметра

    def test_pilot_restricts_everyone_else(self):
        # Решение владельца 22.09.2026: раздел пока виден только ему (alfa330).
        self.assertIn('alfa330', dial_service.DIAL_LIST_PILOT_LOGINS)
        self.assertTrue(dial_service.pilot_allows('alfa330'))
        self.assertTrue(dial_service.pilot_allows(' Alfa330 '))
        self.assertFalse(dial_service.pilot_allows('someone'))
        self.assertFalse(dial_service.pilot_allows(None))

        class _DB:
            pass
        svc = dial_service.DialListService(_DB())
        svc.list_departments = lambda ids=None: [{"department_id": 1}]
        self.assertEqual(svc.manager_scope(True, [], login='other_admin'), [])
        app_src = self._read(os.path.join('src', 'App.jsx'))
        self.assertIn("DIAL_LIST_PILOT_LOGINS = new Set(['alfa330'])", app_src)
        self.assertIn('dialListPilotAllows(user) &&', app_src)

    def test_binotel_client_has_call_methods(self):
        from binotel import client as binotel_client
        from tez import binotel_calls  # прослойка совместимости — те же объекты
        for name in ('originate_internal_to_external', 'call_details', 'hangup_call'):
            self.assertTrue(callable(getattr(binotel_client.BinotelApiClient, name)))
        self.assertIs(binotel_calls.BinotelApiClient, binotel_client.BinotelApiClient)
        self.assertIs(binotel_calls._day_bounds_unix, binotel_client._day_bounds_unix)
        cfg = binotel_client.get_config(env_file='/nonexistent', company='remote_cc')
        self.assertEqual(cfg['env_prefix'], 'REMOTE_CC_BINOTEL')

    def test_dial_list_does_not_import_from_tez_package(self):
        for module in (dial_service, dial_routes, dial_schema):
            src = inspect.getsource(module)
            self.assertNotIn('from tez', src)
            self.assertNotIn('import tez', src)


if __name__ == '__main__':
    unittest.main()
