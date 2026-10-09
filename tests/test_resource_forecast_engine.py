"""Forecast engine of «Расчёт ресурсов»: Erlang A, calendar model, BigQuery TimesFM client,
per-department methods, runs, adjustments and the line integration."""
import math
import random
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import numpy as np

from resource_fte import calendar_model, erlang_a, forecast_engine, timesfm_bq
from resource_fte import calculations

ROOT = Path(__file__).resolve().parents[1]


class ErlangATests(unittest.TestCase):
    def test_reduces_to_erlang_c_without_abandonment(self):
        # Textbook: 100 calls/h, AHT 300 s -> A = 8.33 Erlang; N=11 SL(20s)=0.749, N=12 0.862.
        self.assertAlmostEqual(erlang_a.erlang_a(100, 300, 1e7, 11)["sl"], 0.749, places=3)
        self.assertAlmostEqual(erlang_a.erlang_a(100, 300, 1e7, 12)["sl"], 0.862, places=3)
        self.assertLess(erlang_a.erlang_a(100, 300, 1e7, 12)["ar"], 1e-4)

    def test_more_agents_better_service(self):
        prev = None
        for n in range(3, 15):
            m = erlang_a.erlang_a(80, 250, 250, n)
            if prev:
                self.assertGreater(m["sl"], prev["sl"])
                self.assertLess(m["ar"], prev["ar"])
                self.assertLess(m["occ"], prev["occ"])
            prev = m

    def test_abandonment_rises_when_understaffed(self):
        self.assertGreater(erlang_a.erlang_a(80, 250, 250, 5)["ar"], 0.15)
        self.assertLess(erlang_a.erlang_a(80, 250, 250, 9)["ar"], 0.03)

    def test_no_calls_no_agents(self):
        staff = erlang_a.day_staffing([0.0] * 24, 250, 250)
        self.assertEqual(staff["agents"], [0] * 24)
        self.assertEqual(erlang_a.erlang_a(0, 250, 250, 3)["sl"], 1.0)

    def test_a_fraction_of_a_call_is_no_call(self):
        # Rounded to thousandths it would be lam = 0 and a division by zero.
        self.assertEqual(erlang_a.erlang_a(0.0004, 220, 120, 1)["sl"], 1.0)
        staff = erlang_a.day_staffing([0.0003] + [100.0] * 23, 220, 120)
        self.assertEqual(staff["agents"][0], 0)
        self.assertGreater(staff["agents"][1], 0)

    def test_day_staffing_meets_daily_targets_and_hour_floors(self):
        calls = [20, 17, 7, 5, 5, 4, 9, 20, 38, 78, 67, 70, 75, 79, 80, 85, 89, 85, 76, 73, 72, 56, 43, 30]
        staff = erlang_a.day_staffing(calls, 250, 250, sl_target=0.8, ar_max=0.05,
                                      hour_sl_floor=0.6, max_occupancy=0.85)
        self.assertGreaterEqual(staff["day_sl"], 0.8)
        self.assertLessEqual(staff["day_ar"], 0.05)
        for hour in staff["hours"]:
            self.assertGreaterEqual(hour["agents"], 1)
            self.assertGreaterEqual(hour["sl"], 0.6 - 1e-9)
            self.assertLessEqual(hour["occupancy"], 0.85 + 1e-9)

    def test_daily_target_is_cheaper_than_every_hour_at_target(self):
        calls = [20, 17, 7, 5, 5, 4, 9, 20, 38, 78, 67, 70, 75, 79, 80, 85, 89, 85, 76, 73, 72, 56, 43, 30]
        daily = sum(erlang_a.day_staffing(calls, 250, 250)["agents"])
        per_hour = 0
        for c in calls:
            n = 1
            while erlang_a.erlang_a(c, 250, 250, n)["sl"] < 0.8 or erlang_a.erlang_a(c, 250, 250, n)["ar"] > 0.05:
                n += 1
            per_hour += n
        self.assertLessEqual(daily, per_hour)

    def test_patience_estimate(self):
        self.assertAlmostEqual(erlang_a.estimate_patience(10, 1500, 500), 200.0)
        self.assertEqual(erlang_a.estimate_patience(0, 1500, 500), 0.0)


def _synthetic_series(days=420, start=date(2025, 6, 2), noise=0.03, seed=7):
    rng = random.Random(seed)
    weekday = [1.0, 1.1, 1.05, 1.05, 1.05, 0.95, 0.8]
    series = {}
    for i in range(days):
        d = start + timedelta(days=i)
        dom = 1.15 if 13 <= d.day <= 16 else 1.0
        series[d] = 1500 * weekday[d.weekday()] * dom * math.exp(rng.gauss(0, noise))
    return series


class CalendarModelTests(unittest.TestCase):
    def test_recovers_weekday_and_mid_month_effects(self):
        factors = calendar_model.fit_factors(_synthetic_series())
        self.assertGreater(factors.weekday[1], factors.weekday[0])
        self.assertLess(factors.weekday[6], 0.85)
        mid = factors.dom[calendar_model.dom_bin(15)]
        early = factors.dom[calendar_model.dom_bin(6)]
        self.assertGreater(mid / early, 1.10)

    def test_too_short_history_gives_neutral_factors(self):
        factors = calendar_model.fit_factors({date(2026, 1, 1): 10.0})
        self.assertEqual(factors.weekday, [1.0] * 7)

    def test_fill_gaps_fills_missing_and_excluded_days(self):
        series = _synthetic_series(days=60)
        days = sorted(series)
        missing, excluded = days[30], days[40]
        series.pop(missing)
        series[excluded] = 1e6  # a broken day that must not reach the model
        # As the engine does: factors from the clean days only.
        factors = calendar_model.fit_factors({d: v for d, v in series.items() if d != excluded})
        out_days, values, filled = calendar_model.fill_gaps(series, days[0], days[-1], factors, exclude=[excluded])
        self.assertEqual(out_days, days)
        self.assertIn(missing, filled)
        self.assertIn(excluded, filled)
        self.assertLess(values[out_days.index(excluded)], 5000)
        self.assertEqual(values[0], series[days[0]])

    def test_holiday_list_has_independence_day(self):
        self.assertTrue(calendar_model.is_holiday(date(2026, 12, 16)))
        self.assertFalse(calendar_model.is_holiday(date(2026, 12, 15)))


class _FakeResponse:
    def __init__(self, status, payload, content=b"{}"):
        self.status_code = status
        self._payload = payload
        self.content = content

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeSession:
    """post_responses: a list of (status, payload) answered in turn (the last one repeats)."""

    def __init__(self, post_payload=None, get_payloads=(), post_status=200, post_responses=None):
        self.post_responses = list(post_responses or [(post_status, post_payload)])
        self.get_payloads = list(get_payloads)
        self.posted = []
        self.got = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.posted.append(json)
        status, payload = self.post_responses.pop(0) if len(self.post_responses) > 1 else self.post_responses[0]
        return _FakeResponse(status, payload)

    def get(self, url, headers=None, params=None, timeout=None):
        self.got.append(params)
        return _FakeResponse(200, self.get_payloads.pop(0))


