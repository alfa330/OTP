# -*- coding: utf-8 -*-
"""Маршруты тем: «эта тема уходит в другой Telegram-чат».

По умолчанию тема (сценарий из crm/scenarios.py) уходит в чат своей тематики —
очереди с кодом queue_code. Маршрут перебивает адрес у ОДНОЙ темы и несёт его
ЧАТОМ, а не очередью: выбирают из групп, где состоит бот.

Проверяется здесь не SQL, а правила, цена ошибки в которых — сообщение не тем
людям:

* маршрут меняет адрес, но НЕ переносит тему в чужую тематику: она остаётся
  там, где её ищет оператор, и обращение по-прежнему числится за ней;
* маршрут на чат, из которого бота выгнали, НЕ подменяется чатом тематики.
  Тему уводили ровно для того, чтобы её там перестали получать, и тихий
  возврат — это отправка не по адресу. Тема просто становится недоступной;
* адрес фиксируется в самом обращении: смена маршрута завтра не должна уводить
  продолжение старого разговора в другую группу.

Ни одно из этих правил не падает и не логируется, если сломается.
"""

import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest import mock

from flask import Flask

from crm import queries, routes as crm_routes, scenarios as sc, service

ROOT = Path(__file__).resolve().parents[1]


# Строка очереди в порядке queries._QUEUE_COLUMNS.
def queue_row(queue_id, code, title, chat_id=-100, is_active=True):
    return (queue_id, title, None, chat_id, 'Чат «%s»' % title, None,
            None, 100, is_active, None, code)


# Строка реестра чатов бота в порядке queries.bot_chats.
def chat_row(chat_id, title, used_by_queue=None):
    return (chat_id, title, 'supergroup', None, used_by_queue)


class FakeCursor:
    """Курсор, который отдаёт заготовленные строки и запоминает запросы."""

    def __init__(self, queues=(), routes=(), chats=()):
        self._queues = list(queues)
        self._routes = list(routes)
        self._chats = list(chats)
        self._result = []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((' '.join(sql.split()), params))
        # Реестр чатов проверяем ПЕРВЫМ: внутри него есть подзапрос к
        # crm_queues, и порядок наоборот отдал бы на него очереди.
        if 'it_ticket_channels' in sql:
            self._result = self._chats
        elif 'FROM crm_topic_routes' in sql:
            self._result = self._routes
        elif 'FROM crm_queues' in sql:
            self._result = self._queues
        else:
            self._result = []

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


SAPAR_CHAT = -5152839588
PARCEL_CHAT = -5546214861
KASPI_CHAT = -5137750718        # рабочий чат, за которым тематики не стоит
GONE_CHAT = -5999999999         # бота из него выгнали: в реестре его нет

SAPAR = queue_row(2, 'itaxi_sapar', 'iTaxi Sapar', chat_id=SAPAR_CHAT)
PARCELS = queue_row(3, 'regions', 'Регионы', chat_id=PARCEL_CHAT)
NO_CHAT = queue_row(11, 'yandex_delivery', 'Яндекс Доставка', chat_id=None)
OFF = queue_row(12, 'off_code', 'Выключенная', chat_id=-777, is_active=False)

ALL_QUEUES = [SAPAR, PARCELS, NO_CHAT, OFF]
ALL_CHATS = [
    chat_row(SAPAR_CHAT, 'Тест ТиТаксиSapar', 'iTaxi Sapar'),
    chat_row(PARCEL_CHAT, 'Тест iCore красный', 'Регионы'),
    chat_row(KASPI_CHAT, 'Sapar/Kaspi - отмена'),
]


def context(routes=()):
    return queries.routing_context(FakeCursor(ALL_QUEUES, routes, ALL_CHATS))


def route_row(key, chat_id, title='Sapar/Kaspi - отмена'):
    return (key, chat_id, title, 'Админ', None)


