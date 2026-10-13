"""Схема раздела «Ссылка на подписание». Таблицы sign_link_requests и
sign_link_api_usage.

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS. Разворачивается один раз при
старте из Database._init_db через init_sign_links_schema(cursor).

Ключевые решения, которые видны в DDL:

* **Журнал — одна таблица, строка на запрос.** Каждое нажатие «Получить
  ссылку» — строка с исходом: ссылка выдана, документов нет, генератор
  отказал, генератор не ответил, ИИН не прошёл проверку, дневной предел.
  Неудачи пишутся наравне с удачами: вопрос админа «кто перебирал ИИН» —
  про неудачи.

* **Данные сотрудника — снимок.** Рядом с `user_id` лежат `user_name`,
  `user_role`, `department_code`. Журнал отвечает на вопрос «кто это сделал
  ТОГДА»: человек меняет отдел, увольняется, роль ему заменяют назначением
  главой. Тот же приём, что у dch_events и wiki_article_views_log.
  `user_id` — ON DELETE SET NULL, а не CASCADE: строка журнала — свидетельство,
  и удаление учётки не должно уносить его с собой.

* **Самой ссылки в журнале НЕТ — и это намеренно.** Ссылка ведёт к документам
  живого человека; хранить её в базе значит завести второй канал, по которому
  её можно достать спустя месяц. В журнале лежит факт `link_issued` и домен
  `link_host` — справочно, чтобы отличить смену формата у вендора от сбоя.

* **CHECK на исход намеренно НЕ ставим.** Новый исход не должен требовать
  миграции живой базы; неизвестный журнал показывает нейтральной строкой. То
  же решение, что у parcels.EVENT_KINDS и driver_chats.EVENT_KINDS.

* **Индексы под четыре разреза журнала**: по времени (страница), по человеку
  (фильтр «сотрудник» и дневной предел), по ИИН («кто запрашивал этого
  водителя») и по отделу (журнал главы ограничен его отделом).

* **Внешний сервис считается отдельно — строкой на ключ в сутки**
  (sign_link_api_usage). Сервис присылает ИИН десятками тысяч, и почти на все
  ответ один: «документов нет». Писать это в журнал построчно — сто тысяч строк
  в день, от которых журнал у админа перестаёт открываться; ничего при этом не
  выдано. Поэтому в журнал от сервиса идут выданные ссылки, отказы и опечатки,
  а «документов нет» — только в счётчик. По счётчику же держится дневной предел:
  одна строка вместо подсчёта по журналу на каждый запрос.
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Исходы запроса. Порядок — от «всё хорошо» к «ничего не вышло».
OUTCOMES = ('link', 'no_documents', 'rejected', 'unavailable', 'invalid', 'limit')

# Исходы, при которых генератора СПРАШИВАЛИ. По ним считается дневной предел:
# опечатка в ИИН и сам отказ по пределу вендора не трогают.
VENDOR_OUTCOMES = ('link', 'no_documents', 'rejected', 'unavailable')


_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS sign_link_requests (
        id               BIGSERIAL PRIMARY KEY,

        -- Кто. Снимок на момент запроса, ссылка на учётку — справочно.
        user_id          INTEGER REFERENCES users(id) ON DELETE SET NULL,
        user_name        VARCHAR(200),
        user_role        VARCHAR(32),
        department_id    INTEGER,
        department_code  VARCHAR(64),

        -- Что спрашивали. VARCHAR(32), а не CHAR(12): опечатки тоже пишутся,
        -- а у них длина любая.
        iin              VARCHAR(32) NOT NULL,

        -- Чем кончилось.
        outcome          VARCHAR(16) NOT NULL,
        vendor_message   TEXT,
        link_issued      BOOLEAN NOT NULL DEFAULT FALSE,
        link_host        VARCHAR(120),
        error_text       TEXT,
        latency_ms       INTEGER,

        ip_address       VARCHAR(64),
        user_agent       VARCHAR(500),
        created_at       TIMESTAMP NOT NULL DEFAULT """ + _NOW + """
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS sign_link_api_usage (
        -- Сутки по Алматы и отпечаток ключа: строка на ключ в день.
        day         DATE NOT NULL,
        key_mark    VARCHAR(16) NOT NULL,

        requests    INTEGER NOT NULL DEFAULT 0,
        -- Сколько ИИН принято в счёт дневного предела; ниже — чем они кончились.
        iins        INTEGER NOT NULL DEFAULT 0,
        links       INTEGER NOT NULL DEFAULT 0,
        not_found   INTEGER NOT NULL DEFAULT 0,
        invalid     INTEGER NOT NULL DEFAULT 0,
        errors      INTEGER NOT NULL DEFAULT 0,

        updated_at  TIMESTAMP NOT NULL DEFAULT """ + _NOW + """,
        PRIMARY KEY (day, key_mark)
    )
    """,

    "CREATE INDEX IF NOT EXISTS idx_sign_link_requests_created "
    "ON sign_link_requests (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sign_link_requests_user "
    "ON sign_link_requests (user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sign_link_requests_iin "
    "ON sign_link_requests (iin, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sign_link_requests_department "
    "ON sign_link_requests (department_code, created_at DESC)",
]


def _is_table(statement):
    return 'CREATE TABLE' in statement


def init_sign_links_schema(cursor):
    """Разворачивает схему раздела. Курсор из _init_db, транзакцией правит вызывающий.

    Порядок — таблица, потом индексы: индекс по столбцу, которого ещё нет,
    падает и откатывает весь разворот.
    """
    for statement in _STATEMENTS:
        cursor.execute(statement)


def schema_is_ready(cursor):
    """Развёрнута ли схема. Отличает «раздел ещё не поднялся» от «журнал пуст»."""
    cursor.execute("SELECT to_regclass('public.sign_link_requests') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])


def api_usage_is_ready(cursor):
    """Есть ли таблица счёта внешнего сервиса: без неё дневной предел не посчитать.

    Только чтение каталога — на пути запроса схему не создаём: она ставится при
    старте, и если не встала, вход отвечает «не готов», а не выдаёт без счёта.
    """
    cursor.execute("SELECT to_regclass('sign_link_api_usage') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
