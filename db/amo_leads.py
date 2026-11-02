"""База данных: лиды amoCRM.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    execute_values,
)


class AmoLeadsMixin:
    def upsert_amo_leads(self, rows):
        """Записать выгруженные сделки. Существующие обновляются по lead_id."""
        if not rows:
            return 0
        payload = [(
            r["lead_id"], r["name"], r["created_at"], r["created_date"], r["stage"],
            r["responsible"], r["tags"], r["utm_source"], r["utm_medium"],
            r["utm_campaign"], r["utm_term"], r["registered"], r["trip_done"],
            r["robot_closed"],
        ) for r in rows if r.get("lead_id")]
        if not payload:
            return 0
        with self._get_cursor() as cursor:
            execute_values(cursor, """
                INSERT INTO amo_leads (
                    lead_id, name, created_at, created_date, stage, responsible,
                    tags, utm_source, utm_medium, utm_campaign, utm_term,
                    registered, trip_done, robot_closed
                ) VALUES %s
                ON CONFLICT (lead_id) DO UPDATE SET
                    name         = EXCLUDED.name,
                    created_at   = EXCLUDED.created_at,
                    created_date = EXCLUDED.created_date,
                    stage        = EXCLUDED.stage,
                    responsible  = EXCLUDED.responsible,
                    tags         = EXCLUDED.tags,
                    utm_source   = EXCLUDED.utm_source,
                    utm_medium   = EXCLUDED.utm_medium,
                    utm_campaign = EXCLUDED.utm_campaign,
                    utm_term     = EXCLUDED.utm_term,
                    registered   = EXCLUDED.registered,
                    trip_done    = EXCLUDED.trip_done,
                    robot_closed = EXCLUDED.robot_closed,
                    synced_at    = NOW()
                -- Неизменившуюся сделку не переписываем. Выгрузка раз в три часа и перед
                -- каждой отбивкой берёт все сделки за 16 дней, и почти все они те же:
                -- ~110 тыс. холостых UPDATE в сутки, ~600 МБ WAL за 4 дня (замер
                -- 15–19.10.2026). synced_at никто не читает — свежесть выгрузки живёт в
                -- журнале amo_lead_syncs.
                WHERE (amo_leads.name, amo_leads.created_at, amo_leads.created_date,
                       amo_leads.stage, amo_leads.responsible, amo_leads.tags,
                       amo_leads.utm_source, amo_leads.utm_medium, amo_leads.utm_campaign,
                       amo_leads.utm_term, amo_leads.registered, amo_leads.trip_done,
                       amo_leads.robot_closed)
                      IS DISTINCT FROM
                      (EXCLUDED.name, EXCLUDED.created_at, EXCLUDED.created_date,
                       EXCLUDED.stage, EXCLUDED.responsible, EXCLUDED.tags,
                       EXCLUDED.utm_source, EXCLUDED.utm_medium, EXCLUDED.utm_campaign,
                       EXCLUDED.utm_term, EXCLUDED.registered, EXCLUDED.trip_done,
                       EXCLUDED.robot_closed)
            """, payload, page_size=500)
        return len(payload)

    def get_amo_leads_for_date(self, day):
        """Сделки за календарный день — то, по чему считается отбивка."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT lead_id, name, stage, responsible, tags,
                       utm_source, utm_medium, utm_campaign, utm_term,
                       registered, trip_done, robot_closed
                FROM amo_leads
                WHERE created_date = %s
            """, (day,))
            columns = [c[0] for c in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_amo_leads_between(self, start, end):
        """Сделки, созданные в интервале [start, end). Нужно для окон 6ч/12ч.

        Границы — по created_at, а не по created_date: дневная проверка в 12:00
        сравнивает половину суток с той же половиной суток неделю назад.
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT tags, utm_source
                FROM amo_leads
                WHERE created_at >= %s AND created_at < %s
            """, (start, end))
            return [{"tags": r[0], "utm_source": r[1]} for r in cursor.fetchall()]

    def start_amo_lead_sync(self, days):
        with self._get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO amo_lead_syncs (days) VALUES (%s) RETURNING id", (days,))
            return cursor.fetchone()[0]

    def finish_amo_lead_sync(self, sync_id, leads_fetched=None, error=None):
        if not sync_id:
            return
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE amo_lead_syncs
                SET finished_at = NOW(),
                    status = %s,
                    leads_fetched = %s,
                    error = %s
                WHERE id = %s
            """, ("error" if error else "ok", leads_fetched, error, sync_id))

    def add_amo_lead_subscription(self, chat_id, title=None, chat_type=None, user_id=None):
        """Подписать чат на отбивку. Повторная подписка просто обновляет данные чата."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO amo_lead_subscriptions (chat_id, title, chat_type, subscribed_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    chat_type = EXCLUDED.chat_type,
                    subscribed_by = EXCLUDED.subscribed_by
                RETURNING (xmax = 0) AS is_new
            """, (str(chat_id), title, chat_type, user_id))
            return bool(cursor.fetchone()[0])

    def remove_amo_lead_subscription(self, chat_id):
        """Отписать чат. Возвращает True, если подписка была."""
        with self._get_cursor() as cursor:
            cursor.execute("DELETE FROM amo_lead_subscriptions WHERE chat_id = %s",
                           (str(chat_id),))
            return cursor.rowcount > 0

    def get_amo_lead_subscriptions(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT chat_id, title, chat_type
                FROM amo_lead_subscriptions
                ORDER BY created_at
            """)
            return [{"chat_id": r[0], "title": r[1], "chat_type": r[2]}
                    for r in cursor.fetchall()]

    def mark_amo_lead_subscription_sent(self, chat_id):
        with self._get_cursor() as cursor:
            cursor.execute(
                "UPDATE amo_lead_subscriptions SET last_sent_at = NOW() WHERE chat_id = %s",
                (str(chat_id),))

    def get_last_amo_lead_sync(self, status=None):
        """Последняя завершённая выгрузка — для отметки о свежести в отбивке.

        `status='ok'` даёт последнюю УДАЧНУЮ: возраст цифр надо считать по ней, а
        не по последней попытке. Упавшая выгрузка тоже проставляет `finished_at`,
        но в `amo_leads` ничего не пишет.
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, started_at, finished_at, status, leads_fetched, error
                FROM amo_lead_syncs
                WHERE finished_at IS NOT NULL
                  AND (%s IS NULL OR status = %s)
                ORDER BY finished_at DESC
                LIMIT 1
            """, (status, status))
            row = cursor.fetchone()
            if not row:
                return None
            keys = ("id", "started_at", "finished_at", "status", "leads_fetched", "error")
            return dict(zip(keys, row))
