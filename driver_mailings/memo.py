"""Кэш ответов кабинета с одним запросом на ключ (задача #166).

ЗАЧЕМ. 28.09.2026 подсчёт охвата «встал»: за 45 минут кабинет больше 20 тысяч
раз ответил 429, и почти каждый парк не посчитался по сотне раз. Причина — не
медленный кабинет, а мы сами. Каждый щелчок в форме (галочка диспетчерской,
сегмент, группа) через 450 мс запускал подсчёт ЗАНОВО по всем 89 паркам, а
прежний подсчёт на сервере продолжал идти: браузер его ответ просто выбрасывал.
Подсчёты копились десятками, каждый в восемь потоков, кабинет отбивался 429, и
подсчёт, который шёл 14 секунд, растягивался на минуты.

Отсюда два правила, и оба живут здесь:
* ответ кабинета по ключу держим TTL секунд — снятая и снова поставленная
  галочка, «все минус одна», отправка сразу после подсчёта спрашивают кабинет
  только о том, чего ещё не знаем;
* один ключ в один момент считает ОДИН поток. Второй, пришедший за тем же
  ключом, ждёт первого, а не шлёт в кабинет второй такой же запрос.

`Abandoned` — отказ от подсчёта, который уже никому не нужен (форма успела
запросить новый). Это не ошибка: ждавшие этот ключ не получают её, а считают
сами, и в кэш ничего не ложится.

Время — монотонное (`time.monotonic`): перевод системных часов не должен ни
продлевать ответ кабинета, ни хоронить свежий.
"""

import threading
import time


class Abandoned(Exception):
    """compute отказался считать: его запрос уже никому не нужен."""


class _Flight:
    """Идущий подсчёт одного ключа: ждущие смотрят на него, а не в кабинет."""

    __slots__ = ('done', 'value', 'error', 'abandoned', 'generation')

    def __init__(self, generation):
        self.done = threading.Event()
        self.value = None
        self.error = None
        self.abandoned = False
        self.generation = generation


class FlightCache:
    """TTL-кэш, в котором один ключ одновременно считает только один поток.

    compute() возвращает пару (значение, класть ли в кэш). Второе нужно
    справочникам: неполный ответ (парк отказал посередине) отдаётся человеку,
    но не кэшируется — иначе он час видел бы урезанный список из-за одной
    сетевой ошибки.

    Ошибка compute достаётся и тем, кто ждал этот ключ: они спрашивали о том
    же, и молча посчитать заново значило бы удвоить запросы ровно в тот
    момент, когда кабинет уже отказывает. В кэш ошибка не ложится — следующий
    запрос спросит снова.
    """

    def __init__(self, ttl_seconds, max_items=4096, clock=time.monotonic):
        self._ttl = float(ttl_seconds)
        self._max_items = int(max_items)
        self._clock = clock
        self._lock = threading.Lock()
        self._items = {}        # ключ → (когда, значение)
        self._flights = {}      # ключ → _Flight
        # Растёт на clear(): подсчёт, начатый ДО очистки, в кэш уже не ляжет.
        # Иначе ответ под прежним аккаунтом кабинета, досчитанный после смены
        # аккаунта, пережил бы очистку.
        self._generation = 0

    def _fresh(self, key):
        item = self._items.get(key)
        if item is not None and self._clock() - item[0] < self._ttl:
            return True, item[1]
        return False, None

    def peek(self, key):
        """(True, значение) — если свежее значение есть; иначе (False, None).
        В кабинет не ходит и чужого подсчёта не ждёт."""
        with self._lock:
            return self._fresh(key)

    def get(self, key, compute):
        """Значение по ключу: из кэша, из идущего подсчёта или посчитав самому."""
        while True:
            with self._lock:
                hit, value = self._fresh(key)
                if hit:
                    return value
                flight = self._flights.get(key)
                owner = flight is None
                if owner:
                    flight = self._flights[key] = _Flight(self._generation)
            if owner:
                return self._run(key, flight, compute)
            flight.done.wait()
            if flight.abandoned:
                # Считавший отказался — теперь считать нам (или следующему
                # ждущему, если он успеет раньше).
                continue
            if flight.error is not None:
                raise flight.error
            return flight.value

    def _run(self, key, flight, compute):
        done = False
        keep = False
        try:
            value, keep = compute()
            flight.value = value
            done = True
            return value
        except Abandoned:
            raise
        except Exception as error:
            flight.error = error
            raise
        finally:
            if not done and flight.error is None:
                # Отказ от подсчёта (или что-то вне Exception) — ждущие должны
                # посчитать сами, а не получить None вместо числа.
                flight.abandoned = True
            with self._lock:
                if done and keep and flight.generation == self._generation:
                    self._items[key] = (self._clock(), flight.value)
                    self._prune()
                if self._flights.get(key) is flight:
                    del self._flights[key]
            flight.done.set()

    def _prune(self):
        """Не дать кэшу расти без края: сначала протухшее, потом старшая половина."""
        if len(self._items) <= self._max_items:
            return
        now = self._clock()
        for key in [k for k, (stamp, _value) in self._items.items() if now - stamp >= self._ttl]:
            del self._items[key]
        if len(self._items) > self._max_items:
            oldest = sorted(self._items, key=lambda k: self._items[k][0])
            for key in oldest[:len(oldest) // 2]:
                del self._items[key]

    def clear(self):
        with self._lock:
            self._items.clear()
            self._generation += 1

    def __len__(self):
        with self._lock:
            return len(self._items)
