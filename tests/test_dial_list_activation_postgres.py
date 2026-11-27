# -*- coding: utf-8 -*-
"""«Активация водителей» (СЗоВ, 26.11.2026) на настоящем PostgreSQL: подключение отдела, загрузка выгрузки
«Новички по поездкам с ЭДО», проверка поездок (CRM) и провайдера (Fleet), две цели и их успешки, строка
водителя телефону без номера, журнал с кнопками «Обновить», сводка, аналитика и аренда прогона.
CRM, кабинет Fleet и Binotel — подделки без сети.

Запуск: DIAL_LIST_TEST_PORT=<порт локального PostgreSQL> pytest tests/test_dial_list_activation_postgres.py
(пользователь postgres без пароля; каждый тест — в своей схеме, схема удаляется)."""
import contextlib
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import activation  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402
from dial_list.activation_check import ActivationChecker  # noqa: E402
from dial_list.activation_sources import SourceError  # noqa: E402
from dial_list.sign_check import SignChecker  # noqa: E402

PORT = os.environ.get('DIAL_LIST_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DIAL_LIST_TEST_PORT)')

ACT, RCC, FREE = 2100, 1954, 2200
OPERATOR = 530
GUARDED = '3.22.36'
D1, D2, D3, D4 = 'a' * 32, 'b' * 32, 'c' * 32, 'd' * 32
PARK = 'e' * 32

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE test_phone_numbers (phone_key varchar(10) UNIQUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text, rate numeric);
    CREATE TABLE sip_department_config (department_id integer PRIMARY KEY, provider text,
                                        sip_server text, ring_timeout_rings smallint,
                                        auto_answer boolean, auto_answer_delay smallint);
    INSERT INTO departments (id, name, code) VALUES (2100, 'Активация водителей', 'activation'),
                                                    (1954, 'Удаленный КЦ', 'remote_cc'),
                                                    (2200, 'Пустой отдел', 'free');
    INSERT INTO sip_department_config VALUES (2100, 'binotel', 'sip53.binotel.com', NULL),
                                             (1954, 'binotel', 'sip53.binotel.com', NULL),
                                             (2200, 'binotel', 'sip53.binotel.com', NULL);
    INSERT INTO users (id, name, login, role, status, department_id, sip_number) VALUES
        (1, 'Админ', 'admin', 'admin', 'working', NULL, NULL),
        (530, 'Ашимова Айгерим', 'aigerim', 'operator', 'working', 2100, '911');
    CREATE TABLE chatapp_operator_map (employee_id bigint PRIMARY KEY, employee_name text, email text,
                                       role_name text, user_id integer REFERENCES users(id) ON DELETE SET NULL,
                                       is_bot boolean NOT NULL DEFAULT FALSE, updated_by integer,
                                       updated_at timestamptz NOT NULL DEFAULT now());
