# -*- coding: utf-8 -*-
"""Миграция схемы на старте пода — без блокировок таблиц, когда менять нечего.

26.11.2026, алерт PostgresDeadlocks по базе otp. _init_db идёт одной транзакцией на каждом
старте каждого пода (api ×2, realtime ×2, worker — и на каждом их перезапуске), и даже на
готовой схеме держала до коммита AccessExclusiveLock на 182 таблицах и ShareLock ещё на 247
(замер второго прогона на развёрнутой схеме). Postgres берёт блокировку таблицы ДО проверки
IF [NOT] EXISTS: «ALTER TABLE users ADD COLUMN IF NOT EXISTS city …» при уже существующей
колонке ничего не меняет, но держит users эксклюзивно до конца миграции. Проверка входа на
каждом запросе берёт user_sessions, потом users, а миграция — users, а через ~2000 строк
user_sessions: 391 из ~400 дедлоков за 25–26.11 (жертвы — запросы людей, 500), ~6 с паузы всех
запросов на каждый старт и поды, падавшие по кругу (55P03/40P01 шесть раз подряд, 25.11 19:04).

Курсор миграции (GuardedSchemaCursor) переписывает операторы, которые на готовой схеме —
пустая операция, в DO-блок: сначала каталог (таблица не блокируется), сам оператор — только
если есть что менять. Итог тот же, что у Postgres с IF [NOT] EXISTS:
    ALTER TABLE t ADD [COLUMN] IF NOT EXISTS c …   — пусто, если колонка есть;
    ALTER TABLE t DROP [COLUMN] IF EXISTS c        — пусто, если колонки нет;
    ALTER TABLE t DROP CONSTRAINT IF EXISTS k      — пусто, если ограничения нет;
    ALTER TABLE t ALTER [COLUMN] c DROP NOT NULL   — пусто, если колонка уже допускает NULL;
    ALTER TABLE t ALTER [COLUMN] c SET NOT NULL    — пусто, если NOT NULL уже есть и проверен;
    ALTER TABLE t ALTER [COLUMN] c DROP DEFAULT    — пусто, если умолчания нет;
    CREATE [UNIQUE] INDEX IF NOT EXISTS i ON t …   — пусто, если имя i в схеме таблицы занято.
ALTER из нескольких таких действий пуст, только если пусты все. Остальное идёт в базу как
написано. Пачка из нескольких операторов режется по «;» верхнего уровня (строки, $$-тела и
комментарии учтены), DO-блок встаёт на место своего оператора — условие проверяется в момент
исполнения, после всех операторов выше в той же пачке. Не уверен в разборе — текст как есть.

DROP+ADD ограничений и DROP+CREATE триггеров на каждом старте — не пустая операция, их
пересобирает replace_constraints / replace_trigger, только когда поменялся их текст в коде
(отпечаток текста — в COMMENT объекта).

Выключатель: OTP_SCHEMA_GUARD=0 — всё как до 26.11.2026: курсор без проверок каталога, ограничения
и триггеры пересобираются на каждом старте.
"""
import hashlib
import os
import re

import psycopg2.extensions
from psycopg2 import sql as pg_sql

GUARD_ENV = 'OTP_SCHEMA_GUARD'
# Счётчик применённых операторов — настройка транзакции: DO-блок прибавляет к ней единицу.
APPLIED_SETTING = 'otp.schema_guard_applied'
FINGERPRINT_PREFIX = 'otp-ddl:'
_NAMEDATALEN = 64

_IDENT_CHAR = re.compile(r'[A-Za-z0-9_$\u0080-\uffff]')
_SPECIAL = re.compile(r"""['"$;]|--|/\*""")
_DOLLAR_TAG = re.compile(r'\$(?:[A-Za-z_\u0080-\uffff][A-Za-z0-9_\u0080-\uffff]*)?\$')
_IDENT = r'(?:"(?:[^"]|"")+"|[A-Za-z_\u0080-\uffff][A-Za-z0-9_$\u0080-\uffff]*)'
_TABLE = r'(?:(?P<schema>' + _IDENT + r')\s*\.\s*)?(?P<rel>' + _IDENT + r')'
_ALTER_TABLE = re.compile(
    r'\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?' + _TABLE
    + r'(?:\s*\*)?\s+(?P<actions>\S.*?)\s*\Z', re.I | re.S)
