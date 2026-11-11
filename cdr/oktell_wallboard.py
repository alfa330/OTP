# -*- coding: utf-8 -*-
"""Снимок «Табло СЗоВ · Линия» из базы Oktell: текст запроса и его параметры.

Один модуль на две стороны — как `cdr/pbx_calls.py`:

  * портал (`bot_schedule2._oktell_wallboard_*_sql`) спрашивает базу через мост Oktell
    (HTTP `/query`), как делал всегда;
  * коннектор Oktell на VM шлюза (`cdr_bridge/oktell_connector.py`) читает базу сам, изнутри
    корпоративной сети, и присылает порталу готовые строки (`oktell_live/`).

Запрос у обоих путей обязан быть один: табло не может показывать разные цифры в зависимости
от того, какой дорогой пришли строки. Поэтому текст живёт здесь, а не в двух копиях, и
`tests/test_oktell_wallboard_sql.py` сверяет оба построителя буква в букву.

Что считает запрос (сверено на живой базе 03.08.2026, задача #108)
-------------------------------------------------------------------
Итоги дня — из `Call_Systems_hst` (строка появляется через ~6 с после конца звонка), «сейчас»
— из незакрытых легов `A_Stat_Connections_1x1`: ждут в очереди цепочки с открытым легом
`ct=4/IVR`, у которых не было ни одного лега на оператора (`ct=5`), говорят — открытые `ct=5`.
Статус оператора — последняя запись `A_UserStateHistory` на пользователя. Всё одним SELECT:
итоги повторяются в каждой строке статусов, а если на линии никого — приезжает одна строка
с пустой статусной частью (LEFT JOIN).

Параметры
---------
Их два, и коннектору они приходят от портала числом и списком цифр, а не текстом SQL: порог
SL в секундах и ключи «Реестра тестовых номеров» (последние десять цифр номера,
`test_numbers/keys.py`). Коннектор исполняет только то, что построил сам, — `clean_params`
отсекает всё, что на число и на десять ASCII-цифр не похоже, так что подставить в запрос
нечего, кроме цифр.

Ни сети, ни базы, ни зависимостей за пределами стандартной библиотеки: модуль едет в образ
шлюза вместе с `cdr/`.
"""

# Результаты звонка в Call_Systems_hst.result_call. Те же строки стоят в монолите
# (`_OKTELL_GREETING_ABANDON`, `_OKTELL_FAILED_CALL`) — их читает ещё биллинг; тест сверяет.
GREETING_ABANDON = 'Бросили трубку на приветствии'
FAILED_CALL = 'Неудачный звонок'

# Окна поиска незакрытых легов: ожидание дольше 3 ч и разговор дольше 6 ч — зависшие в базе
# леги, а не живые звонки. Границы держат запрос на кластерном индексе по TimeStart.
QUEUE_LOOKBACK_HOURS = 3
TALK_LOOKBACK_HOURS = 6

# Порог SL по умолчанию (секунд ожидания в очереди) и его разумные границы.
DEFAULT_SL_SECONDS = 20
MIN_SL_SECONDS = 1
MAX_SL_SECONDS = 600

# Ключ тестового номера — ровно десять ASCII-цифр (test_numbers.keys.KEY_LENGTH).
KEY_LENGTH = 10
# Реестр — десятки номеров. Потолок — защита от тела, которое кто-то раздул ошибкой или нарочно.
MAX_TEST_KEYS = 2000

# Колонки одной строки снимка: итоги дня и «сейчас» (одинаковы во всех строках) и статус
# одного оператора. Портал принимает от коннектора только их.
TOTALS_COLUMNS = ('oktell_now', 'queue_now', 'queue_max_wait_seconds', 'talking_now',
                  'arrived', 'served', 'lost', 'greet_drop', 'served_sl',
                  'wait_seconds', 'wait_served_seconds', 'max_wait_seconds', 'talk_seconds')
STATE_COLUMNS = ('operator_name', 'state', 'icode', 'since', 'in_state_seconds')
SNAPSHOT_COLUMNS = TOTALS_COLUMNS + STATE_COLUMNS

# Где в запросе лежит номер клиента — по этим выражениям исключаются тестовые номера.
WAITING_PHONE = 'a.AOutNumber'
TALKING_PHONE = 'c.AOutNumber'
TOTALS_PHONE = 'x.[number]'


# ── параметры ────────────────────────────────────────────────────────────────

