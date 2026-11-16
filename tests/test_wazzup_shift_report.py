# -*- coding: utf-8 -*-
"""«Смены» в «Чатах ОП» (wazzup/shift_report.py): сборка смены из кнопки смены раздела и то,
как сообщения и ответы ложатся в смены. Расчёт чистый — проверяется без базы.

Правила — из просьбы владельца 16.11.2026 («у кого какая смена была и кто как обрабатывал чаты,
интенсивность») и из правил, которые отчёт берёт готовыми: время ответа — «Табло ОП · Чат»,
опоздание — «Учёт часов», смена — кнопка раздела (wazzup/shift.py)."""
from datetime import date, datetime, time, timedelta

import pytest

from op_wallboard import chat as chat_mod
from wazzup import shift as shift_mod
from wazzup import shift_report as R
from wazzup import shift_report_routes as routes

DAY = date(2026, 11, 12)
NOW = datetime(2026, 11, 16, 10, 0)
ANNA, BORIS, VERA = 10, 20, 30   # верификаторы
OSNOVA = 40                       # «Основа»: пишет из Wazzup без смены


def at(hhmm, day=DAY):
    hours, minutes = map(int, hhmm.split(':')[:2])
    seconds = int(hhmm.split(':')[2]) if hhmm.count(':') == 2 else 0
    return datetime.combine(day, time(hours, minutes, seconds))


def ev(hhmm, key, note=None, day=DAY):
    return (at(hhmm, day), key, note)


def client(chat, hhmm, day=DAY):
    return {'channel_id': 'ch', 'chat_id': chat, 'at': at(hhmm, day), 'is_echo': False,
            'user_id': None, 'is_bot': False, 'message_id': f'in-{chat}-{hhmm}-{day}',
            'author_id': None, 'type': 'text'}


def reply(user, chat, hhmm, day=DAY, **extra):
    row = {'channel_id': 'ch', 'chat_id': chat, 'at': at(hhmm, day), 'is_echo': True,
           'user_id': user, 'is_bot': False, 'message_id': f'out-{user}-{chat}-{hhmm}-{day}',
           'author_id': f'icore:{user}', 'type': 'text'}
    row.update(extra)
    return row


def plan(start, end, day=DAY, kind='regular'):
    start_at, end_at = R.plan_interval(day, time(*map(int, start.split(':'))),
                                       time(*map(int, end.split(':'))))
    return (start_at, end_at, kind)


VERIFIER_MODELS = {(user, DAY + timedelta(days=offset)): 'op_verificator'
                   for user in (ANNA, BORIS, VERA) for offset in range(-3, 4)}
MODELS = {**VERIFIER_MODELS, **{(OSNOVA, DAY + timedelta(days=o)): 'op_osnova' for o in range(-3, 4)}}
NAMES = {ANNA: 'Анна', BORIS: 'Борис', VERA: 'Вера', OSNOVA: 'Ольга'}


def report(rows=(), events=None, plans=None, lateness=None, day_from=DAY, day_to=DAY, now=NOW,
           include_shifts=True):
    return R.build_report(day_from=day_from, day_to=day_to, now=now, rows=list(rows),
                          events=events or {}, plans=plans or {}, models=MODELS, names=NAMES,
                          lateness=lateness, include_shifts=include_shifts)


def rows_of(result, kind=None, user=None):
    return [row for row in result['rows']
            if (kind is None or row['kind'] == kind) and (user is None or row['userId'] == user)]


# ── сборка смены ────────────────────────────────────────────────────────────

