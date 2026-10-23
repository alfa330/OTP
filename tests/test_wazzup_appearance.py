"""Обои «Чатов ОП» (wazzup/appearance.py): кто что может, что попадает в хранилище.

Только синтетика: картинки рисуются здесь же, хранилище и база — подделки в
памяти. Настоящий SQL проверяет tests/test_wazzup_appearance_persistence.py.
"""
import io
import json
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import Flask, g, request
from PIL import Image

from wazzup import appearance

ROOT = Path(__file__).resolve().parents[1]
PATH = '/api/wazzup/appearance'
OWNER, OTHER, ADMIN, CLOSED = 7, 8, 9, 10
# Учётка «только стандартный вид» — выдуманная: тест проверяет правило, а не
# сегодняшний состав списка (его равенство в двух местах сверяет последний тест).
LIMITED = 4242


class Cursor:
    def __init__(self, db):
        self.db, self.row, self.rows = db, None, []

    def execute(self, query, values=()):
        sql = ' '.join(query.split())
        self.db.statements.append(sql)
        self.row, self.rows = None, []
        rows = self.db.rows
        if sql.startswith('SELECT pg_advisory_xact_lock'):
            return
        if sql.startswith('SELECT wallpaper_id, width, height, settings, updated_at'):
            item = rows.get(values[0])
            self.row = (item['wallpaper_id'], item['width'], item['height'], item['settings'],
                        item['updated_at']) if item else None
        elif sql.startswith('SELECT bucket, blob_path, thumb_path FROM wazzup_chat_wallpapers WHERE user_id'):
            item = rows.get(values[0])
            self.row = (item['bucket'], item['blob_path'], item['thumb_path']) if item else None
        elif sql.startswith('SELECT user_id, bucket, blob_path, thumb_path'):
            item = next((v for v in rows.values() if v['wallpaper_id'] == values[0]), None)
            self.row = (item['user_id'], item['bucket'], item['blob_path'], item['thumb_path']) if item else None
        elif sql.startswith('INSERT INTO wazzup_chat_wallpapers'):
            (user_id, wallpaper_id, bucket, blob, thumb, width, height, size, settings) = values
            self.db.clock += timedelta(seconds=1)
            rows[user_id] = dict(user_id=user_id, wallpaper_id=wallpaper_id, bucket=bucket, blob_path=blob,
                                 thumb_path=thumb, width=width, height=height, file_size=size,
                                 settings=json.loads(settings), updated_at=self.db.clock)
        elif sql.startswith('UPDATE wazzup_chat_wallpapers SET settings'):
            settings, user_id, wallpaper_id = values
            item = rows.get(user_id)
            if item and item['wallpaper_id'] == wallpaper_id:
                self.db.clock += timedelta(seconds=1)
                item.update(settings=json.loads(settings), updated_at=self.db.clock)
                self.row = (1,)
        elif sql.startswith('DELETE FROM wazzup_chat_wallpapers'):
            item = rows.pop(values[0], None)
            self.row = (item['bucket'], item['blob_path'], item['thumb_path']) if item else None
        elif sql.startswith('SELECT w.user_id, u.name, d.name'):
            ordered = sorted(rows.values(), key=lambda v: (-v['updated_at'].timestamp(), v['user_id']))
            self.rows = [(v['user_id'], f'Сотрудник {v["user_id"]}', 'Отдел продаж', v['wallpaper_id'],
                          v['width'], v['height'], v['settings'], v['updated_at']) for v in ordered][:values[0]]
        else:
            raise AssertionError('Unexpected SQL: ' + sql)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class Database:
    def __init__(self):
        self.rows, self.statements = {}, []
        self.clock = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
        self.local = threading.local()

    @contextmanager
    def _get_cursor(self):
        self.local.transaction = True
        try:
            yield Cursor(self)
        finally:
            self.local.transaction = False


class Storage:
    def __init__(self, db, fail_upload=None):
        self.db, self.objects, self.deleted = db, {}, []
        self.fail_upload = fail_upload

    @property
    def config(self):
        return {'bucket_name': lambda: 'synthetic-bucket', 'client': lambda: self}

    def bucket(self, name):
        assert not getattr(self.db.local, 'transaction', False), 'storage I/O inside a DB transaction'
        assert name == 'synthetic-bucket'
        return self

    def blob(self, path):
        storage = self

        class Blob:
            def upload_from_string(self, data, **kwargs):
                assert kwargs['if_generation_match'] == 0 and kwargs['content_type'] == 'image/webp'
                if storage.fail_upload and storage.fail_upload(path):
                    raise ConnectionError('synthetic outage')
                storage.objects[path] = data

            def download_as_bytes(self, **kwargs):
                if path not in storage.objects:
                    error = Exception('gone')
                    error.code = 404
                    raise error
                return storage.objects[path]

            def delete(self, **kwargs):
                storage.deleted.append(path)
                storage.objects.pop(path, None)
        return Blob()


