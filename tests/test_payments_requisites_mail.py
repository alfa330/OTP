# -*- coding: utf-8 -*-
"""«Оплата счетов»: почта поставщиков и письма о смене реквизитов наших компаний.

Просьба владельца 20.10.2026: «добавление эмайла контрагентов поставщиков и тд,
автомат рассылка по эмайлом при изменении реквизитов»; 21.10.2026 — своя почта у
каждой компании, справочник «Почта поставщиков» и галочки «Кому» в письме. Здесь —
то, что видно без базы: разбор адресов, почта компании (сервер по адресу, пароль,
пробное письмо), что считается сменой реквизитов, текст письма, разбор ответов
почтового сервера и проводка ручек справочников. Что значат запросы (очередь,
повторы, «Отправить ещё раз», галочки), проверяет
tests/test_payments_requisites_mail_postgres.py на настоящем Postgres.

Случаи адресов и смены реквизитов — общие с tests/payments_requisites_mail.test.mjs
(tests/data/payments_requisites_mail_cases.json): форма и сервер решают одинаково.
"""

import base64
import json
import os
import smtplib
import unittest
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from flask import Flask

from payments import access, directory, google_mail, mail, mailbox, requisites_mail, routes, routes_directory

CASES = json.loads((Path(__file__).resolve().parent / 'data' / 'payments_requisites_mail_cases.json')
                   .read_text(encoding='utf-8'))
ADMIN = access.SECTION_ADMIN_USER_IDS[0]

# Почта компании так, как её собирает mailbox.prepare: Gmail по адресу.
GMAIL = mailbox.Settings(address='buh@company.kz', provider='gmail', host='smtp.gmail.com', port=465,
                         security='ssl', login='buh@company.kz', password='app-password')


class EmailsTests(unittest.TestCase):
    def test_shared_cases(self):
        for case in CASES['emails']:
            with self.subTest(case['input']):
                if case['bad'] is None:
                    self.assertEqual(mail.parse_emails(case['input']), case['emails'])
                else:
                    with self.assertRaises(mail.EmailError) as caught:
                        mail.parse_emails(case['input'])
                    self.assertEqual(caught.exception.token, case['bad'])
                    self.assertIn('«%s»' % case['bad'], str(caught.exception))

    def test_list_and_none(self):
        self.assertEqual(mail.parse_emails(None), [])
        self.assertEqual(mail.parse_emails(['A@x.kz', 'b@x.kz, c@x.kz']), ['a@x.kz', 'b@x.kz', 'c@x.kz'])

    def test_five_is_the_limit(self):
        self.assertEqual(len(mail.parse_emails(', '.join('a%s@x.kz' % i for i in range(mail.MAX_EMAILS)))), 5)
        with self.assertRaises(mail.EmailError) as caught:
            mail.parse_emails(CASES['too_many'])
        self.assertIn('Не больше %s адресов' % mail.MAX_EMAILS, str(caught.exception))

    def test_too_long(self):
        with self.assertRaises(mail.EmailError):
            mail.parse_emails('a' * 250 + '@x.kz')

    def test_find_emails_in_contact_text(self):
        self.assertEqual(mail.find_emails('Айдос (buh@oblako.kz), +7 700 000 00 00; «info@oblako.kz».'),
                         ['buh@oblako.kz', 'info@oblako.kz'])
        self.assertEqual(mail.find_emails('звонить Иван, почта: Ivan.Petrov@Romashka.KZ!'), ['ivan.petrov@romashka.kz'])
        self.assertEqual(mail.find_emails('только телефон 8 701 000 00 00'), [])
        self.assertEqual(mail.find_emails(None), [])
        self.assertEqual(len(mail.find_emails(' '.join('a%s@x.kz' % i for i in range(9)))), mail.MAX_EMAILS)


