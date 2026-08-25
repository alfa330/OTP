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

# ─────────────────────────────────────────────────────────────────────────────
# ТУМБЛЕРЫ ПРОСТРАНСТВА
#
# Пространство — это не только «чьи разделы», но и «из чего состоит раздел
# Вики» у тех, кому оно выдано. Тез КЦ нужен свой набор статей и никакого
# «Помощника», парков и офисов iGroup; у другого клиента наоборот.
#
# ОТСУТСТВИЕ ключа = тумблер ВКЛЮЧЁН. Иначе выкат отобрал бы у существующего
# пространства всё сразу: у него в features пусто, и любая другая трактовка
# пустоты означала бы «вики выключена целиком».
#
# «Главной» в списке нет намеренно: витрина статей — это и есть раздел, и
# выключаемая главная оставила бы пространство без единственного экрана, куда
# ведут все ссылки. Внутри неё выключается только рельс парков.
SPACE_FEATURES = (
    'assistant',          # вкладка «Помощник»
    'catalog',            # вкладка «Статьи»…
    'catalog_articles',   #   └ половина «Статьи»
    'catalog_structure',  #   └ половина «Структура»
    'catalog_trainers',   #   └ половина «Тренажёры»
    'catalog_guests',     #   └ половина «Гостевой доступ»
    'overview',           # вкладка «Обзор»
    'parks',              # вкладка «Парки»
    'offices',            # вкладка «Офисы»
    'analytics',          # вкладка «Аналитика»
    'audit',              # вкладка «Журнал»
    'library_park_rail',  # рельс парков на главной
)


def space_features(raw):
    """Полный набор тумблеров: чего нет в JSONB — то включено.

    Один вычислитель на весь пакет. Разложить «пусто = включено» по местам
    использования значит однажды разойтись: витрина покажет вкладку, которую
    сервер уже считает выключенной.
    """
    stored = raw if isinstance(raw, dict) else {}
    return {key: stored.get(key, True) is not False for key in SPACE_FEATURES}

# Тип статьи. Один источник правды на весь пакет: CHECK в wiki_articles,
# белый список правки (routes_edit) и фильтр витрины (routes_articles) обязаны
# брать значения отсюда — разойдись они, тип, который можно сохранить, стало бы
# нельзя отфильтровать.
ARTICLE_TYPES = (
    'general', 'job_description', 'regulation', 'instruction', 'tool_description',
    # 'trainer' — статья-тренажёр: в её тексте стоит кнопка запуска учебного
    # сценария (телефон + помощник), а сам сценарий живёт во фронте
    # (src/components/wiki/trainers). Тип нужен серверу не для отрисовки, а
    # чтобы редактор знал, когда показывать выбор тренажёра, а витрина умела
    # собрать такие статьи в одну подборку.
    'trainer',
)

# Статусы статьи, сгруппированные в три корзины витрины: «Статьи», «Черновики»,
# «Архив». Переключатель на вкладке «Статьи» и счётчики на главной обязаны
# считать ОДИНАКОВО — иначе плитка «9 черновиков» открывает список из семи.
#
# Корзины покрывают ВСЕ шесть статусов CHECK'а wiki_articles намеренно: статус,
# не попавший ни в одну, исчез бы из раздела молча. Поэтому 'on_approval' и
# 'requires_verification' лежат в черновиках (текст ещё не выпущен), а
# 'expired' — в архиве (выпущен и отозван временем).
ARTICLE_BUCKETS = {
    'published': ('published',),
    'draft': ('draft', 'on_approval', 'requires_verification'),
    'archived': ('archived', 'expired'),
}

# Обратное отображение: статус → корзина. Строится из ARTICLE_BUCKETS, чтобы
# второго списка, который можно забыть обновить, не появилось.
BUCKET_OF_STATUS = {
    status: bucket
    for bucket, statuses in ARTICLE_BUCKETS.items()
    for status in statuses
}

# Шесть прав. В оригинальной вики их пять — can_delete добавлено нами.
PERMISSION_COLUMNS = (
    'can_read', 'can_create', 'can_edit', 'can_delete', 'can_publish', 'can_approve',
)

# Способности уровня раздела (глобальные, не привязаны к конкретному разделу вики).
CAPABILITY_COLUMNS = (
    'can_read', 'can_create', 'can_edit', 'can_delete', 'can_publish', 'can_approve',
    'can_manage_users', 'can_manage_structure', 'can_manage_access',
)

# Потолок срока гостевого доступа — решение владельца 25.08.2026: «думаю,
# макс — 14 дней». Выдача на полгода перестаёт быть гостевой и подменяет собой
# правило раздела: правило видно в «Структуре» и объясняет себя, выдача — нет.
# Потолок действует и на продление: продлевают от «сейчас», а не от прежнего
# срока, иначе тем же нажатием набирается любой горизонт.
MAX_GUEST_DAYS = 14

# Человекочитаемые названия прав — для внятного отказа. Живут здесь, рядом с
# самими колонками, а не в routes.py: их читают и декоратор роута, и оба места,
# где выписывают правила, а третья копия однажды разошлась бы с первыми двумя.
CAPABILITY_TITLES = {
    'can_read': 'чтение',
    'can_create': 'создание статей',
    'can_edit': 'правка статей',
    'can_delete': 'удаление',
    'can_publish': 'публикация',
    'can_approve': 'согласование',
    'can_manage_users': 'управление людьми',
    'can_manage_structure': 'управление структурой',
    'can_manage_access': 'управление доступами',
}

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
        -- ВНИМАНИЕ: это НЕ граница пространства. Граница — список отделов в
        -- wiki_space_departments (см. _SPACE_STATEMENTS); department_id здесь
        -- остался от ручного режима доступа, где выдача отдела раскрывается во
        -- все разделы его пространств (queries._MANUAL_SECTIONS_SQL). На проде
        -- он пуст у всех пространств, и конструктор его не заполняет.
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
        -- Список значений живёт в ARTICLE_TYPES; здесь он ИСТОРИЧЕСКИЙ и
        -- нарочно не дописывается: ограничение всё равно пересобирается ниже
        -- (_article_type_check_statement) — и на пустой базе, и на проде, где
        -- оно уже создано со старым набором.
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
    # Отдел читателя СНИМКОМ — так же, как в прохождениях тренажёров и в
    # назначениях на ознакомление. Без него разрез «кто читает» джойнился бы к
    # живому users.department_id и задним числом переписывал бы всю историю
    # человека на его нынешний отдел: оператор, перешедший из СЗоВ в продажи,
    # уносил бы туда и прошлогодние чтения. Пишется из контекста запроса, то
    # есть бесплатно — отдел там уже посчитан. У строк, накопленных до этой
    # колонки, значение остаётся NULL, и отчёт откатывается на живой отдел.
    "ALTER TABLE wiki_article_views_log ADD COLUMN IF NOT EXISTS "
    "snapshot_department_id INTEGER;",
    # Должность на момент чтения — отвечает на «читают операторы или только
    # руководители». ИМЯ отдела снимком НЕ храним намеренно, в отличие от
    # тренажёров: переименование отдела обязано распространяться на всю
    # историю (это тот же отдел), а переход человека между отделами ловится
    # идентификатором выше.
    "ALTER TABLE wiki_article_views_log ADD COLUMN IF NOT EXISTS "
    "snapshot_role VARCHAR(20);",

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

