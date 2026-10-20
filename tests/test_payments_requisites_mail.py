# -*- coding: utf-8 -*-
"""«Оплата счетов»: почта поставщиков и письма о смене реквизитов наших компаний.

Просьба владельца 20.10.2026: «добавление эмайла контрагентов поставщиков и тд,
автомат рассылка по эмайлом при изменении реквизитов». Здесь — то, что видно без
базы: разбор адресов, настройка ящика, что считается сменой реквизитов, текст
письма, разбор ответов почтового сервера и проводка ручек справочников. Что
значат запросы (очередь, повторы, «Отправить ещё раз»), проверяет
tests/test_payments_requisites_mail_postgres.py на настоящем Postgres.

Случаи адресов и смены реквизитов — общие с tests/payments_requisites_mail.test.mjs
(tests/data/payments_requisites_mail_cases.json): форма и сервер решают одинаково.
"""

import json
import smtplib
import unittest
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from flask import Flask

from payments import access, directory, mail, requisites_mail, routes, routes_directory

CASES = json.loads((Path(__file__).resolve().parent / 'data' / 'payments_requisites_mail_cases.json')
                   .read_text(encoding='utf-8'))
ADMIN = access.SECTION_ADMIN_USER_IDS[0]

GMAIL = {'PAYMENTS_SMTP_HOST': 'smtp.gmail.com', 'PAYMENTS_SMTP_USER': 'buh@company.kz',
         'PAYMENTS_SMTP_PASSWORD': 'app-password'}


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


