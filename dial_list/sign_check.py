# -*- coding: utf-8 -*-
"""Проверка подписания документов по базе обзвона (Sapar) и засчитывание успешек.

Правила — в signing.py, сеть — в crm/sapar.py, здесь порядок работы:

  1. Кому пора. Водители с ИИН из баз текущего и прошлого месяца, ещё не
     подписавшие, которых не проверяли дольше RECHECK_HOURS (минус запас, чтобы
     получасовой запуск не отодвигал проверку на лишние полчаса). Свежая база
     проверяется сразу после загрузки: у её строк проверки не было вовсе.
  2. Один запрос в Sapar на пару (ИИН, месяц документов) — одного водителя в
     двух отделах спрашиваем один раз. Итог пишется пачками: оборвавшийся на
     середине прогон не теряет уже проверенное.
  3. Подписавшему — успешка: последний разговор ≥ SUCCESS_MIN_BILLSEC секунд,
     НАЧАВШИЙСЯ ДО подписи. Разговор после подписи на неё не повлиял; подписал
     без такого разговора — «подписал сам», успешки нет. Пока по водителю идёт
     звонок, начатый до подписи, решение откладывается: его billsec ещё не
     известен, а это как раз тот случай, когда оператор помогает подписать.
  4. Подписавший из пула уходит (_POOL_SQL), а его строка, выданная оператору и
     ещё не набранная, снимается с его списка так же, как при «Исключить»:
     звонить подписавшему незачем.

Где крутится. Свой поток на ОДНО место: прогон — сотни запросов подряд, в общем
пуле бота из четырёх мест он держал бы четверть приложения (см. заметку про
executor_pool). Запросы на проверку склеиваются: пока идёт прогон, следующий
запрос лишь просит пройти ещё раз после него.

Проверка «сейчас» (check_now, владелец 11.10.2026: «подписавших не выдавать») —
другое дело: её ждёт человек — оператор нажал «Ещё» или «Позвонить». Поэтому
несколько водителей сразу, параллельно, с коротким таймаутом и жёстким сроком;
не успели — выдача идёт как раньше, остальных проверит прогон. Sapar не ответил —
NOW_BACKOFF_SEC секунд её не пробуем: пока он лежит, каждый клик ждал бы таймаута.
"""
import json
import logging
import queue
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from psycopg2.extras import execute_values

from crm import sapar
from test_numbers import keys as test_keys
from . import signing

log = logging.getLogger(__name__)

# Запас к «раз в 3 часа»: проверка идёт каждые 30 минут, и водитель,
# проверенный в 12:05, должен попасть в прогон 14:50, а не 15:20.
RECHECK_SLACK_MINUTES = 15
# Один прогон — не дольше этого: остаток доберёт следующий, через полчаса.
RUN_BUDGET_SEC = 25 * 60
RUN_MAX_LEADS = 20000
# Sapar лежит: после стольких сетевых сбоев подряд прогон прекращается.
MAX_SYSTEMIC_ERRORS_IN_ROW = 5
REQUEST_TIMEOUT_SEC = 10
# Пауза между запросами: ~10 в секунду, чтобы не упереться в чужие лимиты.
REQUEST_PAUSE_SEC = 0.05
WRITE_CHUNK = 200
# Звонок в «живом» состоянии старше этого — зависший (телефон выключен, reconcile
# не бежал), ждать его исхода для засчитывания успешки не нужно.
IN_FLIGHT_MAX_AGE_MINUTES = 90
# Проверка «сейчас» упёрлась в лежащий Sapar — столько секунд следующие не пробуем.
NOW_BACKOFF_SEC = 30
# Перепроверка перед отчётом о подписании (владелец, 20.10.2026: «до отправки показателей все данные
# самые актуальные — начать собирать примерно за 30 минут до рассылки»). Последовательно водитель —
# ~0,17 с (замер прода 19.10: 1 538 за 4 мин 20 с), 12 тыс. неподписавших в полчаса не уложились бы:
# столько потоков, у каждого та же пауза между запросами. Проверка «сейчас» держит к Sapar 4.
REFRESH_LANES = 3
REFRESH_MAX_LEADS = 60000