class DefaultAddressTest(unittest.TestCase):
    def test_topic_without_a_route_goes_to_the_chat_of_its_own_subject(self):
        found = queries.resolve_route(context(), 'sapar_sign_error', 'itaxi_sapar')
        self.assertEqual(found['chat_id'], SAPAR_CHAT)
        self.assertEqual(found['home']['id'], 2)
        self.assertFalse(found['routed'])
        self.assertTrue(found['is_ready'])

    def test_subject_without_a_chat_leaves_the_topic_unready(self):
        found = queries.resolve_route(context(), 'yandex_termobox', 'yandex_delivery')
        self.assertIsNone(found['chat_id'])
        self.assertFalse(found['is_ready'])

    def test_unknown_subject_has_no_address_at_all(self):
        found = queries.resolve_route(context(), 'whatever', 'нет такой очереди')
        self.assertIsNone(found['home'])
        self.assertIsNone(found['chat_id'])
        self.assertFalse(found['is_ready'])

    def test_disabled_subject_closes_its_topics(self):
        found = queries.resolve_route(context(), 'whatever', 'off_code')
        self.assertEqual(found['home']['title'], 'Выключенная')
        self.assertIsNone(found['chat_id'])
        self.assertFalse(found['is_ready'])


class RouteOverridesAddressTest(unittest.TestCase):
    def test_route_changes_the_chat_but_not_the_subject(self):
        found = queries.resolve_route(
            context([route_row('sapar_payment_required', KASPI_CHAT)]),
            'sapar_payment_required', 'itaxi_sapar')
        self.assertEqual(found['chat_id'], KASPI_CHAT)
        self.assertEqual(found['chat_title'], 'Sapar/Kaspi - отмена')
        # Тематика остаётся прежней: по ней тема стоит в картотеке оператора,
        # и за ней же числится обращение.
        self.assertEqual(found['home']['title'], 'iTaxi Sapar')
        self.assertTrue(found['routed'])
        self.assertTrue(found['is_ready'])

    def test_route_touches_only_its_own_topic(self):
        ctx = context([route_row('sapar_payment_required', KASPI_CHAT)])
        neighbour = queries.resolve_route(ctx, 'sapar_sign_error', 'itaxi_sapar')
        self.assertEqual(neighbour['chat_id'], SAPAR_CHAT)
        self.assertFalse(neighbour['routed'])

    def test_route_may_point_at_the_chat_of_another_subject(self):
        # Разные темы в один чат — обычное дело, уникальности по чату нет.
        found = queries.resolve_route(
            context([route_row('sapar_sign_status', PARCEL_CHAT)]),
            'sapar_sign_status', 'itaxi_sapar')
        self.assertEqual(found['chat_id'], PARCEL_CHAT)
        self.assertTrue(found['is_ready'])

    def test_live_chat_title_wins_over_the_snapshot(self):
        # Группу переименовали — показываем новое имя, а не то, что записали
        # при настройке.
        found = queries.resolve_route(
            context([route_row('sapar_sign_status', PARCEL_CHAT, 'старое имя')]),
            'sapar_sign_status', 'itaxi_sapar')
        self.assertEqual(found['chat_title'], 'Тест iCore красный')

    def test_chat_the_bot_left_never_falls_back_to_the_subject_chat(self):
        # Самое дорогое правило файла: подмена адреса чатом тематики отправила
        # бы обращение тем самым людям, от которых тему и уводили.
        found = queries.resolve_route(
            context([route_row('sapar_payment_required', GONE_CHAT, 'Бывшая группа')]),
            'sapar_payment_required', 'itaxi_sapar')
        self.assertEqual(found['chat_id'], GONE_CHAT)
        self.assertNotEqual(found['chat_id'], SAPAR_CHAT)
        self.assertFalse(found['chat_known'])
        self.assertFalse(found['is_ready'])
        # Снимок названия — единственное, чем можно объяснить адрес.
        self.assertEqual(found['chat_title'], 'Бывшая группа')

    def test_route_does_not_revive_a_disabled_subject(self):
        found = queries.resolve_route(
            context([route_row('whatever', KASPI_CHAT)]), 'whatever', 'off_code')
        self.assertFalse(found['is_ready'])