# Пространство записи журнала. Формула одна на всех: по ней проставляет
# space_id новая запись (queries.log_action) и по ней же разобрана история
# (_scope_audit_to_space). Разъедься они — и журнал разложил бы прошлое и
# настоящее по разным правилам, а заметить это можно было бы только сверкой
# двух выборок вручную.
#
# Ступени по убыванию достоверности:
#   1. сам объект — у раздела, парка, офиса и акции пространство лежит
#      колонкой, у статьи выводится через её разделы (статья без разделов не
#      принадлежит никакому пространству — то же правило, что в
#      queries.articles_of_space);
#   2. details->>'space_id' — часть действий пишется ДО того, как объект
#      появился (импорт документа, загрузка логотипа), и пространство у них
#      названо только там;
#   3. ничего — NULL. Такая запись видна в журнале ЛЮБОГО пространства:
#      спрятать её везде значило бы потерять запись, а придумать ей хозяина
#      нельзя. Ступень существует, потому что объект бывает уже удалён.
#
# Внешний SELECT по wiki_spaces — не украшение: и details, и явный аргумент
# приходят снаружи, а несуществующий id уронил бы саму запись по внешнему
# ключу. Действие при этом уже совершено, и падать на его протоколировании
# нельзя.
AUDIT_SPACE_SQL = """
        SELECT s.id FROM wiki_spaces s WHERE s.id = COALESCE(
            CASE %(etype)s
                WHEN 'space'     THEN (SELECT x.id       FROM wiki_spaces     x WHERE x.id = %(eid)s)
                WHEN 'section'   THEN (SELECT x.space_id FROM wiki_sections   x WHERE x.id = %(eid)s)
                WHEN 'park'      THEN (SELECT x.space_id FROM wiki_taxi_parks x WHERE x.id = %(eid)s)
                WHEN 'office'    THEN (SELECT x.space_id FROM wiki_offices    x WHERE x.id = %(eid)s)
                WHEN 'promotion' THEN (SELECT x.space_id FROM wiki_promotions x WHERE x.id = %(eid)s)
                WHEN 'article'   THEN (SELECT sec.space_id
                                         FROM wiki_article_sections link
                                         JOIN wiki_sections sec ON sec.id = link.section_id
                                        WHERE link.article_id = %(eid)s
                                        ORDER BY sec.space_id LIMIT 1)
            END,
            CASE WHEN %(details)s::jsonb->>'space_id' ~ '^[0-9]+$'
                 THEN (%(details)s::jsonb->>'space_id')::int END
        )
"""

# Типы объектов, для которых формула умеет назвать пространство. Список
# отдельно от самого SQL, чтобы страж (tests/test_wiki_audit_space.py) мог
# сверить его с типами, которые пишут роуты: забытый тип не ломается, он молча
# кладёт запись в журнал ВСЕХ пространств — тише, чем ошибка, и хуже.
AUDIT_SPACE_ENTITIES = ('space', 'section', 'park', 'office', 'promotion', 'article')


def audit_space_sql(entity_type, entity_id, details):
    """Формула с подставленными выражениями.

    Запись подставляет свои плейсхолдеры и остаётся с ними (%(etype)s и
    соседи — имена параметров её INSERT), разбор истории — колонки таблицы.
    Через .replace, а не вторым аргументом execute: у UPDATE на месте
    параметров стоят колонки, а не значения.
    """
    return (AUDIT_SPACE_SQL
            .replace('%(etype)s', entity_type)
            .replace('%(eid)s', entity_id)
            .replace('%(details)s', details))

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
    # Ракурс логотипа: {"zoom":1.6,"x":0.32,"y":0.6,"ratio":1.5} — какая часть
    # картинки видна в квадратной плитке.
    #
    # Отдельным полем, а не обрезкой самого файла: логотип показывается
    # плиткой, и браузер по object-cover брал середину — у широкой вывески это
    # кусок фона между словами. Обрезать файл значило бы решать это один раз
    # навсегда: чтобы отступить обратно, картинку пришлось бы загружать заново.
    # Здесь же ракурс — четыре числа, их правят сколько угодно раз, а пиксели
    # остаются на месте. NULL — «как раньше»: середина без увеличения.
    "ALTER TABLE wiki_taxi_parks ADD COLUMN IF NOT EXISTS logo_frame JSONB;",
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

    # Закрытие на СРОК — на самой записи офиса, а не строкой в wiki_office_days.
    # Причина: отметка дня живёт ровно один день, и закрытие «с 17.08 по 03.09»
    # приходилось бы или ставить заново каждое утро (на проде так и было: 24.08
    # Атырау и Костанай показывались открытыми, хотя закрыты до 28.08 и 03.09),
    # или писать 18 строк вперёд. Периодов у офиса одновременно не бывает больше
    # одного, поэтому отдельная таблица не нужна: три колонки на записи дают то
    # же самое и не заводят третий источник правды.
    # closed_until NULL при заполненном closed_from = «срок не известен» (так и
    # писали руками: «На неопределенный срок»).
    "ALTER TABLE wiki_offices ADD COLUMN IF NOT EXISTS closed_from DATE;",
    "ALTER TABLE wiki_offices ADD COLUMN IF NOT EXISTS closed_until DATE;",
    "ALTER TABLE wiki_offices ADD COLUMN IF NOT EXISTS closed_note TEXT;",
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


