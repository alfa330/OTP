# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: DIAL_LIST_TEST_PORT — порт локального Postgres.

Обзвон из телефона, 12–13.10.2026: что телефон услышал на плече (talk_detected), сообщение
сети «абонент недоступен» (auto_hangup 'announcement'), комментарии оператора
(last_comment), «Позвонить ещё раз» и версия телефона в заголовке. Строковые проверки в
tests/test_dial_list.py ловят, что запрос НАПИСАН, а не что он ЗНАЧИТ. Здесь весь путь — от
событий телефона и исхода Binotel до долга по итогу, строки выдачи, водителя и счётчиков —
исполняется на настоящей схеме раздела (dial_list.schema).

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным именем,
в конце она удаляется. Binotel и Sapar подменены. Запуск на локальном кластере:

    DIAL_LIST_TEST_PORT=55441 python -m pytest tests/test_dial_list_phone_heard_postgres.py

Люди и номера выдуманы.
"""
import contextlib
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

RCC = 1954
OPERATOR, OTHER = 520, 521
# Сборка с защитой по речи до выпуска: заголовок есть, а номер в нём ещё старый.
GUARDED = '3.22.33'

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE test_phone_numbers (phone_key varchar(10) UNIQUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text);
    CREATE TABLE sip_department_config (department_id integer PRIMARY KEY, provider text,
                                        sip_server text, ring_timeout_rings smallint);
    INSERT INTO departments (id, name, code) VALUES (1954, 'Удаленный КЦ', 'remote_cc');
    INSERT INTO sip_department_config VALUES (1954, 'binotel', 'sip53.binotel.com', NULL);
    INSERT INTO users (id, name, login, role, department_id, sip_number) VALUES
        (520, 'Своев Сава', 'sava', 'operator', 1954, '902'),
        (521, 'Новичков Назар', 'nazar', 'operator', 1954, '903');
'''


class _Db:
    """_get_cursor на своей схеме: отдельное соединение, фиксация в конце блока."""

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
    """Binotel без сети: click-to-call отдаёт generalCallID, call-details — то, что тест
    положил в live (нет записи — звонка Binotel ещё не видит)."""

    def __init__(self):
        self.live = {}
        self.originated = []

    def originate_internal_to_external(self, internal_number, external_number, **extra):
        self.originated.append((internal_number, external_number))
        return 'gc-%d' % len(self.originated)

    def call_details(self, general_call_ids, max_wait=None, timeout=None):
        ids = general_call_ids if isinstance(general_call_ids, (list, tuple)) else [general_call_ids]
        return {str(g): dict(self.live[str(g)], general_call_id=str(g)) for g in ids if str(g) in self.live}


class _NoSapar:
    """Sapar не настроен: перед звонком подписание не спрашиваем."""

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

    def lead(self, name, phone, operator=OPERATOR, portion=None):
        """Водитель и его строка в выдаче оператора (новой, если portion не дана)."""
        with self.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO dial_list_leads (department_id, phone_norm, full_name) VALUES (%s, %s, %s) RETURNING id
            """, (RCC, phone, name))
            lead_id = str(cur.fetchone()[0])
            if portion is None:
                cur.execute("INSERT INTO dial_list_portions (operator_id, department_id, size) VALUES (%s, %s, 1) "
                            "RETURNING id", (operator, RCC))
                portion = str(cur.fetchone()[0])
            cur.execute("""
                INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position)
                VALUES (%s, %s, %s, 1) RETURNING id
            """, (portion, lead_id, operator))
            assignment_id = str(cur.fetchone()[0])
        return lead_id, assignment_id

    def call(self, assignment_id, operator=OPERATOR):
        """«Позвонить», плечо пришло и принято — водителя АТС ещё набирает."""
        attempt = self.svc.start_call(operator, assignment_id)
        self.svc.phone_event(operator, attempt["attempt_id"], 'ringing', phone_version=GUARDED)
        self.svc.phone_event(operator, attempt["attempt_id"], 'answered', phone_version=GUARDED)
        return attempt

    def ended(self, attempt, operator=OPERATOR, phone_version=GUARDED, **keys):
        return self.svc.phone_event(operator, attempt["attempt_id"], 'ended', phone_version=phone_version, **keys)

    def final(self, attempt, disposition, billsec):
        """Исход от Binotel — вебхуком «API Call Completed»."""
        return self.svc.handle_webhook({"generalCallID": attempt["general_call_id"], "disposition": disposition,
                                        "billsec": str(billsec), "waitsec": "7"}, "194.88.218.116")

    def pending(self, operator=OPERATOR):
        with self.db._get_cursor() as cur:
            debt = self.svc._pending_outcome(cur, operator)
        return debt and debt["attempt_id"]

    def save(self, attempt, outcome='Отказ', comment='', operator=OPERATOR):
        """Итог по попытке: название итога либо (текст отказа, статус)."""
        try:
            return self.svc.set_attempt_outcome(operator, attempt["attempt_id"], self.outcome(outcome),
                                                comment)["outcome"]["name"]
        except dial_service.DialListError as exc:
            return str(exc), exc.status

    def attempt_row(self, attempt):
        return self.one("""
            SELECT state, disposition, cancelled, auto_hangup, talk_detected, ring_timeout_at IS NOT NULL,
                   operator_hangup_at IS NOT NULL
            FROM dial_list_attempts WHERE id = %s
        """, (attempt["attempt_id"],))

    def assignment(self, assignment_id):
        return self.one("SELECT state, result, attempts FROM dial_list_assignments WHERE id = %s", (assignment_id,))

    def lead_row(self, lead_id):
        return self.one("SELECT status, attempts_total, answered_at IS NOT NULL FROM dial_list_leads WHERE id = %s",
                        (lead_id,))


