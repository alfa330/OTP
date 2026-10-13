# -*- coding: utf-8 -*-
"""Живые звонки обзвона у Binotel: один опрос stats/call-details на компанию и общий кэш.

Зачем (прод, 12.10.2026). call-details спрашивали по каждому поводу отдельно: карточка звонка
(/live) — каждые 3 с на каждый идущий звонок, добор исходов — на каждом GET состояния,
проверка после событий телефона, вопрос автосброса «класть ли плечо». Binotel отвечал
«Requests are too frequent» (код 106, «повторите через 1–4 с») почти непрерывно: ответ на
вопрос автосброса не успевал за пять секунд телефона (оборвано 52 разговора), исходы
приходили через 5–50 с, и всё это время попытка держала следующий звонок оператора.

Теперь у каждой компании Binotel (одного ключа API) — один опрос:
  * один запрос stats/call-details сразу на ВСЕ живые попытки компании (requested,
    leg_ringing, leg_answered, ended — моложе ATTEMPT_STALE_MINUTES) и не чаще REFRESH_SEC:
    сколько бы операторов ни звонили, это не больше одного запроса в три секунды;
  * ответ ложится в общий кэш процесса, и его читают все: карточки звонков всех
    операторов, добор исходов, события телефона, автосброс, вебхук дописывает в него финал;
  * обновление ленивое и одно на компанию (single-flight): его запускает тот, кому нужны
    данные свежее имеющихся, а все остальные ждут его не дольше WAIT_SEC — и дальше
    обходятся тем, что уже знают. Binotel просит паузу (RateLimited) — до её конца новых
    запросов нет, читатели получают прежние данные.

Почему лениво, а не постоянным фоновым потоком. Опрос нужен, только пока кто-то звонит, а
спрашивающие и так приходят каждые три секунды (карточка звонка каждого оператора); в
тишине ленивое обновление не делает ни одного запроса. Поток-планировщик пришлось бы
запускать, останавливать и будить, следить за ним при перезапусках waitress, а тестам —
выключать. При этом сам запрос к Binotel идёт не в потоке спрашивающего, а в своём (один
на компанию): медленный Binotel не держит поток waitress дольше, чем спрашивающий согласен
ждать, — читатель просто уходит с тем, что есть в кэше. У запроса опроса свой короткий таймаут
HTTP (HTTP_TIMEOUT, а не 40 с клиента), а зависшее дольше STUCK_SEC обновление бросается: пока
оно шло, новое не начиналось, и финалы всех операторов компании стояли (ревью 13.10.2026).

Модуль ничего не знает о базе и правилах обзвона: какие звонки спрашивать и что делать с
ответом, решает сервис (dial_list.service) — он передаёт fetch.
"""
import logging
import threading
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from binotel import client as binotel

log = logging.getLogger(__name__)

# Не чаще раза в столько секунд один запрос call-details на компанию (обычный темп).
REFRESH_SEC = 3.0
# Сколько спрашивающий ждёт идущего обновления (своего или чужого), прежде чем взять кэш.
WAIT_SEC = 1.5
# Сколько хранится запись о звонке, который больше никто не спрашивает.
KEEP_SEC = 600.0
# Больше стольких звонков одним запросом не спрашиваем (самые свежие — первыми).
BATCH_MAX = 50
# Отказ Binotel «слишком часто» пишем в журнал не чаще раза в столько секунд на компанию.
RATE_LOG_EVERY_SEC = 60.0
# Таймаут HTTP запроса опроса (соединение, чтение), а не 40 с клиента Binotel (ревью 13.10.2026):
# опрос у компании один, и пока зависший запрос не кончится, не начнётся следующий — финалы всех
# операторов компании стоят (сервис закрывает их после каждого ответа).
HTTP_TIMEOUT = (3.05, 5.0)
# Обновление, идущее дольше этого, считаем зависшим: следующий спрашивающий начинает новое в
# свежем потоке, не дожидаясь старого (тот допишет кэш, только если его данные новее).
STUCK_SEC = 10.0

