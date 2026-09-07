"""Схема раздела «Рассылки». Все таблицы с префиксом driver_mailing.

Идемпотентно (CREATE TABLE/INDEX IF NOT EXISTS), вызывается один раз при старте
из Database._init_db через init_driver_mailings_schema(cursor) — как
init_fleet_edm_schema и init_parcels_schema.

Модель:

    driver_mailings           одна рассылка ПОРТАЛА: текст, фильтры, автор, свод
    driver_mailing_targets    её же в разрезе диспетчерских: по строке на парк
    driver_mailing_templates  заготовки текста и фильтров
    driver_mailing_parks      кэш «где вообще разрешена рассылка»

ПОЧЕМУ У РАССЫЛКИ ОТДЕЛЬНАЯ ТАБЛИЦА ЦЕЛЕЙ. В кабинете нет понятия «отправить в
несколько диспетчерских»: парк задаётся заголовком x-park-id, и одна отправка —
это ровно один парк. Человек же в портале пишет текст один раз и отмечает пять
диспетчерских, то есть одна рассылка портала = N независимых рассылок кабинета.
У каждой свой идентификатор в кабинете, своё время отправки, свой отказ (403
«нет прав», лимит суток исчерпан) и своё окно отзыва в 300 секунд. Держать это
в одной строке нечем: пришлось бы складывать пять разных исходов в одно поле
status и пять разных id в одну строку — и «отозвать там, где ещё можно» стало бы
невыполнимым. Отсюда же сводный status рассылки со значением 'partial': ушло не
везде — обычный, а не исключительный исход.

fleet_mailing_id может остаться NULL, и это не поломка. Ответ кабинета на
отправку — 204 БЕЗ ТЕЛА, идентификатора он не возвращает; мы добываем его сразу
после отправки, разыскивая свежую рассылку по совпадению заголовка. Если не
нашли — честно пишем NULL: журнал скажет, что связать запись с кабинетом не
удалось, а отзыв для такой строки недоступен (отзывать нечего, id неизвестен).
Врать «отправлено и всё в порядке» здесь нельзя.

ПОЧЕМУ КЭШИРУЕМ СПИСОК ПАРКОВ. У аккаунта 90 диспетчерских, а рассылка
разрешена в пяти: остальные 85 отвечают 403 no_permissions на запрос лимитов.
Узнать это можно только опросом — по запросу на парк. Список меняется на
стороне Яндекса, поэтому зашить пятёрку в код нельзя; но и платить 90 запросов
(около десяти секунд ожидания) при каждом входе в раздел не за что. Отсюда
таблица с checked_at: раздел открывается мгновенно из кэша, а пересканирование —
отдельная кнопка. В кэше лежат и запрещённые парки тоже: иначе следующий скан не
отличил бы «парк проверен и запрещён» от «парк ещё не проверяли».

ПОЧЕМУ idempotency_key UNIQUE. Отправка синхронная и занимает пару секунд, всё
это время человек смотрит на неотвеченную кнопку — и жмёт её второй раз. Цена
второго нажатия здесь не «лишняя строка в журнале», а вторая настоящая рассылка
тысяче водителей, которую уже не вернуть (окно отзыва общее, 300 секунд). Токен
генерируется на фронте ОДИН раз на попытку, и UNIQUE делает повторную вставку
невозможной на уровне базы: второй запрос не создаёт рассылку, а находит
существующую и возвращает её же (см. queries.create_mailing). Проверкой «а нет
ли такой же рассылки за последнюю минуту» это не заменяется: две одинаковые
рассылки в разные наборы парков — законный сценарий.
"""

DRIVER_MAILINGS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS driver_mailings (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by          INTEGER,
    created_by_name     TEXT,
    title               TEXT NOT NULL,
    message             TEXT NOT NULL,        -- итоговый текст, как ушёл в кабинет
    message_kk          TEXT,                 -- части двуязычного составителя,
    message_ru          TEXT,                 -- чтобы «Повторить» открывало те же поля
    filters             JSONB NOT NULL DEFAULT '{}'::jsonb,
    recipients_estimate INTEGER,              -- сколько обещал предпросмотр на момент отправки
    status              TEXT NOT NULL DEFAULT 'sending'
                        CHECK (status IN ('sending','sent','partial','failed','revoked')),
    idempotency_key     TEXT UNIQUE,          -- защита от двойного клика
    finished_at         TIMESTAMPTZ,
    error               TEXT
);
CREATE INDEX IF NOT EXISTS idx_driver_mailings_created ON driver_mailings(created_at DESC);

CREATE TABLE IF NOT EXISTS driver_mailing_targets (
    mailing_id          BIGINT NOT NULL REFERENCES driver_mailings(id) ON DELETE CASCADE,
    park_id             TEXT NOT NULL,
    park_name           TEXT,
    park_city           TEXT,
    recipients_estimate INTEGER,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','sent','failed','revoked')),
    fleet_mailing_id    TEXT,                 -- id в кабинете; NULL = связать не удалось
    sent_at             TIMESTAMPTZ,
    revoked_at          TIMESTAMPTZ,
    error               TEXT,
    error_code          TEXT,
    PRIMARY KEY (mailing_id, park_id)
);
CREATE INDEX IF NOT EXISTS idx_driver_mailing_targets_fleet
    ON driver_mailing_targets(fleet_mailing_id);

CREATE TABLE IF NOT EXISTS driver_mailing_templates (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    message_kk      TEXT,
    message_ru      TEXT,
    message         TEXT NOT NULL DEFAULT '',
    filters         JSONB NOT NULL DEFAULT '{}'::jsonb,
    park_ids        JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by      INTEGER,
    created_by_name TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Кэш «где вообще разрешена рассылка»: 90 парков = 90 запросов, на каждый вход
-- в раздел это десять секунд ожидания на пустом месте.
CREATE TABLE IF NOT EXISTS driver_mailing_parks (
    park_id         TEXT PRIMARY KEY,
    name            TEXT,
    city            TEXT,
    is_enabled      BOOLEAN NOT NULL DEFAULT FALSE,
    status          TEXT,
    max_title       INTEGER,
    max_message     INTEGER,
    per_day         INTEGER,
    revoke_seconds  INTEGER,
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def init_driver_mailings_schema(cursor):
    """Создаёт таблицы раздела. Курсор приходит снаружи: инициализация схемы идёт
    одной транзакцией со всеми остальными разделами."""
    cursor.execute(DRIVER_MAILINGS_SCHEMA_SQL)
