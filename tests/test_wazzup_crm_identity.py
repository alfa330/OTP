"""Авторы сообщений для CRM «Потока» (владелец, 20.10.2026: «нужно как-то заматчить учётки,
чтобы видеть авторов сообщений»).

Без приложения и внешнего HTTP; очередь пересылки — на одноразовом локальном Postgres
(WAZZUP_PILOT_TEST_PORT), как у остальных тестов пересылки.
"""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from flask import Blueprint, Flask

from tests.test_wazzup_pilot import user
from tests.test_wazzup_pilot_persistence import PORT, pg  # noqa: F401
from tests.test_wazzup_relay_routes import CRM_URL, clean_env, enable_crm  # noqa: F401
from tests.test_wazzup_syntony_persistence import Database, response
from wazzup import crm_identity, syntony, syntony_schema

HELP = '6983db13-ae8e-4b27-b09d-6c10ae4f54cf'
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
needs_pg = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')


def echo(message_id, seconds_ago=1, **changes):
    result = dict(messageId=message_id, channelId=HELP, chatType='whatsapp', chatId='77000000000',
                  dateTime=(NOW - timedelta(seconds=seconds_ago)).isoformat().replace('+00:00', 'Z'),
                  isEcho=True, type='text', text='Здравствуйте', status='sent', authorName='Admin')
    result.update(changes)
    return result


class RowsDb:
    """Отвечает на один запрос авторов заранее заданными строками."""

    def __init__(self, rows):
        self.rows, self.queries = rows, []

    @contextmanager
    def _get_cursor(self):
        db = self

        class Cursor:
            def execute(self, sql, params=()):
                db.queries.append((' '.join(sql.split()), params))

            def fetchall(self):
                return db.rows

        yield Cursor()


def test_identity_from_the_window_is_validated():
    assert crm_identity.clean_identity({'crmUserId': ' 352 ', 'crmUserName': '  Иванова   Айгерим '}) == (
        '352', 'Иванова Айгерим')
    assert crm_identity.clean_identity({'crmUserId': 352}) == ('352', None)
    for bad in ({}, {'crmUserId': ''}, {'crmUserId': 'a b'}, {'crmUserId': True}, {'crmUserId': 'x' * 65}, None, []):
        assert crm_identity.clean_identity(bad) is None, bad


def test_echo_of_an_icore_send_gets_the_crm_author_like_wazzup_did():
    db = RowsDb([('m-1', 42, 'Иванова Айгерим', '352', 'Айгерим (CRM)'),
                 ('m-2', 43, 'Карабек Ержан', None, None)])
    body = json.dumps({'messages': [echo('m-1'), echo('m-2'), echo('m-phone', authorId='wz-7', authorName='Сотрудник'),
                                    dict(echo('m-in'), isEcho=False)]}).encode()
    shaped, ready = crm_identity.attach_authors(db, body, now=NOW)
    assert ready
    messages = json.loads(shaped)['messages']
    assert (messages[0]['authorId'], messages[0]['authorName']) == ('352', 'Айгерим (CRM)')
    assert messages[0]['icoreAuthor'] == {'userId': 42, 'name': 'Иванова Айгерим'}
    # Без пары — имя сотрудника iCORE, id автора Wazzup не выдумываем.
    assert 'authorId' not in messages[1] and messages[1]['authorName'] == 'Карабек Ержан'
    assert messages[1]['icoreAuthor'] == {'userId': 43, 'name': 'Карабек Ержан'}
    # Отправленное из самого Wazzup и входящее не трогаем.
    assert messages[2]['authorId'] == 'wz-7' and 'icoreAuthor' not in messages[2]
    assert 'icoreAuthor' not in messages[3]
    assert db.queries[0][1] == (['m-1', 'm-2'],)


def test_fresh_echo_without_its_author_waits_an_old_one_goes_as_is():
    shaped, ready = crm_identity.attach_authors(RowsDb([]), json.dumps({'messages': [echo('m-1', 3)]}).encode(), now=NOW)
    assert not ready
    body = json.dumps({'messages': [echo('m-1', crm_identity.AUTHOR_WAIT_SECONDS + 1)]}).encode()
    shaped, ready = crm_identity.attach_authors(RowsDb([]), body, now=NOW)
    assert ready and json.loads(shaped)['messages'][0]['authorName'] == 'Admin'


