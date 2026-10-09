# -*- coding: utf-8 -*-
"""Регулярные платежи, выгрузка реестра, номера карт и тексты уведомлений «Оплаты счетов».

Регулярные платежи (ТЗ #381, пп. 4.4 и 13): периодичность сдвигает срок без
потери числа месяца, заявка создаётся в начале периода и только у платежей с
автосозданием; справочник подставляет в заявку поставщика, договор, компанию,
реквизиты и назначение. Выгрузка: все поля, БИН и номера — текстом, суммы —
числами, номера карты в файле нет. Карты (п. 5.2): номер шифруется, наружу
уходит маска. Уведомления (п. 17): десять событий, у каждого свой текст.

Ни базы, ни сети: проверяются чистые функции.
"""

import io
import os
import re
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payments import cards, fixed, notify, report, workflow  # noqa: E402

META_PATH = ROOT / 'src' / 'components' / 'payments' / 'paymentsMeta.js'


class PeriodTests(unittest.TestCase):
    def test_add_months_keeps_the_day_and_clamps(self):
        self.assertEqual(fixed.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(fixed.add_months(date(2026, 11, 5), 3), date(2027, 2, 5))
        self.assertEqual(fixed.next_due(date(2026, 3, 10), 'quarterly'), date(2026, 6, 10))
        self.assertEqual(fixed.next_due(date(2026, 3, 10), 'yearly'), date(2027, 3, 10))
        self.assertEqual(fixed.next_due(date(2026, 3, 10), 'custom', 14), date(2026, 3, 24))
        with self.assertRaises(ValueError):
            fixed.next_due(date(2026, 3, 10), 'custom', 0)

    def test_due_day_survives_a_short_month(self):
        """Аренда со сроком 31-го: после февраля срок не съезжает на 28-е навсегда."""
        due, seen = date(2027, 1, 31), []
        for _ in range(4):
            due = fixed.next_due(due, 'monthly', due_day=31)
            seen.append(due)
        self.assertEqual(seen, [date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30), date(2027, 5, 31)])
        self.assertEqual(fixed.next_due(date(2027, 11, 30), 'quarterly', due_day=30), date(2028, 2, 29))
        self.assertEqual(fixed.next_due(date(2027, 2, 28), 'monthly'), date(2027, 3, 28),
                         'без числа из справочника — прежний счёт от даты')
        self.assertEqual(fixed.due_day_of(date(2027, 1, 31), 'monthly'), 31)
        self.assertIsNone(fixed.due_day_of(date(2027, 1, 31), 'custom'), 'у интервала в днях числа месяца нет')

    def test_request_is_generated_at_the_start_of_the_period(self):
        self.assertEqual(fixed.generate_on(date(2026, 9, 25), 'monthly'), date(2026, 9, 1))
        self.assertEqual(fixed.generate_on(date(2026, 9, 25), 'custom', lead_days=7), date(2026, 9, 18))

    def test_is_due_respects_activity_period_start_and_manual_mode(self):
        template = {'is_active': True, 'auto_create': True, 'next_due_on': date(2026, 9, 25),
                    'periodicity': 'monthly', 'lead_days': 7, 'last_generated_on': None, 'open_request_id': None}
        self.assertTrue(fixed.is_due(template, date(2026, 9, 1)))
        self.assertFalse(fixed.is_due(template, date(2026, 8, 31)))
        self.assertFalse(fixed.is_due(dict(template, is_active=False), date(2026, 9, 1)))
        # «Только вручную»: заявку по такому платежу заводит человек, календарь её не создаёт.
        self.assertFalse(fixed.is_due(dict(template, auto_create=False), date(2026, 9, 1)))
        # Уже создали под этот срок и заявка ещё живая — второй раз не создаём.
        self.assertFalse(fixed.is_due(dict(template, last_generated_on=date(2026, 9, 1), open_request_id=5),
                                      date(2026, 9, 2)))

    def test_period_text_for_the_request(self):
        due = date(2026, 10, 25)
        self.assertEqual(fixed._period_text(due, 'monthly'), 'октябрь 2026')
        self.assertEqual(fixed._period_text(due, 'quarterly'), '4 квартал 2026')
        self.assertEqual(fixed._period_text(due, 'yearly'), '2026 год')
        self.assertEqual(fixed._period_text(due, 'custom'), 'до 25.10.2026')