class MailboxTests(unittest.TestCase):
    """Почта для писем у каждой компании (решение владельца 21.10.2026): какая почта по
    адресу, сервер вручную, пароль, проверка пробным письмом."""

    def setUp(self):
        patcher = mock.patch.dict('os.environ', {'JWT_SECRET': 'unit-secret'})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ('PAYMENTS_CARD_KEY', 'PAYMENTS_MAIL_ALLOW_LOCAL'):
            os.environ.pop(name, None)

    def test_provider_by_address(self):
        nobody = lambda domain: []  # noqa: E731 — DNS не спрашиваем
        # Публичная почта — по домену (адресов на ней в репозитории нет: tests/test_no_personal_data_in_repo.py).
        for domain, key in (('gmail.com', 'gmail'), ('Yandex.KZ', 'yandex'), ('ya.ru', 'yandex'), ('bk.ru', 'mailru'),
                            ('mail.ru', 'mailru'), ('company.example', None), ('', None)):
            with self.subTest(domain):
                self.assertEqual(mailbox.provider_of_domain(domain, mx=nobody), key)
        self.assertEqual(mailbox.provider_of('Buh@Company.Example', mx=lambda domain: ['mx.yandex.net']), 'yandex')
        for mx, key in ((['aspmx.l.google.com', 'alt1.aspmx.l.google.com'], 'gmail'), (['smtp.google.com'], 'gmail'),
                        (['mx.yandex.net'], 'yandex'), (['emx.mail.ru'], 'mailru'),
                        (['company-kz.mail.protection.outlook.com'], 'microsoft'), (['mail.hoster.kz'], None),
                        (['notgoogle.com'], None)):
            with self.subTest(mx):
                self.assertEqual(mailbox.provider_of('buh@company.kz', mx=lambda domain, hosts=mx: hosts), key)

    def test_detect_tells_where_to_get_the_password(self):
        found = mailbox.detect(' Buh@Company.KZ ', mx=lambda domain: ['aspmx.l.google.com'])
        self.assertEqual(found['address'], 'buh@company.kz')
        self.assertEqual((found['server']['label'], found['server']['host'], found['server']['port'],
                          found['server']['security'], found['server']['help_url']),
                         ('Gmail', 'smtp.gmail.com', 465, 'ssl', 'https://myaccount.google.com/apppasswords'))
        unknown = mailbox.detect('buh@hoster-client.kz', mx=lambda domain: ['mx.hoster.kz'])
        self.assertEqual((unknown['server'], unknown['guess']), (None, 'smtp.hoster-client.kz'))
        with self.assertRaises(mailbox.MailboxError) as caught:
            mailbox.detect('не адрес')
        self.assertEqual((caught.exception.field, caught.exception.code), ('address', 'PAYMENTS_EMAIL_INVALID'))

    def test_mx_lookup_asks_dns_over_https_once(self):
        answer = mock.Mock(status_code=200)
        answer.json.return_value = {'Answer': [{'type': 15, 'data': '10 aspmx.l.google.com.'},
                                               {'type': 5, 'data': 'alias.example.'}]}
        mailbox._MX_CACHE.clear()
        self.addCleanup(mailbox._MX_CACHE.clear)
        with mock.patch('requests.get', return_value=answer) as get:
            self.assertEqual(mailbox._mx_hosts('company.kz'), ['aspmx.l.google.com'])
            self.assertEqual(mailbox._mx_hosts('company.kz'), ['aspmx.l.google.com'])
        self.assertEqual(get.call_count, 1, 'ответ DNS помнится')
        self.assertEqual(get.call_args.kwargs['params'], {'name': 'company.kz', 'type': 'MX'})
        self.assertEqual(get.call_args.kwargs['timeout'], mailbox.MX_TIMEOUT_SECONDS)
        with mock.patch('requests.get', side_effect=OSError('нет сети')):
            self.assertEqual(mailbox._mx_hosts('offline.kz'), [], 'DNS не ответил — окно попросит сервер')

    def test_manual_server_is_only_outside_and_only_mail_ports(self):
        outside = lambda host: None  # noqa: E731
        self.assertEqual(mailbox.manual_server(' SMTP.Hoster.KZ. ', '587', resolve=outside), ('smtp.hoster.kz', 587, 'starttls'))
        self.assertEqual(mailbox.manual_server('smtp.hoster.kz', '', resolve=outside), ('smtp.hoster.kz', 465, 'ssl'))
        for host, port, field in (('', 465, 'host'), ('smtp.hoster.kz', 25, 'port'), ('smtp.hoster.kz', 'порт', 'port'),
                                  ('smtp hoster', 465, 'host'), ('127.0.0.1', 1025, 'port')):
            with self.subTest((host, port)):
                with self.assertRaises(mailbox.MailboxError) as caught:
                    mailbox.manual_server(host, port, resolve=outside)
                self.assertEqual(caught.exception.field, field)
        with self.assertRaises(mailbox.MailboxError) as caught:
            mailbox.manual_server('db.internal', 465, resolve=lambda host: 'Сервер db.internal — во внутренней сети')
        self.assertIn('внутренней сети', str(caught.exception))
        self.assertEqual(mailbox.manual_server('127.0.0.1', '1025', allow_local=True), ('127.0.0.1', 1025, 'none'),
                         'стенд: почтовый сервер на этой же машине')
        self.assertIn('внутренней сети', mailbox._inside('localhost'))

    def test_local_server_only_with_the_stand_flag(self):
        self.assertFalse(mailbox.local_allowed({}))
        self.assertTrue(mailbox.local_allowed({'PAYMENTS_MAIL_ALLOW_LOCAL': '1'}))

    def test_password_is_encrypted_and_survives_only_its_key(self):
        token = mailbox.encrypt('abcd efgh')
        self.assertNotIn('abcd', token)
        self.assertEqual(mailbox.decrypt(token), 'abcd efgh')
        with mock.patch.dict('os.environ', {'JWT_SECRET': 'другой'}):
            self.assertIsNone(mailbox.decrypt(token), 'сменили ключ — пароль не читается, а не падает')
        with mock.patch.dict('os.environ', {'PAYMENTS_CARD_KEY': 'свой ключ раздела'}):
            own = mailbox.encrypt('secret')
            self.assertEqual(mailbox.decrypt(token), 'abcd efgh', 'старые пароли читаются и со своим ключом')
        self.assertIsNone(mailbox.decrypt(own), 'без своего ключа его пароли не читаются')
        with mock.patch.dict('os.environ', {'JWT_SECRET': ''}):
            with self.assertRaises(mailbox.MailboxError):
                mailbox.encrypt('secret')
        self.assertIsNone(mailbox.decrypt(None))

    def test_prepare(self):
        nobody = lambda domain: []  # noqa: E731
        google = lambda domain: ['aspmx.l.google.com']  # noqa: E731 — свой домен на Google Workspace
        settings = mailbox.prepare({'address': 'Buh@Company.KZ', 'password': ' abcd efgh ijkl mnop '}, mx=google)
        self.assertEqual(settings, mailbox.Settings('buh@company.kz', 'gmail', 'smtp.gmail.com', 465, 'ssl',
                                                    'buh@company.kz', 'abcdefghijklmnop'),
                         'пароль приложения Google — без пробелов между группами')
        with self.assertRaises(mailbox.MailboxError) as caught:
            mailbox.prepare({'address': 'buh@company.kz'}, mx=google)
        self.assertEqual((caught.exception.field, caught.exception.code), ('password', 'PAYMENTS_MAILBOX_PASSWORD'))
        current = {'address': 'buh@company.kz', 'password_enc': mailbox.encrypt('stored')}
        self.assertEqual(mailbox.prepare({'address': 'buh@company.kz'}, current=current, mx=google).password, 'stored',
                         'адрес прежний — пароль можно не вводить')
        with self.assertRaises(mailbox.MailboxError):
            mailbox.prepare({'address': 'other@company.kz'}, current=current, mx=google)
        with self.assertRaises(mailbox.MailboxError) as caught:
            mailbox.prepare({'address': 'buh@company.kz'}, current=dict(current, password_enc='испорчен'), mx=google)
        self.assertEqual(str(caught.exception), mailbox.UNREADABLE)
        with self.assertRaises(mailbox.MailboxError) as caught:
            mailbox.prepare({'address': 'buh@hoster-client.kz', 'password': 'p'}, mx=nobody)
        self.assertEqual((caught.exception.field, caught.exception.code), ('host', 'PAYMENTS_MAILBOX_SERVER'))
        manual = mailbox.prepare({'address': 'buh@hoster-client.kz', 'password': 'p q', 'host': 'smtp.hoster.kz',
                                  'port': 587}, mx=nobody, resolve=lambda host: None)
        self.assertEqual((manual.provider, manual.host, manual.port, manual.security, manual.password),
                         (None, 'smtp.hoster.kz', 587, 'starttls', 'p q'))
        known = mailbox.prepare({'address': 'buh@company.kz', 'password': 'p', 'host': 'smtp.gmail.com'},
                                mx=nobody, resolve=lambda host: None)
        self.assertEqual(known.provider, 'gmail', 'сервер ввели руками, а он известный — подсказки те же')

    def test_test_letter_comes_from_the_company_to_itself(self):
        message = mailbox.test_letter(GMAIL, 'ТОО «Наша\nкомпания»')
        self.assertEqual(message['To'], 'buh@company.kz')
        self.assertEqual(message['From'].addresses[0].display_name, 'ТОО «Наша компания»')
        self.assertEqual(message['From'].addresses[0].addr_spec, 'buh@company.kz')
        self.assertEqual(message['Subject'], mailbox.TEST_SUBJECT)
        self.assertIn('С этого ящика поставщики будут получать письма о смене реквизитов ТОО «Наша компания»',
                      message.get_body(('plain',)).get_content())

    def test_verify_maps_refusals_to_fields(self):
        class Box:
            def __init__(self, failure=None, on_enter=None):
                self.failure, self.on_enter, self.sent = failure, on_enter, []

            def __call__(self, cfg):
                self.cfg = cfg
                return self

            def __enter__(self):
                if self.on_enter:
                    raise self.on_enter
                return self

            def __exit__(self, *_exc):
                return False

            def send(self, message):
                if self.failure:
                    raise self.failure
                self.sent.append(message['To'])
                return '<1@company.kz>'

        ok = Box()
        self.assertEqual(mailbox.verify(GMAIL, 'ТОО «Наша»', transport=ok), '<1@company.kz>')
        self.assertEqual((ok.sent, ok.cfg.user, ok.cfg.sender, ok.cfg.security), (['buh@company.kz'], 'buh@company.kz',
                                                                                   'buh@company.kz', 'ssl'))
        cases = (
            (Box(on_enter=mail.MailError('transport', 'Почта не приняла логин или пароль приложения (535 5.7.8 BadCredentials)',
                                         reason='login')), 'password', 'PAYMENTS_MAILBOX_LOGIN'),
            (Box(on_enter=mail.MailError('transport', 'Нет связи с почтовым сервером', reason='connect')), None,
             'PAYMENTS_MAILBOX_CONNECT'),
            (Box(failure=mail.MailError('transport', 'Ящик не пускает отправителя', reason='sender')), 'address',
             'PAYMENTS_MAILBOX_SENDER'),
            (Box(failure=mail.MailError('throttled', 'Сервер не принял письмо: 554 spam')), None, 'PAYMENTS_MAILBOX_SEND'),
        )
        for box, field, code in cases:
            with self.subTest(code):
                with self.assertRaises(mailbox.MailboxError) as caught:
                    mailbox.verify(GMAIL, 'ТОО «Наша»', transport=box)
                self.assertEqual((caught.exception.field, caught.exception.code), (field, code))
        with self.assertRaises(mailbox.MailboxError) as caught:
            mailbox.verify(GMAIL, 'ТОО «Наша»', transport=cases[0][0])
        self.assertIn('пароль приложения из аккаунта Google', str(caught.exception))
        self.assertIn('(535 5.7.8 BadCredentials)', str(caught.exception), 'и что ответил сервер')
        self.assertEqual(caught.exception.help_url, 'https://myaccount.google.com/apppasswords')
        manual = GMAIL._replace(provider=None, host='smtp.hoster.kz')
        with self.assertRaises(mailbox.MailboxError) as caught:
            mailbox.verify(manual, 'ТОО «Наша»', transport=Box(on_enter=mail.MailError('transport', 'нет связи', reason='connect')))
        self.assertEqual(caught.exception.field, 'host', 'свой сервер — красным у поля сервера')

    def test_view_has_no_secrets(self):
        row = {'legal_entity_id': 3, 'address': 'buh@company.kz', 'provider': 'gmail', 'host': 'smtp.gmail.com', 'port': 465,
               'security': 'ssl', 'login': 'buh@company.kz', 'password_enc': 'секрет', 'checked_at': None,
               'problem': None, 'problem_at': None, 'updated_at': None, 'updated_by_name': 'Админ'}
        shown = mailbox.view(row)
        self.assertNotIn('password_enc', shown)
        self.assertNotIn('login', shown)
        self.assertEqual((shown['label'], shown['manual'], shown['help_url']), ('Gmail', False, mailbox.PROVIDERS['gmail']['help_url']))
        self.assertTrue(mailbox.works(shown))
        self.assertFalse(mailbox.works(dict(shown, problem='не пускает')))
        self.assertFalse(mailbox.works(None))
        self.assertTrue(mailbox.view(dict(row, provider=None))['manual'])
        self.assertEqual(mailbox.pause_key(GMAIL), ('smtp.gmail.com', 'buh@company.kz'))


