# -*- coding: utf-8 -*-
"""Проверка подписания документов по базе обзвона (Sapar) и засчитывание успешек.

Правила — в signing.py, сеть — в crm/sapar.py, здесь порядок работы:

  1. Кого и как часто. Круг: неподписавшие водители с ИИН из баз текущего и
     прошлого месяца (база месяца — month_base), все подряд и без перерывов, в
     PASS_LANES потоков. Так с 10.11.2026 (владелец: «проверять только тех, кто
     в списке, способом, который показывает статусы точно, и максимально
     быстро»); до этого каждого спрашивали раз в 3 часа одним потоком. Точно
     все статусы отдаёт только поимённая ручка Sapar: его общий список парка
     не отличает «на проверке у Яндекса» от «не подписал» (сверка 10.11.2026).
     Реже, раз в EXPIRED_RECHECK_HOURS, и только в базе текущего месяца
     спрашиваем тех, у кого срок подписания истёк: подписать такой документ
     уже нельзя. Свежая база идёт в круге первой: у её строк проверки не было.
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

Перед проверкой — ID водителей из Sapar (driver_ids.py), если в базах есть
водители, о которых его ещё не спрашивали: свежая база получает ID сразу.

Где крутится. Свой поток на ОДНО место ведёт очередь (круг, перепроверка перед
отчётом): в общем пуле бота из четырёх мест он держал бы четверть приложения
(см. заметку про executor_pool). Сами запросы к Sapar идут в его потоках-дорожках
(_ask). Запросы на проверку склеиваются: пока идёт круг, следующий запрос лишь
просит пройти ещё раз после него. Планировщик будит проверку каждые
CHECK_TICK_MINUTES минут — так круги и идут один за другим.

Запись. Строку водителя переписываем, когда у него что-то изменилось, а одно лишь
время проверки — не чаще раза в STAMP_MINUTES: иначе круг переписывал бы десятки
тысяч строк каждые несколько минут ради метки времени. Точное время последнего
ответа Sapar держит память круга (seen_at): по ней строится очередь круга, её же
показывает карточка водителя. Память живёт до перезапуска сервера.

Проверка «сейчас» (check_now, владелец 11.10.2026: «подписавших не выдавать») —
другое дело: её ждёт человек — оператор нажал «Ещё» или «Позвонить». Поэтому
несколько водителей сразу, параллельно, с коротким таймаутом и жёстким сроком;
не успели — выдача идёт как раньше, остальных проверит круг. Sapar не ответил —
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
from . import driver_ids, month_base, signing

log = logging.getLogger(__name__)

# Круг: столько потоков спрашивают Sapar разом, у каждого своё соединение и пауза REQUEST_PAUSE_SEC между
# запросами. Замеры 10.11.2026: запрос с сервера — ~0,125 с (сам Sapar отвечает за 16 мс, остальное — дорога),
# три потока идут ровно втрое быстрее одного, 12 потоков с рабочей машины — 190 запросов в секунду без единого
# отказа. Шесть потоков — ~34 запроса в секунду: 24 тыс. неподписавших списка обходятся за ~12 минут. Больше не
# ставим из-за своего сервера: один запрос — 3–5 мс его процессора, 34 в секунду — уже 0,1–0,2 ядра из одного.
PASS_LANES = 6
# Страховки круга: больше водителей за круг не берём и дольше не идём (сеть повисла мимо таймаутов).
PASS_MAX_LEADS = 100000
PASS_BUDGET_SEC = 3 * 3600
# Одно лишь время проверки (статус и документы прежние) переписываем в строке водителя не чаще раза в столько
# минут — см. «Запись» в шапке.
STAMP_MINUTES = 30
# Запас к «раз в 3 часа» у истёкших: проверенный в 12:05 идёт в круг около 14:50, а не после 15:05.
EXPIRED_SLACK_MINUTES = 15
# Оценка конца круга по его скорости — не раньше, чем он проработал столько секунд (до этого — по прошлому кругу).
CYCLE_ETA_MIN_SEC = 20
# Sapar лежит: после стольких сетевых сбоев подряд круг прекращается.
MAX_SYSTEMIC_ERRORS_IN_ROW = 5
REQUEST_TIMEOUT_SEC = 10
# Пауза между запросами одного потока: вместе с дорогой до Sapar это ~6 запросов в секунду на поток.
REQUEST_PAUSE_SEC = 0.05
WRITE_CHUNK = 200
# Звонок в «живом» состоянии старше этого — зависший (телефон выключен, reconcile
# не бежал), ждать его исхода для засчитывания успешки не нужно.
IN_FLIGHT_MAX_AGE_MINUTES = 90
# Проверка «сейчас» упёрлась в лежащий Sapar — столько секунд следующие не пробуем.
NOW_BACKOFF_SEC = 30
# Перепроверка перед отчётом о подписании (владелец, 20.10.2026: «до отправки показателей все данные
# самые актуальные — начать собирать примерно за 30 минут до рассылки»). С быстрым кругом ей остаются те, кого
# круг не спросил за последние минуты (сервер перезапускался, база выросла), — теми же потоками, к сроку.
REFRESH_LANES = PASS_LANES
REFRESH_MAX_LEADS = 60000
# ID водителя (driver_ids.py): Sapar не отдал список — следующая попытка не раньше: пока массовая ручка
# лежит, каждый прогон ждал бы её таймаута.
IDS_RETRY_SEC = 2 * 3600

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
    (прогон таймаут не передаёт — у него свой, REQUEST_TIMEOUT_SEC). `fetch_ids(month,
    year, wanted=…)` — список ID водителей (driver_ids.py), по умолчанию
    crm.sapar.driver_yandex_ids."""

    def __init__(self, db, fetch=None, configured=None, sleep=time.sleep, fetch_ids=None):
        self.db = db
        self._fetch = fetch or _default_fetch
        self._fetch_ids = fetch_ids or sapar.driver_yandex_ids
        self._configured = configured or sapar.configured
        self._sleep = sleep
        self.last_ids_sync = None
        # До какого момента (time.monotonic) сверку ID не пробуем: Sapar не отдал список.
        self._ids_retry_at = 0.0
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
        # Память круга: id лида → когда Sapar последний раз по нему ответил (см. «Запись» в шапке).
        self._seen = {}
        # Круг для отсчёта в «Аналитике» (cycle_state): идёт ли, сколько водителей в нём и сколько спрошено,
        # когда закончился последний полный и сколько он шёл (duration_sec на leads водителей).
        self._cycle = {"running": False, "started_at": None, "total": 0, "done": 0,
                       "finished_at": None, "duration_sec": 0.0, "leads": 0}

    def configured(self):
        """Есть ли доступ к Sapar (SAPAR_API в окружении сервера)."""
        return bool(self._configured())

    # ------------------------------------------------------------ очередь
    def request(self, reason=""):
        """Поставить круг в свой поток, не дожидаясь его. Идёт круг — после него
        пройти ещё раз: так круги и идут один за другим (планировщик зовёт сюда каждые
        CHECK_TICK_MINUTES минут), а новая база, пришедшая посреди круга, не ждёт."""
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

    # ------------------------------------------------------------ круг
    _DUE_SQL = """
        SELECT l.id, l.iin, l.period
        FROM dial_list_leads l
        WHERE l.iin <> '' AND l.signed_at IS NULL
          {due}
          {scope}
        ORDER BY l.sign_checked_at ASC NULLS FIRST, l.created_at ASC
        LIMIT %(limit)s
    """
    # Кому не пора: у кого срок подписания истёк. Подписать такой документ уже нельзя — спрашиваем по-старому, раз
    # в EXPIRED_RECHECK_HOURS (вдруг Sapar срок продлит), и только в базе текущего месяца; в базе прошлого месяца
    # истёкшие уже не изменятся — там круг ждёт лишь подписи Яндекса у тех, кто «на проверке».
    _EXPIRED_DUE_SQL = ("AND (l.sign_status <> %(expired)s OR (l.period = %(to_period)s AND "
                        "(l.sign_checked_at IS NULL OR l.sign_checked_at < %(expired_before)s)))")

    def run(self, reason="", now=None, lead_ids=None, force=False, budget_sec=None, lanes=PASS_LANES):
        """Круг: спросить Sapar о неподписавших баз текущего и прошлого месяца (кого именно — шапка модуля и
        _DUE_SQL) в `lanes` потоков. lead_ids — только об этих, из базы любого месяца, одним потоком («Проверить
        сейчас» в карточке); force — и об истёкших, не глядя на время их прошлой проверки. `now` задают тесты:
        тогда им помечены все записи; без него у каждого ответа Sapar своё время. Возвращает сводку."""
        summary = {"reason": reason, "checked": 0, "signed": 0, "errors": 0, "stopped": "",
                   "successes": 0, "closed_rows": 0}
        cycle = lead_ids is None
        if not self.configured():
            log.warning("dial_list: проверка подписания пропущена — нет доступа к Sapar (SAPAR_API)")
            summary["stopped"] = "not_configured"
            self.last_run = summary
            return summary
        fixed_now = now
        now = now or _utcnow()
        if cycle:
            # ID водителя — до проверки подписания: свежая база получает их сразу, а не после круга по ней.
            self.sync_driver_ids(now)
        from_period, to_period = signing.check_window(now.astimezone(signing.PERIOD_TZ).date())
        params = {"from_period": from_period, "to_period": to_period, "limit": PASS_MAX_LEADS,
                  "expired": signing.EXPIRED,
                  "expired_before": now - timedelta(hours=signing.EXPIRED_RECHECK_HOURS)
                  + timedelta(minutes=EXPIRED_SLACK_MINUTES)}
        due_filter = "" if force else self._EXPIRED_DUE_SQL
        if cycle:
            # Исключённых не из списка месяца (month_base, 10.11.2026) не проверяем: их нет в базе месяца, а это
            # тысячи лишних запросов в Sapar. «Проверить сейчас» по водителю (lead_ids) — для любого.
            scope = ("AND l.period BETWEEN %(from_period)s AND %(to_period)s AND "
                     + month_base.in_month_base_sql("l"))
        else:
            params["lead_ids"] = [str(x) for x in lead_ids]
            scope = "AND l.id = ANY(%(lead_ids)s::uuid[])"
            if not params["lead_ids"]:
                return summary
        with self.db._get_cursor() as cur:
            cur.execute(self._DUE_SQL.format(due=due_filter, scope=scope), params)
            due = cur.fetchall()
        if cycle:
            due = self._cycle_begin(due, now)
        # (ИИН, месяц документов) → строки: один водитель в двух базах одного
        # месяца документов (разные отделы) — один запрос.
        groups = OrderedDict()
        for lead_id, iin, period in due:
            groups.setdefault((str(iin), signing.doc_month_for(period)), []).append(str(lead_id))

        started = time.monotonic()
        try:
            # «Проверить сейчас» в карточке — время проверки пишем всегда: человек только что спросил сам.
            newly_signed = self._ask(groups, summary, fixed_now, lanes=lanes if cycle else 1,
                                     end=started + (PASS_BUDGET_SEC if budget_sec is None else budget_sec),
                                     end_reason="budget", cycle=cycle, sparse=cycle)
        except Exception:
            summary["stopped"] = summary["stopped"] or "error"
            raise
        finally:
            summary["elapsed_sec"] = round(time.monotonic() - started, 1)
            if cycle:
                self._cycle_end(summary, len(due), fixed_now or _utcnow())
        summary["signed"] = len(newly_signed)
        summary["closed_rows"] = self.close_signed_rows(newly_signed)
        summary["successes"] = self.resolve_successes(fixed_now or _utcnow())
        if due or summary["stopped"]:
            log.info("dial_list: проверка подписания (%s): к проверке %d, проверено %d, подписали %d, "
                     "успешек %d, снято со списков %d, ошибок %d, потоков %d, за %.0f с%s", reason, len(due),
                     summary["checked"], summary["signed"], summary["successes"], summary["closed_rows"],
                     summary["errors"], summary["lanes"], summary["elapsed_sec"],
                     f", остановлена: {summary['stopped']}" if summary["stopped"] else "")
        if cycle:
            self.last_run = summary
            if summary["stopped"] == "refresh":
                # Уступили перепроверке перед отчётом — после неё круг продолжается сам, не дожидаясь планировщика.
                with self._lock:
                    self._again = True
        return summary

    def _cycle_begin(self, due, now):
        """Начало круга → `due` в порядке «кого дольше всех не спрашивали». В строке базы время проверки
        переписывается не каждый круг, точное помнит память круга: о ком она не знает (сервер перезапускался,
        новая база) — первыми, в порядке запроса (по времени из базы), остальные — по времени из памяти.
        Заодно память забывает тех, кого в круге уже нет (подписали, исключены), а счётчик круга обнуляется."""
        seen = self._seen
        self._seen = seen = {lid: seen[lid] for lid in (str(row[0]) for row in due) if lid in seen}
        never = datetime.min.replace(tzinfo=timezone.utc)
        due = sorted(due, key=lambda row: seen.get(str(row[0]), never))     # сортировка устойчивая
        self._cycle.update(running=True, started_at=now, total=len(due), done=0)
        return due

    def _cycle_end(self, summary, leads, now):
        """Конец круга. Полным считается круг, спросивший всех: его конец — «обновлено» в «Аналитике», его
        длительность — оценка следующего. Остановленный (Sapar лежит, уступил перепроверке) ничего не меняет."""
        self._cycle["running"] = False
        if not summary["stopped"]:
            self._cycle.update(finished_at=now, duration_sec=summary["elapsed_sec"], leads=leads)

    def cycle_state(self, now=None):
        """Круг для отсчёта в «Аналитике» → {running, total, done, finished_at, eta_at}: идёт ли, сколько в нём
        водителей и сколько спрошено, когда закончился последний полный круг и когда закончится следующий.
        Оценка конца: идёт круг — по его скорости (в первые секунды — по длительности прошлого); круг вот-вот
        начнётся (поток проверки занят: пауза между кругами, перепроверка перед отчётом) — длительность прошлого
        от сейчас; поток свободен — она же от ближайшего запуска планировщика. Неоткуда оценить — None.
        Моменты — aware."""
        now = now or _utcnow()
        cycle = dict(self._cycle)
        expected = None      # сколько секунд займёт круг — по прошлому полному, с поправкой на число водителей
        if cycle["leads"] and cycle["duration_sec"]:
            leads = cycle["total"] if cycle["running"] else cycle["leads"]
            expected = timedelta(seconds=cycle["duration_sec"] * leads / cycle["leads"])
        eta = None
        if cycle["running"] and cycle["started_at"] is not None:
            elapsed = (now - cycle["started_at"]).total_seconds()
            if cycle["done"] and elapsed >= CYCLE_ETA_MIN_SEC:
                eta = now + timedelta(seconds=max(0, cycle["total"] - cycle["done"]) * elapsed / cycle["done"])
            elif expected is not None:
                eta = max(now, cycle["started_at"] + expected)
        elif self._running:
            eta = now + expected if expected is not None else None
        else:
            eta = signing.next_check_tick(now) + (expected or timedelta(0))
        return {"running": bool(cycle["running"]), "total": cycle["total"], "done": cycle["done"],
                "finished_at": cycle["finished_at"], "eta_at": eta}

    def seen_at(self, lead_id, stored=None):
        """Когда Sapar последний раз ответил по водителю: позднее из записанного в строке (`stored`) и того, что
        помнит круг. В строке время переписывается не каждый круг (STAMP_MINUTES); память точнее, но живёт до
        перезапуска сервера."""
        seen = self._seen.get(str(lead_id))
        if seen is None or stored is None:
            return seen or stored
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=timezone.utc)
        return max(stored, seen)

    # ------------------------------------------------------------ запросы к Sapar
    def _ask(self, groups, summary, fixed_now, *, lanes, end, end_reason, cycle=False, sparse=True):
        """Спросить Sapar обо всех `groups` {(ИИН, (месяц, год) документов): [id лидов]} в `lanes` потоков и
        записать ответы пачками (_write; sparse — см. там). У каждого потока свой requests.Session и пауза
        между запросами. `end` (time.monotonic) — после него новых запросов нет; что осталось неспрошенным —
        summary["stopped"] = end_reason. MAX_SYSTEMIC_ERRORS_IN_ROW сетевых сбоев подряд — Sapar лежит,
        остановка. cycle — это круг: ведём его счётчик и уступаем перепроверке перед отчётом, как только её
        поставили (у неё срок, а круг потом продолжится). fixed_now — время для всех записей (тесты); без него у
        каждого ответа своё. Наполняет summary (checked, errors, lanes, stopped) → id подписавших."""
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

        threads = [threading.Thread(target=lane, name="dial-sign-lane-%d" % i, daemon=True)
                   for i in range(lanes)] if groups else []
        summary["lanes"] = len(threads)
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
                at = fixed_now or _utcnow()
                if cycle:
                    self._cycle["done"] += len(ids)
                if not result.get("ok"):
                    summary["errors"] += len(ids)
                    error = str(result.get("error") or "Sapar не ответил")[:300]
                    pending.extend(("error", lid, error, bool(result.get("systemic")), at) for lid in ids)
                    if result.get("systemic"):
                        systemic_in_row += 1
                        if systemic_in_row >= MAX_SYSTEMIC_ERRORS_IN_ROW and not stop.is_set():
                            summary["stopped"] = "sapar_unavailable"
                            stop.set()
                    continue
                systemic_in_row = 0
                code, docs_json, signed_at = _package_verdict(result.get("yandex") or [], at)
                for lid in ids:
                    pending.append(("ok", lid, code, docs_json, signed_at, at))
                    summary["checked"] += 1
                    self._seen[lid] = at
                    if signed_at is not None:
                        newly_signed.append(lid)
                if len(pending) >= WRITE_CHUNK:
                    self._write(pending, at, sparse)
                    pending = []
                if cycle and self._refresh is not None and not stop.is_set():
                    summary["stopped"] = "refresh"
                    stop.set()
        finally:
            stop.set()
            if pending:
                self._write(pending, fixed_now or _utcnow(), sparse)
        if not summary["stopped"] and not work.empty():
            summary["stopped"] = end_reason
        return newly_signed

    # ------------------------------------------------------------ ID водителя
    def sync_driver_ids(self, now=None, force=False):
        """ID водителей баз из Sapar (driver_ids.py), если есть кого спросить. Падение сверки прогон
        подписания не останавливает. → сводка driver_ids.sync или None (спрашивать некого / ждём)."""
        if not force and time.monotonic() < self._ids_retry_at:
            return None
        now = now or _utcnow()
        try:
            summary = driver_ids.sync(self.db, self._fetch_ids, now.astimezone(signing.PERIOD_TZ).date(),
                                      force=force)
        except Exception as exc:
            log.exception("dial_list: сверка ID водителей с Sapar упала")
            summary = {"ok": False, "error": str(exc)[:300]}
        if summary is not None:
            self.last_ids_sync = summary
            if not summary.get("ok"):
                self._ids_retry_at = time.monotonic() + IDS_RETRY_SEC
        return summary

    # ------------------------------------------------------------ перепроверка перед отчётом
    # Только база месяца (month_base, 10.11.2026): исключённых не из списка месяца нет и в отчёте, перепроверять
    # их к рассылке — лишние тысячи запросов в те же полчаса.
    _REFRESH_SQL = """
        SELECT l.id, l.iin, l.period
        FROM dial_list_leads l
        WHERE l.iin <> '' AND l.signed_at IS NULL
          AND (l.sign_checked_at IS NULL OR l.sign_checked_at < %(fresh_before)s)
          AND ({bases})
          AND """ + month_base.in_month_base_sql("l") + """
        ORDER BY l.sign_checked_at ASC NULLS FIRST, l.created_at ASC
        LIMIT %(limit)s
    """

    def refresh(self, bases, fresh_before, deadline, reason="", now=None, lanes=REFRESH_LANES):
        """Перепроверить к отчёту неподписавших баз `bases` [(department_id, period)], кого не спрашивали
        после fresh_before (момент, aware): в отчёте статусы на время рассылки. deadline (aware) — после него
        новых запросов нет, отчёт уходит с тем, что успели. Как круг: один запрос на пару (ИИН, месяц
        документов) в `lanes` потоков, итог пачками (_write), подписавшие снимаются со списков, по ним решается
        успешка; Sapar лежит — остановка. Круг идёт каждые несколько минут, поэтому обычно спрашивать почти
        некого: работа есть после перезапуска сервера и когда база больше, чем круг успевает обойти."""
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
        fixed_now = now
        now = now or _utcnow()
        params = {"fresh_before": fresh_before, "limit": REFRESH_MAX_LEADS}
        clauses = []
        for index, (department_id, period) in enumerate(bases):
            params["d%d" % index], params["p%d" % index] = department_id, period
            clauses.append("(l.department_id = %%(d%d)s AND l.period = %%(p%d)s)" % (index, index))
        with self.db._get_cursor() as cur:
            cur.execute(self._REFRESH_SQL.format(bases=" OR ".join(clauses)), params)
            due = cur.fetchall()
        # В строке время проверки переписывается не каждый круг (STAMP_MINUTES) — кого круг на самом деле
        # спросил после fresh_before, знает его память.
        seen = self._seen
        due = [row for row in due if str(row[0]) not in seen or seen[str(row[0])] < fresh_before]
        summary["due"] = len(due)
        groups = OrderedDict()
        for lead_id, iin, period in due:
            groups.setdefault((str(iin), signing.doc_month_for(period)), []).append(str(lead_id))

        started = time.monotonic()
        newly_signed = self._ask(groups, summary, fixed_now, lanes=lanes,
                                 end=started + max(0.0, (deadline - now).total_seconds()), end_reason="deadline")
        summary["signed"] = len(newly_signed)
        summary["closed_rows"] = self.close_signed_rows(newly_signed)
        summary["successes"] = self.resolve_successes(fixed_now or _utcnow())
        summary["elapsed_sec"] = round(time.monotonic() - started, 1)
        log.info("dial_list: перепроверка подписания перед отчётом (%s): к проверке %d, проверено %d, подписали %d, "
                 "успешек %d, снято со списков %d, ошибок %d, потоков %d, за %.0f с%s", reason, len(due),
                 summary["checked"], summary["signed"], summary["successes"], summary["closed_rows"],
                 summary["errors"], summary["lanes"], summary["elapsed_sec"],
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
                    self._seen[lid] = now
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

    # Запись круга (sparse): строку переписываем, только если есть что писать, — водитель подписал, сменился
    # статус или документы, была ошибка, проверяли впервые — либо времени проверки в строке уже STAMP_MINUTES.
    # Круг спрашивает каждого раз в несколько минут, и почти всегда ответ тот же: без этого условия он переписывал
    # бы все строки базы каждый круг ради одной метки времени.
    _SPARSE_OK_SQL = """
                      AND (v.signed_at IS NOT NULL OR l.sign_status <> v.code OR l.sign_docs <> v.docs
                           OR l.sign_error <> '' OR l.sign_checked_at IS NULL
                           OR l.sign_checked_at < v.now - make_interval(mins => {minutes}))
    """.format(minutes=STAMP_MINUTES)
    _SPARSE_ERROR_SQL = """
                      AND (l.sign_error <> v.error OR (NOT v.systemic AND (l.sign_checked_at IS NULL
                           OR l.sign_checked_at < v.now - make_interval(mins => {minutes}))))
    """.format(minutes=STAMP_MINUTES)

    def _write(self, items, now, sparse=False):
        """Ответы Sapar → строки лидов. Запись — ("ok", id, статус, документы, момент подписи) или ("error", id,
        текст, сетевой ли сбой); последним может идти время ответа, без него — `now`. Подписавшего не трогаем
        повторно (signed_at только ставится). Сетевой сбой не сдвигает время проверки; отказ Sapar по конкретному
        ИИН — сдвигает. sparse — запись круга: только если есть что писать (_SPARSE_OK_SQL). Проверка «сейчас» и
        «Проверить сейчас» в карточке пишут время всегда: по нему видно, что человек только что спросил."""
        ok = [(i[1], i[2], i[3], i[4], i[5] if len(i) > 5 else now) for i in items if i[0] == "ok"]
        errors = [(i[1], i[2], i[3], i[4] if len(i) > 4 else now) for i in items if i[0] == "error"]
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
                """ + (self._SPARSE_OK_SQL if sparse else ""), ok,
                    template="(%s::uuid, %s, %s::jsonb, %s::timestamptz, %s::timestamptz)", page_size=WRITE_CHUNK)
            if errors:
                execute_values(cur, """
                    UPDATE dial_list_leads l
                    SET sign_error = v.error,
                        sign_checked_at = CASE WHEN v.systemic THEN l.sign_checked_at ELSE v.now END
                    FROM (VALUES %s) AS v(id, error, systemic, now)
                    WHERE l.id = v.id AND l.signed_at IS NULL
                """ + (self._SPARSE_ERROR_SQL if sparse else ""), errors,
                    template="(%s::uuid, %s, %s::boolean, %s::timestamptz)", page_size=WRITE_CHUNK)

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
