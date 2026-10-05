"""Optional real SQL check: TEZ_PLAN_TEST_PORT points to an isolated local PG.

No application/database import or production credentials. All fixtures and the
actual migration run inside one rolled-back transaction in a private schema.
"""
import ast
import copy
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path

import pytest
from tests import source_cache
from tez.department_plan import summary, month_start_snapshot

PORT = os.environ.get('TEZ_PLAN_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PostgreSQL (TEZ_PLAN_TEST_PORT)')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db():
    import psycopg2
    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres')
    cursor = conn.cursor()
    cursor.execute('CREATE SCHEMA t377_' + uuid.uuid4().hex)
    schema = cursor.query.decode().split()[-1]
    cursor.execute('SET search_path TO ' + schema)
    cursor.execute('''
        CREATE TABLE departments (id integer PRIMARY KEY);
        CREATE TABLE users (id integer PRIMARY KEY, name text, rate numeric, status text, hire_date date);
        CREATE TABLE operator_profiles (user_id integer, rate numeric);
        CREATE TABLE groups (id integer PRIMARY KEY, department_id integer, calculation_model_code text);
        CREATE TABLE group_operator_memberships (operator_id integer, group_id integer, start_date date, end_date date);
        CREATE TABLE operator_schedule_status_periods (operator_id integer, status_code text, start_date date, end_date date);
        CREATE TABLE user_history (id serial, user_id integer, field_changed text, old_value text, new_value text, changed_at timestamp);
        CREATE TABLE daily_hours (operator_id integer, group_id integer, day date, work_time float);
        CREATE TABLE trainings (operator_id integer, training_date date, start_time time, end_time time, count_in_hours boolean);
        CREATE TABLE operator_technical_issues (id serial, operator_id integer, issue_date date, start_time time, end_time time, reason text, comment text, workplace_number integer, for_supervisor boolean, created_by integer, created_at timestamp);
        CREATE TABLE operator_offline_activities (id serial, operator_id integer, activity_date date, start_time time, end_time time, comment text, created_by integer, created_at timestamp);
        CREATE TABLE work_shifts (id serial, operator_id integer, shift_date date, start_time time, end_time time, shift_type text);
        CREATE TABLE tez_lead_batches (id integer PRIMARY KEY, department_id integer);
        CREATE TABLE tez_leads (id integer PRIMARY KEY, first_batch_id integer);
        CREATE TABLE tez_lead_successes (lead_id integer, year integer, month integer);
        CREATE TABLE department_monthly_plans (department_id integer, year integer, month integer,
            plan_per_fte numeric, updated_by integer, updated_at timestamp,
            UNIQUE(department_id, year, month));
    ''')
    tree = source_cache.parse((ROOT / 'database.py').read_text(encoding='utf-8-sig'))
    migration = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and 'CREATE TABLE IF NOT EXISTS tez_department_plan_snapshots' in n.value)
    cursor.execute(migration)
    cursor.execute(migration)  # repeat deployment must be harmless

    class TestDb:
        @contextmanager
        def _get_cursor(self):
            yield cursor

        def _load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(self, **kw):
            return {}, {}

        def _load_phone_shift_training_offline_intervals_by_operator_day_tx(self, **kw):
            return {}, {}

    ns = {'date': date, 'datetime': datetime, 'dt_time': time,
          '_time_to_minutes': lambda value: int(value[:2]) * 60 + int(value[3:5]),
          'WORK_SHIFT_OFFICE_PRACTICE_OFFLINE_COMMENT': 'Практика',
          'WORK_SHIFT_TYPE_OFFICE_PRACTICE': 'office_practice'}
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Database')
    for name in ['_resolve_user_field_as_of_tx', 'get_department_monthly_plan',
                 'upsert_department_monthly_plan', '_load_training_hours_by_operator_tx',
                 '_load_technical_issues_by_operator_day_tx', '_load_offline_activities_by_operator_day_tx',
                 '_schedule_interval_minutes', '_sum_phone_shift_offline_interval_maps']:
        node = copy.deepcopy(next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name))
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'database.py', 'exec'), ns)
        setattr(TestDb, name, ns[name])
    cursor.execute('''
        INSERT INTO departments VALUES (560), (561);
        INSERT INTO groups VALUES (10, 560, 'tez_op'), (11, 560, 'tez_op'), (12, 561, 'tez_op');
        INSERT INTO users VALUES
            (1, 'A', 0.5, 'working', '2025-01-01'),
            (2, 'B', 1, 'fired', '2025-01-01'),
            (3, 'C', 1, 'working', '2026-07-15'),
            (4, 'D', 1, 'fired', '2025-01-01'),
            (5, 'E', 1, 'working', '2025-01-01');
        INSERT INTO group_operator_memberships VALUES
            (1, 10, '2025-01-01', '2026-07-15'), (1, 11, '2026-07-16', NULL),
            (2, 10, '2025-01-01', NULL), (3, 10, '2026-07-15', NULL),
            (4, 10, '2025-01-01', NULL), (5, 12, '2025-01-01', NULL);
        INSERT INTO user_history (user_id, field_changed, old_value, new_value, changed_at) VALUES
            (1, 'rate', '1', '0.5', '2026-07-20'),
            (2, 'status', 'working', 'fired', '2026-07-10'),
            (4, 'status', 'working', 'fired', '2026-06-20');
        INSERT INTO operator_schedule_status_periods VALUES
            (2, 'dismissal', '2026-07-10', NULL), (4, 'dismissal', '2026-06-20', NULL);
        INSERT INTO daily_hours VALUES
            (1, 10, '2026-07-05', 40), (1, 11, '2026-07-20', 20),
            (2, 10, '2026-07-05', 20), (3, 10, '2026-07-20', 40),
            (5, 12, '2026-07-05', 999);
        INSERT INTO trainings VALUES (1, '2026-07-05', '10:00', '14:00', TRUE),
            (1, '2026-07-05', '14:00', '16:00', FALSE);
        INSERT INTO operator_technical_issues (operator_id, issue_date, start_time, end_time)
            VALUES (1, '2026-07-05', '15:00', '17:00');
        INSERT INTO operator_offline_activities (operator_id, activity_date, start_time, end_time)
            VALUES (3, '2026-07-20', '10:00', '13:00');
        INSERT INTO work_shifts (operator_id, shift_date, start_time, end_time, shift_type)
            VALUES (3, '2026-07-20', '14:00', '17:00', 'office_practice');
        INSERT INTO tez_lead_batches VALUES (1, 560), (2, 561);
        INSERT INTO tez_leads VALUES (1, 1), (2, 2);
        INSERT INTO tez_lead_successes VALUES (1, 2026, 7), (2, 2026, 7);
    ''')
    try:
        yield TestDb(), cursor
    finally:
        conn.rollback()
        conn.close()