class TestBuildShifts:
    def test_shift_from_start_to_finish_with_statuses(self):
        shifts = R.build_shifts([
            ev('09:00', 'готов'), ev('11:00', 'перерыв'), ev('11:15', 'готов'),
            ev('13:00', 'тренинг'), ev('13:30', 'тех причина'), ev('13:40', 'готов'),
            ev('18:00', 'выключен'),
        ], NOW)
        assert len(shifts) == 1
        shift = shifts[0]
        assert (shift['start'], shift['end'], shift['open'], shift['end_note']) == (
            at('09:00'), at('18:00'), False, None)
        # «Активный»: 09:00–11:00, 11:15–13:00, 13:40–18:00
        assert R.durations(shift['segments']) == {
            'active': (120 + 105 + 260) * 60.0, 'break': 15 * 60.0, 'training': 30 * 60.0,
            'tech': 10 * 60.0, 'offline': 0.0}

    def test_portal_closed_and_reopened_within_four_hours_is_one_shift(self):
        shifts = R.build_shifts([
            ev('09:00', 'готов'), ev('12:00', 'выключен', shift_mod.AUTO_LOGOUT_NOTE),
            ev('12:40', 'готов'), ev('18:00', 'выключен'),
        ], NOW)
        assert len(shifts) == 1
        segments = shifts[0]['segments']
        assert [s['kind'] for s in segments] == ['active', 'offline', 'active']
        assert segments[1]['note'] == R.END_PORTAL
        assert R.durations(segments)['offline'] == 40 * 60
        assert shifts[0]['end_note'] is None

    def test_return_after_four_hours_is_a_new_shift(self):
        shifts = R.build_shifts([
            ev('09:00', 'готов'), ev('13:00', 'выключен'),
            ev('17:00', 'готов'), ev('22:00', 'выключен'),
        ], NOW)
        assert [(s['start'], s['end']) for s in shifts] == [(at('09:00'), at('13:00')),
                                                            (at('17:00'), at('22:00'))]

    def test_closed_by_portal_watchdog_is_marked(self):
        shifts = R.build_shifts([ev('09:00', 'готов'),
                                 ev('17:12', 'выключен', shift_mod.AUTO_LOGOUT_NOTE)], NOW)
        assert shifts[0]['end_note'] == R.END_PORTAL
        long_shift = R.build_shifts([ev('09:00', 'готов'),
                                     ev('09:00', 'выключен', shift_mod.LONG_SHIFT_NOTE, day=DAY + timedelta(days=1))],
                                    NOW)
        assert long_shift[0]['end_note'] == R.END_LONG

    def test_open_shift_runs_until_now(self):
        now = at('15:30')
        shifts = R.build_shifts([ev('09:00', 'готов'), ev('12:00', 'перерыв')], now)
        assert shifts[0]['open'] is True
        assert shifts[0]['end'] == now
        assert R.durations(shifts[0]['segments'])['break'] == 3.5 * 3600

    def test_status_left_for_sixteen_hours_ends_on_the_cap(self):
        now = at('12:00', DAY + timedelta(days=2))
        shifts = R.build_shifts([ev('09:00', 'готов')], now)
        assert shifts[0]['open'] is False
        assert shifts[0]['end'] == at('09:00') + timedelta(hours=16)
        assert shifts[0]['end_note'] == R.END_LONG

    def test_stale_status_does_not_glue_next_day(self):
        # Сторож не записал выход: следующее утро — новая смена, а не продолжение вчерашней.
        shifts = R.build_shifts([ev('09:00', 'готов'), ev('09:00', 'готов', day=DAY + timedelta(days=1)),
                                 ev('18:00', 'выключен', day=DAY + timedelta(days=1))], NOW)
        assert len(shifts) == 2
        assert shifts[0]['end'] == at('09:00') + timedelta(hours=16)
        assert shifts[0]['end_note'] == R.END_LONG
        assert shifts[1]['start'] == at('09:00', DAY + timedelta(days=1))

    def test_repeats_unknown_keys_and_double_logout_change_nothing(self):
        shifts = R.build_shifts([
            ev('09:00', 'готов'), ev('09:05', 'готов'), ev('09:10', 'занят'),
            ev('18:00', 'выключен'), ev('18:01', 'выключен'),
        ], NOW)
        assert len(shifts) == 1
        assert [s['kind'] for s in shifts[0]['segments']] == ['active']
        assert shifts[0]['end'] == at('18:00')

    def test_logout_without_shift_is_ignored(self):
        assert R.build_shifts([ev('08:00', 'выключен')], NOW) == []

    def test_status_keys_come_from_the_shift_menu(self):
        assert set(R.STATUS_KINDS) == shift_mod.ON_SHIFT_KEYS

    def test_return_clears_the_end_note_of_a_running_shift(self):
        events = {ANNA: [ev('09:00', 'готов'), ev('12:00', 'выключен', shift_mod.AUTO_LOGOUT_NOTE),
                         ev('12:40', 'готов')]}
        row = rows_of(report(events=events, now=at('15:00')), R.ROW_SHIFT, ANNA)[0]
        assert (row['endNote'], row['open'], row['offline']) == (None, True, 40 * 60)
        assert [segment['note'] for segment in row['segments']] == [None, R.END_PORTAL, None]


