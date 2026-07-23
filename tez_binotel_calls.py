"""Клиент Binotel API 4.0 для «Случайного звонка» отдела TEZ.

В отличие от `tez_status_sync.py` (который скрейпит панель my.binotel.kz ради
истории статусов), этот модуль работает с официальным REST API Binotel 4.0
(developers.binotel.ua, «Integration with CRM») по ключу/секрету:

  - база: https://api.binotel.com/api/4.0/
  - метод: POST {base}/<group>/<method>.json, тело JSON с {key, secret, ...params}
  - звонки сотрудника за период:
        stats/list-of-calls-by-internal-number-for-period
        параметры: internalNumber, startTime, stopTime (unix), максимум 7 дней;
  - ссылка на запись разговора (живёт ~15 минут):
        stats/call-record   (параметр callID = generalCallID);
  - ответ: {status:'success', callDetails:{ "<generalCallID>": {...} }}.

Сопоставление звонка с нашим оператором — ПО ИМЕНИ (employeeData.name из ответа),
а не по internalNumber: один и тот же sip со временем закрепляют за разными
операторами (см. ветку матчинга в bot_schedule2._binotel_random_call).

ENV (.env.codex.local или окружение):
    TEZ_BINOTEL_API_KEY=<ключ>
    TEZ_BINOTEL_API_SECRET=<секрет>
    TEZ_BINOTEL_API_URL=https://api.binotel.com/api/4.0   # опционально
    TEZ_BINOTEL_TZ=Asia/Almaty                            # опционально

Проверка вручную (без БД), чтобы сверить имена полей с реальным ответом:
    python tez_binotel_calls.py --internal 907 --start 2026-06-01 --stop 2026-06-03
"""

import argparse
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


log = logging.getLogger(__name__)

DEFAULT_TZ = "Asia/Almaty"
DEFAULT_API_URL = "https://api.binotel.com/api/4.0"
HTTP_TIMEOUT = 40
# Binotel: list-of-calls-by-internal-number-for-period ограничен окном в 7 дней.
MAX_WINDOW_DAYS = 7
# history-by-external-number принимает массив номеров, НО режет ответ по общему
# числу ЗВОНКОВ (~500): при превышении часть номеров молча выпадает из ответа с
# нулём звонков (проверено 2026-07-22: 95 номеров -> 504 звонка, выпало 45; 30
# номеров -> 344 звонка, все на месте). Поэтому шлём маленькими пачками и, если
# ответ подошёл к лимиту, делим пачку пополам — иначе теряются звонки, а с ними
# успешки. Одиночный номер не делим (у лида столько звонков не бывает).
MAX_EXTERNAL_NUMBERS_PER_REQUEST = 25
# Порог, при достижении которого ответ мог быть обрезан -> дробим пачку.
EXTERNAL_NUMBERS_TRUNCATION_GUARD = 400
# callType в ответе Binotel: 0 = входящий, 1 = исходящий.
CALL_TYPE_INCOMING = 0
CALL_TYPE_OUTGOING = 1
# disposition, у которых по документации существует запись разговора.
RECORDED_DISPOSITIONS = {"ANSWER", "ANSWERED", "SUCCESS", "VM-SUCCESS"}
# recordingStatus из ответа Binotel, при котором запись реально доступна.
RECORDED_STATUSES = {"uploaded"}
# Binotel лимитирует частоту запросов (даёт ошибку status='error' «Requests are too
# frequent. You can do this request after N sec.»). Клиент сериализует запросы,
# разносит их во времени и повторяет при этой ошибке, учитывая подсказку сервера.
MIN_REQUEST_INTERVAL = 1.0   # минимальный зазор между запросами к Binotel, сек
RATE_LIMIT_MAX_RETRIES = 3   # сколько раз повторить при «too frequent»
RATE_LIMIT_MAX_WAIT = 12     # потолок ожидания по подсказке сервера, сек
_RATE_LOCK = threading.Lock()
_LAST_REQUEST_AT = [0.0]     # monotonic-время последнего запроса (общий на процесс)


