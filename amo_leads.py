# -*- coding: utf-8 -*-
"""Лиды amoCRM по источникам: выгрузка сделок и отбивка в Telegram.

Повторяет один в один связку, которая до этого жила в Google-таблице
«Подсчет лидов / Тарифы сервисов»: Apps Script заливал сырые сделки на вкладку
«Импорт ДЕНЬ», а формулы вкладки «Сводка по Дням» считали по ним лиды в разрезе
источников. Здесь та же выгрузка и те же правила подсчёта, но данные лежат в
нашей БД, а не на листе.

Правила подсчёта восстановлены по формулам таблицы и сверены с её цифрами за
05.08.2026: все девять источников и «Общее» (1240) совпали до строки. Формулы
Google (C3) и Звонков (C11) перенесены дословно, остальные семь восстановлены и
проверены на тех же данных — см. `SOURCE_RULES` ниже.

Сознательные отличия от Apps Script (на цифры не влияют, чинят его дефекты):
  * ошибка API больше не оставляет молча старые данные — сбой пишется в
    `amo_lead_syncs`, и отбивка показывает возраст данных;
  * пагинация идёт по `_links.next`, а не по «страница короче лимита»;
  * дата сделки считается в Asia/Almaty явно, а не в часовом поясе книги;
  * обрыв соединения на середине выгрузки повторяется, а не выбрасывает все
    восемь десятков уже вычитанных страниц.
"""

import html
import logging
import os
import re
import time
from datetime import datetime, timedelta
from http.client import RemoteDisconnected
from zoneinfo import ZoneInfo

import requests

# ==== Настройки ===============================================================

AMO_DOMAIN = (os.getenv("AMO_DOMAIN") or "igroupkz.amocrm.ru").strip()
AMO_ACCESS_TOKEN = (os.getenv("AMO_ACCESS_TOKEN") or "").strip()
AMO_CRM_LOGIN = (os.getenv("AMO_CRM_LOGIN") or "").strip()
AMO_CRM_PASSWORD = (os.getenv("AMO_CRM_PASSWORD") or "").strip()

TZ = ZoneInfo(os.getenv("AMO_LEADS_TIMEZONE", "Asia/Almaty"))

# Глубина выгрузки. В Apps Script стояло 21 день. Нам нужно с запасом: алерты
# сравнивают период с тем же отрезком НЕДЕЛЮ назад, и спросить можно за любой из
# последних девяти дней — значит самая старая нужная база лежит на 9+7 суток
# назад. Меньше 16 не берём, иначе у части дней базы просто не окажется.
SYNC_DAYS = max(16, int(os.getenv("AMO_LEADS_DAYS") or 16))

# ID кастомных полей сделки в amoCRM — те же, что были в скрипте.
FIELD_UTM_SOURCE = int(os.getenv("AMO_FIELD_UTM_SOURCE") or 892237)
FIELD_UTM_MEDIUM = int(os.getenv("AMO_FIELD_UTM_MEDIUM") or 892238)
FIELD_UTM_CAMPAIGN = int(os.getenv("AMO_FIELD_UTM_CAMPAIGN") or 892235)
FIELD_UTM_TERM = int(os.getenv("AMO_FIELD_UTM_TERM") or 892239)
FIELD_REGISTERED = int(os.getenv("AMO_FIELD_REGISTERED") or 1074667)
FIELD_TRIP_DONE = int(os.getenv("AMO_FIELD_TRIP_DONE") or 1074671)
FIELD_ROBOT_CLOSED = int(os.getenv("AMO_FIELD_ROBOT_CLOSED") or 1068877)

PAGE_LIMIT = 250
REQUEST_TIMEOUT = 60
# amoCRM разрешает 7 запросов в секунду — на сотне страниц без паузы легко словить 429.
REQUEST_PAUSE_SECONDS = float(os.getenv("AMO_REQUEST_PAUSE") or 0.2)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Одна выгрузка — это ~86 запросов подряд минуты на полторы, и amoCRM время от
# времени закрывает соединение на середине: на проде так упало 5 прогонов из 39
# за 06–10.08, каждый раз через 12–13 с после старта, то есть примерно на десятой
# странице. Ретрая не было, поэтому обрыв выбрасывал весь результат и отбивка
# показывала «⚠️ Последняя выгрузка не удалась» до следующего синка через 3 часа.
REQUEST_RETRIES = max(1, int(os.getenv("AMO_REQUEST_RETRIES") or 3))
RETRY_BACKOFF_SECONDS = float(os.getenv("AMO_RETRY_BACKOFF") or 1.0)

