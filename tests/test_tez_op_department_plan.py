"""Task #377: frozen opening headcount, live actual target and API settings."""
import ast
import copy
import logging
import math
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from tests import source_cache
from tez import department_plan as plans

ROOT = Path(__file__).resolve().parents[1]
BOT_SOURCE = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')


class FakeDb:
    def __init__(self):
        self.snapshot = None
        self.snapshot_version = 1
        self.staff = [(1, 0.5, 'fired'), (2, 1, 'working'), (3, 1, 'fired')]
        self.hist = {'rate': {1: '1'}, 'status': {1: 'working', 3: 'fired'}}
        self.references = []
        self.groups = 1
        self.get_department_monthly_plan = Mock(return_value={'plan_per_fte': 100, 'norm_hours_fte': 160})

    @contextmanager
    def _get_cursor(self):
        yield self

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params
        if 'INSERT INTO tez_department_plan_snapshots' in sql:
            self.snapshot = (params[2], params[3], datetime(2026, 7, 2))
            self.snapshot_version = params[4]

    def fetchone(self):
        if 'tez_department_plan_snapshots' in self.sql:
            if 'calculation_version >=' in self.sql and self.snapshot_version < self.params[2]:
                return None
            return self.snapshot
        return (202 if 'tez_lead_successes' in self.sql else self.groups,)

    def fetchall(self):
        if 'FROM user_history' in self.sql:
            return [(uid, 'working', status, datetime(2026, 6, 1))
                    for uid, status in self.hist['status'].items()]
        if 'FROM operator_schedule_status_periods' in self.sql:
            return []
        return self.staff

    def _resolve_user_field_as_of_tx(self, cursor, ids, field, reference):
        self.references.append(reference)
        return self.hist[field]


def test_snapshot_uses_historical_rates_and_status_and_never_changes():
    db = FakeDb()
    first = plans.month_start_snapshot(db, 560, 2026, 7)
    assert first[:2] == (2, 2)
    assert db.references == [datetime(2026, 7, 1, microsecond=1)]
    db.staff = [(100, 20, 'working')]
    db.hist = {'rate': {}, 'status': {}}
    assert plans.month_start_snapshot(db, 560, 2026, 7) == first


def test_zero_fte_is_a_snapshot_too_and_future_month_is_not():
    db = FakeDb()
    db.staff = []
    first = plans.month_start_snapshot(db, 560, 2026, 7)
    db.staff = [(5, 1, 'working')]
    assert plans.month_start_snapshot(db, 560, 2026, 7) == first
    assert first[:2] == (0, 0)
    assert plans.month_start_snapshot(db, 560, 9999, 12) is None


@pytest.mark.parametrize('status', ['bs', 'unpaid_leave', 'sick_leave', 'annual_leave', 'fired', 'dismissal'])
def test_only_working_status_on_the_first_counts(status):
    db = FakeDb()
    db.staff = [(1, 1, 'working'), (2, 0.5, status)]
    db.hist = {'rate': {}, 'status': {1: status, 2: 'working'}}
    assert plans.month_start_snapshot(db, 560, 2026, 7)[:2] == (0.5, 1)


def test_legacy_snapshot_is_repaired_once():
    db = FakeDb()
    db.snapshot = (3, 3, datetime(2026, 7, 1))
    db.hist['status'][2] = 'bs'
    first = plans.month_start_snapshot(db, 560, 2026, 7)
    assert first[:2] == (1, 1)
    db.hist['status'][2] = 'working'
    assert plans.month_start_snapshot(db, 560, 2026, 7) == first


@pytest.fixture
def summary_db(monkeypatch):
    monkeypatch.setattr(plans, 'month_start_snapshot', lambda *args: (5.5, 6, datetime(2026, 7, 1)))
    monkeypatch.setattr(plans, 'accounted_hours', lambda *args: 440)
    return FakeDb()


def test_fixed_and_actual_targets(summary_db):
    out = plans.summary(summary_db, 560, 2026, 7, 100, 160)
    assert out['plan_total'] == 440
    assert out['actual_fte'] == 2.75
    assert out['actual_plan'] == 220
    assert out['closure_pct'] == 91.8
    assert summary_db.params == (2026, 7, 560)
    summary_db.get_department_monthly_plan.assert_not_called()


