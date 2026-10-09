# -*- coding: utf-8 -*-
"""Аналитика базы обзвона за месяц — вкладка «Аналитика» раздела.

Просьба владельца 29.09.2026: сводку сверху экрана убрать, чтобы не отвлекала,
а показатели собрать дашбордом на отдельной вкладке. Всё считается по базе
ОДНОГО месяца (период лида) — так же, как журнал и успешки: база месяца P
проверяется по документам месяца P − 1, успешка принадлежит строке базы.

Три запроса, каждый — один проход по лидам месяца со своими попытками:
    totals     воронка: база → с ИИН → обзвонены → разговор ≥10 с → подписали → успешки,
               и разбивка по состоянию документов
    by_day     по дням (Алматы): звонки, разговоры ≥10 с, подписи и успешки
    operators  по операторам: звонки, разговоры, водители, успешки

Номеров, ИИН и ФИО здесь нет — только числа и имена операторов.
"""
from datetime import date, datetime, timedelta

from crm import sapar
from test_numbers import keys as test_keys
from . import signing

ANSWERED_SQL = ("ANSWER", "ANSWERED", "SUCCESS", "VM-SUCCESS")

# Лиды месяца и их попытки — общая часть всех трёх запросов.
_LEADS_CTE = """
    leads AS (
        SELECT l.id, l.iin, l.status, l.sign_status, l.signed_at, l.success_attempt_id,
               l.success_operator_id, l.success_resolved_at, l.created_at, l.sign_checked_at, l.sign_error
        FROM dial_list_leads l
        WHERE l.department_id = %(department_id)s AND l.period = %(period)s
          -- Номер из реестра тестовых (test_numbers) — проверка обзвона, а не водитель.
          AND """ + test_keys.sql_not_test('l.phone_norm', digits=True) + """
    ),
    att AS (
        SELECT a.lead_id, t.operator_id, t.requested_at, t.cancelled,
               (t.state = 'finished' AND UPPER(t.disposition) IN %(answered)s) AS answered,
               (t.state = 'finished' AND UPPER(t.disposition) IN %(answered)s
                AND t.billsec >= %(min_sec)s) AS talked,
               CASE WHEN t.state = 'finished' THEN t.billsec ELSE 0 END AS talk_sec
        FROM leads l
        JOIN dial_list_assignments a ON a.lead_id = l.id
        JOIN dial_list_attempts t ON t.assignment_id = a.id
    )
"""

_TOTALS_SQL = "WITH " + _LEADS_CTE + """,
    per_lead AS (
        SELECT lead_id,
               COUNT(*) FILTER (WHERE NOT cancelled) AS attempts,
               BOOL_OR(answered) AS answered,
               BOOL_OR(talked) AS talked
        FROM att GROUP BY lead_id
    )
    SELECT
        COUNT(*),
        COUNT(*) FILTER (WHERE l.iin <> ''),
        COUNT(*) FILTER (WHERE COALESCE(p.attempts, 0) > 0),
        COUNT(*) FILTER (WHERE p.answered),
        COUNT(*) FILTER (WHERE p.talked),
        COUNT(*) FILTER (WHERE l.signed_at IS NOT NULL),
        COUNT(*) FILTER (WHERE l.success_attempt_id IS NOT NULL),
        COUNT(*) FILTER (WHERE l.signed_at IS NOT NULL AND l.success_resolved_at IS NOT NULL
                           AND l.success_attempt_id IS NULL),
        COUNT(*) FILTER (WHERE l.signed_at IS NOT NULL AND l.success_resolved_at IS NOT NULL
                           AND l.success_attempt_id IS NULL AND l.signed_at < l.created_at),
        COUNT(*) FILTER (WHERE l.signed_at IS NOT NULL AND l.success_resolved_at IS NULL),
        COUNT(*) FILTER (WHERE l.signed_at IS NULL AND l.iin <> '' AND l.sign_status = 'processing'),
        COUNT(*) FILTER (WHERE l.signed_at IS NULL AND l.iin <> ''
                           AND l.sign_status IN ('unsigned', 'not_formed', 'rejected')),
        COUNT(*) FILTER (WHERE l.signed_at IS NULL AND l.iin <> '' AND l.sign_status = 'expired'),
        COUNT(*) FILTER (WHERE l.signed_at IS NULL AND l.iin <> '' AND l.sign_status = 'no_docs'),
        COUNT(*) FILTER (WHERE l.signed_at IS NULL AND l.iin <> '' AND l.sign_status = ''),
        COUNT(*) FILTER (WHERE l.status = 'excluded' AND l.signed_at IS NULL),
        COALESCE(SUM(p.attempts), 0),
        MAX(l.sign_checked_at),
        MIN(l.sign_checked_at) FILTER (WHERE l.iin <> '' AND l.signed_at IS NULL),
        COUNT(*) FILTER (WHERE l.iin <> '' AND l.signed_at IS NULL AND l.sign_error <> ''),
        COUNT(*) FILTER (WHERE l.iin <> '' AND l.signed_at IS NULL AND l.sign_checked_at IS NULL)
    FROM leads l
    LEFT JOIN per_lead p ON p.lead_id = l.id
"""
_TOTAL_KEYS = ("leads", "with_iin", "called", "answered", "talked", "signed", "successes", "self_signed",
               "signed_before_upload", "pending_attribution", "processing", "unsigned", "expired", "no_docs",
               "not_checked", "excluded", "attempts")

