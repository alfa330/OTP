# -*- coding: utf-8 -*-
"""Записи разговоров Oktell для портала: файл с веб-сервера Oktell → задание портала.

Зачем
-----
Файлы записей портал брал у моста Oktell (`/record?path=` — чужой HTTP-прокси, выставленный
наружу). 10.11.2026 в 16:32 мост лёг вместе с переездом сервера Oktell на новый адрес, и с тех
пор «Случайный звонок», ежедневная выборка «ИИ-оценки» и ночное «Деление звонков» СЗоВ не могли
получить ни одной записи («Не удалось получить аудиозапись из Oktell»). Запросы к базе к тому
времени уже исполнял коннектор на VM шлюза (`oktell_jobs.py`), а файлы — нет.

Каталог записей Oktell лежит внутри папки `WWW` его встроенного веб-сервера
(`…\\Server\\WWW\\data\\<guid>\\record\\`), а файлы из `WWW` этот сервер отдаёт сам (вики Oktell,
«Серверный HTTP интерфейс»). Значит, файл достаётся простым GET по пути, который портал и так
вычисляет из базы (`<ГГГГММДД>/<ЧЧММ>/mix_<линия>_<линия>__<метка>.mp3`, вики «Получить путь к
записи разговора по завершению коммутации»). Это чтение готового файла: штатные ручки выгрузки
Oktell (`downloadrecordbyid`, `download/rec`) не трогаются — они будят однопоточный микшер.

Чему коннектор НЕ верит
-----------------------
Порталу. Из задания берутся только относительные пути, и каждый обязан совпасть со строгим
шаблоном имени записи: цифры даты и времени, буквы и цифры номеров линий, `.mp3` или `.wav`.
Ни `..`, ни слешей сверх двух папок, ни параметров запроса — что бы ни прислал портал,
скачать можно только файл записи из каталога записей.

Сети. Веб-сервер Oktell отвечает по HTTPS сертификатом своего же центра сертификации
(`LocalOktellCA`), который он в цепочке не отдаёт. Поэтому проверяется не цепочка, а отпечаток
SHA-256 самого сертификата (`OKTELL_RECORD_TLS_SHA256`): чужой сервер на этом адресе файл не
отдаст. Для HTTPS отпечаток обязателен.

Сколько памяти и нагрузки
-------------------------
Файл читается потоком и обрывается на потолке размера (16 МБ — час с лишним MP3 Oktell,
обычная запись — сотни килобайт), проверяется по сигнатуре (MP3 или WAV) и уходит порталу в
base64 со следующим опросом исполнителя. Исполнителей два, так что одновременно с веб-сервера
Oktell читается не больше двух файлов.
"""

import base64
import logging
import re
import threading
import time
from urllib.parse import quote, urlsplit

import urllib3

log = logging.getLogger('cdr_bridge.oktell_records')

KIND = 'record'

# <ГГГГММДД>/<ЧЧММ>/mix_<линия>_<линия>__<ГГГГ_ММ_ДД__ЧЧ_ММ_СС_ммм>.mp3|wav — и ничего больше.
PATH_RE = re.compile(
    r'\d{8}/\d{4}/mix_[0-9A-Za-z]{1,32}_[0-9A-Za-z]{1,32}'
    r'__\d{4}_\d{2}_\d{2}__\d{2}_\d{2}_\d{2}_\d{3}\.(?:mp3|wav)')
MAX_PATHS = 4
MAX_RECORD_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 64 * 1024

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 20.0

# Путь, которого заведомо нет, — для проверки установки: сервер обязан ответить 404, а не 401
# или 403 (значит, каталог отдаётся без входа и адрес верный).
PROBE_PATH = '20000101/0000/mix_0_0__2000_01_01__00_00_00_000.mp3'

_TYPES = {'.mp3': 'audio/mpeg', '.wav': 'audio/wav'}
_STAT_KEYS = ('found', 'missing', 'failed', 'bytes', 'ms')


class RecordError(Exception):
    """Файл не получен. kind — причина для портала:
    params — путь не похож на запись; unavailable — до веб-сервера Oktell не достучались;
    timeout — сервер не отдал файл вовремя; tls — сертификат сервера не совпал с отпечатком;
    big — файл больше потолка; record — сервер ответил не файлом записи."""

    def __init__(self, message, kind='record'):
        super().__init__(message)
        self.kind = kind


def clean_path(value):
    """Относительный путь записи из задания или ValueError. Сверяется буквально, без обрезки
    пробелов: что пришло, то и пойдёт в адрес — значит, ровно это и обязано совпасть."""
    path = value if isinstance(value, str) else ''
    if not PATH_RE.fullmatch(path):
        raise ValueError('путь не похож на запись Oktell: %r' % path[:120])
    return path


