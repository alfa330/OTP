# -*- coding: utf-8 -*-
"""Слепок «Классификатора авто» на настоящем Postgres: CLASSIFIER_TEST_PORT — порт кластера.

Чистую логику сверки проверяет tests/test_wiki_classifier_store.py. Здесь — SQL:
таблица из схемы вики, запись слепка (новое содержимое, то же содержимое, сбой),
«пора ли сверять», и книга помощника, которая подхватывает слепок из базы и
перечитывает его, когда ночная сверка сменила отпечаток. Только 127.0.0.1, своя
схема со случайным именем, в конце она удаляется.

    CLASSIFIER_TEST_PORT=55433 CLASSIFIER_TEST_PASSWORD=verify \\
        python -m pytest tests/test_wiki_classifier_store_postgres.py
"""

import json
import os
import uuid

import pytest

from wiki import classifier
from wiki import classifier_source as source
from wiki import classifier_store as store
from wiki import schema

PORT = os.environ.get('CLASSIFIER_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (CLASSIFIER_TEST_PORT)')


@pytest.fixture
def cursor():
    import psycopg2
    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT),
                            user=os.environ.get('CLASSIFIER_TEST_USER', 'postgres'),
                            password=os.environ.get('CLASSIFIER_TEST_PASSWORD') or None,
                            dbname=os.environ.get('CLASSIFIER_TEST_DB', 'postgres'), connect_timeout=3)
    name = 'classifier_%s' % uuid.uuid4().hex[:12]
    cur = conn.cursor()
    cur.execute('CREATE SCHEMA %s' % name)
    cur.execute('SET search_path TO %s' % name)
    try:
        yield cur
    finally:
        conn.rollback()
        cur.execute('DROP SCHEMA IF EXISTS %s CASCADE' % name)
        conn.commit()
        conn.close()


def _create(cur):
    for statement in schema._CLASSIFIER_SNAPSHOT_STATEMENTS:
        cur.execute(statement.replace('%(now)s', schema._NOW))


def _data(updated='2 нояб 2026'):
    data = json.loads(json.dumps(store.read_bundled()))
    data['source']['updated'] = updated
    return data


def test_without_the_table_nothing_breaks(cursor):
    assert store.table_exists(cursor) is False
    assert store.snapshot_state(cursor) is None
    assert store.load_snapshot(cursor) is None
    assert store.is_due(cursor) is False
    store.mark_error(cursor, 'нет таблицы')                    # молча, без исключения
    assert store.current_data(cursor)[1] == 'file'


def test_store_load_same_error_and_due(cursor):
    _create(cursor)
    assert store.table_exists(cursor) is True
    assert store.is_due(cursor) is True                      # слепка ещё нет

    data = _data()
    assert store.store(cursor, data, warnings=['замечание']) == 'changed'
    state = store.snapshot_state(cursor)
    assert state['digest'] == source.fingerprint(data)
    assert state['source_updated'] == '2 нояб 2026'
    assert state['error'] is None
    assert store.is_due(cursor) is False
    loaded = store.load_snapshot(cursor)
    assert source.fingerprint(loaded['data']) == source.fingerprint(data)
    assert store.current_data(cursor)[1] == 'snapshot'

    # Тот же справочник с другим временем проверки — «same», changed_at не двигается.
    cursor.execute("UPDATE wiki_classifier_snapshot SET changed_at = changed_at - interval '2 days', "
                   "checked_at = checked_at - interval '2 days'")
    assert store.is_due(cursor) is True
    again = dict(data, source=dict(data['source'], checked_at='2026-11-17T05:40:00+05:00'))
    assert store.store(cursor, again) == 'same'
    cursor.execute('SELECT changed_at < checked_at - interval \'1 day\' FROM wiki_classifier_snapshot')
    assert cursor.fetchone()[0] is True

    # Сбой сверки: причина в строке, данные прежние.
    store.mark_error(cursor, 'Не открылись страницы: kostanay — 502')
    state = store.snapshot_state(cursor)
    assert state['error'].startswith('Не открылись страницы')
    assert state['digest'] == source.fingerprint(data)

    # Новое содержимое — новые данные и отпечаток, ошибка снята.
    changed = _data(updated='20 нояб 2026')
    assert store.store(cursor, changed) == 'changed'
    state = store.snapshot_state(cursor)
    assert (state['source_updated'], state['error']) == ('20 нояб 2026', None)


def test_the_book_follows_the_snapshot(cursor):
    _create(cursor)
    classifier.invalidate()
    try:
        file_book = classifier.book(cursor)                  # слепка нет — книга из файла
        assert classifier._STATE['origin'] == 'file'

        data = _data()
        camry = next(car for car in file_book['cars'] if car['name'] == 'Toyota Camry')
        aktau = next(city for city in data['cities'] if city['id'] == 'aktau')
        table = data['lists'][aktau['tariffs']['comfort']['list']]
        row = next(row for row in table['rows'] if row[0] == camry['index'])
        row[1] = 2001                                        # сверка «принесла» новый год
        store.store(cursor, data)

        # Отпечаток спрашивается не чаще раза в минуту — до этого книга прежняя.
        assert classifier.book(cursor) is file_book
        classifier._STATE['checked'] -= classifier.RECHECK_SECONDS
        fresh = classifier.book(cursor)
        assert fresh is not file_book
        assert classifier._STATE['origin'] == 'snapshot'
        city = next(item for item in fresh['cities'] if item['id'] == 'aktau')
        car = next(item for item in fresh['cars'] if item['name'] == 'Toyota Camry')
        assert classifier.requirement(car, city, 'comfort')['year'] == 2001

        # Ответ экрану — из того же справочника, что книга.
        _book, served, digest, origin, state = classifier.current(cursor)
        assert (digest, origin) == (source.fingerprint(data), 'snapshot')
        assert served['lists'][aktau['tariffs']['comfort']['list']] == table
        assert state['digest'] == digest
    finally:
        classifier.invalidate()


def test_broken_query_does_not_break_the_callers_transaction(cursor):
    """Книгу спрашивают из чужой транзакции (помощник, «Наличие авто»): упавший
    запрос слепка откатывается к савпоинту, и транзакция живёт дальше."""
    cursor.execute('CREATE TABLE wiki_classifier_snapshot (id SMALLINT PRIMARY KEY)')   # не та форма
    classifier.invalidate()
    try:
        assert classifier.book(cursor) is not None                    # книга из файла
        cursor.execute('SELECT 1')
        assert cursor.fetchone()[0] == 1
    finally:
        classifier.invalidate()
