"""Static invariants and focused behavior tests for the groups migration."""
import ast
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import unittest
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DB = (ROOT / "database.py").read_text(encoding="utf-8-sig")
BOT = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
APP = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
GROUPS_VIEW = (ROOT / "src" / "components" / "groups" / "GroupsView.jsx").read_text(encoding="utf-8-sig")
USER_MODAL = (ROOT / "src" / "components" / "modals" / "UserEditModal.jsx").read_text(encoding="utf-8-sig")


def _membership_edit_database_class():
    """Load only the membership-boundary code, without importing live DB setup."""
    module = source_cache.parse(DB)
    database_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    wanted_methods = {
        "_assert_no_operator_hours_in_window_tx",
        "update_group_membership_start_date",
    }
    methods = [
        node for node in database_class.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted_methods
    ]
    non_empty_constant = next(
        node for node in database_class.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_DAILY_HOURS_NON_EMPTY"
                for target in node.targets)
    )
    test_class = ast.ClassDef(
        name="MembershipEditDatabase",
        bases=[],
        keywords=[],
        body=[non_empty_constant, *methods],
        decorator_list=[],
    )
    namespace = {
        "date": date,
        "datetime": datetime,
        "timedelta": timedelta,
    }
    test_module = ast.fix_missing_locations(ast.Module(body=[test_class], type_ignores=[]))
    exec(compile(test_module, str(ROOT / "database.py"), "exec"), namespace)
    return namespace["MembershipEditDatabase"]


MembershipEditDatabase = _membership_edit_database_class()


class _MembershipEditCursor:
    """Small stateful model for the SQL used by the membership edit method."""

    def __init__(self, current_start, previous=None, daily_rows=None):
        self.current = {
            "id": 10,
            "group_id": 100,
            "operator_id": 77,
            "start_date": current_start,
            "end_date": None,
        }
        self.previous = dict(previous) if previous else None
        self.daily_rows = [dict(row) for row in (daily_rows or [])]
        self.executions = []
        self._fetchone = None
        self._fetchall = []

    @staticmethod
    def _non_empty(row):
        return any(float(row.get(key) or 0) > 0 for key in (
            "work_time", "training_time", "talk_time", "break_time", "calls", "fine_amount"
        ))

    def _memberships(self):
        return [item for item in (self.previous, self.current) if item]

    def _covered_by_membership(self, day_value):
        return any(
            item["start_date"] <= day_value
            and (item.get("end_date") is None or item["end_date"] >= day_value)
            for item in self._memberships()
        )

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        params = tuple(params or ())
        self.executions.append((normalized, params))
        self._fetchone = None
        self._fetchall = []

        if normalized.startswith("SELECT id, start_date FROM group_operator_memberships"):
            group_id, operator_id = int(params[0]), int(params[1])
            if (
                self.current["group_id"] == group_id
                and self.current["operator_id"] == operator_id
                and self.current["end_date"] is None
            ):
                self._fetchone = (self.current["id"], self.current["start_date"])
            return

        if normalized.startswith("SELECT id, group_id, start_date, end_date FROM group_operator_memberships"):
            operator_id, membership_id, old_start = int(params[0]), int(params[1]), params[2]
            item = self.previous
            if (
                item
                and item["operator_id"] == operator_id
                and item["id"] != membership_id
                and item.get("end_date") is not None
                and item["end_date"] < old_start
            ):
                self._fetchone = (
                    item["id"], item["group_id"], item["start_date"], item["end_date"]
                )
            return

        if normalized.startswith("SELECT MIN(day), MAX(day), COUNT(*) FROM daily_hours"):
            operator_id, lo, hi = int(params[0]), params[1], params[2]
            rows = [
                row for row in self.daily_rows
                if int(row["operator_id"]) == operator_id
                and lo <= row["day"] <= hi
                and self._non_empty(row)
            ]
            # The production guard must distinguish an authoritative historical
            # membership from a genuinely ungrouped day. Accommodate either a
            # membership-aware predicate or a direct non-NULL group stamp check.
            if "group_operator_memberships" in normalized:
                rows = [row for row in rows if self._covered_by_membership(row["day"])]
            elif "group_id IS NOT NULL" in normalized:
                rows = [row for row in rows if row.get("group_id") is not None]
            self._fetchone = (
                min((row["day"] for row in rows), default=None),
                max((row["day"] for row in rows), default=None),
                len(rows),
            )
            return

        if normalized.startswith("UPDATE group_operator_memberships SET end_date ="):
            if not self.previous or int(params[1]) != self.previous["id"]:
                raise AssertionError("Unexpected previous membership update")
            self.previous["end_date"] = params[0]
            return

        if normalized.startswith("UPDATE group_operator_memberships SET start_date ="):
            if int(params[1]) != self.current["id"]:
                raise AssertionError("Unexpected current membership update")
            self.current["start_date"] = params[0]
            return

        if normalized.startswith("SELECT DISTINCT TO_CHAR(day, 'YYYY-MM') FROM daily_hours"):
            operator_id, lo, hi = int(params[0]), params[1], params[2]
            months = sorted({
                row["day"].strftime("%Y-%m")
                for row in self.daily_rows
                if int(row["operator_id"]) == operator_id and lo <= row["day"] <= hi
            })
            self._fetchall = [(month,) for month in months]
            return

        if normalized.startswith("UPDATE daily_hours SET group_id ="):
            group_id, operator_id, lo, hi = params
            for row in self.daily_rows:
                if int(row["operator_id"]) == int(operator_id) and lo <= row["day"] <= hi:
                    row["group_id"] = group_id
            return

        raise AssertionError(f"Unexpected SQL: {normalized}")

    def fetchone(self):
        return self._fetchone

    def fetchall(self):
        return list(self._fetchall)