_CREATE_INDEX = re.compile(
    r'\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(?P<name>' + _IDENT + r')\s+ON\s+(?:ONLY\s+)?'
    + _TABLE + r'(?=\s|\(|\Z)', re.I | re.S)
_DROP_TRIGGER = re.compile(
    r'\s*DROP\s+TRIGGER\s+IF\s+EXISTS\s+(?P<name>' + _IDENT + r')\s+ON\s+' + _TABLE
    + r'(?:\s+(?:CASCADE|RESTRICT))?\s*\Z', re.I | re.S)
_CREATE_TRIGGER = re.compile(
    r'\s*CREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\s+(?P<name>' + _IDENT + r')\s', re.I | re.S)
_ON_TABLE = re.compile(r'\bON\s+' + _TABLE + r'(?=\s|\Z)', re.I | re.S)
_ACTIONS = (
    ('add_column', re.compile(
        r'ADD\s+(?:COLUMN\s+)?IF\s+NOT\s+EXISTS\s+(?P<name>' + _IDENT + r')\s+\S', re.I | re.S)),
    ('drop_column', re.compile(
        r'DROP\s+(?:COLUMN\s+)?IF\s+EXISTS\s+(?P<name>' + _IDENT + r')(?:\s+(?:CASCADE|RESTRICT))?\Z', re.I)),
    ('drop_constraint', re.compile(
        r'DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+(?P<name>' + _IDENT + r')(?:\s+(?:CASCADE|RESTRICT))?\Z', re.I)),
    ('drop_not_null', re.compile(
        r'ALTER\s+(?:COLUMN\s+)?(?P<name>' + _IDENT + r')\s+DROP\s+NOT\s+NULL\Z', re.I)),
    ('set_not_null', re.compile(
        r'ALTER\s+(?:COLUMN\s+)?(?P<name>' + _IDENT + r')\s+SET\s+NOT\s+NULL\Z', re.I)),
    ('drop_default', re.compile(
        r'ALTER\s+(?:COLUMN\s+)?(?P<name>' + _IDENT + r')\s+DROP\s+DEFAULT\Z', re.I)),
)
_COLUMN_EXISTS = (
    'EXISTS (SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid = otp_rel AND attname = {name}'
    ' AND attnum > 0 AND NOT attisdropped{extra})'
)


def _is_ident_char(ch):
    return bool(_IDENT_CHAR.match(ch))


def _quote_end(text, i, quote, backslash=False):
    """Индекс за закрывающей кавычкой строки, открытой в text[i]; None — не закрыта."""
    j, n = i + 1, len(text)
    while j < n:
        ch = text[j]
        if backslash and ch == '\\':
            j += 2
            continue
        if ch == quote:
            if j + 1 < n and text[j + 1] == quote:
                j += 2
                continue
            return j + 1
        j += 1
    return None


def _is_escape_string(text, i):
    # E'…' — обратная косая экранирует; E — отдельный токен, а не хвост идентификатора.
    return i > 0 and text[i - 1] in 'eE' and (i == 1 or not _is_ident_char(text[i - 2]))


def _dollar_end(text, i):
    """Индекс за $тег$-строкой, начатой в text[i]; False — здесь не $-строка; None — не закрыта."""
    tag = _DOLLAR_TAG.match(text, i)
    if tag is None or (i > 0 and _is_ident_char(text[i - 1])):
        return False
    end = text.find(tag.group(0), tag.end())
    return None if end == -1 else end + len(tag.group(0))


def _block_comment_end(text, i):
    depth, j, n = 0, i, len(text)
    while j < n:
        if text.startswith('/*', j):
            depth += 1
            j += 2
        elif text.startswith('*/', j):
            depth -= 1
            j += 2
            if depth == 0:
                return j
        else:
            j += 1
    return None