# Повторяем ТОЛЬКО обрыв уже установленного соединения: сервер закрыл сокет, не
# начав отвечать, — значит запрос не выполнялся, и повтор безопасен. Таймауты и
# отказ в соединении сюда не входят: это «сервис недоступен», и повтор тут только
# добавит нагрузки. Такой же разбор причины есть у клиента Oktell
# (`_oktell_dropped_keepalive` в bot_schedule2.py) — там keep-alive рвёт прокси.
_DROPPED_CONNECTION_ERRORS = (
    RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError)


def _dropped_connection(exc):
    """Оборвалось ли соединение уже ПОСЛЕ установки.

    Причину ищем по всей цепочке, а не по тексту сообщения: requests заворачивает
    urllib3, urllib3 — http.client, и наружу выходит одинаковый на вид
    `ConnectionError('Connection aborted.', ...)`.
    """
    stack = [exc]
    seen = set()
    while stack:
        current = stack.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, _DROPPED_CONNECTION_ERRORS):
            return True
        stack.append(current.__cause__)
        stack.append(current.__context__)
        stack.extend(getattr(current, "args", ()) or ())
    return False


def is_configured() -> bool:
    """Без токена или пары логин/пароль ходить в amoCRM нечем."""
    return bool(AMO_ACCESS_TOKEN or (AMO_CRM_LOGIN and AMO_CRM_PASSWORD))


# ==== Клиент amoCRM ===========================================================

class AmoClient:
    """Bearer-токен: либо долгоживущий из окружения, либо логин/пароль.

    Токен, полученный по логину/паролю, живёт ~40 минут, поэтому запрос сам
    перелогинивается при 401.
    """

    def __init__(self):
        self.base = "https://%s" % AMO_DOMAIN
        self.session = None
        self.expires_at = 0.0
        self._start_session()

    def _start_session(self):
        session = requests.Session()
        session.headers.update({
            "User-Agent": _UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })
        if AMO_ACCESS_TOKEN:
            session.headers["Authorization"] = "Bearer " + AMO_ACCESS_TOKEN
            self.session = session
            self.expires_at = float("inf")
            return
        session.headers.update({"Origin": self.base, "Referer": self.base + "/"})
        self.session = session
        self._login()

    def _request(self, method, url, **kwargs):
        """Запрос к amoCRM с повтором на обрыве соединения.

        Повтор безопасен для всего, что делает клиент: страницы читаются GET'ом, а
        повторный вход просто выдаст ещё один токен. Всё остальное (таймаут, отказ
        в соединении, любой HTTP-код) отдаём наверх как есть.
        """
        for attempt in range(1, REQUEST_RETRIES + 1):
            try:
                return self.session.request(method, url, **kwargs)
            except requests.exceptions.RequestException as exc:
                if attempt >= REQUEST_RETRIES or not _dropped_connection(exc):
                    raise
                logging.warning(
                    "amoCRM: соединение оборвалось (%s), повтор %d из %d: %s",
                    exc, attempt + 1, REQUEST_RETRIES, url)
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    def _login(self):
        if not (AMO_CRM_LOGIN and AMO_CRM_PASSWORD):
            raise RuntimeError("amoCRM: не заданы AMO_ACCESS_TOKEN и AMO_CRM_LOGIN/AMO_CRM_PASSWORD")
        page = self._request("GET", self.base + "/", timeout=REQUEST_TIMEOUT).text
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', page)
        if not match:
            raise RuntimeError("amoCRM: csrf_token не найден на странице входа")
        response = self._request(
            "POST",
            self.base + "/oauth2/authorize",
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
            headers={"X-Requested-With": "XMLHttpRequest"},
            data={
                "username": AMO_CRM_LOGIN,
                "password": AMO_CRM_PASSWORD,
                "csrf_token": match.group(1),
                "temporary_auth": "N",
            },
        )
        if response.status_code != 200:
            raise RuntimeError("amoCRM: вход вернул %s: %s"
                               % (response.status_code, response.text[:300]))
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("amoCRM: во входе нет access_token")
        self.session.headers["Authorization"] = "Bearer " + token
        self.expires_at = time.time() + int(payload.get("expires_in", 2400)) - 120

    def get(self, path, params=None):
        """GET к /api/v4/*. Возвращает None на 204 (пустой ответ — конец списка)."""
        if time.time() > self.expires_at:
            self._login()
        url = path if path.startswith("http") else self.base + path
        for attempt in (1, 2):
            response = self._request("GET", url, params=params or None, timeout=REQUEST_TIMEOUT)
            if response.status_code == 401 and attempt == 1 and not AMO_ACCESS_TOKEN:
                self._login()
                continue
            if response.status_code == 204:
                return None
            if response.status_code != 200:
                raise RuntimeError("amoCRM GET %s -> %s: %s"
                                   % (path, response.status_code, response.text[:300]))
            return response.json()
        raise RuntimeError("amoCRM GET %s: не удалось авторизоваться" % path)


