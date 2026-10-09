"""Настоящий SQL рабочего места «Чатов ОП» на отдельном локальном Postgres.

Порт одноразового кластера — в WAZZUP_PILOT_TEST_PORT (тот же, что у тестов
обработки чатов). Приложение не импортируется, боевых доступов нет, каждый тест
живёт в своей случайной схеме и убирает только её.
"""
import ast
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests import source_cache
from tests.test_wazzup_pilot_persistence import PORT, _database_class, message
from tests.test_wazzup_pilot_persistence import pg  # noqa: F401: общая локальная фикстура
from wazzup import access
from wazzup.workspace_schema import init_wazzup_workspace_schema
from wazzup.workspace_store import WorkspaceStore

pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')
ROOT = Path(__file__).resolve().parents[1]

OP, SZOV = 367, 501
HEAD, SV, VERIFIER, OSNOVA, FIRED_HEAD = 30, 20, 10, 11, 31
TODAY = date(2026, 10, 8)
SECRET = 'unit-test-secret'


class _Db:
    """То, что хранилищу нужно от Database: курсор с фиксацией на выходе."""

    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def _get_cursor(self):
        with self.connection.cursor() as cursor:
            try:
                yield cursor
            except Exception:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def append_operator_status_event(self, operator_id, event_at, status_key, state_note=None,
                                     event_kind=None, client_event_id=None):
        with self._get_cursor() as cursor:
            cursor.execute("""INSERT INTO operator_status_events
                (operator_id, event_at, status_key, state_note, event_kind, client_event_id)
                VALUES (%s, %s, %s, %s, %s, %s)""",
                (operator_id, event_at, status_key, state_note, event_kind, client_event_id))
        return {'duplicate': False}


@pytest.fixture
def stand():
    import psycopg2
    schema = 't_wazzup_workspace_' + uuid.uuid4().hex
    connections = []

    def connect():
        connection = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres',
                                      dbname='postgres', connect_timeout=3)
        connections.append(connection)
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO ' + schema)
            cursor.execute("SET statement_timeout TO '8s'")
        connection.commit()
        return connection

    connection = connect()
    cursor = connection.cursor()
    cursor.execute('CREATE SCHEMA ' + schema)
    cursor.execute('''
        CREATE TABLE departments (id INTEGER PRIMARY KEY, name TEXT, code TEXT,
            head_user_id INTEGER, is_active BOOLEAN DEFAULT TRUE);
        CREATE TABLE directions (id INTEGER PRIMARY KEY, name TEXT, calculation_model_code TEXT);
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, role TEXT, status TEXT,
            telegram_id BIGINT, department_id INTEGER, direction_id INTEGER);
        CREATE TABLE groups (id INTEGER PRIMARY KEY, name TEXT, calculation_model_code TEXT);
        CREATE TABLE group_operator_memberships (id SERIAL PRIMARY KEY, group_id INTEGER,
            operator_id INTEGER, start_date DATE, end_date DATE);
        CREATE TABLE user_sessions (session_id UUID PRIMARY KEY, user_id INTEGER,
            revoked_at TIMESTAMP, expires_at TIMESTAMP NOT NULL,
            sensitive_data_unlocked BOOLEAN NOT NULL DEFAULT FALSE);
        CREATE TABLE operator_status_events (id BIGSERIAL PRIMARY KEY, operator_id INTEGER,
            event_at TIMESTAMP, status_key TEXT, state_note TEXT, event_kind TEXT, client_event_id TEXT);
    ''')
    init_wazzup_workspace_schema(cursor)
    init_wazzup_workspace_schema(cursor)         # идемпотентна: стартует при каждом запуске
    cursor.execute('''
        INSERT INTO departments (id, name, code, head_user_id) VALUES
            (%(op)s, 'Отдел продаж', 'op', %(head)s), (%(szov)s, 'СЗоВ', 'szov', NULL);
        INSERT INTO directions (id, name, calculation_model_code) VALUES
            (71, 'Верификатор', 'op_verificator'), (73, 'Основа ОП', 'op_osnova');
        INSERT INTO groups (id, name, calculation_model_code) VALUES
            (13, 'Верификаторы', 'op_verificator'), (36, 'Основа', 'op_osnova'), (41, 'Без модели', NULL);
        INSERT INTO users (id, name, role, status, telegram_id, department_id, direction_id) VALUES
            (%(head)s, 'Глава Продаж', 'admin', 'working', 7001, %(op)s, NULL),
            (%(fired)s, 'Прежний Глава', 'admin', 'fired', 7002, %(op)s, NULL),
            (%(sv)s, 'Мухтар Адилет', 'sv', 'working', 7003, %(op)s, NULL),
            (%(verifier)s, 'Сарсеке Мерей', 'operator', 'working', NULL, %(op)s, 73),
            (%(osnova)s, 'Оператор Основы', 'operator', 'working', NULL, %(op)s, 71);
        INSERT INTO group_operator_memberships (group_id, operator_id, start_date, end_date) VALUES
            (13, %(verifier)s, '2026-09-01', NULL),
            (36, %(osnova)s, '2026-09-01', NULL);
    ''', {'op': OP, 'szov': SZOV, 'head': HEAD, 'fired': FIRED_HEAD, 'sv': SV,
          'verifier': VERIFIER, 'osnova': OSNOVA})
    connection.commit()
    try:
        yield {'connection': connection, 'cursor': cursor, 'connect': connect,
               'store': WorkspaceStore(_Db(connection))}
    finally:
        for other in connections[1:]:
            other.close()
        connection.rollback()
        cursor.execute('DROP SCHEMA ' + schema + ' CASCADE')
        connection.commit()
        connection.close()