class GoogleMailTests(unittest.TestCase):
    """«Войти через Google» (решение владельца 21.10.2026): OAuth и отправка через Gmail API."""

    CFG = {'client_id': 'cid', 'client_secret': 'secret', 'redirect_uri': 'https://api.example/cb',
           'auth_url': google_mail.AUTH_URL, 'token_url': google_mail.TOKEN_URL, 'send_url': google_mail.SEND_URL}

    def setUp(self):
        patcher = mock.patch.dict('os.environ', {'JWT_SECRET': 'unit-secret'})
        patcher.start()
        self.addCleanup(patcher.stop)
        google_mail._TOKENS.clear()
        self.addCleanup(google_mail._TOKENS.clear)

    @staticmethod
    def reply(status, body=None, text=''):
        response = mock.Mock(status_code=status, text=text or json.dumps(body or {}))
        response.json.return_value = body if body is not None else {}
        return response

    @staticmethod
    def id_token(payload):
        part = lambda data: base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')  # noqa: E731
        return part({'alg': 'RS256'}) + '.' + part(payload) + '.sig'

    def test_config_only_with_both_keys(self):
        self.assertIsNone(google_mail.config({}))
        self.assertIsNone(google_mail.config({'PAYMENTS_GOOGLE_CLIENT_ID': 'cid'}))
        cfg = google_mail.config({'PAYMENTS_GOOGLE_CLIENT_ID': ' cid ', 'PAYMENTS_GOOGLE_CLIENT_SECRET': 'sec'})
        self.assertEqual((cfg['client_id'], cfg['redirect_uri'], cfg['token_url']),
                         ('cid', google_mail.DEFAULT_REDIRECT, google_mail.TOKEN_URL))
        self.assertTrue(google_mail.DEFAULT_REDIRECT.endswith('/api/payments/mailbox/google/callback'))

    def test_state_is_signed_and_expires(self):
        token = google_mail.sign_state({'e': 3, 'u': 10, 'o': 'https://alfa330.github.io'}, now=1000)
        self.assertEqual(google_mail.read_state(token, now=1100)['e'], 3)
        self.assertIsNone(google_mail.read_state(token, now=1000 + google_mail.STATE_SECONDS + 1), 'срок вышел')
        raw, signature = token.split('.')
        forged = base64.urlsafe_b64encode(json.dumps({'e': 4, 'exp': 99999999999}).encode()).decode().rstrip('=')
        self.assertIsNone(google_mail.read_state(forged + '.' + signature, now=1100), 'чужая компания с той же подписью')
        self.assertIsNone(google_mail.read_state('мусор'))
        with mock.patch.dict('os.environ', {'JWT_SECRET': 'другой'}):
            self.assertIsNone(google_mail.read_state(token, now=1100))

    def test_portal_origin(self):
        self.assertEqual(google_mail.portal_origin('https://alfa330.github.io/'), 'https://alfa330.github.io')
        self.assertEqual(google_mail.portal_origin('https://evil.example', env={}), 'https://alfa330.github.io')
        self.assertEqual(google_mail.portal_origin('http://127.0.0.1:5283', env={}), 'https://alfa330.github.io')
        self.assertEqual(google_mail.portal_origin('http://127.0.0.1:5283', env={'PAYMENTS_MAIL_ALLOW_LOCAL': '1'}),
                         'http://127.0.0.1:5283', 'стенд')

    def test_auth_url_asks_only_for_sending(self):
        from urllib.parse import parse_qs, urlsplit
        url = google_mail.auth_url(self.CFG, 'ST', login_hint='buh@company.kz')
        query = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
        self.assertTrue(url.startswith(google_mail.AUTH_URL + '?'))
        self.assertEqual(query['scope'], 'openid email https://www.googleapis.com/auth/gmail.send',
                         'только отправка — читать почту портал не может')
        self.assertEqual((query['access_type'], query['prompt'], query['state'], query['login_hint']),
                         ('offline', 'consent', 'ST', 'buh@company.kz'))

    def test_exchange_and_email(self):
        good = {'access_token': 'at', 'refresh_token': 'rt', 'scope': 'openid email https://www.googleapis.com/auth/gmail.send',
                'id_token': self.id_token({'email': 'Buh@Company.KZ', 'email_verified': True})}
        with mock.patch.object(google_mail, '_post', return_value=self.reply(200, good)) as post:
            tokens = google_mail.exchange(self.CFG, 'CODE')
        self.assertEqual(post.call_args.args[1]['grant_type'], 'authorization_code')
        self.assertEqual(google_mail.email_of(tokens), 'buh@company.kz')
        for body, words in (({'error': 'invalid_grant', 'error_description': 'Bad Request'}, 'Bad Request'),
                            (dict(good, scope='openid email'), 'Отправлять письма'),
                            ({k: v for k, v in good.items() if k != 'refresh_token'}, 'постоянный доступ')):
            with self.subTest(words):
                with mock.patch.object(google_mail, '_post', return_value=self.reply(200 if 'access_token' in body else 400, body)):
                    with self.assertRaises(google_mail.GoogleError) as caught:
                        google_mail.exchange(self.CFG, 'CODE')
                self.assertIn(words, str(caught.exception))
        with self.assertRaises(google_mail.GoogleError):
            google_mail.email_of({'id_token': self.id_token({'email': 'a@x.kz', 'email_verified': False})})

    def test_access_token_is_cached_and_revocation_is_a_login_problem(self):
        with mock.patch.object(google_mail, '_post', return_value=self.reply(200, {'access_token': 'at1', 'expires_in': 3600})) as post:
            self.assertEqual(google_mail.access_token(self.CFG, 'rt', clock=lambda: 0.0), 'at1')
            self.assertEqual(google_mail.access_token(self.CFG, 'rt', clock=lambda: 3000.0), 'at1')
        self.assertEqual(post.call_count, 1, 'токен доступа живёт около часа — Google не спрашиваем на каждое письмо')
        with mock.patch.object(google_mail, '_post', return_value=self.reply(400, {'error': 'invalid_grant'})):
            with self.assertRaises(mail.MailError) as caught:
                google_mail.access_token(self.CFG, 'revoked', clock=lambda: 0.0)
        self.assertEqual((caught.exception.kind, caught.exception.reason), ('transport', 'login'))
        self.assertIn('войдите через Google заново', str(caught.exception))
        with mock.patch.object(google_mail, '_post', side_effect=OSError('нет сети')):
            with self.assertRaises(mail.MailError) as caught:
                google_mail.access_token(self.CFG, 'other', clock=lambda: 0.0)
        self.assertEqual(caught.exception.reason, 'connect')

    def test_gmail_api_transport(self):
        cfg = mailbox.config(mailbox.google_settings('buh@company.kz', 'rt'))
        message = mail.build_message(cfg, to=['a@x.kz'], subject='s', text='t', html=None, display_name='ТОО «Наша»')
        with mock.patch.object(google_mail, 'access_token', return_value='at'), \
                mock.patch.object(google_mail, '_post', return_value=self.reply(200, {'id': 'g1'})) as post:
            with google_mail.Transport(cfg, google=self.CFG) as smtp:
                smtp.deadline(7)
                self.assertTrue(smtp.send(message).startswith('<'))
                self.assertFalse(smtp.closed)
        self.assertEqual(post.call_args.args[0], google_mail.SEND_URL)
        self.assertEqual(post.call_args.kwargs['headers'], {'Authorization': 'Bearer at'})
        self.assertEqual(post.call_args.kwargs['timeout'], 7.0)
        raw = base64.urlsafe_b64decode(post.call_args.kwargs['json']['raw'] + '==')
        self.assertIn(b'To: a@x.kz', raw)
        cases = ((429, {'error': {'message': 'Too many'}}, 'throttled', None),
                 (403, {'error': {'message': 'Limit', 'errors': [{'reason': 'dailyLimitExceeded'}]}}, 'throttled', None),
                 (403, {'error': {'message': 'Insufficient Permission', 'errors': [{'reason': 'insufficientPermissions'}]}},
                  'transport', 'login'),
                 (400, {'error': {'message': 'Invalid To header'}}, 'permanent', None),
                 (503, {'error': {'message': 'Backend Error'}}, 'temporary', None))
        for status, body, kind, reason in cases:
            with self.subTest(status):
                with mock.patch.object(google_mail, 'access_token', return_value='at'), \
                        mock.patch.object(google_mail, '_post', return_value=self.reply(status, body)):
                    with google_mail.Transport(cfg, google=self.CFG) as smtp:
                        with self.assertRaises(mail.MailError) as caught:
                            smtp.send(message)
                self.assertEqual((caught.exception.kind, caught.exception.reason), (kind, reason))
        with mock.patch.object(google_mail, 'config', return_value=None):
            with self.assertRaises(mail.MailError) as caught:
                with google_mail.Transport(cfg):
                    pass
        self.assertIn('не настроен', str(caught.exception))

    def test_google_mailbox_is_sent_through_the_api(self):
        settings = mailbox.google_settings('buh@company.kz', 'rt')
        self.assertIs(mailbox.opener(settings), google_mail.Transport)
        self.assertIs(mailbox.opener(GMAIL), mailbox.TRANSPORT)
        row = {'legal_entity_id': 3, 'address': 'buh@company.kz', 'provider': 'gmail', 'host': google_mail.HOST, 'port': 443,
               'security': 'oauth', 'login': 'buh@company.kz', 'password_enc': 'x', 'checked_at': None, 'problem': None,
               'problem_at': None, 'updated_at': None, 'updated_by_name': None}
        shown = mailbox.view(row)
        self.assertEqual((shown['method'], shown['manual']), ('google', False))
        with self.assertRaises(mailbox.MailboxError) as caught:
            mailbox.prepare({'address': 'buh@company.kz'}, current=dict(row, password_enc=mailbox.encrypt('rt')),
                            mx=lambda domain: ['aspmx.l.google.com'])
        self.assertEqual(caught.exception.field, 'password', 'токен Google — не пароль для SMTP')
        refusal = mailbox.refusal(mail.MailError('transport', 'Google отозвал доступ', reason='login'), settings)
        self.assertEqual((refusal.code, refusal.field, str(refusal)), ('PAYMENTS_MAILBOX_LOGIN', None, 'Google отозвал доступ'))


class ChangedFieldsTests(unittest.TestCase):
    def test_shared_cases(self):
        for case in CASES['requisites']:
            with self.subTest(case['about']):
                self.assertEqual(requisites_mail.changed_fields(case['before'], case['after']), case['changed'])

    def test_snapshot_keeps_only_requisites(self):
        shot = requisites_mail.snapshot({'name': ' ТОО «Наша» ', 'note': 'x', 'kind': 'too', 'vat_payer': 1,
                                         'iik': 'kz11 22', 'bin': '12-34'})
        self.assertEqual(set(shot), set(requisites_mail.REQUISITE_FIELDS))
        self.assertEqual((shot['name'], shot['vat_payer'], shot['iik'], shot['bin']), ('ТОО «Наша»', True, 'KZ1122', '1234'))

    def test_fields_match_copy_requisites(self):
        """В письмо идёт ровно то, что в «Скопировать реквизиты», и в том же порядке."""
        entity = {'name': 'N', 'bin': '1', 'legal_address': 'A', 'iik': 'I', 'bank_name': 'K', 'bik': 'C',
                  'kbe': 'E', 'vat_payer': True, 'requisites': 'R'}
        lines = directory.requisites_text(entity).split('\n')
        self.assertEqual(len(lines), len(requisites_mail.REQUISITE_FIELDS))
        self.assertEqual(set(requisites_mail.FIELD_LABELS), set(requisites_mail.REQUISITE_FIELDS))