# ── выход iCORE Phone, «съевший» выход раздела ──────────────────────────────

class TestPhoneLogout:
    LATER = datetime.combine(DAY + timedelta(days=2), time(12, 0))

    def test_swallowed_section_logout_ends_on_the_phone_logout(self):
        shifts = R.build_shifts([ev('09:00', 'готов')], self.LATER,
                                outside_offs=[at('18:00')], activity=[at('10:00'), at('17:50')])
        assert (shifts[0]['end'], shifts[0]['end_note'], shifts[0]['open']) == (at('18:00'), R.END_PHONE, False)
        assert R.durations(shifts[0]['segments'])['active'] == 9 * 3600

    def test_phone_logout_before_his_last_message_is_not_the_end(self):
        shifts = R.build_shifts([ev('09:00', 'готов')], self.LATER,
                                outside_offs=[at('12:00')], activity=[at('17:00')])
        assert (shifts[0]['end'], shifts[0]['end_note']) == (at('09:00') + timedelta(hours=16), R.END_LONG)

    def test_phone_logout_inside_a_shift_that_went_on_changes_nothing(self):
        events = [ev('09:00', 'готов'), ev('12:00:05', 'готов'), ev('18:00', 'выключен')]
        shifts = R.build_shifts(events, self.LATER, outside_offs=[at('12:00')], activity=[at('13:00')])
        assert (shifts[0]['end'], shifts[0]['end_note']) == (at('18:00'), None)

    def test_stale_status_with_phone_logout_does_not_glue_the_night(self):
        next_day = DAY + timedelta(days=1)
        events = [ev('09:00', 'готов'), ev('09:00', 'готов', day=next_day), ev('18:00', 'выключен', day=next_day)]
        shifts = R.build_shifts(events, self.LATER, outside_offs=[at('18:00')], activity=[at('17:00')])
        assert [(s['start'], s['end'], s['end_note']) for s in shifts] == [
            (at('09:00'), at('18:00'), R.END_PHONE), (at('09:00', next_day), at('18:00', next_day), None)]

    def test_open_shift_ends_on_phone_logout_after_four_hours_of_silence(self):
        args = dict(outside_offs=[at('18:00')], activity=[at('17:50')])
        quiet = R.build_shifts([ev('09:00', 'готов')], at('22:00'), **args)
        assert (quiet[0]['end'], quiet[0]['end_note'], quiet[0]['open']) == (at('18:00'), R.END_PHONE, False)
        recent = R.build_shifts([ev('09:00', 'готов')], at('21:59'), **args)
        assert (recent[0]['end'], recent[0]['open']) == (at('21:59'), True)

    def test_report_passes_phone_logouts_and_messages_through(self):
        rows = [client('c1', '17:40'), reply(ANNA, 'c1', '17:50')]
        result = report(rows, {ANNA: [ev('09:00', 'готов')]}, now=self.LATER)
        assert rows_of(result, R.ROW_SHIFT, ANNA)[0]['endNote'] == R.END_LONG
        result = R.build_report(day_from=DAY, day_to=DAY, now=self.LATER, rows=rows,
                                events={ANNA: [ev('09:00', 'готов')]}, plans={}, models=MODELS,
                                names=NAMES, outside_offs={ANNA: [at('18:00')]})
        row = rows_of(result, R.ROW_SHIFT, ANNA)[0]
        assert (row['end'][11:16], row['endNote'], row['messages']) == ('18:00', R.END_PHONE, 1)


