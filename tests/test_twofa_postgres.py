# -*- coding: utf-8 -*-
"""«Коды 2FA» (#373) на настоящем Postgres: TWOFA_TEST_PORT — порт локального кластера.

Чистые правила (код TOTP, разбор QR, шифрование, права) проверяет tests/test_twofa.py.
Здесь — весь процесс на схеме раздела: запрос → решение главы → код в Telegram → сроки;
первое нажатие выигрывает; снятый доступ гасит и ждущий запрос, и «Код не подошёл»;
удалённый аккаунт уносит ключ; журнал не правится; цифр кода нет ни в базе, ни в ответах.

Telegram — подделка, которая записывает вызовы; часы — свои: тест сам двигает время.
Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным
именем, в конце она удаляется. Люди выдуманы.

    TWOFA_TEST_PORT=5432 python -m pytest tests/test_twofa_postgres.py
"""

import base64
import contextlib
import json
import os
import re
import uuid

import pytest

from twofa import access, crypto, queries, rules, schema, totp
from twofa.service import Twofa

PORT = os.environ.get('TWOFA_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (TWOFA_TEST_PORT)')

SUPER, HEAD, ANNA, BORIS, FIRED, OTHER = 1, 10, 20, 21, 22, 30
SUPER_TG, HEAD_TG, ANNA_TG, OTHER_TG = 9001, 9010, 9020, 9030
SECRET = b'12345678901234567890'
T0 = 1761553800.0 + 5  # 13:30:05 по Алматы: до смены кода 25 секунд

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text UNIQUE, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text NOT NULL,
                        status text NOT NULL DEFAULT 'working', department_id integer, telegram_id bigint UNIQUE);
    INSERT INTO departments (id, code, name, head_user_id) VALUES
        (1, 'marketing', 'Маркетинг', 10), (2, 'szov', 'СЗоВ', NULL);
    INSERT INTO users (id, name, login, role, status, department_id, telegram_id) VALUES
        (1, 'Супер Админов', 'super', 'super_admin', 'working', NULL, 9001),
        (10, 'Глава Маркетинга', 'head', 'admin', 'working', 1, 9010),
        (20, 'Анна Маркетолог', 'anna', 'marketing_manager', 'working', 1, 9020),
        (21, 'Борис Без Телеграма', 'boris', 'marketing_manager', 'working', 1, NULL),
        (22, 'Уволенный Глеб', 'gleb', 'marketing_manager', 'fired', 1, 9022),
        (30, 'Оператор Сзовов', 'op', 'operator', 'working', 2, 9030);
