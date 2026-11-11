# -*- coding: utf-8 -*-
"""Каталог запросов портала к базе Oktell: имя набора + параметры → текст SELECT.

Зачем
-----
До 11.11.2026 каждый SELECT к базе Oktell портал слал мосту — чужому HTTP-прокси `/query`,
выставленному наружу из корпоративной сети, который исполнял любой присланный текст. 10.11 мост
лёг, и вместе с ним — отбивка линии, разбор перерывов, выгрузки статусов и звонков, биллинг.
Базу теперь читает коннектор Oktell на VM шлюза (`cdr_bridge/oktell_connector.py`): он стоит в
одной сети с базой, а с порталом говорит сам, подписанными запросами.

Шлюз не исполняет ничего, присланного порталом, кроме заданий из каталога, — и этот модуль и есть
каталог. Портал называет коннектору ИМЯ набора и его ПАРАМЕТРЫ (даты, числа, идентификаторы,
цифры номеров); текст запроса коннектор строит сам, этим же модулем, из своей копии кода. SQL от
портала на шлюз не приходит вовсе: взломанный портал может попросить те же отчёты за другие даты,
но не может ни прочитать таблицу, которой нет в каталоге, ни собрать свой запрос.

Один текст на две стороны
-------------------------
Построители портала (`bot_schedule2._oktell_*_sql`, `oktell_guard`, `resource_fte`, `call_qa`,
`test_numbers`) зовут `build()` и получают `Query` — обычную строку с текстом запроса, которая
помнит набор и параметры. Мосту уходит текст, коннектору — набор, параметры и отпечаток текста
(`Query.digest`). Коннектор строит текст заново и исполняет, только если отпечаток совпал: так
портал и шлюз, собранные из разных версий кода, не разойдутся молча — запрос просто не
выполнится, а портал скажет, что шлюз пора обновить.

Поэтому: ИЗМЕНИЛ ЗАПРОС ЗДЕСЬ — ОБНОВИ КОННЕКТОР НА VM ШЛЮЗА (порядок — `cdr_bridge/README.md`).
Отпечаток каталога закреплён в `tests/test_oktell_datasets.py`; тест упадёт и напомнит.

Параметры
---------
Каждый набор сам чистит свои параметры (`clean`): даты — восемь цифр существующего дня, числа —
целые в границах, идентификаторы — UUID, ключи номеров — десять ASCII-цифр. Всё, что на это не
похоже, — `ValueError`, а не «как-нибудь подставим». Единственная свободная строка — логин
оператора Oktell у сверки ограничителя: она уходит в литерал с удвоением кавычек.

Ни сети, ни базы, ни зависимостей за пределами стандартной библиотеки: модуль едет в образ шлюза
вместе с `cdr/`.
"""

import hashlib
import re
from datetime import date, datetime, timedelta

from cdr.oktell_wallboard import (FAILED_CALL, GREETING_ABANDON, MAX_TEST_KEYS, and_not_test,
                                  clean_test_keys, phone_key)

# Мост Oktell отдавал не больше тысячи строк на запрос, и вся постраничная логика портала
# («упёрлись в тысячу — дотягиваем по дню», keyset по Enumerator и Id) стоит на этом числе.
# Коннектор режет ответ так же.
MAX_ROWS = 1000

# Период одного запроса. Портал сам режет длинные периоды на окна (7 суток у выгрузок, до 150 у
# прогноза), так что больше года одним запросом — уже не отчёт, а ошибка или чужая рука.
MAX_SPAN_DAYS = 400

MAX_IDS = 1000
MAX_LOGIN = 128

