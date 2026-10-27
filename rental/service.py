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
from datetime import timedelta

from . import access, catalog, collect, queries, rules
from .sources import SourceError, YumeSource

GPS_SOURCES = ('wialon', 'glonass', 'efir')

# Снимок парка Yume для «Наличия авто» (#391): старше 10 минут — экран сам ставит обновление;
# после неудачной попытки — не чаще раза в 2 минуты, чтобы открытый у тридцати операторов раздел
# не долбил Yume каждым открытием.
FLEET_STALE = timedelta(minutes=10)
FLEET_RETRY = timedelta(minutes=2)


class Busy(Exception):
    """Сбор уже идёт."""


class RentalService:
    def __init__(self, *, cursor_factory, config_loader=collect.config_from_env, collector=collect.collect,
                 now=queries.now_almaty, start_thread=None, fleet_loader=None):
        self._cursor = cursor_factory
        self._config = config_loader
        self._collect = collector
        self._now = now
        self._start_thread = start_thread or self._spawn
        self._lock = threading.Lock()
        self._running = None
        self._fleet_loader = fleet_loader or self._load_fleet
        self._fleet_lock = threading.Lock()
        self._fleet_running = False

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

    # ═══ Наличие авто и направления (#391) ══════════════════════════════════

    def _load_fleet(self):
        yume = self._config()['yume']
        return YumeSource(yume['login'], yume['password']).fetch_cars()

    def fleet_refreshing(self):
        return self._fleet_running

    def refresh_fleet(self, *, force=False):
        """Поставить обновление снимка парка Yume своей нитью. True — поставлено.

        Без force — только если снимок устарел и недавно не пробовали: так раздел у тридцати
        операторов сам держит наличие свежим, но Yume спрашивают раз в 10 минут, а не на каждом
        открытии.
        """
        now = self._now()
        with self._fleet_lock:
            if self._fleet_running:
                return False
            if not force:
                with self._cursor() as cursor:
                    sync = queries.fleet_sync(cursor)
                if sync['synced_at'] and now - sync['synced_at'] < FLEET_STALE:
                    return False
                if sync['attempted_at'] and now - sync['attempted_at'] < FLEET_RETRY:
                    return False
            self._fleet_running = True
        try:
            with self._cursor() as cursor:
                queries.mark_fleet_attempt(cursor, now)
        except Exception:
            with self._fleet_lock:
                self._fleet_running = False
            raise
        self._start_thread(self._run_fleet)
        return True

    def _run_fleet(self):
        try:
            try:
                cars = self._fleet_loader()
            except SourceError as exc:
                error = str(exc)
            except Exception as exc:  # noqa: BLE001 — сеть, разбор: Yume просто не ответил
                logging.warning('rental: снимок Yume не обновился: %s', exc, exc_info=True)
                error = 'Yume не ответил (%s)' % type(exc).__name__
            else:
                with self._cursor() as cursor:
                    queries.save_fleet(cursor, cars, self._now())
                return
            with self._cursor() as cursor:
                queries.save_fleet_error(cursor, error, self._now())
        except Exception:  # noqa: BLE001 — нить не должна умирать молча
            logging.exception('rental: запись снимка Yume не удалась')
        finally:
            with self._fleet_lock:
                self._fleet_running = False

    # ─── Карточки ───────────────────────────────────────────────────────────

    def save_card(self, ctx, payload, card_id=None):
        """('ok', карточка) / ('forbidden'|'not_found', None) / ('bad_input', текст ошибки)."""
        if not access.can_edit_cards(ctx):
            return 'forbidden', None
        card, error = catalog.clean_card(payload)
        if error:
            return 'bad_input', error
        now = self._now()
        with self._cursor() as cursor:
            if card_id is None:
                return 'ok', queries.create_card(cursor, card, ctx, now)
            current = queries.get_card(cursor, card_id, for_update=True)
            if not current or not current['is_active']:
                return 'not_found', None
            queries.update_card(cursor, current, card, ctx, now)
            return 'ok', queries.get_card(cursor, card_id)

    def set_free_count(self, ctx, card_id, value):
        """Свободные машины карточки (ТЗ 3.1): вводят сотрудники аренды, Yume их не трогает."""
        if not access.can_edit_cards(ctx):
            return 'forbidden', None
        count, error = catalog.clean_free_count(value)
        if error:
            return 'bad_input', error
        with self._cursor() as cursor:
            current = queries.get_card(cursor, card_id, for_update=True)
            if not current or not current['is_active']:
                return 'not_found', None
            if current.get('free_at') and current.get('free_count') == count:
                return 'ok', current
            queries.set_free_count(cursor, current, count, ctx, self._now())
            return 'ok', queries.get_card(cursor, card_id)

    def hide_card(self, ctx, card_id):
        if not access.can_edit_cards(ctx):
            return 'forbidden'
        with self._cursor() as cursor:
            current = queries.get_card(cursor, card_id, for_update=True)
            if not current or not current['is_active']:
                return 'not_found'
            queries.hide_card(cursor, current, ctx, self._now())
        return 'ok'

    # ─── Направления ────────────────────────────────────────────────────────

    def create_direction(self, ctx, payload):
        """«Направить в офис» (ТЗ 3.2). ('ok', направление) / ('forbidden'|'no_card'|'no_free', None) /
        ('bad_input', текст). Машину не резервирует: число свободных не меняется."""
        if not access.can_send(ctx):
            return 'forbidden', None
        now = self._now()
        with self._cursor() as cursor:
            card_id = payload.get('card_id')
            card = queries.get_card(cursor, card_id) if str(card_id or '').isdigit() else None
            if not card or not card['is_active']:
                return 'no_card', None
            # ТЗ 3.1: свободных 0 — направление заблокировано; не указано ни разу — тоже.
            if not card.get('free_at') or not card.get('free_count'):
                return 'no_free', None
            fields, error = catalog.clean_direction(payload, today=now.date(), parks=set(queries.sales_parks(cursor)))
            if error:
                return 'bad_input', error
            visit = catalog.visit_at(fields['visit_date'], fields['visit_time'])
            return 'ok', queries.create_direction(cursor, fields, card, ctx, visit, now)

    def update_direction(self, ctx, direction_id, payload):
        """Итоговый статус и комментарий (ТЗ 4) — сотрудник аренды и руководители."""
        if not access.can_work_directions(ctx):
            return 'forbidden', None
        changes = {}
        if 'status' in payload:
            if payload.get('status') not in catalog.STATUSES:
                return 'bad_input', 'Такого статуса нет'
            changes['status'] = payload['status']
        if 'comment' in payload:
            changes['comment'] = str(payload.get('comment') or '').strip()[:catalog.COMMENT_MAX] or None
        if not changes:
            return 'bad_input', 'Нечего менять'
        with self._cursor() as cursor:
            current = queries.get_direction(cursor, direction_id, for_update=True)
            if not current:
                return 'not_found', None
            queries.update_direction(cursor, current, changes, ctx, self._now())
            return 'ok', queries.get_direction(cursor, direction_id)

    # ─── Условия работы ─────────────────────────────────────────────────────

    def save_conditions(self, ctx, blocks):
        """Новая версия «Условий работы» (ТЗ 10.3): прежние остаются в истории."""
        if not access.can_edit_conditions(ctx):
            return 'forbidden', None
        cleaned, error = catalog.clean_blocks(blocks)
        if error:
            return 'bad_input', error
        with self._cursor() as cursor:
            current = queries.conditions_current(cursor)
            if current and current['blocks'] == cleaned:
                return 'ok', current
            return 'ok', queries.save_conditions(cursor, cleaned, ctx, self._now())


_SOURCE_NAMES = {'yume': 'Yume', 'wialon': 'Wialon', 'glonass': 'ГЛОНАСС', 'efir': 'Efir'}