def split_statements(sql):
    """[(исходный кусок, он же без комментариев)] по «;» верхнего уровня, сама «;» не входит.

    None — разбор не уверен (незакрытая строка, $-тело или комментарий): тогда пачка уходит в
    базу как есть."""
    pieces, code = [], []
    start = pos = 0
    n = len(sql)
    while True:
        match = _SPECIAL.search(sql, pos)
        if match is None:
            code.append(sql[pos:])
            pieces.append((sql[start:], ''.join(code)))
            return pieces
        i = match.start()
        code.append(sql[pos:i])
        token = match.group(0)
        if token == ';':
            pieces.append((sql[start:i], ''.join(code)))
            code = []
            start = pos = i + 1
            continue
        if token == '--':
            end = sql.find('\n', i)
            code.append(' ')
            pos = n if end == -1 else end
            continue
        if token == '/*':
            end = _block_comment_end(sql, i)
            if end is None:
                return None
            code.append(' ')
            pos = end
            continue
        if token == '$':
            end = _dollar_end(sql, i)
            if end is False:
                code.append('$')
                pos = i + 1
                continue
        else:
            end = _quote_end(sql, i, token, backslash=token == "'" and _is_escape_string(sql, i))
        if end is None:
            return None
        code.append(sql[i:end])
        pos = end


def _split_actions(text):
    """Действия ALTER TABLE по запятым верхнего уровня (скобки и строки учтены); None — не уверен."""
    parts, depth, start, i, n = [], 0, 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch in '\'"':
            end = _quote_end(text, i, ch, backslash=ch == "'" and _is_escape_string(text, i))
            if end is None:
                return None
            i = end
            continue
        if ch == '$':
            end = _dollar_end(text, i)
            if end is None:
                return None
            if end is not False:
                i = end
                continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                return None
        elif ch == ',' and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
        i += 1
    if depth:
        return None
    parts.append(text[start:].strip())
    return parts


def _name(ident):
    """Имя объекта, как его сохранит Postgres: без кавычек — ASCII в нижний регистр; длина ≤ 63 байт."""
    if ident.startswith('"'):
        value = ident[1:-1].replace('""', '"')
    else:
        value = ''.join(ch.lower() if 'A' <= ch <= 'Z' else ch for ch in ident)
    raw = value.encode('utf-8')
    if len(raw) >= _NAMEDATALEN:
        value = raw[:_NAMEDATALEN - 1].decode('utf-8', 'ignore')
    return value


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def _table_literal(match):
    # to_regclass разбирает имя по правилам SQL (кавычки, регистр, схема) — отдаём как написано.
    schema = match.group('schema')
    return _literal((schema + '.' if schema else '') + match.group('rel'))


def _free_tag(text, base):
    tag, k = base, 0
    while '$' + tag + '$' in text:
        k += 1
        tag = '%s%d' % (base, k)
    return tag


def _guarded(statement, table_literal, conditions):
    inner = _free_tag(statement, 'otp_ddl')
    outer = _free_tag(statement, 'otp_guard')
    counter = (
        "PERFORM pg_catalog.set_config('{0}', (COALESCE(NULLIF(pg_catalog.current_setting('{0}', true), ''),"
        " '0')::int + 1)::text, true);".format(APPLIED_SETTING)
    )
    return (
        'DO ${outer}$ DECLARE otp_rel regclass := pg_catalog.to_regclass({table}); '
        'BEGIN IF otp_rel IS NULL OR NOT ({cond}) THEN EXECUTE ${inner}${stmt}${inner}$; {counter} '
        'END IF; END ${outer}$'
    ).format(outer=outer, inner=inner, table=table_literal, cond=' AND '.join(conditions),
             stmt=statement, counter=counter)