def new_session(stand, user_id=VERIFIER, *, expires_in_days=30, revoked=False):
    session_id = str(uuid.uuid4())
    stand['cursor'].execute(
        """INSERT INTO user_sessions (session_id, user_id, revoked_at, expires_at)
           VALUES (%s, %s, %s, (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + make_interval(days => %s))""",
        (session_id, user_id, datetime(2026, 10, 8) if revoked else None, expires_in_days))
    stand['connection'].commit()
    return session_id


def state(stand, user_id, session_id=None, day=TODAY):
    return stand['store'].operator_state(user_id, session_id, day, OP)


def open_challenge(stand, session_id, *, approver=SV, code='482915', ttl=600, moment=None):
    moment = moment or datetime.now(timezone.utc)
    challenge_id = uuid.uuid4()
    stand['store'].create_challenge(challenge_id, session_id, VERIFIER, approver, HEAD,
                                    access.hash_code(SECRET, challenge_id, code),
                                    moment + timedelta(seconds=ttl), moment)
    return challenge_id


def redeem(stand, challenge_id, code, *, approver=SV, store=None, moment=None):
    return (store or stand['store']).redeem(
        challenge_id, approver, moment or datetime.now(timezone.utc),
        lambda row: access.code_matches(SECRET, challenge_id, code, row['code_hash']),
        access.CODE_MAX_ATTEMPTS)


# ── кто верификатор и открыт ли ему доступ ───────────────────────────────────

def test_verifier_is_decided_by_the_group_of_the_day_not_the_direction(stand):
    # Направление у него «Основа ОП», группа — верификаторы: решает группа.
    assert state(stand, VERIFIER) == {'verifier': True, 'unlocked': False}
    # И наоборот: направление «Верификатор», а группа — «Основа».
    assert state(stand, OSNOVA) == {'verifier': False, 'unlocked': False}
    assert state(stand, SV)['verifier'] is False
    assert state(stand, 999) == {'verifier': False, 'unlocked': False}


def test_membership_dates_and_direction_fallback(stand):
    cursor, connection = stand['cursor'], stand['connection']
    # До начала членства группы нет — остаётся направление («Основа ОП»).
    assert state(stand, VERIFIER, day=date(2026, 8, 31))['verifier'] is False
    # Членство закончилось вчера: сегодня оператор «Основы» с направлением «Верификатор» — верификатор.
    cursor.execute("UPDATE group_operator_memberships SET end_date = %s WHERE operator_id = %s",
                   (TODAY - timedelta(days=1), OSNOVA))
    connection.commit()
    assert state(stand, OSNOVA)['verifier'] is True
    assert state(stand, OSNOVA, day=TODAY - timedelta(days=1))['verifier'] is False
    # Группа без модели расчёта не решает ничего — берётся направление.
    cursor.execute("""INSERT INTO group_operator_memberships (group_id, operator_id, start_date)
                      VALUES (41, %s, %s)""", (OSNOVA, TODAY))
    connection.commit()
    assert state(stand, OSNOVA)['verifier'] is True
    # Из двух членств на дату действует начатое позже.
    cursor.execute("""INSERT INTO group_operator_memberships (group_id, operator_id, start_date)
                      VALUES (36, %s, %s)""", (VERIFIER, TODAY))
    connection.commit()
    assert state(stand, VERIFIER)['verifier'] is False


