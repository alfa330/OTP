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
import threading
import time
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

# Версия телефона — заголовок X-ICore-Phone-Version (с 3.22.34). Запрос без него сервер считает
# выпущенным 3.22.33 (active_attempt — в прежнем смысле).
GUARDED_PHONE = '3.22.34'
# Телефон с простым таймером автосброса (13.10.2026): только ему — ring_timeout_sec и ответ по делу на
# вопрос ring_timeout; 3.22.34 и старше — 0 и «не класть».
TIMER_PHONE = '3.22.35'
# Что телефон с 3.22.34 сообщает о попытке — хвост строк с попыткой у подделок курсора:
# auto_hangup, talk_detected и billsec исхода. Здесь — «телефон ничего не сообщил» (до 3.22.34).
PHONE_SAID_NOTHING = ('', None, 0)


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
    не годится: сервис гасит любую ошибку call-details); max_waits — сколько сервис
    разрешил ждать очереди у Binotel (0 — фоновый опрос); timeouts — таймаут HTTP вызова
    (у опроса живых звонков — свой короткий, live_calls.HTTP_TIMEOUT)."""

    def __init__(self, db, answer):
        self.db, self.answer = db, answer
        self.calls, self.inside_tx, self.max_waits, self.timeouts = [], [], [], []

    def call_details(self, general_call_id, max_wait=None, timeout=None):
        self.calls.append(general_call_id)
        self.inside_tx.append(self.db.in_tx)
        self.max_waits.append(max_wait)
        self.timeouts.append(timeout)
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
                     '_precheck_signatures', '_precheck_call_signature', 'recall_lead', '_recall_row',
                     'operator_sign_check', '_operator_sign_lead', '_close_signed_row', '_sign_ask_slot',
                     '_last_comment', '_last_comment_fields', '_ring_timeout_unguarded', 'phone_event',
                     '_outcome_decision', '_pending_outcome', '_mark_ring_timeout', '_clear_ring_timeout',
                     '_unanswer_attempt', 'attempt_live', 'reconcile', '_live_attempts', '_calls_fetch',
                     '_calls_seen', '_settle_live', '_row_settling', '_raise_answered', '_reanswer_attempt',
                     'repair_cut_calls', '_repair_one', '_repair_item', '_operator_base_counts',
                     '_operator_salary', '_day_results', '_operator_day', 'operator_rating'):
            src = inspect.getsource(getattr(dial_service.DialListService, name))
            self.assertNotIn('phone_norm', src, f'{name} выбирает номер — он уедет в телефон')
        for name in ('_RECALL_JOINS_SQL', '_RECALL_COLUMNS', '_POOL_CANDIDATES_SQL', '_OPERATOR_SIGN_LEAD_SQL',
                     '_LAST_COMMENT_JOIN_SQL', '_LAST_COMMENT_COLUMNS', '_LAST_COMMENT_SQL', '_WORKED_SQL',
                     '_LIVE_ATTEMPTS_SQL', '_REPAIR_CUT_SQL', '_ACTIVE_MEMBER_SQL'):
            self.assertNotIn('phone_norm', getattr(dial_service.DialListService, name), name)
        # ИИН читает только проверка подписания — для Sapar; в ответы телефону он не идёт
        # (ответ «Проверить на подписание» сторожит OperatorSignCheckTests). Опрос живых звонков
        # и починка автосброса — тоже без ИИН.
        for name in ('_RECALL_JOINS_SQL', '_RECALL_COLUMNS', '_WORKED_SQL', '_LAST_COMMENT_JOIN_SQL',
                     '_LAST_COMMENT_COLUMNS', '_LAST_COMMENT_SQL', '_LIVE_ATTEMPTS_SQL', '_REPAIR_CUT_SQL'):
            self.assertNotIn('iin', getattr(dial_service.DialListService, name), name)
        # Комментарий оператора (12.10.2026) собирается без таблицы лидов вовсе: попытки,
        # строки выдачи и справочник итогов — ни ФИО, ни номера, ни ИИН взять неоткуда.
        self.assertNotIn('dial_list_leads', dial_service.DialListService._LAST_COMMENT_JOIN_SQL)
        self.assertNotIn('dial_list_leads', dial_service.DialListService._LAST_COMMENT_SQL)
        for name in ('_portion_items', '_last_comment', '_last_comment_fields', '_ring_timeout_unguarded'):
            self.assertNotIn('iin', inspect.getsource(getattr(dial_service.DialListService, name)), name)
        for helper in (dial_service.search_key, dial_service.name_matches, dial_service.operator_sign_status):
            self.assertNotIn('phone', inspect.getsource(helper), helper.__name__)
            self.assertNotIn('iin', inspect.getsource(helper), helper.__name__)
        # Оплата по конверсии и рейтинг дня (14.10.2026): только числа и имена операторов. Номер
        # лида в запросе дня читает лишь условие «Реестра тестовых номеров» (у звонков и у успешек
        # своё) — без него там нет ни номера, ни ИИН, ни ФИО водителя.
        from test_numbers import keys as test_keys
        day_sql = dial_service.DialListService._DAY_RESULTS_SQL
        self.assertEqual(day_sql.count(test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL), 2)
        bare = day_sql.replace(test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL, '')
        for forbidden in ('phone_norm', 'iin', 'full_name'):
            self.assertNotIn(forbidden, bare, f'_DAY_RESULTS_SQL: {forbidden}')
        for name in ('_operator_base_counts', '_operator_salary', '_day_results', '_operator_day', 'operator_rating'):
            self.assertNotIn('iin', inspect.getsource(getattr(dial_service.DialListService, name)), name)
        for name in ('_day_results', '_operator_day', 'operator_rating'):
            self.assertNotIn('full_name', inspect.getsource(getattr(dial_service.DialListService, name)), name)
        # Правила оплаты — чистая арифметика: ни базы, ни номеров.
        from dial_list import pay
        for forbidden in ('phone_norm', 'iin', 'full_name', '_get_cursor', 'execute('):
            self.assertNotIn(forbidden, inspect.getsource(pay), f'pay.py: {forbidden}')

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
        self.assertEqual(dial_service.binotel_error_code(
            "Binotel calls/internal-number-to-external-number status='error' code=150: Can't call to the ext"), 150)
        self.assertEqual(dial_service.call_error_kind(None,
                         "Binotel calls/internal-number-to-external-number status='error' code=150: x"), 'line')
        self.assertIn('count_for_lead=kind not in CALL_ERRORS_NOT_COUNTED', src)
        self.assertIn('line', dial_service.CALL_ERRORS_NOT_COUNTED)
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
        # Карточка, «вернуть», «исключить», «проверить подписание», заметка, запись, список,
        # починка автосброса (13.10.2026) и детализация звонков (15.10.2026).
        self.assertEqual(len(routes), 9)
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
        # и хвост PHONE_SAID_NOTHING: телефон до 3.22.34 о звуке ничего не сообщает — для него
        # правила прежние (что меняют auto_hangup и talk_detected — PhoneHeardTests).
        def decide(*head):
            return svc._outcome_decision(_Cur(head + PHONE_SAID_NOTHING), 'x')

        self.assertEqual(decide('finished', 'CANCEL', True, 't', 't', None, None), (True, False))
        self.assertEqual(decide('ended', '', False, None, 't', None, None), (False, True))
        self.assertEqual(decide('ended', '', False, 't', 't', None, None), (False, None))
        self.assertEqual(decide('finished', 'ANSWERED', False, 't', 't', None, None), (False, True))
        self.assertEqual(decide('finished', 'NOANSWER', False, 't', 't', None, None), (False, False))
        self.assertEqual(decide('finished', 'ANSWERED', False, None, 't', 'o', None), (False, False))
        self.assertEqual(decide('failed', '', False, None, None, None, None), (False, False))
        self.assertEqual(svc._outcome_decision(_Cur(None), 'x'), (False, False))
        # Сброс на гудках (11.10.2026) — как отбой оператора, только без отмены: водитель не
        # ответил — итог не нужен, ответил в последний момент — нужен, исхода ещё нет — ждём.
        self.assertEqual(decide('finished', 'CANCEL', False, None, 't', None, 't'), (False, False))
        self.assertEqual(decide('finished', 'ANSWER', False, None, 't', None, 't'), (False, True))
        self.assertEqual(decide('ended', '', False, None, 't', None, 't'), (False, None))
        self.assertEqual(decide('finished', 'NOANSWER', False, 't', 't', None, 't'), (False, False))
        # Плечо телефон не подтвердил (ACK не дошёл, 11.10.2026): итог нужен, только если
        # Binotel подтвердил ответ водителя; исход ещё не известен или не ответил — не нужен.
        self.assertEqual(decide('finished', 'ANSWER', False, None, None, None, None), (False, True))
        self.assertEqual(decide('finished', 'CANCEL', False, None, None, None, None), (False, False))
        self.assertEqual(decide('ended', '', False, None, None, None, None), (False, False))
        self.assertEqual(decide('finished', 'ANSWER', False, None, None, 'o', None), (False, False))


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
        # Конверсия (11.10.2026) и зарплата (14.10.2026) — запросы прогресса, которым нужна
        # таблица лидов: база месяца, засчитанные оператору успешки, итоги дня. Поэтому сторож
        # у них свой: только счётчики — внешний SELECT из трёх подзапросов, все COUNT (третий,
        # 14.10.2026, — обзвонённые для зарплаты), — и ни ФИО, ни номера, ни ИИН (номер читает
        # лишь условие реестра, у каждого подзапроса своё).
        conversion = svc._PROGRESS_CONVERSION_SQL
        self.assertEqual(conversion.count(test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL), 3)
        bare = conversion.replace(test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL, '')
        for forbidden in ('phone_norm', 'full_name', 'iin'):
            self.assertNotIn(forbidden, bare)
            for method in (svc._operator_conversion, svc._operator_base_counts, svc._operator_salary,
                           svc._operator_day, svc._day_results):
                self.assertNotIn(forbidden, inspect.getsource(method), method.__name__)
        heads = [bare[m.end():].split(None, 1)[0] for m in re.finditer(r'\bSELECT\b', bare)]
        self.assertEqual(heads, ['(SELECT', 'COUNT(DISTINCT', 'COUNT(*)', 'COUNT(DISTINCT'])
        self.assertEqual(re.findall(r'\bAS (\w+)', bare), ['leads_talked', 'successes', 'leads_called'])
        # «Сегодня» у зарплаты — итоги дня с отбором оператора: наружу из них уходят только два
        # числа (диалоги и подписавшие); имя оператора — из users, водителей там нет.
        day = svc._DAY_RESULTS_SQL.replace(test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL, '')
        self.assertEqual(re.findall(r'\bAS (\w+)', day), ['attempts', 'dialogs', 'operator_id', 'successes'])
        self.assertIn("LEFT JOIN users u ON u.id = COALESCE(c.operator_id, s.operator_id)", day)
        self.assertIn('_operator_day(ctx, today)', progress)
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

    def test_live_status_comes_from_the_shared_company_poll(self):
        # 13.10.2026: карточка звонка сама Binotel не спрашивает — общий опрос компании (live_calls),
        # один запрос на всех операторов не чаще CALLS_REFRESH_SEC.
        live = inspect.getsource(dial_service.DialListService.attempt_live)
        self.assertNotIn('call_details(', live)
        self.assertIn('self._calls_seen(', live)
        self.assertIn('live_verdict(', live)
        self.assertIn('_finish_by_call', live)     # известный финал закрывает попытку, как phone_event
        self.assertGreaterEqual(dial_service.CALLS_REFRESH_SEC, 3)
        for name in ('reconcile', 'phone_event', '_ring_timeout_event'):
            src = inspect.getsource(getattr(dial_service.DialListService, name))
            self.assertNotIn('call_details(', src, name)
            self.assertIn('self._calls_seen(', src, name)


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
        # и хвост PHONE_SAID_NOTHING — auto_hangup, talk_detected, billsec.
        cur = _RoutedCursor([
            ('FOR UPDATE OF t', {'one': ('ended', 'lead-1', None, False, None, '', None) + PHONE_SAID_NOTHING}),
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
        cur.rules[0] = ('FOR UPDATE OF t', {'one': ('finished', 'lead-1', None, True, 't', 'CANCEL', None)
                                            + PHONE_SAID_NOTHING})
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
        # Хвост строки пачки (12.10.2026): auto_hangup последней попытки и три колонки
        # последнего комментария оператора — их разбирает LastCommentTests.
        portion_item = ('asg-1', 1, 'Иванов', 'done', 'answered', 1, None, 'att-1', 'finished', 'ANSWER', None,
                        'gc', 'Отказ', '#FF3B30', False, None, 'lead-1', 'Дорого', '', None, None, None)
        plain_item = ('asg-2', 2, 'Петров', 'issued', '', 0, None, None, None, None, None,
                      None, None, None, None, None, 'lead-2', None, None, None, None, None)
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
        # Новые — в конце: типы итога, за ними подписание документов (29.09.2026), за ним —
        # что телефон сообщил о последнем звонке (12.10.2026), за ним — автообзвон Тез (15.10.2026).
        self.assertEqual(columns[37:39], ['last_outcome_subtype_id', 'last_outcome_subtype_name'])
        self.assertEqual(columns[39:50], ['iin', 'sign_status', 'sign_checked_at', 'sign_error', 'signed_at',
                                          'success_attempt_id', 'success_operator_id', 'success_operator_name',
                                          'success_resolved_at', 'last_auto_hangup', 'last_talk_detected'])
        self.assertEqual(columns[50:], ['due_kind', 'owner_call_at', 'promised_online_at', 'owner_operator_id',
                                        'owner_operator_name', 'month_first_order_at', 'last_order_before_at',
                                        'trip_checked_at', 'driver_id'])
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
        # За типом итога — ring_timeout_at (сброс на гудках, 11.10.2026), auto_hangup и
        # talk_detected (что сообщил телефон, 12.10.2026).
        cur = _RoutedCursor([
            ('l.id = %(lead_id)s', {'one': header}),
            ('os.id, os.name', {'all': [tuple(base + [self.S1, 'Дорого', None, '', None]),
                                        tuple(base + [None, None, None, '', None])]}),
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

    def _worked(self, rows, outcomes, limit=None, **kwargs):
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
            return svc.operator_worked(7, **kwargs)

    def _worked_q(self, rows, outcomes, q, limit=None):
        return self._worked(rows, outcomes, limit=limit, q=q)

    # Хвост строки _WORKED_SQL (новые колонки — только в конец): r[16] — подписал или на
    # проверке у Яндекса, r[17] — «перезвонить» ли итог строки, r[18:25] —
    # _RECALL_COLUMNS: подписал, исключён, открытая строка (id, чья), кто звонил последним
    # (не отменённая попытка — с 12.10.2026 любая, до того — с итогом), «перезвонить» ли её
    # итог, звонил ли после неё другой оператор. За ними (12.10.2026): r[25] — auto_hangup
    # последней попытки строки, r[26:29] — последний комментарий оператора по водителю
    # (текст, когда оставлен, название итога той попытки).
    NO_RECALL = (False, False, None, None, None, False, False)
    NO_COMMENT = (None, None, None)

    @classmethod
    def _row(cls, *head, signed=False, requeue=False, recall=None, auto_hangup='', last_comment=None,
             department=None):
        # department — отдел базы водителя (r[29], 13.10.2026); не задан — строка прежней ширины.
        tail = () if department is None else (department,)
        return (head + (signed, requeue) + (recall or cls.NO_RECALL) + (auto_hangup,)
                + (last_comment or cls.NO_COMMENT) + tail)

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

    def test_call_again_and_recall_follow_the_recall_rule(self):
        # «Позвонить ещё раз» (12.10.2026) — на любой вкладке, если водитель свой и свободен;
        # нельзя — почему, тем же текстом, что ответит ручка recall (recall_verdict): кнопка
        # не врёт. can_recall — кнопка «Перезвонить» телефона 3.22.33: то же решение, но только
        # у строки с итогом «перезвонить» (11.10.2026), — телефон работает без изменений.
        from datetime import datetime as dt, timezone as tz
        outcomes = [('cb', 'Перезвонить позже', '#FF9F0A', 1, True, True, 0, []),
                    ('no', 'Отказ', '#FF3B30', 2, False, True, 0, [])]
        own = (False, False, None, None, 7, True, False)
        refusals = dial_service.RECALL_REFUSALS

        def row(name, recall, requeue=True, outcome='cb', hour=9, signed=False):
            at = dt(2026, 10, 11, hour, tzinfo=tz.utc)
            return self._row(f'a-{name}', f'l-{name}', name, 'done', 'answered', at, at, outcome, at, '', 'finished',
                             None, None, None, 'in_progress', None, requeue=requeue, recall=recall, signed=signed)

        rows = [
            row('свой', own, hour=9),
            row('подписал', (True,) + own[1:], hour=10, signed=True),
            row('исключён', (False, True) + own[2:], hour=11),
            row('у другого', own[:2] + ('a-x', 8) + own[4:], hour=12),
            row('уже у меня', own[:2] + ('a-y', 7) + own[4:], hour=13),
            row('чужой итог', own[:4] + (8, True, False), hour=14),
            row('звонил другой', own[:6] + (True,), hour=15),
            row('отказ', own[:5] + (False, False), requeue=False, outcome='no', hour=16),
            row('никто', own[:4] + (None, False, False), hour=17),
        ]
        # Недозвон: вкладка «Не дозвонились», итога у строки нет, последней была моя попытка.
        rows.append(self._row('a-n', 'l-n', 'Недозвон', 'done', 'no_answer', dt(2026, 10, 11, 18, tzinfo=tz.utc),
                              None, None, None, '', 'finished', None, None, None, 'in_progress', None,
                              recall=own[:5] + (False, False)))
        items = {i['full_name']: i for i in self._worked(rows, outcomes)['items']}
        self.assertEqual({n: (i['can_call_again'], i['call_again_block'], i['can_recall']) for n, i in items.items()}, {
            'свой': (True, '', True),
            'подписал': (False, refusals['signed'], False),
            'исключён': (False, refusals['excluded'], False),
            'у другого': (False, refusals['issued_other'], False),
            'уже у меня': (True, '', False),
            'чужой итог': (False, refusals['taken'], False),
            'звонил другой': (False, refusals['taken'], False),
            'отказ': (True, '', False),
            'никто': (False, refusals['not_own'], False),
            'Недозвон': (True, '', False),
        })
        self.assertEqual(items['Недозвон']['tab'], dial_service.WORKED_NO_ANSWER)
        self.assertEqual(items['отказ']['tab'], 'no')
        # signed — подписал или на проверке у Яндекса (r[16]).
        self.assertEqual({n for n, i in items.items() if i['signed']}, {'подписал'})
        # Колонки — в конце строки, после прежних 17: r[17] — «перезвонить» ли итог строки,
        # за ним — колонки «Позвонить ещё раз»; новые (12.10.2026: auto_hangup и последний
        # комментарий оператора; 13.10.2026: отдел базы водителя) — только после них, прежние
        # номера колонок не сдвинуты.
        svc = dial_service.DialListService
        sql = svc._WORKED_SQL
        select = sql[sql.index('SELECT s.id'):sql.index('FROM latest s')]
        self.assertTrue(' '.join(select.split()).endswith(
            "COALESCE(o.requeue, FALSE), " + ' '.join(svc._RECALL_COLUMNS.split())
            + ", t.auto_hangup, " + svc._LAST_COMMENT_COLUMNS + ", l.department_id"))
        self.assertIn("(l.signed_at IS NOT NULL OR l.sign_status = 'processing'),\n", select)
        self.assertEqual(svc._LAST_COMMENT_COLUMNS, 'lc.last_comment, lc.last_comment_at, lc.last_comment_outcome')

    def test_driver_from_previous_department_cannot_be_called_again(self):
        # 13.10.2026: оператора перевели (или сняли с линии другого отдела) — его водители прежнего
        # отдела остаются на вкладках, но «Позвонить ещё раз» им нельзя: ручка recall ищет водителя
        # в базе нынешнего отдела и ответила бы «не найден». Кнопка не врёт — говорит почему.
        from datetime import datetime as dt, timezone as tz
        at = dt(2026, 10, 11, 9, tzinfo=tz.utc)
        outcomes = [('cb', 'Перезвонить позже', '#FF9F0A', 1, True, True, 0, [])]
        own = (False, False, None, None, 7, True, False)

        def row(name, department):
            return self._row(f'a-{name}', f'l-{name}', name, 'done', 'answered', at, at, 'cb', at, '', 'finished',
                             None, None, None, 'in_progress', None, requeue=True, recall=own, department=department)

        items = {i['full_name']: i for i in self._worked([row('Свой отдел', 1), row('Прежний отдел', 1954)],
                                                         outcomes)['items']}
        self.assertEqual((items['Свой отдел']['can_call_again'], items['Свой отдел']['can_recall']), (True, True))
        foreign = items['Прежний отдел']
        self.assertEqual((foreign['can_call_again'], foreign['call_again_block'], foreign['can_recall']),
                         (False, 'Водитель из другого отдела', False))
        self.assertEqual(foreign['tab'], 'cb')                     # на вкладке итога он остаётся
        self.assertEqual(dial_service.RECALL_OTHER_DEPARTMENT, 'Водитель из другого отдела')

    def test_signed_driver_stays_on_outcome_tab_with_flag(self):
        # Подписавший с итогом — на вкладке итога с отметкой signed и без «Позвонить ещё раз»;
        # закрытая из-за подписи строка без итога (сервер, не АТС) — не «Не дозвонились».
        from datetime import datetime as dt, timezone as tz
        outcomes = [('no', 'Отказ', '#FF3B30', 1, False, True, 0, [])]
        signed_cols = (True, False, None, None, 7, False, False)
        at = dt(2026, 10, 12, 9, tzinfo=tz.utc)
        rows = [
            self._row('a1', 'l1', 'Подписавший', 'done', 'answered', at, at, 'no', at, '', 'finished', 'Отказ',
                      '#FF3B30', 1, 'done', None, signed=True, recall=signed_cols),
            self._row('a2', 'l2', 'Снят сервером', 'done', 'other', at, at, None, None, '', None, None, None, None,
                      'in_progress', None, signed=True, recall=signed_cols),
        ]
        res = self._worked(rows, outcomes)
        self.assertEqual([(i['full_name'], i['signed'], i['can_call_again'], i['call_again_block'])
                          for i in res['items']],
                         [('Подписавший', True, False, dial_service.RECALL_REFUSALS['signed'])])

    # ── поиск по ФИО (владелец, 12.10.2026) ──────────────────────────────────
    def test_search_key_and_name_matching(self):
        key, match = dial_service.search_key, dial_service.name_matches
        self.assertEqual(key('  СЕМЁНОВ   Пётр\tИльич '), 'семенов петр ильич')
        self.assertEqual(key(None), '')
        self.assertTrue(match('Семёнов Пётр', 'семенов'))               # регистр и ё/е
        self.assertTrue(match('Семенов Петр', 'СЕМЁН'))
        self.assertTrue(match('Петров Иван Сергеевич', 'иван  петр'))    # слова — в любом порядке, части слов
        self.assertFalse(match('Петров Иван', 'иван сидоров'))           # каждое слово должно найтись
        self.assertTrue(match('Петров Иван', '   '))                     # пустой запрос — все
        self.assertTrue(match('Ахметов Ерлан', 'ерлан'))

    def _search_rows(self):
        from datetime import datetime as dt, timezone as tz
        outcomes = [('o1', 'Отказ', '#FF3B30', 1, False, True, 0, []),
                    ('o2', 'Заинтересован', '#34C759', 2, False, True, 0, [])]
        at = lambda h: dt(2026, 10, 12, h, tzinfo=tz.utc)
        row = self._row
        rows = [
            row('a1', 'l1', 'Семёнов Пётр', 'done', 'answered', at(9), at(8), 'o1', at(9), '', 'finished', 'Отказ',
                '#FF3B30', 1, 'done', None),
            row('a2', 'l2', 'Петрова Анна', 'done', 'answered', at(10), at(8), 'o2', at(10), '', 'finished',
                'Заинтересован', '#34C759', 2, 'done', None),
            row('a3', 'l3', 'Иванов Семен', 'done', 'no_answer', at(11), at(8), None, None, '', 'finished', None,
                None, None, 'in_progress', None),
            # Итог выключили после звонка: вкладка живёт, пока по ней кто-то есть.
            row('a4', 'l4', 'Сидоров Ерлан', 'done', 'answered', at(12), at(8), 'old', at(12), '', 'finished',
                'Думает', '#AF52DE', 9, 'done', None),
        ]
        return rows, outcomes

    def test_search_filters_names_before_counting(self):
        rows, outcomes = self._search_rows()
        whole = self._worked(rows, outcomes)
        res = self._worked_q(rows, outcomes, '  СЕМЁН ')
        self.assertEqual(res['q'], 'семен')                               # в ответе — нормализованный
        self.assertEqual([i['full_name'] for i in res['items']], ['Иванов Семен', 'Семёнов Пётр'])
        # Набор вкладок тот же (и вкладка выключенного итога), count — найденные.
        self.assertEqual([t['key'] for t in res['tabs']], [t['key'] for t in whole['tabs']])
        self.assertEqual([(t['name'], t['count']) for t in res['tabs']],
                         [('Отказ', 1), ('Заинтересован', 0), ('Думает', 0), ('Не дозвонились', 1)])
        # Слова в любом порядке и по любой вкладке.
        res = self._worked_q(rows, outcomes, 'петр сем')
        self.assertEqual([i['full_name'] for i in res['items']], ['Семёнов Пётр'])
        self.assertEqual(self._worked_q(rows, outcomes, 'нет такого')['items'], [])

    def test_empty_search_changes_nothing(self):
        rows, outcomes = self._search_rows()
        whole = self._worked(rows, outcomes)
        self.assertEqual(whole['q'], '')
        for q in ('', '   ', None):
            self.assertEqual(self._worked_q(rows, outcomes, q), whole)

    def test_search_cap_counts_only_matches(self):
        # Отбор — до WORKED_TAB_LIMIT: свежие ненайденные не съедают место найденным.
        from datetime import datetime as dt, timezone as tz
        outcomes = [('o1', 'Отказ', '#FF3B30', 1, False, True, 0, [])]
        rows = [self._row(f'a{n}', f'l{n}', f'Водитель {n}', 'done', 'answered', None, dt(2026, 10, 2, tzinfo=tz.utc),
                          'o1', dt(2026, 10, 2, 20 - n, tzinfo=tz.utc), '', 'finished', 'Отказ', '#FF3B30', 1,
                          'done', None) for n in range(5)]
        rows += [self._row(f'b{n}', f'm{n}', f'Жумабаев {n}', 'done', 'answered', None,
                           dt(2026, 10, 2, tzinfo=tz.utc), 'o1', dt(2026, 10, 2, 5 - n, tzinfo=tz.utc), '',
                           'finished', 'Отказ', '#FF3B30', 1, 'done', None) for n in range(3)]
        res = self._worked_q(rows, outcomes, 'жумабаев', limit=2)
        self.assertEqual(res['tabs'][0]['count'], 3)
        self.assertEqual([i['full_name'] for i in res['items']], ['Жумабаев 0', 'Жумабаев 1'])

    def test_search_query_is_cut_and_never_reaches_phone_or_iin(self):
        rows, outcomes = self._search_rows()
        res = self._worked_q(rows, outcomes, 'семен ' + 'я' * 300)
        self.assertLessEqual(len(res['q']), dial_service.WORKED_QUERY_MAX)
        self.assertEqual(dial_service.WORKED_QUERY_MAX, 100)
        # Ищем только по ФИО: ни номера, ни ИИН в отборе нет (и SQL не меняется).
        src = inspect.getsource(dial_service.DialListService.operator_worked)
        self.assertNotIn('phone', src)
        self.assertNotIn('iin', src)
        self.assertNotIn('ILIKE', dial_service.DialListService._WORKED_SQL)

    def test_worked_route_passes_query(self):
        import flask
        seen = []

        class Svc:
            def operator_worked(self, user_id, q=''):
                seen.append((user_id, q))
                return {"q": q, "tabs": [], "items": []}

        requester = (5, 'Оператор', 'x', 'operator', None, None, None, 'op')
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (5, requester, None), is_admin_role=lambda role: False,
            headed_department_ids=lambda rid: [], service=Svc())
        app = flask.Flask('dial_list_worked_test')
        app.register_blueprint(bp)
        client = app.test_client()
        self.assertEqual(client.get('/api/operator/dial_list/worked?q=%D0%A1%D0%B5%D0%BC%D1%91%D0%BD').status_code, 200)
        client.get('/api/operator/dial_list/worked')
        self.assertEqual(seen, [(5, 'Семён'), (5, '')])

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

    def _state(self, cursor, phone_version=TIMER_PHONE):
        """Состояние для телефона с простым таймером (3.22.35+): автосброс получает только он.
        Что получают 3.22.33 и 3.22.34 — PhoneVersionGateTests."""
        db = _TxDb(cursor)
        svc = dial_service.DialListService(db=db)
        svc.operator_context = self._ctx
        svc.reconcile = lambda ctx: None
        return svc.get_state(7, phone_version=phone_version), db

    def test_state_carries_ring_timeout_of_line_department(self):
        # Отсчёт телефона — от принятия плеча, а первый гудок звучит на 8–10-й секунде (сообщение АТС
        # оператору и соединение сети): RING_LEAD_IN_SEC сверху — 5 гудков — 34 с; 0 гудков — 0.
        self.assertEqual(dial_service.RING_LEAD_IN_SEC, 9)
        for row, expected in (((None,), 34), (None, 34), ((0,), 0), ((3,), 24), ((40,), 109)):
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
        self.assertEqual(state["ring_timeout_sec"], 34)
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
    # Вопрос по делу — только от телефона с простым таймером (3.22.35+, _ring): 3.22.33 и 3.22.34
    # сервер отвечает «не класть» сразу, не спрашивая Binotel (PhoneVersionGateTests).
    def _event_svc(self, row, decision, answer=None, rings=(None,)):
        """row — (state, general_call_id, disposition, cancelled) попытки; decision — строка
        _outcome_decision после события (без хвоста PHONE_SAID_NOTHING — его добавляем здесь);
        answer — ответ call-details (или исключение); rings — строка настройки отдела (гудков)."""
        cur = _RoutedCursor([
            ('general_call_id, disposition, cancelled FROM dial_list_attempts', {'one': row}),
            ('leg_answered_at, outcome_id', {'one': decision and tuple(decision) + PHONE_SAID_NOTHING}),
            ('FROM sip_department_config', {'one': rings}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db)
        svc.operator_context = self._ctx
        client = _CallDetailsClient(db, {} if answer is None else answer)
        svc._client = lambda dep: client
        finished = []
        svc._finish_by_call = lambda aid, call, source: finished.append((aid, call, source, len(cur.executed)))
        return svc, cur, client, finished

    @staticmethod
    def _ring(svc, attempt_id='att-1', **kwargs):
        """Событие ring_timeout от телефона с простым таймером (3.22.35)."""
        return svc.phone_event(7, attempt_id, 'ring_timeout', phone_version=TIMER_PHONE, **kwargs)

    # Строки решения: (state, disposition, cancelled, operator_hangup_at, leg_answered_at, outcome_id,
    # ring_timeout_at).
    def test_dialing_still_goes_hang_up_and_mark(self):
        # Binotel всё ещё набирает водителя — класть плечо; попытка помечена сбросом.
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, 't'),
            {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'CALLING'}})
        res = self._ring(svc, ring_sec=25)
        # Возраст данных Binotel, по которым решали (ревью 13.10.2026): только что спросили.
        age = res.pop("data_age_ms")
        self.assertTrue(0 <= age < 1000, age)
        self.assertEqual(res, {"attempt_id": "att-1", "state": "leg_answered", "final": False,
                               "disposition": "CALLING", "cancelled": False, "outcome_required": None,
                               "hangup": True, "talking": False})
        self.assertEqual(client.calls, ['gc-1'])
        self.assertEqual(client.inside_tx, [False])               # сеть — вне транзакций
        # У опроса свой короткий таймаут HTTP, а не 40 с клиента (ревью 13.10.2026).
        self.assertEqual(client.timeouts, [dial_service.live_calls.HTTP_TIMEOUT])
        # Причина — гудки (auto_hangup), секунд плеча у вопроса нет.
        self.assertEqual([p for _, p in cur.sql_with(self.MARK)], [('ring_timeout', 0, 0, 'att-1')])
        self.assertEqual(cur.sql_with('operator_hangup_at = COALESCE'), [])   # это не отбой оператора
        self.assertEqual(finished, [])

    def test_hang_up_rests_only_on_fresh_dialing_data(self):
        # Ревью 13.10.2026: «АТС ещё набирает» велит класть, только если данные не старше
        # RING_HANGUP_MAX_AGE_SEC (3 с): кэш бывает старше — обновление не ушло (секунда на ключ
        # занята, Binotel просит паузу), — а водитель мог снять трубку уже после них и молчать.
        # Старее — «нет данных»: не класть, без disposition и без отметки сброса. Финал «не
        # дозвонились» не устаревает (звонок кончился), «говорят» — тоже верно до 6 с.
        self.assertEqual(dial_service.RING_HANGUP_MAX_AGE_SEC, dial_service.RING_FRESH_SEC + 1.0)
        fresh, max_age = dial_service.RING_HANGUP_MAX_AGE_SEC, dial_service.RING_DATA_MAX_AGE_SEC
        cases = (
            # данные Binotel, их возраст, hangup, talking, final, решали ли по данным
            ('CALLING', fresh + 0.5, False, False, False, False),
            ('RINGING', max_age - 0.5, False, False, False, False),
            ('CALLING', fresh - 0.8, True, False, False, True),
            ('NOANSWER', max_age - 1.0, True, False, True, True),
            ('ONLINE', max_age - 1.0, False, True, False, True),
        )
        for disposition, age, hangup, talking, final, decided in cases:
            svc, cur, client, finished = self._event_svc(
                ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, None))
            calls = svc._company_calls(dial_service.DEFAULT_BINOTEL_COMPANY)
            calls.put('gc-1', {'general_call_id': 'gc-1', 'disposition': disposition, 'billsec': 0, 'waitsec': 25})
            seen = calls.peek('gc-1')
            calls._entries['gc-1'] = seen._replace(mono=seen.mono - age)
            svc._client = lambda dep: (_ for _ in ()).throw(RuntimeError('нет подключения'))   # обновить нечем
            with self.assertLogs('dial_list.service', level='INFO') as logs:
                res = self._ring(svc)
            self.assertEqual((res["hangup"], res["talking"], res["final"]), (hangup, talking, final),
                             (disposition, age))
            self.assertEqual("disposition" in res, decided, (disposition, age))
            if decided:
                self.assertAlmostEqual(res["data_age_ms"] / 1000.0, age, delta=0.5)
            else:
                self.assertNotIn("data_age_ms", res)
                self.assertTrue(any('устарело' in line and 'не кладём' in line for line in logs.output),
                                logs.output)
            self.assertEqual(len(cur.sql_with(self.MARK)), 1 if hangup else 0, (disposition, age))
            self.assertEqual(len(finished), 1 if final else 0, (disposition, age))
            # Решать не по чему — телефон спросит снова (простой таймер, 13.10.2026); иначе — нет.
            self.assertEqual(res.get("retry_after_sec"), None if (hangup or talking or final)
                             else dial_service.RING_RETRY_AFTER_SEC, (disposition, age))

    def test_no_data_never_hangs_up(self):
        # 13.10.2026: «нет данных» — никогда не «класть». Звонка у Binotel не видно, АТС ответила
        # ошибкой или «слишком часто», generalCallID ещё нет — плечо не кладём и сбросом не
        # помечаем: его закончат водитель, АТС или оператор. Раньше молчание значило «класть», и
        # так рвались живые разговоры (12.10.2026: 52 разговора).
        from binotel import client as binotel_client
        for row, answer, asked in ((('leg_answered', 'gc-1', '', False), {}, ['gc-1']),
                                   (('leg_answered', 'gc-1', '', False), RuntimeError('Binotel недоступен'), ['gc-1']),
                                   (('leg_answered', 'gc-1', '', False),
                                    binotel_client.RateLimited('too frequent', retry_after=3), ['gc-1']),
                                   (('requested', None, '', False), {}, [])):
            svc, cur, client, finished = self._event_svc(row, (row[0], '', False, None, 't', None, None), answer)
            with self.assertLogs('dial_list.service', level='INFO') as logs:
                res = self._ring(svc)
            self.assertEqual((res["hangup"], res["talking"], res["final"]), (False, False, False), answer)
            self.assertNotIn("disposition", res)
            # Плечо идёт — телефон спросит снова через RING_RETRY_AFTER_SEC, а не бросит автосброс.
            self.assertEqual(res["retry_after_sec"], dial_service.RING_RETRY_AFTER_SEC, answer)
            self.assertEqual(client.calls, asked)
            self.assertEqual(cur.sql_with(self.MARK), [], answer)
            self.assertEqual(finished, [])
            self.assertTrue(any('нет данных' in line and 'не кладём' in line for line in logs.output), logs.output)
        # Ответ, по которому звонка не разобрать (данные устарели), — тоже «нет данных».
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, None))
        calls = svc._company_calls(dial_service.DEFAULT_BINOTEL_COMPANY)
        calls.put('gc-1', {'general_call_id': 'gc-1', 'disposition': 'CALLING'})
        old = calls.peek('gc-1')
        calls._entries['gc-1'] = old._replace(mono=old.mono - dial_service.RING_DATA_MAX_AGE_SEC - 1)
        svc._client = lambda dep: (_ for _ in ()).throw(RuntimeError('нет подключения'))
        res = self._ring(svc)
        self.assertEqual((res["hangup"], res["talking"]), (False, False))
        self.assertEqual(cur.sql_with(self.MARK), [])

    def test_driver_talking_keeps_the_leg(self):
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, None),
            {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'online'}})
        res = self._ring(svc, ring_sec=25)
        self.assertEqual((res["talking"], res["hangup"], res["final"]), (True, False, False))
        self.assertEqual(res["disposition"], 'ONLINE')
        self.assertEqual(cur.sql_with(self.MARK), [])               # не сброс — отметки нет
        self.assertEqual(finished, [])

    def test_answer_needs_data_fresher_than_the_usual_poll(self):
        # Класть ли трубку — по данным не старше RING_FRESH_SEC: кэш опроса старше двух секунд —
        # обновление, даже если от прошлого прошло меньше обычных CALLS_REFRESH_SEC; своей очереди
        # у Binotel ждём не дольше RING_BINOTEL_WAIT_SEC.
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, None),
            {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'ONLINE'}})
        calls = svc._company_calls(dial_service.DEFAULT_BINOTEL_COMPANY)
        calls.put('gc-1', {'general_call_id': 'gc-1', 'disposition': 'CALLING'})
        seen = calls.peek('gc-1')
        calls._entries['gc-1'] = seen._replace(mono=seen.mono - dial_service.RING_FRESH_SEC - 0.5)
        calls._last_start = seen.mono - dial_service.RING_FRESH_SEC - 0.5     # меньше CALLS_REFRESH_SEC назад
        res = self._ring(svc)
        self.assertEqual(client.calls, ['gc-1'])
        self.assertEqual(client.max_waits, [dial_service.RING_BINOTEL_WAIT_SEC])
        self.assertTrue(res["talking"])
        self.assertFalse(res["hangup"])
        self.assertEqual((dial_service.RING_FRESH_SEC, dial_service.RING_BUDGET_SEC), (2.0, 2.5))
        # Данные моложе двух секунд — Binotel не спрашиваем: ответ из кэша.
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, 't'),
            {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'ONLINE'}})
        svc._company_calls(dial_service.DEFAULT_BINOTEL_COMPANY).put(
            'gc-1', {'general_call_id': 'gc-1', 'disposition': 'CALLING'})
        res = self._ring(svc)
        self.assertEqual((client.calls, res["hangup"], res["disposition"]), ([], True, 'CALLING'))

    def test_final_no_answer_closes_attempt_after_marking(self):
        # Исход уже финальный, водитель не ответил: закрыть попытку как обычно и класть плечо.
        # Отметка — ДО закрытия: закрытие не примет такую попытку за отбой оператора.
        call = {'general_call_id': 'gc-1', 'disposition': 'NOANSWER', 'billsec': 0, 'waitsec': 25}
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('finished', 'NOANSWER', False, None, 't', None, 't'),
            {'gc-1': call})
        res = self._ring(svc)
        self.assertTrue(0 <= res.pop("data_age_ms") < 1000)
        # hangup вместе с final — Binotel подтвердил «не дозвонились»: телефону можно класть и в паузе.
        self.assertEqual(res, {"attempt_id": "att-1", "state": "finished", "final": True,
                               "disposition": "NOANSWER", "cancelled": False, "outcome_required": False,
                               "hangup": True, "talking": False})
        self.assertEqual([f[:3] for f in finished], [('att-1', call, 'poll')])
        mark_at = next(i for i, (sql, _) in enumerate(cur.executed) if self.MARK in sql)
        self.assertLess(mark_at, finished[0][3])

    def test_answer_while_the_leg_is_up_is_a_talk_not_a_final(self):
        # 12.10.2026: Binotel отдаёт ANSWER и посреди идущего разговора, с неполной длительностью.
        # Пока плечо по телефону идёт — водитель говорит: не класть, попытку не закрывать (её
        # закроют данные после конца плеча).
        call = {'general_call_id': 'gc-1', 'disposition': 'ANSWER', 'billsec': 4, 'waitsec': 20}
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, None),
            {'gc-1': call})
        res = self._ring(svc)
        self.assertEqual((res["final"], res["hangup"], res["talking"], res["disposition"]),
                         (False, False, True, 'ANSWER'))
        self.assertEqual(res["state"], 'leg_answered')
        self.assertEqual(cur.sql_with(self.MARK), [])
        self.assertEqual(finished, [])
        # Плечо уже кончилось (ended раньше вопроса) — ANSWER тоже не закрывается этим вопросом.
        svc, cur, client, finished = self._event_svc(
            ('ended', 'gc-1', '', False), ('ended', '', False, None, 't', None, None), {'gc-1': call})
        res = self._ring(svc)
        self.assertEqual((res["final"], res["hangup"], res["talking"]), (False, False, True))
        self.assertEqual(finished, [])

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
            res = self._ring(svc)
            self.assertEqual((res["final"], res["hangup"], res["talking"]), (True, hangup, False), row)
            self.assertEqual(res["cancelled"], bool(decision[2]))
            self.assertEqual(len(cur.sql_with(self.MARK)), marks, row)
            self.assertEqual(client.calls, [])
            self.assertEqual(finished, [])
        # Не ответил — окна итога нет; ответил — итог обязателен.
        svc, _, _, _ = self._event_svc(cases[0][0], cases[0][1])
        self.assertIs(self._ring(svc)["outcome_required"], False)
        svc, _, _, _ = self._event_svc(cases[1][0], cases[1][1])
        self.assertIs(self._ring(svc)["outcome_required"], True)

    def test_unknown_attempt_is_404(self):
        svc, _, client, _ = self._event_svc(None, None)
        with self.assertRaises(dial_service.DialListError) as ctx:
            self._ring(svc, 'att-x')
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(client.calls, [])

    def test_department_switched_off_answers_at_once(self):
        # Отдел выключил автосброс (0 гудков) посреди звонка: телефон прочёл срок раньше и всё же
        # спросил — «не класть» сразу, без Binotel и без отметки. Это выключатель на случай беды.
        svc, cur, client, finished = self._event_svc(
            ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, 't'),
            {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'CALLING'}}, rings=(0,))
        with self.assertLogs('dial_list.service', level='INFO') as logs:
            res = self._ring(svc, ring_sec=34)
        self.assertEqual(res, {"attempt_id": "att-1", "final": False, "cancelled": False,
                               "outcome_required": None, "hangup": False, "talking": False})
        self.assertEqual((client.calls, cur.sql_with(self.MARK), finished), ([], [], []))
        self.assertTrue(any('автосброс выключен' in line for line in logs.output), logs.output)

    # ── ended с ring_timeout: телефон положил плечо сам ──────────────────────
    def _ended_svc(self, row, decision, answer=None):
        cur = _RoutedCursor([
            ('general_call_id, assignment_id, cancelled, disposition, ring_timeout_at', {'one': row}),
            ('leg_answered_at, outcome_id', {'one': decision and tuple(decision) + PHONE_SAID_NOTHING}),
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
        # Ключ ring_timeout телефона 3.22.33 — причина «гудки» (auto_hangup).
        self.assertEqual([p for _, p in cur.sql_with(self.MARK)], [('ring_timeout', 27, 27, 'att-1')])
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
                            ring_timeout=False, ring_sec=0, auto_hangup='', talk_detected=None,
                            phone_version=None, announcement_suspected=None, announcement_by=None):
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
        # hangup_row — (operator_hangup_at, ring_timeout_at); за ними — что сообщил телефон:
        # auto_hangup и talk_detected (здесь — ничего, как у телефона до 3.22.34).
        cur = _RoutedCursor([
            ("SET state = 'finished'", {'one': ('asg-1',)}),
            ('SELECT operator_hangup_at, ring_timeout_at', {'one': tuple(hangup_row) + PHONE_SAID_NOTHING[:2]}),
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
        # когда Binotel подтвердил, что водитель ответил (разговор, который слышал телефон с
        # 3.22.34, — тоже сразу: PhoneHeardTests).
        flat = ' '.join(sql.split())
        self.assertIn("(t.ring_timeout_at IS NULL AND t.state IN ('ended', 'finished')", flat)
        self.assertIn("(t.operator_hangup_at IS NULL AND t.talk_detected IS NULL)", flat)
        self.assertIn("(t.ring_timeout_at IS NOT NULL AND t.state = 'finished'", sql)
        # Плечо без ACK (роутер с SIP ALG), но Binotel соединил водителя — долг, только по
        # свежим попыткам: старые, до итогов, тоже без leg_answered_at.
        self.assertIn("t.outcome_id IS NULL AND t.leg_answered_at IS NULL", sql)
        self.assertIn("t.phone_ended_at IS NOT NULL AND t.state = 'finished'", sql)
        self.assertIn("make_interval(hours => %s)", sql)
        # Четвёртый список ответов Binotel — у ветки «телефон разговора не услышал»
        # (talk_detected FALSE, 12.10.2026 — PhoneHeardTests).
        # Первый список — финальное «не дозвонились» Binotel: разговор, который будто бы слышал
        # телефон, его не перебивает (ревью 13.10.2026, PhoneHeardTests).
        self.assertEqual(params, (7, dial_service.NOT_ANSWERED_FINAL_SQL) + (dial_service.ANSWERED_SQL,) * 4
                         + (dial_service.UNCONFIRMED_LEG_OUTCOME_HOURS,))
        self.assertEqual(dial_service.UNCONFIRMED_LEG_OUTCOME_HOURS, 12)

    def _outcome_svc(self, row):
        # row — без хвоста PHONE_SAID_NOTHING (auto_hangup, talk_detected, billsec): добавляем здесь.
        cur = _RoutedCursor([
            ('FOR UPDATE OF t', {'one': tuple(row) + PHONE_SAID_NOTHING}),
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
            # За ring_timeout_at — auto_hangup и talk_detected: телефон 3.22.33 их не сообщает.
            return ('att', None, 3, 'Оператор', '913', 'finished', disposition, 0, 25, '', 'poll', None, None, None,
                    True, 'asg', 'no_answer', 'done', None, None, None, None, '', None, cancelled, None, 27,
                    None, None, ring_at, '', None)

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
        # Строка счётчиков базы — leads_talked, successes, leads_called (третий — для зарплаты).
        res, cur = self._progress((40, 5, 60), period=date(2026, 9, 1))
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

        res, _ = self._progress((40, 5, 60), cursor_cls=_Broken)
        self.assertNotIn("conversion", res)
        self.assertNotIn("salary", res)                  # зарплата — из тех же счётчиков
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
                         "l.success_operator_id = %(operator_id)s AND l.success_attempt_id IS NOT NULL",
                         # Обзвонённые (14.10.2026) — без попыток, отменённых до ответа.
                         "WHERE t.operator_id = %(operator_id)s AND NOT t.cancelled"):
            self.assertIn(fragment, sql)
        # Тестовые номера — то же условие, что у аналитики, у каждого подзапроса своё.
        self.assertEqual(sql.count(test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL), 3)
        self.assertIn(test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL, analytics._LEADS_CTE)
        # Определение аналитики, с которым прогресс обязан совпадать: поменяли там — тест
        # напомнит поменять и здесь.
        self.assertEqual(set(analytics.ANSWERED_SQL), set(dial_service.ANSWERED_SQL))
        self.assertIn("AND t.billsec >= %(min_sec)s) AS talked", analytics._LEADS_CTE)
        self.assertIn("COUNT(DISTINCT lead_id) FILTER (WHERE NOT cancelled) AS leads_called",
                      analytics._OPERATORS_SQL)
        self.assertIn("COUNT(DISTINCT lead_id) FILTER (WHERE talked) AS leads_talked", analytics._OPERATORS_SQL)
        self.assertIn("FROM leads WHERE success_attempt_id IS NOT NULL GROUP BY success_operator_id",
                      analytics._OPERATORS_SQL)


class OperatorSalaryTests(unittest.TestCase):
    """«Оплата по конверсии» в «Моём прогрессе» (владелец, 14.10.2026): диалоги × цена диалога по
    конверсии — за ту же базу и из того же снимка счётчиков, что и конверсия, — и свои итоги за
    сегодня. Каждая часть — своей транзакцией: сбой одной не роняет остальное."""

    PROGRESS_ROW = (1, 1, 0, 2, 0, 1, 0, 30, 0, 0)

    def _progress(self, base_row=(500, 335, 1000), day_rows=((7, 'Оператор', 9, 4, 2),), broken=()):
        class _Cursor(_RoutedCursor):
            def execute(self, sql, params=None):
                if any(fragment in sql for fragment in broken):
                    raise RuntimeError('canceling statement due to statement timeout')
                super().execute(sql, params)

        cur = _Cursor([
            ('AS issued', {'one': self.PROGRESS_ROW}),
            ('AS leads_talked', {'one': base_row}),
            ('AS dialogs', {'all': list(day_rows)}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db)
        svc.operator_context = lambda uid: {"user_id": uid, "department_id": 1954, "name": "Оператор",
                                            "settings": {"_period": date(2026, 9, 1)}}
        return svc.operator_progress(7), cur, db

    def test_salary_comes_from_the_same_snapshot_as_conversion(self):
        from dial_list import pay
        # Плакат владельца: обзвонили 1000, диалогов 500, подписали 335 → 67 %, 90 тн, 45 000 тн.
        res, cur, db = self._progress()
        salary = res["salary"]
        self.assertEqual({key: salary[key] for key in ("currency", "called", "dialogs", "no_dialog", "successes",
                                                        "percent", "price", "amount", "level")},
                         {"currency": "тн", "called": 1000, "dialogs": 500, "no_dialog": 500, "successes": 335,
                          "percent": 67.0, "price": 90, "amount": 45000, "level": 4})
        self.assertEqual(salary["next"], {"from": 70, "price": 105, "need": 15, "amount": 52500})
        self.assertEqual(salary["tariff"], pay.tariff_rows())
        # База — та же, что у плитки «Конверсия» (период отдела), а не календарный месяц.
        self.assertEqual((salary["period"], salary["period_label"]), ("2026-09-01", "Сентябрь 2026"))
        self.assertEqual(salary["today"], {"dialogs": 4, "successes": 2})
        conversion = res["conversion"]
        self.assertEqual((conversion["leads_talked"], conversion["successes"], conversion["percent"]),
                         (salary["dialogs"], salary["successes"], salary["percent"]))
        self.assertEqual(len(cur.sql_with('AS leads_talked')), 1)     # один снимок на обе
        # «Сегодня» — отдел линии, сегодняшний день по Алматы и отбор этого оператора.
        (_sql, params), = cur.sql_with('AS dialogs')
        self.assertEqual(params, {"department_id": 1954, "day": dial_service._today(), "operator_id": 7,
                                  "answered": dial_service.ANSWERED_SQL,
                                  "min_sec": dial_service.signing.SUCCESS_MIN_BILLSEC})
        # Счётчики прогресса, счётчики базы и «сегодня» — три транзакции.
        self.assertEqual(len(db.transactions), 3)
        self.assertEqual([any('AS leads_talked' in sql for sql in tx) for tx in db.transactions], [False, True, False])
        self.assertEqual([any('AS dialogs' in sql for sql in tx) for tx in db.transactions], [False, False, True])

    def test_no_dialogs_no_pay(self):
        res, _, _ = self._progress(base_row=(0, 0, 3), day_rows=())
        salary = res["salary"]
        self.assertEqual((salary["called"], salary["dialogs"], salary["no_dialog"], salary["percent"],
                          salary["price"], salary["amount"], salary["next"]), (3, 0, 3, None, 30, 0, None))
        self.assertEqual(salary["today"], {"dialogs": 0, "successes": 0})
        self.assertIsNone(res["conversion"]["percent"])

    def test_today_failure_keeps_salary(self):
        res, cur, db = self._progress(broken=('AS dialogs',))
        self.assertNotIn("today", res["salary"])
        self.assertEqual(res["salary"]["amount"], 45000)
        self.assertEqual(res["conversion"]["leads_talked"], 500)
        self.assertEqual(db.rolled_back, [False, False, True])

    def test_base_counts_failure_drops_conversion_and_salary(self):
        res, cur, db = self._progress(broken=('AS leads_talked',))
        self.assertNotIn("conversion", res)
        self.assertNotIn("salary", res)
        # Остальной прогресс — как всегда.
        self.assertEqual(res["month"]["issued"], 1)
        self.assertEqual(res["month"]["talk_sec"], 30)
        self.assertEqual(res["today_stats"]["attempts"], 0)
        self.assertEqual(res["outcomes"], [])
        # «Сегодня» без зарплаты не нужно — и не читается.
        self.assertEqual(cur.sql_with('AS dialogs'), [])
        self.assertEqual(db.rolled_back, [False, True])

    def test_day_results_follow_conversion_definition(self):
        sql = ' '.join(dial_service.DialListService._DAY_RESULTS_SQL.split())
        for fragment in (
                "COUNT(*) FILTER (WHERE NOT t.cancelled) AS attempts",
                "COUNT(DISTINCT a.lead_id) FILTER ( WHERE t.state = 'finished' AND UPPER(t.disposition) IN "
                "%(answered)s AND t.auto_hangup <> 'announcement' AND t.billsec >= %(min_sec)s) AS dialogs",
                # Сутки по Алматы — у звонков и у засчитанной попытки успешки.
                "t.requested_at >= (%(day)s::date)::timestamp AT TIME ZONE 'Asia/Almaty' "
                "AND t.requested_at < ((%(day)s::date) + 1)::timestamp AT TIME ZONE 'Asia/Almaty'",
                "JOIN dial_list_attempts st ON st.id = l.success_attempt_id",
                "st.requested_at >= (%(day)s::date)::timestamp AT TIME ZONE 'Asia/Almaty' "
                "AND st.requested_at < ((%(day)s::date) + 1)::timestamp AT TIME ZONE 'Asia/Almaty'",
                "(%(operator_id)s IS NULL OR t.operator_id = %(operator_id)s)",
                "(%(operator_id)s IS NULL OR l.success_operator_id = %(operator_id)s)",
                "FULL JOIN suc s ON s.operator_id = c.operator_id"):
            self.assertIn(fragment, sql)
        # Отдел — отдел базы (линии), а не отдел человека; месяц базы рейтингу не важен.
        self.assertEqual(sql.count("l.department_id = %(department_id)s"), 2)
        self.assertNotIn('l.period', sql)


class OperatorRatingTests(unittest.TestCase):
    """«Рейтинг» на телефоне (владелец, 14.10.2026): места операторов отдела за день по заработку,
    виден с 21:00 до 10:00 по Алматы, до того — отсчёт и только свои числа. Суммы коллег сервер не
    отдаёт, id операторов — тоже."""

    TZ = dial_service.PERIOD_TZ
    # (оператор, имя, попыток, диалогов, подписавших) — строки _DAY_RESULTS_SQL.
    ROWS = [(520, 'Своев Сава', 8, 4, 2),             # 50 % → 75 тн × 4 = 300
            (521, 'Новичков Назар', 4, 3, 3),         # 100 % → 135 тн × 3 = 405
            (522, 'Третьякова Тамара', 4, 4, 2),      # те же 300, 2 и 4 — то же место
            (523, 'Пустых Прохор', 0, 0, 0),          # одни отменённые до ответа — не звонил
            (None, '', 0, 0, 1)]                      # успешка удалённого сотрудника — ничья

    def _rating(self, now, user=520, broken=False):
        rows = self.ROWS

        class _Cursor(_RoutedCursor):
            def execute(self, sql, params=None):
                if broken:
                    raise RuntimeError('canceling statement due to statement timeout')
                super().execute(sql, params)

        # Подделка отбирает строки, как отбор оператора в SQL: с ним — только его строку.
        cur = _Cursor([('AS dialogs', {'all': lambda p: [r for r in rows if p["operator_id"] in (None, r[0])]})])
        svc = dial_service.DialListService(db=_routed_db(cur))
        svc.operator_context = lambda uid: {"user_id": uid, "department_id": 1954, "name": "Оператор"}
        return svc.operator_rating(user, now=now), cur

    def test_route_is_phone_only_and_takes_no_day(self):
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        operator_part = src[src.index('# ── телефон оператора'):src.index('# ── руководитель')]
        manager_part = src[src.index('# ── руководитель'):]
        self.assertIn("@bp.route('/api/operator/dial_list/rating', methods=['GET', 'OPTIONS'])", operator_part)
        self.assertNotIn('dial_list/rating', manager_part)
        chunk = operator_part[operator_part.index('/api/operator/dial_list/rating'):]
        chunk = chunk[:chunk.index('@bp.route')]
        self.assertIn('@require_api_key', chunk)
        self.assertIn('@_guard', chunk)
        self.assertIn('svc.operator_rating(user_id)', chunk)
        # День выбирает окно на сервере: ни параметров, ни тела запроса.
        self.assertNotIn('request', chunk)

    def test_hidden_until_nine_pm_shows_only_own_numbers(self):
        res, cur = self._rating(datetime(2026, 10, 14, 15, 0, tzinfo=self.TZ))
        self.assertEqual(res, {"visible": False, "day": "2026-10-14", "day_label": "14 октября",
                               "opens_at": "2026-10-14T21:00:00+05:00", "closes_at": "2026-10-15T10:00:00+05:00",
                               "currency": "тн", "seconds_to_open": 6 * 3600,
                               "me": {"dialogs": 4, "successes": 2}})
        # О коллегах ничего — ни имён, ни чисел, ни их количества; из базы читались только свои.
        for name in ('Новичков', 'Третьякова', 'Пустых'):
            self.assertNotIn(name, repr(res))
        (_sql, params), = cur.executed
        self.assertEqual((params["operator_id"], params["department_id"], params["day"]),
                         (520, 1954, date(2026, 10, 14)))

    def test_hidden_window_survives_a_failed_query(self):
        res, _ = self._rating(datetime(2026, 10, 14, 12, 0, tzinfo=self.TZ), broken=True)
        self.assertEqual((res["visible"], res["seconds_to_open"], res["me"]), (False, 9 * 3600, None))
        self.assertNotIn("operators", res)

    def test_open_window_hides_colleagues_amounts(self):
        res, cur = self._rating(datetime(2026, 10, 14, 21, 30, tzinfo=self.TZ))
        self.assertEqual((res["visible"], res["day"], res["seconds_to_close"], res["total"]),
                         (True, "2026-10-14", 12 * 3600 + 30 * 60, 3))
        # Место — по заработку дня (405 выше 300 при меньшей конверсии у 300); полное равенство —
        # одно место, внутри него — по имени. Пустых (только отменённые) и ничья успешка — не в рейтинге.
        self.assertEqual(res["operators"], [
            {"place": 1, "name": "Новичков Назар", "me": False, "dialogs": 3, "successes": 3, "percent": 100.0},
            {"place": 2, "name": "Своев Сава", "me": True, "dialogs": 4, "successes": 2, "percent": 50.0,
             "price": 75, "amount": 300},
            {"place": 2, "name": "Третьякова Тамара", "me": False, "dialogs": 4, "successes": 2, "percent": 50.0},
        ])
        self.assertEqual(res["me"], res["operators"][1])
        # Сумм коллег нет вовсе — ни ключей, ни чисел; id операторов наружу не уходят.
        values = [value for row in res["operators"] for value in row.values()]
        self.assertNotIn(405, values)
        self.assertNotIn(135, values)
        for row in res["operators"]:
            self.assertNotIn("operator_id", row)
            self.assertNotIn("id", row)
        (_sql, params), = cur.executed
        self.assertIsNone(params["operator_id"])

    def test_colleague_without_name_is_not_shown_by_id(self):
        # Пустое users.name: у руководителя в аналитике это «#id», а коллегам в рейтинг id не
        # уходит — нейтральная подпись (ревью 14.10.2026).
        self.ROWS = [(520, 'Своев Сава', 8, 4, 2), (524, '', 3, 2, 1), (525, '   ', 1, 1, 0)]
        res, _ = self._rating(datetime(2026, 10, 14, 21, 30, tzinfo=self.TZ))
        self.assertEqual([row["name"] for row in res["operators"]], ["Своев Сава", "Сотрудник", "Сотрудник"])
        self.assertNotIn('#', repr(res))
        self.assertNotIn('524', repr(res))
        self.assertNotIn('525', repr(res))

    def test_operator_without_calls_sees_no_amounts_at_all(self):
        res, _ = self._rating(datetime(2026, 10, 14, 23, 0, tzinfo=self.TZ), user=523)
        self.assertIsNone(res["me"])
        self.assertEqual(res["total"], 3)
        self.assertFalse(any(row["me"] for row in res["operators"]))
        self.assertFalse(any("amount" in row or "price" in row for row in res["operators"]))

    def test_yesterday_stays_open_until_ten_am(self):
        # 04:59:59 UTC — 09:59:59 по Алматы: ещё итоги вчерашнего дня.
        res, cur = self._rating(datetime(2026, 10, 15, 4, 59, 59, tzinfo=timezone.utc))
        self.assertEqual((res["visible"], res["day"], res["day_label"], res["seconds_to_close"]),
                         (True, "2026-10-14", "14 октября", 1))
        self.assertEqual(cur.executed[0][1]["day"], date(2026, 10, 14))
        # В 10:00 рейтинг закрыт, отсчёт — до итогов нового дня.
        res, cur = self._rating(datetime(2026, 10, 15, 10, 0, tzinfo=self.TZ))
        self.assertEqual((res["visible"], res["day"], res["seconds_to_open"]), (False, "2026-10-15", 11 * 3600))
        self.assertEqual(cur.executed[0][1]["day"], date(2026, 10, 15))


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
        svc.get_state = lambda uid, phone_version=None: {"portion": "новая"}
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
                                             active=('att-0', 'asg-0', 'leg_answered', 'gc', None, 'Петров', 'lead-0'))
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


class CallErrorKindTests(unittest.TestCase):
    """Почему не удался звонок (13.10.2026). В 19:04 сервер 40 с не мог соединиться с Binotel: оператор
    ждал 41 с, получил «обратитесь к руководителю», строка — «Ошибка АТС», а попытка ушла водителю в
    счёт. Звонок, который не начинался не по вине водителя (линия оператора, связь с Binotel, код 105,
    лимит), водителю больше не в счёт, а оператор видит, что делать."""

    # Тексты api_error из базы за три недели (номера уже скрыты).
    CASES = (
        ("Binotel calls/internal-number-to-external-number status='error' code=150: Can't call to the ext", 'line'),
        ("HTTPSConnectionPool(host='api.binotel.com', port=443): Read timed out. (read timeout=40)", 'network'),
        ("HTTPSConnectionPool(host='api.binotel.com', port=443): Max retries exceeded with url: "
         "/api/4.0/calls/internal-number-to-external-number.json (Caused by ConnectTimeoutError("
         "<HTTPSConnection(host='api.binotel.com', port=443) at 0x…a35d0>, 'Connection to api.binotel.com timed out. "
         "(connect timeout=40)'))", 'network'),
        ("Binotel calls/internal-number-to-external-number status='error' code=105: Something went wrong", 'pbx_busy'),
        ("Binotel calls/internal-number-to-external-number status='error' code=106: Requests are too frequent", 'rate'),
        ("Binotel calls/internal-number-to-external-number status='error' code=104: Нет доступа", 'pbx'),
        ("Binotel HTTP 502: Bad Gateway", 'pbx'),
        ("подключение к Binotel не настроено", 'pbx'),
        ("", 'pbx'),
    )

    def test_kind_from_stored_text(self):
        for text, kind in self.CASES:
            self.assertEqual(dial_service.call_error_kind(None, text), kind, text)
        self.assertEqual(set(dial_service.CALL_ERRORS_NOT_COUNTED), {'line', 'network', 'pbx_busy', 'rate'})

    def test_kind_from_exception(self):
        import requests
        import urllib3
        from binotel import client as binotel_client
        refused = requests.exceptions.ConnectionError(
            urllib3.exceptions.MaxRetryError(None, '/x', urllib3.exceptions.NewConnectionError(None, 'refused')))
        aborted = requests.exceptions.ConnectionError(
            urllib3.exceptions.ProtocolError('Connection aborted.', ConnectionResetError()))
        for exc, kind in ((requests.exceptions.ConnectTimeout('connect'), 'network'),
                          (requests.exceptions.ReadTimeout('read'), 'network'),
                          (refused, 'network'),
                          (binotel_client.RateLimited('too frequent', retry_after=3), 'rate'),
                          (RuntimeError("Binotel x status='error' code=105: Something went wrong"), 'pbx_busy'),
                          (RuntimeError("Binotel x status='error' code=150: Can't call to the ext"), 'line'),
                          (RuntimeError("Binotel x status='error' code=104: y"), 'pbx')):
            self.assertEqual(dial_service.call_error_kind(exc), kind, repr(exc))
        # Обрыв уже открытого соединения — не «не соединились»: запрос мог уйти (повторять нельзя).
        self.assertFalse(binotel_client.connect_failed(aborted))
        self.assertTrue(binotel_client.connect_failed(refused))

    def _start(self, error):
        helper = SignedDriversNeverIssuedTests('test_due_rule')
        svc, cur, db, checker, client = helper._call(configured=False)

        def fail(internal_number, external_number, **extra):
            client.calls.append((internal_number, external_number))
            raise error

        client.originate_internal_to_external = fail
        svc._line_status_hint = lambda dep, num: 'Обычно линия возвращается в сеть со следующей регистрацией.'
        with self.assertLogs('dial_list.service', level='WARNING'), \
                self.assertRaises(dial_service.DialListError) as ctx:
            svc.start_call(7, 'asg-1')
        return ctx.exception, cur

    def test_start_call_kinds_texts_and_counting(self):
        import requests
        from binotel import client as binotel_client
        cases = (
            (requests.exceptions.ConnectTimeout('connect timeout=5'), 'network', 502, 'Нет связи с АТС', False),
            (requests.exceptions.ReadTimeout('read timeout=40'), 'network', 502, 'АТС не ответила за 40 секунд',
             False),
            (RuntimeError("Binotel x status='error' code=105: Something went wrong"), 'pbx_busy', 502,
             'попробуйте ещё раз через несколько секунд', False),
            (binotel_client.RateLimited("Binotel x status='error' code=106: too frequent", retry_after=3), 'rate',
             429, 'слишком частые запросы', False),
            (RuntimeError("Binotel x status='error' code=150: Can't call to the ext"), 'line', 409, 'не на связи',
             False),
            (RuntimeError("Binotel x status='error' code=104: Нет доступа"), 'pbx', 502, 'обратитесь к руководителю',
             True),
        )
        for error, kind, status, text, counted in cases:
            exc, cur = self._start(error)
            self.assertEqual((exc.kind, exc.status), (kind, status), repr(error))
            self.assertIn(text, str(exc), repr(error))
            self.assertNotIn('77010000001', str(exc))
            # Не в счёт водителю — попытку у строки откатили; в счёт — нет.
            rolled = cur.sql_with('SET attempts = GREATEST(attempts - 1, 0)')
            self.assertEqual(len(rolled), 0 if counted else 1, repr(error))
            failed = cur.sql_with("SET state = 'failed', api_error = %s")
            self.assertEqual(len(failed), 1, repr(error))
            self.assertNotIn('77010000001', failed[0][1][0])

    def test_route_returns_error_kind(self):
        class Svc:
            def start_call(self, user_id, assignment_id):
                raise dial_service.DialListError('Нет связи с АТС — попробуйте позвонить ещё раз через полминуты',
                                                 502, kind='network')

        resp = _phone_client(Svc()).post('/api/operator/dial_list/items/asg-1/call', json={})
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.get_json(), {"error": 'Нет связи с АТС — попробуйте позвонить ещё раз через полминуты',
                                           "error_kind": 'network'})
        # Ошибка без вида — как раньше, без ключа.
        class Plain:
            def start_call(self, user_id, assignment_id):
                raise dial_service.DialListError('Строка списка не найдена', 404)

        resp = _phone_client(Plain()).post('/api/operator/dial_list/items/asg-1/call', json={})
        self.assertEqual(resp.get_json(), {"error": 'Строка списка не найдена'})

    def test_portion_row_tells_the_kind_only_for_a_failed_last_attempt(self):
        base = ('asg-1', 1, 'Иванов', 'issued', '', 0, None, 'att-1', 'failed', '', None, None, None, None,
                False, None, 'lead-1', None, '', None, None, None)
        network = base + ("HTTPSConnectionPool(host='api.binotel.com', port=443): Read timed out. (read timeout=40)",)
        busy = base + ("Binotel x status='error' code=105: Something went wrong",)
        done = base[:8] + ('finished',) + base[9:] + ('',)
        cur = _RoutedCursor([('WHERE a.portion_id = %s', {'all': [network, busy, done]})])
        items = dial_service.DialListService(db=None)._portion_items(cur, 'portion-1')
        self.assertEqual([i.get("last_error_kind") for i in items], ['network', 'pbx_busy', None])
        # Сам текст отказа телефону не уходит.
        self.assertNotIn('HTTPSConnectionPool', json.dumps(items, ensure_ascii=False, default=str))


class RecallTests(unittest.TestCase):
    """«Позвонить ещё раз» (владелец, 12.10.2026; с 11.10.2026 — «Перезвонить» раньше срока,
    только по итогу «Перезвонить»): своему водителю — последним ему звонил этот оператор, с
    любым итогом или без, — одной транзакцией и без сети; строки вкладок итогов решаются тем
    же правилом (recall_verdict) — кнопка не врёт."""

    LEAD = '66666666-6666-6666-6666-666666666666'
    # подписал, исключён, открытая строка (id, чья), кто звонил последним (не отменённая попытка),
    # «перезвонить» ли её итог, звонил ли после неё другой
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
        svc.get_state = lambda uid, phone_version=None: {"portion": {"id": "portion-1"}}
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
        # С 12.10.2026 итог последней попытки не важен: «Отказ», «Заинтересован», недозвон —
        # водитель всё равно свой.
        self.assertEqual(verdict(own[:4] + (7, False, False), 7), 'ok')
        self.assertEqual(verdict((True,) + own[1:], 7), 'signed')                 # подписал или ждёт Яндекс
        self.assertEqual(verdict((False, True) + own[2:], 7), 'excluded')
        self.assertEqual(verdict((True, True) + own[2:], 7), 'signed')            # подпись — первой
        self.assertEqual(verdict(own[:2] + ('a', 7) + (None, False, False), 7), 'issued_mine')   # без проверки итога
        self.assertEqual(verdict(own[:2] + ('a', 8) + own[4:], 7), 'issued_other')
        self.assertEqual(verdict(own[:4] + (None, False, False), 7), 'not_own')    # (засчитанных) звонков не было
        self.assertEqual(verdict(own[:4] + (8, False, False), 7), 'taken')         # последним звонил другой
        self.assertEqual(verdict(own[:6] + (True,), 7), 'taken')                   # после меня звонил другой
        self.assertEqual(set(dial_service.RECALL_REFUSALS), {'signed', 'excluded', 'issued_other', 'not_own', 'taken'})
        self.assertEqual(dial_service.RECALL_REFUSALS['not_own'],
                         "Позвонить ещё раз можно только водителю, которому последним звонили вы")

    def test_lead_must_be_in_operator_department(self):
        svc, cur, _ = self._svc()
        self.assertEqual(self._refused(svc, 'не-uuid'), ("Водитель не найден", 404))
        self.assertEqual(cur.executed, [])                                         # до базы не дошло
        svc, cur, _ = self._svc(found=False)
        self.assertEqual(self._refused(svc, self.LEAD.upper()), ("Водитель не найден", 404))
        params = cur.sql_with('FOR UPDATE OF l')[0][1]
        self.assertEqual(params, {"lead_id": self.LEAD, "department_id": 1954, "operator_id": 7})

    def test_signed_and_excluded_refused_before_other_gates(self):
        busy = ('att-0', 'asg-0', 'leg_answered', 'gc', None, 'Петров', 'lead-0')
        for cols, text in (((True,) + self.OWN[1:], "Водитель уже подписал документы — звонить не нужно"),
                           ((False, True) + self.OWN[2:], "Руководитель исключил водителя из обзвона")):
            svc, cur, _ = self._svc(cols=cols, active=busy, pending_outcome=('att-1', 'Петров', None, None))
            self.assertEqual(self._refused(svc), (text, 409))
            self.assertEqual(self._writes(cur), [])

    def test_call_and_outcome_gates(self):
        svc, cur, _ = self._svc(active=('att-0', 'asg-0', 'leg_answered', 'gc', None, 'Петров', 'lead-0'))
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
             "Позвонить ещё раз можно только водителю, которому последним звонили вы"),
            (self.OWN[:4] + (8, True, False), "Этим водителем уже занимается другой оператор"),
            (self.OWN[:4] + (8, False, False), "Этим водителем уже занимается другой оператор"),
            (self.OWN[:6] + (True,), "Этим водителем уже занимается другой оператор"),
        )
        for cols, text in cases:
            svc, cur, _ = self._svc(cols=cols)
            self.assertEqual(self._refused(svc), (text, 409), cols)
            self.assertEqual(self._writes(cur), [])

    def test_any_own_driver_can_be_called_again(self):
        # Владелец, 12.10.2026: «ещё раз позвонить любому из водителей». Последней была моя
        # попытка с итогом без «перезвонить» («Отказ») или вовсе без итога (недозвон) —
        # водитель снова у меня в списке; лид при этом не трогаем.
        for cols in (self.OWN[:4] + (7, False, False), self.OWN):
            svc, cur, _ = self._svc(cols=cols, last_done=('asg-old', 'portion-1'))
            with self.assertLogs('dial_list.service', level='INFO') as logs:
                self.assertEqual(svc.recall_lead(7, self.LEAD)["assignment_id"], 'asg-old')
            self.assertEqual(cur.sql_with("SET state = 'issued', result = '', done_at = NULL")[0][1], ('asg-old',))
            self.assertEqual(cur.sql_with('UPDATE dial_list_leads'), [])
            # В журнале сервера видно, звонит ли он по своему «Перезвонить» или просто ещё раз.
            said = 'по итогу «Перезвонить» раньше срока' if cols[5] else 'ещё раз'
            self.assertTrue(any(said in line for line in logs.output), logs.output)

    def test_reopens_my_done_row_in_latest_portion(self):
        svc, cur, _ = self._svc(last_done=('asg-old', 'portion-1'))
        self.assertEqual(svc.recall_lead(7, self.LEAD)["assignment_id"], 'asg-old')
        self.assertEqual(cur.sql_with("SET state = 'issued', result = '', done_at = NULL")[0][1], ('asg-old',))
        self.assertEqual(cur.sql_with('SET closed_at = NULL WHERE id')[0][1], ('portion-1',))
        self.assertEqual(cur.sql_with('INSERT'), [])
        self.assertEqual(cur.sql_with("state = 'done'")[0][1], (self.LEAD, 7))     # строка именно этого оператора

    def test_reopened_row_counts_its_attempts_from_zero(self):
        # Владелец, 12.10.2026: переоткрытая строка с двумя попытками из трёх закрывалась
        # неудачей после первого же отказа АТС (_fail_attempt сравнивает счётчик строки с
        # пределом). Теперь счётчик переоткрытой строки — с нуля, как у новой.
        svc, cur, db = self._svc(last_done=('asg-old', 'portion-1'))
        svc.recall_lead(7, self.LEAD)
        reset = cur.sql_with('SET attempts = 0')
        self.assertEqual([(sql.strip(), params) for sql, params in reset],
                         [('UPDATE dial_list_assignments SET attempts = 0 WHERE id = %s', ('asg-old',))])
        sqls = [sql for sql, _ in cur.executed]
        reopened_at = next(i for i, s in enumerate(sqls) if "SET state = 'issued', result = '', done_at = NULL" in s)
        self.assertLess(reopened_at, sqls.index(reset[0][0]))                       # сначала переоткрыли
        self.assertEqual(len(db.transactions), 1)                                  # в той же транзакции
        # Счётчик попыток ЛИДА не трогаем: аналитика и «Мой прогресс» считают сами попытки.
        self.assertEqual(cur.sql_with('UPDATE dial_list_leads'), [])
        # Новая строка и так создаётся с нулём; строка, что уже в моём списке, — как есть:
        # её попытки ещё идут в счёт предела (отказ АТС за отказом её и закроет).
        for kwargs in ({"last_done": ('asg-old', 'portion-0')}, {"last_done": None}, {"last_portion": None},
                       {"cols": (False, False, 'asg-open', 7, None, False, False)}):
            svc, cur, _ = self._svc(**kwargs)
            svc.recall_lead(7, self.LEAD)
            self.assertEqual(cur.sql_with('SET attempts = 0'), [], kwargs)
        # «Вернуть в список» руководителя счётчик не обнуляет: переоткрытие у них общее
        # (_reopen_row), обнуление — только в «Позвонить ещё раз».
        svc_cls = dial_service.DialListService
        self.assertNotIn('attempts', inspect.getsource(svc_cls._reopen_row))
        self.assertNotIn('attempts = 0', inspect.getsource(svc_cls._reopen_in_latest_portion))
        self.assertIn('attempts = 0', inspect.getsource(svc_cls._recall_row))
        # Откуда берётся отказ на «двух из трёх»: закрытие по пределу — по счётчику строки.
        self.assertIn('attempts_now >= int(max_attempts)', inspect.getsource(svc_cls._fail_attempt))

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
        # Чей водитель — по последней попытке любого рода (12.10.2026), кроме отменённых
        # оператором до ответа: те не в счёт ни строке, ни лиду. До того — только с итогом.
        for fragment in ('WHERE ra.lead_id = l.id AND NOT rt.cancelled\n', 'ORDER BY rt.requested_at DESC LIMIT 1',
                         'rt2.operator_id <> %(operator_id)s', 'rt2.requested_at > rt.requested_at',
                         "rx.state = 'issued'", 'COALESCE(ro.requeue, FALSE)'):
            self.assertIn(fragment, joins)
        self.assertNotIn('rt.outcome_id IS NOT NULL', joins)
        self.assertEqual(len([c for c in svc._RECALL_COLUMNS.split(',') if c.strip()]), 7)

    def test_route_is_in_operator_zone(self):
        import flask
        seen = []

        class Svc:
            def recall_lead(self, user_id, lead_id, phone_version=None):
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


class LeadStaysDoneTests(unittest.TestCase):
    """Обработанный водитель недозвоном обратно не открывается (12.10.2026). Ему теперь звонят
    «Позвонить ещё раз», а прежнее правило _touch_lead после первого же недозвона делало
    'done' (дозвонились — «Отказ», или попытки кончились) снова 'in_progress': через
    retry_after_hours общий пул выдал бы его другому оператору. Итог «перезвонить» и
    «Вернуть в список» руководителя работают, как раньше."""

    DEP = 1954

    def _touch(self, status, attempts=1, answered=False, requeue=False, max_attempts=3, settings=True):
        """(попыток у лида после, статус после) — по UPDATE, который пишет _touch_lead."""
        cur = _RoutedCursor([
            ('FROM dial_list_leads WHERE id = %s FOR UPDATE', {'one': (self.DEP, attempts, status)}),
            ('SELECT max_attempts FROM dial_list_department_settings',
             {'one': (max_attempts,) if settings else None}),
        ])
        dial_service.DialListService(db=None)._touch_lead(cur, 'lead-1', answered=answered, count_attempt=True,
                                                          requeue=requeue)
        (sql, params), = cur.sql_with('UPDATE dial_list_leads')
        # Попытка в счёте и время последней попытки сдвигается всегда; исключённый — как был.
        self.assertIn('last_attempt_at = CURRENT_TIMESTAMP', sql)
        self.assertIn("status = CASE WHEN status = 'excluded' THEN status ELSE %s END", sql)
        return params[0], params[2]

    def test_done_lead_stays_done_after_no_answer(self):
        # «Отказ» — дозвонились, водитель обработан; «Позвонить ещё раз» — не взял трубку.
        self.assertEqual(self._touch('done', attempts=1), (2, 'done'))
        # Попытки кончились раньше («Не дозвонились») — ещё один недозвон ничего не меняет.
        self.assertEqual(self._touch('done', attempts=3), (4, 'done'))
        # Предел подняли уже после исчерпания: в пул его возвращает руководитель, не звонок.
        self.assertEqual(self._touch('done', attempts=3, max_attempts=5), (4, 'done'))

    def test_requeue_and_answer_work_as_before(self):
        self.assertEqual(self._touch('done', attempts=2, requeue=True), (0, 'in_progress'))   # итог «перезвонить»
        self.assertEqual(self._touch('done', attempts=1, answered=True), (2, 'done'))

    def test_open_leads_keep_the_old_rule(self):
        self.assertEqual(self._touch('in_progress', attempts=1), (2, 'in_progress'))
        self.assertEqual(self._touch('in_progress', attempts=2), (3, 'done'))                 # попытки кончились
        # «Вернуть в список» руководителя ставит 'new' (и обнуляет попытки) ещё до звонка —
        # поэтому возвращённый недозвоном снова в работе, как раньше.
        self.assertEqual(self._touch('new', attempts=0), (1, 'in_progress'))
        self.assertEqual(self._touch('new', attempts=0, settings=False), (1, 'in_progress'))
        requeue = inspect.getsource(dial_service.DialListService.requeue_lead)
        self.assertIn("SET status = 'new', attempts_total = 0, last_attempt_at = NULL", requeue)
        # Итог «перезвонить» открывает лид сразу, ещё до исхода от АТС.
        outcome = inspect.getsource(dial_service.DialListService.set_attempt_outcome)
        self.assertIn("SET status = CASE WHEN status = 'excluded' THEN status ELSE 'in_progress' END", outcome)

    def test_binotel_refusal_on_call_again_keeps_done(self):
        # Попытки строки кончились на отказах АТС (_fail_attempt → _close_assignment) — тот же путь.
        cur = _RoutedCursor([
            ("SET state = 'done', result = %s", {'one': ('portion-1',)}),
            ('FROM dial_list_leads WHERE id = %s FOR UPDATE', {'one': (self.DEP, 1, 'done')}),
            ('SELECT max_attempts FROM dial_list_department_settings', {'one': (3,)}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        svc._fail_attempt('att-1', 'asg-1', 'lead-1', 'АТС отказала', attempts_now=3, max_attempts=3)
        (_, params), = cur.sql_with('UPDATE dial_list_leads')
        self.assertEqual((params[0], params[2]), (2, 'done'))

    def test_late_cancel_reopens_only_what_this_attempt_closed(self):
        # Отбой оператора дошёл после исхода от Binotel — попытку откатывают. Лид снова в работе,
        # только если обработала его эта попытка (без неё попыток меньше предела); дозвонились
        # раньше или попытки кончились ещё до неё — обработанным и остаётся.
        cur = _RoutedCursor([
            ('SET cancelled = TRUE', {'one': ('att-1',)}),
            ('SELECT a.state, a.lead_id, a.portion_id', {'one': ('done', 'lead-1', 'portion-1', 'att-1')}),
            ("WHERE lead_id = %s AND state = 'issued'", {'one': None}),
        ])
        self.assertTrue(dial_service.DialListService(db=None)._cancel_attempt(cur, 'att-1', 'asg-1', late=True))
        (sql, params), = cur.sql_with('UPDATE dial_list_leads')
        self.assertIn('SET attempts_total = GREATEST(attempts_total - 1, 0)', sql)
        self.assertIn("CASE WHEN status = 'done' AND answered_at IS NULL", sql)
        # 13.10.2026: только если ЭТА попытка довела счётчик ровно до предела. Обработанный раньше
        # (попыток больше предела или меньше — предел подняли уже после исчерпания: done, 4 из 5)
        # обработанным и остаётся. Настоящим SQL — test_dial_list_dispatch_postgres.
        self.assertIn('AND attempts_total = COALESCE(', sql)
        self.assertNotIn('attempts_total <= COALESCE(', sql)
        self.assertIn('FROM dial_list_department_settings s', sql)
        self.assertIn('WHERE s.department_id = l.department_id), %s)', sql)
        self.assertEqual(params, (dial_service.DEFAULT_MAX_ATTEMPTS, 'lead-1'))


def _fresh_sign_limits(test):
    """Пределы «Проверить на подписание» — на классе сервиса (один процесс на всё приложение):
    тесту — свои пустые отметки и свои четыре места для одновременных проверок."""
    svc = dial_service.DialListService
    for marks in (svc._sign_asked, svc._sign_operator_asks):
        patcher = unittest.mock.patch.dict(marks, clear=True)
        patcher.start()
        test.addCleanup(patcher.stop)
    patcher = unittest.mock.patch.object(svc, '_sign_now_slots',
                                         threading.BoundedSemaphore(dial_service.SIGN_OPERATOR_CONCURRENT))
    patcher.start()
    test.addCleanup(patcher.stop)


class OperatorSignCheckTests(unittest.TestCase):
    """«Проверить на подписание» с телефона (владелец, 12.10.2026): и во время звонка, без
    передачи данных на телефон — только статус. Sapar — вне транзакций, свежий ответ — из
    базы, об одном водителе — не чаще раза в 20 с; подписавшего — со списка оператора, кроме
    строки с идущим звонком."""

    LEAD = '77777777-7777-7777-7777-777777777777'
    OTHER = '88888888-8888-8888-8888-888888888888'
    IIN = '900101300007'
    PERIOD = date(2026, 10, 1)
    NOW = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)      # 14:00 по Алматы

    def setUp(self):
        # Отметки «Sapar о водителе спрашивали» и «оператор спрашивал» и места для одновременных
        # проверок живут на классе сервиса (один процесс на всё приложение) — у каждого теста свои.
        _fresh_sign_limits(self)

    @staticmethod
    def _age_operator_asks(seconds, operator_id=7):
        """Сдвинуть отметки «оператор спрашивал Sapar» в прошлое — как будто прошло seconds."""
        asks = dial_service.DialListService._sign_operator_asks
        asks[operator_id] = [at - seconds for at in asks.get(operator_id, [])]

    @staticmethod
    def _forget_asks():
        dial_service.DialListService._sign_asked.clear()
        dial_service.DialListService._sign_operator_asks.clear()

    def _ctx(self, uid):
        return {"user_id": uid, "department_id": 1954, "name": "Оператор", "internal_number": "913",
                "settings": {"portion_size": 20, "period": "2026-10-01", "period_label": "Октябрь 2026",
                             "_period": self.PERIOD, "retry_after_hours": 24, "max_attempts": 3,
                             "caller_id_for_employee": ""}}

    def _lead(self, status='unsigned', checked_ago=None, error='', signed=False, iin=None, lead_id=None):
        """Строка _OPERATOR_SIGN_LEAD_SQL: id, ИИН, база, статус, когда проверяли, ошибка, подписал."""
        checked = None if checked_ago is None else self.NOW - checked_ago
        return (lead_id or self.LEAD, self.IIN if iin is None else iin, self.PERIOD, status, checked, error,
                self.NOW - timedelta(hours=1) if signed else None)

    def _svc(self, reads, configured=True, answers=(), open_row=('asg-1', 7), live_call=False):
        """reads — строки лида по очереди (до Sapar, после него); open_row — открытая строка
        водителя (id, чья; None — её нет); live_call — по ней сейчас идёт звонок."""
        queue = list(reads)
        cur = _RoutedCursor([
            ('FROM dial_list_leads l', {'one': lambda: queue.pop(0) if queue else None}),
            ("a.state = 'issued'", {'one': open_row}),
            ('t.state IN %s', {'one': (1,) if live_call else None}),
            ("SET state = 'done', result = 'other'", {'one': ('portion-1',)}),
        ])
        db = _TxDb(cur)
        checker = _FakeSignChecker(db, configured=configured, answers=answers)
        svc = dial_service.DialListService(db=db, sign_checker=checker)
        svc.operator_context = self._ctx
        return svc, cur, db, checker

    def _check(self, svc, lead_id=LEAD):
        with unittest.mock.patch.object(dial_service, '_utcnow', return_value=self.NOW):
            return svc.operator_sign_check(7, lead_id)

    def _client(self, svc):
        import flask
        requester = (7, 'Оператор', 'x', 'operator', None, None, None, 'op')
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (7, requester, None), is_admin_role=lambda role: False,
            headed_department_ids=lambda rid: [], service=svc)
        app = flask.Flask('dial_list_sign_check_test')
        app.register_blueprint(bp)
        return app.test_client()

    # ── когда Sapar не нужен ─────────────────────────────────────────────────
    def test_fresh_processing_is_answered_from_db(self):
        # «На проверке у Яндекса» — уже подписал: по свежей проверке ответ из базы, и он верный.
        svc, cur, db, checker = self._svc([self._lead('processing', checked_ago=timedelta(seconds=30))],
                                          open_row=None)
        self.assertEqual(self._check(svc), {
            "lead_id": self.LEAD,
            "sign": {"status": "processing", "signed": True, "label": "Подписал · на проверке у Яндекса",
                     "checked_at": "2026-10-12T08:59:30+00:00", "checked_label": "сегодня, 13:59", "fresh": True},
            "row_closed": False, "text": ""})
        self.assertEqual(checker.calls, [])
        self.assertEqual(_writes(db.transactions[0]), [])
        self.assertEqual(dial_service.SIGN_OPERATOR_FRESH_SEC, 60)

    def test_not_signed_from_db_is_never_fresh(self):
        # 13.10.2026: «не подписал» из базы свежим не бывает — водитель мог подписать минуту назад.
        # Даже по проверке полуминутной давности спрашиваем Sapar (в пределах _sign_ask_slot).
        svc, cur, db, checker = self._svc([self._lead('unsigned', checked_ago=timedelta(seconds=30)),
                                           self._lead('unsigned', checked_ago=timedelta(0))],
                                          answers=[{"checked": 1}])
        res = self._check(svc)
        self.assertEqual(len(checker.calls), 1)
        self.assertEqual((res['sign']['label'], res['sign']['fresh'], res['text']), ('Не подписал', True, ''))
        # Предел исчерпан (только что спрашивали) — последнее известное и честно: не свежее.
        svc, cur, db, checker = self._svc([self._lead('unsigned', checked_ago=timedelta(seconds=10))])
        res = self._check(svc)
        self.assertEqual(checker.calls, [])
        self.assertEqual((res['sign']['label'], res['sign']['fresh'], res['text']),
                         ('Не подписал', False, dial_service.SIGN_OPERATOR_THROTTLED))
        # «Не удалось проверить» (статус неизвестен) — тоже не из базы.
        self._forget_asks()
        svc, cur, db, checker = self._svc([self._lead('', checked_ago=timedelta(seconds=10)),
                                           self._lead('unsigned', checked_ago=timedelta(0))],
                                          answers=[{"checked": 1}])
        self.assertEqual(self._check(svc)['sign']['fresh'], True)
        self.assertEqual(len(checker.calls), 1)

    def test_signed_driver_needs_no_sapar_and_leaves_my_list(self):
        svc, cur, db, checker = self._svc([self._lead('signed', checked_ago=timedelta(hours=1), signed=True)])
        res = self._check(svc)
        self.assertEqual(checker.calls, [])
        # Подпись окончательна: статус верный и без нового вопроса в Sapar.
        self.assertEqual((res['sign']['status'], res['sign']['signed'], res['sign']['label'], res['sign']['fresh']),
                         ('signed', True, 'Подписал', True))
        self.assertEqual((res['row_closed'], res['text']), (True, dial_service.SIGNED_ROW_CLOSED))
        self.assertEqual([p for _, p in cur.sql_with("SET state = 'done', result = 'other'")], [('asg-1',)])

    def test_no_iin_means_unknown_without_sapar(self):
        svc, cur, db, checker = self._svc([self._lead('', iin='')])
        res = self._check(svc)
        self.assertEqual(res['sign'], {"status": "unknown", "signed": None, "label": "Не удалось проверить",
                                       "checked_at": None, "checked_label": "", "fresh": False})
        self.assertEqual((res['text'], res['row_closed']), ("Нет ИИН — подписание не проверить", False))
        self.assertEqual(checker.calls, [])

    def test_not_configured_shows_last_known(self):
        svc, cur, db, checker = self._svc([self._lead('expired', checked_ago=timedelta(hours=20))], configured=False)
        res = self._check(svc)
        self.assertEqual(res['sign'], {"status": "not_signed", "signed": False,
                                       "label": "Не подписал · срок подписания истёк",
                                       "checked_at": "2026-10-11T13:00:00+00:00", "checked_label": "вчера, 18:00",
                                       "fresh": False})
        self.assertEqual(res['text'], "Проверка подписания не настроена на сервере")
        self.assertEqual(checker.calls, [])

    def test_time_is_shown_only_for_a_clean_check(self):
        # Отказ Sapar по ИИН тоже сдвигает время проверки: со статусом прошлой проверки оно
        # было бы чужим — время не показываем.
        svc, cur, db, checker = self._svc([self._lead('unsigned', checked_ago=timedelta(minutes=10),
                                                      error='Code 500')], configured=False)
        sign = self._check(svc)['sign']
        self.assertEqual((sign['label'], sign['checked_at'], sign['checked_label']), ('Не подписал', None, ''))

    # ── Sapar сейчас ─────────────────────────────────────────────────────────
    def test_stale_check_asks_sapar_outside_transactions_and_closes_my_row(self):
        stale = self._lead('unsigned', checked_ago=timedelta(hours=2))
        after = self._lead('processing', checked_ago=timedelta(0))
        svc, cur, db, checker = self._svc([stale, after], answers=[{"checked": 1, "processing": [self.LEAD]}])
        with self.assertLogs('dial_list.service', level='INFO') as logs:
            res = self._check(svc)
        (call,) = checker.calls
        self.assertEqual(call['leads'], [(self.LEAD, self.IIN, self.PERIOD)])
        self.assertEqual((call['timeout'], call['workers'], call['budget_sec'], call['max_systemic'], call['now']),
                         (5, 1, 6, 1, self.NOW))
        self.assertIs(call['in_tx'], False)                                        # Sapar — вне транзакций
        self.assertEqual(res['sign'], {"status": "processing", "signed": True,
                                       "label": "Подписал · на проверке у Яндекса",
                                       "checked_at": "2026-10-12T09:00:00+00:00", "checked_label": "сегодня, 14:00",
                                       "fresh": True})
        # «На проверке у Яндекса» — уже подписал: строка у меня снята со списка.
        self.assertEqual((res['row_closed'], res['text']), (True, dial_service.SIGNED_ROW_CLOSED))
        # Чтение до Sapar, чтение после — и своя транзакция на снятие строки: найти открытую строку,
        # замок её владельца, запереть строку, ОТДЕЛЬНЫМ запросом — попытки «в полёте», закрыть.
        read, reread, close = db.transactions
        self.assertEqual((_writes(read), _writes(reread)), ([], []))
        self.assertIn("WHERE a.lead_id = %s AND a.state = 'issued'", close[0])
        self.assertIn('pg_advisory_xact_lock(hashtext(%s))', close[1])
        self.assertIn('FOR UPDATE OF a', close[2])
        self.assertIn('t.state IN %s', close[3])
        self.assertNotIn('dial_list_attempts', close[2])                          # не в запросе с замком
        self.assertEqual(cur.sql_with('pg_advisory_xact_lock')[0][1], ('dial_list_operator:7',))
        self.assertEqual([p for _, p in cur.sql_with("SET state = 'done', result = 'other'")], [('asg-1',)])
        self.assertEqual(len(cur.sql_with('UPDATE dial_list_portions SET closed_at = CURRENT_TIMESTAMP')), 1)
        self.assertEqual(db.rolled_back, [False, False, False])
        # Журнал сервера — без личных данных: ни ИИН, ни ФИО.
        self.assertTrue(any('processing' in line and self.LEAD in line for line in logs.output), logs.output)
        self.assertFalse(any(self.IIN in line for line in logs.output))

    def test_fresh_but_failed_check_is_asked_again(self):
        failed = self._lead('unsigned', checked_ago=timedelta(seconds=10), error='Code 500')
        svc, cur, db, checker = self._svc([failed, self._lead('unsigned', checked_ago=timedelta(0))],
                                          answers=[{"checked": 1}])
        res = self._check(svc)
        self.assertEqual(len(checker.calls), 1)
        self.assertEqual((res['sign']['label'], res['sign']['fresh'], res['row_closed']), ('Не подписал', True, False))

    def test_sapar_down_backoff_budget_or_failure_show_last_known(self):
        for answer in ({"errors": 1, "systemic": 1, "stopped": "sapar_unavailable"}, {"stopped": "backoff"},
                       {"stopped": "budget"}, {"errors": 1}, RuntimeError('Sapar упал')):
            self._forget_asks()
            svc, cur, db, checker = self._svc([self._lead('unsigned', checked_ago=timedelta(hours=3))],
                                              answers=[answer])
            if isinstance(answer, Exception):
                with self.assertLogs('dial_list.service', level='ERROR'):
                    res = self._check(svc)
            else:
                res = self._check(svc)
            self.assertEqual(len(checker.calls), 1, answer)
            # Последнее известное до запроса — и честно: «не свежее».
            self.assertEqual(res['sign'], {"status": "not_signed", "signed": False, "label": "Не подписал",
                                           "checked_at": "2026-10-12T06:00:00+00:00",
                                           "checked_label": "сегодня, 11:00", "fresh": False}, answer)
            self.assertEqual(res['text'], "Сапар не ответил — показан последний известный статус")
            self.assertEqual(len(db.transactions), 1)                            # перечитывать нечего

    def test_one_sapar_question_per_driver_per_interval(self):
        stale = self._lead('unsigned', checked_ago=timedelta(hours=3))
        svc, cur, db, checker = self._svc([stale] * 3 + [self._lead('unsigned', checked_ago=timedelta(hours=3),
                                                                    lead_id=self.OTHER)],
                                          answers=[{"stopped": "budget"}] * 3)
        first = self._check(svc)
        second = self._check(svc)
        self.assertEqual(len(checker.calls), 1)
        self.assertEqual(first['text'], "Сапар не ответил — показан последний известный статус")
        self.assertEqual(second['text'], "Только что проверяли — показан последний известный статус")
        self.assertEqual((second['sign']['label'], second['sign']['fresh']), ('Не подписал', False))
        # Интервал прошёл — спрашиваем снова; о другом водителе — сразу (если сам оператор не
        # спрашивает чаще раза в SIGN_OPERATOR_GAP_SEC — это OperatorSignLimitsTests).
        dial_service.DialListService._sign_asked[self.LEAD] -= dial_service.SIGN_OPERATOR_MIN_INTERVAL_SEC + 1
        self._age_operator_asks(dial_service.SIGN_OPERATOR_GAP_SEC + 1)
        self._check(svc)
        self._age_operator_asks(dial_service.SIGN_OPERATOR_GAP_SEC + 1)
        self._check(svc, self.OTHER)
        self.assertEqual([c['leads'][0][0] for c in checker.calls], [self.LEAD, self.LEAD, self.OTHER])
        self.assertEqual(dial_service.SIGN_OPERATOR_MIN_INTERVAL_SEC, 20)

    # ── строка оператора ─────────────────────────────────────────────────────
    def test_live_call_row_is_not_closed(self):
        svc, cur, db, checker = self._svc([self._lead('signed', checked_ago=timedelta(minutes=5), signed=True)],
                                          live_call=True)
        res = self._check(svc)
        self.assertEqual((res['sign']['status'], res['row_closed'], res['text']), ('signed', False, ''))
        (find, find_params), (lock, lock_params) = cur.sql_with("a.state = 'issued'")
        self.assertEqual(find_params, (self.LEAD,))                                  # чья бы ни была
        self.assertIn('FOR UPDATE OF a', lock)
        self.assertEqual(lock_params, ('asg-1',))
        (flight, flight_params), = cur.sql_with('t.state IN %s')
        self.assertIn('FROM dial_list_attempts t', flight)
        self.assertEqual(flight_params, ('asg-1', dial_service.IN_FLIGHT_STATES))
        self.assertEqual(set(dial_service.IN_FLIGHT_STATES), {'requested', 'leg_ringing', 'leg_answered', 'ended'})
        self.assertEqual(cur.sql_with("SET state = 'done'"), [])
        # Открытой строки нет — снимать нечего.
        svc, cur, db, checker = self._svc([self._lead('signed', checked_ago=timedelta(minutes=5), signed=True)],
                                          open_row=None)
        self.assertIs(self._check(svc)['row_closed'], False)
        self.assertEqual(cur.sql_with('pg_advisory_xact_lock'), [])
        # Не подписал — к строкам оператора не прикасаемся вовсе.
        svc, cur, db, checker = self._svc([self._lead('unsigned', checked_ago=timedelta(seconds=10))])
        self.assertIs(self._check(svc)['row_closed'], False)
        self.assertEqual(cur.sql_with('pg_advisory_xact_lock'), [])

    def test_signed_closes_the_open_row_of_whoever_owns_it(self):
        # 13.10.2026: подпись снимает открытую строку водителя, у кого бы она ни была — под замком
        # её владельца; row_closed и текст «строка убрана» — только когда это строка спросившего.
        svc, cur, db, checker = self._svc([self._lead('signed', checked_ago=timedelta(minutes=5), signed=True)],
                                          open_row=('asg-9', 8))
        with self.assertLogs('dial_list.service', level='INFO') as logs:
            res = self._check(svc)
        self.assertEqual((res['sign']['status'], res['row_closed'], res['text']), ('signed', False, ''))
        self.assertEqual([p for _, p in cur.sql_with("SET state = 'done', result = 'other'")], [('asg-9',)])
        self.assertEqual(cur.sql_with('pg_advisory_xact_lock')[0][1], ('dial_list_operator:8',))
        self.assertTrue(any('снят со списка оператора 8' in line for line in logs.output), logs.output)

    def test_works_during_a_call_and_before_the_outcome(self):
        # Проверка нужна именно посреди разговора: ни идущий звонок, ни неуказанный итог её
        # не останавливают — в отличие от «Позвонить» и «Позвонить ещё раз».
        src = inspect.getsource(dial_service.DialListService.operator_sign_check)
        for gate in ('_active_attempt(', '_require_outcome_done(', '_pending_outcome('):
            self.assertNotIn(gate, src)
        svc, cur, db, checker = self._svc([self._lead('unsigned', checked_ago=timedelta(seconds=5))])
        cur.rules.insert(0, ('make_interval(mins => %s)',
                             {'one': ('att-1', 'asg-1', 'leg_answered', 'gc', None, 'Иванов', self.LEAD)}))
        self.assertEqual(self._check(svc)['sign']['status'], 'not_signed')
        self.assertEqual(cur.sql_with('make_interval(mins => %s)'), [])

    # ── кто может спросить ───────────────────────────────────────────────────
    def test_unknown_foreign_or_never_mine_lead_is_404(self):
        svc, cur, db, checker = self._svc([])
        for bad in ('не-uuid', '', None, 123):
            with self.assertRaises(dial_service.DialListError) as ctx:
                self._check(svc, bad)
            self.assertEqual((str(ctx.exception), ctx.exception.status), ("Водитель не найден", 404))
        self.assertEqual(cur.executed, [])                                         # до базы не дошло
        # Водитель, которого у этого оператора не было ни разу, — та же 404. Из отдела, где он
        # работал раньше, — можно (13.10.2026): такой водитель остаётся на его вкладках итогов.
        svc, cur, db, checker = self._svc([None])
        with self.assertRaises(dial_service.DialListError) as ctx:
            self._check(svc, self.LEAD.upper())
        self.assertEqual((str(ctx.exception), ctx.exception.status), ("Водитель не найден", 404))
        sql, params = cur.sql_with('FROM dial_list_leads l')[0]
        self.assertEqual(params, (self.LEAD, 7))
        self.assertIn('WHERE l.id = %s\n', sql)
        self.assertNotIn('department_id', sql)
        self.assertIn('EXISTS (SELECT 1 FROM dial_list_assignments a', sql)
        self.assertIn('WHERE a.lead_id = l.id AND a.operator_id = %s)', sql)
        self.assertEqual(checker.calls, [])

    # ── ответ телефону ───────────────────────────────────────────────────────
    def test_status_mapping_for_the_phone(self):
        status = dial_service.operator_sign_status
        self.assertEqual(status('signed', self.NOW), ('signed', True, 'Подписал'))
        self.assertEqual(status('unsigned', self.NOW), ('signed', True, 'Подписал'))     # решает signed_at
        self.assertEqual(status('signed', None), ('signed', True, 'Подписал'))
        self.assertEqual(status('processing', None), ('processing', True, 'Подписал · на проверке у Яндекса'))
        self.assertEqual(status('unsigned', None), ('not_signed', False, 'Не подписал'))
        self.assertEqual(status('not_formed', None), ('not_signed', False, 'Не подписал · документы не сформированы'))
        self.assertEqual(status('expired', None), ('not_signed', False, 'Не подписал · срок подписания истёк'))
        self.assertEqual(status('rejected', None), ('not_signed', False, 'Не подписал · документы отклонены'))
        self.assertEqual(status('no_docs', None), ('not_signed', False, 'Не подписал · документы не поступили'))
        for unknown in ('', None, 'новый_статус'):
            self.assertEqual(status(unknown, None), ('unknown', None, 'Не удалось проверить'))

    def test_checked_label_today_yesterday_and_date(self):
        label, today = dial_service.checked_at_label, date(2026, 10, 12)
        self.assertEqual(label(datetime(2026, 10, 12, 9, 5, tzinfo=timezone.utc), today), 'сегодня, 14:05')
        self.assertEqual(label(datetime(2026, 10, 11, 4, 12, tzinfo=timezone.utc), today), 'вчера, 09:12')
        self.assertEqual(label(datetime(2026, 10, 10, 9, 5, tzinfo=timezone.utc), today), '10.10, 14:05')
        # Сутки — по Алматы: 20:30 UTC 11.10 — это уже 01:30 12.10.
        self.assertEqual(label(datetime(2026, 10, 11, 20, 30, tzinfo=timezone.utc), today), 'сегодня, 01:30')
        self.assertEqual(label(None, today), '')
        # Подписи вкладок итогов — как были: вчерашний день числом.
        self.assertEqual(dial_service.at_label(datetime(2026, 10, 11, 4, 12, tzinfo=timezone.utc), today),
                         '11.10, 09:12')

    def test_response_carries_status_only(self):
        # Весь путь ручки: ни ИИН (и маски), ни номера, ни документов, ни ошибки Sapar.
        before = self._lead('not_formed', checked_ago=timedelta(hours=2))
        after = self._lead('unsigned', checked_ago=timedelta(0))
        svc, cur, db, checker = self._svc([before, after], answers=[{"checked": 1}])
        with unittest.mock.patch.object(dial_service, '_utcnow', return_value=self.NOW):
            resp = self._client(svc).post(f'/api/operator/dial_list/leads/{self.LEAD}/sign_check', json={})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(set(body), {'status', 'lead_id', 'sign', 'row_closed', 'text'})
        self.assertEqual(set(body['sign']), {'status', 'signed', 'label', 'checked_at', 'checked_label', 'fresh'})
        self.assertEqual((body['status'], body['sign']['label']), ('success', 'Не подписал'))
        text = json.dumps(body, ensure_ascii=False)
        for forbidden in (self.IIN, self.IIN[-4:], '•', 'iin', 'phone', 'docs', 'documents', 'error', 'signed_at'):
            self.assertNotIn(forbidden, text)

    def test_route_is_in_operator_zone_with_guards(self):
        seen = []

        class Svc:
            def operator_sign_check(self, user_id, lead_id):
                seen.append((user_id, lead_id))
                return {"lead_id": lead_id, "sign": {"status": "unknown"}, "row_closed": False, "text": ""}

        resp = self._client(Svc()).post(f'/api/operator/dial_list/leads/{self.LEAD}/sign_check', json={})
        self.assertEqual((resp.status_code, resp.get_json()['status']), (200, 'success'))
        self.assertEqual(seen, [(7, self.LEAD)])
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        operator_part = src[src.index('# ── телефон оператора'):src.index('# ── руководитель')]
        route = "'/api/operator/dial_list/leads/<lead_id>/sign_check'"
        self.assertLess(operator_part.index("'/api/operator/dial_list/leads/<lead_id>/recall'"),
                        operator_part.index(route))
        chunk = operator_part[operator_part.index(route):]
        self.assertIn('@require_api_key', chunk[:300])
        self.assertIn('@_guard', chunk[:300])
        body = chunk[:chunk.find('@bp.route', 1)]
        self.assertIn('svc.operator_sign_check(user_id, lead_id)', body)
        # Карточка руководителя (маски, документы) сюда не попадает ни при каком пути.
        for forbidden in ('check_lead_signature', 'lead_card', 'mask_'):
            self.assertNotIn(forbidden, body)

    def test_operator_errors_like_other_phone_routes(self):
        svc, cur, db, checker = self._svc([])

        def denied(uid):
            raise dial_service.DialListError("Обзвон из телефона для вас не включён", 403)

        svc.operator_context = denied
        resp = self._client(svc).post(f'/api/operator/dial_list/leads/{self.LEAD}/sign_check', json={})
        self.assertEqual((resp.status_code, resp.get_json()), (403, {"error": "Обзвон из телефона для вас не включён"}))
        resp = self._client(self._svc([None])[0]).post('/api/operator/dial_list/leads/x/sign_check', json={})
        self.assertEqual((resp.status_code, resp.get_json()), (404, {"error": "Водитель не найден"}))
        self.assertEqual(cur.executed, [])

    # ── с кем разговор (контракт 4) ──────────────────────────────────────────
    def test_phone_knows_the_driver_of_the_live_call(self):
        # active_attempt состояния и ответ «Позвонить» несут lead_id — по нему телефон
        # спрашивает подписание посреди разговора. Номера в них по-прежнему нет.
        cur = _RoutedCursor([
            ('make_interval(mins => %s)',
             {'one': ('att-1', 'asg-1', 'leg_answered', 'gc-1', None, 'Иванов', self.LEAD)}),
            ('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db, sign_checker=_FakeSignChecker(db, configured=False))
        svc.operator_context = self._ctx
        svc.reconcile = lambda ctx: None
        state = svc.get_state(7)
        self.assertEqual(state['active_attempt']['lead_id'], self.LEAD)
        self.assertIn('a.lead_id', cur.sql_with('make_interval(mins => %s)')[0][0])

        cur = _RoutedCursor([
            ('FOR UPDATE OF a', {'one': ('asg-1', 'issued', 0, '77010000001', 'Иванов', self.LEAD, False)}),
            ('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]}),
            ('INSERT INTO dial_list_attempts', {'one': ('att-1', None)}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db, sign_checker=_FakeSignChecker(db, configured=False))
        svc.operator_context = self._ctx
        client = _OriginateClient(db)
        svc._client = lambda dep: client
        attempt = svc.start_call(7, 'asg-1')
        self.assertEqual((attempt['lead_id'], attempt['assignment_id']), (self.LEAD, 'asg-1'))
        self.assertNotIn('77010000001', json.dumps(attempt, ensure_ascii=False))


class _OneRowCursor:
    """Курсор на один ответ: что бы ни спросили — fetchone отдаёт row."""

    def __init__(self, row):
        self.row = row

    def execute(self, *_args, **_kwargs):
        pass

    def fetchone(self):
        return self.row


def _operator_ctx(uid):
    """Контекст оператора отдела 1954 на линии 913 — как у RingTimeoutTests."""
    return {"user_id": uid, "department_id": 1954, "name": "Оператор", "internal_number": "913",
            "settings": {"portion_size": 20, "period": "2026-10-01", "period_label": "Октябрь 2026",
                         "_period": date(2026, 10, 1), "retry_after_hours": 24, "max_attempts": 3,
                         "caller_id_for_employee": ""}}


def _phone_client(svc):
    """Клиент ручек телефона (Flask) поверх сервиса — настоящего или подделки."""
    import flask
    requester = (7, 'Оператор', 'x', 'operator', None, None, None, 'op')
    bp = dial_routes.build_dial_list_blueprint(
        db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (7, requester, None), is_admin_role=lambda role: False,
        headed_department_ids=lambda rid: [], service=svc)
    app = flask.Flask('dial_list_phone_test')
    app.register_blueprint(bp)
    return app.test_client()


class PhoneVersionGateTests(unittest.TestCase):
    """Версия телефона (владелец, 12.10.2026) — заголовок X-ICore-Phone-Version с 3.22.34.

    Автосброс на гудках (простой таймер, 13.10.2026) — только телефону 3.22.35 и новее: в срок он
    спрашивает сервер и кладёт плечо только по явному hangup: true. 3.22.33 (заголовка нет) и
    3.22.34 на вопрос «класть ли плечо» ждут ответа пять секунд, а молчание и ошибку понимают как
    «класть» — 12.10.2026 так оборвались 52 разговора. Им ring_timeout_sec: 0, а на вопрос
    ring_timeout — сразу «не класть», без базы и без Binotel.

    Наличие заголовка (phone_guards_speech) решает только смысл active_attempt у 3.22.33."""

    HEADER = 'X-ICore-Phone-Version'
    # Заголовка нет либо версию в нём не разобрать — как у выпущенного 3.22.33.
    UNGUARDED = (None, '', '   ', 'iCORE Phone', 'мусор', '3', '3.22.34-beta', 'v3.22.34', '3.22.x', '3..34',
                 '.3.22.34', '3,22,34', '1.2.3.4.5', '３.２２.３４', 32234, ['3.22.34'])
    # Любая разборчивая версия — заголовок есть.
    GUARDED = ('3.22.33', '3.22.34', ' 3.22.34 ', '3.22.35', '3.22.100', '3.23.0', '3.23', '3.22', '3.100.0',
               '4.0', '4.0.0', '10.0.0', '3.22.34.1', '3.9.99', '2.99.99')
    # С простым таймером — 3.22.35 и новее.
    TIMER = ('3.22.35', ' 3.22.35 ', '3.22.35.0', '3.22.35.1', '3.22.36', '3.22.100', '3.23', '3.23.0',
             '3.100.0', '4.0', '4.0.0', '10.0.0')
    NO_TIMER = UNGUARDED + ('3.22.33', '3.22.34', ' 3.22.34 ', '3.22', '3.22.34.1', '3.22.34.99', '3.9.99',
                            '2.99.99', '3.21.99')

    def test_header_name_and_no_version_threshold(self):
        self.assertEqual(dial_service.PHONE_VERSION_HEADER, self.HEADER)
        # У наличия заголовка порога версии нет.
        self.assertFalse(hasattr(dial_service, 'SPEECH_GUARD_PHONE_VERSION'))
        src = inspect.getsource(dial_service.phone_guards_speech)
        self.assertNotIn('>=', src)
        self.assertNotIn('(3, 22', src)

    def test_ring_timer_needs_3_22_35(self):
        self.assertEqual(dial_service.RING_TIMER_MIN_VERSION, (3, 22, 35))
        timer = dial_service.phone_has_ring_timer
        for header in self.TIMER:
            self.assertIs(timer(header), True, repr(header))
        for header in self.NO_TIMER:
            self.assertIs(timer(header), False, repr(header))

    def test_version_is_parsed_strictly(self):
        parse = dial_service.parse_phone_version
        self.assertEqual(parse('3.22.34'), (3, 22, 34))
        self.assertEqual(parse(' 3.22.34 '), (3, 22, 34))
        self.assertEqual(parse('3.23'), (3, 23))
        self.assertEqual(parse('3.22.34.1'), (3, 22, 34, 1))
        self.assertEqual(parse('03.022.034'), (3, 22, 34))
        # Всё, в чём версию не узнать наверняка, — как её отсутствие.
        for garbled in (None, '', '   ', 'iCORE Phone', 'мусор', '3', '3.22.34-beta', 'v3.22.34', '3.22.x',
                        '3..34', '.3.22.34', '3.22.34.', '3,22,34', '1.2.3.4.5', '３.２２.３４', '3.22.34 (beta)',
                        '-3.22.34', '3.22.+34', '123456.1', 32234, ['3.22.34'], {'v': '3.22.34'}):
            self.assertIsNone(parse(garbled), repr(garbled))

    def test_any_readable_version_guards_speech(self):
        guards = dial_service.phone_guards_speech
        for header in self.UNGUARDED:
            self.assertIs(guards(header), False, repr(header))
        for header in self.GUARDED:
            self.assertIs(guards(header), True, repr(header))
        # Пять случаев из задачи владельца: нет заголовка, мусор, 3.22.33, 3.22.34, 3.23.0.
        self.assertEqual([guards(h) for h in (None, 'мусор', '3.22.33', '3.22.34', '3.23.0')],
                         [False, False, True, True, True])

    # ── состояние: ring_timeout_sec ──────────────────────────────────────────
    def _state_svc(self, rings=(None,)):
        cur = _RoutedCursor([('FROM sip_department_config', {'one': rings}),
                             ('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]})])
        svc = dial_service.DialListService(db=_TxDb(cur))
        svc.operator_context = _operator_ctx
        svc.reconcile = lambda ctx: None
        return svc, cur

    def test_state_gives_auto_reset_only_to_phone_with_ring_timer(self):
        for header in self.NO_TIMER:
            svc, cur = self._state_svc()
            state = svc.get_state(7, phone_version=header)
            self.assertEqual(state["ring_timeout_sec"], 0, repr(header))
            # Настройку отдела такому телефону даже не читаем.
            self.assertEqual(cur.sql_with('FROM sip_department_config'), [], repr(header))
            # Остальное состояние — как у всех.
            self.assertEqual((state["enabled"], state["leg_timeout_sec"], state["outcomes"][0]["name"]),
                             (True, dial_service.LEG_TIMEOUT_SEC, 'Отказ'))
        for header in self.TIMER:
            svc, cur = self._state_svc()
            self.assertEqual(svc.get_state(7, phone_version=header)["ring_timeout_sec"], 34, repr(header))
            self.assertEqual(len(cur.sql_with('FROM sip_department_config')), 1, repr(header))

    def test_no_version_given_means_released_phone(self):
        # Вызывающий забыл передать версию — безопасная сторона: автосброса нет.
        svc, cur = self._state_svc()
        self.assertEqual(svc.get_state(7)["ring_timeout_sec"], 0)
        self.assertEqual(inspect.signature(dial_service.DialListService.get_state).parameters['phone_version'].default,
                         None)
        for name in ('issue_next_portion', 'recall_lead', 'phone_event'):
            params = inspect.signature(getattr(dial_service.DialListService, name)).parameters
            self.assertIsNone(params['phone_version'].default, name)

    def test_department_setting_still_rules_the_timer_phone(self):
        # Отдел выключил сброс (0 гудков) или задал свои — телефону с таймером уходит это (+ поправка).
        for rings, expected in (((0,), 0), ((3,), 24), ((None,), 34), (None, 34)):
            svc, _ = self._state_svc(rings)
            self.assertEqual(svc.get_state(7, phone_version=TIMER_PHONE)["ring_timeout_sec"], expected, rings)

    def test_next_and_recall_answers_follow_the_same_gate(self):
        # «Ещё» и «Позвонить ещё раз» отвечают состоянием — телефон читает из него
        # ring_timeout_sec так же, как из GET: версия обязана дойти и туда.
        seen = []

        def spy(uid, phone_version=None):
            seen.append(phone_version)
            return {"portion": None}

        helper = SignedDriversNeverIssuedTests('test_due_rule')
        svc, _, _, _ = helper._next()
        svc.get_state = spy
        with helper._now():
            svc.issue_next_portion(7, phone_version='3.22.34')
            svc.issue_next_portion(7)
        recall = RecallTests('test_verdict_rules')
        svc, _, _ = recall._svc()
        svc.get_state = spy
        svc.recall_lead(7, recall.LEAD, phone_version='3.23.0')
        svc.recall_lead(7, recall.LEAD)
        self.assertEqual(seen, ['3.22.34', None, '3.23.0', None])

    # ── событие ring_timeout ────────────────────────────────────────────────
    def _no_db_svc(self):
        """Сервис, у которого нет ни базы, ни Binotel, ни контекста оператора: любое
        обращение к ним — провал теста."""
        test = self

        class _NoDb:
            def _get_cursor(self):
                test.fail('ответ телефону без простого таймера не должен ходить в базу')

        def no_context(uid):
            test.fail('ответ телефону без простого таймера не должен читать контекст оператора')

        def no_client(dep):
            test.fail('ответ телефону без простого таймера не должен спрашивать Binotel')

        svc = dial_service.DialListService(db=_NoDb())
        svc.operator_context = no_context
        svc._client = no_client
        return svc

    def test_released_phone_question_is_answered_at_once_without_db_or_binotel(self):
        svc = self._no_db_svc()
        for header in self.NO_TIMER:
            with self.assertLogs('dial_list.service', level='INFO') as logs:
                res = svc.phone_event(7, 'att-1', 'ring_timeout', ring_sec=25, phone_version=header)
            # Ровно то, что читает 3.22.33: hangup — класть ли, talking — говорит ли водитель.
            self.assertEqual(res, {"attempt_id": "att-1", "final": False, "cancelled": False,
                                   "outcome_required": None, "hangup": False, "talking": False}, repr(header))
            self.assertTrue(any('плечо не кладём' in line and 'att-1' in line for line in logs.output), logs.output)
        # Версию не передали вовсе — то же.
        with self.assertLogs('dial_list.service', level='INFO'):
            self.assertIs(svc.phone_event(7, 'att-1', 'ring_timeout')["hangup"], False)
        # Мусор в ring_sec ответу не мешает.
        with self.assertLogs('dial_list.service', level='INFO'):
            self.assertIs(svc.phone_event(7, 'att-1', 'ring_timeout', ring_sec='много')["hangup"], False)
        src = inspect.getsource(dial_service.DialListService.phone_event)
        # Развилка — раньше контекста оператора (он читает базу): порядок закреплён.
        self.assertLess(src.index('self._ring_timeout_unguarded('), src.index('self.operator_context(user_id)'))
        unguarded = inspect.getsource(dial_service.DialListService._ring_timeout_unguarded)
        for forbidden in ('_get_cursor', '_client(', 'call_details', 'operator_context', '_mark_ring_timeout'):
            self.assertNotIn(forbidden, unguarded, forbidden)

    def test_released_phone_question_never_marks_the_attempt(self):
        # Плечо не кладём — значит, и сбросом на гудках попытка не помечается: чем кончился
        # звонок, телефон сообщит в ended.
        helper = RingTimeoutTests('test_schema_and_event')
        svc, cur, client, finished = helper._event_svc(
            ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, None),
            {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'CALLING'}})
        with self.assertLogs('dial_list.service', level='INFO'):
            res = svc.phone_event(7, 'att-1', 'ring_timeout', ring_sec=25)
        self.assertEqual((res["hangup"], res["talking"]), (False, False))
        self.assertEqual((cur.executed, client.calls, finished), ([], [], []))

    def test_timer_phone_question_still_goes_to_binotel(self):
        helper = RingTimeoutTests('test_schema_and_event')
        for header in ('3.22.35', '3.22.35.0', '3.23.0', '4.1'):
            svc, cur, client, finished = helper._event_svc(
                ('leg_answered', 'gc-1', '', False), ('leg_answered', '', False, None, 't', None, 't'),
                {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'CALLING'}})
            res = svc.phone_event(7, 'att-1', 'ring_timeout', ring_sec=25, phone_version=header)
            self.assertEqual((res["hangup"], res["talking"], client.calls), (True, False, ['gc-1']), header)
            self.assertEqual(len(cur.sql_with(helper.MARK)), 1, header)

    def test_other_events_of_released_phone_work_as_before(self):
        # Развилка — только у вопроса ring_timeout. ended с ring_timeout от 3.22.33 (плечо
        # кончилось после вопроса, разговора по Binotel не было) — как раньше: отметка, решает АТС.
        # Финал «не ответил» закрывает попытку сразу; ответ водителя — данными после конца плеча
        # (13.10.2026: ANSWER в момент конца плеча может быть ещё неполным), долг — как обычно.
        helper = RingTimeoutTests('test_schema_and_event')
        for answer, closed in (('CANCEL', [('att-1', 'CANCEL')]), ('ANSWER', [])):
            svc, cur, finished, cancelled = helper._ended_svc(
                ('leg_answered', 'gc-1', 'asg-1', False, '', None),
                ('finished', answer, False, None, 't', None, 't'),
                {'gc-1': {'general_call_id': 'gc-1', 'disposition': answer, 'billsec': 40, 'waitsec': 27}})
            res = svc.phone_event(7, 'att-1', 'ended', by_operator=False, leg_sec=70, ring_timeout=True)
            self.assertEqual([p for _, p in cur.sql_with(helper.MARK)], [('ring_timeout', 70, 70, 'att-1')])
            self.assertEqual((finished, res["outcome_required"]), (closed, answer == 'ANSWER'), answer)

    # ── ручки: заголовок доходит до сервиса ─────────────────────────────────
    def test_routes_pass_the_version_header_as_it_came(self):
        seen = []

        class Svc:
            def get_state(self, user_id, phone_version=None):
                seen.append(('state', phone_version))
                return {}

            def issue_next_portion(self, user_id, phone_version=None):
                seen.append(('next', phone_version))
                return {}

            def recall_lead(self, user_id, lead_id, phone_version=None):
                seen.append(('recall', phone_version))
                return {}

            def phone_event(self, user_id, attempt_id, event, at=None, phone_version=None, **kwargs):
                seen.append((event, phone_version))
                return {}

        client = _phone_client(Svc())
        for headers in ({self.HEADER: '3.22.34'}, {'x-icore-phone-version': '3.23.0'}, {}, {self.HEADER: 'мусор'}):
            client.get('/api/operator/dial_list', headers=headers)
            client.post('/api/operator/dial_list/next', json={}, headers=headers)
            client.post(f'/api/operator/dial_list/leads/{RecallTests.LEAD}/recall', json={}, headers=headers)
            client.post('/api/operator/dial_list/attempts/att-1/phone_event', json={"event": "ring_timeout"},
                        headers=headers)
        self.assertEqual(seen, [(kind, version) for version in ('3.22.34', '3.23.0', None, 'мусор')
                                for kind in ('state', 'next', 'recall', 'ring_timeout')])
        # Разбирает и решает сервис — у ручек своей копии правила нет.
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        self.assertIn('request.headers.get(PHONE_VERSION_HEADER)', src)
        # Пятая — автоцикл Тез (POST …/auto/next, 15.10.2026): его ответ — то же состояние.
        self.assertEqual(src.count('phone_version=_phone_version()'), 5)
        for own_rule in ('phone_guards_speech(', 'parse_phone_version(', 'SPEECH_GUARD_PHONE_VERSION',
                         'phone_has_ring_timer(', 'RING_TIMER_MIN_VERSION'):
            self.assertNotIn(own_rule, src)

    def test_state_route_gates_on_the_version(self):
        # Весь путь GET /api/operator/dial_list с настоящим сервисом: без заголовка, с мусором, 3.22.33
        # и 3.22.34 — 0; 3.22.35 и новее — настройка отдела (по умолчанию пять гудков, 34 с).
        for headers, expected in (({}, 0), ({self.HEADER: 'мусор'}, 0), ({self.HEADER: '3.22.33'}, 0),
                                  ({self.HEADER: '3.22.34'}, 0), ({self.HEADER: '3.22.35'}, 34),
                                  ({self.HEADER: '3.23.0'}, 34)):
            svc, cur = self._state_svc()
            resp = _phone_client(svc).get('/api/operator/dial_list', headers=headers)
            self.assertEqual(resp.status_code, 200, headers)
            body = resp.get_json()
            self.assertEqual((body["status"], body["ring_timeout_sec"]), ("success", expected), headers)
            # Настройку отдела читаем только для телефона с таймером.
            self.assertEqual(len(cur.sql_with('FROM sip_department_config')), 1 if expected else 0, headers)

    def test_released_phone_gets_200_through_the_real_route(self):
        # Весь путь ручки с настоящим сервисом без базы: 3.22.33 (заголовка нет или он
        # неразборчив) и 3.22.34 получают 200 и «не класть».
        client = _phone_client(self._no_db_svc())
        url = '/api/operator/dial_list/attempts/att-1/phone_event'
        for headers in ({}, {self.HEADER: ''}, {self.HEADER: 'iCORE Phone'}, {self.HEADER: 'v3.22.34'},
                        {self.HEADER: 'мусор'}, {self.HEADER: '3.22.34'}):
            with self.assertLogs('dial_list.service', level='INFO'):
                resp = client.post(url, json={"event": "ring_timeout", "ring_sec": 25}, headers=headers)
            self.assertEqual(resp.status_code, 200, headers)
            body = resp.get_json()
            self.assertEqual((body["status"], body["hangup"], body["talking"]), ("success", False, False), headers)
        # Неизвестное событие — по-прежнему 400, и тоже без базы.
        resp = client.post(url, json={"event": "ring"})
        self.assertEqual(resp.status_code, 400)


class StateAudioSettingsTests(unittest.TestCase):
    """Анализ звука на плече выключен насовсем (владелец, 13.10.2026: «нестабильная вещь —
    отключить»): ключи его настроек в состоянии — только для телефона 3.22.34 и всегда «выключено»,
    а переменные окружения, которыми их выключали, больше не читаются."""

    def _state(self, phone_version=GUARDED_PHONE):
        cur = _RoutedCursor([('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]})])
        svc = dial_service.DialListService(db=_TxDb(cur))
        svc.operator_context = _operator_ctx
        svc.reconcile = lambda ctx: None
        return svc.get_state(7, phone_version=phone_version)

    def test_state_always_says_sound_is_off(self):
        self.assertEqual(dial_service.SOUND_OFF_STATE, {"speech_guard": False, "announcement_ms": 0,
                                                        "announcement_sit": False, "announcement_mode": "off"})
        env = {'DIAL_LIST_SPEECH_GUARD': '1', 'DIAL_LIST_ANNOUNCEMENT_MS': '3500',
               'DIAL_LIST_ANNOUNCEMENT_SIT': '1', 'DIAL_LIST_ANNOUNCEMENT_MODE': 'auto'}
        for phone in (GUARDED_PHONE, TIMER_PHONE, None):
            with unittest.mock.patch.dict(os.environ, env):
                state = self._state(phone)
            got = {key: state[key] for key in dial_service.SOUND_OFF_STATE}
            self.assertEqual(got, dial_service.SOUND_OFF_STATE, phone)
            self.assertIs(type(state["speech_guard"]), bool)
            self.assertIs(type(state["announcement_ms"]), int)

    def test_environment_switches_are_gone(self):
        for name in ('announcement_ms', 'announcement_sit_enabled', 'announcement_mode', 'speech_guard_enabled',
                     'ANNOUNCEMENT_MS_ENV', 'ANNOUNCEMENT_SIT_ENV', 'ANNOUNCEMENT_MODE_ENV', 'SPEECH_GUARD_ENV'):
            self.assertFalse(hasattr(dial_service, name), name)
        src = inspect.getsource(dial_service)
        for env_name in ('DIAL_LIST_SPEECH_GUARD"', 'DIAL_LIST_ANNOUNCEMENT_MS"', 'DIAL_LIST_ANNOUNCEMENT_SIT"',
                         'DIAL_LIST_ANNOUNCEMENT_MODE"'):
            self.assertNotIn(env_name, src)


class PhoneHeardTests(unittest.TestCase):
    """Что телефон услышал на плече (владелец, 12.10.2026; телефон с 3.22.34) — ключи события
    ended. talk_detected: true и «ключа нет» — как раньше (плечо кончилось после ответа — итог
    нужен); false — разговора телефон не услышал, решает Binotel: финальный ANSWER не короче
    секунды — итог нужен, занято / не ответил / отмена / прочее — не нужен и строка уходит в
    «Не дозвонились», исхода ещё нет — outcome_required: null. Долг по итогу (_pending_outcome)
    считается так же."""

    NOW_ANSWER = {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'ANSWER', 'billsec': 20, 'waitsec': 9}}

    # ── правило «ответ Binotel — разговор ли» ────────────────────────────────
    def test_answer_is_a_talk_unless_the_phone_knows_better(self):
        talked, result = dial_service.attempt_talked, dial_service.attempt_result
        # Телефон до 3.22.34 ничего не сообщил — ответ Binotel и есть разговор, с любой длительностью.
        self.assertTrue(talked(0))
        self.assertTrue(talked(0, '', None))
        self.assertTrue(talked(0, 'ring_timeout', None))
        self.assertEqual(result('ANSWER', 0), 'answered')
        self.assertEqual(result('ANSWER', 0, 'ring_timeout', None), 'answered')
        # Телефон разговор услышал — тоже.
        self.assertTrue(talked(0, '', True))
        self.assertEqual(result('ANSWERED', 0, '', True), 'answered')
        # Не услышал — только ответ не короче секунды.
        self.assertFalse(talked(0, '', False))
        self.assertTrue(talked(1, '', False))
        self.assertEqual(result('ANSWER', 0, '', False), 'no_answer')
        self.assertEqual(result('ANSWER', '0', '', False), 'no_answer')
        self.assertEqual(result('ANSWER', None, '', False), 'no_answer')
        self.assertEqual(result('ANSWER', 1, '', False), 'answered')
        self.assertEqual(result('VM-SUCCESS', 37, 'ring_timeout', False), 'answered')
        # Сообщение сети — недозвон при любом ответе, длительности и talk_detected.
        for billsec in (0, 12, 300):
            for talk in (None, True, False):
                self.assertFalse(talked(billsec, 'announcement', talk))
                self.assertEqual(result('ANSWER', billsec, 'announcement', talk), 'no_answer')
        # Остальные исходы поправка не трогает; нефинальный — по-прежнему пусто.
        for disposition, expected in (('BUSY', 'busy'), ('NOANSWER', 'no_answer'), ('CANCEL', 'no_answer'),
                                      ('CONGESTION', 'other'), ('', ''), ('ONLINE', ''), ('CALLING', '')):
            for auto in ('', 'ring_timeout', 'announcement'):
                for talk in (None, True, False):
                    self.assertEqual(result(disposition, 0, auto, talk), expected, (disposition, auto, talk))
        # Тот же закон в SQL — одной строкой на все запросы сервиса.
        self.assertEqual(dial_service.ATTEMPT_TALK_SQL,
                         "t.auto_hangup <> 'announcement' AND (t.talk_detected IS NOT FALSE OR t.billsec >= 1)")

    def test_auto_hangup_values(self):
        clean = dial_service.clean_auto_hangup
        self.assertEqual(dial_service.AUTO_HANGUPS, ('ring_timeout', 'announcement'))
        self.assertEqual(clean('announcement'), 'announcement')
        self.assertEqual(clean(' Announcement '), 'announcement')
        self.assertEqual(clean('ring_timeout'), 'ring_timeout')
        for unknown in (None, '', 'busy', 'operator', 'true', True, 1, ['announcement'], {'a': 1}):
            self.assertEqual(clean(unknown), '', repr(unknown))

    # ── решение по итогу ────────────────────────────────────────────────────
    def _decide(self, state, disposition, hung_up=None, ring_at=None, leg_answered='t', outcome_id=None,
                cancelled=False, auto_hangup='', talk=None, billsec=0):
        svc = dial_service.DialListService(db=None)
        row = (state, disposition, cancelled, hung_up, leg_answered, outcome_id, ring_at, auto_hangup, talk, billsec)
        return svc._outcome_decision(_OneRowCursor(row), 'att-1')

    def test_talk_detected_matrix(self):
        decide = self._decide
        # talk_detected true и «ключа нет»: плечо кончилось после ответа — итог нужен, как раньше.
        for talk in (True, None):
            self.assertEqual(decide('ended', '', talk=talk), (False, True), talk)
            self.assertEqual(decide('finished', 'ANSWER', talk=talk, billsec=30), (False, True), talk)
            self.assertEqual(decide('finished', 'ANSWER', talk=talk, billsec=0), (False, True), talk)
            # Не ответ Binotel (таймаут, незнакомый статус) — решения не меняет.
            for disposition in ('UNKNOWN', 'FAILED', 'VM'):
                self.assertEqual(decide('finished', disposition, talk=talk), (False, True), (talk, disposition))
        # «Ключа нет» (прежний телефон) — что бы ни ответил Binotel: оператор выбирает итог сам.
        for disposition in ('BUSY', 'NOANSWER', 'CANCEL', 'CHANUNAVAIL', 'CONGESTION'):
            self.assertEqual(decide('finished', disposition, talk=None), (False, True), disposition)
        # А разговор, который будто бы слышал телефон, финальное «не дозвонились» Binotel не
        # перебивает (ревью 13.10.2026: реплика оператора в паузе между фразами сообщения сети —
        # для телефона «обмен репликами»): итог не нужен. Сохранённый итог остаётся (outcome_id).
        self.assertEqual(sorted(dial_service.NOT_ANSWERED_FINAL_DISPOSITIONS),
                         ['BUSY', 'CANCEL', 'CANCELED', 'CANCELLED', 'CHANUNAVAIL', 'CONGESTION', 'NO ANSWER',
                          'NOANSWER', 'NO_ANSWER'])
        for disposition in ('BUSY', 'NOANSWER', 'CANCEL', 'CHANUNAVAIL', 'CONGESTION', 'busy'):
            self.assertEqual(decide('finished', disposition, talk=True), (False, False), disposition)
            self.assertEqual(decide('finished', disposition, talk=True, outcome_id='o'), (False, False), disposition)
        self.assertFalse(dial_service.binotel_not_answered('UNKNOWN'))
        self.assertFalse(dial_service.binotel_not_answered('ONLINE'))
        self.assertTrue(dial_service.binotel_not_answered(' chanunavail '))
        # talk_detected false: решает Binotel.
        self.assertEqual(decide('ended', '', talk=False), (False, None))                      # исхода ещё нет
        self.assertEqual(decide('finished', 'ANSWER', talk=False, billsec=1), (False, True))  # ответил
        self.assertEqual(decide('finished', 'ANSWERED', talk=False, billsec=95), (False, True))
        self.assertEqual(decide('finished', 'ANSWER', talk=False, billsec=0), (False, False))  # ответ без разговора
        for disposition in ('BUSY', 'NOANSWER', 'CANCEL', 'CONGESTION', 'UNKNOWN'):
            self.assertEqual(decide('finished', disposition, talk=False), (False, False), disposition)
            self.assertEqual(decide('finished', disposition, talk=False, billsec=7), (False, False), disposition)

    def test_talk_detected_with_operator_hangup_and_ring_timeout(self):
        decide = self._decide
        # Сброс на гудках решается по Binotel при любом talk_detected — как раньше; false лишь
        # требует, чтобы ответ был не короче секунды. Плечо положил сам телефон — значит,
        # разговора, по его же мнению, не было.
        for marks in ({"ring_at": 't', "auto_hangup": 'ring_timeout'},
                      {"hung_up": 't', "ring_at": 't', "auto_hangup": 'ring_timeout'}):
            for talk in (None, True, False):
                self.assertEqual(decide('ended', '', talk=talk, **marks), (False, None), (marks, talk))
                self.assertEqual(decide('finished', 'ANSWER', talk=talk, billsec=20, **marks), (False, True), (marks, talk))
                self.assertEqual(decide('finished', 'NOANSWER', talk=talk, **marks), (False, False), (marks, talk))
            self.assertEqual(decide('finished', 'ANSWER', talk=None, billsec=0, **marks), (False, True), marks)
            self.assertEqual(decide('finished', 'ANSWER', talk=True, billsec=0, **marks), (False, True), marks)
            self.assertEqual(decide('finished', 'ANSWER', talk=False, billsec=0, **marks), (False, False), marks)
        # Отбой оператора: «ключа нет» и false — по Binotel, как раньше…
        for talk in (None, False):
            self.assertEqual(decide('ended', '', talk=talk, hung_up='t'), (False, None), talk)
            self.assertEqual(decide('finished', 'ANSWER', talk=talk, billsec=20, hung_up='t'), (False, True), talk)
            self.assertEqual(decide('finished', 'NOANSWER', talk=talk, hung_up='t'), (False, False), talk)
        self.assertEqual(decide('finished', 'ANSWER', talk=None, billsec=0, hung_up='t'), (False, True))
        self.assertEqual(decide('finished', 'ANSWER', talk=False, billsec=0, hung_up='t'), (False, False))
        # …а разговор, который слышал телефон, — итог сразу, исхода Binotel не ждём: оператор
        # закончил разговор сам (13.10.2026).
        for state, disposition, billsec in (('ended', '', 0), ('finished', 'ANSWER', 0), ('finished', 'ANSWER', 40)):
            self.assertEqual(decide(state, disposition, talk=True, billsec=billsec, hung_up='t'), (False, True),
                             (state, disposition, billsec))
        # Пришло финальное «не дозвонились» — разговора не было, итог не нужен (ревью 13.10.2026).
        for disposition in ('NOANSWER', 'BUSY', 'CANCEL'):
            self.assertEqual(decide('finished', disposition, talk=True, hung_up='t'), (False, False), disposition)

    def test_unconfirmed_leg_cancelled_and_done_attempts(self):
        decide = self._decide
        # Плечо без ACK: нужен, только если Binotel подтвердил ответ, — и у нового телефона.
        for talk in (None, True, False):
            self.assertEqual(decide('ended', '', leg_answered=None, talk=talk), (False, False), talk)
            self.assertEqual(decide('finished', 'ANSWER', leg_answered=None, talk=talk, billsec=9), (False, True), talk)
            self.assertEqual(decide('finished', 'BUSY', leg_answered=None, talk=talk), (False, False), talk)
            # Отменённая, с итогом, проваленная — как раньше.
            self.assertEqual(decide('finished', 'CANCEL', cancelled=True, hung_up='t', talk=talk), (True, False), talk)
            self.assertEqual(decide('finished', 'ANSWER', outcome_id='o', talk=talk, billsec=9), (False, False), talk)
            self.assertEqual(decide('failed', '', leg_answered=None, talk=talk), (False, False), talk)
        self.assertEqual(decide('finished', 'ANSWER', leg_answered=None, talk=False, billsec=0), (False, False))

    # ── событие ended ────────────────────────────────────────────────────────
    def _ended(self, row, decision, answer=None, cleared=True, phone_version=GUARDED_PHONE, **event):
        """row — (state, general_call_id, assignment_id, cancelled, disposition, ring_timeout_at)
        попытки до события; decision — строка _outcome_decision после него, все 10 колонок;
        cleared — нашлась ли отметка сброса, которую снимают."""
        cur = _RoutedCursor([
            ('general_call_id, assignment_id, cancelled, disposition, ring_timeout_at', {'one': row}),
            ('leg_answered_at, outcome_id', {'one': decision}),
            ('SET ring_timeout_at = NULL', {'one': ('att-1',) if cleared else None}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db)
        svc.operator_context = _operator_ctx
        client = _CallDetailsClient(db, {} if answer is None else answer)
        svc._client = lambda dep: client
        calls = []
        svc._finish_by_call = lambda aid, call, source: calls.append(('finish', call['disposition']))
        svc._cancel_attempt = lambda cur, aid, asg, late=False: calls.append(('cancel', late)) or True
        svc._unanswer_attempt = lambda cur, aid, asg: calls.append(('unanswer', aid, asg)) or True
        res = svc.phone_event(7, 'att-1', 'ended', phone_version=phone_version, **event)
        return res, cur, calls, client

    TALK = 'SET talk_detected = %s'
    CLEAR = 'SET ring_timeout_at = NULL'
    OPEN = ('leg_answered', 'gc-1', 'asg-1', False, '', None)

    def test_talk_detected_is_stored_and_decides_at_once_when_binotel_knows(self):
        # Плечо кончилось, разговора телефон не услышал, Binotel уже знает «занято»:
        # попытка закрыта, итог не нужен — без ожидания.
        res, cur, calls, client = self._ended(
            self.OPEN, ('finished', 'BUSY', False, None, 't', None, None, '', False, 0),
            {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'BUSY', 'billsec': 0, 'waitsec': 12}},
            by_operator=False, leg_sec=14, talk_detected=False)
        self.assertEqual([p for _, p in cur.sql_with(self.TALK)], [(False, 'att-1')])
        self.assertEqual(calls, [('finish', 'BUSY')])
        self.assertEqual((res["final"], res["cancelled"], res["outcome_required"]), (True, False, False))
        self.assertEqual(client.inside_tx, [False])                                # Binotel — вне транзакций
        self.assertEqual(cur.sql_with(RingTimeoutTests.MARK), [])                  # это не сброс на гудках
        self.assertEqual(cur.sql_with('operator_hangup_at = COALESCE'), [])        # и не отбой оператора

    def test_no_final_yet_means_null_until_it_arrives(self):
        res, cur, calls, _ = self._ended(self.OPEN, ('ended', '', False, None, 't', None, None, '', False, 0),
                                         talk_detected=False)
        self.assertEqual((res["state"], res["final"], res["outcome_required"]), ('ended', False, None))
        self.assertEqual(calls, [])
        # Тот же конец плеча с talk_detected true и без ключа — итог нужен сразу.
        for extra, stored in (({"talk_detected": True}, [(True, 'att-1')]), ({}, [])):
            talk = extra.get("talk_detected")
            res, cur, _, _ = self._ended(self.OPEN, ('ended', '', False, None, 't', None, None, '', talk, 0), **extra)
            self.assertIs(res["outcome_required"], True, extra)
            self.assertEqual([p for _, p in cur.sql_with(self.TALK)], stored, extra)

    def _hangup_event(self, row, decision):
        """Событие operator_hangup по попытке row (state, general_call_id, assignment_id, cancelled,
        disposition, ring_timeout_at, talk_detected, auto_hangup); decision — строка _outcome_decision."""
        cur = _RoutedCursor([
            ('general_call_id, assignment_id, cancelled, disposition, ring_timeout_at', {'one': row}),
            ('leg_answered_at, outcome_id', {'one': decision}),
        ])
        svc = dial_service.DialListService(db=_TxDb(cur))
        svc.operator_context = _operator_ctx
        cancelled = []
        svc._cancel_attempt = lambda cur, aid, asg, late=False: cancelled.append((aid, late)) or True
        res = svc.phone_event(7, 'att-1', 'operator_hangup', phone_version=GUARDED_PHONE)
        return res, cur, cancelled

    def test_late_operator_hangup_never_cancels_a_talk_the_phone_heard(self):
        # Ревью 13.10.2026: operator_hangup и ended идут отдельными запросами и приходят в любом
        # порядке. ended(talk_detected true) пришёл первым и открыл окно итога, Binotel закрыл
        # попытку «не ответил», и только потом — запоздавший operator_hangup. В нём ключа нет, но
        # разговор уже записан в попытке: поздней отмены нет, иначе сохранить итог было бы нельзя.
        decision = ('finished', 'NOANSWER', False, 't', 't', None, None, '', True, 0)
        res, cur, cancelled = self._hangup_event(('finished', 'gc-1', 'asg-1', False, 'NOANSWER', None, True, ''),
                                                 decision)
        self.assertEqual(cancelled, [])
        self.assertEqual(res["cancelled"], False)
        self.assertEqual(len(cur.sql_with('operator_hangup_at = COALESCE')), 1)      # отбой записан
        sql = cur.sql_with('general_call_id, assignment_id, cancelled, disposition, ring_timeout_at')[0][0]
        self.assertIn('talk_detected, auto_hangup', sql)
        self.assertIn('FOR UPDATE', sql)
        # Сообщение сети — недозвон, а не отмена, даже без отметки сброса.
        res, cur, cancelled = self._hangup_event(
            ('finished', 'gc-1', 'asg-1', False, 'NOANSWER', None, None, 'announcement'), decision)
        self.assertEqual(cancelled, [])
        # Телефон разговора не слышал (или не сообщал) — поздняя отмена, как раньше; и у строки без
        # новых колонок (подделки прежних тестов) — тоже.
        for row in (('finished', 'gc-1', 'asg-1', False, 'NOANSWER', None, False, ''),
                    ('finished', 'gc-1', 'asg-1', False, 'NOANSWER', None, None, ''),
                    ('finished', 'gc-1', 'asg-1', False, 'NOANSWER', None)):
            res, cur, cancelled = self._hangup_event(row, decision)
            self.assertEqual(cancelled, [('att-1', True)], row)

    def test_announcement_keys_are_stored_for_the_precision_report(self):
        # Ревью 13.10.2026: announcement_suspected — распознавание по речи сработало хоть раз (любой
        # режим), announcement_by — кто решил, что это сообщение сети. Пишутся как факт с телефона:
        # и по закрытой попытке; только true/false и знакомые слова; announcement_by — только с
        # auto_hangup 'announcement'.
        STORE = 'SET announcement_suspected = COALESCE(%s, announcement_suspected)'
        res, cur, calls, _ = self._ended(self.OPEN, ('ended', '', False, None, 't', None, None, '', False, 0),
                                         talk_detected=False, announcement_suspected=True)
        self.assertEqual([p for _, p in cur.sql_with(STORE)], [(True, '', 'att-1')])
        res, cur, calls, _ = self._ended(self.OPEN,
                                         ('ended', '', False, None, 't', None, 't', 'announcement', False, 0),
                                         ring_timeout=True, auto_hangup='announcement', talk_detected=False,
                                         announcement_suspected=True, announcement_by=' Operator ')
        self.assertEqual([p for _, p in cur.sql_with(STORE)], [(True, 'operator', 'att-1')])
        # Отбой оператора по подсказке — то же сообщение сети: отметка сброса с причиной, не отбой
        # оператора и не отмена, итог не нужен.
        self.assertEqual([p for _, p in cur.sql_with(RingTimeoutTests.MARK)], [('announcement', 0, 0, 'att-1')])
        self.assertEqual(cur.sql_with('operator_hangup_at = COALESCE'), [])
        self.assertIs(res["outcome_required"], False)
        # Закрытая попытка — пишем всё равно.
        res, cur, calls, _ = self._ended(('finished', 'gc-1', 'asg-1', False, 'BUSY', None),
                                         ('finished', 'BUSY', False, None, 't', None, None, '', False, 0),
                                         talk_detected=False, announcement_suspected=False)
        self.assertEqual([p for _, p in cur.sql_with(STORE)], [(False, '', 'att-1')])
        # Мусор и announcement_by без сообщения сети — как их нет.
        for keys in ({"announcement_suspected": 'true'}, {"announcement_suspected": 1},
                     {"announcement_by": 'operator'}, {"announcement_by": 'кто-то', "auto_hangup": 'announcement'},
                     {}):
            res, cur, calls, _ = self._ended(self.OPEN, ('ended', '', False, None, 't', None, None, '', False, 0),
                                             talk_detected=False, **keys)
            self.assertEqual(cur.sql_with(STORE), [], keys)
        self.assertEqual(dial_service.ANNOUNCEMENT_BY_VALUES, ('auto', 'sit', 'operator'))
        for raw, clean in (('auto', 'auto'), ('SIT', 'sit'), (' operator ', 'operator'), ('', ''), (None, ''),
                           (True, ''), (['operator'], ''), ('человек', '')):
            self.assertEqual(dial_service.clean_announcement_by(raw), clean, repr(raw))

    def test_route_passes_both_keys_as_they_came(self):
        # «Ключа нет» (телефон 3.22.33) и false сервис различает — ручка передаёт как есть.
        seen = []

        announcement = []

        class Svc:
            def phone_event(self, user_id, attempt_id, event, at=None, **kwargs):
                seen.append((event, kwargs['talk_detected'], kwargs['auto_hangup'], kwargs['ring_timeout'],
                             kwargs['by_operator'], kwargs['leg_sec']))
                announcement.append((kwargs['announcement_suspected'], kwargs['announcement_by']))
                return {"attempt_id": attempt_id}

        client = _phone_client(Svc())
        url = '/api/operator/dial_list/attempts/att-1/phone_event'
        client.post(url, json={"event": "ended", "by_operator": False, "leg_sec": 12, "talk_detected": False})
        client.post(url, json={"event": "ended", "by_operator": False, "leg_sec": 80, "talk_detected": True})
        client.post(url, json={"event": "ended", "by_operator": True, "leg_sec": 3})          # 3.22.33
        client.post(url, json={"event": "ended", "by_operator": False, "leg_sec": 9, "ring_timeout": True,
                               "auto_hangup": "announcement", "talk_detected": False})
        self.assertEqual(seen, [('ended', False, None, False, False, 12), ('ended', True, None, False, False, 80),
                                ('ended', None, None, False, True, 3),
                                ('ended', False, 'announcement', True, False, 9)])
        # Замер распознавания сообщения сети (ревью 13.10.2026) — тоже как пришёл.
        client.post(url, json={"event": "ended", "by_operator": False, "leg_sec": 7, "ring_timeout": True,
                               "auto_hangup": "announcement", "talk_detected": False,
                               "announcement_suspected": True, "announcement_by": "operator"})
        self.assertEqual(announcement, [(None, None)] * 4 + [(True, 'operator')])

    def test_whole_route_answers_null_while_binotel_is_silent(self):
        # Весь путь ручки с настоящим сервисом: разговора телефон не услышал, исхода от АТС
        # ещё нет — в JSON уходит outcome_required: null, телефон ждёт долг из состояния.
        cur = _RoutedCursor([
            ('general_call_id, assignment_id, cancelled, disposition, ring_timeout_at', {'one': self.OPEN}),
            ('leg_answered_at, outcome_id', {'one': ('ended', '', False, None, 't', None, None, '', False, 0)}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db)
        svc.operator_context = _operator_ctx
        svc._client = lambda dep: _CallDetailsClient(db, {})
        resp = _phone_client(svc).post(
            '/api/operator/dial_list/attempts/att-1/phone_event', headers={'X-ICore-Phone-Version': GUARDED_PHONE},
            json={"event": "ended", "by_operator": False, "leg_sec": 14, "talk_detected": False})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"status": "success", "attempt_id": "att-1", "state": "ended",
                                           "final": False, "cancelled": False, "outcome_required": None})
        self.assertEqual([p for _, p in cur.sql_with(self.TALK)], [(False, 'att-1')])

    def test_only_a_boolean_in_ended_counts(self):
        # Незнакомое значение ключа и ключ у другого события — как его отсутствие.
        for junk in ('false', 'true', 0, 1, 'нет', [], {}):
            res, cur, _, _ = self._ended(self.OPEN, ('ended', '', False, None, 't', None, None, '', None, 0),
                                         talk_detected=junk)
            self.assertEqual(cur.sql_with(self.TALK), [], repr(junk))
            self.assertIs(res["outcome_required"], True, repr(junk))
        helper = RingTimeoutTests('test_schema_and_event')
        svc, cur, _, _ = helper._ended_svc(('leg_ringing', 'gc-1', 'asg-1', False, '', None),
                                           ('leg_answered', '', False, None, 't', None, None))
        svc.phone_event(7, 'att-1', 'answered', talk_detected=False, auto_hangup='announcement',
                        phone_version=GUARDED_PHONE)
        self.assertEqual(cur.sql_with(self.TALK), [])
        self.assertEqual(cur.sql_with(RingTimeoutTests.MARK), [])

    def test_false_is_not_written_over_an_attempt_binotel_closed_as_answered(self):
        # Binotel отвечает ANSWER и посреди разговора — попытка закрывается, пока плечо ещё
        # идёт, и длительность в ней может быть неполной (хоть 0). Судить по ней «разговора не
        # было» нельзя: такая попытка остаётся разговором, итог нужен — как у прежних телефонов.
        closed = ('finished', 'gc-1', 'asg-1', False, 'ANSWER', None)
        res, cur, calls, client = self._ended(closed, ('finished', 'ANSWER', False, None, 't', None, None, '', None, 0),
                                              talk_detected=False)
        self.assertEqual(cur.sql_with(self.TALK), [])
        self.assertEqual((res["final"], res["outcome_required"]), (True, True))
        self.assertEqual((calls, client.calls), ([], []))                          # и Binotel не спрашиваем
        # true пишется всегда: разговор он только подтверждает.
        res, cur, _, _ = self._ended(closed, ('finished', 'ANSWER', False, None, 't', None, None, '', True, 0),
                                     talk_detected=True)
        self.assertEqual([p for _, p in cur.sql_with(self.TALK)], [(True, 'att-1')])
        # Закрытую недозвоном (вебхук обогнал телефон) false закрывает без окна итога.
        res, cur, calls, _ = self._ended(('finished', 'gc-1', 'asg-1', False, 'NOANSWER', None),
                                         ('finished', 'NOANSWER', False, None, 't', None, None, '', False, 0),
                                         talk_detected=False)
        self.assertEqual([p for _, p in cur.sql_with(self.TALK)], [(False, 'att-1')])
        self.assertEqual((res["final"], res["outcome_required"], calls), (True, False, []))

    def test_unconfirmed_ring_timeout_mark_is_cleared(self):
        # Сервер на вопрос ring_timeout ответил «класть» и пометил попытку сбросом, а телефон
        # с защитой по речи плечо не положил — на линии была речь, разговор состоялся. В его
        # ended нет ни ring_timeout, ни auto_hangup: отметка — неправда, без неё это обычный
        # конец плеча, итог нужен сразу (иначе «Сохранить» получил бы 409 «сброшен на гудках»).
        marked = ('leg_answered', 'gc-1', 'asg-1', False, '', 't')
        res, cur, calls, _ = self._ended(marked, ('ended', '', False, None, 't', None, None, '', True, 0),
                                         by_operator=False, leg_sec=95, talk_detected=True)
        (sql, params), = cur.sql_with(self.CLEAR)
        self.assertEqual(params, ('att-1',))
        self.assertIn("auto_hangup = ''", sql)
        # Сообщение сети не снимается — его называет сам телефон.
        self.assertIn("WHERE id = %s AND ring_timeout_at IS NOT NULL AND auto_hangup <> 'announcement'", sql)
        self.assertIs(res["outcome_required"], True)
        self.assertEqual(cur.sql_with(RingTimeoutTests.MARK), [])
        # Телефон подтвердил сброс (ключ ring_timeout или auto_hangup) — отметку не снимаем.
        for event in ({"ring_timeout": True}, {"auto_hangup": 'ring_timeout'}, {"auto_hangup": 'announcement'}):
            res, cur, _, _ = self._ended(marked, ('ended', '', False, None, 't', None, 't', 'ring_timeout', False, 0),
                                         talk_detected=False, **event)
            self.assertEqual(cur.sql_with(self.CLEAR), [], event)
            self.assertEqual(len(cur.sql_with(RingTimeoutTests.MARK)), 1, event)
        # Телефон 3.22.33 (ключа talk_detected нет) — как было: отметку не трогаем.
        res, cur, _, _ = self._ended(marked, ('ended', '', False, None, 't', None, 't', '', None, 0),
                                     by_operator=False, leg_sec=30, phone_version=None)
        self.assertEqual(cur.sql_with(self.CLEAR), [])
        self.assertIsNone(res["outcome_required"])
        # Отметки и не было — снимать нечего.
        res, cur, _, _ = self._ended(self.OPEN, ('ended', '', False, None, 't', None, None, '', True, 0),
                                     talk_detected=True)
        self.assertEqual(cur.sql_with(self.CLEAR), [])

    def test_cleared_mark_lets_operator_hangup_rule_apply(self):
        # Отметку сняли, а трубку положил оператор: по закрытой недозвоном попытке работает
        # прежнее правило отбоя оператора (отмена). Не сняли — сброс на гудках сильнее отбоя.
        closed = ('finished', 'gc-1', 'asg-1', False, 'NOANSWER', 't')
        for cleared, expected in ((True, [('cancel', True)]), (False, [])):
            res, cur, calls, _ = self._ended(closed, ('finished', 'NOANSWER', True, 't', 't', None, None, '', False, 0),
                                             cleared=cleared, by_operator=True, talk_detected=False)
            self.assertEqual(len(cur.sql_with(self.CLEAR)), 1)
            self.assertEqual(calls, expected, cleared)
            self.assertEqual(len(cur.sql_with('operator_hangup_at = COALESCE')), 1)

    def test_operator_ending_a_heard_talk_gets_the_outcome_window_at_once(self):
        # Оператор сам закончил разговор, который телефон слышал (talk_detected true): окно
        # итога — сразу, исхода Binotel не ждём (он приходит через секунды, бывало — через
        # минуту, и всё это время телефон показывал «Ждём исход от АТС…»).
        res, cur, calls, client = self._ended(
            self.OPEN, ('ended', '', False, 't', 't', None, None, '', True, 0),
            by_operator=True, leg_sec=95, talk_detected=True)
        self.assertEqual([p for _, p in cur.sql_with(self.TALK)], [(True, 'att-1')])
        self.assertEqual(len(cur.sql_with('operator_hangup_at = COALESCE')), 1)    # отбой записан как был
        self.assertEqual((res["state"], res["final"], res["cancelled"], res["outcome_required"]),
                         ('ended', False, False, True))
        self.assertEqual((calls, client.calls), ([], ['gc-1']))                  # Binotel спросили, финала нет
        # Binotel уже закрыл попытку недозвоном (вебхук обогнал телефон): прежде это была
        # поздняя отмена «до ответа», теперь — нет (телефон разговор слышал), но и итог не нужен:
        # финальное «не дозвонились» Binotel сильнее «разговора» телефона (ревью 13.10.2026).
        res, cur, calls, _ = self._ended(('finished', 'gc-1', 'asg-1', False, 'NOANSWER', None),
                                         ('finished', 'NOANSWER', False, 't', 't', None, None, '', True, 0),
                                         by_operator=True, leg_sec=40, talk_detected=True)
        self.assertEqual(calls, [])
        self.assertEqual((res["final"], res["cancelled"], res["outcome_required"]), (True, False, False))
        # Тот же отбой без ключа (3.22.33) и с false — поздняя отмена, как раньше.
        for extra in ({}, {"talk_detected": False}):
            res, cur, calls, _ = self._ended(('finished', 'gc-1', 'asg-1', False, 'NOANSWER', None),
                                             ('finished', 'NOANSWER', True, 't', 't', None, None, '',
                                              extra.get("talk_detected"), 0),
                                             by_operator=True, leg_sec=40, **extra)
            self.assertEqual(calls, [('cancel', True)], extra)
            self.assertEqual((res["cancelled"], res["outcome_required"]), (True, False), extra)

    def test_answer_to_the_question_marks_only_a_leg_that_is_still_up(self):
        # Ответа Binotel вопрос ring_timeout ждёт секунды. Плечо за это время могло кончиться, и
        # телефон уже сообщил в ended, что было на самом деле (разговор — без ring_timeout):
        # запоздалая отметка сделала бы разговор «сбросом на гудках», а «Сохранить итог» получил
        # бы 409. Поэтому отметка по ответу «класть» ставится, только пока конца плеча не было.
        helper = RingTimeoutTests('test_schema_and_event')
        svc, cur, client, _ = helper._event_svc(
            ('leg_answered', 'gc-1', '', False), ('ended', '', False, None, 't', None, None),
            {'gc-1': {'general_call_id': 'gc-1', 'disposition': 'CALLING'}})
        res = helper._ring(svc, ring_sec=25)
        (sql, params), = cur.sql_with(helper.MARK)
        self.assertTrue(' '.join(sql.split()).endswith(
            "WHERE id = %s AND state <> 'failed' AND NOT cancelled AND phone_ended_at IS NULL"), sql)
        self.assertEqual(params, ('ring_timeout', 0, 0, 'att-1'))
        self.assertIs(res["hangup"], True)
        # Отметка из ended (телефон сам назвал причину) и по уже закрытой недозвоном попытке —
        # без этого условия.
        res, cur, _, _ = self._ended(self.OPEN, ('ended', '', False, None, 't', None, 't', 'ring_timeout', False, 0),
                                     ring_timeout=True, talk_detected=False)
        self.assertTrue(' '.join(cur.sql_with(helper.MARK)[0][0].split()).endswith('AND NOT cancelled'))
        svc, cur, _, _ = helper._event_svc(('finished', 'gc-1', 'NOANSWER', False),
                                           ('finished', 'NOANSWER', False, None, 't', None, 't'))
        helper._ring(svc)
        self.assertTrue(' '.join(cur.sql_with(helper.MARK)[0][0].split()).endswith('AND NOT cancelled'))

    # ── закрытие попытки ────────────────────────────────────────────────────
    def _finish(self, disposition, billsec, hung_up=None, ring_at=None, auto_hangup='', talk=None,
                assignment=('lead-1', 'issued')):
        cur = _RoutedCursor([
            ("SET state = 'finished'", {'one': ('asg-1',)}),
            ('SELECT operator_hangup_at, ring_timeout_at', {'one': (hung_up, ring_at, auto_hangup, talk)}),
            ('COALESCE(o.requeue, FALSE)', {'one': (False,)}),
            ('SELECT lead_id, state FROM dial_list_assignments', {'one': assignment}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        calls = []
        svc._cancel_attempt = lambda cur, aid, asg, late=False: calls.append(('cancel', late)) or True
        svc._close_assignment = (lambda cur, asg, lead, result, answered, count_attempt, requeue=False:
                                 calls.append(('close', result, answered, count_attempt)))
        svc._touch_lead = (lambda cur, lead, answered, count_attempt, requeue=False:
                           calls.append(('touch', answered, count_attempt)))
        svc._finish_attempt('att-1', disposition, billsec, 25, 'poll')
        return calls, cur

    def test_answer_without_talk_closes_the_row_as_no_answer(self):
        # Телефон разговора не услышал, а Binotel закрыл звонок ответом без единой секунды:
        # строка — «Не дозвонились», лид не «дозвонились», попытка в счёте.
        self.assertEqual(self._finish('ANSWER', 0, talk=False)[0], [('close', 'no_answer', False, True)])
        self.assertEqual(self._finish('ANSWER', 1, talk=False)[0], [('close', 'answered', True, True)])
        self.assertEqual(self._finish('ANSWER', 0, talk=True)[0], [('close', 'answered', True, True)])
        self.assertEqual(self._finish('ANSWER', 0, talk=None)[0], [('close', 'answered', True, True)])   # 3.22.33
        self.assertEqual(self._finish('BUSY', 0, talk=False)[0], [('close', 'busy', False, True)])
        self.assertEqual(self._finish('ANSWER', 0, talk=False, assignment=('lead-1', 'done'))[0],
                         [('touch', False, True)])
        # Отбой оператора: ответ без разговора — тоже «водитель не ответил», попытка отменяется.
        self.assertEqual(self._finish('ANSWER', 0, hung_up='t', talk=False)[0], [('cancel', False)])
        self.assertEqual(self._finish('ANSWER', 5, hung_up='t', talk=False)[0], [('close', 'answered', True, True)])
        self.assertEqual(self._finish('NOANSWER', 0, hung_up='t', talk=None)[0], [('cancel', False)])     # 3.22.33
        # Разговор, который слышал телефон, отбой оператора отменой не делает: трубку он положил
        # после разговора, итог уже потребован. Попытка в счёте с исходом Binotel.
        self.assertEqual(self._finish('NOANSWER', 0, hung_up='t', talk=True)[0], [('close', 'no_answer', False, True)])
        self.assertEqual(self._finish('BUSY', 0, hung_up='t', talk=True)[0], [('close', 'busy', False, True)])
        self.assertEqual(self._finish('ANSWER', 0, hung_up='t', talk=True)[0], [('close', 'answered', True, True)])
        # Что сообщил телефон, читается после своего UPDATE — под замком строки попытки.
        _, cur = self._finish('ANSWER', 0, talk=False)
        sqls = [sql for sql, _ in cur.executed]
        self.assertLess(next(i for i, s in enumerate(sqls) if "SET state = 'finished'" in s),
                        next(i for i, s in enumerate(sqls) if 'auto_hangup, talk_detected' in s))

    def test_late_final_over_provisional_one_obeys_the_same_rule(self):
        # Попытку закрыли без настоящего исхода (таймаут), потом пришёл ANSWER: дозвон лиду —
        # только если это разговор.
        def late(flags, billsec):
            cur = _RoutedCursor([
                ("SET state = 'finished'", {'one': None}),
                ("WHERE id = %s AND state = 'finished' AND UPPER(disposition) IN %s", {'one': ('asg-1',)}),
                ('SELECT cancelled, auto_hangup, talk_detected', {'one': flags}),
            ])
            svc = dial_service.DialListService(db=_routed_db(cur))
            uncancelled = []
            svc._uncancel_attempt = lambda cur, aid, asg: uncancelled.append(aid) or True
            self.assertTrue(svc._finish_attempt('att-1', 'ANSWER', billsec, 3, 'webhook'))
            return len(cur.sql_with('UPDATE dial_list_leads l')), uncancelled

        self.assertEqual(late((False, '', None), 30), (1, []))               # разговор — дозвон лиду
        self.assertEqual(late((False, '', False), 30), (1, []))
        self.assertEqual(late((False, '', False), 0), (0, []))               # ответ без разговора
        self.assertEqual(late((False, 'announcement', None), 30), (0, []))   # сообщение сети
        self.assertEqual(late((True, '', None), 30), (0, ['att-1']))         # отменённая — снова в счёт
        self.assertEqual(late((True, 'announcement', None), 30), (0, []))    # …но не сообщение сети

    # ── долг по итогу и сам итог ────────────────────────────────────────────
    def test_pending_outcome_waits_for_binotel_when_phone_heard_no_talk(self):
        cur = _RoutedCursor([])
        dial_service.DialListService(db=None)._pending_outcome(cur, 7)
        sql, params = cur.executed[0]
        flat = ' '.join(sql.split())
        # Сообщение сети и ответ без разговора долга не дают ни в одной ветке.
        self.assertIn("WHERE t.operator_id = %s AND NOT t.cancelled AND " + dial_service.ATTEMPT_TALK_SQL, flat)
        # Конец плеча без сброса на гудках — долг сразу, если телефон слышал разговор (кто бы ни
        # положил трубку) или ничего не сообщил и трубку положил не оператор (как раньше).
        # …кроме разговора, который телефон будто бы слышал, а Binotel закрыл финальным «не
        # дозвонились» (ревью 13.10.2026).
        self.assertIn("((t.ring_timeout_at IS NULL AND t.state IN ('ended', 'finished') "
                      "AND ((t.talk_detected IS TRUE AND NOT (t.state = 'finished' AND UPPER(t.disposition) IN %s)) "
                      "OR (t.operator_hangup_at IS NULL AND t.talk_detected IS NULL)))",
                      flat)
        self.assertEqual(params.count(dial_service.NOT_ANSWERED_FINAL_SQL), 1)
        # Сказал «разговора не было» — только после финального ответа Binotel, как отбой оператора
        # и сброс на гудках.
        self.assertIn("OR (t.talk_detected IS FALSE AND t.state = 'finished' AND UPPER(t.disposition) IN %s)", flat)
        self.assertIn("(t.operator_hangup_at IS NOT NULL AND t.state = 'finished' AND UPPER(t.disposition) IN %s)", flat)
        self.assertIn("(t.ring_timeout_at IS NOT NULL AND t.state = 'finished' AND UPPER(t.disposition) IN %s)", flat)
        self.assertEqual(params.count(dial_service.ANSWERED_SQL), 4)
        self.assertEqual(sql.count('%s'), len(params))

    def _save_outcome(self, row):
        """Попытка сохранить итог по попытке row: название итога либо (текст отказа, статус)."""
        cur = _RoutedCursor([
            ('FOR UPDATE OF t', {'one': row}),
            ('FROM dial_list_outcomes', {'one': ('o-1', 'Отказ', '#FF3B30', False)}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        svc.operator_context = lambda uid: {"user_id": uid, "department_id": 1}
        try:
            return svc.set_attempt_outcome(7, 'att-1', 'o-1')["outcome"]["name"], cur
        except dial_service.DialListError as exc:
            self.assertEqual(cur.sql_with('UPDATE dial_list_attempts'), [])
            return (str(exc), exc.status), cur

    def test_outcome_is_taken_only_for_a_talk_binotel_confirmed(self):
        # (state, lead_id, outcome_id, cancelled, operator_hangup_at, disposition, ring_timeout_at,
        #  auto_hangup, talk_detected, billsec)
        self.assertEqual(self._save_outcome(('ended', 'lead-1', None, False, None, '', None, '', False, 0))[0],
                         ("Разговор не подтверждён: итог нужен, только если водитель ответил. "
                          "Дождитесь исхода от АТС", 409))
        for disposition, billsec in (('BUSY', 0), ('NOANSWER', 0), ('ANSWER', 0)):
            self.assertEqual(
                self._save_outcome(('finished', 'lead-1', None, False, None, disposition, None, '', False, billsec))[0],
                ("Водитель не ответил — итог не нужен", 409), disposition)
        self.assertEqual(self._save_outcome(('finished', 'lead-1', None, False, None, 'ANSWER', None, '', False, 3))[0],
                         'Отказ')
        # talk_detected true и «ключа нет» — как раньше: итог принимается сразу и при любом исходе.
        for talk in (True, None):
            for state, disposition in (('ended', ''), ('finished', 'BUSY'), ('finished', 'ANSWER')):
                self.assertEqual(
                    self._save_outcome((state, 'lead-1', None, False, None, disposition, None, '', talk, 0))[0],
                    'Отказ', (talk, state, disposition))
        # Отбой оператора после разговора, который слышал телефон, — итог принимается сразу, не
        # дожидаясь Binotel и при любом его исходе (окно итога телефон уже открыл).
        for state, disposition in (('ended', ''), ('finished', 'NOANSWER'), ('finished', 'ANSWER')):
            self.assertEqual(
                self._save_outcome((state, 'lead-1', None, False, 't', disposition, None, '', True, 0))[0],
                'Отказ', (state, disposition))
        # «Ключа нет» после отбоя оператора — как раньше: ждём Binotel.
        self.assertEqual(self._save_outcome(('ended', 'lead-1', None, False, 't', '', None, '', None, 0))[0],
                         ("Вы завершили звонок сами: итог можно указать только по состоявшемуся разговору. "
                          "Дождитесь исхода от АТС", 409))
        # Сброс на гудках сильнее: плечо положил сам телефон, без ответа Binotel итога нет.
        self.assertEqual(self._save_outcome(('finished', 'lead-1', None, False, None, 'NOANSWER', 't', 'ring_timeout',
                                             True, 0))[0], ("Водитель не взял трубку — итог не нужен", 409))


class AnnouncementTests(unittest.TestCase):
    """Сообщение сети «абонент недоступен» (владелец, 12.10.2026): телефон с 3.22.34 слышит его
    и кладёт плечо сам — ended с auto_hangup: announcement. Это всегда недозвон, что бы ни
    ответил Binotel (автоответчик отвечает ANSWER с длительностью): окна итога нет, строка —
    «Не дозвонились», в «дозвонились», минуты разговора и успешки попытка не идёт."""

    MARK = RingTimeoutTests.MARK
    OPEN = ('leg_answered', 'gc-1', 'asg-1', False, '', None)

    def _ended(self, row, decision, answer=None, **event):
        return PhoneHeardTests('test_auto_hangup_values')._ended(row, decision, answer, **event)

    def test_schema_columns_come_before_indexes(self):
        ddl = dial_schema.DDL
        ring_at = next(i for i, s in enumerate(ddl) if 'ADD COLUMN IF NOT EXISTS ring_timeout_at' in s)
        auto_at = next(i for i, s in enumerate(ddl) if 'ADD COLUMN IF NOT EXISTS auto_hangup' in s)
        talk_at = next(i for i, s in enumerate(ddl) if 'ADD COLUMN IF NOT EXISTS talk_detected' in s)
        first_index = next(i for i, s in enumerate(ddl) if 'CREATE INDEX' in s or 'CREATE UNIQUE INDEX' in s)
        # Сразу за колонкой сброса на гудках и до первого индекса.
        self.assertEqual((auto_at, talk_at), (ring_at + 1, ring_at + 2))
        self.assertLess(talk_at, first_index)
        self.assertEqual(ddl[auto_at], "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS auto_hangup "
                                       "VARCHAR(16) NOT NULL DEFAULT ''")
        # NULL — «телефон не сообщил»: у него свой смысл, значения по умолчанию нет.
        self.assertEqual(ddl[talk_at], "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS talk_detected BOOLEAN")
        self.assertLessEqual(max(len(v) for v in dial_service.AUTO_HANGUPS), 16)

    def test_announcement_is_marked_and_needs_no_outcome_at_once(self):
        # Исхода от АТС ещё нет, а телефону уже «итог не нужен»: ждать ему нечего.
        res, cur, calls, _ = self._ended(
            self.OPEN, ('ended', '', False, None, 't', None, 't', 'announcement', False, 0),
            by_operator=True, leg_sec=9, ring_timeout=True, auto_hangup='announcement', talk_detected=False)
        self.assertEqual([p for _, p in cur.sql_with(self.MARK)], [('announcement', 9, 9, 'att-1')])
        self.assertEqual(cur.sql_with('operator_hangup_at = COALESCE'), [])        # by_operator не учитывается
        self.assertEqual((res["state"], res["final"], res["cancelled"], res["outcome_required"]),
                         ('ended', False, False, False))
        self.assertEqual(calls, [])
        # Ключа ring_timeout нет — auto_hangup сам говорит, что плечо положил телефон.
        res, cur, calls, _ = self._ended(
            self.OPEN, ('ended', '', False, None, 't', None, 't', 'announcement', None, 0),
            by_operator=True, leg_sec=7, auto_hangup='announcement')
        self.assertEqual([p for _, p in cur.sql_with(self.MARK)], [('announcement', 7, 7, 'att-1')])
        self.assertEqual(cur.sql_with('operator_hangup_at = COALESCE'), [])
        self.assertIs(res["outcome_required"], False)

    def test_unknown_auto_hangup_is_no_auto_hangup(self):
        # Незнакомое слово — телефон плечо сам не клал: отбой оператора остаётся отбоем.
        res, cur, calls, _ = self._ended(
            self.OPEN, ('ended', '', False, 't', 't', None, None, '', None, 0),
            by_operator=True, leg_sec=5, auto_hangup='voicemail')
        self.assertEqual(cur.sql_with(self.MARK), [])
        self.assertEqual(len(cur.sql_with('operator_hangup_at = COALESCE')), 1)
        self.assertIsNone(res["outcome_required"])
        # auto_hangup: ring_timeout — то же, что прежний ключ ring_timeout.
        res, cur, _, _ = self._ended(self.OPEN, ('ended', '', False, None, 't', None, 't', 'ring_timeout', False, 0),
                                     leg_sec=26, auto_hangup='ring_timeout', talk_detected=False)
        self.assertEqual([p for _, p in cur.sql_with(self.MARK)], [('ring_timeout', 26, 26, 'att-1')])

    def test_announcement_outranks_ring_timeout(self):
        # Поздний сигнал о гудках причину «сообщение сети» не затирает; обратное — затирает.
        cur = _RoutedCursor([])
        svc = dial_service.DialListService(db=None)
        svc._mark_ring_timeout(cur, 'att-1')
        svc._mark_ring_timeout(cur, 'att-1', 12, 'announcement')
        svc._mark_ring_timeout(cur, 'att-1', 0, 'что-то')
        (sql, first), (_, second), (_, third) = cur.executed
        self.assertIn("auto_hangup = CASE WHEN auto_hangup = 'announcement' THEN auto_hangup ELSE %s END", sql)
        self.assertIn(self.MARK, sql)
        self.assertIn("WHERE id = %s AND state <> 'failed' AND NOT cancelled", sql)
        self.assertEqual((first, second, third), (('ring_timeout', 0, 0, 'att-1'), ('announcement', 12, 12, 'att-1'),
                                                  ('ring_timeout', 0, 0, 'att-1')))

    def test_decision_is_never_an_outcome(self):
        decide = PhoneHeardTests('test_auto_hangup_values')._decide
        for state, disposition, billsec in (('ended', '', 0), ('leg_answered', '', 0), ('finished', 'BUSY', 0),
                                            ('finished', 'ANSWER', 0), ('finished', 'ANSWER', 45),
                                            ('finished', 'VM-SUCCESS', 12)):
            for talk in (None, True, False):
                for hung_up in (None, 't'):
                    self.assertEqual(decide(state, disposition, ring_at='t', auto_hangup='announcement', talk=talk,
                                            billsec=billsec, hung_up=hung_up), (False, False),
                                     (state, disposition, billsec, talk, hung_up))
        # Плечо без ACK и сообщение сети — тоже не итог.
        self.assertEqual(decide('finished', 'ANSWER', leg_answered=None, ring_at='t', auto_hangup='announcement',
                                billsec=20), (False, False))

    def test_answer_from_binotel_still_closes_as_no_answer(self):
        finish = PhoneHeardTests('test_auto_hangup_values')._finish
        # Событие пришло раньше исхода: ANSWER с длительностью (автоответчик) — недозвон.
        self.assertEqual(finish('ANSWER', 12, ring_at='t', auto_hangup='announcement', talk=False)[0],
                         [('close', 'no_answer', False, True)])
        self.assertEqual(finish('ANSWER', 12, ring_at='t', auto_hangup='announcement')[0],
                         [('close', 'no_answer', False, True)])
        # Как на проде 12.10.2026: Binotel отвечает BUSY с нулём — «занято».
        self.assertEqual(finish('BUSY', 0, ring_at='t', auto_hangup='announcement', talk=False)[0],
                         [('close', 'busy', False, True)])
        # Отбой оператора, записанный каким-то путём, отменой такую попытку не делает.
        self.assertEqual(finish('ANSWER', 12, hung_up='t', ring_at='t', auto_hangup='announcement')[0],
                         [('close', 'no_answer', False, True)])
        # Строку уже закрыла прошлая попытка — лиду недозвон.
        self.assertEqual(finish('ANSWER', 12, ring_at='t', auto_hangup='announcement',
                                assignment=('lead-1', 'done'))[0], [('touch', False, True)])

    def test_answer_that_came_first_is_taken_back(self):
        # Обратный порядок: ANSWER (вебхук, опрос посреди звонка) закрыл попытку дозвоном
        # раньше, чем телефон сообщил про сообщение сети. Дозвон снимается в той же транзакции.
        closed = ('finished', 'gc-1', 'asg-1', False, 'ANSWER', None)
        res, cur, calls, client = self._ended(
            closed, ('finished', 'ANSWER', False, None, 't', None, 't', 'announcement', None, 15),
            leg_sec=8, ring_timeout=True, auto_hangup='announcement', talk_detected=False)
        self.assertEqual(calls, [('unanswer', 'att-1', 'asg-1')])
        self.assertEqual((res["final"], res["outcome_required"], client.calls), (True, False, []))
        # Причина записана и по закрытой попытке; «разговора не было» (talk_detected false)
        # по закрытой ответом не пишется — недозвоном её делает причина, а не длительность.
        self.assertEqual([p for _, p in cur.sql_with(self.MARK)], [('announcement', 8, 8, 'att-1')])
        self.assertEqual(cur.sql_with(PhoneHeardTests.TALK), [])
        # Попытку закрыл недозвон, она отменена или телефон клал плечо на гудках — снимать нечего.
        for row, event in ((('finished', 'gc-1', 'asg-1', False, 'BUSY', None), {"auto_hangup": 'announcement'}),
                           (('finished', 'gc-1', 'asg-1', True, 'ANSWER', None), {"auto_hangup": 'announcement'}),
                           (closed, {"ring_timeout": True}),
                           (self.OPEN, {"auto_hangup": 'announcement'})):
            _, _, calls, _ = self._ended(row, ('finished', 'BUSY', False, None, 't', None, 't', '', None, 0), **event)
            self.assertEqual([c for c in calls if c[0] == 'unanswer'], [], (row, event))

    def _unanswer(self, assignment):
        cur = _RoutedCursor([('SELECT a.state, a.result, a.lead_id', {'one': assignment})])
        svc = dial_service.DialListService(db=None)
        with contextlib.ExitStack() as stack:
            if assignment is not None:
                stack.enter_context(self.assertLogs('dial_list.service', level='INFO'))
            done = svc._unanswer_attempt(cur, 'att-1', 'asg-1')
        return done, cur

    def test_taking_the_answer_back(self):
        # (state, result, lead_id, последняя попытка строки)
        done, cur = self._unanswer(('done', 'answered', 'lead-1', 'att-1'))
        self.assertTrue(done)
        self.assertEqual(cur.sql_with("SET result = 'no_answer'")[0][1], ('asg-1',))
        (sql, params), = cur.sql_with('UPDATE dial_list_leads l')
        flat = ' '.join(sql.split())
        # Время дозвона — по остальным разговорам лида (нет их — пусто)…
        self.assertIn("SET answered_at = other.answered_at", flat)
        self.assertIn("FROM (SELECT MIN(t.finished_at) AS answered_at", flat)
        self.assertIn("WHERE ta.lead_id = %s AND t.id <> %s AND t.state = 'finished' AND NOT t.cancelled "
                      "AND UPPER(t.disposition) IN %s AND " + dial_service.ATTEMPT_TALK_SQL, flat)
        # …лид снова в работе, только если других разговоров не было и попытки не кончились
        # (попытка остаётся в счёте — это недозвон, а не отмена)…
        self.assertIn("status = CASE WHEN l.status = 'done' AND other.answered_at IS NULL "
                      "AND l.attempts_total < COALESCE(", flat)
        self.assertNotIn('attempts_total =', flat)
        # …а успешка, засчитанная этой попытке, снимается: её решит следующая проверка подписания.
        for column in ('success_attempt_id', 'success_operator_id', 'success_resolved_at'):
            self.assertIn(f"{column} = CASE WHEN l.success_attempt_id = %s::uuid THEN NULL", flat)
        self.assertEqual(params, (dial_service.DEFAULT_MAX_ATTEMPTS, 'att-1', 'att-1', 'att-1', 'lead-1', 'att-1',
                                  dial_service.ANSWERED_SQL, 'lead-1'))
        self.assertEqual(sql.count('%s'), len(params))
        # Строку закрыла другая попытка либо не дозвон — её результат не трогаем, лид правим.
        for assignment in (('done', 'answered', 'lead-1', 'att-2'), ('done', 'other', 'lead-1', 'att-1'),
                           ('issued', '', 'lead-1', 'att-1')):
            done, cur = self._unanswer(assignment)
            self.assertEqual(cur.sql_with("SET result = 'no_answer'"), [], assignment)
            self.assertEqual(len(cur.sql_with('UPDATE dial_list_leads l')), 1, assignment)
        # Строки нет — ничего не делаем.
        done, cur = self._unanswer(None)
        self.assertFalse(done)
        self.assertEqual(_writes(sql for sql, _ in cur.executed), [])

    def test_outcome_is_refused(self):
        outcome = PhoneHeardTests('test_auto_hangup_values')._save_outcome
        for state, disposition, billsec, ring_at in (('ended', '', 0, 't'), ('finished', 'BUSY', 0, 't'),
                                                     ('finished', 'ANSWER', 30, 't'), ('finished', 'ANSWER', 30, None)):
            self.assertEqual(
                outcome((state, 'lead-1', None, False, None, disposition, ring_at, 'announcement', None, billsec))[0],
                ("Абонент недоступен — итог не нужен", 409), (state, disposition))
        # Отменённая попытка — прежний отказ, он первым.
        self.assertEqual(outcome(('finished', 'lead-1', None, True, 't', 'CANCEL', None, 'announcement', None, 0))[0],
                         ("Звонок завершён до ответа водителя — итог не ставится", 409))

    def test_no_outcome_debt(self):
        # Долг по итогу для сообщения сети не возникает ни в одной ветке: условие — на весь запрос.
        cur = _RoutedCursor([])
        dial_service.DialListService(db=None)._pending_outcome(cur, 7)
        flat = ' '.join(cur.executed[0][0].split())
        self.assertIn("AND NOT t.cancelled AND t.auto_hangup <> 'announcement' AND (", flat)
        self.assertLess(flat.index("t.auto_hangup <> 'announcement'"), flat.index('t.outcome_id IS NULL'))

    def test_phone_sees_the_reason_in_queue_and_tabs(self):
        # Строка пачки: last_attempt.auto_hangup; вкладки итогов: items[].auto_hangup — по
        # нему телефон подпишет «Абонент недоступен».
        row = ('asg-1', 1, 'Иванов', 'done', 'busy', 1, None, 'att-1', 'finished', 'BUSY', None, 'gc', None, None,
               False, None, 'lead-1', None, 'announcement', None, None, None)
        cur = _RoutedCursor([('WHERE a.portion_id = %s', {'all': [row, row[:18] + ('ring_timeout', None, None, None),
                                                                 row[:18] + (None, None, None, None)]})])
        items = dial_service.DialListService(db=None)._portion_items(cur, 'portion-1')
        self.assertEqual([i["last_attempt"]["auto_hangup"] for i in items], ['announcement', 'ring_timeout', ''])
        sql = cur.executed[0][0]
        self.assertIn('os.name, t.auto_hangup, ', sql)
        self.assertIn('cancelled, operator_hangup_at, outcome_subtype_id, auto_hangup, api_error\n', sql)
        from datetime import datetime as dt, timezone as tz
        at = dt(2026, 10, 12, 9, tzinfo=tz.utc)
        worked = WorkedTabsTests('test_row_lands_on_exactly_one_place')
        rows = [worked._row('a1', 'l1', 'Недоступный', 'done', 'busy', at, at, None, None, '', 'finished', None, None,
                            None, 'in_progress', None, auto_hangup='announcement'),
                worked._row('a2', 'l2', 'Не взял трубку', 'done', 'no_answer', at, at, None, None, '', 'finished', None,
                            None, None, 'in_progress', None, auto_hangup='ring_timeout'),
                worked._row('a3', 'l3', 'Обычный', 'done', 'no_answer', at, at, None, None, '', 'finished', None, None,
                            None, 'in_progress', None)]
        res = worked._worked(rows, [RingTimeoutTests.OUTCOME])
        self.assertEqual({i['full_name']: (i['tab'], i['auto_hangup']) for i in res['items']},
                         {'Недоступный': ('no_answer', 'announcement'), 'Не взял трубку': ('no_answer', 'ring_timeout'),
                          'Обычный': ('no_answer', '')})
        worked_sql = dial_service.DialListService._WORKED_SQL
        self.assertIn('SELECT outcome_id, outcome_at, operator_comment, state, outcome_subtype_id, auto_hangup\n',
                      worked_sql)

    def test_manager_journal_shows_the_reason_and_no_answer(self):
        r = [None] * dial_service.DialListService._JOURNAL_COLUMN_COUNT
        r[0], r[1], r[2], r[3], r[4], r[5], r[10], r[29] = 'lead-1', 1, 'Иванов', '77011234567', 'in_progress', 1, 1, 'queue'
        # Последний звонок — сообщение сети с ANSWER 12 с от Binotel (автоответчик).
        r[22], r[23], r[24], r[25], r[48], r[49] = datetime(2026, 10, 12, 9, tzinfo=timezone.utc), 'finished', 'ANSWER', 12, \
            'announcement', False

        def attempt(disposition, billsec, ring_at, auto_hangup, talk=None, cancelled=False):
            return ('att', None, 3, 'Оператор', '913', 'finished', disposition, billsec, 25, '', 'poll', None, None,
                    None, True, 'asg', 'no_answer', 'done', None, None, None, None, '', None, cancelled, None, 9,
                    None, None, ring_at, auto_hangup, talk)

        cur = _RoutedCursor([
            ('l.id = %(lead_id)s', {'one': tuple(r) + (1,)}),
            ('os.id, os.name', {'all': [attempt('ANSWER', 12, 't', 'announcement', False),
                                        attempt('BUSY', 0, 't', 'announcement', False),
                                        attempt('CANCEL', 0, 't', 'ring_timeout', False),
                                        attempt('ANSWER', 0, None, '', False),
                                        attempt('ANSWER', 40, None, '', True)]}),
        ])
        svc = dial_service.DialListService(db=_routed_db(cur))
        svc.lead_department = lambda lid: 1
        svc.department_settings = lambda d: {"max_attempts": 3, "retry_after_hours": 24}
        card = svc.lead_card('lead-1')
        self.assertEqual((card['last_call']['result'], card['last_call']['auto_hangup'], card['last_call']['billsec']),
                         ('no_answer', 'announcement', 12))
        shown = [(a['result'], a['auto_hangup'], a['ring_timeout'], a['recording_available']) for a in card['attempts']]
        self.assertEqual(shown, [
            # Сообщение сети: недозвон; метки сброса на гудках нет (старая сборка сайта написала
            # бы «водитель не взял трубку»); запись осталась — по ней разбирают ложные срабатывания.
            ('no_answer', 'announcement', False, True),
            ('busy', 'announcement', False, False),
            ('no_answer', 'ring_timeout', True, False),
            ('no_answer', '', False, False),          # ответ без разговора: телефон его не услышал
            ('answered', '', False, True),
        ])
        attempts_sql = cur.sql_with('os.id, os.name')[0][0]
        # За ними — только колонки автообзвона Тез (15.10.2026): прежние номера не сдвинуты.
        self.assertIn('os.id, os.name, t.ring_timeout_at, t.auto_hangup, t.talk_detected,\n'
                      "                       t.due_at, t.auto, t.operator_status, t.wrapup_expired_at, "
                      "COALESCE(o.due_kind, '')\n",
                      attempts_sql)
        # Ручка записи судит так же, как recording_available: по ответу Binotel с длительностью.
        recording = inspect.getsource(dial_service.DialListService.attempt_recording)
        self.assertIn('map_disposition(disposition) != "answered"', recording)
        self.assertNotIn('auto_hangup', recording)
        # Журнал списком — те же колонки последнего звонка, в самом конце строки.
        base = dial_service.DialListService._JOURNAL_BASE_SQL
        self.assertIn('lt.auto_hangup AS last_auto_hangup, lt.talk_detected AS last_talk_detected', base)
        self.assertIn('t.auto_hangup, t.talk_detected\n', base)

    def test_live_card_of_a_finished_attempt_follows_the_same_rule(self):
        # /live по закрытой попытке: исход — как у строки выдачи, а не голый ответ Binotel.
        def live(row):
            cur = _RoutedCursor([('FROM dial_list_attempts', {'one': row})])
            svc = dial_service.DialListService(db=_routed_db(cur))
            svc.operator_context = lambda uid: {"user_id": uid, "department_id": 1954}
            res = svc.attempt_live(7, 'att-1')
            self.assertEqual((res["final"], res["talking"]), (True, False))
            sql = ' '.join(cur.executed[0][0].split())
            self.assertIn('SELECT state, general_call_id, disposition, cancelled, billsec, auto_hangup, talk_detected',
                          sql)
            return res["result"]

        # (state, general_call_id, disposition, cancelled, billsec, auto_hangup, talk_detected)
        self.assertEqual(live(('finished', 'gc-1', 'ANSWER', False, 15, 'announcement', False)), 'no_answer')
        self.assertEqual(live(('finished', 'gc-1', 'ANSWER', False, 0, '', False)), 'no_answer')
        self.assertEqual(live(('finished', 'gc-1', 'ANSWER', False, 0, '', None)), 'answered')      # 3.22.33
        self.assertEqual(live(('finished', 'gc-1', 'ANSWER', False, 30, '', False)), 'answered')
        self.assertEqual(live(('finished', 'gc-1', 'BUSY', False, 0, 'announcement', False)), 'busy')
        self.assertEqual(live(('finished', 'gc-1', 'UNKNOWN', False, 0, '', None)), 'other')
        self.assertEqual(live(('failed', None, '', False, 0, '', None)), 'failed')

    def test_counters_never_take_it_for_an_answer(self):
        svc = dial_service.DialListService
        talk = dial_service.ATTEMPT_TALK_SQL
        # «Мой прогресс»: дозвоны и секунды разговора — только по разговорам.
        progress = ' '.join(svc._PROGRESS_SQL.split())
        self.assertIn(f"({talk}) AS talk FROM dial_list_attempts t", progress)
        self.assertIn("WHERE state = 'finished' AND disp IN %(answered)s AND talk) AS answered,", progress)
        self.assertIn("AND talk AND day = %(today)s) AS answered_today,", progress)
        self.assertIn("FROM att WHERE state = 'finished' AND talk) AS talk_sec,", progress)
        self.assertIn("FROM att WHERE state = 'finished' AND talk AND day = %(today)s) AS talk_sec_today,", progress)
        # Попыткой сообщение сети остаётся: оператор звонил.
        self.assertIn("(SELECT COUNT(*) FROM att WHERE NOT cancelled) AS attempts,", progress)
        # Конверсия и сводка за день.
        self.assertIn("AND t.auto_hangup <> 'announcement' AND t.billsec >= %(min_sec)s",
                      ' '.join(svc._PROGRESS_CONVERSION_SQL.split()))
        overview = ' '.join(inspect.getsource(svc.overview).split())
        self.assertIn("IN ('ANSWER','ANSWERED','SUCCESS','VM-SUCCESS') AND {ATTEMPT_TALK_SQL}) AS answered", overview)
        self.assertIn("FILTER (WHERE t.state = 'finished' AND {ATTEMPT_TALK_SQL}), 0) AS talk_sec", overview)
        self.assertNotIn('%', talk)                       # вставляется в запросы с параметрами как есть


class LastCommentTests(unittest.TestCase):
    """Комментарии оператора (владелец, 12.10.2026): оператор видит, что записал по водителю в
    прошлый раз, — в очереди, на вкладках итогов, в карточке звонка. last_comment — последний
    непустой комментарий ЭТОГО оператора по водителю, из любой его строки и за любое время;
    last_comment_label — когда оставлен; last_comment_outcome — итог той попытки."""

    LEAD = '99999999-9999-9999-9999-999999999999'
    TODAY = date(2026, 10, 12)
    AT = datetime(2026, 10, 12, 10, 14, tzinfo=timezone.utc)          # 15:14 по Алматы
    YESTERDAY_AT = datetime(2026, 10, 11, 4, 12, tzinfo=timezone.utc)  # вчера, 09:12 по Алматы

    def setUp(self):
        # «Сегодня» сервиса закрепляем: подписи времени не зависят от дня запуска теста.
        patcher = unittest.mock.patch.object(dial_service, '_today', return_value=self.TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fields_and_time_label(self):
        fields = dial_service.DialListService._last_comment_fields
        self.assertEqual(fields('просил после 18:00', self.AT, 'Перезвонить позже', self.TODAY),
                         {"last_comment": 'просил после 18:00', "last_comment_label": 'сегодня, 15:14',
                          "last_comment_outcome": 'Перезвонить позже'})
        yesterday = datetime(2026, 10, 11, 4, 12, tzinfo=timezone.utc)
        self.assertEqual(fields('вчерашний', yesterday, None, self.TODAY),
                         {"last_comment": 'вчерашний', "last_comment_label": 'вчера, 09:12', "last_comment_outcome": ''})
        self.assertEqual(fields('давний', datetime(2026, 9, 30, 9, 5, tzinfo=timezone.utc), 'Отказ', self.TODAY)
                         ["last_comment_label"], '30.09, 14:05')
        # Комментария нет — все три поля пусты, даже если время и итог почему-то пришли.
        empty = {"last_comment": '', "last_comment_label": '', "last_comment_outcome": ''}
        self.assertEqual(fields(None, None, None, self.TODAY), empty)
        self.assertEqual(fields('', self.AT, 'Отказ', self.TODAY), empty)
        # Время не пришло — подписи нет, текст есть.
        self.assertEqual(fields('без времени', None, 'Отказ', self.TODAY)["last_comment_label"], '')

    def test_sql_takes_only_this_operators_latest_nonempty_comment(self):
        svc = dial_service.DialListService
        join = ' '.join(svc._LAST_COMMENT_JOIN_SQL.split())
        self.assertIn("SELECT ct.operator_comment AS last_comment, "
                      "COALESCE(ct.outcome_at, ct.requested_at) AS last_comment_at, co.name AS last_comment_outcome", join)
        # Любая строка этого водителя у этого оператора, любое время; пустые не считаются.
        self.assertIn("WHERE ca.lead_id = {lead} AND ct.operator_id = {operator} AND ct.operator_comment <> ''", join)
        self.assertIn("ORDER BY COALESCE(ct.outcome_at, ct.requested_at) DESC LIMIT 1", join)
        self.assertNotIn('portion', join)
        self.assertNotIn('created_at', join)
        # Один кусок — во всех трёх запросах; водитель и оператор — выражения вызывающего.
        portion_sql = inspect.getsource(svc._portion_items)
        self.assertIn('self._LAST_COMMENT_JOIN_SQL.format(lead="a.lead_id", operator="a.operator_id")', portion_sql)
        self.assertIn(svc._LAST_COMMENT_JOIN_SQL.format(lead="s.lead_id", operator="%(operator_id)s"), svc._WORKED_SQL)
        self.assertIn(svc._LAST_COMMENT_JOIN_SQL.format(lead="%(lead_id)s", operator="%(operator_id)s"),
                      svc._LAST_COMMENT_SQL)
        self.assertTrue(svc._LAST_COMMENT_SQL.startswith('SELECT ' + svc._LAST_COMMENT_COLUMNS + ' FROM (SELECT 1) one'))
        for sql in (svc._LAST_COMMENT_JOIN_SQL, svc._LAST_COMMENT_SQL):
            for forbidden in ('phone_norm', 'iin', 'full_name', 'dial_list_leads'):
                self.assertNotIn(forbidden, sql)

    def test_portion_rows_carry_it_after_all_old_columns(self):
        # Новые колонки — только в конец: прежние номера r[0]…r[17] не сдвинуты.
        with_comment = ('asg-1', 1, 'Иванов', 'issued', '', 0, None, None, None, None, None, None, None, None, None,
                        None, 'lead-1', None, None, 'просил после 18:00', self.AT, 'Перезвонить позже')
        without = ('asg-2', 2, 'Петров', 'done', 'answered', 1, None, 'att-2', 'finished', 'ANSWER', None, 'gc',
                   'Отказ', '#FF3B30', False, None, 'lead-2', 'Дорого', '', None, None, None)
        cur = _RoutedCursor([('WHERE a.portion_id = %s', {'all': [with_comment, without]})])
        first, second = dial_service.DialListService(db=None)._portion_items(cur, 'portion-1', self.TODAY)
        self.assertEqual((first["last_comment"], first["last_comment_label"], first["last_comment_outcome"]),
                         ('просил после 18:00', 'сегодня, 15:14', 'Перезвонить позже'))
        self.assertIsNone(first["last_attempt"])                # комментарий — из другой строки водителя
        self.assertEqual((second["last_comment"], second["last_comment_label"], second["last_comment_outcome"]),
                         ('', '', ''))
        self.assertEqual((second["full_name"], second["lead_id"], second["last_attempt"]["outcome_subtype_name"]),
                         ('Петров', 'lead-2', 'Дорого'))
        sql, params = cur.executed[0]
        select = ' '.join(sql[:sql.index('FROM dial_list_assignments a')].split())
        # r[22] — текст отказа АТС последней попытки (13.10.2026): наружу — только его вид.
        self.assertTrue(select.endswith('a.lead_id, os.name, t.auto_hangup, '
                                        + dial_service.DialListService._LAST_COMMENT_COLUMNS + ', t.api_error'),
                        select)
        self.assertEqual(params, ('portion-1',))
        self.assertNotIn('"comment"', inspect.getsource(dial_service.DialListService._portion_items))

    def _state_svc(self, comment_row):
        cur = _RoutedCursor([
            ('make_interval(mins => %s)', {'one': ('att-1', 'asg-1', 'leg_answered', 'gc-1', None, 'Иванов', self.LEAD)}),
            ('FROM (SELECT 1) one', {'one': comment_row}),
            ('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db, sign_checker=_FakeSignChecker(db, configured=False))
        svc.operator_context = _operator_ctx
        svc.reconcile = lambda ctx: None
        return svc, cur, db

    def test_live_call_in_state_carries_it(self):
        svc, cur, db = self._state_svc(('просил после 18:00', self.AT, 'Перезвонить позже'))
        active = svc.get_state(7, phone_version=GUARDED_PHONE)['active_attempt']
        self.assertEqual((active['last_comment'], active['last_comment_label'], active['last_comment_outcome']),
                         ('просил после 18:00', 'сегодня, 15:14', 'Перезвонить позже'))
        self.assertEqual((active['attempt_id'], active['lead_id'], active['full_name']), ('att-1', self.LEAD, 'Иванов'))
        (sql, params), = cur.sql_with('FROM (SELECT 1) one')
        self.assertEqual(sql, dial_service.DialListService._LAST_COMMENT_SQL)
        self.assertEqual(params, {"lead_id": self.LEAD, "operator_id": 7})
        # В той же транзакции, что и остальное состояние.
        self.assertTrue(any(sql in tx for tx in db.transactions if any('make_interval(mins' in s for s in tx)))
        # Комментария нет — ключи есть, пустые.
        svc, cur, _ = self._state_svc(None)
        active = svc.get_state(7)['active_attempt']
        self.assertEqual((active['last_comment'], active['last_comment_label'], active['last_comment_outcome']),
                         ('', '', ''))
        # Идущего звонка нет — и запроса нет.
        cur = _RoutedCursor([('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]})])
        svc = dial_service.DialListService(db=_TxDb(cur))
        svc.operator_context = _operator_ctx
        svc.reconcile = lambda ctx: None
        self.assertIsNone(svc.get_state(7)['active_attempt'])
        self.assertEqual(cur.sql_with('FROM (SELECT 1) one'), [])

    def test_call_reply_carries_it(self):
        cur = _RoutedCursor([
            ('FOR UPDATE OF a', {'one': ('asg-1', 'issued', 0, '77010000001', 'Иванов', self.LEAD, False)}),
            ('FROM (SELECT 1) one', {'one': ('не берёт после обеда', self.YESTERDAY_AT, None)}),
            ('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]}),
            ('INSERT INTO dial_list_attempts', {'one': ('att-1', None)}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db, sign_checker=_FakeSignChecker(db, configured=False))
        svc.operator_context = _operator_ctx
        client = _OriginateClient(db)
        svc._client = lambda dep: client
        attempt = svc.start_call(7, 'asg-1')
        self.assertEqual((attempt['last_comment'], attempt['last_comment_label'], attempt['last_comment_outcome']),
                         ('не берёт после обеда', 'вчера, 09:12', ''))
        self.assertEqual(cur.sql_with('FROM (SELECT 1) one')[0][1], {"lead_id": self.LEAD, "operator_id": 7})
        # Читается в транзакции звонка, до запроса в Binotel; номера в ответе по-прежнему нет.
        self.assertEqual(client.calls[0][3], False)
        self.assertTrue(any('FROM (SELECT 1) one' in s for s in db.transactions[0]))
        self.assertNotIn('77010000001', json.dumps(attempt, ensure_ascii=False))
        # Подписавшему не звоним — и комментарий не читаем.
        cur.rules[0] = ('FOR UPDATE OF a', {'one': ('asg-1', 'issued', 0, '77010000001', 'Иванов', self.LEAD, True)})
        cur.executed.clear()
        with self.assertRaises(dial_service.DialListError):
            svc.start_call(7, 'asg-1')
        self.assertEqual(cur.sql_with('FROM (SELECT 1) one'), [])

    def test_missing_lead_asks_nothing(self):
        cur = _RoutedCursor([])
        fields = dial_service.DialListService(db=None)._last_comment(cur, 7, None, self.TODAY)
        self.assertEqual((fields, cur.executed), ({"last_comment": '', "last_comment_label": '',
                                                   "last_comment_outcome": ''}, []))

    def test_worked_tabs_carry_it_and_keep_comment_as_it_was(self):
        worked = WorkedTabsTests('test_row_lands_on_exactly_one_place')
        outcomes = [('o1', 'Отказ', '#FF3B30', 1, False, True, 0, [])]
        at = self.AT
        rows = [
            # Итог с комментарием: comment — комментарий последней попытки строки (как был),
            # last_comment — последний по водителю вообще (здесь он же).
            worked._row('a1', 'l1', 'С итогом', 'done', 'answered', at, at, 'o1', at, 'дорого', 'finished', 'Отказ',
                        '#FF3B30', 1, 'done', None, last_comment=('дорого', at, 'Отказ')),
            # Недозвон: comment у «Не дозвонились» пуст (как был), а прошлый комментарий виден.
            worked._row('a2', 'l2', 'Недозвон', 'done', 'no_answer', at, at, None, None, 'старый в строке', 'finished',
                        None, None, None, 'in_progress', None,
                        last_comment=('просил вечером', self.YESTERDAY_AT, 'Перезвонить позже')),
            worked._row('a3', 'l3', 'Без комментариев', 'done', 'answered', at, at, 'o1', at, '', 'finished', 'Отказ',
                        '#FF3B30', 1, 'done', None),
        ]
        items = {i['full_name']: i for i in worked._worked(rows, outcomes)['items']}
        self.assertEqual((items['С итогом']['comment'], items['С итогом']['last_comment'],
                          items['С итогом']['last_comment_label'], items['С итогом']['last_comment_outcome']),
                         ('дорого', 'дорого', 'сегодня, 15:14', 'Отказ'))
        self.assertEqual((items['Недозвон']['comment'], items['Недозвон']['last_comment'],
                          items['Недозвон']['last_comment_label'], items['Недозвон']['last_comment_outcome']),
                         ('', 'просил вечером', 'вчера, 09:12', 'Перезвонить позже'))
        # Подпись времени вкладки (at_label) — прежняя: «сегодня» словом, вчерашний день числом.
        self.assertEqual(items['С итогом']['at_label'], 'сегодня, 15:14')
        self.assertEqual((items['Без комментариев']['comment'], items['Без комментариев']['last_comment'],
                          items['Без комментариев']['last_comment_label']), ('', '', ''))
        # Ключи — у каждой строки, чтобы телефон не гадал, старый ли сервер.
        for item in items.values():
            self.assertLessEqual({'comment', 'last_comment', 'last_comment_label', 'last_comment_outcome',
                                  'auto_hangup'}, set(item))


class EndedLegHoldsNothingTests(unittest.TestCase):
    """13.10.2026 (прод 12.10.2026: исход Binotel ехал 5–50 с, и всё это время «Ждём исход от
    АТС…»): кончившееся плечо (ended) не держит ни «Позвонить», ни «Ещё». Держит только звонок,
    плечо которого по телефону ещё идёт; тому же водителю снова — после исхода; итог по позднему
    ответу требует следующий звонок (_require_outcome_done). Настоящим SQL —
    test_dial_list_dispatch_postgres."""

    def test_only_a_leg_that_is_up_is_a_call(self):
        cur = _RoutedCursor([])
        dial_service.DialListService(db=None)._active_attempt(cur, 7)
        sql = ' '.join(cur.executed[0][0].split())
        self.assertIn("t.state IN ('requested', 'leg_ringing', 'leg_answered')", sql)
        self.assertNotIn("'ended'", sql)
        self.assertEqual(dial_service.LEG_UP_STATES, ('requested', 'leg_ringing', 'leg_answered'))
        self.assertEqual(dial_service.LIVE_STATES, dial_service.LEG_UP_STATES + ('ended',))
        # Прежний смысл (с кончившимся плечом) — по запросу (ревью 13.10.2026: состояние 3.22.33).
        cur = _RoutedCursor([])
        dial_service.DialListService(db=None)._active_attempt(cur, 7, include_ended=True)
        sql = ' '.join(cur.executed[0][0].split())
        self.assertIn("t.state IN ('requested', 'leg_ringing', 'leg_answered', 'ended')", sql)
        self.assertEqual(cur.executed[0][1], (7, dial_service.ACTIVE_ATTEMPT_WINDOW_MINUTES))

    def test_released_phone_still_waits_on_an_ended_attempt(self):
        # Ревью 13.10.2026 (находки 2, 8, 9): выпущенный 3.22.33 после «Завершить» ждёт решения и
        # считает: попытки нет ни в active_attempt, ни в pending_outcome — итог не нужен. Ему (нет
        # заголовка версии) active_attempt — с кончившимся плечом, как было; телефону с заголовком —
        # только идущий звонок. «Позвонить» и «Позвонить ещё раз» сервер не держит ни тому, ни другому.
        def states(phone_version):
            cur = _RoutedCursor([('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]})])
            svc = dial_service.DialListService(db=_TxDb(cur))
            svc.operator_context = _operator_ctx
            svc.reconcile = lambda ctx: None
            svc.get_state(7, phone_version=phone_version)
            (sql, _), = cur.sql_with('t.requested_at > CURRENT_TIMESTAMP - make_interval(mins => %s)')
            return ' '.join(sql.split())

        for version in (None, '', 'iCORE', '3.22'[:1]):
            self.assertIn("'leg_answered', 'ended')", states(version), repr(version))
        for version in (GUARDED_PHONE, '3.22.33'):
            self.assertNotIn("'ended'", states(version), version)
        for name in ('start_call', 'recall_lead'):
            src = inspect.getsource(getattr(dial_service.DialListService, name))
            self.assertIn('self._active_attempt(cur, ', src)
            self.assertNotIn('include_ended', src, name)
        state_src = inspect.getsource(dial_service.DialListService.get_state)
        self.assertIn('include_ended=not guarded', state_src)

    def _call_svc(self, settling):
        cur = _RoutedCursor([
            ('FOR UPDATE OF a', {'one': ('asg-1', 'issued', 1, '77010000001', 'Иванов', 'lead-1', False)}),
            ('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]}),
            ("WHERE assignment_id = %s AND state = 'ended'", {'one': (1,) if settling else None}),
            ('INSERT INTO dial_list_attempts', {'one': ('att-2', None)}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db, sign_checker=_FakeSignChecker(db, configured=False))
        svc.operator_context = _operator_ctx
        client = _OriginateClient(db)
        svc._client = lambda dep: client
        return svc, cur, client

    def test_same_driver_again_only_after_the_final(self):
        svc, cur, client = self._call_svc(settling=True)
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.start_call(7, 'asg-1')
        self.assertEqual((str(ctx.exception), ctx.exception.status),
                         ("Исход прошлого звонка этому водителю ещё не пришёл от АТС — подождите несколько секунд", 409))
        self.assertEqual((client.calls, cur.sql_with('INSERT INTO dial_list_attempts')), ([], []))
        (sql, params), = cur.sql_with("WHERE assignment_id = %s AND state = 'ended'")
        self.assertEqual(params, ('asg-1', dial_service.ATTEMPT_STALE_MINUTES * 60))
        # Исхода прошлого звонка ждать не надо — звонок идёт.
        svc, cur, client = self._call_svc(settling=False)
        self.assertEqual(svc.start_call(7, 'asg-1')["attempt_id"], 'att-2')
        self.assertEqual(len(client.calls), 1)

    def test_settling_row_does_not_hold_next(self):
        svc = dial_service.DialListService
        self.assertTrue(svc._row_settling({"state": "issued", "last_attempt": {"state": "ended"}}))
        for item in ({"state": "issued", "last_attempt": {"state": "leg_answered"}},
                     {"state": "issued", "last_attempt": None}, {"state": "done", "last_attempt": {"state": "ended"}}):
            self.assertFalse(svc._row_settling(item), item)
        # «Осталось» у «Ещё» — без строк, исход которых ещё едет.
        cur = _RoutedCursor([('WHERE operator_id = %s AND closed_at IS NULL', {'one': ('p-1', 2, None)}),
                             ("WHERE portion_id = %s AND state = 'issued'", {'one': (0,)}),
                             ('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]})])
        self.assertEqual(dial_service.DialListService(db=None)._refuse_next_portion(cur, _operator_ctx(7))["id"], 'p-1')
        sql = ' '.join(cur.sql_with("WHERE portion_id = %s AND state = 'issued'")[0][0].split())
        self.assertIn("ORDER BY t.requested_at DESC LIMIT 1), '') <> 'ended'", sql)

    def test_state_counts_settling_rows_as_handled(self):
        row = ('asg-1', 1, 'Иванов', 'issued', '', 1, None, 'att-1', 'ended', '', None, 'gc', None, None,
               False, None, 'lead-1', None, '', None, None, None)
        cur = _RoutedCursor([('ORDER BY closed_at IS NULL DESC, issued_at DESC', {'one': ('p-1', 1, None, None)}),
                             ('WHERE a.portion_id = %s', {'all': [row]}),
                             ('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]}),
                             ('AND l.attempts_total < %s', {'all': [('lead-2',)]})])
        svc = dial_service.DialListService(db=_TxDb(cur))
        svc.operator_context = _operator_ctx
        svc.reconcile = lambda ctx: None
        state = svc.get_state(7, phone_version=GUARDED_PHONE)
        self.assertEqual((state["portion"]["pending"], state["can_request_next"]), (0, True))

    def test_next_carries_settling_rows_into_the_new_portion(self):
        cur = _RoutedCursor([
            ('WHERE operator_id = %s AND closed_at IS NULL', {'one': ('p-old', 2, None)}),
            ("WHERE portion_id = %s AND state = 'issued'", {'one': (0,)}),
            ('FROM dial_list_outcomes o', {'all': [RingTimeoutTests.OUTCOME]}),
            ("WHERE a.portion_id = %s AND a.state = 'issued'", {'all': [('asg-ended',)]}),
            ('FOR UPDATE SKIP LOCKED', {'all': [('lead-a',), ('lead-b',)]}),
            ('INSERT INTO dial_list_portions', {'one': ('portion-new',)}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db, sign_checker=_FakeSignChecker(db, configured=False))
        svc.operator_context = lambda uid: dict(_operator_ctx(uid), settings=dict(_operator_ctx(uid)['settings'],
                                                                                 portion_size=2))
        svc.get_state = lambda uid, phone_version=None: {"portion": "новая"}
        with self.assertLogs('dial_list.service', level='INFO') as logs:
            svc.issue_next_portion(7)
        self.assertEqual(cur.sql_with('INSERT INTO dial_list_portions')[0][1], (7, 1954, 3))   # 2 новых + 1
        self.assertEqual([p for _, p in cur.sql_with('SET portion_id = %s, position = %s')],
                         [('portion-new', 3, 'asg-ended')])
        self.assertTrue(any('переехало 1' in line for line in logs.output), logs.output)


class OperatorSignLimitsTests(unittest.TestCase):
    """Пределы «Проверить на подписание» (13.10.2026): об одном водителе — раз в 20 с, от одного
    оператора — не чаще раза в 2 с и не больше 20 в минуту, на весь сервер — не больше 4 сразу,
    без ожидания места. Предел исчерпан — ответ из базы, «только что проверяли», fresh=false."""

    LEAD = OperatorSignCheckTests.LEAD

    def setUp(self):
        _fresh_sign_limits(self)
        self.svc = dial_service.DialListService(db=None)

    def _leads(self, n):
        return [f'{i:08d}-0000-0000-0000-000000000000' for i in range(n)]

    def test_constants(self):
        self.assertEqual((dial_service.SIGN_OPERATOR_GAP_SEC, dial_service.SIGN_OPERATOR_PER_MINUTE,
                          dial_service.SIGN_OPERATOR_CONCURRENT), (2, 20, 4))

    def test_operator_gap_and_per_minute(self):
        slot, asks = self.svc._sign_ask_slot, dial_service.DialListService._sign_operator_asks
        leads = self._leads(25)
        self.assertEqual(slot(7, leads[0]), (True, ''))
        self.svc._sign_now_slots.release()
        ok, why = slot(7, leads[1])                                               # чаще раза в 2 с
        self.assertEqual((ok, why), (False, 'оператор спрашивает чаще раза в 2 с'))
        self.assertEqual(slot(8, leads[1]), (True, ''))                            # другой оператор — можно
        self.svc._sign_now_slots.release()
        # 20 вопросов за минуту (каждый — через 2.5 с) — двадцать первый отказ.
        now = time.monotonic()
        asks[7] = [now - 55 + 2.5 * i for i in range(20)]                           # последний — 7.5 с назад
        ok, why = slot(7, leads[2])
        self.assertEqual((ok, why), (False, 'оператор спросил 20 раз за минуту'))
        # Минута прошла — снова можно; отказ отметок не ставит (водитель не помечен).
        asks[7] = [at - 61 for at in asks[7]]
        self.assertNotIn(leads[2], dial_service.DialListService._sign_asked)
        self.assertEqual(slot(7, leads[2]), (True, ''))
        self.svc._sign_now_slots.release()

    def test_concurrent_checks_are_bounded_without_waiting(self):
        slot = self.svc._sign_ask_slot
        leads = self._leads(6)
        taken = [slot(100 + i, leads[i]) for i in range(4)]
        self.assertEqual(taken, [(True, '')] * 4)
        started = time.monotonic()
        self.assertEqual(slot(200, leads[4]), (False, 'уже идут 4 проверки сразу'))
        self.assertLess(time.monotonic() - started, 0.5)                           # без ожидания
        self.svc._sign_now_slots.release()
        self.assertEqual(slot(201, leads[5]), (True, ''))
        for _ in range(4):
            self.svc._sign_now_slots.release()

    def test_slot_is_released_even_when_sapar_fails(self):
        helper = OperatorSignCheckTests('test_status_mapping_for_the_phone')
        svc, cur, db, checker = helper._svc([helper._lead('unsigned', checked_ago=timedelta(hours=3))],
                                            answers=[RuntimeError('Sapar упал')])
        with self.assertLogs('dial_list.service', level='ERROR'):
            helper._check(svc)
        # Все места свободны: четыре проверки подряд от разных операторов проходят.
        for i in range(4):
            self.assertEqual(self.svc._sign_ask_slot(300 + i, self._leads(4)[i]), (True, ''))
        for _ in range(4):
            self.svc._sign_now_slots.release()

    def test_over_limit_answer_is_the_db_one_and_not_fresh(self):
        helper = OperatorSignCheckTests('test_status_mapping_for_the_phone')
        svc, cur, db, checker = helper._svc([helper._lead('unsigned', checked_ago=timedelta(hours=3))] * 2,
                                            answers=[{"checked": 1}])
        helper._check(svc)
        other = '99999999-9999-9999-9999-999999999999'
        svc2, cur2, db2, checker2 = helper._svc([helper._lead('not_formed', checked_ago=timedelta(hours=3),
                                                              lead_id=other)])
        res = helper._check(svc2, other)
        self.assertEqual(checker2.calls, [])
        self.assertEqual((res['sign']['label'], res['sign']['fresh'], res['text']),
                         ('Не подписал · документы не сформированы', False, dial_service.SIGN_OPERATOR_THROTTLED))


class RepairCutCallsTests(unittest.TestCase):
    """Починка 12.10.2026 (владелец): водители, чьи разговоры оборвал автосброс, — список и
    возврат в очередь того же оператора. Ручка руководителя; ни номеров, ни ИИН. Настоящим
    SQL (отбор, повторный запуск, возврат) — test_dial_list_dispatch_postgres."""

    def test_selection_rules(self):
        sql = ' '.join(dial_service.DialListService._REPAIR_CUT_SQL.split())
        for rule in ("t.ring_timeout_at IS NOT NULL", "UPPER(t.disposition) = 'ANSWER' AND t.billsec > 0",
                     "t.state = 'finished' AND NOT t.cancelled", "t.auto_hangup <> 'announcement'",
                     "t.requested_at >= %(since)s", "l.department_id = %(department_id)s",
                     "l.signed_at IS NULL AND l.sign_status <> 'processing'", "l.status <> 'excluded'",
                     "WHERE x.lead_id = l.id AND x.state = 'issued'",
                     "t2.requested_at > c.requested_at", "t2.ring_timeout_at IS NULL",
                     "e.kind = 'requeue' AND e.note LIKE %(marker)s", "DISTINCT ON (a.lead_id)"):
            self.assertIn(rule, sql)
        for private in ('phone_norm', 'iin'):
            self.assertNotIn(private, sql)
        self.assertEqual(dial_service.DialListService.REPAIR_CUT_NOTE, 'Автосброс оборвал разговор')

    def _svc(self, rows, active=True, recheck=None):
        cur = _RoutedCursor([
            ('WHERE TRUE', {'all': rows}),
            ('l.id = %(lead_id)s', {'one': recheck}),
            ('FROM dial_list_leads WHERE id = %s AND department_id = %s FOR UPDATE', {'one': ('lead',)}),
            ('LEFT JOIN dial_list_line_members m ON m.user_id = u.id', {'one': (1,) if active else None}),
        ])
        db = _TxDb(cur)
        svc = dial_service.DialListService(db=db)
        recalled = []
        svc._recall_row = lambda cur, ctx, lead_id: recalled.append((ctx, lead_id)) or ('asg-new', 'строка')
        return svc, cur, db, recalled

    ROW = ('11111111-1111-1111-1111-111111111111', 'Иванов Иван', 'att-1', 520, 'Своев Сава',
           datetime(2026, 10, 12, 9, 5, tzinfo=timezone.utc), 40, 'Отказ', 'Сброс')

    def test_dry_run_lists_without_writing(self):
        svc, cur, db, recalled = self._svc([self.ROW])
        res = svc.repair_cut_calls(1954, '2026-10-12T00:00', apply=False)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["items"][0], {
            "lead_id": self.ROW[0], "full_name": 'Иванов Иван', "attempt_id": 'att-1',
            "operator": {"id": 520, "name": 'Своев Сава'}, "call_at": '2026-10-12T09:05:00+00:00',
            "call_at_label": at_label_for(self.ROW[5]), "billsec": 40,
            "outcome": {"name": 'Отказ', "subtype": 'Сброс'}})
        self.assertEqual((res["since"], res["target"], res["apply"]), ('2026-10-12T00:00:00+05:00', 'operator', False))
        self.assertEqual(_writes(sql for sql, _ in cur.executed), [])
        params = cur.sql_with('WHERE TRUE')[0][1]
        self.assertEqual((params["department_id"], params["marker"], params["limit"]),
                         (1954, 'Автосброс оборвал разговор%', dial_service.DialListService.REPAIR_CUT_LIMIT))

    def test_apply_to_operator_or_pool(self):
        svc, cur, db, recalled = self._svc([self.ROW], active=True, recheck=self.ROW)
        with self.assertLogs('dial_list.service', level='INFO'):
            res = svc.repair_cut_calls(1954, '2026-10-12T00:00', apply=True, actor_id=5)
        self.assertEqual(res["applied"], {"operator": 1, "pool": 0, "skipped": 0})
        self.assertEqual(recalled, [({"user_id": 520, "department_id": 1954}, self.ROW[0])])
        (sql, params), = cur.sql_with('INSERT INTO dial_list_lead_events')
        self.assertEqual(params[:4], (self.ROW[0], 1954, 5, 'requeue'))
        self.assertEqual(params[4], 'Автосброс оборвал разговор 12.10.2026 14:05 — возвращён в очередь')
        self.assertIn('pg_advisory_xact_lock', ' '.join(s for s, _ in cur.executed))
        # Оператора в отделе нет — в общий список, как «Вернуть в список».
        svc, cur, db, recalled = self._svc([self.ROW], active=False, recheck=self.ROW)
        with self.assertLogs('dial_list.service', level='INFO'):
            res = svc.repair_cut_calls(1954, '2026-10-12T00:00', apply=True, actor_id=5)
        self.assertEqual((res["applied"], recalled), ({"operator": 0, "pool": 1, "skipped": 0}, []))
        self.assertEqual(len(cur.sql_with("SET status = 'new', attempts_total = 0, last_attempt_at = NULL")), 1)
        # Пока обходили, водитель перестал подходить (выдали, подписал) — пропуск, без записи.
        svc, cur, db, recalled = self._svc([self.ROW], recheck=None)
        with self.assertLogs('dial_list.service', level='INFO'):
            res = svc.repair_cut_calls(1954, '2026-10-12T00:00', apply=True)
        self.assertEqual(res["applied"], {"operator": 0, "pool": 0, "skipped": 1})
        self.assertEqual(res["items"][0]["result"], 'skipped')
        self.assertEqual(cur.sql_with('INSERT INTO dial_list_lead_events'), [])

    def test_arguments(self):
        svc, cur, db, recalled = self._svc([])
        for since in ('', None, 'вчера', '12.10.2026'):
            with self.assertRaises(dial_service.DialListError) as ctx:
                svc.repair_cut_calls(1954, since)
            self.assertEqual(ctx.exception.status, 400, since)
        with self.assertRaises(dial_service.DialListError):
            svc.repair_cut_calls(1954, '2026-10-12', target='всем')
        self.assertEqual(svc.repair_cut_calls(1954, '2026-10-12T00:00:00Z')["since"], '2026-10-12T00:00:00+00:00')

    def _client(self, svc, role='operator', headed=()):
        import flask
        requester = (5, 'Руководитель', 'x', role, None, None, None, 'boss')
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (5, requester, None), is_admin_role=lambda r: r == 'admin',
            headed_department_ids=lambda rid: list(headed), service=svc)
        app = flask.Flask('dial_list_repair_test')
        app.register_blueprint(bp)
        return app.test_client()

    def test_route_is_in_the_manager_zone(self):
        seen = []

        class Svc:
            def manager_scope(self, is_admin, headed, login=None, supervisor_department_code=None):
                return None if is_admin else [d for d in headed if d == 1954]

            def repair_cut_calls(self, department_id, since, apply=False, target='operator', actor_id=None):
                seen.append((department_id, since, apply, target, actor_id))
                return {"count": 0, "items": []}

        url = '/api/dial_list/departments/1954/repair/cut-calls'
        resp = self._client(Svc()).post(url, json={"since": "2026-10-12T00:00"})
        self.assertEqual((resp.status_code, resp.get_json()), (403, {"error": "Раздел «Удаленный КЦ» вам недоступен"}))
        resp = self._client(Svc(), headed=[77]).post('/api/dial_list/departments/1954/repair/cut-calls', json={})
        self.assertEqual(resp.status_code, 403)
        resp = self._client(Svc(), headed=[1954]).post(url, json={"since": "2026-10-12T00:00", "apply": True,
                                                                   "target": "pool"})
        self.assertEqual((resp.status_code, resp.get_json()["status"]), (200, 'success'))
        resp = self._client(Svc(), role='admin').post(url, json={"since": "2026-10-12T00:00"})
        self.assertEqual(resp.status_code, 200)
        resp = self._client(Svc(), role='admin').post(url, json={"since": "2026-10-12T00:00", "apply": "да"})
        self.assertEqual((resp.status_code, resp.get_json()), (400, {"error": "apply: true или false"}))
        self.assertEqual(seen, [(1954, '2026-10-12T00:00', True, 'pool', 5),
                                (1954, '2026-10-12T00:00', False, 'operator', 5)])
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        manager_part = src[src.index('# ── руководитель'):]
        chunk = manager_part[manager_part.index("repair/cut-calls'"):]
        self.assertIn('_manager(department_id)', chunk[:900])


class AnnouncementStatsTests(unittest.TestCase):
    """Замер точности распознавания сообщения сети по речи (ревью 13.10.2026): по этим числам
    владелец решает, включать ли режим auto. Настоящим SQL — test_dial_list_dispatch_postgres."""

    def _client(self, svc, role='operator', headed=()):
        import flask
        requester = (5, 'Руководитель', 'x', role, None, None, None, 'boss')
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (5, requester, None), is_admin_role=lambda r: r == 'admin',
            headed_department_ids=lambda rid: list(headed), service=svc)
        app = flask.Flask('dial_list_announcement_stats_test')
        app.register_blueprint(bp)
        return app.test_client()

    def test_route_is_in_the_manager_zone(self):
        seen = []

        class Svc:
            def manager_scope(self, is_admin, headed, login=None, supervisor_department_code=None):
                return None if is_admin else [d for d in headed if d == 1954]

            def announcement_stats(self, department_id, days=None):
                seen.append((department_id, days))
                return {"analysed": 0}

        url = '/api/dial_list/departments/1954/announcement_stats'
        self.assertEqual(self._client(Svc()).get(url).status_code, 403)
        self.assertEqual(self._client(Svc(), headed=[77]).get(url).status_code, 403)
        resp = self._client(Svc(), headed=[1954]).get(url + '?days=7')
        self.assertEqual((resp.status_code, resp.get_json()), (200, {"status": "success", "analysed": 0}))
        self.assertEqual(self._client(Svc(), role='admin').get(url).status_code, 200)
        self.assertEqual(seen, [(1954, '7'), (1954, None)])

    def test_only_counts_leave_the_query(self):
        sql = dial_service.DialListService._ANNOUNCEMENT_STATS_SQL
        select = sql[:sql.index('FROM dial_list_attempts')]
        for word in ('phone', 'iin', 'full_name', 'external', 'comment'):
            self.assertNotIn(word, select, word)
        self.assertIn(dial_service.test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL, sql)       # тестовые номера — мимо
        self.assertIn('l.department_id = %(department_id)s', sql)
        self.assertIn('t.announcement_suspected IS NOT NULL', sql)

    def test_numbers(self):
        rows = [
            # suspected, кто положил, финал, был ли потом разговор, ответ ≥ 10 с, сколько
            (True, 'none', 'BUSY', False, False, 3),
            (True, 'none', 'CHANUNAVAIL', False, False, 1),
            (True, 'none', 'NOANSWER', False, False, 2),
            (True, 'none', 'ANSWER', True, True, 1),
            (True, 'none', 'ANSWER', False, False, 2),
            (True, 'none', '', False, False, 1),
            (True, 'none', 'UNKNOWN', False, False, 1),
            (True, 'auto', 'CANCEL', False, False, 4),
            (True, 'operator', 'CANCEL', False, False, 2),
            (True, 'sit', 'BUSY', False, False, 1),
            (False, 'none', 'BUSY', False, False, 5),
            (False, 'sit', 'BUSY', False, False, 2),
            (False, 'none', 'ANSWER', True, True, 9),
        ]
        cur = _RoutedCursor([('t.announcement_suspected IS NOT NULL', {'all': rows})])
        svc = dial_service.DialListService(db=_routed_db(cur))
        # Прежние переменные окружения больше ни на что не влияют: разбор звука выключен (13.10.2026).
        with unittest.mock.patch.dict(os.environ, {'DIAL_LIST_ANNOUNCEMENT_MODE': 'auto',
                                                   'DIAL_LIST_ANNOUNCEMENT_MS': '4000',
                                                   'DIAL_LIST_ANNOUNCEMENT_SIT': 'off'}):
            stats = svc.announcement_stats(1954)
        self.assertEqual(cur.executed[0][1], {"department_id": 1954, "days": 14, "long_sec": 10})
        self.assertEqual((stats["analysed"], stats["suspected"]), (34, 18))
        self.assertEqual(stats["ended_by"], {"auto": 4, "sit": 1, "operator": 2, "none": 11})
        self.assertEqual(stats["finals"], {"network": 4, "answered": 3, "no_answer": 2, "cancel": 0, "other": 1,
                                           "pending": 1})
        self.assertEqual(stats["finals_ended_by_us"], {"network": 1, "answered": 0, "no_answer": 0, "cancel": 6,
                                                       "other": 0, "pending": 0})
        self.assertEqual(stats["precision"], {"network": 4, "no_answer": 2, "answered_talk": 1, "answered_silent": 2,
                                              "decided": 9, "percent": 44.4})
        self.assertEqual((stats["talked_after"], stats["answered_long"], stats["network_missed"]), (1, 1, 5))
        self.assertEqual(stats["dispositions"][:2], [{"disposition": 'ANSWER', "count": 3},
                                                     {"disposition": 'BUSY', "count": 3}])
        self.assertEqual(stats["settings"], {"announcement_mode": 'off', "announcement_sit": False,
                                             "announcement_ms": 0})
        # Ничего не замерено — проценты не придумываем.
        empty = dial_service.DialListService(db=_routed_db(_RoutedCursor([]))).announcement_stats(1954, days='30')
        self.assertEqual((empty["analysed"], empty["precision"]["percent"], empty["days"]), (0, None, 30))
        for bad in ('x', 0, 93, '-5', 1.5j):
            with self.assertRaises(dial_service.DialListError) as ctx:
                svc.announcement_stats(1954, days=bad)
            self.assertEqual(ctx.exception.status, 400, repr(bad))

    def test_schema_columns_come_before_indexes(self):
        ddl = dial_schema.DDL
        first_index = next(i for i, s in enumerate(ddl) if 'CREATE INDEX' in s or 'CREATE UNIQUE INDEX' in s)
        for column, kind in (('announcement_suspected', 'BOOLEAN'), ('announcement_by', 'VARCHAR(16)')):
            at = next(i for i, s in enumerate(ddl) if f'ADD COLUMN IF NOT EXISTS {column}' in s)
            self.assertLess(at, first_index, column)
            self.assertEqual(ddl[at], f"ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS {column} {kind}")


def at_label_for(moment):
    return dial_service.at_label(moment, dial_service._today())


if __name__ == '__main__':
    unittest.main()
