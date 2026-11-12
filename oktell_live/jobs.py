# -*- coding: utf-8 -*-
"""Запросы портала к базе Oktell, которые исполняет коннектор на VM шлюза.

Зачем
-----
Мост Oktell — чужой HTTP-прокси `/query`, выставленный наружу, — исполнял любой присланный
текст и 10.11.2026 лёг. Коннектор на VM шлюза стоит в одной сети с базой и сам открывает
соединение с порталом; входящих портов у шлюза нет. Поэтому запрос едет «наоборот»: портал
кладёт задание в очередь, исполнитель коннектора забирает его длинным опросом, читает базу и
следующим же запросом приносит строки.

    вызов _oktell_query(Query)                      исполнитель на VM шлюза
          │  run(): задание в очередь  ◄────────────  POST /agent/jobs  (ждёт до 20 с)
          │  ждёт результат            ─── задание ─►  строит запрос сам, читает базу
          ▼  строки                    ◄── результат ─  POST /agent/jobs  (и ждёт следующее)

Что уходит на шлюз
------------------
Имя набора, его параметры и отпечаток текста — не SQL (`cdr/oktell_datasets.py`). Текст запроса
коннектор строит сам, из своей копии каталога, и исполняет, только если отпечаток совпал.
Обычная строка SQL без имени набора на шлюз не попадёт никак: `can_run` её не берёт.

Память процесса, а не таблица
-----------------------------
Задание живёт секунды и нужно только потоку, который его ждёт. Портал поднимается одним
процессом, поэтому очередь в памяти видят и ждущий поток, и ручка коннектора. Перезапуск портала
очередь теряет вместе с ждущими потоками — терять нечего.

Отказы
------
`Refused` — шлюз за запрос не взялся: исполнителя нет на связи, он не знает набор, каталоги
портала и шлюза разошлись, учётке базы не дано право на таблицу, база недоступна. Запрос при этом
не исполнялся — вызывающий может отдать его мосту Oktell.
`Failed` — запрос исполнялся и не удался (ошибка базы, таймаут) или ответа не дождались.
Мосту такой запрос не повторяют: это второй тяжёлый запрос к базе телефонии вслед первому.

Рубильник: `OKTELL_VM_QUERIES=0` — запросы на шлюз не уходят, только мосту, как до 11.11.2026.

Записи разговоров (с 12.11.2026)
--------------------------------
Той же очередью ездят задания «принеси файл записи»: `{id, kind: 'record', paths: [...]}` —
относительные пути, которые портал вычислил из базы (сначала .mp3, потом .wav). Исполнитель
скачивает файл с веб-сервера Oktell и приносит его следующим опросом в base64
(`cdr_bridge/oktell_records.py`). Портал берётся за такое задание, только если исполнитель на
связи сказал, что умеет (`records: true` в опросе); принесённое проверяет сам: путь — один из
запрошенных, тип — `audio/…`, base64 честный, размер не больше потолка. До 12.11.2026 файлы
брались только у моста Oktell, а он лежит с 10.11.2026 — «Случайный звонок» отвечал «Не удалось
получить аудиозапись из Oktell».
"""

import base64
import binascii
import collections
import logging
import math
import os
import threading
import time
import uuid

from cdr import oktell_datasets

log = logging.getLogger(__name__)

ENABLED_ENV = 'OKTELL_VM_QUERIES'
_FALSE = ('0', 'off', 'false', 'no', 'нет')

# Сколько длинный опрос исполнителя ждёт задание. Короче минуты — чтобы соединение через прокси
# шлюза и балансировщик Render не считалось зависшим.
POLL_WAIT_SECONDS = 20
# Исполнитель не опрашивал дольше — его нет: опрос повторяется каждые 20 с, а самый долгий
# запрос к базе обрывается через минуту.
WORKER_SILENT_SECONDS = 90
# Задание никто не забрал за это время, и никто ничего не исполняет — шлюз считаем недоступным
# и отдаём запрос мосту, не дожидаясь полного таймаута: свободный исполнитель забирает задание
# за доли секунды. Пока исполнители заняты другими запросами, задание ждёт своей очереди.
PICKUP_SECONDS = 12
# Очередь длиннее — что-то сломалось (исполнители стоят, а портал всё шлёт): новые не берём.
MAX_PENDING = 200
# Одинаковое предупреждение — не чаще раза в десять минут на набор и причину.
LOG_EVERY_SECONDS = 600

