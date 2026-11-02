"""База данных: чаты водителей.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""


class DriverChatsMixin:
    def cleanup_driver_chats_data(self, events_days=None, cache_days=None):
        """Ретеншн раздела «Чаты водителей»: журнал и кеш переписки.

        Журнал живёт год (вопрос «кто вынес переписку» задают постфактум), кеш —
        двое суток, ровно окно раздела. Зовётся ежедневной джобой рядом с
        чисткой c2d_eval: обе про одни и те же данные вендора.
        """
        from driver_chats import schema as dch_schema

        with self._get_cursor() as cursor:
            return dch_schema.cleanup(
                cursor,
                events_days=events_days or dch_schema.EVENTS_RETENTION_DAYS,
                cache_days=cache_days or dch_schema.CACHE_RETENTION_DAYS)
