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
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import activation  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402
from dial_list.activation_check import ActivationChecker  # noqa: E402
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
    """CRM без сети: counts — ID → поездок (/partners/orders); cards — ID → карточка driver-info."""

    def __init__(self):
        self.counts, self.cards, self.asked = {}, {}, []

    def configured(self):
        return True

    def orders(self, account_ids, date_from, date_to, timeout=None):
        self.asked.append(('orders', sorted(account_ids), date_from, date_to))
        return {i: {'count': self.counts[i], 'last_order_at': None} for i in account_ids if i in self.counts}

    def driver(self, account_id, timeout=None):
        self.asked.append(('driver', account_id))
        return self.cards.get(account_id)


class _Fleet:
    """Кабинет Fleet без сети: providers — ID → (имя, есть ли карточка в парке)."""

    last_error = ''

    def __init__(self):
        self.providers, self.asked = {}, []

    def configured(self):
        return True

    def provider(self, park_id, driver_id):
        self.asked.append((park_id, driver_id))
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
        # Итог разговора — «Перейдёт на Sapar» (без даты): без итога следующий звонок сервер не даст.
        outcome = str(self.one("SELECT id FROM dial_list_outcomes WHERE department_id = %s AND name = %s",
                               (ACT, 'Перейдёт на Sapar'))[0])
        self.svc.set_attempt_outcome(OPERATOR, attempt["attempt_id"], outcome)
        return attempt

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
    assert by_name[l1]["driver"]["label"] == '34 из 50 поездок · ЭДО: Бумажный'
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
    assert (item["driver"]["label"], item["driver"]["city"]) == ('34 из 50 поездок · ЭДО: Бумажный', 'Алматы')
    assert '77001110001' not in json.dumps(state, ensure_ascii=False)
    # Звонок — через АТС, номер телефону не уходит. Поездки, проверенные при выдаче, свежее 15 минут — не
    # переспрашиваются; час назад — перед звонком освежаются карточкой CRM.
    env.q("UPDATE dial_list_leads SET trip_checked_at = trip_checked_at - interval '1 hour'")
    env.crm.cards = {D1: {'park_id': PARK, 'total': 35, 'created_date': None}}
    call = env.svc.start_call(OPERATOR, item["assignment_id"], operator_status='outbound', phone_version='3.22.66')
    assert 'dial' not in call and env.binotel.originated == [('911', '77001110001')]
    active = env.svc.get_state(OPERATOR, phone_version='3.22.66')["active_attempt"]
    assert active["driver"]["label"] == '35 из 50 поездок · ЭДО: Бумажный'
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
        '12 из 50 поездок · ЭДО: Payda', '34 из 50 поездок · ЭДО: Бумажный']
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
