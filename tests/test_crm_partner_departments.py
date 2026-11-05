# -*- coding: utf-8 -*-
"""«Обращения» для отделов-гостей: ОП и фронт-офисы заводят «Сотрудничество».

Просьба владельца 05.11.2026: «открыть доступ для ОП и для фронт-офисов, чтобы
они могли заполнять и отправлять» обращения по сотрудничеству. Уточнение того же
дня: видеть обращения своего отдела и «Сотрудничество» всех отделов, СЗоВ
включительно («ОП может видеть обращения, оставленные СЗоВ, так же и с
фронт-офисами»), — а обращения СЗоВ других тематик, с ИИН и телефонами
водителей, нет.

Что сторожится — по слоям:

* вход       — весь отдел-гость с любой ролью и его глава. Сервер и пункт меню
               сходятся на одной таблице людей (tests/fixtures/crm_section_people.json,
               её же читает tests/crm_section_access.test.mjs);
* тематики   — гость заводит только «Сотрудничество». Каталог, очереди и все
               проверки тематик (Sapar по ИИН, реестр посылок, справочник офисов)
               ему не отдаются — и не просто спрятаны, а запрещены на сервере;
* видимость  — свой отдел и «Сотрудничество» всех отделов; обе формы правила
               (access.can_view_ticket и queries.visibility_sql) говорят одно;
* проверка   — «С Яндексом» гостя решает СВ его отдела, без СВ — глава (у
               фронт-офисов СВ нет); чужое на проверке видно, но не решается;
* настройка  — очереди и выгрузка остаются у СЗоВ и админов;
* СЗоВ       — для своих ничего не поменялось.

Ни одно из этих правил не падает само, если сломается: человек просто увидит
чужое или не увидит своё.
"""

import json
import re
import unittest
from pathlib import Path
from unittest import mock

from crm import access, queries, sapar, scenarios as sc, schema, service

from tests.test_crm_access import RecordingCursor
from tests.test_crm_routing import (
    ALL_CHATS, ALL_QUEUES, FakeCursor, build_client, chat_row, queue_row, route_row,
)

ROOT = Path(__file__).resolve().parents[1]
PEOPLE = json.loads((ROOT / 'tests' / 'fixtures' / 'crm_section_people.json')
                    .read_text(encoding='utf-8'))
DEPARTMENTS = PEOPLE['departments']

SZOV, OP, FRONT_OFFICE = DEPARTMENTS['szov'], DEPARTMENTS['op'], DEPARTMENTS['front_office']
COOP = 'cooperation'


def person(role='operator', department='op', heads=(), user_id=10, groups=()):
    """Контекст доступа в том виде, в каком его собирает queries.load_access_context."""
    return {
        'user_id': user_id,
        'name': 'Тест',
        'role': access.normalize_role(role),
        'department_id': DEPARTMENTS.get(department) if department else None,
        'department_code': department,
        'headed_department_ids': [DEPARTMENTS[code] for code in heads],
        'headed_department_codes': list(heads),
        'group_ids': list(groups),
    }


def of_table(item, user_id=10):
    return person(role=item['role'], department=item['department'], heads=item['heads'],
                  user_id=user_id)


def ticket(created_by=11, department_id=SZOV, scenario_key='sapar_sign_error',
           queue_department_id=None, status='open', review_state=None):
    return {
        'created_by': created_by,
        'department_id': department_id,
        'queue_department_id': queue_department_id,
        'scenario_key': scenario_key,
        'status': status,
        'review_state': review_state,
        'author_group_ids': [],
    }


OTHER_TOPICS = [item['key'] for item in sc.SCENARIOS if item['key'] != COOP]


# ─────────────────────────────────────────────────────────────────────────────
# Вход
# ─────────────────────────────────────────────────────────────────────────────

