# -*- coding: utf-8 -*-
"""Раздел «Обзвон из телефона»: правила без базы и инварианты по исходникам.

Главный инвариант раздела — телефон оператора не получает номер водителя. Он
проверяется по исходникам: ни один запрос, собирающий ответ для оператора, не
выбирает phone_norm, а маршруты телефона не читают лид напрямую.
"""
import inspect
import json
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import routes as dial_routes  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

# Ширина строки страницы журнала без сводки: колонки журнала + total + rn.
PAGE_WIDTH = dial_service.DialListService._JOURNAL_COLUMN_COUNT + 2


class _RoutedCursor:
    """Поддельный курсор: ответ выбирается по первой подстроке SQL из rules.
    rules — [(подстрока, {'one': строка|callable(), 'all': [строки]|callable(params)})]."""

    def __init__(self, rules):
        self.rules = rules
        self.executed = []
        self._current = {}

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._current = next((answer for sub, answer in self.rules if sub in sql), {})

    def fetchone(self):
        value = self._current.get('one')
        return value() if callable(value) else value

    def fetchall(self):
        value = self._current.get('all', [])
        return list(value(self.executed[-1][1]) if callable(value) else value)

    def sql_with(self, fragment):
        return [(sql, params) for sql, params in self.executed if fragment in sql]


def _routed_db(cursor):
    import contextlib

    class Db:
        @contextlib.contextmanager
        def _get_cursor(self):
            yield cursor
    return Db()


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
        # CALLING — АТС ещё набирает водителя (живой тест 23.09.2026 закрыл попытку рано).
        self.assertEqual(dial_service.map_disposition('CALLING'), '')
        self.assertEqual(dial_service.map_disposition('RINGING'), '')

    def test_late_final_outcome_overwrites_provisional_one(self):
        # Попытку, закрытую по таймауту/промежуточному статусу, вебхук вправе дописать.
        src = inspect.getsource(dial_service.DialListService._finish_attempt)
        self.assertIn("state = 'finished' AND UPPER(disposition) IN %s", src)
        self.assertIn('PROVISIONAL_DISPOSITIONS', src)
        self.assertIn('UNKNOWN', dial_service.PROVISIONAL_DISPOSITIONS)
        self.assertIn('CALLING', dial_service.PROVISIONAL_DISPOSITIONS)
        webhook = inspect.getsource(dial_service.DialListService.handle_webhook)
        self.assertNotIn('!= "finished" and map_disposition', webhook)


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
        for name in ('_portion_items', '_active_attempt', 'get_state', 'issue_next_portion', 'operator_worked'):
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

    def test_department_users_filter_by_status_not_is_active(self):
        # users.is_active у живых сотрудников бывает FALSE — фильтровать по нему нельзя
        # (23.09.2026 из-за этого «В отделе нет сотрудников» при живом тест-операторе).
        src = inspect.getsource(dial_service.DialListService.department_users)
        self.assertNotIn('is_active', src.split('cur.execute')[1])
        self.assertIn("LOWER(COALESCE(u.status, '')) <> ALL(%s)", src)
        self.assertIn('_SIP_INACTIVE_STATUSES', src)

    def test_caller_id_for_employee_is_always_sent(self):
        # Живая проверка 23.09.2026: без callerIdForEmployee Binotel кладёт во From номер
        # водителя (виден в SIP-пакете), с ним — переданное значение. Поэтому параметр
        # уходит ВСЕГДА: поле отдела либо внутренний номер оператора.
        src = inspect.getsource(dial_service.DialListService.start_call)
        self.assertIn('extra = {"callerIdForEmployee": caller_id}', src)
        self.assertIn('or ctx["internal_number"]', src)

    def test_ext_unreachable_is_not_counted_against_lead(self):
        # Binotel code=150 «Can't call to the ext»: телефон оператора не зарегистрирован —
        # попытка по лиду не считается, а оператору объясняется, что проверить.
        src = inspect.getsource(dial_service.DialListService.start_call)
        self.assertIn('code=150', src)
        self.assertIn('count_for_lead=not ext_unreachable', src)
        self.assertIn('не на связи', src)
        fail_src = inspect.getsource(dial_service.DialListService._fail_attempt)
        self.assertIn('GREATEST(attempts - 1, 0)', fail_src)

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
        # Номер уникален в базе месяца, повтор в другом месяце допускается.
        self.assertIn('UNIQUE (department_id, period, phone_norm)', ddl)
        self.assertIn('DROP CONSTRAINT IF EXISTS dial_list_leads_department_id_phone_norm_key', ddl)
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
        svc._heads_overseer = lambda ids: 7 in set(ids)   # отдел 7 — СЗоВ
        self.assertIsNone(svc.manager_scope(True, [], login='admin'))          # админ — всё
        self.assertIsNone(svc.manager_scope(True, [5], login='admin'))         # админ с отделом — тоже всё
        self.assertEqual(svc.manager_scope(False, [], login='user'), [])       # обычная роль — ничего
        self.assertEqual(svc.manager_scope(False, [5, 99], login='head'), [5])  # глава — свои отделы из периметра
        self.assertIsNone(svc.manager_scope(False, [7], login='szov_head'))    # глава СЗоВ — весь раздел
        overseer = inspect.getsource(dial_service.DialListService._heads_overseer)
        self.assertIn('DIAL_LIST_OVERSEER_DEPARTMENT_CODES', overseer)
        self.assertIn('szov', dial_service.DIAL_LIST_OVERSEER_DEPARTMENT_CODES)

    def test_pilot_is_over_and_section_is_open(self):
        # Пилот (только alfa330, 22.09.2026) снят 25.09.2026: раздел открыт админам и главам.
        self.assertEqual(len(dial_service.DIAL_LIST_PILOT_LOGINS), 0)
        self.assertTrue(dial_service.pilot_allows('anyone'))
        self.assertTrue(dial_service.pilot_allows(None))

        class _DB:
            pass
        svc = dial_service.DialListService(_DB())
        svc.list_departments = lambda ids=None: [{"department_id": 1}]
        self.assertIsNone(svc.manager_scope(True, [], login='other_admin'))
        app_src = self._read(os.path.join('src', 'App.jsx'))
        self.assertIn("DIAL_LIST_PILOT_LOGINS = new Set();", app_src)
        self.assertIn("DIAL_LIST_OVERSEER_DEPARTMENT_CODES = new Set(['szov'])", app_src)
        self.assertIn('DIAL_LIST_OVERSEER_DEPARTMENT_CODES.has(code)', app_src)

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