# ── сообщения и ответы ──────────────────────────────────────────────────────

class TestWork:
    def test_messages_and_replies_belong_to_the_shift(self):
        events = {ANNA: [ev('09:00', 'готов'), ev('13:00', 'выключен')]}
        rows = [client('c1', '09:10'), reply(ANNA, 'c1', '09:11'), reply(ANNA, 'c1', '09:12'),
                client('c1', '09:20'), reply(ANNA, 'c1', '09:24'),
                client('c2', '10:00'), reply(ANNA, 'c2', '10:00:30')]
        shift = rows_of(report(rows, events), R.ROW_SHIFT, ANNA)[0]
        assert (shift['chats'], shift['messages']) == (2, 4)
        # первые ответы: c1 через 60 с, c2 через 30 с
        assert (shift['firstSum'], shift['firstCount']) == (90.0, 2)
        # ответ в чате: c1 — среднее (60, 240) = 150, c2 — 30; по диалогам
        assert (shift['innerSum'], shift['innerCount']) == (180.0, 2)
        assert shift['active'] == 4 * 3600
        assert shift['chatsPerHour'] == 0.5
        assert [h['hour'][11:13] for h in shift['hours']] == ['09', '10', '11', '12', '13']
        assert [(h['chats'], h['messages']) for h in shift['hours']][:2] == [(1, 3), (1, 1)]

    def test_first_reply_goes_to_whoever_answered_first(self):
        events = {ANNA: [ev('09:00', 'готов'), ev('18:00', 'выключен')],
                  BORIS: [ev('09:00', 'готов'), ev('18:00', 'выключен')]}
        rows = [client('c1', '10:00'), reply(BORIS, 'c1', '10:02'), client('c1', '10:05'),
                reply(ANNA, 'c1', '10:06')]
        result = report(rows, events)
        anna = rows_of(result, R.ROW_SHIFT, ANNA)[0]
        boris = rows_of(result, R.ROW_SHIFT, BORIS)[0]
        assert (boris['firstSum'], boris['firstCount'], boris['innerSum']) == (120.0, 1, 120.0)
        assert (anna['firstSum'], anna['firstCount'], anna['innerSum'], anna['innerCount']) == (0.0, 0, 60.0, 1)
        assert anna['chats'] == boris['chats'] == 1

    def test_dialog_on_a_shift_boundary_is_split_by_reply_time(self):
        # Одна смена до 13:00, вторая с 17:30: ответ в 12:59 — первой, ответ в 17:31 — второй.
        events = {ANNA: [ev('09:00', 'готов'), ev('13:00', 'выключен'),
                         ev('17:30', 'готов'), ev('20:00', 'выключен')]}
        rows = [client('c1', '12:58'), reply(ANNA, 'c1', '12:59'),
                client('c1', '17:00'), reply(ANNA, 'c1', '17:31')]
        morning, evening = sorted(rows_of(report(rows, events), R.ROW_SHIFT, ANNA),
                                  key=lambda row: row['start'])
        assert (morning['firstCount'], morning['innerSum'], morning['messages']) == (1, 60.0, 1)
        assert (evening['firstCount'], evening['innerSum'], evening['messages']) == (0, 31 * 60.0, 1)

    def test_message_a_few_minutes_after_logout_still_counts(self):
        events = {ANNA: [ev('09:00', 'готов'), ev('13:00', 'выключен')]}
        rows = [reply(ANNA, 'c1', '13:04'), reply(ANNA, 'c2', '13:30')]
        result = report(rows, events)
        assert rows_of(result, R.ROW_SHIFT, ANNA)[0]['messages'] == 1
        loose = rows_of(result, R.ROW_NO_SHIFT, ANNA)
        assert len(loose) == 1 and loose[0]['messages'] == 1 and loose[0]['start'].endswith('13:30:00')

    @pytest.mark.parametrize('hhmmss, in_shift', [
        ('08:58:59', False), ('08:59:01', True),     # минута до начала
        ('13:04:59', True), ('13:05:01', False),     # пять минут после выхода
    ])
    def test_attach_window_edges(self, hhmmss, in_shift):
        events = {ANNA: [ev('09:00', 'готов'), ev('13:00', 'выключен')]}
        result = report([reply(ANNA, 'c1', hhmmss)], events)
        assert rows_of(result, R.ROW_SHIFT, ANNA)[0]['messages'] == (1 if in_shift else 0)
        assert len(rows_of(result, R.ROW_NO_SHIFT, ANNA)) == (0 if in_shift else 1)

    def test_no_shift_work_outside_the_period_is_not_shown(self):
        rows = [reply(OSNOVA, 'c1', '23:00', DAY - timedelta(days=1)),
                reply(OSNOVA, 'c2', '01:00', DAY + timedelta(days=1)),
                reply(OSNOVA, 'c3', '12:00')]
        loose = rows_of(report(rows), R.ROW_NO_SHIFT, OSNOVA)
        assert [row['date'] for row in loose] == [DAY.isoformat()]

    def test_hours_chart_covers_messages_in_the_attach_margins(self):
        events = {ANNA: [ev('10:00:30', 'готов'), ev('17:58', 'выключен')]}
        rows = [reply(ANNA, 'c1', '09:59:50'), reply(ANNA, 'c2', '12:00'), reply(ANNA, 'c3', '18:02')]
        row = rows_of(report(rows, events), R.ROW_SHIFT, ANNA)[0]
        assert row['messages'] == 3
        assert sum(hour['messages'] for hour in row['hours']) == 3
        assert (row['hours'][0]['hour'][11:13], row['hours'][-1]['hour'][11:13]) == ('09', '18')

    def test_chats_per_hour_divides_by_active_only(self):
        # Смена 8 часов, из них 2 часа перерыва и тренинга: 12 чатов за 6 часов «Активного» — 2,0.
        events = {ANNA: [ev('09:00', 'готов'), ev('12:00', 'перерыв'), ev('13:00', 'тренинг'),
                         ev('14:00', 'готов'), ev('17:00', 'выключен')]}
        rows = [reply(ANNA, f'c{index}', f'{9 + index // 4:02d}:{(index % 4) * 10:02d}') for index in range(12)]
        row = rows_of(report(rows, events), R.ROW_SHIFT, ANNA)[0]
        assert (row['chats'], row['active'], row['breakTime'], row['training']) == (12, 6 * 3600, 3600, 3600)
        assert row['chatsPerHour'] == 2.0

    def test_writer_without_shift_gets_a_day_row(self):
        rows = [client('c1', '10:00'), reply(OSNOVA, 'c1', '10:01', author_id='wz-77'),
                client('c2', '15:00'), reply(OSNOVA, 'c2', '15:05', author_id='wz-77')]
        loose = rows_of(report(rows, plans={OSNOVA: [plan('10:00', '19:00')]}), R.ROW_NO_SHIFT, OSNOVA)
        assert len(loose) == 1
        row = loose[0]
        assert (row['chats'], row['messages'], row['firstCount'], row['groupKey']) == (2, 2, 2, 'osnova')
        assert (row['start'][11:16], row['end'][11:16]) == ('10:01', '15:05')
        assert row['plan'] == [{'start': '10:00', 'end': '19:00', 'nextDay': False, 'type': None}]
        assert row['active'] == 0 and row['chatsPerHour'] is None

    def test_night_shift_belongs_to_its_evening(self):
        next_day = DAY + timedelta(days=1)
        events = {ANNA: [ev('21:00', 'готов'), ev('09:00', 'выключен', day=next_day)]}
        rows = [client('c1', '02:00', next_day), reply(ANNA, 'c1', '02:01', next_day)]
        result = report(rows, events, plans={ANNA: [plan('21:00', '09:00')]},
                        day_from=DAY, day_to=next_day)
        shifts = rows_of(result, R.ROW_SHIFT, ANNA)
        assert len(shifts) == 1 and shifts[0]['date'] == DAY.isoformat()
        assert shifts[0]['messages'] == 1
        assert shifts[0]['plan'] == [{'start': '21:00', 'end': '09:00', 'nextDay': True, 'type': None}]
        assert rows_of(result, R.ROW_NO_SHIFT) == []

    def test_shift_from_the_day_before_the_period_is_not_shown_and_keeps_its_messages(self):
        prev = DAY - timedelta(days=1)
        events = {ANNA: [ev('21:00', 'готов', day=prev), ev('09:00', 'выключен')]}
        rows = [client('c1', '03:00'), reply(ANNA, 'c1', '03:01')]
        result = report(rows, events)
        assert rows_of(result, user=ANNA) == []

    def test_bots_and_unlinked_authors_are_nobodys_work(self):
        events = {ANNA: [ev('09:00', 'готов'), ev('18:00', 'выключен')]}
        rows = [client('c1', '10:00'),
                {**reply(None, 'c1', '10:01'), 'author_id': 'wz-new', 'user_id': None},
                reply(ANNA, 'c1', '10:02'),
                {**reply(ANNA, 'c2', '10:03'), 'is_bot': True}]
        result = report(rows, events)
        anna = rows_of(result, R.ROW_SHIFT, ANNA)[0]
        # Непривязанный автор закрыл ожидание первым: Анне первый ответ не засчитан.
        assert (anna['messages'], anna['firstCount'], anna['innerCount']) == (1, 0, 0)
        assert result['unlinked'] == {'authors': 1, 'messages': 1}

    def test_unlinked_templates_are_mailings_not_missing_work(self):
        rows = [{**reply(None, 'c1', '10:01'), 'author_id': 'wz-yar', 'user_id': None,
                 'type': chat_mod.TEMPLATE_TYPE}]
        assert report(rows)['unlinked'] == {'authors': 0, 'messages': 0}

    def test_rate_needs_a_quarter_hour_of_active(self):
        assert R.chats_per_hour(3, 14 * 60) is None
        assert R.chats_per_hour(3, 15 * 60) == 12.0
        assert R.chats_per_hour(10, 4 * 3600) == 2.5