class TemplateFillsTheRequestTests(unittest.TestCase):
    """П. 4.4: поставщика, договор, компанию, реквизиты и назначение подставляет справочник."""

    def test_request_fields_come_from_the_directory(self):
        template = {'id': 9, 'name': 'Аренда офиса', 'counterparty_id': 11, 'contract_id': 3, 'legal_entity_id': 1,
                    'counterparty_account_id': 4, 'payment_purpose': 'Аренда за месяц', 'category_id': 2,
                    'subcategory_id': None, 'project_id': 7, 'branch': 'Алматы', 'object_type': None}
        fields = fixed.request_fields(template)
        self.assertEqual(fields['fixed_template_id'], 9)
        for key in ('counterparty_id', 'contract_id', 'legal_entity_id', 'counterparty_account_id',
                    'payment_purpose', 'category_id', 'project_id', 'branch'):
            self.assertEqual(fields[key], template[key], key)
        self.assertEqual(fields['expense_name'], 'Аренда офиса')
        # Регулярное обязательство оплачивается по счёту; по умолчанию это услуга.
        self.assertEqual((fields['payment_method'], fields['object_type'], fields['accounting_category']),
                         ('invoice', 'service', None))
        self.assertEqual(fixed.request_fields(dict(template, object_type='goods'))['object_type'], 'goods')

    def test_goods_bring_their_accounting_category(self):
        """П. 10: у товара категория учёта обязательна — заявка получает её из справочника."""
        template = {'id': 9, 'name': 'Картриджи', 'object_type': 'goods', 'accounting_category': 'asset'}
        fields = fixed.request_fields(template)
        self.assertEqual((fields['object_type'], fields['accounting_category']), ('goods', 'asset'))
        self.assertTrue(workflow.needs_asset_registration(fields), 'имущество → этап постановки на учёт')
        service = fixed.request_fields(dict(template, object_type='service'))
        self.assertIsNone(service['accounting_category'], 'у услуги категории учёта нет')

    def test_same_payment_is_recognised_on_reimport(self):
        row = {'name': 'Аренда  офиса', 'counterparty_id': 11, 'periodicity': 'monthly'}
        self.assertEqual(fixed._template_key(row), fixed._template_key(dict(row, name='аренда офиса')))
        self.assertNotEqual(fixed._template_key(row), fixed._template_key(dict(row, periodicity='yearly')))
        self.assertNotEqual(fixed._template_key(row), fixed._template_key(dict(row, counterparty_id=12)))


class ImportParsingTests(unittest.TestCase):
    def test_headers_are_detected_in_everyday_wording(self):
        mapping = fixed.detect_columns(('Наименование', 'Сумма', 'Периодичность', 'Дата оплаты',
                                        'Проект / филиал', 'Ответственный', 'Категория', 'Подкатегория',
                                        'Поставщик', 'Примечание'))
        self.assertEqual(set(mapping), {'name', 'amount', 'periodicity', 'due', 'project', 'responsible',
                                        'category', 'subcategory', 'counterparty', 'note'})

    def test_periodicity_words(self):
        self.assertEqual(fixed.parse_periodicity('ежемесячно'), ('monthly', None))
        self.assertEqual(fixed.parse_periodicity('Раз в квартал'), ('quarterly', None))
        self.assertEqual(fixed.parse_periodicity('годовой'), ('yearly', None))
        self.assertEqual(fixed.parse_periodicity('каждые 10 дней'), ('custom', 10))
        self.assertEqual(fixed.parse_periodicity(''), (None, None))

    def test_due_day_number_rolls_to_the_next_month_when_passed(self):
        today = date(2026, 9, 14)
        self.assertEqual(fixed.parse_due(20, today), date(2026, 9, 20))
        self.assertEqual(fixed.parse_due('5', today), date(2026, 10, 5))
        self.assertEqual(fixed.parse_due('05.11.2026', today), date(2026, 11, 5))
        self.assertEqual(fixed.parse_due('3-го числа', today), date(2026, 10, 3))
        self.assertIsNone(fixed.parse_due('когда-нибудь', today))

    def test_workbook_is_parsed_with_errors_per_row(self):
        workbook = Workbook()
        sheet = workbook.active
        for row in fixed.template_sample_rows():
            sheet.append(list(row))
        sheet.append(['Без суммы', None, 'ежемесячно', '1', None, None, None, None, None, None])
        stream = io.BytesIO()
        workbook.save(stream)
        rows = fixed.parse_workbook(stream.getvalue(), today=date(2026, 9, 14))
        self.assertEqual(len(rows), 2)
        good, bad = rows
        self.assertEqual(good['name'], 'Аренда офиса Алматы')
        self.assertEqual(good['amount'], Decimal('450000'))
        self.assertEqual(good['periodicity'], 'monthly')
        self.assertEqual(good['next_due_on'], date(2026, 10, 5))
        self.assertEqual(good['counterparty'], 'ТОО «Пример»')
        self.assertEqual(good['errors'], [])
        self.assertIn('сумма не разобрана', bad['errors'])

    def test_sample_file_has_the_columns_the_parser_reads(self):
        header = fixed.template_sample_rows()[0]
        self.assertEqual(len(fixed.detect_columns(header)), len(header), 'каждая колонка образца разбирается')

    def test_missing_headers_raise_a_readable_error(self):
        workbook = Workbook()
        workbook.active.append(['Что-то', 'Другое'])
        stream = io.BytesIO()
        workbook.save(stream)
        with self.assertRaises(ValueError):
            fixed.parse_workbook(stream.getvalue())