class GateTest(unittest.TestCase):

    def test_server_lets_in_exactly_the_table(self):
        """Та же таблица проверяет пункт меню (crm_section_access.test.mjs)."""
        for index, item in enumerate(PEOPLE['people']):
            with self.subTest(who=item['who']):
                self.assertEqual(access.can_open_section(of_table(item, user_id=100 + index)),
                                 item['open'])

    def test_table_covers_both_guests_and_every_role(self):
        """Таблица обязана держать гостей целиком, иначе сверка меню и сервера
        молча перестанет касаться самой правки."""
        covered = {(item['department'], item['role']) for item in PEOPLE['people'] if item['open']}
        for code in access.PARTNER_DEPARTMENT_SCENARIOS:
            self.assertIn((code, 'operator'), covered, code)
            self.assertTrue(any(item['open'] and code in item['heads']
                                for item in PEOPLE['people']), 'глава %s' % code)
        for role in ('operator', 'trainee', 'sv', 'trainer'):
            self.assertIn(('op', role), covered, role)

    def test_partner_head_is_not_a_global_admin(self):
        """Глава ОП — admin по роли, но назначение главой режет его отделом:
        ни всего раздела, ни очередей."""
        head = person(role='admin', department='op', heads=['op'])
        self.assertTrue(access.can_open_section(head))
        self.assertFalse(access.is_global_admin(head))
        self.assertEqual(access.visibility_scope(head), access.SCOPE_PARTNER)

    def test_creating_is_open_to_the_guest(self):
        self.assertTrue(access.can_create_ticket(person(department='front_office')))
        self.assertTrue(access.capabilities(person(department='op'))['can_create'])

    def test_qr_gate_is_the_same_for_the_guest(self):
        """QR — по должности, как у СЗоВ: оператор подтверждает сессию, СВ,
        тренер и глава — нет. Отдел-гость правило не меняет."""
        self.assertTrue(access.requires_sensitive_qr(person(role='operator', department='op')))
        self.assertTrue(access.requires_sensitive_qr(person(role='operator',
                                                            department='front_office')))
        for me in (person(role='sv', department='op'), person(role='trainer', department='op'),
                   person(role='admin', department='front_office', heads=['front_office'])):
            self.assertFalse(access.requires_sensitive_qr(me), me['role'])


# ─────────────────────────────────────────────────────────────────────────────
# Тематики
# ─────────────────────────────────────────────────────────────────────────────

class TopicsTest(unittest.TestCase):

    def test_guest_creates_cooperation_only(self):
        for me in (person(department='op'), person(role='sv', department='op'),
                   person(department='front_office'),
                   person(role='admin', department='front_office', heads=['front_office'])):
            self.assertEqual(access.allowed_scenarios(me), frozenset({COOP}))
            self.assertTrue(access.can_use_scenario(me, COOP))
            for key in OTHER_TOPICS:
                self.assertFalse(access.can_use_scenario(me, key), key)

    def test_szov_and_admins_are_not_limited(self):
        for me in (person(department='szov'), person(role='sv', department='szov'),
                   person(role='admin', department='szov', heads=['szov']),
                   person(role='super_admin', department=None),
                   person(role='admin', department=None)):
            self.assertIsNone(access.allowed_scenarios(me), me['role'])
            for item in sc.SCENARIOS:
                self.assertTrue(access.can_use_scenario(me, item['key']))

    def test_outsider_gets_an_empty_set_not_everything(self):
        """Закрыто, а не «без ограничений»: None значило бы «все тематики»."""
        me = person(department='tez')
        self.assertEqual(access.allowed_scenarios(me), frozenset())
        self.assertFalse(access.can_use_scenario(me, COOP))

    def test_guest_topics_are_real_selectable_topics(self):
        """Опечатка в ключе не упала бы: гость получил бы пустой мастер."""
        for code, keys in access.PARTNER_DEPARTMENT_SCENARIOS.items():
            self.assertTrue(keys, code)
            for key in keys:
                topic = sc.get(key)
                self.assertIsNotNone(topic, '%s: нет тематики %r' % (code, key))
                self.assertFalse(topic.get('entry_only'), key)
                self.assertFalse(topic.get('final_outcome'), key)

    def test_check_by_iin_is_closed_to_the_guest(self):
        """Вход «iTaxi Sapar» проверяет водителя по ИИН — гостю его категории
        закрыты, значит и сама проверка."""
        entries = sc.public_entries()
        self.assertTrue(entries)
        for entry in entries:
            self.assertFalse(access.can_use_entry(person(department='op'), entry))
            self.assertTrue(access.can_use_entry(person(department='szov'), entry))
        self.assertFalse(access.can_use_entry(person(department='op'),
                                              {'categories': [], 'no_documents': None}))


# ─────────────────────────────────────────────────────────────────────────────
# Видимость
# ─────────────────────────────────────────────────────────────────────────────

