"""Ежедневная авто-выгрузка статусов операторов TEZ из Binotel.

Логинится в панель Binotel (my.binotel.kz) под админ-учёткой из env, тянет
историю статусов сотрудников (модуль `analyticsEmployeesOnTimeline`) и собирает
CSV в формате выгрузки TEZ:

    internal number;employee name;started at;stopped at;seconds in status;status

…который затем скармливается существующему парсеру TEZ
(`_status_import_parse_tez_csv`) → авто-пересчёт часов.

Источник реверс-инжинирился по панели Binotel:
  - логин: POST {BINOTEL_URL}/  с полями logining[email] / logining[password]
           (сессия в cookie PHPSESSID, без капчи/2FA);
  - данные: GET {BINOTEL_URL}/main/?module=analyticsEmployeesOnTimeline&mbav=1
            &startDate=DD.MM.YYYY&stopDate=DD.MM.YYYY  → JSON;
  - связка: internal number → email (listOfEmployeesByInternalNumber)
            → employeeID (listOfEmployees) → сегменты (listOfEmployeesPresenceStates);
  - коды статусов: 0=active, 1=work in crm, 3=break in work, 4=inactive;
  - время: unix → Asia/Almaty (UTC+5), формат «HH:MM DD-MM-YYYY».

Вторая задача модуля (он владеет сессией панели): отдавать сторону завершения
разговора — «кто положил трубку». Публичный Binotel API 4.0 её не отдаёт, кабинет
отдаёт; подробности и ссылки — у констант CDRS_* ниже и в fetch_call_end_parties.

ENV (.env.codex.local или окружение):
    BINOTEL_URL=https://my.binotel.kz
    BINOTEL_LOGIN=<email админа панели>
    BINOTEL_PASSWORD=<пароль>
    TEZ_STATUS_TZ=Asia/Almaty          # опционально
    TEZ_STATUS_DAYS_BACK=1             # опционально: тянуть [сегодня-N … сегодня]

Режимы:
  - в приложении: bot_schedule2 регистрирует ежедневный job, который вызывает
    run_sync(importer), где importer парсит CSV и пишет в БД;
  - автономно (для теста): python tez_status_sync.py --start 02.06.2026
    --stop 02.06.2026 --out statuses.csv   (только выгрузка CSV, без БД).
"""

import argparse
import csv
import io
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import requests

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


DEFAULT_TZ = "Asia/Almaty"
# Код статуса Binotel (presenceState) → текст статуса выгрузки TEZ.
PRESENCE_STATE_CODE_TO_STATUS = {
    0: "active",
    1: "work in crm",
    3: "break in work",
    4: "inactive",
}
CSV_HEADER = "internal number;employee name;started at;stopped at;seconds in status;status"
TIMELINE_MODULE_PATH = "/main/?module=analyticsEmployeesOnTimeline&mbav=1"
HTTP_TIMEOUT = 40

# Журнал звонков кабинета — единственный источник, где Binotel отдаёт сторону
# завершения разговора («кто положил трубку»). Публичный API 4.0 её НЕ отдаёт: поле
# whoHungUp в ответе есть, но пустое во всех методах раздела STATS (проверено 31.08.2026
# на 3 700+ звонках в list-of-calls-for-period, incoming/outgoing-calls-for-period,
# list-of-calls-per-day, all-incoming/outgoing-calls-since, list-of-calls-by-internal-
# number-for-period, recent-calls-by-internal-number и поштучном call-details), а в
# документации developers.binotel.ua этого поля нет вовсе. Кабинет то же поле заполняет
# значениями 'internalNumber' (положил сотрудник) и 'externalNumber' (положил клиент).
# Берём CSV-экспортом: он отдаёт весь период одним запросом, тогда как JSON-ручка того
# же модуля листается постранично через POST lastCdrID.
CDRS_EXPORT_MODULE_PATH = "/main/?module=cdrs&action=export2csv&mbav=1"
CDRS_CSV_CALL_ID_COLUMN = "general call id"
CDRS_CSV_END_PARTY_COLUMN = "who hung up"
# Модуль отдаёт ОДНО направление за запрос, и по умолчанию только входящие, поэтому
# исходящие приходится запрашивать вторым запросом (без этого терялось ~80% звонков).
CDRS_CALL_DIRECTIONS = ("internal", "external")
# Экспорт месяца сервер собирает десятки секунд — общего HTTP_TIMEOUT здесь не хватает.
CDRS_EXPORT_TIMEOUT = 300


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

    base_url = (pick("BINOTEL_URL", "https://my.binotel.kz") or "").rstrip("/")
    return {
        "base_url": base_url,
        "login": pick("BINOTEL_LOGIN"),
        "password": pick("BINOTEL_PASSWORD"),
        "tz": pick("TEZ_STATUS_TZ", DEFAULT_TZ),
        "days_back": int(pick("TEZ_STATUS_DAYS_BACK", "1") or "1"),
    }


