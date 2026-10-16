# -*- coding: utf-8 -*-
"""Схема материалов «Продакшна» (задача #316). Все операторы идемпотентны.

Отдельная таблица, а не task_attachments: у вложения задачи нет ни версии, ни
состояния «ещё грузится», ни обложки, а его CHECK по виду (`initial`/`result`)
пришлось бы расширять — и вместе с ним все места, которые этот вид читают
(Telegram, выгрузка, CLI доски). Материал живёт и умирает вместе с задачей
(ON DELETE CASCADE); объекты в хранилище при удалении задачи убирает тот же
маршрут, что убирает её вложения.

Время — как у остальных таблиц задач: местное (Asia/Almaty), без зоны.
"""

STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS task_production_materials (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        task_id          INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        version          INTEGER NOT NULL CHECK (version >= 1),
        -- uploading: сессия докачки открыта, файла в хранилище ещё нет целиком.
        -- ready: объект собран и сверен (размер, CRC32C, подпись формата).
        status           VARCHAR(16) NOT NULL DEFAULT 'uploading'
                         CHECK (status IN ('uploading', 'ready')),
        kind             VARCHAR(16) NOT NULL CHECK (kind IN ('video', 'image', 'file')),
        original_name    VARCHAR(255) NOT NULL,
        extension        VARCHAR(16) NOT NULL,
        content_type     VARCHAR(100) NOT NULL,
        file_size        BIGINT NOT NULL CHECK (file_size > 0),
        bucket           VARCHAR(255) NOT NULL,
        blob_path        TEXT NOT NULL UNIQUE,
        -- Адрес сессии докачки и Origin страницы, которая её открыла. Нужны,
        -- пока файл грузится: по ним сервер узнаёт, сколько уже принято, и
        -- отменяет загрузку. После сборки стираются.
        upload_session   TEXT,
        upload_origin    VARCHAR(255),
        -- Отпечаток файла от браузера (дата изменения): докачка подхватывает
        -- незаконченную загрузку только того же файла, а не любого с тем же именем
        -- и размером. Подменённое содержимое всё равно не пройдёт сверку CRC32C.
        client_fingerprint VARCHAR(64),
        poster_blob_path TEXT,
        duration_seconds NUMERIC(10, 3),
        width            INTEGER,
        height           INTEGER,
        uploaded_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at       TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
        ready_at         TIMESTAMP
    );
    """,
    "ALTER TABLE task_production_materials ADD COLUMN IF NOT EXISTS client_fingerprint VARCHAR(64);",
    "CREATE INDEX IF NOT EXISTS idx_task_production_materials_task "
    "ON task_production_materials (task_id, version, created_at);",
    # Недогруженные — для уборки брошенных сессий (живут неделю).
    "CREATE INDEX IF NOT EXISTS idx_task_production_materials_uploading "
    "ON task_production_materials (created_at) WHERE status = 'uploading';",
    # Доска и «Готовое» читают только задачи продакшна — их единицы среди сотен.
    "CREATE INDEX IF NOT EXISTS idx_tasks_production_status "
    "ON tasks (status, updated_at DESC) WHERE tag = 'production';",
)