def _membership_edit_database(cursor):
    database = MembershipEditDatabase()

    @contextmanager
    def _get_cursor():
        yield cursor

    database._get_cursor = _get_cursor
    database.aggregate_calls = []
    database._aggregate_month_from_daily_tx = (
        lambda _cursor, operator_id, month: database.aggregate_calls.append(
            (int(operator_id), month)
        )
    )
    return database


class SchemaTests(unittest.TestCase):
    def test_group_tables_created(self):
        for t in [
            "CREATE TABLE IF NOT EXISTS groups",
            "CREATE TABLE IF NOT EXISTS group_supervisor_memberships",
            "CREATE TABLE IF NOT EXISTS group_operator_memberships",
            "CREATE TABLE IF NOT EXISTS group_month_snapshots",
            "CREATE TABLE IF NOT EXISTS group_operator_month_snapshots",
        ]:
            self.assertIn(t, DB, f"missing DDL: {t}")

    def test_group_id_and_extra_metrics_columns(self):
        self.assertIn("ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES groups(id)", DB)
        self.assertIn("extra_metrics JSONB", DB)

    def test_group_owns_model_and_status(self):
        self.assertIn("calculation_model_code VARCHAR(32) NOT NULL DEFAULT 'operator'", DB)
        self.assertIn("status VARCHAR(16) NOT NULL DEFAULT 'active'", DB)


class RegistryTests(unittest.TestCase):
    def test_metric_registry_exists(self):
        self.assertIn("CALCULATION_MODEL_METRICS = {", DB)
        self.assertIn("def get_calculation_model_metrics(", DB)

    def test_registry_covers_both_models(self):
        self.assertIn("CALCULATION_MODEL_OPERATOR: _CALC_METRICS_HEAD", DB)
        self.assertIn("CALCULATION_MODEL_CHAT_MANAGER: _CALC_METRICS_HEAD", DB)

    def test_registry_exposed_via_api(self):
        self.assertIn("calculation_model_metrics", BOT)


