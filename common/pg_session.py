# -*- coding: utf-8 -*-
"""Пояс сессии PostgreSQL — UTC, что бы ни стояло на сервере.

Код портала везде исходит из того, что сессия базы живёт в UTC — так на Render (SHOW TimeZone =
UTC, op_funnel/queries.py, db/schedules.py): `DEFAULT CURRENT_TIMESTAMP` и `NOW()` у колонок
TIMESTAMP без пояса, сравнения таких колонок с NOW(), `::date`, `CURRENT_DATE`, строки
TIMESTAMPTZ, которые фронт режет как UTC. Managed PostgreSQL в Yandex Cloud по умолчанию отдаёт
сессию в Europe/Moscow: 24.11.2026 копия прода на нём отвечала `…+03:00` там, где Render отвечает
`…+00:00` за ту же запись, и новые строки легли бы на три часа вперёд старых. Поэтому пояс
закрепляет само соединение, а не настройка кластера, которую легко потерять при переезде.

Почему SET после подключения, а не стартовый параметр libpq `options=-c TimeZone=UTC`: в облаке
соединения идут через пулер Odyssey (порт 6432), а стартовые параметры пулер может и не пропустить —
тогда отказ подключения был бы у всех. SET держится в сессионном режиме пулера — том же, на котором
работают LISTEN/NOTIFY живых потоков.

Модуль без зависимостей от проекта: его импортируют и пул (db/core.py), и соединения LISTEN
(bot_schedule2.py), и «ИИ-оценка» (call_qa/config.py).
"""
import psycopg2
import psycopg2.extensions

SESSION_TIME_ZONE = 'UTC'


def pin_session_time_zone(conn):
    """Поставить сессии пояс UTC и сразу зафиксировать.

    COMMIT обязателен: SET внутри транзакции, которую потом откатят, откатился бы вместе с ней,
    а соединение пула живёт долго и откатывается после каждой ошибки запроса.
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SET TIME ZONE %s", (SESSION_TIME_ZONE,))
    finally:
        cursor.close()
    conn.commit()


class UtcConnection(psycopg2.extensions.connection):
    """Соединение psycopg2, чья сессия сразу в UTC.

    Подставляется как `connection_factory` и в `psycopg2.connect`, и в пулы psycopg2 — они
    передают её в connect сами, так что пояс получает каждое новое физическое соединение.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pin_session_time_zone(self)
