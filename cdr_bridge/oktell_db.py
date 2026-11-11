# -*- coding: utf-8 -*-
"""Чтение базы Oktell (MS SQL Server) изнутри корпоративной сети. Только SELECT.

Зачем отдельный читатель
------------------------
До 10.11.2026 базу Oktell портал читал через мост — HTTP-прокси `/query`, выставленный наружу.
На каждое HTTP-соединение мост поднимал новое ODBC-подключение к SQL Server (вход в базу —
его узкое место: ~36 запросов за 40 секунд клали вход всему проду), а 10.11 лёг совсем.
Коннектор на VM шлюза стоит в одной сети с базой и держит постоянные соединения: одно — для
снимка табло и по одному на исполнителя запросов по требованию (`oktell_jobs.py`, их два).

Чем это безопасно для станции
-----------------------------
  * учётка — только на чтение; какой запрос исполнять, решает коннектор, а не портал
    (тексты — `cdr/oktell_wallboard.py` и каталог `cdr/oktell_datasets.py`);
  * соединений — по числу потоков, без пула; снимок — один запрос в такт, запросы по
    требованию — не больше одного на исполнителя одновременно;
  * `autocommit`: без него драйвер открывает транзакцию на первом же SELECT и держит её всё
    время жизни соединения — открытая сутками транзакция на боевой базе телефонии не нужна
    никому (та же ловушка, что стоила суток «Табло ОП» на MariaDB, только с другим концом);
  * чтение без блокировок (`READ UNCOMMITTED`): снимок не ставит разделяемых блокировок на
    строки, которые станция в эту секунду пишет, — значит, не задерживает её и не попадает с
    ней во взаимную блокировку. Под `READ COMMITTED` это случилось на третьей минуте работы
    (11.11.2026 11:14:44, ошибка 1205: наш SELECT выбран жертвой, а пишущий процесс станции до
    того ждал). Цена — «грязное» чтение строки, запись которой ещё не завершена; для экрана,
    который переснимается раз в десять секунд, это ничего не меняет: сверка 10 пар запросов
    подряд под обоими уровнями — счётчики и статусы операторов совпали во всех десяти;
  * `LOCK_TIMEOUT`: читатель, упёршийся в чужую блокировку (схемы — при обслуживании базы),
    сдаётся через 5 секунд, а не стоит в очереди; `DEADLOCK_PRIORITY LOW`: если взаимная
    блокировка всё же случится, жертвой станет наш SELECT, а не запись звонка;
  * таймаут запроса: драйвер шлёт серверу отмену, запрос не остаётся работать без читателя;
  * потолок строк: ответ длиннее — ошибка чтения, а не урезанный снимок.

Соединение шифруется (TLS, `encryption='require'`). Сертификат сервера не проверяется: у
SQL Server он самоподписанный и меняется при каждом перезапуске службы. Это защита от чтения
трафика в сети, а не от подмены сервера — как и у любого клиента этой базы в корпоративной сети.

Куда коннектору можно ходить по сети, решает конфигурация шлюза (правило по uid процесса), а
не этот модуль.
"""

import decimal
import logging
import math
import uuid
from datetime import date, datetime, time as day_time

log = logging.getLogger('cdr_bridge.oktell_db')

try:  # драйвера может не быть на машине разработчика — тогда читатель честно выключен
    import pymssql
except ImportError:  # pragma: no cover - в образе шлюза пакет есть
    pymssql = None

LOGIN_TIMEOUT = 8
QUERY_TIMEOUT = 15
# На линии десятки операторов. Потолок — защита от запроса, который кто-то расширил ошибкой.
MAX_ROWS = 5000
APP_NAME = 'otp-oktell-connector'
DEFAULT_PORT = 1433
DEFAULT_DATABASE = 'oktell'
ENCRYPTION_MODES = ('require', 'request', 'off')

# Ошибка SQL Server «Login failed»: учётка не подошла. Повторять вход каждые секунды нельзя —
# при политике блокировки учётка запирается.
LOGIN_FAILED = 18456

# Вид отказа — по нему исполнитель запросов решает, что сказать порталу (OktellDbError.kind):
#   permission  учётке не дано право: на таблицу (229), на колонку (230), на базу (916);
#   timeout     запрос не уложился в таймаут драйвера (20003) или в LOCK_TIMEOUT (1222);
#   deadlock    запрос выбран жертвой взаимной блокировки (1205) — его можно повторить;
#   unavailable до базы не достучались или соединение оборвалось;
#   auth        учётка не подошла;
#   db          всё остальное — база отклонила сам запрос.
PERMISSION_ERRORS = (229, 230, 916)
TIMEOUT_ERRORS = (20003, 1222)
DEADLOCK_ERROR = 1205
CONNECTION_ERRORS = (20002, 20004, 20005, 20006, 20009, 20047)


