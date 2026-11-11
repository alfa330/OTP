# -*- coding: utf-8 -*-
"""Исполнители запросов коннектора Oktell: задание портала → SELECT к базе Oktell → строки.

Зачем
-----
Снимок табло коннектор снимает сам, по такту. Остальные чтения базы Oktell портал делает по
требованию: отбивка линии раз в час, разбор перерывов, выгрузки статусов и звонков в «Учёт
часов», биллинг, выборка звонков на оценку. Раньше все они шли мосту Oktell — HTTP-прокси с
сырым SQL наружу; теперь их исполняет коннектор. Входящих портов у шлюза нет, поэтому задание
он забирает сам: длинным опросом `POST /api/oktell_live/agent/jobs`, а результат приносит
следующим таким же запросом.

Чему исполнитель НЕ верит
-------------------------
Порталу. В задании нет SQL — только имя набора каталога (`cdr/oktell_datasets.py`), параметры и
отпечаток текста. Текст запроса исполнитель строит сам, из каталога в своём образе; параметры
чистит каталог (даты, целые в границах, UUID, цифры номеров). Отпечаток построенного текста не
совпал с присланным — значит, портал и шлюз собраны из разных версий каталога: запрос не
исполняется, портал получает причину `catalog` и идёт к мосту. Имени нет в каталоге — `unknown`.
Что бы ни прислал портал, исполниться может только запрос из каталога.

Чем это бережно к базе телефонии
--------------------------------
  * исполнителей мало (по умолчанию два), у каждого одно постоянное соединение — параллельных
    запросов к базе не больше, чем исполнителей;
  * те же настройки сессии, что у снимка: без транзакции, без блокировок чтения, жертва
    взаимной блокировки — наш запрос; таймаут запроса — минута, драйвер шлёт серверу отмену;
  * ответ не длиннее тысячи строк (`SET ROWCOUNT`) — как у моста, на этом стоит постраничная
    логика портала;
  * учётке не дана таблица — исполнитель узнаёт об этом заранее (проверка `SELECT TOP 0`),
    сообщает порталу, и запросы с этой таблицей до базы не доходят.
"""

import json
import logging
import threading
import time

import requests

from cdr import oktell_datasets
from cdr_bridge import oktell_db

log = logging.getLogger('cdr_bridge.oktell_jobs')

JOBS_PATH = '/api/oktell_live/agent/jobs'

# Сколько просим портал держать опрос, если заданий нет, и сколько ждём ответа сами.
POLL_WAIT_SECONDS = 20
POLL_TIMEOUT = (10, POLL_WAIT_SECONDS + 20)
# Отправка результата не ждёт нового задания дольше, чем обычный опрос.
PORTAL_BACKOFF_SECONDS = (2, 5, 15, 30, 60)

# Таймаут одного запроса к базе. Портал ждёт ответа минуту (OKTELL_API_TIMEOUT_SECONDS) —
# дольше исполнять незачем: результат уже никто не ждёт.
QUERY_TIMEOUT_SECONDS = 60
# Учётка не подошла: вход не повторяем чаще раза в пять минут (политика блокировки учётки).
AUTH_PAUSE_SECONDS = 300
# База недоступна: не открываем соединение на каждое задание подряд.
UNAVAILABLE_PAUSE_SECONDS = 10
# Как часто перепроверять, какие таблицы каталога даны учётке (выдали право — увидим сами).
PROBE_SECONDS = 600
SUMMARY_SECONDS = 600
DEADLOCK_RETRY_PAUSE = 0.5
# Пустой ответ портала быстрее этого — пауза до этого срока: опрос не должен стать частым.
MIN_IDLE_SECONDS = 1.0

DEFAULT_WORKERS = 2
MAX_WORKERS = 4

REASON_BY_KIND = {'permission': 'permission', 'auth': 'unavailable', 'unavailable': 'unavailable',
                  'timeout': 'timeout'}


def clamp_workers(value, default=DEFAULT_WORKERS):
    try:
        count = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(MAX_WORKERS, count))


def probe_tables(db):
    """Какие таблицы каталога учётке НЕ даны: список ключей `oktell_datasets.TABLES`.

    `SELECT TOP 0` строк не читает; без права SELECT он падает на разборе. Любой другой отказ
    (база недоступна) — исключение наружу: «не знаем» нельзя выдавать за «не дано»."""
    denied = []
    for key in sorted(oktell_datasets.TABLES):
        try:
            db.query(oktell_datasets.table_probe_sql(key))
        except oktell_db.OktellDbError as exc:
            if exc.kind != 'permission':
                raise
            denied.append(key)
    return denied


