# -*- coding: utf-8 -*-
"""Автообзвон Тез КЦ (15.10.2026) на настоящем PostgreSQL: перевод отдела из удалённого КЦ,
база без ИИН, проверка поездок в TEZ APP, автоцикл, итоги с датой, офферы, WhatsApp, сводки
руководителя. Binotel, TEZ APP и ChatApp — подделки без сети.

Запуск: DIAL_LIST_TEST_PORT=<порт локального PostgreSQL> pytest tests/test_dial_list_tez_postgres.py
(пользователь postgres без пароля; каждый тест — в своей схеме, схема удаляется)."""
import contextlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402
from dial_list.sign_check import SignChecker  # noqa: E402

PORT = os.environ.get('DIAL_LIST_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DIAL_LIST_TEST_PORT)')

TEZ, RCC = 560, 1954
OPERATOR, OTHER = 530, 531
GUARDED = '3.22.36'

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE test_phone_numbers (phone_key varchar(10) UNIQUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text, rate numeric);
    CREATE TABLE sip_department_config (department_id integer PRIMARY KEY, provider text,
                                        sip_server text, ring_timeout_rings smallint);
    INSERT INTO departments (id, name, code) VALUES (560, 'Тез КЦ', 'tez'), (1954, 'Удаленный КЦ', 'remote_cc');
    INSERT INTO sip_department_config VALUES (560, 'binotel', 'sip53.binotel.com', NULL),
                                             (1954, 'binotel', 'sip53.binotel.com', NULL);
    INSERT INTO users (id, name, login, role, status, department_id, sip_number) VALUES
        (1, 'Админ', 'admin', 'admin', 'working', NULL, NULL),
        (530, 'Ашимова Айгерим', 'aigerim', 'operator', 'working', 560, '601'),
        (531, 'Серикова Сауле', 'saule', 'operator', 'working', 560, '602');
