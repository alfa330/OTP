# -*- coding: utf-8 -*-
"""Сторож линий Binotel: правила сверки, разбор вебхука, чтение линий, запись presence, сервис.

Случаи взяты из разбора 16.11.2026 («упущенные при операторе на линии», Тез КЦ), люди, линии и
номера звонков выдуманы. Базы и сети здесь нет: журнал подменён записью в память.

Что сторожится:
* расхождение считается только отстоявшимся: секунды между событием телефона и записью в
  кабинет — не случай;
* «Активен» в кабинете у выключенного телефона и «Неактивен» у работающего — разные виды, и
  чинятся в разные стороны; забытый включённым телефон в очередь не возвращаем;
* плечи вебхука приходят от последнего к первому — ожидание в очереди и «не снял трубку»
  считаются в порядке звонка;
* из ответа Binotel наружу выходит только состояние линии, без SIP-логинов и паролей;
* запись presence повторяет телефон: вход, своя карточка, форма карточки с новым presenceState;
* случай открывается один раз, переживает перезапуск процесса и закрывается, когда прошёл;
* вебхук только ставит звонок в очередь, событие пишется одно на звонок, вид и линию.
"""
import unittest

from line_watch import binotel, rules
from line_watch.service import (ALIVE_SECONDS, CALL_SETTLE_SECONDS, HEAL_DENIED_RETRY_SECONDS,
                                HEAL_MAX_TRIES, HEAL_RETRY_SECONDS, TOUCH_SECONDS, LineWatchService,
                                heal_plan)

NOW = 1_794_500_000          # условное «сейчас», unix


def webhook(call_id, start, disposition, legs, call_type='0', waitsec=None, billsec=0):
    """Поля вебхука как их отдаёт dial_list.service.webhook_fields; legs — в порядке звонка:
    [(цель, ожидание, разговор, итог)]. Binotel кладёт их от последнего к первому."""
    fields = {
        'requestType': 'apiCallCompleted',
        'callDetails[generalCallID]': str(call_id),
        'callDetails[startTime]': str(start),
        'callDetails[callType]': call_type,
        'callDetails[disposition]': disposition,
        'callDetails[waitsec]': str(sum(leg[1] for leg in legs) if waitsec is None else waitsec),
        'callDetails[billsec]': str(billsec),
        'callDetails[externalNumber]': '87010000000',
    }
    for index, (target, wait, talk, result) in enumerate(reversed(legs)):
        prefix = 'callDetails[historyData][%d]' % index
        fields[prefix + '[internalNumber]'] = target
        fields[prefix + '[waitsec]'] = str(wait)
        fields[prefix + '[billsec]'] = str(talk)
        fields[prefix + '[disposition]'] = result
    return fields


class ClassifyTests(unittest.TestCase):
    def test_working_operator_closed_in_cabinet(self):
        # Второй телефон закрыли на другом компьютере: он поставил «Неактивен», человек работает.
        self.assertEqual(rules.classify('готов', 600, 'inactive', 4000, 'online', 500),
                         rules.KIND_CABINET_CLOSED)
        self.assertEqual(rules.classify('готов', 600, 'Work In CRM', 700), rules.KIND_CABINET_CLOSED)

    def test_seconds_between_phone_and_cabinet_are_not_a_case(self):
        self.assertIsNone(rules.classify('готов', 3, 'work in crm', 200))
        self.assertIsNone(rules.classify('готов', 600, 'inactive', 20))
        self.assertIsNone(rules.classify('перезвон', 5, 'active', 900))

    def test_talk_is_legal_under_crm_but_not_under_break(self):
        self.assertIsNone(rules.classify('занят', 40, 'work in crm', 300))
        self.assertIsNone(rules.classify('занят', 40, 'active', 300))
        self.assertEqual(rules.classify('занят', 40, 'inactive', 300), rules.KIND_CABINET_CLOSED)
        self.assertEqual(rules.classify('занят', 40, 'break in work', 300), rules.KIND_CABINET_CLOSED)

    def test_closed_phone_with_active_cabinet(self):
        self.assertEqual(rules.classify('перезвон', 200, 'active', 300, 'online', 100),
                         rules.KIND_CABINET_OPEN)
        self.assertEqual(rules.classify('перерыв', 200, 'active', 300), rules.KIND_CABINET_OPEN)
        # «Работа в CRM» вместо «Перерыва» очередь закрывает так же — не случай.
        self.assertIsNone(rules.classify('перерыв', 200, 'work in crm', 300))
        self.assertIsNone(rules.classify('перезвон', 200, 'inactive', 300))

    def test_status_left_after_exit(self):
        self.assertEqual(rules.classify('выключен', 700, 'active', 800, 'offline', 700),
                         rules.KIND_CABINET_STALE)
        self.assertEqual(rules.classify('выключен', 700, 'break in work', 800), rules.KIND_CABINET_STALE)
        # Выход в процессе: телефон сам ставит «Неактивен» и ждёт до 8 секунд.
        self.assertIsNone(rules.classify('выключен', 30, 'active', 800))
        self.assertIsNone(rules.classify('выключен', 700, 'inactive', 700))

    def test_line_without_phone_events(self):
        # Линия уволенной сотрудницы: событий телефона нет, а в кабинете «Активен» без даты.
        self.assertEqual(rules.classify(None, None, 'active', None, 'offline', None),
                         rules.KIND_CABINET_STALE)
        self.assertIsNone(rules.classify(None, None, 'inactive', None))

    def test_line_not_seen_by_binotel(self):
        self.assertEqual(rules.classify('готов', 400, 'active', 400, 'offline', 300),
                         rules.KIND_LINE_OFFLINE)
        self.assertIsNone(rules.classify('готов', 400, 'active', 400, 'offline', 60))
        self.assertIsNone(rules.classify('готов', 400, 'active', 400, 'online', 300))
        self.assertIsNone(rules.classify('готов', 400, 'active', 400, 'inuse', 300))

    def test_unknown_presence_is_not_compared(self):
        self.assertIsNone(rules.classify('готов', 600, '', 600))
        self.assertIsNone(rules.classify('готов', 600, 'vacation', 600))