# ==== Выгрузка сделок =========================================================

def _load_stage_names(client):
    """id этапа -> название. Нужен, чтобы в выгрузке был текст, а не число.

    Справочник необязателен: на подсчёт лидов он не влияет (там участвуют только
    теги и utm_source), поэтому нехватка прав не должна ронять выгрузку.
    """
    names = {}
    try:
        data = client.get("/api/v4/leads/pipelines")
    except Exception as exc:
        logging.warning("amoCRM: справочник этапов недоступен (%s), останутся id", exc)
        return names
    for pipeline in ((data or {}).get("_embedded") or {}).get("pipelines") or []:
        for status in (pipeline.get("_embedded") or {}).get("statuses") or []:
            names[status.get("id")] = status.get("name")
    return names


def _load_user_names(client):
    """id пользователя -> имя ответственного.

    Метод `/api/v4/users` открыт только администраторам amoCRM. Учётка, под
    которой ходит бот, админом быть не обязана, поэтому 403 здесь — не ошибка:
    в колонке «ответственный» просто останется id.
    """
    names = {}
    page = 1
    while True:
        try:
            data = client.get("/api/v4/users", {"limit": PAGE_LIMIT, "page": page})
        except Exception as exc:
            logging.warning("amoCRM: справочник пользователей недоступен (%s), останутся id", exc)
            return names
        users = ((data or {}).get("_embedded") or {}).get("users") or []
        if not users:
            break
        for user in users:
            names[user.get("id")] = user.get("name")
        if len(users) < PAGE_LIMIT:
            break
        page += 1
    return names


def _checkbox(value):
    """Галочка в amoCRM -> 1, иначе 0 (в таблице было 1 или пусто)."""
    return 1 if value in (True, "true", 1, "1") else 0


def _map_lead(lead, stage_names, user_names):
    """Сделка amoCRM -> строка выгрузки. Порядок полей как на «Импорт ДЕНЬ»."""
    fields = {}
    for field in lead.get("custom_fields_values") or []:
        values = field.get("values") or []
        fields[field.get("field_id")] = (values[0] or {}).get("value", "") if values else ""

    tags = ", ".join(
        (tag.get("name") or "")
        for tag in ((lead.get("_embedded") or {}).get("tags") or [])
    )

    stage = stage_names.get(lead.get("status_id")) or str(lead.get("status_id") or "")
    # Причина отказа приклеивается к этапу — так же, как это делал Apps Script,
    # иначе «Закрыто и не реализовано» потеряет расшифровку.
    embedded = lead.get("_embedded") or {}
    loss = embedded.get("loss_reason")
    if isinstance(loss, list):
        loss = loss[0] if loss else None
    if isinstance(loss, dict) and loss.get("name"):
        stage = "%s (%s)" % (stage, loss["name"])

    created_ts = int(lead.get("created_at") or 0)
    created_at = datetime.fromtimestamp(created_ts, TZ)

    def text(field_id):
        return str(fields.get(field_id) or "")

    return {
        "lead_id": lead.get("id"),
        "name": lead.get("name") or "",
        "created_at": created_at,
        "created_date": created_at.date(),
        "stage": stage,
        "responsible": user_names.get(lead.get("responsible_user_id"))
                       or str(lead.get("responsible_user_id") or ""),
        "tags": tags,
        "utm_source": text(FIELD_UTM_SOURCE),
        "utm_medium": text(FIELD_UTM_MEDIUM),
        "utm_campaign": text(FIELD_UTM_CAMPAIGN),
        "utm_term": text(FIELD_UTM_TERM),
        "registered": _checkbox(fields.get(FIELD_REGISTERED)),
        "trip_done": _checkbox(fields.get(FIELD_TRIP_DONE)),
        "robot_closed": _checkbox(fields.get(FIELD_ROBOT_CLOSED)),
    }


