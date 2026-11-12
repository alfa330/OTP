# -*- coding: utf-8 -*-
"""«Карточка клиента» на настоящем Postgres: CLIENT_CARDS_TEST_PORT — порт локального кластера.

Правила и права без базы проверяет tests/test_client_cards.py. Здесь — настоящие схема и
запросы и сценарии сервиса поверх них: карточка звонка, привязка аккаунта к номеру, причины,
запросы в группу с ответами, алерты, отчёт. TEZ APP и Telegram подставные.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным именем,
в конце она удаляется. Люди и номера выдуманы.

    CLIENT_CARDS_TEST_PORT=55481 python -m pytest tests/test_client_cards_postgres.py
"""

import contextlib
import os
import uuid
from datetime import datetime, timedelta

import pytest

from client_cards import access, queries, report, routes, rules, schema, service as service_module, transport
from client_cards.service import CardError, ClientCardsService
from client_cards.tez import SOURCE_ACCOUNTS, SOURCE_FIRST_ORDERS

PORT = os.environ.get('CLIENT_CARDS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (CLIENT_CARDS_TEST_PORT)')

TEZ, OP = 560, 367
SUPER, HEAD, SV, ANNA, BORIS, SALES, OUTSIDER = 1, 300, 3, 30, 31, 40, 50
GROUP_TP, GROUP_OP = 34, 35
CHAT_FINANCE, CHAT_ALERTS = -1001, -1002
PHONE = '77012345678'
OTHER_PHONE = '77075550011'

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text UNIQUE, name text NOT NULL,
                              head_user_id integer, is_active boolean DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL,
                        status text DEFAULT 'working', department_id integer, telegram_id bigint);
    CREATE TABLE groups (id integer PRIMARY KEY, name text NOT NULL, department_id integer,
                         status text NOT NULL DEFAULT 'active');
    CREATE TABLE group_operator_memberships (id serial PRIMARY KEY, group_id integer NOT NULL,
                                             operator_id integer NOT NULL, start_date date NOT NULL,
                                             end_date date);
    CREATE TABLE it_ticket_channels (id serial PRIMARY KEY, chat_id bigint NOT NULL UNIQUE,
                                     title text, chat_type text, is_active boolean NOT NULL DEFAULT TRUE);
    INSERT INTO departments (id, code, name, head_user_id) VALUES
        (560, 'tez', 'Тез КЦ', 300), (367, 'op', 'Отдел продаж', NULL);
    INSERT INTO users (id, name, role, status, department_id, telegram_id) VALUES
        (1, 'Супер Админов', 'super_admin', 'working', NULL, NULL),
        (300, 'Глава Теза', 'admin', 'working', 560, 700300),
        (3, 'Старший Смены', 'sv', 'working', 560, NULL),
        (30, 'Анна Оператор', 'operator', 'working', 560, NULL),
        (31, 'Борис Оператор', 'operator', 'working', 560, NULL),
        (40, 'Продавец Теза', 'operator', 'working', 560, NULL),
        (50, 'Чужой Оператор', 'operator', 'working', 367, NULL);
    INSERT INTO groups (id, name, department_id) VALUES
        (34, 'Тез КЦ - Тех поддержка', 560), (35, 'Tez КЦ - Отдел продаж', 560);
    INSERT INTO group_operator_memberships (group_id, operator_id, start_date) VALUES
        (34, 30, DATE '2026-01-01'), (34, 31, DATE '2026-01-01'), (35, 40, DATE '2026-01-01');
    INSERT INTO it_ticket_channels (chat_id, title, chat_type) VALUES
        (-1001, 'Тез · Финансы', 'supergroup'), (-1002, 'Тез · Руководители', 'supergroup');
