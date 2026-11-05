# -*- coding: utf-8 -*-
"""«Доска почёта» во вкладке «Ивентов» (задача #318).

Что сторожится:
  * справочник направлений и номинаций один у сервера и у фронта
    (honor_board/catalog.py ↔ src/components/events/honorBoardMeta.js);
  * записи доски приводятся к строкам номинаций, незаполненное не мешает
    черновику, а публикации не хватает ровно того, что пусто;
  * ручки: доску видят все, ведут тренер и супер-админ; черновик сверяет
    revision; опубликованное — снимок заполненных строк, правка после
    публикации до сотрудников не доходит, пока её не опубликуют.

SQL здесь не исполняется — слой запросов подменён памятью; настоящий SQL
проверяет tests/test_honor_board_postgres.py на локальном Postgres.
"""

import json
import shutil
import subprocess
import sys
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from honor_board import catalog  # noqa: E402
from honor_board import queries as honor_queries  # noqa: E402
from honor_board import routes as honor_routes  # noqa: E402

META_JS = ROOT / 'src' / 'components' / 'events' / 'honorBoardMeta.js'
TODAY = date(2026, 11, 4)

# Люди выдуманы.
PEOPLE = {
    1: {'name': 'Тренерова Алия Маратовна', 'role': 'trainer'},
    2: {'name': 'Админов Сергей', 'role': 'super_admin'},
    3: {'name': 'Операторова Дина', 'role': 'operator'},
    4: {'name': 'Абенова Дана Маратовна', 'role': 'operator', 'department_id': 20},
    5: {'name': 'Жумабеков Ерлан Нурланович', 'role': 'operator', 'department_id': 20},
    6: {'name': 'Ким Виктория', 'role': 'operator', 'department_id': 20},
    7: {'name': 'Руководов Руслан', 'role': 'admin'},
    8: {'name': 'Супервайзерова Сауле', 'role': 'sv'},
    9: {'name': 'Главова Фарида', 'role': 'admin'},       # глава отдела «Фронт-офис»
    10: {'name': 'Главов Олжас', 'role': 'admin'},        # глава отдела ОП
}
HEADED = {9: ['front_office'], 10: ['op']}


class CatalogMirrorTests(unittest.TestCase):
    """Справочник фронта — тот же, что у сервера: доска не назовёт номинацию иначе."""

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required')
    def test_frontend_catalog_matches_server(self):
        script = (
            "import('" + META_JS.as_uri() + "').then((m) => console.log(JSON.stringify({"
            "departments: m.HONOR_DEPARTMENTS, main: m.MAIN_NOMINATION, custom: m.CUSTOM_NOMINATION,"
            "limits: [m.NAME_MAX, m.TITLE_MAX, m.DESCRIPTION_MAX, m.CUSTOM_MAX_NOMINATIONS],"
            "managers: m.HONOR_MANAGER_ROLES, heads: m.HONOR_HEAD_MANAGED_CODES})))"
        )
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script],
                                capture_output=True, text=True, encoding='utf-8', check=True)
        front = json.loads(result.stdout)
        # Догадка фронта для первого кадра — то же правило «кто ведёт», что у сервера.
        self.assertEqual(sorted(front['managers']), sorted(honor_routes.MANAGER_ROLES))
        self.assertEqual(front['heads'], sorted(set(honor_routes.HEAD_MANAGED_DEPARTMENTS.values())))
        self.assertEqual(front['main'], catalog.MAIN_NOMINATION)
        self.assertEqual(front['custom'], catalog.CUSTOM_NOMINATION)
        self.assertEqual(front['limits'], [catalog.NAME_MAX, catalog.TITLE_MAX, catalog.DESCRIPTION_MAX,
                                           catalog.CUSTOM_MAX_NOMINATIONS])
        self.assertEqual(
            [(d['key'], d['label'], d['title'], d['custom']) for d in front['departments']],
            [(d['key'], d['label'], d['title'], d['custom']) for d in catalog.DEPARTMENTS],
        )
        for front_department, server_department in zip(front['departments'], catalog.DEPARTMENTS):
            self.assertEqual(
                [(n['key'], n['title'], n['reason']) for n in front_department['nominations']],
                [tuple(n) for n in server_department['nominations']],
                server_department['key'],
            )

    def test_main_nomination_leads_every_department(self):
        # ТЗ: ключевая номинация «Лучший сотрудник отдела» есть у всех трёх направлений.
        for item in catalog.DEPARTMENTS:
            self.assertEqual(item['nominations'][0][0], catalog.MAIN_NOMINATION, item['key'])
        self.assertEqual(catalog.DEPARTMENT_KEYS, ('op', 'szov', 'front_office'))
        self.assertEqual([code for code, _t, _r in catalog.nominations('op')],
                         ['best_employee', 'guru_sales', 'master_yr', 'best_verifier'])
        self.assertEqual([code for code, _t, _r in catalog.nominations('szov')],
                         ['best_employee', 'productivity_champion', 'first_class_manager', 'chat_star'])
        self.assertTrue(catalog.department('front_office')['custom'])