def error_kind(number, connecting=False):
    """Вид отказа по номеру ошибки SQL Server / DB-Library."""
    if number == LOGIN_FAILED:
        return 'auth'
    if number in PERMISSION_ERRORS:
        return 'permission'
    if number in TIMEOUT_ERRORS:
        return 'timeout'
    if number == DEADLOCK_ERROR:
        return 'deadlock'
    if connecting or number in CONNECTION_ERRORS:
        return 'unavailable'
    return 'db'

# Настройки сессии. Первые — те же ANSI-режимы, что драйвер ставит сам (свой список их
# заменяет, а не дополняет), остальное — про вежливость к боевой базе, см. шапку.
SESSION_SETUP = (
    'SET ARITHABORT ON',
    'SET CONCAT_NULL_YIELDS_NULL ON',
    'SET ANSI_NULLS ON',
    'SET ANSI_NULL_DFLT_ON ON',
    'SET ANSI_PADDING ON',
    'SET ANSI_WARNINGS ON',
    'SET QUOTED_IDENTIFIER ON',
    'SET TEXTSIZE 2147483647',
    'SET NOCOUNT ON',
    # Без разделяемых блокировок: читатель снимка не должен ни ждать станцию, ни задерживать её.
    'SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED',
    'SET LOCK_TIMEOUT 5000',
    'SET DEADLOCK_PRIORITY LOW',
)


# Что учётке коннектора уметь НЕ положено. Запрос читает только собственные действующие права
# учётки на сервере и в базе по умолчанию — ни чужих учёток, ни данных он не трогает.
WRITE_PERMISSIONS = (
    ('control_server', "HAS_PERMS_BY_NAME(NULL, NULL, 'CONTROL SERVER')", 'администратор сервера'),
    ('can_insert', "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT')", 'INSERT'),
    ('can_update', "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE')", 'UPDATE'),
    ('can_delete', "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE')", 'DELETE'),
    ('can_alter', "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'ALTER')", 'ALTER'),
    ('can_execute', "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'EXECUTE')", 'EXECUTE'),
)
WRITE_PERMISSIONS_SQL = 'SELECT ' + ', '.join('%s AS %s' % (expr, name)
                                              for name, expr, _label in WRITE_PERMISSIONS)


class OktellDbError(RuntimeError):
    """База Oktell не прочиталась. `auth` — учётка не подошла (повторять вход не спеша);
    `kind` — вид отказа (см. `error_kind`), `number` — номер ошибки сервера, если был."""

    def __init__(self, message, auth=False, kind=None, number=None):
        super().__init__(message)
        self.auth = bool(auth)
        self.kind = kind or ('auth' if auth else 'db')
        self.number = number


