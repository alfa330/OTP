"""База данных: ресурсы и FTE.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    datetime,
)


class ResourceFteMixin:
    def _clip_part_after_authoritative(self, part, authoritative_end):
        """Обрезать кусок сегмента так, чтобы он не наезжал на авторитетный интервал.

        Возвращает None, если от куска ничего не остаётся. Авторитетные интервалы
        (готовые start/stop из выгрузки) остаются как есть, а пересобранное из
        событий начинается после них — так сутки перехода на другой источник
        собираются из двух половин, а не теряются целиком.
        """
        if authoritative_end is None:
            # Границу определить не удалось — ведём себя как раньше и защищаем
            # весь день: потерять кусок дня безопаснее, чем задвоить интервалы.
            return None
        start_at = part.get('start_at')
        end_at = part.get('end_at')
        if not isinstance(start_at, datetime) or not isinstance(end_at, datetime):
            return None
        if start_at >= authoritative_end:
            return part
        if end_at <= authoritative_end:
            return None
        clipped = dict(part)
        clipped['start_at'] = authoritative_end
        clipped['duration_sec'] = max(0, int((end_at - authoritative_end).total_seconds()))
        return clipped
