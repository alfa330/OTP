# -*- coding: utf-8 -*-
"""Раздел «Обзвон из телефона»: правила без базы и инварианты по исходникам.

Главный инвариант раздела — телефон оператора не получает номер водителя. Он
проверяется по исходникам: ни один запрос, собирающий ответ для оператора, не
выбирает phone_norm, а маршруты телефона не читают лид напрямую.
"""
import contextlib
import inspect
import json
import os
import re
import sys
import unittest
import unittest.mock
from datetime import date, datetime, timedelta, timezone

import psycopg2.errors

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


def _writes(statements):
    """Пишущие запросы среди выполненных (SELECT … FOR UPDATE записью не считается)."""
    return [sql for sql in statements if sql.lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE'))]


class _TxDb:
    """База-подделка с границами транзакций: каждый блок `with _get_cursor()` — своя
    транзакция, как в database.py. in_tx — идёт ли транзакция сейчас (по нему подделка
    клиента Binotel проверяет правило модуля «сеть — вне транзакций»); transactions —
    SQL каждой транзакции по порядку; rolled_back — откатилась ли она (блок вышел
    исключением, как rollback в database.py)."""

    def __init__(self, cursor):
        self.cursor = cursor
        self.in_tx = False
        self.transactions = []
        self.rolled_back = []

    @contextlib.contextmanager
    def _get_cursor(self):
        start = len(self.cursor.executed)
        self.in_tx = True
        failed = False
        try:
            yield self.cursor
        except BaseException:
            failed = True
            raise
        finally:
            self.in_tx = False
            self.transactions.append([sql for sql, _ in self.cursor.executed[start:]])
            self.rolled_back.append(failed)


class _FakeSignChecker:
    """Проверка подписания для сервиса — без Sapar. answers — сводки check_now по
    очереди (dict поверх пустой сводки или исключение); calls — с чем спрашивали и шла
    ли в это время транзакция (правило модуля: сеть — вне транзакций)."""

    EMPTY = {"checked": 0, "signed": [], "processing": [], "errors": 0, "systemic": 0, "successes": 0,
             "stopped": "", "elapsed_sec": 0.0}

    def __init__(self, db=None, configured=True, answers=()):
        self.db, self._configured, self.answers, self.calls = db, configured, list(answers), []

    def configured(self):
        return self._configured

    def check_now(self, leads, **kwargs):
        self.calls.append(dict(kwargs, leads=[tuple(x) for x in leads], in_tx=getattr(self.db, 'in_tx', None)))
        answer = self.answers.pop(0) if self.answers else {}
        if isinstance(answer, Exception):
            raise answer
        return dict(self.EMPTY, **answer)


class _CallDetailsClient:
    """Клиент Binotel для событий телефона: call-details по generalCallID. answer —
    ответ или исключение. inside_tx — шла ли транзакция при каждом запросе (assert тут
    не годится: сервис гасит любую ошибку call-details)."""

    def __init__(self, db, answer):
        self.db, self.answer = db, answer
        self.calls, self.inside_tx = [], []

    def call_details(self, general_call_id):
        self.calls.append(general_call_id)
        self.inside_tx.append(self.db.in_tx)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _OriginateClient:
    """Клиент Binotel для «Позвонить»: запоминает звонки и шла ли тогда транзакция."""

    def __init__(self, db):
        self.db, self.calls = db, []

    def originate_internal_to_external(self, internal_number, external_number, **extra):
        self.calls.append((internal_number, external_number, extra, self.db.in_tx))
        return 'gc-1'


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
        for name in ('_portion_items', '_active_attempt', 'get_state', 'issue_next_portion', 'operator_worked',
                     '_ring_timeout_sec', '_ring_timeout_event', '_operator_conversion', '_refuse_next_portion',
                     '_precheck_signatures', '_precheck_call_signature', 'recall_lead', '_recall_row'):
            src = inspect.getsource(getattr(dial_service.DialListService, name))
            self.assertNotIn('phone_norm', src, f'{name} выбирает номер — он уедет в телефон')
        for name in ('_RECALL_JOINS_SQL', '_RECALL_COLUMNS', '_POOL_CANDIDATES_SQL'):
            self.assertNotIn('phone_norm', getattr(dial_service.DialListService, name), name)
        # ИИН читает только проверка подписания — для Sapar; в ответы телефону он не идёт.
        for name in ('_RECALL_JOINS_SQL', '_RECALL_COLUMNS', '_WORKED_SQL'):
            self.assertNotIn('iin', getattr(dial_service.DialListService, name), name)

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
        users = [
            {"id": 10, "name": "Иван", "login": "ivan", "role": "operator", "sip_number": "", "status": ""},
            {"id": 11, "name": "Пётр", "login": "petr", "role": "operator", "sip_number": "902", "status": ""},
        ]
        svc.department_users = lambda dep: users
        svc.department_sip_server = lambda dep: "sip52.binotel.com"
        # Привязки сотрудников других отделов к линиям, замок линии и поиск того, кто её
        # держит, — своя тема (tests/test_dial_list_line_members.py); здесь привязок нет,
        # а линию держит тот, у кого она в SIP-настройках.
        svc.line_member = lambda uid: None
        svc._line_lock = lambda dep, number: contextlib.nullcontext(None)
        svc._line_holder = lambda cur, dep, number, exclude_user_id=None: next(
            (u for u in users if u["sip_number"] == number and u["id"] != exclude_user_id), None)
        svc._release_member = lambda cur, uid, changed_by=None: ""
        svc._release_dead_holders = lambda cur, dep, number, changed_by=None: None
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

    def test_enrolled_department_keeps_mode_off_and_sip_card_follows_the_mode(self):
        # 01.10.2026 Тез КЦ подключили к разделу, режим не включали — и «Настройки SIP»
        # заперли карточки всех его операторов: панель считала «отдел на обзвоне» по
        # самому списку отделов раздела. Подключение заводит строку с ВЫКЛЮЧЕННЫМ
        # режимом, поэтому признаком для карточки может быть только enabled.
        enroll = inspect.getsource(dial_service.DialListService.enroll_department)
        self.assertIn('VALUES (%s, FALSE, %s)', enroll)
        row = dial_service.DialListService._department_row(
            (560, 'Тез КЦ', 'tez', 'binotel', True, False, 20, 0, 0))
        self.assertIs(row['configured'], True)   # в разделе отдел есть…
        self.assertIs(row['enabled'], False)     # …а режим выключен: телефон обычный
        view = self._read(os.path.join('src', 'components', 'sip', 'SipSettingsView.jsx'))
        self.assertIn('setDialListDeptIds(dialListManagedDepartmentIds(data.departments))', view)
        self.assertNotIn('.map((d) => Number(d.department_id))', view)
        helper = self._read(os.path.join('src', 'components', 'sip', 'dialListDepartments.js'))
        self.assertIn('d.enabled === true', helper)

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
        # СВ СЗоВ (с 10.10.2026) — весь раздел, как глава СЗоВ; СВ других отделов — ничего.
        self.assertIsNone(svc.manager_scope(False, [], login='szov_sv', supervisor_department_code='szov'))
        self.assertEqual(svc.manager_scope(False, [], login='op_sv', supervisor_department_code='op'), [])
        self.assertEqual(svc.manager_scope(False, [5], login='op_sv_head', supervisor_department_code='op'), [5])
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
                         {'queue', 'waiting', 'issued', 'answered', 'exhausted', 'excluded', 'signed', 'sign_wait'})
        # Подписавший — первым: из пула он ушёл (signed_at в _POOL_SQL), и «В очереди»
        # у него было бы неправдой (владелец, 29.09.2026). Сразу за ним — «на проверке у
        # Яндекса» (11.10.2026): водитель уже подписал и из пула ушёл так же.
        case = sql[sql.index('CASE'):sql.index('END AS stage')]
        signed_at = case.index("WHEN l.signed_at IS NOT NULL THEN 'signed'")
        wait_at = case.index("WHEN l.sign_status = 'processing' THEN 'sign_wait'")
        self.assertLess(signed_at, wait_at)
        self.assertLess(wait_at, case.index("WHEN l.status = 'excluded'"))
        self.assertIn('AND l.signed_at IS NULL', dial_service.DialListService._POOL_SQL)
        self.assertIn("AND l.sign_status <> 'processing'", dial_service.DialListService._POOL_SQL)
        self.assertEqual(dial_service.LEAD_STAGES['sign_wait'], 'На проверке у Яндекса')
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
        for name in ('start_call', 'recall_lead', '_refuse_next_portion'):
            src = inspect.getsource(getattr(dial_service.DialListService, name))
            self.assertIn('_require_outcome_done', src, f'{name} не проверяет итог предыдущего звонка')
        # «Ещё» (11.10.2026) проверяет отказы дважды: до Sapar и в транзакции выдачи.
        nxt = inspect.getsource(dial_service.DialListService.issue_next_portion)
        self.assertEqual(nxt.count('self._refuse_next_portion(cur, ctx)'), 2)
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

        # (state, disposition, cancelled, operator_hangup_at, leg_answered_at, outcome_id, ring_timeout_at)
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'CANCEL', True, 't', 't', None, None)), 'x'), (True, False))
        self.assertEqual(svc._outcome_decision(_Cur(('ended', '', False, None, 't', None, None)), 'x'), (False, True))
        self.assertEqual(svc._outcome_decision(_Cur(('ended', '', False, 't', 't', None, None)), 'x'), (False, None))
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'ANSWERED', False, 't', 't', None, None)), 'x'), (False, True))
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'NOANSWER', False, 't', 't', None, None)), 'x'), (False, False))
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'ANSWERED', False, None, 't', 'o', None)), 'x'), (False, False))
        self.assertEqual(svc._outcome_decision(_Cur(('failed', '', False, None, None, None, None)), 'x'), (False, False))
        self.assertEqual(svc._outcome_decision(_Cur(None), 'x'), (False, False))
        # Сброс на гудках (11.10.2026) — как отбой оператора, только без отмены: водитель не
        # ответил — итог не нужен, ответил в последний момент — нужен, исхода ещё нет — ждём.
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'CANCEL', False, None, 't', None, 't')), 'x'), (False, False))
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'ANSWER', False, None, 't', None, 't')), 'x'), (False, True))
        self.assertEqual(svc._outcome_decision(_Cur(('ended', '', False, None, 't', None, 't')), 'x'), (False, None))
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'NOANSWER', False, 't', 't', None, 't')), 'x'), (False, False))
        # Плечо телефон не подтвердил (ACK не дошёл, 11.10.2026): итог нужен, только если
        # Binotel подтвердил ответ водителя; исход ещё не известен или не ответил — не нужен.
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'ANSWER', False, None, None, None, None)), 'x'), (False, True))
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'CANCEL', False, None, None, None, None)), 'x'), (False, False))
        self.assertEqual(svc._outcome_decision(_Cur(('ended', '', False, None, None, None, None)), 'x'), (False, False))
        self.assertEqual(svc._outcome_decision(_Cur(('finished', 'ANSWER', False, None, None, 'o', None)), 'x'), (False, False))


