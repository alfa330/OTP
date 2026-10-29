"""Вход в окно «Чатов ОП» в CRM без пароля (владелец, 20.10.2026: по ФИО среди всех, у кого
совпадает, иначе один вход; админские роли тоже; без QR из CRM; ключ — CRM_WEBHOOK_TOKEN).
С 23.10.2026 в окне CRM пишет каждый, кого CRM открыла («не только потоку»): ссылку получает
любая действующая учётка, нашедшаяся по правилам, а сессия помечается сессией из CRM.

Без приложения и сети: поддельная база; очередь кодов — на одноразовом локальном Postgres.
"""
import time
import uuid
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask, jsonify

from tests.test_wazzup_pilot_persistence import PORT, pg  # noqa: F401
from wazzup import access, crm_identity, embed_auth

KEY = 'synthetic-crm-key-0000000000000000'
BASE = 'https://portal.example.test/OTP'
needs_pg = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')


def user_row(user_id, name='Иванова Айгерим', role='operator', status='working'):
    row = [None] * 20
    row[0], row[2], row[3], row[11] = user_id, name, role, status
    return tuple(row)


class FakeDb:
    """Отвечает на запросы входа из CRM по началу SQL; всё записывает."""

    def __init__(self, *, binding=None, users=(), consumed=None, pairs=None):
        self.binding, self.users, self.consumed = binding, list(users), consumed
        self.pairs = dict(pairs or {})  # учётка → пользователь CRM, за которым она закреплена
        self.queries, self.launches, self.binds = [], [], []

    @contextmanager
    def _get_cursor(self):
        db = self

        class Cursor:
            row, rows = None, []

            def execute(self, sql, params=()):
                text = ' '.join(sql.split())
                db.queries.append((text, params))
                self.row, self.rows = None, []
                if 'FROM wazzup_crm_identities i JOIN users u' in text:
                    self.row = (db.binding,) if db.binding is not None else None
                elif text.startswith('SELECT id, name, role FROM users'):
                    self.rows = [(u['id'], u['name'], u.get('role', 'operator')) for u in db.users]
                elif text.startswith('SELECT crm_user_id FROM wazzup_crm_identities WHERE user_id'):
                    pair = db.pairs.get(params[0])
                    self.row = (pair,) if pair is not None else None
                elif text.startswith('INSERT INTO wazzup_embed_launches'):
                    db.launches.append(params)
                elif text.startswith('INSERT INTO wazzup_crm_identities'):
                    db.binds.append(params)
                elif text.startswith('UPDATE wazzup_embed_launches SET used_at'):
                    self.row = db.consumed

            def fetchone(self):
                return self.row

            def fetchall(self):
                return self.rows

        yield Cursor()


def app_for(db, *, users=None, issued=None):
    users = users if users is not None else {}
    issued = issued if issued is not None else []

    def issue(user, transport, crm_user_id):
        issued.append((user[0], transport, crm_user_id))
        return jsonify(status='success', access_token='a', refresh_token='r'), 200

    app = Flask(__name__)
    app.register_blueprint(embed_auth.build_embed_blueprint(
        db=db, preflight=lambda: ('', 204), portal_base_url=BASE,
        load_user=lambda user_id: users.get(user_id),
        issue_session=issue, client_ip=lambda: '203.0.113.7'))
    return app.test_client()


def launch(client, body, key=KEY):
    headers = {'Authorization': f'Bearer {key}'} if key is not None else {}
    return client.post('/api/wazzup/embed/launch', json=body, headers=headers)


@pytest.fixture
def key_env(monkeypatch):
    monkeypatch.setenv(embed_auth.KEY_ENV, KEY)
    return monkeypatch


# ── чистые правила ──────────────────────────────────────────────────────────

