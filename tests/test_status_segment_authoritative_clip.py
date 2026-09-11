"""Сутки перехода на другой источник статусов собираются из двух половин.

У отдела ТЭЗ 11.09.2026 день поделён: ночная выгрузка Binotel (импорт в 01:00)
закрывает ночь и кладёт сегменты с is_authoritative=TRUE, а дальше день закрывают
события телефона iCORE Phone. Раньше защита авторитетных интервалов действовала на
ВЕСЬ день (operator_id, status_date), из-за чего 453 события телефона не давали ни
одного сегмента — день выглядел пустым. Теперь защита действует по времени:
пересобранное из событий обрезается так, чтобы начинаться после авторитетного
интервала. Тест фиксирует именно это.
"""

import ast
import textwrap
import unittest
from datetime import datetime, date as dt_date, time as dt_time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"


def _load_clip():
    source = DATABASE_PATH.read_text(encoding="utf-8-sig")
    module = ast.parse(source)
    database_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    node = next(
        node for node in database_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_clip_part_after_authoritative"
    )
    namespace = {"datetime": datetime}
    exec(textwrap.dedent(ast.get_source_segment(source, node)), namespace)
    return namespace["_clip_part_after_authoritative"]


def _part(start_h, end_h, day=dt_date(2026, 9, 11)):
    start_at = datetime.combine(day, dt_time(start_h, 0))
    end_at = datetime.combine(day, dt_time(end_h, 0))
    return {
        "status_date": day,
        "start_at": start_at,
        "end_at": end_at,
        "duration_sec": int((end_at - start_at).total_seconds()),
    }


# Функция извлечена из класса Database, поэтому первым аргументом идёт self.
# Держим её в модуле, а не в атрибуте класса: иначе unittest сделает из неё метод.
CLIP = _load_clip()


class AuthoritativeClipTests(unittest.TestCase):
    def clip(self, part, authoritative_end):
        return CLIP(None, part, authoritative_end)

    def test_part_entirely_after_authoritative_is_kept_as_is(self):
        # Дневная смена после ночного куска выгрузки — основной случай 11.09.
        part = _part(9, 18)
        boundary = datetime.combine(dt_date(2026, 9, 11), dt_time(1, 0))
        self.assertEqual(self.clip(part, boundary), part)

    def test_part_entirely_inside_authoritative_is_dropped(self):
        # Ночь уже закрыта выгрузкой — дубля быть не должно.
        part = _part(0, 1)
        boundary = datetime.combine(dt_date(2026, 9, 11), dt_time(1, 0))
        self.assertIsNone(self.clip(part, boundary))

    def test_overlapping_part_is_clipped_to_authoritative_end(self):
        # Событие телефона началось в 00:57, а выгрузка закрыла ночь до 01:00:
        # оставляем только хвост, иначе интервалы наложатся.
        part = _part(0, 9)
        boundary = datetime.combine(dt_date(2026, 9, 11), dt_time(1, 0))
        clipped = self.clip(part, boundary)
        self.assertEqual(clipped["start_at"], boundary)
        self.assertEqual(clipped["end_at"], part["end_at"])
        self.assertEqual(clipped["duration_sec"], 8 * 3600)

    def test_clip_does_not_mutate_original_part(self):
        part = _part(0, 9)
        original_start = part["start_at"]
        self.clip(part, datetime.combine(dt_date(2026, 9, 11), dt_time(1, 0)))
        self.assertEqual(part["start_at"], original_start)

    def test_unknown_boundary_protects_whole_day(self):
        # Границу определить не удалось — ведём себя как раньше и день не трогаем:
        # потерять кусок дня безопаснее, чем задвоить интервалы в часах.
        self.assertIsNone(self.clip(_part(9, 18), None))

    def test_broken_part_is_dropped(self):
        boundary = datetime.combine(dt_date(2026, 9, 11), dt_time(1, 0))
        self.assertIsNone(self.clip({"start_at": None, "end_at": None}, boundary))


if __name__ == "__main__":
    unittest.main()