def test_without_our_echoes_the_bytes_stay_untouched():
    db = RowsDb([])
    body = b'{"messages":[{"messageId":"in","isEcho":false}],"statuses":[]}'
    assert crm_identity.attach_authors(db, body) == (body, True)
    assert db.queries == []


def test_route_binds_the_signed_in_person_and_refuses_garbage():
    db = RowsDb([])
    bp = Blueprint('identity_test', __name__, url_prefix='/api/wazzup/pilot')
    crm_identity.register_crm_identity_route(bp, lambda: (user('operator'), None), lambda fn: fn,
                                             lambda: ('', 204), db)
    app = Flask(__name__)
    app.register_blueprint(bp)
    client = app.test_client()
    ok = client.post('/api/wazzup/pilot/crm-identity', json={'crmUserId': '352', 'crmUserName': 'Айгерим'})
    assert ok.status_code == 200 and ok.get_json()['crmUserId'] == '352'
    sql, params = db.queries[0]
    assert sql.startswith('INSERT INTO wazzup_crm_identities')
    assert params == (42, '352', 'Айгерим')
    assert client.post('/api/wazzup/pilot/crm-identity', json={'crmUserId': 'a b'}).status_code == 400
    assert len(db.queries) == 1


@pytest.fixture
def relay_db(pg, clean_env):  # noqa: F811
    connection, cursor, _, _ = pg
    # Схема пилота (вместе с очередью пересылки и парами CRM) уже развёрнута фикстурой.
    syntony_schema.init_schema(cursor)
    crm_identity.init_schema(cursor)
    cursor.execute('CREATE TABLE users (id BIGINT PRIMARY KEY, name TEXT)')
    connection.commit()
    clean_env.setattr(syntony, 'start_worker', lambda *_args, **_kwargs: None)
    return Database(connection)


@needs_pg
def test_relay_waits_for_the_author_and_then_sends_it(relay_db, clean_env):  # noqa: F811
    enable_crm(clean_env)
    message = echo('m-race', 0)
    message['dateTime'] = datetime.now(timezone.utc).isoformat()
    assert syntony.enqueue(relay_db, {'messages': [message]}) == 1
    sent = []

    def send(url, **kwargs):
        sent.append(json.loads(kwargs['data']))
        return response()

    # Эхо пришло раньше ответа Wazzup на отправку: автора ещё нет — ждём, не отправляя.
    assert syntony.process_once(relay_db, transport=send)
    assert sent == []
    with relay_db._get_cursor() as cursor:
        cursor.execute("SELECT phase, last_error FROM wazzup_syntony_outbox WHERE phase <> 'receipt'")
        assert cursor.fetchall() == [('ready', 'awaiting_author')]
        cursor.execute("INSERT INTO users VALUES (42, 'Иванова Айгерим')")
        cursor.execute("""INSERT INTO wazzup_pilot_outbox (request_id, account, channel_id, chat_id, chat_type,
                          text, user_id, author_name, state, message_id)
                          VALUES (gen_random_uuid(), 'op', %s, '77000000000', 'whatsapp', 'Здравствуйте', 42,
                                  'Иванова Айгерим', 'sent', 'm-race')""", (HELP,))
        cursor.execute("INSERT INTO wazzup_crm_identities (user_id, crm_user_id, crm_user_name) VALUES (42, '352', NULL)")
        cursor.execute("UPDATE wazzup_syntony_outbox SET next_attempt_at = now() - interval '1 second'")
    assert syntony.process_once(relay_db, transport=send)
    assert len(sent) == 1
    delivered = sent[0]['messages'][0]
    assert (delivered['authorId'], delivered['authorName']) == ('352', 'Иванова Айгерим')
    assert delivered['icoreAuthor'] == {'userId': 42, 'name': 'Иванова Айгерим'}