'''

HEADER = ['Диспетчерская', 'ID водителя', 'ФИО', 'Телефон', 'ИИН', 'Дата регистрации', 'Завершено поездок',
          'Провайдер ЭДО']


class _Db:
    _SIP_INACTIVE_STATUSES = ('fired', 'dismissal')

    def __init__(self, schema):
        self.schema = schema

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.schema)
            cur.execute("SET statement_timeout TO '10s'")
        conn.commit()
        return conn

    @contextlib.contextmanager
    def _get_cursor(self):
        conn = self.connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


class _Binotel:
    def __init__(self):
        self.live, self.originated = {}, []

    def originate_internal_to_external(self, internal_number, external_number, **extra):
        self.originated.append((internal_number, external_number))
        return 'gc-%d-%s' % (len(self.originated), uuid.uuid4().hex[:6])

    def call_details(self, general_call_ids, max_wait=None, timeout=None):
        ids = general_call_ids if isinstance(general_call_ids, (list, tuple)) else [general_call_ids]
        return {str(g): dict(self.live[str(g)], general_call_id=str(g)) for g in ids if str(g) in self.live}


class _Crm:
    """CRM без сети: counts — ID → поездок (/partners/orders); cards — ID → карточка driver-info; down — исключение
    вместо ответа (CRM лежит)."""

    def __init__(self):
        self.counts, self.cards, self.asked, self.down = {}, {}, [], None

    def configured(self):
        return True

    def orders(self, account_ids, date_from, date_to, timeout=None):
        self.asked.append(('orders', sorted(account_ids), date_from, date_to))
        if self.down:
            raise self.down
        return {i: {'count': self.counts[i], 'last_order_at': None} for i in account_ids if i in self.counts}

    def driver(self, account_id, timeout=None):
        self.asked.append(('driver', account_id))
        if self.down:
            raise self.down
        return self.cards.get(account_id)


class _Fleet:
    """Кабинет Fleet без сети: providers — ID → (имя, есть ли карточка в парке); down — исключение вместо ответа
    (кабинет лежит)."""

    last_error = ''

    def __init__(self):
        self.providers, self.asked, self.down = {}, [], None

    def configured(self):
        return True

    def provider(self, park_id, driver_id):
        self.asked.append((park_id, driver_id))
        if self.down:
            raise self.down
        return self.providers.get(driver_id, ('', False))


def _sync(fn):
    fn()


def _xlsx(rows):
    import io
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = 'До 50 поездок'
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _now():
    return datetime.now(timezone.utc)


class _Env:
    def __init__(self, db, svc, binotel, crm, fleet):
        self.db, self.svc, self.binotel, self.crm, self.fleet = db, svc, binotel, crm, fleet

    def q(self, sql, params=None):
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def one(self, sql, params=None):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def enroll(self, **settings):
        self.svc.enroll_department(ACT, changed_by=1, profile='activation')
        if settings:
            self.svc.save_department_settings(ACT, settings, changed_by=1)

    def upload(self, rows, name='novички.xlsx'):
        goal = self.svc.department_settings(ACT)["trips_goal"]
        parsed, extras, file_counts = activation.parse_activation_file(_xlsx(rows), '.xlsx', goal=goal)
        counts = self.svc.import_leads(ACT, 1, name, parsed, require_iin=False, keep_iin=True, extras=extras)
        return counts, file_counts

    def lead(self, driver_id):
        return str(self.one("SELECT id FROM dial_list_leads WHERE driver_id = %s", (driver_id,))[0])

    def poll_now(self):
        for calls in self.svc._calls.values():
            with calls._lock:
                calls._entries.clear()
                calls._last_start = None
                calls._not_before = 0.0

    def talk(self, assignment_id, billsec=30):
        """Звонок водителю с разговором billsec секунд: попытка, плечо, финал от Binotel."""
        attempt = self.svc.start_call(OPERATOR, assignment_id, operator_status='outbound')
        self.svc.phone_event(OPERATOR, attempt["attempt_id"], 'ringing', phone_version=GUARDED)
        self.svc.phone_event(OPERATOR, attempt["attempt_id"], 'answered', phone_version=GUARDED)
        self.svc.phone_event(OPERATOR, attempt["attempt_id"], 'ended', by_operator=False, leg_sec=billsec,
                             talk_detected=True, phone_version=GUARDED)
        self.binotel.live[attempt["general_call_id"]] = {"disposition": 'ANSWER', "billsec": billsec, "waitsec": 5}
        self.poll_now()
        self.svc.get_state(OPERATOR, phone_version=GUARDED)
        # Итог разговора: без итога следующий звонок сервер не даст.
        self.svc.set_attempt_outcome(OPERATOR, attempt["attempt_id"], self.talked_outcome())
        return attempt

    def talked_outcome(self, department_id=ACT):
        """Итог главной вкладки «Поездки» без даты и с возвратом в список (как «Перейдёт на Sapar» до вкладок
        итогов, 27.11.2026): проверкам и режимам нужен разговор, после которого водитель остаётся в обзвоне, а у
        стартовых итогов главной вкладки возврат — только с датой."""
        row = self.one("SELECT id FROM dial_list_outcomes WHERE department_id = %s AND name = 'Поговорили'",
                       (department_id,))
        if row:
            return str(row[0])
        return str(self.one("INSERT INTO dial_list_outcomes (department_id, name, position, requeue, group_key) "
                            "VALUES (%s, 'Поговорили', 99, TRUE, 'trips') RETURNING id", (department_id,))[0])

    def refused(self, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except dial_service.DialListError as exc:
            return str(exc), exc.status, exc.kind
        return None


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(dial_service, 'ANSWER_SETTLE_SEC', 0.0)
    schema = 't_dial_act_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled, binotel_company) "
                    "VALUES (1954, TRUE, 'remote_cc')")
    binotel, crm, fleet = _Binotel(), _Crm(), _Fleet()
    signs = SignChecker(db)
    checker = ActivationChecker(db, crm=crm, fleet=fleet, successes=signs, sleep=lambda _s: None)
    svc = dial_service.DialListService(db, client_factory=lambda dep: binotel, sign_checker=signs,
                                       calls_submit=_sync, activation_checker=checker)
    svc.operator_context = lambda uid: {"user_id": int(uid), "name": 'Айгерим', "department_id": ACT,
                                        "internal_number": '911', "settings": svc.department_settings(ACT)}
    try:
        yield _Env(db, svc, binotel, crm, fleet)
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


def _iin(prefix):
    """Верный ИИН с началом prefix (11 цифр): контрольная цифра — та, которую примет clean_iin."""
    for last in range(10):
        if dial_service.clean_iin(f'{prefix}{last}'):
            return f'{prefix}{last}'
    raise AssertionError(prefix)


IIN1, IIN2, IIN3, IIN4 = (_iin('95010130012'), _iin('97030350014'), _iin('98040460015'), _iin('96020240013'))
IINS = {D1: IIN1, D2: IIN2, D3: IIN3, D4: IIN4}


def _row(driver_id, phone, trips, provider, iin=None, park='Jana (Алматы)', fio='Тестов Тест'):
    return [park, driver_id, fio, phone, IINS[driver_id] if iin is None else iin, '2026-11-06 12:32:35', trips,
            provider]


# ── подключение и настройки ──────────────────────────────────────────────────

def test_enroll_activation_department_with_its_own_values(env):
    svc = env.svc
    assert {d["department_id"] for d in svc.candidate_departments(profile='activation')} == {ACT, FREE}
    settings = svc.enroll_department(ACT, changed_by=1, profile='activation')
    assert (settings["profile"], settings["auto_dial"], settings["enabled"], settings["retry_after_hours"],
            settings["retry_schedule_min"], settings["trips_goal"], settings["binotel_company"]) == (
        'activation', True, False, 0, [60, 180, 1440], 50, 'remote_cc')
    assert [r[0] for r in env.q("SELECT name FROM dial_list_outcomes WHERE department_id = %s ORDER BY position",
                                (ACT,))] == [o[0] for o in dial_service.DEFAULT_ACTIVATION_OUTCOMES]
    assert [d["department_id"] for d in svc.list_departments(profile='activation')] == [ACT]
    assert ACT not in {d["department_id"] for d in svc.list_departments(profile='remote_cc')}
    assert ACT not in {d["department_id"] for d in svc.list_departments(profile='tez')}
    # Цель меняется в настройках, в пределах.
    assert svc.save_department_settings(ACT, {"trips_goal": 70}, changed_by=1)["trips_goal"] == 70
    assert env.refused(svc.save_department_settings, ACT, {"trips_goal": 0}, changed_by=1)[1] == 400
    # Отдел удалённого КЦ с базой к активации не переводится повторным подключением.
    assert env.refused(svc.enroll_department, RCC, changed_by=1, profile='activation')[1] == 409


# ── загрузка базы ───────────────────────────────────────────────────────────

def test_upload_keeps_driver_fields_and_skips_drivers_who_already_did_the_goal(env):
    env.enroll()
    counts, file_counts = env.upload([
        _row(D1, '77001110001', 34, 'Бумажный документооборот'),
        _row(D2, '77001110002', 7, 'Sapar', iin='bad'),
        _row(D3, '77001110003', 51, 'Payda'),
        _row(D4, '77001110004', 12, 'Не найден', park='iTaxi (доставка) (Шымкент)'),
    ])
    assert (counts["rows_new"], file_counts["rows_goal_reached"]) == (3, 1)
    rows = env.q("SELECT driver_id, trips_total, edm_provider, edm_source, park_name, city, iin, "
                 "account_registered_at IS NOT NULL, trip_checked_at FROM dial_list_leads "
                 "WHERE department_id = %s ORDER BY driver_id", (ACT,))
    assert rows == [
        (D1, 34, 'Бумажный документооборот', 'file', 'Jana (Алматы)', 'Алматы', IIN1, True, None),
        (D2, 7, 'Sapar', 'file', 'Jana (Алматы)', 'Алматы', '', True, None),
        (D4, 12, '', '', 'iTaxi (доставка) (Шымкент)', 'Шымкент', IIN4, True, None),
    ]
    # Тот же водитель (ID) под другим номером — повтор; провайдер, проверенный карточкой, файл не перебивает.
    env.q("UPDATE dial_list_leads SET edm_provider = 'Payda', edm_source = 'card' WHERE driver_id = %s", (D1,))
    counts, _ = env.upload([_row(D1, '77001119999', 35, 'Бумажный документооборот')])
    assert (counts["rows_new"], counts["rows_duplicate"]) == (0, 1)
    counts, _ = env.upload([_row(D1, '77001110001', 36, 'Бумажный документооборот')])
    assert (counts["rows_new"], counts["rows_duplicate"]) == (0, 1)
    assert env.one("SELECT trips_total, edm_provider, edm_source FROM dial_list_leads WHERE driver_id = %s",
                   (D1,)) == (36, 'Payda', 'card')


# ── проверки и успешки ───────────────────────────────────────────────────────

def test_run_finds_goal_and_sapar_switch_and_credits_the_last_talk(env):
    env.enroll(portion_size=5)
    env.upload([_row(D1, '77001110001', 34, 'Бумажный документооборот'),
                _row(D2, '77001110002', 10, 'Sapar'),
                _row(D3, '77001110003', 20, 'Payda')])
    # Пачка оператору: поездки кандидатов спрошены одним запросом в CRM перед выдачей.
    env.crm.counts = {D1: 34, D2: 10, D3: 20}
    items = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)["portion"]["items"]
    assert env.crm.asked[0][0] == 'orders' and env.crm.asked[0][1] == sorted([D1, D2, D3])
    by_name = {i["lead_id"]: i for i in items}
    l1, l2, l3 = env.lead(D1), env.lead(D2), env.lead(D3)
    assert by_name[l1]["driver"]["label"] == f'34 из 50 поездок · ЭДО: Бумажный · Парк: Jana (Алматы) · ID: {D1}'
    # Разговоры с первым и третьим (перед звонком — свежие поездки из карточки CRM).
    # Провайдер из файла карточкой ещё не проверяли — перед звонком его спрашивают заодно.
    env.crm.cards = {D1: {'park_id': PARK, 'total': 34, 'created_date': None},
                     D3: {'park_id': PARK, 'total': 20, 'created_date': None}}
    env.fleet.providers = {D1: ('Бумажный документооборот', True), D3: ('Payda', True)}
    env.talk(by_name[l1]["assignment_id"])
    env.talk(by_name[l3]["assignment_id"])
    assert sorted(d for _p, d in env.fleet.asked) == [D1, D3]
    assert env.one("SELECT edm_source, fleet_park_id FROM dial_list_leads WHERE id = %s", (l1,)) == ('card', PARK)
    # Прогон: третий добрал 50; первый перешёл на Sapar (говорили после проверки — спрошен первым); кто на Sapar
    # по файлу — карточкой не спрашиваем.
    env.crm.counts = {D1: 40, D2: 11, D3: 50}
    env.fleet.providers = {D1: ('Sapar', True), D3: ('Payda', True)}
    env.fleet.asked.clear()
    summary = env.svc.activation_checker.run(reason='тест', now=_now() + timedelta(minutes=61))
    assert (summary["trips_checked"], summary["reached"], summary["sapar"], summary["stopped"]) == (3, 1, 1, '')
    assert summary["successes"] == 1 and summary["sapar_successes"] == 1
    assert {d for _p, d in env.fleet.asked} == {D1}, 'добравший ушёл из обзвона, на Sapar — не спрашиваем'
    assert env.one("SELECT signed_at IS NOT NULL, sign_status, success_operator_id FROM dial_list_leads "
                   "WHERE id = %s", (l3,)) == (True, 'trips', OPERATOR)
    assert env.one("SELECT sapar_at IS NOT NULL, sapar_operator_id, signed_at FROM dial_list_leads WHERE id = %s",
                   (l1,)) == (True, OPERATOR, None)
    # Строка добравшего снята со списка оператора, перешедший на Sapar остаётся в обзвоне.
    assert env.one("SELECT state FROM dial_list_assignments WHERE lead_id = %s", (l3,))[0] == 'done'
    assert env.one("SELECT status FROM dial_list_leads WHERE id = %s", (l1,))[0] != 'excluded'
    # Сводка и аналитика.
    s = env.svc.leads_summary(ACT)
    assert (s["profile"], s["reached"], s["on_sapar"], s["sapar_switched"], s["sapar_successes"],
            s["trips_goal"]) == ('activation', 1, 2, 1, 1, 50)
    a = env.svc.analytics(ACT)
    assert (a["profile"], a["totals"]["went_online"], a["totals"]["successes"], a["sapar"]["switched"],
            a["sapar"]["successes"], a["sapar_by_operator"]) == ('activation', 1, 1, 1, 1, {str(OPERATOR): 1})
    day = datetime.now(dial_service.PERIOD_TZ).date()
    row = next(r for r in env.svc.overview([ACT], day, tez=True, activation_dept=True)
               if r["operator_id"] == OPERATOR)
    assert row["sapar_successes"] == 1


def test_lease_keeps_a_second_process_out(env):
    env.enroll()
    env.upload([_row(D1, '77001110001', 3, 'Бумажный документооборот')])
    env.q("INSERT INTO dial_list_check_leases (name, owner, until) VALUES ('activation', 'other:1', %s)",
          (_now() + timedelta(minutes=10),))
    summary = env.svc.activation_checker.run(reason='тест')
    assert summary["stopped"] == 'other_process' and env.crm.asked == []
    env.q("UPDATE dial_list_check_leases SET until = %s", (_now() - timedelta(seconds=1),))
    env.crm.counts = {D1: 4}
    summary = env.svc.activation_checker.run(reason='тест')
    assert summary["trips_checked"] == 1
    state = env.svc.activation_checker.state()
    assert state["running"] is False and state["last_run"]["trips_checked"] == 1


# ── телефон ─────────────────────────────────────────────────────────────────

def test_phone_gets_tez_mode_driver_line_and_no_number(env):
    env.enroll()
    env.upload([_row(D1, '77001110001', 34, 'Бумажный документооборот')])
    env.crm.counts = {D1: 34}
    state = env.svc.issue_next_portion(OPERATOR, phone_version='3.22.66')
    assert (state["profile"], state["goal"], state["direct_dial"], state["auto"]["enabled"]) == (
        'tez', 'activation', False, True)
    item = state["portion"]["items"][0]
    # ID водителя (владелец 27.11.2026): телефону до 3.22.67 — последним в строке, отдельным полем — всем.
    assert (item["driver"]["label"], item["driver"]["city"], item["driver"]["driver_id"]) == (
        f'34 из 50 поездок · ЭДО: Бумажный · Парк: Jana (Алматы) · ID: {D1}', 'Алматы', D1)
    assert '77001110001' not in json.dumps(state, ensure_ascii=False)
    # Звонок — через АТС, номер телефону не уходит. Поездки, проверенные при выдаче, свежее 15 минут — не
    # переспрашиваются; час назад — перед звонком освежаются карточкой CRM.
    env.q("UPDATE dial_list_leads SET trip_checked_at = trip_checked_at - interval '1 hour'")
    env.crm.cards = {D1: {'park_id': PARK, 'total': 35, 'created_date': None}}
    call = env.svc.start_call(OPERATOR, item["assignment_id"], operator_status='outbound', phone_version='3.22.66')
    assert 'dial' not in call and env.binotel.originated == [('911', '77001110001')]
    active = env.svc.get_state(OPERATOR, phone_version='3.22.66')["active_attempt"]
    assert active["driver"]["label"] == f'35 из 50 поездок · ЭДО: Бумажный · Парк: Jana (Алматы) · ID: {D1}'
    # Телефон 3.22.67+ показывает ID сам: в строке его нет, поле — то же.
    new_phone = env.svc.get_state(OPERATOR, phone_version='3.22.67')
    assert (new_phone["active_attempt"]["driver"]["label"], new_phone["active_attempt"]["driver"]["driver_id"]) == (
        '35 из 50 поездок · ЭДО: Бумажный · Парк: Jana (Алматы)', D1)
    assert [(i["driver"]["label"], i["driver"]["driver_id"]) for i in new_phone["portion"]["items"]] == [
        ('35 из 50 поездок · ЭДО: Бумажный · Парк: Jana (Алматы)', D1)]
    # Блок dial_list для /api/operator/sip_settings: телефон в режиме автообзвона, номера спрятаны.
    env.svc.dial_operator = lambda uid, *a, **k: {"department_id": ACT, "department_provider": 'binotel'}
    block = env.svc.phone_settings(OPERATOR, member=None)
    assert (block["profile"], block["goal"], block["hide_numbers"], block["auto"]) == ('tez', 'activation', True,
                                                                                      True)


def test_driver_who_did_the_goal_before_the_call_is_not_called(env):
    env.enroll()
    env.upload([_row(D1, '77001110001', 48, 'Sapar')])
    env.crm.counts = {D1: 48}
    item = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)["portion"]["items"][0]
    # Час спустя водитель добрал 50 — проверка перед звонком это видит, звонка нет.
    env.q("UPDATE dial_list_leads SET trip_checked_at = trip_checked_at - interval '1 hour'")
    env.crm.cards = {D1: {'park_id': PARK, 'total': 50, 'created_date': None}}
    message, status, kind = env.refused(env.svc.start_call, OPERATOR, item["assignment_id"],
                                        operator_status='outbound')
    assert (message, status, kind) == (dial_service.GOAL_ROW_CLOSED_ACTIVATION, 409, 'goal')
    assert env.binotel.originated == []


# ── журнал и кнопки «Обновить» ───────────────────────────────────────────────

def test_journal_card_and_refresh_buttons(env):
    env.enroll()
    env.upload([_row(D1, '77001110001', 34, 'Бумажный документооборот')])
    lead_id = env.lead(D1)
    page = env.svc.leads_journal(ACT)
    row = page["items"][0]
    block = row["activation"]
    assert (block["trips_label"], block["trips_from_file"], block["provider_label"], block["provider_source"],
            block["driver_id"], block["park_name"], block["sapar"]) == (
        '34 из 50 поездок', True, 'ЭДО: Бумажный', 'file', D1, 'Jana (Алматы)', None)
    assert row["stage_label"] == 'В очереди'
    # «Обновить поездки» — карточка CRM (заодно парк), провайдер не трогается.
    env.crm.cards = {D1: {'park_id': PARK, 'total': 41, 'created_date': '2026-11-06 12:32:35'}}
    card = env.svc.check_lead_activation(lead_id, 'trips')
    assert (card["activation"]["trips_label"], card["activation"]["trips_from_file"],
            card["activation"]["provider_source"]) == ('41 из 50 поездок', False, 'file')
    assert card["activation"]["fleet_url"].endswith(f'park_id={PARK}')
    assert env.fleet.asked == []
    # «Обновить провайдера» — карточка Fleet: перешёл на Sapar, разговора не было — успешки нет.
    env.fleet.providers = {D1: ('Sapar', True)}
    card = env.svc.check_lead_activation(lead_id, 'provider')
    assert (card["activation"]["provider_label"], card["activation"]["on_sapar"]) == ('ЭДО: Sapar', True)
    assert card["activation"]["sapar"]["resolved"] is True and card["activation"]["sapar"]["success"] is None
    # Добрал — кнопкой: этап «Добрал поездки», строка в журнале с подписью активации.
    env.crm.cards = {D1: {'park_id': PARK, 'total': 50, 'created_date': None}}
    card = env.svc.check_lead_activation(lead_id, '')
    assert (card["stage"], card["stage_label"], card["activation"]["trips_label"]) == (
        'signed', 'Добрал поездки', 'Добрал 50 поездок')
    assert env.refused(env.svc.check_lead_activation, lead_id, 'nope')[1] == 400


# ── разбор 27.11: успешка за Sapar, повторная загрузка, цель, сбои источников ─────────────────────────────────

SECOND = 531


def _talk_row(env, lead_id, operator_id, billsec, at, state='finished', disposition='ANSWER'):
    """Звонок водителю прямо в базу: кто, сколько секунд разговора, когда начат."""
    portion = env.one("INSERT INTO dial_list_portions (operator_id, department_id, size) VALUES (%s, %s, 1) "
                      "RETURNING id", (operator_id, ACT))[0]
    assignment = env.one("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state) "
                         "VALUES (%s, %s, %s, 1, 'done') RETURNING id", (portion, lead_id, operator_id))[0]
    return str(env.one("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition, "
                       "billsec) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                       (assignment, operator_id, at, state, disposition, billsec))[0])


def _second_operator(env):
    env.q("INSERT INTO users (id, name, login, role, status, department_id, sip_number) "
          "VALUES (%s, 'Второй оператор', 'second', 'operator', 'working', %s, '912')", (SECOND, ACT))


def test_sapar_success_goes_to_the_last_talk_of_10_seconds_before_the_switch(env):
    env.enroll()
    env.upload([_row(D1, '77001110001', 10, 'Бумажный документооборот'),
                _row(D2, '77001110002', 10, 'Бумажный документооборот'),
                _row(D3, '77001110003', 10, 'Бумажный документооборот'),
                _row(D4, '77001110004', 10, 'Бумажный документооборот')])
    _second_operator(env)
    now = _now()
    l1, l2, l3, l4 = (env.lead(d) for d in (D1, D2, D3, D4))
    # D1: говорили оба — успешка последнему разговору до перехода.
    _talk_row(env, l1, OPERATOR, 30, now - timedelta(hours=3))
    last = _talk_row(env, l1, SECOND, 30, now - timedelta(hours=2, minutes=30))
    # D2: только разговор в 5 секунд — не в счёт.
    _talk_row(env, l2, OPERATOR, 5, now - timedelta(hours=3))
    # D3: разговор после перехода — не в счёт.
    _talk_row(env, l3, OPERATOR, 30, now - timedelta(hours=1))
    # D4: звонок, начатый до перехода, ещё идёт — решение ждёт его конца.
    _talk_row(env, l4, OPERATOR, 0, now - timedelta(minutes=5), state='leg_answered', disposition='')
    env.q("UPDATE dial_list_leads SET edm_provider = 'Sapar', sapar_at = %s WHERE id = ANY(%s::uuid[])",
          (now - timedelta(hours=2), [l1, l2, l3]))
    env.q("UPDATE dial_list_leads SET edm_provider = 'Sapar', sapar_at = %s WHERE id = %s",
          (now - timedelta(minutes=2), l4))
    assert env.svc.activation_checker.resolve_sapar_successes(now=now) == 1
    got = {r[0]: tuple(r[1:]) for r in env.q(
        "SELECT id::text, sapar_operator_id, sapar_attempt_id::text, sapar_resolved_at IS NOT NULL "
        "FROM dial_list_leads WHERE department_id = %s", (ACT,))}
    assert got == {l1: (SECOND, last, True), l2: (None, None, True), l3: (None, None, True), l4: (None, None, False)}
    # Повторное решение не двоит.
    assert env.svc.activation_checker.resolve_sapar_successes(now=now) == 0


def test_sapar_switch_counts_only_from_a_known_provider_or_from_none(env):
    env.enroll()
    env.upload([_row(D1, '77001110001', 10, 'Не найден'), _row(D2, '77001110002', 10, 'Бумажный документооборот')])
    l1, l2 = env.lead(D1), env.lead(D2)
    checker, now = env.svc.activation_checker, _now()
    # Провайдер не был известен — Sapar в карточке не переход.
    assert env.one("SELECT edm_provider FROM dial_list_leads WHERE id = %s", (l1,)) == ('',)
    assert checker._write_provider(l1, 'Sapar', now) is False
    # Карточка сказала «ЭДО нет», потом Sapar — переход.
    assert checker._write_provider(l2, '', now) is False
    assert env.one("SELECT edm_source FROM dial_list_leads WHERE id = %s", (l2,)) == ('none',)
    assert checker._write_provider(l2, 'Sapar', now + timedelta(minutes=1)) is True


def test_reupload_with_sapar_counts_as_the_switch(env):
    """Разбор 27.11: файл перевёл «Бумажный» на Sapar — переход, как у карточки (раньше терялся: водителей на Sapar
    больше не проверяют)."""
    env.enroll()
    env.upload([_row(D1, '77001110001', 10, 'Бумажный документооборот')])
    lead = env.lead(D1)
    _talk_row(env, lead, OPERATOR, 30, _now() - timedelta(hours=1))
    env.upload([_row(D1, '77001110001', 12, 'Sapar')])
    assert env.one("SELECT edm_provider, edm_source, sapar_at IS NOT NULL FROM dial_list_leads WHERE id = %s",
                   (lead,)) == ('Sapar', 'file', True)
    assert env.svc.activation_checker.resolve_sapar_successes() == 1
    assert env.one("SELECT sapar_operator_id FROM dial_list_leads WHERE id = %s", (lead,)) == (OPERATOR,)
    sapar_at = env.one("SELECT sapar_at FROM dial_list_leads WHERE id = %s", (lead,))[0]
    env.upload([_row(D1, '77001110001', 13, 'Sapar')])
    assert env.one("SELECT sapar_at FROM dial_list_leads WHERE id = %s", (lead,)) == (sapar_at,)
    # Новый водитель, сразу на Sapar по файлу, — не переход.
    env.upload([_row(D2, '77001110002', 3, 'Sapar')])
    assert env.one("SELECT sapar_at FROM dial_list_leads WHERE driver_id = %s", (D2,)) == (None,)


def test_new_driver_on_the_same_phone_starts_with_his_own_fields(env):
    """Разбор 27.11: у номера другой ID водителя — поездки и провайдер прежнего к нему не переходят (иначе его Sapar
    читался как перевод с «Бумажного» прежнего — ложная успешка)."""
    env.enroll()
    env.upload([_row(D1, '77001110001', 34, 'Бумажный документооборот')])
    lead = env.lead(D1)
    env.q("UPDATE dial_list_leads SET trips_total = 34, trip_checked_at = now(), edm_source = 'card', "
          "edm_checked_at = now(), fleet_park_id = %s WHERE id = %s", (PARK, lead))
    env.upload([_row(D4, '77001110001', 5, 'Sapar')])
    assert env.one("SELECT driver_id, trips_total, trip_checked_at, edm_provider, edm_source, fleet_park_id, sapar_at "
                   "FROM dial_list_leads WHERE id = %s", (lead,)) == (D4, 5, None, 'Sapar', 'file', '', None)
    # Карточка нового водителя — Sapar: он и по файлу на Sapar, перехода нет.
    assert env.svc.activation_checker._write_provider(lead, 'Sapar', _now()) is False
    # Тот же водитель повторно — проверенные поля не трогаются (CRM главнее файла).
    env.q("UPDATE dial_list_leads SET trips_total = 41, trip_checked_at = now() WHERE id = %s", (lead,))
    env.upload([_row(D4, '77001110001', 6, 'Sapar')])
    assert env.one("SELECT trips_total, trip_checked_at IS NOT NULL FROM dial_list_leads WHERE id = %s",
                   (lead,)) == (41, True)


def test_lowered_goal_sends_drivers_above_it_to_recheck(env, monkeypatch):
    """Разбор 27.11: цель снижена до 30 — водитель с 34 поездками больше не выдаётся и закрыт как добравший."""
    env.enroll(portion_size=5)
    env.upload([_row(D1, '77001110001', 34, 'Бумажный документооборот'),
                _row(D2, '77001110002', 12, 'Бумажный документооборот')])
    env.crm.counts = {D1: 34, D2: 12}
    env.svc.activation_checker.run(reason='тест')
    asked = []
    monkeypatch.setattr(env.svc, 'request_activation_check', lambda reason='': asked.append(reason))
    env.svc.save_department_settings(ACT, {"trips_goal": 30}, changed_by=1)
    assert asked == ['цель по поездкам снижена']
    env.crm.asked.clear()
    items = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)["portion"]["items"]
    assert [i["lead_id"] for i in items] == [env.lead(D2)]
    assert env.crm.asked[0][1] == [D1], 'спрошен только тот, кто выше новой цели'
    assert env.one("SELECT signed_at IS NOT NULL, sign_status FROM dial_list_leads WHERE driver_id = %s",
                   (D1,)) == (True, 'trips')
    # Цель поднята — перепроверять некого.
    asked.clear()
    env.svc.save_department_settings(ACT, {"trips_goal": 60}, changed_by=1)
    assert asked == []


def test_fleet_failure_before_a_call_does_not_hide_the_driver_for_a_day(env):
    """Разбор 27.11: сбой кабинета перед звонком в лид не пишется — после разговора провайдера спросят следующим
    прогоном (раньше ошибка вместе со временем загрузки файла выключала водителя на сутки)."""
    env.enroll()
    env.upload([_row(D1, '77001110001', 34, 'Бумажный документооборот')])
    env.crm.counts = {D1: 34}
    item = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)["portion"]["items"][0]
    env.crm.cards = {D1: {'park_id': PARK, 'total': 34, 'created_date': None}}
    env.fleet.down = SourceError('Кабинет Fleet не ответил: 502')
    env.talk(item["assignment_id"])
    assert env.one("SELECT edm_error, edm_source FROM dial_list_leads WHERE driver_id = %s", (D1,)) == ('', 'file')
    env.fleet.down = None
    env.fleet.providers = {D1: ('Sapar', True)}
    summary = env.svc.activation_checker.run(reason='тест', now=_now() + timedelta(minutes=61))
    assert (summary["provider_checked"], summary["sapar"], summary["sapar_successes"]) == (1, 1, 1)
    # Ошибка по самому водителю (нет в парке) — в лид, и прогон не спросит его снова раньше суток.
    env.upload([_row(D2, '77001110002', 3, 'Бумажный документооборот')])
    env.q("UPDATE dial_list_leads SET fleet_park_id = %s WHERE driver_id = %s", (PARK, D2))
    env.svc.activation_checker.run(reason='тест', now=_now() + timedelta(minutes=62))
    assert env.one("SELECT edm_error <> '' FROM dial_list_leads WHERE driver_id = %s", (D2,)) == (True,)
    env.fleet.asked.clear()
    env.svc.activation_checker.run(reason='тест', now=_now() + timedelta(minutes=63))
    assert D2 not in {d for _p, d in env.fleet.asked}


def test_refresh_button_takes_the_driver_who_did_the_goal_off_the_list(env):
    env.enroll()
    env.upload([_row(D1, '77001110001', 48, 'Бумажный документооборот')])
    env.crm.counts = {D1: 48}
    item = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)["portion"]["items"][0]
    env.crm.cards = {D1: {'park_id': PARK, 'total': 50, 'created_date': None}}
    env.svc.check_lead_activation(env.lead(D1), 'trips')
    assert env.one("SELECT state FROM dial_list_assignments WHERE id = %s", (item["assignment_id"],)) == ('done',)


def test_crm_trips_win_over_the_file_and_crm_errors_are_retried(env):
    env.enroll()
    env.upload([_row(D1, '77001110001', 30, 'Бумажный документооборот')])
    env.crm.counts = {D1: 41}
    env.svc.activation_checker.run(reason='тест')
    env.upload([_row(D1, '77001110001', 34, 'Бумажный документооборот')])
    assert env.one("SELECT trips_total FROM dial_list_leads WHERE driver_id = %s", (D1,)) == (41,)
    # CRM лежит: ошибка — в лид, время проверки не двигается, следующий прогон спрашивает снова.
    env.upload([_row(D2, '77001110002', 7, 'Бумажный документооборот')])
    env.crm.down = SourceError('CRM: 503')
    env.svc.activation_checker.run(reason='тест')
    assert env.one("SELECT trip_checked_at, trip_error <> '' FROM dial_list_leads WHERE driver_id = %s",
                   (D2,)) == (None, True)
    env.crm.down = None
    env.crm.counts = {D1: 41, D2: 8}
    env.svc.activation_checker.run(reason='тест')
    assert env.one("SELECT trips_total, trip_checked_at IS NOT NULL, trip_error FROM dial_list_leads "
                   "WHERE driver_id = %s", (D2,)) == (8, True, '')


def test_refresh_button_says_why_it_did_not_refresh(env):
    """Разбор 27.11: пауза после сбоя и занятые проверки — отказ словами, а не прежняя карточка с 200."""
    env.enroll()
    env.upload([_row(D1, '77001110001', 10, 'Бумажный документооборот')])
    lead = env.lead(D1)
    checker = env.svc.activation_checker
    checker._now_backoff_until = time.monotonic() + 30
    message, status, _kind = env.refused(env.svc.check_lead_activation, lead, 'trips')
    assert status == 503 and 'после сбоя' in message
    checker._now_backoff_until = 0.0
    env.crm.down = SourceError('CRM: 503')
    message, status, _kind = env.refused(env.svc.check_lead_activation, lead, 'trips')
    assert status == 502 and message.startswith('Проверка не ответила: ')


# ── режим линий: «Активация» на линиях удалённого КЦ (владелец 26.11.2026) ─────────────────────────────────

SZOV, RCC_OWN, SZOV_GUEST = 1, 540, 541

HOSTED_DDL = '''
    INSERT INTO departments (id, name, code) VALUES (1, 'СЗоВ', 'szov');
    INSERT INTO sip_department_config VALUES (1, 'asterisk', '', NULL);
    INSERT INTO users (id, name, login, role, status, department_id, sip_number) VALUES
        (540, 'Оператор Удалённого', 'rcc', 'operator', 'working', 1954, '901'),
        (541, 'Гость СЗоВ', 'szov_guest', 'operator', 'working', 1, 'oktell_541');
    INSERT INTO dial_list_line_members (user_id, department_id, internal_number) VALUES (541, 1954, '902');