class VisibilityTest(unittest.TestCase):

    def test_guest_sees_cooperation_of_every_department(self):
        """«ОП может видеть обращения, оставленные СЗоВ» — по своей тематике."""
        for me in (person(department='op'), person(department='front_office')):
            for department_id in (SZOV, OP, FRONT_OFFICE):
                self.assertTrue(access.can_view_ticket(
                    me, ticket(department_id=department_id, scenario_key=COOP)), department_id)

    def test_guest_does_not_see_other_topics_of_szov(self):
        """Sapar, посылки, офисы, термокороба — это ИИН и телефоны водителей."""
        me = person(department='op')
        for key in OTHER_TOPICS + [None]:
            self.assertFalse(access.can_view_ticket(me, ticket(scenario_key=key)), key)

    def test_guest_sees_own_department_whole(self):
        me = person(department='front_office')
        self.assertTrue(access.can_view_ticket(
            me, ticket(department_id=FRONT_OFFICE, scenario_key=None)))
        self.assertFalse(access.can_view_ticket(
            me, ticket(department_id=OP, scenario_key=None)), 'другой гость — только по тематике')

    def test_guest_sees_the_queue_of_own_department(self):
        """Очередь, отданная отделу (crm_queues.department_id), — его целиком,
        как и у главы вне раздела; SQL-форма — q.department_id."""
        me = person(department='op')
        self.assertTrue(access.can_view_ticket(
            me, ticket(scenario_key='office_status', queue_department_id=OP)))
        self.assertFalse(access.can_view_ticket(
            me, ticket(scenario_key='office_status', queue_department_id=FRONT_OFFICE)))

    def test_author_sees_own_ticket_anywhere(self):
        """Перевели из СЗоВ в ОП — свои старые обращения человек не теряет."""
        me = person(department='op', user_id=10)
        self.assertTrue(access.can_view_ticket(
            me, ticket(created_by=10, department_id=SZOV, scenario_key='parcel_location')))

    def test_head_of_a_guest_department(self):
        head = person(role='admin', department='front_office', heads=['front_office'])
        self.assertEqual(access.partner_department_ids(head), [FRONT_OFFICE])
        self.assertTrue(access.can_view_ticket(head, ticket(department_id=FRONT_OFFICE,
                                                            scenario_key=None)))
        self.assertFalse(access.can_view_ticket(head, ticket(department_id=SZOV)))

    def test_head_from_another_department_sees_both(self):
        """Глава отдела-гостя, сама числящаяся в другом отделе, видит обращения
        обоих: возглавляемого и своего (у своего их нет, пока он не в разделе)."""
        head = person(role='admin', department='it', heads=['front_office'])
        self.assertEqual(access.partner_department_ids(head),
                         sorted([FRONT_OFFICE, DEPARTMENTS['it']]))

    def test_scope_reaches_the_interface(self):
        """Сегменты «Мои / Все» лента рисует при любом периметре, кроме «только своё»."""
        self.assertEqual(access.capabilities(person(department='op'))['scope'],
                         access.SCOPE_PARTNER)
        self.assertNotEqual(access.SCOPE_PARTNER, access.SCOPE_OWN)

    def test_reply_and_status_follow_visibility(self):
        """«Видит — может и продолжить»: правило раздела действует и у гостя —
        дописать в обращение СЗоВ по той же компании вместо второго такого же."""
        me = person(department='op')
        coop = ticket(scenario_key=COOP)
        self.assertTrue(access.can_reply(me, coop))
        self.assertTrue(access.can_change_status(me, coop))
        sapar_ticket = ticket()
        self.assertFalse(access.can_reply(me, sapar_ticket))
        self.assertFalse(access.can_change_status(me, sapar_ticket))
        self.assertFalse(access.can_delete_ticket(me, coop))


