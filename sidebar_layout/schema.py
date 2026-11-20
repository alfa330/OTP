"""Схема личной раскладки сайдбара: одна строка на сотрудника.

Идемпотентно (CREATE TABLE IF NOT EXISTS), разворачивается при старте из
Database._init_db через init_sidebar_layout_schema(cursor).

Раскладка — один JSONB: её читают и пишут только целиком (меню человека
собирается одним запросом), разбирать её на строки по разделам незачем.
Учётку удалили — уходит и её раскладка (CASCADE): это вид, а не свидетельство.
Индекса сверх первичного ключа не нужно — других выборок нет.
"""


def init_sidebar_layout_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_sidebar_layouts (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            layout JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