def fetch_leads(days=None):
    """Сделки, созданные за последние `days` суток (с начала самых ранних суток).

    Apps Script отсчитывал окно от текущего момента, из-за чего самый старый день
    всегда приходил обрезанным. Здесь граница округляется до начала суток.
    """
    days = days or SYNC_DAYS
    start_day = datetime.now(TZ).date() - timedelta(days=days - 1)
    from_ts = int(datetime.combine(start_day, datetime.min.time(), TZ).timestamp())

    client = AmoClient()
    stage_names = _load_stage_names(client)
    user_names = _load_user_names(client)

    rows = []
    url = "/api/v4/leads"
    params = {
        "limit": PAGE_LIMIT,
        "page": 1,
        "with": "loss_reason",
        "filter[created_at][from]": from_ts,
        "order[created_at]": "asc",
    }
    while True:
        data = client.get(url, params)
        if not data:
            break
        leads = ((data or {}).get("_embedded") or {}).get("leads") or []
        if not leads:
            break
        for lead in leads:
            rows.append(_map_lead(lead, stage_names, user_names))
        # Идём по ссылке, которую отдаёт сам API: условие «страница короче лимита»
        # в Apps Script молча теряло часть сделок на неполных страницах.
        next_url = ((data.get("_links") or {}).get("next") or {}).get("href")
        if not next_url:
            break
        url, params = next_url, None
        if REQUEST_PAUSE_SECONDS:
            time.sleep(REQUEST_PAUSE_SECONDS)

    logging.info("amoCRM: выгружено сделок за %s дн.: %d", days, len(rows))
    return rows


# ==== Подсчёт лидов по источникам ============================================
#
# Дословный перенос формул вкладки «Сводка по Дням». Ключевые моменты, которые
# легко потерять при переписывании:
#   * сравнение идёт по подстроке и без учёта регистра — как «*google*» в
#     СЧЁТЕСЛИМН и REGEXMATCH(СТРОЧН(...)) в СУММПРОИЗВ;
#   * общий фильтр «<>*arenda*» и «<>*departament*» применяется к КОЛОНКЕ ТЕГОВ;
#   * у части источников формула складывает два счёта — по utm_source и по тегу,
#     а у части считает только по utm_source. Это не единый шаблон, а девять
#     отдельных формул, и повторять надо именно их поведение.

_BRAND_RE = re.compile(
    r"amanat|adal|global|noltaxi|jana|tenge|tengegruz|itaxi|yataxi|цр|честный|wb|kaspi")
_CHANNEL_RE = re.compile(
    r"google|youtube|sait|tiktok|facebook|olx|olxx|yandex|2gis|googleban")
_CALL_EXCLUDE_RE = re.compile(r"nytime|arenda|аренда|departament")

# Порядок строк — как в таблице.
SOURCE_ORDER = ["Google", "YouTube", "SEO", "TikTok", "FB", "OLX", "Яндекс", "2GIS", "Звонки"]

# Каждая строка «Сводки по Дням» — отдельная формула, и устроены они ПО-РАЗНОМУ:
# где-то складываются два счётчика (и тогда возможен двойной счёт), где-то берётся
# объединение условий, а наборы исключений не совпадают. Ниже каждая перенесена
# дословно, с указанием ячейки-оригинала.
SOURCE_RULES = {
    "Google": "C3: СЧЁТЕСЛИМН(utm~google) + СЧЁТЕСЛИМН(тег~google)",
    "YouTube": "C4: СЧЁТЕСЛИМН(utm~youtube) + СЧЁТЕСЛИМН(тег~youtube)",
    "SEO": "C5: объединение (utm~chatgpt|seo) ИЛИ (utm пуст И тег~sait)",
    "TikTok": "C6: СЧЁТЕСЛИМН(тег~tiktok) + СЧЁТЕСЛИМН(utm~tiktok И тег пуст)",
    "FB": "C7: объединение (тег~fb|facebook) ИЛИ (utm~fb|facebook|ig)",
    "OLX": "C8: СЧЁТЕСЛИМН(тег~olx)",
    "Яндекс": "C9: СЧЁТЕСЛИМН(utm~ya) + СЧЁТЕСЛИМН(тег~yandex)",
    "2GIS": "C10: четыре СЧЁТЕСЛИМН — utm и тег, латиницей и кириллицей",
    "Звонки": "C11: звонок нужного бренда, у которого в теге нет канала",
}


