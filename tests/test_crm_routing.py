# -*- coding: utf-8 -*-
"""Маршруты тем: «эта тема уходит не в свою группу».

По умолчанию тема (сценарий из crm/scenarios.py) уходит в группу своей
тематики — очередь с кодом queue_code. Маршрут перебивает адрес у ОДНОЙ темы.

Проверяется здесь не SQL, а два правила, цена ошибки в которых — сообщение не
тем людям:

* маршрут перебивает адрес, но НЕ переносит тему в чужую тематику: она
  остаётся там, где её ищет оператор;
* маршрут, указывающий на выключенную или непривязанную очередь, НЕ
  подменяется родной группой. Тему уводили ровно для того, чтобы её перестали
  получать в родной, и тихий возврат туда — это отправка не по адресу. Тема
  просто становится недоступной.

Ни одно из этих правил не падает и не логируется, если сломается.
"""

import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from flask import Flask

from crm import queries, routes as crm_routes, scenarios as sc, service

ROOT = Path(__file__).resolve().parents[1]


# Строка очереди в порядке queries._QUEUE_COLUMNS.
def queue_row(queue_id, code, title, chat_id=-100, is_active=True):
    return (queue_id, title, None, chat_id, 'Чат ' + title, None,
            None, 100, is_active, None, code)


class FakeCursor:
    """Курсор, который отдаёт заготовленные строки и запоминает запросы."""

    def __init__(self, queues=(), routes=()):
        self._queues = list(queues)
        self._routes = list(routes)
        self._result = []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((' '.join(sql.split()), params))
        if 'FROM crm_queues' in sql:
            self._result = self._queues
        elif 'FROM crm_topic_routes' in sql:
            self._result = self._routes
        else:
            self._result = []

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


SAPAR = queue_row(2, 'itaxi_sapar', 'iTaxi Sapar')
PARCELS = queue_row(3, 'parcels', 'Посылки')
# Очередь, заведённая руками под маршрут: кода у неё нет, домом она не бывает.
SUPPORT = queue_row(9, None, 'Техподдержка')
NO_CHAT = queue_row(11, None, 'Ещё не привязана', chat_id=None)
OFF = queue_row(12, None, 'Выключенная', is_active=False)

ALL_QUEUES = [SAPAR, PARCELS, SUPPORT, NO_CHAT, OFF]


def context(routes=()):
    return queries.routing_context(FakeCursor(ALL_QUEUES, routes))


def route_row(key, queue_id):
    return (key, queue_id, 'Админ', None)


class DefaultAddressTest(unittest.TestCase):
    def test_topic_without_a_route_goes_to_its_own_group(self):
        found = queries.resolve_route(context(), 'sapar_sign_error', 'itaxi_sapar')
        self.assertEqual(found['queue']['id'], 2)
        self.assertEqual(found['home']['id'], 2)
        self.assertFalse(found['routed'])
        self.assertTrue(found['is_ready'])

    def test_topic_of_an_unconfigured_group_has_no_address(self):
        found = queries.resolve_route(context(), 'whatever', 'нет такой очереди')
        self.assertIsNone(found['queue'])
        self.assertIsNone(found['home'])
        self.assertFalse(found['is_ready'])

    def test_queue_without_a_code_is_never_a_home(self):
        # Очередь, заведённую руками, ни один сценарий не назовёт своей: код —
        # единственная связь темы с очередью, и пустой код не совпадает ни с чем.
        found = queries.resolve_route(context(), 'sapar_sign_error', None)
        self.assertIsNone(found['home'])


class RouteOverridesAddressTest(unittest.TestCase):
    def test_route_changes_the_address_but_not_the_home(self):
        found = queries.resolve_route(
            context([route_row('sapar_service_error', 9)]),
            'sapar_service_error', 'itaxi_sapar')
        self.assertEqual(found['queue']['title'], 'Техподдержка')
        # Дом остаётся прежним: по нему тема стоит в картотеке оператора.
        self.assertEqual(found['home']['title'], 'iTaxi Sapar')
        self.assertTrue(found['routed'])
        self.assertTrue(found['is_ready'])

    def test_route_touches_only_its_own_topic(self):
        ctx = context([route_row('sapar_service_error', 9)])
        neighbour = queries.resolve_route(ctx, 'sapar_sign_error', 'itaxi_sapar')
        self.assertEqual(neighbour['queue']['title'], 'iTaxi Sapar')
        self.assertFalse(neighbour['routed'])

    def test_route_to_a_queue_without_a_chat_is_not_ready(self):
        found = queries.resolve_route(
            context([route_row('parcel_location', 11)]), 'parcel_location', 'parcels')
        self.assertEqual(found['queue']['title'], 'Ещё не привязана')
        self.assertFalse(found['is_ready'])

    def test_disabled_target_never_falls_back_to_the_home_group(self):
        # Самое дорогое правило файла: подмена адреса родной группой отправила
        # бы обращение тем самым людям, от которых тему и уводили.
        found = queries.resolve_route(
            context([route_row('parcel_location', 12)]), 'parcel_location', 'parcels')
        self.assertEqual(found['queue']['title'], 'Выключенная')
        self.assertNotEqual(found['queue']['id'], found['home']['id'])
        self.assertFalse(found['is_ready'])

    def test_route_to_a_vanished_queue_leaves_the_topic_without_an_address(self):
        found = queries.resolve_route(
            context([route_row('parcel_location', 404)]), 'parcel_location', 'parcels')
        self.assertIsNone(found['queue'])
        self.assertTrue(found['routed'])
        self.assertFalse(found['is_ready'])