def test_sql_snapshot_scope_hours_and_settings(db):
    obj, cursor = db
    saved = obj.upsert_department_monthly_plan(560, 2026, 7, 200, norm_hours_fte=160)
    assert saved['norm_hours_fte'] == 160
    assert obj.get_department_monthly_plan(560, 2026, 7)['plan_per_fte'] == 200
    first = summary(obj, 560, 2026, 7)
    # A was 1 FTE on July 1; B was still employed; C joined later; D already left.
    assert first['fte_total'] == 2
    assert first['plan_total'] == 320
    # Both groups, including C and B, plus counted extras, excluding other dept.
    assert first['actual_hours'] == 132
    assert first['actual_fte'] == 0.825
    assert first['actual_plan'] == 132
    assert first['successes_total'] == 1
    cursor.execute("UPDATE users SET rate = 0.25, status = 'fired'")
    cursor.execute("UPDATE group_operator_memberships SET end_date = '2026-07-31' WHERE operator_id = 1")
    cursor.execute("INSERT INTO daily_hours VALUES (3, 10, '2026-07-21', 8)")
    second = summary(obj, 560, 2026, 7)
    assert second['fte_total'] == first['fte_total']
    assert second['plan_total'] == first['plan_total']
    assert second['actual_hours'] == 140 and second['actual_plan'] == 140
    # Legacy clients saving only the target do not clear the saved norm.
    assert obj.upsert_department_monthly_plan(560, 2026, 7, 220)['norm_hours_fte'] == 160


def test_sql_new_month_gets_its_own_snapshot_and_legacy_norm(db):
    obj, cursor = db
    july = month_start_snapshot(obj, 560, 2026, 7)
    august = month_start_snapshot(obj, 560, 2026, 8)
    assert float(july[0]) == 2
    assert float(august[0]) == 1.5  # A's changed rate + C, excluding B and D
    assert obj.upsert_department_monthly_plan(560, 2026, 9, 200)['norm_hours_fte'] == 168
