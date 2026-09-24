"""«Мои данные» в «Профиле» оператора (задача #357).

Главное правило постановки: данные других сотрудников оператору не видны и не
доступны ни для просмотра, ни для правки, а в своих он правит только шесть
полей. Поэтому тесты держат три границы:

- кто: только оператор СЗоВ, ОП и Тез — не стажёр, не СВ, не фронт-офис;
- чьё: только своё — личность из сессии, ключ user_id в теле отклоняется;
- что: только шесть полей, от карты наружу уходят лишь 4 последние цифры.

И историю: каждая правка — строка user_history с автором-оператором.
"""

import contextlib
import json
import re
import unittest
from pathlib import Path

from my_data import fields, queries

ROOT = Path(__file__).resolve().parents[1]

CARD = '4400430112345678'


class FakeCursor:
    """Курсор-заглушка поверх одной строки users: понимает ровно тот SQL,
    что пишет my_data.queries, и копит всё выполненное."""

    def __init__(self, row):
        self.row = dict(row) if row is not None else None
        self.hr_row = dict(row) if row is not None else None
        self.executed = []
        self._pending = None

    def execute(self, sql, params=None):
        text = ' '.join(str(sql).split())
        self.executed.append((text, tuple(params or ())))
        if text.startswith('SELECT'):
            self._pending = None if self.row is None else tuple(self.row[f] for f in fields.FIELDS)
            return
        match = re.match(r'UPDATE (users|user_hr_profiles) SET (.+) WHERE (?:id|user_id) = %s$', text)
        if match:
            table, assignments = match.groups()
            names = [part.split('=')[0].strip() for part in assignments.split(',')]
            target = self.row if table == 'users' else self.hr_row
            for name, value in zip(names, params):
                target[name] = value

    def fetchone(self):
        return self._pending

    def statements(self, prefix):
        return [(sql, params) for sql, params in self.executed if sql.startswith(prefix)]


class FakeDb:
    def __init__(self, row):
        self.cursor = FakeCursor(row)

    def _get_cursor(self):
        @contextlib.contextmanager
        def cm():
            yield self.cursor
        return cm()


def base_row(**overrides):
    row = {
        'phone': '+77011234567',
        'telegram_nick': '@operator_one',
        'card_number': CARD,
        'study_place': 'КазНУ',
        'study_specialty': 'Экономика',
        'study_course': '3',
    }
    row.update(overrides)
    return row


def client_for(role='operator', department='szov', *, requester_id=42, row=None):
    from flask import Flask
    from my_data.routes import build_my_data_blueprint

    db = FakeDb(base_row() if row is None else row)
    app = Flask(__name__)
    app.register_blueprint(build_my_data_blueprint(
        db=db,
        require_api_key=lambda f: f,
        build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (requester_id, (requester_id, None, 'Тест', role), None),
        normalize_role=lambda value: str(value or '').strip().lower(),
        department_code_of=lambda user_id: department if user_id == requester_id else 'other',
    ))
    return app.test_client(), db


