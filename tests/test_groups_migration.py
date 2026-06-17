"""Статические инварианты миграции supervisor_id -> группы.

В духе остального харнеса: без живой БД, через инспекцию исходников. Рантайм-поведение
(переводы, метки модели) проверено вживую на проде; здесь фиксируем код-инварианты,
чтобы будущие правки их не сломали.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = (ROOT / "database.py").read_text(encoding="utf-8-sig")
BOT = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
APP = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")


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
        # group-aware ветка идёт через членство, fallback — направление
        self.assertIn("group_operator_memberships gom", DB)

    def test_monthly_callers_thread_as_of(self):
        self.assertIn("_get_operator_calculation_model_tx(cursor, operator_id, as_of=end)", DB)
        self.assertIn("_get_operator_calculation_model_tx(cursor, operator_id, as_of=_cm_end)", DB)

    def test_range_callers_stay_legacy(self):
        # диапазонные вызовы НЕ передают as_of: классификация статусов на пересчёте
        # сохраняет прежнее поведение (намеренно).
        self.assertIn(
            "calculation_model_by_operator = self._load_operator_calculation_models_tx(cursor, op_ids)",
            DB,
        )


class UpsertSafetyTests(unittest.TestCase):
    def test_daily_conflict_unchanged_but_stamps_group(self):
        # правило «один оператор = одна группа в день» делает (operator_id, day) совместимым
        self.assertIn("ON CONFLICT (operator_id, day)", DB)
        self.assertIn("group_id = COALESCE(EXCLUDED.group_id, daily_hours.group_id)", DB)

    def test_work_hours_conflict_not_swapped_yet(self):
        # рискованный своп (group_id, operator_id, month) отложен — без потери данных
        self.assertIn("ON CONFLICT (operator_id, month)", DB)
        self.assertIn("group_id = COALESCE(EXCLUDED.group_id, work_hours.group_id)", DB)


class BackfillTests(unittest.TestCase):
    def test_backfill_exists_guarded_and_savepointed(self):
        self.assertIn("def _backfill_groups_from_supervisors_tx(self, cursor)", DB)
        self.assertIn("SELECT 1 FROM groups LIMIT 1", DB)  # идемпотентный guard
        self.assertIn("SAVEPOINT sp_groups_backfill", DB)  # сбой не роняет init

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
        # перевод в новую группу закрывает прошлую основную
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


class ReadPathTests(unittest.TestCase):
    def test_group_month_read_and_legacy_preserved(self):
        self.assertIn("def get_daily_hours_by_group_month(self, group_id, month)", DB)
        self.assertIn(
            "def get_daily_hours_by_supervisor_month(self, supervisor_id, month, group_id=None)",
            DB,
        )

    def test_sv_daily_hours_accepts_group_id(self):
        self.assertIn("get_daily_hours_by_group_month", BOT)
        self.assertIn("get_supervisor_group_ids", BOT)  # проверка доступа СВ к группе

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


if __name__ == "__main__":
    unittest.main()
