"""Будильник сроков «Кодов 2FA»: сгорание запроса, стирание цифр, удаление сообщения.

Без фонового опроса (тот же запрет, что у колокола): нить спит ровно до ближайшего срока,
который сервис сам называет из базы (queries.next_due). Новый срок — новый запрос или
отправленный код — будит её сразу (wake). Сроков впереди нет — нить спит, пока не разбудят.

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
                timeout = None if next_at is None else max(0.0, next_at - self._clock())
                # Чуть позже срока, а не ровно в него: сравнение в SQL — «<= сейчас».
                self._cond.wait(None if timeout is None else timeout + 0.2)
