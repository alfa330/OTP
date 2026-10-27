"""Будильник сроков «Кодов 2FA»: сгорание запроса, стирание цифр, удаление сообщения.

Нить спит до ближайшего срока, который сервис сам называет из базы (queries.next_due). Новый
срок — новый запрос или отправленный код — будит её сразу (wake). Но не дольше MAX_SLEEP_SECONDS:
при выкладке два экземпляра живут вместе, и срок, заведённый в СТАРОМ (нажатие пришло в его бот),
будит только его нить — а её через минуту гасят. Без потолка новый будильник проспал бы такие
сроки до следующего действия в разделе: цифры не стирались бы, запрос не сгорал (разбор
27.10.2026). Потолок — это один запрос с тремя MIN по частичным индексам раз в минуту на сервере,
а не опрос из браузеров, который запрещён у колокола.

Сроки лежат в базе, а не в памяти, поэтому перезапуск сервера ничего не теряет: при старте
нить сразу доделывает всё просроченное (Telegram даёт стереть сообщение в течение 48 ч).
При выкладке два экземпляра живут минуту одновременно — оба будильника безопасны: каждое
действие сервис сначала «забирает» условным UPDATE, и второй получает пустой список.
"""

import logging
import threading
import time

# Сбой базы или сети — повторить позже, но не долбить: срок «стереть цифры» не горит
# настолько, чтобы ради него будить базу чаще.
RETRY_SECONDS = 30

# Дольше этого нить не спит даже без сроков — см. докстринг модуля.
MAX_SLEEP_SECONDS = 60


class Alarm:
    def __init__(self, run_due, *, name='twofa-alarm', clock=time.time):
        self._run_due = run_due
        self._clock = clock
        self._cond = threading.Condition()
        self._dirty = False
        self._thread = None
        self._name = name

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
            self._thread.start()
        return self

    def wake(self):
        with self._cond:
            self._dirty = True
            self._cond.notify()

    def _loop(self):
        while True:
            with self._cond:
                self._dirty = False
            try:
                next_at = self._run_due()
            except Exception:  # noqa: BLE001 — нить будильника не должна умирать
                logging.exception('twofa: будильник не отработал')
                next_at = self._clock() + RETRY_SECONDS
            with self._cond:
                if self._dirty:
                    continue
                self._cond.wait(self.sleep_for(next_at, self._clock()))

    @staticmethod
    def sleep_for(next_at, now):
        """Сколько спать: до срока (чуть позже — сравнение в SQL «<= сейчас»), но не дольше
        MAX_SLEEP_SECONDS; сроков нет — ровно потолок."""
        if next_at is None:
            return float(MAX_SLEEP_SECONDS)
        return min(float(MAX_SLEEP_SECONDS), max(0.0, next_at - now) + 0.2)
