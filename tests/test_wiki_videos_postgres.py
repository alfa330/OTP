# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: WIKI_VIDEO_TEST_PORT — порт локального Postgres.

В tests/test_wiki_videos.py функции wiki/videos.py подменены, и видно только,
что ручки их зовут. Что запросы ЗНАЧАТ — что найдёт докачка, что уберёт уборка,
что отдадут ручка /file/<id> и подпись витрины, пока ролик едет, — исполняется
здесь: на настоящей таблице wiki_files из wiki/schema.py (тот же CREATE и те же
ALTER, что на проде, дважды подряд — миграция обязана быть идемпотентной)
поверх маленьких таблиц-заглушек.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со
случайным именем, в конце она удаляется. Запуск:

    WIKI_VIDEO_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_wiki_videos_postgres.py
"""

import contextlib
import os
import uuid

import pytest

from wiki import articles as wiki_articles
from wiki import schema as wiki_schema
from wiki import videos

PORT = os.environ.get('WIKI_VIDEO_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (WIKI_VIDEO_TEST_PORT)')

OWNER, OTHER = 42, 7


def _wiki_files_ddl():
    found = [s for s in wiki_schema._STATEMENTS if 'CREATE TABLE IF NOT EXISTS wiki_files' in s]
    assert len(found) == 1
    return found[0].replace('%(now)s', wiki_schema._NOW)


@pytest.fixture()
def cur():
    import psycopg2
    name = 'wiki_video_%s' % uuid.uuid4().hex[:10]
    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                            connect_timeout=3)
    conn.autocommit = False
    cursor = conn.cursor()
    try:
        cursor.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto')
        cursor.execute('CREATE SCHEMA %s' % name)
        cursor.execute('SET search_path TO %s, public' % name)
        cursor.execute("SET statement_timeout TO '10s'")
        cursor.execute('CREATE TABLE users (id integer PRIMARY KEY)')
        cursor.execute('CREATE TABLE wiki_articles (id serial PRIMARY KEY)')
        cursor.execute('INSERT INTO users (id) VALUES (%s), (%s)', (OWNER, OTHER))
        cursor.execute(_wiki_files_ddl())
        for _round in range(2):
            for statement in wiki_schema._VIDEO_STATEMENTS:
                cursor.execute(statement)
        yield cursor
    finally:
        conn.rollback()
        with contextlib.suppress(Exception):
            cursor.execute('DROP SCHEMA IF EXISTS %s CASCADE' % name)
            conn.commit()
        conn.close()


def _start(cur, *, user=OWNER, name='Ролик.mp4', size=2048, fingerprint='1731900000'):
    file_id = uuid.uuid4()
    videos.insert_upload(cur, file_id=file_id, bucket='otp-files',
                         blob_path=videos.blob_path(file_id, 'mp4'), name=name,
                         content_type='video/mp4', size=size,
                         session_url='https://storage.example/s/%s' % file_id.hex,
                         origin='https://alfa330.github.io', uploaded_by=user,
                         fingerprint=fingerprint)
    return str(file_id)


def test_pending_upload_is_found_only_for_the_same_file_and_person(cur):
    file_id = _start(cur)
    found = videos.pending_for(cur, user_id=OWNER, name='Ролик.mp4', size=2048,
                               fingerprint='1731900000')
    assert found['id'] == file_id
    assert found['upload_session'].startswith('https://storage.example/s/')
    assert videos.pending_for(cur, user_id=OTHER, name='Ролик.mp4', size=2048,
                              fingerprint='1731900000') is None
    assert videos.pending_for(cur, user_id=OWNER, name='Ролик.mp4', size=2049,
                              fingerprint='1731900000') is None
    assert videos.pending_for(cur, user_id=OWNER, name='Ролик.mp4', size=2048,
                              fingerprint='другой') is None


def test_unfinished_video_is_not_served_and_not_signed(cur):
    file_id = _start(cur)
    record = wiki_articles.get_file(cur, file_id)
    assert record['upload_state'] == videos.UPLOADING
    assert wiki_articles.files_for_display(cur, [file_id]) == []

    videos.mark_ready(cur, file_id, duration=83.4, width=1920, height=1080)
    record = wiki_articles.get_file(cur, file_id)
    assert record['upload_state'] is None
    shown = wiki_articles.files_for_display(cur, [file_id])
    assert [str(row['id']) for row in shown] == [file_id]
    row = videos.upload_row(cur, file_id)
    assert row['upload_session'] is None and row['upload_origin'] is None
    assert (row['width'], row['height']) == (1920, 1080)
    assert abs(row['duration_seconds'] - 83.4) < 0.001
    # Готовый ролик докачка больше не подхватывает.
    assert videos.pending_for(cur, user_id=OWNER, name='Ролик.mp4', size=2048,
                              fingerprint='1731900000') is None


def test_poster_is_remembered_and_cleared_with_its_video(cur):
    file_id = _start(cur)
    poster_id = str(wiki_articles.register_file(
        cur, article_id=None, bucket='otp-files', blob_path='wiki/files/p.webp',
        original_name='Ролик.webp', content_type='image/webp', file_size=10, width=800,
        height=450, uploaded_by=OWNER))
    videos.set_poster(cur, file_id, poster_id)
    assert videos.upload_row(cur, file_id)['poster_file_id'] == poster_id
    assert videos.poster_row(cur, poster_id)['blob_path'] == 'wiki/files/p.webp'
    videos.delete_rows(cur, [file_id, poster_id])
    assert videos.upload_row(cur, file_id) is None
    assert videos.poster_row(cur, poster_id) is None


def test_stale_uploads_are_only_the_old_unfinished_ones(cur):
    fresh = _start(cur)
    old = _start(cur, name='Старый.mp4')
    done = _start(cur, name='Готовый.mp4')
    videos.mark_ready(cur, done, duration=None, width=None, height=None)
    cur.execute("UPDATE wiki_files SET created_at = created_at - INTERVAL '9 days' WHERE id IN (%s, %s)",
                (old, done))
    assert [row['id'] for row in videos.stale_uploads(cur)] == [old]
    assert fresh not in [row['id'] for row in videos.stale_uploads(cur)]


def test_not_a_uuid_is_simply_not_found(cur):
    assert videos.upload_row(cur, 'не-номер') is None


def test_article_type_check_is_rebuilt_for_video(cur):
    """На проде ограничение создано вместе с таблицей и о «Ролике» не знает:
    без пересборки сохранение статьи-ролика падало бы 500-й ошибкой."""
    import psycopg2
    cur.execute("""
        ALTER TABLE wiki_articles ADD COLUMN article_type VARCHAR(40) NOT NULL DEFAULT 'general'
            CHECK (article_type IN ('general', 'job_description', 'regulation', 'instruction',
                                    'tool_description', 'trainer'))
    """)
    cur.execute('SAVEPOINT before_rebuild')
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("INSERT INTO wiki_articles (article_type) VALUES ('video')")
    cur.execute('ROLLBACK TO SAVEPOINT before_rebuild')

    for _round in range(2):  # второй прогон ничего не меняет
        cur.execute(wiki_schema._article_type_check_statement())
    cur.execute("INSERT INTO wiki_articles (article_type) VALUES ('video') RETURNING id")
    assert cur.fetchone()[0]
    cur.execute('SAVEPOINT after_rebuild')
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("INSERT INTO wiki_articles (article_type) VALUES ('podcast')")
    cur.execute('ROLLBACK TO SAVEPOINT after_rebuild')


def test_resume_waits_for_a_cancel_in_progress(cur):
    """Отмена удалила строку, но ещё не зафиксировала: старт того же файла не
    должен подхватить отменяемую сессию (поймано на стенде 18.11.2026)."""
    import threading

    import psycopg2

    file_id = _start(cur)
    cur.connection.commit()
    cur.execute('SHOW search_path')
    search_path = cur.fetchone()[0]

    other = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                             connect_timeout=3)
    try:
        canceler = other.cursor()
        canceler.execute('SET search_path TO %s' % search_path)
        assert videos.upload_row(canceler, file_id, lock=True)['id'] == file_id
        videos.delete_rows(canceler, [file_id])  # не зафиксировано: «отмена ещё говорит с хранилищем»

        found = {}

        def resume():
            found['row'] = videos.pending_for(cur, user_id=OWNER, name='Ролик.mp4', size=2048,
                                              fingerprint='1731900000', lock=True)

        worker = threading.Thread(target=resume)
        worker.start()
        worker.join(0.6)
        assert worker.is_alive(), 'старт не дождался отмены'
        other.commit()
        worker.join(5)
        assert not worker.is_alive()
        assert found['row'] is None
    finally:
        other.close()
