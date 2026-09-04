"""Схема раздела «Ограничитель Перезвона». Все таблицы с префиксом oktell_guard_.

Идемпотентно (CREATE TABLE/INDEX IF NOT EXISTS), вызывается один раз при старте
из Database._init_db через init_oktell_guard_schema(cursor) — как init_crm_schema.

Модель:

    oktell_guard_settings     общие настройки: адрес Oktell, порог по умолчанию,
                              пин сертификата, вкл/выкл. Одна строка (id = 1).
    oktell_guard_user_rules   на сотрудника: личный порог (кто он в Oktell —
                              известно из users.sip_number).
    oktell_guard_violations   журнал выбросов — то, что показывается в отчёте.
    oktell_guard_agents       живость агентов: кто, где, когда отметился.
    oktell_guard_releases     версии агента: метаданные здесь, сам файл в GCS.
    oktell_guard_tokens       личный токен сотрудника, выданный при скачивании.
    oktell_guard_managed_days пометка «в этот день работал через наше приложение».

Почему файл в GCS, а не на диске и не в базе. Диск на Render эфемерный —
загруженный exe исчезал бы после каждого деплоя. База выдержала бы (15 МБ в
bytea, TOAST), но раздача шла бы через наш единственный инстанс: после выпуска
версии сотня агентов обновляется разом, это гигабайты через тот же процесс,
который в этот момент обслуживает интерфейс. GCS в проекте уже используется
(аватары, вложения задач и событий) — там же лежит и exe, а агент с браузером
качают по подписанной ссылке напрямую у Google.

Токены: файл в хранилище один на всех, а личный токен выдаётся при скачивании
и уезжает в ИМЕНИ файла — агент читает его при первом запуске и запоминает.
Так подделка перестаёт быть анонимной: присланное всегда подписано конкретным
человеком, а токен можно отозвать. Храним только отпечаток (sha256), самого
значения у нас нет.

Пометка «работал через наше приложение» (oktell_guard_managed_days) ставится по
heartbeat: агент видит сессию Oktell в управляемом окне. Сейчас это только
отметка для отчёта; засчитывать по ней смену — отдельное решение на будущее.

Логин в Oktell — это SIP-номер оператора, он уже лежит в `users.sip_number`
и правится в разделе «Настройки SIP». Поэтому своего поля под логин здесь нет:
второй столбец с тем же значением означал бы два источника правды и вопрос
«какой из них верный», когда сотруднику меняют номер. Агент присылает логин из
cookie `__oktelllogin`, сервер сопоставляет его с `users.sip_number`.
"""


