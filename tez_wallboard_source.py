# -*- coding: utf-8 -*-
"""Снимок кабинета Binotel «на сейчас» для табло Тез КЦ (ТП и ОП).

У Тез КЦ телефония Binotel, а не Oktell, и всё нужное для табло кабинет отдаёт
только страницами и полу-JSON-ручками своего веб-интерфейса. Модуль умеет ровно
две вещи: сходить по ним одной сессией и разобрать ответ в понятные числа.

Почему это отдельный файл в корне, а не блок в bot_schedule2.py: разбор HTML —
самое хрупкое место всей затеи (кабинет живой, вёрстку там меняют), и его нужно
держать под тестами на фикстурах. bot_schedule2 из тестов не импортируется —
он на импорте поднимает пул к боевой базе. Отдельный модуль тесты импортируют
обычным import, как tez_status_sync. Отсюда же запрет на зависимости: ни Flask,
ни database.py здесь быть не должно.

Границы ответственности:
  * модуль — только источник: ходит в кабинет и отдаёт разобранные числа;
  * кто из людей ТП, кто ОП, кто на перерыве по нашей базе, кэш и роуты —
    в bot_schedule2.

Что берём и откуда (правила выверены разведкой пересчётом, не менять наугад):
  * `stateOfQueues` — принятые/непринятые/SL/среднее ожидание очереди ТП. Свой
    пересчёт SL по waitsec давал 100 % каждый день — это брак, а не показатель;
  * `analyticsEmployees` — дневные итоги по внутренним номерам: из них считается
    среднее время разговора и берутся готовые исходящие;
  * `dashboard&action=loadCalls` — активные звонки прямо сейчас;
  * `hs_pbx_listOfEndpoints` — регистрация телефонов; ручка дорогая (кабинет
    реально пингует каждую линию, ~3,5 с), поэтому у неё свой TTL.

Безопасность (не «стиль», а требование): ответы `analyticsEmployees` и
`hs_pbx_listOfEndpoints` содержат живые SIP login/password ВСЕХ сотрудников, а
страница очереди и журнал звонков — телефоны клиентов. Поэтому все парсеры
собирают НОВЫЕ словари по белому списку полей: сырой ответ не покидает модуль
ни в результате, ни в исключении, ни в логе.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

import tez_status_sync

logger = logging.getLogger(__name__)

# --- адреса кабинета -------------------------------------------------------
# Страница-индекс мониторинга очередей: с неё берём id очереди, чтобы он не был
# зашит в коде (очередь могут пересобрать, и табло молча показывало бы чужую).
QUEUE_INDEX_PATH = "/main/?module=stateOfQueues&mbav=1"
QUEUE_PATH = "/main/?module=stateOfQueues&queueID={queue_id}"
EMPLOYEES_PATH = "/main/?module=analyticsEmployees&mbav=1&startDate={day}&stopDate={day}"
# &mbav=1 обязателен: без него ручка отдаёт 200 и HTML-скелет SPA вместо JSON.
LIVE_CALLS_PATH = "/main/?module=dashboard&action=loadCalls&mbav=1"
ENDPOINTS_PATH = "/main/?module=hs_pbx_listOfEndpoints&mbav=1"

# Признак протухшей сессии: кабинет отдаёт 200 с формой логина. Именно по нему
# перелогиниваемся — не по таймеру: cookie живут +30 суток от логина, но
# серверный сборщик мусора может быть агрессивнее, и «раз в сутки» не спасёт.
LOGIN_FORM_MARKER = "logining[email]"
# Живость проверяем ТОЛЬКО по содержимому: неизвестный модуль кабинет отдаёт с
# кодом 200 и журналом звонков, так что HTTP-статус здесь ничего не значит.
QUEUE_ALIVE_MARKER = "Клиентов в очереди"
EMPLOYEES_PAYLOAD_KEY = "analyticsEmployees"

HTTP_TIMEOUT_SECONDS = 25
# Дорогая ручка регистраций живёт своим сроком и не входит в основную тройку.
ENDPOINTS_TTL_SECONDS = 120
# id очереди меняется раз в жизни — держим сутки, чтобы не платить лишний GET.
QUEUE_ID_TTL_SECONDS = 24 * 3600
# Порог SL по умолчанию: 20 секунд. Настоящее значение читаем из подписи на
# странице — если в кабинете порог поменяют, табло не должно разъехаться с ним.
DEFAULT_SL_THRESHOLD_SECONDS = 20

# Разметка строки ждущего клиента ни разу не была снята с непустой очередью
# (за всю разведку очередь не набиралась). Пока не увидим её живьём, разобранные
# строки наружу не отдаём: пустой список честнее выдуманной колонки.
QUEUE_CLIENT_ROWS_VERIFIED = False

# Счётчики состояний раскладываем ПО ПОДПИСИ. Классы li.state-ul__item--one…
# --eleven — это порядковые номера: вставят в кабинете новый счётчик, нумерация
# поедет, и табло начнёт молча врать. Подпись переживает вставку.
QUEUE_COUNTER_LABELS = {
    "в работе": "working",
    "в разговоре": "talking",
    "в ожидании": "waiting",
    "статус активен": "status_active",
    "сотрудников с тел. линией": "members",
    "не работают": "not_working",
    "статус работа в crm": "status_work_in_crm",
    "статус перерыв": "status_break",
    "статус не активен": "status_inactive",
    "приостановлено": "paused",
    "телефонов офлайн": "phones_offline",
}

# Статус сотрудника в очереди различается ТОЛЬКО русским текстом: CSS-классов у
# ячейки два (--occupied/--inactive), и они статусы не разделяют — «перерыв в
# работе» и «телефон офлайн» лежат в одном классе.
QUEUE_STATUS_KEYS = {
    "в ожидании": "free",
    "разговаривает": "talking",
    "перерыв в работе": "break",
    "работа в crm со звонком": "work_in_crm",
    "работа в crm": "work_in_crm",
    "неактивен": "inactive",
    "телефон офлайн": "phone_offline",
}
# Ось доступности очереди: «онлайн» = свободные + разговаривающие, как у СЗоВ.
QUEUE_ONLINE_STATUSES = ("free", "talking")
BREAK_REASON_TEXT = "Перерыв"

# Статусы сотрудника вне очереди (поле presenceState в дневных итогах). Это
# ЕДИНСТВЕННОЕ, что кабинет знает про ОП: очереди у отдела продаж нет, страницы
# мониторинга для них не существует. Значения «разговаривает» тут нет вовсе —
# его выводим из активных звонков.
PRESENCE_STATE_KEYS = {
    "active": "active",
    "work in crm": "work_in_crm",
    "break in work": "break",
    "inactive": "inactive",
}

# Белый список полей сотрудника. Всё остальное из ответа кабинета (email,
# endpointData с login/password, extHash, телефоны клиентов в *UniqueCheck)
# остаётся в ответе и наружу не выходит.
EMPLOYEE_STAT_FIELDS = {
    "incomingSuccess": "incoming_success",
    "incomingFailed": "incoming_failed",
    "incomingBillsec": "incoming_billsec",
    "incomingWaitsec": "incoming_waitsec",
    "outgoingAmount": "outgoing_amount",
    "outgoingSuccess": "outgoing_success",
    "outgoingFailed": "outgoing_failed",
    "outgoingBillsec": "outgoing_billsec",
}

CALL_TYPE_NAMES = {0: "incoming", 1: "outgoing"}


class CabinetError(RuntimeError):
    """Кабинет Binotel недоступен или ответил не тем."""


class CabinetAuthError(CabinetError):
    """Вход в кабинет не выполнен: учётка не подошла или сессию не отдали."""


class CabinetParseError(CabinetError):
    """Ответ кабинета не разобрался — снимок отбраковываем целиком.

    Перекошенные цифры на стене хуже надписи «данные замерли»: по ним примут
    решение, а проверить их зрителю нечем."""


# --- мелкие разборщики -----------------------------------------------------

def parse_duration(text):
    """«02:09» → 129, «07:55:45» → 28545, «42» → 42. Не разобралось → None.

    Кабинет пишет длительности тремя способами в одной и той же таблице, а
    ноль здесь означал бы «мгновенно» — поэтому непонятное честнее вернуть
    пустым, чем нулём."""
    raw = _clean_text(text)
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) > 3 or not all(part.strip().isdigit() for part in parts if part.strip() != ""):
        return None
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    if not numbers:
        return None
    seconds = 0
    for value in numbers:
        seconds = seconds * 60 + value
    return seconds


def parse_percent(text):
    """«98.30%» → 0.983, «100,00%» → 1.0. Не разобралось → None."""
    raw = _clean_text(text).replace("%", "").replace(",", ".")
    if not raw:
        return None
    try:
        # Округляем: 98.30 / 100 в double даёт 0.9840000000000001, и такой хвост
        # уезжает в JSON снимка, в лог и в сравнение с порогом.
        return round(float(raw) / 100.0, 6)
    except ValueError:
        return None


def parse_int(text):
    """Целое из ячейки таблицы; пусто или мусор → None (не ноль)."""
    raw = _clean_text(text).replace(" ", "")
    if not raw:
        return None
    match = re.search(r"-?\d+", raw)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def resolve_queue_id(html):
    """id очереди со страницы-индекса мониторинга. Не нашли → None.

    Хардкода id в модуле нет намеренно: очередь в кабинете могут пересоздать, и
    табло тогда должно замолчать (и починиться сменой переменной окружения), а
    не показывать чужую очередь как свою."""
    match = re.search(r"queueID=(\d+)", html or "")
    return match.group(1) if match else None


def queue_id_from_env():
    """Аварийное переопределение id очереди — на случай, если индекс уедет."""
    value = (os.getenv("TEZ_WALLBOARD_QUEUE_ID") or "").strip()
    return value or None


def parse_queue_page(html):
    """Страница мониторинга очереди → числа очереди и строки её сотрудников.

    Опираемся на BEM-классы (`calls__heading-item--received` и т.п.), а не на
    порядок колонок: колонки в кабинете переставляли, классы — нет."""
    text = html or ""
    if QUEUE_ALIVE_MARKER not in text:
        # Кабинет вернул что угодно, кроме страницы очереди: форму логина,
        # скелет SPA, чужой модуль. Всё это приходит с кодом 200.
        raise CabinetParseError("Страница очереди Binotel не распознана")
    soup = BeautifulSoup(text, "html.parser")

    heading = soup.select_one(".clients__heading-item--name")
    queue_size = parse_int(_node_text(heading).split(":")[-1]) if heading is not None else None

    served = _calls_number(soup, "received")
    lost = _calls_number(soup, "missed")
    avg_talk_text = _calls_single(soup, "average-time")
    avg_wait_text = _calls_single(soup, "average-connection-time")
    sl_text = _calls_single(soup, "sla-speed")

    counters, unknown_counters = _parse_state_counters(soup)
    operators, unknown_statuses = _parse_queue_operators(soup)

    parsed = {
        "queue_id": resolve_queue_id(text),
        "queue_name": _node_text(soup.select_one(".header--first__name-accent")) or None,
        "queue": queue_size,
        "served": served,
        "lost": lost,
        "sl_ratio": parse_percent(sl_text),
        "sl_percent_text": sl_text or None,
        "sl_threshold_seconds": _parse_sl_threshold(soup),
        "avg_talk_text": avg_talk_text or None,
        "avg_talk_seconds": parse_duration(avg_talk_text),
        "avg_wait_text": avg_wait_text or None,
        "avg_wait_seconds": parse_duration(avg_wait_text),
        "counters": counters,
        "unknown_counters": unknown_counters,
        "unknown_statuses": unknown_statuses,
        "operators": operators,
    }
    return parsed


def parse_queue_clients(html):
    """Строки ждущих клиентов: [{waiting_text, waiting_seconds}].

    Номер клиента со страницы НЕ забираем: на табло висит время ожидания, а
    телефон звонящего — персональные данные, которым на стене делать нечего."""
    soup = BeautifulSoup(html or "", "html.parser")
    table = soup.select_one("table.queue-content__clients")
    if table is None:
        return []
    rows = []
    for row in table.find_all("tr"):
        if row.select_one("th") is not None:
            continue
        cells = row.find_all("td")
        if not cells:
            continue
        # Ждущего описывает последняя ячейка — «Время ожидания на линии».
        waiting_text = _node_text(cells[-1])
        rows.append({
            "waiting_text": waiting_text or None,
            "waiting_seconds": parse_duration(waiting_text),
        })
    return rows


def validate_queue_counters(parsed):
    """Страж разбора: два тождества самой страницы должны сходиться.

    Кабинет считает своих людей дважды — по статусу и по занятости, и обе суммы
    обязаны давать «сотрудников с тел. линией». Если не дают, значит разметку
    поменяли и мы читаем не те числа: снимок отбраковываем целиком, наверх
    уходит прошлый с пометкой «данные замерли». Это единственная защита от
    тихого перекоса — цифру на стене никто не перепроверит."""
    counters = (parsed or {}).get("counters") or {}
    identities = (
        ("статус активен + работа в CRM + перерыв + не активен",
         ("status_active", "status_work_in_crm", "status_break", "status_inactive")),
        ("в работе + не работают", ("working", "not_working")),
    )
    members = counters.get("members")
    if members is None:
        raise CabinetParseError(
            "Страница очереди Binotel: не нашёл счётчик «сотрудников с тел. линией»")
    for title, keys in identities:
        missing = [key for key in keys if counters.get(key) is None]
        if missing:
            raise CabinetParseError(
                "Страница очереди Binotel: не нашёл счётчики %s" % ", ".join(missing))
        total = sum(counters[key] for key in keys)
        if total != members:
            raise CabinetParseError(
                "Страница очереди Binotel: %s = %d, а сотрудников с тел. линией %d"
                % (title, total, members))

    # Счётчики и таблица сотрудников разбираются РАЗНЫМ кодом: блок счётчиков — по
    # подписям, строки людей — по классам таблицы. Поэтому сверки счётчиков между
    # собой мало: поменяй кабинет разметку строки, и таблица разберётся в пустоту,
    # а счётчики сойдутся как ни в чём не бывало — на стене будет «Онлайн 0» при
    # одиннадцати работающих людях. Ниже два тождества, связывающие эти две
    # половины; на всех шести снятых кадрах они держатся точно.
    operators = (parsed or {}).get("operators") or []
    if len(operators) != members:
        raise CabinetParseError(
            "Страница очереди Binotel: разобрано строк сотрудников %d, а с тел. линией %d"
            % (len(operators), members))
    working = counters.get("working")
    if working is not None:
        on_line = sum(1 for row in operators if row.get("status_key") in QUEUE_ONLINE_STATUSES)
        if on_line != working:
            raise CabinetParseError(
                "Страница очереди Binotel: по строкам в работе %d, а по счётчику %d"
                % (on_line, working))
    return parsed


def summarize_queue_operators(parsed):
    """Строки сотрудников очереди → ось доступности и список перерывов.

    Словарь статусов кабинета живёт в этом модуле, поэтому и раскладка по оси
    здесь: чтобы сервер не заводил вторую копию соответствия «русский текст →
    смысл» и они не разъехались."""
    operators = (parsed or {}).get("operators") or []
    by_key = {}
    breaks = []
    for row in operators:
        key = row.get("status_key") or "unknown"
        by_key[key] = by_key.get(key, 0) + 1
        if key == "break":
            breaks.append({
                "name": row.get("name"),
                "number": row.get("number"),
                "reason": BREAK_REASON_TEXT,
                "reason_key": "break",
                # Кабинет отдаёт только «в статусе уже», момента начала нет.
                "since": None,
                "seconds": row.get("in_state_seconds"),
            })
    total = len(operators)
    free = by_key.get("free", 0)
    talking = by_key.get("talking", 0)
    on_break = by_key.get("break", 0)
    online = free + talking
    return {
        "operators_total": total,
        "operators_online": online,
        "operators_free": free,
        "operators_talking": talking,
        "operators_on_break": on_break,
        "operators_other": max(0, total - online - on_break),
        "break_list": breaks,
    }


def summarize_presence_operators(employees, numbers, live_calls=None, endpoints=None,
                                 now_ts=None):
    """Ось «сейчас» для отдела БЕЗ очереди (ОП): статус + активные звонки + линия.

    Почему не так, как у ТП: страницы мониторинга у отдела продаж нет — очередь
    они не обслуживают. Остаются три разнородных сигнала, и каждый закрывает то,
    чего не знают два других:
      - активный звонок (кабинет, «прямо сейчас») — только он даёт «в разговоре»:
        в presenceState такого значения не существует в принципе;
      - регистрация телефона — она и есть «на линии». По статусу считать нельзя:
        08.09.2026 все семеро продавцов стояли `inactive`, при этом один номер
        сделал за день 50 звонков. Статусы у ОП просто не проставляют, и плитка
        «Онлайн» по ним была бы вечным нулём;
      - presenceState — только для перерыва, там его проставляют осмысленно.

    Разряды не пересекаются и считаются в одном порядке: разговор > перерыв >
    свободен. Иначе человек с зарегистрированным телефоном попал бы и в
    «свободен», и в «на перерыве», а сумма разрядов перестала бы сходиться с
    общим числом людей — ровно та арифметика, которую руководитель проверяет
    глазами за секунду."""
    people = (employees or {}).get("employees") if isinstance(employees, dict) else None
    if people is None:
        people = employees or {}
    wanted = [str(number) for number in (numbers or [])]
    talking_numbers = {str(call.get("employee_number") or "")
                       for call in (live_calls or [])}
    registered = endpoints if isinstance(endpoints, dict) else None

    total = free = talking = on_break = 0
    breaks = []
    for number in wanted:
        record = people.get(number) or {}
        total += 1
        state = PRESENCE_STATE_KEYS.get(
            str(record.get("presence_state") or "").strip().lower())
        if number in talking_numbers:
            talking += 1
            continue
        if state == "break":
            on_break += 1
            since = record.get("presence_state_updated_at") or None
            breaks.append({
                "name": record.get("name"),
                "number": number,
                "reason": BREAK_REASON_TEXT,
                "reason_key": "break",
                "since": since,
                # Момент начала кабинет отдаёт, а «сколько уже» — нет: считаем от
                # времени самого кабинета, чтобы цифра не поехала на наших часах.
                "seconds": (int(now_ts) - int(since)) if (since and now_ts and now_ts > since) else None,
            })
            continue
        # «Свободен» — телефон зарегистрирован.
        if registered is not None and registered.get(number):
            free += 1

    # Список линий не приехал (своя дорогая ручка со своим TTL, её отказ мы глотаем) —
    # значит «свободен» посчитать НЕ ИЗ ЧЕГО. Ноль здесь читался бы со стены как
    # «на линии никого», хотя на самом деле мы просто не знаем: это ровно тот случай,
    # ради которого заведено правило нуля. «Онлайн» тоже неизвестен — он выводится из
    # свободных; «в разговоре» и «на перерыве» известны всегда, их и показываем.
    known = registered is not None
    online = (free + talking) if known else None
    return {
        "operators_total": total,
        "operators_online": online,
        "operators_free": free if known else None,
        "operators_talking": talking,
        "operators_on_break": on_break,
        "operators_other": max(0, total - online - on_break) if known else None,
        "break_list": breaks,
        "free_unknown": not known,
    }


def parse_employees(payload):
    """Дневные итоги по внутренним номерам — строго по белому списку полей.

    В сыром ответе лежат живые SIP login/password всех сотрудников, их почты и
    телефоны клиентов (`*UniqueCheck`). Поэтому здесь не «удаляем лишнее», а
    собираем новый словарь из перечисленных полей: при появлении в кабинете
    нового поля оно по умолчанию НЕ попадёт наружу."""
    data = _as_json(payload, "analyticsEmployees")
    if not isinstance(data, dict):
        raise CabinetParseError("Ответ analyticsEmployees Binotel не распознан")
    page = data.get("pageData")
    if not isinstance(page, dict) or EMPLOYEES_PAYLOAD_KEY not in page:
        raise CabinetParseError("Ответ analyticsEmployees Binotel без pageData.analyticsEmployees")

    employees = {}
    for number, record in (page.get(EMPLOYEES_PAYLOAD_KEY) or {}).items():
        if not isinstance(record, dict):
            continue
        stats_raw = record.get("statistics") or {}
        stats = {}
        for source, target in EMPLOYEE_STAT_FIELDS.items():
            value = stats_raw.get(source) if isinstance(stats_raw, dict) else None
            stats[target] = _to_int(value, 0)
        employees[str(number)] = {
            "number": str(record.get("extNumber") or number),
            "ext_id": str(record.get("extID") or "") or None,
            "name": str(record.get("name") or "").strip() or None,
            "department": str(record.get("department") or "").strip() or None,
            "presence_state": str(record.get("presenceState") or "").strip() or None,
            "presence_state_updated_at": _to_int(record.get("presenceStateUpdatedAt"), None),
            # callCenterIsEnabled=0 — сотрудник отдела, который очередь НЕ
            # обслуживает: в отделе номеров больше, чем в очереди.
            "call_center_enabled": _to_flag(record.get("callCenterIsEnabled")),
            "stats": stats,
        }

    departments = {}
    for dep_id, record in (page.get("listOfDepartments") or {}).items():
        if not isinstance(record, dict):
            continue
        members = record.get("employees")
        departments[str(dep_id)] = {
            "id": str(record.get("id") or dep_id),
            "name": str(record.get("name") or "").strip() or None,
            "employee_ext_ids": sorted(str(x) for x in members) if isinstance(members, dict) else [],
        }
    return {"departments": departments, "employees": employees}


def parse_live_calls(payload):
    """Активные звонки прямо сейчас — по белому списку полей.

    Кабинет отдаёт JSON с Content-Type text/html, а когда звонков нет — два
    байта «[]», поэтому разбираем текст сами и пустое тело считаем пустым
    списком, а не ошибкой. Номера клиентов и имена в результат не берём: на
    табло идёт счёт разговоров, а не журнал звонков."""
    rows = _as_json(payload, "loadCalls")
    if rows is None or rows == "":
        return []
    if not isinstance(rows, list):
        raise CabinetParseError("Ответ loadCalls Binotel не список")
    calls = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        call_type = _to_int(row.get("callType"), None)
        billsec = _to_int(row.get("billsec"), None)
        calls.append({
            "general_call_id": str(row.get("generalCallID") or "") or None,
            "employee_number": str(row.get("employeeNumber") or "").strip() or None,
            "call_type": CALL_TYPE_NAMES.get(call_type, "unknown"),
            "started_at": _to_int(row.get("startTime"), None),
            # duration у отвеченного звонка — секунды РАЗГОВОРА, а billsec
            # отрицательный и равен минус длительности дозвона (сверено на всех
            # снятых кадрах: elapsed - |billsec| == duration). Звонок, который
            # ещё звонит и не отвечен, в выдаче ни разу не попался, поэтому
            # флага «разговаривает» здесь нет: выдумывать признак, который не
            # на чем проверить, — тот же перекос, только незаметный.
            "duration": _to_int(row.get("duration"), None),
            "billsec": billsec,
            "ring_seconds": (-billsec) if (billsec is not None and billsec < 0) else None,
            "now_ts": _to_int(row.get("timeIsNowOnBackend"), None),
        })
    return calls


def count_active_calls(live_calls, numbers):
    """Сколько активных звонков сейчас у набора внутренних номеров.

    Для ТП число разговоров берётся со страницы очереди (кабинет считает его
    сам), а у ОП очереди нет вовсе — там это единственный источник."""
    wanted = {str(number) for number in (numbers or [])}
    return sum(1 for call in (live_calls or [])
               if str(call.get("employee_number") or "") in wanted)


def parse_endpoints(html):
    """Регистрация телефонов: {внутренний номер: онлайн ли}.

    SIP-логин кабинет прячет в подсказку у номера (`title="Логин: …"`) — его не
    забираем: это боевая учётка линии."""
    soup = BeautifulSoup(html or "", "html.parser")
    block = soup.select_one(".list-of-endpoints")
    if block is None:
        raise CabinetParseError("Страница списка линий Binotel не распознана")
    states = {}
    for row in block.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        number = _clean_text(_node_text(cells[0]))
        if not number.isdigit():
            continue
        status = _node_text(cells[2]).lower()
        states[number] = status.startswith("онлайн")
    if not states:
        raise CabinetParseError("Страница списка линий Binotel: ни одной линии")
    return states


def day_totals(employees, numbers, queue=None):
    """Показатели за день по набору внутренних номеров — блок `today` табло.

    Если передана разобранная страница очереди, принятые/непринятые/SL/среднее
    ожидание берём с неё: кабинет считает их по своим правилам, и руководитель
    должен видеть на табло ровно то же число, что у себя в кабинете. Среднее
    время разговора считаем сами (Σ billsec / Σ принятых) — на странице оно
    строкой «02:09», а плитке нужны секунды.

    Правило нуля: посчитать не из чего — None, никогда 0. Ноль на табло читается
    как «всё хорошо, звонков нет»."""
    people = (employees or {}).get("employees") if isinstance(employees, dict) else None
    if people is None:
        people = employees or {}
    wanted = {str(number) for number in (numbers or [])}
    matched = [record for number, record in people.items() if str(number) in wanted]
    if not matched:
        # Ни один номер состава не нашёлся в ответе кабинета — это не «сегодня не
        # звонили», а сорванная привязка людей к линиям. Оба её повода в этом
        # проекте уже случались: sip_number не уникален, а переименование
        # направления обнуляет direction_id. Ноль здесь прочитали бы как «отдел
        # продаж не сделал ни одного звонка» — обвинение вместо диагноза.
        return {field: None for field in (
            "arrived", "served", "lost", "ar_ratio", "sl_ratio",
            "avg_wait_seconds", "avg_talk_seconds", "outgoing_total",
            "outgoing_success", "outgoing_success_ratio",
            "avg_outgoing_talk_seconds")}

    totals = {field: 0 for field in EMPLOYEE_STAT_FIELDS.values()}
    for record in matched:
        stats = (record or {}).get("stats") or {}
        for field in totals:
            totals[field] += _to_int(stats.get(field), 0)

    # Усекаем, а не округляем: кабинет показывает 02:09 при 129,74 с, и округление
    # дало бы на стене 2:10 — расхождение с кабинетом на каждой смене. Та же
    # причина, что у среднего времени разговора табло СЗоВ.
    answered = totals["incoming_success"]
    avg_talk = int(totals["incoming_billsec"] // answered) if answered else None
    out_answered = totals["outgoing_success"]
    avg_out_talk = int(totals["outgoing_billsec"] // out_answered) if out_answered else None

    if queue:
        served = queue.get("served")
        lost = queue.get("lost")
        sl_ratio = queue.get("sl_ratio")
        avg_wait = queue.get("avg_wait_seconds")
    else:
        # У ОП очереди нет вовсе: считать нечему, и «уровень обслуживания = 0»
        # был бы выдумкой. Пусто — значит пусто.
        served = answered
        lost = totals["incoming_failed"]
        sl_ratio = None
        avg_wait = int(totals["incoming_waitsec"] // answered) if answered else None

    arrived = None
    if served is not None and lost is not None:
        arrived = served + lost
    ar_ratio = (lost / float(arrived)) if (arrived and lost is not None) else None

    return {
        "arrived": arrived,
        "served": served,
        "lost": lost,
        "ar_ratio": round(ar_ratio, 4) if ar_ratio is not None else None,
        "sl_ratio": sl_ratio,
        "avg_wait_seconds": avg_wait,
        "avg_talk_seconds": avg_talk,
        "outgoing_total": totals["outgoing_amount"],
        "outgoing_success": totals["outgoing_success"],
        # Дозвон: сколько трубок подняли из набранного. Для отдела продаж это то же
        # по смыслу, что SL для линии — качество, а не объём.
        "outgoing_success_ratio": (round(totals["outgoing_success"] / float(totals["outgoing_amount"]), 4)
                                   if totals["outgoing_amount"] else None),
        # Отдельное среднее по ИСХОДЯЩИМ: у ОП входящих нет вовсе (за 08.09.2026 у
        # всех семи номеров incomingSuccess = 0), и «средняя длительность
        # разговора» из постановки для них может значить только исходящие.
        "avg_outgoing_talk_seconds": avg_out_talk,
    }


# --- сессия кабинета -------------------------------------------------------

class CabinetSession:
    """Одна сессия кабинета на процесс: логин по требованию, перелогин по признаку.

    Поверх tez_status_sync.BinotelClient — второй логин заводить нельзя: учётка
    у кабинета одна, и параллельные входы гасят друг другу PHPSESSID."""

    def __init__(self, config=None, *, client=None, timeout=HTTP_TIMEOUT_SECONDS,
                 tz_name=None):
        self._config = config
        self._client = client
        self._timeout = timeout
        self._tz_name = tz_name
        # Лок сериализует вход; он же берётся вложенно из get() — отсюда RLock.
        self._lock = threading.RLock()
        self._generation = 0
        self._queue_id = None
        self._queue_id_at = 0.0
        self._endpoints = None
        self._endpoints_at = 0.0

    @property
    def config(self):
        if self._config is None:
            self._config = tez_status_sync.get_config()
        return self._config

    @property
    def tz_name(self):
        return self._tz_name or self.config.get("tz") or tez_status_sync.DEFAULT_TZ

    def ensure(self):
        """Клиент с живой сессией; логинится при первом обращении."""
        with self._lock:
            if self._client is None:
                cfg = self.config
                if not cfg.get("login") or not cfg.get("password"):
                    raise CabinetAuthError("BINOTEL_LOGIN/BINOTEL_PASSWORD не заданы")
                try:
                    self._client = tez_status_sync.BinotelClient(
                        cfg.get("base_url"), cfg.get("login"), cfg.get("password"))
                except ValueError as exc:
                    raise CabinetAuthError(str(exc))
                self._login()
            return self._client

    def _login(self, generation=None):
        with self._lock:
            if generation is not None and generation != self._generation:
                # Пока ждали лок, сосед уже перелогинился — второй вход только
                # погасил бы свежую сессию.
                return
            try:
                self._client.authenticate()
            except requests.RequestException as exc:
                # Оборвалась связь, а не отказали в доступе. Разница не косметическая:
                # «вход не выполнен» отправит дежурного менять пароль, хотя чинить
                # надо сеть.
                raise CabinetError("Binotel: кабинет не ответил на входе (%s)" % _safe_reason(exc))
            except Exception as exc:
                # Текст кабинета доносим как есть: «пароль не тот» и «нет такого
                # сотрудника» руководитель чинит по-разному.
                raise CabinetAuthError("Binotel: вход в кабинет не выполнен (%s)" % _safe_reason(exc))
            self._generation += 1

    def get(self, path, *, timeout=None):
        """GET по кабинету с одним перелогином, если сессия протухла."""
        client = self.ensure()
        url = path if str(path).startswith("http") else client.base_url + path
        generation = self._generation
        response = self._request(client, url, timeout)
        body = response.text or ""
        if LOGIN_FORM_MARKER in body:
            # Признак дешёвый и однозначный: кабинет подсунул форму логина
            # вместо данных. Логинимся заново и повторяем — ровно один раз,
            # иначе при смене пароля мы будем долбить кабинет по кругу.
            self._login(generation)
            response = self._request(client, url, timeout)
            if LOGIN_FORM_MARKER in (response.text or ""):
                raise CabinetAuthError(
                    "Binotel: сессия кабинета не восстанавливается — проверьте учётку")
        return response

    def get_text(self, path, *, timeout=None):
        return self.get(path, timeout=timeout).text or ""

    def _request(self, client, url, timeout):
        try:
            return client.session.get(
                url,
                timeout=timeout or self._timeout,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        except Exception as exc:
            raise CabinetError("Binotel: кабинет не ответил (%s)" % _safe_reason(exc))

    def queue_id(self, *, refresh=False, ttl_seconds=QUEUE_ID_TTL_SECONDS):
        """id очереди ТП: со страницы-индекса, с суточным кэшем на процесс."""
        forced = queue_id_from_env()
        if forced:
            return forced
        now = time.time()
        if not refresh and self._queue_id and (now - self._queue_id_at) < ttl_seconds:
            return self._queue_id
        html = self.get_text(QUEUE_INDEX_PATH)
        queue_id = resolve_queue_id(html)
        if not queue_id:
            raise CabinetParseError(
                "Binotel: на странице мониторинга очередей нет ни одной очереди")
        self._queue_id = queue_id
        self._queue_id_at = now
        return queue_id

    def endpoints(self, *, ttl_seconds=ENDPOINTS_TTL_SECONDS, force=False):
        """(регистрация телефонов, возраст в секундах); свой TTL у дорогой ручки.

        Кабинет на этот запрос реально пингует каждую линию и отвечает ~3,5 с —
        втрое дольше всей остальной тройки, поэтому в общий шаг опроса её не
        берём."""
        now = time.time()
        age = now - self._endpoints_at if self._endpoints is not None else None
        if not force and self._endpoints is not None and age < ttl_seconds:
            return self._endpoints, int(age)
        data = parse_endpoints(self.get_text(ENDPOINTS_PATH))
        self._endpoints = data
        self._endpoints_at = now
        return data, 0


def is_configured(config=None):
    """Есть ли чем логиниться в кабинет — гейт готовности для роута (503)."""
    cfg = config or tez_status_sync.get_config()
    return bool(cfg.get("login") and cfg.get("password"))


def cabinet_today(tz_name=None):
    """Сегодняшняя дата глазами кабинета (Asia/Almaty).

    Сутки у Тез закрываются по Алматы посреди ночной смены ТП — но именно так
    считает кабинет, и табло обязано показывать то же, что он."""
    tz = tez_status_sync._tzinfo(tz_name or tez_status_sync.DEFAULT_TZ)
    return datetime.now(tz).date()


def format_cabinet_date(day):
    """Дата в формате модулей аналитики кабинета — DD.MM.YYYY."""
    return day.strftime("%d.%m.%Y")


def fetch_snapshot(session, day=None, *, with_endpoints=False,
                   endpoints_ttl_seconds=ENDPOINTS_TTL_SECONDS, queue_id=None,
                   timeout=None):
    """Один обход кабинета: очередь + дневные итоги + активные звонки.

    Последовательно, а не параллельно, и это не экономия: сессия у кабинета
    одна (PHPSESSID в cookie), а весь обход укладывается в ~2,3 с — заведомо
    меньше шага опроса табло. Регистрация телефонов идёт четвёртой и только
    когда истёк её собственный TTL.

    Отдаёт разобранный материал; кто из этих людей ТП, а кто ОП — решает вызвавший."""
    tz_name = getattr(session, "tz_name", None) or tez_status_sync.DEFAULT_TZ
    day = day or cabinet_today(tz_name)

    # Очередь есть только у ТП, поэтому её отказ обязан гасить только ТП. Раньше
    # исключение отсюда роняло весь снимок — и поехавшая вёрстка ОДНОЙ страницы
    # снимала со стены оба табло, включая то, которому очередь вообще не нужна.
    queue, clients, queue_error = None, [], None
    try:
        queue_id = queue_id or session.queue_id()
        queue_html = session.get_text(QUEUE_PATH.format(queue_id=queue_id), timeout=timeout)
        queue = validate_queue_counters(parse_queue_page(queue_html))
        clients = parse_queue_clients(queue_html) if QUEUE_CLIENT_ROWS_VERIFIED else []
    except CabinetAuthError:
        # Вход не выполнен — это не беда одной страницы: следующие два запроса
        # тоже вернут форму логина. Пусть падает весь снимок.
        raise
    except CabinetError as exc:
        queue_error = str(exc)
        logger.warning("Табло Тез: очередь ТП не разобрана: %s", exc)

    employees = parse_employees(session.get_text(
        EMPLOYEES_PATH.format(day=format_cabinet_date(day)), timeout=timeout))
    live_calls = parse_live_calls(session.get_text(LIVE_CALLS_PATH, timeout=timeout))

    endpoints, endpoints_age = None, None
    if with_endpoints:
        try:
            endpoints, endpoints_age = session.endpoints(ttl_seconds=endpoints_ttl_seconds)
        except CabinetError as exc:
            # Регистрация телефонов — подсказка, а не показатель: из-за неё
            # весь снимок ронять незачем.
            logger.warning("Табло Тез: список линий Binotel не разобран: %s", exc)

    now_ts = max([call["now_ts"] for call in live_calls if call.get("now_ts")] or [0])
    tz = tez_status_sync._tzinfo(tz_name)
    if now_ts:
        now_text = datetime.fromtimestamp(now_ts, tz).strftime("%Y-%m-%d %H:%M:%S")
        now_source = "cabinet"
    else:
        # Своё время кабинет отдаёт только вместе с активным звонком. Когда
        # звонков нет, берём собственные часы в поясе кабинета и честно
        # помечаем это в диагностике.
        now_text = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        now_source = "local"

    return {
        "day": day.strftime("%Y-%m-%d"),
        "binotel_now": now_text,
        "binotel_now_source": now_source,
        "binotel_now_ts": now_ts or int(datetime.now(tz).timestamp()),
        "sl_threshold_seconds": ((queue or {}).get("sl_threshold_seconds")
                                 or DEFAULT_SL_THRESHOLD_SECONDS),
        "queue_id": queue_id,
        "queue": queue,
        "queue_error": queue_error,
        "queue_clients": clients,
        "employees": employees,
        "live_calls": live_calls,
        "endpoints": endpoints,
        "endpoints_age_seconds": endpoints_age,
        "diagnostics": {
            "queue_rows_unverified": not QUEUE_CLIENT_ROWS_VERIFIED,
            "unknown_queue_counters": (queue or {}).get("unknown_counters") or [],
            "unknown_queue_statuses": (queue or {}).get("unknown_statuses") or [],
            "binotel_now_source": now_source,
            "queue_error": queue_error,
        },
    }


# --- внутренняя кухня ------------------------------------------------------

def _safe_reason(exc):
    """Текст ошибки без адресов кабинета.

    Ошибка снимка доезжает до браузера в поле `error` и до логов, а исключения requests
    несут в тексте и хост, и путь модуля кабинета (`host='...'`, `url: /main/?module=...`).
    Адрес внутренней панели там не нужен никому, кроме того, кто её ищет. Вычищаем все три
    формы, в которых requests его пишет."""
    text = str(exc)
    text = re.sub(r"https?://\S+", "кабинет", text)
    text = re.sub(r"host=['\"][^'\"]+['\"]", "host=кабинет", text)
    text = re.sub(r"url:\s*\S+", "url: кабинет", text)
    return text


def _to_flag(value):
    """Флаг кабинета в bool. Кабинет пишет их строками '0'/'1', но наивное
    сравнение с '1' превратило бы JSON-овский true в False — и состав очереди
    молча схлопнулся бы в ноль человек."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


