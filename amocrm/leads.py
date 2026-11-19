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
  * обрыв соединения или зависшая на середине страница повторяются, а не
    выбрасывают все уже вычитанные страницы.

С 19.11.2026 в тех же цифрах — заявки CRM Syntony (syntony_leads/): лиды туда
переехали из amoCRM. Считаются теми же правилами, один лид в обеих CRM — один
раз; см. раздел «Заявки Syntony и склейка с amoCRM» ниже.
"""

import bisect
import html
import logging
import os
import re
import time
from datetime import datetime, timedelta
from http.client import RemoteDisconnected
from zoneinfo import ZoneInfo

import requests
import urllib3

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
# (подключение, пауза между байтами ответа). Страница приходит за 1–3 с, так что
# 30 с тишины — это уже зависшая передача, а не долгий ответ; ждать её дольше
# значит держать поток общего пула бота впустую (см. `_stalled_body`).
REQUEST_TIMEOUT = (10, 30)
# Насколько окно сравнения может отставать от выгруженных данных, чтобы его ещё
# не обрезать (см. `clamp_windows`).
CLAMP_TOLERANCE = timedelta(minutes=5)
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


def _in_chain(exc, types):
    """Есть ли исключение нужного типа где-то в цепочке.

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
        if isinstance(current, types):
            return True
        stack.append(current.__cause__)
        stack.append(current.__context__)
        stack.extend(getattr(current, "args", ()) or ())
    return False


def _dropped_connection(exc):
    """Оборвалось ли соединение уже ПОСЛЕ установки."""
    return _in_chain(exc, _DROPPED_CONNECTION_ERRORS)


def _stalled_body(exc):
    """amoCRM уже ответил, но передача страницы встала на середине.

    С 05.11.2026 ~02:30 так падали все выгрузки с Render: ответ 200 приходил,
    чтение тела (`read_chunked`) висело до таймаута, а та же выгрузка с другой
    сети шла без единой задержки, и соседние запросы с Render проходили. Это не
    «сервис недоступен», а оборванная в пути передача — её повторяем, как обрыв.

    requests отдаёт её как `ConnectionError` с `ReadTimeoutError` внутри; таймаут
    ДО ответа приходит как `requests.exceptions.ReadTimeout` и повтора не получает.
    """
    if isinstance(exc, requests.exceptions.Timeout):
        return False
    return _in_chain(exc, urllib3.exceptions.ReadTimeoutError)


_CSRF_RE = re.compile(rb'name="csrf_token"\s+value="([^"]+)"')


def _read_csrf_token(response):
    """csrf_token со страницы входа — без дочитывания самой страницы.

    Страница весит ~105 КБ и приходит без сжатия, а токен лежит на ~23-м КБ. С
    05.11.2026 именно она зависала с Render чаще всего: после выкладки повтора все
    19 повторов пришлись на неё, а запросы к /api/v4 в те же минуты проходили.
    Остаток страницы не нужен — соединение закрываем, как только токен найден.
    """
    head = b""
    try:
        for chunk in response.iter_content(8192):
            head += chunk
            match = _CSRF_RE.search(head)
            if match:
                return match.group(1).decode()
    finally:
        response.close()
    return None


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

    def _request(self, method, url, read=None, **kwargs):
        """Запрос к amoCRM с повтором на обрыве соединения или зависшей передаче.

        Повтор безопасен для всего, что делает клиент: страницы читаются GET'ом, а
        повторный вход просто выдаст ещё один токен. Всё остальное (таймаут до
        ответа, отказ в соединении, любой HTTP-код) отдаём наверх как есть.

        `read` дочитывает ответ, взятый с `stream=True`, и его результат
        возвращается вместо ответа — так зависание посреди чтения тоже повторяется.
        """
        for attempt in range(1, REQUEST_RETRIES + 1):
            try:
                response = self.session.request(method, url, **kwargs)
                return read(response) if read else response
            except requests.exceptions.RequestException as exc:
                if attempt >= REQUEST_RETRIES or not (
                        _dropped_connection(exc) or _stalled_body(exc)):
                    raise
                logging.warning(
                    "amoCRM: передача оборвалась (%s), повтор %d из %d: %s",
                    exc, attempt + 1, REQUEST_RETRIES, url)
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    def _login(self):
        if not (AMO_CRM_LOGIN and AMO_CRM_PASSWORD):
            raise RuntimeError("amoCRM: не заданы AMO_ACCESS_TOKEN и AMO_CRM_LOGIN/AMO_CRM_PASSWORD")
        csrf_token = self._request("GET", self.base + "/", read=_read_csrf_token,
                                   stream=True, timeout=REQUEST_TIMEOUT)
        if not csrf_token:
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
                "csrf_token": csrf_token,
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