_UUID = re.compile(r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\Z')
_COMPACT = re.compile(r'[0-9]{8}\Z')
_NOT_ASCII_DIGIT = re.compile(r'[^0-9]')

# Таблицы базы Oktell, которые читает каталог: ключ → (имя, колонки для проверки доступа).
# Проверка доступа — `SELECT TOP 0 <колонки> FROM <имя>`: строк не читает, а без права SELECT
# падает на разборе. У пользователей Oktell нужны только две колонки (там же лежат пароли).
TABLES = {
    'connections': ('oktell.dbo.A_Stat_Connections_1x1', '*'),
    'calls': ('oktell.dbo.Call_Systems_hst', '*'),
    'state_history': ('oktell.dbo.A_UserStateHistory', '*'),
    'ratings': ('oktell.dbo.quality_employes', '*'),
    'state_info_types': ('oktell.dbo.A_UserStateInfoTypes', '*'),
    'operators': ('oktell_cc_temp.dbo.A_Cube_CC_Cat_OperatorInfo', '*'),
    'operator_states': ('oktell_cc_temp.dbo.A_Cube_CC_OperatorStates', '*'),
    'tasks': ('oktell_cc_temp.dbo.A_Cube_CC_Cat_Task', '*'),
    'lunch_states': ('oktell_settings.dbo.A_TaskManager_CardLunchStates', '*'),
    'users': ('oktell_settings.dbo.A_Users', 'ID, Login'),
}


def table_probe_sql(key):
    """Запрос-проверка «читается ли таблица»: ни одной строки, только разбор и права."""
    name, columns = TABLES[key]
    return 'SELECT TOP 0 %s FROM %s' % (columns, name)


def digest(text):
    """Отпечаток текста запроса — то, чем портал и коннектор сверяют, что построили одно и то же."""
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()


class Query(str):
    """Текст запроса, который помнит набор каталога и параметры, из которых построен.

    Это обычная строка: мосту Oktell и тестам она уходит как текст. Коннектору на VM шлюза
    уходят `dataset`, `params` и `digest` — сам текст остаётся на портале."""

    def __new__(cls, text, dataset, params):
        self = super().__new__(cls, text)
        self.dataset = dataset
        self.params = params
        return self

    @property
    def digest(self):
        return digest(self)


# ── чистка параметров ────────────────────────────────────────────────────────

_MISSING = object()


def _raw(params, name, default=_MISSING):
    if name in params and params[name] is not None:
        return params[name]
    if default is _MISSING:
        raise ValueError('набор Oktell: не задан параметр %s' % name)
    return default


def _integer(params, name, minimum, maximum, default=_MISSING):
    value = _raw(params, name, default)
    if value is None:
        return None
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError
        number = int(value)
        if isinstance(value, float) and number != value:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise ValueError('набор Oktell: параметр %s — целое число' % name) from None
    if not minimum <= number <= maximum:
        raise ValueError('набор Oktell: параметр %s вне границ %d…%d' % (name, minimum, maximum))
    return number


def _clamped(params, name, minimum, maximum, default=_MISSING):
    """Целое, приведённое к границам, — там, где старый построитель сам подрезал значение."""
    number = _integer(params, name, -2 ** 62, 2 ** 62, default)
    return max(minimum, min(maximum, number))


def _day(value, name):
    text = str(value)
    if not _COMPACT.match(text):
        raise ValueError('набор Oktell: параметр %s — дата ГГГГММДД' % name)
    try:
        return datetime.strptime(text, '%Y%m%d').date()
    except ValueError:
        raise ValueError('набор Oktell: параметр %s — несуществующая дата' % name) from None


def _compact(params, name, default=_MISSING):
    value = _raw(params, name, default)
    if value is None:
        return None
    return _day(value, name).strftime('%Y%m%d')


def _period(params, first='date_from', last='date_to'):
    """Границы периода 'ГГГГММДД' (правая — исключительная). Период не длиннее MAX_SPAN_DAYS."""
    start, end = _compact(params, first), _compact(params, last)
    span = (_day(end, last) - _day(start, first)).days
    if span > MAX_SPAN_DAYS:
        raise ValueError('набор Oktell: период длиннее %d дней' % MAX_SPAN_DAYS)
    return start, end


def _stamp(params, name, fmt):
    """Момент времени строкой строго заданного формата."""
    text = str(_raw(params, name))
    try:
        parsed = datetime.strptime(text, fmt)
    except ValueError:
        raise ValueError('набор Oktell: параметр %s — момент времени %s' % (name, fmt)) from None
    if parsed.strftime(fmt) != text:
        raise ValueError('набор Oktell: параметр %s — момент времени %s' % (name, fmt))
    return text


def _stamps(params, fmt, first='since', last='until'):
    start, end = _stamp(params, first, fmt), _stamp(params, last, fmt)
    span = datetime.strptime(end, fmt) - datetime.strptime(start, fmt)
    if span > timedelta(days=MAX_SPAN_DAYS):
        raise ValueError('набор Oktell: период длиннее %d дней' % MAX_SPAN_DAYS)
    return start, end


def _uuids(params, name, maximum=MAX_IDS):
    """Список идентификаторов Oktell. Регистр и порядок — как прислали: текст запроса обязан
    совпасть с тем, что построил портал."""
    values = _raw(params, name)
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ValueError('набор Oktell: параметр %s — список идентификаторов' % name)
    if not values or len(values) > maximum:
        raise ValueError('набор Oktell: в параметре %s от 1 до %d идентификаторов' % (name, maximum))
    for value in values:
        if not isinstance(value, str) or not _UUID.match(value):
            raise ValueError('набор Oktell: в параметре %s не идентификатор' % name)
    return list(values)


def _test_keys(params, name='test_keys'):
    """Ключи «Реестра тестовых номеров»: строки из десяти ASCII-цифр, остальное отбрасывается
    (так же поступает test_numbers.keys.tsql_not_test)."""
    return list(clean_test_keys(params.get(name)))


def _minutes(params):
    """Окно суток в минутах от полуночи. 1440 — «до конца суток»: так зовут часть отчётов."""
    return _integer(params, 'minute_from', 0, 1440), _integer(params, 'minute_to', 0, 1440)


def _choice(params, name, allowed, default=_MISSING):
    value = _raw(params, name, default)
    if value not in allowed:
        raise ValueError('набор Oktell: параметр %s — одно из %s' % (name, ', '.join(map(str, allowed))))
    return value


# ── общие куски текста ───────────────────────────────────────────────────────

_GRT = GREETING_ABANDON.replace("'", "''")
_FAIL = FAILED_CALL.replace("'", "''")

# Оператор сидит на разных сторонах соединения в зависимости от типа звонка
# (A_Stat_Connections_1x1.ConnectionType): 1 = «изнутри наружу» (исходящий) — оператор на
# A-стороне; 5 = «снаружи внутрь» (входящий) — оператор на B-стороне.
EVAL_CONNECTION_TYPES = ('(1, 5)', '(1)', '(5)')
EVAL_OPERATOR_UID_EXPR = "CASE WHEN s.ConnectionType = 1 THEN s.AUserId ELSE s.BUserId END"
# Номер клиента (вторая сторона): для исходящего — B, для входящего — A.
EVAL_PHONE_EXPR = (
    "CASE WHEN s.ConnectionType = 1 "
    "THEN CASE WHEN LEN(s.BOutNumber) >= 10 THEN s.BOutNumber WHEN LEN(s.AOutNumber) >= 10 THEN s.AOutNumber "
    "ELSE COALESCE(NULLIF(s.BOutNumber, ''), s.AOutNumber) END "
    "ELSE CASE WHEN LEN(s.AOutNumber) >= 10 THEN s.AOutNumber WHEN LEN(s.BOutNumber) >= 10 THEN s.BOutNumber "
    "ELSE COALESCE(NULLIF(s.AOutNumber, ''), s.BOutNumber) END END"
)

# Цепочка не нашлась — разговор нулевой, а не total_length: смешивать в одной колонке две
# разные величины нельзя.
BILLING_TALK_EXPR = "COALESCE(k.talk_sec, 0)"


def _day_before(compact):
    return (datetime.strptime(compact, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')


def _minute_filter(minute_from, minute_to, column='t.dt_insert'):
    if int(minute_from) > 0 or int(minute_to) < 1439:
        return (
            f"AND (DATEPART(HOUR, {column}) * 60 + DATEPART(MINUTE, {column})) "
            f"BETWEEN {int(minute_from)} AND {int(minute_to)} "
        )
    return ''


def _talk_join(date_from, date_to, chain_expr='t.chainid', alias='k'):
    """Чистый разговор цепочки: плечи ct=5 от ответа оператора до конца коммутации. Окно по
    TimeStart расширено на сутки назад: звонок мог начаться до полуночи."""
    return (
        "LEFT JOIN (SELECT IdChain, SUM(DATEDIFF(second, TimeAnswer, TimeStop)) AS talk_sec "
        "FROM oktell.dbo.A_Stat_Connections_1x1 "
        f"WHERE ConnectionType = 5 AND TimeStart >= '{_day_before(date_from)}' AND TimeStart < '{date_to}' "
        f"GROUP BY IdChain) {alias} ON {alias}.IdChain = TRY_CAST({chain_expr} AS uniqueidentifier) "
    )


def _duration_clause(min_duration, max_duration):
    clause = f"AND DATEDIFF(second, s.TimeAnswer, s.TimeStop) >= {int(min_duration)} "
    if max_duration > 0:
        clause += f"AND DATEDIFF(second, s.TimeAnswer, s.TimeStop) <= {int(max_duration)} "
    return clause


def _id_literals(values):
    return ", ".join("'" + value + "'" for value in values)


# ── наборы ───────────────────────────────────────────────────────────────────
# Каждый набор — пара функций: `clean(сырые параметры) → канонические` и `sql(**канонические) →
# текст`. Тексты перенесены из построителей портала буква в букву (сверено эталоном на 220
# вариантах параметров при переносе 11.11.2026).

DATASETS = {}


class Dataset:
    def __init__(self, name, tables, clean, sql, sample, title, tables_for=None):
        self.name = name
        self.tables = tuple(tables)
        self.clean = clean
        self.sql = sql
        self.sample = sample
        self.title = title
        self.tables_for = tables_for


def _register(name, tables, title, sample, tables_for=None):
    """tables — все таблицы, которые набор может читать; tables_for(параметры) — те, что нужны
    запросу с этими параметрами (если от параметров зависит). sample(день) → параметры узкого
    окна для проверки установки (`--check --datasets`) и для отпечатка каталога."""
    def decorator(namespace):
        DATASETS[name] = Dataset(name, tables, namespace.clean, namespace.sql, sample, title, tables_for)
        return namespace
    return decorator


def _c(day):
    return day.strftime('%Y%m%d')


# 1. Статусы операторов — «Учёт часов» ------------------------------------------------------------

@_register('status_page', ('state_history', 'operators', 'lunch_states', 'state_info_types'),
           'история статусов операторов, страница',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1)), 'cursor': -1})
class _StatusPage:
    @staticmethod
    def clean(params):
        date_from, date_to = _period(params)
        return {'date_from': date_from, 'date_to': date_to,
                'cursor': _integer(params, 'cursor', -1, 2 ** 62)}

    @staticmethod
    def sql(date_from, date_to, cursor):
        # Один SELECT, keyset по Enumerator: ответ режется на 1000 строк → листаем по курсору.
        return (
            "SELECT TOP 1000 "
            "oi.Name AS OperatorName, "
            "CASE h.State "
            "WHEN 0 THEN N'Выключен' WHEN 1 THEN N'Готов' WHEN 2 THEN N'Перерыв' "
            "WHEN 3 THEN N'Нет на месте' WHEN 5 THEN N'Занят' WHEN 6 THEN N'Зарезервировано' "
            "WHEN 7 THEN N'Без телефона' "
            "ELSE N'Неизвестно (' + CAST(h.State AS nvarchar(10)) + N')' END AS StateName, "
            "COALESCE(ls.Name, ist.Name, NULLIF(LTRIM(RTRIM(h.Info)), N'')) AS StateNote, "
            "CONVERT(varchar(19), h.TimeChange, 120) AS TimeChange, "
            "h.Enumerator AS Enumerator "
            "FROM oktell.dbo.A_UserStateHistory h "
            "JOIN oktell_cc_temp.dbo.A_Cube_CC_Cat_OperatorInfo oi ON oi.Id = h.UserId "
            "LEFT JOIN oktell_settings.dbo.A_TaskManager_CardLunchStates ls ON ls.Id = h.ICode "
            "LEFT JOIN oktell.dbo.A_UserStateInfoTypes ist ON ist.Id = h.ICode "
            f"WHERE h.TimeChange >= '{date_from}' AND h.TimeChange < '{date_to}' "
            f"AND h.Enumerator > {int(cursor)} "
            "ORDER BY h.Enumerator"
        )