def test_name_match_ignores_word_order_kazakh_letters_and_one_sided_patronymic():
    users = [{'id': 1, 'name': 'Аскаров Әділжан'}, {'id': 2, 'name': 'Иванова Айгерим Сериковна'},
             {'id': 3, 'name': 'Карабек Ержан'}]
    assert [u['id'] for u in embed_auth.name_matches('Адильжан Аскаров', users)] == [1]
    assert [u['id'] for u in embed_auth.name_matches('айгерим иванова', users)] == [2]
    assert [u['id'] for u in embed_auth.name_matches('Карабек Ержан Болатович', users)] == [3]
    assert embed_auth.name_matches('Ержан', users) == [], 'одно слово — не ФИО'
    assert embed_auth.name_matches('', users) == []
    twins = users + [{'id': 4, 'name': 'Иванова Айгерим Болатовна'}]
    assert [u['id'] for u in embed_auth.name_matches('Иванова Айгерим', twins)] == [2, 4]


def test_window_session_reaches_only_chats_and_signing_in_and_out():
    for path in ('/api/wazzup/chats', '/api/wazzup/pilot/send', '/api/auth/me', '/api/auth/refresh',
                 '/api/logout', '/api/login', '/api/auth/embed-launch'):
        assert embed_auth.scope_refusal(embed_auth.SCOPE, path) is None, path
    for path in ('/api/admin/users', '/api/user/profile', '/api/wiki/articles', '/api/auth/sessions',
                 '/api/wazzupX', '/api/hours/send_request'):
        assert embed_auth.scope_refusal(embed_auth.SCOPE, path) == embed_auth.SCOPE_REFUSAL, path
    assert embed_auth.scope_refusal(None, '/api/admin/users') is None, 'обычной сессии ограничения нет'


def test_a_partner_key_opens_the_launch_and_is_journaled(monkeypatch):
    """Второй источник ключа — «Сессии → Ключи» с областью «Окно „Чатов ОП“ в CRM»
    (partner_keys): ключ из окружения не обязателен, вызов ложится в журнал ключа."""
    from partner_keys import auth as partner_auth, journal as partner_journal

    monkeypatch.delenv(embed_auth.KEY_ENV, raising=False)
    partner = {'id': 7, 'name': 'CRM', 'mark': 'abcd1234', 'scopes': ['crm_embed']}
    recorded = []

    def lookup(db, presented, *, scope):
        assert scope == 'crm_embed'
        if presented == 'otpk_partner':
            return partner_auth.OK, partner
        if presented == 'otpk_other-door':
            return partner_auth.NO_SCOPE, partner
        return partner_auth.UNKNOWN, None

    monkeypatch.setattr(partner_auth, 'lookup', lookup)
    monkeypatch.setattr(partner_journal, 'record', lambda db, key, **fields: recorded.append((key, fields)))
    db = FakeDb(binding=10, users=[{'id': 10, 'name': 'Иван'}])
    client = app_for(db, users={10: (10, 'ivan', 'Иван', 'operator')})
    response = launch(client, {'crmUserId': '352', 'crmUserName': 'Иван'}, key='otpk_partner')
    assert response.status_code == 200
    assert response.get_json()['linked'] is True
    key, fields = recorded[0]
    assert key is partner
    assert (fields['scope'], fields['outcome'], fields['details']['crm_user_id'],
            fields['details']['icore_user_id']) == ('crm_embed', 'ok', '352', 10)
    assert launch(client, {'crmUserId': '352'}, key='otpk_other-door').status_code == 403
    assert launch(client, {'crmUserId': '352'}, key='otpk_stranger').status_code == 401
    assert client.post('/api/wazzup/embed/launch', json={'crmUserId': '352'},
                       headers={'X-Api-Key': 'otpk_partner'}).status_code == 200


def test_key_is_bearer_and_compared_whole():
    assert embed_auth.key_matches(f'Bearer {KEY}', KEY)
    assert embed_auth.key_matches(f'bearer {KEY}', KEY)
    assert not embed_auth.key_matches(f'Bearer {KEY}x', KEY)
    assert not embed_auth.key_matches('', KEY)
    assert not embed_auth.key_matches(f'Bearer {KEY}', '')


