# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: PRODUCTION_TEST_PORT — порт локального Postgres.

Раздел «Продакшн» (задача #316). В tests/test_task_production_materials.py запросы
подменены и видно только, что маршруты их зовут. Что они ЗНАЧАТ — кто что видит
на доске и в «Готовом», какая обложка у карточки, что находит поиск, что
подхватит докачка и что уберётся вместе с задачей — исполняется здесь, на
настоящей схеме раздела (task_production.schema) поверх маленьких таблиц задач.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со
случайным именем, в конце она удаляется. Запуск:

    PRODUCTION_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_task_production_materials_postgres.py

Люди выдуманы.
"""
import contextlib
import os
import uuid

import pytest

from task_production import queries, schema

PORT = os.environ.get('PRODUCTION_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PRODUCTION_TEST_PORT)')

HEAD, VIDEO, SMM, REQUESTER, OUTSIDER = 1, 2, 3, 4, 5

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL DEFAULT 'operator',
                        department_id integer, avatar_bucket text, avatar_blob_path text);
    CREATE TABLE tasks (
        id serial PRIMARY KEY, subject text NOT NULL, description text, tag text NOT NULL DEFAULT 'task',
        status text NOT NULL DEFAULT 'assigned', priority text NOT NULL DEFAULT 'normal',
        is_backlog boolean NOT NULL DEFAULT FALSE, due_at timestamp,
        created_at timestamp NOT NULL DEFAULT now(), updated_at timestamp NOT NULL DEFAULT now(),
        started_at timestamp, completed_at timestamp, created_by integer, requested_by_id integer,
        requested_by_name text, production_brief jsonb);
    CREATE TABLE task_assignees (task_id integer NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                                 user_id integer NOT NULL, position integer NOT NULL DEFAULT 0);
    CREATE TABLE task_status_history (id serial PRIMARY KEY, task_id integer NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                                      status_code text NOT NULL, changed_by integer, comment text,
                                      changed_at timestamp NOT NULL DEFAULT now());
    INSERT INTO departments (id, code, name) VALUES (10, 'marketing', 'Маркетинг'), (20, 'szov', 'СЗоВ');
    INSERT INTO users (id, name, role, department_id) VALUES
        (1, 'Главова Гульнара', 'admin', 10),
        (2, 'Видеографов Вадим', 'marketing_manager', 10),
        (3, 'Сммова Сауле', 'marketing_manager', 10),
        (4, 'Заказчиков Заур', 'admin', 20),
        (5, 'Посторонний Павел', 'trainer', 20);
'''


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.name)
            cur.execute("SET statement_timeout TO '10s'")
        conn.commit()
        return conn

    @contextlib.contextmanager
    def cursor(self):
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


@pytest.fixture
def db():
    name = 'production_test_' + uuid.uuid4().hex[:10]
    base = _Db('public')
    with base.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + name)
    test_db = _Db(name)
    with test_db.cursor() as cur:
        cur.execute(BASE_DDL)
        for statement in schema.STATEMENTS:
            cur.execute(statement)
        # Идемпотентность: повторный старт приложения не падает.
        for statement in schema.STATEMENTS:
            cur.execute(statement)
    try:
        yield test_db
    finally:
        with base.cursor() as cur:
            cur.execute('DROP SCHEMA %s CASCADE' % name)


def _task(cur, subject, *, status='assigned', tag='production', creator=HEAD, assignees=(VIDEO,),
          requested_by=None, backlog=False, item='promo_video', due=None):
    cur.execute("""INSERT INTO tasks (subject, tag, status, created_by, requested_by_id, is_backlog,
                                      production_brief, due_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (subject, tag, status, creator, requested_by, backlog,
                 '{"item": "%s"}' % item if item else None, due))
    task_id = cur.fetchone()[0]
    for position, user_id in enumerate(assignees):
        cur.execute('INSERT INTO task_assignees (task_id, user_id, position) VALUES (%s, %s, %s)',
                    (task_id, user_id, position))
    return task_id


def _history(cur, task_id, *codes):
    for code in codes:
        cur.execute("INSERT INTO task_status_history (task_id, status_code, changed_by) VALUES (%s, %s, %s)",
                    (task_id, code, VIDEO))


def _material(cur, task_id, version, name, *, status='ready', kind='video', poster=True, uploader=VIDEO,
              size=100, age_days=0, fingerprint=None):
    material_id = uuid.uuid4()
    path = 'Production/%d/%s.mp4' % (task_id, material_id)
    queries.insert_material(cur, material_id=material_id, task_id=task_id, version=version, kind=kind,
                            name=name, extension='mp4', content_type='video/mp4', size=size, bucket='b',
                            blob_path=path, upload_session='https://s/%s' % material_id,
                            upload_origin='https://o', uploaded_by=uploader, fingerprint=fingerprint)
    if status == 'ready':
        queries.mark_ready(cur, material_id, version=version, duration=5.0, width=1280, height=720)
    if poster:
        queries.set_poster(cur, material_id, path + '.poster.jpg')
    if age_days:
        cur.execute("""UPDATE task_production_materials
                          SET created_at = created_at - make_interval(days => %s) WHERE id = %s""",
                    (age_days, str(material_id)))
    return str(material_id)


def test_board_scope_and_filters(db):
    with db.cursor() as cur:
        mine = _task(cur, 'Ролик видеографа')
        smm = _task(cur, 'Баннер SMM', assignees=(SMM,), item='static_banner')
        ordered = _task(cur, 'Заказ чужого отдела', creator=REQUESTER, assignees=(SMM,))
        _task(cur, 'В бэклоге', backlog=True)
        _task(cur, 'Уже принято', status='accepted')
        _task(cur, 'Обычная задача', tag='task')

        everything = {t['subject'] for t in queries.board(cur, scope_all=True, user_id=VIDEO)}
        assert everything == {'Ролик видеографа', 'Баннер SMM', 'Заказ чужого отдела'}

        own = {t['subject'] for t in queries.board(cur, scope_all=False, user_id=VIDEO)}
        assert own == {'Ролик видеографа'}
        # Постановщик из другого отдела видит свой заказ, и только его.
        assert {t['subject'] for t in queries.board(cur, scope_all=False, user_id=REQUESTER)} == {'Заказ чужого отдела'}
        assert queries.board(cur, scope_all=False, user_id=OUTSIDER) == []

        assert queries.has_production_relation(cur, REQUESTER)
        assert not queries.has_production_relation(cur, OUTSIDER)
        assert queries.is_marketing_member(cur, SMM)
        assert not queries.is_marketing_member(cur, REQUESTER)
        card = next(t for t in queries.board(cur, scope_all=True, user_id=VIDEO) if t['id'] == smm)
        assert [p['id'] for p in card['assignees']] == [SMM]
        assert card['brief'] == {'item': 'static_banner'} or card['brief'].get('item') == 'static_banner'
        assert mine and ordered


def test_cover_follows_current_version(db):
    with db.cursor() as cur:
        task_id = _task(cur, 'С версиями', status='returned')
        _history(cur, task_id, 'in_progress', 'completed', 'returned')
        old = _material(cur, task_id, 1, 'v1.mp4')
        _material(cur, task_id, 2, 'без кадра.mp4', poster=False)
        _material(cur, task_id, 2, 'грузится.mp4', status='uploading')

        card = queries.board(cur, scope_all=True, user_id=VIDEO)[0]
        assert card['completed_count'] == 1
        # В версии 2 готов один файл, кадра у него нет — обложка берётся из версии 1.
        assert card['materials_count'] == 1
        assert card['cover']['id'] == old

        new = _material(cur, task_id, 2, 'v2.mp4')
        card = queries.board(cur, scope_all=True, user_id=VIDEO)[0]
        assert card['cover']['id'] == new
        assert card['materials_count'] == 2


def test_done_search_and_groups(db):
    with db.cursor() as cur:
        video = _task(cur, 'Промо 50%_скидка', status='accepted', item='promo_video')
        _history(cur, video, 'completed', 'accepted')
        _material(cur, video, 1, 'Финал «Осень».mp4')
        banner = _task(cur, 'Баннер для сайта', status='accepted', item='static_banner', assignees=(SMM,))
        _history(cur, banner, 'completed', 'accepted')
        _task(cur, 'Ещё в работе', status='in_progress')

        tasks, total = queries.done(cur, scope_all=True, user_id=VIDEO)
        assert total == 2 and {t['id'] for t in tasks} == {video, banner}
        assert all(t['accepted_at'] is not None for t in tasks)

        found, total = queries.done(cur, scope_all=True, user_id=VIDEO, query='осень')
        assert total == 1 and found[0]['id'] == video  # по имени файла, без учёта регистра
        # % и _ в поиске — буквы, а не шаблон.
        found, total = queries.done(cur, scope_all=True, user_id=VIDEO, query='50%_')
        assert total == 1 and found[0]['id'] == video
        found, total = queries.done(cur, scope_all=True, user_id=VIDEO, query='%')
        assert total == 1

        found, total = queries.done(cur, scope_all=True, user_id=VIDEO, group='digital')
        assert total == 1 and found[0]['id'] == banner
        assert queries.done(cur, scope_all=True, user_id=VIDEO, group='нет такого') == ([], 0)

        mine, total = queries.done(cur, scope_all=False, user_id=SMM)
        assert total == 1 and mine[0]['id'] == banner

        page, total = queries.done(cur, scope_all=True, user_id=VIDEO, limit=1, offset=1)
        assert total == 2 and len(page) == 1


def test_detail_reads(db):
    with db.cursor() as cur:
        task_id = _task(cur, 'Карточка', status='completed', requested_by=REQUESTER)
        _history(cur, task_id, 'in_progress', 'completed')
        found = queries.task(cur, task_id)
        assert found['tag'] == 'production' and found['completed_count'] == 1
        assert found['requested_by_name'] == 'Заказчиков Заур'
        assert queries.visible(cur, found, scope_all=False, user_id=REQUESTER)
        assert not queries.visible(cur, found, scope_all=False, user_id=OUTSIDER)
        assert [row[0] for row in queries.history(cur, task_id)] == ['in_progress', 'completed']
        material_id = _material(cur, task_id, 1, 'файл.mp4')
        locked = queries.material(cur, material_id, lock=True)
        assert locked['name'] == 'файл.mp4' and locked['upload_session'] is None
        assert queries.count_in_version(cur, task_id, 1) == 1


def test_resume_and_cleanup(db):
    with db.cursor() as cur:
        task_id = _task(cur, 'Докачка', status='in_progress')
        pending = _material(cur, task_id, 1, 'большой.mp4', status='uploading', size=5000, poster=False)
        found = queries.pending_for(cur, task_id=task_id, user_id=VIDEO, name='большой.mp4', size=5000)
        assert found['id'] == pending and found['upload_session']
        # Другой размер, другой человек — это другой файл.
        assert queries.pending_for(cur, task_id=task_id, user_id=VIDEO, name='большой.mp4', size=4999) is None
        assert queries.pending_for(cur, task_id=task_id, user_id=SMM, name='большой.mp4', size=5000) is None

        stale = _material(cur, task_id, 1, 'брошенный.mp4', status='uploading', poster=False, age_days=9)
        assert queries.pending_for(cur, task_id=task_id, user_id=VIDEO, name='брошенный.mp4', size=100) is None
        assert [row['id'] for row in queries.stale_uploads(cur)] == [stale]

        _material(cur, task_id, 1, 'готовый.mp4')
        blobs = queries.blobs_of_task(cur, task_id)
        assert len(blobs) == 4  # три файла и одна обложка
        assert any(b['blob_path'].endswith('.poster.jpg') for b in blobs)
        # Недогруженные — с адресом сессии: её отменят, чтобы файл не догрузился после удаления задачи.
        sessions = {b['upload_session'] for b in blobs if b['upload_session']}
        assert len(sessions) == 2 and all(s.startswith('https://s/') for s in sessions)

        cur.execute('DELETE FROM tasks WHERE id = %s', (task_id,))
        cur.execute('SELECT COUNT(*) FROM task_production_materials')
        assert cur.fetchone()[0] == 0


def test_requester_sees_ordered_task(db):
    # Поручитель (requested_by_id) из другого отдела — не постановщик и не исполнитель.
    with db.cursor() as cur:
        ordered = _task(cur, 'Поручено', creator=HEAD, requested_by=REQUESTER)
        assert [t['id'] for t in queries.board(cur, scope_all=False, user_id=REQUESTER)] == [ordered]
        assert queries.has_production_relation(cur, REQUESTER)
        _task(cur, 'Принято поручителю', status='accepted', creator=HEAD, requested_by=REQUESTER)
        assert queries.done(cur, scope_all=False, user_id=REQUESTER)[1] == 1


def test_review_card_counts_submitted_version(db):
    with db.cursor() as cur:
        task_id = _task(cur, 'На проверке', status='completed')
        _history(cur, task_id, 'in_progress', 'completed')
        first = _material(cur, task_id, 1, 'v1.mp4')
        _material(cur, task_id, 1, 'v1-квадрат.mp4')
        card = queries.board(cur, scope_all=True, user_id=VIDEO)[0]
        assert card['materials_count'] == 2 and card['cover']['id'] == first
        accepted = _task(cur, 'Принято', status='accepted')
        _history(cur, accepted, 'completed', 'accepted')
        _material(cur, accepted, 1, 'финал.mp4')
        tasks, _total = queries.done(cur, scope_all=True, user_id=VIDEO)
        assert tasks[0]['materials_count'] == 1


def test_done_search_ignores_unfinished_upload(db):
    with db.cursor() as cur:
        task_id = _task(cur, 'Принятый ролик', status='accepted')
        _history(cur, task_id, 'completed', 'accepted')
        _material(cur, task_id, 1, 'финал.mp4')
        _material(cur, task_id, 2, 'черновик-секрет.mp4', status='uploading', poster=False)
        assert queries.done(cur, scope_all=True, user_id=VIDEO, query='черновик') == ([], 0)


def test_resume_needs_the_same_file(db):
    with db.cursor() as cur:
        task_id = _task(cur, 'Отпечаток', status='in_progress')
        pending = _material(cur, task_id, 1, 'IMG_0001.MOV', status='uploading', size=5000, poster=False,
                            fingerprint='1729000000000')
        found = queries.pending_for(cur, task_id=task_id, user_id=VIDEO, name='IMG_0001.MOV', size=5000,
                                    fingerprint='1729000000000')
        assert found['id'] == pending
        # Тот же размер и имя, но другой файл (другая дата изменения) — начнётся заново.
        assert queries.pending_for(cur, task_id=task_id, user_id=VIDEO, name='IMG_0001.MOV', size=5000,
                                   fingerprint='1729999999999') is None


def test_uploads_of_others_in_version(db):
    with db.cursor() as cur:
        task_id = _task(cur, 'Двое исполнителей', status='in_progress', assignees=(VIDEO, SMM))
        _material(cur, task_id, 1, 'мой.mp4', status='uploading', poster=False)
        _material(cur, task_id, 1, 'коллеги.mp4', status='uploading', poster=False, uploader=SMM)
        _material(cur, task_id, 1, 'готово.mp4', uploader=SMM)
        others = queries.uploading_by_others(cur, task_id, 1, VIDEO)
        assert others == [{'name': 'коллеги.mp4', 'uploaded_by_name': 'Сммова Сауле'}]
        assert queries.uploading_by_others(cur, task_id, 2, VIDEO) == []

