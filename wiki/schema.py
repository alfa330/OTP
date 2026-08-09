"""Схема раздела «Вики». Все таблицы с префиксом wiki_.

Идемпотентно: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
Вызывается один раз при старте из Database._init_db через init_wiki_schema(cursor).

Почему префикс. В базе OTP ~191 таблица; имена articles/sections/spaces/positions/
employees/categories/notifications формально свободны, но слишком общие для монолита —
через полгода никто не вспомнит, чьи они. Префикс снимает вопрос навсегда.

Чего здесь СОЗНАТЕЛЬНО нет (в отличие от оригинальной вики):
  * employees / positions — их роль играет сам users (там уже email, department_id,
    direction_id, supervisor_id), а иерархия должностей заменена на ROLE_HIERARCHY,
    членство в группах и возглавляемые отделы;
  * триггеров на departments — в оригинале они плодили пространства при любой
    вставке отдела, что в общей базе недопустимо;
  * article_changes_log — вторая история статьи рядом с версиями, ни одна из двух
    не источник правды. Оставлены только версии;
  * таблиц новостей — в проде вики все 7 пусты, лента будет общая (см. этап 10).

Чего здесь есть СВЕРХ оригинала (требование владельца):
  * право can_delete — в оригинале его нет вообще, удаление гейтится только ролью;
  * wiki_article_access_rules — права на уровне отдельной статьи, с grant/deny;
  * wiki_articles.visibility_mode (inherit|restricted) и strict_mode.
"""

# Роли OTP, на которые можно адресовать правило (совпадает с CHECK на users.role).
OTP_ROLES = (
    'super_admin', 'admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee',
)

# Шесть прав. В оригинальной вики их пять — can_delete добавлено нами.
PERMISSION_COLUMNS = (
    'can_read', 'can_create', 'can_edit', 'can_delete', 'can_publish', 'can_approve',
)

