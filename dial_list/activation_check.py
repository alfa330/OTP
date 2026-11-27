# -*- coding: utf-8 -*-
"""Проверка водителей «Активации водителей» (профиль 'activation', правила — activation.py): поездки в CRM и
провайдер ЭДО в кабинете Fleet. Источники — activation_sources.py.

Две цели (решение владельца 26.11.2026):
  • поездки: trips_total ≥ trips_goal отдела — водитель добрал, это «цель» лида, как «вышел на линию» у Тез:
    signed_at (момент последнего заказа, не позже проверки), sign_status 'trips'. Поэтому без правок работают
    выход из пула (_POOL_SQL), снятие строк со списков и успешка оператору (SignChecker.close_signed_rows /
    resolve_successes: последний разговор ≥10 с ДО signed_at), этап журнала и конверсия;
  • Sapar: проверка увидела провайдера Sapar у водителя, о котором раньше знали другого провайдера (или что
    провайдера нет), — sapar_at; успешка «перевёл на Sapar» — тому, с кем был последний разговор ≥10 с до
    sapar_at (resolve_sapar_successes). Водитель из обзвона не уходит: ему ещё добирать поездки.

Прогон (run, планировщик раз в 30 минут и загрузка базы), свой поток на одно место — запросы склеиваются:
  1. Поездки — пачками в CRM (/partners/orders, до TRIPS_BATCH ID за запрос): кому пора — не добравшие и не
     исключённые водители обзваниваемого месяца отделов профиля, с ID, проверенные дольше TRIPS_RECHECK_MINUTES
     назад; окно запроса — с первого числа месяца регистрации водителя (раньше заказов у аккаунта нет).
  2. Провайдер — карточками Fleet по одной (~22 в минуту на учётку), не больше PROVIDER_RUN_MAX и в пределах
     бюджета: сначала те, с кем говорили после последней проверки провайдера (обещал перейти — проверить, не
     перешёл ли), потом ни разу не проверенные карточкой, потом проверенные дольше PROVIDER_RECHECK_HOURS
     назад. Кто уже на Sapar — не спрашиваем: провайдера меняют только с «Бумажного» на настоящего, обратно —
     нет (fleet_edm/engine.py). Парк для карточки — из CRM (driver-info), один раз.
Проверка «сейчас» (check_now) — её ждёт человек: перед выдачей пачки (поездки кандидатов, одним запросом),
перед звонком (поездки и, если провайдер давно не проверяли, провайдер этого водителя) и кнопкой руководителя
(всё сразу). Запрос — в своём потоке, ждём не дольше бюджета: не успели — выдаём и звоним как раньше
(fail-open), опоздавший ответ всё равно запишется.
"""
import json
import logging
import os
import queue
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from psycopg2.extras import execute_values

from test_numbers import keys as test_keys
from . import activation, signing
from .activation_sources import CrmSource, FleetSource, SourceError
from .sign_check import ANSWERED_SQL, IN_FLIGHT_MAX_AGE_MINUTES, IN_FLIGHT_STATES, SignChecker

log = logging.getLogger(__name__)

PROFILE = activation.PROFILE_ACTIVATION
PERIOD_TZ = signing.PERIOD_TZ
# Заказов в базе CRM нет раньше этого дня: окно запроса водителя без даты регистрации начинается с него.
CRM_ORDERS_SINCE = date(2026, 4, 17)
TRIPS_RECHECK_MINUTES = 60
TRIPS_BATCH = 200
TRIPS_BATCH_PAUSE_SEC = 1.0
TRIPS_MAX_ERRORS_IN_ROW = 3
PROVIDER_RECHECK_HOURS = 24
# После разговора — провайдера проверяем не раньше, чем через столько минут (дать водителю время перейти).
PROVIDER_AFTER_TALK_MIN = 15
PROVIDER_RUN_MAX = 400
PROVIDER_MAX_ERRORS_IN_ROW = 5
RUN_BUDGET_SEC = 25 * 60
RUN_MAX_LEADS = 20000
WRITE_CHUNK = 200
# Проверка «сейчас»: после сбоя столько секунд её не пробуем; одновременно — не больше стольких.
NOW_BACKOFF_SEC = 30
NOW_CONCURRENT = 4
# Перед звонком провайдера освежаем, если ему больше стольких часов.
PROVIDER_CALL_FRESH_HOURS = 6
CRM_NOW_TIMEOUT_SEC = 5
# Подвисшие решения по успешкам (шёл звонок, начатый до цели) — добираем за прогон не больше.
PENDING_LIMIT = 5000
# Аренда прогона (dial_list_check_leases): имя и запас сверх бюджета прогона.
LEASE_NAME = "activation"
LEASE_SLACK_SEC = 5 * 60


def _utcnow():
    return datetime.now(timezone.utc)


