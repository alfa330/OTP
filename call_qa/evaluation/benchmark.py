"""Deterministic metrics for paired RAG-off/on production experiments."""
from __future__ import annotations

from datetime import datetime
import math
from statistics import mean


def _safe_div(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _pct(value: float | None) -> float | None:
    return round(value * 100, 2) if value is not None else None


def _percentile(values, percentile: float) -> float | None:
    data = sorted(float(value) for value in values if value is not None)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    position = (len(data) - 1) * percentile
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return data[lower]
    return data[lower] + (data[upper] - data[lower]) * (position - lower)


def classification_metrics(gold: list[str], predicted: list[str]) -> dict:
    """Binary alarm metrics where ``Incorrect`` is the safety-critical positive."""
    if len(gold) != len(predicted):
        raise ValueError("gold and predicted lengths differ")
    tp = fp = fn = tn = 0
    exact = 0
    for expected, actual in zip(gold, predicted):
        if expected not in ("Correct", "Incorrect", "N/A"):
            continue
        if actual not in ("Correct", "Incorrect", "N/A"):
            continue
        exact += expected == actual
        expected_alarm, actual_alarm = expected == "Incorrect", actual == "Incorrect"
        if expected_alarm and actual_alarm:
            tp += 1
        elif not expected_alarm and actual_alarm:
            fp += 1
        elif expected_alarm and not actual_alarm:
            fn += 1
        else:
            tn += 1
    total = tp + fp + fn + tn
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = (_safe_div(2 * precision * recall, precision + recall)
          if precision is not None and recall is not None else None)
    return {
        "n": total, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "alarm_precision_pct": _pct(precision), "recall_pct": _pct(recall),
        "f1_pct": _pct(f1), "accuracy_pct": _pct(_safe_div(exact, total)),
        "false_alarms": fp, "misses": fn,
    }


def retrieval_metrics(records: list[dict], *, k: int = 3) -> dict:
    """Macro Recall@K, Precision@K, MRR and false-hit rate."""
    recalls, precisions, reciprocal_ranks = [], [], []
    no_answer_queries = false_hits = 0
    for record in records:
        relevant = {str(value) for value in record.get("relevant_rule_ids") or []}
        hits = [str(value) for value in (record.get("hit_rule_ids") or [])[:k]]
        matched = [index for index, value in enumerate(hits, 1) if value in relevant]
        if relevant:
            recalls.append(len(set(hits) & relevant) / len(relevant))
            precisions.append(len(set(hits) & relevant) / max(1, len(hits)))
            reciprocal_ranks.append(1 / matched[0] if matched else 0)
        else:
            no_answer_queries += 1
            if hits:
                false_hits += 1
    latencies = [record.get("retrieval_ms") for record in records
                 if record.get("retrieval_ms") is not None]
    return {
        "queries": len(records), "labelled_queries": len(recalls), "k": int(k),
        "recall_at_k_pct": _pct(mean(recalls)) if recalls else None,
        "precision_at_k_pct": _pct(mean(precisions)) if precisions else None,
        "mrr": round(mean(reciprocal_ranks), 4) if reciprocal_ranks else None,
        "no_answer_queries": no_answer_queries,
        "false_hit_rate_pct": _pct(_safe_div(false_hits, no_answer_queries)),
        "false_hits": false_hits,
        "latency_p50_ms": round(_percentile(latencies, .5), 2) if latencies else None,
        "latency_p95_ms": round(_percentile(latencies, .95), 2) if latencies else None,
    }


def _mae(examples: list[dict], key: str) -> float | None:
    errors = [abs(float(item[key]) - float(item["gold_score"])) for item in examples
              if item.get(key) is not None and item.get("gold_score") is not None]
    return round(mean(errors), 3) if errors else None


def paired_rag_report(examples: list[dict], *, retrieval_records=None, k: int = 3) -> dict:
    """Compare identical calls/configs where only RAG enabledness differs."""
    usable = [item for item in examples
              if item.get("gold_verdict") in ("Correct", "Incorrect", "N/A")
              and item.get("off_verdict") in ("Correct", "Incorrect", "N/A")
              and item.get("on_verdict") in ("Correct", "Incorrect", "N/A")]
    gold = [item["gold_verdict"] for item in usable]
    off = [item["off_verdict"] for item in usable]
    on = [item["on_verdict"] for item in usable]
    off_metrics = classification_metrics(gold, off)
    on_metrics = classification_metrics(gold, on)
    changed = improved = harmed = 0
    for item in usable:
        before = item["off_verdict"] == item["gold_verdict"]
        after = item["on_verdict"] == item["gold_verdict"]
        if item["off_verdict"] != item["on_verdict"]:
            changed += 1
        if not before and after:
            improved += 1
        elif before and not after:
            harmed += 1
    latency_delta = [(item.get("on_latency_ms") or 0) - (item.get("off_latency_ms") or 0)
                     for item in usable if item.get("on_latency_ms") is not None
                     and item.get("off_latency_ms") is not None]
    input_delta = [(item.get("on_input_tokens") or 0) - (item.get("off_input_tokens") or 0)
                   for item in usable if item.get("on_input_tokens") is not None
                   and item.get("off_input_tokens") is not None]
    cost_delta = [(item.get("on_cost") or 0) - (item.get("off_cost") or 0)
                  for item in usable if item.get("on_cost") is not None
                  and item.get("off_cost") is not None]
    def delta(metric):
        left, right = off_metrics.get(metric), on_metrics.get(metric)
        return round(right - left, 2) if left is not None and right is not None else None
    return {
        "pairs": len(usable), "off": off_metrics, "on": on_metrics,
        "delta": {"alarm_precision_pp": delta("alarm_precision_pct"),
                  "recall_pp": delta("recall_pct"), "f1_pp": delta("f1_pct"),
                  "false_alarms": on_metrics["false_alarms"] - off_metrics["false_alarms"],
                  "misses": on_metrics["misses"] - off_metrics["misses"]},
        "changed": changed, "improved": improved, "harmed": harmed,
        "changed_pct": _pct(_safe_div(changed, len(usable))),
        "improved_pct": _pct(_safe_div(improved, len(usable))),
        "harmed_pct": _pct(_safe_div(harmed, len(usable))),
        "score": {"off_mae": _mae(usable, "off_score"),
                  "on_mae": _mae(usable, "on_score")},
        "efficiency": {
            "latency_delta_p50_ms": round(_percentile(latency_delta, .5), 2) if latency_delta else None,
            "latency_delta_p95_ms": round(_percentile(latency_delta, .95), 2) if latency_delta else None,
            "mean_input_token_delta": round(mean(input_delta), 2) if input_delta else None,
            "mean_cost_delta": round(mean(cost_delta), 6) if cost_delta else None,
        },
        "retrieval": retrieval_metrics(retrieval_records or [], k=k),
    }


def validate_temporal_split(*, knowledge_cutoff_at: datetime, rule_created_at: datetime,
                            call_created_at: datetime):
    """Reject train/test leakage before an experiment is allowed to run."""
    if rule_created_at > knowledge_cutoff_at:
        raise ValueError("rule was created after the knowledge cutoff")
    if call_created_at <= knowledge_cutoff_at:
        raise ValueError("evaluation call must be strictly after the knowledge cutoff")


def evaluate_quality_gates(report: dict, gates: dict | None = None) -> dict:
    gates = {"alarm_precision_gain_pp": 10, "max_recall_drop_pp": 2,
             "max_false_hit_rate": 0.05, "max_p95_retrieval_ms": 500,
             "min_pairs": 30, **(gates or {})}
    delta = report.get("delta") or {}
    retrieval = report.get("retrieval") or {}
    efficiency = report.get("efficiency") or {}
    checks = {
        "sample_size": int(report.get("pairs") or 0) >= int(gates["min_pairs"]),
        "alarm_precision": (delta.get("alarm_precision_pp") is not None and
                            delta["alarm_precision_pp"] >= gates["alarm_precision_gain_pp"]),
        "recall": (delta.get("recall_pp") is not None and
                   delta["recall_pp"] >= -gates["max_recall_drop_pp"]),
        "false_hit_rate": (retrieval.get("false_hit_rate_pct") is not None and
                           retrieval["false_hit_rate_pct"] <= gates["max_false_hit_rate"] * 100),
        "retrieval_p95": (retrieval.get("latency_p95_ms") is not None and
                          retrieval["latency_p95_ms"] <= gates["max_p95_retrieval_ms"]),
    }
    return {"passed": all(checks.values()), "checks": checks, "gates": gates}
