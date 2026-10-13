# -*- coding: utf-8 -*-
"""Очередь запросов к Binotel: зазор и пауза — на ключ API, сам запрос — вне замков.

Разбор 12.10.2026. Клиент держал один замок на весь процесс и на всё время
HTTP-запроса, а click-to-call отвечает секунд десять: за ним стояли запросы всех
компаний. Вопрос телефона «класть ли трубку на гудках» не успевал за его 5 секунд, и
за день так оборвали 52 живых разговора. Отказ Binotel «too frequent» при этом
пережидал только получивший его поток, а остальные продолжали стучаться.

Здесь проверяется новое расписание:
  * медленный запрос не держит ни другую компанию, ни лёгкие запросы своей;
  * зазор между отправками — на ключ API;
  * пауза по подсказке Binotel общая для всех потоков и клиентов ключа: на такой же
    запрос — всегда, на весь метод — когда Binotel показал, что лимит у метода общий;
  * max_wait=0 отвечает отказом сразу, а не сном;
  * выборки статистики идут строго по одной на ключ.

Часы в большинстве тестов подменные (_FakeTime): так проверяется сама арифметика
расписания, без ожиданий. Настоящие потоки и настоящее время — там, где проверяется
именно одновременность.
"""

import email.message
import math
import random
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binotel import client as bc  # noqa: E402


TOO_FREQUENT = "Requests are too frequent. You can do this request after %d sec."
DAY = "2026-10-12"


def _ok(**extra):
    payload = {"status": "success"}
    payload.update(extra)
    return payload


def _too_frequent(seconds):
    return {"status": "error", "code": 106, "message": TOO_FREQUENT % seconds}


class _Resp:
    status_code = 200

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class _Session:
    """Подделка requests.Session: отвечает по сценарию и помнит, что и когда спросили.

    answers — ответы по очереди (кончились — «успех»); on_post — что сделать, пока
    запрос «в полёте» (подождать, сдвинуть часы); clock — часы для отметок времени."""

    def __init__(self, answers=None, on_post=None, clock=time.perf_counter):
        self.answers = list(answers or [])
        self.on_post = on_post
        self.clock = clock
        self.calls = []    # (метод API, время отправки)
        self.bodies = []
        self._lock = threading.Lock()

    def post(self, url, json=None, timeout=None):
        endpoint = url.split("/api/4.0/", 1)[-1][:-len(".json")]
        with self._lock:
            self.calls.append((endpoint, self.clock()))
            self.bodies.append(dict(json or {}))
            payload = self.answers.pop(0) if self.answers else _ok()
        if self.on_post:
            self.on_post(endpoint)
        return payload if isinstance(payload, _Resp) else _Resp(payload)

    @property
    def endpoints(self):
        return [endpoint for endpoint, _ in self.calls]

    @property
    def times(self):
        return [moment for _, moment in self.calls]


class _FakeTime:
    """Часы вместо модуля time внутри клиента: sleep двигает время, а не ждёт.

    on_tick — «остальной мир», который живёт, пока поток спит (фоновые опросы): его
    зовут каждые TICK секунд сна, но не в самый миг пробуждения. Проснувшийся поток
    берёт замок сразу, и чужому запросу пришлось бы угадать в те же микросекунды."""

    TICK = 0.1

    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []
        self.on_sleep = None
        self.on_tick = None

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        end = self.now + max(0.0, seconds)
        if self.on_tick:
            while end - self.now > self.TICK:
                self.now += self.TICK
                self.on_tick()
        self.now = end
        if self.on_sleep:
            self.on_sleep(seconds)


class _BinotelModel(_Session):
    """Лимит Binotel на stats/call-details — в двух правдоподобных вариантах.

    per_request=False: метод целиком — один запрос в window секунд на компанию.
    per_request=True:  один и тот же звонок — не чаще раза в window секунд, а разные
                       звонки друг другу не мешают.
    Какой из них настоящий, неизвестно (документация недоступна); клиент обязан
    разумно вести себя при обоих."""

    def __init__(self, fake, per_request, window=5.0):
        super().__init__(clock=fake.monotonic)
        self.fake = fake
        self.per_request = per_request
        self.window = window
        self.taken = {}     # что занято -> когда принят запрос
        self.accepted = 0
        self.rejected = 0

    def post(self, url, json=None, timeout=None):
        ids = (json or {}).get("generalCallID")
        ids = ids if isinstance(ids, list) else [ids]
        marks = [str(i) for i in ids] if self.per_request else ["*"]
        now = self.fake.now
        left = [self.taken[m] + self.window - now for m in marks
                if m in self.taken and now - self.taken[m] < self.window]
        if left:
            self.rejected += 1
            self.answers.append(_too_frequent(max(1, math.ceil(max(left)))))
        else:
            self.accepted += 1
            for mark in marks:
                self.taken[mark] = now
            self.answers.append(_ok(callDetails={}))
        return super().post(url, json=json, timeout=timeout)


class _Poller:
    """Фоновый опрос, как у обзвона: каждый звонок спрашивают раз в `every` секунд
    и ждать не согласны (max_wait=0)."""

    def __init__(self, client, fake, calls, every, seed=20261012):
        self.client = client
        self.fake = fake
        self.every = every
        self.rng = random.Random(seed)
        self.next_at = {call: fake.now + self.rng.uniform(0, every) for call in calls}
        self.answered = {call: 0 for call in calls}
        self._busy = False

    def tick(self):
        if self._busy:
            return
        self._busy = True
        try:
            for call in list(self.next_at):
                if self.fake.now < self.next_at[call]:
                    continue
                self.next_at[call] = self.fake.now + self.every + self.rng.uniform(-0.3, 0.3)
                try:
                    self.client.call_details(call, max_wait=0)
                    self.answered[call] += 1
                except bc.RateLimited:
                    pass
        finally:
            self._busy = False

    def run(self, seconds):
        end = self.fake.now + seconds
        while self.fake.now < end:
            self.tick()
            self.fake.now += _FakeTime.TICK


class _Worker(threading.Thread):
    """Поток с результатом: тест видит и ответ, и исключение."""

    def __init__(self, fn):
        super().__init__(daemon=True)
        self._fn = fn
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self._fn()
        except BaseException as exc:  # noqa: BLE001 — исключение разбирает тест
            self.error = exc


def _spawn(fn):
    worker = _Worker(fn)
    worker.start()
    return worker


def _client(key, session):
    client = bc.BinotelApiClient(key, "secret")
    client.session = session
    return client