@pytest.fixture
def env():
    schema = 't_dial_heard_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled) VALUES (1954, TRUE)")
        cur.execute("INSERT INTO dial_list_outcomes (department_id, name, position, requeue) VALUES "
                    "(1954, 'Отказ', 1, FALSE), (1954, 'Перезвонить позже', 2, TRUE)")
    binotel = _Binotel()
    svc = dial_service.DialListService(db, client_factory=lambda dep: binotel, sign_checker=_NoSapar())
    svc.operator_context = lambda uid: {
        "user_id": int(uid), "name": "Оператор", "department_id": RCC,
        "internal_number": {OPERATOR: '902', OTHER: '903'}[int(uid)], "settings": svc.department_settings(RCC)}
    try:
        yield _Env(db, svc, binotel)
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


# ── talk_detected: был ли разговор ───────────────────────────────────────────

def test_heard_talk_ended_by_operator_needs_outcome_at_once_and_is_never_cancelled(env):
    # Оператор закончил разговор сам, телефон разговор слышал: окно итога — сразу, без
    # исхода Binotel. «Не ответил» от Binotel потом не отменяет попытку, а итог, сохранённый
    # до него, остаётся (ревью 13.10.2026: долга по такой попытке больше нет — см. ниже).
    lead, asg = env.lead('Иванов Иван', '77010000001')
    attempt = env.call(asg)
    res = env.ended(attempt, by_operator=True, leg_sec=40, talk_detected=True)
    assert (res["state"], res["final"], res["cancelled"], res["outcome_required"]) == ('ended', False, False, True)
    assert env.pending() == attempt["attempt_id"]
    assert env.save(attempt, comment='дорого') == 'Отказ'
    assert env.pending() is None
    env.final(attempt, 'NOANSWER', 0)
    assert env.attempt_row(attempt)[:3] == ('finished', 'NOANSWER', False)
    assert env.assignment(asg) == ('done', 'no_answer', 1)            # попытка в счёте, с исходом Binotel
    assert env.lead_row(lead) == ('in_progress', 1, False)
    assert env.one("SELECT o.name, t.operator_comment FROM dial_list_attempts t "
                   "JOIN dial_list_outcomes o ON o.id = t.outcome_id WHERE t.id = %s",
                   (attempt["attempt_id"],)) == ('Отказ', 'дорого')
    assert env.pending() is None