def _action_condition(kind, name):
    name = _literal(_name(name))
    if kind == 'add_column':
        return _COLUMN_EXISTS.format(name=name, extra='')
    if kind == 'drop_column':
        return 'NOT ' + _COLUMN_EXISTS.format(name=name, extra='')
    if kind == 'drop_constraint':
        return ('NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint'
                ' WHERE conrelid = otp_rel AND conname = {})'.format(name))
    if kind == 'drop_not_null':
        return _COLUMN_EXISTS.format(name=name, extra=' AND NOT attnotnull')
    if kind == 'set_not_null':
        # Непроверенный NOT NULL (NOT VALID, PG 18) SET NOT NULL проверил бы — тогда не пусто.
        return _COLUMN_EXISTS.format(name=name, extra=(
            ' AND attnotnull AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint WHERE conrelid = otp_rel'
            " AND contype = 'n' AND NOT convalidated AND conkey = ARRAY[attnum])"))
    # drop_default: у identity и generated-колонок DROP DEFAULT — ошибка, её оставляем Postgres.
    return _COLUMN_EXISTS.format(name=name, extra=" AND NOT atthasdef AND attidentity = '' AND attgenerated = ''")


def _same_table(first, second):
    def key(match):
        schema = match.group('schema')
        return (_name(schema) if schema else None, _name(match.group('rel')))
    return key(first) == key(second)


def _trigger_pair(pieces, i):
    """«DROP TRIGGER IF EXISTS x ON t; CREATE TRIGGER x … ON t» подряд в пачке → (DO-блок, индекс CREATE).

    Пара на готовой схеме — не пустая операция (снять и поставить тот же триггер), поэтому
    сверяется не каталог, а отпечаток текста CREATE в COMMENT триггера: совпал — триггер
    поставлен ровно этим текстом и трогать его незачем. Отпечатка нет (поставлен до 26.11.2026)
    или текст поменялся — пара выполняется как раньше, и отпечаток ставится. None — не пара."""
    drop = _DROP_TRIGGER.match(pieces[i][1])
    if drop is None:
        return None
    j = i + 1
    while j < len(pieces) and not pieces[j][1].strip():
        j += 1
    if j == len(pieces):
        return None
    create_raw, create_code = pieces[j]
    create = _CREATE_TRIGGER.match(create_code)
    if create is None or _name(create.group('name')) != _name(drop.group('name')):
        return None
    on = _ON_TABLE.search(create_code, create.end())
    if on is None or not _same_table(drop, on):
        return None
    fingerprint = _literal(ddl_fingerprint(create_code))
    name = _literal(_name(drop.group('name')))
    comment = 'COMMENT ON TRIGGER {} ON {} IS {}'.format(
        create.group('name'), (drop.group('schema') + '.' if drop.group('schema') else '') + drop.group('rel'),
        fingerprint)
    body = create_raw + ';' + pieces[i][0] + comment
    inner = _free_tag(body, 'otp_ddl')
    outer = _free_tag(body, 'otp_guard')
    condition = ("EXISTS (SELECT 1 FROM pg_catalog.pg_trigger WHERE tgrelid = otp_rel AND tgname = {} AND NOT "
                 "tgisinternal AND tgenabled = 'O' AND pg_catalog.obj_description(oid, 'pg_trigger') = {})"
                 ).format(name, fingerprint)
    counter = (
        "PERFORM pg_catalog.set_config('{0}', (COALESCE(NULLIF(pg_catalog.current_setting('{0}', true), ''),"
        " '0')::int + 1)::text, true);".format(APPLIED_SETTING)
    )
    block = (
        'DO ${outer}$ DECLARE otp_rel regclass := pg_catalog.to_regclass({table}); '
        'BEGIN IF otp_rel IS NULL OR NOT ({cond}) THEN EXECUTE ${inner}${drop}${inner}$; '
        'EXECUTE ${inner}${create}${inner}$; EXECUTE ${inner}${comment}${inner}$; {counter} '
        'END IF; END ${outer}$'
    ).format(outer=outer, inner=inner, table=_table_literal(drop), cond=condition, drop=pieces[i][0],
             create=create_raw, comment=comment, counter=counter)
    return block, j