class FieldRulesTests(unittest.TestCase):
    def test_phone_is_normalized_to_the_employee_card_format(self):
        for raw in ('+7 701 123 45 67', '87011234567', '7011234567', '+7 (701) 123-45-67'):
            self.assertEqual(('+77011234567', None), fields.normalize_phone(raw), raw)
        self.assertEqual((None, None), fields.normalize_phone('  '))
        self.assertEqual(fields.ERROR_PHONE, fields.normalize_phone('+7 701 123')[1])

    def test_telegram_nick_gets_one_at_sign_and_link_is_unwrapped(self):
        for raw in ('operator_one', '@operator_one', '@@operator_one', 'https://t.me/operator_one', 't.me/operator_one/'):
            self.assertEqual(('@operator_one', None), fields.normalize_telegram(raw), raw)
        self.assertEqual(fields.ERROR_TELEGRAM, fields.normalize_telegram('@abc')[1])
        self.assertEqual(fields.ERROR_TELEGRAM, fields.normalize_telegram('имя фамилия')[1])

    def test_card_must_be_exactly_sixteen_digits(self):
        self.assertEqual((CARD, None), fields.normalize_card('4400 4301 1234 5678'))
        self.assertEqual((CARD, None), fields.normalize_card('4400-4301-1234-5678'))
        for bad in ('440043011234567', '44004301123456789', '4400 4301 1234 567x', ''):
            self.assertEqual(fields.ERROR_CARD, fields.normalize_card(bad)[1], bad)

    def test_only_ascii_digits_count(self):
        """В Python \\d — любые цифры Юникода; в базу они легли бы непригодным номером."""
        fullwidth = '４４００ ４３０１ １２３４ ５６７８'
        arabic = '٤٤٠٠٤٣٠١١٢٣٤٥٦٧٨'
        self.assertEqual(fields.ERROR_CARD, fields.normalize_card(fullwidth)[1])
        self.assertEqual(fields.ERROR_CARD, fields.normalize_card(arabic)[1])
        self.assertEqual(fields.ERROR_PHONE, fields.normalize_phone('７０１１２３４５６７')[1])
        self.assertEqual(fields.ERROR_PHONE, fields.normalize_phone('٧٠١١٢٣٤٥٦٧')[1])
        self.assertIsNone(fields.card_last4(arabic))

    def test_course_comes_only_from_the_list(self):
        self.assertEqual(('3', None), fields.normalize_course('3'))
        self.assertEqual(('Магистратура, 1 курс', None), fields.normalize_course('Магистратура, 1 курс'))
        self.assertEqual((None, None), fields.normalize_course(''))
        self.assertEqual(fields.ERROR_COURSE, fields.normalize_course('3 курс')[1])
        self.assertEqual(fields.ERROR_COURSE, fields.normalize_course('7')[1])

    def test_only_six_fields_are_accepted_user_id_included(self):
        clean, errors, unknown = fields.validate({'phone': '87011234567', 'user_id': 7, 'status': 'fired'})
        self.assertEqual(['status', 'user_id'], unknown)
        self.assertEqual({'phone': '+77011234567'}, clean)
        self.assertEqual({}, errors)
        self.assertEqual(['<body>'], fields.validate(['phone'])[2])

    def test_public_view_never_carries_the_full_card(self):
        view = fields.public_view(base_row())
        self.assertNotIn('card_number', view)
        self.assertEqual('5678', view['card_last4'])
        self.assertTrue(view['has_card'])
        self.assertNotIn(CARD, json.dumps(view))
        empty = fields.public_view(base_row(card_number=None))
        self.assertEqual((False, None), (empty['has_card'], empty['card_last4']))

    def test_eligibility_is_operator_of_szov_op_tez_only(self):
        for dept in ('szov', 'op', 'tez'):
            self.assertTrue(fields.is_eligible('operator', dept), dept)
        self.assertFalse(fields.is_eligible('trainee', 'szov'))
        self.assertFalse(fields.is_eligible('sv', 'szov'))
        self.assertFalse(fields.is_eligible('operator', 'front_office'))
        self.assertFalse(fields.is_eligible('operator', ''))


class SaveQueryTests(unittest.TestCase):
    def test_changed_field_goes_to_both_tables_and_history_with_operator_as_author(self):
        db = FakeDb(base_row())
        final, changed = queries.save(db.cursor, 42, {'phone': '+77019998877'})
        self.assertEqual(['phone'], changed)
        self.assertEqual('+77019998877', db.cursor.row['phone'])
        self.assertEqual('+77019998877', db.cursor.hr_row['phone'])
        (history_sql, params), = db.cursor.statements('INSERT INTO user_history')
        self.assertEqual((42, 42, 'phone', '+77011234567', '+77019998877'), params)
        self.assertIn('FOR UPDATE', db.cursor.statements('SELECT')[0][0])

    def test_nothing_changed_writes_nothing(self):
        db = FakeDb(base_row())
        final, changed = queries.save(db.cursor, 42, {'phone': '+77011234567', 'study_course': '3'})
        self.assertEqual([], changed)
        self.assertEqual([], db.cursor.statements('UPDATE'))
        self.assertEqual([], db.cursor.statements('INSERT'))

    def test_clearing_university_clears_specialty_with_its_own_history_row(self):
        db = FakeDb(base_row())
        final, changed = queries.save(db.cursor, 42, {'study_place': None})
        self.assertEqual(['study_place', 'study_specialty'], changed)
        self.assertIsNone(final['study_specialty'])
        (_sql, params), = db.cursor.statements('INSERT INTO user_history')
        self.assertEqual(
            (42, 42, 'study_place', 'КазНУ', None, 42, 42, 'study_specialty', 'Экономика', None),
            params,
        )

    def test_missing_user_returns_none(self):
        db = FakeDb(None)
        self.assertIsNone(queries.save(db.cursor, 42, {'phone': None}))


