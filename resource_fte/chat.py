"""Расчет ресурсов для чат-направления.

Отличие от линии принципиальное: среднее время обработки НЕ используется. В Chat2Desk
поле длительности обращения меряет его жизнь от начала до закрытия (медиана ~1,6 часа),
а не работу оператора: чат висит открытым, пока клиент молчит. Умножать объём на такую
величину нельзя — модель попросит в разы больше людей, чем нужно.

Вместо этого считаем от ЦЕЛИ по сервису:

    Нужно чатников в час = Чаты в час / Чатов в час на одного чатника

Целей по сервису ДВЕ, и путать их нельзя:

* «ответ внутри чата» (5 мин) — среднее время ответа на сообщения внутри диалога.
  В базе его нет (только API Chat2Desk), зато по нему снята калибровка, поэтому
  именно он работает РЫЧАГОМ: из него выводится ёмкость;
* «первый ответ» (1 мин) — время до первой реплики оператора. Оно лежит в базе
  (`reaction_time`), поэтому измеряется по факту и показывается против цели.

Ёмкость выводится из цели по калибровке `ответ_сек = exp(a + b × нагрузка)`, снятой по
14 дням и 15 701 чату (часы с ≥3 чатниками, r = 0,45, n = 236):

    ёмкость = (ln(цель_сек) − a) / b

Так же поступает Genesys Cloud WFM — снимает перекрытие одновременных чатов и считает
усилие, а не сырое среднее время обработки.

Кривая «первый ответ ↔ нагрузка» НЕ выдумана и по умолчанию отсутствует: её снимает
`fit_first_reply_curve` по нашим же данным. Пока замера нет, первый ответ работает
только как измеряемая цель.

Обработки требуют 100 % чатов. На линии допустимы 5 % потерь, в чате нет: обращение не
«теряется», оно висит открытым, пока клиент не получит ответ. Поэтому ни множителя доли
принятых, ни вычета потерь в этой модели нет и быть не должно.
"""
import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.extras import execute_values

from .common import (
    WEEKDAYS_RU, WORK_DAYS_PER_OPERATOR_WEEK,
    _round_fte_to_half, _to_float, _to_int,
)


# Коэффициенты калибровки «ответ внутри чата ↔ нагрузка». Снимались по выгрузкам табло
# СЗоВ, а не взяты из отраслевых таблиц: у нас 13 каналов и свои шаблоны.
CHAT_CAPACITY_CURVE_A = 4.7047
CHAT_CAPACITY_CURVE_B = 0.0578

# Округление потребности: 'half' — до половины ставки (как на линии), 'exact' — как есть,
# 'ceil' — вверх до целого. Выбор владельца, на саму формулу часа не влияет.
CHAT_FTE_ROUNDING_MODES = ("half", "exact", "ceil")

DEFAULT_CHAT_SETTINGS = {
    "target_reply_seconds": 300,
    "target_first_reply_seconds": 60,
    "capacity_per_hour": 17.0,
    "capacity_curve_a": CHAT_CAPACITY_CURVE_A,
    "capacity_curve_b": CHAT_CAPACITY_CURVE_B,
    "first_reply_curve_a": None,
    "first_reply_curve_b": None,
    "first_reply_curve_fitted_at": None,
    "capacity_manual": None,
    "shrinkage_coeff": 0.90,
    "weekly_hours_per_operator": 40.0,
    "base_weeks": 2,
    "fte_rounding": "half",
}

# Границы вводных: за ними расчёт теряет смысл, а не просто становится неточным.
# У кривых границы широкие — они защищают от опечатки и деления на ноль, а не сужают
# замер: подгонка вправе дать любой разумный наклон.
CHAT_SETTINGS_LIMITS = {
    "target_reply_seconds": (30, 3600),
    "target_first_reply_seconds": (10, 3600),
    "capacity_per_hour": (0.5, 60.0),
    "capacity_curve_a": (0.1, 20.0),
    "capacity_curve_b": (0.0001, 5.0),
    "first_reply_curve_a": (0.1, 20.0),
    "first_reply_curve_b": (0.0001, 5.0),
    "capacity_manual": (0.5, 60.0),
    "shrinkage_coeff": (0.1, 1.0),
    "weekly_hours_per_operator": (1.0, 168.0),
    "base_weeks": (1, 8),
}

CHAT_REQUEST_TYPE = "common"

# Направление «Чат менеджер». Штат для расчёта берём по нему, а не по всем операторам.
CHAT_DIRECTION_NAME_PATTERN = "%чат%"

# Онлайн-сегмент оператора = отработанный чатнико-час. Берём ФАКТ присутствия, а не
# график смен: график говорит, кого поставили, а не кто на самом деле был в строю.
CHAT_ONLINE_STATUS_KEY = "online"

# Смены чат-направления взяты из боевого графика владельца «График чат (6).xlsx»:
# это НЕ дефолтный набор линии — там свой состав (нет 7*16 и 9*18, зато есть 12*21).
# Разложены по длительности: 9–12 ч это полная ставка, 6,5 ч — 0,75.
CHAT_SHIFT_TEMPLATE_LABELS = {
    1.0: [
        "8*17",
        "10*19",
        "11*20",
        "12*21",
        "13*22",
        "15*00",
        "17*02",
        "20*08",
    ],
    0.75: [
        "9*15/30",
        "11*17/30",
        "12*18/30",
        "13*19/30",
    ],
}

# В чате только две ставки — 1,0 и 0,75 (решение владельца 27.08.2026).
# Половинной ставки здесь нет, поэтому и шаблонов на неё быть не должно.
CHAT_RATES = (1.0, 0.75)


def get_chat_shift_templates() -> Dict[str, Any]:
    """Шаблоны смен чата — из боевого графика, а не дефолты линии."""
    from .schedule_generation import _normalize_shift_template

    templates = []
    index = 0
    for rate, labels in CHAT_SHIFT_TEMPLATE_LABELS.items():
        for label in labels:
            templates.append(_normalize_shift_template(label, fallback_rate=rate, index=index))
            index += 1
    return {
        "templates": templates,
        "rates": [{"rate": 1.0, "label": "1"}, {"rate": 0.75, "label": "0.75"}],
        "source": "График чат (6).xlsx",
    }


def _clamp(value: float, key: str) -> float:
    low, high = CHAT_SETTINGS_LIMITS[key]
    return min(max(float(value), float(low)), float(high))


def _capacity_from_curve(target_seconds: Any, curve_a: Any, curve_b: Any) -> Optional[float]:
    """Обратная калибровка: при какой нагрузке кривая даёт ровно целевое время ответа."""
    target = _to_float(target_seconds, 0.0)
    slope = _to_float(curve_b, 0.0)
    if target <= 0 or slope == 0:
        return None
    return (math.log(target) - _to_float(curve_a, 0.0)) / slope


