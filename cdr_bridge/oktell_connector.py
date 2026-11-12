# -*- coding: utf-8 -*-
"""Коннектор Oktell: база Oktell → портал. Снимок «Табло СЗоВ · Линия» и запросы по требованию.

Зачем
-----
Портал читал базу Oktell через мост — HTTP-прокси `/query`, выставленный наружу из
корпоративной сети. Мост чужой и хрупкий (вход в базу на каждое соединение, отказы под
нагрузкой), а 10.11.2026 в 16:32 лёг совсем: табло, отбивка и выгрузки остались без данных.
VM шлюза стоит в одной сети с базой Oktell, поэтому снимок табло теперь снимает она сама и
присылает порталу — тем же способом, каким мост «Касаний» носит звонки, а коннектор АТС —
живые события станции.

Как устроено
------------
    база Oktell (MS SQL) ◄── один SELECT в такт ── коннектор (VM шлюза)
                                                     └─ POST /api/oktell_live/agent/push ──► портал

Раз в такт (10 секунд, портал может назвать другой) коннектор исполняет ОДИН запрос снимка
(`cdr/oktell_wallboard.py` — тот же текст, что портал слал мосту) и отправляет строки порталу
подписанным запросом. Соединение с базой постоянное, соединение с порталом — keep-alive через
прокси шлюза. Входящих портов нет: соединение всегда открывает коннектор.

Запросы по требованию (с 1.1.0)
-------------------------------
Остальные чтения базы Oktell — отбивка линии, разбор перерывов, выгрузки статусов и звонков,
биллинг, выборка звонков на оценку — портал делает, когда они ему нужны. Их исполняют
исполнители (`cdr_bridge/oktell_jobs.py`): отдельные потоки со своим соединением к базе, которые
забирают задание длинным опросом `POST /api/oktell_live/agent/jobs`. В задании имя набора
каталога `cdr/oktell_datasets.py` и параметры; текст запроса исполнитель строит сам.

Записи разговоров (с 1.2.0)
---------------------------
Файлы записей портал брал у моста Oktell, а мост лежит с 10.11.2026. Теперь их приносят те же
исполнители: задание `{kind: 'record', paths: [...]}` — относительные пути к файлу, которые портал
вычислил из базы; исполнитель скачивает файл с веб-сервера Oktell (каталог записей лежит в его
папке `WWW`) по HTTPS с закреплённым отпечатком сертификата и отдаёт порталу следующим опросом
(`cdr_bridge/oktell_records.py`).

Чему коннектор НЕ верит
-----------------------
Порталу — не безусловно. Текст запроса строит сам коннектор; от портала приходят только
параметры: у снимка — порог SL числом и ключи «Реестра тестовых номеров» цифрами, у запросов по
требованию — имя набора каталога и его параметры (даты, целые в границах, идентификаторы, цифры
номеров). Все они чистятся перед подстановкой. Ничего, что прислал портал, не исполняется как
SQL: исполниться может только запрос из каталога в образе шлюза. Учётка базы — только на
чтение. HTTPS_PROXY и подобные переменные не читаются — наружу только через прокси из настроек
(как у моста).

Что будет, если что-то лежит
----------------------------
  * база не отвечает — коннектор сообщает об этом порталу (табло уходит на мост Oktell, а если
    и его нет — показывает последний снимок с пометкой) и пробует снова с растущей паузой;
    при отказе учётки — раз в пять минут, чтобы её не заперла политика блокировки;
  * портал не отвечает — пауза растёт до минуты; базу в это время коннектор не трогает чаще,
    чем раз в паузу;
  * сменились параметры (правка реестра тестовых номеров) — снимок переснимается сразу.

Запуск
------
    python -m cdr_bridge.oktell_connector           рабочий режим (служба oktell-connector)
    python -m cdr_bridge.oktell_connector --once    один такт и выход
    python -m cdr_bridge.oktell_connector --check   проверить базу и портал, ничего не меняя
    python -m cdr_bridge.oktell_connector --check --datasets
                                                    то же и каждый набор каталога узким окном
    python -m cdr_bridge.oktell_connector --check --record <ГГГГММДД/ЧЧММ/mix_….mp3>
                                                    то же и скачать одну запись (размер, формат)

Настройки — окружение (или .env.codex.local при отладке):
    CDR_BRIDGE_PORTAL, CDR_AGENT_KEY_FILE | CDR_AGENT_PRIVATE_KEY, CDR_PORTAL_PROXY,
    CDR_PORTAL_CA_BUNDLE          — как у моста;
    OKTELL_SQL_HOST / _PORT       сервер базы Oktell, 1433
    OKTELL_SQL_USER / _PASSWORD   учётка ТОЛЬКО НА ЧТЕНИЕ
    OKTELL_SQL_DB                 база по умолчанию (oktell)
    OKTELL_SQL_ENCRYPTION         require (по умолчанию) | request | off
    OKTELL_LIVE_INTERVAL_SECONDS  такт, пока портал не назвал свой (10)
    OKTELL_JOB_WORKERS            исполнителей запросов по требованию (2; 0 — выключены)
    OKTELL_RECORD_URL             каталог записей на веб-сервере Oktell:
                                  https://<сервер>/data/<guid>/record/ (пусто — записи не носим)
    OKTELL_RECORD_TLS_SHA256      отпечаток SHA-256 сертификата веб-сервера Oktell (для https)
    OKTELL_HEARTBEAT_FILE         файл пульса для проверки живости контейнера
"""

