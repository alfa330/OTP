import ast
import math
from functools import lru_cache
from datetime import datetime
import re
import textwrap
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"


@lru_cache(maxsize=None)
def _parsed_module(path):
    source = path.read_text(encoding="utf-8-sig")
    return source, source_cache.parse(source)


@lru_cache(maxsize=None)
def _function_source(path, function_name, class_name=None):
    source, module = _parsed_module(path)
    body = module.body
    if class_name:
        class_node = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        body = class_node.body
    node = next(
        item for item in body
        if isinstance(item, ast.FunctionDef) and item.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, node))


class _BreakRuleDummy:
    def _normalize_break_durations_list(self, value):
        return [int(item) for item in (value or []) if int(item) > 0]

    def _is_chat_manager_direction(self, direction_name):
        return str(direction_name or "").strip().lower() in {"чат менеджер", "chat manager"}


class _MergeDummy:
    def _schedule_interval_minutes(self, start_time_value, end_time_value):
        def to_minutes(value):
            hh, mm = str(value).split(":", 1)
            return int(hh) * 60 + int(mm)

        start_min = to_minutes(start_time_value)
        end_min = to_minutes(end_time_value)
        if end_min <= start_min:
            end_min += 24 * 60
        return start_min, end_min

    def _normalize_schedule_time(self, value, field_name):
        return value


class _BreakAdjustDummy(_MergeDummy):
    def __init__(self, occupied=None, cross_gap=0):
        self.occupied = occupied or []
        self.cross_gap = int(cross_gap or 0)

    def _normalize_schedule_date(self, value):
        if hasattr(value, "year") and not isinstance(value, str):
            return value
        return datetime.strptime(str(value), "%Y-%m-%d").date()

    def _almaty_now(self):
        return datetime(2026, 8, 6, 12, 0)

    def _load_occupied_break_intervals_for_operator_date_tx(self, cursor, operator_id, shift_date):
        return list(self.occupied)

    def _get_operator_direction_name_tx(self, cursor, operator_id):
        return "Направление"

    def _get_break_cross_operator_gap_for_direction_tx(self, cursor, direction_name):
        return self.cross_gap


class _TechReasonDummy:
    pass


BREAK_ADJUST_METHODS = (
    "_break_intervals_overlap",
    "_merge_break_intervals",
    "_normalize_break_intervals_soft",
    "_tag_break_intervals_with_cross_gap",
    "_break_layout_spacing",
    "_break_start_bounds_for_index",
    "_break_total_overlap_minutes",
    "_break_allowed_pair_overlap_minutes",
    "_break_pair_overlap_excess_minutes",
    "_break_concurrency_penalty_minutes",
    "_break_cross_gap_deficit_minutes",
    "_find_best_break_start",
    "_place_break_durations_centered_minutes",
    "_fit_break_durations_to_window",
    "_seed_break_positions_from_existing",
    "_split_breaks_by_freeze_boundary",
    "_remaining_break_durations_after_used",
    "_break_freeze_boundary_minutes",
    "_adjust_shift_breaks_against_occupied_tx",
)


def _module_constants(path, names):
    """Модульные константы правил перерывов — их же читают функции в exec-namespace."""
    source, module = _parsed_module(path)
    wanted = set(names)
    values = {}
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in wanted:
                values[target.id] = ast.literal_eval(node.value)
    missing = wanted - set(values)
    assert not missing, f"нет констант {sorted(missing)} в {path.name}"
    return values


BREAK_RULE_CONSTANT_NAMES = (
    "SHIFT_BREAK_MAX_CONCURRENT",
    "SHIFT_BREAK_HANDOVER_OVERLAP_MINUTES",
    "SHIFT_BREAK_PAIR_OVERLAP_BASE_MINUTES",
    "SHIFT_BREAK_PAIR_OVERLAP_SHARE",
    "SHIFT_BREAK_WEIGHT_CONCURRENCY",
    "SHIFT_BREAK_WEIGHT_PAIR_EXCESS",
    "SHIFT_BREAK_WEIGHT_CROSS_GAP",
    "SHIFT_BREAK_WEIGHT_OVERLAP",
)
BREAK_RULE_CONSTANTS = _module_constants(DATABASE_PATH, BREAK_RULE_CONSTANT_NAMES)


def _make_break_adjust_dummy(occupied=None):
    namespace = {
        "math": math,
        "datetime": datetime,
        "SHIFT_BREAK_MIN_EDGE_MARGIN_MINUTES": 30,
        "SHIFT_BREAK_MIN_GAP_MINUTES": 15,
        "SHIFT_BREAK_PLANNING_BUFFER_MINUTES": 15,
        **BREAK_RULE_CONSTANTS,
    }
    for function_name in BREAK_ADJUST_METHODS:
        exec(_function_source(DATABASE_PATH, function_name, class_name="Database"), namespace)

    dummy = _BreakAdjustDummy(occupied=occupied)
    for function_name in BREAK_ADJUST_METHODS:
        setattr(dummy, function_name, namespace[function_name].__get__(dummy, _BreakAdjustDummy))
    return dummy


