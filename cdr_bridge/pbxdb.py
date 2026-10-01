# -*- coding: utf-8 -*-
"""Чтение базы самой станции: CDR, журнал очередей и справочник номеров. Работает ИЗНУТРИ
корпоративной сети.

Почему не HTTP-ручка надстройки
-------------------------------
Надстройка «FreePBX Stats» на отдельной машине склеивает звонок в одну строку и теряет
момент ответа, разговор оператора и его запись; ожидание приходилось выводить из длины
автоинформатора (см. `cdr/queue_facts.py`). А 30.09.2026 в 22:31 она просто перестала
отвечать (порты закрыты при живом хосте), и мост остался без данных на ночь и утро: сама
станция всё это время писала звонки. Сам Asterisk пишет всё точно — в свою базу: сырые
плечи в `cdr`, события очередей в `queuelog`, номера сотрудников в `asterisk.users`. С
21.09.2026 у нас есть на неё учётка ТОЛЬКО НА ЧТЕНИЕ (`SELECT` на `asteriskcdrdb` и
`asterisk`), и с 1.5.0 мост берёт оттуда всё: надстройка ему больше не нужна.

Чем это безопасно для станции
-----------------------------
  * по запросу на источник за цикл, одно соединение, никакого пула;
  * окна по индексированным колонкам (`cdr.calldate`, `queuelog.time`) и потолок строк —
    полных сканов нет. Проверено 21.09.2026: `COUNT(*)` по неиндексированному полю
    `cel.eventtime` рвёт соединение по таймауту, а выборка окном по `time` отвечает
    мгновенно; сутки `cdr` (3–9 тыс. строк) — так же;
  * читаются только девять типов событий (`queue_facts.WANTED_EVENTS`). Паузы операторов
    и попытки дозвона (`RINGNOANSWER` — тысячи строк в сутки) остаются на станции;
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

# ── CDR ──────────────────────────────────────────────────────────────────────
# Сутки станции — 3–9 тыс. плеч (22–29.09.2026). Потолок впятеро выше и не обрезает молча:
# упёрлись — это ошибка чтения, а не неполные сутки на портале.
CDR_MAX_ROWS = 50000
# Окно суточного задания — сутки с часовым хвостом, плюс час запаса назад (CDR_LEAD).
CDR_MAX_WINDOW = timedelta(hours=26)
# Запас назад от начала окна. Строка CDR — плечо, а не звонок: звонок, пришедший в 23:59:45,
# кончается после полуночи, и его плечи за полночью без начала склеились бы в отдельное
# касание новых суток (23.09.2026 так задвоился бы звонок 22.09). С запасом звонок
# собирается целиком, получает настоящее начало, и фильтр суток по началу касания отдаёт
# его прошлым суткам. Надстройка, отдававшая строку на звонок, этой ловушки не знала.
CDR_LEAD = timedelta(hours=1)

# Те же поля, что отдавала надстройка (`/freepbx/cdr`): склейке (`cdr.touches`) нужны они.
_CDR_COLUMNS = ('calldate', 'clid', 'src', 'dst', 'dcontext', 'channel', 'dstchannel',
                'duration', 'billsec', 'disposition', 'uniqueid', 'did', 'recordingfile',
                'linkedid')

# `sequence` — порядок записи строк станцией: у плеч одной секунды он и есть их порядок.
_CDR_SQL = (
    "SELECT " + ', '.join(_CDR_COLUMNS) + " "
    "  FROM cdr "
    " WHERE calldate >= %s AND calldate < %s "
    " ORDER BY calldate, sequence "
    " LIMIT %s"
)

# Справочник номеров самой станции — то, что надстройка отдавала ручкой `/agents/map`:
# имя в транслите («Ivanov_Ivan»), портал сверяет его с ФИО по словам (cdr/directory.py).
_AGENTS_SQL = "SELECT extension, name FROM asterisk.users"


def _text(value):
    return '' if value is None else str(value)


def _cdr_row(values):
    row = dict(zip(_CDR_COLUMNS, values))
    calldate = row['calldate']
    row['calldate'] = (calldate.strftime('%Y-%m-%dT%H:%M:%S') if isinstance(calldate, datetime)
                       else _text(calldate).replace(' ', 'T')[:19])
    for name in ('duration', 'billsec'):
        try:
            row[name] = int(row[name] or 0)
        except (TypeError, ValueError):
            row[name] = 0
    for name in _CDR_COLUMNS:
        if name not in ('calldate', 'duration', 'billsec'):
            row[name] = _text(row[name])
    # Готовой ссылки на запись станция в базе не хранит: склейка соберёт её из имени файла
    # и даты (`cdr.touches._recording_url`) — тем же адресом, каким её давала надстройка.
    row['recording_url'] = None
    return row


def _moment(text):
    try:
        return datetime.strptime(str(text or '')[:19].replace('T', ' '), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        raise PbxDbError('граница окна %r не разбирается' % (text,))


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
        # Докуда журнал прочитан последним запросом без обрыва (время станции). Нужен тому,
        # кто делает выводы из ОТСУТСТВИЯ события: «входа в очередь не было» можно сказать
        # только про звонок, закончившийся до этой отметки (queue_facts.never_entered).
        self.covered_until = None

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
        # autocommit обязателен. У MariaDB по умолчанию REPEATABLE READ, а pymysql без
        # автокоммита открывает транзакцию первым же SELECT и держит её, пока соединение
        # живо: каждый следующий запрос видит базу на момент первого. Соединение у нас
        # одно на весь день, поэтому 22.09.2026 мост с 15:59 читал один и тот же снимок
        # журнала очередей — новые звонки в нём не появлялись, и точные поля до утра
        # 23.09 не приехали ни одному звонку. Ошибок при этом не было ни одной.
        return pymysql.connect(
            host=self.host, port=self.port, user=self.user, password=self.password,
            database=self.database, charset='utf8mb4', autocommit=True,
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
        self.covered_until = None
        rows = self._cursor_rows(_QUEUE_SQL, params)
        if len(rows) >= MAX_ROWS:
            # Молча обрезанное окно — это молча испорченные ожидания на табло.
            log.warning('Журнал очередей: упёрлись в потолок %d строк за %s — %s',
                        MAX_ROWS, start, end)
            # Строки идут по id, то есть по времени записи: всё, что позже последней,
            # осталось на станции. Отметка — время последней, а не конец окна.
            self.covered_until = rows[-1][0] if rows else start
        else:
            self.covered_until = end
        return [dict(zip(_COLUMNS, row)) for row in rows]

    def facts(self, start, end):
        """Готовые факты звонков за окно: {callid: {вход, ответ, ожидание, разговор, отбой}}."""
        return queue_facts.build_facts(self.queue_rows(start, end))

    def cdr_rows(self, start, end):
        """Плечи CDR станции за окно [start, end) — словари с полями, которые ждёт склейка."""
        if not self.enabled:
            raise PbxDbError('база станции не настроена')
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            raise PbxDbError('окно CDR задаётся временем')
        if not timedelta(0) < end - start <= CDR_MAX_WINDOW:
            raise PbxDbError('окно CDR %s — %s шире суток с хвостом' % (start, end))
        rows = self._cursor_rows(_CDR_SQL, [start, end, CDR_MAX_ROWS])
        if len(rows) >= CDR_MAX_ROWS:
            # Обрезанные сутки на портале выглядели бы как тихий день, а не как ошибка.
            raise PbxDbError('CDR: упёрлись в потолок %d строк за %s — %s'
                             % (CDR_MAX_ROWS, start, end))
        return [_cdr_row(row) for row in rows]

    def iter_cdr(self, from_dt, to_dt, on_page=None):
        """Тот же вход, что у `Station.iter_cdr`: границы строками, на выходе строки CDR.

        Читается с запасом назад (`CDR_LEAD`): звонок, начатый до начала окна, должен
        собраться целиком, иначе его хвост станет лишним касанием. Касания из запаса
        отсекает тот, кто знает свои сутки, — по дате начала касания."""
        rows = self.cdr_rows(_moment(from_dt) - CDR_LEAD, _moment(to_dt))
        if on_page:
            on_page(len(rows))
        return iter(rows)

    def agents_map(self):
        """ext → имя по справочнику самой станции (`asterisk.users`)."""
        if not self.enabled:
            raise PbxDbError('база станции не настроена')
        out = {}
        for extension, name in self._cursor_rows(_AGENTS_SQL, []):
            ext = _text(extension).strip()
            if ext:
                out[ext] = _text(name).strip()
        return out

    def describe(self):
        """Где лежит источник — для строки состояния моста на портале (без учётки)."""
        return 'mysql://%s:%d/%s' % (self.host, self.port, self.database)


def from_config(config):
    """PbxDb из настроек моста. Пустые настройки дают выключенный источник."""
    return PbxDb(host=config.get('pbxdb_host', ''), port=config.get('pbxdb_port') or 3306,
                 user=config.get('pbxdb_user', ''), password=config.get('pbxdb_password', ''),
                 database=config.get('pbxdb_name') or 'asteriskcdrdb')