import argparse
import json
import logging
import os
import platform
import signal
import socket
import sys
import threading
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr import oktell_datasets, oktell_wallboard  # noqa: E402
from cdr_bridge import oktell_db, oktell_jobs, oktell_records  # noqa: E402
from cdr_bridge.agent import Bridge, _env_file_values  # noqa: E402

VERSION = 'oktell/1.2.0'
log = logging.getLogger('cdr_bridge.oktell_connector')

PUSH_PATH = '/api/oktell_live/agent/push'
PORTAL_TIMEOUT = (10, 30)

DEFAULT_INTERVAL_SECONDS = 10.0
# Границы такта: чаще трёх секунд базу телефонии не опрашиваем, что бы ни назвал портал;
# реже двух минут — уже не «сейчас».
MIN_INTERVAL_SECONDS = 3.0
MAX_INTERVAL_SECONDS = 120.0

PORTAL_BACKOFF_SECONDS = (2, 5, 15, 30, 60)
DB_BACKOFF_SECONDS = (5, 15, 30, 60)
# Учётка не подошла: каждая попытка входа — запись в журнале SQL Server и шаг к блокировке.
DB_AUTH_BACKOFF_SECONDS = 300
# Сводка в журнал: раз в десять минут одна строка вместо строки на каждый такт.
SUMMARY_SECONDS = 600
CRASH_PAUSE_SECONDS = 15.0


def _clamp_interval(value, default=DEFAULT_INTERVAL_SECONDS):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return float(default)
    if seconds != seconds:  # NaN
        return float(default)
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, seconds))