'''


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


class _TezApp:
    """TEZ APP /drivers/first-orders без сети: answers — номер → {'month', 'prev', 'last_before'}."""

    def __init__(self):
        self.answers, self.asked = {}, []

    def fetch(self, phones, month, timeout=None):
        self.asked.append((sorted(phones), month, timeout))
        return {p: self.answers[p] for p in phones if p in self.answers}


class _ChatApp:
    def __init__(self):
        self.sent = []

    def active_messenger_licenses(self, only_license_ids=None):
        return [(70651, 'caWhatsApp', 'Отдел продаж', '77000000000'), (70652, 'telegram', 'Бот', None)]

    def list_templates(self, license_id, messenger_type='caWhatsApp'):
        return [{"id": "tpl-bonus", "elementName": "bonus", "data": "Здравствуйте, {{1}}! Бонус за выход",
                 "languageCode": "ru", "status": "APPROVED", "templateType": "TEXT"}]

    def send_template(self, license_id, phone, template_id, params=None, tracking=None,
                      messenger_type='caWhatsApp'):
        self.sent.append((license_id, phone, template_id, list(params or [])))
        return 'wa-%d' % len(self.sent)


def _sync(fn):
    fn()


class _Env:
    def __init__(self, db, svc, binotel, tez, chatapp):
        self.db, self.svc, self.binotel, self.tez, self.chatapp = db, svc, binotel, tez, chatapp

    def q(self, sql, params=None):
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def one(self, sql, params=None):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def enroll(self, **settings):
        """Тез КЦ — как на проде 01.10.2026 (удалённый КЦ без данных) — переводит админ."""
        self.svc.enroll_department(TEZ, changed_by=1, profile='tez')
        if settings:
            self.svc.save_department_settings(TEZ, settings, changed_by=1)

    def leads(self, count, start=1):
        rows = [(i, f'Водитель{i} Имя{i}', f'8701000{i:04d}', f'7701000{i:04d}')
                for i in range(start, start + count)]
        self.svc.import_leads(TEZ, 1, 'tez.xlsx', rows, require_iin=False)
        return {r[3]: str(self.one("SELECT id FROM dial_list_leads WHERE phone_norm = %s", (r[3],))[0])
                for r in rows}

    def outcome(self, name):
        return str(self.one("SELECT id FROM dial_list_outcomes WHERE department_id = %s AND name = %s",
                            (TEZ, name))[0])

    def binotel_says(self, attempt, disposition, billsec=0, waitsec=7):
        self.binotel.live[attempt["general_call_id"]] = {"disposition": disposition, "billsec": billsec,
                                                         "waitsec": waitsec}

    def poll_now(self):
        for calls in self.svc._calls.values():
            with calls._lock:
                calls._entries.clear()
                calls._last_start = None
                calls._not_before = 0.0

    def finish(self, attempt, disposition, billsec=0, operator=OPERATOR, talk=None):
        """Плечо кончилось на телефоне, финал — от Binotel (опрос в get_state)."""
        self.svc.phone_event(operator, attempt["attempt_id"], 'ringing', phone_version=GUARDED)
        if billsec:
            self.svc.phone_event(operator, attempt["attempt_id"], 'answered', phone_version=GUARDED)
        self.svc.phone_event(operator, attempt["attempt_id"], 'ended', by_operator=False, leg_sec=billsec or 20,
                             talk_detected=talk if talk is not None else bool(billsec), phone_version=GUARDED)
        self.binotel_says(attempt, disposition, billsec)
        self.poll_now()
        return self.svc.get_state(operator, phone_version=GUARDED)

    def refused(self, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except dial_service.DialListError as exc:
            return str(exc), exc.status, exc.kind
        return None


@pytest.fixture
def env():
    schema = 't_dial_tez_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        # Как на проде: Тез КЦ подключён к удалённому КЦ 01.10.2026 (компания 'remote_cc', режим
        # выключен, стандартные итоги) и не обзвонил никого; у удалённого КЦ база есть.
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled, binotel_company) "
                    "VALUES (560, FALSE, 'remote_cc'), (1954, TRUE, 'remote_cc')")
        cur.execute("INSERT INTO dial_list_outcomes (department_id, name, position, requeue) VALUES "
                    "(560, 'Заинтересован', 1, FALSE), (560, 'Отказ', 2, FALSE), "
                    "(560, 'Перезвонить позже', 3, TRUE), (560, 'Неверный номер', 4, FALSE)")
        cur.execute("INSERT INTO dial_list_leads (department_id, phone_norm, full_name, iin) "
                    "VALUES (1954, '77019999999', 'Чужой Водитель', '')")
    binotel, tez, chatapp = _Binotel(), _TezApp(), _ChatApp()
    svc = dial_service.DialListService(db, client_factory=lambda dep: binotel, sign_checker=SignChecker(db),
                                       calls_submit=_sync, trips_fetch=tez.fetch, trips_configured=lambda: True,
                                       wa_client_factory=lambda: chatapp)
    svc.operator_context = lambda uid: {
        "user_id": int(uid), "name": {OPERATOR: 'Айгерим', OTHER: 'Сауле'}[int(uid)], "department_id": TEZ,
        "internal_number": {OPERATOR: '601', OTHER: '602'}[int(uid)],
        "settings": svc.department_settings(TEZ)}
    try:
        yield _Env(db, svc, binotel, tez, chatapp)
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


@pytest.fixture
def settle_now(monkeypatch):
    monkeypatch.setattr(dial_service, 'ANSWER_SETTLE_SEC', 0.0)


def _now():
    return datetime.now(timezone.utc)


# ── подключение ──────────────────────────────────────────────────────────────

def test_enroll_turns_a_dataless_remote_cc_department_into_tez(env):
    svc = env.svc
    assert [d["department_id"] for d in svc.candidate_departments(profile='tez')] == [TEZ]
    assert {d["department_id"] for d in svc.list_departments(profile='remote_cc')} == {TEZ, RCC}
    # Удалённый КЦ свой список кандидатов не меняет: подключённых в нём нет.
    assert svc.candidate_departments() == []

    settings = svc.enroll_department(TEZ, changed_by=1, profile='tez')
    assert (settings["profile"], settings["binotel_company"], settings["auto_dial"], settings["enabled"],
            settings["retry_after_hours"], settings["retry_schedule_min"]) == ('tez', 'tez', True, False, 0,
                                                                              [60, 180, 1440])
    # Стандартные итоги, ни разу не выбранные, заменены набором Тез с датами.
    assert env.q("SELECT name, due_kind, requeue FROM dial_list_outcomes WHERE department_id = %s "
                 "ORDER BY position", (TEZ,)) == [
        ('Согласен выйти на линию', 'online', True), ('Перезвонить', 'callback', True),
        ('Отказ', '', False), ('Неверный номер', '', False), ('Уже работает', '', False)]
    assert {d["department_id"] for d in svc.list_departments(profile='remote_cc')} == {RCC}
    assert [d["department_id"] for d in svc.list_departments(profile='tez')] == [TEZ]
    assert svc.candidate_departments(profile='tez') == []
    # Повторное подключение — без изменений; отдел с базой удалённого КЦ не переводится.
    assert svc.enroll_department(TEZ, changed_by=1, profile='tez')["profile"] == 'tez'
    assert env.refused(svc.enroll_department, RCC, changed_by=1, profile='tez')[1] == 409
    assert svc.department_settings(RCC)["profile"] == 'remote_cc'


def test_profile_switch_from_a_full_settings_form_still_gets_tez_values(env):
    # Форма сайта шлёт настройки целиком — прежняя компания 'remote_cc' смену профиля не переживает.
    current = env.svc.department_settings(TEZ)
    form = {k: current[k] for k in ("enabled", "portion_size", "max_attempts", "retry_after_hours",
                                    "binotel_company", "auto_dial", "retry_schedule_min")}
    settings = env.svc.save_department_settings(TEZ, dict(form, profile='tez'), changed_by=1)
    assert (settings["binotel_company"], settings["auto_dial"], settings["retry_schedule_min"],
            settings["retry_after_hours"]) == ('tez', True, [60, 180, 1440], 0)
    # Явно другое значение в том же запросе — остаётся.
    env.svc.save_department_settings(TEZ, {"profile": "remote_cc"}, changed_by=1)
    settings = env.svc.save_department_settings(TEZ, dict(form, profile='tez', retry_schedule_min=[30]),
                                                changed_by=1)
    assert settings["retry_schedule_min"] == [30]


# ── база и поездки ───────────────────────────────────────────────────────────

def test_base_without_iin_and_trip_check_takes_drivers_already_online_out(env):
    env.enroll()
    rows = [(1, 'Иванов Иван', '87010000001', '77010000001'), (2, 'Петров Пётр', '87010000002', '77010000002'),
            (3, 'Без номера', 'abc', None)]
    counts = env.svc.import_leads(TEZ, 1, 'tez.xlsx', rows, require_iin=False)
    assert (counts["rows_new"], counts["rows_invalid"], counts["rows_bad_iin"]) == (2, 1, 0)
    assert env.q("SELECT iin FROM dial_list_leads WHERE department_id = %s", (TEZ,)) == [('',), ('',)]
    went = _now() - timedelta(hours=2)
    env.tez.answers['77010000001'] = {'month': went, 'prev': None, 'last_before': went - timedelta(days=60)}
    env.tez.answers['77010000002'] = {'month': None, 'prev': None, 'last_before': went - timedelta(days=3)}
    summary = env.svc.trip_checker.run(reason='тест')
    assert (summary["checked"], summary["online"], summary["errors"]) == (2, 1, 0)
    # Месяц запроса — месяц базы, и только номера отдела Тез (чужой удалённого КЦ не спрошен).
    phones, month, _timeout = env.tez.asked[0]
    assert phones == ['77010000001', '77010000002']
    assert month == dial_service.current_period().strftime('%Y-%m')
    online = env.one("SELECT signed_at IS NOT NULL, sign_status, trip_checked_at IS NOT NULL FROM dial_list_leads "
                     "WHERE phone_norm = '77010000001'")
    assert online == (True, 'online', True)
    summary = env.svc.leads_summary(TEZ)
    assert (summary["profile"], summary["total"], summary["signed"], summary["online"], summary["trip_checked"],
            summary["without_iin"]) == ('tez', 2, 1, 1, 2, 2)
    # В пачку вышедший на линию не попадает; у второго — метка «Работал …».
    state = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)
    items = state["portion"]["items"]
    assert [i["full_name"] for i in items] == ['Петров Пётр']
    assert items[0]["driver"]["label"].startswith('Работал ')
    assert (items[0]["due_kind"], items[0]["due_label"]) == ('', '')
    assert '77010000002' not in json.dumps(state, ensure_ascii=False)


# ── автоцикл ─────────────────────────────────────────────────────────────────

def test_auto_loop_calls_in_order_waits_for_retries_and_reports_empty(env, settle_now, monkeypatch):
    env.enroll(portion_size=2)
    leads = env.leads(3)
    res = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)
    first = res["next"]
    assert (first["kind"], first["full_name"], res["reason"]) == ('queue', 'Водитель1 Имя1', '')
    assert [i["full_name"] for i in res["portion"]["items"]] == ['Водитель1 Имя1', 'Водитель2 Имя2']
    assert (res["profile"], res["auto"]["enabled"]) == ('tez', True)
    # Не «Активный» — автоцикл не выдаёт.
    assert env.refused(env.svc.auto_next, OPERATOR, status='outbound')[1:] == (409, 'status')

    attempt = env.svc.start_call(OPERATOR, first["assignment_id"], auto=True, operator_status='active')
    assert env.one("SELECT auto, operator_status FROM dial_list_attempts WHERE id = %s",
                   (attempt["attempt_id"],)) == (True, 'active')
    # Пока плечо идёт — следующего нет.
    assert env.refused(env.svc.auto_next, OPERATOR, status='active')[1] == 409
    state = env.finish(attempt, 'NOANSWER')
    assert state["pending_outcome"] is None
    # Недозвон: повтор — по расписанию отдела (60 минут после первой попытки).
    wait = env.one("SELECT EXTRACT(EPOCH FROM next_call_at - CURRENT_TIMESTAMP) / 60, owner_operator_id "
                   "FROM dial_list_leads WHERE id = %s", (leads['77010000001'],))
    assert 58 <= float(wait[0]) <= 61 and wait[1] is None

    second = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)["next"]
    assert second["full_name"] == 'Водитель2 Имя2'
    # Оператор сбросил до ответа: строка в работе, но снова — не раньше чем через AUTO_RETRY_GAP_SEC.
    call = env.svc.start_call(OPERATOR, second["assignment_id"], auto=True, operator_status='active')
    for event in ('ringing', 'operator_hangup'):
        env.svc.phone_event(OPERATOR, call["attempt_id"], event, phone_version=GUARDED)
    env.svc.phone_event(OPERATOR, call["attempt_id"], 'ended', by_operator=True, leg_sec=5, phone_version=GUARDED)
    env.binotel_says(call, 'CANCEL')
    env.poll_now()
    env.svc.get_state(OPERATOR, phone_version=GUARDED)
    res = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)
    assert (res["next"], res["reason"]) == (None, 'wait')
    assert 100 <= res["retry_after_sec"] <= dial_service.AUTO_RETRY_GAP_SEC + 1
    monkeypatch.setattr(dial_service, 'AUTO_RETRY_GAP_SEC', 0)
    again = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)["next"]
    assert again["assignment_id"] == second["assignment_id"]
    call = env.svc.start_call(OPERATOR, again["assignment_id"], auto=True, operator_status='active')
    env.finish(call, 'BUSY')

    # Выдача обработана — новая пачка сама: третий водитель.
    third = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)["next"]
    assert third["full_name"] == 'Водитель3 Имя3'
    call = env.svc.start_call(OPERATOR, third["assignment_id"], auto=True, operator_status='active')
    state = env.finish(call, 'ANSWER', 45)
    assert state["pending_outcome"]["attempt_id"] == call["attempt_id"]
    # Без итога дальше нельзя.
    assert env.refused(env.svc.auto_next, OPERATOR, status='active')[1] == 409
    env.svc.set_attempt_outcome(OPERATOR, call["attempt_id"], env.outcome('Отказ'), '')
    # Остальные ждут повтора по расписанию — пул пуст.
    res = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)
    assert (res["next"], res["reason"], res["retry_after_sec"]) == (None, 'empty', dial_service.AUTO_EMPTY_RETRY_SEC)


def test_call_is_refused_outside_active_or_outbound_and_for_a_driver_already_online(env):
    env.enroll()
    env.leads(2)
    items = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)["portion"]["items"]
    first, second = items[0]["assignment_id"], items[1]["assignment_id"]
    message, status, kind = env.refused(env.svc.start_call, OPERATOR, first, operator_status='break')
    assert (status, kind) == (409, 'status')
    # Водитель вышел на линию после выдачи: проверка перед звонком (данные старше 15 минут) снимает
    # строку, звонка нет.
    env.q("UPDATE dial_list_leads SET trip_checked_at = CURRENT_TIMESTAMP - INTERVAL '1 hour'")
    env.tez.answers['77010000001'] = {'month': _now() - timedelta(minutes=30), 'prev': None, 'last_before': None}
    assert env.refused(env.svc.start_call, OPERATOR, first, auto=True, operator_status='active') == (
        dial_service.GOAL_ROW_CLOSED_TEZ, 409, 'goal')
    assert env.one("SELECT state FROM dial_list_assignments WHERE id = %s", (first,)) == ('done',)
    assert env.binotel.originated == []
    # Сам, в «Исходе» — звонит; попытка помечена ручной.
    attempt = env.svc.start_call(OPERATOR, second, operator_status='outbound')
    assert env.one("SELECT auto, operator_status FROM dial_list_attempts WHERE id = %s",
                   (attempt["attempt_id"],)) == (False, 'outbound')
    assert env.binotel.originated == [('601', '77010000002')]


# ── итоги с датой и офферы ───────────────────────────────────────────────────

def test_callback_waits_for_its_operator_then_goes_to_the_pool(env, settle_now):
    env.enroll()
    leads = env.leads(1)
    lead = leads['77010000001']
    offers = env.svc.save_offers(TEZ, [{"title": "Бонус 5000 за 10 поездок"}, {"title": "Комиссия 0% неделю"}])
    bonus = offers["items"][0]["id"]
    nxt = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)["next"]
    attempt = env.svc.start_call(OPERATOR, nxt["assignment_id"], auto=True, operator_status='active')
    env.finish(attempt, 'ANSWER', 60)
    callback = env.outcome('Перезвонить')
    # Новый телефон шлёт due_at всегда; пустой срок — 400 словами итога.
    assert env.refused(env.svc.set_attempt_outcome, OPERATOR, attempt["attempt_id"], callback,
                       due_at=None)[:2] == ('Укажите дату и время перезвона', 400)
    past = (_now() - timedelta(hours=1)).isoformat()
    assert env.refused(env.svc.set_attempt_outcome, OPERATOR, attempt["attempt_id"], callback,
                       due_at=past)[1] == 400
    assert env.refused(env.svc.set_attempt_outcome, OPERATOR, attempt["attempt_id"], callback,
                       due_at=(_now() + timedelta(hours=2)).isoformat(), offer_ids=[str(uuid.uuid4())])[1] == 400
    due = (_now() + timedelta(hours=2)).replace(microsecond=0)
    result = env.svc.set_attempt_outcome(OPERATOR, attempt["attempt_id"], callback, 'просил вечером',
                                         due_at=due.isoformat().replace('+00:00', 'Z'), offer_ids=[bonus])
    assert (result["outcome"]["due_kind"], result["offer_ids"]) == ('callback', [bonus])
    row = env.one("SELECT owner_operator_id, due_kind, owner_call_at, next_call_at, status FROM dial_list_leads "
                  "WHERE id = %s", (lead,))
    assert (row[0], row[1], row[2], row[4]) == (OPERATOR, 'callback', due, 'in_progress')
    assert row[3] == due + timedelta(minutes=120)
    assert env.q("SELECT offer_id::text FROM dial_list_attempt_offers WHERE attempt_id = %s",
                 (attempt["attempt_id"],)) == [(bonus,)]
    # До срока — ни своему автоциклу, ни чужому пулу.
    assert env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)["reason"] == 'empty'
    assert env.svc.auto_next(OTHER, status='active', phone_version=GUARDED)["reason"] == 'empty'
    # Срок наступил — автоцикл берёт его первым, строкой в своей выдаче; чужому он не виден. Время
    # «прошло» и для звонка: сразу после разговора цикл ждёт паузу повтора (AUTO_RETRY_GAP_SEC).
    env.q("UPDATE dial_list_leads SET owner_call_at = CURRENT_TIMESTAMP - INTERVAL '1 minute' WHERE id = %s",
          (lead,))
    assert env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)["reason"] == 'wait'
    env.q("UPDATE dial_list_attempts SET requested_at = requested_at - INTERVAL '3 hours'")
    assert env.svc.auto_next(OTHER, status='active', phone_version=GUARDED)["reason"] == 'empty'
    res = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)
    assert (res["next"]["kind"], res["next"]["lead_id"]) == ('callback', lead)
    item, = [i for i in res["portion"]["items"] if i["assignment_id"] == res["next"]["assignment_id"]]
    assert item["due_kind"] == 'callback' and item["due_label"].startswith('Перезвон ')
    # Согласие выйти на линию: дожим — через followup_after_min после обещанного выхода.
    attempt = env.svc.start_call(OPERATOR, res["next"]["assignment_id"], auto=True, operator_status='active')
    env.finish(attempt, 'ANSWER', 30)
    promised = (_now() + timedelta(hours=1)).replace(microsecond=0)
    env.svc.set_attempt_outcome(OPERATOR, attempt["attempt_id"], env.outcome('Согласен выйти на линию'), '',
                                due_at=promised.isoformat())
    row = env.one("SELECT due_kind, owner_call_at, promised_online_at, next_call_at FROM dial_list_leads "
                  "WHERE id = %s", (lead,))
    assert row == ('online', promised + timedelta(minutes=60), promised, promised + timedelta(minutes=180))
    # Вышел на линию — снят со всех списков, успешка — тому, кто дожал (разговор ≥ порога). Прогон
    # переспрашивает не чаще RECHECK_MINUTES: перед звонками водителя только что проверяли.
    env.tez.answers['77010000001'] = {'month': _now(), 'prev': None, 'last_before': None}
    assert env.svc.trip_checker.run(reason='тест')["checked"] == 0
    env.q("UPDATE dial_list_leads SET trip_checked_at = CURRENT_TIMESTAMP - INTERVAL '2 hours'")
    assert env.svc.trip_checker.run(reason='тест')["online"] == 1
    assert env.one("SELECT sign_status, success_operator_id FROM dial_list_leads WHERE id = %s",
                   (lead,)) == ('online', OPERATOR)
    analytics = env.svc.analytics(TEZ)
    assert analytics["profile"] == 'tez'
    assert {k: analytics["totals"][k] for k in ("leads", "called", "talked", "agreements", "callbacks",
                                                "went_online", "successes", "auto_attempts")} == {
        "leads": 1, "called": 1, "talked": 1, "agreements": 1, "callbacks": 1, "went_online": 1, "successes": 1,
        "auto_attempts": 2}
    assert [(o["title"], o["offered"]) for o in analytics["offers"]] == [
        ('Бонус 5000 за 10 поездок', 1), ('Комиссия 0% неделю', 0)]
    journal = env.svc.leads_journal(TEZ)
    assert [(i["stage"], i["stage_label"]) for i in journal["items"]] == [('signed', 'Вышел на линию')]
    card = env.svc.lead_card(lead)
    assert [a["due_at"] is not None for a in card["attempts"]] == [True, True]
    assert sorted(len(a["offers"]) for a in card["attempts"]) == [0, 1]


def test_wrapup_expired_is_recorded_and_counted(env, settle_now):
    env.enroll()
    env.leads(1)
    nxt = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)["next"]
    attempt = env.svc.start_call(OPERATOR, nxt["assignment_id"], auto=True, operator_status='active')
    env.finish(attempt, 'ANSWER', 25)
    assert env.svc.phone_event(OPERATOR, attempt["attempt_id"], 'wrapup_expired', phone_version=GUARDED) == {
        "ok": True}
    assert env.one("SELECT wrapup_expired_at IS NOT NULL FROM dial_list_attempts WHERE id = %s",
                   (attempt["attempt_id"],)) == (True,)
    # Отметка не снимает долг по итогу.
    assert env.svc.get_state(OPERATOR, phone_version=GUARDED)["pending_outcome"]["attempt_id"] == \
        attempt["attempt_id"]
    # Включённый лично, но ещё не звонивший — тоже в сводке, нулями.
    env.svc.save_user_setting(OTHER, True, changed_by=1)
    rows = env.svc.overview([TEZ], datetime.now(dial_service.PERIOD_TZ).date(), tez=True)
    assert [(r["operator_id"], r["attempts"], r["auto_attempts"], r["wrapup_expired"], r["live_status"])
            for r in rows] == [(OPERATOR, 1, 1, 1, 'off'), (OTHER, 0, 0, 0, 'off')]
    # Удалённому КЦ сводка — прежними ключами.
    plain = env.svc.overview([TEZ], datetime.now(dial_service.PERIOD_TZ).date())
    assert 'wrapup_expired' not in plain[0] and 'live_status' not in plain[0]


def test_tez_still_counts_any_talk_as_a_dial(env, settle_now):
    """Порог 10 с у дозвона (владелец, 29.10.2026: «с засчитанного кол-во дозвонов убрать звонки меньше
    10 секунд») — только у удалённого КЦ: у автообзвона Тез «Дозвонились» — любой разговор, как было."""
    env.enroll()
    env.leads(1)
    nxt = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)["next"]
    attempt = env.svc.start_call(OPERATOR, nxt["assignment_id"], auto=True, operator_status='active')
    env.finish(attempt, 'ANSWER', 5)
    rows = env.svc.overview([TEZ], datetime.now(dial_service.PERIOD_TZ).date(), tez=True)
    assert [(r["attempts"], r["answered"], r["talk_sec"]) for r in rows if r["operator_id"] == OPERATOR] == [(1, 1, 5)]
    progress = env.svc.operator_progress(OPERATOR)
    assert (progress["month"]["answered"], progress["today_stats"]["answered"]) == (1, 1)


# ── WhatsApp ─────────────────────────────────────────────────────────────────

def test_whatsapp_template_goes_to_own_driver_once_a_day(env):
    env.enroll()
    leads = env.leads(1)
    lead = leads['77010000001']
    settings = env.svc.whatsapp_settings(TEZ)
    assert [l["id"] for l in settings["licenses"]] == [70651]
    saved = env.svc.save_whatsapp(TEZ, {"license_id": 70651, "allowed": [
        {"id": "tpl-bonus", "title": "Бонус", "text": "Здравствуйте, {{1}}! Бонус за выход",
         "params": ["{first_name}"]}]}, changed_by=1)
    assert [t["id"] for t in saved["templates"]] == ['tpl-bonus']
    state = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)
    assert state["whatsapp"]["enabled"] is True
    assert [t["id"] for t in state["whatsapp"]["templates"]] == ['tpl-bonus']
    # Чужой водитель — нельзя.
    assert env.refused(env.svc.send_whatsapp, OTHER, lead, 'tpl-bonus')[1] == 403
    assert env.refused(env.svc.send_whatsapp, OPERATOR, lead, 'нет-такого')[1] == 400
    sent = env.svc.send_whatsapp(OPERATOR, lead, 'tpl-bonus')
    assert (sent["sent"], sent["template_title"]) == (True, 'Бонус')
    assert env.chatapp.sent == [(70651, '77010000001', 'tpl-bonus', ['Имя1'])]
    assert env.one("SELECT status, provider_message_id, operator_id FROM dial_list_wa_messages") == (
        'sent', 'wa-1', OPERATOR)
    # Второй раз за сутки — нет, и ChatApp не спрошен.
    assert env.refused(env.svc.send_whatsapp, OPERATOR, lead, 'tpl-bonus')[1] == 409
    assert len(env.chatapp.sent) == 1
    assert env.svc.analytics(TEZ)["whatsapp"] == {"sent": 1, "failed": 0}


def test_requeue_takes_off_the_own_deadline(env, settle_now):
    env.enroll()
    lead = env.leads(1)['77010000001']
    nxt = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)["next"]
    attempt = env.svc.start_call(OPERATOR, nxt["assignment_id"], auto=True, operator_status='active')
    env.finish(attempt, 'ANSWER', 30)
    env.svc.set_attempt_outcome(OPERATOR, attempt["attempt_id"], env.outcome('Перезвонить'), '',
                                due_at=(_now() + timedelta(hours=3)).isoformat())
    assert env.svc.leads_journal(TEZ)["items"][0]["stage"] == 'callback'
    card = env.svc.requeue_lead(lead, 1, note='сам позвоню')
    assert card["stage"] in ('queue', 'issued')
    assert env.one("SELECT owner_operator_id, owner_call_at, due_kind, next_call_at FROM dial_list_leads "
                   "WHERE id = %s", (lead,)) == (None, None, '', None)
    # Вышедшего на линию вернуть нельзя — и слова у Тез свои.
    env.q("UPDATE dial_list_leads SET signed_at = CURRENT_TIMESTAMP, sign_status = 'online' WHERE id = %s", (lead,))
    assert env.refused(env.svc.requeue_lead, lead, 1)[:2] == ('Водитель уже вышел на линию — звонить ему незачем', 409)


def test_phone_without_dates_saves_a_dated_outcome_without_a_date(env, settle_now):
    # Телефон до 3.22.37 ключа due_at не знает: итог «Перезвонить» принимается без срока — водитель
    # вернётся по расписанию повторов (первый интервал — 60 мин), оператор не застревает с долгом.
    env.enroll()
    lead = env.leads(1)['77010000001']
    items = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)["portion"]["items"]
    attempt = env.svc.start_call(OPERATOR, items[0]["assignment_id"])
    env.finish(attempt, 'ANSWER', 40)
    result = env.svc.set_attempt_outcome(OPERATOR, attempt["attempt_id"], env.outcome('Перезвонить'), '')
    assert (result["outcome"]["due_kind"], result["due_at"]) == ('', None)
    row = env.one("SELECT owner_operator_id, due_kind, EXTRACT(EPOCH FROM next_call_at - CURRENT_TIMESTAMP) / 60, "
                  "status FROM dial_list_leads WHERE id = %s", (lead,))
    assert (row[0], row[1], row[3]) == (None, '', 'in_progress')
    assert 58 <= float(row[2]) <= 61
    assert env.svc.get_state(OPERATOR, phone_version=GUARDED)["pending_outcome"] is None


def test_card_attempt_says_what_the_date_means(env, settle_now):
    env.enroll()
    lead = env.leads(1)['77010000001']
    nxt = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)["next"]
    attempt = env.svc.start_call(OPERATOR, nxt["assignment_id"], auto=True, operator_status='active')
    env.finish(attempt, 'ANSWER', 30)
    env.svc.set_attempt_outcome(OPERATOR, attempt["attempt_id"], env.outcome('Согласен выйти на линию'), '',
                                due_at=(_now() + timedelta(hours=5)).isoformat())
    card = env.svc.lead_card(lead)
    assert [(a["due_kind"], a["auto"], a["operator_status"]) for a in card["attempts"]] == [('online', True, 'active')]


def test_own_callback_is_not_redialled_in_a_loop_after_a_cancel(env, settle_now, monkeypatch):
    # Ревью 15.10.2026: свой срок наступил, оператор сбросил звонок до ответа — срок остаётся, и без
    # общей паузы повтора автоцикл звонил бы этому водителю снова и снова, не давая звонить другим.
    env.enroll(portion_size=1)
    leads = env.leads(2)
    first, second = leads['77010000001'], leads['77010000002']
    nxt = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)["next"]
    assert nxt["lead_id"] == first
    attempt = env.svc.start_call(OPERATOR, nxt["assignment_id"], auto=True, operator_status='active')
    env.finish(attempt, 'ANSWER', 30)
    env.svc.set_attempt_outcome(OPERATOR, attempt["attempt_id"], env.outcome('Перезвонить'), '',
                                due_at=(_now() + timedelta(minutes=1)).isoformat())
    env.q("UPDATE dial_list_leads SET owner_call_at = CURRENT_TIMESTAMP - INTERVAL '1 minute' WHERE id = %s",
          (first,))
    env.q("UPDATE dial_list_attempts SET requested_at = requested_at - INTERVAL '10 minutes'")
    res = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)
    assert (res["next"]["lead_id"], res["next"]["kind"]) == (first, 'callback')
    call = env.svc.start_call(OPERATOR, res["next"]["assignment_id"], auto=True, operator_status='active')
    for event in ('ringing', 'operator_hangup'):
        env.svc.phone_event(OPERATOR, call["attempt_id"], event, phone_version=GUARDED)
    env.svc.phone_event(OPERATOR, call["attempt_id"], 'ended', by_operator=True, leg_sec=4, phone_version=GUARDED)
    # Исход ещё едет от АТС — этому водителю нельзя; следующий — другой водитель (новая пачка).
    res = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)
    assert res["next"]["lead_id"] == second
    env.binotel_says(call, 'CANCEL')
    env.poll_now()
    env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert env.one("SELECT owner_operator_id, due_kind FROM dial_list_leads WHERE id = %s", (first,)) == (
        OPERATOR, 'callback')
    other = env.svc.start_call(OPERATOR, res["next"]["assignment_id"], auto=True, operator_status='active')
    env.finish(other, 'NOANSWER')
    # Отмена была только что — свой срок ждёт паузу повтора, цикл не звонит ему сразу.
    res = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)
    assert res["next"] is None and res["reason"] == 'wait'
    assert 100 <= res["retry_after_sec"] <= dial_service.AUTO_RETRY_GAP_SEC + 1
    monkeypatch.setattr(dial_service, 'AUTO_RETRY_GAP_SEC', 0)
    res = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)
    assert (res["next"]["lead_id"], res["next"]["kind"]) == (first, 'callback')


def test_refusal_after_an_unanswered_final_keeps_the_retry_pause(env, settle_now):
    # Ревью 15.10.2026: телефон услышал разговор, Binotel — недозвон; финал поставил паузу повтора,
    # итог «Отказ» её не снимает (у Тез retry_after_hours = 0 — водитель сразу вернулся бы в пул).
    env.enroll()
    lead = env.leads(1)['77010000001']
    nxt = env.svc.auto_next(OPERATOR, status='active', phone_version=GUARDED)["next"]
    attempt = env.svc.start_call(OPERATOR, nxt["assignment_id"], auto=True, operator_status='active')
    env.finish(attempt, 'NOANSWER', talk=True)
    env.svc.set_attempt_outcome(OPERATOR, attempt["attempt_id"], env.outcome('Отказ'), '')
    wait = env.one("SELECT EXTRACT(EPOCH FROM next_call_at - CURRENT_TIMESTAMP) / 60 FROM dial_list_leads "
                   "WHERE id = %s", (lead,))
    assert 58 <= float(wait[0]) <= 61
    assert env.svc.auto_next(OTHER, status='active', phone_version=GUARDED)["reason"] == 'empty'


def test_webhook_of_a_non_dial_call_keeps_no_number(env):
    # У компании Тез вебхук «API Call Completed» приходит по всем звонкам линии: чужой звонок
    # обзвон не трогает, а в журнал вебхука его номер не попадает.
    from werkzeug.datastructures import ImmutableMultiDict
    form = ImmutableMultiDict({
        'requestType': 'apiCallCompleted', 'callDetails[generalCallID]': '999000111',
        'callDetails[externalNumber]': '77071234567', 'callDetails[disposition]': 'ANSWER',
        'callDetails[billsec]': '120', 'callDetails[waitsec]': '4'})
    res = env.svc.handle_webhook(form, '194.88.218.116')
    assert res == {"matched": False, "general_call_id": '999000111', "disposition": 'ANSWER'}
    logged = env.one("SELECT matched, payload FROM dial_list_webhook_log WHERE general_call_id = '999000111'")
    assert logged[0] is False
    assert logged[1] == {"_not_dial": True, "generalCallID": '999000111', "disposition": 'ANSWER'}
    assert '77071234567' not in json.dumps(logged[1])