class PortionStaysVisibleTests(unittest.TestCase):
    """Обработанная пачка остаётся на экране с итогами, пока не взята следующая."""

    def test_state_falls_back_to_last_closed_portion(self):
        state = inspect.getsource(dial_service.DialListService.get_state)
        self.assertIn('_last_portion', state)
        self.assertIn('"closed"', state)
        last = inspect.getsource(dial_service.DialListService._last_portion)
        self.assertIn('ORDER BY closed_at IS NULL DESC, issued_at DESC', last)
        self.assertNotIn('phone_norm', last)
        # Выдача следующей — по-прежнему только через открытую порцию без issued
        # (проверка с 11.10.2026 — в _refuse_next_portion, её зовёт «Ещё»).
        nxt = inspect.getsource(dial_service.DialListService.issue_next_portion)
        self.assertIn('_refuse_next_portion', nxt)
        refuse = inspect.getsource(dial_service.DialListService._refuse_next_portion)
        self.assertIn('_open_portion', refuse)
        self.assertIn("state = 'issued'", refuse)
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
        self.assertIn('self._reopen_row(', reopen)
        self.assertNotIn('phone_norm', reopen)
        # Само переоткрытие — общее с «Перезвонить» оператора (recall_lead, 11.10.2026).
        row = inspect.getsource(dial_service.DialListService._reopen_row)
        self.assertIn("SET state = 'issued', result = '', done_at = NULL", row)
        self.assertIn('SET closed_at = NULL', row)
        # Строку, которая и так у оператора, возврат не дублирует (уникальный индекс).
        self.assertIn("Строка сейчас у оператора", src)