def execute(db, job):
    """Одно задание портала → результат для ручки заданий. Исключений не бросает."""
    job = job if isinstance(job, dict) else {}
    job_id = str(job.get('id') or '')[:64]
    name = job.get('dataset')
    try:
        query = oktell_datasets.build(name, job.get('params'))
    except ValueError as exc:
        known = isinstance(name, str) and name in oktell_datasets.DATASETS
        return {'id': job_id, 'ok': False, 'reason': 'params' if known else 'unknown',
                'error': str(exc)[:300]}
    if query.digest != job.get('digest'):
        return {'id': job_id, 'ok': False, 'reason': 'catalog',
                'error': 'каталог коннектора %s' % oktell_datasets.fingerprint()}
    started = time.monotonic()
    was_connected = bool(getattr(db, 'connected', False))
    try:
        try:
            rows = db.query(query)
        except oktell_db.OktellDbError as exc:
            if exc.kind == 'deadlock':
                # Жертва взаимной блокировки — наш запрос (DEADLOCK_PRIORITY LOW); станция
                # свою запись уже сделала. Один повтор по новому соединению.
                time.sleep(DEADLOCK_RETRY_PAUSE)
            elif exc.kind == 'unavailable' and was_connected:
                # Постоянное соединение умерло за время простоя (ночь, перезапуск службы базы):
                # читатель его уже закрыл — один повтор сразу, по новому соединению. Если не
                # откроется и оно, базы действительно нет.
                pass
            else:
                raise
            rows = db.query(query)
    except oktell_db.OktellDbError as exc:
        return {'id': job_id, 'ok': False, 'reason': REASON_BY_KIND.get(exc.kind, 'db'),
                'error': str(exc)[:300], 'kind': exc.kind}
    return {'id': job_id, 'ok': True, 'rows': rows,
            'query_ms': int((time.monotonic() - started) * 1000)}