# Граница пространства у справочников парков и офисов.
#
# Отдельной функцией, а не строками в _OFFICE_STATEMENTS, по двум причинам:
# порядок шагов внутри имеет значение (сначала заполнить, потом NOT NULL, потом
# уникальность), и нужен DEFAULT_SPACE_CODE, который объявлен ниже в файле —
# из списка-литерала его не достать.
def _scope_directories_to_space(cursor):
    """Привязывает парки, офисы и акции к пространству.

    Справочник был общекомпанейским: одна таблица офисов на всю вику. Пока
    пространство было одно, это совпадало с правдой; со вторым («Тез»)
    совпадать перестало. Слова space в wiki/offices.py и wiki/parks.py не было
    ни разу, то есть границу нельзя было выразить вовсе — стоило включить
    вкладку «Офисы» конструктором, и сотрудник Тез КЦ видел адреса, телефоны и
    графики офисов Таксопарков (и через фильтр «по парку» — сам список парков).

    space_id — колонка на самой записи, как у wiki_sections, а НЕ таблица
    связи: у физического офиса один хозяин, и «офис двух пространств» означал
    бы, что правка телефона в одной вике меняет его в другой — ровно та
    болезнь, от которой ушла статья «Адреса офисов». Понадобится общий на две
    вики адрес — его заведут двумя записями, и каждая будет править свою.

    ON DELETE CASCADE — как у wiki_sections.space_id. Пространства архивируют,
    а не удаляют (routes_structure), так что каскад срабатывает только на
    переезде в _merge_legacy_spaces.

    NOT NULL обязателен: NULL здесь прочитался бы как «принадлежит всем» —
    ровно та дыра, которую эта миграция закрывает.
    """
    tables = ('wiki_taxi_parks', 'wiki_offices', 'wiki_promotions')
    for table in tables:
        cursor.execute(
            'ALTER TABLE ' + table + ' ADD COLUMN IF NOT EXISTS space_id INTEGER '
            'REFERENCES wiki_spaces(id) ON DELETE CASCADE'
        )

    # Существующие записи — в пространство по умолчанию: именно в нём их и
    # вели, других пространств на момент их создания не было. Придумывать
    # раскладку по названиям городов нельзя — это угадывание, а справочник
    # после миграции обязан выглядеть точно так же, как до неё.
    cursor.execute('SELECT id FROM wiki_spaces WHERE code = %s', (DEFAULT_SPACE_CODE,))
    row = cursor.fetchone()
    if not row:
        # Пространства ещё нет — значит и справочников нет. NOT NULL поставим
        # на следующем старте, когда _merge_legacy_spaces заведёт контейнер.
        return
    for table in tables:
        cursor.execute('UPDATE ' + table + ' SET space_id = %s WHERE space_id IS NULL',
                       (row[0],))
        # Идемпотентно: у колонки, которая уже NOT NULL, это пустая операция.
        cursor.execute('ALTER TABLE ' + table + ' ALTER COLUMN space_id SET NOT NULL')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_wiki_offices_space '
                   'ON wiki_offices(space_id, status, city, position, id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_wiki_parks_space '
                   'ON wiki_taxi_parks(space_id, status, position, id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_wiki_promotions_space '
                   'ON wiki_promotions(space_id, status, ends_at)')

    # Слаг уникален В ПРОСТРАНСТВЕ, а не на всю вику. Иначе офис «Астана»,
    # заведённый в Тез, получил бы слаг astana-2 (routes_offices досыпает
    # суффикс, пока slug_is_free не согласится), и по номеру суффикса читалось
    # бы, сколько одноимённых записей лежит в чужой вике.
    #
    # Ограничение объявлено внутри CREATE TABLE, то есть на проде лежит с
    # автоматическим именем <таблица>_slug_key — снимаем по нему. Уникальность
    # при этом не ослабляется: составной индекс создаётся тем же шагом.
    for table, index in (('wiki_offices', 'uq_wiki_offices_space_slug'),
                         ('wiki_taxi_parks', 'uq_wiki_parks_space_slug')):
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS ' + index +
                       ' ON ' + table + '(space_id, slug)')
        cursor.execute('ALTER TABLE ' + table +
                       ' DROP CONSTRAINT IF EXISTS ' + table + '_slug_key')


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
def _scope_audit_to_space(cursor):
    """Привязывает записи журнала к пространству.

    Журнал был один на всю вику: у «Таксопарков» и «Теза» вкладка «Журнал»
    показывала одни и те же 1080 записей, то есть кто в чужой вике что правил.
    Это та же болезнь, от которой лечились справочники
    (_scope_directories_to_space), и лечится она так же — колонкой на записи.

    Почему колонка, а не вычисление на чтении: объект записи бывает удалён
    (на проде 41 запись об офисах, которых больше нет, и 15 о пространствах,
    слитых в «Таксопарки»), и вычисленное на лету пространство у них было бы
    NULL — журнал терял бы историю ровно там, где она и нужна.

    ON DELETE SET NULL, а не CASCADE: у справочников каскад означает «запись
    уехала вместе с пространством», а у журнала он означал бы «удалили
    пространство — и следов не осталось». Запись без пространства видна везде,
    и это честнее пустоты.

    Разбор истории — РАЗОВЫЙ, только в тот запуск, когда колонка появилась.
    Иначе каждый деплой доразмечал бы записи, которым пространство не
    досталось намеренно, и «ничьё» превращалось бы в «чьё-то» само собой.
    """
    cursor.execute(
        """
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'wiki_audit_log' AND column_name = 'space_id'
        """)
    first_run = cursor.fetchone() is None

    cursor.execute('ALTER TABLE wiki_audit_log ADD COLUMN IF NOT EXISTS space_id '
                   'INTEGER REFERENCES wiki_spaces(id) ON DELETE SET NULL')
    # Порядок колонок индекса — как читают журнал: сначала своё пространство,
    # потом сверху вниз по id (ORDER BY a.id DESC в structure.list_audit).
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_wiki_audit_space '
                   'ON wiki_audit_log(space_id, id DESC)')
    if not first_run:
        return

    cursor.execute(
        'UPDATE wiki_audit_log a SET space_id = ('
        + audit_space_sql('a.entity_type', 'a.entity_id', 'a.details') +
        ') WHERE a.space_id IS NULL')

    # Что не разобралось — единственному пространству, которое существовало в
    # тот момент. Это не догадка: пока пространство одно, вся вика и есть оно,
    # и записи старше второго пространства другому принадлежать не могли.
    # Записи МОЛОЖЕ второго пространства остаются без хозяина: там угадывание
    # уже настоящее, а неверно приписанная запись хуже записи, видимой везде.
    cursor.execute('SELECT id FROM wiki_spaces WHERE code = %s', (DEFAULT_SPACE_CODE,))
    row = cursor.fetchone()
    if not row:
        return
    cursor.execute(
        """
        UPDATE wiki_audit_log a
           SET space_id = %(space)s
         WHERE a.space_id IS NULL
           AND a.created_at < COALESCE(
                 (SELECT min(s.created_at) FROM wiki_spaces s WHERE s.id <> %(space)s),
                 'infinity'::timestamp)
        """,
        {'space': row[0]})


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
    # Пространство, в котором задавали вопрос. Ответу оно не нужно — периметр
    # уже сужен на входе, — но нужно ОТЧЁТУ: «о чём спрашивают, а в вике нет»
    # адресуется владельцу конкретной базы знаний, и вопрос из «Тез» в отчёте
    # по «Таксопаркам» это чужая работа. У чатов, заведённых до колонки,
    # остаётся NULL — такие в отчёте показываются в любом пространстве, потому
    # что «неизвестно где» безопаснее потерять, чем спрятать.
    "ALTER TABLE wiki_ai_chats ADD COLUMN IF NOT EXISTS space_id INTEGER;",
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


