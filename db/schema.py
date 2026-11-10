"""База данных: схема и миграции (инициализация при старте).

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _is_schema_lock_conflict,
    BINOTEL_CABINET_URL_DEFAULT,
    CALCULATION_MODEL_OPERATOR,
    contextmanager,
    datetime,
    defaultdict,
    logging,
    normalize_calculation_model_code,
    psycopg2,
    SHIFT_AUCTION_MODE_CHAT,
    SHIFT_AUCTION_MODE_LINE,
    SHIFT_AUCTION_SETTINGS_ROW_ID,
    SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES,
    task_production_schema,
    task_projects,
    time,
)


class SchemaMixin:
    @contextmanager
    def _schema_init_lock(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            acquired = False
            started_at = time.time()
            try:
                while True:
                    cursor.execute("SELECT pg_try_advisory_lock(%s)", (self.SCHEMA_INIT_LOCK_KEY,))
                    acquired = bool(cursor.fetchone()[0])
                    if acquired:
                        break
                    if (time.time() - started_at) >= float(self.SCHEMA_INIT_LOCK_TIMEOUT_SEC):
                        raise TimeoutError("Timeout waiting for schema init advisory lock")
                    time.sleep(0.25)
                yield
            finally:
                if acquired:
                    try:
                        cursor.execute("SELECT pg_advisory_unlock(%s)", (self.SCHEMA_INIT_LOCK_KEY,))
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        logging.exception("Failed to release schema init advisory lock")
                cursor.close()

    def _init_db_with_retry(self):
        last_error = None
        attempts = int(self.SCHEMA_INIT_RETRY_ATTEMPTS)
        for attempt in range(1, attempts + 1):
            try:
                with self._schema_init_lock():
                    self._init_db()
                return
            except psycopg2.Error as exc:
                last_error = exc
                # LockNotAvailable/DeadlockDetected тоже OperationalError, но соединение
                # исправно. Конфликт требует отката всей миграции и паузы, а не сброса пула.
                is_lock_conflict = _is_schema_lock_conflict(exc)
                is_transient_conn = (
                    isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError))
                    and not is_lock_conflict
                )
                if (is_lock_conflict or is_transient_conn) and attempt < attempts:
                    if is_transient_conn:
                        # Пул мог частично создаться с «протухшими» соединениями —
                        # сбрасываем, чтобы следующая попытка построила его заново.
                        self._reset_pool_after_transient_error()
                    delay = min(5.0, float(attempt))
                    logging.warning(
                        "Transient error during DB init (%s, attempt %s/%s). Retrying in %.1fs",
                        getattr(exc, "pgcode", None) or type(exc).__name__,
                        attempt,
                        attempts,
                        delay
                    )
                    time.sleep(delay)
                    continue
                raise
        if last_error:
            raise last_error

    def _init_db(self):
        with self._get_cursor() as cursor:
            # Даже ALTER ... IF NOT EXISTS запрашивает AccessExclusiveLock. Если импорт
            # держит читающую транзакцию, ожидающий ALTER ставит за собой авторизацию
            # пользователей и заполняет пул. Ограничение действует только в этой транзакции;
            # при конфликте _init_db_with_retry повторяет её после полного отката.
            cursor.execute("SELECT set_config('lock_timeout', %s, true)",
                           (f'{self.SCHEMA_DDL_LOCK_TIMEOUT_MS}ms',))
            # Departments table (created first so users/directions can reference it)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS departments (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(50) NOT NULL UNIQUE,
                    slug VARCHAR(100) NOT NULL UNIQUE,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            # Directions table (created before users so direction_id FK can reference it)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS directions (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
                    has_file_upload BOOLEAN NOT NULL DEFAULT TRUE,
                    criteria JSONB NOT NULL DEFAULT '[]',
                    calculation_model_code VARCHAR(32) NOT NULL DEFAULT 'operator',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    version INTEGER NOT NULL DEFAULT 1,
                    previous_version_id INTEGER REFERENCES directions(id)
                );
            """)
            # canonical_id: у архивной версии направления — id его «живой» строки.
            # Живая строка направления держит свой id навсегда (операторы, группы и
            # внешние связки, включая ИИ-оценку, не отвязываются при правках);
            # архивные строки хранят шкалу критериев «как была» для старых оценок.
            cursor.execute("""
                SELECT 1 FROM pg_attribute
                WHERE attrelid = 'directions'::regclass
                  AND attname = 'canonical_id' AND NOT attisdropped
            """)
            if not cursor.fetchone():
                cursor.execute("""
                    ALTER TABLE directions
                    ADD COLUMN IF NOT EXISTS canonical_id INTEGER REFERENCES directions(id) ON DELETE SET NULL;
                """)
            # Бекфилл для исторических цепочек версий (старый механизм создавал новую
            # строку на каждую правку): каждой неактивной версии проставляется ссылка
            # на активное продолжение её цепочки, если оно существует.
            cursor.execute("""
                WITH RECURSIVE chain AS (
                    SELECT id, previous_version_id, id AS head
                    FROM directions WHERE is_active = TRUE
                    UNION ALL
                    SELECT d.id, d.previous_version_id, chain.head
                    FROM directions d
                    JOIN chain ON chain.previous_version_id = d.id
                )
                UPDATE directions SET canonical_id = chain.head
                FROM chain
                WHERE directions.id = chain.id
                  AND chain.head <> chain.id
                  AND directions.canonical_id IS DISTINCT FROM chain.head;
            """)
            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE,
                    name VARCHAR(255) NOT NULL,
                    role VARCHAR(32) NOT NULL CHECK(role IN ('super_admin', 'admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee', 'hr_manager', 'accounting_manager', 'marketing_manager')),
                    hire_date DATE,
                    login VARCHAR(255) UNIQUE,
                    password_hash VARCHAR(255),
                    supervisor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL,
                    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
                    hours_table_url TEXT,
                    scores_table_url TEXT,
                    is_active BOOLEAN NOT NULL DEFAULT FALSE,
                    status VARCHAR(20) NOT NULL DEFAULT 'working' CHECK(status IN ('working', 'fired', 'unpaid_leave', 'bs', 'sick_leave', 'annual_leave', 'dismissal')),
                    rate DECIMAL(3,2) NOT NULL DEFAULT 1.00 CHECK(rate IN (1.00, 0.75, 0.50)),
                    gender VARCHAR(20) CHECK (gender IN ('male', 'female')),
                    birth_date DATE,
                    avatar_bucket VARCHAR(255),
                    avatar_blob_path TEXT,
                    avatar_original_blob_path TEXT,
                    avatar_content_type VARCHAR(128),
                    avatar_file_size INTEGER,
                    avatar_updated_at TIMESTAMP,
                    phone VARCHAR(50),
                    email VARCHAR(255),
                    personal_email VARCHAR(255),
                    instagram VARCHAR(255),
                    telegram_nick VARCHAR(255),
                    company_name VARCHAR(255),
                    employment_type VARCHAR(10) CHECK (employment_type IN ('gph', 'of', 'smz') OR employment_type IS NULL),
                    has_proxy BOOLEAN NOT NULL DEFAULT FALSE,
                    proxy_card_number VARCHAR(64),
                    proxy_status VARCHAR(20) CHECK (proxy_status IN ('lost', 'returned_to_hr', 'not_received', 'on_hand') OR proxy_status IS NULL),
                    has_driver_license BOOLEAN NOT NULL DEFAULT FALSE,
                    sip_number VARCHAR(64),
                    study_place VARCHAR(255),
                    study_specialty VARCHAR(255),
                    study_course VARCHAR(100),
                    study_completed BOOLEAN NOT NULL DEFAULT FALSE,
                    study_completion_year INTEGER,
                    close_contact_1_relation VARCHAR(100),
                    close_contact_1_full_name VARCHAR(255),
                    close_contact_1_phone VARCHAR(50),
                    close_contact_2_relation VARCHAR(100),
                    close_contact_2_full_name VARCHAR(255),
                    close_contact_2_phone VARCHAR(50),
                    card_number VARCHAR(64),
                    city VARCHAR(255),
                    job_title VARCHAR(255),
                    internship_in_company BOOLEAN NOT NULL DEFAULT FALSE,
                    front_office_training BOOLEAN NOT NULL DEFAULT FALSE,
                    front_office_training_date DATE,
                    taxipro_id VARCHAR(128),
                    feedback_telegram_report_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    CONSTRAINT unique_name_role UNIQUE (name, role)
                );
            """)
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS employment_type VARCHAR(10);
            """)
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS proxy_status VARCHAR(20)
                CHECK (proxy_status IN ('lost', 'returned_to_hr', 'not_received', 'on_hand') OR proxy_status IS NULL);
            """)
            cursor.execute("""
                ALTER TABLE users
                DROP CONSTRAINT IF EXISTS users_employment_type_check,
                DROP CONSTRAINT IF EXISTS users_proxy_status_check;
                ALTER TABLE users
                ADD CONSTRAINT users_employment_type_check
                    CHECK (employment_type IN ('gph', 'of', 'smz') OR employment_type IS NULL),
                ADD CONSTRAINT users_proxy_status_check
                    CHECK (proxy_status IN ('lost', 'returned_to_hr', 'not_received', 'on_hand') OR proxy_status IS NULL);
            """)
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS personal_email VARCHAR(255);
            """)
            # Город сотрудника: офисы отдела «Фронт офисы» стоят в разных городах,
            # поэтому город — часть кадровых данных (в карточке показываем только
            # отделам из EMPLOYEE_CITY_DEPARTMENT_CODES).
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS city VARCHAR(255);
            """)
            # Роли рядовых сотрудников бэк-офиса. CHECK на users.role
            # пересобираем целиком: ALTER ... ADD CONSTRAINT не умеет
            # «дополнить», а DROP IF EXISTS + ADD идемпотентен и повторный
            # старт переживает. Колонку заодно расширяем: 'accounting_manager'
            # — 18 символов, в прежние VARCHAR(20) влезало впритык.
            # 'marketing_manager' — 17 символов, в VARCHAR(32) укладывается с
            # запасом: условие ниже (character_maximum_length < 32) для него не
            # срабатывает и не должно, колонка уже расширена.
            # Расширение колонки — под условием, в отличие от соседних ALTER'ов:
            # у ALTER COLUMN ... TYPE нет формы IF NEEDED, а безусловный вызов
            # на каждом старте берёт ACCESS EXCLUSIVE и перестраивает индексы по
            # колонке. Сам CHECK пересоздаём как есть — на 355 строках проверка
            # стоит доли миллисекунды, и это тот же приём, что у CHECK'а
            # employment_type ниже.
            cursor.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                         WHERE table_name = 'users' AND column_name = 'role'
                           AND character_maximum_length < 32
                    ) THEN
                        ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(32);
                    END IF;
                END $$;
                ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
                ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN (
                    'super_admin', 'admin', 'sv', 'supervisor', 'trainer',
                    'operator', 'trainee', 'hr_manager', 'accounting_manager',
                    'marketing_manager'
                ));
            """)
            # Должность сотрудника: у бэк-офиса (Бухгалтерия, HR) человека
            # определяет не направление и не группа, а должность — в карточке
            # показываем только отделам из EMPLOYEE_JOB_TITLE_DEPARTMENTS.
            # Колонка job_title, а не position: position — ключевое слово
            # Postgres, а SQL здесь местами собирается строкой.
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS job_title VARCHAR(255);
            """)
            # Переключатель получения Telegram-отчёта о сменах ставок операторов
            # (для админов — глобальный отчёт, для глав отделов — по своему отделу).
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS rate_change_report_enabled BOOLEAN NOT NULL DEFAULT FALSE;
            """)
            # Наименование специальности (задача #279). Уточнение к «Месту учебы»,
            # поэтому отдельной таблицы не заводим: значение имеет смысл только
            # вместе с местом учёбы и хранится рядом с ним.
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS study_specialty VARCHAR(255);
            """)
            # Приватная фотолента "4 You". В таблице храним только ссылки на
            # оптимизированные варианты; сами изображения остаются в закрытом GCS.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS four_you_images (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    preview_bucket VARCHAR(255) NOT NULL,
                    preview_blob_path TEXT NOT NULL,
                    display_bucket VARCHAR(255) NOT NULL,
                    display_blob_path TEXT NOT NULL,
                    original_name VARCHAR(255) NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    preview_file_size INTEGER NOT NULL DEFAULT 0,
                    display_file_size INTEGER NOT NULL DEFAULT 0,
                    uploaded_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    sort_order BIGINT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_four_you_images_order
                ON four_you_images(sort_order, created_at, id);
            """)
            # Совместная разметка фото (рисунок/стикеры/текст/комментарии/фон).
            # Не-деструктивный JSONB-оверлей; реальные изображения не меняются.
            cursor.execute("""
                ALTER TABLE four_you_images
                ADD COLUMN IF NOT EXISTS annotations JSONB NOT NULL DEFAULT '{}'::jsonb;
            """)
            cursor.execute("""
                ALTER TABLE four_you_images
                ADD COLUMN IF NOT EXISTS annotations_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_four_you_images_ann_updated
                ON four_you_images(annotations_updated_at);
            """)
            # Бейдж новых фото 4 You в сайдбаре: на пользователя храним «последний
            # просмотр». Непрочитанные = фото, добавленные позже last_seen_at.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS four_you_reads (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # ──────────────────────────────────────────────────────────────
            # ИВЕНТЫ ("Ивенты"): общедоступная лента постов с медиа (фото/видео),
            # лайками и комментариями. Виден всем ролям. Таргетинг по отделам —
            # многие-ко-многим через event_departments: НЕТ строк => виден всем
            # отделам; есть строки => виден только перечисленным отделам.
            # (events.department_id — legacy single-target, больше не используется;
            #  оставлен для совместимости, данные перенесены в event_departments.)
            # Файлы лежат в закрытом GCS (как 4 You / LMS) — в таблицах только
            # bucket+blob_path, клиенту отдаём signed URL.
            # ──────────────────────────────────────────────────────────────
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id BIGSERIAL PRIMARY KEY,
                    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    department_id INTEGER REFERENCES departments(id) ON DELETE CASCADE,
                    title VARCHAR(255),
                    body TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            # Лента листается id-курсором (id монотонен по времени создания).
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_id_desc ON events(id DESC);
            """)
            # Закреплённый пост. Единственное, что перенесено из ленты новостей
            # вики: остальное (теги, вложения, отдельный статус прочтения) там
            # существовало, но за всё время не было использовано ни разу —
            # в дампе прода ноль новостей.
            #
            # Закреплённые НЕ участвуют в постраничной выдаче: лента листается
            # курсором по id, а пост, всплывший наверх, ломал бы курсор —
            # выпадал бы из выборки следующей страницы или дублировался.
            # Поэтому они отдаются отдельным списком к первой странице.
            cursor.execute("""
                ALTER TABLE events
                ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT FALSE;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_pinned
                ON events(id DESC) WHERE is_pinned;
            """)
            # Отделы-получатели поста (M:N). Пустой набор => пост виден всем.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_departments (
                    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
                    PRIMARY KEY (event_id, department_id)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_event_departments_dept
                ON event_departments(department_id, event_id);
            """)
            # Бэкфилл из legacy events.department_id (идемпотентно, разово по факту).
            cursor.execute("""
                INSERT INTO event_departments (event_id, department_id)
                SELECT id, department_id FROM events WHERE department_id IS NOT NULL
                ON CONFLICT DO NOTHING;
            """)
            # Медиа поста (несколько фото/видео, порядок sort_order). Для image:
            # blob_path = display-WebP, preview_* = preview-WebP. Для video:
            # blob_path = исходный mp4/webm, preview_* = WebP-постер (кадр).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_media (
                    id BIGSERIAL PRIMARY KEY,
                    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    media_type VARCHAR(16) NOT NULL CHECK (media_type IN ('image', 'video')),
                    bucket VARCHAR(255) NOT NULL,
                    blob_path TEXT NOT NULL,
                    preview_bucket VARCHAR(255),
                    preview_blob_path TEXT,
                    mime_type VARCHAR(100),
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    file_size BIGINT NOT NULL DEFAULT 0,
                    duration_seconds NUMERIC(10,2),
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_event_media_event
                ON event_media(event_id, sort_order, id);
            """)
            # Лайки: одна строка = один пользователь лайкнул пост.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_likes (
                    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    PRIMARY KEY (event_id, user_id)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_event_likes_user ON event_likes(user_id);
            """)
            # Комментарии (плоский список).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_comments (
                    id BIGSERIAL PRIMARY KEY,
                    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    body TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_event_comments_event
                ON event_comments(event_id, id);
            """)
            # Отметка "просмотрено" для бейджа новых постов в сайдбаре.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_reads (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    last_seen_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            # Дни рождения в колоколе: отметка «сегодняшних видел». Знак —
            # ДАТА, а не момент, в отличие от соседей выше: список именинников
            # меняется не по мере поступления, а целиком в полночь, и отметка
            # обязана истечь вместе с ним. Дату пишет процесс (Asia/Almaty),
            # а не база (UTC) — см. notifications/sources.py::mark_seen.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS birthday_reads (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    last_seen_on DATE NOT NULL
                );
            """)
            # ──────────────────────────────────────────────────────────────
            # ГРУППЫ: историческая принадлежность операторов и СВ по датам +
            # модель расчёта на уровне группы. Это источник истины принадлежности
            # вместо текущего users.supervisor_id/direction_id (те остаются как
            # legacy/cache). Одна группа = одна модель расчёта: задаётся при
            # создании группы и далее НЕ меняется (смена модели = архив группы и
            # создание/переиспользование другой). Создаём ДО work_hours/daily_hours,
            # т.к. ниже на них вешается FK group_id -> groups(id).
            # ──────────────────────────────────────────────────────────────
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
                    direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL,
                    calculation_model_code VARCHAR(32) NOT NULL DEFAULT 'operator',
                    table_url TEXT,
                    status VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
                    archived_at TIMESTAMP,
                    reused_from_group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_groups_status ON groups(status);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_groups_department ON groups(department_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_groups_direction ON groups(direction_id);")
            # Историческое членство супервайзеров в группе (0..1 активный СВ на дату).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_supervisor_memberships (
                    id SERIAL PRIMARY KEY,
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    supervisor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    start_date DATE NOT NULL,
                    end_date DATE,
                    assigned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_group_sv_memberships_group
                ON group_supervisor_memberships(group_id);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_group_sv_memberships_sv
                ON group_supervisor_memberships(supervisor_id, start_date);
            """)
            # Историческое членство операторов в группе по датам. Один оператор не
            # должен иметь две активные основные группы на один день (enforce —
            # частичный UNIQUE-индекс добавляется в фазе backfill, когда данные
            # гарантированно непротиворечивы).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_operator_memberships (
                    id SERIAL PRIMARY KEY,
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    start_date DATE NOT NULL,
                    end_date DATE,
                    assigned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_group_op_memberships_group
                ON group_operator_memberships(group_id);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_group_op_memberships_operator
                ON group_operator_memberships(operator_id, start_date);
            """)
            # Помесячный снимок группы (заморозка состояния за месяц).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_month_snapshots (
                    id SERIAL PRIMARY KEY,
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    month VARCHAR(7) NOT NULL,
                    name VARCHAR(255),
                    status VARCHAR(16),
                    direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL,
                    calculation_model_code VARCHAR(32) NOT NULL DEFAULT 'operator',
                    table_url TEXT,
                    archived BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(group_id, month)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_group_month_snapshots_month
                ON group_month_snapshots(month);
            """)
            # Помесячный снимок оператора в группе (замороженные расчётные поля:
            # role, status, rate, direction, модель, СВ). Поддерживает нахождение
            # оператора в двух группах в одном месяце с разными моделями.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_operator_month_snapshots (
                    id SERIAL PRIMARY KEY,
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    month VARCHAR(7) NOT NULL,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name VARCHAR(255),
                    role VARCHAR(20),
                    status VARCHAR(20),
                    direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL,
                    direction_name VARCHAR(255),
                    calculation_model_code VARCHAR(32) NOT NULL DEFAULT 'operator',
                    rate DECIMAL(3,2),
                    supervisor_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    supervisor_names JSONB NOT NULL DEFAULT '[]'::jsonb,
                    first_day DATE,
                    last_day DATE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(group_id, month, operator_id)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_group_op_month_snapshots_op_month
                ON group_operator_month_snapshots(operator_id, month);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_group_op_month_snapshots_group_month
                ON group_operator_month_snapshots(group_id, month);
            """)
            # Этап 4 (снимок месяца): числовые агрегаты по сегменту группы + метка заморозки.
            # Аддитивно (ADD COLUMN IF NOT EXISTS) — старые строки получают NULL, новые пишет
            # _freeze_month_to_snapshots_tx. Наличие frozen_at у строк месяца = месяц заморожен.
            for _snap_col, _snap_type in (
                ("norm_hours", "DOUBLE PRECISION"),
                ("regular_hours", "DOUBLE PRECISION"),
                ("training_hours", "DOUBLE PRECISION"),
                ("total_break_time", "DOUBLE PRECISION"),
                ("total_talk_time", "DOUBLE PRECISION"),
                ("total_dial_time", "DOUBLE PRECISION"),
                ("total_calls", "INTEGER"),
                ("total_chats", "INTEGER"),
                ("total_efficiency_hours", "DOUBLE PRECISION"),
                ("calls_per_hour", "DOUBLE PRECISION"),
                ("fines", "DOUBLE PRECISION"),
                ("offline_activity_hours", "DOUBLE PRECISION"),
                ("no_phone_hours", "DOUBLE PRECISION"),
                ("chat_avg_score", "DOUBLE PRECISION"),
                ("chat_avg_response_time_seconds", "DOUBLE PRECISION"),
                ("chat_transfer_count", "INTEGER"),
                ("frozen_at", "TIMESTAMP"),
            ):
                cursor.execute(
                    "ALTER TABLE group_operator_month_snapshots ADD COLUMN IF NOT EXISTS %s %s" % (_snap_col, _snap_type)
                )
            cursor.execute(
                "ALTER TABLE group_month_snapshots ADD COLUMN IF NOT EXISTS frozen_at TIMESTAMP"
            )
            # Модель расчёта у группы необязательна: группу отдела, где по моделям
            # ничего не считают (бэк-офис), заводят без неё. NULL = своей модели у
            # группы нет, и сотрудник считается по модели своего направления — той
            # же лестницей, что и без членства (_load_operator_calculation_models_tx).
            # Снимок месяца хранит группу «как была», поэтому NULL допустим и в нём.
            # Дефолт 'operator' остаётся: строка, вставленная без этой колонки,
            # ведёт себя как раньше.
            # NOT NULL снимаем под условием — тот же приём, что у users.role выше:
            # безусловный ALTER на каждом старте брал бы ACCESS EXCLUSIVE на groups,
            # пока прежний инстанс ещё читает группы почти в каждом запросе.
            cursor.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                         WHERE table_schema = current_schema()
                           AND table_name = 'groups' AND column_name = 'calculation_model_code'
                           AND is_nullable = 'NO'
                    ) THEN
                        ALTER TABLE groups ALTER COLUMN calculation_model_code DROP NOT NULL;
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                         WHERE table_schema = current_schema()
                           AND table_name = 'group_month_snapshots' AND column_name = 'calculation_model_code'
                           AND is_nullable = 'NO'
                    ) THEN
                        ALTER TABLE group_month_snapshots ALTER COLUMN calculation_model_code DROP NOT NULL;
                    END IF;
                END $$;
            """)
            # Журнал смены модели расчёта группы. Каждая запись = одно изменение
            # (old -> new), позволяет откатить случайную смену модели без потери
            # данных: сырьё (daily_hours/метрики чатов) и замороженные снимки
            # месяцев не трогаются сменой модели, поэтому откат полностью обратим.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_model_change_log (
                    id SERIAL PRIMARY KEY,
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    old_model_code VARCHAR(32),
                    new_model_code VARCHAR(32) NOT NULL,
                    changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    is_revert BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_group_model_log_group "
                "ON group_model_change_log(group_id, id DESC);"
            )
            # Calls table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_activity_logs (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    is_active VARCHAR(20) NOT NULL,
                    change_time TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calls (
                    id SERIAL PRIMARY KEY,
                    evaluator_id INTEGER NOT NULL REFERENCES users(id),
                    operator_id INTEGER NOT NULL REFERENCES users(id),
                    month VARCHAR(7) NOT NULL DEFAULT TO_CHAR(CURRENT_DATE, 'YYYY-MM'),
                    phone_number VARCHAR(255) NOT NULL,
                    appeal_date TIMESTAMP,
                    score FLOAT NOT NULL,
                    comment TEXT,
                    audio_path TEXT,
                    is_draft BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_correction BOOLEAN DEFAULT FALSE,
                    previous_version_id INTEGER REFERENCES calls(id),
                    scores JSONB,
                    criterion_comments JSONB,
                    direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL,
                    comment_visible_to_operator BOOLEAN NOT NULL DEFAULT TRUE,
                    question_resolved BOOLEAN NOT NULL DEFAULT FALSE,
                    resolved_first_contact BOOLEAN,
                    sv_request BOOLEAN NOT NULL DEFAULT FALSE,
                    sv_request_comment TEXT,
                    sv_request_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    sv_request_by_role VARCHAR(20),
                    sv_request_at TIMESTAMP,
                    sv_request_approved BOOLEAN NOT NULL DEFAULT FALSE,
                    sv_request_approved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    sv_request_approved_at TIMESTAMP,
                    sv_request_rejected BOOLEAN NOT NULL DEFAULT FALSE,
                    sv_request_rejected_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    sv_request_rejected_at TIMESTAMP,
                    sv_request_reject_comment TEXT,
                    sv_request_approve_comment TEXT,
                    call_end_party VARCHAR(16),
                    UNIQUE(evaluator_id, operator_id, month, phone_number, appeal_date, score, comment, is_draft)
                );
            """)

            # Идемпотентная миграция для существующих БД (таблица создаётся через
            # CREATE TABLE IF NOT EXISTS, поэтому новые колонки нужно добавлять отдельно).
            cursor.execute("""
                ALTER TABLE calls
                ADD COLUMN IF NOT EXISTS sv_request_approve_comment TEXT;
            """)
            cursor.execute("""
                ALTER TABLE calls
                ADD COLUMN IF NOT EXISTS call_end_party VARCHAR(16);
            """)

            # Идемпотентная миграция уникального ключа оценок: добавляем appeal_date,
            # чтобы один и тот же номер за месяц можно было оценить по разным
            # обращениям (разная дата обращения => разные оценки).
            # Старое ограничение имеет авто-сгенерированное имя, поэтому ищем его по
            # точному набору колонок и удаляем, затем создаём новое именованное.
            cursor.execute("""
                DO $$
                DECLARE
                    old_con text;
                BEGIN
                    SELECT con.conname INTO old_con
                    FROM pg_constraint con
                    WHERE con.conrelid = 'calls'::regclass
                      AND con.contype = 'u'
                      AND (
                          SELECT array_agg(a.attname ORDER BY a.attname)
                          FROM unnest(con.conkey) AS k
                          JOIN pg_attribute a
                            ON a.attrelid = con.conrelid AND a.attnum = k
                      ) = ARRAY['comment','evaluator_id','is_draft','month','operator_id','phone_number','score']::name[];

                    IF old_con IS NOT NULL THEN
                        EXECUTE format('ALTER TABLE calls DROP CONSTRAINT %I', old_con);
                    END IF;

                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conrelid = 'calls'::regclass
                          AND conname = 'calls_unique_eval_with_appeal_date'
                    ) THEN
                        ALTER TABLE calls
                        ADD CONSTRAINT calls_unique_eval_with_appeal_date
                        UNIQUE (evaluator_id, operator_id, month, phone_number, appeal_date, score, comment, is_draft);
                    END IF;
                END $$;
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS imported_calls (
                    id SERIAL PRIMARY KEY,
                    external_id TEXT,
                    operator_name TEXT NOT NULL,
                    operator_id INTEGER,
                    month VARCHAR(7) NOT NULL,
                    datetime_raw TIMESTAMP WITH TIME ZONE,
                    phone_number TEXT,
                    phone_normalized TEXT,
                    duration_sec DOUBLE PRECISION,
                    desired INTEGER,
                    available INTEGER,
                    status VARCHAR(20) NOT NULL DEFAULT 'not_evaluated',
                    imported_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    evaluated_at TIMESTAMP WITH TIME ZONE,
                    evaluated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    notes TEXT,
                    call_end_party VARCHAR(16),
                    call_end_party_checked_at TIMESTAMP WITH TIME ZONE
                );
                ALTER TABLE imported_calls ADD COLUMN IF NOT EXISTS audio_path TEXT;
                ALTER TABLE imported_calls ADD COLUMN IF NOT EXISTS call_end_party VARCHAR(16);
                ALTER TABLE imported_calls ADD COLUMN IF NOT EXISTS call_end_party_checked_at TIMESTAMP WITH TIME ZONE;
                -- Шкала ИИ-оценки, заданная самому звонку (call_qa.subjects.scale_direction_id):
                -- звонки обзвона оцениваются по своей мониторинговой шкале, а оператор остаётся
                -- в основном направлении — видимость и отдел по-прежнему считаются по нему.
                -- NULL — шкала оператора, как было всегда.
                ALTER TABLE imported_calls ADD COLUMN IF NOT EXISTS qa_direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL;
                -- Направление, которому принадлежит САМ разговор, когда это не направление
                -- оператора (call_qa.api._SUBJECT_DIRECTION): в базу удалённого КЦ звонят
                -- фронт-офисы, удалённые операторы и СЗоВ, а разговор — работа направления
                -- «Обзвон — подписание АВР» (задача SZOV-2). По нему считаются отдел,
                -- видимость и место в сводке. NULL — направление оператора, как было всегда.
                ALTER TABLE imported_calls ADD COLUMN IF NOT EXISTS scope_direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS uq_imported_calls_external_id_month ON imported_calls (external_id, month);
                CREATE INDEX IF NOT EXISTS idx_imported_calls_month ON imported_calls(month);
                CREATE INDEX IF NOT EXISTS idx_imported_calls_operator_id ON imported_calls(operator_id);
                CREATE INDEX IF NOT EXISTS idx_imported_calls_status ON imported_calls(status);
                CREATE INDEX IF NOT EXISTS idx_imported_calls_phone_normalized ON imported_calls(phone_normalized);
                CREATE INDEX IF NOT EXISTS idx_imported_calls_eval_match
                    ON imported_calls(operator_id, month, phone_normalized, datetime_raw);
                CREATE INDEX IF NOT EXISTS idx_imported_calls_end_party_backfill
                    ON imported_calls(call_end_party_checked_at, external_id)
                    WHERE call_end_party = 'unknown';
                -- Частичный: размечена малая доля звонков, а спрашивают «есть ли у
                -- направления размеченные звонки» (скоуп СВ, справочник фильтров).
                CREATE INDEX IF NOT EXISTS idx_imported_calls_qa_direction
                    ON imported_calls(qa_direction_id)
                    WHERE qa_direction_id IS NOT NULL;
            """)
            # Старые записи imported_calls — это телефонные звонки. До появления этого поля
            # источник не сохранялся, поэтому сначала честно отмечаем их как неизвестные;
            # фоновая синхронизация Oktell затем заменит значение на точное, где это возможно.
            cursor.execute("""
                UPDATE imported_calls
                SET call_end_party = 'unknown'
                WHERE call_end_party IS NULL;
            """)
            cursor.execute("""
                ALTER TABLE calls
                ADD COLUMN IF NOT EXISTS imported_call_id INTEGER;
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conrelid = 'calls'::regclass
                          AND conname = 'calls_imported_call_id_fkey'
                    ) THEN
                        ALTER TABLE calls
                        ADD CONSTRAINT calls_imported_call_id_fkey
                        FOREIGN KEY (imported_call_id)
                        REFERENCES imported_calls(id)
                        ON DELETE SET NULL;
                    END IF;
                END $$;
                CREATE INDEX IF NOT EXISTS idx_calls_imported_call_id
                    ON calls(imported_call_id);
            """)
            # Однократная совместимость для оценок, созданных до явной связи с imported_calls.
            # Не связываем неоднозначные исторические дубли с произвольной записью.
            cursor.execute("""
                UPDATE calls AS c
                SET imported_call_id = (
                    SELECT ic.id
                    FROM imported_calls AS ic
                    WHERE ic.operator_id = c.operator_id
                      AND ic.month = c.month
                      AND ic.phone_normalized = regexp_replace(c.phone_number, '[^0-9]', '', 'g')
                      AND ic.datetime_raw AT TIME ZONE 'UTC' = c.appeal_date
                )
                WHERE c.imported_call_id IS NULL
                  AND c.appeal_date IS NOT NULL
                  AND 1 = (
                      SELECT COUNT(*)
                      FROM imported_calls AS ic
                      WHERE ic.operator_id = c.operator_id
                        AND ic.month = c.month
                        AND ic.phone_normalized = regexp_replace(c.phone_number, '[^0-9]', '', 'g')
                        AND ic.datetime_raw AT TIME ZONE 'UTC' = c.appeal_date
                  );
            """)
            cursor.execute("""
                UPDATE calls AS c
                SET call_end_party = ic.call_end_party
                FROM imported_calls AS ic
                WHERE c.imported_call_id = ic.id
                  AND (c.call_end_party IS NULL OR c.call_end_party = 'unknown')
                  AND ic.call_end_party IS NOT NULL;
            """)
            cursor.execute("""
                UPDATE calls AS c
                SET call_end_party = 'unknown'
                WHERE c.call_end_party IS NULL
                  AND c.appeal_date IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM imported_calls AS ic
                      WHERE ic.operator_id = c.operator_id
                        AND ic.month = c.month
                        AND ic.phone_normalized = regexp_replace(c.phone_number, '[^0-9]', '', 'g')
                        AND ic.datetime_raw AT TIME ZONE 'UTC' = c.appeal_date
                  );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calibration_rooms (
                    id SERIAL PRIMARY KEY,
                    created_by_admin_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    operator_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    month VARCHAR(7) NOT NULL,
                    phone_number VARCHAR(255),
                    appeal_date TIMESTAMP,
                    direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL,
                    score FLOAT NOT NULL DEFAULT 0,
                    comment TEXT,
                    audio_path TEXT,
                    scores JSONB NOT NULL DEFAULT '[]',
                    criterion_comments JSONB NOT NULL DEFAULT '[]',
                    room_title TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calibration_room_members (
                    id SERIAL PRIMARY KEY,
                    room_id INTEGER NOT NULL REFERENCES calibration_rooms(id) ON DELETE CASCADE,
                    supervisor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(room_id, supervisor_id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calibration_room_calls (
                    id SERIAL PRIMARY KEY,
                    room_id INTEGER NOT NULL REFERENCES calibration_rooms(id) ON DELETE CASCADE,
                    created_by_admin_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    month VARCHAR(7) NOT NULL,
                    phone_number VARCHAR(255) NOT NULL,
                    appeal_date TIMESTAMP,
                    direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL,
                    score FLOAT NOT NULL DEFAULT 0,
                    comment TEXT,
                    audio_path TEXT,
                    scores JSONB NOT NULL DEFAULT '[]',
                    criterion_comments JSONB NOT NULL DEFAULT '[]',
                    etalon_scores JSONB NOT NULL DEFAULT '[]',
                    etalon_criterion_comments JSONB NOT NULL DEFAULT '[]',
                    etalon_updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    etalon_updated_at TIMESTAMP,
                    general_comment TEXT,
                    general_comment_updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    general_comment_updated_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calibration_room_call_evaluations (
                    id SERIAL PRIMARY KEY,
                    room_call_id INTEGER NOT NULL REFERENCES calibration_room_calls(id) ON DELETE CASCADE,
                    evaluator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    score FLOAT NOT NULL DEFAULT 0,
                    comment TEXT,
                    scores JSONB NOT NULL DEFAULT '[]',
                    criterion_comments JSONB NOT NULL DEFAULT '[]',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(room_call_id, evaluator_id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calibration_room_evaluations (
                    id SERIAL PRIMARY KEY,
                    room_id INTEGER NOT NULL REFERENCES calibration_rooms(id) ON DELETE CASCADE,
                    evaluator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    score FLOAT NOT NULL DEFAULT 0,
                    comment TEXT,
                    scores JSONB NOT NULL DEFAULT '[]',
                    criterion_comments JSONB NOT NULL DEFAULT '[]',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(room_id, evaluator_id)
                );
            """)


            #Trainings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trainings (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    training_date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    reason VARCHAR(255) NOT NULL CHECK(reason IN (
                        'Обратная связь', 'Собрание', 'Тех. сбой', 'Мотивационная беседа', 
                        'Дисциплинарный тренинг', 'Тренинг по качеству. Разбор ошибок', 
                        'Тренинг по качеству. Объяснение МШ', 'Тренинг по продукту', 
                        'Мониторинг', 'Практика в офисе таксопарка', 'Другое'
                    )),
                    comment TEXT,
                    created_by INTEGER REFERENCES users(id),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    count_in_hours BOOLEAN NOT NULL DEFAULT TRUE,
                    UNIQUE(operator_id, training_date, start_time, end_time)
                );
            """)
            # Поинтервальное отклонение авто-тренингов: каждая запись — это конкретный
            # интервал статус-тренинга, который проверяющий отметил как «не тренинг».
            # Отклонение необратимо (повторно подтвердить интервал нельзя) и не влияет
            # на расчёт часов — служит только для построчного статуса в модалке дня.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS training_interval_rejections (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    rejection_date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    comment TEXT,
                    created_by INTEGER REFERENCES users(id),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, rejection_date, start_time, end_time)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS call_feedbacks (
                    id SERIAL PRIMARY KEY,
                    call_id INTEGER NOT NULL UNIQUE REFERENCES calls(id) ON DELETE CASCADE,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    supervisor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    training_id INTEGER REFERENCES trainings(id) ON DELETE SET NULL,
                    feedback_comment TEXT NOT NULL,
                    delivery_comment TEXT NOT NULL,
                    feedback_date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Allow several call feedbacks to share ONE training (batch feedback:
            # multiple evaluations of the same operator reviewed in a single training
            # session). Drop the legacy 1:1 UNIQUE constraint on training_id.
            cursor.execute("ALTER TABLE call_feedbacks DROP CONSTRAINT IF EXISTS call_feedbacks_training_id_key;")

            # Work hours table with fines column
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS work_hours (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    operator_id INTEGER NOT NULL REFERENCES users(id),
                    month VARCHAR(7) NOT NULL DEFAULT TO_CHAR(CURRENT_DATE, 'YYYY-MM'),
                    regular_hours FLOAT NOT NULL DEFAULT 0,    
                    training_hours FLOAT NOT NULL DEFAULT 0,
                    fines FLOAT NOT NULL DEFAULT 0,
                    norm_hours FLOAT NOT NULL DEFAULT 0,
                    total_break_time FLOAT NOT NULL DEFAULT 0,
                    total_talk_time FLOAT NOT NULL DEFAULT 0,
                    total_calls INTEGER NOT NULL DEFAULT 0,
                    total_efficiency_hours FLOAT NOT NULL DEFAULT 0, 
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    calls_per_hour FLOAT NOT NULL DEFAULT 0.0,
                    UNIQUE(operator_id, month)
                );
            """)

            # Помесячная ставка оператора. NULL = ставка не задана явно за месяц
            # (на чтении подхватывается переносом с прошлых месяцев / из users.rate).
            cursor.execute("""
                ALTER TABLE work_hours
                ADD COLUMN IF NOT EXISTS rate DECIMAL(3,2);
            """)
            cursor.execute("""
                ALTER TABLE work_hours
                DROP CONSTRAINT IF EXISTS work_hours_rate_check;
                ALTER TABLE work_hours
                ADD CONSTRAINT work_hours_rate_check
                    CHECK (rate IS NULL OR rate IN (1.00, 0.75, 0.50));
            """)
            # group_id: историческая привязка месячных часов к группе. Совместимая
            # миграция — пока nullable + backfill; смена UNIQUE(operator_id, month)
            # на (group_id, operator_id, month) делается в фазе обновления upsert-кода.
            cursor.execute("""
                ALTER TABLE work_hours
                ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_work_hours_group_month
                ON work_hours(group_id, operator_id, month);
            """)

            cursor.execute("""
                CREATE EXTENSION IF NOT EXISTS pgcrypto;
                CREATE TABLE IF NOT EXISTS daily_hours (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    day DATE NOT NULL,
                    work_time FLOAT NOT NULL DEFAULT 0,
                    break_time FLOAT NOT NULL DEFAULT 0,
                    talk_time FLOAT NOT NULL DEFAULT 0,
                    calls INTEGER NOT NULL DEFAULT 0,
                    efficiency FLOAT NOT NULL DEFAULT 0,
                    fine_amount FLOAT NOT NULL DEFAULT 0,
                    fine_reason VARCHAR(64),
                    fine_comment TEXT,
                    training_time FLOAT NOT NULL DEFAULT 0,
                    late_minutes INTEGER NOT NULL DEFAULT 0,
                    late_seconds INTEGER NOT NULL DEFAULT 0,
                    early_leave_minutes INTEGER NOT NULL DEFAULT 0,
                    early_leave_seconds INTEGER NOT NULL DEFAULT 0,
                    overtime_minutes INTEGER NOT NULL DEFAULT 0,
                    overtime_seconds INTEGER NOT NULL DEFAULT 0,
                    training_minutes INTEGER NOT NULL DEFAULT 0,
                    training_seconds INTEGER NOT NULL DEFAULT 0,
                    technical_reason_minutes INTEGER NOT NULL DEFAULT 0,
                    technical_reason_seconds INTEGER NOT NULL DEFAULT 0,
                    no_phone_minutes INTEGER NOT NULL DEFAULT 0,
                    no_phone_seconds INTEGER NOT NULL DEFAULT 0,
                    late_status VARCHAR(16),
                    early_leave_status VARCHAR(16),
                    training_status VARCHAR(16),
                    technical_reason_status VARCHAR(16),
                    late_fine_id INTEGER,
                    early_leave_fine_id INTEGER,
                    training_fine_id INTEGER,
                    technical_reason_fine_id INTEGER,
                    auto_aggregated BOOLEAN NOT NULL DEFAULT FALSE,
                    auto_aggregated_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, day)
                );
            """)
            # NOTE: idx_daily_hours_operator_day was a byte-for-byte duplicate of the
            # UNIQUE(operator_id, day) constraint index above — same columns, same order.
            # The constraint index serves every query the duplicate did, so dropping it
            # cannot change a plan. Do not re-add.
            cursor.execute("DROP INDEX IF EXISTS idx_daily_hours_operator_day;")
            cursor.execute("""
                ALTER TABLE daily_hours
                ADD COLUMN IF NOT EXISTS no_phone_minutes INTEGER NOT NULL DEFAULT 0;
            """)
            cursor.execute("""
                ALTER TABLE daily_hours
                ADD COLUMN IF NOT EXISTS no_phone_seconds INTEGER NOT NULL DEFAULT 0;
            """)
            # group_id: историческая привязка дневных часов к группе. Совместимая
            # миграция — пока nullable + backfill; смена UNIQUE(operator_id, day) на
            # (group_id, operator_id, day) делается в фазе обновления upsert-кода
            # (иначе сломается существующий ON CONFLICT (operator_id, day)).
            # extra_metrics: мешок значений для новых декларативных метрик модели,
            # чтобы добавление метрики не требовало новой колонки.
            cursor.execute("""
                ALTER TABLE daily_hours
                ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL;
            """)
            cursor.execute("""
                ALTER TABLE daily_hours
                ADD COLUMN IF NOT EXISTS extra_metrics JSONB NOT NULL DEFAULT '{}'::jsonb;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_hours_group_day
                ON daily_hours(group_id, operator_id, day);
            """)

            # Table for multiple fines per day (new schema)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_fines (
                    id SERIAL PRIMARY KEY,
                    daily_hours_id UUID REFERENCES daily_hours(id) ON DELETE CASCADE,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    day DATE NOT NULL,
                    amount FLOAT NOT NULL DEFAULT 0,
                    reason VARCHAR(64),
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_bonuses (
                    id SERIAL PRIMARY KEY,
                    daily_hours_id UUID REFERENCES daily_hours(id) ON DELETE CASCADE,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    day DATE NOT NULL,
                    bonus_type VARCHAR(64) NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    training_hours FLOAT,
                    amount FLOAT NOT NULL DEFAULT 0,
                    friend_names TEXT,
                    video_links TEXT,
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_fines_operator_day
                ON daily_fines(operator_id, day);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_fines_daily_hours_id
                ON daily_fines(daily_hours_id);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_bonuses_operator_day
                ON daily_bonuses(operator_id, day);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_bonuses_daily_hours_id
                ON daily_bonuses(daily_hours_id);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_manager_daily_metrics (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    day DATE NOT NULL,
                    chats_count INTEGER NOT NULL DEFAULT 0,
                    avg_score FLOAT,
                    avg_response_time_seconds FLOAT,
                    transfer_chat_count INTEGER NOT NULL DEFAULT 0,
                    imported_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    source_batch_id UUID,
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, day)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_manager_daily_metrics_operator_day
                ON chat_manager_daily_metrics(operator_id, day);
            """)
            # Сумма баллов и количество оценок за день: чтобы среднюю оценку считать ВЗВЕШЕННО
            # (сумма/количество за период), а не «среднее средних». avg_score продолжаем хранить
            # как удобное кэш-значение (= sum/count при импорте, либо ручной ввод из модалки).
            cursor.execute("""
                ALTER TABLE chat_manager_daily_metrics
                ADD COLUMN IF NOT EXISTS score_sum FLOAT;
            """)
            cursor.execute("""
                ALTER TABLE chat_manager_daily_metrics
                ADD COLUMN IF NOT EXISTS score_count INTEGER;
            """)
            cursor.execute("""
                ALTER TABLE chat_manager_daily_metrics
                ADD COLUMN IF NOT EXISTS whatsapp_chats_count INTEGER NOT NULL DEFAULT 0;
            """)
            cursor.execute("""
                ALTER TABLE chat_manager_daily_metrics
                ADD COLUMN IF NOT EXISTS name_requests_chats_count INTEGER NOT NULL DEFAULT 0;
            """)
            cursor.execute("""
                ALTER TABLE chat_manager_daily_metrics
                ADD COLUMN IF NOT EXISTS raw_score_sum FLOAT;
            """)
            cursor.execute("""
                ALTER TABLE chat_manager_daily_metrics
                ADD COLUMN IF NOT EXISTS raw_score_count INTEGER;
            """)
            cursor.execute("""
                UPDATE chat_manager_daily_metrics
                SET raw_score_sum = score_sum
                WHERE raw_score_sum IS NULL AND score_sum IS NOT NULL;
            """)
            cursor.execute("""
                UPDATE chat_manager_daily_metrics
                SET raw_score_count = score_count
                WHERE raw_score_count IS NULL AND score_count IS NOT NULL;
            """)
            # Месячный план для модели TEZ ОП: цель (план успешек/сделок) и факт
            # вносит СВ/глава отдела вручную; % выполнения = факт/цель участвует в ЗП.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_monthly_plans (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    plan_target NUMERIC(12,2) NOT NULL DEFAULT 0,
                    plan_fact NUMERIC(12,2) NOT NULL DEFAULT 0,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, year, month)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_operator_monthly_plans_operator_period
                ON operator_monthly_plans(operator_id, year, month);
            """)
            # Общий (одинаковый для всех) месячный план направления/отдела TEZ ОП:
            # «план успешек на 1 FTE». Вносит СВ/глава в учёте часов; индивидуальный
            # план = plan_per_fte / norm_hours_fte × фактически учтённые часы.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS department_monthly_plans (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    plan_per_fte NUMERIC(12,2) NOT NULL DEFAULT 0,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(department_id, year, month)
                );
            """)
            cursor.execute("""
                ALTER TABLE department_monthly_plans
                ADD COLUMN IF NOT EXISTS norm_hours_fte NUMERIC(8,2)
                    CHECK (norm_hours_fte > 0 AND norm_hours_fte <= 744);
                CREATE TABLE IF NOT EXISTS tez_department_plan_snapshots (
                    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
                    month_start DATE NOT NULL,
                    fte_total NUMERIC(12,4) NOT NULL CHECK (fte_total >= 0),
                    operators_count INTEGER NOT NULL CHECK (operators_count >= 0),
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (department_id, month_start)
                );
                ALTER TABLE tez_department_plan_snapshots
                ADD COLUMN IF NOT EXISTS calculation_version INTEGER NOT NULL DEFAULT 1;
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_manager_low_rating_reviews (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    source TEXT NOT NULL DEFAULT 'chat2desk',
                    source_key TEXT NOT NULL,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    operator_name TEXT NOT NULL DEFAULT '',
                    phone_number TEXT NOT NULL DEFAULT '',
                    phone_normalized TEXT NOT NULL DEFAULT '',
                    taxi_park TEXT NOT NULL DEFAULT '',
                    rated_at TIMESTAMP,
                    day DATE NOT NULL,
                    score FLOAT NOT NULL,
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    imported_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    source_batch_id UUID,
                    arai_status TEXT,
                    arai_comment TEXT NOT NULL DEFAULT '',
                    arai_updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    arai_updated_at TIMESTAMP,
                    zhanna_status TEXT,
                    zhanna_comment TEXT NOT NULL DEFAULT '',
                    zhanna_updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    zhanna_updated_at TIMESTAMP,
                    head_status TEXT,
                    head_comment TEXT NOT NULL DEFAULT '',
                    head_updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    head_updated_at TIMESTAMP,
                    final_status TEXT,
                    final_source TEXT,
                    final_decided_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    final_decided_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source, source_key)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_low_rating_reviews_operator_day
                ON chat_manager_low_rating_reviews(operator_id, day);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_low_rating_reviews_day
                ON chat_manager_low_rating_reviews(day);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_low_rating_reviews_final_status
                ON chat_manager_low_rating_reviews(final_status);
            """)
            cursor.execute("""
                ALTER TABLE chat_manager_low_rating_reviews
                ADD COLUMN IF NOT EXISTS final_comment TEXT NOT NULL DEFAULT '';
            """)
            cursor.execute("""
                UPDATE chat_manager_low_rating_reviews
                SET final_comment = COALESCE(NULLIF(head_comment, ''), final_comment)
                WHERE final_comment = '' AND head_comment <> '';
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_manager_low_rating_review_entries (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    review_id UUID NOT NULL REFERENCES chat_manager_low_rating_reviews(id) ON DELETE CASCADE,
                    reviewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    status TEXT,
                    comment TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(review_id, reviewer_id)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_low_rating_review_entries_review
                ON chat_manager_low_rating_review_entries(review_id);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_low_rating_review_entries_reviewer
                ON chat_manager_low_rating_review_entries(reviewer_id);
            """)
            cursor.execute("""
                INSERT INTO chat_manager_low_rating_review_entries (
                    review_id, reviewer_id, status, comment, created_at, updated_at
                )
                SELECT id, arai_updated_by, arai_status, arai_comment,
                       COALESCE(arai_updated_at, updated_at, CURRENT_TIMESTAMP),
                       COALESCE(arai_updated_at, updated_at, CURRENT_TIMESTAMP)
                FROM chat_manager_low_rating_reviews
                WHERE arai_updated_by IS NOT NULL AND arai_status IS NOT NULL
                ON CONFLICT (review_id, reviewer_id) DO NOTHING;
            """)
            cursor.execute("""
                INSERT INTO chat_manager_low_rating_review_entries (
                    review_id, reviewer_id, status, comment, created_at, updated_at
                )
                SELECT id, zhanna_updated_by, zhanna_status, zhanna_comment,
                       COALESCE(zhanna_updated_at, updated_at, CURRENT_TIMESTAMP),
                       COALESCE(zhanna_updated_at, updated_at, CURRENT_TIMESTAMP)
                FROM chat_manager_low_rating_reviews
                WHERE zhanna_updated_by IS NOT NULL AND zhanna_status IS NOT NULL
                ON CONFLICT (review_id, reviewer_id) DO NOTHING;
            """)
            # Кому засчитана низкая оценка (задача #286). Chat2Desk отдаёт оценку
            # тому, кто закрыл чат, а вести его могли несколько менеджеров. NULL —
            # оценка на том, кому её отдал Chat2Desk (operator_id); массив — решение
            # проверяющих. Обоснованная засчитывается КАЖДОМУ из списка целиком, а
            # не делится, и действует только при итоговом «обоснованно».
            cursor.execute("""
                ALTER TABLE chat_manager_low_rating_reviews
                ADD COLUMN IF NOT EXISTS attributed_operator_ids INTEGER[];
            """)
            # Журнал разбора: кто и когда менял вердикт и атрибуцию. Текущие голоса
            # живут в review_entries и перезаписываются, поэтому история — отдельно
            # и только пополняется. comment = NULL — комментарий не менялся;
            # operator_ids = NULL — атрибуция не менялась.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_manager_low_rating_review_history (
                    id BIGSERIAL PRIMARY KEY,
                    review_id UUID NOT NULL REFERENCES chat_manager_low_rating_reviews(id) ON DELETE CASCADE,
                    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    action TEXT NOT NULL,
                    status TEXT,
                    prev_status TEXT,
                    comment TEXT,
                    operator_ids INTEGER[],
                    prev_operator_ids INTEGER[],
                    final_status TEXT,
                    prev_final_status TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_low_rating_review_history_review
                ON chat_manager_low_rating_review_history(review_id, created_at);
            """)
            self._migrate_chat_low_rating_duplicate_keys(cursor)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_metric_surge_windows (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    month DATE NOT NULL,
                    start_at TIMESTAMP NOT NULL,
                    end_at TIMESTAMP NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_metric_surge_windows_month
                ON chat_metric_surge_windows(month, start_at);
            """)
            # Wazzup (Верификаторы): сырые сообщения из вебхука messagesAndStatuses.
            # Ретеншн 45 дней (см. cleanup_wazzup_messages) — таблица не растёт бесконечно.
            # Полезная нагрузка вебхука разобрана по типизированным колонкам; целиком
            # (колонка raw JSONB) она больше не хранится — это было 60% веса таблицы
            # при нулевом числе читателей.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wazzup_messages (
                    message_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    chat_type TEXT,
                    chat_id TEXT NOT NULL,
                    dt TIMESTAMPTZ NOT NULL,
                    is_echo BOOLEAN NOT NULL DEFAULT FALSE,
                    type TEXT,
                    text TEXT,
                    content_uri TEXT,
                    author_name TEXT,
                    author_id TEXT,
                    contact_name TEXT,
                    contact_phone TEXT,
                    status TEXT,
                    sent_from_app BOOLEAN,
                    is_edited BOOLEAN NOT NULL DEFAULT FALSE,
                    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            # Снос raw: колонка дублировала уже разобранные поля и не имела читателей.
            cursor.execute("ALTER TABLE wazzup_messages DROP COLUMN IF EXISTS raw;")
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_wazzup_messages_chat
                ON wazzup_messages(channel_id, chat_id, dt);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_wazzup_messages_dt
                ON wazzup_messages(dt);
            """)
            # Сводка по чату для списка «как в мессенджере»: одна строка на
            # (канал, чат), обновляется при вставке сообщения — список чатов
            # не сканирует wazzup_messages.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wazzup_chats (
                    channel_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    chat_type TEXT,
                    contact_name TEXT,
                    contact_phone TEXT,
                    last_message_at TIMESTAMPTZ,
                    last_message_text TEXT,
                    last_message_is_echo BOOLEAN,
                    messages_count INTEGER NOT NULL DEFAULT 0,
                    inbound_count INTEGER NOT NULL DEFAULT 0,
                    outbound_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (channel_id, chat_id)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_wazzup_chats_last_message
                ON wazzup_chats(last_message_at DESC);
            """)
            # Привязка авторов Wazzup к нашим операторам (для атрибуции ИИ-оценки).
            # is_bot — авторассылки/интеграции (Autocontact и т.п.), исключаются из оценки.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wazzup_operator_map (
                    author_id TEXT PRIMARY KEY,
                    author_name TEXT,
                    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    is_bot BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            # Два аккаунта Wazzup в одном разделе («op» — Верификаторы, «potok» —
            # «Поток», см. wazzup/accounts.py). channel_id у аккаунтов не
            # пересекается, поэтому колонка нужна не для уникальности, а чтобы
            # список чатов, показатели и привязка авторов фильтровались по
            # аккаунту одним условием. Метаданные-DEFAULT — без перезаписи строк.
            cursor.execute("""
                ALTER TABLE wazzup_messages
                    ADD COLUMN IF NOT EXISTS account TEXT NOT NULL DEFAULT 'op';
            """)
            # Время, которое прислал Wazzup, если dt заменено временем доставки:
            # входящее канала на обычном WhatsApp дошло с опозданием — например,
            # накопилось, пока канал был отключён (wazzup/delivery.py). NULL —
            # dt и есть время Wazzup.
            cursor.execute("""
                ALTER TABLE wazzup_messages
                    ADD COLUMN IF NOT EXISTS wazzup_dt TIMESTAMPTZ;
            """)
            # Цитата ответа, сделанного не из iCORE (wazzup/quotes.py): id
            # цитируемого и снимок на случай, когда оригинала в архиве нет.
            cursor.execute("""
                ALTER TABLE wazzup_messages
                    ADD COLUMN IF NOT EXISTS quoted_message_id TEXT,
                    ADD COLUMN IF NOT EXISTS quoted_is_echo BOOLEAN,
                    ADD COLUMN IF NOT EXISTS quoted_type TEXT,
                    ADD COLUMN IF NOT EXISTS quoted_text TEXT,
                    ADD COLUMN IF NOT EXISTS quoted_author_name TEXT;
            """)
            cursor.execute("""
                ALTER TABLE wazzup_chats
                    ADD COLUMN IF NOT EXISTS account TEXT NOT NULL DEFAULT 'op';
            """)
            cursor.execute("""
                ALTER TABLE wazzup_operator_map
                    ADD COLUMN IF NOT EXISTS account TEXT NOT NULL DEFAULT 'op';
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_wazzup_messages_account_dt
                ON wazzup_messages(account, dt);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_wazzup_chats_account_last
                ON wazzup_chats(account, last_message_at DESC);
            """)
            # «Чаты по каналам»: чаты одного номера во всех каналах аккаунта
            # (wazzup/chat_list.py) — без него каждый список просматривал бы
            # всю таблицу ради стрелки в строке.
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_wazzup_chats_account_chat
                ON wazzup_chats(account, chat_id);
            """)
            # Пилот обработки чатов и ранние статусы доставки Wazzup.
            from wazzup.pilot_schema import init_schema as init_wazzup_pilot_schema
            init_wazzup_pilot_schema(cursor)

            # Эпизоды Wazzup: чат нарезан по паузе неактивности (порог откалиброван
            # по данным: «долина» 4–11 ч, см. build_wazzup_episodes). Единица ИИ-оценки.
            # Транскрипт фиксируется на момент сборки — оценка воспроизводима, даже
            # когда исходные сообщения удалит ретеншн.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wazzup_episodes (
                    id BIGSERIAL PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    chat_type TEXT,
                    contact_name TEXT,
                    contact_phone TEXT,
                    started_at TIMESTAMPTZ NOT NULL,
                    ended_at TIMESTAMPTZ NOT NULL,
                    messages_count INTEGER NOT NULL,
                    inbound_count INTEGER NOT NULL,
                    outbound_count INTEGER NOT NULL,
                    human_outbound_count INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL CHECK (kind IN ('dialog', 'unanswered', 'outbound_only')),
                    operator_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    operator_share DOUBLE PRECISION,
                    authors JSONB NOT NULL DEFAULT '[]'::jsonb,
                    force_closed BOOLEAN NOT NULL DEFAULT FALSE,
                    transcript TEXT NOT NULL,
                    context_tail TEXT,
                    journal_evaluated_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (channel_id, chat_id, started_at)
                );
            """)
            cursor.execute("""
                ALTER TABLE wazzup_episodes
                ADD COLUMN IF NOT EXISTS journal_evaluated_at TIMESTAMPTZ;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_wazzup_episodes_ended
                ON wazzup_episodes(ended_at DESC);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_wazzup_episodes_operator
                ON wazzup_episodes(operator_user_id, ended_at DESC);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_wazzup_episodes_kind
                ON wazzup_episodes(kind, ended_at DESC);
            """)
            # ── ChatApp (ТП/ОП отдела ТЭЗ) ───────────────────────────────────
            # Переписка ТЭЗ живёт в ChatApp (WhatsApp Cloud API). В отличие от
            # Wazzup вебхуков нет — всё тянет ночной синк через API, поэтому
            # порядок таблиц тот же (сообщения -> чаты -> привязка -> эпизоды),
            # а наполнение другое. См. chatapp_client.py.
            #
            # Пара токенов лежит в БД, а не в памяти воркера: ChatApp даёт
            # только 100 токенов в сутки на пару email-appId, и рестарт Render
            # или второй воркер сожгли бы квоту за день.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chatapp_tokens (
                    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                    access_token TEXT,
                    access_expires_at TIMESTAMPTZ,
                    refresh_token TEXT,
                    refresh_expires_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            # Сырые сообщения. Ретеншн 45 дней (cleanup_chatapp_data).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chatapp_messages (
                    license_id INTEGER NOT NULL,
                    messenger_type TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    dt TIMESTAMPTZ NOT NULL,
                    side TEXT,
                    type TEXT,
                    subtype TEXT,
                    text TEXT,
                    file_link TEXT,
                    file_name TEXT,
                    file_content_type TEXT,
                    employee_id BIGINT,
                    app_id TEXT,
                    app_sender TEXT,
                    client_name TEXT,
                    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (license_id, messenger_type, chat_id, message_id)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chatapp_messages_chat
                ON chatapp_messages(license_id, messenger_type, chat_id, dt);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chatapp_messages_dt
                ON chatapp_messages(dt);
            """)
            # Пул чатов: у списка чатов ChatApp нет фильтра по датам, ночной синк
            # листает его назад по lastTime и складывает сюда.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chatapp_chats (
                    license_id INTEGER NOT NULL,
                    messenger_type TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    internal_id TEXT,
                    chat_type TEXT,
                    name TEXT,
                    phone TEXT,
                    responsible_employee_id BIGINT,
                    status TEXT,
                    last_time TIMESTAMPTZ,
                    messages_synced_until TIMESTAMPTZ,
                    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (license_id, messenger_type, chat_id)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chatapp_chats_last_time
                ON chatapp_chats(last_time DESC);
            """)
            # Привязка сотрудников ChatApp к нашим операторам. Заполняется из
            # /v1/companies/{id}/employees с автоподбором по ФИО и правится руками.
            # is_bot — интеграции/автоответы (fromApp.id='bitrix' идёт от владельца
            # аккаунта), они не претендуют на атрибуцию эпизода.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chatapp_operator_map (
                    employee_id BIGINT PRIMARY KEY,
                    employee_name TEXT,
                    email TEXT,
                    role_name TEXT,
                    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    is_bot BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            # Эпизоды: тот же принцип, что у wazzup_episodes — нарезка по паузе.
            # Транскрипта здесь нет: он нужен был ИИ-оценке Верификаторов, а
            # «Случайный чат» рендерит ленту из снапшота.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chatapp_episodes (
                    id BIGSERIAL PRIMARY KEY,
                    license_id INTEGER NOT NULL,
                    messenger_type TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    contact_name TEXT,
                    contact_phone TEXT,
                    started_at TIMESTAMPTZ NOT NULL,
                    ended_at TIMESTAMPTZ NOT NULL,
                    messages_count INTEGER NOT NULL,
                    inbound_count INTEGER NOT NULL,
                    outbound_count INTEGER NOT NULL,
                    human_outbound_count INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL CHECK (kind IN ('dialog', 'unanswered', 'outbound_only')),
                    operator_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    operator_share DOUBLE PRECISION,
                    authors JSONB NOT NULL DEFAULT '[]'::jsonb,
                    force_closed BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (license_id, messenger_type, chat_id, started_at)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chatapp_episodes_operator
                ON chatapp_episodes(operator_user_id, ended_at DESC);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chatapp_episodes_kind
                ON chatapp_episodes(kind, ended_at DESC);
            """)
            # Chat2Desk: заявки (request_stats) для выборки случайного чата в разделе
            # «Оценка чатов ЧМ». Заполняется ежедневным синком метрик (те же строки,
            # что идут в chat_manager_daily_metrics — API-квоту не тратим).
            # Ретеншн 45 дней (cleanup_c2d_eval_data).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS c2d_requests (
                    request_id BIGINT PRIMARY KEY,
                    day DATE NOT NULL,
                    request_start TIMESTAMP,
                    request_end TIMESTAMP,
                    request_time INTEGER,
                    reaction_time INTEGER,
                    request_type TEXT,
                    transport TEXT,
                    channel_id BIGINT,
                    channel_name TEXT,
                    client_id BIGINT,
                    client_name TEXT,
                    client_phone TEXT,
                    c2d_operator_id BIGINT,
                    c2d_operator_name TEXT,
                    operator_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    incoming_messages INTEGER,
                    outgoing_messages INTEGER,
                    rating_score REAL,
                    rating_text TEXT,
                    synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_c2d_requests_day ON c2d_requests(day);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_c2d_requests_operator_day
                ON c2d_requests(operator_id, day);
            """)
            # Справочник «учётка Chat2Desk → сотрудник» для разбора низких оценок
            # (задача #286): сообщения вендора знают только id учётки.
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_c2d_requests_c2d_operator_day
                ON c2d_requests(c2d_operator_id, day);
            """)
            # Телефон водителя — разрез раздела «Чаты водителей»: он начинается
            # с номера и по нему находит клиента Chat2Desk. Без индекса это шло
            # обратным проходом по idx_c2d_requests_day через ВСЕ 86 052 строки
            # (22 мс на каждом поиске и обновлении, замер на проде 07.09.2026).
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_c2d_requests_client_phone
                ON c2d_requests(client_phone);
            """)
            # «Ответ внутри чата» для биллинга (задача #343): сколько раз оператор
            # отвечал и среднее время между репликой клиента и ответом — колонки
            # request_stats `replies` и `average_replies_time`, по которым сверяются
            # супервайзеры. Среднее пишется только при replies > 0: ноль ответов —
            # это «не отвечал», а не «ответил мгновенно». replies IS NULL — строка
            # сохранена до появления колонок и ещё не пересинхронизирована.
            cursor.execute("""
                ALTER TABLE c2d_requests ADD COLUMN IF NOT EXISTS replies INTEGER;
            """)
            cursor.execute("""
                ALTER TABLE c2d_requests ADD COLUMN IF NOT EXISTS average_replies_time REAL;
            """)
            # Настоящий номер водителя, который пришёл в WhatsApp идентификатором:
            # с 02.09.2026 у такого клиента в client_phone лежит
            # «[wa_gupshup] KZ.1000000000000001», а номер — только в колонке
            # request_stats `assigned_phone`. Без неё «Чаты водителей» не находят
            # по номеру каждое шестое обращение «Ноль такси». NULL — строка
            # синхронизирована до появления колонки.
            cursor.execute("""
                ALTER TABLE c2d_requests ADD COLUMN IF NOT EXISTS assigned_phone TEXT;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_c2d_requests_assigned_phone
                ON c2d_requests(assigned_phone);
            """)
            # Снапшот переписки заявки: тянется из API один раз при выборе чата и
            # дальше служит источником и для оценки СВ, и для просмотра ЧМ — даже
            # если Chat2Desk удалит историю. Ретеншн 180 дней (~полгода).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS c2d_chat_snapshots (
                    id SERIAL PRIMARY KEY,
                    request_id BIGINT NOT NULL UNIQUE,
                    dialog_id BIGINT,
                    day DATE,
                    operator_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    c2d_operator_name TEXT,
                    channel_name TEXT,
                    transport TEXT,
                    client_name TEXT,
                    client_phone TEXT,
                    messages JSONB NOT NULL DEFAULT '[]'::jsonb,
                    messages_count INTEGER NOT NULL DEFAULT 0,
                    request_payload JSONB,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_c2d_snapshots_operator
                ON c2d_chat_snapshots(operator_id, day);
            """)
            # Эпизоды Wazzup (Верификаторы) живут в той же таблице снапшотов:
            # source='wazzup', ключ — чат + начало эпизода (request_id нет).
            # Весь конвейер оценки (calls.c2d_snapshot_id, цитаты, просмотр)
            # работает поверх snapshot_id и не различает источники.
            cursor.execute("""
                ALTER TABLE c2d_chat_snapshots ALTER COLUMN request_id DROP NOT NULL;
            """)
            cursor.execute("""
                ALTER TABLE c2d_chat_snapshots
                ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'chat2desk';
            """)
            cursor.execute("""
                ALTER TABLE c2d_chat_snapshots ADD COLUMN IF NOT EXISTS wz_channel_id TEXT;
            """)
            cursor.execute("""
                ALTER TABLE c2d_chat_snapshots ADD COLUMN IF NOT EXISTS wz_chat_id TEXT;
            """)
            cursor.execute("""
                ALTER TABLE c2d_chat_snapshots ADD COLUMN IF NOT EXISTS episode_start TIMESTAMPTZ;
            """)
            cursor.execute("""
                ALTER TABLE c2d_chat_snapshots ADD COLUMN IF NOT EXISTS episode_end TIMESTAMPTZ;
            """)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_wz_snapshots_episode
                ON c2d_chat_snapshots (wz_channel_id, wz_chat_id, episode_start)
                WHERE source = 'wazzup';
            """)
            # Эпизоды ChatApp (ТЭЗ) живут в той же таблице: source='chatapp',
            # колонки wz_* переиспользуются как обобщённый ключ эпизода —
            # wz_channel_id = '<licenseId>:<messengerType>', wz_chat_id = chatId.
            # Имена остались от Wazzup, потому что вся оценка (calls.c2d_snapshot_id,
            # цитаты, просмотр) работает поверх snapshot_id и источник не различает.
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_ca_snapshots_episode
                ON c2d_chat_snapshots (wz_channel_id, wz_chat_id, episode_start)
                WHERE source = 'chatapp';
            """)
            # Сырые вебхуки Chat2Desk (задача табло, 18.09.2026). Вендор не отдаёт
            # событий статусов операторов, но сообщения и закрытие обращений шлёт
            # сам — это снимает основной расход квоты: табло больше не выкачивает
            # день обращений опросом каждые 10 минут.
            # Храним СЫРЬЁ, а не готовые метрики: событие приходит по одному, при
            # неуспехе вендор повторяет доставку трижды, а пересчитать показатели
            # из сырья можно задним числом — восстановить выброшенное нельзя.
            # Ретеншн 7 суток (решение владельца 18.09.2026): табло смотрит на
            # сегодня, недели с запасом хватает и на сверку с ночным синком.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS c2d_webhook_events (
                    id BIGSERIAL PRIMARY KEY,
                    event_key TEXT NOT NULL UNIQUE,
                    hook_type TEXT NOT NULL,
                    event_at TIMESTAMP NOT NULL,
                    day DATE NOT NULL,
                    message_id BIGINT,
                    request_id BIGINT,
                    dialog_id BIGINT,
                    client_id BIGINT,
                    c2d_operator_id BIGINT,
                    channel_id BIGINT,
                    transport TEXT,
                    message_type TEXT,
                    -- Тип обращения приходит ТОЛЬКО в событии new_request («common» или
                    -- «rating»), зато приходит от самого вендора — гадать по составу
                    -- ленты, автоопрос это или чат, больше не нужно.
                    request_type TEXT,
                    -- Телефон и имя водителя лежат в теле события, но вынесены колонками:
                    -- «Чаты водителей» начинаются с поиска по номеру, а индекс по
                    -- выражению над JSON и читается хуже, и живёт отдельной жизнью от
                    -- условия в запросе.
                    client_phone TEXT,
                    client_name TEXT,
                    is_new_request BOOLEAN,
                    payload JSONB NOT NULL,
                    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            # Колонки появились после первой версии таблицы: там, где таблицу уже
            # создали (стенд, промежуточный деплой), `CREATE TABLE IF NOT EXISTS` их
            # не добавит, и запись падала бы UndefinedColumn.
            cursor.execute("""
                ALTER TABLE c2d_webhook_events ADD COLUMN IF NOT EXISTS request_type TEXT;
            """)
            cursor.execute("""
                ALTER TABLE c2d_webhook_events ADD COLUMN IF NOT EXISTS client_phone TEXT;
            """)
            cursor.execute("""
                ALTER TABLE c2d_webhook_events ADD COLUMN IF NOT EXISTS client_name TEXT;
            """)
            # Разрезы «Чатов водителей»: лента клиента за окно и поиск по номеру.
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_c2d_webhook_events_client
                ON c2d_webhook_events(client_id, event_at);
            """)
            # Хвост номера — тем же выражением, что у c2d_requests: иностранный номер
            # называют без кода страны, и достроить его можно только по последним
            # девяти цифрам. Выражение обязано совпадать с условием в запросе
            # ДОСЛОВНО, иначе планировщик уйдёт в полный проход.
            cursor.execute(r"""
                CREATE INDEX IF NOT EXISTS idx_c2d_webhook_events_phone_tail
                ON c2d_webhook_events (
                    right(regexp_replace(client_phone, '\D', '', 'g'), 9))
                WHERE client_id IS NOT NULL;
            """)
            # Разрез табло — сутки целиком, а внутри суток события собираются по
            # обращению: оба поля в одном индексе, чтобы день читался одним проходом.
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_c2d_webhook_events_day
                ON c2d_webhook_events(day, request_id);
            """)
            # Ретеншн ходит по времени события, а не по времени приёма: повтор
            # доставки через два часа не должен продлевать жизнь старым суткам.
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_c2d_webhook_events_at
                ON c2d_webhook_events(event_at);
            """)
            # Закрытые обращения — для «открытых чатов» табло (get_c2d_open_chats_by_operator):
            # проверка «закрывалось ли обращение» без него была отдельным проходом всей
            # недели событий, ~1,5 с на пересборку (EXPLAIN на проде 19.10.2026). Индекс
            # крошечный: закрытий ~1,5 тыс. в сутки на ~20 тыс. событий.
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_c2d_webhook_events_closed
                ON c2d_webhook_events(request_id)
                WHERE hook_type IN ('close_request', 'close_dialog');
            """)
            # Оценка чата — обычная запись журнала (calls) с критериями направления
            # ЧМ; чат-специфика — ссылка на снапшот переписки и цитаты
            # ({messageId, text, comment}). Отдельная таблица оценок чатов была
            # ошибкой первой итерации — удалена до появления данных.
            cursor.execute("DROP TABLE IF EXISTS c2d_chat_evaluations;")
            cursor.execute("""
                ALTER TABLE calls
                ADD COLUMN IF NOT EXISTS c2d_snapshot_id INTEGER
                    REFERENCES c2d_chat_snapshots(id) ON DELETE SET NULL;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_calls_c2d_snapshot
                ON calls(c2d_snapshot_id)
                WHERE c2d_snapshot_id IS NOT NULL;
            """)
            cursor.execute("""
                ALTER TABLE calls
                ADD COLUMN IF NOT EXISTS chat_quotes JSONB;
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_technical_issues (
                    id SERIAL PRIMARY KEY,
                    batch_id UUID NOT NULL,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    issue_date DATE NOT NULL,
                    start_time TIME NOT NULL DEFAULT TIME '00:00:00',
                    end_time TIME NOT NULL DEFAULT TIME '23:59:59',
                    requested_issue_date DATE,
                    requested_start_time TIME,
                    requested_end_time TIME,
                    reason VARCHAR(255) NOT NULL,
                    comment TEXT,
                    workplace_number INTEGER,
                    for_supervisor BOOLEAN NOT NULL DEFAULT FALSE,
                    direction_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                ALTER TABLE operator_technical_issues
                ADD COLUMN IF NOT EXISTS for_supervisor BOOLEAN NOT NULL DEFAULT FALSE;
            """)
            cursor.execute("""
                ALTER TABLE operator_technical_issues
                ADD COLUMN IF NOT EXISTS requested_issue_date DATE;
            """)
            cursor.execute("""
                ALTER TABLE operator_technical_issues
                ADD COLUMN IF NOT EXISTS requested_start_time TIME;
            """)
            cursor.execute("""
                ALTER TABLE operator_technical_issues
                ADD COLUMN IF NOT EXISTS requested_end_time TIME;
            """)
            # Офис РМ: 'support' (Тех поддержка, РМ 1–30) или 'sales' (Отдел продаж, 28–97).
            # Нужен, т.к. номера 28–30 есть у обоих и иначе счётчики смешиваются.
            cursor.execute("""
                ALTER TABLE operator_technical_issues
                ADD COLUMN IF NOT EXISTS workplace_office VARCHAR(16);
            """)
            # Все существующие инциденты принадлежат Тех поддержке (ТП).
            cursor.execute("""
                UPDATE operator_technical_issues
                SET workplace_office = 'support'
                WHERE workplace_office IS NULL AND workplace_number IS NOT NULL;
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS technical_issue_workplace_settings (
                    workplace_number INTEGER PRIMARY KEY CHECK (workplace_number BETWEEN 1 AND 200),
                    for_supervisor BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Расширяем верхнюю границу РМ (раньше было 1..30) до 1..200 — у отдела
            # продаж РМ нумеруются до 97. На существующих БД пересоздаём CHECK.
            # Имя CHECK-констрейнта не угадываем: дропаем все CHECK на таблице и ставим новый.
            cursor.execute("""
                DO $$
                DECLARE c text;
                BEGIN
                    FOR c IN
                        SELECT conname FROM pg_constraint
                        WHERE conrelid = 'technical_issue_workplace_settings'::regclass
                          AND contype = 'c'
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE technical_issue_workplace_settings DROP CONSTRAINT %I', c
                        );
                    END LOOP;
                    ALTER TABLE technical_issue_workplace_settings
                        ADD CONSTRAINT technical_issue_workplace_settings_workplace_number_check
                        CHECK (workplace_number BETWEEN 1 AND 200);
                END $$;
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_offline_activities (
                    id SERIAL PRIMARY KEY,
                    batch_id UUID NOT NULL,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    activity_date DATE NOT NULL,
                    start_time TIME NOT NULL DEFAULT TIME '00:00:00',
                    end_time TIME NOT NULL DEFAULT TIME '23:59:59',
                    comment TEXT,
                    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS it_ticket_channels (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL UNIQUE,
                    title VARCHAR(255),
                    chat_type VARCHAR(32),
                    username VARCHAR(255),
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    source VARCHAR(16) NOT NULL DEFAULT 'manual',
                    added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_it_ticket_channels_active
                ON it_ticket_channels(is_active);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS it_tickets (
                    id SERIAL PRIMARY KEY,
                    profile VARCHAR(16) NOT NULL DEFAULT 'op',
                    category VARCHAR(255),
                    subcategory VARCHAR(255),
                    priority VARCHAR(16),
                    title VARCHAR(500),
                    body TEXT NOT NULL,
                    fields JSONB NOT NULL DEFAULT '{}'::jsonb,
                    channel_id INTEGER REFERENCES it_ticket_channels(id) ON DELETE SET NULL,
                    channel_chat_id BIGINT,
                    channel_title VARCHAR(255),
                    telegram_message_id BIGINT,
                    status VARCHAR(16) NOT NULL DEFAULT 'sent',
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_by_name VARCHAR(255),
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_it_tickets_created_at
                ON it_tickets(created_at);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS it_ticket_instructions (
                    profile VARCHAR(16) PRIMARY KEY,
                    instructions TEXT NOT NULL DEFAULT '',
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS it_ticket_pinned_channels (
                    profile VARCHAR(16) PRIMARY KEY,
                    channel_id INTEGER REFERENCES it_ticket_channels(id) ON DELETE SET NULL,
                    pinned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    pinned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Редактируемый каталог тематик заявок (одна строка). Если пусто —
            # используется дефолт IT_TICKET_CATALOG. Редактирует админ.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS it_ticket_catalog (
                    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                    data JSONB NOT NULL,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_history (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    changed_by INTEGER REFERENCES users(id),  -- Who made the change (e.g., admin or sv ID)
                    field_changed VARCHAR(50) NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    changed_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id UUID PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    refresh_token_hash TEXT NOT NULL,
                    previous_refresh_token_hash TEXT,
                    previous_refresh_valid_until TIMESTAMP,
                    user_agent TEXT,
                    ip_address VARCHAR(64),
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    last_seen_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    expires_at TIMESTAMP NOT NULL,
                    revoked_at TIMESTAMP,
                    sensitive_data_unlocked BOOLEAN NOT NULL DEFAULT FALSE,
                    sensitive_data_unlocked_at TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id
                ON user_sessions(user_id);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at
                ON user_sessions(expires_at);
            """)
            # Кто выдал доступ к чувствительным данным. До этой колонки
            # подтверждающий уходил только в Telegram-уведомление, и в карточке
            # сессии на вопрос «кто открыл» ответить было нечем.
            cursor.execute("""
                ALTER TABLE user_sessions
                ADD COLUMN IF NOT EXISTS sensitive_data_unlocked_by INTEGER
                    REFERENCES users(id) ON DELETE SET NULL;
            """)
            # Индекса по last_seen_at нет НАМЕРЕННО. Список «Сессий» собирает живые
            # сессии в MATERIALIZED CTE и сортирует людей по MAX(last_seen_at), карточка
            # идёт по idx_user_sessions_user_id — частичный индекс по сырому last_seen_at
            # не нужен ни одному запросу (и на проде idx_scan = 0). Зато из-за него каждое
            # продление сессии (touch раз в минуту на активную вкладку) было не-HOT
            # обновлением: к 19.10.2026 индекс на 9 тыс. строк раздуло до 31 МБ. Удалён
            # в базе 19.10; здесь — чтобы он не вернулся на базе, где ещё есть.
            cursor.execute("DROP INDEX IF EXISTS idx_user_sessions_active_last_seen;")
            # Журнал выдачи доступа: карточка сессии показывает, кто и когда
            # открыл или закрыл чувствительные данные. Живёт ровно столько же,
            # сколько сама сессия — ретенция чистит его каскадом.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS session_access_events (
                    id SERIAL PRIMARY KEY,
                    session_id UUID NOT NULL REFERENCES user_sessions(session_id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    action VARCHAR(16) NOT NULL CHECK (action IN ('granted', 'revoked')),
                    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    actor_role VARCHAR(32),
                    ip_address VARCHAR(64),
                    user_agent TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_access_events_session
                ON session_access_events(session_id, created_at DESC);
            """)
            cursor.execute("""
                CREATE OR REPLACE FUNCTION revoke_sessions_on_inactive_status()
                RETURNS TRIGGER
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF NEW.status IN ('fired', 'dismissal')
                       AND (OLD.status IS DISTINCT FROM NEW.status) THEN
                        UPDATE user_sessions
                        SET revoked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                        WHERE user_id = NEW.id
                          AND revoked_at IS NULL;
                    END IF;

                    RETURN NEW;
                END;
                $$;
            """)
            cursor.execute("""
                DROP TRIGGER IF EXISTS trg_revoke_sessions_on_inactive_status ON users;
            """)
            cursor.execute("""
                CREATE TRIGGER trg_revoke_sessions_on_inactive_status
                AFTER UPDATE OF status ON users
                FOR EACH ROW
                EXECUTE FUNCTION revoke_sessions_on_inactive_status();
            """)
            # Work schedules (shifts) table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS work_shifts (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    shift_date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    shift_type VARCHAR(32) NOT NULL DEFAULT 'regular' CHECK (shift_type IN ('regular', 'office_practice', 'phone_shift')),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, shift_date, start_time, end_time)
                );
            """)
            cursor.execute("""
                ALTER TABLE work_shifts
                DROP CONSTRAINT IF EXISTS work_shifts_shift_type_check;
            """)
            cursor.execute("""
                ALTER TABLE work_shifts
                ADD CONSTRAINT work_shifts_shift_type_check
                CHECK (shift_type IN ('regular', 'office_practice', 'phone_shift'));
            """)

            # Break periods within shifts
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_breaks (
                    id SERIAL PRIMARY KEY,
                    shift_id INTEGER NOT NULL REFERENCES work_shifts(id) ON DELETE CASCADE,
                    start_minutes INTEGER NOT NULL,
                    end_minutes INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # История изменений графика: кто, когда и откуда правил смены оператора.
            # Пишется не построчно по work_shifts, а сравнением состояния дня «до» и
            # «после» операции: публикация аукциона стирает день и пересобирает его
            # заново, и построчный аудит выдавал бы пару «удалена + добавлена» на
            # каждую смену при каждой перепубликации. actor_name хранится копией —
            # чтобы запись читалась и после переименования или увольнения автора.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS work_shift_changes (
                    id BIGSERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    shift_date DATE NOT NULL,
                    -- Без CHECK намеренно: набор действий и источников будет
                    -- пополняться, а расширение констрейнта на живой таблице —
                    -- миграция там, где достаточно нового значения. Допустимые
                    -- значения перечислены в WORK_SHIFT_CHANGE_ACTIONS/SOURCES,
                    -- незнакомый код интерфейс показывает как есть, а не прячет.
                    action VARCHAR(24) NOT NULL,
                    source VARCHAR(32) NOT NULL,
                    start_time TIME NULL,
                    end_time TIME NULL,
                    prev_start_time TIME NULL,
                    prev_end_time TIME NULL,
                    shift_type VARCHAR(32) NULL,
                    prev_shift_type VARCHAR(32) NULL,
                    actor_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    actor_name VARCHAR(255) NOT NULL DEFAULT '',
                    actor_role VARCHAR(32) NOT NULL DEFAULT '',
                    changed_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            # Был ли день сотрудника пуст (ни смены, ни выходного) ДО операции.
            # По нему сводка отличает первичное внесение графика от правки:
            # из одних строк журнала этого не видно — добавленная вторая смена
            # и смена на пустой день пишутся одинаковым 'added'. NULL — строки,
            # записанные до появления колонки: для них сводка судит по действиям.
            cursor.execute("""
                ALTER TABLE work_shift_changes ADD COLUMN IF NOT EXISTS day_was_empty BOOLEAN NULL;
            """)
            # Заявка на отправку суточной сводки об изменениях графика
            # («Уведомления об изменениях» в меню раздела). Render поднимает
            # новый процесс до остановки старого, и если окно накрыло время
            # рассылки, оба планировщика разошлют одну и ту же сводку:
            # max_instances защищает только внутри одного процесса. Ключ —
            # «сутки + получатель», строка занимается ДО отправки.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schedule_change_report_sends (
                    period_start DATE NOT NULL,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    sent_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    PRIMARY KEY (period_start, user_id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS work_schedule_break_rules (
                    id SERIAL PRIMARY KEY,
                    direction_name VARCHAR(255) NOT NULL,
                    min_shift_minutes INTEGER NOT NULL,
                    max_shift_minutes INTEGER NOT NULL,
                    break_durations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CHECK (min_shift_minutes >= 0),
                    CHECK (max_shift_minutes > min_shift_minutes)
                );
            """)
            # Настройки перерывов уровня направления (не привязаны к диапазону смены):
            # cross_operator_gap_minutes — минимальный промежуток между перерывами
            # РАЗНЫХ операторов одного направления (0 = требования нет).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS work_schedule_break_direction_settings (
                    id SERIAL PRIMARY KEY,
                    direction_name VARCHAR(255) NOT NULL,
                    cross_operator_gap_minutes INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CHECK (cross_operator_gap_minutes >= 0 AND cross_operator_gap_minutes <= 240)
                );
            """)
            # «Разрешить пересечение» (09.11.2026): перерывы операторов направления
            # могут совпадать по времени — автоматика не разводит их между собой.
            # ALTER — только когда колонки ещё нет: даже с IF NOT EXISTS он берёт
            # ACCESS EXCLUSIVE до конца инициализации, а таблицу читает каждое
            # сохранение смены.
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                         WHERE table_schema = current_schema()
                           AND table_name = 'work_schedule_break_direction_settings'
                           AND column_name = 'allow_break_overlap'
                    ) THEN
                        ALTER TABLE work_schedule_break_direction_settings
                            ADD COLUMN allow_break_overlap BOOLEAN NOT NULL DEFAULT FALSE;
                    END IF;
                END $$;
            """)
            # «Время перерыва» направления с «Разрешить пересечение» (10.11.2026): у
            # регионов, например, обед с 13:00 до 14:00 всем. Минуты от полуночи;
            # пусто — перерывы по правилам. Тот же приём: ALTER только без колонки.
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                         WHERE table_schema = current_schema()
                           AND table_name = 'work_schedule_break_direction_settings'
                           AND column_name = 'fixed_break_start_minutes'
                    ) THEN
                        ALTER TABLE work_schedule_break_direction_settings
                            ADD COLUMN fixed_break_start_minutes INTEGER NULL,
                            ADD COLUMN fixed_break_end_minutes INTEGER NULL;
                    END IF;
                END $$;
            """)
            # Задача #352. Перерыв супервайзера, который РОП вычитает из его часов по
            # Clockster (supervisor_hours.py). На человека, а не на направление: у СВ
            # направления нет, а значение у разных СВ разное. Нет строки или NULL —
            # действует supervisor_hours.DEFAULT_BREAK_MINUTES (час), таблицу не засеваем.
            # no_clockster_card — РОП явно сказал «у этого СВ карточки нет»: сшивка по
            # ФИО могла найти чужого человека, и без флага отменить её было бы нечем.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS supervisor_hours_settings (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    break_minutes INTEGER NULL,
                    no_clockster_card BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CHECK (break_minutes IS NULL OR (break_minutes >= 0 AND break_minutes <= 240))
                );
            """)
            # Ручные отметки СВ (задача #352): РОП дописывает приход/уход, который
            # терминал Clockster не записал (забыл отметить уход, не зарегистрирован
            # на терминале). Сам Clockster не меняем — отметка живёт у нас и идёт в
            # ночной пересчёт наравне с терминальной. Удаление мягкое: по этим
            # строкам считаются часы и ЗП, и кто что дописал/снял, должно остаться.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS supervisor_manual_marks (
                    id BIGSERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    mark_at TIMESTAMPTZ NOT NULL,
                    kind VARCHAR(8) NOT NULL CHECK (kind IN ('in', 'out')),
                    comment TEXT NULL,
                    -- Правка ручной отметки: прежняя строка мягко снимается, новая
                    -- ссылается на неё — история правок остаётся.
                    replaces_mark_id BIGINT NULL REFERENCES supervisor_manual_marks(id) ON DELETE SET NULL,
                    -- Исправление отметки Clockster: исходная (время и тип) в пары не
                    -- идёт, вместо неё считается эта. Снятие исправления её возвращает.
                    replaces_clockster_at TIMESTAMPTZ NULL,
                    replaces_clockster_kind VARCHAR(8) NULL CHECK (replaces_clockster_kind IN ('in', 'out')),
                    created_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    deleted_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    deleted_at TIMESTAMPTZ NULL
                );
                CREATE INDEX IF NOT EXISTS idx_supervisor_manual_marks_user_at
                    ON supervisor_manual_marks(user_id, mark_at) WHERE deleted_at IS NULL;
            """)
            # Отметки Clockster для часов СВ (задача #352), взятые синхронизацией прямо из
            # Clockster: кнопкой «Синхронизация с Clockster» и ночной задачей. Хранятся
            # у нас, чтобы окно дня показывало те же отметки, по которым посчитаны
            # часы, — кэш раздела «Отметки» собирается не за все дни. Дни, за которые
            # синхронизация прошла целиком, — в supervisor_clockster_sync_days: за них
            # отметки берутся отсюда, за остальные — из кэша «Отметок».
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS supervisor_clockster_marks (
                    ext_id TEXT NOT NULL,
                    mark_at TIMESTAMPTZ NOT NULL,
                    kind VARCHAR(8) NOT NULL CHECK (kind IN ('in', 'out')),
                    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (ext_id, mark_at, kind)
                );
                CREATE INDEX IF NOT EXISTS idx_supervisor_clockster_marks_at
                    ON supervisor_clockster_marks(mark_at);
                CREATE TABLE IF NOT EXISTS supervisor_clockster_sync_days (
                    day DATE PRIMARY KEY,
                    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            # Разовые задачи старта: строка появляется один раз, и задача с её ключом
            # больше не выполняется. Нужна там, где повторный запуск на каждом старте
            # стёр бы уже новые, правильные данные.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_one_time_jobs (
                    job_key TEXT PRIMARY KEY,
                    done_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            # Перерывы на сменах СВ до задачи #352 ставил запасной профиль операторов
            # (15/30/15): у СВ нет направления. Теперь перерывы СВ РОП ставит только
            # руками (_save_shift_tx), поэтому автоматические снимаем — ОДИН раз, при
            # первом старте нового кода: на каждом старте это стёрло бы и ручные.
            # Смены дней, когда человек ещё был оператором (операторское членство в
            # группе), не трогаем: это его операторское прошлое, а не график СВ.
            cursor.execute("""
                INSERT INTO app_one_time_jobs (job_key) VALUES (%s)
                ON CONFLICT (job_key) DO NOTHING RETURNING job_key
            """, ('task352_drop_auto_supervisor_breaks',))
            if cursor.fetchone():
                cursor.execute("""
                DELETE FROM shift_breaks sb
                USING work_shifts ws, users u, departments d
                WHERE sb.shift_id = ws.id
                  AND ws.operator_id = u.id
                  AND u.department_id = d.id
                  AND lower(COALESCE(u.role, '')) IN ('sv', 'supervisor')
                  AND lower(COALESCE(d.code, '')) = ANY(%s)
                  AND NOT EXISTS (
                      SELECT 1 FROM group_operator_memberships gom
                      WHERE gom.operator_id = ws.operator_id
                        AND gom.start_date <= ws.shift_date
                        AND (gom.end_date IS NULL OR gom.end_date >= ws.shift_date)
                  )
                """, (list(SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES),))

            # Days off table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS days_off (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    day_off_date DATE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, day_off_date)
                );
            """)
            # Shift swap requests between operators (operator -> operator)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS work_shift_swap_requests (
                    id SERIAL PRIMARY KEY,
                    requester_operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    target_operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    direction_id INTEGER NULL REFERENCES directions(id) ON DELETE SET NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    request_comment TEXT NULL,
                    response_comment TEXT NULL,
                    requested_shifts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    responded_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    responded_at TIMESTAMP NULL,
                    accepted_at TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT work_shift_swap_requests_status_check CHECK (status IN ('pending', 'accepted', 'rejected', 'cancelled')),
                    CONSTRAINT work_shift_swap_requests_period_check CHECK (end_date >= start_date),
                    CONSTRAINT work_shift_swap_requests_participants_check CHECK (requester_operator_id <> target_operator_id)
                );
            """)
            # Заявки на изменение СВОЕЙ смены (оператор -> супервайзер), задача #17.
            #
            # Отдельная таблица, а не ещё один request_type у обменов: у обмена
            # две стороны-оператора и решение принимает второй оператор, здесь же
            # вторая сторона — руководитель, и согласование идёт по периметру
            # отдела. Общая таблица потребовала бы делать target_operator_id
            # необязательным и разводить проверки прав по колонке вида заявки.
            #
            # supervisor_id — СНИМОК руководителя на момент подачи. Он нужен
            # триггеру колокола: вычислять СВ через группу в PL/pgSQL значило бы
            # продублировать _get_operator_group_id_tx на SQL. На право
            # согласовать он не влияет — там периметр отдела, как везде.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS work_shift_change_requests (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    department_id INTEGER NULL REFERENCES departments(id) ON DELETE SET NULL,
                    supervisor_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    request_kind VARCHAR(16) NOT NULL,
                    shift_date DATE NOT NULL,
                    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    request_comment TEXT NULL,
                    response_comment TEXT NULL,
                    reviewed_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    reviewed_at TIMESTAMP NULL,
                    operator_seen_at TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT work_shift_change_requests_kind_check
                        CHECK (request_kind IN ('shorten', 'extra', 'change', 'day_off')),
                    CONSTRAINT work_shift_change_requests_status_check
                        CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled'))
                );
            """)
            # Виды «change» и «day_off» добавлены задачей #349. Ограничение,
            # заведённое прежним кодом с двумя видами, снимаем и заводим заново —
            # но только пока в его определении нет самого свежего вида. DROP + ADD
            # на каждом старте держал бы ACCESS EXCLUSIVE на таблице до конца всей
            # инициализации, а её читает колокол каждого пользователя.
            # Добавляешь вид — меняй и ключ поиска здесь, иначе прежний CHECK
            # переживёт деплой и отвергнет новое значение.
            cursor.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conrelid = 'work_shift_change_requests'::regclass
                          AND conname = 'work_shift_change_requests_kind_check'
                          AND position('''day_off''' IN pg_get_constraintdef(oid)) = 0
                    ) THEN
                        ALTER TABLE work_shift_change_requests
                            DROP CONSTRAINT work_shift_change_requests_kind_check;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conrelid = 'work_shift_change_requests'::regclass
                          AND conname = 'work_shift_change_requests_kind_check'
                    ) THEN
                        ALTER TABLE work_shift_change_requests
                            ADD CONSTRAINT work_shift_change_requests_kind_check
                            CHECK (request_kind IN ('shorten', 'extra', 'change', 'day_off'));
                    END IF;
                END $$;
            """)
            # Подписка главы отдела на уведомления о заявках по сменам.
            #
            # Уведомление о заявке уходит ПРЯМОМУ супервайзеру оператора всегда —
            # это его люди и его решение. Глава отдела получает их только по
            # своей воле: у отдела десятки операторов, и поток чужих заявок
            # превратил бы колокол в ленту, на которую перестают смотреть.
            #
            # Строки заводятся только для тех, кто подписался: отсутствие строки
            # и есть «не подписан». Обратной подписки (замолчать) у супервайзера
            # нет намеренно — иначе заявку можно было бы не заметить совсем.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_change_notify_optin (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Special statuses by period (vacation/sick leave/unpaid leave/dismissal)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_schedule_status_periods (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    status_code VARCHAR(32) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NULL,
                    dismissal_reason TEXT NULL,
                    is_blacklist BOOLEAN NOT NULL DEFAULT FALSE,
                    comment TEXT NULL,
                    created_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Период, который не идёт в стаж (задача #358, п. 8: длительный Б/С).
            # Флаг живёт на периоде графика, а не на заявке: стаж считается по
            # тому, что стоит в графике, — заявка лишь один из путей туда.
            # ALTER — только когда колонки ещё нет: даже с IF NOT EXISTS он берёт
            # ACCESS EXCLUSIVE до конца инициализации, а таблицу читает каждый
            # экран графиков.
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                         WHERE table_schema = current_schema()
                           AND table_name = 'operator_schedule_status_periods'
                           AND column_name = 'exclude_from_tenure'
                    ) THEN
                        ALTER TABLE operator_schedule_status_periods
                            ADD COLUMN exclude_from_tenure BOOLEAN NOT NULL DEFAULT FALSE;
                    END IF;
                END $$;
            """)
            # Временная ставка оператора на период — ТОЛЬКО для расчёта ресурсов и
            # аукциона смен. Заводится решением владельца 29.08.2026: график на
            # следующую неделю строится по ставкам, которых в карточках ещё нет
            # (кадровые изменят их позже, обычным порядком).
            #
            # Почему отдельной таблицей, а не правкой users.rate: смена ставки в
            # карточке (update_user, field == 'rate') замораживает прошлые месяцы в
            # work_hours.rate прежним значением и якорит текущий месяц новым. Две
            # правки «туда и обратно» за две недели оставили бы в помесячной ставке
            # два следа, и вернуться к настоящей ставке уже нельзя было бы без
            # ручной чистки work_hours. Здесь же подмена живёт своим слоем, сама
            # истекает по valid_to и до денег не доходит вовсе.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_rate_overrides (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    rate DECIMAL(3,2) NOT NULL CHECK (rate IN (1.00, 0.75, 0.50)),
                    valid_from DATE NOT NULL,
                    valid_to DATE NOT NULL,
                    note TEXT NULL,
                    created_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT operator_rate_overrides_window CHECK (valid_to >= valid_from)
                );
            """)
            # Один оператор — одна подмена на один и тот же период: повторное
            # сохранение обновляет строку, а не плодит вторую с другой ставкой.
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_operator_rate_overrides_period
                ON operator_rate_overrides(operator_id, valid_from, valid_to);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_operator_rate_overrides_window
                ON operator_rate_overrides(valid_from, valid_to);
            """)
            # Ставка «на дату» одним выражением: мест чтения девять (расчёт
            # ресурсов и аукцион), и повторять COALESCE-подзапрос в каждом — верный
            # способ развести семантику. Функция STABLE, поэтому планировщик зовёт
            # её один раз на строку.
            #
            # ВАЖНО: сюда НЕ должны ходить учёт часов и зарплата. Там своя ставка —
            # помесячная work_hours.rate с переносом, а users.rate лишь запасной
            # вариант. Если подменённое значение доедет до _freeze_month_to_snapshots_tx,
            # оно станет в истории неотличимо от настоящей ставки.
            cursor.execute("""
                CREATE OR REPLACE FUNCTION operator_effective_rate(
                    p_operator_id INTEGER, p_on_date DATE
                ) RETURNS NUMERIC AS $$
                    SELECT COALESCE(
                        (SELECT o.rate
                           FROM operator_rate_overrides o
                          WHERE o.operator_id = p_operator_id
                            AND p_on_date BETWEEN o.valid_from AND o.valid_to
                          ORDER BY o.valid_from DESC, o.id DESC
                          LIMIT 1),
                        (SELECT u.rate FROM users u WHERE u.id = p_operator_id),
                        1.0
                    );
                $$ LANGUAGE sql STABLE;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_shift_breaks_shift_id
                ON shift_breaks(shift_id);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_work_schedule_break_rules_direction
                ON work_schedule_break_rules(LOWER(direction_name));
            """)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_work_schedule_break_rules_direction_range
                ON work_schedule_break_rules(LOWER(direction_name), min_shift_minutes, max_shift_minutes);
            """)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_work_schedule_break_dir_settings_direction
                ON work_schedule_break_direction_settings(LOWER(TRIM(direction_name)));
            """)
            # Заявки на изменение смены: три выборки и три индекса под них —
            # свои заявки оператора, очередь «Запросы» у руководителя (по дате
            # смены, а не по дате подачи: разбирают день, а не ленту) и адресный
            # счётчик колокола у конкретного СВ.
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_shift_change_requests_operator
                ON work_shift_change_requests(operator_id, created_at DESC);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_shift_change_requests_status_date
                ON work_shift_change_requests(status, shift_date);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_shift_change_requests_supervisor_status
                ON work_shift_change_requests(supervisor_id, status);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_op_sched_status_periods_operator_start
                ON operator_schedule_status_periods(operator_id, start_date);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_op_sched_status_periods_operator_end
                ON operator_schedule_status_periods(operator_id, end_date);
            """)
            # Стаж: периоды «не в стаж» читаются при каждом расчёте цели оценок и
            # профиля — частичный индекс крошечный, их единицы.
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_op_sched_status_periods_tenure
                ON operator_schedule_status_periods(operator_id)
                WHERE exclude_from_tenure;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_op_sched_status_periods_operator_range
                ON operator_schedule_status_periods(operator_id, start_date DESC, end_date, id DESC);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_op_sched_status_periods_operator_status
                ON operator_schedule_status_periods(operator_id, status_code);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_op_sched_status_periods_status_operator
                ON operator_schedule_status_periods(status_code, operator_id);
            """)
            # Status timeline imports (CSV transitions -> events/segments for analytics and KPI calculations)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_status_import_batches (
                    id UUID PRIMARY KEY,
                    imported_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    source_rows INTEGER NOT NULL DEFAULT 0,
                    valid_events INTEGER NOT NULL DEFAULT 0,
                    matched_events INTEGER NOT NULL DEFAULT 0,
                    segments_saved INTEGER NOT NULL DEFAULT 0,
                    deleted_events INTEGER NOT NULL DEFAULT 0,
                    deleted_segments INTEGER NOT NULL DEFAULT 0,
                    invalid_rows_count INTEGER NOT NULL DEFAULT 0,
                    parse_errors_count INTEGER NOT NULL DEFAULT 0,
                    operators_count INTEGER NOT NULL DEFAULT 0,
                    open_tail_events INTEGER NOT NULL DEFAULT 0,
                    zero_or_negative_transitions INTEGER NOT NULL DEFAULT 0,
                    date_from DATE NULL,
                    date_to DATE NULL,
                    meta_json JSONB NOT NULL DEFAULT '{}'::jsonb
                );
            """)
            # Прогоны синка звонков Oktell («Учёт часов» -> «Звонки»). По ним ночной прогон
            # находит недобранные дни: после окончания дня по нему не прошло ни одного удачного
            # синка (18.10.2026 прокси лёг до утра, и день застыл на срезе 14:20). Такие дни
            # ночью дозабираются, а добранные не трогаются — ручные правки звонков в них
            # остаются. Время прогона — местное, как у самого синка (datetime.now()).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS oktell_calls_sync_runs (
                    id BIGSERIAL PRIMARY KEY,
                    started_at TIMESTAMP NOT NULL,
                    finished_at TIMESTAMP NOT NULL,
                    date_from DATE NOT NULL,
                    date_to DATE NOT NULL,
                    triggered_by VARCHAR(32) NOT NULL DEFAULT '',
                    status VARCHAR(16) NOT NULL,
                    updated_days INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_oktell_calls_sync_runs_dates
                ON oktell_calls_sync_runs (date_to, date_from);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_status_events (
                    id BIGSERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    event_at TIMESTAMP NOT NULL,
                    event_date DATE NOT NULL,
                    status_key VARCHAR(128) NOT NULL,
                    state_note VARCHAR(255) NULL,
                    event_kind VARCHAR(16) NOT NULL DEFAULT 'status' CHECK (event_kind IN ('status', 'action')),
                    imported_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    is_authoritative BOOLEAN NOT NULL DEFAULT FALSE
                );
            """)
            cursor.execute("""
                ALTER TABLE operator_status_events
                ADD COLUMN IF NOT EXISTS is_authoritative BOOLEAN NOT NULL DEFAULT FALSE;
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_status_segments (
                    id BIGSERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    status_date DATE NOT NULL,
                    start_at TIMESTAMP NOT NULL,
                    end_at TIMESTAMP NOT NULL,
                    duration_sec INTEGER NOT NULL,
                    status_key VARCHAR(128) NOT NULL,
                    state_note VARCHAR(255) NULL,
                    imported_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    is_authoritative BOOLEAN NOT NULL DEFAULT FALSE,
                    CHECK (end_at > start_at),
                    CHECK (duration_sec > 0)
                );
            """)
            # Некоторые источники (сейчас TEZ/Binotel) отдают готовые интервалы
            # start/stop. Их нельзя впоследствии растягивать пересборкой только
            # из start-событий, поэтому provenance хранится на durable-сегменте.
            cursor.execute("""
                ALTER TABLE operator_status_segments
                ADD COLUMN IF NOT EXISTS is_authoritative BOOLEAN NOT NULL DEFAULT FALSE;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_operator_status_events_operator_time
                ON operator_status_events(operator_id, event_at);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_operator_status_events_operator_date
                ON operator_status_events(operator_id, event_date);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_operator_status_segments_operator_date
                ON operator_status_segments(operator_id, status_date);
            """)
            # iCORE Phone (live-пуш статусов оператора): идемпотентность по
            # client_event_id. Телефон шлёт каждое событие с уникальным id и
            # ретраит из офлайн-очереди при разрывах сети — уникальный индекс не
            # даёт повтору задвоить событие. Колонка nullable: у исторических
            # событий (импорт CSV) её нет, индекс частичный (только NOT NULL).
            cursor.execute("""
                ALTER TABLE operator_status_events
                ADD COLUMN IF NOT EXISTS client_event_id VARCHAR(64) NULL;
            """)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_operator_status_events_client_event
                ON operator_status_events(operator_id, client_event_id)
                WHERE client_event_id IS NOT NULL;
            """)
            # NOTE: indexes idx_operator_status_events_imported_by,
            # idx_operator_status_segments_imported_by, idx_operator_status_segments_status_key
            # and idx_operator_status_segments_operator_start were intentionally removed:
            # no query filters/sorts on imported_by / status_key / start_at (start_at is only a
            # secondary ORDER BY key served by idx_..._operator_date). Do not re-add without a
            # query that actually uses them — they were pure dead weight (~78 MB combined).
            # Proactively drop them if an older deployment recreated them:
            cursor.execute("DROP INDEX IF EXISTS idx_operator_status_events_imported_by;")
            cursor.execute("DROP INDEX IF EXISTS idx_operator_status_segments_imported_by;")
            cursor.execute("DROP INDEX IF EXISTS idx_operator_status_segments_status_key;")
            cursor.execute("DROP INDEX IF EXISTS idx_operator_status_segments_operator_start;")

            # AI feedback cache table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_feedback_cache (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    month VARCHAR(7) NOT NULL,
                    feedback_data JSONB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, month)
                );
            """)

            # AI birthday greeting cache table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_birthday_greeting_cache (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    greeting_date DATE NOT NULL,
                    greeting_data JSONB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, greeting_date)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_birthday_greeting_cache_user_date
                ON ai_birthday_greeting_cache(user_id, greeting_date);
            """)

            # Recruiting parser runs + snapshots
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recruiting_parse_runs (
                    id SERIAL PRIMARY KEY,
                    run_uuid UUID UNIQUE NOT NULL,
                    source VARCHAR(64) NOT NULL DEFAULT 'enbek',
                    triggered_by VARCHAR(32) NOT NULL DEFAULT 'scheduler',
                    status VARCHAR(16) NOT NULL CHECK (status IN ('success', 'failed')),
                    total_items INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    started_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    finished_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recruiting_resumes (
                    id SERIAL PRIMARY KEY,
                    run_uuid UUID NOT NULL REFERENCES recruiting_parse_runs(run_uuid) ON DELETE CASCADE,
                    keyword_group VARCHAR(128),
                    keyword_query TEXT,
                    page_found INTEGER,
                    title TEXT,
                    category TEXT,
                    experience TEXT,
                    location TEXT,
                    salary TEXT,
                    education TEXT,
                    published_at TEXT,
                    detail_url TEXT,
                    scraped_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    UNIQUE (run_uuid, detail_url)
                );
            """)

            # Surveys tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS surveys (
                    id SERIAL PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    direction_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    tenure_weeks_min INTEGER,
                    tenure_weeks_max INTEGER,
                    repeat_root_id INTEGER REFERENCES surveys(id) ON DELETE SET NULL,
                    repeat_iteration INTEGER NOT NULL DEFAULT 1,
                    is_test BOOLEAN NOT NULL DEFAULT FALSE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    CHECK (tenure_weeks_min IS NULL OR tenure_weeks_min >= 0),
                    CHECK (tenure_weeks_max IS NULL OR tenure_weeks_max >= 0),
                    CHECK (tenure_weeks_min IS NULL OR tenure_weeks_max IS NULL OR tenure_weeks_min <= tenure_weeks_max),
                    CHECK (repeat_iteration >= 1)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS survey_questions (
                    id SERIAL PRIMARY KEY,
                    survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    question_text TEXT NOT NULL,
                    question_type VARCHAR(16) NOT NULL CHECK (question_type IN ('single', 'multiple', 'rating')),
                    is_required BOOLEAN NOT NULL DEFAULT TRUE,
                    allow_other BOOLEAN NOT NULL DEFAULT FALSE,
                    options_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    correct_options_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    UNIQUE (survey_id, position)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS survey_assignments (
                    id SERIAL PRIMARY KEY,
                    survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    assigned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned', 'in_progress', 'completed')),
                    assigned_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    completed_at TIMESTAMP,
                    UNIQUE (survey_id, operator_id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS survey_responses (
                    id SERIAL PRIMARY KEY,
                    survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    assignment_id INTEGER REFERENCES survey_assignments(id) ON DELETE SET NULL,
                    submitted_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    UNIQUE (survey_id, operator_id)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS survey_answers (
                    id SERIAL PRIMARY KEY,
                    response_id INTEGER NOT NULL REFERENCES survey_responses(id) ON DELETE CASCADE,
                    question_id INTEGER NOT NULL REFERENCES survey_questions(id) ON DELETE CASCADE,
                    answer_text TEXT,
                    selected_options_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    rating_value INTEGER,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    CHECK (rating_value IS NULL OR (rating_value >= 1 AND rating_value <= 5))
                );
            """)

            # ── Тест по расписанию (опрос в режиме теста) ────────────────────
            # Статус теста (Запланирован / Активен / Завершён) НЕ хранится: он
            # однозначно выводится из окна starts_at..ends_at, поэтому не может
            # разойтись с реальным временем и не требует cron-а на переключение.
            cursor.execute("ALTER TABLE surveys ADD COLUMN IF NOT EXISTS starts_at TIMESTAMP;")
            cursor.execute("ALTER TABLE surveys ADD COLUMN IF NOT EXISTS ends_at TIMESTAMP;")
            cursor.execute("""
                ALTER TABLE surveys
                ADD COLUMN IF NOT EXISTS group_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
            """)
            cursor.execute("""
                ALTER TABLE surveys
                ADD COLUMN IF NOT EXISTS single_attempt BOOLEAN NOT NULL DEFAULT TRUE;
            """)
            cursor.execute("""
                ALTER TABLE surveys
                ADD COLUMN IF NOT EXISTS affects_quality BOOLEAN NOT NULL DEFAULT FALSE;
            """)
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conrelid = 'surveys'::regclass
                          AND conname = 'surveys_schedule_window_valid'
                    ) THEN
                        ALTER TABLE surveys
                        ADD CONSTRAINT surveys_schedule_window_valid
                        CHECK (starts_at IS NULL OR ends_at IS NULL OR starts_at < ends_at);
                    END IF;
                END $$;
            """)
            # Баллы за вопрос: вес вопроса в итоговом результате теста.
            cursor.execute("""
                ALTER TABLE survey_questions
                ADD COLUMN IF NOT EXISTS points NUMERIC(6, 2) NOT NULL DEFAULT 1;
            """)
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conrelid = 'survey_questions'::regclass
                          AND conname = 'survey_questions_points_positive'
                    ) THEN
                        ALTER TABLE survey_questions
                        ADD CONSTRAINT survey_questions_points_positive CHECK (points > 0);
                    END IF;
                END $$;
            """)
            # Частичный зачёт вопроса с несколькими ответами: балл начисляется
            # пропорционально угаданным вариантам, но ЛЮБОЙ неверный вариант
            # обнуляет вопрос — иначе «отметить всё» давало бы полбалла.
            cursor.execute("""
                ALTER TABLE survey_questions
                ADD COLUMN IF NOT EXISTS partial_credit BOOLEAN NOT NULL DEFAULT FALSE;
            """)
            cursor.execute("""
                ALTER TABLE survey_assignments
                ADD COLUMN IF NOT EXISTS started_at TIMESTAMP;
            """)
            # Итог попытки считается один раз при отправке и хранится рядом с
            # ответами: статистика и передача в качество читают готовые числа,
            # а не пересчитывают их на каждый запрос.
            cursor.execute("""
                ALTER TABLE survey_responses
                ADD COLUMN IF NOT EXISTS earned_points NUMERIC(8, 2);
            """)
            cursor.execute("""
                ALTER TABLE survey_responses
                ADD COLUMN IF NOT EXISTS max_points NUMERIC(8, 2);
            """)
            cursor.execute("""
                ALTER TABLE survey_responses
                ADD COLUMN IF NOT EXISTS score_percent NUMERIC(5, 2);
            """)
            cursor.execute("""
                ALTER TABLE survey_responses
                ADD COLUMN IF NOT EXISTS is_auto_submitted BOOLEAN NOT NULL DEFAULT FALSE;
            """)
            cursor.execute("""
                ALTER TABLE survey_responses
                ADD COLUMN IF NOT EXISTS started_at TIMESTAMP;
            """)
            # Черновик начатой попытки: нужен, чтобы по окончании окна
            # автоотправка сохранила именно то, что оператор успел выбрать.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS survey_attempt_drafts (
                    id SERIAL PRIMARY KEY,
                    survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    answers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    started_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    UNIQUE (survey_id, operator_id)
                );
            """)
            # Результат теста — обычная запись журнала оценок (calls), как и
            # оценка чата: ссылка на попытку отличает её от прослушанного звонка.
            # Такие строки НЕ считаются оценёнными звонками (план прослушки
            # остаётся прежним), но входят в средний показатель качества.
            cursor.execute("""
                ALTER TABLE calls
                ADD COLUMN IF NOT EXISTS survey_response_id INTEGER
                    REFERENCES survey_responses(id) ON DELETE SET NULL;
            """)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_calls_survey_response
                ON calls(survey_response_id)
                WHERE survey_response_id IS NOT NULL;
            """)

            # ── Архив опросов ────────────────────────────────────────────────
            # Опрос живёт две недели: дальше он перестаёт быть «делом» —
            # уходит в архив, пропадает из счётчиков раздела и колокола и
            # больше не тревожит ни оператора, ни руководителя.
            #
            # Тест уходит туда же, но только когда его окно уже не идёт:
            # закончилось или не было задано вовсе. Идущий тест не архивируем —
            # его сейчас проходят.
            #
            # Отметка об отправке уведомления владельцу — отдельная колонка, а не
            # флаг «архивирован»: Telegram может не ответить, и тогда напоминание
            # должно уйти следующим прогоном, а не потеряться молча.
            cursor.execute("ALTER TABLE surveys ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP;")
            cursor.execute("ALTER TABLE surveys ADD COLUMN IF NOT EXISTS archive_notified_at TIMESTAMP;")

            # Tasks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    subject VARCHAR(255) NOT NULL,
                    description TEXT,
                    tag VARCHAR(32) NOT NULL DEFAULT 'task' CHECK (tag IN ('task', 'problem', 'suggestion', 'production')),
                    priority VARCHAR(16) NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal', 'urgent', 'critical')),
                    status VARCHAR(32) NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned', 'in_progress', 'completed', 'accepted', 'returned')),
                    assigned_to INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    deadline_duration_minutes INTEGER CHECK (deadline_duration_minutes IS NULL OR deadline_duration_minutes >= 0),
                    due_at TIMESTAMP,
                    is_regulation BOOLEAN NOT NULL DEFAULT FALSE,
                    recurrence_type VARCHAR(16) CHECK (recurrence_type IS NULL OR recurrence_type IN ('daily', 'weekly', 'monthly')),
                    recurrence_interval INTEGER CHECK (recurrence_interval IS NULL OR recurrence_interval >= 1),
                    recurrence_next_at TIMESTAMP,
                    regulation_parent_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
                    regulation_iteration INTEGER NOT NULL DEFAULT 0,
                    completion_summary TEXT,
                    completed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)

            # Tasks status history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_status_history (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    status_code VARCHAR(32) NOT NULL CHECK (status_code IN ('assigned', 'in_progress', 'completed', 'accepted', 'returned', 'reopened')),
                    changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    comment TEXT,
                    changed_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)

            # Task attachments (GCS storage; DB `file_data` kept for legacy reads)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_attachments (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    file_name TEXT NOT NULL,
                    content_type VARCHAR(255),
                    file_size INTEGER NOT NULL DEFAULT 0,
                    file_data BYTEA,
                    storage_type VARCHAR(16) NOT NULL DEFAULT 'gcs' CHECK (storage_type IN ('db', 'gcs')),
                    gcs_bucket VARCHAR(255),
                    gcs_blob_path TEXT,
                    attachment_kind VARCHAR(16) NOT NULL DEFAULT 'initial' CHECK (attachment_kind IN ('initial', 'result')),
                    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)

            cursor.execute("""
                ALTER TABLE tasks ADD COLUMN IF NOT EXISTS priority VARCHAR(16) NOT NULL DEFAULT 'normal'
                    CHECK (priority IN ('normal', 'urgent', 'critical'));
            """)
            cursor.execute("""
                ALTER TABLE tasks ADD COLUMN IF NOT EXISTS deadline_duration_minutes INTEGER
                    CHECK (deadline_duration_minutes IS NULL OR deadline_duration_minutes >= 0);
            """)
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS due_at TIMESTAMP;")
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS is_regulation BOOLEAN NOT NULL DEFAULT FALSE;")
            cursor.execute("""
                ALTER TABLE tasks ADD COLUMN IF NOT EXISTS recurrence_type VARCHAR(16)
                    CHECK (recurrence_type IS NULL OR recurrence_type IN ('daily', 'weekly', 'monthly'));
            """)
            cursor.execute("""
                ALTER TABLE tasks ADD COLUMN IF NOT EXISTS recurrence_interval INTEGER
                    CHECK (recurrence_interval IS NULL OR recurrence_interval >= 1);
            """)
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS recurrence_next_at TIMESTAMP;")
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS regulation_parent_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL;")
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS regulation_iteration INTEGER NOT NULL DEFAULT 0;")

            # Бэклог + канбан + таймлайн раздела «Задачи».
            # is_backlog — задача лежит в бэклоге (ещё не взята в работу, исполнителя не тревожим),
            # backlog_rank — ручной порядок приоритезации (DOUBLE, чтобы вставлять между соседями),
            # estimate_minutes / planned_start_at — плановая длительность и старт для таймлайна,
            # started_at — фактическое начало работ (первый переход в in_progress).
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS is_backlog BOOLEAN NOT NULL DEFAULT FALSE;")
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS backlog_rank DOUBLE PRECISION;")
            cursor.execute("""
                ALTER TABLE tasks ADD COLUMN IF NOT EXISTS estimate_minutes INTEGER
                    CHECK (estimate_minutes IS NULL OR estimate_minutes >= 0);
            """)
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS planned_start_at TIMESTAMP;")
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS started_at TIMESTAMP;")
            # Балл приоритета 1–5 (1 — важнее всего): ставят только суперадмины, в основном
            # на совещании по бэклогу; NULL — ещё не оценена. Бэклог идёт по баллу, внутри
            # балла — ручной очередью backlog_rank.
            cursor.execute("""
                ALTER TABLE tasks ADD COLUMN IF NOT EXISTS priority_score SMALLINT
                    CHECK (priority_score IS NULL OR priority_score BETWEEN 1 AND 5);
            """)
            # Источник задачи: кто её поручил, если это не тот, кто создал карточку
            # (устная просьба руководителя, запрос клиента и т.п.).
            # Оба поля пустые = своя инициатива.
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS requested_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL;")
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS requested_by_name VARCHAR(160);")
            # Напоминание в Telegram перед дедлайном: за сколько минут предупредить.
            # NULL = напоминание выключено; максимум сутки (по требованию — «максимум за день до»).
            # reminder_sent_at сбрасывается при смене срока или интервала, чтобы напомнить заново.
            cursor.execute("""
                ALTER TABLE tasks ADD COLUMN IF NOT EXISTS reminder_minutes_before INTEGER
                    CHECK (reminder_minutes_before IS NULL
                           OR (reminder_minutes_before >= 0 AND reminder_minutes_before <= 1440));
            """)
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMP;")
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_due_reminder
                ON tasks(due_at, reminder_minutes_before)
                WHERE due_at IS NOT NULL AND reminder_minutes_before IS NOT NULL;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_backlog_rank
                ON tasks(is_backlog, backlog_rank NULLS LAST, created_at DESC);
            """)
            # Под порядок бэклога: балл, затем ручная очередь (get_tasks_for_requester).
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_backlog_score
                ON tasks(is_backlog, priority_score NULLS LAST, backlog_rank NULLS LAST, created_at DESC);
            """)
            # Разовый бэкфилл фактического старта по истории статусов (идемпотентен).
            cursor.execute("""
                UPDATE tasks t
                SET started_at = h.first_started
                FROM (
                    SELECT task_id, MIN(changed_at) AS first_started
                    FROM task_status_history
                    WHERE status_code = 'in_progress'
                    GROUP BY task_id
                ) h
                WHERE h.task_id = t.id AND t.started_at IS NULL;
            """)

            # Task checklist items
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_checklist_items (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    title VARCHAR(255) NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    is_required BOOLEAN NOT NULL DEFAULT TRUE,
                    is_done BOOLEAN NOT NULL DEFAULT FALSE,
                    completed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    UNIQUE (task_id, position)
                );
            """)
            cursor.execute("ALTER TABLE task_checklist_items ADD COLUMN IF NOT EXISTS result_note TEXT;")

            # Отчёты о проделанной работе: журнал по задаче.
            # 'progress' — промежуточный отчёт (можно писать по ходу), 'completion' — итоговый при сдаче.
            # spent_minutes — фактические трудозатраты; сумма по отчётам даёт факт против estimate_minutes.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_reports (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    kind VARCHAR(16) NOT NULL DEFAULT 'progress'
                        CHECK (kind IN ('progress', 'completion')),
                    body TEXT NOT NULL,
                    spent_minutes INTEGER CHECK (spent_minutes IS NULL OR spent_minutes >= 0),
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_reports_task ON task_reports(task_id, created_at ASC, id ASC);")
            # Старые «Итоги выполнения» переносим в журнал, чтобы он не начинался с пустоты (идемпотентно).
            cursor.execute("""
                INSERT INTO task_reports (task_id, author_id, kind, body, created_at, updated_at)
                SELECT
                    t.id,
                    COALESCE(t.completed_by, t.assigned_to),
                    'completion',
                    t.completion_summary,
                    COALESCE(t.completed_at, t.updated_at),
                    COALESCE(t.completed_at, t.updated_at)
                FROM tasks t
                WHERE COALESCE(NULLIF(TRIM(t.completion_summary), ''), NULL) IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM task_reports r
                      WHERE r.task_id = t.id AND r.kind = 'completion'
                  );
            """)

            # Уточнения по задаче: постановщик дополняет постановку, исполнитель
            # просит то, чего не хватает. Отдельная лента, а не task_reports:
            # отчёт — это «что я сделал» с трудозатратами, уточнение — переписка
            # по самой постановке, и мешать их в одном журнале значит смешать
            # факт работы с обсуждением задания.
            #   note     — постановщик дополнил задачу (можно в любом статусе);
            #   request  — исполнителю не хватает информации;
            #   answer   — ответ на запрос, он же его закрывает.
            # Лента только пополняется: править и удалять чужие (и свои) слова
            # по постановке нельзя — на них ссылаются и по ним работают.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_messages (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    kind VARCHAR(16) NOT NULL DEFAULT 'note'
                        CHECK (kind IN ('note', 'request', 'answer')),
                    body TEXT NOT NULL,
                    -- Запрос информации живёт «открытым», пока на него не ответили
                    -- или пока автор его не отозвал; reply_to_id связывает ответ
                    -- с запросом, чтобы в ленте было видно, на что отвечали.
                    reply_to_id INTEGER REFERENCES task_messages(id) ON DELETE SET NULL,
                    resolved_at TIMESTAMP,
                    resolved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_messages_task ON task_messages(task_id, created_at ASC, id ASC);")
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_messages_open_request
                ON task_messages(task_id)
                WHERE kind = 'request' AND resolved_at IS NULL;
            """)

            # Открытый запрос информации денормализован в саму задачу: по нему
            # считаются бейдж «ждут вас», колокол и чип на карточке, а все три
            # смотрят только в tasks. Плюс UPDATE задачи сам будит триггер
            # колокола (trg_bell_tasks) — отдельный триггер на ленту не нужен.
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS info_request_id INTEGER REFERENCES task_messages(id) ON DELETE SET NULL;")
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS info_request_at TIMESTAMP;")
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_info_request
                ON tasks(info_request_at)
                WHERE info_request_id IS NOT NULL;
            """)

            # Файл уточнения лежит там же, где файлы постановки (attachment_kind
            # остаётся 'initial'): так его видят и выгрузка, и CLI, и блок
            # «Файлы задачи». message_id говорит, что файл приехал с уточнением,
            # — карточка показывает его в самом уточнении и не дублирует выше.
            cursor.execute("ALTER TABLE task_attachments ADD COLUMN IF NOT EXISTS message_id INTEGER REFERENCES task_messages(id) ON DELETE SET NULL;")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_attachments_message ON task_attachments(message_id) WHERE message_id IS NOT NULL;")

            # Миниатюра фотографии для плитки в карточке (task_photos). NULL —
            # не картинка, картинка загружена до предпросмотра или кадр меньше
            # миниатюры: плитка тогда показывает сам файл.
            cursor.execute("ALTER TABLE task_attachments ADD COLUMN IF NOT EXISTS thumb_blob_path TEXT;")

            # ──────────────────────────────────────────────────────────────
            # ПРОДАКШН (задача #354): тип задачи для отдела маркетинга и бриф
            # к нему — что сделать, где разместят, размеры, цветность. Бриф —
            # колонкой, а не таблицей: он читается всегда целиком вместе с
            # задачей, и список задач остаётся одним запросом. NULL у всех
            # задач, кроме продакшна; коды вариантов проверяет task_production.
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS production_brief JSONB;")
            # Новый тип в CHECK. Меняем ограничение, только если в нём ещё нет
            # 'production': иначе каждый старт приложения брал бы на tasks
            # эксклюзивную блокировку и заново проверял все строки.
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                         WHERE conrelid = 'tasks'::regclass
                           AND conname = 'tasks_tag_check'
                           AND position('production' IN pg_get_constraintdef(oid)) > 0
                    ) THEN
                        ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_tag_check;
                        ALTER TABLE tasks ADD CONSTRAINT tasks_tag_check
                            CHECK (tag IN ('task', 'problem', 'suggestion', 'production'));
                    END IF;
                END $$;
            """)

            # Материалы продакшна (задача #316): ролики, баннеры и макеты по
            # версиям, загрузка прямо в хранилище. Схема — task_production/schema.py.
            for statement in task_production_schema.STATEMENTS:
                cursor.execute(statement)

            # ──────────────────────────────────────────────────────────────
            # СОСТАВ ИСПОЛНИТЕЛЕЙ. У задачи их может быть несколько, и права у
            # них равные: каждый берёт задачу в работу, отмечает пункты чеклиста,
            # пишет отчёты и сдаёт результат.
            #
            # Скалярная tasks.assigned_to СОХРАНЕНА и означает «первый
            # исполнитель». Это не дубль ради удобства, а осознанный выбор:
            # колонка задействована почти в трёхсот местах (SQL-джойны раздела,
            # триггер колокола, Telegram-бот, выгрузка в Excel, CLI), она NOT NULL,
            # и снос её означал бы переписывание раздела целиком. Ровно так же
            # устроены «ответственный + соисполнители» в готовых трекерах.
            #
            # Инвариант, на который опирается весь раздел: task_assignees всегда
            # содержит и tasks.assigned_to — это строка с наименьшим position.
            # Держится он в одном месте, _sync_task_assignees_tx.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_assignees (
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    -- Порядок показа. Прав не даёт и не отнимает: нужен только
                    -- чтобы люди не прыгали местами между обновлениями списка.
                    position INTEGER NOT NULL DEFAULT 0,
                    added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    added_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    PRIMARY KEY (task_id, user_id)
                );
            """)
            # Напоминание перед дедлайном отмечается ПО ЧЕЛОВЕКУ, а не по задаче.
            # Отметка на задаче тут не годится: рассылка идёт по строке на
            # каждого исполнителя, и успех первого закрывал бы задачу целиком —
            # тот, кому сообщение не ушло (бот заблокирован, чат недоступен),
            # не получил бы своё напоминание уже никогда. tasks.reminder_sent_at
            # остаётся как «когда напомнили в первый раз» и уходит в карточку.
            cursor.execute("ALTER TABLE task_assignees ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMP;")

            # Фильтр «мои задачи», доска сотрудника и счётчик «ждут вас» ходят
            # через EXISTS по (user_id, task_id) — без этого индекса они уедут в
            # seq scan по всей таблице задач.
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_assignees_user
                ON task_assignees(user_id, task_id);
            """)
            # Бэкфилл: у каждой существующей задачи её единственный исполнитель
            # становится первым в составе. Идемпотентно — ON CONFLICT.
            cursor.execute("""
                INSERT INTO task_assignees (task_id, user_id, position, added_by, added_at)
                SELECT t.id, t.assigned_to, 0, t.created_by, t.created_at
                FROM tasks t
                WHERE t.assigned_to IS NOT NULL
                ON CONFLICT (task_id, user_id) DO NOTHING;
            """)

            # ──────────────────────────────────────────────────────────────
            # ПРОЕКТЫ-ДОСКИ (задача #401): у отдела своя доска и свой ключ на
            # латинице — HR-125, FIN-84. Номер в ключе — свой у проекта
            # (tasks.project_number); глобальный tasks.id остаётся, на него
            # ссылаются Telegram, коммиты, CLI и старые ссылки. Правила и
            # стартовый список — task_projects.py.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_projects (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(10) NOT NULL UNIQUE CHECK (code ~ '^[A-Z][A-Z0-9]{1,9}$'),
                    name VARCHAR(120) NOT NULL,
                    -- Привязка к отделу подставляет доску по умолчанию, и только.
                    -- Видимость решает состав, а не отдел.
                    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
                    -- Следующий номер задачи. Берётся UPDATE ... RETURNING в той же
                    -- транзакции, что и вставка задачи: строка проекта блокируется,
                    -- и две задачи, поставленные в одну секунду, номер не делят.
                    next_number INTEGER NOT NULL DEFAULT 1 CHECK (next_number >= 1),
                    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            # Состав доски. member — участник: видит все задачи проекта; board —
            # доступ к доске: ставит задачи в проект, а видит свои и те, где он
            # наблюдатель. Права на действия ни один уровень не даёт.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_project_members (
                    project_id INTEGER NOT NULL REFERENCES task_projects(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    access_level VARCHAR(16) NOT NULL DEFAULT 'member'
                        CHECK (access_level IN ('member', 'board')),
                    added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    added_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    PRIMARY KEY (project_id, user_id)
                );
            """)
            # Охват списка задач спрашивает «в каких проектах я» по каждой строке.
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_project_members_user
                ON task_project_members(user_id, project_id);
            """)
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES task_projects(id) ON DELETE SET NULL;")
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS project_number INTEGER;")
            # Один номер — одна задача проекта. Индекс же держит и фильтр доски
            # по проекту: project_id в нём первым.
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_project_number
                ON tasks(project_id, project_number)
                WHERE project_id IS NOT NULL;
            """)
            # Наблюдатели задачи. Видят её, только пока у них есть доступ к доске
            # проекта: это условие стоит в самой выборке, а не только при
            # добавлении, — сняли с доски, и задача пропала сама.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_watchers (
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    added_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    PRIMARY KEY (task_id, user_id)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_watchers_user
                ON task_watchers(user_id, task_id);
            """)
            # Прежние ключи задач, перенесённых в другой проект: ссылка HR-125,
            # отправленная до переноса, должна открывать ту же задачу. Номер в
            # проекте не переиспользуется (next_number только растёт), поэтому
            # старый ключ ни с кем не столкнётся. Сюда же — прежние ключи всех задач
            # доски, которой сменили ключ. project_id — чей это был ключ: свой прежний
            # ключ доска вернуть себе может, чужой — нет (_rename_task_project_code_tx).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_key_aliases (
                    code VARCHAR(10) NOT NULL,
                    number INTEGER NOT NULL,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    project_id INTEGER REFERENCES task_projects(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    PRIMARY KEY (code, number)
                );
            """)
            # Таблица старше колонки (стенды #401): без владельца ключ считается чужим.
            cursor.execute("""
                ALTER TABLE task_key_aliases
                ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES task_projects(id) ON DELETE SET NULL;
            """)
            # Разделы доски («регулируемый набор разделов» из постановки #401):
            # у каждой доски свои группы задач — «Подбор», «Адаптация» у HR.
            # Колонки доски остаются статусами (на них держатся приёмка и
            # уведомления); раздел — это «о чём задача», а не «на каком она шаге».
            # Удалили раздел — его задачи остаются на доске, просто без раздела.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_project_sections (
                    id SERIAL PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES task_projects(id) ON DELETE CASCADE,
                    name VARCHAR(80) NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_project_sections_project
                ON task_project_sections(project_id, position);
            """)
            cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS section_id INTEGER REFERENCES task_project_sections(id) ON DELETE SET NULL;")
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_section
                ON tasks(section_id) WHERE section_id IS NOT NULL;
            """)
            # Стартовые проекты — только на пустой таблице: дальше список ведут
            # в разделе, и переименованный или убранный в архив проект при
            # следующем старте приложения не воскресает и не меняется.
            seed_values = ", ".join(["(%s, %s, %s, %s)"] * len(task_projects.SEED_PROJECTS))
            seed_params = []
            for order, (seed_code, seed_name, seed_department) in enumerate(task_projects.SEED_PROJECTS):
                seed_params.extend([seed_code, seed_name, seed_department, order])
            cursor.execute(f"""
                INSERT INTO task_projects (code, name, department_id)
                SELECT seed.code, seed.name,
                       (SELECT d.id FROM departments d WHERE d.code = seed.department_code ORDER BY d.id LIMIT 1)
                  FROM (VALUES {seed_values}) AS seed(code, name, department_code, ord)
                 WHERE NOT EXISTS (SELECT 1 FROM task_projects)
                 ORDER BY seed.ord
                ON CONFLICT (code) DO NOTHING;
            """, tuple(seed_params))

            # Отметка «уведомление просмотрено»: гасит счётчик «ждут вас», но не задачу.
            # Ключ — пользователь+задача; kind и seen_at нужны, чтобы отметка сгорала,
            # когда причина сменилась (не начата → просрочена) или задачу тронули снова.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_action_reads (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    kind VARCHAR(16) NOT NULL,
                    seen_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    PRIMARY KEY (user_id, task_id)
                );
            """)

            # Personal notes inside the Tasks section
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_notes (
                    id SERIAL PRIMARY KEY,
                    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title VARCHAR(160) NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    priority VARCHAR(16) NOT NULL DEFAULT 'normal'
                        CHECK (priority IN ('normal', 'urgent', 'critical')),
                    due_at TIMESTAMP,
                    is_task BOOLEAN NOT NULL DEFAULT FALSE,
                    is_done BOOLEAN NOT NULL DEFAULT FALSE,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_notes_owner_updated ON task_notes(owner_id, updated_at DESC);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_notes_owner_due ON task_notes(owner_id, due_at);")
            # Напоминание в Telegram по дедлайну заметки — та же механика, что у задач.
            cursor.execute("""
                ALTER TABLE task_notes ADD COLUMN IF NOT EXISTS reminder_minutes_before INTEGER
                    CHECK (reminder_minutes_before IS NULL
                           OR (reminder_minutes_before >= 0 AND reminder_minutes_before <= 1440));
            """)
            cursor.execute("ALTER TABLE task_notes ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMP;")
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_notes_due_reminder
                ON task_notes(due_at, reminder_minutes_before)
                WHERE due_at IS NOT NULL AND reminder_minutes_before IS NOT NULL;
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS raw_resource_uploads (
                    id SERIAL PRIMARY KEY,
                    report_date DATE NOT NULL UNIQUE,
                    filename TEXT,
                    content_sha256 VARCHAR(64),
                    headers JSONB NOT NULL DEFAULT '[]',
                    row_count INTEGER NOT NULL DEFAULT 0,
                    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    uploaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_resource_hours (
                    id SERIAL PRIMARY KEY,
                    report_date DATE NOT NULL,
                    hour INTEGER NOT NULL CHECK (hour >= 0 AND hour <= 23),
                    received_calls INTEGER NOT NULL DEFAULT 0,
                    accepted_calls INTEGER NOT NULL DEFAULT 0,
                    lost_calls INTEGER NOT NULL DEFAULT 0,
                    no_answer_rate NUMERIC(10,6) NOT NULL DEFAULT 0,
                    talk_time_seconds INTEGER NOT NULL DEFAULT 0,
                    avg_talk_seconds NUMERIC(12,4) NOT NULL DEFAULT 0,
                    success_wait_seconds INTEGER NOT NULL DEFAULT 0,
                    avg_success_wait_seconds NUMERIC(12,4) NOT NULL DEFAULT 0,
                    total_time_seconds INTEGER NOT NULL DEFAULT 0,
                    greeting_abandoned INTEGER NOT NULL DEFAULT 0,
                    greeting_time_seconds INTEGER NOT NULL DEFAULT 0,
                    queue_abandoned INTEGER NOT NULL DEFAULT 0,
                    queue_wait_seconds INTEGER NOT NULL DEFAULT 0,
                    avg_lost_wait_seconds NUMERIC(12,4) NOT NULL DEFAULT 0,
                    avg_wait_seconds NUMERIC(12,4) NOT NULL DEFAULT 0,
                    forecast_calls NUMERIC(12,4) NOT NULL DEFAULT 0,
                    forecast_fte NUMERIC(12,4) NOT NULL DEFAULT 0,
                    planned_fte NUMERIC(12,4) NOT NULL DEFAULT 0,
                    actual_fte NUMERIC(12,4) NOT NULL DEFAULT 0,
                    fact_forecast_delta NUMERIC(12,4) NOT NULL DEFAULT 0,
                    comments TEXT,
                    raw_payload JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(report_date, hour)
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_resource_summary (
                    report_date DATE PRIMARY KEY,
                    weekday INTEGER NOT NULL CHECK (weekday >= 0 AND weekday <= 6),
                    total_received INTEGER NOT NULL DEFAULT 0,
                    total_accepted INTEGER NOT NULL DEFAULT 0,
                    total_lost INTEGER NOT NULL DEFAULT 0,
                    no_answer_rate NUMERIC(10,6) NOT NULL DEFAULT 0,
                    avg_talk_seconds NUMERIC(12,4) NOT NULL DEFAULT 0,
                    avg_wait_seconds NUMERIC(12,4) NOT NULL DEFAULT 0,
                    forecast_fte_total NUMERIC(12,4) NOT NULL DEFAULT 0,
                    planned_fte_total NUMERIC(12,4) NOT NULL DEFAULT 0,
                    actual_fte_total NUMERIC(12,4) NOT NULL DEFAULT 0,
                    fact_forecast_delta_total NUMERIC(12,4) NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS resource_settings (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    aht_seconds NUMERIC(12,4) NOT NULL DEFAULT 239,
                    answer_rate NUMERIC(8,6) NOT NULL DEFAULT 0.95,
                    occ NUMERIC(8,6) NOT NULL DEFAULT 0.70,
                    ur NUMERIC(8,6) NOT NULL DEFAULT 0.95,
                    shrinkage_coeff NUMERIC(8,6) NOT NULL DEFAULT 0.90,
                    weekly_hours_per_operator NUMERIC(8,2) NOT NULL DEFAULT 40,
                    fte_rounding VARCHAR(20) NOT NULL DEFAULT 'none',
                    shift_rounding VARCHAR(20) NOT NULL DEFAULT 'ceil',
                    selected_direction_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT resource_settings_singleton CHECK (id = 1),
                    CONSTRAINT resource_settings_fte_rounding_check CHECK (fte_rounding IN ('none', 'ceil', 'floor', 'round')),
                    CONSTRAINT resource_settings_shift_rounding_check CHECK (shift_rounding IN ('none', 'ceil', 'floor', 'round'))
                );
            """)
            cursor.execute("""
                INSERT INTO resource_settings (id)
                VALUES (1)
                ON CONFLICT (id) DO NOTHING;
            """)
            # Расчет ресурсов для ЧАТА. Отдельная singleton-таблица, а не колонки в
            # resource_settings: у чата другая модель — среднего времени обработки нет
            # вообще, вместо него цель по «ответу внутри чата» и ёмкость (чатов в час
            # на одного чатника). Смешивать вводные линии и чата в одной строке нельзя:
            # у них разный смысл и их правят разные люди.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS resource_chat_settings (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    target_reply_seconds INTEGER NOT NULL DEFAULT 300,
                    capacity_per_hour NUMERIC(8,2) NOT NULL DEFAULT 17,
                    shrinkage_coeff NUMERIC(8,6) NOT NULL DEFAULT 0.90,
                    weekly_hours_per_operator NUMERIC(8,2) NOT NULL DEFAULT 40,
                    base_weeks INTEGER NOT NULL DEFAULT 2,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT resource_chat_settings_singleton CHECK (id = 1),
                    CONSTRAINT resource_chat_settings_target_check
                        CHECK (target_reply_seconds BETWEEN 30 AND 3600),
                    CONSTRAINT resource_chat_settings_capacity_check
                        CHECK (capacity_per_hour > 0 AND capacity_per_hour <= 60),
                    CONSTRAINT resource_chat_settings_base_weeks_check
                        CHECK (base_weeks BETWEEN 1 AND 8)
                );
            """)
            cursor.execute("""
                INSERT INTO resource_chat_settings (id)
                VALUES (1)
                ON CONFLICT (id) DO NOTHING;
            """)
            # «Биллинг Oktell → Группировка»: комментарий к часу дня («во время поступления
            # звонка оператор находился в разговоре»). Хранится ПО ЧАСУ, а не отрезком:
            # объединённая ячейка на экране и в Excel — это подряд идущие часы с одним
            # текстом, и перекрывающихся отрезков тогда не бывает по построению. Звонки
            # Oktell у нас не хранятся, поэтому ключ — дата и час, без ссылки на отчёт.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS oktell_billing_hour_comments (
                    report_date DATE NOT NULL,
                    hour SMALLINT NOT NULL,
                    comment TEXT NOT NULL,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (report_date, hour),
                    CONSTRAINT oktell_billing_hour_comments_hour_check CHECK (hour BETWEEN 0 AND 23)
                );
            """)
            # Ёмкость чата больше не вводится руками: она выводится из цели по ответу
            # внутри чата через снятую по нашим данным кривую exp(a + b × нагрузка),
            # поэтому её коэффициенты приезжают с дефолтами калибровки. Кривая ПЕРВОГО
            # ответа — отдельная и остаётся NULL до замера: выдуманный коэффициент молча
            # зажал бы штат под цель, которая на реальной кривой может быть недостижима.
            # capacity_manual — аварийный рычаг: пока он NULL, ёмкость считается из цели.
            cursor.execute("""
                ALTER TABLE resource_chat_settings
                ADD COLUMN IF NOT EXISTS target_first_reply_seconds INTEGER DEFAULT 60;
            """)
            cursor.execute("""
                ALTER TABLE resource_chat_settings
                ADD COLUMN IF NOT EXISTS capacity_curve_a REAL DEFAULT 4.7047;
            """)
            cursor.execute("""
                ALTER TABLE resource_chat_settings
                ADD COLUMN IF NOT EXISTS capacity_curve_b REAL DEFAULT 0.0578;
            """)
            cursor.execute("""
                ALTER TABLE resource_chat_settings
                ADD COLUMN IF NOT EXISTS first_reply_curve_a REAL;
            """)
            cursor.execute("""
                ALTER TABLE resource_chat_settings
                ADD COLUMN IF NOT EXISTS first_reply_curve_b REAL;
            """)
            cursor.execute("""
                ALTER TABLE resource_chat_settings
                ADD COLUMN IF NOT EXISTS first_reply_curve_fitted_at TIMESTAMPTZ;
            """)
            cursor.execute("""
                ALTER TABLE resource_chat_settings
                ADD COLUMN IF NOT EXISTS capacity_manual REAL;
            """)
            cursor.execute("""
                ALTER TABLE resource_chat_settings
                ADD COLUMN IF NOT EXISTS fte_rounding TEXT DEFAULT 'half';
            """)
            # Прогноз чата за прошедший час рядом с фактом того же часа. Без хранения
            # истории нельзя ни посчитать наплыв (ФАКТ/ПРОГНОЗ по часам последних дней),
            # ни показать, выдержал ли прогноз. Факт часов — онлайн-сегменты чатников,
            # а не график смен, поэтому величина дробная. Уникальность (дата, час)
            # делает пересчёт идемпотентным: он переписывает строку, а не плодит дубли.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_resource_hours (
                    id BIGSERIAL PRIMARY KEY,
                    report_date DATE NOT NULL,
                    hour SMALLINT NOT NULL CHECK (hour BETWEEN 0 AND 23),
                    forecast_chats REAL NOT NULL DEFAULT 0,
                    forecast_fte REAL NOT NULL DEFAULT 0,
                    actual_chats INTEGER NOT NULL DEFAULT 0,
                    actual_online_hours REAL NOT NULL DEFAULT 0,
                    answered_in_target INTEGER NOT NULL DEFAULT 0,
                    answered_total INTEGER NOT NULL DEFAULT 0,
                    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (report_date, hour)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_resource_hours_date
                ON chat_resource_hours(report_date);
            """)
            # Планы графиков теперь общие для линии и чата, поэтому им нужен
            # дискриминатор направления. DEFAULT 'line' — все существующие планы
            # (и вся механика аукциона, что на них завязана) остаются линией.
            cursor.execute("""
                ALTER TABLE resource_saved_schedule_plans
                ADD COLUMN IF NOT EXISTS direction_mode VARCHAR(10) NOT NULL DEFAULT 'line';
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_resource_saved_schedule_plans_mode_period
                ON resource_saved_schedule_plans(direction_mode, date_from, date_to, archived_at);
            """)
            # Настройки распределения звонков на оценку («Деление звонков»): фильтр длительности
            # + флаг автораспределения. Singleton + аудит (кто/когда), история изменений отдельно.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS call_distribution_settings (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    min_duration_sec INTEGER NOT NULL DEFAULT 0,
                    max_duration_sec INTEGER NOT NULL DEFAULT 3600,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    CONSTRAINT call_distribution_settings_singleton CHECK (id = 1)
                );
            """)
            cursor.execute("""
                INSERT INTO call_distribution_settings (id)
                VALUES (1)
                ON CONFLICT (id) DO NOTHING;
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS call_distribution_settings_history (
                    id SERIAL PRIMARY KEY,
                    changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    changed_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    settings JSONB NOT NULL DEFAULT '{}'::jsonb
                );
            """)
            # Настройки SIP для iCORE Phone: общий сервер/домен + база пароля.
            # Пароль оператора = base_password + users.sip_number. Singleton + аудит.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sip_config (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    sip_server VARCHAR(255) NOT NULL DEFAULT '',
                    base_password VARCHAR(255) NOT NULL DEFAULT '',
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    CONSTRAINT sip_config_singleton CHECK (id = 1)
                );
            """)
            cursor.execute("""
                INSERT INTO sip_config (id)
                VALUES (1)
                ON CONFLICT (id) DO NOTHING;
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sip_config_history (
                    id SERIAL PRIMARY KEY,
                    changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    changed_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    settings JSONB NOT NULL DEFAULT '{}'::jsonb
                );
            """)
            # Отбивка показателей «Табло СЗоВ» в Telegram: в какой чат слать и включена ли.
            # Singleton + аудит — как sip_config: чат меняют редко, но знать, кто и когда
            # переключил, обязательно (отбивка уходит руководству).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS szov_wallboard_broadcast_config (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    chat_id BIGINT,
                    chat_title VARCHAR(255) NOT NULL DEFAULT '',
                    is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    CONSTRAINT szov_wallboard_broadcast_config_singleton CHECK (id = 1)
                );
            """)
            cursor.execute("""
                INSERT INTO szov_wallboard_broadcast_config (id)
                VALUES (1)
                ON CONFLICT (id) DO NOTHING;
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS szov_wallboard_broadcast_history (
                    id SERIAL PRIMARY KEY,
                    changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    changed_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    settings JSONB NOT NULL DEFAULT '{}'::jsonb
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_szov_wallboard_broadcast_history_changed_at
                ON szov_wallboard_broadcast_history(changed_at DESC);
            """)
            # Аудит тоже разводим по направлениям: в модалке «Чата» незачем видеть правки
            # получателей «Линии» — это разные списки и разные люди их ведут.
            cursor.execute("""
                ALTER TABLE szov_wallboard_broadcast_history
                ADD COLUMN IF NOT EXISTS direction VARCHAR(16) NOT NULL DEFAULT 'osnova';
            """)
            # Получатели отбивки. Раньше чат был один (singleton выше), но одному отделу
            # нужна каждая отбивка, а руководству — только когда показатели вышли из нормы,
            # поэтому получателей стало несколько и у каждого свой режим:
            #   always     — каждая плановая отбивка;
            #   deviations — то же расписание, но письмо уходит, только если есть отклонения.
            # Направление — второй ключ: направлений у табло два («Линия» и «Чат»),
            # показатели и расписание у них свои, и один чат вправе получать обе отбивки
            # или только одну. Ключ 'osnova' — историческое имя «Линии»: подпись на экране
            # сменили, а ключ трогать нельзя — по нему уже лежат строки на проде.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS szov_wallboard_broadcast_chats (
                    direction VARCHAR(16) NOT NULL DEFAULT 'osnova',
                    chat_id BIGINT NOT NULL,
                    chat_title VARCHAR(255) NOT NULL DEFAULT '',
                    mode VARCHAR(16) NOT NULL DEFAULT 'always',
                    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    PRIMARY KEY (direction, chat_id),
                    CONSTRAINT szov_wallboard_broadcast_chats_mode
                        CHECK (mode IN ('always', 'deviations')),
                    CONSTRAINT szov_wallboard_broadcast_chats_direction
                        CHECK (direction IN ('osnova', 'chat', 'op', 'tez', 'op_chat'))
                );
            """)
            # Группа, по которой чат получает отбивку «Табло ОП» (владелец 23.09.2026): чату
            # «Основы» — показатели «Основы», руководству — всего отдела. NULL — весь отдел,
            # как было. Внешнего ключа на groups нет намеренно: группу расформируют, а строка
            # получателя должна остаться и просто начать получать весь отдел.
            cursor.execute("""
                ALTER TABLE szov_wallboard_broadcast_chats
                ADD COLUMN IF NOT EXISTS group_id INTEGER;
            """)
            # Таблица старше направлений: там ключом был один chat_id. Досыпаем колонку и
            # переводим ключ на составной — иначе второе направление не смогло бы писать в
            # тот же чат, что и первое (ON CONFLICT (chat_id) молча затирал бы соседа).
            # Порядок важен: перевод ключа обязан пройти ДО одноразового переезда из
            # singleton-config ниже — тот уже пишет с ON CONFLICT (direction, chat_id).
            # Под SAVEPOINT: весь _init_db идёт ОДНОЙ транзакцией, и упавшая смена ключа
            # откатила бы инициализацию всей схемы — то есть уронила бы приложение целиком
            # ради одной таблицы отбивки. Здесь цена отказа честнее: получатели читаются,
            # а сохранение вернёт 500 с этой ошибкой в логе.
            try:
                cursor.execute("SAVEPOINT sp_szov_broadcast_direction")
                cursor.execute("""
                    ALTER TABLE szov_wallboard_broadcast_chats
                    ADD COLUMN IF NOT EXISTS direction VARCHAR(16) NOT NULL DEFAULT 'osnova';
                """)
                # Имя старого ключа НЕ зашиваем, а находим: Postgres назвал бы его
                # szov_wallboard_broadcast_chats_pkey, но полагаться на это ради DROP
                # CONSTRAINT незачем — ошибка в имени уронила бы миграцию на проде.
                # Склейка через quote_ident, а не format('%I'): процент в SQL — это
                # placeholder psycopg2, и один запрос с параметрами рядом сделал бы его
                # ловушкой на пустом месте.
                cursor.execute("""
                    DO $$
                    DECLARE
                        single_key_pk text;
                    BEGIN
                        SELECT con.conname INTO single_key_pk
                        FROM pg_constraint con
                        JOIN pg_index i ON i.indexrelid = con.conindid
                        WHERE con.conrelid = 'szov_wallboard_broadcast_chats'::regclass
                          AND con.contype = 'p'
                          AND i.indnkeyatts = 1;
                        IF single_key_pk IS NOT NULL THEN
                            EXECUTE 'ALTER TABLE szov_wallboard_broadcast_chats DROP CONSTRAINT '
                                || quote_ident(single_key_pk);
                            ALTER TABLE szov_wallboard_broadcast_chats
                                ADD PRIMARY KEY (direction, chat_id);
                        END IF;
                        -- Список направлений растёт («Табло ОП» — третье, 16.09.2026;
                        -- «Тез КЦ» — четвёртое, 22.09.2026; «Табло ОП · Чат» — пятое,
                        -- 25.09.2026). Ограничение, заведённое старым
                        -- кодом с коротким списком, снимаем и заводим заново; ищем старое по
                        -- отсутствию САМОГО СВЕЖЕГО ключа в его определении, а не по имени
                        -- версии. Добавляешь направление — меняй и ключ поиска здесь, иначе
                        -- прежний CHECK переживёт деплой и отвергнет новое значение.
                        IF EXISTS (
                            SELECT 1 FROM pg_constraint
                            WHERE conrelid = 'szov_wallboard_broadcast_chats'::regclass
                              AND conname = 'szov_wallboard_broadcast_chats_direction'
                              AND position('''op_chat''' IN pg_get_constraintdef(oid)) = 0
                        ) THEN
                            ALTER TABLE szov_wallboard_broadcast_chats
                                DROP CONSTRAINT szov_wallboard_broadcast_chats_direction;
                        END IF;
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint
                            WHERE conrelid = 'szov_wallboard_broadcast_chats'::regclass
                              AND conname = 'szov_wallboard_broadcast_chats_direction'
                        ) THEN
                            ALTER TABLE szov_wallboard_broadcast_chats
                                ADD CONSTRAINT szov_wallboard_broadcast_chats_direction
                                CHECK (direction IN ('osnova', 'chat', 'op', 'tez', 'op_chat'));
                        END IF;
                    END $$;
                """)
                # Переезд с одного чата на список — ровно один раз. Флаг нужен, чтобы
                # удалённый получатель не воскресал на каждом рестарте; строку в config при
                # этом НЕ трогаем, чтобы откат деплоя на прежний код продолжал слать в тот
                # же чат.
                # Стоит внутри того же SAVEPOINT, и это важно: INSERT ниже опирается и на
                # колонку direction, и на составной ключ (ON CONFLICT). Не пройди смена
                # ключа — он упал бы уже при планировании, даже когда переносить нечего, и
                # уволок бы за собой инициализацию всей схемы.
                cursor.execute("""
                    ALTER TABLE szov_wallboard_broadcast_config
                    ADD COLUMN IF NOT EXISTS chats_migrated BOOLEAN NOT NULL DEFAULT FALSE;
                """)
                cursor.execute("""
                    INSERT INTO szov_wallboard_broadcast_chats
                        (direction, chat_id, chat_title, mode, is_enabled, updated_by, updated_at)
                    SELECT 'osnova', chat_id, chat_title, 'always',
                           is_enabled, updated_by, updated_at
                    FROM szov_wallboard_broadcast_config
                    WHERE id = 1 AND chat_id IS NOT NULL AND NOT chats_migrated
                    ON CONFLICT (direction, chat_id) DO NOTHING;
                """)
                cursor.execute("""
                    UPDATE szov_wallboard_broadcast_config
                    SET chats_migrated = TRUE
                    WHERE id = 1 AND NOT chats_migrated;
                """)
                cursor.execute("RELEASE SAVEPOINT sp_szov_broadcast_direction")
            except Exception as exc:
                if _is_schema_lock_conflict(exc):
                    raise
                cursor.execute("ROLLBACK TO SAVEPOINT sp_szov_broadcast_direction")
                logging.error("Отбивка табло: направление получателей не применилось: %s",
                              exc, exc_info=True)
            # Последний удачный снимок табло. Кэш живёт в памяти процесса, поэтому рестарт или
            # деплой раньше оставлял экран в операционном зале пустым до первого удачного ответа
            # Oktell — а прокси бывает недоступен минутами. Это не история, а «что показать,
            # пока не приехали свежие данные»: на каждое направление ровно одна строка.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS szov_wallboard_snapshot (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    payload JSONB NOT NULL,
                    captured_at DOUBLE PRECISION NOT NULL,
                    CONSTRAINT szov_wallboard_snapshot_singleton CHECK (id = 1)
                );
            """)
            # Табло стало больше одного (задача #292: ТП и ОП отдела Тез КЦ на Binotel рядом с
            # линией и чатом СЗоВ на Oktell), а таблица была рассчитана ровно на одну строку —
            # снимки затирали бы друг друга, и после рестарта табло ТП показало бы цифры линии
            # Oktell с пометкой «данные устарели». Ключом становится направление.
            #
            # Под SAVEPOINT — весь _init_db идёт одной транзакцией, и упавший DDL иначе уносит
            # инициализацию ВСЕЙ схемы, а не только этой таблицы. Имя старого ключа не зашиваем,
            # а ищем в каталоге: Postgres назвал бы его szov_wallboard_snapshot_pkey, но ошибка
            # в имени уронила бы миграцию на проде.
            try:
                cursor.execute("SAVEPOINT sp_wallboard_snapshot_direction")
                cursor.execute("""
                    ALTER TABLE szov_wallboard_snapshot
                    ADD COLUMN IF NOT EXISTS direction VARCHAR(16) NOT NULL DEFAULT 'osnova';
                """)
                cursor.execute("""
                    DO $$
                    DECLARE
                        single_key_pk text;
                    BEGIN
                        ALTER TABLE szov_wallboard_snapshot
                            DROP CONSTRAINT IF EXISTS szov_wallboard_snapshot_singleton;
                        SELECT con.conname INTO single_key_pk
                        FROM pg_constraint con
                        JOIN pg_index i ON i.indexrelid = con.conindid
                        WHERE con.conrelid = 'szov_wallboard_snapshot'::regclass
                          AND con.contype = 'p'
                          AND i.indnkeyatts = 1
                          AND EXISTS (
                              SELECT 1 FROM pg_attribute a
                              WHERE a.attrelid = con.conrelid
                                AND a.attnum = i.indkey[0]
                                AND a.attname = 'id'
                          );
                        IF single_key_pk IS NOT NULL THEN
                            EXECUTE 'ALTER TABLE szov_wallboard_snapshot DROP CONSTRAINT '
                                || quote_ident(single_key_pk);
                            ALTER TABLE szov_wallboard_snapshot
                                ALTER COLUMN id DROP NOT NULL;
                            ALTER TABLE szov_wallboard_snapshot
                                ADD PRIMARY KEY (direction);
                        END IF;
                    END $$;
                """)
                cursor.execute("RELEASE SAVEPOINT sp_wallboard_snapshot_direction")
            except Exception as exc:
                if _is_schema_lock_conflict(exc):
                    raise
                cursor.execute("ROLLBACK TO SAVEPOINT sp_wallboard_snapshot_direction")
                logging.error("Табло: направление снимка не применилось: %s", exc, exc_info=True)
            # Выходы на перерыв мимо графика (задача #114). Пишем ТОЛЬКО нарушения: перерыв,
            # совпавший с графиком, — это норма, и хранить её незачем.
            #   kind: off_schedule — перерыв есть в графике, но в другое время;
            #         not_planned  — смена в графике есть, перерывов в ней нет;
            #         no_shift     — на этот день смены в графике вообще нет.
            # UNIQUE(operator_id, started_at) делает почасовой разбор идемпотентным: он
            # перекрывает окна, и один и тот же перерыв не должен превратиться в два нарушения.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS szov_break_violations (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    violation_date DATE NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    ended_at TIMESTAMP NULL,
                    duration_minutes INTEGER NULL,
                    kind VARCHAR(24) NOT NULL,
                    planned_start_minutes INTEGER NULL,
                    planned_end_minutes INTEGER NULL,
                    deviation_minutes INTEGER NULL,
                    detected_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    reported_at TIMESTAMP NULL,
                    CONSTRAINT szov_break_violations_kind CHECK (
                        kind IN ('off_schedule', 'not_planned', 'no_shift')),
                    CONSTRAINT szov_break_violations_episode UNIQUE (operator_id, started_at)
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_szov_break_violations_day
                ON szov_break_violations(violation_date DESC, started_at DESC);
            """)
            # Отбивка забирает только то, о чём ещё не писала: частичный индекс держит выборку
            # короткой независимо от того, сколько нарушений накопилось за месяцы.
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_szov_break_violations_unreported
                ON szov_break_violations(started_at)
                WHERE reported_at IS NULL;
            """)
            # Направление нарушения. Появилось, когда ту же сверку включили чат-менеджерам:
            # у «Линии» факт берётся из статусов Oktell, у «Чата» — из событий Chat2Desk,
            # у «Тез КЦ» — из событий телефона iCORE Phone, а правило и журнал общие.
            # Прежние строки — все с «Линии», отсюда DEFAULT.
            cursor.execute("""
                ALTER TABLE szov_break_violations
                ADD COLUMN IF NOT EXISTS direction VARCHAR(8) NOT NULL DEFAULT 'line';
            """)
            cursor.execute("""
                ALTER TABLE szov_break_violations
                DROP CONSTRAINT IF EXISTS szov_break_violations_direction;
            """)
            cursor.execute("""
                ALTER TABLE szov_break_violations
                ADD CONSTRAINT szov_break_violations_direction
                CHECK (direction IN ('line', 'chat', 'tez'));
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_szov_break_violations_direction_day
                ON szov_break_violations(direction, violation_date DESC, started_at DESC);
            """)
            # Дни, когда человек работал, а смены в графике у него нет: сверять его перерывы
            # не с чем. По «Чату» это пока почти все (замер 18–20.08: работали 11–14 чатников
            # в день, смена была у 0–1), и владелец решил о них МОЛЧАТЬ — в отбивку они не
            # идут. Но и делать вид, что нарушений нет, нельзя: журнал показывает счётчиком,
            # у скольких человек перерывы за период вообще не проверялись. Храним агрегатом,
            # а не строками, чтобы журнал не заполнился сообщениями об отсутствии данных.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS szov_break_unscheduled_days (
                    direction VARCHAR(8) NOT NULL,
                    day DATE NOT NULL,
                    operators INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    PRIMARY KEY (direction, day),
                    CONSTRAINT szov_break_unscheduled_days_direction
                        CHECK (direction IN ('line', 'chat')),
                    CONSTRAINT szov_break_unscheduled_days_operators CHECK (operators >= 0)
                );
            """)
            # Общий код автодозвона: номер, на который оператор один раз звонит,
            # чтобы телефон подключился к режиму автодозвона. Один на всех.
            cursor.execute("""
                ALTER TABLE sip_config
                ADD COLUMN IF NOT EXISTS autodial_code VARCHAR(64) NOT NULL DEFAULT '';
            """)
            # Автодозвон часто живёт на отдельной АТС — свой домен. Пусто =
            # тот же домен, что и у основного номера.
            cursor.execute("""
                ALTER TABLE sip_config
                ADD COLUMN IF NOT EXISTS autodial_server VARCHAR(255) NOT NULL DEFAULT '';
            """)
            # ...и своя база пароля: пароль автодозвона = база + его номер.
            cursor.execute("""
                ALTER TABLE sip_config
                ADD COLUMN IF NOT EXISTS autodial_base_password VARCHAR(255) NOT NULL DEFAULT '';
            """)
            # История ведётся и по общим настройкам, и по персональным аккаунтам:
            # target_user_id пуст у глобальных изменений.
            cursor.execute("""
                ALTER TABLE sip_config_history
                ADD COLUMN IF NOT EXISTS target_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
            """)
            # Настройки SIP отдела: у каждой АТС свой набор номеров, поэтому
            # сервер/база пароля/код автодозвона задаются по отделам. Пустая
            # строка = наследовать из общих (sip_config), строки нет = отдел не
            # настраивали.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sip_department_config (
                    department_id INTEGER PRIMARY KEY REFERENCES departments(id) ON DELETE CASCADE,
                    sip_server VARCHAR(255) NOT NULL DEFAULT '',
                    base_password VARCHAR(255) NOT NULL DEFAULT '',
                    autodial_code VARCHAR(64) NOT NULL DEFAULT '',
                    autodial_server VARCHAR(255) NOT NULL DEFAULT '',
                    autodial_base_password VARCHAR(255) NOT NULL DEFAULT '',
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            cursor.execute("""
                ALTER TABLE sip_department_config
                ADD COLUMN IF NOT EXISTS autodial_server VARCHAR(255) NOT NULL DEFAULT '';
            """)
            cursor.execute("""
                ALTER TABLE sip_department_config
                ADD COLUMN IF NOT EXISTS autodial_base_password VARCHAR(255) NOT NULL DEFAULT '';
            """)
            cursor.execute("""
                ALTER TABLE sip_config_history
                ADD COLUMN IF NOT EXISTS department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL;
            """)
            # Персональные SIP-параметры сотрудника. Пустая строка = «как у всех»
            # (домен отдела или общий, пароль «база + номер»). Второй номер —
            # для автодозвона; у него свои опциональные пароль/домен.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_sip_settings (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    sip_password VARCHAR(255) NOT NULL DEFAULT '',
                    sip_domain VARCHAR(255) NOT NULL DEFAULT '',
                    autodial_number VARCHAR(64) NOT NULL DEFAULT '',
                    autodial_password VARCHAR(255) NOT NULL DEFAULT '',
                    autodial_domain VARCHAR(255) NOT NULL DEFAULT '',
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            # Персональный выключатель входа в FOP2 для ОСНОВНОГО аккаунта.
            # Колонка появилась позже таблицы, поэтому ALTER: строки операторов
            # уже существуют, а вход в FOP2 до сих пор был безусловным — значит
            # единственный безопасный дефолт «включён».
            cursor.execute("""
                ALTER TABLE user_sip_settings
                ADD COLUMN IF NOT EXISTS fop2_enabled BOOLEAN NOT NULL DEFAULT TRUE;
            """)
            # Провайдер телефонии отдела: локальная АТС («asterisk») или Binotel.
            # У Binotel общих значений нет в принципе — сервер, логин и пароль
            # выдаются каждому сотруднику свои, поэтому провайдер решает и набор
            # полей в панели, и способ регистрации в телефоне.
            cursor.execute("""
                ALTER TABLE sip_department_config
                ADD COLUMN IF NOT EXISTS provider VARCHAR(16) NOT NULL DEFAULT 'asterisk';
            """)
            # SIP-логин Binotel — непрозрачная строка (напр. «sip-fixture-912»), а не
            # номер. У локальной АТС логин совпадает с внутренним номером, у
            # Binotel это разные вещи, и users.sip_number у тезовцев обязан
            # остаться ВНУТРЕННИМ НОМЕРОМ: по нему tez_binotel_calls матчит звонки.
            cursor.execute("""
                ALTER TABLE user_sip_settings
                ADD COLUMN IF NOT EXISTS sip_login VARCHAR(128) NOT NULL DEFAULT '';
            """)
            # Автопринятие входящего звонка в телефоне сотрудника: окно звонка
            # показывается auto_answer_delay секунд без возможности отклонить,
            # затем телефон отвечает сам. Дефолт ОБРАТНЫЙ фопешному — выключено:
            # FOP2 до появления своей настройки работал у всех, а автопринятия не
            # было ни у кого, и включаться само оно не должно.
            cursor.execute("""
                ALTER TABLE user_sip_settings
                ADD COLUMN IF NOT EXISTS auto_answer BOOLEAN NOT NULL DEFAULT FALSE;
            """)
            # Задержка держится в БД, а не в телефоне: два секунды устраивают не
            # всех, а перевыкатывать exe ради одного числа никто не станет.
            # DEFAULT 2 совпадает с SIP_AUTO_ANSWER_DELAY_DEFAULT (и с
            # ICORE_AUTOANSWER_DELAY_DEFAULT в телефоне) — менять их надо вместе.
            cursor.execute("""
                ALTER TABLE user_sip_settings
                ADD COLUMN IF NOT EXISTS auto_answer_delay SMALLINT NOT NULL DEFAULT 2;
            """)
            # У автопринятия появился ярус отдела, и персональному значению стало
            # нужно третье состояние — «как у отдела». Выразить его нечем, пока
            # колонки NOT NULL: явное FALSE неотличимо от «не настраивали».
            # Поэтому снимаем NOT NULL (NULL = наследовать) и убираем дефолты,
            # чтобы строка, созданная без этих полей, наследовала, а не выключала.
            # Существующие значения не трогаем: у кого стоит своё — то и остаётся
            # своим (решение владельца: общее применяется только к новым).
            cursor.execute("ALTER TABLE user_sip_settings ALTER COLUMN auto_answer DROP NOT NULL;")
            cursor.execute("ALTER TABLE user_sip_settings ALTER COLUMN auto_answer DROP DEFAULT;")
            cursor.execute("ALTER TABLE user_sip_settings ALTER COLUMN auto_answer_delay DROP NOT NULL;")
            cursor.execute("ALTER TABLE user_sip_settings ALTER COLUMN auto_answer_delay DROP DEFAULT;")
            # Ярус отдела для автопринятия: «общий автоприём» вкладки «Общие».
            # Тоже nullable, и по той же причине — NULL значит «отдел не задавал»,
            # и тогда действует выключенное состояние по умолчанию. Дефолта у
            # колонок нет намеренно: отделы, которых настройка не касается,
            # должны остаться ровно с тем поведением, что было до её появления.
            cursor.execute("""
                ALTER TABLE sip_department_config
                ADD COLUMN IF NOT EXISTS auto_answer BOOLEAN;
            """)
            cursor.execute("""
                ALTER TABLE sip_department_config
                ADD COLUMN IF NOT EXISTS auto_answer_delay SMALLINT;
            """)
            # Автосброс исходящего без ответа: после скольких гудков телефон кладёт
            # трубку сам (запрос владельца 11.10.2026). NULL — отдел не задавал, и
            # действует SIP_RING_TIMEOUT_RINGS_DEFAULT, то есть сброс ВКЛЮЧЁН; 0 — не
            # сбрасывать. Дефолта у колонки нет, и строки отделов, в отличие от
            # автоприёма, не проставляем: NULL само по себе и есть умолчание.
            cursor.execute("""
                ALTER TABLE sip_department_config
                ADD COLUMN IF NOT EXISTS ring_timeout_rings SMALLINT;
            """)
            # Общий адрес кабинета Binotel. У локальной АТС кабинета нет вовсе,
            # поэтому поле живёт в той же строке отдела, но показывается только
            # в разделе «Тез».
            cursor.execute("""
                ALTER TABLE sip_department_config
                ADD COLUMN IF NOT EXISTS binotel_cabinet_url VARCHAR(255) NOT NULL DEFAULT '';
            """)
            # Значения по умолчанию для отделов на Binotel (задача #309): автоприём
            # включён, окно 3 секунды, кабинет my.binotel.kz. Людей это не задевает:
            # у кого есть своя строка, тот наследование не читает.
            #
            # Проставляем ОДИН РАЗ, а не при каждом старте. Миграции гоняются на
            # каждом запуске, и условия «поле пустое» было бы мало: администратор,
            # снявший общий автоприём, получил бы его обратно после ближайшего
            # деплоя — молча и без объяснений. Признак «уже решали» берём из
            # истории: update_sip_department_config пишет туда снимок с этими
            # ключами при КАЖДОМ сохранении отдела, поэтому запись в истории и
            # значит «человек тут уже высказался».
            cursor.execute("""
                UPDATE sip_department_config c
                SET auto_answer = TRUE, auto_answer_delay = 3
                WHERE c.provider = 'binotel'
                  AND c.auto_answer IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM sip_config_history h
                      WHERE h.department_id = c.department_id
                        AND h.settings ? 'auto_answer'
                  );
            """)
            cursor.execute("""
                UPDATE sip_department_config c
                SET binotel_cabinet_url = %s
                WHERE c.provider = 'binotel'
                  AND c.binotel_cabinet_url = ''
                  AND NOT EXISTS (
                      SELECT 1 FROM sip_config_history h
                      WHERE h.department_id = c.department_id
                        AND h.settings ? 'binotel_cabinet_url'
                  );
            """, (BINOTEL_CABINET_URL_DEFAULT,))
            # Учётка веб-кабинета my.binotel.kz. Отдельной таблицей, а не
            # колонками в user_sip_settings: та строка удаляется, когда у
            # сотрудника не осталось SIP-переопределений, и сброс SIP унёс бы
            # вместе с ней доступ в кабинет.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS binotel_user_accounts (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    cabinet_login VARCHAR(255) NOT NULL DEFAULT '',
                    cabinet_password VARCHAR(255) NOT NULL DEFAULT '',
                    employee_id VARCHAR(32) NOT NULL DEFAULT '',
                    cabinet_url VARCHAR(255) NOT NULL DEFAULT '',
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            # Учётка личного кабинета Oktell. Отдельной таблицей по той же
            # причине, что и у Binotel: строка user_sip_settings удаляется, как
            # только у сотрудника не осталось SIP-переопределений, и сброс SIP
            # унёс бы вместе с ней доступ в АТС.
            #
            # Зачем она вообще нужна серверу: клиент Oktell раздаётся с нашего
            # сайта и спрашивает у человека логин от iCORE, а логин и пароль
            # кабинета получает от нас ответом. То есть эта строка — то, что
            # оператор больше не вводит руками и чего он может не знать вовсе.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS oktell_user_accounts (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    cabinet_login VARCHAR(255) NOT NULL DEFAULT '',
                    cabinet_password VARCHAR(255) NOT NULL DEFAULT '',
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            # Снятие общего яруса: раньше отдел без своей строки наследовал
            # sip_config. Ярус убираем — одна унификация на все отделы опасна, —
            # поэтому действующие значения переносим в строки отделов, иначе
            # операторы таких отделов разом остались бы без сервера и пароля.
            # Идемпотентно: только там, где своей строки ещё нет.
            cursor.execute("""
                INSERT INTO sip_department_config (
                    department_id, sip_server, base_password, autodial_code,
                    autodial_server, autodial_base_password)
                SELECT DISTINCT u.department_id, c.sip_server, c.base_password,
                       c.autodial_code, c.autodial_server, c.autodial_base_password
                FROM users u
                CROSS JOIN sip_config c
                WHERE c.id = 1
                  AND u.department_id IS NOT NULL
                  AND NULLIF(TRIM(COALESCE(c.sip_server, '')), '') IS NOT NULL
                  AND NULLIF(TRIM(COALESCE(u.sip_number, '')), '') IS NOT NULL
                ON CONFLICT (department_id) DO NOTHING;
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS resource_saved_schedule_plans (
                    id SERIAL PRIMARY KEY,
                    date_from DATE NOT NULL,
                    date_to DATE NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    selected_variant_key VARCHAR(120) NOT NULL DEFAULT '',
                    include_incident_uplift BOOLEAN NOT NULL DEFAULT FALSE,
                    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
                    capacity JSONB NOT NULL DEFAULT '{}'::jsonb,
                    templates JSONB NOT NULL DEFAULT '[]'::jsonb,
                    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    archived_at TIMESTAMP NULL
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS resource_saved_schedule_shifts (
                    id SERIAL PRIMARY KEY,
                    plan_id INTEGER NOT NULL REFERENCES resource_saved_schedule_plans(id) ON DELETE CASCADE,
                    client_shift_id TEXT NOT NULL DEFAULT '',
                    shift_date DATE NOT NULL,
                    start_minute INTEGER NOT NULL DEFAULT 0,
                    end_minute INTEGER NOT NULL DEFAULT 0,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    duration_minutes INTEGER NOT NULL DEFAULT 0,
                    rate_min NUMERIC(8,2) NOT NULL DEFAULT 0,
                    label TEXT NOT NULL DEFAULT '',
                    template_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    tone TEXT NOT NULL DEFAULT '',
                    overnight BOOLEAN NOT NULL DEFAULT FALSE,
                    breaks JSONB NOT NULL DEFAULT '[]'::jsonb,
                    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_auction_test_access (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    launch_note TEXT NOT NULL DEFAULT '',
                    starts_at TIMESTAMP NULL,
                    ends_at TIMESTAMP NULL,
                    paused_at TIMESTAMP NULL,
                    finished_at TIMESTAMP NULL,
                    selected_schedule_plan_id INTEGER NULL REFERENCES resource_saved_schedule_plans(id) ON DELETE SET NULL,
                    published_to_work_schedules_at TIMESTAMP NULL,
                    published_to_work_schedules_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT shift_auction_test_access_singleton CHECK (id = 1)
                );
            """)
            cursor.execute("ALTER TABLE shift_auction_test_access ADD COLUMN IF NOT EXISTS starts_at TIMESTAMP NULL;")
            cursor.execute("ALTER TABLE shift_auction_test_access ADD COLUMN IF NOT EXISTS ends_at TIMESTAMP NULL;")
            cursor.execute("ALTER TABLE shift_auction_test_access ADD COLUMN IF NOT EXISTS paused_at TIMESTAMP NULL;")
            cursor.execute("ALTER TABLE shift_auction_test_access ADD COLUMN IF NOT EXISTS finished_at TIMESTAMP NULL;")
            cursor.execute("ALTER TABLE shift_auction_test_access ADD COLUMN IF NOT EXISTS selected_schedule_plan_id INTEGER NULL;")
            cursor.execute("ALTER TABLE shift_auction_test_access ADD COLUMN IF NOT EXISTS published_to_work_schedules_at TIMESTAMP NULL;")
            cursor.execute("ALTER TABLE shift_auction_test_access ADD COLUMN IF NOT EXISTS published_to_work_schedules_by INTEGER NULL;")
            cursor.execute("ALTER TABLE shift_auction_test_access ADD COLUMN IF NOT EXISTS topup_started_at TIMESTAMP NULL;")
            cursor.execute("ALTER TABLE shift_auction_test_access ADD COLUMN IF NOT EXISTS topup_started_by INTEGER NULL;")
            # Legacy early-access ("избранная группа") window. Superseded by the
            # per-week time groups below; kept only so the one-time migration can
            # read the old config out of it.
            cursor.execute("ALTER TABLE shift_auction_test_access ADD COLUMN IF NOT EXISTS favored_starts_at TIMESTAMP NULL;")
            # Rate lock: when enabled, operators may claim only lots whose rate
            # group (derived from shift duration) matches their own users.rate.
            cursor.execute("ALTER TABLE shift_auction_test_access ADD COLUMN IF NOT EXISTS rate_lock_enabled BOOLEAN NOT NULL DEFAULT FALSE;")
            # Аукцион перестал быть одним на компанию: линия и чат — два независимых
            # прогона. Настройки каждого лежат своей строкой, поэтому singleton-CHECK
            # (id = 1) снимается, а направление пишем явной колонкой — по ней, а не по
            # номеру строки, фильтруют запросы.
            cursor.execute("ALTER TABLE shift_auction_test_access DROP CONSTRAINT IF EXISTS shift_auction_test_access_singleton;")
            cursor.execute(
                "ALTER TABLE shift_auction_test_access ADD COLUMN IF NOT EXISTS direction_mode VARCHAR(16) NOT NULL DEFAULT 'line';"
            )
            cursor.execute(
                "UPDATE shift_auction_test_access SET direction_mode = %s WHERE id = %s AND direction_mode <> %s;",
                (SHIFT_AUCTION_MODE_LINE, SHIFT_AUCTION_SETTINGS_ROW_ID[SHIFT_AUCTION_MODE_LINE], SHIFT_AUCTION_MODE_LINE)
            )
            cursor.execute("""
                INSERT INTO shift_auction_test_access (id, direction_mode)
                VALUES (%s, %s), (%s, %s)
                ON CONFLICT (id) DO NOTHING;
            """, (
                SHIFT_AUCTION_SETTINGS_ROW_ID[SHIFT_AUCTION_MODE_LINE], SHIFT_AUCTION_MODE_LINE,
                SHIFT_AUCTION_SETTINGS_ROW_ID[SHIFT_AUCTION_MODE_CHAT], SHIFT_AUCTION_MODE_CHAT,
            ))
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_shift_auction_test_access_mode "
                "ON shift_auction_test_access(direction_mode);"
            )
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_auction_test_participants (
                    operator_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Legacy early-access marker, replaced by shift_auction_time_groups.
            # Kept for the one-time migration only (see below).
            cursor.execute("ALTER TABLE shift_auction_test_participants ADD COLUMN IF NOT EXISTS early_access BOOLEAN NOT NULL DEFAULT FALSE;")
            # Направление участника. Человек принадлежит ровно одному направлению, так что
            # PK по operator_id менять не нужно — колонка нужна, чтобы сохранение состава
            # ОДНОГО аукциона не стирало состав второго (там идёт DELETE всей таблицы).
            cursor.execute(
                "ALTER TABLE shift_auction_test_participants ADD COLUMN IF NOT EXISTS direction_mode VARCHAR(16) NOT NULL DEFAULT 'line';"
            )
            # Week time groups ("группы времени"): a group opens the auction for
            # its members earlier OR later than the main window. Bound to one
            # weekly plan on purpose — a group must not leak into other weeks.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_auction_time_groups (
                    id SERIAL PRIMARY KEY,
                    plan_id INTEGER NOT NULL REFERENCES resource_saved_schedule_plans(id) ON DELETE CASCADE,
                    name TEXT NOT NULL DEFAULT '',
                    starts_at TIMESTAMP NULL,
                    ends_at TIMESTAMP NULL,
                    self_schedule_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # "Свой график": members put shifts on days themselves instead of
            # taking prepared lots, capped at their norm plus a fixed allowance.
            cursor.execute("ALTER TABLE shift_auction_time_groups ADD COLUMN IF NOT EXISTS self_schedule_enabled BOOLEAN NOT NULL DEFAULT FALSE;")
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_shift_auction_time_groups_plan
                ON shift_auction_time_groups(plan_id, sort_order, id);
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_auction_time_group_members (
                    group_id INTEGER NOT NULL REFERENCES shift_auction_time_groups(id) ON DELETE CASCADE,
                    plan_id INTEGER NOT NULL,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    PRIMARY KEY (group_id, operator_id)
                );
            """)
            # One operator belongs to at most one group per week: the denormalized
            # plan_id makes that a DB-level guarantee instead of a code convention.
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_shift_auction_time_group_member_week
                ON shift_auction_time_group_members(plan_id, operator_id);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_shift_auction_time_group_members_operator
                ON shift_auction_time_group_members(operator_id);
            """)
            # One-time migration of the retired "избранная группа" into a time
            # group of the week it was configured for, so a live early window is
            # not silently lost on deploy.
            cursor.execute("""
                INSERT INTO shift_auction_time_groups (plan_id, name, starts_at, created_by)
                SELECT s.selected_schedule_plan_id, 'Ранний доступ', s.favored_starts_at, s.updated_by
                FROM shift_auction_test_access s
                WHERE s.id = 1
                  AND s.selected_schedule_plan_id IS NOT NULL
                  AND s.favored_starts_at IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM shift_auction_test_participants
                      WHERE COALESCE(early_access, FALSE)
                  )
                  AND NOT EXISTS (SELECT 1 FROM shift_auction_time_groups)
                RETURNING id, plan_id;
            """)
            migrated_group_row = cursor.fetchone()
            if migrated_group_row:
                cursor.execute("""
                    INSERT INTO shift_auction_time_group_members (group_id, plan_id, operator_id)
                    SELECT %s, %s, operator_id
                    FROM shift_auction_test_participants
                    WHERE COALESCE(early_access, FALSE)
                    ON CONFLICT DO NOTHING;
                """, (migrated_group_row[0], migrated_group_row[1]))
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_auction_test_lots (
                    id SERIAL PRIMARY KEY,
                    shift_date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    rate_min NUMERIC(8,2) NOT NULL DEFAULT 0,
                    breaks JSONB NOT NULL DEFAULT '[]'::jsonb,
                    source_schedule_plan_id INTEGER NULL REFERENCES resource_saved_schedule_plans(id) ON DELETE SET NULL,
                    source_schedule_shift_id INTEGER NULL REFERENCES resource_saved_schedule_shifts(id) ON DELETE SET NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'available'
                        CHECK (status IN ('available', 'claimed', 'cancelled')),
                    claimed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    claimed_at TIMESTAMP NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("ALTER TABLE shift_auction_test_lots ADD COLUMN IF NOT EXISTS breaks JSONB NOT NULL DEFAULT '[]'::jsonb;")
            cursor.execute("ALTER TABLE shift_auction_test_lots ADD COLUMN IF NOT EXISTS source_schedule_plan_id INTEGER NULL;")
            cursor.execute("ALTER TABLE shift_auction_test_lots ADD COLUMN IF NOT EXISTS source_schedule_shift_id INTEGER NULL;")
            cursor.execute("ALTER TABLE shift_auction_test_lots ADD COLUMN IF NOT EXISTS post_auction_claimed BOOLEAN NOT NULL DEFAULT FALSE;")
            cursor.execute("ALTER TABLE shift_auction_test_lots ADD COLUMN IF NOT EXISTS post_claim_start_time TIME NULL;")
            cursor.execute("ALTER TABLE shift_auction_test_lots ADD COLUMN IF NOT EXISTS post_claim_end_time TIME NULL;")
            # Links a "remainder" lot (the unclaimed leftover of a partial post-auction
            # claim) back to the lot it was split from, so admin unclaim can reconcile them.
            cursor.execute("ALTER TABLE shift_auction_test_lots ADD COLUMN IF NOT EXISTS remainder_of_lot_id INTEGER NULL;")
            # Manually-added lots (supervisor/admin add an extra shift via the "+" button
            # in the auction grid). added_by lets the UI show a distinct colour and
            # "кто добавил" both in the auction grid and in the schedule planner.
            cursor.execute("ALTER TABLE shift_auction_test_lots ADD COLUMN IF NOT EXISTS added_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL;")
            cursor.execute("ALTER TABLE shift_auction_test_lots ADD COLUMN IF NOT EXISTS added_at TIMESTAMP NULL;")
            # Shift an operator put on the calendar themselves ("свой график"):
            # created already claimed and marked apart in the grid.
            cursor.execute("ALTER TABLE shift_auction_test_lots ADD COLUMN IF NOT EXISTS self_scheduled_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL;")
            # Направление лота. Сид, перезапуск и снимок фильтруют по нему: без колонки
            # чат увидел бы смены линии, а «Перезапустить» одного направления снесло бы
            # лоты второго.
            cursor.execute(
                "ALTER TABLE shift_auction_test_lots ADD COLUMN IF NOT EXISTS direction_mode VARCHAR(16) NOT NULL DEFAULT 'line';"
            )
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_auction_test_day_offs (
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    day_off_date DATE NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (operator_id, day_off_date)
                );
            """)
            cursor.execute(
                "ALTER TABLE shift_auction_test_day_offs ADD COLUMN IF NOT EXISTS direction_mode VARCHAR(16) NOT NULL DEFAULT 'line';"
            )
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_auction_test_events (
                    id SERIAL PRIMARY KEY,
                    event_type VARCHAR(64) NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Журнал и поток SSE тоже раздельные: событие чата не должно будить экран
            # линии и наоборот.
            cursor.execute(
                "ALTER TABLE shift_auction_test_events ADD COLUMN IF NOT EXISTS direction_mode VARCHAR(16) NOT NULL DEFAULT 'line';"
            )
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_auction_published_periods (
                    plan_id INTEGER PRIMARY KEY REFERENCES resource_saved_schedule_plans(id) ON DELETE CASCADE,
                    date_from DATE NOT NULL,
                    date_to DATE NOT NULL,
                    published_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    published_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    participant_ids INTEGER[] NOT NULL DEFAULT '{}'::INTEGER[],
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_auction_historical_claims (
                    plan_id INTEGER NOT NULL REFERENCES resource_saved_schedule_plans(id) ON DELETE CASCADE,
                    source_schedule_shift_id INTEGER NOT NULL REFERENCES resource_saved_schedule_shifts(id) ON DELETE CASCADE,
                    claimed_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    claimed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    claimed_start_time TIME NULL,
                    claimed_end_time TIME NULL,
                    PRIMARY KEY (plan_id, source_schedule_shift_id)
                );
            """)
            cursor.execute("ALTER TABLE shift_auction_historical_claims ADD COLUMN IF NOT EXISTS claimed_start_time TIME NULL;")
            cursor.execute("ALTER TABLE shift_auction_historical_claims ADD COLUMN IF NOT EXISTS claimed_end_time TIME NULL;")
            # На каком этапе взята часть смены. Прежде таблица знала только пост-аукционный
            # добор, поэтому дефолт 'post_auction' верен для всех существующих строк. Чат
            # берёт части ПРЯМО В ХОДЕ аукциона — такие строки помечены 'auction' и не
            # попадают в панель «Мои доп. смены» (у них своя кнопка «Вернуть»).
            cursor.execute(
                "ALTER TABLE shift_auction_historical_claims "
                "ADD COLUMN IF NOT EXISTS claim_stage VARCHAR(16) NOT NULL DEFAULT 'post_auction';"
            )
            # Allow several operators to each claim a DISJOINT part of the same shift
            # (partial добор of the leftover). The primary key therefore includes
            # claimed_by; overlap between two operators' ranges is rejected in code.
            cursor.execute("ALTER TABLE shift_auction_historical_claims DROP CONSTRAINT IF EXISTS shift_auction_historical_claims_pkey;")
            cursor.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conrelid = 'shift_auction_historical_claims'::regclass
                          AND contype = 'p'
                    ) THEN
                        ALTER TABLE shift_auction_historical_claims
                            ADD PRIMARY KEY (plan_id, source_schedule_shift_id, claimed_by);
                    END IF;
                END $$;
            """)
            # Consolidate to the single-lot model: drop legacy remainder lots and
            # re-open partially-claimed shifts so their free part is claimable again.
            self._consolidate_post_auction_lots(cursor)

            # Optimized Indexes (added more based on query patterns)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_calls_month ON calls(month);
                CREATE INDEX IF NOT EXISTS idx_calls_operator_id ON calls(operator_id);
                CREATE INDEX IF NOT EXISTS idx_calls_operator_month ON calls(operator_id, month);
                CREATE INDEX IF NOT EXISTS idx_calls_op_month_phone_created ON calls(operator_id, month, phone_number, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_calls_phone_number ON calls(phone_number);
                CREATE INDEX IF NOT EXISTS idx_calls_created_at ON calls(created_at);
                CREATE INDEX IF NOT EXISTS idx_calls_evaluator_id ON calls(evaluator_id);
                CREATE INDEX IF NOT EXISTS idx_calls_is_draft ON calls(is_draft);
                CREATE INDEX IF NOT EXISTS idx_work_hours_operator_id ON work_hours(operator_id);
                CREATE INDEX IF NOT EXISTS idx_work_hours_month ON work_hours(month);
                CREATE INDEX IF NOT EXISTS idx_directions_name ON directions(name);
                CREATE INDEX IF NOT EXISTS idx_directions_is_active ON directions(is_active);
                CREATE INDEX IF NOT EXISTS idx_directions_calculation_model ON directions(calculation_model_code);
                CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
                CREATE INDEX IF NOT EXISTS idx_users_supervisor_id ON users(supervisor_id);
                CREATE INDEX IF NOT EXISTS idx_users_login ON users(login);
                CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);
                CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
                CREATE INDEX IF NOT EXISTS idx_users_rate ON users(rate);
                CREATE INDEX IF NOT EXISTS idx_user_history_user_id ON user_history(user_id);
                CREATE INDEX IF NOT EXISTS idx_user_history_changed_at ON user_history(changed_at);
                CREATE INDEX IF NOT EXISTS idx_work_hours_calls_per_hour ON work_hours(calls_per_hour);
                CREATE INDEX IF NOT EXISTS idx_trainings_operator_id ON trainings(operator_id);
                CREATE INDEX IF NOT EXISTS idx_trainings_training_date ON trainings(training_date);
                CREATE INDEX IF NOT EXISTS idx_call_feedbacks_call_id ON call_feedbacks(call_id);
                CREATE INDEX IF NOT EXISTS idx_call_feedbacks_training_id ON call_feedbacks(training_id);
                CREATE INDEX IF NOT EXISTS idx_call_feedbacks_operator_id ON call_feedbacks(operator_id);
                CREATE INDEX IF NOT EXISTS idx_call_feedbacks_feedback_date ON call_feedbacks(feedback_date);
                CREATE INDEX IF NOT EXISTS idx_operator_technical_issues_issue_date
                ON operator_technical_issues(issue_date);
                CREATE INDEX IF NOT EXISTS idx_operator_technical_issues_issue_date_time
                ON operator_technical_issues(issue_date, start_time, end_time);
                CREATE INDEX IF NOT EXISTS idx_operator_technical_issues_operator_id
                ON operator_technical_issues(operator_id);
                CREATE INDEX IF NOT EXISTS idx_operator_technical_issues_operator_date
                ON operator_technical_issues(operator_id, issue_date);
                CREATE INDEX IF NOT EXISTS idx_operator_technical_issues_reason
                ON operator_technical_issues(reason);
                CREATE INDEX IF NOT EXISTS idx_operator_technical_issues_workplace_number
                ON operator_technical_issues(workplace_number);
                CREATE INDEX IF NOT EXISTS idx_operator_technical_issues_for_supervisor
                ON operator_technical_issues(for_supervisor);
                CREATE INDEX IF NOT EXISTS idx_operator_technical_issues_created_by
                ON operator_technical_issues(created_by);
                CREATE INDEX IF NOT EXISTS idx_operator_technical_issues_created_at
                ON operator_technical_issues(created_at);
                CREATE INDEX IF NOT EXISTS idx_operator_offline_activities_activity_date
                ON operator_offline_activities(activity_date);
                CREATE INDEX IF NOT EXISTS idx_operator_offline_activities_activity_date_time
                ON operator_offline_activities(activity_date, start_time, end_time);
                CREATE INDEX IF NOT EXISTS idx_operator_offline_activities_operator_id
                ON operator_offline_activities(operator_id);
                CREATE INDEX IF NOT EXISTS idx_operator_offline_activities_operator_date
                ON operator_offline_activities(operator_id, activity_date);
                CREATE INDEX IF NOT EXISTS idx_operator_offline_activities_created_by
                ON operator_offline_activities(created_by);
                CREATE INDEX IF NOT EXISTS idx_operator_offline_activities_created_at
                ON operator_offline_activities(created_at);
                CREATE INDEX IF NOT EXISTS idx_work_shifts_operator_date ON work_shifts(operator_id, shift_date);
                CREATE INDEX IF NOT EXISTS idx_work_shifts_date ON work_shifts(shift_date);
                CREATE INDEX IF NOT EXISTS idx_days_off_operator_date ON days_off(operator_id, day_off_date);
                CREATE INDEX IF NOT EXISTS idx_work_shift_changes_operator_date
                ON work_shift_changes(operator_id, shift_date, changed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_work_shift_changes_date
                ON work_shift_changes(shift_date, changed_at DESC);
                -- Сводка за сутки идёт по окну changed_at, а оба индекса выше
                -- ведут shift_date — им она воспользоваться не может.
                CREATE INDEX IF NOT EXISTS idx_work_shift_changes_changed_at
                ON work_shift_changes(changed_at);
                CREATE INDEX IF NOT EXISTS idx_work_shift_swap_requests_target_status
                ON work_shift_swap_requests(target_operator_id, status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_work_shift_swap_requests_requester_status
                ON work_shift_swap_requests(requester_operator_id, status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_work_shift_swap_requests_period
                ON work_shift_swap_requests(start_date, end_date);
                CREATE INDEX IF NOT EXISTS idx_ai_feedback_cache_operator_month ON ai_feedback_cache(operator_id, month);
                CREATE INDEX IF NOT EXISTS idx_recruiting_parse_runs_status_finished
                ON recruiting_parse_runs(status, finished_at DESC);
                CREATE INDEX IF NOT EXISTS idx_recruiting_resumes_run_uuid ON recruiting_resumes(run_uuid);
                CREATE INDEX IF NOT EXISTS idx_recruiting_resumes_group ON recruiting_resumes(keyword_group);
                CREATE INDEX IF NOT EXISTS idx_recruiting_resumes_page ON recruiting_resumes(page_found);
                CREATE INDEX IF NOT EXISTS idx_recruiting_resumes_detail_url ON recruiting_resumes(detail_url);
                CREATE INDEX IF NOT EXISTS idx_surveys_created_by ON surveys(created_by, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_surveys_repeat_root_iteration ON surveys(repeat_root_id, repeat_iteration);
                CREATE INDEX IF NOT EXISTS idx_survey_questions_survey_position ON survey_questions(survey_id, position);
                CREATE INDEX IF NOT EXISTS idx_survey_assignments_operator ON survey_assignments(operator_id, status);
                CREATE INDEX IF NOT EXISTS idx_survey_assignments_survey ON survey_assignments(survey_id);
                CREATE INDEX IF NOT EXISTS idx_survey_responses_survey ON survey_responses(survey_id);
                CREATE INDEX IF NOT EXISTS idx_survey_answers_response ON survey_answers(response_id);
                CREATE INDEX IF NOT EXISTS idx_surveys_test_window
                    ON surveys(ends_at) WHERE is_test AND ends_at IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_survey_attempt_drafts_survey
                    ON survey_attempt_drafts(survey_id);
                CREATE INDEX IF NOT EXISTS idx_surveys_archive_page
                    ON surveys(archived_at, created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_surveys_archive_pending_notify
                    ON surveys(archived_at) WHERE archive_notified_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to ON tasks(assigned_to);
                CREATE INDEX IF NOT EXISTS idx_tasks_created_by ON tasks(created_by);
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
                CREATE INDEX IF NOT EXISTS idx_tasks_due_at ON tasks(due_at);
                CREATE INDEX IF NOT EXISTS idx_tasks_recurrence_next ON tasks(recurrence_next_at);
                -- Доска грузит колонки: срез по статусу + порядок по свежести.
                CREATE INDEX IF NOT EXISTS idx_tasks_status_created
                    ON tasks(status, created_at DESC, id DESC);
                -- Доска сотрудника и «мои задачи»: тот же порядок в рамках участника.
                CREATE INDEX IF NOT EXISTS idx_tasks_assigned_created
                    ON tasks(assigned_to, created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_tasks_created_by_created
                    ON tasks(created_by, created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_tasks_requested_by ON tasks(requested_by_id);
                CREATE INDEX IF NOT EXISTS idx_task_status_history_task_id ON task_status_history(task_id);
                CREATE INDEX IF NOT EXISTS idx_task_attachments_task_id ON task_attachments(task_id);
                CREATE INDEX IF NOT EXISTS idx_task_checklist_items_task_id ON task_checklist_items(task_id, position);
                CREATE INDEX IF NOT EXISTS idx_raw_resource_uploads_report_date ON raw_resource_uploads(report_date DESC);
                -- idx_daily_resource_hours_report_hour duplicated the UNIQUE(report_date, hour)
                -- constraint index byte for byte; the constraint serves the same queries.
                DROP INDEX IF EXISTS idx_daily_resource_hours_report_hour;
                CREATE INDEX IF NOT EXISTS idx_daily_resource_summary_weekday_date ON daily_resource_summary(weekday, report_date DESC);
                CREATE INDEX IF NOT EXISTS idx_resource_saved_schedule_plans_period ON resource_saved_schedule_plans(date_from, date_to, archived_at, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_resource_saved_schedule_shifts_plan_date ON resource_saved_schedule_shifts(plan_id, shift_date, start_time);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_test_participants_created ON shift_auction_test_participants(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_test_lots_status_date ON shift_auction_test_lots(status, shift_date, start_time);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_test_lots_mode_date ON shift_auction_test_lots(direction_mode, shift_date, start_time);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_test_events_mode_id ON shift_auction_test_events(direction_mode, id);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_test_lots_claimed_by ON shift_auction_test_lots(claimed_by);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_test_lots_source_schedule ON shift_auction_test_lots(source_schedule_plan_id, source_schedule_shift_id);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_test_day_offs_operator ON shift_auction_test_day_offs(operator_id, day_off_date);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_test_events_id ON shift_auction_test_events(id);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_test_events_type_id ON shift_auction_test_events(event_type, id);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_published_periods_dates ON shift_auction_published_periods(date_from, date_to);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_published_periods_participants ON shift_auction_published_periods USING GIN(participant_ids);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_historical_claims_claimed_by ON shift_auction_historical_claims(claimed_by);
                CREATE INDEX IF NOT EXISTS idx_shift_auction_historical_claims_plan ON shift_auction_historical_claims(plan_id);
                CREATE INDEX IF NOT EXISTS idx_calibration_rooms_month ON calibration_rooms(month);
                CREATE INDEX IF NOT EXISTS idx_calibration_rooms_operator_id ON calibration_rooms(operator_id);
                CREATE INDEX IF NOT EXISTS idx_calibration_rooms_admin_id ON calibration_rooms(created_by_admin_id);
                CREATE INDEX IF NOT EXISTS idx_calibration_rooms_created_at ON calibration_rooms(created_at);
                CREATE INDEX IF NOT EXISTS idx_calibration_members_room_sv ON calibration_room_members(room_id, supervisor_id);
                CREATE INDEX IF NOT EXISTS idx_calibration_room_calls_room_id ON calibration_room_calls(room_id);
                CREATE INDEX IF NOT EXISTS idx_calibration_room_calls_operator_id ON calibration_room_calls(operator_id);
                CREATE INDEX IF NOT EXISTS idx_calibration_room_calls_month ON calibration_room_calls(month);
                CREATE INDEX IF NOT EXISTS idx_calibration_call_evals_room_call_id ON calibration_room_call_evaluations(room_call_id);
                CREATE INDEX IF NOT EXISTS idx_calibration_call_evals_evaluator_id ON calibration_room_call_evaluations(evaluator_id);
                CREATE INDEX IF NOT EXISTS idx_calibration_eval_room_id ON calibration_room_evaluations(room_id);
                CREATE INDEX IF NOT EXISTS idx_calibration_eval_evaluator_id ON calibration_room_evaluations(evaluator_id);
                
                    -- Table for Baiga daily scores (points per operator per day)
                    CREATE TABLE IF NOT EXISTS baiga_daily_scores (
                        id SERIAL PRIMARY KEY,
                        operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        day DATE NOT NULL,
                        points INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(operator_id, day)
                    );

                CREATE INDEX IF NOT EXISTS idx_baiga_daily_scores_day ON baiga_daily_scores(day);
                CREATE INDEX IF NOT EXISTS idx_baiga_daily_scores_operator ON baiga_daily_scores(operator_id);

                -- LMS core entities
                CREATE TABLE IF NOT EXISTS lms_courses (
                    id SERIAL PRIMARY KEY,
                    slug VARCHAR(255) UNIQUE,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    category VARCHAR(100),
                    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'published', 'archived')),
                    default_pass_threshold NUMERIC(5,2) NOT NULL DEFAULT 80.00 CHECK(default_pass_threshold >= 0 AND default_pass_threshold <= 100),
                    default_attempt_limit INTEGER NOT NULL DEFAULT 3 CHECK(default_attempt_limit >= 1),
                    current_version_id INTEGER,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                CREATE TABLE IF NOT EXISTS lms_course_versions (
                    id SERIAL PRIMARY KEY,
                    course_id INTEGER NOT NULL REFERENCES lms_courses(id) ON DELETE CASCADE,
                    version_number INTEGER NOT NULL CHECK(version_number >= 1),
                    title VARCHAR(255),
                    description TEXT,
                    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'published', 'archived')),
                    pass_threshold NUMERIC(5,2) NOT NULL DEFAULT 80.00 CHECK(pass_threshold >= 0 AND pass_threshold <= 100),
                    attempt_limit INTEGER NOT NULL DEFAULT 3 CHECK(attempt_limit >= 1),
                    anti_cheat_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    published_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    published_at TIMESTAMP,
                    UNIQUE(course_id, version_number)
                );

                CREATE TABLE IF NOT EXISTS lms_modules (
                    id SERIAL PRIMARY KEY,
                    course_version_id INTEGER NOT NULL REFERENCES lms_course_versions(id) ON DELETE CASCADE,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    position INTEGER NOT NULL DEFAULT 1 CHECK(position >= 1),
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                CREATE TABLE IF NOT EXISTS lms_lessons (
                    id SERIAL PRIMARY KEY,
                    module_id INTEGER NOT NULL REFERENCES lms_modules(id) ON DELETE CASCADE,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    lesson_type VARCHAR(20) NOT NULL DEFAULT 'video' CHECK(lesson_type IN ('video', 'text', 'combined')),
                    position INTEGER NOT NULL DEFAULT 1 CHECK(position >= 1),
                    duration_seconds INTEGER NOT NULL DEFAULT 0 CHECK(duration_seconds >= 0),
                    allow_fast_forward BOOLEAN NOT NULL DEFAULT FALSE,
                    completion_threshold NUMERIC(5,2) NOT NULL DEFAULT 95.00 CHECK(completion_threshold >= 0 AND completion_threshold <= 100),
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                CREATE TABLE IF NOT EXISTS lms_lesson_materials (
                    id SERIAL PRIMARY KEY,
                    lesson_id INTEGER NOT NULL REFERENCES lms_lessons(id) ON DELETE CASCADE,
                    title VARCHAR(255) NOT NULL,
                    material_type VARCHAR(20) NOT NULL CHECK(material_type IN ('video', 'pdf', 'link', 'text', 'file')),
                    content_text TEXT,
                    content_url TEXT,
                    gcs_bucket VARCHAR(255),
                    gcs_blob_path TEXT,
                    mime_type VARCHAR(255),
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    position INTEGER NOT NULL DEFAULT 1 CHECK(position >= 1),
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                CREATE TABLE IF NOT EXISTS lms_tests (
                    id SERIAL PRIMARY KEY,
                    course_version_id INTEGER NOT NULL REFERENCES lms_course_versions(id) ON DELETE CASCADE,
                    module_id INTEGER REFERENCES lms_modules(id) ON DELETE SET NULL,
                    position INTEGER CHECK(position IS NULL OR position >= 1),
                    source_lesson_id INTEGER REFERENCES lms_lessons(id) ON DELETE SET NULL,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    pass_threshold NUMERIC(5,2) NOT NULL DEFAULT 80.00 CHECK(pass_threshold >= 0 AND pass_threshold <= 100),
                    attempt_limit INTEGER NOT NULL DEFAULT 3 CHECK(attempt_limit >= 1),
                    time_limit_minutes INTEGER CHECK(time_limit_minutes IS NULL OR time_limit_minutes > 0),
                    question_count INTEGER CHECK(question_count IS NULL OR question_count >= 1),
                    random_order BOOLEAN NOT NULL DEFAULT TRUE,
                    is_final BOOLEAN NOT NULL DEFAULT TRUE,
                    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'published', 'archived')),
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                CREATE TABLE IF NOT EXISTS lms_questions (
                    id SERIAL PRIMARY KEY,
                    test_id INTEGER NOT NULL REFERENCES lms_tests(id) ON DELETE CASCADE,
                    question_type VARCHAR(20) NOT NULL CHECK(question_type IN ('single', 'multiple', 'true_false', 'matching', 'text')),
                    prompt TEXT NOT NULL,
                    points NUMERIC(8,2) NOT NULL DEFAULT 1 CHECK(points > 0),
                    position INTEGER NOT NULL DEFAULT 1 CHECK(position >= 1),
                    required BOOLEAN NOT NULL DEFAULT TRUE,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    correct_text_answers JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                CREATE TABLE IF NOT EXISTS lms_question_options (
                    id SERIAL PRIMARY KEY,
                    question_id INTEGER NOT NULL REFERENCES lms_questions(id) ON DELETE CASCADE,
                    option_key VARCHAR(255),
                    option_text TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 1 CHECK(position >= 1),
                    is_correct BOOLEAN NOT NULL DEFAULT FALSE,
                    match_key VARCHAR(255),
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                );

                CREATE TABLE IF NOT EXISTS lms_course_assignments (
                    id SERIAL PRIMARY KEY,
                    course_id INTEGER NOT NULL REFERENCES lms_courses(id) ON DELETE CASCADE,
                    course_version_id INTEGER REFERENCES lms_course_versions(id) ON DELETE SET NULL,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    assigned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    due_at TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    status VARCHAR(20) NOT NULL DEFAULT 'assigned' CHECK(status IN ('assigned', 'in_progress', 'completed')),
                    completion_color_status VARCHAR(20) CHECK(completion_color_status IN ('green', 'orange', 'red')),
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    UNIQUE(course_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS lms_lesson_progress (
                    id SERIAL PRIMARY KEY,
                    assignment_id INTEGER NOT NULL REFERENCES lms_course_assignments(id) ON DELETE CASCADE,
                    lesson_id INTEGER NOT NULL REFERENCES lms_lessons(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    status VARCHAR(20) NOT NULL DEFAULT 'not_started' CHECK(status IN ('not_started', 'in_progress', 'completed')),
                    max_position_seconds NUMERIC(10,2) NOT NULL DEFAULT 0,
                    confirmed_seconds NUMERIC(10,2) NOT NULL DEFAULT 0,
                    completion_ratio NUMERIC(5,2) NOT NULL DEFAULT 0,
                    active_seconds INTEGER NOT NULL DEFAULT 0,
                    last_heartbeat_at TIMESTAMP,
                    last_event_at TIMESTAMP,
                    tab_hidden_count INTEGER NOT NULL DEFAULT 0,
                    stale_gap_count INTEGER NOT NULL DEFAULT 0,
                    anti_cheat_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
                    started_at TIMESTAMP,
                    content_completed_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    UNIQUE(assignment_id, lesson_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS lms_learning_sessions (
                    id SERIAL PRIMARY KEY,
                    assignment_id INTEGER NOT NULL REFERENCES lms_course_assignments(id) ON DELETE CASCADE,
                    lesson_id INTEGER NOT NULL REFERENCES lms_lessons(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    started_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    ended_at TIMESTAMP,
                    last_heartbeat_at TIMESTAMP,
                    last_visible_at TIMESTAMP,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    max_position_seconds NUMERIC(10,2) NOT NULL DEFAULT 0,
                    confirmed_seconds NUMERIC(10,2) NOT NULL DEFAULT 0,
                    active_seconds INTEGER NOT NULL DEFAULT 0,
                    tab_hidden_count INTEGER NOT NULL DEFAULT 0,
                    stale_gap_count INTEGER NOT NULL DEFAULT 0,
                    client_fingerprint VARCHAR(255)
                );

                CREATE TABLE IF NOT EXISTS lms_learning_events (
                    id SERIAL PRIMARY KEY,
                    session_id INTEGER REFERENCES lms_learning_sessions(id) ON DELETE CASCADE,
                    lesson_id INTEGER NOT NULL REFERENCES lms_lessons(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    event_type VARCHAR(50) NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    client_ts TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                CREATE TABLE IF NOT EXISTS lms_test_attempts (
                    id SERIAL PRIMARY KEY,
                    assignment_id INTEGER NOT NULL REFERENCES lms_course_assignments(id) ON DELETE CASCADE,
                    test_id INTEGER NOT NULL REFERENCES lms_tests(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
                    status VARCHAR(20) NOT NULL DEFAULT 'in_progress' CHECK(status IN ('in_progress', 'finished', 'expired')),
                    score_percent NUMERIC(6,2),
                    passed BOOLEAN,
                    started_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    finished_at TIMESTAMP,
                    duration_seconds INTEGER,
                    question_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    proctor_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
                    UNIQUE(assignment_id, test_id, attempt_no)
                );

                CREATE TABLE IF NOT EXISTS lms_test_attempt_answers (
                    id SERIAL PRIMARY KEY,
                    attempt_id INTEGER NOT NULL REFERENCES lms_test_attempts(id) ON DELETE CASCADE,
                    question_id INTEGER NOT NULL REFERENCES lms_questions(id) ON DELETE CASCADE,
                    answer_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    is_correct BOOLEAN,
                    points_awarded NUMERIC(8,2),
                    answered_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    UNIQUE(attempt_id, question_id)
                );

                CREATE TABLE IF NOT EXISTS lms_certificates (
                    id SERIAL PRIMARY KEY,
                    assignment_id INTEGER REFERENCES lms_course_assignments(id) ON DELETE SET NULL,
                    course_id INTEGER NOT NULL REFERENCES lms_courses(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    test_attempt_id INTEGER REFERENCES lms_test_attempts(id) ON DELETE SET NULL,
                    certificate_number VARCHAR(64) NOT NULL UNIQUE,
                    verify_token VARCHAR(128) NOT NULL UNIQUE,
                    score_percent NUMERIC(6,2),
                    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'revoked')),
                    issued_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    revoked_at TIMESTAMP,
                    revoked_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    revoke_reason TEXT,
                    pdf_storage_type VARCHAR(16) NOT NULL DEFAULT 'db' CHECK(pdf_storage_type IN ('db', 'gcs')),
                    pdf_data BYTEA,
                    gcs_bucket VARCHAR(255),
                    gcs_blob_path TEXT,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                );

                CREATE TABLE IF NOT EXISTS lms_notifications (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    notification_type VARCHAR(50) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    message TEXT,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    is_read BOOLEAN NOT NULL DEFAULT FALSE,
                    read_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                CREATE TABLE IF NOT EXISTS lms_admin_audit_log (
                    id SERIAL PRIMARY KEY,
                    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    actor_role VARCHAR(20),
                    action VARCHAR(100) NOT NULL,
                    entity_type VARCHAR(50) NOT NULL,
                    entity_id INTEGER,
                    details JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                -- LMS indexes
                CREATE INDEX IF NOT EXISTS idx_lms_courses_status ON lms_courses(status);
                CREATE INDEX IF NOT EXISTS idx_lms_course_versions_course_status ON lms_course_versions(course_id, status);
                CREATE INDEX IF NOT EXISTS idx_lms_modules_course_version_position ON lms_modules(course_version_id, position);
                CREATE INDEX IF NOT EXISTS idx_lms_lessons_module_position ON lms_lessons(module_id, position);
                CREATE INDEX IF NOT EXISTS idx_lms_lesson_materials_lesson_position ON lms_lesson_materials(lesson_id, position);
                CREATE INDEX IF NOT EXISTS idx_lms_tests_course_version ON lms_tests(course_version_id);
                CREATE INDEX IF NOT EXISTS idx_lms_tests_module_position ON lms_tests(module_id, position);
                CREATE INDEX IF NOT EXISTS idx_lms_tests_source_lesson ON lms_tests(source_lesson_id);
                CREATE INDEX IF NOT EXISTS idx_lms_questions_test_position ON lms_questions(test_id, position);
                CREATE INDEX IF NOT EXISTS idx_lms_question_options_question_position ON lms_question_options(question_id, position);
                CREATE INDEX IF NOT EXISTS idx_lms_assignments_user_status ON lms_course_assignments(user_id, status);
                CREATE INDEX IF NOT EXISTS idx_lms_assignments_course ON lms_course_assignments(course_id);
                CREATE INDEX IF NOT EXISTS idx_lms_assignments_due_at ON lms_course_assignments(due_at);
                CREATE INDEX IF NOT EXISTS idx_lms_lesson_progress_assignment ON lms_lesson_progress(assignment_id);
                CREATE INDEX IF NOT EXISTS idx_lms_lesson_progress_user_lesson ON lms_lesson_progress(user_id, lesson_id);
                CREATE INDEX IF NOT EXISTS idx_lms_lesson_progress_assignment_user_lesson ON lms_lesson_progress(assignment_id, user_id, lesson_id);
                CREATE INDEX IF NOT EXISTS idx_lms_lesson_progress_assignment_user_activity_at
                ON lms_lesson_progress(assignment_id, user_id, (COALESCE(last_heartbeat_at, last_event_at, updated_at)));
                CREATE INDEX IF NOT EXISTS idx_lms_learning_sessions_lesson_user_active ON lms_learning_sessions(lesson_id, user_id, is_active);
                CREATE INDEX IF NOT EXISTS idx_lms_learning_sessions_assignment_lesson_user_active_fp
                ON lms_learning_sessions(assignment_id, lesson_id, user_id, is_active, client_fingerprint);
                CREATE INDEX IF NOT EXISTS idx_lms_learning_sessions_assignment_user_started
                ON lms_learning_sessions(assignment_id, user_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_lms_learning_events_session_created ON lms_learning_events(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_lms_learning_events_session_type_created
                ON lms_learning_events(session_id, event_type, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_lms_learning_events_lesson_user_created
                ON lms_learning_events(lesson_id, user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_lms_test_attempts_assignment_test ON lms_test_attempts(assignment_id, test_id);
                CREATE INDEX IF NOT EXISTS idx_lms_test_attempts_assignment_user_test_status_passed ON lms_test_attempts(assignment_id, user_id, test_id, status, passed);
                CREATE INDEX IF NOT EXISTS idx_lms_test_attempts_assignment_user_started ON lms_test_attempts(assignment_id, user_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_lms_test_attempts_user_started ON lms_test_attempts(user_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_lms_attempt_answers_attempt ON lms_test_attempt_answers(attempt_id);
                CREATE INDEX IF NOT EXISTS idx_lms_certificates_user_status ON lms_certificates(user_id, status);
                CREATE INDEX IF NOT EXISTS idx_lms_certificates_token ON lms_certificates(verify_token);
                CREATE INDEX IF NOT EXISTS idx_lms_notifications_user_read ON lms_notifications(user_id, is_read, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_lms_admin_audit_actor_created ON lms_admin_audit_log(actor_id, created_at DESC);

                -- Operator profiles
                CREATE TABLE IF NOT EXISTS operator_profiles (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                    direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL,
                    supervisor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    is_active BOOLEAN NOT NULL DEFAULT FALSE,
                    rate DECIMAL(3,2) NOT NULL DEFAULT 1.00 CHECK(rate IN (1.00, 0.75, 0.50)),
                    sip_number VARCHAR(64),
                    has_proxy BOOLEAN NOT NULL DEFAULT FALSE,
                    proxy_card_number VARCHAR(64),
                    proxy_status VARCHAR(20) CHECK (proxy_status IN ('lost', 'returned_to_hr', 'not_received', 'on_hand') OR proxy_status IS NULL),
                    has_driver_license BOOLEAN NOT NULL DEFAULT FALSE,
                    internship_in_company BOOLEAN NOT NULL DEFAULT FALSE,
                    front_office_training BOOLEAN NOT NULL DEFAULT FALSE,
                    front_office_training_date DATE,
                    taxipro_id VARCHAR(128)
                );
                ALTER TABLE operator_profiles
                ADD COLUMN IF NOT EXISTS proxy_status VARCHAR(20)
                CHECK (proxy_status IN ('lost', 'returned_to_hr', 'not_received', 'on_hand') OR proxy_status IS NULL);
                ALTER TABLE operator_profiles
                DROP CONSTRAINT IF EXISTS operator_profiles_proxy_status_check;
                ALTER TABLE operator_profiles
                ADD CONSTRAINT operator_profiles_proxy_status_check
                    CHECK (proxy_status IN ('lost', 'returned_to_hr', 'not_received', 'on_hand') OR proxy_status IS NULL);

                -- User HR profiles (кадровые данные)
                CREATE TABLE IF NOT EXISTS user_hr_profiles (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                    phone VARCHAR(50),
                    email VARCHAR(255),
                    personal_email VARCHAR(255),
                    instagram VARCHAR(255),
                    telegram_nick VARCHAR(255),
                    company_name VARCHAR(255),
                    employment_type VARCHAR(10) CHECK (employment_type IN ('gph', 'of', 'smz') OR employment_type IS NULL),
                    study_place VARCHAR(255),
                    study_specialty VARCHAR(255),
                    study_course VARCHAR(100),
                    study_completed BOOLEAN NOT NULL DEFAULT FALSE,
                    study_completion_year INTEGER,
                    close_contact_1_relation VARCHAR(100),
                    close_contact_1_full_name VARCHAR(255),
                    close_contact_1_phone VARCHAR(50),
                    close_contact_2_relation VARCHAR(100),
                    close_contact_2_full_name VARCHAR(255),
                    close_contact_2_phone VARCHAR(50),
                    card_number VARCHAR(64),
                    city VARCHAR(255),
                    job_title VARCHAR(255)
                );
                ALTER TABLE user_hr_profiles
                ADD COLUMN IF NOT EXISTS employment_type VARCHAR(10);
                ALTER TABLE user_hr_profiles
                ADD COLUMN IF NOT EXISTS personal_email VARCHAR(255);
                ALTER TABLE user_hr_profiles
                ADD COLUMN IF NOT EXISTS city VARCHAR(255);
                ALTER TABLE user_hr_profiles
                ADD COLUMN IF NOT EXISTS job_title VARCHAR(255);
                ALTER TABLE user_hr_profiles
                DROP CONSTRAINT IF EXISTS user_hr_profiles_employment_type_check;
                ALTER TABLE user_hr_profiles
                ADD CONSTRAINT user_hr_profiles_employment_type_check
                    CHECK (employment_type IN ('gph', 'of', 'smz') OR employment_type IS NULL);

                -- Admin profiles
                CREATE TABLE IF NOT EXISTS admin_profiles (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                    feedback_telegram_report_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    auction_post_claim_notify_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    call_evaluation_notify_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    call_evaluation_notify_department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL
                );
                ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS auction_post_claim_notify_enabled BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS call_evaluation_notify_enabled BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS call_evaluation_notify_department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL;

                -- Подписка на автоотчёт по проведённым тренингам (задача #261).
                -- Три независимых флага, а не один: «за день», «за неделю» и «за
                -- месяц» отвечают на разные вопросы, и человек вправе взять
                -- только недельную сводку, не получая письма каждое утро.
                -- Периодичностей ровно три — их назвала постановка, — поэтому
                -- колонки, а не таблица подписок: она добавила бы JOIN и
                -- вторую точку правды к четырём уже живущим здесь флагам.
                ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS training_report_daily_enabled BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS training_report_weekly_enabled BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS training_report_monthly_enabled BOOLEAN NOT NULL DEFAULT FALSE;

                -- «Уведомления об изменениях» в разделе «Графики работы»:
                -- утренняя сводка за прошедшие сутки. Флаг ровно один —
                -- периодичность в постановке одна («сводку за день»), а лишняя
                -- периодичность здесь означала бы лишнее письмо, то есть ровно
                -- тот спам, которого владелец просил избежать.
                ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS schedule_change_report_enabled BOOLEAN NOT NULL DEFAULT FALSE;

                -- Отбивка «Табло Тез КЦ» лично себе: та же рассылка, что уходит
                -- группам, но бот пишет админу в личные сообщения. Флаг, а не
                -- строка в szov_wallboard_broadcast_chats: подписка принадлежит
                -- человеку, а не чату — сменит он Telegram или потеряет права, и
                -- рассылка обязана пойти следом, а не в прежний chat_id.
                ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS tez_broadcast_personal_enabled BOOLEAN NOT NULL DEFAULT FALSE;
                -- Режим личной отбивки — те же два, что у группы: каждую отбивку или только
                -- при отклонениях. Отбивка табло идёт каждый час круглые сутки, и без выбора
                -- лично подписанный админ получал бы 24 сообщения в сутки. Значение проверяет
                -- код (SZOV_BROADCAST_MODES), CHECK здесь не заводим.
                ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS tez_broadcast_personal_mode VARCHAR(16) NOT NULL DEFAULT 'always';
                -- То же для «Табло ОП · Чат» (#367, 25.09.2026): у каждого табло с личной
                -- отбивкой своя пара колонок — подписаться можно на одно и не получать другое.
                ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS op_chat_broadcast_personal_enabled BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS op_chat_broadcast_personal_mode VARCHAR(16) NOT NULL DEFAULT 'always';

                -- Indexes for new tables
                CREATE INDEX IF NOT EXISTS idx_departments_code ON departments(code);
                CREATE INDEX IF NOT EXISTS idx_departments_slug ON departments(slug);
                CREATE INDEX IF NOT EXISTS idx_departments_is_active ON departments(is_active);
                CREATE INDEX IF NOT EXISTS idx_users_department_id ON users(department_id);
                CREATE INDEX IF NOT EXISTS idx_directions_department_id ON directions(department_id);
                CREATE INDEX IF NOT EXISTS idx_operator_profiles_user_id ON operator_profiles(user_id);
                CREATE INDEX IF NOT EXISTS idx_operator_profiles_direction_id ON operator_profiles(direction_id);
                CREATE INDEX IF NOT EXISTS idx_operator_profiles_supervisor_id ON operator_profiles(supervisor_id);
                CREATE INDEX IF NOT EXISTS idx_operator_profiles_is_active ON operator_profiles(is_active);
                CREATE INDEX IF NOT EXISTS idx_user_hr_profiles_user_id ON user_hr_profiles(user_id);
                CREATE INDEX IF NOT EXISTS idx_admin_profiles_user_id ON admin_profiles(user_id);
                CREATE INDEX IF NOT EXISTS idx_admin_profiles_call_eval_notify_dept ON admin_profiles(call_evaluation_notify_department_id);

                -- Seed default department (СЗоВ)
                INSERT INTO departments (code, slug, name, description)
                VALUES ('szov', 'szov', 'СЗоВ — Служба заботы о водителях', 'Техническая поддержка водителей. Контроль качества звонков, оценка операторов, графики, обучение.')
                ON CONFLICT (code) DO NOTHING;

                -- =========================================================
                -- Этап 1: масштабируемая модель отделов + профилей
                -- (глава отдела с историей, перенос полей, JSONB-спецполя)
                -- =========================================================

                -- Глава отдела (денормализованный «текущий глава» для быстрых проверок)
                ALTER TABLE departments
                ADD COLUMN IF NOT EXISTS head_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

                -- Журнал назначений/снятий главы отдела (история)
                CREATE TABLE IF NOT EXISTS department_head_assignments (
                    id SERIAL PRIMARY KEY,
                    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
                    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    action VARCHAR(20) NOT NULL CHECK (action IN ('assigned', 'unassigned')),
                    changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                -- HR-профиль добирает перенесённые из users поля (proxy / driver license / онбординг)
                ALTER TABLE user_hr_profiles ADD COLUMN IF NOT EXISTS has_proxy BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE user_hr_profiles ADD COLUMN IF NOT EXISTS proxy_card_number VARCHAR(64);
                ALTER TABLE user_hr_profiles ADD COLUMN IF NOT EXISTS proxy_status VARCHAR(20);
                ALTER TABLE user_hr_profiles
                DROP CONSTRAINT IF EXISTS user_hr_profiles_proxy_status_check;
                ALTER TABLE user_hr_profiles
                ADD CONSTRAINT user_hr_profiles_proxy_status_check
                    CHECK (proxy_status IN ('lost', 'returned_to_hr', 'not_received', 'on_hand') OR proxy_status IS NULL);
                ALTER TABLE user_hr_profiles ADD COLUMN IF NOT EXISTS has_driver_license BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE user_hr_profiles ADD COLUMN IF NOT EXISTS internship_in_company BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE user_hr_profiles ADD COLUMN IF NOT EXISTS front_office_training BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE user_hr_profiles ADD COLUMN IF NOT EXISTS front_office_training_date DATE;

                -- Наименование специальности (задача #279): уточнение к «Месту учебы»
                ALTER TABLE user_hr_profiles ADD COLUMN IF NOT EXISTS study_specialty VARCHAR(255);

                -- JSONB-масштабируемость (гибрид): уникальные поля конкретного отдела
                ALTER TABLE operator_profiles ADD COLUMN IF NOT EXISTS attributes JSONB NOT NULL DEFAULT '{}'::jsonb;
                ALTER TABLE departments ADD COLUMN IF NOT EXISTS operator_field_schema JSONB NOT NULL DEFAULT '[]'::jsonb;

                -- Индексы для новых сущностей
                CREATE INDEX IF NOT EXISTS idx_departments_head_user_id ON departments(head_user_id);
                CREATE INDEX IF NOT EXISTS idx_dept_head_assignments_dept ON department_head_assignments(department_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_profiles_attributes ON operator_profiles USING GIN (attributes);

                -- Сид схемы спец-полей для СЗоВ (единственное уникальное поле — taxipro_id)
                UPDATE departments
                SET operator_field_schema = '[{"key":"taxipro_id","label":"TaxiPro ID","type":"text"}]'::jsonb
                WHERE code = 'szov' AND operator_field_schema = '[]'::jsonb;

                -- ======================= Успешки TEZ ОП =======================
                -- База лидов помесячная (СВ грузит её в учёте часов, внутри месяца
                -- загрузок несколько), а факт первой поездки — свойство САМОГО
                -- водителя, а не месяца. Поэтому два уровня: tez_drivers хранит
                -- первую поездку по номеру один раз на всё время, tez_leads —
                -- принадлежность номера базе конкретного месяца.
                CREATE TABLE IF NOT EXISTS tez_drivers (
                    phone_norm VARCHAR(16) PRIMARY KEY,
                    first_order_at TIMESTAMP WITH TIME ZONE,
                    first_order_checked_at TIMESTAMP WITH TIME ZONE,
                    check_attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                CREATE TABLE IF NOT EXISTS tez_lead_batches (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    file_name TEXT NOT NULL DEFAULT '',
                    rows_total INTEGER NOT NULL DEFAULT 0,
                    rows_new INTEGER NOT NULL DEFAULT 0,
                    rows_duplicate INTEGER NOT NULL DEFAULT 0,
                    rows_invalid INTEGER NOT NULL DEFAULT 0,
                    already_working INTEGER NOT NULL DEFAULT 0,
                    check_status VARCHAR(20) NOT NULL DEFAULT 'pending'
                        CHECK (check_status IN ('pending', 'running', 'done', 'error')),
                    check_error TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                CREATE TABLE IF NOT EXISTS tez_leads (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    phone_norm VARCHAR(16) NOT NULL REFERENCES tez_drivers(phone_norm) ON DELETE CASCADE,
                    full_name TEXT NOT NULL DEFAULT '',
                    first_batch_id UUID REFERENCES tez_lead_batches(id) ON DELETE SET NULL,
                    last_batch_id UUID REFERENCES tez_lead_batches(id) ON DELETE SET NULL,
                    upload_count INTEGER NOT NULL DEFAULT 1,
                    status VARCHAR(20) NOT NULL DEFAULT 'new'
                        CHECK (status IN ('new', 'in_progress', 'already_working', 'success', 'not_counted')),
                    status_rule VARCHAR(32),
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    UNIQUE(year, month, phone_norm)
                );

                -- Каждая строка каждой загрузки: отсюда видно, сколько раз номер
                -- приходил повторно и когда именно (владелец просил историю дублей).
                CREATE TABLE IF NOT EXISTS tez_lead_batch_rows (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    batch_id UUID NOT NULL REFERENCES tez_lead_batches(id) ON DELETE CASCADE,
                    row_number INTEGER NOT NULL DEFAULT 0,
                    raw_full_name TEXT NOT NULL DEFAULT '',
                    raw_phone TEXT NOT NULL DEFAULT '',
                    phone_norm VARCHAR(16),
                    lead_id UUID REFERENCES tez_leads(id) ON DELETE SET NULL,
                    row_status VARCHAR(20) NOT NULL
                        CHECK (row_status IN ('new', 'duplicate', 'invalid')),
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                -- Звонки Binotel, из которых выводится и «Обзвонено», и успешка.
                -- Наполняется с двух сторон: история по номеру клиента для тех,
                -- кто выехал (stats/history-by-external-number, за всё время), и
                -- зеркало по дням за окно месяца (stats/list-of-calls-for-period).
                -- Зеркало кладёт сюда ВЕСЬ трафик компании за день, включая номера,
                -- которых пока нет ни в одной базе: базы грузят весь месяц, и лид,
                -- добавленный 20-го, должен сразу видеть свои попытки с 1-го, а не
                -- ждать повторной выкачки. Связь с лидом — по phone_norm, лишние
                -- строки в JOIN просто не участвуют. is_qualifying — вычисляемый
                -- флаг: смена порога длительности пересчитывается локально.
                CREATE TABLE IF NOT EXISTS tez_lead_calls (
                    general_call_id VARCHAR(32) PRIMARY KEY,
                    phone_norm VARCHAR(16) NOT NULL,
                    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    call_type SMALLINT NOT NULL DEFAULT -1,
                    billsec INTEGER NOT NULL DEFAULT 0,
                    waitsec INTEGER NOT NULL DEFAULT 0,
                    disposition VARCHAR(32) NOT NULL DEFAULT '',
                    internal_number VARCHAR(64) NOT NULL DEFAULT '',
                    employee_name TEXT NOT NULL DEFAULT '',
                    employee_email TEXT NOT NULL DEFAULT '',
                    operator_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    is_qualifying BOOLEAN NOT NULL DEFAULT FALSE,
                    fetched_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                -- Журнал зеркала: какой день уже перекачан из Binotel целиком.
                -- Без него добор пропущенных дней пришлось бы угадывать по
                -- наличию звонков, а «в этот день никто не звонил» неотличимо от
                -- «этот день ещё не качали».
                CREATE TABLE IF NOT EXISTS tez_call_sync_days (
                    day DATE PRIMARY KEY,
                    calls INTEGER NOT NULL DEFAULT 0,
                    synced_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                -- Успешка: связка «кто звонил и когда -> кто вышел на линию и когда».
                -- UNIQUE(lead_id) закрепляет инвариант «один лид = максимум одна успешка».
                CREATE TABLE IF NOT EXISTS tez_lead_successes (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    lead_id UUID NOT NULL UNIQUE REFERENCES tez_leads(id) ON DELETE CASCADE,
                    phone_norm VARCHAR(16) NOT NULL,
                    operator_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    operator_name TEXT NOT NULL DEFAULT '',
                    call_general_id VARCHAR(32),
                    call_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    first_order_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    success_date DATE NOT NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    rule_code VARCHAR(32) NOT NULL,
                    is_late BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );

                -- Даты заказов ОКОННЫЕ (TEZ APP отдаёт первый заказ в запрошенном
                -- месяце и в предыдущем), поэтому живут на лиде, а не на водителе:
                -- один номер в июньской и июльской базе имеет разные даты.
                -- tez_drivers.first_order_at остаётся как реестр номеров/легаси.
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS month_first_order_at TIMESTAMP WITH TIME ZONE;
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS prev_month_first_order_at TIMESTAMP WITH TIME ZONE;
                -- Последний завершённый заказ водителя ДО 1-го числа месяца лида.
                -- Пришло на смену prev_month_first_order_at (TEZ APP убрал его из
                -- ответа 11.08.2026): по первому заказу прошлого месяца разрыв
                -- между заказами посчитать нельзя, а всё правило «спал 30 дней»
                -- держится именно на нём. Значение для (год, месяц) неизменно,
                -- поэтому пишется через COALESCE и не перетирается.
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS last_order_before_at TIMESTAMP WITH TIME ZONE;
                -- Перенос: лид, не ставший успешкой, дорабатывается ещё один
                -- месяц. Ответ TEZ APP за СЛЕДУЮЩИЙ месяц кладём в отдельные
                -- колонки, а не в основные: month_first_order_at защищён
                -- COALESCE и застыл бы навсегда чужой датой, а воронка месяца
                -- базы посчитала бы поездку следующего месяца своей.
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS carry_first_order_at TIMESTAMP WITH TIME ZONE;
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS carry_last_order_before_at TIMESTAMP WITH TIME ZONE;
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS carry_checked_at TIMESTAMP WITH TIME ZONE;
                -- Статус того же лида в месяце ДОРАБОТКИ. Своя пара колонок по
                -- той же причине, что и у carry_first_order_at: status описывает
                -- месяц базы, и перенос не имеет права его переписать (пересчёт
                -- месяца базы тут же вернул бы всё назад, а статус мигал бы
                -- между прогонами). Без этих колонок результат доработки нигде
                -- не хранился: расчёт его получал и выбрасывал, поэтому в отчёте
                -- у перенесённого лида стоял его прошлый статус.
                -- Месяц в колонках не нужен: перенос — это всегда ровно +1 месяц
                -- к месяцу базы (инвариант get_tez_leads_for_recompute), состояния
                -- «перенесён дважды» нечем выразить.
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS carry_status VARCHAR(20)
                    CHECK (carry_status IN ('new', 'in_progress', 'already_working',
                                            'success', 'not_counted'));
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS carry_status_rule VARCHAR(32);
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS first_order_checked_at TIMESTAMP WITH TIME ZONE;
                -- Отметка «звонки по этому лиду уже подняли из Binotel». Гейтить
                -- докачку звонков статусом нельзя: лид со СТАРЫМ статусом
                -- (already_working от прежней логики) иначе никогда не получит
                -- свежие звонки и навсегда останется без успешки. Дата поездки уже
                -- известна, звонки до неё в прошлом — одной докачки достаточно.
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS calls_synced_at TIMESTAMP WITH TIME ZONE;
                -- Происхождение состояния: NULL — ещё не вычисленный живой лид,
                -- 0 — результат реального пересчёта, положительное значение —
                -- event_seq архивного снимка. Это не даёт restore перезаписать
                -- свежий вычисленный результат и делает порядок откатов A/B
                -- детерминированным.
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS archive_state_event_seq BIGINT;
                -- «Воронка ОП» Тез (#407): ответ TEZ APP о водителе (поля с 04.11.2026) —
                -- создание аккаунта и начало последней завершённой поездки за всё время, от
                -- месяца не зависят. account_checked_at — когда TEZ APP о них ответил: NULL —
                -- ещё не спрашивали, сверка спросит такого лида один раз, даже если поездка в
                -- месяце уже найдена. city — колонка «город» файла базы (загрузка через «Базу
                -- водителей» раздела «Воронка ОП Tez»); '' — не указан.
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS registered_at TIMESTAMP WITH TIME ZONE;
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS last_order_at TIMESTAMP WITH TIME ZONE;
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS account_checked_at TIMESTAMP WITH TIME ZONE;
                ALTER TABLE tez_leads ADD COLUMN IF NOT EXISTS city VARCHAR(64) NOT NULL DEFAULT '';

                CREATE INDEX IF NOT EXISTS idx_tez_leads_pending_order_check
                    ON tez_leads(year, month) WHERE month_first_order_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_tez_drivers_pending_check
                    ON tez_drivers(first_order_checked_at) WHERE first_order_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_tez_leads_period_status ON tez_leads(year, month, status);
                CREATE INDEX IF NOT EXISTS idx_tez_leads_phone ON tez_leads(phone_norm);
                CREATE INDEX IF NOT EXISTS idx_tez_lead_batch_rows_batch ON tez_lead_batch_rows(batch_id);
                CREATE INDEX IF NOT EXISTS idx_tez_lead_batches_period ON tez_lead_batches(year, month, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tez_lead_calls_phone_started ON tez_lead_calls(phone_norm, started_at);
                CREATE INDEX IF NOT EXISTS idx_tez_lead_calls_qualifying
                    ON tez_lead_calls(phone_norm, started_at) WHERE is_qualifying;
                -- «Обзвонено» = попытки оператора ОП: исходящий с разрезолвленным
                -- оператором, длительность не важна. Зеркало кладёт в таблицу и
                -- чужой трафик, поэтому фильтр вынесен в частичный индекс.
                CREATE INDEX IF NOT EXISTS idx_tez_lead_calls_op_attempts
                    ON tez_lead_calls(phone_norm, started_at)
                    WHERE call_type = 1 AND operator_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_tez_successes_operator_period
                    ON tez_lead_successes(operator_id, year, month);
                CREATE INDEX IF NOT EXISTS idx_tez_successes_date ON tez_lead_successes(success_date);
                -- Один ответ /api/tez_leads/stats делает четыре прохода по
                -- успешкам периода (счётчик, рейтинг, по дням, план отдела) —
                -- без этого индекса все четыре шли Seq Scan'ом.
                CREATE INDEX IF NOT EXISTS idx_tez_successes_period
                    ON tez_lead_successes(year, month);

                -- Страховка от двойного зачёта одной поездки. UNIQUE(lead_id)
                -- её не даёт: один номер живёт отдельной строкой в базе каждого
                -- месяца (UNIQUE(year, month, phone_norm)), и оба лида видят
                -- один и тот же список звонков. Сегодня дубль невозможен по
                -- построению правила, но как только лид обрабатывается дольше
                -- одного месяца, защита нужна на уровне схемы.
                -- Индекс создаём мягко: если дубли уже есть, приложение обязано
                -- подняться и написать в лог, а не упасть на старте целиком.
                DO $$
                BEGIN
                    BEGIN
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_tez_successes_phone_date
                            ON tez_lead_successes(phone_norm, success_date);
                    EXCEPTION WHEN unique_violation THEN
                        RAISE WARNING 'uq_tez_successes_phone_date не создан: '
                                      'в tez_lead_successes есть дубли (номер, дата поездки)';
                    END;
                END
                $$;

                -- ── Удаление загруженной базы с историей и откатом ──
                -- Сама загрузка удаляется МЯГКО (deleted_at), а вот её лиды —
                -- жёстко. Иначе пришлось бы дописывать «AND deleted_at IS NULL» в
                -- каждый читающий запрос (воронка, рейтинг, успешки по дням,
                -- детализация, экспорт, план ОП, ночные выборки номеров), и любой
                -- пропущенный фильтр молча утёк бы в расчёт зарплаты. Возврат
                -- обеспечивают архивные таблицы ниже: там лежит полный снимок всего,
                -- что удалили, включая исходные UUID.
                ALTER TABLE tez_lead_batches ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE;
                ALTER TABLE tez_lead_batches ADD COLUMN IF NOT EXISTS deleted_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
                ALTER TABLE tez_lead_batches ADD COLUMN IF NOT EXISTS delete_reason TEXT;
                ALTER TABLE tez_lead_batches ADD COLUMN IF NOT EXISTS restored_at TIMESTAMP WITH TIME ZONE;
                ALTER TABLE tez_lead_batches ADD COLUMN IF NOT EXISTS restored_by INTEGER REFERENCES users(id) ON DELETE SET NULL;

                -- Отдельная sequence нужна и свежей схеме, и детерминированной
                -- миграции ранней схемы. ADD BIGSERIAL IF NOT EXISTS здесь
                -- намеренно не используется: на старых PostgreSQL он мог плодить
                -- лишние sequence при каждом запуске init.
                CREATE SEQUENCE IF NOT EXISTS tez_lead_batch_event_order_seq;

                -- Журнал «кто удалил / кто вернул» — append-only, не чистится при
                -- откате: история должна пережить и удаление, и восстановление.
                CREATE TABLE IF NOT EXISTS tez_lead_batch_events (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    event_seq BIGINT NOT NULL
                        DEFAULT nextval('tez_lead_batch_event_order_seq') UNIQUE,
                    batch_id UUID NOT NULL REFERENCES tez_lead_batches(id) ON DELETE RESTRICT,
                    action VARCHAR(16) NOT NULL CHECK (action IN ('delete', 'restore')),
                    source_event_id UUID REFERENCES tez_lead_batch_events(id) ON DELETE RESTRICT,
                    actor_id INTEGER,
                    actor_name TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    leads_removed INTEGER NOT NULL DEFAULT 0,
                    leads_kept INTEGER NOT NULL DEFAULT 0,
                    successes_removed INTEGER NOT NULL DEFAULT 0,
                    leads_merged INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                ALTER TABLE tez_lead_batch_events ADD COLUMN IF NOT EXISTS event_seq BIGINT;
                ALTER TABLE tez_lead_batch_events ADD COLUMN IF NOT EXISTS source_event_id UUID
                    REFERENCES tez_lead_batch_events(id) ON DELETE RESTRICT;
                ALTER TABLE tez_lead_batch_events ADD COLUMN IF NOT EXISTS actor_name TEXT NOT NULL DEFAULT '';
                -- Для ранней схемы последовательность событий восстанавливаем в
                -- устойчивом порядке. Уже назначенные номера не трогаем. Таблицу
                -- на время синхронизации sequence закрываем от INSERT, чтобы
                -- параллельное audit-событие не получило номер между MAX и setval.
                LOCK TABLE tez_lead_batch_events IN SHARE ROW EXCLUSIVE MODE;
                SELECT setval(
                    'tez_lead_batch_event_order_seq',
                    COALESCE(MAX(event_seq), 1),
                    MAX(event_seq) IS NOT NULL
                )
                FROM tez_lead_batch_events;
                WITH missing_events AS (
                    SELECT ordered.id,
                           nextval('tez_lead_batch_event_order_seq') AS assigned_seq
                    FROM (
                        SELECT id
                        FROM tez_lead_batch_events
                        WHERE event_seq IS NULL
                        ORDER BY created_at NULLS FIRST, id
                    ) ordered
                )
                UPDATE tez_lead_batch_events e
                SET event_seq = m.assigned_seq
                FROM missing_events m
                WHERE e.id = m.id;
                SELECT setval(
                    'tez_lead_batch_event_order_seq',
                    COALESCE(MAX(event_seq), 1),
                    MAX(event_seq) IS NOT NULL
                )
                FROM tez_lead_batch_events;
                ALTER TABLE tez_lead_batch_events
                    ALTER COLUMN event_seq
                    SET DEFAULT nextval('tez_lead_batch_event_order_seq');
                ALTER TABLE tez_lead_batch_events ALTER COLUMN event_seq SET NOT NULL;
                ALTER TABLE tez_lead_batch_events ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP;
                -- actor_id намеренно без FK: числовой id и снимок имени должны
                -- пережить удаление аккаунта автора из users.
                UPDATE tez_lead_batch_events e
                SET actor_name = u.name
                FROM users u
                WHERE e.actor_id = u.id
                  AND BTRIM(COALESCE(e.actor_name, '')) = '';
                ALTER TABLE tez_lead_batch_events
                    DROP CONSTRAINT IF EXISTS tez_lead_batch_events_actor_id_fkey;

                -- Снимок удалённых лидов типизированными колонками (а не JSONB):
                -- восстановление — обычный INSERT ... SELECT, а не разбор структуры.
                -- first/last_batch_id БЕЗ внешнего ключа: архив должен пережить
                -- любую дальнейшую судьбу самих загрузок.
                CREATE TABLE IF NOT EXISTS tez_lead_archived_leads (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    event_id UUID NOT NULL REFERENCES tez_lead_batch_events(id) ON DELETE RESTRICT,
                    lead_id UUID NOT NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    phone_norm VARCHAR(16) NOT NULL,
                    full_name TEXT NOT NULL DEFAULT '',
                    first_batch_id UUID,
                    last_batch_id UUID,
                    upload_count INTEGER NOT NULL DEFAULT 1,
                    status VARCHAR(20) NOT NULL DEFAULT 'new',
                    status_rule VARCHAR(32),
                    month_first_order_at TIMESTAMP WITH TIME ZONE,
                    prev_month_first_order_at TIMESTAMP WITH TIME ZONE,
                    first_order_checked_at TIMESTAMP WITH TIME ZONE,
                    calls_synced_at TIMESTAMP WITH TIME ZONE,
                    lead_created_at TIMESTAMP,
                    lead_updated_at TIMESTAMP,
                    restored_at TIMESTAMP WITH TIME ZONE,
                    restored_lead_id UUID
                );
                ALTER TABLE tez_lead_archived_leads ADD COLUMN IF NOT EXISTS restored_lead_id UUID;
                -- Город лида (06.11.2026) переживает удаление и возврат загрузки. Ответ TEZ APP
                -- об аккаунте не архивируется: у вернувшегося лида account_checked_at пуст, и
                -- сверка спросит его заново.
                ALTER TABLE tez_lead_archived_leads ADD COLUMN IF NOT EXISTS city VARCHAR(64) NOT NULL DEFAULT '';

                CREATE TABLE IF NOT EXISTS tez_lead_archived_successes (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    event_id UUID NOT NULL REFERENCES tez_lead_batch_events(id) ON DELETE RESTRICT,
                    success_id UUID NOT NULL,
                    lead_id UUID NOT NULL,
                    phone_norm VARCHAR(16) NOT NULL,
                    operator_id INTEGER,
                    operator_name TEXT NOT NULL DEFAULT '',
                    call_general_id VARCHAR(32),
                    call_at TIMESTAMP WITH TIME ZONE,
                    first_order_at TIMESTAMP WITH TIME ZONE,
                    success_date DATE,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    rule_code VARCHAR(32) NOT NULL DEFAULT '',
                    is_late BOOLEAN NOT NULL DEFAULT FALSE,
                    success_created_at TIMESTAMP,
                    success_updated_at TIMESTAMP,
                    restored_at TIMESTAMP WITH TIME ZONE,
                    restored_lead_id UUID,
                    restored_success_id UUID
                );
                ALTER TABLE tez_lead_archived_successes ADD COLUMN IF NOT EXISTS success_id UUID;
                ALTER TABLE tez_lead_archived_successes ADD COLUMN IF NOT EXISTS success_updated_at TIMESTAMP;
                ALTER TABLE tez_lead_archived_successes ADD COLUMN IF NOT EXISTS restored_lead_id UUID;
                ALTER TABLE tez_lead_archived_successes ADD COLUMN IF NOT EXISTS restored_success_id UUID;
                -- Совместимость с ранней локальной версией миграции, где UUID
                -- самой успешки ещё не сохранялся.
                UPDATE tez_lead_archived_successes SET success_id = id WHERE success_id IS NULL;
                ALTER TABLE tez_lead_archived_successes ALTER COLUMN success_id SET NOT NULL;

                -- Ранняя локальная версия этих таблиц использовала CASCADE и могла
                -- стереть аудит при физическом DELETE родителя. Переводим уже
                -- созданные FK на RESTRICT один раз; свежая схема сразу создаётся
                -- с правильным действием.
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'tez_lead_batch_events_batch_id_fkey'
                          AND conrelid = 'tez_lead_batch_events'::regclass
                          AND confdeltype = 'c'
                    ) THEN
                        EXECUTE 'ALTER TABLE tez_lead_batch_events '
                                'DROP CONSTRAINT tez_lead_batch_events_batch_id_fkey';
                        EXECUTE 'ALTER TABLE tez_lead_batch_events '
                                'ADD CONSTRAINT tez_lead_batch_events_batch_id_fkey '
                                'FOREIGN KEY (batch_id) REFERENCES tez_lead_batches(id) ON DELETE RESTRICT';
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'tez_lead_archived_leads_event_id_fkey'
                          AND conrelid = 'tez_lead_archived_leads'::regclass
                          AND confdeltype = 'c'
                    ) THEN
                        EXECUTE 'ALTER TABLE tez_lead_archived_leads '
                                'DROP CONSTRAINT tez_lead_archived_leads_event_id_fkey';
                        EXECUTE 'ALTER TABLE tez_lead_archived_leads '
                                'ADD CONSTRAINT tez_lead_archived_leads_event_id_fkey '
                                'FOREIGN KEY (event_id) REFERENCES tez_lead_batch_events(id) ON DELETE RESTRICT';
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'tez_lead_archived_successes_event_id_fkey'
                          AND conrelid = 'tez_lead_archived_successes'::regclass
                          AND confdeltype = 'c'
                    ) THEN
                        EXECUTE 'ALTER TABLE tez_lead_archived_successes '
                                'DROP CONSTRAINT tez_lead_archived_successes_event_id_fkey';
                        EXECUTE 'ALTER TABLE tez_lead_archived_successes '
                                'ADD CONSTRAINT tez_lead_archived_successes_event_id_fkey '
                                'FOREIGN KEY (event_id) REFERENCES tez_lead_batch_events(id) ON DELETE RESTRICT';
                    END IF;
                END
                $$;

                CREATE INDEX IF NOT EXISTS idx_tez_lead_batch_events_batch_seq
                    ON tez_lead_batch_events(batch_id, event_seq DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tez_lead_batch_events_seq
                    ON tez_lead_batch_events(event_seq);
                CREATE INDEX IF NOT EXISTS idx_tez_archived_leads_event ON tez_lead_archived_leads(event_id);
                CREATE INDEX IF NOT EXISTS idx_tez_archived_successes_event ON tez_lead_archived_successes(event_id);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tez_archived_leads_event_lead
                    ON tez_lead_archived_leads(event_id, lead_id);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tez_archived_successes_event_success
                    ON tez_lead_archived_successes(event_id, success_id);

                -- Бэкофилл привязки направлений к отделу: всё, что без отдела — в СЗоВ
                -- (исторически направления создавались до разделения на отделы).
                UPDATE directions
                SET department_id = (SELECT id FROM departments WHERE code = 'szov' LIMIT 1)
                WHERE department_id IS NULL;
            """)
            self._init_group_late_bot_schema_tx(cursor)
            self._init_amo_leads_schema_tx(cursor)
            self._init_chat_hourly_schema_tx(cursor)
            self._init_reg_contest_schema_tx(cursor)
            self._init_front_office_calls_schema_tx(cursor)
            self._init_wiki_schema_tx(cursor)
            self._init_news_schema_tx(cursor)
            self._init_crm_schema_tx(cursor)
            self._init_parcels_schema_tx(cursor)
            self._init_driver_chats_schema_tx(cursor)
            self._init_sign_links_schema_tx(cursor)
            self._init_partner_keys_schema_tx(cursor)
            self._init_calls_api_schema_tx(cursor)
            self._init_oktell_guard_schema_tx(cursor)
            self._init_fleet_edm_schema_tx(cursor)
            self._init_driver_mailings_schema_tx(cursor)
            self._init_icore_phone_schema_tx(cursor)
            self._init_trainings_schema_tx(cursor)
            self._init_trainer_schema_tx(cursor)
            self._init_cdr_schema_tx(cursor)
            self._init_pbx_schema_tx(cursor)
            self._init_olx_amo_schema_tx(cursor)
            self._init_olx_ads_schema_tx(cursor)
            self._init_op_funnel_schema_tx(cursor)
            self._init_dial_list_schema_tx(cursor)
            self._init_qa_marketing_schema_tx(cursor)
            self._init_payments_schema_tx(cursor)
            self._init_library_schema_tx(cursor)
            self._init_complaints_schema_tx(cursor)
            self._init_discipline_schema_tx(cursor)
            self._init_leave_requests_schema_tx(cursor)
            self._init_water_schema_tx(cursor)
            self._init_thermoboxes_schema_tx(cursor)
            self._init_honor_board_schema_tx(cursor)
            self._init_test_numbers_schema_tx(cursor)
            self._init_baiga_schema_tx(cursor)
            self._init_twofa_schema_tx(cursor)
            self._init_rental_schema_tx(cursor)
            self._init_hiring_schema_tx(cursor)
            self._init_wazzup_workspace_schema_tx(cursor)
            self._backfill_shift_auction_history_tables_tx(cursor)
            self._backfill_user_profiles_tx(cursor)
            self._backfill_work_hours_rate_from_history_tx(cursor)
            # Сидинг групп из текущих супервайзеров (одноразово, идемпотентно).
            # Под SAVEPOINT — сбой сидинга не должен ронять инициализацию БД.
            try:
                cursor.execute("SAVEPOINT sp_groups_backfill")
                self._backfill_groups_from_supervisors_tx(cursor)
                cursor.execute("RELEASE SAVEPOINT sp_groups_backfill")
            except Exception as exc:
                if _is_schema_lock_conflict(exc):
                    raise
                cursor.execute("ROLLBACK TO SAVEPOINT sp_groups_backfill")
                logging.error("groups backfill skipped due to error: %s", exc, exc_info=True)

            # Бэкфилл осиротевших group_id (NULL) из членства — идемпотентно, под SAVEPOINT.
            try:
                cursor.execute("SAVEPOINT sp_null_group_backfill")
                self._backfill_null_group_id_tx(cursor)
                cursor.execute("RELEASE SAVEPOINT sp_null_group_backfill")
            except Exception as exc:
                if _is_schema_lock_conflict(exc):
                    raise
                cursor.execute("ROLLBACK TO SAVEPOINT sp_null_group_backfill")
                logging.error("null group_id backfill skipped: %s", exc, exc_info=True)

            # Триггеры мгновенных уведомлений (канал bell_events). Под SAVEPOINT:
            # без них колокол просто остаётся на обновлении по фокусу, ронять
            # инициализацию базы из-за реалтайма нельзя.
            try:
                cursor.execute("SAVEPOINT sp_bell_notify")
                self._init_bell_notify_schema_tx(cursor)
                self._backfill_task_accepted_reads_tx(cursor)
                cursor.execute("RELEASE SAVEPOINT sp_bell_notify")
            except Exception as exc:
                if _is_schema_lock_conflict(exc):
                    raise
                cursor.execute("ROLLBACK TO SAVEPOINT sp_bell_notify")
                logging.error("bell notify triggers skipped: %s", exc, exc_info=True)

    def _backfill_task_accepted_reads_tx(self, cursor):
        """Одноразово помечаем уже принятые задачи просмотренными.

        Уведомление «работу приняли» появилось позже самих задач, и без этого
        при первом же запуске людям прилетела бы вся история разом: на боевой
        базе это 73 принятые задачи у одного человека — колокол превратился бы
        в свалку, а счётчик перестал бы что-либо значить.

        Идемпотентность по состоянию, как у остальных сидингов: появилась хоть
        одна отметка этого вида — значит бэкфилл уже отработал, и новые
        принятия гасить нельзя, они и есть уведомления.
        """
        cursor.execute("SELECT 1 FROM task_action_reads WHERE kind = 'accepted' LIMIT 1")
        if cursor.fetchone() is not None:
            return

        # По исполнителю на строку: у задачи их может быть несколько, и каждому
        # иначе при первом запуске прилетела бы вся история принятых задач.
        cursor.execute("""
            INSERT INTO task_action_reads (user_id, task_id, kind, seen_at)
            SELECT ta.user_id, t.id, 'accepted',
                   (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
              FROM tasks t
              JOIN task_assignees ta ON ta.task_id = t.id
             WHERE t.status = 'accepted'
            ON CONFLICT (user_id, task_id) DO UPDATE
                SET kind = EXCLUDED.kind, seen_at = EXCLUDED.seen_at
        """)

    def _init_bell_notify_schema_tx(self, cursor):
        """Триггеры мгновенных уведомлений: запись в таблицу источника — pg_notify.

        Триггеры, а не вызовы в коде записи: точек записи много и часть из них
        не проходит через роуты вовсе (ночной materialize_due_regulation_tasks
        создаёт задачи регламентов, назначения опросов пишутся пачками) —
        триггер не забудешь позвать. Payload несёт только адресатов
        ('{"u":[id,...]}' либо '{"b":1}' для широковещательного), данные никто
        не передаёт: получив тычок, клиент сам перечитывает /api/notifications,
        поэтому потерянный тычок — это максимум задержка до обновления по
        фокусу, а не потерянное уведомление.

        «Ивенты» и «4 You» широковещательные: их видимость зависит от отдела
        зрителя, и вычислять круг адресатов в триггере значило бы продублировать
        _events_viewer_scope на PL/pgSQL. Дешевле разбудить всех — каждый клиент
        перечитает свою сводку, и сервер сам отфильтрует по периметру.
        """
        cursor.execute("""
            CREATE OR REPLACE FUNCTION bell_notify_change()
            RETURNS TRIGGER
            LANGUAGE plpgsql
            AS $$
            DECLARE
                targets INTEGER[] := ARRAY[]::INTEGER[];
            BEGIN
                IF TG_TABLE_NAME IN ('events', 'four_you_images', 'news_posts', 'honor_boards') THEN
                    -- Новость широковещательная по той же причине, что ивенты:
                    -- круг адресатов задан правилами (отдел, направление,
                    -- группа, должность), и разворачивать их в PL/pgSQL значило
                    -- бы продублировать news/access.py на втором языке. Дешевле
                    -- разбудить всех — каждый клиент спросит своё /pending, и
                    -- сервер отфильтрует по тем же правилам, что и всегда.
                    -- Доска почёта (#318) видна всем сотрудникам — её выход
                    -- будит всех буквально.
                    PERFORM pg_notify('bell_events', '{"b":1}');
                    RETURN NULL;
                ELSIF TG_TABLE_NAME = 'lms_notifications' THEN
                    targets := ARRAY[NEW.user_id];
                ELSIF TG_TABLE_NAME = 'surveys' THEN
                    -- Уход опроса в архив убирает его из сводки у ВСЕХ, кому он
                    -- был назначен: адресатов достаём из назначений, своего
                    -- поля с получателями у опроса нет.
                    targets := ARRAY(
                        SELECT sa.operator_id FROM survey_assignments sa
                         WHERE sa.survey_id = NEW.id
                    );
                ELSIF TG_TABLE_NAME = 'survey_assignments' THEN
                    -- В черновике теста UPDATE всегда упоминает status и
                    -- started_at. Состав колокола меняется лишь при смене
                    -- адресата/опроса или переходе через границу completed.
                    IF TG_OP = 'UPDATE'
                       AND OLD.operator_id IS NOT DISTINCT FROM NEW.operator_id
                       AND OLD.survey_id IS NOT DISTINCT FROM NEW.survey_id
                       AND (COALESCE(OLD.status, '') = 'completed') IS NOT DISTINCT FROM
                           (COALESCE(NEW.status, '') = 'completed') THEN
                        RETURN NULL;
                    END IF;
                    targets := ARRAY[NEW.operator_id];
                    IF TG_OP = 'UPDATE' THEN
                        targets := targets || ARRAY[OLD.operator_id];
                    END IF;
                ELSIF TG_TABLE_NAME = 'wiki_ack_assignments' THEN
                    -- Прокрутка и раскрытие блоков меняют служебный прогресс,
                    -- но уведомление остаётся тем же. Будим только если
                    -- изменилась его видимость, адресат, статья или срок.
                    IF TG_OP = 'UPDATE'
                       AND OLD.user_id IS NOT DISTINCT FROM NEW.user_id
                       AND OLD.article_id IS NOT DISTINCT FROM NEW.article_id
                       AND OLD.due_at IS NOT DISTINCT FROM NEW.due_at
                       AND (OLD.acknowledged_at IS NULL) IS NOT DISTINCT FROM
                           (NEW.acknowledged_at IS NULL)
                       AND COALESCE(OLD.status IN ('superseded', 'cancelled'), FALSE)
                           IS NOT DISTINCT FROM
                           COALESCE(NEW.status IN ('superseded', 'cancelled'), FALSE) THEN
                        RETURN NULL;
                    END IF;
                    targets := ARRAY[NEW.user_id];
                    IF TG_OP = 'UPDATE' THEN
                        targets := targets || ARRAY[OLD.user_id];
                    END IF;
                ELSIF TG_TABLE_NAME IN ('event_reads', 'four_you_reads', 'honor_board_reads') THEN
                    -- Водяной знак гасит бейдж. Адресная тычка синхронизирует
                    -- остальные вкладки того же пользователя без массовой
                    -- перечитки у всех подключённых клиентов.
                    targets := ARRAY[NEW.user_id];
                    IF TG_OP = 'UPDATE' THEN
                        targets := targets || ARRAY[OLD.user_id];
                    END IF;
                ELSIF TG_TABLE_NAME = 'birthday_reads' THEN
                    -- Тот же водяной знак, но OLD.user_id тут не нужен: user_id
                    -- первичный ключ, при перезаписи отметки он не меняется.
                    targets := ARRAY[NEW.user_id];
                ELSIF TG_TABLE_NAME = 'task_action_reads' THEN
                    targets := ARRAY[NEW.user_id];
                ELSIF TG_TABLE_NAME = 'crm_tickets' THEN
                    -- У обращения ровно один адресат уведомлений — его автор:
                    -- это он ждёт ответа из группы и отметки «выполнено».
                    -- Остальные (СВ, глава отдела) видят раздел, но звонить им
                    -- по чужой переписке было бы шумом.
                    targets := ARRAY[NEW.created_by];
                    IF TG_OP = 'UPDATE' THEN
                        targets := targets || ARRAY[OLD.created_by];
                    END IF;
                    -- Исключение — обращение на проверке у супервайзера (задача
                    -- #297, «Сотрудничество с Яндексом»): это уже не чужая
                    -- переписка, а его задача. Проверяет любой супервайзер
                    -- отдела автора, а без них — глава отдела
                    -- (crm/queries.py::department_reviewer_sql). Будим всех
                    -- работающих СВ отдела и главу — и когда обращение встало
                    -- на проверку, и когда по нему решили: задача должна
                    -- погаснуть у всех проверяющих. Глава при живых СВ — круг
                    -- шире точного, лишний тычок стоит одной перечитки сводки.
                    IF NEW.review_state IS NOT NULL THEN
                        targets := targets || ARRAY(
                            SELECT u.id FROM users u
                             WHERE u.department_id = NEW.department_id
                               AND COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
                               AND lower(COALESCE(u.role, '')) IN ('sv', 'supervisor')
                        ) || ARRAY(
                            SELECT d.head_user_id FROM departments d
                             WHERE d.id = NEW.department_id
                               AND d.head_user_id IS NOT NULL);
                    END IF;
                ELSIF TG_TABLE_NAME = 'complaints' THEN
                    -- Жалоба будит двоих: автора (пришёл ответ для водителя или
                    -- вопрос группы) и того, кому поставлена работа с
                    -- сотрудником. При UPDATE — и прежнего ответственного:
                    -- сотрудника поправили, и задача должна погаснуть у старого.
                    targets := ARRAY[NEW.created_by, NEW.responsible_id];
                    -- Ответственного не нашлось (у отдела нет ни СВ, ни главы
                    -- у сотрудника) — задача у главы отдела сотрудника.
                    IF NEW.responsible_id IS NULL AND NEW.target_department_id IS NOT NULL THEN
                        targets := targets || ARRAY(
                            SELECT d.head_user_id FROM departments d
                             WHERE d.id = NEW.target_department_id
                               AND d.head_user_id IS NOT NULL);
                    END IF;
                    IF TG_OP = 'UPDATE' THEN
                        targets := targets || ARRAY[OLD.created_by, OLD.responsible_id];
                    END IF;
                    -- Жалоба на проверке (Яндекс): решает любой супервайзер
                    -- отдела автора, а без них — глава отдела
                    -- (crm/queries.py::department_reviewer_sql). Будим всех
                    -- работающих СВ отдела и главу — круг шире точного, лишний
                    -- тычок стоит одной перечитки сводки.
                    IF NEW.review_state IS NOT NULL THEN
                        targets := targets || ARRAY(
                            SELECT u.id FROM users u
                             WHERE u.department_id = NEW.creator_department_id
                               AND COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
                               AND lower(COALESCE(u.role, '')) IN ('sv', 'supervisor')
                        ) || ARRAY(
                            SELECT d.head_user_id FROM departments d
                             WHERE d.id = NEW.creator_department_id
                               AND d.head_user_id IS NOT NULL);
                    END IF;
                    -- Назначенный тренинг: в его день строка в колоколе у самого
                    -- сотрудника и у супервайзеров его отдела (источник
                    -- training_plans). При смене сотрудника назначение снимается —
                    -- будим и прежнего.
                    IF NEW.training_planned_at IS NOT NULL THEN
                        targets := targets || ARRAY[NEW.employee_id] || ARRAY(
                            SELECT u.id FROM users u
                             WHERE u.department_id = NEW.target_department_id
                               AND COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
                               AND lower(COALESCE(u.role, '')) IN ('sv', 'supervisor'));
                    END IF;
                    IF TG_OP = 'UPDATE' AND OLD.training_planned_at IS NOT NULL THEN
                        targets := targets || ARRAY[OLD.employee_id] || ARRAY(
                            SELECT u.id FROM users u
                             WHERE u.department_id = OLD.target_department_id
                               AND COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
                               AND lower(COALESCE(u.role, '')) IN ('sv', 'supervisor'));
                    END IF;
                ELSIF TG_TABLE_NAME = 'tasks' THEN
                    -- Исполнители и принимающий; при UPDATE — и прежние тоже:
                    -- переназначенная задача должна погаснуть у старого владельца.
                    -- Исполнителей может быть несколько, поэтому весь состав
                    -- берём из связи; скалярную колонку оставляем в списке на
                    -- случай, если строка состава почему-то не доехала.
                    targets := ARRAY[NEW.assigned_to,
                                     COALESCE(NEW.requested_by_id, NEW.created_by)]
                               || ARRAY(SELECT user_id FROM task_assignees WHERE task_id = NEW.id);
                    IF TG_OP = 'UPDATE' THEN
                        targets := targets || ARRAY[OLD.assigned_to,
                                                    COALESCE(OLD.requested_by_id, OLD.created_by)];
                    END IF;
                ELSIF TG_TABLE_NAME = 'operator_checkpoints' THEN
                    -- Контрольная точка: будим супервайзера, который её ведёт,
                    -- и самого сотрудника — но сотрудника ТОЛЬКО если ему
                    -- решили сообщать (notify_operator). Иначе тычок пришёл бы
                    -- человеку, которому в колоколе показывать нечего, и он
                    -- увидел бы пустое обновление вместо строки.
                    targets := ARRAY[NEW.supervisor_id, NEW.created_by];
                    IF NEW.notify_operator THEN
                        targets := targets || ARRAY[NEW.operator_id];
                    END IF;
                    IF TG_OP = 'UPDATE' THEN
                        targets := targets || ARRAY[OLD.supervisor_id];
                        IF OLD.notify_operator THEN
                            targets := targets || ARRAY[OLD.operator_id];
                        END IF;
                    END IF;
                ELSIF TG_TABLE_NAME = 'work_shift_change_requests' THEN
                    -- Кого будим по заявке на изменение смены. Список узкий
                    -- НАМЕРЕННО: разобрать заявку вправе любой управленец
                    -- отдела, но уведомлять всех подряд — значит превратить
                    -- колокол в ленту, на которую перестают смотреть.
                    --
                    --   автор            — ему приходит решение;
                    --   прямой СВ        — это его человек и его решение;
                    --   глава отдела     — только если сам подписался;
                    --   глава отдела     — ещё и когда у оператора СВ нет вовсе,
                    --                      иначе заявку не увидит никто.
                    --
                    -- Администраторы сюда не попадают: очередь с её счётчиком
                    -- никуда не девается, а звать их по каждой смене нечем.
                    targets := ARRAY[NEW.operator_id, NEW.supervisor_id];
                    targets := targets || ARRAY(
                        SELECT d.head_user_id
                        FROM departments d
                        LEFT JOIN shift_change_notify_optin o
                               ON o.user_id = d.head_user_id AND o.enabled
                        WHERE d.id = NEW.department_id
                          AND d.head_user_id IS NOT NULL
                          AND (o.user_id IS NOT NULL OR NEW.supervisor_id IS NULL)
                    );
                    IF TG_OP = 'UPDATE' THEN
                        targets := targets || ARRAY[OLD.supervisor_id];
                    END IF;
                ELSIF TG_TABLE_NAME = 'leave_requests' THEN
                    -- Заявка на отпуск, Б/С или больничный (#358, #346): оператор (ответ,
                    -- предложенные даты), СВ из снимка (его шаг) и глава отдела
                    -- (его шаг, утверждение). Глава будится при любой смене
                    -- статуса: круг шире точного на одну перечитку сводки, зато
                    -- без второй копии правила «чей сейчас ход» на PL/pgSQL.
                    targets := ARRAY[NEW.operator_id, NEW.supervisor_id] || ARRAY(
                        SELECT d.head_user_id FROM departments d
                         WHERE d.id = NEW.department_id AND d.head_user_id IS NOT NULL);
                    IF TG_OP = 'UPDATE' THEN
                        targets := targets || ARRAY[OLD.supervisor_id];
                    END IF;
                ELSIF TG_TABLE_NAME = 'wiki_operator_questions' THEN
                    -- Вопрос оператора, на который не ответил помощник
                    -- (wiki/questions.py). Смена статуса — дело отдела:
                    -- будим супервайзеров отдела вопроса, а главу — только
                    -- когда супервайзеров нет, иначе вопрос не увидел бы
                    -- никто (то же правило у notifications/sources.py:
                    -- wiki_questions). Автора — на ответ и закрытие.
                    -- Отметка «оператор открыл ответ» касается только его.
                    IF TG_OP = 'UPDATE'
                       AND OLD.status IS NOT DISTINCT FROM NEW.status THEN
                        targets := ARRAY[NEW.asker_id];
                    ELSE
                        targets := ARRAY(
                            SELECT u.id FROM users u
                             WHERE u.department_id = NEW.department_id
                               AND u.status = 'working'
                               AND lower(u.role) IN ('sv', 'supervisor')
                        );
                        IF array_length(targets, 1) IS NULL THEN
                            targets := ARRAY(
                                SELECT d.head_user_id FROM departments d
                                 WHERE d.id = NEW.department_id
                                   AND d.head_user_id IS NOT NULL
                            );
                        END IF;
                        IF TG_OP = 'UPDATE' THEN
                            targets := targets || ARRAY[NEW.asker_id];
                        END IF;
                    END IF;
                ELSIF TG_TABLE_NAME = 'payment_notifications' THEN
                    -- «Оплата счетов»: уведомление адресное, будим только
                    -- его получателя — и когда оно появилось, и когда погасло
                    -- (дело сделано, заявку открыли), чтобы бейдж обновился
                    -- во всех его вкладках.
                    targets := ARRAY[NEW.user_id];
                ELSIF TG_TABLE_NAME = 'discipline_notifications' THEN
                    -- «Дисциплина» (#254): так же адресно — строку пишет сам
                    -- процесс одному человеку, будим только его.
                    targets := ARRAY[NEW.user_id];
                ELSIF TG_TABLE_NAME = 'hiring_notifications' THEN
                    -- «Воронка найма» (#404): адресно, как у «Оплаты счетов» —
                    -- появление строки и её гашение (карточку открыли).
                    targets := ARRAY[NEW.user_id];
                ELSIF TG_TABLE_NAME = 'twofa_requests' THEN
                    -- «Коды 2FA» (#373): запрос ждёт тех, кто включил уведомления о
                    -- запросах (глава маркетинга, супер-админы — по тумблеру в разделе);
                    -- решение и сгорание гасят строку у них. Сотрудника будим тоже:
                    -- открытый у него раздел перечитает статус своего запроса.
                    targets := ARRAY[NEW.requester_id] || ARRAY(
                        SELECT o.user_id FROM twofa_notify_optin o);
                ELSIF TG_TABLE_NAME = 'twofa_notify_optin' THEN
                    -- Тумблер включили — колокол сразу показывает ждущие запросы,
                    -- выключили — гасит их. Строка есть только у включённого.
                    IF TG_OP = 'DELETE' THEN
                        targets := ARRAY[OLD.user_id];
                    ELSE
                        targets := ARRAY[NEW.user_id];
                    END IF;
                ELSIF TG_TABLE_NAME = 'task_assignees' THEN
                    -- Состав поменялся: будим и добавленного, и снятого. Здесь
                    -- обязательно COALESCE(NEW, OLD) — при DELETE есть только OLD,
                    -- и обращение к NEW.user_id уронило бы функцию (а её
                    -- EXCEPTION-обёртка сделала бы это молча).
                    IF TG_OP = 'DELETE' THEN
                        targets := ARRAY[OLD.user_id];
                    ELSE
                        targets := ARRAY[NEW.user_id];
                    END IF;
                END IF;
                targets := ARRAY(SELECT DISTINCT t FROM unnest(targets) AS t WHERE t IS NOT NULL);
                IF array_length(targets, 1) IS NOT NULL THEN
                    PERFORM pg_notify('bell_events', json_build_object('u', targets)::text);
                END IF;
                RETURN NULL;
            EXCEPTION WHEN OTHERS THEN
                -- Уведомление никогда не важнее записи: сломанный тычок — это
                -- максимум задержка колокола до обновления по фокусу, а упавший
                -- здесь триггер откатил бы само действие пользователя.
                RETURN NULL;
            END;
            $$;
        """)
        # UPDATE у назначений опросов/ознакомлений и отметок задач нужен, чтобы
        # действие пользователя (прошёл, подтвердил, просмотрел) мгновенно гасило
        # уведомление в других его вкладках, а не только добавляло новые.
        for trigger_name, table_name, timing, when_clause in (
            ('trg_bell_events', 'events', 'AFTER INSERT', ''),
            ('trg_bell_four_you', 'four_you_images', 'AFTER INSERT', ''),
            ('trg_bell_lms', 'lms_notifications', 'AFTER INSERT OR UPDATE', ''),
            ('trg_bell_surveys_insert', 'survey_assignments', 'AFTER INSERT', ''),
            (
                'trg_bell_surveys_archived',
                'surveys',
                'AFTER UPDATE OF archived_at',
                'WHEN (OLD.archived_at IS DISTINCT FROM NEW.archived_at)',
            ),
            (
                'trg_bell_surveys',
                'survey_assignments',
                'AFTER UPDATE OF operator_id, survey_id, status',
                """WHEN (
                    OLD.operator_id IS DISTINCT FROM NEW.operator_id
                    OR OLD.survey_id IS DISTINCT FROM NEW.survey_id
                    OR (COALESCE(OLD.status, '') = 'completed') IS DISTINCT FROM
                       (COALESCE(NEW.status, '') = 'completed')
                )""",
            ),
            ('trg_bell_wiki_ack_insert', 'wiki_ack_assignments', 'AFTER INSERT', ''),
            (
                'trg_bell_wiki_ack',
                'wiki_ack_assignments',
                'AFTER UPDATE OF user_id, article_id, due_at, acknowledged_at, status',
                """WHEN (
                    OLD.user_id IS DISTINCT FROM NEW.user_id
                    OR OLD.article_id IS DISTINCT FROM NEW.article_id
                    OR OLD.due_at IS DISTINCT FROM NEW.due_at
                    OR (OLD.acknowledged_at IS NULL) IS DISTINCT FROM
                       (NEW.acknowledged_at IS NULL)
                    OR COALESCE(OLD.status IN ('superseded', 'cancelled'), FALSE)
                       IS DISTINCT FROM
                       COALESCE(NEW.status IN ('superseded', 'cancelled'), FALSE)
                )""",
            ),
            # Обращения. INSERT не будим намеренно: в момент создания автору
            # ещё нечего читать — он сам его и завёл. А вот WHEN обязателен,
            # иначе каждое исходящее сообщение в нить (last_message_at) слало
            # бы тычок, не меняя ничего в сводке. review_state — решение
            # супервайзера по обращению на проверке: оно гасит задачу у всех
            # проверяющих.
            (
                'trg_bell_crm_tickets',
                'crm_tickets',
                'AFTER UPDATE OF author_unread_at, status, delivery_status, review_state',
                """WHEN (
                    OLD.author_unread_at IS DISTINCT FROM NEW.author_unread_at
                    OR OLD.status IS DISTINCT FROM NEW.status
                    OR OLD.delivery_status IS DISTINCT FROM NEW.delivery_status
                    OR OLD.review_state IS DISTINCT FROM NEW.review_state
                )""",
            ),
            # Единственная вставка, о которой колоколу есть что сказать, —
            # обращение, вставшее на проверку: у супервайзера появилась задача.
            # Отдельным триггером: WHEN у INSERT не может ссылаться на OLD.
            (
                'trg_bell_crm_tickets_review', 'crm_tickets', 'AFTER INSERT',
                """WHEN (NEW.review_state IS NOT NULL)""",
            ),
            # Жалобы (задача #297). INSERT будит ответственного сразу: сотрудника
            # мог определить уже оператор, и задача «проведите ОС» появляется в
            # момент создания жалобы. UPDATE — только по колонкам, которые
            # меняют сводку колокола, иначе каждый реплай группы (last_activity_at)
            # слал бы тычок ни за что. Два триггера, а не один: WHEN у
            # INSERT-триггера не может ссылаться на OLD.
            ('trg_bell_complaints_insert', 'complaints', 'AFTER INSERT', ''),
            # review_state — проверка жалобы на Яндекс супервайзером (решение
            # гасит задачу у всех проверяющих); training_planned_at и
            # training_required — назначенный тренинг и его снятие (строка
            # «тренинг сегодня» у сотрудника и СВ его отдела).
            (
                'trg_bell_complaints',
                'complaints',
                'AFTER UPDATE OF author_unread_at, responsible_id, work_state, status, '
                'review_state, training_planned_at, training_required',
                """WHEN (
                    OLD.author_unread_at IS DISTINCT FROM NEW.author_unread_at
                    OR OLD.responsible_id IS DISTINCT FROM NEW.responsible_id
                    OR OLD.work_state IS DISTINCT FROM NEW.work_state
                    OR OLD.status IS DISTINCT FROM NEW.status
                    OR OLD.review_state IS DISTINCT FROM NEW.review_state
                    OR OLD.training_planned_at IS DISTINCT FROM NEW.training_planned_at
                    OR OLD.training_required IS DISTINCT FROM NEW.training_required
                )""",
            ),
            ('trg_bell_tasks', 'tasks', 'AFTER INSERT OR UPDATE', ''),
            # «Оплата счетов» (ТЗ «Закуп и оплата», п. 17). UPDATE — только
            # погашение: других правок у строки уведомления не бывает.
            ('trg_bell_payment_notifications_insert', 'payment_notifications', 'AFTER INSERT', ''),
            (
                'trg_bell_payment_notifications',
                'payment_notifications',
                'AFTER UPDATE OF read_at',
                'WHEN (OLD.read_at IS DISTINCT FROM NEW.read_at)',
            ),
            # «Воронка найма» (#404): появление уведомления и его погашение (карточку открыли).
            ('trg_bell_hiring_notifications_insert', 'hiring_notifications', 'AFTER INSERT', ''),
            (
                'trg_bell_hiring_notifications',
                'hiring_notifications',
                'AFTER UPDATE OF read_at',
                'WHEN (OLD.read_at IS DISTINCT FROM NEW.read_at)',
            ),
            # «Дисциплина» (#254): появление уведомления и его погашение.
            ('trg_bell_discipline_notifications_insert', 'discipline_notifications', 'AFTER INSERT', ''),
            (
                'trg_bell_discipline_notifications',
                'discipline_notifications',
                'AFTER UPDATE OF read_at',
                'WHEN (OLD.read_at IS DISTINCT FROM NEW.read_at)',
            ),
            # «Коды 2FA» (#373): новый запрос, смена его статуса (решение, сгорание, отмена) и
            # итог отправки кода. Последний колокол главы не меняет, но открытый раздел у
            # сотрудника и главы по этому тычку перечитывается: отметка «код не дошёл» пишется
            # ПОСЛЕ смены статуса, и без неё экран навсегда остался бы на «Код отправлен».
            # Отметку выдачи (last_issued_at) WHEN отсекает — экрану она ничего не говорит.
            ('trg_bell_twofa_requests_insert', 'twofa_requests', 'AFTER INSERT', ''),
            ('trg_bell_twofa_notify_optin', 'twofa_notify_optin', 'AFTER INSERT OR DELETE', ''),
            (
                'trg_bell_twofa_requests',
                'twofa_requests',
                'AFTER UPDATE OF status, delivery_error, codes_sent',
                """WHEN (
                    OLD.status IS DISTINCT FROM NEW.status
                    OR OLD.delivery_error IS DISTINCT FROM NEW.delivery_error
                    OR OLD.codes_sent IS DISTINCT FROM NEW.codes_sent
                )""",
            ),
            # Смена состава исполнителей саму задачу не трогает (UPDATE tasks не
            # происходит), поэтому trg_bell_tasks об этом не узнает — нужен свой
            # триггер, иначе добавленный соисполнитель не увидит задачу в колоколе
            # до следующего обновления по фокусу.
            ('trg_bell_task_assignees', 'task_assignees', 'AFTER INSERT OR DELETE', ''),
            # Контрольные точки. Двумя триггерами, а не одним на INSERT OR
            # UPDATE: WHEN у такого триггера НЕ может ссылаться на OLD
            # («INSERT trigger's WHEN condition cannot reference OLD values»),
            # и TG_OP в WHEN тоже недоступен — там разрешены только NEW/OLD.
            # Тот же приём, что у ознакомлений и назначений опросов.
            ('trg_bell_checkpoints_insert', 'operator_checkpoints', 'AFTER INSERT', ''),
            # UPDATE обязателен: точку закрывают («проверено») и переносят срок,
            # и оба действия обязаны гасить или двигать строку в колоколе. WHEN
            # отсекает правки, которых сводка не видит, — иначе каждое повторное
            # сохранение ОС слало бы тычок обоим ни за что.
            (
                'trg_bell_checkpoints',
                'operator_checkpoints',
                'AFTER UPDATE OF status, due_date, operator_id, supervisor_id, '
                'notify_operator, focus',
                """WHEN (
                    OLD.status IS DISTINCT FROM NEW.status
                    OR OLD.due_date IS DISTINCT FROM NEW.due_date
                    OR OLD.operator_id IS DISTINCT FROM NEW.operator_id
                    OR OLD.supervisor_id IS DISTINCT FROM NEW.supervisor_id
                    OR OLD.notify_operator IS DISTINCT FROM NEW.notify_operator
                    OR OLD.focus IS DISTINCT FROM NEW.focus
                )""",
            ),
            ('trg_bell_task_reads', 'task_action_reads', 'AFTER INSERT OR UPDATE', ''),
            ('trg_bell_event_reads', 'event_reads', 'AFTER INSERT OR UPDATE', ''),
            ('trg_bell_four_you_reads', 'four_you_reads', 'AFTER INSERT OR UPDATE', ''),
            # «Доска почёта» (#318): колокол зовёт всех, когда доска месяца
            # вышла ВПЕРВЫЕ, и убирает строку, когда её сняли с публикации.
            # WHEN обязателен: окно тренера сохраняет черновик само, по ходу
            # ввода (draft_entries, revision), и без него каждое нажатие в окне
            # будило бы весь портал. «Опубликовать правки» (published_at
            # меняется, но доска остаётся выложенной) тоже мимо.
            (
                'trg_bell_honor_boards',
                'honor_boards',
                'AFTER UPDATE OF first_published_at, published_at',
                """WHEN (
                    OLD.first_published_at IS DISTINCT FROM NEW.first_published_at
                    OR (OLD.published_at IS NULL) IS DISTINCT FROM (NEW.published_at IS NULL)
                )""",
            ),
            ('trg_bell_honor_board_reads', 'honor_board_reads', 'AFTER INSERT OR UPDATE', ''),
            # UPDATE обязателен: отметка «сегодняшних именинников видел»
            # перезаписывается по дате, а не вставляется заново, — на одном
            # INSERT тычок ушёл бы только в первый день жизни строки.
            ('trg_bell_birthday_reads', 'birthday_reads', 'AFTER INSERT OR UPDATE', ''),
            # Новость дня: выпуск (INSERT сразу опубликованной) и переход
            # черновика в опубликованные. WHEN обязателен — правку заголовка и
            # текста черновика видит только автор, и будить ею весь портал
            # значило бы разослать широковещательный тычок на каждое нажатие в
            # форме (та же ловушка, что у автосейва черновика теста).
            (
                'trg_bell_news_insert', 'news_posts', 'AFTER INSERT',
                """WHEN (NEW.status = 'published')""",
            ),
            # UPDATE OF перечисляет ДВЕ колонки, и вторая не для красоты:
            # правка круга адресатов у уже выпущенной новости всегда переписывает
            # audience_max_role_level (news/queries.py: set_audience), и без неё
            # человек, которого только что дописали в адресаты, увидел бы окно
            # лишь при следующем заходе. WHEN отсекает правку ЧЕРНОВИКА: у него
            # NEW.status = 'draft', и будить весь портал на каждое сохранение
            # формы было бы ровно той ловушкой, от которой лечили автосейв
            # черновика теста.
            (
                'trg_bell_news', 'news_posts',
                'AFTER UPDATE OF status, audience_max_role_level',
                """WHEN (OLD.status IS DISTINCT FROM NEW.status
                        OR NEW.status = 'published')""",
            ),
            # Вопросы операторов (задача #321): вопрос пришёл отделу, на него
            # ответили или его закрыли, оператор открыл ответ. WHEN на UPDATE
            # обязателен: оформление разобранного вопроса в статью и новость
            # (kb_*) сводку колокола не меняет, и будить им отдел незачем.
            ('trg_bell_wiki_questions_insert', 'wiki_operator_questions',
             'AFTER INSERT', ''),
            (
                'trg_bell_wiki_questions',
                'wiki_operator_questions',
                'AFTER UPDATE OF status, asker_seen_at',
                """WHEN (
                    OLD.status IS DISTINCT FROM NEW.status
                    OR OLD.asker_seen_at IS DISTINCT FROM NEW.asker_seen_at
                )""",
            ),
            # Заявка на изменение смены: подача (INSERT) и решение/отзыв/отметка
            # прочтения (UPDATE). WHEN на UPDATE обязателен — updated_at трогает
            # каждая правка, а колокол интересует только смена статуса и гашение.
            ('trg_bell_shift_change_requests_insert', 'work_shift_change_requests',
             'AFTER INSERT', ''),
            (
                'trg_bell_shift_change_requests',
                'work_shift_change_requests',
                'AFTER UPDATE OF status, operator_seen_at, supervisor_id',
                """WHEN (
                    OLD.status IS DISTINCT FROM NEW.status
                    OR OLD.operator_seen_at IS DISTINCT FROM NEW.operator_seen_at
                    OR OLD.supervisor_id IS DISTINCT FROM NEW.supervisor_id
                )""",
            ),
            # Заявка на отпуск или Б/С (#358): подача и смена шага / отметка
            # прочтения. Правка дат без смены шага колокол не меняет — WHEN
            # отсекает её, как у заявок по сменам.
            ('trg_bell_leave_requests_insert', 'leave_requests', 'AFTER INSERT', ''),
            (
                'trg_bell_leave_requests',
                'leave_requests',
                'AFTER UPDATE OF status, operator_seen_at, supervisor_id',
                """WHEN (
                    OLD.status IS DISTINCT FROM NEW.status
                    OR OLD.operator_seen_at IS DISTINCT FROM NEW.operator_seen_at
                    OR OLD.supervisor_id IS DISTINCT FROM NEW.supervisor_id
                )""",
            ),
        ):
            # Таблицы разделов создаются выше в этой же транзакции, каждая под
            # своим SAVEPOINT. Если схема раздела не применилась, его таблицы
            # нет — и CREATE TRIGGER на ней уронил бы САВПОИНТ КОЛОКОЛА целиком,
            # то есть один сломанный раздел лишил бы реалтайма все остальные.
            cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (f'public.{table_name}',))
            if not cursor.fetchone()[0]:
                logging.warning(
                    "Триггер колокола %s пропущен: таблицы %s нет", trigger_name, table_name,
                )
                continue
            cursor.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name};")
            cursor.execute(f"""
                CREATE TRIGGER {trigger_name}
                {timing} ON {table_name}
                FOR EACH ROW
                {when_clause}
                EXECUTE FUNCTION bell_notify_change();
            """)

    def _init_amo_leads_schema_tx(self, cursor):
        """Схема выгрузки сделок amoCRM (раздел «Лиды по источникам»).

        Раньше это жило на вкладке «Импорт ДЕНЬ» Google-таблицы, куда Apps Script
        раз в сутки перезаливал сделки. Здесь те же поля, но сделка хранится по
        своему id: выгрузка идемпотентна и не оставляет лист пустым в момент
        обновления, как это делал `sheet.clear()`.
        """
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS amo_leads (
                lead_id       BIGINT PRIMARY KEY,
                name          TEXT,
                created_at    TIMESTAMPTZ NOT NULL,
                created_date  DATE NOT NULL,
                stage         TEXT,
                responsible   TEXT,
                tags          TEXT,
                utm_source    TEXT,
                utm_medium    TEXT,
                utm_campaign  TEXT,
                utm_term      TEXT,
                registered    SMALLINT NOT NULL DEFAULT 0,
                trip_done     SMALLINT NOT NULL DEFAULT 0,
                robot_closed  SMALLINT NOT NULL DEFAULT 0,
                synced_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_amo_leads_created_date
                ON amo_leads(created_date);

            -- Журнал выгрузок. Нужен, чтобы отбивка могла показать возраст цифр:
            -- в Apps Script ошибка API молча оставляла на листе прошлые данные.
            CREATE TABLE IF NOT EXISTS amo_lead_syncs (
                id            BIGSERIAL PRIMARY KEY,
                started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at   TIMESTAMPTZ,
                status        TEXT NOT NULL DEFAULT 'running',
                leads_fetched INTEGER,
                days          INTEGER,
                error         TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_amo_lead_syncs_started
                ON amo_lead_syncs(started_at DESC);

            -- Подписки на отбивку. Чат подписывает себя сам командой, поэтому
            -- отдельного реестра чатов и раздела в интерфейсе не нужно.
            CREATE TABLE IF NOT EXISTS amo_lead_subscriptions (
                chat_id       TEXT PRIMARY KEY,
                title         TEXT,
                chat_type     TEXT,
                subscribed_by BIGINT,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_sent_at  TIMESTAMPTZ
            );
        """)

    def _init_chat_hourly_schema_tx(self, cursor):
        """Подписки на почасовой отчёт по чатам Chat2Desk.

        Отдельно от подписок на лиды: расписание, права и содержимое у отчётов разные,
        а один общий реестр заставил бы держать колонку «на что подписан» и фильтровать
        по ней в каждом запросе."""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_hourly_subscriptions (
                chat_id       TEXT PRIMARY KEY,
                title         TEXT,
                chat_type     TEXT,
                subscribed_by BIGINT,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_sent_at  TIMESTAMPTZ
            );
        """)

    def _init_front_office_calls_schema_tx(self, cursor):
        """Обзвон фронт-офиса: дневной план и подписки на утреннюю отбивку.

        Порядок строго CREATE TABLE → CREATE INDEX: на боевой базе иначе
        падает вся инициализация разом (см. историю с crm_queues).

        План хранится строкой на отдел, а не константой в коде: норму задаёт
        глава отдела командой в боте, и менять её не должен деплой. Ключ —
        departments.id, чтобы та же механика досталась второму отделу без
        миграции схемы."""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS front_office_call_plans (
                department_id INTEGER PRIMARY KEY
                    REFERENCES departments(id) ON DELETE CASCADE,
                calls_per_day INTEGER NOT NULL CHECK (calls_per_day > 0),
                updated_by    INTEGER,
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            -- Подписки: чат подписывает себя сам командой, как у лидов и
            -- почасового отчёта по чатам. Отдельный реестр не нужен.
            CREATE TABLE IF NOT EXISTS front_office_call_subscriptions (
                chat_id       TEXT PRIMARY KEY,
                title         TEXT,
                chat_type     TEXT,
                subscribed_by BIGINT,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_sent_at  TIMESTAMPTZ
            );
        """)

    def _init_wiki_schema_tx(self, cursor):
        """Схема раздела «Вики» (таблицы wiki_*).

        Сам DDL живёт в пакете wiki/schema.py, а не здесь: раздел большой
        (20 таблиц), и держать его в database.py, где уже 48k строк, незачем.
        Импорт локальный — на уровне модуля он создал бы цикл, потому что
        database импортируется отовсюду.

        SAVEPOINT нужен вот почему: весь _init_db идёт ОДНОЙ транзакцией, и
        падение здесь уронило бы инициализацию всей базы — приложение просто
        не поднялось бы из-за раздела с базой знаний. С савпоинтом отказ
        локален: остальные подсистемы стартуют штатно, а раздел честно
        сообщает о себе через /api/wiki/ping (schema_ready=false), и в
        интерфейсе видно «раздел разворачивается».

        Это сознательно не «тихий except»: в оригинальной вике вся инициализация
        была обёрнута одним try/catch с console.error, из-за чего на чистой базе
        половина таблиц молча не создавалась и никто об этом не знал.
        """
        import logging

        cursor.execute("SAVEPOINT wiki_schema")
        try:
            from wiki.schema import init_wiki_schema
            init_wiki_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT wiki_schema")
            logging.exception(
                "Схема раздела «Вики» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT wiki_schema")

    def _init_news_schema_tx(self, cursor):
        """Схема раздела «Новости» — своим SAVEPOINT'ом, как у вики.

        Стоит ПОСЛЕ вики намеренно: раздел заимствует у неё лестницу выдачи
        (news/access.py), и порядок разворачивания читается так же, как порядок
        зависимости. Общих таблиц у них нет — новости живут отдельно, потому
        что читать их обязан и тот, кому вики не выдана.
        """
        cursor.execute("SAVEPOINT news_schema")
        try:
            from news.schema import init_news_schema
            init_news_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT news_schema")
            logging.exception(
                "Схема раздела «Новости» не применилась — окно новости "
                "показываться не будет, остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT news_schema")

        # Пространство ничьим новостям — ОТДЕЛЬНЫМ SAVEPOINT'ом, а не внутри
        # схемы: он читает таблицу ВИКИ (wiki_space_departments), и сбой на
        # чужой стороне не должен уносить с собой развёрнутые таблицы раздела.
        cursor.execute("SAVEPOINT news_spaces_backfill")
        try:
            from news.schema import backfill_space_ids
            filled = backfill_space_ids(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT news_spaces_backfill")
            logging.error("Пространства новостям не проставлены: %s", exc, exc_info=True)
        else:
            cursor.execute("RELEASE SAVEPOINT news_spaces_backfill")
            if filled:
                logging.info("Новости: пространство проставлено %s объявлениям", filled)

    def _init_crm_schema_tx(self, cursor):
        """Схема раздела «Обращения» (таблицы crm_*).

        DDL живёт в пакете crm/schema.py по той же причине, что и у вики:
        держать пять таблиц раздела в файле на 50k строк незачем, а импорт
        локальный — на уровне модуля он создал бы цикл.

        SAVEPOINT — потому что весь _init_db идёт одной транзакцией: падение
        здесь не должно ронять инициализацию всей базы. При отказе раздел
        честно сообщает о себе через /api/crm/ping (schema_ready=false), а
        остальное приложение стартует штатно.
        """
        import logging

        cursor.execute("SAVEPOINT crm_schema")
        try:
            from crm.schema import init_crm_schema
            init_crm_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT crm_schema")
            logging.exception(
                "Схема раздела «Обращения» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT crm_schema")

    def _init_complaints_schema_tx(self, cursor):
        """Схема раздела «Жалобы» (таблицы complaint*, задача #297).

        DDL — в пакете complaints/schema.py, импорт локальный (цикл). Вызывается
        ПОСЛЕ базового DDL: журнал работы с сотрудником ссылается на trainings,
        а жалоба — на departments и users. Под SAVEPOINT по той же причине, что
        у «Обращений»: сломанная миграция раздела не должна ронять инициализацию
        базы — раздел тогда честно скажет о себе через /api/complaints/ping.
        """
        import logging

        cursor.execute("SAVEPOINT complaints_schema")
        try:
            from complaints.schema import init_complaints_schema
            init_complaints_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT complaints_schema")
            logging.exception(
                "Схема раздела «Жалобы» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT complaints_schema")

    def _init_discipline_schema_tx(self, cursor):
        """Схема раздела «Дисциплина» (таблицы discipline_*, задача #254).

        DDL — в пакете discipline/schema.py, импорт локальный (цикл). Вызывается
        ПОСЛЕ базового DDL: запись ссылается на trainings, groups, departments и
        users. Под SAVEPOINT, как у «Жалоб»: сломанная миграция раздела не должна
        ронять инициализацию базы — раздел тогда честно скажет о себе 503.
        """
        import logging

        cursor.execute("SAVEPOINT discipline_schema")
        try:
            from discipline.schema import init_discipline_schema
            init_discipline_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT discipline_schema")
            logging.exception(
                "Схема раздела «Дисциплина» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT discipline_schema")

    def _init_leave_requests_schema_tx(self, cursor):
        """Заявки на отпуск и Б/С (таблицы leave_*, задача #358).

        DDL — в пакете leave_requests/schema.py, импорт локальный (цикл). Под
        SAVEPOINT, как у «Дисциплины»: сломанная миграция раздела не роняет
        инициализацию, а API заявок честно отвечает 503.
        """
        import logging

        cursor.execute("SAVEPOINT leave_requests_schema")
        try:
            from leave_requests.schema import init_leave_requests_schema
            init_leave_requests_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT leave_requests_schema")
            logging.exception(
                "Схема заявок на отпуск не применилась — заявки будут недоступны, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT leave_requests_schema")

    def _init_payments_schema_tx(self, cursor):
        """Схема раздела «Оплата счетов» (задача #179): заявки, шаги маршрута,
        справочники, договоры, Приказы, календарь фиксированных платежей.

        DDL живёт в пакете payments/schema.py, импорт локальный (цикл). Под
        SAVEPOINT по той же причине, что у посылок: падение здесь не должно
        ронять инициализацию всей базы — раздел тогда честно скажет о себе
        через /api/payments/ping (schema_ready=false).
        """
        import logging

        cursor.execute("SAVEPOINT payments_schema")
        try:
            from payments.schema import init_payments_schema
            init_payments_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT payments_schema")
            logging.exception(
                "Схема раздела «Оплата счетов» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT payments_schema")

    def _init_parcels_schema_tx(self, cursor):
        """Схема раздела «Посылки» (таблицы parcels/parcel_events).

        DDL живёт в пакете parcels/schema.py по той же причине, что у вики и
        обращений; импорт локальный — на уровне модуля он создал бы цикл.

        Стоит ПОСЛЕ _init_wiki_schema_tx намеренно: `parcels.office_id` — внешний
        ключ на `wiki_offices`, и на чистой базе в обратном порядке разворот
        раздела упал бы на несуществующей таблице.

        SAVEPOINT — потому что весь _init_db идёт одной транзакцией: падение
        здесь не должно ронять инициализацию всей базы. При отказе раздел честно
        сообщает о себе через /api/parcels/ping (schema_ready=false), а остальное
        приложение стартует штатно.
        """
        import logging

        cursor.execute("SAVEPOINT parcels_schema")
        try:
            from parcels.schema import init_parcels_schema
            init_parcels_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT parcels_schema")
            logging.exception(
                "Схема раздела «Посылки» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT parcels_schema")

    def _init_driver_chats_schema_tx(self, cursor):
        """Схема раздела «Чаты водителей» (таблицы dch_events/dch_message_cache).

        DDL живёт в пакете driver_chats/schema.py по той же причине, что у вики,
        обращений и посылок; импорт локальный — на уровне модуля он создал бы
        цикл.

        Порядок относительно других разделов безразличен: внешний ключ у раздела
        один, на `users`, а та создаётся в самом начале _init_db.

        SAVEPOINT — потому что весь _init_db идёт одной транзакцией: падение
        здесь не должно ронять инициализацию всей базы. При отказе раздел просто
        не откроется, а остальное приложение стартует штатно.
        """
        import logging

        cursor.execute("SAVEPOINT driver_chats_schema")
        try:
            from driver_chats.schema import init_driver_chats_schema
            init_driver_chats_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT driver_chats_schema")
            logging.exception(
                "Схема раздела «Чаты водителей» не применилась — раздел будет "
                "недоступен, остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT driver_chats_schema")

    def _init_sign_links_schema_tx(self, cursor):
        """Схема раздела «Ссылка на подписание» (таблица sign_link_requests).

        DDL живёт в пакете sign_links/schema.py по той же причине, что у вики,
        обращений, посылок и чатов водителей; импорт локальный — на уровне
        модуля он создал бы цикл.

        Порядок относительно других разделов безразличен: внешний ключ у
        раздела один, на `users`, а та создаётся в самом начале _init_db.

        SAVEPOINT — потому что весь _init_db идёт одной транзакцией: падение
        здесь не должно ронять инициализацию всей базы. При отказе раздел
        честно сообщает о себе через /api/sign_links/ping (schema_ready=false),
        а остальное приложение стартует штатно.
        """
        import logging

        cursor.execute("SAVEPOINT sign_links_schema")
        try:
            from sign_links.schema import init_sign_links_schema
            init_sign_links_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT sign_links_schema")
            logging.exception(
                "Схема раздела «Ссылка на подписание» не применилась — раздел будет "
                "недоступен, остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT sign_links_schema")

    def _init_partner_keys_schema_tx(self, cursor):
        """Схема ключей партнёров (partner_keys/schema.py): ключи с областями
        доступа и журнал вызовов; ими открыты ручка знаний, внешний вход
        «Ссылок на подписание» и окно «Чатов ОП» для CRM.

        Внешние ключи — только на users и departments, поэтому место в
        порядке безразлично. Под своим савпоинтом, как «Ссылка на подписание»:
        при отказе внешние входы по ключу отвечают 503, остальное приложение
        стартует.
        """
        import logging

        cursor.execute("SAVEPOINT partner_keys_schema")
        try:
            from partner_keys.schema import init_partner_keys_schema
            init_partner_keys_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT partner_keys_schema")
            logging.exception(
                "Схема ключей партнёров не применилась — входы по ключу будут "
                "недоступны, остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT partner_keys_schema")

    def _init_calls_api_schema_tx(self, cursor):
        """Схема «Звонков Binotel» для партнёров (calls_api/schema.py): копия звонков
        фронт-офиса для CRM и состояние сверки. Внешние ключи — на users и departments.
        Под своим савпоинтом: при отказе ручки /api/calls отвечают 503, вебхук Binotel и
        остальное приложение работают штатно."""
        import logging

        cursor.execute("SAVEPOINT calls_api_schema")
        try:
            from calls_api.schema import init_calls_api_schema
            init_calls_api_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT calls_api_schema")
            logging.exception("Схема «Звонков Binotel» не применилась — ручки /api/calls недоступны")
        else:
            cursor.execute("RELEASE SAVEPOINT calls_api_schema")

    def _init_olx_amo_schema_tx(self, cursor):
        """Схема робота «Лиды OLX» (таблицы olx_accounts/threads/journal/poll_runs).

        DDL живёт в пакете olx_amo/schema.py по той же причине, что у вики,
        обращений и посылок; импорт локальный — на уровне модуля он создал бы
        цикл.

        SAVEPOINT — потому что весь _init_db идёт одной транзакцией: падение
        здесь не должно ронять инициализацию всей базы. При отказе раздел честно
        сообщает о себе через /api/olx_amo/ping (schema_ready=false), а робот
        просто не начнёт опрос — обращения останутся непрочитанными в кабинетах,
        что заметно и обратимо, в отличие от полупрочитанных без журнала.
        """
        import logging

        cursor.execute("SAVEPOINT olx_amo_schema")
        try:
            from olx_amo.schema import init_olx_amo_schema
            init_olx_amo_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT olx_amo_schema")
            logging.exception(
                "Схема раздела «Лиды OLX» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT olx_amo_schema")

    def _init_olx_ads_schema_tx(self, cursor):
        """Схема раздела «Объявления OLX» (таблицы olx_ads_*).

        Отдельно от «Лидов OLX» намеренно: разделы делят кабинеты и токены, но не
        данные. Падение схемы объявлений не должно закрывать журнал лидов, а
        падение схемы лидов — управление объявлениями.

        SAVEPOINT — по той же причине, что у соседей: весь _init_db идёт одной
        транзакцией, и падение здесь не должно ронять инициализацию всей базы.
        При отказе раздел честно сообщает о себе через /api/olx_ads/ping
        (schema_ready=false) и ничего не пишет в OLX.
        """
        import logging

        cursor.execute("SAVEPOINT olx_ads_schema")
        try:
            from olx_ads.schema import init_olx_ads_schema
            init_olx_ads_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT olx_ads_schema")
            logging.exception(
                "Схема раздела «Объявления OLX» не применилась — раздел будет "
                "недоступен, остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT olx_ads_schema")

    def _init_trainer_schema_tx(self, cursor):
        """Схема раздела «Тренажёр» (таблицы trainer_*).

        SAVEPOINT по той же причине, что у соседей: весь _init_db идёт одной
        транзакцией, и падение тестового раздела не должно ронять инициализацию
        базы. При отказе раздел честно скажет о себе через /api/trainer/ping
        (schema_ready=false), а остальное приложение стартует штатно.
        """
        import logging

        cursor.execute("SAVEPOINT trainer_schema")
        try:
            from voice_trainer.schema import init_trainer_schema
            init_trainer_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT trainer_schema")
            logging.exception(
                "Схема раздела «Тренажёр» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT trainer_schema")

    def _init_oktell_guard_schema_tx(self, cursor):
        """Схема раздела «Ограничитель Перезвона» (таблицы oktell_guard_*).

        SAVEPOINT — потому что весь _init_db идёт одной транзакцией: падение
        здесь не должно ронять инициализацию базы. Порядок инструкций внутри
        (CREATE TABLE → ALTER → CREATE INDEX) закреплён тестом: 17.08.2026
        обратный порядок в другом разделе положил прод, а на чистой базе такая
        ошибка не воспроизводится.
        """
        import logging

        cursor.execute("SAVEPOINT oktell_guard_schema")
        try:
            from oktell_guard.schema import init_oktell_guard_schema
            init_oktell_guard_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT oktell_guard_schema")
            logging.exception(
                "Схема раздела «Ограничитель Перезвона» не применилась — раздел будет "
                "недоступен, остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT oktell_guard_schema")

    def _init_fleet_edm_schema_tx(self, cursor):
        """Схема раздела «Провайдер ЭДО» (таблицы fleet_edm_*).

        Под SAVEPOINT по той же причине, что и соседи: весь _init_db идёт одной
        транзакцией, и упавший раздел не имеет права уронить старт приложения.
        """
        import logging

        cursor.execute("SAVEPOINT fleet_edm_schema")
        try:
            from fleet_edm.schema import init_fleet_edm_schema
            init_fleet_edm_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT fleet_edm_schema")
            logging.exception(
                "Схема раздела «Провайдер ЭДО» не применилась — раздел будет "
                "недоступен, остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT fleet_edm_schema")

    def _init_driver_mailings_schema_tx(self, cursor):
        """Схема раздела «Рассылки» (таблицы driver_mailing*).

        Стоит ПОСЛЕ «Провайдера ЭДО» намеренно: сессию кабинета (fleet_edm_session)
        заводит тот раздел, а «Рассылки» её только читают. Своей таблицы под куки
        здесь нет и быть не должно — учётная запись кабинета одна, и второе
        хранилище тех же кук означало бы две даты обновления и молчаливые 401 в
        половине портала.
        """
        import logging

        cursor.execute("SAVEPOINT driver_mailings_schema")
        try:
            from driver_mailings.schema import init_driver_mailings_schema
            init_driver_mailings_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT driver_mailings_schema")
            logging.exception(
                "Схема раздела «Рассылки» не применилась — раздел будет "
                "недоступен, остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT driver_mailings_schema")

    def _init_cdr_schema_tx(self, cursor):
        """Схема раздела «Касания» (cdr_touches, cdr_sync_days, cdr_operators,
        cdr_agent_state).

        Под SAVEPOINT по той же причине, что и соседи: весь _init_db идёт одной
        транзакцией, и упавший раздел не имеет права уронить старт приложения.
        При отказе роуты раздела отвечают понятным 503 (schema_is_ready), а не
        падают пятисоткой из-под первого SELECT.
        """
        import logging

        cursor.execute("SAVEPOINT cdr_schema")
        try:
            from cdr.schema import init_cdr_schema
            init_cdr_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT cdr_schema")
            logging.exception(
                "Схема раздела «Касания» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT cdr_schema")

    def _init_pbx_schema_tx(self, cursor):
        """Схема раздела «АТС» (pbx_calls, pbx_commands, pbx_connector_state) — живые
        звонки станции от коннектора и команды ей. Под SAVEPOINT, как соседи: упавший
        раздел не роняет старт приложения, его ручки отвечают 503 словами."""
        import logging

        cursor.execute("SAVEPOINT pbx_schema")
        try:
            from pbx.schema import init_pbx_schema
            init_pbx_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT pbx_schema")
            logging.exception(
                "Схема раздела «АТС» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT pbx_schema")

    def _init_dial_list_schema_tx(self, cursor):
        """Схема раздела «Обзвон из телефона» (таблицы dial_list_*).

        Под SAVEPOINT, как соседи: весь _init_db идёт одной транзакцией, и
        упавший раздел не должен уронить старт приложения. Раздел без схемы
        отвечает 500 на своих ручках, остальное приложение работает штатно.
        """
        import logging

        cursor.execute("SAVEPOINT dial_list_schema")
        try:
            from dial_list.schema import init_dial_list_schema
            init_dial_list_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT dial_list_schema")
            logging.exception(
                "Схема раздела «Обзвон из телефона» не применилась — раздел будет "
                "недоступен, остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT dial_list_schema")

    def _init_op_funnel_schema_tx(self, cursor):
        """Схема раздела «Воронка ОП» (таблицы op_funnel_*) и сид норм.

        Под SAVEPOINT по той же причине, что и соседи: весь _init_db идёт одной
        транзакцией, и упавший раздел не имеет права уронить старт приложения.
        При отказе роуты отвечают понятным 503 (schema_is_ready), а не падают
        пятисоткой из-под первого SELECT.

        Сид норм (нормы дозвонов в час, планы на ставку, таргеты Верификаторов)
        стоит здесь, а не миграцией отдельным скриптом: он идемпотентен
        (ON CONFLICT DO NOTHING), а раздел без норм показывал бы пустой план и
        выглядел бы сломанным на чистой базе.
        """
        import logging

        cursor.execute("SAVEPOINT op_funnel_schema")
        try:
            from op_funnel.schema import init_op_funnel_schema
            init_op_funnel_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT op_funnel_schema")
            logging.exception(
                "Схема раздела «Воронка ОП» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT op_funnel_schema")

    def _init_wazzup_workspace_schema_tx(self, cursor):
        """Схема рабочего места верификатора в «Чатах ОП» (wazzup/workspace_schema.py):
        подтверждённый доступ сессии, ожидание кода из Telegram, отметки портала.

        Под SAVEPOINT, как соседи: упавший DDL раздела не вправе уронить
        инициализацию всей схемы. Цена отката — раздел остаётся закрытым для
        верификаторов (доступ подтвердить негде), остальной портал и просмотр
        переписки работают как раньше."""
        import logging

        cursor.execute("SAVEPOINT wazzup_workspace_schema")
        try:
            from wazzup.workspace_schema import init_wazzup_workspace_schema
            init_wazzup_workspace_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT wazzup_workspace_schema")
            logging.exception(
                "Схема рабочего места «Чатов ОП» не применилась — верификаторам раздел "
                "не откроется, остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT wazzup_workspace_schema")

    def _init_qa_marketing_schema_tx(self, cursor):
        """Схема модуля «Маркетинговый мониторинг» (ТЗ #317): таблицы qa_marketing_*.

        Идёт ПОСЛЕ «Воронки ОП»: связь разговора со сделкой смотрит в
        `op_funnel_leads`, и разворачивать её раньше самих лидов незачем.

        Под SAVEPOINT по той же причине, что и соседи. Цена отката здесь
        мягкая и это сделано намеренно: без этих таблиц «ИИ-оценка» работает
        ровно как до модуля — просто без маркетингового отбора. Панель фильтров
        узнаёт об этом по пустому справочнику и не показывает мёртвых полей.
        """
        import logging

        cursor.execute("SAVEPOINT qa_marketing_schema")
        try:
            from call_qa.marketing.schema import init_qa_marketing_schema
            init_qa_marketing_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT qa_marketing_schema")
            logging.exception(
                "Схема «Маркетингового мониторинга» не применилась — раздел «ИИ-оценка» "
                "работает без маркетингового отбора, остальное приложение штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT qa_marketing_schema")

    def _init_trainings_schema_tx(self, cursor):
        """Схема раздела «Тренинги»: справочник корпоративных тем (training_topics)
        и привязка проведённого тренинга к теме (trainings.topic_id).

        Разворачивается ПОСЛЕ базового DDL, потому что опирается на уже
        существующие `trainings`, `departments` и `users`, и расширяет
        именованный констрейнт trainings_reason_check.

        SAVEPOINT — как у остальных разделов: весь _init_db идёт одной
        транзакцией, и падение здесь не должно ронять инициализацию базы.
        Цена отката, впрочем, конкретная: без расширения CHECK корпоративную
        тему не записать, и раздел честно сообщает об этом через
        schema_ready=false в /api/training_topics.
        """
        import logging

        cursor.execute("SAVEPOINT trainings_schema")
        try:
            from trainings.schema import init_trainings_schema
            init_trainings_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT trainings_schema")
            logging.exception(
                "Схема раздела «Тренинги» не применилась — корпоративные темы будут "
                "недоступны, остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT trainings_schema")

    def _init_icore_phone_schema_tx(self, cursor):
        """Реестр релизов iCORE Phone — то, по чему телефоны решают, обновляться ли.

        Схема скопирована с oktell_guard_releases (там она уже год возит
        обновления агента), плюс два поля:
          * variant — online-инсталлятор (~6 МБ, качается автообновлением) и
            offline (~218 МБ, только для первичной установки руками);
          * mandatory — «поставить молча при первой паузе», без права отложить.

        SAVEPOINT и порядок CREATE TABLE → CREATE INDEX — как у остальных
        разделов: весь _init_db идёт одной транзакцией, и падение здесь не
        должно ронять инициализацию базы.
        """
        import logging

        cursor.execute("SAVEPOINT icore_phone_schema")
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS icore_phone_releases (
                    id          SERIAL PRIMARY KEY,
                    version     VARCHAR(32) NOT NULL,
                    variant     VARCHAR(16) NOT NULL DEFAULT 'online',
                    filename    VARCHAR(160) NOT NULL DEFAULT '',
                    sha256      CHAR(64) NOT NULL,
                    size_bytes  BIGINT NOT NULL,
                    gcs_bucket  VARCHAR(255) NOT NULL DEFAULT '',
                    gcs_path    VARCHAR(512) NOT NULL DEFAULT '',
                    notes       TEXT NOT NULL DEFAULT '',
                    mandatory   BOOLEAN NOT NULL DEFAULT FALSE,
                    is_current  BOOLEAN NOT NULL DEFAULT TRUE,
                    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    uploaded_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            # Одна версия одного варианта — один раз: повторная публикация той же
            # сборки должна обновлять строку, а не плодить дубли с разными sha256.
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS icore_phone_releases_variant_version_idx
                ON icore_phone_releases (variant, version);
            """)
            # Текущая ровно одна на вариант: «две текущие» означали бы, что часть
            # парка уедет не туда, а телефон берёт релиз без ORDER BY.
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS icore_phone_releases_current_idx
                ON icore_phone_releases (variant)
                WHERE is_current;
            """)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT icore_phone_schema")
            logging.exception(
                "Схема релизов iCORE Phone не применилась — автообновление телефона "
                "будет недоступно, остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT icore_phone_schema")

    def _init_reg_contest_schema_tx(self, cursor):
        """Конкурс «Топ по регистрациям» (данные из CRM yataxi).

        Строка reg_contest_operators = один оператор CRM с двумя счётчиками.
        Раньше строка была водителем, но CRM убрала построчную выдачу и отдаёт
        только агрегаты (см. docstring reg_contest.py).

        reached_at — момент, когда мы ЗАСЕКЛИ смену счётчика successful. Это
        наш собственный штамп: времени поездок CRM больше не присылает, а
        тай-брейк конкурса — «выше тот, кто набрал результат раньше». Поэтому
        синк не заменяет строки целиком, а обновляет их на месте: снести и
        вставить заново значило бы потерять reached_at на каждом прогоне."""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reg_contest_operators (
                id            SERIAL PRIMARY KEY,
                contest_code  VARCHAR(64) NOT NULL,
                crm_operator_id VARCHAR(64) NOT NULL,
                operator_login  VARCHAR(255),
                operator_name   VARCHAR(255),
                user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
                user_name     VARCHAR(255),
                contest_group VARCHAR(10) NOT NULL DEFAULT 'off'
                    CHECK (contest_group IN ('chat', 'line', 'off')),
                match_method  VARCHAR(20),
                registrations INTEGER NOT NULL DEFAULT 0,
                successful    INTEGER NOT NULL DEFAULT 0,
                reached_at    TIMESTAMPTZ,
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (contest_code, crm_operator_id)
            );
            CREATE INDEX IF NOT EXISTS idx_reg_contest_operators_group
                ON reg_contest_operators(contest_code, contest_group);

            CREATE TABLE IF NOT EXISTS reg_contest_syncs (
                contest_code  VARCHAR(64) PRIMARY KEY,
                synced_at     TIMESTAMPTZ,
                status        VARCHAR(20) NOT NULL DEFAULT 'ok',
                total_rows    INTEGER NOT NULL DEFAULT 0,
                error         TEXT
            );

            -- Журнал изменений счётчиков. Строка пишется только когда счёт
            -- РЕАЛЬНО поменялся, то есть несколько строк в сутки, а не 69
            -- на каждый получасовой синк. Нужен потому, что CRM переписывает
            -- прошлое (см. п. 5 в шапке reg_contest.py): без журнала вопрос
            -- «почему у меня стало меньше» отвечать нечем — в срезе лежит
            -- только текущее число, а прошлое нигде не остаётся.
            -- NULL в *_before = оператор появился в выдаче;
            -- NULL в *_after  = оператор из выдачи пропал.
            CREATE TABLE IF NOT EXISTS reg_contest_operator_changes (
                id            BIGSERIAL PRIMARY KEY,
                contest_code  VARCHAR(64) NOT NULL,
                crm_operator_id VARCHAR(64) NOT NULL,
                operator_name VARCHAR(255),
                registrations_before INTEGER,
                registrations_after  INTEGER,
                successful_before    INTEGER,
                successful_after     INTEGER,
                changed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_reg_contest_changes_at
                ON reg_contest_operator_changes(contest_code, changed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_reg_contest_changes_operator
                ON reg_contest_operator_changes(contest_code, crm_operator_id, changed_at DESC);
        """)
        # Метка просадки на самом синке: сколько счётчиков ушло вниз в этом
        # прогоне и у кого — чтобы админ видел это в разделе, а не только в логе.
        cursor.execute("""
            ALTER TABLE reg_contest_syncs
                ADD COLUMN IF NOT EXISTS decreases INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE reg_contest_syncs
                ADD COLUMN IF NOT EXISTS decrease_note TEXT;
        """)

    def _init_group_late_bot_schema_tx(self, cursor):
        """Схема раздела «Бот опозданий» (group_late_bot, префикс glb_).

        Владелец схемы — OTP: бот на Render только читает и пишет строки, DDL не
        выполняет. До этого его состояние лежало в chats.json/mutes.json на
        эфемерном диске Render и терялось при каждом деплое, а истории отбивок и
        отчётов не было вовсе — раздел без общей БД показывать было бы нечего.
        """
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS glb_chats (
                chat_id           TEXT PRIMARY KEY,
                title             TEXT,
                chat_type         TEXT,
                is_admin_chat     BOOLEAN NOT NULL DEFAULT FALSE,
                enabled           BOOLEAN NOT NULL DEFAULT TRUE,
                note              TEXT,
                created_by        TEXT,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_delivery_at  TIMESTAMPTZ
            );

            -- Связка «чат → отдел». Пусто = чат получает все отделы.
            CREATE TABLE IF NOT EXISTS glb_chat_departments (
                id              BIGSERIAL PRIMARY KEY,
                chat_id         TEXT NOT NULL REFERENCES glb_chats(chat_id) ON DELETE CASCADE,
                department_name TEXT NOT NULL,
                created_by      TEXT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_glb_chat_departments
                ON glb_chat_departments(chat_id, lower(department_name));

            -- Правила тишины. chat_id IS NULL = глобальное правило.
            CREATE TABLE IF NOT EXISTS glb_mutes (
                id          BIGSERIAL PRIMARY KEY,
                chat_id     TEXT,
                mute_kind   TEXT NOT NULL,
                mute_value  TEXT NOT NULL DEFAULT '',
                created_by  TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_glb_mutes
                ON glb_mutes(COALESCE(chat_id, ''), mute_kind, lower(mute_value));

            -- Нарушение, найденное опросом Workpace. event_key = ключ дедупликации
            -- (сотрудник + дата + тип): раньше он жил в памяти процесса и после
            -- рестарта бот слал повторы.
            CREATE TABLE IF NOT EXISTS glb_events (
                id               BIGSERIAL PRIMARY KEY,
                event_key        TEXT NOT NULL UNIQUE,
                event_type       TEXT NOT NULL,
                event_date       DATE NOT NULL,
                employee_name    TEXT,
                employee_ext_id  TEXT,
                department_name  TEXT,
                schedule_name    TEXT,
                plan_at          TIMESTAMPTZ,
                fact_at          TIMESTAMPTZ,
                minutes          INTEGER,
                location         TEXT,
                message_text     TEXT,
                detected_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_glb_events_date ON glb_events(event_date DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_glb_events_type ON glb_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_glb_events_dept ON glb_events(lower(department_name));

            -- Отметка «Отбито» под уведомлением убрана по решению владельца: она
            -- ничего не меняла ни в Workpace, ни в отчётах и не хранила причину.
            -- Колонки и индекс подчищаем у баз, которые их успели создать.
            DROP INDEX IF EXISTS idx_glb_events_pending;
            ALTER TABLE glb_events
                DROP COLUMN IF EXISTS ack_at,
                DROP COLUMN IF EXISTS ack_by,
                DROP COLUMN IF EXISTS ack_chat_id,
                DROP COLUMN IF EXISTS ack_source;

            -- В какие чаты ушло уведомление и каким сообщением.
            CREATE TABLE IF NOT EXISTS glb_event_deliveries (
                id                  BIGSERIAL PRIMARY KEY,
                event_id            BIGINT NOT NULL REFERENCES glb_events(id) ON DELETE CASCADE,
                chat_id             TEXT NOT NULL,
                telegram_message_id TEXT,
                sent_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                error               TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_glb_event_deliveries
                ON glb_event_deliveries(event_id, chat_id);
            CREATE INDEX IF NOT EXISTS idx_glb_event_deliveries_chat
                ON glb_event_deliveries(chat_id, sent_at DESC);

            CREATE TABLE IF NOT EXISTS glb_reports (
                id                BIGSERIAL PRIMARY KEY,
                source            TEXT NOT NULL DEFAULT 'telegram',
                requested_chat_id TEXT,
                requested_by      TEXT,
                date_from         DATE NOT NULL,
                date_to           DATE NOT NULL,
                department_filter TEXT,
                status            TEXT NOT NULL DEFAULT 'ok',
                error             TEXT,
                file_name         TEXT,
                file_size         INTEGER,
                rows_count        INTEGER,
                employees_count   INTEGER,
                late_count        INTEGER,
                absent_count      INTEGER,
                duration_ms       INTEGER,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            -- Отчёт по конкретным людям, а не по всему отделу (задача #273):
            -- список ФИО через перевод строки. Пустое значение = весь отдел.
            ALTER TABLE glb_reports ADD COLUMN IF NOT EXISTS employee_filter TEXT;
            CREATE INDEX IF NOT EXISTS idx_glb_reports_created ON glb_reports(created_at DESC);

            -- Тело Excel-файла отдельно от карточки: список отчётов читается часто,
            -- файл — только при скачивании.
            CREATE TABLE IF NOT EXISTS glb_report_files (
                report_id BIGINT PRIMARY KEY REFERENCES glb_reports(id) ON DELETE CASCADE,
                content   BYTEA NOT NULL
            );

            CREATE TABLE IF NOT EXISTS glb_poll_runs (
                id           BIGSERIAL PRIMARY KEY,
                started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at  TIMESTAMPTZ,
                ok           BOOLEAN,
                fetched      INTEGER,
                events_found INTEGER,
                sent         INTEGER,
                duration_ms  INTEGER,
                error        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_glb_poll_runs_started ON glb_poll_runs(started_at DESC);

            -- Кэш справочника отделов Workpace: сайт не ходит в Workpace сам.
            CREATE TABLE IF NOT EXISTS glb_departments (
                name            TEXT PRIMARY KEY,
                employees_count INTEGER NOT NULL DEFAULT 0,
                synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            -- Кэш списка сотрудников Workpace. Нужен вкладке «Сотрудники»: без него
            -- в таблице видны только те, у кого за период были нарушения, а нужен
            -- весь состав отдела. Наполняет опрос (он и так тянет сотрудников),
            -- архивных Workpace не отдаёт — они просто уходят из кэша.
            CREATE TABLE IF NOT EXISTS glb_employees (
                ext_id          TEXT PRIMARY KEY,
                external_id     TEXT,
                full_name       TEXT NOT NULL,
                department_name TEXT,
                synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_glb_employees_dept
                ON glb_employees(lower(department_name));

            -- Ручная привязка карточки Workpace к нашему сотруднику. Автоматически
            -- они сшиваются по ФИО (см. _glb_resolve_entry), и в большинстве случаев
            -- этого хватает; таблица — про остальные: смена фамилии, две карточки на
            -- одного человека, чужая компания холдинга в том же отделе.
            -- user_id IS NULL = «это не наш сотрудник, контролировать не нужно»:
            -- отсутствие строки и явное исключение — разные вещи, иначе исключение
            -- каждый раз возвращалось бы автоматическим матчингом.
            CREATE TABLE IF NOT EXISTS glb_employee_links (
                workpace_ext_id TEXT PRIMARY KEY,
                user_id         INTEGER NULL REFERENCES users(id) ON DELETE CASCADE,
                linked_by       INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                linked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_glb_employee_links_user
                ON glb_employee_links(user_id) WHERE user_id IS NOT NULL;

            -- План из графика iCore читается по операторам отдела за дату, а исключения
            -- (отпуск, больничный, Б/С, увольнение) проверяются по периодам статусов.
            CREATE INDEX IF NOT EXISTS idx_osp_operator_dates
                ON operator_schedule_status_periods(operator_id, start_date);
        """)

        # Задача #307. Раздел перерос один источник: состав теперь приходит и из
        # Clockster, период просмотра стал неограниченным, а план смены кадровик
        # заводит сам. Всё это — отдельным блоком, чтобы правка не конфликтовала
        # с DDL выше при параллельной работе двух машин.
        cursor.execute("""
            -- Откуда состав: Workpace или Clockster. Полная замена в glb_sync_*
            -- обязана считаться ПО ИСТОЧНИКУ, иначе двухминутный опрос Workpace
            -- каждый раз стирал бы центральный офис.
            ALTER TABLE glb_employees ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'workpace';
            ALTER TABLE glb_employees ADD COLUMN IF NOT EXISTS position_name TEXT;
            CREATE INDEX IF NOT EXISTS idx_glb_employees_source ON glb_employees(source);

            -- Кэш строк раздела «Отметки» по дням. Без него период на экране был
            -- ограничен неделей: каждый день — два запроса в Workpace, и месяц
            -- собирался бы около минуты прямо в HTTP-запросе. С кэшем прошедшие
            -- дни читаются из базы, а живьём тянется только сегодняшний.
            CREATE TABLE IF NOT EXISTS glb_attendance_rows (
                day               DATE NOT NULL,
                employee_id       TEXT NOT NULL,
                seq               SMALLINT NOT NULL DEFAULT 0,
                employee_name     TEXT NOT NULL,
                department_name   TEXT,
                position_name     TEXT,
                location_name     TEXT,
                schedule_name     TEXT,
                system            TEXT NOT NULL,
                plan_in           TIMESTAMPTZ,
                plan_out          TIMESTAMPTZ,
                fact_in           TIMESTAMPTZ,
                fact_out          TIMESTAMPTZ,
                late_minutes      INTEGER NOT NULL DEFAULT 0,
                early_out_minutes INTEGER NOT NULL DEFAULT 0,
                work_seconds      INTEGER NOT NULL DEFAULT 0,
                present_seconds   INTEGER NOT NULL DEFAULT 0,
                lunch_seconds     INTEGER NOT NULL DEFAULT 0,
                status            TEXT NOT NULL,
                plan_mode         TEXT NOT NULL DEFAULT 'schedule',
                plan_source       TEXT,
                hours_norm        NUMERIC(5,2),
                marks             JSONB NOT NULL DEFAULT '[]'::jsonb,
                PRIMARY KEY (day, employee_id, seq)
            );
            CREATE INDEX IF NOT EXISTS idx_glb_att_rows_day ON glb_attendance_rows(day DESC);
            CREATE INDEX IF NOT EXISTS idx_glb_att_rows_dept
                ON glb_attendance_rows(lower(COALESCE(department_name, '')));
            CREATE INDEX IF NOT EXISTS idx_glb_att_rows_name
                ON glb_attendance_rows(lower(employee_name));

            -- Какие дни уже собраны. Отдельная таблица, а не признак у строк:
            -- день без единой отметки — это тоже собранный день, и без отметки
            -- «собран» он тянулся бы из Workpace заново при каждом открытии.
            CREATE TABLE IF NOT EXISTS glb_attendance_days (
                day        DATE PRIMARY KEY,
                built_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                rows_count INTEGER NOT NULL DEFAULT 0,
                sources    TEXT
            );
            -- Версия правил, которыми день собран (ТЗ iCore 3, п. 4): день прежних
            -- правил пересобирается, а не отдаётся из кэша со старыми числами.
            ALTER TABLE glb_attendance_days
                ADD COLUMN IF NOT EXISTS engine_version SMALLINT NOT NULL DEFAULT 1;

            -- Свой график смен (ТЗ #307, п. 5): кадровик заводит план на проект
            -- целиком или на конкретного человека, в том числе на выходные дни.
            -- Наш план ДОПОЛНЯЕТ источники, а не спорит с ними: он ставится
            -- только в те дни, где ни Workpace, ни Clockster смены не дали.
            -- Иначе одно правило «пн–пт 10:00–19:00» на отдел перебило бы
            -- настоящий сменный график колл-центра и сделало бы опоздавшими всех.
            CREATE TABLE IF NOT EXISTS glb_plan_rules (
                id            SERIAL PRIMARY KEY,
                scope         TEXT NOT NULL,
                target        TEXT NOT NULL,
                target_label  TEXT,
                mode          TEXT NOT NULL DEFAULT 'schedule',
                weekdays      SMALLINT[],
                date_from     DATE,
                date_to       DATE,
                time_start    TEXT,
                time_end      TEXT,
                break_minutes INTEGER,
                hours_norm    NUMERIC(5,2),
                note          TEXT,
                enabled       BOOLEAN NOT NULL DEFAULT TRUE,
                created_by    INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_glb_plan_rules_target
                ON glb_plan_rules(scope, lower(target)) WHERE enabled;
        """)

    def _backfill_null_group_id_tx(self, cursor):
        """Стартовый прогон _stamp_orphan_group_ids_tx по всем операторам."""
        daily_fixed, wh_fixed = self._stamp_orphan_group_ids_tx(cursor)
        if daily_fixed or wh_fixed:
            logging.info("backfill NULL group_id: daily=%s work_hours=%s", daily_fixed, wh_fixed)

    def _backfill_work_hours_rate_from_history_tx(self, cursor):
        """
        Идемпотентный одноразовый backfill помесячной ставки `work_hours.rate`.

        Раньше ставка оператора (`users.rate`) была статичной — одинаковой для всех
        месяцев. Теперь ставка хранится отдельно на каждый месяц и переносится на
        следующие месяцы (carry-forward) до очередного изменения.

        Для операторов, у которых ставка менялась (есть записи в `user_history` с
        field_changed='rate'), проставляем историческую ставку за каждый прошедший
        месяц по правилу «ставка месяца = значение, действовавшее на конец месяца».
        `norm_hours` не пересчитывается и не перезаписывается.

        Текущий и будущие месяцы оставляем NULL: на чтении они подхватывают актуальную
        ставку по переносу/из `users.rate`, а изменение ставки якорит текущий месяц.

        Идемпотентность: заполняются только строки прошлых месяцев с rate IS NULL,
        поэтому повторные запуски (каждый деплой) ничего не портят.
        """
        # Текущий месяц по серверной дате БД.
        cursor.execute("SELECT TO_CHAR(CURRENT_DATE, 'YYYY-MM')")
        current_month = cursor.fetchone()[0]

        # Быстрый guard: есть ли вообще что заполнять (прошлые месяцы без ставки).
        cursor.execute(
            "SELECT 1 FROM work_hours WHERE rate IS NULL AND month < %s LIMIT 1",
            (current_month,),
        )
        if cursor.fetchone() is None:
            return

        allowed_rates = (1.0, 0.75, 0.5)

        def _snap(raw):
            try:
                val = float(raw)
            except (TypeError, ValueError):
                return None
            return min(allowed_rates, key=lambda a: abs(a - val))

        # История изменений ставок: оператор -> отсортированный по времени список
        # (changed_at, new_rate); плюс ставка до самого первого изменения.
        cursor.execute(
            """
            SELECT user_id, old_value, new_value, changed_at
            FROM user_history
            WHERE field_changed = 'rate'
            ORDER BY user_id, changed_at
            """
        )
        changes_by_op = {}
        first_old_by_op = {}
        for user_id, old_value, new_value, changed_at in cursor.fetchall():
            new_rate = _snap(new_value)
            if new_rate is None or changed_at is None:
                continue
            if user_id not in changes_by_op:
                changes_by_op[user_id] = []
                first_old_by_op[user_id] = _snap(old_value)
            changes_by_op[user_id].append((changed_at, new_rate))

        if not changes_by_op:
            return

        def _next_month_start(month_str):
            year, mon = map(int, month_str.split('-'))
            return datetime(year + 1, 1, 1) if mon == 12 else datetime(year, mon + 1, 1)

        def _rate_for_month(user_id, month_str):
            changes = changes_by_op.get(user_id)
            if not changes:
                return None
            # конец месяца = строго до начала следующего месяца
            ref = _next_month_start(month_str)
            eff = None
            for changed_at, new_rate in changes:
                if changed_at < ref:
                    eff = new_rate
                else:
                    break
            if eff is None:
                # месяц раньше самого первого изменения — берём ставку до него
                eff = first_old_by_op.get(user_id)
            return eff

        # Заполняем только существующие строки прошлых месяцев без ставки и только
        # для операторов, у которых ставка менялась.
        cursor.execute(
            "SELECT operator_id, month FROM work_hours WHERE rate IS NULL AND month < %s",
            (current_month,),
        )
        updates = []
        for operator_id, month_str in cursor.fetchall():
            if operator_id not in changes_by_op:
                continue
            rate_val = _rate_for_month(operator_id, month_str)
            if rate_val is None:
                continue
            updates.append((rate_val, operator_id, month_str))

        if not updates:
            return

        cursor.executemany(
            """
            UPDATE work_hours
            SET rate = %s
            WHERE operator_id = %s AND month = %s AND rate IS NULL
            """,
            updates,
        )
        logging.info(
            "work_hours.rate backfill: проставлено %s помесячных ставок для %s операторов",
            len(updates), len({u[1] for u in updates}),
        )

    def _backfill_groups_from_supervisors_tx(self, cursor):
        """
        Одноразовый идемпотентный сидинг групп из текущих супервайзеров.

        Для каждого СВ создаётся группа "<имя СВ> группа <основное направление>",
        к ней привязывается сам СВ и его текущие операторы (по users.supervisor_id),
        а модель расчёта группы берётся из основного (самого частого) направления
        его операторов. Затем на существующих daily_hours/work_hours проставляется
        group_id по членству — best-effort: вся история оператора относится к его
        текущей группе, т.к. историю переводов между СВ мы достоверно не знаем.

        Идемпотентность: если группы уже существуют — выходим (сидинг выполнен).
        Дальнейшее создание групп/членств идёт через API групп, не через сидинг.
        Членства открываются с даты найма (или эпохи), чтобы as-of резолвер
        покрывал всю прошлую историю часов оператора в его текущей группе.
        """
        cursor.execute("SELECT 1 FROM groups LIMIT 1")
        if cursor.fetchone() is not None:
            return  # уже засидено

        EPOCH = '2000-01-01'

        # Справочник направлений: id -> (name, model_code, department_id)
        cursor.execute(
            "SELECT id, name, calculation_model_code, department_id FROM directions"
        )
        dir_map = {}
        for did, dname, dmodel, ddept in cursor.fetchall() or []:
            dir_map[int(did)] = (
                dname,
                normalize_calculation_model_code(dmodel, dname),
                ddept,
            )

        # Текущие супервайзеры.
        cursor.execute(
            """
            SELECT id, name, department_id, hire_date
            FROM users
            WHERE LOWER(COALESCE(role, '')) IN ('sv', 'supervisor')
            ORDER BY id
            """
        )
        supervisors = cursor.fetchall() or []

        groups_created = 0
        sv_memberships = 0
        op_memberships = 0

        for sv_id, sv_name, sv_dept, sv_hire in supervisors:
            # Операторы этого СВ (по текущему supervisor_id).
            cursor.execute(
                """
                SELECT id, direction_id, hire_date
                FROM users
                WHERE supervisor_id = %s AND LOWER(COALESCE(role, '')) = 'operator'
                ORDER BY id
                """,
                (sv_id,),
            )
            operators = cursor.fetchall() or []

            # Основное направление = самое частое среди операторов (без NULL).
            dir_counts = defaultdict(int)
            for _oid, d, _h in operators:
                if d is not None:
                    dir_counts[int(d)] += 1
            main_dir_id = max(dir_counts, key=dir_counts.get) if dir_counts else None

            if main_dir_id is not None and main_dir_id in dir_map:
                dir_name, model_code, dir_dept = dir_map[main_dir_id]
            else:
                dir_name, model_code, dir_dept = None, CALCULATION_MODEL_OPERATOR, None

            dept_id = sv_dept or dir_dept
            group_name = (
                "{} группа {}".format(sv_name, dir_name)
                if dir_name else "{} группа".format(sv_name)
            )

            cursor.execute(
                """
                INSERT INTO groups
                    (name, department_id, direction_id, calculation_model_code,
                     status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (group_name, dept_id, main_dir_id, model_code),
            )
            group_id = cursor.fetchone()[0]
            groups_created += 1

            cursor.execute(
                """
                INSERT INTO group_supervisor_memberships
                    (group_id, supervisor_id, start_date, created_at)
                VALUES (%s, %s, COALESCE(%s::date, %s::date), CURRENT_TIMESTAMP)
                """,
                (group_id, sv_id, sv_hire, EPOCH),
            )
            sv_memberships += 1

            if operators:
                cursor.executemany(
                    """
                    INSERT INTO group_operator_memberships
                        (group_id, operator_id, start_date, created_at)
                    VALUES (%s, %s, COALESCE(%s::date, %s::date), CURRENT_TIMESTAMP)
                    """,
                    [(group_id, int(oid), ohire, EPOCH) for oid, _d, ohire in operators],
                )
                op_memberships += len(operators)

        # Проставляем group_id на исторических часах по членству (best-effort:
        # вся история оператора уходит в его текущую группу).
        cursor.execute(
            """
            UPDATE daily_hours d
            SET group_id = gom.group_id
            FROM group_operator_memberships gom
            WHERE d.operator_id = gom.operator_id
              AND d.group_id IS NULL
              AND d.day >= gom.start_date
              AND (gom.end_date IS NULL OR d.day <= gom.end_date)
            """
        )
        daily_stamped = cursor.rowcount

        cursor.execute(
            """
            UPDATE work_hours w
            SET group_id = gom.group_id
            FROM group_operator_memberships gom
            WHERE w.operator_id = gom.operator_id
              AND w.group_id IS NULL
              AND w.month >= TO_CHAR(gom.start_date, 'YYYY-MM')
              AND (gom.end_date IS NULL OR w.month <= TO_CHAR(gom.end_date, 'YYYY-MM'))
            """
        )
        work_stamped = cursor.rowcount

        logging.info(
            "groups backfill: создано групп=%s, СВ-членств=%s, операторских членств=%s; "
            "проставлен group_id: daily_hours=%s, work_hours=%s",
            groups_created, sv_memberships, op_memberships, daily_stamped, work_stamped,
        )

    def _backfill_user_profiles_tx(self, cursor):
        """
        Этап 1 рефакторинга «отделы + профили»: идемпотентно выровнять профильные
        таблицы по авторитетному источнику `users`.

        - создаёт недостающие строки профилей (на случай будущих пользователей);
        - переносит в user_hr_profiles перенесённые поля (proxy / driver license /
          онбординг);
        - чинит исторический дрейф operator_profiles (например, direction_id) по users;
        - кладёт taxipro_id в operator_profiles.attributes (JSONB).

        Идемпотентность: каждый UPDATE ограничен `IS DISTINCT FROM`, поэтому при
        отсутствии расхождений это no-op. Источник истины — `users` (на время
        «фундамента»; до Этапа 8 users держится в синхроне двойной записью).
        ВНИМАНИЕ: на Этапе 8 (drop колонок users) этот бэкофилл удалить.
        """
        # 1) гарантируем наличие строк профилей
        cursor.execute("""
            INSERT INTO user_hr_profiles (user_id)
            SELECT id FROM users
            ON CONFLICT (user_id) DO NOTHING
        """)
        cursor.execute("""
            INSERT INTO operator_profiles (user_id)
            SELECT id FROM users WHERE role IN ('operator', 'trainee')
            ON CONFLICT (user_id) DO NOTHING
        """)

        # 2) HR-поля, перенесённые из users (proxy / driver license / онбординг)
        cursor.execute("""
            UPDATE user_hr_profiles h SET
                has_proxy = u.has_proxy,
                proxy_card_number = u.proxy_card_number,
                proxy_status = u.proxy_status,
                has_driver_license = u.has_driver_license,
                internship_in_company = u.internship_in_company,
                front_office_training = u.front_office_training,
                front_office_training_date = u.front_office_training_date
            FROM users u
            WHERE h.user_id = u.id
              AND (
                    h.has_proxy IS DISTINCT FROM u.has_proxy
                 OR h.proxy_card_number IS DISTINCT FROM u.proxy_card_number
                 OR h.proxy_status IS DISTINCT FROM u.proxy_status
                 OR h.has_driver_license IS DISTINCT FROM u.has_driver_license
                 OR h.internship_in_company IS DISTINCT FROM u.internship_in_company
                 OR h.front_office_training IS DISTINCT FROM u.front_office_training
                 OR h.front_office_training_date IS DISTINCT FROM u.front_office_training_date
              )
        """)

        # 3) операторские overlap-поля — чиним дрейф по users (например, direction_id)
        cursor.execute("""
            UPDATE operator_profiles o SET
                direction_id = u.direction_id,
                supervisor_id = u.supervisor_id,
                is_active = u.is_active,
                rate = u.rate,
                sip_number = u.sip_number
            FROM users u
            WHERE o.user_id = u.id
              AND (
                    o.direction_id IS DISTINCT FROM u.direction_id
                 OR o.supervisor_id IS DISTINCT FROM u.supervisor_id
                 OR o.is_active IS DISTINCT FROM u.is_active
                 OR o.rate IS DISTINCT FROM u.rate
                 OR o.sip_number IS DISTINCT FROM u.sip_number
              )
        """)

        # 4) taxipro_id -> JSONB attributes (спец-поле отдела СЗоВ)
        cursor.execute("""
            UPDATE operator_profiles o SET
                attributes = COALESCE(o.attributes, '{}'::jsonb)
                             || jsonb_build_object('taxipro_id', u.taxipro_id)
            FROM users u
            WHERE o.user_id = u.id
              AND u.taxipro_id IS NOT NULL AND u.taxipro_id <> ''
              AND (o.attributes->>'taxipro_id') IS DISTINCT FROM u.taxipro_id
        """)

    def _backfill_shift_auction_history_tables_tx(self, cursor):
        """
        Перенести данные из журнала событий в новые таблицы
        shift_auction_published_periods и shift_auction_historical_claims.
        Идемпотентно за счёт ON CONFLICT DO NOTHING — безопасно гонять при
        каждом старте, повторные записи не появляются.
        """
        cursor.execute("SELECT 1 FROM shift_auction_published_periods LIMIT 1")
        periods_already_filled = cursor.fetchone() is not None

        if not periods_already_filled:
            cursor.execute("""
                SELECT id, date_from, date_to
                FROM resource_saved_schedule_plans
                WHERE archived_at IS NULL
            """)
            plans_by_id = {}
            plans_by_dates = {}
            for plan_id, date_from, date_to in (cursor.fetchall() or []):
                plans_by_id[int(plan_id)] = (date_from, date_to)
                if date_from and date_to:
                    plans_by_dates[(date_from.strftime('%Y-%m-%d'), date_to.strftime('%Y-%m-%d'))] = int(plan_id)

            cursor.execute("""
                SELECT id, event_type, payload, created_at
                FROM shift_auction_test_events
                WHERE event_type IN ('settings_updated', 'auction_restarted', 'auction_published')
                ORDER BY id
            """)
            current_plan_id = None
            current_participants = []
            current_dates = (None, None)
            for _event_id, event_type, payload, created_at in (cursor.fetchall() or []):
                payload = payload or {}
                raw_plan_id = payload.get('selected_schedule_plan_id') or payload.get('plan_id')
                try:
                    parsed_plan_id = int(raw_plan_id) if raw_plan_id not in (None, '') else None
                except Exception:
                    parsed_plan_id = None
                date_from = payload.get('date_from')
                date_to = payload.get('date_to')
                if event_type in ('settings_updated', 'auction_restarted'):
                    if parsed_plan_id is not None:
                        current_plan_id = parsed_plan_id
                    elif date_from and date_to and (date_from, date_to) in plans_by_dates:
                        current_plan_id = plans_by_dates[(date_from, date_to)]
                    if isinstance(payload.get('selected_operator_ids'), list):
                        current_participants = [int(x) for x in payload['selected_operator_ids'] if str(x).isdigit()]
                    if date_from and date_to:
                        current_dates = (date_from, date_to)
                    continue
                if event_type == 'auction_published':
                    plan_for_pub = parsed_plan_id or current_plan_id
                    if plan_for_pub is None:
                        continue
                    plan_dates = plans_by_id.get(plan_for_pub)
                    if plan_dates is None:
                        continue
                    cursor.execute("""
                        INSERT INTO shift_auction_published_periods
                            (plan_id, date_from, date_to, published_at, published_by, participant_ids, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (plan_id) DO UPDATE
                          SET published_at = EXCLUDED.published_at,
                              published_by = EXCLUDED.published_by,
                              participant_ids = CASE
                                WHEN COALESCE(array_length(EXCLUDED.participant_ids, 1), 0) > 0
                                  THEN EXCLUDED.participant_ids
                                ELSE shift_auction_published_periods.participant_ids
                              END,
                              updated_at = CURRENT_TIMESTAMP
                    """, (
                        plan_for_pub,
                        plan_dates[0],
                        plan_dates[1],
                        created_at,
                        payload.get('published_by'),
                        list(current_participants),
                    ))

        # Event-replay для восстановления состояния claim'ов на момент каждой
        # публикации. Идемпотентно: INSERT ON CONFLICT DO NOTHING — повторные
        # запуски ничего не дублируют, поэтому выполняем всегда.
        cursor.execute("""
            SELECT event_type, payload, created_at
            FROM shift_auction_test_events
            WHERE event_type IN ('lot_claimed', 'lot_released', 'lot_post_auction_claimed',
                                 'auction_restarted', 'auction_published', 'settings_updated')
            ORDER BY id
        """)
        in_flight = {}
        current_plan_id_for_publish = None
        for event_type, payload, created_at in (cursor.fetchall() or []):
            payload = payload or {}
            event_name = str(event_type or '')
            lot = payload.get('lot') if isinstance(payload, dict) else None
            if not isinstance(lot, dict):
                lot = {}

            def _parse_int(value):
                try:
                    result = int(value)
                except Exception:
                    return None
                return result if result > 0 else None

            plan_id = _parse_int(lot.get('source_schedule_plan_id'))
            shift_id = _parse_int(lot.get('source_schedule_shift_id'))
            claimed_by = _parse_int(lot.get('claimed_by') or payload.get('operator_id'))
            claim_start_time = lot.get('claim_start_time') or lot.get('post_claim_start_time')
            claim_end_time = lot.get('claim_end_time') or lot.get('post_claim_end_time')
            claimed_at = created_at
            claimed_at_raw = lot.get('claimed_at')
            if claimed_at_raw:
                try:
                    claimed_at = datetime.fromisoformat(str(claimed_at_raw).replace('Z', '+00:00'))
                except Exception:
                    claimed_at = created_at

            if event_name == 'lot_claimed' and plan_id and shift_id and claimed_by:
                in_flight[(plan_id, shift_id)] = (claimed_by, claimed_at, None, None)
            elif event_name == 'lot_released' and plan_id and shift_id:
                in_flight.pop((plan_id, shift_id), None)
            elif event_name == 'lot_post_auction_claimed' and plan_id and shift_id and claimed_by:
                cursor.execute("""
                    INSERT INTO shift_auction_historical_claims
                        (plan_id, source_schedule_shift_id, claimed_by, claimed_at, claimed_start_time, claimed_end_time)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (plan_id, source_schedule_shift_id, claimed_by) DO NOTHING
                """, (plan_id, shift_id, claimed_by, claimed_at, claim_start_time, claim_end_time))
            elif event_name in ('settings_updated', 'auction_restarted'):
                raw_plan_id = payload.get('selected_schedule_plan_id') or payload.get('plan_id')
                parsed_plan_id = _parse_int(raw_plan_id)
                if parsed_plan_id:
                    current_plan_id_for_publish = parsed_plan_id
                if event_name == 'auction_restarted' and parsed_plan_id:
                    in_flight = {k: v for k, v in in_flight.items() if k[0] != parsed_plan_id}
            elif event_name == 'auction_published':
                raw_plan_id = payload.get('selected_schedule_plan_id') or payload.get('plan_id')
                publish_plan_id = _parse_int(raw_plan_id) or current_plan_id_for_publish
                if publish_plan_id:
                    for (p_id, s_id), (cb, ca, cs, ce) in list(in_flight.items()):
                        if p_id != publish_plan_id:
                            continue
                        cursor.execute("""
                            INSERT INTO shift_auction_historical_claims
                                (plan_id, source_schedule_shift_id, claimed_by, claimed_at, claimed_start_time, claimed_end_time)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (plan_id, source_schedule_shift_id, claimed_by) DO NOTHING
                        """, (p_id, s_id, cb, ca, cs, ce))

    def _migrate_chat_low_rating_duplicate_keys(self, cursor):
        """Склеивает дубли низких оценок Chat2Desk и переводит их на новый ключ.

        Chat2Desk с вечера 01.09.2026 отдаёт закрытый день со сдвигом на 5 часов
        (задача #267). Время оценки входило в source_key, поэтому сдвинутая копия
        не склеивалась и в разделе одно обращение показывалось дважды. Ключ
        переведён на номера заявки, а накопленные пары надо свести.

        Выживает строка с САМЫМ РАННИМ rated_at: сверка с отчётом request_stats и
        с сообщениями (там время отдаётся с явной меткой UTC) показала, что верное
        местное время — раннее, а сдвинутая копия уехала на 5 часов вперёд, часть —
        в следующие сутки. Вердикты проверяющих переносятся на выжившую строку;
        если у неё вердикт этого же проверяющего уже есть, остаётся её собственный.

        Идемпотентна: после первого прогона выборка дублей пуста, а переименование
        ключа отфильтровано по несовпадению.
        """
        ranked_cte = f"""
            WITH ranked AS (
                SELECT lr.id,
                       lr.operator_id,
                       lr.day,
                       row_number() OVER (
                           PARTITION BY lr.source, {self._C2D_RATING_KEY_SQL}
                           ORDER BY lr.rated_at ASC, lr.created_at ASC, lr.id ASC
                       ) AS rn,
                       first_value(lr.id) OVER (
                           PARTITION BY lr.source, {self._C2D_RATING_KEY_SQL}
                           ORDER BY lr.rated_at ASC, lr.created_at ASC, lr.id ASC
                       ) AS winner_id
                FROM chat_manager_low_rating_reviews lr
                WHERE {self._C2D_RATING_KEY_SCOPE_SQL}
            )
        """

        # Дни, которые придётся пересчитать: и у выживших, и у удаляемых строк.
        cursor.execute(f"""
            {ranked_cte}
            SELECT DISTINCT lr.operator_id, lr.day
            FROM ranked r
            JOIN chat_manager_low_rating_reviews lr
              ON lr.id IN (r.id, r.winner_id)
            WHERE r.rn > 1
        """)
        affected_pairs = {(int(row[0]), row[1]) for row in (cursor.fetchall() or [])}
        if affected_pairs:
            # И дни тех, кому эти оценки засчитаны решением проверяющих.
            cursor.execute(f"""
                {ranked_cte}
                SELECT DISTINCT unnest(lr.attributed_operator_ids), lr.day
                FROM ranked r
                JOIN chat_manager_low_rating_reviews lr
                  ON lr.id IN (r.id, r.winner_id)
                WHERE r.rn > 1
                  AND lr.attributed_operator_ids IS NOT NULL
            """)
            affected_pairs |= {
                (int(row[0]), row[1]) for row in (cursor.fetchall() or []) if row[0] is not None
            }

        if affected_pairs:
            # 1. Перенести журнал вердиктов ДО удаления: внешний ключ стоит
            #    с ON DELETE CASCADE и молча унёс бы записи вместе со строкой.
            cursor.execute(f"""
                {ranked_cte}
                UPDATE chat_manager_low_rating_review_entries e
                SET review_id = r.winner_id,
                    updated_at = CURRENT_TIMESTAMP
                FROM ranked r
                WHERE e.review_id = r.id
                  AND r.rn > 1
                  AND NOT EXISTS (
                      SELECT 1
                      FROM chat_manager_low_rating_review_entries kept
                      WHERE kept.review_id = r.winner_id
                        AND kept.reviewer_id = e.reviewer_id
                  )
            """)
            moved_entries = cursor.rowcount or 0

            # 2. Денормализованные вердикты — только в пустые поля выжившей.
            cursor.execute(f"""
                {ranked_cte}
                UPDATE chat_manager_low_rating_reviews w
                SET arai_status = COALESCE(w.arai_status, l.arai_status),
                    arai_comment = CASE WHEN w.arai_comment = '' THEN l.arai_comment ELSE w.arai_comment END,
                    arai_updated_by = COALESCE(w.arai_updated_by, l.arai_updated_by),
                    arai_updated_at = COALESCE(w.arai_updated_at, l.arai_updated_at),
                    zhanna_status = COALESCE(w.zhanna_status, l.zhanna_status),
                    zhanna_comment = CASE WHEN w.zhanna_comment = '' THEN l.zhanna_comment ELSE w.zhanna_comment END,
                    zhanna_updated_by = COALESCE(w.zhanna_updated_by, l.zhanna_updated_by),
                    zhanna_updated_at = COALESCE(w.zhanna_updated_at, l.zhanna_updated_at),
                    head_status = COALESCE(w.head_status, l.head_status),
                    head_comment = CASE WHEN w.head_comment = '' THEN l.head_comment ELSE w.head_comment END,
                    head_updated_by = COALESCE(w.head_updated_by, l.head_updated_by),
                    head_updated_at = COALESCE(w.head_updated_at, l.head_updated_at),
                    final_status = COALESCE(w.final_status, l.final_status),
                    final_source = COALESCE(w.final_source, l.final_source),
                    final_comment = CASE WHEN w.final_comment = '' THEN l.final_comment ELSE w.final_comment END,
                    final_decided_by = COALESCE(w.final_decided_by, l.final_decided_by),
                    final_decided_at = COALESCE(w.final_decided_at, l.final_decided_at),
                    attributed_operator_ids = COALESCE(w.attributed_operator_ids, l.attributed_operator_ids),
                    raw_payload = l.raw_payload || w.raw_payload,
                    updated_at = CURRENT_TIMESTAMP
                FROM ranked r
                JOIN chat_manager_low_rating_reviews l ON l.id = r.id
                WHERE w.id = r.winner_id AND r.rn > 1
            """)

            # 2b. История разбора копии — к выжившей строке: журнал только
            #     пополняется, и каскад молча стёр бы, кто что решал.
            cursor.execute(f"""
                {ranked_cte}
                UPDATE chat_manager_low_rating_review_history h
                SET review_id = r.winner_id
                FROM ranked r
                WHERE h.review_id = r.id
                  AND r.rn > 1
            """)

            # 2a. Вердикт этого же проверяющего у выжившей строки уже есть —
            #     перенести второй нельзя (UNIQUE(review_id, reviewer_id)), он
            #     уйдёт каскадом. Молча терять оценку человека нельзя: пишем в
            #     лог, что именно отбрасываем, и отдельно — где проверяющий
            #     разметил копии по-разному (там решение за ним, не за нами).
            cursor.execute(f"""
                {ranked_cte}
                SELECT lost.reviewer_id, lost.status, kept.status,
                       r.winner_id, victim.day, victim.operator_id
                FROM ranked r
                JOIN chat_manager_low_rating_reviews victim ON victim.id = r.id
                JOIN chat_manager_low_rating_review_entries lost ON lost.review_id = r.id
                JOIN chat_manager_low_rating_review_entries kept
                  ON kept.review_id = r.winner_id AND kept.reviewer_id = lost.reviewer_id
                WHERE r.rn > 1
            """)
            for reviewer_id, lost_status, kept_status, winner_id, day, operator_id in (cursor.fetchall() or []):
                if lost_status == kept_status:
                    continue
                logging.warning(
                    "Низкие оценки Chat2Desk: проверяющий %s разметил копии одного обращения "
                    "по-разному (оставлено «%s», отброшено «%s»); оценка %s, оператор %s, день %s — "
                    "требуется повторное решение",
                    reviewer_id, kept_status, lost_status, winner_id, operator_id, day
                )

            # 3. Удалить сдвинутые копии.
            cursor.execute(f"""
                {ranked_cte}
                DELETE FROM chat_manager_low_rating_reviews victim
                USING ranked r
                WHERE victim.id = r.id AND r.rn > 1
            """)
            removed_rows = cursor.rowcount or 0

            logging.warning(
                "Низкие оценки Chat2Desk: склеено %s дублей, перенесено %s записей вердиктов, "
                "пересчитывается %s пар (оператор, день)",
                removed_rows, moved_entries, len(affected_pairs)
            )

        # 4. Перевести выживших на новый ключ. Старые ключи — hex uuid5, новые
        #    начинаются с 'c2d-rating:', поэтому совпасть со ещё не обновлённой
        #    строкой новый ключ не может и уникальность по ходу не ломается.
        cursor.execute(f"""
            UPDATE chat_manager_low_rating_reviews lr
            SET source_key = LEFT({self._C2D_RATING_KEY_SQL}, 255),
                updated_at = CURRENT_TIMESTAMP
            WHERE {self._C2D_RATING_KEY_SCOPE_SQL}
              AND lr.source_key <> LEFT({self._C2D_RATING_KEY_SQL}, 255)
        """)
        rekeyed = cursor.rowcount or 0
        if rekeyed:
            logging.warning(
                "Низкие оценки Chat2Desk: %s строк переведено на ключ по номеру заявки",
                rekeyed
            )

        if affected_pairs:
            self._recalculate_chat_manager_score_adjustments_tx(cursor, affected_pairs)


    def _init_library_schema_tx(self, cursor):
        """Схема раздела «Библиотека» (задача #282) — своим SAVEPOINT'ом.

        DDL живёт в пакете library/schema.py, как у новостей и посылок; импорт
        локальный, иначе цикл. Не развернулась — раздел отвечает пустым
        каталогом (schema_ready=false), остальное приложение стартует штатно.
        """
        import logging

        cursor.execute("SAVEPOINT library_schema")
        try:
            from library.schema import init_library_schema
            init_library_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT library_schema")
            logging.exception(
                "Схема раздела «Библиотека» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT library_schema")

    def _init_water_schema_tx(self, cursor):
        """Схема раздела «Учёт воды» — своим SAVEPOINT'ом.

        DDL живёт в пакете water/schema.py, как у посылок; импорт локальный,
        иначе цикл. Стоит ПОСЛЕ _init_wiki_schema_tx: `water_offices.office_id`
        — внешний ключ на `wiki_offices`. Не развернулась — раздел честно
        скажет о себе через /api/water/ping (schema_ready=false), остальное
        приложение стартует штатно.
        """
        import logging

        cursor.execute("SAVEPOINT water_schema")
        try:
            from water.schema import init_water_schema
            init_water_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT water_schema")
            logging.exception(
                "Схема раздела «Учёт воды» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT water_schema")

    def _init_thermoboxes_schema_tx(self, cursor):
        """Схема раздела «Термокороба» (#363) — своим SAVEPOINT'ом.

        DDL живёт в пакете thermoboxes/schema.py, как у посылок и воды; импорт
        локальный, иначе цикл. Стоит ПОСЛЕ _init_wiki_schema_tx:
        `thermobox_offices.office_id` — внешний ключ на `wiki_offices`. Не
        развернулась — раздел честно скажет о себе (schema_ready=false),
        остальное приложение стартует штатно.
        """
        import logging

        cursor.execute("SAVEPOINT thermoboxes_schema")
        try:
            from thermoboxes.schema import init_thermoboxes_schema
            init_thermoboxes_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT thermoboxes_schema")
            logging.exception(
                "Схема раздела «Термокороба» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT thermoboxes_schema")

    def _init_honor_board_schema_tx(self, cursor):
        """Схема «Доски почёта» во вкладке «Ивентов» (#318) — своим SAVEPOINT'ом.

        DDL живёт в пакете honor_board/schema.py, как у библиотеки; импорт
        локальный, иначе цикл. Не развернулась — доска отвечает пустой
        (schema_ready=false), остальное приложение стартует штатно.
        """
        import logging

        cursor.execute("SAVEPOINT honor_board_schema")
        try:
            from honor_board.schema import init_honor_board_schema
            init_honor_board_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT honor_board_schema")
            logging.exception(
                "Схема «Доски почёта» не применилась — доска будет недоступна, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT honor_board_schema")

    def _init_twofa_schema_tx(self, cursor):
        """Схема раздела «Коды 2FA» (#373) — своим SAVEPOINT'ом.

        DDL живёт в пакете twofa/schema.py, как у «Термокоробов»; импорт локальный, иначе
        цикл. Стоит ДО триггеров колокола: trg_bell_twofa_requests вешается на
        twofa_requests. Не развернулась — раздел недоступен, остальное работает штатно.
        """
        import logging

        cursor.execute("SAVEPOINT twofa_schema")
        try:
            from twofa.schema import init_twofa_schema
            init_twofa_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT twofa_schema")
            logging.exception(
                "Схема раздела «Коды 2FA» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT twofa_schema")

    def _init_rental_schema_tx(self, cursor):
        """Схема раздела «Аренда авто» (ночной отчёт #398) — своим SAVEPOINT'ом.

        DDL живёт в пакете rental/schema.py, как у «Кодов 2FA»; импорт локальный, иначе
        цикл. Не развернулась — раздел недоступен, остальное работает штатно.
        """
        import logging

        cursor.execute("SAVEPOINT rental_schema")
        try:
            from rental.schema import init_rental_schema
            init_rental_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT rental_schema")
            logging.exception(
                "Схема раздела «Аренда авто» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT rental_schema")

    def _init_hiring_schema_tx(self, cursor):
        """Схема раздела «Воронка найма» (#404) — своим SAVEPOINT'ом, как у «Аренды авто».

        DDL живёт в пакете hiring/schema.py; импорт локальный, иначе цикл. Не развернулась —
        раздел недоступен, остальное приложение работает штатно.
        """
        import logging

        cursor.execute("SAVEPOINT hiring_schema")
        try:
            from hiring.schema import init_hiring_schema
            init_hiring_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT hiring_schema")
            logging.exception(
                "Схема раздела «Воронка найма» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT hiring_schema")

    def _init_baiga_schema_tx(self, cursor):
        """Схема раздела «Списки Байги» (#356) — своим SAVEPOINT'ом.

        DDL живёт в пакете baiga/schema.py, как у посылок и термокоробов; импорт
        локальный, иначе цикл. Не развернулась — раздел честно скажет о себе
        (schema_ready=false), остальное приложение стартует штатно.
        """
        import logging

        cursor.execute("SAVEPOINT baiga_schema")
        try:
            from baiga.schema import init_baiga_schema
            init_baiga_schema(cursor)
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT baiga_schema")
            logging.exception(
                "Схема раздела «Списки Байги» не применилась — раздел будет недоступен, "
                "остальное приложение работает штатно"
            )
        else:
            cursor.execute("RELEASE SAVEPOINT baiga_schema")

    def _init_test_numbers_schema_tx(self, cursor):
        """Схема «Реестра тестовых номеров» — БЕЗ своего SAVEPOINT, намеренно.

        Таблица реестра стоит в условиях исключения десятков расчётов (табло,
        отчёты, ИИ-оценка — test_numbers/keys.py: sql_not_test). Не развернись она
        тихо, как раздел, — упали бы все эти запросы сразу. Пусть лучше не
        стартует новая версия: старая продолжит работать, а ошибка будет видна в
        логе деплоя. DDL простой и зависит только от `users`.
        """
        from test_numbers.schema import init_test_numbers_schema
        init_test_numbers_schema(cursor)