def test_binotel_no_answer_outranks_a_talk_the_phone_thought_it_heard(env):
    # Ревью 13.10.2026: «Абонент недоступен.» — пауза — оператор «алло?» — «Позвоните позднее»:
    # для телефона это обмен репликами (talk_detected true), и окно итога открылось. Binotel
    # закрыл звонок финальным «не дозвонились» — итог не нужен и следующий звонок не держит;
    # попытка — недозвон в счёте, не отмена «до ответа».
    results = {'BUSY': 'busy', 'NOANSWER': 'no_answer', 'CANCEL': 'no_answer', 'CHANUNAVAIL': 'other',
               'CONGESTION': 'other'}
    for n, (disposition, result) in enumerate(results.items()):
        lead, asg = env.lead('Водитель %d' % n, '7701100000%d' % n)
        attempt = env.call(asg)
        res = env.ended(attempt, by_operator=True, leg_sec=9, talk_detected=True)
        assert res["outcome_required"] is True, disposition
        assert env.pending() == attempt["attempt_id"], disposition
        env.final(attempt, disposition, 0)
        assert env.pending() is None, disposition
        assert env.attempt_row(attempt)[:3] == ('finished', disposition, False), disposition
        assert env.assignment(asg) == ('done', result, 1), disposition
        assert env.lead_row(lead) == ('in_progress', 1, False), disposition
        state = env.svc.get_state(OPERATOR, phone_version=GUARDED)
        assert state["pending_outcome"] is None, disposition
    # Звонить дальше можно — сервер «Сначала укажите итог» не просит.
    lead, asg = env.lead('Следующий Семён', '77011000099')
    nxt = env.svc.start_call(OPERATOR, asg)
    assert nxt["lead_id"] == lead
    # Окно итога у оператора уже открыто — сохранить итог всё равно можно, он останется.
    assert env.save(attempt) == 'Отказ'
    # Исход пришёл раньше конца плеча (вебхук обогнал телефон) — итог не нужен сразу.
    env.ended(nxt, by_operator=False, leg_sec=3, talk_detected=False)
    lead, asg = env.lead('Ранний Роман', '77011000098')
    attempt = env.call(asg)
    env.final(attempt, 'BUSY', 0)
    res = env.ended(attempt, by_operator=True, leg_sec=12, talk_detected=True)
    assert (res["final"], res["cancelled"], res["outcome_required"]) == (True, False, False)
    # Таймаут (UNKNOWN) — не ответ Binotel: разговор, который слышал телефон, — итог нужен.
    lead, asg = env.lead('Таймаут Тимур', '77011000097')
    attempt = env.call(asg)
    env.ended(attempt, by_operator=False, leg_sec=30, talk_detected=True)
    env.svc._finish_attempt(attempt["attempt_id"], 'UNKNOWN', 0, 0, 'timeout')
    assert env.pending() == attempt["attempt_id"]