def _row(sid, day, value, low, high, status=""):
    ts = (day - date(1970, 1, 1)).days * 86400
    return {"f": [{"v": sid}, {"v": f"{ts:.0f}"}, {"v": str(value)}, {"v": str(low)}, {"v": str(high)}, {"v": status}]}


class TimesFMClientTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(timesfm_bq, "_credentials", return_value=("token", "proj"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_query_is_bounded(self):
        q = timesfm_bq.build_query(14, 0.8)
        self.assertIn("model => 'TimesFM 2.5'", q)
        self.assertIn("horizon => 14", q)
        self.assertIn("UNNEST(@series)", q)
        with self.assertRaises(ValueError):
            timesfm_bq.build_query(0, 0.8)
        with self.assertRaises(ValueError):
            timesfm_bq.build_query(5, 1.2)
        with self.assertRaises(ValueError):
            timesfm_bq.build_query(5, 0.8, model="DROP TABLE x")

    def test_parameters_validate_ids_and_trim_context(self):
        points = [(date(2026, 1, 1) + timedelta(days=i), float(i)) for i in range(600)]
        params = timesfm_bq.build_parameters({"szov": points}, context_window=512)
        values = params[0]["parameterValue"]["arrayValues"]
        self.assertEqual(len(values), 512)
        self.assertEqual(values[-1]["structValues"]["d"]["value"], points[-1][0].isoformat())
        with self.assertRaises(ValueError):
            timesfm_bq.build_parameters({"bad id; --": points})

    def test_forecast_parses_rows(self):
        day = date(2026, 10, 12)
        session = _FakeSession({"jobComplete": True, "rows": [_row("szov", day, 1200.5, 1000, 1400)]})
        out = timesfm_bq.forecast({"szov": [(date(2026, 10, 1), 1.0)] * 3}, horizon=1, session=session)
        self.assertEqual(out["szov"][day], (1200.5, 1000.0, 1400.0))
        self.assertEqual(session.posted[0]["parameterMode"], "NAMED")

    def test_forecast_waits_for_slow_job(self):
        day = date(2026, 10, 12)
        session = _FakeSession({"jobComplete": False, "jobReference": {"jobId": "j1", "location": "US"}},
                               get_payloads=[{"jobComplete": False}, {"jobComplete": True, "rows": [_row("op", day, 300, 250, 350)]}])
        out = timesfm_bq.forecast({"op": [(date(2026, 10, 1), 1.0)] * 3}, horizon=1, session=session)
        self.assertEqual(out["op"][day][0], 300.0)
        self.assertEqual(session.got[0]["location"], "US")

    def test_http_error_and_model_status_raise(self):
        denied = _FakeSession({"error": {"message": "denied"}}, post_status=403)
        with self.assertRaises(timesfm_bq.TimesFMError):
            timesfm_bq.forecast({"op": [(date(2026, 10, 1), 1.0)] * 3}, horizon=1, session=denied, sleep=lambda s: None)
        self.assertEqual(len(denied.posted), 1)  # a refusal is not retried
        bad = _FakeSession({"jobComplete": True, "rows": [_row("op", date(2026, 10, 2), 0, 0, 0, status="too short")]})
        with self.assertRaises(timesfm_bq.TimesFMError):
            timesfm_bq.forecast({"op": [(date(2026, 10, 1), 1.0)] * 3}, horizon=1, session=bad)

    def test_passing_failures_are_retried_as_the_same_job(self):
        day = date(2026, 10, 12)
        session = _FakeSession(post_responses=[
            (503, {"error": "backend"}),
            (502, ValueError("not json: gateway page")),
            (200, {"jobComplete": True, "rows": [_row("tez", day, 90, 80, 100)]}),
        ])
        slept = []
        out = timesfm_bq.forecast({"tez": [(date(2026, 10, 1), 1.0)] * 3}, horizon=1, session=session, sleep=slept.append)
        self.assertEqual(out["tez"][day][0], 90.0)
        self.assertEqual(len(session.posted), 3)
        self.assertEqual(len(slept), 2)
        self.assertEqual(len({body["requestId"] for body in session.posted}), 1)


class _RecordingForecaster:
    def __init__(self, flat=1000.0, fail=False):
        self.flat = flat
        self.fail = fail
        self.inputs = None

    def __call__(self, series, horizon, confidence):
        if self.fail:
            raise timesfm_bq.TimesFMError("BigQuery down")
        (sid, points), = series.items()
        self.inputs = points
        last = points[-1][0]
        return {sid: {last + timedelta(days=i): (self.flat, self.flat * 0.9, self.flat * 1.1) for i in range(1, horizon + 1)}}


def _engine_day(day, calls=1500.0, made_on="2026-10-08"):
    return {"date": day.isoformat(), "made_on": made_on, "method": "timesfm_dom",
            "method_label": "TimesFM + цикл месяца", "base_calls": calls, "calls": calls,
            "calls_low": calls * 0.8, "calls_high": calls * 1.25, "uplift_factor": 1.0, "uplift_notes": []}


def _shares():
    shares = [0.01] * 6 + [0.03] * 2 + [0.06] * 12 + [0.03] * 4
    return [s / sum(shares) for s in shares]


class ForecastEngineTests(unittest.TestCase):
    def test_engine_settings_bounds(self):
        cfg = forecast_engine.engine_settings({"forecast_engine": "nonsense", "sl_target": 5, "ar_min": 0.2, "ar_max": 0.05})
        self.assertEqual(cfg["forecast_engine"], "timesfm")
        self.assertEqual(cfg["sl_target"], 0.99)
        self.assertLessEqual(cfg["ar_min"], cfg["ar_max"])
        self.assertEqual(forecast_engine.engine_settings({})["sl_seconds"], 20)

    def test_department_methods_follow_the_backtest(self):
        self.assertEqual(forecast_engine.DEPARTMENTS["szov"]["method"], "timesfm_dom")
        self.assertEqual(forecast_engine.DEPARTMENTS["op"]["method"], "timesfm")
        self.assertEqual(forecast_engine.DEPARTMENTS["tez"]["method"], "timesfm")

    def test_szov_takes_month_cycle_out_and_puts_it_back(self):
        series = _synthetic_series(days=420, noise=0.0)
        made_on = max(series)
        fake = _RecordingForecaster(flat=1000.0)
        result = forecast_engine.forecast_department(series, "szov", made_on, forecaster=fake)
        self.assertEqual(result["method"], "timesfm_dom")
        # The model saw the series without the mid-month bump...
        inputs = dict(fake.inputs)
        mid = [v for d, v in inputs.items() if 13 <= d.day <= 16 and d.weekday() == 2]
        rest = [v for d, v in inputs.items() if 5 <= d.day <= 9 and d.weekday() == 2]
        self.assertAlmostEqual(sum(mid) / len(mid) / (sum(rest) / len(rest)), 1.0, delta=0.05)
        # ...and the flat answer comes back with the bump on the 13th-16th.
        points = result["points"]
        mid_day = next(d for d in points if d.day == 15)
        early_day = next(d for d in points if d.day == 6)
        self.assertGreater(points[mid_day][0] / points[early_day][0], 1.10)

    def test_op_series_goes_to_the_model_as_is(self):
        series = _synthetic_series(days=200)
        made_on = max(series)
        fake = _RecordingForecaster()
        result = forecast_engine.forecast_department(series, "op", made_on, forecaster=fake)
        self.assertEqual(result["method"], "timesfm")
        self.assertAlmostEqual(dict(fake.inputs)[made_on], series[made_on])

    def test_fallback_when_bigquery_fails(self):
        series = _synthetic_series(days=200)
        made_on = max(series)
        result = forecast_engine.forecast_department(series, "op", made_on, forecaster=_RecordingForecaster(fail=True))
        self.assertEqual(result["method"], "calendar")
        self.assertIn("TimesFM", result["detail"])
        self.assertEqual(len(result["points"]), forecast_engine.HORIZON_DAYS)
        for calls, low, high in result["points"].values():
            self.assertLess(low, calls)
            self.assertLess(calls, high)

    def test_excluded_days_never_reach_the_model(self):
        series = _synthetic_series(days=200)
        made_on = max(series)
        broken = made_on - timedelta(days=10)
        series[broken] = 1e7
        fake = _RecordingForecaster()
        forecast_engine.forecast_department(series, "op", made_on, excluded=[broken], forecaster=fake)
        self.assertLess(dict(fake.inputs)[broken], 1e5)

    def test_uplift_factors_multiply_inside_their_dates(self):
        adjustments = [
            {"kind": "uplift", "date_from": "2026-10-13", "date_to": "2026-10-16", "percent": 20.0, "note": "волна"},
            {"kind": "uplift", "date_from": "2026-10-15", "date_to": "2026-10-15", "percent": -10.0, "note": ""},
            {"kind": "exclude", "date_from": "2026-10-15", "date_to": "2026-10-15", "percent": None, "note": ""},
        ]
        self.assertAlmostEqual(forecast_engine.uplift_factor(adjustments, date(2026, 10, 15))[0], 1.2 * 0.9)
        self.assertEqual(forecast_engine.uplift_factor(adjustments, date(2026, 10, 12))[0], 1.0)

    def test_add_adjustment_validates(self):
        cursor = mock.MagicMock()
        cursor.fetchone.return_value = (5,)
        with self.assertRaises(ValueError):
            forecast_engine.add_adjustment(cursor, "szov", {"kind": "boom", "date_from": "2026-10-13"}, 1)
        with self.assertRaises(ValueError):
            forecast_engine.add_adjustment(cursor, "szov", {"kind": "uplift", "date_from": "2026-10-13", "percent": 900}, 1)
        with self.assertRaises(ValueError):
            forecast_engine.add_adjustment(cursor, "szov", {"kind": "uplift", "date_from": "2026-10-13", "percent": "nan"}, 1)
        with self.assertRaises(ValueError):
            forecast_engine.add_adjustment(cursor, "szov", {"kind": "exclude", "date_from": "2026-01-01", "date_to": "2026-06-01"}, 1)
        with self.assertRaises(ValueError):
            forecast_engine.add_adjustment(cursor, "nope", {"kind": "exclude", "date_from": "2026-10-13"}, 1)
        today = date(2026, 10, 9)
        with self.assertRaises(ValueError):
            forecast_engine.add_adjustment(cursor, "szov", {"kind": "uplift", "date_from": "2026-10-06", "date_to": "2026-10-08", "percent": 20}, 1, today=today)
        excluded = forecast_engine.add_adjustment(cursor, "szov", {"kind": "exclude", "date_from": "2026-09-14", "date_to": "2026-09-16"}, 1, today=today)
        self.assertEqual(excluded["kind"], "exclude")
        created = forecast_engine.add_adjustment(cursor, "szov", {"kind": "uplift", "date_from": "2026-10-16", "date_to": "2026-10-09", "percent": "15"}, 1, today=today)
        self.assertEqual((created["date_from"], created["date_to"], created["percent"]), ("2026-10-09", "2026-10-16", 15.0))

    def test_volumes_take_closed_days_only(self):
        op = forecast_engine.volume_sql("op")
        self.assertIn("d.status = 'done' AND d.complete", op)
        tez = forecast_engine.volume_sql("tez")
        self.assertIn("s.synced_at >= ((s.day + 1)::timestamp AT TIME ZONE 'Asia/Almaty')", tez)

    def test_registry_numbers_stay_out_when_the_registry_is_there(self):
        keys = mock.MagicMock()
        keys.TABLE = "test_phone_numbers"
        keys.sql_not_test.side_effect = lambda expr, digits=False: f"NOT EXISTS (registry {expr})"
        keys.tsql_and_not_test.side_effect = lambda expr: f"AND {expr} NOT IN ('7011234567') "
        ready = mock.MagicMock()
        ready.fetchone.return_value = (True,)
        with mock.patch.object(forecast_engine, "_test_keys", keys):
            self.assertEqual(forecast_engine._not_test_sql(ready, "t.phone"), " AND NOT EXISTS (registry t.phone)")
            self.assertIn("NOT EXISTS (registry t.phone)", forecast_engine.volume_sql("op", forecast_engine._not_test_sql(ready, "t.phone")))
            sql = forecast_engine.oktell_daily_sql("20260101", "20260201", forecast_engine._oktell_not_test_sql())
            self.assertIn("AND t.[number] NOT IN ('7011234567') GROUP BY", sql)
            params_sql = forecast_engine.oktell_params_sql("20260101", "20260201", "20251231", forecast_engine._oktell_not_test_sql())
            self.assertTrue(params_sql.endswith("NOT IN ('7011234567')"))
            missing = mock.MagicMock()
            missing.fetchone.return_value = (False,)
            self.assertEqual(forecast_engine._not_test_sql(missing, "t.phone"), "")
        with mock.patch.object(forecast_engine, "_test_keys", None):
            self.assertEqual(forecast_engine._oktell_not_test_sql(), "")
            self.assertIn("N'Неудачный звонок' GROUP BY", forecast_engine.oktell_daily_sql("20260101", "20260201"))

    def test_line_profile_is_none_when_engine_off_or_empty(self):
        cursor = mock.MagicMock()
        self.assertIsNone(forecast_engine.line_profile_for_date(cursor, date(2026, 10, 12), {"forecast_engine": "legacy"}))
        with mock.patch.object(forecast_engine, "schema_ready", return_value=False):
            self.assertIsNone(forecast_engine.line_profile_for_date(cursor, date(2026, 10, 12), {"forecast_engine": "timesfm"}))
        with mock.patch.object(forecast_engine, "schema_ready", return_value=True), \
                mock.patch.object(forecast_engine, "params_history", return_value=[]):
            self.assertIsNone(forecast_engine.line_profile_for_date(cursor, date(2026, 10, 12), {"forecast_engine": "timesfm"}))

    def test_line_profile_shape_and_fte(self):
        day = date(2026, 10, 13)
        settings = {"forecast_engine": "timesfm", "ur": 0.95}
        history = [("2026-10-08", {"aht_seconds": 245, "patience_seconds": 210, "made_on": "2026-10-08"})]
        with mock.patch.object(forecast_engine, "schema_ready", return_value=True), \
                mock.patch.object(forecast_engine, "params_history", return_value=history), \
                mock.patch.object(forecast_engine, "daily_forecast", return_value={day: _engine_day(day)}), \
                mock.patch.object(forecast_engine, "hourly_shares", return_value=_shares()):
            profile = forecast_engine.line_profile_for_date(mock.MagicMock(), day, settings)
        self.assertEqual(len(profile["hourly_profile"]), 24)
        self.assertAlmostEqual(sum(r["avg_calls"] for r in profile["hourly_profile"]), 1500.0)
        for row in profile["hourly_profile"]:
            self.assertAlmostEqual(row["fte"], row["engine_agents"] / 0.95)
        self.assertAlmostEqual(profile["daily_fte"], sum(r["fte"] for r in profile["hourly_profile"]))
        self.assertGreaterEqual(profile["engine"]["day_sl"], 0.8)
        self.assertLessEqual(profile["engine"]["day_ar"], 0.05)
        self.assertEqual(profile["engine"]["params_measured_on"], "2026-10-08")

    def test_past_day_keeps_the_handle_time_it_was_planned_with(self):
        history = [
            ("2026-10-01", {"aht_seconds": 230, "made_on": "2026-10-01"}),
            ("2026-10-05", {"aht_seconds": 250, "made_on": "2026-10-05"}),
            ("2026-10-05", {"aht_seconds": 252, "made_on": "2026-10-05"}),  # a rerun the same day
            ("2026-10-09", {"aht_seconds": 270, "made_on": "2026-10-09"}),
        ]
        self.assertEqual(forecast_engine.params_for(history, "2026-10-04")["aht_seconds"], 230)
        self.assertEqual(forecast_engine.params_for(history, "2026-10-07")["aht_seconds"], 252)
        self.assertEqual(forecast_engine.params_for(history, "2026-10-20")["aht_seconds"], 270)
        self.assertEqual(forecast_engine.params_for(history, "2026-09-01")["aht_seconds"], 230)
        self.assertIsNone(forecast_engine.params_for([], "2026-10-01"))

    def test_recent_timesfm_beats_a_newer_calendar_fallback(self):
        day = date(2026, 10, 20)
        cursor = mock.MagicMock()
        cursor.fetchall.side_effect = [
            [
                (day, date(2026, 10, 15), "calendar", 900.0, 800.0, 1000.0),
                (day, date(2026, 10, 14), "timesfm_dom", 1200.0, 1100.0, 1300.0),
                (day + timedelta(days=1), date(2026, 10, 15), "calendar", 950.0, 850.0, 1050.0),
                (day + timedelta(days=1), date(2026, 10, 11), "timesfm_dom", 1250.0, 1150.0, 1350.0),
            ],
            [],  # adjustments
        ]
        out = forecast_engine.daily_forecast(cursor, "szov", day, day + timedelta(days=1))
        self.assertEqual((out[day]["method"], out[day]["calls"]), ("timesfm_dom", 1200.0))
        # Four days older than the fallback: too old, the fallback stays.
        self.assertEqual(out[day + timedelta(days=1)]["method"], "calendar")

    def test_hourly_shares_window_ends_at_today_for_future_days(self):
        today = datetime.now().date()
        hours = {}
        for weeks_back in range(1, 12):
            d = today - timedelta(days=7 * weeks_back)
            arr = np.zeros(24)
            # The last three weeks call at 10, the five before at 12, older weeks at 20.
            arr[10 if weeks_back <= 3 else 12 if weeks_back <= 8 else 20] = 1000.0
            hours[d] = (arr, 0.0)
        far = today + timedelta(days=35)  # same weekday as today
        shares = forecast_engine.hourly_shares(None, far, hours=hours)
        # Eight full weeks before today, not the three left of [far - 56, today).
        self.assertAlmostEqual(shares[10], 3 / 8)
        self.assertAlmostEqual(shares[12], 5 / 8)
        self.assertAlmostEqual(shares[20], 0.0)

    def test_excluded_days_do_not_shape_the_hours(self):
        today = datetime.now().date()
        hours = {}
        for weeks_back in range(1, 9):
            d = today - timedelta(days=7 * weeks_back)
            arr = np.zeros(24)
            arr[10 if weeks_back != 2 else 20] = 1000.0   # the outage day called at the wrong hour
            hours[d] = (arr, 0.0)
        outage = today - timedelta(days=14)
        far = today + timedelta(days=7)
        self.assertGreater(forecast_engine.hourly_shares(None, far, hours=hours)[20], 0.1)
        shares = forecast_engine.hourly_shares(None, far, hours=hours, excluded=[outage])
        self.assertAlmostEqual(shares[20], 0.0)
        self.assertAlmostEqual(shares[10], 1.0)

    def test_prefetched_day_runs_no_queries(self):
        # A prefetched day is computed without a savepoint, so it must not touch the database.
        day = date(2026, 10, 16)
        history = [("2026-10-08", {"aht_seconds": 245, "patience_seconds": 210, "made_on": "2026-10-08"})]
        cursor = mock.MagicMock()
        cache = {}
        settings = {"forecast_engine": "timesfm", "ur": 0.95}
        with mock.patch.object(forecast_engine, "schema_ready", return_value=True), \
                mock.patch.object(forecast_engine, "params_history", return_value=history), \
                mock.patch.object(forecast_engine, "daily_forecast", return_value={day: _engine_day(day)}), \
                mock.patch.object(forecast_engine, "list_adjustments", return_value=[]), \
                mock.patch.object(forecast_engine, "load_hours", return_value={}), \
                mock.patch.object(forecast_engine, "hourly_shares", return_value=_shares()):
            forecast_engine.prefetch_line(cursor, day, day, settings, cache)
            cursor.reset_mock()
            with mock.patch.object(forecast_engine, "list_adjustments", side_effect=AssertionError("query")):
                profile = forecast_engine.line_profile_for_date(cursor, day, settings, cache)
        self.assertIsNotNone(profile)
        cursor.execute.assert_not_called()

    def test_line_profile_leaves_excluded_days_out_of_the_shape(self):
        day = date(2026, 10, 16)
        history = [("2026-10-08", {"aht_seconds": 245, "patience_seconds": 210, "made_on": "2026-10-08"})]
        outage = {"kind": "exclude", "date_from": "2026-09-25", "date_to": "2026-09-25", "percent": None, "note": ""}
        with mock.patch.object(forecast_engine, "schema_ready", return_value=True), \
                mock.patch.object(forecast_engine, "params_history", return_value=history), \
                mock.patch.object(forecast_engine, "daily_forecast", return_value={day: _engine_day(day)}), \
                mock.patch.object(forecast_engine, "list_adjustments", return_value=[outage]), \
                mock.patch.object(forecast_engine, "hourly_shares", return_value=_shares()) as shares:
            forecast_engine.line_profile_for_date(mock.MagicMock(), day, {"forecast_engine": "timesfm", "ur": 0.95})
        self.assertEqual(set(shares.call_args.kwargs["excluded"]), {date(2026, 9, 25)})

    def test_incident_wave_in_a_quiet_hour_adds_nobody(self):
        params = {"aht_seconds": 245, "patience_seconds": 210}
        settings = {"forecast_engine": "timesfm"}
        quiet = erlang_a.erlang_a(0.77, 245, 210, 1)
        self.assertEqual(forecast_engine.incident_extra_agents(0.77, 0.77, 1, quiet["sl"], params, settings), 0)
        busy_agents = 12
        busy = erlang_a.erlang_a(150, 245, 210, busy_agents)
        extra = forecast_engine.incident_extra_agents(150, 45, busy_agents, busy["sl"], params, settings)
        self.assertGreater(extra, 0)
        after = erlang_a.erlang_a(195, 245, 210, busy_agents + extra)
        self.assertGreaterEqual(after["sl"], min(busy["sl"], 0.8) - 1e-9)


class _ScriptedCursor:
    """Answers by the first SQL fragment that matches; records every statement."""

    def __init__(self, db):
        self.db = db
        self._one = None
        self._all = []

    def execute(self, sql, params=None):
        self.db.log.append(" ".join(sql.split()))
        self._one, self._all = None, []
        for fragment, one, many in self.db.answers:
            if fragment in sql:
                self._one, self._all = one, list(many)
                break

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._all)


class _ScriptedDb:
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.log = []
        self.commits = 0

    @contextmanager
    def _get_cursor(self):
        yield _ScriptedCursor(self)
        self.commits += 1

    def executed(self, fragment):
        return [sql for sql in self.log if fragment in sql]


class RunTests(unittest.TestCase):
    TODAY = date(2026, 10, 9)
    MADE_ON = date(2026, 10, 8)

    def _series(self, days=420):
        start = self.MADE_ON - timedelta(days=days - 1)
        return _synthetic_series(days=days, start=start)

    def _run(self, db, forecaster=None, latest=None, backfill_done=True, oktell=None, **patches):
        measured = {"aht_seconds": 240.0, "patience_seconds": 200.0, "answered": 30000}
        with mock.patch.object(forecast_engine, "ensure_schema_db", return_value=True), \
                mock.patch.object(forecast_engine, "refresh_volume"), \
                mock.patch.object(forecast_engine, "_szov_backfill_done", return_value=backfill_done), \
                mock.patch.object(forecast_engine, "load_volume", return_value=self._series()), \
                mock.patch.object(forecast_engine, "list_adjustments", return_value=[]), \
                mock.patch.object(forecast_engine, "latest_params", return_value=latest), \
                mock.patch.object(forecast_engine, "measure_szov_params", return_value=measured) as measure, \
                mock.patch.object(forecast_engine, "fetch_szov_history_from_oktell",
                                  side_effect=patches.get("fetch", RuntimeError("Oktell proxy down"))) as fetch:
            result = forecast_engine.run_department(
                db, "szov", today=self.TODAY, oktell_query=oktell or (lambda sql: []), sleep=lambda s: None,
                forecaster=forecaster or _RecordingForecaster(flat=1000.0))
        return result, measure, fetch

    def test_oktell_backfill_failure_costs_a_note_not_the_forecast(self):
        db = _ScriptedDb([
            ("INSERT INTO resource_forecast_runs", (11,), []),
            ("SELECT MIN(day)", (self.MADE_ON - timedelta(days=200),), []),
        ])
        result, measure, fetch = self._run(db, backfill_done=False)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["status"], "success")
        self.assertIn("Oktell", result["detail"])
        self.assertIn("error", result["params"]["oktell_backfill"])
        self.assertEqual(len(db.executed("INSERT INTO resource_daily_forecasts")), forecast_engine.HORIZON_DAYS)

    def test_rerun_of_the_same_morning_does_not_ask_oktell_again(self):
        db = _ScriptedDb([("INSERT INTO resource_forecast_runs", (12,), [])])
        latest = {"aht_seconds": 244.0, "patience_seconds": 190.0, "made_on": self.MADE_ON.isoformat(),
                  "factors": {"weekday": [1] * 7}}
        result, measure, fetch = self._run(db, latest=latest)
        measure.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["params"]["aht_seconds"], 244.0)
        self.assertEqual(result["params"]["reused_from_run_of"], self.MADE_ON.isoformat())

    def test_new_morning_measures_again(self):
        db = _ScriptedDb([("INSERT INTO resource_forecast_runs", (13,), [])])
        latest = {"aht_seconds": 244.0, "patience_seconds": 190.0, "made_on": (self.MADE_ON - timedelta(days=1)).isoformat()}
        result, measure, _ = self._run(db, latest=latest)
        measure.assert_called_once()
        self.assertEqual(result["params"]["aht_seconds"], 240.0)

    def test_fallback_does_not_overwrite_a_timesfm_forecast_of_the_same_day(self):
        db = _ScriptedDb([
            ("INSERT INTO resource_forecast_runs", (14,), []),
            ("AND method <> 'calendar'", (1,), []),
        ])
        result, _, _ = self._run(db, forecaster=_RecordingForecaster(fail=True))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(db.executed("INSERT INTO resource_daily_forecasts"), [])
        self.assertTrue(db.executed("SET status = %s"))

    def test_fallback_is_stored_when_the_day_has_nothing_better(self):
        db = _ScriptedDb([("INSERT INTO resource_forecast_runs", (15,), [])])
        result, _, _ = self._run(db, forecaster=_RecordingForecaster(fail=True))
        self.assertEqual((result["status"], result["method"]), ("success", "calendar"))
        self.assertEqual(len(db.executed("INSERT INTO resource_daily_forecasts")), forecast_engine.HORIZON_DAYS)

    def _portal_run(self, db, department="op"):
        with mock.patch.object(forecast_engine, "ensure_schema_db", return_value=True),                 mock.patch.object(forecast_engine, "refresh_volume") as refresh,                 mock.patch.object(forecast_engine, "load_volume", return_value=self._series()),                 mock.patch.object(forecast_engine, "list_adjustments", return_value=[]),                 mock.patch.object(forecast_engine, "measure_params_from_portal",
                                  return_value={"aht_seconds": 90.0, "patience_seconds": 60.0}):
            result = forecast_engine.run_department(db, department, today=self.TODAY, forecaster=_RecordingForecaster())
        return result, [(c.args[1], c.args[2], c.args[3]) for c in refresh.call_args_list]

    def test_short_stored_history_takes_every_source_day(self):
        # The bridge holds ОП from March; the first production run stored only the last 60 days.
        db = _ScriptedDb([
            ("INSERT INTO resource_forecast_runs", (16,), []),
            ("SELECT MIN(day)", (self.MADE_ON - timedelta(days=60),), []),
        ])
        result, windows = self._portal_run(db, "op")
        self.assertEqual(result["status"], "success")
        self.assertIn(("op", self.MADE_ON - timedelta(days=forecast_engine.HISTORY_DAYS - 1), self.MADE_ON), windows)

    def test_long_stored_history_refreshes_only_recent_days(self):
        db = _ScriptedDb([
            ("INSERT INTO resource_forecast_runs", (17,), []),
            ("SELECT MIN(day)", (self.MADE_ON - timedelta(days=600),), []),
        ])
        result, windows = self._portal_run(db, "tez")
        self.assertEqual(result["status"], "success")
        self.assertEqual(windows, [("tez", self.MADE_ON - timedelta(days=60), self.MADE_ON)])

    def test_no_schema_no_run(self):
        db = _ScriptedDb()
        with mock.patch.object(forecast_engine, "ensure_schema_db", return_value=False):
            result = forecast_engine.run_department(db, "op", today=self.TODAY)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(db.log, [])


class RunQueueTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(forecast_engine._run_state, {"running": False, "current": None, "pending": {}})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_request_during_a_run_is_queued_and_run_after_it(self):
        calls = []

        def fake_run(db, dept, **kwargs):
            calls.append((dept, kwargs.get("triggered_by")))
            if len(calls) == 1:
                queued = forecast_engine.run_all(db, departments=["szov"], triggered_by="user:7")
                self.assertEqual(queued, [{"department": "szov", "status": "queued"}])
                self.assertTrue(forecast_engine.run_state()["running"])
            return {"department": dept, "status": "success"}

        with mock.patch.object(forecast_engine, "run_department", side_effect=fake_run):
            results = forecast_engine.run_all(object(), departments=["szov", "op"])
        self.assertEqual(calls, [("szov", "scheduler"), ("op", "scheduler"), ("szov", "user:7")])
        self.assertEqual(len(results), 3)
        self.assertEqual(forecast_engine.run_state(), {"running": False, "current": None, "pending": []})

    def test_a_queued_department_that_is_about_to_run_runs_once(self):
        calls = []

        def fake_run(db, dept, **kwargs):
            calls.append(dept)
            if dept == "szov":
                forecast_engine.run_all(db, departments=["tez"], triggered_by="user:7")
            return {"department": dept, "status": "success"}

        with mock.patch.object(forecast_engine, "run_department", side_effect=fake_run):
            forecast_engine.run_all(object())
        self.assertEqual(calls, ["szov", "op", "tez"])

    def test_one_department_crash_does_not_stop_the_others(self):
        def fake_run(db, dept, **kwargs):
            if dept == "szov":
                raise RuntimeError("boom")
            return {"department": dept, "status": "success"}

        with mock.patch.object(forecast_engine, "run_department", side_effect=fake_run):
            results = forecast_engine.run_all(object())
        self.assertEqual([r["status"] for r in results], ["failed", "success", "success"])
        self.assertFalse(forecast_engine.run_state()["running"])