class VisibilitySqlTest(unittest.TestCase):
    """SQL-форма того же правила: список, поиск, «На проверку», выгрузка."""

    def test_guest_filter_has_all_three_parts(self):
        sql, params = queries.visibility_sql(person(department='op'))
        self.assertNotEqual(sql, 'TRUE')
        self.assertIn('t.created_by = %(viewer_id)s', sql)
        self.assertIn('t.department_id = ANY(%(partner_departments)s)', sql)
        self.assertIn('q.department_id = ANY(%(partner_departments)s)', sql)
        self.assertIn('t.scenario_key = ANY(%(partner_scenarios)s)', sql)
        self.assertEqual(params['partner_departments'], [OP])
        self.assertEqual(params['partner_scenarios'], [COOP])

    def test_szov_stays_without_a_filter(self):
        for me in (person(department='szov'), person(role='trainer', department='szov'),
                   person(role='admin', department='szov', heads=['szov'])):
            self.assertEqual(queries.visibility_sql(me)[0], 'TRUE', me['role'])

    def test_list_search_review_and_export_compose_for_the_guest(self):
        cursor = RecordingCursor()
        me = person(role='sv', department='op')
        for search in (None, 'Альфа', '7701', '100%'):
            queries.list_tickets(cursor, me, search=search, mine=True)
            queries.list_tickets(cursor, me, search=search, review_only=True)
        queries.export_tickets(cursor, me, date_from='2026-11-01', date_to='2026-11-05')
        queries.scenario_breakdown(cursor, me)
        scoped = [sql for sql in cursor.queries
                  if 'crm_tickets t' in sql and 'JOIN crm_queues q' in sql]
        self.assertEqual(len(scoped), 10, 'восемь порций ленты, выгрузка и разбивка')
        for sql in scoped:
            self.assertIn('t.scenario_key = ANY(%(partner_scenarios)s)', sql)


# ─────────────────────────────────────────────────────────────────────────────
# Проверка «С Яндексом»
# ─────────────────────────────────────────────────────────────────────────────

def pending(department_id, created_by=158):
    return ticket(created_by=created_by, department_id=department_id, scenario_key=COOP,
                  review_state=schema.REVIEW_PENDING)


class ReviewTest(unittest.TestCase):

    def test_guest_supervisor_decides_for_own_department(self):
        sv = person(role='sv', department='op')
        self.assertTrue(access.can_review(sv, pending(OP)))

    def test_front_office_head_decides_without_supervisors(self):
        """У фронт-офисов СВ нет — проверяет глава (department_reviewer_sql)."""
        head = person(role='admin', department='front_office', heads=['front_office'])
        self.assertTrue(access.can_review(head, pending(FRONT_OFFICE)))

    def test_someone_elses_review_is_visible_but_not_decided(self):
        sv = person(role='sv', department='op')
        self.assertTrue(access.can_view_ticket(sv, pending(SZOV)))
        self.assertFalse(access.can_review(sv, pending(SZOV)))
        self.assertFalse(access.can_review(person(role='sv', department='szov'), pending(OP)))

    def test_guest_operator_never_decides(self):
        self.assertFalse(access.can_review(person(department='op'), pending(OP)))

    def test_bell_wakes_the_authors_department_not_szov(self):
        """Колокол «проверьте» будит СВ и главу отдела АВТОРА — правило не
        привязано к СЗоВ ни в SQL раздела, ни в триггере."""
        reviewer = queries.department_reviewer_sql('viewer_id', author='t.created_by',
                                                   department='t.department_id')
        self.assertNotIn('szov', reviewer)
        self.assertIn('rv.department_id = t.department_id', reviewer)


# ─────────────────────────────────────────────────────────────────────────────
# Настройка раздела остаётся у СЗоВ
# ─────────────────────────────────────────────────────────────────────────────

GUEST_MANAGERS = (
    person(role='sv', department='op'),
    person(role='admin', department='op', heads=['op']),
    person(role='admin', department='front_office', heads=['front_office']),
    person(role='trainer', department='op'),
    person(role='operator', department='front_office'),
)


class SetupStaysWithSzovTest(unittest.TestCase):

    def test_guest_never_manages_queues_or_exports(self):
        for me in GUEST_MANAGERS:
            self.assertFalse(access.can_manage_queues(me), me['role'])
            self.assertFalse(access.can_export(me), me['role'])
            caps = access.capabilities(me)
            self.assertFalse(caps['can_manage_queues'])
            self.assertFalse(caps['can_export'])

    def test_szov_and_admins_keep_it(self):
        for me in (person(role='sv', department='szov'),
                   person(role='admin', department='szov', heads=['szov']),
                   person(role='super_admin', department=None),
                   person(role='admin', department=None)):
            self.assertTrue(access.can_manage_queues(me), me['role'])
            self.assertTrue(access.can_export(me), me['role'])
        self.assertFalse(access.can_manage_queues(person(department='szov')))


