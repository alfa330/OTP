"""Конкурс «Топ по регистрациям»: клиент CRM + матчинг операторов + рейтинг.

Источник данных — CRM (backend.yataxi.kz):
  - метод: POST {CRM_API_URL} (= /api/partners/registrations-contest)
  - авторизация: заголовок X-Integration-Token
  - тело: {"registered_from": "YYYY-MM-DD", "registered_to": "YYYY-MM-DD",
           "trip_deadline": "YYYY-MM-DD"}
  - ответ: {"total_registrations", "total_successful", "operators": [...]}
    строка = один оператор CRM с двумя счётчиками:
    operator_id / operator_login / operator_name / operator_group (null),
    registrations_count (все регистрации за период),
    successful_registrations_count (из них с завершённой поездкой — зачёт).

ВАЖНО. Контракт эндпоинта менялся дважды за неделю (проверено живым API):
  * 07.08.2026 — строка на каждого водителя: {"total", "has_more", "rows": [...]}
    с driver_id / driver_phone / registered_at / first_trip_at / trips_count.
  * 13.08.2026 (утро) — {"total", "operators": [...]} с ОДНИМ счётчиком
    registrations_count; ключ rows исчез. Старый парсер читал rows, получал
    пусто и записывал нулевой срез — рейтинг у операторов опустел, а синк
    при этом рапортовал «ok». Отсюда правило ниже.
  * 13.08.2026 (день) — текущий формат с двумя счётчиками.

ПРАВИЛО: незнакомый формат = ошибка синка, а не пустой срез. Мы лучше
покажем админу «CRM отдала не то» и оставим прошлый рейтинг, чем молча
обнулим конкурс. Поэтому fetch_operators() строго требует список operators.

Чего в текущем формате НЕТ (и что из-за этого делаем сами):
  1. operator_group CRM не заполняет — группу (Чаты/Линия) определяем по
     направлению нашего пользователя: «Чат менеджер» СЗоВ -> chat, остальные
     направления СЗоВ -> line, всё прочее (ОП, фронт-офис) -> off.
  2. Времени поездок больше нет, а тай-брейк конкурса — «выше тот, кто набрал
     результат раньше». Момент смены счётчика засекаем сами: синк ходит раз в
     полчаса и штампует reached_at, когда successful у оператора изменился
     (см. Database.upsert_reg_contest_operators). Историю до первого синка на
     новом формате восстановить нечем — у всех, кто с тех пор не изменился,
     reached_at будет одинаковый.
  3. Расшифровки по водителям нет совсем — ни имён, ни телефонов, ни дат.
  4. operator_login CRM = users.email (корп. почта @yandextaxi.kz), но не у
     всех: часть операторов заведена у нас с личной почтой, поэтому фолбэк —
     матчинг по ФИО с фолдингом казахских букв (Нұрасыл == Нурасыл) и по
     префиксу (CRM хранит «Фамилия Имя», у нас часто ещё отчество).

Прочие особенности живого API: page/per_page игнорируются (список всегда
полный), trip_deadline раньше registered_to роняет ответ в HTML с кодом 200.

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
MAX_RETRIES = 3
RETRY_PAUSE = 2.0

# Единственный активный конкурс. Новый конкурс = новый словарь с новым code:
# строки в reg_contest_operators разделяются по contest_code.
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
    """Минимальный клиент POST {CRM_API_URL}."""

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
                # На кривые параметры CRM отвечает HTML-страницей с кодом 200 —
                # для нас это ошибка разбора, а не пустой ответ.
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                log.warning("reg_contest: попытка %s/%s не удалась: %s",
                            attempt, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_PAUSE * attempt)
        raise RuntimeError(f"CRM недоступна после {MAX_RETRIES} попыток: {last_error}")

    def fetch_operators(self, registered_from, registered_to, trip_deadline):
        """Счётчики по операторам за период; формат проверяем строго.

        Пагинации у эндпоинта нет — page/per_page он игнорирует и всегда
        отдаёт полный список операторов."""
        data = self._post({
            "registered_from": registered_from,
            "registered_to": registered_to,
            "trip_deadline": trip_deadline,
        })
        operators = data.get("operators")
        if not isinstance(operators, list):
            raise RuntimeError(
                "CRM отдала незнакомый формат: нет списка operators, "
                f"ключи ответа = {sorted(data)[:10]}")
        seen = set()
        for row in operators:
            if "successful_registrations_count" not in row:
                raise RuntimeError(
                    "CRM отдала операторов без successful_registrations_count, "
                    f"поля = {sorted(row)[:10]}")
            # Дубль operator_id означает, что счётчики оператора разъехались по
            # двум строкам: какой из них верный — снаружи не решить, а «взять
            # первый» тихо занизил бы человеку счёт в конкурсе. Падаем.
            operator_id = str(row.get("operator_id") or "")
            if operator_id in seen:
                raise RuntimeError(
                    f"CRM отдала оператора {operator_id} дважды — счётчики "
                    "неоднозначны, срез не обновляем")
            seen.add(operator_id)
        return operators


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


def _int_or_zero(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def resolve_operators(crm_operators, directory):
    """Обогащение строк CRM привязкой к пользователю iCORE и группе."""
    resolved = []
    for row in crm_operators:
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
            "registrations": _int_or_zero(row.get("registrations_count")),
            "successful": _int_or_zero(row.get("successful_registrations_count")),
        })
    return resolved


# ---------------------------------------------------------------------------
# Рейтинг
# ---------------------------------------------------------------------------

def build_leaderboards(entries):
    """Раскладывает счётчики операторов по группам конкурса и расставляет места.

    Правила из условий конкурса: место — по числу ЗАСЧИТАННЫХ регистраций
    (водитель совершил первую поездку); при равенстве выше тот, кто набрал
    свой результат раньше — сравниваем reached_at, момент последней смены
    счётчика (его штампует БД, см. модульный docstring, п. 2).
    registrations — все регистрации оператора за период, включая ожидающих
    первую поездку; на место не влияют, показываются подписью под счётом.
    Возвращает {"chat": [...], "line": [...], "off": [...]} — off отдельным
    списком, чтобы регистрации других отделов и несопоставленных операторов
    не пропадали молча (наружу off не отдаём — только для диагностики).
    """
    result = {"chat": [], "line": [], "off": []}
    for entry in entries:
        group = entry.get("contest_group") or "off"
        result.setdefault(group, []).append({
            "user_id": entry.get("user_id"),
            "name": entry.get("user_name") or entry.get("operator_name"),
            "operator_login": entry.get("operator_login"),
            "crm_operator_id": entry.get("crm_operator_id"),
            "match_method": entry.get("match_method"),
            "group": group,
            "drivers": _int_or_zero(entry.get("successful")),
            "registrations": _int_or_zero(entry.get("registrations")),
            "reached_at": entry.get("reached_at"),
        })

    for group, items in result.items():
        # reached_at — datetime из БД; None бывает только у строки, которую
        # ещё ни разу не переписывал синк, поэтому страхуем сортировку флагом,
        # чтобы не сравнивать None с датой.
        items.sort(key=lambda a: (-a["drivers"], a["reached_at"] is None,
                                  a["reached_at"] or 0, fold_name(a["name"])))
        prizes = CONTEST["prizes"].get(group) or []
        for idx, item in enumerate(items):
            item["place"] = idx + 1
            # Приз только за засчитанные регистрации: одни «ожидающие поездку»
            # призового места не занимают.
            item["prize"] = (prizes[idx]
                             if idx < len(prizes) and item["drivers"] > 0 else None)
    return result
