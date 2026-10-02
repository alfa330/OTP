# -*- coding: utf-8 -*-
"""Кто положил трубку на исходящем — правило по CEL станции (cdr/hangups.py).

До 02.10.2026 у исходящих ОП сторона отбоя была пустой всегда: её называл только журнал
очередей, а исходящий в очередь не входит. События ниже — по образцу живой станции (сутки
29.09–01.10.2026; время и id сокращены, номера линий выдуманы), на ней и сверено правило:
входящие — против журнала очередей, 784 из 784.

Что закреплено:
  * сторону решает хвост звонка: отбои попыток очереди к другим операторам за минуты до
    конца (`PJSIP/6417-…`, причина 17) — не конец разговора;
  * хвост — ровно секунда до последнего отбоя: отбой, разошедшийся на две секунды, в нём,
    попытка очереди за две секунды до конца — уже нет;
  * в хвосте — первое по порядку записи событие со стороной разговора, а не последнее
    «положил сам»: транк, который станция кладёт следом за оператором, пишет источником
    себя;
  * без LINKEDID_END звонок не решается вовсе — и это «спросить снова», а не «стороны нет»;
  * переведённый звонок стороны не получает;
  * сторона ставится только исходящему с разговором и не перетирает известную;
  * портал дописывает её звонкам журнала оценок по ключу касания и только звонкам ОП.
"""

import ast
import unittest
from datetime import datetime
from pathlib import Path

from cdr import hangups, touches as touches_mod
from tests import source_cache

DATABASE_PATH = Path(__file__).resolve().parents[1] / 'database.py'


def hangup(event_id, at, channel, source, cause=16, linkedid='1.1'):
    """Событие HANGUP канала, как его отдаёт `pbxdb.hangup_rows`."""
    return {'linkedid': linkedid, 'id': event_id, 'eventtype': 'HANGUP',
            'eventtime': datetime.strptime('2026-09-29 ' + at, '%Y-%m-%d %H:%M:%S'),
            'extra': '{"hangupcause":%d,"hangupsource":"%s","dialstatus":""}' % (cause, source),
            'channel': channel}


def end(event_id, at, linkedid='1.1'):
    return {'linkedid': linkedid, 'id': event_id, 'eventtype': 'LINKEDID_END',
            'eventtime': datetime.strptime('2026-09-29 ' + at, '%Y-%m-%d %H:%M:%S'),
            'extra': ''}


OPERATOR = 'PJSIP/6687-0006fb1b'
TRUNK = 'PJSIP/+77475777778-0006fb29'


class PartyTests(unittest.TestCase):
    def test_employee_phone_is_the_operator(self):
        for channel in ('PJSIP/6687-0006fb1b', 'PJSIP/931-0006ee69', 'SIP/6650-00000abc'):
            self.assertEqual(hangups.party(channel), 'operator', channel)

    def test_trunk_is_the_client(self):
        for channel in ('PJSIP/+77475777778-0006fb29', 'PJSIP/trunk_7000000001-0006d112',
                        'PJSIP/77000000002_park-0006fb1c', 'PJSIP/7000000003-0006daec'):
            self.assertEqual(hangups.party(channel), 'client', channel)

    def test_station_plumbing_is_nobody(self):
        for channel in ('Local/6229@from-queue-0005ec91;2', 'Local/3016@ext-to-queue-0005f246;1',
                        'dialplan/builtin', '', None):
            self.assertEqual(hangups.party(channel), '', channel)