def json_value(value):
    """Значение колонки → то, что уедет порталу в JSON: число, строка или None.

    Даты — строкой «ГГГГ-ММ-ДД ЧЧ:ММ:СС», как их отдаёт сам запрос через CONVERT(…, 120) и как
    их отдавал мост; Decimal — числом (целым, если дробной части нет)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            return None
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, (date, day_time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def _error_number(exc):
    """Номер ошибки SQL Server из исключения драйвера: (номер, текст) в args."""
    args = getattr(exc, 'args', None) or ()
    if args and isinstance(args[0], tuple) and args[0]:
        args = args[0]
    try:
        return int(args[0])
    except (TypeError, ValueError, IndexError):
        return None


def _error_text(exc):
    """Текст ошибки драйвера одной строкой. Пароля в нём нет: драйвер его не печатает."""
    args = getattr(exc, 'args', None) or ()
    if args and isinstance(args[0], tuple):
        args = args[0]
    parts = []
    for item in args:
        if isinstance(item, (bytes, bytearray)):
            item = bytes(item).decode('utf-8', 'replace')
        parts.append(str(item))
    text = ' '.join(parts) or str(exc)
    return ' '.join(text.split())[:300]


class OktellDb:
    """Одно постоянное соединение с базой Oktell. Не потокобезопасен: у каждого потока
    коннектора (снимок табло, каждый исполнитель запросов) — свой экземпляр.

    query_timeout — сколько драйвер ждёт ответа, прежде чем послать серверу отмену;
    row_limit — потолок строк ответа на стороне сервера (`SET ROWCOUNT`): сервер сам
    останавливается на этой строке, и в соединении не остаётся недочитанного хвоста. Так
    исполнитель повторяет правило моста Oktell «не больше тысячи строк на запрос», на котором
    стоит постраничная логика портала."""

    def __init__(self, host, port=DEFAULT_PORT, user='', password='', database=DEFAULT_DATABASE,
                 encryption='require', connect=None, query_timeout=QUERY_TIMEOUT, row_limit=None,
                 app_name=APP_NAME):
        self.host = str(host or '').strip()
        try:
            self.port = int(port or DEFAULT_PORT)
        except (TypeError, ValueError):
            self.port = DEFAULT_PORT
        self.user = str(user or '').strip()
        self._password = password or ''
        self.database = str(database or DEFAULT_DATABASE).strip() or DEFAULT_DATABASE
        self.encryption = encryption if encryption in ENCRYPTION_MODES else 'require'
        self.query_timeout = max(1, int(query_timeout or QUERY_TIMEOUT))
        self.row_limit = max(1, int(row_limit)) if row_limit else None
        self.app_name = str(app_name or APP_NAME)[:60]
        self._connect = connect
        self._connection = None

    def session_setup(self):
        """Настройки сессии этого соединения: общие и, если задан, потолок строк."""
        setup = list(SESSION_SETUP)
        if self.row_limit:
            setup.append('SET ROWCOUNT %d' % self.row_limit)
        return setup

    @property
    def enabled(self):
        return bool(self.host and self.user and (self._connect is not None or pymssql is not None))

    @property
    def connected(self):
        return self._connection is not None

    def describe(self):
        """Куда ходим — без учётки: уходит порталу в состояние коннектора и в журнал."""
        return '%s:%d/%s' % (self.host, self.port, self.database)

    def _open(self):
        if self._connect is not None:
            return self._connect()
        if pymssql is None:
            raise OktellDbError('драйвер pymssql не установлен')
        return pymssql.connect(
            server=self.host, port=str(self.port), user=self.user, password=self._password,
            database=self.database, login_timeout=LOGIN_TIMEOUT, timeout=self.query_timeout,
            charset='UTF-8', as_dict=True, appname=self.app_name, tds_version='7.4',
            # Шифрование — весь сеанс, не только вход. read_only — намерение «только чтение»
            # (ApplicationIntent); права всё равно задаёт учётка.
            encryption=self.encryption, read_only=True,
            # См. шапку: без autocommit соединение держало бы открытую транзакцию.
            autocommit=True, conn_properties=self.session_setup())

    def _ensure(self):
        if self._connection is not None:
            return self._connection
        if not self.host or not self.user:
            raise OktellDbError('база Oktell не настроена: нет адреса или учётки')
        try:
            self._connection = self._open()
        except OktellDbError:
            raise
        except Exception as exc:  # noqa: BLE001 - у драйвера своё дерево исключений
            number = _error_number(exc)
            raise OktellDbError('вход в базу Oktell %s не удался: %s' % (self.describe(), _error_text(exc)),
                                auth=number == LOGIN_FAILED, kind=error_kind(number, connecting=True),
                                number=number) from exc
        log.info('База Oktell %s: соединение открыто', self.describe())
        return self._connection

    def query(self, sql):
        """Один SELECT → список строк-словарей с простыми значениями.

        Любая ошибка закрывает соединение: после таймаута или обрыва оно непригодно, а
        следующий вызов откроет новое. Повтора здесь нет — когда пробовать снова, решает
        коннектор (с паузой), иначе упавшая база получала бы вход за входом."""
        connection = self._ensure()
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(sql)
            fetched = cursor.fetchmany(MAX_ROWS + 1)
        except Exception as exc:  # noqa: BLE001
            self.close()
            number = _error_number(exc)
            raise OktellDbError('запрос к базе Oktell не выполнен: %s' % _error_text(exc),
                                kind=error_kind(number), number=number) from exc
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:  # noqa: BLE001
                    pass
        if len(fetched) > MAX_ROWS:
            # Остаток ответа остался в соединении — с ним оно непригодно.
            self.close()
            raise OktellDbError('ответ базы Oktell длиннее %d строк' % MAX_ROWS)
        rows = []
        for item in fetched:
            if not isinstance(item, dict):
                self.close()
                raise OktellDbError('драйвер вернул строку не словарём — соединение открыто без as_dict')
            # Ключи — только имена колонок: числовые индексы, которые драйвер добавляет к
            # словарю в некоторых версиях, в JSON не нужны.
            rows.append({str(key): json_value(value) for key, value in item.items()
                         if isinstance(key, str)})
        return rows

    def write_permissions(self):
        """Что учётка умеет сверх чтения — словами; пустой список значит «только чтение».

        Учётке коннектора положено только читать: текст запроса строит он сам, но при
        захвате контейнера учётка утекает, и тогда важно, что ею можно сделать. Проверка
        ловит администратора сервера и право писать во всю базу; право на отдельную таблицу
        она не видит — выдавать учётке такое незачем."""
        rows = self.query(WRITE_PERMISSIONS_SQL)
        first = rows[0] if rows else {}
        extra = []
        for name, _expr, label in WRITE_PERMISSIONS:
            try:
                granted = int(first.get(name) or 0) == 1
            except (TypeError, ValueError):
                granted = False
            if granted:
                extra.append(label)
        return extra

    def close(self):
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass


def from_config(config, **options):
    """Читатель по настройкам коннектора. `options` — query_timeout, row_limit, app_name."""
    return OktellDb(config.get('sql_host'), config.get('sql_port') or DEFAULT_PORT,
                    config.get('sql_user'), config.get('sql_password'),
                    config.get('sql_database') or DEFAULT_DATABASE,
                    encryption=config.get('sql_encryption') or 'require', **options)
