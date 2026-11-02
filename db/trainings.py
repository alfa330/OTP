"""База данных: тренинги.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _UNSET,
    Any,
    Dict,
    List,
    logging,
    Optional,
)


class TrainingsMixin:
    def get_training_report_subscription(self, user_id) -> Optional[Dict[str, bool]]:
        """Какие сводки по тренингам получает пользователь.

        None — пользователя нет или подписываться ему нельзя (не админ и не
        глава отдела). Пустого профиля достаточно: LEFT JOIN и COALESCE дают
        «ничего не включено», строку в admin_profiles заводит только запись.
        """
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT COALESCE(ap.training_report_daily_enabled, FALSE),
                       COALESCE(ap.training_report_weekly_enabled, FALSE),
                       COALESCE(ap.training_report_monthly_enabled, FALSE)
                  FROM users u
                  LEFT JOIN admin_profiles ap ON ap.user_id = u.id
                 WHERE u.id = %%s
                   AND (%s)
                 LIMIT 1
            """ % self.TRAINING_REPORT_SUBSCRIBER_SQL, (uid,))
            row = cursor.fetchone()
            if not row:
                return None
            return {'daily': bool(row[0]), 'weekly': bool(row[1]), 'monthly': bool(row[2])}

    def set_training_report_subscription(self, user_id, periods: Dict[str, bool]) -> Optional[Dict[str, bool]]:
        """Включить/выключить сводки. periods — только те периодичности, что
        пришли в запросе; остальные остаются как были.

        Право проверяется тем же условием, что и на чтение: UPDATE молча ничего
        не изменил бы, но INSERT в admin_profiles прошёл бы — и настройка
        появилась бы у того, кому она не положена.
        """
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None

        columns = {}
        for period, value in (periods or {}).items():
            column = self.TRAINING_REPORT_COLUMNS.get(str(period or '').strip().lower())
            if column:
                columns[column] = bool(value)
        if not columns:
            return self.get_training_report_subscription(uid)

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT 1 FROM users u
                 WHERE u.id = %%s AND (%s) LIMIT 1
            """ % self.TRAINING_REPORT_SUBSCRIBER_SQL, (uid,))
            if not cursor.fetchone():
                return None

            names = list(columns.keys())
            cursor.execute(
                """
                INSERT INTO admin_profiles (user_id, %(cols)s)
                VALUES (%%s, %(placeholders)s)
                ON CONFLICT (user_id) DO UPDATE SET %(updates)s
                RETURNING training_report_daily_enabled,
                          training_report_weekly_enabled,
                          training_report_monthly_enabled
                """ % {
                    'cols': ', '.join(names),
                    'placeholders': ', '.join(['%s'] * len(names)),
                    'updates': ', '.join('%s = EXCLUDED.%s' % (name, name) for name in names),
                },
                [uid] + [columns[name] for name in names],
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {'daily': bool(row[0]), 'weekly': bool(row[1]), 'monthly': bool(row[2])}

    def get_training_report_recipients(self, period) -> List[Dict[str, Any]]:
        """Кому уходит сводка по тренингам этой периодичности.

        Область — как у остальных Telegram-отчётов портала: админ и
        супер-админ получают все отделы (`department_ids = None`), глава
        отдела — только свои. Если человек и админ, и глава, остаётся
        глобальная область: так же решено в отчёте о сменах ставок.

        Без привязанного Telegram получателя в списке нет — отправлять некуда.
        """
        column = self.TRAINING_REPORT_COLUMNS.get(str(period or '').strip().lower())
        if not column:
            return []

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    u.id,
                    u.name,
                    u.telegram_id,
                    LOWER(COALESCE(u.role, '')) AS role_norm,
                    COALESCE(
                        ARRAY_AGG(DISTINCT d.id ORDER BY d.id) FILTER (WHERE d.id IS NOT NULL),
                        ARRAY[]::INTEGER[]
                    ) AS headed_ids,
                    COALESCE(
                        ARRAY_AGG(DISTINCT d.name) FILTER (WHERE d.name IS NOT NULL),
                        ARRAY[]::VARCHAR[]
                    ) AS headed_names
                  FROM users u
                  LEFT JOIN admin_profiles ap ON ap.user_id = u.id
                  LEFT JOIN departments d
                    ON d.head_user_id = u.id
                   AND COALESCE(d.is_active, TRUE) = TRUE
                 WHERE u.telegram_id IS NOT NULL
                   -- Увольнение не снимает ни telegram_id, ни роль 'admin':
                   -- уволенный админ продолжал бы получать сводку по отделу,
                   -- из которого ушёл. Тот же фильтр стоит в рассылке
                   -- напоминаний по задачам.
                   AND COALESCE(u.status, 'working') <> 'fired'
                   AND (
                       LOWER(COALESCE(u.role, '')) IN ('admin', 'super_admin')
                       OR d.id IS NOT NULL
                   )
                   AND COALESCE(ap.{column}, FALSE) = TRUE
                 GROUP BY u.id, u.name, u.telegram_id, u.role
                 ORDER BY u.name
            """.format(column=column))
            rows = cursor.fetchall() or []

        recipients = []
        for row in rows:
            is_admin = str(row[3] or '').strip().lower() in ('admin', 'super_admin')
            department_ids = [int(value) for value in (row[4] or []) if value is not None]
            department_names = sorted(str(value) for value in (row[5] or []) if value)
            recipients.append({
                'id': int(row[0]),
                'name': row[1] or '',
                'telegram_id': int(row[2]),
                'scope': 'global' if is_admin else 'department',
                'department_ids': None if is_admin else department_ids,
                'department_names': [] if is_admin else department_names,
                'scope_label': 'Все отделы' if is_admin else (
                    ', '.join(department_names) or 'Отдел главы'),
            })
        return recipients

    def release_training_report_send(self, period, period_start, user_id) -> None:
        """Снять заявку — отправка не удалась, следующий прогон попробует снова."""
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return
        try:
            with self._get_cursor() as cursor:
                cursor.execute("""
                    DELETE FROM training_report_sends
                     WHERE period = %s AND period_start = %s AND user_id = %s
                """, (str(period or '').strip().lower(), period_start, uid))
        except Exception:
            # Не смогли снять — хуже пропущенной сводки только упавшая джоба.
            logging.exception("Не удалось снять заявку на сводку по тренингам")

    def prune_training_report_sends(self, keep_days: int = 400) -> int:
        """Подчистить журнал заявок. Год с запасом: он нужен только чтобы
        отличить «уже отправляли» от «ещё нет», и расти вечно ему незачем."""
        with self._get_cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.training_report_sends') IS NOT NULL")
            row = cursor.fetchone()
            if not (row and row[0]):
                return 0
            cursor.execute("""
                DELETE FROM training_report_sends
                 WHERE period_start < (CURRENT_DATE - %s::INTEGER)
            """, (int(keep_days),))
            return cursor.rowcount or 0

    def _load_training_hours_by_operator_tx(self, cursor, operator_ids, start_date, end_date, group_id=None):
        normalized_ids = []
        seen_ids = set()
        for raw_id in (operator_ids or []):
            try:
                op_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if op_id in seen_ids:
                continue
            seen_ids.add(op_id)
            normalized_ids.append(op_id)

        if not normalized_ids:
            return {}

        group_filter_sql = ""
        params = [normalized_ids, start_date, end_date]
        if group_id is not None:
            group_filter_sql = """
              AND EXISTS (
                  SELECT 1
                  FROM group_operator_memberships gom
                  WHERE gom.operator_id = t.operator_id
                    AND gom.group_id = %s
                    AND gom.start_date <= t.training_date
                    AND (gom.end_date IS NULL OR gom.end_date >= t.training_date)
              )
            """
            params.append(int(group_id))

        cursor.execute(f"""
            SELECT
                t.operator_id,
                COALESCE(SUM(
                    CASE
                        WHEN t.end_time <= t.start_time
                            THEN EXTRACT(EPOCH FROM (t.end_time + INTERVAL '24 hours' - t.start_time))
                        ELSE EXTRACT(EPOCH FROM (t.end_time - t.start_time))
                    END
                ), 0) / 3600.0 AS training_hours
            FROM trainings t
            WHERE t.operator_id = ANY(%s)
              AND t.count_in_hours = TRUE
              AND t.training_date >= %s
              AND t.training_date <= %s
              {group_filter_sql}
            GROUP BY t.operator_id
        """, params)

        totals = {int(op_id): float(hours or 0.0) for op_id, hours in cursor.fetchall()}
        _manual_phone_training_map, manual_phone_training_totals = self._load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
            cursor=cursor,
            operator_ids=normalized_ids,
            start_date=start_date,
            end_date=end_date,
            group_id=group_id,
            counted_only=True
        )
        for op_id in normalized_ids:
            totals[op_id] = max(
                0.0,
                float(totals.get(op_id, 0.0)) - float(manual_phone_training_totals.get(op_id, 0.0) or 0.0)
            )
        return totals



    def add_training(self, operator_id, training_date, start_time, end_time, reason, comment,
                     created_by, count_in_hours=True, topic_id=None):
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO trainings (operator_id, training_date, start_time, end_time, reason, comment, created_by, count_in_hours, topic_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (operator_id, training_date, start_time, end_time) DO NOTHING
                RETURNING id
            """, (operator_id, training_date, start_time, end_time, reason, comment, created_by, count_in_hours, topic_id))
            inserted = cursor.fetchone()
            if inserted:
                return inserted[0]

            cursor.execute("""
                SELECT id
                FROM trainings
                WHERE operator_id = %s
                  AND training_date = %s
                  AND start_time = %s
                  AND end_time = %s
                LIMIT 1
            """, (operator_id, training_date, start_time, end_time))
            existing = cursor.fetchone()
            if existing:
                return existing[0]

            raise RuntimeError("Failed to persist training record")


    def get_trainings(self, requester_id=None, month=None):
        """
        Получить тренинги для оператора/всех, в зависимости от роли requester_id.
        Если operator_id указан — только для него.
        Если requester_id — определяет роль и фильтрует:
            - admin, sv: все тренинги (с фильтром по month, если указан)
            - operator: только свои тренинги (с фильтром по month, если указан)
        """
        # Получаем роль requester_id
        role = None
        if requester_id:
            with self._get_cursor() as cursor:
                cursor.execute("SELECT role FROM users WHERE id = %s", (requester_id,))
                res = cursor.fetchone()
                if res:
                    role = res[0]
        query = """
            SELECT t.id, t.operator_id, t.training_date, t.start_time, t.end_time, t.reason, t.comment, t.created_at, cb.name as created_by_name, t.count_in_hours
            FROM trainings t
            JOIN users u ON t.operator_id = u.id
            LEFT JOIN users cb ON t.created_by = cb.id
        """
        params = []
        where_clauses = []
        if month:
            where_clauses.append("TO_CHAR(t.training_date, 'YYYY-MM') = %s")
            params.append(month)
        if role == "operator":
            where_clauses.append("t.operator_id = %s")
            params.append(requester_id)
        # Для admin — без ограничений, кроме month
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += " ORDER BY t.training_date DESC, t.start_time DESC"
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return [
                {
                    "id": row[0],
                    "operator_id": row[1],
                    "date": row[2].strftime('%Y-%m-%d'),
                    "start_time": row[3].strftime('%H:%M'),
                    "end_time": row[4].strftime('%H:%M'),
                    "reason": row[5],
                    "comment": row[6],
                    "created_at": row[7].strftime('%Y-%m-%d %H:%M'),
                    "created_by_name": row[8] if row[8] else "System",
                    "count_in_hours": bool(row[9])
                } for row in cursor.fetchall()
            ]

    def add_training_interval_rejection(self, operator_id, rejection_date, start_time, end_time, comment, created_by):
        """Сохранить отклонение интервала авто-тренинга. Идемпотентно: при повторе
        возвращает id уже существующей записи."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO training_interval_rejections (operator_id, rejection_date, start_time, end_time, comment, created_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (operator_id, rejection_date, start_time, end_time) DO NOTHING
                RETURNING id
            """, (operator_id, rejection_date, start_time, end_time, comment, created_by))
            inserted = cursor.fetchone()
            if inserted:
                return inserted[0]

            cursor.execute("""
                SELECT id
                FROM training_interval_rejections
                WHERE operator_id = %s
                  AND rejection_date = %s
                  AND start_time = %s
                  AND end_time = %s
                LIMIT 1
            """, (operator_id, rejection_date, start_time, end_time))
            existing = cursor.fetchone()
            if existing:
                return existing[0]

            raise RuntimeError("Failed to persist training interval rejection")

    def update_training(self, training_id, training_date=None, start_time=None, end_time=None,
                        reason=None, comment=_UNSET, count_in_hours=None, topic_id=_UNSET):
        updates = []
        params = []
        if training_date:
            updates.append("training_date = %s"); params.append(training_date)
        if start_time:
            updates.append("start_time = %s"); params.append(start_time)
        if end_time:
            updates.append("end_time = %s"); params.append(end_time)
        if reason:
            updates.append("reason = %s"); params.append(reason)
        # Через _UNSET, а не через None: комментарий — единственное поле, которое
        # человек осмысленно СТИРАЕТ, а с проверкой `is not None` пустая правка
        # молча не доезжала и старый текст оставался в базе.
        if comment is not _UNSET:
            updates.append("comment = %s"); params.append(comment or None)
        if count_in_hours is not None:
            updates.append("count_in_hours = %s"); params.append(count_in_hours)
        if topic_id is not _UNSET:
            updates.append("topic_id = %s"); params.append(topic_id)

        if not updates:
            return False

        query = f"UPDATE trainings SET {', '.join(updates)} WHERE id = %s RETURNING id"
        params.append(training_id)

        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone() is not None

    def delete_training(self, training_id):
        with self._get_cursor() as cursor:
            cursor.execute("DELETE FROM trainings WHERE id = %s RETURNING id", (training_id,))
            return cursor.fetchone() is not None