class SideTests(unittest.TestCase):
    def test_operator_hung_up_a_manual_call(self):
        """02.10.2026 10:54:22, звонок 6687: отбой с телефона оператора, транк — следом."""
        events = [hangup(674, '10:54:22', OPERATOR, OPERATOR),
                  hangup(676, '10:54:22', TRUNK, OPERATOR),
                  end(678, '10:54:22')]
        self.assertEqual(hangups.side(events), 'operator')

    def test_client_hung_up_a_manual_call(self):
        events = [hangup(141, '11:00:31', TRUNK, TRUNK),
                  hangup(143, '11:00:31', OPERATOR, TRUNK),
                  end(145, '11:00:31')]
        self.assertEqual(hangups.side(events), 'client')

    def test_queue_attempts_to_other_operators_are_not_the_end_of_the_talk(self):
        """Автообзвон 29.09: очередь звала 6417 и 6728 (оба «заняты», сами положили), взял
        6360, через две минуты положил клиент. Первое событие звонка назвало бы оператора."""
        events = [
            hangup(1, '08:42:30', 'Local/6229@from-queue-0005ec91;1', ''),
            hangup(2, '08:42:30', 'Local/6229@from-queue-0005ec91;2', 'dialplan/builtin', 17),
            hangup(3, '08:42:31', 'PJSIP/6417-0006d10f', 'PJSIP/6417-0006d10f', 17),
            hangup(4, '08:42:31', 'Local/6417@from-queue-0005ec93;2', 'dialplan/builtin', 17),
            hangup(5, '08:42:32', 'PJSIP/6728-0006d110', 'PJSIP/6728-0006d110', 17),
            hangup(6, '08:44:40', 'PJSIP/trunk_7000000001-0006d112',
                   'PJSIP/trunk_7000000001-0006d112'),
            hangup(7, '08:44:40', 'Local/3041@ext-to-queue-0005ec90;1',
                   'PJSIP/trunk_7000000001-0006d112'),
            hangup(8, '08:44:40', 'Local/6360@from-queue-0005ec95;1', ''),
            hangup(9, '08:44:40', 'PJSIP/6360-0006d111', ''),
            hangup(10, '08:44:40', 'Local/6360@from-queue-0005ec95;2', 'dialplan/builtin'),
            end(11, '08:44:40'),
        ]
        self.assertEqual(hangups.side(events), 'client')

    def test_trunk_that_names_itself_after_the_operator_left_is_not_the_client(self):
        """29.09 16:51:32: положил оператор 6669, станция уронила транк следом, и транк записал
        источником себя (причина 31). «Последний сам положил» назвал бы клиента; журнал
        очередей — COMPLETEAGENT."""
        events = [
            hangup(194, '16:50:59', 'Local/6726@from-queue-0005f1f3;1', '', 0),
            hangup(198, '16:50:59', 'Local/6726@from-queue-0005f1f3;2', 'dialplan/builtin', 17),
            hangup(391, '16:51:32', 'PJSIP/6669-0006daf4', 'PJSIP/6669-0006daf4'),
            hangup(393, '16:51:32', 'Local/6669@from-queue-0005f1f4;2', 'PJSIP/6669-0006daf4'),
            hangup(398, '16:51:32', 'Local/6669@from-queue-0005f1f4;1', ''),
            hangup(400, '16:51:32', 'PJSIP/7000000003-0006daec', 'PJSIP/7000000003-0006daec', 31),
            end(401, '16:51:32'),
        ]
        self.assertEqual(hangups.side(events), 'operator')

    def test_operator_channel_without_a_source_does_not_hide_the_client(self):
        """30.09 15:24:37: первым записан канал оператора, но без источника — решает следующий,
        у которого источник есть (транк). Журнал очередей: COMPLETECALLER."""
        events = [
            hangup(116, '15:24:37', 'PJSIP/6715-0006e652', '', 31),
            hangup(118, '15:24:37', 'Local/6715@from-queue-0005fe15;2', 'dialplan/builtin', 31),
            hangup(120, '15:24:37', 'Local/6715@from-queue-0005fe15;1', 'PJSIP/7000000004-0006e651', 31),
            hangup(122, '15:24:37', 'PJSIP/7000000004-0006e651', 'PJSIP/7000000004-0006e651', 31),
            end(123, '15:24:37'),
        ]
        self.assertEqual(hangups.side(events), 'client')

    def test_tail_reaches_into_the_previous_second(self):
        """Отбой разошёлся на две секунды: оператор положил в 16:51:31, транк станция уронила в
        16:51:32, и он записал источником себя. Хвост в секунду видит оператора; хвост только
        из последней секунды назвал бы клиента."""
        events = [
            hangup(391, '16:51:31', 'PJSIP/6669-0006daf4', 'PJSIP/6669-0006daf4'),
            hangup(393, '16:51:31', 'Local/6669@from-queue-0005f1f4;2', 'PJSIP/6669-0006daf4'),
            hangup(398, '16:51:32', 'Local/6669@from-queue-0005f1f4;1', ''),
            hangup(400, '16:51:32', 'PJSIP/7000000003-0006daec', 'PJSIP/7000000003-0006daec', 31),
            end(401, '16:51:32'),
        ]
        self.assertEqual(hangups.side(events), 'operator')

    def test_tail_is_one_second_and_no_more(self):
        """Попытка очереди к другому оператору за две секунды до отбоя — уже не хвост: шире
        хвост — и сторону назвала бы она, а не клиент."""
        events = [
            hangup(1, '16:35:16', 'PJSIP/6417-0006d10f', 'PJSIP/6417-0006d10f', 17),
            hangup(2, '16:35:18', TRUNK, TRUNK),
            end(3, '16:35:18'),
        ]
        self.assertEqual(hangups.side(events), 'client')

    def test_short_talk_with_a_queue_attempt_in_the_tail(self):
        """29.09 16:35: разговор в секунду. Отбой попытки очереди в 16:35:17 попал в хвост, но
        источник у него служебный, и решает отбой оператора 6229 в 16:35:18."""
        events = [
            hangup(866, '16:35:17', 'Local/6726@from-queue-0005f1d2;1', '', 0),
            hangup(870, '16:35:17', 'Local/6726@from-queue-0005f1d2;2', 'dialplan/builtin', 17),
            hangup(884, '16:35:18', 'PJSIP/6229-0006da55', 'PJSIP/6229-0006da55'),
            hangup(886, '16:35:18', 'Local/6229@from-queue-0005f1d3;2', 'PJSIP/6229-0006da55'),
            hangup(893, '16:35:18', 'PJSIP/77000000005-0006da52', 'dialplan/builtin'),
            end(894, '16:35:18'),
        ]
        self.assertEqual(hangups.side(events), 'operator')

    def test_transferred_call_gets_no_side(self):
        """Оператор 6687 перевёл клиента на 6688, тот договорил, и положил клиент. Касание и
        запись — 6687: «положил клиент» о его разговоре было бы неправдой."""
        events = [
            {'linkedid': '1.1', 'id': 600, 'eventtype': 'BLINDTRANSFER',
             'eventtime': datetime(2026, 9, 30, 11, 0, 0), 'extra': '{"extension":"6688"}'},
            hangup(601, '11:00:00', OPERATOR, OPERATOR),
            hangup(700, '11:04:10', TRUNK, TRUNK),
            hangup(701, '11:04:10', 'PJSIP/6688-0006fc01', TRUNK),
            end(702, '11:04:10'),
        ]
        self.assertEqual(hangups.side(events), '')
        events[0] = dict(events[0], eventtype='ATTENDEDTRANSFER')
        self.assertEqual(hangups.side(events), '')
        self.assertEqual(hangups.build_sides(events), {'1.1': ''},
                         'записан целиком — ответ «стороны нет», переспрашивать незачем')

    def test_an_early_attempt_does_not_answer_for_a_tail_without_a_side(self):
        """Хвост без стороны разговора — '' (не знаем), а не сторона давней попытки очереди."""
        events = [hangup(1, '10:00:00', 'PJSIP/6417-0006d10f', 'PJSIP/6417-0006d10f', 17),
                  hangup(2, '10:02:00', 'Local/3016@ext-to-queue-0005f246;1', 'dialplan/builtin'),
                  hangup(3, '10:02:00', 'PJSIP/+77475777778-0006dc31', ''),
                  end(4, '10:02:00')]
        self.assertEqual(hangups.side(events), '')

    def test_order_is_by_id_not_by_list_position(self):
        events = [hangup(676, '10:54:22', TRUNK, TRUNK),
                  end(678, '10:54:22'),
                  hangup(674, '10:54:22', OPERATOR, OPERATOR)]
        self.assertEqual(hangups.side(events), 'operator')

    def test_unfinished_call_is_not_decided(self):
        """Без LINKEDID_END хвоста может ещё не быть: отбой попытки очереди выдал бы себя за
        конец разговора."""
        events = [hangup(3, '08:42:31', 'PJSIP/6417-0006d10f', 'PJSIP/6417-0006d10f', 17)]
        self.assertEqual(hangups.side(events), '')

    def test_broken_extra_is_tolerated(self):
        events = [dict(hangup(1, '10:00:00', OPERATOR, OPERATOR), extra='не json'),
                  dict(hangup(2, '10:00:00', TRUNK, TRUNK), extra=None),
                  hangup(3, '10:00:00', TRUNK, TRUNK),
                  end(4, '10:00:00')]
        self.assertEqual(hangups.side(events), 'client')

    def test_text_times_and_ready_dicts_work_too(self):
        events = [{'id': 1, 'eventtype': 'HANGUP', 'eventtime': '2026-09-29T10:00:00',
                   'extra': {'hangupsource': OPERATOR}},
                  {'id': 2, 'eventtype': 'LINKEDID_END', 'eventtime': '2026-09-29 10:00:00'}]
        self.assertEqual(hangups.side(events), 'operator')