# ==== Заявки Syntony и склейка с amoCRM ======================================
#
# С ноября 2026 лиды переезжают из amoCRM в CRM Syntony (syntony_leads/), и
# отбивка считает обе CRM вместе — ТЕМИ ЖЕ правилами `count_by_source`. Две
# вещи, без которых сумма врала бы:
#
#   * Переезд шёл с параллелью: лид-формы FB 12–17.11 и TikTok 06–17.11, звонки
#     с 19.11 приходили в ОБЕ CRM. Один лид — две записи («близнецы»), и неделя-
#     база в отбивке удвоилась бы. Близнецов склеиваем (`drop_twins`).
#   * У заявок Syntony свои привычки: теги дописываются в открытую заявку при
#     повторных обращениях, лид-формы аренды до 18.11 приходили без тега аренды.
#     Это выравнивает `syntony_row`.

# Близнецы находятся по времени. Замер 19.11.2026 по выгрузкам обеих CRM за
# 06–19.11: лид-формы доходят в обе CRM за секунды (кампания «iTaxi/16.09.2026»:
# 207 пар из 211 сделок и 213 заявок — в пределах 10 с, в пределах 2 мин — 209),
# а в дни переключения форм сайтов, где лиды НЕ дублировались, пар в 10 с почти
# нет (Google 18.11: 2 из 39, Яндекс и SEO — ни одной). Звонок Syntony заводит
# позже amoCRM — через 6 с…4 мин (один раз 8,6 мин), и тег линии у обоих один:
# для него допуск шире, но только при общем теге. Кампания ключом не служит: по
# ней в 10 минутах идут десятки разных лидов, а близнецы лид-форм и так в 10 с.
TWIN_SECONDS = 10
TWIN_KEYED_SECONDS = 600
# Насколько шире окна отбивки читать обе CRM, чтобы близнец на краю окна нашёл
# пару и не посчитался дважды — в этом окне и в соседнем.
TWIN_MARGIN = timedelta(seconds=TWIN_KEYED_SECONDS)

# Кампании аренды. В amoCRM их лиды несут тег с «arenda» (forma_itaxi_arenda,
# forma_itaxi_tiktok_arenda_avto) и правилами отбивки отсекаются; в Syntony до
# 18.11 те же лиды приходили без тегов, только с кампанией «iTaxi / Аренда авто /
# Видео / 06.04.2026» — 65 заявок FB за 12–16.11.
_RENTAL_RE = re.compile(r"аренд|arenda", re.IGNORECASE)
# Лид-формы рекламных кабинетов: источник однозначен и без utm-метки.
SYNTONY_AD_SOURCES = ("facebook", "tiktok")


def _is_call_tag(tag):
    """Тег линии звонка: call_<парк>_<канал> (бывает и с заглавной: Call_Dongelek)."""
    return (tag or "").strip().lower().startswith("call")