def request_row(**overrides):
    row = {
        'id': 17, 'created_at': '2026-10-01T10:00:00', 'status': 'active', 'state': 'active',
        'stage': 'payment', 'request_kind': 'purchase', 'current_assignee_name': 'Бухгалтерия',
        'initiator_name': 'Ядигаров Руслан', 'manager_name': None, 'department_name': 'СЗоВ',
        'legal_entity_name': 'ТОО «Наше»', 'project_name': 'Проект А', 'branch': 'Алматы',
        'expense_name': 'Бумага А4', 'justification': '=срочно, закончилась', 'object_type': 'goods',
        'accounting_category': 'consumable', 'amount': Decimal('2950'), 'amount_approved': Decimal('2950'),
        'refund_amount': Decimal('450'), 'refund_on': date(2026, 10, 5), 'category_name': 'Канцелярия',
        'subcategory_name': None, 'counterparty_name': 'ТОО «Пример»', 'counterparty_bin': '000000000123',
        'no_alternatives': False, 'supplier_choice_reason': 'Дешевле', 'contract_number': '0025',
        'payment_method': 'invoice', 'card_recipient': None, 'card_holder_name': None, 'card_mask': '',
        'payment_purpose': None, 'payment_period': None, 'due_on': date(2026, 10, 30),
        'approved_by_name': 'Молдагалиева Сауле', 'approved_at': '2026-10-02T12:00:00',
        'route_basis': {'basis': 'Поставщик «ТОО «Пример»» закреплён за согласующим'},
        'invoice_number': '00012', 'invoice_date': date(2026, 10, 1), 'paid_on': None, 'paid_amount': None,
        'received_on': None, 'received_quantity': None, 'closing_docs_status': 'none', 'notes': None,
        'rejected_reason': None, 'closed_at': None,
    }
    row.update(overrides)
    return row


ITEMS = [{'name': 'Бумага А4', 'quantity': Decimal('1'), 'unit': 'уп', 'unit_price': Decimal('2500')},
         {'name': 'Карандаш', 'quantity': Decimal('3'), 'unit': 'шт', 'unit_price': Decimal('150')}]
OFFERS = [{'supplier_name': 'ТОО «Пример»', 'amount': Decimal('2950'), 'terms': 'доставка 2 дня', 'is_recommended': True},
          {'supplier_name': 'ИП Другой', 'amount': None, 'terms': None, 'is_recommended': False}]