class RoutingContextTest(unittest.TestCase):
    def test_disabled_queues_are_loaded_too(self):
        # Выключенная очередь не адрес, но её название нужно, чтобы объяснить
        # настройщику, что происходит.
        self.assertIn('Выключенная', {q['title'] for q in context()['queues']})

    def test_whole_layout_costs_three_queries(self):
        cursor = FakeCursor(ALL_QUEUES, [route_row('parcel_location', KASPI_CHAT)], ALL_CHATS)
        queries.routing_context(cursor)
        self.assertEqual(len(cursor.executed), 3)

    def test_route_carries_who_and_when(self):
        found = context([route_row('parcel_location', KASPI_CHAT)])['routes']['parcel_location']
        self.assertEqual(found['chat_id'], KASPI_CHAT)
        self.assertEqual(found['updated_by_name'], 'Админ')


class SetRouteTest(unittest.TestCase):
    def test_setting_a_route_is_an_upsert(self):
        # Второй выбор той же темы обязан переписать строку, а не упасть на
        # первичном ключе: настройщик тыкает в список сколько захочет.
        cursor = FakeCursor()
        queries.set_topic_route(cursor, scenario_key='parcel_location', chat_id=KASPI_CHAT,
                                chat_title='Sapar/Kaspi - отмена',
                                actor_user_id=1, actor_name='Админ')
        sql, params = cursor.executed[-1]
        self.assertIn('INSERT INTO crm_topic_routes', sql)
        self.assertIn('ON CONFLICT (scenario_key) DO UPDATE', sql)
        self.assertEqual(params[:3], ('parcel_location', KASPI_CHAT, 'Sapar/Kaspi - отмена'))

    def test_returning_to_the_subject_chat_deletes_the_row(self):
        # Не «маршрут на тот же чат»: такая строка ничего не меняет, но
        # переживёт смену чата у тематики и однажды начнёт менять.
        cursor = FakeCursor()
        queries.set_topic_route(cursor, scenario_key='parcel_location', chat_id=None)
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

    def test_address_is_a_chat_and_it_is_mandatory(self):
        self.assertRegex(self.source, r'chat_id\s+BIGINT NOT NULL')

    def test_no_foreign_key_to_the_chat_registry(self):
        # Реестр наполняет my_chat_member, и строка оттуда может уехать. FK
        # уронил бы запись маршрута или увёл бы адрес вместе с чатом.
        table = self.source[self.source.index('CREATE TABLE IF NOT EXISTS crm_topic_routes'):]
        table = table[:table.index('"""')]
        self.assertNotIn('it_ticket_channels', table)
        self.assertNotIn('crm_queues', table)

    def test_the_queue_column_of_the_first_version_is_migrated_away(self):
        # Первая версия адресовала очередь. Перенос обязан быть в миграциях, а
        # не «заведём заново»: на стенде строки могли появиться.
        self.assertIn('ALTER TABLE crm_topic_routes ADD COLUMN IF NOT EXISTS chat_id BIGINT',
                      self.source)
        self.assertIn('DROP COLUMN queue_id', self.source)

    def test_ticket_remembers_the_chat_it_went_to(self):
        self.assertIn(
            'ALTER TABLE crm_tickets ADD COLUMN IF NOT EXISTS tg_chat_title VARCHAR(255)',
            self.source)

    def test_key_column_holds_the_longest_scenario_key(self):
        self.assertLessEqual(max(len(item['key']) for item in sc.SCENARIOS), 64)


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
            self.assertEqual(item['sends_to_group'], not item['final_outcome'], item['key'])