class ResolverTests(unittest.TestCase):
    def test_resolver_period_aware(self):
        self.assertIn(
            "def _load_operator_calculation_models_tx(self, cursor, operator_ids, as_of=None)", DB
        )
        self.assertIn(
            "def _get_operator_calculation_model_tx(self, cursor, operator_id, as_of=None)", DB
        )
        self.assertIn("group_operator_memberships gom", DB)

    def test_monthly_callers_thread_as_of(self):
        self.assertIn("_get_operator_calculation_model_tx(cursor, operator_id, as_of=end)", DB)
        self.assertIn("_get_operator_calculation_model_tx(cursor, operator_id, as_of=_cm_end)", DB)

    def test_auto_aggregation_uses_historical_day_scope(self):
        self.assertIn(
            "_get_operator_calculation_model_tx(\n                    cursor, op_id, as_of=day_value",
            DB,
        )
        self.assertIn("_get_operator_group_id_tx(\n                    cursor, op_id, day_value", DB)


class UpsertSafetyTests(unittest.TestCase):
    def test_daily_conflict_unchanged_but_stamps_group(self):
        self.assertIn("ON CONFLICT (operator_id, day)", DB)
        self.assertIn("group_id = COALESCE(EXCLUDED.group_id, daily_hours.group_id)", DB)

    def test_work_hours_conflict_not_swapped_yet(self):
        self.assertIn("ON CONFLICT (operator_id, month)", DB)
        self.assertIn("group_id = COALESCE(EXCLUDED.group_id, work_hours.group_id)", DB)


class BackfillTests(unittest.TestCase):
    def test_backfill_exists_guarded_and_savepointed(self):
        self.assertIn("def _backfill_groups_from_supervisors_tx(self, cursor)", DB)
        self.assertIn("SELECT 1 FROM groups LIMIT 1", DB)
        self.assertIn("SAVEPOINT sp_groups_backfill", DB)

    def test_backfill_group_naming(self):
        self.assertIn('"{} группа {}".format(sv_name, dir_name)', DB)


class CrudTests(unittest.TestCase):
    def test_crud_methods_exist(self):
        for m in [
            "def list_groups(self,",
            "def get_group(self, group_id)",
            "def get_group_members(self, group_id)",
            "def create_group(self,",
            "def archive_group(self,",
            "def reuse_archived_group(self,",
            "def add_operator_to_group(self,",
            "def remove_operator_from_group(self,",
            "def add_supervisor_to_group(self,",
            "def remove_supervisor_from_group(self,",
        ]:
            self.assertIn(m, DB, f"missing method: {m}")

    def test_one_active_group_per_operator(self):
        self.assertIn("WHERE operator_id = %s AND end_date IS NULL AND group_id <> %s", DB)

    def test_endpoints_exist(self):
        for r in [
            "@app.route('/api/groups', methods=['GET'])",
            "@app.route('/api/admin/groups', methods=['POST'])",
            "@app.route('/api/admin/groups/<int:group_id>/archive'",
            "@app.route('/api/admin/groups/<int:group_id>/reuse'",
            "@app.route('/api/admin/groups/<int:group_id>/operators'",
            "@app.route('/api/admin/groups/<int:group_id>/supervisors'",
            "@app.route('/api/admin/groups/<int:group_id>/members'",
        ]:
            self.assertIn(r, BOT, f"missing route: {r}")


