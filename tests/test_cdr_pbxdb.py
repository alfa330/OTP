# -*- coding: utf-8 -*-
"""Чтение журнала очередей из базы станции: что мост себе позволяет, а что нет.

Станцию однажды уже положили тремя запросами (25.08.2026), поэтому здесь закреплены
именно ограничители, а не удобство:
  * без настроек источник выключен и мост ведёт себя как раньше;
  * окно шире суток с хвостом не уходит в базу вовсе;
  * запрос один, по индексированной колонке `time`, с потолком строк и белым списком
    событий — паузы операторов и попытки дозвона остаются на станции;
  * оборванное соединение переоткрывается один раз, вторая неудача подряд — ошибка.
"""

import unittest
from datetime import datetime, timedelta
from unittest import mock

from cdr_bridge import pbxdb

START = datetime(2026, 9, 21)
END = datetime(2026, 9, 22, 1)


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.conn.broken:
            raise RuntimeError('соединение потеряно')
        self.conn.queries.append((' '.join(str(sql).split()), list(params or [])))

    def fetchall(self):
        return self.conn.rows


class _Conn:
    def __init__(self, rows=(), broken=False):
        self.rows = list(rows)
        self.broken = broken
        self.queries = []
        self.closed = 0

    def cursor(self):
        return _Cursor(self)

    def close(self):
        self.closed += 1


def row(callid='1.1', event='ENTERQUEUE', at='2026-09-21 09:00:26', queue='3041'):
    return (datetime.strptime(at, '%Y-%m-%d %H:%M:%S'), callid, queue, 'NONE', event, '', '', '')


class DisabledTests(unittest.TestCase):
    def test_without_settings_it_is_off_and_silent(self):
        source = pbxdb.PbxDb(connect=lambda: _Conn())
        self.assertFalse(source.enabled)
        self.assertEqual(source.queue_rows(START, END), [])

    def test_host_without_user_is_still_off(self):
        self.assertFalse(pbxdb.PbxDb(host='10.0.0.1', connect=lambda: _Conn()).enabled)


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.conn = _Conn(rows=[row(), row(event='CONNECT', at='2026-09-21 09:00:30')])
        self.source = pbxdb.PbxDb(host='10.0.0.1', user='ro', password='x',
                                  connect=lambda: self.conn)

    def test_rows_come_back_as_named_fields(self):
        rows = self.source.queue_rows(START, END)
        self.assertEqual(rows[0]['event'], 'ENTERQUEUE')
        self.assertEqual(rows[0]['callid'], '1.1')
        self.assertEqual(rows[0]['time'], datetime(2026, 9, 21, 9, 0, 26))

    def test_one_indexed_query_with_a_row_ceiling(self):
        self.source.queue_rows(START, END)
        self.assertEqual(len(self.conn.queries), 1)
        sql, params = self.conn.queries[0]
        self.assertIn('FROM queuelog WHERE time >= %s AND time < %s', sql)
        self.assertIn('LIMIT', sql)
        self.assertEqual(params[0], START)
        self.assertEqual(params[-1], pbxdb.MAX_ROWS)

    def test_rows_go_by_time_so_the_time_index_is_used_on_any_day(self):
        """`ORDER BY id LIMIT` толкал оптимизатор станции в первичный ключ с начала таблицы:
        сутки 03.09.2026 упирались в таймаут на каждой перечитке. По времени порядок отдаёт сам
        индекс времени — у любых суток."""
        self.source.queue_rows(START, END)
        sql, _params = self.conn.queries[0]
        self.assertIn('ORDER BY time, id', sql)
        self.assertNotIn('ORDER BY id', sql)

    def test_only_the_events_we_need_are_asked_for(self):
        self.source.queue_rows(START, END)
        _sql, params = self.conn.queries[0]
        self.assertEqual(params[2:-1], list(pbxdb.queue_facts.WANTED_EVENTS))
        self.assertNotIn('RINGNOANSWER', params, 'попыток дозвона тысячи в сутки')
        self.assertNotIn('PAUSE', params, 'паузы операторов — 99 % журнала')

    def test_a_window_wider_than_a_day_never_reaches_the_station(self):
        with self.assertRaises(pbxdb.PbxDbError):
            self.source.queue_rows(START, START + timedelta(days=3))
        self.assertEqual(self.conn.queries, [])

    def test_a_backwards_window_is_refused(self):
        with self.assertRaises(pbxdb.PbxDbError):
            self.source.queue_rows(END, START)
        self.assertEqual(self.conn.queries, [])

    def test_full_read_covers_the_whole_window(self):
        """По отметке мост решает «входа в очередь не было» (queue_facts.never_entered)."""
        self.assertIsNone(self.source.covered_until, 'до первого чтения — ничего не известно')
        self.source.queue_rows(START, END)
        self.assertEqual(self.source.covered_until, END)

    def test_read_cut_by_the_ceiling_covers_only_up_to_its_last_row(self):
        self.conn.rows = [row(callid='%d.1' % n, at='2026-09-21 09:%02d:00' % (n % 60))
                          for n in range(3)]
        with mock.patch.object(pbxdb, 'MAX_ROWS', 3):
            self.source.queue_rows(START, END)
        self.assertEqual(self.source.covered_until, datetime(2026, 9, 21, 9, 2, 0))

    def test_failed_read_covers_nothing(self):
        self.source.queue_rows(START, END)
        self.conn.broken = True
        with self.assertRaises(pbxdb.PbxDbError):
            self.source.queue_rows(START, END)
        self.assertIsNone(self.source.covered_until,
                          'после отказа прежняя отметка не должна выдавать себя за свежую')

    def test_facts_are_built_from_the_rows(self):
        facts = self.source.facts(START, END)
        self.assertEqual(facts['1.1']['queued_at'], datetime(2026, 9, 21, 9, 0, 26))
        self.assertEqual(facts['1.1']['wait_seconds'], 4)