BEFORE = {'name': 'ТОО «Наша компания»', 'bin': '123456789012', 'legal_address': 'Алматы, Абая 1',
          'iik': 'KZ11TEST0000000001', 'bank_name': 'АО «Альфа»', 'bik': 'ALFAKZKA', 'kbe': '17',
          'vat_payer': False, 'requisites': ''}


class _Cells(HTMLParser):
    """Ячейки таблицы «Реквизит / Было / Стало» и текст блока «целиком» из HTML письма."""

    def __init__(self):
        super().__init__()
        self.rows, self.blocks, self._row, self._cell, self._div = [], [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            self._cell = []
        elif tag == 'div' and 'background:#f8fafc' in (dict(attrs).get('style') or ''):
            self._div = []
        elif tag == 'br' and self._div is not None:
            self._div.append('\n')

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self._cell is not None:
            self._row.append(''.join(self._cell).strip())
            self._cell = None
        elif tag == 'tr' and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == 'div' and self._div is not None:
            self.blocks.append(''.join(self._div))
            self._div = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._div is not None:
            self._div.append(data)


class LetterTests(unittest.TestCase):
    def test_what_changed_and_full_requisites(self):
        after = dict(BEFORE, iik='KZ22TEST0000000002', bank_name='АО «Бета»', vat_payer=True)
        subject, text, body = requisites_mail.letter(BEFORE, after)
        self.assertEqual(subject, 'Изменение реквизитов ТОО «Наша компания»')
        self.assertIn('Сообщаем об изменении реквизитов ТОО «Наша компания» (БИН 123456789012).', text)
        self.assertIn('— ИИК: было «KZ11TEST0000000001», стало «KZ22TEST0000000002»', text)
        self.assertIn('— Банк: было «АО «Альфа»», стало «АО «Бета»»', text)
        self.assertIn('— НДС: было «без НДС», стало «плательщик НДС»', text)
        self.assertNotIn('Юридический адрес: было', text, 'неизменённое в «что изменилось» не попадает')
        self.assertIn('Новые реквизиты целиком:\nТОО «Наша компания»\nБИН 123456789012\n', text)
        self.assertTrue(text.rstrip().endswith('С уважением,\nТОО «Наша компания»'))

    def test_html_table_was_then_became(self):
        """Gmail, Outlook и iOS Mail показывают HTML-часть: в ней «было» и «стало» не перепутаны."""
        after = dict(BEFORE, iik='KZ22TEST0000000002', kbe='19')
        _subject, _text, body = requisites_mail.letter(BEFORE, after)
        cells = _Cells()
        cells.feed(body)
        self.assertEqual(cells.rows, [['Реквизит', 'Было', 'Стало'], ['ИИК', 'KZ11TEST0000000001', 'KZ22TEST0000000002'],
                                      ['КБЕ', '17', '19']])
        self.assertEqual(cells.blocks, [directory.requisites_text(requisites_mail.snapshot(after))],
                         'блок «целиком» — новые реквизиты, а не старые')
        self.assertIn('Сообщаем об изменении реквизитов <b>ТОО «Наша компания» (БИН 123456789012)</b>.', body)
        self.assertIn('<meta name="viewport"', body)
        # Телефон: таблица по ширине письма, длинный ИИК переносится в своей ячейке, поля уже.
        self.assertIn('table-layout:fixed', body)
        self.assertEqual(body.count('overflow-wrap:anywhere'), 6, 'у каждой ячейки двух строк')
        self.assertIn('@media only screen and (max-width:480px)', body)
        self.assertIn('class="letter-card"', body)

    def test_order_follows_requisites(self):
        after = dict(BEFORE, kbe='19', name='ТОО «Новое имя»', bik='BETAKZKA')
        _subject, text, _body = requisites_mail.letter(BEFORE, after)
        positions = [text.index('— %s:' % label) for label in ('Наименование', 'БИК', 'КБЕ')]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('Изменение реквизитов ТОО «Новое имя»', requisites_mail.letter(BEFORE, after)[0])

    def test_html_is_escaped(self):
        after = dict(BEFORE, requisites='<script>alert(1)</script>\nКорр. счёт & прочее')
        _subject, text, body = requisites_mail.letter(BEFORE, after)
        self.assertNotIn('<script>', body)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;<br>Корр. счёт &amp; прочее', body)
        self.assertIn('— Прочие реквизиты: было «не указано», стало «<script>alert(1)</script>; Корр. счёт & прочее»',
                      text, 'многострочное значение — одной строкой через «;»')

    def test_name_with_line_break_still_makes_a_letter(self):
        subject, _text, _body = requisites_mail.letter(BEFORE, dict(BEFORE, name='ТОО «Наша\nкомпания»\r\nBcc: x@y.kz'))
        self.assertEqual(subject, 'Изменение реквизитов ТОО «Наша компания» Bcc: x@y.kz')
        message = mail.build_message(mailbox.config(GMAIL), to=['a@x.kz'], subject=subject, text='t', html=None,
                                     display_name='ТОО «Наша компания» Bcc: x@y.kz')
        self.assertNotIn('\nBcc', message.as_string(), 'перенос строки в заголовок не попадает')

    def test_without_bin_and_name(self):
        subject, text, _body = requisites_mail.letter({}, {'iik': 'KZ1'})
        self.assertEqual(subject, 'Изменение реквизитов нашей компании')
        self.assertIn('Сообщаем об изменении реквизитов нашей компании.', text)


class MessageTests(unittest.TestCase):
    def parsed(self, message):
        return BytesParser(policy=policy.default).parsebytes(message.as_bytes(policy=policy.SMTP))

    def test_headers_and_parts(self):
        cfg = mailbox.config(GMAIL)._replace(reply_to='finance@company.kz')
        subject, text, body = requisites_mail.letter(BEFORE, dict(BEFORE, iik='KZ22'))
        message = self.parsed(mail.build_message(cfg, to=['buh@romashka.kz'], subject=subject, text=text, html=body,
                                                 display_name='ТОО «Наша компания»'))
        self.assertEqual(message['Subject'], subject)
        self.assertEqual(message['To'], 'buh@romashka.kz')
        self.assertEqual(message['From'].addresses[0].display_name, 'ТОО «Наша компания»')
        self.assertEqual(message['From'].addresses[0].addr_spec, 'buh@company.kz')
        self.assertEqual(message['Reply-To'], 'finance@company.kz')
        self.assertEqual(message['Auto-Submitted'], 'auto-generated')
        self.assertEqual(message['X-Auto-Response-Suppress'], 'OOF, AutoReply')
        self.assertEqual(message['Date'].datetime.utcoffset().total_seconds(), 0, 'Date есть и в UTC')
        self.assertTrue(message['Message-ID'].endswith('@company.kz>'))
        self.assertEqual([part.get_content_type() for part in message.iter_parts()], ['text/plain', 'text/html'])
        self.assertIn('KZ22', message.get_body(('plain',)).get_content())

    def test_sender_is_the_company_and_its_mailbox(self):
        message = self.parsed(mail.build_message(mailbox.config(GMAIL), to=['a@x.kz'], subject='s', text='t', html=None,
                                                 display_name='ТОО «Наша компания»'))
        self.assertEqual(message['From'].addresses[0].display_name, 'ТОО «Наша компания»')
        self.assertEqual(message['From'].addresses[0].addr_spec, 'buh@company.kz')
        self.assertIsNone(message['Reply-To'], 'ответы поставщиков — в тот же ящик')
        self.assertEqual(message.get_content_type(), 'text/plain')


class RefusalKindTests(unittest.TestCase):
    def test_sender_trouble_is_not_the_address(self):
        cases = [
            (550, '5.4.5 Daily user sending quota exceeded', 'throttled'),
            (550, 'Daily sending limit exceeded for this account', 'throttled'),
            (554, '5.7.1 Message rejected under suspicion of SPAM', 'throttled'),
            (421, '4.7.0 Try again later, closing connection', 'throttled'),
            (451, 'Ratelimit: too many messages, try later', 'throttled'),
            (552, '5.2.2 The email account that you tried to reach is over quota', 'permanent'),
            (550, '5.1.1 No such user here', 'permanent'),
            (451, '4.3.0 Temporary server error', 'temporary'),
            (None, '', 'permanent'),
        ]
        for code, text, kind in cases:
            with self.subTest(text):
                self.assertEqual(mail.refusal_kind(code, text), kind)


class _FakeSock:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class _FakeSMTP:
    """Почтовый сервер на месте smtplib: что он ответит — задаёт тест.

    `auth_errors` — способ входа → ошибка; `close_after_refusal` — как Gmail: отказал в
    пароле и закрыл соединение, следующий способ входа уже не спросить."""
    instances = []
    connect_error = None
    auth_methods = 'LOGIN PLAIN XOAUTH2'
    auth_errors = {}
    close_after_refusal = False
    send_errors = []
    refused_next = {}

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls = []
        self.sock = _FakeSock()
        self.tls_context = None
        self.ehlo_resp = None
        self.esmtp_features = {}
        _FakeSMTP.instances.append(self)
        if _FakeSMTP.connect_error:
            raise _FakeSMTP.connect_error

    def ehlo(self):
        self.calls.append('ehlo')
        self.ehlo_resp = b'smtp.test'
        self.esmtp_features = {'auth': _FakeSMTP.auth_methods} if _FakeSMTP.auth_methods else {}

    def ehlo_or_helo_if_needed(self):
        if self.ehlo_resp is None:
            self.ehlo()

    def has_extn(self, name):
        return name.lower() in self.esmtp_features

    def starttls(self, context=None):
        self.tls_context = context
        self.calls.append('starttls')

    def auth_plain(self, challenge=None):
        return '\0%s\0%s' % (self.user, self.password)

    auth_login = auth_plain
    auth_cram_md5 = auth_plain

    def auth(self, mechanism, authobject, *, initial_response_ok=True):
        if self.sock is None:
            raise smtplib.SMTPServerDisconnected('Connection unexpectedly closed')
        self.calls.append(('auth', mechanism, self.user))
        authobject().encode('ascii')          # smtplib кодирует логин и пароль в ASCII
        error = _FakeSMTP.auth_errors.get(mechanism)
        if error:
            if _FakeSMTP.close_after_refusal:
                self.sock = None
            raise error
        return 235, b'2.7.0 Accepted'

    def send_message(self, message):
        self.calls.append(('send', message['To']))
        if _FakeSMTP.send_errors:
            error = _FakeSMTP.send_errors.pop(0)
            codes = [getattr(error, 'smtp_code', None)] + [item[0] for item in getattr(error, 'recipients', {}).values()]
            if 421 in codes:
                self.sock = None      # так поступает smtplib, получив 421
            raise error
        return dict(_FakeSMTP.refused_next)

    def quit(self):
        self.calls.append('quit')

    def close(self):
        self.calls.append('close')


class TransportTests(unittest.TestCase):
    def setUp(self):
        _FakeSMTP.instances = []
        _FakeSMTP.connect_error = None
        _FakeSMTP.auth_methods = 'LOGIN PLAIN XOAUTH2'
        _FakeSMTP.auth_errors = {}
        _FakeSMTP.close_after_refusal = False
        _FakeSMTP.send_errors = []
        _FakeSMTP.refused_next = {}
        for name in ('SMTP', 'SMTP_SSL'):
            patcher = mock.patch.object(smtplib, name, _FakeSMTP)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cfg = mailbox.config(GMAIL)
        self.message = mail.build_message(self.cfg, to=['a@x.kz'], subject='s', text='t', html=None)

    def test_ssl_login_send_quit(self):
        with mail.Transport(self.cfg) as smtp:
            self.assertTrue(smtp.send(self.message).startswith('<'))
            self.assertFalse(smtp.closed)
            smtp.deadline(7)
            sock = smtp.smtp.sock
        server = _FakeSMTP.instances[0]
        self.assertEqual((server.host, server.port, server.timeout), ('smtp.gmail.com', 465, mail.TIMEOUT_SECONDS))
        self.assertIsNotNone(server.context, 'SSL проверяет сертификат сервера')
        self.assertEqual(server.calls, ['ehlo', ('auth', 'PLAIN', 'buh@company.kz'), ('send', 'a@x.kz'), 'quit'],
                         'вход — способом PLAIN, как у smtplib.login')
        self.assertEqual(sock.timeouts, [7.0, mail.QUIT_SECONDS], 'остаток бюджета, затем короткий QUIT')

    def test_starttls(self):
        with mail.Transport(mailbox.config(GMAIL._replace(port=587, security='starttls'))) as smtp:
            smtp.send(self.message)
        server = _FakeSMTP.instances[0]
        self.assertEqual(server.calls[:4], ['ehlo', 'starttls', 'ehlo', ('auth', 'PLAIN', 'buh@company.kz')])
        self.assertIsNotNone(server.tls_context)

    def test_login_refused_is_transport(self):
        _FakeSMTP.auth_errors = {'PLAIN': smtplib.SMTPAuthenticationError(535, b'5.7.8 Username and Password not accepted'),
                                 'LOGIN': smtplib.SMTPAuthenticationError(535, b'5.7.8 Username and Password not accepted')}
        with self.assertRaises(mail.MailError) as caught:
            with mail.Transport(self.cfg):
                pass
        self.assertEqual((caught.exception.kind, caught.exception.reason), ('transport', 'login'))
        self.assertIn('логин или пароль приложения', str(caught.exception))
        self.assertIn('535 5.7.8 Username and Password not accepted', str(caught.exception))

    def test_refused_password_survives_a_closed_connection(self):
        """Gmail (21.10.2026): 535 на PLAIN — и соединение закрыто. smtplib.login пробовал бы
        LOGIN и падал «Connection unexpectedly closed»; ответом остаётся отказ в пароле."""
        _FakeSMTP.auth_errors = {'PLAIN': smtplib.SMTPAuthenticationError(
            535, b'5.7.8 Username and Password not accepted. For more information, go to\n5.7.8  https://support.google.com/mail/?p=BadCredentials')}
        _FakeSMTP.close_after_refusal = True
        with self.assertRaises(mail.MailError) as caught:
            with mail.Transport(self.cfg):
                pass
        self.assertEqual((caught.exception.kind, caught.exception.reason), ('transport', 'login'))
        self.assertIn('535 5.7.8 Username and Password not accepted', str(caught.exception))
        self.assertEqual([call for call in _FakeSMTP.instances[0].calls if call[0] == 'auth'],
                         [('auth', 'PLAIN', 'buh@company.kz')], 'соединение закрыто — второй способ не спрашивали')

    def test_second_method_is_tried_while_the_connection_lives(self):
        _FakeSMTP.auth_errors = {'PLAIN': smtplib.SMTPAuthenticationError(504, b'5.5.4 Unrecognized authentication type')}
        with mail.Transport(self.cfg) as smtp:
            smtp.send(self.message)
        self.assertEqual([call for call in _FakeSMTP.instances[0].calls if call[0] == 'auth'],
                         [('auth', 'PLAIN', 'buh@company.kz'), ('auth', 'LOGIN', 'buh@company.kz')])

    def test_password_not_in_latin_is_a_password_problem(self):
        cfg = self.cfg._replace(password='пароль')
        with self.assertRaises(mail.MailError) as caught:
            with mail.Transport(cfg):
                pass
        self.assertEqual(caught.exception.reason, 'login')
        self.assertIn('не латиницей', str(caught.exception))

    def test_server_without_password_login(self):
        _FakeSMTP.auth_methods = ''
        with self.assertRaises(mail.MailError) as caught:
            with mail.Transport(self.cfg):
                pass
        self.assertEqual(caught.exception.reason, 'connect')
        self.assertIn('Сервер не предлагает вход по паролю', str(caught.exception))

    def test_no_connection_is_transport(self):
        _FakeSMTP.connect_error = TimeoutError('timed out')
        with self.assertRaises(mail.MailError) as caught:
            with mail.Transport(self.cfg):
                pass
        self.assertEqual((caught.exception.kind, caught.exception.reason), ('transport', 'connect'))
        self.assertIn('Нет связи с почтовым сервером smtp.gmail.com:465', str(caught.exception))

    def send_failure(self, error):
        _FakeSMTP.send_errors = [error]
        with mail.Transport(self.cfg) as smtp:
            with self.assertRaises(mail.MailError) as caught:
                smtp.send(self.message)
            return caught.exception, smtp.closed

    def test_send_failures_by_kind(self):
        error, closed = self.send_failure(smtplib.SMTPRecipientsRefused({'a@x.kz': (550, b'5.1.1 No such user')}))
        self.assertEqual((error.kind, closed), ('permanent', False))
        self.assertIn('550 5.1.1 No such user', str(error))
        error, _closed = self.send_failure(smtplib.SMTPRecipientsRefused({'a@x.kz': (450, b'Mailbox busy')}))
        self.assertEqual(error.kind, 'temporary')
        error, _closed = self.send_failure(smtplib.SMTPRecipientsRefused({'a@x.kz': (550, b'5.4.5 Daily user sending quota exceeded')}))
        self.assertEqual(error.kind, 'throttled', 'лимит ящика — не вина адреса')
        error, _closed = self.send_failure(smtplib.SMTPDataError(554, b'5.7.1 Message rejected under suspicion of SPAM'))
        self.assertEqual(error.kind, 'throttled')
        error, _closed = self.send_failure(smtplib.SMTPDataError(554, b'Message too big'))
        self.assertEqual(error.kind, 'permanent')
        error, _closed = self.send_failure(smtplib.SMTPDataError(451, b'Try again later'))
        self.assertEqual(error.kind, 'temporary')
        error, _closed = self.send_failure(smtplib.SMTPSenderRefused(553, b'Sender not allowed', 'buh@company.kz'))
        self.assertEqual((error.kind, error.reason), ('transport', 'sender'), 'ящик не пускает отправителя — дело в настройке')
        error, closed = self.send_failure(smtplib.SMTPServerDisconnected('gone'))
        self.assertEqual((error.kind, closed), ('temporary', True), 'оборвалась связь — остальные ждут следующего прогона')

    def test_421_closes_the_connection(self):
        error, closed = self.send_failure(smtplib.SMTPRecipientsRefused({'a@x.kz': (421, b'4.7.0 Try again later, closing connection')}))
        self.assertEqual((error.kind, closed), ('throttled', True),
                         'smtplib закрыл сокет сам — следующий адрес не тратит попытку на «соединение закрыто»')

    def test_partly_refused(self):
        _FakeSMTP.refused_next = {'a@x.kz': (550, b'No such user')}
        with mail.Transport(self.cfg) as smtp:
            with self.assertRaises(mail.MailError) as caught:
                smtp.send(self.message)
        self.assertEqual(caught.exception.kind, 'permanent')


class RunGuardTests(unittest.TestCase):
    """Прогон планировщика без базы: что он трогает, пока писать нечего или некому."""

    def setUp(self):
        self.db = mock.MagicMock()
        for name, value in (('_reap_stuck', None), ('_finish', 0)):
            patcher = mock.patch.object(requisites_mail, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.transport = mock.MagicMock()

    def run_with(self, due, boxes, clock=lambda: 0.0):
        with mock.patch.object(requisites_mail, '_due_mailings', return_value=due), \
                mock.patch.object(requisites_mail.mailbox, 'outboxes', return_value=boxes) as outboxes:
            return requisites_mail.run(self.db, transport=self.transport, clock=clock), outboxes

    def test_nothing_due_touches_no_mailbox(self):
        result, outboxes = self.run_with([], {})
        self.assertEqual(result, {'idle': True, 'finished': 0})
        outboxes.assert_not_called()
        self.transport.assert_not_called()

    def test_company_without_mailbox_waits(self):
        result, outboxes = self.run_with([(9, 3)], {})
        self.assertEqual((result['without_mailbox'], result['sent']), (1, 0))
        self.assertEqual(outboxes.call_args.args[1], [3])
        self.transport.assert_not_called()

    def test_paused_mailbox_is_not_knocked(self):
        with mock.patch.dict(requisites_mail._PAUSE, {mailbox.pause_key(GMAIL): 1000.0}, clear=True):
            result, _outboxes = self.run_with([(9, 3)], {3: GMAIL}, clock=lambda: 999.0)
        self.assertEqual(result['paused'], 1)
        self.transport.assert_not_called()


class SchedulerWiringTests(unittest.TestCase):
    def test_job_runs_every_minute_in_pool(self):
        source = (Path(__file__).resolve().parents[1] / 'bot_schedule2.py').read_text(encoding='utf-8')
        start = source.index("id='payments_requisites_mail_minutely'")
        block = source[source.rindex('scheduler.add_job(', 0, start):source.index(')', start)]
        self.assertIn('payments_requisites_mail_async', block)
        self.assertIn("CronTrigger(minute='*'", block)
        self.assertIn('max_instances=1', source[start:start + 200])
        job = source[source.index('def payments_requisites_mail_job'):source.index('async def payments_requisites_mail_async')]
        self.assertIn('payments_requisites_mail.run(db)', job)
        wrapper = source[source.index('async def payments_requisites_mail_async'):][:400]
        self.assertIn('run_in_executor(executor_pool, payments_requisites_mail_job)', wrapper)

    def test_contact_emails_adopted_at_startup(self):
        source = (Path(__file__).resolve().parents[1] / 'payments' / 'schema.py').read_text(encoding='utf-8')
        init = source[source.index('def init_payments_schema'):source.index('def schema_ready')]
        self.assertIn('requisites_mail.adopt_contact_emails(cursor)', init)
        self.assertIn('SAVEPOINT payments_contact_emails', init)
        # Адреса из поля «Почта» — строками «Почты поставщиков», после переноса из «Контакта».
        self.assertLess(init.index('adopt_contact_emails'), init.index('directory.adopt_email_array(cursor)'))
        self.assertIn('SAVEPOINT payments_counterparty_emails', init)


class DictionaryRoutesTests(unittest.TestCase):
    """Проводка ручек справочников: почта поставщика и письмо после правки компании."""

    def client(self, can_edit=True):
        queries = mock.MagicMock()
        queries.load_access_context.return_value = {'user_id': ADMIN, 'name': 'Админ Раздела', 'role': 'admin'}
        queries.roles_of_user.return_value = set()
        self.directory = mock.MagicMock()
        self.directory.find_duplicate_name.return_value = None
        self.directory.requisites_text.return_value = 'реквизиты'
        self.mailing = mock.MagicMock()
        self.mailing.MailingError = requisites_mail.MailingError
        self.mailing.SEND_DELAY_MINUTES = requisites_mail.SEND_DELAY_MINUTES
        self.mailing.TRANSPORT_PAUSE_SECONDS = requisites_mail.TRANSPORT_PAUSE_SECONDS
        self.mailing.history.return_value = ([], 0)
        self.mailing.known_now.return_value = {}
        self.mailing.recipients_overview.return_value = {'addresses': 2}
        self.mailing.states.return_value = ({3: {'id': 9, 'status': 'pending'}}, {3: 4})
        self.mailing.waiting_for_mailbox.return_value = []
        self.box = mock.MagicMock()
        self.box.MailboxError = mailbox.MailboxError
        self.box.UNREADABLE = mailbox.UNREADABLE
        self.box.works.side_effect = mailbox.works
        self.box.view.side_effect = lambda row: row
        self.box.read.return_value = None
        self.box.views.return_value = {}
        self.box.local_allowed.return_value = False
        patches = [mock.patch.object(routes, 'queries', queries), mock.patch.object(routes_directory, 'queries', queries),
                   mock.patch.object(routes_directory, 'directory', self.directory),
                   mock.patch.object(routes_directory, 'requisites_mail', self.mailing),
                   mock.patch.object(routes_directory, 'mailbox', self.box)]
        if not can_edit:
            patches.append(mock.patch.object(access, 'can_save_dictionary', return_value=False))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (ADMIN, None, None)))
        return app.test_client()

    def test_supplier_email_is_parsed(self):
        client = self.client()
        self.directory.upsert_counterparty.return_value = 7
        response = client.post('/api/payments/dictionaries/counterparties',
                               json={'id': 7, 'name': 'ТОО «Ромашка»', 'emails': 'Buh@Romashka.kz; info@romashka.kz'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        fields = self.directory.upsert_counterparty.call_args.kwargs['fields']
        self.assertNotIn('emails', fields, 'карточка почту не пишет — это строки «Почты поставщиков»')
        args = self.directory.replace_counterparty_emails.call_args
        self.assertEqual((args.args[1:], args.kwargs), ((7, ['buh@romashka.kz', 'info@romashka.kz']), {'actor_id': ADMIN}))
        self.assertNotIn('mailing', response.get_json(), 'правка поставщика писем не вызывает')
        self.mailing.after_entity_saved.assert_not_called()

    def test_old_window_without_email_field_keeps_emails(self):
        client = self.client()
        self.directory.upsert_counterparty.return_value = 7
        response = client.post('/api/payments/dictionaries/counterparties', json={'id': 7, 'name': 'ТОО «Ромашка»'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.directory.replace_counterparty_emails.assert_not_called()
        client.post('/api/payments/dictionaries/counterparties', json={'id': 7, 'name': 'ТОО «Ромашка»', 'emails': ''})
        self.assertEqual(self.directory.replace_counterparty_emails.call_args.args[1:], (7, []),
                         'пустое поле — адреса убрали')

    def test_supplier_bad_email_is_refused(self):
        client = self.client()
        response = client.post('/api/payments/dictionaries/counterparties',
                               json={'id': 7, 'name': 'ТОО «Ромашка»', 'emails': 'buh@romashka'})
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertEqual((body['code'], body['field']), ('PAYMENTS_EMAIL_INVALID', 'emails'))
        self.assertEqual(body['error'], 'Почта: «buh@romashka» — не похоже на адрес почты')
        self.directory.upsert_counterparty.assert_not_called()

    def test_entity_save_locks_before_and_schedules_letter(self):
        client = self.client()
        before = dict(BEFORE, id=3)
        after = dict(BEFORE, id=3, iik='KZ22')
        self.directory.read_legal_entity.side_effect = lambda cursor, entity_id, lock=False: before if lock else after
        self.directory.upsert_legal_entity.return_value = 3
        self.mailing.after_entity_saved.return_value = {'id': 9, 'status': 'pending'}
        response = client.post('/api/payments/dictionaries/legal_entities', json=dict(after, iik='KZ22'))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['mailing'], {'id': 9, 'status': 'pending'})
        reads = self.directory.read_legal_entity.call_args_list
        self.assertTrue(reads[0].kwargs.get('lock'), '«как было» читается под блокировкой и до записи')
        args = self.mailing.after_entity_saved.call_args.args
        self.assertEqual((args[1], args[2], args[3], args[4]), (3, before, after, {'id': ADMIN, 'name': 'Админ Раздела'}))

    def test_new_entity_sends_nothing(self):
        client = self.client()
        self.directory.upsert_legal_entity.return_value = 4
        response = client.post('/api/payments/dictionaries/legal_entities', json={'name': 'ТОО «Новая»'})
        self.assertEqual(response.status_code, 200)
        self.mailing.after_entity_saved.assert_not_called()
        self.assertNotIn('mailing', response.get_json())

    def test_entity_list_carries_letters_for_editors(self):
        client = self.client()
        self.directory.list_legal_entities.return_value = [{'id': 3, 'name': 'ТОО «Наша»'}, {'id': 4, 'name': 'ИП Тестов'}]
        self.box.views.return_value = {3: {'address': 'buh@company.kz', 'problem': None}}
        body = client.get('/api/payments/dictionaries/legal_entities').get_json()
        self.assertEqual([(row['id'], row['mailing'], row['mailings_count'], row['mailbox']) for row in body['items']],
                         [(3, {'id': 9, 'status': 'pending'}, 4, {'address': 'buh@company.kz', 'problem': None}),
                          (4, None, 0, None)], 'почта — у каждой компании своя, в её строке')
        self.assertEqual(body['mail'], {'recipients': {'addresses': 2}, 'delay_minutes': 10, 'pause_minutes': 10})

    def test_entity_list_hides_letters_from_readers(self):
        client = self.client(can_edit=False)
        self.directory.list_legal_entities.return_value = [{'id': 3, 'name': 'ТОО «Наша»'}]
        body = client.get('/api/payments/dictionaries/legal_entities').get_json()
        self.assertNotIn('mail', body)
        self.assertNotIn('mailing', body['items'][0])
        self.assertNotIn('mailbox', body['items'][0])
        self.mailing.states.assert_not_called()
        self.box.views.assert_not_called()

    def test_mailings_reply(self):
        client = self.client()
        self.directory.read_legal_entity.return_value = {'id': 3}
        self.mailing.history.return_value = ([{'id': 9}], 12)
        self.mailing.known_now.return_value = {'iik': 'KZ11'}
        body = client.get('/api/payments/dictionaries/legal_entities/3/mailings').get_json()
        self.assertEqual((body['items'], body['total'], body['known']), ([{'id': 9}], 12, {'iik': 'KZ11'}))
        self.assertEqual((body['sender'], body['mailbox'], body['mail']['ready']),
                         ({'name': '', 'address': None}, None, False), 'почты у компании нет — письма ждут')
        self.box.read.return_value = {'address': 'buh@company.kz', 'problem': None}
        self.directory.read_legal_entity.return_value = {'id': 3, 'name': 'ТОО  «Наша»'}
        body = client.get('/api/payments/dictionaries/legal_entities/3/mailings').get_json()
        self.assertEqual((body['sender'], body['mail']['ready']),
                         ({'name': 'ТОО «Наша»', 'address': 'buh@company.kz'}, True))
        self.mailing.retry.return_value = {'queued': 2, 'removed': 1}
        body = client.post('/api/payments/dictionaries/legal_entities/3/mailings/9/retry').get_json()
        self.assertEqual((body['status'], body['queued'], body['removed']), ('success', 2, 1))
        self.assertEqual(self.mailing.retry.call_args.args[1:], (3, 9, {'id': ADMIN, 'name': 'Админ Раздела'}))

    def test_letters_journal_and_one_letter(self):
        client = self.client()
        self.mailing.journal.return_value = ([{'id': 9}], 31)
        body = client.get('/api/payments/mailings?limit=500&offset=-3').get_json()
        self.assertEqual((body['items'], body['total'], body['limit'], body['offset']), ([{'id': 9}], 31, 100, 0))
        self.assertEqual(self.mailing.journal.call_args.kwargs, {'limit': 100, 'offset': 0})
        self.assertEqual(body['mail']['recipients'], {'addresses': 2})
        self.assertEqual(body['waiting_mailbox'], [])
        self.mailing.detail.return_value = {'id': 9, 'legal_entity_id': 3, 'legal_entity_name': 'ТОО «Наша»'}
        self.box.read.return_value = {'address': 'buh@company.kz', 'problem': None}
        body = client.get('/api/payments/mailings/9').get_json()
        self.assertEqual((body['item']['id'], body['sender']), (9, {'name': 'ТОО «Наша»', 'address': 'buh@company.kz'}))
        self.assertTrue(body['mail']['ready'])
        self.assertEqual(self.box.read.call_args.args[1], 3, 'отправитель — почта компании этого письма')
        self.box.read.return_value = {'address': 'buh@company.kz', 'problem': 'Почта не приняла пароль'}
        self.assertFalse(client.get('/api/payments/mailings/9').get_json()['mail']['ready'], 'почта с бедой — письма ждут')
        self.box.read.return_value = None
        body = client.get('/api/payments/mailings/9').get_json()
        self.assertEqual(body['sender'], {'name': 'ТОО «Наша»', 'address': None}, 'без почты — адреса отправителя нет')
        self.assertFalse(body['mail']['ready'])
        self.mailing.detail.side_effect = requisites_mail.MailingError('Письмо не найдено', status=404,
                                                                       code='PAYMENT_MAILING_NOT_FOUND')
        self.assertEqual(client.get('/api/payments/mailings/10').status_code, 404)

    # ── 21.10.2026: почта компании, галочки «Кому», «Почта поставщиков» ────

    def test_mailbox_connect_verifies_then_saves(self):
        client = self.client()
        self.directory.read_legal_entity.return_value = {'id': 3, 'name': 'ТОО «Наша»'}
        self.box.prepare.return_value = GMAIL
        saved = {'address': 'buh@company.kz', 'label': 'Gmail', 'problem': None}
        self.box.read.side_effect = [None, saved]
        body = client.post('/api/payments/dictionaries/legal_entities/3/mailbox',
                           json={'address': 'buh@company.kz', 'password': 'app-password'}).get_json()
        self.assertEqual((body['status'], body['mailbox']), ('success', saved))
        self.assertEqual(self.box.prepare.call_args.args[0], {'address': 'buh@company.kz', 'password': 'app-password'})
        self.assertEqual(self.box.prepare.call_args.kwargs, {'current': None, 'allow_local': False})
        self.assertEqual(self.box.verify.call_args.args, (GMAIL, 'ТОО «Наша»'))
        self.assertEqual(self.box.save.call_args.args[1:], (3, GMAIL, {'id': ADMIN, 'name': 'Админ Раздела'}))
        self.assertNotIn('app-password', json.dumps(body, ensure_ascii=False), 'пароль наружу не уходит')

    def test_mailbox_refusal_is_red_at_its_field(self):
        client = self.client()
        self.directory.read_legal_entity.return_value = {'id': 3, 'name': 'ТОО «Наша»'}
        self.box.prepare.return_value = GMAIL
        self.box.verify.side_effect = mailbox.MailboxError('Почта не приняла пароль.', field='password',
                                                           code='PAYMENTS_MAILBOX_LOGIN',
                                                           help_url='https://myaccount.google.com/apppasswords')
        response = client.post('/api/payments/dictionaries/legal_entities/3/mailbox',
                               json={'address': 'buh@company.kz', 'password': 'wrong'})
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertEqual((body['code'], body['field'], body['help_url']),
                         ('PAYMENTS_MAILBOX_LOGIN', 'password', 'https://myaccount.google.com/apppasswords'))
        self.box.save.assert_not_called()
        self.directory.read_legal_entity.return_value = None
        self.assertEqual(client.post('/api/payments/dictionaries/legal_entities/3/mailbox', json={}).status_code, 404)

    def test_mailbox_check_marks_a_broken_password(self):
        client = self.client()
        self.directory.read_legal_entity.return_value = {'id': 3, 'name': 'ТОО «Наша»'}
        self.box.read.return_value = None
        response = client.post('/api/payments/dictionaries/legal_entities/3/mailbox/check')
        self.assertEqual((response.status_code, response.get_json()['code']), (404, 'PAYMENTS_MAILBOX_MISSING'))
        self.box.read.return_value = {'address': 'buh@company.kz', 'problem': None}
        self.box.settings_of.return_value = GMAIL
        self.assertEqual(client.post('/api/payments/dictionaries/legal_entities/3/mailbox/check').status_code, 200)
        self.assertEqual(self.box.note_ok.call_args.args[1], 3)
        self.box.verify.side_effect = mailbox.MailboxError('Почта не приняла пароль.', field='password',
                                                           code='PAYMENTS_MAILBOX_LOGIN')
        response = client.post('/api/payments/dictionaries/legal_entities/3/mailbox/check')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.box.note_problem.call_args.args[1:], (3, 'Почта не приняла пароль.'))
        self.box.note_problem.reset_mock()
        self.box.verify.side_effect = mailbox.MailboxError('Не удалось связаться', code='PAYMENTS_MAILBOX_CONNECT')
        client.post('/api/payments/dictionaries/legal_entities/3/mailbox/check')
        self.box.note_problem.assert_not_called()   # сеть — не беда ящика
        self.box.settings_of.return_value = None
        response = client.post('/api/payments/dictionaries/legal_entities/3/mailbox/check')
        self.assertEqual(response.get_json()['field'], 'password')
        self.assertEqual(self.box.note_problem.call_args.args[2], mailbox.UNREADABLE)

    def test_mailbox_detect_and_remove(self):
        client = self.client()
        self.box.detect.return_value = {'address': 'buh@company.kz', 'server': {'label': 'Gmail'}, 'guess': None}
        body = client.post('/api/payments/dictionaries/legal_entities/3/mailbox/detect',
                           json={'address': 'buh@company.kz'}).get_json()
        self.assertEqual(body['server'], {'label': 'Gmail'})
        self.directory.read_legal_entity.return_value = {'id': 3}
        body = client.delete('/api/payments/dictionaries/legal_entities/3/mailbox').get_json()
        self.assertEqual((body['status'], body['mailbox']), ('success', None))
        self.assertEqual(self.box.remove.call_args.args[1], 3)

    def test_recipients_checkmarks(self):
        client = self.client()
        self.mailing.set_recipients.return_value = {'excluded': 2}
        body = client.post('/api/payments/dictionaries/legal_entities/3/mailings/9/recipients',
                           json={'excluded': [5, '7']}).get_json()
        self.assertEqual((body['status'], body['excluded']), ('success', 2))
        self.assertEqual(self.mailing.set_recipients.call_args.args[1:],
                         (3, 9, {'id': ADMIN, 'name': 'Админ Раздела'}, [5, 7]))
        self.assertEqual(client.post('/api/payments/dictionaries/legal_entities/3/mailings/9/recipients',
                                     json={'excluded': 'все'}).status_code, 400)
        client.post('/api/payments/dictionaries/legal_entities/3/mailings/9/recipients', json={})
        self.assertEqual(self.mailing.set_recipients.call_args.args[4], [], 'без списка — отмечены все')

    def test_supplier_email_directory_save(self):
        client = self.client()
        self.directory.read_counterparty.return_value = {'id': 7}
        self.directory.find_counterparty_email.return_value = None
        self.directory.count_counterparty_emails.return_value = 1
        self.directory.upsert_counterparty_email.return_value = 41
        body = client.post('/api/payments/dictionaries/counterparty_emails',
                           json={'counterparty_id': 7, 'email': ' Buh@Romashka.KZ ', 'contact_name': 'Айгерим',
                                 'note': ''}).get_json()
        self.assertEqual(body, {'status': 'success', 'id': 41})
        kwargs = self.directory.upsert_counterparty_email.call_args.kwargs
        self.assertEqual(kwargs['fields'], {'counterparty_id': 7, 'email': 'buh@romashka.kz', 'contact_name': 'Айгерим',
                                            'note': None})
        cases = (({'email': 'buh@romashka.kz'}, None, None, 400, 'counterparty_id'),
                 ({'counterparty_id': 7, 'email': 'buh@romashka'}, None, None, 400, 'email'),
                 ({'counterparty_id': 7, 'email': 'a@x.kz, b@x.kz'}, None, None, 400, 'email'),
                 ({'counterparty_id': 7, 'email': 'buh@romashka.kz'}, 40, None, 409, 'email'),
                 ({'counterparty_id': 7, 'email': 'buh@romashka.kz'}, None, mail.MAX_EMAILS, 400, 'email'))
        for payload, duplicate, count, status, field in cases:
            with self.subTest(payload):
                self.directory.read_counterparty.return_value = {'id': 7} if payload.get('counterparty_id') else None
                self.directory.find_counterparty_email.return_value = duplicate
                self.directory.count_counterparty_emails.return_value = count or 0
                response = client.post('/api/payments/dictionaries/counterparty_emails', json=payload)
                self.assertEqual((response.status_code, response.get_json().get('field')), (status, field))
        self.directory.DICTIONARIES = directory.DICTIONARIES
        self.directory.usage.return_value = []
        self.directory.delete_row.return_value = 1
        self.assertEqual(client.delete('/api/payments/dictionaries/counterparty_emails/41').status_code, 200)
        self.assertEqual(self.directory.delete_row.call_args.args[1:], ('counterparty_emails', 41))

    # ── «Войти через Google» ──────────────────────────────────────────────

    GOOGLE = {'client_id': 'cid', 'client_secret': 'secret', 'redirect_uri': 'https://api.example/cb',
              'auth_url': 'https://accounts.google.com/o/oauth2/v2/auth', 'token_url': 'https://oauth2.googleapis.com/token',
              'send_url': 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'}

    def test_google_start_gives_a_signed_link(self):
        from urllib.parse import parse_qs, urlsplit
        client = self.client()
        with mock.patch.object(routes_directory.google_mail, 'config', return_value=None):
            response = client.post('/api/payments/dictionaries/legal_entities/3/mailbox/google', json={})
        self.assertEqual((response.status_code, response.get_json()['code']), (503, 'PAYMENTS_GOOGLE_OFF'))
        self.directory.read_legal_entity.return_value = {'id': 3, 'name': 'ТОО «Наша»'}
        with mock.patch.dict('os.environ', {'JWT_SECRET': 'unit'}), \
                mock.patch.object(routes_directory.google_mail, 'config', return_value=self.GOOGLE):
            body = client.post('/api/payments/dictionaries/legal_entities/3/mailbox/google',
                               json={'address': 'Buh@Company.KZ', 'return_to': 'https://alfa330.github.io/OTP/?view=payments'},
                               headers={'Origin': 'https://alfa330.github.io'}).get_json()
            query = {key: value[0] for key, value in parse_qs(urlsplit(body['url']).query).items()}
            state = google_mail.read_state(query['state'])
            evil = client.post('/api/payments/dictionaries/legal_entities/3/mailbox/google',
                               json={'return_to': 'https://evil.example/steal'},
                               headers={'Origin': 'https://evil.example'}).get_json()
            evil_state = google_mail.read_state(parse_qs(urlsplit(evil['url']).query)['state'][0])
        self.assertEqual({key: state[key] for key in ('e', 'u', 'n', 'o', 'r')},
                         {'e': 3, 'u': ADMIN, 'n': 'Админ Раздела', 'o': 'https://alfa330.github.io',
                          'r': 'https://alfa330.github.io/OTP/?view=payments'})
        self.assertEqual(query['login_hint'], 'buh@company.kz')
        self.assertEqual((evil_state['o'], evil_state['r']), ('https://alfa330.github.io', ''),
                         'итог сообщается только окну портала')

    def google_callback(self, client, *, query, state=None, email='buh@company.kz'):
        tokens = {'access_token': 'at', 'refresh_token': 'rt-new'}
        with mock.patch.dict('os.environ', {'JWT_SECRET': 'unit'}), \
                mock.patch.object(routes_directory.google_mail, 'config', return_value=self.GOOGLE), \
                mock.patch.object(routes_directory.google_mail, 'exchange', return_value=tokens) as exchange, \
                mock.patch.object(routes_directory.google_mail, 'email_of', return_value=email), \
                mock.patch.object(routes_directory.google_mail, 'revoke') as revoke:
            if state is None:
                state = google_mail.sign_state({'e': 3, 'u': ADMIN, 'n': 'Админ Раздела',
                                                'o': 'https://alfa330.github.io', 'r': ''})
            response = client.get('/api/payments/mailbox/google/callback?' + query.replace('STATE', state))
        return response, exchange, revoke

    def test_google_callback_connects_the_company_mailbox(self):
        client = self.client()
        self.directory.read_legal_entity.return_value = {'id': 3, 'name': 'ТОО «Наша»'}
        self.box.OAUTH = 'oauth'
        self.box.google_settings.side_effect = mailbox.google_settings
        self.box.read.return_value = {'security': 'oauth', 'password_enc': 'old'}
        self.box.decrypt.return_value = 'rt-old'
        response, exchange, revoke = self.google_callback(client, query='state=STATE&code=CODE')
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn('Почта подключена', page)
        self.assertIn('buh@company.kz — с неё уйдут письма поставщикам о смене реквизитов ТОО «Наша»', page)
        self.assertIn('window.opener.postMessage(note,"https://alfa330.github.io")', page)
        self.assertEqual(exchange.call_args.args[1], 'CODE')
        settings = self.box.verify.call_args.args[0]
        self.assertEqual((settings.address, settings.security, settings.password), ('buh@company.kz', 'oauth', 'rt-new'))
        self.assertEqual(self.box.save.call_args.args[1:], (3, settings, {'id': ADMIN, 'name': 'Админ Раздела'}))
        revoke.assert_called_once_with('rt-old')

    def test_google_callback_refuses_what_it_should(self):
        client = self.client()
        self.directory.read_legal_entity.return_value = {'id': 3, 'name': 'ТОО «Наша»'}
        self.box.read.return_value = None
        cases = (('state=bad&code=CODE', 'Ссылка входа устарела'), ('state=STATE&error=access_denied', 'Вход отменён'))
        for query, words in cases:
            with self.subTest(words):
                response, exchange, _revoke = self.google_callback(client, query=query)
                self.assertIn(words, response.get_data(as_text=True))
                exchange.assert_not_called()
        self.box.verify.side_effect = mailbox.MailboxError('Gmail не пускает отправку: 403', code='PAYMENTS_MAILBOX_LOGIN')
        response, _exchange, _revoke = self.google_callback(client, query='state=STATE&code=CODE')
        self.assertIn('Gmail не пускает отправку: 403', response.get_data(as_text=True))
        self.box.save.assert_not_called()
        reader = self.client(can_edit=False)
        self.box.verify.side_effect = None
        response, _exchange, _revoke = self.google_callback(reader, query='state=STATE&code=CODE')
        self.assertIn('Нет прав', response.get_data(as_text=True))
        self.box.save.assert_not_called()

    def test_google_callback_page_does_not_break_out_of_its_script(self):
        client = self.client()
        self.directory.read_legal_entity.return_value = {'id': 3, 'name': '</script><script>alert(1)</script>'}
        self.box.read.return_value = None
        self.box.google_settings.side_effect = mailbox.google_settings
        response, _exchange, _revoke = self.google_callback(client, query='state=STATE&code=CODE')
        page = response.get_data(as_text=True)
        self.assertNotIn('<script>alert(1)', page)
        self.assertEqual(page.count('<script>'), 1)

    def test_removing_a_google_mailbox_revokes_access(self):
        client = self.client()
        self.directory.read_legal_entity.return_value = {'id': 3}
        self.box.OAUTH = 'oauth'
        self.box.read.return_value = {'security': 'oauth', 'password_enc': 'enc'}
        self.box.decrypt.return_value = 'rt'
        with mock.patch.object(routes_directory.google_mail, 'revoke') as revoke:
            self.assertEqual(client.delete('/api/payments/dictionaries/legal_entities/3/mailbox').status_code, 200)
        revoke.assert_called_once_with('rt')

    def test_mailing_errors_become_409(self):
        client = self.client()
        self.mailing.cancel.side_effect = requisites_mail.MailingError('Письмо уже отправлено')
        response = client.post('/api/payments/dictionaries/legal_entities/3/mailings/9/cancel')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error'], 'Письмо уже отправлено')

    def test_mailings_only_for_section_admin(self):
        client = self.client(can_edit=False)
        for method, path in (('get', '/api/payments/mailings'), ('get', '/api/payments/mailings/9'),
                             ('get', '/api/payments/dictionaries/legal_entities/3/mailings'),
                             ('post', '/api/payments/dictionaries/legal_entities/3/mailings/9/cancel'),
                             ('post', '/api/payments/dictionaries/legal_entities/3/mailings/9/retry'),
                             ('post', '/api/payments/dictionaries/legal_entities/3/mailings/9/recipients'),
                             ('get', '/api/payments/dictionaries/legal_entities/3/mailbox'),
                             ('post', '/api/payments/dictionaries/legal_entities/3/mailbox'),
                             ('post', '/api/payments/dictionaries/legal_entities/3/mailbox/detect'),
                             ('post', '/api/payments/dictionaries/legal_entities/3/mailbox/check'),
                             ('delete', '/api/payments/dictionaries/legal_entities/3/mailbox')):
            with self.subTest(path):
                response = getattr(client, method)(path)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.get_json()['code'], 'PAYMENTS_ADMIN_ONLY')
        self.mailing.cancel.assert_not_called()
        self.mailing.retry.assert_not_called()
        self.mailing.journal.assert_not_called()
        self.mailing.detail.assert_not_called()
        self.mailing.set_recipients.assert_not_called()
        self.box.verify.assert_not_called()
        self.box.save.assert_not_called()
        self.box.remove.assert_not_called()


if __name__ == '__main__':
    unittest.main()
