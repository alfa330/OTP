# -*- coding: utf-8 -*-
"""Фоновый прогон ИИ «Объявлений OLX» на настоящем PostgreSQL (жалоба 25.11.2026: «Прогон не
найден — возможно, сервер перезапускался» посреди пачки из 34 объявлений).

Причина: на /api в Yandex Cloud отвечают несколько процессов, а реестр прогонов жил в памяти
одного. Здесь проверяется настоящий SQL прогона в базе (olx_ads_gen_jobs): второй идущий
прогон не ставится уникальным индексом, ход и стоп видны без процесса-ведущего, брошенный
прогон подхватывается с места обрыва ровно одним процессом, бросивший поток больше не пишет,
перезапусков подряд — не больше MAX_ATTEMPTS.

Ни боевых учёток, ни сети: только 127.0.0.1, своя схема со случайным именем, в конце она
удаляется. ИИ — заглушка с настоящей формой ответа цепочки вики (кортеж). Запуск:

    OLX_ADS_TEST_PORT=55471 python -m pytest tests/test_olx_ads_jobs_postgres.py

Объявления выдуманы.
"""
import contextlib
import os
import sys
import threading
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from olx_ads import ai, jobs, queries, schema  # noqa: E402

PORT = os.environ.get('OLX_ADS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (OLX_ADS_TEST_PORT)')

BODY = ('Подключаем водителей к Яндекс Про в нашем таксопарке, выплаты каждый день, '
        'комиссия парка два процента, поддержка круглосуточно, бонусы новичкам. ') * 4


class _Db:
    def __init__(self, schema_name):
        self.schema = schema_name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres',
                                dbname='postgres', connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.schema)
            cur.execute("SET statement_timeout TO '10s'")
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

    def q(self, sql, params=None):
        with self._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None


class _Ai:
    def __init__(self):
        self.calls = []
        self.gate = None
        self.down = 0
        self.lock = threading.Lock()

    def __call__(self, system, user, **kwargs):
        with self.lock:
            self.calls.append(user)
            down = self.down > 0
            if down:
                self.down -= 1
        if self.gate is not None:
            self.gate.wait(10)
        if down:
            raise ai.AiUnavailable('все провайдеры цепочки отказали: vertex 429')
        return ('ЗАГОЛОВОК: Работа в такси без ИП и отчётности\nОПИСАНИЕ:\n<p>%s</p>' % BODY,
                {'provider': 'vertex', 'model': 'fake'})


@pytest.fixture
def env(monkeypatch):
    import psycopg2
    name = 'olx_jobs_' + uuid.uuid4().hex[:8]
    admin = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres')
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + name)
    db = _Db(name)
    with db._get_cursor() as cur:
        schema.init_olx_ads_schema(cur)
        for n in range(1, 6):
            cur.execute("INSERT INTO olx_ads_adverts (cabinet_code, advert_id, status, title, "
                        "description, category_id, city_name) VALUES ('itaxi', %s, 'active', %s, "
                        "%s, 1812, %s)", (str(n), 'Объявление %d' % n, BODY, 'Город %d' % n))
        cur.execute("INSERT INTO olx_ads_briefs (title, offer, is_active) "
                    "VALUES ('Декабрь', 'Комиссия 2 процента', TRUE) RETURNING id")
        brief_id = cur.fetchone()[0]
        cur.execute("INSERT INTO olx_ads_brief_cabinets (brief_id, cabinet_code, is_active) "
                    "VALUES (%s, 'itaxi', TRUE)", (brief_id,))
    monkeypatch.setattr(jobs, 'HEARTBEAT_SECONDS', 0.1)
    monkeypatch.setattr(jobs, 'ORPHAN_SECONDS', 1.0)
    monkeypatch.setattr(jobs, 'SWEEP_MIN_GAP', 0)
    fake = _Ai()
    try:
        yield db, fake, brief_id
    finally:
        if fake.gate is not None:
            fake.gate.set()
        time.sleep(0.3)                     # дать потокам ведущих выйти до сноса схемы
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA %s CASCADE' % name)
        admin.close()


