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
                         {'queue', 'waiting', 'issued', 'answered', 'exhausted', 'excluded'})
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

        # stage, last_outcome_id, for_stage (без фильтра этапа), for_outcome (без фильтра итога)
        summary = [['answered', str(refusal), 4, 4], ['answered', str(callback), 0, 2],
                   ['queue', None, 0, 7], ['exhausted', str(refusal), 1, 0]]

        class Cursor:
            def execute(self, sql, params=None):
                executed.append((sql, dict(params or {})))

            def fetchall(self):
                sql = executed[-1][0]
                if 'FROM dial_list_outcomes' in sql:
                    return [(refusal, 'Отказ', '#FF3B30', True), (callback, 'Перезвонить', '#FF9F0A', True)]
                if 'counts.summary' in sql:
                    # Пустая страница: одна строка, колонки страницы — NULL, сводка — в последней.
                    return [(None,) * 38 + (summary,)]
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
                return [(None,) * 38 + ([],)] if 'counts.summary' in executed[-1][0] else []

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
        self.assertEqual(len(routes), 6)
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
        outcomes = [('o1', 'Заинтересован', '#34C759', 1, False, True, 0),
                    ('o2', 'Отказ', '#FF3B30', 2, False, True, 0)]
        # s.id, lead, name, state, result, done_at, created_at, outcome_id, outcome_at, comment, t.state, o.name, o.color, o.pos
        rows = [
            ('a1', 'l1', 'Иванов', 'done', 'answered', at(9), at(8), 'o2', at(9), 'дорого', 'finished', 'Отказ', '#FF3B30', 2, 'done'),
            ('a2', 'l2', 'Петров', 'done', 'answered', at(10), at(8), 'o2', at(11), '', 'finished', 'Отказ', '#FF3B30', 2, 'done'),
            ('a3', 'l3', '', 'done', 'busy', at(12), at(8), None, None, 'старый', 'finished', None, None, None, 'in_progress'),
            ('a4', 'l4', 'Сидоров', 'done', 'answered', at(13), at(8), 'old', at(13), '', 'finished', 'Думает', '#AF52DE', 9, 'done'),
            # Исключил руководитель: строка закрыта без звонка — это не недозвон.
            ('a6', 'l6', 'Исключённый', 'done', 'other', at(14), at(8), None, None, '', None, None, None, None, 'excluded'),
            ('a5', 'l5', 'В очереди', 'issued', '', None, at(8), None, None, '', None, None, None, None, 'in_progress'),
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

    def test_tab_is_capped_but_count_is_true(self):
        from datetime import datetime as dt, timezone as tz
        outcomes = [('o1', 'Отказ', '#FF3B30', 1, False, True, 0)]
        rows = [(f'a{n}', f'l{n}', f'Водитель {n}', 'done', 'answered', None, dt(2026, 9, 2, tzinfo=tz.utc),
                 'o1', dt(2026, 9, 2, n % 24, tzinfo=tz.utc), '', 'finished', 'Отказ', '#FF3B30', 1, 'done') for n in range(5)]
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