class HealPlanTests(unittest.TestCase):
    def snap(self, **over):
        base = {'operator_id': 7, 'phone': 'готов', 'phone_age': 600, 'presence': 'inactive',
                'presence_age': 4000, 'sip': 'online', 'sip_age': 500, 'inuse_age': 120}
        base.update(over)
        return base

    def test_stale_status_goes_inactive_only_when_line_is_silent(self):
        snap = self.snap(phone='выключен', presence='active', sip='offline')
        self.assertEqual(heal_plan(rules.KIND_CABINET_STALE, snap), 'inactive')
        # На линии кто-то зарегистрирован — работающего человека «Неактивным» не делаем.
        self.assertIsNone(heal_plan(rules.KIND_CABINET_STALE, dict(snap, sip='online')))
        self.assertIsNone(heal_plan(rules.KIND_CABINET_STALE, dict(snap, sip='inuse')))

    def test_working_operator_returns_to_active_only_with_signs_of_work(self):
        self.assertEqual(heal_plan(rules.KIND_CABINET_CLOSED, self.snap()), 'active')
        # Телефон забыли включённым на спящем компьютере: проснулся и прислал «готов», а линии
        # в сети нет или по ней давно тихо — в очередь его не возвращаем.
        self.assertIsNone(heal_plan(rules.KIND_CABINET_CLOSED, self.snap(sip='offline')))
        quiet = self.snap(phone_age=ALIVE_SECONDS + 1, inuse_age=ALIVE_SECONDS + 1)
        self.assertIsNone(heal_plan(rules.KIND_CABINET_CLOSED, quiet))
        self.assertEqual(heal_plan(rules.KIND_CABINET_CLOSED, dict(quiet, inuse_age=60)), 'active')
        # Под «занят» выбранный статус неизвестен — писать нечего.
        self.assertIsNone(heal_plan(rules.KIND_CABINET_CLOSED, self.snap(phone='занят')))

    def test_closed_phone_gets_its_own_presence(self):
        snap = self.snap(phone='перезвон', presence='active')
        self.assertEqual(heal_plan(rules.KIND_CABINET_OPEN, snap), 'work in crm')
        self.assertEqual(heal_plan(rules.KIND_CABINET_OPEN, dict(snap, phone='перерыв')), 'break in work')
        self.assertIsNone(heal_plan(rules.KIND_CABINET_OPEN, dict(snap, sip='offline')))

    def test_nothing_to_write_without_employee_or_for_line_offline(self):
        self.assertIsNone(heal_plan(rules.KIND_CABINET_STALE,
                                    self.snap(operator_id=None, phone=None, presence='active', sip='offline')))
        self.assertIsNone(heal_plan(rules.KIND_LINE_OFFLINE, self.snap(presence='active', sip='offline')))