def init_oktell_guard_schema(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oktell_guard_settings (
            id                  SMALLINT PRIMARY KEY DEFAULT 1,
            enabled             BOOLEAN NOT NULL DEFAULT FALSE,
            dry_run             BOOLEAN NOT NULL DEFAULT TRUE,
            oktell_url          TEXT NOT NULL DEFAULT '',
            cert_spki           TEXT NOT NULL DEFAULT '',
            threshold_s         INTEGER NOT NULL DEFAULT 180,
            warn_before_s       INTEGER NOT NULL DEFAULT 30,
            recall_reason_id    INTEGER NOT NULL DEFAULT 2,
            call_state_strings  JSONB NOT NULL DEFAULT '["fullbusy","talk","dial","call","ring"]'::jsonb,
            heartbeat_interval_s INTEGER NOT NULL DEFAULT 60,
            updated_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
            updated_at          TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
            CONSTRAINT oktell_guard_settings_single_row CHECK (id = 1)
        );
    """)
    # Строка настроек одна и создаётся сразу: половина кода иначе занималась бы
    # проверкой «а есть ли она». Выключено по умолчанию — включает человек.
    cursor.execute("""
        INSERT INTO oktell_guard_settings (id) VALUES (1)
        ON CONFLICT (id) DO NOTHING;
    """)

    # threshold_s = NULL означает «общий порог из настроек».
    # Состояние разговора у Oktell называется usFullbusy (числовой код 5) —
    # выяснено на живых звонках 19.08.2026. Прежние догадки talk/dial/call/ring
    # не совпадали ни с чем, поэтому звонок НЕ обнулял накопленное время, и
    # оператор, честно отзвонивший смену, всё равно копил выброс.
    cursor.execute("""
        ALTER TABLE oktell_guard_settings
        ADD COLUMN IF NOT EXISTS call_state_ids JSONB NOT NULL DEFAULT '[5]'::jsonb;
    """)
    cursor.execute("""
        UPDATE oktell_guard_settings
           SET call_state_strings = '["fullbusy","talk","dial","call","ring"]'::jsonb
         WHERE call_state_strings = '["talk","dial","call","ring"]'::jsonb;
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oktell_guard_user_rules (
            user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            threshold_s INTEGER,
            enabled     BOOLEAN NOT NULL DEFAULT TRUE,
            updated_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
            updated_at  TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
        );
    """)

    # client_key — ключ идемпотентности от агента: один выброс не должен попасть
    # в отчёт дважды из-за повторной отправки при обрыве связи.
    #
    # verified: 'confirmed' | 'rejected' | 'pending'. Программа стоит на
    # компьютере сотрудника, значит сказать она может что угодно — вплоть до
    # выдуманных выбросов на коллегу. Поэтому каждый факт сверяется с историей
    # статусов самого Oktell, и в отчёт идёт только подтверждённое.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oktell_guard_violations (
            id            BIGSERIAL PRIMARY KEY,
            user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
            sip_number    VARCHAR(64) NOT NULL DEFAULT '',
            happened_at   TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
            seconds       INTEGER NOT NULL DEFAULT 0,
            threshold_s   INTEGER NOT NULL DEFAULT 0,
            reason        VARCHAR(64) NOT NULL DEFAULT 'recall_timeout',
            hostname      VARCHAR(128) NOT NULL DEFAULT '',
            windows_user  VARCHAR(128) NOT NULL DEFAULT '',
            agent_version VARCHAR(32) NOT NULL DEFAULT '',
            dry_run       BOOLEAN NOT NULL DEFAULT FALSE,
            client_key    VARCHAR(128) NOT NULL DEFAULT '',
            verified      VARCHAR(16) NOT NULL DEFAULT 'pending',
            verified_note TEXT NOT NULL DEFAULT '',
            reported_by   INTEGER REFERENCES users(id) ON DELETE SET NULL
        );
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS oktell_guard_violations_client_key_idx
        ON oktell_guard_violations (client_key)
        WHERE client_key <> '';
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS oktell_guard_violations_day_idx
        ON oktell_guard_violations (happened_at DESC);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS oktell_guard_violations_verified_idx
        ON oktell_guard_violations (verified, happened_at DESC);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS oktell_guard_violations_user_idx
        ON oktell_guard_violations (user_id, happened_at DESC);
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oktell_guard_agents (
            agent_id       VARCHAR(160) PRIMARY KEY,
            user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
            sip_number     VARCHAR(64) NOT NULL DEFAULT '',
            hostname       VARCHAR(128) NOT NULL DEFAULT '',
            windows_user   VARCHAR(128) NOT NULL DEFAULT '',
            agent_version  VARCHAR(32) NOT NULL DEFAULT '',
            managed_window BOOLEAN NOT NULL DEFAULT FALSE,
            session_present BOOLEAN NOT NULL DEFAULT FALSE,
            unmanaged_count INTEGER NOT NULL DEFAULT 0,
            last_seen_at   TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS oktell_guard_agents_seen_idx
        ON oktell_guard_agents (last_seen_at DESC);
    """)

    # rule_alive: считает ли правило ПРЯМО СЕЙЧАС, а не «стоит ли оно».
    # Разница стоила двух недель тишины: правило живёт в окне и слышит статусы
    # только через сокеты, созданные уже подменённым WebSocket. Если документ
    # загрузился раньше нашей регистрации, код в странице есть, а кадры мимо —
    # счётчик стоит на нуле. Снаружи это выглядело как исправный ограничитель
    # с пустым отчётом (04.09.2026: 334 с в «Перезвоне» при пороге 180 — ноль).
    # NULL = сказать нечего (нет окна или сессии).
    cursor.execute("""
        ALTER TABLE oktell_guard_agents
        ADD COLUMN IF NOT EXISTS rule_alive BOOLEAN;
    """)
    cursor.execute("""
        ALTER TABLE oktell_guard_agents
        ADD COLUMN IF NOT EXISTS rule_sockets INTEGER NOT NULL DEFAULT 0;
    """)
    cursor.execute("""
        ALTER TABLE oktell_guard_agents
        ADD COLUMN IF NOT EXISTS rule_seconds INTEGER NOT NULL DEFAULT 0;
    """)
    cursor.execute("""
        ALTER TABLE oktell_guard_agents
        ADD COLUMN IF NOT EXISTS rule_version VARCHAR(64) NOT NULL DEFAULT '';
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oktell_guard_releases (
            id          SERIAL PRIMARY KEY,
            version     VARCHAR(32) NOT NULL,
            filename    VARCHAR(128) NOT NULL DEFAULT 'OktellRecallGuard.exe',
            sha256      CHAR(64) NOT NULL,
            size_bytes  BIGINT NOT NULL,
            gcs_bucket  VARCHAR(255) NOT NULL DEFAULT '',
            gcs_path    VARCHAR(512) NOT NULL DEFAULT '',
            notes       TEXT NOT NULL DEFAULT '',
            is_current  BOOLEAN NOT NULL DEFAULT TRUE,
            uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            uploaded_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
        );
    """)
    # Текущая версия ровно одна: агенты обновляются по ней, и «две текущие»
    # означали бы, что половина машин уедет не туда.
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS oktell_guard_releases_current_idx
        ON oktell_guard_releases (is_current)
        WHERE is_current;
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS oktell_guard_releases_version_idx
        ON oktell_guard_releases (version);
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oktell_guard_tokens (
            id           SERIAL PRIMARY KEY,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash   CHAR(64) NOT NULL,
            issued_at    TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
            last_used_at TIMESTAMP,
            revoked_at   TIMESTAMP,
            note         TEXT NOT NULL DEFAULT ''
        );
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS oktell_guard_tokens_hash_idx
        ON oktell_guard_tokens (token_hash);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS oktell_guard_tokens_user_idx
        ON oktell_guard_tokens (user_id, issued_at DESC);
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oktell_guard_managed_days (
            user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            day           DATE NOT NULL,
            first_seen_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
            last_seen_at  TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
            samples       INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, day)
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS oktell_guard_managed_days_day_idx
        ON oktell_guard_managed_days (day DESC);
    """)
