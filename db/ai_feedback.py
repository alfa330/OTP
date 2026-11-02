"""База данных: кеш ИИ-обратной связи.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    json,
)


class AiFeedbackMixin:
    def get_ai_feedback_cache(self, operator_id: int, month: str):
        """Получить кэшированный AI фидбэк для оператора за месяц"""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT feedback_data, created_at, updated_at
                FROM ai_feedback_cache
                WHERE operator_id = %s AND month = %s
            """, (operator_id, month))
            result = cursor.fetchone()
            if result:
                return {
                    'feedback_data': result[0],
                    'created_at': result[1],
                    'updated_at': result[2]
                }
            return None

    def save_ai_feedback_cache(self, operator_id: int, month: str, feedback_data: dict):
        """Сохранить или обновить AI фидбэк в кэше"""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO ai_feedback_cache (operator_id, month, feedback_data, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (operator_id, month)
                DO UPDATE SET
                    feedback_data = EXCLUDED.feedback_data,
                    updated_at = CURRENT_TIMESTAMP
            """, (operator_id, month, json.dumps(feedback_data, ensure_ascii=False)))