def _tags(row):
    return (row.get("tags") or "").lower()


def _utm(row):
    return (row.get("utm_source") or "").lower()


# Наборы исключений у строк РАЗНЫЕ, и это не небрежность переноса: C3/C4/C6
# отсекают «arenda» и «departament», C8/C9 добавляют кириллическую «аренду»,
# C5/C7/C11 — ещё и «nytime», а C10 аренду не отсекает вовсе.
_EXCL_BASE = ("arenda", "departament")
_EXCL_CYR = ("arenda", "аренда", "departament")
_EXCL_FULL = ("nytime", "arenda", "аренда", "departament")
_EXCL_DEPT = ("departament",)


def _kept(row, words):
    """Условие вида «<>*arenda*» — всегда по колонке тегов, не по utm."""
    tags = _tags(row)
    return not any(word in tags for word in words)


def _has(text, *needles):
    return any(n in text for n in needles)


def count_by_source(rows):
    """Лиды по источникам за уже отфильтрованный по дате набор строк."""

    def count(predicate):
        return sum(1 for r in rows if predicate(r))

    counts = {
        # C3: два счётчика — по utm и по тегу. Строка, где «google» есть в обоих,
        # посчитается дважды: так устроена таблица, воспроизводим как есть.
        "Google": (count(lambda r: "google" in _utm(r) and _kept(r, _EXCL_BASE))
                   + count(lambda r: "google" in _tags(r) and _kept(r, _EXCL_BASE))),
        # C4: тот же шаблон.
        "YouTube": (count(lambda r: "youtube" in _utm(r) and _kept(r, _EXCL_BASE))
                    + count(lambda r: "youtube" in _tags(r) and _kept(r, _EXCL_BASE))),
        # C5: не сумма, а ОБЪЕДИНЕНИЕ — сделка засчитывается один раз, даже если
        # подходит сразу под оба условия.
        "SEO": count(lambda r: (_has(_utm(r), "chatgpt", "seo")
                                or (not _utm(r) and "sait" in _tags(r)))
                     and _kept(r, _EXCL_FULL)),
        # C6: первый счётчик идёт по ТЕГУ, второй — по utm, но только у сделок
        # вообще без тега. Сделка с utm=tiktok и посторонним тегом не считается.
        "TikTok": (count(lambda r: "tiktok" in _tags(r) and _kept(r, _EXCL_BASE))
                   + count(lambda r: "tiktok" in _utm(r) and not _tags(r))),
        # C7: объединение, причём в utm ловится ещё и «ig» (Instagram).
        "FB": count(lambda r: (_has(_tags(r), "fb", "facebook")
                               or _has(_utm(r), "fb", "facebook", "ig"))
                    and _kept(r, _EXCL_FULL)),
        # C8: только по тегу.
        "OLX": count(lambda r: "olx" in _tags(r) and _kept(r, _EXCL_CYR)),
        # C9: utm ловится коротким «ya» — сделки с utm_source=«ya» существуют,
        # и по «yandex» они бы потерялись.
        "Яндекс": (count(lambda r: "ya" in _utm(r) and _kept(r, _EXCL_CYR))
                   + count(lambda r: "yandex" in _tags(r) and _kept(r, _EXCL_CYR))),
        # C10: четыре счётчика (utm и тег, латиница и кириллица), и аренда здесь
        # НЕ отсекается — только departament.
        "2GIS": (count(lambda r: "2gis" in _utm(r) and _kept(r, _EXCL_DEPT))
                 + count(lambda r: "2гис" in _utm(r) and _kept(r, _EXCL_DEPT))
                 + count(lambda r: "2gis" in _tags(r) and _kept(r, _EXCL_DEPT))
                 + count(lambda r: "2гис" in _tags(r) and _kept(r, _EXCL_DEPT))),
        # C11: звонок нужного бренда, у которого в теге нет ни одного канала.
        "Звонки": count(lambda r: "call" in _tags(r)
                        and _BRAND_RE.search(_tags(r))
                        and not _CHANNEL_RE.search(_tags(r))
                        and not _CALL_EXCLUDE_RE.search(_tags(r))),
    }
    counts["Общее"] = sum(counts[name] for name in SOURCE_ORDER)
    return counts