# ─────────────────────────────────────────────────────────────────────────────
# ГОСТЕВОЙ ДОСТУП
#
# Таблица wiki_guest_access заведена вместе с базовыми (см. _STATEMENTS выше), и
# ЧИТАЮЩАЯ сторона по ней работала с самого начала: выданный раздел попадает в
# периметр (queries._AUTO_SECTIONS_SQL), выданная статья — в витрину
# (articles._VISIBLE_ARTICLES_SQL). Не было ВЫДАЮЩЕЙ стороны: ни двери, ни
# права, ни срока в интерфейсе — доступ существовал только в схеме. Здесь
# появляются две недостающие колонки.
#
# include_subsections — раскрывается ли выдача на подразделы. По умолчанию TRUE,
# потому что человек, выдающий «Регламент СЗоВ», имеет в виду раздел со всем,
# что в нём лежит; ровно один узел дерева — частный случай, и он остаётся
# галочкой в форме. Прежним строкам TRUE ничего не расширяет сверх ожидаемого:
# подразделы того же раздела и есть то, за чем гостя посылали. Граница
# пространства при этом на месте — она наложена снаружи union'а
# (queries._SPACE_GATE_SQL) и подразделами не обходится.
# ─────────────────────────────────────────────────────────────────────────────
_GUEST_STATEMENTS = [
    "ALTER TABLE wiki_guest_access ADD COLUMN IF NOT EXISTS "
    "include_subsections BOOLEAN NOT NULL DEFAULT TRUE;",

    # Тумблер «право выдавать гостевой доступ» в правиле раздела прожил один
    # день. Владелец 25.08.2026 заменил его лестницей должностей
    # (access.GUEST_GRANT_CEILING): супервайзер и выше, каждый своим
    # подчинённым. Колонку убираем, а не оставляем пустой, ровно поэтому: право,
    # которого больше нет, но которое видно в схеме, однажды подключат обратно
    # вторым источником истины о том же самом. Терять нечего — колонка прожила
    # день, и записанное в ней право не существует.
    "ALTER TABLE wiki_section_access_rules DROP COLUMN IF EXISTS can_grant_guest;",

    # Список выдач читается с двух сторон: «кому открыт этот раздел» и «что я
    # раздал». Индекс по получателю уже есть (idx_wiki_guest_active), а этих
    # трёх не было вовсе — в оригинале у таблицы не было ни одного.
    "CREATE INDEX IF NOT EXISTS idx_wiki_guest_section "
    "ON wiki_guest_access (section_id) WHERE section_id IS NOT NULL;",
    "CREATE INDEX IF NOT EXISTS idx_wiki_guest_article "
    "ON wiki_guest_access (article_id) WHERE article_id IS NOT NULL;",
    "CREATE INDEX IF NOT EXISTS idx_wiki_guest_granted_by "
    "ON wiki_guest_access (granted_by, created_at DESC);",
]


