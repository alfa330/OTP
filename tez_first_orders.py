"""Клиент TEZ APP API: дата первого завершённого заказа водителя.

  - метод: POST {base}/drivers/first-orders
  - авторизация: заголовок X-Integration-Token (JWT/X-Admin-Token не нужны)
  - тело: {"drivers": [{"full_name": ..., "phone": "+7701..."}, ...]}
  - ответ: {"drivers": [{..., "first_order_at": "2026-03-14T10:35:21+05:00"|null}]}
  - лимит: от 1 до 100 водителей в одном запросе

ВАЖНО (проверено на живом API):
  1. Поиск идёт ИСКЛЮЧИТЕЛЬНО по телефону. `full_name` сервер не валидирует и
     возвращает дословно из запроса — ответ не подтверждает личность водителя.
  2. Без браузерного User-Agent Cloudflare отдаёт 403 «error code: 1010» на любой
     токен. Это легко принять за проблему с ключом — поэтому UA зашит в клиент.
  3. Токенов три (test/stage/prod), и для хоста api.tezapp.org подходит ТОЛЬКО
     prod: остальные дают 401.
  4. first_order_at неизменна: узнав её однажды, повторно спрашивать номер не надо.

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

    def _post_batch(self, drivers):
        """Один запрос на <=100 водителей. Возвращает список строк ответа."""
        url = f"{self.base_url}/drivers/first-orders"
        headers = dict(BROWSER_HEADERS)
        headers["X-Integration-Token"] = self.token
        headers["Content-Type"] = "application/json"
        body = json.dumps({"drivers": drivers}, ensure_ascii=False).encode("utf-8")

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
                m = re.search(r"error code:\s*(\d+)", resp.text, re.I)
                code = f" (Cloudflare code {m.group(1)})" if m else ""
                raise RuntimeError(
                    f"TEZ APP: запрос заблокирован Cloudflare{code}. Токен ни при чём — "
                    "нужно добавить исходящий IP сервера в вайтлист на стороне TEZ APP "
                    "(или снять JS-челлендж с эндпоинта /drivers/first-orders)."
                )
            if resp.status_code == 401:
                raise RuntimeError(
                    "TEZ APP: 401 — неверный X-Integration-Token (для api.tezapp.org "
                    "подходит только prod-токен)"
                )
            if resp.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                last_error = f"HTTP {resp.status_code}"
                time.sleep(RETRY_PAUSE * attempt)
                continue

            snippet = resp.text[:200].replace("\n", " ").strip()
            raise RuntimeError(f"TEZ APP HTTP {resp.status_code}: {snippet}")

        raise RuntimeError(f"TEZ APP first-orders: не удалось получить ответ ({last_error})")

    def fetch_first_orders(self, phones, full_names=None, progress=None):
        """Даты первых заказов по списку телефонов.

        phones — любые формы записи номера, нормализуются внутри.
        full_names — необязательный dict phone_norm -> ФИО (API его не проверяет,
        но с ним удобнее читать сырые ответы при разборе спорных случаев).

        Возвращает dict phone_norm -> datetime (Алматы) либо None. Невалидные
        номера в результат не попадают — их отдаёт отдельным списком свойство
        `.last_invalid`.
        """
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
        total = len(normalized)
        for start in range(0, total, MAX_BATCH_SIZE):
            chunk = normalized[start:start + MAX_BATCH_SIZE]
            drivers = [
                {"full_name": names.get(p) or "-", "phone": to_e164(p)}
                for p in chunk
            ]
            rows = self._post_batch(drivers)
            for row in rows:
                norm = normalize_kz_phone(row.get("phone"))
                if norm:
                    out[norm] = parse_first_order_at(row.get("first_order_at"))
            # Номера, которых почему-то не оказалось в ответе, тоже помечаем
            # проверенными — иначе они будут переспрашиваться каждую ночь.
            for p in chunk:
                out.setdefault(p, None)

            if progress:
                progress(min(start + len(chunk), total), total)
            if start + MAX_BATCH_SIZE < total:
                time.sleep(BATCH_PAUSE)
        return out


def _main():
    parser = argparse.ArgumentParser(description="Проверка TEZ APP /drivers/first-orders")
    parser.add_argument("--phones", required=True, help="номера через запятую")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = get_config()
    if not api_ready(cfg):
        raise SystemExit("TEZ_DRIVERS_API_TOKEN не задан (env или .env.codex.local)")

    client = TezFirstOrdersClient.from_config(cfg)
    result = client.fetch_first_orders([p for p in args.phones.split(",") if p.strip()])
    for phone, dt in sorted(result.items(), key=lambda kv: (kv[1] is None, kv[1] or 0)):
        print(f"{phone}\t{dt.isoformat() if dt else '—'}")
    if client.last_invalid:
        print("невалидные номера:", ", ".join(str(x) for x in client.last_invalid))


if __name__ == "__main__":
    _main()