def resolve_chat_capacity(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Единая точка вывода ёмкости: сколько чатов в час тянет один чатник.

    Порядок ровно такой: ручное переопределение → цель «ответа внутри чата» → более
    жёсткая из двух целей, если кривая первого ответа ЗАМЕРЕНА. Замер может показать,
    что цель первого ответа недостижима ни при каком штате (так уже было: даже в самой
    разгруженной полосе первый ответ держался около 357 с). Молча зажимать ёмкость в
    этом случае нельзя — иначе модель начнёт требовать людей под недостижимую цель,
    поэтому возвращаем честный признак и остаёмся на «ответе внутри чата».
    """
    settings = dict(settings or {})
    floor, _ceiling = CHAT_SETTINGS_LIMITS["capacity_per_hour"]

    inside_raw = _capacity_from_curve(
        settings.get("target_reply_seconds", DEFAULT_CHAT_SETTINGS["target_reply_seconds"]),
        settings.get("capacity_curve_a", CHAT_CAPACITY_CURVE_A),
        settings.get("capacity_curve_b", CHAT_CAPACITY_CURVE_B),
    )
    # Кривая не задана (наклон обнулили или колонок ещё нет) — считать по ней нечего.
    # Прятать это за дефолтом 17 нельзя: цифра выглядела бы ответом на выставленную
    # цель, хотя цель в неё не входила.
    inside_curve_broken = inside_raw is None
    inside = float(DEFAULT_CHAT_SETTINGS["capacity_per_hour"]) if inside_curve_broken else inside_raw

    curve_a = settings.get("first_reply_curve_a")
    curve_b = settings.get("first_reply_curve_b")
    first_reply = None
    if curve_a is not None and curve_b is not None:
        first_reply = _capacity_from_curve(
            settings.get("target_first_reply_seconds",
                         DEFAULT_CHAT_SETTINGS["target_first_reply_seconds"]),
            curve_a, curve_b,
        )

    # Недостижима не только отрицательная ёмкость. Значение НИЖЕ пола лимитов тоже
    # недостижимо: зажатие подняло бы его до пола, и модель попросила бы людей под
    # цель, которой на замеренной кривой не существует. Проверяем обе цели одинаково.
    def _unreachable(value: Optional[float]) -> bool:
        return value is not None and value < floor

    first_unreachable = _unreachable(first_reply)
    inside_unreachable = (not inside_curve_broken) and _unreachable(inside_raw)

    candidates = []
    if not inside_unreachable:
        candidates.append(("inside_chat", inside))
    if first_reply is not None and not first_unreachable:
        candidates.append(("first_reply", first_reply))

    if candidates:
        source, value = min(candidates, key=lambda item: item[1])
    else:
        # Обе цели недостижимы: ёмкости, при которой они выполняются, не существует.
        # Числа здесь нет и быть не может. Пол лимитов (0,5 чата в час) выглядел бы
        # ответом на выставленную цель и заставлял раздел просить в 35 раз больше
        # людей, поэтому наружу уходит признак, а витрина показывает предупреждение.
        source, value = "unreachable", None

    manual = settings.get("capacity_manual")
    if manual is not None and _to_float(manual, 0.0) > 0:
        # Ручное переопределение — аварийный рычаг: им владелец продолжает считать
        # даже там, где ни одна цель не берётся.
        value = _to_float(manual, 0.0)
        source = "manual"

    return {
        "value": None if value is None else round(_clamp(value, "capacity_per_hour"), 4),
        "source": source,
        # Расчёт потребности невозможен: витрина обязана показать предупреждение
        # вместо числа, а не подставить своё.
        "capacity_unavailable": value is None,
        "inside_chat": round(inside, 4),
        "first_reply": round(first_reply, 4) if first_reply is not None else None,
        "first_reply_target_unreachable": bool(first_unreachable),
        "inside_chat_target_unreachable": bool(inside_unreachable),
        "capacity_curve_missing": bool(inside_curve_broken),
    }


def _capacity_explain(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Из чего получилась ёмкость — чтобы в интерфейсе не было магического числа."""
    resolved = resolve_chat_capacity(settings)
    return {
        "target_seconds": _to_int(settings.get("target_reply_seconds"),
                                  DEFAULT_CHAT_SETTINGS["target_reply_seconds"]),
        "target_first_reply_seconds": _to_int(
            settings.get("target_first_reply_seconds"),
            DEFAULT_CHAT_SETTINGS["target_first_reply_seconds"]),
        "curve_a": _to_float(settings.get("capacity_curve_a"), CHAT_CAPACITY_CURVE_A),
        "curve_b": _to_float(settings.get("capacity_curve_b"), CHAT_CAPACITY_CURVE_B),
        "first_reply_curve_a": settings.get("first_reply_curve_a"),
        "first_reply_curve_b": settings.get("first_reply_curve_b"),
        "first_reply_curve_fitted_at": settings.get("first_reply_curve_fitted_at"),
        "derived": resolved["inside_chat"],
        "derived_first_reply": resolved["first_reply"],
        "manual": settings.get("capacity_manual"),
        "used": resolved["value"],
        "source": resolved["source"],
        "first_reply_target_unreachable": resolved["first_reply_target_unreachable"],
        "inside_chat_target_unreachable": resolved["inside_chat_target_unreachable"],
        "capacity_unavailable": resolved["capacity_unavailable"],
    }


def _capacity_for_math(settings: Dict[str, Any]) -> Optional[float]:
    """Ёмкость, пригодной для расчёта, либо None — когда ни одна цель не достижима.

    Подставлять здесь любое число нельзя: потребность, посчитанная от подставленной
    ёмкости, выглядит как ответ на цель, которой не существует.
    """
    if settings.get("capacity_unavailable"):
        return None
    value = _to_float(settings.get("capacity_per_hour"), 0.0)
    return value if value > 0 else None


def _round_chat_fte(value: float, mode: Any) -> float:
    number = max(0.0, _to_float(value, 0.0))
    if mode == "exact":
        return round(number, 4)
    if mode == "ceil":
        return float(math.ceil(number))
    return float(_round_fte_to_half(number))


def _as_chat_settings(row: Any) -> Dict[str, Any]:
    if not row:
        return dict(DEFAULT_CHAT_SETTINGS)
    fitted_at = row[8]
    rounding = str(row[11] or DEFAULT_CHAT_SETTINGS["fte_rounding"]).strip().lower()
    settings = {
        "target_reply_seconds": _to_int(row[0], DEFAULT_CHAT_SETTINGS["target_reply_seconds"]),
        "target_first_reply_seconds": _to_int(
            row[1], DEFAULT_CHAT_SETTINGS["target_first_reply_seconds"]),
        "capacity_per_hour": _to_float(row[2], DEFAULT_CHAT_SETTINGS["capacity_per_hour"]),
        "shrinkage_coeff": _to_float(row[3], DEFAULT_CHAT_SETTINGS["shrinkage_coeff"]),
        "weekly_hours_per_operator": _to_float(
            row[4], DEFAULT_CHAT_SETTINGS["weekly_hours_per_operator"]),
        "base_weeks": _to_int(row[5], DEFAULT_CHAT_SETTINGS["base_weeks"]),
        "capacity_curve_a": _to_float(row[6], CHAT_CAPACITY_CURVE_A),
        "capacity_curve_b": _to_float(row[7], CHAT_CAPACITY_CURVE_B),
        # Кривая первого ответа остаётся пустой, пока её не ЗАМЕРИЛИ. Подставлять сюда
        # правдоподобные числа нельзя: по ним модель попросит реальных людей.
        "first_reply_curve_a": None if row[9] is None else _to_float(row[9]),
        "first_reply_curve_b": None if row[10] is None else _to_float(row[10]),
        "first_reply_curve_fitted_at": fitted_at.isoformat() if fitted_at else None,
        "capacity_manual": None if row[12] is None else _to_float(row[12]),
        "fte_rounding": rounding if rounding in CHAT_FTE_ROUNDING_MODES
        else DEFAULT_CHAT_SETTINGS["fte_rounding"],
    }
    # Ёмкость больше не вводится руками — колонка держит лишь кэш последнего значения,
    # поэтому при чтении она всегда пересобирается из целей. Может выйти пустой:
    # значит, ни одна цель не достижима и потребность считать нечем.
    resolved = resolve_chat_capacity(settings)
    settings["capacity_per_hour"] = resolved["value"]
    settings["capacity_unavailable"] = resolved["capacity_unavailable"]
    return settings


_CHAT_SETTINGS_COLUMNS = """
    target_reply_seconds, target_first_reply_seconds, capacity_per_hour,
    shrinkage_coeff, weekly_hours_per_operator, base_weeks,
    capacity_curve_a, capacity_curve_b, first_reply_curve_fitted_at,
    first_reply_curve_a, first_reply_curve_b, fte_rounding, capacity_manual
"""


def _get_chat_settings_tx(cursor) -> Dict[str, Any]:
    cursor.execute(
        "SELECT %s FROM resource_chat_settings WHERE id = 1" % _CHAT_SETTINGS_COLUMNS
    )
    return _as_chat_settings(cursor.fetchone())


def get_chat_settings(db) -> Dict[str, Any]:
    with db._get_cursor() as cursor:
        return _get_chat_settings_tx(cursor)


def _save_chat_settings_tx(cursor, values: Dict[str, Any],
                           user_id: Optional[int] = None) -> None:
    cursor.execute(
        """
        UPDATE resource_chat_settings
        SET target_reply_seconds = %s,
            target_first_reply_seconds = %s,
            capacity_per_hour = %s,
            capacity_curve_a = %s,
            capacity_curve_b = %s,
            capacity_manual = %s,
            shrinkage_coeff = %s,
            weekly_hours_per_operator = %s,
            base_weeks = %s,
            fte_rounding = %s,
            updated_by = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
        """,
        # capacity_per_hour в таблице — кэш под NOT NULL: положить в него пустоту
        # нельзя, а чтение всё равно пересобирает ёмкость из целей. При недостижимой
        # цели пишем дефолт калибровки — расчётом он не становится.
        (values["target_reply_seconds"], values["target_first_reply_seconds"],
         values["capacity_per_hour"] if values["capacity_per_hour"] is not None
         else DEFAULT_CHAT_SETTINGS["capacity_per_hour"],
         values["capacity_curve_a"], values["capacity_curve_b"],
         values["capacity_manual"], values["shrinkage_coeff"],
         values["weekly_hours_per_operator"], values["base_weeks"],
         values["fte_rounding"], user_id),
    )


def update_chat_settings(db, payload: Dict[str, Any],
                         user_id: Optional[int] = None) -> Dict[str, Any]:
    current = get_chat_settings(db)
    nxt = dict(current)
    for key in ("shrinkage_coeff", "weekly_hours_per_operator",
                "capacity_curve_a", "capacity_curve_b"):
        if key in payload:
            nxt[key] = _clamp(_to_float(payload.get(key), current[key]), key)
    for key in ("target_reply_seconds", "target_first_reply_seconds", "base_weeks"):
        if key in payload:
            nxt[key] = int(_clamp(_to_int(payload.get(key), current[key]), key))
    # Долю можно прислать и процентами — 90 значит 0,9.
    if nxt["shrinkage_coeff"] > 1:
        nxt["shrinkage_coeff"] = _clamp(nxt["shrinkage_coeff"] / 100.0, "shrinkage_coeff")
    if "fte_rounding" in payload:
        mode = str(payload.get("fte_rounding") or "").strip().lower()
        nxt["fte_rounding"] = mode if mode in CHAT_FTE_ROUNDING_MODES else current["fte_rounding"]
    # Пустое значение — это «вернуться к выводу из цели», а не ноль.
    if "capacity_manual" in payload:
        raw = payload.get("capacity_manual")
        if raw is None or str(raw).strip() == "" or _to_float(raw, 0.0) <= 0:
            nxt["capacity_manual"] = None
        else:
            nxt["capacity_manual"] = _clamp(_to_float(raw, 0.0), "capacity_manual")
    resolved = resolve_chat_capacity(nxt)
    nxt["capacity_per_hour"] = resolved["value"]
    nxt["capacity_unavailable"] = resolved["capacity_unavailable"]

    with db._get_cursor() as cursor:
        _save_chat_settings_tx(cursor, nxt, user_id)
    return nxt


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError("INVALID_CHAT_DATE")


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _latest_chat_day_tx(cursor) -> Optional[date]:
    cursor.execute("SELECT MAX(day) FROM c2d_requests WHERE request_type = %s",
                   (CHAT_REQUEST_TYPE,))
    row = cursor.fetchone()
    return row[0] if row and row[0] else None


def _hourly_volume_tx(cursor, day_from: date, day_to: date) -> Dict[str, Dict[int, int]]:
    """Чаты по часам за период. Час берётся из request_start — он уже местный (Алматы)."""
    cursor.execute(
        """
        SELECT day, EXTRACT(HOUR FROM request_start)::int AS hh, COUNT(*)
        FROM c2d_requests
        WHERE request_type = %s
          AND request_start IS NOT NULL
          AND day BETWEEN %s AND %s
        GROUP BY 1, 2
        """,
        (CHAT_REQUEST_TYPE, day_from, day_to),
    )
    out: Dict[str, Dict[int, int]] = {}
    for day_value, hour, count in cursor.fetchall():
        key = day_value.isoformat()
        out.setdefault(key, {})[int(hour)] = int(count)
    return out


def _online_hours_tx(cursor, day_from: date, day_to: date) -> Dict[str, Dict[int, float]]:
    """ФАКТ чатнико-часов по часам: онлайн-сегменты людей чат-направления.

    Именно факт присутствия, а не график смен: график говорит, кого поставили, а
    отработали в итоге другое. Сегмент режется по границам часа — смена с 15:40 до
    00:00 даёт 0,33 часа в 15-м часе и по часу в остальных, иначе ночные часы
    получают чужую нагрузку.
    """
    cursor.execute(
        """
        SELECT (g.slot)::date AS d,
               EXTRACT(HOUR FROM g.slot)::int AS hh,
               SUM(EXTRACT(EPOCH FROM (
                   LEAST(s.end_at, g.slot + INTERVAL '1 hour')
                   - GREATEST(s.start_at, g.slot)
               ))) / 3600.0 AS worked_hours
        FROM operator_status_segments s
        JOIN users u ON u.id = s.operator_id
        JOIN directions d ON d.id = u.direction_id
        CROSS JOIN LATERAL generate_series(
            date_trunc('hour', s.start_at),
            s.end_at - INTERVAL '1 microsecond',
            INTERVAL '1 hour'
        ) AS g(slot)
        WHERE s.status_key = %s
          AND d.name ILIKE %s
          AND s.status_date BETWEEN %s AND %s
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
        (CHAT_ONLINE_STATUS_KEY, CHAT_DIRECTION_NAME_PATTERN,
         day_from - timedelta(days=1), day_to),
    )
    out: Dict[str, Dict[int, float]] = {}
    for day_value, hour, worked in cursor.fetchall():
        if day_value < day_from or day_value > day_to:
            # Ночная смена вылезает за края окна — такие часы не наши.
            continue
        out.setdefault(day_value.isoformat(), {})[int(hour)] = round(_to_float(worked), 4)
    return out


def _reply_stats_tx(cursor, day_from: date, day_to: date,
                    target_first_seconds: int) -> Dict[str, Dict[int, Dict[str, Any]]]:
    """Первый ответ по дню и часу — единственная измеримая по нашей базе цель.

    «В цель» в чате считается по цели ПЕРВОГО ОТВЕТА: время до первой реплики
    оператора лежит в `reaction_time`. Цель «ответа внутри чата» по базе не измерить —
    она живёт только в API Chat2Desk и работает рычагом ёмкости, а не мерой факта.
    """
    cursor.execute(
        """
        SELECT day, EXTRACT(HOUR FROM request_start)::int AS hh,
               COUNT(*)::int,
               COUNT(reaction_time)::int,
               COUNT(*) FILTER (WHERE reaction_time <= %s)::int,
               AVG(reaction_time)::float
        FROM c2d_requests
        WHERE request_type = %s
          AND request_start IS NOT NULL
          AND day BETWEEN %s AND %s
        GROUP BY 1, 2
        """,
        (int(target_first_seconds), CHAT_REQUEST_TYPE, day_from, day_to),
    )
    out: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for day_value, hour, total, answered, in_target, avg_first in cursor.fetchall():
        out.setdefault(day_value.isoformat(), {})[int(hour)] = {
            "total": int(total or 0),
            "answered": int(answered or 0),
            "in_target": int(in_target or 0),
            "no_reply": int(total or 0) - int(answered or 0),
            "avg_first_reply": round(_to_float(avg_first), 1) if avg_first is not None else None,
        }
    return out


def _weighted_linear_fit(points: List[Tuple[float, float, float]]) -> Optional[Tuple[float, float, float]]:
    """Взвешенная прямая y = a + b·x и её корреляция. Вес — число чатов в часе."""
    total_weight = sum(item[2] for item in points)
    if len(points) < 3 or total_weight <= 0:
        return None
    mean_x = sum(x * w for x, _, w in points) / total_weight
    mean_y = sum(y * w for _, y, w in points) / total_weight
    sxx = sum(w * (x - mean_x) ** 2 for x, _, w in points)
    syy = sum(w * (y - mean_y) ** 2 for _, y, w in points)
    sxy = sum(w * (x - mean_x) * (y - mean_y) for x, y, w in points)
    if sxx <= 0:
        return None
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    correlation = sxy / math.sqrt(sxx * syy) if syy > 0 else 0.0
    return intercept, slope, correlation


# Час идёт в замер, только если в нём было не меньше трёх чатников: на одном-двух
# человеках отклик определяется не нагрузкой, а тем, отошёл ли этот один.
CHAT_FIT_MIN_STAFF = 3.0


def fit_first_reply_curve(db, days: int = 14) -> Dict[str, Any]:
    """ЗАМЕР кривой «первый ответ ↔ нагрузка» по нашим данным.

    Подгоняем `первый_ответ_сек = exp(a + b × нагрузка)`, где нагрузка часа — чаты
    часа на одного чатника, а отклик — среднее время до первой реплики. Коэффициенты
    берутся ТОЛЬКО отсюда: пока замера нет, колонки остаются пустыми и ёмкость
    выводится из «ответа внутри чата».

    Замер вправе сказать, что цель первого ответа недостижима — это не ошибка, а
    результат: в ночные часы работает 1–2 чатника, и никакой штат этого не лечит.
    """
    window = min(max(_to_int(days, 14), 3), 90)
    settings = get_chat_settings(db)
    with db._get_cursor() as cursor:
        latest = _latest_chat_day_tx(cursor)
        if latest is None:
            return {"fitted": False, "reason": "NO_CHAT_DATA", "points": 0,
                    "first_reply_curve_a": None, "first_reply_curve_b": None}
        day_from = latest - timedelta(days=window - 1)
        volume = _hourly_volume_tx(cursor, day_from, latest)
        online = _online_hours_tx(cursor, day_from, latest)
        replies = _reply_stats_tx(cursor, day_from, latest,
                                  settings["target_first_reply_seconds"])

        points: List[Tuple[float, float, float]] = []
        for day_key, per_hour in volume.items():
            online_day = online.get(day_key, {})
            replies_day = replies.get(day_key, {})
            for hour, chats in per_hour.items():
                staff = _to_float(online_day.get(hour), 0.0)
                stats = replies_day.get(hour) or {}
                avg_first = stats.get("avg_first_reply")
                if staff < CHAT_FIT_MIN_STAFF or chats <= 0 or not avg_first or avg_first <= 0:
                    continue
                points.append((chats / staff, math.log(float(avg_first)), float(chats)))

        fit = _weighted_linear_fit(points)
        if fit is None:
            return {"fitted": False, "reason": "NOT_ENOUGH_POINTS", "points": len(points),
                    "first_reply_curve_a": None, "first_reply_curve_b": None}
        intercept, slope, correlation = fit
        # Наклон обязан быть положительным: смысл кривой в том, что чем больше чатов
        # на человека, тем дольше он отвечает. Отрицательный наклон означает обратное
        # («чем занятее, тем быстрее») — так бывает на шумной выборке, где ночные часы
        # с одним чатником отвечают медленнее дневных. Записать такую кривую нельзя:
        # обратная подстановка дала бы тем большую ёмкость, чем жёстче цель, и модель
        # молча попросила бы МЕНЬШЕ людей под более строгий сервис.
        if slope <= 0:
            return {"fitted": False, "reason": "SLOPE_NOT_POSITIVE",
                    "points": len(points), "correlation": round(correlation, 4),
                    "first_reply_curve_a": None, "first_reply_curve_b": None,
                    "rejected_slope": round(slope, 6)}
        cursor.execute(
            """
            UPDATE resource_chat_settings
            SET first_reply_curve_a = %s,
                first_reply_curve_b = %s,
                first_reply_curve_fitted_at = now()
            WHERE id = 1
            """,
            (intercept, slope),
        )

    fitted = dict(settings, first_reply_curve_a=intercept, first_reply_curve_b=slope)
    resolved = resolve_chat_capacity(fitted)
    return {
        "fitted": True,
        "first_reply_curve_a": round(intercept, 6),
        "first_reply_curve_b": round(slope, 6),
        "correlation": round(correlation, 4),
        "points": len(points),
        "days": window,
        "min_staff": CHAT_FIT_MIN_STAFF,
        "target_first_reply_seconds": settings["target_first_reply_seconds"],
        "capacity": resolved["value"],
        "capacity_first_reply": resolved["first_reply"],
        "target_unreachable": resolved["first_reply_target_unreachable"],
    }


MAX_BASE_LOOKBACK_WEEKS = 12


def _base_week_starts(anchor_week_start: date, base_weeks: int) -> List[date]:
    """Недели-основания без учёта данных: `base_weeks` недель ПЕРЕД целевой."""
    return [anchor_week_start - timedelta(weeks=offset)
            for offset in range(1, max(1, int(base_weeks)) + 1)]


def _covered_base_week_starts(anchor_week_start: date, base_weeks: int,
                              covered_days: set) -> Dict[str, Any]:
    """Берём только ПОЛНЫЕ недели: все 7 дней должны быть в данных.

    Иначе выходит перекос: свежая неделя обрывается на середине (ретеншн
    c2d_requests или ещё не наступивший день), и тогда понедельник считается по двум
    неделям, а пятница — по одной. Неполные недели пропускаем и уходим глубже.
    """
    used: List[date] = []
    skipped: List[Dict[str, Any]] = []
    wanted = max(1, int(base_weeks))
    for offset in range(1, MAX_BASE_LOOKBACK_WEEKS + 1):
        if len(used) >= wanted:
            break
        candidate = anchor_week_start - timedelta(weeks=offset)
        days = [candidate + timedelta(days=i) for i in range(7)]
        missing = [d.isoformat() for d in days if d.isoformat() not in covered_days]
        if missing:
            skipped.append({"week_start": candidate.isoformat(), "missing_days": missing})
            continue
        used.append(candidate)
    if not used:
        # Полных недель нет вообще — честнее считать по тому, что есть, чем отдать пусто.
        used = _base_week_starts(anchor_week_start, wanted)
    return {"used": used, "skipped": skipped}


def _covered_days_tx(cursor, day_from: date, day_to: date) -> set:
    cursor.execute(
        """
        SELECT DISTINCT day FROM c2d_requests
        WHERE request_type = %s AND day BETWEEN %s AND %s
        """,
        (CHAT_REQUEST_TYPE, day_from, day_to),
    )
    return {row[0].isoformat() for row in cursor.fetchall()}


MAX_FORECAST_PERIOD_DAYS = 31

# Наплыв. Числа те же, что у линии: механика портируется целиком, а не переизобретается,
# иначе два раздела начнут по-разному объяснять один и тот же всплеск.
CHAT_UPLIFT_LOOKBACK_DAYS = 6
CHAT_UPLIFT_MAX_RATIO = 2.0
CHAT_UPLIFT_CONFIDENCE_FLOOR = 0.35
CHAT_UPLIFT_FORECAST_DAYS = 7
CHAT_UPLIFT_FUTURE_MIN_WEIGHT = 0.55


def _uplift_future_weight(day_index: int, day_count: int) -> float:
    """Чем дальше день от всплеска, тем меньше веры, что всплеск ещё держится."""
    day_count = max(1, int(day_count or 1))
    if day_count <= 1:
        return 1.0
    safe_index = min(max(0, int(day_index or 0)), day_count - 1)
    progress = safe_index / (day_count - 1)
    return max(
        CHAT_UPLIFT_FUTURE_MIN_WEIGHT,
        1.0 - ((1.0 - CHAT_UPLIFT_FUTURE_MIN_WEIGHT) * progress),
    )


def _empty_chat_uplift_profile() -> Dict[str, Any]:
    return {
        "lookback_days": CHAT_UPLIFT_LOOKBACK_DAYS,
        "max_ratio": CHAT_UPLIFT_MAX_RATIO,
        "confidence_floor": CHAT_UPLIFT_CONFIDENCE_FLOOR,
        "forecast_window_days": CHAT_UPLIFT_FORECAST_DAYS,
        "future_min_weight": CHAT_UPLIFT_FUTURE_MIN_WEIGHT,
        "source_dates": [],
        "source_anchor_date": None,
        "forecast_window_start": None,
        "forecast_window_end": None,
        "source_day_count": 0,
        "average_growth_ratio": 0.0,
        "raw_average_growth_ratio": 0.0,
        "max_hourly_growth_ratio": 0.0,
        "total_positive_delta_chats": 0.0,
        "daily": [],
        "daily_summary": {
            "held_day_count": 0,
            "overload_day_count": 0,
            "source_day_count": 0,
            "total_forecast_chats": 0.0,
            "total_actual_chats": 0.0,
            "total_delta_chats": 0.0,
            "total_positive_delta_chats": 0.0,
        },
        "future_weights": [],
        "hourly": [
            {
                "hour": hour,
                "growth_ratio": 0.0,
                "raw_growth_ratio": 0.0,
                "weighted_delta_chats": 0.0,
                "confidence": 0.0,
                "coverage_factor": 0.0,
                "persistence_factor": 0.0,
                "source_count": 0,
                "positive_source_count": 0,
                "sources": [],
            }
            for hour in range(24)
        ],
    }


def _chat_uplift_profile_tx(cursor, as_of_date: date,
                            lookback_days: int = CHAT_UPLIFT_LOOKBACK_DAYS) -> Dict[str, Any]:
    """Профиль наплыва: насколько последние дни перерастали собственный прогноз.

    Смотрим по каждому часу отношение «сколько сверх прогноза пришло» к прогнозу и
    гасим его уверенностью: разовый выброс в одном дне из шести не должен закладываться
    в штат так же, как всплеск, который держится шестой день подряд.
    """
    lookback_days = max(1, int(lookback_days or CHAT_UPLIFT_LOOKBACK_DAYS))
    window_end = as_of_date + timedelta(days=CHAT_UPLIFT_FORECAST_DAYS - 1)
    # Сегодняшний день закрыт не весь: часы, которые ещё не наступили, лежат в таблице
    # с нулевым фактом против ненулевого прогноза. Считать их «недобором» нельзя — и
    # особенно нельзя потому, что свежий день весит в профиле БОЛЬШЕ всех остальных:
    # дневной пересчёт в 14:00 занижал наплыв +30 % до +25 %, а часы вечернего пика
    # обнулял почти полностью. Берём сегодня ТОЛЬКО по закрытым часам, а не выбрасываем
    # день целиком: наплыв — свойство самых свежих дней, и терять сегодняшний сигнал
    # вреднее, чем потерять его вечерний хвост. Время процесса — Алматы (database.py
    # ставит TZ), то есть тот же пояс, в котором лежат часы чатов.
    now = datetime.now()
    today = now.date()
    closed_hours_today = now.hour   # текущий час идёт прямо сейчас — он тоже неполный
    cursor.execute(
        """
        SELECT DISTINCT report_date
        FROM chat_resource_hours
        WHERE report_date < %s
        ORDER BY report_date DESC
        LIMIT %s
        """,
        (as_of_date, lookback_days + 1),
    )
    source_dates = [row[0] for row in cursor.fetchall()]
    if closed_hours_today <= 0:
        # Пересчёт сразу после полуночи: у сегодняшнего дня нет ни одного закрытого
        # часа, и пустой день только отобрал бы вес у настоящих.
        source_dates = [item for item in source_dates if item != today]
    # Запас в один день брался как раз на случай выброшенного дня — окно держим прежним.
    source_dates = source_dates[:lookback_days]
    if not source_dates:
        return {
            **_empty_chat_uplift_profile(),
            "source_anchor_date": as_of_date.isoformat(),
            "forecast_window_start": as_of_date.isoformat(),
            "forecast_window_end": window_end.isoformat(),
        }

    weights_by_date = {value: lookback_days - index
                       for index, value in enumerate(source_dates)}
    daily = {
        value: {"weight": float(weights_by_date[value]), "forecast_chats": 0.0,
                "actual_chats": 0.0, "positive_delta_chats": 0.0,
                "positive_hour_count": 0, "source_hour_count": 0,
                "max_hourly_growth_ratio": 0.0}
        for value in source_dates
    }
    hourly = {
        hour: {"ratio_weighted_sum": 0.0, "ratio_weight": 0.0,
               "delta_weighted_sum": 0.0, "delta_weight": 0.0,
               "source_weight": 0.0, "positive_weight": 0.0,
               "source_count": 0, "positive_source_count": 0, "sources": []}
        for hour in range(24)
    }
    cursor.execute(
        """
        SELECT report_date, hour, actual_chats, forecast_chats
        FROM chat_resource_hours
        WHERE report_date = ANY(%s)
        ORDER BY report_date DESC, hour
        """,
        (source_dates,),
    )
    total_positive_delta = 0.0
    total_forecast_for_ratio = 0.0
    total_weighted_ratio = 0.0
    total_ratio_weight = 0.0
    for report_date, hour_raw, actual_raw, forecast_raw in cursor.fetchall():
        hour = int(hour_raw)
        if hour < 0 or hour > 23:
            continue
        if report_date == today and hour >= closed_hours_today:
            # Час ещё не закончился (или не начался): его факт сравнивать с полным
            # прогнозом часа нечестно — это не недобор, это отсутствие данных.
            continue
        weight = float(weights_by_date.get(report_date, 1))
        forecast_chats = max(0.0, _to_float(forecast_raw))
        actual_chats = max(0.0, _to_float(actual_raw))
        positive_delta = max(0.0, actual_chats - forecast_chats)
        ratio = 0.0
        day = daily.get(report_date)
        if day is not None:
            day["forecast_chats"] += forecast_chats
            day["actual_chats"] += actual_chats
            day["positive_delta_chats"] += positive_delta
            day["source_hour_count"] += 1
            if positive_delta > 0:
                day["positive_hour_count"] += 1
        bucket = hourly[hour]
        bucket["source_weight"] += weight
        if forecast_chats > 0:
            ratio = min(positive_delta / forecast_chats, CHAT_UPLIFT_MAX_RATIO)
            if day is not None:
                day["max_hourly_growth_ratio"] = max(day["max_hourly_growth_ratio"], ratio)
            bucket["ratio_weighted_sum"] += ratio * weight
            bucket["ratio_weight"] += weight
            total_weighted_ratio += ratio * weight
            total_ratio_weight += weight
            total_forecast_for_ratio += forecast_chats
        bucket["delta_weighted_sum"] += positive_delta * weight
        bucket["delta_weight"] += weight
        bucket["source_count"] += 1
        if positive_delta > 0:
            bucket["positive_source_count"] += 1
            bucket["positive_weight"] += weight
            total_positive_delta += positive_delta
        bucket["sources"].append({
            "date": report_date.isoformat(),
            "weight": weight,
            "forecast_chats": round(forecast_chats, 2),
            "actual_chats": round(actual_chats, 2),
            "delta_chats": round(positive_delta, 2),
            "growth_ratio": round(ratio, 4),
        })

    # Сколько дней окна ВООБЩЕ могли дать этот час. Незакрытые часы сегодняшнего дня —
    # не пропуск данных, их ещё не было; если оставить в знаменателе полный lookback,
    # уверенность вечерних часов падает, и занижение наплыва возвращается с другой
    # стороны. Нехватку самой истории (дней меньше окна) знаменатель по-прежнему
    # штрафует — это разные вещи.
    today_in_window = today in set(source_dates)
    hourly_rows = []
    adjusted_weighted_ratio = 0.0
    adjusted_ratio_weight = 0.0
    for hour in range(24):
        item = hourly[hour]
        ratio_weight = item["ratio_weight"]
        delta_weight = item["delta_weight"]
        source_count = item["source_count"]
        positive_source_count = item["positive_source_count"]
        raw_growth_ratio = item["ratio_weighted_sum"] / ratio_weight if ratio_weight > 0 else 0.0
        raw_delta = item["delta_weighted_sum"] / delta_weight if delta_weight > 0 else 0.0
        expected_days = lookback_days
        if today_in_window and hour >= closed_hours_today:
            expected_days = max(1, lookback_days - 1)
        coverage_factor = min(1.0, source_count / expected_days) if expected_days > 0 else 0.0
        positive_frequency = positive_source_count / source_count if source_count > 0 else 0.0
        recency_share = (item["positive_weight"] / item["source_weight"]
                         if item["source_weight"] > 0 else positive_frequency)
        persistence_factor = min(1.0, max(0.0, (positive_frequency + recency_share) / 2))
        confidence = 0.0
        if positive_source_count > 0:
            confidence = (coverage_factor ** 0.5) * (
                CHAT_UPLIFT_CONFIDENCE_FLOOR
                + ((1.0 - CHAT_UPLIFT_CONFIDENCE_FLOOR) * persistence_factor)
            )
        confidence = min(1.0, max(0.0, confidence))
        growth_ratio = min(CHAT_UPLIFT_MAX_RATIO, raw_growth_ratio * confidence)
        if ratio_weight > 0:
            adjusted_weighted_ratio += growth_ratio * ratio_weight
            adjusted_ratio_weight += ratio_weight
        hourly_rows.append({
            "hour": hour,
            "growth_ratio": round(growth_ratio, 4),
            "raw_growth_ratio": round(raw_growth_ratio, 4),
            "weighted_delta_chats": round(raw_delta * confidence, 4),
            "confidence": round(confidence, 4),
            "coverage_factor": round(coverage_factor, 4),
            "persistence_factor": round(persistence_factor, 4),
            "source_count": source_count,
            "positive_source_count": positive_source_count,
            "sources": item["sources"],
        })

    daily_rows = []
    for report_date in source_dates:
        day = daily[report_date]
        forecast_chats = day["forecast_chats"]
        actual_chats = day["actual_chats"]
        positive_delta = day["positive_delta_chats"]
        growth_ratio = min(positive_delta / forecast_chats,
                           CHAT_UPLIFT_MAX_RATIO) if forecast_chats > 0 else 0.0
        daily_rows.append({
            "date": report_date.isoformat(),
            "weight": round(day["weight"], 4),
            "forecast_chats": round(forecast_chats, 2),
            "actual_chats": round(actual_chats, 2),
            "delta_chats": round(actual_chats - forecast_chats, 2),
            "positive_delta_chats": round(positive_delta, 2),
            "growth_ratio": round(growth_ratio, 4),
            "completion_ratio": round(actual_chats / forecast_chats, 4) if forecast_chats > 0 else 0.0,
            "positive_hour_count": day["positive_hour_count"],
            "source_hour_count": day["source_hour_count"],
            "max_hourly_growth_ratio": round(day["max_hourly_growth_ratio"], 4),
            "status": "overload" if positive_delta > 0 else "held",
            "is_within_forecast": positive_delta <= 0,
            # День посчитан не по всем 24 часам — чтобы его меньшие числа не читались
            # как провал объёма.
            "is_partial_day": report_date == today,
        })
    overload_day_count = sum(1 for row in daily_rows if row["status"] == "overload")
    total_forecast = sum(row["forecast_chats"] for row in daily_rows)
    total_actual = sum(row["actual_chats"] for row in daily_rows)
    return {
        "lookback_days": lookback_days,
        "max_ratio": CHAT_UPLIFT_MAX_RATIO,
        "confidence_floor": CHAT_UPLIFT_CONFIDENCE_FLOOR,
        "forecast_window_days": CHAT_UPLIFT_FORECAST_DAYS,
        "future_min_weight": CHAT_UPLIFT_FUTURE_MIN_WEIGHT,
        "source_dates": [item.isoformat() for item in source_dates],
        "source_anchor_date": as_of_date.isoformat(),
        "forecast_window_start": as_of_date.isoformat(),
        "forecast_window_end": window_end.isoformat(),
        "source_day_count": len(source_dates),
        "average_growth_ratio": round(
            adjusted_weighted_ratio / adjusted_ratio_weight, 4) if adjusted_ratio_weight > 0 else 0.0,
        "raw_average_growth_ratio": round(
            total_weighted_ratio / total_ratio_weight, 4) if total_ratio_weight > 0 else 0.0,
        "raw_weighted_total_growth_ratio": round(
            total_positive_delta / total_forecast_for_ratio, 4) if total_forecast_for_ratio > 0 else 0.0,
        "max_hourly_growth_ratio": max((row["growth_ratio"] for row in hourly_rows), default=0.0),
        "total_positive_delta_chats": round(total_positive_delta, 2),
        "daily": daily_rows,
        "daily_summary": {
            "held_day_count": len(daily_rows) - overload_day_count,
            "overload_day_count": overload_day_count,
            "source_day_count": len(daily_rows),
            "total_forecast_chats": round(total_forecast, 2),
            "total_actual_chats": round(total_actual, 2),
            "total_delta_chats": round(total_actual - total_forecast, 2),
            "total_positive_delta_chats": round(total_positive_delta, 2),
        },
        "future_weights": [
            {
                "date": (as_of_date + timedelta(days=index)).isoformat(),
                "day_index": index,
                "weight": round(_uplift_future_weight(index, CHAT_UPLIFT_FORECAST_DAYS), 4),
            }
            for index in range(CHAT_UPLIFT_FORECAST_DAYS)
        ],
        "hourly": hourly_rows,
    }


def build_chat_uplift_profile(db, as_of_date: Any = None) -> Dict[str, Any]:
    """Профиль наплыва на дату. Без даты — от завтрашнего дня относительно данных."""
    with db._get_cursor() as cursor:
        anchor = _parse_date(as_of_date)
        if anchor is None:
            latest = _latest_chat_day_tx(cursor)
            anchor = (latest + timedelta(days=1)) if latest else date.today()
        return _chat_uplift_profile_tx(cursor, anchor)


def build_chat_forecast(db, week_start_value: Any = None,
                        settings: Optional[Dict[str, Any]] = None,
                        period_end_value: Any = None,
                        uplift_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Почасовой прогноз: среднее того же дня недели в базовых неделях.

    Период по умолчанию — неделя от `week_start_value`. Планировщик графиков
    умеет работать и с произвольным отрезком, поэтому принимаем `period_end_value`.
    """
    with db._get_cursor() as cursor:
        settings = dict(settings or _get_chat_settings_tx(cursor))
        latest = _latest_chat_day_tx(cursor)
        requested = _parse_date(week_start_value)
        if requested is not None:
            period_start = requested
        elif latest is not None:
            period_start = _week_start(latest) + timedelta(weeks=1)
        else:
            period_start = _week_start(date.today()) + timedelta(weeks=1)

        period_end = _parse_date(period_end_value) or (period_start + timedelta(days=6))
        if period_end < period_start:
            period_start, period_end = period_end, period_start
        span = (period_end - period_start).days + 1
        if span > MAX_FORECAST_PERIOD_DAYS:
            period_end = period_start + timedelta(days=MAX_FORECAST_PERIOD_DAYS - 1)
            span = MAX_FORECAST_PERIOD_DAYS

        # Базовые недели отсчитываем от недели, в которую попадает начало периода.
        target_week = _week_start(period_start)
        lookback_from = target_week - timedelta(weeks=MAX_BASE_LOOKBACK_WEEKS)
        covered = _covered_days_tx(cursor, lookback_from, target_week - timedelta(days=1))
        chosen = _covered_base_week_starts(target_week, settings["base_weeks"], covered)
        base_weeks = chosen["used"]
        skipped_weeks = chosen["skipped"]
        oldest = min(base_weeks)
        newest_end = max(base_weeks) + timedelta(days=6)
        hourly = _hourly_volume_tx(cursor, oldest, newest_end)
        capacity_info = _chat_operator_capacity_tx(cursor)

    # Ёмкости может не быть вовсе: ни одна цель по сервису не достижима. Объём чатов
    # при этом остаётся измеренным и считается как обычно, а всё, что делится на
    # ёмкость, уходит пустым — витрина показывает предупреждение вместо числа.
    capacity = _capacity_for_math(settings)
    rounding = settings.get("fte_rounding", DEFAULT_CHAT_SETTINGS["fte_rounding"])

    def _fte_round(value: Optional[float], digits: int = 4) -> Optional[float]:
        return None if value is None else round(value, digits)
    uplift_profile = uplift_profile or _empty_chat_uplift_profile()
    uplift_by_hour = {int(item["hour"]): item for item in uplift_profile.get("hourly", [])}
    window_start = (_parse_date(uplift_profile.get("forecast_window_start"))
                    or _parse_date(uplift_profile.get("source_anchor_date"))
                    or period_start)

    days: List[Dict[str, Any]] = []
    for offset in range(span):
        target_day = period_start + timedelta(days=offset)
        sources = []
        for base in base_weeks:
            # Сопоставляем по ДНЮ НЕДЕЛИ, а не по смещению в периоде: период может
            # начинаться не с понедельника, и тогда смещение уводит на чужой день.
            source_day = base + timedelta(days=target_day.weekday())
            per_hour = hourly.get(source_day.isoformat(), {})
            sources.append({
                "date": source_day.isoformat(),
                "chats": int(sum(per_hour.values())),
                "hourly": per_hour,
                "has_data": bool(per_hour),
            })
        used = [item for item in sources if item["has_data"]] or sources
        divisor = max(1, len(used))

        window_day_index = (target_day - window_start).days
        window_active = 0 <= window_day_index < CHAT_UPLIFT_FORECAST_DAYS
        future_weight = (_uplift_future_weight(window_day_index, CHAT_UPLIFT_FORECAST_DAYS)
                         if window_active else 0.0)

        hourly_forecast = []
        for hour in range(24):
            chats = sum(item["hourly"].get(hour, 0) for item in used) / divisor
            required = None if capacity is None else chats / capacity
            uplift_hour = uplift_by_hour.get(hour, {}) if window_active else {}
            base_ratio = _to_float(uplift_hour.get("growth_ratio"))
            ratio = base_ratio * future_weight
            uplift_chats = chats * ratio
            if chats <= 0 and uplift_chats <= 0:
                # Час, которого в базовых неделях не было вовсе: прогноз нулевой, и
                # процент от нуля тоже ноль — тогда берём саму прибавку в чатах.
                uplift_chats = _to_float(uplift_hour.get("weighted_delta_chats")) * future_weight
            uplift_chats = max(0.0, uplift_chats)
            uplift_fte = None if capacity is None else uplift_chats / capacity
            hourly_forecast.append({
                "hour": hour,
                "forecast_chats": round(chats, 2),
                "forecast_fte": _fte_round(required),
                "rounded_fte": None if required is None else _round_chat_fte(required, rounding),
                "incident_uplift_ratio": round(ratio, 4),
                "incident_base_uplift_ratio": round(base_ratio, 4),
                "incident_future_weight": round(future_weight, 4),
                "incident_uplift_window_active": window_active,
                "incident_uplift_confidence": _to_float(uplift_hour.get("confidence")),
                "incident_uplift_chats": round(uplift_chats, 2),
                "incident_uplift_fte": _fte_round(uplift_fte),
                "incident_adjusted_chats": round(chats + uplift_chats, 2),
                "incident_adjusted_fte": (None if required is None
                                          else round(required + uplift_fte, 4)),
                "incident_uplift_sources": uplift_hour.get("sources") or [],
            })

        weekday = WEEKDAYS_RU[target_day.weekday()]
        day_uplift_chats = sum(r["incident_uplift_chats"] for r in hourly_forecast)
        day_chats = sum(r["forecast_chats"] for r in hourly_forecast)
        day_uplift_fte = (None if capacity is None
                          else sum(r["incident_uplift_fte"] for r in hourly_forecast))
        day_fte_hours = (None if capacity is None
                         else sum(r["forecast_fte"] for r in hourly_forecast))
        days.append({
            "forecast_date": target_day.isoformat(),
            "weekday": target_day.weekday(),
            "short": weekday["short"],
            "label": weekday["label"],
            "sources": sources,
            "used_source_count": divisor,
            "forecast_chats": round(day_chats, 1),
            "forecast_fte_hours": _fte_round(day_fte_hours, 2),
            "peak_fte": (None if capacity is None else
                         round(max((r["forecast_fte"] for r in hourly_forecast), default=0.0), 2)),
            "incident_uplift_chats": round(day_uplift_chats, 1),
            "incident_uplift_fte_hours": _fte_round(day_uplift_fte, 2),
            "incident_uplift_ratio": round(day_uplift_chats / day_chats, 4) if day_chats > 0 else 0.0,
            "incident_uplift_window_active": window_active,
            "incident_future_weight": round(future_weight, 4),
            "incident_adjusted_chats": round(day_chats + day_uplift_chats, 1),
            "incident_adjusted_fte_hours": (None if day_fte_hours is None
                                            else round(day_fte_hours + day_uplift_fte, 2)),
            "hourly_forecast": hourly_forecast,
        })

    total_chats = round(sum(d["forecast_chats"] for d in days), 1)
    total_uplift_chats = round(sum(d["incident_uplift_chats"] for d in days), 1)
    total_fte_hours = (None if capacity is None
                       else round(sum(d["forecast_fte_hours"] for d in days), 2))
    total_uplift_fte_hours = (None if capacity is None
                              else round(sum(d["incident_uplift_fte_hours"] for d in days), 2))
    weekly_hours = float(settings["weekly_hours_per_operator"])
    shrink = min(max(float(settings["shrinkage_coeff"]), 0.01), 1.0)
    # Норма часов считается на длину периода, а не жёстко на неделю — как у линии.
    # Делим на ФАКТИЧЕСКУЮ норму и лишь отсекаем неположительную (как в
    # resource_fte/calculations.py). Пол max(1.0, ...) на этом месте занижал
    # потребность в разы: на однодневном периоде норма 0,14 ч подменялась единицей.
    period_hours_per_operator = weekly_hours * span / 7
    if capacity is None:
        operators = operators_with_shrinkage = None
    else:
        operators = (total_fte_hours / period_hours_per_operator
                     if period_hours_per_operator > 0 else 0.0)
        operators_with_shrinkage = operators / shrink
    current_fte = float(capacity_info.get("current_operator_fte") or 0.0)
    return {
        "week_start": period_start.isoformat(),
        "week_end": period_end.isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "base_week_starts": [item.isoformat() for item in base_weeks],
        "skipped_base_weeks": skipped_weeks,
        "settings": settings,
        "days": days,
        "capacity_unavailable": capacity is None,
        "operator_capacity": capacity_info,
        "totals": {
            "forecast_chats": total_chats,
            "forecast_fte_hours": total_fte_hours,
            "period_days": span,
            "period_hours_per_operator": round(period_hours_per_operator, 2),
            "operators": _fte_round(operators, 2),
            "operators_with_shrinkage": _fte_round(operators_with_shrinkage, 2),
            "peak_fte": (None if capacity is None else
                         round(max((d["peak_fte"] for d in days), default=0.0), 2)),
            "current_operator_fte": round(current_fte, 2),
            "operator_fte_gap": (None if operators_with_shrinkage is None
                                 else round(current_fte - operators_with_shrinkage, 2)),
            "head_count": capacity_info.get("head_count", 0),
            "capacity_per_hour": _fte_round(capacity),
            # Потребность не посчитана: ни одна цель по сервису не достижима.
            "capacity_unavailable": capacity is None,
            "target_reply_seconds": _to_int(settings.get("target_reply_seconds"), 300),
            "uplift_chats": total_uplift_chats,
            "uplift_fte_hours": total_uplift_fte_hours,
        },
    }


def _chat_operator_capacity_tx(cursor) -> Dict[str, Any]:
    """Текущий штат чат-направления по ставкам — аналог того, что линия берёт по своим."""
    cursor.execute(
        """
        SELECT COALESCE(u.rate, 1.0), COUNT(*)
        FROM users u
        JOIN directions d ON d.id = u.direction_id
        WHERE u.role = 'operator'
          AND COALESCE(u.status, 'working') = 'working'
          AND d.name ILIKE %s
        GROUP BY COALESCE(u.rate, 1.0)
        ORDER BY 1 DESC
        """,
        (CHAT_DIRECTION_NAME_PATTERN,),
    )
    rows = cursor.fetchall() or []
    rate_capacity = []
    head_count = 0
    current_fte = 0.0
    off_scale = []
    for rate_raw, count_raw in rows:
        rate = float(rate_raw or 1.0)
        count = int(count_raw or 0)
        if rate not in CHAT_RATES:
            # Ставку вне набора направления в расчёт не берём, но и не прячем:
            # это расхождение в карточках людей, и его должно быть видно.
            off_scale.append({"rate": rate, "count": count})
            continue
        head_count += count
        current_fte += count * rate
        rate_capacity.append({
            "rate": rate,
            "count": count,
            "daily_shift_capacity": count,
            "weekly_shift_capacity": count * WORK_DAYS_PER_OPERATOR_WEEK,
        })
    return {
        "head_count": head_count,
        "current_operator_fte": round(current_fte, 4),
        "rate_capacity": rate_capacity,
        "rates": list(CHAT_RATES),
        "off_scale_rates": off_scale,
    }


def _chat_direction_ids_tx(cursor) -> List[int]:
    cursor.execute("SELECT id FROM directions WHERE name ILIKE %s",
                   (CHAT_DIRECTION_NAME_PATTERN,))
    return [int(row[0]) for row in cursor.fetchall()]


def get_chat_schedule_inputs(db, period_start_value: Any) -> Dict[str, Any]:
    """Штат и переходящие ночные смены для планировщика графиков чата.

    Линия считает их НА СЕРВЕРЕ (`build_resource_schedule_preview`), а планировщик
    шлёт только период и шаблоны — один и тот же компонент на два направления.
    Пока чат брал эти данные из тела запроса, они всегда приходили пустыми:
    генератор не знал ни состава по ставкам, ни смены `20*08`, уходящей за полночь,
    и утро первого дня периода оставалось непокрытым.
    """
    period_start = _parse_date(period_start_value)
    if period_start is None:
        raise ValueError("INVALID_CHAT_DATE")

    from resource_fte_service import _resource_work_shift_carry_in_tx

    with db._get_cursor() as cursor:
        capacity_info = _chat_operator_capacity_tx(cursor)
        direction_ids = _chat_direction_ids_tx(cursor)
        # Направление не нашлось (например, его переименовали) — переносить чужие
        # ночные смены нельзя: в график чата уехали бы люди всей компании.
        carry_in = (
            _resource_work_shift_carry_in_tx(
                cursor, period_start, {"selected_direction_ids": direction_ids})
            if direction_ids else []
        )
    return {
        "operator_capacity": capacity_info,
        "carry_in_shifts": carry_in,
        "direction_ids": direction_ids,
    }


def get_chat_operator_availability(db, as_of_date_value: Optional[str] = None,
                                   forecast_week_start_value: Optional[str] = None,
                                   forecast_date_from_value: Optional[str] = None,
                                   forecast_date_to_value: Optional[str] = None) -> Dict[str, Any]:
    """Доступность людей на период — тем же расчётом, что у линии, но по чат-направлению.

    Настройки направлений в `resource_settings` общие, и делить их между разделами
    нельзя, поэтому список направлений передаём явно.
    """
    from resource_fte_service import get_resource_operator_availability_details

    with db._get_cursor() as cursor:
        direction_ids = _chat_direction_ids_tx(cursor)
    return get_resource_operator_availability_details(
        db,
        as_of_date_value=as_of_date_value,
        forecast_week_start_value=forecast_week_start_value,
        forecast_date_from_value=forecast_date_from_value,
        forecast_date_to_value=forecast_date_to_value,
        direction_ids_override=direction_ids,
    )


def _weekday_profile_tx(cursor, day_from: date, day_to: date) -> List[Dict[str, Any]]:
    """Средний профиль по дню недели и часу — то же, что «профиль» у линии.

    Знаменатель среднего — число ДНЕЙ ВЫБОРКИ этого дня недели, один и тот же для
    всех 24 часов. Считать его внутри пары (день недели, час) нельзя: ночной час,
    в который чат пришёл лишь в одну из двух пятниц, делился бы на единицу и
    показывал двойной объём, а «Дней в выборке» рядом — единицу вместо двойки.
    """
    cursor.execute(
        """
        WITH sample_days AS (
            SELECT DISTINCT day
            FROM c2d_requests
            WHERE request_type = %s AND request_start IS NOT NULL
              AND day BETWEEN %s AND %s
        ), weekday_days AS (
            SELECT EXTRACT(ISODOW FROM day)::int - 1 AS wd, COUNT(*)::int AS days
            FROM sample_days
            GROUP BY 1
        )
        SELECT w.wd,
               EXTRACT(HOUR FROM r.request_start)::int AS hh,
               COUNT(*)::float / w.days AS avg_chats,
               w.days
        FROM c2d_requests r
        JOIN weekday_days w ON w.wd = EXTRACT(ISODOW FROM r.day)::int - 1
        WHERE r.request_type = %s AND r.request_start IS NOT NULL
          AND r.day BETWEEN %s AND %s
        GROUP BY w.wd, 2, w.days
        ORDER BY 1, 2
        """,
        (CHAT_REQUEST_TYPE, day_from, day_to, CHAT_REQUEST_TYPE, day_from, day_to),
    )
    by_weekday: Dict[int, Dict[str, Any]] = {}
    for weekday, hour, avg_chats, days in cursor.fetchall():
        entry = by_weekday.setdefault(int(weekday), {
            "weekday": int(weekday),
            "short": WEEKDAYS_RU[int(weekday)]["short"],
            "label": WEEKDAYS_RU[int(weekday)]["label"],
            "days_in_sample": int(days),
            "hourly": [],
        })
        entry["hourly"].append({"hour": int(hour), "avg_chats": round(float(avg_chats), 2)})
    for entry in by_weekday.values():
        entry["avg_chats"] = round(sum(item["avg_chats"] for item in entry["hourly"]), 1)
        entry["peak_hour"] = max(entry["hourly"], key=lambda x: x["avg_chats"])["hour"] \
            if entry["hourly"] else None
    return [by_weekday[key] for key in sorted(by_weekday)]


def _daily_history_tx(cursor, day_from: date, day_to: date) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT day, COUNT(*)
        FROM c2d_requests
        WHERE request_type = %s AND day BETWEEN %s AND %s
        GROUP BY 1 ORDER BY 1
        """,
        (CHAT_REQUEST_TYPE, day_from, day_to),
    )
    rows = []
    for day_value, count in cursor.fetchall():
        weekday = WEEKDAYS_RU[day_value.weekday()]
        rows.append({
            "date": day_value.isoformat(),
            "weekday": day_value.weekday(),
            "short": weekday["short"],
            "chats": int(count),
        })
    return rows


def _channel_split_tx(cursor, day_from: date, day_to: date) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT COALESCE(NULLIF(channel_name, ''), 'Без канала') AS channel, COUNT(*)
        FROM c2d_requests
        WHERE request_type = %s AND day BETWEEN %s AND %s
        GROUP BY 1 ORDER BY 2 DESC
        """,
        (CHAT_REQUEST_TYPE, day_from, day_to),
    )
    rows = cursor.fetchall()
    total = sum(int(item[1]) for item in rows) or 1
    return [{"channel": item[0], "chats": int(item[1]),
             "share": round(int(item[1]) / total, 4)} for item in rows]


def _stored_chat_hours_tx(cursor, day_from: date,
                          day_to: date) -> Dict[str, Dict[int, Dict[str, Any]]]:
    cursor.execute(
        """
        SELECT report_date, hour, forecast_chats, forecast_fte, actual_chats,
               actual_online_hours, answered_in_target, answered_total
        FROM chat_resource_hours
        WHERE report_date BETWEEN %s AND %s
        ORDER BY report_date, hour
        """,
        (day_from, day_to),
    )
    out: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for row in cursor.fetchall():
        out.setdefault(row[0].isoformat(), {})[int(row[1])] = {
            "forecast_chats": _to_float(row[2]),
            "forecast_fte": _to_float(row[3]),
            "actual_chats": _to_int(row[4]),
            "actual_online_hours": _to_float(row[5]),
            "answered_in_target": _to_int(row[6]),
            "answered_total": _to_int(row[7]),
        }
    return out


def _hour_label(hour: int) -> str:
    return "%02d:00" % int(hour)


def get_chat_day(db, day_value: Any) -> Dict[str, Any]:
    """Почасовая детализация одного дня: прогноз против факта и первый ответ."""
    day = _parse_date(day_value)
    if day is None:
        raise ValueError("INVALID_CHAT_DATE")
    settings = get_chat_settings(db)
    capacity = _capacity_for_math(settings)
    target_first = settings["target_first_reply_seconds"]
    key = day.isoformat()

    with db._get_cursor() as cursor:
        stored = _stored_chat_hours_tx(cursor, day, day).get(key, {})
        volume = _hourly_volume_tx(cursor, day, day).get(key, {})
        online = _online_hours_tx(cursor, day, day).get(key, {})
        replies = _reply_stats_tx(cursor, day, day, target_first).get(key, {})

    if not stored:
        # Дня ещё нет в истории пересчётов — собираем прогноз на лету, чтобы карточка
        # дня открывалась всегда, а не только после «Пересчитать».
        built = build_chat_forecast(db, day, settings, period_end_value=day)["days"][0]
        stored = {row["hour"]: {"forecast_chats": row["forecast_chats"],
                                "forecast_fte": row["forecast_fte"]}
                  for row in built["hourly_forecast"]}

    hours = []
    for hour in range(24):
        forecast_row = stored.get(hour, {})
        stats = replies.get(hour) or {}
        chats = int(volume.get(hour, 0))
        forecast_chats = _to_float(forecast_row.get("forecast_chats"))
        # Без ёмкости часов чатника не существует — ноль на их месте выглядел бы
        # идеальным перекрытием, поэтому и потребность, и разницу оставляем пустыми.
        forecast_fte = (None if capacity is None
                        else _to_float(forecast_row.get("forecast_fte")))
        worked = _to_float(online.get(hour), 0.0)
        hours.append({
            "hour": hour,
            "hour_label": _hour_label(hour),
            "chats": chats,
            "forecast_chats": round(forecast_chats, 2),
            "forecast_fte": None if forecast_fte is None else round(forecast_fte, 4),
            "actual_online_hours": round(worked, 2),
            "in_target": int(stats.get("in_target") or 0),
            "answered": int(stats.get("answered") or 0),
            "no_reply": int(stats.get("no_reply") or 0),
            "avg_first_reply_seconds": stats.get("avg_first_reply"),
            "delta_fte": None if forecast_fte is None else round(worked - forecast_fte, 4),
        })

    total_chats = sum(row["chats"] for row in hours)
    total_in_target = sum(row["in_target"] for row in hours)
    total_answered = sum(row["answered"] for row in hours)
    total_no_reply = sum(row["no_reply"] for row in hours)
    total_online = sum(row["actual_online_hours"] for row in hours)
    total_forecast_fte = (None if capacity is None
                          else sum(row["forecast_fte"] for row in hours))
    weighted_first_reply = sum(
        (row["avg_first_reply_seconds"] or 0) * row["answered"] for row in hours)
    weekday = WEEKDAYS_RU[day.weekday()]
    return {
        "date": key,
        "weekday": day.weekday(),
        "short": weekday["short"],
        "label": weekday["label"],
        "summary": {
            "chats": total_chats,
            "forecast_chats": round(sum(row["forecast_chats"] for row in hours), 1),
            "forecast_fte_hours": (None if total_forecast_fte is None
                                   else round(total_forecast_fte, 2)),
            "actual_online_hours": round(total_online, 2),
            "delta_fte_hours": (None if total_forecast_fte is None
                                else round(total_online - total_forecast_fte, 2)),
            "in_target": total_in_target,
            "answered": total_answered,
            "no_reply": total_no_reply,
            "in_target_share": round(total_in_target / total_chats, 4) if total_chats else 0.0,
            "avg_first_reply_seconds": round(weighted_first_reply / total_answered, 1)
            if total_answered else None,
            "target_first_reply_seconds": target_first,
            "capacity_per_hour": None if capacity is None else round(capacity, 4),
            "capacity_unavailable": capacity is None,
        },
        "hours": hours,
    }


def _analytics_range(cursor, date_from: Any, date_to: Any) -> Optional[Tuple[date, date]]:
    day_to = _parse_date(date_to)
    day_from = _parse_date(date_from)
    if day_to is None:
        day_to = _latest_chat_day_tx(cursor)
    if day_to is None:
        return None
    if day_from is None:
        day_from = day_to - timedelta(days=13)
    if day_from > day_to:
        day_from, day_to = day_to, day_from
    return day_from, day_to


def get_chat_analytics(db, date_from: Any = None, date_to: Any = None) -> Dict[str, Any]:
    """Вкладка «Чаты»: объём, первый ответ против цели и часы риска.

    Чатовая альтернатива вкладке «Звонки» линии. Метрика сервиса тут одна — первый
    ответ: только он лежит в нашей базе, всё остальное было бы декорацией.
    """
    settings = get_chat_settings(db)
    target_first = settings["target_first_reply_seconds"]
    capacity = _capacity_for_math(settings)

    with db._get_cursor() as cursor:
        bounds = _analytics_range(cursor, date_from, date_to)
        if bounds is None:
            return {"days": [], "hours": [], "channels": [], "risk_hours": [],
                    "totals": {"chats": 0, "in_target": 0, "no_reply": 0,
                               "in_target_share": 0.0, "avg_first_reply_seconds": None,
                               "target_first_reply_seconds": target_first,
                               "capacity_per_hour": None if capacity is None else round(capacity, 4),
                               "capacity_unavailable": capacity is None},
                    "range": {"from": None, "to": None, "days": 0}}
        day_from, day_to = bounds
        volume = _hourly_volume_tx(cursor, day_from, day_to)
        online = _online_hours_tx(cursor, day_from, day_to)
        replies = _reply_stats_tx(cursor, day_from, day_to, target_first)
        channels = _channel_split_tx(cursor, day_from, day_to)
        stored = _stored_chat_hours_tx(cursor, day_from, day_to)

    days: List[Dict[str, Any]] = []
    by_hour = {hour: {"hour": hour, "chats": 0, "in_target": 0, "answered": 0,
                      "no_reply": 0, "first_reply_sum": 0.0, "online_hours": 0.0,
                      "forecast_chats": 0.0}
               for hour in range(24)}
    span = (day_to - day_from).days + 1
    for offset in range(span):
        current = day_from + timedelta(days=offset)
        key = current.isoformat()
        per_hour = volume.get(key, {})
        replies_day = replies.get(key, {})
        online_day = online.get(key, {})
        stored_day = stored.get(key, {})
        if not per_hour and not replies_day and not online_day:
            # Пропускаем только по-настоящему пустой день. Раньше выход стоял до того,
            # как учтены онлайн-часы, и день без чатов (сбой выгрузки, праздник) уносил
            # с собой ФАКТ отработанных часов — итог по людям молча уменьшался.
            continue
        day_chats = day_in_target = day_answered = day_no_reply = 0
        day_first_reply_sum = 0.0
        for hour in range(24):
            chats = int(per_hour.get(hour, 0))
            stats = replies_day.get(hour) or {}
            answered = int(stats.get("answered") or 0)
            in_target = int(stats.get("in_target") or 0)
            no_reply = int(stats.get("no_reply") or 0)
            avg_first = stats.get("avg_first_reply") or 0.0
            day_chats += chats
            day_answered += answered
            day_in_target += in_target
            day_no_reply += no_reply
            day_first_reply_sum += avg_first * answered
            bucket = by_hour[hour]
            bucket["chats"] += chats
            bucket["answered"] += answered
            bucket["in_target"] += in_target
            bucket["no_reply"] += no_reply
            bucket["first_reply_sum"] += avg_first * answered
            bucket["online_hours"] += _to_float(online_day.get(hour), 0.0)
            bucket["forecast_chats"] += _to_float((stored_day.get(hour) or {}).get("forecast_chats"))
        weekday = WEEKDAYS_RU[current.weekday()]
        days.append({
            "date": key,
            "weekday": current.weekday(),
            "short": weekday["short"],
            "chats": day_chats,
            "answered": day_answered,
            "in_target": day_in_target,
            "no_reply": day_no_reply,
            "in_target_share": round(day_in_target / day_chats, 4) if day_chats else 0.0,
            "avg_first_reply_seconds": round(day_first_reply_sum / day_answered, 1)
            if day_answered else None,
            "forecast_chats": round(sum(
                _to_float((stored_day.get(hour) or {}).get("forecast_chats"))
                for hour in range(24)), 1),
            "actual_online_hours": round(sum(
                _to_float(online_day.get(hour), 0.0) for hour in range(24)), 2),
        })

    hours = []
    for hour in range(24):
        bucket = by_hour[hour]
        chats = bucket["chats"]
        answered = bucket["answered"]
        hours.append({
            "hour": hour,
            "hour_label": _hour_label(hour),
            "chats": chats,
            "answered": answered,
            "in_target": bucket["in_target"],
            "no_reply": bucket["no_reply"],
            "in_target_share": round(bucket["in_target"] / chats, 4) if chats else 0.0,
            "avg_first_reply_seconds": round(bucket["first_reply_sum"] / answered, 1)
            if answered else None,
            "actual_online_hours": round(bucket["online_hours"], 2),
            "forecast_chats": round(bucket["forecast_chats"], 1),
        })

    # Час риска — это не просто худшая доля, а худшая доля НА ОБЪЁМЕ: провалить
    # ночной час с тремя чатами дешевле, чем дневной с сотней.
    risk_hours = sorted(
        (row for row in hours if row["chats"] > 0),
        key=lambda row: (1.0 - row["in_target_share"]) * row["chats"],
        reverse=True,
    )[:5]

    total_chats = sum(row["chats"] for row in hours)
    total_answered = sum(row["answered"] for row in hours)
    total_in_target = sum(row["in_target"] for row in hours)
    total_first_reply = sum(by_hour[hour]["first_reply_sum"] for hour in range(24))
    return {
        "days": days,
        "hours": hours,
        "channels": channels,
        "risk_hours": risk_hours,
        "totals": {
            "chats": total_chats,
            "answered": total_answered,
            "in_target": total_in_target,
            "no_reply": sum(row["no_reply"] for row in hours),
            "in_target_share": round(total_in_target / total_chats, 4) if total_chats else 0.0,
            "avg_first_reply_seconds": round(total_first_reply / total_answered, 1)
            if total_answered else None,
            "target_first_reply_seconds": target_first,
            "capacity_per_hour": None if capacity is None else round(capacity, 4),
            "capacity_unavailable": capacity is None,
            "actual_online_hours": round(sum(row["actual_online_hours"] for row in hours), 2),
        },
        "range": {"from": day_from.isoformat(), "to": day_to.isoformat(), "days": len(days)},
    }


CHAT_RECALCULATE_DAYS = 14


def recalculate_chat_forecast(db, as_of_date: Any = None,
                              days: int = CHAT_RECALCULATE_DAYS) -> Dict[str, Any]:
    """Складывает в `chat_resource_hours` прошедшие дни: прогноз против факта.

    Прогноз каждого дня пересобирается НА ТУ ДАТУ — по неделям, что были известны
    тогда. Иначе «выдержка прогноза» превращается в подгонку задним числом. Запись
    идемпотентна: повторный пересчёт того же окна ничего не задваивает.
    """
    window = min(max(_to_int(days, CHAT_RECALCULATE_DAYS), 1), 60)
    settings = get_chat_settings(db)
    target_first = settings["target_first_reply_seconds"]

    with db._get_cursor() as cursor:
        anchor = _parse_date(as_of_date)
        if anchor is None:
            latest = _latest_chat_day_tx(cursor)
            anchor = (latest + timedelta(days=1)) if latest else date.today()
    day_to = anchor - timedelta(days=1)
    day_from = day_to - timedelta(days=window - 1)
    if day_to < day_from:
        return {"from": None, "to": None, "days": 0, "rows": 0}

    forecast_by_day: Dict[str, Dict[int, Dict[str, float]]] = {}
    for offset in range(window):
        current = day_from + timedelta(days=offset)
        built = build_chat_forecast(db, current, settings, period_end_value=current)
        forecast_by_day[current.isoformat()] = {
            row["hour"]: row for row in built["days"][0]["hourly_forecast"]
        }

    with db._get_cursor() as cursor:
        volume = _hourly_volume_tx(cursor, day_from, day_to)
        online = _online_hours_tx(cursor, day_from, day_to)
        replies = _reply_stats_tx(cursor, day_from, day_to, target_first)
        rows = []
        for offset in range(window):
            current = day_from + timedelta(days=offset)
            key = current.isoformat()
            per_hour = volume.get(key, {})
            online_day = online.get(key, {})
            replies_day = replies.get(key, {})
            forecast_day = forecast_by_day.get(key, {})
            for hour in range(24):
                stats = replies_day.get(hour) or {}
                row = forecast_day.get(hour) or {}
                rows.append((
                    current, hour,
                    _to_float(row.get("forecast_chats")), _to_float(row.get("forecast_fte")),
                    int(per_hour.get(hour, 0)), _to_float(online_day.get(hour), 0.0),
                    int(stats.get("in_target") or 0), int(stats.get("total") or 0),
                ))
        execute_values(
            cursor,
            """
            INSERT INTO chat_resource_hours (
                report_date, hour, forecast_chats, forecast_fte,
                actual_chats, actual_online_hours, answered_in_target, answered_total
            )
            VALUES %s
            ON CONFLICT (report_date, hour) DO UPDATE SET
                forecast_chats = EXCLUDED.forecast_chats,
                forecast_fte = EXCLUDED.forecast_fte,
                actual_chats = EXCLUDED.actual_chats,
                actual_online_hours = EXCLUDED.actual_online_hours,
                answered_in_target = EXCLUDED.answered_in_target,
                answered_total = EXCLUDED.answered_total,
                computed_at = now()
            """,
            rows,
        )
        written = len(rows)

    return {
        "from": day_from.isoformat(),
        "to": day_to.isoformat(),
        "days": window,
        "rows": written,
        "target_first_reply_seconds": target_first,
        "capacity_per_hour": settings["capacity_per_hour"],
    }


# Календарь подсвечивает дни, по которым есть чаты. Глубже полугода Chat2Desk всё
# равно не отдаёт, а полный скан таблицы ради подсветки не нужен.
CHAT_COVERED_DAYS_LOOKBACK = 180


def get_chat_overview(db, week_start_value: Any = None,
                      period_end_value: Any = None,
                      date_from: Any = None,
                      date_to: Any = None,
                      history_days: Any = None) -> Dict[str, Any]:
    """Витрина раздела: прогноз на период, факт за период истории, наплыв и каналы.

    Периода два и они независимы: прогноз смотрит вперёд, история — назад. Раньше
    раздел жил на одной неделе вперёд и фиксированной глубине назад, из-за чего
    «сравнить прогноз с фактом» приходилось делать глазами.
    """
    depth = _to_int(history_days, 45)
    depth = min(max(depth, 7), 120)
    # Наплыв привязан к СЕГОДНЯ, а не к просматриваемой неделе: это свойство последних
    # дней (у линии — окно 6 дней до текущей даты), а не периода, который открыл
    # пользователь. Раньше сюда уходил `week_start_value`, и пролистав вперёд на пять
    # недель человек получал «прирост», перенесённый на чужое окно. Без аргумента
    # профиль встаёт на последний день с данными — от него же считается и окно действия.
    uplift = build_chat_uplift_profile(db)
    forecast = build_chat_forecast(db, week_start_value, period_end_value=period_end_value,
                                   uplift_profile=uplift)
    settings = forecast["settings"]
    target_first = settings["target_first_reply_seconds"]

    history: List[Dict[str, Any]] = []
    channels: List[Dict[str, Any]] = []
    profile: List[Dict[str, Any]] = []
    covered: List[str] = []
    coverage = {"from": None, "to": None, "days": 0}
    actual_range = None
    volume: Dict[str, Any] = {}
    online: Dict[str, Any] = {}
    replies: Dict[str, Any] = {}
    period_volume: Dict[str, Any] = {}
    period_online: Dict[str, Any] = {}
    period_replies: Dict[str, Any] = {}

    with db._get_cursor() as cursor:
        latest = _latest_chat_day_tx(cursor)
        if latest is not None:
            # Границы истории: явные даты, а если их не прислали — прежняя глубина.
            history_to = _parse_date(date_to) or latest
            history_from = _parse_date(date_from) or (history_to - timedelta(days=depth - 1))
            if history_from > history_to:
                history_from, history_to = history_to, history_from
            actual_range = (history_from, history_to)
            history = _daily_history_tx(cursor, history_from, history_to)
            base_from = min(_parse_date(x) for x in forecast["base_week_starts"])
            base_to = max(_parse_date(x) for x in forecast["base_week_starts"]) + timedelta(days=6)
            channels = _channel_split_tx(cursor, base_from, base_to)
            profile = _weekday_profile_tx(cursor, base_from, base_to)
            coverage = {"from": history_from.isoformat(), "to": history_to.isoformat(),
                        "days": len(history)}
            covered = sorted(_covered_days_tx(
                cursor, latest - timedelta(days=CHAT_COVERED_DAYS_LOOKBACK), latest))
            volume = _hourly_volume_tx(cursor, history_from, history_to)
            online = _online_hours_tx(cursor, history_from, history_to)
            replies = _reply_stats_tx(cursor, history_from, history_to, target_first)
            # Факт по дням самого периода ПРОГНОЗА — чтобы прошедшие дни в списке
            # показывали не только план, но и чем он кончился.
            period_start = _parse_date(forecast["period_start"])
            period_end = _parse_date(forecast["period_end"])
            period_volume = _hourly_volume_tx(cursor, period_start, period_end)
            period_online = _online_hours_tx(cursor, period_start, period_end)
            period_replies = _reply_stats_tx(cursor, period_start, period_end, target_first)

    actual_days: List[Dict[str, Any]] = []
    if actual_range is not None:
        for offset in range((actual_range[1] - actual_range[0]).days + 1):
            current = actual_range[0] + timedelta(days=offset)
            key = current.isoformat()
            per_hour = volume.get(key, {})
            if not per_hour:
                continue
            replies_day = replies.get(key, {})
            chats = sum(per_hour.values())
            in_target = sum((replies_day.get(h) or {}).get("in_target", 0) for h in range(24))
            worked = sum(_to_float(v) for v in (online.get(key, {}) or {}).values())
            weekday = WEEKDAYS_RU[current.weekday()]
            actual_days.append({
                "date": key,
                "short": weekday["short"],
                "chats": int(chats),
                "actual_online_hours": round(worked, 2),
                "in_target": int(in_target),
                "in_target_share": round(in_target / chats, 4) if chats else 0.0,
            })

    # Прошедшие дни периода прогноза дополняем фактом — план без исхода бесполезен.
    actual_fte_hours = 0.0
    comparable_forecast_hours = 0.0
    comparable_days = 0
    for day in forecast["days"]:
        key = day["forecast_date"]
        per_hour = period_volume.get(key, {})
        if not per_hour:
            day["has_actual"] = False
            continue
        replies_day = period_replies.get(key, {})
        online_day = period_online.get(key, {})
        chats = int(sum(per_hour.values()))
        worked = round(sum(_to_float(v) for v in online_day.values()), 2)
        in_target = sum((replies_day.get(h) or {}).get("in_target", 0) for h in range(24))
        actual_fte_hours += worked
        comparable_forecast_hours += _to_float(day["forecast_fte_hours"])
        comparable_days += 1
        day["has_actual"] = True
        day["actual_chats"] = chats
        day["actual_online_hours"] = worked
        day["actual_delta_fte_hours"] = (
            None if day["forecast_fte_hours"] is None
            else round(worked - day["forecast_fte_hours"], 2))
        day["in_target_share"] = round(in_target / chats, 4) if chats else 0.0

    # Разница «факт − прогноз» считается по ТЕМ ЖЕ дням, где факт есть. Раньше факт
    # копился по прошедшим дням, а вычитался из прогноза за весь период: прогноз на
    # следующую неделю (факта нет вовсе) давал «−840 часов» при нулевом факте, а
    # половина прошедшей недели — «−480» там, где план сошёлся день в день.
    # `actual_comparable_days` говорит витрине, по скольким дням сравнение вообще шло.
    # Без ёмкости сравнивать не с чем: прогноза в часах нет, и «факт − прогноз»
    # выродился бы в сам факт.
    plan_missing = bool(forecast["totals"].get("capacity_unavailable"))
    forecast["totals"]["actual_fte_hours"] = round(actual_fte_hours, 2)
    forecast["totals"]["actual_forecast_hours"] = (
        None if plan_missing else round(comparable_forecast_hours, 2))
    forecast["totals"]["actual_comparable_days"] = comparable_days
    forecast["totals"]["actual_forecast_delta"] = (
        None if plan_missing else round(actual_fte_hours - comparable_forecast_hours, 2))
    # Из какого окна взят прирост и сколько дней периода в это окно попало — иначе
    # «+N чатов» в шапке выглядит свойством открытой недели.
    forecast["totals"]["uplift_window_start"] = uplift.get("forecast_window_start")
    forecast["totals"]["uplift_window_end"] = uplift.get("forecast_window_end")
    forecast["totals"]["uplift_window_days_in_period"] = sum(
        1 for day in forecast["days"] if day.get("incident_uplift_window_active"))

    return {
        "forecast": forecast,
        "settings": settings,
        "capacity_explain": _capacity_explain(settings),
        "uplift": uplift,
        "history": history,
        "channels": channels,
        "weekday_profile": profile,
        "history_coverage": coverage,
        "covered_days": covered,
        "actual": {"days": actual_days,
                   "from": actual_range[0].isoformat() if actual_range else None,
                   "to": actual_range[1].isoformat() if actual_range else None},
        "latest_chat_day": latest.isoformat() if latest else None,
    }


# --- «Биллинг чата» -----------------------------------------------------------------
# Полный аналог биллинга линии, но на наших данных: Oktell здесь не нужен, всё лежит в
# c2d_requests. Разрез у линии «таксопарк / номер линии»; у нас таксопарк лежит в
# `channel_name` (Техподдержка iTaxi, Jana Taxi, Ноль такси и ещё десяток), а `transport`
# — это канал связи, которым клиент пришёл. Отсюда разрез «парк / транспорт».
#
# Колонки читаются так же, как на линии, только по-чатовому:
#   Поступило → чатов; Обслужено → отвечено (reaction_time есть); Потеряно → без ответа;
#   Ср. ожидание → средний первый ответ; AR → доля отвеченных;
#   SL → доля первых ответов в пределах порога.
# Колонок «Ср. разговор», «Время разговора» и «Общее время» здесь НЕТ: это время
# обработки, которого в чатовой модели быть не должно (см. шапку модуля).

CHAT_BILLING_MAX_RANGE_DAYS = 31
CHAT_BILLING_SL_SECONDS_LIMITS = (1, 600)
CHAT_BILLING_SL_DEFAULT_SECONDS = DEFAULT_CHAT_SETTINGS["target_first_reply_seconds"]
CHAT_BILLING_PER_PAGE_LIMITS = (1, 100)
# Потолок построчной выгрузки: Excel на большем всё равно нечитаем, а полный скан
# периода без границы кладёт запрос.
CHAT_BILLING_DETAIL_EXPORT_LIMIT = 10000

CHAT_BILLING_NO_PARK = "Без парка"
CHAT_BILLING_NO_TRANSPORT = "Без канала"

# Счётчики сырые: доли и средние считает витрина, как у линии, — иначе одно и то же
# число округлялось бы дважды и таблица не сходилась бы с итогом.
CHAT_BILLING_METRICS = (
    "chats", "answered", "no_reply", "answered_sl", "first_reply_seconds",
)
CHAT_BILLING_OPERATOR_METRICS = CHAT_BILLING_METRICS + (
    "incoming_messages", "outgoing_messages",
)

CHAT_BILLING_GROUP_BY = ("park", "transport")

_CHAT_BILLING_PARK_SQL = "COALESCE(NULLIF(r.channel_name, ''), '%s')" % CHAT_BILLING_NO_PARK
_CHAT_BILLING_TRANSPORT_SQL = (
    "COALESCE(NULLIF(r.transport, ''), '%s')" % CHAT_BILLING_NO_TRANSPORT)
_CHAT_BILLING_OPERATOR_SQL = "COALESCE(NULLIF(u.name, ''), NULLIF(r.c2d_operator_name, ''))"


def _chat_billing_window(day_from: date, day_to: date,
                         minute_from: int, minute_to: int) -> Tuple[str, List[Any]]:
    """Общий WHERE биллинга: период по дню плюс окно времени суток по началу чата.

    Окно накладывается только когда оно уже полных суток: лишнее условие в WHERE
    отрезает индекс по `day` и заставляет читать таблицу целиком.
    """
    where = ["r.request_type = %s", "r.request_start IS NOT NULL", "r.day BETWEEN %s AND %s"]
    params: List[Any] = [CHAT_REQUEST_TYPE, day_from, day_to]
    if int(minute_from) > 0 or int(minute_to) < 1439:
        where.append("(EXTRACT(HOUR FROM r.request_start)::int * 60"
                     " + EXTRACT(MINUTE FROM r.request_start)::int) BETWEEN %s AND %s")
        params.extend([int(minute_from), int(minute_to)])
    return " AND ".join(where), params


def _chat_billing_blank(metrics: Tuple[str, ...]) -> Dict[str, int]:
    return {key: 0 for key in metrics}


def _chat_billing_merge(target: Dict[str, int], row: Dict[str, int],
                        metrics: Tuple[str, ...]) -> None:
    for key in metrics:
        target[key] += row[key]


def _chat_billing_rows_tx(cursor, day_from: date, day_to: date, minute_from: int,
                          minute_to: int, sl_seconds: int,
                          group_by: str) -> List[Tuple[Any, ...]]:
    """Строки (день x парк [x транспорт]) за период."""
    where, params = _chat_billing_window(day_from, day_to, minute_from, minute_to)
    transport_sql = _CHAT_BILLING_TRANSPORT_SQL if group_by == "transport" else "''"
    cursor.execute(
        f"""
        SELECT r.day,
               {_CHAT_BILLING_PARK_SQL} AS park,
               {transport_sql} AS transport_name,
               COUNT(*)::int,
               COUNT(r.reaction_time)::int,
               COUNT(*) FILTER (WHERE r.reaction_time <= %s)::int,
               COALESCE(SUM(r.reaction_time), 0)::float
        FROM c2d_requests r
        WHERE {where}
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
        """,
        [int(sl_seconds)] + params,
    )
    return cursor.fetchall()


def get_chat_billing_report(db, day_from: date, day_to: date, minute_from: int = 0,
                            minute_to: int = 1439,
                            sl_seconds: int = CHAT_BILLING_SL_DEFAULT_SECONDS,
                            group_by: str = "park") -> Dict[str, Any]:
    """{days, parks, totals} — та же форма ответа, что у биллинга линии."""
    include_transport = group_by == "transport"
    with db._get_cursor() as cursor:
        raw_rows = _chat_billing_rows_tx(cursor, day_from, day_to, minute_from,
                                         minute_to, sl_seconds, group_by)

    days_map: Dict[str, Dict[Tuple[str, str], Dict[str, int]]] = {}
    parks_map: Dict[Tuple[str, str], Dict[str, int]] = {}
    totals = _chat_billing_blank(CHAT_BILLING_METRICS)
    for day_value, park, transport_name, chats, answered, answered_sl, reply_sum in raw_rows:
        chats = max(0, int(chats or 0))
        answered = max(0, int(answered or 0))
        row = {
            "chats": chats,
            "answered": answered,
            # «Потеряно» в чате — это чат, на который так и не ответили. Он не
            # теряется, как звонок: он висит открытым, пока клиент ждёт.
            "no_reply": max(0, chats - answered),
            "answered_sl": max(0, int(answered_sl or 0)),
            "first_reply_seconds": int(round(_to_float(reply_sum, 0.0))),
        }
        key = (str(park or ""), str(transport_name or "") if include_transport else "")
        day_key = day_value.isoformat()
        _chat_billing_merge(days_map.setdefault(day_key, {}).setdefault(
            key, _chat_billing_blank(CHAT_BILLING_METRICS)), row, CHAT_BILLING_METRICS)
        _chat_billing_merge(parks_map.setdefault(
            key, _chat_billing_blank(CHAT_BILLING_METRICS)), row, CHAT_BILLING_METRICS)
        _chat_billing_merge(totals, row, CHAT_BILLING_METRICS)

    def _sorted_parks(source: Dict[Tuple[str, str], Dict[str, int]]) -> List[Dict[str, Any]]:
        items = []
        for (park, transport_name), metrics in source.items():
            item = {"park": park, **metrics}
            if include_transport:
                item["transport"] = transport_name
            items.append(item)
        items.sort(key=lambda item: (-item["chats"], item["park"], item.get("transport") or ""))
        return items

    days = []
    for day_key in sorted(days_map):
        park_rows = _sorted_parks(days_map[day_key])
        day_totals = _chat_billing_blank(CHAT_BILLING_METRICS)
        for item in park_rows:
            _chat_billing_merge(day_totals, item, CHAT_BILLING_METRICS)
        days.append({"date": day_key, "parks": park_rows, "totals": day_totals})

    return {"days": days, "parks": _sorted_parks(parks_map), "totals": totals}


def get_chat_billing_operators(db, day_from: date, day_to: date, minute_from: int = 0,
                               minute_to: int = 1439,
                               sl_seconds: int = CHAT_BILLING_SL_DEFAULT_SECONDS
                               ) -> Dict[str, Any]:
    """{days, operators, totals} — тот же разрез, но по чатникам.

    Имя берём из нашей карточки, а если чат ещё не привязан к человеку — из
    Chat2Desk. Непривязанные чаты в разрез по людям не попадают: они не про то,
    как работал конкретный чатник.
    """
    where, params = _chat_billing_window(day_from, day_to, minute_from, minute_to)
    with db._get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT r.day,
                   {_CHAT_BILLING_OPERATOR_SQL} AS operator_name,
                   COUNT(*)::int,
                   COUNT(r.reaction_time)::int,
                   COUNT(*) FILTER (WHERE r.reaction_time <= %s)::int,
                   COALESCE(SUM(r.reaction_time), 0)::float,
                   COALESCE(SUM(r.incoming_messages), 0)::int,
                   COALESCE(SUM(r.outgoing_messages), 0)::int
            FROM c2d_requests r
            LEFT JOIN users u ON u.id = r.operator_id
            WHERE {where} AND {_CHAT_BILLING_OPERATOR_SQL} IS NOT NULL
            GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            [int(sl_seconds)] + params,
        )
        raw_rows = cursor.fetchall()

    days_map: Dict[str, Dict[str, Dict[str, int]]] = {}
    operators_map: Dict[str, Dict[str, int]] = {}
    totals = _chat_billing_blank(CHAT_BILLING_OPERATOR_METRICS)
    for (day_value, operator_name, chats, answered, answered_sl,
         reply_sum, incoming, outgoing) in raw_rows:
        chats = max(0, int(chats or 0))
        answered = max(0, int(answered or 0))
        row = {
            "chats": chats,
            "answered": answered,
            "no_reply": max(0, chats - answered),
            "answered_sl": max(0, int(answered_sl or 0)),
            "first_reply_seconds": int(round(_to_float(reply_sum, 0.0))),
            "incoming_messages": max(0, int(incoming or 0)),
            "outgoing_messages": max(0, int(outgoing or 0)),
        }
        entry = days_map.setdefault(day_value.isoformat(), {}).setdefault(
            str(operator_name), _chat_billing_blank(CHAT_BILLING_OPERATOR_METRICS))
        _chat_billing_merge(entry, row, CHAT_BILLING_OPERATOR_METRICS)

    days = []
    for day_key in sorted(days_map):
        rows = [{"operator": name, **metrics} for name, metrics in days_map[day_key].items()]
        rows.sort(key=lambda item: (-item["chats"], item["operator"]))
        day_totals = _chat_billing_blank(CHAT_BILLING_OPERATOR_METRICS)
        for item in rows:
            _chat_billing_merge(day_totals, item, CHAT_BILLING_OPERATOR_METRICS)
            _chat_billing_merge(operators_map.setdefault(
                item["operator"], _chat_billing_blank(CHAT_BILLING_OPERATOR_METRICS)),
                item, CHAT_BILLING_OPERATOR_METRICS)
        _chat_billing_merge(totals, day_totals, CHAT_BILLING_OPERATOR_METRICS)
        days.append({"date": day_key, "operators": rows, "totals": day_totals})

    operators = [{"operator": name, **metrics} for name, metrics in operators_map.items()]
    operators.sort(key=lambda item: (-item["chats"], item["operator"]))
    return {"days": days, "operators": operators, "totals": totals}