# 2. Звонки операторов по дням — «Учёт часов» -----------------------------------------------------

@_register('call_count', ('operator_states', 'operators', 'tasks'),
           'звонки операторов по дням',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1))})
class _CallCount:
    @staticmethod
    def clean(params):
        date_from, date_to = _period(params)
        return {'date_from': date_from, 'date_to': date_to}

    @staticmethod
    def sql(date_from, date_to):
        return (
            "SELECT oi.Name AS OperatorName, "
            "CONVERT(varchar(10), os.DateStart, 23) AS CallDate, "
            "COUNT(*) AS CallCount "
            "FROM oktell_cc_temp.dbo.A_Cube_CC_OperatorStates os "
            "INNER JOIN oktell_cc_temp.dbo.A_Cube_CC_Cat_OperatorInfo oi ON oi.Id = os.IdOperator "
            "INNER JOIN oktell_cc_temp.dbo.A_Cube_CC_Cat_Task tk ON tk.Id = os.IdTask "
            "WHERE os.State IN (6, 14) AND os.CallResult IS NOT NULL "
            f"AND os.DateStart >= '{date_from}' AND os.DateStart < '{date_to}' "
            "GROUP BY oi.Name, CONVERT(varchar(10), os.DateStart, 23)"
        )


# 3. Входящие почасово — «Расчёт ресурсов» --------------------------------------------------------

@_register('resource_hourly', ('calls',), 'входящие звонки линии по часам',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1)), 'test_keys': []})
class _ResourceHourly:
    @staticmethod
    def clean(params):
        date_from, date_to = _period(params)
        return {'date_from': date_from, 'date_to': date_to, 'test_keys': _test_keys(params)}

    @staticmethod
    def sql(date_from, date_to, test_keys):
        grt, fail = _GRT, _FAIL
        return (
            "SELECT CONVERT(varchar(10), t.dt_insert, 23) AS report_date, "
            "DATEPART(HOUR, t.dt_insert) AS hour, "
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (13,19,5) THEN 1 ELSE 0 END) AS received_calls, "
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) THEN 1 ELSE 0 END) AS accepted_calls, "
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) THEN t.total_length ELSE 0 END) AS talk_time_seconds, "
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) THEN t.LenQueue ELSE 0 END) AS success_wait_seconds, "
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (13,19,5) THEN t.total_length ELSE 0 END) AS total_time_seconds, "
            f"SUM(CASE WHEN t.result_call = N'{grt}' THEN 1 ELSE 0 END) AS greeting_abandoned, "
            f"SUM(CASE WHEN t.result_call = N'{grt}' THEN t.total_length ELSE 0 END) AS greeting_time_seconds, "
            f"SUM(CASE WHEN t.call_result IN (13,19) AND t.result_call NOT IN (N'{grt}', N'{fail}') THEN 1 ELSE 0 END) AS queue_abandoned, "
            f"SUM(CASE WHEN t.call_result IN (13,19) AND t.result_call NOT IN (N'{grt}', N'{fail}') THEN t.total_length ELSE 0 END) AS queue_wait_seconds, "
            f"SUM(CASE WHEN t.call_result IN (13,19) AND t.result_call NOT IN (N'{grt}', N'{fail}') THEN t.LenQueue ELSE 0 END) AS lost_lenqueue_seconds "
            "FROM oktell.dbo.Call_Systems_hst t "
            f"WHERE t.dt_insert >= '{date_from}' AND t.dt_insert < '{date_to}' "
            f"AND t.taxi_park <> '' AND t.route = 'incoming' AND t.result_call <> N'{fail}' "
            f"{and_not_test('t.[number]', test_keys)}"
            "GROUP BY CONVERT(varchar(10), t.dt_insert, 23), DATEPART(HOUR, t.dt_insert)"
        )


# 4. «Биллинг Oktell»: день × таксопарк [× линия] или день × час ---------------------------------

BILLING_GROUPS = ('park', 'hour', 'line')


def _billing_tables(params):
    """Разрез «час» читает только звонки; «таксопарк» — ещё оценки водителей; «линия» — ещё и
    соединения (набранная линия). Учётке без права на оценки разрез «час» всё равно доступен."""
    group_by = params.get('group_by')
    if group_by == 'hour':
        return ('calls',)
    if group_by == 'line':
        return ('calls', 'connections', 'ratings')
    return ('calls', 'ratings')


@_register('billing', ('calls', 'connections', 'ratings'), 'биллинг: день × таксопарк, линия или час',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1)), 'minute_from': 0,
                        'minute_to': 1439, 'sl_seconds': 20, 'group_by': 'line', 'test_keys': []},
           tables_for=_billing_tables)
class _Billing:
    @staticmethod
    def clean(params):
        date_from, date_to = _period(params)
        minute_from, minute_to = _minutes(params)
        group_by = params.get('group_by')
        return {'date_from': date_from, 'date_to': date_to,
                'minute_from': minute_from, 'minute_to': minute_to,
                'sl_seconds': _integer(params, 'sl_seconds', 0, 86400),
                # Неизвестный разрез старый построитель считал разрезом по таксопаркам.
                'group_by': group_by if group_by in ('hour', 'line') else 'park',
                'test_keys': _test_keys(params)}

    @staticmethod
    def sql(date_from, date_to, minute_from, minute_to, sl_seconds, group_by, test_keys):
        grt, fail = _GRT, _FAIL
        minute_filter = _minute_filter(minute_from, minute_to)
        line_select = ''
        line_join = ''
        line_group = ''
        # Разрез «Группировка» — день x час без таксопарка. Метрики те же, что у остальных
        # разрезов, поэтому итог дня по часам сходится с итогом по таксопаркам.
        key_select = "t.taxi_park AS taxi_park, "
        key_group = "t.taxi_park"
        if group_by == 'hour':
            key_select = "DATEPART(HOUR, t.dt_insert) AS report_hour, "
            key_group = "DATEPART(HOUR, t.dt_insert)"
        # Оценка водителя после разговора (задача #298): IVR-опрос пишет балл 1–5 в
        # quality_employes по цепочке звонка, '0' — водитель ничего не нажал, в среднее не идёт.
        # Считаем только у обслуженных: оценивают разговор с оператором. GROUP BY chainid держит
        # одну оценку на цепочку, чтобы повторный опрос не удвоил звонок в сумме.
        rating_select = (
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) AND qe.rating IS NOT NULL THEN qe.rating ELSE 0 END) AS rating_sum, "
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) AND qe.rating IS NOT NULL THEN 1 ELSE 0 END) AS rating_count "
        )
        rating_join = (
            "LEFT JOIN (SELECT chainid, MAX(CASE WHEN answer IN (N'1', N'2', N'3', N'4', N'5') "
            "THEN CAST(answer AS int) END) AS rating FROM oktell.dbo.quality_employes GROUP BY chainid) qe "
            "ON qe.chainid = t.chainid "
        )
        if group_by == 'hour':
            # В «Группировке» оценки не показываются, а подзапрос по quality_employes
            # агрегирует всю таблицу опросов — в этом разрезе его не гоняем.
            rating_select = "0 AS rating_sum, 0 AS rating_count "
            rating_join = ''
        if group_by == 'line':
            # Набранная SIP-линия: лег ConnectionType=4 (снаружи в IVR) той же цепочки.
            # Цепочки бывают с несколькими легами ct=4 (повторные заходы в IVR) — дедуп через
            # MIN; разные номера внутри одной цепочки — единичные случаи, MIN детерминирован.
            # Окно по TimeStart расширено на день назад: звонок может начаться до полуночи.
            line_select = "COALESCE(c.line_number, N'') AS line_number, "
            line_join = (
                "LEFT JOIN (SELECT IdChain, MIN(ANumberDialed) AS line_number "
                "FROM oktell.dbo.A_Stat_Connections_1x1 "
                f"WHERE ConnectionType = 4 AND TimeStart >= '{_day_before(date_from)}' AND TimeStart < '{date_to}' "
                "GROUP BY IdChain) c ON c.IdChain = TRY_CAST(t.chainid AS uniqueidentifier) "
            )
            line_group = ", COALESCE(c.line_number, N'')"
        return (
            "SELECT CONVERT(varchar(10), t.dt_insert, 23) AS report_date, "
            f"{key_select}"
            f"{line_select}"
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (13,19,5) THEN 1 ELSE 0 END) AS arrived, "
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) THEN 1 ELSE 0 END) AS served, "
            # числитель SL: отвечены оператором И ждали в очереди не дольше порога
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) AND t.LenQueue <= {int(sl_seconds)} THEN 1 ELSE 0 END) AS served_sl, "
            f"SUM(CASE WHEN t.result_call = N'{grt}' THEN 1 ELSE 0 END) AS greet_drop, "
            # Разговор — как в отчёте Oktell: принятый звонок целиком.
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) THEN t.total_length ELSE 0 END) AS talk_seconds, "
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) THEN t.LenQueue ELSE 0 END) AS wait_ok_seconds, "
            f"SUM(CASE WHEN t.call_result IN (13,19) AND t.result_call NOT IN (N'{grt}', N'{fail}') THEN t.LenQueue ELSE 0 END) AS wait_lost_seconds, "
            f"SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (13,19,5) THEN t.total_length ELSE 0 END) AS total_seconds, "
            f"{rating_select}"
            "FROM oktell.dbo.Call_Systems_hst t "
            f"{line_join}"
            f"{rating_join}"
            f"WHERE t.dt_insert >= '{date_from}' AND t.dt_insert < '{date_to}' "
            f"AND t.taxi_park <> '' AND t.route = 'incoming' AND t.result_call <> N'{fail}' "
            f"{minute_filter}"
            f"{and_not_test('t.[number]', test_keys)}"
            f"GROUP BY CONVERT(varchar(10), t.dt_insert, 23), {key_group}{line_group}"
        )


