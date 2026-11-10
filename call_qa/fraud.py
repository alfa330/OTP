# -*- coding: utf-8 -*-
"""Триггеры фрода в звонках удалённого КЦ (задача SZOV-2, владелец 10.11.2026).

Задача от СЗоВ: «добавить триггеры на проверку качества для ИИ» — проверять,
не делают ли операторы лишнего, чтобы быстрее «обрабатывать» водителей. Оплата
удалённого КЦ — за диалог от 10 секунд (dial_list.pay), поэтому подозрительно
всё, что выглядит как разговор чуть длиннее этой границы. Четыре шаблона —
дословно из задачи:

  1. «2-3 звонка подряд по 11–20 секунд. Особенно когда оператор сам завершает
     разговор» — series;
  2. «Короткие диалоги (до 30 сек) с положительным статусом (Подписал в звонке,
     Сообщил, что подпишет, Подпишет в офисе)» — short_positive;
  3. «Необычно высокая доля разговоров длительностью 11–15 секунд у конкретного
     оператора относительно остальных» — share;
  4. «Повторяющийся сценарий: первый звонок на 11–12 секунд, сброс, повторный
     звонок и продолжение беседы» — redial.

ЧИСЛА, КОТОРЫХ В ЗАДАЧЕ НЕТ (сказаны владельцу 10.11.2026):
  * «подряд» — подряд идущие РАЗГОВОРЫ оператора за день: недозвон между ними
    серию не рвёт (это не разговор), разговор другой длины — рвёт;
  * «необычно высокая доля» — у оператора доля 11–15 с минимум вдвое выше, чем у
    всех остальных операторов того же дня вместе, таких разговоров не меньше трёх,
    и случайно такое бывает реже, чем в одном дне из двадцати (биномиальный хвост
    при доле остальных ≤ 0,05): на 3 из 48 против 3 % у остальных одной
    удвоенной доли мало — так выходит каждый шестой день просто по случайности;
  * «повторный звонок» — тому же водителю тем же оператором не позже 10 минут
    после начала первого, и это снова разговор.

ПОЧЕМУ СЧИТАЕТ КОД, А НЕ МОДЕЛЬ. Шаблоны — про длительности и порядок сотен
звонков дня, а модель уверенно ошибается в счёте (см. digest/data.py). Код
находит совпадения точно, модель в «Поиске фрода» объясняет их по словам самих
разговоров: звонки под триггерами ИИ слушает сверх случайной выборки
(владелец: «Да, сверх 30 случайных»).

Источник — журнал раздела «Удаленный КЦ» (dial_list_attempts): только там есть
длительность разговора, итог оператора и кто положил трубку. Разговор — тот же,
что у раздела: финальный ответ Binotel, не сообщение сети, не отменённая попытка.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

ALMATY = ZoneInfo("Asia/Almaty")

SERIES = "series"
SHORT_POSITIVE = "short_positive"
SHARE = "share"
REDIAL = "redial"
# Порядок важности — он же порядок разделов в данных модели и порядок, в котором
# звонки берутся на прослушку, если день упёрся в потолок.
KINDS = (SHORT_POSITIVE, REDIAL, SERIES, SHARE)

SERIES_MIN_S, SERIES_MAX_S = 11, 20
SERIES_MIN_LEN = 2
SHORT_DIALOG_MAX_S = 30
# Итоги звонка — справочник отдела (dial_list_outcomes), сверяются по названию.
# Переименуют итог — триггер 2 перестанет его видеть; тест держит этот список
# рядом с задачей, а сравнение — без регистра и лишних пробелов.
POSITIVE_OUTCOMES = ("Подписал в звонке", "Сообщил, что подпишет", "Подпишет в офисе")
SHARE_MIN_S, SHARE_MAX_S = 11, 15
SHARE_RATIO = 2.0
SHARE_MIN_COUNT = 3
SHARE_MAX_P = 0.05
REDIAL_FIRST_MIN_S, REDIAL_FIRST_MAX_S = 11, 12
REDIAL_WINDOW = timedelta(minutes=10)


def _norm(text) -> str:
    return " ".join(str(text or "").split()).casefold()


_POSITIVE = frozenset(_norm(name) for name in POSITIVE_OUTCOMES)


def is_positive(outcome) -> bool:
    return _norm(outcome) in _POSITIVE


def _by_operator(calls) -> dict:
    groups = defaultdict(list)
    for call in calls:
        groups[call["operator_id"]].append(call)
    for group in groups.values():
        group.sort(key=lambda c: (c["started_at"], c["id"]))
    return groups


def series(calls) -> list[dict]:
    """Шаблон 1: подряд идущие разговоры оператора по 11–20 с, от двух подряд."""
    out = []
    for operator_id, group in _by_operator(calls).items():
        run: list[dict] = []
        for call in group + [None]:
            if call is not None and SERIES_MIN_S <= call["billsec"] <= SERIES_MAX_S:
                run.append(call)
                continue
            if len(run) >= SERIES_MIN_LEN:
                out.append({"operator_id": operator_id, "operator": run[0]["operator"],
                            "calls": [c["id"] for c in run],
                            "operator_ended": sum(1 for c in run if c["operator_hung_up"])})
            run = []
    out.sort(key=lambda s: (-len(s["calls"]), -s["operator_ended"], s["operator"] or ""))
    return out


def short_positive(calls) -> list[dict]:
    """Шаблон 2: разговор до 30 с с итогом «подписал / подпишет»."""
    hits = [{"operator_id": c["operator_id"], "operator": c["operator"], "calls": [c["id"]]}
            for c in sorted(calls, key=lambda c: (c["started_at"], c["id"]))
            if c["billsec"] <= SHORT_DIALOG_MAX_S and is_positive(c.get("outcome"))]
    return hits


def binomial_tail(k: int, n: int, p: float) -> float:
    """P(X ≥ k) при X ~ Bin(n, p): насколько случайно у оператора столько коротких."""
    if k <= 0:
        return 1.0
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    return min(1.0, sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1)))


def share(calls) -> list[dict]:
    """Шаблон 3: у оператора доля разговоров 11–15 с необычно высока против остальных."""
    groups = _by_operator(calls)
    if len(groups) < 2:
        return []      # сравнивать не с кем
    short = {op: [c for c in group if SHARE_MIN_S <= c["billsec"] <= SHARE_MAX_S]
             for op, group in groups.items()}
    total_n = sum(len(g) for g in groups.values())
    total_k = sum(len(s) for s in short.values())
    out = []
    for operator_id, group in groups.items():
        n, k = len(group), len(short[operator_id])
        rest_n, rest_k = total_n - n, total_k - k
        if k < SHARE_MIN_COUNT or rest_n <= 0:
            continue
        rest_share = rest_k / rest_n
        own_share = k / n
        if own_share < SHARE_RATIO * rest_share:
            continue
        p_value = binomial_tail(k, n, rest_share)
        if p_value > SHARE_MAX_P:
            continue
        out.append({"operator_id": operator_id, "operator": group[0]["operator"],
                    "calls": [c["id"] for c in short[operator_id]],
                    "short": k, "talks": n, "rest_short": rest_k, "rest_talks": rest_n,
                    "p_value": p_value})
    out.sort(key=lambda s: (s["p_value"], s["operator"] or ""))
    return out


def redial(calls) -> list[dict]:
    """Шаблон 4: разговор 11–12 с, затем тот же оператор тому же водителю — снова разговор."""
    out = []
    for operator_id, group in _by_operator(calls).items():
        by_lead = defaultdict(list)
        for call in group:
            by_lead[call["lead_id"]].append(call)
        for lead_calls in by_lead.values():
            for first, second in zip(lead_calls, lead_calls[1:]):
                if not (REDIAL_FIRST_MIN_S <= first["billsec"] <= REDIAL_FIRST_MAX_S):
                    continue
                gap = second["started_at"] - first["started_at"]
                if timedelta(0) <= gap <= REDIAL_WINDOW:
                    out.append({"operator_id": operator_id, "operator": first["operator"],
                                "calls": [first["id"], second["id"]],
                                "gap_s": int(gap.total_seconds())})
    out.sort(key=lambda r: (r["operator"] or "", r["calls"]))
    return out


def find(calls) -> dict:
    """Все четыре шаблона по разговорам дня. calls — разговоры (load_day)."""
    calls = list(calls or [])
    return {SHORT_POSITIVE: short_positive(calls), REDIAL: redial(calls),
            SERIES: series(calls), SHARE: share(calls),
            "talks": len(calls), "operators": len({c["operator_id"] for c in calls})}


def flagged_calls(found: dict) -> list[tuple[str, list[str]]]:
    """[(id попытки, [шаблоны])] — звонки на прослушку, по важности шаблона."""
    order: list[str] = []
    reasons: dict[str, list[str]] = {}
    for kind in KINDS:
        for hit in found.get(kind) or []:
            for call_id in hit["calls"]:
                if call_id not in reasons:
                    reasons[call_id] = []
                    order.append(call_id)
                if kind not in reasons[call_id]:
                    reasons[call_id].append(kind)
    return [(call_id, reasons[call_id]) for call_id in order]


def public_stats(found: dict | None) -> dict | None:
    """Цифры над разделом «Поиск фрода»: сколько совпадений и у скольких операторов."""
    if not found:
        return None
    out = {"talks": int(found.get("talks") or 0), "operators": int(found.get("operators") or 0)}
    for kind in KINDS:
        hits = found.get(kind) or []
        out[kind] = {"hits": len(hits),
                     "calls": len({c for h in hits for c in h["calls"]}),
                     "operators": len({h["operator_id"] for h in hits})}
    out["flagged"] = len(flagged_calls(found))
    return out


# ── день из журнала удалённого КЦ ─────────────────────────────────────────────

def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, dtime.min, tzinfo=ALMATY)
    return start, start + timedelta(days=1)


def _talk_sql() -> str:
    """Разговор — как у раздела «Удаленный КЦ» (dial_list.service.ATTEMPT_TALK_SQL)."""
    from dial_list import service as dial_service
    return dial_service.ATTEMPT_TALK_SQL


def _answered() -> list[str]:
    from dial_list import service as dial_service
    return sorted(dial_service.ANSWERED_DISPOSITIONS)


def remote_cc_department_id(cur) -> int | None:
    from . import config
    found = config.department_ids_by_code(cur, [config.REMOTE_CC_DEPARTMENT_CODE])
    return found.get(config.REMOTE_CC_DEPARTMENT_CODE)


def load_day(cur, day: date, department_id: int | None = None) -> list[dict]:
    """Разговоры дня в базе удалённого КЦ — кто бы ни звонил (фронт-офисы, удалённые
    операторы, СЗоВ). Номера «Реестра тестовых номеров» не берутся."""
    from test_numbers import keys as test_keys
    if department_id is None:
        department_id = remote_cc_department_id(cur)
    if department_id is None:
        return []
    start, end = day_bounds(day)
    cur.execute(
        """SELECT t.id::text, t.operator_id, COALESCE(u.name, ''), a.lead_id::text,
                  l.phone_norm, t.requested_at, t.billsec, t.operator_hangup_at IS NOT NULL,
                  t.phone_ended_at IS NOT NULL, COALESCE(o.name, ''), t.general_call_id,
                  l.signed_at
             FROM dial_list_attempts t
             JOIN dial_list_assignments a ON a.id = t.assignment_id
             JOIN dial_list_leads l ON l.id = a.lead_id
             LEFT JOIN users u ON u.id = t.operator_id
             LEFT JOIN dial_list_outcomes o ON o.id = t.outcome_id
            WHERE l.department_id = %s
              AND t.requested_at >= %s AND t.requested_at < %s
              AND t.state = 'finished' AND UPPER(t.disposition) = ANY(%s)
              AND NOT t.cancelled AND t.billsec > 0
              AND """ + _talk_sql() + """
              AND """ + test_keys.sql_not_test("l.phone_norm") + """
            ORDER BY t.operator_id, t.requested_at, t.id""",
        (int(department_id), start, end, _answered()))
    calls = []
    for (attempt_id, operator_id, operator, lead_id, phone, started_at, billsec, op_hung_up,
         phone_ended, outcome, general_call_id, signed_at) in cur.fetchall():
        calls.append({
            "id": attempt_id, "operator_id": int(operator_id), "operator": operator.strip() or "—",
            "lead_id": lead_id, "phone": phone or "", "started_at": started_at,
            "billsec": int(billsec or 0), "operator_hung_up": bool(op_hung_up),
            # Кто положил трубку: оператор нажал «Завершить» — он; телефон сообщил конец
            # разговора без его нажатия — водитель; телефон не сообщил ничего — неизвестно.
            "end_party": "operator" if op_hung_up else ("client" if phone_ended else "unknown"),
            "outcome": outcome or "", "general_call_id": str(general_call_id or "") or None,
            "signed_at": signed_at,
        })
    return calls


def local_time(value) -> str:
    if not isinstance(value, datetime):
        return ""
    return value.astimezone(ALMATY).strftime("%H:%M")


__all__ = [
    "SERIES", "SHORT_POSITIVE", "SHARE", "REDIAL", "KINDS", "POSITIVE_OUTCOMES",
    "is_positive", "series", "short_positive", "share", "redial", "find", "flagged_calls",
    "public_stats", "binomial_tail", "load_day", "day_bounds", "remote_cc_department_id",
    "local_time",
]