class WebhookCallTests(unittest.TestCase):
    def test_legs_come_last_first_and_are_put_in_call_order(self):
        call = rules.call_from_webhook(webhook(601, NOW, 'ANSWER', [
            ('Очередь', 90, 0, 'NOANSWER'), ('915', 2, 682, 'ANSWER')], billsec=682))
        self.assertEqual([leg['target'] for leg in call['legs']], ['Очередь', '915'])
        self.assertEqual([leg['start'] for leg in call['legs']], [NOW, NOW + 90])
        self.assertTrue(call['incoming'])
        self.assertEqual(rules.queue_wait(call), (NOW, 90))

    def test_lost_in_queue(self):
        call = rules.call_from_webhook(webhook(602, NOW, 'NOANSWER', [('Очередь', 120, 0, 'NOANSWER')]))
        self.assertEqual(rules.queue_wait(call), (NOW, 120))
        self.assertEqual(rules.failed_operator_legs(call, ['915']), [])
        self.assertIsNone(rules.group_drop(call, ['915']))

    def test_greeting_is_not_a_queue(self):
        call = rules.call_from_webhook(webhook(603, NOW, 'CANCEL', [
            ('Приветствие в рабочее время New 1', 1, 0, 'CANCEL')]))
        self.assertIsNone(rules.queue_wait(call))

    def test_operator_did_not_pick_up(self):
        call = rules.call_from_webhook(webhook(604, NOW, 'ANSWER', [
            ('907', 16, 0, 'BUSY'), ('907', 0, 0, 'CONGESTION'), ('904', 2, 66, 'ANSWER')], billsec=66))
        self.assertEqual(rules.failed_operator_legs(call, ['904', '907']), [('907', NOW, 16, 'BUSY')])
        # Клиент сбросил за две секунды — это не «телефон не снял».
        quick = rules.call_from_webhook(webhook(605, NOW, 'CANCEL', [('913', 2, 0, 'CANCEL')]))
        self.assertEqual(rules.failed_operator_legs(quick, ['913']), [])

    def test_group_drop_and_group_leg_to_line(self):
        dropped = rules.call_from_webhook(webhook(606, NOW, 'CHANUNAVAIL', [
            ('Приветствие в рабочее время New 1', 3, 0, 'NOANSWER'), ('802', 0, 0, 'CHANUNAVAIL')]))
        self.assertEqual(rules.group_drop(dropped, ['920', '929']), (NOW + 3, '802'))
        via_group = rules.call_from_webhook(webhook(607, NOW, 'BUSY', [('802 > 929', 4, 0, 'BUSY')]))
        self.assertEqual(rules.group_drop(via_group, ['920', '929']), (NOW, '802'))
        self.assertEqual(rules.leg_line('802 > 929'), '929')
        # Отказ на линии оператора — не срыв группы, а отвеченный звонок — не срыв вовсе.
        own = rules.call_from_webhook(webhook(608, NOW, 'CHANUNAVAIL', [('912', 5, 0, 'CHANUNAVAIL')]))
        self.assertIsNone(rules.group_drop(own, ['912']))
        answered = rules.call_from_webhook(webhook(609, NOW, 'ANSWER', [('802 > 924', 3, 40, 'ANSWER')],
                                                   billsec=40))
        self.assertIsNone(rules.group_drop(answered, ['924']))

    def test_outgoing_and_broken_bodies(self):
        outgoing = rules.call_from_webhook(webhook(610, NOW, 'CANCEL', [('', 12, 0, 'CANCEL')], call_type='1'))
        self.assertFalse(outgoing['incoming'])
        self.assertIsNone(rules.queue_wait(outgoing))
        self.assertIsNone(rules.call_from_webhook({}))
        self.assertIsNone(rules.call_from_webhook({'callDetails[generalCallID]': '5'}))
        self.assertIsNone(rules.call_from_webhook(None))


class ReadLinesTests(unittest.TestCase):
    PAYLOAD = {'status': 'success', 'listOfEmployees': {
        '111': {'employeeID': '111', 'email': 'a@example.kz', 'name': 'Выдуманная Алия',
                'presenceState': 'Active', 'presenceStateUpdatedAt': '1794490000',
                'endpointData': {'internalNumber': '915', 'login': 'sip-fixture-915', 'password': 'not-a-secret',
                                 'status': {'preparedStatus': 'inuse',
                                            'sip': {'status': 'inuse', 'updatedAt': 1794499000,
                                                    'wasLastInInuseAt': '1794499000'}}}},
        '222': {'employeeID': '222', 'presenceState': 'inactive', 'presenceStateUpdatedAt': '0',
                'endpointData': {'internalNumber': '929',
                                 'status': {'sip': {'status': 'offline', 'updatedAt': 1794400000,
                                                    'wasLastInInuseAt': 0}}}},
        '333': {'employeeID': '333', 'presenceState': 'active', 'endpointData': ''},
    }}

    def test_only_line_state_leaves_the_response(self):
        lines = binotel.parse_lines(self.PAYLOAD)
        self.assertEqual(set(lines), {'915', '929'})
        self.assertEqual(lines['915'], {'presence': 'active', 'presence_at': 1794490000, 'sip': 'inuse',
                                        'sip_at': 1794499000, 'inuse_at': 1794499000, 'employee_id': '111'})
        self.assertEqual(lines['929']['sip'], 'offline')
        self.assertIsNone(lines['929']['presence_at'])
        self.assertIsNone(lines['929']['inuse_at'])
        self.assertNotIn('not-a-secret', repr(lines))
        self.assertNotIn('sip-fixture-915', repr(lines))

    def test_read_lines_asks_the_light_endpoint_without_waiting_in_line(self):
        calls = []

        class Client(object):
            def _post(self, endpoint, params, max_wait=None, timeout=None):
                calls.append((endpoint, params, max_wait))
                return ReadLinesTests.PAYLOAD

        self.assertEqual(set(binotel.read_lines(Client())), {'915', '929'})
        self.assertEqual(calls, [('settings/list-of-employees', {}, binotel.READ_MAX_WAIT)])


