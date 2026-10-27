# -*- coding: utf-8 -*-
"""«Оплата счетов»: почта поставщиков и письма о смене реквизитов наших компаний.

Просьба владельца 20.10.2026: «добавление эмайла контрагентов поставщиков и тд,
автомат рассылка по эмайлом при изменении реквизитов»; 21.10.2026 — справочник
«Почта поставщиков» и галочки «Кому» в письме; 27.10.2026 — «пока убери автоотправку
и сделай полу ручной»: кнопка «Отправить письмо» открывает почту компании с готовым
письмом, отправляет человек. Здесь — то, что видно без базы: разбор адресов, какая
почта у компании, что считается сменой реквизитов, текст письма и то, что кнопка
кладёт в буфер, проводка ручек справочников и то, что автоотправки больше нет. Что
значат запросы («открыто в почте», галочки, «Не отправлять»), проверяет
tests/test_payments_requisites_mail_postgres.py на настоящем Postgres.

Случаи адресов и смены реквизитов — общие с tests/payments_requisites_mail.test.mjs
(tests/data/payments_requisites_mail_cases.json): форма и сервер решают одинаково.
"""

import json
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from flask import Flask

from payments import access, directory, mail, requisites_mail, routes, routes_directory

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'tests' / 'data' / 'payments_requisites_mail_cases.json').read_text(encoding='utf-8'))
ADMIN = access.SECTION_ADMIN_USER_IDS[0]
# Публичная почта — только доменом: адресов на ней в репозитории нет (tests/test_no_personal_data_in_repo.py).
GMAIL_ADDRESS = 'buh' + '@' + 'gmail.com'


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


class ProviderTests(unittest.TestCase):
    """Какая почта у компании — по ней «Отправить письмо» решает, что открыть: Gmail в
    браузере или почтовую программу."""

    def test_provider_by_address(self):
        nobody = lambda domain: []  # noqa: E731 — DNS не спрашиваем
        for domain, key in (('gmail.com', 'gmail'), ('Yandex.KZ', 'yandex'), ('ya.ru', 'yandex'), ('bk.ru', 'mailru'),
                            ('mail.ru', 'mailru'), ('company.example', None), ('', None)):
            with self.subTest(domain):
                self.assertEqual(mail.provider_of_domain(domain, mx=nobody), key)
        self.assertEqual(mail.provider_of(GMAIL_ADDRESS, mx=nobody), 'gmail')
        self.assertEqual(mail.provider_of('Buh@Company.Example', mx=lambda domain: ['mx.yandex.net']), 'yandex')
        # Свой домен — по MX: Google Workspace откроется Gmail, свой сервер (Exchange) — почтовой программой.
        for mx, key in ((['aspmx.l.google.com', 'alt1.aspmx.l.google.com'], 'gmail'), (['smtp.google.com'], 'gmail'),
                        (['mx.yandex.net'], 'yandex'), (['emx.mail.ru'], 'mailru'),
                        (['company-kz.mail.protection.outlook.com'], 'microsoft'), (['ex1.company.kz'], None),
                        (['notgoogle.com'], None)):
            with self.subTest(mx):
                self.assertEqual(mail.provider_of('buh@company.kz', mx=lambda domain, hosts=mx: hosts), key)

    def test_mx_lookup_asks_dns_over_https_once(self):
        answer = mock.Mock(status_code=200)
        answer.json.return_value = {'Answer': [{'type': 15, 'data': '10 aspmx.l.google.com.'},
                                               {'type': 5, 'data': 'alias.example.'}]}
        mail._MX_CACHE.clear()
        self.addCleanup(mail._MX_CACHE.clear)
        with mock.patch('requests.get', return_value=answer) as get:
            self.assertEqual(mail._mx_hosts('company.kz'), ['aspmx.l.google.com'])
            self.assertEqual(mail._mx_hosts('company.kz'), ['aspmx.l.google.com'])
        self.assertEqual(get.call_count, 1, 'ответ DNS помнится')
        self.assertEqual(get.call_args.kwargs['params'], {'name': 'company.kz', 'type': 'MX'})
        self.assertEqual(get.call_args.kwargs['timeout'], mail.MX_TIMEOUT_SECONDS)
        with mock.patch('requests.get', side_effect=OSError('нет сети')):
            self.assertEqual(mail._mx_hosts('offline.kz'), [], 'DNS не ответил — почта не узнана')
        self.assertIsNone(mail.provider_of('buh@offline.kz', mx=lambda domain: []),
                          'не узнали — кнопка откроет почтовую программу')


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
        self.assertEqual(subject, 'Изменение реквизитов ТОО «Наша компания» Bcc: x@y.kz', 'тема — одной строкой')
        self.assertEqual(requisites_mail.subject_of(dict(BEFORE, name='ТОО «Наша\nкомпания»')),
                         'Изменение реквизитов ТОО «Наша компания»', 'тема в журнале — та же, что у письма')

    def test_without_bin_and_name(self):
        subject, text, _body = requisites_mail.letter({}, {'iik': 'KZ1'})
        self.assertEqual(subject, 'Изменение реквизитов нашей компании')
        self.assertIn('Сообщаем об изменении реквизитов нашей компании.', text)