def _chat_billing_detail_row(raw: Tuple[Any, ...], sl_seconds: int) -> Dict[str, Any]:
    (request_id, started_at, park, transport_name, client, operator_name,
     reply_seconds, incoming, outgoing) = raw
    answered = reply_seconds is not None
    return {
        "id": int(request_id or 0),
        "started_at": started_at.strftime("%Y-%m-%d %H:%M:%S") if started_at else "",
        "park": str(park or ""),
        "transport": str(transport_name or ""),
        "client": str(client or ""),
        "operator": str(operator_name or ""),
        "first_reply_seconds": int(reply_seconds) if answered else None,
        "no_reply": 0 if answered else 1,
        "answered_sl": 1 if answered and int(reply_seconds) <= int(sl_seconds) else 0,
        "incoming_messages": max(0, int(incoming or 0)),
        "outgoing_messages": max(0, int(outgoing or 0)),
    }


_CHAT_BILLING_DETAIL_SELECT = f"""
    r.request_id,
    r.request_start,
    {_CHAT_BILLING_PARK_SQL} AS park,
    {_CHAT_BILLING_TRANSPORT_SQL} AS transport_name,
    COALESCE(NULLIF(r.client_name, ''), NULLIF(r.client_phone, ''), '') AS client,
    COALESCE({_CHAT_BILLING_OPERATOR_SQL}, '') AS operator_name,
    r.reaction_time,
    r.incoming_messages,
    r.outgoing_messages
"""


