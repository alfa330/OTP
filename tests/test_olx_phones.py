# -*- coding: utf-8 -*-
"""Нормализация номеров и разбор текста обращения OLX (задача #223).

Таблица правил взята из раздела 3 ТЗ буквально — вплоть до тех же примеров, что
привёл заказчик. Номер в примерах выдуманный (775 702 51 44 из самой постановки),
поэтому страж персональных данных на этот файл не ругается.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from olx_amo import cabinets, phones  # noqa: E402


class NormalizeTests(unittest.TestCase):
    """Шесть строк таблицы ТЗ, дословно."""

    def test_table_from_specification(self):
        cases = [
            ('+7 775 702 51 44', '77757025144'),
            ('+77757025144', '77757025144'),
            ('87757025144', '77757025144'),
            ('8 (775) 702-51-44', '77757025144'),
            ('77757025144', '77757025144'),
            ('7757025144', '77757025144'),
        ]
        for raw, expected in cases:
            self.assertEqual(expected, phones.normalize(raw), raw)

    def test_unicode_dash_and_nbsp(self):
        """Юникодный дефис и неразрывный пробел — не повод не узнать номер."""
        self.assertEqual('77757025144', phones.normalize('8 775–702–51–44'))

    def test_rejects_what_does_not_fit_the_mask(self):
        for bad in ('', None, '757025144', '997757025144', '+996 555 123456',
                    'нет номера', '8775702514', '0757025144'):
            self.assertIsNone(phones.normalize(bad), repr(bad))

    def test_eight_is_replaced_only_at_the_head_of_eleven_digits(self):
        """«8» превращается в «7» лишь как код выхода, а не где попало."""
        self.assertEqual('77087025144', phones.normalize('87087025144'))
        self.assertIsNone(phones.normalize('88087025144'))

    def test_is_normalized(self):
        self.assertTrue(phones.is_normalized('77757025144'))
        self.assertFalse(phones.is_normalized('87757025144'))
        self.assertFalse(phones.is_normalized(''))


class ScanTests(unittest.TestCase):
    def test_pulls_number_out_of_a_sentence(self):
        text = 'Здравствуйте! Меня зовут Асель, мой номер +7 775 702 51 44, звоните'
        found = phones.scan(text)
        self.assertEqual(['77757025144'], found.phones)
        self.assertEqual('77757025144', found.first)
        self.assertFalse(found.needs_manual_review)

    def test_keeps_order_and_drops_repeats(self):
        text = 'звоните 87757025144 или 8 705 111 22 33, лучше 77757025144'
        self.assertEqual(['77757025144', '77051112233'], phones.scan(text).phones)

    def test_dates_and_amounts_are_not_phones(self):
        for text in ('заявка от 20.08.2026 в 16:50', 'зарплата 250000 тенге',
                     'машина 123 ABC 02', 'год 2026'):
            found = phones.scan(text)
            self.assertEqual([], found.phones, text)
            self.assertEqual([], found.rejected, text)
            self.assertFalse(found.has_phone, text)

    def test_foreign_number_is_flagged_for_manual_review(self):
        """ТЗ: кривой номер — не повод потерять обращение, лид всё равно заводим."""
        found = phones.scan('мой номер +996 555 123456')
        self.assertEqual([], found.phones)
        self.assertTrue(found.rejected)
        self.assertTrue(found.needs_manual_review)

    def test_no_phone_at_all_is_not_manual_review(self):
        """Разница между «номера нет» и «номер кривой» — это разные ветки робота."""
        found = phones.scan('Здравствуйте, а какие условия работы?')
        self.assertFalse(found.has_phone)
        self.assertFalse(found.needs_manual_review)

    def test_our_own_line_is_not_a_candidate_phone(self):
        """Кандидат процитировал наш автоответ — это не его номер."""
        text = 'Здравствуйте! По Вашему вопросу просьба позвонить по номеру 87008581223'
        found = phones.scan(text, own_lines=cabinets.LINE_PHONES)
        self.assertEqual([], found.phones)
        self.assertEqual(['77008581223'], found.own_lines)
        self.assertFalse(found.needs_manual_review)

    def test_own_line_does_not_hide_the_candidate_number(self):
        text = ('Здравствуйте! По Вашему вопросу просьба позвонить по номеру 87008581223. '
                'Хорошо, мой номер 8 775 702 51 44')
        found = phones.scan(text, own_lines=cabinets.LINE_PHONES)
        self.assertEqual(['77757025144'], found.phones)


class CabinetsTests(unittest.TestCase):
    def test_all_nine_cabinets_from_the_specification(self):
        self.assertEqual(9, len(cabinets.CABINETS))
        self.assertEqual(
            ['cr', 'adal', 'amanat', 'itaxi', 'global', 'jana', 'tenge', 'noltaxi', 'arenda'],
            [c.code for c in cabinets.CABINETS])

    def test_tags_are_unique_and_match_the_specification(self):
        self.assertEqual(9, len(set(cabinets.FORM_TAGS)))
        self.assertEqual('forma_olx_цр', cabinets.BY_CODE['cr'].tag_form)
        self.assertEqual('forma_arenda_olx', cabinets.BY_CODE['arenda'].tag_form)

    def test_line_phones_are_normalized_and_unique(self):
        for cab in cabinets.CABINETS:
            self.assertTrue(phones.is_normalized(cab.line_phone), cab.code)
        self.assertEqual(9, len(cabinets.LINE_PHONES))

    def test_canned_reply_carries_the_line_of_its_own_cabinet(self):
        """Текст ответа обязан звать на номер ТОГО кабинета, где пришло обращение."""
        for cab in cabinets.CABINETS:
            self.assertIn('8' + cab.line_phone[1:], cab.canned_reply, cab.code)
            self.assertTrue(cab.canned_reply.startswith('Здравствуйте!'), cab.code)

    def test_lookup_by_code_and_by_olx_id(self):
        self.assertIs(cabinets.BY_CODE['itaxi'], cabinets.get('itaxi'))
        self.assertIs(cabinets.BY_CODE['itaxi'], cabinets.get('188288847'))
        self.assertIsNone(cabinets.get('нет такого'))

    def test_env_index_follows_the_credentials_file(self):
        """Порядок кабинетов = порядок OLX_LOGIN_N в файле доступов."""
        self.assertEqual(1, cabinets.BY_CODE['cr'].env_index)
        self.assertEqual(9, cabinets.BY_CODE['arenda'].env_index)


if __name__ == '__main__':
    unittest.main()