def test_launch_url_carries_the_code_only_for_a_found_account_and_always_our_signature():
    with_code = parse_qs(urlsplit(embed_auth.launch_url(BASE, crm_user_id='352', crm_user_name='Иван',
                                                        code='c' * 43, user_id=42, key=KEY, now=1_790_000_000)).query)
    exp = 1_790_000_000 + crm_identity.IDENTITY_TTL_SECONDS
    assert with_code == {'view': ['wazzup_chats'], 'embed': ['1'], 'launch': ['c' * 43], 'uid': ['42'],
                         'crmUserId': ['352'], 'crmUserName': ['Иван'], 'crmExp': [str(exp)],
                         'crmSig': [crm_identity.sign_identity(KEY, '352', 'Иван', exp)]}
    assert crm_identity.identity_signed(KEY, '352', 'Иван', exp, with_code['crmSig'][0], now=1_790_000_000)
    without = urlsplit(embed_auth.launch_url(BASE, crm_user_id='352', key=KEY))
    query = parse_qs(without.query)
    assert without.path == '/OTP/' and 'launch' not in query and 'crmSig' in query


def test_rate_limiter_counts_per_key_inside_the_window():
    now = [100.0]
    limiter = embed_auth.RateLimiter(2, window=60, clock=lambda: now[0])
    assert limiter.allow('a') and limiter.allow('a') and not limiter.allow('a')
    assert limiter.allow('b')
    now[0] += 60
    assert limiter.allow('a')


# ── ссылка для CRM ──────────────────────────────────────────────────────────

def test_launch_needs_the_configured_key(monkeypatch):
    monkeypatch.delenv(embed_auth.KEY_ENV, raising=False)
    assert launch(app_for(FakeDb()), {'crmUserId': '352'}).status_code == 503
    monkeypatch.setenv(embed_auth.KEY_ENV, KEY)
    client = app_for(FakeDb())
    assert launch(client, {'crmUserId': '352'}, key='wrong').status_code == 401
    assert launch(client, {'crmUserId': '352'}, key=None).status_code == 401
    assert launch(client, {'crmUserId': 'a b'}).status_code == 400


def test_known_pair_gets_a_one_time_link(key_env):
    db = FakeDb(binding=42)
    response = launch(app_for(db, users={42: user_row(42)}),
                      {'crmUserId': '352', 'crmUserName': 'Айгерим'})
    data = response.get_json()
    assert (response.status_code, data['linked'], data['matchedBy']) == (200, True, 'binding')
    query = parse_qs(urlsplit(data['url']).query)
    assert query['uid'] == ['42'] and len(query['launch'][0]) >= 40
    code_hash, user_id, crm_user_id, ttl = db.launches[0]
    assert (user_id, crm_user_id, ttl) == (42, '352', embed_auth.LAUNCH_TTL_SECONDS)
    assert code_hash == embed_auth.hash_code(query['launch'][0]), 'в базе — только отпечаток кода'
    assert data['icoreUser'] == {'id': 42, 'name': 'Иванова Айгерим'}
    assert not db.binds


def test_name_finds_any_role_including_admins_and_remembers_the_pair(key_env):
    db = FakeDb(users=[{'id': 7, 'name': 'Аскаров Адильжан', 'role': 'admin'}, {'id': 8, 'name': 'Карабек Ержан'}])
    data = launch(app_for(db, users={7: user_row(7, 'Аскаров Адильжан', 'admin')}),
                  {'crmUserId': '500', 'crmUserName': 'Адильжан Аскаров'}).get_json()
    assert (data['linked'], data['matchedBy'], data['icoreUser']['id']) == (True, 'name', 7)
    assert db.binds == [(7, '500', 'Адильжан Аскаров')]
    assert db.launches and db.launches[0][1] == 7


