# -*- coding: utf-8 -*-
"""Схема «Доски почёта» (задача #318) — одна таблица honor_boards.

Строка — доска одного направления (ОП, СЗоВ, Фронт-офис) за один месяц.
У неё две версии записей:

    draft_entries      черновик тренера: окно сохраняет его само по ходу ввода
    published_entries  то, что видят сотрудники; NULL — доска не опубликована

«Опубликовать» (ТЗ, п. 4) копирует заполненные строки черновика в
опубликованные. Правка уже опубликованной доски идёт в черновик и выходит к
сотрудникам следующим «Опубликовать» — недописанный стих никто не увидит.

Записи — JSONB, а не таблица строк: доска читается и пишется только целиком,
строк в ней не больше четырёх, а опубликованная версия — снимок на момент
публикации (имя и номинация остаются такими, какими их объявили, даже если
сотрудник потом сменит фамилию или уволится). Фото не снимается: карточка
берёт его из профиля при показе.

revision растёт с каждой сменой черновика. Окно присылает ту, от которой
правило: две вкладки (или два тренера) не затрут друг друга молча, а
«Опубликовать» выкладывает ровно то, что тренер видит на экране.

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
)


def _table_exists(cursor, name):
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (f'public.{name}',))
    row = cursor.fetchone()
    return bool(row and row[0])


def init_honor_board_schema(cursor):
    """Разворачивает схему доски. Идемпотентно."""
    for statement in _STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW))


def schema_is_ready(cursor):
    return _table_exists(cursor, 'honor_boards')