def syntony_row(lead):
    """Заявка Syntony (строка таблицы syntony_leads) → строка для `count_by_source`.

    Отличия от сделки amoCRM, и каждое — чтобы заявка попала в ту же строку
    отбивки, куда попала бы такая же сделка:

      * берётся только ПЕРВЫЙ тег — тот, с которым заявка заведена. Пока заявка
        открыта, Syntony не заводит новую на повторное обращение с того же
        номера, а дописывает тег: 19.11 проверочный номер прозвонил линии
        парков, и у одной заявки набралось 15 тегов `call_itaxi_*` — по всем
        тегам она посчиталась бы в пяти строках сразу;
      * тег звонка у заявки, заведённой НЕ звонком, не считается вовсе: это тот
        же повтор — звонок с номера, у которого уже открыт чат. 19.11 так чат
        ИИ-агента от 15.11 получил тег `call_цр_wb` и посчитался бы звонком за
        15.11, а сам звонок 19.11 есть ещё и сделкой amoCRM;
      * заявка рекламной кампании аренды без тега аренды получает пометку
        «arenda» — как её сделка в amoCRM, которую правила отсекают;
      * лид-форма Facebook/TikTok без utm_source считается по своему источнику.
    """
    tags = [t.strip() for t in (lead.get("tags") or "").split(",") if t.strip()]
    if (lead.get("source") or "") != "phone":
        tags = [t for t in tags if not _is_call_tag(t)]
    first = tags[0] if tags else ""
    campaign = (lead.get("utm_campaign") or "").strip()
    marks = [first] if first else []
    if campaign and _RENTAL_RE.search(campaign) and not _RENTAL_RE.search(first):
        marks.append("arenda")
    utm_source = (lead.get("utm_source") or "").strip()
    if not utm_source and (lead.get("source") or "") in SYNTONY_AD_SOURCES:
        utm_source = lead["source"]
    return {
        "tags": ", ".join(marks),
        "utm_source": utm_source,
        "utm_campaign": campaign,
        "created_at": lead.get("created_at"),
        "first_tag": first.lower(),
    }


def _hits(row):
    """В каких строках отбивки посчитается строка."""
    counts = count_by_source([row])
    return frozenset(name for name in SOURCE_ORDER if counts[name])


def _deal_tags(deal):
    return {t.strip().lower() for t in (deal.get("tags") or "").split(",") if t.strip()}


def _max_pairs(edges, order, blocked=frozenset()):
    """Наибольшее паросочетание «заявка → сделка» (дополняющие цепочки, алгоритм Куна).

    `edges[i]` — сделки-кандидаты заявки i от ближайшей; `blocked` — сделки, уже
    занятые прошлым проходом. Жадное «ближайшие первыми» здесь ошибается: два
    звонка одной линии с задержкой Syntony 4 мин — и ближайшая к первой заявке
    оказывается сделка ВТОРОГО звонка, а вторая заявка остаётся без пары (опыт
    разбора 19.11). Цепочки короткие: рёбра только в пределах 10 минут.
    """
    owner = {}

    def augment(index, seen):
        for position in edges.get(index, ()):
            if position in blocked or position in seen:
                continue
            seen.add(position)
            if position not in owner or augment(owner[position], seen):
                owner[position] = index
                return True
        return False

    for index in order:
        augment(index, set())
    return owner


