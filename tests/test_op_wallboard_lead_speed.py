# -*- coding: utf-8 -*-
"""«Табло ОП»: скорость принятия лида в работу (op_wallboard/lead_speed.py).

Что закреплено (определение владельца 02.10.2026 — «время поступления лида в amoCRM и момент
попытки дозвона по основе»):
  * попытка — исходящий, где станция набрала клиента: у ручного — начало звонка, у заявки
    автообзвона — начало плеча к клиенту по файлу записи; заявка без такого файла — не попытка;
  * к сделке звонок относится по правилу режима «Сделки» («Касания»): допуск две минуты до
    создания, окно до следующей сделки того же номера;
  * «по основе» — набирал номер группы «Основа»; звонок ЯР по тому же номеру не в счёт;
  * в среднем только сделки с попыткой; сделку, взятую в работу входящим разговором раньше
    попытки, не считаем; среднее усекается до секунды.
"""

import unittest
from datetime import datetime

from op_wallboard import lead_speed as L

NOW = datetime(2026, 10, 2, 18, 0, 0)
OSNOVA = {'6650', '6651'}


def deal(key='1', created='2026-10-02 10:00:00', phones='7015550001'):
    return {'lead_key': key, 'created_at': datetime.strptime(created, '%Y-%m-%d %H:%M:%S'),
            'phones': phones, 'phone': phones.split(',')[0]}


def call(started, phone='7015550001', ext='6650', call_type='Исходящий', talk=0, dial=30,
         queue='', recording=None):
    if recording is None and call_type == 'Исходящий' and not queue:
        recording = 'http://192.168.88.251/recordings/2026/10/02/out-4242*7%s-%s-%s-1790.1.wav' % (
            phone, ext, started.replace('-', '').replace(':', '').replace(' ', '-'))
    return {'started_at': datetime.strptime(started, '%Y-%m-%d %H:%M:%S'), 'phone': phone, 'ext': ext,
            'call_type': call_type, 'talk_seconds': talk, 'dial_seconds': dial, 'queue': queue,
            'recording_url': recording or ''}


def autodial(started, dialed=None, phone='7015550001', ext='6650', dial=60):
    """Заявка автообзвона: исходящий с очередью; набор клиента — время файла out-… (если был)."""
    recording = ''
    if dialed:
        recording = '10/02/out-7778*7%s-%s-%s-1790.2.wav' % (
            phone, ext, dialed.replace('-', '').replace(':', '').replace(' ', '-'))
    return call(started, phone=phone, ext=ext, dial=dial, queue='3035', recording=recording)


class DialMomentTests(unittest.TestCase):
    def test_manual_call_is_dialed_at_its_start(self):
        self.assertEqual(L.dial_moment(call('2026-10-02 10:03:00')), datetime(2026, 10, 2, 10, 3, 0))

    def test_manual_call_without_recording_still_counts_from_its_start(self):
        touch = call('2026-10-02 10:03:00', recording='')
        self.assertEqual(L.dial_moment(touch), datetime(2026, 10, 2, 10, 3, 0))

    def test_autodial_counts_from_the_client_leg_not_from_the_request(self):
        """Заявка ждала в очереди 40 с: клиента набрали в 10:00:45, а не в 10:00:05."""
        touch = autodial('2026-10-02 10:00:05', dialed='2026-10-02 10:00:45')
        self.assertEqual(L.dial_moment(touch), datetime(2026, 10, 2, 10, 0, 45))

    def test_autodial_without_client_leg_is_not_an_attempt(self):
        self.assertIsNone(L.dial_moment(autodial('2026-10-02 10:00:05')))

    def test_autodial_with_talk_but_no_recording_counts_from_the_request(self):
        """Разговор исходящего склейка берёт только с плеча к клиенту: клиента набрали, файла нет."""
        touch = dict(autodial('2026-10-02 10:00:05'), talk_seconds=40)
        self.assertEqual(L.dial_moment(touch), datetime(2026, 10, 2, 10, 0, 5))

    def test_recording_of_another_client_or_outside_the_call_is_ignored(self):
        foreign = autodial('2026-10-02 10:00:05', dialed='2026-10-02 10:00:45')
        foreign['phone'] = '7015559999'
        self.assertIsNone(L.dial_moment(foreign))
        late = autodial('2026-10-02 10:00:05', dialed='2026-10-02 10:05:00', dial=60)
        self.assertIsNone(L.dial_moment(late))
        manual_foreign = call('2026-10-02 10:03:00')
        manual_foreign['phone'] = '7015559999'
        self.assertEqual(L.dial_moment(manual_foreign), datetime(2026, 10, 2, 10, 3, 0))

    def test_incoming_call_is_not_a_dial(self):
        self.assertIsNone(L.dial_moment(call('2026-10-02 10:03:00', call_type='Входящий', talk=40)))