# 5–6. «Биллинг Oktell»: построчная детализация ---------------------------------------------------
# Одна строка = один входящий звонок. Снимок по максимальному Id фиксирует выборку при переходе
# между страницами, чтобы новые звонки текущего дня не сдвигали уже просмотренные строки.

def _detail_source(date_from, date_to, minute_from, minute_to, max_call_id, test_keys):
    grt, fail = _GRT, _FAIL
    minute_filter = _minute_filter(minute_from, minute_to)
    id_filter = f"AND t.Id <= {int(max_call_id)} " if max_call_id is not None else ''
    return (
        "FROM oktell.dbo.Call_Systems_hst t "
        f"WHERE t.dt_insert >= '{date_from}' AND t.dt_insert < '{date_to}' "
        f"AND t.taxi_park <> '' AND t.route = 'incoming' AND t.result_call <> N'{fail}' "
        f"AND (t.result_call = N'{grt}' OR (t.result_call <> N'{grt}' AND t.call_result IN (5,13,19))) "
        f"{id_filter}{minute_filter}"
        f"{and_not_test('t.[number]', test_keys)}"
    )


def _detail_select():
    return (
        "t.Id AS call_id, "
        "t.dt_insert AS occurred_at, "
        "t.taxi_park AS taxi_park, "
        "COALESCE(t.[number], N'') AS driver_number, "
        "t.chainid AS chain_id, t.result_call AS result_call, "
        "t.call_result AS call_result, t.total_length AS total_length"
    )


def _detail_line_apply(call_alias, date_from, date_to):
    return (
        "OUTER APPLY (SELECT TOP 1 s.ANumberDialed AS line_number, s.AOutNumber AS caller_number "
        "FROM oktell.dbo.A_Stat_Connections_1x1 s "
        f"WHERE s.IdChain = TRY_CAST({call_alias}.chain_id AS uniqueidentifier) "
        "AND s.ConnectionType = 4 "
        f"AND s.TimeStart >= '{_day_before(date_from)}' AND s.TimeStart < '{date_to}' "
        "ORDER BY s.TimeStart ASC, s.Id ASC) c "
    )


@_register('billing_detail_page', ('calls', 'connections'), 'биллинг: детализация, страница экрана',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1)), 'minute_from': 0,
                        'minute_to': 1439, 'page': 1, 'per_page': 5, 'snapshot_id': None, 'test_keys': []})
class _BillingDetailPage:
    @staticmethod
    def clean(params):
        date_from, date_to = _period(params)
        minute_from, minute_to = _minutes(params)
        return {'date_from': date_from, 'date_to': date_to,
                'minute_from': minute_from, 'minute_to': minute_to,
                'page': _clamped(params, 'page', 1, 2 ** 31),
                'per_page': _clamped(params, 'per_page', 1, 100),
                'snapshot_id': _integer(params, 'snapshot_id', -2 ** 62, 2 ** 62, None),
                'test_keys': _test_keys(params)}

    @staticmethod
    def sql(date_from, date_to, minute_from, minute_to, page, per_page, snapshot_id, test_keys):
        grt = _GRT
        row_from = (page - 1) * per_page + 1
        row_to = row_from + per_page - 1
        source_sql = _detail_source(date_from, date_to, minute_from, minute_to, snapshot_id, test_keys)
        return (
            "SELECT q.call_id, CONVERT(varchar(19), q.occurred_at, 120) AS occurred_at, "
            "q.taxi_park, COALESCE(c.line_number, N'') AS line_number, "
            "COALESCE(NULLIF(q.driver_number, N''), NULLIF(c.caller_number, N''), N'') AS driver_number, "
            f"CASE WHEN q.result_call = N'{grt}' THEN 1 ELSE 0 END AS ivr_drop, "
            f"CASE WHEN q.result_call <> N'{grt}' AND q.call_result IN (13,19) THEN 1 ELSE 0 END AS queue_drop, "
            # Разговор — как в отчёте Oktell, тот же, что у «Таксопарков»: сумма по детализации
            # сходится с их итогом.
            f"CASE WHEN q.result_call <> N'{grt}' AND q.call_result = 5 THEN q.total_length ELSE 0 END AS talk_seconds, "
            "q.total_count, q.snapshot_id "
            "FROM (SELECT ranked.* FROM (SELECT "
            f"{_detail_select()}, "
            "COUNT(*) OVER() AS total_count, MAX(t.Id) OVER() AS snapshot_id, "
            "ROW_NUMBER() OVER (ORDER BY t.dt_insert DESC, t.Id DESC) AS row_num "
            f"{source_sql}"
            ") ranked "
            f"WHERE ranked.row_num BETWEEN {row_from} AND {row_to}) q "
            f"{_detail_line_apply('q', date_from, date_to)}"
            "ORDER BY q.row_num"
        )


@_register('billing_detail_export', ('calls', 'connections'), 'биллинг: детализация, страница выгрузки',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1)), 'minute_from': 0,
                        'minute_to': 1439, 'max_call_id': None, 'size': 5, 'test_keys': []})
class _BillingDetailExport:
    @staticmethod
    def clean(params):
        date_from, date_to = _period(params)
        minute_from, minute_to = _minutes(params)
        return {'date_from': date_from, 'date_to': date_to,
                'minute_from': minute_from, 'minute_to': minute_to,
                'max_call_id': _integer(params, 'max_call_id', -2 ** 62, 2 ** 62, None),
                'size': _integer(params, 'size', 1, MAX_ROWS),
                'test_keys': _test_keys(params)}

    @staticmethod
    def sql(date_from, date_to, minute_from, minute_to, max_call_id, size, test_keys):
        grt = _GRT
        source_sql = _detail_source(date_from, date_to, minute_from, minute_to, max_call_id, test_keys)
        return (
            "SELECT p.call_id, CONVERT(varchar(19), p.occurred_at, 120) AS occurred_at, "
            "p.taxi_park, COALESCE(c.line_number, N'') AS line_number, "
            "COALESCE(NULLIF(p.driver_number, N''), NULLIF(c.caller_number, N''), N'') AS driver_number, "
            f"CASE WHEN p.result_call = N'{grt}' THEN 1 ELSE 0 END AS ivr_drop, "
            f"CASE WHEN p.result_call <> N'{grt}' AND p.call_result IN (13,19) THEN 1 ELSE 0 END AS queue_drop, "
            f"CASE WHEN p.result_call <> N'{grt}' AND p.call_result = 5 THEN p.total_length ELSE 0 END AS talk_seconds "
            f"FROM (SELECT TOP {size} {_detail_select()} "
            f"{source_sql}"
            "ORDER BY t.Id DESC) p "
            f"{_detail_line_apply('p', date_from, date_to)}"
            "ORDER BY p.call_id DESC"
        )


