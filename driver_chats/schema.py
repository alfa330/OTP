"""Схема раздела «Чаты водителей». Таблицы dch_events и dch_message_cache.

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS. Разворачивается один раз при
старте из Database._init_db через init_driver_chats_schema(cursor).

Ключевые решения, которые видны в DDL:

* **Журнал — одна таблица с видом события, а не три.** Постановка просит видеть
  «кто берёт скрины и кто нажимает «Передан» и в какой чат он был направлен».
  Это одна лента действий одного человека по одному водителю: искал -> открыл
  -> передал. Разложенная по трём таблицам, она собиралась бы обратно
  UNION-ом на каждом открытии журнала и на каждой выгрузке.

* **Данные пользователя — снимок.** Рядом с `user_id` лежат `user_name`,
  `user_role`, `department_id`. Журнал отвечает на вопрос «кто это сделал
  ТОГДА»: человек меняет отдел, увольняется, роль ему заменяют назначением
  главой. Тот же приём, что у `wiki_article_views_log` со снимком отдела и
  должности.

* **Адрес чата хранится тремя полями сразу** — `client_id`, `dialog_id`,
  `request_id`. Постановка требует знать, «в какой чат он был направлен», а у
  Chat2Desk это три разных ключа: клиент (водитель), диалог (переписка) и
  заявка (обращение внутри переписки). Комментарий адресуется клиенту, а
  показывается в диалоге; заявка нужна, чтобы связать запись с метриками
  чат-менеджера в c2d_requests.

* **CHECK на вид события намеренно НЕ ставим.** Новый вид (скажем, «скопировал
  текст») не должен требовать миграции живой базы; неизвестный вид журнал
  показывает нейтральной строкой. То же решение, что у parcels.EVENT_KINDS.

* **Кеш переписки — своя таблица, а не c2d_chat_snapshots.** У той UNIQUE по
  request_id и upsert, ПЕРЕЗАПИСЫВАЮЩИЙ messages (database.py: ON CONFLICT
  (request_id) DO UPDATE SET messages = EXCLUDED.messages). На тех сообщениях
  держатся цитаты супервайзера в уже выставленных оценках: открытие чата
  оператором затёрло бы снимок, по которому оценивали чат-менеджера, и
  подсветка цитат поехала бы. Раздел не имеет права трогать чужие данные ради
  своего кеша.

* **Телефон хранится нормализованным.** В c2d_requests он лежит как пришёл от
  вендора (11, 12, 14, 15 и даже 31 знак), и поиск по нему без приведения молча
  ничего не находит у части водителей. Нормализуем на входе — и в журнале, и в
  кеше лежит один формат, по которому потом строится выгрузка.
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Виды событий журнала. Порядок = порядок жизни: искал -> открыл -> передал.
EVENT_KINDS = ('search', 'open', 'handoff')

# Сколько держим журнал. Год: вопрос «кто вынес переписку» задают постфактум и
# редко, а объём копеечный (строка на действие).
EVENTS_RETENTION_DAYS = 365

# Сколько живёт кеш переписки. Двое суток — ровно окно, которое показывает
# раздел: держать дольше незачем, а короче — значит ходить в API повторно за
# тем же самым.
CACHE_RETENTION_DAYS = 2


DDL = (
    # ── Журнал действий ──────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS dch_events (
        id              BIGSERIAL PRIMARY KEY,
        kind            TEXT NOT NULL,
        user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        user_name       TEXT,
        user_role       TEXT,
        department_id   INTEGER,
        phone           TEXT,
        client_id       BIGINT,
        dialog_id       BIGINT,
        request_id      BIGINT,
        channel_name    TEXT,
        comment_text    TEXT,
        c2d_message_id  BIGINT,
        messages_count  INTEGER,
        ip_address      TEXT,
        user_agent      TEXT,
        created_at      TIMESTAMP NOT NULL DEFAULT {now}
    )
    """.format(now=_NOW),
    # Журнал читают двумя способами: лентой за период (главный экран и выгрузка)
    # и «что делал этот человек». Второй индекс нужен, потому что супервайзер
    # приходит в журнал с конкретным вопросом про конкретного оператора.
    "CREATE INDEX IF NOT EXISTS idx_dch_events_created ON dch_events (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_dch_events_user ON dch_events (user_id, created_at DESC)",
    # Третий разрез — «кто трогал чаты этого водителя». Спрашивают, когда
    # водитель жалуется на утечку переписки.
    "CREATE INDEX IF NOT EXISTS idx_dch_events_phone ON dch_events (phone, created_at DESC)",

    # ── Кеш переписки ────────────────────────────────────────────────────────
    #
    # Ключ — клиент Chat2Desk, а не телефон: телефон у водителя может смениться,
    # а переписка остаётся у того же клиента. Одна строка на клиента: окно всегда
    # одно и то же (последние двое суток), держать несколько срезов незачем.
    """
    CREATE TABLE IF NOT EXISTS dch_message_cache (
        client_id       BIGINT PRIMARY KEY,
        phone           TEXT,
        messages        JSONB NOT NULL,
        messages_count  INTEGER NOT NULL DEFAULT 0,
        window_from     DATE,
        window_to       DATE,
        fetched_at      TIMESTAMP NOT NULL DEFAULT {now}
    )
    """.format(now=_NOW),
    "CREATE INDEX IF NOT EXISTS idx_dch_cache_fetched ON dch_message_cache (fetched_at)",
)


def init_driver_chats_schema(cursor):
    """Разворачивает схему раздела. Вызывается из Database._init_db."""
    for statement in DDL:
        cursor.execute(statement)


CLEANUP_SQL = (
    f"DELETE FROM dch_events "
    f"WHERE created_at < {_NOW} - make_interval(days => %(days)s)",
    f"DELETE FROM dch_message_cache "
    f"WHERE fetched_at < {_NOW} - make_interval(days => %(days)s)",
)


def cleanup(cursor, events_days=EVENTS_RETENTION_DAYS, cache_days=CACHE_RETENTION_DAYS):
    """Ежедневная чистка журнала и кеша. Зовётся из database.py.

    Возвращает, сколько строк удалено — чтобы джоба писала это в лог, а не
    молчала (по молчанию ретеншна не отличить «нечего чистить» от «джоба не
    запускалась»).
    """
    cursor.execute(CLEANUP_SQL[0], {'days': int(events_days)})
    removed_events = cursor.rowcount or 0
    cursor.execute(CLEANUP_SQL[1], {'days': int(cache_days)})
    removed_cache = cursor.rowcount or 0
    return {'events': removed_events, 'cache': removed_cache}