def _moment(value):
    """Время из ответа CRM («2026-11-23 15:55:01», Алматы без пояса) → datetime с поясом или None."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=PERIOD_TZ)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=PERIOD_TZ)


def window_start(registered_at):
    """С какого дня спрашивать заказы аккаунта: первое число месяца регистрации, не раньше CRM_ORDERS_SINCE."""
    if not isinstance(registered_at, datetime):
        return CRM_ORDERS_SINCE
    local = registered_at.astimezone(PERIOD_TZ).date()
    return max(CRM_ORDERS_SINCE, local.replace(day=1))


def reached_moment(last_order_at, now):
    """signed_at добравшего: последний заказ, но не позже момента проверки; заказа нет — сама проверка."""
    moment = _moment(last_order_at)
    return now if moment is None or moment > now else moment


class ActivationChecker:
    """Проверки «Активации водителей» для всего раздела. crm / fleet — источники (activation_sources; в тестах —
    подделки); successes — у кого брать resolve_successes и close_signed_rows (по умолчанию SignChecker)."""

    def __init__(self, db, crm=None, fleet=None, successes=None, sleep=time.sleep):
        self.db = db
        self.crm = crm if crm is not None else CrmSource()
        self.fleet = fleet if fleet is not None else FleetSource(db)
        self._successes = successes
        self._sleep = sleep
        self._executor = None
        self._lock = threading.Lock()
        self._running = False
        self._again = False
        self.last_run = None
        self._now_backoff_until = 0.0
        self._now_slots = threading.BoundedSemaphore(NOW_CONCURRENT)
        # Кто держит аренду прогона: процесс (хост и pid) и этот экземпляр.
        self._owner = f"{socket.gethostname()[:40]}:{os.getpid()}:{id(self) % 100000}"

    def configured(self):
        try:
            return bool(self.crm.configured())
        except Exception:
            log.exception("dial_list: активация — не удалось узнать, настроена ли CRM")
            return False

    def fleet_configured(self):
        try:
            return bool(self.fleet.configured())
        except Exception:
            log.exception("dial_list: активация — не удалось узнать, настроен ли кабинет Fleet")
            return False

    @property
    def running(self):
        return self._running

    def _outcomes(self):
        if self._successes is None:
            self._successes = SignChecker(self.db)
        return self._successes

    # ------------------------------------------------------------ очередь
    def request(self, reason=""):
        """Прогон в своём потоке, не дожидаясь его; идёт прогон — после него ещё раз."""
        with self._lock:
            if self._running:
                self._again = True
                return False
            self._running = True
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dial-activation")
        self._executor.submit(self._loop, reason)
        return True

    def _loop(self, reason):
        while True:
            try:
                self.run(reason=reason)
            except Exception:
                log.exception("dial_list: проверка активации упала (%s)", reason)
            with self._lock:
                if not self._again:
                    self._running = False
                    return
                self._again = False
            reason = "повтор после прогона"

    # ------------------------------------------------------------ кому пора
    # Водители, которых проверяет прогон: не добравшие и не исключённые, с ID, обзваниваемого месяца отделов
    # профиля; номера реестра тестовых не спрашиваем.
    _BASE_WHERE_SQL = """
          s.profile = %(profile)s
          AND l.signed_at IS NULL AND l.status <> 'excluded' AND l.driver_id <> ''
          AND l.period = COALESCE(s.active_period, %(current)s)
          AND """ + test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL

    _TRIPS_DUE_SQL = """
        SELECT l.id, l.driver_id, l.account_registered_at
        FROM dial_list_leads l
        JOIN dial_list_department_settings s ON s.department_id = l.department_id
        WHERE """ + _BASE_WHERE_SQL + """
          AND (l.trip_checked_at IS NULL OR l.trip_checked_at < %(due_before)s)
        ORDER BY l.trip_checked_at ASC NULLS FIRST, l.created_at ASC
        LIMIT %(limit)s
    """

    # Очередь провайдера: 0 — говорили после последней проверки (не раньше PROVIDER_AFTER_TALK_MIN назад),
    # 1 — карточкой ещё не проверяли (известно только из файла), 2 — проверяли давно. Кто уже на Sapar — мимо;
    # не узнали по самому водителю (нет в CRM, нет в парке) — не чаще раза в сутки.
    _PROVIDER_DUE_SQL = """
        SELECT l.id, l.driver_id, l.fleet_park_id, p.prio
        FROM dial_list_leads l
        JOIN dial_list_department_settings s ON s.department_id = l.department_id
        CROSS JOIN LATERAL (SELECT CASE
            WHEN EXISTS (
                SELECT 1 FROM dial_list_assignments a
                JOIN dial_list_attempts t ON t.assignment_id = a.id
                WHERE a.lead_id = l.id AND t.state = 'finished' AND NOT t.cancelled
                  AND UPPER(t.disposition) IN %(answered)s AND t.billsec >= %(min_sec)s
                  AND t.requested_at < %(talk_before)s
                  AND t.requested_at > COALESCE(CASE WHEN l.edm_source IN ('card', 'none') THEN l.edm_checked_at END,
                                                '-infinity'::timestamptz))
                THEN 0
            WHEN l.edm_source NOT IN ('card', 'none') THEN 1
            WHEN l.edm_checked_at IS NULL OR l.edm_checked_at < %(recheck_before)s THEN 2
            END AS prio) p
        WHERE """ + _BASE_WHERE_SQL + """
          AND p.prio IS NOT NULL
          AND lower(l.edm_provider) <> lower(%(sapar)s)
          AND NOT (l.edm_error <> '' AND l.edm_checked_at IS NOT NULL AND l.edm_checked_at > %(error_before)s)
        ORDER BY p.prio, l.edm_checked_at ASC NULLS FIRST, l.created_at ASC
        LIMIT %(limit)s
    """

    def _params(self, now):
        today = now.astimezone(PERIOD_TZ).date()
        return {"profile": PROFILE, "current": signing.month_start(today), "sapar": activation.SAPAR,
                "answered": ANSWERED_SQL, "min_sec": signing.SUCCESS_MIN_BILLSEC}

    # ------------------------------------------------------------ аренда прогона
    # Процессов на проде несколько, планировщик — в каждом: прогон идёт только у взявшего аренду (её срок —
    # бюджет прогона с запасом; кончился — аренда снова свободна, даже если процесс умер посреди прогона).
    _LEASE_SQL = """
        INSERT INTO dial_list_check_leases (name, owner, until)
        VALUES (%(name)s, %(owner)s, %(until)s)
        ON CONFLICT (name) DO UPDATE SET owner = EXCLUDED.owner, until = EXCLUDED.until
        WHERE dial_list_check_leases.until < %(now)s OR dial_list_check_leases.owner = EXCLUDED.owner
        RETURNING owner
    """

    def _take_lease(self, now, budget_sec):
        try:
            with self.db._get_cursor() as cur:
                cur.execute(self._LEASE_SQL, {"name": LEASE_NAME, "owner": self._owner, "now": now,
                                              "until": now + timedelta(seconds=budget_sec + LEASE_SLACK_SEC)})
                return cur.fetchone() is not None
        except Exception:
            # Таблицы аренды нет (схема не догнала) — прогон всё равно нужен: идём без аренды.
            log.exception("dial_list: активация — аренда прогона не взята, идём без неё")
            return True

    def _drop_lease(self, summary=None):
        try:
            with self.db._get_cursor() as cur:
                cur.execute("""
                    UPDATE dial_list_check_leases
                    SET until = CURRENT_TIMESTAMP, last_summary = COALESCE(%s::jsonb, last_summary)
                    WHERE name = %s AND owner = %s
                """, (json.dumps(summary, ensure_ascii=False) if summary else None, LEASE_NAME, self._owner))
        except Exception:
            log.exception("dial_list: активация — аренда прогона не снята (истечёт сама)")

    def _lease_state(self):
        """(идёт ли прогон в каком-нибудь процессе, сводка последнего прогона) — из строки аренды."""
        try:
            with self.db._get_cursor() as cur:
                cur.execute("SELECT until > CURRENT_TIMESTAMP, last_summary FROM dial_list_check_leases WHERE name = %s",
                            (LEASE_NAME,))
                row = cur.fetchone()
        except Exception:
            log.exception("dial_list: активация — строка аренды прогона не прочитана")
            return None, None
        if not row:
            return False, None
        last = row[1]
        if isinstance(last, str):
            try:
                last = json.loads(last)
            except ValueError:
                last = None
        return bool(row[0]), last if isinstance(last, dict) else None

    # ------------------------------------------------------------ прогон
    def run(self, reason="", now=None, budget_sec=RUN_BUDGET_SEC):
        """Проверить всех, кому пора: поездки, затем провайдер. Возвращает сводку (она же last_run)."""
        summary = {"reason": reason, "at": None, "trips_checked": 0, "reached": 0, "trips_errors": 0,
                   "provider_checked": 0, "sapar": 0, "provider_errors": 0, "successes": 0, "sapar_successes": 0,
                   "closed_rows": 0, "stopped": "", "provider_stopped": ""}
        if not self.configured():
            log.warning("dial_list: проверка активации пропущена — CRM не настроена (CRM_INTEGRATION_TOKEN)")
            summary["stopped"] = "not_configured"
            self.last_run = summary
            return summary
        now = now or _utcnow()
        summary["at"] = now.isoformat()
        if not self._take_lease(now, budget_sec):
            # Прогон идёт в другом процессе — этот не нужен (last_run не трогаем: сводка — о настоящем прогоне).
            summary["stopped"] = "other_process"
            return summary
        try:
            self._run_leased(reason, now, budget_sec, summary)
        finally:
            self._drop_lease(summary)
        return summary

    def _run_leased(self, reason, now, budget_sec, summary):
        started = time.monotonic()
        reached = self._run_trips(now, summary, started, budget_sec)
        outcomes = self._outcomes()
        if reached:
            summary["closed_rows"] = outcomes.close_signed_rows(reached)
        left = budget_sec - (time.monotonic() - started)
        if left > 0 and not summary["stopped"]:
            self._run_providers(now, summary, time.monotonic(), left)
        summary["successes"] = self._resolve_pending(now)
        summary["sapar_successes"] = self.resolve_sapar_successes(now)
        summary["elapsed_sec"] = round(time.monotonic() - started, 1)
        if summary["trips_checked"] or summary["provider_checked"] or summary["stopped"]:
            log.info("dial_list: проверка активации (%s): поездки %d (добрали %d, ошибок %d), провайдер %d (на Sapar "
                     "%d, ошибок %d%s), успешек %d, за Sapar %d, снято строк %d%s", reason, summary["trips_checked"],
                     summary["reached"], summary["trips_errors"], summary["provider_checked"], summary["sapar"],
                     summary["provider_errors"],
                     f", {summary['provider_stopped']}" if summary["provider_stopped"] else "",
                     summary["successes"], summary["sapar_successes"], summary["closed_rows"],
                     f", остановлена: {summary['stopped']}" if summary["stopped"] else "")
        self.last_run = summary
        return summary

    def _run_trips(self, now, summary, started, budget_sec):
        params = dict(self._params(now), due_before=now - timedelta(minutes=TRIPS_RECHECK_MINUTES),
                      limit=RUN_MAX_LEADS)
        with self.db._get_cursor() as cur:
            cur.execute(self._TRIPS_DUE_SQL, params)
            due = cur.fetchall()
        # Окно запроса — с месяца регистрации: водители одного окна — в один запрос.
        windows = {}
        for lead_id, driver_id, registered_at in due:
            windows.setdefault(window_start(registered_at), []).append((str(lead_id), str(driver_id)))
        today = now.astimezone(PERIOD_TZ).date()
        reached, errors_in_row, first = [], 0, True
        for since in sorted(windows):
            leads = windows[since]
            for start in range(0, len(leads), TRIPS_BATCH):
                if time.monotonic() - started > budget_sec:
                    summary["stopped"] = "budget"
                    return reached
                if not first and TRIPS_BATCH_PAUSE_SEC:
                    self._sleep(TRIPS_BATCH_PAUSE_SEC)
                first = False
                chunk = leads[start:start + TRIPS_BATCH]
                try:
                    answer = self.crm.orders([d for _, d in chunk], since, today)
                except Exception as exc:
                    summary["trips_errors"] += len(chunk)
                    errors_in_row += 1
                    log.warning("dial_list: активация — CRM не ответила по пачке %d водителей: %s", len(chunk),
                                str(exc)[:300])
                    self._write_trip_error([lead_id for lead_id, _ in chunk], str(exc))
                    if errors_in_row >= TRIPS_MAX_ERRORS_IN_ROW:
                        summary["stopped"] = "crm_unavailable"
                        return reached
                    continue
                errors_in_row = 0
                values, missing = [], []
                for lead_id, driver_id in chunk:
                    info = (answer or {}).get(driver_id)
                    if info is None:
                        missing.append(lead_id)
                        continue
                    values.append((lead_id, info["count"], reached_moment(info.get("last_order_at"), now), now))
                if missing:
                    self._write_trip_error(missing, "CRM не знает этого водителя")
                    summary["trips_errors"] += len(missing)
                newly = self._write_trips(values)
                reached.extend(newly)
                summary["trips_checked"] += len(values)
                summary["reached"] += len(newly)
        return reached

    def _run_providers(self, now, summary, started, budget_sec):
        if not self.fleet_configured():
            summary["provider_stopped"] = "fleet_not_configured"
            return
        params = dict(self._params(now), limit=PROVIDER_RUN_MAX,
                      talk_before=now - timedelta(minutes=PROVIDER_AFTER_TALK_MIN),
                      recheck_before=now - timedelta(hours=PROVIDER_RECHECK_HOURS),
                      # Ошибка карточки (нет в парке, CRM не знает) — не чаще раза в сутки на водителя.
                      error_before=now - timedelta(hours=PROVIDER_RECHECK_HOURS))
        with self.db._get_cursor() as cur:
            cur.execute(self._PROVIDER_DUE_SQL, params)
            due = cur.fetchall()
        errors_in_row = 0
        for lead_id, driver_id, park_id, _prio in due:
            if time.monotonic() - started > budget_sec:
                summary["provider_stopped"] = "budget"
                return
            result = self._provider_one(str(lead_id), str(driver_id), park_id or "", now)
            if result == "fleet_down":
                summary["provider_errors"] += 1
                summary["provider_stopped"] = "fleet_unavailable"
                return
            if result == "error":
                summary["provider_errors"] += 1
                errors_in_row += 1
                if errors_in_row >= PROVIDER_MAX_ERRORS_IN_ROW:
                    summary["provider_stopped"] = "errors"
                    return
                continue
            errors_in_row = 0
            summary["provider_checked"] += 1
            if result == "sapar":
                summary["sapar"] += 1

    def _provider_one(self, lead_id, driver_id, park_id, now, crm_timeout=None):
        """Провайдер одного водителя: парк (из CRM, если его нет) и карточка Fleet. Пишет итог в лид.
        Возвращает 'sapar' (перешёл на Sapar этой проверкой), 'ok', 'error' (по водителю) или 'fleet_down'."""
        if not park_id:
            try:
                info = self.crm.driver(driver_id, timeout=crm_timeout)
            except Exception as exc:
                self._write_provider_error(lead_id, f"CRM не ответила: {str(exc)[:200]}", now, advance=False)
                return "error"
            if info is None:
                self._write_provider_error(lead_id, "CRM не знает этого водителя", now)
                return "error"
            self._write_driver_info(lead_id, info, now)
            park_id = info.get("park_id") or ""
            if not park_id:
                self._write_provider_error(lead_id, "CRM не знает парк водителя", now)
                return "error"
        try:
            name, found = self.fleet.provider(park_id, driver_id)
        except SourceError as exc:
            self._write_provider_error(lead_id, str(exc), now, advance=False)
            return "fleet_down"
        except Exception as exc:
            self._write_provider_error(lead_id, f"Кабинет Fleet: {str(exc)[:200]}", now, advance=False)
            return "error"
        if not found:
            # В этом парке водителя нет — ушёл в другой: парк спросим у CRM заново в следующий раз.
            self._write_provider_error(lead_id, "В парке водителя нет — парк уточним заново", now, drop_park=True)
            return "error"
        switched = self._write_provider(lead_id, name, now)
        return "sapar" if switched else "ok"

    # ------------------------------------------------------------ проверка «сейчас»
    _NOW_SQL = """
        SELECT l.id, l.driver_id, l.account_registered_at, l.trip_checked_at, l.trip_error,
               l.fleet_park_id, l.edm_source, l.edm_checked_at, l.edm_provider
        FROM dial_list_leads l
        WHERE l.id = ANY(%s::uuid[]) AND l.driver_id <> '' AND l.signed_at IS NULL
          AND """ + test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL + """
    """

    def check_now(self, lead_ids, *, budget_sec, trips_fresh_minutes=None, provider_fresh_hours=None,
                  per_driver=False, now=None):
        """Проверить этих водителей сразу — их ждёт человек. trips_fresh_minutes — поездки свежее не спрашивать
        (None — поездки не спрашивать вовсе, 0 — спросить всегда); provider_fresh_hours — то же для провайдера.
        per_driver — поездки через карточку CRM (driver-info: заодно парк; перед звонком и кнопкой), иначе одной
        пачкой (/partners/orders: кандидаты пачки). Ждём не дольше budget_sec.

        Сводка: checked, reached — id добравших, sapar — id перешедших, errors, stopped — '' | not_configured |
        backoff | busy | budget | unavailable."""
        summary = {"checked": 0, "reached": [], "sapar": [], "errors": 0, "stopped": "", "elapsed_sec": 0.0}
        ids = [str(x) for x in (lead_ids or []) if x]
        if not ids:
            return summary
        if not self.configured():
            summary["stopped"] = "not_configured"
            return summary
        if time.monotonic() < self._now_backoff_until:
            summary["stopped"] = "backoff"
            return summary
        now = now or _utcnow()
        with self.db._get_cursor() as cur:
            cur.execute(self._NOW_SQL, (ids,))
            rows = cur.fetchall()
        trip_rows, provider_rows = [], []
        for (lead_id, driver_id, registered_at, trip_checked_at, trip_error, park_id, edm_source, edm_checked_at,
             edm_provider) in rows:
            if trips_fresh_minutes is not None and _is_due(trip_checked_at, trip_error, now,
                                                           timedelta(minutes=trips_fresh_minutes)):
                trip_rows.append((str(lead_id), str(driver_id), registered_at))
            if (provider_fresh_hours is not None and not activation.is_sapar(edm_provider)
                    and (edm_source not in (activation.EDM_FROM_CARD, activation.EDM_NONE)
                         or _is_due(edm_checked_at, "", now, timedelta(hours=provider_fresh_hours)))):
                provider_rows.append((str(lead_id), str(driver_id), park_id or ""))
        if not trip_rows and not provider_rows:
            return summary
        if not self._now_slots.acquire(blocking=False):
            summary["stopped"] = "busy"
            return summary
        started = time.monotonic()
        answers = queue.Queue()

        def lane():
            try:
                if trip_rows:
                    try:
                        if per_driver:
                            result = self._trips_per_driver(trip_rows, now)
                        else:
                            result = self._trips_batch(trip_rows, now)
                        answers.put(("trips", result))
                    except Exception as exc:
                        self._write_trip_error([r[0] for r in trip_rows], str(exc))
                        answers.put(("trips", exc))
                for lead_id, driver_id, park_id in provider_rows:
                    answers.put(("provider", (lead_id, self._provider_one(lead_id, driver_id, park_id, now,
                                                                          crm_timeout=CRM_NOW_TIMEOUT_SEC))))
            except Exception as exc:
                answers.put(("provider", ("", exc)))
            finally:
                answers.put(("done", None))
                self._now_slots.release()

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dial-activation-now")
        try:
            executor.submit(lane)
        except Exception:
            self._now_slots.release()
            raise
        finally:
            # Поток не ждём: запрос, переживший срок, доработает и запишет сам.
            executor.shutdown(wait=False)
        while True:
            left = max(0.0, budget_sec - (time.monotonic() - started))
            try:
                kind, result = answers.get(timeout=left)
            except queue.Empty:
                summary["stopped"] = "budget"
                break
            if kind == "done":
                break
            if kind == "trips":
                if isinstance(result, Exception):
                    summary["errors"] += len(trip_rows)
                    summary["stopped"] = "unavailable"
                    continue
                checked, reached = result
                summary["checked"] += checked
                summary["reached"].extend(reached)
            else:
                lead_id, outcome = result
                if isinstance(outcome, Exception) or outcome in ("error", "fleet_down"):
                    summary["errors"] += 1
                    if outcome == "fleet_down":
                        summary["stopped"] = summary["stopped"] or "fleet_unavailable"
                    continue
                summary["checked"] += 1
                if outcome == "sapar":
                    summary["sapar"].append(lead_id)
        if summary["stopped"] in ("budget", "unavailable"):
            self._now_backoff_until = time.monotonic() + NOW_BACKOFF_SEC
        if summary["reached"]:
            summary["successes"] = self._outcomes().resolve_successes(now, lead_ids=summary["reached"])
        if summary["sapar"]:
            summary["sapar_successes"] = self.resolve_sapar_successes(now, lead_ids=summary["sapar"])
        summary["elapsed_sec"] = round(time.monotonic() - started, 1)
        return summary

    def _trips_batch(self, trip_rows, now):
        """Поездки пачкой (кандидаты выдачи): одно окно — с самого раннего месяца регистрации."""
        since = min(window_start(r[2]) for r in trip_rows)
        today = now.astimezone(PERIOD_TZ).date()
        answer = self.crm.orders([r[1] for r in trip_rows], since, today, timeout=CRM_NOW_TIMEOUT_SEC)
        values, missing = [], []
        for lead_id, driver_id, _registered in trip_rows:
            info = (answer or {}).get(driver_id)
            if info is None:
                missing.append(lead_id)
            else:
                values.append((lead_id, info["count"], reached_moment(info.get("last_order_at"), now), now))
        if missing:
            self._write_trip_error(missing, "CRM не знает этого водителя")
        return len(values), self._write_trips(values)

    def _trips_per_driver(self, trip_rows, now):
        """Поездки по карточке CRM (driver-info) — заодно парк и дата регистрации. Её orders.total — окно ~полгода,
        у пачки (_trips_batch) — заказы с месяца регистрации: сверено на живой CRM 27.11.2026 — 12 из 12 водителей
        файла совпали (новички, все зарегистрированы в ноябре). Разойтись могут лишь у зарегистрированных больше
        полугода назад. На чужой ID карточка отвечает честным 404, пачка — нулём, поэтому для одного водителя — она."""
        checked, reached = 0, []
        for lead_id, driver_id, _registered in trip_rows:
            info = self.crm.driver(driver_id, timeout=CRM_NOW_TIMEOUT_SEC)
            if info is None:
                self._write_trip_error([lead_id], "CRM не знает этого водителя")
                continue
            self._write_driver_info(lead_id, info, now)
            if info.get("total") is None:
                self._write_trip_error([lead_id], "CRM не отдала число поездок")
                continue
            reached.extend(self._write_trips([(lead_id, info["total"],
                                               reached_moment(info.get("last_order_at"), now), now)]))
            checked += 1
        return checked, reached

    # ------------------------------------------------------------ запись
    def _write_trips(self, values):
        """[(lead_id, поездок, момент последнего заказа, now)] → лиды. Добрал цель отдела — signed_at (только у ещё
        не добравших). Возвращает id добравших этой записью."""
        if not values:
            return []
        with self.db._get_cursor() as cur:
            rows = execute_values(cur, """
                UPDATE dial_list_leads l
                SET trips_total = v.trips,
                    trip_checked_at = v.now,
                    trip_error = '',
                    signed_at = CASE WHEN v.trips >= s.trips_goal THEN v.last_order END,
                    signed_detected_at = CASE WHEN v.trips >= s.trips_goal THEN v.now END,
                    sign_status = CASE WHEN v.trips >= s.trips_goal THEN '""" + activation.GOAL_STATUS + """'
                                       ELSE l.sign_status END,
                    updated_at = CASE WHEN v.trips >= s.trips_goal THEN CURRENT_TIMESTAMP ELSE l.updated_at END
                FROM (VALUES %s) AS v(id, trips, last_order, now), dial_list_department_settings s
                WHERE l.id = v.id AND s.department_id = l.department_id AND l.signed_at IS NULL
                RETURNING l.id, l.signed_at IS NOT NULL
            """, values, template="(%s::uuid, %s::integer, %s::timestamptz, %s::timestamptz)",
                page_size=WRITE_CHUNK, fetch=True)
        return [str(r[0]) for r in (rows or []) if r[1]]

    def _write_trip_error(self, lead_ids, error):
        """Сбой CRM по водителям: ошибку — в лиды, время проверки не двигаем (снова «пора»)."""
        if not lead_ids:
            return
        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_leads SET trip_error = %s
                WHERE id = ANY(%s::uuid[]) AND signed_at IS NULL
            """, (str(error or "CRM не ответила")[:300], [str(x) for x in lead_ids]))

    def _write_driver_info(self, lead_id, info, now):
        """Парк и дата регистрации из карточки CRM (их нет в файле — или парк сменился)."""
        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_leads
                SET fleet_park_id = CASE WHEN %s <> '' THEN %s ELSE fleet_park_id END,
                    account_registered_at = COALESCE(account_registered_at, %s::timestamptz)
                WHERE id = %s
            """, (info.get("park_id") or "", info.get("park_id") or "", _moment(info.get("created_date")),
                  str(lead_id)))

    def _write_provider(self, lead_id, name, now):
        """Провайдер из карточки → лид. Был известен другой провайдер (или что его нет), а теперь Sapar —
        sapar_at. Возвращает True, если переход на Sapar увидела эта запись."""
        name = activation.clean_provider(name)
        source = activation.EDM_FROM_CARD if name else activation.EDM_NONE
        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_leads l
                SET edm_provider = %(name)s, edm_source = %(source)s, edm_checked_at = %(now)s, edm_error = '',
                    sapar_at = CASE
                        WHEN l.sapar_at IS NULL AND lower(%(name)s) = lower(%(sapar)s)
                             AND (l.edm_source = %(none)s
                                  OR (l.edm_provider <> '' AND lower(l.edm_provider) <> lower(%(sapar)s)))
                        THEN %(now)s ELSE l.sapar_at END
                WHERE l.id = %(id)s
                RETURNING l.sapar_at = %(now)s
            """, {"name": name, "source": source, "now": now, "sapar": activation.SAPAR, "none": activation.EDM_NONE,
                  "id": str(lead_id)})
            row = cur.fetchone()
        return bool(row and row[0])

    def _write_provider_error(self, lead_id, error, now, advance=True, drop_park=False):
        """Провайдера не узнали. advance — по водителю (нет в CRM, нет в парке): ошибка и отметка времени — в лид,
        прогон не спросит его снова раньше суток (_PROVIDER_DUE_SQL). Сбой кабинета или CRM (advance=False) в лид не
        пишется (разбор 27.11): суточный пропуск сверяет ошибку с edm_checked_at, а у провайдера из файла это время
        загрузки — сбой перед звонком выключал водителя из проверки после разговора на сутки. Такой сбой руководитель
        видит в сводке базы (fleet_error) и ответом кнопки «Обновить провайдера»."""
        if not advance:
            log.info("dial_list: активация — провайдер водителя %s не проверен (сбой источника): %s", lead_id,
                     str(error or "")[:200])
            return
        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_leads
                SET edm_error = %s,
                    edm_checked_at = CASE WHEN %s THEN %s ELSE edm_checked_at END,
                    fleet_park_id = CASE WHEN %s THEN '' ELSE fleet_park_id END
                WHERE id = %s
            """, (str(error or "")[:300], bool(advance), now, bool(drop_park), str(lead_id)))

    # ------------------------------------------------------------ успешки
    def _resolve_pending(self, now):
        """Добравшие, по кому решение об успешке отложено (шёл звонок, начатый до цели)."""
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT l.id FROM dial_list_leads l
                JOIN dial_list_department_settings s ON s.department_id = l.department_id
                WHERE s.profile = %s AND l.signed_at IS NOT NULL AND l.success_resolved_at IS NULL
                LIMIT %s
            """, (PROFILE, PENDING_LIMIT))
            pending = [str(r[0]) for r in cur.fetchall()]
        return self._outcomes().resolve_successes(now, lead_ids=pending) if pending else 0

    # Успешка «перевёл на Sapar» — по правилу успешки цели (SignChecker._RESOLVE_SQL): последний разговор от
    # SUCCESS_MIN_BILLSEC секунд до sapar_at; сообщение сети (auto_hangup 'announcement') — не разговор; пока
    # идёт звонок, начатый до перехода, решение откладывается.
    _SAPAR_RESOLVE_SQL = """
        WITH cand AS (
            SELECT l.id, l.sapar_at
            FROM dial_list_leads l
            WHERE l.sapar_at IS NOT NULL AND l.sapar_resolved_at IS NULL
              {lead_filter}
              AND """ + test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL + """
              AND NOT EXISTS (
                  SELECT 1 FROM dial_list_assignments a
                  JOIN dial_list_attempts t ON t.assignment_id = a.id
                  WHERE a.lead_id = l.id
                    AND t.state IN %(in_flight)s
                    AND t.requested_at <= l.sapar_at
                    AND t.requested_at > %(stale_before)s)
            FOR UPDATE OF l SKIP LOCKED
        ), best AS (
            SELECT DISTINCT ON (c.id) c.id AS lead_id, t.id AS attempt_id, t.operator_id
            FROM cand c
            JOIN dial_list_assignments a ON a.lead_id = c.id
            JOIN dial_list_attempts t ON t.assignment_id = a.id
            WHERE t.state = 'finished' AND NOT t.cancelled
              AND UPPER(t.disposition) IN %(answered)s
              AND t.auto_hangup <> 'announcement'
              AND t.billsec >= %(min_sec)s
              AND t.requested_at <= c.sapar_at
            ORDER BY c.id, t.requested_at DESC
        )
        UPDATE dial_list_leads l
        SET sapar_attempt_id = b.attempt_id,
            sapar_operator_id = b.operator_id,
            sapar_resolved_at = %(now)s
        FROM cand c
        LEFT JOIN best b ON b.lead_id = c.id
        WHERE l.id = c.id
        RETURNING l.id, b.attempt_id
    """

    def resolve_sapar_successes(self, now=None, lead_ids=None):
        """Перешедшим на Sapar, по кому решение ещё не принято, — кому успешка. Возвращает число засчитанных."""
        now = now or _utcnow()
        params = {"in_flight": IN_FLIGHT_STATES, "answered": ANSWERED_SQL, "min_sec": signing.SUCCESS_MIN_BILLSEC,
                  "now": now, "stale_before": now - timedelta(minutes=IN_FLIGHT_MAX_AGE_MINUTES)}
        lead_filter = ""
        if lead_ids is not None:
            params["lead_ids"] = [str(x) for x in lead_ids]
            lead_filter = "AND l.id = ANY(%(lead_ids)s::uuid[])"
        with self.db._get_cursor() as cur:
            cur.execute(self._SAPAR_RESOLVE_SQL.format(lead_filter=lead_filter), params)
            rows = cur.fetchall()
        credited = sum(1 for r in rows if r[1] is not None)
        if rows:
            log.info("dial_list: активация — по перешедшим на Sapar принято решение: %d, из них успешек %d",
                     len(rows), credited)
        return credited

    def state(self):
        """Для сводки базы руководителя: настроены ли источники, идёт ли прогон, последний прогон. Прогон и его
        сводка — из строки аренды (их видит любой процесс), а нет её — из памяти этого процесса."""
        leased, stored = self._lease_state()
        last = stored or self.last_run or {}
        keys = ("at", "reason", "trips_checked", "reached", "trips_errors", "provider_checked", "sapar",
                "provider_errors", "stopped", "provider_stopped")
        return {"configured": self.configured(), "fleet_configured": self.fleet_configured(),
                "fleet_error": getattr(self.fleet, "last_error", "") or "",
                "running": bool(self._running or leased),
                "last_run": {key: last.get(key) for key in keys} if last else None}


def _is_due(checked_at, error, now, fresh):
    """Пора ли спросить снова: не спрашивали, ошибка или ответ старше fresh."""
    if checked_at is None or str(error or "").strip():
        return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return checked_at < now - fresh
