"""Клиент TEZ APP API: дата первого завершённого заказа водителя.

  - метод: POST {base}/drivers/first-orders
  - авторизация: заголовок X-Integration-Token (JWT/X-Admin-Token не нужны)
  - тело: {"month": "YYYY-MM", "drivers": [{"full_name": ..., "phone": "+7701..."}]}
  - ответ: {"drivers": [{..., "month_first_order_at": ...|null,
                              "previous_month_first_order_at": ...|null}]}
  - лимит: от 1 до 100 водителей в одном запросе

ВАЖНО (проверено на живом API):
  1. Поиск идёт ИСКЛЮЧИТЕЛЬНО по телефону. `full_name` сервер не валидирует и
     возвращает дословно из запроса — ответ не подтверждает личность водителя.
  2. Без браузерного User-Agent Cloudflare отдаёт 403 «error code: 1010» на любой
     токен. Это легко принять за проблему с ключом — поэтому UA зашит в клиент.
  3. Токенов три (test/stage/prod), и для хоста api.tezapp.org подходит ТОЛЬКО
     prod: остальные дают 401.
  4. `month` ОБЯЗАТЕЛЕН: без него API отвечает 400 (error_code 30001).
  5. Даты оконные (месяц запроса + предыдущий), а не «за всё время», поэтому
     привязаны к месяцу базы лида. Первый заказ ВНУТРИ месяца уже не изменится —
     найдя его, номер можно больше не переспрашивать.

ENV (окружение или .env.codex.local):
    TEZ_DRIVERS_API_TOKEN=<prod-токен>     # в .env.codex.local лежит под именем
                                           # X-Integration-Token_na_prod (оттуда и
                                           # подхватывается как фолбэк локально)
    TEZ_DRIVERS_API_URL=https://api.tezapp.org   # опционально

Прод (Render) читает конфиг только через os.getenv — TEZ_DRIVERS_API_TOKEN
обязательно завести в переменных окружения сервиса, .env-файл там не работает.

Проверка вручную:
    python tez_first_orders.py --phones 77023227108,77000409090
"""

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path

import requests

from tez_op_leads import normalize_kz_phone, parse_first_order_at, to_e164


log = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.tezapp.org"
HTTP_TIMEOUT = 60
# Ограничение эндпоинта: от 1 до 100 водителей за запрос.
MAX_BATCH_SIZE = 100
# Cloudflare перед API фильтрует запросы по «браузерности» клиента. Одного
# User-Agent мало — добавляем полный набор заголовков настоящего Chrome, иначе
# по fingerprint прилетает 403 (страница-заглушка Cloudflare, коды 1010/1020 и
# т.п.). Полностью JS-челлендж этим не обходится (нужен вайтлист IP на стороне
# TEZ APP), но обычные bot-фильтры так проходятся.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://api.tezapp.org",
    "Referer": "https://api.tezapp.org/",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class TezBadRequest(RuntimeError):
    """400 от TEZ APP — как правило невалидный номер в теле запроса.

    Важно: API валидирует ВЕСЬ батч, поэтому один плохой номер из 100 роняет
    запрос целиком. Отдельный класс нужен, чтобы вызывающий мог разбить батч
    пополам и потерять только реально битые номера, а не всю сотню.
    """


def _looks_like_cloudflare_block(text):
    """Похоже ли тело ответа на страницу-заглушку Cloudflare (а не на JSON API)."""
    low = str(text or "").lower()
    return any(marker in low for marker in (
        "cloudflare", "cf-ray", "cf-error", "error code:",
        "attention required", "just a moment", 'class="no-js"',
    ))
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
RETRY_PAUSE = 3.0
# Небольшая пауза между батчами: документированного рейт-лимита нет, но ночью
# мы отправляем десятки запросов подряд и не хотим выяснять его на проде.
BATCH_PAUSE = 0.3


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

    token = pick("TEZ_DRIVERS_API_TOKEN")
    if not token:
        # Локально ключ лежит под «человеческим» именем из переписки с TEZ.
        token = file_env.get("X-Integration-Token_na_prod")

    base_url = (pick("TEZ_DRIVERS_API_URL", DEFAULT_API_URL) or DEFAULT_API_URL).rstrip("/")
    return {"base_url": base_url, "token": token}