MAX_ROWS = oktell_datasets.MAX_ROWS
MAX_COLUMNS = 80
MAX_TEXT = 8000
MAX_DONE_PER_POLL = 8

# Причины отказа исполнителя, при которых запрос НЕ исполнялся, — их можно отдать мосту.
REFUSED_REASONS = ('catalog', 'unknown', 'params', 'permission', 'unavailable')

REASON_TEXT = {
    'catalog': 'каталог запросов на VM шлюза другой версии — коннектор Oktell нужно обновить',
    'unknown': 'коннектор Oktell на VM шлюза не знает такой набор — его нужно обновить',
    'params': 'коннектор Oktell на VM шлюза не принял параметры набора',
    'permission': 'учётке шлюза не выдано право читать таблицу базы Oktell '
                  '(права выдаёт администратор SQL Server)',
    'unavailable': 'коннектор на VM шлюза не достучался до базы Oktell',
    'timeout': 'база Oktell не ответила коннектору на VM шлюза вовремя',
    'db': 'база Oktell отклонила запрос коннектора на VM шлюза',
}


RECORD_KIND = 'record'
# Потолок файла записи — тот же, что у исполнителя (cdr_bridge/oktell_records.py): час с лишним
# MP3 Oktell; обычная запись — сотни килобайт.
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_RECORD_PATHS = 4
_MAX_RECORD_B64 = (MAX_RECORD_BYTES + 2) // 3 * 4

# Причины отказа исполнителя по записи, при которых файл не скачивался, — их можно отдать мосту.
RECORD_REFUSED_REASONS = ('unknown', 'params', 'unavailable')

RECORD_REASON_TEXT = {
    'unknown': 'коннектор Oktell на VM шлюза не умеет приносить записи разговоров — '
               'его нужно обновить или задать ему OKTELL_RECORD_URL',
    'params': 'коннектор Oktell на VM шлюза не принял путь к записи',
    'unavailable': 'коннектор на VM шлюза не достучался до веб-сервера Oktell',
    'timeout': 'веб-сервер Oktell не отдал запись коннектору на VM шлюза вовремя',
    'tls': 'сертификат веб-сервера Oktell не совпал с закреплённым на VM шлюза',
    'big': 'запись разговора больше потолка, который возит коннектор',
    'record': 'веб-сервер Oktell не отдал запись коннектору на VM шлюза',
}


class Refused(RuntimeError):
    """Шлюз за запрос не взялся; запрос не исполнялся."""


class Failed(RuntimeError):
    """Запрос ушёл на шлюз и не удался."""


def enabled():
    """Уходят ли запросы на VM шлюза. По умолчанию — да."""
    return str(os.environ.get(ENABLED_ENV) or '').strip().lower() not in _FALSE


def _clean_value(value):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:MAX_TEXT]
    raise ValueError('значение колонки — не число и не строка')


def clean_rows(raw):
    """Строки ответа исполнителя → список словарей с простыми значениями.

    ValueError — тело не похоже на ответ запроса: не список, строк больше потолка каталога,
    строка не объект, в значении что-то сложнее числа и строки."""
    if not isinstance(raw, list):
        raise ValueError('строки ответа — список')
    if len(raw) > MAX_ROWS:
        raise ValueError('строк в ответе больше %d' % MAX_ROWS)
    rows = []
    for item in raw:
        if not isinstance(item, dict) or len(item) > MAX_COLUMNS:
            raise ValueError('строка ответа — объект с колонками запроса')
        row = {}
        for key, value in item.items():
            if not isinstance(key, str) or len(key) > 128:
                raise ValueError('имя колонки — короткая строка')
            row[key] = _clean_value(value)
        rows.append(row)
    return rows