ANSWERED_SQL = ("ANSWER", "ANSWERED", "SUCCESS", "VM-SUCCESS")
IN_FLIGHT_STATES = ("requested", "leg_ringing", "leg_answered", "ended")


def _utcnow():
    return datetime.now(timezone.utc)


def _default_fetch(iin, month, year, session=None, timeout=REQUEST_TIMEOUT_SEC):
    return sapar.driver_documents(iin, month, year, session=session, timeout=timeout)


def _new_session():
    """Свой requests.Session: соединение с Sapar держится между запросами потока."""
    try:
        import requests
        return requests.Session()
    except Exception:  # pragma: no cover — requests есть всегда, но проверка не должна от него зависеть
        return None


def _package_verdict(documents, now):
    """Документы Яндекса → (общий статус, документы для карточки, момент подписи —
    только у подписавшего). Одно толкование у прогона и у проверки «сейчас»: итог
    пишется одинаково."""
    code = signing.aggregate(documents)
    signed_at = signing.signed_moment(documents, now) if code == signing.SIGNED else None
    return code, json.dumps(signing.document_rows(documents), ensure_ascii=False), signed_at


class SignChecker:
    """Проверка подписания для всего раздела. `fetch(iin, month, year, session,
    timeout=…)` — подменяется в тестах; по умолчанию crm.sapar.driver_documents
    (прогон таймаут не передаёт — у него свой, REQUEST_TIMEOUT_SEC)."""

    def __init__(self, db, fetch=None, configured=None, sleep=time.sleep):
        self.db = db
        self._fetch = fetch or _default_fetch
        self._configured = configured or sapar.configured
        self._sleep = sleep
        self._executor = None
        self._lock = threading.Lock()
        self._running = False
        self._again = False
        # Ждущая перепроверка перед отчётом (request_refresh): идёт сразу за текущим прогоном.
        self._refresh = None
        self.last_run = None
        self.last_refresh = None
        # До какого момента (time.monotonic) проверку «сейчас» не пробуем: Sapar лежит.
        self._now_backoff_until = 0.0

    def configured(self):
        """Есть ли доступ к Sapar (SAPAR_API в окружении сервера)."""
        return bool(self._configured())

    # ------------------------------------------------------------ очередь
    def request(self, reason=""):
        """Поставить прогон в свой поток, не дожидаясь его. Идёт прогон — после
        него пройти ещё раз (новая база, пришедшая посреди прогона, не ждёт полчаса)."""
        with self._lock:
            if self._running:
                self._again = True
                return False
            self._running = True
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dial-sign")
        self._executor.submit(self._loop, reason)
        return True

    def request_refresh(self, bases, fresh_before, deadline, reason=""):
        """Поставить перепроверку перед отчётом (refresh) в тот же поток: идёт прогон — сразу после
        него. Поздний запрос заменяет ждущий: у них одна цель — свежие статусы к ближайшему отчёту."""
        with self._lock:
            self._refresh = {"bases": list(bases or ()), "fresh_before": fresh_before, "deadline": deadline,
                             "reason": reason}
            if self._running:
                return False
            self._running = True
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dial-sign")
        self._executor.submit(self._loop, None)
        return True

    def _loop(self, reason):
        # reason None — поток поднят перепроверкой: обычный прогон за ней не должен.
        owed = reason is not None
        while True:
            with self._lock:
                refresh, self._refresh = self._refresh, None
                if refresh is None and not owed:
                    if not self._again:
                        self._running = False
                        return
                    self._again = False
                    owed, reason = True, "повтор после прогона"
            try:
                if refresh is not None:
                    self.refresh(**refresh)
                else:
                    owed = False
                    self.run(reason=reason)
            except Exception:
                log.exception("dial_list: проверка подписания упала (%s)",
                              refresh.get("reason") if refresh is not None else reason)

    # ------------------------------------------------------------ прогон
    _DUE_SQL = """
        SELECT l.id, l.iin, l.period
        FROM dial_list_leads l
        WHERE l.iin <> '' AND l.signed_at IS NULL
          {due}
          {scope}
        ORDER BY l.sign_checked_at ASC NULLS FIRST, l.created_at ASC
        LIMIT %(limit)s
    """

    def run(self, reason="", now=None, lead_ids=None, force=False, budget_sec=RUN_BUDGET_SEC):
        """Проверить всех, кому пора, из баз текущего и прошлого месяца. lead_ids —
        только этих, из базы любого месяца («Проверить сейчас» в карточке); force —
        не глядя на время прошлой проверки. Возвращает сводку прогона."""
        summary = {"reason": reason, "checked": 0, "signed": 0, "errors": 0, "stopped": "",
                   "successes": 0, "closed_rows": 0}
        if not self.configured():
            log.warning("dial_list: проверка подписания пропущена — нет доступа к Sapar (SAPAR_API)")
            summary["stopped"] = "not_configured"
            self.last_run = summary
            return summary
        now = now or _utcnow()
        from_period, to_period = signing.check_window(now.astimezone(signing.PERIOD_TZ).date())
        params = {"from_period": from_period, "to_period": to_period, "limit": RUN_MAX_LEADS,
                  "due_before": now - timedelta(hours=signing.RECHECK_HOURS)
                  + timedelta(minutes=RECHECK_SLACK_MINUTES)}
        due_filter = "" if force else "AND (l.sign_checked_at IS NULL OR l.sign_checked_at < %(due_before)s)"
        if lead_ids is not None:
            params["lead_ids"] = [str(x) for x in lead_ids]
            scope = "AND l.id = ANY(%(lead_ids)s::uuid[])"
            if not params["lead_ids"]:
                return summary
        else:
            scope = "AND l.period BETWEEN %(from_period)s AND %(to_period)s"
        with self.db._get_cursor() as cur:
            cur.execute(self._DUE_SQL.format(due=due_filter, scope=scope), params)
            due = cur.fetchall()
        # (ИИН, месяц документов) → строки: один водитель в двух базах одного
        # месяца документов (разные отделы) — один запрос.
        groups = OrderedDict()
        for lead_id, iin, period in due:
            groups.setdefault((str(iin), signing.doc_month_for(period)), []).append(str(lead_id))

        started = time.monotonic()
        session = _new_session()
        pending, newly_signed = [], []
        systemic_in_row = 0
        first = True
        try:
            for (iin, (month, year)), ids in groups.items():
                if time.monotonic() - started > budget_sec:
                    summary["stopped"] = "budget"
                    break
                if not first and REQUEST_PAUSE_SEC:
                    self._sleep(REQUEST_PAUSE_SEC)
                first = False
                result = self._fetch(iin, month, year, session=session)
                if not result.get("ok"):
                    summary["errors"] += len(ids)
                    error = str(result.get("error") or "Sapar не ответил")[:300]
                    pending.extend(("error", lid, error, bool(result.get("systemic"))) for lid in ids)
                    if result.get("systemic"):
                        systemic_in_row += 1
                        if systemic_in_row >= MAX_SYSTEMIC_ERRORS_IN_ROW:
                            summary["stopped"] = "sapar_unavailable"
                            break
                    continue
                systemic_in_row = 0
                documents = result.get("yandex") or []
                code, docs_json, signed_at = _package_verdict(documents, now)
                for lid in ids:
                    pending.append(("ok", lid, code, docs_json, signed_at))
                    summary["checked"] += 1
                    if signed_at is not None:
                        newly_signed.append(lid)
                if len(pending) >= WRITE_CHUNK:
                    self._write(pending, now)
                    pending = []
        finally:
            if pending:
                self._write(pending, now)
            if session is not None:
                session.close()
        summary["signed"] = len(newly_signed)
        summary["closed_rows"] = self.close_signed_rows(newly_signed)
        summary["successes"] = self.resolve_successes(now)
        summary["elapsed_sec"] = round(time.monotonic() - started, 1)
        if due or summary["stopped"]:
            log.info("dial_list: проверка подписания (%s): к проверке %d, проверено %d, подписали %d, "
                     "успешек %d, снято со списков %d, ошибок %d%s", reason, len(due), summary["checked"],
                     summary["signed"], summary["successes"], summary["closed_rows"], summary["errors"],
                     f", остановлена: {summary['stopped']}" if summary["stopped"] else "")
        if lead_ids is None:
            self.last_run = summary
        return summary

    # ------------------------------------------------------------ перепроверка перед отчётом
    _REFRESH_SQL = """
        SELECT l.id, l.iin, l.period
        FROM dial_list_leads l
        WHERE l.iin <> '' AND l.signed_at IS NULL
          AND (l.sign_checked_at IS NULL OR l.sign_checked_at < %(fresh_before)s)
          AND ({bases})
        ORDER BY l.sign_checked_at ASC NULLS FIRST, l.created_at ASC
        LIMIT %(limit)s
    """

    def refresh(self, bases, fresh_before, deadline, reason="", now=None, lanes=REFRESH_LANES):
        """Перепроверить к отчёту всех неподписавших баз `bases` [(department_id, period)], кого
        проверяли раньше fresh_before (момент, aware): в отчёте статусы на время рассылки, а не
        трёхчасовой давности. deadline (aware) — после него новых запросов нет, отчёт уходит с тем,
        что успели. Как прогон: один запрос на пару (ИИН, месяц документов), итог пачками (_write),
        подписавшие снимаются со списков, по ним решается успешка; Sapar лежит — остановка. Запросы —
        в `lanes` потоков, у каждого свой requests.Session и та же пауза, что у прогона."""
        summary = {"reason": reason, "refresh": True, "due": 0, "checked": 0, "signed": 0, "errors": 0,
                   "stopped": "", "successes": 0, "closed_rows": 0}
        if not self.configured():
            log.warning("dial_list: перепроверка перед отчётом пропущена — нет доступа к Sapar (SAPAR_API)")
            summary["stopped"] = "not_configured"
            self.last_refresh = summary
            return summary
        bases = [(int(department_id), period) for department_id, period in bases or ()]
        if not bases:
            self.last_refresh = summary
            return summary
        now = now or _utcnow()
        params = {"fresh_before": fresh_before, "limit": REFRESH_MAX_LEADS}
        clauses = []
        for index, (department_id, period) in enumerate(bases):
            params["d%d" % index], params["p%d" % index] = department_id, period
            clauses.append("(l.department_id = %%(d%d)s AND l.period = %%(p%d)s)" % (index, index))
        with self.db._get_cursor() as cur:
            cur.execute(self._REFRESH_SQL.format(bases=" OR ".join(clauses)), params)
            due = cur.fetchall()
        summary["due"] = len(due)
        groups = OrderedDict()
        for lead_id, iin, period in due:
            groups.setdefault((str(iin), signing.doc_month_for(period)), []).append(str(lead_id))

        started = time.monotonic()
        end = started + max(0.0, (deadline - now).total_seconds())
        work, answers, stop = queue.Queue(), queue.Queue(), threading.Event()
        for key in groups:
            work.put(key)
        lanes = max(1, min(int(lanes), len(groups) or 1))

        def lane():
            session = _new_session()
            first = True
            try:
                while not stop.is_set() and time.monotonic() < end:
                    try:
                        key = work.get_nowait()
                    except queue.Empty:
                        return
                    if not first and REQUEST_PAUSE_SEC:
                        self._sleep(REQUEST_PAUSE_SEC)
                    first = False
                    iin, (month, year) = key
                    try:
                        result = self._fetch(iin, month, year, session=session)
                    except Exception as exc:  # sapar.driver_documents не бросает, подменённый fetch — может
                        result = {"ok": False, "error": str(exc), "systemic": True}
                    answers.put((key, result))
            finally:
                if session is not None:
                    session.close()
                answers.put(None)

        threads = [threading.Thread(target=lane, name="dial-sign-refresh-%d" % i, daemon=True)
                   for i in range(lanes)] if groups else []
        for thread in threads:
            thread.start()
        pending, newly_signed = [], []
        systemic_in_row, finished = 0, 0
        try:
            while finished < len(threads):
                item = answers.get()
                if item is None:
                    finished += 1
                    continue
                key, result = item
                ids = groups[key]
                if not result.get("ok"):
                    summary["errors"] += len(ids)
                    error = str(result.get("error") or "Sapar не ответил")[:300]
                    pending.extend(("error", lid, error, bool(result.get("systemic"))) for lid in ids)
                    if result.get("systemic"):
                        systemic_in_row += 1
                        if systemic_in_row >= MAX_SYSTEMIC_ERRORS_IN_ROW and not stop.is_set():
                            summary["stopped"] = "sapar_unavailable"
                            stop.set()
                    continue
                systemic_in_row = 0
                code, docs_json, signed_at = _package_verdict(result.get("yandex") or [], now)
                for lid in ids:
                    pending.append(("ok", lid, code, docs_json, signed_at))
                    summary["checked"] += 1
                    if signed_at is not None:
                        newly_signed.append(lid)
                if len(pending) >= WRITE_CHUNK:
                    self._write(pending, now)
                    pending = []
        finally:
            stop.set()
            if pending:
                self._write(pending, now)
        if not summary["stopped"] and not work.empty():
            summary["stopped"] = "deadline"
        summary["signed"] = len(newly_signed)
        summary["closed_rows"] = self.close_signed_rows(newly_signed)
        summary["successes"] = self.resolve_successes(now)
        summary["elapsed_sec"] = round(time.monotonic() - started, 1)
        log.info("dial_list: перепроверка подписания перед отчётом (%s): к проверке %d, проверено %d, подписали %d, "
                 "успешек %d, снято со списков %d, ошибок %d, потоков %d, за %.0f с%s", reason, len(due),
                 summary["checked"], summary["signed"], summary["successes"], summary["closed_rows"],
                 summary["errors"], len(threads), summary["elapsed_sec"],
                 f", остановлена: {summary['stopped']}" if summary["stopped"] else "")
        self.last_refresh = summary
        return summary

    # ------------------------------------------------------------ проверка «сейчас»
    def check_now(self, leads, *, timeout, workers, budget_sec, max_systemic, now=None):
        """Проверить этих водителей сразу — их ждёт оператор (выдача порции, звонок).

        leads — [(lead_id, ИИН, база месяца)]; без ИИН не спрашиваем. Один запрос на
        пару (ИИН, месяц документов), как у прогона, но параллельно: `workers` потоков,
        у каждого свой requests.Session. `timeout` — на запрос, `budget_sec` — на всё:
        что пришло к сроку, записано, остальных проверит прогон. После `max_systemic`
        сетевых сбоев Sapar лежит — дальше не спрашиваем и NOW_BACKOFF_SEC не пробуем.
        Итог пишется как у прогона (_write); по новым подписавшим — успешка, только по
        ним. Строки со списков операторов здесь не снимаются: это решает вызывающий
        (пул выдачи, проверка перед звонком).

        Возвращает сводку: checked; signed и processing — id лидов (подписал / на
        проверке у Яндекса); errors, systemic, successes; stopped — '' | not_configured |
        backoff | sapar_unavailable | budget; elapsed_sec."""
        summary = {"checked": 0, "signed": [], "processing": [], "errors": 0, "systemic": 0,
                   "successes": 0, "stopped": "", "elapsed_sec": 0.0}
        if not self.configured():
            summary["stopped"] = "not_configured"
            return summary
        groups = OrderedDict()
        for lead_id, iin, period in leads or ():
            if str(iin or "").strip():
                groups.setdefault((str(iin).strip(), signing.doc_month_for(period)), []).append(str(lead_id))
        if not groups:
            return summary
        if time.monotonic() < self._now_backoff_until:
            summary["stopped"] = "backoff"
            return summary
        now = now or _utcnow()
        started = time.monotonic()
        work, answers, stop = queue.Queue(), queue.Queue(), threading.Event()
        for key in groups:
            work.put(key)

        def lane():
            session = _new_session()
            try:
                while not stop.is_set():
                    try:
                        key = work.get_nowait()
                    except queue.Empty:
                        return
                    iin, (month, year) = key
                    try:
                        result = self._fetch(iin, month, year, session=session, timeout=timeout)
                    except Exception as exc:  # sapar.driver_documents не бросает, подменённый fetch — может
                        result = {"ok": False, "error": str(exc), "systemic": True}
                    answers.put((key, result))
            finally:
                if session is not None:
                    session.close()

        lanes = max(1, min(int(workers), len(groups)))
        executor = ThreadPoolExecutor(max_workers=lanes, thread_name_prefix="dial-sign-now")
        try:
            for _ in range(lanes):
                executor.submit(lane)
        finally:
            # Потоки не ждём: запрос, переживший срок, доработает сам — его ответ уже не нужен.
            executor.shutdown(wait=False)
        pending = []
        try:
            for _ in range(len(groups)):
                # Срок вышел — забираем только то, что уже пришло.
                left = max(0.0, budget_sec - (time.monotonic() - started))
                try:
                    key, result = answers.get(timeout=left)
                except queue.Empty:
                    summary["stopped"] = "budget"
                    break
                ids = groups[key]
                if not result.get("ok"):
                    summary["errors"] += len(ids)
                    error = str(result.get("error") or "Sapar не ответил")[:300]
                    pending.extend(("error", lid, error, bool(result.get("systemic"))) for lid in ids)
                    if result.get("systemic"):
                        summary["systemic"] += 1
                        if summary["systemic"] >= max(1, int(max_systemic)):
                            summary["stopped"] = "sapar_unavailable"
                            break
                    continue
                code, docs_json, signed_at = _package_verdict(result.get("yandex") or [], now)
                for lid in ids:
                    pending.append(("ok", lid, code, docs_json, signed_at))
                    summary["checked"] += 1
                    if signed_at is not None:
                        summary["signed"].append(lid)
                    elif code == signing.PROCESSING:
                        summary["processing"].append(lid)
        finally:
            stop.set()
        if summary["stopped"] == "sapar_unavailable":
            self._now_backoff_until = time.monotonic() + NOW_BACKOFF_SEC
        if pending:
            self._write(pending, now)
        if summary["signed"]:
            summary["successes"] = self.resolve_successes(now, lead_ids=summary["signed"])
        summary["elapsed_sec"] = round(time.monotonic() - started, 1)
        return summary

    def _write(self, items, now):
        """Итоги Sapar → строки лидов. Подписавшего не трогаем повторно (signed_at
        только ставится). Сетевой сбой не сдвигает время проверки: такой водитель
        снова «пора» в следующем прогоне; отказ Sapar по конкретному ИИН — сдвигает."""
        ok = [(i[1], i[2], i[3], i[4], now) for i in items if i[0] == "ok"]
        errors = [(i[1], i[2], i[3], now) for i in items if i[0] == "error"]
        with self.db._get_cursor() as cur:
            if ok:
                execute_values(cur, """
                    UPDATE dial_list_leads l
                    SET sign_status = v.code,
                        sign_docs = v.docs,
                        sign_checked_at = v.now,
                        sign_error = '',
                        signed_at = v.signed_at,
                        signed_detected_at = CASE WHEN v.signed_at IS NOT NULL THEN v.now END,
                        updated_at = CASE WHEN v.signed_at IS NOT NULL THEN CURRENT_TIMESTAMP
                                          ELSE l.updated_at END
                    FROM (VALUES %s) AS v(id, code, docs, signed_at, now)
                    WHERE l.id = v.id AND l.signed_at IS NULL
                """, ok, template="(%s::uuid, %s, %s::jsonb, %s::timestamptz, %s::timestamptz)",
                    page_size=WRITE_CHUNK)
            if errors:
                execute_values(cur, """
                    UPDATE dial_list_leads l
                    SET sign_error = v.error,
                        sign_checked_at = CASE WHEN v.systemic THEN l.sign_checked_at ELSE v.now END
                    FROM (VALUES %s) AS v(id, error, systemic, now)
                    WHERE l.id = v.id AND l.signed_at IS NULL
                """, errors, template="(%s::uuid, %s, %s::boolean, %s::timestamptz)", page_size=WRITE_CHUNK)

    # ------------------------------------------------------------ успешки
    # Сообщение сети «абонент недоступен», плечо на котором положил сам телефон
    # (dial_list_attempts.auto_hangup 'announcement'), — не разговор даже при ANSWER от
    # Binotel с длительностью (автоответчик): успешку за него не даём (владелец,
    # 12.10.2026; то же условие — service.ATTEMPT_TALK_SQL и analytics._LEADS_CTE).
    _RESOLVE_SQL = """
        WITH cand AS (
            SELECT l.id, l.signed_at
            FROM dial_list_leads l
            WHERE l.signed_at IS NOT NULL AND l.success_resolved_at IS NULL
              {lead_filter}
              {test_filter}
              AND NOT EXISTS (
                  SELECT 1 FROM dial_list_assignments a
                  JOIN dial_list_attempts t ON t.assignment_id = a.id
                  WHERE a.lead_id = l.id
                    AND t.state IN %(in_flight)s
                    AND t.requested_at <= l.signed_at
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
              AND t.requested_at <= c.signed_at
            ORDER BY c.id, t.requested_at DESC
        )
        UPDATE dial_list_leads l
        SET success_attempt_id = b.attempt_id,
            success_operator_id = b.operator_id,
            success_resolved_at = %(now)s
        FROM cand c
        LEFT JOIN best b ON b.lead_id = c.id
        WHERE l.id = c.id
        RETURNING l.id, b.attempt_id
    """

    def resolve_successes(self, now=None, lead_ids=None):
        """Подписавшим, по кому решение ещё не принято, — кому успешка. Возвращает
        число засчитанных успешек (подписавшие сами тоже закрываются, но без неё)."""
        now = now or _utcnow()
        params = {"in_flight": IN_FLIGHT_STATES, "answered": ANSWERED_SQL,
                  "min_sec": signing.SUCCESS_MIN_BILLSEC, "now": now,
                  "stale_before": now - timedelta(minutes=IN_FLIGHT_MAX_AGE_MINUTES)}
        lead_filter = ""
        if lead_ids is not None:
            params["lead_ids"] = [str(x) for x in lead_ids]
            lead_filter = "AND l.id = ANY(%(lead_ids)s::uuid[])"
        # Успешка за номер из реестра тестовых (test_numbers) не засчитывается: это
        # проверка обзвона, а не водитель.
        test_filter = "AND " + test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL
        with self.db._get_cursor() as cur:
            cur.execute(self._RESOLVE_SQL.format(lead_filter=lead_filter, test_filter=test_filter),
                        params)
            rows = cur.fetchall()
        credited = sum(1 for r in rows if r[1] is not None)
        if rows:
            log.info("dial_list: по подписавшим принято решение: %d, из них успешек %d", len(rows), credited)
        return credited

    # ------------------------------------------------------------ снять со списков
    def close_signed_rows(self, lead_ids):
        """Строки подписавших, выданные операторам и не набираемые сейчас, — закрыть,
        как при «Исключить». Идущий звонок не трогаем: он закроет строку сам."""
        ids = [str(x) for x in (lead_ids or [])]
        if not ids:
            return 0
        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_assignments a
                SET state = 'done', result = 'other', done_at = CURRENT_TIMESTAMP
                WHERE a.state = 'issued' AND a.lead_id = ANY(%s::uuid[])
                  AND NOT EXISTS (SELECT 1 FROM dial_list_attempts t
                                  WHERE t.assignment_id = a.id AND t.state IN %s)
                RETURNING a.portion_id
            """, (ids, IN_FLIGHT_STATES))
            closed = [str(r[0]) for r in cur.fetchall()]
            portions = sorted(set(closed))
            if portions:
                # Отдельным запросом: в одном UPDATE с CTE подзапрос видел бы
                # строки ещё выданными, и выдача не закрылась бы.
                cur.execute("""
                    UPDATE dial_list_portions p SET closed_at = CURRENT_TIMESTAMP
                    WHERE p.id = ANY(%s::uuid[]) AND p.closed_at IS NULL
                      AND NOT EXISTS (SELECT 1 FROM dial_list_assignments x
                                      WHERE x.portion_id = p.id AND x.state = 'issued')
                """, (portions,))
        if closed:
            log.info("dial_list: подписавшие сняты со списков операторов: строк %d, выдач %d",
                     len(closed), len(portions))
        return len(closed)