# ─────────────────────────────────────────────────────────────────────────────
# Ручки: боевой Blueprint на подменённом SQL-слое
# ─────────────────────────────────────────────────────────────────────────────

COOP_CHAT = -5200000001
# Как на проде: у очереди «Сотрудничество» своего чата нет, тема уведена в группу.
COOP_QUEUE = queue_row(6, 'cooperation', 'Сотрудничество', chat_id=None)
CHATS = ALL_CHATS + [chat_row(COOP_CHAT, 'Сотрудничество')]
ROUTES = [route_row(COOP, COOP_CHAT, 'Сотрудничество')]

PARKS = {
    'coop_channel': 'Звонок', 'coop_park': 'iTaxi', 'coop_city': 'Алматы',
    'coop_type': sc.COOP_WITH_PARKS, 'coop_company': 'ТОО Альфа',
    'coop_phone': '+7 701 123 45 67', 'coop_service': 'Подключение водителей',
}
YANDEX = {
    'coop_channel': 'Звонок', 'coop_park': 'iTaxi', 'coop_city': 'Алматы',
    'coop_type': sc.COOP_WITH_YANDEX, 'coop_phone': '+7 701 123 45 67',
    'coop_comment': 'Менеджер по партнёрам, просит связаться с директором',
}


def _driver_data_is_not_asked(*_args, **_kwargs):
    raise AssertionError('гостю раздела данные водителей не запрашиваются')


