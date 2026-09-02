# -*- coding: utf-8 -*-
"""Идентичность и время низкой оценки Chat2Desk (задача #267).

Как ломалось. Chat2Desk с вечера 01.09.2026 стал отдавать закрытый день со
сдвигом `created_at` на 5 часов вперёд (проверено живым запросом и якорем
/v1/messages, где время приходит с явной меткой UTC; целый отчёт request_stats
подтверждает раннее значение). Время оценки входило в `source_key`, поэтому у
сдвинутой копии ключ получался другой, `ON CONFLICT (source, source_key)` не
срабатывал, и на каждый повторный синк в раздел добавлялась вторая строка того
же обращения. Накопилось 116 пар; проверяющая разметила 7 обращений дважды и на
двух вынесла противоположные вердикты, не зная, что это одно и то же.

Здесь сторожим два инварианта, каждый из которых и был причиной:
1. время не участвует в идентичности, а формулу ключа считают ОДИНАКОВО синк
   (bot_schedule2.py) и миграция (SQL в database.py) — разъехаться им нельзя,
   иначе ближайшая пересинка не узнает ни одну строку и продублирует таблицу;
2. пояс при разборе времени задаётся явно: `astimezone()` без аргумента берёт
   пояс процесса, а он на Render UTC, локально Алматы — то же значение
   раскладывалось на пять часов по-разному.
"""

import ast
import re
import unittest
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"

RATING_FIELDS = [
    "valuation_request_id",
    "request_id",
    "rating_scale_id",
    "operator_id",
]


def _database_class():
    module = source_cache.parse(DATABASE_PATH.read_text(encoding="utf-8-sig"))
    return next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )


def _class_constant(name):
    for node in _database_class().body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"В классе Database нет константы {name}")


def _method(class_node, name):
    for node in class_node.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"В классе Database нет метода {name}")


def _parse_low_rating_datetime():
    """Достаём staticmethod без импорта модуля: database.py на импорте зовёт
    time.tzset(), которого на Windows нет."""
    node = _method(_database_class(), "_parse_low_rating_datetime")
    node = ast.FunctionDef(
        name=node.name, args=node.args, body=node.body,
        decorator_list=[], returns=None, type_comment=None,
        type_params=getattr(node, "type_params", []),
    )
    ast.fix_missing_locations(ast.copy_location(node, _method(_database_class(), "_parse_low_rating_datetime")))
    namespace = {"datetime": datetime, "date": date, "dt_time": datetime.min.time().__class__,
                 "ZoneInfo": ZoneInfo}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(DATABASE_PATH), "exec"), namespace)
    return namespace["_parse_low_rating_datetime"]


def _bot_source_key_function():
    module = source_cache.parse(BOT_PATH.read_text(encoding="utf-8"))
    node = next(
        n for n in module.body
        if isinstance(n, ast.FunctionDef) and n.name == "_chat2desk_rating_source_key"
    )
    return ast.get_source_segment(BOT_PATH.read_text(encoding="utf-8"), node) or ""