def drop_twins(amo_rows, syntony_rows):
    """Заявки Syntony без близнецов в amoCRM.

    Близнец — сделка, которая считается в ТОЙ ЖЕ строке отбивки. Два прохода, и
    порядок важен:
      1. общий тег (линии звонка, формы сайта) и не дальше `TWIN_KEYED_SECONDS`:
         так склеиваются звонки, которые Syntony заводит на минуты позже;
      2. среди оставшихся — не дальше `TWIN_SECONDS` без общего тега: лид-формы, у
         которых теги в двух CRM названы по-разному (forma_itaxi_общая и
         fb_forma_itaxi_obshchaya). Звонок так не склеивается: два звонка — это два
         лида, если линии разные, а одна линия склеивается первым проходом.
    Первый проход идёт до второго, иначе заявка звонка в 5 с от сделке ЧУЖОЙ
    линии отнимала бы её у своей заявки (опыт разбора 19.11). Внутри прохода —
    наибольшее число пар (`_max_pairs`), одна сделка — пара не больше чем одной
    заявке.

    Из пары считается сделка amoCRM: её теги размечены интеграцией, по которой
    отбивка считала всё время, и сравнение с прошлыми неделями остаётся честным.
    """
    syntony_rows = list(syntony_rows or [])
    amo = []
    for row in amo_rows or []:
        hits = _hits(row) if row.get("created_at") else frozenset()
        if hits:
            tags = _deal_tags(row)
            amo.append((row["created_at"], hits, tags, any(_is_call_tag(t) for t in tags)))
    amo.sort(key=lambda item: item[0])
    times = [item[0] for item in amo]
    window = timedelta(seconds=TWIN_KEYED_SECONDS)

    keyed, loose = {}, {}
    for index, row in enumerate(syntony_rows):
        created = row.get("created_at")
        hits = _hits(row) if created else frozenset()
        if not hits:
            continue
        first = row.get("first_tag") or ""
        is_call = _is_call_tag(first)
        low = bisect.bisect_left(times, created - window)
        high = bisect.bisect_right(times, created + window)
        for position in range(low, high):
            when, deal_hits, deal_tags, deal_is_call = amo[position]
            if not hits & deal_hits:
                continue
            gap = abs((created - when).total_seconds())
            if first and first in deal_tags:
                keyed.setdefault(index, []).append((gap, position))
            elif gap <= TWIN_SECONDS and not is_call and not deal_is_call:
                loose.setdefault(index, []).append((gap, position))

    def ordered(edges):
        return {i: [p for _g, p in sorted(found)] for i, found in edges.items()}

    by_time = sorted((i for i in range(len(syntony_rows)) if syntony_rows[i].get("created_at")),
                     key=lambda i: syntony_rows[i]["created_at"])
    first_pass = _max_pairs(ordered(keyed), [i for i in by_time if i in keyed])
    paired = set(first_pass.values())
    second_pass = _max_pairs(ordered(loose), [i for i in by_time if i in loose and i not in paired],
                             blocked=frozenset(first_pass))
    paired |= set(second_pass.values())
    return [row for index, row in enumerate(syntony_rows) if index not in paired]


