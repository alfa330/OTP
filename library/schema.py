# -*- coding: utf-8 -*-
"""Схема раздела «Библиотека» (задача #282).

Три таблицы под префиксом `library_`:

    library_books     книга: описание, оглавление, разметка страниц, где файл
    library_saved     «Сохранённые» — личная подборка читателя
    library_progress  где читатель остановился и сколько прочитал

Порядок важен: обе личные таблицы ссылаются на library_books, а та — на users.

Удаление книги уносит закладки и прогресс каскадом — книги больше нет, и
строка «читал 45 % книги, которой нет» в мониторинге читалась бы как ошибка.
Блобы файла и обложки удаляет роут ПОСЛЕ фиксации (library/routes.py).
"""

# Время Алматы без пояса — как во всём портале (news/schema.py, parcels/schema.py).
_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS library_books (
        id SERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        author TEXT NOT NULL DEFAULT '',
        language TEXT NOT NULL DEFAULT '',
        -- Файл книги и обложка в бакете. Обложки может не быть вовсе:
        -- у части книг её нет в самом файле, карточка тогда рисует заглушку.
        bucket TEXT NOT NULL,
        file_blob TEXT NOT NULL,
        file_size INTEGER NOT NULL DEFAULT 0,
        original_name TEXT NOT NULL DEFAULT '',
        cover_blob TEXT,
        cover_type TEXT,
        -- Разметка страниц (library/epub.py): главы в порядке чтения с числом
        -- знаков и началом каждой, оглавление с номерами страниц. Считается
        -- ОДИН раз при загрузке, ридер лишь складывает.
        spine JSONB NOT NULL DEFAULT '[]'::jsonb,
        toc JSONB NOT NULL DEFAULT '[]'::jsonb,
        total_chars INTEGER NOT NULL DEFAULT 0,
        total_pages INTEGER NOT NULL DEFAULT 1,
        uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_library_books_created ON library_books (created_at DESC, id DESC)",
    """
    CREATE TABLE IF NOT EXISTS library_saved (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        book_id INTEGER NOT NULL REFERENCES library_books(id) ON DELETE CASCADE,
        created_at TIMESTAMP NOT NULL DEFAULT %(now)s,
        PRIMARY KEY (user_id, book_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS library_progress (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        book_id INTEGER NOT NULL REFERENCES library_books(id) ON DELETE CASCADE,
        -- Место в книге: «номер главы:доля главы» (12:0.4375). Доля, а не
        -- номер экрана: на другом экране страниц в главе другое число, и
        -- ридер открывается на той же странице, где закрыли, плюс-минус лист.
        position TEXT NOT NULL DEFAULT '',
        percent NUMERIC(5, 2) NOT NULL DEFAULT 0
            CHECK (percent >= 0 AND percent <= 100),
        page INTEGER NOT NULL DEFAULT 1 CHECK (page >= 1),
        started_at TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at TIMESTAMP NOT NULL DEFAULT %(now)s,
        -- Когда пролистали последнюю страницу. Однажды поставленная отметка
        -- не снимается: вернувшийся перечитать главу книгу не «разчитал».
        finished_at TIMESTAMP,
        PRIMARY KEY (user_id, book_id)
    )
    """,
    # Мониторинг: «кто что читает» по книге и свежесть активности.
    "CREATE INDEX IF NOT EXISTS idx_library_progress_book ON library_progress (book_id)",
    "CREATE INDEX IF NOT EXISTS idx_library_progress_updated ON library_progress (updated_at DESC)",
)


def init_library_schema(cursor):
    """Разворачивает схему раздела. Идемпотентно."""
    for statement in _STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW))


def schema_is_ready(cursor):
    cursor.execute("SELECT to_regclass('public.library_progress') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