class RoutingContextTest(unittest.TestCase):
    def test_disabled_queues_are_loaded_too(self):
        # Выключенная очередь не адрес, но её название нужно, чтобы объяснить
        # настройщику, куда указывает маршрут.
        titles = {q['title'] for q in context()['queues']}
        self.assertIn('Выключенная', titles)

    def test_whole_layout_costs_two_queries(self):
        cursor = FakeCursor(ALL_QUEUES, [route_row('parcel_location', 9)])
        queries.routing_context(cursor)
        self.assertEqual(len(cursor.executed), 2)

    def test_route_carries_who_and_when(self):
        found = context([route_row('parcel_location', 9)])['routes']['parcel_location']
        self.assertEqual(found['queue_id'], 9)
        self.assertEqual(found['updated_by_name'], 'Админ')


class SetRouteTest(unittest.TestCase):
    def test_setting_a_route_is_an_upsert(self):
        # Второй выбор той же темы обязан переписать строку, а не упасть на
        # первичном ключе: настройщик тыкает в список сколько захочет.
        cursor = FakeCursor()
        queries.set_topic_route(cursor, scenario_key='parcel_location', queue_id=9,
                                actor_user_id=1, actor_name='Админ')
        sql, params = cursor.executed[-1]
        self.assertIn('INSERT INTO crm_topic_routes', sql)
        self.assertIn('ON CONFLICT (scenario_key) DO UPDATE', sql)
        self.assertEqual(params[:2], ('parcel_location', 9))

    def test_returning_to_the_home_group_deletes_the_row(self):
        # Не «маршрут на родную очередь»: такая строка ничего не меняет, но
        # переживёт переименование очереди и однажды начнёт менять.
        cursor = FakeCursor()
        queries.set_topic_route(cursor, scenario_key='parcel_location', queue_id=None)
        sql, params = cursor.executed[-1]
        self.assertIn('DELETE FROM crm_topic_routes', sql)
        self.assertEqual(params, ('parcel_location',))