_BY_DAY_SQL = "WITH " + _LEADS_CTE + """
    SELECT day, SUM(attempts), SUM(talked), SUM(talk_sec), SUM(signed), SUM(successes)
    FROM (
        SELECT (requested_at AT TIME ZONE 'Asia/Almaty')::date AS day,
               COUNT(*) FILTER (WHERE NOT cancelled) AS attempts,
               COUNT(*) FILTER (WHERE talked) AS talked,
               COALESCE(SUM(talk_sec), 0) AS talk_sec,
               0 AS signed, 0 AS successes
        FROM att GROUP BY 1
        UNION ALL
        SELECT (signed_at AT TIME ZONE 'Asia/Almaty')::date, 0, 0, 0,
               COUNT(*), COUNT(*) FILTER (WHERE success_attempt_id IS NOT NULL)
        FROM leads WHERE signed_at IS NOT NULL GROUP BY 1
    ) x
    GROUP BY day ORDER BY day
"""

_OPERATORS_SQL = "WITH " + _LEADS_CTE + """,
    calls AS (
        SELECT operator_id,
               COUNT(*) FILTER (WHERE NOT cancelled) AS attempts,
               COUNT(DISTINCT lead_id) FILTER (WHERE NOT cancelled) AS leads_called,
               COUNT(*) FILTER (WHERE talked) AS talked_calls,
               COUNT(DISTINCT lead_id) FILTER (WHERE talked) AS leads_talked,
               COALESCE(SUM(talk_sec), 0) AS talk_sec
        FROM att GROUP BY operator_id
    ),
    suc AS (
        SELECT success_operator_id AS operator_id, COUNT(*) AS successes
        FROM leads WHERE success_attempt_id IS NOT NULL GROUP BY success_operator_id
    )
    SELECT COALESCE(c.operator_id, s.operator_id), COALESCE(u.name, ''),
           COALESCE(c.attempts, 0), COALESCE(c.leads_called, 0), COALESCE(c.talked_calls, 0),
           COALESCE(c.leads_talked, 0), COALESCE(c.talk_sec, 0), COALESCE(s.successes, 0)
    FROM calls c
    FULL JOIN suc s ON s.operator_id = c.operator_id
    LEFT JOIN users u ON u.id = COALESCE(c.operator_id, s.operator_id)
"""


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


def _month_end(period):
    return signing.shift_month(period, 1) - timedelta(days=1)


