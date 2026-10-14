# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: DIAL_LIST_TEST_PORT — порт локального Postgres.

«Оплата по конверсии» в «Моём прогрессе» и «Рейтинг» дня (владелец, 14.10.2026). Строковые
проверки в tests/test_dial_list.py ловят, что запрос НАПИСАН, а не что он ЗНАЧИТ. Здесь
зарплата и рейтинг считаются на настоящей схеме раздела (dial_list.schema): диалоги по
водителям, короткий ответ, сообщение сети, отменённая до ответа попытка, недозвон, повторный
разговор с тем же водителем, успешки, засчитанные настоящим SignChecker по попытке этого и
другого дня, базы разных месяцев и другой отдел, номер «Реестра тестовых номеров», границы
суток по Алматы.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным именем,
в конце она удаляется. Binotel и Sapar не нужны. Запуск на локальном кластере:

    DIAL_LIST_TEST_PORT=55441 python -m pytest tests/test_dial_list_salary_postgres.py

Люди и номера выдуманы.
"""
import contextlib
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import pay  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402
from dial_list.sign_check import SignChecker  # noqa: E402

PORT = os.environ.get('DIAL_LIST_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DIAL_LIST_TEST_PORT)')

RCC, OTHER_DEP = 1954, 1955
SAVA, NAZAR, TAMARA, PROKHOR = 520, 521, 522, 523
TZ = dial_service.PERIOD_TZ
# База, которую обзванивает отдел (active_period), и база прошлого месяца.
BASE, OLD_BASE = date(2026, 10, 1), date(2026, 9, 1)
TEST_PHONE = '77010000099'

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE test_phone_numbers (phone_key varchar(10) UNIQUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text);
    INSERT INTO departments (id, name, code) VALUES (1954, 'Удаленный КЦ', 'remote_cc'),
                                                    (1955, 'Другой отдел', 'other_cc');
    INSERT INTO users (id, name, login, role, department_id, sip_number) VALUES
        (520, 'Своев Сава', 'sava', 'operator', 1954, '902'),
        (521, 'Новичков Назар', 'nazar', 'operator', 1954, '903'),
        (522, 'Третьякова Тамара', 'tamara', 'operator', 1954, '904'),
        (523, 'Пустых Прохор', 'prokhor', 'operator', 1954, '905');
'''


class _Db:
    """_get_cursor на своей схеме: отдельное соединение, фиксация в конце блока."""

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


class _NoSapar:
    """Sapar не настроен: аналитике — «не подключён», подписи кладёт сам тест."""

    def configured(self):
        return False


class _Env:
    def __init__(self, db, svc, day):
        self.db, self.svc = db, svc
        # Сутки по Алматы: «сегодня» — настоящее (его берёт «Мой прогресс»), и вчера.
        self.day, self.prev = day, day - timedelta(days=1)

    def q(self, sql, params=None):
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    @staticmethod
    def at(day, hour, minute=0, second=0):
        return datetime.combine(day, time(hour, minute, second), tzinfo=TZ)

    def lead(self, name, phone, period=BASE, department=RCC):
        return str(self.q("INSERT INTO dial_list_leads (department_id, phone_norm, full_name, period) "
                          "VALUES (%s, %s, %s, %s) RETURNING id", (department, phone, name, period))[0][0])

    def call(self, lead_id, operator, at, disposition='ANSWER', billsec=0, auto_hangup='', cancelled=False):
        """Звонок оператора водителю: выдача, строка и попытка с исходом Binotel. Строка обработана;
        отменённая до ответа попытка ей не в счёт — строка осталась в работе."""
        if cancelled:
            state, result, done_at = 'issued', '', None
        else:
            talked = disposition == 'ANSWER' and billsec and not auto_hangup
            state, result, done_at = 'done', 'answered' if talked else 'no_answer', at
        with self.db._get_cursor() as cur:
            cur.execute("SELECT department_id FROM dial_list_leads WHERE id = %s", (lead_id,))
            department = cur.fetchone()[0]
            cur.execute("INSERT INTO dial_list_portions (operator_id, department_id, size, issued_at, closed_at) "
                        "VALUES (%s, %s, 1, %s, %s) RETURNING id", (operator, department, at, done_at))
            portion = cur.fetchone()[0]
            cur.execute("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state, "
                        "result, attempts, created_at, done_at) VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s) "
                        "RETURNING id", (portion, lead_id, operator, state, result, 0 if cancelled else 1, at,
                                         done_at))
            assignment = cur.fetchone()[0]
            cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, "
                        "disposition, billsec, auto_hangup, cancelled, finished_at) "
                        "VALUES (%s, %s, %s, 'finished', %s, %s, %s, %s, %s) RETURNING id",
                        (assignment, operator, at, disposition, billsec, auto_hangup, cancelled,
                         at + timedelta(seconds=billsec + 20)))
            return str(cur.fetchone()[0])

    def sign(self, lead_id, at):
        self.q("UPDATE dial_list_leads SET signed_at = %s WHERE id = %s", (at, lead_id))


@pytest.fixture
def env():
    schema = 't_dial_salary_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled, active_period) "
                    "VALUES (1954, TRUE, %s)", (BASE,))
    svc = dial_service.DialListService(db, sign_checker=_NoSapar())
    svc.operator_context = lambda uid: {
        "user_id": int(uid), "name": "Оператор", "department_id": RCC,
        "internal_number": {SAVA: '902', NAZAR: '903', TAMARA: '904', PROKHOR: '905'}[int(uid)],
        "settings": svc.department_settings(RCC)}
    try:
        yield _scenario(_Env(db, svc, datetime.now(TZ).date()))
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


def _scenario(env):
    """День отдела: сегодня (env.day) и вчера (env.prev), по Алматы.

    Своев Сава — сегодня диалогов 4, подписавших 2 (50 % → 75 тн × 4 = 300), вчера 3 и 2; в базе
    октября обзвонил 9, диалогов 6, подписали 3 (50 % → 75 тн × 6 = 450).
    Новичков Назар — сегодня 3 и 3 (100 % → 135 × 3 = 405); в базе обзвонил 4, диалогов 3,
    подписали 3.
    Третьякова Тамара — сегодня 4 и 2 (те же 300, что у Савы: одно место), вчера 1 и 0; в базе
    обзвонила 5, диалогов 5, подписали 2 (40 % → 60 × 5 = 300).
    Пустых Прохор — одна отменённая до ответа попытка: не звонил."""
    day, prev, at = env.day, env.prev, env.at
    n = iter(range(10, 99))

    def lead(**kwargs):
        index = next(n)
        return env.lead(f'Водитель {index}', f'770100000{index}', **kwargs)

    # ── Своев Сава ───────────────────────────────────────────────────────────
    l1 = lead()
    env.call(l1, SAVA, at(day, 10, 0), billsec=40)                       # диалог, подписал сегодня
    env.sign(l1, at(day, 13, 0))
    env.call(lead(), SAVA, at(day, 10, 5), billsec=5)                    # ответ короче 10 с — не диалог
    env.call(lead(), SAVA, at(day, 10, 10), billsec=15, auto_hangup='announcement')  # сообщение сети
    env.call(lead(), SAVA, at(day, 10, 15), 'NOANSWER', cancelled=True)  # отменил до ответа — не звонил
    env.call(lead(), SAVA, at(day, 10, 20), 'NOANSWER')                  # недозвон: звонил, «не поднял»
    l6 = lead()
    env.call(l6, SAVA, at(day, 10, 25), billsec=20)                      # два разговора — один диалог
    env.call(l6, SAVA, at(day, 10, 40), billsec=30)
    l7 = lead()
    env.call(l7, SAVA, at(prev, 15, 0), billsec=25)                      # вчера: диалог и подпись
    env.sign(l7, at(prev, 18, 0))
    l8 = lead()
    env.call(l8, SAVA, at(prev, 16, 0), billsec=12)                      # разговор вчера, подпись сегодня:
    env.sign(l8, at(day, 9, 0))                                          # успешка — вчерашнего дня
    l9 = lead(period=OLD_BASE)
    env.call(l9, SAVA, at(day, 11, 0), billsec=50)                       # база сентября: в рейтинге дня
    env.sign(l9, at(day, 14, 0))                                         # есть, в зарплате за октябрь — нет
    test = env.lead('Тестовый Тест', TEST_PHONE)
    test_attempt = env.call(test, SAVA, at(day, 11, 10), billsec=60)
    l11 = lead(department=OTHER_DEP)
    env.call(l11, SAVA, at(day, 11, 20), billsec=70)                     # чужой отдел — не наш рейтинг
    env.sign(l11, at(day, 15, 0))
    env.call(lead(), SAVA, at(day, 0, 0, 30), billsec=15)                # границы суток по Алматы
    env.call(lead(), SAVA, at(prev, 23, 59, 30), billsec=15)
    # ── Новичков Назар ───────────────────────────────────────────────────────
    for minute, sec, signed in ((0, 30, 15), (10, 45, 16)):
        l20 = lead()
        env.call(l20, NAZAR, at(day, 12, minute), billsec=sec)
        env.sign(l20, at(day, signed, 0))
    env.call(lead(), NAZAR, at(day, 12, 20), 'BUSY')
    # ── Третьякова Тамара ────────────────────────────────────────────────────
    for minute, sec in ((0, 20), (10, 25), (20, 30), (30, 35)):
        l30 = lead()
        env.call(l30, TAMARA, at(day, 13, minute), billsec=sec)
        if minute < 20:
            env.sign(l30, at(day, 18, minute))
    # Водитель говорил с обоими: вчера с Тамарой, сегодня с Назаром — успешка последнему.
    l40 = lead()
    env.call(l40, TAMARA, at(prev, 17, 0), billsec=20)
    env.call(l40, NAZAR, at(day, 12, 30), billsec=25)
    env.sign(l40, at(day, 17, 0))
    # ── Пустых Прохор ────────────────────────────────────────────────────────
    env.call(lead(), PROKHOR, at(day, 14, 0), 'NOANSWER', cancelled=True)

    # Подписавшие — кому успешка: настоящее правило (последний разговор ≥10 с до подписи).
    SignChecker(env.db).resolve_successes()
    # Номер, попавший в «Реестр тестовых номеров» уже после засчитанной успешки: его нет нигде.
    env.q("INSERT INTO test_phone_numbers (phone_key) VALUES (%s)", (TEST_PHONE[-10:],))
    env.q("UPDATE dial_list_leads SET signed_at = %s, success_attempt_id = %s, success_operator_id = %s, "
          "success_resolved_at = %s WHERE id = %s", (at(day, 12, 0), test_attempt, SAVA, at(day, 12, 0), test))
    env.l8 = l8
    return env


def _row(rating, name):
    return next(row for row in rating["operators"] if row["name"] == name)


# ── «Мой прогресс»: зарплата ─────────────────────────────────────────────────

def test_salary_counts_the_active_base_like_conversion_and_analytics(env):
    # Успешки засчитаны там, где ждали: вчерашний разговор и сегодняшняя подпись — вчера.
    credited = env.q("SELECT l.success_operator_id, (t.requested_at AT TIME ZONE 'Asia/Almaty')::date "
                     "FROM dial_list_leads l JOIN dial_list_attempts t ON t.id = l.success_attempt_id "
                     "WHERE l.id = %s", (env.l8,))
    assert credited == [(SAVA, env.prev)]

    progress = env.svc.operator_progress(SAVA)
    salary = progress["salary"]
    assert {key: salary[key] for key in ("currency", "called", "dialogs", "no_dialog", "successes", "percent",
                                         "price", "amount", "level", "next", "period", "period_label")} == {
        "currency": "тн", "called": 9, "dialogs": 6, "no_dialog": 3, "successes": 3, "percent": 50.0,
        "price": 75, "amount": 450, "level": 3,
        "next": {"from": 60, "price": 90, "need": 1, "amount": 540},
        "period": "2026-10-01", "period_label": "Октябрь 2026"}
    # Сегодня — по всем базам отдела (и сентябрьской), без тестового номера и чужого отдела.
    assert salary["today"] == {"dialogs": 4, "successes": 2}
    # Конверсия — из того же снимка, что и зарплата.
    assert progress["conversion"] == {"successes": 3, "leads_talked": 6, "percent": 50.0,
                                      "period": "2026-10-01", "period_label": "Октябрь 2026"}
    # Определения — ровно те, что у руководителя в «Аналитике» по операторам.
    analytics = {row["operator_id"]: row for row in env.svc.analytics(RCC)["operators"]}
    expected = {SAVA: (9, 6, 3), NAZAR: (4, 3, 3), TAMARA: (5, 5, 2)}
    for operator, (called, dialogs, successes) in expected.items():
        row = analytics[operator]
        assert (row["leads_called"], row["leads_talked"], row["successes"]) == (called, dialogs, successes)
        mine = env.svc.operator_progress(operator)
        assert (mine["salary"]["called"], mine["salary"]["dialogs"], mine["salary"]["successes"]) == \
            (called, dialogs, successes), operator
        assert mine["salary"]["percent"] == mine["conversion"]["percent"] == row["conversion"], operator
    nazar = env.svc.operator_progress(NAZAR)["salary"]
    assert (nazar["percent"], nazar["price"], nazar["amount"], nazar["next"], nazar["today"]) == \
        (100.0, 135, 405, None, {"dialogs": 3, "successes": 3})
    tamara = env.svc.operator_progress(TAMARA)["salary"]
    assert (tamara["percent"], tamara["price"], tamara["amount"], tamara["today"]) == \
        (40.0, 60, 300, {"dialogs": 4, "successes": 2})
    # Одна отменённая до ответа попытка — не звонил: ни обзвонённых, ни денег.
    prokhor = env.svc.operator_progress(PROKHOR)["salary"]
    assert (prokhor["called"], prokhor["dialogs"], prokhor["percent"], prokhor["amount"], prokhor["today"]) == \
        (0, 0, None, 0, {"dialogs": 0, "successes": 0})
    # Ни номеров, ни ФИО водителей.
    assert '7701000' not in repr(progress)
    assert 'Водитель' not in repr(progress)


# ── «Рейтинг» ────────────────────────────────────────────────────────────────

def test_rating_of_the_day_after_nine_pm(env):
    res = env.svc.operator_rating(SAVA, now=env.at(env.day, 21, 30))
    assert (res["visible"], res["day"], res["seconds_to_close"], res["total"], res["currency"]) == \
        (True, env.day.isoformat(), 12 * 3600 + 30 * 60, 3, "тн")
    assert res["opens_at"] == f"{env.day.isoformat()}T21:00:00+05:00"
    # Место — по заработку дня; Сава и Тамара с одинаковыми 300 тн, 2 и 4 — на одном месте.
    # Прохор (только отменённая попытка) не в рейтинге; суммы — только своя.
    assert res["operators"] == [
        {"place": 1, "name": "Новичков Назар", "me": False, "dialogs": 3, "successes": 3, "percent": 100.0},
        {"place": 2, "name": "Своев Сава", "me": True, "dialogs": 4, "successes": 2, "percent": 50.0,
         "price": 75, "amount": 300},
        {"place": 2, "name": "Третьякова Тамара", "me": False, "dialogs": 4, "successes": 2, "percent": 50.0},
    ]
    assert res["me"] == res["operators"][1]
    # Назар видит свою сумму и не видит Савину.
    nazar = env.svc.operator_rating(NAZAR, now=env.at(env.day, 23, 0))
    assert (nazar["me"]["price"], nazar["me"]["amount"]) == (135, 405)
    assert "amount" not in _row(nazar, "Своев Сава") and "price" not in _row(nazar, "Своев Сава")
    # Прохор в этот день не звонил: своей строки нет, чужих сумм тоже.
    prokhor = env.svc.operator_rating(PROKHOR, now=env.at(env.day, 22, 0))
    assert prokhor["me"] is None and prokhor["total"] == 3
    assert not any("amount" in row for row in prokhor["operators"])
    for reply in (res, nazar, prokhor):
        assert '7701000' not in repr(reply) and 'Водитель' not in repr(reply)


def test_rating_is_hidden_until_nine_pm(env):
    res = env.svc.operator_rating(SAVA, now=env.at(env.day, 15, 0))
    assert res == {"visible": False, "day": env.day.isoformat(), "day_label": pay.day_label(env.day),
                   "opens_at": f"{env.day.isoformat()}T21:00:00+05:00",
                   "closes_at": f"{(env.day + timedelta(days=1)).isoformat()}T10:00:00+05:00",
                   "currency": "тн", "seconds_to_open": 6 * 3600, "me": {"dialogs": 4, "successes": 2}}
    for name in ('Новичков', 'Третьякова', 'Пустых'):
        assert name not in repr(res)
    nazar = env.svc.operator_rating(NAZAR, now=env.at(env.day, 20, 59, 59))
    assert (nazar["visible"], nazar["seconds_to_open"], nazar["me"]) == (False, 1, {"dialogs": 3, "successes": 3})
    prokhor = env.svc.operator_rating(PROKHOR, now=env.at(env.day, 10, 0))
    assert (prokhor["visible"], prokhor["me"]) == (False, {"dialogs": 0, "successes": 0})


def test_yesterday_stays_open_until_ten_am(env):
    # 09:59 по Алматы (и тот же момент в UTC) — ещё итоги вчерашнего дня: Сава — 3 диалога, 2 подписали
    # (66,7 % → 90 тн × 3 = 270; разговор вчера и подпись сегодня — вчерашняя успешка), Тамара —
    # 1 диалог без подписи; Назар вчера не звонил.
    for now in (env.at(env.day, 9, 59), env.at(env.day, 9, 59).astimezone(timezone.utc)):
        res = env.svc.operator_rating(SAVA, now=now)
        assert (res["visible"], res["day"], res["seconds_to_close"], res["total"]) == \
            (True, env.prev.isoformat(), 60, 2)
        assert res["operators"] == [
            {"place": 1, "name": "Своев Сава", "me": True, "dialogs": 3, "successes": 2, "percent": 66.7,
             "price": 90, "amount": 270},
            {"place": 2, "name": "Третьякова Тамара", "me": False, "dialogs": 1, "successes": 0, "percent": 0.0},
        ]
    # В 10:00 вчерашний рейтинг закрыт — отсчёт до итогов сегодняшнего.
    closed = env.svc.operator_rating(SAVA, now=env.at(env.day, 10, 0))
    assert (closed["visible"], closed["day"], closed["seconds_to_open"]) == (False, env.day.isoformat(), 11 * 3600)
