# -*- coding: utf-8 -*-
"""История карточки посылки: что попадает в ленту, а что нет.

ТЗ требует историю прямо («так же необходимо отобразить историю изменений»), и
именно по ней через месяц отвечают на вопрос «кому отдали коробку». Поэтому
проверяется не «пишется ли что-то», а СМЫСЛ записей:

  * повторное нажатие того же статуса событием не считается — лента из
    одинаковых строк ответа не добавляет;
  * тот же статус С комментарием — событие, но НЕ смена статуса: иначе в
    истории стояло бы «Статус изменён: В офисе → В офисе», то есть неправда;
  * правка пишет, ЧТО изменилось, и только по полям, понятным человеку;
  * открыть форму и закрыть, ничего не поменяв, — не событие.

База здесь не нужна: курсор подменён и запоминает запросы.
"""

import json
import unittest

from parcels import queries


class _RecordingCursor:
    """Курсор-двойник: помнит запросы и отдаёт заранее подготовленные ответы."""

    def __init__(self, rows=None):
        self.statements = []
        self.rows = list(rows or [])
        self.rowcount = 1

    def execute(self, statement, params=None):
        self.statements.append((' '.join(str(statement).split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return []

    def events(self):
        """Все записанные события истории: (kind, payload)."""
        found = []
        for statement, params in self.statements:
            if 'INSERT INTO parcel_events' not in statement:
                continue
            found.append((params[1], json.loads(params[4])))
        return found

    def updates(self):
        return [statement for statement, _params in self.statements
                if statement.startswith('UPDATE parcels')]


ACTOR = {'user_id': 427, 'name': 'Аликулова Айдана'}


def parcel(**overrides):
    base = {
        'id': 1, 'received_on': '2026-08-01', 'city': 'Шымкент', 'office_id': 65,
        'office_name': 'Офис Шымкент', 'office_address': 'проспект Республики 17',
        'driver_account_id': 'a' * 32, 'driver_name': 'Абдикарим Нурканат',
        'driver_phone': '+77719736925', 'kind': 'parcel', 'description': 'коробка',
        'sender': None, 'recipient': None, 'order_number': None, 'comment': None,
        'status': 'in_office',
    }
    base.update(overrides)
    return base


class _Base(unittest.TestCase):
    def setUp(self):
        self.reads = []

        def fake_read(_cursor, parcel_id):
            """Отдаёт заготовленные состояния карточки по порядку чтений."""
            self.reads.append(parcel_id)
            return dict(self.states[min(len(self.reads) - 1, len(self.states) - 1)])

        original = queries.read_parcel
        queries.read_parcel = fake_read
        self.addCleanup(setattr, queries, 'read_parcel', original)
        self.states = [parcel()]


class StatusTests(_Base):
    def test_real_change_is_logged_as_a_status_change(self):
        self.states = [parcel(), parcel(status='given_to_recipient')]
        cursor = _RecordingCursor()
        queries.set_status(cursor, 1, status='given_to_recipient', actor=ACTOR)
        events = cursor.events()
        self.assertEqual([kind for kind, _ in events], ['status'])
        self.assertEqual(events[0][1]['from'], 'in_office')
        self.assertEqual(events[0][1]['to'], 'given_to_recipient')

    def test_same_status_without_a_comment_writes_nothing(self):
        cursor = _RecordingCursor()
        queries.set_status(cursor, 1, status='in_office', actor=ACTOR)
        self.assertEqual(cursor.events(), [])
        self.assertEqual(cursor.updates(), [], 'карточку тоже не трогаем')

    def test_same_status_with_a_comment_is_a_comment_not_a_status_change(self):
        """Иначе в истории стояло бы «Статус изменён: В офисе → В офисе»."""
        cursor = _RecordingCursor()
        queries.set_status(cursor, 1, status='in_office', actor=ACTOR,
                           comment='проверили, лежит на месте')
        events = cursor.events()
        self.assertEqual([kind for kind, _ in events], ['comment'])
        self.assertEqual(events[0][1]['comment'], 'проверили, лежит на месте')
        self.assertNotIn('from', events[0][1])

    def test_status_change_stamps_who_and_when_on_the_card_itself(self):
        """ТЗ показывает «Дата изменения статуса» и «Кто изменил» в реестре."""
        self.states = [parcel(), parcel(status='given_to_sender')]
        cursor = _RecordingCursor()
        queries.set_status(cursor, 1, status='given_to_sender', actor=ACTOR)
        update = cursor.updates()[0]
        self.assertIn('status_changed_at', update)
        self.assertIn('status_changed_by', update)
        self.assertIn('status_changed_by_name', update)

    def test_missing_parcel_answers_none(self):
        self.states = [None]

        def missing(_cursor, _parcel_id):
            return None

        queries.read_parcel = missing
        cursor = _RecordingCursor()
        self.assertIsNone(queries.set_status(cursor, 999, status='in_office', actor=ACTOR))
        self.assertEqual(cursor.events(), [])


class EditTests(_Base):
    def test_edit_records_what_changed_in_human_words(self):
        self.states = [parcel(), parcel(description='два пакета', recipient='Иван Иванов')]
        cursor = _RecordingCursor()
        queries.update_parcel(cursor, 1,
                              fields={'description': 'два пакета', 'recipient': 'Иван Иванов'},
                              actor=ACTOR)
        events = cursor.events()
        self.assertEqual([kind for kind, _ in events], ['edited'])
        labels = sorted(change['label'] for change in events[0][1]['changes'])
        self.assertEqual(labels, ['Описание', 'Получатель'])
        by_field = {change['field']: change for change in events[0][1]['changes']}
        self.assertEqual(by_field['description']['from'], 'коробка')
        self.assertEqual(by_field['description']['to'], 'два пакета')

    def test_saving_without_changes_is_not_an_event(self):
        """Открыть форму и закрыть — не событие; строка без содержания шумит."""
        self.states = [parcel(), parcel()]
        cursor = _RecordingCursor()
        queries.update_parcel(cursor, 1, fields={'description': 'коробка'}, actor=ACTOR)
        self.assertEqual(cursor.events(), [])

    def test_empty_patch_touches_nothing_at_all(self):
        cursor = _RecordingCursor()
        queries.update_parcel(cursor, 1, fields={}, actor=ACTOR)
        self.assertEqual(cursor.updates(), [])
        self.assertEqual(cursor.events(), [])

    def test_service_snapshot_is_not_shown_in_the_feed(self):
        """`driver_info` — служебный снимок ответа CRM, человеку он ни о чём."""
        self.states = [parcel(), parcel(driver_name='Абдикарим Н.')]
        cursor = _RecordingCursor()
        queries.update_parcel(cursor, 1,
                              fields={'driver_name': 'Абдикарим Н.',
                                      'driver_info': {'много': 'полей'}},
                              actor=ACTOR)
        changes = cursor.events()[0][1]['changes']
        self.assertNotIn('driver_info', [change['field'] for change in changes])
        self.assertIn('driver_name', [change['field'] for change in changes])

    def test_driver_info_is_stored_as_json_not_as_a_python_dict(self):
        """psycopg2 не умеет адаптировать dict — колонка JSONB принимает строку."""
        self.states = [parcel(), parcel()]
        cursor = _RecordingCursor()
        queries.update_parcel(cursor, 1, fields={'driver_info': {'a': 1}}, actor=ACTOR)
        params = next(params for statement, params in cursor.statements
                      if statement.startswith('UPDATE parcels'))
        self.assertIsInstance(params['driver_info'], str)
        self.assertEqual(json.loads(params['driver_info']), {'a': 1})


class CreateTests(_Base):
    def test_creation_is_logged_and_the_author_is_stamped(self):
        cursor = _RecordingCursor(rows=[[7]])
        queries.create_parcel(cursor, fields={
            'received_on': '2026-08-24', 'city': 'Шымкент', 'office_id': 65,
            'office_name': 'Офис Шымкент', 'office_address': 'проспект Республики 17',
            'driver_account_id': 'a' * 32, 'kind': 'parcel', 'description': 'коробка',
        }, actor=ACTOR)
        events = cursor.events()
        self.assertEqual([kind for kind, _ in events], ['created'])
        self.assertEqual(events[0][1]['status'], 'in_office')
        insert = next(statement for statement, _p in cursor.statements
                      if statement.startswith('INSERT INTO parcels'))
        for column in ('created_by', 'created_by_name', 'status_changed_at',
                       'status_changed_by', 'status_changed_by_name'):
            self.assertIn(column, insert)

    def test_new_card_already_has_a_status_stamp(self):
        """«—» в колонке «Кто изменил статус» читалось бы как «статус не поставлен»."""
        cursor = _RecordingCursor(rows=[[7]])
        queries.create_parcel(cursor, fields={
            'received_on': '2026-08-24', 'city': 'Тараз', 'office_id': 62,
            'driver_account_id': 'a' * 32, 'kind': 'document', 'description': 'документы',
        }, actor=ACTOR)
        params = next(params for statement, params in cursor.statements
                      if statement.startswith('INSERT INTO parcels'))
        self.assertEqual(params['actor_id'], 427)
        self.assertEqual(params['actor_name'], 'Аликулова Айдана')


if __name__ == '__main__':
    unittest.main()
