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

# Субъекты, на которые можно адресовать правило доступа. Один источник правды:
# CHECK в схеме, валидация в роутах и подстановка в SQL обязаны идти отсюда.
# 'department_head' — не роль и не человек, а НАЗНАЧЕНИЕ главой отдела
# (department_head_assignments). Правило переезжает вместе с назначением, поэтому
# смена главы не требует переставлять права руками, а у отдела с двумя главами
# (на проде это «Отдел продаж») правило одно на обоих.
SUBJECT_TYPES = (
    'department', 'department_head', 'direction', 'group',
    'otp_role', 'wiki_role', 'user',
)

# Тип раздела в дереве. 'department' — ветка конкретного отдела внутри
# пространства (ОП / ОТП у «Коммерческого отдела»); 'common' — всё остальное.
SECTION_KINDS = ('common', 'department')

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
        -- Второе измерение правила: «отдел И не ниже такой-то должности».
        -- Здесь, а не только миграцией ниже: на него опирается уникальный
        -- индекс, который создаётся следующим же оператором.
        min_role_level    INTEGER,
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
    # Для БАЗ, созданных до появления уровня: колонка нужна раньше индекса,
    # а ALTER в _ORG_STATEMENTS отработает уже вхолостую.
    "ALTER TABLE wiki_section_access_rules ADD COLUMN IF NOT EXISTS min_role_level INTEGER;",
    # Уровень должности входит в ключ уникальности С САМОГО НАЧАЛА, а не
    # добавляется миграцией ниже. Раньше здесь создавался ключ БЕЗ уровня, а
    # _ORG_STATEMENTS его дропал и ставил правильный. Пока на разделе не
    # появлялось двух правил с одним субъектом и разными порогами, это молчало;
    # стоило появиться (отдел СЗоВ «без порога» + он же «от тренера»), как
    # CREATE INDEX начинал падать UniqueViolation на КАЖДОМ старте — а вся
    # init_wiki_schema идёт одним савпоинтом, и откатывалась схема целиком.
    # Симптом был неочевидный: приложение работает, таблицы на месте, но ни
    # одна новая миграция раздела больше не применяется.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_wiki_section_rule_subject_level
        ON wiki_section_access_rules(section_id, subject_type,
                                     COALESCE(subject_id, -1), COALESCE(subject_role, ''),
                                     COALESCE(min_role_level, -1));
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
# Офисы
#
# Физический адрес — самостоятельная запись, а парки к нему привязываются.
# В статье «Адреса офисов» модель была обратной: таблица на каждый парк, и один
# и тот же адрес (Астана, Сарыарка 31) переписан в шести таблицах. Отсюда и
# расхождения в проде — у Костаная, Павлодара, Тараза, Атырау и Кызылорды
# телефон зависел от того, в чьей таблице его правили последним.
#
# Поэтому связь несёт переопределения: адрес и карта живут у офиса, а телефон
# и график можно задать отдельно для конкретного парка. NULL в переопределении
# означает «как у офиса» — это позволяет не размножать одинаковые значения.
#
# all_parks — «офис у всех таксопарков». Флаг, а не 15 строк связи: парки
# заводят и архивируют, и список пришлось бы досыпать руками при каждом новом.
# Строка связи при этом всё равно может существовать — как носитель
# переопределения.
# ─────────────────────────────────────────────────────────────────────────────
_OFFICE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS wiki_offices (
        id            SERIAL PRIMARY KEY,
        slug          VARCHAR(120) NOT NULL UNIQUE,
        name          VARCHAR(255) NOT NULL,
        city          VARCHAR(120),
        address       TEXT,
        address_note  TEXT,
        phone         VARCHAR(64),
        map_url       TEXT,
        map_resolved_url TEXT,
        lat           NUMERIC(9,6),
        lon           NUMERIC(9,6),
        map_checked_at TIMESTAMP,
        schedule      JSONB,
        is_online     BOOLEAN NOT NULL DEFAULT FALSE,
        all_parks     BOOLEAN NOT NULL DEFAULT FALSE,
        kind          VARCHAR(16) NOT NULL DEFAULT 'park'
                      CHECK (kind IN ('park', 'partner')),
        partner_label VARCHAR(120),
        status        VARCHAR(16) NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'archived')),
        position      INTEGER NOT NULL DEFAULT 0,
        created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at    TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at    TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_offices_order ON wiki_offices(status, city, position, id);",
    """
    CREATE TABLE IF NOT EXISTS wiki_office_taxi_parks (
        office_id INTEGER NOT NULL REFERENCES wiki_offices(id) ON DELETE CASCADE,
        park_id   INTEGER NOT NULL REFERENCES wiki_taxi_parks(id) ON DELETE CASCADE,
        phone     VARCHAR(64),
        schedule  JSONB,
        note      TEXT,
        PRIMARY KEY (office_id, park_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_office_parks_park ON wiki_office_taxi_parks(park_id);",
    # Номера парка.
    #
    # Отдельная таблица, а не колонка phone у связи: у парка в одном офисе
    # бывает несколько номеров, и второй номер некуда было положить. Строка без
    # office_id — номер без офиса, «онлайн»: парк принимает только по телефону.
    # Туда же переехал общий телефон парка (wiki_taxi_parks.phone) — иначе номер
    # заводился бы в двух разных местах формы и расходился, как расходились
    # телефоны офисов в статье «Адреса офисов».
    """
    CREATE TABLE IF NOT EXISTS wiki_park_phones (
        id        SERIAL PRIMARY KEY,
        park_id   INTEGER NOT NULL REFERENCES wiki_taxi_parks(id) ON DELETE CASCADE,
        office_id INTEGER REFERENCES wiki_offices(id) ON DELETE CASCADE,
        phone     VARCHAR(64) NOT NULL,
        position  INTEGER NOT NULL DEFAULT 0
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_park_phones_park "
    "ON wiki_park_phones(park_id, office_id, position, id);",
    "CREATE INDEX IF NOT EXISTS idx_wiki_park_phones_office ON wiki_park_phones(office_id);",
    # Записка у номера: «звонить после 10», «только WhatsApp», «спросить Асель».
    # У связи «парк ↔ офис» своя note (примечание к офису целиком), а эта —
    # к конкретному номеру, потому что у одной точки номеров несколько и
    # относится записка обычно к одному из них.
    "ALTER TABLE wiki_park_phones ADD COLUMN IF NOT EXISTS note VARCHAR(200);",
    # Адрес парка — ссылка на офис, а не текст.
    #
    # Пока это было свободное поле, оно повторяло адрес, уже записанный в
    # справочнике офисов: та же болезнь, от которой ушла статья «Адреса офисов»
    # — один адрес в двух местах расходится, и правят тот, который попался.
    # ON DELETE SET NULL: офис архивируют, а не удаляют, но если запись всё же
    # исчезнет, парк должен остаться без адреса, а не пропасть каскадом.
    "ALTER TABLE wiki_taxi_parks ADD COLUMN IF NOT EXISTS head_office_id INTEGER "
    "REFERENCES wiki_offices(id) ON DELETE SET NULL;",
    # Перенос: старый одиночный телефон становится первым номером и ОСУШАЕТСЯ
    # в источнике. Без осушения удалённый в форме номер возвращался бы при
    # каждом старте — старая колонка залила бы его заново.
    """
    INSERT INTO wiki_park_phones (park_id, office_id, phone, position)
    SELECT op.park_id, op.office_id, btrim(op.phone), 0
      FROM wiki_office_taxi_parks op
     WHERE op.phone IS NOT NULL AND btrim(op.phone) <> ''
       AND NOT EXISTS (SELECT 1 FROM wiki_park_phones ph
                        WHERE ph.park_id = op.park_id AND ph.office_id = op.office_id);
    """,
    "UPDATE wiki_office_taxi_parks SET phone = NULL WHERE phone IS NOT NULL;",
    """
    INSERT INTO wiki_park_phones (park_id, office_id, phone, position)
    SELECT p.id, NULL, btrim(p.phone), 0
      FROM wiki_taxi_parks p
     WHERE p.phone IS NOT NULL AND btrim(p.phone) <> ''
       AND NOT EXISTS (SELECT 1 FROM wiki_park_phones ph
                        WHERE ph.park_id = p.id AND ph.office_id IS NULL);
    """,
    "UPDATE wiki_taxi_parks SET phone = NULL WHERE phone IS NOT NULL;",
    # «Офиса в городе нет» — тоже запись справочника, иначе такой город виден
    # только тому, кто знает, что его там нет. Флагом, а не третьим значением
    # kind: расширять CHECK у существующей колонки дороже, чем добавить флаг.
    "ALTER TABLE wiki_offices ADD COLUMN IF NOT EXISTS no_office BOOLEAN NOT NULL DEFAULT FALSE;",
    # Статус офиса за день.
    #
    # График отвечает на вопрос «во сколько открывается по вторникам», а не «был
    # ли офис открыт 17 августа»: временное закрытие в графике не выразить, а
    # задним числом менять график нельзя — он перепишет и всю прошлую историю.
    # Поэтому день фиксируется строкой: source='manual' ставит человек
    # («закрыт, прорвало трубу»), source='auto' — ночной снимок по графику.
    # Ручная отметка снимку не уступает: ON CONFLICT DO NOTHING в
    # snapshot_offices_day.
    #
    # Состояний два. «Нет офиса» — свойство самой записи (no_office), а не дня:
    # город не закрывается на один вторник.
    """
    CREATE TABLE IF NOT EXISTS wiki_office_days (
        office_id   INTEGER NOT NULL REFERENCES wiki_offices(id) ON DELETE CASCADE,
        day         DATE NOT NULL,
        state       VARCHAR(16) NOT NULL CHECK (state IN ('open', 'closed')),
        note        TEXT,
        source      VARCHAR(16) NOT NULL DEFAULT 'manual'
                    CHECK (source IN ('manual', 'auto')),
        recorded_at TIMESTAMP NOT NULL DEFAULT %(now)s,
        recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        PRIMARY KEY (office_id, day)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_office_days_day ON wiki_office_days(day);",
    # Кэш тайлов карты.
    #
    # Замер 12.08: 2ГИС отдаёт растровые тайлы без ключа, но пачку запросов
    # режет — на странице с пятнадцатью офисами четыре карты приходили пустыми
    # (204 с пустым телом, для <img> это ошибка). Тайл скачивается один раз и
    # дальше отдаётся нами: и 2ГИС не долбим, и карта не зависит от того,
    # сколько человек открыло раздел одновременно.
    #
    # Объём мал: пятнадцати офисам хватает шестидесяти тайлов, это ~2,5 МБ.
    """
    CREATE TABLE IF NOT EXISTS wiki_map_tiles (
        z          SMALLINT NOT NULL,
        x          INTEGER NOT NULL,
        y          INTEGER NOT NULL,
        image      BYTEA NOT NULL,
        fetched_at TIMESTAMP NOT NULL DEFAULT %(now)s,
        PRIMARY KEY (z, x, y)
    );
    """,
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
# Свёртка на каждом источнике: конфигурация 'russian' НЕ склеивает ни ё с е, ни
# казахские буквы с их русскими двойниками. Без неё запрос «отчет» не находил
# «отчёт», а «Казына» — акцию «Қазына» (замер на проде). Правило одно на весь
# раздел и живёт в wiki/text.py; запрос сворачивается тем же (wiki/search.py) —
# обе стороны обязаны быть согласованы, иначе становится хуже, а не лучше.
#
# Выражение колонки МЕНЯЕТСЯ ПРИ ОБНОВЛЕНИИ: генерируемую колонку нельзя
# «доправить», её пересоздают. Миграция ниже сравнивает сохранённое выражение с
# нужным и пересобирает колонку, только если правило разошлось.
# Пересборка генерируемых колонок при смене правила свёртки.
#
# ADD COLUMN IF NOT EXISTS ничего не меняет у СУЩЕСТВУЮЩЕЙ колонки: выражение
# генерации переписать нельзя, колонку пересоздают. Поэтому при изменении правила
# (например когда к ё→е добавились казахские буквы) нужен явный шаг: сравнить
# сохранённое в базе выражение с нужным и, если правило разошлось, снести колонку
# вместе с индексом и собрать заново.
#
# Проверка идёт по подстроке правила, а не по всему выражению: постгрес хранит
# его в своём нормализованном виде (кавычки, приведения типов), и сравнивать
# тексты целиком значило бы пересобирать колонку на каждом запуске.
def _regenerate_folded_columns(cursor):
    """Вернуть список колонок, которые пришлось пересобрать."""
    from .text import SQL_FOLD_FROM

    rebuilt = []
    for table, column in (('wiki_articles', 'search_vector'),
                          ('wiki_ai_chunks', 'chunk_tsv')):
        cursor.execute(
            """
            SELECT pg_get_expr(d.adbin, d.adrelid)
              FROM pg_attrdef d
              JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
             WHERE d.adrelid = %s::regclass AND a.attname = %s
            """,
            (table, column),
        )
        row = cursor.fetchone()
        if not row:
            continue                      # колонки ещё нет — её создаст ADD COLUMN
        if SQL_FOLD_FROM in (row[0] or ''):
            continue                      # правило уже актуальное
        cursor.execute('ALTER TABLE %s DROP COLUMN %s' % (table, column))
        rebuilt.append('%s.%s' % (table, column))
    return rebuilt


_SEARCH_STATEMENTS = [
    """
    ALTER TABLE wiki_articles ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('russian', translate(coalesce(title, ''), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')),          'A') ||
            setweight(to_tsvector('russian', translate(coalesce(search_aliases, ''), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')), 'B') ||
            setweight(to_tsvector('russian', translate(coalesce(summary, ''), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')),        'C') ||
            setweight(to_tsvector('russian', translate(coalesce(content_plain, ''), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')),  'D')
        ) STORED;
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_articles_fts ON wiki_articles USING GIN (search_vector);",
]

# ── Рубильник «во внешний ИИ не отправлять» ──────────────────────────────────
#
# Право прочитать статью на экране и право отправить её текст во внешний API —
# РАЗНЫЕ права, и второе строго уже. Поэтому отдельный флаг, а не переиспользование
# strict_mode или visibility_mode: те решают, кому показывать, а этот — что можно
# выгружать наружу. По умолчанию выключен: помощник видит то же, что человек,
# а владелец точечно помечает корпоративную информацию.
#
# Флаг есть и на разделе. Семантика СТРОГАЯ: статья выпадает, если помечен хотя
# бы один её раздел (см. шапку wiki/perimeter.py — там же про ловушку all() по
# пустому множеству, из-за которой статья без разделов исчезала бы молча).
_AI_STATEMENTS = [
    "ALTER TABLE wiki_articles ADD COLUMN IF NOT EXISTS "
    "ai_opt_out BOOLEAN NOT NULL DEFAULT FALSE;",
    "ALTER TABLE wiki_sections ADD COLUMN IF NOT EXISTS "
    "ai_opt_out BOOLEAN NOT NULL DEFAULT FALSE;",
    # Периметр помощника всегда сужает выборку этими тремя условиями сразу,
    # поэтому индекс частичный и покрывает ровно пригодные статьи.
    "CREATE INDEX IF NOT EXISTS idx_wiki_articles_ai_eligible "
    "ON wiki_articles (id) "
    "WHERE status = 'published' AND NOT strict_mode AND NOT ai_opt_out;",
    # Что проиндексировано и не устарело ли. Признак изменения — sha256 текста,
    # а НЕ updated_at и не version_number: правка только тегов или разделов до
    # UPDATE статьи не доходит, а version_number растёт даже на пустом PATCH.
    """
    CREATE TABLE IF NOT EXISTS wiki_ai_article_index (
        article_id   INTEGER PRIMARY KEY REFERENCES wiki_articles(id) ON DELETE CASCADE,
        content_hash CHAR(64) NOT NULL,
        chunk_count  INTEGER NOT NULL DEFAULT 0,
        indexed_at   TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    # Единица поиска помощника. heading_path — единственный источник контекста
    # куска: колонка wiki_articles.toc на проде пуста у всех статей.
    """
    CREATE TABLE IF NOT EXISTS wiki_ai_chunks (
        id           BIGSERIAL PRIMARY KEY,
        article_id   INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
        chunk_idx    INTEGER NOT NULL,
        heading_path TEXT NOT NULL DEFAULT '',
        text         TEXT NOT NULL,
        requires_ack BOOLEAN NOT NULL DEFAULT FALSE,
        char_len     INTEGER NOT NULL DEFAULT 0,
        text_hash    CHAR(64) NOT NULL,
        created_at   TIMESTAMP NOT NULL DEFAULT %(now)s,
        UNIQUE (article_id, chunk_idx)
    );
    """,
    # Свёртка с обеих сторон и словарь 'russian' — ровно как у
    # wiki_articles.search_vector: иначе запрос «отчет» не нашёл бы «отчёт», а
    # «Казына» — «Қазына».
    # Путь заголовков весит выше текста: «Залог» в заголовке — сильный сигнал.
    """
    ALTER TABLE wiki_ai_chunks ADD COLUMN IF NOT EXISTS chunk_tsv tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('russian', translate(coalesce(heading_path, ''), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')), 'B') ||
            setweight(to_tsvector('russian', translate(coalesce(text, ''), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')),         'D')
        ) STORED;
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_ai_chunks_fts "
    "ON wiki_ai_chunks USING GIN (chunk_tsv);",
    "CREATE INDEX IF NOT EXISTS idx_wiki_ai_chunks_article "
    "ON wiki_ai_chunks (article_id, chunk_idx);",
    "CREATE INDEX IF NOT EXISTS idx_wiki_ai_chunks_text_hash "
    "ON wiki_ai_chunks (text_hash);",
    # ── История чатов ────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS wiki_ai_chats (
        id              BIGSERIAL PRIMARY KEY,
        user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title           VARCHAR(255) NOT NULL DEFAULT '',
        message_count   INTEGER NOT NULL DEFAULT 0,
        last_message_at TIMESTAMP,
        deleted_at      TIMESTAMP,
        deleted_by      INTEGER,
        created_at      TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at      TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_ai_chats_user "
    "ON wiki_ai_chats (user_id, last_message_at DESC) WHERE deleted_at IS NULL;",
    # kind различает три исхода, и они РАЗНЫЕ для отчёта «о чём спрашивают, а в
    # вике нет»: answer — ответили, no_answer — ответа в доступных статьях не
    # нашлось, clarify — переспросили.
    """
    CREATE TABLE IF NOT EXISTS wiki_ai_messages (
        id            BIGSERIAL PRIMARY KEY,
        chat_id       BIGINT NOT NULL REFERENCES wiki_ai_chats(id) ON DELETE CASCADE,
        seq           INTEGER NOT NULL,
        role          VARCHAR(16) NOT NULL,
        kind          VARCHAR(16) NOT NULL DEFAULT 'answer',
        text          TEXT NOT NULL,
        provider      VARCHAR(32),
        model         VARCHAR(128),
        elapsed_ms    INTEGER,
        input_tokens  INTEGER,
        output_tokens INTEGER,
        feedback      SMALLINT,
        created_at    TIMESTAMP NOT NULL DEFAULT %(now)s,
        UNIQUE (chat_id, seq)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_ai_messages_chat "
    "ON wiki_ai_messages (chat_id, seq);",
    # Источники — СНИМОК момента ответа, а не ссылка на живой кусок. chunk_id
    # обнулится при пересборке индекса (куски пересоздаются), поэтому текст
    # цитаты, путь заголовков и хеш куска хранятся здесь и НЕ перепроверяются
    # при показе истории: quote_ok означает «проверено при генерации».
    # chunk_text_hash позволяет отличить устаревший якорь от живого.
    """
    CREATE TABLE IF NOT EXISTS wiki_ai_message_sources (
        id              BIGSERIAL PRIMARY KEY,
        message_id      BIGINT NOT NULL REFERENCES wiki_ai_messages(id) ON DELETE CASCADE,
        ord             SMALLINT NOT NULL,
        article_id      INTEGER NOT NULL REFERENCES wiki_articles(id) ON DELETE CASCADE,
        chunk_id        BIGINT,
        chunk_text_hash CHAR(64),
        title           VARCHAR(255) NOT NULL DEFAULT '',
        slug            VARCHAR(255) NOT NULL DEFAULT '',
        heading_path    TEXT NOT NULL DEFAULT '',
        quote           TEXT NOT NULL DEFAULT '',
        quote_ok        BOOLEAN NOT NULL DEFAULT FALSE,
        requires_ack    BOOLEAN NOT NULL DEFAULT FALSE,
        created_at      TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_ai_message_sources_message "
    "ON wiki_ai_message_sources (message_id, ord);",
    # Фрагмент назвала модель или сопоставил сервер по пересечению с ответом.
    # Отдельной колонкой, чтобы пометка в истории совпадала со свежим ответом.
    "ALTER TABLE wiki_ai_message_sources ADD COLUMN IF NOT EXISTS "
    "attributed BOOLEAN NOT NULL DEFAULT FALSE;",
]

# ── Векторы кусков ───────────────────────────────────────────────────────────
#
# Под ОТДЕЛЬНЫМ савпоинтом, как pg_trgm: расширение vector может быть недоступно
# по правам, и тогда помощник обязан продолжить работать на одной лексике, а не
# утащить за собой всю схему раздела. На проде vector 0.8.0 уже стоит (его
# ставит call_qa/rag/schema.sql), но опираться на это в DDL нельзя.
#
# Ключ — ХЕШ ТЕКСТА, а не идентификатор куска. Пересборка индекса удаляет и
# создаёт куски заново, и при ключе по куску правка одного абзаца в статье из 28
# кусков сжигала бы 28 векторов вместо одного. Provider/model/dim в ключе:
# смена контракта не портит старые векторы, они просто перестают подходить.
#
# vector БЕЗ размерности намеренно: так же сделано в call_qa
# (qa_policy_rule_embeddings), и это позволяет сменить модель без переливки
# таблицы. Индекса HNSW нет и не планируется: pgvector фильтрует права уже ПОСЛЕ
# прохода по индексу, а у оператора с 15 доступными статьями из 36 выдача тогда
# оказалась бы пустой при наличии разрешённой релевантной статьи. На нашем
# масштабе (около 200 кусков) плоский перебор — единицы миллисекунд.
_AI_VECTOR_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector;",
    """
    CREATE TABLE IF NOT EXISTS wiki_ai_embeddings (
        text_hash      CHAR(64) NOT NULL,
        embed_provider VARCHAR(32) NOT NULL,
        embed_model    VARCHAR(128) NOT NULL,
        embed_dim      INTEGER NOT NULL,
        embedding      vector NOT NULL,
        created_at     TIMESTAMP NOT NULL DEFAULT %(now)s,
        PRIMARY KEY (text_hash, embed_provider, embed_model, embed_dim)
    );
    """,
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


# ── Классификатор авто как статья вики ───────────────────────────────────────
#
# Раньше это был отдельный раздел портала. Отдельного раздела он не заслуживает:
# по смыслу это справочник, то есть статья. Тело у неё пустое — вместо него
# фронт рисует интерактивный калькулятор (см. CLASSIFIER_SLUG в WikiArticle).
#
# Доступ. Пункт «Классификатор авто» в сайдбаре не был ничем ограничен: его
# видели все. Разделы вики, наоборот, restricted и без правил, поэтому статья,
# унаследовавшая их периметр, пропала бы у всех, кроме админов. Чтобы перенос
# ничего не отнял, у статьи СВОЙ периметр (visibility_mode='restricted') и своё
# правило: читать могут все роли OTP. Секретного в ней нет — марки, модели и
# годы по тарифам.
CLASSIFIER_SLUG = 'klassifikator-avto'

_CLASSIFIER_SUMMARY = ('Подходит ли автомобиль под тарифы Яндекс Про: '
                       'марка, модель, город и год выпуска.')

# Текст для поиска: сама статья тела не имеет, но находиться по смыслу обязана.
_CLASSIFIER_PLAIN = (
    'Классификатор авто. Проверка автомобиля по тарифам Яндекс Про: '
    'марка, модель, город, год выпуска. Эконом, комфорт, комфорт плюс, бизнес, '
    'межгород, доставка. Какие машины подходят под тариф и с какого года.')

_CLASSIFIER_STATEMENTS = [
    """
    INSERT INTO wiki_articles (slug, title, summary, content, content_plain,
                               article_type, status, visibility_mode)
    VALUES (%(slug)s, 'Классификатор авто', %(summary)s, '', %(plain)s,
            'tool_description', 'published', 'restricted')
    ON CONFLICT (slug) DO NOTHING;
    """,
    # Правило на каждую роль: подписки на «всех» в модели прав нет, и это
    # правильно — субъект всегда назван явно.
    """
    INSERT INTO wiki_article_access_rules (article_id, subject_type, subject_role,
                                           mode, can_read)
    SELECT a.id, 'otp_role', r.role, 'grant', TRUE
      FROM wiki_articles a, unnest(%(roles)s::text[]) AS r(role)
     WHERE a.slug = %(slug)s
       AND NOT EXISTS (SELECT 1 FROM wiki_article_access_rules x
                        WHERE x.article_id = a.id
                          AND x.subject_type = 'otp_role'
                          AND x.subject_role = r.role);
    """,
]


# ─────────────────────────────────────────────────────────────────────────────
# ОТДЕЛ И УРОВЕНЬ ДОЛЖНОСТИ В ДЕРЕВЕ РАЗДЕЛОВ
#
# Дерево «Коммерческого отдела» выглядит так:
#
#     Коммерческий отдел (пространство)
#     ├── Коммерческий директор      min_role_level 50
#     ├── Руководитель группы        subject_type='department_head'
#     ├── Супервайзер                department + min_role_level 30
#     └── Оператор
#         ├── ОП    (section_kind='department', department_id=<ОП>)
#         └── ОТП   (section_kind='department', department_id=<СЗоВ>)
#
# Зачем min_role_level. Правило несёт РОВНО ОДИН субъект, а нам нужна связка
# «отдел И не ниже уровня»: правило на department открыло бы операторам раздел
# супервайзера, а правило на otp_role='sv' пробило бы границу отдела — СВ продаж
# увидел бы ОТП. Уровень — второе измерение того же правила, а не второй субъект:
# правило действует, если уровень роли человека НЕ НИЖЕ указанного. Отсюда же
# бесплатно получается «видит своё и всё, что ниже себя»: раздел супервайзера
# требует 30, операторский — ничего, и глава отдела (40) подпадает под оба.
#
# Шкала — ROLE_LEVELS из wiki/access.py (operator 10, trainer 20, sv 30,
# admin 40, super_admin 50). NULL = ограничения по уровню нет.
# ─────────────────────────────────────────────────────────────────────────────
_ORG_STATEMENTS = [
    "ALTER TABLE wiki_sections ADD COLUMN IF NOT EXISTS "
    "department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL;",
    "ALTER TABLE wiki_sections ADD COLUMN IF NOT EXISTS "
    "section_kind VARCHAR(16) NOT NULL DEFAULT 'common';",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conname = 'wiki_sections_kind_chk') THEN
            ALTER TABLE wiki_sections ADD CONSTRAINT wiki_sections_kind_chk
                CHECK (section_kind IN ('common', 'department'));
        END IF;
    END $$;
    """,
    # Один отдел — одна ветка у одного родителя. Предикат отсекает архив:
    # иначе ветку, убранную в архив, нельзя было бы создать заново.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_wiki_section_department
        ON wiki_sections (space_id, COALESCE(parent_section_id, 0), department_id)
     WHERE section_kind = 'department' AND status = 'active';
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_sections_department "
    "ON wiki_sections (department_id) WHERE department_id IS NOT NULL;",

    # ──────────────────────────────────────────────────────────────────
    # КОМУ ВИДЕН ПУБЛИЧНЫЙ РАЗДЕЛ
    #
    # «Публичный» до сих пор означало «вообще всем в компании», и другого
    # варианта не было: раздел «Общий сотрудник» открывался в том числе Тез КЦ,
    # которому вики не предназначена вовсе.
    #
    # ПУСТОЙ список = виден всем. Это не лень, а обратная совместимость: у всех
    # существующих публичных разделов списка нет, и они обязаны продолжать
    # работать как раньше. Заведён список — раздел виден только этим отделам.
    """
    CREATE TABLE IF NOT EXISTS wiki_section_public_departments (
        section_id    INTEGER NOT NULL REFERENCES wiki_sections(id) ON DELETE CASCADE,
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        PRIMARY KEY (section_id, department_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_public_dept_section "
    "ON wiki_section_public_departments (section_id);",

    "ALTER TABLE wiki_section_access_rules ADD COLUMN IF NOT EXISTS "
    "min_role_level INTEGER;",
    # Уровень входит в КЛЮЧ правила, а не просто в его поля. Иначе на одном
    # разделе нельзя выразить «отдел читает» + «супервайзер того же отдела ещё и
    # правит»: пара (раздел, субъект) совпадает, и второе правило затирало бы
    # первое при ON CONFLICT. Старый индекс без уровня снимаем.
    "DROP INDEX IF EXISTS uq_wiki_section_rule_subject;",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_wiki_section_rule_subject_level
        ON wiki_section_access_rules (section_id, subject_type,
                                      COALESCE(subject_id, -1),
                                      COALESCE(subject_role, ''),
                                      COALESCE(min_role_level, -1));
    """,
    "ALTER TABLE wiki_article_access_rules ADD COLUMN IF NOT EXISTS "
    "min_role_level INTEGER;",

    # Родословная копии и рубильник витрины соседей.
    # cross_department по умолчанию TRUE: закрытость — осознанное решение
    # владельца статьи, а не состояние по умолчанию, иначе заимствовать будет
    # нечего и механика «перенести к себе» останется мёртвой.
    # Раздел «Вики» как обычный раздел портала: выдаётся отделу тумблером.
    # По умолчанию TRUE — раздел уже открыт всем, и миграция не должна его
    # ни у кого отобрать; закрывают точечно.
    "ALTER TABLE departments ADD COLUMN IF NOT EXISTS "
    "wiki_enabled BOOLEAN NOT NULL DEFAULT TRUE;",

    "ALTER TABLE wiki_articles ADD COLUMN IF NOT EXISTS "
    "cross_department BOOLEAN NOT NULL DEFAULT TRUE;",
    "ALTER TABLE wiki_articles ADD COLUMN IF NOT EXISTS "
    "source_article_id INTEGER REFERENCES wiki_articles(id) ON DELETE SET NULL;",
    "CREATE INDEX IF NOT EXISTS idx_wiki_articles_source "
    "ON wiki_articles (source_article_id) WHERE source_article_id IS NOT NULL;",
]


def _subject_type_check_statement(table):
    """Пересобирает CHECK на subject_type, добавляя новые типы субъектов.

    Список типов задан один раз в SUBJECT_TYPES, а в базе на проде уже лежит
    ограничение со СТАРЫМ набором и автоматическим именем
    (wiki_section_access_rules_subject_type_check). ADD COLUMN IF NOT EXISTS тут
    не поможет — колонка есть, ограничение просто отстало, поэтому старое
    снимаем и ставим именованное.

    Отличаем нужное ограничение по 'ARRAY[': в этих же таблицах есть второе
    CHECK, тоже упоминающее subject_type («у otp_role заполнена subject_role, у
    остальных subject_id»), и снести его нельзя — оно продолжает работать и для
    department_head, где заполняется subject_id (идентификатор отдела). Списка
    значений в нём нет, поэтому 'ARRAY[' их и разделяет.

    Ловушка, на которой это уже сломалось: pg_get_constraintdef печатает список
    как `= ANY ((ARRAY[...])::text[])` — с ДВОЙНОЙ скобкой. Шаблон 'ANY (ARRAY['
    не совпадал ни с чем, старое ограничение оставалось рядом с новым, и вставка
    правила с субъектом department_head падала на нём с 500.
    """
    values = ", ".join("'%s'" % name for name in SUBJECT_TYPES)
    name = '%s_subject_type_chk' % table
    return """
    DO $$
    DECLARE stale text;
    BEGIN
        FOR stale IN
            SELECT conname FROM pg_constraint
             WHERE conrelid = '{table}'::regclass
               AND contype = 'c'
               AND conname <> '{name}'
               AND pg_get_constraintdef(oid) LIKE '%subject_type%'
               AND pg_get_constraintdef(oid) LIKE '%ARRAY[%'
        LOOP
            EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', stale);
        END LOOP;

        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN
            ALTER TABLE {table} ADD CONSTRAINT {name}
                CHECK (subject_type IN ({values}));
        END IF;
    END $$;
    """.format(table=table, name=name, values=values)


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

    # Отдел и уровень должности в дереве — строго после базовых таблиц:
    # это ALTER по wiki_sections и обеим таблицам правил.
    for statement in _ORG_STATEMENTS:
        cursor.execute(statement)
    for table in ('wiki_section_access_rules', 'wiki_article_access_rules'):
        cursor.execute(_subject_type_check_statement(table))

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

    # Офисы — строго после парков: связь ссылается на wiki_taxi_parks.
    for statement in _OFFICE_STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW))

    # Выражение генерируемой колонки менять через ALTER нельзя — только
    # пересоздать; «ADD COLUMN IF NOT EXISTS» молча оставит старое определение.
    # Проверка и пересборка вынесены в _regenerate_folded_columns: правило
    # свёртки одно на обе колонки, и обновляться они обязаны вместе.
    rebuilt_columns = _regenerate_folded_columns(cursor)
    rebuilt_search_vector = 'wiki_articles.search_vector' in rebuilt_columns

    for statement in _SEARCH_STATEMENTS:
        cursor.execute(statement)

    # Рубильник ИИ и таблицы кусков — сразу после поисковых колонок: обе группы
    # это ALTER по wiki_articles, и держать их рядом дешевле, чем искать по файлу.
    # Подстановка даты через str.replace, как и выше: второй аргумент execute не
    # передаётся, поэтому psycopg2 %(now)s сам не раскрыл бы.
    for statement in _AI_STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW))

    # Куски пересобирать не нужно: chunk_tsv генерируемая, и постгрес пересчитал
    # её сам при создании колонки. Эмбеддинги к свёртке отношения не имеют — они
    # считаются по тексту куска, а текст не менялся.

    # Векторы — под своим савпоинтом: без расширения помощник остаётся на
    # лексическом поиске, а схема раздела не откатывается целиком.
    cursor.execute('SAVEPOINT wiki_ai_vector')
    try:
        for statement in _AI_VECTOR_STATEMENTS:
            cursor.execute(statement.replace('%(now)s', _NOW))
    except Exception:
        cursor.execute('ROLLBACK TO SAVEPOINT wiki_ai_vector')
        import logging
        logging.warning(
            'Раздел «Вики»: расширение vector недоступно — ИИ-помощник будет '
            'искать только лексически (гибрид отключён)'
        )
    else:
        cursor.execute('RELEASE SAVEPOINT wiki_ai_vector')

    # Вместе с колонкой пересчитываются и search_aliases: нормализация запроса
    # (ё -> е кириллическая) изменилась, а сохранённые алиасы считались старой.
    if rebuilt_search_vector:
        from .search import refresh_aliases
        cursor.execute('SELECT id FROM wiki_articles')
        for (article_id,) in cursor.fetchall():
            refresh_aliases(cursor, article_id)

    # Классификатор — статья вики, а не отдельный раздел портала.
    for statement in _CLASSIFIER_STATEMENTS:
        cursor.execute(statement, {'slug': CLASSIFIER_SLUG,
                                   'summary': _CLASSIFIER_SUMMARY,
                                   'plain': _CLASSIFIER_PLAIN,
                                   'roles': list(OTP_ROLES)})
    from .search import refresh_aliases as _refresh_aliases
    cursor.execute('SELECT id FROM wiki_articles WHERE slug = %s', (CLASSIFIER_SLUG,))
    _row = cursor.fetchone()
    if _row:
        _refresh_aliases(cursor, _row[0])

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
