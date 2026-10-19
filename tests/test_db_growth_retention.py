"""Рост боевой базы (19.10.2026): чистки и индексы, которые держат диск в рамках.

За месяц база выросла с 1,4 до 3,1 ГБ из 5 ГБ. Часть роста давали таблицы без чистки
(журнал опросов OLX, прогоны парсера резюме, файлы выгрузок ЭДО) и индексы, которые никто
не читал, но которые раздувались на каждом обновлении. Эти тесты держат то, что легко
вернуть одной строкой: CREATE INDEX IF NOT EXISTS на старте вернул бы удалённый индекс,
а забытый вызов чистки — бесконечный рост.
"""
import ast
import logging
import re
import unittest
from contextlib import contextmanager
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
OP_FUNNEL_SCHEMA_PATH = ROOT / "op_funnel" / "schema.py"

DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8-sig")


def _database_methods(*names):
    module = source_cache.parse(DATABASE_SOURCE)
    source_class = next(node for node in module.body
                        if isinstance(node, ast.ClassDef) and node.name == "Database")
    methods = [node for node in source_class.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in methods} != set(names):
        raise AssertionError(f"нет методов Database: {sorted(set(names) - {m.name for m in methods})}")
    test_module = ast.fix_missing_locations(ast.Module(
        body=[ast.ClassDef(name="Database", bases=[], keywords=[], body=methods,
                           decorator_list=[])],
        type_ignores=[]))
    namespace = {"logging": logging, "RECRUITING_RUNS_KEEP_DAYS": 14}
    exec(compile(test_module, str(DATABASE_PATH), "exec"), namespace)
    return namespace["Database"]


class _Cursor:
    def __init__(self, rowcount=0, fail=False):
        self.rowcount = rowcount
        self.fail = fail
        self.executions = []

    def execute(self, query, params=None):
        self.executions.append((" ".join(str(query).split()), params))
        if self.fail:
            raise RuntimeError("lock timeout")


def _with_cursor(database, cursor):
    @contextmanager
    def _get_cursor():
        yield cursor
    database._get_cursor = _get_cursor
    return database


def _method_source(name):
    module = source_cache.parse(DATABASE_SOURCE)
    source_class = next(node for node in module.body
                        if isinstance(node, ast.ClassDef) and node.name == "Database")
    node = next(node for node in source_class.body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    lines = DATABASE_SOURCE.splitlines()
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


class RecruitingRunsRetentionTests(unittest.TestCase):
    """Каждый прогон парсера хранит полный снимок выдачи, а раздел читает только последний
    успешный: без чистки 98% recruiting_resumes были повторами."""

    def setUp(self):
        self.Database = _database_methods("_prune_recruiting_parse_runs")

    def test_old_runs_go_and_the_fresh_one_is_kept(self):
        cursor = _Cursor(rowcount=3)
        database = _with_cursor(self.Database(), cursor)
        self.assertEqual(3, database._prune_recruiting_parse_runs(keep_run_uuid="run-new"))
        sql, params = cursor.executions[0]
        self.assertIn("DELETE FROM recruiting_parse_runs", sql)
        # finished_at пишется стенным временем Алматы — и граница в той же зоне.
        self.assertIn("CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'", sql)
        self.assertIn("make_interval(days => %s)", sql)
        # Только что записанный прогон не трогаем ни при каком сроке.
        self.assertIn("run_uuid <> %s::uuid", sql)
        self.assertEqual((14, "run-new"), params)

    def test_a_failed_prune_does_not_undo_the_saved_run(self):
        """Чистка идёт своей транзакцией и не бросает: прогон уже записан и показан."""
        database = _with_cursor(self.Database(), _Cursor(fail=True))
        with self.assertLogs(level="ERROR"):
            self.assertEqual(0, database._prune_recruiting_parse_runs(keep_run_uuid="run-new"))

    def test_prune_runs_after_the_run_is_committed(self):
        """Вызов стоит ПОСЛЕ транзакции записи, а не внутри неё: сбой чистки не должен
        откатить новый прогон и его резюме."""
        source = _method_source("save_recruiting_parse_success")
        call = source.index("self._prune_recruiting_parse_runs(keep_run_uuid=run_uuid)")
        last_with = source.rindex("with self._get_cursor() as cursor:")
        self.assertGreater(call, last_with)
        call_line = source[:call].splitlines()[-1] + "self."
        self.assertEqual(len(call_line) - len(call_line.lstrip()), 8,
                         "чистка обязана стоять на уровне тела метода, вне with")

    def test_resumes_go_with_their_run(self):
        """Резюме уходят каскадом по внешнему ключу — отдельного DELETE нет."""
        self.assertRegex(DATABASE_SOURCE, re.compile(
            r"run_uuid UUID NOT NULL REFERENCES recruiting_parse_runs\(run_uuid\)\s+ON DELETE CASCADE"))


class DroppedIndexesStayDroppedTests(unittest.TestCase):
    """Индекс, удалённый только в базе, вернётся на следующем деплое: схема
    разворачивается при каждом старте через CREATE INDEX IF NOT EXISTS."""

    def test_user_sessions_last_seen_index_is_not_recreated(self):
        """Индекс на last_seen_at делал каждое продление сессии не-HOT обновлением,
        а читать его некому; на 9 тыс. строк его раздуло до 31 МБ."""
        self.assertNotIn("CREATE INDEX IF NOT EXISTS idx_user_sessions_active_last_seen",
                         DATABASE_SOURCE)
        self.assertIn("DROP INDEX IF EXISTS idx_user_sessions_active_last_seen", DATABASE_SOURCE)

    def test_op_funnel_unread_indexes_are_not_recreated(self):
        from op_funnel import schema
        for index in ("idx_op_funnel_leads_reason", "idx_op_funnel_leads_phone"):
            self.assertNotIn(f"CREATE INDEX IF NOT EXISTS {index}", schema.OP_FUNNEL_SCHEMA_SQL)
            self.assertIn(f"DROP INDEX IF EXISTS {index}", schema.OP_FUNNEL_SCHEMA_MIGRATIONS)
        # Индекс, на котором держится список за причиной, остаётся.
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_op_funnel_leads_day", schema.OP_FUNNEL_SCHEMA_SQL)


class FleetEdmFilesRetentionTests(unittest.TestCase):
    """Чистка файлов выгрузок шла только при запуске новой выгрузки — без выгрузок файлы
    по ~0,7 МБ лежали бессрочно. Теперь она идёт каждую ночь."""

    def test_cleanup_is_scheduled_nightly(self):
        start = BOT_SOURCE.index("id='fleet_edm_retention_daily'")
        block = BOT_SOURCE[start - 300:start + 200]
        self.assertIn("run_fleet_edm_retention_async", block)
        self.assertIn("CronTrigger(hour=3, minute=55", block)
        self.assertIn("max_instances=1", block)

    def test_job_calls_the_module_cleanup_on_the_shared_pool(self):
        job = BOT_SOURCE[BOT_SOURCE.index("async def run_fleet_edm_retention_async"):]
        job = job[:job.index("\nasync def ")]
        self.assertIn("fleet_edm_queries.cleanup(cursor)", job)
        # Пул выгрузок — на одно место и бывает занят часами; чистка его не ждёт.
        self.assertIn("run_in_executor(executor_pool", job)
        self.assertNotIn("run_in_executor(fleet_edm_pool", job)


if __name__ == "__main__":
    unittest.main()
