# -*- coding: utf-8 -*-
"""Номер линии таксопарка в «Касаниях»: на какой номер звонил клиент и с какого звонили ему.

Раньше в разделе была только очередь («3034»), а вопрос у отдела продаж — «на какой номер,
какого парка». Номер у звонка есть всегда, но в трёх разных местах строки CDR (замер
суток 23.09.2026, 2000 касаний, покрытие 100 %):

  * входящий — `did`, набранный клиентом номер (304 из 304);
  * исходящий — транк в канале назначения `PJSIP/+77475777778-00066790` (1589 из 1696);
  * заявка автообзвона — ни транка, ни номера: `src` = внутренний номер, канал
    `Local/3034@ext-to-queue`; номер выводится из префикса набора `3322*…`, который в те
    же сутки встречается у звонков через транк (104 из 1696).

Парк по номеру выводится при чтении (cdr/lines.py): входящему — по своей очереди, исходящему —
по очереди, куда уходят входящие на тот же номер. Здесь закреплено и это правило, и то, что
SQL-фильтр «Таксопарк» отбирает ровно те строки, которые подписаны этим парком.

Номера клиентов учебные (7XX555XXXX); номера линий — служебные номера парков, не личные.
"""

import json
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from unittest import mock

from openpyxl import Workbook

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from cdr import lines, queries, report, routes as cdr_routes, touches as T
from cdr_bridge import live

CLIENT = '+77015550001'


def row(**kwargs):
    base = {
        'calldate': '2026-09-23T11:00:00', 'src': '', 'dst': '', 'clid': '',
        'did': '', 'duration': 0, 'billsec': 0, 'disposition': 'NO ANSWER',
        'dcontext': '', 'uniqueid': '1.1', 'linkedid': '1.1',
        'recordingfile': '', 'recording_url': None, 'channel': '', 'dstchannel': '',
    }
    base.update(kwargs)
    return base


def incoming(**over):
    """Звонок в очередь, как его отдаёт станция с 09.09.2026 — одной строкой."""
    base = dict(src=CLIENT, dst='3034', did='+77470939675', dcontext='ext-queues',
                channel='PJSIP/87470939675_jana_olx-000667a7',
                dstchannel='Local/6687@from-queue-00059980;1', disposition='ANSWERED',
                billsec=266, duration=281)
    base.update(over)
    return row(**base)


def outgoing(**over):
    base = dict(src='77475777778', dst='7778*+77015550001', dcontext='from-internal',
                channel='PJSIP/6729-0006678f', dstchannel='PJSIP/+77475777778-00066790',
                disposition='NO ANSWER',
                recordingfile='out-7778*+77015550001-6729-20260923-110052-1790143251.1153651.wav')
    base.update(over)
    return row(**base)


def autodial(**over):
    """Заявка автообзвона: ни транка, ни нашего номера в строке нет."""
    base = dict(src='6718', dst='7778*77025550002', dcontext='client-out',
                channel='Local/3016@ext-to-queue-00059018;1', dstchannel='',
                disposition='ANSWERED', linkedid='2.2', uniqueid='2.2')
    base.update(over)
    return row(**base)


def one(rows):
    built = T.build_touches(rows)
    return built[0] if len(built) == 1 else built