def _clean_text(value):
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _node_text(node, separator=" "):
    if node is None:
        return ""
    return _clean_text(node.get_text(separator))


def _calls_number(soup, modifier):
    """Число из колонки итогов очереди по BEM-модификатору её заголовка."""
    cell = _calls_cell(soup, modifier)
    return parse_int(_node_text(cell.select_one(".calls__info"))) if cell is not None else None


def _calls_single(soup, modifier):
    cell = _calls_cell(soup, modifier)
    return _node_text(cell.select_one(".calls__info-single")) if cell is not None else ""


def _calls_cell(soup, modifier):
    """Ячейка строки итогов, стоящая под заголовком с нужным модификатором.

    Считаем позицию заголовка и берём ту же позицию в строке значений: сами
    ячейки значений различающих классов не имеют."""
    heading = soup.select_one(".calls__heading-item--%s" % modifier)
    if heading is None:
        return None
    row = heading.find_parent("tr")
    if row is None:
        return None
    # Ищем именно ЭТОТ заголовок по тождеству: list.index сравнивал бы теги по
    # содержимому и на двух одинаковых подписях молча взял бы первую.
    headings = row.find_all("th")
    index = next((i for i, item in enumerate(headings) if item is heading), None)
    if index is None:
        return None
    values = row.find_next_sibling("tr")
    if values is None:
        return None
    cells = values.find_all("td")
    return cells[index] if index < len(cells) else None


