# -*- coding: utf-8 -*-
"""Склейка строк CDR в касания: ловушки, которые при правке теряются молча.

Здесь закрыты решения, каждое из которых стоило отдельного разбора на живых
данных и каждое из которых ломается БЕЗ ОШИБКИ — просто цифра в отчёте
становится другой:

  * длительность разговора берётся с плеча АГЕНТА, а не очереди: у плеча очереди
    billsec включает ожидание в очереди и завышает разговор на минуты;
  * `ANSWERED` при `billsec = 0` — это не разговор, а повторный набор
    автодозвонщика: соединение было, говорить не начали;
  * внутренний номер оператора живёт в ИМЕНИ ФАЙЛА записи, а не в src/dst — у
    автодозвона (78% трафика) в этих полях его нет вовсе;
  * время касания — начало вызова, а не момент ответа: у входящего через очередь
    между ними медиана 16 секунд, а бывает и 11 минут;
  * первое число в `in-<did>-<клиент>` — НАШ номер, а не клиента.

Сети и базы здесь нет: модуль `cdr.touches` чистый, на вход список словарей.

Номера клиентов — учебные (7XX555XXXX), настоящих в репозитории быть не должно.
Логика сверена с боевым эталоном отдельно: перенос прогонялся на сутках
24.08.2026 против уже собранного файла «Лиды 24.08 + касания ОП.xlsx» — все 1288
касаний эталона воспроизвелись, расхождений по полям ноль. Тот прогон
воспроизводим только при наличии кэша CDR, поэтому здесь он не живёт.
"""

import unittest

from cdr import report, touches as T


def row(**kwargs):
    """Строка CDR со всеми полями, которые отдаёт станция."""
    base = {
        'calldate': '2026-08-24T09:00:00', 'src': '', 'dst': '', 'clid': '',
        'did': '', 'duration': 0, 'billsec': 0, 'disposition': 'NO ANSWER',
        'dcontext': '', 'uniqueid': '1.1', 'linkedid': '1.1',
        'recordingfile': '', 'recording_url': None, 'channel': '', 'dstchannel': '',
    }
    base.update(kwargs)
    return base


class PhoneTests(unittest.TestCase):
    def test_any_national_form_is_one_client(self):
        for value in ('+77015550001', '87015550001', '77015550001', '7015550001',
                      '+7 (701) 555-00-01'):
            self.assertEqual(T.norm_phone(value), '7015550001', value)

    def test_too_short_is_not_a_client(self):
        for value in ('', None, '6650', '3001', '12345'):
            self.assertEqual(T.norm_phone(value), '')


class ParseRowTests(unittest.TestCase):
    """Кто клиент, кто оператор и куда шёл звонок."""

    def test_operator_comes_from_the_recording_name(self):
        """У автодозвона внутреннего номера нет ни в src, ни в dst."""
        parsed = T.parse_row(row(
            src='+77015550001', dst='4242*77015550001',
            recordingfile='out-4242*+77015550001-6650-20260824-090000-1.1.wav'))
        self.assertEqual(parsed, ('7015550001', 'out', '6650'))

    def test_trunk_prefix_is_not_mistaken_for_the_client(self):
        """`out-3322*<клиент>` — 3322 это транк, он идёт слитно через звёздочку."""
        parsed = T.parse_row(row(
            recordingfile='out-3322*+77015550002-6474-20260824-090000-1.1.wav'))
        self.assertEqual(parsed[0], '7015550002')

    def test_incoming_did_is_ours_not_the_clients(self):
        """В `in-<did>-<клиент>` ПЕРВОЕ число — наш номер. Наивный разбор
        записал бы в карточку наш собственный DID."""
        parsed = T.parse_row(row(
            recordingfile='in-77470957683-+77015550003-20260824-090000-1.1.wav'))
        self.assertEqual(parsed, ('7015550003', 'in', None))

    def test_queue_leg_names_the_queue_not_the_agent(self):
        parsed = T.parse_row(row(
            recordingfile='q-3001-+77015550004-20260824-090000-1.1.wav'))
        self.assertEqual(parsed, ('7015550004', 'in', None))

    def test_external_leg_names_the_agent(self):
        parsed = T.parse_row(row(
            recordingfile='external-6651-+77015550005-20260824-090000-1.1.wav'))
        self.assertEqual(parsed, ('7015550005', 'in', '6651'))

    def test_without_a_recording_the_dialled_numbers_are_read(self):
        out = T.parse_row(row(src='6650', dst='3322*77015550006'))
        self.assertEqual(out[:2], ('7015550006', 'out'))
        incoming = T.parse_row(row(src='+77015550007', dst='3001'))
        self.assertEqual(incoming[:2], ('7015550007', 'in'))

    def test_internal_call_is_not_a_touch(self):
        """Сотрудники звонят друг другу — это половина строк CDR, и клиентом там
        никто не является."""
        self.assertIsNone(T.parse_row(row(src='6650', dst='6651',
                                          dstchannel='PJSIP/6651-0001')))


