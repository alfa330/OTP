# -*- coding: utf-8 -*-
"""Совместимость: клиент Binotel API переехал в binotel/client.py.

Binotel стоит уже не только у ТЭЗ, поэтому клиент официального API стал общим
пакетом. Здесь — те же имена, что были, чтобы bot_schedule2.py, соседние модули
и тесты продолжали работать без правок. Новый код импортирует `binotel.client`.
"""
from binotel.client import *  # noqa: F401,F403
from binotel.client import (  # noqa: F401 — приватные имена, которыми пользуются соседи и тесты
    _day_bounds_unix, _main, _parse_env_file, _to_int, _too_frequent_wait_seconds, _tzinfo,
    _LAST_REQUEST_AT, _RATE_LOCK,
)

if __name__ == "__main__":
    _main()
