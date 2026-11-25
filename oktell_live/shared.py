# -*- coding: utf-8 -*-
"""Очередь запросов к Oktell, общая для всех процессов портала: задания и ответы лежат в базе.

Зачем
-----
Очередь в памяти процесса (`jobs.Jobs`) работала, пока портал был одним процессом (Render). В
облаке портал — две копии api за балансировщиком и отдельный worker без HTTP (бот, планировщик).
Исполнитель коннектора приносит ответ следующим опросом, а балансировщик отдаёт этот опрос любой
копии: ответ, попавший не в ту, выбрасывался, и запрос ждал 60 с и падал. 25.11.2026 «Биллинг
Oktell» показывал «Не удалось получить данные из Oktell» примерно через раз (из шести одинаковых
запросов два — 502 ровно через 60 с, хотя коннектор исполнил их за 0,17 с). Запросы worker
(ночные выгрузки, отбивка, выборка на оценку, записи) до исполнителя не доходили вовсе: опросы
коннектора приходят только в api.

Как устроено
------------
    любой процесс: run()      INSERT задание + NOTIFY 'n' ──────────────►  oktell_jobs
    любая копия api: опрос    claim — FOR UPDATE SKIP LOCKED  ◄────────────┘  (будит NOTIFY 'n')
    любая копия api: ответ    проверка → UPDATE + NOTIFY 'd:<id>' ──► слушатель процесса, где ждут
    ждущий поток              DELETE … RETURNING — результат забран, строки больше нет

Сведения об исполнителе (какие наборы знает, каких таблиц ему не дали, носит ли записи) — строка
`oktell_workers` на каждый опрос: «на связи ли шлюз» отвечает любой процесс, а не только тот, куда
пришёл опрос. Логика ответов, отказов и проверки принесённого — та же, что у `jobs.Jobs`: этот
класс меняет только то, где лежат задания.

Протокол коннектора не меняется — VM обновлять не нужно.

Сигналы
-------
Один слушатель LISTEN на процесс, на своём соединении (как у колокола и «АТС»); поднимается при
первом обращении к очереди, поэтому realtime его не поднимает. Сигнал — только «загляни в базу».
Потерялся (слушатель переподключается) — ждущий поток всё равно сверяется с базой раз в
RECHECK_SECONDS, опрос исполнителя — раз в TAKE_RECHECK_SECONDS; после переподключения слушатель
будит всех сам.

Рубильник: OKTELL_JOBS_SHARED=0 — очередь снова в памяти процесса, как до 25.11.2026 (годится
только для портала из одного процесса).
"""

import json
import logging
import os
import selectors
import threading
import time

from cdr import oktell_datasets
from oktell_live import jobs as jobs_mod

log = logging.getLogger(__name__)

CHANNEL = 'oktell_jobs'
SHARED_ENV = 'OKTELL_JOBS_SHARED'

# Ждущий поток сверяется с базой не реже — на случай потерянного сигнала.
RECHECK_SECONDS = 2.0
# Опрос исполнителя без сигнала заглядывает в базу не реже.
TAKE_RECHECK_SECONDS = 5.0
# «Кто на связи» читается из базы не чаще раза в столько секунд на процесс.
ALIVE_CACHE_SECONDS = 2.0
# Брошенное задание (процесс умер, не дождавшись) лежит после срока не дольше, подметается не чаще.
STALE_SECONDS = 600
CLEANUP_EVERY_SECONDS = 60

HEARTBEAT_SECONDS = 20
LISTENER_RETRY_SECONDS = 2
LISTENER_RETRY_MAX_SECONDS = 60
LISTENER_HEALTHY_SECONDS = 60

MAX_ID = 64
MAX_WORKER = 120


def enabled():
    """Общая очередь включена (по умолчанию — да)."""
    return str(os.environ.get(SHARED_ENV) or '').strip().lower() not in jobs_mod._FALSE