class TalkTimeTests(unittest.TestCase):
    """Самая дорогая ловушка: billsec плеча очереди — не разговор."""

    def _incoming_call(self):
        """Входящий через очередь: две неудачные попытки, ответившее плечо
        очереди с раздутым billsec и настоящее плечо агента."""
        return [
            row(calldate='2026-08-24T10:00:00', src='+77015550010', dst='3001',
                disposition='BUSY', duration=5, billsec=0,
                dstchannel='Local/6651@from-queue-0001;1'),
            row(calldate='2026-08-24T10:00:05', src='+77015550010', dst='3001',
                disposition='BUSY', duration=5, billsec=0,
                dstchannel='Local/6652@from-queue-0002;1'),
            # Плечо очереди: ANSWERED, но billsec = ожидание 180 с + разговор.
            row(calldate='2026-08-24T10:00:00', src='+77015550010', dst='3001',
                disposition='ANSWERED', duration=222, billsec=222,
                dstchannel='Local/6653@from-queue-0003;1'),
            # Плечо самого агента: честные 42 секунды разговора.
            row(calldate='2026-08-24T10:03:00', src='+77015550010', dst='6653',
                disposition='ANSWERED', duration=42, billsec=42,
                dstchannel='PJSIP/6653-0004',
                recordingfile='external-6653-+77015550010-20260824-100300-1.1.wav'),
        ]

    def test_talk_time_comes_from_the_agent_leg(self):
        touch = T.build_touches(self._incoming_call())[0]
        self.assertEqual(touch['talk_seconds'], 42,
                         'взяли billsec плеча очереди — в нём ожидание в очереди')
        self.assertEqual(touch['ext'], '6653')
        self.assertEqual(touch['call_type'], T.TYPE_IN)
        self.assertEqual(touch['result'], T.RESULT_TALK)

    def test_touch_time_is_the_start_not_the_answer(self):
        touch = T.build_touches(self._incoming_call())[0]
        self.assertEqual(touch['started_at'], '2026-08-24 10:00:00')
        self.assertEqual(touch['answered_at'], '2026-08-24 10:03:00',
                         'момент ответа обязан быть виден отдельно от начала')

    def test_all_legs_collapse_into_one_touch(self):
        touches = T.build_touches(self._incoming_call())
        self.assertEqual(len(touches), 1, 'четыре строки CDR — это один звонок')
        self.assertEqual(touches[0]['legs'], 4)

    def test_queue_leg_alone_still_names_the_agent(self):
        """Если плеча агента в CDR не оказалось, оператора всё равно называем —
        но это единственный случай, когда billsec берётся с очереди."""
        legs = [leg for leg in self._incoming_call()
                if 'external-' not in leg['recordingfile']]
        touch = T.build_touches(legs)[0]
        self.assertEqual(touch['ext'], '6653')
        self.assertEqual(touch['talk_seconds'], 222)


class ResultTests(unittest.TestCase):
    def test_answered_with_zero_billsec_is_not_a_conversation(self):
        """Повторный набор автодозвонщика: соединение было, разговора нет."""
        touch = T.build_touches([row(
            calldate='2026-08-24T11:00:00', src='6650', dst='4242*77015550020',
            disposition='ANSWERED', duration=3, billsec=0)])[0]
        self.assertEqual(touch['result'], T.RESULT_DROPPED)
        self.assertEqual(touch['talk_seconds'], 0)

    def test_outgoing_stays_outgoing_even_unanswered(self):
        touch = T.build_touches([row(
            src='6650', dst='4242*77015550021', disposition='NO ANSWER', duration=20)])[0]
        self.assertEqual(touch['call_type'], T.TYPE_OUT)
        self.assertEqual(touch['result'], T.RESULT_NO_ANSWER)

    def test_unanswered_incoming_is_marked_separately(self):
        """«Входящий (не приняли)» — отдельный тип, а не результат: по нему
        считают пропущенные, и смешивать его с исходящими нельзя."""
        touch = T.build_touches([row(
            src='+77015550022', dst='3001', disposition='NO ANSWER', duration=30)])[0]
        self.assertEqual(touch['call_type'], T.TYPE_IN_MISSED)

    def test_busy_is_reported_as_busy(self):
        touch = T.build_touches([row(
            src='6650', dst='4242*77015550023', disposition='BUSY')])[0]
        self.assertEqual(touch['result'], T.RESULT_BUSY)


