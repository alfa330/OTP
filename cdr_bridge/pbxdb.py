# -*- coding: utf-8 -*-
"""Чтение журнала очередей из базы станции. Работает ИЗНУТРИ корпоративной сети.

Зачем ещё один источник, если есть HTTP-ручка CDR
-------------------------------------------------
Надстройка станции склеивает звонок в одну строку и теряет момент ответа, а ожидание
приходится выводить из длины автоинформатора (см. `cdr/queue_facts.py`). Сам Asterisk
пишет те же величины точно — в таблицу `queuelog` своей базы. С 21.09.2026 у нас есть
на неё учётка ТОЛЬКО НА ЧТЕНИЕ (`SELECT` на `asteriskcdrdb`).

Чем это безопасно для станции
-----------------------------
  * один запрос за цикл, одно соединение, никакого пула;
  * окно по индексированной колонке `time` и потолок строк — полных сканов нет.
    Проверено 21.09.2026: `COUNT(*)` по неиндексированному полю `cel.eventtime`
    рвёт соединение по таймауту, а выборка окном по `time` отвечает мгновенно;
  * читаются только восемь типов событий. Паузы операторов и попытки дозвона
    (`RINGNOANSWER` — тысячи строк в сутки) остаются на станции;
  * никаких `INSERT`/`UPDATE`: их и грант не позволит.

Соединение идёт не напрямую, а через локальный прокси шлюза (как и HTTP к станции):
мосту фаервол разрешает только loopback.
"""

import logging
from datetime import datetime, timedelta

log = logging.getLogger('cdr_bridge.pbxdb')

try:  # pymysql может не стоять — тогда мост работает как раньше, без точных фактов
    import pymysql
except ImportError:  # pragma: no cover - на боевой машине пакет есть
    pymysql = None

from cdr import queue_facts

CONNECT_TIMEOUT = 8
READ_TIMEOUT = 25
# Потолок строк на запрос. Сутки станции дают около полутора тысяч нужных событий
# (431 вход + 375 ответов + 56 брошенных + завершения), так что десять тысяч — это
# запас в шесть раз и одновременно защита от окна, которое кто-то расширил ошибкой.
MAX_ROWS = 10000
# Шире суток с хвостом не спрашиваем — та же граница, что у суточного задания моста.
MAX_WINDOW = timedelta(hours=26)

_COLUMNS = ('time', 'callid', 'queuename', 'agent', 'event', 'data1', 'data2', 'data3')

_QUEUE_SQL = (
    "SELECT time, callid, queuename, agent, event, data1, data2, data3 "
    "  FROM queuelog "
    " WHERE time >= %s AND time < %s "
    "   AND event IN (" + ', '.join(['%s'] * len(queue_facts.WANTED_EVENTS)) + ") "
    " ORDER BY id "
    " LIMIT %s"
)


class PbxDbError(Exception):
    pass


class PbxDb:
    """Учётка станции на чтение. Без настроек — выключен, и мост этого не замечает."""

    def __init__(self, host='', port=3306, user='', password='', database='asteriskcdrdb',
                 connect=None):
        self.host = (host or '').strip()
        self.port = int(port or 3306)
        self.user = (user or '').strip()
        self.password = password or ''
        self.database = (database or 'asteriskcdrdb').strip()
        self._own_driver = connect is None
        self._connect = connect or self._connect_pymysql
        self._conn = None

    @property
    def enabled(self):
        """Настроен ли источник. Нет драйвера или адреса — точных фактов просто не будет,
        а мост работает ровно как до этой правки."""
        if self._own_driver and pymysql is None:
            return False
        return bool(self.host and self.user)

    def _connect_pymysql(self):
        if pymysql is None:
            raise PbxDbError('pymysql не установлен')
        return pymysql.connect(
            host=self.host, port=self.port, user=self.user, password=self.password,
            database=self.database, charset='utf8mb4',
            connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT,
            write_timeout=CONNECT_TIMEOUT)

    def _cursor_rows(self, sql, params):
        """Один запрос с одной попыткой переподключения.

        Соединение живёт между циклами (раз в двадцать секунд открывать новое — лишняя
        работа станции), а станция рвёт простаивающие сами. Поэтому обрыв — не ошибка,
        а повод соединиться заново; вторая неудача подряд уже уходит наверх."""
        for attempt in (1, 2):
            try:
                if self._conn is None:
                    self._conn = self._connect()
                with self._conn.cursor() as cursor:
                    cursor.execute(sql, params)
                    return cursor.fetchall()
            except Exception as exc:  # noqa: BLE001
                self.close()
                if attempt == 2:
                    raise PbxDbError('база станции: %s' % exc)
                log.debug('База станции: соединение переоткрывается (%s)', exc)
        return []

    def close(self):
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def queue_rows(self, start, end):
        """События очередей за окно [start, end) — список словарей по колонкам `queuelog`."""
        if not self.enabled:
            return []
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            raise PbxDbError('окно журнала очередей задаётся временем')
        if not timedelta(0) < end - start <= MAX_WINDOW:
            raise PbxDbError('окно %s — %s шире суток с хвостом' % (start, end))
        params = [start, end] + list(queue_facts.WANTED_EVENTS) + [MAX_ROWS]
        rows = self._cursor_rows(_QUEUE_SQL, params)
        if len(rows) >= MAX_ROWS:
            # Молча обрезанное окно — это молча испорченные ожидания на табло.
            log.warning('Журнал очередей: упёрлись в потолок %d строк за %s — %s',
                        MAX_ROWS, start, end)
        return [dict(zip(_COLUMNS, row)) for row in rows]

    def facts(self, start, end):
        """Готовые факты звонков за окно: {callid: {вход, ответ, ожидание, разговор, отбой}}."""
        return queue_facts.build_facts(self.queue_rows(start, end))


def from_config(config):
    """PbxDb из настроек моста. Пустые настройки дают выключенный источник."""
    return PbxDb(host=config.get('pbxdb_host', ''), port=config.get('pbxdb_port') or 3306,
                 user=config.get('pbxdb_user', ''), password=config.get('pbxdb_password', ''),
                 database=config.get('pbxdb_name') or 'asteriskcdrdb')
