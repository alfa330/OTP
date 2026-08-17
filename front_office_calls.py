"""Обзвон фронт-офиса: статистика CRM + проверка дневного плана.

Задача #159 (Турарбек Даурен): каждое утро сообщать в Telegram, кто из
менеджеров фронт-офиса не выполнил дневной план по обзвону.

Источник — та же CRM yataxi, что и у конкурса регистраций, поэтому токен
берём общий (см. reg_contest.get_config):
  - метод: POST {CRM_REGION_CALL_STATS_URL} (= /api/partners/region-call-stats)
  - авторизация: заголовок X-Integration-Token
  - тело: {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"} — обе границы включительно
  - ответ: {"total", "dialog_statuses": [...], "managers": [...]},
    строка менеджера = {manager_id, manager_name, manager_login, total_calls,
    by_status: [{status_id, status_code, status_title, count}]}

Проверено на живом API 17.08.2026:
  * границы включительно, а суммы по дням складываются в сумму за период
    (41 + 65 + 111 = 217 за 10–12.08);
  * будущая дата — законный пустой ответ {"total": 0, "managers": []}, а не
    ошибка; отсюда правило «нет звонков = молчим», а не «все провалили план»;
  * кривые параметры (перевёрнутый период, дата в формате ДД.ММ.ГГГГ) CRM
    отдаёт HTML-страницей с кодом 200 — тот же капкан, что у конкурса
    регистраций, поэтому разбор строгий: незнакомый формат = ошибка;
  * без "to" период открытый (до сегодня), без тела — вообще за всё время;
    оба ключа поэтому передаём всегда;
  * неверный токен — честный 401.

ГЛАВНОЕ ПРО ПЛАН. CRM присылает только тех, кто звонил, — менеджер с нулём
звонков в ответе просто отсутствует. А это ровно тот, кто план и не выполнил.
Поэтому реестр берём у себя (менеджеры отдела «Фронт офисы» в iCORE), а числа
CRM подставляем к нему; несопоставленные звонки CRM не выбрасываем молча, а
показываем отдельной строкой.

Смен фронт-офиса в iCORE нет, так что «был ли человек сегодня на работе»
проверить нечем: выходной выглядит как невыполненный план. Ограничение
осознанное — иначе пришлось бы заводить графики на весь отдел.

ENV (окружение или .env.codex.local):
    CRM_REGION_CALL_STATS_URL  — по умолчанию адрес ниже, менять не нужно
    X-Integration-Token        — общий токен CRM (см. reg_contest)
"""

import html
import logging
import os
import time
from datetime import date, datetime, timedelta

import requests

import reg_contest

log = logging.getLogger(__name__)

DEFAULT_API_URL = "https://backend.yataxi.kz/api/partners/region-call-stats"

HTTP_TIMEOUT = 60
MAX_RETRIES = 3
RETRY_PAUSE = 2.0

# Сколько менеджеров показываем в полной сводке. Отдел — 22 человека, запас
# на вырост; список длиннее просто не читается в Telegram.
MAX_ROWS = 60


def get_config(env_file=".env.codex.local"):
    """Адрес эндпоинта + токен CRM.

    Токен общий с конкурсом регистраций (одна и та же CRM), поэтому его
    разрешение целиком отдано reg_contest: там же лежит фолбэк на «человеческое»
    имя ключа X-Integration-Token. Адрес свой и не секретный — держим значением
    по умолчанию, чтобы не заводить лишнюю переменную на Render.
    """
    token = (reg_contest.get_config(env_file) or {}).get("token")
    url = os.getenv("CRM_REGION_CALL_STATS_URL") or DEFAULT_API_URL
    return {"url": url, "token": token}


def is_configured(config=None):
    cfg = config or get_config()
    return bool(cfg.get("url") and cfg.get("token"))