class GroupingTests(unittest.TestCase):
    def test_two_clients_on_one_linkedid_are_two_touches(self):
        """Перевод звонка: один linkedid, два разных клиента. Это честно два
        касания, поэтому ключ составной."""
        touches = T.build_touches([
            row(linkedid='7.7', src='6650', dst='4242*77015550030'),
            row(linkedid='7.7', src='6650', dst='4242*77015550031'),
        ])
        self.assertEqual(len(touches), 2)
        self.assertEqual({t['phone'] for t in touches},
                         {'7015550030', '7015550031'})

    def test_touches_come_back_in_time_order(self):
        touches = T.build_touches([
            row(linkedid='2.2', calldate='2026-08-24T15:00:00',
                src='6650', dst='4242*77015550041'),
            row(linkedid='1.1', calldate='2026-08-24T09:00:00',
                src='6650', dst='4242*77015550040'),
        ])
        self.assertEqual([t['phone'] for t in touches],
                         ['7015550040', '7015550041'])

    def test_only_requested_phones_are_kept_when_asked(self):
        """Режим, которым офлайн-сборка оставляла касания по лидам amoCRM."""
        rows = [row(linkedid='1.1', src='6650', dst='4242*77015550050'),
                row(linkedid='2.2', src='6650', dst='4242*77015550051')]
        touches = T.build_touches(rows, phones={'7015550050'})
        self.assertEqual([t['phone'] for t in touches], ['7015550050'])


class MidnightTests(unittest.TestCase):
    """Звонок через полночь. Сутки читаются с часовым хвостом, поэтому плечи
    собираются целиком, а лишние сутки отсекает уже портал по началу вызова."""

    def test_legs_across_midnight_stay_one_touch(self):
        touches = T.build_touches([
            row(linkedid='9.9', calldate='2026-08-24T23:59:50',
                src='+77015550060', dst='3001', disposition='BUSY',
                dstchannel='Local/6653@from-queue-0001;1'),
            row(linkedid='9.9', calldate='2026-08-25T00:00:30',
                src='+77015550060', dst='6653', disposition='ANSWERED',
                duration=200, billsec=200, dstchannel='PJSIP/6653-0002',
                recordingfile='external-6653-+77015550060-20260824-235950-9.9.wav'),
        ])
        self.assertEqual(len(touches), 1)
        self.assertEqual(touches[0]['started_at'], '2026-08-24 23:59:50',
                         'касание принадлежит суткам, в которые звонок НАЧАЛСЯ')
        self.assertEqual(touches[0]['talk_seconds'], 200)


class RecordingTests(unittest.TestCase):
    def test_ready_url_is_preferred(self):
        touch = T.build_touches([row(
            src='6650', dst='4242*77015550070', recording_url='http://rec/a.wav',
            recordingfile='out-4242*+77015550070-6650-20260824-090000-1.1.wav')])[0]
        self.assertEqual(touch['recording_url'], 'http://rec/a.wav')
        self.assertTrue(touch['has_recording'])

    def test_url_is_assembled_from_the_date_when_absent(self):
        touch = T.build_touches([row(
            calldate='2026-08-24T09:00:00', src='6650', dst='4242*77015550071',
            recordingfile='out-4242*+77015550071-6650-20260824-090000-1.1.wav')])[0]
        self.assertEqual(
            touch['recording_url'],
            T.RECORDINGS_BASE + '/2026/08/24/'
            'out-4242*+77015550071-6650-20260824-090000-1.1.wav')

    def test_no_recording_is_stated_honestly(self):
        touch = T.build_touches([row(src='6650', dst='4242*77015550072')])[0]
        self.assertEqual(touch['recording_url'], '')
        self.assertFalse(touch['has_recording'])


class SummaryTests(unittest.TestCase):
    def test_summary_counts_each_type_separately(self):
        touches = T.build_touches([
            row(linkedid='1.1', src='6650', dst='4242*77015550080',
                disposition='ANSWERED', billsec=10, duration=10),
            row(linkedid='2.2', src='+77015550081', dst='3001',
                disposition='NO ANSWER'),
            row(linkedid='3.3', src='+77015550082', dst='6653',
                disposition='ANSWERED', billsec=30, duration=30,
                dstchannel='PJSIP/6653-0001'),
        ])
        summary = T.summarize(touches)
        self.assertEqual(summary['total'], 3)
        self.assertEqual(summary['talks'], 2)
        self.assertEqual(summary['outgoing'], 1)
        self.assertEqual(summary['incoming'], 1)
        self.assertEqual(summary['incoming_missed'], 1)
        self.assertEqual(summary['talk_seconds'], 40)
        self.assertEqual(summary['phones'], 3)


class DurationLabelTests(unittest.TestCase):
    """Двойник `hms` из tests/cdr_touch_meta.test.mjs.

    Экран и выгрузка показывают одни и те же звонки. Если один напишет «0:42», а
    второй «42 с», человек решит, что перед ним разные цифры, — поэтому набор
    случаев здесь и в mjs-тесте связан. Правя один, правьте второй.
    """

    def test_zero_is_a_dash_not_zero_zero(self):
        for value in (0, None, -5):
            self.assertEqual(report.hms(value), '—', repr(value))

    def test_minutes_and_seconds(self):
        self.assertEqual(report.hms(42), '0:42')
        self.assertEqual(report.hms(60), '1:00')
        self.assertEqual(report.hms(432), '7:12')
        self.assertEqual(report.hms(3599), '59:59')

    def test_hours_appear_from_an_hour(self):
        self.assertEqual(report.hms(3600), '1:00:00')
        self.assertEqual(report.hms(3870), '1:04:30')
        self.assertEqual(report.hms(419947), '116:39:07')


if __name__ == '__main__':
    unittest.main()