'''


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT),
                                user=os.environ.get('CLIENT_CARDS_TEST_USER', 'postgres'),
                                dbname='postgres', connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.name)
            cur.execute("SET statement_timeout TO '20s'")
        conn.commit()
        return conn

    @contextlib.contextmanager
    def _get_cursor(self):
        conn = self.connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


@pytest.fixture()
def db():
    import psycopg2
    name = 'client_cards_' + uuid.uuid4().hex[:12]
    admin = psycopg2.connect(host='127.0.0.1', port=int(PORT),
                             user=os.environ.get('CLIENT_CARDS_TEST_USER', 'postgres'),
                             dbname='postgres', connect_timeout=3)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + name)
    store = _Db(name)
    try:
        with store._get_cursor() as cursor:
            cursor.execute(BASE_DDL)
            schema.init_client_cards_schema(cursor)
            # Повторный разворот ничего не ломает и не плодит строк.
            schema.init_client_cards_schema(cursor)
        yield store
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA %s CASCADE' % name)
        admin.close()


class FakeTez:
    """Подставной TEZ APP: что отдать по номеру и по ID — задаёт тест."""

    def __init__(self, source=SOURCE_FIRST_ORDERS):
        self._source = source
        self.by_phone_reply = {}
        self.by_id_reply = {}
        self.calls = []
        self.forgotten = []

    def source(self):
        return self._source

    def admin_url(self):
        return rules.DEFAULT_ADMIN_URL

    def forget(self, phone=None):
        self.forgotten.append(phone)

    def by_phone(self, phone):
        self.calls.append(('phone', phone))
        reply = self.by_phone_reply.get(phone)
        if reply is not None:
            return reply
        return {'source': self._source, 'ok': True, 'error': None, 'accounts': [], 'driver_hint': None}

    def by_id(self, kind, tez_id):
        self.calls.append(('id', kind, tez_id))
        if self._source != SOURCE_ACCOUNTS:
            return {'source': self._source, 'ok': False, 'error': None, 'account': None}
        return {'source': self._source, 'ok': True, 'error': None,
                'account': self.by_id_reply.get((kind, tez_id))}


class Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def tick(self, **kwargs):
        self.now += timedelta(**kwargs)
        return self.now


class Outbox:
    """Подставной Telegram: запоминает отправленное и раздаёт номера сообщений."""

    def __init__(self):
        self.messages = []
        self.files = []
        self.fail_chats = set()
        self.migrate = {}
        self._next = 1000

    def _id(self):
        self._next += 1
        return self._next

    def send_message(self, chat_id, text, *, reply_to_message_id=None):
        if chat_id in self.migrate:
            return None, transport.TelegramError('group chat was upgraded', self.migrate[chat_id])
        if chat_id in self.fail_chats:
            return None, transport.TelegramError('bot was kicked')
        message_id = self._id()
        self.messages.append({'chat_id': chat_id, 'text': text, 'reply_to': reply_to_message_id,
                              'message_id': message_id})
        return {'message_id': message_id}, None

    def send_file(self, chat_id, *, name, data, mime=None, kind=None, reply_to_message_id=None, caption=None):
        if chat_id in self.fail_chats:
            return None, transport.TelegramError('bot was kicked')
        message_id = self._id()
        self.files.append({'chat_id': chat_id, 'name': name, 'size': len(data), 'kind': kind,
                           'reply_to': reply_to_message_id, 'message_id': message_id})
        return {'message_id': message_id, 'document': {'file_id': 'tg-file-%d' % message_id}}, None


class FakeStorage:
    """Подставное хранилище файлов: gs://-ссылки на байты в памяти."""

    def __init__(self):
        self.blobs = {}

    def gcs(self):
        return {'bucket_name': lambda: 'test-bucket', 'client': lambda: self}

    def bucket(self, name):
        return _Bucket(self, name)


class _Bucket:
    def __init__(self, storage, name):
        self.storage, self.name = storage, name

    def blob(self, path):
        return _Blob(self.storage, self.name, path)


class _Blob:
    def __init__(self, storage, bucket, path):
        self.storage, self.key = storage, (bucket, path)

    def upload_from_string(self, data, content_type=None):
        self.storage.blobs[self.key] = bytes(data)

    def exists(self):
        return self.key in self.storage.blobs

    def download_as_bytes(self):
        return self.storage.blobs[self.key]

    def delete(self):
        self.storage.blobs.pop(self.key, None)

    def generate_signed_url(self, **_kwargs):
        return 'https://storage.example/%s/%s?sig=1' % self.key


@pytest.fixture()
def world(db, monkeypatch):
    """Сервис с подставными TEZ APP, Telegram и хранилищем; раздел у Тез КЦ включён группе ТП."""
    outbox, storage = Outbox(), FakeStorage()
    monkeypatch.setattr(transport, 'send_message', outbox.send_message)
    monkeypatch.setattr(transport, 'send_file', outbox.send_file)
    clock = Clock(datetime(2026, 11, 12, 10, 0, 0))
    tez = FakeTez()
    migrated = []
    svc = ClientCardsService(cursor_factory=db._get_cursor, tez=tez, gcs=storage.gcs(), now=clock,
                             spawn=lambda work: work(),
                             migrate_chat=lambda old, new: migrated.append((old, new)))
    with db._get_cursor() as cursor:
        queries.save_settings(cursor, TEZ, {'enabled': True, 'group_ids': [GROUP_TP], 'wrapup_sec': 60},
                              by=HEAD, by_name='Глава Теза', now=clock())
        orders = queries.create_category(cursor, TEZ, title='Заказы', parent_id=None, by=HEAD, now=clock())
        no_orders = queries.create_category(cursor, TEZ, title='Не приходят заказы', parent_id=orders,
                                            by=HEAD, now=clock())
        cancel = queries.create_category(cursor, TEZ, title='Отмена заказа', parent_id=orders, by=HEAD,
                                         now=clock())
        money = queries.create_category(cursor, TEZ, title='Выплаты', parent_id=None, by=HEAD, now=clock())
        finance = queries.create_destination(
            cursor, TEZ, {'title': 'Финансы', 'chat_id': CHAT_FINANCE, 'chat_title': 'Тез · Финансы',
                          'mention_usernames': '@fin_lead'}, by=HEAD, now=clock())
        no_chat = queries.create_destination(cursor, TEZ, {'title': 'Модерация'}, by=HEAD, now=clock())

    class World:
        pass

    w = World()
    w.db, w.svc, w.tez, w.clock, w.outbox, w.storage, w.migrated = db, svc, tez, clock, outbox, storage, migrated
    w.orders, w.no_orders, w.cancel, w.money = orders, no_orders, cancel, money
    w.finance, w.no_chat = finance, no_chat
    w.ctx = lambda user_id: svc.context(user_id)
    return w


def _driver(tez_id=12345, **extra):
    account = {'kind': rules.KIND_DRIVER, 'tez_id': tez_id, 'full_name': 'Иванов Иван', 'phone': PHONE,
               'city': 'Алматы', 'status': 'active', 'registered_at': datetime(2026, 11, 5, 14, 3),
               'last_order_at': datetime(2026, 11, 10, 18, 41), 'details': {}}
    account.update(extra)
    return account


def _fill(w, ctx, card_id, *, link='https://admin.tezapp.org/admin/drivers/12345', topic=None, comment=None):
    """Заполнить карточку до «можно сохранять»: ссылка на аккаунт и причина."""
    w.svc.attach_link(ctx, card_id, link)
    data = {'topics': [{'category_id': w.orders, 'subcategory_id': topic or w.no_orders}]}
    if comment is not None:
        data['comment'] = comment
    return w.svc.save_card(ctx, card_id, data)


# ─── Схема и права ───────────────────────────────────────────────────────────

def test_schema_seeds_tez_disabled(db):
    with db._get_cursor() as cursor:
        assert schema.schema_is_ready(cursor)
        settings = queries.get_settings(cursor, TEZ)
        assert settings['enabled'] is False and settings['wrapup_sec'] == rules.WRAPUP_DEFAULT_SEC
        assert settings['alerts'] == rules.normalize_alerts(None)
        assert queries.get_settings(cursor, OP) is None


def test_context_and_access_circle(world):
    w = world
    anna, sales, head, sv, sup, outsider = (w.ctx(x) for x in (ANNA, SALES, HEAD, SV, SUPER, OUTSIDER))
    # Карточка — операторам группы техподдержки; продавцу того же отдела телефон её не открывает.
    assert access.phone_card_enabled(anna) and not access.phone_card_enabled(sales)
    assert access.can_open_section(anna) and not access.can_open_section(sales)
    assert not access.can_open_section(outsider)
    # Глава отдела руководит разделом и карточку проверяет на себе, не числясь в группе.
    assert access.managed_department_ids(head) == [TEZ] and access.phone_card_enabled(head)
    assert access.managed_department_ids(sv) == [TEZ]
    # Админ, возглавляющий отдел, — руководитель отдела, а не администратор компании.
    assert not access.is_global_admin(head) and access.is_global_admin(sup)
    assert access.managed_department_ids(sup) == [TEZ] and not access.phone_card_enabled(sup)
    assert w.svc.phone_settings(ANNA) == {'enabled': True, 'wrapup_sec': 60}
    assert w.svc.phone_settings(SALES) == {'enabled': False}
    assert w.svc.phone_settings(OUTSIDER) == {'enabled': False}


def test_disabled_department_keeps_section_for_head_only(world):
    w = world
    with w.db._get_cursor() as cursor:
        queries.save_settings(cursor, TEZ, {'enabled': False}, by=HEAD, by_name='Глава', now=w.clock())
    assert not access.can_open_section(w.ctx(ANNA))
    assert access.can_open_section(w.ctx(HEAD)) and not access.phone_card_enabled(w.ctx(HEAD))
    with pytest.raises(CardError) as error:
        w.svc.open_call(w.ctx(ANNA), phone=PHONE, direction='in', call_key='c1')
    assert error.value.code == 'CARD_DISABLED' and error.value.status == 403


# ─── Карточка звонка ─────────────────────────────────────────────────────────

def test_call_opens_one_card_per_call(world):
    w = world
    ctx = w.ctx(ANNA)
    first = w.svc.open_call(ctx, phone='8 701 234-56-78', direction='in', call_key='call-1')
    again = w.svc.open_call(ctx, phone='+77012345678', direction='in', call_key='call-1')
    assert first['card']['id'] == again['card']['id']
    assert first['card']['phone'] == PHONE and first['card']['wrapup_sec'] == 60
    assert first['accounts'] == [] and first['account'] is None
    other = w.svc.open_call(ctx, phone=PHONE, direction='out', call_key='call-2')
    assert other['card']['id'] != first['card']['id'] and other['card']['direction'] == 'out'
    # Тот же идентификатор звонка у другого оператора — это другой звонок.
    boris = w.svc.open_call(w.ctx(BORIS), phone=PHONE, direction='in', call_key='call-1')
    assert boris['card']['id'] not in (first['card']['id'], other['card']['id'])
    with pytest.raises(CardError) as error:
        w.svc.open_call(ctx, phone='105', direction='in', call_key='call-3')
    assert error.value.code == 'CARD_BAD_PHONE'


def test_unanswered_call_voids_empty_card_but_keeps_a_noted_one(world):
    w = world
    ctx = w.ctx(ANNA)
    missed = w.svc.open_call(ctx, phone=PHONE, direction='in', call_key='m1')['card']['id']
    w.svc.attach_link(ctx, missed, 'admin.tezapp.org/admin/drivers/12345')
    bundle = w.svc.call_event(ctx, missed, 'ended')
    # Аккаунт выбрали ещё на гудках — записью оператора это не считается.
    assert bundle['card']['status'] == rules.STATUS_VOID
    with pytest.raises(CardError) as error:
        w.svc.save_card(ctx, missed, {'comment': 'поздно'})
    assert error.value.code == 'CARD_VOID'

    noted = w.svc.open_call(ctx, phone=PHONE, direction='out', call_key='m2')['card']['id']
    w.svc.save_card(ctx, noted, {'comment': 'не дозвонился, перезвонить после 18:00'})
    bundle = w.svc.call_event(ctx, noted, 'ended')
    assert bundle['card']['status'] == rules.STATUS_OPEN

    talked = w.svc.open_call(ctx, phone=PHONE, direction='in', call_key='m3')['card']['id']
    w.svc.call_event(ctx, talked, 'answered')
    bundle = w.svc.call_event(ctx, talked, 'ended')
    assert bundle['card']['status'] == rules.STATUS_OPEN and bundle['card']['ended_at'] == w.clock()
    # Чужой телефон события звонка не шлёт.
    with pytest.raises(CardError):
        w.svc.call_event(w.ctx(BORIS), talked, 'ended')


def test_link_binds_account_to_phone_and_next_call_finds_it(world):
    w = world
    ctx = w.ctx(ANNA)
    card_id = w.svc.open_call(ctx, phone=PHONE, direction='in', call_key='l1')['card']['id']
    with pytest.raises(CardError) as error:
        w.svc.attach_link(ctx, card_id, 'https://example.com/admin/drivers/1')
    assert error.value.code == 'CARD_BAD_LINK'
    bundle = w.svc.attach_link(ctx, card_id, 'https://admin.tezapp.org/admin/clients/67890?tab=orders')
    account = bundle['account']
    assert (account['kind'], account['tez_id']) == (rules.KIND_CLIENT, 67890)
    assert account['fetched_at'] is None and account['link']['origin'] == 'manual'
    assert bundle['card']['account_kind'] == rules.KIND_CLIENT
    assert w.tez.forgotten == [PHONE]

    # Тот же человек звонит снова с того же номера — и с другого, который привязывает другой оператор.
    again = w.svc.open_call(ctx, phone=PHONE, direction='in', call_key='l2')
    assert [(a['kind'], a['tez_id']) for a in again['accounts']] == [(rules.KIND_CLIENT, 67890)]
    assert again['card']['account_id'] is None
    other = w.svc.open_call(w.ctx(BORIS), phone=OTHER_PHONE, direction='in', call_key='l3')
    assert other['accounts'] == []
    linked = w.svc.attach_link(w.ctx(BORIS), other['card']['id'], 'admin.tezapp.org/admin/clients/67890')
    assert linked['account']['id'] == account['id']
    with w.db._get_cursor() as cursor:
        assert queries.account_phones(cursor, account['id']) == [PHONE, OTHER_PHONE]

    # Выбор из привязанных; аккаунт чужого номера выбрать нельзя — только ссылкой.
    chosen = w.svc.choose_account(ctx, again['card']['id'], account['id'])
    assert chosen['card']['account_id'] == account['id']
    stranger = w.svc.open_call(ctx, phone='77770000000', direction='in', call_key='l4')
    with pytest.raises(CardError) as error:
        w.svc.choose_account(ctx, stranger['card']['id'], account['id'])
    assert error.value.code == 'CARD_ACCOUNT_NOT_LINKED'
    cleared = w.svc.choose_account(ctx, again['card']['id'], None)
    assert cleared['card']['account_id'] is None


def test_unlink_is_for_author_and_manager(world):
    w = world
    anna, boris, head = w.ctx(ANNA), w.ctx(BORIS), w.ctx(HEAD)
    card_id = w.svc.open_call(anna, phone=PHONE, direction='in', call_key='u1')['card']['id']
    bundle = w.svc.attach_link(anna, card_id, 'admin.tezapp.org/admin/drivers/5')
    link_id = bundle['account']['link']['id']
    boris_card = w.svc.open_call(boris, phone=PHONE, direction='in', call_key='u2')['card']['id']
    with pytest.raises(CardError) as error:
        w.svc.unlink(boris, boris_card, link_id)
    assert error.value.status == 403
    after = w.svc.unlink(head, card_id, link_id)
    assert after['accounts'] == [] and after['card']['account_id'] is None


def test_tez_lookup_links_found_accounts_and_fills_city(world):
    w = world
    w.tez._source = SOURCE_ACCOUNTS
    ctx = w.ctx(ANNA)
    card_id = w.svc.open_call(ctx, phone=PHONE, direction='in', call_key='t1')['card']['id']
    w.tez.by_phone_reply[PHONE] = {
        'source': SOURCE_ACCOUNTS, 'ok': True, 'error': None, 'driver_hint': None,
        'accounts': [_driver(), {'kind': rules.KIND_CLIENT, 'tez_id': 67890, 'full_name': 'Иван',
                                 'phone': PHONE, 'city': 'Алматы', 'status': 'active',
                                 'registered_at': None, 'last_order_at': None, 'details': {}}]}
    bundle = w.svc.lookup(ctx, card_id)
    assert bundle['tez'] == {'source': SOURCE_ACCOUNTS, 'ok': True, 'error': None, 'driver_hint': None}
    assert sorted((a['kind'], a['tez_id'], a['link']['origin']) for a in bundle['accounts']) == [
        (rules.KIND_CLIENT, 67890, 'tez'), (rules.KIND_DRIVER, 12345, 'tez')]
    driver = next(a for a in bundle['accounts'] if a['kind'] == rules.KIND_DRIVER)
    assert driver['full_name'] == 'Иванов Иван' and driver['fetched_at'] == w.clock()
    chosen = w.svc.choose_account(ctx, card_id, driver['id'])
    assert chosen['card']['city'] == 'Алматы'
    # Найденную Тезом привязку руками не снимают — она вернётся со следующим звонком.
    with pytest.raises(CardError):
        w.svc.unlink(w.ctx(HEAD), card_id, driver['link']['id'])

    # Ссылка на аккаунт, которого в Тез нет, не закрепляется.
    with pytest.raises(CardError) as error:
        w.svc.attach_link(ctx, card_id, 'admin.tezapp.org/admin/drivers/999')
    assert error.value.code == 'CARD_ACCOUNT_NOT_FOUND'
    # А существующий приходит сразу с профилем.
    w.tez.by_id_reply[(rules.KIND_DRIVER, 80000)] = _driver(80000, full_name='Петров Пётр', city='Астана',
                                                             phone=OTHER_PHONE)
    linked = w.svc.attach_link(ctx, card_id, 'admin.tezapp.org/admin/drivers/80000')
    assert linked['account']['full_name'] == 'Петров Пётр' and linked['card']['city'] == 'Астана'


def test_tez_silence_keeps_card_working(world):
    w = world
    ctx = w.ctx(ANNA)
    card_id = w.svc.open_call(ctx, phone=PHONE, direction='in', call_key='s1')['card']['id']
    w.svc.attach_link(ctx, card_id, 'admin.tezapp.org/admin/drivers/12345')
    w.tez.by_phone_reply[PHONE] = {'source': SOURCE_FIRST_ORDERS, 'ok': False, 'error': 'TEZ APP не отвечает',
                                   'accounts': [], 'driver_hint': None}
    bundle = w.svc.lookup(ctx, card_id)
    assert bundle['tez']['ok'] is False and len(bundle['accounts']) == 1
    w.tez.by_phone_reply[PHONE] = {
        'source': SOURCE_FIRST_ORDERS, 'ok': True, 'error': None, 'accounts': [],
        'driver_hint': {'exists': True, 'registered_at': datetime(2026, 11, 5), 'last_order_at': None}}
    assert w.svc.lookup(ctx, card_id)['tez']['driver_hint']['exists'] is True


def test_topics_comment_and_completion(world):
    w = world
    ctx = w.ctx(ANNA)
    card_id = w.svc.open_call(ctx, phone=PHONE, direction='in', call_key='k1')['card']['id']
    w.svc.call_event(ctx, card_id, 'answered')
    with pytest.raises(CardError) as error:
        w.svc.complete_card(ctx, card_id)
    assert 'аккаунт' in error.value.message
    w.svc.attach_link(ctx, card_id, 'admin.tezapp.org/admin/drivers/12345')
    with pytest.raises(CardError) as error:
        w.svc.complete_card(ctx, card_id)
    assert 'причину' in error.value.message
    # Категорию с подтемами саму выбрать нельзя; чужую подтему — тоже.
    for bad in ([{'category_id': w.orders}], [{'category_id': w.money, 'subcategory_id': w.no_orders}],
                [{'category_id': 999999}]):
        with pytest.raises(CardError) as error:
            w.svc.save_card(ctx, card_id, {'topics': bad})
        assert error.value.code == 'CARD_BAD_TOPICS'
    bundle = w.svc.save_card(ctx, card_id, {
        'topics': [{'category_id': w.orders, 'subcategory_id': w.no_orders}, {'category_id': w.money},
                   {'category_id': w.money}],
        'comment': '  второй день тишина  ', 'city': 'Шымкент'})
    assert [(t['title'], t['sub_title']) for t in bundle['card']['topics']] == [
        ('Заказы', 'Не приходят заказы'), ('Выплаты', None)]
    assert bundle['card']['comment'] == 'второй день тишина' and bundle['card']['city'] == 'Шымкент'
    # Город со слов оператора запомнен на аккаунте: в следующий раз подставится сам.
    assert bundle['account']['city'] == 'Шымкент'

    w.svc.call_event(ctx, card_id, 'ended')
    w.clock.tick(seconds=40)
    done = w.svc.complete_card(ctx, card_id)
    assert done['card']['status'] == rules.STATUS_DONE and done['card']['completed_at'] == w.clock()
    public = routes.public_card(done['card'], w.clock())
    assert public['state'] == rules.STATE_DONE and public['late'] is False
    # Заполненную карточку правят, но без причины она остаться не может.
    with pytest.raises(CardError):
        w.svc.save_card(ctx, card_id, {'topics': []})
    # Чужую карточку оператор видит (история водителя), но не правит; руководитель — правит.
    assert w.svc.bundle(w.ctx(BORIS), card_id)['card']['id'] == card_id
    with pytest.raises(CardError):
        w.svc.save_card(w.ctx(BORIS), card_id, {'comment': 'чужое'})
    assert w.svc.save_card(w.ctx(HEAD), card_id, {'comment': 'проверено'})['card']['comment'] == 'проверено'
    with pytest.raises(CardError):
        w.svc.bundle(w.ctx(OUTSIDER), card_id)


def test_wrapup_state_and_pending_list(world):
    w = world
    ctx = w.ctx(ANNA)
    card_id = w.svc.open_call(ctx, phone=PHONE, direction='in', call_key='w1')['card']['id']
    card = w.svc.bundle(ctx, card_id)['card']
    assert rules.card_state(card, w.clock()) == rules.STATE_RINGING
    w.svc.call_event(ctx, card_id, 'answered')
    w.clock.tick(minutes=3)
    card = w.svc.call_event(ctx, card_id, 'ended')['card']
    public = routes.public_card(card, w.clock())
    assert public['state'] == rules.STATE_WRAPUP and public['wrapup_left_sec'] == 60
    w.clock.tick(seconds=61)
    assert routes.public_card(card, w.clock())['state'] == rules.STATE_OVERDUE
    cards, _names = w.svc.pending(ctx)
    assert [item['id'] for item in cards] == [card_id]
    _fill(w, ctx, card_id)
    done = w.svc.complete_card(ctx, card_id)
    assert routes.public_card(done['card'], w.clock())['late'] is True
    assert w.svc.pending(ctx)[0] == []


def test_history_follows_account_across_phones(world):
    w = world
    anna, boris = w.ctx(ANNA), w.ctx(BORIS)
    first = w.svc.open_call(anna, phone=PHONE, direction='in', call_key='h1')['card']['id']
    w.svc.call_event(anna, first, 'answered')
    _fill(w, anna, first, comment='не приходят заказы в аэропорту')
    w.svc.complete_card(anna, first)
    w.clock.tick(hours=2)
    second = w.svc.open_call(boris, phone=OTHER_PHONE, direction='in', call_key='h2')['card']['id']
    w.svc.call_event(boris, second, 'answered')
    # До выбора аккаунта у нового номера истории нет.
    cards, _more, _names, _requests = w.svc.history(boris, card_id=second)
    assert cards == []
    w.svc.attach_link(boris, second, 'admin.tezapp.org/admin/drivers/12345')
    cards, has_more, names, _requests = w.svc.history(boris, card_id=second)
    assert [card['id'] for card in cards] == [first] and has_more is False
    assert names[cards[0]['account_id']]['tez_id'] == 12345
    # Поиск: по слову комментария, по причине, по ID аккаунта; мимо — пусто.
    assert [c['id'] for c in w.svc.history(boris, card_id=second, search='аэропорт')[0]] == [first]
    assert [c['id'] for c in w.svc.history(boris, card_id=second, search='не приходят')[0]] == [first]
    assert w.svc.history(boris, card_id=second, search='выплаты')[0] == []
    assert [c['id'] for c in w.svc.history(boris, scope='all', search='12345')[0]] == [second, first]
    # «Продолжение обращения»: прошлое обращение того же отдела, не само себя.
    assert w.svc.save_card(boris, second, {'parent_id': first})['card']['parent_id'] == first
    with pytest.raises(CardError):
        w.svc.save_card(boris, second, {'parent_id': second})
    detail = w.svc.card_detail(boris, second)
    assert detail['parent']['id'] == first and detail['can_edit'] is True
    assert w.svc.card_detail(anna, second)['can_edit'] is False


# ─── Запросы в другие отделы ─────────────────────────────────────────────────

def _request(w, ctx, card_id, **kwargs):
    kwargs.setdefault('destination_id', w.finance)
    kwargs.setdefault('body', 'Водителю не пришла выплата за 10.11, проверьте, пожалуйста')
    return w.svc.create_request(ctx, card_id=card_id, **kwargs)


def _ready_card(w, ctx, key='r1'):
    card_id = w.svc.open_call(ctx, phone=PHONE, direction='in', call_key=key)['card']['id']
    w.svc.call_event(ctx, card_id, 'answered')
    _fill(w, ctx, card_id)
    return card_id


def test_request_goes_to_group_with_files_and_answers_come_back(world):
    w = world
    anna = w.ctx(ANNA)
    card_id = _ready_card(w, anna)
    detail, error = _request(w, anna, card_id, uploads=[
        {'name': 'Скрин.png', 'data': b'png-bytes', 'mime': 'image/png'},
        {'name': 'экран.mp4', 'data': b'video-bytes' * 10, 'mime': 'video/mp4'}])
    assert error is None
    request = detail['request']
    assert request['delivery_status'] == 'sent' and request['status'] == rules.REQUEST_OPEN
    assert request['topic_title'] == 'Заказы → Не приходят заказы'
    root = w.outbox.messages[0]
    assert root['chat_id'] == CHAT_FINANCE and request['tg_message_id'] == root['message_id']
    for part in ('Заказы → Не приходят заказы', 'Запрос №%d' % request['id'], 'Финансы', 'ID 12345',
                 '/admin/drivers/12345', '+7 701 234-56-78', 'не пришла выплата', 'Анна Оператор', '@fin_lead'):
        assert part in root['text'], part
    assert [(f['name'], f['kind'], f['reply_to']) for f in w.outbox.files] == [
        ('Скрин.png', 'photo', root['message_id']), ('экран.mp4', 'video', root['message_id'])]
    assert len(w.storage.blobs) == 2
    assert [m['direction'] for m in detail['messages']] == ['out', 'out', 'out']
    assert detail['messages'][0]['body'].startswith('Водителю не пришла') and all(
        m['tg_message_id'] for m in detail['messages'])

    # Ответ сотрудника финансов реплаем на файл — тоже в эту переписку; повтор апдейта не двоит.
    reply = {'message_id': 5001, 'text': 'Выплата ушла 11.11, проверьте карту',
             'from': {'id': 77, 'first_name': 'Айгуль', 'username': 'aigul_fin'}}
    assert w.svc.ingest_group_reply(chat_id=CHAT_FINANCE, reply_to_message_id=w.outbox.files[0]['message_id'],
                                    message=reply) == {'request_id': request['id']}
    w.svc.ingest_group_reply(chat_id=CHAT_FINANCE, reply_to_message_id=root['message_id'], message=reply)
    assert w.svc.ingest_group_reply(chat_id=CHAT_FINANCE, reply_to_message_id=424242, message=reply) is None
    assert w.svc.ingest_group_reply(chat_id=CHAT_ALERTS, reply_to_message_id=root['message_id'],
                                    message=reply) is None
    with w.db._get_cursor() as cursor:
        assert queries.unread_total(cursor, ANNA) == 1
        total, rows = queries.unread_for_bell(cursor, ANNA, 5)
        assert total == 1 and rows[0][0] == request['id']
    # Руководитель открыл запрос — «непрочитано» автора не гаснет; автор открыл — гаснет.
    seen_by_head = w.svc.request_detail(w.ctx(HEAD), request['id'])
    assert seen_by_head['request']['author_unread_count'] == 1
    mine = w.svc.request_detail(anna, request['id'])
    assert mine['request']['status'] == rules.REQUEST_ANSWERED and mine['request']['author_unread_count'] == 0
    assert [m['direction'] for m in mine['messages']] == ['out', 'out', 'out', 'in']
    assert mine['messages'][-1]['author_name'] == 'Айгуль'

    # Оператор уточняет реплаем на ответ отдела и закрывает запрос — группе уходит отбивка.
    answer_row = mine['messages'][-1]
    detail, error = w.svc.reply(anna, request['id'], body='Спасибо, передам водителю', reply_to=answer_row['id'])
    assert error is None and w.outbox.messages[-1]['reply_to'] == 5001
    closed = w.svc.set_request_status(anna, request['id'], 'resolved')
    assert closed['request']['status'] == rules.REQUEST_RESOLVED
    assert 'решён' in w.outbox.messages[-1]['text'] and w.outbox.messages[-1]['reply_to'] == root['message_id']
    with pytest.raises(CardError) as error:
        w.svc.reply(anna, request['id'], body='ещё вопрос')
    assert error.value.code == 'REQUEST_CLOSED'
    reopened = w.svc.set_request_status(anna, request['id'], 'reopen')
    assert reopened['request']['status'] == rules.REQUEST_ANSWERED

    # Вложение: наш файл — ссылкой на хранилище.
    file_row = next(m for m in mine['messages'] if m['file_name'] == 'экран.mp4')
    assert w.svc.request_file(anna, request['id'], file_row['id'])['url'].startswith('https://storage.example/')


def test_request_visibility_and_validation(world):
    w = world
    anna, boris, head = w.ctx(ANNA), w.ctx(BORIS), w.ctx(HEAD)
    card_id = _ready_card(w, anna)
    for kwargs, code in (({'body': ' '}, 'REQUEST_EMPTY'), ({'destination_id': 999999}, 'REQUEST_BAD_DESTINATION'),
                         ({'destination_id': w.no_chat}, 'REQUEST_NO_CHAT'),
                         ({'uploads': [{'name': 'f%d.png' % i, 'data': b'x', 'mime': 'image/png'}
                                       for i in range(rules.REQUEST_FILES_LIMIT + 1)]}, 'REQUEST_TOO_MANY_FILES')):
        with pytest.raises(CardError) as error:
            _request(w, anna, card_id, **kwargs)
        assert error.value.code == code
    assert w.outbox.messages == [] and w.storage.blobs == {}
    with pytest.raises(CardError):
        _request(w, boris, card_id)  # чужая карточка
    detail, _error = _request(w, anna, card_id)
    request_id = detail['request']['id']
    with pytest.raises(CardError) as error:
        w.svc.request_detail(boris, request_id)
    assert error.value.status == 403
    assert w.svc.request_detail(head, request_id)['can_act'] is True

    boris_card = _ready_card(w, boris, key='r2')
    _request(w, boris, boris_card, body='Второй запрос от Бориса')
    rows, _more, _names = w.svc.list_requests(anna)
    assert [row['id'] for row in rows] == [request_id]
    rows, _more, _names = w.svc.list_requests(head)
    assert len(rows) == 2
    assert [row['id'] for row in w.svc.list_requests(head, search='Борис')[0]] == [rows[0]['id']]
    assert [row['id'] for row in w.svc.list_requests(head, search=str(request_id))[0]] == [request_id]
    assert w.svc.list_requests(head, status='resolved')[0] == []
    assert w.svc.list_requests(w.ctx(OUTSIDER))[0] == []


def test_failed_delivery_is_kept_and_resent(world):
    w = world
    anna = w.ctx(ANNA)
    card_id = _ready_card(w, anna)
    w.outbox.fail_chats.add(CHAT_FINANCE)
    detail, error = _request(w, anna, card_id, uploads=[{'name': 'a.pdf', 'data': b'pdf', 'mime': 'application/pdf'}])
    assert 'kicked' in error and detail['request']['delivery_status'] == 'failed'
    with pytest.raises(CardError) as failure:
        w.svc.reply(anna, detail['request']['id'], body='уточнение')
    assert failure.value.code == 'REQUEST_NOT_DELIVERED'
    w.outbox.fail_chats.clear()
    detail, error = w.svc.retry_delivery(anna, detail['request']['id'])
    assert error is None and detail['request']['delivery_status'] == 'sent'
    assert len(w.outbox.messages) == 1 and len(w.outbox.files) == 1
    # Повтор уже доставленного ничего не шлёт второй раз.
    w.svc.retry_delivery(anna, detail['request']['id'])
    assert len(w.outbox.messages) == 1 and len(w.outbox.files) == 1


def test_group_migration_moves_destination(world):
    w = world
    anna = w.ctx(ANNA)
    card_id = _ready_card(w, anna)
    new_chat = -100777
    w.outbox.migrate[CHAT_FINANCE] = new_chat
    detail, error = _request(w, anna, card_id)
    assert error is None and w.outbox.messages[0]['chat_id'] == new_chat
    assert w.migrated == [(CHAT_FINANCE, new_chat)]
    with w.db._get_cursor() as cursor:
        assert queries.get_destination(cursor, w.finance)['chat_id'] == new_chat
        assert queries.get_request(cursor, detail['request']['id'])['tg_chat_id'] == new_chat


# ─── Алерты ──────────────────────────────────────────────────────────────────

def _calls(w, operator_id, count, *, topic, start_key, city='Алматы', same_person=False):
    """count заполненных карточек по причине; люди разные, если не сказано иное."""
    ctx = w.ctx(operator_id)
    for index in range(count):
        number = PHONE if same_person else '7701%07d' % (start_key + index)
        tez_id = 12345 if same_person else 100000 + start_key + index
        card_id = w.svc.open_call(ctx, phone=number, direction='in',
                                  call_key='a-%d-%d' % (start_key, index))['card']['id']
        w.svc.call_event(ctx, card_id, 'answered')
        w.svc.attach_link(ctx, card_id, 'admin.tezapp.org/admin/drivers/%d' % tez_id)
        w.svc.save_card(ctx, card_id, {'topics': [{'category_id': w.orders, 'subcategory_id': topic}],
                                       'city': city})
        w.svc.call_event(ctx, card_id, 'ended')
        w.svc.complete_card(ctx, card_id)
        w.clock.tick(minutes=2)


def _alerts(w):
    with w.db._get_cursor() as cursor:
        return queries.alerts_log(cursor, TEZ)


def test_alert_fires_once_and_respects_cooldown(world):
    w = world
    with w.db._get_cursor() as cursor:
        queries.save_settings(cursor, TEZ, {'alert_chat_id': CHAT_ALERTS, 'alert_chat_title': 'Руководители'},
                              by=HEAD, by_name='Глава', now=w.clock())
    _calls(w, ANNA, 4, topic=w.no_orders, start_key=0)
    assert _alerts(w) == [] and w.outbox.messages == []
    # Пятый разный человек за час — порог: одно сообщение главе лично и в группу.
    _calls(w, BORIS, 1, topic=w.no_orders, start_key=10, city='Астана')
    log = _alerts(w)
    assert [(a['title'], a['parent_title'], a['kind'], a['count'], a['delivered']) for a in log] == [
        ('Не приходят заказы', 'Заказы', rules.ALERT_NEW, 5, True)]
    assert sorted(m['chat_id'] for m in w.outbox.messages) == [CHAT_ALERTS, 700300]
    text = w.outbox.messages[0]['text']
    for part in ('Рост обращений', 'Тез КЦ', 'Заказы → Не приходят заказы', '5 человек', 'за последний час',
                 'Алматы — 4', 'Астана — 1'):
        assert part in text, part
    # Шестой и седьмой звонок — та же волна: тишина. Категорию «Заказы» отдельно тоже не шлём.
    _calls(w, ANNA, 2, topic=w.no_orders, start_key=20)
    assert len(_alerts(w)) == 1 and len(w.outbox.messages) == 2
    # Стало в полтора раза больше и прошло полчаса — «становится больше», одним сообщением;
    # девятый человек через две минуты — снова тишина.
    w.clock.tick(minutes=25)
    _calls(w, ANNA, 2, topic=w.no_orders, start_key=30)
    log = _alerts(w)
    assert [(a['kind'], a['count']) for a in log] == [(rules.ALERT_GROWING, 8), (rules.ALERT_NEW, 5)]
    assert len(w.outbox.messages) == 4
    assert 'становится больше' in w.outbox.messages[-1]['text']


def test_one_person_calling_many_times_is_not_a_wave(world):
    w = world
    _calls(w, ANNA, 7, topic=w.no_orders, start_key=0, same_person=True)
    assert _alerts(w) == [] and w.outbox.messages == []


def test_usual_volume_is_not_an_alert(world):
    w = world
    # Две недели подряд в этот же час по причине обращается по шесть человек — это норма.
    start = w.clock()
    for day in range(1, 15):
        w.clock.now = start - timedelta(days=day, minutes=40)
        with w.db._get_cursor() as cursor:
            queries.save_settings(cursor, TEZ, {'alerts': dict(rules.ALERT_DEFAULTS, enabled=False)},
                                  by=HEAD, by_name='Глава', now=w.clock())
        _calls(w, ANNA, 6, topic=w.cancel, start_key=day * 100)
    w.clock.now = start
    with w.db._get_cursor() as cursor:
        queries.save_settings(cursor, TEZ, {'alerts': rules.normalize_alerts(None)}, by=HEAD, by_name='Глава',
                              now=w.clock())
        leaves, roots = queries.alert_baseline(cursor, TEZ, until=start + timedelta(minutes=20), window_min=60,
                                               days=rules.ALERT_BASELINE_DAYS)
    assert leaves[(w.orders, w.cancel)] == pytest.approx(6.0) and roots[w.orders] == pytest.approx(6.0)
    # Сегодня — восемь: больше пяти, но не вдвое больше обычного.
    _calls(w, ANNA, 8, topic=w.cancel, start_key=5000)
    assert _alerts(w) == []
    # Причина, по которой обычно не звонят: пятый человек за час — уже алерт.
    w.clock.now = start + timedelta(hours=3)
    _calls(w, BORIS, 5, topic=w.no_orders, start_key=7000)
    assert [(a['title'], a['count']) for a in _alerts(w)] == [('Не приходят заказы', 5)]


def test_alert_without_recipients_is_logged_not_lost(world):
    w = world
    with w.db._get_cursor() as cursor:
        queries.save_settings(cursor, TEZ, {'alert_head': False}, by=HEAD, by_name='Глава', now=w.clock())
    _calls(w, ANNA, 5, topic=w.no_orders, start_key=0)
    log = _alerts(w)
    assert len(log) == 1 and log[0]['delivered'] is False and w.outbox.messages == []
    with pytest.raises(CardError) as error:
        w.svc.send_test_alert(w.ctx(HEAD), TEZ)
    assert error.value.code == 'ALERT_NO_RECIPIENTS'
    with pytest.raises(CardError):
        w.svc.send_test_alert(w.ctx(ANNA), TEZ)


# ─── Справочники, журнал, отчёт ──────────────────────────────────────────────

def test_categories_tree_and_protection(world):
    w = world
    ctx = w.ctx(ANNA)
    card_id = w.svc.open_call(ctx, phone=PHONE, direction='in', call_key='c1')['card']['id']
    w.svc.call_event(ctx, card_id, 'answered')
    _fill(w, ctx, card_id)
    with w.db._get_cursor() as cursor:
        tree = queries.categories(cursor, TEZ)
        assert [(node['title'], [child['title'] for child in node['children']]) for node in tree] == [
            ('Заказы', ['Не приходят заказы', 'Отмена заказа']), ('Выплаты', [])]
        assert tree[0]['children'][0]['used'] == 1 and tree[0]['used'] == 1
        assert queries.category_in_use(cursor, w.orders) and queries.category_in_use(cursor, w.no_orders)
        assert not queries.category_in_use(cursor, w.cancel) and not queries.category_in_use(cursor, w.money)
        queries.reorder_categories(cursor, TEZ, None, [w.money, w.orders], now=w.clock())
        queries.reorder_categories(cursor, TEZ, w.orders, [w.cancel, w.no_orders], now=w.clock())
        tree = queries.categories(cursor, TEZ)
        assert [node['title'] for node in tree] == ['Выплаты', 'Заказы']
        assert [child['title'] for child in tree[1]['children']] == ['Отмена заказа', 'Не приходят заказы']
        # Скрытую причину оператору не предлагают, но в старой карточке она остаётся.
        queries.update_category(cursor, w.no_orders, {'is_active': False}, now=w.clock())
        assert [child['title'] for child in queries.categories(cursor, TEZ)[1]['children']] == ['Отмена заказа']
        assert len(queries.categories(cursor, TEZ, include_inactive=True)[1]['children']) == 2
        assert queries.get_card(cursor, card_id)['topics'][0]['sub_title'] == 'Не приходят заказы'
        queries.delete_category(cursor, w.cancel)
        assert queries.get_category(cursor, w.cancel) is None
    with pytest.raises(CardError) as error:
        w.svc.save_card(ctx, card_id, {'topics': [{'category_id': w.orders, 'subcategory_id': w.no_orders}]})
    assert error.value.code == 'CARD_BAD_TOPICS'
    # Все подтемы категории скрыты — её выбирают саму.
    assert w.svc.save_card(ctx, card_id, {'topics': [{'category_id': w.orders}]})['card']['topics'][0]['sub_title'] is None


def test_journal_and_report(world):
    w = world
    anna, boris = w.ctx(ANNA), w.ctx(BORIS)
    _calls(w, ANNA, 3, topic=w.no_orders, start_key=0, city='Алматы')
    _calls(w, BORIS, 2, topic=w.cancel, start_key=10, city='Астана')
    # Карточка с двумя причинами, клиент, без города; одна незаполненная; один несостоявшийся звонок.
    both = w.svc.open_call(anna, phone='77019990001', direction='out', call_key='j1')['card']['id']
    w.svc.call_event(anna, both, 'answered')
    w.svc.attach_link(anna, both, 'admin.tezapp.org/admin/clients/900')
    w.svc.save_card(anna, both, {'topics': [{'category_id': w.orders, 'subcategory_id': w.cancel},
                                            {'category_id': w.money}]})
    w.svc.call_event(anna, both, 'ended')
    w.clock.tick(seconds=90)
    w.svc.complete_card(anna, both)
    pending = w.svc.open_call(boris, phone='77019990002', direction='in', call_key='j2')['card']['id']
    w.svc.call_event(boris, pending, 'answered')
    w.svc.call_event(boris, pending, 'ended')
    missed = w.svc.open_call(boris, phone='77019990003', direction='in', call_key='j3')['card']['id']
    w.svc.call_event(boris, missed, 'ended')
    _request(w, anna, both)

    day_start = datetime(2026, 11, 12)
    with w.db._get_cursor() as cursor:
        cards, has_more = queries.journal(cursor, TEZ, date_from=day_start, date_to=day_start + timedelta(days=1))
        assert len(cards) == 7 and not has_more and missed not in [card['id'] for card in cards]
        assert [c['id'] for c in queries.journal(cursor, TEZ, state='pending')[0]] == [pending]
        assert len(queries.journal(cursor, TEZ, operator_id=BORIS)[0]) == 3
        assert len(queries.journal(cursor, TEZ, category_id=w.cancel)[0]) == 3
        assert len(queries.journal(cursor, TEZ, category_id=w.orders)[0]) == 6
        assert [c['id'] for c in queries.journal(cursor, TEZ, search='9990001')[0]] == [both]
        page, more = queries.journal(cursor, TEZ, limit=3)
        assert len(page) == 3 and more is True
        assert len(queries.journal(cursor, TEZ, before_id=page[-1]['id'], limit=10)[0]) == 4
        data = queries.report(cursor, TEZ, date_from=day_start, date_to=day_start + timedelta(days=1))
        assert queries.report(cursor, TEZ, date_from=day_start - timedelta(days=5),
                              date_to=day_start)['totals']['calls'] == 0

    totals = data['totals']
    assert (totals['calls'], totals['done'], totals['pending']) == (7, 6, 1)
    assert (totals['incoming'], totals['outgoing'], totals['drivers'], totals['clients']) == (6, 1, 5, 1)
    assert totals['people'] == 7 and totals['late'] == 1
    by_title = {node['title']: node for node in data['categories']}
    # «Заказы» — шесть карточек: карточка с двумя причинами считается один раз.
    assert by_title['Заказы']['count'] == 6 and by_title['Выплаты']['count'] == 1
    assert {sub['title']: sub['count'] for sub in by_title['Заказы']['subtopics']} == {
        'Не приходят заказы': 3, 'Отмена заказа': 3}
    assert data['categories'][0]['title'] == 'Заказы'
    assert {city['city']: city['count'] for city in data['cities']} == {'Алматы': 3, 'Астана': 2, '': 1}
    assert data['days'] == [{'date': day_start.date(), 'calls': 7, 'done': 6}]
    operators = {item['name']: item for item in data['operators']}
    assert (operators['Анна Оператор']['calls'], operators['Анна Оператор']['late']) == (4, 1)
    assert (operators['Борис Оператор']['done'], operators['Борис Оператор']['pending']) == (2, 1)
    assert data['requests'] == {'total': 1, 'active': 1, 'avg_first_reply_sec': None}

    # Книга: доли подписаны формулой, сумма городов равна числу заполненных карточек.
    from openpyxl import load_workbook
    stream = report.build_workbook(data, department_name='Тез КЦ', date_from=day_start.date(),
                                   date_to=day_start.date(), generated_by='Глава Теза', generated_at=w.clock())
    book = load_workbook(stream)
    assert book.sheetnames == [report.SHEET_SUMMARY, report.SHEET_REASONS, report.SHEET_CITIES,
                               report.SHEET_DAYS, report.SHEET_OPERATORS, report.SHEET_NOTES]
    summary = {row[0]: row for row in book[report.SHEET_SUMMARY].iter_rows(min_row=5, values_only=True)}
    assert summary['А'][2] == 7 and summary['Б'][2] == 6 and summary['В'][2] == 1
    assert summary['Г'][1] == 'Доля заполненных = Б ÷ А' and summary['Г'][2] == pytest.approx(6 / 7)
    cities = [row for row in book[report.SHEET_CITIES].iter_rows(min_row=2, values_only=True) if row[1] is not None]
    assert sum(row[1] for row in cities) == 6 and report.CITY_UNKNOWN in [row[0] for row in cities]
    reasons = list(book[report.SHEET_REASONS].iter_rows(min_row=2, values_only=True))
    assert reasons[0][:3] == ('Заказы', 'вся категория', 6)
    assert report.report_filename('Тез КЦ', day_start.date(), day_start.date()) == \
        'Карточка клиента Тез КЦ 12.11.2026–12.11.2026.xlsx'


def test_no_account_card(world):
    """«Аккаунта нет» (владелец, 12.11.2026): звонивший не зарегистрирован в Тез — карточка
    сохраняется без аккаунта, причина всё равно нужна, в отчёте такие обращения отдельно."""
    w = world
    anna = w.ctx(ANNA)
    card_id = w.svc.open_call(anna, phone='77019990011', direction='in', call_key='na1')['card']['id']
    w.svc.call_event(anna, card_id, 'answered')
    with pytest.raises(CardError) as failed:
        w.svc.complete_card(anna, card_id)
    assert 'Аккаунта нет' in str(failed.value)

    bundle = w.svc.mark_no_account(anna, card_id)
    assert bundle['card']['account_id'] is None and bundle['card']['account_kind'] == rules.KIND_NONE
    assert bundle['account'] is None and rules.has_no_account(bundle['card'])
    # Отметка аккаунт не заменяет причину: без неё карточка по-прежнему не сохраняется.
    with pytest.raises(CardError) as failed:
        w.svc.complete_card(anna, card_id)
    assert str(failed.value) == 'Выберите причину обращения'
    # Города у обращения без аккаунта нет — и указать его нельзя.
    with pytest.raises(CardError):
        w.svc.save_card(anna, card_id, {'city': 'Алматы'})

    # Аккаунт всё-таки нашёлся: выбор аккаунта снимает отметку, отметка — снимает аккаунт.
    linked = w.svc.attach_link(anna, card_id, 'admin.tezapp.org/admin/drivers/777')
    assert linked['card']['account_kind'] == rules.KIND_DRIVER and not rules.has_no_account(linked['card'])
    account_id = linked['card']['account_id']
    cleared = w.svc.mark_no_account(anna, card_id)
    assert cleared['card']['account_id'] is None and rules.has_no_account(cleared['card'])
    # Привязка номера остаётся: отметка говорит о звонке, а не о номере.
    assert [item['id'] for item in cleared['accounts']] == [account_id]
    again = w.svc.choose_account(anna, card_id, account_id)
    assert again['card']['account_id'] == account_id and not rules.has_no_account(again['card'])
    w.svc.choose_account(anna, card_id, None)
    w.svc.mark_no_account(anna, card_id)

    w.svc.save_card(anna, card_id, {'topics': [{'category_id': w.money}], 'comment': 'спрашивал про регистрацию'})
    w.svc.call_event(anna, card_id, 'ended')
    done = w.svc.complete_card(anna, card_id)
    assert done['card']['status'] == rules.STATUS_DONE and rules.has_no_account(done['card'])
    # У заполненной карточки нельзя снять и аккаунт, и отметку: она выпала бы из отчёта.
    with pytest.raises(CardError) as failed:
        w.svc.choose_account(anna, card_id, None)
    assert failed.value.code == 'CARD_INCOMPLETE'

    # Отчёт: обращение без аккаунта — отдельным числом, в города не попадает.
    _calls(w, ANNA, 2, topic=w.no_orders, start_key=40, city='Алматы')
    day_start = datetime(2026, 11, 12)
    with w.db._get_cursor() as cursor:
        data = queries.report(cursor, TEZ, date_from=day_start, date_to=day_start + timedelta(days=1))
        journal = queries.journal(cursor, TEZ, search='9990011')[0]
    totals = data['totals']
    assert (totals['done'], totals['drivers'], totals['clients'], totals['no_account']) == (3, 2, 0, 1)
    assert totals['drivers'] + totals['clients'] + totals['no_account'] == totals['done']
    assert {city['city']: city['count'] for city in data['cities']} == {'Алматы': 2}
    assert sum(city['count'] for city in data['cities']) + totals['no_account'] == totals['done']
    # Человек без аккаунта считается по номеру.
    assert totals['people'] == 3
    assert [card['account_kind'] for card in journal] == [rules.KIND_NONE]

    from openpyxl import load_workbook
    book = load_workbook(report.build_workbook(data, department_name='Тез КЦ', date_from=day_start.date(),
                                               date_to=day_start.date(), generated_at=w.clock()))
    summary = {row[0]: row for row in book[report.SHEET_SUMMARY].iter_rows(min_row=5, values_only=True)}
    assert summary['И'][1] == 'Обращений без аккаунта (из Б)' and summary['И'][2] == 1
    assert summary['Ж'][2] + summary['З'][2] + summary['И'][2] == summary['Б'][2]
    cities = [row for row in book[report.SHEET_CITIES].iter_rows(min_row=2, values_only=True) if row[1] is not None]
    assert [(row[0], row[1]) for row in cities] == [('Алматы', 2), (rules.NO_ACCOUNT_LABEL, 1)]
    assert sum(row[1] for row in cities) == summary['Б'][2]


def test_service_module_exports():
    assert service_module.HISTORY_PAGE > 0 and service_module.PENDING_DAYS >= 1


# ─── Ручки API ───────────────────────────────────────────────────────────────

@pytest.fixture()
def api(world, monkeypatch):
    """Blueprint раздела на Flask: кто вошёл и закрыт ли он замком QR — задаёт тест."""
    from flask import Flask

    w = world
    state = {'user': ANNA, 'locked': set()}
    # Ручки берут «сейчас» из rules — ведём его теми же часами, что и сервис.
    monkeypatch.setattr(rules, 'now_almaty', w.clock)
    app = Flask(__name__)
    app.register_blueprint(routes.build_client_cards_blueprint(
        db=w.db, require_api_key=lambda handler: handler,
        build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (state['user'], None, None) if state['user'] else (None, None, ('Unauthorized', 401)),
        service=w.svc, qr_locked=lambda user_id: user_id in state['locked']))
    client = app.test_client()

    class Api:
        pass

    a = Api()
    a.w, a.state = w, state

    def call(method, path, user=ANNA, **kwargs):
        state['user'] = user
        response = getattr(client, method)('/api/client_cards' + path, **kwargs)
        return response.status_code, (response.get_json() if response.is_json else response)

    a.get = lambda path, user=ANNA, **kw: call('get', path, user, **kw)
    a.post = lambda path, user=ANNA, **kw: call('post', path, user, **kw)
    a.patch = lambda path, user=ANNA, **kw: call('patch', path, user, **kw)
    a.put = lambda path, user=ANNA, **kw: call('put', path, user, **kw)
    a.delete = lambda path, user=ANNA, **kw: call('delete', path, user, **kw)
    return a


def test_api_gates(api):
    assert api.get('/phone/state', user=None)[0] == 401
    status, body = api.get('/phone/state', user=OUTSIDER)
    assert (status, body['code']) == (403, 'CLIENT_CARDS_CLOSED')
    status, body = api.get('/phone/state', user=SALES)
    assert (status, body['code']) == (403, 'CLIENT_CARDS_CLOSED')
    # Замок QR: тем же кодом, по которому страница телефона показывает QR.
    api.state['locked'].add(ANNA)
    for method, path in (('get', '/phone/state'), ('get', '/requests'), ('get', '/history')):
        status, body = getattr(api, method)(path)
        assert (status, body['code']) == (403, 'SENSITIVE_ACCESS_REQUIRED'), path
    status, body = api.post('/phone/calls', json={'phone': PHONE, 'direction': 'in', 'call_key': 'g1'})
    assert (status, body['code']) == (403, 'SENSITIVE_ACCESS_REQUIRED')
    api.state['locked'].clear()
    assert api.get('/phone/state')[0] == 200
    assert api.w.outbox.messages == []


def test_api_phone_card_flow(api):
    w = api.w
    status, state = api.get('/phone/state')
    assert status == 200 and state['enabled'] is True and state['wrapup_sec'] == 60
    assert [node['title'] for node in state['categories']] == ['Заказы', 'Выплаты']
    # Оператору — название отдела и готов ли он; id чужого чата не уходит.
    assert state['destinations'] == [
        {'id': w.finance, 'title': 'Финансы', 'is_active': True, 'is_ready': True, 'chat_title': 'Тез · Финансы'},
        {'id': w.no_chat, 'title': 'Модерация', 'is_active': True, 'is_ready': False, 'chat_title': None}]
    assert state['pending'] == [] and state['user'] == {'id': ANNA, 'name': 'Анна Оператор'}
    assert state['link_example'] == 'https://admin.tezapp.org/admin/drivers/12345'

    status, bundle = api.post('/phone/calls', json={'phone': '+7 (701) 234-56-78', 'direction': 'in',
                                                    'call_key': 'api-1'})
    assert status == 200
    card = bundle['card']
    card_id = card['id']
    assert card['phone_text'] == '+7 701 234-56-78' and card['state'] == 'ringing'
    assert card['direction_label'] == 'Входящий' and card['created_at'].endswith('+05:00')
    assert bundle['accounts'] == [] and bundle['completion_error'].startswith('Выберите аккаунт')
    assert api.post('/phone/calls', json={'phone': '12', 'direction': 'in'})[1]['code'] == 'CARD_BAD_PHONE'

    assert api.post('/cards/%d/event' % card_id, json={'event': 'answered'})[1]['card']['state'] == 'talking'
    assert api.post('/cards/%d/event' % card_id, json={'event': 'boom'})[1]['code'] == 'CARD_BAD_EVENT'
    w.tez.by_phone_reply[PHONE] = {
        'source': SOURCE_FIRST_ORDERS, 'ok': True, 'error': None, 'accounts': [],
        'driver_hint': {'exists': True, 'registered_at': datetime(2026, 11, 5, 14, 3), 'last_order_at': None}}
    status, looked = api.post('/cards/%d/lookup' % card_id)
    assert looked['tez'] == {'source': 'first_orders', 'ok': True, 'error': None, 'driver_hint': {
        'exists': True, 'registered_at': '2026-11-05T14:03:00+05:00', 'last_order_at': None}}

    status, body = api.post('/cards/%d/account' % card_id, json={'link': 'https://ya.ru'})
    assert (status, body['code']) == (400, 'CARD_BAD_LINK') and 'admin.tezapp.org' in body['error']
    status, linked = api.post('/cards/%d/account' % card_id,
                              json={'link': 'https://admin.tezapp.org/admin/drivers/12345'})
    account = linked['account']
    assert status == 200 and account['kind_label'] == 'Водитель' and account['known'] is False
    assert account['url'] == 'https://admin.tezapp.org/admin/drivers/12345'
    assert account['link']['origin'] == 'manual' and account['link']['can_unlink'] is True
    assert linked['completion_error'] == 'Выберите причину обращения'

    status, saved = api.patch('/cards/%d' % card_id, json={
        'topics': [{'category_id': w.orders, 'subcategory_id': w.no_orders}], 'comment': 'тишина с утра',
        'city': 'Алматы'})
    assert status == 200 and saved['completion_error'] is None and saved['card']['city'] == 'Алматы'
    assert api.patch('/cards/%d' % card_id, json={'topics': [{'category_id': w.orders}]})[1]['code'] == 'CARD_BAD_TOPICS'

    w.clock.tick(minutes=4)
    status, ended = api.post('/cards/%d/event' % card_id, json={'event': 'ended'})
    assert ended['card']['state'] == 'wrapup' and ended['card']['wrapup_left_sec'] == 60
    assert [item['id'] for item in api.get('/phone/state')[1]['pending']] == [card_id]
    w.clock.tick(seconds=20)
    status, done = api.post('/cards/%d/complete' % card_id)
    assert status == 200 and done['card']['state'] == 'done' and done['card']['late'] is False
    assert api.get('/phone/state')[1]['pending'] == []

    # Следующий звонок с того же номера: аккаунт уже привязан, история — на месте.
    status, again = api.post('/phone/calls', json={'phone': PHONE, 'direction': 'out', 'call_key': 'api-2'})
    assert [item['tez_id'] for item in again['accounts']] == [12345] and again['accounts'][0]['city'] == 'Алматы'
    second = again['card']['id']
    assert api.get('/history?card_id=%d' % second)[1]['items'][0]['id'] == card_id
    assert api.post('/cards/%d/account' % second, json={'account_id': account['id']})[1]['card']['account_kind'] == 'driver'
    assert api.post('/cards/%d/account' % second, json={'account_id': 'x'})[1]['code'] == 'CARD_BAD_ACCOUNT'
    status, history = api.get('/history?card_id=%d&q=тишина' % second)
    assert [item['id'] for item in history['items']] == [card_id] and history['has_more'] is False
    assert history['items'][0]['account'] == {'kind': 'driver', 'kind_label': 'Водитель', 'tez_id': 12345,
                                              'full_name': None}
    status, detail = api.get('/cards/%d' % card_id, user=BORIS)
    assert status == 200 and detail['can_edit'] is False and detail['card']['comment'] == 'тишина с утра'
    link_id = again['accounts'][0]['link']['id']
    assert api.delete('/cards/%d/links/%d' % (second, link_id), user=BORIS)[0] == 403
    assert api.delete('/cards/%d/links/%d' % (second, link_id))[1]['accounts'] == []


def test_api_requests_flow(api):
    w = api.w
    card_id = _ready_card(w, w.ctx(ANNA), key='q1')
    import io

    status, created = api.post('/requests', data={
        'card_id': str(card_id), 'destination_id': str(w.finance), 'body': 'Проверьте выплату за 10.11',
        'files': [(io.BytesIO(b'png'), 'Скрин.png', 'image/png'), (io.BytesIO(b''), 'пусто.txt')]},
        content_type='multipart/form-data')
    assert status == 201 and created['delivery_error'] is None and created['can_act'] is True
    request = created['request']
    assert request['status_label'] == 'Ждёт ответа' and request['chat_title'] == 'Тез · Финансы'
    assert request['account']['url'].endswith('/admin/drivers/12345') and request['unread'] == 0
    assert [(m['direction'], bool(m['file']), m['pending']) for m in created['messages']] == [
        ('out', False, False), ('out', True, False)]
    assert created['messages'][1]['file'] == {'name': 'Скрин.png', 'mime': 'image/png', 'size': 3, 'kind': 'photo'}
    status, body = api.post('/requests', json={'card_id': card_id, 'destination_id': w.finance, 'body': ''})
    assert (status, body['code']) == (400, 'REQUEST_EMPTY')

    root = w.outbox.messages[0]['message_id']
    w.svc.ingest_group_reply(chat_id=CHAT_FINANCE, reply_to_message_id=root, message={
        'message_id': 9001, 'caption': 'Вот выписка', 'from': {'id': 77, 'first_name': 'Айгуль'},
        'document': {'file_id': 'tg-doc-1', 'file_name': 'выписка.pdf', 'mime_type': 'application/pdf',
                     'file_size': 1024}})
    status, listing = api.get('/requests')
    assert listing['unread_requests'] == 1 and listing['items'][0]['unread'] == 1
    assert listing['items'][0]['status'] == 'answered'
    assert api.get('/requests', user=BORIS)[1]['items'] == []
    assert len(api.get('/requests', user=HEAD)[1]['items']) == 1
    assert api.get('/requests?mine=1', user=HEAD)[1]['items'] == []
    assert api.get('/requests/%d' % request['id'], user=BORIS)[0] == 403

    status, detail = api.get('/requests/%d' % request['id'])
    assert detail['request']['unread'] == 0 and api.get('/requests')[1]['unread_requests'] == 0
    incoming = detail['messages'][-1]
    assert incoming['direction'] == 'in' and incoming['author_name'] == 'Айгуль' and incoming['body'] == 'Вот выписка'
    assert incoming['file'] == {'name': 'выписка.pdf', 'mime': 'application/pdf', 'size': 1024, 'kind': 'document'}
    # Наш файл — ссылкой на хранилище; чужого сообщения в запросе нет.
    ours = detail['messages'][1]
    assert api.get('/requests/%d/files/%d' % (request['id'], ours['id']))[1]['url'].startswith('https://storage.example/')
    assert api.get('/requests/%d/files/%d' % (request['id'], 999999))[0] == 404

    status, replied = api.post('/requests/%d/reply' % request['id'],
                               json={'body': 'Спасибо!', 'reply_to': incoming['id']})
    assert status == 200 and w.outbox.messages[-1]['reply_to'] == 9001
    assert api.post('/requests/%d/reply' % request['id'], json={'body': ' '})[1]['code'] == 'REQUEST_EMPTY'
    assert api.post('/requests/%d/status' % request['id'], json={'status': 'resolved'})[1]['request']['status'] == 'resolved'
    assert api.post('/requests/%d/status' % request['id'], json={'status': 'bogus'})[1]['code'] == 'REQUEST_BAD_STATUS'
    assert api.post('/requests/%d/status' % request['id'], json={'status': 'reopen'}, user=HEAD)[1]['request']['status'] == 'answered'
    assert api.post('/requests/%d/retry' % request['id'])[1]['delivery_error'] is None


def test_api_management(api):
    w = api.w
    status, screen = api.get('', user=HEAD)
    assert status == 200 and screen['can_manage'] is True and screen['department_id'] == TEZ
    assert screen['settings']['enabled'] is True and screen['settings']['group_ids'] == [GROUP_TP]
    assert sorted(group['id'] for group in screen['groups']) == [GROUP_TP, GROUP_OP]
    assert sorted(chat['chat_id'] for chat in screen['chats']) == sorted([CHAT_FINANCE, CHAT_ALERTS])
    assert screen['head'] == {'name': 'Глава Теза', 'has_telegram': True}
    assert screen['destinations'][0]['chat_id'] == CHAT_FINANCE and 'all_departments' not in screen
    status, screen = api.get('')
    assert screen['can_manage'] is False and screen['settings'] == {'enabled': True}
    assert 'groups' not in screen and 'chats' not in screen and 'chat_id' not in screen['destinations'][0]
    assert any(item['id'] == OP and not item['provided'] for item in api.get('', user=SUPER)[1]['all_departments'])

    # Настройки: оператору закрыты; значения приводятся к границам; чужая группа и чужой чат не проходят.
    assert api.put('/settings', json={'wrapup_sec': 30})[0] == 403
    status, body = api.put('/settings', user=HEAD, json={
        'wrapup_sec': 5000, 'group_ids': [GROUP_TP, GROUP_OP, 999], 'alert_head': False,
        'alerts': {'min_count': 1, 'window_min': 30}, 'alert_chat_id': CHAT_ALERTS})
    settings = body['settings']
    assert settings['wrapup_sec'] == rules.WRAPUP_MAX_SEC and settings['group_ids'] == [GROUP_TP, GROUP_OP]
    assert settings['alerts']['min_count'] == 2 and settings['alerts']['window_min'] == 30
    assert settings['alert_chat_title'] == 'Тез · Руководители' and settings['updated_by_name'] == 'Глава Теза'
    assert api.put('/settings', user=HEAD, json={'alert_chat_id': -555})[1]['code'] == 'CLIENT_CARDS_BAD_CHAT'
    assert api.put('/settings', user=SV, json={'alert_chat_id': None})[1]['settings']['alert_chat_id'] is None
    assert w.svc.phone_settings(SALES) == {'enabled': True, 'wrapup_sec': rules.WRAPUP_MAX_SEC}
    # Выдать раздел отделу — только администратор компании.
    assert api.post('/departments', user=HEAD, json={'department_id': OP})[0] == 403
    assert api.post('/departments', user=SUPER, json={'department_id': OP})[0] == 201
    assert api.post('/departments', user=SUPER, json={'department_id': 12345})[0] == 404
    assert sorted(item['id'] for item in api.get('', user=SUPER)[1]['departments']) == [OP, TEZ]

    # Причины: список одним вставленным текстом, подтема, правка, порядок, удаление.
    assert api.post('/categories', json={'title': 'Нельзя'})[0] == 403
    status, body = api.post('/categories', user=HEAD, json={'titles': ['Аккаунт', ' ', 'Приложение']})
    assert status == 201 and [node['title'] for node in body['categories']][-2:] == ['Аккаунт', 'Приложение']
    account_id = body['categories'][-2]['id']
    status, body = api.post('/categories', user=HEAD, json={'title': 'Блокировка', 'parent_id': account_id})
    sub_id = next(node for node in body['categories'] if node['id'] == account_id)['children'][0]['id']
    assert api.post('/categories', user=HEAD, json={'title': 'X', 'parent_id': sub_id})[0] == 404  # два уровня
    assert api.post('/categories', user=HEAD, json={'title': '  '})[1]['code'] == 'CLIENT_CARDS_EMPTY_TITLE'
    status, body = api.patch('/categories/%d' % sub_id, user=HEAD, json={'title': 'Блокировка аккаунта',
                                                                       'is_active': False})
    node = next(node for node in body['categories'] if node['id'] == account_id)
    assert node['children'][0] == {'id': sub_id, 'title': 'Блокировка аккаунта', 'is_active': False,
                                   'position': 1, 'used': 0}
    status, body = api.post('/categories/reorder', user=HEAD, json={'ids': [account_id, w.money, w.orders]})
    assert [node['id'] for node in body['categories']][:3] == [account_id, w.money, w.orders]
    card_id = _ready_card(w, w.ctx(ANNA), key='m1')
    assert api.delete('/categories/%d' % w.orders, user=HEAD)[1]['code'] == 'CLIENT_CARDS_CATEGORY_IN_USE'
    assert api.delete('/categories/%d' % account_id, user=HEAD)[0] == 200
    assert api.delete('/categories/%d' % account_id, user=HEAD)[0] == 404
    assert api.patch('/categories/%d' % w.orders, json={'title': 'Чужое'})[0] == 404

    # Адресаты: чат — только из тех, где бот состоит; с перепиской — не удалить.
    status, body = api.post('/destinations', user=HEAD, json={'title': 'Техотдел', 'chat_id': CHAT_ALERTS,
                                                              'mention_usernames': 'fin_lead, @second_lead https://t.me/third_lead'})
    created = body['destinations'][-1]
    assert status == 201 and created['chat_title'] == 'Тез · Руководители' and created['is_ready'] is True
    assert created['mention_usernames'] == ['fin_lead', 'second_lead', 'third_lead']
    assert api.post('/destinations', user=HEAD, json={'title': 'Опечатка', 'mention_usernames': 'a b!'})[1]['code'] == 'CLIENT_CARDS_BAD_USERNAME'
    assert api.post('/destinations', user=HEAD, json={'title': 'Нет чата', 'chat_id': -9})[1]['code'] == 'CLIENT_CARDS_BAD_CHAT'
    assert api.post('/destinations', user=HEAD, json={'title': ''})[1]['code'] == 'CLIENT_CARDS_EMPTY_TITLE'
    assert api.patch('/destinations/%d' % created['id'], user=HEAD, json={'is_active': False, 'chat_id': None})[0] == 200
    _request(w, w.ctx(ANNA), card_id)
    assert api.delete('/destinations/%d' % w.finance, user=HEAD)[1]['code'] == 'CLIENT_CARDS_DESTINATION_IN_USE'
    assert api.delete('/destinations/%d' % created['id'], user=HEAD)[0] == 200
    assert api.delete('/destinations/%d' % w.no_chat)[0] == 404

    # Проверочный алерт: адресатов нет — так и сказано; есть — сообщение уходит главе лично.
    assert api.post('/alerts/test', user=HEAD, json={})[1]['code'] == 'ALERT_NO_RECIPIENTS'
    api.put('/settings', user=HEAD, json={'alert_head': True})
    sent_before = len(w.outbox.messages)
    status, body = api.post('/alerts/test', user=HEAD, json={})
    assert status == 200 and body == {'sent': ['Глава Теза'], 'errors': []}
    assert w.outbox.messages[sent_before]['chat_id'] == 700300
    assert api.post('/alerts/test', json={})[0] == 403


def test_api_no_account(api):
    """«Аккаунта нет» ручкой: карточка отдаёт отметку, сохраняется без аккаунта, снимается выбором."""
    w = api.w
    status, bundle = api.post('/phone/calls', json={'phone': '77019990021', 'direction': 'in', 'call_key': 'api-na'})
    card_id = bundle['card']['id']
    assert status == 200 and bundle['card']['no_account'] is False
    assert 'Аккаунта нет' in bundle['completion_error']
    api.post('/cards/%d/event' % card_id, json={'event': 'answered'})

    status, marked = api.post('/cards/%d/account' % card_id, json={'none': True})
    assert status == 200 and marked['card']['no_account'] is True and marked['account'] is None
    assert marked['card']['account_id'] is None and marked['completion_error'] == 'Выберите причину обращения'
    # Чужую карточку так не отметить.
    assert api.post('/cards/%d/account' % card_id, user=BORIS, json={'none': True})[0] == 403

    status, cleared = api.post('/cards/%d/account' % card_id, json={'account_id': None})
    assert status == 200 and cleared['card']['no_account'] is False
    api.post('/cards/%d/account' % card_id, json={'none': True})
    api.patch('/cards/%d' % card_id, json={'topics': [{'category_id': w.money}]})
    api.post('/cards/%d/event' % card_id, json={'event': 'ended'})
    status, done = api.post('/cards/%d/complete' % card_id)
    assert status == 200 and done['card']['state'] == 'done' and done['card']['no_account'] is True

    # В журнале и в обращении для чтения отметка видна; в отчёте — отдельным числом.
    status, journal = api.get('/journal', user=HEAD)
    row = next(item for item in journal['items'] if item['id'] == card_id)
    assert row['no_account'] is True and row['account'] is None
    status, detail = api.get('/cards/%d' % card_id)
    assert detail['card']['no_account'] is True and detail['account'] is None
    status, body = api.get('/report?from=2026-11-12&to=2026-11-12', user=HEAD)
    assert status == 200 and body['totals']['no_account'] == 1 and body['cities'] == []


def test_api_journal_and_report(api):
    w = api.w
    _calls(w, ANNA, 2, topic=w.no_orders, start_key=0)
    _calls(w, BORIS, 1, topic=w.cancel, start_key=10, city='Астана')
    status, journal = api.get('/journal', user=HEAD)
    assert status == 200 and len(journal['items']) == 3 and journal['own_only'] is False
    assert journal['from'] == '2026-11-01' and journal['to'] == '2026-11-12'
    assert journal['items'][0]['account']['url'].startswith('https://admin.tezapp.org/admin/drivers/')
    # Оператор видит в журнале только свои обращения, какой бы фильтр ни прислал.
    status, mine = api.get('/journal?operator_id=%d' % BORIS)
    assert mine['own_only'] is True and {item['operator_id'] for item in mine['items']} == {ANNA}
    assert len(api.get('/journal?operator_id=%d' % BORIS, user=HEAD)[1]['items']) == 1
    assert api.get('/journal?from=2026-10-01&to=2026-10-31', user=HEAD)[1]['items'] == []
    assert api.get('/journal?from=2020-01-01&to=2026-11-12', user=HEAD)[1]['code'] == 'CLIENT_CARDS_PERIOD_TOO_LONG'

    assert api.get('/report')[0] == 403
    status, data = api.get('/report?from=2026-11-12&to=2026-11-12', user=HEAD)
    assert status == 200 and data['totals']['done'] == 3 and data['days'] == [
        {'date': '2026-11-12', 'calls': 3, 'done': 3}]
    assert [(city['city'], city['count']) for city in data['cities']] == [('Алматы', 2), ('Астана', 1)]
    status, response = api.get('/report/export?from=2026-11-12&to=2026-11-12', user=HEAD)
    assert status == 200 and response.mimetype == routes.XLSX_MIME and response.data[:2] == b'PK'
    assert 'attachment' in response.headers['Content-Disposition']