def build(db=None, storage=None, closed=(CLOSED,), admins=(ADMIN,)):
    db = db or Database()
    storage = storage or Storage(db)
    app = Flask(__name__)

    def require_api_key(view):
        def wrapped(*args, **kwargs):
            g.user_id = int(request.headers['X-Test-User'])
            return view(*args, **kwargs)
        wrapped.__name__ = view.__name__
        return wrapped

    def chat_access():
        mode = None if g.user_id in closed else 'operator'
        return {'user_id': g.user_id, 'mode': mode}

    @app.after_request
    def stamp(response):
        # Как монолит: на /api/ без разрешения кэша — no-store.
        if not getattr(g, 'allow_public_cache', False):
            response.headers['Cache-Control'] = 'no-store'
        return response

    app.register_blueprint(appearance.build_appearance_blueprint(
        db=db, require_api_key=require_api_key, preflight=lambda: ('', 204),
        chat_access=chat_access, is_admin=lambda user_id: user_id in admins, gcs=storage.config))
    return app.test_client(), db, storage


@pytest.fixture(autouse=True)
def fresh_limits():
    appearance._rate.__globals__['_usage'].clear()


def picture(kind='JPEG', size=(1200, 800), color=(30, 120, 200), **save):
    mode = 'RGBA' if kind == 'PNG' and save.pop('alpha', False) else 'RGB'
    image = Image.new(mode, size, color + ((128,) if mode == 'RGBA' else ()))
    out = io.BytesIO()
    image.save(out, kind, **save)
    return out.getvalue()


def as_user(user_id):
    return {'X-Test-User': str(user_id)}


def upload(client, user_id, data, settings=None, name='wall.jpg'):
    form = {'file': (io.BytesIO(data), name)}
    if settings is not None:
        form['settings'] = json.dumps(settings)
    return client.post(PATH + '/wallpaper', data=form, headers=as_user(user_id),
                       content_type='multipart/form-data')


def test_a_new_person_has_no_wallpaper_and_may_upload():
    client, _, _ = build()
    answer = client.get(PATH, headers=as_user(OWNER))
    assert answer.status_code == 200
    assert answer.get_json() == {'wallpaper': None, 'settings': appearance.DEFAULT_SETTINGS, 'mayUpload': True}
    assert answer.headers['Cache-Control'] == 'no-store'


def test_upload_rewrites_the_picture_without_metadata_and_keeps_the_view_settings():
    client, db, storage = build()
    exif = Image.Exif()
    exif[0x0112] = 6  # снимок с телефона боком: поворот записан в EXIF
    exif[0x8825] = {2: (43.0, 15.0, 0.0), 4: (76.0, 54.0, 0.0)}  # место съёмки
    original = picture('JPEG', size=(3000, 1000), exif=exif.tobytes())
    settings = {'brightness': -20, 'blur': 6, 'saturation': 80, 'x': 30.25, 'y': 70}
    answer = upload(client, OWNER, original, settings)
    assert answer.status_code == 201, answer.get_json()
    state = answer.get_json()
    assert state['settings'] == {'brightness': -20, 'blur': 6, 'saturation': 80, 'x': 30.2, 'y': 70.0}
    wallpaper = state['wallpaper']
    # Повёрнут по EXIF (портрет), длинная сторона ужата до 2560.
    assert (wallpaper['width'], wallpaper['height']) == (853, 2560)
    full = storage.objects[db.rows[OWNER]['blob_path']]
    thumb = storage.objects[db.rows[OWNER]['thumb_path']]
    for data, longest in ((full, 2560), (thumb, 640)):
        with Image.open(io.BytesIO(data)) as stored:
            assert stored.format == 'WEBP'
            assert max(stored.size) == longest
            assert not stored.getexif(), 'EXIF (и место съёмки) не должно доехать до хранилища'
            assert 'exif' not in stored.info and 'xmp' not in stored.info
    assert all(path.startswith(f'wazzup/appearance/{OWNER}/') for path in storage.objects)
    assert client.get(PATH, headers=as_user(OWNER)).get_json() == state


