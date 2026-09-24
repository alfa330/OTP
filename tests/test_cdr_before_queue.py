# -*- coding: utf-8 -*-
"""Звонки, не дошедшие до очереди: видны в «Касаниях» строками, но ни в одном итоге.

Клиент позвонил и положил трубку на приветствии (у Jana оно 16 с) — оператору звонок не
поступал. Станция пишет такой звонок «Отвеченным» (ответил автоинформатор), `dst = 's'`,
без записи и без плеча агента; склейка касаний его раньше не брала вовсе, и отдел продаж
не находил в разделе звонков, которые видел в CDR станции (24.09.2026, два звонка по 3–4 с
на номер Jana).

Решение владельца 24.09.2026: в «Касаниях» показывать как «не дошедших до очереди», в
«Сделках» — нет. Закреплено:

  * обычные касания от этого не меняются ни на одно поле — строка приветствия к звонку,
    дошедшему до очереди, не подмешивается (на начале и числе плеч стоит «Табло ОП»);
  * у такого звонка нет разговора, хотя станция пишет ANSWERED и billsec в секунды;
  * «Касаний», «Клиентов», «Операторы», «По дням», разрезы и табло их не считают, число —
    отдельным полем `before_queue`;
  * в режим «Сделки» они не попадают.

Номера клиентов учебные (7XX555XXXX).
"""

import inspect
import unittest
from datetime import date, datetime

from openpyxl import Workbook

from cdr import lead_queries, queries, report, touches as T

CLIENT = '+77015550001'


def row(**kwargs):
    base = {
        'calldate': '2026-09-24T20:02:13', 'src': '', 'dst': '', 'clid': '',
        'did': '', 'duration': 0, 'billsec': 0, 'disposition': 'NO ANSWER',
        'dcontext': '', 'uniqueid': '1.1', 'linkedid': '1.1',
        'recordingfile': '', 'recording_url': None, 'channel': '', 'dstchannel': '',
    }
    base.update(kwargs)
    return base


def greeting(**over):
    """Строка ровно как на скриншоте CDR станции: 4 секунды на приветствии Jana."""
    base = dict(src=CLIENT, dst='s', did='+77001223322', dcontext='app-announcement-54',
                channel='PJSIP/+77001223322_jana-000abcd1', disposition='ANSWERED',
                billsec=4, duration=4)
    base.update(over)
    return row(**base)


def queue_call(**over):
    base = dict(src=CLIENT, dst='3034', did='+77001223322', dcontext='ext-queues',
                channel='PJSIP/+77001223322_jana-000abcd1',
                dstchannel='Local/6687@from-queue-00059980;1', disposition='ANSWERED',
                billsec=120, duration=140, calldate='2026-09-24T20:02:29')
    base.update(over)
    return row(**base)


class ParseTests(unittest.TestCase):
    def test_greeting_row_is_a_call_before_the_queue(self):
        self.assertEqual(T.parse_before_queue(greeting()), '7015550001')
        self.assertIsNone(T.parse_row(greeting()), 'обычным касанием она не становится')

    def test_menu_and_unknown_did_count_too(self):
        for context in ('ivr-5', 'from-pstn'):
            self.assertEqual(T.parse_before_queue(greeting(dcontext=context)), '7015550001')

    def test_rows_that_are_not_a_client_on_the_greeting_are_ignored(self):
        for bad in (greeting(dst='3034'),                                  # это уже очередь
                    greeting(recordingfile='in-7001223322-7015550001-1.wav'),  # есть запись
                    greeting(dstchannel='PJSIP/6687-000667a8'),           # есть агент
                    greeting(src='6650'),                                  # звонит сотрудник
                    greeting(src='anonymous')):                            # номера нет
            self.assertIsNone(T.parse_before_queue(bad), bad)


class BuildTests(unittest.TestCase):
    def test_greeting_alone_becomes_a_touch_without_an_operator(self):
        touches = T.build_touches([greeting()])
        self.assertEqual(len(touches), 1)
        touch = touches[0]
        self.assertEqual(touch['call_type'], T.TYPE_IN_BEFORE_QUEUE)
        self.assertEqual(touch['result'], T.RESULT_BEFORE_QUEUE)
        self.assertEqual((touch['ext'], touch['queue'], touch['recording_url']), ('', '', ''))
        self.assertEqual(touch['started_at'], '2026-09-24 20:02:13')
        self.assertEqual(touch['dial_seconds'], 4)
        self.assertEqual(touch['line_number'], '7001223322')

    def test_answered_by_the_greeting_is_not_a_conversation(self):
        """ANSWERED и billsec 4 — это ответ автоинформатора, а не человека."""
        touch = T.build_touches([greeting(billsec=18, duration=18)])[0]
        self.assertEqual(touch['talk_seconds'], 0)
        self.assertNotEqual(touch['result'], T.RESULT_TALK)

    def test_call_that_reached_the_queue_is_untouched_by_its_greeting_row(self):
        with_greeting = T.build_touches([greeting(), queue_call()])
        without = T.build_touches([queue_call()])
        self.assertEqual(with_greeting, without)
        self.assertEqual(with_greeting[0]['call_type'], T.TYPE_IN)

    def test_two_greeting_rows_of_one_call_are_one_touch(self):
        touches = T.build_touches([greeting(), greeting(calldate='2026-09-24T20:02:15',
                                                        uniqueid='1.2')])
        self.assertEqual(len(touches), 1)
        self.assertEqual(touches[0]['legs'], 2)
        self.assertEqual(touches[0]['started_at'], '2026-09-24 20:02:13')

    def test_phone_filter_applies_to_them_as_well(self):
        self.assertEqual(T.build_touches([greeting()], phones={'7995550009'}), [])
        self.assertEqual(len(T.build_touches([greeting()], phones={'7015550001'})), 1)

    def test_summary_keeps_them_apart(self):
        touches = T.build_touches([greeting(), greeting(linkedid='2.2', src='+77025550002'),
                                   queue_call(linkedid='3.3')])
        summary = T.summarize(touches)
        self.assertEqual((summary['total'], summary['before_queue']), (1, 2))
        self.assertEqual(summary['phones'], 1)
        self.assertEqual(summary['incoming'], 1)