def test_department_and_status_are_part_of_the_rule(stand):
    cursor, connection = stand['cursor'], stand['connection']
    cursor.execute("UPDATE users SET department_id = %s WHERE id = %s", (SZOV, VERIFIER))
    connection.commit()
    assert state(stand, VERIFIER)['verifier'] is False
    # Отдел продаж узнаётся и по id: код в справочнике заполнен не везде.
    cursor.execute("UPDATE users SET department_id = %s WHERE id = %s", (OP, VERIFIER))
    cursor.execute("UPDATE departments SET code = NULL WHERE id = %s", (OP,))
    connection.commit()
    assert state(stand, VERIFIER)['verifier'] is True
    for status, expected in (('fired', False), ('dismissal', False), ('vacation', True)):
        cursor.execute("UPDATE users SET status = %s WHERE id = %s", (status, VERIFIER))
        connection.commit()
        assert state(stand, VERIFIER)['verifier'] is expected, status
    cursor.execute("UPDATE users SET status = 'working', role = 'trainee' WHERE id = %s", (VERIFIER,))
    connection.commit()
    assert state(stand, VERIFIER)['verifier'] is False


def test_access_belongs_to_one_live_session(stand):
    session = new_session(stand)
    other = new_session(stand)
    challenge = open_challenge(stand, session)
    assert redeem(stand, challenge, '482915')[0] == 'granted'
    assert state(stand, VERIFIER, session) == {'verifier': True, 'unlocked': True}
    assert state(stand, VERIFIER, other)['unlocked'] is False
    # «Нет сессии» во всех её видах — закрыто, а не ошибка запроса.
    for missing in (None, '', 'None', 'not-a-session', 12345):
        assert state(stand, VERIFIER, missing) == {'verifier': True, 'unlocked': False}, missing
        assert stand['store'].session_is_live(missing, VERIFIER) is False
    # Чужой человек с этой же сессией доступа не получает.
    assert state(stand, OSNOVA, session) == {'verifier': False, 'unlocked': False}
    assert stand['store'].session_is_live(session, VERIFIER) is True
    assert stand['store'].session_is_live(session, OSNOVA) is False


def test_portal_qr_opens_the_chats_of_that_session_only(stand):
    """Решение владельца 09.10.2026: обычный QR портала (тот же, что у «Вики»)
    открывает и чаты — без строки в wazzup_chat_access и без кода главы."""
    cursor, connection = stand['cursor'], stand['connection']

    def unlock_portal(session_id):
        cursor.execute("UPDATE user_sessions SET sensitive_data_unlocked = TRUE WHERE session_id = %s",
                       (session_id,))
        connection.commit()

    session, other = new_session(stand), new_session(stand)
    assert state(stand, VERIFIER, session) == {'verifier': True, 'unlocked': False}
    unlock_portal(session)
    assert state(stand, VERIFIER, session) == {'verifier': True, 'unlocked': True}
    assert state(stand, VERIFIER, other)['unlocked'] is False
    cursor.execute('SELECT COUNT(*) FROM wazzup_chat_access')
    assert cursor.fetchone()[0] == 0
    # Подтверждение живёт с сессией: отозванная или истёкшая чатов не открывает.
    revoked = new_session(stand, revoked=True)
    expired = new_session(stand, expires_in_days=-1)
    for dead in (revoked, expired):
        unlock_portal(dead)
        assert state(stand, VERIFIER, dead)['unlocked'] is False
    # Сессия чужого человека, даже подтверждённая, верификатору не засчитывается.
    stranger = new_session(stand, user_id=OSNOVA)
    unlock_portal(stranger)
    assert state(stand, VERIFIER, stranger)['unlocked'] is False
    # Обычный QR верификатором не делает: оператор «Основы» остаётся без режима обработки.
    assert state(stand, OSNOVA, stranger) == {'verifier': False, 'unlocked': False}
    # Снял подтверждение сам (выход из QR-доступа) — чаты снова закрыты.
    cursor.execute("UPDATE user_sessions SET sensitive_data_unlocked = FALSE WHERE session_id = %s",
                   (session,))
    connection.commit()
    assert state(stand, VERIFIER, session)['unlocked'] is False
    # Прежний код чатов продолжает действовать и без обычного подтверждения.
    assert redeem(stand, open_challenge(stand, session), '482915')[0] == 'granted'
    assert state(stand, VERIFIER, session)['unlocked'] is True