def clean_sl_seconds(value):
    """Порог SL целым числом в разумных границах; мусор — порог по умолчанию."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SL_SECONDS
    if isinstance(value, bool) or not MIN_SL_SECONDS <= seconds <= MAX_SL_SECONDS:
        return DEFAULT_SL_SECONDS
    return seconds


def clean_test_keys(values):
    """Ключи реестра: только строки из десяти ASCII-цифр, без повторов, по порядку.

    Всё остальное молча отбрасывается — в текст запроса попадают одни цифры. Список длиннее
    потолка обрезается: лучше на цикл посчитать лишний тест, чем собрать запрос на мегабайт."""
    if isinstance(values, (str, bytes)) or values is None:
        return ()
    try:
        items = list(values)
    except TypeError:
        return ()
    keys = sorted({item for item in items
                   if isinstance(item, str) and len(item) == KEY_LENGTH
                   and item.isascii() and item.isdigit()})
    return tuple(keys[:MAX_TEST_KEYS])


def clean_params(raw):
    """Параметры снимка в каноническом виде: {'sl_seconds': int, 'test_keys': [..]}."""
    raw = raw if isinstance(raw, dict) else {}
    return {'sl_seconds': clean_sl_seconds(raw.get('sl_seconds')),
            'test_keys': list(clean_test_keys(raw.get('test_keys')))}


def same_params(left, right):
    """Сняты ли два снимка с одними и теми же параметрами."""
    return clean_params(left) == clean_params(right)


# ── исключение тестовых номеров ──────────────────────────────────────────────
# Та же форма, что у test_numbers.keys.tsql_key / tsql_not_test (пакета портала в образе
# шлюза нет); tests/test_oktell_wallboard_sql.py сверяет обе.

def phone_key(expr):
    return ("RIGHT(REPLACE(REPLACE(REPLACE(ISNULL(%s, ''), '+', ''), ' ', ''), '-', ''), %d)"
            % (expr, KEY_LENGTH))


def and_not_test(expr, keys):
    """«AND номер не из реестра » для WHERE; реестр пуст — пустая строка.

    Хвостовой пробел — чтобы склеиваться со следующей частью запроса."""
    literals = clean_test_keys(keys)
    if not literals:
        return ''
    values = ', '.join("'%s'" % key for key in literals)
    return 'AND %s NOT IN (%s) ' % (phone_key(expr), values)


# ── текст запроса ────────────────────────────────────────────────────────────

def totals_sql(sl_seconds, *, waiting_not_test='', talking_not_test='', totals_not_test='',
               greeting=GREETING_ABANDON, failed=FAILED_CALL,
               queue_hours=QUEUE_LOOKBACK_HOURS, talk_hours=TALK_LOOKBACK_HOURS):
    """Итоги за текущий день Oktell + очередь и разговоры «на сейчас». Ровно одна строка.

    Фрагмент: подставляется подзапросом в `join_sql`. «Сегодня» берётся по часам самого
    Oktell (`CONVERT(date, GETDATE())`), чтобы граница суток совпадала с источником.

    `*_not_test` — готовые условия «AND … NOT IN (…) » от доверенного построителя: портал
    берёт их у `test_numbers.keys.tsql_and_not_test`, коннектор — у `and_not_test` выше.
    Чужой текст сюда не приходит: коннектор зовёт только `snapshot_sql`."""
    sl_seconds = int(sl_seconds)
    grt = str(greeting).replace("'", "''")
    fail = str(failed).replace("'", "''")
    # Цепочки, которые прямо сейчас ждут ответа: открытый лег в IVR/очереди и ни одного лега
    # на оператора за всю историю цепочки (иначе это пост-обработка после разговора).
    waiting_chains = (
        "SELECT a.IdChain, MIN(a.TimeStart) AS started "
        "FROM oktell.dbo.A_Stat_Connections_1x1 a "
        "WHERE a.ConnectionType = 4 AND a.BLineNum = N'IVR' AND a.TimeStop IS NULL "
        f"AND a.TimeStart >= DATEADD(hour, -{int(queue_hours)}, GETDATE()) "
        f"{waiting_not_test}"
        "AND NOT EXISTS (SELECT 1 FROM oktell.dbo.A_Stat_Connections_1x1 d "
        "WHERE d.IdChain = a.IdChain AND d.ConnectionType = 5) "
        "GROUP BY a.IdChain"
    )
    return (
        "SELECT CONVERT(varchar(19), GETDATE(), 120) AS oktell_now, "
        "q.queue_now, q.queue_max_wait_seconds, k.talking_now, "
        "t.arrived, t.served, t.lost, t.greet_drop, t.served_sl, "
        "t.wait_seconds, t.wait_served_seconds, t.max_wait_seconds, t.talk_seconds "
        "FROM ("
        "SELECT COUNT(*) AS queue_now, "
        "ISNULL(MAX(DATEDIFF(second, w.started, GETDATE())), 0) AS queue_max_wait_seconds "
        f"FROM ({waiting_chains}) w"
        ") q CROSS JOIN ("
        "SELECT COUNT(DISTINCT c.IdChain) AS talking_now "
        "FROM oktell.dbo.A_Stat_Connections_1x1 c "
        "WHERE c.ConnectionType = 5 AND c.TimeStop IS NULL "
        f"AND c.TimeStart >= DATEADD(hour, -{int(talk_hours)}, GETDATE()) "
        f"{talking_not_test}"
        ") k CROSS JOIN ("
        "SELECT "
        f"ISNULL(SUM(CASE WHEN x.result_call <> N'{grt}' AND x.call_result IN (13,19,5) THEN 1 ELSE 0 END), 0) AS arrived, "
        f"ISNULL(SUM(CASE WHEN x.result_call <> N'{grt}' AND x.call_result IN (5) THEN 1 ELSE 0 END), 0) AS served, "
        f"ISNULL(SUM(CASE WHEN x.result_call <> N'{grt}' AND x.call_result IN (13,19) THEN 1 ELSE 0 END), 0) AS lost, "
        f"ISNULL(SUM(CASE WHEN x.result_call = N'{grt}' THEN 1 ELSE 0 END), 0) AS greet_drop, "
        f"ISNULL(SUM(CASE WHEN x.result_call <> N'{grt}' AND x.call_result IN (5) AND x.LenQueue <= {sl_seconds} THEN 1 ELSE 0 END), 0) AS served_sl, "
        f"ISNULL(SUM(CASE WHEN x.result_call <> N'{grt}' AND x.call_result IN (13,19,5) THEN x.LenQueue ELSE 0 END), 0) AS wait_seconds, "
        # Числитель ASA: ожидание в очереди только у принятых — потерянные в него не входят.
        f"ISNULL(SUM(CASE WHEN x.result_call <> N'{grt}' AND x.call_result IN (5) THEN x.LenQueue ELSE 0 END), 0) AS wait_served_seconds, "
        f"MAX(CASE WHEN x.result_call <> N'{grt}' AND x.call_result IN (13,19,5) THEN x.LenQueue END) AS max_wait_seconds, "
        f"ISNULL(SUM(CASE WHEN x.result_call <> N'{grt}' AND x.call_result IN (5) THEN x.total_length ELSE 0 END), 0) AS talk_seconds "
        "FROM oktell.dbo.Call_Systems_hst x "
        "WHERE x.route = 'incoming' AND x.taxi_park <> '' "
        f"AND x.result_call <> N'{fail}' "
        f"{totals_not_test}"
        "AND x.dt_insert >= CONVERT(date, GETDATE()) "
        "AND x.dt_insert < DATEADD(day, 1, CONVERT(date, GETDATE()))"
        ") t"
    )


def operator_states_sql():
    """Текущий статус каждого оператора: последняя запись A_UserStateHistory на пользователя.

    Фрагмент без ORDER BY: подставляется подзапросом в `join_sql`, а производной таблице
    сортировка запрещена — она навешивается снаружи.

    State 0/7 (Выключен/Без телефона) отбрасываются — это «не на линии». Окно в 2 суток нужно,
    чтобы поймать смену, начавшуюся вчера; Oktell пишет явный переход в 0 при отключении,
    поэтому «залипших» готовых операторов из прошлых дней здесь не будет."""
    return (
        "SELECT oi.Name AS operator_name, x.State AS state, x.ICode AS icode, "
        "CONVERT(varchar(19), x.TimeChange, 120) AS since, "
        "DATEDIFF(second, x.TimeChange, GETDATE()) AS in_state_seconds "
        "FROM (SELECT h.UserId, h.State, h.ICode, h.TimeChange, "
        "ROW_NUMBER() OVER (PARTITION BY h.UserId ORDER BY h.Enumerator DESC) AS rn "
        "FROM oktell.dbo.A_UserStateHistory h "
        "WHERE h.TimeChange >= DATEADD(day, -2, GETDATE())) x "
        "JOIN oktell_cc_temp.dbo.A_Cube_CC_Cat_OperatorInfo oi ON oi.Id = x.UserId "
        "WHERE x.rn = 1 AND x.State NOT IN (0, 7)"
    )


def join_sql(totals, states):
    """Весь снимок ОДНИМ SELECT: итоги дня, очередь «на сейчас» и статусы операторов.

    LEFT JOIN, а не CROSS JOIN: если на линии нет ни одного оператора (ночь, все выключены),
    итоги дня всё равно должны приехать — вернётся одна строка со статусными колонками NULL."""
    return (
        "SELECT t.oktell_now, t.queue_now, t.queue_max_wait_seconds, t.talking_now, "
        "t.arrived, t.served, t.lost, t.greet_drop, t.served_sl, "
        "t.wait_seconds, t.wait_served_seconds, t.max_wait_seconds, t.talk_seconds, "
        "s.operator_name, s.state, s.icode, s.since, s.in_state_seconds "
        f"FROM ({totals}) t "
        f"LEFT JOIN ({states}) s ON 1 = 1 "
        "ORDER BY s.operator_name"
    )


def snapshot_sql(sl_seconds, test_keys=()):
    """Запрос снимка по числу и списку цифр — то, что исполняет коннектор на VM шлюза.

    Параметры чистятся здесь же: что бы ни прислал портал, в текст попадут порог SL целым
    числом в границах и ключи из десяти цифр."""
    keys = clean_test_keys(test_keys)
    return join_sql(
        totals_sql(clean_sl_seconds(sl_seconds),
                   waiting_not_test=and_not_test(WAITING_PHONE, keys),
                   talking_not_test=and_not_test(TALKING_PHONE, keys),
                   totals_not_test=and_not_test(TOTALS_PHONE, keys)),
        operator_states_sql())
