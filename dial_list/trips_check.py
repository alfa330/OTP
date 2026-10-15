# -*- coding: utf-8 -*-
"""Проверка поездок водителей базы автообзвона Тез КЦ (TEZ APP): вышел ли водитель на линию.

Решение владельца 15.10.2026: «цель» лида отдела профиля tez — водитель ВЫШЕЛ НА ЛИНИЮ, то
есть у TEZ APP есть его завершённый заказ в месяце базы (month_first_order_at). Хранится в тех
же колонках, что «подписал» у удалённого КЦ: signed_at = month_first_order_at, sign_status =
'online'. Поэтому без правок работают выход из пула (_POOL_SQL), снятие строк со списков
(SignChecker.close_signed_rows), успешка оператору (SignChecker.resolve_successes: последний
разговор ≥10 с ДО signed_at), этап журнала и конверсия. Sapar лидов Тез не видит: у них нет ИИН.

Устроено по образцу sign_check.py:

  1. Кому пора. Лиды отделов профиля tez, ещё не вышедшие на линию и не исключённые, из базы
     обзваниваемого месяца отдела (1–7 числа — и прошлого: поздний заказ последних дней месяца
     TEZ APP отдаёт не сразу), которых не проверяли дольше RECHECK_MINUTES; сначала никогда не
     проверенные — свежая база проверяется первой. Номера реестра тестовых не спрашиваем.
  2. Пачками по BATCH_SIZE номеров одного месяца (предел TEZ APP — 100 за запрос). Итог пишется
     сразу после пачки: оборвавшийся прогон уже проверенное не теряет. Сбой пачки — trip_error,
     время проверки не двигается (водитель снова «пора»); MAX_ERRORS_IN_ROW сбоев подряд —
     TEZ APP лежит, прогон прекращается.
  3. Вышедшим — успешка и снятие открытых строк со списков операторов (методы SignChecker —
     правило одно на оба профиля).

Где крутится. Свой поток на ОДНО место: прогон — десятки запросов подряд по 60 с таймаута
каждый, в общем пуле бота он держал бы место. Запросы склеиваются: пока идёт прогон,
следующий лишь просит пройти ещё раз после него. Запускают прогон планировщик раз в 30 минут
и загрузка базы.

Проверка «сейчас» (check_now) — её ждёт человек: перед выдачей пачки (кандидаты пула) и перед
звонком (один водитель). Срок жёсткий, запрос — в своём потоке: не успели — выдача и звонок
идут как раньше, опоздавший ответ не нужен (его догонит прогон). TEZ APP не настроен или лежит
— fail-open: выдаём и звоним, сбой — только в журнал; после сбоя NOW_BACKOFF_SEC не пробуем.

Клиент TEZ APP здесь не импортируется (пакет tez — не зависимость раздела): fetch(phones,
month, timeout=None) → {phone_norm: {'month': datetime|None, 'last_before': datetime|None}}
передаёт фабрика из bot_schedule2. Номер водителя отсюда уходит только в этот запрос.
"""
import logging
import queue
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from psycopg2.extras import execute_values

from test_numbers import keys as test_keys
from . import signing
from .sign_check import SignChecker

log = logging.getLogger(__name__)

PROFILE_TEZ = "tez"
# sign_status лида, вышедшего на линию: не статус Sapar, а пометка, откуда signed_at.
ONLINE_STATUS = "online"
# Не вышедшего водителя переспрашиваем не чаще раза в час: поездка — событие дня, а не минуты.
RECHECK_MINUTES = 60
# 1–7 числа проверяется и база прошлого месяца (как ночная сверка «Базы лидов ОП TEZ»).
PREVIOUS_MONTH_DAYS = 7
RUN_BUDGET_SEC = 25 * 60
RUN_MAX_LEADS = 20000
# Предел эндпоинта TEZ APP — 100 водителей за запрос.
BATCH_SIZE = 100
BATCH_PAUSE_SEC = 0.3
MAX_ERRORS_IN_ROW = 5
WRITE_CHUNK = 200
# Проверка «сейчас»: после сбоя TEZ APP столько секунд её не пробуем; одновременно — не больше
# стольких (без ожидания места: занято — выдаём и звоним без неё).
NOW_BACKOFF_SEC = 30
NOW_CONCURRENT = 4
# Подвисших «вышел, а решение по успешке не принято» (звонок шёл) добираем за прогон не больше.
PENDING_SUCCESS_LIMIT = 5000

PERIOD_TZ = signing.PERIOD_TZ


def _utcnow():
    return datetime.now(timezone.utc)