'''


class _SipDb(_Db):
    """Карточка сотрудника — как db.get_sip_operator (те поля, что читает раздел)."""

    def get_sip_operator(self, user_id):
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT u.id, u.name, u.department_id, COALESCE(dc.provider, 'asterisk'), COALESCE(u.sip_number, '')
                FROM users u LEFT JOIN sip_department_config dc ON dc.department_id = u.department_id
                WHERE u.id = %s
            """, (int(user_id),))
            r = cur.fetchone()
        if not r:
            return None
        return {"id": r[0], "name": r[1], "department_id": r[2], "department_provider": r[3], "sip_number": r[4]}

    def save_user_sip_settings(self, user_id, settings, changed_by=None):
        """Как db.save_user_sip_settings для снятия своего сотрудника с линии: номер очищается."""
        with self._get_cursor() as cur:
            cur.execute("UPDATE users SET sip_number = %s WHERE id = %s",
                        (settings.get("sip_number") or None, int(user_id)))


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setattr(dial_service, 'ANSWER_SETTLE_SEC', 0.0)
    schema = 't_dial_host_' + uuid.uuid4().hex
    db = _SipDb(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        cur.execute(HOSTED_DDL)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled, binotel_company, "
                    "caller_id_for_employee) VALUES (1954, TRUE, 'remote_cc', '100')")
    binotel, crm, fleet = _Binotel(), _Crm(), _Fleet()
    signs = SignChecker(db)
    signs.configured = lambda: False
    checker = ActivationChecker(db, crm=crm, fleet=fleet, successes=signs, sleep=lambda _s: None)
    svc = dial_service.DialListService(db, client_factory=lambda dep: binotel, sign_checker=signs,
                                       calls_submit=_sync, activation_checker=checker)
    try:
        yield _Env(db, svc, binotel, crm, fleet)
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


def _enroll_szov(env):
    settings = env.svc.enroll_department(SZOV, changed_by=1, profile='activation')
    goal = settings["trips_goal"]
    parsed, extras, _counts = activation.parse_activation_file(
        _xlsx([_row(D1, '77001110001', 34, 'Бумажный документооборот'),
               _row(D2, '77001110002', 12, 'Payda')]), '.xlsx', goal=goal)
    env.svc.import_leads(SZOV, 1, 'novички.xlsx', parsed, require_iin=False, keep_iin=True, extras=extras)
    return settings


def test_szov_is_enrolled_on_remote_cc_lines(hosted):
    svc = hosted.svc
    candidates = {d["department_id"]: d for d in svc.candidate_departments(profile='activation')}
    assert candidates[SZOV]["host_department_id"] == RCC
    assert candidates[SZOV]["host_department_name"] == 'Удаленный КЦ'
    settings = _enroll_szov(hosted)
    assert (settings["profile"], settings["host_department_id"], settings["binotel_company"],
            settings["caller_id_for_employee"], settings["enabled"]) == ('activation', RCC, 'remote_cc', '100', False)
    block = svc.line_modes_block(RCC)
    assert (block["host"]["id"], block["container"]["id"], block["hosted"], block["modes"]) == (RCC, SZOV, False, {})
    assert svc.line_modes_block(SZOV)["hosted"] is True
    # Отдел без своих линий дистрибутив телефона не получает (bot_schedule2._dial_list_department_allowed).
    assert [d["host_department_id"] for d in svc.list_departments([SZOV])] == [RCC]
    # Сотрудники «Активации» — только переключённые в её режим.
    assert svc.department_users(SZOV) == []


def test_switching_a_line_moves_the_operator_between_bases_without_touching_the_line(hosted):
    svc = hosted.svc
    _enroll_szov(hosted)
    # Своя база удалённого КЦ: оператор взял пачку и ещё не звонил.
    svc.import_leads(RCC, 1, 'rcc.xlsx', [(2, 'Водитель РКЦ', '77009990001', '77009990001', '')], require_iin=False)
    state = svc.issue_next_portion(RCC_OWN, phone_version=GUARDED)
    assert (state["profile"], [i["full_name"] for i in state["portion"]["items"]]) == ('remote_cc', ['Водитель РКЦ'])

    result = svc.set_line_modes(RCC, 'activation', user_ids=[RCC_OWN], changed_by=1)
    assert (result["changed"], result["skipped"], result["released_rows"]) == (1, [], 1)
    assert result["line_modes"]["modes"] == {str(RCC_OWN): 'activation'}
    # Линия и отдел сотрудника — прежние; работа обзвона — в «Активации».
    assert svc.dial_operator(RCC_OWN)["department_id"] == RCC
    work = svc.work_operator(RCC_OWN)
    assert (work["department_id"], work["mode_host_department_id"], work["sip_number"]) == (SZOV, RCC, '901')
    # Водитель удалённого КЦ снова в общем списке, его выдача закрыта.
    assert hosted.q("SELECT COUNT(*) FROM dial_list_assignments a JOIN dial_list_leads l ON l.id = a.lead_id "
                    "WHERE l.department_id = %s", (RCC,)) == [(0,)]
    # Телефон: профиль tez, цель activation, строка водителя — поездки и ЭДО; включён — как у отдела линии.
    hosted.crm.counts = {D1: 34, D2: 12}
    state = svc.issue_next_portion(RCC_OWN, phone_version=GUARDED)
    assert (state["profile"], state["goal"]) == ('tez', 'activation')
    assert sorted(i["driver"]["label"] for i in state["portion"]["items"]) == [
        f'12 из 50 поездок · ЭДО: Payda · Парк: Jana (Алматы) · ID: {D2}',
        f'34 из 50 поездок · ЭДО: Бумажный · Парк: Jana (Алматы) · ID: {D1}']
    block = svc.phone_settings(RCC_OWN, member=None)
    assert (block["enabled"], block["profile"], block["goal"], block["auto"], block["no_active"]) == (
        True, 'tez', 'activation', True, False)
    assert [u["id"] for u in svc.department_users(SZOV)] == [RCC_OWN]
    # Звонок — с той же линии в кабинете удалённого КЦ.
    item = state["portion"]["items"][0]
    svc.start_call(RCC_OWN, item["assignment_id"], operator_status='outbound')
    assert hosted.binotel.originated[-1][0] == '901'


def test_busy_operator_is_not_switched_and_all_lines_switch_back(hosted):
    svc = hosted.svc
    _enroll_szov(hosted)
    res = svc.set_line_modes(SZOV, 'activation', all_lines=True, changed_by=1)
    assert res["changed"] == 2 and res["line_modes"]["hosted"] is True
    assert svc.work_operator(SZOV_GUEST)["department_id"] == SZOV
    # Гость СЗоВ в разговоре — назад в удалённый КЦ его не переключаем, остальных — да.
    hosted.crm.counts = {D1: 34, D2: 12}
    item = svc.issue_next_portion(SZOV_GUEST, phone_version=GUARDED)["portion"]["items"][0]
    svc.start_call(SZOV_GUEST, item["assignment_id"], operator_status='outbound')
    res = svc.set_line_modes(RCC, 'host', all_lines=True, changed_by=1)
    assert res["changed"] == 1 and [s["user_id"] for s in res["skipped"]] == [SZOV_GUEST]
    assert res["skipped"][0]["reason"] == dial_service.DialListService.MODE_BUSY
    assert svc.work_operator(RCC_OWN)["department_id"] == RCC
    assert svc.work_operator(SZOV_GUEST)["department_id"] == SZOV
    # Чужой отделу человек и неверный режим — отказ.
    assert hosted.refused(svc.set_line_modes, RCC, 'activation', user_ids=[530])[1] == 404
    assert hosted.refused(svc.set_line_modes, RCC, 'nope', user_ids=[RCC_OWN])[1] == 400


def test_release_from_the_line_clears_the_mode(hosted):
    svc = hosted.svc
    _enroll_szov(hosted)
    svc.set_line_modes(RCC, 'activation', user_ids=[SZOV_GUEST], changed_by=1)
    assert svc.operator_mode(SZOV_GUEST) == (SZOV, RCC)
    svc.release_line(RCC, SZOV_GUEST, changed_by=1, seat_anyone=True)
    assert svc.operator_mode(SZOV_GUEST) is None
    # Снят с линии — своя телефония отдела (не Binotel): в обзвоне его нет.
    assert svc.work_operator(SZOV_GUEST)["department_id"] == SZOV
    assert svc.work_operator(SZOV_GUEST)["department_provider"] == 'asterisk'
    # Режим, оставшийся от другой линии, не действует: сверяется отдел линии.
    hosted.q("INSERT INTO dial_list_operator_modes (user_id, department_id, host_department_id) VALUES (%s, %s, %s)",
             (RCC_OWN, SZOV, 9999))
    assert svc.work_operator(RCC_OWN)["department_id"] == RCC


def _talk(env, user_id, assignment_id, billsec=30):
    """Разговор billsec секунд до финала Binotel, без итога. Возвращает попытку."""
    svc = env.svc
    attempt = svc.start_call(user_id, assignment_id, operator_status='outbound')
    svc.phone_event(user_id, attempt["attempt_id"], 'ringing', phone_version=GUARDED)
    svc.phone_event(user_id, attempt["attempt_id"], 'answered', phone_version=GUARDED)
    svc.phone_event(user_id, attempt["attempt_id"], 'ended', by_operator=False, leg_sec=billsec, talk_detected=True,
                    phone_version=GUARDED)
    env.binotel.live[attempt["general_call_id"]] = {"disposition": 'ANSWER', "billsec": billsec, "waitsec": 5}
    env.poll_now()
    svc.get_state(user_id, phone_version=GUARDED)
    return attempt


def _talk_with_outcome(env, user_id, assignment_id, department_id, outcome_name):
    """Разговор 30 с и итог outcome_name из справочника отдела department_id."""
    attempt = _talk(env, user_id, assignment_id)
    outcome = str(env.one("SELECT id FROM dial_list_outcomes WHERE department_id = %s AND name = %s",
                          (department_id, outcome_name))[0])
    env.svc.set_attempt_outcome(user_id, attempt["attempt_id"], outcome)


def test_phone_tabs_and_progress_show_only_the_current_mode(hosted):
    """Владелец 26.11.2026: «что отображается в iCORE Phone, зависит от включённого режима» — вкладки итогов и
    «Мой прогресс» оператора, который работал в обоих режимах, показывают только обзвон нынешнего режима."""
    svc = hosted.svc
    _enroll_szov(hosted)
    hosted.q("INSERT INTO dial_list_outcomes (department_id, name, position) VALUES (%s, 'Отказ РКЦ', 1)", (RCC,))
    svc.import_leads(RCC, 1, 'rcc.xlsx', [(2, 'Водитель РКЦ', '77009990001', '77009990001', '')], require_iin=False)
    item = svc.issue_next_portion(RCC_OWN, phone_version=GUARDED)["portion"]["items"][0]
    _talk_with_outcome(hosted, RCC_OWN, item["assignment_id"], RCC, 'Отказ РКЦ')

    svc.set_line_modes(RCC, 'activation', user_ids=[RCC_OWN], changed_by=1)
    hosted.crm.counts = {D1: 34, D2: 12}
    items = svc.issue_next_portion(RCC_OWN, phone_version=GUARDED)["portion"]["items"]
    first = next(i for i in items if i["full_name"] == 'Тестов Тест')
    _talk_with_outcome(hosted, RCC_OWN, first["assignment_id"], SZOV, 'Работает в другом парке')

    def phone_view():
        worked = svc.operator_worked(RCC_OWN)
        progress = svc.operator_progress(RCC_OWN)
        return ({t["name"] for t in worked["tabs"] if t["count"]}, len(worked["items"]),
                [o["name"] for o in progress["outcomes"]], progress["month"]["attempts"])

    # В «Активации» — только её водитель, её итоги и её звонок.
    assert phone_view() == ({'Работает в другом парке'}, 1, ['Работает в другом парке'], 1)
    assert 'Отказ РКЦ' not in {t["name"] for t in svc.operator_worked(RCC_OWN)["tabs"]}

    # Назад в удалённый КЦ — только его.
    svc.set_line_modes(RCC, 'host', user_ids=[RCC_OWN], changed_by=1)
    assert phone_view() == ({'Отказ РКЦ'}, 1, ['Отказ РКЦ'], 1)
    assert 'Работает в другом парке' not in {t["name"] for t in svc.operator_worked(RCC_OWN)["tabs"]}

    # Отдел без режима линий: водители отдела, где оператор работал раньше, на вкладках остаются (как было).
    hosted.q("DELETE FROM dial_list_department_settings WHERE department_id = %s", (SZOV,))
    with hosted.db._get_cursor() as cur:
        assert svc._other_mode_departments(cur, {"department_id": RCC}) == []
    assert len(svc.operator_worked(RCC_OWN)["items"]) == 2


def _rcc_driver(env):
    env.q("INSERT INTO dial_list_outcomes (department_id, name, position) VALUES (%s, 'Отказ РКЦ', 1)", (RCC,))
    env.svc.import_leads(RCC, 1, 'rcc.xlsx', [(2, 'Водитель РКЦ', '77009990001', '77009990001', '')],
                         require_iin=False)


def test_long_call_and_its_missing_outcome_hold_the_mode(hosted):
    """Разбор 27.11: разговор дольше 3 минут (окно «Позвонить») режим держит, как и неставленный итог."""
    svc = hosted.svc
    _enroll_szov(hosted)
    _rcc_driver(hosted)
    item = svc.issue_next_portion(RCC_OWN, phone_version=GUARDED)["portion"]["items"][0]
    attempt = svc.start_call(RCC_OWN, item["assignment_id"], operator_status='outbound')
    svc.phone_event(RCC_OWN, attempt["attempt_id"], 'ringing', phone_version=GUARDED)
    svc.phone_event(RCC_OWN, attempt["attempt_id"], 'answered', phone_version=GUARDED)
    hosted.q("UPDATE dial_list_attempts SET requested_at = requested_at - interval '4 minutes' WHERE id = %s",
             (attempt["attempt_id"],))
    res = svc.set_line_modes(RCC, 'activation', user_ids=[RCC_OWN], changed_by=1)
    assert (res["changed"], [s["user_id"] for s in res["skipped"]]) == (0, [RCC_OWN])
    # Разговор кончился, итога нет — тоже занят.
    svc.phone_event(RCC_OWN, attempt["attempt_id"], 'ended', by_operator=False, leg_sec=300, talk_detected=True,
                    phone_version=GUARDED)
    hosted.binotel.live[attempt["general_call_id"]] = {"disposition": 'ANSWER', "billsec": 300, "waitsec": 5}
    hosted.poll_now()
    svc.get_state(RCC_OWN, phone_version=GUARDED)
    assert svc.set_line_modes(RCC, 'activation', user_ids=[RCC_OWN], changed_by=1)["changed"] == 0
    outcome = str(hosted.one("SELECT id FROM dial_list_outcomes WHERE department_id = %s", (RCC,))[0])
    svc.set_attempt_outcome(RCC_OWN, attempt["attempt_id"], outcome)
    assert svc.set_line_modes(RCC, 'activation', user_ids=[RCC_OWN], changed_by=1)["changed"] == 1


def test_release_and_reseat_keep_the_mode_while_the_outcome_is_owed(hosted):
    """Разбор 27.11: снятие и посадка режим снимают, но не с того, кто должен итог: итог «Активации» ставят из её
    справочника, а не удалённого КЦ."""
    svc = hosted.svc
    _enroll_szov(hosted)
    svc.set_line_modes(RCC, 'activation', user_ids=[SZOV_GUEST], changed_by=1)
    hosted.crm.counts = {D1: 34, D2: 12}
    item = svc.issue_next_portion(SZOV_GUEST, phone_version=GUARDED)["portion"]["items"][0]
    attempt = _talk(hosted, SZOV_GUEST, item["assignment_id"])
    svc.release_line(RCC, SZOV_GUEST, changed_by=1, seat_anyone=True)
    assert svc.operator_mode(SZOV_GUEST) == (SZOV, RCC)
    svc._binotel_employees = lambda dep: [{"endpointData": {"internalNumber": '903', "login": 'l903',
                                                            "password": 'p'}}]
    svc.assign_line(RCC, SZOV_GUEST, '903', changed_by=1, seat_anyone=True)
    assert svc.operator_mode(SZOV_GUEST) == (SZOV, RCC)
    state = svc.get_state(SZOV_GUEST, phone_version=GUARDED)
    assert (state["profile"], state["pending_outcome"]["attempt_id"]) == ('tez', attempt["attempt_id"])
    assert 'Работает в другом парке' in [o["name"] for o in state["outcomes"]]
    outcome = str(hosted.one("SELECT id FROM dial_list_outcomes WHERE department_id = %s AND name = %s",
                             (SZOV, 'Работает в другом парке'))[0])
    svc.set_attempt_outcome(SZOV_GUEST, attempt["attempt_id"], outcome)
    # Итог поставлен — снятие режим снимает.
    svc.release_line(RCC, SZOV_GUEST, changed_by=1, seat_anyone=True)
    assert svc.operator_mode(SZOV_GUEST) is None
    # Свой сотрудник удалённого КЦ (снятие — очисткой его SIP-настроек) — тоже.
    svc.set_line_modes(RCC, 'activation', user_ids=[RCC_OWN], changed_by=1)
    svc.release_line(RCC, RCC_OWN, changed_by=1)
    assert svc.operator_mode(RCC_OWN) is None


def test_mode_switch_waits_for_a_call_being_placed(hosted):
    """Разбор 27.11: «Позвонить» держит замок оператора — переключение ждёт, видит звонок и оператора пропускает
    (раньше уводило его в «Активацию» посреди набора водителя удалённого КЦ)."""
    svc = hosted.svc
    _enroll_szov(hosted)
    _rcc_driver(hosted)
    item = svc.issue_next_portion(RCC_OWN, phone_version=GUARDED)["portion"]["items"][0]
    inside, release = threading.Event(), threading.Event()
    original = svc._require_outcome_done

    def held(cur, ctx):
        original(cur, ctx)
        inside.set()
        release.wait(10)

    svc._require_outcome_done = held
    results = {}
    call = threading.Thread(target=lambda: results.setdefault(
        "call", svc.start_call(RCC_OWN, item["assignment_id"], operator_status='outbound')))
    switch = threading.Thread(target=lambda: results.setdefault(
        "switch", svc.set_line_modes(RCC, 'activation', user_ids=[RCC_OWN], changed_by=1)))
    call.start()
    try:
        assert inside.wait(10)
        svc._require_outcome_done = original
        switch.start()
        switch.join(1.0)
        assert switch.is_alive(), 'переключение ждёт, пока звонок записывается'
    finally:
        # Звонок отпускаем при любом исходе: иначе его транзакция держала бы соединение до конца теста.
        release.set()
        call.join(10)
        if switch.ident is not None:
            switch.join(10)
    assert results["call"]["attempt_id"]
    assert (results["switch"]["changed"], [s["user_id"] for s in results["switch"]["skipped"]]) == (0, [RCC_OWN])
    assert svc.operator_mode(RCC_OWN) is None


def test_line_settings_follow_the_line_in_activation_mode(hosted):
    """Разбор 27.11: гудки до сброса — настройка линии удалённого КЦ и в режиме «Активация» (у СЗоВ своя телефония)."""
    svc = hosted.svc
    _enroll_szov(hosted)
    hosted.q("UPDATE sip_department_config SET ring_timeout_rings = 0 WHERE department_id = %s", (RCC,))
    hosted.q("UPDATE sip_department_config SET ring_timeout_rings = 7 WHERE department_id = %s", (SZOV,))
    assert svc.get_state(RCC_OWN, phone_version=GUARDED)["ring_timeout_sec"] == 0
    svc.set_line_modes(RCC, 'activation', user_ids=[RCC_OWN], changed_by=1)
    assert svc.get_state(RCC_OWN, phone_version=GUARDED)["ring_timeout_sec"] == 0
    ctx = svc.operator_context(RCC_OWN)
    assert (ctx["department_id"], svc._line_department(ctx)) == (SZOV, RCC)


# ── вкладки итогов: у звонка три цели (владелец 27.11.2026) ─────────────────────────────────────────────────

TABS_PHONE = '3.22.67'


def _outcome_id(env, name, group):
    return str(env.one("SELECT id FROM dial_list_outcomes WHERE department_id = %s AND name = %s AND group_key = %s",
                       (ACT, name, group))[0])


def _tabs_talk(env, driver_id=D1, trips=34, phone='77001110001'):
    """Водитель в пачке и разговор с ним, итога ещё нет. Возвращает (попытку, лид)."""
    env.upload([_row(driver_id, phone, trips, 'Бумажный документооборот')])
    env.crm.counts = {driver_id: trips}
    item = env.svc.issue_next_portion(OPERATOR, phone_version=TABS_PHONE)["portion"]["items"][0]
    return _talk(env, OPERATOR, item["assignment_id"]), env.lead(driver_id)


def test_activation_gets_three_required_tabs_and_new_phone_sees_them(env):
    env.enroll()
    groups = env.svc.department_settings(ACT)["outcome_groups"]
    assert groups == [{"key": 'trips', "name": 'Поездки', "required": True},
                      {"key": 'provider', "name": 'Провайдер ЭДО', "required": True},
                      {"key": 'promo', "name": 'Акции', "required": True}]
    by_group = {}
    for o in env.svc.outcomes(ACT):
        by_group.setdefault(o["group"], []).append(o["name"])
    assert by_group == {
        'trips': ['Будет ездить', 'Перезвонить', 'Отказ', 'Неверный номер', 'Работает в другом парке'],
        'provider': ['Перейдёт на Sapar', 'Уже на Sapar', 'Подумает', 'Отказался переходить', 'Не обсуждали'],
        'promo': ['Рассказали', 'Не интересно', 'Не успели рассказать']}
    env.upload([_row(D1, '77001110001', 34, 'Бумажный документооборот')])
    env.crm.counts = {D1: 34}
    # Телефон до 3.22.67 вкладок не знает: ему — итоги главной вкладки, без group, и вкладок в состоянии нет.
    old = env.svc.issue_next_portion(OPERATOR, phone_version='3.22.66')
    assert [o["name"] for o in old["outcomes"]] == by_group['trips']
    assert 'group' not in old["outcomes"][0] and 'outcome_groups' not in old
    new = env.svc.get_state(OPERATOR, phone_version=TABS_PHONE)
    assert new["outcome_groups"] == groups
    assert [(o["group"], o["name"]) for o in new["outcomes"]][:6] == [
        ('trips', 'Будет ездить'), ('trips', 'Перезвонить'), ('trips', 'Отказ'), ('trips', 'Неверный номер'),
        ('trips', 'Работает в другом парке'), ('provider', 'Перейдёт на Sapar')]
    assert len(new["outcomes"]) == 13


def test_outcome_of_each_tab_is_checked_and_saved_with_the_attempt(env):
    env.enroll()
    attempt, lead = _tabs_talk(env)
    att = attempt["attempt_id"]
    refusal = _outcome_id(env, 'Отказ', 'trips')
    sapar = _outcome_id(env, 'Перейдёт на Sapar', 'provider')
    thinks = _outcome_id(env, 'Подумает', 'provider')
    told = _outcome_id(env, 'Рассказали', 'promo')
    # Тип у итога вкладки «Провайдер ЭДО» — обязателен, если у итога типы есть.
    (sub_id,) = env.one("INSERT INTO dial_list_outcome_subtypes (outcome_id, department_id, name) "
                        "VALUES (%s, %s, 'Через неделю') RETURNING id", (thinks, ACT))
    sub_id = str(sub_id)

    def save(main, extra):
        return env.refused(env.svc.set_attempt_outcome, OPERATOR, att, main, group_outcomes=extra)

    # Главный итог — только с главной вкладки; без обязательной вкладки, два итога на одной, главный среди
    # остальных, чужой тип, тип не выбран — 400 словами, и ничего не записано.
    assert save(sapar, [])[:2] == ('Выберите итог на вкладке «Поездки»', 400)
    assert save(refusal, [{"outcome_id": sapar}])[:2] == ('Выберите итог на вкладке «Акции»', 400)
    assert save(refusal, [{"outcome_id": sapar}, {"outcome_id": thinks, "subtype_id": sub_id},
                          {"outcome_id": told}])[:2] == ('На вкладке «Провайдер ЭДО» выберите один итог', 400)
    assert save(refusal, [{"outcome_id": refusal}, {"outcome_id": told}])[:2] == (
        'Итог «Отказ» — с другой вкладки, обновите список', 400)
    assert save(refusal, [{"outcome_id": thinks}, {"outcome_id": told}])[:2] == (
        'Выберите тип итога «Подумает» на вкладке «Провайдер ЭДО»', 400)
    assert save(refusal, [{"outcome_id": sapar, "subtype_id": sub_id}, {"outcome_id": told}])[:2] == (
        'Такого типа итога нет — обновите список', 400)
    assert save(refusal, [{"outcome_id": 'не uuid'}, {"outcome_id": told}])[:2] == (
        'Такого итога нет — обновите список', 400)
    assert save(refusal, {"outcome_id": sapar})[:2] == ('group_outcomes: ожидается список итогов вкладок', 400)
    assert env.one("SELECT outcome_id FROM dial_list_attempts WHERE id = %s", (att,)) == (None,)
    assert env.q("SELECT 1 FROM dial_list_attempt_outcomes") == []

    assert save(refusal, [{"outcome_id": thinks, "subtype_id": sub_id}, {"outcome_id": told}]) is None
    assert env.q("SELECT group_key, outcome_id::text, outcome_subtype_id::text FROM dial_list_attempt_outcomes "
                 "WHERE attempt_id = %s ORDER BY group_key", (att,)) == [
        ('promo', told, None), ('provider', thinks, sub_id)]
    # Повторное сохранение заменяет итоги вкладок целиком.
    assert save(refusal, [{"outcome_id": sapar}, {"outcome_id": told}]) is None
    assert env.q("SELECT group_key, outcome_id::text FROM dial_list_attempt_outcomes WHERE attempt_id = %s "
                 "ORDER BY group_key", (att,)) == [('promo', told), ('provider', sapar)]

    # Карточка водителя: итоги вкладок у звонка — по порядку вкладок.
    card = env.svc.lead_card(lead)
    assert [(g["group"], g["group_name"], g["name"]) for g in card["attempts"][0]["group_outcomes"]] == [
        ('provider', 'Провайдер ЭДО', 'Перейдёт на Sapar'), ('promo', 'Акции', 'Рассказали')]
    # Аналитика: по каждой неглавной вкладке — итоги и их число; главная — в основном дашборде.
    tabs = env.svc.analytics(ACT)["outcome_groups"]
    assert [(t["key"], t["total"], [(i["name"], i["count"]) for i in t["items"]]) for t in tabs] == [
        ('provider', 1, [('Перейдёт на Sapar', 1)]), ('promo', 1, [('Рассказали', 1)])]
    # Справочник считает и выбор на вкладках: выбранный итог редактор не спутает с невыбранным.
    used = {o["name"]: o["used"] for o in env.svc.outcomes(ACT)}
    assert (used['Перейдёт на Sapar'], used['Рассказали'], used['Отказ'], used['Подумает']) == (1, 1, 1, 0)
    # Вкладки отработанных в телефоне — по итогу главной вкладки (и служебная «Не дозвонились»): итогов
    # «Провайдера ЭДО» и «Акций» там нет, звонок — под «Отказом».
    worked = env.svc.operator_worked(OPERATOR)["tabs"]
    assert not {t["name"] for t in worked} & {'Перейдёт на Sapar', 'Подумает', 'Рассказали', 'Не интересно'}
    assert {t["name"]: t["count"] for t in worked}['Отказ'] == 1


def test_old_phone_saves_only_the_main_outcome_and_optional_tab_may_stay_empty(env):
    env.enroll()
    attempt, lead = _tabs_talk(env)
    refusal = _outcome_id(env, 'Отказ', 'trips')
    # Телефон без вкладок ключа group_outcomes не шлёт — обязательность вкладок не проверяется.
    env.svc.set_attempt_outcome(OPERATOR, attempt["attempt_id"], refusal)
    assert env.one("SELECT outcome_id::text FROM dial_list_attempts WHERE id = %s", (attempt["attempt_id"],)) == (
        refusal,)
    assert env.q("SELECT 1 FROM dial_list_attempt_outcomes") == []
    assert env.svc.lead_card(lead)["attempts"][0]["group_outcomes"] == []
    # Необязательная вкладка: без её итога сохранить можно.
    rows, version = env.svc.outcomes(ACT, with_version=True)
    env.svc.save_outcomes(ACT, [{"id": o["id"], "name": o["name"]} for o in rows], version=version, groups=[
        {"key": 'trips', "name": 'Поездки'}, {"key": 'provider', "name": 'Провайдер ЭДО'},
        {"key": 'promo', "name": 'Акции', "required": False}])
    attempt2, _lead2 = _tabs_talk(env, D2, 12, '77001110002')
    assert env.refused(env.svc.set_attempt_outcome, OPERATOR, attempt2["attempt_id"], refusal,
                       group_outcomes=[{"outcome_id": _outcome_id(env, 'Уже на Sapar', 'provider')}]) is None


def test_editor_moves_outcomes_between_tabs_and_guards_each_tab(env):
    env.enroll()
    rows, version = env.svc.outcomes(ACT, with_version=True)
    groups = env.svc.department_settings(ACT)["outcome_groups"]
    items = [{"id": o["id"], "name": o["name"], "color": o["color"], "is_active": o["is_active"],
              "group": o["group"]} for o in rows]

    def put(new_items, new_groups=dial_service.VALUE_NOT_SENT, ver=None):
        kw = {} if new_groups is dial_service.VALUE_NOT_SENT else {"groups": new_groups}
        return env.refused(env.svc.save_outcomes, ACT, new_items, version=ver, with_version=True, **kw)

    # Одинаковое название — на разных вкладках можно, на одной — нет.
    assert put(items + [{"name": 'Отказ', "group": 'promo'}]) is None
    rows, version = env.svc.outcomes(ACT, with_version=True)
    assert sorted(o["group"] for o in rows if o["name"] == 'Отказ') == ['promo', 'trips']
    items = [{"id": o["id"], "name": o["name"], "is_active": o["is_active"], "group": o["group"]} for o in rows]
    assert put(items + [{"name": 'Рассказали', "group": 'promo'}])[0] == 'Итог «Рассказали» повторяется'
    # Вкладки нет у отдела — отказ; новый итог без вкладки — на главную; без ключа group — вкладка прежняя.
    assert put(items + [{"name": 'Новый', "group": 'nope'}])[0] == 'Итог «Новый»: такой вкладки итогов у отдела нет'
    keep = [{"id": o["id"], "name": o["name"], "is_active": o["is_active"]} for o in rows]
    assert put(keep + [{"name": 'Без вкладки'}]) is None
    groups_of = {(o["name"], o["group"]) for o in env.svc.outcomes(ACT)}
    assert ('Без вкладки', 'trips') in groups_of and ('Рассказали', 'promo') in groups_of
    # На обязательной вкладке не осталось включённых итогов — отказ; вкладку сделали необязательной — можно.
    rows, version = env.svc.outcomes(ACT, with_version=True)
    no_promo = [{"id": o["id"], "name": o["name"], "group": o["group"],
                 "is_active": o["is_active"] and o["group"] != 'promo'} for o in rows]
    assert put(no_promo)[0] == 'На вкладке «Акции» нет ни одного включённого итога'
    optional = [dict(g, required=g["key"] != 'promo') for g in groups]
    assert put(no_promo, optional) is None
    assert env.svc.department_settings(ACT)["outcome_groups"][2] == {"key": 'promo', "name": 'Акции',
                                                                     "required": False}
    # Вкладки — тоже содержимое справочника: их правка меняет версию (открытый редактор получит 409). Итоги уходят
    # целиком, как их шлёт сайт: меняется только название вкладки.
    rows, version = env.svc.outcomes(ACT, with_version=True)
    rows = sorted(rows, key=lambda o: o["position"])   # выдача — включённые первыми; порядок номеров — свой
    same_rows = [{"id": o["id"], "name": o["name"], "color": o["color"], "requeue": o["requeue"],
                  "is_active": o["is_active"], "due_kind": o["due_kind"], "group": o["group"]} for o in rows]
    renamed = [dict(g, name='Акции месяца') if g["key"] == 'promo' else g for g in optional]
    assert put(same_rows, renamed, ver=version) is None
    look = lambda items: sorted((o["id"], o["name"], o["color"], o["requeue"], o["is_active"], o["group"])  # noqa: E731
                                for o in items)
    assert look(env.svc.outcomes(ACT)) == look(rows), 'итоги не тронуты — изменилась только вкладка'
    assert put(same_rows, optional, ver=version)[1:] == (409, 'stale')
    # Главная вкладка без включённых итогов — отказ, даже необязательная.
    no_main = [dict(i, is_active=i["is_active"] and o["group"] != 'trips') for i, o in zip(same_rows, rows)]
    assert put(no_main, [dict(g, required=False) for g in renamed])[0] == (
        'На вкладке «Поездки» нет ни одного включённого итога')
    # Ключи вкладок — латиница и без повторов; пустое название — отказ.
    assert put(same_rows, [{"key": 'Поездки', "name": 'x'}])[0] == 'Ключ вкладки — латиница и цифры, с буквы'
    assert put(same_rows, [{"key": 'trips', "name": 'a'}, {"key": 'trips', "name": 'b'}])[0] == (
        'Вкладки итогов повторяются')
    assert put(same_rows, [{"key": 'trips', "name": '  '}])[0] == 'У вкладки итогов должно быть название'
    # Вкладки убрали совсем — справочник снова один: одинаковые названия с разных вкладок теперь повтор; без
    # повтора у итогов вкладки нет, телефону — все включённые итоги.
    assert put(same_rows, [])[0] == 'Итог «Отказ» повторяется'
    unique = [dict(i, name='Отказ от акций') if i["name"] == 'Отказ' and o["group"] == 'promo' else i
              for i, o in zip(same_rows, rows)]
    assert put(unique, []) is None
    assert env.svc.department_settings(ACT)["outcome_groups"] == []
    assert {o["group"] for o in env.svc.outcomes(ACT)} == {''}
    env.upload([_row(D1, '77001110001', 34, 'Бумажный документооборот')])
    env.crm.counts = {D1: 34}
    state = env.svc.issue_next_portion(OPERATOR, phone_version=TABS_PHONE)
    assert 'outcome_groups' not in state and 'group' not in state["outcomes"][0]
    assert len(state["outcomes"]) == len([o for o in env.svc.outcomes(ACT) if o["is_active"]])


def test_tez_seed_clears_activation_tabs(env):
    """Стартовый набор Тез — без вкладок: прежние вкладки «Активации» спрятали бы от старого телефона все итоги
    (их вкладка '' не главная)."""
    env.enroll()
    assert env.svc.department_settings(ACT)["outcome_groups"]
    env.q("DELETE FROM dial_list_outcomes WHERE department_id = %s", (ACT,))
    with env.db._get_cursor() as cur:
        env.svc._seed_outcomes(cur, ACT, profile='tez')
    assert env.svc.department_settings(ACT)["outcome_groups"] == []
    assert {o["group"] for o in env.svc.outcomes(ACT)} == {''}