def record_result(item, paths):
    """Результат исполнителя по записи → {'ok': True, 'found': False} | {'ok': True, 'found': True,
    'path', 'content_type', 'audio': bytes} | {'ok': False, 'reason', 'error'}.

    Что принесли, проверяется здесь, что бы ни прислал исполнитель: путь — один из запрошенных,
    тип — audio/…, base64 честный и не длиннее потолка."""
    if not item.get('ok'):
        return {'ok': False, 'reason': str(item.get('reason') or 'record')[:20],
                'error': str(item.get('error') or '')[:400]}

    def bad(text):
        return {'ok': False, 'reason': 'record', 'error': 'ответ коннектора не похож на запись: %s' % text}

    # «Файла нет» — только явным found: false. Ответ без признака (чужой формат, строки
    # запроса вместо файла) — ошибка, а не тихое «записи нет».
    found = item.get('found')
    if found is False:
        return {'ok': True, 'found': False}
    if found is not True:
        return bad('нет признака found')
    path = item.get('path')
    if not isinstance(path, str) or path not in paths:
        return bad('файл не по запрошенному пути')
    content_type = str(item.get('content_type') or '').split(';', 1)[0].strip().lower()
    if not content_type.startswith('audio/') or len(content_type) > 60:
        return bad('тип %r' % content_type[:60])
    raw = item.get('audio')
    if not isinstance(raw, str) or not raw:
        return bad('нет файла')
    if len(raw) > _MAX_RECORD_B64:
        return {'ok': False, 'reason': 'big', 'error': 'файл больше %d байт' % MAX_RECORD_BYTES}
    try:
        audio = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return bad('base64 испорчен')
    if not audio:
        return bad('пустой файл')
    return {'ok': True, 'found': True, 'path': path, 'content_type': content_type, 'audio': audio}


class _Job:
    __slots__ = ('id', 'kind', 'dataset', 'params', 'digest', 'paths', 'created_at', 'deadline',
                 'taken_at', 'worker', 'done', 'result')

    def __init__(self, now, timeout, query=None, paths=None):
        self.id = uuid.uuid4().hex
        self.kind = RECORD_KIND if paths is not None else 'query'
        self.dataset = getattr(query, 'dataset', None)
        self.params = getattr(query, 'params', None)
        self.digest = getattr(query, 'digest', None)
        self.paths = tuple(paths) if paths is not None else None
        self.created_at = now
        self.deadline = now + timeout
        self.taken_at = None
        self.worker = None
        self.done = threading.Event()
        self.result = None

    @property
    def label(self):
        """Что это за задание — словами для журнала и сообщений."""
        return 'запись разговора' if self.kind == RECORD_KIND else 'набор %s' % self.dataset

    def view(self):
        if self.kind == RECORD_KIND:
            return {'id': self.id, 'kind': RECORD_KIND, 'paths': list(self.paths)}
        return {'id': self.id, 'dataset': self.dataset, 'params': self.params, 'digest': self.digest}