class RouteTests(unittest.TestCase):
    def test_operator_reads_own_six_fields_with_masked_card(self):
        client, db = client_for()
        response = client.get('/api/my_data')
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertEqual('5678', body['data']['card_last4'])
        self.assertNotIn(CARD, response.get_data(as_text=True))
        self.assertEqual(list(fields.COURSE_OPTIONS), body['course_options'])
        select_sql, select_params = db.cursor.statements('SELECT')[0]
        self.assertEqual((42,), select_params)

    def test_closed_for_trainee_supervisor_and_other_departments(self):
        for role, dept in (('trainee', 'szov'), ('sv', 'szov'), ('admin', 'szov'),
                           ('operator', 'front_office'), ('operator', 'hr')):
            client, db = client_for(role, dept)
            for method in ('get', 'post'):
                response = getattr(client, method)('/api/my_data', json={'phone': '87011234567'})
                self.assertEqual(403, response.status_code, (role, dept, method))
                self.assertEqual('MY_DATA_CLOSED', response.get_json()['code'])
            self.assertEqual([], db.cursor.executed, (role, dept))

    def test_other_employee_cannot_be_targeted(self):
        """Номер сотрудника в теле или в адресе ничего не меняет: пишется только свой."""
        client, db = client_for(requester_id=42)
        response = client.post('/api/my_data', json={'user_id': 7, 'phone': '87019998877'})
        self.assertEqual(400, response.status_code)
        self.assertEqual('FIELD_NOT_EDITABLE', response.get_json()['code'])
        self.assertEqual([], db.cursor.executed)

        response = client.post('/api/my_data?user_id=7', json={'phone': '87019998877'})
        self.assertEqual(200, response.status_code)
        for sql, params in db.cursor.statements('UPDATE'):
            self.assertEqual(42, params[-1], sql)
        (_sql, params), = db.cursor.statements('INSERT INTO user_history')
        self.assertEqual((42, 42), params[:2])

    def test_fields_outside_the_task_are_rejected_whole(self):
        client, db = client_for()
        response = client.post('/api/my_data', json={'rate': 0.5, 'phone': '87019998877'})
        self.assertEqual(400, response.status_code)
        self.assertEqual(['rate'], response.get_json()['fields'])
        self.assertEqual([], db.cursor.executed)

    def test_invalid_card_is_rejected_with_field_error(self):
        client, db = client_for()
        response = client.post('/api/my_data', json={'card_number': '4400 4301 1234 567'})
        self.assertEqual(400, response.status_code)
        body = response.get_json()
        self.assertEqual('INVALID_FIELDS', body['code'])
        self.assertEqual(fields.ERROR_CARD, body['errors']['card_number'])
        self.assertEqual([], db.cursor.executed)

    def test_new_card_is_saved_but_answer_still_shows_only_last_four(self):
        client, db = client_for()
        new_card = '5169 4971 0000 1111'
        response = client.post('/api/my_data', json={'card_number': new_card})
        self.assertEqual(200, response.status_code)
        self.assertEqual('5169497100001111', db.cursor.row['card_number'])
        text = response.get_data(as_text=True)
        self.assertNotIn('5169497100001111', text)
        self.assertEqual('1111', response.get_json()['data']['card_last4'])
        self.assertEqual(['card_number'], response.get_json()['changed'])


class MirrorTests(unittest.TestCase):
    """Фронт повторяет правила сервера — списки обязаны совпадать."""

    source = (ROOT / 'src/components/profile/myData.js').read_text(encoding='utf-8')

    def _js_list(self, name):
        match = re.search(r'export const %s = \[(.*?)\];' % name, self.source, re.S)
        self.assertIsNotNone(match, name)
        return re.findall(r"'([^']*)'", match.group(1))

    def test_departments_match(self):
        self.assertEqual(sorted(fields.ELIGIBLE_DEPARTMENT_CODES), sorted(self._js_list('MY_DATA_DEPARTMENT_CODES')))
        self.assertIn(f"MY_DATA_ROLE = '{fields.ELIGIBLE_ROLE}'", self.source)

    def test_courses_match_in_order(self):
        self.assertEqual(list(fields.COURSE_OPTIONS), self._js_list('COURSE_OPTIONS'))

    def test_error_texts_match(self):
        for name in ('ERROR_PHONE', 'ERROR_TELEGRAM', 'ERROR_CARD'):
            self.assertIn(getattr(fields, name), self.source, name)


class FrontendWiringTests(unittest.TestCase):
    card = (ROOT / 'src/components/profile/MyDataCard.jsx').read_text(encoding='utf-8')
    app = (ROOT / 'src/App.jsx').read_text(encoding='utf-8')

    def test_block_never_sends_an_employee_id(self):
        rules = (ROOT / 'src/components/profile/myData.js').read_text(encoding='utf-8')
        for text in (self.card, rules):
            self.assertNotIn('user_id', text)
        self.assertNotRegex(self.card, r'/api/my_data\?')

    def test_block_is_rendered_only_behind_the_eligibility_check(self):
        self.assertIn('{canEditOwnData(user) && (\n', self.app)
        self.assertEqual(1, self.app.count('<MyDataCard'))

    def test_history_labels_cover_the_six_fields(self):
        labels = (ROOT / 'src/components/modals/historyFieldLabels.js').read_text(encoding='utf-8')
        for field in fields.FIELDS:
            self.assertRegex(labels, r'\n\s+%s: ' % field, field)
        modal = (ROOT / 'src/components/modals/HistoryModal.jsx').read_text(encoding='utf-8-sig')
        self.assertNotIn('{entry.field}', modal)


if __name__ == '__main__':
    unittest.main()