def _loads(value):
    """JSONB из драйвера: psycopg2 отдаёт уже разобранным, другой драйвер — строкой."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value).decode('utf-8')
    if isinstance(value, str):
        return json.loads(value)
    return value


def _bytes(value):
    return bytes(value) if value is not None else None


class PgStore:
    """Задания и исполнители в PostgreSQL. `cursor` — фабрика контекста курсора пула приложения
    (коммит на выходе), `listen_connect` — новое соединение для LISTEN, вне пула."""

    def __init__(self, cursor, listen_connect):
        self._cursor = cursor
        self._listen_connect = listen_connect
        self._callbacks = []
        self._start_lock = threading.Lock()
        self._started = False
        self._cleanup_at = 0.0

    # ── сигналы ──────────────────────────────────────────────────────────────

    def subscribe(self, callback):
        self._callbacks.append(callback)

    def start(self):
        """Поднять слушатель — один раз на процесс."""
        if self._started:
            return
        with self._start_lock:
            if self._started:
                return
            threading.Thread(target=self._listen, daemon=True, name='oktell-jobs-listener').start()
            self._started = True

    def _signal(self, payload):
        for callback in list(self._callbacks):
            try:
                callback(payload)
            except Exception:  # noqa: BLE001
                log.exception('Очередь Oktell: сигнал %r не обработан', payload)

    def _drain(self, conn):
        while conn.notifies:
            note = conn.notifies.pop(0)
            self._signal(str(getattr(note, 'payload', '') or ''))

    def _listen(self, stop=None):
        """Слушатель на собственном соединении. selectors, а не select.select: у процесса бывает
        больше тысячи соединений, и номер дескриптора выше 1023 select.select не берёт."""
        delay = LISTENER_RETRY_SECONDS
        while stop is None or not stop.is_set():
            conn = None
            selector = None
            started = time.monotonic()
            try:
                conn = self._listen_connect()
                conn.set_session(autocommit=True)
                cursor = conn.cursor()
                cursor.execute('LISTEN ' + CHANNEL)
                # Пока LISTEN лежал, сигналы этому процессу не копились: все ждущие сверяются с базой.
                self._signal('*')
                log.info('Очередь Oktell: слушатель %s подключён', CHANNEL)
                selector = selectors.DefaultSelector()
                selector.register(conn, selectors.EVENT_READ)
                while stop is None or not stop.is_set():
                    ready = selector.select(HEARTBEAT_SECONDS)
                    conn.poll()
                    self._drain(conn)
                    if not ready:
                        # Тишина — проверить, что соединение живо: мёртвое бросит, и слушатель переподключится.
                        cursor.execute('SELECT 1')
                        conn.poll()
                        self._drain(conn)
            except Exception:  # noqa: BLE001
                log.exception('Очередь Oktell: слушатель %s упал, переподключение', CHANNEL)
            finally:
                for resource in (selector, conn):
                    if resource is not None:
                        try:
                            resource.close()
                        except Exception:  # noqa: BLE001
                            pass
            if stop is not None and stop.is_set():
                return
            if time.monotonic() - started >= LISTENER_HEALTHY_SECONDS:
                delay = LISTENER_RETRY_SECONDS
            else:
                delay = min(LISTENER_RETRY_MAX_SECONDS, max(LISTENER_RETRY_SECONDS, delay * 2))
            time.sleep(delay)

    # ── задания ──────────────────────────────────────────────────────────────

    def submit(self, job_id, kind, view, timeout):
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO oktell_jobs (id, kind, job, deadline) "
                "VALUES (%s, %s, %s::jsonb, now() + %s * interval '1 second')",
                (job_id, kind, json.dumps(view, ensure_ascii=False), float(timeout)))
            # Уходит при коммите — вместе с заданием, о котором сообщает.
            cur.execute("SELECT pg_notify(%s, 'n')", (CHANNEL,))
        self._cleanup_now_and_then()

    def claim(self, worker):
        """Старейшее незабранное задание этому исполнителю — описание для него или None."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE oktell_jobs SET taken_at = now(), worker = %s "
                "WHERE id = (SELECT id FROM oktell_jobs WHERE taken_at IS NULL AND deadline > now() "
                "            ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
                "RETURNING job", (str(worker)[:MAX_WORKER],))
            row = cur.fetchone()
        return _loads(row[0]) if row else None

    def taken(self, job_id):
        """(вид, описание) задания, которое забрано и ждёт ответа, или None."""
        with self._cursor() as cur:
            cur.execute("SELECT kind, job FROM oktell_jobs "
                        "WHERE id = %s AND taken_at IS NOT NULL AND done_at IS NULL", (job_id,))
            row = cur.fetchone()
        return (row[0], _loads(row[1])) if row else None

    def complete(self, job_id, result, audio):
        """Записать ответ и разбудить ждущего. False — задание уже снято или закрыто."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE oktell_jobs SET done_at = now(), result = %s::jsonb, audio = %s "
                "WHERE id = %s AND done_at IS NULL RETURNING id",
                (json.dumps(result, ensure_ascii=False), audio, job_id))
            done = cur.fetchone() is not None
            if done:
                cur.execute("SELECT pg_notify(%s, %s)", (CHANNEL, 'd:' + job_id))
        return done

    def collect(self, job_id):
        """Забрать готовый ответ (строка удаляется): (результат, аудио) или None, если не готов."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM oktell_jobs WHERE id = %s AND done_at IS NOT NULL "
                        "RETURNING result, audio", (job_id,))
            row = cur.fetchone()
        return (_loads(row[0]), _bytes(row[1])) if row else None

    def drop_if_idle(self, job_id):
        """Задание никто не забрал, и никто ничего не исполняет: снять его — True."""
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM oktell_jobs j WHERE j.id = %s AND j.taken_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM oktell_jobs w WHERE w.taken_at IS NOT NULL "
                "                AND w.done_at IS NULL AND w.deadline > now()) "
                "RETURNING j.id", (job_id,))
            return cur.fetchone() is not None

    def withdraw(self, job_id):
        """Срок вышел — снять задание: (забрано ли, результат или None, аудио)."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM oktell_jobs WHERE id = %s "
                        "RETURNING taken_at IS NOT NULL, done_at IS NOT NULL, result, audio", (job_id,))
            row = cur.fetchone()
        if not row:
            return False, None, None
        taken, done, result, audio = row
        if not done:
            return bool(taken), None, None
        return True, _loads(result), _bytes(audio)

    def snapshot(self):
        with self._cursor() as cur:
            cur.execute(
                "SELECT count(*) FILTER (WHERE taken_at IS NULL), "
                "       count(*) FILTER (WHERE taken_at IS NOT NULL AND done_at IS NULL), "
                "       count(*) FILTER (WHERE done_at IS NOT NULL) "
                "FROM oktell_jobs WHERE deadline > now()")
            pending, in_flight, answered = cur.fetchone()
        return {'pending': int(pending or 0), 'in_flight': int(in_flight or 0),
                'answered': int(answered or 0)}

    def _cleanup_now_and_then(self):
        now = time.monotonic()
        if now < self._cleanup_at:
            return
        self._cleanup_at = now + CLEANUP_EVERY_SECONDS
        try:
            with self._cursor() as cur:
                cur.execute("DELETE FROM oktell_jobs WHERE deadline < now() - %s * interval '1 second'",
                            (float(STALE_SECONDS),))
                cur.execute("DELETE FROM oktell_workers WHERE seen_at < now() - interval '1 day'")
        except Exception as exc:  # noqa: BLE001
            log.warning('Очередь Oktell: брошенные задания не подметены: %s', exc)

    # ── исполнители ──────────────────────────────────────────────────────────

    def worker_seen(self, worker, info):
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO oktell_workers (worker, info, seen_at) VALUES (%s, %s::jsonb, now()) "
                "ON CONFLICT (worker) DO UPDATE SET info = EXCLUDED.info, seen_at = now()",
                (str(worker)[:MAX_WORKER], json.dumps(info, ensure_ascii=False)))

    def touch_worker(self, worker):
        with self._cursor() as cur:
            cur.execute("UPDATE oktell_workers SET seen_at = now() WHERE worker = %s",
                        (str(worker)[:MAX_WORKER],))

    def workers(self, silent_seconds):
        """[(исполнитель, сведения, сколько секунд молчит)] — опрашивавшие за silent_seconds."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT worker, info, EXTRACT(EPOCH FROM now() - seen_at) FROM oktell_workers "
                "WHERE seen_at > now() - %s * interval '1 second' ORDER BY worker",
                (float(silent_seconds),))
            rows = cur.fetchall()
        return [(row[0], _loads(row[1]), float(row[2] or 0)) for row in rows]


