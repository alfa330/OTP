# -*- coding: utf-8 -*-
"""Пульс телефона: сервер закрывает статус оператора, чей iCORE Phone перестал выходить на связь.

Решение владельца 16.11.2026: телефон молчит 30 минут — статус закрывается; телефон появился —
статус продолжается. Здесь правила (чистые функции) и обход с подставным хранилищем; настоящий
SQL — в tests/test_phone_liveness_postgres.py. Люди выдуманы.
"""
import contextlib
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from phone_liveness import rules
from phone_liveness import service as liveness_service
from phone_liveness.service import PhoneLivenessService

NOW = datetime(2026, 11, 16, 12, 0, 0)


def minutes(value):
    return timedelta(minutes=value)


def row(operator_id=7, key='готов', event_ago=120, pulse_ago=45, note=None, event_id=500, seen_ago=None):
    """Строка оператора: событие event_ago минут назад, пульс телефона pulse_ago минут назад,
    активность любой сессии seen_ago минут назад (None — таких сессий нет)."""
    return {'operator_id': operator_id, 'event_id': event_id, 'key': key, 'note': note,
            'event_at': NOW - minutes(event_ago),
            'pulse': None if pulse_ago is None else NOW - minutes(pulse_ago),
            'seen': None if seen_ago is None else NOW - minutes(seen_ago)}


class CloseRuleTests(unittest.TestCase):
    def test_thirty_minutes_of_silence_close_the_status(self):
        self.assertTrue(rules.wants_close(row(pulse_ago=30), NOW))
        self.assertFalse(rules.wants_close(row(pulse_ago=29), NOW))

    def test_a_talking_or_resting_phone_is_closed_too(self):
        """Умер телефон — висит любой статус: и разговор, и перерыв."""
        for key in ('занят', 'соединение', 'перерыв', 'исход', 'перезвон', 'офлайн', 'тренинг'):
            self.assertTrue(rules.wants_close(row(key=key), NOW), key)

    def test_switched_off_phone_has_nothing_to_close(self):
        self.assertFalse(rules.wants_close(row(key='выключен'), NOW))
        self.assertFalse(rules.wants_close(row(key='выключен', note=rules.CLOSE_NOTE), NOW))

    def test_the_freshest_sign_of_life_counts(self):
        """Оператор час стоит «готов» без звонков, но телефон на связи — статус живой."""
        self.assertFalse(rules.wants_close(row(event_ago=600, pulse_ago=4), NOW))
        # Событие свежее пульса: активность сессии отмечается не чаще раза в пять минут.
        self.assertFalse(rules.wants_close(row(event_ago=3, pulse_ago=50), NOW))

    def test_status_set_from_the_browser_is_never_closed(self):
        """Чат-менеджеры отдела продаж ставят статус из рабочего места в браузере: сессии
        телефона у них нет или она давно молчит. Их статус телефону не принадлежит — по
        молчанию телефона его закрывать нельзя, человек работает."""
        browser_only = row(event_ago=300, pulse_ago=None, seen_ago=200)
        self.assertFalse(rules.phone_owned(browser_only))
        self.assertFalse(rules.wants_close(browser_only, NOW))
        self.assertFalse(rules.wants_close(row(event_ago=300, pulse_ago=None), NOW))
        # Телефон закрыли вчера, статус сегодня поставлен из браузера.
        stale_phone = row(event_ago=120, pulse_ago=1500)
        self.assertFalse(rules.phone_owned(stale_phone))
        self.assertFalse(rules.wants_close(stale_phone, NOW))

    def test_status_belongs_to_the_phone_that_was_alive_at_the_event(self):
        # Активность сессии отмечается не чаще раза в пять минут: пульс отстаёт от события.
        self.assertTrue(rules.phone_owned(row(event_ago=120, pulse_ago=125)))
        self.assertTrue(rules.wants_close(row(event_ago=120, pulse_ago=125), NOW))
        self.assertFalse(rules.phone_owned(row(event_ago=120, pulse_ago=127)))

    def test_a_person_seen_anywhere_is_not_closed(self):
        """Телефон упал, а сайт у человека открыт — он на месте и мёртвый телефон увидит сам."""
        self.assertFalse(rules.wants_close(row(pulse_ago=45, seen_ago=4), NOW))
        self.assertTrue(rules.wants_close(row(pulse_ago=45, seen_ago=31), NOW))
        self.assertEqual(rules.close_moment(row(pulse_ago=45, seen_ago=31), NOW), NOW - minutes(21))

    def test_the_term_is_the_owner_setting(self):
        self.assertFalse(rules.wants_close(row(pulse_ago=45), NOW, silence=3600))
        self.assertTrue(rules.wants_close(row(pulse_ago=61), NOW, silence=3600))
        self.assertEqual(rules.SILENCE_SECONDS, 30 * 60)

    def test_status_is_closed_ten_minutes_after_the_last_contact(self):
        """Телефон выходит на связь раз в десять минут: умер он между последней связью и
        следующей, несостоявшейся."""
        self.assertEqual(rules.close_moment(row(pulse_ago=45), NOW), NOW - minutes(35))

    def test_close_moment_stays_between_the_event_and_now(self):
        # Событие свежее пульса: закрытие не раньше самого события.
        late = row(event_ago=20, pulse_ago=24)
        self.assertEqual(rules.close_moment(late, NOW), NOW - minutes(10))
        self.assertGreater(rules.close_moment(late, NOW), late['event_at'])
        # Закрытие не уходит в будущее.
        self.assertEqual(rules.close_moment(row(event_ago=600, pulse_ago=5), NOW), NOW)

    def test_one_silence_is_closed_once(self):
        first = row(pulse_ago=45)
        self.assertEqual(rules.close_event_id(first), rules.close_event_id(dict(first)))
        self.assertTrue(rules.close_event_id(first).startswith(rules.CLOSE_ID_PREFIX))
        self.assertNotEqual(rules.close_event_id(first), rules.close_event_id(row(pulse_ago=46)))
        self.assertLessEqual(len(rules.close_event_id(first)), 64)