def month_key(period):
    """База месяца (date первого дня) → 'YYYY-MM' для запроса в TEZ APP."""
    return f"{period.year:04d}-{period.month:02d}"


def _moment(value):
    """Дата из ответа TEZ APP → datetime с поясом или None. Клиент отдаёт datetime; строка —
    ISO (подменённый fetch), без пояса — время Алматы, как у TEZ APP."""
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


def is_due(checked_at, trip_error, now, fresh_minutes):
    """Спросить ли TEZ APP о водителе сейчас: не проверяли, проверка старше fresh_minutes или
    кончилась ошибкой."""
    if checked_at is None or str(trip_error or "").strip():
        return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return checked_at < now - timedelta(minutes=fresh_minutes)


class TripChecker:
    """Проверка поездок для всего раздела (лиды отделов профиля tez). fetch и configured —
    из bot_schedule2 (в тестах — подделки); successes — у кого брать resolve_successes и
    close_signed_rows (по умолчанию SignChecker этой же базы: правило успешки одно)."""

    def __init__(self, db, fetch=None, configured=None, sleep=time.sleep, successes=None):
        self.db = db
        self._fetch = fetch
        self._configured = configured
        self._sleep = sleep
        self._successes = successes
        self._executor = None
        self._lock = threading.Lock()
        self._running = False
        self._again = False
        self.last_run = None
        self._now_backoff_until = 0.0
        self._now_slots = threading.BoundedSemaphore(NOW_CONCURRENT)

    def configured(self):
        """Есть ли доступ к TEZ APP (фабрика передана и говорит, что токен задан)."""
        if self._fetch is None:
            return False
        if self._configured is None:
            return True
        try:
            return bool(self._configured())
        except Exception:
            log.exception("dial_list: не удалось узнать, настроен ли TEZ APP")
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
        """Поставить прогон в свой поток, не дожидаясь его. Идёт прогон — после него пройти
        ещё раз (база, загруженная посреди прогона, не ждёт полчаса)."""
        with self._lock:
            if self._running:
                self._again = True
                return False
            self._running = True
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dial-trips")
        self._executor.submit(self._loop, reason)
        return True

    def _loop(self, reason):
        while True:
            try:
                self.run(reason=reason)
            except Exception:
                log.exception("dial_list: проверка поездок упала (%s)", reason)
            with self._lock:
                if not self._again:
                    self._running = False
                    return
                self._again = False
            reason = "повтор после прогона"

    # ------------------------------------------------------------ прогон
    # Кому пора: лиды отделов tez, не вышедшие и не исключённые, база обзваниваемого месяца отдела
    # (1–7 числа — и прошлого календарного), проверка старше RECHECK_MINUTES или её не было.
    _DUE_SQL = """
        SELECT l.id, l.phone_norm, l.period
        FROM dial_list_leads l
        JOIN dial_list_department_settings s ON s.department_id = l.department_id
        WHERE s.profile = %(profile)s
          AND l.signed_at IS NULL AND l.status <> 'excluded'
          AND (l.period = COALESCE(s.active_period, %(current)s)
               OR (%(with_previous)s AND l.period = %(previous)s))
          AND (l.trip_checked_at IS NULL OR l.trip_checked_at < %(due_before)s)
          AND """ + test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL + """
        ORDER BY l.trip_checked_at ASC NULLS FIRST, l.created_at ASC
        LIMIT %(limit)s
    """
    # Вышедшие, по кому решение об успешке отложено (шёл звонок, начатый до выхода на линию):
    # прогон Sapar их тоже доберёт, но без Sapar они висели бы.
    _PENDING_SUCCESS_SQL = """
        SELECT l.id FROM dial_list_leads l
        JOIN dial_list_department_settings s ON s.department_id = l.department_id
        WHERE s.profile = %s AND l.signed_at IS NOT NULL AND l.success_resolved_at IS NULL
        LIMIT %s
    """

    def run(self, reason="", now=None, budget_sec=RUN_BUDGET_SEC):
        """Проверить всех, кому пора. Возвращает сводку прогона (она же — last_run)."""
        summary = {"reason": reason, "checked": 0, "online": 0, "errors": 0, "stopped": "",
                   "successes": 0, "closed_rows": 0, "at": None}
        if not self.configured():
            log.warning("dial_list: проверка поездок пропущена — TEZ APP не настроен (TEZ_DRIVERS_API_TOKEN)")
            summary["stopped"] = "not_configured"
            self.last_run = summary
            return summary
        now = now or _utcnow()
        summary["at"] = now.isoformat()
        today = now.astimezone(PERIOD_TZ).date()
        current = signing.month_start(today)
        params = {"profile": PROFILE_TEZ, "current": current, "previous": signing.shift_month(current, -1),
                  "with_previous": today.day <= PREVIOUS_MONTH_DAYS, "limit": RUN_MAX_LEADS,
                  "due_before": now - timedelta(minutes=RECHECK_MINUTES)}
        with self.db._get_cursor() as cur:
            cur.execute(self._DUE_SQL, params)
            due = cur.fetchall()
        # Месяц базы → [(id, номер)]: один запрос — номера одного месяца (окно TEZ APP месячное).
        months = OrderedDict()
        for lead_id, phone, period in due:
            months.setdefault(month_key(period), []).append((str(lead_id), str(phone or "")))
        started = time.monotonic()
        newly_online, errors_in_row, first = [], 0, True
        for month, leads in months.items():
            for start in range(0, len(leads), BATCH_SIZE):
                if time.monotonic() - started > budget_sec:
                    summary["stopped"] = "budget"
                    break
                if not first and BATCH_PAUSE_SEC:
                    self._sleep(BATCH_PAUSE_SEC)
                first = False
                chunk = leads[start:start + BATCH_SIZE]
                try:
                    answer = self._fetch([phone for _, phone in chunk], month)
                except Exception as exc:
                    summary["errors"] += len(chunk)
                    errors_in_row += 1
                    log.warning("dial_list: TEZ APP не ответил по пачке %d номеров за %s: %s", len(chunk), month,
                                str(exc)[:300])
                    self._write_error([lead_id for lead_id, _ in chunk], str(exc))
                    if errors_in_row >= MAX_ERRORS_IN_ROW:
                        summary["stopped"] = "tez_app_unavailable"
                        break
                    continue
                errors_in_row = 0
                newly_online.extend(self._write(chunk, answer or {}, now))
                summary["checked"] += len(chunk)
            if summary["stopped"]:
                break
        summary["online"] = len(newly_online)
        outcomes = self._outcomes()
        if newly_online:
            summary["closed_rows"] = outcomes.close_signed_rows(newly_online)
        with self.db._get_cursor() as cur:
            cur.execute(self._PENDING_SUCCESS_SQL, (PROFILE_TEZ, PENDING_SUCCESS_LIMIT))
            pending = [str(r[0]) for r in cur.fetchall()]
        if pending:
            summary["successes"] = outcomes.resolve_successes(now, lead_ids=pending)
        summary["elapsed_sec"] = round(time.monotonic() - started, 1)
        if due or summary["stopped"]:
            log.info("dial_list: проверка поездок (%s): к проверке %d, проверено %d, вышли на линию %d, успешек %d, "
                     "снято со списков %d, ошибок %d%s", reason, len(due), summary["checked"], summary["online"],
                     summary["successes"], summary["closed_rows"], summary["errors"],
                     f", остановлена: {summary['stopped']}" if summary["stopped"] else "")
        self.last_run = summary
        return summary

    # ------------------------------------------------------------ проверка «сейчас»
    _NOW_LEADS_SQL = """
        SELECT l.id, l.phone_norm, l.period, l.trip_checked_at, l.trip_error
        FROM dial_list_leads l
        WHERE l.id = ANY(%s::uuid[]) AND l.signed_at IS NULL
          AND """ + test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL + """
    """

    def check_now(self, lead_ids, *, fresh_minutes, timeout, budget_sec, now=None):
        """Проверить этих водителей сразу — их ждёт оператор. Спрашиваем только тех, чья проверка
        старше fresh_minutes или с ошибкой; запрос — в своём потоке, ждём не дольше budget_sec
        (timeout — на HTTP-запрос клиента). Итог пишется как у прогона; вышедшим — успешка.
        Строки со списков здесь не снимаются: это решает вызывающий (пул, звонок).

        Возвращает сводку: checked, online — id вышедших, errors, stopped — '' | not_configured |
        backoff | busy | budget | tez_app_unavailable, elapsed_sec."""
        summary = {"checked": 0, "online": [], "errors": 0, "stopped": "", "elapsed_sec": 0.0}
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
            cur.execute(self._NOW_LEADS_SQL, (ids,))
            rows = cur.fetchall()
        months = OrderedDict()
        for lead_id, phone, period, checked_at, trip_error in rows:
            if phone and is_due(checked_at, trip_error, now, fresh_minutes):
                months.setdefault(month_key(period), []).append((str(lead_id), str(phone)))
        if not months:
            return summary
        if not self._now_slots.acquire(blocking=False):
            summary["stopped"] = "busy"
            return summary
        started = time.monotonic()
        work = [(month, leads[i:i + BATCH_SIZE]) for month, leads in months.items()
                for i in range(0, len(leads), BATCH_SIZE)]
        answers = queue.Queue()

        def lane():
            try:
                for month, chunk in work:
                    try:
                        result = self._fetch([phone for _, phone in chunk], month, timeout=timeout)
                    except Exception as exc:
                        answers.put((chunk, exc))
                        return
                    answers.put((chunk, result))
            finally:
                self._now_slots.release()

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dial-trips-now")
        try:
            executor.submit(lane)
        except Exception:
            self._now_slots.release()
            raise
        finally:
            # Поток не ждём: запрос, переживший срок, доработает сам — его ответ уже не нужен.
            executor.shutdown(wait=False)
        for _ in work:
            left = max(0.0, budget_sec - (time.monotonic() - started))
            try:
                chunk, result = answers.get(timeout=left)
            except queue.Empty:
                summary["stopped"] = "budget"
                break
            if isinstance(result, Exception):
                summary["errors"] += len(chunk)
                summary["stopped"] = "tez_app_unavailable"
                self._write_error([lead_id for lead_id, _ in chunk], str(result))
                break
            summary["checked"] += len(chunk)
            summary["online"].extend(self._write(chunk, result or {}, now))
        if summary["stopped"] in ("budget", "tez_app_unavailable"):
            self._now_backoff_until = time.monotonic() + NOW_BACKOFF_SEC
        if summary["online"]:
            summary["successes"] = self._outcomes().resolve_successes(now, lead_ids=summary["online"])
        summary["elapsed_sec"] = round(time.monotonic() - started, 1)
        return summary

    # ------------------------------------------------------------ запись
    def _write(self, chunk, answer, now):
        """Ответ TEZ APP по пачке → строки лидов: даты заказов, время проверки, ошибка пуста.
        Заказ в месяце базы — вышел на линию: signed_at = month_first_order_at (только у ещё не
        вышедших). Номера, которого нет в ответе, у TEZ APP нет — проверен, заказов нет.
        Возвращает id тех, кто вышел на линию этой записью."""
        values = []
        for lead_id, phone in chunk:
            info = answer.get(phone) if isinstance(answer, dict) else None
            info = info if isinstance(info, dict) else {}
            values.append((lead_id, _moment(info.get("month")), _moment(info.get("last_before")), now))
        if not values:
            return []
        # sign_status — текстом в запросе: у execute_values единственный %s — список VALUES.
        with self.db._get_cursor() as cur:
            rows = execute_values(cur, """
                UPDATE dial_list_leads l
                SET month_first_order_at = v.month_first,
                    last_order_before_at = v.last_before,
                    trip_checked_at = v.now,
                    trip_error = '',
                    signed_at = v.month_first,
                    signed_detected_at = CASE WHEN v.month_first IS NOT NULL THEN v.now END,
                    sign_status = CASE WHEN v.month_first IS NOT NULL THEN '""" + ONLINE_STATUS + """'
                                       ELSE l.sign_status END,
                    updated_at = CASE WHEN v.month_first IS NOT NULL THEN CURRENT_TIMESTAMP ELSE l.updated_at END
                FROM (VALUES %s) AS v(id, month_first, last_before, now)
                WHERE l.id = v.id AND l.signed_at IS NULL
                RETURNING l.id, l.signed_at IS NOT NULL
            """, values, template="(%s::uuid, %s::timestamptz, %s::timestamptz, %s::timestamptz)",
                page_size=WRITE_CHUNK, fetch=True)
        return [str(r[0]) for r in (rows or []) if r[1]]

    def _write_error(self, lead_ids, error):
        """Сбой TEZ APP по пачке: ошибку — в лиды, время проверки не двигаем (снова «пора»)."""
        if not lead_ids:
            return
        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_leads SET trip_error = %s
                WHERE id = ANY(%s::uuid[]) AND signed_at IS NULL
            """, (str(error or "TEZ APP не ответил")[:300], [str(x) for x in lead_ids]))

    def state(self):
        """Для сводки базы руководителя: настроен ли, идёт ли прогон, последний прогон."""
        last = self.last_run or {}
        return {"configured": self.configured(), "running": bool(self._running),
                "last_run": ({key: last.get(key) for key in ("at", "reason", "checked", "online", "errors", "stopped")}
                             if last else None)}
