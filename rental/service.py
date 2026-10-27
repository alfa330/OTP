# -*- coding: utf-8 -*-
"""Ночной отчёт: сбор своим потоком и ручное «Состояние».

Сбор — это ~10 секунд внешних запросов (collect.py). Поэтому:

* он идёт СВОЕЙ нитью, а не в общем пуле бота (4 потока на всё) и не в потоке запроса:
  планировщик и кнопка «Собрать» только ставят сбор и сразу возвращаются;
* база в это время не держится: строка отчёта переводится в «собирается» короткой
  транзакцией, потом сбор без соединения, потом результат — второй короткой транзакцией;
* одновременно идёт один сбор (замок процесса + блокировка строки отчёта в базе).

Пересбор уже собранного отчёта заменяет данные, только если ответили все GPS-системы:
иначе машины из молчащей системы стали бы «Нет GPS», а их строки пропали бы. Тогда
прежние данные остаются, а в отчёте — причина, почему обновить не вышло.
"""

import logging
import threading

from . import access, collect, queries, rules

GPS_SOURCES = ('wialon', 'glonass', 'efir')


class Busy(Exception):
    """Сбор уже идёт."""


class RentalService:
    def __init__(self, *, cursor_factory, config_loader=collect.config_from_env, collector=collect.collect,
                 now=queries.now_almaty, start_thread=None):
        self._cursor = cursor_factory
        self._config = config_loader
        self._collect = collector
        self._now = now
        self._start_thread = start_thread or self._spawn
        self._lock = threading.Lock()
        self._running = None

    @staticmethod
    def _spawn(target):
        thread = threading.Thread(target=target, name='rental-night-report', daemon=True)
        thread.start()
        return thread

    # ─── Права ──────────────────────────────────────────────────────────────

    def context(self, user_id):
        with self._cursor() as cursor:
            return queries.load_access_context(cursor, user_id)

    # ─── Сбор ───────────────────────────────────────────────────────────────

    def running(self):
        return self._running

    def start(self, kind, slot, *, by=None):
        """Поставить сбор отчёта слота. Возвращает отчёт в состоянии «собирается».

        Busy — сбор уже идёт (этот или другой отчёт).
        """
        if kind not in rules.KINDS:
            raise ValueError('kind')
        with self._lock:
            if self._running is not None:
                raise Busy()
            now = self._now()
            with self._cursor() as cursor:
                queries.expire_stale(cursor, now)
                report, busy = queries.claim_report(cursor, kind, slot, by, now)
                had_rows = queries.has_rows(cursor, report['id']) if not busy else False
            if busy:
                raise Busy()
            self._running = report['id']
        self._start_thread(lambda: self._run(report['id'], had_rows))
        return report

    def scheduled(self, kind):
        """Планировщик: отчёт сегодняшнего слота (01:00 или 07:00). Идёт сбор — пропускаем."""
        slot = rules.slot_at(kind, self._now().date())
        try:
            return self.start(kind, slot)
        except Busy:
            logging.warning('rental: отчёт %s не поставлен — уже идёт сбор', rules.KIND_LABELS[kind])
            return None

    def _run(self, report_id, had_rows):
        try:
            try:
                result = self._collect(self._config())
            except Exception as exc:  # noqa: BLE001
                logging.exception('rental: сбор отчёта %s упал', report_id)
                self._finish(report_id, status='failed', sources={}, error='сбор не удался (%s)' % type(exc).__name__)
                return
            sources = result.get('sources') or {}
            failed_gps = [name for name in GPS_SOURCES if not (sources.get(name) or {}).get('ok')]
            nothing = not any((s or {}).get('ok') for s in sources.values())
            collected_at = rules.almaty_from_ts(result.get('collected_ts'))
            if nothing:
                self._finish(report_id, status='failed', sources=sources,
                             error='ни Yume, ни GPS-системы не ответили — данные прежние' if had_rows
                             else 'ни Yume, ни GPS-системы не ответили')
                return
            if had_rows and failed_gps:
                self._finish(report_id, status='ready', sources=sources, keep_rows=True,
                             error='обновить не вышло: %s не ответил — данные прежние' % ', '.join(
                                 _SOURCE_NAMES[name] for name in failed_gps))
                return
            with self._cursor() as cursor:
                queries.save_rows(cursor, report_id, result.get('rows') or [])
                queries.finish_report(cursor, report_id, status='ready', sources=sources, error=None,
                                      collected_at=collected_at, now=self._now())
        except Exception:  # noqa: BLE001 — нить не должна умирать молча
            logging.exception('rental: запись отчёта %s не удалась', report_id)
            try:
                self._finish(report_id, status='failed', sources={}, error='отчёт не записался')
            except Exception:  # noqa: BLE001
                logging.exception('rental: и отметка об отказе не записалась')
        finally:
            with self._lock:
                self._running = None

    def _finish(self, report_id, *, status, sources, error, keep_rows=False):
        with self._cursor() as cursor:
            if keep_rows:
                # Прежние строки и прежний список источников остаются: экран покажет причину.
                report = queries.get_report(cursor, report_id)
                sources = (report or {}).get('sources') or sources
            queries.finish_report(cursor, report_id, status=status, sources=sources, error=error,
                                  collected_at=None, now=self._now())

    # ─── «Состояние» вручную ────────────────────────────────────────────────

    def set_state(self, ctx, row_id, state):
        """('ok', строка) / ('not_found', None) / ('bad_state', None)."""
        if state is not None and state not in rules.STATES:
            return 'bad_state', None
        now = self._now()
        with self._cursor() as cursor:
            row = queries.get_row(cursor, row_id, for_update=True)
            if not row:
                return 'not_found', None
            if not access.can_open_night_report(ctx):
                return 'forbidden', None
            if row.get('state') == state and row.get('state_source') == rules.STATE_BY_HAND:
                return 'ok', row
            queries.set_state(cursor, row, state, ctx, now)
            return 'ok', queries.get_row(cursor, row_id)


_SOURCE_NAMES = {'yume': 'Yume', 'wialon': 'Wialon', 'glonass': 'ГЛОНАСС', 'efir': 'Efir'}