def clean_base_url(value):
    """Адрес каталога записей: http(s)://хост[:порт]/путь/ — без входа, параметров и якоря."""
    url = str(value or '').strip()
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('OKTELL_RECORD_URL — адрес http(s)://сервер/путь/ к каталогу записей')
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('OKTELL_RECORD_URL — без логина, параметров и якоря')
    return url if url.endswith('/') else url + '/'


def clean_fingerprint(value):
    """Отпечаток SHA-256 сертификата: 64 шестнадцатеричных знака, двоеточия допустимы."""
    text = str(value or '').strip().replace(':', '').lower()
    if not re.fullmatch(r'[0-9a-f]{64}', text):
        raise ValueError('OKTELL_RECORD_TLS_SHA256 — отпечаток SHA-256 сертификата (64 знака)')
    return text


def audio_format(body):
    """'mp3' | 'wav' | None по сигнатуре файла."""
    if not body:
        return None
    if body.startswith(b'ID3'):
        return 'mp3'
    if len(body) >= 2 and body[0] == 0xFF and (body[1] & 0xE0) == 0xE0:
        return 'mp3'
    if body[:4] == b'RIFF' and body[8:12] == b'WAVE':
        return 'wav'
    return None


def _reason_of(exc):
    """Исключение urllib3 → (kind, текст). MaxRetryError разворачивается до причины."""
    cause = getattr(exc, 'reason', None) if isinstance(exc, urllib3.exceptions.MaxRetryError) else exc
    cause = cause or exc
    if isinstance(cause, urllib3.exceptions.SSLError):
        text = str(cause)
        if 'ingerprint' in text:
            return 'tls', 'сертификат веб-сервера Oktell не совпал с отпечатком OKTELL_RECORD_TLS_SHA256'
        return 'tls', 'TLS с веб-сервером Oktell не установлен: %s' % text[:200]
    if isinstance(cause, (urllib3.exceptions.ConnectTimeoutError, urllib3.exceptions.NewConnectionError)):
        return 'unavailable', 'веб-сервер Oktell не принял соединение: %s' % str(cause)[:200]
    if isinstance(cause, urllib3.exceptions.ReadTimeoutError):
        return 'timeout', 'веб-сервер Oktell не отдал файл за %d с' % int(READ_TIMEOUT)
    return 'record', 'обрыв при скачивании записи: %s' % str(cause)[:200]