def guard_statement(statement, code):
    """DO-блок вместо оператора, который может оказаться пустым; None — оператор не из таких."""
    match = _DROP_TRIGGER.match(code)
    if match:
        condition = ('NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger WHERE tgrelid = otp_rel AND tgname = {})'
                     ).format(_literal(_name(match.group('name'))))
        return _guarded(statement, _table_literal(match), [condition])
    match = _CREATE_INDEX.match(code)
    if match:
        condition = ('EXISTS (SELECT 1 FROM pg_catalog.pg_class WHERE relname = {} AND relnamespace = '
                     '(SELECT relnamespace FROM pg_catalog.pg_class WHERE oid = otp_rel))'
                     ).format(_literal(_name(match.group('name'))))
        return _guarded(statement, _table_literal(match), [condition])
    match = _ALTER_TABLE.match(code)
    if not match:
        return None
    actions = _split_actions(match.group('actions'))
    if not actions:
        return None
    conditions = []
    for action in actions:
        for kind, pattern in _ACTIONS:
            found = pattern.match(action)
            if found:
                conditions.append(_action_condition(kind, found.group('name')))
                break
        else:
            return None
    return _guarded(statement, _table_literal(match), conditions)


def guard_sql(sql):
    """(текст для базы, сколько операторов обёрнуто проверкой каталога)."""
    pieces = split_statements(sql)
    if pieces is None:
        return sql, 0
    out, guarded, i = [], 0, 0
    while i < len(pieces):
        statement, code = pieces[i]
        if not code.strip():
            out.append(statement)
            i += 1
            continue
        pair = _trigger_pair(pieces, i)
        if pair is not None:
            block, j = pair
            # Пара уходит одним DO-блоком; пустые куски между ней — пустые операторы.
            out.append(block)
            out.extend('' for _ in range(i + 1, j + 1))
            guarded += 1
            i = j + 1
            continue
        replacement = guard_statement(statement, code)
        out.append(statement if replacement is None else replacement)
        guarded += replacement is not None
        i += 1
    return (';'.join(out), guarded) if guarded else (sql, 0)