class _RateCase(unittest.TestCase):
    def setUp(self):
        bc._reset_rate_state()
        self.addCleanup(bc._reset_rate_state)

    def fake_time(self, start=1000.0):
        """Подменяет часы клиента на управляемые до конца теста."""
        fake = _FakeTime(start)
        patcher = mock.patch.object(bc, "time", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def patch_const(self, **values):
        for name, value in values.items():
            patcher = mock.patch.object(bc, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class SlotSpacingTests(_RateCase):
    """Зазор MIN_REQUEST_INTERVAL между отправками — у каждого ключа свой."""

    def test_requests_of_one_key_are_spaced(self):
        fake = self.fake_time()
        session = _Session(clock=fake.monotonic)
        client = _client("key-a", session)
        for _ in range(3):
            client.call_details("1")
        self.assertEqual(session.times, [1000.0, 1001.0, 1002.0])
        self.assertEqual(fake.sleeps, [1.0, 1.0])

    def test_spacing_is_shared_by_all_clients_and_methods_of_the_key(self):
        # Клиент создают заново почти на каждый вызов (from_config), а методы у ключа
        # общие: зазор обязан держаться между любыми двумя запросами одного ключа.
        fake = self.fake_time()
        session = _Session(clock=fake.monotonic)
        _client("key-a", session).call_details("1")
        _client("key-a", session).hangup_call("1")
        _client("key-a", session).get_call_record_url("1")
        self.assertEqual(session.endpoints,
                         ["stats/call-details", "calls/hangup-call", "stats/call-record"])
        self.assertEqual(session.times, [1000.0, 1001.0, 1002.0])

    def test_another_key_is_not_delayed(self):
        fake = self.fake_time()
        session = _Session(clock=fake.monotonic)
        _client("key-tez", session).call_details("1")
        _client("key-remote", session).call_details("1")
        _client("key-third", session).hangup_call("1")
        self.assertEqual(session.times, [1000.0, 1000.0, 1000.0])
        self.assertEqual(fake.sleeps, [])

    def test_no_sleep_when_the_interval_has_passed(self):
        fake = self.fake_time()
        session = _Session(clock=fake.monotonic)
        client = _client("key-a", session)
        client.call_details("1")
        fake.now += 5
        client.call_details("1")
        self.assertEqual(fake.sleeps, [])
        self.assertEqual(session.times, [1000.0, 1005.0])

    def test_threads_of_one_key_are_spaced_in_real_time(self):
        interval = 0.2
        self.patch_const(MIN_REQUEST_INTERVAL=interval)
        session = _Session()
        other = _Session()
        barrier = threading.Barrier(5)

        def one():
            barrier.wait(5)
            return _client("key-a", session).call_details("1")

        workers = [_spawn(one) for _ in range(5)]
        # Пока пятеро стоят в очереди своего ключа, чужой ключ уходит сразу.
        started = time.perf_counter()
        _client("key-b", other).call_details("1")
        other_took = time.perf_counter() - started
        for worker in workers:
            worker.join(10)
        self.assertFalse(any(w.is_alive() for w in workers))
        self.assertEqual([w.error for w in workers], [None] * 5)
        moments = sorted(session.times)
        gaps = [b - a for a, b in zip(moments, moments[1:])]
        self.assertEqual(len(moments), 5)
        # Допуск — на грубые часы monotonic в Windows (шаг ~16 мс) и на планировщик.
        self.assertGreaterEqual(min(gaps), interval - 0.06, gaps)
        self.assertGreaterEqual(moments[-1] - moments[0], 4 * interval - 0.06)
        self.assertLess(other_took, interval)

    def test_queued_requests_follow_each_answer_without_wasted_slots(self):
        # Четверо ждут один ключ, Binotel отвечает за 0,1 с. Каждый следующий уходит
        # через зазор после ответа предыдущему — и не позже: слоты заранее не бронируют,
        # так что ждущий не теряет круг из-за слота, занятого «на потом».
        interval, latency = 0.2, 0.1
        self.patch_const(MIN_REQUEST_INTERVAL=interval)
        answered = []
        guard = threading.Lock()

        def on_post(endpoint):
            time.sleep(latency)
            with guard:
                answered.append(time.perf_counter())

        session = _Session(on_post=on_post)
        barrier = threading.Barrier(4)

        def one():
            barrier.wait(5)
            return _client("key-a", session).call_details("1")

        started = time.perf_counter()
        workers = [_spawn(one) for _ in range(4)]
        for worker in workers:
            worker.join(10)
        elapsed = time.perf_counter() - started
        self.assertFalse(any(w.is_alive() for w in workers))
        self.assertEqual([w.error for w in workers], [None] * 4)
        sent = sorted(session.times)
        for previous_answer, next_send in zip(sorted(answered), sent[1:]):
            self.assertGreaterEqual(next_send - previous_answer, interval - 0.06)
        self.assertLess(elapsed, 4 * latency + 3 * interval + 0.5)


class MaxWaitTests(_RateCase):
    """max_wait: сколько вызов согласен ждать своей очереди."""

    def test_zero_sends_at_once_when_the_slot_is_free(self):
        fake = self.fake_time()
        session = _Session(clock=fake.monotonic)
        _client("key-a", session).call_details("1", max_wait=0)
        self.assertEqual(session.times, [1000.0])
        self.assertEqual(fake.sleeps, [])

    def test_zero_raises_at_once_when_the_slot_is_taken(self):
        fake = self.fake_time()
        session = _Session(clock=fake.monotonic)
        client = _client("key-a", session)
        client.call_details("1")
        with self.assertRaises(bc.RateLimited) as caught:
            client.call_details("2", max_wait=0)
        error = caught.exception
        self.assertIsInstance(error, RuntimeError)      # прежние except RuntimeError работают
        self.assertAlmostEqual(error.retry_after, 1.0)
        self.assertEqual(error.endpoint, "stats/call-details")
        # По этим словам обзвон отличает лимит частоты от отказа АТС (429 против 502).
        self.assertIn("too frequent", str(error).lower())
        self.assertIn("часто", str(error).lower())
        self.assertEqual(len(session.calls), 1)          # второй запрос не ушёл
        self.assertEqual(fake.sleeps, [])                # и никто не спал

    def test_negative_max_wait_means_zero(self):
        fake = self.fake_time()
        session = _Session(clock=fake.monotonic)
        client = _client("key-a", session)
        client.call_details("1", max_wait=-5)
        with self.assertRaises(bc.RateLimited):
            client.call_details("2", max_wait=-5)
        self.assertEqual(fake.sleeps, [])

    def test_bound_waits_only_when_it_is_enough(self):
        fake = self.fake_time()
        session = _Session(clock=fake.monotonic)
        client = _client("key-a", session)
        client.call_details("1")
        # До слота секунда, а ждать согласны полсекунды: отказ сразу, без сна «на удачу».
        with self.assertRaises(bc.RateLimited) as caught:
            client.call_details("2", max_wait=0.5)
        self.assertAlmostEqual(caught.exception.retry_after, 1.0)
        self.assertEqual(fake.sleeps, [])
        # Двух секунд хватает: ждём слот и отправляем.
        client.call_details("3", max_wait=2)
        self.assertEqual(fake.sleeps, [1.0])
        self.assertEqual(session.times, [1000.0, 1001.0])

    def test_refused_request_does_not_take_a_slot(self):
        fake = self.fake_time()
        session = _Session(clock=fake.monotonic)
        client = _client("key-a", session)
        client.call_details("1")
        for _ in range(5):
            with self.assertRaises(bc.RateLimited):
                client.call_details("2", max_wait=0)
        # Пять отказов очередь не удлинили: следующий запрос уходит через тот же зазор.
        client.call_details("3")
        self.assertEqual(session.times, [1000.0, 1001.0])

    def test_zero_returns_immediately_in_real_time(self):
        session = _Session(answers=[_too_frequent(3)])
        client = _client("key-a", session)
        with self.assertRaises(bc.RateLimited):
            client.call_details("1", max_wait=0)
        started = time.perf_counter()
        for _ in range(20):
            with self.assertRaises(bc.RateLimited):
                client.call_details("1", max_wait=0)
        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertEqual(len(session.calls), 1)


class CooldownTests(_RateCase):
    """Подсказка Binotel «after N sec» — пауза, общая для всех потоков ключа."""

    def test_cooldown_is_shared_and_zero_wait_raises_without_sending(self):
        fake = self.fake_time()
        first = _Session(answers=[_too_frequent(3)], clock=fake.monotonic)
        second = _Session(clock=fake.monotonic)
        third = _Session(clock=fake.monotonic)
        with self.assertRaises(bc.RateLimited) as rejected:
            _client("key-a", first).call_details("1", max_wait=0)
        # Отвергнутый Binotel запрос: в тексте — его ответ с кодом, как и раньше.
        self.assertIn("code=106", str(rejected.exception))
        self.assertIn("too frequent", str(rejected.exception).lower())
        self.assertAlmostEqual(rejected.exception.retry_after, 3.4)

        # Другой клиент того же ключа про паузу уже знает: не отправляет и не спит.
        with self.assertRaises(bc.RateLimited) as refused:
            _client("key-a", second).call_details("2", max_wait=0)
        self.assertEqual(second.calls, [])
        self.assertAlmostEqual(refused.exception.retry_after, 3.4)
        self.assertEqual(fake.sleeps, [])

        # Чужого ключа пауза не касается.
        _client("key-b", third).call_details("3", max_wait=0)
        self.assertEqual(len(third.calls), 1)

        # Пауза истекла — запрос уходит.
        fake.now += 3.5
        _client("key-a", second).call_details("2", max_wait=0)
        self.assertEqual(len(second.calls), 1)

    def test_default_caller_waits_out_a_cooldown_set_by_another_client(self):
        fake = self.fake_time()
        first = _Session(answers=[_too_frequent(3)], clock=fake.monotonic)
        second = _Session(clock=fake.monotonic)
        with self.assertRaises(bc.RateLimited):
            _client("key-a", first).call_details("1", max_wait=0)
        _client("key-a", second).call_details("2")
        # Не «отправил, получил тот же отказ и уснул», а сразу дождался: запрос один.
        self.assertEqual(len(second.calls), 1)
        self.assertAlmostEqual(second.times[0], 1003.4)
        self.assertEqual(fake.sleeps, [3.4])

    def test_default_mode_retries_after_the_hint(self):
        fake = self.fake_time()
        session = _Session(answers=[_too_frequent(2), _ok(callDetails={})], clock=fake.monotonic)
        payload = _client("key-a", session)._post("stats/call-details", {"generalCallID": "1"})
        self.assertEqual(payload["status"], "success")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(fake.sleeps, [2.4])               # подсказка + запас, как раньше

    def test_default_mode_gives_up_after_max_retries(self):
        fake = self.fake_time()
        session = _Session(answers=[_too_frequent(4)] * 5, clock=fake.monotonic)
        with self.assertRaises(RuntimeError) as caught:
            _client("key-a", session).call_details("1")
        self.assertIsInstance(caught.exception, bc.RateLimited)
        self.assertIn("code=106", str(caught.exception))
        self.assertIn("too frequent", str(caught.exception).lower())
        self.assertEqual(len(session.calls), bc.RATE_LIMIT_MAX_RETRIES)
        self.assertEqual(fake.sleeps, [4.4, 4.4])
        self.assertAlmostEqual(caught.exception.retry_after, 4.4)

    def test_hint_is_capped(self):
        # Одна строка ответа не должна останавливать запросы ключа на десять минут.
        self.fake_time()
        session = _Session(answers=[_too_frequent(600)])
        with self.assertRaises(bc.RateLimited) as caught:
            _client("key-a", session).call_details("1", max_wait=0)
        self.assertAlmostEqual(caught.exception.retry_after,
                               bc.RATE_LIMIT_MAX_WAIT + bc.RATE_LIMIT_MARGIN)

    def test_bound_retries_within_budget(self):
        fake = self.fake_time()
        session = _Session(answers=[_too_frequent(2), _ok(callDetails={})], clock=fake.monotonic)
        self.assertEqual(_client("key-a", session).call_details("1", max_wait=5), {})
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(fake.sleeps, [2.4])

    def test_bound_refuses_at_once_when_the_hint_is_longer(self):
        fake = self.fake_time()
        session = _Session(answers=[_too_frequent(4), _ok()], clock=fake.monotonic)
        with self.assertRaises(bc.RateLimited) as caught:
            _client("key-a", session).call_details("1", max_wait=3)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(fake.sleeps, [])                  # не спали «до упора» впустую
        self.assertAlmostEqual(caught.exception.retry_after, 4.4)

    def test_light_cooldown_is_per_method(self):
        # Отказ в call-details не должен задерживать click-to-call по тому же ключу.
        fake = self.fake_time()
        session = _Session(answers=[_too_frequent(4), _ok(generalCallID="77")], clock=fake.monotonic)
        client = _client("key-a", session)
        with self.assertRaises(bc.RateLimited):
            client.call_details("1", max_wait=0)
        fake.now += 1.0                                     # слот ключа освободился
        self.assertEqual(client.originate_internal_to_external("904", "77000000000", max_wait=0), "77")
        fake.now += 1.0
        with self.assertRaises(bc.RateLimited) as caught:   # а call-details ещё на паузе
            client.call_details("1", max_wait=0)
        self.assertAlmostEqual(caught.exception.retry_after, 2.4)
        self.assertEqual(session.endpoints,
                         ["stats/call-details", "calls/internal-number-to-external-number"])

    def test_heavy_cooldown_is_shared_by_heavy_methods_only(self):
        # У выборок статистики бюджет общий на ключ: отказ одной — пауза всем выборкам,
        # но не точечным запросам (запись разговора).
        fake = self.fake_time()
        session = _Session(answers=[_too_frequent(10)], clock=fake.monotonic)
        client = _client("key-a", session)
        with self.assertRaises(bc.RateLimited):
            client.list_calls_for_day(DAY, max_wait=0)
        fake.now += 2.0                                     # слот и зазор тяжёлых прошли
        with self.assertRaises(bc.RateLimited) as caught:
            client.list_calls_by_internal_number("907", 0, 3600, max_wait=0)
        self.assertAlmostEqual(caught.exception.retry_after, 8.4)
        with self.assertRaises(bc.RateLimited):
            client.list_calls_by_external_numbers(["77000000000"], max_wait=0)
        self.assertIsNone(client.get_call_record_url("1", max_wait=0))
        self.assertEqual(session.endpoints, ["stats/list-of-calls-for-period", "stats/call-record"])

    def test_code_106_is_enough_without_the_text(self):
        self.fake_time()
        session = _Session(answers=[{"status": "error", "code": "106", "message": "Limit"}])
        with self.assertRaises(bc.RateLimited) as caught:
            _client("key-a", session).call_details("1", max_wait=0)
        self.assertAlmostEqual(caught.exception.retry_after, 10 + bc.RATE_LIMIT_MARGIN)
        self.assertEqual(bc._too_frequent_wait_seconds({"code": 106}), 10)
        self.assertIsNone(bc._too_frequent_wait_seconds({"code": 150, "message": "Can't call to the ext"}))

    def test_other_errors_are_not_rate_limits(self):
        fake = self.fake_time()
        session = _Session(answers=[{"status": "error", "code": 150, "message": "Can't call to the ext"},
                                    _ok(generalCallID="9")],
                           clock=fake.monotonic)
        client = _client("key-a", session)
        with self.assertRaises(RuntimeError) as caught:
            client.originate_internal_to_external("904", "77000000000")
        self.assertNotIsInstance(caught.exception, bc.RateLimited)
        self.assertIn("code=150", str(caught.exception))
        self.assertEqual(len(session.calls), 1)             # повторов нет
        fake.now += 1.0
        client.originate_internal_to_external("904", "77000000000", max_wait=0)   # и паузы нет
        self.assertEqual(len(session.calls), 2)

    def test_http_error_is_a_plain_runtime_error(self):
        self.fake_time()
        session = _Session(answers=[_Resp("Bad Gateway", status_code=502)])
        with self.assertRaises(RuntimeError) as caught:
            _client("key-a", session).call_details("1")
        self.assertNotIsInstance(caught.exception, bc.RateLimited)
        self.assertIn("Binotel HTTP 502", str(caught.exception))

    def test_default_mode_stops_waiting_when_the_pause_keeps_growing(self):
        # Паузу раз за разом продлевают чужие отказы. Режим «как раньше» не ждёт
        # бесконечно: на паузы уходит не больше ~37 с — на это рассчитаны вызывающие.
        fake = self.fake_time()
        other = _Session(answers=[_too_frequent(5)], clock=fake.monotonic)
        with self.assertRaises(bc.RateLimited):
            _client("key-a", other).call_details("0", max_wait=0)
        state = bc._rate_state("key-a")

        def prolong(_seconds):
            with state.lock:
                state.cooldowns["stats/call-details"] = fake.now + 5.4

        fake.on_sleep = prolong
        session = _Session(clock=fake.monotonic)
        with self.assertRaises(bc.RateLimited) as caught:
            _client("key-a", session).call_details("1")
        self.assertEqual(session.calls, [])                 # запрос так и не ушёл
        budget = bc.RATE_LIMIT_MAX_RETRIES * (bc.RATE_LIMIT_MAX_WAIT + bc.RATE_LIMIT_MARGIN)
        self.assertEqual(len(fake.sleeps), 6)               # 6 × 5.4 = 32.4 с; седьмая пауза — уже 37.8
        self.assertLessEqual(sum(fake.sleeps), budget)
        self.assertIn("too frequent", str(caught.exception).lower())

    def test_waiting_for_a_cooldown_does_not_hold_the_queue(self):
        # Поток ждёт паузу call-details. Слот «на потом» он не занимает: запрос другого
        # метода по тому же ключу проходит сразу, а не встаёт за ним в очередь.
        self.patch_const(MIN_REQUEST_INTERVAL=0.05, RATE_LIMIT_MARGIN=0.0)
        rejected = threading.Event()
        session = _Session(answers=[_too_frequent(1)],
                           on_post=lambda endpoint: rejected.set())
        client = _client("key-a", session)
        waiter = _spawn(lambda: client.call_details("1"))
        self.assertTrue(rejected.wait(5))
        time.sleep(0.15)                                    # ждущий успел уснуть на паузу
        started = time.perf_counter()
        client.hangup_call("1", max_wait=0.3)
        self.assertLess(time.perf_counter() - started, 0.3)
        waiter.join(5)
        self.assertFalse(waiter.is_alive())
        self.assertIsNone(waiter.error)
        self.assertEqual(session.endpoints,
                         ["stats/call-details", "calls/hangup-call", "stats/call-details"])
        # Повтор ушёл не раньше, чем через секунду паузы после отказа.
        self.assertGreaterEqual(session.times[2] - session.times[0], 0.9)

    def test_waiters_leave_a_pause_one_by_one(self):
        # Четверо ждут одну паузу. Проснувшись разом, они не уходят толпой: раньше конца
        # паузы — никто, дальше — по одному через зазор.
        interval = 0.15
        self.patch_const(MIN_REQUEST_INTERVAL=interval, RATE_LIMIT_MARGIN=0.0)
        session = _Session(answers=[_too_frequent(1)])
        with self.assertRaises(bc.RateLimited):
            _client("key-a", session).call_details("1", max_wait=0)
        rejected_at = time.perf_counter()
        workers = [_spawn(lambda n=n: _client("key-a", session).call_details(str(10 + n)))
                   for n in range(4)]
        for worker in workers:
            worker.join(10)
        self.assertFalse(any(w.is_alive() for w in workers))
        self.assertEqual([w.error for w in workers], [None] * 4)
        moments = sorted(session.times[1:])
        self.assertEqual(len(moments), 4)
        self.assertGreaterEqual(moments[0] - rejected_at, 0.9)
        gaps = [b - a for a, b in zip(moments, moments[1:])]
        self.assertGreaterEqual(min(gaps), interval - 0.06, gaps)

    def test_cooldown_is_shared_between_threads_in_real_time(self):
        self.patch_const(MIN_REQUEST_INTERVAL=0.05, RATE_LIMIT_MARGIN=0.0)
        first = _Session(answers=[_too_frequent(1)])
        second = _Session()
        with self.assertRaises(bc.RateLimited):
            _client("key-a", first).call_details("1", max_wait=0)
        rejected_at = time.perf_counter()
        worker = _spawn(lambda: _client("key-a", second).call_details("2"))
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(worker.error)
        self.assertEqual(len(second.calls), 1)
        self.assertGreaterEqual(second.times[0] - rejected_at, 0.9)


class PauseScopeTests(_RateCase):
    """На что ложится пауза лёгкого метода.

    На такой же запрос — всегда. На весь метод — когда Binotel отверг запрос, какого мы
    не отправляли: повтором это быть не могло, значит лимит у метода общий."""

    def test_two_accepted_requests_in_a_row_cancel_the_conclusion(self):
        # «Новый» для нас запрос отвергнут, но лимит не общий: этот звонок до нас
        # спрашивал кто-то ещё (прежний процесс перед перезапуском). Два принятых подряд
        # запроса это показывают — и пауза снова ложится только на такой же запрос.
        fake = self.fake_time()
        session = _Session(answers=[_too_frequent(2), _ok(), _ok(), _too_frequent(2), _ok()],
                           clock=fake.monotonic)
        client = _client("key-a", session)
        state = bc._rate_state("key-a")
        with self.assertRaises(bc.RateLimited):
            client.call_details("1", max_wait=0)            # вывод: лимит общий
        self.assertIn("stats/call-details", state.wide_until)
        fake.now += 3.0
        client.call_details("1", max_wait=0)                # принят
        fake.now += 1.0
        client.call_details("2", max_wait=0)                # и следующий принят: вывод снят
        self.assertEqual(state.wide_until, {})
        fake.now += 2.0
        with self.assertRaises(bc.RateLimited):
            client.call_details("2", max_wait=0)            # повтор «2» отвергнут…
        fake.now += 1.0
        client.call_details("3", max_wait=0)                # …остальных это не держит
        self.assertEqual(len(session.calls), 5)

    def test_accepted_requests_far_apart_keep_the_conclusion(self):
        # Принятые с разрывом в окно лимита ничего не опровергают: так и выглядит общий лимит.
        fake = self.fake_time()
        session = _Session(answers=[_too_frequent(4), _ok(), _ok()], clock=fake.monotonic)
        client = _client("key-a", session)
        with self.assertRaises(bc.RateLimited):
            client.call_details("1", max_wait=0)
        fake.now += 5.0
        client.call_details("1", max_wait=0)
        fake.now += 5.0
        client.call_details("2", max_wait=0)
        self.assertIn("stats/call-details", bc._rate_state("key-a").wide_until)

    def test_rejected_new_request_pauses_the_whole_method(self):
        fake = self.fake_time()
        session = _Session(answers=[_too_frequent(4)], clock=fake.monotonic)
        client = _client("key-a", session)
        with self.assertRaises(bc.RateLimited):
            client.call_details("1", max_wait=0)            # «1» не спрашивали: лимит общий
        fake.now += 1.0
        with self.assertRaises(bc.RateLimited) as caught:
            client.call_details("2", max_wait=0)
        self.assertAlmostEqual(caught.exception.retry_after, 3.4)
        self.assertEqual(len(session.calls), 1)
        client.hangup_call("2", max_wait=0)                 # другого метода пауза не касается
        self.assertEqual(session.endpoints[-1], "calls/hangup-call")

    def test_rejected_repeat_pauses_only_that_request(self):
        # Лимит «на запрос»: один звонок спросили дважды подряд. Остальные ни при чём,
        # и держать их из-за него нельзя.
        fake = self.fake_time()
        session = _Session(answers=[_ok(callDetails={}), _too_frequent(2)], clock=fake.monotonic)
        client = _client("key-a", session)
        client.call_details("1", max_wait=0)
        fake.now += 3.0
        with self.assertRaises(bc.RateLimited):
            client.call_details("1", max_wait=0)            # повтор: о методе вывода нет
        fake.now += 1.0
        client.call_details("2", max_wait=0)                # другой звонок уходит
        with self.assertRaises(bc.RateLimited) as caught:
            client.call_details("1", max_wait=0)            # а этот — на паузе
        self.assertAlmostEqual(caught.exception.retry_after, 1.4)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(bc._rate_state("key-a").wide_until, {})

    def test_list_and_single_requests_share_call_ids(self):
        fake = self.fake_time()
        session = _Session(answers=[_ok(callDetails={}), _too_frequent(2)], clock=fake.monotonic)
        client = _client("key-a", session)
        client.call_details("1", max_wait=0)
        fake.now += 3.0
        with self.assertRaises(bc.RateLimited):
            client.call_details(["1", "2"], max_wait=0)     # «1» спрашивали только что — это повтор
        self.assertEqual(bc._rate_state("key-a").wide_until, {})
        fake.now += 1.0
        with self.assertRaises(bc.RateLimited):
            client.call_details("2", max_wait=0)            # «2» был в отвергнутом списке
        client.call_details("3", max_wait=0)                # «3» ни при чём
        self.assertEqual(len(session.calls), 3)

    def test_request_without_parameters_always_pauses_its_method(self):
        fake = self.fake_time()
        session = _Session(answers=[_too_frequent(2)], clock=fake.monotonic)
        client = _client("key-a", session)
        with self.assertRaises(bc.RateLimited):
            client._post("settings/list-of-employees", {}, max_wait=0)
        fake.now += 1.0
        with self.assertRaises(bc.RateLimited):
            client._post("settings/list-of-employees", {}, max_wait=0)
        self.assertEqual(len(session.calls), 1)

    def test_method_wide_mode_is_remembered_and_then_forgotten(self):
        fake = self.fake_time()
        session = _Session(answers=[_too_frequent(1), _ok(), _too_frequent(1),
                                    _ok(), _too_frequent(1), _ok()],
                           clock=fake.monotonic)
        client = _client("key-a", session)
        with self.assertRaises(bc.RateLimited):
            client.call_details("1", max_wait=0)            # новый запрос отвергнут → лимит общий
        fake.now += 2.0
        client.call_details("1", max_wait=0)
        fake.now += 2.0
        with self.assertRaises(bc.RateLimited):
            client.call_details("1", max_wait=0)            # уже повтор, но вывод ещё в силе…
        fake.now += 1.0
        with self.assertRaises(bc.RateLimited):
            client.call_details("2", max_wait=0)            # …и пауза легла на весь метод
        self.assertEqual(len(session.calls), 3)

        fake.now += bc._WIDE_LIMIT_TTL + 1                  # вывод забыт
        client.call_details("1", max_wait=0)
        fake.now += 2.0
        with self.assertRaises(bc.RateLimited):
            client.call_details("1", max_wait=0)            # повтор ставит на паузу только себя
        fake.now += 1.0
        client.call_details("2", max_wait=0)
        self.assertEqual(len(session.calls), 6)

    def test_request_values_are_kept_as_hashes(self):
        # Среди значений запроса — номера телефонов: в памяти процесса им делать нечего.
        tokens = bc._request_tokens({"internalNumber": "904", "externalNumber": "77000000000",
                                     "generalCallID": ["1", "2", "1"], "skip": None,
                                     "callerIdForEmployee": "1000"})
        # Линия, номер и два звонка. Повтор «1» не считается, callerIdForEmployee — настройка
        # запроса, а не то, о чём он.
        self.assertEqual(len(tokens), 4)
        self.assertTrue(all(isinstance(token, int) for token in tokens))
        self.assertEqual(bc._request_tokens(None), ())
        self.assertEqual(bc._request_tokens({"startTime": 1, "stopTime": 2}), ())
        self.assertNotEqual(bc._request_tokens({"internalNumber": "904"}),
                            bc._request_tokens({"externalNumber": "904"}))
        many = bc._request_tokens({"generalCallID": [str(i) for i in range(500)]})
        self.assertEqual(len(many), bc._MAX_REQUEST_TOKENS)

    def test_shared_request_option_does_not_couple_operators(self):
        # callerIdForEmployee у всего отдела один. Отказ одному оператору (повторный клик)
        # не должен ставить на паузу звонки остальных.
        fake = self.fake_time()
        session = _Session(answers=[_ok(generalCallID="1"), _too_frequent(3), _ok(generalCallID="2")],
                           clock=fake.monotonic)
        client = _client("key-a", session)
        client.originate_internal_to_external("904", "77000000001", callerIdForEmployee="1000")
        fake.now += 2.0
        with self.assertRaises(bc.RateLimited):
            client.originate_internal_to_external("904", "77000000001", max_wait=0,
                                                  callerIdForEmployee="1000")
        fake.now += 1.0
        self.assertEqual(client.originate_internal_to_external(
            "905", "77000000002", max_wait=0, callerIdForEmployee="1000"), "2")
        with self.assertRaises(bc.RateLimited):             # а линия 904 ещё на паузе
            client.originate_internal_to_external("904", "77000000003", max_wait=0,
                                                  callerIdForEmployee="1000")
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(bc._rate_state("key-a").wide_until, {})

    def test_memory_of_a_key_stays_bounded(self):
        fake = self.fake_time()
        self.patch_const(MIN_REQUEST_INTERVAL=0.0)
        session = _Session(clock=fake.monotonic)
        client = _client("key-a", session)
        state = bc._rate_state("key-a")
        for number in range(3 * bc._MAX_TRACKED):
            session.answers.append(_too_frequent(1) if number % 2 else _ok())
            try:
                client.call_details(str(number), max_wait=0)
            except bc.RateLimited:
                pass
            fake.now += 2.0                                 # паузы истекают, запросы уходят
        self.assertEqual(len(session.calls), 3 * bc._MAX_TRACKED)
        self.assertLessEqual(len(state.seen), bc._MAX_TRACKED + 1)
        self.assertLessEqual(len(state.cooldowns), bc._MAX_TRACKED + 1)

    def _polling(self, per_request, calls=7, seconds=60.0, forced_wide=False):
        """Минута фонового опроса звонков при заданном устройстве лимита Binotel."""
        bc._reset_rate_state()
        fake = self.fake_time()
        if forced_wide:
            # Для сравнения: клиент, который ставит паузу на весь метод всегда.
            self.patch_const(_BACK_TO_BACK=0.0)
            bc._rate_state("key-a").wide_until["stats/call-details"] = float("inf")
        binotel = _BinotelModel(fake, per_request=per_request)
        client = _client("key-a", binotel)
        poller = _Poller(client, fake, [str(n) for n in range(1, calls + 1)], every=3.0)
        poller.run(seconds)
        return fake, binotel, client, poller

    def test_adapts_to_a_method_wide_limit(self):
        # Binotel: один call-details в 5 секунд на компанию. Клиент это выясняет и держит
        # паузу на весь метод: отказ — один на окно, а не по одному с каждого звонка.
        fake, binotel, client, poller = self._polling(per_request=False)
        self.assertIn("stats/call-details", bc._rate_state("key-a").wide_until)
        self.assertGreaterEqual(binotel.accepted, 9)        # минута / (5 с окно + запас)
        self.assertLessEqual(binotel.rejected, binotel.accepted + 2)

        # Живой вопрос («класть ли трубку») при идущем фоновом опросе: ответ приходит за
        # окно Binotel — его опережают только фоновые, а они ждать не согласны.
        fake.on_tick = poller.tick
        for call in ("1", "2", "3"):
            poller.run(2.3)
            asked_at = fake.now
            self.assertEqual(client.call_details(call, max_wait=6), {})
            self.assertLessEqual(fake.now - asked_at, 6.0)

    def test_adapts_to_a_per_request_limit(self):
        # Binotel: один и тот же звонок — не чаще раза в 5 секунд, разные не мешают друг
        # другу. Клиент не ставит паузу на весь метод: отказ одному звонку остальных не держит.
        fake, binotel, client, poller = self._polling(per_request=True)
        self.assertEqual(bc._rate_state("key-a").wide_until, {})
        self.assertGreaterEqual(min(poller.answered.values()), 1)
        adaptive = binotel.accepted

        fake.on_tick = poller.tick
        for call in ("1", "2", "3"):
            poller.run(2.3)
            asked_at = fake.now
            self.assertEqual(client.call_details(call, max_wait=6), {})
            self.assertLessEqual(fake.now - asked_at, 6.0)

        # Для сравнения — та же минута, но с паузой на весь метод: принятых запросов меньше.
        _fake, forced, _client_, _poller = self._polling(per_request=True, forced_wide=True)
        self.assertGreater(adaptive, forced.accepted)

    def test_recovers_from_a_false_method_wide_conclusion(self):
        # Лимит «на запрос», а клиент (после перезапуска) решил, что он общий. Первая же
        # пара принятых подряд запросов возвращает его к паузам «на запрос».
        fake = self.fake_time()
        state = bc._rate_state("key-a")
        state.wide_until["stats/call-details"] = fake.now + bc._WIDE_LIMIT_TTL
        binotel = _BinotelModel(fake, per_request=True)
        poller = _Poller(_client("key-a", binotel), fake, [str(n) for n in range(1, 8)], every=3.0)
        poller.run(15)
        self.assertEqual(state.wide_until, {})


class HeavyRequestTests(_RateCase):
    """Выборки статистики («нагружаемые» методы) — строго по одной на ключ."""

    def test_classification(self):
        for endpoint in ("stats/list-of-calls-for-period",
                         "stats/list-of-calls-by-internal-number-for-period",
                         "stats/history-by-external-number",
                         "stats/outgoing-calls-for-period",
                         "stats/incoming-calls-for-period",
                         "stats/list-of-calls-per-day",
                         "stats/all-incoming-calls-since",
                         "stats/something-new", "/Stats/List-Of-Calls-For-Period/"):
            self.assertTrue(bc.is_heavy_endpoint(endpoint), endpoint)
        for endpoint in ("stats/call-details", "stats/call-record",
                         "calls/internal-number-to-external-number", "calls/hangup-call",
                         "settings/list-of-employees", "", None):
            self.assertFalse(bc.is_heavy_endpoint(endpoint), endpoint)

    def test_heavy_requests_of_one_key_go_one_at_a_time(self):
        self.patch_const(MIN_REQUEST_INTERVAL=0.05)
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        guard = threading.Lock()
        flight = {"now": 0, "max": 0, "heavy": 0}

        def on_post(endpoint):
            if not bc.is_heavy_endpoint(endpoint):
                return
            with guard:
                flight["now"] += 1
                flight["heavy"] += 1
                flight["max"] = max(flight["max"], flight["now"])
            entered.set()
            release.wait(10)
            with guard:
                flight["now"] -= 1

        session = _Session(on_post=on_post)
        first = _spawn(lambda: _client("key-a", session).list_calls_for_day(DAY))
        self.assertTrue(entered.wait(5))
        second = _spawn(lambda: _client("key-a", session).list_calls_by_internal_number("907", 0, 3600))
        time.sleep(0.3)
        self.assertEqual(flight["heavy"], 1, "вторая выборка ушла, не дождавшись первой")

        # Пока выборка в полёте: лёгкий запрос того же ключа проходит…
        started = time.perf_counter()
        _client("key-a", session).call_details("1")
        self.assertLess(time.perf_counter() - started, 1.0)
        # …выборка другой компании тоже…
        other = _Session()
        _client("key-b", other).list_calls_for_day(DAY)
        self.assertEqual(other.endpoints, ["stats/list-of-calls-for-period"])
        # …а третья выборка этого ключа с max_wait=0 получает отказ сразу,
        started = time.perf_counter()
        with self.assertRaises(bc.RateLimited):
            _client("key-a", session).list_calls_for_day(DAY, max_wait=0)
        self.assertLess(time.perf_counter() - started, 0.2)
        # и с небольшим max_wait — когда он истечёт.
        started = time.perf_counter()
        with self.assertRaises(bc.RateLimited):
            _client("key-a", session).list_calls_for_day(DAY, max_wait=0.2)
        waited = time.perf_counter() - started
        self.assertGreaterEqual(waited, 0.15)
        self.assertLess(waited, 1.0)
        self.assertEqual(flight["heavy"], 1)

        release.set()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual([first.error, second.error], [None, None])
        self.assertEqual(flight["heavy"], 2)
        self.assertEqual(flight["max"], 1)

    def test_gap_is_counted_from_the_received_answer_too(self):
        # Как при общем замке: следующий запрос — не раньше чем через зазор после ОТВЕТА
        # на предыдущий. На этом темпе выверены длинные обходы (месяц по дням, окна).
        fake = self.fake_time()

        def slow(endpoint):
            if bc.is_heavy_endpoint(endpoint):
                fake.now += 5.0                             # выборка готовилась пять секунд

        session = _Session(on_post=slow, clock=fake.monotonic)
        client = _client("key-a", session)
        client.list_calls_for_day(DAY)                      # ушла в 1000, ответ в 1005
        client.list_calls_for_day(DAY)                      # 1006, ответ в 1011
        client.call_details("1")                            # 1012
        self.assertEqual(session.times, [1000.0, 1006.0, 1012.0])
        self.assertEqual(fake.sleeps, [1.0, 1.0])

    def test_request_in_flight_does_not_delay_the_gap(self):
        # Пока ответа нет, зазору не от чего считаться: запросы идут с зазором от отправки.
        fake = self.fake_time()
        in_flight = {"n": 0}
        session = _Session(clock=fake.monotonic)
        client = _client("key-a", session)

        def nested(endpoint):
            # Внутри «висящего» click-to-call проходят секунды, и другие запросы ключа уходят.
            if endpoint == "calls/internal-number-to-external-number" and not in_flight["n"]:
                in_flight["n"] = 1
                for _ in range(3):
                    fake.now += 1.0
                    client.call_details("1", max_wait=0)

        session.on_post = nested
        session.answers = [_ok(generalCallID="77")]
        self.assertEqual(client.originate_internal_to_external("904", "77000000000"), "77")
        self.assertEqual(session.times, [1000.0, 1001.0, 1002.0, 1003.0])
        self.assertEqual(fake.sleeps, [])

    def test_heavy_lock_is_released_after_errors(self):
        self.fake_time()
        session = _Session(answers=[_Resp("oops", status_code=500),
                                    {"status": "error", "code": 102, "message": "Wrong data"}])
        client = _client("key-a", session)
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                client.list_calls_for_day(DAY)
        self.assertFalse(bc._rate_state("key-a").heavy_lock.locked())
        self.assertEqual(client.list_calls_for_day(DAY, max_wait=5), [])


class ConcurrencyTests(_RateCase):
    """HTTP-запрос идёт вне замков: медленный ответ никого не держит."""

    def _slow_session(self, slow_endpoint):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def on_post(endpoint):
            if endpoint == slow_endpoint:
                entered.set()
                release.wait(10)

        return _Session(on_post=on_post), entered, release

    def test_slow_request_does_not_block_another_key(self):
        # Тяжёлая выборка Тез КЦ висит, а удалённый КЦ в это время звонит и опрашивает.
        self.patch_const(MIN_REQUEST_INTERVAL=0.05)
        tez_session, entered, release = self._slow_session("stats/list-of-calls-for-period")
        slow = _spawn(lambda: _client("key-tez", tez_session).list_calls_for_day(DAY))
        self.assertTrue(entered.wait(5))

        remote_session = _Session(answers=[_ok(generalCallID="77")])
        remote = _client("key-remote", remote_session)
        fast = _spawn(lambda: (remote.originate_internal_to_external("904", "77000000000"),
                               remote.call_details("77", max_wait=2)))
        fast.join(5)
        self.assertFalse(fast.is_alive(), "запрос другой компании встал за чужим медленным")
        self.assertIsNone(fast.error)
        self.assertEqual(fast.result[0], "77")
        self.assertTrue(slow.is_alive())                    # медленный всё ещё в полёте

        release.set()
        slow.join(5)
        self.assertFalse(slow.is_alive())
        self.assertIsNone(slow.error)

    def test_slow_click_to_call_does_not_block_polling_of_the_same_key(self):
        # Это и был сбой 12.10.2026: click-to-call отвечает ~10 с, и всё это время
        # опрос исходов (в том числе вопрос «класть ли трубку») стоял за ним.
        self.patch_const(MIN_REQUEST_INTERVAL=0.05)
        session, entered, release = self._slow_session("calls/internal-number-to-external-number")
        session.answers = [_ok(generalCallID="77")]
        client = _client("key-remote", session)
        call = _spawn(lambda: client.originate_internal_to_external("904", "77000000000"))
        self.assertTrue(entered.wait(5))

        started = time.perf_counter()
        for _ in range(3):
            _client("key-remote", session).call_details("55")
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertTrue(call.is_alive())

        release.set()
        call.join(5)
        self.assertFalse(call.is_alive())
        self.assertEqual((call.result, call.error), ("77", None))

    def test_many_threads_do_not_deadlock(self):
        self.patch_const(MIN_REQUEST_INTERVAL=0.002, RATE_LIMIT_MARGIN=0.01)
        rng = random.Random(20261012)
        guard = threading.Lock()
        flight = {}
        worst = {}

        def make_session(key):
            def on_post(endpoint):
                if not bc.is_heavy_endpoint(endpoint):
                    return
                with guard:
                    flight[key] = flight.get(key, 0) + 1
                    worst[key] = max(worst.get(key, 0), flight[key])
                time.sleep(0.003)
                with guard:
                    flight[key] -= 1

            class _Flaky(_Session):
                def post(self, url, json=None, timeout=None):
                    with guard:
                        refuse = rng.random() < 0.15
                    if refuse:
                        self.answers.append(_too_frequent(0))
                    return super().post(url, json=json, timeout=timeout)

            return _Flaky(on_post=on_post)

        sessions = {key: make_session(key) for key in ("key-a", "key-b")}
        unexpected = []

        def work(seed):
            local = random.Random(seed)
            for _ in range(12):
                key = local.choice(("key-a", "key-b"))
                client = _client(key, sessions[key])
                max_wait = local.choice((None, 0, 0.05))
                try:
                    kind = local.randrange(4)
                    if kind == 0:
                        client.list_calls_for_day(DAY, max_wait=max_wait)
                    elif kind == 1:
                        client.call_details("1", max_wait=max_wait)
                    elif kind == 2:
                        client.hangup_call("1", max_wait=max_wait)
                    else:
                        client.get_call_record_url("1", max_wait=max_wait)
                except bc.RateLimited:
                    pass
                except Exception as exc:  # noqa: BLE001
                    unexpected.append(exc)

        workers = [_spawn(lambda seed=seed: work(seed)) for seed in range(16)]
        for worker in workers:
            worker.join(60)
        self.assertFalse(any(w.is_alive() for w in workers), "потоки зависли — взаимная блокировка")
        self.assertEqual(unexpected, [])
        self.assertEqual([w.error for w in workers], [None] * len(workers))
        for key, top in worst.items():
            self.assertEqual(top, 1, "выборки ключа %s шли одновременно" % key)
        for key in sessions:
            self.assertFalse(bc._rate_state(key).heavy_lock.locked())


class CompatibilityTests(_RateCase):
    """Прежние вызывающие и подделки клиента работают без правок."""

    def test_rate_limited_is_a_runtime_error_and_is_reexported(self):
        from tez import binotel_calls
        self.assertTrue(issubclass(bc.RateLimited, RuntimeError))
        self.assertIs(binotel_calls.RateLimited, bc.RateLimited)
        self.assertIs(binotel_calls.BinotelApiClient, bc.BinotelApiClient)
        error = bc.RateLimited("x")
        self.assertEqual((error.retry_after, error.endpoint), (0.0, ""))
        # Подделкам клиента в чужих тестах удобно бросать его без текста.
        self.assertIn("too frequent", str(bc.RateLimited()))
        self.assertEqual(bc.RateLimited(retry_after=2.5).retry_after, 2.5)

    def test_rate_limit_config_is_kept(self):
        self.assertEqual(bc.MIN_REQUEST_INTERVAL, 1.0)
        self.assertEqual(bc.RATE_LIMIT_MAX_RETRIES, 3)
        self.assertEqual(bc.RATE_LIMIT_MAX_WAIT, 12)

    def test_no_process_wide_lock_is_left(self):
        # Общий замок и был причиной: вернись он — компании снова встанут друг за другом.
        self.assertFalse(hasattr(bc, "_RATE_LOCK"))
        self.assertFalse(hasattr(bc, "_LAST_REQUEST_AT"))
        self.assertIsNot(bc._rate_state("key-a"), bc._rate_state("key-b"))
        self.assertIs(bc._rate_state("key-a"), bc._rate_state("key-a"))
        self.assertIsNot(bc._rate_state("key-a").lock, bc._rate_state("key-b").lock)
        self.assertIsNot(bc._rate_state("key-a").heavy_lock, bc._rate_state("key-b").heavy_lock)

    def test_subclass_with_the_old_post_signature_still_works(self):
        # Так устроены подделки в тестах соседей: _post(endpoint, params) без max_wait.
        seen = []

        class _Old(bc.BinotelApiClient):
            def _post(self, endpoint, params):
                seen.append(endpoint)
                return {"status": "success", "generalCallID": "5", "callDetails": {}}

        client = _Old("key-a", "secret")
        client.call_details("1")
        client.hangup_call("1")
        client.get_call_record_url("1")
        client.list_calls_for_day(DAY)
        client.list_calls_by_internal_number("907", 0, 3600)
        client.list_calls_by_external_numbers(["77000000000"])
        self.assertEqual(client.originate_internal_to_external("904", "77000000000"), "5")
        self.assertEqual(len(seen), 7)

    def test_public_methods_pass_max_wait_through(self):
        seen = []

        class _Spy(bc.BinotelApiClient):
            def _post(self, endpoint, params, max_wait=None):
                seen.append((endpoint, dict(params), max_wait))
                return {"status": "success", "generalCallID": "5", "callDetails": {}}

        client = _Spy("key-a", "secret")
        client.call_details("1", max_wait=0)
        client.hangup_call("1", max_wait=1)
        client.get_call_record_url("1", max_wait=2)
        client.list_calls_for_day(DAY, max_wait=3)
        client.list_calls_by_internal_number("907", 0, 3600, max_wait=4)
        client.list_calls_by_external_numbers(["77000000000"], max_wait=5)
        client.originate_internal_to_external("904", "77000000000", max_wait=6,
                                              callerIdForEmployee="904")
        self.assertEqual([item[2] for item in seen], [0, 1, 2, 3, 4, 5, 6])
        # max_wait — наш параметр: в запрос к Binotel он не попадает.
        self.assertEqual(seen[-1][1], {"internalNumber": "904", "externalNumber": "77000000000",
                                       "callerIdForEmployee": "904"})

    def test_http_timeout_can_be_given_per_call(self):
        # Ревью 13.10.2026: общему опросу живых звонков обзвона — свой короткий таймаут HTTP (опрос
        # у компании один, и зависший на 40 с запрос держал бы финалы всех её операторов);
        # остальным вызовам — таймаут клиента, как было.
        self.fake_time()
        timeouts = []

        class _Timed(_Session):
            def post(self, url, json=None, timeout=None):
                timeouts.append(timeout)
                return super().post(url, json=json, timeout=timeout)

        client = _client("key-a", _Timed())
        client.call_details(["1", "2"], max_wait=0, timeout=(3.05, 5.0))
        client.call_details("3")
        client._post("stats/call-details", {"generalCallID": "4"}, timeout=7)
        self.assertEqual(timeouts, [(3.05, 5.0), bc.HTTP_TIMEOUT, 7])
        self.assertEqual(bc.HTTP_TIMEOUT, 40)
        # Подделки с прежней сигнатурой _post без timeout вызов без timeout не ломает, а timeout
        # уходит дальше, только когда задан.
        seen = []

        class _Spy(bc.BinotelApiClient):
            def _post(self, endpoint, params, max_wait=None, timeout=None):
                seen.append((max_wait, timeout))
                return {"status": "success", "callDetails": {}}

        spy = _Spy("key-a", "secret")
        spy.call_details("1", max_wait=0, timeout=(1, 2))
        spy.call_details("1")
        self.assertEqual(seen, [(0, (1, 2)), (None, None)])

    def test_key_and_secret_are_sent_and_the_callers_dict_is_untouched(self):
        self.fake_time()
        session = _Session()
        params = {"generalCallID": "1"}
        client = _client("key-a", session)
        client._post("stats/call-details", params)
        self.assertEqual(params, {"generalCallID": "1"})
        self.assertEqual(session.bodies[0], {"generalCallID": "1", "key": "key-a", "secret": "secret"})

    def test_session_keeps_no_cookies(self):
        # Сессию клиента теперь делят потоки, а банка cookies к этому не готова:
        # клиент не принимает cookies вовсе (API они не нужны).
        class _Response:
            def info(self):
                message = email.message.Message()
                message["Set-Cookie"] = "sid=abc; Path=/"
                return message

        request = urllib.request.Request("https://api.binotel.com/api/4.0/stats/call-details.json")
        plain = requests.Session().cookies
        plain.extract_cookies(_Response(), request)
        self.assertEqual(len(plain), 1)                     # обычная сессия cookie запомнила бы
        jar = bc.BinotelApiClient("key-a", "secret").session.cookies
        jar.extract_cookies(_Response(), request)
        self.assertEqual(len(jar), 0)


if __name__ == "__main__":
    unittest.main()
