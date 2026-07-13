"""Static invariants for the supervisor_id -> groups migration."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = (ROOT / "database.py").read_text(encoding="utf-8-sig")
BOT = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
APP = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
GROUPS_VIEW = (ROOT / "src" / "components" / "groups" / "GroupsView.jsx").read_text(encoding="utf-8-sig")
USER_MODAL = (ROOT / "src" / "components" / "modals" / "UserEditModal.jsx").read_text(encoding="utf-8-sig")


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


if __name__ == "__main__":
    unittest.main()
