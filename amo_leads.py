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
  * дата сделки считается в Asia/Almaty явно, а не в часовом поясе книги.
"""

import html
import logging
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# ==== Настройки ===============================================================

AMO_DOMAIN = (os.getenv("AMO_DOMAIN") or "igroupkz.amocrm.ru").strip()
AMO_ACCESS_TOKEN = (os.getenv("AMO_ACCESS_TOKEN") or "").strip()
AMO_CRM_LOGIN = (os.getenv("AMO_CRM_LOGIN") or "").strip()
AMO_CRM_PASSWORD = (os.getenv("AMO_CRM_PASSWORD") or "").strip()

TZ = ZoneInfo(os.getenv("AMO_LEADS_TIMEZONE", "Asia/Almaty"))

# Глубина выгрузки. В Apps Script стояло 21 день, потому что таблице нужна была
# история; отбивке хватает сегодняшнего дня, поэтому по умолчанию берём короткое
# окно — синк укладывается в секунды вместо минут. Для перезаливки истории
# значение поднимается переменной окружения.
SYNC_DAYS = max(1, int(os.getenv("AMO_LEADS_DAYS") or 3))

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

    def _login(self):
        if not (AMO_CRM_LOGIN and AMO_CRM_PASSWORD):
            raise RuntimeError("amoCRM: не заданы AMO_ACCESS_TOKEN и AMO_CRM_LOGIN/AMO_CRM_PASSWORD")
        page = self.session.get(self.base + "/", timeout=REQUEST_TIMEOUT).text
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', page)
        if not match:
            raise RuntimeError("amoCRM: csrf_token не найден на странице входа")
        response = self.session.post(
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
            response = self.session.get(url, params=params or None, timeout=REQUEST_TIMEOUT)
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

SOURCE_RULES = {
    "Google": "формула C3, дословно",
    "Звонки": "формула C11, дословно",
    "YouTube": "восстановлено, сверено с таблицей",
    "SEO": "восстановлено, сверено с таблицей",
    "TikTok": "восстановлено, сверено с таблицей",
    "FB": "восстановлено, сверено с таблицей",
    "OLX": "восстановлено, сверено с таблицей",
    "Яндекс": "восстановлено, сверено с таблицей",
    "2GIS": "восстановлено, сверено с таблицей",
}


def _tags(row):
    return (row.get("tags") or "").lower()


def _utm(row):
    return (row.get("utm_source") or "").lower()


def _not_excluded(row):
    """Общий фильтр формул: «<>*arenda*» и «<>*departament*» по колонке тегов."""
    tags = _tags(row)
    return "arenda" not in tags and "departament" not in tags


def count_by_source(rows):
    """Лиды по источникам за уже отфильтрованный по дате набор строк."""
    def by_utm(needle):
        return sum(1 for r in rows if needle in _utm(r) and _not_excluded(r))

    def by_tag(needle):
        return sum(1 for r in rows if needle in _tags(r) and _not_excluded(r))

    counts = {
        # C3: СЧЁТЕСЛИМН по utm_source + СЧЁТЕСЛИМН по тегам.
        "Google": by_utm("google") + by_tag("google"),
        "YouTube": by_utm("youtube") + by_tag("youtube"),
        "Яндекс": by_utm("yandex") + by_tag("yandex"),
        # У этих двух формула считает только по utm_source: теги «*facebook*» и
        # «*tiktok*» стоят и на сделках с другим utm, и второе слагаемое дало бы
        # двойной счёт (проверено: FB было бы 473 вместо 263).
        "FB": by_utm("fb"),
        "TikTok": by_utm("tiktok"),
        # Здесь наоборот — размечено только тегами.
        "OLX": by_tag("olx"),
        # Обе формы написания — одно условие, а не два счётчика: тег, где
        # встречаются сразу «2gis» и «2ГИС», иначе посчитался бы дважды.
        "2GIS": sum(1 for r in rows
                    if ("2gis" in _tags(r) or "2гис" in _tags(r)) and _not_excluded(r)),
        # SEO: свой utm, плюс заявки с сайта без utm, плюс переходы из ChatGPT.
        "SEO": (by_utm("seo")
                + sum(1 for r in rows if "sait" in _tags(r) and not _utm(r) and _not_excluded(r))
                + by_utm("chatgpt")),
        # C11: звонок нужного бренда, у которого в теге нет ни одного канала.
        "Звонки": sum(
            1 for r in rows
            if "call" in _tags(r)
            and _BRAND_RE.search(_tags(r))
            and not _CHANNEL_RE.search(_tags(r))
            and not _CALL_EXCLUDE_RE.search(_tags(r))
        ),
    }
    counts["Общее"] = sum(counts[name] for name in SOURCE_ORDER)
    return counts


def summarize(rows):
    """Отбивка за день: цифры таблицы плюс сколько сделок осталось без источника."""
    counts = count_by_source(rows)
    return {
        "counts": counts,
        "total_leads": counts["Общее"],
        "total_deals": len(rows),
        # Сколько сделок за день не попало ни в один источник. В таблице этой
        # строки нет, и потерю там не видно — а это около четверти сделок.
        "unattributed": max(0, len(rows) - counts["Общее"]),
    }


# ==== Текст отбивки ===========================================================

def render_report(day, summary, synced_at=None, sync_error=None):
    """HTML-текст отбивки для Telegram."""
    counts = summary["counts"]
    width = max(len(name) for name in SOURCE_ORDER) + 1

    lines = ["<b>Лиды за %s</b>" % day.strftime("%d.%m.%Y"), "", "<pre>"]
    for name in SOURCE_ORDER:
        lines.append("%-*s %6d" % (width, name, counts[name]))
    lines.append("-" * (width + 7))
    lines.append("%-*s %6d" % (width, "Общее", counts["Общее"]))
    lines.append("</pre>")

    if summary["unattributed"]:
        share = 100.0 * summary["unattributed"] / max(1, summary["total_deals"])
        lines.append("Сделок в amoCRM за день: %d, не попало в таблицу: %d (%.0f%%)."
                     % (summary["total_deals"], summary["unattributed"], share))

    if synced_at:
        age = datetime.now(TZ) - synced_at
        minutes = max(0, int(age.total_seconds() // 60))
        if minutes < 60:
            ago = "%d мин назад" % minutes
        else:
            ago = "%d ч %d мин назад" % (minutes // 60, minutes % 60)
        lines.append("Данные обновлены %s (%s)."
                     % (synced_at.astimezone(TZ).strftime("%H:%M"), ago))
    if sync_error:
        # Текст ошибки приходит от amoCRM и может содержать < или &, а сообщение
        # уходит с parse_mode=HTML — без экранирования отбивка не отправится вовсе.
        lines.append("⚠️ Последняя выгрузка не удалась: %s"
                     % html.escape(str(sync_error)[:160]))
    return "\n".join(lines)