# 7–8. «Биллинг Oktell»: разрез по операторам -----------------------------------------------------

@_register('billing_operator_calls', ('calls', 'operators', 'connections'), 'биллинг: звонки операторов',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1)), 'minute_from': 0,
                        'minute_to': 1439, 'test_keys': []})
class _BillingOperatorCalls:
    @staticmethod
    def clean(params):
        date_from, date_to = _period(params)
        minute_from, minute_to = _minutes(params)
        return {'date_from': date_from, 'date_to': date_to,
                'minute_from': minute_from, 'minute_to': minute_to, 'test_keys': _test_keys(params)}

    @staticmethod
    def sql(date_from, date_to, minute_from, minute_to, test_keys):
        """Звонки оператора за день, обе стороны сразу: входящие и исходящие.

        Фильтр `taxi_park <> ''` стоит только на входящих: у исходящих парк не заполнен у 58 %
        строк, и это не мусор, а обычный звонок водителю. Две длительности на направление:
        call_* — разговор (ATT), handle_* — всё время обработки звонка (AHT)."""
        grt, fail = _GRT, _FAIL
        minute_filter = _minute_filter(minute_from, minute_to)
        incoming = "t.route = 'incoming' AND t.taxi_park <> ''"
        outgoing = "t.route = 'outgoing'"
        return (
            "SELECT CONVERT(varchar(10), t.dt_insert, 23) AS report_date, "
            "oi.Name AS operator_name, "
            f"SUM(CASE WHEN {incoming} THEN 1 ELSE 0 END) AS served_in, "
            f"SUM(CASE WHEN {incoming} THEN {BILLING_TALK_EXPR} ELSE 0 END) AS call_in_seconds, "
            f"SUM(CASE WHEN {incoming} THEN t.total_length ELSE 0 END) AS handle_in_seconds, "
            f"SUM(CASE WHEN {outgoing} THEN 1 ELSE 0 END) AS served_out, "
            f"SUM(CASE WHEN {outgoing} THEN t.total_length ELSE 0 END) AS call_out_seconds, "
            f"SUM(CASE WHEN {outgoing} THEN t.total_length ELSE 0 END) AS handle_out_seconds "
            "FROM oktell.dbo.Call_Systems_hst t "
            "JOIN oktell_cc_temp.dbo.A_Cube_CC_Cat_OperatorInfo oi ON oi.Id = TRY_CAST(t.id_operator AS uniqueidentifier) "
            f"{_talk_join(date_from, date_to)}"
            f"WHERE t.dt_insert >= '{date_from}' AND t.dt_insert < '{date_to}' "
            "AND t.route IN ('incoming', 'outgoing') "
            f"AND t.result_call NOT IN (N'{grt}', N'{fail}') AND t.call_result IN (5) "
            f"{minute_filter}"
            f"{and_not_test('t.[number]', test_keys)}"
            "GROUP BY CONVERT(varchar(10), t.dt_insert, 23), oi.Name"
        )


@_register('billing_operator_states', ('operator_states', 'operators'), 'биллинг: состояния операторов',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1)), 'minute_from': 0,
                        'minute_to': 1439})
class _BillingOperatorStates:
    @staticmethod
    def clean(params):
        date_from, date_to = _period(params)
        minute_from, minute_to = _minutes(params)
        return {'date_from': date_from, 'date_to': date_to,
                'minute_from': minute_from, 'minute_to': minute_to}

    @staticmethod
    def sql(date_from, date_to, minute_from, minute_to):
        # Коды состояний куба: 2 ожидание ответа абонента, 3 обратный вызов, 6 разговор по
        # задаче, 7 поствызывная обработка, 9 ПЕРЕРЫВ, 10 ГОТОВ, 12 техническое распределение,
        # 33 удержание. IsOutput: 0 — входящий, 1 — исходящий.
        minute_filter = _minute_filter(minute_from, minute_to, column='s.DateTimeStart')
        return (
            "SELECT CONVERT(varchar(10), s.DateStart, 23) AS report_date, "
            "oi.Name AS operator_name, "
            "SUM(CASE WHEN s.State = 6 AND s.IsOutput = 0 THEN s.LenTime ELSE 0 END) AS talk_in_seconds, "
            "SUM(CASE WHEN s.State = 6 AND s.IsOutput = 1 THEN s.LenTime ELSE 0 END) AS talk_out_seconds, "
            "SUM(CASE WHEN s.State = 7 AND s.IsOutput = 0 THEN s.LenTime ELSE 0 END) AS postproc_in_seconds, "
            "SUM(CASE WHEN s.State = 7 AND s.IsOutput = 1 THEN s.LenTime ELSE 0 END) AS postproc_out_seconds, "
            "SUM(CASE WHEN s.State = 33 AND s.IsOutput = 0 THEN s.LenTime ELSE 0 END) AS hold_in_seconds, "
            "SUM(CASE WHEN s.State = 33 AND s.IsOutput = 1 THEN s.LenTime ELSE 0 END) AS hold_out_seconds, "
            # «Ожидание» — это State=10 «Готов»: оператор на линии и ждёт звонка.
            "SUM(CASE WHEN s.State = 10 THEN s.LenTime ELSE 0 END) AS wait_seconds, "
            # «Пауза» — State=9 «Перерыв» (в нём же лежат подпричины перерыва в ICode).
            "SUM(CASE WHEN s.State = 9 THEN s.LenTime ELSE 0 END) AS pause_seconds, "
            "SUM(CASE WHEN s.State = 3 AND s.IsOutput = 0 THEN s.LenTime ELSE 0 END) AS dial_in_seconds, "
            "SUM(CASE WHEN s.State = 3 AND s.IsOutput = 1 THEN s.LenTime ELSE 0 END) AS dial_out_seconds, "
            "SUM(CASE WHEN s.State = 12 THEN s.LenTime ELSE 0 END) AS dial_other_seconds, "
            # «Дозвон» исходящего — State=2 «Ожидание ответа абонента».
            "SUM(CASE WHEN s.State = 2 AND s.IsOutput = 1 THEN s.LenTime ELSE 0 END) AS dial_wait_out_seconds "
            "FROM oktell_cc_temp.dbo.A_Cube_CC_OperatorStates s "
            "JOIN oktell_cc_temp.dbo.A_Cube_CC_Cat_OperatorInfo oi ON oi.Id = s.IdOperator "
            f"WHERE s.DateTimeStart >= '{date_from}' AND s.DateTimeStart < '{date_to}' "
            f"{minute_filter}"
            "GROUP BY CONVERT(varchar(10), s.DateStart, 23), oi.Name"
        )


# 9. Почасовая таблица отбивки «Линии» ------------------------------------------------------------

@_register('wallboard_hourly', ('calls',), 'почасовая таблица отбивки линии',
           lambda day: {'hour_to': None, 'day': _c(day), 'sl_seconds': 20, 'test_keys': []})
