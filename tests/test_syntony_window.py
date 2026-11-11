"""Окно Syntony в iCORE (ТЗ Syntony от 09.11.2026, решения владельца 11.11.2026).

Что держат тесты — по пунктам ТЗ и решений:

- ключ Syntony не выходит за сервер: ни в ответах браузеру, ни в журнале;
- оператор — только из сессии: id, ФИО и почту страница подставить не может,
  canWrite не уходит никогда;
- ответ Syntony отдаётся как есть, ссылка каждый раз запрашивается заново;
- окно — только людям из списка доступа (и супер-админу тоже), рядовым — после QR;
- список ведут супер-админы; добавленного сервер сам записывает у Syntony.

SQL здесь не исполняется: ручки ходят в подставной склад (store). Настоящие запросы
и схема — в tests/test_syntony_window_postgres.py.
"""

import contextlib
import io
import logging
import re
import unittest
from datetime import datetime
from pathlib import Path

import requests
from openpyxl import load_workbook

from syntony_window import access, client, report, rules

ROOT = Path(__file__).resolve().parents[1]
KEY = 'sk_live_' + 'testkeyNEVERleaves0123456789'
WINDOW_URL = 'https://app.syntony.kz/ru/embed/one-time-code'


# ── Правила запроса ───────────────────────────────────────────────────────────

class WindowRequestRulesTests(unittest.TestCase):
    def test_four_windows_are_known_and_inbox_is_the_default(self):
        for scope in ('inbox', 'leads'):
            window, error = rules.clean_window_request({'scope': scope})
            self.assertIsNone(error)
            self.assertEqual(window, {'scope': scope, 'phones': [], 'theme': 'light'})
        window, error = rules.clean_window_request({})
        self.assertEqual(window['scope'], 'inbox')
        window, error = rules.clean_window_request(None)
        self.assertEqual(window['scope'], 'inbox')

    def test_unknown_window_is_an_invalid_request(self):
        for payload in ({'scope': 'admin'}, {'scope': None}, {'scope': ['inbox']}, 'inbox', []):
            window, error = rules.clean_window_request(payload)
            self.assertIsNone(window)
            self.assertEqual(rules.error_code(error), 'invalid_request')

    def test_driver_window_needs_a_number_and_picks_phone_or_phones_itself(self):
        window, error = rules.clean_window_request({'scope': 'phones', 'phones': ['+7 701 019 02 19']})
        self.assertEqual((window['scope'], window['phones']), ('phone', ['+7 701 019 02 19']))
        window, error = rules.clean_window_request(
            {'scope': 'phone', 'phones': ['+7 701 019 02 19', '8 (702) 555-44-33']})
        self.assertEqual(window['scope'], 'phones')
        self.assertEqual(len(window['phones']), 2)
        for phones in (None, [], ['abc'], [''], [123], 'нет'):
            window, error = rules.clean_window_request({'scope': 'phone', 'phones': phones})
            self.assertIsNone(window)
            self.assertEqual(rules.error_code(error), 'invalid_request')

    def test_numbers_are_not_sent_with_chats_and_leads(self):
        window, _ = rules.clean_window_request({'scope': 'inbox', 'phones': ['+77010190219']})
        self.assertEqual(window['phones'], [])
        body = rules.build_request_body(window, user_id=7, name='А', email='a@b.kz')
        self.assertNotIn('phones', body)

    def test_same_number_written_twice_goes_once_and_ten_is_the_ceiling(self):
        self.assertEqual(rules.clean_phones(['+7 701 019 02 19', '87010190219', '7 (701) 019-02-19']),
                         ['+7 701 019 02 19'])
        many = ['+7701000%04d' % index for index in range(25)]
        self.assertEqual(len(rules.clean_phones(many)), rules.MAX_PHONES)

    def test_theme_is_light_unless_dark_is_asked(self):
        for raw, expected in (('dark', 'dark'), ('light', 'light'), ('neon', 'light'), (None, 'light')):
            window, _ = rules.clean_window_request({'scope': 'inbox', 'theme': raw})
            self.assertEqual(window['theme'], expected)

    def test_page_cannot_name_the_operator_or_send_can_write(self):
        window, error = rules.clean_window_request({
            'scope': 'inbox', 'partnerUserId': '1', 'partnerUserName': 'Чужой',
            'partnerUserEmail': 'boss@example.kz', 'canWrite': False, 'locale': 'en'})
        self.assertIsNone(error)
        self.assertEqual(set(window), {'scope', 'phones', 'theme'})
        body = rules.build_request_body(window, user_id=9126, name='Айгерим Сапарова', email='AIGERIM@example.kz ')
        self.assertEqual(body, {
            'scope': 'inbox', 'partnerUserId': '9126', 'partnerUserName': 'Айгерим Сапарова',
            'partnerUserEmail': 'aigerim@example.kz', 'theme': 'light', 'locale': 'ru'})
        self.assertNotIn('canWrite', body)

    def test_operator_name_is_one_printable_line_of_at_most_100(self):
        self.assertEqual(rules.clean_operator_name('Иванов\nИван\t​Иванович\x00'), 'Иванов Иван Иванович')
        self.assertEqual(len(rules.clean_operator_name('Я' * 300)), 100)
        self.assertEqual(rules.clean_operator_name(None), '')
        body = rules.build_request_body(rules.registration_window(), user_id=5, name='\n', email='')
        self.assertNotIn('partnerUserName', body)
        self.assertNotIn('partnerUserEmail', body)

    def test_email_is_an_address_or_nothing(self):
        self.assertEqual(rules.clean_email('  Aigerim.S@Example.KZ '), 'aigerim.s@example.kz')
        too_long = 'x' * (rules.EMAIL_MAX_LENGTH - 4) + '@b.kz'
        for bad in ('', None, 'нет', 'a@b', 'a b@c.kz', 'a@@b.kz', '@b.kz', 'a@b.', too_long):
            self.assertEqual(rules.clean_email(bad), '', bad)

    def test_registration_asks_for_chats_window(self):
        self.assertEqual(rules.registration_window()['scope'], 'inbox')

    def test_reply_tells_the_link_state_only_when_it_is_about_the_link(self):
        self.assertEqual(rules.link_state_of(200, {'url': WINDOW_URL}), 'linked')
        self.assertEqual(rules.link_state_of(409, rules.error_payload('operator_not_linked', '…')), 'not_linked')
        for status, code in ((401, 'invalid_key'), (403, 'connection_inactive'), (503, 'embed_disabled'),
                             (502, rules.CODE_UNREACHABLE), (429, 'rate_limited')):
            self.assertIsNone(rules.link_state_of(status, rules.error_payload(code, '…')))
        self.assertIsNone(rules.link_state_of(200, {'url': ''}))


