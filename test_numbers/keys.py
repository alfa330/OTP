"""Ключ номера реестра и способы исключить тестовые номера из расчётов.

Номер клиента в источниках лежит по-разному: касания FreePBX — десять цифр
(cdr/touches.py: norm_phone), пул оценок — «7» и десять цифр, Binotel —
'77011234567', Wazzup — номер в chat_id, Chat2Desk — '+7 701 …' рядом с
'[wa_…] KZ.…'. Общий знаменатель — ПОСЛЕДНИЕ ДЕСЯТЬ ЦИФР: у казахстанского
номера это код оператора и абонент (8 701…, +7 701…, 7701… — один человек),
у чужого — хвост номера. Меньше десяти цифр — внутренний номер или мусор:
ключа нет, и такая строка тестовой не станет никогда.

Здесь три формы одного правила, и они обязаны совпадать:
    phone_key(raw)             Python — реестр, живые источники (Oktell, Binotel)
    sql_key(expr)              SQL-выражение — исключение в запросах к нашей базе
    tsql_key(expr)             T-SQL — запросы к базе Oktell

Цифры — только ASCII: `\\d` в Python понимает и арабские, и деванагари, и такой
«номер» дал бы ключ, которого SQL никогда не повторит.
"""

import logging
import re
import threading
import time

KEY_LENGTH = 10

TABLE = 'test_phone_numbers'

_NON_DIGITS = re.compile(r'[^0-9]')


def phone_key(raw):
    """Последние десять цифр номера или None, если цифр меньше десяти."""
    if raw is None:
        return None
    digits = _NON_DIGITS.sub('', str(raw))
    if len(digits) < KEY_LENGTH:
        return None
    return digits[-KEY_LENGTH:]


def is_test_phone(raw, keys):
    """Тестовый ли номер — по уже загруженному набору ключей."""
    if not keys:
        return False
    key = phone_key(raw)
    return key is not None and key in keys


# ─────────────────────────────────────────────────────────────────────────────
# SQL (Postgres, наша база)
# ─────────────────────────────────────────────────────────────────────────────
#
# Исключение — NOT EXISTS, а не NOT IN: у NOT IN строка без номера (NULL)
# выпала бы из расчёта вместе с тестовыми, а таких строк много — Telegram и
# 2ГИС приходят без номера вовсе. Ключ строки короче десяти цифр не совпадёт
# ни с одним ключом реестра (там ровно десять), поэтому отдельная проверка
# длины в SQL не нужна. Реестр — десятки строк: планировщик берёт его хешем,
# и запрос, который и так читает свои строки, дороже почти не становится.

def sql_key(expr):
    """Ключ строки в SQL — то же правило, что phone_key()."""
    return (
        f"RIGHT(REGEXP_REPLACE(COALESCE(({expr})::text, ''), '[^0-9]', '', 'g'), {KEY_LENGTH})"
    )


def sql_digits_key(expr):
    """Ключ колонки, где уже только цифры (касания FreePBX, пул оценок):
    без регулярного выражения на каждую строку."""
    return f"RIGHT(COALESCE(({expr})::text, ''), {KEY_LENGTH})"


def sql_is_test(expr, *, digits=False):
    key = sql_digits_key(expr) if digits else sql_key(expr)
    return f"EXISTS (SELECT 1 FROM {TABLE} _tpn WHERE _tpn.phone_key = {key})"


def sql_not_test(expr, *, digits=False):
    """Условие «номер строки не тестовый» для WHERE/ON/FILTER."""
    return 'NOT ' + sql_is_test(expr, digits=digits)


def sql_not_test_any(*exprs, digits=False):
    """Ни одно из полей строки не тестовое — для источников, где номер клиента
    лежит в одном из двух полей (Chat2Desk: client_phone или assigned_phone).

    digits=True — поля уже из одних цифр (у Chat2Desk номер клиента — одиннадцать
    цифр; идентификатор WhatsApp «[wa_…] KZ.…» своим хвостом ни с одним ключом не
    совпадёт, и номер такого клиента лежит во втором поле).
    """
    make = sql_digits_key if digits else sql_key
    keys = ', '.join(make(expr) for expr in exprs)
    return f"NOT EXISTS (SELECT 1 FROM {TABLE} _tpn WHERE _tpn.phone_key IN ({keys}))"