class PeriodTests(unittest.TestCase):
    def test_parse_period_bounds(self):
        self.assertEqual(catalog.parse_period('2026-10', TODAY), date(2026, 10, 1))
        self.assertEqual(catalog.parse_period(' 2025-01 ', TODAY), date(2025, 1, 1))
        # Текущий месяц ещё не закончился: итоги — «по итогам прошедшего месяца».
        for raw in ('2026-11', '2026-12', '2024-12', '2026-13', '2026-1', '2026/10', '', None, 'abc'):
            self.assertIsNone(catalog.parse_period(raw, TODAY), raw)

    def test_default_period_is_previous_month(self):
        self.assertEqual(catalog.default_period(TODAY), date(2026, 10, 1))
        self.assertEqual(catalog.default_period(date(2027, 1, 15)), date(2026, 12, 1))

    def test_selectable_periods_start_at_last_closed_month(self):
        periods = catalog.selectable_periods(TODAY)
        self.assertEqual(periods[0], date(2026, 10, 1))
        self.assertEqual(periods[-1], catalog.FIRST_PERIOD)
        self.assertEqual(len(periods), 22)
        self.assertEqual(len(catalog.selectable_periods(date(2030, 6, 1))), catalog.SELECTABLE_MONTHS)

    def test_period_label(self):
        self.assertEqual(catalog.period_label(date(2026, 9, 1)), 'Сентябрь 2026')
        self.assertEqual(catalog.period_key(date(2026, 9, 1)), '2026-09')