class BuildSidesTests(unittest.TestCase):
    def test_sides_by_call_and_unfinished_calls_left_out(self):
        rows = [hangup(1, '10:00:00', OPERATOR, OPERATOR, linkedid='1.1'), end(2, '10:00:00', '1.1'),
                hangup(3, '10:01:00', TRUNK, TRUNK, linkedid='2.2'), end(4, '10:01:00', '2.2'),
                hangup(5, '10:02:00', 'Local/6229@from-queue-0005ec91;2', 'dialplan/builtin',
                       linkedid='3.3'), end(6, '10:02:00', '3.3'),
                hangup(7, '10:03:00', TRUNK, TRUNK, linkedid='4.4')]
        self.assertEqual(hangups.build_sides(rows), {'1.1': 'operator', '2.2': 'client', '3.3': ''})

    def test_nothing_in_nothing_out(self):
        self.assertEqual(hangups.build_sides([]), {})
        self.assertEqual(hangups.build_sides(None), {})


def touch(linkedid='1.1', call_type=touches_mod.TYPE_OUT, talk=42, side=''):
    return {'linkedid': linkedid, 'phone': '7015550001', 'call_type': call_type,
            'talk_seconds': talk, 'hangup_side': side}


class AttachTests(unittest.TestCase):
    def test_only_outgoing_with_a_talk_and_without_a_side_is_asked(self):
        touches = [touch('1.1'), touch('2.2', talk=0), touch('3.3', call_type=touches_mod.TYPE_IN),
                   touch('4.4', side='client'), touch('5.5'), touch('', talk=10)]
        self.assertEqual(hangups.wanted_linkedids(touches), ['1.1', '5.5'])

    def test_side_lands_only_where_it_is_wanted(self):
        touches = [touch('1.1'), touch('2.2', talk=0), touch('3.3', call_type=touches_mod.TYPE_IN),
                   touch('4.4', side='client'), touch('5.5')]
        sides = {'1.1': 'operator', '2.2': 'client', '3.3': 'client', '4.4': 'operator', '5.5': ''}
        out = hangups.attach(touches, sides)
        self.assertEqual([t['hangup_side'] for t in out], ['operator', '', '', 'client', ''],
                         'непринятый, входящий и известная сторона не трогаются, пустая — тоже')
        self.assertEqual(touches[0]['hangup_side'], '', 'исходный словарь не меняется')

    def test_without_sides_nothing_changes(self):
        touches = [touch('1.1')]
        self.assertEqual(hangups.attach(touches, None), touches)