# ─────────────────────────────────────────────────────────────────────────────
# ПРОСТРАНСТВО КАК ГРАНИЦА
#
# Раньше пространство было буфером ВНУТРИ одной вики: «Коммерческий отдел»,
# «IT-отдел», «Общий отдел». Оно ничего не закрывало — department_id у него не
# читал никто, и содержимое было общим на всю компанию. Из-за этого Тез КЦ,
# которому вика iGroup не предназначена вовсе, видел её разделы, и затыкать это
# приходилось поштучно на уровне раздела (wiki_section_public_departments у
# «Общий сотрудник»).
#
# Теперь пространство — верхний уровень и настоящая граница: список отделов, за
# который не выходит ни один его раздел, даже публичный. Бывшие пространства при
# миграции стали верхними РАЗДЕЛАМИ единственного пространства «iGroup»:
# структура сохранилась целиком, а уровней осталось столько же.
#
# ПУСТОЙ список отделов = пространство видно всем. Как и у публичного раздела,
# это обратная совместимость, а не умолчание «на всякий случай».
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# ПРОХОЖДЕНИЯ ТРЕНАЖЁРОВ
#
# Сами тренажёры сервер по-прежнему не знает: сценарии, экраны и реплики живут
# во фронте (src/components/wiki/trainers), и таблицы у них нет — см. шапку
# wiki/articles.py про trainer_usages. Здесь хранится ДРУГОЕ: факт, что человек
# садился за тренажёр, и чем это кончилось.
#
# Почему строка заводится на СТАРТЕ, а не по завершении. Половина ценности
# статистики — брошенные попытки: «пятеро дошли до подписи в eGov и закрыли»
# говорит про инструкцию больше, чем «трое прошли до конца». Строка со статусом
# started и есть такая попытка; закрытие урока её дополняет, а не создаёт.
#
# article_id NULL — это не потеря данных, а второй законный источник: тренажёр
# запускают и из статьи, и из вкладки «Тренажёры». Различает их source.
#
# Снимки отдела, группы и роли — как в wiki_ack_assignments и по той же причине:
# через год отчёт обязан показывать, кем человек был ТОГДА. Операторы переходят
# между группами каждый месяц, и без снимка «кто из моей группы прошёл» врёт.
#
# trainer_key НЕ ссылается ни на какую таблицу: ключ сценария живёт в коде
# (registry.js), и внешнего ключа для него не существует. Переименование ключа
# осиротит статистику — поэтому ключи и не переименовывают (см. шапку
# scenarioSapar.js).
# ─────────────────────────────────────────────────────────────────────────────
_TRAINER_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS wiki_trainer_runs (
        id            BIGSERIAL PRIMARY KEY,
        trainer_key   VARCHAR(64) NOT NULL,
        user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
        article_id    INTEGER REFERENCES wiki_articles(id) ON DELETE SET NULL,
        -- Название статьи снимком, как оргданные ниже: статью переименуют или
        -- уберут в архив, а отчёт обязан показывать, откуда запускали ТОГДА.
        article_title VARCHAR(255),
        source        VARCHAR(16) NOT NULL DEFAULT 'article',
        status        VARCHAR(16) NOT NULL DEFAULT 'started',
        stages_total  SMALLINT NOT NULL DEFAULT 0,
        stages_done   SMALLINT NOT NULL DEFAULT 0,
        errors        SMALLINT NOT NULL DEFAULT 0,
        hints         SMALLINT NOT NULL DEFAULT 0,
        restarts      SMALLINT NOT NULL DEFAULT 0,
        duration_ms   INTEGER,
        -- department_id нужен НЕ для отчёта, а для границы видимости: без него
        -- супервайзер СЗоВ с правом публикации видел бы поимённый состав ОП.
        snapshot_department_id   INTEGER,
        snapshot_department_name VARCHAR(120),
        snapshot_group_name      VARCHAR(120),
        snapshot_role            VARCHAR(30),
        started_at    TIMESTAMP NOT NULL DEFAULT %(now)s,
        finished_at   TIMESTAMP,
        CONSTRAINT wiki_trainer_runs_status_check
            CHECK (status IN ('started', 'finished', 'abandoned')),
        CONSTRAINT wiki_trainer_runs_source_check
            CHECK (source IN ('article', 'catalog'))
    );
    """,
    # Витрина статистики читает по ключу и по времени, выгрузка — по ключу и
    # диапазону дат. Оба запроса ложатся на один индекс.
    "CREATE INDEX IF NOT EXISTS idx_wiki_trainer_runs_key "
    "ON wiki_trainer_runs(trainer_key, started_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_wiki_trainer_runs_user "
    "ON wiki_trainer_runs(user_id, started_at DESC);",
    # «Сколько раз из этой статьи» — отдельный разрез вкладки, и без индекса он
    # читал бы всю таблицу: попыток со временем становится кратно больше, чем
    # статей.
    "CREATE INDEX IF NOT EXISTS idx_wiki_trainer_runs_article "
    "ON wiki_trainer_runs(article_id) WHERE article_id IS NOT NULL;",

    # ЧТО человек сделал, а не только сколько раз ошибся.
    #
    # У тренажёров-прогулок итог один на всех: дошёл до конца. У тренажёра
    # «Обращение в CRM» итог — сама заведённая карточка: какой источник выбран,
    # какая ветка категорий, что написано в комментарии. Смотреть на «промахов
    # 0» и не видеть, ЧТО человек завёл, бессмысленно: ошибиться веткой можно и
    # без единого промаха — с подсказкой.
    #
    # JSONB, а не колонки: у каждого тренажёра свой состав итога, и колонки
    # пришлось бы добавлять под каждый новый сценарий. Читает это только
    # человек глазами в карточке статистики, поэтому индекс не нужен.
    "ALTER TABLE wiki_trainer_runs ADD COLUMN IF NOT EXISTS "
    "result JSONB;",
]


# ─────────────────────────────────────────────────────────────────────────────
# ЖУРНАЛ ПОИСКОВЫХ ЗАПРОСОВ
#
# Пишется ради одного отчёта: «что ищут и не находят». Это единственный источник
# правды о дырах в базе знаний, который не требует ни от кого доброй воли, —
# человек, не нашедший статью, обычно не пишет об этом никому.
#
# Четыре решения, каждое дороже, чем кажется.
#
# 1. ПАРА (results_count, perimeter_size), А НЕ ОДИН НОЛЬ. Ноль находок сам по
#    себе неинтерпретируем: он одинаково означает «такой статьи нет» и «статья
#    есть, но этому человеку не выдана». Периметр здесь личный — у главы отдела
#    на проде это 16 статей из 36, — и без второго числа отчёт «нет статьи»
#    наполовину состоял бы из отказов доступа. Их лечат выдачей прав, а не
#    написанием текста, и путать их нельзя.
#
# 2. СВЁРТКА ПРЕФИКСОВ ВМЕСТО ЧЕСТНОЙ ЗАПИСИ КАЖДОГО ЗАПРОСА. Поле в шапке ищет
#    по мере набора (дебаунс 250 мс, порог 2 символа), поэтому одна фраза
#    «как оформить самозанятость» приезжает шестью запросами-огрызками. В логе
#    они дали бы топ из «ка», «как», «как о» — то есть отчёт, читать который
#    невозможно. Поэтому запись за тем же человеком за последние 30 секунд
#    ПЕРЕПИСЫВАЕТСЯ, если старый запрос — префикс нового или наоборот (человек
#    стирал), а steps считает, сколько огрызков свернулось в строку.
#
# 3. ЦИФРЫ МАСКИРУЮТСЯ ДО ЗАПИСИ. В вике есть статья про смену номера телефона,
#    и ищут по ней телефоном и ИИН. Текст запроса чувствительнее, чем факт
#    «открыл статью X», а для отчёта конкретный номер не нужен вовсе.
#
# 4. IP НЕ ПИШЕТСЯ, а user_id пишется. Без человека не отличить дыру в контенте
#    от дыры в правах (см. пункт 1) и некому адресовать находку; ip к этому не
#    добавляет ничего.
#
# query_norm — та же свёртка, что в самом поиске (нижний регистр, казахские
# буквы и ё к русским двойникам). Иначе «Қазына» и «казына» лягут разными
# строками отчёта и не склеятся префиксом.
# ─────────────────────────────────────────────────────────────────────────────
_SEARCH_LOG_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS wiki_search_log (
        id             BIGSERIAL PRIMARY KEY,
        user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        -- Снимок отдела, а не join к users: человек переходит между отделами,
        -- а отчёт за прошлый месяц обязан показывать, чей это был запрос ТОГДА.
        department_id  INTEGER,
        space_id       INTEGER,
        query          VARCHAR(200) NOT NULL,
        query_norm     VARCHAR(200) NOT NULL,
        results_count  SMALLINT NOT NULL DEFAULT 0,
        -- Сколько статей человек вообще вправе прочитать в момент запроса.
        perimeter_size SMALLINT NOT NULL DEFAULT 0,
        -- Сколько запросов-огрызков свернулось в эту строку (см. пункт 2).
        steps          SMALLINT NOT NULL DEFAULT 1,
        created_at     TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    # Горячий путь: при КАЖДОЙ записи ищется строка того же человека за
    # последние 30 секунд — без этого индекса свёртка читала бы всю таблицу.
    "CREATE INDEX IF NOT EXISTS idx_wiki_search_log_user "
    "ON wiki_search_log(user_id, created_at DESC);",
    # Индекса на голом created_at здесь НЕТ намеренно. Свёртка префиксов
    # ПЕРЕПИСЫВАЕТ created_at, то есть UPDATE перестаёт быть HOT и трогает
    # каждый индекс по этому полю — и платить за это пришлось бы на каждом
    # нажатии клавиши в поиске. Отчёт за период обходится последовательным
    # чтением: при 100–350 запросах в сутки это тысячи строк, а не миллионы.
    # Главный отчёт вкладки — «искали и не нашли». Частичный индекс: пустых
    # выдач единицы процентов от всех запросов, и полный индекс по тому же
    # полю обошёлся бы дороже без выигрыша.
    "CREATE INDEX IF NOT EXISTS idx_wiki_search_log_empty "
    "ON wiki_search_log(created_at DESC) WHERE results_count = 0;",
]


# ─────────────────────────────────────────────────────────────────────────────
# ПЕРЕНОС СТАТЕЙ ИЗ ВНЕШНЕЙ ВИКИ И ИХ МОДЕРАЦИЯ
#
# Отдельная таблица, а не колонки в wiki_articles, и не расширение CHECK'а
# статуса — по трём причинам, и каждая проверена на этом переносе.
#
# 1. ПРОВЕНАНС ЖИВЁТ ДОЛЬШЕ РЕШЕНИЯ. Строка отвечает на «откуда это взялось»
#    и после того, как статью опубликовали или убрали в архив. Колонка
#    moderation_state в самой статье обнулилась бы вместе с решением, и через
#    месяц никто не ответил бы, что именно приехало из старой вики.
# 2. НОВОГО СТАТУСА НЕ НУЖНО. «Ждёт модерации» — это не состояние текста, а
#    состояние РАБОТЫ над ним. Текст при этом обычный черновик, и его уже
#    правильно скрывают от читателя (wiki/articles.py: status = 'published'
#    OR автор OR can_see_drafts). Добавь седьмой статус — и его пришлось бы
#    разложить по ARTICLE_BUCKETS, по периметру ИИ, по счётчикам и по четырём
#    местам, где статусы перечислены руками.
# 3. ПОВТОРНЫЙ ПРОГОН НЕ ДОЛЖЕН ПЛОДИТЬ КОПИИ. Уникальный индекс по
#    (source, source_id) — единственная надёжная защита от этого: сверка по
#    slug ломается, как только slug в приёмнике занят и к нему дописали «-2».
#
# Вердикт ИИ хранится СНИМКОМ, а не пересчитывается при показе очереди. Вектор
# считает внешний сервис, и открытие очереди из сорока статей означало бы сорок
# обращений наружу на каждое нажатие «Обновить». Снимок берётся один раз, в
# момент переноса — тогда же, когда статья и попадает в приёмник.
# ─────────────────────────────────────────────────────────────────────────────
_MIGRATION_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS wiki_article_imports (
        article_id     INTEGER PRIMARY KEY
                       REFERENCES wiki_articles(id) ON DELETE CASCADE,
        -- Код источника: 'wikijs' — старая корпоративная вика на Wiki.js 2.
        -- Строкой, а не ссылкой на справочник: источников переноса за всё время
        -- два-три, и таблица на три строки читалась бы хуже, чем это поле.
        source         VARCHAR(32) NOT NULL,
        source_id      INTEGER,
        source_slug    VARCHAR(255),
        -- Название и статус в ИСТОЧНИКЕ снимком: в приёмнике статью переименуют
        -- при модерации, а сверять с оригиналом надо будет по тому имени, под
        -- которым она там лежала. Источник к тому моменту уже недоступен.
        source_title   VARCHAR(255),
        source_status  VARCHAR(32),
        -- Вердикт проверки на дубль. 'unique' — ничего похожего не нашли;
        -- degraded отдельным флагом, потому что «не нашли» и «не смогли
        -- посмотреть» — разные ответы, и склеивать их значит врать в обоих.
        dedup_verdict  VARCHAR(16) NOT NULL DEFAULT 'unique',
        dedup_score    NUMERIC(5, 4),
        dedup_match_id INTEGER REFERENCES wiki_articles(id) ON DELETE SET NULL,
        dedup_note     TEXT,
        dedup_degraded BOOLEAN NOT NULL DEFAULT FALSE,
        imported_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        imported_at    TIMESTAMP NOT NULL DEFAULT %(now)s,
        reviewed_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        reviewed_at    TIMESTAMP,
        review_action  VARCHAR(16),
        review_note    TEXT,
        CONSTRAINT wiki_article_imports_verdict_check
            CHECK (dedup_verdict IN ('unique', 'nearby', 'similar', 'duplicate')),
        CONSTRAINT wiki_article_imports_action_check
            CHECK (review_action IS NULL
                   OR review_action IN ('published', 'kept', 'discarded')),
        -- Решение и его автор приходят вместе или не приходят вовсе: строка с
        -- reviewed_at без review_action означала бы «промодерировано неизвестно
        -- как», и очередь показывала бы её как закрытую.
        CONSTRAINT wiki_article_imports_review_check
            CHECK ((reviewed_at IS NULL AND review_action IS NULL)
                   OR (reviewed_at IS NOT NULL AND review_action IS NOT NULL))
    );
    """,
    # Главный запрос очереди — «что ещё не смотрели», и он частичный: закрытых
    # строк со временем становится в разы больше, чем открытых.
    "CREATE INDEX IF NOT EXISTS idx_wiki_article_imports_pending "
    "ON wiki_article_imports (source, imported_at) WHERE reviewed_at IS NULL;",
    # Защита от второго переноса той же статьи. Частичный: source_id пуст у
    # строк, восстановленных по slug, и NULL'ы в уникальном индексе не считаются
    # равными — без WHERE такие строки прошли бы, а смысла в них нет.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_wiki_article_imports_source "
    "ON wiki_article_imports (source, source_id) WHERE source_id IS NOT NULL;",
]


