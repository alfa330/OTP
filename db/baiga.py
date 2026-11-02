"""База данных: списки Байги.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    datetime,
)


class BaigaMixin:
    def replace_baiga_scores_for_day(self, day, scores_map: dict):
        """Replace all baiga_daily_scores for given day.
        scores_map: { operator_id (int) : points (int) }
        If operator_id keys are not ints, they will be skipped.
        """
        if isinstance(day, str):
            try:
                day_date = datetime.strptime(day, "%Y-%m-%d").date()
            except Exception:
                raise ValueError("Invalid day format, expected YYYY-MM-DD")
        else:
            day_date = day

        with self._get_cursor() as cursor:
            # remove all existing for day
            cursor.execute("DELETE FROM baiga_daily_scores WHERE day = %s", (day_date,))
            insert_vals = []
            for op_id, pts in (scores_map or {}).items():
                try:
                    oid = int(op_id)
                    pts_i = int(pts) if pts is not None else 0
                    insert_vals.append((oid, day_date, pts_i))
                except Exception:
                    continue

            if insert_vals:
                cursor.executemany(
                    "INSERT INTO baiga_daily_scores (operator_id, day, points) VALUES (%s, %s, %s)",
                    insert_vals
                )

    def get_baiga_scores_for_day(self, day):
        """Return list of {operator_id, name, points} for given day."""
        if isinstance(day, str):
            try:
                day_date = datetime.strptime(day, "%Y-%m-%d").date()
            except Exception:
                raise ValueError("Invalid day format, expected YYYY-MM-DD")
        else:
            day_date = day

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT b.operator_id, b.points, u.name
                FROM baiga_daily_scores b
                LEFT JOIN users u ON u.id = b.operator_id
                WHERE b.day = %s
            """, (day_date,))
            rows = cursor.fetchall()
        return [ { 'operator_id': r[0], 'points': int(r[1] or 0), 'name': r[2] } for r in rows ]

    def get_baiga_scores_for_range(self, start_day, end_day):
        """Return mapping of day (YYYY-MM-DD) -> list of {operator_id, points, name} for days in [start_day, end_day]."""
        if isinstance(start_day, str):
            try:
                start_date = datetime.strptime(start_day, "%Y-%m-%d").date()
            except Exception:
                raise ValueError("Invalid start_day format, expected YYYY-MM-DD")
        else:
            start_date = start_day

        if isinstance(end_day, str):
            try:
                end_date = datetime.strptime(end_day, "%Y-%m-%d").date()
            except Exception:
                raise ValueError("Invalid end_day format, expected YYYY-MM-DD")
        else:
            end_date = end_day

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT to_char(b.day, 'YYYY-MM-DD') as day_str, b.operator_id, b.points, u.name
                FROM baiga_daily_scores b
                LEFT JOIN users u ON u.id = b.operator_id
                WHERE b.day >= %s AND b.day <= %s
                ORDER BY b.day, b.operator_id
            """, (start_date, end_date))
            rows = cursor.fetchall()

        result = {}
        for day_str, op_id, pts, name in rows:
            entry = { 'operator_id': op_id, 'points': int(pts or 0), 'name': name }
            result.setdefault(day_str, []).append(entry)
        return result
