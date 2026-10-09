"""Схема рабочего места верификатора в «Чатах ОП» (wazzup/access.py, wazzup/shift.py).

Идемпотентно, разворачивается при старте из `Database._init_db` через
`init_wazzup_workspace_schema(cursor)` под своим SAVEPOINT.

Три своих таблицы и ни одной правки чужих. Подтверждённый доступ лежит отдельной
таблицей, а не колонкой в `user_sessions`: DDL по ней при старте берёт
AccessExclusiveLock, пока прежний инстанс ещё отвечает запросам с авторизацией,
— на этом деплой уже ловил взаимную блокировку.

Сами статусы смены отдельной таблицы не получили: они ложатся в общий
`operator_status_events` тем же путём, что события iCORE Phone.
"""


def init_wazzup_workspace_schema(cursor):
    # Строка на сессию: доступ выдаётся устройству, а не человеку, и уходит
    # вместе с сессией (ON DELETE CASCADE и проверка revoked_at в запросе).
    # Здесь же ответ на вопрос «кто открыл и чьим кодом»: своего журнала нет,
    # а общий (`session_access_events`) про другой ключ — запись «открыт доступ»
    # в нём читалась бы как доступ к «Вики» и оценкам.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_chat_access (
            session_id UUID PRIMARY KEY REFERENCES user_sessions(session_id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            code_recipient_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_wazzup_chat_access_user
            ON wazzup_chat_access(user_id);
    """)
    # Скан супервайзера, ждущий код из Telegram. Кода в строке нет — только его
    # отпечаток (wazzup.access.hash_code). Скан живёт своим сроком, а не сроком
    # QR: тот истекает через пять минут, а код ещё надо получить и ввести.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_chat_access_challenges (
            id UUID PRIMARY KEY,
            session_id UUID NOT NULL REFERENCES user_sessions(session_id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            approver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            recipient_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            code_hash TEXT NOT NULL,
            attempts SMALLINT NOT NULL DEFAULT 0,
            sent_count SMALLINT NOT NULL DEFAULT 1,
            last_sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_wazzup_chat_challenges_open
            ON wazzup_chat_access_challenges(session_id, approver_id)
            WHERE consumed_at IS NULL;
    """)
    # Когда портал человека последний раз подавал признаки жизни. Строка на
    # человека, а не журнал: сторожу нужна только последняя отметка. Время —
    # naive Asia/Almaty, как event_at статусов, с которыми его сравнивают.
    # Индекса по last_seen_at нет намеренно: строк столько, сколько верификаторов.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_workspace_presence (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            last_seen_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
