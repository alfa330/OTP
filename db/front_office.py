"""База данных: фронт-офис.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    FRONT_OFFICE_DEPARTMENT_CODE,
)


class FrontOfficeMixin:
    def get_front_office_department_id(self):
        """id отдела «Фронт офисы». Хардкодить нельзя — отдел заведён данными."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id FROM departments
                WHERE code = %s AND COALESCE(is_active, TRUE) = TRUE
                ORDER BY id LIMIT 1
            """, (FRONT_OFFICE_DEPARTMENT_CODE,))
            r = cursor.fetchone()
            return r[0] if r else None