class SchemaContractTest(unittest.TestCase):
    """Правила, которые держит сама база."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'crm' / 'schema.py').read_text(encoding='utf-8')

    def test_one_topic_one_address(self):
        # Второй маршрут той же темы означал бы двух получателей одного
        # обращения, а ответ возвращается в одну нить — второй нити нет.
        self.assertRegex(self.source, r'scenario_key\s+VARCHAR\(64\)\s+PRIMARY KEY')

    def test_deleting_a_queue_takes_its_routes_with_it(self):
        self.assertRegex(
            self.source,
            r'queue_id\s+INTEGER NOT NULL REFERENCES crm_queues\(id\) ON DELETE CASCADE')

    def test_incoming_topics_are_looked_up_by_queue(self):
        self.assertIn('idx_crm_topic_routes_queue', self.source)

    def test_key_column_holds_the_longest_scenario_key(self):
        longest = max(len(item['key']) for item in sc.SCENARIOS)
        self.assertLessEqual(longest, 64)


class RoutableTopicsTest(unittest.TestCase):
    """Каталог сам говорит, какой теме адрес нужен, а какой — нет."""

    def test_topic_that_never_reaches_a_group_is_not_routable(self):
        # «Документы не поступили» кончается передачей супервайзеру: адреса у
        # неё нет, и предлагать его настройщику значило бы показать настройку,
        # которая ни на что не влияет.
        catalog = {item['key']: item for item in sc.public_catalog()}
        self.assertFalse(catalog['sapar_docs_missing']['sends_to_group'])

    def test_every_other_topic_is_routable(self):
        for item in sc.public_catalog():
            self.assertEqual(item['sends_to_group'], not item['final_outcome'],
                             item['key'])

    def test_at_least_one_topic_can_be_routed(self):
        catalog = sc.public_catalog()
        self.assertTrue([i for i in catalog if i['sends_to_group']])


# ─────────────────────────────────────────────────────────────────────────────
# НАСТОЯЩИЕ ОБРАБОТЧИКИ
#
# Дальше — не проверка правил, а проверка раздела: тот же Blueprint, что стоит
# на проде, поднимается на подменённом SQL-слое и вызывается HTTP-клиентом.
# Ровно здесь живут решения, которых нет ни в SQL, ни в чистых функциях:
# «выбрал родную группу — это возврат к умолчанию», «теме без адреса маршрут не
# назначают», «обращение пишется в очередь МАРШРУТА, а не тематики».
# ─────────────────────────────────────────────────────────────────────────────

def admin_ctx(role='super_admin'):
    return {
        'user_id': 7, 'name': 'Админ', 'role': role,
        'department_id': 1, 'department_code': 'szov',
        'headed_department_ids': [], 'headed_department_codes': [],
        'group_ids': [],
    }


class FakeDb:
    def __init__(self, cursor):
        self.cursor = cursor

    @contextmanager
    def _get_cursor(self):
        yield self.cursor


def build_client(cursor, ctx):
    """Boевой Blueprint на подменённом SQL-слое."""
    app = Flask(__name__)
    app.register_blueprint(crm_routes.build_crm_blueprint(
        db=FakeDb(cursor),
        require_api_key=lambda handler: handler,
        build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (ctx['user_id'], {'id': ctx['user_id']}, None),
        sensitive_access_granted=lambda user_id: True,
    ))
    return app.test_client()


class RouteEndpointTest(unittest.TestCase):
    def setUp(self):
        self.cursor = FakeCursor(ALL_QUEUES)
        self.saved = []
        patches = [
            mock.patch.object(queries, 'load_access_context',
                              lambda cursor, user_id: self.ctx),
            mock.patch.object(queries, 'set_topic_route', self._remember),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.ctx = admin_ctx()

    def _remember(self, cursor, *, scenario_key, queue_id,
                  actor_user_id=None, actor_name=None):
        self.saved.append((scenario_key, queue_id, actor_name))

    def put(self, key, payload):
        return build_client(self.cursor, self.ctx).put(
            '/api/crm/routes/%s' % key, json=payload)

    def test_topic_is_sent_to_the_chosen_queue(self):
        response = self.put('sapar_service_error', {'queue_id': 9})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.saved, [('sapar_service_error', 9, 'Админ')])

    def test_choosing_the_home_group_clears_the_route(self):
        # Иначе в базе осталась бы строка, которая ничего не меняет, но
        # переживёт переименование очереди и однажды начнёт менять.
        response = self.put('sapar_service_error', {'queue_id': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.saved, [('sapar_service_error', None, 'Админ')])

    def test_empty_value_returns_the_topic_home(self):
        self.assertEqual(self.put('parcel_location', {'queue_id': None}).status_code, 200)
        self.assertEqual(self.saved, [('parcel_location', None, 'Админ')])

    def test_topic_that_never_reaches_a_group_takes_no_address(self):
        response = self.put('sapar_docs_missing', {'queue_id': 9})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'CRM_TOPIC_NOT_ROUTABLE')
        self.assertFalse(self.saved)

    def test_disabled_queue_is_refused_at_the_door(self):
        # Маршрут на выключенную очередь закрыл бы тему для оператора, и понять
        # почему было бы негде: тема просто исчезла бы из картотеки.
        response = self.put('parcel_location', {'queue_id': 12})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.saved)

    def test_queue_without_a_chat_is_refused_too(self):
        self.assertEqual(self.put('parcel_location', {'queue_id': 11}).status_code, 400)
        self.assertFalse(self.saved)

    def test_unknown_queue_and_unknown_topic_are_both_404(self):
        self.assertEqual(self.put('parcel_location', {'queue_id': 404}).status_code, 404)
        self.assertEqual(self.put('нет такой темы', {'queue_id': 9}).status_code, 404)
        self.assertFalse(self.saved)

    def test_operator_changes_nothing(self):
        self.ctx = admin_ctx(role='operator')
        response = self.put('parcel_location', {'queue_id': 9})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.saved)


class CatalogAddressTest(unittest.TestCase):
    """Каталог тематик отдаёт ОБА адреса: дом темы и куда она уйдёт."""

    def setUp(self):
        self.ctx = admin_ctx()
        self.cursor = FakeCursor(ALL_QUEUES, [route_row('sapar_service_error', 9)])
        patches = [
            mock.patch.object(queries, 'load_access_context',
                              lambda cursor, user_id: self.ctx),
            mock.patch.object(queries, 'taxi_parks', lambda cursor: []),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def catalog(self):
        response = build_client(self.cursor, self.ctx).get('/api/crm/scenarios')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        return {item['key']: item for item in data['items']}, data['entries']

    def test_routed_topic_keeps_its_home_and_shows_its_address(self):
        catalog, _ = self.catalog()
        item = catalog['sapar_service_error']
        self.assertEqual(item['home_queue_title'], 'iTaxi Sapar')
        self.assertEqual(item['queue_title'], 'Техподдержка')
        self.assertEqual(item['queue_id'], 9)
        self.assertTrue(item['routed'])
        self.assertTrue(item['is_ready'])

    def test_neighbour_topic_is_untouched(self):
        catalog, _ = self.catalog()
        item = catalog['sapar_sign_error']
        self.assertEqual(item['queue_title'], 'iTaxi Sapar')
        self.assertFalse(item['routed'])

    def test_who_moved_the_topic_is_visible_to_the_one_who_configures(self):
        catalog, _ = self.catalog()
        self.assertEqual(catalog['sapar_service_error']['routed_by'], 'Админ')

    def test_operator_is_not_told_who_configured_it(self):
        self.ctx = admin_ctx(role='operator')
        catalog, _ = self.catalog()
        self.assertNotIn('routed_by', catalog['sapar_service_error'])
        # Сам адрес оператору нужен: он выбирает тему, глядя на заголовок
        # раздела, и обязан видеть, что эта уйдёт не туда.
        self.assertEqual(catalog['sapar_service_error']['queue_title'], 'Техподдержка')

    def test_entry_is_open_while_at_least_one_category_has_an_address(self):
        # Вход сам в группу ничего не отправляет: обращение уходит из категории.
        entry = self.catalog()[1][0]
        self.assertTrue(entry['is_ready'])
        self.assertEqual(entry['home_queue_title'], 'iTaxi Sapar')

    def test_entry_closes_when_no_category_can_be_delivered(self):
        # Все категории Sapar уведены в выключенную очередь — отправлять некуда,
        # и вход обязан честно закрыться, а не пустить в интервью.
        self.cursor = FakeCursor(ALL_QUEUES, [route_row(key, 12)
                                              for key in sc.SAPAR_ENTRY['categories']])
        self.assertFalse(self.catalog()[1][0]['is_ready'])


PARCEL_ANSWERS = {
    'iin': '123456789012',
    'contact_number': '+7 777 000 00 00',
    'parcel_description': 'Пакет документов',
    'city': 'Алматы',
    'order_date': '2026-08-20',
}


class TicketGoesToTheRoutedQueueTest(unittest.TestCase):
    """Обращение пишется в очередь МАРШРУТА — ради этого всё и затевалось."""

    def setUp(self):
        self.ctx = admin_ctx()
        self.created = {}
        patches = [
            mock.patch.object(queries, 'load_access_context',
                              lambda cursor, user_id: self.ctx),
            mock.patch.object(queries, 'create_ticket', self._create),
            mock.patch.object(queries, 'add_event', lambda cursor, **kw: None),
            mock.patch.object(queries, 'get_ticket',
                              lambda cursor, ticket_id, viewer_id=None: {'id': ticket_id}),
            mock.patch.object(service, 'deliver_ticket',
                              lambda db, ticket_id, attachment=None: (True, None)),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _create(self, cursor, **kwargs):
        self.created = kwargs
        return 42

    def post(self, routes=()):
        client = build_client(FakeCursor(ALL_QUEUES, routes), self.ctx)
        return client.post('/api/crm/tickets', json={
            'scenario_key': 'parcel_location',
            'answers': PARCEL_ANSWERS,
            'checks_confirmed': True,
        })

    def test_without_a_route_the_ticket_lands_in_its_own_queue(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(self.created['queue_id'], 3)

    def test_route_decides_where_the_ticket_goes(self):
        response = self.post([route_row('parcel_location', 9)])
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(self.created['queue_id'], 9)

    def test_unavailable_route_stops_the_ticket_instead_of_redirecting_it(self):
        # Молча отправить в родную группу — значит показать обращение тем самым
        # людям, от которых тему уводили. Лучше отказ с понятным текстом.
        response = self.post([route_row('parcel_location', 12)])
        self.assertEqual(response.status_code, 400)
        self.assertIn('Выключенная', response.get_json()['error'])
        self.assertFalse(self.created)


if __name__ == '__main__':
    unittest.main()
