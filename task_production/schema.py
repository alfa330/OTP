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
        -- NULL — файл «Медиатеки»: загружен просто так, без задачи.
        task_id          INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
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
    # Файлы «Медиатеки» без задачи (просьба владельца 16.10.2026: «как можно просто загружать
    # ролики — данная возможность тоже должна быть на сайте»). Снятие NOT NULL
    # идемпотентно: на уже снятом ограничении оператор ничего не делает.
    "ALTER TABLE task_production_materials ALTER COLUMN task_id DROP NOT NULL;",
    # Категория файла «Медиатеки» (без задачи): баннеры, анимационные, рекламные,
    # рилсы. Без CHECK намеренно: список правит код (materials.LIBRARY_CATEGORIES),
    # а новый пункт не должен требовать перестройки ограничения на живой таблице.
    "ALTER TABLE task_production_materials ADD COLUMN IF NOT EXISTS library_category VARCHAR(16);",
    "CREATE INDEX IF NOT EXISTS idx_task_production_materials_library_category "
    "ON task_production_materials (library_category, created_at DESC) WHERE task_id IS NULL;",
    "CREATE INDEX IF NOT EXISTS idx_task_production_materials_library "
    "ON task_production_materials (created_at DESC) WHERE task_id IS NULL;",
    "CREATE INDEX IF NOT EXISTS idx_task_production_materials_task "
    "ON task_production_materials (task_id, version, created_at);",
    # Недогруженные — для уборки брошенных сессий (живут неделю).
    "CREATE INDEX IF NOT EXISTS idx_task_production_materials_uploading "
    "ON task_production_materials (created_at) WHERE status = 'uploading';",
    # Доска и «Готовое» читают только задачи продакшна — их единицы среди сотен.
    "CREATE INDEX IF NOT EXISTS idx_tasks_production_status "
    "ON tasks (status, updated_at DESC) WHERE tag = 'production';",
    # «История» «Медиатеки»: кто и когда добавил, перенёс или удалил файл (просьба
    # владельца 16.10.2026 — «историю кто когда добавлял»). Отдельным журналом, а
    # не из таблицы файлов: удалённый файл из неё исчезает, а в истории остаётся —
    # с тем, кто его удалил. Поэтому material_id без внешнего ключа, а имя, вид и
    # размер файла записаны в саму строку. action — added | moved | deleted, без
    # CHECK: список действий правит код. category — куда положили (added, moved)
    # или откуда удалили (deleted); from_category — откуда перенесли.
    """
    CREATE TABLE IF NOT EXISTS task_production_library_events (
        id            BIGSERIAL PRIMARY KEY,
        material_id   UUID NOT NULL,
        action        VARCHAR(16) NOT NULL,
        file_name     VARCHAR(255) NOT NULL,
        kind          VARCHAR(16) NOT NULL,
        file_size     BIGINT NOT NULL DEFAULT 0,
        category      VARCHAR(16),
        from_category VARCHAR(16),
        actor_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at    TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_production_library_events_created "
    "ON task_production_library_events (created_at DESC, id DESC);",
    # Переименование (просьба владельца 19.10.2026: «чтобы можно было менять
    # название… загруженных видеороликов или банеров») — action renamed:
    # file_name — новое имя, from_name — прежнее.
    "ALTER TABLE task_production_library_events ADD COLUMN IF NOT EXISTS from_name VARCHAR(255);",
)
