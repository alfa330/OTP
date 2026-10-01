# -*- coding: utf-8 -*-
"""Мост 1.5.0 берёт CDR и справочник номеров из базы самой станции, а не у надстройки.

30.09.2026 в 22:31 надстройка «FreePBX Stats» перестала отвечать (порты закрыты при живом
хосте), и до утра на портал не доехал ни один звонок, хотя станция исправно их писала.
Здесь закреплено: при настроенной базе мост к надстройке не ходит вовсе — ни за сутками,
ни за живым хвостом, ни за справочником; без базы (локальная отладка) — как раньше.
"""

import unittest

from cdr_bridge import agent as agent_mod, pbxdb

CONFIG = {'portal': 'http://portal.invalid', 'token': 'x', 'station': 'http://127.0.0.1:9',
          'login': '', 'password': '', 'live_interval': '20'}


class _Station:
    """Надстройка. Мосту с базой обращаться к ней незачем — любое обращение и есть дефект."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def iter_cdr(self, from_dt, to_dt, on_page=None):
        self.calls.append(('iter_cdr', from_dt, to_dt))
        return iter(self.rows)

    def agents_map(self):
        self.calls.append(('agents_map',))
        return {}


class _Db:
    enabled = True
    covered_until = None

    def __init__(self, cdr=(), agents=None, fail=None):
        self.cdr = list(cdr)
        self.agents = dict(agents or {})
        self.fail = fail
        self.cdr_calls = []

    def iter_cdr(self, from_dt, to_dt, on_page=None):
        self.cdr_calls.append((from_dt, to_dt))
        if self.fail:
            raise self.fail
        return iter(self.cdr)

    def facts(self, start, end):
        return {}

    def agents_map(self):
        return dict(self.agents)

    def describe(self):
        return 'mysql://pbx.invalid:3306/asteriskcdrdb'


def leg(linkedid, calldate, **over):
    """Плечо входящего через очередь — строка `cdr`, как её отдаёт `pbxdb.cdr_rows`."""
    base = {'calldate': calldate, 'clid': '', 'src': '+77015550101', 'dst': '3001',
            'dcontext': 'ext-queues', 'channel': 'PJSIP/+77475555578-0006ce4f',
            'dstchannel': 'Local/6699@from-queue-0005e9cf;1', 'duration': 40, 'billsec': 40,
            'disposition': 'ANSWERED', 'uniqueid': linkedid, 'did': '7475555578',
            'recordingfile': '', 'linkedid': linkedid, 'recording_url': None}
    base.update(over)
    return base


def agent_leg(linkedid, calldate, phone='+77015550101', billsec=30):
    return leg(linkedid, calldate, src=phone, dst='6699', dcontext='from-internal',
               channel='Local/6699@from-queue-0005e9cf;2', dstchannel='PJSIP/6699-0006ce50',
               duration=billsec + 2, billsec=billsec, did='', uniqueid=linkedid + '1',
               recordingfile='external-6699-%s-x-%s1.wav' % (phone, linkedid))


DAY_JOB = {'day': '2026-09-15', 'from_dt': '2026-09-15T00:00:00', 'to_dt': '2026-09-16T01:00:00'}


class SourceTests(unittest.TestCase):
    def _bridge(self, db, station=None):
        bridge = agent_mod.Bridge(dict(CONFIG), station=station or _Station(), pbxdb_source=db)
        self.sent = []
        bridge._post = lambda path, payload: self.sent.append((path, payload)) or {'complete': True}
        return bridge

    def test_with_a_database_the_day_comes_from_it(self):
        station = _Station()
        db = _Db(cdr=[leg('1.1', '2026-09-15T09:00:00'), agent_leg('1.1', '2026-09-15T09:00:05')])
        bridge = self._bridge(db, station)
        self.assertTrue(bridge.do_day(DAY_JOB))
        self.assertEqual(db.cdr_calls, [('2026-09-15T00:00:00', '2026-09-16T01:00:00')])
        self.assertEqual(station.calls, [], 'к надстройке мост с базой не ходит')
        path, payload = self.sent[0]
        self.assertEqual(path, 'day')
        self.assertEqual([(t['linkedid'], t['call_type'], t['talk_seconds'])
                          for t in payload['touches']], [('1.1', 'Входящий', 30)])

    def test_live_tail_reads_the_same_database(self):
        db = _Db()
        bridge = self._bridge(db)
        self.assertIs(bridge.live._station, db)

    def test_directory_comes_from_the_stations_own_users(self):
        station = _Station()
        bridge = self._bridge(_Db(agents={'6699': 'Ivanov_Ivan'}), station)
        bridge.send_directory()
        self.assertEqual(self.sent, [('directory', {'agents': {'6699': 'Ivanov_Ivan'}})])
        self.assertEqual(station.calls, [])

    def test_portal_is_told_where_cdr_comes_from(self):
        bridge = self._bridge(_Db())
        bridge.poll()
        self.assertEqual(self.sent[0][1]['station_url'], 'mysql://pbx.invalid:3306/asteriskcdrdb')

    def test_database_failure_closes_the_day_with_the_reason(self):
        """Молчать нельзя: портал оставил бы сутки «в работе» и выдал бы их снова."""
        bridge = self._bridge(_Db(fail=pbxdb.PbxDbError('база станции: нет соединения')))
        self.assertFalse(bridge.do_day(DAY_JOB))
        path, payload = self.sent[0]
        self.assertEqual(path, 'day')
        self.assertIn('база станции', payload['error'])

    def test_a_call_that_started_before_midnight_stays_with_its_own_day(self):
        """База отдаёт окно с запасом назад, и звонок 23:59:45 приходит целиком: его касание
        принадлежит прошлым суткам и в эти не попадает — иначе он задвоился бы."""
        db = _Db(cdr=[
            leg('9.9', '2026-09-14T23:59:45', disposition='BUSY', billsec=0, duration=20),
            agent_leg('9.9', '2026-09-15T00:00:10', billsec=60),
            leg('1.1', '2026-09-15T09:00:00', src='+77015550102'),
            agent_leg('1.1', '2026-09-15T09:00:05', phone='+77015550102'),
        ])
        bridge = self._bridge(db)
        self.assertTrue(bridge.do_day(DAY_JOB))
        self.assertEqual([t['linkedid'] for t in self.sent[0][1]['touches']], ['1.1'])


class NightlyPassTests(unittest.TestCase):
    """Звонок 23:59:45 с отбоем за полночью пишется в CDR уже в новых сутках, а принадлежит
    прошлым: живой хвост новых суток его не берёт. Его довозит ночной проход моста по
    вчерашним суткам — без него непринятый не успел бы к роботу пропущенных (три часа)."""

    def setUp(self):
        self.db = _Db(cdr=[
            leg('9.9', '2026-09-14T23:59:45', disposition='BUSY', billsec=0, duration=20),
            agent_leg('9.9', '2026-09-15T00:00:10', billsec=60),
        ])
        self.bridge = agent_mod.Bridge(dict(CONFIG), station=_Station(), pbxdb_source=self.db)
        self.sent = []
        self.bridge._post = (lambda path, payload:
                             self.sent.append((path, payload)) or {'complete': True})

    def test_not_before_the_hour_tail_is_in(self):
        from datetime import datetime
        self.assertFalse(self.bridge.maybe_finalize_yesterday(datetime(2026, 9, 15, 0, 40)))
        self.assertEqual(self.sent, [])

    def test_yesterday_is_read_whole_once_the_tail_is_in(self):
        from datetime import datetime
        self.assertTrue(self.bridge.maybe_finalize_yesterday(datetime(2026, 9, 15, 1, 10)))
        self.assertEqual(self.db.cdr_calls, [('2026-09-14T00:00:00', '2026-09-15T01:00:00')])
        path, payload = self.sent[0]
        self.assertEqual((path, payload['day']), ('day', '2026-09-14'))
        self.assertEqual([t['linkedid'] for t in payload['touches']], ['9.9'],
                         'звонок через полночь доехал в свои сутки')

    def test_once_per_day(self):
        from datetime import datetime
        self.assertTrue(self.bridge.maybe_finalize_yesterday(datetime(2026, 9, 15, 1, 10)))
        self.assertFalse(self.bridge.maybe_finalize_yesterday(datetime(2026, 9, 15, 13, 0)))
        self.assertTrue(self.bridge.maybe_finalize_yesterday(datetime(2026, 9, 16, 1, 10)))
        self.assertEqual([p['day'] for _path, p in self.sent], ['2026-09-14', '2026-09-15'])

    def test_a_failed_pass_is_not_retried_every_minute(self):
        from datetime import datetime
        self.db.fail = pbxdb.PbxDbError('база станции: нет соединения')
        self.assertTrue(self.bridge.maybe_finalize_yesterday(datetime(2026, 9, 15, 1, 10)))
        self.assertFalse(self.bridge.maybe_finalize_yesterday(datetime(2026, 9, 15, 1, 11)))
        self.assertEqual(len(self.db.cdr_calls), 1)

    def test_not_with_the_addon(self):
        from datetime import datetime
        bridge = agent_mod.Bridge(dict(CONFIG), station=_Station(), pbxdb_source=pbxdb.PbxDb())
        bridge._post = lambda path, payload: self.fail('проход с надстройкой не нужен')
        self.assertFalse(bridge.maybe_finalize_yesterday(datetime(2026, 9, 15, 1, 10)))


class WithoutDatabaseTests(unittest.TestCase):
    def test_without_a_database_the_addon_is_used_as_before(self):
        station = _Station([leg('1.1', '2026-09-15T09:00:00')])
        disabled = pbxdb.PbxDb()
        bridge = agent_mod.Bridge(dict(CONFIG), station=station, pbxdb_source=disabled)
        self.assertIs(bridge.cdr, station)
        self.assertIs(bridge.live._station, station)
        self.assertEqual(bridge.cdr_label, 'http://127.0.0.1:9')
        sent = []
        bridge._post = lambda path, payload: sent.append((path, payload)) or {'complete': True}
        self.assertTrue(bridge.do_day(DAY_JOB))
        self.assertEqual(station.calls[0][0], 'iter_cdr')


if __name__ == '__main__':
    unittest.main()