def test_hours_change_only_actual_target(summary_db, monkeypatch):
    before = plans.summary(summary_db, 560, 2026, 7)
    monkeypatch.setattr(plans, 'accounted_hours', lambda *args: 880)
    after = plans.summary(summary_db, 560, 2026, 7)
    assert before['plan_total'] == after['plan_total'] == 440
    assert after['actual_plan'] == 440 and after['closure_pct'] == 45.9


@pytest.mark.parametrize('hours,fte,norm,plan', [(0, 5, 160, 100), (80, 0, 160, 100), (80, 5, 0, 100), (80, 5, 160, 0)])
def test_no_division_by_zero(summary_db, monkeypatch, hours, fte, norm, plan):
    monkeypatch.setattr(plans, 'accounted_hours', lambda *args: hours)
    monkeypatch.setattr(plans, 'month_start_snapshot', lambda *args: (fte, 6, datetime(2026, 7, 1)))
    assert plans.summary(summary_db, 560, 2026, 7, plan, norm)['closure_pct'] is None


def test_non_tez_department_has_no_summary(summary_db):
    summary_db.groups = 0
    assert plans.summary(summary_db, 560, 2026, 7) is None


def call_endpoint(name, db, payload=None, role='admin', scope=560):
    tree = source_cache.parse(BOT_SOURCE)
    node = copy.deepcopy(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name))
    node.decorator_list = []
    request = Mock(method='POST' if payload is not None else 'GET')
    request.args = {'department_id': '560', 'year': '2026', 'month': '7'}
    request.get_json.return_value = payload
    ns = dict(request=request, db=db, jsonify=lambda x: x, logging=logging, math=math,
              _get_authenticated_requester=lambda: (7, (7, 'x', 'y', role), None),
              _normalize_user_role=lambda x: x,
              _is_global_admin_requester=lambda *args: role == 'admin',
              _is_admin_role=lambda x: x == 'admin',
              _is_supervisor_role=lambda x: x == 'supervisor',
              _headed_department_id=lambda x: None,
              _department_scope_id_for_requester=lambda x: scope)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot_schedule2.py', 'exec'), ns)
    return ns[name]()


def test_api_returns_settings_and_summary_together():
    db = Mock()
    db.get_department_monthly_plan.return_value = {'plan_per_fte': 100, 'norm_hours_fte': 152}
    db.get_tez_op_department_plan_summary.return_value = {'plan_total': 440, 'actual_plan': 220}
    result, status = call_endpoint('get_department_plan', db)
    assert status == 200 and result['plan']['norm_hours_fte'] == 152
    assert result['summary']['actual_plan'] == 220
    db.get_tez_op_department_plan_summary.assert_called_once_with(560, 2026, 7, plan_per_fte=100, norm_hours_fte=152)


def test_api_defaults_and_summary_failure():
    db = Mock()
    db.get_department_monthly_plan.return_value = None
    db.get_tez_op_department_plan_summary.side_effect = RuntimeError('test')
    result, status = call_endpoint('get_department_plan', db)
    assert status == 200 and result['plan']['norm_hours_fte'] == 176
    assert result['summary'] is None


@pytest.mark.parametrize('value', [0, 0.001, -1, 745, 'NaN', 'Infinity', None, ''])
def test_api_rejects_bad_fte_norm(value):
    db = Mock()
    _, status = call_endpoint('save_department_plan', db, dict(department_id=560, year=2026, month=7, plan_per_fte=100, norm_hours_fte=value))
    assert status == 400
    db.upsert_department_monthly_plan.assert_not_called()


@pytest.mark.parametrize('role,scope,status', [('operator', 560, 403), ('supervisor', 561, 403), ('supervisor', 560, 200)])
def test_only_department_manager_can_save(role, scope, status):
    db = Mock()
    _, actual = call_endpoint('save_department_plan', db, dict(department_id=560, year=2026, month=7, plan_per_fte=100, norm_hours_fte=152), role, scope)
    assert actual == status
    if status == 200:
        db.upsert_department_monthly_plan.assert_called_once_with(560, 2026, 7, 100, updated_by=7, norm_hours_fte=152)
    else:
        db.upsert_department_monthly_plan.assert_not_called()


def test_all_individual_plan_surfaces_receive_fte_norm():
    app = (ROOT / 'src/App.jsx').read_text(encoding='utf-8')
    for block in app.split('calculateTezOpMonthlyPlan({')[1:]:
        assert 'normHoursFte:' in block.split('})', 1)[0]
    assert 'normHoursFte={tezNormHoursFte}' in app