class _WallboardHourly:
    @staticmethod
    def clean(params):
        return {'hour_to': _integer(params, 'hour_to', 0, 24, None),
                'day': _compact(params, 'day', None),
                'sl_seconds': _integer(params, 'sl_seconds', 0, 86400),
                'test_keys': _test_keys(params)}

    @staticmethod
    def sql(hour_to, day, sl_seconds, test_keys):
        """Почасовая таблица за день Oktell: принято / поступило / потеряно / разговор / SL.

        hour_to — включительная верхняя граница часа. day — явные сутки: нужны отбивке в
        полночь, когда последний полный час уже вчерашний; границы суток тогда задаются
        литералом, а не GETDATE() Oktell — иначе отставшие на пару секунд часы сервера унесли
        бы запрос на позавчера."""
        grt, fail = _GRT, _FAIL
        cutoff = ''
        if hour_to is not None:
            cutoff = f"AND DATEPART(HOUR, t.dt_insert) <= {int(hour_to)} "
        if day is None:
            day_start = "CONVERT(date, GETDATE())"
            day_end = "DATEADD(day, 1, CONVERT(date, GETDATE()))"
        else:
            day_start = "'%s'" % day
            day_end = "'%s'" % (datetime.strptime(day, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
        return (
            "SELECT DATEPART(HOUR, t.dt_insert) AS hh, "
            f"ISNULL(SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) THEN 1 ELSE 0 END), 0) AS served, "
            f"ISNULL(SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (13,19,5) THEN 1 ELSE 0 END), 0) AS arrived, "
            f"ISNULL(SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (13,19) THEN 1 ELSE 0 END), 0) AS lost, "
            f"ISNULL(SUM(CASE WHEN t.result_call = N'{grt}' THEN 1 ELSE 0 END), 0) AS greet_drop, "
            f"ISNULL(SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) AND t.LenQueue <= {sl_seconds} THEN 1 ELSE 0 END), 0) AS served_sl, "
            f"ISNULL(SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) THEN t.LenQueue ELSE 0 END), 0) AS wait_served_seconds, "
            f"ISNULL(SUM(CASE WHEN t.result_call <> N'{grt}' AND t.call_result IN (5) THEN t.total_length ELSE 0 END), 0) AS talk_seconds "
            "FROM oktell.dbo.Call_Systems_hst t "
            "WHERE t.route = 'incoming' AND t.taxi_park <> '' "
            f"AND t.result_call <> N'{fail}' "
            f"AND t.dt_insert >= {day_start} "
            f"AND t.dt_insert < {day_end} "
            f"{cutoff}"
            f"{and_not_test('t.[number]', test_keys)}"
            "GROUP BY DATEPART(HOUR, t.dt_insert) ORDER BY hh"
        )


# 10. Эпизоды перерывов — разбор «перерыв не по графику» ------------------------------------------

BREAK_STAMP = '%Y-%m-%dT%H:%M:%S'


@_register('break_episodes', ('state_history', 'operators'), 'эпизоды статуса «Перерыв»',
           lambda day: {'since': day.strftime('%Y-%m-%dT09:00:00'), 'until': day.strftime('%Y-%m-%dT12:00:00'),
                        'icode': 4})
class _BreakEpisodes:
    @staticmethod
    def clean(params):
        since, until = _stamps(params, BREAK_STAMP)
        return {'since': since, 'until': until, 'icode': _integer(params, 'icode', 0, 100000)}

    @staticmethod
    def sql(since, until, icode):
        """Эпизоды статуса «Перерыв» за окно: кто, когда вышел и когда вернулся.

        Конец эпизода — время СЛЕДУЮЩЕЙ записи истории того же оператора (LEAD), поэтому окно
        подзапроса берётся целиком, а фильтр по причине навешивается снаружи. Формат 126
        (ISO 8601) вместо 'YYYYMMDD': локаль сервера dmy, а тут нужны и часы."""
        return (
            "SELECT oi.Name AS operator_name, "
            "CONVERT(varchar(19), x.TimeChange, 120) AS started_at, "
            "CONVERT(varchar(19), x.next_change, 120) AS ended_at "
            "FROM (SELECT h.UserId, h.State, h.ICode, h.TimeChange, "
            "LEAD(h.TimeChange) OVER (PARTITION BY h.UserId ORDER BY h.Enumerator) AS next_change "
            "FROM oktell.dbo.A_UserStateHistory h "
            f"WHERE h.TimeChange >= CONVERT(datetime, '{since}', 126) "
            f"AND h.TimeChange < CONVERT(datetime, '{until}', 126)) x "
            "JOIN oktell_cc_temp.dbo.A_Cube_CC_Cat_OperatorInfo oi ON oi.Id = x.UserId "
            f"WHERE x.State = 2 AND x.ICode = {int(icode)} "
            "ORDER BY oi.Name, x.TimeChange"
        )


# 11. Кто положил трубку — журнал «ИИ-оценки» -----------------------------------------------------

_SAMPLE_ID = '00000000-0000-0000-0000-000000000000'


@_register('call_end_party', ('connections',), 'кто завершил соединение',
           lambda day: {'conn_ids': [_SAMPLE_ID]})
class _CallEndParty:
    @staticmethod
    def clean(params):
        return {'conn_ids': _uuids(params, 'conn_ids')}

    @staticmethod
    def sql(conn_ids):
        return (
            "SELECT LOWER(CONVERT(varchar(36), s.Id)) AS conn_id, "
            "s.ConnectionType AS ct, s.StopSide AS stop_side, s.ReasonStop AS reason_stop "
            "FROM oktell.dbo.A_Stat_Connections_1x1 s "
            f"WHERE s.Id IN ({_id_literals(conn_ids)})"
        )


# 12–13. Звонки на оценку: операторы с записями и случайная выборка -------------------------------

def _eval_common(params):
    date_from, date_to = _period(params)
    return {'date_from': date_from, 'date_to': date_to,
            'min_duration': _integer(params, 'min_duration', 0, 86400, 0),
            'max_duration': _integer(params, 'max_duration', 0, 86400, 0),
            'conn_types': _choice(params, 'conn_types', EVAL_CONNECTION_TYPES, EVAL_CONNECTION_TYPES[0]),
            'test_keys': _test_keys(params)}


@_register('eval_operators', ('connections', 'operators'), 'операторы с записанными звонками',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1)), 'min_duration': 20,
                        'max_duration': 600, 'conn_types': '(1, 5)', 'test_keys': []})
class _EvalOperators:
    clean = staticmethod(_eval_common)

    @staticmethod
    def sql(date_from, date_to, min_duration, max_duration, conn_types, test_keys):
        # Операторы с подходящими записанными звонками за период + сколько доступно. UUID
        # оператора считаем в подзапросе, группируем снаружи. Джойн к OperatorInfo по
        # операторской стороне заодно отсеивает внешнюю линию/IVR (их Id нет в каталоге).
        return (
            "SELECT t.auserid, t.operator_name, COUNT(*) AS available FROM ("
            f"SELECT LOWER(CONVERT(varchar(36), {EVAL_OPERATOR_UID_EXPR})) AS auserid, oi.Name AS operator_name "
            "FROM oktell.dbo.A_Stat_Connections_1x1 s "
            f"JOIN oktell_cc_temp.dbo.A_Cube_CC_Cat_OperatorInfo oi ON oi.Id = {EVAL_OPERATOR_UID_EXPR} "
            f"WHERE s.TimeStart >= '{date_from}' AND s.TimeStart < '{date_to}' "
            f"AND s.ConnectionType IN {conn_types} AND s.IsRecorded = 1 AND s.TimeStop IS NOT NULL "
            + _duration_clause(min_duration, max_duration)
            + and_not_test(f"({EVAL_PHONE_EXPR})", test_keys) +
            ") t GROUP BY t.auserid, t.operator_name"
        )


@_register('eval_sample', ('connections',), 'случайные записанные звонки операторов',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1)), 'min_duration': 20,
                        'max_duration': 600, 'conn_types': '(1, 5)', 'test_keys': [],
                        'operator_ids': [_SAMPLE_ID], 'cap': 3, 'phone_digits': ''})
class _EvalSample:
    @staticmethod
    def clean(params):
        cleaned = _eval_common(params)
        cleaned['operator_ids'] = _uuids(params, 'operator_ids')
        cleaned['cap'] = _integer(params, 'cap', 1, MAX_ROWS)
        cleaned['phone_digits'] = _NOT_ASCII_DIGIT.sub('', str(params.get('phone_digits') or ''))[-10:]
        return cleaned

    @staticmethod
    def sql(date_from, date_to, min_duration, max_duration, conn_types, test_keys, operator_ids, cap,
            phone_digits):
        # Точечный подбор по номеру клиента: хвост цифр сравнивается прямо в SQL, иначе
        # случайная выборка могла бы просто не содержать нужный звонок. Только цифры.
        phone_clause = f"AND ({EVAL_PHONE_EXPR}) LIKE '%{phone_digits}' " if phone_digits else ""
        return (
            "SELECT q.auserid, q.conn_id, q.phone, q.dt_raw, q.talk_sec, q.ct, "
            "q.stop_side, q.reason_stop FROM ("
            f"SELECT LOWER(CONVERT(varchar(36), {EVAL_OPERATOR_UID_EXPR})) AS auserid, LOWER(CONVERT(varchar(36), s.Id)) AS conn_id, "
            f"{EVAL_PHONE_EXPR} AS phone, "
            "CONVERT(varchar(10), s.TimeStart, 104) + ' ' + CONVERT(varchar(8), s.TimeStart, 108) AS dt_raw, "
            "DATEDIFF(second, s.TimeAnswer, s.TimeStop) AS talk_sec, s.ConnectionType AS ct, "
            "s.StopSide AS stop_side, s.ReasonStop AS reason_stop, "
            f"ROW_NUMBER() OVER (PARTITION BY {EVAL_OPERATOR_UID_EXPR} ORDER BY NEWID()) AS rn "
            "FROM oktell.dbo.A_Stat_Connections_1x1 s "
            f"WHERE s.TimeStart >= '{date_from}' AND s.TimeStart < '{date_to}' "
            f"AND s.ConnectionType IN {conn_types} AND s.IsRecorded = 1 AND s.TimeStop IS NOT NULL "
            f"AND {EVAL_OPERATOR_UID_EXPR} IN ({_id_literals(operator_ids)}) "
            + _duration_clause(min_duration, max_duration) + phone_clause
            + and_not_test(f"({EVAL_PHONE_EXPR})", test_keys) +
            f") q WHERE q.rn <= {int(cap)} ORDER BY q.auserid"
        )