def test_no_talk_heard_waits_for_binotel(env):
    # Телефон слушал плечо и разговора не услышал: итог нужен, только если Binotel
    # подтвердит ответ не короче секунды.
    lead, asg = env.lead('Петров Пётр', '77010000002')
    attempt = env.call(asg)
    res = env.ended(attempt, by_operator=False, leg_sec=14, talk_detected=False)
    assert (res["state"], res["outcome_required"]) == ('ended', None)
    assert env.pending() is None
    assert env.save(attempt) == ("Разговор не подтверждён: итог нужен, только если водитель ответил. "
                                 "Дождитесь исхода от АТС", 409)
    env.final(attempt, 'BUSY', 0)
    assert env.assignment(asg) == ('done', 'busy', 1)
    assert env.pending() is None
    assert env.save(attempt) == ("Водитель не ответил — итог не нужен", 409)
    # Ответ без единой секунды — недозвон: строка «Не дозвонились», водитель не дозвонён.
    lead2, asg2 = env.lead('Сидоров Сидор', '77010000003')
    attempt = env.call(asg2)
    env.ended(attempt, by_operator=False, leg_sec=6, talk_detected=False)
    env.final(attempt, 'ANSWER', 0)
    assert env.assignment(asg2) == ('done', 'no_answer', 1)
    assert env.lead_row(lead2) == ('in_progress', 1, False)
    assert env.pending() is None
    # Ответ с разговором — итог нужен.
    lead3, asg3 = env.lead('Ахметов Арман', '77010000004')
    attempt = env.call(asg3)
    env.ended(attempt, by_operator=False, leg_sec=30, talk_detected=False)
    env.final(attempt, 'ANSWER', 12)
    assert env.assignment(asg3) == ('done', 'answered', 1)
    assert env.lead_row(lead3) == ('done', 1, True)
    assert env.pending() == attempt["attempt_id"]
    assert env.save(attempt) == 'Отказ'


def test_phone_without_the_key_behaves_as_before(env):
    # Телефон 3.22.33 ключей не шлёт: конец плеча — итог сразу; отбой оператора — решает
    # Binotel, «не ответил» — попытка отменена и не в счёте; сброс на гудках — по Binotel.
    lead, asg = env.lead('Иванов Иван', '77010000001')
    attempt = env.call(asg)
    res = env.ended(attempt, phone_version=None, by_operator=False, leg_sec=50)
    assert res["outcome_required"] is True
    assert env.pending() == attempt["attempt_id"]
    env.final(attempt, 'ANSWER', 45)
    assert env.save(attempt) == 'Отказ'

    lead, asg = env.lead('Петров Пётр', '77010000002')
    attempt = env.call(asg)
    res = env.ended(attempt, phone_version=None, by_operator=True, leg_sec=12)
    assert res["outcome_required"] is None
    env.final(attempt, 'NOANSWER', 0)
    assert env.attempt_row(attempt)[2] is True                        # отменена
    assert env.assignment(asg) == ('issued', '', 0)                   # строка в работе, попытка не в счёте
    assert env.pending() is None

    lead, asg = env.lead('Сидоров Сидор', '77010000003')
    attempt = env.call(asg)
    res = env.ended(attempt, phone_version=None, by_operator=False, leg_sec=26, ring_timeout=True)
    assert res["outcome_required"] is None
    assert env.attempt_row(attempt)[3:6] == ('ring_timeout', None, True)
    env.final(attempt, 'ANSWER', 0)                                   # ответил в последний момент
    assert env.pending() == attempt["attempt_id"]


# ── сообщение сети ───────────────────────────────────────────────────────────