class LeadsJournalTests(unittest.TestCase):
    """Журнал водителей: руководитель видит историю, но не номер."""

    def test_mask_keeps_only_tail(self):
        self.assertEqual(dial_service.mask_phone('77011234567'), '+7 ••• ••• 45 67')
        self.assertEqual(dial_service.mask_phone('380501234567'), '••• 45 67')
        self.assertEqual(dial_service.mask_phone(''), '•••')
        self.assertEqual(dial_service.mask_phone(None), '•••')

    def test_journal_rows_expose_masked_phone_only(self):
        src = inspect.getsource(dial_service.DialListService._journal_row)
        self.assertIn('mask_phone(', src)
        self.assertNotIn('"phone_norm"', src)
        self.assertNotIn('"phone":', src)
        card = inspect.getsource(dial_service.DialListService.lead_card)
        self.assertNotIn('phone_norm', card.split('_journal_row', 1)[1])

    def test_stage_filter_mirrors_pool_rules(self):
        # «В очереди» в журнале и выдача порции считают одно и то же.
        sql = dial_service.DialListService._JOURNAL_SQL
        for fragment in ("a.state = 'issued'", "make_interval(hours => %(retry_hours)s)",
                         "l.attempts_total >= %(max_attempts)s", "l.status = 'excluded'"):
            self.assertIn(fragment, sql)
        self.assertEqual(set(dial_service.LEAD_STAGES),
                         {'queue', 'waiting', 'issued', 'answered', 'exhausted', 'excluded', 'signed'})
        # Подписавший — первым: из пула он ушёл (signed_at в _POOL_SQL), и «В очереди»
        # у него было бы неправдой (владелец, 29.09.2026).
        case = sql[sql.index('CASE'):sql.index('END AS stage')]
        self.assertLess(case.index("WHEN l.signed_at IS NOT NULL THEN 'signed'"),
                        case.index("WHEN l.status = 'excluded'"))
        self.assertIn('AND l.signed_at IS NULL', dial_service.DialListService._POOL_SQL)
        svc = dial_service.DialListService(db=None)
        with self.assertRaises(dial_service.DialListError):
            svc.department_settings = lambda d: {"max_attempts": 3, "retry_after_hours": 24,
                                                 "_period": dial_service.current_period()}
            svc.leads_journal(1, stage='nope')

    def test_counts_follow_filters_except_their_own(self):
        # Число на чипе этапа/итога равно числу строк, которые покажет нажатие на
        # него: сводка считается по тем же фильтрам, что и список, кроме «своего».
        import contextlib
        import uuid
        refusal, callback = uuid.uuid4(), uuid.uuid4()
        executed = []

        # stage, last_outcome_id, last_outcome_subtype_id, for_stage (без фильтра этапа),
        # for_outcome (без фильтра итога)
        summary = [['answered', str(refusal), None, 4, 4], ['answered', str(callback), None, 0, 2],
                   ['queue', None, None, 0, 7], ['exhausted', str(refusal), None, 1, 0]]

        class Cursor:
            def execute(self, sql, params=None):
                executed.append((sql, dict(params or {})))

            def fetchall(self):
                sql = executed[-1][0]
                if 'FROM dial_list_outcomes' in sql:
                    return [(refusal, 'Отказ', '#FF3B30', True), (callback, 'Перезвонить', '#FF9F0A', True)]
                if 'counts.summary' in sql:
                    # Пустая страница: одна строка, колонки страницы — NULL, сводка — в последней.
                    return [(None,) * PAGE_WIDTH + (summary,)]
                return []

        class Db:
            @contextlib.contextmanager
            def _get_cursor(self):
                yield Cursor()

        svc = dial_service.DialListService(db=Db())
        svc.department_settings = lambda d: {"max_attempts": 3, "retry_after_hours": 24, "period": "2026-09-01",
                                             "_period": dial_service.current_period()}
        res = svc.leads_journal(1, stage='answered', outcome_id=str(refusal), q='Иван')

        sql = executed[0][0]
        # Общие фильтры — в выборке f, её читают и страница, и сводка.
        f_part = sql.split('f AS MATERIALIZED', 1)[1].split('page AS', 1)[0]
        self.assertIn('full_name ILIKE', f_part)
        self.assertNotIn('stage = %(stage)s', f_part)
        self.assertNotIn('last_outcome_id = %(outcome_id)s', f_part)
        # Страница — со всеми фильтрами.
        page_part = sql.split('page AS', 1)[1].split('counts AS', 1)[0]
        self.assertIn('stage = %(stage)s AND last_outcome_id = %(outcome_id)s::uuid', page_part)
        # Сводка: этапы без своего фильтра, итоги без своего.
        self.assertIn('FILTER (WHERE last_outcome_id = %(outcome_id)s::uuid) AS for_stage', sql)
        self.assertIn('FILTER (WHERE stage = %(stage)s) AS for_outcome', sql)
        self.assertEqual(sql.count('FROM dial_list_leads l'), 1)  # base строится один раз
        self.assertEqual(res['items'], [])
        self.assertEqual(res['total'], 0)
        self.assertEqual(res['by_stage']['answered'], 4)
        self.assertEqual(res['by_stage']['exhausted'], 1)
        self.assertEqual(res['by_stage']['queue'], 0)
        self.assertEqual({o['name']: o['count'] for o in res['by_outcome']}, {'Отказ': 4, 'Перезвонить': 2})
        # Типов у итогов нет — все водители итога «без типа».
        self.assertEqual({o['name']: (o['subtypes'], o['none_count']) for o in res['by_outcome']},
                         {'Отказ': ([], 4), 'Перезвонить': ([], 2)})

    def test_date_filter_means_call_date(self):
        # «Дата звонка»: хотя бы одна попытка в эти дни, обе границы — на одной
        # попытке. Раньше фильтр шёл по «последнему движению» (звонок ИЛИ загрузка
        # файла) — руководителю было непонятно, что он отбирает.
        import contextlib
        executed = []

        class Cursor:
            def execute(self, sql, params=None):
                executed.append((sql, dict(params or {})))

            def fetchall(self):
                return [(None,) * PAGE_WIDTH + ([],)] if 'counts.summary' in executed[-1][0] else []

        class Db:
            @contextlib.contextmanager
            def _get_cursor(self):
                yield Cursor()

        svc = dial_service.DialListService(db=Db())
        svc.department_settings = lambda d: {"max_attempts": 3, "retry_after_hours": 24, "period": "2026-09-01",
                                             "_period": dial_service.current_period()}
        svc.leads_journal(1, date_from='2026-09-20', date_to='2026-09-24')
        sql, params = executed[0]
        f_part = sql.split('f AS MATERIALIZED', 1)[1].split('page AS', 1)[0]
        self.assertNotIn('activity_at', f_part)
        exists = f_part.split('EXISTS', 1)[1]
        self.assertIn('FROM dial_list_attempts t', exists)
        self.assertIn('%(date_from)s', exists)
        self.assertIn('%(date_to)s', exists)
        self.assertEqual(f_part.count('EXISTS'), 1)                 # обе границы в одном EXISTS
        self.assertIn("AT TIME ZONE 'Asia/Almaty'", exists)
        self.assertEqual((params['date_from'], params['date_to']), ('2026-09-20', '2026-09-24'))

    def test_manual_actions_are_logged_and_guarded(self):
        ddl = ' '.join(dial_schema.DDL)
        self.assertIn('dial_list_lead_events', ddl)
        self.assertIn("kind IN ('requeue', 'exclude', 'restore', 'note')", ddl)
        requeue = inspect.getsource(dial_service.DialListService.requeue_lead)
        self.assertIn("state = 'issued'", requeue)          # строку у оператора не трогаем
        self.assertIn('_log_lead_event', requeue)
        exclude = inspect.getsource(dial_service.DialListService.exclude_lead)
        self.assertIn("'leg_ringing', 'leg_answered'", exclude)  # во время звонка — нельзя
        self.assertIn('_log_lead_event', exclude)

    def test_recording_only_for_answered_calls(self):
        src = inspect.getsource(dial_service.DialListService.attempt_recording)
        self.assertIn('!= "answered"', src)
        self.assertIn('get_call_record_url', src)

    def test_outcomes_and_periods_schema(self):
        ddl = ' '.join(dial_schema.DDL)
        self.assertIn('CREATE TABLE IF NOT EXISTS dial_list_outcomes', ddl)
        for column in ('outcome_id UUID REFERENCES dial_list_outcomes', 'operator_comment TEXT',
                       'leg_answered_at TIMESTAMP', 'active_period DATE'):
            self.assertIn(column, ddl)

    def test_pool_is_limited_to_active_period(self):
        self.assertIn('AND l.period = %s', dial_service.DialListService._POOL_SQL)
        for name in ('get_state', 'issue_next_portion', 'leads_summary'):
            src = inspect.getsource(getattr(dial_service.DialListService, name))
            self.assertIn('_POOL_SQL', src)
            self.assertIn('_period', src, f'{name} зовёт пул без месяца')

    def test_period_parsing(self):
        import datetime as dt
        self.assertEqual(dial_service.parse_period('2026-09'), dt.date(2026, 9, 1))
        self.assertEqual(dial_service.parse_period('2026-09-17'), dt.date(2026, 9, 1))
        self.assertIsNone(dial_service.parse_period(''))
        self.assertEqual(dial_service.parse_period('all', allow_all=True), 'all')
        with self.assertRaises(dial_service.DialListError):
            dial_service.parse_period('all')
        with self.assertRaises(dial_service.DialListError):
            dial_service.parse_period('сентябрь')
        self.assertEqual(dial_service.period_label(dt.date(2026, 9, 1)), 'Сентябрь 2026')

    def test_outcome_is_required_before_next_call_or_portion(self):
        for name in ('start_call', 'issue_next_portion'):
            src = inspect.getsource(getattr(dial_service.DialListService, name))
            self.assertIn('_require_outcome_done', src, f'{name} не проверяет итог предыдущего звонка')
        state = inspect.getsource(dial_service.DialListService.get_state)
        self.assertIn('"pending_outcome"', state)
        self.assertIn('"outcomes"', state)
        pending = inspect.getsource(dial_service.DialListService._pending_outcome)
        # Итог нужен только там, где разговор был (плечо принято), и только с этого релиза.
        self.assertIn('leg_answered_at IS NOT NULL', pending)
        self.assertIn('outcome_id IS NULL', pending)

    def test_save_outcomes_validation(self):
        svc = dial_service.DialListService(db=None)
        with self.assertRaises(dial_service.DialListError):
            svc.save_outcomes(1, [{"name": "", "color": "#FFFFFF"}])
        with self.assertRaises(dial_service.DialListError):
            svc.save_outcomes(1, [{"name": "Отказ", "color": "red"}])
        with self.assertRaises(dial_service.DialListError):
            svc.save_outcomes(1, [{"name": "Отказ", "color": "#FF0000"}, {"name": "отказ", "color": "#00FF00"}])
        with self.assertRaises(dial_service.DialListError):
            svc.save_outcomes(1, [{"name": "Отказ", "color": "#FF0000", "is_active": False}])
        with self.assertRaises(dial_service.DialListError):
            svc.save_outcomes(1, "not a list")

    def test_requeue_outcome_survives_late_pbx_outcome(self):
        # «Перезвонить» от оператора не должен перебиваться исходом АТС, пришедшим позже.
        finish = inspect.getsource(dial_service.DialListService._finish_attempt)
        self.assertIn('requeue', finish)
        touch = inspect.getsource(dial_service.DialListService._touch_lead)
        self.assertIn('if requeue:', touch)

    def test_journal_routes_are_manager_only(self):
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        journal_part = src[src.index('# ── журнал водителей'):src.index("@bp.route('/api/dial_list/overview'")]
        routes = [chunk for chunk in journal_part.split('@bp.route(')[1:]]
        # Карточка, «вернуть», «исключить», «проверить подписание», заметка, запись, список.
        self.assertEqual(len(routes), 7)
        for chunk in routes:
            self.assertIn('_manager(', chunk, 'ручка журнала без проверки зоны руководителя')
            self.assertIn('@require_api_key', chunk)
        self.assertNotIn('/api/operator/', journal_part)