def get_chat_billing_details(db, day_from: date, day_to: date, minute_from: int = 0,
                             minute_to: int = 1439,
                             sl_seconds: int = CHAT_BILLING_SL_DEFAULT_SECONDS,
                             page: int = 1, per_page: int = 25,
                             snapshot_id: Optional[int] = None) -> Dict[str, Any]:
    """Одна строка = один чат. Снимок по максимальному `request_id` держит выборку.

    Без снимка ночной синк Chat2Desk дописывает свежие чаты между переходами по
    страницам, и уже просмотренные строки съезжают вниз.
    """
    page = max(1, int(page))
    low, high = CHAT_BILLING_PER_PAGE_LIMITS
    per_page = max(low, min(high, int(per_page)))
    where, params = _chat_billing_window(day_from, day_to, minute_from, minute_to)
    if snapshot_id is not None:
        where += " AND r.request_id <= %s"
        params = params + [int(snapshot_id)]

    with db._get_cursor() as cursor:
        # Счётчик отдельным запросом, а не окном COUNT(*) OVER(): на странице за
        # хвостом выборки окно не возвращает ни строки, и общее число обнуляется.
        cursor.execute(
            f"SELECT COUNT(*)::int, MAX(r.request_id) FROM c2d_requests r WHERE {where}",
            params)
        head = cursor.fetchone() or (0, None)
        total = int(head[0] or 0)
        resolved_snapshot = snapshot_id if snapshot_id is not None else (
            int(head[1]) if head[1] is not None else None)
        cursor.execute(
            f"""
            SELECT {_CHAT_BILLING_DETAIL_SELECT}
            FROM c2d_requests r
            LEFT JOIN users u ON u.id = r.operator_id
            WHERE {where}
            ORDER BY r.request_start DESC, r.request_id DESC
            LIMIT %s OFFSET %s
            """,
            params + [per_page, (page - 1) * per_page],
        )
        raw_rows = cursor.fetchall()

    return {
        "rows": [_chat_billing_detail_row(raw, sl_seconds) for raw in raw_rows],
        "snapshot_id": resolved_snapshot,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": (total + per_page - 1) // per_page if total else 0,
        },
    }


def get_chat_billing_detail_export_rows(db, day_from: date, day_to: date,
                                        minute_from: int = 0, minute_to: int = 1439,
                                        sl_seconds: int = CHAT_BILLING_SL_DEFAULT_SECONDS,
                                        limit: int = CHAT_BILLING_DETAIL_EXPORT_LIMIT
                                        ) -> List[Dict[str, Any]]:
    """Построчная детализация для выгрузки: свежие чаты сверху, с потолком строк."""
    where, params = _chat_billing_window(day_from, day_to, minute_from, minute_to)
    with db._get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {_CHAT_BILLING_DETAIL_SELECT}
            FROM c2d_requests r
            LEFT JOIN users u ON u.id = r.operator_id
            WHERE {where}
            ORDER BY r.request_start DESC, r.request_id DESC
            LIMIT %s
            """,
            params + [max(1, int(limit))],
        )
        raw_rows = cursor.fetchall()
    return [_chat_billing_detail_row(raw, sl_seconds) for raw in raw_rows]