class TouchLineNumberTests(unittest.TestCase):
    def test_incoming_line_is_the_number_the_client_dialled(self):
        self.assertEqual(one([incoming()])['line_number'], '7470939675')

    def test_did_comes_in_any_national_form(self):
        """Станция пишет DID как придётся: 7475777778, +77470939675, 77002512629."""
        for did, expected in (('7475777778', '7475777778'), ('+77470939675', '7470939675'),
                              ('77002512629', '7002512629'), ('+7009214242', '7009214242')):
            self.assertEqual(one([incoming(did=did)])['line_number'], expected, did)

    def test_without_did_the_incoming_trunk_names_the_line(self):
        self.assertEqual(one([incoming(did='')])['line_number'], '7470939675')

    def test_outgoing_line_is_the_trunk_the_call_left_through(self):
        self.assertEqual(one([outgoing()])['line_number'], '7475777778')

    def test_trunk_with_a_hyphen_and_letters_still_gives_its_number(self):
        for channel, expected in (('PJSIP/77005554222-Stabilnyii-0006678e', '7005554222'),
                                  ('PJSIP/yVnnduj2_7009214242-0006678e', '7009214242'),
                                  ('PJSIP/77470957777_DEP_taxi_telemarketing-0006678e',
                                   '7470957777')):
            self.assertEqual(T.trunk_number(channel), expected, channel)

    def test_extension_and_nameless_trunk_are_not_lines(self):
        for channel in ('PJSIP/6687-0006678d', 'PJSIP/Beeline-0006678d', 'PJSIP/TECH_252-0006678d',
                        'Local/6723@from-queue-00059971;2', '', None):
            self.assertEqual(T.trunk_number(channel), '', channel)

    def test_outgoing_without_a_trunk_falls_back_to_the_shown_number(self):
        self.assertEqual(one([outgoing(dstchannel='')])['line_number'], '7475777778')

    def test_client_number_is_never_taken_for_our_line(self):
        """`src` исходящего — наш номер, только если это не номер самого клиента."""
        touch = one([outgoing(dstchannel='', src=CLIENT)])
        self.assertEqual(touch['line_number'], '')

    def test_autodial_request_learns_the_line_from_the_same_prefix(self):
        """Заявке `7778*…` номер подсказывает звонок через транк с тем же префиксом."""
        built = {t['linkedid']: t for t in T.build_touches([outgoing(), autodial()])}
        self.assertEqual(built['2.2']['line_number'], '7475777778')

    def test_autodial_request_alone_stays_without_a_line(self):
        self.assertEqual(one([autodial()])['line_number'], '')

    def test_prefix_is_learned_from_all_rows_even_when_phones_are_filtered(self):
        built = T.build_touches([outgoing(), autodial()], phones={'7025550002'})
        self.assertEqual([t['line_number'] for t in built], ['7475777778'])

    def test_prefix_with_two_numbers_takes_the_more_frequent_one(self):
        seen = {'7778': {'7475777778': 5, '7470943333': 1},
                '3333': {'7470943333': 2, '7009214242': 2}}
        self.assertEqual(T.learn_prefix_lines(seen),
                         {'7778': '7475777778', '3333': '7009214242'})

    def test_incoming_line_comes_from_the_arrival_leg(self):
        """Плечо агента DID не несёт — номер берётся с плеча прихода."""
        legs = [incoming(calldate='2026-09-23T11:09:52'),
                incoming(calldate='2026-09-23T11:10:07', did='', dst='6687',
                         dcontext='from-internal', channel='Local/6687@from-queue-00059980;2',
                         dstchannel='PJSIP/6687-000667a8',
                         recordingfile='external-6687-+77015550001-20260923-111007-1.2.wav')]
        self.assertEqual(one(legs)['line_number'], '7470939675')