class OperatorProgressTests(unittest.TestCase):
    """Вкладка «Мой прогресс» на телефоне: счётчики оператора за месяц и за сегодня."""

    def test_progress_route_is_operator_only_and_counts_only(self):
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        operator_part = src[src.index('# ── телефон оператора'):src.index('# ── руководитель')]
        self.assertIn("/api/operator/dial_list/progress", operator_part)
        progress = inspect.getsource(dial_service.DialListService.operator_progress)
        # Единственное разрешённое упоминание лида — исключение «Реестра тестовых номеров»:
        # номер там только сравнивается с реестром и наружу не уходит.
        from test_numbers import keys as test_keys
        registry = (test_keys.DIAL_LIST_ATTEMPT_NOT_TEST_SQL, test_keys.DIAL_LIST_ASSIGNMENT_NOT_TEST_SQL)

        def without_registry(sql):
            for fragment in registry:
                sql = sql.replace(fragment, '')
            return sql

        svc = dial_service.DialListService
        self.assertIn('AND ' + registry[0], svc._PROGRESS_SQL)
        self.assertIn('AND ' + registry[1], svc._PROGRESS_SQL)
        self.assertIn('AND ' + registry[0], svc._PROGRESS_OUTCOMES_SQL)
        self.assertIn('AND ' + registry[0], svc._PROGRESS_SUBTYPES_SQL)
        for forbidden in ('phone_norm', 'full_name', 'dial_list_leads'):
            self.assertNotIn(forbidden, progress)
            self.assertNotIn(forbidden, without_registry(svc._PROGRESS_SQL))
            self.assertNotIn(forbidden, without_registry(svc._PROGRESS_OUTCOMES_SQL))
            self.assertNotIn(forbidden, without_registry(svc._PROGRESS_SUBTYPES_SQL))
        # Конверсия (11.10.2026) — единственный запрос прогресса, которому нужна таблица
        # лидов: база месяца и засчитанные оператору успешки. Поэтому сторож у неё свой:
        # только счётчики — внешний SELECT из двух подзапросов, оба COUNT, — и ни ФИО, ни
        # номера, ни ИИН (номер читает лишь условие реестра, у каждого подзапроса своё).
        conversion = svc._PROGRESS_CONVERSION_SQL
        self.assertEqual(conversion.count(test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL), 2)
        bare = conversion.replace(test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL, '')
        for forbidden in ('phone_norm', 'full_name', 'iin'):
            self.assertNotIn(forbidden, bare)
            self.assertNotIn(forbidden, inspect.getsource(svc._operator_conversion))
        heads = [bare[m.end():].split(None, 1)[0] for m in re.finditer(r'\bSELECT\b', bare)]
        self.assertEqual(heads, ['(SELECT', 'COUNT(DISTINCT', 'COUNT(*)'])
        self.assertEqual(re.findall(r'\bAS (\w+)', bare), ['leads_talked', 'successes'])
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
        # (state, lead_id, outcome_id, cancelled, operator_hangup_at, disposition, ring_timeout_at)
        cur = _RoutedCursor([
            ('FOR UPDATE OF t', {'one': ('ended', 'lead-1', None, False, None, '', None)}),
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
        cur.rules[0] = ('FOR UPDATE OF t', {'one': ('finished', 'lead-1', None, True, 't', 'CANCEL', None)})
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
            def manager_scope(self, is_admin, heads, login=None, supervisor_department_code=None):
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
        # Последняя колонка — ring_timeout_at (сброс на гудках, 11.10.2026).
        cur = _RoutedCursor([
            ('l.id = %(lead_id)s', {'one': header}),
            ('os.id, os.name', {'all': [tuple(base + [self.S1, 'Дорого', None]), tuple(base + [None, None, None])]}),
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

    # Хвост строки _WORKED_SQL (новые колонки — только в конец): r[16] — подписал или на
    # проверке у Яндекса, r[17] — «перезвонить» ли итог строки, r[18:25] —
    # _RECALL_COLUMNS: подписал, исключён, открытая строка (id, чья), чей последний итог,
    # «перезвонить» ли он, звонил ли после него другой оператор (11.10.2026).
    NO_RECALL = (False, False, None, None, None, False, False)

    @classmethod
    def _row(cls, *head, signed=False, requeue=False, recall=None):
        return head + (signed, requeue) + (recall or cls.NO_RECALL)

    def test_tabs_follow_directory_and_newest_first(self):
        from datetime import datetime as dt, timezone as tz
        at = lambda h: dt(2026, 9, 24, h, 0, tzinfo=tz.utc)
        # id, name, color, position, requeue, is_active, used, subtypes (json_agg)
        outcomes = [('o1', 'Заинтересован', '#34C759', 1, False, True, 0, []),
                    ('o2', 'Отказ', '#FF3B30', 2, False, True, 0,
                     [{'id': 's1', 'name': 'Дорого', 'position': 1, 'is_active': True, 'used': 1}])]
        # s.id, lead, name, state, result, done_at, created_at, outcome_id, outcome_at, comment, t.state, o.name, o.color,
        # o.pos, l.status, тип итога
        row = self._row
        rows = [
            row('a1', 'l1', 'Иванов', 'done', 'answered', at(9), at(8), 'o2', at(9), 'дорого', 'finished', 'Отказ', '#FF3B30', 2, 'done', 'Дорого'),
            row('a2', 'l2', 'Петров', 'done', 'answered', at(10), at(8), 'o2', at(11), '', 'finished', 'Отказ', '#FF3B30', 2, 'done', None),
            row('a3', 'l3', '', 'done', 'busy', at(12), at(8), None, None, 'старый', 'finished', None, None, None, 'in_progress', None),
            row('a4', 'l4', 'Сидоров', 'done', 'answered', at(13), at(8), 'old', at(13), '', 'finished', 'Думает', '#AF52DE', 9, 'done', None),
            # Исключил руководитель: строка закрыта без звонка — это не недозвон.
            row('a6', 'l6', 'Исключённый', 'done', 'other', at(14), at(8), None, None, '', None, None, None, None, 'excluded', None),
            row('a5', 'l5', 'В очереди', 'issued', '', None, at(8), None, None, '', None, None, None, None, 'in_progress', None),
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
        rows = [self._row(f'a{n}', f'l{n}', f'Водитель {n}', 'done', 'answered', None, dt(2026, 9, 2, tzinfo=tz.utc),
                          'o1', dt(2026, 9, 2, n % 24, tzinfo=tz.utc), '', 'finished', 'Отказ', '#FF3B30', 1, 'done', None)
                for n in range(5)]
        res = self._worked(rows, outcomes, limit=3)
        self.assertEqual(res['tabs'][0]['count'], 5)
        self.assertEqual(len(res['items']), 3)

    def test_can_recall_follows_the_recall_rule(self):
        # Кнопка «Перезвонить» (11.10.2026): итог строки — «перезвонить», а водитель свой и
        # свободен — то же решение, что у ручки recall (recall_verdict), кнопка не врёт.
        from datetime import datetime as dt, timezone as tz
        outcomes = [('cb', 'Перезвонить позже', '#FF9F0A', 1, True, True, 0, []),
                    ('no', 'Отказ', '#FF3B30', 2, False, True, 0, [])]
        own = (False, False, None, None, 7, True, False)

        def row(name, recall, requeue=True, outcome='cb', hour=9):
            at = dt(2026, 10, 11, hour, tzinfo=tz.utc)
            return self._row(f'a-{name}', f'l-{name}', name, 'done', 'answered', at, at, outcome, at, '', 'finished',
                             None, None, None, 'in_progress', None, requeue=requeue, recall=recall)

        rows = [
            row('свой', own, hour=9),
            row('подписал', (True,) + own[1:], hour=10),
            row('исключён', (False, True) + own[2:], hour=11),
            row('у другого', own[:2] + ('a-x', 8) + own[4:], hour=12),
            row('уже у меня', own[:2] + ('a-y', 7) + own[4:], hour=13),
            row('чужой итог', own[:4] + (8, True, False), hour=14),
            row('звонил другой', own[:6] + (True,), hour=15),
            row('не перезвонить', own[:5] + (False, False), hour=16),
            row('вкладка отказа', own, requeue=False, outcome='no', hour=17),
        ]
        res = self._worked(rows, outcomes)
        self.assertEqual({i['full_name']: i['can_recall'] for i in res['items']}, {
            'свой': True, 'подписал': False, 'исключён': False, 'у другого': False, 'уже у меня': False,
            'чужой итог': False, 'звонил другой': False, 'не перезвонить': False, 'вкладка отказа': False})
        # «Не дозвонились» — без кнопки: итога «перезвонить» у строки нет.
        nothing = self._row('a-n', 'l-n', 'Недозвон', 'done', 'no_answer', None, None, None, None, '', 'finished',
                            None, None, None, 'in_progress', None, recall=own)
        self.assertIs(self._worked([nothing], outcomes)['items'][0]['can_recall'], False)
        # Колонки — в конце строки, после прежних 17: r[17] — «перезвонить» ли итог строки.
        sql = dial_service.DialListService._WORKED_SQL
        select = sql[sql.index('SELECT s.id'):sql.index('FROM latest s')]
        self.assertTrue(select.rstrip().endswith(
            "COALESCE(o.requeue, FALSE), " + dial_service.DialListService._RECALL_COLUMNS))
        self.assertIn("(l.signed_at IS NOT NULL OR l.sign_status = 'processing'),\n", select)

    def test_at_label_today_and_other_days(self):
        from datetime import date, datetime as dt, timezone as tz
        now = dt(2026, 9, 25, 9, 5, tzinfo=tz.utc)             # 14:05 по Алматы
        self.assertEqual(dial_service.at_label(now, date(2026, 9, 25)), 'сегодня, 14:05')
        self.assertEqual(dial_service.at_label(now, date(2026, 9, 26)), '25.09, 14:05')
        self.assertEqual(dial_service.at_label(None, date(2026, 9, 25)), '')


class RingTimeoutTests(unittest.TestCase):
    """Сброс на гудках (владелец, 11.10.2026): водитель за пять гудков не взял трубку —
    телефон сам кладёт плечо, а попытка считается обычным недозвоном «Не поднял трубку»:
    не отменяется, итог по ней не нужен. Отбой оператора — другое правило (отмена)."""

    DEP = 1954
    OUTCOME = ('o-1', 'Отказ', '#FF3B30', 1, False, True, 0, [])
    MARK = 'ring_timeout_at = COALESCE(ring_timeout_at, CURRENT_TIMESTAMP)'

    def _ctx(self, uid):
        from datetime import date
        return {"user_id": uid, "department_id": self.DEP, "name": "Оператор", "internal_number": "913",
                "settings": {"portion_size": 20, "period": "2026-10-01", "period_label": "Октябрь 2026",
                             "_period": date(2026, 10, 1), "retry_after_hours": 24, "max_attempts": 3}}

    # ── настройка отдела → секунды в состоянии телефона ─────────────────────
    def test_rings_from_department_column(self):
        rings = dial_service.ring_timeout_rings
        self.assertEqual(rings(None), 5)           # не задано — по умолчанию пять гудков
        self.assertEqual(rings(0), 0)              # отдел выключил сброс
        self.assertEqual(rings(3), 3)
        self.assertEqual(rings(99), 20)            # больше предела не бывает
        self.assertEqual(rings(-2), 0)
        self.assertEqual(rings('мусор'), 5)
        self.assertEqual(rings(True), 5)
        self.assertEqual(dial_service.RING_SECONDS_PER_RING, 5)

    def _state(self, cursor):
        db = _TxDb(cursor)
        svc = dial_service.DialListService(db=db)
        svc.operator_context = self._ctx
        svc.reconcile = lambda ctx: None
        return svc.get_state(7), db

    def test_state_carries_ring_timeout_of_line_department(self):
        # Отсчёт телефона — от принятия плеча (АТС начинает набирать водителя): 5 гудков — 25 с.
        for row, expected in (((None,), 25), (None, 25), ((0,), 0), ((3,), 15), ((40,), 100)):
            cur = _RoutedCursor([('FROM sip_department_config', {'one': row}),
                                 ('FROM dial_list_outcomes o', {'all': [self.OUTCOME]})])
            state, db = self._state(cur)
            self.assertEqual(state["ring_timeout_sec"], expected, row)
            self.assertEqual(state["leg_timeout_sec"], dial_service.LEG_TIMEOUT_SEC)
            ring = cur.sql_with('FROM sip_department_config')
            self.assertEqual(len(ring), 1)
            self.assertIn('ring_timeout_rings', ring[0][0])
            self.assertEqual(ring[0][1], (self.DEP,))       # отдел из контекста: у сидящего — отдел линии
            # Своей короткой транзакцией: упавший запрос не оборвёт транзакцию состояния.
            self.assertIn([ring[0][0]], db.transactions)

    def test_state_survives_missing_ring_column(self):
        # Колонку заводит схема «Настроек SIP»; её ещё нет или запрос упал — пять гудков.
        class _NoColumn(_RoutedCursor):
            def execute(self, sql, params=None):
                if 'ring_timeout_rings' in sql:
                    raise RuntimeError('column "ring_timeout_rings" does not exist')
                super().execute(sql, params)

        state, _ = self._state(_NoColumn([('FROM dial_list_outcomes o', {'all': [self.OUTCOME]})]))
        self.assertEqual(state["ring_timeout_sec"], 25)
        self.assertEqual(state["outcomes"][0]["name"], 'Отказ')

    def test_schema_and_event(self):
        ddl = dial_schema.DDL
        at = next(i for i, s in enumerate(ddl) if 'ADD COLUMN IF NOT EXISTS ring_timeout_at' in s)
        first_index = next(i for i, s in enumerate(ddl) if 'CREATE INDEX' in s or 'CREATE UNIQUE INDEX' in s)
        self.assertLess(at, first_index)
        self.assertIn('ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS ring_timeout_at '
                      'TIMESTAMP WITH TIME ZONE', ddl[at])
        self.assertIn('ring_timeout', dial_service.PHONE_EVENTS)
        svc = dial_service.DialListService(db=None)
        svc.operator_context = self._ctx
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.phone_event(7, 'att-1', 'ring')
        self.assertEqual(ctx.exception.status, 400)

    # ── событие ring_timeout: класть ли плечо ────────────────────────────────
    def _event_svc(self, row, decision, answer=None):
        """row — (state, general_call_id, disposition, cancelled) попытки; decision — строка
        _outcome_decision после события; answer — ответ call-details (или исключение)."""
        cur = _RoutedCursor([
            ('general_call_id, disposition, cancelled FROM dial_list_attempts', {'one': row}),
            ('leg_answered_at, outcome_id', {'one': decision}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db)
        svc.operator_context = self._ctx
        client = _CallDetailsClient(db, {} if answer is None else answer)
        svc._client = lambda dep: client
        finished = []
        svc._finish_by_call = lambda aid, call, source: finished.append((aid, call, source, len(cur.executed)))
        return svc, cur, client, finished

    # Строки решения: (state, disposition, cancelled, operator_hangup_at, leg_answered_at, outcome_id,
    # ring_timeout_at).
    def test_dialing_still_goes_hang_up_and_mark(self):
        # Binotel всё ещё набирает водителя — класть плечо; попытка помечена сбросом.
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, 't'),
            {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'CALLING'}})
        res = svc.phone_event(7, 'att-1', 'ring_timeout', ring_sec=25)
        self.assertEqual(res, {"attempt_id": "att-1", "state": "leg_answered", "final": False,
                               "disposition": "CALLING", "cancelled": False, "outcome_required": None,
                               "hangup": True, "talking": False})
        self.assertEqual(client.calls, ['gc-1'])
        self.assertEqual(client.inside_tx, [False])               # сеть — вне транзакций
        self.assertEqual([p for _, p in cur.sql_with(self.MARK)], [(0, 0, 'att-1')])
        self.assertEqual(cur.sql_with('operator_hangup_at = COALESCE'), [])   # это не отбой оператора
        self.assertEqual(finished, [])

    def test_unknown_or_error_hangs_up(self):
        # Звонка у Binotel не видно, АТС ответила ошибкой, generalCallID ещё нет — класть.
        for row, answer, asked in ((('leg_answered', 'gc-1', '', False), {}, ['gc-1']),
                                   (('leg_answered', 'gc-1', '', False), RuntimeError('Binotel недоступен'), ['gc-1']),
                                   (('requested', None, '', False), {}, [])):
            svc, cur, client, finished = self._event_svc(row, (row[0], '', False, None, 't', None, 't'), answer)
            res = svc.phone_event(7, 'att-1', 'ring_timeout')
            self.assertEqual((res["hangup"], res["talking"], res["final"]), (True, False, False), answer)
            self.assertNotIn("disposition", res)
            self.assertEqual(client.calls, asked)
            self.assertEqual(len(cur.sql_with(self.MARK)), 1)
            self.assertEqual(finished, [])

    def test_driver_talking_keeps_the_leg(self):
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, None),
            {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'online'}})
        res = svc.phone_event(7, 'att-1', 'ring_timeout', ring_sec=25)
        self.assertEqual((res["talking"], res["hangup"], res["final"]), (True, False, False))
        self.assertEqual(res["disposition"], 'ONLINE')
        self.assertEqual(cur.sql_with(self.MARK), [])               # не сброс — отметки нет
        self.assertEqual(finished, [])

    def test_answer_is_fresh_not_from_live_cache(self):
        # Класть ли трубку — по свежему ответу АТС, а не по кэшу карточки звонка (4 с).
        import time
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, None),
            {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'ONLINE'}})
        with unittest.mock.patch.dict(dial_service.DialListService._live_cache,
                                      {'att-1': (time.monotonic(), 'CALLING')}):
            res = svc.phone_event(7, 'att-1', 'ring_timeout')
        self.assertEqual(client.calls, ['gc-1'])
        self.assertTrue(res["talking"])

    def test_final_no_answer_closes_attempt_after_marking(self):
        # Исход уже финальный, водитель не ответил: закрыть попытку как обычно и класть плечо.
        # Отметка — ДО закрытия: закрытие не примет такую попытку за отбой оператора.
        call = {'general_call_id': 'gc-1', 'disposition': 'NOANSWER', 'billsec': 0, 'waitsec': 25}
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('finished', 'NOANSWER', False, None, 't', None, 't'),
            {'gc-1': call})
        res = svc.phone_event(7, 'att-1', 'ring_timeout')
        self.assertEqual(res, {"attempt_id": "att-1", "state": "finished", "final": True,
                               "disposition": "NOANSWER", "cancelled": False, "outcome_required": False,
                               "hangup": True, "talking": False})
        self.assertEqual([f[:3] for f in finished], [('att-1', call, 'poll')])
        mark_at = next(i for i, (sql, _) in enumerate(cur.executed) if self.MARK in sql)
        self.assertLess(mark_at, finished[0][3])

    def test_final_answer_does_not_hang_up(self):
        # Водитель ответил и разговор уже кончился — класть нечего, итог обязателен.
        call = {'general_call_id': 'gc-1', 'disposition': 'ANSWER', 'billsec': 4, 'waitsec': 20}
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('finished', 'ANSWER', False, None, 't', None, None),
            {'gc-1': call})
        res = svc.phone_event(7, 'att-1', 'ring_timeout')
        self.assertEqual((res["final"], res["hangup"], res["outcome_required"]), (True, False, True))
        self.assertEqual(cur.sql_with(self.MARK), [])
        self.assertEqual(len(finished), 1)

    def test_closed_attempt_answers_from_db(self):
        # Попытку уже закрыли (вебхук, опрос): в Binotel не ходим, отвечаем как обычно.
        cases = (
            # попытка, строка решения, hangup, отметок сброса
            (('finished', 'gc-1', 'NOANSWER', False), ('finished', 'NOANSWER', False, None, 't', None, 't'), True, 1),
            (('finished', 'gc-1', 'ANSWER', False), ('finished', 'ANSWER', False, None, 't', None, None), False, 0),
            (('finished', 'gc-1', 'CANCEL', True), ('finished', 'CANCEL', True, 't', 't', None, None), True, 0),
            (('failed', 'gc-1', '', False), ('failed', '', False, None, None, None, None), True, 0),
        )
        for row, decision, hangup, marks in cases:
            svc, cur, client, finished = self._event_svc(row, decision)
            res = svc.phone_event(7, 'att-1', 'ring_timeout')
            self.assertEqual((res["final"], res["hangup"], res["talking"]), (True, hangup, False), row)
            self.assertEqual(res["cancelled"], bool(decision[2]))
            self.assertEqual(len(cur.sql_with(self.MARK)), marks, row)
            self.assertEqual(client.calls, [])
            self.assertEqual(finished, [])
        # Не ответил — окна итога нет; ответил — итог обязателен.
        svc, _, _, _ = self._event_svc(cases[0][0], cases[0][1])
        self.assertIs(svc.phone_event(7, 'att-1', 'ring_timeout')["outcome_required"], False)
        svc, _, _, _ = self._event_svc(cases[1][0], cases[1][1])
        self.assertIs(svc.phone_event(7, 'att-1', 'ring_timeout')["outcome_required"], True)

    def test_unknown_attempt_is_404(self):
        svc, _, client, _ = self._event_svc(None, None)
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.phone_event(7, 'att-x', 'ring_timeout')
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(client.calls, [])

    # ── ended с ring_timeout: телефон положил плечо сам ──────────────────────
    def _ended_svc(self, row, decision, answer=None):
        cur = _RoutedCursor([
            ('general_call_id, assignment_id, cancelled, disposition, ring_timeout_at', {'one': row}),
            ('leg_answered_at, outcome_id', {'one': decision}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db)
        svc.operator_context = self._ctx
        client = _CallDetailsClient(db, {} if answer is None else answer)
        svc._client = lambda dep: client
        finished, cancelled = [], []
        svc._finish_by_call = lambda aid, call, source: finished.append((aid, call['disposition']))
        svc._cancel_attempt = lambda cur, aid, asg, late=False: cancelled.append((aid, late)) or True
        return svc, cur, finished, cancelled

    def test_ended_after_ring_timeout_is_not_operator_hangup(self):
        # by_operator не учитывается, даже если пришёл; отметка сброса и секунды плеча — в
        # попытку, исход — сразу у Binotel, закрытие — обычный недозвон без окна итога.
        svc, cur, finished, cancelled = self._ended_svc(
            ('leg_answered', 'gc-1', 'asg-1', False, '', None), ('finished', 'CANCEL', False, None, 't', None, 't'),
            {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'CANCEL', 'billsec': 0, 'waitsec': 26}})
        res = svc.phone_event(7, 'att-1', 'ended', by_operator=True, leg_sec=27, ring_timeout=True)
        self.assertEqual(cur.sql_with('operator_hangup_at = COALESCE'), [])
        self.assertEqual([p for _, p in cur.sql_with(self.MARK)], [(27, 27, 'att-1')])
        self.assertEqual(finished, [('att-1', 'CANCEL')])
        self.assertEqual(cancelled, [])
        self.assertEqual((res["final"], res["cancelled"], res["outcome_required"]), (True, False, False))

    def test_ended_on_closed_attempt_still_marks_no_answer(self):
        # Вебхук обогнал телефон и уже закрыл попытку «не ответил»: отметка снимает с неё
        # окно итога, отмены нет.
        svc, cur, finished, cancelled = self._ended_svc(
            ('finished', 'gc-1', 'asg-1', False, 'NOANSWER', None), ('finished', 'NOANSWER', False, None, 't', None, 't'))
        res = svc.phone_event(7, 'att-1', 'ended', leg_sec=26, ring_timeout=True)
        self.assertEqual(len(cur.sql_with(self.MARK)), 1)
        self.assertEqual((res["final"], res["cancelled"], res["outcome_required"]), (True, False, False))
        self.assertEqual((finished, cancelled), ([], []))

    def test_ring_timeout_flag_means_nothing_without_ended(self):
        svc, cur, _, _ = self._ended_svc(
            ('leg_ringing', 'gc-1', 'asg-1', False, '', None), ('leg_answered', '', False, None, 't', None, None))
        svc.phone_event(7, 'att-1', 'answered', ring_timeout=True)
        self.assertEqual(cur.sql_with(self.MARK), [])

    def test_stray_operator_hangup_does_not_cancel_ring_timeout(self):
        # Отбой оператора по закрытой попытке «не ответил» — отмена (как было), но если
        # телефон уже сбросил её на гудках — сброс сильнее: попытка остаётся недозвоном.
        for ring_at, expected in ((None, [('att-1', True)]), ('t', [])):
            svc, cur, finished, cancelled = self._ended_svc(
                ('finished', 'gc-1', 'asg-1', False, 'NOANSWER', ring_at),
                ('finished', 'NOANSWER', False, 't', 't', None, ring_at))
            svc.phone_event(7, 'att-1', 'operator_hangup')
            self.assertEqual(cancelled, expected)

    def _client(self, svc):
        import flask
        requester = (5, 'Оператор', 'x', 'operator', None, None, None, 'op')
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (5, requester, None), is_admin_role=lambda role: False,
            headed_department_ids=lambda rid: [], service=svc)
        app = flask.Flask('dial_list_ring_test')
        app.register_blueprint(bp)
        return app.test_client()

    def test_route_passes_ring_timeout_keys(self):
        seen = []

        class Svc:
            def phone_event(self, user_id, attempt_id, event, at=None, by_operator=False, leg_sec=0,
                            ring_timeout=False, ring_sec=0):
                seen.append((event, by_operator, leg_sec, ring_timeout, ring_sec))
                return {"attempt_id": attempt_id, "hangup": True, "talking": False}

        client = self._client(Svc())
        url = '/api/operator/dial_list/attempts/att-1/phone_event'
        resp = client.post(url, json={"event": "ring_timeout", "ring_sec": 25})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((resp.get_json()["hangup"], resp.get_json()["talking"]), (True, False))
        client.post(url, json={"event": "ended", "by_operator": False, "leg_sec": 27, "ring_timeout": True})
        client.post(url, json={"event": "ended", "by_operator": True, "leg_sec": 3})   # старый телефон
        self.assertEqual(seen, [('ring_timeout', False, 0, False, 25), ('ended', False, 27, True, 0),
                                ('ended', True, 3, False, 0)])

    # ── закрытие попытки: сброс — недозвон, не отмена ────────────────────────
    def _finish(self, hangup_row, disposition, assignment=('lead-1', 'issued')):
        cur = _RoutedCursor([
            ("SET state = 'finished'", {'one': ('asg-1',)}),
            ('SELECT operator_hangup_at, ring_timeout_at', {'one': hangup_row}),
            ('COALESCE(o.requeue, FALSE)', {'one': (False,)}),
            ('SELECT lead_id, state FROM dial_list_assignments', {'one': assignment}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        calls = []
        svc._cancel_attempt = lambda cur, aid, asg, late=False: calls.append(('cancel', asg, late)) or True
        svc._close_assignment = (lambda cur, asg, lead, result, answered, count_attempt, requeue=False:
                                 calls.append(('close', asg, lead, result, answered, count_attempt)))
        svc._touch_lead = (lambda cur, lead, answered, count_attempt, requeue=False:
                           calls.append(('touch', lead, answered, count_attempt)))
        svc._finish_attempt('att-1', disposition, 0, 25, 'poll')
        return calls

    def test_ring_timeout_no_answer_is_counted_never_cancelled(self):
        # Отбой оператора без сброса — отмена (правило 24.09.2026, как было).
        self.assertEqual(self._finish(('t', None), 'CANCEL'), [('cancel', 'asg-1', False)])
        # Сброс на гудках — обычный недозвон: строка закрыта «не ответил», лиду +1 попытка,
        # даже если отбой оператора каким-то путём тоже записан.
        for hung in (None, 't'):
            self.assertEqual(self._finish((hung, 't'), 'CANCEL'),
                             [('close', 'asg-1', 'lead-1', 'no_answer', False, True)])
        # Водитель ответил в последний момент — обычный разговор.
        self.assertEqual(self._finish((None, 't'), 'ANSWER'), [('close', 'asg-1', 'lead-1', 'answered', True, True)])
        # Строку уже закрыла прошлая попытка — недозвон всё равно у лида.
        self.assertEqual(self._finish(('t', 't'), 'NOANSWER', assignment=('lead-1', 'done')),
                         [('touch', 'lead-1', False, True)])

    # ── долг итога и сам итог ───────────────────────────────────────────────
    def test_pending_outcome_only_for_answered_ring_timeout(self):
        cur = _RoutedCursor([])
        self.assertIsNone(dial_service.DialListService(db=None)._pending_outcome(cur, 7))
        sql, params = cur.executed[0]
        # Обычный конец разговора — долг сразу; отбой оператора и сброс на гудках — только
        # когда Binotel подтвердил, что водитель ответил.
        self.assertIn("t.operator_hangup_at IS NULL AND t.ring_timeout_at IS NULL", sql)
        self.assertIn("(t.ring_timeout_at IS NOT NULL AND t.state = 'finished'", sql)
        # Плечо без ACK (роутер с SIP ALG), но Binotel соединил водителя — долг, только по
        # свежим попыткам: старые, до итогов, тоже без leg_answered_at.
        self.assertIn("t.outcome_id IS NULL AND t.leg_answered_at IS NULL", sql)
        self.assertIn("t.phone_ended_at IS NOT NULL AND t.state = 'finished'", sql)
        self.assertIn("make_interval(hours => %s)", sql)
        self.assertEqual(params, (7, dial_service.ANSWERED_SQL, dial_service.ANSWERED_SQL,
                                  dial_service.ANSWERED_SQL, dial_service.UNCONFIRMED_LEG_OUTCOME_HOURS))
        self.assertEqual(dial_service.UNCONFIRMED_LEG_OUTCOME_HOURS, 12)

    def _outcome_svc(self, row):
        cur = _RoutedCursor([
            ('FOR UPDATE OF t', {'one': row}),
            ('FROM dial_list_outcomes', {'one': ('o-1', 'Отказ', '#FF3B30', False)}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        svc.operator_context = lambda uid: {"user_id": uid, "department_id": 1}
        return svc, cur

    def test_no_outcome_for_ring_timeout_without_answer(self):
        # (state, lead_id, outcome_id, cancelled, operator_hangup_at, disposition, ring_timeout_at)
        svc, cur = self._outcome_svc(('finished', 'lead-1', None, False, None, 'CANCEL', 't'))
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.set_attempt_outcome(7, 'att-1', 'o-1')
        self.assertEqual((str(ctx.exception), ctx.exception.status), ("Водитель не взял трубку — итог не нужен", 409))
        self.assertEqual(cur.sql_with('UPDATE dial_list_attempts'), [])
        # Исхода от АТС ещё нет — ждать его, как при отбое оператора.
        svc, cur = self._outcome_svc(('ended', 'lead-1', None, False, None, '', 't'))
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.set_attempt_outcome(7, 'att-1', 'o-1')
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn('Дождитесь исхода от АТС', str(ctx.exception))
        self.assertEqual(cur.sql_with('UPDATE dial_list_attempts'), [])
        # Водитель успел ответить — итог обязателен и принимается.
        svc, cur = self._outcome_svc(('finished', 'lead-1', None, False, None, 'ANSWER', 't'))
        self.assertEqual(svc.set_attempt_outcome(7, 'att-1', 'o-1')["outcome"]["name"], 'Отказ')
        self.assertEqual(len(cur.sql_with('SET outcome_id = %s')), 1)

    # ── карточка водителя ────────────────────────────────────────────────────
    def test_lead_card_marks_auto_reset_attempts(self):
        r = [None] * dial_service.DialListService._JOURNAL_COLUMN_COUNT
        r[0], r[1], r[2], r[3], r[4], r[5], r[10], r[29] = 'lead-1', 1, 'Иванов', '77011234567', 'in_progress', 1, 1, 'queue'

        def attempt(disposition, ring_at, cancelled=False):
            return ('att', None, 3, 'Оператор', '913', 'finished', disposition, 0, 25, '', 'poll', None, None, None,
                    True, 'asg', 'no_answer', 'done', None, None, None, None, '', None, cancelled, None, 27,
                    None, None, ring_at)

        cur = _RoutedCursor([
            ('l.id = %(lead_id)s', {'one': tuple(r) + (1,)}),
            ('os.id, os.name', {'all': [attempt('CANCEL', 't'), attempt('ANSWER', 't'), attempt('NOANSWER', None),
                                        attempt('CANCEL', 't', cancelled=True)]}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        svc.lead_department = lambda lid: 1
        svc.department_settings = lambda d: {"max_attempts": 3, "retry_after_hours": 24}
        card = svc.lead_card('lead-1')
        # Флаг — только у сброшенных без ответа: ответивший в последний момент — разговор.
        self.assertEqual([a['ring_timeout'] for a in card['attempts']], [True, False, False, False])
        self.assertEqual([a['result'] for a in card['attempts']], ['no_answer', 'answered', 'no_answer', 'cancelled'])
        self.assertIn('t.ring_timeout_at', cur.sql_with('os.id, os.name')[0][0])


class OperatorConversionTests(unittest.TestCase):
    """Конверсия в «Моём прогрессе» (владелец, 11.10.2026): ровно та, что у руководителя в
    «Аналитике» по операторам, — база обзваниваемого месяца, успешки из поговоривших."""

    def _progress(self, row, period=None, cursor_cls=None):
        from datetime import date
        cur = (cursor_cls or _RoutedCursor)([
            ('AS issued', {'one': (1, 1, 0, 2, 0, 1, 0, 30, 0, 0)}),
            ('AS leads_talked', {'one': row}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        settings = {"_period": period or date(2026, 9, 1)}
        svc.operator_context = lambda uid: {"user_id": uid, "department_id": 1954, "name": "Оператор",
                                            "settings": settings}
        return svc.operator_progress(7), cur

    def test_progress_carries_conversion_of_active_base(self):
        from datetime import date
        res, cur = self._progress((40, 5), period=date(2026, 9, 1))
        self.assertEqual(res["conversion"], {"successes": 5, "leads_talked": 40, "percent": 12.5,
                                             "period": "2026-09-01", "period_label": "Сентябрь 2026"})
        # Остальные счётчики — как были: календарный месяц.
        self.assertEqual(res["period"], dial_service.current_period().isoformat())
        self.assertEqual(res["month"]["issued"], 1)
        sql, params = cur.sql_with('AS leads_talked')[0]
        self.assertEqual(params, {"operator_id": 7, "department_id": 1954, "period": date(2026, 9, 1),
                                  "answered": dial_service.ANSWERED_SQL,
                                  "min_sec": dial_service.signing.SUCCESS_MIN_BILLSEC})

    def test_percent_like_manager_analytics(self):
        from dial_list import analytics
        for talked, successes in ((40, 5), (7, 3), (3, 0), (0, 0)):
            res, _ = self._progress((talked, successes))
            self.assertEqual(res["conversion"]["percent"], analytics._pct(successes, talked))
        self.assertEqual(self._progress((7, 3))[0]["conversion"]["percent"], 42.9)
        self.assertIsNone(self._progress((0, 0))[0]["conversion"]["percent"])    # «—» на телефоне
        self.assertIsNone(self._progress(None)[0]["conversion"]["percent"])

    def test_conversion_failure_never_breaks_progress(self):
        class _Broken(_RoutedCursor):
            def execute(self, sql, params=None):
                if 'AS leads_talked' in sql:
                    raise RuntimeError('canceling statement due to statement timeout')
                super().execute(sql, params)

        res, _ = self._progress((40, 5), cursor_cls=_Broken)
        self.assertNotIn("conversion", res)
        self.assertEqual(res["month"]["issued"], 1)
        self.assertEqual(res["month"]["talk_sec"], 30)

    def test_definition_matches_manager_analytics(self):
        from dial_list import analytics
        from test_numbers import keys as test_keys
        sql = dial_service.DialListService._PROGRESS_CONVERSION_SQL
        for fragment in ("COUNT(DISTINCT a.lead_id)", "t.operator_id = %(operator_id)s",
                         "l.department_id = %(department_id)s AND l.period = %(period)s",
                         "t.state = 'finished' AND UPPER(t.disposition) IN %(answered)s",
                         "t.billsec >= %(min_sec)s",
                         "l.success_operator_id = %(operator_id)s AND l.success_attempt_id IS NOT NULL"):
            self.assertIn(fragment, sql)
        # Тестовые номера — то же условие, что у аналитики, у каждого подзапроса своё.
        self.assertEqual(sql.count(test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL), 2)
        self.assertIn(test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL, analytics._LEADS_CTE)
        # Определение аналитики, с которым прогресс обязан совпадать: поменяли там — тест
        # напомнит поменять и здесь.
        self.assertEqual(set(analytics.ANSWERED_SQL), set(dial_service.ANSWERED_SQL))
        self.assertIn("AND t.billsec >= %(min_sec)s) AS talked", analytics._LEADS_CTE)
        self.assertIn("COUNT(DISTINCT lead_id) FILTER (WHERE talked) AS leads_talked", analytics._OPERATORS_SQL)
        self.assertIn("FROM leads WHERE success_attempt_id IS NOT NULL GROUP BY success_operator_id",
                      analytics._OPERATORS_SQL)


class SignedDriversNeverIssuedTests(unittest.TestCase):
    """«Подписавших не выдавать» (владелец, 11.10.2026). «На проверке у Яндекса» — тоже
    подписал: пул, этап журнала, счётчики отдела и «Вернуть в список» держат одно
    правило; перед «Ещё» и перед звонком сервер сам спрашивает Sapar, вне транзакций,
    а Sapar лежит или не настроен — выдаёт и звонит, как раньше."""

    DEP = 1954
    IIN = '900101300007'
    PERIOD = date(2026, 10, 1)
    NOW = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)

    def _ctx(self, uid, size=4):
        return {"user_id": uid, "department_id": self.DEP, "name": "Оператор", "internal_number": "913",
                "settings": {"portion_size": size, "period": "2026-10-01", "period_label": "Октябрь 2026",
                             "_period": self.PERIOD, "retry_after_hours": 24, "max_attempts": 3,
                             "caller_id_for_employee": ""}}

    def _lead(self, lead_id, checked_min_ago=None, error='', iin=None):
        """Кандидат пула: (id, ИИН, база, когда проверяли, ошибка проверки)."""
        checked = None if checked_min_ago is None else self.NOW - timedelta(minutes=checked_min_ago)
        return (lead_id, self.IIN if iin is None else iin, self.PERIOD, checked, error)

    def _now(self):
        return unittest.mock.patch.object(dial_service, '_utcnow', return_value=self.NOW)

    # ── правило пула и его зеркала ──────────────────────────────────────────
    def test_pool_and_its_mirrors_skip_processing(self):
        svc = dial_service.DialListService
        self.assertIn("AND l.signed_at IS NULL", svc._POOL_SQL)
        self.assertIn("AND l.sign_status <> 'processing'", svc._POOL_SQL)
        self.assertIn("AND l.signed_at IS NULL AND l.sign_status <> 'processing') AS leads_open",
                      svc._DEPARTMENTS_SQL)
        self.assertIn("AND sign_status <> 'processing'", inspect.getsource(svc.leads_summary))
        # Кандидаты проверки перед выдачей — тот же пул: те же условия и порядок, без замка.
        cand = svc._POOL_CANDIDATES_SQL
        self.assertTrue(cand.strip().startswith('SELECT l.id, l.iin, l.period, l.sign_checked_at, l.sign_error\n'))
        self.assertEqual(cand.split('FROM dial_list_leads l', 1)[1],
                         svc._POOL_SQL.format(lock='').split('FROM dial_list_leads l', 1)[1])
        self.assertNotIn('FOR UPDATE', cand)
        # Подсчёт «доступно» в состоянии телефона — тот же текст пула.
        self.assertIn('self._POOL_SQL.format(lock="")', inspect.getsource(svc.get_state))

    def test_journal_stage_and_filter_know_sign_wait(self):
        svc = dial_service.DialListService(db=None)
        r = [None] * svc._JOURNAL_COLUMN_COUNT
        r[0], r[1], r[2], r[3], r[4], r[5], r[10] = 'lead-1', 1, 'Иванов', '77011234567', 'in_progress', 0, 1
        r[29], r[39], r[40] = 'sign_wait', self.IIN, 'processing'
        row = svc._journal_row(tuple(r), 3)
        self.assertEqual((row['stage'], row['stage_label']), ('sign_wait', 'На проверке у Яндекса'))
        self.assertEqual(row['sign']['status'], 'processing')
        executed = []

        class Cursor:
            def execute(self, sql, params=None):
                executed.append((sql, params))

            def fetchall(self):
                return [(None,) * PAGE_WIDTH + ([['sign_wait', None, None, 2, 2]],)] \
                    if 'counts.summary' in executed[-1][0] else []

        svc.db = _routed_db(Cursor())
        svc.department_settings = lambda d: {"max_attempts": 3, "retry_after_hours": 24, "period": "2026-10-01",
                                             "_period": self.PERIOD}
        res = svc.leads_journal(1, stage='sign_wait')
        self.assertEqual(executed[0][1]['stage'], 'sign_wait')
        self.assertEqual(res['by_stage']['sign_wait'], 2)
        self.assertEqual(res['stages']['sign_wait'], 'На проверке у Яндекса')

    def test_requeue_refuses_processing_like_signed(self):
        for row in ((self.DEP, 'in_progress', None, 'processing'), (self.DEP, 'done', self.NOW, 'signed')):
            cur = _RoutedCursor([('sign_status FROM dial_list_leads WHERE id = %s FOR UPDATE', {'one': row})])
            svc = dial_service.DialListService(db=_routed_db(cur))
            with self.assertRaises(dial_service.DialListError) as ctx:
                svc.requeue_lead('lead-1', 5)
            self.assertEqual((str(ctx.exception), ctx.exception.status),
                             ("Водитель уже подписал документы — звонить ему незачем", 409))
            self.assertEqual(_writes(sql for sql, _ in cur.executed), [])

    def test_due_rule(self):
        due, now = dial_service.sign_check_due, self.NOW
        self.assertFalse(due('', None, '', now, 15))                                 # без ИИН не спросить
        self.assertTrue(due(self.IIN, None, '', now, 15))                            # не проверяли
        self.assertTrue(due(self.IIN, now - timedelta(minutes=16), '', now, 15))     # устарела
        self.assertFalse(due(self.IIN, now - timedelta(minutes=14), '', now, 15))    # свежая
        # Отказ Sapar по ИИН тоже сдвигает время проверки — «свежая» с ошибкой не свежая.
        self.assertTrue(due(self.IIN, now - timedelta(minutes=1), 'Code 500', now, 15))
        self.assertTrue(due(self.IIN, (now - timedelta(minutes=20)).replace(tzinfo=None), '', now, 15))
        self.assertEqual((dial_service.SIGN_PRECHECK_ROUNDS, dial_service.SIGN_PRECHECK_BUDGET_SEC,
                          dial_service.SIGN_PRECHECK_FRESH_MINUTES, dial_service.SIGN_PRECHECK_WORKERS,
                          dial_service.SIGN_PRECHECK_TIMEOUT_SEC, dial_service.SIGN_PRECHECK_MAX_SYSTEMIC,
                          dial_service.SIGN_CALL_FRESH_MINUTES, dial_service.SIGN_CALL_TIMEOUT_SEC),
                         (3, 8, 15, 4, 3, 2, 30, 3))

    # ── проверка перед «Ещё» ─────────────────────────────────────────────────
    def _precheck(self, rounds, answers=(), configured=True, size=4):
        queue = [list(r) for r in rounds]
        cur = _RoutedCursor([('SELECT l.id, l.iin, l.period',
                              {'all': lambda params: queue.pop(0) if queue else []})])
        db = _TxDb(cur)
        checker = _FakeSignChecker(db, configured=configured, answers=answers)
        svc = dial_service.DialListService(db=db, sign_checker=checker)
        with self._now():
            totals = svc._precheck_signatures(self._ctx(7, size=size))
        return totals, cur, checker

    def test_checks_stale_head_with_margin_and_tops_up(self):
        lead = self._lead
        first = [lead('l1', 5), lead('l2'), lead('l3', 20), lead('l4', 2, 'Code 500'), lead('l5', iin=''),
                 lead('l6')]
        # l3 подписал, l2 «на проверке у Яндекса» — из пула ушли, в голову встал l7.
        second = [lead('l1', 5), lead('l4', 2, 'Code 500'), lead('l5', iin=''), lead('l7'), lead('l6'),
                  lead('l8', 1)]
        totals, cur, checker = self._precheck(
            [first, second], answers=[{"checked": 4, "signed": ['l3'], "processing": ['l2']}, {"checked": 1}])
        # l1 свежий и l5 без ИИН — не спрашиваем; l4 с ошибкой — спрашиваем; l6 — запас на замену.
        # Второй круг: l4 и l6 в этот раз уже спрашивали, l8 проверен минуту назад.
        self.assertEqual([[x[0] for x in c['leads']] for c in checker.calls], [['l2', 'l3', 'l4', 'l6'], ['l7']])
        call = checker.calls[0]
        self.assertEqual(call['leads'][0], ('l2', self.IIN, self.PERIOD))
        self.assertEqual((call['timeout'], call['workers'], call['max_systemic'], call['now']), (3, 4, 2, self.NOW))
        self.assertTrue(0 < call['budget_sec'] <= dial_service.SIGN_PRECHECK_BUDGET_SEC)
        self.assertEqual([c['in_tx'] for c in checker.calls], [False, False])     # Sapar — вне транзакций
        selects = cur.sql_with('SELECT l.id, l.iin, l.period')
        # Порция 4 + запас max(5, 4 // 2) = 9; остальное — как у выдачи.
        self.assertEqual([p for _, p in selects], [(self.DEP, self.PERIOD, 24, 3, 9)] * 2)
        self.assertEqual({k: totals[k] for k in ('rounds', 'checked', 'signed', 'processing', 'stopped')},
                         {'rounds': 2, 'checked': 5, 'signed': 1, 'processing': 1, 'stopped': ''})

    def test_fresh_head_needs_no_sapar(self):
        # l5 стоит в запасе: в порцию не попадёт, его не спрашиваем.
        rows = [self._lead('l1', 5), self._lead('l2', 14), self._lead('l3', iin=''), self._lead('l4', 1),
                self._lead('l5')]
        totals, cur, checker = self._precheck([rows])
        self.assertEqual((checker.calls, totals['rounds']), ([], 0))
        self.assertEqual(len(cur.sql_with('SELECT l.id, l.iin')), 1)

    def test_margin_is_at_least_five(self):
        _, cur, _ = self._precheck([[]], size=20)
        self.assertEqual(cur.sql_with('SELECT l.id, l.iin')[0][1][-1], 30)          # 20 + 20 // 2
        _, cur, _ = self._precheck([[]], size=1)
        self.assertEqual(cur.sql_with('SELECT l.id, l.iin')[0][1][-1], 6)           # 1 + 5

    def test_no_sapar_means_issue_as_before(self):
        # Sapar не настроен — ни кандидатов, ни запросов.
        totals, cur, checker = self._precheck([[self._lead('l1')]], configured=False)
        self.assertEqual((cur.executed, checker.calls, totals['stopped']), ([], [], 'not_configured'))
        # Sapar лежит — после первого круга больше не спрашиваем.
        totals, cur, checker = self._precheck(
            [[self._lead('l1'), self._lead('l2')]] * 3,
            answers=[{"errors": 2, "systemic": 2, "stopped": "sapar_unavailable"}])
        self.assertEqual((len(checker.calls), totals['stopped'], totals['systemic']), (1, 'sapar_unavailable', 2))
        # Срок вышел — даже кандидатов не читаем.
        with unittest.mock.patch.object(dial_service, 'SIGN_PRECHECK_BUDGET_SEC', 0):
            totals, cur, checker = self._precheck([[self._lead('l1')]])
        self.assertEqual((cur.executed, checker.calls, totals['stopped']), ([], [], 'budget'))

    def test_rounds_and_systemic_allowance_are_shared(self):
        rounds = [[self._lead(f'r{n}a'), self._lead(f'r{n}b')] for n in range(5)]
        answers = [{"checked": 1, "errors": 1, "systemic": 1, "signed": [f'r{n}a']} if n == 0
                   else {"checked": 2, "signed": [f'r{n}a']} for n in range(5)]
        totals, _, checker = self._precheck(rounds, answers=answers, size=2)
        self.assertEqual(len(checker.calls), dial_service.SIGN_PRECHECK_ROUNDS)
        # Один сетевой сбой в первом круге — на остальные круги остаётся один.
        self.assertEqual([c['max_systemic'] for c in checker.calls], [2, 1, 1])
        self.assertEqual((totals['rounds'], totals['signed'], totals['systemic']), (3, 3, 1))

    # ── «Ещё»: отказы → проверка → выдача под замком ─────────────────────────
    def _next(self, open_portion=None, pending=0, pending_outcome=None, candidates=(), answers=(),
              pool=(('lead-a',), ('lead-b',))):
        cur = _RoutedCursor([
            ('WHERE operator_id = %s AND closed_at IS NULL', {'one': open_portion}),
            ("WHERE portion_id = %s AND state = 'issued'", {'one': (pending,)}),
            ('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]}),
            ('t.outcome_id IS NULL AND t.leg_answered_at IS NOT NULL', {'one': pending_outcome}),
            ('SELECT l.id, l.iin, l.period', {'all': list(candidates)}),
            ('FOR UPDATE SKIP LOCKED', {'all': list(pool)}),
            ('INSERT INTO dial_list_portions', {'one': ('portion-new',)}),
        ])
        db = _TxDb(cur)
        checker = _FakeSignChecker(db, answers=answers)
        svc = dial_service.DialListService(db=db, sign_checker=checker)
        svc.operator_context = self._ctx
        svc.get_state = lambda uid: {"portion": "новая"}
        return svc, cur, db, checker

    def test_refusals_come_before_sapar(self):
        stale = [self._lead('l1')]
        for kwargs, text in (({"open_portion": ('p-1', 20, None), "pending": 3},
                              "Сначала обработайте текущий список: осталось 3"),
                             ({"pending_outcome": ('att-9', 'Петров', None, None)},
                              "Сначала укажите итог звонка: Петров")):
            svc, cur, db, checker = self._next(candidates=stale, **kwargs)
            with self._now(), self.assertRaises(dial_service.DialListError) as ctx:
                svc.issue_next_portion(7)
            self.assertEqual((str(ctx.exception), ctx.exception.status), (text, 409))
            # Одно чтение — до Sapar и до выдачи дело не дошло.
            self.assertEqual((checker.calls, cur.sql_with('SELECT l.id, l.iin')), ([], []))
            self.assertEqual(len(db.transactions), 1)
            self.assertEqual(_writes(db.transactions[0]), [])

    def test_precheck_runs_between_refusals_and_locked_claim(self):
        svc, cur, db, checker = self._next(candidates=[self._lead('l1'), self._lead('l2', 5)],
                                           answers=[{"checked": 1, "signed": ['l1']}])
        with self._now():
            self.assertEqual(svc.issue_next_portion(7), {"portion": "новая"})
        self.assertEqual([c['leads'] for c in checker.calls], [[('l1', self.IIN, self.PERIOD)]])
        self.assertIs(checker.calls[0]['in_tx'], False)
        refusals, *checks, claim = db.transactions
        self.assertEqual(_writes(refusals), [])
        self.assertTrue(checks and all(t == [self._candidates_sql()] for t in checks))
        # Выдача: первым — замок оператора, затем отказы ещё раз и пул с SKIP LOCKED.
        self.assertIn('pg_advisory_xact_lock(hashtext(%s))', claim[0])
        self.assertEqual(cur.sql_with('pg_advisory_xact_lock')[0][1], ('dial_list_operator:7',))
        self.assertTrue(any('WHERE operator_id = %s AND closed_at IS NULL' in s for s in claim[1:]))
        self.assertTrue(any('FOR UPDATE SKIP LOCKED' in s for s in claim))
        self.assertEqual([p for _, p in cur.sql_with('INSERT INTO dial_list_assignments')],
                         [('portion-new', 'lead-a', 7, 1), ('portion-new', 'lead-b', 7, 2)])
        self.assertEqual(db.rolled_back, [False] * len(db.transactions))

    def _candidates_sql(self):
        return dial_service.DialListService._POOL_CANDIDATES_SQL

    def test_stale_open_portion_is_closed_only_under_lock(self):
        svc, cur, db, _ = self._next(open_portion=('p-old', 20, None), pending=0)
        with self._now():
            svc.issue_next_portion(7)
        closes = [i for i, t in enumerate(db.transactions)
                  if any('SET closed_at = CURRENT_TIMESTAMP WHERE id = %s' in s for s in t)]
        self.assertEqual(closes, [len(db.transactions) - 1])

    def test_precheck_failure_never_blocks_portion(self):
        svc, cur, db, checker = self._next(candidates=[self._lead('l1')], answers=[RuntimeError('Sapar упал')])
        with self._now(), self.assertLogs('dial_list.service', level='ERROR'):
            self.assertEqual(svc.issue_next_portion(7), {"portion": "новая"})
        self.assertEqual(len(cur.sql_with('INSERT INTO dial_list_assignments')), 2)

    # ── «Позвонить»: свежая проверка и отказ подписавшему ───────────────────
    def _call(self, jit_row=None, signed=False, configured=True, answers=(), active=None):
        cur = _RoutedCursor([
            ("AND l.signed_at IS NULL AND l.sign_status <> 'processing'", {'one': jit_row}),
            ('FOR UPDATE OF a', {'one': ('asg-1', 'issued', 0, '77010000001', 'Иванов', 'lead-1', signed)}),
            ('make_interval(mins => %s)', {'one': active}),
            ('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]}),
            ("SET state = 'done', result = 'other'", {'one': ('portion-1',)}),
            ('INSERT INTO dial_list_attempts', {'one': ('att-1', None)}),
        ])
        db = _TxDb(cur)
        checker = _FakeSignChecker(db, configured=configured, answers=answers)
        svc = dial_service.DialListService(db=db, sign_checker=checker)
        svc.operator_context = self._ctx
        client = _OriginateClient(db)
        svc._client = lambda dep: client
        return svc, cur, db, checker, client

    def test_signed_driver_row_is_closed_and_not_called(self):
        # Проверка перед звонком нашла подпись — транзакция звонка видит её и снимает строку.
        stale = ('lead-1', self.IIN, self.PERIOD, None, '')
        svc, cur, db, checker, client = self._call(jit_row=stale, signed=True, answers=[{"signed": ['lead-1']}])
        with self._now(), self.assertRaises(dial_service.DialListError) as ctx:
            svc.start_call(7, 'asg-1')
        self.assertEqual((str(ctx.exception), ctx.exception.status), (dial_service.SIGNED_ROW_CLOSED, 409))
        self.assertEqual(len(checker.calls), 1)
        self.assertIn("(l.signed_at IS NOT NULL OR l.sign_status = 'processing')",
                      cur.sql_with('FOR UPDATE OF a')[0][0])
        self.assertEqual([p for _, p in cur.sql_with("SET state = 'done', result = 'other'")], [('asg-1',)])
        self.assertEqual(len(cur.sql_with('UPDATE dial_list_portions SET closed_at = CURRENT_TIMESTAMP')), 1)
        self.assertEqual((cur.sql_with('INSERT INTO dial_list_attempts'), client.calls), ([], []))
        # Закрытие зафиксировано: отказ поднят уже после транзакции и её не откатил.
        self.assertEqual(db.rolled_back, [False, False])

    def test_guard_keeps_existing_gates_first(self):
        svc, cur, db, _, client = self._call(signed=True, configured=False,
                                             active=('att-0', 'asg-0', 'leg_answered', 'gc', None, 'Петров'))
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.start_call(7, 'asg-1')
        self.assertIn('Идёт звонок (Петров)', str(ctx.exception))
        self.assertEqual(cur.sql_with("SET state = 'done', result = 'other'"), [])

    def test_unsigned_driver_is_called_as_before(self):
        svc, cur, db, checker, client = self._call(configured=False)
        attempt = svc.start_call(7, 'asg-1')
        self.assertEqual((attempt['attempt_id'], attempt['general_call_id']), ('att-1', 'gc-1'))
        self.assertEqual(client.calls, [('913', '77010000001', {"callerIdForEmployee": '913'}, False)])
        # Sapar не настроен — водителя для проверки даже не читаем.
        self.assertEqual(cur.sql_with("AND l.sign_status <> 'processing'"), [])

    def test_stale_check_runs_before_call_outside_transactions(self):
        stale = ('lead-1', self.IIN, self.PERIOD, None, '')
        svc, cur, db, checker, client = self._call(jit_row=stale)
        with self._now():
            svc.start_call(7, 'asg-1')
        self.assertEqual(len(checker.calls), 1)
        call = checker.calls[0]
        self.assertEqual(call['leads'], [('lead-1', self.IIN, self.PERIOD)])
        self.assertEqual((call['timeout'], call['workers'], call['budget_sec'], call['max_systemic']), (3, 1, 3, 1))
        self.assertIs(call['in_tx'], False)
        sqls = [sql for sql, _ in cur.executed]
        jit_at = next(i for i, s in enumerate(sqls) if "AND l.sign_status <> 'processing'" in s)
        self.assertLess(jit_at, next(i for i, s in enumerate(sqls) if 'FOR UPDATE OF a' in s))
        self.assertEqual(cur.sql_with("AND l.sign_status <> 'processing'")[0][1], ('asg-1', 7))
        self.assertEqual(len(client.calls), 1)

    def test_check_only_when_stale_or_failed(self):
        for minutes, error, asked in ((10, '', 0), (29, '', 0), (31, '', 1), (1, 'Code 500', 1)):
            row = ('lead-1', self.IIN, self.PERIOD, self.NOW - timedelta(minutes=minutes), error)
            svc, cur, db, checker, client = self._call(jit_row=row)
            with self._now():
                svc.start_call(7, 'asg-1')
            self.assertEqual(len(checker.calls), asked, (minutes, error))
            self.assertEqual(len(client.calls), 1)

    def test_check_failure_still_calls(self):
        stale = ('lead-1', self.IIN, self.PERIOD, None, '')
        svc, cur, db, checker, client = self._call(jit_row=stale, answers=[RuntimeError('Sapar упал')])
        with self._now(), self.assertLogs('dial_list.service', level='ERROR'):
            svc.start_call(7, 'asg-1')
        self.assertEqual(len(client.calls), 1)


class RecallTests(unittest.TestCase):
    """«Перезвонить» раньше срока (владелец, 11.10.2026): только своему водителю с итогом
    «Перезвонить», одной транзакцией и без сети; кнопка на вкладке итога решается тем же
    правилом (recall_verdict) — она не врёт."""

    LEAD = '66666666-6666-6666-6666-666666666666'
    # подписал, исключён, открытая строка (id, чья), чей последний итог, «перезвонить», звонил другой
    OWN = (False, False, None, None, 7, True, False)

    def _ctx(self, uid):
        return {"user_id": uid, "department_id": 1954, "name": "Оператор", "internal_number": "913",
                "settings": {"portion_size": 20, "_period": date(2026, 10, 1), "retry_after_hours": 24,
                             "max_attempts": 3}}

    def _svc(self, cols=OWN, found=True, active=None, pending_outcome=None, last_portion=('portion-1', 3, None, None),
             last_done=None, insert_error=None):
        class _Cur(_RoutedCursor):
            def execute(self, sql, params=None):
                super().execute(sql, params)
                if insert_error is not None and 'INSERT INTO dial_list_assignments' in sql:
                    raise insert_error

        cur = _Cur([
            ('FOR UPDATE OF l', {'one': (self.LEAD,) + tuple(cols) if found else None}),
            ('make_interval(mins => %s)', {'one': active}),
            ('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]}),
            ('t.outcome_id IS NULL AND t.leg_answered_at IS NOT NULL', {'one': pending_outcome}),
            ('ORDER BY closed_at IS NULL DESC, issued_at DESC', {'one': last_portion}),
            ("WHERE lead_id = %s AND operator_id = %s AND state = 'done'", {'one': last_done}),
            ('COALESCE(MAX(position), 0) + 1', {'one': (4,)}),
            ('INSERT INTO dial_list_portions', {'one': ('portion-new',)}),
            ('INSERT INTO dial_list_assignments', {'one': ('asg-new',)}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db, sign_checker=_FakeSignChecker(db, configured=False))
        svc.operator_context = self._ctx
        svc.get_state = lambda uid: {"portion": {"id": "portion-1"}}
        return svc, cur, db

    def _refused(self, svc, lead_id=None):
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.recall_lead(7, lead_id or self.LEAD)
        return str(ctx.exception), ctx.exception.status

    def _writes(self, cur):
        return _writes(sql for sql, _ in cur.executed)

    def test_verdict_rules(self):
        verdict, own = dial_service.recall_verdict, self.OWN
        self.assertEqual(verdict(own, 7), 'ok')
        self.assertEqual(verdict((True,) + own[1:], 7), 'signed')                 # подписал или ждёт Яндекс
        self.assertEqual(verdict((False, True) + own[2:], 7), 'excluded')
        self.assertEqual(verdict((True, True) + own[2:], 7), 'signed')            # подпись — первой
        self.assertEqual(verdict(own[:2] + ('a', 7) + (None, False, False), 7), 'issued_mine')   # без проверки итога
        self.assertEqual(verdict(own[:2] + ('a', 8) + own[4:], 7), 'issued_other')
        self.assertEqual(verdict(own[:4] + (None, False, False), 7), 'not_own')    # итога не было
        self.assertEqual(verdict(own[:4] + (7, False, False), 7), 'not_own')       # итог свой, но не «перезвонить»
        self.assertEqual(verdict(own[:4] + (8, True, False), 7), 'taken')          # последний итог — чужой
        self.assertEqual(verdict(own[:6] + (True,), 7), 'taken')                   # после итога звонил другой
        self.assertEqual(set(dial_service.RECALL_REFUSALS), {'signed', 'excluded', 'issued_other', 'not_own', 'taken'})

    def test_lead_must_be_in_operator_department(self):
        svc, cur, _ = self._svc()
        self.assertEqual(self._refused(svc, 'не-uuid'), ("Водитель не найден", 404))
        self.assertEqual(cur.executed, [])                                         # до базы не дошло
        svc, cur, _ = self._svc(found=False)
        self.assertEqual(self._refused(svc, self.LEAD.upper()), ("Водитель не найден", 404))
        params = cur.sql_with('FOR UPDATE OF l')[0][1]
        self.assertEqual(params, {"lead_id": self.LEAD, "department_id": 1954, "operator_id": 7})

    def test_signed_and_excluded_refused_before_other_gates(self):
        busy = ('att-0', 'asg-0', 'leg_answered', 'gc', None, 'Петров')
        for cols, text in (((True,) + self.OWN[1:], "Водитель уже подписал документы — звонить не нужно"),
                           ((False, True) + self.OWN[2:], "Руководитель исключил водителя из обзвона")):
            svc, cur, _ = self._svc(cols=cols, active=busy, pending_outcome=('att-1', 'Петров', None, None))
            self.assertEqual(self._refused(svc), (text, 409))
            self.assertEqual(self._writes(cur), [])

    def test_call_and_outcome_gates(self):
        svc, cur, _ = self._svc(active=('att-0', 'asg-0', 'leg_answered', 'gc', None, 'Петров'))
        self.assertEqual(self._refused(svc), ("Идёт звонок (Петров). Дождитесь его завершения", 409))
        svc, cur, _ = self._svc(pending_outcome=('att-1', 'Сидоров', None, None))
        self.assertEqual(self._refused(svc), ("Сначала укажите итог звонка: Сидоров", 409))
        self.assertEqual(self._writes(cur), [])

    def test_already_in_my_list_returns_that_row(self):
        # Прошлый «Перезвонить» упёрся в отказ АТС — строка так и осталась у оператора.
        svc, cur, _ = self._svc(cols=(False, False, 'asg-open', 7, None, False, False))
        res = svc.recall_lead(7, self.LEAD)
        self.assertEqual(res, {"assignment_id": "asg-open", "portion": {"id": "portion-1"}})
        self.assertEqual(self._writes(cur), [])

    def test_issued_to_other_and_ownership_refusals(self):
        cases = (
            (self.OWN[:2] + ('asg-x', 8) + self.OWN[4:], "Водитель уже в списке у другого оператора"),
            (self.OWN[:4] + (None, False, False),
             "Перезвонить раньше срока можно только своему водителю с итогом «Перезвонить»"),
            (self.OWN[:4] + (7, False, False),
             "Перезвонить раньше срока можно только своему водителю с итогом «Перезвонить»"),
            (self.OWN[:4] + (8, True, False), "Этим водителем уже занимается другой оператор"),
            (self.OWN[:6] + (True,), "Этим водителем уже занимается другой оператор"),
        )
        for cols, text in cases:
            svc, cur, _ = self._svc(cols=cols)
            self.assertEqual(self._refused(svc), (text, 409), cols)
            self.assertEqual(self._writes(cur), [])

    def test_reopens_my_done_row_in_latest_portion(self):
        svc, cur, _ = self._svc(last_done=('asg-old', 'portion-1'))
        self.assertEqual(svc.recall_lead(7, self.LEAD)["assignment_id"], 'asg-old')
        self.assertEqual(cur.sql_with("SET state = 'issued', result = '', done_at = NULL")[0][1], ('asg-old',))
        self.assertEqual(cur.sql_with('SET closed_at = NULL WHERE id')[0][1], ('portion-1',))
        self.assertEqual(cur.sql_with('INSERT'), [])
        self.assertEqual(cur.sql_with("state = 'done'")[0][1], (self.LEAD, 7))     # строка именно этого оператора

    def test_appends_to_latest_portion(self):
        # Его строка — в старой выдаче (или её нет вовсе): новая строка в конец последней.
        for last_done in (('asg-old', 'portion-0'), None):
            svc, cur, _ = self._svc(last_done=last_done)
            self.assertEqual(svc.recall_lead(7, self.LEAD)["assignment_id"], 'asg-new')
            self.assertEqual(cur.sql_with('SET size = size + 1, closed_at = NULL')[0][1], ('portion-1',))
            self.assertEqual(cur.sql_with('INSERT INTO dial_list_assignments')[0][1],
                             ('portion-1', self.LEAD, 7, 4))
            self.assertEqual(cur.sql_with('INSERT INTO dial_list_portions'), [])
            self.assertEqual(cur.sql_with("SET state = 'issued'"), [])
            # Лид не трогаем: из пула его убирает сама открытая строка.
            self.assertEqual(cur.sql_with('UPDATE dial_list_leads'), [])

    def test_creates_portion_when_operator_has_none(self):
        svc, cur, _ = self._svc(last_portion=None)
        self.assertEqual(svc.recall_lead(7, self.LEAD)["assignment_id"], 'asg-new')
        portion = cur.sql_with('INSERT INTO dial_list_portions')[0]
        self.assertIn('VALUES (%s, %s, 1)', portion[0])
        self.assertEqual(portion[1], (7, 1954))
        self.assertEqual(cur.sql_with('INSERT INTO dial_list_assignments')[0][1], ('portion-new', self.LEAD, 7, 1))

    def test_race_with_another_operator_is_409(self):
        svc, cur, db = self._svc(insert_error=psycopg2.errors.UniqueViolation())
        self.assertEqual(self._refused(svc), ("Водитель уже в списке у другого оператора", 409))
        self.assertEqual(db.rolled_back, [True])

    def test_one_transaction_under_operator_lock(self):
        svc, cur, db = self._svc()
        svc.recall_lead(7, self.LEAD)
        self.assertEqual(len(db.transactions), 1)
        self.assertEqual(db.rolled_back, [False])
        self.assertIn('pg_advisory_xact_lock(hashtext(%s))', db.transactions[0][0])
        self.assertEqual(cur.sql_with('pg_advisory_xact_lock')[0][1], ('dial_list_operator:7',))
        self.assertIn('FOR UPDATE OF l', db.transactions[0][1])
        src = inspect.getsource(dial_service.DialListService.recall_lead)
        self.assertNotIn('_client(', src)                                          # без сети

    def test_button_and_endpoint_share_one_rule(self):
        svc = dial_service.DialListService
        for sql in (svc._WORKED_SQL, svc._RECALL_LEAD_SQL):
            self.assertIn(svc._RECALL_JOINS_SQL, sql)
            self.assertIn(svc._RECALL_COLUMNS, sql)
        self.assertIn('recall_verdict(r[18:25]', inspect.getsource(svc.operator_worked))
        self.assertIn('recall_verdict(row[1:8]', inspect.getsource(svc.recall_lead))
        joins = svc._RECALL_JOINS_SQL
        for fragment in ('rt.outcome_id IS NOT NULL AND NOT rt.cancelled', 'ORDER BY rt.requested_at DESC LIMIT 1',
                         'rt2.operator_id <> %(operator_id)s', 'rt2.requested_at > rt.requested_at',
                         "rx.state = 'issued'", 'COALESCE(ro.requeue, FALSE)'):
            self.assertIn(fragment, joins)
        self.assertEqual(len([c for c in svc._RECALL_COLUMNS.split(',') if c.strip()]), 7)

    def test_route_is_in_operator_zone(self):
        import flask
        seen = []

        class Svc:
            def recall_lead(self, user_id, lead_id):
                seen.append((user_id, lead_id))
                return {"assignment_id": "asg-new", "portion": None}

        requester = (5, 'Оператор', 'x', 'operator', None, None, None, 'op')
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (5, requester, None), is_admin_role=lambda role: False,
            headed_department_ids=lambda rid: [], service=Svc())
        app = flask.Flask('dial_list_recall_test')
        app.register_blueprint(bp)
        resp = app.test_client().post(f'/api/operator/dial_list/leads/{self.LEAD}/recall', json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"status": "success", "assignment_id": "asg-new", "portion": None})
        self.assertEqual(seen, [(5, self.LEAD)])
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        operator_part = src[src.index('# ── телефон оператора'):src.index('# ── руководитель')]
        chunk = operator_part[operator_part.index("'/api/operator/dial_list/leads/<lead_id>/recall'"):]
        self.assertIn('@require_api_key', chunk[:300])
        self.assertIn('@_guard', chunk[:300])


if __name__ == '__main__':
    unittest.main()
