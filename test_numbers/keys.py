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


def sql_not_test_any(*exprs):
    """Ни одно из полей строки не тестовое — для источников, где номер клиента
    лежит в одном из двух полей (Chat2Desk: client_phone или assigned_phone)."""
    keys = ', '.join(sql_key(expr) for expr in exprs)
    return f"NOT EXISTS (SELECT 1 FROM {TABLE} _tpn WHERE _tpn.phone_key IN ({keys}))"


def wazzup_phone_sql(alias):
    """Номер клиента в строках Wazzup (сообщение, чат, эпизод): contact_phone, а у
    WhatsApp, где его нет, — сам chat_id (это и есть номер). У Telegram и прочих
    без номера — NULL: такая строка тестовой не станет."""
    return (f"COALESCE(NULLIF({alias}.contact_phone, ''), "
            f"CASE WHEN {alias}.chat_type IN ('whatsapp', 'wapi') THEN {alias}.chat_id END)")


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
