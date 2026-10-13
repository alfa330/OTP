# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: DIAL_LIST_TEST_PORT — порт локального Postgres.

Обзвон из телефона, 13.10.2026 — разбор прода 12.10.2026 (телефоны 3.22.33):
  * один опрос call-details на компанию и общий кэш — финал попытки закрывается, кто бы ни
    спросил Binotel; ответ водителя, пока плечо идёт, — разговор, а не финал; закрывают его
    данные после конца плеча, поздний финал длительность только увеличивает;
  * кончившееся плечо (ended) не держит ни «Позвонить», ни «Ещё»; итог по позднему ответу
    требует следующий звонок;
  * вебхук «API Call Completed» в настоящей форме Binotel;
  * подпись снимает открытую строку водителя у любого оператора и никогда — посреди звонка
    (замок строки до проверки попыток), поздняя отмена не открывает водителя, обработанного раньше;
  * починка: водители, чьи разговоры оборвал автосброс, — обратно к тому же оператору.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным именем,
в конце она удаляется. Binotel и Sapar подменены. Запуск на локальном кластере:

    DIAL_LIST_TEST_PORT=55441 python -m pytest tests/test_dial_list_dispatch_postgres.py

Люди и номера выдуманы.
"""
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

from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

PORT = os.environ.get('DIAL_LIST_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DIAL_LIST_TEST_PORT)')

RCC, OLD_DEP = 1954, 1955
OPERATOR, OTHER, GONE = 520, 521, 522
GUARDED = '3.22.34'

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE test_phone_numbers (phone_key varchar(10) UNIQUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text);
    CREATE TABLE sip_department_config (department_id integer PRIMARY KEY, provider text,
                                        sip_server text, ring_timeout_rings smallint);
    INSERT INTO departments (id, name, code) VALUES (1954, 'Удаленный КЦ', 'remote_cc'),
                                                    (1955, 'Прежний отдел', 'old_cc');
    INSERT INTO sip_department_config VALUES (1954, 'binotel', 'sip53.binotel.com', NULL),
                                             (1955, 'binotel', 'sip53.binotel.com', NULL);
    INSERT INTO users (id, name, login, role, status, department_id, sip_number) VALUES
        (520, 'Своев Сава', 'sava', 'operator', 'working', 1954, '902'),
        (521, 'Новичков Назар', 'nazar', 'operator', 'working', 1954, '903'),
        (522, 'Ушедший Ульян', 'ulyan', 'operator', 'fired', 1954, '904');
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
    """Binotel без сети: click-to-call отдаёт generalCallID, call-details — то, что лежит в
    live (нет записи — звонка Binotel не видит); asked — с чем спрашивали call-details."""

    def __init__(self):
        self.live, self.originated, self.asked, self.timeouts = {}, [], [], []

    def originate_internal_to_external(self, internal_number, external_number, **extra):
        self.originated.append((internal_number, external_number))
        return 'gc-%d-%s' % (len(self.originated), uuid.uuid4().hex[:6])

    def call_details(self, general_call_ids, max_wait=None, timeout=None):
        ids = general_call_ids if isinstance(general_call_ids, (list, tuple)) else [general_call_ids]
        self.asked.append(list(ids))
        self.timeouts.append(timeout)
        return {str(g): dict(self.live[str(g)], general_call_id=str(g)) for g in ids if str(g) in self.live}


class _NoSapar:
    def configured(self):
        return False


class _Env:
    def __init__(self, db, svc, binotel):
        self.db, self.svc, self.binotel = db, svc, binotel

    def q(self, sql, params=None):
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def one(self, sql, params=None):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def outcome(self, name):
        return str(self.one("SELECT id FROM dial_list_outcomes WHERE name = %s", (name,))[0])

    def portion(self, operator=OPERATOR, closed=False, department=RCC):
        with self.db._get_cursor() as cur:
            cur.execute("INSERT INTO dial_list_portions (operator_id, department_id, size, closed_at) "
                        "VALUES (%s, %s, 1, %s) RETURNING id",
                        (operator, department, datetime.now(timezone.utc) if closed else None))
            return str(cur.fetchone()[0])

    def lead(self, name, phone, operator=OPERATOR, portion=None, department=RCC, iin=''):
        with self.db._get_cursor() as cur:
            cur.execute("INSERT INTO dial_list_leads (department_id, phone_norm, full_name, iin) "
                        "VALUES (%s, %s, %s, %s) RETURNING id", (department, phone, name, iin))
            lead_id = str(cur.fetchone()[0])
        if portion is None:
            portion = self.portion(operator, department=department)
        with self.db._get_cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM dial_list_assignments WHERE portion_id = %s",
                        (portion,))
            position = cur.fetchone()[0]
            cur.execute("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position) "
                        "VALUES (%s, %s, %s, %s) RETURNING id", (portion, lead_id, operator, position))
            return lead_id, str(cur.fetchone()[0])

    def call(self, assignment_id, operator=OPERATOR):
        attempt = self.svc.start_call(operator, assignment_id)
        self.svc.phone_event(operator, attempt["attempt_id"], 'ringing', phone_version=GUARDED)
        self.svc.phone_event(operator, attempt["attempt_id"], 'answered', phone_version=GUARDED)
        return attempt

    def ended(self, attempt, operator=OPERATOR, **keys):
        return self.svc.phone_event(operator, attempt["attempt_id"], 'ended', phone_version=GUARDED, **keys)

    def binotel_says(self, attempt, disposition, billsec=0, waitsec=7):
        self.binotel.live[attempt["general_call_id"]] = {"disposition": disposition, "billsec": billsec,
                                                         "waitsec": waitsec}

    def poll_now(self):
        """Следующий запрос к сервису увидит Binotel заново: в тесте секунды между шагами не
        проходят, а кэш опроса держит данные CALLS_REFRESH_SEC."""
        for calls in self.svc._calls.values():
            with calls._lock:
                calls._entries.clear()
                calls._last_start = None
                calls._not_before = 0.0

    def attempt_row(self, attempt):
        return self.one("SELECT state, disposition, billsec, waitsec, final_source, cancelled "
                        "FROM dial_list_attempts WHERE id = %s", (attempt["attempt_id"],))

    def assignment(self, assignment_id):
        return self.one("SELECT state, result, attempts, portion_id FROM dial_list_assignments WHERE id = %s",
                        (assignment_id,))

    def lead_row(self, lead_id):
        return self.one("SELECT status, attempts_total, answered_at IS NOT NULL FROM dial_list_leads WHERE id = %s",
                        (lead_id,))

    def pending(self, operator=OPERATOR):
        with self.db._get_cursor() as cur:
            debt = self.svc._pending_outcome(cur, operator)
        return debt and debt["attempt_id"]

    def refused(self, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except dial_service.DialListError as exc:
            return str(exc), exc.status
        return None


def _sync(fn):
    fn()


@pytest.fixture
def env():
    schema = 't_dial_dispatch_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled) VALUES (1954, TRUE), "
                    "(1955, TRUE)")
        cur.execute("INSERT INTO dial_list_outcomes (department_id, name, position, requeue) VALUES "
                    "(1954, 'Отказ', 1, FALSE), (1954, 'Перезвонить позже', 2, TRUE), (1955, 'Отказ', 1, FALSE)")
    binotel = _Binotel()
    svc = dial_service.DialListService(db, client_factory=lambda dep: binotel, sign_checker=_NoSapar(),
                                       calls_submit=_sync)
    svc.operator_context = lambda uid: {
        "user_id": int(uid), "name": "Оператор", "department_id": RCC,
        "internal_number": {OPERATOR: '902', OTHER: '903', GONE: '904'}[int(uid)],
        "settings": svc.department_settings(RCC)}
    try:
        yield _Env(db, svc, binotel)
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


@pytest.fixture
def settle_now(monkeypatch):
    """Ответ водителя закрывается данными «после конца плеча» — в тесте без секунды ожидания."""
    monkeypatch.setattr(dial_service, 'ANSWER_SETTLE_SEC', 0.0)


# ── кончившееся плечо не держит звонки ───────────────────────────────────────

def test_ended_attempt_holds_neither_call_nor_next_and_late_answer_asks_outcome(env, settle_now):
    portion = env.portion()
    lead_a, asg_a = env.lead('Иванов Иван', '77010000001', portion=portion)
    lead_b, asg_b = env.lead('Петров Пётр', '77010000002', portion=portion)
    first = env.call(asg_a)
    # Телефон положил трубку, разговора не услышал; исхода от Binotel ещё нет.
    res = env.ended(first, by_operator=False, leg_sec=12, talk_detected=False)
    assert (res["state"], res["final"], res["outcome_required"]) == ('ended', False, None)
    # Звонок не держит: в состоянии нет активной попытки, «Позвонить» следующему проходит. Тому же
    # водителю — только после исхода.
    state = env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert state["active_attempt"] is None
    assert env.refused(env.svc.start_call, OPERATOR, asg_a) == (
        "Исход прошлого звонка этому водителю ещё не пришёл от АТС — подождите несколько секунд", 409)
    second = env.svc.start_call(OPERATOR, asg_b)
    assert second["lead_id"] == lead_b
    # Ответ водителя пришёл поздно (ANSWER 25 с): первая попытка закрывается в фоне (опрос по
    # чужому поводу — карточка второго звонка), итог по ней требует следующий звонок.
    env.binotel_says(first, 'ANSWER', 25)
    env.binotel_says(second, 'CALLING')
    env.poll_now()
    card = env.svc.attempt_live(OPERATOR, second["attempt_id"])
    assert (card["talking"], card["final"]) == (False, False)
    assert env.attempt_row(first)[:3] == ('finished', 'ANSWER', 25)
    assert env.assignment(asg_a)[:2] == ('done', 'answered')
    assert env.pending() == first["attempt_id"]
    # Второй звонок кончился «не ответил» — следующий «Позвонить» требует итог первого.
    env.ended(second, by_operator=False, leg_sec=20, talk_detected=False)
    env.binotel_says(second, 'NOANSWER')
    env.poll_now()
    env.svc.get_state(OPERATOR, phone_version=GUARDED)
    lead_c, asg_c = env.lead('Сидоров Сидор', '77010000003', portion=portion)
    assert env.refused(env.svc.start_call, OPERATOR, asg_c) == ("Сначала укажите итог звонка: Иванов Иван", 409)


def test_next_portion_carries_rows_still_waiting_for_their_final(env):
    portion = env.portion()
    lead_a, asg_a = env.lead('Иванов Иван', '77010000001', portion=portion)
    with env.db._get_cursor() as cur:   # в пуле — ещё один водитель
        cur.execute("INSERT INTO dial_list_leads (department_id, phone_norm, full_name) VALUES (%s, %s, %s) "
                    "RETURNING id", (RCC, '77010000009', 'Новый Ной'))
        new_lead = str(cur.fetchone()[0])
    attempt = env.call(asg_a)
    env.ended(attempt, by_operator=True, leg_sec=9, talk_detected=False)
    state = env.svc.get_state(OPERATOR, phone_version=GUARDED)
    # Строка ждёт исхода от АТС — «осталось» её не считает, «Ещё» доступно.
    assert (state["portion"]["pending"], state["can_request_next"]) == (0, True)
    state = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)
    names = [i["full_name"] for i in state["portion"]["items"]]
    assert names == ['Новый Ной', 'Иванов Иван']                       # переехала в конец новой выдачи
    assert env.one("SELECT closed_at IS NOT NULL FROM dial_list_portions WHERE id = %s", (portion,))[0] is True
    assert str(env.assignment(asg_a)[3]) == state["portion"]["id"]
    # Binotel: водитель не ответил, а трубку положил оператор — отмена, строка снова в работе и
    # видна телефону (в новой выдаче, а не в закрытой).
    env.binotel_says(attempt, 'CANCEL')
    env.poll_now()
    state = env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert env.attempt_row(attempt)[5] is True
    row, = [i for i in state["portion"]["items"] if i["lead_id"] == lead_a]
    assert (row["state"], row["last_attempt"]["cancelled"]) == ('issued', True)
    assert state["portion"]["pending"] == 2
    assert new_lead in [i["lead_id"] for i in state["portion"]["items"]]


# ── ответ водителя: разговор, пока плечо идёт; длительность только растёт ─────

def test_answer_mid_call_is_a_talk_and_the_final_billsec_only_grows(env, settle_now):
    lead, asg = env.lead('Иванов Иван', '77010000001')
    attempt = env.call(asg)
    env.binotel_says(attempt, 'ANSWER', 5)
    card = env.svc.attempt_live(OPERATOR, attempt["attempt_id"])
    assert (card["talking"], card["final"], card["state"]) == (True, False, 'leg_answered')
    assert env.attempt_row(attempt)[0] == 'leg_answered'                # не закрыта посреди разговора
    # Разговор кончился, телефон его слышал: итог нужен сразу. ANSWER в кэше — ещё неполный (5 с).
    res = env.ended(attempt, by_operator=True, leg_sec=60, talk_detected=True)
    assert (res["final"], res["outcome_required"]) == (False, True)
    assert env.attempt_row(attempt)[0] == 'ended'
    # Данные после конца плеча — полная длительность.
    env.binotel_says(attempt, 'ANSWER', 42, 9)
    env.poll_now()
    env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert env.attempt_row(attempt)[:5] == ('finished', 'ANSWER', 42, 9, 'poll')
    # Вебхук с меньшей длительностью ничего не уменьшает; с большей — увеличивает.
    final = lambda billsec, waitsec=9: env.svc.handle_webhook(
        {"callDetails[generalCallID]": attempt["general_call_id"], "callDetails[disposition]": "ANSWER",
         "callDetails[billsec]": str(billsec), "callDetails[waitsec]": str(waitsec)}, '194.88.218.116')
    final(40, 8)
    assert env.attempt_row(attempt)[:5] == ('finished', 'ANSWER', 42, 9, 'poll')
    final(50, 11)
    assert env.attempt_row(attempt)[:5] == ('finished', 'ANSWER', 50, 11, 'webhook')
    assert env.assignment(asg)[:3] == ('done', 'answered', 1)
    assert env.lead_row(lead) == ('done', 1, True)


def test_late_longer_answer_turns_a_silent_zero_into_a_talk(env, settle_now):
    # Телефон разговора не услышал, а Binotel сначала отдал ANSWER 0 с — недозвон. Вебхук
    # приносит 6 с: это разговор — строке и лиду дозвон, итог требует следующий звонок.
    lead, asg = env.lead('Иванов Иван', '77010000001')
    attempt = env.call(asg)
    env.ended(attempt, by_operator=False, leg_sec=10, talk_detected=False)
    env.binotel_says(attempt, 'ANSWER', 0)
    env.poll_now()
    env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert env.assignment(asg)[:2] == ('done', 'no_answer')
    assert env.lead_row(lead) == ('in_progress', 1, False)
    assert env.pending() is None
    env.svc.handle_webhook({"callDetails": {"generalCallID": attempt["general_call_id"], "disposition": "ANSWER",
                                            "billsec": 6, "waitsec": 4}}, '194.88.218.116')
    assert env.attempt_row(attempt)[:3] == ('finished', 'ANSWER', 6)
    assert env.assignment(asg)[:3] == ('done', 'answered', 1)
    assert env.lead_row(lead) == ('done', 1, True)
    assert env.pending() == attempt["attempt_id"]


# ── один опрос на всех ───────────────────────────────────────────────────────

def test_one_poll_closes_the_finals_of_everybody(env, settle_now):
    lead_a, asg_a = env.lead('Иванов Иван', '77010000001', operator=OPERATOR)
    lead_b, asg_b = env.lead('Петров Пётр', '77010000002', operator=OTHER)
    mine = env.call(asg_a, operator=OPERATOR)
    theirs = env.call(asg_b, operator=OTHER)
    env.ended(theirs, operator=OTHER, by_operator=False, leg_sec=15, talk_detected=False)
    env.binotel_says(theirs, 'NOANSWER')
    env.binotel_says(mine, 'CALLING')
    env.poll_now()
    asked_before = len(env.binotel.asked)
    env.svc.attempt_live(OPERATOR, mine["attempt_id"])
    # Один запрос — сразу на оба живых звонка компании; чужая попытка закрыта тем же опросом.
    assert len(env.binotel.asked) == asked_before + 1
    assert sorted(env.binotel.asked[-1]) == sorted([mine["general_call_id"], theirs["general_call_id"]])
    assert env.attempt_row(theirs)[:2] == ('finished', 'NOANSWER')
    assert env.assignment(asg_b)[:2] == ('done', 'no_answer')
    # В те же три секунды ещё десяток карточек — без запросов к Binotel.
    for _ in range(10):
        env.svc.attempt_live(OPERATOR, mine["attempt_id"])
    assert len(env.binotel.asked) == asked_before + 1


def test_webhook_in_the_real_binotel_form(env):
    from werkzeug.datastructures import ImmutableMultiDict
    lead, asg = env.lead('Иванов Иван', '77010000001')
    attempt = env.call(asg)
    env.ended(attempt, by_operator=False, leg_sec=33)
    form = ImmutableMultiDict({
        'requestType': 'apiCallCompleted', 'callDetails[generalCallID]': attempt["general_call_id"],
        'callDetails[externalNumber]': '77010000001', 'callDetails[disposition]': 'ANSWER',
        'callDetails[billsec]': '28', 'callDetails[waitsec]': '6', 'callDetails[historyData][0][disposition]': 'BUSY'})
    res = env.svc.handle_webhook(form, '194.88.218.116')
    assert res == {"matched": True, "general_call_id": attempt["general_call_id"], "disposition": 'ANSWER'}
    assert env.attempt_row(attempt)[:5] == ('finished', 'ANSWER', 28, 6, 'webhook')
    logged = env.one("SELECT matched, payload FROM dial_list_webhook_log WHERE general_call_id = %s",
                     (attempt["general_call_id"],))
    assert logged[0] is True
    assert logged[1]['callDetails[disposition]'] == 'ANSWER'
    # Карточка звонка видит финал сразу — из общего кэша, без опроса.
    seen = env.svc._company_calls('remote_cc').peek(attempt["general_call_id"])
    assert (seen.call["disposition"], seen.call["billsec"]) == ('ANSWER', 28)


# ── подписание и отмены ──────────────────────────────────────────────────────

def test_signed_driver_leaves_the_list_of_whoever_has_him(env):
    # Водителя звал раньше OPERATOR, сейчас он в списке у OTHER. OPERATOR проверяет подписание с
    # вкладки итогов — подписал: строка у OTHER снята, а row_closed — только про свою строку.
    lead, old_asg = env.lead('Иванов Иван', '77010000001', operator=OPERATOR, iin='900101300007')
    env.q("UPDATE dial_list_assignments SET state = 'done', result = 'answered', done_at = now() WHERE id = %s",
          (old_asg,))
    with env.db._get_cursor() as cur:
        portion = env.portion(OTHER)
        cur.execute("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position) "
                    "VALUES (%s, %s, %s, 1) RETURNING id", (portion, lead, OTHER))
        their_asg = str(cur.fetchone()[0])
    env.q("UPDATE dial_list_leads SET signed_at = now(), sign_status = 'signed' WHERE id = %s", (lead,))
    res = env.svc.operator_sign_check(OPERATOR, lead)
    assert (res["sign"]["status"], res["row_closed"], res["text"]) == ('signed', False, '')
    assert env.assignment(their_asg)[:2] == ('done', 'other')
    assert env.one("SELECT closed_at IS NOT NULL FROM dial_list_portions WHERE id = %s", (portion,))[0] is True


def test_signed_row_is_never_closed_under_a_call_started_meanwhile(env):
    # Гонка (13.10.2026): проверка подписания запирает строку, а «Позвонить» уже держит её и
    # заводит попытку. Попытку проверка видит отдельным запросом ПОСЛЕ замка — строку не закрывает.
    lead, asg = env.lead('Иванов Иван', '77010000001', operator=OPERATOR, iin='900101300007')
    env.q("UPDATE dial_list_leads SET signed_at = now(), sign_status = 'signed' WHERE id = %s", (lead,))
    caller = env.db.connect()
    cur = caller.cursor()
    cur.execute("SELECT id FROM dial_list_assignments WHERE id = %s FOR UPDATE", (asg,))
    cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, internal_number) VALUES (%s, %s, '902')",
                (asg, OPERATOR))
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault('closed', env.svc._close_signed_row(OPERATOR, lead)))
    worker.start()
    time.sleep(0.5)                       # проверка ждёт замок строки
    assert worker.is_alive()
    caller.commit()
    caller.close()
    worker.join(5)
    assert result == {'closed': False}
    assert env.assignment(asg)[0] == 'issued'


def test_late_cancel_reopens_only_a_lead_this_attempt_exhausted(env):
    env.q("UPDATE dial_list_department_settings SET max_attempts = 5 WHERE department_id = %s", (RCC,))

    def late_cancel(attempts_before, status_before):
        lead, asg = env.lead('Иванов Иван', '7701%07d' % attempts_before)
        with env.db._get_cursor() as cur:
            cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, state, disposition) "
                        "VALUES (%s, %s, 'finished', 'NOANSWER') RETURNING id", (asg, OPERATOR))
            attempt_id = str(cur.fetchone()[0])
            cur.execute("UPDATE dial_list_assignments SET state = 'done', result = 'no_answer', attempts = 1, "
                        "done_at = now() WHERE id = %s", (asg,))
            cur.execute("UPDATE dial_list_leads SET status = %s, attempts_total = %s WHERE id = %s",
                        (status_before, attempts_before, lead))
        with env.db._get_cursor() as cur:
            assert env.svc._cancel_attempt(cur, attempt_id, asg, late=True)
        return env.lead_row(lead)

    # Предел подняли до 5 уже после исчерпания: 4 попытки, «обработан» — так и остаётся.
    assert late_cancel(4, 'done') == ('done', 3, False)
    # Эта попытка довела счётчик ровно до предела — снова в работе.
    assert late_cancel(5, 'done') == ('in_progress', 4, False)
    # Попытки кончились ещё до неё — обработанным и остаётся.
    assert late_cancel(6, 'done') == ('done', 5, False)


def test_driver_of_a_previous_department(env):
    # OPERATOR раньше работал в OLD_DEP: его водитель оттуда — на вкладках, подписание проверить
    # можно, «Позвонить ещё раз» — нет: база не его нынешнего отдела.
    lead, asg = env.lead('Давний Даниил', '77010000077', operator=OPERATOR, department=OLD_DEP, iin='900101300007')
    with env.db._get_cursor() as cur:
        cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, state, disposition, billsec) "
                    "VALUES (%s, %s, 'finished', 'BUSY', 0)", (asg, OPERATOR))
        cur.execute("UPDATE dial_list_assignments SET state = 'done', result = 'busy', done_at = now() WHERE id = %s",
                    (asg,))
    res = env.svc.operator_sign_check(OPERATOR, lead)
    assert res["lead_id"] == lead
    item, = [i for i in env.svc.operator_worked(OPERATOR)["items"] if i["lead_id"] == lead]
    assert (item["can_call_again"], item["call_again_block"], item["can_recall"]) == (
        False, 'Водитель из другого отдела', False)
    assert env.refused(env.svc.recall_lead, OPERATOR, lead) == ("Водитель не найден", 404)
    # Чужой водитель, которого у оператора не было ни разу, — по-прежнему 404.
    stranger, _ = env.lead('Чужой Чеслав', '77010000078', operator=OTHER)
    assert env.refused(env.svc.operator_sign_check, OPERATOR, stranger) == ("Водитель не найден", 404)


# ── починка: разговоры, оборванные автосбросом ────────────────────────────────

def _cut(env, name, phone, operator=OPERATOR, disposition='ANSWER', billsec=40, auto_hangup='ring_timeout',
         when=None, outcome='Отказ', ring=True):
    """Водитель, разговор с которым оборвал автосброс (по умолчанию), — строка оператора закрыта."""
    lead, asg = env.lead(name, phone, operator=operator)
    when = when or datetime.now(timezone.utc) - timedelta(hours=2)
    with env.db._get_cursor() as cur:
        cur.execute("""
            INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition, billsec,
                                            ring_timeout_at, auto_hangup, outcome_id, leg_answered_at)
            VALUES (%s, %s, %s, 'finished', %s, %s, %s, %s, %s, %s) RETURNING id
        """, (asg, operator, when, disposition, billsec, when + timedelta(seconds=25) if ring else None,
              auto_hangup if ring else '', env.outcome(outcome) if outcome else None, when))
        attempt_id = str(cur.fetchone()[0])
        cur.execute("UPDATE dial_list_assignments SET state = 'done', result = %s, attempts = 1, done_at = %s "
                    "WHERE id = %s", ('answered' if disposition == 'ANSWER' else 'no_answer',
                                      when + timedelta(seconds=40), asg))
        cur.execute("UPDATE dial_list_leads SET status = 'done', attempts_total = 1, last_attempt_at = %s, "
                    "answered_at = %s WHERE id = %s",
                    (when, when if disposition == 'ANSWER' else None, lead))
        cur.execute("UPDATE dial_list_portions SET closed_at = %s WHERE id = (SELECT portion_id FROM "
                    "dial_list_assignments WHERE id = %s)", (when + timedelta(minutes=1), asg))
    return lead, asg, attempt_id


def test_repair_returns_cut_drivers_to_their_operator(env):
    since = (datetime.now(timezone.utc) - timedelta(days=1)).astimezone(dial_service.PERIOD_TZ)
    gone, _, _ = _cut(env, 'Ушедшего Глеб', '77011110002', operator=GONE,
                      when=datetime.now(timezone.utc) - timedelta(hours=3))
    signed, _, _ = _cut(env, 'Подписавший Пётр', '77011110003')
    env.q("UPDATE dial_list_leads SET signed_at = now() WHERE id = %s", (signed,))
    issued, _, _ = _cut(env, 'Выданный Иван', '77011110004')
    env.lead('Выданный Иван 2', '77011110014', operator=OTHER)                  # не тот же лид — проверим ниже
    env.q("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position) "
          "SELECT id, %s, %s, 9 FROM dial_list_portions WHERE operator_id = %s LIMIT 1", (issued, OTHER, OTHER))
    talked, talked_asg, _ = _cut(env, 'Поговоривший Тимур', '77011110005')
    with env.db._get_cursor() as cur:
        cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition, "
                    "billsec) VALUES (%s, %s, now() - interval '30 minutes', 'finished', 'ANSWER', 95)",
                    (talked_asg, OTHER))
    _cut(env, 'Недозвон Нурлан', '77011110006', disposition='NOANSWER', billsec=0)
    _cut(env, 'Автоответчик Айдар', '77011110007', auto_hangup='announcement')
    _cut(env, 'Без сброса Борис', '77011110008', ring=False)
    _cut(env, 'Давно Дамир', '77011110009', when=datetime.now(timezone.utc) - timedelta(days=3))
    # Последним — его выдача у оператора последняя: строка вернётся в неё же («Снова в списке»).
    cut, cut_asg, cut_attempt = _cut(env, 'Оборванный Олег', '77011110001')

    dry = env.svc.repair_cut_calls(RCC, since.isoformat(), apply=False)
    assert [i["full_name"] for i in dry["items"]] == ['Ушедшего Глеб', 'Оборванный Олег']
    item = dry["items"][1]
    assert (item["lead_id"], item["attempt_id"], item["operator"], item["billsec"], item["outcome"]) == (
        cut, cut_attempt, {"id": OPERATOR, "name": 'Своев Сава'}, 40, {"name": 'Отказ', "subtype": ''})
    assert "applied" not in dry
    text = json.dumps(dry, ensure_ascii=False)
    assert '7701111' not in text and 'iin' not in text and 'phone' not in text
    # Сухой прогон ничего не меняет.
    assert env.assignment(cut_asg)[0] == 'done'

    done = env.svc.repair_cut_calls(RCC, since.isoformat(), apply=True, target='operator', actor_id=OTHER)
    assert done["applied"] == {"operator": 1, "pool": 1, "skipped": 0}
    assert {i["full_name"]: i["result"] for i in done["items"]} == {'Оборванный Олег': 'operator',
                                                                   'Ушедшего Глеб': 'pool'}
    # К тому же оператору: его строка снова открыта в его последней выдаче — «Снова в списке».
    assert env.assignment(cut_asg)[:3] == ('issued', '', 0)
    state = env.svc.get_state(OPERATOR, phone_version=GUARDED)
    row, = [i for i in state["portion"]["items"] if i["lead_id"] == cut]
    assert (row["state"], row["last_attempt"]["outcome_name"]) == ('issued', 'Отказ')
    assert env.lead_row(cut) == ('done', 1, True)                     # водителя не трогаем, как кнопка
    # Ушедшего оператора нет — водитель в общем пуле: попытки с нуля.
    assert env.lead_row(gone)[:2] == ('new', 0)
    assert env.one("SELECT COUNT(*) FROM dial_list_assignments WHERE lead_id = %s AND state = 'issued'",
                   (gone,))[0] == 0
    note, actor = env.one("SELECT note, actor_id FROM dial_list_lead_events WHERE lead_id = %s AND kind = 'requeue'",
                          (cut,))
    assert note.startswith('Автосброс оборвал разговор ') and note.endswith(' — возвращён в очередь')
    assert actor == OTHER
    # Повторный запуск ничего не повторяет — и после нового недозвона тоже.
    assert env.svc.repair_cut_calls(RCC, since.isoformat(), apply=True)["count"] == 0
    env.binotel.live.clear()
    attempt = env.call(cut_asg)
    env.ended(attempt, by_operator=False, leg_sec=20, talk_detected=False)
    env.binotel_says(attempt, 'NOANSWER')
    env.poll_now()
    env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert env.assignment(cut_asg)[0] == 'done'
    assert env.svc.repair_cut_calls(RCC, since.isoformat(), apply=False)["count"] == 0


def test_repair_appends_to_the_operators_latest_portion(env):
    # Строка оборванного разговора — в старой выдаче оператора: водитель встаёт новой строкой в
    # конец его последней выдачи, как по «Позвонить ещё раз».
    since = (datetime.now(timezone.utc) - timedelta(days=1)).astimezone(dial_service.PERIOD_TZ)
    cut, cut_asg, _ = _cut(env, 'Оборванный Олег', '77011110001')
    latest = env.portion(OPERATOR, closed=True)
    res = env.svc.repair_cut_calls(RCC, since.isoformat(), apply=True)
    assert res["applied"] == {"operator": 1, "pool": 0, "skipped": 0}
    assert env.assignment(cut_asg)[0] == 'done'
    row = env.one("SELECT portion_id, state FROM dial_list_assignments WHERE lead_id = %s AND operator_id = %s "
                  "AND state = 'issued'", (cut, OPERATOR))
    assert (str(row[0]), row[1]) == (latest, 'issued')
    assert env.one("SELECT closed_at FROM dial_list_portions WHERE id = %s", (latest,))[0] is None


def test_repair_into_the_pool_and_arguments(env):
    since = (datetime.now(timezone.utc) - timedelta(days=1)).astimezone(dial_service.PERIOD_TZ)
    cut, cut_asg, _ = _cut(env, 'Оборванный Олег', '77011110001')
    # Его строку уже успели выдать в новую выдачу другого оператора? Нет — выдаём в пул:
    # «Вернуть в список» руководителя — попытки с нуля; строку в последней выдаче оператора
    # (он ещё в отделе) открываем обратно, как при обычном «Вернуть в список».
    res = env.svc.repair_cut_calls(RCC, since.isoformat(), apply=True, target='pool', actor_id=OPERATOR)
    assert res["applied"] == {"operator": 0, "pool": 1, "skipped": 0}
    assert env.lead_row(cut)[:2] == ('new', 0)
    assert env.assignment(cut_asg)[0] == 'issued'
    # Без пояса — время Алматы; дата без времени — полночь.
    assert env.svc.parse_since('2026-10-12T00:00').utcoffset() == timedelta(hours=5)
    assert env.svc.parse_since('2026-10-12').isoformat() == '2026-10-12T00:00:00+05:00'
    for since_bad in ('', None, 'вчера', '12.10.2026'):
        assert env.refused(env.svc.repair_cut_calls, RCC, since_bad)[1] == 400
    assert env.refused(env.svc.repair_cut_calls, RCC, since.isoformat(), target='всем') == (
        "target: operator или pool", 400)


# ── ревью 13.10.2026 ──────────────────────────────────────────────────────────

def test_released_phone_keeps_waiting_on_an_ended_attempt_until_the_final(env, settle_now):
    # Находки 2, 8, 9: выпущенный 3.22.33 (без заголовка версии) после «Завершить» ждёт решения
    # сервера и понимает так: попытки нет ни в active_attempt, ни в pending_outcome — итог не нужен.
    # Ему active_attempt — в прежнем смысле, с кончившимся плечом, пока Binotel не отдал финал;
    # телефону с заголовком кончившееся плечо звонком не считается.
    lead, asg = env.lead('Иванов Иван', '77010000001')
    with env.db._get_cursor() as cur:   # в пуле — ещё один водитель
        cur.execute("INSERT INTO dial_list_leads (department_id, phone_norm, full_name) VALUES (%s, %s, %s)",
                    (RCC, '77010000009', 'Новый Ной'))
    attempt = env.svc.start_call(OPERATOR, asg)
    for event in ('ringing', 'answered', 'operator_hangup'):
        env.svc.phone_event(OPERATOR, attempt["attempt_id"], event)
    res = env.svc.phone_event(OPERATOR, attempt["attempt_id"], 'ended', by_operator=True, leg_sec=40)
    assert (res["state"], res["outcome_required"]) == ('ended', None)
    env.binotel_says(attempt, 'ONLINE')                                  # финала у Binotel ещё нет
    env.poll_now()
    state = env.svc.get_state(OPERATOR)
    assert (state["active_attempt"]["attempt_id"], state["active_attempt"]["state"]) == (attempt["attempt_id"], 'ended')
    assert state["pending_outcome"] is None
    assert env.svc.get_state(OPERATOR, phone_version=GUARDED)["active_attempt"] is None
    # «Ещё» отвечает тем же состоянием — и в нём та же попытка (строка переехала в новую выдачу).
    state = env.svc.issue_next_portion(OPERATOR)
    assert state["active_attempt"]["attempt_id"] == attempt["attempt_id"]
    # Финал «ответил» — попытка больше не идёт, итог нужен.
    env.binotel_says(attempt, 'ANSWER', 35)
    env.poll_now()
    state = env.svc.get_state(OPERATOR)
    assert state["active_attempt"] is None
    assert state["pending_outcome"]["attempt_id"] == attempt["attempt_id"]
    assert env.svc.set_attempt_outcome(OPERATOR, attempt["attempt_id"], env.outcome('Отказ'))["outcome"]["name"] == 'Отказ'
    # Отбой до ответа: финал «отменён» — попытка отменена, ждать больше нечего.
    lead2, asg2 = env.lead('Петров Пётр', '77010000002')
    second = env.svc.start_call(OPERATOR, asg2)
    for event in ('ringing', 'answered', 'operator_hangup'):
        env.svc.phone_event(OPERATOR, second["attempt_id"], event)
    env.svc.phone_event(OPERATOR, second["attempt_id"], 'ended', by_operator=True, leg_sec=6)
    assert env.svc.get_state(OPERATOR)["active_attempt"]["attempt_id"] == second["attempt_id"]
    env.binotel_says(second, 'CANCEL')
    env.poll_now()
    state = env.svc.get_state(OPERATOR)
    assert (state["active_attempt"], state["pending_outcome"]) == (None, None)
    row, = [i for i in state["portion"]["items"] if i["lead_id"] == lead2]
    assert (row["last_attempt"]["attempt_id"], row["last_attempt"]["cancelled"]) == (second["attempt_id"], True)


def test_next_locks_carried_rows_before_the_portion(env, monkeypatch):
    # Находка 15: «Ещё» переносит строку, исход которой ещё едет, а опрос в ту же секунду закрывает
    # её финалом. Закрытие запирает строку выдачи, потом выдачу; «Ещё» запирал выдачу, потом
    # строку — база обрывала одного взаимной блокировкой (40P01). Теперь «Ещё» запирает строки
    # первым и ждёт закрытия; закрытую строку не переносит. И без повтора транзакции (его спасение
    # — на случай иной встречи замков): транзакция выдачи одна.
    claims = []
    real_lock = env.svc._lock_operator
    monkeypatch.setattr(env.svc, '_lock_operator', lambda cur, user_id: claims.append(user_id) or real_lock(cur, user_id))
    portion = env.portion()
    lead_a, asg_a = env.lead('Иванов Иван', '77010000001', portion=portion)
    with env.db._get_cursor() as cur:
        cur.execute("INSERT INTO dial_list_leads (department_id, phone_norm, full_name) VALUES (%s, %s, %s)",
                    (RCC, '77010000009', 'Новый Ной'))
    attempt = env.call(asg_a)
    env.ended(attempt, by_operator=False, leg_sec=9, talk_detected=False)
    finisher = env.db.connect()
    fcur = finisher.cursor()
    # Как _finish_attempt: сначала строка выдачи под замком…
    fcur.execute("SELECT lead_id, state FROM dial_list_assignments WHERE id = %s FOR UPDATE", (asg_a,))
    result = {}

    def run_next():
        try:
            result['state'] = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)
        except Exception as exc:   # noqa: BLE001 — тест смотрит, что именно
            result['error'] = exc

    worker = threading.Thread(target=run_next)
    worker.start()
    time.sleep(0.7)
    assert worker.is_alive()                     # «Ещё» ждёт замок строки
    try:
        # …потом лид (_touch_lead) и выдача (_close_assignment). «Ещё» её ещё не трогал — ждать нечего.
        fcur.execute("UPDATE dial_list_assignments SET state = 'done', result = 'no_answer', done_at = now() "
                     "WHERE id = %s", (asg_a,))
        fcur.execute("SELECT id FROM dial_list_leads WHERE id = %s FOR UPDATE", (lead_a,))
        fcur.execute("UPDATE dial_list_leads SET status = 'in_progress', attempts_total = 1, last_attempt_at = now() "
                     "WHERE id = %s", (lead_a,))
        fcur.execute("UPDATE dial_list_portions SET closed_at = now() WHERE id = %s AND closed_at IS NULL "
                     "AND NOT EXISTS (SELECT 1 FROM dial_list_assignments WHERE portion_id = %s AND state = 'issued')",
                     (portion, portion))
        finisher.commit()
    finally:
        finisher.close()
    worker.join(10)
    assert not worker.is_alive()
    assert 'error' not in result, result
    assert claims == [OPERATOR]                  # взаимной блокировки и повтора не было
    assert [i["full_name"] for i in result['state']["portion"]["items"]] == ['Новый Ной']
    assert str(env.assignment(asg_a)[3]) == portion


def test_next_retries_once_after_a_deadlock(env, monkeypatch):
    # Иная встреча замков, которой мы не предусмотрели, — один повтор транзакции выдачи.
    import psycopg2.errors
    with env.db._get_cursor() as cur:
        cur.execute("INSERT INTO dial_list_leads (department_id, phone_norm, full_name) VALUES (%s, %s, %s)",
                    (RCC, '77010000009', 'Новый Ной'))
    real = env.svc._lock_operator
    calls = []

    def flaky(cur, user_id):
        calls.append(user_id)
        if len(calls) == 1:
            raise psycopg2.errors.DeadlockDetected('deadlock detected')
        return real(cur, user_id)

    monkeypatch.setattr(env.svc, '_lock_operator', flaky)
    state = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)
    assert [i["full_name"] for i in state["portion"]["items"]] == ['Новый Ной']
    assert len(calls) == 2
    assert env.one("SELECT COUNT(*) FROM dial_list_portions WHERE operator_id = %s", (OPERATOR,))[0] == 1
    # Второй раз подряд — ошибка наружу (500), а не бесконечные повторы.
    calls.clear()
    monkeypatch.setattr(env.svc, '_lock_operator',
                        lambda cur, user_id: calls.append(user_id) or (_ for _ in ()).throw(
                            psycopg2.errors.DeadlockDetected('deadlock detected')))
    env.q("UPDATE dial_list_assignments SET state = 'done', result = 'busy', done_at = now()")
    with env.db._get_cursor() as cur:
        cur.execute("INSERT INTO dial_list_leads (department_id, phone_norm, full_name) VALUES (%s, %s, %s)",
                    (RCC, '77010000008', 'Ещё Один'))
    with pytest.raises(psycopg2.errors.DeadlockDetected):
        env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)
    assert len(calls) == 2


def test_late_operator_hangup_never_cancels_a_talk_the_phone_heard(env):
    # Находка 16: ended(talk_detected true) пришёл раньше operator_hangup (отдельные запросы), Binotel
    # закрыл попытку «не ответил». Запоздавший operator_hangup — без ключа, но разговор уже записан:
    # поздней отмены нет, сохранить итог можно.
    lead, asg = env.lead('Иванов Иван', '77010000001')
    attempt = env.call(asg)
    assert env.ended(attempt, by_operator=True, leg_sec=40, talk_detected=True)["outcome_required"] is True
    env.binotel_says(attempt, 'NOANSWER')
    env.poll_now()
    env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert env.attempt_row(attempt)[:2] == ('finished', 'NOANSWER')
    res = env.svc.phone_event(OPERATOR, attempt["attempt_id"], 'operator_hangup', phone_version=GUARDED)
    assert res["cancelled"] is False
    assert env.attempt_row(attempt)[5] is False
    assert env.assignment(asg)[:3] == ('done', 'no_answer', 1)
    assert env.svc.set_attempt_outcome(OPERATOR, attempt["attempt_id"], env.outcome('Отказ'))["outcome"]["name"] == 'Отказ'


def test_leg_end_and_poll_data_use_one_clock(env, monkeypatch):
    # Находка 17: конец плеча писался часами базы, данные опроса помечены часами приложения. Часы
    # приложения на 5 с впереди — ANSWER, полученный посреди разговора с неполной длительностью,
    # сходил за финал (длительность так и оставалась неполной). Теперь конец плеча — по часам
    # приложения, как и данные опроса.
    from dial_list import live_calls
    skew = timedelta(seconds=5)
    real_utcnow = dial_service._utcnow

    def ahead():
        return real_utcnow() + skew

    monkeypatch.setattr(dial_service, '_utcnow', ahead)
    company = dial_service.DEFAULT_BINOTEL_COMPANY
    env.svc._calls[company] = live_calls.CompanyCalls(company, submit=_sync, wall_clock=ahead)
    lead, asg = env.lead('Иванов Иван', '77010000001')
    attempt = env.call(asg)
    env.binotel_says(attempt, 'ANSWER', 5)                        # посреди разговора — неполная
    assert env.svc.attempt_live(OPERATOR, attempt["attempt_id"])["talking"] is True
    env.ended(attempt, by_operator=True, leg_sec=40, talk_detected=True)
    env.svc.get_state(OPERATOR, phone_version=GUARDED)            # добор исходов — по кэшу посреди разговора
    assert env.attempt_row(attempt)[:3] == ('ended', '', 0)       # неполная длительность финалом не стала
    stored = env.one("SELECT phone_ended_at FROM dial_list_attempts WHERE id = %s", (attempt["attempt_id"],))[0]
    assert stored > datetime.now(timezone.utc) + timedelta(seconds=3)          # часы приложения
    time.sleep(1.1)                                               # данные после конца плеча
    env.binotel_says(attempt, 'ANSWER', 42, 9)
    env.poll_now()
    env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert env.attempt_row(attempt)[:3] == ('finished', 'ANSWER', 42)


def test_operator_confirmed_announcement_counts_like_the_phones_own(env, settle_now):
    # Режим suggest: оператор завершил звонок по подсказке «похоже на автоответчик» — телефон шлёт
    # ended как сообщение сети с announcement_by: operator. Это недозвон в счёте: итог не нужен и
    # не ставится, не отмена — и при ANSWER с длительностью (автоответчик), и при отмене у Binotel.
    lead, asg = env.lead('Автоответчик Айдар', '77010000005')
    attempt = env.call(asg)
    keys = dict(by_operator=False, ring_timeout=True, auto_hangup='announcement', talk_detected=False,
                announcement_suspected=True, announcement_by='operator')
    res = env.ended(attempt, leg_sec=11, **keys)
    assert (res["cancelled"], res["outcome_required"]) == (False, False)
    assert env.one("SELECT auto_hangup, announcement_by, announcement_suspected, ring_timeout_at IS NOT NULL, "
                   "operator_hangup_at IS NULL, cancelled FROM dial_list_attempts WHERE id = %s",
                   (attempt["attempt_id"],)) == ('announcement', 'operator', True, True, True, False)
    env.binotel_says(attempt, 'ANSWER', 14)
    env.poll_now()
    env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert env.attempt_row(attempt)[:3] == ('finished', 'ANSWER', 14)
    assert env.assignment(asg)[:3] == ('done', 'no_answer', 1)
    assert env.lead_row(lead) == ('in_progress', 1, False)
    assert env.pending() is None
    assert env.refused(env.svc.set_attempt_outcome, OPERATOR, attempt["attempt_id"], env.outcome('Отказ')) == (
        "Абонент недоступен — итог не нужен", 409)
    # Binotel отдал отмену (плечо положили до ответа), а следом пришёл запоздалый отбой — не отмена.
    lead2, asg2 = env.lead('Недоступный Нурбол', '77010000006')
    second = env.call(asg2)
    env.ended(second, leg_sec=8, **keys)
    env.binotel_says(second, 'CANCEL')
    env.poll_now()
    env.svc.get_state(OPERATOR, phone_version=GUARDED)
    env.svc.phone_event(OPERATOR, second["attempt_id"], 'operator_hangup', phone_version=GUARDED)
    assert env.attempt_row(second)[:2] == ('finished', 'CANCEL') and env.attempt_row(second)[5] is False
    assert env.assignment(asg2)[:3] == ('done', 'no_answer', 1)


def test_announcement_stats_on_real_sql(env):
    # Числа для владельца: подозрительные плечи против финалов Binotel (без номеров и ФИО).
    lead_rows = []

    def attempt(n, suspected, disposition, state='finished', auto='', by=None, talk=None, billsec=0,
                phone=None):
        lead, asg = env.lead('Водитель %d' % n, phone or '770100001%02d' % n)
        with env.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO dial_list_attempts (assignment_id, operator_id, state, disposition, billsec,
                                                auto_hangup, announcement_by, announcement_suspected, talk_detected)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (asg, OPERATOR, state, disposition, billsec, auto, by, suspected, talk))
        lead_rows.append(lead)

    attempt(1, True, 'BUSY')
    attempt(2, True, 'CHANUNAVAIL')
    attempt(3, True, 'NOANSWER')
    attempt(4, True, 'ANSWER', talk=True, billsec=40)
    attempt(5, True, 'ANSWER', talk=False, billsec=3)
    attempt(6, True, 'CANCEL', auto='announcement')                     # телефон положил сам (auto)
    attempt(7, True, 'CANCEL', auto='announcement', by='operator')       # оператор по подсказке
    attempt(8, True, '', state='ended')                                  # исхода ещё нет
    attempt(9, False, 'BUSY')                                            # пропущенное сообщение сети
    attempt(10, False, 'ANSWER', billsec=60)
    attempt(11, None, 'BUSY')                                            # телефон не сообщал — не в счёт
    attempt(12, True, 'BUSY', phone='77010000999')                       # тестовый номер — не в счёт
    env.q("INSERT INTO test_phone_numbers (phone_key) VALUES ('7010000999')")
    # Разбор звука выключен (13.10.2026): прежние переменные окружения ни на что не влияют.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv('DIAL_LIST_ANNOUNCEMENT_MODE', 'suggest')
        stats = env.svc.announcement_stats(RCC)
    assert (stats["analysed"], stats["suspected"]) == (10, 8)
    assert stats["ended_by"] == {"auto": 1, "sit": 0, "operator": 1, "none": 6}
    assert stats["finals"] == {"network": 2, "answered": 2, "no_answer": 1, "cancel": 0, "other": 0, "pending": 1}
    assert stats["finals_ended_by_us"] == {"network": 0, "answered": 0, "no_answer": 0, "cancel": 2, "other": 0,
                                           "pending": 0}
    assert stats["precision"] == {"network": 2, "no_answer": 1, "answered_talk": 1, "answered_silent": 1,
                                  "decided": 5, "percent": 40.0}
    assert (stats["talked_after"], stats["answered_long"], stats["network_missed"]) == (1, 1, 1)
    assert stats["dispositions"][0] == {"disposition": 'ANSWER', "count": 2}
    assert stats["settings"] == {"announcement_mode": 'off', "announcement_sit": False, "announcement_ms": 0}
    text = json.dumps(stats, ensure_ascii=False)
    assert '7701' not in text and 'Водитель' not in text
    # Окно в днях: старые попытки не в счёте; days проверяется.
    env.q("UPDATE dial_list_attempts SET requested_at = now() - interval '30 days'")
    assert env.svc.announcement_stats(RCC, days=14)["analysed"] == 0
    assert env.svc.announcement_stats(RCC, days='31')["analysed"] == 10
    for bad in ('ноль', 0, 93, -1):
        assert env.refused(env.svc.announcement_stats, RCC, days=bad)[1] == 400