# ── Права ─────────────────────────────────────────────────────────────────────

def ctx(role='operator', *, granted=False, status='working', is_head=False):
    return {'user_id': 1, 'role': role, 'status': status, 'granted': granted, 'is_head': is_head}


class AccessRulesTests(unittest.TestCase):
    def test_only_super_admin_keeps_the_list(self):
        self.assertTrue(access.can_manage(ctx('super_admin')))
        for role in ('admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee', 'hr_manager', ''):
            self.assertFalse(access.can_manage(ctx(role, granted=True)), role)
        self.assertFalse(access.can_manage(ctx('super_admin', status='fired')))

    def test_window_goes_only_to_people_on_the_list_super_admin_included(self):
        self.assertFalse(access.can_request_window(ctx('super_admin')))
        self.assertTrue(access.can_request_window(ctx('super_admin', granted=True)))
        self.assertTrue(access.can_request_window(ctx('operator', granted=True)))
        self.assertFalse(access.can_request_window(ctx('admin')))
        self.assertFalse(access.can_request_window(ctx('operator', granted=True, status='fired')))
        self.assertFalse(access.can_request_window(ctx('operator', granted=True, status='dismissal')))

    def test_menu_is_for_the_list_and_for_who_keeps_it(self):
        self.assertTrue(access.can_open_section(ctx('super_admin')))
        self.assertTrue(access.can_open_section(ctx('operator', granted=True)))
        self.assertFalse(access.can_open_section(ctx('operator')))
        self.assertFalse(access.can_open_section(ctx('admin')))

    def test_rank_and_file_need_qr_and_managers_do_not(self):
        for role in ('operator', 'trainee', 'accounting_manager', 'marketing_manager'):
            self.assertTrue(access.requires_qr(ctx(role)), role)
            self.assertFalse(access.requires_qr(ctx(role, is_head=True)), role)
        for role in ('sv', 'supervisor', 'trainer', 'admin', 'super_admin', 'superadmin', 'hr_manager'):
            self.assertFalse(access.requires_qr(ctx(role)), role)

    def test_qr_is_asked_exactly_from_those_the_portal_can_issue_it_to(self):
        # Шире — человек упёрся бы в замок, который ему нечем открыть; уже — рядовой
        # с выданным порталом QR открывал бы переписку без подтверждения.
        from driver_chats.access import QR_GATED_ROLES as driver_chats_roles
        from wiki.access import QR_GATED_ROLES as wiki_roles
        self.assertEqual(access.QR_GATED_ROLES, frozenset(wiki_roles) | frozenset(driver_chats_roles))


# ── Разговор с Syntony ────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status, payload=None, raw=None):
        self.status_code = status
        self._payload = payload
        self.content = raw if raw is not None else b'{}'

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