class EntryRulesTests(unittest.TestCase):
    def test_fixed_department_keeps_catalog_order_and_titles(self):
        entries = catalog.normalize_entries('op', [
            {'nomination': 'best_verifier', 'user_id': 6, 'name': ' Виктория   Ким ', 'title': 'Чужое название'},
            {'nomination': 'best_employee', 'user_id': '4', 'name': 'Дана Абенова',
             'description': 'Строка 1   \r\n\r\n\r\n\tСтрока 2  '},
        ])
        self.assertEqual([entry['nomination'] for entry in entries],
                         ['best_employee', 'guru_sales', 'master_yr', 'best_verifier'])
        self.assertEqual(entries[0]['user_id'], 4)
        self.assertEqual(entries[0]['description'], 'Строка 1\n\n Строка 2')
        self.assertEqual(entries[3]['name'], 'Виктория Ким')
        # Название фиксированной номинации — из справочника, не из запроса.
        self.assertEqual(entries[3]['title'], 'Лучший верификатор')
        self.assertEqual(entries[1], catalog.blank_entry('guru_sales', 'Гуру продаж'))

    def test_bad_entries_are_rejected(self):
        cases = [
            ('op', [{'nomination': 'best_employee'}, {'nomination': 'best_employee'}], 'повторяется'),
            ('op', [{'nomination': 'custom', 'title': 'Своя'}], 'Неизвестная'),
            ('szov', [{'nomination': 'guru_sales'}], 'Неизвестная'),
            ('op', {'nomination': 'best_employee'}, 'неверно'),
            ('op', ['строка'], 'неверно'),
            ('op', [{'nomination': 'best_employee', 'user_id': True}], 'Сотрудник'),
            ('op', [{'nomination': 'best_employee', 'user_id': -3}], 'Сотрудник'),
            ('op', [{'nomination': 'best_employee', 'user_id': '①'}], 'Сотрудник'),
            ('op', [{'nomination': 'best_employee', 'name': 'я' * (catalog.NAME_MAX + 1)}], 'Имя'),
            ('op', [{'nomination': 'best_employee', 'description': 'я' * (catalog.DESCRIPTION_MAX + 1)}], 'Описание'),
            ('front_office', [{'nomination': 'custom', 'title': 'я' * (catalog.TITLE_MAX + 1)}], 'Название'),
            # Не строки в текстовых полях — ошибка, а не str() от объекта.
            ('op', [{'nomination': 'best_employee', 'name': {'a': 1}}], 'неверно'),
            ('op', [{'nomination': 'best_employee', 'description': 12}], 'неверно'),
            ('front_office', [{'nomination': 'custom', 'title': ['Лучший']}], 'неверно'),
            ('hr', [], 'направление'),
        ]
        for department_key, raw, fragment in cases:
            with self.subTest(raw=str(raw)[:60]):
                with self.assertRaises(catalog.EntriesError) as caught:
                    catalog.normalize_entries(department_key, raw)
                self.assertIn(fragment, str(caught.exception))

    def test_front_office_holds_main_plus_three_own(self):
        own = [{'nomination': 'custom', 'title': f'Номинация {index}'} for index in range(3)]
        entries = catalog.normalize_entries('front_office', own + [{'nomination': 'best_employee', 'user_id': 4}])
        self.assertEqual([entry['nomination'] for entry in entries], ['best_employee', 'custom', 'custom', 'custom'])
        self.assertEqual(entries[0]['title'], catalog.MAIN_TITLE)
        with self.assertRaises(catalog.EntriesError):
            catalog.normalize_entries('front_office', own + [{'nomination': 'custom', 'title': 'Четвёртая своя'}])

    def test_blank_slots(self):
        self.assertEqual([entry['nomination'] for entry in catalog.blank_slots('szov')],
                         ['best_employee', 'productivity_champion', 'first_class_manager', 'chat_star'])
        self.assertEqual(catalog.blank_slots('front_office'),
                         [catalog.blank_entry('best_employee', catalog.MAIN_TITLE), catalog.blank_entry('custom')])
        # Свои номинации прошлого месяца — без победителей, пустые названия не в счёт, не больше трёх.
        slots = catalog.blank_slots('front_office', ['Лучший кассир', '', '  ', 'А', 'Б', 'В'])
        self.assertEqual([entry['title'] for entry in slots], [catalog.MAIN_TITLE, 'Лучший кассир', 'А', 'Б'])
        self.assertEqual(catalog.blank_slots('hr'), [])

    def test_restore_slots_skips_what_catalog_no_longer_knows(self):
        stored = [
            {'nomination': 'retired_award', 'user_id': 4, 'name': 'Дана'},
            {'nomination': 'guru_sales', 'user_id': 5, 'name': 'Ерлан', 'description': 'я' * 999},
            {'nomination': 'best_employee', 'user_id': 6, 'name': 'Виктория'},
            'мусор',
        ]
        entries = catalog.restore_slots('op', stored)
        self.assertEqual(entries[0]['user_id'], 6)
        # Описание длиннее нынешнего предела не роняет окно — строка просто пустая.
        self.assertEqual(entries[1], catalog.blank_entry('guru_sales', 'Гуру продаж'))
        self.assertEqual(catalog.restore_slots('op', None), catalog.blank_slots('op'))

    def test_revert_keeps_own_nomination_without_winner(self):
        draft = catalog.normalize_entries('front_office', [
            {'nomination': 'best_employee', 'user_id': 4, 'name': 'Дана', 'description': 'Черновой стих'},
            {'nomination': 'custom', 'title': 'Лучший наставник', 'user_id': 5, 'name': 'Ерлан', 'description': 'Учит'},
            {'nomination': 'custom', 'title': 'Скорость ответа'},
        ])
        published = catalog.published_snapshot(catalog.normalize_entries('front_office', [
            {'nomination': 'best_employee', 'user_id': 4, 'name': 'Дана', 'description': 'Стих'},
            {'nomination': 'custom', 'title': 'Лучший наставник', 'user_id': 5, 'name': 'Ерлан', 'description': 'Учит'},
        ]))
        reverted = catalog.revert_slots('front_office', published, draft)
        self.assertEqual([(entry['title'], entry['description']) for entry in reverted], [
            ('Лучший сотрудник отдела', 'Стих'), ('Лучший наставник', 'Учит'), ('Скорость ответа', ''),
        ])
        self.assertEqual(catalog.published_snapshot(reverted), published)
        self.assertEqual(catalog.revert_slots('op', published[:1], []), catalog.restore_slots('op', published[:1]))

    def test_publish_issues(self):
        self.assertEqual(catalog.publish_issues(catalog.blank_slots('op')),
                         [{'index': None, 'field': None, 'message': 'Выберите хотя бы одного победителя'}])
        entries = catalog.normalize_entries('front_office', [
            {'nomination': 'best_employee', 'user_id': 4, 'name': 'Дана Абенова', 'description': 'Лучшая'},
            # Название с прошлого месяца без победителя — не в счёт, на доску не выйдет.
            {'nomination': 'custom', 'title': 'Лучший кассир'},
            # Победитель есть, а названия нет.
            {'nomination': 'custom', 'title': '', 'user_id': 5, 'name': 'Ерлан', 'description': 'Быстрый'},
            {'nomination': 'custom', 'title': 'Душа офиса', 'description': 'Стих'},
        ])
        self.assertEqual(catalog.publish_issues(entries), [
            {'index': 2, 'field': 'title', 'message': 'Назовите номинацию'},
            {'index': 3, 'field': 'user', 'message': 'Выберите сотрудника'},
            {'index': 3, 'field': 'name', 'message': 'Впишите имя и фамилию'},
        ])

    def test_snapshot_keeps_only_filled_rows(self):
        entries = catalog.normalize_entries('op', [
            {'nomination': 'master_yr', 'user_id': 5, 'name': 'Ерлан Жумабеков', 'description': 'Анкеты'},
        ])
        self.assertEqual(catalog.published_snapshot(entries), [{
            'nomination': 'master_yr', 'title': 'Мастер ЯР', 'user_id': 5,
            'name': 'Ерлан Жумабеков', 'description': 'Анкеты',
        }])