class Worker:
    """Один исполнитель: своё соединение с базой, своя сессия к порталу, свой поток."""

    def __init__(self, index, portal, session, signer, db, hello, stop_event, clock=time.monotonic,
                 sleep=None):
        self.index = int(index)
        self.portal = portal
        self.session = session
        self.signer = signer
        self.db = db
        self.hello = hello
        self.stop_event = stop_event
        self.clock = clock
        self._sleep = sleep or (lambda seconds: stop_event.wait(seconds))
        self.unreadable = None      # None — ещё не проверяли
        self._probe_at = None
        self._db_blocked_until = 0.0
        self._db_blocked_reason = ''
        self._portal_failures = 0
        self._summary_at = None
        self._done = self._failed = 0
        self._query_ms = 0.0
        self.thread = None

    # ── портал ───────────────────────────────────────────────────────────────

    def _post(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

        def send():
            headers = {'Content-Type': 'application/json'}
            headers.update(self.signer.headers('POST', JOBS_PATH, body))
            return self.session.post(self.portal + JOBS_PATH, data=body, timeout=POLL_TIMEOUT,
                                     headers=headers)
        try:
            response = send()
        except requests.exceptions.ConnectionError as exc:
            if isinstance(exc, requests.exceptions.Timeout):
                raise
            # Соединение из пула закрыли на той стороне — один повтор сразу, по новому.
            response = send()
        if response.status_code == 401:
            raise RuntimeError('портал не принял подпись исполнителя (ключ %s)' % self.signer.key_id)
        if response.status_code == 404:
            raise RuntimeError('портал не знает ручку заданий — он старше коннектора')
        if response.status_code != 200:
            raise RuntimeError('портал ответил %d: %s' % (response.status_code, (response.text or '')[:200]))
        try:
            reply = response.json()
        except ValueError as exc:
            raise RuntimeError('портал вернул не JSON') from exc
        if not isinstance(reply, dict):
            raise RuntimeError('портал вернул не объект')
        return reply

    def _payload(self, done):
        body = dict(self.hello())
        body.update({'worker': self.index, 'catalog': oktell_datasets.fingerprint(),
                     'datasets': list(oktell_datasets.NAMES),
                     'unreadable': list(self.unreadable or ()),
                     'done': done, 'wait': POLL_WAIT_SECONDS})
        return body

    # ── база ─────────────────────────────────────────────────────────────────

    def probe(self, force=False):
        """Перепроверить, какие таблицы даны учётке. Не чаще раза в PROBE_SECONDS."""
        now = self.clock()
        if not force and self._probe_at is not None and now - self._probe_at < PROBE_SECONDS:
            return
        if now < self._db_blocked_until:
            return
        self._probe_at = now
        try:
            denied = probe_tables(self.db)
        except oktell_db.OktellDbError as exc:
            self._block_db(exc)
            return
        if self.unreadable is None or set(denied) != set(self.unreadable):
            if denied:
                log.warning('исполнитель %d: учётке базы не даны таблицы каталога: %s', self.index,
                            ', '.join(oktell_datasets.TABLES[key][0] for key in denied))
            else:
                log.info('исполнитель %d: учётке базы даны все таблицы каталога (%d)', self.index,
                         len(oktell_datasets.TABLES))
        self.unreadable = denied

    def _block_db(self, exc):
        pause = AUTH_PAUSE_SECONDS if exc.kind == 'auth' else UNAVAILABLE_PAUSE_SECONDS
        self._db_blocked_until = self.clock() + pause
        self._db_blocked_reason = str(exc)[:300]

    def execute(self, job):
        """Задание → результат; после отказа входа или связи базу какое-то время не трогаем."""
        now = self.clock()
        if now < self._db_blocked_until:
            return {'id': str((job or {}).get('id') or '')[:64], 'ok': False, 'reason': 'unavailable',
                    'error': self._db_blocked_reason}
        result = execute(self.db, job)
        kind = result.pop('kind', None)
        if result.get('ok'):
            self._done += 1
            self._query_ms += result.get('query_ms') or 0
            return result
        self._failed += 1
        if kind in ('auth', 'unavailable'):
            self._block_db(oktell_db.OktellDbError(result.get('error') or '', kind=kind))
        elif kind == 'permission':
            # Право отозвали или базу куба пересоздали — перепроверим таблицы следующим заходом.
            self._probe_at = None
        return result

    # ── цикл ─────────────────────────────────────────────────────────────────

    def _summary(self):
        now = self.clock()
        if self._summary_at is None:
            self._summary_at = now
            return
        if now - self._summary_at < SUMMARY_SECONDS:
            return
        if self._done or self._failed:
            log.info('исполнитель %d за %d мин: запросов исполнено %d, отказов %d, в среднем %.0f мс',
                     self.index, int((now - self._summary_at) / 60), self._done, self._failed,
                     (self._query_ms / self._done) if self._done else 0.0)
        self._summary_at = now
        self._done = self._failed = 0
        self._query_ms = 0.0

    def step(self, done):
        """Один заход: отдать результаты, получить задания, исполнить. Возвращает новые
        результаты (их унесёт следующий заход). Исключение — портал недоступен."""
        self.probe()
        started = self.clock()
        reply = self._post(self._payload(done))
        jobs = reply.get('jobs')
        results = []
        for job in jobs if isinstance(jobs, list) else []:
            if self.stop_event.is_set():
                break
            results.append(self.execute(job))
        if not results:
            # Портал обязан держать пустой опрос (длинный опрос). Ответил пусто сразу — не
            # крутим запросы без паузы, что бы ни было причиной.
            spare = MIN_IDLE_SECONDS - (self.clock() - started)
            if spare > 0:
                self._sleep(spare)
        return results

    def run(self):
        done = []
        while not self.stop_event.is_set():
            try:
                done = self.step(done)
            except Exception as exc:  # noqa: BLE001
                # Результаты прошлого захода ждать некому: портал отсчитывает свой таймаут, а
                # повторный опрос принесёт их слишком поздно. Выбрасываем.
                done = []
                self._portal_failures += 1
                pause = PORTAL_BACKOFF_SECONDS[min(self._portal_failures, len(PORTAL_BACKOFF_SECONDS)) - 1]
                if self._portal_failures in (1, 5) or self._portal_failures % 20 == 0:
                    log.warning('исполнитель %d: портал недоступен (%s), повтор через %d с',
                                self.index, exc, pause)
                self._sleep(pause)
            else:
                if self._portal_failures:
                    log.info('исполнитель %d: портал снова отвечает (после %d отказов)',
                             self.index, self._portal_failures)
                self._portal_failures = 0
            self._summary()
        self.db.close()

    def start(self):
        self.thread = threading.Thread(target=self.run, name='oktell-jobs-%d' % self.index, daemon=True)
        self.thread.start()
        return self.thread


def build_workers(config, count, session_factory, signer, hello, stop_event):
    """Исполнители по настройкам коннектора: у каждого своя сессия и своё соединение с базой."""
    workers = []
    for index in range(clamp_workers(count)):
        db = oktell_db.from_config(config, query_timeout=QUERY_TIMEOUT_SECONDS,
                                   row_limit=oktell_datasets.MAX_ROWS,
                                   app_name='%s-jobs' % oktell_db.APP_NAME)
        workers.append(Worker(index, config['portal'], session_factory(), signer, db, hello, stop_event))
    return workers