def test_an_account_without_the_section_in_the_portal_still_gets_the_window(key_env):
    # «Всем кто есть в crm… не только потоку» (владелец, 23.10.2026): учётке, которой «Чаты ОП»
    # в портале не открыты (здесь — бухгалтер), окно CRM всё равно открывается — писать в нём
    # она будет в режиме «crm» (access.py). Раздел портала тут не спрашивается вовсе.
    db = FakeDb(users=[{'id': 31, 'name': 'Ахметова Алия Маратовна', 'role': 'accounting_manager'}])
    data = launch(app_for(db, users={31: user_row(31, 'Ахметова Алия Маратовна', 'accounting_manager')}),
                  {'crmUserId': 777, 'crmUserName': 'Ахметова Алия'}).get_json()
    assert (data['linked'], data['matchedBy'], data['icoreUser']['id']) == (True, 'name', 31)
    assert db.binds == [(31, '777', 'Ахметова Алия')], 'числовой id CRM — строкой'


@pytest.mark.parametrize('users,reason', [
    ([], 'NOT_FOUND'),
    ([{'id': 1, 'name': 'Иванова Айгерим Сериковна'}, {'id': 2, 'name': 'Иванова Айгерим Болатовна'}], 'AMBIGUOUS'),
])
def test_without_a_single_account_the_window_asks_to_sign_in_once(key_env, users, reason):
    db = FakeDb(users=users)
    data = launch(app_for(db), {'crmUserId': '352', 'crmUserName': 'Иванова Айгерим'}).get_json()
    assert (data['linked'], data['reason']) == (False, reason)
    query = parse_qs(urlsplit(data['url']).query)
    assert 'launch' not in query and query['crmUserId'] == ['352']
    # После входа паролем окно запомнит пару — по этой подписи.
    assert crm_identity.identity_signed(KEY, '352', 'Иванова Айгерим', query['crmExp'][0], query['crmSig'][0])
    assert not db.launches and not db.binds


def test_a_namesake_does_not_get_an_account_paired_with_another_crm_user(key_env):
    # Учётка с этим ФИО уже закреплена за пользователем CRM 352: второй аккаунт CRM с тем же
    # именем (тёзка, дубль, тестовый) получает вход паролем, а пара 352 остаётся как была.
    users = [{'id': 42, 'name': 'Иванова Айгерим Сериковна'}]
    db = FakeDb(users=users, pairs={42: '352'})
    data = launch(app_for(db, users={42: user_row(42)}),
                  {'crmUserId': '777', 'crmUserName': 'Иванова Айгерим'}).get_json()
    assert (data['linked'], data['reason']) == (False, 'TAKEN')
    assert not db.launches and not db.binds


def test_a_known_pair_wins_over_the_name(key_env):
    # Пара — от входа в окне или прежнего совпадения: держится, пока учётка действующая
    # (user_for_crm), даже если ФИО в CRM сменили или оно теперь совпадает с другим.
    db = FakeDb(binding=42, users=[{'id': 9, 'name': 'Карабек Ержан'}])
    data = launch(app_for(db, users={42: user_row(42)}), {'crmUserId': '352', 'crmUserName': 'Ержан Карабек'}).get_json()
    assert (data['matchedBy'], data['icoreUser']['id']) == ('binding', 42)
    assert not db.binds