def test_access_ends_with_the_session(stand):
    cursor, connection = stand['cursor'], stand['connection']
    session = new_session(stand)
    assert redeem(stand, open_challenge(stand, session), '482915')[0] == 'granted'
    cursor.execute("UPDATE user_sessions SET revoked_at = now() WHERE session_id = %s", (session,))
    connection.commit()
    assert state(stand, VERIFIER, session)['unlocked'] is False
    assert stand['store'].session_is_live(session, VERIFIER) is False
    cursor.execute("""UPDATE user_sessions SET revoked_at = NULL,
                      expires_at = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - interval '1 second'
                      WHERE session_id = %s""", (session,))
    connection.commit()
    assert state(stand, VERIFIER, session)['unlocked'] is False
    # Строка доступа уходит вместе с сессией.
    cursor.execute("DELETE FROM user_sessions WHERE session_id = %s", (session,))
    connection.commit()
    cursor.execute("SELECT COUNT(*) FROM wazzup_chat_access")
    assert cursor.fetchone()[0] == 0
    cursor.execute("SELECT COUNT(*) FROM wazzup_chat_access_challenges")
    assert cursor.fetchone()[0] == 0


def test_revoked_grant_row_closes_access(stand):
    session = new_session(stand)
    assert redeem(stand, open_challenge(stand, session), '482915')[0] == 'granted'
    stand['cursor'].execute("UPDATE wazzup_chat_access SET revoked_at = now() WHERE session_id = %s", (session,))
    stand['connection'].commit()
    assert state(stand, VERIFIER, session)['unlocked'] is False
    # Повторное подтверждение открывает ту же сессию заново.
    assert redeem(stand, open_challenge(stand, session), '482915')[0] == 'granted'
    assert state(stand, VERIFIER, session)['unlocked'] is True
    stand['cursor'].execute("SELECT COUNT(*), MAX(granted_by), MAX(code_recipient_id) FROM wazzup_chat_access")
    assert stand['cursor'].fetchone() == (1, SV, HEAD)


def test_admin_grant_without_code_is_session_bound_and_audited(stand):
    store = stand['store']
    session = new_session(stand)
    other = new_session(stand)
    assert store.grant_without_code(session, VERIFIER, HEAD) is True
    assert state(stand, VERIFIER, session)['unlocked'] is True
    assert state(stand, VERIFIER, other)['unlocked'] is False
    assert store.grant_without_code(session, VERIFIER, HEAD) is True
    stand['cursor'].execute('SELECT user_id, granted_by, code_recipient_id FROM wazzup_chat_access')
    assert stand['cursor'].fetchall() == [(VERIFIER, HEAD, None)]
    stand['cursor'].execute('SELECT COUNT(*) FROM wazzup_chat_access_challenges')
    assert stand['cursor'].fetchone()[0] == 0
    stand['cursor'].execute('UPDATE wazzup_chat_access SET revoked_at = now() WHERE session_id = %s', (session,))
    stand['connection'].commit()
    assert store.grant_without_code(session, VERIFIER, HEAD) is True
    assert state(stand, VERIFIER, session)['unlocked'] is True