class ReconnectTests(unittest.TestCase):
    def test_a_dropped_connection_is_reopened_once(self):
        """Станция рвёт простаивающие соединения сама, а хвост ходит раз в двадцать
        секунд: обрыв — это не отказ, а повод соединиться заново."""
        conns = [_Conn(broken=True), _Conn(rows=[row()])]
        source = pbxdb.PbxDb(host='10.0.0.1', user='ro', connect=lambda: conns.pop(0))
        rows = source.queue_rows(START, END)
        self.assertEqual(len(rows), 1)
        self.assertEqual(conns, [], 'второе соединение было открыто')

    def test_two_failures_in_a_row_are_an_error(self):
        source = pbxdb.PbxDb(host='10.0.0.1', user='ro', connect=lambda: _Conn(broken=True))
        with self.assertRaises(pbxdb.PbxDbError):
            source.queue_rows(START, END)

    def test_a_query_that_timed_out_is_not_sent_again(self):
        """Таймаут чтения — запрос станция ещё выполняет, у MariaDB 5.5 его не прервать.
        Повтор дал бы ей второй такой же: только ошибка, без нового соединения."""
        conns = [_Conn(broken=True), _Conn(rows=[row()])]
        source = pbxdb.PbxDb(host='10.0.0.1', user='ro', connect=lambda: conns.pop(0))
        clock = iter([100.0, 100.0 + pbxdb.READ_TIMEOUT])
        with mock.patch.object(pbxdb.time, 'monotonic', side_effect=lambda: next(clock)):
            with self.assertRaises(pbxdb.PbxDbError):
                source.queue_rows(START, END)
        self.assertEqual(len(conns), 1, 'второе соединение не открывалось')

    def test_the_connection_is_reused_between_calls(self):
        conn = _Conn(rows=[row()])
        source = pbxdb.PbxDb(host='10.0.0.1', user='ro', connect=lambda: conn)
        source.queue_rows(START, END)
        source.queue_rows(START, END)
        self.assertEqual(len(conn.queries), 2)
        self.assertEqual(conn.closed, 0)


class SnapshotTrapTests(unittest.TestCase):
    """Без автокоммита соединение навсегда застревает в первом снимке базы.

    У MariaDB изоляция REPEATABLE READ, а pymysql без autocommit открывает транзакцию
    первым же SELECT. Соединение у моста одно на весь день, поэтому 22.09.2026 с 15:59
    каждый запрос видел журнал очередей на момент первого, и новые звонки точных полей
    не получали — без единой ошибки. Проверено на живой станции: max(id) у такого
    соединения за две минуты не сдвинулся, у соединения с autocommit вырос.
    """

    def test_real_connection_is_opened_with_autocommit(self):
        from unittest import mock
        source = pbxdb.PbxDb(host='10.0.0.1', user='ro', password='x')
        with mock.patch.object(pbxdb, 'pymysql') as driver:
            source._connect_pymysql()
        self.assertTrue(driver.connect.call_args.kwargs.get('autocommit'),
                        'без autocommit мост читает один и тот же снимок весь день')


def cdr_row(at='2026-09-21 09:00:26', linkedid='1.1', billsec=30, recordingfile=None):
    """Строка `cdr`, как её отдаёт pymysql: время — datetime, NULL — None."""
    return (datetime.strptime(at, '%Y-%m-%d %H:%M:%S'), '"Client" <+77015550001>',
            '+77015550001', '3001', 'ext-queues', 'PJSIP/+77475555578-0006ce4f',
            'Local/6699@from-queue-0005e9cf;1', 40, billsec, 'ANSWERED', linkedid,
            '7475555578', recordingfile, linkedid)