class ReportTests(unittest.TestCase):
    def build(self, **overrides):
        return report.build_workbook([request_row(**overrides)], {17: ITEMS}, offers_by_request={17: OFFERS},
                                     filters_text='тест', total=1)

    def cells(self, **overrides):
        sheet = load_workbook(self.build(**overrides))['Заявки']
        headers = [cell.value for cell in sheet[1]]
        return {header: sheet.cell(row=2, column=index + 1) for index, header in enumerate(headers)}

    def test_context_sheet_comes_first_and_all_columns_are_present(self):
        book = load_workbook(self.build())
        self.assertEqual(book.sheetnames, ['Контекст', 'Заявки'])
        headers = [cell.value for cell in book['Заявки'][1]]
        self.assertEqual(headers, [column[0] for column in report.COLUMNS])
        for header in ('Тип заявки', 'Этап', 'Компания-плательщик', 'Тип объекта', 'Категория учёта',
                       'Способ оплаты', 'Варианты поставщиков', 'Согласовал', 'Закрывающие документы'):
            self.assertIn(header, headers)

    def test_numbers_are_text_where_leading_zeros_matter(self):
        row = self.cells()
        self.assertEqual(row['БИН поставщика'].value, '000000000123')
        self.assertEqual(row['БИН поставщика'].number_format, '@')
        self.assertEqual(row['Договор'].value, '0025')
        self.assertEqual(row['Номер счёта'].value, '00012')
        self.assertEqual(row['Сумма'].value, 2950.0)
        self.assertEqual(row['Итоговая сумма'].value, 2500.0, 'сумма минус возврат')
        self.assertEqual(row['Описание и обоснование'].value, '=срочно, закончилась')
        self.assertNotEqual(row['Описание и обоснование'].data_type, 'f', 'текст с «=» не должен стать формулой')
        self.assertEqual(row['Срок'].value.date(), date(2026, 10, 30))

    def test_full_card_number_never_leaves_in_the_export(self):
        """П. 5.2: полный номер — по правам; файл выгрузки уходит из раздела, в нём только маска."""
        headers = [column[0] for column in report.COLUMNS]
        self.assertNotIn('Номер карты', headers)
        row = self.cells(payment_method='card', card_recipient='employee', card_holder_name='Иванов Иван',
                         card_mask='•••• 5432', card_number_enc='секрет')
        self.assertEqual(row['Карта'].value, '•••• 5432')
        self.assertEqual(row['Получатель по карте'].value, 'Карта сотрудника · Иванов Иван')
        self.assertEqual(row['Способ оплаты'].value, 'Пополнение банковской карты')
        invoice = self.cells(card_mask='•••• 5432')
        self.assertIsNone(invoice['Карта'].value, 'у оплаты по счёту карты нет')

    def test_labels_are_the_words_from_the_screen(self):
        source = META_PATH.read_text(encoding='utf-8')
        for code, label in report.STATE_LABELS.items():
            self.assertRegex(source, r"%s: \{ label: '%s'" % (code, re.escape(label)), code)
        row = report.enrich(request_row(), ITEMS, OFFERS)
        self.assertEqual(row['kind_label'], 'Новый закуп')
        self.assertEqual(row['stage_text'], 'Оплата')
        self.assertEqual(row['object_label'], 'Товар')
        self.assertEqual(row['accounting_label'], 'Расходный материал')
        self.assertEqual(row['method_label'], 'Оплата счёта')
        self.assertEqual(row['approver_label'], 'Поставщик «ТОО «Пример»» закреплён за согласующим')
        self.assertEqual(report.enrich(request_row(status='done', state='done'), [], [])['stage_text'], '',
                         'у закрытой заявки этап уже сказан состоянием')

    def test_items_and_offers_are_described(self):
        row = report.enrich(request_row(), ITEMS, OFFERS)
        self.assertEqual(row['items_text'],
                         'Бумага А4 — 1 уп × 2 500 = 2 500\nКарандаш — 3 шт × 150 = 450')
        self.assertEqual(row['offers_text'],
                         '★ ТОО «Пример» — 2 950; доставка 2 дня\nИП Другой — цена не указана')
        self.assertEqual(row['alternatives_label'], '')
        alone = report.enrich(request_row(no_alternatives=True, no_alternatives_reason='single_supplier',
                                          no_alternatives_comment='возит только он'), [], [])
        self.assertEqual(alone['alternatives_label'], 'Отсутствуют: Единственный поставщик; возит только он')

    def test_item_line_shows_its_own_sum(self):
        """В строке позиции видна формула целиком; дробное количество округляется до тиына
        так же, как в карточке и в форме."""
        row = report.enrich(request_row(), [
            {'name': 'Сахар', 'quantity': Decimal('2.500'), 'unit': 'кг', 'unit_price': Decimal('10.01')},
            {'name': 'Доставка', 'quantity': Decimal('1'), 'unit': None, 'unit_price': Decimal('1500')}])
        self.assertEqual(row['items_text'],
                         'Сахар — 2.5 кг × 10,01 = 25,03\nДоставка — 1 × 1 500 = 1 500')

    def test_closing_documents_are_reported_only_after_payment(self):
        self.assertEqual(report.enrich(request_row(), [], [])['docs_label'], '')
        paid = report.enrich(request_row(paid_on=date(2026, 10, 3), closing_docs_status='scan'), [], [])
        self.assertEqual(paid['docs_label'], 'Скан получен')

    def test_text_warning_patch_targets_the_second_sheet(self):
        calls = []

        def patch(stream, sqref, sheet_path):
            calls.append((sqref, sheet_path))
            return stream

        report.build_workbook([request_row()], {}, text_warning_patch=patch)
        self.assertEqual(len(calls), 1)
        sqref, sheet_path = calls[0]
        self.assertEqual(sheet_path, 'xl/worksheets/sheet2.xml')
        self.assertIn('2:', sqref)