def test_launch_is_rate_limited_per_crm_user(key_env):
    db = FakeDb(binding=42)
    client = app_for(db, users={42: user_row(42)})
    codes = [launch(client, {'crmUserId': '352'}).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
    assert launch(client, {'crmUserId': '353'}).status_code == 200


# ── обмен кода на сессию ────────────────────────────────────────────────────

def exchange(client, code='x' * 43):
    return client.post('/api/auth/embed-launch', json={'code': code, 'auth_transport': 'bearer'})


def test_exchange_opens_the_window_session_only_for_a_live_code():
    issued = []
    client = app_for(FakeDb(consumed=(42, '352')), users={42: user_row(42)}, issued=issued)
    response = exchange(client)
    # Сессия выдаётся вместе с пользователем CRM кода — она будет помечена сессией из CRM.
    assert response.status_code == 200 and issued == [(42, 'bearer', '352')]
    assert exchange(app_for(FakeDb(consumed=None))).status_code == 401
    assert exchange(app_for(FakeDb()), code='').status_code == 400
    assert exchange(app_for(FakeDb()), code='y' * 101).status_code == 400


def test_exchange_refuses_dismissed_accounts_only():
    fired = app_for(FakeDb(consumed=(42, '352')), users={42: user_row(42, status='fired')})
    assert (exchange(fired).status_code, exchange(fired).get_json()['code']) == (403, 'USER_INACTIVE')
    issued = []
    clerk = app_for(FakeDb(consumed=(31, '777')), users={31: user_row(31, role='accounting_manager')}, issued=issued)
    assert exchange(clerk).status_code == 200 and issued == [(31, 'bearer', '777')], 'без раздела в портале — тоже'


def test_password_sign_in_counts_as_a_crm_window_only_with_our_signature(key_env):
    exp = int(time.time()) + 600
    signed = {'crmUserId': '352', 'crmUserName': 'Иван', 'crmExp': str(exp),
              'crmSig': crm_identity.sign_identity(KEY, '352', 'Иван', exp)}
    assert embed_auth.signed_crm_user(signed) == '352'
    assert embed_auth.signed_crm_user(dict(signed, crmUserId='353')) is None, 'чужой пользователь CRM'
    assert embed_auth.signed_crm_user(dict(signed, crmSig='0' * 64)) is None
    assert embed_auth.signed_crm_user({'crmUserId': '352', 'crmUserName': 'Иван'}) is None, 'без подписи'
    for junk in (None, [], 'icore', {}):
        assert embed_auth.signed_crm_user(junk) is None, junk
    key_env.delenv(embed_auth.KEY_ENV)
    assert embed_auth.signed_crm_user(signed) is None, 'без ключа CRM окна из CRM нет'


def test_exchange_is_rate_limited_per_ip():
    client = app_for(FakeDb(consumed=None))
    codes = [exchange(client).status_code for _ in range(61)]
    assert codes[:60] == [401] * 60 and codes[60] == 429


# ── на настоящем Postgres ───────────────────────────────────────────────────

class Database:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def _get_cursor(self):
        with self.connection.cursor() as cursor:
            yield cursor
        self.connection.commit()


@pytest.fixture
def real(pg):  # noqa: F811
    connection, cursor, _, _ = pg
    cursor.execute("CREATE TABLE users (id BIGINT PRIMARY KEY, name TEXT, role TEXT, status TEXT)")
    cursor.execute("INSERT INTO users VALUES (42, 'Иванова Айгерим', 'operator', 'working'),"
                   " (43, 'Карабек Ержан', 'admin', 'working'), (44, 'Тестова Уволенная', 'operator', 'fired')")
    crm_identity.init_schema(cursor)
    embed_auth.init_schema(cursor)
    connection.commit()
    return Database(connection)


@needs_pg
def test_code_is_single_use_and_expires(real):
    with real._get_cursor() as cursor:
        code = embed_auth.create_launch(cursor, 42, '352')
        stale = embed_auth.create_launch(cursor, 42, '352', ttl=-1)
        cursor.execute('SELECT code_hash FROM wazzup_embed_launches')
        stored = {row[0] for row in cursor.fetchall()}
    assert code not in stored and embed_auth.hash_code(code) in stored
    with real._get_cursor() as cursor:
        assert embed_auth.consume_launch(cursor, code) == (42, '352')
        assert embed_auth.consume_launch(cursor, code) is None, 'второй раз код не открывает'
        assert embed_auth.consume_launch(cursor, stale) is None, 'истёкший код не открывает'
        assert embed_auth.consume_launch(cursor, 'made-up') is None


@needs_pg
def test_pair_is_one_to_one_and_dismissed_accounts_are_not_found(real):
    with real._get_cursor() as cursor:
        crm_identity.bind(cursor, 42, '352', 'Айгерим')
        assert crm_identity.user_for_crm(cursor, '352') == 42
        crm_identity.bind(cursor, 43, '352', 'Ержан')
        cursor.execute("SELECT user_id FROM wazzup_crm_identities WHERE crm_user_id = '352'")
        assert cursor.fetchall() == [(43,)], 'пользователя CRM у прежней учётки сняли'
        assert (crm_identity.pair_of(cursor, 43), crm_identity.pair_of(cursor, 42)) == ('352', None)
        crm_identity.bind(cursor, 44, '999', None)
        assert crm_identity.user_for_crm(cursor, '999') is None
        cursor.execute(embed_auth.ACTIVE_USERS_SQL)
        assert sorted(row[0] for row in cursor.fetchall()) == [42, 43]


@needs_pg
def test_crm_window_session_is_marked_and_its_lock_is_the_link_or_the_qr(real):
    """Режим «crm» (access.py) держится на пометке сессии и её замке: ссылка CRM открывает
    сразу (wazzup_chat_access), вход паролем — после обычного QR (sensitive_data_unlocked)."""
    link, password, portal, revoked = (str(uuid.uuid4()) for _ in range(4))
    with real._get_cursor() as cursor:
        # Таблицы портала — в том виде, в каком их читает проверка (database.py их и создаёт).
        cursor.execute("""
            CREATE TABLE user_sessions (session_id UUID PRIMARY KEY, user_id BIGINT NOT NULL,
                revoked_at TIMESTAMP, expires_at TIMESTAMP NOT NULL,
                sensitive_data_unlocked BOOLEAN NOT NULL DEFAULT FALSE);
            CREATE TABLE wazzup_chat_access (session_id UUID PRIMARY KEY, user_id BIGINT NOT NULL,
                revoked_at TIMESTAMPTZ);
        """)
        for session_id, revoked_at in ((link, None), (password, None), (portal, None), (revoked, 'now')):
            cursor.execute("""INSERT INTO user_sessions (session_id, user_id, revoked_at, expires_at)
                              VALUES (%s, 43, CASE WHEN %s IS NULL THEN NULL ELSE now() END,
                                      now() + interval '30 days')""", (session_id, revoked_at))
        cursor.execute("INSERT INTO wazzup_chat_access (session_id, user_id) VALUES (%s, 43), (%s, 43)",
                       (link, revoked))
        embed_auth.mark_crm_session(cursor, link, 43, '352', 'launch')
        embed_auth.mark_crm_session(cursor, link, 43, '352', 'launch')  # повтор — без ошибки
        embed_auth.mark_crm_session(cursor, password, 43, '352', 'password')
        embed_auth.mark_crm_session(cursor, revoked, 43, '352', 'launch')
    with real._get_cursor() as cursor:
        state = lambda session_id, user_id=43: access.load_crm_window_state(cursor, user_id, session_id)
        assert state(link) == {'crm': True, 'unlocked': True}, 'по ссылке — сразу'
        assert state(password) == {'crm': True, 'unlocked': False}, 'паролем — до QR закрыто'
        cursor.execute("UPDATE user_sessions SET sensitive_data_unlocked = TRUE WHERE session_id = %s", (password,))
        assert state(password) == {'crm': True, 'unlocked': True}, 'QR открыл'
        assert state(portal) == {'crm': False, 'unlocked': False}, 'сессия портала — не окно CRM'
        assert state(revoked) == {'crm': True, 'unlocked': False}, 'отозванная не пишет'
        assert state(link, user_id=42) == {'crm': False, 'unlocked': False}, 'чужая сессия'
        assert state(None) == state('None') == {'crm': False, 'unlocked': False}
        cursor.execute("SELECT via FROM wazzup_crm_sessions WHERE session_id = %s", (password,))
        assert cursor.fetchone() == ('password',)
        with pytest.raises(Exception):
            embed_auth.mark_crm_session(cursor, str(uuid.uuid4()), 43, '352', 'guess')
