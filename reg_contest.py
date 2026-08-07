"""Конкурс «Топ по регистрациям»: клиент CRM + матчинг операторов + рейтинг.

Источник данных — CRM (backend.yataxi.kz):
  - метод: POST {CRM_API_URL} (= /api/partners/registrations-contest)
  - авторизация: заголовок X-Integration-Token
  - тело: {"registered_from": "YYYY-MM-DD", "registered_to": "YYYY-MM-DD",
           "trip_deadline": "YYYY-MM-DD", "page": N, "per_page": M}
  - ответ: {"total", "page", "per_page", "has_more", "rows": [...]}
    строка = один ЗАСЧИТАННЫЙ водитель (дедуп и фильтр «есть завершённая
    поездка до trip_deadline» делает CRM); поля строки:
    operator_id / operator_login / operator_name / operator_group (null),
    driver_id / driver_phone / driver_name,
    registered_at / first_trip_at (ISO с +05:00), trips_count.

ВАЖНО (проверено на живом API 2026-08-07):
  1. operator_group CRM не заполняет — группу (Чаты/Линия) определяем сами по
     направлению нашего пользователя: «Чат менеджер» СЗоВ -> chat, остальные
     направления СЗоВ -> line, всё прочее (ОП, фронт-офис) -> off («вне зачёта»).
  2. operator_login CRM = users.email (корп. почта @yandextaxi.kz), но не у
     всех: часть операторов заведена у нас с личной почтой, поэтому фолбэк —
     матчинг по ФИО с фолдингом казахских букв (Нұрасыл == Нурасыл) и по
     префиксу (CRM хранит «Фамилия Имя», у нас часто ещё отчество).
  3. Даты приходят с таймзоной (+05:00) — храним timestamptz как есть.

ENV (окружение или .env.codex.local):
    CRM_API_URL=https://backend.yataxi.kz/api/partners/registrations-contest
    CRM_INTEGRATION_TOKEN=<токен>   # локально лежит под именем X-Integration-Token
"""

import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

HTTP_TIMEOUT = 60
PER_PAGE = 500
MAX_PAGES = 200          # предохранитель от бесконечного has_more
MAX_RETRIES = 3
RETRY_PAUSE = 2.0

# Единственный активный конкурс. Новый конкурс = новый словарь с новым code:
# строки в reg_contest_entries разделяются по contest_code.
CONTEST = {
    "code": "top_registrations_2026_09",
    "title": "Топ по регистрациям",
    "registered_from": "2026-08-07",   # включительно
    "registered_to": "2026-09-07",     # включительно
    "trip_deadline": "2026-09-11",     # завершённая поездка до этой даты включительно
    "results_date": "2026-09-11",      # день подведения итогов
    # Призовые: индекс = место - 1.
    "prizes": {"chat": [40000, 20000], "line": [40000, 25000, 10000]},
}

GROUP_LABELS = {"chat": "Чаты", "line": "Линия"}


def _parse_env_file(path):
    """Простой парсер .env (KEY=VALUE); кавычки снимаем."""
    data = {}
    try:
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return data


def get_config(env_file=".env.codex.local"):
    """Конфиг из окружения; чего нет — добираем из .env.codex.local."""
    file_env = _parse_env_file(env_file)

    def pick(name, default=None):
        return os.getenv(name) or file_env.get(name) or default

    token = pick("CRM_INTEGRATION_TOKEN")
    if not token:
        # На Render и локально ключ заведён под «человеческим» именем из
        # переписки с CRM — с дефисами, как сам заголовок.
        token = pick("X-Integration-Token")
    url = pick("CRM_API_URL")
    return {"url": url, "token": token}


def api_ready(config=None):
    cfg = config or get_config()
    return bool(cfg.get("url") and cfg.get("token"))


class RegContestClient:
    """Минимальный клиент POST {CRM_API_URL} с пагинацией."""

    def __init__(self, url, token, timeout=HTTP_TIMEOUT):
        if not url or not token:
            raise ValueError("CRM_API_URL / CRM_INTEGRATION_TOKEN не заданы")
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
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                log.warning("reg_contest: попытка %s/%s не удалась: %s",
                            attempt, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_PAUSE * attempt)
        raise RuntimeError(f"CRM недоступна после {MAX_RETRIES} попыток: {last_error}")

    def fetch_all_rows(self, registered_from, registered_to, trip_deadline):
        """Все страницы за период; возвращает список сырых строк CRM."""
        rows = []
        page = 1
        while page <= MAX_PAGES:
            data = self._post({
                "registered_from": registered_from,
                "registered_to": registered_to,
                "trip_deadline": trip_deadline,
                "page": page,
                "per_page": PER_PAGE,
            })
            batch = data.get("rows") or []
            rows.extend(batch)
            if not data.get("has_more") or not batch:
                break
            page += 1
        else:
            raise RuntimeError(f"CRM: не дошли до конца пагинации за {MAX_PAGES} страниц")
        return rows


# ---------------------------------------------------------------------------
# Матчинг оператора CRM -> пользователь iCORE
# ---------------------------------------------------------------------------

# Казахские буквы -> русские «двойники»: CRM и наша база пишут одни и те же
# имена то так, то так («Нұрасыл» == «Нурасыл»).
_KZ_FOLD = str.maketrans({
    "ә": "а", "ғ": "г", "қ": "к", "ң": "н", "ө": "о",
    "ұ": "у", "ү": "у", "һ": "х", "і": "и", "ё": "е",
})