class _Response(object):
    def __init__(self, payload=None, text='', url='https://my.binotel.kz/f/pbx/', status=200):
        self._payload = payload
        self.text = text
        self.url = url
        self.status_code = status

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


class _Cabinet(object):
    """Подставной кабинет: помнит запросы и отдаёт карточку сотрудника."""

    def __init__(self, presence='inactive', expire_once=False, save_status='success',
                 bad_password=False):
        self.headers = {}
        self.presence = presence
        self.expire_once = expire_once
        self.save_status = save_status
        self.bad_password = bad_password
        self.requests = []

    def post(self, url, data=None, files=None, timeout=None, allow_redirects=True):
        self.requests.append(('POST', url, data, files))
        if data is not None:
            if self.bad_password:
                return _Response(text='', url='https://my.binotel.kz/?action=badPassword&email=x')
            return _Response(text='<html>кабинет</html>')
        fields = dict((name, value[1]) for name, value in files)
        if self.save_status == 'success':
            self.presence = {0: 'active', 1: 'work in crm', 3: 'break in work',
                             4: 'inactive'}[int(fields['presenceState'])]
        return _Response({'status': self.save_status})

    def get(self, url, timeout=None):
        self.requests.append(('GET', url, None, None))
        if self.expire_once:
            self.expire_once = False
            return _Response(None, text='<form>logining[email]</form>')
        record = {'employeeID': '480431', 'email': 'Alia@Example.kz', 'name': 'Выдуманная Алия',
                  'extID': '702662', 'extNumber': '915', 'language': 'ru', 'mobilePhoneNumber': '',
                  'wirePbxNumber': None, 'presenceState': self.presence}
        other = {'employeeID': '1', 'email': 'other@example.kz', 'presenceState': 'active'}
        return _Response({'listOfEmployeesWorkingPresenceStates': {'1': other},
                          'listOfEmployeesBreakPresenceStates': [],
                          'listOfEmployeesInactivePresenceStates': {'480431': record}})


class CabinetPresenceTests(unittest.TestCase):
    def cabinet(self, **kwargs):
        fake = _Cabinet(**kwargs)
        return fake, binotel.CabinetPresence('https://my.binotel.kz/', 'alia@example.kz', 'pw', session=fake)

    def test_write_repeats_the_phone(self):
        fake, cabinet = self.cabinet(presence='inactive')
        self.assertTrue(cabinet.set('active'))
        kinds = [(method, url) for method, url, _, _ in fake.requests]
        self.assertEqual(kinds, [
            ('POST', 'https://my.binotel.kz/'),
            ('GET', 'https://my.binotel.kz/main/?module=ajax&action=listOfEmployeesForBce&mbav=1'),
            ('POST', 'https://my.binotel.kz/main/?module=users&action=add&userID=480431&mbav=1'),
        ])
        self.assertEqual(fake.requests[0][2], {'logining[email]': 'alia@example.kz', 'logining[password]': 'pw'})
        form = [(name, value[1]) for name, value in fake.requests[2][3]]
        self.assertEqual(form, [
            ('email', 'Alia@Example.kz'), ('password', ''), ('name', 'Выдуманная Алия'),
            ('extID', '702662'), ('language', 'ru'), ('mobilePhoneNumber', ''), ('wirePbxNumber', ''),
            ('employeeID', '480431'), ('presenceState', '0')])
        # Имя файла у полей пустое: это поля формы, а не вложения.
        self.assertTrue(all(value[0] is None for _, value in fake.requests[2][3]))
        self.assertEqual(fake.headers['X-Requested-With'], 'XMLHttpRequest')
        self.assertEqual(cabinet.current(), 'active')

    def test_same_presence_is_not_written(self):
        fake, cabinet = self.cabinet(presence='inactive')
        self.assertFalse(cabinet.set('inactive'))
        self.assertEqual([m for m, _, _, _ in fake.requests], ['POST', 'GET'])

    def test_expired_session_logs_in_again(self):
        fake, cabinet = self.cabinet(presence='active', expire_once=True)
        self.assertTrue(cabinet.set('inactive'))
        self.assertEqual([m for m, _, _, _ in fake.requests], ['POST', 'GET', 'POST', 'GET', 'POST'])
        self.assertEqual(fake.presence, 'inactive')

    def test_refusals_are_errors(self):
        # Непринятый пароль — отдельный вид отказа: по нему сторож не повторяет вход часами.
        _, cabinet = self.cabinet(bad_password=True)
        with self.assertRaises(binotel.CabinetDenied):
            cabinet.set('active')
        # Неподтверждённая запись — обычная ошибка, её повторяют.
        _, cabinet = self.cabinet(presence='inactive', save_status='error')
        with self.assertRaises(binotel.CabinetError) as caught:
            cabinet.set('active')
        self.assertNotIsInstance(caught.exception, binotel.CabinetDenied)
        _, cabinet = self.cabinet()
        with self.assertRaises(binotel.CabinetError):
            cabinet.set('vacation')
        with self.assertRaises(binotel.CabinetError):
            binotel.CabinetPresence('https://my.binotel.kz', '', 'pw')