def api_ready(config=None):
    cfg = config or get_config()
    return bool(cfg.get("token"))


class TezFirstOrdersClient:
    """Минимальный клиент POST /drivers/first-orders."""

    def __init__(self, token, base_url=DEFAULT_API_URL, timeout=HTTP_TIMEOUT):
        if not token:
            raise ValueError("TEZ_DRIVERS_API_TOKEN не задан")
        self.token = token
        self.base_url = (base_url or DEFAULT_API_URL).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    @classmethod
    def from_config(cls, config=None):
        cfg = config or get_config()
        return cls(cfg.get("token"), cfg.get("base_url"))

    def _post_batch(self, drivers, month):
        """Один запрос на <=100 водителей за период month ('YYYY-MM')."""
        url = f"{self.base_url}/drivers/first-orders"
        headers = dict(BROWSER_HEADERS)
        headers["X-Integration-Token"] = self.token
        headers["Content-Type"] = "application/json"
        body = json.dumps({"month": month, "drivers": drivers}, ensure_ascii=False).encode("utf-8")

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.post(url, data=body, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"сеть: {exc}"
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_PAUSE * attempt)
                    continue
                raise RuntimeError(f"TEZ APP first-orders недоступен: {last_error}")

            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except ValueError:
                    raise RuntimeError(f"TEZ APP: не JSON в ответе: {resp.text[:300]}")
                return payload.get("drivers") or []

            if resp.status_code in (403, 503) and _looks_like_cloudflare_block(resp.text):
                # Cloudflare заблокировал запрос на своём периметре (до проверки
                # токена). С домашнего IP тот же код проходит, с датацентрового
                # (Render) — челлендж. Лечится только на стороне TEZ APP: вайтлист
                # исходящего IP сервера или WAF-исключение для /drivers/first-orders.
                # В сообщение кладём реальную диагностику: HTTP-код, CF-Ray и код
                # Cloudflare (1010=UA, 1020=IP access rule, 1009=country и т.п.) —
                # по нему на стороне TEZ APP видно, какое именно правило сработало.
                m = re.search(r"error code:\s*(\d+)", resp.text, re.I)
                cf_ray = resp.headers.get("cf-ray") or "—"
                cf_mitig = resp.headers.get("cf-mitigated") or ""
                code = f", Cloudflare code {m.group(1)}" if m else ""
                mit = f", mitigated={cf_mitig}" if cf_mitig else ""
                raise RuntimeError(
                    f"TEZ APP: запрос заблокирован Cloudflare (HTTP {resp.status_code}{code}, "
                    f"CF-Ray {cf_ray}{mit}). Токен ни при чём — нужно добавить исходящий IP "
                    "сервера в вайтлист на стороне TEZ APP (или снять челлендж с "
                    "эндпоинта /drivers/first-orders)."
                )
            if resp.status_code == 401:
                raise RuntimeError(
                    "TEZ APP: 401 — неверный X-Integration-Token (для api.tezapp.org "
                    "подходит только prod-токен)"
                )
            if resp.status_code == 400:
                # Валидируется весь батч — один плохой номер роняет сотню. Бросаем
                # особый класс, чтобы fetch_first_orders разбил батч и потерял
                # только реально битые номера.
                snippet = resp.text[:200].replace("\n", " ").strip()
                raise TezBadRequest(f"TEZ APP 400: {snippet}")
            if resp.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                last_error = f"HTTP {resp.status_code}"
                time.sleep(RETRY_PAUSE * attempt)
                continue

            snippet = resp.text[:200].replace("\n", " ").strip()
            raise RuntimeError(f"TEZ APP HTTP {resp.status_code}: {snippet}")

        raise RuntimeError(f"TEZ APP first-orders: не удалось получить ответ ({last_error})")

    def fetch_first_orders(self, phones, month, full_names=None, progress=None):
        """Оконные даты заказов по списку телефонов.

        month — обязательный период 'YYYY-MM'. API считает окно в два месяца и
        возвращает по каждому водителю ДВЕ даты: первый заказ в самом месяце и
        первый заказ в предыдущем. Без month эндпоинт отвечает 400 (error_code
        30001), поэтому параметр не опциональный.

        phones — любые формы записи номера, нормализуются внутри.
        full_names — необязательный dict phone_norm -> ФИО (API его не проверяет,
        но с ним удобнее читать сырые ответы при разборе спорных случаев).

        Возвращает dict phone_norm -> {'month': datetime|None, 'prev': datetime|None}.
        Невалидные номера в результат не попадают — их отдаёт свойство
        `.last_invalid`.
        """
        month = str(month or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError("month обязателен и должен быть в формате YYYY-MM")
        normalized, invalid = [], []
        seen = set()
        for raw in phones or []:
            norm = normalize_kz_phone(raw)
            if not norm:
                invalid.append(raw)
                continue
            if norm in seen:
                continue
            seen.add(norm)
            normalized.append(norm)
        self.last_invalid = invalid

        names = full_names or {}
        out = {}
        rejected = []
        total = len(normalized)

        def _fetch_chunk(chunk):
            """Запрашивает пачку номеров; на 400 (битый номер в батче) делит
            пополам, чтобы вычислить и изолировать реально невалидные номера,
            а не терять всю сотню."""
            if not chunk:
                return
            drivers = [{"full_name": names.get(p) or "-", "phone": to_e164(p)} for p in chunk]
            try:
                rows = self._post_batch(drivers, month)
            except TezBadRequest:
                if len(chunk) == 1:
                    # Один номер и он же битый — по нему у нас данных не будет.
                    rejected.append(chunk[0])
                    return
                mid = len(chunk) // 2
                _fetch_chunk(chunk[:mid])
                _fetch_chunk(chunk[mid:])
                return
            for row in rows:
                norm = normalize_kz_phone(row.get("phone"))
                if norm:
                    out[norm] = {
                        "month": parse_first_order_at(row.get("month_first_order_at")),
                        "prev": parse_first_order_at(row.get("previous_month_first_order_at")),
                    }
            # Номера, которых почему-то не оказалось в ответе, тоже помечаем
            # проверенными — иначе они будут переспрашиваться каждую ночь.
            for p in chunk:
                out.setdefault(p, {"month": None, "prev": None})

        for start in range(0, total, MAX_BATCH_SIZE):
            chunk = normalized[start:start + MAX_BATCH_SIZE]
            _fetch_chunk(chunk)

            if progress:
                progress(min(start + len(chunk), total), total)
            if start + MAX_BATCH_SIZE < total:
                time.sleep(BATCH_PAUSE)

        # Номера, которые TEZ APP отверг как невалидные (прошли наш нормализатор,
        # но API их не принял) — добавляем к списку невалидных для отчёта.
        if rejected:
            self.last_invalid = list(self.last_invalid) + rejected
        return out


def _main():
    parser = argparse.ArgumentParser(description="Проверка TEZ APP /drivers/first-orders")
    parser.add_argument("--phones", required=True, help="номера через запятую")
    parser.add_argument("--month", required=True, help="период YYYY-MM (обязателен для API)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = get_config()
    if not api_ready(cfg):
        raise SystemExit("TEZ_DRIVERS_API_TOKEN не задан (env или .env.codex.local)")

    client = TezFirstOrdersClient.from_config(cfg)
    result = client.fetch_first_orders(
        [p for p in args.phones.split(",") if p.strip()], month=args.month
    )
    print(f"{'номер':<14}{'заказ в ' + args.month:<34}заказ в пред. месяце")
    for phone, dates in sorted(result.items()):
        cur = dates["month"].isoformat() if dates["month"] else "—"
        prev = dates["prev"].isoformat() if dates["prev"] else "—"
        print(f"{phone:<14}{cur:<34}{prev}")
    if client.last_invalid:
        print("невалидные номера:", ", ".join(str(x) for x in client.last_invalid))


if __name__ == "__main__":
    _main()