class _DdlCursor:
    def __init__(self, db):
        self.db = db

    def execute(self, sql, params=None):
        self.db.statements.append(sql.strip())
        if self.db.fail_on and sql.strip().startswith(self.db.fail_on):
            raise RuntimeError("canceling statement due to lock timeout")

    def fetchall(self):
        return [(name,) for name in self.db.present]

    def fetchone(self):
        return (self.db.tables_exist,)


class _TxDb:
    def __init__(self, fail_on=None, present=(), tables_exist=True):
        self.fail_on = fail_on
        self.present = list(present)
        self.tables_exist = tables_exist
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def _get_cursor(self):
        try:
            yield _DdlCursor(self)
            self.commits += 1
        except Exception:
            self.rollbacks += 1
            raise


class SchemaSafetyTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(forecast_engine._schema_state, {"done": False, "failed_at": None})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_schema_is_created_in_its_own_committed_transaction(self):
        db = _TxDb()
        self.assertTrue(forecast_engine.ensure_schema_db(db))
        self.assertEqual(db.commits, 1)
        self.assertTrue(forecast_engine._schema_state["done"])
        self.assertTrue(db.statements[0].startswith("SET LOCAL lock_timeout"))
        self.assertTrue(any(sql.startswith("ALTER TABLE resource_settings") for sql in db.statements))

    def test_failed_ddl_is_not_remembered_as_done_and_waits_before_retrying(self):
        db = _TxDb(fail_on="ALTER TABLE")
        self.assertFalse(forecast_engine.ensure_schema_db(db))
        self.assertFalse(forecast_engine._schema_state["done"])
        self.assertEqual((db.commits, db.rollbacks), (0, 1))
        count = len(db.statements)
        self.assertFalse(forecast_engine.ensure_schema_db(db))
        self.assertEqual(len(db.statements), count)

    def test_warm_start_does_not_alter_existing_columns(self):
        db = _TxDb(present=[name for name, _ in forecast_engine.SETTINGS_COLUMNS])
        self.assertTrue(forecast_engine.ensure_schema_db(db))
        self.assertFalse(any(sql.startswith("ALTER TABLE resource_settings") for sql in db.statements))

    def test_request_paths_only_read_the_catalog(self):
        db = _TxDb(tables_exist=False)
        with db._get_cursor() as cursor:
            self.assertFalse(forecast_engine.schema_ready(cursor))
            self.assertFalse(forecast_engine.settings_columns_ready(cursor))
        self.assertFalse(any(sql.split()[0] in ("CREATE", "ALTER") for sql in db.statements))
        self.assertFalse(forecast_engine._schema_state["done"])
        ready = _TxDb(present=[name for name, _ in forecast_engine.SETTINGS_COLUMNS])
        with ready._get_cursor() as cursor:
            self.assertTrue(forecast_engine.schema_ready(cursor))
        self.assertTrue(forecast_engine._schema_state["done"])

    def test_settings_read_falls_back_to_old_columns(self):
        from resource_fte import service
        cursor = mock.MagicMock()
        cursor.fetchone.return_value = (0.95, 0.7, 0.95, 0.9, 40, "none", "ceil", [70])
        with mock.patch.object(service, "_forecast_engine_columns_ready", return_value=False):
            settings = service._get_settings_tx(cursor)
        sql = cursor.execute.call_args[0][0]
        self.assertNotIn("forecast_engine", sql)
        self.assertEqual(settings["forecast_engine"], "timesfm")
        self.assertEqual(settings["sl_target"], 0.80)
        self.assertEqual(settings["selected_direction_ids"], [70])

    def test_engine_failure_leaves_the_day_to_the_old_rule(self):
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = []
        settings = {"forecast_engine": "timesfm", "occ": 0.7, "ur": 0.95, "answer_rate": 0.95, "fte_rounding": "none"}
        cache = {}
        with mock.patch.object(calculations, "_engine_line_profile_for_date",
                               side_effect=RuntimeError('relation "resource_daily_forecasts" does not exist')):
            profile = calculations._compute_forecast_profile_for_date_tx(cursor, date(2026, 10, 13), settings, cache)
        statements = [c.args[0] for c in cursor.execute.call_args_list]
        self.assertEqual(statements[0], "SAVEPOINT resource_engine")
        self.assertEqual(statements[1], "ROLLBACK TO SAVEPOINT resource_engine")
        self.assertNotIn("engine", profile)
        self.assertTrue(cache["failed"])