def department_analytics(cur, department_id, period, today=None, configured=None):
    """Дашборд базы месяца `period` (date первого дня). `today` — дата по Алматы,
    `configured` — есть ли доступ к Sapar (по умолчанию — по окружению)."""
    today = today or datetime.now(signing.PERIOD_TZ).date()
    params = {"department_id": int(department_id), "period": period, "answered": ANSWERED_SQL,
              "min_sec": signing.SUCCESS_MIN_BILLSEC}

    cur.execute(_TOTALS_SQL, params)
    r = cur.fetchone() or (0,) * len(_TOTAL_KEYS) + (None, None, 0, 0)
    totals = {key: int(r[i] or 0) for i, key in enumerate(_TOTAL_KEYS)}
    totals["without_iin"] = totals["leads"] - totals["with_iin"]
    last_checked, oldest_check = r[len(_TOTAL_KEYS)], r[len(_TOTAL_KEYS) + 1]
    sign_errors, never_checked = int(r[len(_TOTAL_KEYS) + 2] or 0), int(r[len(_TOTAL_KEYS) + 3] or 0)

    cur.execute(_BY_DAY_SQL, params)
    by_day_rows = {row[0]: row for row in cur.fetchall()}
    # Ось — дни месяца базы до сегодня; дни после конца месяца добавляются, только
    # если в них что-то было (подписи догоняют базу в первые дни следующего месяца).
    # Что раньше начала месяца (подписал ещё до базы), в график не идёт — оно в итогах.
    start, end = period, min(_month_end(period), max(today, period))
    tail = [d for d in by_day_rows if d > end]
    if tail:
        end = max(tail)
    days = []
    day = start
    while day <= end:
        row = by_day_rows.get(day)
        days.append({
            "day": day.isoformat(),
            "attempts": int(row[1] or 0) if row else 0,
            "talked": int(row[2] or 0) if row else 0,
            "talk_sec": int(row[3] or 0) if row else 0,
            "signed": int(row[4] or 0) if row else 0,
            "successes": int(row[5] or 0) if row else 0,
        })
        day += timedelta(days=1)

    cur.execute(_OPERATORS_SQL, params)
    operators = []
    for row in cur.fetchall():
        if row[0] is None:
            continue
        leads_talked, successes = int(row[5] or 0), int(row[7] or 0)
        operators.append({
            "operator_id": int(row[0]), "name": row[1] or f"#{row[0]}",
            "attempts": int(row[2] or 0), "leads_called": int(row[3] or 0),
            "talked_calls": int(row[4] or 0), "leads_talked": leads_talked,
            "talk_sec": int(row[6] or 0), "successes": successes,
            "conversion": _pct(successes, leads_talked),
        })
    operators.sort(key=lambda o: (-o["successes"], -o["leads_talked"], -o["attempts"], o["name"]))

    month, year = signing.doc_month_for(period)
    in_window = signing.check_window(today)
    checked_now = in_window[0] <= period <= in_window[1]
    next_check = None
    if checked_now and totals["with_iin"] - totals["signed"] > 0:
        # Следующая проверка — не раньше, чем «старейшей» строке исполнится 3 часа;
        # никогда не проверенные уйдут в ближайший получасовой прогон.
        if never_checked or oldest_check is None:
            next_check = None
        else:
            next_check = (oldest_check + timedelta(hours=signing.RECHECK_HOURS)).isoformat()
    return {
        "totals": totals,
        "rates": {
            "called": _pct(totals["called"], totals["leads"]),
            "talked": _pct(totals["talked"], totals["called"]),
            "signed": _pct(totals["signed"], totals["with_iin"]),
            "conversion": _pct(totals["successes"], totals["talked"]),
        },
        "by_day": days,
        "operators": operators,
        "sign_check": {
            "configured": bool(sapar.configured() if configured is None else configured),
            # Проверяется ли база этого месяца сейчас: текущий и прошлый месяцы.
            "active": checked_now,
            "doc_month": date(year, month, 1).isoformat(),
            "last_checked_at": last_checked.isoformat() if last_checked else None,
            "next_check_at": next_check,
            "never_checked": never_checked,
            "errors": sign_errors,
            "recheck_hours": signing.RECHECK_HOURS,
            "min_billsec": signing.SUCCESS_MIN_BILLSEC,
        },
    }