class _Journal(object):
    """Журнал в памяти: строки расхождений и событий звонков."""

    def __init__(self, ready=True, open_rows=()):
        self.is_ready = ready
        self.rows = {}
        self.next_id = 100
        self.events = []
        self.cleanups = 0
        # open_rows: (линия, вид) либо (линия, вид, когда чинили, итог) — строка с прошлой починкой.
        for item in open_rows:
            line, kind = item[0], item[1]
            self.rows[self.next_id] = {'line': line, 'kind': kind, 'ended': None, 'touched': 0, 'heal': None,
                                       'heal_at': item[2] if len(item) > 2 else None,
                                       'heal_result': item[3] if len(item) > 3 else None}
            self.next_id += 1

    def ready(self):
        return self.is_ready

    def load_open(self):
        return [(i, r['line'], r['kind'], r.get('heal_at'), r.get('heal_result'))
                for i, r in self.rows.items() if r['ended'] is None]

    def open(self, kind, line, snap, details, started, now):
        self.rows[self.next_id] = {'line': line, 'kind': kind, 'ended': None, 'touched': 0, 'heal': None,
                                   'snap': dict(snap), 'details': dict(details), 'started': started}
        self.next_id += 1
        return self.next_id - 1

    def touch(self, row_id, snap, details, now):
        self.rows[row_id]['touched'] += 1

    def close(self, row_id, now):
        self.rows[row_id]['ended'] = now

    def heal(self, row_id, action, result, now):
        self.rows[row_id]['heal'] = (action, result)

    def add_events(self, general_call_id, events):
        seen = {(c, e['kind'], e['line']) for c, e in self.events}
        new = [e for e in events if (general_call_id, e['kind'], e['line']) not in seen]
        self.events.extend((general_call_id, e) for e in new)
        return len(new)

    def cleanup(self):
        self.cleanups += 1


class _World(object):
    """Состав, линии Binotel и события телефонов — всё, что читает сторож."""

    def __init__(self):
        self.now = float(NOW)
        self.people = [
            {'id': 1, 'sip_number': '915', 'direction': 'tp'},
            {'id': 2, 'sip_number': '904', 'direction': 'tp'},
            {'id': 3, 'sip_number': '929', 'direction': 'op'},
            {'id': 4, 'sip_number': '923', 'direction': 'op'},
            {'id': 5, 'sip_number': None, 'direction': 'tp'},
        ]
        self.lines = {}
        self.phones = {}          # id -> [(момент, ключ)]
        self.line_reads = 0
        self.status_calls = []
        self.accounts = {1: {'cabinet_url': 'https://my.binotel.kz', 'cabinet_login': 'a@example.kz',
                             'cabinet_password': 'pw'},
                         3: {'cabinet_url': 'https://my.binotel.kz', 'cabinet_login': 'c@example.kz',
                             'cabinet_password': 'pw'}}
        self.writes = []
        self.queue_page = {'915': 'телефон офлайн', '904': 'в ожидании'}

    def line(self, number, presence, presence_ago, sip='online', sip_ago=1000, inuse_ago=None):
        self.lines[number] = {'presence': presence, 'presence_at': int(self.now - presence_ago),
                              'sip': sip, 'sip_at': int(self.now - sip_ago),
                              'inuse_at': int(self.now - inuse_ago) if inuse_ago is not None else None,
                              'employee_id': 'e' + number}

    def phone(self, operator_id, key, ago):
        self.phones.setdefault(operator_id, []).append((self.now - ago, key))

    def read_lines(self):
        self.line_reads += 1
        return dict(self.lines)

    def live_statuses(self, ids, as_of=None):
        moment = as_of.timestamp() if as_of is not None else self.now
        self.status_calls.append((tuple(ids), as_of is not None))
        out = {}
        for operator_id in ids:
            past = [(at, key) for at, key in self.phones.get(operator_id, []) if at <= moment]
            if past:
                at, key = max(past)
                out[operator_id] = {'status_key': key, 'seconds': int(moment - at)}
        return out

    def presence_factory(self, url, login, password):
        world = self

        class Writer(object):
            def set(self, presence):
                world.writes.append((login, presence))
                if login == 'c@example.kz' and world.deny_writes:
                    raise binotel.CabinetDenied('кабинет Binotel не принял пароль сотрудника')
                if login == 'c@example.kz' and world.fail_writes:
                    raise binotel.CabinetError('кабинет не подтвердил')
                return True

        return Writer()

    fail_writes = False
    deny_writes = False

    def service(self, journal=None, heal_kinds=()):
        self.journal = journal or _Journal()
        return LineWatchService(
            None, roster=lambda: self.people, read_lines=self.read_lines, live_statuses=self.live_statuses,
            account_for=self.accounts.get, queue_states=lambda: self.queue_page, heal_kinds=heal_kinds,
            now=lambda: self.now, presence_factory=self.presence_factory, journal=self.journal)


