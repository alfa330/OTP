"""Схема окна Syntony. Идемпотентно: CREATE … IF NOT EXISTS.

Разворачивается при старте из Database._init_db через init_syntony_window_schema(cursor) —
своим SAVEPOINT'ом: не развернулась — раздел недоступен, остальное работает штатно.

Решения, видные в DDL:

* **Список доступа — одна строка на человека.** Строка есть — человек оператор для
  Syntony и видит разделы; строки нет — нет. Удаление учётки уносит и строку
  (ON DELETE CASCADE): слияние дублей не должно на ней падать.

* **Почта лежит в строке доступа.** По ней администратор Syntony находит сотрудника
  Syntony, и совпасть она должна с почтой ЕГО учётной записи там, а не обязательно с
  почтой карточки сотрудника. Пустая строка — почты нет; непустая — одна на весь список.

* **Связь — последнее, что ответила Syntony** (rules.LINK_STATES) и когда. Без CHECK:
  список состояний растёт кодом, а не миграцией.

* **След выдач без внешних ключей и с именами снимком**: кто кому открыл и закрыл
  доступ, должно читаться и после удаления учётки. Экрана у следа нет.
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS syntony_window_access (
        user_id          INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        email            VARCHAR(255) NOT NULL DEFAULT '',
        granted_by       INTEGER,
        granted_at       TIMESTAMP NOT NULL DEFAULT {now},
        link_state       VARCHAR(16) NOT NULL DEFAULT 'unknown',
        link_checked_at  TIMESTAMP
    )
    """.format(now=_NOW),

    # action: grant / revoke / email.
    """
    CREATE TABLE IF NOT EXISTS syntony_window_access_log (
        id          BIGSERIAL PRIMARY KEY,
        action      VARCHAR(16) NOT NULL,
        user_id     INTEGER NOT NULL,
        user_name   VARCHAR(255),
        email       VARCHAR(255),
        actor_id    INTEGER,
        actor_name  VARCHAR(255),
        created_at  TIMESTAMP NOT NULL DEFAULT {now}
    )
    """.format(now=_NOW),

    # Одна почта — один сотрудник Syntony: двух операторов iCORE на неё не связать.
    # Ручки проверяют это сами и отвечают словами; индекс — страховка от гонки.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_syntony_window_access_email "
    "ON syntony_window_access (email) WHERE email <> ''",

    "CREATE INDEX IF NOT EXISTS idx_syntony_window_access_log_user "
    "ON syntony_window_access_log (user_id, id DESC)",
]


def init_syntony_window_schema(cursor):
    """Разворачивает схему. Курсор из _init_db, транзакцией правит вызывающий."""
    for statement in _STATEMENTS:
        cursor.execute(statement)