class ConfigTests(unittest.TestCase):
    def test_not_configured(self):
        self.assertIsNone(mail.config({}))
        self.assertFalse(mail.ready({}))
        for missing in GMAIL:
            with self.subTest(missing):
                self.assertIsNone(mail.config({k: v for k, v in GMAIL.items() if k != missing}))

    def test_ports_and_security(self):
        cfg = mail.config(GMAIL)
        self.assertEqual((cfg.port, cfg.security, cfg.sender), (465, 'ssl', 'buh@company.kz'))
        cfg = mail.config(dict(GMAIL, PAYMENTS_SMTP_PORT='587'))
        self.assertEqual((cfg.port, cfg.security), (587, 'starttls'))
        cfg = mail.config(dict(GMAIL, PAYMENTS_SMTP_PORT='2525', PAYMENTS_SMTP_SECURITY='ssl'))
        self.assertEqual((cfg.port, cfg.security), (2525, 'ssl'))
        self.assertIsNone(mail.config(dict(GMAIL, PAYMENTS_SMTP_PORT='порт')))
        self.assertIsNone(mail.config(dict(GMAIL, PAYMENTS_SMTP_SECURITY='tls13')))

    def test_plain_text_only_on_this_machine(self):
        self.assertIsNone(mail.config(dict(GMAIL, PAYMENTS_SMTP_SECURITY='none')),
                          'без шифрования пароль ушёл бы в сеть открытым текстом')
        local = mail.config({'PAYMENTS_SMTP_HOST': '127.0.0.1', 'PAYMENTS_SMTP_PORT': '1025',
                             'PAYMENTS_SMTP_SECURITY': 'none', 'PAYMENTS_MAIL_FROM': 'stand@local.kz'})
        self.assertEqual((local.security, local.user, local.sender), ('none', '', 'stand@local.kz'))
        self.assertIsNone(mail.config({'PAYMENTS_SMTP_HOST': 'smtp.gmail.com', 'PAYMENTS_MAIL_FROM': 'a@x.kz'}),
                          'без логина — только стенд без шифрования')

    def test_sender_and_reply_to(self):
        cfg = mail.config(dict(GMAIL, PAYMENTS_MAIL_FROM='Бухгалтерия <buh@company.kz>',
                               PAYMENTS_MAIL_REPLY_TO='finance@company.kz'))
        self.assertEqual((cfg.sender, cfg.reply_to), ('Бухгалтерия <buh@company.kz>', 'finance@company.kz'))
        self.assertIsNone(mail.config(dict(GMAIL, PAYMENTS_MAIL_FROM='не адрес')))
        self.assertIsNone(mail.config(dict(GMAIL, PAYMENTS_SMTP_PASSWORD='')), 'логин без пароля')


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
        message = mail.build_message(mail.config(GMAIL), to=['a@x.kz'], subject=subject, text='t', html=None,
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
        cfg = mail.config(dict(GMAIL, PAYMENTS_MAIL_REPLY_TO='finance@company.kz'))
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

    def test_own_sender_name_wins(self):
        cfg = mail.config(dict(GMAIL, PAYMENTS_MAIL_FROM='Бухгалтерия iCore <buh@company.kz>'))
        message = self.parsed(mail.build_message(cfg, to=['a@x.kz'], subject='s', text='t', html=None,
                                                 display_name='ТОО «Наша компания»'))
        self.assertEqual(message['From'].addresses[0].display_name, 'Бухгалтерия iCore')
        self.assertIsNone(message['Reply-To'])
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
    """Почтовый сервер на месте smtplib: что он ответит — задаёт тест."""
    instances = []
    connect_error = None
    login_error_next = None
    send_errors = []
    refused_next = {}

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls = []
        self.sock = _FakeSock()
        self.tls_context = None
        _FakeSMTP.instances.append(self)
        if _FakeSMTP.connect_error:
            raise _FakeSMTP.connect_error

    def ehlo(self):
        self.calls.append('ehlo')

    def starttls(self, context=None):
        self.tls_context = context
        self.calls.append('starttls')

    def login(self, user, password):
        self.calls.append(('login', user))
        if _FakeSMTP.login_error_next:
            raise _FakeSMTP.login_error_next

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
        _FakeSMTP.login_error_next = None
        _FakeSMTP.send_errors = []
        _FakeSMTP.refused_next = {}
        for name in ('SMTP', 'SMTP_SSL'):
            patcher = mock.patch.object(smtplib, name, _FakeSMTP)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cfg = mail.config(GMAIL)
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
        self.assertEqual(server.calls, [('login', 'buh@company.kz'), ('send', 'a@x.kz'), 'quit'])
        self.assertEqual(sock.timeouts, [7.0, mail.QUIT_SECONDS], 'остаток бюджета, затем короткий QUIT')

    def test_starttls(self):
        with mail.Transport(mail.config(dict(GMAIL, PAYMENTS_SMTP_PORT='587'))) as smtp:
            smtp.send(self.message)
        server = _FakeSMTP.instances[0]
        self.assertEqual(server.calls[:4], ['ehlo', 'starttls', 'ehlo', ('login', 'buh@company.kz')])
        self.assertIsNotNone(server.tls_context)

    def test_login_refused_is_transport(self):
        _FakeSMTP.login_error_next = smtplib.SMTPAuthenticationError(535, b'5.7.8 Username and Password not accepted')
        with self.assertRaises(mail.MailError) as caught:
            with mail.Transport(self.cfg):
                pass
        self.assertEqual(caught.exception.kind, 'transport')
        self.assertIn('логин или пароль приложения', str(caught.exception))
        self.assertIn('535 5.7.8 Username and Password not accepted', str(caught.exception))

    def test_no_connection_is_transport(self):
        _FakeSMTP.connect_error = TimeoutError('timed out')
        with self.assertRaises(mail.MailError) as caught:
            with mail.Transport(self.cfg):
                pass
        self.assertEqual(caught.exception.kind, 'transport')
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
        self.assertEqual(error.kind, 'transport', 'ящик не пускает отправителя — дело в настройке')
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
    def test_not_configured_touches_nothing(self):
        db = mock.MagicMock()
        self.assertEqual(requisites_mail.run(db, env={}), {'ready': False})
        db._get_cursor.assert_not_called()

    def test_pause_after_mailbox_refusal(self):
        db = mock.MagicMock()
        with mock.patch.dict(requisites_mail._PAUSE, {'until': 1000.0}):
            self.assertEqual(requisites_mail.run(db, env=GMAIL, clock=lambda: 999.0), {'ready': True, 'paused': True})
        db._get_cursor.assert_not_called()


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
        patches = [mock.patch.object(routes, 'queries', queries), mock.patch.object(routes_directory, 'queries', queries),
                   mock.patch.object(routes_directory, 'directory', self.directory),
                   mock.patch.object(routes_directory, 'requisites_mail', self.mailing)]
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
        self.assertEqual(fields['emails'], ['buh@romashka.kz', 'info@romashka.kz'])
        self.assertNotIn('mailing', response.get_json(), 'правка поставщика писем не вызывает')
        self.mailing.after_entity_saved.assert_not_called()

    def test_old_window_without_email_field_keeps_emails(self):
        client = self.client()
        self.directory.upsert_counterparty.return_value = 7
        self.directory.read_counterparty.return_value = {'id': 7, 'emails': ['buh@romashka.kz']}
        response = client.post('/api/payments/dictionaries/counterparties', json={'id': 7, 'name': 'ТОО «Ромашка»'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.directory.upsert_counterparty.call_args.kwargs['fields']['emails'], ['buh@romashka.kz'],
                         'окно без поля «Почта» не стирает адреса')
        self.directory.upsert_counterparty.reset_mock()
        client.post('/api/payments/dictionaries/counterparties', json={'id': 7, 'name': 'ТОО «Ромашка»', 'emails': ''})
        self.assertEqual(self.directory.upsert_counterparty.call_args.kwargs['fields']['emails'], [],
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
        with mock.patch.dict('os.environ', {}, clear=True):
            body = client.get('/api/payments/dictionaries/legal_entities').get_json()
        self.assertEqual([(row['id'], row['mailing'], row['mailings_count']) for row in body['items']],
                         [(3, {'id': 9, 'status': 'pending'}, 4), (4, None, 0)])
        self.assertEqual(body['mail'], {'ready': False, 'recipients': {'addresses': 2}, 'delay_minutes': 10,
                                        'pause_minutes': 10}, 'без PAYMENTS_SMTP_* почта не подключена')
        with mock.patch.dict('os.environ', GMAIL, clear=True):
            self.assertTrue(client.get('/api/payments/dictionaries/legal_entities').get_json()['mail']['ready'])

    def test_entity_list_hides_letters_from_readers(self):
        client = self.client(can_edit=False)
        self.directory.list_legal_entities.return_value = [{'id': 3, 'name': 'ТОО «Наша»'}]
        body = client.get('/api/payments/dictionaries/legal_entities').get_json()
        self.assertNotIn('mail', body)
        self.assertNotIn('mailing', body['items'][0])
        self.mailing.states.assert_not_called()

    def test_mailings_reply(self):
        client = self.client()
        self.directory.read_legal_entity.return_value = {'id': 3}
        self.mailing.history.return_value = ([{'id': 9}], 12)
        self.mailing.known_now.return_value = {'iik': 'KZ11'}
        body = client.get('/api/payments/dictionaries/legal_entities/3/mailings').get_json()
        self.assertEqual((body['items'], body['total'], body['known']), ([{'id': 9}], 12, {'iik': 'KZ11'}))
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
        self.mailing.detail.return_value = {'id': 9, 'legal_entity_name': 'ТОО «Наша»'}
        with mock.patch.dict('os.environ', GMAIL, clear=True):
            body = client.get('/api/payments/mailings/9').get_json()
        self.assertEqual((body['item']['id'], body['sender']), (9, {'name': 'ТОО «Наша»', 'address': 'buh@company.kz'}))
        self.assertTrue(body['mail']['ready'])
        with mock.patch.dict('os.environ', dict(GMAIL, PAYMENTS_MAIL_FROM='Бухгалтерия <buh@company.kz>'), clear=True):
            self.assertEqual(client.get('/api/payments/mailings/9').get_json()['sender']['name'], 'Бухгалтерия')
        with mock.patch.dict('os.environ', {}, clear=True):
            body = client.get('/api/payments/mailings/9').get_json()
        self.assertEqual(body['sender'], {'name': 'ТОО «Наша»', 'address': None}, 'без почты — адреса отправителя нет')
        self.assertFalse(body['mail']['ready'])
        self.mailing.detail.side_effect = requisites_mail.MailingError('Письмо не найдено', status=404,
                                                                       code='PAYMENT_MAILING_NOT_FOUND')
        self.assertEqual(client.get('/api/payments/mailings/10').status_code, 404)

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
                             ('post', '/api/payments/dictionaries/legal_entities/3/mailings/9/retry')):
            with self.subTest(path):
                response = getattr(client, method)(path)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.get_json()['code'], 'PAYMENTS_ADMIN_ONLY')
        self.mailing.cancel.assert_not_called()
        self.mailing.retry.assert_not_called()
        self.mailing.journal.assert_not_called()
        self.mailing.detail.assert_not_called()


if __name__ == '__main__':
    unittest.main()