def _tzinfo(tz_name):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name or DEFAULT_TZ)
        except Exception:
            pass
    # Фоллбэк: Asia/Almaty без перехода на летнее время — фиксированный UTC+5.
    from datetime import timezone
    return timezone(timedelta(hours=5))


def _cdrs_date(value):
    """Дата в формате журнала звонков кабинета — DD-MM-YYYY.

    Таймлайн статусов принимает DD.MM.YYYY, а модуль cdrs — DD-MM-YYYY, поэтому
    приводим здесь и не заставляем вызывающего помнить про разные разделители.
    Принимаем date/datetime и строки в трёх привычных проекту форматах."""
    if hasattr(value, "strftime"):
        return value.strftime("%d-%m-%Y")
    text = str(value or "").strip()
    for fmt in ("%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    raise ValueError(f"Не разобрал дату для журнала звонков Binotel: {value!r}")


class BinotelClient:
    """Минимальный клиент панели Binotel: логин + выгрузка истории статусов."""

    def __init__(self, base_url, login, password):
        if not base_url or not login or not password:
            raise ValueError("BINOTEL_URL/BINOTEL_LOGIN/BINOTEL_PASSWORD не заданы")
        self.base_url = base_url.rstrip("/")
        self.login = login
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        })
        self._logged_in = False

    def authenticate(self):
        # Прогреваем сессию (cookie), затем постим форму логина.
        self.session.get(self.base_url + "/", timeout=HTTP_TIMEOUT)
        resp = self.session.post(
            self.base_url + "/",
            data={"logining[email]": self.login, "logining[password]": self.password},
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
        )
        body = resp.text or ""
        # Признак успеха: ушли с формы логина (редирект в /f/pbx/...).
        if "logining[email]" in body and "/f/pbx/" not in (resp.url or ""):
            # Причину кабинет пишет в редиректе: /?action=badPassword&email=...
            # Её стоит донести дословно: «пароль не тот» и «нет такого
            # сотрудника» руководитель чинит по-разному, а одинаковое
            # сообщение на оба случая выглядит как поломка автоопределения.
            marker = re.search(r"action=([A-Za-z]+)", resp.url or "")
            reason = marker.group(1) if marker else ""
            if reason == "badPassword":
                raise RuntimeError(
                    "Binotel: логин принят, а пароль кабинета не подошёл")
            raise RuntimeError(
                "Binotel: вход не выполнен (проверьте логин/пароль)"
                + (" [%s]" % reason if reason else ""))
        self._logged_in = True
        return self

    def fetch_timeline(self, start_ddmmyyyy, stop_ddmmyyyy):
        if not self._logged_in:
            self.authenticate()
        url = (
            f"{self.base_url}{TIMELINE_MODULE_PATH}"
            f"&startDate={start_ddmmyyyy}&stopDate={stop_ddmmyyyy}"
        )
        resp = self.session.get(
            url, timeout=HTTP_TIMEOUT, headers={"X-Requested-With": "XMLHttpRequest"}
        )
        resp.raise_for_status()
        payload = resp.json()
        if str(payload.get("status", "")).lower() not in ("", "success", "ok", "1", "true"):
            # Binotel обычно возвращает status:'success'; не валим жёстко, но логируем.
            logging.getLogger(__name__).warning(
                "Binotel timeline status=%r", payload.get("status")
            )
        return payload

    def fetch_call_end_parties(self, start, stop):
        """{generalCallID: whoHungUp} за период — по обоим направлениям звонков.

        Значения отдаём как есть ('internalNumber' / 'externalNumber' / ''), без
        нормализации: её делает вызывающая сторона тем же резолвером, что и для
        публичного API (tez_binotel_calls.normalize_call_end_party). Пустое значение
        сохраняем — по нему видно, что звонок в кабинете есть, но стороны у него нет
        (несостоявшиеся CANCEL/BUSY, где разговора не было)."""
        if not self._logged_in:
            self.authenticate()
        start_date = _cdrs_date(start)
        stop_date = _cdrs_date(stop)
        parties = {}
        for direction in CDRS_CALL_DIRECTIONS:
            url = (
                f"{self.base_url}{CDRS_EXPORT_MODULE_PATH}"
                f"&callType={direction}&startDate={start_date}&stopDate={stop_date}"
            )
            resp = self.session.get(
                url,
                timeout=CDRS_EXPORT_TIMEOUT,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            resp.raise_for_status()
            # BOM в начале файла: читаем через utf-8-sig, иначе первый заголовок
            # приезжает с невидимым префиксом и колонка не находится.
            text = resp.content.decode("utf-8-sig", "replace")
            for row in csv.DictReader(io.StringIO(text), delimiter=";"):
                call_id = str(row.get(CDRS_CSV_CALL_ID_COLUMN) or "").strip()
                if not call_id:
                    continue
                value = str(row.get(CDRS_CSV_END_PARTY_COLUMN) or "").strip()
                # Заполненное значение приоритетнее пустого: звонок приходит только в
                # одном направлении, но подстраховываемся от дублей между выгрузками.
                if value or call_id not in parties:
                    parties[call_id] = value
        return parties


def build_tez_status_csv(timeline_payload, tz_name=DEFAULT_TZ):
    """Собирает CSV формата TEZ из JSON `analyticsEmployeesOnTimeline`."""
    page = (timeline_payload or {}).get("pageData", {}) or {}
    by_internal = page.get("listOfEmployeesByInternalNumber", {}) or {}
    employees = page.get("listOfEmployees", {}) or {}
    presence = page.get("listOfEmployeesPresenceStates", {}) or {}
    tz = _tzinfo(tz_name)

    def fmt_ts(value):
        try:
            return datetime.fromtimestamp(int(value), tz).strftime("%H:%M %d-%m-%Y")
        except Exception:
            return ""

    lines = [CSV_HEADER]
    # Стабильный порядок: по внутреннему номеру (как в ручной выгрузке).
    def _num_key(item):
        try:
            return (0, int(item[0]))
        except Exception:
            return (1, str(item[0]))

    for internal_number, email in sorted(by_internal.items(), key=_num_key):
        record = employees.get(email) or {}
        name = str(record.get("name") or "").strip()
        employee_id = str(record.get("employeeID") or "").strip()
        segments = presence.get(employee_id) or []
        for seg in segments:
            try:
                code = int(seg.get("presenceState"))
            except Exception:
                continue
            status = PRESENCE_STATE_CODE_TO_STATUS.get(code)
            if status is None:
                continue
            started = fmt_ts(seg.get("startedAt"))
            stopped = fmt_ts(seg.get("stoppedAt"))
            if not started or not stopped:
                continue
            seconds = int(seg.get("timeInState") or 0)
            lines.append(
                f"{internal_number};{name};{started};{stopped};{seconds};{status}"
            )
    return "\n".join(lines) + "\n"


def default_date_range(days_back, tz_name=DEFAULT_TZ):
    """Диапазон [сегодня-days_back … сегодня] в DD.MM.YYYY (часовой пояс панели)."""
    tz = _tzinfo(tz_name)
    today = datetime.now(tz).date()
    start = today - timedelta(days=max(0, int(days_back or 0)))
    fmt = lambda d: d.strftime("%d.%m.%Y")
    return fmt(start), fmt(today)


def fetch_status_csv(start=None, stop=None, config=None):
    """Логин в Binotel + выгрузка + сборка CSV (формат TEZ). Возвращает текст CSV."""
    cfg = config or get_config()
    if not start or not stop:
        start, stop = default_date_range(cfg["days_back"], cfg["tz"])
    client = BinotelClient(cfg["base_url"], cfg["login"], cfg["password"]).authenticate()
    payload = client.fetch_timeline(start, stop)
    return build_tez_status_csv(payload, cfg["tz"]), (start, stop)


def fetch_call_end_parties(start, stop, config=None):
    """Сторона завершения разговора из кабинета Binotel за период [start … stop].

    Возвращает {generalCallID: 'internalNumber'|'externalNumber'|''}. Без логина в
    панель источника нет — тогда отдаём пустой словарь, а не падаем: сторона
    завершения звонка не должна ломать наполнение пула оценок."""
    cfg = config or get_config()
    if not cfg.get("login") or not cfg.get("password"):
        logging.getLogger(__name__).warning(
            "Binotel: BINOTEL_LOGIN/PASSWORD не заданы — сторона завершения недоступна"
        )
        return {}
    client = BinotelClient(cfg["base_url"], cfg["login"], cfg["password"]).authenticate()
    return client.fetch_call_end_parties(start, stop)


def run_sync(importer, start=None, stop=None, config=None, logger=None):
    """Тянет статусы из Binotel и передаёт CSV в `importer(csv_text)`.

    `importer` — колбэк (его даёт bot_schedule2), который парсит CSV парсером TEZ
    и сохраняет в БД. Возвращает то, что вернул importer (сводка импорта).
    """
    log = logger or logging.getLogger(__name__)
    cfg = config or get_config()
    if not cfg.get("login") or not cfg.get("password"):
        log.warning("TEZ status sync: BINOTEL_LOGIN/PASSWORD не заданы — пропуск")
        return {"skipped": True, "reason": "no_credentials"}
    csv_text, (start, stop) = fetch_status_csv(start, stop, cfg)
    rows = max(0, csv_text.count("\n") - 1)
    log.info("TEZ status sync: Binotel %s..%s, строк CSV=%d", start, stop, rows)
    summary = importer(csv_text)
    log.info("TEZ status sync: импорт завершён: %s", summary)
    return summary


def register_daily_job(scheduler, importer, hour=1, minute=0, tz_name=DEFAULT_TZ,
                       job_id="tez_status_sync_daily"):
    """Регистрирует ежедневный job в переданном APScheduler-планировщике."""
    try:
        from apscheduler.triggers.cron import CronTrigger
        trigger = CronTrigger(hour=hour, minute=minute, timezone=_tzinfo(tz_name))
    except Exception:
        from apscheduler.triggers.cron import CronTrigger
        trigger = CronTrigger(hour=hour, minute=minute)
    scheduler.add_job(
        lambda: run_sync(importer),
        trigger,
        id=job_id,
        misfire_grace_time=3600,
        max_instances=1,
        coalesce=True,
    )


def _main():
    parser = argparse.ArgumentParser(description="Выгрузка статусов TEZ из Binotel (тест)")
    parser.add_argument("--start", help="DD.MM.YYYY")
    parser.add_argument("--stop", help="DD.MM.YYYY")
    parser.add_argument("--out", help="Файл для сохранения CSV (иначе stdout)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    csv_text, (start, stop) = fetch_status_csv(args.start, args.stop)
    rows = max(0, csv_text.count("\n") - 1)
    logging.info("Binotel %s..%s → строк CSV: %d", start, stop, rows)
    if args.out:
        Path(args.out).write_text(csv_text, encoding="utf-8-sig")
        logging.info("Сохранено: %s", args.out)
    else:
        try:
            print(csv_text)
        except UnicodeEncodeError:
            import sys
            sys.stdout.buffer.write(csv_text.encode("utf-8", "replace"))


if __name__ == "__main__":
    _main()