# ── график, опоздание, «не вышел» ───────────────────────────────────────────

class TestPlan:
    def test_plan_overlapping_the_shift_and_out_of_schedule(self):
        events = {ANNA: [ev('09:05', 'готов'), ev('18:00', 'выключен')],
                  BORIS: [ev('09:00', 'готов'), ev('12:00', 'выключен')]}
        plans = {ANNA: [plan('09:00', '18:00'), plan('09:00', '18:00', day=DAY + timedelta(days=1))]}
        result = report(events=events, plans=plans)
        assert rows_of(result, R.ROW_SHIFT, ANNA)[0]['plan'] == [
            {'start': '09:00', 'end': '18:00', 'nextDay': False, 'type': None}]
        assert rows_of(result, R.ROW_SHIFT, BORIS)[0]['plan'] == []

    def test_lateness_from_hours_accounting_goes_to_first_and_last_shift_of_the_plan(self):
        events = {ANNA: [ev('09:20', 'готов'), ev('12:00', 'выключен'),
                         ev('17:00', 'готов'), ev('20:30', 'выключен')]}
        result = report(events=events, plans={ANNA: [plan('09:00', '21:00')]},
                        lateness={(ANNA, DAY): (1200, 1800)})
        morning, evening = sorted(rows_of(result, R.ROW_SHIFT, ANNA), key=lambda row: row['start'])
        assert (morning['lateSeconds'], morning['earlySeconds']) == (1200, 0)
        assert (evening['lateSeconds'], evening['earlySeconds']) == (0, 1800)

    def test_lateness_follows_the_plan_date_of_a_late_night_shift(self):
        # Ночная смена графика 12-го с 21:00 начата в 00:30 13-го: строка 13-го, а опоздание — 12-го.
        next_day = DAY + timedelta(days=1)
        third = DAY + timedelta(days=2)
        events = {ANNA: [ev('00:30', 'готов', day=next_day), ev('09:00', 'выключен', day=next_day),
                         ev('21:10', 'готов', day=next_day), ev('09:00', 'выключен', day=third)]}
        plans = {ANNA: [plan('21:00', '09:00'), plan('21:00', '09:00', day=next_day)]}
        result = report(events=events, plans=plans, day_from=DAY, day_to=next_day,
                        lateness={(ANNA, DAY): (12600, 0), (ANNA, next_day): (600, 0)})
        late_night, evening = sorted(rows_of(result, R.ROW_SHIFT, ANNA), key=lambda row: row['start'])
        assert (late_night['date'], late_night['lateSeconds']) == (next_day.isoformat(), 12600)
        assert (evening['date'], evening['lateSeconds']) == (next_day.isoformat(), 600)

    def test_shift_out_of_schedule_has_no_lateness(self):
        events = {ANNA: [ev('14:00', 'готов'), ev('18:00', 'выключен')]}
        row = rows_of(report(events=events, lateness={(ANNA, DAY): (3600, 0)}), R.ROW_SHIFT, ANNA)[0]
        assert (row['plan'], row['lateSeconds']) == ([], 0)

    def test_open_shift_has_no_early_leave(self):
        events = {ANNA: [ev('09:00', 'готов')]}
        result = report(events=events, plans={ANNA: [plan('09:00', '18:00')]},
                        lateness={(ANNA, DAY): (0, 3600)}, now=at('15:00'))
        row = rows_of(result, R.ROW_SHIFT, ANNA)[0]
        assert row['open'] is True and row['earlySeconds'] == 0

    def test_plan_worked_at_other_hours_is_absent_plus_shift_out_of_schedule(self):
        # Так задумано: смена в другие часы того же дня смену графика не закрывает.
        result = report(events={VERA: [ev('14:00', 'готов'), ev('18:00', 'выключен')]},
                        plans={VERA: [plan('09:00', '13:00')]})
        assert sorted((row['kind'], row['start'][11:16]) for row in rows_of(result, user=VERA)) == [
            (R.ROW_ABSENT, '09:00'), (R.ROW_SHIFT, '14:00')]

    def test_absent_verifier(self):
        plans = {VERA: [plan('09:00', '18:00')], OSNOVA: [plan('09:00', '18:00')]}
        absent = rows_of(report(plans=plans), R.ROW_ABSENT)
        assert [(row['userId'], row['plan'][0]['start']) for row in absent] == [(VERA, '09:00')]

    def test_not_absent_yet_within_half_an_hour(self):
        plans = {VERA: [plan('09:00', '18:00')]}
        assert rows_of(report(plans=plans, now=at('09:29')), R.ROW_ABSENT) == []
        assert len(rows_of(report(plans=plans, now=at('09:31')), R.ROW_ABSENT)) == 1

    def test_not_absent_when_worked_or_wrote_or_phone_shift(self):
        worked = report(events={VERA: [ev('10:00', 'готов'), ev('17:00', 'выключен')]},
                        plans={VERA: [plan('09:00', '18:00')]})
        assert rows_of(worked, R.ROW_ABSENT) == []
        wrote = report(rows=[client('c1', '10:00'), reply(VERA, 'c1', '10:01')],
                       plans={VERA: [plan('09:00', '18:00')]})
        assert rows_of(wrote, R.ROW_ABSENT) == []
        assert len(rows_of(wrote, R.ROW_NO_SHIFT, VERA)) == 1
        phone = report(plans={VERA: [plan('09:00', '18:00', kind='phone_shift')]})
        assert rows_of(phone, R.ROW_ABSENT) == []

    def test_account_without_shifts(self):
        events = {ANNA: [ev('09:00', 'готов'), ev('18:00', 'выключен')]}
        rows = [client('c1', '10:00'), reply(ANNA, 'c1', '10:01')]
        result = report(rows, events, plans={VERA: [plan('09:00', '18:00')]}, include_shifts=False)
        assert [row['kind'] for row in result['rows']] == [R.ROW_NO_SHIFT]


