"""Daily call forecasts for the contact-centre departments and the staffing they need.

Per department the method that won the rolling backtest (forecast made from data known on
the Thursday before each week, compared with the actual week):
  * СЗоВ   — TimesFM on the series with the day-of-month cycle taken out, cycle put back
             (10.4 % weekly error vs 14.3 % for the old «-21/-14 days» rule, 61 weeks);
  * ОП     — TimesFM as is (16.0 % vs 23.4 %, 18 weeks; the calendar cycle is too noisy on
             a short history and made things worse);
  * Тез КЦ — TimesFM as is (6.7 % vs 11.3 %, only 7 weeks — preliminary).
TimesFM runs in BigQuery (timesfm_bq); when it is unreachable the calendar model is the
fallback, and the run says so.

Staffing is Erlang A against the owner's targets (SL over the day, AR band) with the
handle time and caller patience measured on the department's own recent calls.

Numbers of the test-number registry stay out of the volumes and handle times, as out of
every other calculation of the portal.

Tables are created here (ensure_schema_db, a transaction of its own), so the shared
database.py does not change. Request paths only read the catalog (schema_ready) and keep
the old rule until the tables are there.
"""
import json
import logging
import math
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from . import calendar_model, erlang_a, timesfm_bq

try:  # the registry ships on its own; until it is deployed nothing is excluded
    from test_numbers import keys as _test_keys
except ImportError:  # pragma: no cover - depends on the deployment
    _test_keys = None

HORIZON_DAYS = 42
HISTORY_DAYS = 800
CONFIDENCE = 0.8
PARAMS_WINDOW_DAYS = 28
LONG_HISTORY_DAYS = 400
OKTELL_PAUSE_SECONDS = 30
PROFILE_WEEKS = 8
SCHEMA_RETRY_SECONDS = 600
# A night BigQuery failed: the TimesFM forecast made a night or two before is still better
# than the calendar fallback made today (backtest: 10 % vs 14 % weekly error).
TIMESFM_STALE_DAYS = 2
STAFFING_CACHE_SIZE = 4096

DEPARTMENTS = {
    "szov": {"label": "СЗоВ", "method": "timesfm_dom"},
    "op": {"label": "ОП", "method": "timesfm"},
    "tez": {"label": "Тез КЦ", "method": "timesfm"},
}
METHOD_LABELS = {
    "timesfm_dom": "TimesFM + цикл месяца",
    "timesfm": "TimesFM",
    "calendar": "Календарная модель (запасной вариант)",
}
# What a run measures about the calls; the rest of a run's params is about the forecast.
PARAM_KEYS = ("aht_seconds", "talk_seconds", "after_call_seconds", "patience_seconds",
              "answered", "lost", "window", "source")

ENGINE_TABLES = ("resource_daily_volume", "resource_daily_forecasts",
                 "resource_forecast_runs", "resource_forecast_adjustments")

_schema_lock = threading.Lock()
_schema_state = {"done": False, "failed_at": None}
_run_state_lock = threading.Lock()
_run_state = {"running": False, "current": None, "pending": {}}
_staffing_lock = threading.Lock()
_staffing_cache: "OrderedDict[tuple, Dict[str, object]]" = OrderedDict()

SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS resource_daily_volume (
        department VARCHAR(16) NOT NULL,
        day DATE NOT NULL,
        offered INTEGER NOT NULL,
        answered INTEGER,
        source VARCHAR(32) NOT NULL DEFAULT '',
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (department, day)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS resource_daily_forecasts (
        department VARCHAR(16) NOT NULL,
        made_on DATE NOT NULL,
        forecast_date DATE NOT NULL,
        method VARCHAR(32) NOT NULL,
        calls DOUBLE PRECISION NOT NULL,
        calls_low DOUBLE PRECISION,
        calls_high DOUBLE PRECISION,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (department, made_on, forecast_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_resource_daily_forecasts_lookup "
    "ON resource_daily_forecasts(department, forecast_date, made_on DESC)",
    """
    CREATE TABLE IF NOT EXISTS resource_forecast_runs (
        id SERIAL PRIMARY KEY,
        department VARCHAR(16) NOT NULL,
        made_on DATE NOT NULL,
        method VARCHAR(32) NOT NULL DEFAULT '',
        status VARCHAR(16) NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        params JSONB NOT NULL DEFAULT '{}'::jsonb,
        triggered_by VARCHAR(32) NOT NULL DEFAULT '',
        started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_resource_forecast_runs_dept ON resource_forecast_runs(department, id DESC)",
    """
    CREATE TABLE IF NOT EXISTS resource_forecast_adjustments (
        id SERIAL PRIMARY KEY,
        department VARCHAR(16) NOT NULL,
        date_from DATE NOT NULL,
        date_to DATE NOT NULL,
        kind VARCHAR(16) NOT NULL CHECK (kind IN ('exclude', 'uplift')),
        percent DOUBLE PRECISION,
        note TEXT NOT NULL DEFAULT '',
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
)

# Targets and the engine switch for the line. Defaults are the owner's targets
# (09.10.2026): SL 80 % within 20 s over the day, AR between 3 and 5 %.
SETTINGS_COLUMNS = (
    ("forecast_engine", "VARCHAR(16) NOT NULL DEFAULT 'timesfm'"),
    ("sl_target", "NUMERIC(6,4) NOT NULL DEFAULT 0.80"),
    ("sl_seconds", "INTEGER NOT NULL DEFAULT 20"),
    ("ar_min", "NUMERIC(6,4) NOT NULL DEFAULT 0.03"),
    ("ar_max", "NUMERIC(6,4) NOT NULL DEFAULT 0.05"),
    ("hour_sl_floor", "NUMERIC(6,4) NOT NULL DEFAULT 0.60"),
    ("max_occupancy", "NUMERIC(6,4) NOT NULL DEFAULT 0.85"),
)

ENGINE_SETTING_DEFAULTS = {
    "forecast_engine": "timesfm",
    "sl_target": 0.80,
    "sl_seconds": 20,
    "ar_min": 0.03,
    "ar_max": 0.05,
    "hour_sl_floor": 0.60,
    "max_occupancy": 0.85,
}
ENGINE_SETTING_LIMITS = {
    "sl_target": (0.5, 0.99),
    "sl_seconds": (5, 300),
    "ar_min": (0.0, 0.5),
    "ar_max": (0.005, 0.5),
    "hour_sl_floor": (0.0, 0.95),
    "max_occupancy": (0.5, 0.98),
}
FORECAST_ENGINES = ("timesfm", "legacy")


# ── schema ───────────────────────────────────────────────────────────────────

def _settings_columns_present(cursor) -> set:
    cursor.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'resource_settings' AND table_schema = current_schema()"
    )
    return {row[0] for row in cursor.fetchall()}


def ensure_schema_db(db) -> bool:
    """Create the engine's tables and settings columns, in a transaction of their own.

    Never inside a caller's transaction: a rollback of the caller's work would take the
    DDL with it while the process went on believing the tables exist. The flag is set only
    after this transaction has committed. ALTER runs only for columns that are really
    missing (a warm start takes no lock on resource_settings), and a short lock_timeout
    keeps a busy table from queueing every request behind the ALTER. A failure is retried
    at most every SCHEMA_RETRY_SECONDS; until then the line keeps the old rule.
    """
    if _schema_state["done"]:
        return True
    with _schema_lock:
        if _schema_state["done"]:
            return True
        failed_at = _schema_state["failed_at"]
        if failed_at is not None and time.monotonic() - failed_at < SCHEMA_RETRY_SECONDS:
            return False
        try:
            with db._get_cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                for statement in SCHEMA_SQL:
                    cursor.execute(statement)
                present = _settings_columns_present(cursor)
                for column, ddl in SETTINGS_COLUMNS:
                    if column not in present:
                        cursor.execute(f"ALTER TABLE resource_settings ADD COLUMN IF NOT EXISTS {column} {ddl}")
                # A run cut off by a restart would stay «running» for ever and keep the
                # screen's button busy. A real run takes minutes, not half an hour.
                cursor.execute(
                    "UPDATE resource_forecast_runs SET status = 'failed', finished_at = CURRENT_TIMESTAMP, "
                    "detail = 'Прогон прервался: сервер перезапускался' "
                    "WHERE status = 'running' AND started_at < CURRENT_TIMESTAMP - INTERVAL '30 minutes'"
                )
        except Exception:  # noqa: BLE001
            logging.exception("forecast engine schema could not be created")
            _schema_state["failed_at"] = time.monotonic()
            return False
        _schema_state["done"] = True
        _schema_state["failed_at"] = None
        return True


def schema_ready(cursor) -> bool:
    """Whether the tables and the settings columns exist — a catalog read, never DDL, so any
    request transaction (a read-only one too) may ask. True is remembered: only
    ensure_schema_db creates them, in its own committed transaction."""
    if _schema_state["done"]:
        return True
    cursor.execute("SELECT " + " AND ".join(f"to_regclass('{table}') IS NOT NULL" for table in ENGINE_TABLES))
    row = cursor.fetchone()
    if not (row and row[0]):
        return False
    present = _settings_columns_present(cursor)
    if not all(column in present for column, _ in SETTINGS_COLUMNS):
        return False
    _schema_state["done"] = True
    return True


def settings_columns_ready(cursor) -> bool:
    """Whether resource_settings has the engine columns, so the settings read may select them."""
    if schema_ready(cursor):
        return True
    present = _settings_columns_present(cursor)
    return all(column in present for column, _ in SETTINGS_COLUMNS)


def engine_settings(settings: Optional[Dict[str, object]]) -> Dict[str, object]:
    """Engine fields of resource_settings with defaults and sane bounds."""
    settings = settings or {}
    out = dict(ENGINE_SETTING_DEFAULTS)
    engine = str(settings.get("forecast_engine") or "").strip().lower()
    out["forecast_engine"] = engine if engine in FORECAST_ENGINES else ENGINE_SETTING_DEFAULTS["forecast_engine"]
    for key, (low, high) in ENGINE_SETTING_LIMITS.items():
        try:
            value = float(settings.get(key, out[key]))
        except (TypeError, ValueError):
            value = float(out[key])
        if not math.isfinite(value):
            value = float(out[key])
        out[key] = min(high, max(low, value))
    out["sl_seconds"] = int(round(out["sl_seconds"]))
    if out["ar_min"] > out["ar_max"]:
        out["ar_min"] = out["ar_max"]
    return out


# ── test-number registry ─────────────────────────────────────────────────────

def _registry_ready(cursor) -> bool:
    if _test_keys is None:
        return False
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (_test_keys.TABLE,))
    row = cursor.fetchone()
    return bool(row and row[0])


def _not_test_sql(cursor, phone_expr: str) -> str:
    """' AND <the call's number is not in the registry>' for our database; `phone_expr` is a
    column of digits. Empty while the registry is not deployed."""
    if not _registry_ready(cursor):
        return ""
    return " AND " + _test_keys.sql_not_test(phone_expr, digits=True)


def _oktell_not_test_sql() -> str:
    """The same rule for the Oktell queries, keys as literals like the hourly sync has them."""
    if _test_keys is None:
        return ""
    return _test_keys.tsql_and_not_test("t.[number]")


# ── daily volumes ────────────────────────────────────────────────────────────

def volume_sql(department: str, not_test: str = "") -> str:
    """Daily offered/answered calls of a department from its own tables, closed days only."""
    if department == "szov":
        # Calls that reached distribution, greeting drops excluded — the definition of
        # daily_resource_hours.received_calls and of the line's AR/SL. The hourly Oktell
        # sync that fills the table already leaves registry numbers out.
        return """
            SELECT h.report_date, SUM(h.received_calls), SUM(h.accepted_calls)
              FROM daily_resource_hours h
              JOIN daily_resource_summary s ON s.report_date = h.report_date
             WHERE h.report_date BETWEEN %s AND %s
             GROUP BY h.report_date
        """
    if department == "op":
        # Inbound calls that reached an ОП queue (answered or not), like the ОП wallboard.
        # Only days the bridge read after they had ended: a day read in the evening holds
        # part of its calls, and TimesFM leans on the newest points.
        return f"""
            SELECT t.call_day,
                   COUNT(*) FILTER (WHERE t.call_type IN ('Входящий', 'Входящий (не приняли)') AND t.queue <> ''),
                   COUNT(*) FILTER (WHERE t.call_type = 'Входящий' AND t.queue <> '')
              FROM cdr_touches t
              JOIN cdr_sync_days d ON d.day = t.call_day AND d.status = 'done' AND d.complete
             WHERE t.call_day BETWEEN %s AND %s{not_test}
             GROUP BY t.call_day
        """
    if department == "tez":
        # Inbound calls of the Тез cabinet. Only days the Binotel mirror copied again after
        # they had ended: its first pass marks a day as copied while the day still runs.
        return f"""
            SELECT (c.started_at AT TIME ZONE 'Asia/Almaty')::date AS day,
                   COUNT(*) FILTER (WHERE c.call_type = 0),
                   COUNT(*) FILTER (WHERE c.call_type = 0 AND c.billsec > 0)
              FROM tez_lead_calls c
             WHERE (c.started_at AT TIME ZONE 'Asia/Almaty')::date BETWEEN %s AND %s
               AND (c.started_at AT TIME ZONE 'Asia/Almaty')::date IN (
                    SELECT s.day FROM tez_call_sync_days s
                     WHERE s.synced_at >= ((s.day + 1)::timestamp AT TIME ZONE 'Asia/Almaty')){not_test}
             GROUP BY 1
        """
    raise ValueError(department)


_VOLUME_PHONE = {"op": "t.phone", "tez": "c.phone_norm"}


def refresh_volume(cursor, department: str, date_from: date, date_to: date) -> int:
    """Re-aggregate a department's daily volume from its source tables into resource_daily_volume."""
    phone = _VOLUME_PHONE.get(department)
    cursor.execute(volume_sql(department, _not_test_sql(cursor, phone) if phone else ""), (date_from, date_to))
    rows = [(r[0], int(r[1] or 0), int(r[2] or 0)) for r in cursor.fetchall() if r[0] is not None]
    for day, offered, answered in rows:
        if offered <= 0:
            continue
        cursor.execute(
            """
            INSERT INTO resource_daily_volume (department, day, offered, answered, source, updated_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (department, day) DO UPDATE
               SET offered = EXCLUDED.offered, answered = EXCLUDED.answered,
                   source = EXCLUDED.source, updated_at = CURRENT_TIMESTAMP
            """,
            (department, day, offered, answered, "portal"),
        )
    return len(rows)


def oktell_daily_sql(date_from_compact: str, date_to_excl_compact: str, not_test: str = "") -> str:
    """Daily totals of the line straight from Oktell, same filters as the hourly sync."""
    greeting = "Бросили трубку на приветствии"
    failed = "Неудачный звонок"
    return (
        "SELECT CONVERT(varchar(10), t.dt_insert, 23) AS report_date, "
        f"SUM(CASE WHEN t.result_call <> N'{greeting}' AND t.call_result IN (13,19,5) THEN 1 ELSE 0 END) AS received, "
        f"SUM(CASE WHEN t.result_call <> N'{greeting}' AND t.call_result = 5 THEN 1 ELSE 0 END) AS accepted "
        "FROM oktell.dbo.Call_Systems_hst t "
        f"WHERE t.dt_insert >= '{date_from_compact}' AND t.dt_insert < '{date_to_excl_compact}' "
        f"AND t.taxi_park <> '' AND t.route = 'incoming' AND t.result_call <> N'{failed}' {not_test}"
        "GROUP BY CONVERT(varchar(10), t.dt_insert, 23)"
    )


def fetch_szov_history_from_oktell(oktell_query: Callable[[str], List[dict]], history_start: date,
                                   first_known: date, sleep: Callable[[float], None] = time.sleep) -> List[Tuple[date, int, int]]:
    """One-off: the line's long history (daily_resource_hours starts in March 2026) from Oktell.

    At most a handful of requests, each a whole window aggregated by day (under the proxy's
    1000-row cap) with a pause between them: a burst of small requests once locked the
    proxy out of SQL Server for minutes, a few aggregated ones did not. Runs outside any
    database transaction — the pauses would otherwise hold a connection open.
    """
    out: List[Tuple[date, int, int]] = []
    window_start = history_start
    first = True
    not_test = _oktell_not_test_sql()
    while window_start < first_known:
        window_end = min(first_known, window_start + timedelta(days=150))
        if not first:
            sleep(OKTELL_PAUSE_SECONDS)
        first = False
        rows = oktell_query(oktell_daily_sql(window_start.strftime("%Y%m%d"), window_end.strftime("%Y%m%d"), not_test)) or []
        for row in rows:
            try:
                day = date.fromisoformat(str(row.get("report_date"))[:10])
                received = int(row.get("received") or 0)
                accepted = int(row.get("accepted") or 0)
            except (TypeError, ValueError):
                continue
            if received > 0 and day < first_known:
                out.append((day, received, accepted))
        window_start = window_end
    return out


def store_szov_history(cursor, rows: Iterable[Tuple[date, int, int]]) -> int:
    """Days already present (from the portal's own hourly table) are left alone."""
    stored = 0
    for day, received, accepted in rows:
        cursor.execute(
            """
            INSERT INTO resource_daily_volume (department, day, offered, answered, source)
            VALUES ('szov', %s, %s, %s, 'oktell_daily')
            ON CONFLICT (department, day) DO NOTHING
            """,
            (day, received, accepted),
        )
        stored += 1
    return stored


def load_volume(cursor, department: str, date_from: date, date_to: date) -> Dict[date, float]:
    cursor.execute(
        "SELECT day, offered FROM resource_daily_volume WHERE department = %s AND day BETWEEN %s AND %s",
        (department, date_from, date_to),
    )
    return {row[0]: float(row[1]) for row in cursor.fetchall() if row[1] is not None}


# ── handle time and patience ─────────────────────────────────────────────────

def oktell_params_sql(date_from_compact: str, date_to_excl_compact: str, conn_from_compact: str,
                      not_test: str = "") -> str:
    """Answered calls, operator talk from the connection legs (not total_length, which also
    holds IVR and queue time), queue waits of answered and lost calls."""
    greeting = "Бросили трубку на приветствии"
    failed = "Неудачный звонок"
    return (
        "SELECT "
        f"SUM(CASE WHEN t.result_call <> N'{greeting}' AND t.call_result = 5 THEN 1 ELSE 0 END) AS accepted, "
        f"SUM(CASE WHEN t.result_call <> N'{greeting}' AND t.call_result = 5 THEN COALESCE(k.talk_sec, 0) ELSE 0 END) AS talk_sec, "
        f"SUM(CASE WHEN t.result_call <> N'{greeting}' AND t.call_result = 5 AND k.talk_sec IS NULL THEN 1 ELSE 0 END) AS no_chain, "
        f"SUM(CASE WHEN t.result_call <> N'{greeting}' AND t.call_result = 5 THEN COALESCE(t.LenQueue, 0) ELSE 0 END) AS wait_ok_sec, "
        f"SUM(CASE WHEN t.call_result IN (13,19) AND t.result_call NOT IN (N'{greeting}', N'{failed}') THEN 1 ELSE 0 END) AS lost, "
        f"SUM(CASE WHEN t.call_result IN (13,19) AND t.result_call NOT IN (N'{greeting}', N'{failed}') THEN COALESCE(t.LenQueue, 0) ELSE 0 END) AS wait_lost_sec "
        "FROM oktell.dbo.Call_Systems_hst t "
        "LEFT JOIN (SELECT IdChain, SUM(DATEDIFF(second, TimeAnswer, TimeStop)) AS talk_sec "
        "FROM oktell.dbo.A_Stat_Connections_1x1 "
        f"WHERE ConnectionType = 5 AND TimeStart >= '{conn_from_compact}' AND TimeStart < '{date_to_excl_compact}' "
        "GROUP BY IdChain) k ON k.IdChain = TRY_CAST(t.chainid AS uniqueidentifier) "
        f"WHERE t.dt_insert >= '{date_from_compact}' AND t.dt_insert < '{date_to_excl_compact}' "
        f"AND t.taxi_park <> '' AND t.route = 'incoming' AND t.result_call <> N'{failed}' {not_test}"
    ).rstrip()


def oktell_after_call_sql(date_from_compact: str, date_to_excl_compact: str) -> str:
    """After-call work and hold of incoming calls from the operator-state cube."""
    return (
        "SELECT SUM(CASE WHEN s.State = 7 AND s.IsOutput = 0 THEN s.LenTime ELSE 0 END) AS post_sec, "
        "SUM(CASE WHEN s.State = 33 AND s.IsOutput = 0 THEN s.LenTime ELSE 0 END) AS hold_sec "
        "FROM oktell_cc_temp.dbo.A_Cube_CC_OperatorStates s "
        f"WHERE s.DateTimeStart >= '{date_from_compact}' AND s.DateTimeStart < '{date_to_excl_compact}'"
    )


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def measure_szov_params(oktell_query: Callable[[str], List[dict]], made_on: date,
                        sleep: Callable[[float], None] = time.sleep) -> Dict[str, object]:
    date_from = made_on - timedelta(days=PARAMS_WINDOW_DAYS - 1)
    date_to_excl = made_on + timedelta(days=1)
    calls = (oktell_query(oktell_params_sql(date_from.strftime("%Y%m%d"), date_to_excl.strftime("%Y%m%d"),
                                            (date_from - timedelta(days=1)).strftime("%Y%m%d"),
                                            _oktell_not_test_sql())) or [{}])[0]
    sleep(OKTELL_PAUSE_SECONDS)
    after = (oktell_query(oktell_after_call_sql(date_from.strftime("%Y%m%d"), date_to_excl.strftime("%Y%m%d"))) or [{}])[0]
    accepted = _num(calls.get("accepted"))
    if accepted <= 0:
        raise ValueError("no answered calls in the window")
    talk, post, hold = _num(calls.get("talk_sec")), _num(after.get("post_sec")), _num(after.get("hold_sec"))
    aht = (talk + post + hold) / accepted
    patience = erlang_a.estimate_patience(_num(calls.get("lost")), _num(calls.get("wait_ok_sec")), _num(calls.get("wait_lost_sec")))
    return {
        "aht_seconds": round(aht, 1),
        "talk_seconds": round(talk / accepted, 1),
        "after_call_seconds": round((post + hold) / accepted, 1),
        "patience_seconds": round(patience, 1),
        "answered": int(accepted),
        "lost": int(_num(calls.get("lost"))),
        "window": [date_from.isoformat(), made_on.isoformat()],
        "source": "Oktell: плечи оператора + постобработка + удержание",
    }


def measure_params_from_portal(cursor, department: str, made_on: date) -> Dict[str, object]:
    """ОП (bridge CDR) and Тез (Binotel mirror) keep talk and wait per call in our database.
    Same days and numbers as the volumes: closed days, registry numbers left out."""
    date_from = made_on - timedelta(days=PARAMS_WINDOW_DAYS - 1)
    if department == "op":
        cursor.execute(
            f"""
            SELECT COUNT(*) FILTER (WHERE t.call_type = 'Входящий'),
                   SUM(COALESCE(t.talk_measured_seconds, t.talk_seconds)) FILTER (WHERE t.call_type = 'Входящий'),
                   COUNT(*) FILTER (WHERE t.call_type = 'Входящий (не приняли)'),
                   SUM(t.wait_seconds) FILTER (WHERE t.call_type = 'Входящий' AND t.wait_seconds IS NOT NULL),
                   SUM(t.wait_seconds) FILTER (WHERE t.call_type = 'Входящий (не приняли)' AND t.wait_seconds IS NOT NULL),
                   COUNT(t.wait_seconds) FILTER (WHERE t.call_type = 'Входящий (не приняли)')
              FROM cdr_touches t
              JOIN cdr_sync_days d ON d.day = t.call_day AND d.status = 'done' AND d.complete
             WHERE t.call_day BETWEEN %s AND %s AND t.queue <> ''
               AND t.call_type IN ('Входящий', 'Входящий (не приняли)'){_not_test_sql(cursor, "t.phone")}
            """,
            (date_from, made_on),
        )
        answered, talk, lost, wait_ok, wait_lost, lost_measured = cursor.fetchone()
        source = "CDR АТС через мост: разговор агента и ожидание из журнала очередей"
        lost_for_patience = lost_measured
    elif department == "tez":
        cursor.execute(
            f"""
            SELECT COUNT(*) FILTER (WHERE c.billsec > 0),
                   SUM(c.billsec) FILTER (WHERE c.billsec > 0),
                   COUNT(*) FILTER (WHERE c.billsec <= 0),
                   SUM(c.waitsec) FILTER (WHERE c.billsec > 0),
                   SUM(c.waitsec) FILTER (WHERE c.billsec <= 0)
              FROM tez_lead_calls c
             WHERE c.call_type = 0
               AND (c.started_at AT TIME ZONE 'Asia/Almaty')::date BETWEEN %s AND %s
               AND (c.started_at AT TIME ZONE 'Asia/Almaty')::date IN (
                    SELECT s.day FROM tez_call_sync_days s
                     WHERE s.synced_at >= ((s.day + 1)::timestamp AT TIME ZONE 'Asia/Almaty')){_not_test_sql(cursor, "c.phone_norm")}
            """,
            (date_from, made_on),
        )
        answered, talk, lost, wait_ok, wait_lost = cursor.fetchone()
        lost_for_patience = lost
        source = "Журнал Binotel: разговор и ожидание в очереди"
    else:
        raise ValueError(department)
    answered = _num(answered)
    if answered <= 0:
        raise ValueError("no answered calls in the window")
    aht = _num(talk) / answered
    patience = erlang_a.estimate_patience(_num(lost_for_patience), _num(wait_ok), _num(wait_lost))
    return {
        "aht_seconds": round(aht, 1),
        "talk_seconds": round(aht, 1),
        "after_call_seconds": 0.0,
        "patience_seconds": round(patience, 1),
        "answered": int(answered),
        "lost": int(_num(lost)),
        "window": [date_from.isoformat(), made_on.isoformat()],
        "source": source,
    }


# ── adjustments ──────────────────────────────────────────────────────────────

def list_adjustments(cursor, department: str, date_from: Optional[date] = None, date_to: Optional[date] = None) -> List[dict]:
    params: List[object] = [department]
    where = "department = %s"
    if date_from is not None:
        where += " AND date_to >= %s"
        params.append(date_from)
    if date_to is not None:
        where += " AND date_from <= %s"
        params.append(date_to)
    cursor.execute(
        f"""
        SELECT a.id, a.date_from, a.date_to, a.kind, a.percent, a.note, a.created_by, a.created_at
          FROM resource_forecast_adjustments a
         WHERE {where}
         ORDER BY a.date_from, a.id
        """,
        params,
    )
    return [
        {"id": r[0], "date_from": r[1].isoformat(), "date_to": r[2].isoformat(), "kind": r[3],
         "percent": float(r[4]) if r[4] is not None else None, "note": r[5] or "",
         "created_by": r[6], "created_at": r[7].isoformat() if r[7] else None}
        for r in cursor.fetchall()
    ]


def add_adjustment(cursor, department: str, payload: Dict[str, object], user_id: Optional[int],
                   today: Optional[date] = None) -> dict:
    if department not in DEPARTMENTS:
        raise ValueError("Неизвестный отдел")
    kind = str(payload.get("kind") or "").strip()
    if kind not in ("exclude", "uplift"):
        raise ValueError("Вид поправки: exclude или uplift")
    try:
        date_from = date.fromisoformat(str(payload.get("date_from"))[:10])
        date_to = date.fromisoformat(str(payload.get("date_to") or payload.get("date_from"))[:10])
    except ValueError:
        raise ValueError("Некорректные даты поправки")
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    if (date_to - date_from).days > 92:
        raise ValueError("Поправка не длиннее 93 дней")
    percent = None
    if kind == "uplift":
        try:
            percent = float(payload.get("percent"))
        except (TypeError, ValueError):
            raise ValueError("Укажите процент поправки")
        if not math.isfinite(percent) or not -90 <= percent <= 300:
            raise ValueError("Поправка от −90 до +300 %")
        # The past is counted already: an event there would only rewrite yesterday's plan
        # and, hidden from the list, could not even be removed.
        if date_to < (today or datetime.now().date()):
            raise ValueError("Событие ставится на сегодня и следующие дни")
    note = str(payload.get("note") or "").strip()[:300]
    cursor.execute(
        """
        INSERT INTO resource_forecast_adjustments (department, date_from, date_to, kind, percent, note, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (department, date_from, date_to, kind, percent, note, user_id),
    )
    new_id = cursor.fetchone()[0]
    return {"id": new_id, "department": department, "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(), "kind": kind, "percent": percent, "note": note}


def delete_adjustment(cursor, adjustment_id: int) -> Optional[dict]:
    """Deletes and returns {department, kind} of the removed adjustment (None if absent)."""
    cursor.execute(
        "DELETE FROM resource_forecast_adjustments WHERE id = %s RETURNING department, kind",
        (int(adjustment_id),),
    )
    row = cursor.fetchone()
    return {"department": row[0], "kind": row[1]} if row else None


def _excluded_days(adjustments: Iterable[dict]) -> List[date]:
    days: List[date] = []
    for adj in adjustments:
        if adj["kind"] != "exclude":
            continue
        current = date.fromisoformat(adj["date_from"])
        end = date.fromisoformat(adj["date_to"])
        while current <= end:
            days.append(current)
            current += timedelta(days=1)
    return days


def uplift_factor(adjustments: Iterable[dict], day: date) -> Tuple[float, List[str]]:
    factor, notes = 1.0, []
    iso = day.isoformat()
    for adj in adjustments:
        if adj["kind"] == "uplift" and adj["date_from"] <= iso <= adj["date_to"] and adj["percent"] is not None:
            factor *= 1.0 + float(adj["percent"]) / 100.0
            notes.append(adj.get("note") or "")
    return factor, notes


# ── the run ──────────────────────────────────────────────────────────────────

def _residual_sd(series: Dict[date, float], factors: calendar_model.Factors, days: int = 90) -> float:
    tail = sorted(d for d, v in series.items() if v and v > 0)[-days:]
    if len(tail) < 14:
        return 0.15
    level = float(np.median([series[d] / factors.season(d) for d in tail[-28:]]))
    resid = [math.log(series[d] / (level * factors.season(d))) for d in tail]
    return float(np.std(resid)) or 0.15


def forecast_department(series: Dict[date, float], department: str, made_on: date,
                        excluded: Iterable[date] = (), horizon: int = HORIZON_DAYS,
                        forecaster: Callable = timesfm_bq.forecast) -> Dict[str, object]:
    """Pure part of a run: series up to `made_on` -> {forecast_date: (calls, low, high)} + method."""
    method = DEPARTMENTS[department]["method"]
    excluded = sorted(set(excluded))
    clean = {d: v for d, v in series.items() if d <= made_on and d not in set(excluded)}
    known = sorted(d for d, v in clean.items() if v and v > 0)
    if len(known) < 28:
        raise ValueError("Слишком короткая история: меньше 28 дней")
    factors = calendar_model.fit_factors({d: clean[d] for d in known if d > made_on - timedelta(days=365)})
    start = max(known[0], made_on - timedelta(days=HISTORY_DAYS - 1))
    days, values, filled = calendar_model.fill_gaps(clean, start, made_on, factors, exclude=excluded)
    targets = [made_on + timedelta(days=i) for i in range(1, horizon + 1)]
    out: Dict[date, Tuple[float, float, float]] = {}
    detail = ""
    try:
        if method == "timesfm_dom":
            seasonal = [factors.season(d, with_weekday=False) for d in days]
            inputs = [v / s for v, s in zip(values, seasonal)]
        else:
            inputs = values
        result = forecaster({department: list(zip(days, inputs))}, horizon=horizon, confidence=CONFIDENCE)
        points = result.get(department) or {}
        for day in targets:
            if day not in points:
                raise timesfm_bq.TimesFMError(f"no forecast for {day}")
            value, low, high = points[day]
            scale = factors.season(day, with_weekday=False) if method == "timesfm_dom" else 1.0
            out[day] = (max(0.0, value * scale), max(0.0, low * scale), max(0.0, high * scale))
    except Exception as exc:  # noqa: BLE001 — any failure of the model means the fallback
        logging.warning("TimesFM forecast failed for %s, calendar fallback: %s", department, exc)
        detail = f"TimesFM недоступен ({str(exc)[:160]}), прогноз по календарной модели"
        method = "calendar"
        sd = _residual_sd(clean, factors)
        base = calendar_model.calendar_forecast(clean, targets, factors)
        z = 1.2816  # 80 % two-sided
        out = {d: (v, v * math.exp(-z * sd), v * math.exp(z * sd)) for d, v in base.items()}
    return {
        "method": method,
        "points": out,
        "factors": factors.as_dict(),
        "history_days": len(days),
        "history_from": days[0].isoformat() if days else None,
        "filled_days": len(filled),
        "detail": detail,
    }


def _first_volume_day(cursor, department: str) -> Optional[date]:
    cursor.execute("SELECT MIN(day) FROM resource_daily_volume WHERE department = %s", (department,))
    row = cursor.fetchone()
    return row[0] if row else None


def _szov_backfill_done(cursor) -> bool:
    cursor.execute(
        "SELECT 1 FROM resource_forecast_runs WHERE department = 'szov' AND params ? 'oktell_backfill' "
        "AND NOT (params -> 'oktell_backfill') ? 'error' LIMIT 1"
    )
    return cursor.fetchone() is not None


def _finish_run(db, run_id: int, status: str, method: str, detail: str, params: Optional[dict] = None) -> None:
    with db._get_cursor() as cursor:
        cursor.execute(
            """
            UPDATE resource_forecast_runs
               SET status = %s, method = %s, detail = %s,
                   params = COALESCE(%s::jsonb, params), finished_at = CURRENT_TIMESTAMP
             WHERE id = %s
            """,
            (status, method, detail[:500], json.dumps(params, ensure_ascii=False) if params is not None else None, run_id),
        )


def run_department(db, department: str, today: Optional[date] = None, oktell_query=None,
                   triggered_by: str = "scheduler", sleep: Callable[[float], None] = time.sleep,
                   forecaster: Callable = timesfm_bq.forecast) -> Dict[str, object]:
    """Refresh volumes, forecast HORIZON_DAYS ahead, measure handle time and patience, store."""
    if department not in DEPARTMENTS:
        raise ValueError("unknown department")
    today = today or datetime.now().date()
    made_on = today - timedelta(days=1)
    if not ensure_schema_db(db):
        return {"department": department, "status": "failed", "error": "Таблицы прогноза не созданы"}
    try:
        with db._get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO resource_forecast_runs (department, made_on, status, triggered_by) VALUES (%s, %s, 'running', %s) RETURNING id",
                (department, made_on, triggered_by),
            )
            run_id = cursor.fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        logging.exception("resource forecast run could not start for %s", department)
        return {"department": department, "status": "failed", "error": str(exc)[:500]}
    notes: List[str] = []
    extra: Dict[str, object] = {}
    try:
        backfill_from = None
        with db._get_cursor() as cursor:
            refresh_volume(cursor, department, made_on - timedelta(days=60), made_on)
            wanted_start = made_on - timedelta(days=HISTORY_DAYS - 1)
            first_known = _first_volume_day(cursor, department)
            if first_known is None or (first_known - wanted_start).days > HISTORY_DAYS - LONG_HISTORY_DAYS:
                # A short stored history: the first run, or a source filled back later (the
                # bridge holds ОП from March, the Binotel mirror Тез from June). Take every day
                # the source tables have, not only the last 60 — the backtest gave TimesFM all
                # of them. Portal days first, so the long Oktell history only fills the rest.
                refresh_volume(cursor, department, wanted_start, made_on)
                first_known = _first_volume_day(cursor, department) or made_on
                if (department == "szov" and oktell_query is not None
                        and (first_known - wanted_start).days > 30 and not _szov_backfill_done(cursor)):
                    backfill_from = (wanted_start, first_known)
        if backfill_from is not None:
            # Optional: the portal's own days are enough to forecast, so an Oktell failure
            # here costs a note in the run, not the night's forecast. Tried again next night
            # until it succeeds once.
            try:
                history = fetch_szov_history_from_oktell(oktell_query, backfill_from[0], backfill_from[1], sleep=sleep)
                with db._get_cursor() as cursor:
                    stored = store_szov_history(cursor, history)
                extra["oktell_backfill"] = {"from": backfill_from[0].isoformat(), "to": backfill_from[1].isoformat(), "days": stored}
            except Exception as exc:  # noqa: BLE001
                logging.warning("СЗоВ history backfill from Oktell failed: %s", exc)
                extra["oktell_backfill"] = {"error": str(exc)[:200]}
                notes.append("длинная история из Oktell не загрузилась — прогноз по истории портала")
        with db._get_cursor() as cursor:
            series = load_volume(cursor, department, made_on - timedelta(days=HISTORY_DAYS + 30), made_on)
            adjustments = list_adjustments(cursor, department)
            previous = latest_params(cursor, department) if department == "szov" else None
            cursor.execute(
                "SELECT 1 FROM resource_daily_forecasts WHERE department = %s AND made_on = %s AND method <> 'calendar' LIMIT 1",
                (department, made_on),
            )
            has_model_forecast = cursor.fetchone() is not None
        result = forecast_department(series, department, made_on, _excluded_days(adjustments), forecaster=forecaster)
        if result["method"] == "calendar" and has_model_forecast:
            # A rerun of a day that already has a TimesFM forecast: a passing BigQuery
            # failure must not overwrite it with the fallback.
            detail = "TimesFM недоступен — оставлен прогноз TimesFM, сделанный раньше по тем же данным"
            _finish_run(db, run_id, "failed", "calendar", detail)
            return {"department": department, "status": "failed", "error": detail}
        if previous and previous.get("made_on") == made_on.isoformat():
            # A rerun of the same morning (an exclusion, the button): the handle time and
            # patience measured on these very days are still current — Oktell is not asked again.
            params = {key: previous[key] for key in PARAM_KEYS if key in previous}
            params["reused_from_run_of"] = made_on.isoformat()
        else:
            try:
                if department == "szov":
                    if oktell_query is None:
                        raise ValueError("Oktell is not configured")
                    params = measure_szov_params(oktell_query, made_on, sleep=sleep)
                else:
                    with db._get_cursor() as cursor:
                        params = measure_params_from_portal(cursor, department, made_on)
            except Exception as exc:  # noqa: BLE001
                params = {"error": f"Не удалось измерить AHT и терпение: {str(exc)[:160]}"}
        run_params = {**params, **extra, "factors": result["factors"], "history_days": result["history_days"],
                      "history_from": result["history_from"], "filled_days": result["filled_days"]}
        detail = "; ".join(part for part in [result["detail"], *notes] if part)
        with db._get_cursor() as cursor:
            for day, (calls, low, high) in sorted(result["points"].items()):
                cursor.execute(
                    """
                    INSERT INTO resource_daily_forecasts (department, made_on, forecast_date, method, calls, calls_low, calls_high)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (department, made_on, forecast_date) DO UPDATE
                       SET method = EXCLUDED.method, calls = EXCLUDED.calls, calls_low = EXCLUDED.calls_low,
                           calls_high = EXCLUDED.calls_high, created_at = CURRENT_TIMESTAMP
                    """,
                    (department, made_on, day, result["method"], round(calls, 3), round(low, 3), round(high, 3)),
                )
            cursor.execute(
                """
                UPDATE resource_forecast_runs
                   SET status = 'success', method = %s, detail = %s, params = %s::jsonb, finished_at = CURRENT_TIMESTAMP
                 WHERE id = %s
                """,
                (result["method"], detail, json.dumps(run_params, ensure_ascii=False), run_id),
            )
        return {"department": department, "status": "success", "method": result["method"],
                "made_on": made_on.isoformat(), "days": len(result["points"]), "detail": detail,
                "params": run_params}
    except Exception as exc:  # noqa: BLE001
        logging.exception("resource forecast run failed for %s", department)
        try:
            _finish_run(db, run_id, "failed", "", str(exc), extra or None)
        except Exception:  # noqa: BLE001
            logging.exception("resource forecast run %s could not be marked failed", run_id)
        return {"department": department, "status": "failed", "error": str(exc)[:500]}


def run_state() -> Dict[str, object]:
    """Whether a run is going on (in this process) and what waits for it."""
    with _run_state_lock:
        return {"running": bool(_run_state["running"]), "current": _run_state["current"],
                "pending": sorted(_run_state["pending"])}


def run_all(db, today: Optional[date] = None, oktell_query=None, triggered_by: str = "scheduler",
            departments: Sequence[str] = tuple(DEPARTMENTS), **run_kwargs) -> List[Dict[str, object]]:
    """Run the departments one after another.

    A request that comes while a run is going on is queued, not dropped: the running thread
    takes it up when it is through (an exclusion added during the nightly run must reach the
    model the same morning). Requests for one department merge into one rerun, and a queued
    department that is about to run anyway is not run twice.
    """
    wanted = [dept for dept in departments if dept in DEPARTMENTS]
    with _run_state_lock:
        if _run_state["running"]:
            for dept in wanted:
                _run_state["pending"].setdefault(dept, triggered_by)
            return [{"department": dept, "status": "queued"} for dept in wanted]
        _run_state["running"] = True
    results: List[Dict[str, object]] = []
    queue = [(dept, triggered_by) for dept in wanted]
    try:
        while True:
            for dept, by in queue:
                with _run_state_lock:
                    _run_state["current"] = dept
                    _run_state["pending"].pop(dept, None)
                try:
                    results.append(run_department(db, dept, today=today, oktell_query=oktell_query,
                                                  triggered_by=by, **run_kwargs))
                except Exception as exc:  # noqa: BLE001 — one department must not stop the others
                    logging.exception("resource forecast run crashed for %s", dept)
                    results.append({"department": dept, "status": "failed", "error": str(exc)[:500]})
            with _run_state_lock:
                if not _run_state["pending"]:
                    _run_state["running"] = False
                    _run_state["current"] = None
                    return results
                queue = list(_run_state["pending"].items())
                _run_state["pending"].clear()
    except BaseException:
        with _run_state_lock:
            _run_state["running"] = False
            _run_state["current"] = None
        raise


# ── reading ──────────────────────────────────────────────────────────────────

def latest_run(cursor, department: str, successful: bool = True) -> Optional[dict]:
    cursor.execute(
        f"""
        SELECT id, made_on, method, status, detail, params, triggered_by, started_at, finished_at
          FROM resource_forecast_runs
         WHERE department = %s {"AND status = 'success'" if successful else ""}
         ORDER BY id DESC LIMIT 1
        """,
        (department,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    params = row[5] if isinstance(row[5], dict) else json.loads(row[5] or "{}")
    return {"id": row[0], "made_on": row[1].isoformat(), "method": row[2], "method_label": METHOD_LABELS.get(row[2], row[2]),
            "status": row[3], "detail": row[4], "params": params, "triggered_by": row[6],
            "started_at": row[7].isoformat() if row[7] else None, "finished_at": row[8].isoformat() if row[8] else None}


def latest_params(cursor, department: str) -> Optional[Dict[str, object]]:
    """Handle time and patience from the newest run that managed to measure them."""
    cursor.execute(
        """
        SELECT params, made_on FROM resource_forecast_runs
         WHERE department = %s AND status = 'success' AND params ? 'aht_seconds'
         ORDER BY id DESC LIMIT 1
        """,
        (department,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    params = row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")
    return {**params, "made_on": row[1].isoformat()}


def params_history(cursor, department: str) -> List[Tuple[str, Dict[str, object]]]:
    """(made_on, measured params) of every successful run that measured them, oldest first."""
    cursor.execute(
        """
        SELECT made_on, params FROM resource_forecast_runs
         WHERE department = %s AND status = 'success' AND params ? 'aht_seconds'
         ORDER BY made_on, id
        """,
        (department,),
    )
    out: List[Tuple[str, Dict[str, object]]] = []
    for made_on, params in cursor.fetchall():
        params = params if isinstance(params, dict) else json.loads(params or "{}")
        measured = {key: params[key] for key in PARAM_KEYS if key in params}
        out.append((made_on.isoformat(), {**measured, "made_on": made_on.isoformat()}))
    return out


def params_for(history: Sequence[Tuple[str, Dict[str, object]]], made_on_iso: str) -> Optional[Dict[str, object]]:
    """The handle time and patience a forecast was made with: those of the newest run on or
    before its made_on (the oldest known when it predates them all). A past day so keeps
    the numbers it was planned with instead of moving with every new measurement."""
    chosen = None
    for run_made_on, params in history:
        if run_made_on <= made_on_iso:
            chosen = params
        else:
            break
    if chosen is None and history:
        chosen = history[0][1]
    return chosen


def daily_forecast(cursor, department: str, date_from: date, date_to: date,
                   as_of: Optional[date] = None) -> Dict[date, dict]:
    """For each day the newest forecast made BEFORE that day (and not after `as_of`), with the
    planner's uplift adjustments applied. When the newest one is the calendar fallback of a
    night BigQuery failed, a TimesFM forecast at most TIMESFM_STALE_DAYS older wins."""
    params: List[object] = [department, date_from, date_to]
    as_of_sql = ""
    if as_of is not None:
        as_of_sql = "AND made_on <= %s"
        params.append(as_of)
    params.append(TIMESFM_STALE_DAYS + 1)
    cursor.execute(
        f"""
        SELECT forecast_date, made_on, method, calls, calls_low, calls_high
          FROM (
                SELECT forecast_date, made_on, method, calls, calls_low, calls_high,
                       ROW_NUMBER() OVER (PARTITION BY forecast_date ORDER BY made_on DESC) AS rn
                  FROM resource_daily_forecasts
                 WHERE department = %s AND forecast_date BETWEEN %s AND %s AND made_on < forecast_date {as_of_sql}
               ) ranked
         WHERE rn <= %s
         ORDER BY forecast_date, made_on DESC
        """,
        params,
    )
    candidates: Dict[date, list] = {}
    for row in cursor.fetchall():
        candidates.setdefault(row[0], []).append(row)
    adjustments = list_adjustments(cursor, department, date_from, date_to) if candidates else []
    out: Dict[date, dict] = {}
    for forecast_date, rows in candidates.items():
        chosen = rows[0]
        if chosen[2] == "calendar":
            for row in rows[1:]:
                if row[2] != "calendar" and (chosen[1] - row[1]).days <= TIMESFM_STALE_DAYS:
                    chosen = row
                    break
        _, made_on, method, calls, low, high = chosen
        factor, notes = uplift_factor(adjustments, forecast_date)
        out[forecast_date] = {
            "date": forecast_date.isoformat(),
            "made_on": made_on.isoformat(),
            "method": method,
            "method_label": METHOD_LABELS.get(method, method),
            "base_calls": float(calls),
            "calls": float(calls) * factor,
            "calls_low": float(low) * factor if low is not None else None,
            "calls_high": float(high) * factor if high is not None else None,
            "uplift_factor": factor,
            "uplift_notes": [n for n in notes if n],
        }
    return out


def load_hours(cursor, date_from: date, date_to: date, isodow: Optional[int] = None) -> Dict[date, Tuple[np.ndarray, float]]:
    """Calls per hour and lost calls of the line's days in [date_from, date_to)."""
    sql = """
        SELECT report_date, hour, received_calls, lost_calls
          FROM daily_resource_hours
         WHERE report_date >= %s AND report_date < %s
    """
    params: List[object] = [date_from, date_to]
    if isodow is not None:
        sql += " AND EXTRACT(ISODOW FROM report_date) = %s"
        params.append(isodow)
    cursor.execute(sql, params)
    by_day: Dict[date, Tuple[np.ndarray, float]] = {}
    for report_date, hour, received, lost_calls in cursor.fetchall():
        arr, lost = by_day.get(report_date, (np.zeros(24), 0.0))
        arr[int(hour)] += float(received or 0)
        by_day[report_date] = (arr, lost + float(lost_calls or 0))
    return by_day


def hourly_shares(cursor, forecast_date: date, weeks: int = PROFILE_WEEKS,
                  hours: Optional[Dict[date, Tuple[np.ndarray, float]]] = None,
                  excluded: Iterable[date] = ()) -> Optional[List[float]]:
    """Share of the day's calls per hour for this weekday: mean over the `weeks` same weekdays
    before min(forecast date, today). Days that lost more than 15 % of calls or had fewer
    than 300 calls are skipped: an overloaded day has a distorted shape (redials). So are
    the days the planner excluded — a telephony outage keeps its 300 calls but not its shape.
    `hours` — load_hours() of a range that covers the window, to spare the query."""
    end = min(forecast_date, datetime.now().date())
    start = end - timedelta(days=7 * weeks)
    isodow = forecast_date.isoweekday()
    if hours is None:
        hours = load_hours(cursor, start, end, isodow)
    skip = set(excluded)
    days = {d: v for d, v in hours.items() if start <= d < end and d.isoweekday() == isodow and d not in skip}
    shares = []
    for arr, lost in days.values():
        total = arr.sum()
        if total < 300 or lost / total > 0.15:
            continue
        shares.append(arr / total)
    if not shares:
        shares = [arr / arr.sum() for arr, _ in days.values() if arr.sum() > 0]
    if not shares:
        return None
    mean = np.mean(shares, axis=0)
    return (mean / mean.sum()).tolist()


def prefetch_line(cursor, date_from: date, date_to: date, settings: Dict[str, object], cache: dict) -> None:
    """Everything the line's engine days of a period need, in four queries: a month in the
    planner and the refresh of every past day after an Oktell import must not query per day."""
    if engine_settings(settings)["forecast_engine"] != "timesfm":
        return
    cache["ready"] = schema_ready(cursor)
    if not cache["ready"]:
        return
    cache["params_history"] = params_history(cursor, "szov")
    if not cache["params_history"]:
        return
    cache["excluded"] = frozenset(_excluded_days(list_adjustments(cursor, "szov")))
    cache["forecasts"] = daily_forecast(cursor, "szov", date_from, date_to)
    cache["forecast_range"] = (date_from, date_to)
    if cache["forecasts"]:
        today = datetime.now().date()
        first, last = min(cache["forecasts"]), max(cache["forecasts"])
        start = min(first, today) - timedelta(days=7 * PROFILE_WEEKS)
        end = min(last, today)
        cache["hours"] = load_hours(cursor, start, end)
        cache["hours_range"] = (start, end)


def _cached_hours(cache: dict, forecast_date: date) -> Optional[Dict[date, Tuple[np.ndarray, float]]]:
    covered = cache.get("hours_range")
    if not covered:
        return None
    end = min(forecast_date, datetime.now().date())
    if covered[0] <= end - timedelta(days=7 * PROFILE_WEEKS) and end <= covered[1]:
        return cache["hours"]
    return None


def line_profile_for_date(cursor, forecast_date: date, settings: Dict[str, object],
                          cache: Optional[dict] = None) -> Optional[Dict[str, object]]:
    """The line's (СЗоВ) day in the shape of the old profile, built by the engine.

    None when the engine is switched off or has nothing for this day yet (tables not there,
    no stored forecast made before the day, no measured handle time, no hourly history):
    the caller then keeps the old rule, so the switch-over needs no backfill of past days.
    """
    cfg = engine_settings(settings)
    if cfg["forecast_engine"] != "timesfm":
        return None
    cache = cache if cache is not None else {}
    if "ready" not in cache:
        cache["ready"] = schema_ready(cursor)
    if not cache["ready"]:
        return None
    if "params_history" not in cache:
        cache["params_history"] = params_history(cursor, "szov")
    if not cache["params_history"]:
        return None
    covered = cache.get("forecast_range")
    if covered and covered[0] <= forecast_date <= covered[1]:
        forecast = cache["forecasts"].get(forecast_date)
    else:
        forecast = daily_forecast(cursor, "szov", forecast_date, forecast_date).get(forecast_date)
    if not forecast:
        return None
    params = params_for(cache["params_history"], forecast["made_on"])
    if not params:
        return None
    if "excluded" not in cache:
        cache["excluded"] = frozenset(_excluded_days(list_adjustments(cursor, "szov")))
    shares_key = ("shares", forecast_date.isoweekday(), min(forecast_date, datetime.now().date()))
    if shares_key not in cache:
        cache[shares_key] = hourly_shares(cursor, forecast_date, hours=_cached_hours(cache, forecast_date),
                                          excluded=cache["excluded"])
    shares = cache[shares_key]
    if not shares:
        return None
    ur = float(settings.get("ur") or 0.95) or 0.95
    aht = float(params.get("aht_seconds") or 0) or 250.0
    calls_by_hour = [forecast["calls"] * share for share in shares]
    staff = staffing_for_day(calls_by_hour, params, settings)
    hourly_profile = []
    daily_fte = 0.0
    for hour_row in staff["hours"]:
        hour = hour_row["hour"]
        calls = calls_by_hour[hour]
        fte = hour_row["agents"] / ur
        daily_fte += fte
        workload = calls * aht / 60.0
        hourly_profile.append({
            "hour": hour,
            "avg_calls": calls,
            "aht_seconds": aht,
            "distribution": shares[hour],
            "workload_minutes": workload,
            # Minutes of agent time one scheduled FTE gives this hour under the targets:
            # what the old model called 60 x Occ x UR, here an outcome of Erlang A.
            "effective_fte_minutes": (workload / fte) if fte > 0 else 0.0,
            "fte": fte,
            "fte_rounded": round(fte, 4),
            "source_calls": [],
            "engine_agents": hour_row["agents"],
            "engine_sl": hour_row["sl"],
            "engine_ar": hour_row["ar"],
            "engine_occupancy": hour_row["occupancy"],
        })
    return {
        "weekday": forecast_date.weekday(),
        "history_dates": [],
        "history_count": 0,
        "insufficient_history": False,
        "avg_daily_calls": forecast["calls"],
        "aht_seconds": aht,
        "forecast_aht_seconds": aht,
        "daily_fte": daily_fte,
        "hourly_profile": hourly_profile,
        "engine": {
            "method": forecast["method"],
            "method_label": forecast["method_label"],
            "made_on": forecast["made_on"],
            "calls_low": forecast["calls_low"],
            "calls_high": forecast["calls_high"],
            "base_calls": forecast["base_calls"],
            "uplift_factor": forecast["uplift_factor"],
            "uplift_notes": forecast["uplift_notes"],
            "day_sl": staff["day_sl"],
            "day_ar": staff["day_ar"],
            # Fewer lost calls than the band allows: the occupancy cap or the hour floor,
            # not the daily target, set the staffing — more people than the target needs.
            "ar_below_band": staff["day_ar"] < cfg["ar_min"],
            "aht_seconds": aht,
            "patience_seconds": float(params.get("patience_seconds") or 0) or None,
            "params_measured_on": params.get("made_on"),
            "targets": {key: cfg[key] for key in ("sl_target", "sl_seconds", "ar_min", "ar_max", "hour_sl_floor", "max_occupancy")},
        },
    }


def staffing_for_day(calls_by_hour: Sequence[float], params: Dict[str, object], settings: Dict[str, object]) -> Dict[str, object]:
    """Erlang A staffing of a day; remembered per inputs, so recounting the same past day
    after every Oktell import costs nothing. The result is shared — do not modify it."""
    cfg = engine_settings(settings)
    aht = float(params.get("aht_seconds") or 0) or 250.0
    patience = float(params.get("patience_seconds") or 0) or 250.0
    calls = tuple(round(max(0.0, float(c or 0.0)), 3) for c in calls_by_hour)
    key = (calls, round(aht, 1), round(patience, 1), cfg["sl_target"], cfg["sl_seconds"], cfg["ar_max"],
           cfg["hour_sl_floor"], cfg["max_occupancy"])
    with _staffing_lock:
        hit = _staffing_cache.get(key)
        if hit is not None:
            _staffing_cache.move_to_end(key)
            return hit
    staff = erlang_a.day_staffing(
        list(calls), aht, patience,
        sl_target=cfg["sl_target"], sl_seconds=cfg["sl_seconds"], ar_max=cfg["ar_max"],
        hour_sl_floor=cfg["hour_sl_floor"], max_occupancy=cfg["max_occupancy"],
    )
    with _staffing_lock:
        _staffing_cache[key] = staff
        while len(_staffing_cache) > STAFFING_CACHE_SIZE:
            _staffing_cache.popitem(last=False)
    return staff


def incident_extra_agents(hour_calls: float, extra_calls: float, agents: int, hour_sl: Optional[float],
                          params: Dict[str, object], settings: Dict[str, object]) -> int:
    """Agents an hour needs on top of its plan when `extra_calls` more arrive (an incident wave).

    The hour keeps the service it was planned at, capped at the day's target (a quiet hour
    planned at 99 % with its one agent need not stay at 99 %) and never under the hour floor;
    the occupancy cap holds too. «Extra calls × FTE per call» turned a fraction of a call in
    a quiet hour into a whole person.
    """
    if extra_calls <= 0:
        return 0
    cfg = engine_settings(settings)
    aht = float(params.get("aht_seconds") or 0) or 250.0
    patience = float(params.get("patience_seconds") or 0) or 250.0
    planned = max(0, int(agents or 0))
    target = max(cfg["hour_sl_floor"], min(float(hour_sl if hour_sl is not None else 1.0), cfg["sl_target"]))
    total = max(0.0, float(hour_calls or 0.0)) + float(extra_calls)
    n = max(1, planned)
    while n < planned + 400:
        m = erlang_a.erlang_a(total, aht, patience, n, cfg["sl_seconds"])
        if m["sl"] >= target - 1e-9 and m["occ"] <= cfg["max_occupancy"] + 1e-9:
            break
        n += 1
    return n - planned
