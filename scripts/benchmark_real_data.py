"""Benchmark schedule_generation on real readonly data.

Runs the same pipeline as `build_resource_schedule_preview` but against
DATABASE_URL_READONLY from .env.codex.local. Used to measure deficit /
over-coverage / runtime on the actual production-shaped period.

Usage:
    python scripts/benchmark_real_data.py [start_date end_date]
Default period: 2026-05-25 .. 2026-05-31.
"""
import os
import sys
import time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load_env(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_env(os.path.join(ROOT, ".env.codex.local"))

import psycopg2  # noqa: E402

from resource_fte.calculations import (  # noqa: E402
    _build_forecast_payload,
    _compute_period_forecast_profiles_tx,
    _compute_recent_incident_uplift_profile_tx,
)
from resource_fte import schedule_generation  # noqa: E402
from resource_fte.schedule_generation import (  # noqa: E402
    _generate_schedule_preview_from_forecast,
    _normalize_shift_templates,
)
from resource_fte_service import (  # noqa: E402
    _current_operator_fte_tx,
    _get_settings_tx,
    _resource_work_shift_carry_in_tx,
)


def _install_profiling():
    """Wrap key functions with timing + per-strategy results."""
    stats = {"calls": [], "strategy_results": []}
    targets = [
        "_run_shift_preview_cp_sat_strategy",
        "_run_shift_preview_cp_sat_min_over_strategy",
        "_run_shift_preview_cp_sat_fixed_mix_refine_strategy",
        "_run_shift_preview_greedy_strategy",
        "_shift_preview_improve_selected",
        "_shift_preview_prune_selected",
        "_build_shift_preview_candidates",
        "_build_freeform_shift_templates",
    ]
    tracked = {
        "_run_shift_preview_greedy_strategy",
        "_run_shift_preview_cp_sat_strategy",
        "_run_shift_preview_cp_sat_min_over_strategy",
        "_run_shift_preview_cp_sat_fixed_mix_refine_strategy",
        "_shift_preview_improve_selected",
    }
    for name in targets:
        original = getattr(schedule_generation, name, None)
        if original is None:
            continue

        def make_wrapper(fn, label):
            def wrapper(*args, **kwargs):
                t0 = time.perf_counter()
                result = None
                try:
                    result = fn(*args, **kwargs)
                    return result
                finally:
                    dur = time.perf_counter() - t0
                    stats["calls"].append((label, dur))
                    if label in tracked and isinstance(result, dict):
                        totals = result.get("totals") or {}
                        source = (
                            (result.get("selected") or [{}])[0].get("source")
                            if result.get("selected")
                            else None
                        )
                        stats["strategy_results"].append(
                            {
                                "stage": label,
                                "method": result.get("method"),
                                "source": source,
                                "deficit": float(totals.get("deficitFteHours") or 0),
                                "over": float(totals.get("overFteHours") or 0),
                                "shifts": len(result.get("selected") or []),
                                "elapsed": dur,
                            }
                        )
            return wrapper

        setattr(schedule_generation, name, make_wrapper(original, name))
    return stats


def _parse_date(value):
    return date(*[int(part) for part in value.split("-")])


def _fetch_inputs(period_start, period_end):
    url = os.environ.get("DATABASE_URL_READONLY")
    if not url:
        raise SystemExit("DATABASE_URL_READONLY is not set (check .env.codex.local)")
    conn = psycopg2.connect(url)
    try:
        cursor = conn.cursor()
        try:
            settings = _get_settings_tx(cursor)
            cursor.execute("SELECT CURRENT_DATE")
            incident_anchor_date = cursor.fetchone()[0]
            profiles = _compute_period_forecast_profiles_tx(
                cursor, period_start, period_end, settings
            )
            operator_capacity = _current_operator_fte_tx(cursor, settings)
            current_fte = operator_capacity.get("current_operator_fte", 0.0)
            incident_uplift_profile = _compute_recent_incident_uplift_profile_tx(
                cursor, incident_anchor_date, settings
            )
            carry_in_shifts = _resource_work_shift_carry_in_tx(
                cursor, period_start, settings
            )
            forecast_payload = _build_forecast_payload(
                period_start,
                period_end,
                profiles,
                settings,
                current_operator_fte=current_fte,
                incident_uplift_profile=incident_uplift_profile,
            )
        finally:
            cursor.close()
    finally:
        conn.close()
    return forecast_payload, operator_capacity, carry_in_shifts


def _format_rate_mix(rates):
    lines = []
    total_ops = sum(int(r.get("count") or 0) for r in rates) or 1
    total_shifts = sum(int(r.get("weeklyShiftsUsed") or 0) for r in rates) or 1
    for r in rates:
        ops = int(r.get("count") or 0)
        shifts = int(r.get("weeklyShiftsUsed") or 0)
        lines.append(
            f"  rate={r.get('rate'):<5} ops={ops:>3} ({ops / total_ops * 100:5.2f}%)"
            f"  shifts={shifts:>4} ({shifts / total_shifts * 100:5.2f}%)"
        )
    return "\n".join(lines)


def main():
    if len(sys.argv) >= 3:
        period_start = _parse_date(sys.argv[1])
        period_end = _parse_date(sys.argv[2])
    else:
        period_start = date(2026, 5, 25)
        period_end = date(2026, 5, 31)

    print(f"period: {period_start} .. {period_end}")
    print("loading inputs...")
    t0 = time.perf_counter()
    forecast_payload, operator_capacity, carry_in_shifts = _fetch_inputs(
        period_start, period_end
    )
    print(f"  inputs loaded in {time.perf_counter() - t0:.2f}s")

    templates = _normalize_shift_templates(None)

    profile_stats = _install_profiling()
    print("generating preview...")
    t0 = time.perf_counter()
    preview = _generate_schedule_preview_from_forecast(
        forecast_payload,
        templates,
        operator_capacity,
        carry_in_shifts=carry_in_shifts,
    )
    elapsed = time.perf_counter() - t0
    summary = preview["summary"]
    needed = float(summary.get("neededFteHours") or 0)
    deficit = float(summary.get("deficitFteHours") or 0)
    over = float(summary.get("overFteHours") or 0)
    coverage = float(summary.get("coveragePercent") or 0)

    print(f"\nelapsed:         {elapsed:.2f}s")
    print(f"selectedVariant: {preview.get('selectedVariant')}")
    print(f"needed:          {needed:.2f} FTE-h")
    print(f"deficit:         {deficit:.2f} FTE-h")
    print(f"over:            {over:.2f} FTE-h")
    print(f"coverage:        {coverage:.2f}%")
    print("rate mix (ops vs shifts):")
    print(_format_rate_mix(preview["capacity"]["rates"]))

    print("\nper-variant:")
    for variant in preview.get("variants") or []:
        s = variant.get("summary") or {}
        g = variant.get("generation") or {}
        print(
            f"  {variant.get('key'):<30} "
            f"deficit={float(s.get('deficitFteHours') or 0):7.2f}  "
            f"over={float(s.get('overFteHours') or 0):7.2f}  "
            f"shifts={g.get('shiftCount')}  method={g.get('method')}  "
            f"quality={g.get('qualityScore')}"
        )

    print("\nprofile (sum of durations per function):")
    by_name = {}
    for name, dur in profile_stats["calls"]:
        entry = by_name.setdefault(name, [0, 0.0])
        entry[0] += 1
        entry[1] += dur
    for name, (count, total) in sorted(by_name.items(), key=lambda kv: -kv[1][1]):
        print(f"  {name:<45} calls={count:>3}  total={total:6.2f}s  avg={total / count:.2f}s")

    print("\nper-strategy raw results:")
    for r in profile_stats["strategy_results"]:
        stage_label = r["stage"].replace("_run_shift_preview_", "").replace("_shift_preview_", "")
        print(
            f"  {stage_label:<32} {str(r['method']):<22} source={str(r['source']):<10} "
            f"deficit={r['deficit']:6.2f}  over={r['over']:7.2f}  shifts={r['shifts']:>3}  "
            f"elapsed={r['elapsed']:.2f}s"
        )


if __name__ == "__main__":
    main()