def fold_name(value):
    """Нормализация ФИО для сравнения: регистр, казахские буквы, пробелы."""
    if not value:
        return ""
    return " ".join(str(value).lower().translate(_KZ_FOLD).split())


def classify_group(direction_name, department_name):
    """Группа конкурса по направлению пользователя iCORE."""
    dept = (department_name or "").strip().lower()
    if not dept.startswith("сзов"):
        return "off"
    direction = (direction_name or "").strip().lower()
    return "chat" if direction.startswith("чат") else "line"


def match_operator(crm_login, crm_name, directory):
    """Сопоставление оператора CRM с пользователем iCORE.

    directory — список словарей {id, name, email, status, direction_name,
    department_name}. Возвращает (user_dict | None, match_method).
    Порядок строгий, от точного к нестрогому:
      1) email == operator_login (любой статус — регистрации уволенного
         остаются за ним);
      2) точное совпадение свёрнутого ФИО;
      3) CRM-ФИО («Фамилия Имя») — префикс нашего ФИО с отчеством; берём
         только однозначное совпадение, двусмысленность = не сопоставлен.
    """
    login = (crm_login or "").strip().lower()
    if login:
        for user in directory:
            if (user.get("email") or "").strip().lower() == login:
                return user, "email"

    folded = fold_name(crm_name)
    if not folded:
        return None, "none"

    exact = [u for u in directory if fold_name(u.get("name")) == folded]
    if len(exact) == 1:
        return exact[0], "name"

    prefix = [u for u in directory
              if fold_name(u.get("name")).startswith(folded + " ")]
    if len(prefix) == 1:
        return prefix[0], "name_prefix"

    return None, "none"


def resolve_rows(crm_rows, directory):
    """Обогащение сырых строк CRM привязкой к пользователю и группе."""
    resolved = []
    for row in crm_rows:
        user, method = match_operator(row.get("operator_login"),
                                      row.get("operator_name"), directory)
        group = "off"
        if user:
            group = classify_group(user.get("direction_name"),
                                   user.get("department_name"))
        resolved.append({
            "crm_operator_id": str(row.get("operator_id") or ""),
            "operator_login": row.get("operator_login"),
            "operator_name": row.get("operator_name"),
            "user_id": user.get("id") if user else None,
            "user_name": user.get("name") if user else None,
            "contest_group": group,
            "match_method": method,
            "driver_id": str(row.get("driver_id") or ""),
            "driver_phone": row.get("driver_phone"),
            "driver_name": row.get("driver_name"),
            "registered_at": row.get("registered_at"),
            "first_trip_at": row.get("first_trip_at"),
            "trips_count": row.get("trips_count"),
        })
    return resolved


# ---------------------------------------------------------------------------
# Рейтинг
# ---------------------------------------------------------------------------

def build_leaderboards(entries):
    """Схлопывает строки-водители в рейтинги по группам конкурса.

    Правила из условий конкурса: место — по числу засчитанных водителей;
    при равенстве выше тот, кто раньше достиг итога — сравниваем время
    ПОСЛЕДНЕЙ засчитанной поездки (меньше = раньше = выше).
    Возвращает {"chat": [...], "line": [...], "off": [...]} — off отдельным
    списком, чтобы админ видел регистрации вне зачёта (ОП, фронт-офис,
    несопоставленные), а не терял их молча.
    """
    buckets = {}
    for entry in entries:
        group = entry.get("contest_group") or "off"
        # Ключ участника: наш пользователь, а без него — оператор CRM.
        key = (group, entry.get("user_id") or f"crm:{entry.get('crm_operator_id')}")
        agg = buckets.get(key)
        if agg is None:
            agg = buckets[key] = {
                "user_id": entry.get("user_id"),
                "name": entry.get("user_name") or entry.get("operator_name"),
                "operator_login": entry.get("operator_login"),
                "crm_operator_id": entry.get("crm_operator_id"),
                "match_method": entry.get("match_method"),
                "group": group,
                "drivers": 0,
                "last_trip_at": None,
                "rows": [],
            }
        agg["drivers"] += 1
        trip_at = entry.get("first_trip_at")
        if trip_at and (agg["last_trip_at"] is None or trip_at > agg["last_trip_at"]):
            agg["last_trip_at"] = trip_at
        agg["rows"].append({
            "driver_id": entry.get("driver_id"),
            "driver_phone": entry.get("driver_phone"),
            "driver_name": entry.get("driver_name"),
            "registered_at": entry.get("registered_at"),
            "first_trip_at": entry.get("first_trip_at"),
            "trips_count": entry.get("trips_count"),
        })

    result = {"chat": [], "line": [], "off": []}
    for agg in buckets.values():
        # Даты — строки ISO (свежий ответ CRM) либо datetime (чтение из БД);
        # None по контракту не бывает, но сортировку от него страхуем флагом,
        # чтобы не сравнивать None с датой.
        agg["rows"].sort(key=lambda r: (r.get("registered_at") is None,
                                        r.get("registered_at") or 0))
        result.setdefault(agg["group"], []).append(agg)
    for group, items in result.items():
        items.sort(key=lambda a: (-a["drivers"], a["last_trip_at"] is None,
                                  a["last_trip_at"] or 0, fold_name(a["name"])))
        prizes = CONTEST["prizes"].get(group) or []
        for idx, item in enumerate(items):
            item["place"] = idx + 1
            item["prize"] = prizes[idx] if idx < len(prizes) else None
    return result