def _plan(db, *numbers):
    with db._get_cursor() as cur:
        adverts = [queries.get_advert(cur, 'itaxi', str(n)) for n in numbers]
        briefs = queries.active_briefs_for(cur, ['itaxi'])
    return {'adverts': adverts, 'briefs': briefs}


def _start(db, fake, *numbers):
    return jobs.start(db, _plan(db, *numbers), actor_id=7, actor_name='Тест',
                      instruction='короче', generate_fn=fake, pause=0.05)


def _wait(db, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = jobs.get(db, job_id, full=True)
        if snap['status'] != 'running':
            return snap
        time.sleep(0.05)
    raise AssertionError('прогон %s не закончился: %s' % (job_id, jobs.get(db, job_id)))


def _orphan(db, owner, position=0, attempts=1, numbers=(1, 2, 3), stop=False):
    """Строка «идёт», ведущий молчит минуту — как после перезапуска пода."""
    with db._get_cursor() as cur:
        job_id = queries.create_gen_job(
            cur, [{'cabinet': 'itaxi', 'advert_id': str(n)} for n in numbers], owner=owner,
            actor_name='Тест')
        cur.execute("UPDATE olx_ads_gen_jobs SET position = %s, attempts = %s, "
                    "stop_requested = %s, beat_at = NOW() - interval '1 minute' WHERE id = %s",
                    (position, attempts, stop, job_id))
    return job_id


def test_batch_writes_drafts_and_keeps_the_result_in_the_database(env):
    db, fake, brief_id = env
    job = _start(db, fake, 1, 2, 3)
    snap = _wait(db, job['id'])
    assert (snap['status'], snap['done'], snap['made'], snap['failed']) == ('done', 3, 3, 0)
    assert [m['advert_id'] for m in snap['result']['made']] == ['1', '2', '3']
    assert snap['result']['briefs'] == [{'id': brief_id, 'title': 'Декабрь'}]
    assert isinstance(snap['elapsed'], float) and snap['beat_age'] is None
    rows = db.q("SELECT advert_id, created_by, created_by_name FROM olx_ads_drafts "
                "WHERE status = 'draft' ORDER BY advert_id")
    assert rows == [('1', 7, 'Тест'), ('2', 7, 'Тест'), ('3', 7, 'Тест')]
    assert db.q("SELECT status, position, instruction, finished_at IS NOT NULL, current, note "
                "FROM olx_ads_gen_jobs") == [('done', 3, 'короче', True, None, None)]
    assert 'короче' in fake.calls[0]


def test_only_one_running_job_is_held_by_the_index(env):
    import psycopg2
    db, fake, _ = env
    fake.gate = threading.Event()
    job = _start(db, fake, 1, 2)
    with pytest.raises(jobs.JobBusy) as busy:
        _start(db, fake, 3, 4)
    assert busy.value.job['id'] == job['id']
    with pytest.raises(psycopg2.Error) as raw:
        with db._get_cursor() as cur:
            queries.create_gen_job(cur, [], owner='другой/1')
    assert raw.value.pgcode == '23505'
    fake.gate.set()
    assert _wait(db, job['id'])['status'] == 'done'
    # Место освободилось — следующая пачка ставится.
    assert _wait(db, _start(db, fake, 3)['id'])['status'] == 'done'


def test_poll_and_stop_work_without_the_runner_process(env):
    db, fake, _ = env
    fake.gate = threading.Event()
    job = _start(db, fake, 1, 2, 3)
    deadline = time.time() + 5
    while not fake.calls and time.time() < deadline:   # ИИ уже пишет первое объявление
        time.sleep(0.02)
    with jobs._live_lock:
        jobs._live.clear()                 # соседний процесс: ведущего у него нет
    assert jobs.active(db)['id'] == job['id']
    assert jobs.request_stop(db, job['id'])['stop_requested'] is True
    fake.gate.set()
    snap = _wait(db, job['id'])
    assert (snap['status'], snap['made'], snap['skipped']) == ('stopped', 1, 2)
    assert db.q("SELECT advert_id FROM olx_ads_drafts") == [('1',)]


def test_orphan_is_resumed_from_its_position_by_exactly_one_process(env):
    db, fake, _ = env
    job_id = _orphan(db, 'мёртвый/aaaa', position=1)
    # Два процесса обходят одновременно — забирает один.
    with db._get_cursor() as cur:
        first = queries.claim_gen_job(cur, job_id, 'первый/1', jobs.ORPHAN_SECONDS)
    with db._get_cursor() as cur:
        second = queries.claim_gen_job(cur, job_id, 'второй/2', jobs.ORPHAN_SECONDS)
    assert (first, second) == (2, None)
    # Тот, кто забрал, «умер» сразу — строка снова молчит; обход продолжает со второго.
    db.q("UPDATE olx_ads_gen_jobs SET beat_at = NOW() - interval '1 minute' WHERE id = %s",
         (job_id,))
    assert jobs.sweep(db, force=True, generate_fn=fake, pause=0.05) == [job_id]
    snap = _wait(db, job_id)
    assert (snap['status'], snap['made'], snap['resumed']) == ('done', 2, True)
    assert db.q("SELECT attempts FROM olx_ads_gen_jobs WHERE id = %s", (job_id,)) == [(3,)]
    assert sorted(r[0] for r in db.q("SELECT advert_id FROM olx_ads_drafts")) == ['2', '3']
    # Бросившие прогон потоки, очнувшись, ничего не допишут и не закроют.
    for owner in ('мёртвый/aaaa', 'первый/1'):
        with db._get_cursor() as cur:
            assert queries.record_gen_job_item(cur, job_id, owner, 9, made=[{'x': 1}]) is None
            assert queries.finish_gen_job(cur, job_id, owner, 'error') == 0
    assert db.q("SELECT status, position FROM olx_ads_gen_jobs WHERE id = %s",
                (job_id,)) == [('done', 3)]


def test_live_runner_is_not_taken_over(env):
    db, fake, _ = env
    fake.gate = threading.Event()
    job = _start(db, fake, 1, 2)
    time.sleep(jobs.ORPHAN_SECONDS + 0.4)      # дольше порога, но пульс идёт
    assert jobs.sweep(db, force=True) == []
    assert db.q("SELECT attempts FROM olx_ads_gen_jobs") == [(1,)]
    fake.gate.set()
    assert _wait(db, job['id'])['status'] == 'done'


def test_restarts_have_a_limit_and_stop_of_a_dead_job_closes_it(env):
    db, fake, _ = env
    worn = _orphan(db, 'мёртвый/bbbb', position=1, attempts=jobs.MAX_ATTEMPTS)
    assert jobs.sweep(db, force=True, generate_fn=fake) == []
    snap = jobs.get(db, worn, full=True)
    assert (snap['status'], snap['code'], snap['skipped']) == ('error', 'too_many_restarts', 2)
    assert [s['advert_id'] for s in snap['result']['skipped']] == ['2', '3']

    dead = _orphan(db, 'мёртвый/cccc', numbers=(4, 5))
    snap = jobs.request_stop(db, dead)
    assert (snap['status'], snap['skipped']) == ('stopped', 2)
    assert fake.calls == []


def test_ai_outage_waited_out_once_then_the_rest_is_marked(env):
    db, fake, _ = env
    fake.down = 1
    snap = _wait(db, _start(db, fake, 1, 2)['id'])
    assert (snap['status'], snap['made'], len(fake.calls)) == ('done', 2, 3)

    fake.calls.clear()
    fake.down = 2
    snap = _wait(db, _start(db, fake, 3, 4, 5)['id'])
    assert (snap['status'], snap['code'], snap['made']) == ('error', 'ai_unavailable', 0)
    assert snap['skipped'] == 3 and 'ИИ сейчас недоступен' in snap['error']


def test_summary_does_not_carry_texts_while_running(env):
    db, fake, _ = env
    fake.gate = threading.Event()
    job = _start(db, fake, 1, 2)
    with db._get_cursor() as cur:
        row = queries.gen_job_summary(cur, job['id'])
    assert 'made' not in row and 'targets' not in row
    snap = jobs.get(db, job['id'], full=True)
    assert 'result' not in snap and snap['beat_age'] < 5
    fake.gate.set()
    _wait(db, job['id'])