class ParkTests(unittest.TestCase):
    KNOWN = {'7475777778': '3010', '7001223322': '3034'}

    def test_known_queue_gets_the_business_name(self):
        self.assertEqual(lines.park_of_queue('3034'), 'Jana такси')
        self.assertEqual(lines.park_of_queue('3010'), 'Центр регистрации')
        self.assertEqual(lines.park_of_queue('3037'), 'Центр регистрации')

    def test_unknown_queue_is_named_by_its_number_not_hidden(self):
        self.assertEqual(lines.park_of_queue('3016'), 'очередь 3016')
        self.assertEqual(lines.park_of_queue(''), '')

    def test_two_queues_mean_a_transfer_and_the_first_one_counts(self):
        self.assertEqual(lines.park_of_queue('3034,3041'), 'Jana такси')

    def test_park_back_to_its_queues(self):
        self.assertEqual(lines.queues_of_park('Центр регистрации'), ['3010', '3037'])
        self.assertEqual(lines.queues_of_park('очередь 3016'), ['3016'])
        self.assertEqual(lines.queues_of_park('нет такого'), [])

    def test_every_named_park_maps_back_to_its_own_queue(self):
        for queue, name in lines.PARK_BY_QUEUE.items():
            self.assertIn(queue, lines.queues_of_park(name))
            self.assertFalse(name.startswith(lines.QUEUE_PREFIX), queue)

    def test_number_goes_to_the_queue_most_of_its_calls_went_to(self):
        rows = [('7475777778', '3010', 87), ('7475777778', '3034', 2),
                ('7001223322', '3034,3041', 24), ('', '3034', 5), ('7009214242', '', 3)]
        self.assertEqual(lines.line_queues(rows), {'7475777778': '3010', '7001223322': '3034'})

    def test_tie_is_broken_the_same_way_every_time(self):
        rows = [('7470943333', '3041', 2), ('7470943333', '3001', 2)]
        self.assertEqual(lines.line_queues(rows), {'7470943333': '3001'})
        self.assertEqual(lines.line_queues(list(reversed(rows))), {'7470943333': '3001'})

    def test_incoming_is_named_by_where_it_actually_went(self):
        touch = {'line_number': '7475777778', 'queue': '3034', 'call_type': T.TYPE_IN}
        self.assertEqual(lines.park(touch, self.KNOWN), 'Jana такси')

    def test_outgoing_is_named_by_its_number_not_by_the_campaign_queue(self):
        """Заявка автообзвона идёт через очередь кампании 3016, а клиент видит номер
        «Центра регистрации» — парк звонка это он."""
        touch = {'line_number': '7475777778', 'queue': '3016', 'call_type': T.TYPE_OUT}
        self.assertEqual(lines.park(touch, self.KNOWN), 'Центр регистрации')

    def test_outgoing_with_an_unknown_number_falls_back_to_its_queue(self):
        touch = {'line_number': '7085550000', 'queue': '3016', 'call_type': T.TYPE_OUT}
        self.assertEqual(lines.park(touch, self.KNOWN), 'очередь 3016')

    def test_incoming_without_a_queue_uses_its_number(self):
        touch = {'line_number': '7001223322', 'queue': '', 'call_type': T.TYPE_IN_MISSED}
        self.assertEqual(lines.park(touch, self.KNOWN), 'Jana такси')

    def test_nothing_known_means_no_park(self):
        touch = {'line_number': '', 'queue': '', 'call_type': T.TYPE_OUT}
        self.assertEqual(lines.park(touch, self.KNOWN), '')

    def test_filter_lists_match_the_sql_condition(self):
        queues, numbers, known = lines.park_filter('Центр регистрации', self.KNOWN)
        self.assertEqual(queues, ['3010', '3037'])
        self.assertEqual(numbers, ['7475777778'])
        self.assertEqual(known, ['7001223322', '7475777778'])


def sql_filter_matches(touch, park_name, known):
    """Условие по парку из queries._FILTER_SQL, переписанное один в один на Python.

    Проверяется им, что фильтр и подпись строки — одно правило: SQL здесь не
    исполнить, поэтому сверка идёт по его тексту (test_sql_text_is_the_rule_below) и
    по этому двойнику на всех сочетаниях."""
    queues, numbers, all_known = lines.park_filter(park_name, known)
    number = touch['line_number']
    if number in all_known and (touch['call_type'] == 'Исходящий' or touch['queue'] == ''):
        return number in numbers
    return touch['queue'].split(',', 1)[0] in queues


class ParkFilterSqlTests(unittest.TestCase):
    KNOWN = {'7475777778': '3010', '7001223322': '3034', '7470943333': '3001'}

    def test_sql_text_is_the_rule_below(self):
        sql = ' '.join(queries._FILTER_SQL.split())
        self.assertIn("WHEN t.line_number = ANY(%(park_known)s) "
                      "AND (t.call_type = 'Исходящий' OR t.queue = '') "
                      "THEN t.line_number = ANY(%(park_lines)s) "
                      "ELSE split_part(t.queue, ',', 1) = ANY(%(park_queues)s) END", sql)

    def test_filter_selects_exactly_the_rows_labelled_with_the_park(self):
        numbers = ['7475777778', '7001223322', '7470943333', '7085550000', '']
        queue_values = ['3010', '3034', '3001', '3016', '3034,3041', '']
        types = [T.TYPE_OUT, T.TYPE_IN, T.TYPE_IN_MISSED]
        parks = {lines.park_of_queue(q) for q in queue_values if q} | {'Честный'}
        for number in numbers:
            for queue in queue_values:
                for call_type in types:
                    touch = {'line_number': number, 'queue': queue, 'call_type': call_type}
                    label = lines.park(touch, self.KNOWN)
                    for park in parks:
                        self.assertEqual(sql_filter_matches(touch, park, self.KNOWN),
                                         label == park, (touch, park, label))

    def test_params_are_empty_lists_without_the_filter(self):
        params = queries._params(date(2026, 9, 1), date(2026, 9, 2), {})
        self.assertFalse(params['park_on'])
        self.assertEqual((params['park_queues'], params['park_lines'], params['park_known']),
                         ([], [], []))

    def test_params_carry_the_prepared_lists(self):
        park = lines.park_filter('Jana такси', self.KNOWN)
        params = queries._params(date(2026, 9, 1), date(2026, 9, 2), {'park_filter': park})
        self.assertTrue(params['park_on'])
        self.assertEqual(params['park_queues'], ['3034'])
        self.assertEqual(params['park_lines'], ['7001223322'])

    def test_unknown_park_selects_nothing_rather_than_everything(self):
        park = lines.park_filter('нет такого', self.KNOWN)
        params = queries._params(date(2026, 9, 1), date(2026, 9, 2), {'park_filter': park})
        self.assertTrue(params['park_on'])
        self.assertEqual((params['park_queues'], params['park_lines']), ([], []))