# ─────────────────────────────────────────────────────────────────────────────
# НАСТОЯЩИЕ ОБРАБОТЧИКИ
#
# Дальше — не проверка правил, а проверка раздела: тот же Blueprint, что стоит
# на проде, поднимается на подменённом SQL-слое и вызывается HTTP-клиентом.
# Ровно здесь живут решения, которых нет ни в SQL, ни в чистых функциях:
# «выбрал чат тематики — это возврат к умолчанию», «в чужой чат маршрут не
# заводят», «обращение уходит в чат ТЕМЫ, а числится за тематикой».
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
    """Боевой Blueprint на подменённом SQL-слое."""
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
        self.cursor = FakeCursor(ALL_QUEUES, (), ALL_CHATS)
        self.saved = []
        self.ctx = admin_ctx()
        patches = [
            mock.patch.object(queries, 'load_access_context',
                              lambda cursor, user_id: self.ctx),
            mock.patch.object(queries, 'set_topic_route', self._remember),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _remember(self, cursor, *, scenario_key, chat_id, chat_title=None,
                  actor_user_id=None, actor_name=None):
        self.saved.append((scenario_key, chat_id, chat_title))
        # Запись отражается в курсоре: ответ ручки собирается ПОСЛЕ неё, и
        # подмена, которая ничего не меняет, показала бы старый адрес.
        self.cursor._routes = ([] if chat_id is None
                               else [route_row(scenario_key, chat_id, chat_title)])

    def put(self, key, payload):
        return build_client(self.cursor, self.ctx).put(
            '/api/crm/routes/%s' % key, json=payload)

    def test_topic_is_sent_to_the_chosen_chat(self):
        response = self.put('sapar_payment_required', {'chat_id': KASPI_CHAT})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.saved,
                         [('sapar_payment_required', KASPI_CHAT, 'Sapar/Kaspi - отмена')])

    def test_answer_carries_the_new_address_back(self):
        item = self.put('sapar_payment_required',
                        {'chat_id': KASPI_CHAT}).get_json()['item']
        self.assertEqual(item['chat_title'], 'Sapar/Kaspi - отмена')
        self.assertEqual(item['home_queue_title'], 'iTaxi Sapar')
        self.assertTrue(item['routed'])

    def test_choosing_the_subject_chat_clears_the_route(self):
        # Иначе в базе осталась бы строка, которая ничего не меняет, но
        # переживёт смену чата у тематики и однажды начнёт менять.
        response = self.put('sapar_payment_required', {'chat_id': SAPAR_CHAT})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.saved, [('sapar_payment_required', None, None)])

    def test_empty_value_returns_the_topic_home(self):
        self.assertEqual(self.put('parcel_location', {'chat_id': None}).status_code, 200)
        self.assertEqual(self.saved, [('parcel_location', None, None)])

    def test_chat_the_bot_is_not_in_is_refused_at_the_door(self):
        # В чужой чат бот всё равно не напишет: лучше внятный отказ сейчас, чем
        # обращение с ошибкой доставки потом.
        response = self.put('parcel_location', {'chat_id': GONE_CHAT})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'CRM_CHAT_UNKNOWN')
        self.assertFalse(self.saved)

    def test_topic_that_never_reaches_a_group_takes_no_address(self):
        response = self.put('sapar_docs_missing', {'chat_id': KASPI_CHAT})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'CRM_TOPIC_NOT_ROUTABLE')
        self.assertFalse(self.saved)

    def test_unknown_topic_is_404(self):
        self.assertEqual(self.put('нет такой темы', {'chat_id': KASPI_CHAT}).status_code, 404)
        self.assertFalse(self.saved)

    def test_operator_changes_nothing(self):
        self.ctx = admin_ctx(role='operator')
        self.assertEqual(self.put('parcel_location', {'chat_id': KASPI_CHAT}).status_code, 403)
        self.assertFalse(self.saved)