def test_network_announcement_is_a_no_answer_whatever_binotel_says(env):
    lead, asg = env.lead('Иванов Иван', '77010000001')
    attempt = env.call(asg)
    res = env.ended(attempt, by_operator=False, leg_sec=9, ring_timeout=True, auto_hangup='announcement',
                    talk_detected=False)
    assert (res["final"], res["cancelled"], res["outcome_required"]) == (False, False, False)
    assert env.attempt_row(attempt)[3:7] == ('announcement', False, True, False)
    # Автоответчик: Binotel отвечает ANSWER с длительностью — всё равно недозвон.
    env.final(attempt, 'ANSWER', 15)
    assert env.assignment(asg) == ('done', 'no_answer', 1)
    assert env.lead_row(lead) == ('in_progress', 1, False)
    assert env.pending() is None
    assert env.save(attempt) == ("Абонент недоступен — итог не нужен", 409)
    assert env.svc.attempt_live(OPERATOR, attempt["attempt_id"])["result"] == 'no_answer'
    # Поздний сигнал о гудках причину не затирает.
    with env.db._get_cursor() as cur:
        env.svc._mark_ring_timeout(cur, attempt["attempt_id"])
    assert env.attempt_row(attempt)[3] == 'announcement'

    # Для сравнения — обычный разговор телефона 3.22.33 с другим водителем.
    lead2, asg2 = env.lead('Петров Пётр', '77010000002')
    talk = env.call(asg2)
    env.ended(talk, phone_version=None, by_operator=False, leg_sec=40)
    env.final(talk, 'ANSWER', 30)
    assert env.save(talk) == 'Отказ'

    # «Мой прогресс»: сообщение сети — попытка, но не дозвон и не минуты разговора.
    progress = env.svc.operator_progress(OPERATOR)
    assert (progress["month"]["attempts"], progress["month"]["answered"], progress["month"]["talk_sec"]) == (2, 1, 30)
    assert progress["conversion"]["leads_talked"] == 1
    # «Аналитика» руководителя — то же.
    totals = env.svc.analytics(RCC)["totals"]
    assert (totals["called"], totals["answered"], totals["talked"], totals["attempts"]) == (2, 1, 1, 2)
    # Успешка: оба подписали после звонка — засчитана только за разговор.
    env.q("UPDATE dial_list_leads SET signed_at = CURRENT_TIMESTAMP WHERE id IN (%s, %s)", (lead, lead2))
    assert SignChecker(env.db).resolve_successes() == 1
    assert env.one("SELECT success_attempt_id FROM dial_list_leads WHERE id = %s", (lead,))[0] is None
    assert str(env.one("SELECT success_attempt_id FROM dial_list_leads WHERE id = %s", (lead2,))[0]) == \
        talk["attempt_id"]


def test_announcement_after_an_early_answer_takes_the_answer_back(env):
    # Вебхук ANSWER (автоответчик) обогнал событие телефона: дозвон уже засчитан —
    # событие с сообщением сети снимает его в той же транзакции.
    lead, asg = env.lead('Иванов Иван', '77010000001')
    attempt = env.call(asg)
    env.final(attempt, 'ANSWER', 15)
    assert env.assignment(asg) == ('done', 'answered', 1)
    assert env.lead_row(lead) == ('done', 1, True)
    res = env.ended(attempt, by_operator=False, leg_sec=8, ring_timeout=True, auto_hangup='announcement',
                    talk_detected=False)
    assert (res["final"], res["outcome_required"]) == (True, False)
    assert env.assignment(asg) == ('done', 'no_answer', 1)
    assert env.lead_row(lead) == ('in_progress', 1, False)            # попытки не кончились — снова в работе
    assert env.attempt_row(attempt)[3:5] == ('announcement', None)    # «разговора не было» не пишется
    assert env.pending() is None


# ── сброс на гудках и версия телефона ────────────────────────────────────────

def test_ring_timeout_follows_the_header_presence(env):
    assert env.svc.get_state(OPERATOR)["ring_timeout_sec"] == 0
    assert env.svc.get_state(OPERATOR, phone_version='мусор')["ring_timeout_sec"] == 0
    for version in ('3.22.33', '3.22.34', '3.23.0'):
        assert env.svc.get_state(OPERATOR, phone_version=version)["ring_timeout_sec"] == 25, version
    env.q("UPDATE sip_department_config SET ring_timeout_rings = 3 WHERE department_id = 1954")
    assert env.svc.get_state(OPERATOR, phone_version=GUARDED)["ring_timeout_sec"] == 15
    assert env.svc.get_state(OPERATOR)["ring_timeout_sec"] == 0

    lead, asg = env.lead('Иванов Иван', '77010000001')
    attempt = env.call(asg)
    env.binotel.live[attempt["general_call_id"]] = {"disposition": "CALLING", "billsec": 0}
    # Без заголовка (выпущенный 3.22.33): «не класть», попытку не трогаем.
    res = env.svc.phone_event(OPERATOR, attempt["attempt_id"], 'ring_timeout', ring_sec=25)
    assert (res["hangup"], res["talking"]) == (False, False)
    assert env.attempt_row(attempt)[5] is False
    # С заголовком — решает Binotel: набор ещё идёт — «класть», попытка помечена сбросом.
    res = env.svc.phone_event(OPERATOR, attempt["attempt_id"], 'ring_timeout', ring_sec=25, phone_version=GUARDED)
    assert (res["hangup"], res["talking"], res["disposition"]) == (True, False, 'CALLING')
    assert env.attempt_row(attempt)[3:6] == ('ring_timeout', None, True)
    # А на линии была речь: телефон не положил, разговор состоялся, его закончил оператор.
    del env.binotel.live[attempt["general_call_id"]]
    res = env.ended(attempt, by_operator=True, leg_sec=95, talk_detected=True)
    assert res["outcome_required"] is True
    assert env.attempt_row(attempt)[3:7] == ('', True, False, True)    # отметка сброса снята
    env.final(attempt, 'ANSWER', 60)
    assert env.assignment(asg) == ('done', 'answered', 1)
    assert env.save(attempt) == 'Отказ'