class StorageTests(unittest.TestCase):
    TOUCH = {'linkedid': '1.1', 'phone': '7015550001', 'started_at': '2026-09-23 09:00:00',
             'answered_at': '', 'ext': '6650', 'call_type': 'Исходящий', 'result': 'Разговор',
             'talk_seconds': 30, 'dial_seconds': 40, 'queue': '', 'recording_url': None,
             'legs': 1, 'line_number': '7475777778'}

    def columns(self):
        head = queries._TOUCH_INSERT_SQL.split('(', 1)[1].split(')', 1)[0]
        return [name.strip() for name in head.split(',')]

    def test_line_lands_in_its_column(self):
        values = dict(zip(self.columns(), queries._touch_values(date(2026, 9, 23), [self.TOUCH])[0]))
        self.assertEqual(values['line_number'], '7475777778')

    def test_touch_of_an_old_bridge_stores_an_empty_line(self):
        touch = {k: v for k, v in self.TOUCH.items() if k != 'line_number'}
        values = dict(zip(self.columns(), queries._touch_values(date(2026, 9, 23), [touch])[0]))
        self.assertEqual(values['line_number'], '')

    def test_increment_without_a_line_keeps_the_known_one(self):
        """Старый мост шлёт приращение без номера — известный номер не стирается."""
        self.assertIn("line_number = CASE WHEN EXCLUDED.line_number <> ''",
                      ' '.join(queries._TOUCH_UPSERT_SQL.split()))

    def test_full_day_pass_writes_the_line(self):
        import inspect
        source = ' '.join(inspect.getsource(queries.replace_day_touches).split())
        self.assertIn('line_number = EXCLUDED.line_number', source)

    def test_line_is_read_back(self):
        row = (datetime(2026, 9, 23, 9, 0, 0), None, '7015550001', '6650', 'Исходящий',
               'Разговор', 30, 40, '', None, '1.1', 1, None, None, None, '', '7475777778')
        self.assertEqual(queries._row_to_touch(row)['line_number'], '7475777778')

    def test_schema_adds_the_column_on_an_old_table(self):
        from cdr import schema
        self.assertIn('line_number', schema.CDR_SCHEMA_SQL)
        self.assertTrue(any('ADD COLUMN IF NOT EXISTS line_number' in statement
                            for statement in schema.CDR_SCHEMA_MIGRATIONS))


class BridgeTests(unittest.TestCase):
    def test_live_tail_ships_and_compares_the_line(self):
        self.assertIn('line_number', live.TOUCH_FIELDS)
        self.assertIn('line_number', live._FINGERPRINT_FIELDS)

    def test_portal_takes_only_a_real_number(self):
        base = {'linkedid': '1.1', 'phone': '7015550001', 'started_at': '2026-09-23 09:00:00'}
        for sent, expected in (('+77475777778', '7475777778'), ('7475777778', '7475777778'),
                               ('6718', ''), ('<script>', ''), (None, ''), ('', '')):
            cleaned = cdr_routes._clean_touch(dict(base, line_number=sent), date(2026, 9, 23))
            self.assertEqual(cleaned['line_number'], expected, sent)

    def test_old_bridge_without_the_field_still_passes(self):
        cleaned = cdr_routes._clean_touch(
            {'linkedid': '1.1', 'phone': '7015550001', 'started_at': '2026-09-23 09:00:00'},
            date(2026, 9, 23))
        self.assertEqual(cleaned['line_number'], '')