# ── портал: сторона доезжает до журнала оценок ─────────────────────────────────

class _Cursor:
    def __init__(self, returned_ids):
        self.returned_ids = returned_ids
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((' '.join(sql.split()), params))

    def fetchall(self):
        return [(value,) for value in self.returned_ids]


def _load_update_method():
    """Метод Database.update_cdr_call_end_parties в изоляции: импорт database.py поднимает
    пул к боевой базе, поэтому метод достаётся через ast (как в test_call_end_party).
    Пространство имён пустое намеренно: сошлись метод на имя модуля, которого здесь нет,
    тест упадёт NameError, а не спрячет его."""
    method = source_cache.function_copy(DATABASE_PATH, 'update_cdr_call_end_parties',
                                        class_name='Database')
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(DATABASE_PATH), 'exec'), namespace)
    return namespace['update_cdr_call_end_parties']


class _Db:
    def __init__(self, returned_ids):
        self.cursor = _Cursor(returned_ids)

    def _get_cursor(self):
        from contextlib import contextmanager

        @contextmanager
        def scope():
            yield self.cursor
        return scope()


class JournalUpdateTests(unittest.TestCase):
    def setUp(self):
        self.update = _load_update_method()

    def test_side_comes_from_the_calls_own_touch_and_only_for_sales_calls(self):
        db = _Db(returned_ids=[11, 12])
        self.assertEqual(self.update(db), 2)
        sql, params = db.cursor.statements[0]
        self.assertEqual(params, {'cdr': '%:cdr'}, 'только звонки ОП из касаний')
        self.assertIn('ic.notes LIKE %(cdr)s', sql)
        self.assertIn('t.linkedid = ic.external_id', sql, 'без LOWER — по ключу касания')
        self.assertIn('t.phone = right(ic.phone_normalized, 10)', sql,
                      'касание — ровно то, что легло в пул: linkedid и телефон')
        self.assertIn("t.hangup_side IN ('client', 'operator')", sql)
        self.assertEqual(sql.count("ic.call_end_party IS NULL OR ic.call_end_party = 'unknown'"), 2,
                         'известную сторону не переписываем — и при отборе, и при записи')
        calls_sql, params = db.cursor.statements[1]
        self.assertIn('UPDATE calls AS c', calls_sql)
        self.assertEqual(params, ([11, 12],), 'и уже сделанным оценкам этих звонков')

    def test_nothing_updated_means_no_second_update(self):
        db = _Db(returned_ids=[])
        self.assertEqual(self.update(db), 0)
        self.assertEqual(len(db.cursor.statements), 1)

    def test_the_portal_wires_it_into_the_section(self):
        source = source_cache.read(Path(__file__).resolve().parents[1] / 'bot_schedule2.py')
        self.assertIn('share_call_end_parties=db.update_cdr_call_end_parties', source)


if __name__ == '__main__':
    unittest.main()