class CatalogAddressTest(unittest.TestCase):
    """Каталог тематик отдаёт и тематику темы, и её настоящий чат."""

    def setUp(self):
        self.ctx = admin_ctx()
        self.cursor = FakeCursor(ALL_QUEUES,
                                 [route_row('sapar_payment_required', KASPI_CHAT)],
                                 ALL_CHATS)
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

    def test_routed_topic_keeps_its_subject_and_shows_its_chat(self):
        item = self.catalog()[0]['sapar_payment_required']
        self.assertEqual(item['home_queue_title'], 'iTaxi Sapar')
        self.assertEqual(item['chat_title'], 'Sapar/Kaspi - отмена')
        self.assertEqual(item['chat_id'], KASPI_CHAT)
        self.assertTrue(item['routed'])
        self.assertTrue(item['is_ready'])

    def test_neighbour_topic_is_untouched(self):
        item = self.catalog()[0]['sapar_sign_error']
        self.assertEqual(item['chat_title'], 'Тест ТиТаксиSapar')
        self.assertFalse(item['routed'])

    def test_operator_gets_the_name_but_not_the_chat_id(self):
        self.ctx = admin_ctx(role='operator')
        item = self.catalog()[0]['sapar_payment_required']
        # Служебный номер чужого чата рядовому сотруднику ни к чему — то же
        # правило, что у очередей.
        self.assertNotIn('chat_id', item)
        self.assertNotIn('routed_by', item)
        # А название нужно: он выбирает тему, глядя на заголовок раздела, и
        # обязан видеть, что эта уйдёт не туда.
        self.assertEqual(item['chat_title'], 'Sapar/Kaspi - отмена')

    def test_who_moved_the_topic_is_visible_to_the_one_who_configures(self):
        self.assertEqual(self.catalog()[0]['sapar_payment_required']['routed_by'], 'Админ')

    def test_entry_is_open_while_at_least_one_category_has_an_address(self):
        # Вход сам в группу ничего не отправляет: обращение уходит из категории.
        entry = self.catalog()[1][0]
        self.assertTrue(entry['is_ready'])
        self.assertEqual(entry['home_queue_title'], 'iTaxi Sapar')

    def test_entry_closes_when_no_category_can_be_delivered(self):
        self.cursor = FakeCursor(ALL_QUEUES,
                                 [route_row(key, GONE_CHAT)
                                  for key in sc.SAPAR_ENTRY['categories']],
                                 ALL_CHATS)
        self.assertFalse(self.catalog()[1][0]['is_ready'])


# Пять полей §2.3 ТЗ #201 — то, чем тематика «Уточнение посылки» отправляется.
PARCEL_ANSWERS = {
    'driver_name': 'Иванов Иван Иванович',
    'driver_licence': '123456789',
    'contact_number': '+7 777 000 00 00',
    'delivery_date': '2026-08-20',
    'parcel_description': 'Пакет документов',
}