# 14. Координаты записей разговоров ---------------------------------------------------------------

@_register('record_paths', ('connections',), 'линии и время соединения — путь к записи',
           lambda day: {'conn_ids': [_SAMPLE_ID]})
class _RecordPaths:
    @staticmethod
    def clean(params):
        return {'conn_ids': _uuids(params, 'conn_ids')}

    @staticmethod
    def sql(conn_ids):
        return (
            "SELECT CONVERT(varchar(36), Id) AS conn_id, ALineNum, BLineNum, "
            "CONVERT(varchar(23), TimeStart, 121) AS ts "
            "FROM oktell.dbo.A_Stat_Connections_1x1 "
            f"WHERE Id IN ({_id_literals(conn_ids)})"
        )


# 15. Таксопарк и маршрут звонка — карточка «ИИ-оценки» -------------------------------------------

@_register('call_parks', ('connections', 'calls'), 'таксопарк и номера соединения',
           lambda day: {'conn_ids': [_SAMPLE_ID]})
class _CallParks:
    @staticmethod
    def clean(params):
        return {'conn_ids': _uuids(params, 'conn_ids')}

    @staticmethod
    def sql(conn_ids):
        # Общий JOIN читает историю один раз на пакет. Коррелированный TOP 1 через OUTER APPLY
        # сканировал её заново для каждого звонка: 50 строк занимали больше 30 секунд.
        return (
            "SELECT conn_id, taxi_park, ct, a_out, b_out, a_dialed FROM ("
            "SELECT LOWER(CONVERT(varchar(36), s.Id)) AS conn_id, "
            "NULLIF(LTRIM(RTRIM(t.taxi_park)), '') AS taxi_park, "
            "s.ConnectionType AS ct, s.AOutNumber AS a_out, s.BOutNumber AS b_out, "
            "s.ANumberDialed AS a_dialed, "
            "ROW_NUMBER() OVER (PARTITION BY s.Id ORDER BY t.dt_insert DESC, t.Id DESC) AS rn "
            "FROM oktell.dbo.A_Stat_Connections_1x1 s "
            "LEFT JOIN oktell.dbo.Call_Systems_hst t "
            "ON t.chainid = CONVERT(varchar(36), s.IdChain) "
            "AND NULLIF(LTRIM(RTRIM(t.taxi_park)), '') IS NOT NULL "
            f"WHERE s.Id IN ({_id_literals(conn_ids)})) q WHERE rn = 1"
        )


# 16–17. Ограничитель «Перезвона»: история статусов по логинам ------------------------------------

GUARD_STAMP = '%Y%m%d %H:%M:%S'


@_register('guard_history', ('state_history', 'users'), 'история статусов всех операторов, страница',
           lambda day: {'since': day.strftime('%Y%m%d 09:00:00'), 'until': day.strftime('%Y%m%d 10:00:00'),
                        'cursor': 0, 'limit': 5})
class _GuardHistory:
    @staticmethod
    def clean(params):
        since, until = _stamps(params, GUARD_STAMP)
        return {'since': since, 'until': until,
                'cursor': _integer(params, 'cursor', -1, 2 ** 62, 0),
                'limit': _integer(params, 'limit', 1, MAX_ROWS, MAX_ROWS)}

    @staticmethod
    def sql(since, until, cursor, limit):
        return (
            f"SELECT TOP {int(limit)} u.Login AS login, h.State AS state, h.ICode AS icode, "
            "CONVERT(varchar(19), h.TimeChange, 120) AS time_change, "
            "h.Enumerator AS enumerator "
            "FROM oktell.dbo.A_UserStateHistory h "
            "JOIN oktell_settings.dbo.A_Users u ON u.ID = h.UserId "
            f"WHERE h.TimeChange >= '{since}' "
            f"AND h.TimeChange < '{until}' "
            f"AND h.Enumerator > {int(cursor)} "
            "ORDER BY h.Enumerator"
        )


@_register('guard_operator_history', ('state_history', 'users'), 'статусы одного оператора вокруг момента',
           lambda day: {'login': '0', 'since': day.strftime('%Y%m%d 09:00:00'),
                        'until': day.strftime('%Y%m%d 09:10:00')})
class _GuardOperatorHistory:
    @staticmethod
    def clean(params):
        since, until = _stamps(params, GUARD_STAMP)
        login = params.get('login')
        login = '' if login is None else str(login)
        if len(login) > MAX_LOGIN or any(ord(char) < 32 for char in login):
            raise ValueError('набор Oktell: параметр login — строка до %d знаков' % MAX_LOGIN)
        return {'login': login, 'since': since, 'until': until}

    @staticmethod
    def sql(login, since, until):
        # Единственная свободная строка каталога: уходит в литерал с удвоением кавычек.
        safe_login = login.replace("'", "''")
        return (
            "SELECT TOP 50 h.State, h.ICode, "
            "CONVERT(varchar(19), h.TimeChange, 120) AS time_change "
            "FROM oktell.dbo.A_UserStateHistory h "
            "JOIN oktell_settings.dbo.A_Users u ON u.ID = h.UserId "
            f"WHERE u.Login = '{safe_login}' "
            f"AND h.TimeChange BETWEEN '{since}' AND '{until}' "
            "ORDER BY h.Enumerator"
        )


# 18–20. Прогноз нагрузки («Расчёт ресурсов») -----------------------------------------------------

@_register('forecast_daily', ('calls',), 'входящие линии по дням',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1)), 'test_keys': []})
class _ForecastDaily:
    @staticmethod
    def clean(params):
        date_from, date_to = _period(params)
        return {'date_from': date_from, 'date_to': date_to, 'test_keys': _test_keys(params)}

    @staticmethod
    def sql(date_from, date_to, test_keys):
        greeting, failed = _GRT, _FAIL
        return (
            "SELECT CONVERT(varchar(10), t.dt_insert, 23) AS report_date, "
            f"SUM(CASE WHEN t.result_call <> N'{greeting}' AND t.call_result IN (13,19,5) THEN 1 ELSE 0 END) AS received, "
            f"SUM(CASE WHEN t.result_call <> N'{greeting}' AND t.call_result = 5 THEN 1 ELSE 0 END) AS accepted "
            "FROM oktell.dbo.Call_Systems_hst t "
            f"WHERE t.dt_insert >= '{date_from}' AND t.dt_insert < '{date_to}' "
            f"AND t.taxi_park <> '' AND t.route = 'incoming' AND t.result_call <> N'{failed}' "
            f"{and_not_test('t.[number]', test_keys)}"
            "GROUP BY CONVERT(varchar(10), t.dt_insert, 23)"
        )


@_register('forecast_params', ('calls', 'connections'), 'разговор и ожидание принятых и потерянных',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1)),
                        'conn_from': _c(day - timedelta(days=1)), 'test_keys': []})