class ChatLowRatingIdentityTest(unittest.TestCase):
    def test_sql_key_and_sync_key_use_the_same_fields_in_the_same_order(self):
        """Формулу ключа считают в двух местах — они обязаны совпадать.

        Ключ переименовывает миграция (SQL), а новые строки пишет синк (Python).
        Если формулы разойдутся, синк перестанет узнавать уже сохранённые строки
        и продублирует всю таблицу — ровно та поломка, что и починена.
        """
        key_sql = _class_constant("_C2D_RATING_KEY_SQL")
        sync_source = _bot_source_key_function()

        self.assertIn("'c2d-rating:'", key_sql)
        self.assertIn("CHAT2DESK_RATING_SOURCE_KEY_PREFIX", sync_source)

        sql_fields = re.findall(r"raw_payload->>'([a-z_]+)'", key_sql)
        self.assertEqual(sql_fields, RATING_FIELDS)

        # Порядок обращений к полям в синке — тот же и без времени.
        sync_fields = re.findall(r"_chat2desk_row_first\(row, '([a-z_]+)'", sync_source)
        stable_part = sync_source.split("# Номеров заявки нет вовсе")[0]
        stable_fields = re.findall(r"_chat2desk_row_first\(row, '([a-z_]+)'", stable_part)
        self.assertEqual(stable_fields, RATING_FIELDS)
        self.assertIn("created_at", sync_fields[len(RATING_FIELDS):],
                      "время допустимо только в резервной ветке без номеров заявки")

    def test_time_is_not_part_of_identity(self):
        """В устойчивой ветке ключа не должно быть ни времени, ни дня, ни балла.

        Смотрим сам код, а не текст: в описании функции слово created_at
        упомянуто намеренно — там сказано, почему его убрали.
        """
        module = source_cache.parse(BOT_PATH.read_text(encoding="utf-8"))
        node = next(
            n for n in module.body
            if isinstance(n, ast.FunctionDef) and n.name == "_chat2desk_rating_source_key"
        )
        stable_branch = next(n for n in node.body if isinstance(n, ast.If))
        used = set()
        for inner in ast.walk(stable_branch):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                used.add(inner.value)
            elif isinstance(inner, ast.Name):
                used.add(inner.id)
        for forbidden in ("created_at", "request_start", "date", "metric_day", "score"):
            self.assertNotIn(
                forbidden, used,
                f"{forbidden} снова попал в ключ идентичности — дубли вернутся"
            )
        self.assertNotIn("raw_payload->>'created_at'", _class_constant("_C2D_RATING_KEY_SQL"))

    def test_sql_key_scope_skips_rows_without_request_numbers(self):
        """Строку без номеров заявки переименовывать нельзя: склеить её нечем,
        и её ключ в синке остаётся прежним, со временем."""
        scope = _class_constant("_C2D_RATING_KEY_SCOPE_SQL")
        self.assertIn("chat2desk_rating", scope)
        self.assertIn("valuation_request_id", scope)
        self.assertIn("request_id", scope)

    def test_low_rating_datetime_is_converted_to_almaty(self):
        """Значение со смещением приводится к Алматы, а не к поясу машины."""
        parse = _parse_low_rating_datetime()
        self.assertEqual(parse("2026-09-01T00:34:55+00:00"), datetime(2026, 9, 1, 5, 34, 55))
        self.assertEqual(parse("2026-08-31T19:34:55Z"), datetime(2026, 9, 1, 0, 34, 55))
        # Наивную строку не трогаем: Chat2Desk отдаёт её уже местной.
        self.assertEqual(parse("2026-09-01 00:34:55"), datetime(2026, 9, 1, 0, 34, 55))
        # datetime и date проходят как раньше.
        self.assertEqual(parse(datetime(2026, 9, 1, 12, 0)), datetime(2026, 9, 1, 12, 0))
        self.assertEqual(parse(date(2026, 9, 1)), datetime(2026, 9, 1, 0, 0))
        self.assertIsNone(parse(""))
        self.assertIsNone(parse("не дата"))

    def test_low_rating_datetime_names_its_timezone_explicitly(self):
        """Страж: `astimezone()` без аргумента здесь запрещён.

        На машине с TZ=Asia/Almaty голый вызов даёт тот же ответ, что и явный,
        поэтому проверкой значения регресс не поймать — смотрим на сам вызов.
        """
        node = _method(_database_class(), "_parse_low_rating_datetime")
        bare = [
            call for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "astimezone"
            and not call.args
        ]
        self.assertEqual(
            bare, [],
            "astimezone() без пояса берёт пояс процесса: на Render это UTC, "
            "локально Алматы — время оценки разъедется с её днём"
        )


if __name__ == "__main__":
    unittest.main()