def load_config():
    """Окружение важнее файла: на VM переменные ставит служба, файл нужен только при отладке."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dev = _env_file_values(os.path.join(root, '.env.codex.local'))

    def value(key, default=''):
        return (os.environ.get(key) or dev.get(key) or default).strip()

    return {
        'portal': value('CDR_BRIDGE_PORTAL').rstrip('/'),
        'key_file': value('CDR_AGENT_KEY_FILE'),
        'private_key': value('CDR_AGENT_PRIVATE_KEY'),
        'proxy': value('CDR_PORTAL_PROXY'),
        'ca_bundle': value('CDR_PORTAL_CA_BUNDLE'),
        'sql_host': value('OKTELL_SQL_HOST'),
        'sql_port': value('OKTELL_SQL_PORT', str(oktell_db.DEFAULT_PORT)),
        'sql_user': value('OKTELL_SQL_USER'),
        'sql_password': value('OKTELL_SQL_PASSWORD'),
        'sql_database': value('OKTELL_SQL_DB', oktell_db.DEFAULT_DATABASE),
        'sql_encryption': value('OKTELL_SQL_ENCRYPTION', 'require').lower(),
        'interval': _clamp_interval(value('OKTELL_LIVE_INTERVAL_SECONDS', str(DEFAULT_INTERVAL_SECONDS))),
        'job_workers': oktell_jobs.clamp_workers(value('OKTELL_JOB_WORKERS', str(oktell_jobs.DEFAULT_WORKERS))),
        'record_url': value('OKTELL_RECORD_URL'),
        'record_tls_sha256': value('OKTELL_RECORD_TLS_SHA256'),
        'heartbeat_file': value('OKTELL_HEARTBEAT_FILE'),
    }


def build_records(config):
    """Скачивание записей по настройкам или None. Ошибка настройки работу коннектора не
    останавливает — табло и запросы важнее, — но видна в журнале с первой строки."""
    try:
        return oktell_records.Records.from_config(config)
    except ValueError as exc:
        log.error('записи разговоров выключены: %s', exc)
        return None


class Connector:
    def __init__(self, config, session=None, db=None, clock=time.time, jobs_db=None, records=False):
        self.config = config
        self.portal = config['portal']
        self.session = session or Bridge._build_session(config)
        self.signer = Bridge._build_signer(config)
        self.db = db if db is not None else oktell_db.from_config(config)
        # Соединение для запросов по требованию — не то, которым снимается табло: у него свой
        # таймаут (минута, а не 15 с) и потолок строк ответа. Подставленный читатель (тесты)
        # служит обоим.
        self._jobs_db = jobs_db or ((lambda: db) if db is not None else self._open_jobs_db)
        self.clock = clock
        # records=False — взять из настроек; тесты подставляют своё (или None).
        self.records = build_records(config) if records is False else records
        self.agent_id = '%s-%d' % (socket.gethostname()[:60], os.getpid())
        # Параметры снимка называет портал; пока не спросили — None, и базу не трогаем.
        self.params = None
        self.interval = _clamp_interval(config.get('interval'))
        self.db_error = ''
        self.db_failures = 0
        self.portal_failures = 0
        self.stop_event = threading.Event()
        self.workers = []
        self._privileges_checked = False
        self._summary_at = None
        self._taken = 0
        self._accepted = 0
        self._refused = 0
        self._failed = 0
        self._query_ms = 0.0

    # ── служебное ────────────────────────────────────────────────────────────

    def _open_jobs_db(self):
        return oktell_db.from_config(self.config, query_timeout=oktell_jobs.QUERY_TIMEOUT_SECONDS,
                                     row_limit=oktell_datasets.MAX_ROWS,
                                     app_name='%s-jobs' % oktell_db.APP_NAME)

    def _hello(self):
        return {'agent_id': self.agent_id, 'hostname': socket.gethostname(),
                'version': '%s/%s' % (VERSION, platform.python_version())}

    def _payload(self, snapshot):
        body = dict(self._hello())
        # Состояние базы — три значения: читается, не читается (с причиной) или ещё не
        # пробовали (None). Первый запрос уходит до первого чтения базы, и «не читается» в нём
        # было бы ложной тревогой на портале при каждом запуске.
        if self.db.connected:
            connected = True
        else:
            connected = False if self.db_error else None
        body['db'] = {'host': self.db.describe(), 'connected': connected,
                      'error': self.db_error or None}
        body['wallboard'] = snapshot
        return body

    def _heartbeat(self):
        """Пульс — только после снимка, который портал принял: «процесс запущен» не признак
        здоровья, коннектор без базы или без портала обязан считаться больным."""
        path = self.config.get('heartbeat_file')
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(str(int(self.clock())))
        except OSError as exc:
            log.warning('пульс не записался: %s', exc)

    # ── портал ───────────────────────────────────────────────────────────────

    def _send(self, body):
        # Подпись — на каждую отправку заново: одноразовый номер второй раз портал не примет.
        headers = {'Content-Type': 'application/json'}
        headers.update(self.signer.headers('POST', PUSH_PATH, body))
        return self.session.post(self.portal + PUSH_PATH, data=body, timeout=PORTAL_TIMEOUT,
                                 headers=headers)

    def _post(self, payload):
        if self.signer is None:
            raise RuntimeError('у коннектора нет ключа подписи (CDR_AGENT_KEY_FILE)')
        # Тело сериализуем сами и отправляем байтами: подписывается хеш ровно тех байтов,
        # что уйдут в сеть.
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        try:
            response = self._send(body)
        except requests.exceptions.ConnectionError as exc:
            if isinstance(exc, requests.exceptions.Timeout):
                raise
            # Соединение из пула закрыли на той стороне (простой, перезапуск портала, прокси):
            # один повтор сразу, по новому соединению. Снимок от повтора не портится — портал
            # просто кладёт его ещё раз. Второй отказ — уже не протухший сокет: пауза.
            response = self._send(body)
        if response.status_code == 401:
            raise RuntimeError('портал не принял подпись: ключ %s не внесён в CDR_AGENT_KEYS или '
                               'разошлись часы' % self.signer.key_id)
        if response.status_code != 200:
            raise RuntimeError('портал ответил %d: %s' % (response.status_code, (response.text or '')[:200]))
        try:
            reply = response.json()
        except ValueError as exc:
            raise RuntimeError('портал вернул не JSON: %s' % (response.text or '')[:200]) from exc
        if not isinstance(reply, dict):
            raise RuntimeError('портал вернул не объект')
        return reply

    def _apply_reply(self, reply):
        """Параметры и такт из ответа портала. True — параметры снимка сменились."""
        if 'interval_seconds' in reply:
            self.interval = _clamp_interval(reply.get('interval_seconds'), self.interval)
        if not isinstance(reply.get('params'), dict):
            return False
        params = oktell_wallboard.clean_params(reply['params'])
        changed = self.params is not None and params != self.params
        if self.params is None or changed:
            log.info('параметры снимка: порог SL %d с, тестовых номеров в реестре %d',
                     params['sl_seconds'], len(params['test_keys']))
        self.params = params
        return changed

    def _portal_failed(self, exc):
        self.portal_failures += 1
        pause = PORTAL_BACKOFF_SECONDS[min(self.portal_failures, len(PORTAL_BACKOFF_SECONDS)) - 1]
        if self.portal_failures in (1, 5) or self.portal_failures % 20 == 0:
            log.warning('портал недоступен (%s), повтор через %d с', exc, pause)
        return float(pause)

    def _portal_ok(self):
        if self.portal_failures:
            log.info('портал снова отвечает (после %d отказов)', self.portal_failures)
        self.portal_failures = 0

    # ── база ─────────────────────────────────────────────────────────────────

    def take(self):
        """Снять снимок: один SELECT, параметры — те, что назвал портал."""
        params = self.params or oktell_wallboard.clean_params(None)
        sql = oktell_wallboard.snapshot_sql(params['sl_seconds'], params['test_keys'])
        started = time.monotonic()
        rows = self.db.query(sql)
        query_ms = int((time.monotonic() - started) * 1000)
        if not rows:
            # Запрос всегда отдаёт хотя бы строку итогов (LEFT JOIN): пусто — это не «ночь».
            raise oktell_db.OktellDbError('запрос снимка вернул пустой ответ')
        return {'rows': rows, 'params': params, 'taken_at': self.clock(), 'query_ms': query_ms}

    def _db_failed(self, exc):
        self.db_failures += 1
        self.db_error = str(exc)[:300]
        if getattr(exc, 'auth', False):
            pause = DB_AUTH_BACKOFF_SECONDS
        else:
            pause = DB_BACKOFF_SECONDS[min(self.db_failures, len(DB_BACKOFF_SECONDS)) - 1]
        if self.db_failures in (1, 5) or self.db_failures % 20 == 0:
            log.warning('база Oktell: %s — повтор через %d с', exc, pause)
        return float(pause)

    def _db_ok(self):
        if self.db_failures:
            log.info('база Oktell снова отвечает (после %d отказов)', self.db_failures)
        self.db_failures = 0
        self.db_error = ''

    def _check_privileges_once(self):
        """Раз за жизнь процесса: умеет ли учётка базы больше чтения.

        Работу это не останавливает — табло важнее, — но должно быть видно каждому, кто
        откроет журнал: административной учётке на шлюзе не место."""
        if self._privileges_checked:
            return
        self._privileges_checked = True
        try:
            extra = self.db.write_permissions()
        except oktell_db.OktellDbError as exc:
            log.warning('права учётки базы не проверились: %s', exc)
            return
        if extra:
            log.warning('учётка базы Oktell умеет больше чтения (%s) — замените её на учётку '
                        'только на чтение', ', '.join(extra))
        else:
            log.info('учётка базы Oktell: только чтение')

    # ── такт ─────────────────────────────────────────────────────────────────

    def cycle(self):
        """Один такт. Возвращает, сколько секунд ждать до следующего."""
        started = time.monotonic()
        if self.params is None:
            # Сначала — параметры: без них первый снимок был бы снят без реестра тестовых
            # номеров, и портал бы его не принял. Заодно не трогаем базу, пока портала нет.
            try:
                self._apply_reply(self._post(self._payload(None)))
            except Exception as exc:  # noqa: BLE001
                return self._portal_failed(exc)
            self._portal_ok()
            if self.params is None:
                # Старый портал или ответ без параметров: работаем с параметрами по умолчанию.
                self.params = oktell_wallboard.clean_params(None)

        snapshot, db_pause = None, 0.0
        try:
            snapshot = self.take()
        except oktell_db.OktellDbError as exc:
            db_pause = self._db_failed(exc)
            self._failed += 1
        else:
            self._db_ok()
            self._taken += 1
            self._query_ms += snapshot['query_ms']

        try:
            reply = self._post(self._payload(snapshot))
        except Exception as exc:  # noqa: BLE001
            return max(self._portal_failed(exc), db_pause)
        self._portal_ok()
        changed = self._apply_reply(reply)

        if snapshot is None:
            return db_pause
        if reply.get('accepted'):
            self._accepted += 1
            self._heartbeat()
            self._check_privileges_once()
        else:
            self._refused += 1
            if changed:
                # Портал сменил параметры (правка реестра тестовых номеров) — переснять сразу.
                return 0.0
            if self._refused in (1, 5) or self._refused % 50 == 0:
                log.warning('портал не принял снимок: %s', reply.get('reason') or 'причина не названа')
        return max(0.0, self.interval - (time.monotonic() - started))

    def _summary(self):
        now = time.monotonic()
        if self._summary_at is None:
            self._summary_at = now
            return
        if now - self._summary_at < SUMMARY_SECONDS:
            return
        average = (self._query_ms / self._taken) if self._taken else 0.0
        log.info('за %d мин: снимков снято %d, принято порталом %d, не принято %d, отказов базы %d, '
                 'запрос в среднем %.0f мс, такт %.0f с',
                 int((now - self._summary_at) / 60), self._taken, self._accepted, self._refused,
                 self._failed, average, self.interval)
        if self.records is not None:
            stats = self.records.take_stats()
            if stats['found'] or stats['missing'] or stats['failed']:
                log.info('записи за %d мин: принесено %d (%.1f МБ, в среднем %.0f мс), нет файла %d, '
                         'отказов %d', int((now - self._summary_at) / 60), stats['found'],
                         stats['bytes'] / 1048576.0,
                         (stats['ms'] / stats['found']) if stats['found'] else 0.0,
                         stats['missing'], stats['failed'])
        self._summary_at = now
        self._taken = self._accepted = self._refused = self._failed = 0
        self._query_ms = 0.0

    def start_workers(self):
        """Исполнители запросов по требованию — отдельными потоками, каждый со своим
        соединением к базе и своей сессией к порталу. Снимок табло от них не зависит."""
        count = oktell_jobs.clamp_workers(self.config.get('job_workers'))
        if not count or self.signer is None:
            return []
        self.workers = oktell_jobs.build_workers(
            self.config, count, lambda: Bridge._build_session(self.config), self.signer,
            self._hello, self.stop_event, records=self.records)
        for worker in self.workers:
            worker.start()
        log.info('исполнителей запросов: %d, наборов в каталоге: %d, отпечаток каталога %s, записи: %s',
                 len(self.workers), len(oktell_datasets.NAMES), oktell_datasets.fingerprint(),
                 self.records.describe() if self.records is not None else 'не настроены')
        return self.workers

    def run(self):
        log.info('Коннектор Oktell %s запущен. Портал: %s (ключ %s), база: %s, такт %.0f с, id: %s',
                 VERSION, self.portal, self.signer.key_id if self.signer else '—',
                 self.db.describe(), self.interval, self.agent_id)
        self.start_workers()
        while not self.stop_event.is_set():
            try:
                wait = self.cycle()
            except Exception:  # noqa: BLE001
                log.exception('такт упал')
                wait = CRASH_PAUSE_SECONDS
            self._summary()
            if self.stop_event.wait(wait):
                break
        self.db.close()
        log.info('Коннектор Oktell остановлен')
        return 0

    def stop(self, *_args):
        self.stop_event.set()


def check_tables(db):
    """Какие таблицы каталога учётке даны — строками для человека. Возвращает ключи недоступных."""
    denied = oktell_jobs.probe_tables(db)
    names = [oktell_datasets.TABLES[key][0] for key in sorted(oktell_datasets.TABLES)]
    if not denied:
        print('таблицы  даны все %d: %s' % (len(names), ', '.join(names)))
        return denied
    print('таблицы  НЕ даны %d из %d: %s' % (len(denied), len(names),
                                             ', '.join(oktell_datasets.TABLES[key][0] for key in denied)))
    blocked = sorted(name for name in oktell_datasets.NAMES
                     if set(oktell_datasets.tables_of(name)) & set(denied))
    print('         без них не исполнятся наборы (или часть их разрезов): %s' % ', '.join(blocked))
    return denied


def check_datasets(db, denied=()):
    """Каждый набор каталога узким окном (сегодня) — что запрос разбирается и исполняется.
    Наборы с недоступной таблицей пропускаются. Возвращает число отказов."""
    failures = 0
    for name, params in sorted(oktell_datasets.samples().items()):
        if set(oktell_datasets.tables_of(name, oktell_datasets.build(name, params).params)) & set(denied):
            print('  —  %-26s пропущен: таблица не дана' % name)
            continue
        result = oktell_jobs.execute(db, {'id': name, 'dataset': name, 'params': params,
                                          'digest': oktell_datasets.build(name, params).digest})
        if result.get('ok'):
            print('  ок %-26s %4d строк за %5d мс' % (name, len(result['rows']), result['query_ms']))
        else:
            failures += 1
            print('  ✗  %-26s %s: %s' % (name, result.get('reason'), result.get('error')))
    return failures


def check_records(connector, record_path=None):
    """Записи разговоров: веб-сервер Oktell отвечает, сертификат совпал, путь, которого нет,
    даёт 404. С record_path — ещё и скачать эту запись. Возвращает True, если всё в порядке."""
    if connector.records is None:
        if connector.config.get('record_url'):
            print('записи   НЕ настроены: ошибка в OKTELL_RECORD_URL / OKTELL_RECORD_TLS_SHA256 '
                  '(см. журнал выше)')
            return False
        print('записи   не настроены (OKTELL_RECORD_URL) — пропущены')
        return not record_path
    try:
        print('записи   %s' % connector.records.probe())
        if record_path:
            found = connector.records.fetch(record_path)
            if found is None:
                print('запись   %s — файла нет (404)' % record_path)
                return False
            body, content_type = found
            print('запись   %s — %d байт, %s, %s' % (record_path, len(body), content_type,
                                                    oktell_records.audio_format(body)))
    except (oktell_records.RecordError, ValueError) as exc:
        print('записи   НЕ работают: %s' % exc)
        return False
    return True


def check(connector, require_readonly=False, datasets=False, record_path=None):
    """Проверка установки: база читается, учётка только читает, портал принимает подпись,
    веб-сервер Oktell отдаёт записи. Ничего не меняет: снимок на портал не отправляется,
    уходит только «я на связи».

    require_readonly — учётка, умеющая больше чтения, считается отказом (так проверяет
    выкладка на шлюз); без флага о ней только сказано. datasets — исполнить ещё и каждый
    набор каталога узким окном. record_path — скачать ещё и эту запись."""
    ok = check_records(connector, record_path)
    print('ключ     %s' % (connector.signer.key_id if connector.signer else 'НЕ ЗАДАН'))
    try:
        snapshot = connector.take()
        first = snapshot['rows'][0]
        operators = sum(1 for row in snapshot['rows'] if str(row.get('operator_name') or '').strip())
        print('база     %s — отвечает за %d мс: время Oktell %s, дошло до очереди сегодня %s, '
              'принято %s, в очереди сейчас %s, операторов на линии %d'
              % (connector.db.describe(), snapshot['query_ms'], first.get('oktell_now'),
                 first.get('arrived'), first.get('served'), first.get('queue_now'), operators))
        extra = connector.db.write_permissions()
        if extra:
            print('учётка   умеет больше чтения: %s — на шлюзе нужна учётка только на чтение'
                  % ', '.join(extra))
            if require_readonly:
                ok = False
        else:
            print('учётка   только чтение')
        # Запросы по требованию: какие таблицы каталога даны учётке (и, по просьбе, каждый
        # набор узким окном). Отдельным соединением — с тем же потолком строк, что у исполнителя.
        jobs_db = connector._jobs_db()
        try:
            print('каталог  наборов %d, отпечаток %s' % (len(oktell_datasets.NAMES),
                                                         oktell_datasets.fingerprint()))
            denied = check_tables(jobs_db)
            if datasets and check_datasets(jobs_db, denied):
                ok = False
        finally:
            jobs_db.close()
    except oktell_db.OktellDbError as exc:
        ok = False
        print('база     %s — НЕ читается: %s' % (connector.db.describe(), exc))
    finally:
        connector.db.close()
    if not connector.portal:
        print('портал   не задан (CDR_BRIDGE_PORTAL) — пропущен')
        return 0 if ok else 1
    try:
        reply = connector._post(connector._payload(None))
        params = oktell_wallboard.clean_params(reply.get('params'))
        print('портал   %s — принял: порог SL %d с, тестовых номеров %d, такт %s с'
              % (connector.portal, params['sl_seconds'], len(params['test_keys']),
                 reply.get('interval_seconds')))
    except Exception as exc:  # noqa: BLE001
        ok = False
        print('портал   %s — НЕ принял: %s' % (connector.portal, exc))
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='Коннектор Oktell: снимок табло СЗоВ → портал')
    parser.add_argument('--once', action='store_true', help='один такт и выход')
    parser.add_argument('--check', action='store_true',
                        help='проверить базу Oktell и портал, снимок не отправлять')
    parser.add_argument('--require-readonly', action='store_true',
                        help='вместе с --check: учётка базы, умеющая больше чтения, — отказ')
    parser.add_argument('--datasets', action='store_true',
                        help='вместе с --check: исполнить каждый набор каталога узким окном')
    parser.add_argument('--record', metavar='ПУТЬ',
                        help='вместе с --check: скачать одну запись по относительному пути')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)-7s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    # Журнал urllib3 на DEBUG печатает каждый запрос — в рабочем режиме это шум.
    logging.getLogger('urllib3').setLevel(logging.WARNING)

    config = load_config()
    if not config['sql_host'] or not config['sql_user']:
        print('Не задано: OKTELL_SQL_HOST / OKTELL_SQL_USER')
        return 2
    if not args.check and not config['portal']:
        print('Не задано: CDR_BRIDGE_PORTAL')
        return 2
    if not args.check and not (config['key_file'] or config['private_key']):
        print('Не задан ключ подписи (CDR_AGENT_KEY_FILE)')
        return 2
    try:
        connector = Connector(config)
    except ValueError as exc:
        print('Ключ подписи не годится: %s' % exc)
        return 2
    if args.check:
        return check(connector, require_readonly=args.require_readonly, datasets=args.datasets,
                     record_path=args.record)
    if args.once:
        wait = connector.cycle()
        connector.db.close()
        print('такт выполнен, следующий через %.0f с' % wait)
        return 0
    signal.signal(signal.SIGTERM, connector.stop)
    try:
        return connector.run()
    except KeyboardInterrupt:
        connector.db.close()
        log.info('Остановлен с клавиатуры')
        return 0


if __name__ == '__main__':
    sys.exit(main())