# Что известно о звонке: call — звонок из ответа клиента Binotel (dict, только CALL_FIELDS) либо
# None — Binotel звонка не отдал (ещё не начался или уже не найден); wall — когда данные получены
# (UTC, часы приложения; момент ОТПРАВКИ запроса — нижняя граница, «данные не раньше чем»);
# mono — тот же момент по time.monotonic, по нему считается возраст данных.
Seen = namedtuple("Seen", "call wall mono")
# Что из звонка храним. Номер водителя (external_number) и данные сотрудника в кэш не кладём:
# исходу они не нужны, а память процесса — не место для номеров.
CALL_FIELDS = ("general_call_id", "disposition", "billsec", "waitsec")


def _utcnow():
    return datetime.now(timezone.utc)


def _slim(call):
    """Звонок → только CALL_FIELDS; не словарь — None."""
    if not isinstance(call, dict):
        return None
    return {key: call[key] for key in CALL_FIELDS if key in call}


class CompanyCalls:
    """Опрос call-details одной компании Binotel и его кэш. Потокобезопасен.

    submit(fn) — где выполнить обновление; по умолчанию свой поток компании
    (ThreadPoolExecutor на одного работника). Тесты подставляют синхронный запуск."""

    def __init__(self, company, submit=None, clock=time.monotonic, wall_clock=_utcnow):
        self.company = str(company or "")
        self._lock = threading.Lock()
        self._entries = {}           # generalCallID -> Seen
        self._inflight = None        # threading.Event идущего обновления
        self._inflight_started = None  # monotonic: когда начато идущее обновление
        self._last_start = None      # monotonic: когда начато последнее обновление
        self._not_before = 0.0       # monotonic: раньше этого Binotel просил не спрашивать
        self._submit = submit
        self._executor = None
        self._clock = clock
        self._wall = wall_clock
        self._rate_logged_at = None
        # Сколько запросов call-details ушло от этого кэша (журнал, тесты).
        self.requests = 0

    # ------------------------------------------------------------ чтение и запись
    def peek(self, general_call_id):
        """Что уже известно о звонке (Seen) — без запросов; None — ничего."""
        with self._lock:
            return self._entries.get(str(general_call_id))

    def put(self, general_call_id, call):
        """Финал звонка, известный не из опроса (вебхук «API Call Completed»): в кэш как
        свежие данные — карточка звонка и добор исходов увидят его сразу."""
        gid = str(general_call_id or "").strip()
        if not gid:
            return
        with self._lock:
            self._entries[gid] = Seen(_slim(call or {}), self._wall(), self._clock())

    # ------------------------------------------------------------ главное
    def lookup(self, general_call_ids, fetch, max_age=REFRESH_SEC, newer_than=None, wait=WAIT_SEC,
               min_interval=REFRESH_SEC):
        """{generalCallID: Seen} по этим звонкам — то, что есть в кэше после (возможного)
        обновления; о ком ничего не известно, того в ответе нет.

        Обновление запускается, если хоть по одному звонку данных нет, они старше max_age
        секунд или получены раньше newer_than (UTC; момент в будущем не в счёт — данных
        «после него» ещё быть не может). Не запускается, пока не прошло min_interval секунд
        от начала прошлого обновления и пока не кончилась пауза, которую попросил Binotel, —
        тогда ответ из того, что есть. Идёт чужое обновление — ждём его, второго не запускаем.
        Ждём всего не дольше wait секунд.

        fetch(wanted) — сделать один запрос: wanted — эти звонки (их надо спросить обязательно);
        возвращает (спрошенные id, {id: звонок}, after) — after (или None) выполняется после
        того, как ответ лёг в кэш и ждущие отпущены (сервис закрывает в нём попытки с финалом).
        Бросает binotel.RateLimited — Binotel просит паузу; прочие ошибки — «данных нет»."""
        wanted = list(dict.fromkeys(str(g).strip() for g in (general_call_ids or ()) if str(g or "").strip()))
        if not wanted:
            return {}
        deadline = self._clock() + max(0.0, float(wait))
        abandoned = None
        with self._lock:
            now, wall_now = self._clock(), self._wall()
            if not self._stale(wanted, now, wall_now, max_age, newer_than):
                return self._pick(wanted)
            event = self._inflight
            if (event is not None and self._inflight_started is not None
                    and now - self._inflight_started > STUCK_SEC):
                # Обновление зависло (сеть, DNS, база): бросаем его — ждущих отпускаем, новое
                # пойдёт в свежем потоке, а не в очередь за зависшим (работник у компании один).
                abandoned, event = self._abandon(event, now), None
            start = False
            if event is None and self._may_start(now, min_interval):
                event = self._inflight = threading.Event()
                self._inflight_started = now
                self._last_start = now
                start = True
        if abandoned is not None:
            abandoned.shutdown(wait=False)
        if start:
            self._run(fetch, wanted, event)
        if event is not None:
            left = deadline - self._clock()
            if left > 0:
                event.wait(left)
        with self._lock:
            return self._pick(wanted)

    # ------------------------------------------------------------ внутреннее
    def _stale(self, wanted, now, wall_now, max_age, newer_than):
        """Нужно ли обновление ради этих звонков. Под self._lock."""
        for gid in wanted:
            seen = self._entries.get(gid)
            if seen is None or now - seen.mono > max_age:
                return True
            if newer_than is not None and seen.wall < newer_than <= wall_now:
                return True
        return False

    def _may_start(self, now, min_interval):
        """Можно ли начать обновление сейчас: темп и пауза Binotel. Под self._lock."""
        if now < self._not_before:
            return False
        return self._last_start is None or now - self._last_start >= min_interval

    def _pick(self, wanted):
        return {gid: self._entries[gid] for gid in wanted if gid in self._entries}

    def _abandon(self, event, now):
        """Бросить зависшее обновление (под self._lock): ждущих отпустить, исполнитель с зависшим
        потоком отдать вызывающему — закрыть вне замка; следующее обновление создаст свой."""
        log.warning("dial_list: опрос call-details компании %s идёт %.0f с — считаем зависшим, начинаем новый",
                    self.company, now - self._inflight_started)
        event.set()
        self._inflight = None
        self._inflight_started = None
        executor, self._executor = self._executor, None
        return executor

    def _run(self, fetch, wanted, event):
        """Запустить обновление в своём потоке компании; ждущих отпускает event."""
        def work():
            after = None
            try:
                after = self._refresh(fetch, wanted)
            finally:
                with self._lock:
                    if self._inflight is event:
                        self._inflight = None
                        self._inflight_started = None
                event.set()
            if after is not None:
                try:
                    after()
                except Exception:
                    log.exception("dial_list: разбор ответа call-details компании %s упал", self.company)

        try:
            (self._submit or self._submit_default)(work)
        except Exception:
            # Поток не запустился (исполнитель закрыт при остановке) — сделаем здесь же.
            log.exception("dial_list: поток опроса call-details компании %s не запустился", self.company)
            work()

    def _submit_default(self, fn):
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"dial-calls-{self.company}")
            executor = self._executor
        executor.submit(fn)

    def _refresh(self, fetch, wanted):
        """Один запрос и его ответ — в кэш. Возвращает after из fetch (или None)."""
        sent_wall, sent_mono = self._wall(), self._clock()
        try:
            asked, details, after = fetch(wanted)
        except binotel.RateLimited as exc:
            with self._lock:
                self._not_before = max(self._not_before, self._clock() + float(exc.retry_after or 0.0))
                quiet = (self._rate_logged_at is not None
                         and self._clock() - self._rate_logged_at < RATE_LOG_EVERY_SEC)
                if not quiet:
                    self._rate_logged_at = self._clock()
            if not quiet:
                log.info("dial_list: Binotel просит паузу %.1f с в call-details (компания %s) — до неё "
                         "живые звонки показываем по последним данным", exc.retry_after, self.company)
            return None
        except Exception as exc:
            log.info("dial_list: опрос call-details компании %s не удался: %s", self.company, exc)
            return None
        details = details or {}
        with self._lock:
            self.requests += 1
            for gid in asked:
                gid = str(gid)
                known = self._entries.get(gid)
                # Новее не затираем: финал вебхука (put) пришёл, пока шёл этот запрос, или ответил
                # раньше новый опрос, начатый вместо зависшего, — их данные позже наших.
                if known is not None and known.mono > sent_mono:
                    continue
                self._entries[gid] = Seen(_slim(details.get(gid)), sent_wall, sent_mono)
            if len(self._entries) > 4 * BATCH_MAX:
                edge = sent_mono - KEEP_SEC
                self._entries = {k: v for k, v in self._entries.items() if v.mono > edge}
        return after
