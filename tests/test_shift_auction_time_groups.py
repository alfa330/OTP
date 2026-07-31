"""Week time groups of the shift auction ("группы времени").

Replaces the retired "избранная группа": a group belongs to one weekly plan and
may open the auction for its members earlier *or* later than the main window.
database.py is not importable on Windows (time.tzset), so the pure helpers are
exec'ed standalone and the wiring is asserted against the parsed source.
"""

import ast
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_SOURCE = (ROOT / "database.py").read_text(encoding="utf-8-sig")
ROUTES_SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
FRONTEND_SOURCE = (
    ROOT / "src" / "components" / "resources" / "ShiftAuctionView.jsx"
).read_text(encoding="utf-8-sig")
DATABASE_MODULE = ast.parse(DATABASE_SOURCE)


def _database_class():
    return next(
        node for node in DATABASE_MODULE.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )


def _method(name):
    return next(
        node for node in _database_class().body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _method_source(name):
    return ast.get_source_segment(DATABASE_SOURCE, _method(name))


def _build_window_sandbox():
    """Exec the pure window helpers onto a stand-in object."""
    class Holder:
        pass

    for method_name in ("_shift_auction_effective_window", "_shift_auction_run_bounds_tx"):
        namespace = {}
        exec(_method_source(method_name), namespace)
        setattr(Holder, method_name, namespace[method_name])

    namespace = {"datetime": datetime}
    exec(_method_source("_shift_auction_group_window").replace("@staticmethod\n", ""), namespace)
    Holder._shift_auction_group_window = staticmethod(namespace["_shift_auction_group_window"])
    return Holder()


class _StubCursor:
    """Minimal cursor: returns the queued group windows for any query."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return self._rows


MAIN_START = datetime(2026, 8, 3, 10, 0)
MAIN_END = datetime(2026, 8, 3, 12, 0)


class ShiftAuctionEffectiveWindowTests(unittest.TestCase):
    def setUp(self):
        self.db = _build_window_sandbox()

    def test_operator_without_group_keeps_the_main_window(self):
        self.assertEqual(
            self.db._shift_auction_effective_window(MAIN_START, MAIN_END),
            (MAIN_START, MAIN_END),
        )

    def test_group_may_open_earlier(self):
        early = datetime(2026, 8, 3, 9, 0)
        self.assertEqual(
            self.db._shift_auction_effective_window(MAIN_START, MAIN_END, early, None),
            (early, MAIN_END),
        )

    def test_group_may_open_later_and_is_not_clamped_to_the_main_start(self):
        # The retired favored group took min(main, favored); a later group must
        # keep its own start, otherwise "позже" would be impossible.
        late = datetime(2026, 8, 3, 14, 0)
        late_end = datetime(2026, 8, 3, 16, 0)
        self.assertEqual(
            self.db._shift_auction_effective_window(MAIN_START, MAIN_END, late, late_end),
            (late, late_end),
        )

    def test_group_without_its_own_end_inherits_the_main_end(self):
        early = datetime(2026, 8, 3, 9, 0)
        _, ends_at = self.db._shift_auction_effective_window(MAIN_START, MAIN_END, early, None)
        self.assertEqual(ends_at, MAIN_END)

    def test_group_window_is_parsed_back_from_a_serialized_group(self):
        starts_at, ends_at = self.db._shift_auction_group_window({
            "starts_at": "2026-08-03T09:00:00",
            "ends_at": None,
        })
        self.assertEqual(starts_at, datetime(2026, 8, 3, 9, 0))
        self.assertIsNone(ends_at)

    def test_group_window_survives_broken_values(self):
        self.assertEqual(
            self.db._shift_auction_group_window({"starts_at": "not-a-date", "ends_at": ""}),
            (None, None),
        )
        self.assertEqual(self.db._shift_auction_group_window(None), (None, None))


class ShiftAuctionRunBoundsTests(unittest.TestCase):
    """Manager controls act on the run as a whole: earliest start → latest end."""

    def setUp(self):
        self.db = _build_window_sandbox()

    def test_no_groups_keeps_the_main_bounds(self):
        self.assertEqual(
            self.db._shift_auction_run_bounds_tx(_StubCursor([]), 7, MAIN_START, MAIN_END),
            (MAIN_START, MAIN_END),
        )

    def test_without_a_week_groups_are_not_consulted(self):
        cursor = _StubCursor([(datetime(2026, 8, 3, 6, 0), None)])
        self.assertEqual(
            self.db._shift_auction_run_bounds_tx(cursor, None, MAIN_START, MAIN_END),
            (MAIN_START, MAIN_END),
        )

    def test_earlier_and_later_groups_widen_the_run(self):
        cursor = _StubCursor([
            (datetime(2026, 8, 3, 9, 0), None),
            (datetime(2026, 8, 3, 14, 0), datetime(2026, 8, 3, 16, 0)),
        ])
        self.assertEqual(
            self.db._shift_auction_run_bounds_tx(cursor, 7, MAIN_START, MAIN_END),
            (datetime(2026, 8, 3, 9, 0), datetime(2026, 8, 3, 16, 0)),
        )

    def test_missing_edges_widen_instead_of_narrowing(self):
        cursor = _StubCursor([(datetime(2026, 8, 3, 9, 0), None)])
        self.assertEqual(
            self.db._shift_auction_run_bounds_tx(cursor, 7, None, None),
            (None, None),
        )


class ShiftAuctionTimeGroupNormalizationTests(unittest.TestCase):
    def setUp(self):
        namespace = {}
        exec(_method_source("_normalize_shift_auction_time_groups"), namespace)

        class Holder:
            SHIFT_AUCTION_TIME_GROUP_LIMIT = 10
            _normalize_shift_auction_time_groups = namespace["_normalize_shift_auction_time_groups"]

        self.db = Holder()

    def _normalize(self, groups, ends_at=MAIN_END):
        return self.db._normalize_shift_auction_time_groups(groups, MAIN_START, ends_at)

    def test_absent_field_leaves_groups_untouched(self):
        self.assertIsNone(self._normalize(None))

    def test_empty_list_is_a_clear_request(self):
        self.assertEqual(self._normalize([]), [])

    def test_group_without_a_start_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._normalize([{"name": "Наставники", "operator_ids": [1]}])
        self.assertEqual(str(ctx.exception), "AUCTION_GROUP_START_REQUIRED")

    def test_group_starting_after_the_inherited_end_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._normalize([{"starts_at": datetime(2026, 8, 3, 14, 0)}])
        self.assertEqual(str(ctx.exception), "AUCTION_GROUP_WINDOW_EMPTY")

    def test_late_group_with_its_own_end_is_accepted(self):
        groups = self._normalize([{
            "name": "  Новички  ",
            "starts_at": datetime(2026, 8, 3, 14, 0),
            "ends_at": datetime(2026, 8, 3, 16, 0),
            "operator_ids": [3, "4", 3, 0, "x"],
        }])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["name"], "Новички")
        self.assertEqual(groups[0]["operator_ids"], [3, 4])

    def test_group_may_sit_on_another_day_of_the_week(self):
        groups = self._normalize([{
            "starts_at": datetime(2026, 8, 5, 9, 0),
            "ends_at": datetime(2026, 8, 5, 11, 0),
        }])
        self.assertEqual(groups[0]["starts_at"], datetime(2026, 8, 5, 9, 0))
        self.assertEqual(groups[0]["ends_at"], datetime(2026, 8, 5, 11, 0))

    def test_self_schedule_flag_round_trips(self):
        groups = self._normalize([
            {"starts_at": datetime(2026, 8, 3, 9, 0), "self_schedule_enabled": True},
            {"starts_at": datetime(2026, 8, 3, 9, 30)},
        ])
        self.assertTrue(groups[0]["self_schedule_enabled"])
        self.assertFalse(groups[1]["self_schedule_enabled"])

    def test_group_end_before_its_own_start_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._normalize([{
                "starts_at": datetime(2026, 8, 3, 11, 0),
                "ends_at": datetime(2026, 8, 3, 10, 30),
            }])
        self.assertEqual(str(ctx.exception), "AUCTION_GROUP_WINDOW_EMPTY")

    def test_unnamed_group_gets_a_positional_name(self):
        groups = self._normalize([
            {"starts_at": datetime(2026, 8, 3, 9, 0)},
            {"starts_at": datetime(2026, 8, 3, 9, 30), "name": ""},
        ])
        self.assertEqual([group["name"] for group in groups], ["Группа 1", "Группа 2"])

    def test_too_many_groups_are_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._normalize([{"starts_at": MAIN_START - timedelta(hours=1)}] * 11)
        self.assertEqual(str(ctx.exception), "AUCTION_GROUP_LIMIT")

    def test_non_list_payload_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._normalize({"starts_at": MAIN_START})
        self.assertEqual(str(ctx.exception), "AUCTION_GROUP_INVALID")


class ShiftAuctionTimeGroupSchemaTests(unittest.TestCase):
    def test_tables_and_indexes_are_created_idempotently(self):
        for statement in (
            "CREATE TABLE IF NOT EXISTS shift_auction_time_groups",
            "CREATE TABLE IF NOT EXISTS shift_auction_time_group_members",
            "CREATE INDEX IF NOT EXISTS idx_shift_auction_time_groups_plan",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_shift_auction_time_group_member_week",
            "CREATE INDEX IF NOT EXISTS idx_shift_auction_time_group_members_operator",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, DATABASE_SOURCE)

    def test_a_group_is_bound_to_one_weekly_plan(self):
        self.assertIn(
            "plan_id INTEGER NOT NULL REFERENCES resource_saved_schedule_plans(id) ON DELETE CASCADE",
            DATABASE_SOURCE,
        )

    def test_one_operator_belongs_to_one_group_per_week(self):
        self.assertIn(
            "ON shift_auction_time_group_members(plan_id, operator_id);",
            DATABASE_SOURCE,
        )

    def test_retired_favored_config_is_migrated_once(self):
        self.assertIn("INSERT INTO shift_auction_time_groups (plan_id, name, starts_at, created_by)", DATABASE_SOURCE)
        self.assertIn("AND NOT EXISTS (SELECT 1 FROM shift_auction_time_groups)", DATABASE_SOURCE)


class ShiftAuctionTimeGroupPersistenceTests(unittest.TestCase):
    def test_groups_are_saved_against_the_week_being_edited(self):
        source = _method_source("update_shift_auction_test_access")

        self.assertIn("_save_shift_auction_time_groups_tx(", source)
        self.assertIn("next_selected_plan_id,", source)
        self.assertIn("AUCTION_GROUP_WEEK_REQUIRED", source)

    def test_other_weeks_are_never_touched(self):
        source = _method_source("_save_shift_auction_time_groups_tx")

        for statement in (
            "DELETE FROM shift_auction_time_groups WHERE plan_id = %s",
            "DELETE FROM shift_auction_time_group_members WHERE plan_id = %s",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, source)
        self.assertIn("WHERE plan_id = %s AND NOT (id = ANY(%s))", source)

    def test_membership_is_validated_against_the_direction(self):
        source = _method_source("_save_shift_auction_time_groups_tx")

        self.assertIn("if operator_id in valid_operator_ids and operator_id not in assigned_members:", source)

    def test_group_takes_any_operator_of_the_direction(self):
        source = _method_source("update_shift_auction_test_access")

        # Group members ride along into the same direction/status validation as the
        # ticked participants, so they may be anyone of the direction — and joining
        # a group makes them a participant.
        self.assertIn('for member_id in group["operator_ids"]:', source)
        self.assertIn("normalized_ids.append(member_id)", source)
        self.assertIn("COALESCE(u.status, '') NOT IN ('fired', 'dismissal')", source)
        self.assertIn("BTRIM(COALESCE(d.name, '')) = %s", source)

    def test_favored_columns_are_no_longer_read_or_written(self):
        for method_name in (
            "update_shift_auction_test_access",
            "get_shift_auction_test_access",
            "get_shift_auction_test_snapshot",
            "_build_shift_auction_snapshot_common_tx",
            "claim_shift_auction_test_lot",
            "release_shift_auction_test_lot",
            "control_shift_auction_test",
        ):
            with self.subTest(method_name=method_name):
                source = _method_source(method_name)
                self.assertNotIn("favored", source)
                self.assertNotIn("early_access", source)


class ShiftAuctionTimeGroupGatingTests(unittest.TestCase):
    def test_operator_actions_use_the_operator_window(self):
        for method_name in ("claim_shift_auction_test_lot", "release_shift_auction_test_lot"):
            with self.subTest(method_name=method_name):
                source = _method_source(method_name)
                self.assertIn("_shift_auction_effective_window(", source)
                self.assertIn("m.plan_id = s.selected_schedule_plan_id", source)
                self.assertIn("effective_start, effective_end", source)

    def test_day_offs_follow_the_operator_window(self):
        source = _method_source("set_shift_auction_test_day_off")

        self.assertIn("_get_shift_auction_operator_group_tx(", source)
        self.assertIn("day_off_starts_at, day_off_ends_at", source)

    def test_manager_controls_use_the_whole_run(self):
        for method_name in (
            "control_shift_auction_test",
            "set_shift_auction_test_topup",
            "publish_shift_auction_test_to_work_schedules",
        ):
            with self.subTest(method_name=method_name):
                self.assertIn("_shift_auction_run_bounds_tx(", _method_source(method_name))

    def test_resume_shifts_group_ends_by_the_pause(self):
        source = _method_source("control_shift_auction_test")

        self.assertIn("UPDATE shift_auction_time_groups", source)
        self.assertIn("SET ends_at = ends_at + %s", source)
        self.assertIn("AND ends_at IS NOT NULL", source)


class ShiftAuctionSelfScheduleTests(unittest.TestCase):
    """«Свой график»: members place shifts themselves, capped at norm + 10 hours."""

    def test_schema_carries_the_toggle_and_the_lot_marker(self):
        self.assertIn(
            "ALTER TABLE shift_auction_time_groups ADD COLUMN IF NOT EXISTS self_schedule_enabled BOOLEAN NOT NULL DEFAULT FALSE;",
            DATABASE_SOURCE,
        )
        self.assertIn(
            "ALTER TABLE shift_auction_test_lots ADD COLUMN IF NOT EXISTS self_scheduled_by INTEGER NULL",
            DATABASE_SOURCE,
        )

    def test_allowance_is_ten_hours(self):
        self.assertIn("SHIFT_AUCTION_SELF_SCHEDULE_EXTRA_MINUTES = 600", DATABASE_SOURCE)
        self.assertIn("const AUCTION_SELF_SCHEDULE_EXTRA_MINUTES = 600;", FRONTEND_SOURCE)

    def test_toggle_is_persisted_with_the_group(self):
        source = _method_source("_save_shift_auction_time_groups_tx")

        self.assertIn("self_schedule_enabled = %s", source)
        self.assertIn('group["self_schedule_enabled"]', source)

    def test_placing_a_shift_is_gated_on_the_group_toggle(self):
        source = _method_source("self_schedule_shift_auction_shift")

        self.assertIn("SELF_SCHEDULE_NOT_ALLOWED", source)
        self.assertIn("NOT_TEST_PARTICIPANT", source)
        self.assertIn("AUCTION_NOT_OPEN", source)
        self.assertIn("_shift_auction_effective_window(", source)

    def test_placing_a_shift_respects_the_ceiling_and_day_rules(self):
        source = _method_source("self_schedule_shift_auction_shift")

        self.assertIn("SHIFT_AUCTION_SELF_SCHEDULE_EXTRA_MINUTES", source)
        self.assertIn("SELF_SCHEDULE_LIMIT_EXCEEDED", source)
        for code in (
            "SHIFT_AUCTION_STATUS_PERIOD_BLOCKED",
            "DAY_OFF_SELECTED",
            "DAY_ALREADY_HAS_SHIFT",
            "SHIFT_ALREADY_STARTED",
            "DATE_OUT_OF_RANGE",
        ):
            with self.subTest(code=code):
                self.assertIn(code, source)

    def test_shift_length_follows_the_rate_bucket_but_the_norm_uses_the_real_rate(self):
        source = _method_source("self_schedule_shift_auction_shift")

        self.assertIn("rate_bucket = self._shift_auction_rate_bucket(operator_rate)", source)
        self.assertIn("SHIFT_AUCTION_RATE_SHIFT_MINUTES.get(rate_bucket)", source)
        self.assertIn("cursor, operator_id, operator_rate,", source)

    def test_shift_is_created_already_claimed_and_marked(self):
        source = _method_source("self_schedule_shift_auction_shift")

        self.assertIn("'claimed', %s, CURRENT_TIMESTAMP, %s", source)
        self.assertIn("self_scheduled_by", source)
        self.assertIn("'auction-self'", source)
        # A new row cannot be patched into client state — clients must resnapshot.
        self.assertIn('"shift_self_scheduled"', source)

    def test_giving_up_a_self_scheduled_shift_removes_it(self):
        release_source = _method_source("release_shift_auction_test_lot")
        remove_source = _method_source("_remove_self_scheduled_shift_tx")

        self.assertIn("_remove_self_scheduled_shift_tx(cursor, operator_id, lot)", release_source)
        self.assertIn("DELETE FROM shift_auction_test_lots WHERE id = %s", remove_source)
        self.assertIn("AND source = 'auction-self'", remove_source)
        self.assertIn('"self_scheduled_shift_removed"', remove_source)

    def test_ordinary_claims_of_such_operators_share_the_wider_ceiling(self):
        source = _method_source("claim_shift_auction_test_lot")

        self.assertIn("norm_allowance_minutes = workload[\"norm_minutes\"] + (", source)
        self.assertIn("self.SHIFT_AUCTION_SELF_SCHEDULE_EXTRA_MINUTES if self_schedule_enabled else 0", source)
        self.assertIn("> norm_allowance_minutes + 1", source)

    def test_lot_marker_reaches_the_grid(self):
        self.assertIn('"self_scheduled": bool(row[22]) if len(row) > 22 else False,', DATABASE_SOURCE)
        self.assertIn("l.self_scheduled_by", DATABASE_SOURCE)
        self.assertIn("const isSelfScheduledLot = Boolean(lot.self_scheduled);", FRONTEND_SOURCE)

    def test_progress_is_measured_against_the_wider_ceiling(self):
        source = _method_source("_get_shift_auction_participant_workloads_tx")

        self.assertIn("self_schedule_operator_ids", source)
        self.assertIn('"ceiling_minutes": ceiling_minutes,', source)
        self.assertIn('"over_minutes": max(0, int(claimed_net) - ceiling_minutes),', source)
        # The plain norm stays in the payload — payroll still needs it.
        self.assertIn('"norm_minutes": norm_minutes,', source)
        self.assertIn("workload.ceiling_minutes || workload.norm_minutes", FRONTEND_SOURCE)

    def test_route_is_operator_only(self):
        route = ROUTES_SOURCE.split("def api_shift_auction_self_schedule(")[1].split("@app.route")[0]

        self.assertIn("_normalize_user_role(requester[3]) != 'operator'", route)
        self.assertIn("db.self_schedule_shift_auction_shift(", route)
        for code in ("SELF_SCHEDULE_NOT_ALLOWED", "SELF_SCHEDULE_LIMIT_EXCEEDED"):
            with self.subTest(code=code):
                self.assertIn(f'"{code}": (', ROUTES_SOURCE)


class ShiftAuctionTimeGroupApiTests(unittest.TestCase):
    def test_route_forwards_parsed_groups(self):
        self.assertIn("time_groups=_parse_shift_auction_time_groups(payload.get('time_groups'))", ROUTES_SOURCE)
        self.assertNotIn("favored_starts_at=", ROUTES_SOURCE)
        self.assertNotIn("favored_operator_ids=", ROUTES_SOURCE)

    def test_absent_field_is_distinguished_from_an_empty_list(self):
        source = ROUTES_SOURCE.split("def _parse_shift_auction_time_groups(")[1]
        self.assertIn("if raw_groups is None:\n        return None", source)

    def test_group_errors_are_translated(self):
        for code in (
            "AUCTION_GROUP_INVALID",
            "AUCTION_GROUP_LIMIT",
            "AUCTION_GROUP_START_REQUIRED",
            "AUCTION_GROUP_WINDOW_EMPTY",
            "AUCTION_GROUP_WEEK_REQUIRED",
        ):
            with self.subTest(code=code):
                self.assertIn(f'"{code}": (', ROUTES_SOURCE)
        self.assertNotIn("AUCTION_FAVORED_START_AFTER_END", ROUTES_SOURCE)


class ShiftAuctionTimeGroupFrontendTests(unittest.TestCase):
    def test_favored_ui_is_gone(self):
        for token in ("favored", "early_access", "избранн"):
            with self.subTest(token=token):
                self.assertNotIn(token, FRONTEND_SOURCE)

    def test_viewer_status_uses_its_own_end(self):
        self.assertIn("my_effective_ends_at", FRONTEND_SOURCE)
        self.assertIn(
            "getAuctionRuntimeStatus(settings, Date.now(), operatorEffectiveStartsAt, operatorEffectiveEndsAt)",
            FRONTEND_SOURCE,
        )

    def test_draft_groups_are_scoped_to_the_picked_week(self):
        self.assertIn("normalizeSchedulePlanId(group?.plan_id) === planId", FRONTEND_SOURCE)

    def test_group_payload_is_only_sent_with_a_week(self):
        self.assertIn(
            "...(selectedDraftPeriod?.id\n            ? { time_groups: buildTimeGroupsPayload("
            "draftTimeGroupsForSave, draftStartsAt, draftEndsAt) }\n            : {})",
            FRONTEND_SOURCE,
        )

    def test_save_is_blocked_while_a_group_window_is_broken(self):
        self.assertIn("if (timeGroupIssues.size) {", FRONTEND_SOURCE)

    def test_group_day_is_picked_inside_the_auction_week(self):
        self.assertIn("const getWeekDatesForDate = (dateValue) => {", FRONTEND_SOURCE)
        self.assertIn("getWeekDatesForDate(draftStartsAtParts.date)", FRONTEND_SOURCE)
        self.assertIn("patchDraftTimeGroup(group.key, { date: weekDate })", FRONTEND_SOURCE)

    def test_own_end_before_the_start_rolls_to_the_next_day(self):
        self.assertIn(
            "sameDayEnd > windowStartsAt ? sameDayEnd : `${addDaysToDateInputValue(groupDate, 1)}T${group.endTime}`",
            FRONTEND_SOURCE,
        )

    def test_group_picker_offers_every_operator_of_the_direction(self):
        # The picker used to list only ticked participants; it now offers the same
        # roster as the participants list itself.
        self.assertIn("? operatorOptions.filter((operator) => (", FRONTEND_SOURCE)
        self.assertIn("const draftTimeGroupsForSave = draftTimeGroups;", FRONTEND_SOURCE)
        self.assertNotIn("Сначала выберите участников аукциона.", FRONTEND_SOURCE)

    def test_self_schedule_is_offered_only_to_its_group(self):
        self.assertIn(
            "const canSelfSchedule = canClaim && !canMonitor && Boolean(settings.my_time_group?.self_schedule_enabled);",
            FRONTEND_SOURCE,
        )
        self.assertIn("openSelfSchedule(item.date)", FRONTEND_SOURCE)
        self.assertIn("self_schedule_enabled: Boolean(group.selfSchedule)", FRONTEND_SOURCE)


if __name__ == "__main__":
    unittest.main()