def _parse_sl_threshold(soup):
    """Порог SL из подписи колонки («…SLA: 20 секунд»)."""
    caption = _node_text(soup.select_one(".calls__heading-item--sla-speed"))
    match = re.search(r"SLA[:\s]*(\d+)\s*секунд", caption, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return DEFAULT_SL_THRESHOLD_SECONDS


def _parse_state_counters(soup):
    counters = {}
    unknown = []
    for item in soup.select("li.state-ul__item"):
        label = _counter_label(item.select_one(".state-ul__info"))
        value = parse_int(_node_text(item.select_one(".state-ul__info-num")))
        if not label:
            continue
        key = QUEUE_COUNTER_LABELS.get(label)
        if key is None:
            unknown.append(label)
            continue
        counters[key] = value
    return counters, unknown


def _counter_label(node):
    label = _node_text(node).lower()
    # Длинные подписи кабинет переносит посреди слова: «приоста-<br>новлено».
    # Дефис перед переносом — не часть слова, склеиваем обратно.
    return re.sub(r"-\s+", "", label)


def _parse_queue_operators(soup):
    rows = []
    unknown = []
    for line in soup.select("tr.employees__line"):
        cells = line.find_all("td")
        if len(cells) < 3:
            continue
        status_text = _node_text(line.select_one(".employees__status-text--text"))
        key = QUEUE_STATUS_KEYS.get(status_text.lower())
        if key is None and status_text:
            unknown.append(status_text)
        in_state_text = _node_text(cells[5]) if len(cells) > 5 else ""
        rows.append({
            "name": _node_text(cells[0]) or None,
            "number": _node_text(cells[1]) or None,
            "status_text": status_text or None,
            "status_key": key,
            "received_calls": parse_int(_node_text(cells[3])) if len(cells) > 3 else None,
            "last_call_text": (_node_text(cells[4]) or None) if len(cells) > 4 else None,
            "in_state_text": in_state_text or None,
            "in_state_seconds": parse_duration(in_state_text),
        })
    return rows, unknown


def _as_json(payload, what):
    """JSON из чего угодно: кабинет отдаёт его с Content-Type text/html."""
    if payload is None:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "replace")
    text = str(payload).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        raise CabinetParseError("Ответ %s Binotel — не JSON" % what)


def _to_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default
