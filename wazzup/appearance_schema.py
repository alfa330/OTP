"""Обои «Чатов ОП» (wazzup/appearance.py): своя картинка на фоне переписки.

Строка на человека и только пока обои стоят: убрал — строки нет. Сам файл и его
миниатюра лежат в хранилище (GCS), здесь — их адреса, размеры и настройки вида.

Внешнего ключа на users нет намеренно, как у wazzup_pilot_uploads: при старте
он брал бы блокировку общей таблицы, а людей не удаляют — увольнение это статус.
"""


def init_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_chat_wallpapers (
            user_id BIGINT PRIMARY KEY,
            wallpaper_id UUID NOT NULL,
            bucket TEXT NOT NULL,
            blob_path TEXT NOT NULL,
            thumb_path TEXT NOT NULL,
            width INTEGER NOT NULL CHECK (width > 0),
            height INTEGER NOT NULL CHECK (height > 0),
            file_size INTEGER NOT NULL CHECK (file_size > 0),
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wazzup_chat_wallpapers_id
            ON wazzup_chat_wallpapers(wallpaper_id);
    """)