# ==== Алерты по отклонению от нормы ==========================================
#
# Перенос методики из файла «анализ_лидов_алерты.xlsx». База сравнения — тот же
# день НЕДЕЛЮ назад в том же временном окне: полночь сравнивает полные прошедшие
# сутки с теми же сутками семь дней назад, дневная проверка — отрезок с начала
# суток с тем же отрезком того же дня недели.
#
# CPL и всё, что считается от денег, сюда намеренно не перенесено: расходов в
# amoCRM нет, и владелец решил, что считать их не нужно. Поэтому статус у нас
# один — по лидам.


def _env_float(name, default):
    try:
        return float(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default


# Пороги — из листа «Пороги алертов», меняются переменными окружения.
THRESHOLDS = {
    "leads_critical_12h": _env_float("AMO_ALERT_LEADS_CRIT_12H", -0.40),
    # -20% (решение владельца 06.08.2026; в исходном файле стояло -15%).
    "leads_warning_12h": _env_float("AMO_ALERT_LEADS_WARN_12H", -0.20),
    "leads_critical_6h": _env_float("AMO_ALERT_LEADS_CRIT_6H", -0.60),
    "min_base_leads": _env_float("AMO_ALERT_MIN_BASE_LEADS", 10),
}

PERIOD_12H = "12ч"
PERIOD_6H = "6ч"


def _leads_status(period, delta_leads, enough_sample):
    """Колонка «Статус: Лиды».

    Малые выборки не судим по процентам: у SEO, 2ГИС и звонков разница в
    три-пять лидов даёт скачок в десятки процентов, который ничего не значит.
    """
    if not enough_sample or delta_leads is None:
        return "Недостаточно данных"
    if period == PERIOD_6H:
        # Шестичасовая проверка — дозор: ловим явный сбой, а не шум.
        return ("Критично: лиды упали" if delta_leads <= THRESHOLDS["leads_critical_6h"]
                else "Норма")
    if delta_leads <= THRESHOLDS["leads_critical_12h"]:
        return "Критично: лиды упали"
    if delta_leads <= THRESHOLDS["leads_warning_12h"]:
        return "Внимание: лиды ниже нормы"
    return "Норма"


def _verdict(leads_status):
    """Колонки «Итог» и «Рекомендация»."""
    if "Критично" in leads_status:
        return "АЛЕРТ", ("Немедленно проверить канал (форма/оплата/таргетинг) "
                         "и рассмотреть паузу расхода")
    if "Внимание" in leads_status:
        return "Проверить", ("Не менять бюджет сразу — подтвердить тренд "
                             "на следующем чекпоинте")
    if leads_status == "Недостаточно данных":
        return "Мало данных", "Не делать выводов по %: слишком малая выборка"
    return "Норма", "Без действий"


def analyze(current_counts, base_counts, period=PERIOD_12H):
    """Строки анализа по каждому источнику плюс «Общее»."""
    rows = []
    for name in SOURCE_ORDER + ["Общее"]:
        leads = current_counts.get(name, 0)
        base_leads = base_counts.get(name, 0)
        delta_leads = ((leads - base_leads) / base_leads) if base_leads else None
        enough = base_leads >= THRESHOLDS["min_base_leads"]
        leads_status = _leads_status(period, delta_leads, enough)
        verdict, advice = _verdict(leads_status)
        rows.append({
            "source": name, "leads": leads, "base_leads": base_leads,
            "delta_leads": delta_leads, "enough_sample": enough,
            "leads_status": leads_status, "verdict": verdict, "advice": advice,
        })
    return rows


_VERDICT_MARK = {"АЛЕРТ": "🔴", "Проверить": "🟡", "Мало данных": "⚪", "Норма": "🟢"}


def _pct(value):
    return "—" if value is None else "%+.0f%%" % (value * 100)


def render_alert_report(rows, window_label="", base_label="",
                        synced_at=None, sync_error=None, failed_at=None):
    """Текст отбивки: таблица с отклонениями и список того, что требует реакции.

    `synced_at` — время последней УДАЧНОЙ выгрузки, то есть настоящий возраст
    цифр; `failed_at` и `sync_error` — про упавшую попытку. Раньше сюда попадало
    время последней завершённой выгрузки любой судьбы, и при сбое подпись
    сообщала свежесть, которой нет: «Данные обновлены 09:10» рядом с ⚠️ о том,
    что выгрузка 09:10 не удалась, а в таблице лежали цифры от 06:11.
    """
    lines = ["<b>Лиды: проверка за %s</b>" % window_label]
    if base_label:
        lines.append("Сравнение с %s" % base_label)
    lines.append("")

    body = [r for r in rows if r["source"] != "Общее"]
    total = next((r for r in rows if r["source"] == "Общее"), None)
    width = max(len(r["source"]) for r in body) + 1

    lines.append("<pre>")
    lines.append("%-*s %6s %6s %8s" % (width, "Источник", "лиды", "база", "Δ"))
    for r in body:
        lines.append("%-*s %6d %6d %8s" % (width, r["source"], r["leads"],
                                           r["base_leads"], _pct(r["delta_leads"])))
    if total:
        lines.append("-" * (width + 23))
        lines.append("%-*s %6d %6d %8s" % (width, "Общее", total["leads"],
                                           total["base_leads"], _pct(total["delta_leads"])))
    lines.append("</pre>")

    problems = [r for r in rows if r["verdict"] in ("АЛЕРТ", "Проверить")]
    lines.append("")
    if problems:
        for r in problems:
            lines.append("%s <b>%s</b> — %s" % (_VERDICT_MARK.get(r["verdict"], ""),
                                                r["source"], r["leads_status"]))
        lines.append("")
        lines.append(problems[0]["advice"])
    else:
        lines.append("🟢 Отклонений нет.")

    if synced_at:
        lines.append("Данные обновлены %s." % synced_at.astimezone(TZ).strftime("%H:%M"))
    if sync_error:
        when = " (%s)" % failed_at.astimezone(TZ).strftime("%H:%M") if failed_at else ""
        lines.append("⚠️ Последняя выгрузка%s не удалась: %s"
                     % (when, html.escape(str(sync_error)[:160])))
    return "\n".join(lines)


def day_windows(day):
    """Окна для конкретных суток: сами сутки и те же сутки неделю назад.

    Используется, когда день задан явно (/leads 05.08.2026): сравнивать половину
    дня тут не с чем, поэтому берём полные сутки с обеих сторон.
    """
    start = datetime.combine(day, datetime.min.time(), TZ)
    base_start = start - timedelta(days=7)
    return {
        "current_start": start, "current_end": start + timedelta(days=1),
        "base_start": base_start, "base_end": base_start + timedelta(days=1),
        "window_label": "%s (сутки)" % start.strftime("%d.%m"),
        "base_label": "%s (сутки, неделю назад)" % base_start.strftime("%d.%m"),
    }


def alert_windows(now=None):
    """Окна сравнения: текущее и то же окно неделю назад.

    В полночь сутки только что закончились — подводим итог прошедшего дня. В
    остальные часы берём день с начала суток до текущего момента и сравниваем с
    тем же отрезком того же дня недели неделей ранее.
    """
    now = now or datetime.now(TZ)
    midnight = datetime.combine(now.date(), datetime.min.time(), TZ)
    if now.hour == 0:
        current_end = midnight
        current_start = midnight - timedelta(days=1)
        label = "%s (сутки)" % current_start.strftime("%d.%m")
    else:
        current_start = midnight
        current_end = now
        label = "%s, %s–%s" % (now.strftime("%d.%m"),
                               current_start.strftime("%H:%M"), now.strftime("%H:%M"))
    base_start = current_start - timedelta(days=7)
    base_end = current_end - timedelta(days=7)
    # В подписи базы показываем и её отрезок: иначе непонятно, сравнили с целыми
    # сутками или с той же половиной дня.
    if now.hour == 0:
        base_label = "%s (сутки, неделю назад)" % base_start.strftime("%d.%m")
    else:
        base_label = "%s, %s–%s (неделю назад)" % (base_start.strftime("%d.%m"),
                                                   base_start.strftime("%H:%M"),
                                                   base_end.strftime("%H:%M"))
    return {
        "current_start": current_start, "current_end": current_end,
        "base_start": base_start, "base_end": base_end,
        "window_label": label, "base_label": base_label,
    }