class Records:
    """Скачивание записей с веб-сервера Oktell. Один на коннектор, общий для исполнителей:
    пул соединений urllib3 потокобезопасен."""

    def __init__(self, base_url, fingerprint=None, http=None, max_bytes=MAX_RECORD_BYTES,
                 connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT):
        self.base_url = clean_base_url(base_url)
        parts = urlsplit(self.base_url)
        self.https = parts.scheme == 'https'
        self.host = parts.hostname
        self.port = parts.port
        self.fingerprint = clean_fingerprint(fingerprint) if fingerprint else None
        if self.https and not self.fingerprint:
            raise ValueError('для https нужен отпечаток сертификата (OKTELL_RECORD_TLS_SHA256): '
                             'сервер Oktell подписан своим центром сертификации')
        self.max_bytes = int(max_bytes)
        self.timeout = urllib3.Timeout(connect=connect_timeout, read=read_timeout)
        # Один повтор — на протухшее keep-alive соединение (сервер закрыл его за время простоя);
        # переадресации не выполняются; ошибку TLS не повторяем.
        self.retries = urllib3.Retry(total=1, connect=1, read=1, redirect=False, status=0, other=0,
                                     raise_on_status=False)
        if http is not None:
            self.http = http
        elif self.https:
            # Цепочку не проверяем (центр сертификации Oktell сервер не отдаёт) — вместо неё
            # urllib3 сверяет отпечаток сертификата сразу после рукопожатия.
            self.http = urllib3.PoolManager(num_pools=2, maxsize=4, cert_reqs='CERT_NONE',
                                            assert_fingerprint=self.fingerprint)
        else:
            self.http = urllib3.PoolManager(num_pools=2, maxsize=4)
        self._stats_lock = threading.Lock()
        self._stats = dict.fromkeys(_STAT_KEYS, 0)

    @classmethod
    def from_config(cls, config):
        """Из настроек коннектора; None — записи не настроены (нет OKTELL_RECORD_URL)."""
        url = (config.get('record_url') or '').strip()
        if not url:
            return None
        return cls(url, config.get('record_tls_sha256') or None)

    def describe(self):
        """Куда ходим — без пути каталога: в нём идентификатор, который в журнал не нужен."""
        port = ':%d' % self.port if self.port else ''
        return '%s://%s%s (%s)' % ('https' if self.https else 'http', self.host, port,
                                   'отпечаток %s…' % self.fingerprint[:12] if self.fingerprint
                                   else 'без TLS')

    def fetch(self, path):
        """Один файл по относительному пути → (байты, тип) или None, если файла нет (404).
        RecordError — файл не получен."""
        path = clean_path(path)
        url = self.base_url + quote(path, safe='/')
        try:
            response = self.http.request('GET', url, preload_content=False, timeout=self.timeout,
                                         retries=self.retries, redirect=False)
        except urllib3.exceptions.HTTPError as exc:
            kind, text = _reason_of(exc)
            raise RecordError(text, kind) from exc
        try:
            if response.status == 404:
                response.drain_conn()
                return None
            if response.status != 200:
                response.drain_conn()
                raise RecordError('веб-сервер Oktell ответил %d' % response.status, 'record')
            length = response.headers.get('Content-Length')
            if length and length.isdigit() and int(length) > self.max_bytes:
                response.close()
                raise RecordError('файл записи %s байт больше потолка %d' % (length, self.max_bytes), 'big')
            chunks, total = [], 0
            try:
                for chunk in response.stream(CHUNK_BYTES):
                    total += len(chunk)
                    if total > self.max_bytes:
                        response.close()
                        raise RecordError('файл записи больше потолка %d байт' % self.max_bytes, 'big')
                    chunks.append(chunk)
            except urllib3.exceptions.HTTPError as exc:
                kind, text = _reason_of(exc)
                raise RecordError(text, kind) from exc
            body = b''.join(chunks)
            del chunks
            if audio_format(body) is None:
                raise RecordError('веб-сервер Oktell отдал не MP3 и не WAV (%d байт)' % len(body), 'record')
            content_type = str(response.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
            if not content_type.startswith('audio/'):
                content_type = _TYPES.get(path[-4:].lower(), 'audio/mpeg')
            return body, content_type
        finally:
            response.release_conn()

    def execute(self, job):
        """Задание портала {id, kind: 'record', paths: [...]} → результат для ручки заданий.

        Пути пробуются по порядку (сначала .mp3, потом .wav), первый найденный уходит порталу.
        Ни одного нет — {ok: True, found: False}: это не ошибка, у ~0,4 % звонков файла нет.
        Исключений не бросает."""
        job = job if isinstance(job, dict) else {}
        job_id = str(job.get('id') or '')[:64]
        raw_paths = job.get('paths')
        try:
            if not isinstance(raw_paths, list) or not raw_paths or len(raw_paths) > MAX_PATHS:
                raise ValueError('в задании должен быть список из 1–%d путей' % MAX_PATHS)
            paths = [clean_path(item) for item in raw_paths]
        except ValueError as exc:
            return {'id': job_id, 'ok': False, 'reason': 'params', 'error': str(exc)[:300]}
        started = time.monotonic()
        for path in paths:
            try:
                found = self.fetch(path)
            except RecordError as exc:
                self._count(failed=1)
                return {'id': job_id, 'ok': False, 'reason': exc.kind, 'error': str(exc)[:300]}
            if found is None:
                continue
            body, content_type = found
            elapsed = int((time.monotonic() - started) * 1000)
            self._count(found=1, bytes=len(body), ms=elapsed)
            return {'id': job_id, 'ok': True, 'found': True, 'path': path,
                    'content_type': content_type, 'bytes': len(body), 'fetch_ms': elapsed,
                    'audio': base64.b64encode(body).decode('ascii')}
        self._count(missing=1)
        return {'id': job_id, 'ok': True, 'found': False}

    def _count(self, **deltas):
        with self._stats_lock:
            for key, value in deltas.items():
                self._stats[key] += value

    def take_stats(self):
        """Счётчики с прошлого вызова — для сводки в журнал."""
        with self._stats_lock:
            stats, self._stats = self._stats, dict.fromkeys(_STAT_KEYS, 0)
        return stats

    def probe(self):
        """Проверка установки: путь, которого заведомо нет, обязан дать 404 (не 401/403).
        Возвращает строку для человека; RecordError — не работает."""
        started = time.monotonic()
        if self.fetch(PROBE_PATH) is not None:
            raise RecordError('на проверочный путь сервер отдал файл — адрес каталога не тот', 'record')
        return '%s — отвечает за %d мс, сертификат %s' % (
            self.describe(), int((time.monotonic() - started) * 1000),
            'совпал с отпечатком' if self.fingerprint else 'не проверяется (http)')
