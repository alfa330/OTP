# -*- coding: utf-8 -*-
"""«Основные средства» (ACC-1) на настоящем Postgres: FIXED_ASSETS_TEST_PORT — порт локального кластера.

Чистые правила и права проверяет tests/test_fixed_assets_rules.py. Здесь — весь серверный путь
через настоящий Blueprint: приём в учёт, перемещение, списание, передача при увольнении, «Новое
ОС», фото и сканы, история — и то, что видимость в SQL совпадает с правилами access.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным именем, в конце
она удаляется. Люди и ОС выдуманы.

    FIXED_ASSETS_TEST_PORT=55481 python -m pytest tests/test_fixed_assets_postgres.py
"""
import contextlib
import json
import os
import uuid
from datetime import timedelta
from io import BytesIO
from urllib.parse import quote

import pytest

from fixed_assets import access, queries, rules, schema

PORT = os.environ.get('FIXED_ASSETS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (FIXED_ASSETS_TEST_PORT)')

SUPER, CHIEF, ACCOUNTANT, HR_HEAD, HR, IT_HEAD, IT, ANALYTICS_HEAD, ANALYST = 1, 2, 12, 3, 13, 4, 14, 5, 15
SALES_HEAD, HOLDER, HOLDER2, RECEIVER, MARKETING_HEAD, FIRED, OUTSIDER, AHO = 7, 8, 9, 20, 60, 70, 80, 90
EVERYONE = (SUPER, CHIEF, ACCOUNTANT, HR_HEAD, HR, IT_HEAD, IT, ANALYTICS_HEAD, ANALYST, SALES_HEAD, HOLDER, HOLDER2,
            RECEIVER, MARKETING_HEAD, OUTSIDER, AHO)
OP, MARKETING = 5, 6

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text UNIQUE, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text NOT NULL,
                        status text NOT NULL DEFAULT 'working', department_id integer, job_title varchar(255));
    INSERT INTO departments (id, code, name, head_user_id) VALUES
        (1, 'accounting', 'Бухгалтерия', 2), (2, 'hr', 'HR', 3), (3, 'it', 'IT', 4), (4, 'analytik', 'Отдел аналитики', 5),
        (5, 'op', 'Отдел продаж', 7), (6, 'marketing', 'Маркетинг', 60), (7, 'aho', 'АХО', NULL);
    INSERT INTO users (id, name, login, role, department_id, job_title, status) VALUES
        (1, 'Суперадминов Сергей', 'super', 'super_admin', NULL, 'Директор', 'working'),
        (2, 'Главбухова Гульмира', 'chief', 'admin', 1, 'Главный бухгалтер', 'working'),
        (12, 'Бухгалтерова Асель', 'buh', 'accounting_manager', 1, 'Бухгалтер', 'working'),
        (3, 'Кадрова Мадина', 'hr_head', 'admin', 2, 'Руководитель HR', 'working'),
        (13, 'Кадровикова Айгерим', 'hr', 'hr_manager', 2, 'HR-менеджер', 'working'),
        (4, 'Айтишников Сергей', 'it_head', 'admin', 3, 'Руководитель IT', 'working'),
        (14, 'Админов Данияр', 'it', 'operator', 3, 'Системный администратор', 'working'),
        (5, 'Аналитиков Ержан', 'analytics_head', 'admin', 4, 'Руководитель аналитики', 'working'),
        (15, 'Аналитикова Дана', 'analyst', 'operator', 4, 'Аналитик', 'working'),
        (7, 'Продажина Алия', 'sales_head', 'sv', 5, 'Руководитель отдела продаж', 'working'),
        (8, 'Менеджеров Тимур', 'holder', 'operator', 5, 'Менеджер', 'working'),
        (9, 'Менеджерова Дана', 'holder2', 'operator', 5, 'Менеджер', 'working'),
        (20, 'Маркетологова Ольга', 'receiver', 'marketing_manager', 6, 'Маркетолог', 'working'),
        (60, 'Маркетингов Олег', 'marketing_head', 'admin', 6, 'Руководитель маркетинга', 'working'),
        (70, 'Уволенный Ульян', 'fired', 'operator', 5, 'Менеджер', 'fired'),
        (80, 'Посторонний Петр', 'outsider', 'operator', 6, 'Дизайнер', 'working'),
        (90, 'Завхозов Захар', 'aho', 'operator', 7, 'Завхоз', 'working');