class ExportTests(unittest.TestCase):
    def test_number_and_park_are_in_the_file(self):
        touch = {'started_at': '2026-09-23 09:00:00', 'phone': '7015550001',
                 'line_number': '7475777778', 'line_park': 'Центр регистрации', 'queue': '3016'}
        sheet = Workbook(write_only=True).create_sheet('касания')
        values = dict(zip([title for _k, title, _w in report.COLUMNS],
                          report._touch_row(sheet, touch)))
        self.assertEqual(len(report._touch_row(sheet, touch)), len(report.COLUMNS))
        self.assertEqual(values['Номер парка'], '7475777778')
        self.assertEqual(values['Таксопарк'], 'Центр регистрации')

    def test_number_stays_text(self):
        """Числом номер уехал бы в экспоненту и потерял бы вид номера."""
        self.assertIn('line_number', report.TEXT_COLUMNS)


# ── ручка периода ────────────────────────────────────────────────────────────

ADMIN = {'user_id': 1, 'name': 'Админ', 'role': 'super_admin', 'department_id': None,
         'department_code': None, 'headed_department_ids': [], 'headed_department_codes': []}


class _FakeDb:
    def _get_cursor(self):
        @contextmanager
        def scope():
            yield object()
        return scope()


class _Queries:
    """Двойник cdr.queries ровно под /period и /export: три касания и входящие за окно."""

    def __init__(self):
        self.filters_seen = []
        self.line_rows_since = []
        self.touches = [
            {'started_at': '2026-09-23 09:00:00', 'answered_at': '', 'phone': '7015550001',
             'ext': '6718', 'call_type': 'Исходящий', 'result': 'Не ответил',
             'talk_seconds': 0, 'dial_seconds': 0, 'queue': '3016', 'recording_url': '',
             'has_recording': False, 'linkedid': '1.1', 'legs': 1, 'queued_at': '',
             'ivr_seconds': None, 'wait_seconds': None, 'talk_measured_seconds': None,
             'hangup_side': '', 'line_number': '7475777778'},
            {'started_at': '2026-09-23 09:05:00', 'answered_at': '', 'phone': '7025550002',
             'ext': '6687', 'call_type': 'Входящий', 'result': 'Разговор',
             'talk_seconds': 40, 'dial_seconds': 0, 'queue': '3034', 'recording_url': '',
             'has_recording': False, 'linkedid': '1.2', 'legs': 1, 'queued_at': '',
             'ivr_seconds': None, 'wait_seconds': None, 'talk_measured_seconds': None,
             'hangup_side': '', 'line_number': '7470939675'},
            {'started_at': '2026-09-23 09:07:00', 'answered_at': '', 'phone': '7035550003',
             'ext': '6687', 'call_type': 'Исходящий', 'result': 'Не ответил',
             'talk_seconds': 0, 'dial_seconds': 0, 'queue': '', 'recording_url': '',
             'has_recording': False, 'linkedid': '1.3', 'legs': 1, 'queued_at': '',
             'ivr_seconds': None, 'wait_seconds': None, 'talk_measured_seconds': None,
             'hangup_side': '', 'line_number': ''},
        ]

    # доступ, мост, справочник
    def load_access_context(self, cursor, user_id):
        return dict(ADMIN)

    def day_states(self, cursor, day_from, day_to):
        return []

    def enqueue_days(self, cursor, days, requested_by=None):
        return len(days)

    def agent_state(self, cursor):
        return {'connected': True, 'last_seen_at': '2026-09-23T10:00:00'}

    def load_directory(self, cursor):
        return {'6718': {'periods': [{'since': None, 'name': 'Оператор', 'direction': 'ОП'}]}}

    def directory_updated_at(self, cursor):
        return datetime(2026, 9, 23, 9, 0, 0)

    def today_almaty(self):
        return date(2026, 9, 23)

    def now_almaty(self):
        return datetime(2026, 9, 23, 12, 0, 0)

    # данные
    def line_queue_rows(self, cursor, since_day):
        self.line_rows_since.append(since_day)
        return [('7475777778', '3010', 87), ('7470939675', '3034', 5)]

    def count_touches(self, cursor, day_from, day_to, filters=None):
        self.filters_seen.append(filters)
        return len(self.touches)

    def select_touches(self, cursor, day_from, day_to, filters=None, limit=100, offset=0):
        return [dict(t) for t in self.touches]

    def summary(self, cursor, day_from, day_to, filters=None):
        return {'total': len(self.touches)}

    def filter_values(self, cursor, day_from, day_to):
        return {'results': ['Разговор'], 'queues': ['3016', '3034']}

    def park_keys(self, cursor, day_from, day_to):
        return [{'line_number': t['line_number'], 'queue': t['queue'], 'call_type': t['call_type']}
                for t in self.touches]

    # выгрузка
    def iter_touches(self, cursor, day_from, day_to, filters=None, chunk=5000):
        for touch in self.touches:
            yield dict(touch)

    def operator_stats(self, cursor, day_from, day_to, filters=None):
        return []

    def daily_stats(self, cursor, day_from, day_to, filters=None):
        return []

    def breakdown(self, cursor, day_from, day_to, column, filters=None):
        return []


