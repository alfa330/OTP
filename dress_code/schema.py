# -*- coding: utf-8 -*-
"""Схема раздела «Дресс-код». Все таблицы с префиксом dress_code_.

Идемпотентно (IF NOT EXISTS), вызывается при старте из Database._init_db через
init_dress_code_schema(cursor) под своим SAVEPOINT: сломанная миграция раздела не
роняет базу, а раздел честно отвечает «ещё не развёрнут» (schema_is_ready).

    dress_code_settings   одна строка: какие отделы и роли снимают фото
    dress_code_rules      версии текста положения (id = номер версии)
    dress_code_checks     фото дня и вывод ИИ, решение руководителя — отдельно
    dress_code_decisions  журнал решений: только дописывается

Решения, которые видны в DDL:

* Предварительный (ИИ) и итоговый (руководитель) статусы хранятся раздельно
  (ТЗ п. 5): ai_status не переписывается решением, решение — своими колонками
  плюс строкой журнала, первоначальный вывод остаётся (ТЗ п. 6).
* Одно «первое» фото в день у человека — уникальным частичным индексом: двойное
  нажатие «Отправить» не заведёт два фото. Повторное фото (просьба руководителя
  или исправление после нарушения) — строка с retake_of, по одному на фото.
* Время — TIMESTAMP по Алматы, как во всём портале.
"""

from .rules import DEFAULT_RULES

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS dress_code_settings (
        id              SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
        department_ids  INTEGER[] NOT NULL DEFAULT '{}',
        roles           TEXT[] NOT NULL DEFAULT '{}',
        updated_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at      TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    # По умолчанию — фронт-офис (ТЗ п. 1), рядовые и стажёры.
    """
    INSERT INTO dress_code_settings (id, department_ids, roles)
    SELECT 1,
           COALESCE((SELECT array_agg(id ORDER BY id) FROM departments WHERE code = 'front_office'), '{}'),
           ARRAY['operator', 'trainee']
    ON CONFLICT (id) DO NOTHING
    """,
    """
    CREATE TABLE IF NOT EXISTS dress_code_rules (
        id          SERIAL PRIMARY KEY,
        body        TEXT NOT NULL,
        created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at  TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    """
    INSERT INTO dress_code_rules (body)
    SELECT %(default_rules)s
    WHERE NOT EXISTS (SELECT 1 FROM dress_code_rules)
    """,
    """
    CREATE TABLE IF NOT EXISTS dress_code_checks (
        id                     SERIAL PRIMARY KEY,
        user_id                INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id          INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        city                   VARCHAR(255),
        day                    DATE NOT NULL,
        taken_at               TIMESTAMP NOT NULL DEFAULT %(now)s,
        retake_of              INTEGER REFERENCES dress_code_checks(id) ON DELETE CASCADE,
        source                 VARCHAR(16) NOT NULL DEFAULT 'camera',
        attempts               SMALLINT NOT NULL DEFAULT 1,
        photo_bucket           TEXT NOT NULL,
        photo_path             TEXT NOT NULL,
        photo_width            INTEGER,
        photo_height           INTEGER,
        rules_version          INTEGER REFERENCES dress_code_rules(id) ON DELETE SET NULL,
        ai_status              VARCHAR(16) NOT NULL DEFAULT 'pending',
        ai_quality             VARCHAR(32),
        ai_visible             JSONB NOT NULL DEFAULT '[]'::jsonb,
        ai_reasons             JSONB NOT NULL DEFAULT '[]'::jsonb,
        ai_remarks             JSONB NOT NULL DEFAULT '[]'::jsonb,
        ai_note                TEXT,
        ai_model               VARCHAR(64),
        ai_attempts            SMALLINT NOT NULL DEFAULT 0,
        ai_error               TEXT,
        analyzed_at            TIMESTAMP,
        decision               VARCHAR(16),
        decision_comment       TEXT,
        decided_by             INTEGER REFERENCES users(id) ON DELETE SET NULL,
        decided_at             TIMESTAMP,
        is_repeat              BOOLEAN NOT NULL DEFAULT FALSE,
        acknowledged_at        TIMESTAMP,
        reviewers_notified_at  TIMESTAMP,
        CONSTRAINT dress_code_checks_ai_status_check
            CHECK (ai_status IN ('pending', 'ok', 'violation', 'unclear')),
        CONSTRAINT dress_code_checks_decision_check
            CHECK (decision IS NULL OR decision IN ('confirmed', 'no_violation', 'retake'))
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_dress_code_checks_primary
        ON dress_code_checks (user_id, day) WHERE retake_of IS NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_dress_code_checks_retake
        ON dress_code_checks (retake_of) WHERE retake_of IS NOT NULL
    """,
    "CREATE INDEX IF NOT EXISTS idx_dress_code_checks_day ON dress_code_checks (day DESC)",
    """
    CREATE INDEX IF NOT EXISTS idx_dress_code_checks_pending
        ON dress_code_checks (taken_at) WHERE ai_status = 'pending'
    """,
    """
    CREATE TABLE IF NOT EXISTS dress_code_decisions (
        id          SERIAL PRIMARY KEY,
        check_id    INTEGER NOT NULL REFERENCES dress_code_checks(id) ON DELETE CASCADE,
        decision    VARCHAR(16) NOT NULL,
        comment     TEXT,
        actor_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        actor_name  VARCHAR(255),
        created_at  TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_dress_code_decisions_check
        ON dress_code_decisions (check_id, created_at)
    """,
]


def init_dress_code_schema(cursor):
    params = {'now': _NOW, 'default_rules': DEFAULT_RULES}
    for statement in _STATEMENTS:
        if '%(default_rules)s' in statement:
            cursor.execute(statement.replace('%(now)s', _NOW), {'default_rules': DEFAULT_RULES})
        else:
            cursor.execute(statement % params if '%(' in statement else statement)


def schema_is_ready(cursor):
    # Без «public.»: таблица ищется по search_path — тесты на настоящем Postgres
    # разворачивают раздел в своей схеме.
    cursor.execute("SELECT to_regclass('dress_code_decisions') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