class OperatorHangupTests(unittest.TestCase):
    """Решение владельца 24.09.2026: оператор сам положил трубку до ответа водителя —
    итог не ставится, попытка не засчитывается."""

    def test_phone_reports_operator_hangup(self):
        self.assertIn('operator_hangup', dial_service.PHONE_EVENTS)
        src = inspect.getsource(dial_service.DialListService.phone_event)
        # Отбой оператора запоминается всегда (и по закрытой попытке — откат), ответ
        # несёт решение по итогу для телефона.
        self.assertIn('operator_hangup_at = COALESCE(operator_hangup_at, CURRENT_TIMESTAMP)', src)
        self.assertIn('"outcome_required"', src)
        self.assertIn('"cancelled"', src)
        self.assertIn('late=True', src)
        route = inspect.getsource(dial_routes.build_dial_list_blueprint)
        self.assertIn("by_operator=bool(payload.get('by_operator'))", route)

    def test_schema_has_cancellation_columns(self):
        ddl = dial_schema.DDL
        for column in ('operator_hangup_at', 'cancelled', 'leg_sec'):
            self.assertTrue(any(f'ADD COLUMN IF NOT EXISTS {column}' in s for s in ddl), column)

    def test_cancelled_attempt_is_not_counted(self):
        finish = inspect.getsource(dial_service.DialListService._finish_attempt)
        # Отмена решается ДО закрытия строки и не доходит до _close_assignment/_touch_lead.
        cancel_at = finish.index('self._cancel_attempt(')
        self.assertLess(cancel_at, finish.index('self._close_assignment('))
        self.assertIn('result != "answered"', finish[:cancel_at + 200])
        cancel = inspect.getsource(dial_service.DialListService._cancel_attempt)
        self.assertIn('SET cancelled = TRUE', cancel)
        self.assertIn('GREATEST(attempts - 1, 0)', cancel)
        self.assertNotIn('_touch_lead', cancel)
        # Поздний откат: строку и выдачу открываем обратно, лид — минус попытка.
        self.assertIn("SET state = 'issued', result = '', done_at = NULL", cancel)
        self.assertIn('SET closed_at = NULL', cancel)
        self.assertIn('GREATEST(attempts_total - 1, 0)', cancel)
        # Отвеченный позже разговор возвращает попытку в счёт.
        self.assertIn('_uncancel_attempt', finish)

    def test_cancelled_attempt_takes_no_outcome(self):
        src = inspect.getsource(dial_service.DialListService.set_attempt_outcome)
        self.assertIn('t.cancelled, t.operator_hangup_at', src)
        self.assertIn('итог не ставится', src)
        self.assertIn('Дождитесь исхода от АТС', src)
        pending = inspect.getsource(dial_service.DialListService._pending_outcome)
        self.assertIn('NOT t.cancelled', pending)
        self.assertIn('t.operator_hangup_at IS NOT NULL AND t.state = \'finished\'', pending)
        self.assertIn('ANSWERED_SQL', pending)

    def test_outcome_decision_rules(self):
        svc = dial_service.DialListService(db=None)

        class _Cur:
            def __init__(self, row):
                self.row = row

            def execute(self, *_a, **_k):
                pass

            def fetchone(self):
                return self.row

        # (state, disposition, cancelled, operator_hangup_at, leg_answered_at, outcome_id)
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'CANCEL', True, 't', 't', None)), 'x'), (True, False))
        self.assertEqual(svc._outcome_decision(_Cur(('ended', '', False, None, 't', None)), 'x'), (False, True))
        self.assertEqual(svc._outcome_decision(_Cur(('ended', '', False, 't', 't', None)), 'x'), (False, None))
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'ANSWERED', False, 't', 't', None)), 'x'), (False, True))
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'NOANSWER', False, 't', 't', None)), 'x'), (False, False))
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'ANSWERED', False, None, 't', 'o')), 'x'), (False, False))
        self.assertEqual(svc._outcome_decision(_Cur(('failed', '', False, None, None, None)), 'x'), (False, False))
        self.assertEqual(svc._outcome_decision(_Cur(None), 'x'), (False, False))


class PortionStaysVisibleTests(unittest.TestCase):
    """Обработанная пачка остаётся на экране с итогами, пока не взята следующая."""

    def test_state_falls_back_to_last_closed_portion(self):
        state = inspect.getsource(dial_service.DialListService.get_state)
        self.assertIn('_last_portion', state)
        self.assertIn('"closed"', state)
        last = inspect.getsource(dial_service.DialListService._last_portion)
        self.assertIn('ORDER BY closed_at IS NULL DESC, issued_at DESC', last)
        self.assertNotIn('phone_norm', last)
        # Выдача следующей — по-прежнему только через открытую порцию без issued.
        nxt = inspect.getsource(dial_service.DialListService.issue_next_portion)
        self.assertIn('_open_portion', nxt)
        self.assertIn("state = 'issued'", nxt)
        items = inspect.getsource(dial_service.DialListService._portion_items)
        self.assertIn('"cancelled"', items)

    def test_requeue_reopens_row_in_operator_latest_portion(self):
        # «Вернуть в список» по уже обработанному водителю: строка в последней выдаче
        # оператора снова issued и выдача открыта — иначе оператор видел бы старый итог
        # и не мог позвонить до следующей порции (владелец, 24.09.2026).
        src = inspect.getsource(dial_service.DialListService.requeue_lead)
        self.assertIn('_reopen_in_latest_portion', src)
        self.assertIn('reopened_for_operator', src)
        reopen = inspect.getsource(dial_service.DialListService._reopen_in_latest_portion)
        self.assertIn("a.state = 'done'", reopen)
        self.assertIn('ORDER BY issued_at DESC LIMIT 1', reopen)      # только последняя выдача оператора
        self.assertIn("SET state = 'issued', result = '', done_at = NULL", reopen)
        self.assertIn('SET closed_at = NULL', reopen)
        self.assertNotIn('phone_norm', reopen)
        # Строку, которая и так у оператора, возврат не дублирует (уникальный индекс).
        self.assertIn("Строка сейчас у оператора", src)


class OperatorProgressTests(unittest.TestCase):
    """Вкладка «Мой прогресс» на телефоне: счётчики оператора за месяц и за сегодня."""

    def test_progress_route_is_operator_only_and_counts_only(self):
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        operator_part = src[src.index('# ── телефон оператора'):src.index('# ── руководитель')]
        self.assertIn("/api/operator/dial_list/progress", operator_part)
        progress = inspect.getsource(dial_service.DialListService.operator_progress)
        for forbidden in ('phone_norm', 'full_name', 'dial_list_leads'):
            self.assertNotIn(forbidden, progress)
            self.assertNotIn(forbidden, dial_service.DialListService._PROGRESS_SQL)
            self.assertNotIn(forbidden, dial_service.DialListService._PROGRESS_OUTCOMES_SQL)
            self.assertNotIn(forbidden, dial_service.DialListService._PROGRESS_SUBTYPES_SQL)
        # Отменённые до ответа попытки не входят в «попытки», дозвон — по диспозициям Binotel.
        self.assertIn('WHERE NOT cancelled', dial_service.DialListService._PROGRESS_SQL)
        self.assertIn('disp IN %(answered)s', dial_service.DialListService._PROGRESS_SQL)
        self.assertIn('"today_stats"', progress)
        self.assertIn('"outcomes"', progress)
        self.assertIn('current_period()', progress)


