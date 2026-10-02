# -*- coding: utf-8 -*-
"""Схема раздела «Библиотека» (задача #282).

Шесть таблиц под префиксом `library_`:

    library_books             книга: описание, оглавление, разметка страниц, где
                              файл; archived_at — книга в архиве
    library_book_departments  каким отделам книга видна (один или несколько)
    library_saved             «Сохранённые» — личная подборка читателя
    library_progress          где читатель остановился и сколько прочитал
    library_genres            жанры — общий справочник библиотеки
    library_book_genres       жанры книги (ни одного, один или несколько)

Порядок важен: обе личные таблицы, отделы и жанры книги ссылаются на
library_books, а та — на users.

Архив — не удаление: книга пропадает из каталога у читателей, но закладки и
прогресс остаются, и мониторинг по-прежнему показывает, кто её читал. Удаляется
насовсем только книга из архива (library/routes.py).

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
    # Архив: книга убрана из каталога, но не удалена. Кто и когда убрал —
    # чтобы в архиве было видно, откуда книга там взялась.
    "ALTER TABLE library_books ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP",
    "ALTER TABLE library_books ADD COLUMN IF NOT EXISTS archived_by INTEGER REFERENCES users(id) ON DELETE SET NULL",
    # Отделы книги. Отдельная таблица, а не массив в строке книги: удалённый
    # отдел уходит из книг каскадом, а вопрос «что видно отделу» — индекс.
    """
    CREATE TABLE IF NOT EXISTS library_book_departments (
        book_id INTEGER NOT NULL REFERENCES library_books(id) ON DELETE CASCADE,
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        PRIMARY KEY (book_id, department_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_library_book_departments_department"
    " ON library_book_departments (department_id, book_id)",
    # Жанры (02.10.2026). Справочник общий на всю библиотеку, а не на отдел:
    # жанр — свойство книги, а книга бывает выдана нескольким отделам сразу.
    # Имя уникально без учёта регистра: «Психология» и «психология» — один жанр.
    """
    CREATE TABLE IF NOT EXISTS library_genres (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_library_genres_name ON library_genres (LOWER(name))",
    # Удалённый жанр снимается с книг каскадом, удалённая книга — тоже.
    """
    CREATE TABLE IF NOT EXISTS library_book_genres (
        book_id INTEGER NOT NULL REFERENCES library_books(id) ON DELETE CASCADE,
        genre_id INTEGER NOT NULL REFERENCES library_genres(id) ON DELETE CASCADE,
        PRIMARY KEY (book_id, genre_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_library_book_genres_genre ON library_book_genres (genre_id, book_id)",
)

# Книги, загруженные до разделения по отделам (28–29.09.2026), получают отдел
# того, кто их загрузил. Выполняется ОДИН раз — в момент создания таблицы
# отделов: позже книга без отделов — это книга, чей отдел удалили, и молча
# раздавать её отделу загрузившего было бы решением за управляющего.
_BACKFILL_DEPARTMENTS = """
    INSERT INTO library_book_departments (book_id, department_id)
    SELECT b.id, u.department_id
      FROM library_books b
      JOIN users u ON u.id = b.uploaded_by
     WHERE u.department_id IS NOT NULL
    ON CONFLICT DO NOTHING
"""


def _table_exists(cursor, name):
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (f'public.{name}',))
    row = cursor.fetchone()
    return bool(row and row[0])


def init_library_schema(cursor):
    """Разворачивает схему раздела. Идемпотентно."""
    departments_existed = _table_exists(cursor, 'library_book_departments')
    for statement in _STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW))
    if not departments_existed:
        cursor.execute(_BACKFILL_DEPARTMENTS)


def schema_is_ready(cursor):
    # Последний объект схемы: есть он — есть и всё, что создано до него.
    # Проверка по таблице постарше пустила бы запросы с жанрами книг на базу,
    # где до жанров миграция ещё не дошла, — 500 вместо «разворачивается».
    return _table_exists(cursor, 'library_book_genres')