class TicketGoesToTheRoutedChatTest(unittest.TestCase):
    """Обращение уходит в чат ТЕМЫ, а числится за её тематикой."""

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
        client = build_client(FakeCursor(ALL_QUEUES, routes, ALL_CHATS), self.ctx)
        return client.post('/api/crm/tickets', json={
            'scenario_key': 'parcel_location',
            'answers': PARCEL_ANSWERS,
            'checks_confirmed': True,
        })

    def test_without_a_route_the_ticket_goes_to_the_subject_chat(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(self.created['queue_id'], 3)
        self.assertEqual(self.created['tg_chat_id'], PARCEL_CHAT)

    def test_route_decides_the_chat_but_the_subject_stays(self):
        response = self.post([route_row('parcel_location', KASPI_CHAT)])
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(self.created['tg_chat_id'], KASPI_CHAT)
        self.assertEqual(self.created['tg_chat_title'], 'Sapar/Kaspi - отмена')
        # Тематика прежняя: по ней строится отчёт и по ней фильтруют список.
        self.assertEqual(self.created['queue_id'], 3)

    def test_unavailable_chat_stops_the_ticket_instead_of_redirecting_it(self):
        # Молча отправить в чат тематики — значит показать обращение тем самым
        # людям, от которых тему уводили. Лучше отказ с понятным текстом.
        response = self.post([route_row('parcel_location', GONE_CHAT, 'Бывшая группа')])
        self.assertEqual(response.status_code, 400)
        self.assertIn('Бывшая группа', response.get_json()['error'])
        self.assertFalse(self.created)


# Офисы города, как их отдаёт справочник вики. Проверка §3.2 ТЗ #201 решает по
# ним, спрашивать регион или нет, — значит и подделать их не должно быть можно.
OPEN_OFFICE = {'id': 7, 'name': 'Офис Астана', 'address': 'проспект Сарыарка, 31',
               'state': 'open', 'label': 'Открыт', 'note': None, 'closed_until': None}
CLOSED_OFFICE = dict(OPEN_OFFICE, state='closed', label='Закрыт',
                     note='ремонт', closed_until='2026-08-31')


class OfficeSnapshotIsTheServersTest(unittest.TestCase):
    """Статус офиса сервер перечитывает сам — как и данные Sapar.

    Снимок участвует в решении «отправлять или нет», а приезжает он в тех же
    ответах, что и всё остальное. Поверь мы присланному — достаточно было бы
    дописать в запрос «офис открыт», чтобы пройти проверку, которой не было.
    """

    def setUp(self):
        self.ctx = admin_ctx()
        self.created = {}
        self.offices = [OPEN_OFFICE]
        patches = [
            mock.patch.object(queries, 'load_access_context',
                              lambda cursor, user_id: self.ctx),
            mock.patch.object(queries, 'create_ticket', self._create),
            mock.patch.object(queries, 'add_event', lambda cursor, **kw: None),
            mock.patch.object(queries, 'get_ticket',
                              lambda cursor, ticket_id, viewer_id=None: {'id': ticket_id}),
            mock.patch.object(queries, 'today', lambda cursor: date(2026, 8, 27)),
            mock.patch.object(queries, 'city_offices',
                              lambda cursor, city, day: list(self.offices)),
            mock.patch.object(service, 'deliver_ticket',
                              lambda db, ticket_id, attachment=None: (True, None)),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _create(self, cursor, **kwargs):
        self.created = kwargs
        return 77

    def post(self, answers):
        client = build_client(FakeCursor(ALL_QUEUES, (), ALL_CHATS), self.ctx)
        return client.post('/api/crm/tickets', json={
            'scenario_key': 'office_status',
            'answers': answers,
            'checks_confirmed': True,
        })

    def test_open_office_is_sent_and_keeps_the_servers_snapshot(self):
        response = self.post({'office_city': 'Астана', 'office': '7'})
        self.assertEqual(response.status_code, 201, response.get_json())
        snapshot = self.created['answers'][sc.OFFICES_ANSWER_KEY]
        self.assertEqual(snapshot['offices'], [OPEN_OFFICE])
        self.assertEqual(snapshot['day'], '2026-08-27')

    def test_forged_snapshot_does_not_open_a_closed_office(self):
        self.offices = [CLOSED_OFFICE]
        response = self.post({
            'office_city': 'Астана', 'office': '7',
            # Клиент утверждает, что офис открыт. Справочник говорит обратное.
            sc.OFFICES_ANSWER_KEY: {'available': True, 'city': 'Астана',
                                           'offices': [OPEN_OFFICE]},
        })
        self.assertEqual(response.status_code, 409)
        self.assertIn('Закрыт', response.get_json()['error'])
        self.assertFalse(self.created)

    def test_lookup_answers_with_the_offices_and_the_verdict(self):
        client = build_client(FakeCursor(ALL_QUEUES, (), ALL_CHATS), self.ctx)
        response = client.post('/api/crm/scenarios/office_status/lookup',
                               json={'answers': {'office_city': 'Астана'}})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['snapshot']['offices'], [OPEN_OFFICE])
        # Офис ещё не выбран, но открытый в городе есть — оператор идёт дальше.
        self.assertEqual(data['verdict']['outcome'], sc.PASS)

    def test_lookup_closes_the_ticket_when_the_whole_city_is_closed(self):
        self.offices = [CLOSED_OFFICE]
        client = build_client(FakeCursor(ALL_QUEUES, (), ALL_CHATS), self.ctx)
        response = client.post('/api/crm/scenarios/office_status/lookup',
                               json={'answers': {'office_city': 'Астана'}})
        self.assertEqual(response.get_json()['verdict']['outcome'], sc.CLOSE)

    def test_topic_without_a_lookup_is_skipped(self):
        client = build_client(FakeCursor(ALL_QUEUES, (), ALL_CHATS), self.ctx)
        response = client.post('/api/crm/scenarios/sapar_sign_status/lookup',
                               json={'answers': {}})
        self.assertTrue(response.get_json()['skipped'])


if __name__ == '__main__':
    unittest.main()