class ScriptAndLiveTests(unittest.TestCase):
    """Скрипт разговора (владелец 25.09.2026) и живое состояние попытки для карточки звонка."""

    def test_script_routes_split_between_manager_and_operator(self):
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        operator_part = src[src.index('# ── телефон оператора'):src.index('# ── руководитель')]
        manager_part = src[src.index('# ── руководитель'):]
        self.assertIn("/api/operator/dial_list/script", operator_part)
        self.assertIn("/api/operator/dial_list/attempts/<attempt_id>/live", operator_part)
        self.assertIn("/api/dial_list/departments/<int:department_id>/script", manager_part)
        script_chunk = manager_part[manager_part.index("departments/<int:department_id>/script"):]
        self.assertIn('_manager(department_id)', script_chunk[:600])
        for name in ('operator_script', 'attempt_live', '_script_rows', 'save_script'):
            body = inspect.getsource(getattr(dial_service.DialListService, name))
            self.assertNotIn('phone_norm', body, name)
            self.assertNotIn('dial_list_leads', body, name)

    def test_script_schema_and_limits(self):
        ddl = ' '.join(dial_schema.DDL)
        self.assertIn('CREATE TABLE IF NOT EXISTS dial_list_scripts', ddl)
        self.assertIn('CREATE TABLE IF NOT EXISTS dial_list_script_questions', ddl)
        self.assertIn('version INTEGER NOT NULL DEFAULT 1', ddl)
        save = inspect.getsource(dial_service.DialListService.save_script)
        self.assertIn('version = dial_list_scripts.version + 1', save)   # телефон перечитывает по версии
        self.assertIn('SET is_active = FALSE', save)                     # вопросы выключаются, не удаляются
        svc = dial_service.DialListService(db=None)
        with self.assertRaises(dial_service.DialListError):
            svc.save_script(1, "not a dict")
        with self.assertRaises(dial_service.DialListError):
            svc.save_script(1, {"body": "x" * (dial_service.SCRIPT_BODY_MAX + 1)})
        with self.assertRaises(dial_service.DialListError):
            svc.save_script(1, {"body": "", "questions": [{"question": "", "answer": "a"}]})
        with self.assertRaises(dial_service.DialListError):
            svc.save_script(1, {"body": "", "questions": [{"question": "q"}] * (dial_service.SCRIPT_QUESTIONS_MAX + 1)})
        state = inspect.getsource(dial_service.DialListService.get_state)
        self.assertIn('"script_version"', state)

    def test_live_status_is_throttled_and_uses_binotel(self):
        live = inspect.getsource(dial_service.DialListService.attempt_live)
        self.assertIn('LIVE_MIN_INTERVAL_SEC', live)
        self.assertIn('call_details(', live)
        self.assertIn('"talking": live == "ONLINE"', live)
        self.assertIn('_finish_by_call', live)     # известный финал закрывает попытку, как phone_event
        self.assertGreaterEqual(dial_service.LIVE_MIN_INTERVAL_SEC, 3)