class CardNumberTests(unittest.TestCase):
    """П. 5.2: в заявке хранится полный номер карты — но не открытым текстом."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {'JWT_SECRET': 'тестовый секрет', 'PAYMENTS_CARD_KEY': ''})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_number_is_encrypted_and_restored(self):
        token = cards.encrypt('4400 4302 1234 5678')
        self.assertNotIn('4400430212345678', token)
        self.assertNotIn('5678', token)
        self.assertEqual(cards.decrypt(token), '4400430212345678')
        self.assertNotEqual(token, cards.encrypt('4400430212345678'), 'один номер шифруется каждый раз иначе')
        self.assertIsNone(cards.encrypt(''))
        self.assertIsNone(cards.decrypt(None))

    def test_another_key_cannot_read_the_number(self):
        token = cards.encrypt('4400430212345678')
        with mock.patch.dict(os.environ, {'JWT_SECRET': 'другой секрет'}):
            self.assertIsNone(cards.decrypt(token), 'нечитаемый номер — маска на экране, а не падение')
        with mock.patch.dict(os.environ, {'JWT_SECRET': 'другой секрет', 'PAYMENTS_CARD_KEY': 'свой ключ раздела'}):
            self.assertIsNone(cards.decrypt(token))

    def test_own_key_can_be_introduced_later(self):
        """Завели PAYMENTS_CARD_KEY — прежние номера читаются и перешифровываются своим ключом."""
        old = cards.encrypt('4400430212345678')
        self.assertFalse(cards.has_own_key())
        self.assertIsNone(cards.rotated(old), 'ключ один — перешифровывать нечего')
        with mock.patch.dict(os.environ, {'PAYMENTS_CARD_KEY': 'свой ключ раздела'}):
            self.assertTrue(cards.has_own_key())
            self.assertEqual(cards.decrypt(old), '4400430212345678', 'старый номер не потерян')
            fresh = cards.rotated(old)
            self.assertTrue(fresh and fresh != old)
            self.assertIsNone(cards.rotated(fresh), 'уже под своим ключом')
            # Теперь секрет входа можно менять: номер держится на своём ключе.
            with mock.patch.dict(os.environ, {'JWT_SECRET': 'новый секрет'}):
                self.assertEqual(cards.decrypt(fresh), '4400430212345678')
                self.assertIsNone(cards.decrypt(old), 'неперешифрованный номер смену секрета не переживает')
                self.assertIsNone(cards.rotated(old), 'нечитаемое не перешифровать')
        self.assertIsNone(cards.rotated(None))

    def test_no_key_means_no_card_requests(self):
        with mock.patch.dict(os.environ, {'JWT_SECRET': '', 'PAYMENTS_CARD_KEY': ''}):
            self.assertFalse(cards.key_ready())
            with self.assertRaises(cards.CardKeyMissing):
                cards.encrypt('4400430212345678')
        self.assertTrue(cards.key_ready())

    def test_mask_format_and_checks(self):
        self.assertEqual(cards.mask(cards.last4('4400430212345678')), '•••• 5678')
        self.assertEqual(cards.mask(''), '')
        self.assertEqual(cards.pretty('4400430212345678'), '4400 4302 1234 5678')
        self.assertEqual(cards.problem(''), 'Укажите номер карты')
        self.assertEqual(cards.problem('1234'), 'Номер карты: от 13 до 19 цифр')
        self.assertEqual(cards.problem('4400 4302 1234 5678'), '')

    def test_card_limits_match_the_frontend(self):
        source = META_PATH.read_text(encoding='utf-8')
        self.assertIn('[min, max] = [%s, %s]' % (cards.MIN_DIGITS, cards.MAX_DIGITS), source)


class NotificationTextTests(unittest.TestCase):
    """П. 17: десять событий. Заголовок говорит, ЧТО сделать, тело — по какой заявке."""

    REQUEST = {'id': 12, 'expense_name': 'Бумага <A4>', 'amount': 25000, 'initiator_name': 'Иванов',
               'counterparty_name': 'ТОО «Канцлер»', 'due_on': date(2026, 10, 20)}

    def events(self):
        request = self.REQUEST
        return {
            'назначение новой задачи': notify.task(request, {'kind': 'invoice_payment'}),
            'возврат на доработку': notify.returned(request, by_name='Сауле', comment='Уточните цену'),
            'согласование': notify.approved(request, by_name='Сауле'),
            'отклонение': notify.rejected(request, by_name='Сауле', comment='Не нужно'),
            'оплата': notify.paid(request),
            'пополнение карты': notify.topped_up(request),
            'необходимость приложить чек': notify.task(request, {'kind': 'receipt_confirm'}),
            'необходимость приложить закрывающие документы': notify.docs_needed(request),
            'приближение срока': notify.due_soon(request, 2),
            'просрочка': notify.overdue(request, 3),
        }

    def test_every_event_of_clause_17_has_a_text(self):
        events = self.events()
        self.assertEqual(len(events), 10)
        for name, event in events.items():
            with self.subTest(event=name):
                self.assertTrue(event['title'])
                self.assertIn('№12', event['body'])
                self.assertIn('Заявка №12', event['telegram'])
        titles = {name: event['title'] for name, event in events.items()}
        self.assertEqual(titles['назначение новой задачи'], 'Новый счёт на оплату')
        self.assertEqual(titles['необходимость приложить чек'], 'Приложите чек')
        self.assertEqual(titles['возврат на доработку'], 'Заявку вернули на доработку')
        self.assertEqual(titles['просрочка'], 'Заявка просрочена на 3 дн.')
        self.assertEqual(events['просрочка']['tone'], 'warning')

    def test_cancel_notice_and_admin_fallback(self):
        cancelled = notify.cancelled(self.REQUEST, comment='Передумали')
        self.assertEqual((cancelled['kind'], cancelled['title']), ('cancelled', 'Заявка отменена'))
        self.assertIn('Передумали', cancelled['body'])
        task = notify.task(self.REQUEST, {'kind': 'manager_approval'})
        for_admin = notify.unreachable(task)
        self.assertEqual(for_admin['title'], task['title'])
        self.assertIn('раздел не открыт', for_admin['body'])
        self.assertIn('№12', for_admin['body'])
        self.assertIn('раздел не открыт', for_admin['telegram'])
        self.assertNotIn('раздел не открыт', task['body'], 'исходное уведомление не тронуто')

    def test_every_task_kind_has_a_plain_title(self):
        for item in workflow.SUBTASKS:
            if item['kind'] != workflow.KIND_INITIATION:
                self.assertIn(item['kind'], notify.TASK_TITLES, item['kind'])

    def test_telegram_text_is_escaped_and_links_to_the_request(self):
        event = notify.task(self.REQUEST, {'kind': 'approval'})
        self.assertIn('Бумага &lt;A4&gt;', event['telegram'])
        self.assertNotIn('<A4>', event['telegram'])
        self.assertEqual(notify.request_link('https://portal.example/OTP/', 12),
                         'https://portal.example/OTP/?view=payments&request=12')
        self.assertIsNone(notify.request_link('', 12))
        self.assertIsNone(notify.reply_markup(None))
        markup = notify.reply_markup('https://portal.example/?view=payments&request=12')
        self.assertEqual(markup['inline_keyboard'][0][0]['text'], 'Открыть заявку')

    def test_brief_is_one_line_about_the_request(self):
        self.assertEqual(notify.brief(self.REQUEST), '№12 · Бумага <A4> · 25 000 ₸')


if __name__ == '__main__':
    unittest.main()