def _shareable(info):
    """Сведения об исполнителе из jobs.Jobs.worker_seen → JSON для oktell_workers."""
    return {'version': info.get('version'), 'catalog': info.get('catalog'),
            'datasets': sorted(info['datasets']) if info.get('datasets') is not None else None,
            'unreadable': sorted(info.get('unreadable') or ()), 'records': info.get('records')}


def _from_shared(info):
    """JSON из oktell_workers → описание исполнителя в форме jobs.Jobs._alive_workers."""
    info = info if isinstance(info, dict) else {}
    datasets = info.get('datasets')
    records = info.get('records')
    return {'version': info.get('version'), 'catalog': info.get('catalog'),
            'datasets': frozenset(str(name) for name in datasets) if isinstance(datasets, list) else None,
            'unreadable': frozenset(str(key) for key in (info.get('unreadable') or ()) if isinstance(key, str)),
            'records': records if isinstance(records, bool) else None}


def validated_result(item, kind, view):
    """Ответ исполнителя → результат, как его проверяет jobs.Jobs.finish."""
    if kind == jobs_mod.RECORD_KIND:
        paths = tuple(str(path) for path in ((view or {}).get('paths') or ()))
        return jobs_mod.record_result(item, paths)
    if item.get('ok'):
        try:
            return {'ok': True, 'rows': jobs_mod.clean_rows(item.get('rows'))}
        except ValueError as exc:
            return {'ok': False, 'reason': 'db',
                    'error': 'ответ коннектора не похож на строки запроса: %s' % exc}
    return {'ok': False, 'reason': str(item.get('reason') or 'db')[:20],
            'error': str(item.get('error') or '')[:400]}