'''


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user=os.environ.get('TWOFA_TEST_USER', 'postgres'),
                                dbname='postgres', connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.name)
            cur.execute("SET statement_timeout TO '20s'")
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


class FakeTelegram:
    """Записывает вызовы Bot API (и их порядок) и выдаёт id сообщений по порядку."""

    def __init__(self):
        self.sent, self.edited, self.deleted, self.calls = [], [], [], []
        self.fail_chats = set()
        self.fail_edit = self.fail_delete = False
        self._next = 100

    def send(self, chat_id, text, markup=None):
        self.calls.append(('send', chat_id))
        if chat_id in self.fail_chats:
            return None, 'Forbidden: bot was blocked by the user'
        self._next += 1
        self.sent.append({'chat_id': chat_id, 'text': text, 'markup': markup, 'message_id': self._next})
        return self._next, None

    def edit(self, chat_id, message_id, text, markup=None):
        self.calls.append(('edit', chat_id))
        if self.fail_edit:
            return False
        self.edited.append({'chat_id': chat_id, 'message_id': message_id, 'text': text, 'markup': markup})
        return True

    def delete(self, chat_id, message_id):
        self.calls.append(('delete', chat_id))
        if self.fail_delete:
            return False
        self.deleted.append({'chat_id': chat_id, 'message_id': message_id})
        return True

    def username(self):
        return 'icore_test_bot'

    def to(self, chat_id):
        return [m for m in self.sent if m['chat_id'] == chat_id]

    def forget(self):
        for log in (self.sent, self.edited, self.deleted, self.calls):
            log.clear()


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(crypto.ENV_KEY, base64.b64encode(b'k' * 32).decode())


@pytest.fixture
def db(key):
    name = 't_twofa_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL)
            schema.init_twofa_schema(cur)
            # Повторный запуск — как каждый рестарт прода: идемпотентно.
            schema.init_twofa_schema(cur)
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA %s CASCADE' % name)
        admin.commit()
        admin.close()


@pytest.fixture
def env(db):
    tg = FakeTelegram()
    clock = Clock(T0)
    wakes = []
    service = Twofa(cursor_factory=db._get_cursor, tg=tg, clock=clock, wake=lambda: wakes.append(clock.now))
    head = service.context(HEAD)
    [account_id] = service.add_accounts(head, [{'title': 'Google Ads', 'service': 'Google', 'secret': SECRET}])['created']
    assert service.set_person_grants(head, ANNA, [account_id])['outcome'] == 'ok'
    # Уведомления по умолчанию выключены (тесты ниже — test_notifications_*); здесь глава их
    # включил, чтобы процесс шёл через его Telegram, как в жизни.
    assert service.set_notify(head, True) is True
    # Анне открыт доступ — бот прислал ей меню с кнопкой (тесты test_first_grant_*). Дальше
    # тесты считают сообщения сами, с чистого листа.
    [welcome] = tg.sent
    assert welcome['chat_id'] == ANNA_TG and rules.REQUEST_BUTTON in str(welcome['markup'])
    tg.forget()
    return {'db': db, 'tg': tg, 'clock': clock, 'service': service, 'account_id': account_id, 'wakes': wakes}


def _rows(db, sql, params=()):
    with db._get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _request(env, user=ANNA):
    service = env['service']
    return service.request_code(service.context(user), env['account_id'])


def _approve(env, request_id, via='portal'):
    service = env['service']
    return service.decide(service.context(HEAD), request_id, 'approve', via)


def _code_in(text):
    match = re.search(r'<code>(\d{3}) (\d{3})</code>', text)
    return match.group(1) + match.group(2) if match else None


# ─── Права на схеме ──────────────────────────────────────────────────────────

def test_context_knows_the_approver_and_grants(env):
    service = env['service']
    anna = service.context(ANNA)
    assert anna['approver_id'] == HEAD and anna['granted_account_ids'] == [env['account_id']]
    assert access.can_request(anna, env['account_id']) and not access.can_manage(anna)
    head = service.context(HEAD)
    assert access.can_approve(head) and access.can_manage(head)
    sup = service.context(SUPER)
    assert access.can_manage(sup) and access.can_approve(sup)
    assert not access.can_open_section(service.context(OTHER))


def test_fired_employee_has_no_access_even_with_a_grant(env):
    service = env['service']
    with env['db']._get_cursor() as cur:
        cur.execute("INSERT INTO twofa_grants (account_id, user_id, granted_at) VALUES (%s, %s, now())",
                    (env['account_id'], FIRED))
    gleb = service.context(FIRED)
    assert gleb['granted_account_ids'] == [env['account_id']]
    assert not access.can_open_section(gleb)
    assert service.request_code(gleb, env['account_id'])['outcome'] == 'forbidden'
    # И выдать ему заново нельзя.
    assert service.set_person_grants(service.context(HEAD), FIRED, [env['account_id']])['outcome'] == 'not_working'


def test_without_a_head_super_admins_still_approve_and_without_anyone_nobody_can(env):
    with env['db']._get_cursor() as cur:
        cur.execute("UPDATE users SET status = 'fired' WHERE id = %s", (HEAD,))
    assert not access.can_open_section(env['service'].context(HEAD))
    request = _request(env)['request']
    service = env['service']
    assert service.decide(service.context(SUPER), request['id'], 'approve', 'portal')['outcome'] == 'approved'
    with env['db']._get_cursor() as cur:
        cur.execute("UPDATE users SET status = 'fired' WHERE id = %s", (SUPER,))
    assert service.request_code(service.context(ANNA), env['account_id'])['outcome'] == 'no_approver'


def test_key_missing_closes_the_section_for_all_but_super_admin(env, monkeypatch):
    monkeypatch.delenv(crypto.ENV_KEY)
    service = env['service']
    assert not access.can_open_section(service.context(HEAD))
    assert not access.can_open_section(service.context(ANNA))
    assert access.can_manage(service.context(SUPER))


# ─── Запрос ──────────────────────────────────────────────────────────────────

def test_request_reaches_the_head_with_two_buttons(env):
    result = _request(env)
    assert result['outcome'] == 'created'
    request = result['request']
    assert request['status'] == 'pending'
    assert request['expires_at'] == rules.almaty(T0 + rules.REQUEST_TTL_SECONDS)
    [message] = env['tg'].to(HEAD_TG)
    assert 'Анна Маркетолог' in message['text'] and '«Google Ads»' in message['text']
    buttons = [b['callback_data'] for b in message['markup']['inline_keyboard'][0]]
    assert buttons == ['twofa:ok:%d' % request['id'], 'twofa:no:%d' % request['id']]
    assert _rows(env['db'], "SELECT kind, state FROM twofa_messages") == [('approval', 'shown')]
    assert env['wakes'], 'новый срок — будильник обязан проснуться'


def test_second_request_while_pending_does_not_bother_the_head_again(env):
    first = _request(env)['request']
    second = _request(env)
    assert second['outcome'] == 'pending' and second['request']['id'] == first['id']
    assert len(env['tg'].to(HEAD_TG)) == 1


def test_unique_index_holds_one_pending_per_pair(env):
    _request(env)
    with env['db']._get_cursor() as cur:
        again = queries.insert_request(cur, account_id=env['account_id'], requester_id=ANNA,
                                       now=rules.almaty(T0), expires_at=rules.almaty(T0 + 600))
    assert again is None


def test_without_telegram_the_request_is_refused_before_bothering_anyone(env):
    service = env['service']
    service.set_person_grants(service.context(HEAD), BORIS, [env['account_id']])
    assert service.request_code(service.context(BORIS), env['account_id'])['outcome'] == 'no_telegram'
    assert env['tg'].sent == []


def test_not_granted_account_is_refused(env):
    service = env['service']
    [other] = service.add_accounts(service.context(HEAD), [{'title': 'Meta', 'service': None,
                                                             'secret': b'abcdefghij0123456789'}])['created']
    assert service.request_code(service.context(ANNA), other)['outcome'] == 'forbidden'


# ─── Решение ─────────────────────────────────────────────────────────────────

def test_approval_sends_the_code_and_its_expiry_and_closes_the_head_message(env):
    request = _request(env)['request']
    result = _approve(env, request['id'])
    assert result['outcome'] == 'approved' and result['delivered'] is True
    [code_message] = env['tg'].to(ANNA_TG)
    assert _code_in(code_message['text']) == totp.code_at(SECRET, T0)
    assert 'Действует до <b>13:30:30</b>' in code_message['text']
    assert code_message['markup']['inline_keyboard'][0][0]['callback_data'] == 'twofa:new:%d' % request['id']
    [closed] = env['tg'].edited
    assert closed['chat_id'] == HEAD_TG and '✅ Подтверждено в 13:30' in closed['text']
    assert closed['markup'] is None
    rows = _rows(env['db'], "SELECT kind, state, hide_at, delete_at FROM twofa_messages ORDER BY id")
    assert rows[0][:2] == ('approval', 'closed')
    assert rows[1] == ('code', 'shown', rules.almaty(T0 + 60), rules.almaty(T0 + 180))


def test_code_with_less_than_ten_seconds_left_is_the_next_one(env):
    env['clock'].now = T0 + 20  # 13:30:25 — до смены 5 секунд
    request = _request(env)['request']
    _approve(env, request['id'])
    [code_message] = env['tg'].to(ANNA_TG)
    assert _code_in(code_message['text']) == totp.code_at(SECRET, T0 + 25)
    assert 'Действует до <b>13:31:00</b>' in code_message['text']
    assert 'Начнёт действовать в 13:30:30 — через 5 с' in code_message['text']


def test_first_press_wins_second_is_told_it_is_decided(env):
    request = _request(env)['request']
    assert _approve(env, request['id'], 'portal')['outcome'] == 'approved'
    again = env['service'].decide(env['service'].context(HEAD), request['id'], 'reject', 'telegram')
    assert again['outcome'] == 'already' and again['request']['status'] == 'approved'
    assert len(env['tg'].to(ANNA_TG)) == 1


def test_reject_tells_the_employee(env):
    request = _request(env)['request']
    result = env['service'].decide(env['service'].context(HEAD), request['id'], 'reject', 'portal')
    assert result['outcome'] == 'rejected'
    [notice] = env['tg'].to(ANNA_TG)
    assert 'отклонён' in notice['text'] and _code_in(notice['text']) is None
    assert '❌ Отклонено' in env['tg'].edited[0]['text']


def test_employees_cannot_decide(env):
    request = _request(env)['request']
    service = env['service']
    assert service.decide(service.context(ANNA), request['id'], 'approve', 'portal')['outcome'] == 'forbidden'
    assert service.decide(service.context(OTHER), request['id'], 'approve', 'portal')['outcome'] == 'forbidden'
    assert env['tg'].to(ANNA_TG) == []


# ─── Тумблер уведомлений (владелец, 27.10.2026) ──────────────────────────────

def test_notifications_are_off_by_default(env):
    service = env['service']
    service.set_notify(service.context(HEAD), False)
    request = _request(env)['request']
    assert env['tg'].sent == []
    with env['db']._get_cursor() as cur:
        assert queries.bell(cur, HEAD, rules.almaty(T0), 5) == (0, [])
        assert queries.bell(cur, SUPER, rules.almaty(T0), 5) == (0, [])
    # Подтверждать можно и без уведомлений — в разделе.
    assert service.decide(service.context(HEAD), request['id'], 'approve', 'portal')['outcome'] == 'approved'


def test_every_approver_with_the_toggle_gets_buttons_first_press_wins(env):
    service = env['service']
    assert service.set_notify(service.context(SUPER), True) is True
    request = _request(env)['request']
    assert {m['chat_id'] for m in env['tg'].sent} == {HEAD_TG, SUPER_TG}
    text, alert = service.handle_callback(SUPER_TG, 'twofa:ok:%d' % request['id'])
    assert text == 'Подтверждено — код отправлен' and not alert
    closed = {m['chat_id']: m['text'] for m in env['tg'].edited}
    assert set(closed) == {HEAD_TG, SUPER_TG}
    assert all('✅ Подтверждено в 13:30 — Супер Админов' in t for t in closed.values())
    text, _alert = service.handle_callback(HEAD_TG, 'twofa:no:%d' % request['id'])
    assert text.startswith('Уже решено: подтверждён')
    assert len([m for m in env['tg'].to(ANNA_TG) if _code_in(m['text'])]) == 1


def test_toggle_off_stops_notifications_and_the_bell(env):
    service = env['service']
    service.set_notify(service.context(HEAD), False)
    with env['db']._get_cursor() as cur:
        assert not queries.notify_enabled(cur, HEAD)
    _request(env)
    assert env['tg'].to(HEAD_TG) == []


def test_toggle_is_for_approvers_only_and_bell_needs_the_approver_role(env):
    with env['db']._get_cursor() as cur:
        queries.set_notify(cur, ANNA, True, rules.almaty(T0))
    _request(env)
    with env['db']._get_cursor() as cur:
        assert queries.bell(cur, ANNA, rules.almaty(T0), 5) == (0, [])
        assert queries.bell(cur, HEAD, rules.almaty(T0), 5)[0] == 1


def test_blocked_bot_is_recorded_as_not_delivered(env):
    env['tg'].fail_chats.add(ANNA_TG)
    request = _request(env)['request']
    result = _approve(env, request['id'])
    assert result['outcome'] == 'approved' and result['delivered'] is False
    assert 'blocked' in result['request']['delivery_error']
    assert env['service'].delivery_reason(result['request']['delivery_error']) == 'сотрудник остановил бота портала'
    kinds = [row[0] for row in _rows(env['db'], "SELECT kind FROM twofa_events ORDER BY id")]
    assert kinds[-2:] == ['approved', 'code_failed']


# ─── Сроки ───────────────────────────────────────────────────────────────────

def test_request_burns_after_ten_minutes_and_the_employee_is_told(env):
    request = _request(env)['request']
    env['clock'].now = T0 + 600
    assert env['service'].run_due() is None
    assert _rows(env['db'], "SELECT status FROM twofa_requests") == [('expired',)]
    assert '⏱ Сгорел в 13:40' in env['tg'].edited[-1]['text']
    [notice] = env['tg'].to(ANNA_TG)
    assert 'сгорел' in notice['text'] and 'заново' in notice['text']
    assert _approve(env, request['id'])['outcome'] == 'already'


def test_late_press_on_an_overdue_request_does_not_issue_a_code(env):
    request = _request(env)['request']
    env['clock'].now = T0 + 601  # будильник ещё не дошёл
    result = _approve(env, request['id'])
    assert result['outcome'] == 'expired'
    assert all(_code_in(m['text']) is None for m in env['tg'].to(ANNA_TG))


def test_new_request_after_burn_is_possible_even_before_the_alarm(env):
    first = _request(env)['request']
    env['clock'].now = T0 + 700
    second = _request(env)
    assert second['outcome'] == 'created' and second['request']['id'] != first['id']


def test_digits_are_hidden_after_a_minute_and_message_removed_after_three(env):
    request = _request(env)['request']
    _approve(env, request['id'])
    code_message = env['tg'].to(ANNA_TG)[0]
    env['clock'].now = T0 + 60
    next_at = env['service'].run_due()
    hidden = env['tg'].edited[-1]
    assert hidden['message_id'] == code_message['message_id']
    assert _code_in(hidden['text']) is None and 'стёрт' in hidden['text']
    assert hidden['markup']['inline_keyboard'][0][0]['text'] == 'Код не подошёл'
    assert next_at == pytest.approx(T0 + 180)
    env['clock'].now = T0 + 180
    env['service'].run_due()
    assert env['tg'].deleted[-1]['message_id'] == code_message['message_id']
    assert _rows(env['db'], "SELECT state FROM twofa_messages WHERE kind = 'code'") == [('deleted',)]


def test_overlapping_instances_do_not_act_twice(env):
    """При выкладке два экземпляра живут одновременно: второй будильник не правит то же."""
    request = _request(env)['request']
    _approve(env, request['id'])
    env['clock'].now = T0 + 60
    twin = Twofa(cursor_factory=env['db']._get_cursor, tg=env['tg'], clock=env['clock'])
    env['service'].run_due()
    edits = len(env['tg'].edited)
    twin.run_due()
    assert len(env['tg'].edited) == edits


# ─── «Код не подошёл» ────────────────────────────────────────────────────────

def test_resend_within_three_minutes_replaces_the_message(env):
    request = _request(env)['request']
    _approve(env, request['id'])
    first = env['tg'].to(ANNA_TG)[0]
    env['clock'].now = T0 + 30  # следующий шаг — другие цифры
    result = env['service'].resend(ANNA, request['id'])
    assert result['outcome'] == 'resent'
    assert env['tg'].deleted[-1]['message_id'] == first['message_id']
    second = env['tg'].to(ANNA_TG)[-1]
    assert _code_in(second['text']) == totp.code_at(SECRET, T0 + 30)
    kinds = [row[0] for row in _rows(env['db'], "SELECT kind FROM twofa_events ORDER BY id")]
    assert kinds[-1] == 'code_resent'


def test_resend_in_the_same_step_says_the_code_is_the_same(env):
    request = _request(env)['request']
    _approve(env, request['id'])
    env['clock'].now = T0 + 3
    result = env['service'].resend(ANNA, request['id'])
    assert result['outcome'] == 'same_code' and result['seconds_left'] == 22


def test_code_reaches_the_employee_before_the_head_message_is_marked(env):
    """Код — первым: медленная правка сообщения главы не съедает 10 секунд запаса (п. 5)."""
    request = _request(env)['request']
    env['tg'].calls.clear()
    _approve(env, request['id'])
    assert env['tg'].calls[:2] == [('send', ANNA_TG), ('edit', HEAD_TG)]


def test_code_is_counted_at_send_time_not_at_decision(env):
    request = _request(env)['request']
    clock = env['clock']
    original_send = env['tg'].send

    def late_send(chat_id, text, markup=None):
        clock.now += 15  # отправка ушла на 15 секунд позже решения
        return original_send(chat_id, text, markup)

    env['tg'].send = late_send
    clock.now = T0  # решение в 13:30:05, отправка в 13:30:20 — до смены 10 с
    _approve(env, request['id'])
    [code_message] = env['tg'].to(ANNA_TG)
    assert _code_in(code_message['text']) == totp.code_at(SECRET, T0 + 15)


def test_digits_window_closed_means_one_delete_not_hide_then_delete(env):
    request = _request(env)['request']
    _approve(env, request['id'])
    code_message = env['tg'].to(ANNA_TG)[0]
    # Будильник проспал оба срока (выкладка): окно «Код не подошёл» уже закрыто.
    env['clock'].now = T0 + 200
    env['tg'].calls.clear()
    env['service'].run_due()
    assert env['tg'].calls == [('delete', ANNA_TG)]
    assert env['tg'].deleted[-1]['message_id'] == code_message['message_id']
    assert _rows(env['db'], "SELECT state FROM twofa_messages WHERE kind = 'code'") == [('deleted',)]


def test_failed_hide_and_delete_are_retried_not_forgotten(env):
    request = _request(env)['request']
    _approve(env, request['id'])
    env['tg'].fail_edit = True
    env['clock'].now = T0 + 60
    next_at = env['service'].run_due()
    assert _rows(env['db'], "SELECT state, attempts FROM twofa_messages WHERE kind = 'code'") == [('shown', 1)]
    assert next_at == pytest.approx(T0 + 90)
    env['tg'].fail_edit = False
    env['clock'].now = T0 + 90
    env['service'].run_due()
    assert 'стёрт' in env['tg'].edited[-1]['text']
    env['tg'].fail_delete = True
    env['clock'].now = T0 + 180
    env['service'].run_due()
    assert _rows(env['db'], "SELECT state, attempts FROM twofa_messages WHERE kind = 'code'") == [('hidden', 2)]
    env['tg'].fail_delete = False
    env['clock'].now = T0 + 210
    env['service'].run_due()
    assert _rows(env['db'], "SELECT state FROM twofa_messages WHERE kind = 'code'") == [('deleted',)]


def test_retries_stop_after_the_limit(env):
    """Telegram не принимает ни правку, ни удаление (бота остановили навсегда): повторы
    кончаются на MAX_MESSAGE_ATTEMPTS, а не идут каждые 30 секунд вечно."""
    request = _request(env)['request']
    _approve(env, request['id'])
    env['tg'].fail_edit = env['tg'].fail_delete = True
    for step in range(20):
        env['clock'].now = T0 + 60 + step * 30
        env['service'].run_due()
    assert _rows(env['db'], "SELECT state, attempts FROM twofa_messages WHERE kind = 'code'") == [
        ('deleted', queries.MAX_MESSAGE_ATTEMPTS)]
    tries = [call for call in env['tg'].calls if call[0] in ('edit', 'delete') and call[1] == ANNA_TG]
    assert len(tries) == queries.MAX_MESSAGE_ATTEMPTS + 1
    assert env['service'].run_due() is None


def test_resend_of_a_next_code_says_when_it_starts(env):
    env['clock'].now = T0 + 20  # 13:30:25 — до смены 5 с, выдан код следующего шага
    request = _request(env)['request']
    _approve(env, request['id'])
    env['clock'].now = T0 + 23.2  # больше 3 с после выдачи — не двойное нажатие
    text, alert = env['service'].handle_callback(ANNA_TG, 'twofa:new:%d' % request['id'])
    assert alert and text.startswith('Код тот же — он начнёт действовать через 2 с')


def test_resend_sends_the_new_code_before_removing_the_old(env):
    request = _request(env)['request']
    _approve(env, request['id'])
    env['clock'].now = T0 + 30
    env['tg'].calls.clear()
    env['service'].resend(ANNA, request['id'])
    assert env['tg'].calls == [('send', ANNA_TG), ('delete', ANNA_TG)]


def test_failed_resend_keeps_the_old_message_and_allows_another_press(env):
    request = _request(env)['request']
    _approve(env, request['id'])
    first = env['tg'].to(ANNA_TG)[0]
    env['clock'].now = T0 + 30
    env['tg'].fail_chats.add(ANNA_TG)
    assert env['service'].resend(ANNA, request['id'])['outcome'] == 'failed'
    assert env['tg'].deleted == []
    assert _rows(env['db'], "SELECT state FROM twofa_messages WHERE kind = 'code'") == [('shown',)]
    env['tg'].fail_chats.clear()
    env['clock'].now = T0 + 31
    assert env['service'].resend(ANNA, request['id'])['outcome'] == 'resent'
    assert env['tg'].deleted[-1]['message_id'] == first['message_id']


def test_double_press_of_resend_sends_one_code(env):
    request = _request(env)['request']
    _approve(env, request['id'])
    env['clock'].now = T0 + 30
    assert env['service'].resend(ANNA, request['id'])['outcome'] == 'resent'
    env['clock'].now = T0 + 31
    assert env['service'].resend(ANNA, request['id'])['outcome'] == 'just_sent'
    assert len(env['tg'].to(ANNA_TG)) == 2  # код при подтверждении + один повтор
    env['clock'].now = T0 + 35
    assert env['service'].resend(ANNA, request['id'])['outcome'] in ('resent', 'same_code')


def test_resend_after_the_window_needs_a_new_request(env):
    request = _request(env)['request']
    _approve(env, request['id'])
    env['clock'].now = T0 + 180
    assert env['service'].resend(ANNA, request['id'])['outcome'] == 'window_closed'


def test_resend_is_only_for_the_requester(env):
    request = _request(env)['request']
    _approve(env, request['id'])
    assert env['service'].resend(OTHER, request['id'])['outcome'] == 'forbidden'


# ─── Снятый доступ и удалённый аккаунт ───────────────────────────────────────

def test_revoking_access_cancels_the_pending_request(env):
    request = _request(env)['request']
    service = env['service']
    result = service.set_person_grants(service.context(HEAD), ANNA, [])
    assert result['removed'] == [env['account_id']]
    assert _rows(env['db'], "SELECT status FROM twofa_requests") == [('cancelled',)]
    assert 'Отменён' in env['tg'].edited[-1]['text']
    assert _approve(env, request['id'])['outcome'] == 'already'
    assert env['tg'].to(ANNA_TG) == []


def test_revoking_after_approval_stops_resend(env):
    request = _request(env)['request']
    _approve(env, request['id'])
    service = env['service']
    service.set_person_grants(service.context(HEAD), ANNA, [])
    env['clock'].now = T0 + 30
    assert service.resend(ANNA, request['id'])['outcome'] == 'forbidden'


def test_grant_removed_while_pending_by_other_path_still_blocks_the_code(env):
    """Доступ сняли мимо сервиса (руками в базе) — подтверждение всё равно кода не даёт."""
    request = _request(env)['request']
    with env['db']._get_cursor() as cur:
        cur.execute("DELETE FROM twofa_grants")
    result = _approve(env, request['id'])
    assert result['outcome'] == 'cancelled'
    assert env['tg'].to(ANNA_TG) == []


def test_ciphertext_moved_to_another_account_does_not_decrypt(env):
    """Шифротекст привязан к своей строке: перенос в базе в чужой аккаунт кода не даёт."""
    service = env['service']
    head = service.context(HEAD)
    [other] = service.add_accounts(head, [{'title': 'Meta', 'service': None,
                                           'secret': b'abcdefghij0123456789'}])['created']
    service.set_person_grants(head, ANNA, [env['account_id'], other])
    with env['db']._get_cursor() as cur:
        cur.execute("UPDATE twofa_accounts SET secret_enc = (SELECT secret_enc FROM twofa_accounts WHERE id = %s) "
                    "WHERE id = %s", (env['account_id'], other))
    request = service.request_code(service.context(ANNA), other)['request']
    result = service.decide(service.context(HEAD), request['id'], 'approve', 'portal')
    assert result['outcome'] == 'key_error'
    assert env['tg'].to(ANNA_TG) == []


def test_same_key_cannot_be_added_twice(env):
    service = env['service']
    result = service.add_accounts(service.context(HEAD), [{'title': 'Копия', 'service': None, 'secret': SECRET}])
    assert result == {'outcome': 'duplicate', 'index': 0, 'title': 'Google Ads'}
    twin = service.add_accounts(service.context(HEAD), [
        {'title': 'A', 'service': None, 'secret': b'0123456789abcdefghij'},
        {'title': 'B', 'service': None, 'secret': b'0123456789abcdefghij'}])
    assert twin['outcome'] == 'duplicate' and twin['index'] == 1
    assert _rows(env['db'], "SELECT count(*) FROM twofa_accounts") == [(1,)]


def test_key_missing_gives_the_head_the_real_reason(env, monkeypatch):
    request = _request(env)['request']
    monkeypatch.setenv(crypto.ENV_KEY, base64.b64encode(b'short').decode())
    text, alert = env['service'].handle_callback(HEAD_TG, 'twofa:ok:%d' % request['id'])
    assert alert and 'Ключ шифрования' in text
    with env['db']._get_cursor() as cur:
        assert queries.bell(cur, HEAD, rules.almaty(T0), 5) == (0, [])


def test_removing_an_account_destroys_its_key(env):
    request = _request(env)['request']
    service = env['service']
    assert service.remove_account(service.context(HEAD), env['account_id'])
    assert _rows(env['db'], "SELECT is_active, secret_enc FROM twofa_accounts") == [(False, None)]
    assert _rows(env['db'], "SELECT count(*) FROM twofa_grants") == [(0,)]
    assert _rows(env['db'], "SELECT status FROM twofa_requests WHERE id = %s", (request['id'],)) == [('cancelled',)]
    assert not access.can_open_section(service.context(ANNA))


# ─── Журнал ──────────────────────────────────────────────────────────────────

def test_journal_is_append_only(env):
    import psycopg2
    _request(env)
    for sql in ("UPDATE twofa_events SET kind = 'x'", "DELETE FROM twofa_events"):
        with pytest.raises(psycopg2.Error):
            with env['db']._get_cursor() as cur:
                cur.execute(sql)


def test_no_digits_and_no_key_anywhere_in_the_database(env):
    request = _request(env)['request']
    _approve(env, request['id'])
    env['clock'].now = T0 + 30
    env['service'].resend(ANNA, request['id'])
    codes = {_code_in(m['text']) for m in env['tg'].sent if _code_in(m['text'])}
    assert len(codes) == 2
    dump = json.dumps(_rows(env['db'], "SELECT row_to_json(e)::text FROM twofa_events e")
                      + _rows(env['db'], "SELECT row_to_json(r)::text FROM twofa_requests r")
                      + _rows(env['db'], "SELECT row_to_json(m)::text FROM twofa_messages m"), ensure_ascii=False)
    for code in codes:
        assert code not in dump
    secret_b32 = base64.b32encode(SECRET).decode().rstrip('=')
    full = json.dumps(_rows(env['db'], "SELECT row_to_json(a)::text FROM twofa_accounts a"))
    assert secret_b32 not in full and SECRET.decode() not in full


def test_journal_tells_the_whole_story(env):
    request = _request(env)['request']
    _approve(env, request['id'], 'telegram')
    with env['db']._get_cursor() as cur:
        items, has_more = queries.journal(cur)
    kinds = [item['kind'] for item in reversed(items)]
    assert kinds == ['account_added', 'access_granted', 'requested', 'approved', 'code_sent']
    approved = next(i for i in items if i['kind'] == 'approved')
    assert approved['actor_name'] == 'Глава Маркетинга' and approved['subject_name'] == 'Анна Маркетолог'
    assert approved['details'] == {'via': 'telegram'}
    assert not has_more


# ─── Кнопки в Telegram ───────────────────────────────────────────────────────

def test_head_approves_by_telegram_button(env):
    request = _request(env)['request']
    text, alert = env['service'].handle_callback(HEAD_TG, 'twofa:ok:%d' % request['id'])
    assert text == 'Подтверждено — код отправлен' and not alert
    assert _code_in(env['tg'].to(ANNA_TG)[0]['text'])
    text, _alert = env['service'].handle_callback(HEAD_TG, 'twofa:no:%d' % request['id'])
    assert text.startswith('Уже решено: подтверждён')


def test_forwarded_button_pressed_by_someone_else_does_nothing(env):
    request = _request(env)['request']
    text, alert = env['service'].handle_callback(OTHER_TG, 'twofa:ok:%d' % request['id'])
    assert alert and 'глава маркетинга' in text
    text, alert = env['service'].handle_callback(777, 'twofa:ok:%d' % request['id'])
    assert alert and 'не привязан' in text
    assert _rows(env['db'], "SELECT status FROM twofa_requests") == [('pending',)]


def test_resend_button_by_the_employee(env):
    request = _request(env)['request']
    env['service'].handle_callback(HEAD_TG, 'twofa:ok:%d' % request['id'])
    env['clock'].now = T0 + 30
    text, alert = env['service'].handle_callback(ANNA_TG, 'twofa:new:%d' % request['id'])
    assert text == 'Новый код отправлен' and not alert
    text, alert = env['service'].handle_callback(HEAD_TG, 'twofa:new:%d' % request['id'])
    assert alert


def test_garbage_callback_is_refused(env):
    assert env['service'].handle_callback(HEAD_TG, 'twofa:ok:abc') == ('Неверная кнопка', True)


# ─── Колокол ─────────────────────────────────────────────────────────────────

def test_bell_shows_pending_to_approvers_who_turned_it_on(env):
    _request(env)
    with env['db']._get_cursor() as cur:
        assert queries.bell(cur, HEAD, rules.almaty(T0), 5)[0] == 1
        assert queries.bell(cur, SUPER, rules.almaty(T0), 5) == (0, [])
        assert queries.bell(cur, HEAD, rules.almaty(T0 + 600), 5)[0] == 0
    env['service'].set_notify(env['service'].context(SUPER), True)
    with env['db']._get_cursor() as cur:
        assert queries.bell(cur, SUPER, rules.almaty(T0), 5)[0] == 1


# ─── Ручки ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client(env):
    from flask import Flask, g

    from twofa.routes import build_twofa_blueprint

    app = Flask(__name__)
    current = {'user': ANNA}

    def require_api_key(fn):
        return fn

    app.register_blueprint(build_twofa_blueprint(
        db=env['db'], require_api_key=require_api_key,
        build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (current['user'], None, None),
        service=env['service'],
    ))
    test_client = app.test_client()
    test_client.as_user = lambda user_id: current.update(user=user_id)
    return test_client


def test_screen_per_role(client, env):
    client.as_user(ANNA)
    screen = client.get('/api/twofa').get_json()
    assert screen['capabilities'] == {'can_open': True, 'can_manage': False, 'can_approve': False, 'can_request': True}
    assert [a['title'] for a in screen['accounts']] == ['Google Ads'] and 'user_ids' not in screen['accounts'][0]
    assert screen['people'] == [] and screen['pending'] == []
    assert screen['notify'] is None and screen['approvers_available'] is True
    assert screen['me']['telegram_linked'] is True and screen['approver']['name'] == 'Глава Маркетинга'
    client.as_user(HEAD)
    screen = client.get('/api/twofa').get_json()
    assert screen['capabilities']['can_approve'] and screen['accounts'][0]['user_ids'] == [ANNA]
    assert screen['notify'] == {'enabled': True}
    client.as_user(SUPER)
    assert client.get('/api/twofa').get_json()['notify'] == {'enabled': False}
    client.as_user(HEAD)
    assert [p['name'] for p in screen['people']] == ['Анна Маркетолог']
    client.as_user(OTHER)
    response = client.get('/api/twofa')
    assert response.status_code == 403 and response.get_json()['code'] == 'TWOFA_SECTION_CLOSED'


def test_request_and_approve_through_the_routes(client, env):
    client.as_user(ANNA)
    response = client.post('/api/twofa/requests', json={'account_id': env['account_id']})
    assert response.status_code == 201
    request_id = response.get_json()['request']['id']
    again = client.post('/api/twofa/requests', json={'account_id': env['account_id']})
    assert again.status_code == 409 and again.get_json()['code'] == 'TWOFA_PENDING'
    assert client.post('/api/twofa/requests/%d/approve' % request_id).status_code == 403
    client.as_user(HEAD)
    screen = client.get('/api/twofa').get_json()
    assert [r['id'] for r in screen['pending']] == [request_id]
    response = client.post('/api/twofa/requests/%d/approve' % request_id)
    body = response.get_json()
    assert response.status_code == 200 and body['delivered'] is True and body['request']['status'] == 'approved'
    code = _code_in(env['tg'].to(ANNA_TG)[0]['text'])
    for path in ('/api/twofa', '/api/twofa/journal'):
        assert code not in response.get_data(as_text=True) and code not in client.get(path).get_data(as_text=True)
    second = client.post('/api/twofa/requests/%d/reject' % request_id)
    assert second.status_code == 409 and second.get_json()['code'] == 'TWOFA_ALREADY'


def test_import_from_a_qr_through_the_routes(client, env):
    client.as_user(HEAD)
    secret_b32 = base64.b32encode(b'abcdefghij0123456789').decode()
    text = 'otpauth://totp/Meta:ads@company.kz?secret=%s&issuer=Meta' % secret_b32
    preview = client.post('/api/twofa/accounts/preview', json={'text': text}).get_json()
    assert preview['entries'] == [{'name': 'ads@company.kz', 'issuer': 'Meta', 'supported': True, 'reason': None,
                                   'index': 0, 'title': 'Meta · ads@company.kz', 'duplicate_of': None}]
    response = client.post('/api/twofa/accounts', json={'text': text, 'items': [{'index': 0, 'title': 'Meta Ads', 'service': 'Meta'}]})
    assert response.status_code == 201
    # Тот же QR второй раз: в предпросмотре — «уже добавлен», сохранить — нельзя.
    again = client.post('/api/twofa/accounts/preview', json={'text': text}).get_json()
    assert again['entries'][0]['duplicate_of'] == 'Meta Ads'
    twice = client.post('/api/twofa/accounts', json={'text': text, 'items': [{'index': 0, 'title': 'Ещё раз'}]})
    assert twice.status_code == 409 and twice.get_json()['code'] == 'TWOFA_DUPLICATE' and twice.get_json()['index'] == 0
    titles = [a['title'] for a in client.get('/api/twofa').get_json()['accounts']]
    assert titles == ['Google Ads', 'Meta Ads']
    bad = client.post('/api/twofa/accounts', json={'text': 'https://example.com', 'items': [{'index': 0, 'title': 'x'}]})
    assert bad.status_code == 400 and bad.get_json()['code'] == 'TWOFA_PARSE'
    out_of_range = client.post('/api/twofa/accounts', json={'text': text, 'items': [{'index': 3, 'title': 'x'}]})
    assert out_of_range.status_code == 400
    client.as_user(ANNA)
    assert client.post('/api/twofa/accounts/preview', json={'text': text}).status_code == 403


def test_grants_and_people_through_the_routes(client, env):
    client.as_user(HEAD)
    people = client.get('/api/twofa/people?q=борис').get_json()['people']
    assert [(p['id'], p['telegram_linked']) for p in people] == [(BORIS, False)]
    assert all(p['id'] != FIRED for p in client.get('/api/twofa/people').get_json()['people'])
    response = client.put('/api/twofa/people/%d/grants' % BORIS, json={'account_ids': [env['account_id']]})
    assert response.get_json() == {'added': [env['account_id']], 'removed': []}
    assert client.put('/api/twofa/people/%d/grants' % BORIS, json={'account_ids': [999]}).status_code == 400
    assert client.put('/api/twofa/people/%d/grants' % BORIS, json={'account_ids': 'x'}).status_code == 400


def test_toggle_route(client, env):
    client.as_user(ANNA)
    assert client.put('/api/twofa/notify', json={'enabled': True}).status_code == 403
    client.as_user(SUPER)
    assert client.put('/api/twofa/notify', json={'enabled': 'да'}).status_code == 400
    assert client.put('/api/twofa/notify', json={'enabled': True}).get_json() == {'enabled': True}
    assert client.get('/api/twofa').get_json()['notify'] == {'enabled': True}
    assert client.put('/api/twofa/notify', json={'enabled': False}).get_json() == {'enabled': False}


def test_saving_accounts_without_the_key_is_refused(client, env, monkeypatch):
    monkeypatch.delenv(crypto.ENV_KEY)
    client.as_user(SUPER)
    secret_b32 = base64.b32encode(b'abcdefghij0123456789').decode()
    response = client.post('/api/twofa/accounts', json={'text': secret_b32, 'items': [{'index': 0, 'title': 'X'}]})
    assert response.status_code == 409 and response.get_json()['code'] == 'TWOFA_KEY_MISSING'


# ─── Запрос из бота: «Запросить код 🔐» → выбор аккаунта → запрос ────────────

SECRET_2 = b'abcdefghijabcdefghij'
OPERATOR_MENU = {'keyboard': [[{'text': 'Моя статистика📊'}]], 'resize_keyboard': True}


def _offer(env, tg_id=ANNA_TG):
    """Нажать «Запросить код 🔐»: (outcome, что бот прислал в ответ)."""
    outcome = env['service'].offer_accounts(tg_id)['outcome']
    return outcome, env['tg'].to(tg_id)[-1]


def _choose(env, offer, account_id, tg_id=ANNA_TG):
    return env['service'].handle_callback(tg_id, 'twofa:req:%d' % account_id,
                                          (offer['chat_id'], offer['message_id']))


def _second_account(env, grant_to_anna=True):
    service = env['service']
    head = service.context(HEAD)
    [account_id] = service.add_accounts(
        head, [{'title': 'ads@company.kz', 'service': 'Meta', 'secret': SECRET_2}])['created']
    if grant_to_anna:
        service.set_person_grants(head, ANNA, [env['account_id'], account_id])
    return account_id


def _with_menu(env, menu):
    return Twofa(cursor_factory=env['db']._get_cursor, tg=env['tg'], clock=env['clock'], menu=menu)


def test_request_button_offers_only_the_granted_accounts(env):
    meta = _second_account(env, grant_to_anna=False)
    outcome, offer = _offer(env)
    assert outcome == 'shown'
    assert offer['text'] == rules.choose_text()
    assert offer['markup']['inline_keyboard'] == [
        [{'text': 'Google Ads', 'callback_data': 'twofa:req:%d' % env['account_id']}],
        [{'text': 'Отмена', 'callback_data': 'twofa:x:0'}],
    ]
    assert 'twofa:req:%d' % meta not in str(offer['markup'])


def test_choosing_an_account_sends_the_request_and_turns_the_choice_into_a_receipt(env):
    _outcome, offer = _offer(env)
    assert _choose(env, offer, env['account_id']) == ('Запрос отправлен', False)
    [(request_id, status)] = _rows(env['db'], "SELECT id, status FROM twofa_requests")
    assert status == 'pending'
    # Дальше — тот же путь, что у кнопки портала: подтверждающему с уведомлениями — кнопки.
    [approval] = env['tg'].to(HEAD_TG)
    assert approval['markup'] == rules.approval_markup(request_id)
    # Выбор стал отметкой «отправлен» и потерял кнопки — второй раз по нему не нажать.
    [receipt] = env['tg'].edited
    assert (receipt['chat_id'], receipt['message_id']) == (ANNA_TG, offer['message_id'])
    assert 'отправлен в 13:30' in receipt['text'] and 'сгорит в 13:40' in receipt['text']
    assert receipt['markup'] is None
    # В журнале видно, откуда пришёл запрос.
    assert _rows(env['db'], "SELECT details FROM twofa_events WHERE kind = 'requested'") == [({'via': 'telegram'},)]
    # Подтвердили — код пришёл в тот же чат.
    env['service'].handle_callback(HEAD_TG, 'twofa:ok:%d' % request_id)
    assert _code_in(env['tg'].to(ANNA_TG)[-1]['text'])


def test_portal_request_is_marked_in_the_journal_too(env):
    _request(env)
    assert _rows(env['db'], "SELECT details FROM twofa_events WHERE kind = 'requested'") == [({'via': 'portal'},)]


def test_second_choice_while_pending_makes_no_second_request(env):
    _outcome, offer = _offer(env)
    _choose(env, offer, env['account_id'])
    _outcome, again = _offer(env)
    assert _choose(env, again, env['account_id']) == ('Запрос уже ждёт подтверждения — сгорит в 13:40', False)
    assert len(_rows(env['db'], "SELECT id FROM twofa_requests")) == 1
    assert len(env['tg'].to(HEAD_TG)) == 1


def test_choice_left_in_the_chat_follows_revoked_access(env):
    meta = _second_account(env)
    _outcome, offer = _offer(env)
    head = env['service'].context(HEAD)
    env['service'].set_person_grants(head, ANNA, [meta])  # Google Ads сняли, пока выбор висел
    assert _choose(env, offer, env['account_id']) == ('Этот аккаунт вам больше не выдан', True)
    assert env['tg'].edited[-1]['markup'] == rules.choose_markup(
        [{'id': meta, 'title': 'ads@company.kz', 'service': 'Meta'}])
    env['service'].set_person_grants(head, ANNA, [])
    assert _choose(env, offer, meta) == ('Запрос кодов вам больше не открыт', True)
    assert env['tg'].edited[-1]['text'] == rules.request_closed_text()
    assert env['tg'].edited[-1]['markup'] is None
    assert _rows(env['db'], "SELECT id FROM twofa_requests") == []


def test_button_without_access_answers_with_the_menu_that_has_no_button(env):
    asked = []

    def menu(user_id):
        asked.append(user_id)
        return OPERATOR_MENU

    assert _with_menu(env, menu).offer_accounts(OTHER_TG) == {'outcome': 'closed'}
    [reply] = env['tg'].to(OTHER_TG)
    assert reply['text'] == rules.request_closed_text() and reply['markup'] == OPERATOR_MENU
    assert asked == [OTHER]
    # У роли нет своего меню — кнопку убрать целиком.
    assert env['service'].offer_accounts(OTHER_TG) == {'outcome': 'closed'}
    assert env['tg'].to(OTHER_TG)[-1]['markup'] == {'remove_keyboard': True}


def test_button_from_an_unknown_telegram_points_at_the_login(env):
    assert env['service'].offer_accounts(777) == {'outcome': 'no_user'}
    assert env['tg'].to(777)[-1]['text'] == rules.no_user_text()


def test_button_when_nobody_can_approve_says_so_before_the_choice(env):
    with env['db']._get_cursor() as cur:
        cur.execute("UPDATE departments SET head_user_id = NULL WHERE code = 'marketing'")
        cur.execute("UPDATE users SET status = 'fired' WHERE id = %s", (SUPER,))
    assert env['service'].offer_accounts(ANNA_TG) == {'outcome': 'no_approver'}
    [reply] = env['tg'].to(ANNA_TG)
    assert reply['text'] == rules.no_approver_text() and reply['markup'] is None


def test_cancel_removes_the_choice(env):
    _outcome, offer = _offer(env)
    assert env['service'].handle_callback(ANNA_TG, 'twofa:x:0', (ANNA_TG, offer['message_id'])) == ('', False)
    assert env['tg'].deleted == [{'chat_id': ANNA_TG, 'message_id': offer['message_id']}]
    assert _rows(env['db'], "SELECT id FROM twofa_requests") == []


def test_first_grant_sends_the_menu_with_the_button_once(env):
    menu = {'keyboard': [[{'text': 'Моя статистика📊'}], [{'text': rules.REQUEST_BUTTON}]], 'resize_keyboard': True}
    service = _with_menu(env, lambda _user_id: menu)
    head = service.context(HEAD)
    service.set_person_grants(head, OTHER, [env['account_id']])
    [welcome] = env['tg'].to(OTHER_TG)
    assert '«Google Ads»' in welcome['text'] and '«%s»' % rules.REQUEST_BUTTON in welcome['text']
    assert welcome['markup'] == menu
    # Ещё один аккаунт к уже открытому доступу — без нового сообщения: кнопка у человека есть.
    meta = _second_account(env, grant_to_anna=False)
    service.set_person_grants(head, OTHER, [env['account_id'], meta])
    assert len(env['tg'].to(OTHER_TG)) == 1
    # Сняли всё и выдали снова — доступ открылся заново, и сообщение тоже.
    service.set_person_grants(head, OTHER, [])
    service.set_person_grants(head, OTHER, [meta])
    assert len(env['tg'].to(OTHER_TG)) == 2
    assert '«ads@company.kz»' in env['tg'].to(OTHER_TG)[-1]['text']


def test_first_grant_without_telegram_or_key_sends_nothing(env, monkeypatch):
    service = env['service']
    service.set_person_grants(service.context(HEAD), BORIS, [env['account_id']])  # Telegram не привязан
    monkeypatch.delenv(crypto.ENV_KEY)
    # Без ключа доступы ведёт только супер-админ, а кнопка не сработала бы — и не обещаем её.
    assert service.set_person_grants(service.context(SUPER), OTHER, [env['account_id']])['outcome'] == 'ok'
    assert env['tg'].sent == []


def test_bot_menu_runs_the_real_functions_from_the_bot(env):
    """_main_menu_keyboard, _twofa_can_request_for и _twofa_reply_menu — настоящие, из
    bot_schedule2, на этой базе: кнопка появляется с выдачей, а сервис получает меню словарём."""
    import ast
    import logging
    from pathlib import Path

    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    names = {'_normalize_user_role', 'ROLE_HIERARCHY', '_get_role_level', '_has_min_role', '_is_admin_role',
             'get_admin_keyboard', 'get_sv_keyboard', 'get_operator_keyboard', '_main_menu_keyboard',
             '_twofa_can_request_for', '_twofa_reply_menu'}
    bot = Path(__file__).resolve().parents[1] / 'bot_schedule2.py'
    body = [node for node in ast.parse(bot.read_text(encoding='utf-8-sig')).body
            if (isinstance(node, ast.FunctionDef) and node.name in names)
            or (isinstance(node, ast.Assign) and any(getattr(t, 'id', None) in names for t in node.targets))]

    class Users:
        """db.get_user(id=…) бота — те поля кортежа, что читает меню: id, telegram_id, имя, роль."""

        def get_user(self, id):
            found = _rows(env['db'], "SELECT id, telegram_id, name, role FROM users WHERE id = %s", (id,))
            return found[0] if found else None

    namespace = {'db': Users(), 'logging': logging, 'ReplyKeyboardMarkup': ReplyKeyboardMarkup,
                 'KeyboardButton': KeyboardButton, 'TWOFA_REQUEST_BUTTON': rules.REQUEST_BUTTON,
                 '_TWOFA': {'service': env['service']}}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(bot), 'exec'), namespace)
    assert names <= set(namespace)
    reply_menu = namespace['_twofa_reply_menu']
    assert reply_menu(ANNA) == {'keyboard': [[{'text': rules.REQUEST_BUTTON}]], 'resize_keyboard': True}
    assert reply_menu(BORIS) is None  # роль без меню и без кодов
    operator_rows = [[{'text': 'Моя статистика📊'}], [{'text': 'Мои оценки📝'}], [{'text': 'Доступ🔑'}]]
    assert reply_menu(OTHER)['keyboard'] == operator_rows
    # Сервис с настоящим меню бота: первая выдача приносит оператору его меню и кнопку под ним.
    service = _with_menu(env, reply_menu)
    service.set_person_grants(service.context(HEAD), OTHER, [env['account_id']])
    [welcome] = env['tg'].to(OTHER_TG)
    assert welcome['markup']['keyboard'] == operator_rows + [[{'text': rules.REQUEST_BUTTON}]]
    assert not namespace['_twofa_can_request_for'](None)


# ─── Флаг профиля — настоящая функция из bot_schedule2 ───────────────────────

def test_profile_flag_runs_the_real_function_from_the_bot(env):
    """Тест профиля подменяет _twofa_section_open_for заглушкой — здесь она исполняется
    по-настоящему, на этой базе: имена сверены с модулем, SQL — с схемой."""
    import ast
    import logging
    from pathlib import Path

    bot = Path(__file__).resolve().parents[1] / 'bot_schedule2.py'
    tree = ast.parse(bot.read_text(encoding='utf-8-sig'))
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_twofa_section_open_for']
    assert len(funcs) == 1
    namespace = {'db': env['db'], 'logging': logging}
    exec(compile(ast.Module(body=funcs, type_ignores=[]), str(bot), 'exec'), namespace)
    open_for = namespace['_twofa_section_open_for']
    assert open_for(ANNA) and open_for(HEAD) and open_for(SUPER)
    assert not open_for(OTHER) and not open_for(BORIS) and not open_for(None)


# ─── Разделы аккаунтов ───────────────────────────────────────────────────────

SECRET_3 = b'0123456789abcdefghij'


def _groups(db):
    return _rows(db, 'SELECT id, title FROM twofa_groups ORDER BY id')


def _group_of(db, account_id):
    return _rows(db, 'SELECT group_id FROM twofa_accounts WHERE id = %s', (account_id,))[0][0]


def _events(db, kind):
    return [row[0] for row in _rows(db, 'SELECT details FROM twofa_events WHERE kind = %s ORDER BY id', (kind,))]


def test_accounts_added_before_sections_are_gathered_into_the_first_one_once(db):
    """Как на проде 27.10.2026: аккаунты уже есть, а колонки раздела ещё нет."""
    with db._get_cursor() as cur:
        cur.execute('ALTER TABLE twofa_accounts DROP COLUMN group_id')
        cur.execute("""
            INSERT INTO twofa_accounts (title, secret_enc, is_active, created_at, updated_at)
            VALUES ('Google Ads', 'x', TRUE, NOW(), NOW()), ('Meta', 'y', TRUE, NOW(), NOW()),
                   ('Удалённый', NULL, FALSE, NOW(), NOW())
        """)
        schema.init_twofa_schema(cur)
        schema.init_twofa_schema(cur)
    [(group_id, title)] = _groups(db)
    assert title == schema.DEFAULT_GROUP_TITLE == 'Общий маркетинг и рекламные аккаунты'
    assert {row[0] for row in _rows(db, 'SELECT group_id FROM twofa_accounts')} == {group_id}
    assert _events(db, 'groups_introduced') == [{'group': title, 'accounts': 2}]
    # Дальше «без раздела» — решение человека: следующий старт сервера его не трогает.
    with db._get_cursor() as cur:
        cur.execute("UPDATE twofa_accounts SET group_id = NULL WHERE title = 'Meta'")
        schema.init_twofa_schema(cur)
    assert _rows(db, "SELECT group_id FROM twofa_accounts WHERE title = 'Meta'") == [(None,)]
    assert len(_groups(db)) == 1 and len(_events(db, 'groups_introduced')) == 1


def test_fresh_install_starts_without_sections(db):
    assert _groups(db) == [] and _events(db, 'groups_introduced') == []


def test_accounts_go_into_the_chosen_section(env):
    service, db = env['service'], env['db']
    head = service.context(HEAD)
    assert _group_of(db, env['account_id']) is None  # фикстура добавила его без раздела
    [meta] = service.add_accounts(head, [{'title': 'Meta', 'service': None, 'secret': SECRET_2}],
                                  group={'title': 'SMM и таргет'})['created']
    [(smm, title)] = _groups(db)
    assert title == 'SMM и таргет' and _group_of(db, meta) == smm
    # То же название другим регистром — тот же раздел, а не второй.
    [tiktok] = service.add_accounts(head, [{'title': 'TikTok', 'service': None, 'secret': SECRET_3}],
                                    group={'title': 'smm И ТАРГЕТ'})['created']
    assert _group_of(db, tiktok) == smm and len(_groups(db)) == 1
    assert _events(db, 'group_created') == [{'group': 'SMM и таргет'}]
    added = _rows(db, "SELECT account_title, details FROM twofa_events WHERE kind = 'account_added' ORDER BY id")
    assert added[-1] == ('TikTok', {'group': 'SMM и таргет'})
    # Раздел удалили, пока окно добавления было открыто, — аккаунт не заводится.
    result = service.add_accounts(head, [{'title': 'X', 'service': None, 'secret': b'z' * 20}], group={'id': smm + 100})
    assert result == {'outcome': 'group_not_found'}
    assert _rows(db, "SELECT count(*) FROM twofa_accounts WHERE title = 'X'") == [(0,)]


def test_section_composition_moves_accounts_both_ways(env):
    service, db = env['service'], env['db']
    head = service.context(HEAD)
    google, meta = env['account_id'], _second_account(env, grant_to_anna=False)
    general = service.create_group(head, 'Общий', [google, meta])
    smm = service.create_group(head, 'SMM')['id']
    assert general['outcome'] == 'ok' and _group_of(db, google) == _group_of(db, meta) == general['id']
    # В «SMM» отметили Meta — она переезжает из «Общего».
    assert service.update_group(head, smm, account_ids=[meta]) == {'outcome': 'ok'}
    assert _group_of(db, meta) == smm and _group_of(db, google) == general['id']
    # Из «Общего» убрали Google — он остаётся без раздела, а не пропадает.
    assert service.update_group(head, general['id'], account_ids=[]) == {'outcome': 'ok'}
    assert _group_of(db, google) is None
    moved = _rows(db, "SELECT account_title, details FROM twofa_events WHERE kind = 'account_moved' ORDER BY id")
    assert moved[-2:] == [('ads@company.kz', {'from_group': 'Общий', 'to_group': 'SMM'}),
                          ('Google Ads', {'from_group': 'Общий', 'to_group': None})]
    # Название: занятое — нельзя, своё же другим регистром — можно.
    assert service.update_group(head, smm, title='общий') == {'outcome': 'group_exists'}
    assert service.create_group(head, 'SMM') == {'outcome': 'group_exists'}
    assert service.update_group(head, smm, title='smm') == {'outcome': 'ok'}
    assert dict(_groups(db))[smm] == 'smm' and _events(db, 'group_renamed') == [{'from': 'SMM', 'to': 'smm'}]
    # Удалённый аккаунт в составе — отказ, и ничего не тронуто.
    service.remove_account(head, meta)
    assert service.update_group(head, smm, account_ids=[meta, google]) == {'outcome': 'unknown_account'}
    assert _group_of(db, google) is None
    assert service.update_group(head, smm + general['id'] + 100, title='Нет') == {'outcome': 'not_found'}


def test_deleting_a_section_keeps_its_accounts_and_access(env):
    service, db = env['service'], env['db']
    head = service.context(HEAD)
    group_id = service.create_group(head, 'Временный', [env['account_id']])['id']
    assert service.delete_group(head, group_id) == {'outcome': 'ok', 'accounts': 1}
    assert _groups(db) == [] and _group_of(db, env['account_id']) is None
    assert _events(db, 'group_deleted') == [{'group': 'Временный', 'accounts': 1}]
    assert _request(env)['outcome'] == 'created'  # доступ и запрос кода — как были
    assert service.delete_group(head, group_id) == {'outcome': 'not_found'}


def test_account_sheet_moves_the_account(env):
    service, db = env['service'], env['db']
    head = service.context(HEAD)
    account_id = env['account_id']
    assert service.update_account(head, account_id, 'Google Ads', 'Google', group={'title': 'Реклама'}) == {
        'outcome': 'ok'}
    [(ads, _title)] = _groups(db)
    assert _group_of(db, account_id) == ads
    # Правка одного названия раздел не трогает.
    assert service.update_account(head, account_id, 'Google Ads — кабинет', 'Google')['outcome'] == 'ok'
    assert _group_of(db, account_id) == ads
    assert service.update_account(head, account_id, 'Google Ads — кабинет', 'Google', group=None)['outcome'] == 'ok'
    assert _group_of(db, account_id) is None
    # Раздел исчез — ни название, ни раздел не меняются.
    assert service.update_account(head, account_id, 'X', None, group={'id': ads + 1})['outcome'] == 'group_not_found'
    assert _rows(db, 'SELECT title FROM twofa_accounts WHERE id = %s', (account_id,)) == [('Google Ads — кабинет',)]
    assert _events(db, 'account_moved') == [{'from_group': None, 'to_group': 'Реклама'},
                                            {'from_group': 'Реклама', 'to_group': None}]


def test_bot_lists_accounts_section_by_section(env):
    service = env['service']
    head = service.context(HEAD)
    meta = _second_account(env)  # «ads@company.kz» по алфавиту раньше «Google Ads»
    service.create_group(head, 'SMM', [env['account_id']])
    service.create_group(head, 'Медиабаинг', [meta])
    _outcome, offer = _offer(env)
    pressed = [row[0]['callback_data'] for row in offer['markup']['inline_keyboard'][:-1]]
    assert pressed == ['twofa:req:%d' % env['account_id'], 'twofa:req:%d' % meta]  # «SMM» создан первым


def test_sections_through_the_routes(client, env):
    client.as_user(HEAD)
    response = client.post('/api/twofa/groups', json={'title': '  Общий   маркетинг ', 'account_ids': [env['account_id']]})
    assert response.status_code == 201
    general = response.get_json()['id']
    screen = client.get('/api/twofa').get_json()
    assert screen['groups'] == [{'id': general, 'title': 'Общий маркетинг'}]
    assert screen['accounts'][0]['group_id'] == general
    again = client.post('/api/twofa/groups', json={'title': 'общий МАРКЕТИНГ'})
    assert again.status_code == 409 and again.get_json()['code'] == 'TWOFA_GROUP_EXISTS'
    assert again.get_json()['field'] == 'title'
    empty = client.post('/api/twofa/groups', json={'title': '   '})
    assert empty.status_code == 400 and empty.get_json()['field'] == 'title'
    # Новый аккаунт — сразу в новый раздел; удалённый раздел — ошибка у поля «Раздел».
    added = client.post('/api/twofa/accounts', json={
        'text': base64.b32encode(SECRET_2).decode(), 'items': [{'index': 0, 'title': 'Meta'}], 'group_title': 'SMM'})
    assert added.status_code == 201
    [meta] = added.get_json()['created']
    gone = client.post('/api/twofa/accounts', json={
        'text': base64.b32encode(SECRET_3).decode(), 'items': [{'index': 0, 'title': 'TT'}], 'group_id': 999999})
    assert gone.status_code == 409 and gone.get_json()['field'] == 'group'
    groups = client.get('/api/twofa').get_json()['groups']
    assert [g['title'] for g in groups] == ['Общий маркетинг', 'SMM']
    smm = groups[1]['id']
    # Карточка аккаунта: перенос по id и «без раздела» по null; без поля раздел не трогается.
    edit = '/api/twofa/accounts/%d' % meta
    assert client.patch(edit, json={'title': 'Meta', 'group_id': general}).status_code == 200
    assert _group_of(env['db'], meta) == general
    assert client.patch(edit, json={'title': 'Meta Ads'}).status_code == 200
    assert _group_of(env['db'], meta) == general
    assert client.patch(edit, json={'title': 'Meta Ads', 'group_id': None}).status_code == 200
    assert _group_of(env['db'], meta) is None
    # Сотрудник видит только разделы своих аккаунтов.
    client.as_user(ANNA)
    assert client.get('/api/twofa').get_json()['groups'] == [{'id': general, 'title': 'Общий маркетинг'}]
    assert client.post('/api/twofa/groups', json={'title': 'X'}).status_code == 403
    # Переименовать и удалить; журнал отдаёт поля разделов.
    client.as_user(HEAD)
    assert client.patch('/api/twofa/groups/%d' % general, json={'title': 'Общий'}).status_code == 200
    assert client.patch('/api/twofa/groups/%d' % smm, json={'account_ids': ['x']}).status_code == 400
    assert client.delete('/api/twofa/groups/%d' % general).get_json() == {'ok': True, 'accounts': 1}
    items = {item['kind']: item for item in reversed(client.get('/api/twofa/journal').get_json()['items'])}
    assert items['group_deleted']['details'] == {'group': 'Общий', 'accounts': 1}
    assert items['group_renamed']['details'] == {'from': 'Общий маркетинг', 'to': 'Общий'}
    assert items['account_moved']['details'] == {'from_group': 'Общий маркетинг', 'to_group': None}
    assert not items['group_deleted']['system'] and items['group_deleted']['actor_name'] == 'Глава Маркетинга'