'''

PDF = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n'


def jpeg(color=(40, 90, 160)):
    from PIL import Image

    image = Image.new('RGB', (900, 700), color)
    for x in range(0, 900, 30):                       # не однотонный кадр: иначе WebP выйдет в сотню байт
        for y in range(0, 700, 30):
            image.putpixel((x, y), (x % 255, y % 255, (x + y) % 255))
    out = BytesIO()
    image.save(out, 'JPEG', quality=85)
    return out.getvalue()


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres', connect_timeout=3)
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


class _Blob:
    def __init__(self, store, path):
        self.store, self.path = store, path
        self.cache_control = None

    def upload_from_string(self, data, content_type=None):
        self.store[self.path] = (data, content_type)

    def exists(self):
        return self.path in self.store

    def download_as_bytes(self):
        return self.store[self.path][0]

    def delete(self):
        self.store.pop(self.path, None)

    def generate_signed_url(self, **_kwargs):
        return 'https://signed.example/' + self.path


class _Client:
    def __init__(self, store):
        self.store = store

    def bucket(self, _name):
        store = self.store

        class _Bucket:
            def blob(self, path):
                return _Blob(store, path)
        return _Bucket()


@pytest.fixture(autouse=True)
def section_open(monkeypatch):
    """Здесь проверяются правила раздела для всех ролей; пилот — отдельным тестом ниже."""
    monkeypatch.setattr(access, 'PILOT_SUPER_ADMIN_ONLY', False)


@pytest.fixture
def db():
    name = 't_fa_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL)
            schema.ensure_in_cursor(cur)
            schema.ensure_in_cursor(cur)          # повторный разворот ничего не ломает и не дублирует
        admin.commit()
        yield base
    finally:
        admin.rollback()
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.commit()
        admin.close()


class Api:
    def __init__(self, db):
        from flask import Flask, jsonify

        from fixed_assets.routes import build_fixed_assets_blueprint

        self.user = SUPER
        self.blobs = {}
        app = Flask(__name__)
        app.register_blueprint(build_fixed_assets_blueprint(
            db=db,
            require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: (jsonify({}), 200),
            resolve_requester=lambda: (self.user, None, None),
            excel_text_warning=None,
            gcs={'bucket_name': lambda: 'fa-test', 'client': lambda: _Client(self.blobs)},
        ))
        self.client = app.test_client()

    def as_(self, user):
        self.user = user
        return self

    def get(self, path, **params):
        return self.client.get('/api/fixed_assets' + path, query_string=params)

    def post(self, path, body=None, files=None):
        if files:
            data = {'payload': json.dumps(body or {}, ensure_ascii=False, default=str),
                    'files': [(BytesIO(content), filename) for filename, content in files]}
            return self.client.post('/api/fixed_assets' + path, data=data, content_type='multipart/form-data')
        return self.client.post('/api/fixed_assets' + path, json=body or {})

    def put(self, path, body):
        return self.client.put('/api/fixed_assets' + path, json=body)

    def patch(self, path, body):
        return self.client.patch('/api/fixed_assets' + path, json=body)

    def delete(self, path, **params):
        return self.client.delete('/api/fixed_assets' + path, query_string=params)


@pytest.fixture
def api(db):
    return Api(db)


def categories(api):
    return {item['name']: item['id'] for item in api.as_(CHIEF).get('/context').get_json()['categories']}


COUNTER = iter(range(1, 10_000))


def intake(api, *, user=ACCOUNTANT, holder=HOLDER, category='Компьютеры и ноутбуки', expect=201, photos=True, **fields):
    number = next(COUNTER)
    body = {'name': 'Ноутбук %d' % number, 'category_id': categories(api)[category],
            'inventory_number': 'ОС-%06d' % number, 'serial_number': 'SN%d' % number, 'cost': '250000',
            'to_user_id': holder, 'to_location': 'каб. 204',
            'photos': photos if isinstance(photos, list)
            else ([{'kind': 'photo_whole'}, {'kind': 'photo_number'}] if photos else [])}
    body.update(fields)
    files = [('whole.jpg', jpeg()), ('number.jpg', jpeg((200, 200, 190)))][:len(body['photos'])]
    response = api.as_(user).post('/assets', body, files or None)
    assert response.status_code == expect, response.get_json()
    return response.get_json()


def new_asset(api, **fields):
    return intake(api, **fields)['asset']['id']


def asset_photos(asset_ids):
    meta, files = [], []
    for asset_id in asset_ids:
        meta += [{'asset_id': asset_id, 'kind': 'photo_whole'}, {'asset_id': asset_id, 'kind': 'photo_number'}]
        files += [('whole.jpg', jpeg()), ('number.jpg', jpeg((210, 210, 200)))]
    return meta, files


def new_request(api, user, kind, asset_ids=(), *, submit=True, photos=True, expect=201, **fields):
    body = dict(fields, kind=kind, asset_ids=list(asset_ids), submit=submit)
    files = None
    if photos and asset_ids:
        body['photos'], files = asset_photos(asset_ids)
    response = api.as_(user).post('/requests', body, files)
    assert response.status_code == expect, response.get_json()
    return response.get_json()


def scan(api, user, request_id, *, signed_on=None, content=PDF, name='akt.pdf', expect=200):
    body = {'signed_on': (signed_on or queries.today_almaty()).isoformat()}
    response = api.as_(user).post('/requests/%d/scan' % request_id, body, [(name, content)])
    assert response.status_code == expect, response.get_json()
    return response.get_json()


def receive(api, user, request_id, *, handed_by=HOLDER, handed_on=None, expect=200):
    body = {'handed_by_user_id': handed_by, 'handed_on': (handed_on or queries.today_almaty()).isoformat()}
    response = api.as_(user).post('/requests/%d/original' % request_id, body)
    assert response.status_code == expect, response.get_json()
    return response.get_json()


def card(api, user, request_id):
    response = api.as_(user).get('/requests/%d' % request_id)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def asset_card(api, user, asset_id):
    response = api.as_(user).get('/assets/%d' % asset_id)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def kinds(events):
    return [event['kind'] for event in events]


# ─── Приём в учёт ────────────────────────────────────────────────────────────

def test_accounting_takes_an_asset_on_the_books(api):
    result = intake(api)
    asset = result['asset']
    assert (asset['status'], asset['mol_user_id'], asset['department_name']) == ('active', HOLDER, 'Отдел продаж')
    assert asset['cost'] == 250000.0
    request = card(api, ACCOUNTANT, result['request_id'])
    assert (request['request']['kind'], request['request']['status']) == ('intake', 'signing')
    assert request['request']['act']['act_date'] == queries.today_almaty().isoformat()
    assert len(request['items'][0]['photos']) == 2
    full = asset_card(api, ACCOUNTANT, asset['id'])
    assert kinds(full['events']) == ['registered']
    assert len(full['events'][0]['payload']['files']) == 2
    assert [photo['kind'] for photo in full['photos']] == ['photo_whole', 'photo_number']
    assert all(photo['url'].startswith('https://signed.example/fixed_assets/photos/') for photo in full['photos'])


def test_only_accounting_creates_asset_cards(api):
    for user in (IT, HR, SALES_HEAD, HOLDER, ANALYTICS_HEAD):
        result = intake(api, user=user, expect=403)
        assert result['code'] in ('FA_INTAKE_FORBIDDEN', 'FA_CLOSED')
    assert api.as_(CHIEF).get('/assets').get_json()['total'] == 0


def test_intake_without_both_photos_is_refused_and_leaves_nothing(api):
    result = intake(api, expect=400, photos=[{'kind': 'photo_whole'}])
    assert result['code'] == 'FA_PHOTOS_REQUIRED' and 'инвентарный номер' in result['error']
    assert api.blobs == {}
    assert api.as_(CHIEF).get('/assets').get_json()['total'] == 0


def test_inventory_number_is_unique_ignoring_case(api):
    intake(api, inventory_number='ос-77')
    result = intake(api, inventory_number='ОС-77', expect=409)
    assert result['code'] == 'FA_INVENTORY_TAKEN' and 'уже занят: «Ноутбук' in result['error']
    assert len([path for path in api.blobs if 'thumb' not in path]) == 2      # фото отказа в бакете не осталось


def test_asset_cannot_be_assigned_to_a_fired_employee(api):
    assert intake(api, holder=FIRED, expect=400)['code'] == 'FA_USER_INVALID'


def test_next_inventory_number_follows_the_last_one(api):
    assert api.as_(CHIEF).get('/assets/next-number').get_json()['suggestion'] == ''
    intake(api, inventory_number='ИНВ-0099')
    assert api.as_(CHIEF).get('/assets/next-number').get_json()['suggestion'] == 'ИНВ-0100'
    assert api.as_(IT).get('/assets/next-number').status_code == 403


def test_card_is_corrected_only_while_intake_act_is_open(api):
    result = intake(api)
    asset_id, request_id = result['asset']['id'], result['request_id']
    assert api.as_(HR).patch('/assets/%d' % asset_id, {'serial_number': 'X', 'reason': 'опечатка'}).status_code == 409
    response = api.as_(ACCOUNTANT).patch('/assets/%d' % asset_id, {'serial_number': 'SN-FIXED', 'reason': 'опечатка'})
    assert response.status_code == 200 and response.get_json()['asset']['serial_number'] == 'SN-FIXED'
    assert api.as_(ACCOUNTANT).patch('/assets/%d' % asset_id, {'serial_number': 'SN-FIXED', 'reason': 'ещё раз'}).status_code == 409
    assert api.as_(ACCOUNTANT).patch('/assets/%d' % asset_id, {'serial_number': 'Z'}).status_code == 400   # без причины
    events = asset_card(api, CHIEF, asset_id)['events']
    assert events[0]['kind'] == 'corrected'
    assert events[0]['payload']['changes'][0]['from'] == result['asset']['serial_number']
    assert card(api, CHIEF, request_id)['items'][0]['serial_number'] == 'SN-FIXED'      # акт печатает исправленное
    scan(api, ACCOUNTANT, request_id)
    receive(api, ACCOUNTANT, request_id)
    closed = api.as_(ACCOUNTANT).patch('/assets/%d' % asset_id, {'serial_number': 'LATE', 'reason': 'поздно'})
    assert closed.status_code == 409 and closed.get_json()['code'] == 'FA_CORRECT_CLOSED'


# ─── Штрихкод ────────────────────────────────────────────────────────────────

def test_asset_is_found_by_scanning_its_sticker(api):
    """Владелец 24.11.2026: «находить ОС, просто отсканировав штрихкод, и дальше выполнять с ним
    действия». Скан находит ОС по штрихкоду наклейки, инвентарному или серийному номеру — и только
    среди тех, что человек и так видит."""
    laptop = intake(api, inventory_number='ЕК0000456', serial_number='K2201N0012345', barcode='2000000 056784')
    asset_id = laptop['asset']['id']
    assert laptop['asset']['barcode'] == '2000000056784'

    def lookup(user, code):
        response = api.as_(user).get('/assets/lookup', code=code)
        assert response.status_code == 200, response.get_json()
        return response.get_json()

    for code in ('2000000056784', ' 2000000056784\n', 'ек0000456', 'k2201n0012345'):
        with_code = lookup(ACCOUNTANT, code)
        assert [item['id'] for item in with_code['items']] == [asset_id], code
    # МОЛ находит своё ОС, руководитель — ОС своего отдела; чужой руководитель — нет, но узнаёт, что ОС есть.
    assert [item['id'] for item in lookup(HOLDER, '2000000056784')['items']] == [asset_id]
    assert [item['id'] for item in lookup(SALES_HEAD, '2000000056784')['items']] == [asset_id]
    hidden = lookup(MARKETING_HEAD, '2000000056784')
    assert hidden['items'] == [] and hidden['hidden'] == 1
    nothing = lookup(ACCOUNTANT, '4600000000000')
    assert nothing['items'] == [] and nothing['hidden'] == 0
    assert api.as_(ACCOUNTANT).get('/assets/lookup', code='  ').status_code == 400
    # С найденным ОС дальше работают как обычно: карточка открывается, перемещение доступно.
    found = asset_card(api, SALES_HEAD, asset_id)
    assert 'transfer' in found['can_request'] and found['asset']['barcode'] == '2000000056784'
    # Штрихкод ищется и в реестре.
    registry = api.as_(CHIEF).get('/assets', q='056784').get_json()
    assert [item['id'] for item in registry['items']] == [asset_id]


def test_scan_finds_numbers_typed_differently(api):
    """Разбор 24.11: номер в карточке заведён с пробелом или кириллицей, а сканер и ручной ввод дают код без
    пробелов и на любой раскладке; UPC-A одни сканеры читают 12 цифрами, другие — 13 с нулём."""
    spaced = intake(api, inventory_number='ОС 000777', serial_number='SN 5CG 1234')['asset']['id']
    cyrillic = intake(api, inventory_number='ЕК0000456')['asset']['id']
    upc = intake(api, barcode='012345678905')['asset']['id']

    def found(code):
        response = api.as_(ACCOUNTANT).get('/assets/lookup', code=code)
        assert response.status_code == 200, response.get_json()
        return [item['id'] for item in response.get_json()['items']]

    assert found('ОС000777') == [spaced]
    assert found('OC 000777') == [spaced]            # латинские O и C
    assert found('sn5cg1234') == [spaced]
    assert found('EK0000456') == [cyrillic]          # латиница вместо кириллицы
    assert found('0012345678905') == [upc]           # EAN-13 с ведущим нулём — тот же UPC-A
    assert found('012345678905') == [upc]
    assert found('12345678905') == []                # без нуля — уже другой код


def test_card_correction_does_not_touch_the_barcode(api):
    """Разбор 24.11: штрихкод меняет только привязка — с записью «штрихкод» в истории и без списанных ОС.
    Исправление карточки (акт приёма ещё открыт) его молча пропускает."""
    asset_id = intake(api, barcode='2000000056784')['asset']['id']
    patched = api.as_(ACCOUNTANT).patch('/assets/%d' % asset_id, {'barcode': '2000000001111', 'reason': 'опечатка'})
    assert patched.status_code == 409 and patched.get_json()['code'] == 'FA_UNCHANGED', patched.get_json()
    assert asset_card(api, ACCOUNTANT, asset_id)['asset']['barcode'] == '2000000056784'


def test_one_barcode_one_asset(api):
    intake(api, barcode='2000000056784')
    twin = intake(api, barcode='2000000056784', expect=409)
    assert twin['code'] == 'FA_BARCODE_TAKEN'
    assert intake(api, barcode='20000;00', expect=400)['code'] == 'FA_BARCODE_INVALID'


def test_accounting_binds_a_sticker_to_an_asset_any_time(api):
    """ОС, принятые до наклеек, связывают с наклейкой позже — и после закрытия акта приёма."""
    result = intake(api)
    asset_id, request_id = result['asset']['id'], result['request_id']
    scan(api, ACCOUNTANT, request_id)
    receive(api, ACCOUNTANT, request_id)                       # акт приёма закрыт — исправлять карточку уже нельзя
    other = new_asset(api)

    def bind(user, code, expect):
        response = api.as_(user).post('/assets/%d/barcode' % asset_id, {'barcode': code})
        assert response.status_code == expect, response.get_json()
        return response.get_json()

    assert bind(HR, '2000000056784', 403)['code'] == 'FA_BARCODE_FORBIDDEN'
    assert bind(SALES_HEAD, '2000000056784', 403)['code'] == 'FA_BARCODE_FORBIDDEN'
    assert bind(ACCOUNTANT, '2000000056784', 200)['asset']['barcode'] == '2000000056784'
    assert bind(ACCOUNTANT, '2000000056784', 409)['code'] == 'FA_UNCHANGED'
    taken = api.as_(ACCOUNTANT).post('/assets/%d/barcode' % other, {'barcode': '2000000056784'})
    assert taken.status_code == 409 and taken.get_json()['code'] == 'FA_BARCODE_TAKEN'
    events = asset_card(api, CHIEF, asset_id)['events']
    assert events[0]['kind'] == 'barcode_set' and events[0]['payload'] == {'from': None, 'to': '2000000056784'}
    assert events[0]['label'] == 'Привязан штрихкод'
    # Снять штрихкод — тоже запись в истории.
    assert bind(ACCOUNTANT, '', 200)['asset']['barcode'] is None
    removed = asset_card(api, CHIEF, asset_id)['events'][0]
    assert removed['payload'] == {'from': '2000000056784', 'to': None} and removed['label'] == 'Снят штрихкод'
    assert asset_card(api, ACCOUNTANT, asset_id)['can_set_barcode'] is True
    assert asset_card(api, SALES_HEAD, asset_id)['can_set_barcode'] is False


# ─── Перемещение ─────────────────────────────────────────────────────────────

def test_transfer_goes_through_every_step_and_moves_the_asset_only_when_closed(api):
    asset_id = new_asset(api)
    draft = new_request(api, SALES_HEAD, 'transfer', [asset_id], submit=False, photos=False,
                        to_user_id=RECEIVER, to_location='каб. 302', comment='Переезд')
    request_id = draft['request']['id']
    assert draft['request']['status'] == 'draft' and draft['photo_gaps'][0]['missing'] == ['photo_whole', 'photo_number']

    # ТЗ: без обоих фото «Отправить» не срабатывает, и система называет причину.
    refused = api.as_(SALES_HEAD).post('/requests/%d/submit' % request_id)
    assert refused.status_code == 400 and refused.get_json()['code'] == 'FA_PHOTOS_REQUIRED'
    assert 'ОС целиком и инвентарный номер' in refused.get_json()['error']

    meta, files = asset_photos([asset_id])
    saved = api.as_(SALES_HEAD).post('/requests/%d/save' % request_id,
                                     {'asset_ids': [asset_id], 'to_user_id': RECEIVER, 'to_location': 'каб. 302',
                                      'comment': 'Переезд', 'photos': meta, 'submit': True}, files)
    assert saved.status_code == 200, saved.get_json()
    sent = saved.get_json()
    assert sent['request']['status'] == 'signing' and sent['request']['from_user_id'] == HOLDER
    assert [signer['role'] for signer in sent['request']['signers']] == [
        'Сдал', 'Принял', 'Руководитель отдела (сдающая сторона)', 'Руководитель отдела (принимающая сторона)', 'Бухгалтер']

    held = asset_card(api, CHIEF, asset_id)['asset']
    assert held['open_request']['id'] == request_id and held['mol_user_id'] == HOLDER
    busy = new_request(api, CHIEF, 'writeoff', [asset_id], comment='сломан', expect=409)
    assert busy['code'] == 'FA_ASSET_BUSY'

    act = api.as_(RECEIVER).get('/requests/%d/act' % request_id)             # сторона акта скачивает его, чтобы подписать
    assert act.status_code == 200 and act.data.startswith(b'PK')

    assert scan(api, SALES_HEAD, request_id, content=jpeg(), name='akt.pdf', expect=400)['code'] == 'FA_SCAN_NOT_PDF'
    future = queries.today_almaty() + timedelta(days=5)
    assert scan(api, SALES_HEAD, request_id, signed_on=future, expect=400)['code'] == 'FA_DATE_IN_FUTURE'
    assert api.as_(OUTSIDER).post('/requests/%d/scan' % request_id, {'signed_on': '2026-11-17'}, [('a.pdf', PDF)]).status_code == 403
    waiting = scan(api, SALES_HEAD, request_id)
    assert waiting['request']['status'] == 'original' and waiting['request']['act']['scan']['file_name'] == 'akt.pdf'
    assert asset_card(api, CHIEF, asset_id)['asset']['mol_user_id'] == HOLDER      # ОС ещё у прежнего МОЛ

    assert receive(api, SALES_HEAD, request_id, expect=409)['code'] == 'FA_STEP_GONE'  # оригинал принимает бухгалтерия
    closed = receive(api, ACCOUNTANT, request_id)
    assert closed['request']['status'] == 'closed'
    block = closed['request']['act']
    assert block['original_received'] and block['original_received_by_name'] == 'Бухгалтерова Асель'
    assert block['original_handed_by_name'] == 'Менеджеров Тимур'
    moved = asset_card(api, CHIEF, asset_id)
    assert (moved['asset']['mol_user_id'], moved['asset']['department_name'], moved['asset']['location']) == (
        RECEIVER, 'Маркетинг', 'каб. 302')
    assert moved['asset']['open_request'] is None
    assert kinds(card(api, CHIEF, request_id)['events']) == [
        'submitted', 'scan_attached', 'original_received', 'moved', 'closed']
    move = next(event for event in moved['events'] if event['kind'] == 'moved')
    assert move['payload']['from']['mol_name'] == 'Менеджеров Тимур'
    assert move['payload']['to']['mol_name'] == 'Маркетологова Ольга'
    assert receive(api, ACCOUNTANT, request_id, expect=409)['code'] == 'FA_STEP_GONE'   # второй раз не закрыть


def test_holder_only_watches(api):
    """Решение владельца 17.11.2026: МОЛ не заводит запросы и не отклоняет их — только смотрит
    за своим ОС. Запрос ведёт тот, кто его завёл; сдающий и принимающий подписывают бумагу."""
    asset_id = new_asset(api)
    context = api.as_(HOLDER).get('/context').get_json()
    assert context['capabilities']['tabs'] == ['my', 'history']
    assert context['capabilities']['create_kinds'] == [] and context['capabilities']['can_intake'] is False
    own = asset_card(api, HOLDER, asset_id)
    assert (own['level'], own['can_request'], own['can_correct']) == ('own', [], False)

    for kind, extra in (('transfer', {'to_user_id': RECEIVER}), ('writeoff', {'comment': 'Сломан'})):
        refused = new_request(api, HOLDER, kind, [asset_id], expect=403, **extra)
        assert refused['code'] == 'FA_KIND_FORBIDDEN'
    assert api.as_(HOLDER).get('/assets', pick='transfer').status_code == 403
    assert api.as_(CHIEF).get('/requests').get_json()['counters']['all'] == 1     # только акт приёма

    request_id = new_request(api, SALES_HEAD, 'transfer', [asset_id], to_user_id=RECEIVER)['request']['id']
    for user in (HOLDER, RECEIVER):                                              # обе стороны акта — только смотрят
        seen = card(api, user, request_id)
        assert seen['actions'] == ['act'] and seen['waiting'] is False
        assert api.as_(user).post('/requests/%d/reject' % request_id, {'reason': 'Не согласен'}).status_code == 409
        assert scan(api, user, request_id, expect=409)['code'] == 'FA_STEP_GONE'
        assert api.as_(user).get('/requests', tile='waiting').get_json()['items'] == []
    assert card(api, CHIEF, request_id)['request']['status'] == 'signing'       # запрос никуда не сдвинулся

    scan(api, SALES_HEAD, request_id)
    for user in (HOLDER, RECEIVER):
        assert card(api, user, request_id)['actions'] == ['act']
        assert receive(api, user, request_id, expect=409)['code'] == 'FA_STEP_GONE'
        assert scan(api, user, request_id, expect=409)['code'] == 'FA_STEP_GONE'  # и заменить скан тоже нельзя


def test_transfer_must_change_holder_or_place(api):
    asset_id = new_asset(api, to_location='каб. 204')
    same = new_request(api, SALES_HEAD, 'transfer', [asset_id], to_user_id=HOLDER, to_location='каб. 204', expect=409)
    assert same['code'] == 'FA_UNCHANGED'
    moved = new_request(api, SALES_HEAD, 'transfer', [asset_id], to_user_id=HOLDER, to_location='каб. 301')
    assert moved['request']['status'] == 'signing'


def test_empty_new_place_keeps_the_old_one(api):
    asset_id = new_asset(api, to_location='каб. 204')
    # Тот же МОЛ, место не указано — менять нечего.
    assert new_request(api, SALES_HEAD, 'transfer', [asset_id], to_user_id=HOLDER, expect=409)['code'] == 'FA_UNCHANGED'
    request_id = new_request(api, SALES_HEAD, 'transfer', [asset_id], to_user_id=RECEIVER)['request']['id']
    scan(api, SALES_HEAD, request_id)
    receive(api, ACCOUNTANT, request_id)
    moved = asset_card(api, CHIEF, asset_id)
    assert (moved['asset']['mol_user_id'], moved['asset']['location']) == (RECEIVER, 'каб. 204')
    move = next(event for event in moved['events'] if event['kind'] == 'moved')
    assert move['payload']['to']['location'] == 'каб. 204'


def test_one_request_holds_assets_of_one_holder(api):
    first, second = new_asset(api, holder=HOLDER), new_asset(api, holder=HOLDER2)
    mixed = new_request(api, IT, 'transfer', [first, second], to_user_id=RECEIVER, expect=400)
    assert mixed['code'] == 'FA_MIXED_HOLDERS'


def test_head_requests_only_assets_of_own_department(api):
    own, foreign = new_asset(api, holder=RECEIVER), new_asset(api, holder=HOLDER)
    refused = new_request(api, MARKETING_HEAD, 'transfer', [foreign], to_user_id=RECEIVER, expect=403)
    assert refused['code'] == 'FA_ASSET_FORBIDDEN'
    assert new_request(api, MARKETING_HEAD, 'transfer', [own], to_user_id=OUTSIDER)['request']['status'] == 'signing'
    picked = api.as_(MARKETING_HEAD).get('/assets', pick='transfer').get_json()
    assert [item['id'] for item in picked['items']] == []                          # своё уже в запросе, чужое не его


def test_draft_is_private_editable_and_deletable(api):
    first, second = new_asset(api), new_asset(api)
    draft = new_request(api, SALES_HEAD, 'transfer', [first, second], submit=False, to_user_id=RECEIVER)
    request_id = draft['request']['id']
    assert api.as_(CHIEF).get('/requests/%d' % request_id).status_code == 404        # черновик видит автор
    assert api.as_(HOLDER).get('/requests/%d' % request_id).status_code == 404       # и МОЛ его ещё не видит
    assert len(api.blobs) >= 4
    edited = api.as_(SALES_HEAD).post('/requests/%d/save' % request_id, {'asset_ids': [second], 'to_user_id': RECEIVER})
    assert [item['asset_id'] for item in edited.get_json()['items']] == [second]
    assert len([p for p in api.blobs if 'thumb' not in p]) == 2 + 4
    assert api.as_(MARKETING_HEAD).delete('/requests/%d' % request_id).status_code == 409
    assert api.as_(SALES_HEAD).delete('/requests/%d' % request_id).status_code == 200
    assert api.as_(SALES_HEAD).get('/requests/%d' % request_id).status_code == 404
    assert len([p for p in api.blobs if 'thumb' not in p]) == 4                      # остались только фото приёма


def test_rejection_needs_a_reason_and_frees_the_asset(api):
    asset_id = new_asset(api)
    request_id = new_request(api, SALES_HEAD, 'transfer', [asset_id], to_user_id=RECEIVER)['request']['id']
    # Отклоняет руководитель стороны акта (здесь — принимающей), а не сам принимающий.
    assert api.as_(MARKETING_HEAD).post('/requests/%d/reject' % request_id, {'reason': ' '}).status_code == 400
    assert api.as_(MARKETING_HEAD).post('/requests/%d/reject' % request_id, {'reason': 'ок'}).status_code == 400   # не причина
    rejected = api.as_(MARKETING_HEAD).post('/requests/%d/reject' % request_id, {'reason': 'Не принимаем: экран разбит'})
    body = rejected.get_json()
    assert rejected.status_code == 200 and body['request']['status'] == 'rejected'
    assert body['request']['rejected_from'] == 'signing' and body['actions'] == []
    assert [step['state'] for step in body['request']['track']] == ['done', 'stopped', 'todo', 'todo']
    assert asset_card(api, CHIEF, asset_id)['asset']['open_request'] is None
    assert scan(api, SALES_HEAD, request_id, expect=409)['code'] == 'FA_STEP_GONE'
    again = new_request(api, SALES_HEAD, 'transfer', [asset_id], to_user_id=RECEIVER)
    assert again['request']['status'] == 'signing'


# ─── Списание ────────────────────────────────────────────────────────────────

def test_tech_writeoff_waits_for_it_check(api):
    asset_id = new_asset(api, category='Оргтехника')
    sent = new_request(api, SALES_HEAD, 'writeoff', [asset_id], comment='Не включается')
    request_id = sent['request']['id']
    assert (sent['request']['status'], sent['request']['review_by']) == ('review', 'it')
    assert sent['request']['status_label'] == 'На проверке IT'
    # ТЗ: «Запрос не уходит на подписи, пока IT не поставит статус «Проверено»».
    assert scan(api, SALES_HEAD, request_id, expect=409)['code'] == 'FA_STEP_GONE'
    assert api.as_(SALES_HEAD).get('/requests/%d/act' % request_id).status_code == 409
    for user in (HOLDER, SALES_HEAD, ACCOUNTANT):
        assert api.as_(user).post('/requests/%d/check' % request_id, {'comment': 'ок'}).status_code == 409
    checked = api.as_(IT).post('/requests/%d/check' % request_id, {'comment': 'Плата сгорела'}).get_json()
    assert checked['request']['status'] == 'signing' and checked['request']['checked_by_name'] == 'Админов Данияр'
    scan(api, SALES_HEAD, request_id)
    receive(api, CHIEF, request_id)
    gone = asset_card(api, CHIEF, asset_id)
    assert gone['asset']['status'] == 'written_off' and 'written_off' in kinds(gone['events'])
    assert new_request(api, CHIEF, 'transfer', [asset_id], to_user_id=RECEIVER, expect=403)['code'] == 'FA_ASSET_FORBIDDEN'
    assert api.as_(HOLDER).get('/assets', scope='mine').get_json()['total'] == 0


def test_furniture_writeoff_is_checked_by_the_department_head(api):
    asset_id = new_asset(api, category='Мебель')
    sent = new_request(api, IT, 'writeoff', [asset_id], comment='Сломано')
    request_id = sent['request']['id']
    assert (sent['request']['review_by'], sent['request']['review_department_id']) == ('head', OP)
    for user in (IT, MARKETING_HEAD, HOLDER):
        assert api.as_(user).post('/requests/%d/check' % request_id, {}).status_code in (403, 404, 409)
    assert api.as_(SALES_HEAD).post('/requests/%d/check' % request_id, {}).get_json()['request']['status'] == 'signing'


def test_department_without_head_is_checked_by_accounting(api):
    asset_id = new_asset(api, category='Мебель', holder=AHO)
    sent = new_request(api, IT, 'writeoff', [asset_id], comment='Сломано')
    assert sent['request']['review_by'] == 'accounting'
    assert api.as_(ACCOUNTANT).post('/requests/%d/check' % sent['request']['id'], {}).status_code == 200


def test_tech_and_furniture_are_written_off_separately(api):
    laptop, chair = new_asset(api), new_asset(api, category='Мебель')
    assert new_request(api, SALES_HEAD, 'writeoff', [laptop, chair], comment='Всё сломано', expect=400)['code'] == 'FA_MIXED_CHECK'


def test_writeoff_needs_a_reason(api):
    asset_id = new_asset(api)
    refused = new_request(api, SALES_HEAD, 'writeoff', [asset_id], expect=400)
    assert refused['code'] == 'FA_INCOMPLETE' and 'comment' in refused['fields']


def docx_text(response):
    import re
    import zipfile

    with zipfile.ZipFile(BytesIO(response.data)) as archive:
        xml = archive.read('word/document.xml').decode('utf-8')
    return ' '.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', xml))


def test_writeoff_act_is_form_da3_with_requisites_from_settings(api):
    asset_id = new_asset(api, category='Оргтехника', acquired_on='2026-01-15')
    request_id = new_request(api, SALES_HEAD, 'writeoff', [asset_id], comment='Износ ОС')['request']['id']
    api.as_(IT).post('/requests/%d/check' % request_id, {'comment': 'Плата сгорела'})

    downloaded = api.as_(CHIEF).get('/requests/%d/act' % request_id)
    # Сайт и API на разных адресах: имя файла браузер отдаёт экрану, только если заголовок раскрыт.
    assert downloaded.headers['Access-Control-Expose-Headers'] == 'Content-Disposition'
    assert quote('Акт списания № %d.docx' % request_id) in downloaded.headers['Content-Disposition']
    bare = docx_text(downloaded)
    assert 'Форма № ДА-3' in bare and 'НА ВЫБЫТИЕ (СПИСАНИЕ) ДОЛГОСРОЧНЫХ АКТИВОВ' in bare
    assert 'Оборотная сторона формы ДА-3' in bare and 'Износ ОС' in bare and 'Плата сгорела' in bare
    assert 'Админов Д.' in bare and 'Продажина А.' in bare                        # проверивший и руководитель отдела
    assert '250 000,00' in bare                                                   # бухгалтерия видит стоимость

    saved = api.as_(CHIEF).put('/settings/requisites', {
        'org_name': 'ТОО «Образец»', 'org_bin': '123456789012', 'head_name': 'Директоров Дамир Маратович',
        'commission_order_number': '7', 'commission_order_date': '2026-11-02',
        'chairman_title': 'Генеральный директор', 'chairman_name': 'Директоров Дамир Маратович',
        'members': [{'title': 'Главный бухгалтер', 'name': 'Главбухова Гульмира'}, {'title': '', 'name': ''}],
        'chief_accountant_name': 'Главбухова Гульмира'})
    assert saved.status_code == 200 and saved.get_json()['requisites']['members'] == [
        {'title': 'Главный бухгалтер', 'name': 'Главбухова Гульмира'}]
    filled = docx_text(api.as_(CHIEF).get('/requests/%d/act' % request_id))
    for piece in ('ТОО «Образец»', '123456789012', 'Директоров Д. М.', 'приказом от 02.11.2026 года № 7',
                  'Генеральный директор', 'Главный бухгалтер', 'Главбухова Г.'):
        assert piece in filled, piece
    # Руководитель отдела стоимость в карточке не видит — и в акте графа пустая.
    assert '250 000,00' not in docx_text(api.as_(SALES_HEAD).get('/requests/%d/act' % request_id))

    assert api.as_(IT).put('/settings/requisites', {'org_name': 'x'}).status_code == 403
    assert api.as_(CHIEF).put('/settings/requisites', {'org_bin': '12345'}).get_json()['code'] == 'FA_BIN_INVALID'
    crowd = [{'title': 'Член', 'name': 'Человек %d' % number} for number in range(rules.MAX_COMMISSION_MEMBERS + 1)]
    assert api.as_(CHIEF).put('/settings/requisites', {'members': crowd}).status_code == 400
    assert api.as_(CHIEF).get('/settings').get_json()['requisites']['org_name'] == 'ТОО «Образец»'   # отказ ничего не стёр


# ─── «Новое ОС» ──────────────────────────────────────────────────────────────

def test_need_is_closed_by_taking_the_asset_on_the_books(api):
    cats = categories(api)
    need = new_request(api, IT, 'need', need_title='Ноутбук 15"', need_for_user_id=HOLDER2,
                       need_category_id=cats['Компьютеры и ноутбуки'], comment='Новый сотрудник')
    request_id = need['request']['id']
    assert (need['request']['status'], need['request']['review_by']) == ('review', 'accounting')
    new_asset(api, holder=HOLDER)
    assert new_request(api, HOLDER, 'need', need_title='Хочу монитор', need_for_user_id=HOLDER, comment='надо',
                       expect=403)['code'] == 'FA_KIND_FORBIDDEN'
    assert intake(api, user=ACCOUNTANT, holder=HOLDER2, source_request_id=request_id)['asset']['mol_user_id'] == HOLDER2
    closed = card(api, IT, request_id)
    assert closed['request']['status'] == 'closed'
    assert kinds(closed['events']) == ['submitted', 'need_accepted', 'closed']
    assert intake(api, source_request_id=request_id, expect=409)['code'] == 'FA_SOURCE_INVALID'


# ─── Увольнение ──────────────────────────────────────────────────────────────

def test_dismissal_is_locked_until_the_signed_act_is_attached(api, db):
    laptop, phone = new_asset(api), new_asset(api, category='Оргтехника')
    chair = new_asset(api, category='Мебель')
    writeoff_id = new_request(api, SALES_HEAD, 'writeoff', [chair], comment='Сломано')['request']['id']   # кресло занято

    assert api.as_(SALES_HEAD).get('/dismissals').status_code == 403
    assert api.as_(HOLDER).get('/dismissals/check', user_id=HOLDER).status_code == 403   # проверяет HR, не сам сотрудник
    assert api.as_(HR).get('/dismissals').get_json()['items'] == []                       # передач ещё нет
    before = api.as_(HR).get('/dismissals/check', user_id=HOLDER).get_json()
    assert before['order_locked'] and len(before['blockers']) == 3 and before['request'] is None

    same = new_request(api, HR, 'dismissal', [laptop, phone], from_user_id=HOLDER, to_user_id=HOLDER, expect=400)
    assert 'to_user_id' in same['fields']
    # HR не выбирает ОС: в передачу входят все свободные ОС сотрудника.
    sent = new_request(api, HR, 'dismissal', [laptop, phone], from_user_id=HOLDER, to_user_id=SALES_HEAD)
    request_id = sent['request']['id']
    assert sorted(item['asset_id'] for item in sent['items']) == [laptop, phone]
    assert sent['request']['status'] == 'signing'
    board = api.as_(HR).get('/dismissals').get_json()['items']
    assert [row['user']['id'] for row in board] == [HOLDER] and board[0]['order_locked']
    with db._get_cursor() as cursor:
        assert len(queries.dismissal_blockers(cursor, HOLDER)) == 3

    # Скан подписанного акта прикладывает HR — после этого приказ по этим ОС больше не держится.
    assert scan(api, HOLDER, request_id, expect=409)['code'] == 'FA_STEP_GONE'
    scan(api, HR, request_id)
    with db._get_cursor() as cursor:
        left = queries.dismissal_blockers(cursor, HOLDER)
    assert [item['id'] for item in left] == [chair]                               # держит только списание
    state = api.as_(HR).get('/dismissals/check', user_id=HOLDER).get_json()
    assert state['order_locked'] and len(state['assets']) == 3

    api.as_(SALES_HEAD).post('/requests/%d/check' % writeoff_id, {})
    scan(api, SALES_HEAD, writeoff_id)
    assert api.as_(HR).get('/dismissals/check', user_id=HOLDER).get_json()['order_locked'] is False

    receive(api, ACCOUNTANT, request_id)
    after = api.as_(HR).get('/dismissals/check', user_id=HOLDER).get_json()
    assert [asset['id'] for asset in after['assets']] == [chair]
    assert asset_card(api, CHIEF, laptop)['asset']['mol_user_id'] == SALES_HEAD


def test_employee_without_assets_has_nothing_to_hand_over(api):
    new_asset(api, holder=HOLDER2)
    refused = new_request(api, HR, 'dismissal', [], from_user_id=HOLDER, to_user_id=SALES_HEAD, expect=409)
    assert refused['code'] == 'FA_NOTHING_TO_HAND'


def test_employee_does_not_call_hr(api, db):
    """Решение владельца 17.11.2026: шага «сотрудник вызывает HR» нет — передачу запускает сам HR."""
    new_asset(api)
    assert api.as_(HOLDER).post('/dismissals/call', {}).status_code in (404, 405)
    assert api.as_(HOLDER).delete('/dismissals/call').status_code in (404, 405)
    mine = api.as_(HOLDER).get('/assets', scope='mine').get_json()
    assert 'call' not in mine and set(mine) == {'items', 'total', 'incoming'}
    with db._get_cursor() as cursor:
        cursor.execute("SELECT to_regclass('fa_hr_calls')")
        assert cursor.fetchone()[0] is None


# ─── Кто что видит ───────────────────────────────────────────────────────────

def test_everyone_sees_exactly_what_the_rules_allow(api, db):
    """Видимость в SQL (списки) совпадает с правилами access (карточки) — для всех людей и запросов."""
    laptop, chair = new_asset(api), new_asset(api, category='Мебель')
    stock = new_asset(api, holder=IT, to_stock=True)
    new_request(api, SALES_HEAD, 'transfer', [laptop], to_user_id=RECEIVER)
    new_request(api, SALES_HEAD, 'writeoff', [chair], comment='Сломано')
    new_request(api, IT, 'transfer', [stock], to_user_id=HOLDER2, submit=False)
    new_request(api, IT, 'need', need_title='Монитор', need_for_user_id=HOLDER2, comment='Нужен второй')
    with db._get_cursor() as cursor:
        cursor.execute('SELECT id FROM fa_requests ORDER BY id')
        request_ids = [row[0] for row in cursor.fetchall()]
        cursor.execute('SELECT id FROM fa_assets ORDER BY id')
        asset_ids = [row[0] for row in cursor.fetchall()]
        for user in EVERYONE:
            ctx = queries.load_context(cursor, user)
            if not access.can_open(ctx):
                continue
            _total, listed = queries.list_requests(cursor, ctx, limit=0)
            by_rule = {rid for rid in request_ids if access.can_view_request(ctx, queries.read_request(cursor, rid))}
            assert {item['id'] for item in listed} == by_rule, 'запросы пользователя %s' % user
            _total, assets = queries.list_assets(cursor, ctx, limit=0)
            visible = {aid for aid in asset_ids if access.asset_level(ctx, queries.read_asset(cursor, aid))}
            assert {item['id'] for item in assets} == visible, 'ОС пользователя %s' % user
    assert api.as_(OUTSIDER).get('/context').status_code == 403
    assert api.as_(ANALYST).get('/assets').status_code == 403
    assert api.as_(FIRED).get('/context').status_code == 403


def test_registry_and_card_levels(api):
    asset_id = new_asset(api)
    other = new_asset(api, holder=RECEIVER)
    assert api.as_(HOLDER).get('/assets').status_code == 403                      # сотруднику — «Мои ОС»
    mine = api.as_(HOLDER).get('/assets', scope='mine').get_json()
    assert [item['id'] for item in mine['items']] == [asset_id] and 'cost' not in mine['items'][0]
    assert api.as_(HOLDER).get('/assets/%d' % other).status_code == 404
    head = api.as_(SALES_HEAD).get('/assets').get_json()
    assert [item['id'] for item in head['items']] == [asset_id]                    # руководитель — ОС своего отдела
    # IT: «наличие на складе ОС и МОЛ» — реестр целиком, без стоимости, истории и актов.
    it_list = api.as_(IT).get('/assets').get_json()
    assert it_list['total'] == 2 and all('cost' not in item for item in it_list['items'])
    brief = asset_card(api, IT, asset_id)
    assert brief['level'] == 'brief' and 'events' not in brief and 'operations' not in brief
    full = asset_card(api, ANALYTICS_HEAD, asset_id)
    assert full['level'] == 'full' and full['asset']['cost'] == 250000.0 and full['can_request'] == []
    scoped = asset_card(api, SALES_HEAD, asset_id)
    assert scoped['level'] == 'scoped' and scoped['can_request'] == ['transfer', 'writeoff'] and 'cost' not in scoped['asset']
    assert asset_card(api, HOLDER, asset_id)['can_request'] == []                  # МОЛ запросов не заводит


def test_registry_slices_and_counters(api):
    used, stock = new_asset(api), new_asset(api, holder=IT, to_stock=True)
    new_request(api, SALES_HEAD, 'transfer', [used], to_user_id=RECEIVER)
    data = api.as_(CHIEF).get('/assets').get_json()
    assert data['counters'] == {'all': 2, 'in_use': 1, 'stock': 1, 'busy': 1, 'written_off': 0}
    assert [item['id'] for item in api.as_(CHIEF).get('/assets', slice='stock').get_json()['items']] == [stock]
    assert [item['id'] for item in api.as_(CHIEF).get('/assets', q='ос-').get_json()['items']] == [stock, used]
    picked = api.as_(IT).get('/assets', pick='transfer').get_json()
    assert [item['id'] for item in picked['items']] == [stock]                     # занятое запросом не предлагается


def test_waiting_list_follows_the_next_step(api):
    asset_id = new_asset(api, category='Оргтехника')
    request_id = new_request(api, SALES_HEAD, 'writeoff', [asset_id], comment='Сломан')['request']['id']

    def waiting(user):
        return [item['id'] for item in api.as_(user).get('/requests', tile='waiting').get_json()['items']
                if item['kind'] == 'writeoff']

    assert waiting(IT) == [request_id] and waiting(SALES_HEAD) == [] and waiting(ACCOUNTANT) == []
    api.as_(IT).post('/requests/%d/check' % request_id, {})
    assert waiting(IT) == [] and waiting(SALES_HEAD) == [request_id] and waiting(HOLDER) == []
    scan(api, SALES_HEAD, request_id)
    assert waiting(SALES_HEAD) == [] and waiting(ACCOUNTANT) == [request_id]
    receive(api, ACCOUNTANT, request_id)
    assert waiting(ACCOUNTANT) == [] and api.as_(ACCOUNTANT).get('/context').get_json()['waiting'] == 1  # акт приёма


def test_request_tiles_count_and_filter(api):
    first, second, third = new_asset(api), new_asset(api), new_asset(api)
    closed = new_request(api, SALES_HEAD, 'transfer', [first], to_user_id=RECEIVER)['request']['id']
    scan(api, SALES_HEAD, closed)
    receive(api, ACCOUNTANT, closed)
    rejected = new_request(api, SALES_HEAD, 'transfer', [second], to_user_id=RECEIVER)['request']['id']
    api.as_(CHIEF).post('/requests/%d/reject' % rejected, {'reason': 'Не тот получатель'})
    new_request(api, SALES_HEAD, 'writeoff', [third], comment='Сломан')
    data = api.as_(ACCOUNTANT).get('/requests').get_json()
    # «В работе» — три акта приёма на подписании и списание на проверке; акты приёма ждут их автора.
    assert data['counters'] == {'all': 6, 'waiting': 3, 'open': 4, 'closed': 1, 'rejected': 1}
    assert [item['id'] for item in api.as_(CHIEF).get('/requests', tile='closed').get_json()['items']] == [closed]
    assert [item['id'] for item in api.as_(CHIEF).get('/requests', tile='rejected').get_json()['items']] == [rejected]
    opened = api.as_(CHIEF).get('/requests', tile='open').get_json()
    assert opened['total'] == 4 and {item['status'] for item in opened['items']} == {'signing', 'review'}
    assert api.as_(CHIEF).get('/requests', tile='open', kind='writeoff').get_json()['total'] == 1
    assert api.as_(CHIEF).get('/requests', tile='nonsense').status_code == 400


MOVE_STEPS = {'submit', 'check', 'accept', 'scan', 'receive', 'reject'}


def test_request_board_columns_by_status(api):
    """Доска запросов: колонка на каждый статус, в колонке — только этот статус, по тем же правам и
    отбору, что список; «Ждут меня» — только ждущие человека; «Посмотреть ещё» — страница колонки."""
    first, second, third, fourth, fifth = (new_asset(api) for _ in range(5))
    closed = new_request(api, SALES_HEAD, 'transfer', [first], to_user_id=RECEIVER)['request']['id']
    scan(api, SALES_HEAD, closed)
    receive(api, ACCOUNTANT, closed)
    rejected = new_request(api, SALES_HEAD, 'transfer', [second], to_user_id=RECEIVER)['request']['id']
    api.as_(CHIEF).post('/requests/%d/reject' % rejected, {'reason': 'Не тот получатель'})
    review = new_request(api, SALES_HEAD, 'writeoff', [third], comment='Сломан')['request']['id']
    original = new_request(api, SALES_HEAD, 'transfer', [fourth], to_user_id=RECEIVER)['request']['id']
    scan(api, SALES_HEAD, original)
    draft = new_request(api, SALES_HEAD, 'transfer', [fifth], to_user_id=RECEIVER, submit=False)['request']['id']

    def board(user, **params):
        response = api.as_(user).get('/requests/board', **params)
        assert response.status_code == 200, response.get_json()
        data = response.get_json()
        return data, {column['status']: column for column in data['columns']}

    data, columns = board(CHIEF)
    assert [column['status'] for column in data['columns']] == list(rules.STATUSES)
    # Пять актов приёма на подписании; чужой черновик бухгалтерии не виден.
    assert {status: column['total'] for status, column in columns.items()} == {
        'draft': 0, 'review': 1, 'signing': 5, 'original': 1, 'closed': 1, 'rejected': 1}
    for status, column in columns.items():
        assert all(card['status'] == status for card in column['items'])
        assert len(column['items']) == column['total']
    signing = columns['signing']['items']
    assert [card['since'] for card in signing] == sorted((card['since'] for card in signing), reverse=True)
    closed_card = columns['closed']['items'][0]
    assert closed_card['id'] == closed and closed_card['cover_url']
    # Дата карточки — когда запрос пришёл в свою колонку: закрытый — по закрытию, отклонённый — по отказу.
    assert closed_card['since'] == closed_card['closed_at']
    assert columns['rejected']['items'][0]['since'] == columns['rejected']['items'][0]['rejected_at']
    # Перетащить можно туда, куда есть шаг: бухгалтерия получает оригинал и отклоняет.
    assert {'receive', 'reject'} <= set(columns['original']['items'][0]['actions'])
    assert not set(closed_card['actions']) & MOVE_STEPS

    _data, mine = board(SALES_HEAD)
    assert [card['id'] for card in mine['draft']['items']] == [draft]
    assert 'submit' in mine['draft']['items'][0]['actions']

    # «Ждут меня»: у бухгалтера — свои акты приёма на подписании и оригинал; закрытых там нет.
    waiting, waiting_columns = board(ACCOUNTANT, waiting=1)
    assert {status: column['total'] for status, column in waiting_columns.items()} == {
        'draft': 0, 'review': 0, 'signing': 5, 'original': 1, 'closed': 0, 'rejected': 0}
    assert waiting['counters']['waiting'] == 6
    assert all(card['waiting'] for column in waiting['columns'] for card in column['items'])

    _data, by_kind = board(CHIEF, kind='writeoff')
    assert {status: column['total'] for status, column in by_kind.items() if column['total']} == {'review': 1}
    _data, by_number = board(CHIEF, q='№%d' % rejected)          # «№ 7» — поиск по номеру, а не по «7» в названиях
    assert [card['id'] for column in by_number.values() for card in column['items']] == [rejected]

    # «Посмотреть ещё»: следующая страница одной колонки, без повторов.
    page_one, _ = board(CHIEF, status='signing', limit=2)
    page_two, _ = board(CHIEF, status='signing', limit=2, offset=2)
    assert [column['status'] for column in page_one['columns']] == ['signing']
    ids_one = [card['id'] for card in page_one['columns'][0]['items']]
    ids_two = [card['id'] for card in page_two['columns'][0]['items']]
    assert page_one['columns'][0]['total'] == 5 and len(ids_one) == len(ids_two) == 2
    assert not set(ids_one) & set(ids_two)
    assert ids_one + ids_two == [card['id'] for card in signing][:4]

    # Сотрудник видит запросы по своим ОС, но двигать их не может: шагов у него нет.
    _data, holder = board(HOLDER)
    seen = {card['id'] for column in holder.values() for card in column['items']}
    assert {closed, rejected, review, original} <= seen and draft not in seen
    assert not any(set(card['actions']) & MOVE_STEPS for column in holder.values() for card in column['items'])

    assert api.as_(CHIEF).get('/requests/board', status='nonsense').status_code == 400
    assert api.as_(CHIEF).get('/requests/board', kind='nonsense').status_code == 400


# ─── История ─────────────────────────────────────────────────────────────────

def test_history_cannot_be_edited_or_deleted(api, db):
    import psycopg2

    new_asset(api)
    for statement in ("UPDATE fa_events SET actor_name = 'Другой'", 'DELETE FROM fa_events', 'TRUNCATE fa_events'):
        with pytest.raises(psycopg2.Error) as caught:
            with db._get_cursor() as cursor:
                cursor.execute(statement)
        assert 'не редактируется и не удаляется' in str(caught.value)
    with db._get_cursor() as cursor:
        cursor.execute('SELECT COUNT(*), MIN(actor_name) FROM fa_events')
        assert cursor.fetchone() == (1, 'Бухгалтерова Асель')


def test_history_shows_everyone_their_own_part(api):
    """ТЗ: бухгалтерия, HR и руководитель аналитики видят всё. Остальные — записи, в которых
    участвуют сами: МОЛ видит историю своего участия, а не всю жизнь ОС."""
    first, second = new_asset(api), new_asset(api, holder=HOLDER2)
    assert kinds(asset_card(api, HOLDER, first)['events']) == ['registered']
    request_id = new_request(api, SALES_HEAD, 'transfer', [first], to_user_id=RECEIVER)['request']['id']
    scan(api, SALES_HEAD, request_id)
    receive(api, ACCOUNTANT, request_id)
    transfer = {'submitted', 'scan_attached', 'original_received', 'moved', 'closed'}

    def history(user, **params):
        response = api.as_(user).get('/history', **params)
        assert response.status_code == 200, response.get_json()
        return response.get_json()

    for user in (CHIEF, HR, ANALYTICS_HEAD, SUPER):
        seen = history(user)
        assert (seen['total'], seen['scope']) == (7, 'all')
    was_holder = history(HOLDER)                       # принял в учёт и сдал — это его история
    assert (was_holder['total'], was_holder['scope']) == (6, 'own')
    assert set(kinds(was_holder['items'])) == transfer | {'registered'}
    assert kinds(history(HOLDER2)['items']) == ['registered']                     # только своё ОС
    assert {event['asset_id'] for event in history(HOLDER2)['items']} == {second}
    assert set(kinds(history(RECEIVER)['items'])) == transfer                     # принял — приём прежнего МОЛ не его
    assert history(SALES_HEAD)['total'] == 7                                      # ОС и запросы своего отдела
    assert history(MARKETING_HEAD)['total'] == 5                                  # только перемещение в его отдел
    assert history(IT)['total'] == 0                                              # не участвовал ни в чём

    # Карточка ОС у нового МОЛ: история с момента, когда он в ней появился.
    now_his = asset_card(api, RECEIVER, first)
    assert now_his['level'] == 'own' and set(kinds(now_his['events'])) == transfer
    assert [operation['request']['kind'] for operation in now_his['operations']] == ['transfer']
    assert set(kinds(asset_card(api, CHIEF, first)['events'])) == transfer | {'registered'}
    # У прежнего МОЛ запись о передаче осталась, а само ОС ему больше не открывается.
    assert api.as_(HOLDER).get('/assets/%d' % first).status_code == 404
    moved = next(event for event in was_holder['items'] if event['kind'] == 'moved')
    assert moved['asset_open'] is False
    assert next(event for event in history(RECEIVER)['items'] if event['kind'] == 'moved')['asset_open'] is True

    only = history(CHIEF, kind='submitted')
    assert kinds(only['items']) == ['submitted'] and only['items'][0]['items_title']
    assert history(CHIEF, q='Маркетологова')['total'] == 5                        # перемещение к ней
    assert history(CHIEF, q='Менеджерова')['total'] == 1                          # приём второго ОС
    assert api.as_(CHIEF).get('/history', kind='nonsense').status_code == 400
    assert api.as_(CHIEF).get('/history', kind='hr_called').status_code == 400    # такого события больше нет


def test_scan_is_opened_only_by_those_who_see_the_request(api):
    asset_id = new_asset(api)
    request_id = new_request(api, SALES_HEAD, 'transfer', [asset_id], to_user_id=RECEIVER)['request']['id']
    file_id = scan(api, SALES_HEAD, request_id)['request']['act']['scan']['id']
    opened = api.as_(RECEIVER).get('/files/%d' % file_id)
    assert opened.status_code == 200 and opened.data == PDF and opened.mimetype == 'application/pdf'
    assert opened.headers['Content-Disposition'].startswith('inline')              # открывается, а не скачивается
    assert api.as_(HOLDER2).get('/files/%d' % file_id).status_code == 403          # раздел ему не открыт
    assert api.as_(MARKETING_HEAD).get('/files/%d' % file_id).status_code == 200   # глава принимающего отдела


def test_wrong_scan_is_replaced_and_the_old_one_stays_in_history(api):
    asset_id = new_asset(api)
    request_id = new_request(api, SALES_HEAD, 'transfer', [asset_id], to_user_id=RECEIVER)['request']['id']
    first = scan(api, SALES_HEAD, request_id, name='не тот.pdf')['request']['act']['scan']['id']
    assert scan(api, MARKETING_HEAD, request_id, name='другой.pdf', expect=409)['code'] == 'FA_STEP_GONE'   # не он прикладывал
    replaced = scan(api, SALES_HEAD, request_id, name='верный.pdf')
    assert replaced['request']['status'] == 'original'
    assert replaced['request']['act']['scan']['file_name'] == 'верный.pdf'
    events = replaced['events']
    assert kinds(events) == ['submitted', 'scan_attached', 'scan_replaced']
    assert events[-1]['payload']['replaced'] == {'id': first, 'name': 'не тот.pdf'}
    assert api.as_(CHIEF).get('/files/%d' % first).status_code == 200                # прежний файл не стёрт
    scan(api, ACCOUNTANT, request_id, name='от бухгалтерии.pdf')                     # бухгалтерия тоже может
    receive(api, ACCOUNTANT, request_id)
    assert scan(api, ACCOUNTANT, request_id, expect=409)['code'] == 'FA_STEP_GONE'   # закрытый запрос не трогают


def test_export_is_for_those_who_see_everything(api):
    new_asset(api)
    book = api.as_(CHIEF).get('/export')
    assert book.status_code == 200 and book.data.startswith(b'PK')
    assert book.headers['Access-Control-Expose-Headers'] == 'Content-Disposition'
    assert api.as_(IT).get('/export').status_code == 403


# ─── Настройки и пилот ───────────────────────────────────────────────────────

def test_categories_are_kept_by_accounting(api):
    assert api.as_(IT).get('/settings').status_code == 403
    created = api.as_(CHIEF).post('/settings/categories', {'name': 'Автомобили', 'check_by': 'head'})
    assert created.status_code == 201
    names = {item['name']: item for item in created.get_json()['categories']}
    assert names['Автомобили']['check_by'] == 'head' and len(names) == 5
    assert api.as_(CHIEF).post('/settings/categories', {'name': 'автомобили', 'check_by': 'it'}).status_code == 409
    assert api.as_(CHIEF).post('/settings/categories', {'name': 'Без проверки', 'check_by': 'nobody'}).status_code == 400
    hidden = api.as_(CHIEF).put('/settings/categories/%d' % names['Автомобили']['id'],
                                {'name': 'Автомобили', 'check_by': 'it', 'is_active': False})
    assert hidden.status_code == 200
    assert 'Автомобили' not in categories(api)                                      # скрытая не предлагается


def test_named_members_are_added_by_super_admin(api):
    assert api.as_(CHIEF).post('/settings/members', {'user_id': OUTSIDER, 'role': 'it'}).status_code == 403
    assert api.as_(OUTSIDER).get('/context').status_code == 403
    added = api.as_(SUPER).post('/settings/members', {'user_id': OUTSIDER, 'role': 'it'})
    assert added.status_code == 201 and added.get_json()['members'][0]['name'] == 'Посторонний Петр'
    context = api.as_(OUTSIDER).get('/context').get_json()
    assert context['capabilities']['tabs'] == ['registry', 'requests', 'history']
    assert 'need' in context['capabilities']['create_kinds']
    assert api.as_(SUPER).delete('/settings/members', user_id=OUTSIDER, role='it').status_code == 200
    assert api.as_(OUTSIDER).get('/context').status_code == 403


def test_pilot_keeps_the_section_for_super_admins(api, db, monkeypatch):
    new_asset(api)
    monkeypatch.setattr(access, 'PILOT_SUPER_ADMIN_ONLY', True)
    assert api.as_(SUPER).get('/assets').status_code == 200
    for user in (CHIEF, HR, IT, HOLDER):
        assert api.as_(user).get('/context').status_code == 403
    with db._get_cursor() as cursor:
        assert queries.section_open_for(cursor, SUPER) is True
        assert queries.section_open_for(cursor, CHIEF) is False