class Store:
    """Память вместо базы: ровно те функции queries, что зовут ручки."""

    def __init__(self):
        self.boards = {}
        self.next_id = 1
        self.departments = {'op': {'id': 20, 'name': 'Отдел продаж'}}

    def _view(self, row):
        names = {key: value['name'] for key, value in PEOPLE.items()}
        return {
            'id': row['id'], 'period_month': row['period_month'], 'department_key': row['department_key'],
            'draft_entries': json.loads(json.dumps(row['draft_entries'])), 'revision': row['revision'],
            'updated_at': row['updated_at'], 'updated_by_name': names.get(row['updated_by']),
            'published_entries': json.loads(json.dumps(row['published_entries'])) if row['published_entries'] is not None else None,
            'published_at': row['published_at'], 'published_by_name': names.get(row['published_by']),
        }

    def board(self, _cursor, period, department_key, *, lock=False):
        row = self.boards.get((period, department_key))
        return self._view(row) if row else None

    def published_periods(self, _cursor):
        return sorted({period for (period, _key), row in self.boards.items() if row['published_at']}, reverse=True)

    def published_boards(self, _cursor, period):
        return [{'department_key': key, 'published_entries': row['published_entries'],
                 'published_at': row['published_at']}
                for (row_period, key), row in self.boards.items() if row_period == period and row['published_at']]

    def boards_between(self, _cursor, first, last):
        return [{'period_month': period, 'department_key': key, 'draft_entries': row['draft_entries'],
                 'published_entries': row['published_entries'], 'published_at': row['published_at']}
                for (period, key), row in self.boards.items() if first <= period <= last]

    def previous_custom_titles(self, _cursor, period, department_key):
        earlier = sorted((p for (p, key) in self.boards if key == department_key and p < period), reverse=True)
        if not earlier:
            return []
        entries = self.boards[(earlier[0], department_key)]['draft_entries']
        return [entry['title'] for entry in entries if entry['nomination'] == 'custom']

    def insert_board(self, _cursor, period, department_key, entries, actor_id):
        if (period, department_key) in self.boards:
            return None
        board_id = self.next_id
        self.next_id += 1
        self.boards[(period, department_key)] = {
            'id': board_id, 'period_month': period, 'department_key': department_key,
            'draft_entries': json.loads(json.dumps(entries)), 'revision': 1,
            'updated_at': datetime(2026, 11, 4, 10, 0), 'updated_by': actor_id,
            'published_entries': None, 'published_at': None, 'published_by': None,
        }
        return board_id

    def _row(self, board_id):
        return next(row for row in self.boards.values() if row['id'] == board_id)

    def update_draft(self, _cursor, board_id, entries, actor_id):
        row = self._row(board_id)
        row['draft_entries'] = json.loads(json.dumps(entries))
        row['revision'] += 1
        row['updated_by'] = actor_id

    def publish(self, _cursor, board_id, snapshot, actor_id):
        row = self._row(board_id)
        row['published_entries'] = json.loads(json.dumps(snapshot))
        row['published_at'] = datetime(2026, 11, 4, 10, 5)
        row['published_by'] = actor_id

    def unpublish(self, _cursor, board_id):
        row = self._row(board_id)
        row['published_entries'] = row['published_at'] = row['published_by'] = None

    def existing_user_ids(self, _cursor, user_ids):
        return {value for value in user_ids if value in PEOPLE}

    def user_avatars(self, _cursor, user_ids):
        return [(value, 'stand', f'avatars/{value}.webp') for value in sorted(user_ids) if value in PEOPLE]

    def people_cards(self, _cursor, user_ids):
        return [{'id': value, 'name': PEOPLE[value]['name'], 'role': PEOPLE[value]['role'],
                 'avatar_bucket': 'stand', 'avatar_blob_path': f'avatars/{value}.webp', 'direction_name': 'Основа'}
                for value in sorted(user_ids) if value in PEOPLE]

    def department_by_code(self, _cursor, code):
        return self.departments.get(code)

    def candidates(self, _cursor, department_id):
        return [{'id': value, 'name': person['name'], 'role': person['role'], 'avatar_bucket': 'stand',
                 'avatar_blob_path': f'avatars/{value}.webp' if value != 6 else None, 'direction_name': 'Основа'}
                for value, person in PEOPLE.items() if person.get('department_id') == department_id]