_SPACE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS wiki_space_departments (
        space_id      INTEGER NOT NULL REFERENCES wiki_spaces(id) ON DELETE CASCADE,
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        PRIMARY KEY (space_id, department_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_wiki_space_dept_space "
    "ON wiki_space_departments (space_id);",

    # Тумблеры вкладок. Пустой объект = всё включено (см. space_features).
    "ALTER TABLE wiki_spaces ADD COLUMN IF NOT EXISTS "
    "features JSONB NOT NULL DEFAULT '{}'::jsonb;",
]

# Код пространства, в которое переезжает всё, что было в вике до того, как
# пространство стало границей.
DEFAULT_SPACE_CODE = 'igroup'
DEFAULT_SPACE_NAME = 'iGroup'


def _slugify_ascii(value):
    """Транслитерация в слаг. Дубль правила из routes_structure._slugify.

    Здесь оно нужно ровно один раз — на миграции, когда роутов ещё нет в
    обороте, а импортировать модуль с Flask-зависимостями из схемы нельзя.
    """
    table = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '',
        'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    }
    out = []
    for char in (value or '').strip().lower():
        if char in table:
            out.append(table[char])
        elif char.isalnum():
            out.append(char)
        else:
            out.append('-')
    slug = '-'.join(filter(None, ''.join(out).split('-')))
    return slug[:245] or 'razdel'


def _free_section_slug(cursor, space_id, slug):
    """Свободный слаг внутри пространства: base, base-2, base-3…"""
    candidate, attempt = slug, 1
    while True:
        cursor.execute('SELECT 1 FROM wiki_sections WHERE space_id = %s AND slug = %s',
                       (space_id, candidate))
        if not cursor.fetchone():
            return candidate
        attempt += 1
        candidate = '%s-%d' % (slug[:240], attempt)


def _merge_legacy_spaces(cursor):
    """Одноразовый переезд: бывшие пространства → верхние разделы «iGroup».

    Идемпотентность держится на КОДЕ пространства: как только в базе есть
    пространство с DEFAULT_SPACE_CODE, миграция пройдена и больше не трогает
    ничего. Отдельного флага-таблицы не нужно — код и есть маркер.

    По шагам:
      1. заводит пространство «iGroup»;
      2. каждое АКТИВНОЕ бывшее пространство превращает в верхний раздел этого
         пространства и подвешивает под него его же корневые разделы;
      3. архивные не оборачивает: их разделы переезжают как есть — плодить ради
         архива пять пустых разделов значит переносить мусор дважды;
      4. переписывает слаги, столкнувшиеся при слиянии, — ДО смены space_id,
         иначе UNIQUE (space_id, slug) не даст перенести строку;
      5. удаляет опустевшие строки бывших пространств ТОЛЬКО после переноса
         разделов: на space_id висит ON DELETE CASCADE, и удаление раньше унесло
         бы за собой всё дерево;
      6. переносит границу отдела с публичного раздела на пространство.

    На ПУСТОЙ базе шаги 2–5 вырождаются, а пространство всё равно создаётся:
    вике нужен хотя бы один контейнер, иначе первый раздел некуда положить.
    """
    cursor.execute('SELECT id FROM wiki_spaces WHERE code = %s', (DEFAULT_SPACE_CODE,))
    if cursor.fetchone():
        return

    cursor.execute('SELECT id, name, description, icon, status, position '
                   '  FROM wiki_spaces ORDER BY position, id')
    legacy = cursor.fetchall()

    cursor.execute(
        """
        INSERT INTO wiki_spaces (code, name, description, position)
        VALUES (%s, %s, %s, 0)
        RETURNING id
        """,
        (DEFAULT_SPACE_CODE, DEFAULT_SPACE_NAME,
         'Основное пространство вики: всё, что было в разделе до того, '
         'как пространства стали границей между отделами.'),
    )
    space_id = cursor.fetchone()[0]

    for legacy_id, name, description, icon, status, position in legacy:
        if status == 'active':
            slug = _free_section_slug(cursor, space_id, _slugify_ascii(name))
            cursor.execute(
                """
                INSERT INTO wiki_sections (space_id, parent_section_id, name, slug,
                                           description, icon, position, status)
                VALUES (%s, NULL, %s, %s, %s, %s, %s, 'active')
                RETURNING id
                """,
                (space_id, name, slug, description, icon, position),
            )
            wrapper_id = cursor.fetchone()[0]
            cursor.execute(
                'UPDATE wiki_sections SET parent_section_id = %s '
                ' WHERE space_id = %s AND parent_section_id IS NULL AND id <> %s',
                (wrapper_id, legacy_id, wrapper_id),
            )

        cursor.execute(
            """
            UPDATE wiki_sections s
               SET slug = left(s.slug, 240) || '-' || s.id
             WHERE s.space_id = %(legacy)s
               AND EXISTS (SELECT 1 FROM wiki_sections t
                            WHERE t.space_id = %(space)s AND t.slug = s.slug)
            """,
            {'legacy': legacy_id, 'space': space_id},
        )
        cursor.execute('UPDATE wiki_sections SET space_id = %s WHERE space_id = %s',
                       (space_id, legacy_id))
        cursor.execute('DELETE FROM wiki_spaces WHERE id = %s', (legacy_id,))

    # Граница отдела у пространства — та, что уже выставлена руками у публичного
    # раздела. Придумывать список заново нельзя: он либо шире выставленного (и
    # тогда миграция сама открывает то, что закрывали), либо уже (и тогда молча
    # отбирает доступ). Пусто — пространство остаётся видимым всем.
    cursor.execute(
        """
        INSERT INTO wiki_space_departments (space_id, department_id)
        SELECT DISTINCT %s, department_id FROM wiki_section_public_departments
        ON CONFLICT DO NOTHING
        """,
        (space_id,),
    )


def _article_type_check_statement():
    """Пересобирает CHECK на article_type под текущий ARTICLE_TYPES.

    Ограничение объявлено ВНУТРИ CREATE TABLE, то есть на проде лежит с
    автоматическим именем и со старым списком значений. Добавить тип одним
    правкой кортежа поэтому нельзя: сохранение статьи с новым типом падало бы
    на ограничении 500-й ошибкой, причём молча для того, кто выбрал тип.

    Проверка «нужно ли пересобирать» идёт по ТЕКСТУ определения: именованное
    ограничение обязано упоминать каждое значение из ARTICLE_TYPES. Условие
    «существует ограничение с таким именем» здесь не годится — оно один раз
    создало бы ограничение и навсегда заморозило список: следующий добавленный
    тип не прошёл бы, а причину пришлось бы искать в базе, а не в коде.
    """
    values = ", ".join("'%s'" % name for name in ARTICLE_TYPES)
    fresh = " AND ".join(
        "pg_get_constraintdef(oid) LIKE '%%''%s''%%'" % name for name in ARTICLE_TYPES
    ).replace('%%', '%')
    return """
    DO $$
    DECLARE stale text;
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = 'wiki_articles'::regclass
               AND contype = 'c'
               AND conname = 'wiki_articles_type_chk'
               AND {fresh}
        ) THEN
            FOR stale IN
                SELECT conname FROM pg_constraint
                 WHERE conrelid = 'wiki_articles'::regclass
                   AND contype = 'c'
                   AND pg_get_constraintdef(oid) LIKE '%article_type%'
            LOOP
                EXECUTE format('ALTER TABLE wiki_articles DROP CONSTRAINT %I', stale);
            END LOOP;

            ALTER TABLE wiki_articles ADD CONSTRAINT wiki_articles_type_chk
                CHECK (article_type IN ({values}));
        END IF;
    END $$;
    """.format(fresh=fresh, values=values)


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

    # Гостевой доступ — сразу за правилами разделов: одна из колонок ALTER'ится
    # именно в wiki_section_access_rules, и до неё таблица обязана существовать.
    for statement in _GUEST_STATEMENTS:
        cursor.execute(statement)

    # Пространства — после _ORG_STATEMENTS: список отделов ссылается на
    # departments, а переезд читает wiki_section_public_departments, которая
    # заводится там же.
    for statement in _SPACE_STATEMENTS:
        cursor.execute(statement)
    _merge_legacy_spaces(cursor)

    # Тип статьи: список в ARTICLE_TYPES растёт (последним пришёл 'trainer'), а
    # ограничение в базе создано вместе с таблицей и о новых значениях не знает.
    cursor.execute(_article_type_check_statement())

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

    # Прохождения тренажёров — после базовых таблиц: строка ссылается и на
    # users, и на wiki_articles.
    for statement in _TRAINER_STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW))

    # Перенос из внешней вики — после базовых таблиц: строка ссылается и на
    # wiki_articles, и на users.
    for statement in _MIGRATION_STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW))

    for statement in _PARK_STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW))

    # Офисы — строго после парков: связь ссылается на wiki_taxi_parks.
    for statement in _OFFICE_STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW))

    # Граница пространства у справочников — после обеих групп: функция трогает
    # и парки, и офисы, и акции, и читает wiki_spaces, которую к этому моменту
    # уже завёл _merge_legacy_spaces.
    _scope_directories_to_space(cursor)

    # Граница пространства у журнала — здесь же и по той же причине: функция
    # разбирает историю по разделам, паркам и офисам, а значит все три таблицы
    # к этому моменту должны существовать.
    _scope_audit_to_space(cursor)

    # Выражение генерируемой колонки менять через ALTER нельзя — только
    # пересоздать; «ADD COLUMN IF NOT EXISTS» молча оставит старое определение.
    # Проверка и пересборка вынесены в _regenerate_folded_columns: правило
    # свёртки одно на обе колонки, и обновляться они обязаны вместе.
    rebuilt_columns = _regenerate_folded_columns(cursor)
    rebuilt_search_vector = 'wiki_articles.search_vector' in rebuilt_columns

    for statement in _SEARCH_STATEMENTS:
        cursor.execute(statement)

    # Журнал поисковых запросов. Через .replace, а не вторым аргументом execute:
    # DEFAULT %(now)s без подстановки уехал бы в Postgres буквально — ровно та
    # ошибка, на которую _SEARCH_STATEMENTS выше не наступает только потому,
    # что дат в них нет вовсе.
    for statement in _SEARCH_LOG_STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW))

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

    # ── Разовая заливка внутренних ссылок ────────────────────────────────
    # Таблица wiki_article_links лежит в схеме с первого дня раздела, и всё это
    # время в неё НИКТО не писал: обратные ссылки читались из пустоты, то есть
    # блок «Сюда ссылаются» не показывался никогда и ни у кого. Тела статей при
    # этом ссылки содержат — на момент правки 253 пары в 53 статьях.
    #
    # Разбор — тот же питоновский, что и при сохранении (wiki/links.py). Второй
    # разбор, написанный на SQL, неизбежно разошёлся бы с первым: в Postgres нет
    # urldecode, а слагов с кириллицей в проде 25 — каждая четвёртая ссылка
    # потерялась бы молча.
    #
    # Только когда таблица ПУСТА. Дальше её поддерживает сохранение статьи
    # (edit.link_content_articles во всех четырёх путях записи тела), и разбирать
    # все тела на каждом старте незачем.
    cursor.execute('SAVEPOINT wiki_links_backfill')
    try:
        cursor.execute('SELECT 1 FROM wiki_article_links LIMIT 1')
        if cursor.fetchone() is None:
            from .links import article_slugs
            cursor.execute(
                "SELECT id, content FROM wiki_articles WHERE content LIKE '%article=%'")
            _sources = {}          # слаг цели -> id статей, которые на неё ссылаются
            for _article_id, _content in cursor.fetchall():
                for _slug in article_slugs(_content):
                    _sources.setdefault(_slug, set()).add(_article_id)
            if _sources:
                cursor.execute(
                    'SELECT slug, id FROM wiki_articles WHERE slug = ANY(%s::text[])',
                    (list(_sources),))
                _pairs = sorted({(_src, _target)
                                 for _slug, _target in cursor.fetchall()
                                 for _src in _sources[_slug]
                                 if _src != _target})
                if _pairs:
                    cursor.execute(
                        """
                        INSERT INTO wiki_article_links (source_id, target_id)
                        SELECT s, t FROM unnest(%s::int[], %s::int[]) AS u(s, t)
                        ON CONFLICT (source_id, target_id) DO NOTHING
                        """,
                        ([_p[0] for _p in _pairs], [_p[1] for _p in _pairs]),
                    )
    except Exception:
        cursor.execute('ROLLBACK TO SAVEPOINT wiki_links_backfill')
        import logging
        logging.warning('Раздел «Вики»: разовая заливка внутренних ссылок не удалась — '
                        '«Сюда ссылаются» наполнится по мере правки статей')
    else:
        cursor.execute('RELEASE SAVEPOINT wiki_links_backfill')

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
