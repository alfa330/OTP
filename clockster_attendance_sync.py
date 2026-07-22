"""Ежедневная авто-выгрузка отметок прихода/ухода из Clockster (отдел продаж).

Clockster — система учёта посещаемости (face-терминал в ЦО): каждая отметка =
{user, datetime, status: 1=приход / 0=уход, source}. Синк тянет отметки за
период и передаёт их importer-колбэку (его даёт bot_schedule2), который матчит
сотрудников по ФИО с операторами отдела продаж и сохраняет СОБЫТИЯ статусов
(приход → «Готов», уход → «Выключен») стандартным путём импорта статусов —
дальше rebuild строит сегменты, а авто-агрегация считает часы, опоздания и
штрафы по сменам графика iCORE.

API: {CLOCKSTER_API_URL}/attendance (Bearer-токен из админки Clockster; дока —
публичная Postman-коллекция «Clockster API»). Ответ пагинирован (links.next),
per_page до 1000; datetime приходит ISO с оффсетом (+05:00 для ЦО).

ENV (.env.codex.local или окружение):
    CLOCKSTER_API_TOKEN=<токен из админки Clockster>
    CLOCKSTER_API_URL=https://api.clockster.com/company/v2   # опционально
    CLOCKSTER_SYNC_DAYS_BACK=2    # опционально: тянуть [сегодня-N … сегодня]

Режимы:
  - в приложении: bot_schedule2 регистрирует ежедневный job, который вызывает
    run_sync(importer), где importer матчит ФИО и пишет события в БД;
  - автономно (для теста): python clockster_attendance_sync.py
    --start 2026-07-20 --stop 2026-07-22   (только выгрузка, без БД).
"""

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

DEFAULT_API_URL = "https://api.clockster.com/company/v2"
# Отметки нормализуем в местное время Казахстана: naive-datetime в этом поясе,
# как хранит операторские статусы вся остальная схема (Asia/Almaty, UTC+5).
LOCAL_TZ = timezone(timedelta(hours=5))
HTTP_TIMEOUT = 40
ATTENDANCE_PER_PAGE = 1000
ATTENDANCE_MAX_PAGES = 30
DEFAULT_DAYS_BACK = 2


def _parse_env_file(path):
    """Простой парсер .env (KEY=VALUE), значения в кавычках — снимаем."""
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

    return {
        "api_url": (pick("CLOCKSTER_API_URL", DEFAULT_API_URL) or "").rstrip("/"),
        "token": pick("CLOCKSTER_API_TOKEN"),
        "days_back": int(pick("CLOCKSTER_SYNC_DAYS_BACK", str(DEFAULT_DAYS_BACK)) or DEFAULT_DAYS_BACK),
    }


class ClocksterApiClient:
    """Минимальный клиент Clockster API: только выгрузка отметок посещаемости."""

    def __init__(self, api_url, token):
        if not api_url or not token:
            raise RuntimeError("CLOCKSTER_API_URL/CLOCKSTER_API_TOKEN не заданы")
        self.api_url = api_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })

    def fetch_attendance(self, date_start, date_end):
        """Все отметки за период [date_start..date_end] (даты YYYY-MM-DD, включительно)."""
        marks = []
        page = 1
        while page <= ATTENDANCE_MAX_PAGES:
            resp = self.session.get(
                f"{self.api_url}/attendance",
                params={
                    "per_page": ATTENDANCE_PER_PAGE,
                    "page": page,
                    "date_start": date_start,
                    "date_end": date_end,
                },
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Clockster /attendance HTTP {resp.status_code}: {resp.text[:300]}")
            body = resp.json() or {}
            marks.extend(body.get("data") or [])
            if not ((body.get("links") or {}).get("next")):
                break
            page += 1
        return marks


def _parse_mark_datetime(value):
    """ISO-строка отметки (с оффсетом) → naive datetime в местном поясе (UTC+5)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(LOCAL_TZ).replace(tzinfo=None)
    return parsed


def build_attendance_rows(marks):
    """Сырые отметки API → строки для importer'а.

    Строка: {employee_name, event_at (naive местное), status (1=приход/0=уход),
    source, clockster_user_id}. Отметки без имени/времени пропускаются;
    сортировка по времени — стабильный порядок событий для rebuild-а сегментов.
    """
    rows = []
    for mark in marks or []:
        if not isinstance(mark, dict):
            continue
        user = mark.get("user") or {}
        name = " ".join(filter(None, [
            str(user.get("last_name") or "").strip(),
            str(user.get("first_name") or "").strip(),
            str(user.get("middle_name") or "").strip(),
        ])).strip()
        event_at = _parse_mark_datetime(mark.get("datetime"))
        if not name or event_at is None:
            continue
        try:
            status = 1 if int(mark.get("status")) == 1 else 0
        except (TypeError, ValueError):
            continue
        rows.append({
            "employee_name": name,
            "event_at": event_at,
            "status": status,
            "source": str(mark.get("source") or "").strip(),
            "clockster_user_id": user.get("id"),
        })
    rows.sort(key=lambda r: (r["employee_name"], r["event_at"]))
    return rows


def default_date_range(days_back=DEFAULT_DAYS_BACK):
    """Диапазон [сегодня-days_back … сегодня] в YYYY-MM-DD (местный пояс)."""
    today = datetime.now(LOCAL_TZ).date()
    start = today - timedelta(days=max(0, int(days_back or 0)))
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def fetch_attendance_rows(date_start=None, date_end=None, config=None):
    """Выгрузка отметок Clockster за период → нормализованные строки."""
    cfg = config or get_config()
    if not date_start or not date_end:
        date_start, date_end = default_date_range(cfg["days_back"])
    client = ClocksterApiClient(cfg["api_url"], cfg["token"])
    marks = client.fetch_attendance(date_start, date_end)
    return build_attendance_rows(marks), (date_start, date_end)


def run_sync(importer, date_start=None, date_end=None, config=None, logger=None):
    """Тянет отметки Clockster и передаёт их в `importer(rows)`.

    `importer` — колбэк (его даёт bot_schedule2), который матчит ФИО с
    операторами отдела продаж и сохраняет события статусов в БД.
    Возвращает то, что вернул importer (сводка импорта).
    """
    log = logger or logging.getLogger(__name__)
    cfg = config or get_config()
    if not cfg.get("token"):
        log.warning("Clockster sync: CLOCKSTER_API_TOKEN не задан — пропуск")
        return {"skipped": True, "reason": "no_credentials"}
    rows, (date_start, date_end) = fetch_attendance_rows(date_start, date_end, cfg)
    log.info("Clockster sync: %s..%s, отметок=%d", date_start, date_end, len(rows))
    summary = importer(rows)
    log.info("Clockster sync: импорт завершён: %s", summary)
    return summary


def _main():
    parser = argparse.ArgumentParser(description="Выгрузка отметок Clockster (тест)")
    parser.add_argument("--start", help="YYYY-MM-DD")
    parser.add_argument("--stop", help="YYYY-MM-DD")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rows, (start, stop) = fetch_attendance_rows(args.start, args.stop)
    logging.info("Clockster %s..%s → отметок: %d", start, stop, len(rows))
    for row in rows:
        line = (f"{row['event_at'].strftime('%Y-%m-%d %H:%M:%S')} "
                f"{'IN ' if row['status'] == 1 else 'OUT'} {row['employee_name']} ({row['source']})")
        try:
            print(line)
        except UnicodeEncodeError:
            import sys
            sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))


if __name__ == "__main__":
    _main()