class GuardedSchemaCursor(psycopg2.extensions.cursor):
    """Курсор миграции схемы: «пустые» DDL — через проверку каталога (см. шапку модуля).

    Переписывается только текст без параметров: с параметрами (%s) и составные/байтовые запросы
    (psycopg2.sql, extras.execute_values) идут как есть."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.guarded_statements = 0

    def execute(self, query, vars=None):
        if vars is None and isinstance(query, str):
            query, guarded = guard_sql(query)
            self.guarded_statements += guarded
        return super().execute(query, vars)

    def applied_statements(self):
        """Сколько обёрнутых операторов в этой транзакции дошли до базы (было что менять)."""
        super().execute('SELECT pg_catalog.current_setting(%s, true)', (APPLIED_SETTING,))
        value = self.fetchone()[0]
        return int(value) if value else 0


def guard_enabled():
    """False — OTP_SCHEMA_GUARD=0/false/off/no: прежнее поведение целиком."""
    return (os.getenv(GUARD_ENV) or '').strip().lower() not in ('0', 'false', 'off', 'no')


def schema_cursor_factory():
    """Класс курсора для _init_db; None — выключено переменной OTP_SCHEMA_GUARD."""
    return GuardedSchemaCursor if guard_enabled() else None


def ddl_fingerprint(sql):
    return FINGERPRINT_PREFIX + hashlib.sha256(' '.join(sql.split()).encode('utf-8')).hexdigest()[:16]


def _normalized(text, schema='public'):
    """Текст триггера для сверки: без «;», лишних пробелов, регистра и схемы таблицы.

    pg_get_triggerdef всегда пишет таблицу со схемой («ON public.users»), а функцию — со схемой,
    только если её не видно по search_path."""
    text = ' '.join(text.replace(';', ' ').split()).lower()
    for prefix in {'public', (schema or 'public').lower()}:
        text = text.replace(' %s.' % prefix, ' ')
    return text


def _check_literals(text):
    """Строковые и числовые литералы выражения CHECK — для сверки «тот же список значений»."""
    strings = [value.replace("''", "'") for value in re.findall(r"'((?:[^']|'')*)'", text)]
    numbers = re.findall(r'(?<![\w.])\d+(?:\.\d+)?(?![\w.])', re.sub(r"'(?:[^']|'')*'", ' ', text))
    return sorted(strings), sorted(numbers)


def _added_checks(sql):
    """{имя: текст выражения CHECK (…)} из ADD CONSTRAINT имя CHECK (…) в тексте DDL."""
    found = {}
    for match in re.finditer(r'ADD\s+CONSTRAINT\s+(' + _IDENT + r')\s+CHECK\s*\(', sql, re.I):
        depth, i = 1, match.end()
        while i < len(sql) and depth:
            if sql[i] == "'":
                i = _quote_end(sql, i, "'") or len(sql)
                continue
            depth += {'(': 1, ')': -1}.get(sql[i], 0)
            i += 1
        found[_name(match.group(1))] = sql[match.end():i - 1]
    return found


def replace_constraints(cursor, table, names, sql):
    """Выполнить DDL пересборки ограничений names таблицы table, только если поменялся его текст.

    Текущим считается ограничение с отпечатком sql в COMMENT. Отпечатка ещё нет (ограничения
    созданы до 26.11.2026 этим же текстом на прошлом старте) — CHECK с тем же набором значений
    принимается как есть, ставится только отпечаток: COMMENT берёт ShareUpdateExclusiveLock, он не
    мешает ни чтению, ни записи. True — пересобрано."""
    if not guard_enabled():
        cursor.execute(sql)
        return True
    fingerprint = ddl_fingerprint(sql)
    cursor.execute(
        """SELECT conname, contype, pg_catalog.pg_get_constraintdef(oid),
                  pg_catalog.obj_description(oid, 'pg_constraint')
             FROM pg_catalog.pg_constraint
            WHERE conrelid = pg_catalog.to_regclass(%s) AND conname = ANY(%s)""",
        (table, list(names)))
    existing = {row[0]: row[1:] for row in cursor.fetchall()}
    checks = _added_checks(sql)
    adopt = []
    for name in names:
        row = existing.get(name)
        if row is None:
            break
        contype, definition, comment = row
        if comment == fingerprint:
            continue
        if (comment is None and contype == 'c' and name in checks
                and _check_literals(definition) == _check_literals(checks[name])):
            adopt.append(name)
            continue
        break
    else:
        for name in adopt:
            cursor.execute(pg_sql.SQL('COMMENT ON CONSTRAINT {} ON {} IS %s').format(
                pg_sql.Identifier(name), pg_sql.Identifier(table)), (fingerprint,))
        return False
    cursor.execute(sql)
    for name in names:
        cursor.execute(pg_sql.SQL('COMMENT ON CONSTRAINT {} ON {} IS %s').format(
            pg_sql.Identifier(name), pg_sql.Identifier(table)), (fingerprint,))
    return True


def replace_trigger(cursor, table, name, create_sql):
    """DROP TRIGGER + CREATE TRIGGER, только если поменялся текст create_sql (отпечаток в COMMENT).

    Отпечатка ещё нет — триггер с тем же определением (pg_get_triggerdef без пробелов, регистра и
    «public.») принимается как есть, ставится только отпечаток. True — пересоздан."""
    drop_sql = pg_sql.SQL('DROP TRIGGER IF EXISTS {} ON {}').format(
        pg_sql.Identifier(name), pg_sql.Identifier(table))
    if not guard_enabled():
        cursor.execute(drop_sql)
        cursor.execute(create_sql)
        return True
    fingerprint = ddl_fingerprint(create_sql)
    cursor.execute(
        """SELECT pg_catalog.obj_description(t.oid, 'pg_trigger'), pg_catalog.pg_get_triggerdef(t.oid), n.nspname
             FROM pg_catalog.pg_trigger t
             JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
             JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE t.tgrelid = pg_catalog.to_regclass(%s) AND t.tgname = %s
              AND NOT t.tgisinternal AND t.tgenabled = 'O'""",
        (table, name))
    row = cursor.fetchone()
    comment_sql = pg_sql.SQL('COMMENT ON TRIGGER {} ON {} IS %s').format(
        pg_sql.Identifier(name), pg_sql.Identifier(table))
    if row is not None:
        if row[0] == fingerprint:
            return False
        if row[0] is None and _normalized(row[1], row[2]) == _normalized(create_sql, row[2]):
            cursor.execute(comment_sql, (fingerprint,))
            return False
    cursor.execute(drop_sql)
    cursor.execute(create_sql)
    cursor.execute(comment_sql, (fingerprint,))
    return True