# Способности уровня раздела (глобальные, не привязаны к конкретному разделу вики).
CAPABILITY_COLUMNS = (
    'can_read', 'can_create', 'can_edit', 'can_delete', 'can_publish', 'can_approve',
    'can_manage_users', 'can_manage_structure', 'can_manage_access',
)

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_STATEMENTS = [

    # ──────────────────────────────────────────────────────────────────────
    # РОЛИ ВИКИ
    # Роль OTP (users.role) — одна строка из CHECK-энума, её мало: человек может
    # быть одновременно читателем одного пространства и владельцем процесса
    # в другом. Поэтому роли вики — отдельная связь многие-ко-многим.
    # Если у пользователя нет ни одной wiki-роли, способности выводятся из
    # users.role (см. wiki/access.py: capabilities_from_otp_role).
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS wiki_roles (
        id                   SERIAL PRIMARY KEY,
        code                 VARCHAR(80) NOT NULL UNIQUE,
        name                 VARCHAR(255) NOT NULL,
        description          TEXT,
        can_read             BOOLEAN NOT NULL DEFAULT FALSE,
        can_create           BOOLEAN NOT NULL DEFAULT FALSE,
        can_edit             BOOLEAN NOT NULL DEFAULT FALSE,
        can_delete           BOOLEAN NOT NULL DEFAULT FALSE,
        can_publish          BOOLEAN NOT NULL DEFAULT FALSE,
        can_approve          BOOLEAN NOT NULL DEFAULT FALSE,
        can_manage_users     BOOLEAN NOT NULL DEFAULT FALSE,
        can_manage_structure BOOLEAN NOT NULL DEFAULT FALSE,
        can_manage_access    BOOLEAN NOT NULL DEFAULT FALSE,
        is_system            BOOLEAN NOT NULL DEFAULT FALSE,
        created_at           TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at           TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS wiki_user_roles (
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        wiki_role_id INTEGER NOT NULL REFERENCES wiki_roles(id) ON DELETE CASCADE,
        assigned_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        assigned_at  TIMESTAMP NOT NULL DEFAULT %(now)s,
        PRIMARY KEY (user_id, wiki_role_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_user_roles_role ON wiki_user_roles(wiki_role_id);",

    # ──────────────────────────────────────────────────────────────────────
    # СТРУКТУРА: пространство → раздел (дерево) → статья
    # department_id у пространства НЕОБЯЗАТЕЛЕН и БЕЗ UNIQUE: структура вики не
    # обязана быть зеркалом оргструктуры (в оригинале была, и это мешало).
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS wiki_spaces (
        id            SERIAL PRIMARY KEY,
        code          VARCHAR(80) UNIQUE,
        name          VARCHAR(255) NOT NULL,
        description   TEXT,
        icon          VARCHAR(64),
        department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        status        VARCHAR(16) NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'archived')),
        position      INTEGER NOT NULL DEFAULT 0,
        created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at    TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at    TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_spaces_order ON wiki_spaces(status, position, id);",

    """
    CREATE TABLE IF NOT EXISTS wiki_sections (
        id                SERIAL PRIMARY KEY,
        space_id          INTEGER NOT NULL REFERENCES wiki_spaces(id) ON DELETE CASCADE,
        parent_section_id INTEGER REFERENCES wiki_sections(id) ON DELETE SET NULL,
        name              VARCHAR(255) NOT NULL,
        slug              VARCHAR(255) NOT NULL,
        description       TEXT,
        icon              VARCHAR(64),
        -- 'public' = раздел виден любому сотруднику без правил.
        -- В оригинале это поле проставлялось только сидом по эвристике LIKE '%общ%'
        -- и не имело ни API, ни UI. У нас — редактируемое поле.
        visibility_scope  VARCHAR(16) NOT NULL DEFAULT 'restricted'
                          CHECK (visibility_scope IN ('restricted', 'public')),
        owner_user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        status            VARCHAR(16) NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'archived')),
        position          INTEGER NOT NULL DEFAULT 0,
        created_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at        TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at        TIMESTAMP NOT NULL DEFAULT %(now)s,
        UNIQUE (space_id, slug)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_sections_space ON wiki_sections(space_id, position, id);",
    "CREATE INDEX IF NOT EXISTS idx_wiki_sections_parent ON wiki_sections(parent_section_id);",
    "CREATE INDEX IF NOT EXISTS idx_wiki_sections_public ON wiki_sections(visibility_scope) WHERE status = 'active';",

    # ──────────────────────────────────────────────────────────────────────
    # СТАТЬИ
    # visibility_mode='restricted' — права берутся ТОЛЬКО из правил статьи,
    # разделы игнорируются. Это и есть «ручная настройка» на уровне статьи.
    # strict_mode=TRUE — даже администратору вики нужен явный грант; обходит
    # только super_admin, и каждый обход пишется в wiki_audit_log.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS wiki_articles (
        id                SERIAL PRIMARY KEY,
        slug              VARCHAR(255) NOT NULL UNIQUE,
        title             VARCHAR(255) NOT NULL,
        summary           TEXT,
        content           TEXT NOT NULL DEFAULT '',
        content_plain     TEXT NOT NULL DEFAULT '',
        search_aliases    TEXT NOT NULL DEFAULT '',
        article_type      VARCHAR(32) NOT NULL DEFAULT 'general'
                          CHECK (article_type IN ('general', 'job_description', 'regulation',
                                                  'instruction', 'tool_description')),
        status            VARCHAR(32) NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft', 'on_approval', 'published',
                                            'requires_verification', 'archived', 'expired')),
        visibility_mode   VARCHAR(16) NOT NULL DEFAULT 'inherit'
                          CHECK (visibility_mode IN ('inherit', 'restricted')),
        strict_mode       BOOLEAN NOT NULL DEFAULT FALSE,
        toc               JSONB NOT NULL DEFAULT '[]'::jsonb,
        author_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        owner_user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        related_course_id INTEGER REFERENCES lms_courses(id) ON DELETE SET NULL,
        views             INTEGER NOT NULL DEFAULT 0,
        position          INTEGER NOT NULL DEFAULT 0,
        review_due_at     TIMESTAMP,
        published_at      TIMESTAMP,
        created_at        TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at        TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_articles_status ON wiki_articles(status);",
    "CREATE INDEX IF NOT EXISTS idx_wiki_articles_author ON wiki_articles(author_id);",
    "CREATE INDEX IF NOT EXISTS idx_wiki_articles_restricted ON wiki_articles(visibility_mode) WHERE visibility_mode = 'restricted';",

    # ──────────────────────────────────────────────────────────────────────
    # ПРАВИЛА ДОСТУПА К РАЗДЕЛУ
    # Полиморфный субъект (subject_type, subject_id) вместо трёх nullable-колонок
    # оригинала: у нас нет справочника должностей, зато есть отделы, направления,
    # группы, роли OTP, роли вики и конкретные люди — одна пара покрывает всё.
    # subject_role используется только при subject_type='otp_role' (там строка,
    # а не id), поэтому CHECK следит, чтобы заполнено было ровно одно из двух.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS wiki_section_access_rules (
        id                SERIAL PRIMARY KEY,
        section_id        INTEGER NOT NULL REFERENCES wiki_sections(id) ON DELETE CASCADE,
        subject_type      VARCHAR(20) NOT NULL
                          CHECK (subject_type IN ('department', 'direction', 'group',
                                                  'otp_role', 'wiki_role', 'user')),
        subject_id        INTEGER,
        subject_role      VARCHAR(20)
                          CHECK (subject_role IS NULL OR subject_role IN
                                 ('super_admin', 'admin', 'sv', 'supervisor',
                                  'trainer', 'operator', 'trainee')),
        can_read          BOOLEAN NOT NULL DEFAULT TRUE,
        can_create        BOOLEAN NOT NULL DEFAULT FALSE,
        can_edit          BOOLEAN NOT NULL DEFAULT FALSE,
        can_delete        BOOLEAN NOT NULL DEFAULT FALSE,
        can_publish       BOOLEAN NOT NULL DEFAULT FALSE,
        can_approve       BOOLEAN NOT NULL DEFAULT FALSE,
        grant_subsections BOOLEAN NOT NULL DEFAULT TRUE,
        created_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at        TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at        TIMESTAMP NOT NULL DEFAULT %(now)s,
        CHECK (
            (subject_type = 'otp_role' AND subject_role IS NOT NULL AND subject_id IS NULL)
            OR
            (subject_type <> 'otp_role' AND subject_id IS NOT NULL AND subject_role IS NULL)
        )
    );
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_wiki_section_rule_subject
        ON wiki_section_access_rules(section_id, subject_type,
                                     COALESCE(subject_id, -1), COALESCE(subject_role, ''));
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_section_rules_subject ON wiki_section_access_rules(subject_type, subject_id);",

    # ──────────────────────────────────────────────────────────────────────
    # ПРАВИЛА ДОСТУПА К ОТДЕЛЬНОЙ СТАТЬЕ — этого в оригинальной вике нет вовсе.
    # mode='deny' всегда сильнее любого grant: без этого нельзя выразить
    # «скрыть от одного человека внутри разрешённого отдела».
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS wiki_article_access_rules (
        id           SERIAL PRIMARY KEY,
        article_id   INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
        subject_type VARCHAR(20) NOT NULL
                     CHECK (subject_type IN ('department', 'direction', 'group',
                                             'otp_role', 'wiki_role', 'user')),
        subject_id   INTEGER,
        subject_role VARCHAR(20)
                     CHECK (subject_role IS NULL OR subject_role IN
                            ('super_admin', 'admin', 'sv', 'supervisor',
                             'trainer', 'operator', 'trainee')),
        mode         VARCHAR(8) NOT NULL DEFAULT 'grant'
                     CHECK (mode IN ('grant', 'deny')),
        can_read     BOOLEAN NOT NULL DEFAULT TRUE,
        can_create   BOOLEAN NOT NULL DEFAULT FALSE,
        can_edit     BOOLEAN NOT NULL DEFAULT FALSE,
        can_delete   BOOLEAN NOT NULL DEFAULT FALSE,
        can_publish  BOOLEAN NOT NULL DEFAULT FALSE,
        can_approve  BOOLEAN NOT NULL DEFAULT FALSE,
        created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at   TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at   TIMESTAMP NOT NULL DEFAULT %(now)s,
        CHECK (
            (subject_type = 'otp_role' AND subject_role IS NOT NULL AND subject_id IS NULL)
            OR
            (subject_type <> 'otp_role' AND subject_id IS NOT NULL AND subject_role IS NULL)
        )
    );
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_wiki_article_rule_subject
        ON wiki_article_access_rules(article_id, subject_type,
                                     COALESCE(subject_id, -1), COALESCE(subject_role, ''));
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_article_rules_subject ON wiki_article_access_rules(subject_type, subject_id);",

    # ──────────────────────────────────────────────────────────────────────
    # РУЧНОЙ РЕЖИМ ДОСТУПА
    # access_mode='manual' отключает для человека все автоматические правила:
    # он видит ровно то, что ему выдали руками, плюс публичные разделы.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS wiki_user_access_settings (
        user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        access_mode VARCHAR(10) NOT NULL DEFAULT 'auto'
                    CHECK (access_mode IN ('auto', 'manual')),
        updated_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at  TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS wiki_user_manual_access (
        id            SERIAL PRIMARY KEY,
        user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id INTEGER REFERENCES departments(id) ON DELETE CASCADE,
        section_id    INTEGER REFERENCES wiki_sections(id) ON DELETE CASCADE,
        created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at    TIMESTAMP NOT NULL DEFAULT %(now)s,
        CHECK (
            (department_id IS NOT NULL AND section_id IS NULL)
            OR
            (department_id IS NULL AND section_id IS NOT NULL)
        )
    );
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_wiki_manual_dept
        ON wiki_user_manual_access(user_id, department_id) WHERE department_id IS NOT NULL;
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_wiki_manual_section
        ON wiki_user_manual_access(user_id, section_id) WHERE section_id IS NOT NULL;
    """,

    # ──────────────────────────────────────────────────────────────────────
    # ГОСТЕВОЙ ДОСТУП
    # В оригинале отзыв был физическим DELETE (история терялась), не было ни одного
    # индекса, и список отдавал уже истёкшие выдачи. Здесь revoked_at + индекс.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS wiki_guest_access (
        id            SERIAL PRIMARY KEY,
        user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        article_id    INTEGER REFERENCES wiki_articles(id) ON DELETE CASCADE,
        section_id    INTEGER REFERENCES wiki_sections(id) ON DELETE CASCADE,
        granted_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        reason        TEXT,
        expires_at    TIMESTAMP NOT NULL,
        revoked_at    TIMESTAMP,
        revoked_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at    TIMESTAMP NOT NULL DEFAULT %(now)s,
        CHECK (
            (article_id IS NOT NULL AND section_id IS NULL)
            OR
            (article_id IS NULL AND section_id IS NOT NULL)
        )
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_wiki_guest_active
        ON wiki_guest_access(user_id, expires_at) WHERE revoked_at IS NULL;
    """,

    # Статья принадлежит одному или нескольким разделам (M:N). Основа фильтра
    # доступа в режиме inherit: статья видна, если разрешён хотя бы один её раздел.
    """
    CREATE TABLE IF NOT EXISTS wiki_article_sections (
        article_id INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
        section_id INTEGER NOT NULL REFERENCES wiki_sections(id) ON DELETE CASCADE,
        PRIMARY KEY (article_id, section_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_article_sections_section ON wiki_article_sections(section_id);",

    """
    CREATE TABLE IF NOT EXISTS wiki_article_tags (
        article_id INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
        tag_name   VARCHAR(64) NOT NULL,
        PRIMARY KEY (article_id, tag_name)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_article_tags_name ON wiki_article_tags(tag_name);",

    # Версии. session_id — UUID, ссылается на user_sessions(session_id):
    # в оригинале это был INT со ссылкой на несуществующий у нас user_sessions(id),
    # а значение искалось лукапом по refresh-токену. У нас sid лежит прямо в
    # access-токене (_current_session_id_from_access_token).
    """
    CREATE TABLE IF NOT EXISTS wiki_article_versions (
        id                     SERIAL PRIMARY KEY,
        article_id             INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
        version_number         INTEGER NOT NULL,
        title                  VARCHAR(255) NOT NULL,
        summary                TEXT,
        content                TEXT NOT NULL DEFAULT '',
        status                 VARCHAR(32),
        change_comment         TEXT,
        editor_id              INTEGER REFERENCES users(id) ON DELETE SET NULL,
        session_id             UUID REFERENCES user_sessions(session_id) ON DELETE SET NULL,
        restored_from_version_id INTEGER REFERENCES wiki_article_versions(id) ON DELETE SET NULL,
        created_at             TIMESTAMP NOT NULL DEFAULT %(now)s,
        UNIQUE (article_id, version_number)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_versions_article ON wiki_article_versions(article_id, version_number DESC);",

    # Просмотры. Инкремент wiki_articles.views и запись сюда идут ОДНОЙ транзакцией —
    # в оригинале это два запроса без транзакции, и счётчики расходились.
    """
    CREATE TABLE IF NOT EXISTS wiki_article_views_log (
        id         BIGSERIAL PRIMARY KEY,
        article_id INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
        user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        ip_address VARCHAR(45),
        viewed_at  TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_views_article ON wiki_article_views_log(article_id, viewed_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_wiki_views_user ON wiki_article_views_log(user_id, viewed_at DESC);",

    """
    CREATE TABLE IF NOT EXISTS wiki_user_reading_history (
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        article_id INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
        viewed_at  TIMESTAMP NOT NULL DEFAULT %(now)s,
        PRIMARY KEY (user_id, article_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_reading_recent ON wiki_user_reading_history(user_id, viewed_at DESC);",

    """
    CREATE TABLE IF NOT EXISTS wiki_user_favorite_articles (
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        article_id   INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
        position     INTEGER NOT NULL DEFAULT 0,
        favorited_at TIMESTAMP NOT NULL DEFAULT %(now)s,
        PRIMARY KEY (user_id, article_id)
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS wiki_article_links (
        id              SERIAL PRIMARY KEY,
        source_id       INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
        target_id       INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
        is_manual       BOOLEAN NOT NULL DEFAULT FALSE,
        created_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at      TIMESTAMP NOT NULL DEFAULT %(now)s,
        UNIQUE (source_id, target_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_links_target ON wiki_article_links(target_id);",

    # ──────────────────────────────────────────────────────────────────────
    # ОБЯЗАТЕЛЬНОЕ ОЗНАКОМЛЕНИЕ
    # Ключ включает версию статьи: при выходе новой версии назначение
    # перевыпускается, старое остаётся в истории. Три отдельные метки времени —
    # «открыл», «дочитал», «подтвердил»: это разные события, и для формального
    # документооборота их надо различать.
    # Снимки отдела/группы/роли/супервайзера на момент назначения — чтобы отчёт
    # через год показывал, кем человек был тогда, а не кем стал.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS wiki_ack_assignments (
        id                 SERIAL PRIMARY KEY,
        article_id         INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
        article_version    INTEGER NOT NULL,
        user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        assigned_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        due_at             TIMESTAMP,
        first_viewed_at    TIMESTAMP,
        read_completed_at  TIMESTAMP,
        acknowledged_at    TIMESTAMP,
        blocks_total       INTEGER NOT NULL DEFAULT 0,
        blocks_opened      INTEGER NOT NULL DEFAULT 0,
        completed_in_time  BOOLEAN,
        overdue_days       INTEGER,
        status             VARCHAR(32) NOT NULL DEFAULT 'not_open'
                           CHECK (status IN ('not_open', 'in_progress', 'read_completed',
                                             'acknowledged', 'overdue',
                                             'requires_reacknowledgement', 'cancelled',
                                             'superseded')),
        snapshot_department_id   INTEGER,
        snapshot_department_name VARCHAR(255),
        snapshot_group_id        INTEGER,
        snapshot_group_name      VARCHAR(255),
        snapshot_role            VARCHAR(20),
        snapshot_supervisor_id   INTEGER,
        snapshot_supervisor_name VARCHAR(255),
        created_at         TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at         TIMESTAMP NOT NULL DEFAULT %(now)s,
        UNIQUE (article_id, article_version, user_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_ack_user ON wiki_ack_assignments(user_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_wiki_ack_article ON wiki_ack_assignments(article_id, status);",

    # ──────────────────────────────────────────────────────────────────────
    # ФАЙЛЫ
    # Хранилище — GCS, как у LMS/Ивентов/аватарок. В БД только bucket+blob_path.
    # Отдаём НЕ signed URL напрямую в HTML (у LMS так, и ссылки протухают через
    # 240 минут), а через стабильный прокси /api/wiki/file/<id>, который проверяет
    # доступ и отдаёт 302 на свежий signed URL.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS wiki_files (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        article_id    INTEGER REFERENCES wiki_articles(id) ON DELETE SET NULL,
        bucket        VARCHAR(255) NOT NULL,
        blob_path     TEXT NOT NULL,
        original_name VARCHAR(255) NOT NULL,
        content_type  VARCHAR(100),
        file_size     BIGINT NOT NULL DEFAULT 0,
        width         INTEGER,
        height        INTEGER,
        uploaded_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at    TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_files_article ON wiki_files(article_id);",

    # ──────────────────────────────────────────────────────────────────────
    # ЖУРНАЛ
    # Одна таблица вместо двух почти одинаковых (security_audit_logs и
    # access_audit_logs в оригинале). Сюда же пишутся обходы strict_mode.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS wiki_audit_log (
        id          BIGSERIAL PRIMARY KEY,
        actor_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        action      VARCHAR(64) NOT NULL,
        entity_type VARCHAR(32),
        entity_id   INTEGER,
        target_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        details     JSONB NOT NULL DEFAULT '{}'::jsonb,
        ip_address  VARCHAR(45),
        created_at  TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_audit_created ON wiki_audit_log(created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_wiki_audit_entity ON wiki_audit_log(entity_type, entity_id);",
    "CREATE INDEX IF NOT EXISTS idx_wiki_audit_actor ON wiki_audit_log(actor_id, created_at DESC);",
]

# Сид системных ролей. ON CONFLICT DO NOTHING, а НЕ DO UPDATE: в оригинале сид
# при каждом рестарте затирал права, отредактированные через интерфейс.
_SEED_ROLES = [
    # code,            name,                        read,  create, edit,  delete, publish, approve, users, structure, access
    ('reader',         'Читатель',                  True,  False, False, False, False, False, False, False, False),
    ('editor',         'Редактор',                  True,  True,  True,  False, False, False, False, False, False),
    ('process_owner',  'Владелец процесса',         True,  True,  True,  True,  True,  False, False, False, False),
    ('approver',       'Согласующий',               True,  False, False, False, False, True,  False, False, False),
    ('wiki_admin',     'Администратор вики',        True,  True,  True,  True,  True,  True,  True,  True,  True),
]


# ─────────────────────────────────────────────────────────────────────────────
# Таксопарки и акции
#
# Автономная фича на три таблицы: к статьям она не привязана и живёт своей
# вкладкой. В проде вики её содержимым не пользовались (16 парков — ровно
# захардкоженный сид, акций ноль), поэтому переносим механику, а не данные:
# заполнять начнут заново.
#
# Публичных эндпоинтов, в отличие от оригинала, не будет: там GET по паркам и
# акциям отдавались без авторизации вообще.
# ─────────────────────────────────────────────────────────────────────────────
_PARK_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS wiki_taxi_parks (
        id            SERIAL PRIMARY KEY,
        slug          VARCHAR(120) NOT NULL UNIQUE,
        name          VARCHAR(255) NOT NULL,
        description   TEXT,
        city          VARCHAR(120),
        phone         VARCHAR(64),
        address       TEXT,
        website       TEXT,
        commission    NUMERIC(5,2),
        logo_file_id  UUID REFERENCES wiki_files(id) ON DELETE SET NULL,
        status        VARCHAR(16) NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'archived')),
        position      INTEGER NOT NULL DEFAULT 0,
        created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at    TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at    TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_parks_order ON wiki_taxi_parks(status, position, id);",
    """
    CREATE TABLE IF NOT EXISTS wiki_promotions (
        id            SERIAL PRIMARY KEY,
        title         VARCHAR(255) NOT NULL,
        description   TEXT,
        content       TEXT NOT NULL DEFAULT '',
        banner_file_id UUID REFERENCES wiki_files(id) ON DELETE SET NULL,
        starts_at     TIMESTAMP,
        ends_at       TIMESTAMP,
        status        VARCHAR(16) NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'archived')),
        created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at    TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at    TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_promotions_period ON wiki_promotions(status, ends_at);",
    """
    CREATE TABLE IF NOT EXISTS wiki_promotion_taxi_parks (
        promotion_id INTEGER NOT NULL REFERENCES wiki_promotions(id) ON DELETE CASCADE,
        park_id      INTEGER NOT NULL REFERENCES wiki_taxi_parks(id) ON DELETE CASCADE,
        PRIMARY KEY (promotion_id, park_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_promo_parks_park ON wiki_promotion_taxi_parks(park_id);",
]


# ─────────────────────────────────────────────────────────────────────────────
# Поиск
# ─────────────────────────────────────────────────────────────────────────────

# Генерируемая колонка вместо триггера: значение не может разъехаться с текстом
# в принципе. Это структурно снимает баг оригинала, где массовая переиндексация
# заливала в поисковый движок пустое тело статьи (models/article.ts: '' as content),
# и после каждого рестарта поиск работал только через ILIKE-фолбэк.
#
# Веса: заголовок > алиасы > описание > текст. В Meilisearch приоритетов было
# девять, в Postgres их четыре (A-D) — разница есть, но алиасы забирают на себя
# ровно тот слой, ради которого в оригинале держали отдельные searchable-поля.
#
# translate(ё -> е) на каждом источнике: конфигурация 'russian' НЕ склеивает
# ё и е, поэтому без свёртки запрос «отчет» не находил «отчёт» в теле статьи.
# Запрос сворачивается так же (wiki/search.py) — обе стороны согласованы.
_SEARCH_STATEMENTS = [
    """
    ALTER TABLE wiki_articles ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('russian', translate(coalesce(title, ''), 'ёЁ', 'еЕ')),          'A') ||
            setweight(to_tsvector('russian', translate(coalesce(search_aliases, ''), 'ёЁ', 'еЕ')), 'B') ||
            setweight(to_tsvector('russian', translate(coalesce(summary, ''), 'ёЁ', 'еЕ')),        'C') ||
            setweight(to_tsvector('russian', translate(coalesce(content_plain, ''), 'ёЁ', 'еЕ')),  'D')
        ) STORED;
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_articles_fts ON wiki_articles USING GIN (search_vector);",
]

# Опечатки и префиксный поиск. Отдельно и под своим савпоинтом: расширение
# требует прав, и если их нет — поиск обязан продолжить работать на одном FTS,
# а не утащить за собой всю схему раздела.
# Триграммных ИНДЕКСОВ здесь намеренно нет, хотя расширение нужно.
#
# Проверено на боевой базе: планировщик не берёт GIN gin_trgm_ops ни для
# word_similarity(q, lower(title)) >= 0.45, ни для оператора %> — Seq Scan
# остаётся даже при enable_seqscan = off. Причина в форме условия: индексируемая
# сторона у word_similarity/%> — «стог сена», и он стоит СПРАВА, а индексному
# скану нужно индексируемое выражение слева. Индексы, заведённые под этот поиск,
# не использовались ни разу и стоили только записи; их сносим.
#
# Когда возвращаться к вопросу: если статей станет тысячи и Seq Scan по
# заголовкам перестанет укладываться в десятки миллисекунд. Тогда предикат надо
# переписать на индексируемую форму (similarity через оператор % с
# pg_trgm.similarity_threshold) и завести индекс уже под неё.
_TRIGRAM_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm;",
]

# Индексы, заведённые под триграммный поиск и не использованные ни разу.
_DEAD_TRIGRAM_INDEXES = (
    'idx_wiki_articles_title_trgm',
    'idx_wiki_articles_aliases_trgm',
)


def init_wiki_schema(cursor):
    """Создаёт/дополняет схему раздела «Вики». Идемпотентно.

    Принимает ЧУЖОЙ курсор (из Database._get_cursor), чтобы вся инициализация
    базы шла одной транзакцией, как у остальных подсистем проекта.
    """
    for statement in _STATEMENTS:
        # Подстановка через str.replace, а НЕ через %-форматирование: в SQL есть
        # комментарии вида LIKE '%общ%', и любой %-формат на них падает
        # (ValueError: unsupported format character). psycopg2 интерполяцию тоже
        # не делает — второй аргумент execute не передаётся.
        cursor.execute(statement.replace('%(now)s', _NOW))

    for row in _SEED_ROLES:
        cursor.execute(
            """
            INSERT INTO wiki_roles (code, name, can_read, can_create, can_edit, can_delete,
                                    can_publish, can_approve, can_manage_users,
                                    can_manage_structure, can_manage_access, is_system)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (code) DO NOTHING
            """,
            row,
        )

    for statement in _PARK_STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW))

    # Выражение генерируемой колонки менять через ALTER нельзя — только
    # пересоздать. Ловушка «ADD COLUMN IF NOT EXISTS молча оставляет старое
    # определение» обходится явной проверкой выражения: старая версия без
    # свёртки ё удаляется, и следующий блок создаёт колонку заново (35 строк
    # на проде — пересчёт мгновенный).
    cursor.execute(
        """
        SELECT pg_get_expr(d.adbin, d.adrelid)
          FROM pg_attribute a
          JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
         WHERE a.attrelid = 'wiki_articles'::regclass
           AND a.attname = 'search_vector' AND NOT a.attisdropped
        """
    )
    row = cursor.fetchone()
    rebuilt_search_vector = bool(row and row[0] and 'translate' not in row[0])
    if rebuilt_search_vector:
        cursor.execute('ALTER TABLE wiki_articles DROP COLUMN search_vector')

    for statement in _SEARCH_STATEMENTS:
        cursor.execute(statement)

    # Вместе с колонкой пересчитываются и search_aliases: нормализация запроса
    # (ё -> е кириллическая) изменилась, а сохранённые алиасы считались старой.
    if rebuilt_search_vector:
        from .search import refresh_aliases
        cursor.execute('SELECT id FROM wiki_articles')
        for (article_id,) in cursor.fetchall():
            refresh_aliases(cursor, article_id)

    # Триграммы — под собственным савпоинтом. Расширение может быть недоступно
    # по правам; тогда поиск остаётся полнотекстовым (без опечаток), а схема
    # раздела не откатывается целиком.
    cursor.execute('SAVEPOINT wiki_trgm')
    try:
        for index_name in _DEAD_TRIGRAM_INDEXES:
            cursor.execute('DROP INDEX IF EXISTS ' + index_name)
        for statement in _TRIGRAM_STATEMENTS:
            cursor.execute(statement)
    except Exception:
        cursor.execute('ROLLBACK TO SAVEPOINT wiki_trgm')
        import logging
        logging.warning(
            'Раздел «Вики»: pg_trgm недоступен — поиск будет без опечаток '
            '(полнотекстовый работает)'
        )
    else:
        cursor.execute('RELEASE SAVEPOINT wiki_trgm')


def trigram_available(cursor):
    """Установлено ли pg_trgm — от этого зависит форма поискового запроса."""
    try:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        return cursor.fetchone() is not None
    except Exception:
        return False