class WorkScheduleBreakRuleTests(unittest.TestCase):
    def test_phone_shift_type_normalization_and_merge_priority(self):
        namespace = {
            "Any": Any,
            "Dict": Dict,
            "List": List,
            "Optional": Optional,
            "WORK_SHIFT_TYPE_REGULAR": "regular",
            "WORK_SHIFT_TYPE_OFFICE_PRACTICE": "office_practice",
            "WORK_SHIFT_TYPE_PHONE_SHIFT": "phone_shift",
            "WORK_SHIFT_TYPE_ALLOWED": {"regular", "office_practice", "phone_shift"},
            "WORK_SHIFT_TYPE_PRIORITY": {
                "regular": 0,
                "office_practice": 1,
                "phone_shift": 2,
            },
        }
        for function_name in (
            "_time_to_minutes",
            "_minutes_to_time",
            "_normalize_work_shift_type_value",
            "_work_shift_type_priority",
            "_merge_shifts_for_date",
        ):
            exec(_function_source(DATABASE_PATH, function_name), namespace)

        normalize = namespace["_normalize_work_shift_type_value"]
        merge = namespace["_merge_shifts_for_date"]

        self.assertEqual(normalize("phone_shift"), "phone_shift")
        self.assertEqual(normalize("phones"), "phone_shift")
        self.assertEqual(normalize("office_practice"), "office_practice")

        result = merge([
            {"id": 1, "start": "09:00", "end": "13:00", "shift_type": "regular"},
            {"id": 2, "start": "10:00", "end": "12:00", "shift_type": "phone_shift"},
            {"id": 3, "start": "12:00", "end": "14:00", "shift_type": "office_practice"},
        ])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["start"], "09:00")
        self.assertEqual(result[0]["end"], "14:00")
        self.assertEqual(result[0]["shift_type"], "phone_shift")

    def test_phone_shift_training_is_exposed_as_offline_activity_source(self):
        recalc_source = _function_source(
            DATABASE_PATH,
            "_recalculate_auto_daily_hours_tx",
            class_name="Database",
        )
        list_source = _function_source(
            DATABASE_PATH,
            "get_operator_offline_activities",
            class_name="Database",
        )
        summary_source = _function_source(
            DATABASE_PATH,
            "get_hours_summary",
            class_name="Database",
        )
        loader_source = _function_source(
            DATABASE_PATH,
            "_load_phone_shift_training_offline_intervals_by_operator_day_tx",
            class_name="Database",
        )
        manual_loader_source = _function_source(
            DATABASE_PATH,
            "_load_phone_shift_manual_training_offline_intervals_by_operator_day_tx",
            class_name="Database",
        )

        self.assertIn("WORK_SHIFT_TYPE_PHONE_SHIFT", recalc_source)
        self.assertIn("phone_shift_training_intervals", recalc_source)
        self.assertIn("_schedule_auto_subtract_intervals", recalc_source)
        self.assertIn("WORK_SHIFT_PHONE_SHIFT_OFFLINE_COMMENT", loader_source)
        self.assertIn("work_shift_training_status", loader_source)
        self.assertIn("training_phone_shift", manual_loader_source)
        self.assertIn("counted_only", manual_loader_source)
        self.assertIn("manual_phone_training_counted_totals", summary_source)
        self.assertIn("manual_phone_training_map", list_source)
        self.assertIn("_load_phone_shift_training_offline_intervals_by_operator_day_tx", list_source)
        self.assertIn("'read_only': True", list_source)

    def test_phone_shift_offline_activity_uses_day_number_key_in_hours_payload(self):
        namespace = {
            "WORK_SHIFT_PHONE_SHIFT_OFFLINE_COMMENT": "смена на телефонах",
            "WORK_SHIFT_TYPE_PHONE_SHIFT": "phone_shift",
        }
        exec(
            _function_source(
                DATABASE_PATH,
                "_load_offline_activities_by_operator_day_tx",
                class_name="Database",
            ),
            namespace,
        )
        load_offline = namespace["_load_offline_activities_by_operator_day_tx"]

        class EmptyCursor:
            def execute(self, query, params=None):
                return None

            def fetchall(self):
                return []

        class OfflinePayloadDummy:
            _load_offline_activities_by_operator_day_tx = load_offline

            def _load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(self, **kwargs):
                return {}, {}

            def _load_phone_shift_training_offline_intervals_by_operator_day_tx(self, **kwargs):
                return {
                    42: {
                        "2026-08-01": [{
                            "id": "phone-shift-42-2026-08-01",
                            "date": "2026-08-01",
                            "start_time": "00:00",
                            "end_time": "02:00",
                            "start_seconds": 0,
                            "end_seconds": 7200,
                            "source": "work_shift_training_status",
                        }]
                    }
                }, {42: 2.0}

            def _sum_phone_shift_offline_interval_maps(self, operator_ids, *interval_maps):
                return {42: 2.0}

            def _schedule_auto_seconds_to_display_minutes(self, seconds):
                return int(seconds) // 60

            def _schedule_interval_minutes(self, start_time, end_time):
                return 0, 0

        activities, totals = OfflinePayloadDummy()._load_offline_activities_by_operator_day_tx(
            cursor=EmptyCursor(),
            operator_ids=[42],
            start_date="2026-08-01",
            end_date="2026-08-31",
        )

        self.assertEqual(list(activities[42]), ["1"])
        self.assertEqual(activities[42]["1"][0]["date"], "2026-08-01")
        self.assertEqual(totals[42], 2.0)

    def test_database_custom_direction_rules_disable_default_fallback_for_gaps(self):
        namespace = {}
        exec(_function_source(DATABASE_PATH, "_pick_break_durations_for_shift", class_name="Database"), namespace)
        pick = namespace["_pick_break_durations_for_shift"]
        rules = [
            {"minMinutes": 330, "maxMinutes": 390, "breakDurations": [15]},
        ]

        self.assertEqual(pick(_BreakRuleDummy(), 300, direction_name="Основа", direction_rules=rules), [])
        self.assertEqual(pick(_BreakRuleDummy(), 300, direction_name="Основа", direction_rules=[]), [15])

    def test_tech_reason_status_detection_accepts_chat2desk_aliases(self):
        namespace = {"re": re}
        exec(_function_source(DATABASE_PATH, "_schedule_auto_compact_status_key", class_name="Database"), namespace)
        exec(_function_source(DATABASE_PATH, "_schedule_auto_is_tech_reason_status_key", class_name="Database"), namespace)
        dummy = _TechReasonDummy()
        dummy._schedule_auto_compact_status_key = namespace["_schedule_auto_compact_status_key"].__get__(dummy, _TechReasonDummy)
        is_tech_reason = namespace["_schedule_auto_is_tech_reason_status_key"]

        self.assertTrue(is_tech_reason(dummy, "тех причина"))
        self.assertTrue(is_tech_reason(dummy, "tech_break"))
        self.assertTrue(is_tech_reason(dummy, "status.tech_break"))

    def test_import_simulation_custom_direction_rules_disable_default_fallback_for_gaps(self):
        namespace = {}
        for function_name in (
            "_operator_info_is_chat_manager",
            "_ws_normalize_direction_key",
            "_ws_normalize_break_durations",
            "_ws_is_chat_manager_direction",
            "_ws_pick_break_durations_for_shift",
        ):
            exec(_function_source(BOT_PATH, function_name), namespace)
        pick = namespace["_ws_pick_break_durations_for_shift"]
        rules_map = {
            "основа": [
                {"minMinutes": 330, "maxMinutes": 390, "breakDurations": [15]},
            ]
        }

        self.assertEqual(pick(300, direction_value="Основа", break_rules_map=rules_map), [])
        self.assertEqual(pick(300, direction_value="Основа", break_rules_map={}), [15])

    def test_database_break_adjustment_keeps_breaks_away_from_shift_edges_and_each_other(self):
        dummy = _make_break_adjust_dummy()

        result = dummy._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date="2026-05-21",
            start_time="12:00",
            end_time="21:00",
            breaks=[
                {"start": 12 * 60 + 10, "end": 12 * 60 + 25},
                {"start": 12 * 60 + 25, "end": 12 * 60 + 40},
                {"start": 16 * 60 + 15, "end": 16 * 60 + 45},
            ],
        )

        self.assertEqual([item["end"] - item["start"] for item in result], [15, 15, 30])
        self.assertGreaterEqual(result[0]["start"] - 12 * 60, 90)
        self.assertGreaterEqual(21 * 60 - result[-1]["end"], 90)
        self.assertTrue(all(b["start"] - a["end"] >= 45 for a, b in zip(result, result[1:])))

    def test_database_break_adjustment_prefers_good_overlap_slot_over_edge_violation(self):
        dummy = _make_break_adjust_dummy(occupied=[{"start": 16 * 60 + 15, "end": 16 * 60 + 45}])

        result = dummy._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date="2026-05-21",
            start_time="12:00",
            end_time="21:00",
            breaks=[
                {"start": 14 * 60 + 10, "end": 14 * 60 + 25},
                {"start": 16 * 60 + 15, "end": 16 * 60 + 45},
                {"start": 18 * 60 + 40, "end": 18 * 60 + 55},
            ],
        )

        lunch = result[1]
        self.assertFalse(dummy._break_intervals_overlap(lunch, {"start": 16 * 60 + 15, "end": 16 * 60 + 45}))
        self.assertGreaterEqual(lunch["start"] - result[0]["end"], 45)
        self.assertGreaterEqual(result[2]["start"] - lunch["end"], 45)

    def test_database_break_adjustment_keeps_cross_operator_gap_from_other_operators(self):
        # Перерыв коллеги 16:15-16:45 и требование держать 10 минут между
        # перерывами разных операторов: свой обед не должен встать вплотную.
        colleague_break = {"start": 16 * 60 + 15, "end": 16 * 60 + 45}
        dummy = _make_break_adjust_dummy(occupied=[colleague_break])

        result = dummy._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date="2026-08-04",
            start_time="12:00",
            end_time="21:00",
            breaks=[
                {"start": 14 * 60 + 10, "end": 14 * 60 + 25},
                {"start": 16 * 60 + 15, "end": 16 * 60 + 45},
                {"start": 18 * 60 + 40, "end": 18 * 60 + 55},
            ],
            cross_gap_minutes=10,
        )

        self.assertEqual([item["end"] - item["start"] for item in result], [15, 30, 15])
        for item in result:
            distance = (
                item["start"] - colleague_break["end"]
                if item["start"] >= colleague_break["end"]
                else colleague_break["start"] - item["end"]
            )
            self.assertGreaterEqual(distance, 10)

    def test_database_break_adjustment_without_gap_setting_keeps_previous_layout(self):
        colleague_break = {"start": 16 * 60 + 15, "end": 16 * 60 + 45}
        breaks = [
            {"start": 14 * 60 + 10, "end": 14 * 60 + 25},
            {"start": 16 * 60 + 15, "end": 16 * 60 + 45},
            {"start": 18 * 60 + 40, "end": 18 * 60 + 55},
        ]

        without_gap = _make_break_adjust_dummy(occupied=[colleague_break])._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date="2026-08-04",
            start_time="12:00",
            end_time="21:00",
            breaks=[dict(item) for item in breaks],
            cross_gap_minutes=0,
        )
        legacy = _make_break_adjust_dummy(occupied=[colleague_break])._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date="2026-08-04",
            start_time="12:00",
            end_time="21:00",
            breaks=[dict(item) for item in breaks],
        )

        self.assertEqual(without_gap, legacy)

    def test_database_cross_gap_is_tagged_not_baked_into_intervals(self):
        # Раздувать интервалы коллег нельзя: по исходным считаются и лимит
        # пересечения пары перерывов, и «одновременно не больше двух».
        dummy = _make_break_adjust_dummy()
        self.assertEqual(
            dummy._tag_break_intervals_with_cross_gap(
                [{"start": 645, "end": 675}, {"start": 600, "end": 630}],
                10,
            ),
            [
                {"start": 600, "end": 630, "gap": 10},
                {"start": 645, "end": 675, "gap": 10},
            ],
        )
        self.assertEqual(
            dummy._tag_break_intervals_with_cross_gap([{"start": 600, "end": 630}], 0),
            [{"start": 600, "end": 630, "gap": 0}],
        )

    # --- Задача #175: равномерность и допустимые пересечения --------------------

    # Смена 10:00-21:00, норма [15, 30, 15]: равномерные точки — 12:40, 15:15, 18:10.
    CROWDED_SHIFT = (10 * 60, 21 * 60)
    CROWDED_DURATIONS = [15, 30, 15]
    # Вторая половина смены плотно занята коллегами (окна между их перерывами по
    # 10 минут), а начало смены свободно. Раньше обед из-за этого уезжал в 14:00 —
    # к первому перерыву вплотную, — и смена оставалась с дырой в 3,5 часа.
    CROWDED_COLLEAGUES = [
        {"start": start, "end": start + 30}
        for start in (870, 910, 950, 990, 1030, 1070, 1110, 1150, 1190)
    ]

    def _even_targets(self, seg_start, seg_end, durations):
        span = seg_end - seg_start
        return [
            int(round((seg_start + span * ((idx + 1) / (len(durations) + 1)) - size / 2) / 5) * 5)
            for idx, size in enumerate(durations)
        ]

    def _crowded_layout(self):
        seg_start, seg_end = self.CROWDED_SHIFT
        dummy = _make_break_adjust_dummy(occupied=[dict(x) for x in self.CROWDED_COLLEAGUES])
        planned = dummy._place_break_durations_centered_minutes(seg_start, seg_end, self.CROWDED_DURATIONS)
        return dummy, dummy._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date="2026-08-20",
            start_time="10:00",
            end_time="21:00",
            breaks=planned,
            cross_gap_minutes=0,
        )

    def test_crowded_direction_keeps_breaks_evenly_spread_over_the_shift(self):
        # Задача #175: перерывы больше не сбиваются в начало смены, когда у
        # коллег занята вся середина, — каждый остаётся у своей равномерной точки.
        seg_start, seg_end = self.CROWDED_SHIFT
        _, result = self._crowded_layout()

        self.assertEqual([item["end"] - item["start"] for item in result], self.CROWDED_DURATIONS)
        targets = self._even_targets(seg_start, seg_end, self.CROWDED_DURATIONS)
        for item, target in zip(result, targets):
            self.assertLessEqual(
                abs(item["start"] - target),
                30,
                f"перерыв {item} уехал от равномерной точки {target}",
            )
        # И не жмутся друг к другу: между ними куда больше обязательного минимума в 45 минут.
        self.assertTrue(
            all(b["start"] - a["end"] >= 120 for a, b in zip(result, result[1:])),
            result,
        )

    def test_crowded_direction_respects_pair_overlap_limit(self):
        # Пересечение допустимо, но не любое: 15-минутный — до 10 минут, 30-минутный — до 15.
        dummy, result = self._crowded_layout()
        for item in result:
            for colleague in self.CROWDED_COLLEAGUES:
                overlap = min(item["end"], colleague["end"]) - max(item["start"], colleague["start"])
                if overlap <= 0:
                    continue
                allowed = dummy._break_allowed_pair_overlap_minutes(
                    item["end"] - item["start"],
                    colleague["end"] - colleague["start"],
                )
                self.assertLessEqual(overlap, allowed, f"{item} × {colleague}")

    def test_half_minute_snap_rounds_up_like_the_browser(self):
        # Python округляет половину «к чётному», JS — вверх. Из-за этого планировщик
        # показывал перерыв на 5 минут левее того, что сохранял сервер.
        dummy = _make_break_adjust_dummy()
        # Центр доли — 690, минус половина 15-минутного перерыва → 682.5.
        self.assertEqual(
            dummy._place_break_durations_centered_minutes(300, 1080, [15]),
            [{"start": 685, "end": 700}],
        )

    def test_allowed_pair_overlap_follows_break_duration(self):
        dummy = _make_break_adjust_dummy()
        self.assertEqual(dummy._break_allowed_pair_overlap_minutes(15, 15), 10)
        self.assertEqual(dummy._break_allowed_pair_overlap_minutes(30, 30), 15)
        # У разных длительностей действует более строгое из двух правил.
        self.assertEqual(dummy._break_allowed_pair_overlap_minutes(15, 30), 10)
        # Пересечение не может быть длиннее самого перерыва.
        self.assertEqual(dummy._break_allowed_pair_overlap_minutes(5, 30), 5)

    def test_third_operator_on_break_is_allowed_only_as_short_handover(self):
        # Двое уже на перерыве 14:00-14:30; третий может выйти только на пересдаче,
        # не больше чем на 5 минут внахлёст.
        dummy = _make_break_adjust_dummy()
        pair = [{"start": 14 * 60, "end": 14 * 60 + 30}, {"start": 14 * 60, "end": 14 * 60 + 30}]
        self.assertEqual(dummy._break_concurrency_penalty_minutes({"start": 14 * 60 + 25, "end": 14 * 60 + 40}, pair), 0)
        self.assertEqual(dummy._break_concurrency_penalty_minutes({"start": 14 * 60 + 20, "end": 14 * 60 + 35}, pair), 5)
        # Один коллега на перерыве — это норма, а не «третий».
        self.assertEqual(dummy._break_concurrency_penalty_minutes({"start": 14 * 60, "end": 14 * 60 + 30}, pair[:1]), 0)
        # Четвёртый оператор не оправдан ничем.
        trio = pair + [{"start": 14 * 60, "end": 14 * 60 + 30}]
        self.assertGreater(dummy._break_concurrency_penalty_minutes({"start": 14 * 60, "end": 14 * 60 + 5}, trio), 5)

    def test_break_placement_avoids_becoming_the_third_operator_on_break(self):
        # Свободного окна без коллег нет вовсе, но пара 15:00-15:30 занята дважды:
        # автоматика обязана уйти от неё, а не встать третьей.
        crowd = [
            {"start": 15 * 60, "end": 15 * 60 + 30},
            {"start": 15 * 60, "end": 15 * 60 + 30},
        ]
        dummy = _make_break_adjust_dummy(occupied=[dict(x) for x in crowd])
        result = dummy._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date="2026-08-20",
            start_time="12:00",
            end_time="18:00",
            breaks=[{"start": 15 * 60, "end": 15 * 60 + 15}],
            cross_gap_minutes=0,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(dummy._break_concurrency_penalty_minutes(result[0], crowd), 0)

    def test_import_simulation_lays_breaks_out_exactly_like_the_server(self):
        # Алгоритм живёт в трёх местах; при расхождении фронт покажет одно, а
        # сервер запишет другое. Гоняем импорт-симуляцию на том же сценарии.
        namespace = {
            "math": math,
            "datetime": __import__("datetime").datetime,
            "timedelta": __import__("datetime").timedelta,
            **BREAK_RULE_CONSTANTS,
        }
        for function_name in (
            "_ws_time_to_minutes",
            "_ws_normalize_direction_key",
            "_ws_is_chat_manager_direction",
            "_ws_normalize_break_durations",
            "_ws_pick_break_durations_for_shift",
            "_ws_place_break_durations_centered",
            "_ws_compute_breaks_for_shift_minutes",
            "_ws_intervals_overlap",
            "_ws_parse_date_str",
            "_ws_date_str",
            "_ws_add_days_str",
            "_ws_cross_operator_gap_for_direction",
            "_ws_build_occupied_intervals_for_date",
            "_ws_break_layout_spacing",
            "_ws_break_start_bounds_for_index",
            "_ws_break_total_overlap_minutes",
            "_ws_break_allowed_pair_overlap_minutes",
            "_ws_break_pair_overlap_excess_minutes",
            "_ws_break_concurrency_penalty_minutes",
            "_ws_break_cross_gap_deficit_minutes",
            "_ws_find_best_break_start",
            "_ws_adjust_breaks_for_operator_on_date",
        ):
            exec(_function_source(BOT_PATH, function_name), namespace)

        date_str = "2026-08-20"
        colleagues = [
            {
                "id": 100 + index,
                "direction": "Основа",
                "shifts": {date_str: [{"start": "00:00", "end": "23:59", "breaks": [dict(item)]}]},
            }
            for index, item in enumerate(self.CROWDED_COLLEAGUES)
        ]
        seg_start, seg_end = self.CROWDED_SHIFT
        server_dummy, server_result = self._crowded_layout()
        target = {
            "id": 1,
            "direction": "Основа",
            "shifts": {date_str: [{
                "start": "10:00",
                "end": "21:00",
                # Норму берём ту же, что у сервера: сверяем раскладку, а не выбор длительностей.
                "breaks": server_dummy._place_break_durations_centered_minutes(
                    seg_start, seg_end, self.CROWDED_DURATIONS
                ),
            }]},
        }
        namespace["_ws_adjust_breaks_for_operator_on_date"](
            op=target,
            date_str=date_str,
            all_operators=[target] + colleagues,
            get_direction_scope_key=lambda value: f"dir:{str(value or '').strip().lower()}",
            break_rules_map=None,
        )

        self.assertEqual(target["shifts"][date_str][0]["breaks"], server_result)

    def test_frontend_mirrors_overlap_and_concurrency_rules(self):
        app_source = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
        for marker in (
            "BREAK_MAX_CONCURRENT",
            "BREAK_HANDOVER_OVERLAP_MIN",
            "breakAllowedPairOverlapMinutes",
            "breakPairOverlapExcessMinutes",
            "breakConcurrencyPenaltyMinutes",
            "breakCrossGapDeficitMinutes",
        ):
            self.assertIn(marker, app_source, marker)
        # Значения правил обязаны совпадать с серверными.
        self.assertIn(f"const BREAK_MAX_CONCURRENT = {BREAK_RULE_CONSTANTS['SHIFT_BREAK_MAX_CONCURRENT']};", app_source)
        self.assertIn(
            f"const BREAK_HANDOVER_OVERLAP_MIN = {BREAK_RULE_CONSTANTS['SHIFT_BREAK_HANDOVER_OVERLAP_MINUTES']};",
            app_source,
        )
        self.assertIn(
            f"const BREAK_PAIR_OVERLAP_BASE_MIN = {BREAK_RULE_CONSTANTS['SHIFT_BREAK_PAIR_OVERLAP_BASE_MINUTES']};",
            app_source,
        )
        self.assertIn(
            f"const BREAK_PAIR_OVERLAP_SHARE = {BREAK_RULE_CONSTANTS['SHIFT_BREAK_PAIR_OVERLAP_SHARE']};",
            app_source,
        )
        for js_name, py_name in (
            ("BREAK_WEIGHT_CONCURRENCY", "SHIFT_BREAK_WEIGHT_CONCURRENCY"),
            ("BREAK_WEIGHT_PAIR_EXCESS", "SHIFT_BREAK_WEIGHT_PAIR_EXCESS"),
            ("BREAK_WEIGHT_CROSS_GAP", "SHIFT_BREAK_WEIGHT_CROSS_GAP"),
            ("BREAK_WEIGHT_OVERLAP", "SHIFT_BREAK_WEIGHT_OVERLAP"),
        ):
            self.assertIn(f"const {js_name} = {BREAK_RULE_CONSTANTS[py_name]};", app_source, js_name)

    def test_import_simulation_tags_other_operators_breaks_with_direction_gap(self):
        namespace = {}
        for function_name in (
            "_operator_info_is_chat_manager",
            "_ws_normalize_direction_key",
            "_ws_cross_operator_gap_for_direction",
            "_ws_add_days_str",
            "_ws_parse_date_str",
            "_ws_build_occupied_intervals_for_date",
        ):
            exec(_function_source(BOT_PATH, function_name), namespace)
        namespace["datetime"] = __import__("datetime").datetime
        namespace["timedelta"] = __import__("datetime").timedelta
        build = namespace["_ws_build_occupied_intervals_for_date"]

        operators = [
            {
                "id": 1,
                "direction": "Чат менеджер",
                "shifts": {"2026-08-04": [{"breaks": [{"start": 600, "end": 630}]}]},
            },
            {
                "id": 2,
                "direction": "Чат менеджер",
                "shifts": {"2026-08-04": [{"breaks": [{"start": 700, "end": 730}]}]},
            },
        ]

        def scope_key(direction_value):
            return f"dir:{str(direction_value or '').strip().lower()}"

        without_gap = build(
            all_operators=operators,
            date_str="2026-08-04",
            exclude_op_id=1,
            direction_scope="Чат менеджер",
            get_scope_key=scope_key,
        )
        self.assertEqual(without_gap, [{"start": 700, "end": 730, "gap": 0}])

        with_gap = build(
            all_operators=operators,
            date_str="2026-08-04",
            exclude_op_id=1,
            direction_scope="Чат менеджер",
            get_scope_key=scope_key,
            break_gaps_map={"чат менеджер": 10},
        )
        self.assertEqual(with_gap, [{"start": 700, "end": 730, "gap": 10}])

    def test_import_simulation_uses_strictest_gap_inside_direction_group(self):
        namespace = {}
        for function_name in (
            "_operator_info_is_chat_manager",
            "_ws_normalize_direction_key",
            "_ws_cross_operator_gap_for_direction",
            "_ws_add_days_str",
            "_ws_parse_date_str",
            "_ws_build_occupied_intervals_for_date",
        ):
            exec(_function_source(BOT_PATH, function_name), namespace)
        namespace["datetime"] = __import__("datetime").datetime
        namespace["timedelta"] = __import__("datetime").timedelta
        build = namespace["_ws_build_occupied_intervals_for_date"]

        operators = [
            {"id": 1, "direction": "Основа", "shifts": {}},
            {
                "id": 2,
                "direction": "Чат менеджер",
                "shifts": {"2026-08-04": [{"breaks": [{"start": 700, "end": 730}]}]},
            },
        ]

        # Оба направления в одной группе перерывов.
        def scope_key(_direction_value):
            return "group:основа|чат менеджер"

        occupied = build(
            all_operators=operators,
            date_str="2026-08-04",
            exclude_op_id=1,
            direction_scope="Основа",
            get_scope_key=scope_key,
            break_gaps_map={"чат менеджер": 15},
        )
        self.assertEqual(occupied, [{"start": 700, "end": 730, "gap": 15}])

    def test_frontend_break_simulation_receives_cross_operator_gap(self):
        app_source = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")

        self.assertIn("getPlannerBreakCrossGapForDirection", app_source)
        self.assertIn("crossOperatorGapMinutes", app_source)
        # Все три места локальной симуляции перерывов должны знать про промежуток.
        # Все места локальной симуляции перерывов должны знать про промежуток:
        # часть вызовов многострочная (туда добавлены замороженные перерывы).
        self.assertEqual(
            app_source.count("getPlannerBreakRuleRangesForDirection, getPlannerBreakCrossGapForDirection)")
            + app_source.count("getPlannerBreakCrossGapForDirection,\n"),
            3,
        )

    def test_auction_publish_merges_touching_claimed_shifts_before_saving(self):
        namespace = {}
        exec(_function_source(DATABASE_PATH, "_minutes_to_time"), namespace)
        exec(
            _function_source(
                DATABASE_PATH,
                "_merge_shift_auction_claimed_shifts_for_publish",
                class_name="Database"
            ),
            namespace
        )
        merge = namespace["_merge_shift_auction_claimed_shifts_for_publish"]

        result = merge(_MergeDummy(), [
            {"start_time": "15:00", "end_time": "19:00"},
            {"start_time": "19:00", "end_time": "23:00"},
            {"start_time": "08:00", "end_time": "12:00"},
            {"start_time": "13:00", "end_time": "14:00"},
        ])

        self.assertEqual(result, [
            {"start_time": "08:00", "end_time": "12:00"},
            {"start_time": "13:00", "end_time": "14:00"},
            {"start_time": "15:00", "end_time": "23:00"},
        ])

    def test_post_auction_claim_merges_full_touching_shift_chain(self):
        namespace = {}
        exec(
            _function_source(
                DATABASE_PATH,
                "_resolve_post_auction_merged_shift_range",
                class_name="Database"
            ),
            namespace
        )
        resolve = namespace["_resolve_post_auction_merged_shift_range"]

        start_min, end_min, merge_ids = resolve(_MergeDummy(), [
            (1, "11:00", "15:00"),
            (2, "15:00", "19:00"),
            (3, "23:30", "01:00"),
        ], 19 * 60, 23 * 60)

        self.assertEqual((start_min, end_min), (11 * 60, 23 * 60))
        self.assertEqual(merge_ids, [2, 1])

    def test_post_auction_merge_preserves_overnight_tail_when_saving(self):
        namespace = {}
        exec(_function_source(DATABASE_PATH, "_minutes_to_time"), namespace)
        minutes_to_time = namespace["_minutes_to_time"]

        self.assertEqual(minutes_to_time(32 * 60), "08:00")
        for method_name in ("post_auction_claim_lot", "post_auction_claim_saved_shift"):
            source = _function_source(DATABASE_PATH, method_name, class_name="Database")
            self.assertIn("_minutes_to_time(merged_end_min)", source)
            self.assertNotIn("if merged_end_min < 24 * 60 else '00:00'", source)

    def test_post_auction_claim_rejects_overlap_with_existing_shift(self):
        namespace = {}
        exec(
            _function_source(
                DATABASE_PATH,
                "_resolve_post_auction_merged_shift_range",
                class_name="Database"
            ),
            namespace
        )
        resolve = namespace["_resolve_post_auction_merged_shift_range"]

        with self.assertRaisesRegex(ValueError, "SHIFT_OVERLAPS_EXISTING"):
            resolve(_MergeDummy(), [(1, "18:30", "21:00")], 19 * 60, 23 * 60)