# ── порядок и группы ────────────────────────────────────────────────────────

def test_rows_fresh_day_first_then_by_start_and_groups_in_order():
    next_day = DAY + timedelta(days=1)
    events = {ANNA: [ev('14:00', 'готов'), ev('18:00', 'выключен'),
                     ev('09:00', 'готов', day=next_day), ev('12:00', 'выключен', day=next_day)],
              BORIS: [ev('09:00', 'готов'), ev('12:00', 'выключен')]}
    rows = [client('c9', '11:00'), reply(OSNOVA, 'c9', '11:01')]
    result = report(rows, events, day_from=DAY, day_to=next_day)
    assert [(row['date'], row['userId']) for row in result['rows']] == [
        (next_day.isoformat(), ANNA), (DAY.isoformat(), BORIS), (DAY.isoformat(), OSNOVA),
        (DAY.isoformat(), ANNA)]
    assert [group['key'] for group in result['groups']] == ['verifier', 'osnova']
    keys = [row['key'] for row in result['rows']]
    assert len(keys) == len(set(keys))


def test_group_key_by_model():
    assert R.group_key('op_verificator') == 'verifier'
    assert R.group_key('OP_POTOK') == 'potok'
    assert R.group_key('szov_line') == R.GROUP_OTHER
    assert R.group_key('') == R.GROUP_NONE