class LineIntegrationTests(unittest.TestCase):
    def test_engine_profile_replaces_the_old_rule(self):
        stub = {"weekday": 1, "history_dates": [], "history_count": 0, "insufficient_history": False,
                "avg_daily_calls": 100.0, "aht_seconds": 245.0, "forecast_aht_seconds": 245.0, "daily_fte": 12.0,
                "hourly_profile": [], "engine": {"method": "timesfm_dom"}}
        cursor = mock.MagicMock()
        with mock.patch.object(calculations, "_engine_line_profile_for_date", return_value=stub):
            profile = calculations._compute_forecast_profile_for_date_tx(cursor, date(2026, 10, 13), {})
        self.assertEqual(profile["engine"]["method"], "timesfm_dom")
        self.assertEqual(profile["forecast_date"], "2026-10-13")
        statements = [c.args[0] for c in cursor.execute.call_args_list]
        self.assertEqual(statements, ["SAVEPOINT resource_engine", "RELEASE SAVEPOINT resource_engine"])

    def test_prefetched_period_needs_no_savepoint_per_day(self):
        stub = {"weekday": 1, "hourly_profile": [], "engine": {"method": "timesfm_dom"}}
        cursor = mock.MagicMock()
        cache = {"forecast_range": (date(2026, 10, 12), date(2026, 10, 18))}
        with mock.patch.object(calculations, "_engine_line_profile_for_date", return_value=stub):
            calculations._compute_forecast_profile_for_date_tx(cursor, date(2026, 10, 13), {}, cache)
        cursor.execute.assert_not_called()

    def test_engine_switched_off_costs_nothing(self):
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = []
        settings = {"forecast_engine": "legacy", "occ": 0.7, "ur": 0.95, "answer_rate": 0.95, "fte_rounding": "none"}
        with mock.patch.object(calculations, "_engine_line_profile_for_date") as engine:
            calculations._compute_forecast_profile_for_date_tx(cursor, date(2026, 10, 13), settings, {})
        engine.assert_not_called()
        self.assertNotIn("SAVEPOINT resource_engine", [c.args[0] for c in cursor.execute.call_args_list])

    def test_old_rule_when_engine_has_nothing(self):
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = []
        settings = {"occ": 0.7, "ur": 0.95, "answer_rate": 0.95, "fte_rounding": "none"}
        with mock.patch.object(calculations, "_engine_line_profile_for_date", return_value=None):
            profile = calculations._compute_forecast_profile_for_date_tx(cursor, date(2026, 10, 13), settings)
        self.assertNotIn("engine", profile)
        self.assertTrue(profile["insufficient_history"])

    def test_hourly_forecast_keeps_engine_days(self):
        from resource_fte import service
        engine_day = {"forecast_date": "2026-10-13", "history_dates": [], "engine": {"method": "timesfm_dom"},
                      "hourly_profile": [{"hour": 9, "fte": 3.2}, {"hour": 10, "fte": 4.1}]}
        empty_day = {"forecast_date": "2026-10-14", "history_dates": [], "hourly_profile": [{"hour": 9, "fte": 0.0}]}
        with mock.patch.object(service, "_get_settings_tx", return_value={}), \
                mock.patch.object(service, "_compute_period_forecast_profiles_tx", return_value=[engine_day, empty_day]):
            out = service.get_resource_hourly_forecast(_ScriptedDb(), date(2026, 10, 13), date(2026, 10, 14))
        self.assertEqual(out["2026-10-13"], {9: 3.2, 10: 4.1})
        self.assertEqual(out["2026-10-14"], {})

    def _settings(self):
        return {"forecast_engine": "timesfm", "occ": 0.7, "ur": 0.95, "answer_rate": 0.95, "shrinkage_coeff": 0.9,
                "weekly_hours_per_operator": 40.0, "shift_rounding": "ceil"}

    def test_payload_summarises_engine_days(self):
        def day_profile(calls, sl, ar, below=False):
            return {"weekday": 1, "forecast_date": "2026-10-13", "avg_daily_calls": calls, "daily_fte": 10.0,
                    "aht_seconds": 245.0, "hourly_profile": [
                        {"hour": 10, "avg_calls": calls, "aht_seconds": 245.0, "workload_minutes": 1.0,
                         "fte": 10.0, "engine_agents": 9, "engine_sl": sl}],
                    "engine": {"method": "timesfm_dom", "method_label": "TimesFM + цикл месяца", "made_on": "2026-10-08",
                               "calls_low": calls * 0.8, "calls_high": calls * 1.2, "day_sl": sl, "day_ar": ar,
                               "ar_below_band": below, "aht_seconds": 245.0, "patience_seconds": 210.0,
                               "targets": {"sl_target": 0.8}}}
        payload = calculations._build_forecast_payload(
            date(2026, 10, 13), date(2026, 10, 14),
            [day_profile(1000, 0.85, 0.04), day_profile(3000, 0.81, 0.02, below=True)], self._settings())
        summary = payload["forecastEngine"]
        self.assertEqual(summary["days"], 2)
        self.assertAlmostEqual(summary["period_sl"], (1000 * 0.85 + 3000 * 0.81) / 4000, places=4)
        self.assertEqual(summary["calls"], 4000)
        self.assertEqual(summary["below_ar_min_days"], 1)

    def test_summary_takes_the_newest_run(self):
        def engine_day(day, made_on, aht):
            return {"weekday": 1, "forecast_date": day, "avg_daily_calls": 1000.0, "daily_fte": 10.0, "aht_seconds": aht,
                    "hourly_profile": [{"hour": 10, "avg_calls": 1000.0, "aht_seconds": aht, "workload_minutes": 1.0,
                                        "fte": 10.0, "engine_agents": 9, "engine_sl": 0.85}],
                    "engine": {"method": "timesfm_dom", "method_label": "TimesFM + цикл месяца", "made_on": made_on,
                               "calls_low": 800.0, "calls_high": 1200.0, "day_sl": 0.85, "day_ar": 0.04,
                               "aht_seconds": aht, "patience_seconds": 200.0, "params_measured_on": made_on,
                               "targets": {"sl_target": 0.8}}}
        payload = calculations._build_forecast_payload(
            date(2026, 10, 1), date(2026, 10, 2),
            [engine_day("2026-10-01", "2026-09-28", 241.0), engine_day("2026-10-02", "2026-10-01", 231.0)], self._settings())
        summary = payload["forecastEngine"]
        self.assertEqual((summary["made_on"], summary["aht_seconds"], summary["params_measured_on"]), ("2026-10-01", 231.0, "2026-10-01"))

    def test_overview_fact_of_engine_days_uses_the_same_model(self):
        from resource_fte import service
        engine_day, legacy_day = date(2026, 10, 7), date(2026, 10, 6)
        calls = np.array([0.0] * 7 + [60.0] * 14 + [10.0] * 3)

        def fake_prefetch(cursor, date_from, date_to, settings, cache):
            # The prefetch holds the weeks BEFORE a forecast day only, not the day itself.
            cache.update({"forecasts": {engine_day: {}}, "hours": {}, "forecast_range": (date_from, date_to)})

        history = [{"report_date": engine_day.isoformat(), "actual_report_fte_total": 99.0},
                   {"report_date": legacy_day.isoformat(), "actual_report_fte_total": 88.0}]
        settings = self._settings()
        with mock.patch.object(service, "_prefetch_engine_tx", side_effect=fake_prefetch), \
                mock.patch.object(service, "_engine_load_hours",
                                  return_value={engine_day: (calls, 0.0), legacy_day: (calls, 0.0)}) as load_hours, \
                mock.patch.object(service, "_compute_forecast_profile_for_date_tx",
                                  return_value={"engine": {"aht_seconds": 245.0, "patience_seconds": 210.0}}):
            service._apply_engine_fact_to_history_tx(mock.MagicMock(), history, settings)
        expected = forecast_engine.staffing_for_day(list(calls), {"aht_seconds": 245.0, "patience_seconds": 210.0}, settings)
        self.assertAlmostEqual(history[0]["actual_report_fte_total"], sum(h["agents"] for h in expected["hours"]) / 0.95)
        self.assertEqual(history[0]["actual_workload_report_fte_total"], 99.0)
        self.assertEqual(history[1]["actual_report_fte_total"], 88.0)
        self.assertNotIn("actual_workload_report_fte_total", history[1])
        # The days' own hours, up to and including the newest history day.
        self.assertEqual(load_hours.call_args.args[1:], (legacy_day, engine_day + timedelta(days=1)))

    def _engine_profile(self, day, shares=None):
        settings = self._settings()
        history = [("2026-10-08", {"aht_seconds": 245, "patience_seconds": 210, "made_on": "2026-10-08"})]
        forecast = {day: _engine_day(day, calls=1500.0)}
        with mock.patch.object(forecast_engine, "schema_ready", return_value=True), \
                mock.patch.object(forecast_engine, "params_history", return_value=history), \
                mock.patch.object(forecast_engine, "daily_forecast", return_value=forecast), \
                mock.patch.object(forecast_engine, "hourly_shares", return_value=shares or _shares()):
            profile = calculations._compute_forecast_profile_for_date_tx(mock.MagicMock(), day, settings, {})
        self.assertIn("engine", profile)
        return profile

    def test_incident_wave_is_staffed_by_erlang_a(self):
        day = date(2026, 10, 13)
        shares = _shares()
        shares[2] = 0.0005  # about three quarters of a call at 02:00
        profile = self._engine_profile(day, [x / sum(shares) for x in shares])
        incident = {"forecast_window_start": day.isoformat(),
                    "hourly": [{"hour": 2, "growth_ratio": 1.0}, {"hour": 12, "growth_ratio": 0.3}]}
        payload = calculations._build_forecast_payload(day, day, [profile], self._settings(),
                                                       incident_uplift_profile=incident)
        hours = {row["hour"]: row for row in payload["days"][0]["hourly_forecast"]}
        # A doubled quiet hour (under one call) needs nobody more...
        self.assertLess(hours[2]["forecast_calls"], 2)
        self.assertEqual(hours[2]["incident_uplift_fte"], 0)
        # ...a busy hour with 30 % more calls needs whole people, counted by Erlang A.
        extra_agents = hours[12]["incident_uplift_fte"] * 0.95
        self.assertGreater(extra_agents, 0)
        self.assertAlmostEqual(extra_agents, round(extra_agents))

    def test_fact_of_an_engine_day_is_measured_by_the_same_model(self):
        day = date(2026, 10, 13)
        profile = self._engine_profile(day)
        actual_calls = {hour: round(row["avg_calls"] * 1.1) for hour, row in
                        ((r["hour"], r) for r in profile["hourly_profile"])}
        actual = {day.isoformat(): {
            "has_actual_report": True, "actual_report_fte": 50.0,
            "hourly": {hour: {"has_actual_report": True, "actual_received_calls": calls, "actual_report_fte": 2.0}
                       for hour, calls in actual_calls.items()},
        }}
        payload = calculations._build_forecast_payload(day, day, [profile], self._settings(),
                                                       actual_resource_by_day=actual)
        result = payload["days"][0]
        expected = forecast_engine.staffing_for_day([actual_calls[h] for h in range(24)],
                                                    {"aht_seconds": 245, "patience_seconds": 210}, self._settings())
        self.assertAlmostEqual(result["actual_report_fte"], sum(h["agents"] for h in expected["hours"]) / 0.95)
        self.assertEqual(result["actual_workload_report_fte"], 50.0)
        noon = next(row for row in result["hourly_forecast"] if row["hour"] == 12)
        self.assertAlmostEqual(noon["actual_report_fte"], expected["hours"][12]["agents"] / 0.95)
        self.assertEqual(noon["actual_workload_report_fte"], 2.0)
        self.assertAlmostEqual(result["actual_forecast_fte_delta"], result["actual_report_fte"] - result["forecast_daily_fte"])
        # 10 % more calls than forecast -> more people needed, not the model gap of the old «fact».
        self.assertGreater(result["actual_forecast_fte_delta"], 0)


class RouteGuardTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8")
        start = self.source.index("# ── Прогноз звонков по отделам (TimesFM в BigQuery)")
        self.block = self.source[start:self.source.index("@app.route('/api/shift_auction/test_access'", start)]

    def test_nightly_and_manual_runs_stay_off_the_shared_pool(self):
        self.assertIn("ThreadPoolExecutor(max_workers=1, thread_name_prefix='resource-forecast')", self.block)
        self.assertIn("run_in_executor(resource_forecast_pool, _resource_engine_run", self.source)
        self.assertNotIn("run_in_executor(None, lambda: forecast_engine.run_all", self.source)

    def test_unknown_department_is_refused_not_run_for_all(self):
        run_route = self.block[self.block.index("def api_resource_fte_engine_run"):]
        self.assertIn('return jsonify({"error": "Неизвестный отдел"}), 400', run_route[:1500])

    def test_routes_create_the_schema_before_touching_tables(self):
        for name in ("def api_resource_fte_engine()", "def api_resource_fte_engine_run", "def api_resource_fte_engine_adjustments()",
                     "def api_resource_fte_engine_adjustment_delete"):
            body = self.block[self.block.index(name):]
            body = body[:body.index("\n@app.route") if "\n@app.route" in body else len(body)]
            self.assertIn("_resource_engine_schema_response()", body, name)