class RoutesTest(unittest.TestCase):

    def setUp(self):
        self.ctx = person(department='op')
        self.created = []
        self.delivered = []
        patches = [
            mock.patch.object(queries, 'load_access_context', lambda cursor, user_id: self.ctx),
            mock.patch.object(queries, 'taxi_parks', lambda cursor, *, space_ids: []),
            mock.patch.object(queries, 'create_ticket', self._create),
            mock.patch.object(queries, 'add_message', lambda cursor, **kw: 1),
            mock.patch.object(queries, 'add_event', lambda cursor, **kw: None),
            mock.patch.object(queries, 'get_ticket',
                              lambda cursor, ticket_id, viewer_id=None: {'id': ticket_id}),
            mock.patch.object(service, 'deliver_ticket', self._deliver),
            # Проверки чужих тематик отвечают данными водителей: до них гость
            # не доходит вовсе, а не получает пустой ответ.
            mock.patch.object(sapar, 'driver_snapshot', _driver_data_is_not_asked),
            mock.patch.object(queries, 'parcel_matches', _driver_data_is_not_asked),
            mock.patch.object(queries, 'city_offices', _driver_data_is_not_asked),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _create(self, cursor, **kwargs):
        self.created.append(kwargs)
        return 110

    def _deliver(self, db, ticket_id, attachment=None, gcs=None):
        self.delivered.append(ticket_id)
        return True, None

    def client(self):
        return build_client(FakeCursor(ALL_QUEUES + [COOP_QUEUE], ROUTES, CHATS), self.ctx)

    def test_catalog_offers_cooperation_only(self):
        data = self.client().get('/api/crm/scenarios').get_json()
        self.assertEqual([item['key'] for item in data['items']], [COOP])
        self.assertEqual(data['entries'], [], 'вход по ИИН гостю не нужен')
        self.assertTrue(data['items'][0]['is_ready'], 'тема уведена в группу — готова')

    def test_szov_catalog_is_untouched(self):
        self.ctx = person(department='szov')
        data = self.client().get('/api/crm/scenarios').get_json()
        self.assertEqual([item['key'] for item in data['items']],
                         [item['key'] for item in sc.SCENARIOS])
        self.assertEqual(len(data['entries']), len(sc.public_entries()))

    def test_queue_filter_offers_cooperation_only(self):
        """Фильтр ленты по очереди: «iTaxi Sapar» и «Посылки» гостю — шум."""
        items = self.client().get('/api/crm/queues').get_json()['items']
        self.assertEqual([item['code'] for item in items], [COOP])
        self.ctx = person(department='szov')
        codes = [item['code'] for item in self.client().get('/api/crm/queues').get_json()['items']]
        self.assertIn('itaxi_sapar', codes)
        self.assertIn(COOP, codes)

    def test_other_topics_are_refused_before_any_lookup(self):
        client = self.client()
        for key in OTHER_TOPICS:
            with self.subTest(key=key):
                for path, payload in (
                        ('/api/crm/scenarios/%s/evaluate' % key, {'answers': {}}),
                        ('/api/crm/scenarios/%s/sapar' % key,
                         {'iin': '060606202020', 'period': '2026-10'}),
                        ('/api/crm/scenarios/%s/lookup' % key,
                         {'answers': {'contact_number': '87011234567',
                                      'office_city': 'Алматы'}}),
                        ('/api/crm/tickets', {'scenario_key': key, 'answers': {}})):
                    response = client.post(path, json=payload)
                    self.assertEqual(response.status_code, 403, path)
                    self.assertEqual(response.get_json()['code'], 'CRM_TOPIC_CLOSED', path)
        response = client.post('/api/crm/entry/itaxi_sapar/sapar',
                                json={'iin': '060606202020', 'period': '2026-10'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'CRM_TOPIC_CLOSED')
        self.assertEqual(self.created, [])
        self.assertEqual(self.delivered, [])

    def test_cooperation_is_created_in_the_authors_department(self):
        response = self.client().post('/api/crm/tickets',
                                      json={'scenario_key': COOP, 'answers': PARKS})
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0]['scenario_key'], COOP)
        self.assertEqual(self.created[0]['department_id'], OP)
        self.assertEqual(self.created[0]['tg_chat_id'], COOP_CHAT, 'ушло в «Сотрудничество»')
        self.assertEqual(self.delivered, [110])

    def test_yandex_cooperation_waits_for_review_as_in_szov(self):
        self.ctx = person(department='front_office')
        response = self.client().post('/api/crm/tickets',
                                      json={'scenario_key': COOP, 'answers': YANDEX})
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertTrue(response.get_json()['review'])
        self.assertEqual(self.created[0]['review_state'], schema.REVIEW_PENDING)
        self.assertEqual(self.created[0]['department_id'], FRONT_OFFICE)
        self.assertEqual(self.delivered, [], 'в группу — только после проверки')

    def test_cooperation_preview_is_open(self):
        response = self.client().post('/api/crm/scenarios/%s/evaluate' % COOP,
                                      json={'answers': PARKS})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['outcome'], sc.READY)

    def test_setup_routes_are_closed_to_the_guest_head(self):
        self.ctx = person(role='admin', department='op', heads=['op'])
        client = self.client()
        response = client.post('/api/crm/queues', json={'title': 'Своя очередь'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'CRM_FORBIDDEN')
        self.assertEqual(client.put('/api/crm/routes/%s' % COOP,
                                    json={'chat_id': None}).status_code, 403)
        self.assertEqual(client.get('/api/crm/export?date_from=2026-11-01&date_to=2026-11-05')
                         .status_code, 403)


# ─────────────────────────────────────────────────────────────────────────────
# Интерфейс: пункт меню и селектор отдела знают гостей
# ─────────────────────────────────────────────────────────────────────────────

class FrontendContractTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')

    def test_partner_codes_mirror_the_backend(self):
        found = re.search(r"const CRM_PARTNER_DEPARTMENT_CODES = \[(.*?)\];", self.app)
        self.assertIsNotNone(found, 'в App.jsx пропал список отделов-гостей')
        self.assertEqual(sorted(re.findall(r"'([a-z_]+)'", found.group(1))),
                         sorted(access.PARTNER_DEPARTMENT_SCENARIOS))

    def test_sidebar_department_filter_shows_the_item_for_guests(self):
        """Админ, выбравший в сайдбаре ОП, видит разделы ОП — «Обращения» тоже."""
        found = re.search(r"^\s*crm_tickets: \[(.*?)\],", self.app, re.M)
        self.assertIsNotNone(found)
        self.assertEqual(set(re.findall(r"'([a-z_]+)'", found.group(1))),
                         {access.SECTION_DEPARTMENT_CODE, *access.PARTNER_DEPARTMENT_SCENARIOS})

    def test_department_guard_does_not_throw_the_guest_out(self):
        """У ОП и фронт-офисов есть карта разделов отдела — гард вида выкинул бы
        их из «Обращений», не стой раздел на своём предикате раньше карты."""
        guard = self.app.index("if (view === 'crm_tickets' && canAccessCrmSection) return;")
        fallback = self.app.index('if (departmentAllowsView(user, view)) return;', guard)
        self.assertGreater(fallback, guard)


if __name__ == '__main__':
    unittest.main()
