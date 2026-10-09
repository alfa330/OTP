"""Уведомления раздела в портале (колокол). Только SQL, без Flask.

ТЗ «Закуп и оплата», п. 17: уведомления приходят при назначении новой задачи,
возврате на доработку, согласовании, отклонении, оплате, пополнении карты,
необходимости приложить чек и закрывающие документы, приближении срока и
просрочке. Telegram — «дополнительный канал оповещения о новой задаче»; основное
место уведомления — сам портал.

Строка `payment_notifications` — одному человеку об одном событии заявки.
Два вида по тому, как гаснут:

* **«от вас ждут действия»** (`actionable`) — новая задача, чек, закрывающие
  документы. Просмотром не гасится: исчезает, когда дело сделано — кем угодно из
  исполнителей (задача подразделения одна на всех, п. 9);
* **«к сведению»** — согласовано, отклонено, оплачено, срок. Гаснет, когда
  человек открыл заявку. Кнопкой колокола «отметить прочитанным» не гасится —
  как ответы в «Обращениях»: уведомление про конкретную заявку, и прочитано оно
  тогда, когда заявку открыли.

Колокол читает отсюда через notifications/sources.py::payments; тычок в канал
реального времени шлёт триггер на этой таблице (database.py).
"""

from .sqlutil import NOW_SQL

MAX_TITLE = 300


def push(cursor, *, user_ids, request_id, kind, title, body=None, subtask_id=None, actionable=False,
         tone='default', dedupe_key=None):
    """Кладёт уведомление каждому из `user_ids`. Возвращает, скольким положено.

    С `dedupe_key` второе такое же уведомление тому же человеку не создаётся
    (напоминание о сроке — раз в день, а не при каждом прогоне).
    """
    ids = sorted({int(x) for x in (user_ids or ()) if x})
    if not ids:
        return 0
    created = 0
    for user_id in ids:
        cursor.execute(
            """
            INSERT INTO payment_notifications (user_id, request_id, subtask_id, kind, actionable, title, body,
                                               tone, dedupe_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
            """,
            (user_id, int(request_id), subtask_id, kind, bool(actionable), str(title or '')[:MAX_TITLE],
             body, tone if tone in ('default', 'warning') else 'default', dedupe_key))
        created += cursor.rowcount
    return created


def _mark_read(cursor, where, params):
    cursor.execute(
        "UPDATE payment_notifications SET read_at = %s WHERE read_at IS NULL AND %s" % (NOW_SQL, where),
        params)
    return cursor.rowcount


def clear_subtask(cursor, subtask_id):
    """Подзадача выполнена или ушла на уточнение — её «ждут вас» гаснут у всех исполнителей."""
    if not subtask_id:
        return 0
    return _mark_read(cursor, "actionable AND subtask_id = %s", (int(subtask_id),))


def clear_kind(cursor, request_id, kind):
    """Гасит уведомления одного вида по заявке (документы приложены — «приложите документы» не нужно)."""
    return _mark_read(cursor, "request_id = %s AND kind = %s", (int(request_id), kind))


def clear_request(cursor, request_id):
    """Заявка закрыта, отклонена или отменена — действий по ней больше ни от кого не ждут."""
    return _mark_read(cursor, "actionable AND request_id = %s", (int(request_id),))


def mark_seen(cursor, user_id, request_id):
    """Человек открыл заявку — его уведомления «к сведению» по ней прочитаны."""
    return _mark_read(cursor, "NOT actionable AND user_id = %s AND request_id = %s",
                      (int(user_id), int(request_id)))


def bell(cursor, user_id, limit):
    """(сколько непрочитанных, первые `limit` из них) для колокола.

    Сначала то, что требует действия, внутри — свежее сверху.
    """
    cursor.execute("SELECT COUNT(*) FROM payment_notifications WHERE user_id = %s AND read_at IS NULL",
                   (int(user_id),))
    total = int(cursor.fetchone()[0] or 0)
    if not total:
        return 0, []
    cursor.execute(
        """
        SELECT id, request_id, kind, actionable, title, body, tone, created_at
          FROM payment_notifications
         WHERE user_id = %s AND read_at IS NULL
         ORDER BY actionable DESC, id DESC
         LIMIT %s
        """,
        (int(user_id), int(limit)))
    rows = [{'id': row[0], 'request_id': row[1], 'kind': row[2], 'actionable': bool(row[3]),
             'title': row[4], 'body': row[5], 'tone': row[6], 'created_at': row[7]}
            for row in cursor.fetchall()]
    return total, rows