class TakeSpeedTests(unittest.TestCase):
    def speed(self, deals, touches, exts=OSNOVA, now=NOW):
        """Итог суток; разбивка по часам откладывается в self.hourly — её проверяют свои тесты."""
        result = L.take_speed(deals, touches, exts, now)
        self.hourly = {item['hour']: item for item in result.pop('hourly')}
        self.assertEqual(sorted(self.hourly), list(range(24)))
        return result

    def test_hour_is_the_hour_of_the_first_dial_not_of_the_deal(self):
        """Владелец 02.10.2026: «в показателях за час тоже». Сделка 09:58, набор 10:02 — взяли в
        работу в десятом часу; сделка, которая к отбивке ещё ждёт, иначе не попала бы ни в один час."""
        self.speed([deal('1', '2026-10-02 09:58:00', '7015550001'),
                    deal('2', '2026-10-02 09:10:00', '7015550002'),
                    deal('3', '2026-10-02 10:20:00', '7015550003')],
                   [call('2026-10-02 10:02:00', phone='7015550001'),
                    call('2026-10-02 09:11:00', phone='7015550002'),
                    call('2026-10-02 10:26:01', phone='7015550003')])
        self.assertEqual((self.hourly[9]['taken'], self.hourly[9]['avg_seconds']), (1, 60))
        self.assertEqual((self.hourly[10]['taken'], self.hourly[10]['avg_seconds']), (2, (240 + 361) // 2))
        self.assertEqual((self.hourly[11]['taken'], self.hourly[11]['avg_seconds']), (0, None))

    def test_hours_add_up_to_the_day(self):
        result = self.speed([deal('1', '2026-10-02 09:58:00', '7015550001'),
                             deal('2', '2026-10-02 11:00:00', '7015550002')],
                            [call('2026-10-02 10:02:00', phone='7015550001'),
                             call('2026-10-02 13:00:07', phone='7015550002')])
        seconds = sum(item['avg_seconds'] * item['taken'] for item in self.hourly.values() if item['taken'])
        self.assertEqual(sum(item['taken'] for item in self.hourly.values()), result['taken'])
        self.assertEqual(seconds // result['taken'], result['avg_seconds'])

    def test_dial_from_the_previous_day_tail_goes_to_the_first_hour(self):
        """Сделка 00:00:40, набор 23:59:50 — взяли сразу; час — первый час этих суток, а не вчерашний."""
        self.speed([deal(created='2026-10-02 00:00:40')], [call('2026-10-01 23:59:50')])
        self.assertEqual((self.hourly[0]['taken'], self.hourly[0]['avg_seconds']), (1, 0))

    def test_average_of_first_dials_is_truncated(self):
        result = self.speed(
            [deal('1', '2026-10-02 10:00:00', '7015550001'), deal('2', '2026-10-02 11:00:00', '7015550002')],
            [call('2026-10-02 10:01:40', phone='7015550001'),
             call('2026-10-02 10:30:00', phone='7015550001'),
             call('2026-10-02 11:03:23', phone='7015550002')])
        # 151,5 с: усечение даёт 151, округление — 152 (а при x,5 к чётному оно бывало и неотличимо).
        self.assertEqual(result, {'deals': 2, 'taken': 2, 'avg_seconds': 151})

    def test_first_attempt_is_the_earliest_dial_not_the_earliest_row(self):
        """Заявка автообзвона пришла раньше ручного звонка, но клиента по ней набрали позже."""
        result = self.speed([deal()], [autodial('2026-10-02 10:00:05', dialed='2026-10-02 10:05:00', dial=400),
                                       call('2026-10-02 10:02:00')])
        self.assertEqual(result['avg_seconds'], 120)

    def test_request_without_dial_does_not_take_the_lead(self):
        result = self.speed([deal()], [autodial('2026-10-02 10:00:05'), call('2026-10-02 10:07:00')])
        self.assertEqual(result['avg_seconds'], 420)

    def test_only_osnova_dials_count(self):
        """Оператор ЯР набрал тот же номер раньше — это работа его базы, а не взятие сделки."""
        result = self.speed([deal()], [call('2026-10-02 10:01:00', ext='6700'),
                                       call('2026-10-02 10:10:00', ext='6651')])
        self.assertEqual(result['avg_seconds'], 600)
        self.assertEqual(self.speed([deal()], [call('2026-10-02 10:01:00', ext='6700')])['taken'], 0)

    def test_deal_without_attempt_counts_as_a_deal_but_not_in_the_average(self):
        result = self.speed([deal('1'), deal('2', phones='7015550002')], [call('2026-10-02 10:05:00')])
        self.assertEqual(result, {'deals': 2, 'taken': 1, 'avg_seconds': 300})
        self.assertEqual(self.speed([deal()], []), {'deals': 1, 'taken': 0, 'avg_seconds': None})

    def test_talk_on_an_incoming_call_before_the_first_dial_leaves_the_deal_out(self):
        """Клиент дозвонился сам и поговорил — сделку взяли входящим; перезвон позже не «скорость»."""
        talked = self.speed([deal()], [call('2026-10-02 09:59:30', call_type='Входящий', talk=45),
                                       call('2026-10-02 14:00:00')])
        self.assertEqual((talked['taken'], talked['avg_seconds']), (0, None))
        missed = self.speed([deal()], [call('2026-10-02 09:59:30', call_type='Входящий', talk=0),
                                       call('2026-10-02 10:04:00')])
        self.assertEqual(missed['avg_seconds'], 240)

    def test_talk_on_an_incoming_call_after_the_first_dial_keeps_the_deal(self):
        """Клиент перезвонил сам уже ПОСЛЕ набора — сделку взяли набором, она в среднем."""
        result = self.speed([deal()], [call('2026-10-02 10:05:00'),
                                       call('2026-10-02 10:20:00', call_type='Входящий', talk=90)])
        self.assertEqual((result['taken'], result['avg_seconds']), (1, 300))

    def test_window_is_the_one_of_the_deals_section(self):
        """Звонок за минуту до создания (сделку завели во время звонка) — взяли сразу; за пять
        минут — до сделки, не её попытка."""
        self.assertEqual(self.speed([deal()], [call('2026-10-02 09:59:00')])['avg_seconds'], 0)
        self.assertEqual(self.speed([deal()], [call('2026-10-02 09:55:00')])['taken'], 0)

    def test_repeated_deal_of_the_same_number_takes_the_calls_after_it(self):
        result = self.speed([deal('1', '2026-10-02 10:00:00'), deal('2', '2026-10-02 10:30:00')],
                            [call('2026-10-02 10:10:00'), call('2026-10-02 10:31:00')])
        self.assertEqual(result, {'deals': 2, 'taken': 2, 'avg_seconds': (600 + 60) // 2})

    def test_second_phone_of_the_contact_is_searched_too(self):
        result = self.speed([deal(phones='7015550001,7025550002')], [call('2026-10-02 10:02:00', phone='7025550002')])
        self.assertEqual(result['avg_seconds'], 120)

    def test_dial_after_now_and_deal_without_creation_time_are_ignored(self):
        broken = dict(deal('2'), created_at=None)
        result = self.speed([deal(), broken], [call('2026-10-02 10:02:00')], now=datetime(2026, 10, 2, 10, 1, 0))
        self.assertEqual(result, {'deals': 1, 'taken': 0, 'avg_seconds': None})

    def test_request_inside_the_window_dialed_after_now_is_not_taken_yet(self):
        """Заявка пришла до «сейчас», а клиента по ней набрали позже — это ещё не попытка."""
        touch = autodial('2026-10-02 10:00:30', dialed='2026-10-02 10:01:30')
        result = self.speed([deal()], [touch], now=datetime(2026, 10, 2, 10, 1, 0))
        self.assertEqual(result['taken'], 0)


class _RecordingCursor:
    def __init__(self, rows=None, one=None):
        self.calls, self._rows, self._one = [], rows or [], one

    def execute(self, sql, params=None):
        self.calls.append((' '.join(sql.split()), params))

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._one


class LoaderTests(unittest.TestCase):
    """Загрузчики ручки: какие сутки и какие прогоны читаются (`op_wallboard.routes`)."""

    def setUp(self):
        from op_wallboard import routes
        self.routes = routes
        self.day = datetime(2026, 10, 2).date()

    def test_touches_carry_the_grace_tail_of_the_previous_day(self):
        """Сделка 00:01 владеет звонком 23:59 — как в режиме «Сделки» раздела «Касания»."""
        cursor = _RecordingCursor()
        self.routes.load_lead_touches(cursor, self.day)
        sql, params = cursor.calls[0]
        self.assertIn('call_day BETWEEN %s AND %s AND started_at >= %s', sql)
        self.assertEqual(params[:3], (datetime(2026, 10, 1).date(), self.day, datetime(2026, 10, 1, 23, 58)))

    def test_sync_age_counts_only_runs_that_reread_this_day(self):
        """Ночная выгрузка вчера-позавчера и догрузка прошлых дней сегодняшних сделок не трогают."""
        cursor = _RecordingCursor(one=(120.6,))
        self.assertEqual(self.routes.load_lead_sync_age(cursor, self.day), 120)
        sql, params = cursor.calls[0]
        self.assertIn('period_from <= %s AND period_to >= %s', sql)
        self.assertEqual(params, ('op_osnova', 'amo', self.day, self.day))
        self.assertIsNone(self.routes.load_lead_sync_age(_RecordingCursor(one=(None,)), self.day))

    def test_deals_are_the_osnova_amo_deals_created_this_day(self):
        cursor = _RecordingCursor(rows=[('57060147', datetime(2026, 10, 2, 9, 0), '7015550001', '')])
        deals = self.routes.load_lead_deals(cursor, self.day)
        self.assertEqual(cursor.calls[0][1], ('op_osnova', 'amo', self.day))
        self.assertEqual(deals[0]['lead_key'], '57060147')


class OsnovaScopeTests(unittest.TestCase):
    def test_numbers_and_groups_of_osnova_only(self):
        people = [{'id': 1, 'sip_number': '6650'}, {'id': 2, 'sip_number': '6700'},
                  {'id': 3, 'sip_number': ''}, {'id': 4, 'sip_number': '6651'}]
        memberships = {1: {'group_id': 36, 'model': 'op_osnova'}, 2: {'group_id': 15, 'model': 'op_yandex_reg'},
                       3: {'group_id': 36, 'model': 'op_osnova'}, 4: {'group_id': 40, 'model': 'op_osnova'}}
        self.assertEqual(L.osnova_scope(people, memberships), ({'6650', '6651'}, [36, 40]))
        self.assertEqual(L.osnova_scope(people, {}), (set(), []))


if __name__ == '__main__':
    unittest.main()
