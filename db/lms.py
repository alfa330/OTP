"""База данных: LMS.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    role_has_min,
)


class LmsMixin:
    def _get_visible_lms_learner_ids_for_requester_tx(self, cursor, requester_id, requester_role):
        role = self._normalize_survey_role(requester_role)
        requester_id = int(requester_id)

        if role_has_min(role, 'admin') or role == 'trainer':
            cursor.execute("""
                SELECT id
                FROM users
                WHERE role IN ('operator', 'trainee')
                  AND COALESCE(status, 'working') <> 'fired'
            """)
            return [int(row[0]) for row in cursor.fetchall()]

        if role == 'sv':
            cursor.execute("""
                SELECT id
                FROM users
                WHERE role IN ('operator', 'trainee')
                  AND supervisor_id = %s
                  AND COALESCE(status, 'working') <> 'fired'
            """, (requester_id,))
            return [int(row[0]) for row in cursor.fetchall()]

        if role in ('operator', 'trainee'):
            return [requester_id]

        return []

    def get_visible_lms_learner_ids_for_requester(self, requester_id, requester_role):
        with self._get_cursor() as cursor:
            return self._get_visible_lms_learner_ids_for_requester_tx(cursor, requester_id, requester_role)