class TickTests(unittest.TestCase):
    def test_opens_once_touches_and_closes(self):
        world = _World()
        world.line('915', 'inactive', 4000)
        world.phone(1, 'готов', 600)
        world.line('904', 'active', 5000)
        world.phone(2, 'готов', 700)
        service = world.service()

        found = service.tick()
        self.assertEqual(list(found), [('915', rules.KIND_CABINET_CLOSED)])
        row = world.journal.rows[100]
        self.assertEqual((row['line'], row['kind'], row['snap']['operator_id'], row['snap']['direction']),
                         ('915', rules.KIND_CABINET_CLOSED, 1, 'tp'))
        # Началось, когда оператор стал «готов» — позже, чем кабинет стал «Неактивен».
        self.assertEqual(row['started'], NOW - 600)

        world.now += 60
        service.tick()
        self.assertEqual(len(world.journal.rows), 1)
        self.assertEqual(row['touched'], 0)
        world.now += TOUCH_SECONDS
        service.tick()
        self.assertEqual(row['touched'], 1)

        world.line('915', 'active', 5)                       # оператор переключил статус сам
        world.now += 60
        self.assertEqual(service.tick(), {})
        self.assertEqual(row['ended'], world.now)

    def test_new_process_continues_open_case_and_closes_gone_one(self):
        world = _World()
        world.line('929', 'active', 90000, sip='offline')
        world.phone(3, 'выключен', 80000)
        journal = _Journal(open_rows=[('929', rules.KIND_CABINET_STALE), ('904', rules.KIND_CABINET_CLOSED)])
        service = world.service(journal)
        service.tick()
        self.assertEqual(len(journal.rows), 2)
        self.assertIsNone(journal.rows[100]['ended'])
        self.assertEqual(journal.rows[100]['touched'], 1)
        self.assertEqual(journal.rows[101]['ended'], world.now)

    def test_line_without_working_employee(self):
        world = _World()
        world.lines['928'] = {'presence': 'active', 'presence_at': None, 'sip': 'offline',
                              'sip_at': int(world.now - 9000), 'inuse_at': None, 'employee_id': 'e928'}
        service = world.service()
        self.assertEqual(list(service.tick()), [('928', rules.KIND_CABINET_STALE)])
        row = world.journal.rows[100]
        self.assertIsNone(row['snap']['operator_id'])
        self.assertNotIn('would_set', row['details'])         # учётки нет — чинить некем

    def test_journal_without_table_keeps_binotel_quiet(self):
        world = _World()
        service = world.service(_Journal(ready=False))
        self.assertIsNone(service.tick())
        self.assertEqual(world.line_reads, 0)

    def test_plan_is_written_even_when_healing_is_off(self):
        world = _World()
        world.line('929', 'active', 90000, sip='offline')
        world.phone(3, 'выключен', 80000)
        service = world.service()
        service.tick()
        self.assertEqual(world.journal.rows[100]['details']['would_set'], 'inactive')
        self.assertEqual(world.writes, [])
        self.assertIsNone(world.journal.rows[100]['heal'])


