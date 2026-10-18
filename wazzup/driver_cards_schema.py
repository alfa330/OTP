"""Карточка водителя в чатах Верификаторов: версии карточки и очередь записи в amoCRM.

Каждое сохранение — новая строка: история карточки и есть эти строки, текущая
карточка чата — последняя. Строка же служит очередью записи в сделку amoCRM
(amo_*): аренда, попытки, итог. Хранится без ретеншна переписки — это рабочая
запись верификатора, а не сообщение, и в отличие от чата она нужна и через
45 дней. Поэтому имя и номер контакта сняты в строку при сохранении: строка
чата к тому времени может уйти по сроку хранения.
"""


def init_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_driver_cards (
            id UUID PRIMARY KEY,
            account TEXT NOT NULL CHECK (account = 'op'),
            channel_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            client_card_id UUID NOT NULL,
            author_id BIGINT NOT NULL,
            author_name TEXT NOT NULL,
            contact_name TEXT,
            contact_phone TEXT,
            driver_id TEXT CHECK (driver_id ~ '^[0-9a-f]{32}$'),
            park_enum_id BIGINT,
            park_name TEXT,
            city_enum_id BIGINT,
            city_name TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            amo_state TEXT NOT NULL DEFAULT 'pending'
                CHECK (amo_state IN ('pending', 'synced', 'failed', 'no_deal', 'superseded')),
            amo_lead_id BIGINT,
            amo_attempts INTEGER NOT NULL DEFAULT 0,
            amo_next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            amo_lease_token UUID,
            amo_lease_until TIMESTAMPTZ,
            amo_error TEXT,
            amo_synced_at TIMESTAMPTZ,
            -- С какого момента идут повторы: сохранение или «Повторить». Сутки
            -- на повторы считаются отсюда, а не от создания карточки.
            amo_queued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (author_id, client_card_id),
            CHECK (driver_id IS NOT NULL OR park_enum_id IS NOT NULL OR city_enum_id IS NOT NULL),
            CHECK ((park_enum_id IS NULL) = (park_name IS NULL)),
            CHECK ((city_enum_id IS NULL) = (city_name IS NULL)),
            CHECK (channel_id NOT IN ('99df6893-fb6b-4e1d-a78e-9e6e6b37abb2',
                                     'a4bccb5e-5d41-483d-b7c1-a1079685577d'))
        );
        ALTER TABLE wazzup_driver_cards
            ADD COLUMN IF NOT EXISTS amo_queued_at TIMESTAMPTZ NOT NULL DEFAULT now();
        -- Ожидания сделки нового чата считаются отдельно от сбоев amoCRM: иначе
        -- два сбоя подряд съедали бы эти ожидания, а ожидания — паузу сбоя.
        ALTER TABLE wazzup_driver_cards
            ADD COLUMN IF NOT EXISTS amo_no_deal_attempts INTEGER NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_wazzup_driver_cards_chat
            ON wazzup_driver_cards(account, channel_id, chat_id, created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_wazzup_driver_cards_created
            ON wazzup_driver_cards(account, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_wazzup_driver_cards_due
            ON wazzup_driver_cards(amo_next_attempt_at) WHERE amo_state = 'pending';

        -- Справочники «Таксопарк привлечения» и «Город привлечения» из amoCRM:
        -- последний удачный снимок. Без него карточку нечем проверить, пока
        -- amoCRM недоступна или процесс только поднялся.
        CREATE TABLE IF NOT EXISTS wazzup_driver_card_options (
            key TEXT PRIMARY KEY CHECK (key = 'amo'),
            payload JSONB NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        -- Данные водителя из CRM yataxi по ID (wazzup/driver_info.py): кэш
        -- ответа, общий для всех процессов. payload NULL — «водитель не найден».
        -- Хранится 30 дней (в нём ФИО, телефон и ИИН) — чистит driver_info.
        CREATE TABLE IF NOT EXISTS wazzup_driver_info (
            driver_id TEXT PRIMARY KEY CHECK (driver_id ~ '^[0-9a-f]{32}$'),
            payload JSONB,
            found BOOLEAN NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (found = (payload IS NOT NULL))
        );
        -- Справочник регистрации CRM: город и бренд каждого парка Яндекса,
        -- подписи тарифов. Последний удачный снимок.
        CREATE TABLE IF NOT EXISTS wazzup_crm_dictionaries (
            key TEXT PRIMARY KEY CHECK (key = 'registration'),
            payload JSONB NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        -- Профессия водителя из кабинета Fleet (wazzup/driver_profession.py) с
        -- подписью: кэш на неделю, общий для всех процессов. found = FALSE —
        -- в этом парке водителя нет. Личных данных нет: ID и код профессии.
        CREATE TABLE IF NOT EXISTS wazzup_driver_professions (
            driver_id TEXT PRIMARY KEY CHECK (driver_id ~ '^[0-9a-f]{32}$'),
            park_id TEXT NOT NULL CHECK (park_id ~ '^[0-9a-f]{32}$'),
            found BOOLEAN NOT NULL,
            profession_id TEXT,
            profession_name TEXT,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (found OR profession_id IS NULL)
        );
        -- Откуда ответ: 'list' — список исполнителей. Строки без него — из карточки
        -- исполнителя первой выкладки (у таксистов профессии там нет), они устарели.
        ALTER TABLE wazzup_driver_professions ADD COLUMN IF NOT EXISTS source TEXT;
        -- Справочник профессий парка из кабинета: подписи по-русски, последний снимок.
        CREATE TABLE IF NOT EXISTS wazzup_fleet_professions (
            park_id TEXT PRIMARY KEY CHECK (park_id ~ '^[0-9a-f]{32}$'),
            names JSONB NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE OR REPLACE FUNCTION wazzup_notify_driver_card() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            -- Как у заметок: в NOTIFY только адрес строки. Содержимое читает
            -- общий слушатель потока после COMMIT (wazzup/realtime.py).
            PERFORM pg_notify('wazzup_pilot_events', json_build_object(
                'account', NEW.account, 'channelId', NEW.channel_id, 'chatId', NEW.chat_id,
                'messageId', 'card:' || NEW.id::text, 'cardId', NEW.id::text,
                'kind', 'card', 'statusOnly', FALSE, 'affectsList', FALSE,
                'emittedAt', EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::text);
            RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS wazzup_driver_card_notify_insert ON wazzup_driver_cards;
        CREATE TRIGGER wazzup_driver_card_notify_insert AFTER INSERT ON wazzup_driver_cards
            FOR EACH ROW EXECUTE FUNCTION wazzup_notify_driver_card();
        -- Аренда и счётчик попыток — внутреннее дело очереди: экрану важны
        -- только итог записи, её ошибка и сделка.
        DROP TRIGGER IF EXISTS wazzup_driver_card_notify_update ON wazzup_driver_cards;
        CREATE TRIGGER wazzup_driver_card_notify_update AFTER UPDATE ON wazzup_driver_cards
            FOR EACH ROW
            WHEN ((OLD.amo_state, OLD.amo_error, OLD.amo_lead_id)
                  IS DISTINCT FROM (NEW.amo_state, NEW.amo_error, NEW.amo_lead_id))
            EXECUTE FUNCTION wazzup_notify_driver_card();
    """)
