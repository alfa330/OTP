"""Тесты «операторско-дневного» гейта синка Oktell.

Проверяют, что статусы/звонки из Oktell тянутся и сохраняются только за дни,
когда оператор был в группе с операторской моделью. Сценарий из задачи: оператор
был оператором до 8-го числа, затем стал чат-менеджером — его операторские дни до
перехода должны синхронизироваться, а чат-дни после — нет.

Используется тот же приём, что и в test_status_import_chat2desk.py: целевые функции
извлекаются из bot_schedule2.py через AST и исполняются в изолированном namespace,
где их зависимости подменены стабами (модуль целиком импортировать нельзя — у него
сетевые/БД сайд-эффекты на импорте).
"""

import ast
import unittest
from datetime import datetime, timedelta, date
from pathlib import Path


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"

CALCULATION_MODEL_OPERATOR = "operator"
CALCULATION_MODEL_CHAT_MANAGER = "chat_manager"
CALCULATION_MODEL_TEZ_LINE = "tez_line"


def _extract_functions(names):
    """Возвращает namespace с указанными функциями из bot_schedule2.py."""
    module = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    wanted = set(names)
    selected = [
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    found = {n.name for n in selected}
    missing = wanted - found
    if missing:
        raise AssertionError("Не найдены функции в bot_schedule2.py: %s" % sorted(missing))
    ns = {
        "datetime": datetime,
        "timedelta": timedelta,
        "CALCULATION_MODEL_CHAT_MANAGER": CALCULATION_MODEL_CHAT_MANAGER,
        "STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT": 30,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), ns)
    return ns


# ── Стабы зависимостей _status_import_build_status_events ───────────────────────

def _stub_parse_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _stub_resolve_operator_matches(operator_name, operator_lookup):
    key = " ".join(str(operator_name or "").strip().lower().split())
    return list(operator_lookup.get(key) or [])


def _stub_resolve_display_state(state_name, state_note):
    return {"kind": "status", "key": " ".join(str(state_name or "").strip().lower().split())}


def _stub_normalize_key(value):
    return " ".join(str(value or "").strip().lower().split())


def _stub_split_segment_by_day(start, end):
    # В тестовых данных сегменты не пересекают полночь — одна часть на сегмент.
    return [{
        "date": start.date(),
        "start": start,
        "end": end,
        "duration_sec": int((end - start).total_seconds()),
    }]


def _build_status_events_ns():
    ns = _extract_functions(["_status_import_build_status_events"])
    ns["_status_import_parse_datetime"] = _stub_parse_datetime
    ns["_status_import_resolve_operator_matches"] = _stub_resolve_operator_matches
    ns["_status_import_resolve_display_state"] = _stub_resolve_display_state
    ns["_status_import_normalize_key"] = _stub_normalize_key
    ns["_status_import_split_segment_by_day"] = _stub_split_segment_by_day
    return ns


class _FakeDB:
    """Подменяет db в гейте/lookup. model_by_day: {date: {op_id: model_code}}."""

    def __init__(self, model_by_day=None, all_operators=None):
        self._model_by_day = model_by_day or {}
        self._all_operators = all_operators or []

    def get_operator_calculation_models_as_of(self, operator_ids, as_of):
        day_map = self._model_by_day.get(as_of, {})
        # По умолчанию (нет членства/направления) — операторская модель, как в проде.
        return {int(op): day_map.get(int(op), CALCULATION_MODEL_OPERATOR) for op in operator_ids}

    def get_all_operators(self):
        return self._all_operators


class StatusEventsDayFilterTests(unittest.TestCase):
    def setUp(self):
        self.ns = _build_status_events_ns()
        self.build = self.ns["_status_import_build_status_events"]
        # Бехруз id=1 — оператор до 8-го, затем чат-менеджер.
        self.lookup = {"бехруз": [{"id": 1, "name": "Бехруз"}]}

    def _rows(self):
        # Готов -> Перерыв в один из дней; повторяем для операторского и чат-дня.
        return [
            {"row_num": 2, "operator_name": "Бехруз", "state_name": "Готов",
             "state_note": "", "time_change": "2026-06-05 09:00:00"},
            {"row_num": 3, "operator_name": "Бехруз", "state_name": "Перерыв",
             "state_note": "", "time_change": "2026-06-05 13:00:00"},
            {"row_num": 4, "operator_name": "Бехруз", "state_name": "Готов",
             "state_note": "", "time_change": "2026-06-10 09:00:00"},
            {"row_num": 5, "operator_name": "Бехруз", "state_name": "Перерыв",
             "state_note": "", "time_change": "2026-06-10 13:00:00"},
        ]

    def test_no_filter_keeps_all_days(self):
        parsed = self.build(self._rows(), self.lookup)
        days = {e["event_at"].date() for e in parsed["events"]}
        self.assertEqual(days, {date(2026, 6, 5), date(2026, 6, 10)})
        self.assertEqual(int(parsed.get("skipped_non_operator_day") or 0), 0)

    def test_filter_drops_non_operator_days(self):
        # Оператор до 8-го: 5 июня — операторский день, 10 июня — чат-день.
        def is_op_day(operator_id, day_iso):
            return day_iso < "2026-06-08"

        parsed = self.build(self._rows(), self.lookup, operator_day_filter=is_op_day)
        days = {e["event_at"].date() for e in parsed["events"]}
        self.assertEqual(days, {date(2026, 6, 5)})
        # Оба события чат-дня (10 июня) отброшены.
        self.assertEqual(int(parsed["skipped_non_operator_day"]), 2)
        # Сегменты построены только за операторский день.
        seg_days = {s["status_date"] for s in parsed["segments"]}
        self.assertTrue(all(d.startswith("2026-06-05") for d in seg_days))