class HealTests(unittest.TestCase):
    def test_heals_only_named_kinds(self):
        world = _World()
        world.line('929', 'active', 90000, sip='offline')
        world.phone(3, 'выключен', 80000)
        world.line('915', 'inactive', 4000, inuse_ago=100)
        world.phone(1, 'готов', 600)
        service = world.service(heal_kinds=[rules.KIND_CABINET_STALE, 'queue_held', 'nonsense'])
        self.assertEqual(service.heal_kinds, frozenset([rules.KIND_CABINET_STALE]))
        service.tick()
        self.assertEqual(world.writes, [('c@example.kz', 'inactive')])
        healed = [r for r in world.journal.rows.values() if r['heal']]
        self.assertEqual([(r['line'], r['heal']) for r in healed], [('929', ('set:inactive', 'записано'))])

    def test_retries_are_rare_and_limited(self):
        world = _World()
        world.fail_writes = True
        world.line('929', 'active', 90000, sip='offline')
        world.phone(3, 'выключен', 80000)
        service = world.service(heal_kinds=[rules.KIND_CABINET_STALE])
        service.tick()
        self.assertEqual(world.journal.rows[100]['heal'], ('set:inactive', 'не вышло: кабинет не подтвердил'))
        world.now += 60
        service.tick()
        self.assertEqual(len(world.writes), 1)
        for _ in range(6):
            world.now += HEAL_RETRY_SECONDS
            service.tick()
        self.assertEqual(len(world.writes), HEAL_MAX_TRIES)

    def test_employee_without_cabinet_account(self):
        world = _World()
        world.line('904', 'active', 90000, sip='offline')
        world.phone(2, 'выключен', 80000)
        service = world.service(heal_kinds=[rules.KIND_CABINET_STALE])
        service.tick()
        self.assertEqual(world.writes, [])
        self.assertEqual(world.journal.rows[100]['heal'],
                         ('set:inactive', 'отказ: учётки кабинета у сотрудника нет'))

    def test_rejected_password_is_not_hammered(self):
        """Кабинет не принял пароль сотрудника: вторая попытка с теми же данными — это ещё один
        неудачный вход в чужой кабинет. Следующая — не раньше чем через шесть часов."""
        world = _World()
        world.deny_writes = True
        world.line('929', 'active', 90000, sip='offline')
        world.phone(3, 'выключен', 80000)
        service = world.service(heal_kinds=[rules.KIND_CABINET_STALE])
        service.tick()
        self.assertEqual(world.journal.rows[100]['heal'],
                         ('set:inactive', 'отказ: кабинет Binotel не принял пароль сотрудника'))
        for _ in range(8):
            world.now += HEAL_RETRY_SECONDS
            service.tick()
        self.assertEqual(len(world.writes), 1)
        world.now += HEAL_DENIED_RETRY_SECONDS
        service.tick()
        self.assertEqual(len(world.writes), 2)

    def test_restart_does_not_forget_a_rejection(self):
        """Сервер перезапускается при каждом деплое — по нескольку раз в день. Момент отказа
        лежит в журнале, и новый процесс ждёт остаток срока, а не входит в кабинет заново."""
        world = _World()
        world.deny_writes = True
        world.line('929', 'active', 90000, sip='offline')
        world.phone(3, 'выключен', 80000)
        journal = _Journal(open_rows=[('929', rules.KIND_CABINET_STALE, world.now - 3600,
                                       'отказ: кабинет Binotel не принял пароль сотрудника')])
        service = world.service(journal, heal_kinds=[rules.KIND_CABINET_STALE])
        service.tick()
        self.assertEqual(world.writes, [])
        world.now += HEAL_DENIED_RETRY_SECONDS - 3600 - 120
        service.tick()
        self.assertEqual(world.writes, [])
        world.now += 180
        service.tick()
        self.assertEqual(len(world.writes), 1)

    def test_restart_retries_a_plain_failure_and_old_journals_still_load(self):
        """«Не вышло» (сеть, кабинет не подтвердил) — не отказ: новый процесс пробует сразу.
        Журнал прежнего вида, без момента и итога починки, читается как раньше."""
        world = _World()
        world.line('929', 'active', 90000, sip='offline')
        world.phone(3, 'выключен', 80000)
        journal = _Journal(open_rows=[('929', rules.KIND_CABINET_STALE, world.now - 60,
                                       'не вышло: кабинет не подтвердил')])
        service = world.service(journal, heal_kinds=[rules.KIND_CABINET_STALE])
        service.tick()
        self.assertEqual(world.writes, [('c@example.kz', 'inactive')])

        world = _World()
        world.line('929', 'active', 90000, sip='offline')
        world.phone(3, 'выключен', 80000)
        journal = _Journal(open_rows=[('929', rules.KIND_CABINET_STALE)])
        journal.load_open = lambda: [(100, '929', rules.KIND_CABINET_STALE)]
        service = world.service(journal, heal_kinds=[rules.KIND_CABINET_STALE])
        service.tick()
        self.assertEqual(world.writes, [('c@example.kz', 'inactive')])


