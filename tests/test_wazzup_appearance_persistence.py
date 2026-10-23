"""Обои «Чатов ОП» на настоящем SQL: схема, замена, удаление, список у админа.

Нужен одноразовый локальный Postgres (WAZZUP_PILOT_TEST_PORT), как у соседних
тестов «Чатов ОП»; каждый тест живёт в своей схеме и удаляет только её.
"""
import json
import os
import threading
import uuid
from contextlib import contextmanager

import pytest

from tests.test_wazzup_appearance import (ADMIN, OTHER, OWNER, PATH, Database, Storage, as_user, build,
                                          picture, upload)
from wazzup import appearance
from wazzup.appearance_schema import init_schema

PORT = os.environ.get('WAZZUP_PILOT_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')


@pytest.fixture(autouse=True)
def fresh_limits():
    appearance._rate.__globals__['_usage'].clear()


@pytest.fixture
def pg():
    import psycopg2
    schema = 't_wazzup_appearance_' + uuid.uuid4().hex
    connection = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                  connect_timeout=3)
    with connection.cursor() as cursor:
        cursor.execute('CREATE SCHEMA ' + schema)
        cursor.execute('SET search_path TO ' + schema)
        cursor.execute("SET statement_timeout TO '5s'")
        cursor.execute("""
            CREATE TABLE departments (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, department_id INTEGER);
            INSERT INTO departments VALUES (1, 'Отдел продаж');
            INSERT INTO users VALUES (%s, 'Иванова Айгерим', 1), (%s, 'Карабек Ержан', NULL);
        """, (OWNER, OTHER))
        init_schema(cursor)
        init_schema(cursor)  # повтор при каждом старте безвреден
    connection.commit()
    try:
        yield connection
    finally:
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute('DROP SCHEMA ' + schema + ' CASCADE')
        connection.commit()
        connection.close()


def database(connection):
    db = Database()
    lock = threading.Lock()

    @contextmanager
    def cursor():
        with lock:
            db.local.transaction = True
            try:
                with connection:
                    with connection.cursor() as cur:
                        yield cur
            finally:
                db.local.transaction = False
    db._get_cursor = cursor
    return db


def rows(connection):
    with connection.cursor() as cursor:
        cursor.execute('SELECT user_id, wallpaper_id::text, blob_path, thumb_path, width, height, '
                       'file_size, settings FROM wazzup_chat_wallpapers ORDER BY user_id')
        result = cursor.fetchall()
    connection.commit()
    return result


def test_upload_replace_settings_and_remove_on_real_sql(pg):
    db = database(pg)
    storage = Storage(db)
    client, _, _ = build(db, storage)
    first = upload(client, OWNER, picture(), {'blur': 3})
    assert first.status_code == 201, first.get_json()
    first_state = first.get_json()
    assert client.get(PATH, headers=as_user(OWNER)).get_json() == first_state
    [(user_id, wallpaper_id, blob, thumb, width, height, size, settings)] = rows(pg)
    assert (user_id, wallpaper_id, width, height) == (OWNER, first_state['wallpaper']['id'], 1200, 800)
    assert size == len(storage.objects[blob]) and thumb in storage.objects
    assert settings == {**appearance.DEFAULT_SETTINGS, 'blur': 3}

    changed = client.put(PATH, json={'wallpaperId': wallpaper_id, 'settings': {'brightness': -40, 'y': 12.5}},
                         headers=as_user(OWNER))
    assert changed.status_code == 200
    assert rows(pg)[0][7] == {**appearance.DEFAULT_SETTINGS, 'brightness': -40, 'y': 12.5}
    assert changed.get_json()['wallpaper']['updatedAt'] >= first_state['wallpaper']['updatedAt']

    second = upload(client, OWNER, picture(color=(0, 0, 0)))
    assert second.status_code == 201
    [(_, second_id, second_blob, second_thumb, *_rest)] = rows(pg)
    assert second_id != wallpaper_id and set(storage.deleted) == {blob, thumb}
    assert client.put(PATH, json={'wallpaperId': wallpaper_id, 'settings': {}},
                      headers=as_user(OWNER)).status_code == 409
    image = client.get(PATH + '/wallpaper/' + second_id, headers=as_user(OWNER))
    assert image.status_code == 200 and image.data == storage.objects[second_blob]

    assert client.delete(PATH + '/wallpaper', headers=as_user(OWNER)).get_json()['wallpaper'] is None
    assert rows(pg) == [] and storage.objects == {}
    assert {second_blob, second_thumb} <= set(storage.deleted)


def test_admin_list_joins_people_and_departments(pg):
    db = database(pg)
    client, _, _ = build(db, Storage(db))
    upload(client, OWNER, picture())
    upload(client, OTHER, picture(color=(1, 1, 1)), {'saturation': 0})
    with pg.cursor() as cursor:
        # Самые свежие сверху — и при равном времени порядок не скачет.
        cursor.execute("UPDATE wazzup_chat_wallpapers SET updated_at = now() - interval '1 hour' "
                       'WHERE user_id = %s', (OTHER,))
    pg.commit()
    items = client.get(PATH + '/wallpapers', headers=as_user(ADMIN)).get_json()['items']
    assert [(item['userId'], item['name'], item['department']) for item in items] == [
        (OWNER, 'Иванова Айгерим', 'Отдел продаж'), (OTHER, 'Карабек Ержан', '')]
    assert items[1]['settings']['saturation'] == 0


def test_two_tabs_uploading_at_once_keep_one_wallpaper_and_no_orphans(pg):
    db = database(pg)
    storage = Storage(db)
    client, _, _ = build(db, storage)
    # Один слот пересжатия на процесс: второй одновременный запрос получает 429,
    # а не гонку за строку. Подряд — заменяет, и файлы первого уходят.
    appearance._slots.acquire()
    try:
        assert upload(client, OWNER, picture()).status_code == 429
    finally:
        appearance._slots.release()
    for shade in range(3):
        assert upload(client, OWNER, picture(color=(shade, shade, shade))).status_code == 201
    [(_, _, blob, thumb, *_rest)] = rows(pg)
    assert set(storage.objects) == {blob, thumb}


def test_settings_column_survives_unknown_and_missing_fields(pg):
    db = database(pg)
    client, _, _ = build(db, Storage(db))
    upload(client, OWNER, picture())
    with pg.cursor() as cursor:
        cursor.execute("UPDATE wazzup_chat_wallpapers SET settings = %s::jsonb", (json.dumps({'blur': 9, 'tint': 1}),))
    pg.commit()
    assert client.get(PATH, headers=as_user(OWNER)).get_json()['settings'] == {
        **appearance.DEFAULT_SETTINGS, 'blur': 9}
