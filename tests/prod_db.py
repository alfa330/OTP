# -*- coding: utf-8 -*-
"""Одно общее соединение с боевой базой на весь набор тестов.

Зачем singleton. У роли codex_readonly лимит `rolconnlimit = 2`, а тестов,
которым нужна настоящая база, уже несколько файлов. Когда каждый класс открывал своё
соединение в setUpClass, при полном прогоне они пересекались и падали с
«too many connections for role» — при этом по отдельности проходили, что
делало набор флаки́м и незаслуженно подозрительным.

Соединение открывается лениво при первом обращении, живёт до конца процесса и
всегда READ ONLY: тесты гоняют боевые SQL по синтетическим данным (CTE
перекрывает одноимённую таблицу), боевые таблицы не читаются и не изменяются.

Если базы нет — тесты пропускаются с внятной причиной, а не падают: они
зависят от внешнего сервиса, и сетевой сбой не должен выглядеть как регресс
в коде.

Порвалось на бегу — тоже не регресс. Полный прогон идёт больше десяти минут, и
соединение до Render за это время может отвалиться («SSL SYSCALL error», потом
«connection already closed» у всех, кто держал его из setUpClass). Поэтому
`connection()` отдаёт не сам psycopg2-объект, а обёртку: она переоткрывает
соединение и пересоздаёт курсор, а если база так и не отвечает — тест
пропускается. Разделяет эти случаи состояние соединения: если база жива и
ОТВЕТИЛА ошибкой, это регресс в SQL, и он краснеет как раньше.
"""

import os
import re
import threading
import unittest
from pathlib import Path

try:
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None

ROOT = Path(__file__).resolve().parents[1]

# Сколько раз подряд можно не дозвониться до базы, прежде чем перестать пытаться.
# Без потолка мёртвая база стоила бы по connect_timeout на каждый тест.
_MAX_FAILURES_IN_ROW = 2

_lock = threading.Lock()
_connection = None
_failure = None
_failures_in_row = 0


def dsn():
    env = os.environ.get('DATABASE_URL_READONLY')
    if env:
        return env
    local = ROOT / '.env.codex.local'
    if not local.exists():
        return None
    text = local.read_text(encoding='utf-8', errors='replace')
    match = re.search(r'^DATABASE_URL_READONLY\s*=\s*(.+)$', text, re.M)
    return match.group(1).strip().strip('"\'') if match else None


def available():
    """Можно ли вообще идти в базу — без попытки соединения."""
    return psycopg2 is not None and bool(dsn())


def _dead(conn):
    """psycopg2 помечает соединение закрытым сам, когда сеть под ним порвалась."""
    return conn is None or conn.closed != 0


def _live():
    """Живое READ ONLY соединение или None (причина — в _failure)."""
    global _connection, _failure, _failures_in_row
    if not _dead(_connection):
        return _connection
    if not available():
        return None

    with _lock:
        if not _dead(_connection):
            return _connection
        if _connection is not None:
            try:
                _connection.close()
            except Exception:
                pass
            _connection = None
        if _failures_in_row >= _MAX_FAILURES_IN_ROW:
            return None
        try:
            conn = psycopg2.connect(dsn(), connect_timeout=30)
            conn.set_session(readonly=True)
        except Exception as error:  # noqa: BLE001 — причина уходит в skip-сообщение
            _failures_in_row += 1
            _failure = str(error).strip().splitlines()[0][:160]
            return None
        _connection = conn
        _failure = None
        _failures_in_row = 0
    return _connection


def _require():
    """Живое соединение или skip: до базы не дозвонились — это не регресс."""
    conn = _live()
    if conn is None:
        raise unittest.SkipTest('боевая база недоступна: %s'
                                % (_failure or 'неизвестная причина'))
    return conn


class _Cursor:
    """Курсор поверх общего соединения, переживающий обрыв связи.

    Тесты держат курсор с setUpClass, а соединение под ним может отвалиться
    между тестами. Тогда курсор создаётся заново на новом соединении и запрос
    повторяется — он read only, повтор безопасен. Если база жива и ответила
    ошибкой, ошибка идёт наружу: это регресс в SQL, а не сеть.
    """

    def __init__(self, args, kwargs):
        self._args = args
        self._kwargs = kwargs
        self._cur = _require().cursor(*args, **kwargs)

    def _renew(self):
        self._cur = _require().cursor(*self._args, **self._kwargs)
        return self._cur

    def execute(self, query, params=None):
        try:
            return self._cur.execute(query, params)
        except psycopg2.Error:
            if not _dead(self._cur.connection):
                raise
            return self._renew().execute(query, params)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        try:
            self._cur.close()
        except Exception:
            pass
        return False

    def __getattr__(self, name):
        return getattr(self._cur, name)


class _Connection:
    """Общее соединение как объект, который можно запомнить на весь класс тестов."""

    def cursor(self, *args, **kwargs):
        return _Cursor(args, kwargs)

    def rollback(self):
        rollback()

    def __getattr__(self, name):
        return getattr(_require(), name)


_SHARED = _Connection()


def connection():
    """Общее READ ONLY соединение. None, если базу поднять не удалось."""
    return _SHARED if _live() is not None else None


def skip_reason():
    if psycopg2 is None:
        return 'psycopg2 не установлен'
    if not dsn():
        return 'нет DATABASE_URL_READONLY'
    if _live() is None:
        return 'база недоступна: %s' % (_failure or 'неизвестная причина')
    return None


def rollback():
    """Откат после каждого запроса: соединение общее, состояние тянуть нельзя."""
    conn = _connection
    if _dead(conn):
        return  # мёртвое соединение откатывать нечего, переоткроется само
    try:
        conn.rollback()
    except Exception:
        pass
