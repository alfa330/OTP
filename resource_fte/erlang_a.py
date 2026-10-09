"""Erlang A (M/M/N+M): how many agents a queue needs when callers can hang up.

Why Erlang A and not the old «workload / occupancy» formula: the targets are a service
level (answered within T seconds, over the day) and a band of lost calls (AR). Both are
queueing outcomes; a fixed occupancy cannot hit them, and Erlang C ignores the callers
who give up, which on this line are 3-15 % of all calls.

Model: Poisson arrivals (lam per second), exponential handle time (mean `aht`), exponential
patience (mean `patience`), N agents. Stationary distribution of the birth-death chain,
then for a caller who finds k >= N calls in the system the chance to be answered within T
(uniformization of the small chain «position in queue -> answered / gave up»).

SL here is the wallboard definition: answered within T over ALL calls that reached the
queue (lost calls included). AR is lost over all calls.

Only numpy: scipy is not a production dependency.
"""
import math
from functools import lru_cache
from typing import Dict, List, Sequence, Tuple

import numpy as np

_POISSON_TAIL = 1e-10


def _stationary(lam: float, mu: float, theta: float, n: int, k_max: int) -> np.ndarray:
    logp = np.empty(k_max + 1)
    logp[0] = 0.0
    log_lam = math.log(lam)
    for k in range(1, k_max + 1):
        death = mu * min(k, n) + theta * max(k - n, 0)
        logp[k] = logp[k - 1] + log_lam - math.log(death)
    logp -= logp.max()
    p = np.exp(logp)
    return p / p.sum()


def _answer_probabilities(mu: float, theta: float, n: int, j_max: int, t: float) -> Tuple[np.ndarray, np.ndarray]:
    """For a caller entering at queue position j = 1..j_max:
    within[j-1] = P(answered within t), ever[j-1] = P(answered at all)."""
    pos = np.arange(1, j_max + 1, dtype=float)
    up = n * mu + (pos - 1.0) * theta          # someone ahead leaves or an agent frees up
    out = up + theta                          # ... or this caller gives up
    ever = np.cumprod(up / out)
    rate = float(out.max())
    stay = 1.0 - out / rate
    step = up / rate
    # Poisson weights of the uniformized chain up to the tail we care about.
    mean = rate * t
    n_max = int(mean + 12.0 * math.sqrt(mean + 1.0) + 20)
    k = np.arange(n_max + 1, dtype=float)
    log_w = -mean + k * (math.log(mean) if mean > 0 else 0.0) - np.array([math.lgamma(x + 1.0) for x in k])
    weights = np.exp(log_w)
    s = np.zeros(j_max)                        # P(answered within m steps | start at position j)
    within = np.zeros(j_max)
    for m in range(n_max + 1):
        within += weights[m] * s
        if weights[m] < _POISSON_TAIL and m > mean:
            break
        shifted = np.empty_like(s)
        shifted[0] = 1.0                       # position 1 moves straight to an agent
        shifted[1:] = s[:-1]
        s = stay * s + step * shifted
    return np.minimum(within, ever), ever


@lru_cache(maxsize=200_000)
def _erlang_a_cached(calls_milli: int, aht_ms: int, patience_ms: int, n: int, t_ms: int) -> Tuple[float, float, float, float]:
    calls_per_hour = calls_milli / 1000.0
    aht = aht_ms / 1000.0
    patience = patience_ms / 1000.0
    t = t_ms / 1000.0
    lam = calls_per_hour / 3600.0
    mu = 1.0 / aht
    theta = 1.0 / patience
    # How far the queue can realistically grow before its probability is negligible: the
    # chance of one more caller falls as lam / (n*mu + j*theta) — geometrically once the
    # agents alone keep up, and below one half once j*theta > 2*lam. The tighter of the
    # two bounds, never shorter than 60 places and never longer than 3000.
    if n * mu > lam:
        geometric = 12.0 * math.log(10.0) / math.log(n * mu / lam) + 20.0
    else:
        geometric = float("inf")
    by_patience = 2.0 * lam / theta + 60.0
    k_max = int(n + min(3000.0, max(60.0, min(geometric, by_patience))))
    p = _stationary(lam, mu, theta, n, k_max)
    j_max = k_max - n + 1
    within, ever = _answer_probabilities(mu, theta, n, j_max, t)
    p_free = float(p[:n].sum())
    queue_p = p[n:k_max + 1]
    sl = p_free + float((queue_p * within[:len(queue_p)]).sum())
    answered = p_free + float((queue_p * ever[:len(queue_p)]).sum())
    busy = float((p * np.minimum(np.arange(k_max + 1), n)).sum())
    return sl, max(0.0, 1.0 - answered), busy / n, float(queue_p.sum())