def test_admin_grant_rechecks_live_session_and_owner(stand):
    store = stand['store']
    assert store.grant_without_code(new_session(stand, revoked=True), VERIFIER, HEAD) is False
    assert store.grant_without_code(new_session(stand, expires_in_days=-1), VERIFIER, HEAD) is False
    assert store.grant_without_code(new_session(stand, user_id=OSNOVA), VERIFIER, HEAD) is False
    assert store.grant_without_code(str(uuid.uuid4()), VERIFIER, HEAD) is False
    stand['cursor'].execute('SELECT COUNT(*) FROM wazzup_chat_access')
    assert stand['cursor'].fetchone()[0] == 0


def test_code_goes_to_the_working_head_of_the_employees_department(stand):
    cursor, connection, store = stand['cursor'], stand['connection'], stand['store']
    assert store.code_recipient(VERIFIER) == {'id': HEAD, 'name': 'Глава Продаж', 'telegram_id': 7001,
                                             'department_name': 'Отдел продаж'}
    cursor.execute("UPDATE users SET telegram_id = NULL WHERE id = %s", (HEAD,))
    connection.commit()
    assert store.code_recipient(VERIFIER)['telegram_id'] is None
    cursor.execute("UPDATE departments SET head_user_id = %s WHERE id = %s", (FIRED_HEAD, OP))
    connection.commit()
    assert store.code_recipient(VERIFIER) is None, 'уволенному главе код не уходит'
    cursor.execute("UPDATE departments SET head_user_id = %s, is_active = FALSE WHERE id = %s", (HEAD, OP))
    connection.commit()
    assert store.code_recipient(VERIFIER) is None
    cursor.execute("UPDATE departments SET head_user_id = NULL, is_active = TRUE WHERE id = %s", (OP,))
    connection.commit()
    assert store.code_recipient(VERIFIER) is None


# ── скан, ждущий код ─────────────────────────────────────────────────────────

def test_challenge_lifecycle(stand):
    store = stand['store']
    session = new_session(stand)
    now = datetime.now(timezone.utc)
    challenge = open_challenge(stand, session, moment=now)
    active = store.active_challenge(session, SV, VERIFIER, now, access.CODE_MAX_ATTEMPTS)
    assert (active['id'], active['sent_count'], active['attempts']) == (str(challenge), 1, 0)
    assert active['expires_at'] == now + timedelta(seconds=600)
    assert store.recent_challenges(SV, VERIFIER) == 1
    assert store.active_challenge(session, HEAD, VERIFIER, now, access.CODE_MAX_ATTEMPTS) is None
    assert store.challenge(challenge, HEAD) is None, 'чужое ожидание не отдаётся'
    assert store.challenge(challenge, SV)['recipient_id'] == HEAD

    outcome, left, _ = redeem(stand, challenge, '000000')
    assert (outcome, left) == ('wrong', access.CODE_MAX_ATTEMPTS - 1)
    assert redeem(stand, challenge, '482915', approver=HEAD)[0] == 'gone'
    outcome, left, row = redeem(stand, challenge, '482915')
    assert (outcome, row['user_id'], row['session_id']) == ('granted', VERIFIER, session)
    assert redeem(stand, challenge, '482915')[0] == 'gone', 'код одноразовый'
    assert store.active_challenge(session, SV, VERIFIER, now, access.CODE_MAX_ATTEMPTS) is None
    assert store.challenge(challenge, SV)['consumed_at'] is not None


def test_attempts_run_out_and_new_code_restores_them(stand):
    store = stand['store']
    session = new_session(stand)
    challenge = open_challenge(stand, session)
    for left in range(access.CODE_MAX_ATTEMPTS - 1, -1, -1):
        assert redeem(stand, challenge, '000000')[:2] == ('wrong', left)
    assert redeem(stand, challenge, '482915')[:2] == ('attempts', 0)
    assert store.active_challenge(session, SV, VERIFIER, datetime.now(timezone.utc),
                                  access.CODE_MAX_ATTEMPTS) is None

    now = datetime.now(timezone.utc) + timedelta(seconds=access.CODE_RESEND_SECONDS)
    sent = store.renew_code(challenge, SV, access.hash_code(SECRET, challenge, '111222'), now,
                            now + timedelta(seconds=600), HEAD, access.CODE_MAX_SENDS)
    assert sent == 2
    assert redeem(stand, challenge, '482915')[0] == 'wrong', 'прежний код погашен'
    assert redeem(stand, challenge, '111222')[0] == 'granted'
    assert store.renew_code(challenge, SV, 'x', now, now, HEAD, access.CODE_MAX_SENDS) is None