# ── комментарии оператора ────────────────────────────────────────────────────

def test_last_comment_reaches_queue_tabs_call_and_state(env):
    now = datetime.now(timezone.utc)
    two_days, hour = now - timedelta(days=2), now - timedelta(hours=1)
    refuse, later = env.outcome('Отказ'), env.outcome('Перезвонить позже')
    # Иванов: прошлая строка оператора с комментарием, новая строка в очереди, свежая попытка без
    # комментария и чужой комментарий новее всех — показать нужно «не берёт после обеда».
    lead, asg = env.lead('Иванов Иван', '77010000001')
    with env.db._get_cursor() as cur:
        cur.execute("INSERT INTO dial_list_portions (operator_id, department_id, size, issued_at, closed_at) "
                    "VALUES (%s, %s, 1, %s, %s) RETURNING id", (OPERATOR, RCC, two_days, two_days))
        old_portion = cur.fetchone()[0]
        cur.execute("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state, result, "
                    "created_at, done_at) VALUES (%s, %s, %s, 1, 'done', 'answered', %s, %s) RETURNING id",
                    (old_portion, lead, OPERATOR, two_days, two_days))
        old_asg = cur.fetchone()[0]
        cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition, "
                    "billsec, outcome_id, operator_comment, outcome_at) VALUES "
                    "(%s, %s, %s, 'finished', 'ANSWER', 40, %s, 'не берёт после обеда', %s)",
                    (old_asg, OPERATOR, two_days, later, two_days))
        cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition) "
                    "VALUES (%s, %s, %s, 'finished', 'NOANSWER')", (asg, OPERATOR, hour))
        cur.execute("INSERT INTO dial_list_portions (operator_id, department_id, size, issued_at, closed_at) "
                    "VALUES (%s, %s, 1, %s, %s) RETURNING id", (OTHER, RCC, two_days, two_days))
        other_portion = cur.fetchone()[0]
        cur.execute("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state, result, "
                    "created_at, done_at) VALUES (%s, %s, %s, 1, 'done', 'answered', %s, %s) RETURNING id",
                    (other_portion, lead, OTHER, now - timedelta(minutes=40), now - timedelta(minutes=30)))
        other_asg = cur.fetchone()[0]
        cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition, "
                    "billsec, outcome_id, operator_comment, outcome_at) VALUES "
                    "(%s, %s, %s, 'finished', 'ANSWER', 20, %s, 'чужой комментарий', %s)",
                    (other_asg, OTHER, now - timedelta(minutes=35), refuse, now - timedelta(minutes=30)))
    today = dial_service._today()
    expected = {"last_comment": 'не берёт после обеда',
                "last_comment_label": dial_service.checked_at_label(two_days, today),
                "last_comment_outcome": 'Перезвонить позже'}

    def fields(item):
        return {key: item[key] for key in expected}

    state = env.svc.get_state(OPERATOR, phone_version=GUARDED)
    item, = state["portion"]["items"]
    assert fields(item) == expected
    # Последняя попытка самой строки — свежий недозвон без итога; комментарий — из прошлой строки.
    assert (item["last_attempt"]["disposition"], item["last_attempt"]["outcome_name"]) == ('NOANSWER', '')
    # Вкладка итогов: прошлая строка с итогом «Перезвонить позже» у оператора не последняя по
    # водителю (новая — в очереди), поэтому на вкладках его нет; проверим недозвон Петрова ниже.
    lead2, asg2 = env.lead('Петров Пётр', '77010000002')
    with env.db._get_cursor() as cur:
        cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition, "
                    "operator_comment, outcome_at, outcome_id) VALUES "
                    "(%s, %s, %s, 'finished', 'ANSWER', 'просил вечером', %s, %s)",
                    (asg2, OPERATOR, two_days, two_days, later))
        cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition) "
                    "VALUES (%s, %s, %s, 'finished', 'BUSY')", (asg2, OPERATOR, hour))
        cur.execute("UPDATE dial_list_assignments SET state = 'done', result = 'busy', done_at = %s WHERE id = %s",
                    (hour, asg2))
    worked = env.svc.operator_worked(OPERATOR)
    petrov, = [i for i in worked["items"] if i["lead_id"] == lead2]
    assert (petrov["tab"], petrov["comment"], petrov["last_comment"], petrov["last_comment_outcome"]) == \
        ('no_answer', '', 'просил вечером', 'Перезвонить позже')
    # «Позвонить» и идущий звонок в состоянии.
    attempt = env.svc.start_call(OPERATOR, asg)
    assert fields(attempt) == expected
    assert attempt["lead_id"] == lead
    active = env.svc.get_state(OPERATOR, phone_version=GUARDED)["active_attempt"]
    assert fields(active) == expected
    # Ни номера, ни ИИН в ответах телефону.
    for reply in (state, worked, attempt, active):
        assert '7701000000' not in repr(reply)