def _too_frequent_wait_seconds(payload):
    """Если Binotel ответил «слишком часто» — вернёт рекомендованную паузу (сек), иначе None."""
    text = f"{payload.get('message') or ''} {payload.get('error') or ''}".lower()
    if 'too frequent' not in text and 'frequent' not in text and 'часто' not in text:
        return None
    m = re.search(r'after\s+(\d+)\s*sec', text)
    return int(m.group(1)) if m else 10


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

    base_url = (pick("TEZ_BINOTEL_API_URL", DEFAULT_API_URL) or DEFAULT_API_URL).rstrip("/")
    return {
        "base_url": base_url,
        "api_key": pick("TEZ_BINOTEL_API_KEY"),
        "api_secret": pick("TEZ_BINOTEL_API_SECRET"),
        "tz": pick("TEZ_BINOTEL_TZ", DEFAULT_TZ),
    }


def api_ready(config=None):
    cfg = config or get_config()
    return bool(cfg.get("api_key")) and bool(cfg.get("api_secret"))


def _tzinfo(tz_name):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name or DEFAULT_TZ)
        except Exception:
            pass
    from datetime import timezone
    return timezone(timedelta(hours=5))  # Asia/Almaty без перехода на летнее время


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


class BinotelApiClient:
    """Минимальный клиент Binotel API 4.0 (key/secret)."""

    def __init__(self, api_key, api_secret, base_url=DEFAULT_API_URL, tz=DEFAULT_TZ, timeout=HTTP_TIMEOUT):
        if not api_key or not api_secret:
            raise ValueError("TEZ_BINOTEL_API_KEY/TEZ_BINOTEL_API_SECRET не заданы")
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = (base_url or DEFAULT_API_URL).rstrip("/")
        self.tz = tz or DEFAULT_TZ
        self.timeout = timeout
        self.session = requests.Session()

    @classmethod
    def from_config(cls, config=None):
        cfg = config or get_config()
        return cls(cfg.get("api_key"), cfg.get("api_secret"), cfg.get("base_url"), cfg.get("tz"))

    def _post(self, endpoint, params):
        """POST {base}/{endpoint}.json с key+secret. Возвращает распарсенный JSON.

        Запросы к Binotel сериализуются и разносятся во времени (общий на процесс
        замок + зазор MIN_REQUEST_INTERVAL), а на ошибку «too frequent» делается
        ретрай с паузой по подсказке сервера — иначе второй запрос подряд (второе
        7-дневное окно, фоновая докачка записи или повторный клик) валится в 502."""
        url = f"{self.base_url}/{endpoint}.json"
        body = dict(params or {})
        body["key"] = self.api_key
        body["secret"] = self.api_secret

        last_rate_err = None
        for attempt in range(1, RATE_LIMIT_MAX_RETRIES + 1):
            # Сериализуем запросы и выдерживаем минимальный зазор между ними.
            with _RATE_LOCK:
                gap = time.monotonic() - _LAST_REQUEST_AT[0]
                if gap < MIN_REQUEST_INTERVAL:
                    time.sleep(MIN_REQUEST_INTERVAL - gap)
                try:
                    resp = self.session.post(url, json=body, timeout=self.timeout)
                finally:
                    _LAST_REQUEST_AT[0] = time.monotonic()

            if resp.status_code != 200:
                raise RuntimeError(f"Binotel HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                payload = resp.json()
            except ValueError:
                raise RuntimeError(f"Binotel: не JSON в ответе {endpoint}: {resp.text[:300]}")
            status = str(payload.get("status", "")).lower()
            if not status or status in ("success", "ok", "1", "true"):
                return payload

            wait = _too_frequent_wait_seconds(payload)
            if wait is not None and attempt < RATE_LIMIT_MAX_RETRIES:
                sleep_s = min(wait, RATE_LIMIT_MAX_WAIT) + 0.4  # пауза вне замка
                last_rate_err = payload.get('message') or payload.get('error') or 'too frequent'
                log.info("Binotel %s: слишком часто, пауза %.1fs и повтор (попытка %d/%d)",
                         endpoint, sleep_s, attempt, RATE_LIMIT_MAX_RETRIES)
                time.sleep(sleep_s)
                continue

            raise RuntimeError(
                f"Binotel {endpoint} status={payload.get('status')!r}: "
                f"{payload.get('message') or payload.get('error') or ''}"[:300]
            )

        raise RuntimeError(f"Binotel {endpoint}: превышен лимит частоты запросов ({last_rate_err})")

    @staticmethod
    def _normalize_call(raw):
        """callDetails-элемент Binotel -> нормализованный dict.

        Имена полей защищены фолбэками (в разных инсталляциях встречаются
        варианты написания), т.к. официальную схему сверяем через CLI.
        """
        if not isinstance(raw, dict):
            return None
        gid = raw.get("generalCallID") or raw.get("generalCallId") or raw.get("callID") or raw.get("id")
        if gid is None:
            return None
        call_type = _to_int(raw.get("callType"), default=-1)
        billsec = _to_int(raw.get("billsec"), default=0)
        waitsec = _to_int(raw.get("waitsec"), default=0)
        start_time = _to_int(raw.get("startTime") or raw.get("startTimeUTC") or raw.get("start_time"), default=0)
        # employeeData несёт имя/почту сотрудника, обслужившего звонок. Один и тот же
        # sip (internalNumber) со временем закрепляют за разными операторами, поэтому
        # сопоставлять звонок с нашим оператором нужно ПО ИМЕНИ, а не по номеру.
        emp = raw.get("employeeData")
        emp_name = ""
        emp_email = ""
        if isinstance(emp, dict):
            emp_name = str(emp.get("name") or emp.get("fullName") or "").strip()
            emp_email = str(emp.get("email") or "").strip()
        return {
            "general_call_id": str(gid),
            "call_type": call_type,
            "billsec": billsec,
            "waitsec": waitsec,
            "start_time": start_time,
            "internal_number": str(raw.get("internalNumber") or raw.get("internalNumbers") or "").strip(),
            "external_number": str(raw.get("externalNumber") or raw.get("clientNumber") or "").strip(),
            "disposition": str(raw.get("disposition") or "").strip().upper(),
            "employee_name": emp_name,
            "employee_email": emp_email,
            "recording_status": str(raw.get("recordingStatus") or "").strip().lower(),
        }

    @staticmethod
    def _extract_call_details(payload):
        details = payload.get("callDetails")
        if isinstance(details, dict):
            return list(details.values())
        if isinstance(details, list):
            return details
        return []

    def list_calls_by_internal_number(self, internal_number, start_ts, stop_ts):
        """Все звонки сотрудника за период. Период бьём на окна <=7 дней."""
        internal_number = str(internal_number).strip()
        if not internal_number:
            return []
        start_ts = int(start_ts)
        stop_ts = int(stop_ts)
        if stop_ts < start_ts:
            start_ts, stop_ts = stop_ts, start_ts
        window = MAX_WINDOW_DAYS * 86400
        out = []
        seen = set()
        cur = start_ts
        while cur <= stop_ts:
            chunk_stop = min(cur + window - 1, stop_ts)
            payload = self._post(
                "stats/list-of-calls-by-internal-number-for-period",
                {"internalNumber": internal_number, "startTime": cur, "stopTime": chunk_stop},
            )
            for raw in self._extract_call_details(payload):
                call = self._normalize_call(raw)
                if not call or call["general_call_id"] in seen:
                    continue
                seen.add(call["general_call_id"])
                out.append(call)
            cur = chunk_stop + 1
        return out

    def list_calls_by_external_numbers(self, phones):
        """Вся история звонков по номерам КЛИЕНТОВ (stats/history-by-external-number).

        В отличие от list_calls_by_internal_number период не задаётся вообще —
        метод отдаёт всю историю по каждому номеру. Именно поэтому для успешек
        ОП не нужно зеркалить весь трафик компании: список интересующих номеров
        известен заранее (это база лидов), и один запрос закрывает сразу пачку.

        Проверено на живом API: 99 номеров -> 677 звонков; 300 номеров метод
        принимает, 1000 отвергает («Request is too large»). Шлём по
        MAX_EXTERNAL_NUMBERS_PER_REQUEST с запасом.
        """
        numbers = [str(p).strip() for p in (phones or []) if str(p or '').strip()]
        if not numbers:
            return []
        out = []
        seen = set()

        def _fetch(chunk):
            """Тянет историю по пачке номеров; если ответ подошёл к лимиту выдачи
            (значит часть номеров могла выпасть) — делит пачку пополам и повторяет,
            пока не станет надёжно. Одиночный номер уже не делим."""
            if not chunk:
                return
            payload = self._post("stats/history-by-external-number", {"externalNumbers": chunk})
            details = self._extract_call_details(payload)
            if len(details) >= EXTERNAL_NUMBERS_TRUNCATION_GUARD and len(chunk) > 1:
                mid = len(chunk) // 2
                _fetch(chunk[:mid])
                _fetch(chunk[mid:])
                return
            for raw in details:
                call = self._normalize_call(raw)
                if not call or call["general_call_id"] in seen:
                    continue
                seen.add(call["general_call_id"])
                out.append(call)

        for start in range(0, len(numbers), MAX_EXTERNAL_NUMBERS_PER_REQUEST):
            _fetch(numbers[start:start + MAX_EXTERNAL_NUMBERS_PER_REQUEST])
        return out

    def get_call_record_url(self, general_call_id):
        """Ссылка на запись разговора (подписанный S3-URL, живёт ~1 час) или None."""
        payload = self._post("stats/call-record", {"generalCallID": str(general_call_id)})
        url = payload.get("url") or payload.get("recordUrl") or payload.get("link")
        if not url and isinstance(payload.get("callDetails"), dict):
            first = next(iter(payload["callDetails"].values()), {})
            if isinstance(first, dict):
                url = first.get("url") or first.get("recordUrl")
        return url or None

    def format_dt(self, unix_ts):
        """unix -> 'dd.mm.YYYY HH:MM:SS' в TZ панели (совместимо с _parse_datetime_raw)."""
        try:
            return datetime.fromtimestamp(int(unix_ts), _tzinfo(self.tz)).strftime("%d.%m.%Y %H:%M:%S")
        except Exception:
            return ""


def _day_bounds_unix(date_from, date_to, tz_name=DEFAULT_TZ):
    """['YYYY-MM-DD', 'YYYY-MM-DD'] -> (start_unix, stop_unix), включительно по дню."""
    tz = _tzinfo(tz_name)
    start = datetime.strptime(str(date_from).strip(), "%Y-%m-%d")
    end = datetime.strptime(str(date_to).strip(), "%Y-%m-%d")
    if end < start:
        start, end = end, start
    start_dt = start.replace(hour=0, minute=0, second=0, tzinfo=tz)
    end_dt = end.replace(hour=23, minute=59, second=59, tzinfo=tz)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def _main():
    parser = argparse.ArgumentParser(description="Проверка Binotel API 4.0 (звонки по internalNumber)")
    parser.add_argument("--internal", help="internalNumber (sip_number оператора)")
    parser.add_argument("--start", help="YYYY-MM-DD")
    parser.add_argument("--stop", help="YYYY-MM-DD")
    parser.add_argument("--record", help="generalCallID: получить ссылку на запись")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = get_config()
    if not api_ready(cfg):
        raise SystemExit("TEZ_BINOTEL_API_KEY/SECRET не заданы (env или .env.codex.local)")
    client = BinotelApiClient.from_config(cfg)

    if args.record:
        print("record url:", client.get_call_record_url(args.record))
        return

    if not (args.internal and args.start and args.stop):
        raise SystemExit("Нужны --internal, --start, --stop (или --record <generalCallID>)")
    start_ts, stop_ts = _day_bounds_unix(args.start, args.stop, cfg["tz"])
    calls = client.list_calls_by_internal_number(args.internal, start_ts, stop_ts)
    logging.info("Получено звонков: %d", len(calls))
    for c in calls[:20]:
        c = dict(c)
        c["dt"] = client.format_dt(c["start_time"])
        print(json.dumps(c, ensure_ascii=False))


if __name__ == "__main__":
    _main()
