# -*- coding: utf-8 -*-
"""Схема «Доски почёта» (задача #318).

honor_boards — доска одного направления (ОП, СЗоВ, Фронт-офис) за один месяц.
У неё две версии записей:

    draft_entries      черновик тренера: окно сохраняет его само по ходу ввода
    published_entries  то, что видят сотрудники; NULL — доска не опубликована

«Опубликовать» (ТЗ, п. 4) копирует заполненные строки черновика в
опубликованные. Правка уже опубликованной доски идёт в черновик и выходит к
сотрудникам следующим «Опубликовать» — недописанный стих никто не увидит.

Записи — JSONB, а не таблица строк: доска читается и пишется только целиком,
строк в ней не больше восьми, а опубликованная версия — снимок на момент
публикации (имя и номинация остаются такими, какими их объявили, даже если
сотрудник потом сменит фамилию или уволится).

revision растёт с каждой сменой черновика. Окно присылает ту, от которой
правило: две вкладки (или два тренера) не затрут друг друга молча, а
«Опубликовать» выкладывает ровно то, что тренер видит на экране.

first_published_at — когда доску выложили ВПЕРВЫЕ: по нему колокол сообщает
всем, что вышли итоги месяца. «Опубликовать правки» и повторная публикация
после снятия его не трогают — второй раз о той же доске не звонят.

honor_board_photos — фото для доски, которое загрузил тренер (не у всех фото
профиля годятся для доски): снимок с вырезанным фоном или как есть. Строка
привязана к сотруднику на снимке, запись доски ссылается на неё photo_id.
Фото не удаляются: на них ссылаются и опубликованные доски прошлых месяцев.

honor_board_reads — водяной знак «видел вышедшие доски» для колокола (как
event_reads у ленты «Ивентов»).

Направление — строкой без CHECK: список живёт в honor_board/catalog.py, и
четвёртое направление не потребует миграции констрейнта.
"""

# Время Алматы без пояса — как во всём портале (library/schema.py, news/schema.py).
_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS honor_boards (
        id SERIAL PRIMARY KEY,
        period_month DATE NOT NULL CHECK (EXTRACT(DAY FROM period_month) = 1),
        department_key TEXT NOT NULL,
        draft_entries JSONB NOT NULL DEFAULT '[]'::jsonb,
        revision INTEGER NOT NULL DEFAULT 1,
        updated_at TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        published_entries JSONB,
        published_at TIMESTAMP,
        published_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP NOT NULL DEFAULT %(now)s,
        CONSTRAINT uq_honor_boards_period_department UNIQUE (period_month, department_key)
    )
    """,
    "ALTER TABLE honor_boards ADD COLUMN IF NOT EXISTS first_published_at TIMESTAMP",
    # Доски, выложенные до появления колонки: их первая публикация — нынешняя.
    """
    UPDATE honor_boards
       SET first_published_at = published_at
     WHERE first_published_at IS NULL AND published_at IS NOT NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS honor_board_photos (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        bucket TEXT NOT NULL,
        blob_path TEXT NOT NULL,
        content_type TEXT NOT NULL,
        width INTEGER NOT NULL,
        height INTEGER NOT NULL,
        file_size INTEGER NOT NULL,
        cutout BOOLEAN NOT NULL DEFAULT FALSE,
        uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    # Последнее фото человека — его окно подставляет, когда тренер выбирает победителя.
    "CREATE INDEX IF NOT EXISTS idx_honor_board_photos_user ON honor_board_photos (user_id, id DESC)",
    """
    CREATE TABLE IF NOT EXISTS honor_board_reads (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        last_seen_at TIMESTAMP NOT NULL
    )
    """,
)

TABLES = ('honor_boards', 'honor_board_photos', 'honor_board_reads')


def _table_exists(cursor, name):
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (f'public.{name}',))
    row = cursor.fetchone()
    return bool(row and row[0])


def _column_exists(cursor, table, column):
    cursor.execute("""
        SELECT EXISTS (
            SELECT 1 FROM pg_attribute
             WHERE attrelid = to_regclass(%s) AND attname = %s AND NOT attisdropped
        )
    """, (f'public.{table}', column))
    row = cursor.fetchone()
    return bool(row and row[0])


def init_honor_board_schema(cursor):
    """Разворачивает схему доски. Идемпотентно."""
    for statement in _STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW))


def schema_is_ready(cursor):
    return (all(_table_exists(cursor, name) for name in TABLES)
            and _column_exists(cursor, 'honor_boards', 'first_published_at'))