class BreakFreezePastTests(unittest.TestCase):
    """Прошедшие перерывы не переставляются и не удаляются автоматикой."""

    OSNOVA_RULES = None  # используем дефолтные правила: 8-11 ч → [15, 30, 15]

    def _dummy(self, occupied=None):
        return _make_break_adjust_dummy(occupied=occupied)

    def _plan(self, dummy, seg_start, seg_end, durations, day_breaks, now, shift_date="2026-08-06",
              occupied_extra=None):
        """Повторяет серверный сценарий: граница → сплит → норма минус отсиженное → раскладка."""
        boundary = dummy._break_freeze_boundary_minutes(shift_date, seg_start=seg_start, now=now)
        planning_from = boundary + 15
        frozen, upcoming, used = dummy._split_breaks_by_freeze_boundary(
            day_breaks, planning_from, seg_start=seg_start, seg_end=seg_end
        )
        nothing_to_protect = not frozen and not used and not upcoming
        if (planning_from <= seg_start and not used) or (nothing_to_protect and planning_from >= seg_end):
            window_start = seg_start
            planned = dummy._place_break_durations_centered_minutes(seg_start, seg_end, durations)
        else:
            window_start = min(max(seg_start, planning_from), seg_end)
            planned = dummy._place_break_durations_centered_minutes(
                window_start,
                seg_end,
                dummy._fit_break_durations_to_window(
                    window_start,
                    seg_end,
                    dummy._remaining_break_durations_after_used(durations, used),
                ),
            )
        planned = dummy._seed_break_positions_from_existing(planned, upcoming)
        return dummy._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date=shift_date,
            start_time="%02d:%02d" % (seg_start // 60, seg_start % 60),
            end_time="%02d:%02d" % ((seg_end // 60) % 24, seg_end % 60),
            breaks=planned,
            cross_gap_minutes=0,
            frozen_breaks=frozen,
            planning_from_minutes=window_start,
            extra_occupied=occupied_extra,
        )

    def test_extending_shift_midway_keeps_used_break_and_adds_only_missing(self):
        # Смена 12:00-18:30, перерыв 13:00-13:15 уже отсижен, в 13:30 продлили до 21:00.
        dummy = self._dummy()
        result = self._plan(
            dummy,
            seg_start=12 * 60,
            seg_end=21 * 60,
            durations=[15, 30, 15],
            day_breaks=[{"start": 13 * 60, "end": 13 * 60 + 15}],
            now=datetime(2026, 8, 6, 13, 30),
        )
        self.assertIn({"start": 13 * 60, "end": 13 * 60 + 15}, result)
        self.assertEqual(sum(b["end"] - b["start"] for b in result), 60)
        for item in result[1:]:
            self.assertGreaterEqual(item["start"], 13 * 60 + 45)

    def test_no_break_is_planned_in_already_passed_time(self):
        # Перерывов ещё не было, «сейчас» 12:20 — ни один перерыв не встаёт раньше 12:35.
        dummy = self._dummy()
        result = self._plan(
            dummy,
            seg_start=12 * 60,
            seg_end=21 * 60,
            durations=[15, 30, 15],
            day_breaks=[],
            now=datetime(2026, 8, 6, 12, 20),
        )
        self.assertTrue(result)
        for item in result:
            self.assertGreaterEqual(item["start"], 12 * 60 + 35)

    def test_break_in_progress_is_not_moved(self):
        # Перерыв 13:05-13:20 идёт прямо сейчас (13:10) — остаётся на месте.
        dummy = self._dummy()
        result = self._plan(
            dummy,
            seg_start=12 * 60,
            seg_end=21 * 60,
            durations=[15, 30, 15],
            day_breaks=[{"start": 13 * 60 + 5, "end": 13 * 60 + 20}],
            now=datetime(2026, 8, 6, 13, 10),
        )
        self.assertEqual(result[0], {"start": 13 * 60 + 5, "end": 13 * 60 + 20})

    def test_shortened_shift_keeps_clipped_past_break(self):
        # Смену подрезали до 16:45, перерыв 16:30-17:00 уже начался — остаётся обрезанным.
        dummy = self._dummy()
        result = self._plan(
            dummy,
            seg_start=12 * 60,
            seg_end=16 * 60 + 45,
            durations=[15, 15],
            day_breaks=[{"start": 16 * 60 + 30, "end": 17 * 60}],
            now=datetime(2026, 8, 6, 16, 40),
        )
        self.assertEqual(result, [{"start": 16 * 60 + 30, "end": 16 * 60 + 45}])

    def test_used_break_outside_new_interval_is_not_reissued(self):
        # Начало смены сдвинули на 14:00; отсиженный в 13:00 перерыв хранить негде,
        # но и полный набор заново выдавать нельзя.
        dummy = self._dummy()
        frozen, upcoming, used = dummy._split_breaks_by_freeze_boundary(
            [{"start": 13 * 60, "end": 13 * 60 + 15}],
            15 * 60,
            seg_start=14 * 60,
            seg_end=18 * 60 + 30,
        )
        self.assertEqual(frozen, [])
        self.assertEqual(upcoming, [])
        self.assertEqual(used, [])

    def test_remaining_norm_counts_minutes_not_slots(self):
        dummy = self._dummy()
        # Отсидел обед — остаются два коротких, а не второй обед.
        self.assertEqual(
            dummy._remaining_break_durations_after_used([15, 30, 15], [{"start": 0, "end": 30}]),
            [15, 15],
        )
        # Отсидел короткий — остаются обед и второй короткий.
        self.assertEqual(
            dummy._remaining_break_durations_after_used([15, 30, 15], [{"start": 0, "end": 15}]),
            [30, 15],
        )
        self.assertEqual(
            dummy._remaining_break_durations_after_used(
                [15, 30, 15],
                [{"start": 0, "end": 30}, {"start": 100, "end": 130}],
            ),
            [],
        )
        self.assertEqual(dummy._remaining_break_durations_after_used([15, 30, 15], []), [15, 30, 15])

    def test_narrow_remaining_window_gets_no_break_instead_of_cramming(self):
        # Смену продлили в 20:00 до 21:00 — в остаток перерыв не влезает.
        dummy = self._dummy()
        result = self._plan(
            dummy,
            seg_start=12 * 60,
            seg_end=21 * 60,
            durations=[15, 30, 15],
            day_breaks=[],
            now=datetime(2026, 8, 6, 20, 0),
        )
        self.assertEqual(result, [])

    def test_night_claim_on_lot_date_is_not_treated_as_past(self):
        # Ночной кусок 00:00-08:00 хранится на дате лота: граница обязана выравниваться,
        # иначе смена целиком считается прошедшей и остаётся без перерывов.
        dummy = self._dummy()
        result = self._plan(
            dummy,
            seg_start=0,
            seg_end=8 * 60,
            durations=[15, 30, 15],
            day_breaks=[],
            now=datetime(2026, 8, 5, 21, 0),
            shift_date="2026-08-05",
        )
        self.assertEqual(len(result), 3)

    def test_future_date_layout_is_bit_identical_to_legacy(self):
        # Для будущих дат заморозка обязана схлопываться в прежнее поведение.
        cases = [
            (12 * 60, 21 * 60, [15, 30, 15]),
            (9 * 60, 15 * 60, [15, 15]),
            (8 * 60, 20 * 60, [15, 30, 15, 15]),
            (10 * 60, 15 * 60 + 30, [15]),
            (22 * 60, 30 * 60, [15, 30, 15]),
        ]
        colleague_sets = [
            [],
            [{"start": 16 * 60 + 15, "end": 16 * 60 + 45}],
            [{"start": 13 * 60, "end": 13 * 60 + 15}, {"start": 18 * 60, "end": 18 * 60 + 30}],
        ]
        for seg_start, seg_end, durations in cases:
            for occupied in colleague_sets:
                for cross_gap in (0, 15, 45):
                    legacy_dummy = self._dummy(occupied=[dict(x) for x in occupied])
                    planned = legacy_dummy._place_break_durations_centered_minutes(
                        seg_start, seg_end, durations
                    )
                    legacy = legacy_dummy._adjust_shift_breaks_against_occupied_tx(
                        cursor=None,
                        operator_id=1,
                        shift_date="2026-08-20",
                        start_time="%02d:%02d" % (seg_start // 60, seg_start % 60),
                        end_time="%02d:%02d" % ((seg_end // 60) % 24, seg_end % 60),
                        breaks=[dict(x) for x in planned],
                        cross_gap_minutes=cross_gap,
                    )
                    frozen_dummy = self._dummy(occupied=[dict(x) for x in occupied])
                    with_freeze = frozen_dummy._adjust_shift_breaks_against_occupied_tx(
                        cursor=None,
                        operator_id=1,
                        shift_date="2026-08-20",
                        start_time="%02d:%02d" % (seg_start // 60, seg_start % 60),
                        end_time="%02d:%02d" % ((seg_end // 60) % 24, seg_end % 60),
                        breaks=[dict(x) for x in planned],
                        cross_gap_minutes=cross_gap,
                        frozen_breaks=[],
                        planning_from_minutes=seg_start,
                    )
                    self.assertEqual(legacy, with_freeze, f"{seg_start}-{seg_end} gap={cross_gap}")

    def test_split_tolerates_broken_input(self):
        dummy = self._dummy()
        frozen, upcoming, used = dummy._split_breaks_by_freeze_boundary(
            [None, "мусор", {"start": 10, "end": 5}, {"start": "x", "end": 3}, {"start": 600, "end": 615}],
            700,
            seg_start=0,
            seg_end=1440,
        )
        self.assertEqual(frozen, [{"start": 600, "end": 615}])
        self.assertEqual(upcoming, [])
        self.assertEqual(used, [{"start": 600, "end": 615}])

    def test_upcoming_break_keeps_its_place_when_shift_changes(self):
        # Перерыв, который уже стоит в графике и ещё не начался, не должен уезжать.
        dummy = self._dummy()
        seeded = dummy._seed_break_positions_from_existing(
            [{"start": 15 * 60, "end": 15 * 60 + 15}, {"start": 18 * 60, "end": 18 * 60 + 30}],
            [{"start": 16 * 60 + 20, "end": 16 * 60 + 35}],
        )
        self.assertIn({"start": 16 * 60 + 20, "end": 16 * 60 + 35}, seeded)
        self.assertIn({"start": 18 * 60, "end": 18 * 60 + 30}, seeded)


class BreakFreezeWiringTests(unittest.TestCase):
    """Правило заморозки продублировано во всех трёх реализациях алгоритма."""

    def test_save_shift_takes_day_snapshot_before_deleting(self):
        source = _function_source(DATABASE_PATH, "_save_shift_tx", class_name="Database")
        self.assertIn("_load_day_shift_breaks_tx", source)
        self.assertIn("_split_breaks_by_freeze_boundary", source)
        self.assertIn("planning_from_minutes=planning_window_start", source)
        snapshot_pos = source.index("day_breaks_snapshot = self._load_day_shift_breaks_tx")
        delete_pos = source.index("DELETE FROM work_shifts")
        self.assertLess(snapshot_pos, delete_pos, "снимок обязан сниматься до удаления смен")

    def test_paths_that_clear_the_day_pass_snapshot(self):
        for method_name in (
            "respond_shift_swap_request",
            "publish_shift_auction_test_to_work_schedules",
            "apply_work_schedule_bulk_actions",
            "import_work_schedule_excel_entries",
            "post_auction_claim_lot",
            "post_auction_claim_saved_shift",
            "_split_post_auction_shift_slice_tx",
        ):
            source = _function_source(DATABASE_PATH, method_name, class_name="Database")
            self.assertIn("_load_day_shift_breaks_tx", source, method_name)
            self.assertIn("day_breaks_snapshot", source, method_name)

    def test_recalculate_never_touches_past_days(self):
        source = _function_source(DATABASE_PATH, "recalculate_work_schedule_breaks", class_name="Database")
        self.assertIn("start_date_obj = today_obj", source)
        self.assertIn("skipped_past_days", source)
        clamp_pos = source.index("start_date_obj = today_obj")
        delete_pos = source.index("DELETE FROM shift_breaks")
        self.assertLess(clamp_pos, delete_pos, "прошлое отсекается до массового удаления")

    def test_import_simulation_mirrors_freeze_rules(self):
        source = _function_source(BOT_PATH, "_ws_compute_breaks_for_entries")
        self.assertIn("_ws_break_freeze_boundary_minutes", source)
        self.assertIn("_ws_remaining_break_durations_after_used", source)
        self.assertIn("frozen_breaks_by_index", source)
        adjust_source = _function_source(BOT_PATH, "_ws_adjust_breaks_for_operator_on_date")
        self.assertIn("planning_from_minutes", adjust_source)
        self.assertIn("protected", adjust_source)

    def test_frontend_mirrors_freeze_rules(self):
        app_source = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("breakFreezeBoundaryMinutes", app_source)
        self.assertIn("planBreaksForShiftWithFrozen", app_source)
        self.assertIn("frozenBreaksBySegIndex", app_source)
        self.assertIn("Asia/Almaty", app_source)
        # прошедший перерыв в редакторе не перетаскивается
        self.assertIn("перерыв уже прошёл, изменить нельзя", app_source)
        self.assertIn("modalFrozenBreakIndexes", app_source)


if __name__ == "__main__":
    unittest.main()