class CdrTests(unittest.TestCase):
    """С 1.5.0 мост берёт CDR из базы станции, а не у надстройки: строки обязаны выглядеть
    так, как их ждёт склейка, а чтение — оставаться таким же бережным, как журнал очередей."""

    def setUp(self):
        self.conn = _Conn(rows=[cdr_row(), cdr_row(at='2026-09-21 09:00:31', billsec=0)])
        self.source = pbxdb.PbxDb(host='10.0.0.1', user='ro', password='x',
                                  connect=lambda: self.conn)

    def test_rows_have_the_fields_the_touch_builder_reads(self):
        rows = self.source.cdr_rows(START, END)
        self.assertEqual(rows[0]['calldate'], '2026-09-21T09:00:26')
        self.assertEqual((rows[0]['duration'], rows[0]['billsec']), (40, 30))
        self.assertEqual(rows[0]['dstchannel'], 'Local/6699@from-queue-0005e9cf;1')
        self.assertEqual(rows[0]['recordingfile'], '', 'NULL — пустая строка, а не None')
        self.assertIsNone(rows[0]['recording_url'],
                          'ссылку склейка соберёт из имени файла и даты сама')

    def test_one_indexed_query_with_a_row_ceiling(self):
        self.source.cdr_rows(START, END)
        self.assertEqual(len(self.conn.queries), 1)
        sql, params = self.conn.queries[0]
        self.assertIn('FROM cdr WHERE calldate >= %s AND calldate < %s', sql)
        self.assertIn('ORDER BY calldate, sequence', sql)
        self.assertEqual(params, [START, END, pbxdb.CDR_MAX_ROWS])

    def test_reading_starts_an_hour_early_to_catch_calls_from_before_midnight(self):
        """Звонок 23:59:45 кончается после полуночи: без запаса его хвост стал бы лишним
        касанием новых суток (23.09.2026 так задвоился бы звонок 22.09)."""
        list(self.source.iter_cdr('2026-09-21T00:00:00', '2026-09-22T01:00:00'))
        _sql, params = self.conn.queries[0]
        self.assertEqual(params[0], START - pbxdb.CDR_LEAD)
        self.assertEqual(params[1], END)

    def test_hitting_the_ceiling_is_an_error_not_a_quiet_day(self):
        with mock.patch.object(pbxdb, 'CDR_MAX_ROWS', 2):
            with self.assertRaises(pbxdb.PbxDbError):
                self.source.cdr_rows(START, END)

    def test_a_window_wider_than_a_day_never_reaches_the_station(self):
        with self.assertRaises(pbxdb.PbxDbError):
            self.source.cdr_rows(START, START + timedelta(days=2))
        self.assertEqual(self.conn.queries, [])

    def test_unreadable_window_bounds_are_refused(self):
        with self.assertRaises(pbxdb.PbxDbError):
            list(self.source.iter_cdr('вчера', '2026-09-22T01:00:00'))
        self.assertEqual(self.conn.queries, [])

    def test_a_disabled_source_refuses_instead_of_returning_an_empty_day(self):
        """Пустой список выглядел бы как сутки без звонков — и портал закрыл бы их."""
        with self.assertRaises(pbxdb.PbxDbError):
            pbxdb.PbxDb(connect=lambda: _Conn()).cdr_rows(START, END)

    def test_cdr_does_not_touch_the_journal_coverage_mark(self):
        self.source.cdr_rows(START, END)
        self.assertIsNone(self.source.covered_until,
                          'отметку полноты журнала ставит только чтение журнала')


def cel_row(linkedid='1.1', event_id=1, eventtype='HANGUP', source='PJSIP/6687-0006fb1b',
            at='2026-09-21 09:02:00'):
    """Строка `cel`, как её отдаёт pymysql: (linkedid, id, eventtype, eventtime, extra)."""
    extra = '{"hangupcause":16,"hangupsource":"%s","dialstatus":"ANSWER"}' % source
    return (linkedid, event_id, eventtype, datetime.strptime(at, '%Y-%m-%d %H:%M:%S'),
            extra if eventtype == 'HANGUP' else '')