class CallEventTests(unittest.TestCase):
    def ready_world(self):
        world = _World()
        world.line('915', 'active', 3000)
        world.line('904', 'active', 3000)
        world.line('929', 'active', 3000, sip='offline')
        world.line('923', 'active', 3000)
        world.phone(1, 'готов', 1500)
        world.phone(2, 'готов', 1500)
        world.phone(4, 'готов', 1500)
        world.phone(3, 'выключен', 90000)
        return world

    def feed(self, world, service, fields):
        accepted = service.on_binotel_call(fields)
        world.now += CALL_SETTLE_SECONDS + 1
        return accepted, service.drain_calls()

    def test_webhook_only_queues_the_call(self):
        world = self.ready_world()
        service = world.service()
        start = int(world.now) - 130
        self.assertTrue(service.on_binotel_call(webhook(701, start, 'NOANSWER', [('Очередь', 120, 0, 'NOANSWER')])))
        self.assertEqual((world.line_reads, world.status_calls, world.journal.events), (0, [], []))
        self.assertEqual(service.drain_calls(), 0)             # ещё не отстоялся
        world.now += CALL_SETTLE_SECONDS + 1
        self.assertEqual(service.drain_calls(), 2)

    def test_queue_held_while_operators_were_ready(self):
        world = self.ready_world()
        service = world.service()
        start = int(world.now) - 130
        _, saved = self.feed(world, service, webhook(702, start, 'NOANSWER', [('Очередь', 120, 0, 'NOANSWER')]))
        self.assertEqual(saved, 2)
        events = {e['line']: e for _, e in world.journal.events}
        self.assertEqual(set(events), {'915', '904'})                    # ОП в очереди не стоит
        event = events['915']
        self.assertEqual((event['kind'], event['operator_id'], event['start'], event['end']),
                         (rules.KIND_QUEUE_HELD, 1, start, start + 120))
        self.assertEqual(event['details']['wait'], 120)
        self.assertEqual(event['details']['queue_status'], 'телефон офлайн')
        self.assertEqual(events['904']['details']['queue_status'], 'в ожидании')
        # Повтор вебхука того же звонка ничего не добавляет.
        _, again = self.feed(world, service, webhook(702, start, 'NOANSWER', [('Очередь', 120, 0, 'NOANSWER')]))
        self.assertEqual(again, 0)

    def test_operator_busy_or_closed_during_the_wait_is_not_reported(self):
        world = self.ready_world()
        world.phone(1, 'занят', 100)                 # говорил, пока клиент ждал
        world.line('904', 'work in crm', 3000)       # в кабинете закрыт — очередь и не должна звонить
        service = world.service()
        start = int(world.now) - 130
        accepted, saved = self.feed(world, service,
                                    webhook(703, start, 'CANCEL', [('Очередь', 95, 0, 'CANCEL')]))
        self.assertTrue(accepted)
        self.assertEqual(saved, 0)

    def test_presence_changed_during_the_wait_is_not_reported(self):
        world = self.ready_world()
        world.line('915', 'active', 60)              # вернулся в «Активен» уже во время ожидания
        world.line('904', 'inactive', 3000)
        service = world.service()
        start = int(world.now) - 130
        _, saved = self.feed(world, service, webhook(704, start, 'NOANSWER', [('Очередь', 120, 0, 'NOANSWER')]))
        self.assertEqual(saved, 0)

    def test_no_pickup_and_short_waits(self):
        world = self.ready_world()
        service = world.service()
        start = int(world.now) - 40
        _, saved = self.feed(world, service, webhook(705, start, 'ANSWER', [
            ('915', 16, 0, 'BUSY'), ('904', 2, 20, 'ANSWER')], billsec=20))
        self.assertEqual(saved, 1)
        event = world.journal.events[0][1]
        self.assertEqual((event['kind'], event['line'], event['details']['ring'], event['details']['leg_disposition']),
                         (rules.KIND_NO_PICKUP, '915', 16, 'BUSY'))
        # Обычный звонок: три секунды до ответа, без очереди — вебхук даже не ставится в разбор.
        self.assertIsNone(service.on_binotel_call(webhook(706, start, 'ANSWER', [('904', 3, 50, 'ANSWER')],
                                                          billsec=50)))
        self.assertIsNone(service.on_binotel_call(webhook(707, start, 'ANSWER', [
            ('Очередь', 8, 0, 'NOANSWER'), ('904', 2, 50, 'ANSWER')], billsec=50)))

    def test_group_drop_with_ready_sales_operator(self):
        world = self.ready_world()
        service = world.service()
        start = int(world.now) - 20
        _, saved = self.feed(world, service, webhook(708, start, 'CHANUNAVAIL', [('802', 0, 0, 'CHANUNAVAIL')]))
        self.assertEqual(saved, 1)
        event = world.journal.events[0][1]
        self.assertEqual((event['kind'], event['line'], event['operator_id'], event['direction']),
                         (rules.KIND_GROUP_DROP, '802', None, 'op'))
        self.assertEqual(event['details']['ready'], [{'line': '923', 'operator_id': 4, 'sip': 'online'}])

    def test_group_drop_with_nobody_ready_is_silent(self):
        world = self.ready_world()
        world.phones[4] = [(world.now - 5000, 'перезвон')]
        service = world.service()
        start = int(world.now) - 20
        _, saved = self.feed(world, service, webhook(709, start, 'CHANUNAVAIL', [('802', 0, 0, 'CHANUNAVAIL')]))
        self.assertEqual(saved, 0)

    def test_dial_list_attempts_and_outgoing_are_ignored(self):
        world = self.ready_world()
        service = world.service()
        start = int(world.now) - 130
        body = webhook(710, start, 'NOANSWER', [('Очередь', 120, 0, 'NOANSWER')])
        self.assertIsNone(service.on_binotel_call(body, matched=True))
        self.assertIsNone(service.on_binotel_call(webhook(711, start, 'CANCEL', [('', 25, 0, 'CANCEL')],
                                                          call_type='1')))
        self.assertIsNone(service.on_binotel_call({'requestType': 'apiCallCompleted'}))


if __name__ == '__main__':
    unittest.main()