class _Db:
    @contextmanager
    def _get_cursor(self):
        yield object()


QUERY_NAMES = (
    'board', 'published_periods', 'published_boards', 'boards_between', 'previous_custom_titles',
    'insert_board', 'update_draft', 'publish', 'unpublish', 'existing_user_ids', 'user_avatars',
    'people_cards', 'department_by_code', 'candidates',
)


@unittest.skipIf(Flask is None, 'Flask is required')
class HonorBoardRoutesTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.requester_id = 1
        patches = [mock.patch.object(honor_queries, name, getattr(self.store, name)) for name in QUERY_NAMES]
        patches.append(mock.patch.object(honor_routes, 'schema_is_ready', lambda _cursor: True))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        def resolve_requester():
            person = PEOPLE[self.requester_id]
            return self.requester_id, (self.requester_id, None, person['name'], person['role']), None

        def avatar_url(bucket, blob):
            if not blob:
                return None
            if blob.endswith('/5.webp'):
                raise RuntimeError('GCS 403')
            return f'https://photos.example/{blob}'

        app = Flask(__name__)
        app.register_blueprint(honor_routes.build_honor_board_blueprint(
            db=_Db(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204), resolve_requester=resolve_requester,
            normalize_role=lambda role: str(role or '').lower(), avatar_url=avatar_url,
            headed_department_codes=lambda user_id: HEADED.get(user_id, []), today=lambda: TODAY,
        ))
        self.client = app.test_client()

    def as_user(self, user_id):
        self.requester_id = user_id

    def put(self, entries, revision, period='2026-10', department='op'):
        return self.client.put('/api/events/honor_board/edit', json={
            'period': period, 'department': department, 'entries': entries, 'revision': revision})

    def post(self, action, revision=None, period='2026-10', department='op'):
        body = {'period': period, 'department': department}
        if revision is not None:
            body['revision'] = revision
        return self.client.post(f'/api/events/honor_board/{action}', json=body)

    FULL = [
        {'nomination': 'best_employee', 'user_id': 4, 'name': 'Дана Абенова', 'description': 'Звонок за звонком'},
        {'nomination': 'master_yr', 'user_id': 5, 'name': 'Ерлан Жумабеков', 'description': 'Анкеты'},
    ]

    def test_everyone_sees_board_only_trainer_and_super_admin_manage(self):
        for user_id, manager in ((3, False), (8, False), (7, False), (1, True), (2, True)):
            with self.subTest(role=PEOPLE[user_id]['role']):
                self.as_user(user_id)
                response = self.client.get('/api/events/honor_board')
                self.assertEqual(response.status_code, 200)
                self.assertIs(response.get_json()['can_manage'], manager)
                edit = self.client.get('/api/events/honor_board/edit?period=2026-10&department=op')
                self.assertEqual(edit.status_code, 200 if manager else 403)
                if not manager:
                    self.assertEqual(edit.get_json()['code'], 'HONOR_MANAGE_FORBIDDEN')
                    self.assertEqual(self.put(self.FULL, 0).status_code, 403)
                    self.assertEqual(self.post('publish', 0).status_code, 403)
                    self.assertEqual(self.client.get('/api/events/honor_board/candidates?department=op').status_code, 403)
        self.assertEqual(self.store.boards, {})

    def test_new_board_and_default_period(self):
        response = self.client.get('/api/events/honor_board')
        data = response.get_json()
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual((data['periods'], data['period'], data['boards']), ([], None, {}))
        self.assertEqual(data['default_period'], '2026-10')
        self.assertEqual(data['manage_departments'], ['op', 'szov', 'front_office'])
        board = self.client.get('/api/events/honor_board/edit?period=2026-10&department=szov').get_json()
        self.assertEqual(board['board']['revision'], 0)
        self.assertIs(board['board']['saved'], False)
        self.assertEqual(len(board['board']['entries']), 4)
        self.assertEqual(board['months'][0], {'period': '2026-10', 'states': {'op': None, 'szov': None, 'front_office': None}})
        for query in ('period=2026-11&department=op', 'period=2026-12&department=op',
                      'period=2026-10&department=hr', 'department=op'):
            self.assertEqual(self.client.get(f'/api/events/honor_board/edit?{query}').status_code, 400, query)

    def test_draft_revisions_and_conflicts(self):
        first = self.put(self.FULL[:1], 0)
        self.assertEqual(first.status_code, 200)
        board = first.get_json()['board']
        self.assertEqual((board['revision'], board['state'], board['saved']), (1, 'draft', True))
        self.assertEqual(first.get_json()['people']['4']['name'], 'Абенова Дана Маратовна')
        # Вторая вкладка с устаревшей revision не затирает, а получает свежую доску.
        stale = self.put(self.FULL, 0)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()['code'], 'HONOR_CONFLICT')
        self.assertEqual(stale.get_json()['board']['revision'], 1)
        # То же содержимое — revision не растёт (окно сохраняет само, по ходу ввода).
        self.assertEqual(self.put(self.FULL[:1], 1).get_json()['board']['revision'], 1)
        self.assertEqual(self.put(self.FULL, 1).get_json()['board']['revision'], 2)
        self.assertEqual(self.put(self.FULL, 7).status_code, 409)
        missing = self.put([{'nomination': 'best_employee', 'user_id': 999}], 2)
        self.assertEqual((missing.status_code, missing.get_json()['code']), (400, 'HONOR_UNKNOWN_USER'))
        bad = self.put([{'nomination': 'nope'}], 2)
        self.assertEqual((bad.status_code, bad.get_json()['code']), (400, 'HONOR_BAD_ENTRIES'))
        self.assertEqual(self.put(self.FULL, None).status_code, 400)
        self.assertEqual(self.store.boards[(date(2026, 10, 1), 'op')]['revision'], 2)

    def test_publish_edit_revert_unpublish(self):
        self.assertEqual(self.post('publish', 0).status_code, 400)
        self.put([{**self.FULL[0], 'description': ''}], 0)
        incomplete = self.post('publish', 1)
        self.assertEqual(incomplete.status_code, 400)
        self.assertEqual(incomplete.get_json()['issues'],
                         [{'index': 0, 'field': 'description', 'message': 'Опишите заслуги'}])
        self.assertIsNone(self.store.boards[(date(2026, 10, 1), 'op')]['published_at'])

        self.put(self.FULL, 1)
        self.assertEqual(self.post('publish', 1).status_code, 409)   # опубликовать можно только то, что на экране
        published = self.post('publish', 2)
        self.assertEqual(published.status_code, 200)
        self.assertEqual(published.get_json()['board']['state'], 'published')

        self.as_user(3)
        view = self.client.get('/api/events/honor_board').get_json()
        self.assertEqual((view['periods'], view['period']), (['2026-10'], '2026-10'))
        cards = view['boards']['op']['entries']
        self.assertEqual([(card['nomination'], card['main']) for card in cards],
                         [('best_employee', True), ('master_yr', False)])
        # Фото — одним списком; отказ подписи (5) не роняет доску.
        self.assertEqual(view['photos'], {'4': 'https://photos.example/avatars/4.webp'})

        self.as_user(1)
        self.put([{**self.FULL[0], 'description': 'Черновой стих'}, self.FULL[1]], 2)
        edited = self.client.get('/api/events/honor_board/edit?period=2026-10&department=op').get_json()['board']
        self.assertEqual(edited['state'], 'changed')
        self.as_user(3)
        still = self.client.get('/api/events/honor_board?period=2026-10').get_json()['boards']['op']['entries'][0]
        self.assertEqual(still['description'], 'Звонок за звонком')   # правка до публикации не видна

        # Месяц, который сняли или которого нет среди опубликованных, — последний опубликованный.
        self.assertEqual(self.client.get('/api/events/honor_board?period=2026-09').get_json()['period'], '2026-10')

        self.as_user(1)
        reverted = self.post('revert', 3).get_json()['board']
        self.assertEqual(reverted['state'], 'published')
        self.assertEqual(reverted['entries'][0]['description'], 'Звонок за звонком')
        unpublished = self.post('unpublish')
        self.assertEqual(unpublished.get_json()['board']['state'], 'draft')
        self.assertEqual(self.post('unpublish').status_code, 409)
        self.as_user(3)
        self.assertEqual(self.client.get('/api/events/honor_board').get_json()['periods'], [])

    def test_front_office_new_month_takes_last_titles(self):
        self.put([
            {'nomination': 'best_employee', 'user_id': 4, 'name': 'Дана', 'description': 'Лучшая'},
            {'nomination': 'custom', 'title': 'Лучший кассир', 'user_id': 5, 'name': 'Ерлан', 'description': 'Быстрый'},
            {'nomination': 'custom', 'title': 'Скорость ответа'},
        ], 0, period='2026-09', department='front_office')
        # Опубликована только номинация с победителем — названия всё равно берутся все.
        self.assertEqual(self.post('publish', 1, period='2026-09', department='front_office').status_code, 200)
        board = self.client.get('/api/events/honor_board/edit?period=2026-10&department=front_office').get_json()['board']
        self.assertEqual([(entry['nomination'], entry['title'], entry['user_id']) for entry in board['entries']],
                         [('best_employee', 'Лучший сотрудник отдела', None), ('custom', 'Лучший кассир', None),
                          ('custom', 'Скорость ответа', None)])

    def test_front_office_head_manages_only_front_office(self):
        # ТЗ, п. 2: номинации Фронт-офиса указывает «тренер или руководитель».
        self.as_user(9)
        view = self.client.get('/api/events/honor_board').get_json()
        self.assertEqual((view['can_manage'], view['manage_departments']), (True, ['front_office']))
        edit = self.client.get('/api/events/honor_board/edit?period=2026-10&department=front_office')
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.get_json()['months'][0]['states'], {'front_office': None})
        self.assertEqual(self.put([{'nomination': 'best_employee', 'user_id': 4, 'name': 'Дана', 'description': 'Лучшая'}],
                                  0, department='front_office').status_code, 200)
        self.assertEqual(self.post('publish', 1, department='front_office').status_code, 200)
        for response in (
            self.client.get('/api/events/honor_board/edit?period=2026-10&department=op'),
            self.put(self.FULL, 0),
            self.post('publish', 0),
            self.client.get('/api/events/honor_board/candidates?department=szov'),
        ):
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get('/api/events/honor_board/candidates?department=front_office').status_code, 200)
        # Глава другого отдела (ОП) по ТЗ доску не ведёт.
        self.as_user(10)
        self.assertEqual(self.client.get('/api/events/honor_board').get_json()['manage_departments'], [])
        self.assertEqual(self.put(self.FULL, 0).status_code, 403)

    def test_body_must_be_an_object(self):
        for body in ([1, 2], 'abc', 123):
            for path, method in (('edit', self.client.put), ('publish', self.client.post),
                                 ('unpublish', self.client.post), ('revert', self.client.post)):
                with self.subTest(path=path, body=body):
                    response = method(f'/api/events/honor_board/{path}', json=body)
                    self.assertEqual((response.status_code, response.get_json()['code']), (400, 'HONOR_BAD_REQUEST'))
        bad = self.put([{'nomination': 'best_employee', 'name': {'a': 1}}], 0)
        self.assertEqual((bad.status_code, bad.get_json()['code']), (400, 'HONOR_BAD_ENTRIES'))
        self.assertEqual(self.store.boards, {})

    def test_months_carry_state_per_department(self):
        self.put(self.FULL, 0)
        self.post('publish', 1)
        self.put(self.FULL[:1], 0, department='szov')
        months = self.client.get('/api/events/honor_board/edit?period=2026-10&department=op').get_json()['months']
        october = next(month for month in months if month['period'] == '2026-10')
        self.assertEqual(october['states'], {'op': 'published', 'szov': 'draft', 'front_office': None})
        self.assertEqual(months[0]['period'], '2026-10')

    def test_candidates(self):
        data = self.client.get('/api/events/honor_board/candidates?department=op').get_json()
        self.assertEqual(data['department_name'], 'Отдел продаж')
        photos = {person['id']: person['photo_url'] for person in data['people']}
        self.assertEqual(photos, {4: 'https://photos.example/avatars/4.webp', 5: None, 6: None})
        self.assertEqual(self.client.get('/api/events/honor_board/candidates?department=szov').get_json()['people'], [])
        self.assertEqual(self.client.get('/api/events/honor_board/candidates?department=x').status_code, 400)