class SharedJobs(jobs_mod.Jobs):
    """jobs.Jobs, у которой задания, ответы и исполнители — в общем хранилище (PgStore)."""

    def __init__(self, store, clock=time.monotonic):
        super().__init__(clock=clock)
        self._store = store
        self._waiting = {}            # id → _Job, которого ждёт поток этого процесса
        self._seq = 0                 # растёт с каждым сигналом «появилось задание»
        self._alive_at = None
        self._alive_cache = []
        self._listening = False
        store.subscribe(self._on_signal)

    def _ensure_listening(self):
        if not self._listening:
            self._listening = True
            self._store.start()

    def _on_signal(self, payload):
        if payload.startswith('d:'):
            with self._cond:
                job = self._waiting.get(payload[2:])
            if job is not None:
                job.done.set()
            return
        with self._cond:
            self._seq += 1
            self._cond.notify_all()
            everyone = list(self._waiting.values()) if payload == '*' else []
        for job in everyone:
            job.done.set()

    def _store_problem(self, what, exc):
        now = self._clock()
        key = ('store', what)
        with self._cond:
            last = self._logged.get(key)
            if last is not None and now - last < jobs_mod.LOG_EVERY_SECONDS:
                return
            self._logged[key] = now
        log.warning('Очередь Oktell в базе: %s — %s', what, str(exc)[:300])

    # ── исполнители ──────────────────────────────────────────────────────────

    def worker_seen(self, worker, *, version=None, catalog=None, datasets=None, unreadable=None,
                    records=None):
        super().worker_seen(worker, version=version, catalog=catalog, datasets=datasets,
                            unreadable=unreadable, records=records)
        with self._cond:
            info = self._workers.get(worker)
            shared = _shareable(info) if info else None
        if shared is None:
            return
        try:
            self._store.worker_seen(worker, shared)
        except Exception as exc:  # noqa: BLE001
            self._store_problem('исполнитель не записан', exc)
            return
        with self._cond:
            self._alive_at = None

    def _alive(self):
        now = self._clock()
        with self._cond:
            if self._alive_at is not None and now - self._alive_at < ALIVE_CACHE_SECONDS:
                return list(self._alive_cache)
        try:
            rows = self._store.workers(jobs_mod.WORKER_SILENT_SECONDS)
        except Exception as exc:  # noqa: BLE001
            self._store_problem('не прочитано, кто на связи', exc)
            return super()._alive()
        alive = [_from_shared(info) for _worker, info, _silent in rows]
        with self._cond:
            self._alive_at, self._alive_cache = now, alive
        return list(alive)

    # ── вызывающий ───────────────────────────────────────────────────────────

    def _submit(self, job):
        self._ensure_listening()
        with self._cond:
            if len(self._waiting) >= jobs_mod.MAX_PENDING:
                self._stats['overflow'] += 1
                raise jobs_mod.Refused('очередь запросов к VM шлюза переполнена')
            self._waiting[job.id] = job
            self._stats['submitted'] += 1
        try:
            self._store.submit(job.id, job.kind, job.view(), max(1.0, job.deadline - self._clock()))
        except Exception as exc:  # noqa: BLE001
            with self._cond:
                self._waiting.pop(job.id, None)
            self._store_problem('задание не записано', exc)
            raise jobs_mod.Refused('очередь запросов к VM шлюза недоступна: база не приняла задание')
        with self._cond:
            # Опрос исполнителя, ждущий в этом же процессе, не ждёт сигнала из базы.
            self._seq += 1
            self._cond.notify_all()

    def _collect(self, job):
        try:
            got = self._store.collect(job.id)
        except Exception as exc:  # noqa: BLE001
            self._store_problem('ответ не прочитан', exc)
            return None
        if got is None:
            return None
        result, audio = got
        result = dict(result or {})
        if audio is not None:
            result['audio'] = audio
        return result

    def _await(self, job, timeout):
        try:
            pickup_at = self._clock() + jobs_mod.PICKUP_SECONDS
            while True:
                remaining = job.deadline - self._clock()
                if remaining <= 0:
                    return self._expire(job, timeout)
                job.done.wait(min(RECHECK_SECONDS, remaining))
                job.done.clear()
                result = self._collect(job)
                if result is not None:
                    return result
                if self._clock() >= pickup_at:
                    pickup_at = self._clock() + jobs_mod.PICKUP_SECONDS
                    # Никто не забрал задание и никто ничего не исполняет — исполнителей нет. Если же
                    # они заняты чужими запросами, ждём своей очереди: свободный появится.
                    try:
                        dropped = self._store.drop_if_idle(job.id)
                    except Exception as exc:  # noqa: BLE001
                        self._store_problem('не проверено, забрано ли задание', exc)
                        dropped = False
                    if dropped:
                        with self._cond:
                            self._stats['not_taken'] += 1
                        raise jobs_mod.Refused('коннектор Oktell на VM шлюза не забрал запрос за %d с'
                                               % int(min(jobs_mod.PICKUP_SECONDS, timeout)))
        finally:
            with self._cond:
                self._waiting.pop(job.id, None)

    def _expire(self, job, timeout):
        try:
            taken, result, audio = self._store.withdraw(job.id)
        except Exception as exc:  # noqa: BLE001
            # Неизвестно, исполнялся ли запрос, — мосту его не отдаём (не повторять тяжёлое вслепую).
            self._store_problem('задание не снято по сроку', exc)
            taken, result, audio = True, None, None
        if result is not None:
            result = dict(result)
            if audio is not None:
                result['audio'] = audio
            return result
        with self._cond:
            self._stats['no_answer' if taken else 'not_taken'] += 1
        if taken:
            raise jobs_mod.Failed('коннектор Oktell на VM шлюза не ответил за %d с (%s)'
                                  % (int(timeout), job.label))
        raise jobs_mod.Refused('очередь запросов к VM шлюза не дошла до запроса за %d с' % int(timeout))

    # ── ручка исполнителя ────────────────────────────────────────────────────

    def take(self, worker, wait):
        self._ensure_listening()
        wait = max(0.0, min(float(wait), jobs_mod.POLL_WAIT_SECONDS))
        end = self._clock() + wait
        with self._cond:
            info = self._workers.get(worker)
            if info is not None:
                info['waiting'] += 1
        try:
            while True:
                with self._cond:
                    seq = self._seq
                try:
                    view = self._store.claim(worker)
                except Exception as exc:  # noqa: BLE001
                    self._store_problem('задание не выдано', exc)
                    view = None
                if view is not None:
                    with self._cond:
                        self._stats['taken'] += 1
                    return [view]
                now = self._clock()
                if now >= end:
                    return []
                with self._cond:
                    if self._seq == seq:
                        self._cond.wait(min(end - now, TAKE_RECHECK_SECONDS))
        finally:
            with self._cond:
                if info is not None:
                    info['waiting'] = max(0, info['waiting'] - 1)
                    info['seen_at'] = self._clock()
            try:
                self._store.touch_worker(worker)
            except Exception as exc:  # noqa: BLE001
                self._store_problem('отметка исполнителя не обновлена', exc)

    def finish(self, results):
        if not isinstance(results, list):
            return 0
        delivered = 0
        for item in results[:jobs_mod.MAX_DONE_PER_POLL]:
            if not isinstance(item, dict):
                continue
            job_id = str(item.get('id') or '')[:MAX_ID]
            try:
                meta = self._store.taken(job_id) if job_id else None
            except Exception as exc:  # noqa: BLE001
                self._store_problem('ответ не сверен с заданием', exc)
                continue
            if meta is None:
                # Вызывающий уже не ждёт (срок вышел) или задание чужое: ответ выбрасываем.
                with self._cond:
                    self._stats['late'] += 1
                continue
            kind, view = meta
            result = validated_result(item, kind, view)
            audio = result.pop('audio', None)
            try:
                stored = self._store.complete(job_id, result, audio)
            except Exception as exc:  # noqa: BLE001
                self._store_problem('ответ не записан', exc)
                continue
            if not stored:
                with self._cond:
                    self._stats['late'] += 1
                continue
            delivered += 1
            with self._cond:
                job = self._waiting.get(job_id)
            if job is not None:
                job.done.set()
        return delivered

    # ── диагностика ──────────────────────────────────────────────────────────

    def status(self):
        with self._cond:
            stats = dict(self._stats)
            waiting_here = len(self._waiting)
        report = {'enabled': jobs_mod.enabled(), 'shared': True, 'catalog': oktell_datasets.fingerprint(),
                  'waiting_here': waiting_here, 'stats': stats}
        try:
            report.update(self._store.snapshot())
            report['workers'] = [
                {'worker': worker, 'version': info.get('version'), 'catalog': info.get('catalog'),
                 'silent_seconds': int(silent),
                 'datasets': len(info['datasets']) if isinstance(info.get('datasets'), list) else None,
                 'unreadable': info.get('unreadable') or [], 'records': info.get('records')}
                for worker, info, silent in self._store.workers(jobs_mod.WORKER_SILENT_SECONDS * 10)]
        except Exception as exc:  # noqa: BLE001
            report['store_error'] = str(exc)[:200]
        return report

    def reset(self):
        super().reset()
        with self._cond:
            self._waiting.clear()
            self._alive_at = None
            self._alive_cache = []


def configure(cursor, listen_connect):
    """Сделать очередь процесса общей (до сборки ручки коннектора). Рубильник выключен —
    очередь остаётся в памяти процесса."""
    if not enabled():
        log.warning('Очередь Oktell: %s=0 — очередь в памяти процесса, годится только для одного процесса',
                    SHARED_ENV)
        return jobs_mod.jobs
    queue = jobs_mod.use(SharedJobs(PgStore(cursor, listen_connect)))
    log.info('Очередь Oktell: общая для процессов портала (таблица oktell_jobs, канал %s)', CHANNEL)
    return queue