class RestoreRuleTests(unittest.TestCase):
    def closed(self, closed_ago=40, pulse_ago=3, note=rules.CLOSE_NOTE):
        return row(key='выключен', note=note, event_ago=closed_ago, pulse_ago=pulse_ago)

    def test_phone_is_back_after_a_server_close(self):
        self.assertTrue(rules.wants_restore(self.closed(), NOW))

    def test_a_minute_is_left_for_the_phone_to_flush_its_queue(self):
        self.assertFalse(rules.wants_restore(self.closed(pulse_ago=0.5), NOW))
        self.assertTrue(rules.wants_restore(self.closed(pulse_ago=1), NOW))

    def test_only_a_new_sign_of_life_restores(self):
        self.assertFalse(rules.wants_restore(self.closed(closed_ago=40, pulse_ago=50), NOW))
        self.assertFalse(rules.wants_restore(self.closed(pulse_ago=None), NOW))

    def test_only_the_phone_itself_brings_the_status_back(self):
        """Оператор умершего телефона открыл сайт из дома — рабочий статус от этого не возвращается."""
        home_visit = row(key='выключен', note=rules.CLOSE_NOTE, event_ago=40, pulse_ago=50, seen_ago=3)
        self.assertFalse(rules.wants_restore(home_visit, NOW))

    def test_the_operators_own_exit_is_never_undone(self):
        self.assertFalse(rules.wants_restore(self.closed(note=None), NOW))
        self.assertFalse(rules.wants_restore(self.closed(note='авто'), NOW))
        self.assertFalse(rules.wants_restore(row(key='готов', pulse_ago=3), NOW))