class Recorder:
    """Подмена requests.post: копит вызовы и отвечает по очереди."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({'url': url, **kwargs})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


CONFIG = client.Config(url=client.DEFAULT_URL, key=KEY)
BODY = {'scope': 'inbox', 'partnerUserId': '7'}


class ClientTests(unittest.TestCase):
    def test_key_comes_only_from_the_environment(self):
        self.assertIsNone(client.load_config({}))
        self.assertIsNone(client.load_config({client.ENV_KEY: '   '}))
        self.assertIsNone(client.load_config({client.ENV_KEY: 'sk live with space'}))
        config = client.load_config({client.ENV_KEY: ' ' + KEY + '\n'})
        self.assertEqual((config.url, config.key), ('https://app.syntony.kz/v1/iframe', KEY))
        self.assertNotIn(KEY, repr(config))

    def test_address_must_be_https_on_the_syntony_domain(self):
        for url in ('http://app.syntony.kz/v1/iframe', 'https://evil.example/v1/iframe',
                    'https://app.syntony.kz.evil.example/v1/iframe', 'https://user:pw@app.syntony.kz/v1/iframe',
                    'https://app.syntony.kz:8443/v1/iframe', 'ftp://app.syntony.kz/x'):
            self.assertIsNone(client.load_config({client.ENV_KEY: KEY, client.ENV_URL: url}), url)
        self.assertIsNotNone(client.load_config(
            {client.ENV_KEY: KEY, client.ENV_URL: 'https://staging.syntony.kz/v1/iframe'}))

    def test_request_carries_the_key_as_bearer_and_never_follows_redirects(self):
        post = Recorder(FakeResponse(200, {'url': WINDOW_URL, 'expiresAt': '2026-11-11T09:12:00Z'}))
        reply = client.request_window(CONFIG, BODY, post=post)
        call = post.calls[0]
        self.assertEqual(call['url'], 'https://app.syntony.kz/v1/iframe')
        self.assertEqual(call['headers']['Authorization'], 'Bearer ' + KEY)
        self.assertEqual(call['json'], BODY)
        self.assertIs(call['allow_redirects'], False)
        self.assertEqual(call['timeout'], (client.CONNECT_TIMEOUT_SECONDS, client.READ_TIMEOUT_SECONDS))
        self.assertEqual((reply.status, reply.body),
                         (200, {'url': WINDOW_URL, 'expiresAt': '2026-11-11T09:12:00Z'}))

    def test_syntony_error_is_passed_as_is_with_its_status(self):
        for status, code in ((400, 'invalid_request'), (401, 'invalid_key'), (403, 'connection_inactive'),
                             (409, 'operator_not_linked'), (422, 'transport_not_connected'),
                             (503, 'operator_lookup_unavailable'), (418, 'code_of_tomorrow')):
            payload = {'error': {'code': code, 'message': 'Текст Syntony', 'details': {'retryAfter': 3}}}
            reply = client.request_window(CONFIG, BODY, post=Recorder(FakeResponse(status, payload)))
            self.assertEqual((reply.status, reply.body, reply.code), (status, payload, code))

    def test_silence_and_garbage_become_one_unreachable_error(self):
        cases = (
            requests.Timeout('slow'),
            requests.ConnectionError('down'),
            FakeResponse(502, None, raw=b'<html>Bad gateway</html>'),
            FakeResponse(200, ['not', 'an', 'object']),
            FakeResponse(200, {'expiresAt': 'x'}),                       # успех без ссылки
            FakeResponse(500, {'message': 'oops'}),                      # ошибка без кода
            FakeResponse(200, {'url': WINDOW_URL}, raw=b'x' * (client.MAX_REPLY_BYTES + 1)),
        )
        for case in cases:
            reply = client.request_window(CONFIG, BODY, post=Recorder(case))
            self.assertEqual((reply.status, reply.code), (502, rules.CODE_UNREACHABLE), case)

    def test_window_link_must_be_https_on_the_syntony_domain(self):
        for url in ('javascript:alert(1)', 'http://app.syntony.kz/ru/embed/x', 'https://evil.example/embed/x',
                    'https://app.syntony.kz.evil.example/x', '//app.syntony.kz/x', '', None, 5):
            reply = client.request_window(CONFIG, BODY, post=Recorder(FakeResponse(200, {'url': url})))
            self.assertEqual(reply.code, rules.CODE_UNREACHABLE, url)

    def test_reply_that_echoes_the_key_never_reaches_the_browser(self):
        echoes = (
            FakeResponse(401, {'error': {'code': 'invalid_key', 'message': 'Bad key ' + KEY}}),
            FakeResponse(400, {'error': {'code': 'invalid_request', 'message': 'x', 'details': {'seen': [KEY]}}}),
            FakeResponse(200, {'url': WINDOW_URL, KEY: 1}),
        )
        for echo in echoes:
            reply = client.request_window(CONFIG, BODY, post=Recorder(echo))
            self.assertEqual(reply.code, rules.CODE_UNREACHABLE)
            self.assertNotIn(KEY, repr(reply.body))

    def test_error_with_a_success_status_is_still_an_error_for_the_browser(self):
        payload = {'error': {'code': 'operator_not_linked', 'message': '…'}}
        reply = client.request_window(CONFIG, BODY, post=Recorder(FakeResponse(200, payload)))
        self.assertEqual(reply.code, rules.CODE_UNREACHABLE)   # 200 без ссылки — не успех
        reply = client.request_window(CONFIG, BODY, post=Recorder(FakeResponse(302, payload)))
        self.assertEqual((reply.status, reply.code), (502, 'operator_not_linked'))

    def test_nothing_secret_is_written_to_the_log(self):
        body = {'scope': 'inbox', 'partnerUserId': '7', 'partnerUserEmail': 'secret.person@example.kz'}
        with self.assertLogs('syntony_window.client', level='DEBUG') as captured:
            client.request_window(CONFIG, body, post=Recorder(requests.ConnectionError(
                'HTTPSConnectionPool(host=app.syntony.kz): Bearer ' + KEY)))
            client.request_window(CONFIG, body, post=Recorder(FakeResponse(200, {'url': 'https://evil.example/' + KEY})))
        text = '\n'.join(captured.output)
        self.assertNotIn(KEY, text)
        self.assertNotIn('secret.person', text)
        self.assertNotIn('evil.example', text)


# ── Ручки ─────────────────────────────────────────────────────────────────────

SUPER, ADMIN, SV, OPERATOR, TRAINEE, FIRED, HEAD, NEWBIE = 1, 2, 30, 40, 41, 42, 50, 60


class FakeStore:
    """Склад вместо SQL: те же функции, что у syntony_window.queries, поверх словарей."""

    def __init__(self):
        self.users = {
            SUPER: {'name': 'Супер Админов', 'role': 'super_admin', 'status': 'working', 'card_email': 'super@icore.kz'},
            ADMIN: {'name': 'Админ Обычный', 'role': 'admin', 'status': 'working', 'card_email': ''},
            SV: {'name': 'Старший Смены', 'role': 'sv', 'status': 'working', 'card_email': 'sv@icore.kz'},
            OPERATOR: {'name': 'Айгерим Сапарова', 'role': 'operator', 'status': 'working', 'card_email': 'aigerim@icore.kz'},
            TRAINEE: {'name': 'Стажёр Новый', 'role': 'trainee', 'status': 'working', 'card_email': ''},
            FIRED: {'name': 'Уволенный Глеб', 'role': 'operator', 'status': 'fired', 'card_email': 'gleb@icore.kz'},
            HEAD: {'name': 'Глава Отдела', 'role': 'operator', 'status': 'working', 'card_email': '', 'is_head': True},
            NEWBIE: {'name': 'Новый Оператор', 'role': 'operator', 'status': 'working', 'card_email': 'not an email'},
        }
        self.access = {}
        self.journal = []

    def add(self, user_id, email='', link_state='unknown'):
        self.access[user_id] = {'email': email, 'link_state': link_state, 'granted_by': SUPER}

    def _working(self, user_id):
        return self.users[user_id]['status'] not in ('fired', 'dismissal')

    def load_access_context(self, cursor, user_id):
        user = self.users.get(user_id)
        if not user:
            return None
        row = self.access.get(user_id)
        return {'user_id': user_id, 'name': user['name'], 'role': user['role'], 'status': user['status'],
                'is_head': bool(user.get('is_head')), 'granted': row is not None,
                'email': row['email'] if row else '', 'link_state': row['link_state'] if row else ''}

    def get_item(self, cursor, user_id):
        row = self.access.get(user_id)
        if not row:
            return None
        user = self.users[user_id]
        return {'id': user_id, 'name': user['name'], 'role': user['role'], 'working': self._working(user_id),
                'department': 'Отдел', 'email': row['email'], 'granted_at': '2026-11-11T10:00:00',
                'granted_by': self.users[row['granted_by']]['name'], 'link_state': row['link_state'],
                'link_checked_at': None}

    def list_access(self, cursor):
        return sorted((self.get_item(cursor, uid) for uid in self.access), key=lambda item: item['name'].lower())

    def person(self, cursor, user_id):
        user = self.users.get(user_id)
        if not user:
            return None
        return {'id': user_id, 'name': user['name'], 'working': self._working(user_id),
                'card_email': rules.clean_email(user['card_email'])}

    def search_people(self, cursor, query, limit=30):
        text = str(query or '').strip().lower()
        return [{'id': uid, 'name': user['name'], 'department': 'Отдел',
                 'card_email': rules.clean_email(user['card_email'])}
                for uid, user in sorted(self.users.items())
                if self._working(uid) and uid not in self.access and text in user['name'].lower()][:limit]

    def email_owner(self, cursor, email, except_user_id):
        if not email:
            return None
        for uid, row in self.access.items():
            if uid != except_user_id and row['email'] == email:
                return self.users[uid]['name']
        return None

    def grant(self, cursor, user_id, email, actor_id):
        if user_id in self.access:
            return False
        self.access[user_id] = {'email': email, 'link_state': 'unknown', 'granted_by': actor_id}
        return True

    def set_email(self, cursor, user_id, email):
        row = self.access.get(user_id)
        if not row:
            return None
        if row['email'] == email:
            return False
        row.update(email=email, link_state='unknown')
        return True

    def revoke(self, cursor, user_id):
        row = self.access.pop(user_id, None)
        return row['email'] if row else None

    def set_link_state(self, cursor, user_id, state):
        if user_id in self.access:
            self.access[user_id]['link_state'] = state
            return True
        return False

    def log(self, cursor, action, **fields):
        self.journal.append((action, fields))


class FakeDb:
    def _get_cursor(self):
        @contextlib.contextmanager
        def cm():
            yield object()
        return cm()


OK_REPLY = FakeResponse(200, {'url': WINDOW_URL, 'expiresAt': '2026-11-11T09:12:00Z'})
NOT_LINKED = FakeResponse(409, {'error': {'code': 'operator_not_linked',
                                          'message': 'Оператор не связан с сотрудником Syntony…'}})


class Portal:
    """Портал в миниатюре: блюпринт окна, подставной склад и подставная Syntony."""

    def __init__(self, *replies, configured=True):
        from flask import Flask
        from syntony_window.routes import build_syntony_window_blueprint

        self.store = FakeStore()
        self.syntony = Recorder(*(replies or (OK_REPLY,)))
        self.user_id = SUPER
        self.qr_confirmed = set()
        app = Flask(__name__)
        app.register_blueprint(build_syntony_window_blueprint(
            db=FakeDb(),
            require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (self.user_id, (self.user_id, None, 'Тест', 'x'), None),
            sensitive_access_granted=lambda user_id: user_id in self.qr_confirmed,
            load_config=lambda: CONFIG if configured else None,
            request_window=lambda config, body: client.request_window(config, body, post=self.syntony),
            store=self.store,
        ))
        self.http = app.test_client()

    def as_user(self, user_id):
        self.user_id = user_id
        return self

    def window(self, payload=None):
        return self.http.post('/api/syntony/window', json={'scope': 'inbox'} if payload is None else payload)

    @property
    def asked(self):
        """Что ушло в Syntony: тела запросов по порядку."""
        return [call['json'] for call in self.syntony.calls]


class WindowRouteTests(unittest.TestCase):
    def test_listed_supervisor_gets_the_reply_of_syntony_as_is(self):
        portal = Portal()
        portal.store.add(SV, 'sv@icore.kz')
        response = portal.as_user(SV).window({'scope': 'leads', 'theme': 'dark'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'url': WINDOW_URL, 'expiresAt': '2026-11-11T09:12:00Z'})
        self.assertEqual(portal.asked, [{
            'scope': 'leads', 'partnerUserId': str(SV), 'partnerUserName': 'Старший Смены',
            'partnerUserEmail': 'sv@icore.kz', 'theme': 'dark', 'locale': 'ru'}])

    def test_operator_is_taken_from_the_session_not_from_the_page(self):
        portal = Portal()
        portal.store.add(SV, 'sv@icore.kz')
        portal.as_user(SV).window({
            'scope': 'inbox', 'partnerUserId': str(SUPER), 'partnerUserName': 'Супер Админов',
            'partnerUserEmail': 'super@icore.kz', 'canWrite': False, 'user_id': SUPER})
        sent = portal.asked[0]
        self.assertEqual((sent['partnerUserId'], sent['partnerUserEmail']), (str(SV), 'sv@icore.kz'))
        self.assertNotIn('canWrite', sent)
        self.assertNotIn('user_id', sent)

    def test_nobody_outside_the_list_reaches_syntony_super_admin_included(self):
        portal = Portal()
        for user_id in (SUPER, ADMIN, SV, OPERATOR):
            response = portal.as_user(user_id).window()
            self.assertEqual(response.status_code, 403)
            self.assertEqual(rules.error_code(response.get_json()), rules.CODE_NO_ACCESS)
        self.assertEqual(portal.asked, [])

    def test_fired_person_left_on_the_list_gets_nothing(self):
        portal = Portal()
        portal.store.add(FIRED, 'gleb@icore.kz')
        response = portal.as_user(FIRED).window()
        self.assertEqual(rules.error_code(response.get_json()), rules.CODE_NO_ACCESS)
        self.assertEqual(portal.asked, [])

    def test_rank_and_file_open_the_window_only_after_qr(self):
        portal = Portal()
        for user_id in (OPERATOR, TRAINEE):
            portal.store.add(user_id, 'u%d@icore.kz' % user_id)
            response = portal.as_user(user_id).window()
            self.assertEqual(response.status_code, 403)
            self.assertEqual(rules.error_code(response.get_json()), rules.CODE_QR_REQUIRED)
        self.assertEqual(portal.asked, [])
        portal.qr_confirmed.add(OPERATOR)
        self.assertEqual(portal.as_user(OPERATOR).window().status_code, 200)
        self.assertEqual(portal.as_user(TRAINEE).window().status_code, 403)
        self.assertEqual(len(portal.asked), 1)

    def test_head_of_department_and_managers_need_no_qr(self):
        portal = Portal()
        for user_id in (HEAD, SV, SUPER):
            portal.store.add(user_id, 'u%d@icore.kz' % user_id)
            self.assertEqual(portal.as_user(user_id).window().status_code, 200, user_id)

    def test_every_opening_asks_for_a_new_link(self):
        portal = Portal()
        portal.store.add(SV, 'sv@icore.kz')
        portal.as_user(SV).window()
        portal.window()
        self.assertEqual(len(portal.asked), 2)

    def test_error_of_syntony_is_passed_by_its_code_and_status(self):
        portal = Portal(NOT_LINKED)
        portal.store.add(SV, 'sv@icore.kz')
        response = portal.as_user(SV).window()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json(), {'error': {
            'code': 'operator_not_linked', 'message': 'Оператор не связан с сотрудником Syntony…'}})

    def test_link_state_follows_what_syntony_answers(self):
        portal = Portal(NOT_LINKED, OK_REPLY, FakeResponse(503, {'error': {'code': 'embed_disabled', 'message': '…'}}))
        portal.store.add(SV, 'sv@icore.kz')
        portal.as_user(SV).window()
        self.assertEqual(portal.store.access[SV]['link_state'], 'not_linked')
        portal.window()
        self.assertEqual(portal.store.access[SV]['link_state'], 'linked')
        portal.window()   # сбой не про связь — состояние остаётся
        self.assertEqual(portal.store.access[SV]['link_state'], 'linked')

    def test_bad_window_is_refused_before_syntony(self):
        portal = Portal()
        portal.store.add(SV, 'sv@icore.kz')
        for payload in ({'scope': 'admin'}, {'scope': 'phone'}, {'scope': 'phones', 'phones': ['x']}):
            response = portal.as_user(SV).window(payload)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(rules.error_code(response.get_json()), 'invalid_request')
        self.assertEqual(portal.asked, [])

    def test_driver_window_sends_the_numbers(self):
        portal = Portal()
        portal.store.add(SV, 'sv@icore.kz')
        portal.as_user(SV).window({'scope': 'phone', 'phones': ['+7 701 019 02 19', '87010190219', '+7 702 555 44 33']})
        self.assertEqual((portal.asked[0]['scope'], portal.asked[0]['phones']),
                         ('phones', ['+7 701 019 02 19', '+7 702 555 44 33']))

    def test_without_the_key_on_the_server_the_window_says_so(self):
        portal = Portal(configured=False)
        portal.store.add(SV, 'sv@icore.kz')
        response = portal.as_user(SV).window()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(rules.error_code(response.get_json()), rules.CODE_NOT_CONFIGURED)
        self.assertEqual(portal.asked, [])

    def test_key_is_in_no_answer_to_the_browser(self):
        portal = Portal(OK_REPLY, NOT_LINKED, FakeResponse(401, {'error': {'code': 'invalid_key', 'message': KEY}}),
                        requests.ConnectionError(KEY))
        portal.store.add(SV, 'sv@icore.kz')
        portal.as_user(SV)
        answers = [portal.window() for _ in range(4)]
        answers += [portal.http.get('/api/syntony/access'), portal.as_user(SUPER).http.get('/api/syntony/access'),
                    portal.http.post('/api/syntony/access', json={'user_id': OPERATOR}),
                    portal.http.post('/api/syntony/access/%d/check' % SV)]
        for answer in answers:
            self.assertNotIn(KEY, answer.get_data(as_text=True))
            self.assertNotIn('sk_live_', answer.get_data(as_text=True))

    def test_unknown_error_code_is_logged_and_still_passed(self):
        portal = Portal(FakeResponse(451, {'error': {'code': 'code_of_tomorrow', 'message': 'Новое'}}))
        portal.store.add(SV, 'sv@icore.kz')
        with self.assertLogs('syntony_window.routes', level='WARNING') as captured:
            response = portal.as_user(SV).window()
        self.assertEqual((response.status_code, rules.error_code(response.get_json())), (451, 'code_of_tomorrow'))
        self.assertIn('code_of_tomorrow', '\n'.join(captured.output))

    def test_preflight_is_answered_without_a_session(self):
        portal = Portal()
        self.assertEqual(portal.http.open('/api/syntony/window', method='OPTIONS').status_code, 204)


class AccessListRouteTests(unittest.TestCase):
    def test_only_super_admin_reads_and_edits_the_list(self):
        portal = Portal()
        portal.store.add(SV, 'sv@icore.kz')
        calls = (
            ('get', '/api/syntony/access', None),
            ('get', '/api/syntony/access/people?q=а', None),
            ('get', '/api/syntony/access/export', None),
            ('post', '/api/syntony/access', {'user_id': OPERATOR}),
            ('patch', '/api/syntony/access/%d' % SV, {'email': 'x@icore.kz'}),
            ('post', '/api/syntony/access/%d/check' % SV, None),
            ('delete', '/api/syntony/access/%d' % SV, None),
        )
        for user_id in (ADMIN, SV, OPERATOR):
            portal.as_user(user_id)
            for method, path, payload in calls:
                response = getattr(portal.http, method)(path, json=payload)
                self.assertEqual(response.status_code, 403, (user_id, method, path))
                self.assertEqual(response.get_json()['code'], 'SYNTONY_MANAGE_FORBIDDEN')
        self.assertEqual(set(portal.store.access), {SV})
        self.assertEqual(portal.asked, [])

    def test_added_person_is_registered_at_syntony_at_once(self):
        portal = Portal(NOT_LINKED)
        response = portal.http.post('/api/syntony/access', json={'user_id': OPERATOR, 'email': ' Aigerim@Example.KZ '})
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(portal.asked, [{
            'scope': 'inbox', 'partnerUserId': str(OPERATOR), 'partnerUserName': 'Айгерим Сапарова',
            'partnerUserEmail': 'aigerim@example.kz', 'theme': 'light', 'locale': 'ru'}])
        self.assertEqual((data['item']['email'], data['item']['link_state']), ('aigerim@example.kz', 'not_linked'))
        self.assertEqual(data['syntony'], {'configured': True, 'code': '', 'message': ''})
        self.assertEqual(portal.store.journal[0][0], 'grant')
        self.assertEqual(portal.store.journal[0][1]['actor_id'], SUPER)

    def test_email_defaults_to_the_one_in_the_employee_card(self):
        portal = Portal(NOT_LINKED)
        portal.http.post('/api/syntony/access', json={'user_id': OPERATOR})
        self.assertEqual(portal.store.access[OPERATOR]['email'], 'aigerim@icore.kz')
        # В карточке не адрес — человек добавлен без почты, а не с мусором.
        portal.http.post('/api/syntony/access', json={'user_id': NEWBIE})
        self.assertEqual(portal.store.access[NEWBIE]['email'], '')
        self.assertNotIn('partnerUserEmail', portal.asked[-1])

    def test_bad_person_or_email_is_refused_and_nothing_is_written(self):
        portal = Portal()
        portal.store.add(SV, 'sv@icore.kz')
        cases = (
            ({}, 400, 'SYNTONY_BAD_REQUEST'),
            ({'user_id': 'abc'}, 400, 'SYNTONY_BAD_REQUEST'),
            ({'user_id': True}, 400, 'SYNTONY_BAD_REQUEST'),
            ({'user_id': 999}, 404, 'SYNTONY_PERSON_NOT_FOUND'),
            ({'user_id': FIRED}, 409, 'SYNTONY_PERSON_FIRED'),
            ({'user_id': OPERATOR, 'email': 'не почта'}, 400, 'SYNTONY_INVALID_EMAIL'),
            ({'user_id': OPERATOR, 'email': 'SV@icore.kz'}, 409, 'SYNTONY_EMAIL_TAKEN'),
        )
        for payload, status, code in cases:
            response = portal.http.post('/api/syntony/access', json=payload)
            self.assertEqual((response.status_code, response.get_json()['code']), (status, code), payload)
        self.assertEqual(set(portal.store.access), {SV})
        self.assertEqual(portal.asked, [])
        self.assertEqual(portal.store.journal, [])

    def test_adding_twice_keeps_one_row_and_its_email(self):
        portal = Portal(NOT_LINKED)
        portal.http.post('/api/syntony/access', json={'user_id': OPERATOR, 'email': 'first@icore.kz'})
        portal.http.post('/api/syntony/access', json={'user_id': OPERATOR, 'email': 'second@icore.kz'})
        self.assertEqual(portal.store.access[OPERATOR]['email'], 'first@icore.kz')
        self.assertEqual([entry[0] for entry in portal.store.journal], ['grant'])

    def test_new_email_resets_the_link_and_registers_again(self):
        portal = Portal(OK_REPLY, NOT_LINKED)
        portal.http.post('/api/syntony/access', json={'user_id': SV, 'email': 'old@icore.kz'})
        self.assertEqual(portal.store.access[SV]['link_state'], 'linked')
        response = portal.http.patch('/api/syntony/access/%d' % SV, json={'email': 'new@icore.kz'})
        self.assertEqual(response.get_json()['item']['link_state'], 'not_linked')
        self.assertEqual(portal.asked[-1]['partnerUserEmail'], 'new@icore.kz')
        self.assertEqual([entry[0] for entry in portal.store.journal], ['grant', 'email'])
        for payload, status, code in (({'email': 'плохо'}, 400, 'SYNTONY_INVALID_EMAIL'),
                                      ({}, 400, 'SYNTONY_BAD_REQUEST')):
            response = portal.http.patch('/api/syntony/access/%d' % SV, json=payload)
            self.assertEqual((response.status_code, response.get_json()['code']), (status, code))
        response = portal.http.patch('/api/syntony/access/%d' % OPERATOR, json={'email': 'x@icore.kz'})
        self.assertEqual((response.status_code, response.get_json()['code']), (404, 'SYNTONY_NOT_LISTED'))

    def test_removed_person_loses_the_window(self):
        portal = Portal()
        portal.store.add(SV, 'sv@icore.kz')
        self.assertEqual(portal.as_user(SV).window().status_code, 200)
        self.assertEqual(portal.as_user(SUPER).http.delete('/api/syntony/access/%d' % SV).status_code, 200)
        self.assertEqual(portal.as_user(SV).window().status_code, 403)
        self.assertEqual(portal.store.journal[-1][0], 'revoke')
        # Снять того, кого в списке нет, — не ошибка и не строка следа.
        self.assertEqual(portal.as_user(SUPER).http.delete('/api/syntony/access/%d' % SV).status_code, 200)
        self.assertEqual(len(portal.store.journal), 1)

    def test_check_asks_syntony_and_tells_what_is_wrong_when_it_is_not_about_the_link(self):
        portal = Portal(FakeResponse(401, {'error': {'code': 'invalid_key', 'message': 'Ключ неверный'}}))
        portal.store.add(SV, 'sv@icore.kz', link_state='linked')
        data = portal.http.post('/api/syntony/access/%d/check' % SV).get_json()
        self.assertEqual(data['syntony'], {'configured': True, 'code': 'invalid_key', 'message': 'Ключ неверный'})
        self.assertEqual(data['item']['link_state'], 'linked')
        response = portal.http.post('/api/syntony/access/%d/check' % OPERATOR)
        self.assertEqual((response.status_code, response.get_json()['code']), (404, 'SYNTONY_NOT_LISTED'))
        portal.store.add(FIRED, 'gleb@icore.kz')
        asked = len(portal.asked)
        response = portal.http.post('/api/syntony/access/%d/check' % FIRED)
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'SYNTONY_PERSON_FIRED'))
        self.assertEqual(len(portal.asked), asked)

    def test_without_the_key_people_are_added_but_not_registered(self):
        portal = Portal(configured=False)
        data = portal.http.post('/api/syntony/access', json={'user_id': SV}).get_json()
        self.assertEqual(data['item']['link_state'], 'unknown')
        self.assertEqual(data['syntony']['code'], rules.CODE_NOT_CONFIGURED)
        self.assertFalse(portal.http.get('/api/syntony/access').get_json()['configured'])
        self.assertEqual(portal.asked, [])

    def test_search_offers_working_people_who_are_not_on_the_list(self):
        portal = Portal()
        portal.store.add(SV, 'sv@icore.kz')
        people = portal.http.get('/api/syntony/access/people?q=').get_json()['people']
        ids = {person['id'] for person in people}
        self.assertNotIn(SV, ids)
        self.assertNotIn(FIRED, ids)
        self.assertIn(OPERATOR, ids)
        self.assertEqual({person['card_email'] for person in people if person['id'] == NEWBIE}, {''})

    def test_export_is_three_columns_of_working_people(self):
        portal = Portal()
        portal.store.add(SV, 'sv@icore.kz')
        portal.store.add(OPERATOR, 'aigerim@icore.kz')
        portal.store.add(FIRED, 'gleb@icore.kz')
        response = portal.http.get('/api/syntony/access/export')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, report.XLSX_MIME)
        sheet = load_workbook(io.BytesIO(response.data)).active
        rows = [tuple(cell.value for cell in row) for row in sheet.iter_rows()]
        self.assertEqual(rows, [
            ('ID оператора в iCORE', 'ФИО', 'Рабочая почта'),
            (OPERATOR, 'Айгерим Сапарова', 'aigerim@icore.kz'),
            (SV, 'Старший Смены', 'sv@icore.kz'),
        ])
        self.assertEqual(report.export_file_name(datetime(2026, 11, 11).date()),
                         'Операторы iCORE для Syntony 11.11.2026.xlsx')


# ── Проводка ──────────────────────────────────────────────────────────────────

class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.monolith = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        cls.schema = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8')

    def test_blueprint_is_registered_with_the_portal_qr_check(self):
        block = self.monolith[self.monolith.index('from syntony_window.routes import build_syntony_window_blueprint'):]
        block = block[:block.index('logging.info(')]
        self.assertIn('sensitive_access_granted=_sensitive_access_granted_for_user', block)
        self.assertIn('require_api_key=require_api_key', block)

    def test_profile_carries_two_flags_counted_by_the_section_rules(self):
        # Пункты меню — по syntony_access (ведущий список или человек из списка); кнопка
        # «Переписка в Syntony» в карточке водителя — по syntony_window (только из списка).
        self.assertIn('"syntony_access": syntony_access', self.monolith)
        self.assertIn('"syntony_window": syntony_window_open', self.monolith)
        self.assertIn('syntony_access, syntony_window_open = _syntony_flags_for(user_id)', self.monolith)
        helper = self.monolith[self.monolith.index('def _syntony_flags_for(user_id):'):]
        helper = helper[:helper.index('\n\n\n')]
        self.assertIn('syntony_access.can_open_section(ctx)', helper)
        self.assertIn('syntony_access.can_request_window(ctx)', helper)
        # База недоступна или учётки нет — оба флага сняты, вход не падает.
        self.assertEqual(helper.count('return False, False'), 3)

    def test_profile_flags_follow_the_list(self):
        # Сама функция профиля, исполненная на подставном складе: настоящие правила, без базы.
        namespace = {'logging': logging}
        store = FakeStore()
        store.add(SV, 'sv@icore.kz')
        store.add(FIRED, 'gleb@icore.kz')

        class _Queries:
            load_access_context = staticmethod(lambda cursor, user_id: store.load_access_context(cursor, user_id))

        import sys
        import types
        helper = self.monolith[self.monolith.index('def _syntony_flags_for(user_id):'):]
        helper = helper[:helper.index('\n\n\n')]
        fake_package = types.ModuleType('syntony_window')
        fake_package.access = access
        fake_package.queries = _Queries
        saved = {name: sys.modules.get(name) for name in ('syntony_window', 'syntony_window.access', 'syntony_window.queries')}
        sys.modules.update({'syntony_window': fake_package, 'syntony_window.access': access,
                            'syntony_window.queries': _Queries})
        try:
            namespace['db'] = FakeDb()
            exec(helper, namespace)
            flags = namespace['_syntony_flags_for']
            self.assertEqual(flags(SUPER), (True, False))      # ведёт список, но окна нет: сам не в списке
            self.assertEqual(flags(SV), (True, True))          # в списке
            self.assertEqual(flags(OPERATOR), (False, False))  # не в списке
            self.assertEqual(flags(FIRED), (False, False))     # уволен, хотя строка осталась
            self.assertEqual(flags(999), (False, False))       # учётки нет
            self.assertEqual(flags(None), (False, False))

            def broken(cursor, user_id):
                raise RuntimeError('база недоступна')
            _Queries.load_access_context = staticmethod(broken)
            with self.assertLogs(level='ERROR'):
                self.assertEqual(flags(SV), (False, False))
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    def test_driver_card_button_needs_the_window_flag(self):
        chats = (ROOT / 'src' / 'components' / 'wazzup' / 'WazzupChatsView.jsx').read_text(encoding='utf-8')
        self.assertIn('syntonyAccess={!embed && user?.syntony_window === true}', chats)
        card = (ROOT / 'src' / 'components' / 'wazzup' / 'ChatDriverCard.jsx').read_text(encoding='utf-8')
        self.assertIn('const syntonyPhones = syntonyAccess ? syntonyPhonesOf(chat, crm) : [];', card)
        self.assertIn('{syntonyPhones.length > 0 && (', card)

    def test_sections_are_wired_into_the_portal(self):
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        # Пункты меню — строго по флагу сервера.
        self.assertIn('const canAccessSyntonyForUser = (userLike) => userLike?.syntony_access === true;', app)
        for view, title in (('syntony_chats', 'Чаты Syntony'), ('syntony_leads', 'Заявки Syntony')):
            self.assertIn("handleSidebarViewNavigation(e, '%s')" % view, app)
            self.assertIn('<span className="sidebar-text">%s</span>' % title, app)
        # Оператор запрашивает статус QR при входе в раздел — иначе вечное «Проверяем доступ…».
        self.assertIn("|| view === 'syntony_chats' || view === 'syntony_leads'\n", app.replace('\r\n', '\n'))
        # Замок — всем, кому портал выдаёт QR; двойник syntony_window/access.py: requires_qr.
        self.assertIn('const syntonyLocked = sensitiveQrAvailableFor(user) && !sensitiveAccess.granted;', app)
        # Человека без доступа ссылка на раздел не оставляет на пустом экране.
        self.assertIn("if ((view === 'syntony_chats' || view === 'syntony_leads') && !canAccessSyntonySection) {", app)
        self.assertIn("if ((view === 'syntony_chats' || view === 'syntony_leads') && canAccessSyntonySection) return;", app)
        # Открытое окно не переходит к другой учётке.
        self.assertIn('key={`syntony:${user?.id}`}', app)
        self.assertIn('syntonyOpenedFor === user?.id', app)

    def test_frame_is_as_the_spec_asks(self):
        window = (ROOT / 'src' / 'components' / 'syntony' / 'SyntonyWindow.jsx').read_text(encoding='utf-8')
        frame = window[window.index('<iframe'):window.index('/>', window.index('<iframe'))]
        self.assertIn('allow="microphone"', frame)       # запись голосовых
        self.assertNotIn('sandbox', frame)                # с ним не открылась бы кнопка звонка
        self.assertIn('key={state.url}', frame)           # новая ссылка — новый фрейм
        # Ссылку не хранят: к хранилищам браузера экран окна не обращается вовсе.
        for name in ('SyntonyWindow.jsx', 'SyntonyView.jsx', 'SyntonyDriverWindow.jsx', 'syntonyRules.js'):
            text = (ROOT / 'src' / 'components' / 'syntony' / name).read_text(encoding='utf-8')
            self.assertIsNone(re.search(r'\b(?:local|session)Storage\s*(?:\.\s*[A-Za-z]|\[)', text), name)

    def test_error_codes_on_the_screen_match_the_server(self):
        rules_js = (ROOT / 'src' / 'components' / 'syntony' / 'syntonyRules.js').read_text(encoding='utf-8')
        table = rules_js[rules_js.index('export const ERROR_VIEWS'):rules_js.index('export const KNOWN_ERROR_CODES')]
        on_screen = set(re.findall(r'^\s{4}([a-z_]+): \{', table, flags=re.M))
        self.assertEqual(on_screen, set(rules.SYNTONY_ERROR_CODES) | set(rules.ICORE_ERROR_CODES))

    def test_schema_is_created_at_start_under_its_own_savepoint(self):
        self.assertIn('self._init_syntony_window_schema_tx(cursor)', self.schema)
        method = self.schema[self.schema.index('def _init_syntony_window_schema_tx(self, cursor):'):]
        method = method[:method.index('\n    def ')]
        self.assertIn('SAVEPOINT syntony_window_schema', method)
        self.assertIn('from syntony_window.schema import init_syntony_window_schema', method)
        self.assertIn('ROLLBACK TO SAVEPOINT syntony_window_schema', method)

    def test_key_is_read_from_the_environment_only(self):
        # Ключ вида sk_live_… не должен появиться ни в одном исходнике пакета и экрана.
        sources = list((ROOT / 'syntony_window').glob('*.py')) + list((ROOT / 'src' / 'components' / 'syntony').glob('*'))
        self.assertTrue(sources)
        for path in sources:
            text = path.read_text(encoding='utf-8')
            self.assertIsNone(re.search(r'sk_live_[A-Za-z0-9]', text), path.name)
        self.assertIn("ENV_KEY = 'SYNTONY_WINDOW_API_KEY'", (ROOT / 'syntony_window' / 'client.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    unittest.main()
