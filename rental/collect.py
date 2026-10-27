# -*- coding: utf-8 -*-
"""Один срез для ночного отчёта: Yume и три GPS-системы разом.

Базу здесь не трогаем вовсе: сбор — это минута внешних запросов, и держать всё это время
открытую транзакцию нельзя (20 минут открытой транзакции скрипта 22.10.2026 вместе с
деплоем положили портал). Сервис сначала собирает, потом одной короткой транзакцией пишет.

Сбой одного источника не роняет отчёт: строки собираются из того, что ответило, а в
`sources` остаётся, кто не ответил и почему, — экран показывает это ночной смене. Yume,
ГЛОНАСС и Efir опрашиваются параллельно; Wialon — после Efir: у Efir нет своего адреса,
и его точки уходят в геокодер Wialon той же сессией.
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import rules
from .sources import EfirSource, GlonassSource, SourceError, WialonSource, YumeSource

WIALON_ACCOUNTS = 4


def config_from_env(env=None):
    """Доступы из окружения. Пароли дальше клиентов не уходят и нигде не пишутся."""
    env = os.environ if env is None else env

    def value(key):
        return str(env.get(key) or '').strip()

    wialon = []
    for index in range(1, WIALON_ACCOUNTS + 1):
        wialon.append({'token': value('WIALON_TOKEN_%d' % index),
                       'login': value('WIALON_LOGIN_%d' % index),
                       'password': value('WIALON_PASSWORD_%d' % index)})
    return {
        'yume': {'login': value('YUME_CLOUD_LOGIN'), 'password': value('YUME_CLOUD_PASSWORD')},
        'wialon': wialon,
        'glonass': {'login': value('GLONASS_SOFT_LOGIN'), 'password': value('GLONASS_SOFT_PASSWORD')},
        'efir': {'url': value('EFIR_SOFT_URL'), 'login': value('EFIR_SOFT_LOGIN'),
                 'password': value('EFIR_SOFT_PASSWORD')},
    }


def _source_status(ok, count, error=None):
    return {'ok': bool(ok), 'count': int(count or 0), 'error': error or None}


def _safe(name, fetch):
    try:
        return fetch(), None
    except SourceError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 — сеть, разбор, что угодно: источник просто не ответил
        logging.warning('rental: источник %s не ответил: %s', name, exc, exc_info=True)
        return None, 'не ответил (%s)' % type(exc).__name__


def collect(config, *, now_ts=None, make_sources=None):
    """{'rows', 'sources', 'collected_ts'} — строки отчёта и итог по каждому источнику."""
    make = make_sources or default_sources
    yume, wialon, glonass, efir = make(config)
    started = time.time()

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix='rental-src') as pool:
        yume_future = pool.submit(_safe, 'yume', yume.fetch)
        glonass_future = pool.submit(_safe, 'glonass', glonass.fetch)
        efir_future = pool.submit(_safe, 'efir', efir.fetch)
        efir_units, efir_error = efir_future.result()
        efir_units = efir_units or []
        need_address = [u for u in efir_units if u.get('lat') is not None and u.get('lon') is not None]
        wialon_result, wialon_fatal = _safe(
            'wialon', lambda: wialon.fetch([(u['lat'], u['lon']) for u in need_address]))
        yume_cars, yume_error = yume_future.result()
        glonass_units, glonass_error = glonass_future.result()

    wialon_units, efir_addresses, wialon_errors = wialon_result or ([], [None] * len(need_address), [])
    for unit, address in zip(need_address, efir_addresses):
        unit['address'] = address
    glonass_units = glonass_units or []
    wialon_error = wialon_fatal or ('; '.join(wialon_errors) if wialon_errors else None)

    now_ts = time.time() if now_ts is None else now_ts
    rows = rules.build_rows(wialon_units + glonass_units + efir_units, yume_cars or {}, now_ts,
                            yume_ok=yume_cars is not None)
    sources = {
        'yume': _source_status(yume_cars is not None, len([c for c in (yume_cars or {}).values()
                                                            if not c.get('deleted')]), yume_error),
        'wialon': _source_status(not wialon_error, len(wialon_units), wialon_error),
        'glonass': _source_status(glonass_error is None, len(glonass_units), glonass_error),
        'efir': _source_status(efir_error is None, len(efir_units), efir_error),
    }
    logging.info('rental: срез за %.1f с — строк %d, источники %s', time.time() - started, len(rows),
                 {name: (s['ok'], s['count']) for name, s in sources.items()})
    return {'rows': rows, 'sources': sources, 'collected_ts': now_ts}


def default_sources(config):
    return (
        YumeSource(config['yume']['login'], config['yume']['password']),
        WialonSource(config['wialon']),
        GlonassSource(config['glonass']['login'], config['glonass']['password']),
        EfirSource(config['efir']['url'], config['efir']['login'], config['efir']['password']),
    )
