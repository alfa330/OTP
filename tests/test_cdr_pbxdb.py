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

    def test_the_connection_is_reused_between_calls(self):
        conn = _Conn(rows=[row()])
        source = pbxdb.PbxDb(host='10.0.0.1', user='ro', connect=lambda: conn)
        source.queue_rows(START, END)
        source.queue_rows(START, END)
        self.assertEqual(len(conn.queries), 2)
        self.assertEqual(conn.closed, 0)


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
