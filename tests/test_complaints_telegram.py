# -*- coding: utf-8 -*-
"""Раздел «Жалобы»: сообщение в группе, кнопки и проводка в монолиты.

Формат проверяется здесь, а не глазами в рабочем чате. Проводка — чтением
исходников, как у «Обращений»: обработчики бота обязаны стоять сразу за
«Обращениями» (aiogram идёт по порядку регистрации), Blueprint — быть
подключён, схема — разворачиваться после базового DDL.
"""

import unittest
from pathlib import Path

from complaints import catalog, schema, telegram

ROOT = Path(__file__).resolve().parents[1]


def complaint(**overrides):
    item = {'id': 12, 'target': 'call_center', 'reason_code': 'rude',
            'unit_kind': catalog.UNIT_DEPARTMENT, 'unit_name': 'Отдел продаж',
            'employee_name': 'Иванова Айгерим', 'driver_name': 'Сериков Ерлан',
            'driver_phone': '+7 701 123 45 67', 'driver_ref': None, 'city': 'Алматы',
            'event_at': '2026-09-28T14:30:00', 'description': 'Нагрубил <b>по телефону</b>',
            'created_by_name': 'Хайрихан Шерзад', 'creator_department_name': 'СЗоВ',
            'result_code': None, 'work_state': 'pending', 'training_required': False,
            'status': 'open'}
    item.update(overrides)
    return item


class RootMessageTest(unittest.TestCase):
    def test_four_blocks_of_the_specification(self):
        """«Понятно, к какому обращению относится сообщение, кто его создал, на
        что поступила жалоба и какие данные предоставил водитель»."""
        text = telegram.build_root_message(complaint())
        self.assertIn('Жалоба №12', text)
        self.assertIn('?view=complaints&amp;complaint_id=12', text)
        self.assertIn('Грубость / некорректное общение', text)
        self.assertIn('<b>Сотрудник:</b> Иванова Айгерим', text)
        self.assertIn('Сериков Ерлан · +7 701 123 45 67', text)
        self.assertIn('28.09.2026 14:30', text)
        self.assertIn('🙍 <b>Принял:</b> Хайрихан Шерзад · СЗоВ', text)

    def test_operator_text_is_escaped(self):
        text = telegram.build_root_message(complaint())
        self.assertIn('Нагрубил &lt;b&gt;по телефону&lt;/b&gt;', text)

    def test_unknown_employee_is_said_so(self):
        text = telegram.build_root_message(complaint(employee_name=None))
        self.assertIn('<b>Сотрудник:</b> не определён', text)
        text = telegram.build_root_message(complaint(target='car_rental', reason_code='terms',
                                                     unit_name=None))
        self.assertNotIn('Сотрудник', text)

    def test_status_line_follows_the_work(self):
        text = telegram.build_root_message(complaint(result_code='confirmed'))
        self.assertIn('📌 <b>Итог:</b> Жалоба подтверждена', text)
        self.assertIn('ожидает супервайзера', text)
        text = telegram.build_root_message(complaint(result_code='confirmed', work_state='done',
                                                     status='closed'))
        self.assertIn('✅ <b>Жалоба обработана</b>', text)

    def test_mentions_last(self):
        text = telegram.build_root_message(complaint(description='я' * 5000),
                                           mentions=[{'username': 'lead'}])
        self.assertTrue(text.endswith('@lead'))
        self.assertLessEqual(len(text), telegram.MESSAGE_LIMIT)


