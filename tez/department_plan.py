"""Monthly TEZ OP targets. Headcount is frozen; accounted hours stay live."""

import calendar
import math
from datetime import date, datetime
from zoneinfo import ZoneInfo


COEFFICIENT = 0.8


def default_norm_hours(year, month):
    # Same rule as opFteNormHoursForMonth in salaryFormula.js.
    return math.floor(calendar.monthrange(int(year), int(month))[1] / 7 * 5 + 0.5) * 8


def month_start_snapshot(db, department_id, year, month):
    """Freeze once, including zero FTE. Future months are never frozen early.

    First access after downtime/deployment reconstructs rates and employment
    status at midnight on the first from history, not today's employee cards.
    A unique key + DO NOTHING makes concurrent initializations harmless.
    """
    start = date(int(year), int(month), 1)
    if start > datetime.now(ZoneInfo('Asia/Almaty')).date():
        return None
    with db._get_cursor() as cursor:
        cursor.execute("""
            SELECT fte_total, operators_count, captured_at
            FROM tez_department_plan_snapshots
            WHERE department_id = %s AND month_start = %s
        """, (int(department_id), start))
        row = cursor.fetchone()
        if row:
            return row
        cursor.execute("""
            SELECT DISTINCT u.id, COALESCE(op.rate, u.rate, 0), u.status
            FROM group_operator_memberships gom
            JOIN groups g ON g.id = gom.group_id
            JOIN users u ON u.id = gom.operator_id
            LEFT JOIN operator_profiles op ON op.user_id = u.id
            WHERE g.department_id = %s AND g.calculation_model_code = 'tez_op'
              AND gom.start_date <= %s
              AND (gom.end_date IS NULL OR gom.end_date >= %s)
              AND (u.hire_date IS NULL OR u.hire_date <= %s)
              AND NOT EXISTS (
                  SELECT 1 FROM operator_schedule_status_periods p
                  WHERE p.operator_id = u.id AND p.status_code = 'dismissal'
                    AND p.start_date <= %s
                    AND (p.end_date IS NULL OR p.end_date >= %s)
              )
        """, (int(department_id), start, start, start, start, start))
        staff = cursor.fetchall()
        ids = [row[0] for row in staff]
        reference = datetime.combine(start, datetime.min.time())
        rates = db._resolve_user_field_as_of_tx(cursor, ids, 'rate', reference)
        statuses = db._resolve_user_field_as_of_tx(cursor, ids, 'status', reference)
        total, count = 0.0, 0
        for uid, current_rate, current_status in staff:
            status = str(statuses.get(uid, current_status) or '').strip().lower()
            if status in ('fired', 'dismissal'):
                continue
            rate = float(rates.get(uid, current_rate) or 0)
            if not math.isfinite(rate) or rate < 0:
                raise ValueError('Некорректная ставка на начало месяца')
            total += rate
            count += 1
        cursor.execute("""
            INSERT INTO tez_department_plan_snapshots
                (department_id, month_start, fte_total, operators_count)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (department_id, month_start) DO NOTHING
        """, (int(department_id), start, round(total, 4), count))
        cursor.execute("""
            SELECT fte_total, operators_count, captured_at
            FROM tez_department_plan_snapshots
            WHERE department_id = %s AND month_start = %s
        """, (int(department_id), start))
        return cursor.fetchone()


