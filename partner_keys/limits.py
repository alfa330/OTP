# -*- coding: utf-8 -*-
"""Ограничитель частоты вызовов ключа партнёра — общий для всех внешних ручек."""

import threading
import time
from collections import deque


class RateLimiter:
    """Скользящая минута на ключ. В памяти процесса: экземпляр портала один."""

    def __init__(self, per_minute, clock=None):
        self.per_minute = int(per_minute)
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._hits = {}

    def allow(self, key_id):
        """(пускаем?, сколько секунд ждать)."""
        now = self._clock()
        with self._lock:
            window = self._hits.setdefault(int(key_id), deque())
            while window and now - window[0] >= 60.0:
                window.popleft()
            if len(window) >= self.per_minute:
                return False, max(1, int(60.0 - (now - window[0])) + 1)
            window.append(now)
            return True, 0