class FrontendGuardTests(unittest.TestCase):
    def test_engine_ui_uses_site_pickers_and_hints(self):
        source = (ROOT / "src/components/resources/ResourceForecastEngine.jsx").read_text(encoding="utf-8")
        self.assertNotIn('type="date"', source)
        self.assertIn("IosDateRangePicker", source)
        self.assertIn("InfoHint", source)
        self.assertIn("IosSegmented", source)

    def test_hook_watches_runs_it_did_not_start_and_guards_double_clicks(self):
        source = (ROOT / "src/components/resources/ResourceForecastEngine.jsx").read_text(encoding="utf-8")
        # A run going on when the screen opens (nightly, another planner) is watched until it ends.
        self.assertIn("if (data?.run_state?.department_busy && !activeRef.current) waitForRunRef.current?.(data.last_run, { quiet: true });", source)
        # Past the wait limit the screen keeps watching instead of leaving the button busy.
        self.assertIn("pollRef.current = setTimeout(tick, watching ? QUIET_POLL_MS : RUN_POLL_MS);", source)
        self.assertIn("if (!enabled || busy || startingRef.current) return;", source)
        # Only in-range values reach the draft while typing; leaving a field empty restores it.
        self.assertIn("if (parsed !== null && parsed >= min && parsed <= max) onCommit(toStored(parsed));", source)

    def test_overview_measures_the_fact_of_engine_days_by_the_same_model(self):
        import inspect
        from resource_fte import service
        self.assertIn("_apply_engine_fact_to_history_tx(cursor, history, settings)", inspect.getsource(service.get_resource_overview))

    def test_engine_cards_are_line_only(self):
        source = (ROOT / "src/components/resources/ResourceFteView.jsx").read_text(encoding="utf-8")
        self.assertIn("enabled: !isChat", source)
        self.assertRegex(source, r"activeDashboardView === 'next_week' && !isChat && \(\s*<EngineForecastCard")


if __name__ == "__main__":
    unittest.main()
