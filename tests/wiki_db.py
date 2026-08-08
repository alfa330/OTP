# -*- coding: utf-8 -*-
"""Одно общее соединение с базой на весь набор тестов раздела «Вики».

Зачем singleton. У роли codex_readonly лимит `rolconnlimit = 2`, а тестов,
которым нужна настоящая база, три файла. Когда каждый класс открывал своё
соединение в setUpClass, при полном прогоне они пересекались и падали с
«too many connections for role» — при этом по отдельности проходили, что
делало набор флаки́м и незаслуженно подозрительным.

Соединение открывается лениво при первом обращении, живёт до конца процесса и
всегда READ ONLY: тесты гоняют боевые SQL по синтетическим данным (CTE
перекрывает одноимённую таблицу), боевые таблицы не читаются и не изменяются.

Если базы нет — тесты пропускаются с внятной причиной, а не падают: они
зависят от внешнего сервиса, и сетевой сбой не должен выглядеть как регресс
в коде.
"""

import os
import re
import threading
from pathlib import Path

try:
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None

ROOT = Path(__file__).resolve().parents[1]

_lock = threading.Lock()
_connection = None
_failure = None


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


def connection():
    """Общее READ ONLY соединение. None, если базу поднять не удалось."""
    global _connection, _failure
    if _connection is not None:
        return _connection
    if _failure is not None or not available():
        return None

    with _lock:
        if _connection is not None:
            return _connection
        try:
            conn = psycopg2.connect(dsn(), connect_timeout=30)
            conn.set_session(readonly=True)
            _connection = conn
        except Exception as error:  # noqa: BLE001 — причина уходит в skip-сообщение
            _failure = str(error).strip().splitlines()[0][:160]
            return None
    return _connection


def skip_reason():
    if psycopg2 is None:
        return 'psycopg2 не установлен'
    if not dsn():
        return 'нет DATABASE_URL_READONLY'
    if connection() is None:
        return 'база недоступна: %s' % (_failure or 'неизвестная причина')
    return None


def rollback():
    """Откат после каждого запроса: соединение общее, состояние тянуть нельзя."""
    conn = _connection
    if conn is not None:
        try:
            conn.rollback()
        except Exception:
            pass