class ScriptAITests(unittest.TestCase):
    """«Создать с ИИ» / «Оформить с ИИ» (владелец 25.09.2026): ручка руководителя, наша разметка,
    без данных водителей; сеть подменяется."""

    def _fake_post(self, answer):
        calls = []

        def post(url, headers, payload):
            calls.append((url, headers, payload))
            text = json.dumps(answer, ensure_ascii=False)
            return 200, {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        return post, calls

    @staticmethod
    def _load_ai_service():
        """ai_feedback.service тянет database (time.tzset — только Linux, плюс пул к базе):
        подменяем зависимость заглушкой, как в test_birthday_greeting_ai_request."""
        import importlib
        import types
        if 'ai_feedback.service' in sys.modules:
            return sys.modules['ai_feedback.service']
        stub = types.ModuleType('database')
        stub.db = unittest.mock.MagicMock()
        stub.IT_TICKET_CATALOG = {}
        with unittest.mock.patch.dict(sys.modules, {'database': stub}):
            module = importlib.import_module('ai_feedback.service')
        # patch.dict на выходе возвращает sys.modules как было — вместе с только что
        # загруженным модулем. Кладём обратно, иначе dial_list.ai импортирует его заново
        # уже без заглушки.
        sys.modules['ai_feedback.service'] = module
        import ai_feedback
        ai_feedback.service = module
        return module

    def setUp(self):
        ai = self._load_ai_service()
        self._patch = unittest.mock.patch.object(ai, "GEMINI_API_KEY", "test-key")
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_generate_returns_body_and_questions_with_markup_rules_in_prompt(self):
        from dial_list import ai as script_ai
        post, calls = self._fake_post({"body": "# Приветствие\n**Здравствуйте**", "questions": [
            {"question": "Сколько платят?", "answer": "==от 300 000=="}, {"question": "", "answer": "x"}]})
        result = script_ai.generate_script("Звоним водителям, приглашаем в парк", post=post)
        self.assertEqual(result["body"], "# Приветствие\n**Здравствуйте**")
        self.assertEqual(len(result["questions"]), 1)          # вопрос без текста отброшен
        prompt = calls[0][2]["contents"][0]["parts"][0]["text"]
        for marker in ("# Заголовок", "**жирный**", "==выделение==", "- пункт", "> текст", "---"):
            self.assertIn(marker, prompt)
        self.assertIn("responseSchema", calls[0][2]["generationConfig"])
        self.assertNotIn("phone", prompt.lower())

    def test_polish_keeps_text_and_rejects_empty(self):
        from dial_list import ai as script_ai
        post, calls = self._fake_post({"text": "# Скрипт\nТекст"})
        self.assertEqual(script_ai.polish_text("просто текст без разметки", post=post), "# Скрипт\nТекст")
        with self.assertRaises(script_ai.ScriptAIError) as ctx:
            script_ai.polish_text("", post=post)
        self.assertEqual(ctx.exception.status, 400)
        with self.assertRaises(script_ai.ScriptAIError):
            script_ai.generate_script("мало", post=post)

    def test_model_chain_falls_through_on_overload(self):
        from dial_list import ai as script_ai
        seen = []

        def post(url, headers, payload):
            seen.append(url)
            if len(seen) == 1:
                return 503, {}
            return 200, {"candidates": [{"content": {"parts": [{"text": '{"text": "ok"}'}]}}]}
        self.assertEqual(script_ai.polish_text("текст для оформления", post=post), "ok")
        self.assertEqual(len(seen), 2)                          # первая модель перегружена → вторая

    def test_ai_route_is_manager_only_and_saves_nothing(self):
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        manager_part = src[src.index('# ── руководитель'):]
        chunk = manager_part[manager_part.index("departments/<int:department_id>/script/ai"):]
        self.assertIn('_manager(department_id)', chunk[:500])
        self.assertNotIn("/api/operator/", chunk[:500])
        service = inspect.getsource(dial_service.DialListService.script_ai)
        for forbidden in ('phone_norm', 'dial_list_leads', 'INSERT', 'UPDATE'):
            self.assertNotIn(forbidden, service)
        from dial_list import ai as script_ai
        module = inspect.getsource(script_ai)
        self.assertNotIn('phone_norm', module)
        self.assertNotIn('x-goog-api-key', module)              # секрет остаётся в ai_feedback.service


class OutcomeSubtypesTests(unittest.TestCase):
    """Типы итога (запрос владельца 29.09.2026): «Отказ» → «Дорого», «Уже работает в
    другом парке»… Руководитель заводит типы на сайте, оператор выбирает тип после
    итога, журнал показывает и фильтрует по типу, «Мой прогресс» считает по типам."""

    R = '11111111-1111-1111-1111-111111111111'    # «Отказ»
    C = '22222222-2222-2222-2222-222222222222'    # «Заинтересован»
    S1 = '33333333-3333-3333-3333-333333333333'   # «Дорого»
    S2 = '44444444-4444-4444-4444-444444444444'   # «Далеко»
    S3 = '55555555-5555-5555-5555-555555555555'   # выключенный «Старый»

    # ── схема ──────────────────────────────────────────────────────────────
    def test_schema_table_before_alter_before_indexes(self):
        ddl = dial_schema.DDL
        outcomes_at = next(i for i, s in enumerate(ddl) if 'CREATE TABLE IF NOT EXISTS dial_list_outcomes ' in s)
        table_at = next(i for i, s in enumerate(ddl) if 'CREATE TABLE IF NOT EXISTS dial_list_outcome_subtypes' in s)
        alter_at = next(i for i, s in enumerate(ddl) if 'ADD COLUMN IF NOT EXISTS outcome_subtype_id UUID' in s)
        first_index = next(i for i, s in enumerate(ddl) if 'CREATE INDEX' in s or 'CREATE UNIQUE INDEX' in s)
        # Таблица — после итогов (FK) и до ALTER, который на неё ссылается; всё — до индексов.
        self.assertLess(outcomes_at, table_at)
        self.assertLess(table_at, alter_at)
        self.assertLess(alter_at, first_index)
        table = ddl[table_at]
        for fragment in ('id UUID PRIMARY KEY DEFAULT gen_random_uuid()',
                         'outcome_id UUID NOT NULL REFERENCES dial_list_outcomes(id) ON DELETE CASCADE',
                         'department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE',
                         'name VARCHAR(64) NOT NULL', 'position SMALLINT NOT NULL DEFAULT 0',
                         'is_active BOOLEAN NOT NULL DEFAULT TRUE'):
            self.assertIn(fragment, table)
        self.assertNotIn('color', table)        # цвет — у итога
        self.assertNotIn('requeue', table)      # «перезвонить» — у итога
        self.assertIn('REFERENCES dial_list_outcome_subtypes(id) ON DELETE SET NULL', ddl[alter_at])
        indexes = ddl[first_index:]
        self.assertIn('CREATE INDEX IF NOT EXISTS idx_dial_list_outcome_subtypes_outcome '
                      'ON dial_list_outcome_subtypes(outcome_id, position)', indexes)
        self.assertIn('CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_outcome_subtype '
                      'ON dial_list_attempts(outcome_subtype_id)', indexes)
        # Подделки курсоров в тестах узнают итоги по 'FROM dial_list_outcomes' — имя
        # таблицы типов не должно под это подпадать.
        self.assertNotIn('FROM dial_list_outcomes', 'FROM dial_list_outcome_subtypes')

    # ── справочник: сохранение ───────────────────────────────────────────────
    def test_save_outcomes_validates_subtypes_before_db(self):
        svc = dial_service.DialListService(db=None)
        base = {"name": "Отказ", "color": "#FF3B30"}
        cases = [
            ({"subtypes": "Дорого"}, 'ожидается список'),
            ({"subtypes": None}, 'ожидается список'),
            ({"subtypes": ["Дорого"]}, 'объект'),
            ({"subtypes": [{"name": "   "}]}, 'должно быть название'),
            ({"subtypes": [{"name": "Дорого"}, {"name": " дорого "}]}, 'повторяется'),
            ({"subtypes": [{"name": f"Тип {n}"} for n in range(dial_service.SUBTYPES_MAX + 1)]}, 'не больше 20'),
        ]
        for extra, text in cases:
            with self.assertRaises(dial_service.DialListError, msg=text) as ctx:
                svc.save_outcomes(1, [dict(base, **extra)])
            self.assertIn(text, str(ctx.exception))
            self.assertIn('Отказ', str(ctx.exception))      # сообщение называет итог
            self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(dial_service.SUBTYPES_MAX, 20)
        # 20 типов (выключенные — в счёт) проходят проверку и доходят до базы (здесь её нет).
        with self.assertRaises(AttributeError):
            svc.save_outcomes(1, [dict(base, subtypes=[{"name": f"Тип {n}", "is_active": n % 2 == 0}
                                                       for n in range(20)])])
        cleaned = svc._clean_subtypes('Отказ', [{"name": "  " + "я" * 80}, {"name": "Б", "is_active": False}])
        self.assertEqual(len(cleaned[0]["name"]), dial_service.SUBTYPE_NAME_MAX)
        self.assertEqual([c["is_active"] for c in cleaned], [True, False])

    def _save_cursor(self, stored=('Дорого', 'Далеко')):
        import uuid
        return _RoutedCursor([
            ('SELECT id FROM dial_list_outcomes WHERE', {'all': [(self.R,)]}),
            # Типы есть только у «Отказа».
            ('SELECT id FROM dial_list_outcome_subtypes',
             {'all': lambda params: [(self.S1,), (self.S2,)] if params[0] == self.R else []}),
            # Названия всех типов итога в базе после сохранения (с выключенными).
            ('SELECT name FROM dial_list_outcome_subtypes', {'all': [(n,) for n in stored]}),
            ('INSERT INTO dial_list_outcomes', {'one': lambda: (uuid.uuid4(),)}),
            ('INSERT INTO dial_list_outcome_subtypes', {'one': lambda: (uuid.uuid4(),)}),
            ('FROM dial_list_outcomes o', {'all': [(self.R, 'Отказ', '#FF3B30', 1, False, True, 0, [])]}),
        ])

    def test_save_outcomes_syncs_nested_subtypes(self):
        cur = self._save_cursor()
        svc = dial_service.DialListService(db=_routed_db(cur))
        res = svc.save_outcomes(1, [
            {"id": self.R, "name": "Отказ", "color": "#FF3B30",
             "subtypes": [{"id": self.S2, "name": "Далеко!", "is_active": True}, {"name": "Не интересно"}]},
            {"name": "Новый", "color": "#000000", "subtypes": [{"name": "Первый", "is_active": False}]},
            {"name": "Без ключа", "color": "#000000"},
        ])
        self.assertEqual(res[0]["subtypes"], [])
        # S2 — переименован и стал первым; новый тип — вторым; S1 пропал из списка — выключен.
        upd = cur.sql_with('UPDATE dial_list_outcome_subtypes\n')
        self.assertEqual([p for _, p in upd], [('Далеко!', 1, True, self.S2, self.R)])
        ins = [p for _, p in cur.sql_with('INSERT INTO dial_list_outcome_subtypes')]
        self.assertEqual(ins[0], (self.R, 1, 'Не интересно', 2, True))
        new_outcome_id = ins[1][0]
        self.assertNotEqual(new_outcome_id, self.R)          # типы нового итога — к нему
        self.assertEqual(ins[1][1:], (1, 'Первый', 1, False))
        off = cur.sql_with('SET is_active = FALSE')
        sub_off = [p for sql, p in off if 'dial_list_outcome_subtypes' in sql]
        self.assertEqual(sub_off, [(self.R, [self.S1])])
        # «Без ключа» — типы не читались и не трогались.
        reads = [p for _, p in cur.sql_with('SELECT id FROM dial_list_outcome_subtypes')]
        self.assertEqual([p[0] for p in reads], [self.R, new_outcome_id])

    def test_save_outcomes_rejects_stale_tab_overflowing_stored_subtypes(self):
        # Устаревшая вкладка: в запросе ≤ 20 типов, но вместе с выключенными
        # (пропавшими из её списка) в базе их больше 20 или названия повторяются.
        # Сохранение отвергается — иначе GET → PUT без правок больше не прошёл бы.
        for stored, text in (([f"Тип {n}" for n in range(21)], 'не больше 20'),
                             (['Дорого', 'Новый', 'ДОРОГО'], 'повторяется')):
            cur = self._save_cursor(stored=stored)
            svc = dial_service.DialListService(db=_routed_db(cur))
            with self.assertRaises(dial_service.DialListError, msg=text) as ctx:
                svc.save_outcomes(1, [{"id": self.R, "name": "Отказ", "color": "#FF3B30",
                                       "subtypes": [{"name": "Новый"}]}])
            self.assertIn(text, str(ctx.exception))
            self.assertIn('Отказ', str(ctx.exception))
            self.assertIn('обновите страницу', str(ctx.exception))
            self.assertEqual(ctx.exception.status, 400)
            names = cur.sql_with('SELECT name FROM dial_list_outcome_subtypes')
            self.assertEqual([p for _, p in names], [(self.R,)])

    def test_save_outcomes_without_subtypes_key_leaves_types_untouched(self):
        # Старая сборка сайта шлёт итоги без subtypes — типы не должны стереться.
        cur = self._save_cursor()
        svc = dial_service.DialListService(db=_routed_db(cur))
        svc.save_outcomes(1, [{"id": self.R, "name": "Отказ", "color": "#FF3B30", "requeue": False}])
        for fragment in ('INSERT INTO dial_list_outcome_subtypes', 'UPDATE dial_list_outcome_subtypes',
                         'SELECT id FROM dial_list_outcome_subtypes'):
            self.assertEqual(cur.sql_with(fragment), [], fragment)

    def test_outcome_rows_carry_subtypes(self):
        rows = [(self.R, 'Отказ', '#FF3B30', 1, False, True, 7,
                 '[{"id": "%s", "name": "Дорого", "position": 1, "is_active": true, "used": 5}]' % self.S1),
                (self.C, 'Заинтересован', '#34C759', 2, False, True, 0, None)]
        cur = _RoutedCursor([('FROM dial_list_outcomes o', {'all': rows})])
        svc = dial_service.DialListService(db=None)
        out = svc._outcome_rows(cur, 1)
        self.assertEqual(out[0]["subtypes"], [{"id": self.S1, "name": "Дорого", "position": 1,
                                               "is_active": True, "used": 5}])
        self.assertEqual(out[1]["subtypes"], [])
        self.assertNotIn('s.is_active', cur.executed[-1][0].split('WHERE s.outcome_id = o.id', 1)[1].split(')')[0])
        svc._outcome_rows(cur, 1, active_only=True)
        sql = cur.executed[-1][0]
        self.assertIn('WHERE s.outcome_id = o.id AND s.is_active', sql)   # телефону — только включённые
        self.assertIn('ORDER BY s.is_active DESC, s.position, s.created_at', sql)
        self.assertIn('t2.outcome_subtype_id = s.id', sql)                  # used у типа

    # ── телефон: выбор итога ────────────────────────────────────────────────
    def _outcome_svc(self, subtype_row=None, has_active=False):
        cur = _RoutedCursor([
            ('FOR UPDATE OF t', {'one': ('ended', 'lead-1', None, False, None, '')}),
            ('SELECT id, name FROM dial_list_outcome_subtypes', {'one': subtype_row}),
            ('SELECT 1 FROM dial_list_outcome_subtypes', {'one': (1,) if has_active else None}),
            ('FROM dial_list_outcomes', {'one': (self.R, 'Отказ', '#FF3B30', False)}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        svc.operator_context = lambda uid: {"user_id": uid, "department_id": 1}
        return svc, cur

    def _update_params(self, cur):
        updates = cur.sql_with('UPDATE dial_list_attempts')
        return [p for _, p in updates]

    def test_old_phone_without_subtype_key_saves_outcome_without_type(self):
        # Телефоны ≤ 3.22.28 ключ не шлют: итог сохраняется, даже если у итога есть типы.
        svc, cur = self._outcome_svc(has_active=True)
        res = svc.set_attempt_outcome(7, 'att-1', self.R, ' ок ')
        self.assertEqual(self._update_params(cur), [(self.R, None, 'ок', 'att-1')])
        self.assertEqual(cur.sql_with('dial_list_outcome_subtypes'), [])
        self.assertIsNone(res["outcome"]["subtype"])
        self.assertEqual(res["outcome"]["name"], 'Отказ')

    def test_chosen_active_type_is_saved_with_outcome(self):
        svc, cur = self._outcome_svc(subtype_row=(self.S1, 'Дорого'), has_active=True)
        res = svc.set_attempt_outcome(7, 'att-1', self.R, '', subtype_id=self.S1.upper())
        lookup = cur.sql_with('SELECT id, name FROM dial_list_outcome_subtypes')
        self.assertEqual(lookup[0][1], (self.S1, self.R, 1))                 # тип этого итога и отдела
        self.assertIn('AND is_active', lookup[0][0])
        self.assertEqual(self._update_params(cur), [(self.R, self.S1, '', 'att-1')])
        self.assertEqual(res["outcome"]["subtype"], {"id": self.S1, "name": "Дорого"})
        self.assertEqual(set(res["outcome"]), {"id", "name", "color", "requeue", "subtype"})

    def test_bad_or_foreign_type_is_rejected(self):
        for bad in ('abc', 123, 'ffffffff-ffff'):
            svc, cur = self._outcome_svc(has_active=True)
            with self.assertRaises(dial_service.DialListError) as ctx:
                svc.set_attempt_outcome(7, 'att-1', self.R, '', subtype_id=bad)
            self.assertEqual((str(ctx.exception), ctx.exception.status),
                             ("Такого типа итога нет — обновите список", 400))
            self.assertEqual(cur.sql_with('dial_list_outcome_subtypes'), [])   # кривой id до базы не доходит
            self.assertEqual(self._update_params(cur), [])
        # Правильный uuid, но не тип этого итога / выключен — тоже 400.
        svc, cur = self._outcome_svc(subtype_row=None, has_active=True)
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.set_attempt_outcome(7, 'att-1', self.R, '', subtype_id=self.S2)
        self.assertEqual((str(ctx.exception), ctx.exception.status), ("Такого типа итога нет — обновите список", 400))
        self.assertEqual(self._update_params(cur), [])

    def test_type_is_required_when_key_sent_and_outcome_has_active_types(self):
        for empty in (None, '', '   '):
            svc, cur = self._outcome_svc(has_active=True)
            with self.assertRaises(dial_service.DialListError) as ctx:
                svc.set_attempt_outcome(7, 'att-1', self.R, '', subtype_id=empty)
            self.assertEqual((str(ctx.exception), ctx.exception.status), ("Выберите тип итога", 400))
            self.assertEqual(self._update_params(cur), [])
        # У итога нет включённых типов — без типа можно, тип пишется NULL.
        svc, cur = self._outcome_svc(has_active=False)
        res = svc.set_attempt_outcome(7, 'att-1', self.R, '', subtype_id=None)
        self.assertEqual(self._update_params(cur), [(self.R, None, '', 'att-1')])
        self.assertIsNone(res["outcome"]["subtype"])

    def test_existing_outcome_checks_come_first(self):
        # Отменённая попытка — прежняя 409, до любых проверок типа.
        svc, cur = self._outcome_svc(has_active=True)
        cur.rules[0] = ('FOR UPDATE OF t', {'one': ('finished', 'lead-1', None, True, 't', 'CANCEL')})
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.set_attempt_outcome(7, 'att-1', self.R, '', subtype_id='abc')
        self.assertEqual(ctx.exception.status, 409)
        src = inspect.getsource(dial_service.DialListService.set_attempt_outcome)
        # Тип пишется тем же UPDATE, что и итог, — повторный выбор не оставит чужой тип.
        self.assertIn('SET outcome_id = %s, outcome_subtype_id = %s', src)
        self.assertLess(src.index('Такого итога нет'), src.index('Такого типа итога нет'))

    def test_outcome_route_passes_subtype_key_presence(self):
        seen = []

        class Svc:
            def set_attempt_outcome(self, user_id, attempt_id, outcome_id, comment='',
                                    subtype_id=dial_service.SUBTYPE_NOT_SENT):
                seen.append(subtype_id)
                return {"attempt_id": attempt_id}

        client = self._client(Svc())
        url = '/api/operator/dial_list/attempts/att-1/outcome'
        self.assertEqual(client.post(url, json={"outcome_id": self.R}).status_code, 200)
        client.post(url, json={"outcome_id": self.R, "subtype_id": None})
        client.post(url, json={"outcome_id": self.R, "subtype_id": self.S1})
        self.assertIs(seen[0], dial_service.SUBTYPE_NOT_SENT)
        self.assertEqual(seen[1:], [None, self.S1])

    def _client(self, svc):
        import flask
        requester = (5, 'Руководитель', 'x', 'admin', None, None, None, 'admin')
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (5, requester, None), is_admin_role=lambda role: True,
            headed_department_ids=lambda rid: [], service=svc)
        app = flask.Flask('dial_list_test')
        app.register_blueprint(bp)
        return app.test_client()

    def test_journal_route_validates_subtype_filter(self):
        seen = []

        class Svc:
            def manager_scope(self, is_admin, heads, login=None):
                return None

            def leads_journal(self, department_id, **kwargs):
                seen.append(kwargs)
                return {}

        client = self._client(Svc())
        url = '/api/dial_list/departments/1/leads'
        # Без итога тип не учитывается — даже кривой.
        self.assertEqual(client.get(url + '?outcome_subtype_id=zzz').status_code, 200)
        self.assertIsNone(seen[-1]['outcome_subtype_id'])
        resp = client.get(f'{url}?outcome_id={self.R}&outcome_subtype_id=zzz')
        self.assertEqual((resp.status_code, resp.get_json()), (404, {"error": "Тип итога не найден"}))
        client.get(f'{url}?outcome_id={self.R}&outcome_subtype_id=NONE')
        self.assertEqual(seen[-1]['outcome_subtype_id'], 'none')
        client.get(f'{url}?outcome_id={self.R}&outcome_subtype_id={self.S1.upper()}')
        self.assertEqual((seen[-1]['outcome_id'], seen[-1]['outcome_subtype_id']), (self.R, self.S1))

    # ── телефон: состояние, вкладки, прогресс ────────────────────────────────
    def test_state_sends_active_types_and_last_attempt_type(self):
        from datetime import date
        portion_item = ('asg-1', 1, 'Иванов', 'done', 'answered', 1, None, 'att-1', 'finished', 'ANSWER', None,
                        'gc', 'Отказ', '#FF3B30', False, None, 'lead-1', 'Дорого')
        plain_item = ('asg-2', 2, 'Петров', 'issued', '', 0, None, None, None, None, None,
                      None, None, None, None, None, 'lead-2', None)
        outcomes = [(self.R, 'Отказ', '#FF3B30', 1, False, True, 3,
                     [{"id": self.S1, "name": "Дорого", "position": 1, "is_active": True, "used": 2}]),
                    (self.C, 'Заинтересован', '#34C759', 2, False, True, 0, [])]
        cur = _RoutedCursor([
            ('FROM dial_list_portions', {'one': ('portion-1', 20, None, None)}),
            ('WHERE a.portion_id = %s', {'all': [portion_item, plain_item]}),
            ('FROM dial_list_outcomes o', {'all': outcomes}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        svc.operator_context = lambda uid: {
            "user_id": uid, "department_id": 1, "name": "Оператор", "internal_number": "901",
            "settings": {"portion_size": 20, "period": "2026-09-01", "period_label": "Сентябрь 2026",
                         "_period": date(2026, 9, 1), "retry_after_hours": 24, "max_attempts": 3}}
        svc.reconcile = lambda ctx: None
        state = svc.get_state(7)
        self.assertEqual(state["outcomes"][0], {"id": self.R, "name": "Отказ", "color": "#FF3B30", "requeue": False,
                                                "subtypes": [{"id": self.S1, "name": "Дорого"}]})
        self.assertEqual(state["outcomes"][1]["subtypes"], [])
        items = state["portion"]["items"]
        self.assertEqual(items[0]["last_attempt"]["outcome_subtype_name"], 'Дорого')
        self.assertEqual(items[0]["last_attempt"]["outcome_name"], 'Отказ')
        self.assertIsNone(items[1]["last_attempt"])
        self.assertIn('AND s.is_active', cur.sql_with('FROM dial_list_outcomes o')[0][0])

    def test_progress_nests_type_counts_under_outcomes(self):
        cur = _RoutedCursor([
            ('AS issued', {'one': (0,) * 10}),
            ('JOIN dial_list_outcome_subtypes s', {'all': [(self.R, self.S1, 'Дорого', 3, 1),
                                                           (self.R, self.S2, 'Далеко', 1, 0),
                                                           ('ghost', self.S3, 'Чужой', 1, 1)]}),
            ('JOIN dial_list_outcomes o ON o.id = t.outcome_id', {'all': [(self.R, 'Отказ', '#FF3B30', 5, 2),
                                                                          (self.C, 'Заинтересован', None, 1, 0)]}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        svc.operator_context = lambda uid: {"user_id": uid, "department_id": 1, "name": "Оператор"}
        res = svc.operator_progress(7)
        self.assertEqual(res["outcomes"][0]["subtypes"], [
            {"id": self.S1, "name": "Дорого", "count": 3, "count_today": 1},
            {"id": self.S2, "name": "Далеко", "count": 1, "count_today": 0}])
        self.assertEqual(res["outcomes"][1], {"id": self.C, "name": "Заинтересован", "color": "#8E8E93",
                                              "count": 1, "count_today": 0, "subtypes": []})
        # Те же попытки, что у счётчиков итогов: оператор и месяц; без типа — не показываются.
        sub_sql = dial_service.DialListService._PROGRESS_SUBTYPES_SQL
        for fragment in ("WHERE t.operator_id = %(operator_id)s",
                         "AND t.requested_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'",
                         "JOIN dial_list_outcomes o ON o.id = t.outcome_id"):
            self.assertIn(fragment, sub_sql)
            self.assertIn(fragment, dial_service.DialListService._PROGRESS_OUTCOMES_SQL)
        self.assertIn('JOIN dial_list_outcome_subtypes s ON s.id = t.outcome_subtype_id', sub_sql)
        self.assertIn('ORDER BY cnt DESC, s.name', sub_sql)

    # ── журнал и карточка ────────────────────────────────────────────────────
    def _journal_row(self, lead_id, outcome=None, subtype=None, total=None, rn=None, summary=None):
        r = [None] * dial_service.DialListService._JOURNAL_COLUMN_COUNT
        r[0], r[1], r[2], r[3], r[4], r[5], r[10], r[29] = lead_id, 1, 'Иванов', '77011234567', 'done', 1, 1, 'answered'
        if outcome:
            r[32], r[33], r[34] = outcome, 'Отказ', '#FF3B30'
        if subtype:
            r[37], r[38] = subtype, 'Дорого'
        r[35], r[36] = 'коммент', False
        return tuple(r) + (total, rn) + ((summary,) if summary is not None else ())

    def _journal(self, summary, page=(), **kwargs):
        cur = _RoutedCursor([
            ('counts.summary', {'all': list(page) or [(None,) * PAGE_WIDTH + (summary,)]}),
            ('FROM dial_list_outcome_subtypes s', {'all': [(self.S1, self.R, 'Дорого', True),
                                                           (self.S2, self.R, 'Далеко', True),
                                                           (self.S3, self.R, 'Старый', False)]}),
            ('FROM dial_list_outcomes', {'all': [(self.R, 'Отказ', '#FF3B30', True),
                                                 (self.C, 'Заинтересован', '#34C759', True)]}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        svc.department_settings = lambda d: {"max_attempts": 3, "retry_after_hours": 24, "period": "2026-09-01",
                                             "_period": dial_service.current_period()}
        return svc.leads_journal(1, **kwargs), cur

    def test_journal_rows_types_counts_and_total(self):
        columns = [c.strip() for c in dial_service.DialListService._JOURNAL_COLUMNS.split(',')]
        # Новые — в конце: типы итога, за ними подписание документов (29.09.2026).
        self.assertEqual(columns[37:39], ['last_outcome_subtype_id', 'last_outcome_subtype_name'])
        self.assertEqual(columns[39:], ['iin', 'sign_status', 'sign_checked_at', 'sign_error', 'signed_at',
                                        'success_attempt_id', 'success_operator_id', 'success_operator_name',
                                        'success_resolved_at'])
        # stage, outcome, subtype, for_stage, for_outcome
        summary = [['answered', self.R, self.S1, 2, 3], ['answered', self.R, None, 1, 1],
                   ['exhausted', self.R, self.S2, 0, 2], ['queue', None, None, 5, 5],
                   ['answered', self.C, None, 0, 4]]
        page = [self._journal_row('l1', self.R, self.S1, 17, 1, summary),
                self._journal_row('l2', self.R, None, 17, 2, summary),
                self._journal_row('l3', None, None, 17, 3, summary)]
        res, _ = self._journal(summary, page)
        self.assertEqual(res['total'], 17)                    # индекс total выведен из числа колонок
        self.assertEqual(res['items'][0]['outcome'], {"id": self.R, "name": "Отказ", "color": "#FF3B30",
                                                      "subtype": {"id": self.S1, "name": "Дорого"}})
        self.assertIsNone(res['items'][1]['outcome']['subtype'])
        self.assertIsNone(res['items'][2]['outcome'])
        refusal, interested = res['by_outcome']
        self.assertEqual(refusal['count'], 6)
        self.assertEqual(refusal['none_count'], 1)
        self.assertEqual(refusal['subtypes'], [
            {"id": self.S1, "name": "Дорого", "is_active": True, "count": 3},
            {"id": self.S2, "name": "Далеко", "is_active": True, "count": 2},
            {"id": self.S3, "name": "Старый", "is_active": False, "count": 0}])
        self.assertEqual((interested['count'], interested['subtypes'], interested['none_count']), (4, [], 4))
        self.assertEqual(res['by_stage']['answered'], 3)

    def test_journal_subtype_filter_is_part_of_outcome_filter(self):
        res, cur = self._journal([], outcome_id=self.R, outcome_subtype_id='none', stage='answered')
        sql, params = cur.executed[0]
        f_part = sql.split('f AS MATERIALIZED', 1)[1].split('page AS', 1)[0]
        page_part = sql.split('page AS', 1)[1].split('counts AS', 1)[0]
        self.assertNotIn('last_outcome_subtype_id IS NULL', f_part)
        self.assertIn('stage = %(stage)s AND last_outcome_id = %(outcome_id)s::uuid'
                      ' AND last_outcome_subtype_id IS NULL', page_part)
        # Полоса этапов — с фильтром типа, чипы итогов и типов — без него.
        self.assertIn('FILTER (WHERE last_outcome_id = %(outcome_id)s::uuid AND last_outcome_subtype_id IS NULL)'
                      ' AS for_stage', sql)
        self.assertIn('FILTER (WHERE stage = %(stage)s) AS for_outcome', sql)
        self.assertIn('GROUP BY stage, last_outcome_id, last_outcome_subtype_id', sql)
        self.assertNotIn('outcome_subtype_id', params)

        res, cur = self._journal([], outcome_id=self.R, outcome_subtype_id=self.S1)
        sql, params = cur.executed[0]
        self.assertIn('last_outcome_id = %(outcome_id)s::uuid AND last_outcome_subtype_id = '
                      '%(outcome_subtype_id)s::uuid', sql.split('page AS', 1)[1])
        self.assertEqual(params['outcome_subtype_id'], self.S1)

        # Без итога тип не фильтрует.
        res, cur = self._journal([], outcome_subtype_id=self.S1)
        sql, params = cur.executed[0]
        self.assertNotIn('last_outcome_subtype_id =', sql)
        self.assertNotIn('outcome_subtype_id', params)

    def test_lead_card_attempts_carry_type(self):
        header = self._journal_row('lead-1', self.R, self.S1, 1)
        base = ['att', None, 3, 'Оператор', '901', 'finished', 'ANSWER', 30, 5, '', 'webhook', None, None, None,
                True, 'asg', 'answered', 'done', None, self.R, 'Отказ', '#FF3B30', 'дорого', None, False, None, 0]
        cur = _RoutedCursor([
            ('l.id = %(lead_id)s', {'one': header}),
            ('os.id, os.name', {'all': [tuple(base + [self.S1, 'Дорого']), tuple(base + [None, None])]}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        svc.lead_department = lambda lid: 1
        svc.department_settings = lambda d: {"max_attempts": 3, "retry_after_hours": 24}
        card = svc.lead_card('lead-1')
        self.assertEqual(card['outcome']['subtype'], {"id": self.S1, "name": "Дорого"})
        self.assertEqual(card['attempts'][0]['outcome']['subtype'], {"id": self.S1, "name": "Дорого"})
        self.assertIsNone(card['attempts'][1]['outcome']['subtype'])
        self.assertIn('LEFT JOIN dial_list_outcome_subtypes os ON os.id = t.outcome_subtype_id',
                      cur.sql_with('os.id, os.name')[0][0])


class WorkedTabsTests(unittest.TestCase):
    """Вкладки итогов на телефоне (владелец, 25.09.2026): очередь остаётся очередью,
    обработанный водитель сразу уходит на вкладку своего итога, недозвон — на
    «Не дозвонились». Срок — текущий месяц."""

    def test_row_lands_on_exactly_one_place(self):
        tab = dial_service.worked_tab
        # Итог оператора — его вкладка, и важнее исхода АТС.
        self.assertEqual(tab('done', 'answered', 'o1', 'finished'), 'o1')
        self.assertEqual(tab('done', 'no_answer', 'o1', 'finished'), 'o1')
        # Итог поставлен, АТС исход ещё не прислала: строка уже не в очереди.
        self.assertEqual(tab('issued', '', 'o1', 'ended'), 'o1')
        # Руководитель вернул в список: строка issued при завершённой попытке с итогом — это очередь.
        self.assertIsNone(tab('issued', '', 'o1', 'finished'))
        # Разговор без итога ждёт окна итога — ни в «Не дозвонились», ни в очереди списка.
        self.assertIsNone(tab('done', 'answered', None, 'finished'))
        for result in ('busy', 'no_answer', 'other', 'failed'):
            self.assertEqual(tab('done', result, None, 'finished'), dial_service.WORKED_NO_ANSWER)
        # Не обработанная строка — в очереди.
        self.assertIsNone(tab('issued', '', None, None))
        self.assertIsNone(tab('issued', '', None, 'failed'))

    def test_route_is_operator_only_and_carries_no_phone(self):
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        operator_part = src[src.index('# ── телефон оператора'):src.index('# ── руководитель')]
        self.assertIn("/api/operator/dial_list/worked", operator_part)
        self.assertNotIn('phone_norm', dial_service.DialListService._WORKED_SQL)
        self.assertIn('current_period()', inspect.getsource(dial_service.DialListService.operator_worked))
        # По водителю — последняя строка этого оператора за месяц, как «Мой прогресс».
        sql = dial_service.DialListService._WORKED_SQL
        self.assertIn('DISTINCT ON (a.lead_id)', sql)
        self.assertIn('ORDER BY a.lead_id, a.created_at DESC', sql)
        self.assertIn("a.created_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'", sql)
        # Стык месяцев: пачка живёт до «Ещё», выданная 30-го и обработанная 1-го строка
        # не должна пропасть ни из очереди, ни с вкладок.
        self.assertIn("OR a.done_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'", sql)
        self.assertIn("OR a.state = 'issued'", sql)

    def test_portion_rows_name_the_driver_for_the_phone(self):
        items = inspect.getsource(dial_service.DialListService._portion_items)
        self.assertIn('"lead_id"', items)
        self.assertIn('a.lead_id', items)

    def test_operator_index_for_month_queries(self):
        ddl = ' '.join(dial_schema.DDL)
        self.assertIn('ON dial_list_assignments(operator_id, created_at)', ddl)

    def _worked(self, rows, outcomes, limit=None):
        import contextlib

        class Cursor:
            def __init__(self):
                self.sql = ''

            def execute(self, sql, params=None):
                self.sql = sql

            def fetchall(self):
                if 'FROM dial_list_outcomes o' in self.sql and 'DISTINCT ON' not in self.sql:
                    return outcomes
                return rows

        class Db:
            @contextlib.contextmanager
            def _get_cursor(self):
                yield Cursor()

        svc = dial_service.DialListService(db=Db())
        svc.operator_context = lambda uid: {"user_id": uid, "department_id": 1}
        with unittest.mock.patch.object(dial_service, 'WORKED_TAB_LIMIT', limit or 300):
            return svc.operator_worked(7)

    def test_tabs_follow_directory_and_newest_first(self):
        from datetime import datetime as dt, timezone as tz
        at = lambda h: dt(2026, 9, 24, h, 0, tzinfo=tz.utc)
        # id, name, color, position, requeue, is_active, used, subtypes (json_agg)
        outcomes = [('o1', 'Заинтересован', '#34C759', 1, False, True, 0, []),
                    ('o2', 'Отказ', '#FF3B30', 2, False, True, 0,
                     [{'id': 's1', 'name': 'Дорого', 'position': 1, 'is_active': True, 'used': 1}])]
        # s.id, lead, name, state, result, done_at, created_at, outcome_id, outcome_at, comment, t.state, o.name, o.color,
        # o.pos, l.status, тип итога
        rows = [
            ('a1', 'l1', 'Иванов', 'done', 'answered', at(9), at(8), 'o2', at(9), 'дорого', 'finished', 'Отказ', '#FF3B30', 2, 'done', 'Дорого'),
            ('a2', 'l2', 'Петров', 'done', 'answered', at(10), at(8), 'o2', at(11), '', 'finished', 'Отказ', '#FF3B30', 2, 'done', None),
            ('a3', 'l3', '', 'done', 'busy', at(12), at(8), None, None, 'старый', 'finished', None, None, None, 'in_progress', None),
            ('a4', 'l4', 'Сидоров', 'done', 'answered', at(13), at(8), 'old', at(13), '', 'finished', 'Думает', '#AF52DE', 9, 'done', None),
            # Исключил руководитель: строка закрыта без звонка — это не недозвон.
            ('a6', 'l6', 'Исключённый', 'done', 'other', at(14), at(8), None, None, '', None, None, None, None, 'excluded', None),
            ('a5', 'l5', 'В очереди', 'issued', '', None, at(8), None, None, '', None, None, None, None, 'in_progress', None),
        ]
        res = self._worked(rows, outcomes)
        # Включённые — в порядке справочника, даже пустые; выключенный — пока по нему есть люди; недозвон — последний.
        self.assertEqual([(t['name'], t['count']) for t in res['tabs']],
                         [('Заинтересован', 0), ('Отказ', 2), ('Думает', 1), ('Не дозвонились', 1)])
        self.assertEqual([i['full_name'] for i in res['items']], ['Сидоров', 'Без имени', 'Петров', 'Иванов'])
        busy = next(i for i in res['items'] if i['tab'] == dial_service.WORKED_NO_ANSWER)
        self.assertEqual(busy['comment'], '')                  # комментарий — только к итогу оператора
        self.assertEqual(busy['result'], 'busy')
        self.assertEqual(res['items'][2]['at_label'], '24.09, 16:00')   # время итога по Алматы
        self.assertNotIn('В очереди', [i['full_name'] for i in res['items']])
        self.assertNotIn('Исключённый', [i['full_name'] for i in res['items']])
        # Тип итога (29.09.2026) — в строке, вкладки по типам не появляются.
        self.assertEqual({i['full_name']: i['subtype_name'] for i in res['items']},
                         {'Сидоров': '', 'Без имени': '', 'Петров': '', 'Иванов': 'Дорого'})
        self.assertNotIn('Дорого', [t['name'] for t in res['tabs']])

    def test_tab_is_capped_but_count_is_true(self):
        from datetime import datetime as dt, timezone as tz
        outcomes = [('o1', 'Отказ', '#FF3B30', 1, False, True, 0, [])]
        rows = [(f'a{n}', f'l{n}', f'Водитель {n}', 'done', 'answered', None, dt(2026, 9, 2, tzinfo=tz.utc),
                 'o1', dt(2026, 9, 2, n % 24, tzinfo=tz.utc), '', 'finished', 'Отказ', '#FF3B30', 1, 'done', None)
                for n in range(5)]
        res = self._worked(rows, outcomes, limit=3)
        self.assertEqual(res['tabs'][0]['count'], 5)
        self.assertEqual(len(res['items']), 3)

    def test_at_label_today_and_other_days(self):
        from datetime import date, datetime as dt, timezone as tz
        now = dt(2026, 9, 25, 9, 5, tzinfo=tz.utc)             # 14:05 по Алматы
        self.assertEqual(dial_service.at_label(now, date(2026, 9, 25)), 'сегодня, 14:05')
        self.assertEqual(dial_service.at_label(now, date(2026, 9, 26)), '25.09, 14:05')
        self.assertEqual(dial_service.at_label(None, date(2026, 9, 25)), '')


if __name__ == '__main__':
    unittest.main()