def sql_not_test_keys(*key_exprs):
    """Ни один из готовых ключей строки (sql_key / sql_digits_key) не тестовый —
    когда у полей разный вид: свободный текст рядом с цифрами."""
    return f"NOT EXISTS (SELECT 1 FROM {TABLE} _tpn WHERE _tpn.phone_key IN ({', '.join(key_exprs)}))"


def sql_is_test_any(*exprs, digits=False):
    """Хоть одно из полей строки — номер реестра (зеркало sql_not_test_any)."""
    make = sql_digits_key if digits else sql_key
    keys = ', '.join(make(expr) for expr in exprs)
    return f"EXISTS (SELECT 1 FROM {TABLE} _tpn WHERE _tpn.phone_key IN ({keys}))"


def wazzup_phone_sql(alias=None):
    """Номер клиента в строках Wazzup (сообщение, чат, эпизод): contact_phone, а у
    WhatsApp, где его нет, — сам chat_id (это и есть номер). У Telegram и прочих
    без номера — NULL: такая строка тестовой не станет.

    alias=None — колонки без алиаса: для запроса, где одно и то же условие стоит и
    под алиасом, и без него (у реестра таких колонок нет, имена уходят наружу)."""
    p = f'{alias}.' if alias else ''
    return (f"COALESCE(NULLIF({p}contact_phone, ''), "
            f"CASE WHEN {p}chat_type IN ('whatsapp', 'wapi') THEN {p}chat_id END)")


# Обращения Chat2Desk с номером реестра — по ленте вебхуков. Номер приходит не в
# каждом событии (у new_request и close_request карточки клиента нет), поэтому
# тестовым считается всё обращение, если хоть одно его событие несёт такой номер.
# Соединение — по хвосту из девяти цифр ДОСЛОВНО тем же выражением, что у индекса
# idx_c2d_webhook_events_phone_tail (иначе планировщик уйдёт в полный проход), а
# точное совпадение — по десяти.
C2D_WEBHOOK_TEST_REQUESTS_SQL = (
    "SELECT _tw.request_id FROM " + TABLE + " _tpn "
    "JOIN c2d_webhook_events _tw "
    "ON right(regexp_replace(_tw.client_phone, '\\D', '', 'g'), 9) = right(_tpn.phone_key, 9) "
    "AND _tw.client_id IS NOT NULL "
    "WHERE _tw.request_id IS NOT NULL "
    "AND right(regexp_replace(_tw.client_phone, '\\D', '', 'g'), 10) = _tpn.phone_key"
)


def all_test(phones, keys):
    """У строки есть номера, и ВСЕ они тестовые.

    Так решается про сделку и лида с несколькими номерами («Воронка ОП», лиды
    Тез, базы «Обзвона»): выпадает только сделка тестировщика. Настоящий клиент,
    к сделке которого кто-то дописал свой номер, остаётся в расчёте.
    """
    if not keys:
        return False
    found = [key for key in (phone_key(phone) for phone in (phones or ())) if key]
    return bool(found) and all(key in keys for key in found)


def drop_test(rows, phone_of, keys):
    """Строки без тестовых номеров — для живых источников (Binotel, Oktell в Python).

    phone_of(row) → сырой номер строки. Пустой набор ключей — список как есть,
    без прохода по строкам.
    """
    if not keys:
        return list(rows)
    return [row for row in rows if not is_test_phone(phone_of(row), keys)]


# ─────────────────────────────────────────────────────────────────────────────
# T-SQL (база Oktell, читается через прокси)
# ─────────────────────────────────────────────────────────────────────────────
#
# Таблицы реестра в Oktell нет, поэтому ключи едут литералами. Они — ровно
# десять ASCII-цифр (так их пишет phone_key и проверяет CHECK таблицы), то есть
# подставить их в текст запроса безопасно; всё, что на это не похоже, сюда не
# попадает вовсе.

def tsql_key(expr):
    return f"RIGHT(REPLACE(REPLACE(REPLACE(ISNULL({expr}, ''), '+', ''), ' ', ''), '-', ''), {KEY_LENGTH})"


def tsql_not_test(expr, keys):
    """Условие для WHERE запроса к Oktell; пустой набор — пустая строка."""
    literals = sorted(k for k in (keys or ()) if isinstance(k, str)
                      and len(k) == KEY_LENGTH and k.isascii() and k.isdigit())
    if not literals:
        return ''
    values = ', '.join(f"'{k}'" for k in literals)
    return f"{tsql_key(expr)} NOT IN ({values})"