class MemberStartDateEditTests(unittest.TestCase):
    """Дата вступления участника правится прямо в разделе «Состав» группы."""

    def test_backdate_over_nonempty_ungrouped_day_is_allowed(self):
        cursor = _MembershipEditCursor(
            current_start=date(2026, 7, 10),
            daily_rows=[{
                "operator_id": 77,
                "day": date(2026, 7, 9),
                "group_id": None,
                "work_time": 8,
            }],
        )
        database = _membership_edit_database(cursor)

        result = database.update_group_membership_start_date(
            group_id=100,
            member_id=77,
            start_date=date(2026, 7, 9),
        )

        self.assertTrue(result["changed"])
        self.assertEqual(date(2026, 7, 9), cursor.current["start_date"])
        self.assertEqual(100, cursor.daily_rows[0]["group_id"])
        self.assertEqual([(77, "2026-07")], database.aggregate_calls)

    def test_backdate_across_contiguous_previous_group_hours_stays_guarded(self):
        previous = {
            "id": 9,
            "group_id": 200,
            "operator_id": 77,
            "start_date": date(2026, 6, 1),
            "end_date": date(2026, 7, 9),
        }
        cursor = _MembershipEditCursor(
            current_start=date(2026, 7, 10),
            previous=previous,
            daily_rows=[{
                "operator_id": 77,
                "day": date(2026, 7, 9),
                "group_id": 200,
                "work_time": 8,
            }],
        )
        database = _membership_edit_database(cursor)

        with self.assertRaisesRegex(ValueError, "учтённые часы"):
            database.update_group_membership_start_date(
                group_id=100,
                member_id=77,
                start_date=date(2026, 7, 9),
            )

        self.assertEqual(date(2026, 7, 10), cursor.current["start_date"])
        self.assertEqual(date(2026, 7, 9), cursor.previous["end_date"])
        self.assertEqual([], database.aggregate_calls)
        self.assertFalse(any(sql.startswith("UPDATE") for sql, _ in cursor.executions))

    def test_forward_shift_over_current_group_hours_stays_guarded(self):
        cursor = _MembershipEditCursor(
            current_start=date(2026, 7, 9),
            daily_rows=[{
                "operator_id": 77,
                "day": date(2026, 7, 9),
                "group_id": 100,
                "work_time": 8,
            }],
        )
        database = _membership_edit_database(cursor)

        with self.assertRaisesRegex(ValueError, "учтённые часы"):
            database.update_group_membership_start_date(
                group_id=100,
                member_id=77,
                start_date=date(2026, 7, 10),
            )

        self.assertEqual(date(2026, 7, 9), cursor.current["start_date"])
        self.assertEqual([], database.aggregate_calls)
        self.assertFalse(any(sql.startswith("UPDATE") for sql, _ in cursor.executions))

    def test_forward_shift_over_noncontiguous_gap_targets_no_group(self):
        previous = {
            "id": 9,
            "group_id": 200,
            "operator_id": 77,
            "start_date": date(2026, 6, 1),
            "end_date": date(2026, 7, 7),
        }
        cursor = _MembershipEditCursor(
            current_start=date(2026, 7, 9),
            previous=previous,
            daily_rows=[{
                "operator_id": 77,
                "day": date(2026, 7, 9),
                "group_id": 100,
                "work_time": 0,
            }],
        )
        database = _membership_edit_database(cursor)

        result = database.update_group_membership_start_date(
            group_id=100,
            member_id=77,
            start_date=date(2026, 7, 10),
        )

        self.assertTrue(result["changed"])
        self.assertEqual(date(2026, 7, 10), cursor.current["start_date"])
        self.assertEqual(date(2026, 7, 7), cursor.previous["end_date"])
        self.assertIsNone(cursor.daily_rows[0]["group_id"])
        self.assertEqual([(77, "2026-07")], database.aggregate_calls)

    def test_db_method_moves_chain_and_restamps_hours(self):
        self.assertIn(
            "def update_group_membership_start_date(self, group_id, member_id, kind='operator',", DB
        )
        method = DB.split("def update_group_membership_start_date(", 1)[1].split(
            "def _load_operator_calculation_models_tx(", 1
        )[0]
        # прошлое членство, закрытое встык, двигается вместе — без дыр/нахлёстов
        self.assertIn("prev[3] == old_start - timedelta(days=1)", method)
        # часы за сдвинутые дни переезжают в нужную группу, месяцы пересчитываются
        self.assertIn("UPDATE daily_hours SET group_id = %s", method)
        self.assertIn("_aggregate_month_from_daily_tx(cursor, member_id, m)", method)

    def test_shift_over_days_with_hours_is_blocked_before_any_write(self):
        """Опечатка в дате не должна уносить часы в соседнюю группу."""
        self.assertIn("def _assert_no_operator_hours_in_window_tx(self, cursor, operator_id, lo, hi)", DB)
        self.assertIn("_DAILY_HOURS_NON_EMPTY", DB)
        method = DB.split("def update_group_membership_start_date(", 1)[1].split(
            "def _load_operator_calculation_models_tx(", 1
        )[0]
        # гвард стоит ДО первого UPDATE — иначе часть правки успела бы записаться
        guard_at = method.index("_assert_no_operator_hours_in_window_tx(cursor, member_id, lo, hi)")
        self.assertLess(guard_at, method.index("UPDATE " + '" + table + "' + " SET end_date"))
        self.assertLess(guard_at, method.index("UPDATE " + '" + table + "' + " SET start_date"))

    def test_frontend_shows_block_reason_inline_not_in_toast(self):
        # причину отказа надо прочитать и исправить дату — тост живёт 5 секунд
        self.assertIn("error: data.error || 'Не удалось изменить дату'", GROUPS_VIEW)
        self.assertIn("dateEdit.error", GROUPS_VIEW)

    def test_endpoint_exists_and_is_scoped(self):
        self.assertIn(
            "@app.route('/api/admin/groups/<int:group_id>/member_start_date', methods=['POST'])", BOT
        )
        ep = BOT.split("def update_group_member_start_date_endpoint(", 1)[1].split("@app.route", 1)[0]
        self.assertIn("_ensure_group_manager()", ep)
        self.assertIn("_ensure_group_in_requester_scope(group_id, rid, role)", ep)
        self.assertIn("db.update_group_membership_start_date(", ep)

    def test_frontend_edits_date_inline_in_members(self):
        self.assertIn("/member_start_date", GROUPS_VIEW)
        self.assertIn("submitDateEdit", GROUPS_VIEW)
        self.assertIn("renderMemberRow", GROUPS_VIEW)