class PlanTests(unittest.TestCase):
    def plan(self, rows, freshest_ago=1, uptime=3600):
        freshest = None if freshest_ago is None else NOW - minutes(freshest_ago)
        return rules.plan(rows, NOW, freshest, uptime)

    def test_dead_phone_among_living_ones(self):
        rows = [row(1, pulse_ago=45), row(2, pulse_ago=3), row(3, pulse_ago=8), row(4, key='перерыв', pulse_ago=2)]
        decision = self.plan(rows)
        self.assertEqual([r['operator_id'] for r in decision['close']], [1])
        self.assertIsNone(decision['held'])

    def test_fresh_server_start_only_watches(self):
        """После деплоя запросы телефонов не доходили — пятнадцать минут никого не закрываем."""
        decision = self.plan([row(1, pulse_ago=45)], uptime=rules.WARMUP_SECONDS - 1)
        self.assertEqual((decision['close'], decision['held']), ([], 'warmup'))
        self.assertEqual(len(self.plan([row(1, pulse_ago=45)], uptime=rules.WARMUP_SECONDS)['close']), 1)

    def test_stale_pulse_everywhere_means_the_server_is_blind(self):
        """Молчат не телефоны — сервер перестал отмечать активность сессий."""
        rows = [row(1, pulse_ago=45), row(2, pulse_ago=40)]
        self.assertEqual(self.plan(rows, freshest_ago=11)['held'], 'pulse_stale')
        self.assertEqual(self.plan(rows, freshest_ago=None)['held'], 'pulse_stale')
        self.assertIsNone(self.plan(rows, freshest_ago=9)['held'])

    def test_mass_silence_is_a_failure_not_people(self):
        rows = [row(i, pulse_ago=45) for i in range(1, 7)] + [row(7, pulse_ago=2), row(8, pulse_ago=2)]
        decision = self.plan(rows)
        self.assertEqual((decision['close'], decision['held'], decision['candidates']), ([], 'mass_silence', 6))
        # В офисе пропал свет у пятерых из двадцати — это люди, их закрываем.
        rows = [row(i, pulse_ago=45) for i in range(1, 6)] + [row(i, pulse_ago=2) for i in range(6, 21)]
        self.assertEqual(len(self.plan(rows)['close']), 5)
        # Шестеро из двадцати — меньше половины открытых статусов: тоже люди.
        rows = [row(i, pulse_ago=45) for i in range(1, 7)] + [row(i, pulse_ago=2) for i in range(7, 21)]
        self.assertEqual(len(self.plan(rows)['close']), 6)

    def test_restore_never_waits_for_the_guards(self):
        back = row(9, key='выключен', note=rules.CLOSE_NOTE, event_ago=40, pulse_ago=3)
        decision = self.plan([back, row(1, pulse_ago=45)], freshest_ago=None, uptime=5)
        self.assertEqual([r['operator_id'] for r in decision['restore']], [9])
        self.assertEqual(decision['close'], [])

    def test_quiet_sweep_has_no_reason_to_hold(self):
        decision = self.plan([row(1, pulse_ago=2)], freshest_ago=None, uptime=5)
        self.assertEqual((decision['close'], decision['restore'], decision['held']), ([], [], None))


class _Db(object):
    def __init__(self):
        self.appended = []
        self.rebuilt = []
        self.fail_for = set()
        self.duplicate_for = set()

    @contextlib.contextmanager
    def _get_cursor(self):
        yield object()

    def append_operator_status_event(self, operator_id, event_at, status_key, state_note=None,
                                     event_kind=None, client_event_id=None):
        if operator_id in self.fail_for:
            raise RuntimeError('база не ответила')
        if operator_id in self.duplicate_for:
            return {'duplicate': True}
        self.appended.append((operator_id, event_at, status_key, state_note, client_event_id))
        return {'duplicate': False, 'event_id': 1}

    def rebuild_operator_status_days(self, operator_id, start_date, end_date):
        self.rebuilt.append((operator_id, start_date, end_date))