def accounted_hours(db, department_id, year, month):
    """Use the same counted training/technical/offline hours as Hours Accounting.

    Queries are batched per group, never per operator. Group membership filters
    preserve work by employees who have since left or transferred.
    """
    start = date(int(year), int(month), 1)
    end = date(int(year), int(month), calendar.monthrange(int(year), int(month))[1])
    with db._get_cursor() as cursor:
        cursor.execute("""
            SELECT g.id, ARRAY_AGG(DISTINCT gom.operator_id)
            FROM groups g JOIN group_operator_memberships gom ON gom.group_id = g.id
            WHERE g.department_id = %s AND g.calculation_model_code = 'tez_op'
              AND gom.start_date <= %s AND (gom.end_date IS NULL OR gom.end_date >= %s)
            GROUP BY g.id
        """, (int(department_id), end, start))
        groups = cursor.fetchall()
        cursor.execute("""
            SELECT COALESCE(SUM(d.work_time), 0)
            FROM daily_hours d JOIN groups g ON g.id = d.group_id
            WHERE g.department_id = %s AND g.calculation_model_code = 'tez_op'
              AND d.day BETWEEN %s AND %s
        """, (int(department_id), start, end))
        total = float(cursor.fetchone()[0] or 0)
        for group_id, ids in groups:
            args = dict(cursor=cursor, operator_ids=ids, start_date=start,
                        end_date=end, group_id=group_id)
            training = db._load_training_hours_by_operator_tx(**args)
            _, technical = db._load_technical_issues_by_operator_day_tx(**args)
            _, offline = db._load_offline_activities_by_operator_day_tx(**args)
            total += sum(float(v or 0) for values in (training, technical, offline)
                         for v in values.values())
    return max(0.0, total)


def summary(db, department_id, year, month, plan_per_fte=None, norm_hours_fte=None):
    with db._get_cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*) FROM groups
            WHERE department_id = %s AND calculation_model_code = 'tez_op'
        """, (int(department_id),))
        if not cursor.fetchone()[0]:
            return None
    if plan_per_fte is None or norm_hours_fte is None:
        plan = db.get_department_monthly_plan(department_id, year, month) or {}
        if plan_per_fte is None:
            plan_per_fte = plan.get('plan_per_fte', 0)
        if norm_hours_fte is None:
            norm_hours_fte = plan.get('norm_hours_fte', default_norm_hours(year, month))
    snapshot = month_start_snapshot(db, department_id, year, month)
    fte = float(snapshot[0]) if snapshot else None
    monthly = fte * float(plan_per_fte or 0) * COEFFICIENT if fte is not None else None
    hours = accounted_hours(db, department_id, year, month)
    norm = float(norm_hours_fte or 0)
    actual_fte = hours / norm if norm > 0 else None
    # Do not cancel out FTE: when the opening headcount is zero, the requested
    # formula is undefined. A new hire must not create an opening-month target.
    actual_plan = monthly / fte * actual_fte if fte and actual_fte is not None else None
    with db._get_cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*) FROM tez_lead_successes s
            JOIN tez_leads l ON l.id = s.lead_id
            JOIN tez_lead_batches b ON b.id = l.first_batch_id
            WHERE s.year = %s AND s.month = %s AND b.department_id = %s
        """, (int(year), int(month), int(department_id)))
        successes = int(cursor.fetchone()[0] or 0)
    return {
        'fte_total': fte, 'operators_count': int(snapshot[1]) if snapshot else None,
        'snapshot_at': snapshot[2].isoformat() if snapshot else None,
        'plan_per_fte': float(plan_per_fte or 0), 'norm_hours_fte': norm,
        'coefficient': COEFFICIENT,
        'plan_total': round(monthly, 2) if monthly is not None else None,
        'actual_hours': round(hours, 2),
        'actual_fte': round(actual_fte, 4) if actual_fte is not None else None,
        'actual_plan': round(actual_plan, 2) if actual_plan is not None else None,
        'successes_total': successes,
        'closure_pct': round(successes / actual_plan * 100, 1) if actual_plan and actual_plan > 0 else None,
    }


def freeze_current_month(db):
    today = datetime.now(ZoneInfo('Asia/Almaty')).date()
    with db._get_cursor() as cursor:
        cursor.execute("""SELECT DISTINCT department_id FROM groups
                          WHERE calculation_model_code = 'tez_op' AND department_id IS NOT NULL""")
        departments = cursor.fetchall()
    for (department_id,) in departments:
        month_start_snapshot(db, department_id, today.year, today.month)