class KeyboardTest(unittest.TestCase):
    def test_main_buttons(self):
        rows = telegram.main_keyboard(complaint())['inline_keyboard']
        self.assertEqual([[b['text'] for b in row] for row in rows],
                         [['💬 Ответ водителю', '❓ Вопрос оператору'], ['📌 Итог проверки']])

    def test_closed_complaint_keeps_its_buttons(self):
        """Итог ставят раньше, чем пишут разъяснение водителю: закрытая жалоба
        кнопки «Ответ водителю» и «Вопрос оператору» не теряет (сверка с ТЗ)."""
        rows = telegram.main_keyboard(complaint(status='closed'))['inline_keyboard']
        self.assertEqual([b['text'] for b in rows[0]], ['💬 Ответ водителю', '❓ Вопрос оператору'])

    def test_event_without_time_shows_the_date_only(self):
        text = telegram.build_root_message(complaint(event_at='2026-09-28T00:00:00',
                                                     event_time_known=False))
        self.assertIn('<b>Когда:</b> 28.09.2026\n', text)
        text = telegram.build_root_message(complaint(event_at='2026-09-28T00:00:00',
                                                     event_time_known=True))
        self.assertIn('28.09.2026 00:00', text, 'настоящая полночь показывается честно')

    def test_result_on_the_button(self):
        rows = telegram.main_keyboard(complaint(result_code='partial'))['inline_keyboard']
        self.assertEqual(rows[1][0]['text'], '📌 Итог: Информация частично подтверждена')

    def test_results_keyboard_has_every_result_and_back(self):
        rows = telegram.results_keyboard(12)['inline_keyboard']
        self.assertEqual(len(rows), len(catalog.RESULTS) + 1)
        self.assertEqual(rows[-1][0]['callback_data'], 'cmp:b:12')

    def test_callback_data_fits_telegram(self):
        for item in catalog.RESULTS:
            data = telegram.callback_data(telegram.ACTION_SET_RESULT, 999999999, item['code'])
            self.assertLessEqual(len(data.encode('utf-8')), 64, data)

    def test_parse_callback(self):
        self.assertEqual(telegram.parse_callback('cmp:s:12:confirmed'),
                         {'action': 's', 'complaint_id': 12, 'value': 'confirmed'})
        for foreign in ('editsv_5', 'cmp:x:12', 'cmp:s:12:hacked', 'cmp:a:-3', 'cmp:a', None):
            self.assertIsNone(telegram.parse_callback(foreign), foreign)

    def test_prompt_mentions_the_one_who_pressed(self):
        text = telegram.build_prompt('answer', 12, user_id=777, user_name='Айгуль')
        self.assertIn('<a href="tg://user?id=777">Айгуль</a>', text)
        self.assertIn('ответом на это', text)
        markup = telegram.force_reply_markup('question')
        self.assertTrue(markup['force_reply'] and markup['selective'])

    def test_work_notice_carries_no_details(self):
        text = telegram.build_work_notice(12, 'Жалоба обработана. Сотрудник определён.')
        self.assertEqual(text, '✅ Жалоба №12: Жалоба обработана. Сотрудник определён.')


class SchemaOrderTest(unittest.TestCase):
    def test_tables_before_indexes(self):
        """CREATE TABLE → ALTER → CREATE INDEX: обратный порядок положил прод
        «Обращений» 17.08.2026."""
        source = (ROOT / 'complaints' / 'schema.py').read_text(encoding='utf-8')
        body = source[source.index('def init_complaints_schema'):]
        tables = body.index('if _is_table(statement)')
        migrations = body.index('for statement in _MIGRATIONS')
        indexes = body.index('if not _is_table(statement)')
        self.assertLess(tables, migrations)
        self.assertLess(migrations, indexes)

    def test_every_message_kind_is_known(self):
        for kind in ('root', 'prompt', 'answer', 'question', 'operator_reply', 'internal',
                     'notice'):
            self.assertIn(kind, schema.MESSAGE_KINDS)


class WiringTest(unittest.TestCase):
    BOT = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
    DATABASE = (ROOT / 'database.py').read_text(encoding='utf-8')

    def test_bot_handlers_register_right_after_crm(self):
        crm = self.BOT.index('crm_bot.register(dp, db, crm_pool, types)')
        complaints = self.BOT.index('complaints_bot.register(dp, db, crm_pool, types)')
        first_regexp = self.BOT.index("@dp.message_handler(")
        self.assertLess(crm, complaints)
        self.assertLess(complaints, first_regexp)

    def test_blueprint_is_registered_with_the_qr_gate(self):
        start = self.BOT.index('build_complaints_blueprint(')
        block = self.BOT[start:start + 600]
        self.assertIn('sensitive_access_granted=_sensitive_access_granted_for_user', block)

    def test_schema_runs_after_the_base_ddl(self):
        body = self.DATABASE[self.DATABASE.index('self._init_trainings_schema_tx(cursor)'):]
        self.assertIn('self._init_complaints_schema_tx(cursor)', body[:4000])

    def test_bell_function_knows_complaints(self):
        start = self.DATABASE.index('CREATE OR REPLACE FUNCTION bell_notify_change()')
        body = self.DATABASE[start:self.DATABASE.index('$$;', start)]
        self.assertIn("TG_TABLE_NAME = 'complaints'", body)
        self.assertIn('NEW.responsible_id', body)
        self.assertIn('OLD.responsible_id', body)


if __name__ == '__main__':
    unittest.main()