class _ForecastParams:
    @staticmethod
    def clean(params):
        date_from, date_to = _period(params)
        _period(params, 'conn_from', 'date_to')
        return {'date_from': date_from, 'date_to': date_to, 'conn_from': _compact(params, 'conn_from'),
                'test_keys': _test_keys(params)}

    @staticmethod
    def sql(date_from, date_to, conn_from, test_keys):
        """Принятые звонки, разговор оператора по плечам соединения (не total_length — там ещё
        IVR и очередь), ожидание в очереди принятых и потерянных."""
        greeting, failed = _GRT, _FAIL
        return (
            "SELECT "
            f"SUM(CASE WHEN t.result_call <> N'{greeting}' AND t.call_result = 5 THEN 1 ELSE 0 END) AS accepted, "
            f"SUM(CASE WHEN t.result_call <> N'{greeting}' AND t.call_result = 5 THEN COALESCE(k.talk_sec, 0) ELSE 0 END) AS talk_sec, "
            f"SUM(CASE WHEN t.result_call <> N'{greeting}' AND t.call_result = 5 AND k.talk_sec IS NULL THEN 1 ELSE 0 END) AS no_chain, "
            f"SUM(CASE WHEN t.result_call <> N'{greeting}' AND t.call_result = 5 THEN COALESCE(t.LenQueue, 0) ELSE 0 END) AS wait_ok_sec, "
            f"SUM(CASE WHEN t.call_result IN (13,19) AND t.result_call NOT IN (N'{greeting}', N'{failed}') THEN 1 ELSE 0 END) AS lost, "
            f"SUM(CASE WHEN t.call_result IN (13,19) AND t.result_call NOT IN (N'{greeting}', N'{failed}') THEN COALESCE(t.LenQueue, 0) ELSE 0 END) AS wait_lost_sec "
            "FROM oktell.dbo.Call_Systems_hst t "
            "LEFT JOIN (SELECT IdChain, SUM(DATEDIFF(second, TimeAnswer, TimeStop)) AS talk_sec "
            "FROM oktell.dbo.A_Stat_Connections_1x1 "
            f"WHERE ConnectionType = 5 AND TimeStart >= '{conn_from}' AND TimeStart < '{date_to}' "
            "GROUP BY IdChain) k ON k.IdChain = TRY_CAST(t.chainid AS uniqueidentifier) "
            f"WHERE t.dt_insert >= '{date_from}' AND t.dt_insert < '{date_to}' "
            f"AND t.taxi_park <> '' AND t.route = 'incoming' AND t.result_call <> N'{failed}' "
            f"{and_not_test('t.[number]', test_keys)}"
        ).rstrip()


@_register('forecast_after_call', ('operator_states',), 'постобработка и удержание входящих',
           lambda day: {'date_from': _c(day), 'date_to': _c(day + timedelta(days=1))})
class _ForecastAfterCall:
    @staticmethod
    def clean(params):
        date_from, date_to = _period(params)
        return {'date_from': date_from, 'date_to': date_to}

    @staticmethod
    def sql(date_from, date_to):
        return (
            "SELECT SUM(CASE WHEN s.State = 7 AND s.IsOutput = 0 THEN s.LenTime ELSE 0 END) AS post_sec, "
            "SUM(CASE WHEN s.State = 33 AND s.IsOutput = 0 THEN s.LenTime ELSE 0 END) AS hold_sec "
            "FROM oktell_cc_temp.dbo.A_Cube_CC_OperatorStates s "
            f"WHERE s.DateTimeStart >= '{date_from}' AND s.DateTimeStart < '{date_to}'"
        )


# 21. «Реестр тестовых номеров»: звонки с номерами реестра ----------------------------------------

@_register('test_number_calls', ('calls', 'operators', 'connections'), 'звонки с номерами реестра',
           lambda day: {'phone_keys': ['0000000000'], 'date_from': _c(day),
                        'date_to': _c(day + timedelta(days=1)), 'row_cap': 5})
class _TestNumberCalls:
    @staticmethod
    def clean(params):
        date_from, date_to = _period(params)
        phone_keys = _test_keys(params, 'phone_keys')
        if not phone_keys:
            raise ValueError('набор Oktell: в параметре phone_keys нет ни одного номера')
        return {'phone_keys': phone_keys, 'date_from': date_from, 'date_to': date_to,
                'row_cap': _integer(params, 'row_cap', 1, MAX_ROWS)}

    @staticmethod
    def sql(phone_keys, date_from, date_to, row_cap):
        """Звонки с номерами реестра за период ОДНИМ запросом. Номер клиента у входящих и
        исходящих — `[number]`. Наш номер — набранная линия цепочки: `ANumberDialed` у плеча
        «снаружи в IVR» (ConnectionType = 4); у исходящего такого плеча нет — линия пустая."""
        # Условие «НЕ тестовый» перевёрнуто: здесь нужны как раз тестовые.
        match = "%s IN (%s)" % (phone_key('t.[number]'), ', '.join("'%s'" % key for key in phone_keys))
        return (
            f"SELECT TOP {row_cap} t.Id AS call_id, "
            "CONVERT(varchar(19), t.dt_insert, 120) AS occurred_at, t.route AS route, "
            "COALESCE(t.[number], N'') AS phone, t.taxi_park AS taxi_park, "
            "t.result_call AS result_call, t.call_result AS call_result, "
            "t.total_length AS total_length, oi.Name AS operator_name, l.ANumberDialed AS line "
            "FROM oktell.dbo.Call_Systems_hst t "
            "LEFT JOIN oktell_cc_temp.dbo.A_Cube_CC_Cat_OperatorInfo oi "
            "ON oi.Id = TRY_CAST(t.id_operator AS uniqueidentifier) "
            "OUTER APPLY (SELECT TOP 1 s.ANumberDialed FROM oktell.dbo.A_Stat_Connections_1x1 s "
            "WHERE s.IdChain = TRY_CONVERT(uniqueidentifier, t.chainid) AND s.ConnectionType = 4 "
            "ORDER BY s.TimeStart) l "
            f"WHERE t.dt_insert >= '{date_from}' AND t.dt_insert < '{date_to}' "
            f"AND {match} "
            # Упрёмся в потолок — пусть пропадут старые, а не сегодняшние: их смотрят чаще.
            "ORDER BY t.dt_insert DESC"
        )


# ── вход для портала и коннектора ────────────────────────────────────────────

NAMES = tuple(sorted(DATASETS))


def build(name, params):
    """Имя набора и параметры → `Query`. `ValueError` — набора нет или параметры не годятся.

    Зовут обе стороны: портал — чтобы получить текст (и отправить коннектору набор, параметры
    и отпечаток), коннектор — чтобы построить текст самому и сверить отпечаток."""
    dataset = DATASETS.get(name) if isinstance(name, str) else None
    if dataset is None:
        raise ValueError('набор Oktell: неизвестный набор %r' % (name,))
    if not isinstance(params, dict):
        raise ValueError('набор Oktell: параметры — объект')
    cleaned = dataset.clean(params)
    return Query(dataset.sql(**cleaned), name, cleaned)


def tables_of(name, params=None):
    """Ключи таблиц (`TABLES`), которые читает набор. С параметрами — только те, что нужны
    запросу именно с ними; без — все, какие набор вообще может читать."""
    dataset = DATASETS[name]
    if params is not None and dataset.tables_for is not None:
        return tuple(dataset.tables_for(params))
    return dataset.tables


def samples(day=None):
    """{набор: параметры узкого окна} — проверка установки коннектора (`--check-datasets`)."""
    day = day or date.today()
    return {name: DATASETS[name].sample(day) for name in NAMES}


_FINGERPRINT_DAY = date(2026, 1, 15)


def fingerprint():
    """Отпечаток каталога: меняется, когда меняется текст любого набора. По нему портал и
    коннектор видят, что собраны из разных версий, а тест напоминает обновить VM шлюза."""
    hasher = hashlib.sha256()
    for name, params in sorted(samples(_FINGERPRINT_DAY).items()):
        hasher.update(name.encode('utf-8'))
        hasher.update(b'\0')
        hasher.update(build(name, params).encode('utf-8'))
        hasher.update(b'\0')
    return hasher.hexdigest()[:16]


__all__ = ['BILLING_GROUPS', 'BILLING_TALK_EXPR', 'BREAK_STAMP', 'DATASETS', 'EVAL_CONNECTION_TYPES',
           'EVAL_OPERATOR_UID_EXPR', 'EVAL_PHONE_EXPR', 'GUARD_STAMP', 'MAX_ROWS', 'MAX_TEST_KEYS',
           'NAMES', 'Query', 'TABLES', 'build', 'digest', 'fingerprint', 'samples', 'table_probe_sql',
           'tables_of']