def test_png_with_transparency_and_webp_are_accepted():
    client, db, storage = build()
    assert upload(client, OWNER, picture('PNG', alpha=True), name='wall.png').status_code == 201
    with Image.open(io.BytesIO(storage.objects[db.rows[OWNER]['blob_path']])) as stored:
        assert stored.mode == 'RGBA'
    assert upload(client, OWNER, picture('WEBP'), name='wall.webp').status_code == 201


@pytest.mark.parametrize('data,status', [
    (b'%PDF-1.4\nnot a picture\n%%EOF', 415),
    (picture('JPEG')[:400], 415),
    (picture('GIF'), 415),
    (b'', 400),
])
def test_what_is_not_a_picture_is_refused_and_nothing_is_stored(data, status):
    client, db, storage = build()
    answer = upload(client, OWNER, data)
    assert answer.status_code == status, answer.get_json()
    assert answer.get_json()['error']
    assert db.rows == {} and storage.objects == {}


def test_a_giant_picture_is_refused_before_decoding():
    client, db, storage = build()
    image = Image.new('1', (8000, 6000))
    out = io.BytesIO()
    image.save(out, 'PNG')
    answer = upload(client, OWNER, out.getvalue(), name='huge.png')
    assert answer.status_code == 413
    assert db.rows == {} and storage.objects == {}


@pytest.mark.parametrize('settings', [
    {'brightness': 61}, {'blur': -1}, {'saturation': 201}, {'x': 101}, {'y': 'top'},
    {'blur': True}, {'brightness': float('nan')}, ['brightness'],
])
def test_settings_out_of_bounds_are_refused(settings):
    client, db, storage = build()
    answer = upload(client, OWNER, picture(), settings)
    assert answer.status_code == 400
    assert db.rows == {} and storage.objects == {}


def test_only_the_owner_and_admins_see_the_picture_with_a_long_private_cache():
    client, db, storage = build()
    wallpaper = upload(client, OWNER, picture()).get_json()['wallpaper']
    url = PATH + '/wallpaper/' + wallpaper['id']
    own = client.get(url, headers=as_user(OWNER))
    assert own.status_code == 200 and own.mimetype == 'image/webp'
    assert own.data == storage.objects[db.rows[OWNER]['blob_path']]
    assert own.headers['Cache-Control'] == 'private, max-age=31536000, immutable'
    thumb = client.get(url + '?size=thumb', headers=as_user(ADMIN))
    assert thumb.status_code == 200 and thumb.data == storage.objects[db.rows[OWNER]['thumb_path']]
    for stranger in (OTHER, CLOSED):
        assert client.get(url, headers=as_user(stranger)).status_code in (403, 404)
    assert client.get(PATH + '/wallpaper/not-an-id', headers=as_user(OWNER)).status_code == 404


def test_replacing_and_removing_leave_no_old_files_behind():
    client, db, storage = build()
    first = upload(client, OWNER, picture(color=(1, 2, 3))).get_json()['wallpaper']
    first_paths = (db.rows[OWNER]['blob_path'], db.rows[OWNER]['thumb_path'])
    second = upload(client, OWNER, picture(color=(200, 10, 10))).get_json()['wallpaper']
    assert second['id'] != first['id']
    assert set(storage.deleted) == set(first_paths)
    assert set(storage.objects) == {db.rows[OWNER]['blob_path'], db.rows[OWNER]['thumb_path']}
    assert client.get(PATH + '/wallpaper/' + first['id'], headers=as_user(OWNER)).status_code == 404
    removed = client.delete(PATH + '/wallpaper', headers=as_user(OWNER))
    assert removed.status_code == 200 and removed.get_json()['wallpaper'] is None
    assert storage.objects == {} and db.rows == {}
    assert client.delete(PATH + '/wallpaper', headers=as_user(OWNER)).status_code == 200, 'повтор безвреден'


def test_settings_change_only_for_the_wallpaper_they_were_made_for():
    client, db, _ = build()
    first = upload(client, OWNER, picture()).get_json()['wallpaper']
    changed = client.put(PATH, json={'wallpaperId': first['id'], 'settings': {'blur': 12, 'x': 10}},
                         headers=as_user(OWNER))
    assert changed.status_code == 200
    assert changed.get_json()['settings'] == {**appearance.DEFAULT_SETTINGS, 'blur': 12, 'x': 10.0}
    second = upload(client, OWNER, picture(color=(9, 9, 9))).get_json()['wallpaper']
    stale = client.put(PATH, json={'wallpaperId': first['id'], 'settings': {'blur': 20}},
                       headers=as_user(OWNER))
    assert stale.status_code == 409
    assert stale.get_json()['wallpaper']['id'] == second['id']
    assert db.rows[OWNER]['settings']['blur'] == 0
    assert client.put(PATH, json={'wallpaperId': second['id'], 'settings': {'blur': 21}},
                      headers=as_user(OWNER)).status_code == 400