# ── общий расчёт табло помнит каждый ответ ──────────────────────────────────

def test_wallboard_episode_keeps_replies_in_order():
    messages = chat_mod.classify([client('c1', '10:00'), reply(ANNA, 'c1', '10:01'),
                                  reply(ANNA, 'c1', '10:02'), client('c1', '10:10'),
                                  reply(BORIS, 'c1', '10:13')], {ANNA, BORIS})
    (episode,) = chat_mod.split_episodes(messages)
    assert episode['replies'] == [(ANNA, at('10:01'), 60.0, True), (BORIS, at('10:13'), 180.0, False)]


@pytest.mark.parametrize('key', ['выключен', 'Выключен ', 'logout', 'offline'])
def test_off_shift_keys(key):
    assert shift_mod.is_off_shift_key(key)
    assert not shift_mod.is_off_shift_key('готов')


# ── период ручки (чистая часть shift_report_routes; SQL-тест в CI не идёт) ──

class TestParsePeriod:
    TODAY = date(2026, 11, 16)

    def parse(self, start, end, **kwargs):
        return routes.parse_period(start, end, self.TODAY, **kwargs)

    def test_empty_is_the_last_seven_days(self):
        assert self.parse('', '') == (date(2026, 11, 10), self.TODAY)

    def test_one_side_is_one_day_and_reversed_is_swapped(self):
        assert self.parse('2026-11-12', '') == (date(2026, 11, 12), date(2026, 11, 12))
        assert self.parse('2026-11-16', '2026-11-10') == (date(2026, 11, 10), self.TODAY)

    def test_future_tail_is_cut_and_future_start_refused(self):
        assert self.parse('2026-11-14', '2026-11-20') == (date(2026, 11, 14), self.TODAY)
        with pytest.raises(ValueError, match='в будущем'):
            self.parse('2026-11-17', '2026-11-18')

    def test_thirty_one_days_pass_thirty_two_do_not(self):
        assert self.parse('2026-10-17', '2026-11-16') == (date(2026, 10, 17), self.TODAY)
        with pytest.raises(ValueError, match='31 суток'):
            self.parse('2026-10-16', '2026-11-16')
        assert self.parse('2026-11-14', '2026-11-16', max_days=3)
        with pytest.raises(ValueError, match='3 суток'):
            self.parse('2026-11-13', '2026-11-16', max_days=3)

    def test_retention_edge(self):
        assert self.parse('2026-10-03', '2026-10-05')[0] == date(2026, 10, 3)
        with pytest.raises(ValueError, match='45 суток'):
            self.parse('2026-10-02', '2026-10-05')

    def test_bad_format(self):
        with pytest.raises(ValueError, match='ГГГГ-ММ-ДД'):
            self.parse('16.11.2026', '')