class LetterViewTests(unittest.TestCase):
    """Что кнопка «Отправить письмо» кладёт в буфер: то же письмо без страницы вокруг."""

    def test_paste_is_the_letter_without_the_page(self):
        after = dict(BEFORE, iik='KZ22TEST0000000002', kbe='19')
        view = requisites_mail.letter_view(BEFORE, after)
        subject, text, body = requisites_mail.letter(BEFORE, after)
        self.assertEqual((view['subject'], view['text'], view['html']), (subject, text, body))
        paste = view['paste']
        for page_only in ('<html', '<body', '<style', 'background:#f5f5f7', 'letter-card', '<title'):
            with self.subTest(page_only):
                self.assertNotIn(page_only, paste, 'серый фон и карточка в чужом письме смотрелись бы рамкой')
        self.assertTrue(paste.startswith('<div style="color:#0f172a;font-size:14px;line-height:1.5">'))
        cells = _Cells()
        cells.feed(paste)
        self.assertEqual(cells.rows, [['Реквизит', 'Было', 'Стало'], ['ИИК', 'KZ11TEST0000000001', 'KZ22TEST0000000002'],
                                      ['КБЕ', '17', '19']])
        self.assertEqual(cells.blocks, [directory.requisites_text(requisites_mail.snapshot(after))])
        self.assertIn(paste[paste.index('>') + 1:-len('</div>')], body, 'содержимое — то же, что в окне письма')

    def test_paste_is_escaped(self):
        paste = requisites_mail.letter_view(BEFORE, dict(BEFORE, requisites='<img src=x onerror=alert(1)>'))['paste']
        self.assertNotIn('<img', paste)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', paste)


class AutoSendRemovedTests(unittest.TestCase):
    """27.10.2026: «пока убери автоотправку» — портал писем сам не отправляет."""

    def test_no_letters_job_in_the_scheduler(self):
        source = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        for gone in ('payments_requisites_mail', 'requisites_mail.run'):
            with self.subTest(gone):
                self.assertNotIn(gone, source)
        for gone in ('run', 'retry', 'SEND_DELAY_MINUTES', 'waiting_for_mailbox'):
            with self.subTest(gone):
                self.assertFalse(hasattr(requisites_mail, gone))
        for module in ('mailbox.py', 'google_mail.py'):
            with self.subTest(module):
                self.assertFalse((ROOT / 'payments' / module).exists(), 'SMTP и вход через Google — только в git')
        self.assertFalse(hasattr(mail, 'Transport'))

    def test_startup_moves_mailbox_address_and_closes_unsent(self):
        source = (ROOT / 'payments' / 'schema.py').read_text(encoding='utf-8')
        init = source[source.index('def init_payments_schema'):source.index('def schema_ready')]
        self.assertIn('requisites_mail.adopt_contact_emails(cursor)', init)
        self.assertIn('SAVEPOINT payments_contact_emails', init)
        # Адреса из поля «Почта» — строками «Почты поставщиков», после переноса из «Контакта».
        self.assertLess(init.index('adopt_contact_emails'), init.index('directory.adopt_email_array(cursor)'))
        self.assertIn('SAVEPOINT payments_counterparty_emails', init)
        # Почта, подключённая для автоотправки, — «Почтой для писем»; недосланное — закрыто. Под своим SAVEPOINT.
        block = init[init.index('SAVEPOINT payments_manual_letters'):]
        self.assertLess(block.index('requisites_mail.adopt_mailbox_addresses(cursor)'),
                        block.index('requisites_mail.close_auto_sending(cursor)'))
        self.assertIn('ROLLBACK TO SAVEPOINT payments_manual_letters', block)
        statements = '\n'.join(sql for sql in requisites_schema_statements() if 'ADD COLUMN IF NOT EXISTS' in sql)
        for column in ('opened_at', 'opened_by', 'opened_by_name', 'letters_email', 'letters_provider'):
            with self.subTest(column):
                self.assertIn('ADD COLUMN IF NOT EXISTS %s ' % column, statements)

    def test_mailbox_address_adopted_once(self):
        cursor = mock.MagicMock()
        cursor.fetchone.return_value = ('mailbox_addresses_adopted',)
        cursor.rowcount = 1
        self.assertEqual(requisites_mail.adopt_mailbox_addresses(cursor), 1)
        sql = cursor.execute.call_args_list[-1].args[0]
        self.assertIn('letters_email = mb.address', sql)
        self.assertIn('le.letters_email IS NULL', sql, 'адрес, вписанный руками, не трогаем')
        cursor = mock.MagicMock()
        cursor.fetchone.return_value = None   # отметка уже стоит
        self.assertEqual(requisites_mail.adopt_mailbox_addresses(cursor), 0)
        self.assertEqual(cursor.execute.call_count, 1, 'второй раз — только проверка отметки')