# ── «Позвонить ещё раз» ──────────────────────────────────────────────────────

def test_call_again_resets_the_row_counter_but_manager_requeue_does_not(env):
    hour = datetime.now(timezone.utc) - timedelta(hours=1)
    lead, asg = env.lead('Иванов Иван', '77010000001')
    portion = str(env.one("SELECT portion_id FROM dial_list_assignments WHERE id = %s", (asg,))[0])
    lead2, asg2 = env.lead('Петров Пётр', '77010000002', portion=portion)
    for lead_id, assignment_id in ((lead, asg), (lead2, asg2)):
        with env.db._get_cursor() as cur:
            cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition) "
                        "VALUES (%s, %s, %s, 'finished', 'NOANSWER')", (assignment_id, OPERATOR, hour))
            cur.execute("UPDATE dial_list_assignments SET state = 'done', result = 'no_answer', attempts = 2, "
                        "done_at = %s WHERE id = %s", (hour, assignment_id))
            cur.execute("UPDATE dial_list_leads SET status = 'done', attempts_total = 3 WHERE id = %s", (lead_id,))
    env.q("UPDATE dial_list_portions SET closed_at = %s WHERE id = %s", (hour, portion))
    res = env.svc.recall_lead(OPERATOR, lead, phone_version=GUARDED)
    assert res["assignment_id"] == asg
    assert env.assignment(asg) == ('issued', '', 0)                   # с нуля, как новая строка
    assert env.lead_row(lead) == ('done', 3, False)                   # водителя не трогаем
    assert env.one("SELECT closed_at FROM dial_list_portions WHERE id = %s", (portion,))[0] is None
    # «Вернуть в список» руководителя переоткрывает строку, но счётчик не обнуляет.
    card = env.svc.requeue_lead(lead2, actor_id=OTHER)
    assert card["reopened_for_operator"] is True
    assert env.assignment(asg2) == ('issued', '', 2)