class WiringTests(unittest.TestCase):
    """Доска подключена к приложению и к развёртыванию схемы."""

    def test_blueprint_and_schema_are_wired(self):
        bot = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')
        self.assertIn('from honor_board.routes import build_honor_board_blueprint', bot)
        self.assertIn('avatar_url=_build_avatar_signed_url,', bot)
        schema = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8-sig')
        self.assertIn('self._init_honor_board_schema_tx(cursor)', schema)
        self.assertIn('from honor_board.schema import init_honor_board_schema', schema)

    def test_manager_roles_are_literal_tz(self):
        # ТЗ: вести доску — роль «Тренер»; супер-админ — владельцы портала; Фронт-офис —
        # ещё и его руководитель. Других руководителей ТЗ не называет.
        self.assertEqual(honor_routes.MANAGER_ROLES, frozenset({'trainer', 'super_admin'}))
        self.assertEqual(honor_routes.HEAD_MANAGED_DEPARTMENTS, {'front_office': 'front_office'})
        self.assertEqual(honor_routes.managed_departments('admin', ['FRONT_OFFICE', 'op']), ('front_office',))
        self.assertEqual(honor_routes.managed_departments('sv', []), ())
        self.assertEqual(honor_routes.managed_departments('trainer', []), ('op', 'szov', 'front_office'))
        bot = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')
        self.assertIn('headed_department_codes=_headed_department_codes,', bot)

    def test_events_view_has_honor_tab(self):
        events = (ROOT / 'src' / 'components' / 'events' / 'EventsView.jsx').read_text(encoding='utf-8')
        self.assertIn("{ value: 'honor', label: 'Доска почёта' }", events)
        self.assertIn('<HonorBoard user={user}', events)
        # Раздел открывается лентой: вход гасит бейдж новых постов.
        self.assertIn("const [tab, setTab] = useState('feed');", events)


if __name__ == '__main__':
    unittest.main()