def test_resend_is_capped_and_bound_to_the_scanner(stand):
    store = stand['store']
    challenge = open_challenge(stand, new_session(stand))
    now = datetime.now(timezone.utc) + timedelta(seconds=access.CODE_RESEND_SECONDS)
    args = (now, now + timedelta(seconds=600), HEAD, access.CODE_MAX_SENDS)
    assert store.renew_code(challenge, HEAD, 'h', *args) is None
    assert store.renew_code(challenge, SV, 'first', *args) == 2
    assert store.renew_code(challenge, SV, 'too-soon', *args) is None
    later = now + timedelta(seconds=access.CODE_RESEND_SECONDS)
    assert store.renew_code(challenge, SV, 'second', later, later + timedelta(seconds=600),
                            HEAD, access.CODE_MAX_SENDS) == 3
    assert store.renew_code(challenge, SV, 'over-limit', later + timedelta(seconds=60),
                            later + timedelta(seconds=660), HEAD, access.CODE_MAX_SENDS) is None


def test_parallel_scans_reuse_one_challenge_and_resends_keep_one_code(stand):
    session = new_session(stand)
    stores = [WorkspaceStore(_Db(stand['connect']())) for _ in range(4)]
    barrier, results = threading.Barrier(4), []
    moment = datetime.now(timezone.utc)

    def scan(store):
        barrier.wait(5)
        results.append(store.create_challenge(uuid.uuid4(), session, VERIFIER, SV, HEAD,
                                              'initial', moment + timedelta(seconds=600), moment)[0])

    threads = [threading.Thread(target=scan, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(results) == ['active', 'active', 'active', 'created']
    assert stand['store'].recent_challenges(SV, VERIFIER) == 1
    challenge = stand['store'].active_challenge(session, SV, VERIFIER, moment, 5)['id']
    results.clear()
    barrier.reset()
    sent_at = moment + timedelta(seconds=60)

    def resend(store, code):
        barrier.wait(5)
        results.append((store.renew_code(challenge, SV, code, sent_at,
                                         sent_at + timedelta(seconds=600), HEAD, 3), code))

    threads = [threading.Thread(target=resend, args=(store, f'code-{i}'))
               for i, store in enumerate(stores)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    winners = [code for count, code in results if count == 2]
    assert len(results) == 4 and len(winners) == 1
    assert stand['store'].challenge(challenge, SV)['code_hash'] == winners[0]


def test_expired_code_and_cleanup_of_old_scans(stand):
    store, cursor, connection = stand['store'], stand['cursor'], stand['connection']
    session = new_session(stand)
    past = datetime.now(timezone.utc) - timedelta(seconds=700)
    challenge = open_challenge(stand, session, moment=past)
    assert redeem(stand, challenge, '482915')[0] == 'expired'
    assert state(stand, VERIFIER, session)['unlocked'] is False
    assert store.active_challenge(session, SV, VERIFIER, datetime.now(timezone.utc),
                                  access.CODE_MAX_ATTEMPTS) is None
    cursor.execute("UPDATE wazzup_chat_access_challenges SET created_at = now() - interval '3 days'")
    connection.commit()
    assert store.recent_challenges(SV, VERIFIER) == 0
    store.active_challenge(session, SV, VERIFIER, datetime.now(timezone.utc), access.CODE_MAX_ATTEMPTS)
    cursor.execute("SELECT COUNT(*) FROM wazzup_chat_access_challenges")
    assert cursor.fetchone()[0] == 0, 'старые ожидания убираются по дороге'
    store.drop_challenge(uuid.uuid4())           # несуществующее — не ошибка


def test_parallel_wrong_codes_each_spend_an_attempt_and_right_code_wins_once(stand):
    """Счётчик попыток и выдача под одним замком строки: параллельный ввод не
    даёт ни лишней попытки, ни двойной выдачи."""
    session = new_session(stand)
    challenge = open_challenge(stand, session)
    stores = [WorkspaceStore(_Db(stand['connect']())) for _ in range(4)]
    results, barrier = [], threading.Barrier(4)

    def attempt(store, code):
        barrier.wait(5)
        results.append(redeem(stand, challenge, code, store=store)[0])

    threads = [threading.Thread(target=attempt, args=(store, '000000')) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert results == ['wrong'] * 4
    assert stand['store'].challenge(challenge, SV)['attempts'] == 4

    results.clear()
    barrier.reset()
    threads = [threading.Thread(target=attempt, args=(store, '482915')) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(results) == ['gone', 'gone', 'gone', 'granted']
    stand['cursor'].execute("SELECT COUNT(*) FROM wazzup_chat_access")
    assert stand['cursor'].fetchone()[0] == 1


# ── смена ────────────────────────────────────────────────────────────────────

def test_latest_status_presence_and_stale_rows(stand):
    store = stand['store']
    base = datetime(2026, 10, 8, 9, 0)
    store.append_status(VERIFIER, base, 'готов', None, 'wzws-1')
    store.append_status(VERIFIER, base + timedelta(minutes=30), 'перерыв', None, 'wzws-2')
    stand['cursor'].execute("""INSERT INTO operator_status_events
        (operator_id, event_at, status_key, event_kind, client_event_id)
        VALUES (%s, %s, 'take chat', 'action', 'c2d-1'), (%s, %s, 'готов', 'status', 'wzws-future')""",
        (VERIFIER, base + timedelta(minutes=40), VERIFIER, base + timedelta(hours=5)))
    stand['connection'].commit()
    latest = store.latest_status(VERIFIER, base + timedelta(minutes=45))
    assert (latest['status_key'], latest['client_event_id']) == ('перерыв', 'wzws-2'), \
        'действие статусом не считается, будущее событие не видно'
    assert store.latest_status(OSNOVA, base) is None

    assert store.last_seen(VERIFIER) is None
    store.touch(VERIFIER, base + timedelta(minutes=31))
    store.touch(VERIFIER, base + timedelta(minutes=20))      # опоздавшая отметка второй вкладки
    assert store.last_seen(VERIFIER) == base + timedelta(minutes=31)

    moment = base + timedelta(hours=1)
    rows = store.stale_presences(moment - timedelta(minutes=10), moment - timedelta(hours=48))
    assert [(row['user_id'], row['last_seen_at']) for row in rows] == [(VERIFIER, base + timedelta(minutes=31))]
    # Последний статус в строке сторожа — без окна по времени, как у append_operator_status_event.
    assert rows[0]['latest']['status_key'] == 'готов' and rows[0]['latest']['client_event_id'] == 'wzws-future'
    assert store.stale_presences(moment - timedelta(hours=2), moment - timedelta(hours=48)) == []
    store.touch(OSNOVA, base)                                # отметка без единого статуса сторожу не интересна
    assert len(store.stale_presences(moment, moment - timedelta(hours=48))) == 1


# ── авторство сообщений, отправленных из iCORE ───────────────────────────────

def outbox(cursor, message_id, author_id, *, name='Сарсеке Мерей', user_id=VERIFIER):
    cursor.execute("""INSERT INTO wazzup_pilot_outbox
        (request_id, account, channel_id, chat_id, chat_type, text, user_id, author_name,
         state, message_id, author_id)
        VALUES (%s, 'op', 'channel-op', '70000000000', 'whatsapp', 'Текст', %s, %s, 'sent', %s, %s)""",
        (str(uuid.uuid4()), user_id, name, message_id, author_id))


def author(cursor, message_id):
    cursor.execute("SELECT author_id, author_name, status FROM wazzup_messages WHERE message_id = %s",
                   (message_id,))
    return cursor.fetchone()


def test_echo_of_a_verifier_send_keeps_the_real_author(pg):
    """Wazzup возвращает отправленное по API от «Admin» без id автора."""
    connection, cursor, database, _ = pg
    db = database()
    outbox(cursor, 'm1', 'icore:10')
    connection.commit()
    db.store_wazzup_messages([message('m1', status='sent', authorName='Admin')])
    connection.commit()
    assert author(cursor, 'm1') == ('icore:10', 'Сарсеке Мерей', 'sent')
    # Статус доставки и повтор вебхука автора не возвращают.
    db.update_wazzup_statuses([{'messageId': 'm1', 'status': 'read'}])
    db.store_wazzup_messages([message('m1', status='delivered', authorName='Admin')])
    connection.commit()
    assert author(cursor, 'm1') == ('icore:10', 'Сарсеке Мерей', 'read')


def test_echo_that_arrived_before_the_outbox_knew_its_id_is_stamped_afterwards(pg):
    connection, cursor, database, _ = pg
    database().store_wazzup_messages([message('m2', authorName='Admin')])
    connection.commit()
    assert author(cursor, 'm2')[:2] == (None, 'Admin')
    outbox(cursor, 'm2', 'icore:10')
    # То, что делает отправка, когда эхо уже в архиве (wazzup/pilot.py).
    cursor.execute("UPDATE wazzup_messages SET author_id=%s,author_name=%s WHERE account='op' AND message_id=%s",
                   ('icore:10', 'Сарсеке Мерей', 'm2'))
    connection.commit()
    assert author(cursor, 'm2')[:2] == ('icore:10', 'Сарсеке Мерей')


def test_other_messages_keep_their_authors(pg):
    connection, cursor, database, _ = pg
    db = database()
    outbox(cursor, 'admin-send', None, name='Супер Админ', user_id=2)   # супер-админ: без авторства
    connection.commit()
    db.store_wazzup_messages([
        message('admin-send', authorName='Admin'),
        message('native', authorName='Нуржан Перизат', authorId='12345678'),
        dict(message('inbound'), isEcho=False),
    ])
    connection.commit()
    assert author(cursor, 'admin-send')[:2] == (None, 'Admin')
    assert author(cursor, 'native')[:2] == ('12345678', 'Нуржан Перизат')
    assert author(cursor, 'inbound')[:2] == (None, None)
    # Сообщение другого аккаунта с тем же id автора не получает.
    outbox(cursor, 'potok-1', 'icore:10')
    cursor.execute("""INSERT INTO wazzup_messages (message_id, channel_id, chat_id, dt, is_echo, author_name, account)
                      VALUES ('potok-1', 'c', 'chat', now(), TRUE, 'Admin', 'potok')""")
    connection.commit()
    assert author(cursor, 'potok-1')[:2] == (None, 'Admin')


def test_icore_authors_do_not_appear_in_the_manual_mapping_list(pg):
    connection, cursor, database, _ = pg
    module = source_cache.parse((ROOT / 'database.py').read_text(encoding='utf-8-sig'))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'Database')
    wanted = [n for n in cls.body
              if (isinstance(n, ast.FunctionDef) and n.name == 'list_wazzup_authors')
              or (isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == '_WAZZUP_ICORE_AUTHOR_LIKE'
                                                    for t in n.targets))]
    assert len(wanted) == 2
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name='Database', bases=[], keywords=[], body=wanted, decorator_list=[])], type_ignores=[])),
        'database.py', 'exec'), namespace)
    db = namespace['Database']()

    @contextmanager
    def get_cursor():
        with connection.cursor() as cur:
            yield cur
    db._get_cursor = get_cursor

    cursor.execute("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE wazzup_operator_map (author_id TEXT PRIMARY KEY, author_name TEXT, user_id INTEGER,
            is_bot BOOLEAN NOT NULL DEFAULT FALSE, updated_by INTEGER, updated_at TIMESTAMPTZ DEFAULT now(),
            account TEXT NOT NULL DEFAULT 'op');
        INSERT INTO users VALUES (10, 'Сарсеке Мерей');
        INSERT INTO wazzup_operator_map (author_id, author_name, user_id) VALUES
            ('icore:10', 'Сарсеке Мерей', 10), ('icore:99', 'Без сообщений', 10), ('12345678', 'Нуржан Перизат', 10);
    """)
    outbox(cursor, 'from-icore', 'icore:10')
    connection.commit()
    database().store_wazzup_messages([
        message('from-icore', authorName='Admin'),
        message('native', authorName='Нуржан Перизат', authorId='12345678'),
        message('unmapped', authorName='Новый Автор', authorId='87654321'),
    ])
    connection.commit()
    listed = {row['author_id'] for row in db.list_wazzup_authors('op')}
    assert listed == {'12345678', '87654321'}