class QueryTests(unittest.TestCase):
    """SQL здесь не исполнить — сверяется, что каждое место, где раздел считает, их
    исключает, а строки таблицы — нет. На боевой схеме те же запросы прогнаны на
    сутках 23.09.2026 с подставленными 69 такими звонками: итоги не сдвинулись."""

    def test_table_and_file_list_them(self):
        for name in ('count_touches', 'select_touches', 'iter_touches'):
            self.assertNotIn('COUNTED_SQL', inspect.getsource(getattr(queries, name)), name)

    def test_every_aggregate_leaves_them_out(self):
        for name in ('operator_stats', 'daily_stats', 'breakdown'):
            self.assertIn('COUNTED_SQL', inspect.getsource(getattr(queries, name)), name)
        self.assertEqual(queries.COUNTED_SQL.strip(), 'AND t.call_type <> %(before_queue_type)s')

    def test_summary_counts_them_separately(self):
        source = ' '.join(inspect.getsource(queries.summary).split())
        self.assertIn('SELECT COUNT(*) FILTER (WHERE t.call_type <> %(before_queue_type)s)', source)
        self.assertIn('COUNT(DISTINCT t.phone) FILTER (WHERE t.call_type <> %(before_queue_type)s)',
                      source)
        self.assertIn("'before_queue': int(row[9])", source)
        params = queries._params(date(2026, 9, 24), date(2026, 9, 24), {})
        self.assertEqual(params['before_queue_type'], T.TYPE_IN_BEFORE_QUEUE)

    def test_wallboard_does_not_see_them(self):
        source = ' '.join(inspect.getsource(queries.day_touches_compact).split())
        self.assertIn('WHERE call_day = %s AND call_type <> %s', source)
        self.assertIn('TYPE_IN_BEFORE_QUEUE', source)

    def test_deals_do_not_get_them(self):
        self.assertIn('t.call_type <> %(before_queue_type)s', lead_queries._TOUCH_SQL)

        class Cursor:
            def execute(self, sql, params):
                self.params = params

            def fetchall(self):
                return []
        cursor = Cursor()
        lead_queries.select_touches_for_phones(cursor, ['7015550001'], date(2026, 9, 24),
                                               date(2026, 9, 24))
        self.assertEqual(cursor.params['before_queue_type'], T.TYPE_IN_BEFORE_QUEUE)

    def test_greeting_length_shows_in_the_ivr_column(self):
        line = (datetime(2026, 9, 24, 20, 2, 13), None, '7015550001', '', T.TYPE_IN_BEFORE_QUEUE,
                T.RESULT_BEFORE_QUEUE, 0, 4, '', None, '1790348533.1', 1, None, None, None, '',
                '7001223322')
        touch = queries._row_to_touch(line)
        self.assertEqual(touch['ivr_seconds'], 4)
        self.assertIsNone(touch['wait_seconds'])


class ExportTests(unittest.TestCase):
    def test_context_and_summary_name_them_apart(self):
        book = Workbook(write_only=True)
        sheet = book.create_sheet('Контекст')
        report._fill_context(sheet, '2026-09-24', '2026-09-24',
                             {'total': 2000, 'talks': 900, 'before_queue': 69},
                             datetime(2026, 9, 24, 21, 0), 'Админ', '', {})
        summary = book.create_sheet('Сводка')
        report._fill_summary(summary, {'total': 2000, 'before_queue': 69}, [], [], [])
        # write-only лист не читается обратно — сохраняем книгу и читаем её целиком.
        from io import BytesIO
        from openpyxl import load_workbook
        stream = BytesIO()
        book.save(stream)
        loaded = load_workbook(BytesIO(stream.getvalue()))
        cells = {str(c.value) for ws in loaded.worksheets for r in ws.iter_rows() for c in r
                 if c.value is not None}
        self.assertIn('Не дошли до очереди (в счёт касаний не входят)', cells)
        self.assertIn('Не дошли до очереди (не входят в касания)', cells)
        self.assertIn('69', cells)


if __name__ == '__main__':
    unittest.main()
