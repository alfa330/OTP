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
"""
import json
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from psycopg2.extras import execute_values

from crm import sapar
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

ANSWERED_SQL = ("ANSWER", "ANSWERED", "SUCCESS", "VM-SUCCESS")
IN_FLIGHT_STATES = ("requested", "leg_ringing", "leg_answered", "ended")


def _utcnow():
    return datetime.now(timezone.utc)


def _default_fetch(iin, month, year, session=None):
    return sapar.driver_documents(iin, month, year, session=session, timeout=REQUEST_TIMEOUT_SEC)


class SignChecker:
    """Проверка подписания для всего раздела. `fetch(iin, month, year, session)` —
    подменяется в тестах; по умолчанию crm.sapar.driver_documents."""

    def __init__(self, db, fetch=None, configured=None, sleep=time.sleep):
        self.db = db
        self._fetch = fetch or _default_fetch
        self._configured = configured or sapar.configured
        self._sleep = sleep
        self._executor = None
        self._lock = threading.Lock()
        self._running = False
        self._again = False
        self.last_run = None

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

    def _loop(self, reason):
        while True:
            try:
                self.run(reason=reason)
            except Exception:
                log.exception("dial_list: проверка подписания упала (%s)", reason)
            with self._lock:
                if not self._again:
                    self._running = False
                    return
                self._again = False
            reason = "повтор после прогона"

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
        session = None
        try:
            import requests
            session = requests.Session()
        except Exception:  # pragma: no cover — requests есть всегда, но прогон не должен от него зависеть
            session = None
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
                code = signing.aggregate(documents)
                signed_at = signing.signed_moment(documents, now) if code == signing.SIGNED else None
                docs_json = json.dumps(signing.document_rows(documents), ensure_ascii=False)
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
    _RESOLVE_SQL = """
        WITH cand AS (
            SELECT l.id, l.signed_at
            FROM dial_list_leads l
            WHERE l.signed_at IS NOT NULL AND l.success_resolved_at IS NULL
              {lead_filter}
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
        with self.db._get_cursor() as cur:
            cur.execute(self._RESOLVE_SQL.format(lead_filter=lead_filter), params)
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