class Jobs:
    """Очередь заданий и то, что известно об исполнителях. Одна на процесс (`jobs` ниже)."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._cond = threading.Condition()
        self._pending = collections.deque()
        self._jobs = {}
        self._workers = {}
        self._logged = {}
        self._stats = collections.Counter()

    # ── исполнители ──────────────────────────────────────────────────────────

    def worker_seen(self, worker, *, version=None, catalog=None, datasets=None, unreadable=None,
                    records=None):
        """Исполнитель вышел на связь: что он умеет и какие таблицы его учётке не даны.

        records — умеет ли он приносить записи разговоров: True/False у коннектора с 1.2.0,
        None — коннектор старше и о записях не знает."""
        now = self._clock()
        names = None
        if isinstance(datasets, (list, tuple)):
            names = frozenset(str(name) for name in datasets[:500] if isinstance(name, str))
        denied = frozenset()
        if isinstance(unreadable, (list, tuple)):
            denied = frozenset(str(key) for key in unreadable[:200] if isinstance(key, str))
        can_records = records if isinstance(records, bool) else None
        with self._cond:
            previous = self._workers.get(worker)
            self._workers[worker] = {
                'seen_at': now,
                'waiting': previous['waiting'] if previous else 0,
                'version': str(version or '')[:60] or None,
                'catalog': str(catalog or '')[:32] or None,
                'datasets': names,
                'unreadable': denied,
                'records': can_records,
            }
            self._forget_silent_workers(now)
        if previous is None:
            mine = oktell_datasets.fingerprint()
            log.info('Исполнитель запросов Oktell на связи: %s, %s, наборов %s, каталог %s%s, записи %s',
                     worker, version, len(names) if names is not None else '—', catalog,
                     '' if catalog == mine else ' (у портала %s — версии разные)' % mine,
                     {True: 'носит', False: 'не настроены', None: 'не умеет'}[can_records])
        if denied and (previous is None or previous['unreadable'] != denied):
            log.warning('Коннектор Oktell: учётке базы не даны таблицы %s — наборы с ними идут '
                        'мосту Oktell', ', '.join(sorted(denied)))
        elif previous is not None and previous['unreadable'] and not denied:
            log.info('Коннектор Oktell: учётке базы теперь даны все таблицы каталога')

    def _forget_silent_workers(self, now):
        for key in [key for key, info in self._workers.items()
                    if not info['waiting'] and now - info['seen_at'] > WORKER_SILENT_SECONDS * 10]:
            del self._workers[key]

    def _alive_workers(self, now):
        return [info for info in self._workers.values()
                if info['waiting'] or now - info['seen_at'] <= WORKER_SILENT_SECONDS]

    def known(self):
        """Есть ли на связи исполнитель — то есть можно ли читать Oktell через VM шлюза."""
        if not enabled():
            return False
        with self._cond:
            return bool(self._alive_workers(self._clock()))

    def can_run(self, query):
        """Возьмётся ли шлюз за этот запрос: рубильник включён, у текста есть имя набора,
        исполнитель на связи, знает набор, и его учётке даны таблицы набора."""
        return not self.why_not(query)

    def why_not(self, query):
        """Почему запрос не уйдёт на шлюз — словами; пусто, если уйдёт."""
        if not enabled():
            return 'запросы на VM шлюза выключены (%s)' % ENABLED_ENV
        dataset = getattr(query, 'dataset', None)
        if not isinstance(query, oktell_datasets.Query) or dataset not in oktell_datasets.DATASETS:
            return 'у запроса нет набора каталога'
        tables = set(oktell_datasets.tables_of(dataset, getattr(query, 'params', None)))
        with self._cond:
            alive = self._alive_workers(self._clock())
        if not alive:
            return 'коннектор Oktell на VM шлюза не на связи'
        knowing = [info for info in alive if info['datasets'] is None or dataset in info['datasets']]
        if not knowing:
            return REASON_TEXT['unknown']
        if all(tables & info['unreadable'] for info in knowing):
            # Называем таблицы: по этой строке человек должен понять, что делать, не открывая
            # журнал (12.11.2026 то же самое без имён прочитали как «ошибку моста»).
            denied = sorted({key for info in knowing for key in tables & info['unreadable']})
            return ('учётке шлюза не выдано право читать таблицы базы Oktell: %s '
                    '(права выдаёт администратор SQL Server)'
                    % ', '.join(oktell_datasets.TABLES[key][0] for key in denied))
        return ''

    def records_why_not(self):
        """Почему запись разговора не попросить у шлюза — словами; пусто, если можно."""
        if not enabled():
            return 'запросы на VM шлюза выключены (%s)' % ENABLED_ENV
        with self._cond:
            alive = self._alive_workers(self._clock())
        if not alive:
            return 'коннектор Oktell на VM шлюза не на связи'
        if any(info.get('records') for info in alive):
            return ''
        if any(info.get('records') is False for info in alive):
            return 'коннектору Oktell на VM шлюза не задан адрес записей разговоров (OKTELL_RECORD_URL)'
        return RECORD_REASON_TEXT['unknown']

    # ── вызывающий ───────────────────────────────────────────────────────────

    def _submit(self, job):
        with self._cond:
            if len(self._pending) >= MAX_PENDING:
                self._stats['overflow'] += 1
                raise Refused('очередь запросов к VM шлюза переполнена')
            self._pending.append(job)
            self._jobs[job.id] = job
            self._stats['submitted'] += 1
            self._cond.notify_all()

    def _await(self, job, timeout):
        """Дождаться результата задания. `Refused` — до него очередь не дошла (можно идти к
        мосту), `Failed` — забрали и не ответили вовремя."""
        while True:
            remaining = job.deadline - self._clock()
            if remaining <= 0:
                with self._cond:
                    taken = job.taken_at is not None
                    self._drop(job)
                    self._stats['no_answer' if taken else 'not_taken'] += 1
                if taken:
                    raise Failed('коннектор Oktell на VM шлюза не ответил за %d с (%s)'
                                 % (int(timeout), job.label))
                # До запроса очередь так и не дошла — он не исполнялся, его можно отдать мосту.
                raise Refused('очередь запросов к VM шлюза не дошла до запроса за %d с' % int(timeout))
            if job.done.wait(min(PICKUP_SECONDS, remaining)):
                return job.result or {}
            with self._cond:
                # Никто не забрал задание и никто ничего не исполняет — исполнителей нет (шлюз
                # перезапускается, связь оборвалась). Если же исполнители заняты чужими
                # запросами, ждём своей очереди дальше: свободный появится.
                if job.taken_at is None and not self._someone_is_working():
                    self._drop(job)
                    self._stats['not_taken'] += 1
                    raise Refused('коннектор Oktell на VM шлюза не забрал запрос за %d с'
                                  % int(min(PICKUP_SECONDS, timeout)))

    def run(self, query, timeout):
        """Исполнить запрос на шлюзе и вернуть строки. `Refused` или `Failed` — см. шапку."""
        timeout = max(1.0, float(timeout))
        job = _Job(self._clock(), timeout, query=query)
        self._submit(job)
        result = self._await(job, timeout)
        if result.get('ok'):
            self._stats['ok'] += 1
            return result['rows']
        reason = result.get('reason') or 'db'
        message = '%s (набор %s)' % (REASON_TEXT.get(reason, REASON_TEXT['db']), job.dataset)
        detail = str(result.get('error') or '').strip()
        if detail:
            message = '%s: %s' % (message, detail[:300])
        self._stats['refused' if reason in REFUSED_REASONS else 'failed'] += 1
        self._warn(job.dataset, reason, message)
        if reason in REFUSED_REASONS:
            raise Refused(message)
        raise Failed(message)

    def fetch_record(self, paths, timeout):
        """Принести файл записи разговора с веб-сервера Oktell через шлюз.

        paths — относительные пути-кандидаты (сначала .mp3, потом .wav). Возвращает
        (байты, тип) первого найденного или None, если файла нет ни по одному пути (это норма:
        у ~0,4 % записанных звонков файла на диске нет). `Refused` — шлюз за запись не взялся
        (не умеет, не принял путь, не достучался до веб-сервера Oktell): файл можно попросить у
        моста. `Failed` — скачивание шло и не удалось."""
        clean = [str(path) for path in (paths or ()) if isinstance(path, str) and path]
        if not clean or len(clean) > MAX_RECORD_PATHS:
            raise Refused('путь к записи не вычислен' if not clean else 'путей к записи больше %d'
                          % MAX_RECORD_PATHS)
        timeout = max(1.0, float(timeout))
        job = _Job(self._clock(), timeout, paths=clean)
        self._submit(job)
        result = self._await(job, timeout)
        if result.get('ok'):
            if not result.get('found'):
                self._stats['record_missing'] += 1
                return None
            self._stats['record_ok'] += 1
            return result['audio'], result['content_type']
        reason = result.get('reason') or 'record'
        message = RECORD_REASON_TEXT.get(reason, RECORD_REASON_TEXT['record'])
        detail = str(result.get('error') or '').strip()
        if detail:
            message = '%s: %s' % (message, detail[:300])
        refused = reason in RECORD_REFUSED_REASONS
        self._stats['record_refused' if refused else 'record_failed'] += 1
        self._warn(RECORD_KIND, reason, message)
        if refused:
            raise Refused(message)
        raise Failed(message)

    def _someone_is_working(self):
        """Исполняется ли сейчас хоть одно задание (забрано и ещё не закрыто)."""
        return any(job.taken_at is not None for job in self._jobs.values())

    def _drop(self, job):
        self._jobs.pop(job.id, None)
        try:
            self._pending.remove(job)
        except ValueError:
            pass

    def _warn(self, dataset, reason, message):
        now = self._clock()
        key = (dataset, reason)
        with self._cond:
            last = self._logged.get(key)
            if last is not None and now - last < LOG_EVERY_SECONDS:
                return
            self._logged[key] = now
        log.warning('Запрос Oktell через VM шлюза: %s', message)

    # ── ручка исполнителя ────────────────────────────────────────────────────

    def take(self, worker, wait):
        """Задание для исполнителя или пустой список, если за `wait` секунд его не появилось."""
        wait = max(0.0, min(float(wait), POLL_WAIT_SECONDS))
        end = self._clock() + wait
        with self._cond:
            info = self._workers.get(worker)
            if info is not None:
                info['waiting'] += 1
            try:
                while True:
                    now = self._clock()
                    while self._pending:
                        job = self._pending.popleft()
                        if job.deadline <= now or job.id not in self._jobs:
                            continue
                        job.taken_at = now
                        job.worker = worker
                        self._stats['taken'] += 1
                        return [job.view()]
                    # Рубильник выключен — заданий просто не появляется (run их не кладёт), а
                    # опрос всё равно держим до конца: иначе исполнитель крутил бы пустые
                    # запросы без паузы.
                    if now >= end:
                        return []
                    self._cond.wait(end - now)
            finally:
                if info is not None:
                    info['waiting'] = max(0, info['waiting'] - 1)
                    info['seen_at'] = self._clock()

    def finish(self, results):
        """Результаты исполнителя → ждущим потокам. Возвращает, сколько из них кто-то ждал."""
        if not isinstance(results, list):
            return 0
        delivered = 0
        for item in results[:MAX_DONE_PER_POLL]:
            if not isinstance(item, dict):
                continue
            with self._cond:
                job = self._jobs.pop(str(item.get('id') or ''), None)
            if job is None:
                # Вызывающий уже не ждёт (таймаут) или задание чужое: результат выбрасываем.
                self._stats['late'] += 1
                continue
            if job.kind == RECORD_KIND:
                job.result = record_result(item, job.paths)
            elif item.get('ok'):
                try:
                    job.result = {'ok': True, 'rows': clean_rows(item.get('rows'))}
                except ValueError as exc:
                    job.result = {'ok': False, 'reason': 'db',
                                  'error': 'ответ коннектора не похож на строки запроса: %s' % exc}
            else:
                job.result = {'ok': False, 'reason': str(item.get('reason') or 'db')[:20],
                              'error': str(item.get('error') or '')[:400]}
            job.done.set()
            delivered += 1
        return delivered

    # ── диагностика ──────────────────────────────────────────────────────────

    def status(self):
        now = self._clock()
        with self._cond:
            workers = [{'worker': key, 'version': info['version'], 'catalog': info['catalog'],
                        'waiting': info['waiting'], 'silent_seconds': int(max(0, now - info['seen_at'])),
                        'datasets': len(info['datasets']) if info['datasets'] is not None else None,
                        'unreadable': sorted(info['unreadable']), 'records': info.get('records')}
                       for key, info in sorted(self._workers.items())]
            return {'enabled': enabled(), 'catalog': oktell_datasets.fingerprint(),
                    'pending': len(self._pending), 'in_flight': len(self._jobs) - len(self._pending),
                    'workers': workers, 'stats': dict(self._stats)}

    def reset(self):
        with self._cond:
            self._pending.clear()
            self._jobs.clear()
            self._workers.clear()
            self._logged.clear()
            self._stats.clear()


jobs = Jobs()

# Монолит зовёт эти функции локальным импортом пакета (тесты вырезают функции монолита через AST
# и исполняют в своём пространстве имён — помощников уровня монолита там нет).
can_run = jobs.can_run
why_not = jobs.why_not
run = jobs.run
known = jobs.known
status = jobs.status
records_why_not = jobs.records_why_not
fetch_record = jobs.fetch_record