# ─────────────────────────────────────────────────────────────────────────────
# Набор ключей для живых источников
# ─────────────────────────────────────────────────────────────────────────────
#
# Табло и отбивки читают Oktell и Binotel вживую и фильтруют строки в Python.
# Ходить за реестром на каждый опрос табло незачем: набор держится в памяти
# полминуты и сбрасывается сразу, когда раздел добавил или убрал номер. База
# недоступна — остаётся последний известный набор: лучше на минуту посчитать
# тест, чем уронить табло.

CACHE_SECONDS = 30.0

_cache_lock = threading.Lock()
_cache = {'keys': frozenset(), 'at': None}


def load_keys(cursor):
    """Все ключи реестра одним запросом."""
    cursor.execute(f"SELECT phone_key FROM {TABLE}")
    return frozenset(row[0] for row in cursor.fetchall() if row and row[0])


def cached_keys(get_cursor, *, clock=time.monotonic):
    """Ключи реестра с кешем. `get_cursor` — db._get_cursor (контекстный менеджер)."""
    now = clock()
    with _cache_lock:
        at = _cache['at']
        if at is not None and now - at < CACHE_SECONDS:
            return _cache['keys']
    try:
        with get_cursor() as cursor:
            keys = load_keys(cursor)
    except Exception:  # noqa: BLE001 — табло не должно падать из-за реестра
        logging.exception('Реестр тестовых номеров: ключи не прочитаны, остаётся прежний набор')
        with _cache_lock:
            # Повторим не раньше, чем через тот же срок: иначе каждый опрос
            # табло бился бы в лежащую базу.
            _cache['at'] = now
            return _cache['keys']
    with _cache_lock:
        _cache['keys'] = keys
        _cache['at'] = now
    return keys


def invalidate_cache():
    """Сбросить кеш — после правки реестра."""
    with _cache_lock:
        _cache['at'] = None


# ─────────────────────────────────────────────────────────────────────────────
# Откуда живые источники берут ключи
# ─────────────────────────────────────────────────────────────────────────────
#
# Приложение один раз при старте отдаёт сюда свой курсор (bot_schedule2:
# configure(db._get_cursor)), и функции монолита берут ключи вызовом
# current_keys() через локальный импорт пакета — без помощников уровня модуля.
# Это важно: тесты вырезают функции монолита через AST и исполняют их в своём
# пространстве имён. Не настроено (так в тестах и скриптах) — реестр пуст, и
# функция ведёт себя ровно как до появления реестра.

_provider = {'get_cursor': None}


def configure(get_cursor):
    _provider['get_cursor'] = get_cursor
    invalidate_cache()


def current_keys():
    get_cursor = _provider['get_cursor']
    if get_cursor is None:
        return frozenset()
    return cached_keys(get_cursor)


def tsql_and_not_test(expr):
    """«AND номер не из реестра » для WHERE запроса к Oktell; реестр пуст — пусто.

    Хвостовой пробел — чтобы склеиваться со следующей частью запроса."""
    condition = tsql_not_test(expr, current_keys())
    return f"AND {condition} " if condition else ""


def drop_test_calls(calls):
    """Звонки журнала Binotel без номеров реестра (номер клиента — external_number)."""
    if calls is None:
        return None
    return drop_test(calls, lambda call: (call or {}).get('external_number'), current_keys())


# ── Chat2Desk: строки отчётов request_stats / rating ─────────────────────────
#
# Номер клиента — в `phone`, а у клиента WhatsApp, пришедшего идентификатором
# «[wa_…] KZ.…», — в `assigned_phone` (request_stats) или `client_phone`.

def c2d_row_is_test(row, keys):
    if not keys or not isinstance(row, dict):
        return False
    return any(is_test_phone(row.get(field), keys) for field in ('phone', 'assigned_phone', 'client_phone'))


def c2d_request_id(row):
    try:
        return int((row or {}).get('request_id'))
    except (TypeError, ValueError, AttributeError):
        return None


def c2d_test_request_ids(keys, *row_sets):
    """Заявки request_stats с номерами реестра — по ним опознаются оценки, в строке
    которых номера нет (у клиента WhatsApp с идентификатором)."""
    out = set()
    if not keys:
        return out
    for rows in row_sets:
        for row in rows or []:
            if c2d_row_is_test(row, keys):
                request_id = c2d_request_id(row)
                if request_id:
                    out.add(request_id)
    return out