class OperatorModelGateTests(unittest.TestCase):
    def setUp(self):
        ns = _extract_functions(["_oktell_operator_model_gate"])
        self.gate = ns["_oktell_operator_model_gate"]

    def test_switcher_included_but_chat_days_excluded(self):
        # op 1 (Бехруз): оператор 5-7 июня, чат-менеджер 8-10 июня.
        # op 2: чат-менеджер весь период. op 3: оператор весь период (нет в карте -> дефолт).
        model_by_day = {}
        d = date(2026, 6, 5)
        while d <= date(2026, 6, 10):
            model_by_day[d] = {
                1: CALCULATION_MODEL_OPERATOR if d.day <= 7 else CALCULATION_MODEL_CHAT_MANAGER,
                2: CALCULATION_MODEL_CHAT_MANAGER,
            }
            d += timedelta(days=1)
        fake = _FakeDB(model_by_day=model_by_day)
        ns_globals = self.gate.__globals__
        ns_globals["db"] = fake

        op_ids, is_op_day = self.gate(date(2026, 6, 5), date(2026, 6, 10), [1, 2, 3])

        # Бехруз и оператор-3 имеют операторские дни; чистый чат-менеджер (2) — нет.
        self.assertEqual(op_ids, {1, 3})
        # Операторские дни Бехруза.
        self.assertTrue(is_op_day(1, "2026-06-05"))
        self.assertTrue(is_op_day(1, "2026-06-07"))
        # Чат-дни Бехруза.
        self.assertFalse(is_op_day(1, "2026-06-08"))
        self.assertFalse(is_op_day(1, "2026-06-10"))
        # Чистый чат-менеджер — всегда False.
        self.assertFalse(is_op_day(2, "2026-06-05"))
        # Оператор без записи в карте — дефолт операторский, True.
        self.assertTrue(is_op_day(3, "2026-06-05"))


class OperatorLookupRestrictTests(unittest.TestCase):
    def setUp(self):
        ns = _extract_functions(["_status_import_build_operator_lookup"])
        self.build_lookup = ns["_status_import_build_operator_lookup"]
        self.ns = ns
        # Минимальный variants-стаб: один ключ = нормализованное имя.
        self.ns["_status_import_operator_name_variants"] = lambda name: [
            " ".join(str(name or "").strip().lower().split())
        ]

    def _operators(self):
        # row: id, name, *, direction_name(idx7), calc_model(idx8)
        return [
            (1, "Бехруз", None, None, None, None, None, "Чат менеджер", "chat_manager"),
            (2, "Оператор Два", None, None, None, None, None, "Операторы", "operator"),
            (3, "Чатник Три", None, None, None, None, None, "Чат менеджер", "chat_manager"),
        ]

    def test_restrict_to_ids_includes_only_listed(self):
        self.ns["db"] = _FakeDB(all_operators=self._operators())
        # Бехруз (1) — переключившийся, попал в гейт-набор несмотря на текущую чат-модель.
        lookup = self.build_lookup(restrict_to_ids={1, 2})
        ids = {info["id"] for infos in lookup.values() for info in infos}
        self.assertEqual(ids, {1, 2})
        self.assertIn("бехруз", lookup)
        self.assertNotIn("чатник три", lookup)

    def test_exclude_chat_managers_still_works(self):
        self.ns["db"] = _FakeDB(all_operators=self._operators())
        lookup = self.build_lookup(exclude_chat_managers=True)
        ids = {info["id"] for infos in lookup.values() for info in infos}
        self.assertEqual(ids, {2})


if __name__ == "__main__":
    unittest.main()