def requisites_schema_statements():
    from payments import schema
    return schema.statements()


class DictionaryRoutesTests(unittest.TestCase):
    """Проводка ручек справочников: почта поставщика, «Почта для писем» компании,
    письмо после правки компании и «Отправить письмо»."""

    def client(self, can_edit=True):
        queries = mock.MagicMock()
        queries.load_access_context.return_value = {'user_id': ADMIN, 'name': 'Админ Раздела', 'role': 'admin'}
        queries.roles_of_user.return_value = set()
        self.directory = mock.MagicMock()
        self.directory.find_duplicate_name.return_value = None
        self.directory.requisites_text.return_value = 'реквизиты'
        self.directory.read_legal_entity.return_value = {'id': 3, 'name': 'ТОО «Наша»'}
        self.mailing = mock.MagicMock()
        self.mailing.MailingError = requisites_mail.MailingError
        self.mailing.history.return_value = ([], 0)
        self.mailing.known_now.return_value = {}
        self.mailing.recipients_overview.return_value = {'addresses': 2}
        self.mailing.states.return_value = ({3: {'id': 9, 'status': 'pending'}}, {3: 4})
        self.provider = mock.MagicMock(return_value=None)
        patches = [mock.patch.object(routes, 'queries', queries), mock.patch.object(routes_directory, 'queries', queries),
                   mock.patch.object(routes_directory, 'directory', self.directory),
                   mock.patch.object(routes_directory, 'requisites_mail', self.mailing),
                   mock.patch.object(routes_directory.mail, 'provider_of', self.provider)]
        if not can_edit:
            patches.append(mock.patch.object(access, 'can_save_dictionary', return_value=False))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        self.db = mock.MagicMock()
        app.register_blueprint(routes.build_payments_blueprint(
            db=self.db, require_api_key=lambda handler: handler,
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

    def test_entity_save_locks_before_and_prepares_letter(self):
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
        fields = self.directory.upsert_legal_entity.call_args.kwargs['fields']
        self.assertEqual((fields['letters_email'], fields['letters_provider']), (None, None))

    def test_letters_email_is_parsed_and_its_mail_known_before_the_transaction(self):
        client = self.client()
        current = dict(BEFORE, id=3, letters_email='old@company.kz', letters_provider=None)
        self.directory.read_legal_entity.return_value = current
        self.directory.upsert_legal_entity.return_value = 3
        self.mailing.after_entity_saved.return_value = None
        order = []
        self.provider.side_effect = lambda address: order.append('dns') or 'gmail'
        self.db._get_cursor.side_effect = lambda: order.append('cursor') or mock.MagicMock()
        response = client.post('/api/payments/dictionaries/legal_entities',
                               json=dict(current, letters_email=' Buh@Company.KZ '))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        fields = self.directory.upsert_legal_entity.call_args.kwargs['fields']
        self.assertEqual((fields['letters_email'], fields['letters_provider']), ('buh@company.kz', 'gmail'))
        self.assertEqual(order[-2:], ['dns', 'cursor'], 'MX спрашиваем до транзакции записи: строку компании сеть не держит')
        self.provider.assert_called_once_with('buh@company.kz')
        # Стёрли — почты нет; окно без поля (открыто до выкладки) — почта прежняя.
        client.post('/api/payments/dictionaries/legal_entities', json=dict(current, letters_email=''))
        fields = self.directory.upsert_legal_entity.call_args.kwargs['fields']
        self.assertEqual((fields['letters_email'], fields['letters_provider']), (None, None))
        old_window = {key: value for key, value in current.items() if key not in ('letters_email', 'letters_provider')}
        client.post('/api/payments/dictionaries/legal_entities', json=old_window)
        fields = self.directory.upsert_legal_entity.call_args.kwargs['fields']
        self.assertEqual(fields['letters_email'], 'old@company.kz')
        # Адрес прежний и почта известна — DNS не спрашиваем: его сбой не превратит Gmail в программу.
        self.provider.reset_mock()
        self.directory.read_legal_entity.return_value = dict(current, letters_email='buh@company.kz', letters_provider='gmail')
        client.post('/api/payments/dictionaries/legal_entities', json=dict(current, letters_email='Buh@Company.kz'))
        fields = self.directory.upsert_legal_entity.call_args.kwargs['fields']
        self.assertEqual((fields['letters_email'], fields['letters_provider']), ('buh@company.kz', 'gmail'))
        self.provider.assert_not_called()
        self.directory.read_legal_entity.return_value = dict(current, letters_email='buh@company.kz', letters_provider=None)
        client.post('/api/payments/dictionaries/legal_entities', json=dict(current, letters_email='buh@company.kz'))
        self.provider.assert_called_once_with('buh@company.kz')
        self.directory.read_legal_entity.return_value = current
        for bad in ('buh@company', 'a@x.kz, b@x.kz'):
            with self.subTest(bad):
                self.directory.upsert_legal_entity.reset_mock()
                response = client.post('/api/payments/dictionaries/legal_entities', json=dict(current, letters_email=bad))
                self.assertEqual(response.status_code, 400)
                body = response.get_json()
                self.assertEqual((body['code'], body['field']), ('PAYMENTS_EMAIL_INVALID', 'letters_email'))
                self.assertTrue(body['error'].startswith('Почта для писем: '))
                self.directory.upsert_legal_entity.assert_not_called()

    def test_entity_list_carries_letters_for_editors(self):
        client = self.client()
        self.directory.list_legal_entities.return_value = [{'id': 3, 'name': 'ТОО «Наша»'}, {'id': 4, 'name': 'ИП Тестов'}]
        body = client.get('/api/payments/dictionaries/legal_entities').get_json()
        self.assertEqual([(row['id'], row['mailing'], row['mailings_count']) for row in body['items']],
                         [(3, {'id': 9, 'status': 'pending'}, 4), (4, None, 0)])
        self.assertEqual(body['mail'], {'recipients': {'addresses': 2}})
        for row in body['items']:
            self.assertNotIn('mailbox', row)
            self.assertNotIn('google_mail', row)

    def test_entity_list_hides_letters_from_readers(self):
        client = self.client(can_edit=False)
        self.directory.list_legal_entities.return_value = [{'id': 3, 'name': 'ТОО «Наша»'}]
        body = client.get('/api/payments/dictionaries/legal_entities').get_json()
        self.assertNotIn('mail', body)
        self.assertNotIn('mailing', body['items'][0])
        self.mailing.states.assert_not_called()

    def test_mailings_reply_and_open_in_mail(self):
        client = self.client()
        self.directory.read_legal_entity.return_value = {'id': 3}
        self.mailing.history.return_value = ([{'id': 9}], 12)
        self.mailing.known_now.return_value = {'iik': 'KZ11'}
        body = client.get('/api/payments/dictionaries/legal_entities/3/mailings').get_json()
        self.assertEqual((body['items'], body['total'], body['known']), ([{'id': 9}], 12, {'iik': 'KZ11'}))
        self.assertEqual(body['sender'], {'name': '', 'address': None, 'provider': None})
        self.assertEqual(body['mail'], {'recipients': {'addresses': 2}})
        self.directory.read_legal_entity.return_value = {'id': 3, 'name': 'ТОО  «Наша»', 'letters_email': GMAIL_ADDRESS,
                                                         'letters_provider': 'gmail'}
        body = client.get('/api/payments/dictionaries/legal_entities/3/mailings').get_json()
        self.assertEqual(body['sender'], {'name': 'ТОО «Наша»', 'address': GMAIL_ADDRESS, 'provider': 'gmail'},
                         'кнопка откроет Gmail этой компании')
        self.mailing.open_in_mail.return_value = {'addresses': 2}
        body = client.post('/api/payments/dictionaries/legal_entities/3/mailings/9/open',
                           json={'addresses': ['a@x.kz', 'b@y.kz']}).get_json()
        self.assertEqual((body['status'], body['addresses'], body['items']), ('success', 2, [{'id': 9}]))
        self.assertEqual(self.mailing.open_in_mail.call_args.args[1:],
                         (3, 9, {'id': ADMIN, 'name': 'Админ Раздела'}, ['a@x.kz', 'b@y.kz']))
        client.post('/api/payments/dictionaries/legal_entities/3/mailings/9/open', json={})
        self.assertIsNone(self.mailing.open_in_mail.call_args.args[4], 'старая вкладка без списка — адреса на сервере')
        self.assertEqual(client.post('/api/payments/dictionaries/legal_entities/3/mailings/9/open',
                                     json={'addresses': 'a@x.kz'}).status_code, 400)
        self.mailing.open_in_mail.side_effect = requisites_mail.MailingError('Адрес «x»: не похоже', status=400,
                                                                            code='PAYMENTS_EMAIL_INVALID')
        response = client.post('/api/payments/dictionaries/legal_entities/3/mailings/9/open', json={'addresses': ['x']})
        self.assertEqual((response.status_code, response.get_json()['code']), (400, 'PAYMENTS_EMAIL_INVALID'))
        for gone in ('retry', 'mailbox', 'mailbox/detect', 'mailbox/check', 'mailbox/google'):
            with self.subTest(gone):
                path = ('/api/payments/dictionaries/legal_entities/3/mailings/9/retry' if gone == 'retry'
                        else '/api/payments/dictionaries/legal_entities/3/%s' % gone)
                self.assertEqual(client.post(path).status_code, 404, 'автоотправки и подключения почты больше нет')
        self.assertEqual(client.get('/api/payments/mailbox/google/callback').status_code, 404)

    def test_letters_journal_and_one_letter(self):
        client = self.client()
        self.mailing.journal.return_value = ([{'id': 9}], 31)
        body = client.get('/api/payments/mailings?limit=500&offset=-3').get_json()
        self.assertEqual(body, {'items': [{'id': 9}], 'total': 31, 'limit': 100, 'offset': 0})
        self.assertEqual(self.mailing.journal.call_args.kwargs, {'limit': 100, 'offset': 0})
        self.mailing.detail.return_value = {'id': 9, 'legal_entity_id': 3, 'legal_entity_name': 'ТОО «Наша»'}
        self.directory.read_legal_entity.return_value = {'id': 3, 'name': 'ТОО «Наша»', 'letters_email': 'buh@company.kz',
                                                         'letters_provider': None}
        body = client.get('/api/payments/mailings/9').get_json()
        self.assertEqual((body['item']['id'], body['sender']),
                         (9, {'name': 'ТОО «Наша»', 'address': 'buh@company.kz', 'provider': None}))
        self.assertEqual(self.directory.read_legal_entity.call_args.args[1], 3, 'отправитель — компания этого письма')
        self.mailing.detail.side_effect = requisites_mail.MailingError('Письмо не найдено', status=404,
                                                                       code='PAYMENT_MAILING_NOT_FOUND')
        self.assertEqual(client.get('/api/payments/mailings/10').status_code, 404)

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

    def test_mailing_errors_become_409(self):
        client = self.client()
        self.mailing.cancel.side_effect = requisites_mail.MailingError('Письмо уже открыто в почте')
        response = client.post('/api/payments/dictionaries/legal_entities/3/mailings/9/cancel')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error'], 'Письмо уже открыто в почте')

    def test_mailings_only_for_section_admin(self):
        client = self.client(can_edit=False)
        for method, path in (('get', '/api/payments/mailings'), ('get', '/api/payments/mailings/9'),
                             ('get', '/api/payments/dictionaries/legal_entities/3/mailings'),
                             ('post', '/api/payments/dictionaries/legal_entities/3/mailings/9/cancel'),
                             ('post', '/api/payments/dictionaries/legal_entities/3/mailings/9/open'),
                             ('post', '/api/payments/dictionaries/legal_entities/3/mailings/9/recipients')):
            with self.subTest(path):
                response = getattr(client, method)(path)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.get_json()['code'], 'PAYMENTS_ADMIN_ONLY')
        self.mailing.cancel.assert_not_called()
        self.mailing.open_in_mail.assert_not_called()
        self.mailing.journal.assert_not_called()
        self.mailing.detail.assert_not_called()
        self.mailing.set_recipients.assert_not_called()
        # «Почту для писем» пишет тот же администратор: запись компании — его ручка.
        response = client.post('/api/payments/dictionaries/legal_entities', json={'id': 3, 'name': 'ТОО «Наша»',
                                                                                  'letters_email': 'buh@company.kz'})
        self.assertEqual(response.status_code, 403)
        self.directory.upsert_legal_entity.assert_not_called()


if __name__ == '__main__':
    unittest.main()