class ModelChangeTests(unittest.TestCase):
    """Смена модели группы с журналом и откатом (данные не теряются)."""

    def test_change_log_table_created(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS group_model_change_log", DB)
        self.assertIn("old_model_code VARCHAR(32)", DB)
        self.assertIn("new_model_code VARCHAR(32) NOT NULL", DB)
        self.assertIn("is_revert BOOLEAN NOT NULL DEFAULT FALSE", DB)

    def test_db_methods_exist(self):
        for m in [
            "def change_group_model(self, group_id, new_model_code, changed_by=None, is_revert=False)",
            "def get_group_model_history(self, group_id, limit=50)",
            "def revert_group_model(self, group_id, target_model_code=None, changed_by=None)",
        ]:
            self.assertIn(m, DB, f"missing method: {m}")

    def test_change_logs_before_update(self):
        # Изменение журналируется (INSERT в лог) и только потом меняется модель группы.
        self.assertIn("INSERT INTO group_model_change_log", DB)
        self.assertIn("UPDATE groups SET calculation_model_code = %s", DB)

    def test_endpoints_exist(self):
        for r in [
            "@app.route('/api/admin/groups/<int:group_id>/model', methods=['POST'])",
            "@app.route('/api/admin/groups/<int:group_id>/model_history', methods=['GET'])",
            "@app.route('/api/admin/groups/<int:group_id>/model/revert', methods=['POST'])",
        ]:
            self.assertIn(r, BOT, f"missing route: {r}")

    def test_frontend_wires_model_change(self):
        self.assertIn("/model_history", GROUPS_VIEW)
        self.assertIn("/model/revert", GROUPS_VIEW)
        self.assertIn("revertModel", GROUPS_VIEW)
        self.assertIn("submitModelChange", GROUPS_VIEW)


class ReadPathTests(unittest.TestCase):
    def test_group_month_read_and_legacy_preserved(self):
        self.assertIn("def get_daily_hours_by_group_month(self, group_id, month)", DB)
        self.assertIn(
            "def get_daily_hours_by_supervisor_month(self, supervisor_id, month, group_id=None)",
            DB,
        )

    def test_sv_daily_hours_accepts_group_id(self):
        self.assertIn("get_daily_hours_by_group_month", BOT)
        self.assertIn("get_supervisor_group_ids", BOT)

    def test_group_month_reads_activity_metrics_by_historical_membership(self):
        for helper in [
            "_load_training_hours_by_operator_tx(self, cursor, operator_ids, start_date, end_date, group_id=None)",
            "_load_technical_issues_by_operator_day_tx(self, cursor, operator_ids, start_date, end_date, group_id=None)",
            "_load_offline_activities_by_operator_day_tx(self, cursor, operator_ids, start_date, end_date, group_id=None)",
            "_load_chat_manager_metrics_by_operator_day_tx(self, cursor, operator_ids, start_date, end_date, group_id=None)",
        ]:
            self.assertIn(helper, DB)
        self.assertIn("gom.group_id = %s", DB)
        self.assertIn("group_id=group_id", DB)

    def test_group_writes_use_membership_date_instead_of_current_supervisor(self):
        self.assertIn("selected_group_id = None", BOT)
        self.assertIn("db.operator_in_group_on_date(selected_group_id, op_id_int, row_day_obj)", BOT)
        self.assertIn("db.find_operator_in_group_by_name(selected_group_id, name, row_day_obj)", BOT)
        self.assertIn("group_id=selected_group_id", BOT)

    def test_hours_frontend_sends_selected_group_context(self):
        self.assertIn("hoursParams.append('group_id', selectedGroupId)", APP)
        self.assertIn("trainingsParams.append('group_id', selectedGroupId)", APP)
        self.assertIn("technicalParams.append('group_id', selectedGroupId)", APP)
        self.assertIn("offlineParams.append('group_id', selectedGroupId)", APP)
        self.assertIn("group_id: selectedGroupId || null", APP)


class SupervisorSyncTests(unittest.TestCase):
    """Оператор создаётся сразу в группу; users.supervisor_id — производное от
    СВ группы и синхронизируется при любой смене членства (оператора или СВ)."""

    def test_sync_helpers_exist(self):
        for m in [
            "def _group_active_supervisor_id_tx(self, cursor, group_id)",
            "def _set_operators_supervisor_tx(self, cursor, operator_ids, supervisor_id)",
            "def _sync_group_operators_supervisor_tx(self, cursor, group_id)",
            "def get_group_active_supervisor_id(self, group_id)",
        ]:
            self.assertIn(m, DB, f"missing helper: {m}")

    def test_sync_updates_both_users_and_profiles(self):
        self.assertIn("UPDATE users SET supervisor_id = %s WHERE id = ANY(%s)", DB)
        self.assertIn("UPDATE operator_profiles SET supervisor_id = %s WHERE user_id = ANY(%s)", DB)

    def test_membership_mutations_resync_supervisor(self):
        # add_operator_to_group: оператору проставляется СВ новой группы.
        add_op = DB.split("def add_operator_to_group(", 1)[1].split("def remove_operator_from_group(", 1)[0]
        self.assertIn("_set_operators_supervisor_tx", add_op)
        self.assertIn("_group_active_supervisor_id_tx", add_op)
        # remove_operator_from_group: без группы нет СВ (или наследуется от оставшейся).
        remove_op = DB.split("def remove_operator_from_group(", 1)[1].split("def add_supervisor_to_group(", 1)[0]
        self.assertIn("_set_operators_supervisor_tx", remove_op)
        # смена СВ группы каскадится на её операторов.
        add_sv = DB.split("def add_supervisor_to_group(", 1)[1].split("def remove_supervisor_from_group(", 1)[0]
        self.assertIn("_sync_group_operators_supervisor_tx", add_sv)
        remove_sv = DB.split("def remove_supervisor_from_group(", 1)[1].split("def reassign_operator_history(", 1)[0]
        self.assertIn("_sync_group_operators_supervisor_tx", remove_sv)

    def test_archive_group_clears_operator_supervisors(self):
        archive = DB.split("def archive_group(", 1)[1].split("def reuse_archived_group(", 1)[0]
        self.assertIn("_set_operators_supervisor_tx(cursor, orphaned, None)", archive)


class AddUserGroupTests(unittest.TestCase):
    """При создании оператора указывается группа (своего отдела), СВ — из группы."""

    def test_add_user_accepts_group_and_derives_supervisor(self):
        add_user = BOT.split("def add_user():", 1)[1].split("@app.route('/api/admin/directions'", 1)[0]
        self.assertIn("data.get('group_id')", add_user)
        self.assertIn("Группа не найдена", add_user)
        self.assertIn("Группа в архиве", add_user)
        self.assertIn("Группа не принадлежит выбранному отделу", add_user)
        self.assertIn("db.get_group_active_supervisor_id(target_group['id'])", add_user)
        self.assertIn("db.add_operator_to_group(", add_user)
        self.assertIn("start_date=hire_date", add_user)

    def test_create_payload_sends_group_id(self):
        self.assertIn("group_id: isCreatedTrainer ? null : (editedUser.group_id ? Number(editedUser.group_id) : null)", APP)
        self.assertIn("groups={userModalGroups}", APP)

    def test_modal_uses_group_select_instead_of_supervisor(self):
        self.assertIn("groupsForSelectedDept", USER_MODAL)
        self.assertIn("Группа обязательна", USER_MODAL)
        self.assertIn('group_id: ""', USER_MODAL)
        # смена отдела сбрасывает группу чужого отдела
        self.assertIn("next.group_id = ''", USER_MODAL)


class ExistingUserGroupEditTests(unittest.TestCase):
    """Редактирование существующих сотрудников тоже идёт через группу:
    СВ напрямую не меняется ни в карточке, ни массово."""

    def test_admin_users_projection_includes_current_group(self):
        get_users = BOT.split("def get_admin_users():", 1)[1].split("def admin_update_user():", 1)[0]
        self.assertIn("grp.group_id", get_users)
        self.assertIn("grp.group_name", get_users)
        self.assertIn("FROM group_operator_memberships gom", get_users)
        self.assertIn("gom.end_date IS NULL", get_users)
        self.assertIn('"group_id": row[49]', get_users)
        self.assertIn('"group_name": row[50]', get_users)

    def test_update_user_blocks_direct_supervisor_change(self):
        upd = BOT.split("def admin_update_user():", 1)[1].split("def admin_bulk_update_users():", 1)[0]
        self.assertIn("Супервайзер назначается группой оператора", upd)

    def test_bulk_update_moves_group_instead_of_supervisor(self):
        bulk = BOT.split("def admin_bulk_update_users():", 1)[1].split("def admin_promote_to_supervisor():", 1)[0]
        self.assertIn("allowed_fields = {'direction_id', 'group_id', 'rate'}", bulk)
        self.assertNotIn("updates['supervisor_id']", bulk)
        self.assertIn("db.add_operator_to_group(target_group['id'], target_user_id, assigned_by=requester_id)", bulk)
        # скоуп: не-глобальный админ переводит только в группы своего отдела
        self.assertIn("Группа не из вашего отдела", bulk)
        # группа применима только к операторам/стажёрам
        self.assertIn("target_group is not None and target_role not in ('operator', 'trainee')", bulk)

    def test_edit_flow_posts_group_membership_not_supervisor(self):
        self.assertIn("`${API_BASE_URL}/api/admin/groups/${nextGroupId}/operators`", APP)
        # в edit-флоу больше нет прямого update_user(supervisor_id) для операторов —
        # осталось только легаси-обнуление для тренеров
        self.assertEqual(APP.count("field: 'supervisor_id'"), 1)

    def test_bulk_panel_uses_groups(self):
        self.assertIn("Группа: не менять", APP)
        self.assertIn("payloadChanges.group_id = Number(bulkManageUsersChanges.group_id)", APP)
        self.assertNotIn("payloadChanges.supervisor_id", APP)

    def test_edit_modal_prefills_and_changes_group(self):
        self.assertIn('group_id: isTrainerBase ? "" : (base.group_id ?? "")', USER_MODAL)
        self.assertIn("Супервайзер меняется автоматически вместе с группой", USER_MODAL)
        # прямого селекта СВ в модалке больше нет
        self.assertNotIn("Выберите супервайзера", USER_MODAL)


class SupervisorGroupChangeTests(unittest.TestCase):
    """Задача #228: у СВ смена ГРУППЫ вместо прежней смены НАПРАВЛЕНИЯ."""

    def test_group_move_endpoint_lets_supervisor_move_own_department(self):
        ep = BOT.split("def add_group_operator_endpoint(", 1)[1].split("@app.route", 1)[0]
        self.assertIn("_ensure_group_operator_manager()", ep)
        # у СВ свой скоуп: активные группы своего отдела
        self.assertIn("_is_plain_supervisor_requester(rid, _role)", ep)
        self.assertIn("_ensure_group_in_supervisor_scope(group_id, rid)", ep)
        # прежний путь админа и главы отдела не тронут
        self.assertIn("_ensure_group_in_requester_scope(group_id, rid, _role)", ep)
        # СВ переводит между группами, но не оставляет оператора без группы
        self.assertIn("Убрать оператора из группы может админ или глава отдела", ep)
        self.assertIn("supervisor_target_roles=('operator', 'trainee')", ep)

    def test_supervisor_scope_helper_requires_active_group_of_own_department(self):
        helper = BOT.split("def _ensure_group_in_supervisor_scope(", 1)[1].split("def ", 1)[0]
        self.assertIn("grp.get('status') != 'active'", helper)
        self.assertIn("grp.get('department_id') != sv_dept", helper)

    def test_group_manager_guard_still_excludes_supervisors(self):
        # остальные операции с группами (архив, модель, состав СВ) СВ по-прежнему недоступны
        guard = BOT.split("def _ensure_group_manager():", 1)[1].split("def ", 1)[0]
        self.assertNotIn("_is_supervisor_role", guard)

    def test_direction_change_is_denied_for_plain_supervisor(self):
        upd = BOT.split("def admin_update_user():", 1)[1].split("def admin_bulk_update_users():", 1)[0]
        self.assertIn(
            "if field == 'direction_id' and requester_role == 'sv' and headed_dept_id is None:",
            upd,
        )
        bulk = BOT.split("def admin_bulk_update_users():", 1)[1].split("def admin_promote_to_supervisor():", 1)[0]
        self.assertIn("if requester_role == 'sv' and headed_dept_id is None:", bulk)
        self.assertIn(
            "Направление сотрудника меняет админ или глава отдела — супервайзер меняет группу",
            bulk,
        )

    def test_modal_shows_group_and_hides_direction_for_supervisor(self):
        edit_block = USER_MODAL.split("--- Режим редактирования", 1)[1]
        # селект группы больше не заперт на админа и главу отдела
        self.assertNotIn(
            "{(isAdminLikeRequester || isScopedDepartmentHeadRequester) && isOperatorDraft(editedUser) && (",
            edit_block,
        )
        # направление в карточке — не для обычного СВ (и не для бэк-офиса,
        # где направлений нет вовсе: showOperatorLineFields)
        self.assertIn(
            "{isOperatorDraft(editedUser) && !isPureSupervisorRequester && showOperatorLineFields && (",
            edit_block,
        )
        # СВ не может оставить оператора без группы
        self.assertIn("{(!isPureSupervisorRequester || !editedUser?.group_id) && (", edit_block)
        # раздела «Группы» у СВ нет — не отправляем его туда
        self.assertIn(
            "{!isPureSupervisorRequester && ' Убрать оператора из группы совсем — в разделе «Группы».'}",
            edit_block,
        )

    def test_supervisor_section_prefetches_groups(self):
        self.assertIn("|| (view === 'manage_operators' && isDepartmentManager)) {", APP)


if __name__ == "__main__":
    unittest.main()