class CelTests(unittest.TestCase):
    """С 1.6.0 мост спрашивает CEL, кто положил трубку на исходящем. У CEL нет индекса по
    времени (окно по eventtime — полный скан 15 млн строк, проверено 21.09.2026: таймаут),
    поэтому только по названным звонкам — по индексу linkedid — и пачками."""

    def setUp(self):
        self.conn = _Conn(rows=[cel_row(), cel_row(event_id=2, eventtype='LINKEDID_END')])
        self.source = pbxdb.PbxDb(host='10.0.0.1', user='ro', password='x',
                                  connect=lambda: self.conn)

    def test_only_named_calls_by_the_linkedid_index(self):
        self.source.hangup_rows(['1.1', '2.2'])
        self.assertEqual(len(self.conn.queries), 1)
        sql, params = self.conn.queries[0]
        self.assertIn('FROM cel WHERE linkedid IN (%s, %s) AND eventtype IN (%s, %s, %s, %s)', sql)
        self.assertNotIn('eventtime >=', sql, 'окно по времени у CEL — полный скан')
        self.assertIn('LIMIT %s', sql)
        self.assertEqual(params, ['1.1', '2.2', 'HANGUP', 'LINKEDID_END', 'BLINDTRANSFER',
                                  'ATTENDEDTRANSFER', pbxdb.HANGUP_MAX_ROWS],
                         'отбои, конец звонка и переводы — больше ничего (каналы, мосты, приложения)')

    def test_rows_come_back_as_named_fields(self):
        rows = self.source.hangup_rows(['1.1'])
        self.assertEqual(rows[0]['linkedid'], '1.1')
        self.assertEqual(rows[0]['eventtype'], 'HANGUP')
        self.assertIn('hangupsource', rows[0]['extra'])

    def test_calls_are_asked_in_chunks_without_duplicates(self):
        with mock.patch.object(pbxdb, 'HANGUP_CHUNK', 2):
            self.source.hangup_rows(['5.5', '1.1', '2.2', '1.1', ' 3.3 ', '', None, '4.4'])
        tail = len(pbxdb.hangups_mod.WANTED_EVENTS) + 1          # типы событий и потолок строк
        self.assertEqual([params[:-tail] for _sql, params in self.conn.queries],
                         [['1.1', '2.2'], ['3.3', '4.4'], ['5.5']])

    def test_no_calls_no_query(self):
        self.assertEqual(self.source.hangup_rows([]), [])
        self.assertEqual(self.conn.queries, [])

    def test_too_many_calls_never_reach_the_station(self):
        with mock.patch.object(pbxdb, 'HANGUP_MAX_CALLS', 2):
            with self.assertRaises(pbxdb.PbxDbError):
                self.source.hangup_rows(['1.1', '2.2', '3.3'])
        self.assertEqual(self.conn.queries, [])

    def test_hitting_the_row_ceiling_is_an_error_not_a_cut_tail(self):
        """Урезанный хвост звонка назвал бы не ту сторону — лучше отказ."""
        with mock.patch.object(pbxdb, 'HANGUP_MAX_ROWS', 2):
            with self.assertRaises(pbxdb.PbxDbError):
                self.source.hangup_rows(['1.1'])

    def test_disabled_source_is_silent(self):
        self.assertEqual(pbxdb.PbxDb(connect=lambda: _Conn()).hangup_rows(['1.1']), [])

    def test_sides_are_built_from_the_rows(self):
        self.assertEqual(self.source.hangup_sides(['1.1']), {'1.1': 'operator'})

    def test_hangups_do_not_touch_the_journal_coverage_mark(self):
        self.source.hangup_rows(['1.1'])
        self.assertIsNone(self.source.covered_until)


class DirectoryTests(unittest.TestCase):
    def test_extensions_and_names_come_from_the_stations_users(self):
        conn = _Conn(rows=[('6699', 'Ivanov_Ivan'), (6452, 'Petrova Anna'),
                           ('', 'без номера'), ('305', None)])
        source = pbxdb.PbxDb(host='10.0.0.1', user='ro', connect=lambda: conn)
        self.assertEqual(source.agents_map(), {'6699': 'Ivanov_Ivan',
                                               '6452': 'Petrova Anna', '305': ''})
        sql, _params = conn.queries[0]
        self.assertIn('FROM asterisk.users', sql)


class ConfigTests(unittest.TestCase):
    def test_empty_config_gives_a_disabled_source(self):
        self.assertFalse(pbxdb.from_config({}).enabled)

    def test_settings_are_read_from_the_bridge_config(self):
        source = pbxdb.from_config({'pbxdb_host': '10.0.0.1', 'pbxdb_port': '3306',
                                    'pbxdb_user': 'ro', 'pbxdb_password': 'x',
                                    'pbxdb_name': 'asteriskcdrdb'})
        self.assertEqual((source.host, source.port, source.user, source.database),
                         ('10.0.0.1', 3306, 'ro', 'asteriskcdrdb'))


if __name__ == '__main__':
    unittest.main()