def test_a_standard_only_account_has_no_wallpaper_at_all(monkeypatch):
    monkeypatch.setattr(appearance, 'STANDARD_ONLY', frozenset({LIMITED}))
    client, db, storage = build()
    state = client.get(PATH, headers=as_user(LIMITED)).get_json()
    assert state == {'wallpaper': None, 'settings': appearance.DEFAULT_SETTINGS, 'mayUpload': False}
    assert upload(client, LIMITED, picture()).status_code == 403
    assert storage.objects == {} and db.rows == {}
    # Обои, сохранённые раньше (до попадания в список), не отдаются никому.
    upload(client, OWNER, picture())
    db.rows[LIMITED] = dict(db.rows.pop(OWNER), user_id=LIMITED)
    wallpaper_id = db.rows[LIMITED]['wallpaper_id']
    assert client.get(PATH, headers=as_user(LIMITED)).get_json()['wallpaper'] is None
    assert client.put(PATH, json={'wallpaperId': wallpaper_id, 'settings': {}},
                      headers=as_user(LIMITED)).status_code == 403
    for viewer in (LIMITED, ADMIN):
        assert client.get(PATH + '/wallpaper/' + wallpaper_id, headers=as_user(viewer)).status_code == 404
    assert client.get(PATH + '/wallpapers', headers=as_user(ADMIN)).get_json()['items'] == []


def test_a_closed_section_answers_nothing():
    client, _, _ = build()
    for method, url in (('get', PATH), ('put', PATH), ('post', PATH + '/wallpaper'),
                        ('delete', PATH + '/wallpaper'), ('get', PATH + '/wallpapers')):
        assert getattr(client, method)(url, headers=as_user(CLOSED)).status_code == 403, (method, url)


def test_admins_see_who_has_which_wallpaper_newest_first():
    client, _, _ = build()
    upload(client, OWNER, picture(), {'brightness': 30})
    upload(client, OTHER, picture(color=(5, 5, 5)))
    assert client.get(PATH + '/wallpapers', headers=as_user(OWNER)).status_code == 403
    listing = client.get(PATH + '/wallpapers', headers=as_user(ADMIN))
    assert listing.status_code == 200 and listing.headers['Cache-Control'] == 'no-store'
    items = listing.get_json()['items']
    assert [item['userId'] for item in items] == [OTHER, OWNER]
    assert items[1]['settings']['brightness'] == 30
    assert items[1]['name'] and items[1]['department']
    assert set(items[1]['wallpaper']) == {'id', 'width', 'height', 'updatedAt'}


def test_a_storage_outage_stores_nothing_and_cleans_up_the_half_written_upload():
    db = Database()
    storage = Storage(db, fail_upload=lambda path: path.endswith('.thumb.webp'))
    client, _, _ = build(db, storage)
    answer = upload(client, OWNER, picture())
    assert answer.status_code == 503
    assert db.rows == {} and storage.objects == {}
    assert len(storage.deleted) == 1 and storage.deleted[0].endswith('.webp')


def test_requests_without_one_picture_are_refused():
    client, _, _ = build()
    assert client.post(PATH + '/wallpaper', json={}, headers=as_user(OWNER)).status_code == 400
    two = {'file': [(io.BytesIO(picture()), 'a.jpg'), (io.BytesIO(picture()), 'b.jpg')]}
    assert client.post(PATH + '/wallpaper', data=two, headers=as_user(OWNER),
                       content_type='multipart/form-data').status_code == 400
    assert client.put(PATH, data='nope', headers=as_user(OWNER)).status_code == 400


def test_stored_settings_fill_gaps_with_defaults():
    assert appearance.stored_settings('{"blur": 4, "x": 999}') == {**appearance.DEFAULT_SETTINGS, 'blur': 4}
    assert appearance.stored_settings(None) == appearance.DEFAULT_SETTINGS
    assert appearance.stored_settings('not json') == appearance.DEFAULT_SETTINGS


def test_the_standard_only_list_is_the_same_on_the_server_and_in_the_browser():
    source = (ROOT / 'src/components/wazzup/chatThemes.js').read_text(encoding='utf-8')
    found = re.search(r'const STANDARD_ONLY = Object\.freeze\(\[([^\]]*)\]\)', source)
    assert found, 'STANDARD_ONLY в chatThemes.js не найден'
    browser = {int(item) for item in found.group(1).split(',') if item.strip()}
    assert browser == set(appearance.STANDARD_ONLY)