class SweepTests(unittest.TestCase):
    def service(self, rows, freshest_ago=1, previous='готов', uptime=3600, codes=('op', 'tez'), now=NOW):
        self.db = _Db()
        self.loaded = []
        clock = {'t': 0.0}

        def load_rows(cursor, codes_arg, now_arg):
            self.loaded.append((tuple(codes_arg), now_arg))
            return rows

        fake_store = mock.Mock()
        fake_store.load_rows.side_effect = load_rows
        fake_store.freshest_pulse.return_value = None if freshest_ago is None else now - minutes(freshest_ago)
        fake_store.previous_status.return_value = previous
        patcher = mock.patch.object(liveness_service, 'store', fake_store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = fake_store
        service = PhoneLivenessService(self.db, codes, now=lambda: now, monotonic=lambda: clock['t'])
        clock['t'] = float(uptime)
        return service

    def test_closes_the_silent_phone_with_a_marked_event(self):
        service = self.service([row(7, pulse_ago=45), row(8, pulse_ago=2)])
        self.assertEqual(service.sweep(), {'closed': 1, 'restored': 0, 'held': None})
        operator_id, event_at, key, note, event_id = self.db.appended[0]
        self.assertEqual((operator_id, event_at, key, note), (7, NOW - minutes(35), 'выключен', rules.CLOSE_NOTE))
        self.assertTrue(event_id.startswith(rules.CLOSE_ID_PREFIX))
        self.assertEqual(self.loaded, [(('op', 'tez'), NOW)])
        self.assertEqual(self.db.rebuilt, [])              # закрытие сегодняшнее — событие пересобрало само

    def test_restores_the_previous_status_at_the_new_contact(self):
        back = row(9, key='выключен', note=rules.CLOSE_NOTE, event_ago=40, pulse_ago=3)
        service = self.service([back], previous='перерыв')
        self.assertEqual(service.sweep(), {'closed': 0, 'restored': 1, 'held': None})
        operator_id, event_at, key, note, event_id = self.db.appended[0]
        # Статус продолжен с момента закрытия: телефон работал, пропадала только связь —
        # время между закрытием и возвратом оператору не срезается.
        self.assertEqual((operator_id, event_at, key, note),
                         (9, NOW - minutes(40) + timedelta(seconds=1), 'перерыв', rules.RESTORE_NOTE))
        self.assertTrue(event_id.startswith(rules.RESTORE_ID_PREFIX))
        self.assertEqual(self.db.rebuilt, [])

    def test_restore_after_a_long_silence_rebuilds_the_days_between(self):
        back = row(9, key='выключен', note=rules.CLOSE_NOTE, event_ago=3 * 24 * 60, pulse_ago=3)
        service = self.service([back])
        self.assertEqual(service.sweep()['restored'], 1)
        closed_day = (NOW - minutes(3 * 24 * 60)).date()
        self.assertEqual(self.db.rebuilt, [(9, closed_day + timedelta(days=2), NOW.date())])

    def test_one_close_is_undone_once(self):
        back = row(9, key='выключен', note=rules.CLOSE_NOTE, event_ago=40, pulse_ago=3)
        later = dict(back, pulse=NOW - minutes(1))
        self.assertEqual(rules.restore_event_id(back), rules.restore_event_id(later))
        self.assertNotEqual(rules.restore_event_id(back),
                            rules.restore_event_id(dict(back, event_at=NOW - minutes(41))))

    def test_nothing_to_continue_means_no_event(self):
        back = row(9, key='выключен', note=rules.CLOSE_NOTE, event_ago=40, pulse_ago=3)
        service = self.service([back], previous=None)
        self.assertEqual(service.sweep()['restored'], 0)
        self.assertEqual(self.db.appended, [])

    def test_old_death_rebuilds_the_days_after_it(self):
        """Телефон умер три дня назад: событие пересоберёт свой день и соседние, остальные — сами."""
        dead = row(7, event_ago=3 * 24 * 60 + 60, pulse_ago=3 * 24 * 60)
        service = self.service([dead])
        self.assertEqual(service.sweep()['closed'], 1)
        close_day = (NOW - minutes(3 * 24 * 60 - 10)).date()
        self.assertEqual(self.db.rebuilt, [(7, close_day + timedelta(days=2), NOW.date())])
        self.assertEqual(close_day, date(2026, 11, 13))

    def test_one_failure_does_not_stop_the_sweep(self):
        service = self.service([row(7, pulse_ago=45), row(8, pulse_ago=50)])
        self.db.fail_for.add(7)
        with self.assertLogs('phone_liveness.service', level='ERROR'):
            self.assertEqual(service.sweep()['closed'], 1)
        self.assertEqual([item[0] for item in self.db.appended], [8])

    def test_a_repeated_close_is_not_counted(self):
        service = self.service([row(7, pulse_ago=45)])
        self.db.duplicate_for.add(7)
        self.assertEqual(service.sweep()['closed'], 0)

    def test_guards_are_logged_once_and_nothing_is_written(self):
        service = self.service([row(i, pulse_ago=45) for i in range(1, 8)])
        with self.assertLogs('phone_liveness.service', level='WARNING') as logs:
            self.assertEqual(service.sweep(), {'closed': 0, 'restored': 0, 'held': 'mass_silence'})
            service.sweep()
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(self.db.appended, [])
        service = self.service([row(1, pulse_ago=45)], freshest_ago=None)
        with self.assertLogs('phone_liveness.service', level='WARNING'):
            self.assertEqual(service.sweep()['held'], 'pulse_stale')

    def test_without_departments_the_database_is_not_touched(self):
        service = self.service([row(7, pulse_ago=45)], codes=())
        self.assertEqual(service.sweep(), {'closed': 0, 'restored': 0, 'held': None})
        self.assertEqual(self.loaded, [])

    def test_term_cannot_be_set_below_ten_minutes(self):
        """Телефон выходит на связь раз в десять минут — короче срок закрывал бы работающих."""
        service = PhoneLivenessService(_Db(), ['op'], silence_seconds=60)
        self.assertEqual(service.silence_seconds, 600)


if __name__ == '__main__':
    unittest.main()