def erlang_a(calls_per_hour: float, aht: float, patience: float, n: int, t: float = 20.0) -> Dict[str, float]:
    """Service level, abandon rate, occupancy and P(wait) for one hour."""
    # Calls are counted in thousandths; less than that is no call at all (and lam = 0 would
    # divide by zero below).
    calls_milli = int(round(calls_per_hour * 1000)) if calls_per_hour > 0 else 0
    if calls_milli <= 0:
        return {"sl": 1.0, "ar": 0.0, "occ": 0.0, "pw": 0.0}
    if n <= 0:
        return {"sl": 0.0, "ar": 1.0, "occ": 0.0, "pw": 1.0}
    if aht <= 0 or patience <= 0:
        raise ValueError("aht and patience must be positive")
    sl, ar, occ, pw = _erlang_a_cached(
        calls_milli, int(round(aht * 1000)), int(round(patience * 1000)),
        int(n), int(round(t * 1000)),
    )
    return {"sl": sl, "ar": ar, "occ": occ, "pw": pw}


def day_staffing(
    calls_by_hour: Sequence[float],
    aht: float,
    patience: float,
    sl_target: float = 0.80,
    sl_seconds: float = 20.0,
    ar_max: float = 0.05,
    hour_sl_floor: float = 0.60,
    max_occupancy: float = 0.85,
    min_agents: int = 1,
    max_agents: int = 400,
) -> Dict[str, object]:
    """Fewest agents per hour so that the whole DAY reaches SL >= sl_target and AR <= ar_max.

    Every hour with calls keeps a floor (at least `min_agents`, hourly SL >= hour_sl_floor,
    occupancy <= max_occupancy): a daily target alone would starve quiet hours. Above the
    floors agents go one at a time to the hour where they buy the most (answered in time
    plus not lost), which is the cheapest way to reach a daily target.
    """
    # Under a thousandth of a call an hour is no call: such an hour gets no agent.
    calls = [c if c >= 0.0005 else 0.0 for c in (max(0.0, float(c or 0.0)) for c in calls_by_hour)]
    agents: List[int] = []
    for c in calls:
        if c <= 0:
            agents.append(0)
            continue
        n = max(1, int(min_agents))
        while n < max_agents:
            m = erlang_a(c, aht, patience, n, sl_seconds)
            if m["sl"] >= hour_sl_floor and m["occ"] <= max_occupancy:
                break
            n += 1
        agents.append(n)
    total = sum(calls)

    def day_metrics():
        if total <= 0:
            return 1.0, 0.0
        sl = sum(c * erlang_a(c, aht, patience, agents[h], sl_seconds)["sl"] for h, c in enumerate(calls) if c > 0)
        ar = sum(c * erlang_a(c, aht, patience, agents[h], sl_seconds)["ar"] for h, c in enumerate(calls) if c > 0)
        return sl / total, ar / total

    sl, ar = day_metrics()
    guard = 0
    while (sl < sl_target or ar > ar_max) and guard < 24 * max_agents:
        guard += 1
        best_h, best_gain = None, -1.0
        for h, c in enumerate(calls):
            if c <= 0 or agents[h] >= max_agents:
                continue
            cur = erlang_a(c, aht, patience, agents[h], sl_seconds)
            nxt = erlang_a(c, aht, patience, agents[h] + 1, sl_seconds)
            gain = c * ((nxt["sl"] - cur["sl"]) + (cur["ar"] - nxt["ar"]))
            if gain > best_gain:
                best_h, best_gain = h, gain
        if best_h is None:
            break
        agents[best_h] += 1
        sl, ar = day_metrics()
    hours = []
    for h, c in enumerate(calls):
        m = erlang_a(c, aht, patience, agents[h], sl_seconds) if c > 0 else {"sl": 1.0, "ar": 0.0, "occ": 0.0, "pw": 0.0}
        hours.append({"hour": h, "calls": c, "agents": agents[h], "sl": m["sl"], "ar": m["ar"], "occupancy": m["occ"]})
    return {"agents": agents, "day_sl": sl, "day_ar": ar, "hours": hours}


def estimate_patience(abandoned: float, wait_answered_seconds: float, wait_abandoned_seconds: float) -> float:
    """Mean patience (seconds) by the standard estimate for exponential patience:
    callers who gave up / all time spent waiting (answered callers' waits are censored
    observations of their patience)."""
    total_wait = max(0.0, float(wait_answered_seconds or 0)) + max(0.0, float(wait_abandoned_seconds or 0))
    abandoned = max(0.0, float(abandoned or 0))
    if abandoned <= 0 or total_wait <= 0:
        return 0.0
    return total_wait / abandoned