class RegionCallStatsClient:
    """Минимальный клиент POST {CRM_REGION_CALL_STATS_URL}."""

    def __init__(self, url, token, timeout=HTTP_TIMEOUT):
        if not url or not token:
            raise ValueError("CRM_REGION_CALL_STATS_URL / токен CRM не заданы")
        self.url = url
        self.token = token
        self.timeout = timeout
        self.session = requests.Session()

    @classmethod
    def from_config(cls, config=None):
        cfg = config or get_config()
        return cls(cfg.get("url"), cfg.get("token"))

    def _post(self, payload):
        headers = {
            "X-Integration-Token": self.token,
            "Content-Type": "application/json",
        }
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.post(self.url, json=payload,
                                         headers=headers, timeout=self.timeout)
                if resp.status_code >= 500:
                    raise requests.RequestException(f"CRM HTTP {resp.status_code}")
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"CRM HTTP {resp.status_code}: {resp.text[:300]}")
                # На кривой период CRM отвечает HTML-страницей с кодом 200 —
                # для нас это ошибка разбора, а не пустой ответ.
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                log.warning("front_office_calls: попытка %s/%s не удалась: %s",
                            attempt, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_PAUSE * attempt)
        raise RuntimeError(f"CRM недоступна после {MAX_RETRIES} попыток: {last_error}")

    def fetch_managers(self, date_from, date_to):
        """Звонки менеджеров за период [date_from, date_to] включительно.

        Формат проверяем строго: пустой список менеджеров — законный ответ
        («в этот день не звонили»), а вот ответ без ключа managers означает,
        что CRM сменила контракт, и молча считать это нулём нельзя.
        """
        data = self._post({
            "from": _iso(date_from),
            "to": _iso(date_to),
        })
        if not isinstance(data, dict):
            raise RuntimeError(
                f"CRM отдала не объект, а {type(data).__name__}")
        managers = data.get("managers")
        if not isinstance(managers, list):
            raise RuntimeError(
                "CRM отдала незнакомый формат: нет списка managers, "
                f"ключи ответа = {sorted(data)[:10]}")
        for row in managers:
            if not isinstance(row, dict) or "total_calls" not in row:
                raise RuntimeError(
                    "CRM отдала менеджера без total_calls, "
                    f"поля = {sorted(row)[:10] if isinstance(row, dict) else row}")
        return managers


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _int_or_zero(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Сборка отчёта
# ---------------------------------------------------------------------------

def build_report(roster, crm_managers, date_from, date_to=None, plan_per_day=None):
    """Свести реестр менеджеров iCORE с числами CRM.

    roster — список {id, name, email} из отдела «Фронт офисы»; порядок в
    отчёте задаём мы. crm_managers — сырые строки CRM.

    Считаем по реестру, а не по ответу CRM: тот, кто не сделал ни одного
    звонка, в ответе отсутствует, а в отчёте обязан быть — иначе отбивка
    покажет «все молодцы» ровно в тот день, когда никто не работал.

    Один человек может иметь в CRM несколько учёток (у отдела уже есть пары
    id из разных наборов), поэтому звонки по сопоставленному пользователю
    складываем, а не берём первую строку.
    """
    date_to = date_to or date_from
    days = (date_to - date_from).days + 1
    plan_per_day = plan_per_day if plan_per_day and plan_per_day > 0 else None
    plan_total = plan_per_day * days if plan_per_day else None

    directory = [{"id": u.get("id"), "name": u.get("name"),
                  "email": u.get("email")} for u in (roster or [])]

    calls_by_user = {}
    unmatched = []
    for row in crm_managers or []:
        calls = _int_or_zero(row.get("total_calls"))
        user, _method = reg_contest.match_operator(
            row.get("manager_login"), row.get("manager_name"), directory)
        if user:
            calls_by_user[user["id"]] = calls_by_user.get(user["id"], 0) + calls
        elif calls:
            # Нулевые чужие строки прятать можно — они ничего не меняют,
            # а вот звонки мимо реестра обязаны быть видны.
            unmatched.append({
                "crm_manager_id": row.get("manager_id"),
                "name": row.get("manager_name"),
                "login": row.get("manager_login"),
                "calls": calls,
            })

    rows = []
    for user in roster or []:
        calls = calls_by_user.get(user.get("id"), 0)
        rows.append({
            "user_id": user.get("id"),
            "name": user.get("name"),
            "calls": calls,
            "met": (calls >= plan_total) if plan_total else None,
        })
    # Больше звонков — выше; при равенстве по алфавиту, чтобы список не
    # перетасовывался между днями на одинаковых числах.
    rows.sort(key=lambda r: (-r["calls"], reg_contest.fold_name(r["name"])))
    unmatched.sort(key=lambda r: -r["calls"])

    missing = [r for r in rows if r["met"] is False] if plan_total else []
    return {
        "date_from": date_from,
        "date_to": date_to,
        "days": days,
        "plan_per_day": plan_per_day,
        "plan_total": plan_total,
        "rows": rows,
        "missing": missing,
        "roster_size": len(rows),
        "called_count": sum(1 for r in rows if r["calls"] > 0),
        "total_calls": sum(r["calls"] for r in rows),
        "unmatched": unmatched,
        "unmatched_calls": sum(r["calls"] for r in unmatched),
    }


def has_data(report):
    """Есть ли вообще о чём говорить: хоть один звонок за период."""
    return bool(report.get("total_calls") or report.get("unmatched_calls"))


# ---------------------------------------------------------------------------
# Текст для Telegram
# ---------------------------------------------------------------------------

def _period_label(report):
    date_from = report["date_from"]
    date_to = report["date_to"]
    if date_from == date_to:
        return date_from.strftime("%d.%m.%Y")
    return "%s — %s" % (date_from.strftime("%d.%m.%Y"), date_to.strftime("%d.%m.%Y"))


def _plural(count, one, few, many):
    tail = abs(count) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def _plan_line(report):
    """Строка про план: за день так и пишем, за период раскрываем умножение."""
    if not report["plan_total"]:
        return None
    if report["days"] == 1:
        return "План: %d %s в день." % (
            report["plan_per_day"],
            _plural(report["plan_per_day"], "звонок", "звонка", "звонков"))
    return "План: %d в день, за %d %s — %d." % (
        report["plan_per_day"], report["days"],
        _plural(report["days"], "день", "дня", "дней"), report["plan_total"])


def render_report(report, only_missing=False):
    """Текст отбивки (HTML для Telegram).

    only_missing — утренняя отбивка: в ней перечисляем только тех, кто план
    не вытянул, остальное сворачивается в одну строку. Полная сводка (команда
    /obzvon) показывает всех и отбивает планку чертой.
    """
    lines = ["<b>Обзвон фронт-офиса за %s</b>" % _period_label(report)]

    total = report["total_calls"]
    summary = "Всего %d %s у %d %s." % (
        total, _plural(total, "звонок", "звонка", "звонков"),
        report["roster_size"],
        _plural(report["roster_size"], "менеджера", "менеджеров", "менеджеров"))
    lines.append("")
    lines.append(summary)

    plan_line = _plan_line(report)
    if plan_line:
        lines.append(plan_line)
        missed = len(report["missing"])
        lines.append("Не выполнили: %d из %d." % (missed, report["roster_size"]))

    shown = report["missing"] if only_missing else report["rows"]
    lines.append("")
    if only_missing:
        # Сколько человек план выполнило, уже сказано строкой «Не выполнили:
        # N из M» — повторять это списком или отдельной строкой незачем.
        for row in shown[:MAX_ROWS]:
            lines.append("%s — %d из %d" % (
                html.escape(row["name"] or "—"), row["calls"], report["plan_total"]))
    else:
        previous_met = None
        for row in shown[:MAX_ROWS]:
            # Одна черта вместо значка у каждой строки: где кончился план,
            # видно сразу, а список не рябит. Рисуем её только там, где
            # граница настоящая — то есть выше есть кто-то с выполненным
            # планом. Если план не вытянул никто, черте не место.
            if previous_met is True and row["met"] is False:
                lines.append("— ниже плана —")
            previous_met = row["met"]
            lines.append("%s — %d" % (html.escape(row["name"] or "—"), row["calls"]))
    if len(shown) > MAX_ROWS:
        lines.append("…и ещё %d." % (len(shown) - MAX_ROWS))

    if report["unmatched_calls"]:
        lines.append("")
        lines.append("Ещё %d %s у тех, кого нет в реестре отдела: %s%s." % (
            report["unmatched_calls"],
            _plural(report["unmatched_calls"], "звонок", "звонка", "звонков"),
            html.escape(_unmatched_label(report["unmatched"][:5])),
            " и др." if len(report["unmatched"]) > 5 else ""))

    return "\n".join(lines)


def _unmatched_label(unmatched):
    """«Иванов Иван, id 583, 476» — имена, где они есть, остальные одним хвостом.

    Имён у части менеджеров CRM нет вовсе (живой API отдаёт null), и пять раз
    подряд написать «id» — это шум ради шума.
    """
    named = [r["name"] for r in unmatched if r.get("name")]
    ids = [str(r.get("crm_manager_id")) for r in unmatched if not r.get("name")]
    if ids:
        named.append("id " + ", ".join(ids))
    return ", ".join(named)


def render_no_plan_hint(command="/obzvon_plan"):
    """Что ответить, когда план ещё не задан."""
    return ("Дневной план по обзвону не задан, поэтому утренняя отбивка молчит.\n"
            "Задайте норму командой <code>%s 10</code> — "
            "столько звонков в день на менеджера." % html.escape(command))


def yesterday(today=None):
    """Прошлый день — то, за что отчитывается утренняя отбивка."""
    return (today or date.today()) - timedelta(days=1)


# Форматы, которыми люди пишут дату в чате. «17.08» без года — текущий год:
# спрашивают почти всегда про свежие дни.
_DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%d.%m", "%Y-%m-%d")


def parse_period(argument, today):
    """«17.08.2026», «17.08», «10.08 12.08» или пусто (вчера) -> (с, по).

    None — разобрать не удалось; вызывающий показывает формат. Перевёрнутый
    период разворачиваем сами: CRM на нём отдаёт HTML вместо данных.
    """
    chunks = (argument or "").replace(",", " ").split()
    if not chunks:
        day = yesterday(today)
        return day, day
    if len(chunks) > 2:
        return None

    days = []
    for chunk in chunks:
        parsed = None
        for fmt in _DATE_FORMATS:
            try:
                value = datetime.strptime(chunk, fmt).date()
            except ValueError:
                continue
            parsed = value.replace(year=today.year) if fmt == "%d.%m" else value
            break
        if parsed is None:
            return None
        days.append(parsed)

    date_from, date_to = days[0], days[-1]
    return (date_to, date_from) if date_to < date_from else (date_from, date_to)