def combined_counts(amo_rows, syntony_rows, start, end, extra_rows=()):
    """Лиды по источникам за [start, end) из обеих CRM.

    `amo_rows` и `syntony_rows` читаются шире окна на `TWIN_MARGIN` с обеих
    сторон — только ради пар на краю; считаются строки внутри окна. Заявки
    Syntony — уже через `syntony_row`. `extra_rows` — строки не из CRM (журнал
    робота OLX, когда сделки он больше не заводит), считаются как есть.
    """
    def inside(row):
        created = row.get("created_at")
        return created is not None and start <= created < end

    kept = drop_twins(amo_rows, syntony_rows)
    rows = [r for r in amo_rows or [] if inside(r)]
    rows += [r for r in kept if inside(r)]
    rows += [r for r in extra_rows or [] if inside(r)]
    return count_by_source(rows)


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
                        synced_at=None, sync_error=None, failed_at=None,
                        more_sources=None):
    """Текст отбивки: таблица с отклонениями и список того, что требует реакции.

    `synced_at` — время последней УДАЧНОЙ выгрузки, то есть настоящий возраст
    цифр; `failed_at` и `sync_error` — про упавшую попытку. Раньше сюда попадало
    время последней завершённой выгрузки любой судьбы, и при сбое подпись
    сообщала свежесть, которой нет: «Данные обновлены 09:10» рядом с ⚠️ о том,
    что выгрузка 09:10 не удалась, а в таблице лежали цифры от 06:11.

    `more_sources` — другие CRM в тех же цифрах (Syntony): список словарей
    {name, synced_at, error, failed_at}. С ними подпись называет каждую CRM.
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

    def clock(value):
        return value.astimezone(TZ).strftime("%H:%M")

    if not more_sources:
        if synced_at:
            lines.append("Данные обновлены %s." % clock(synced_at))
        if sync_error:
            when = " (%s)" % clock(failed_at) if failed_at else ""
            lines.append("⚠️ Последняя выгрузка%s не удалась: %s"
                         % (when, html.escape(str(sync_error)[:160])))
        return "\n".join(lines)

    sources = [{"name": "amoCRM", "synced_at": synced_at, "error": sync_error,
                "failed_at": failed_at}] + list(more_sources)
    lines.append("Данные обновлены: %s." % ", ".join(
        "%s %s" % (s["name"], clock(s["synced_at"]) if s.get("synced_at") else "—")
        for s in sources))
    for s in sources:
        if s.get("error"):
            when = " (%s)" % clock(s["failed_at"]) if s.get("failed_at") else ""
            lines.append("⚠️ %s: выгрузка%s не удалась: %s"
                         % (s["name"], when, html.escape(str(s["error"])[:160])))
    return "\n".join(lines)


def _window_labels(current_start, current_end, base_start, base_end):
    """Подписи окна и базы. Полные сутки называем сутками, иначе — отрезком.

    Отрезок базы показываем целиком: иначе непонятно, сравнили с целыми сутками
    или с той же половиной дня.
    """
    whole_day = (current_end - current_start == timedelta(days=1)
                 and current_start.timetz().replace(tzinfo=None) == datetime.min.time())
    if whole_day:
        return ("%s (сутки)" % current_start.strftime("%d.%m"),
                "%s (сутки, неделю назад)" % base_start.strftime("%d.%m"))
    return ("%s, %s–%s" % (current_start.strftime("%d.%m"),
                           current_start.strftime("%H:%M"),
                           current_end.strftime("%H:%M")),
            "%s, %s–%s (неделю назад)" % (base_start.strftime("%d.%m"),
                                          base_start.strftime("%H:%M"),
                                          base_end.strftime("%H:%M")))


def clamp_windows(windows, data_until):
    """Обрезать окно сравнения по тому, что реально выгружено из amoCRM.

    Днём «сегодня» сравнивается с тем же отрезком неделю назад, но в БД сегодня
    доходит только до последней удачной выгрузки, а она идёт раз в три часа. Без
    обрезки последние часы «сегодня» просто отсутствуют, и отбивка показывает
    падение, которого нет: 10.08 в 11:53 (данные от 06:11) все девять источников
    ушли в минус, у SEO и OLX встали нули, «Общее» дало -51%.

    Обрезаем ОБА отрезка одинаково — сравнивать надо равные по длине куски.
    `data_until` — время начала последней удачной выгрузки: всё, что создано
    позже, могло в неё не попасть.
    """
    if not data_until:
        return windows
    data_until = data_until.astimezone(TZ)
    # Мелкий зазор не трогаем: выгрузка идёт полторы минуты, и после неё окно и
    # так отстаёт на эти полторы минуты. Обрезать по ним — значит превратить итог
    # за сутки в «00:00–23:58» и потерять пару лидов из подписи ради ничего.
    if data_until >= windows["current_end"] - CLAMP_TOLERANCE:
        return windows
    # Данных за окно нет вовсе — обрезать нечего, отвечает вызывающий (досинком).
    if data_until <= windows["current_start"]:
        return windows
    current_end = data_until
    base_end = windows["base_start"] + (current_end - windows["current_start"])
    window_label, base_label = _window_labels(
        windows["current_start"], current_end, windows["base_start"], base_end)
    return {**windows, "current_end": current_end, "base_end": base_end,
            "window_label": window_label, "base_label": base_label}


def day_windows(day):
    """Окна для конкретных суток: сами сутки и те же сутки неделю назад.

    Используется, когда день задан явно (/leads 05.08.2026): сравнивать половину
    дня тут не с чем, поэтому берём полные сутки с обеих сторон.
    """
    start = datetime.combine(day, datetime.min.time(), TZ)
    base_start = start - timedelta(days=7)
    current_end = start + timedelta(days=1)
    base_end = base_start + timedelta(days=1)
    window_label, base_label = _window_labels(start, current_end, base_start, base_end)
    return {
        "current_start": start, "current_end": current_end,
        "base_start": base_start, "base_end": base_end,
        "window_label": window_label, "base_label": base_label,
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
    else:
        current_start = midnight
        current_end = now
    base_start = current_start - timedelta(days=7)
    base_end = current_end - timedelta(days=7)
    window_label, base_label = _window_labels(current_start, current_end,
                                              base_start, base_end)
    return {
        "current_start": current_start, "current_end": current_end,
        "base_start": base_start, "base_end": base_end,
        "window_label": window_label, "base_label": base_label,
    }