@unittest.skipIf(Flask is None, 'flask не установлен')
class PeriodRouteTests(unittest.TestCase):
    def setUp(self):
        self.queries = _Queries()
        patcher = mock.patch.object(cdr_routes, 'queries', self.queries)
        patcher.start()
        self.addCleanup(patcher.stop)
        schema_patch = mock.patch.object(cdr_routes.schema, 'schema_is_ready', return_value=True)
        schema_patch.start()
        self.addCleanup(schema_patch.stop)
        app = Flask(__name__)
        app.register_blueprint(cdr_routes.build_cdr_blueprint(
            db=_FakeDb(), require_api_key=lambda fn: fn,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (1, None, None)))
        app.config['TESTING'] = True
        self.client = app.test_client()

    def period(self, **params):
        params.setdefault('date_from', '2026-09-23')
        params.setdefault('date_to', '2026-09-23')
        params.setdefault('sync', '0')
        response = self.client.get('/api/cdr/period', query_string=params)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def test_each_row_gets_its_park(self):
        body = self.period()
        parks = {t['linkedid']: t['line_park'] for t in body['touches']}
        self.assertEqual(parks, {'1.1': 'Центр регистрации', '1.2': 'Jana такси', '1.3': ''})
        self.assertEqual(body['touches'][0]['line_number'], '7475777778')

    def test_filter_offers_only_parks_present_in_the_period(self):
        self.assertEqual(self.period()['filter_values']['parks'],
                         ['Jana такси', 'Центр регистрации'])

    def test_park_filter_reaches_sql_as_prepared_lists(self):
        self.period(park='Центр регистрации')
        prepared = self.queries.filters_seen[-1]['park_filter']
        self.assertEqual(prepared, (['3010', '3037'], ['7475777778'],
                                    ['7470939675', '7475777778']))

    def test_known_lines_are_cached_between_requests(self):
        self.period()
        self.period(park='Jana такси')
        self.assertEqual(len(self.queries.line_rows_since), 1)
        self.assertEqual(self.queries.line_rows_since[0], date(2026, 7, 25))

    def test_file_carries_the_park_of_every_row_and_names_the_filter(self):
        from io import BytesIO
        from openpyxl import load_workbook
        response = self.client.get('/api/cdr/export', query_string={
            'date_from': '2026-09-23', 'date_to': '2026-09-23', 'park': 'Jana такси'})
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(response.data))
        sheet = book['Касания']
        head = [cell.value for cell in sheet[1]]
        rows = [dict(zip(head, [cell.value for cell in line])) for line in sheet.iter_rows(min_row=2)]
        # Пустая строка в xlsx читается обратно как None — ячейка без значения.
        self.assertEqual([r['Таксопарк'] for r in rows], ['Центр регистрации', 'Jana такси', None])
        self.assertEqual(rows[0]['Номер парка'], '7475777778')
        context = ' '.join(str(cell.value) for line in book['Контекст'].iter_rows()
                           for cell in line if cell.value)
        self.assertIn('таксопарк = Jana такси', context)


if __name__ == '__main__':
    unittest.main()
