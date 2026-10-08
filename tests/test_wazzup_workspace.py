"""Рабочее место верификатора в «Чатах ОП»: статусы смены, сторож и подтверждение доступа.

Ручки работают на хранилище в памяти (tests/wazzup_workspace_memory.py), поэтому
тесты идут без базы. Правило «кто вправе подтверждать» исполняется настоящее —
`_sensitive_access_approval_error` из bot_schedule2.py.
"""
import ast
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask

from tests import source_cache
from tests.wazzup_workspace_memory import MemoryWorkspaceStore
from wazzup import access
from wazzup import shift
from wazzup import workspace_routes

ROOT = Path(__file__).resolve().parents[1]
BOT_SOURCE = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')
DATABASE_SOURCE = (ROOT / 'database.py').read_text(encoding='utf-8-sig')

NOW = datetime(2026, 10, 8, 14, 0, 0)                       # Алматы, как event_at статусов
UTC = datetime(2026, 10, 8, 9, 0, 0, tzinfo=timezone.utc)   # часы кода и QR
SECRET = 'unit-test-secret'
SESSION = '11111111-2222-3333-4444-555555555555'


def _bot_names(names, namespace):
    wanted = set(names)
    for node in source_cache.parse(BOT_SOURCE).body:
        targets = []
        if isinstance(node, ast.FunctionDef):
            targets = [node.name]
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if wanted & set(targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot_schedule2.py', 'exec'),
                 namespace)
            wanted -= set(targets)
    assert not wanted, sorted(wanted)
    return namespace


def _hours_profile():
    """Настоящие правила учёта часов из database.py: профиль статусов модели и
    признак «тех причины»."""
    module = source_cache.parse(DATABASE_SOURCE)
    namespace = {'re': re}
    prefixes = ('SCHEDULE_AUTO_', 'CHAT_MANAGER_', 'TEZ_', 'CALCULATION_MODEL_')
    for node in module.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if names and all(name.startswith(prefixes) for name in names):
                try:
                    exec(compile(ast.Module(body=[node], type_ignores=[]), 'database.py', 'exec'),
                         namespace)
                except Exception:
                    pass
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'Database')
    wanted = {'_status_profile_for_calculation_model', '_schedule_auto_is_tech_reason_status_key',
              '_schedule_auto_compact_status_key', '_schedule_auto_is_late_excused_status_key',
              '_normalize_import_status_key'}
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in methods} == wanted
    holder = ast.ClassDef(name='Profile', bases=[], keywords=[], body=methods, decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[holder], type_ignores=[])),
                 'database.py', 'exec'), namespace)
    profile_cls = namespace['Profile']
    profile_cls._normalize_calculation_model_code = (
        lambda self, code=None, direction_name=None: str(code or '').strip().lower() or 'operator')
    return profile_cls()


class StatusCatalogTests(unittest.TestCase):
    """«Стандартные» статусы — те же слова, что у iCORE Phone: учёт часов и табло
    читают их без единого нового правила."""

    def test_labels_and_order_are_the_owners(self):
        self.assertEqual([item['label'] for item in shift.STATUSES],
                         ['Активный', 'Перерыв', 'Тренинг', 'Тех. пауза'])
        self.assertEqual(shift.START_KEY, shift.STATUSES[0]['key'])

    def test_hours_accounting_reads_every_status(self):
        rules = _hours_profile()
        profile = rules._status_profile_for_calculation_model('op_verificator')
        by_label = {item['label']: item['key'] for item in shift.STATUSES}
        self.assertIn(by_label['Активный'], profile['work'])
        self.assertIn(by_label['Активный'], profile['late_start'])
        self.assertIn(by_label['Перерыв'], profile['break'])
        self.assertIn(by_label['Тренинг'], profile['training'])
        self.assertTrue(rules._schedule_auto_is_tech_reason_status_key(by_label['Тех. пауза']))
        self.assertTrue(rules._schedule_auto_is_late_excused_status_key(by_label['Тренинг']))
        self.assertTrue(rules._schedule_auto_is_late_excused_status_key(by_label['Тех. пауза']))
        # Конец смены в часы не идёт ни работой, ни перерывом.
        for bucket in ('work', 'talk', 'break', 'training', 'late_start'):
            self.assertNotIn(shift.LOGOUT_KEY, profile[bucket], bucket)
        self.assertFalse(rules._schedule_auto_is_tech_reason_status_key(shift.LOGOUT_KEY))

    def test_wallboards_know_the_same_words(self):
        catalog = _bot_names(('_TEZ_WALLBOARD_STATUS_CATALOG',), {})['_TEZ_WALLBOARD_STATUS_CATALOG']
        tones = {'Активный': 'free', 'Перерыв': 'break', 'Тренинг': 'training', 'Тех. пауза': 'tech'}
        for item in shift.STATUSES:
            self.assertEqual(catalog[item['key']][1], tones[item['label']], item)
        self.assertEqual(catalog[shift.LOGOUT_KEY][1], 'offline')
        self.assertEqual(catalog[shift.START_KEY][0], 'Активный')

    def test_client_event_id_is_marked_and_sanitized(self):
        self.assertEqual(shift.client_event_id('ab-12"; drop'), 'wzws-ab-12drop')
        self.assertTrue(len(shift.client_event_id('x' * 200)) <= 64)
        for empty in ('', None, '!!!'):
            with self.assertRaises(ValueError):
                shift.client_event_id(empty)


class CurrentStatusTests(unittest.TestCase):
    def test_no_events_is_off_shift(self):
        self.assertFalse(shift.current_status(None, NOW)['onShift'])

    def test_workspace_status(self):
        event = {'status_key': 'перерыв', 'event_at': NOW - timedelta(minutes=5),
                 'client_event_id': 'wzws-1', 'state_note': None}
        state = shift.current_status(event, NOW)
        self.assertEqual((state['label'], state['tone'], state['onShift'], state['elapsedSeconds']),
                         ('Перерыв', 'break', True, 300))

    def test_logout_is_off_shift(self):
        state = shift.current_status({'status_key': 'Выключен', 'event_at': NOW}, NOW)
        self.assertEqual((state['onShift'], state['tone'], state['auto']), (False, 'off', False))

    def test_auto_logout_is_marked(self):
        event = {'status_key': shift.LOGOUT_KEY, 'event_at': NOW, 'client_event_id': 'wzws-auto-5-x',
                 'state_note': shift.AUTO_LOGOUT_NOTE}
        self.assertTrue(shift.current_status(event, NOW)['auto'])
        self.assertFalse(shift.current_status(dict(event, state_note=None), NOW)['auto'])
        # Чужой выход с той же заметкой — не наш.
        self.assertFalse(shift.current_status(dict(event, client_event_id='phone-1'), NOW)['auto'])

    def test_phone_status_is_shown_as_is(self):
        state = shift.current_status({'status_key': 'исход', 'event_at': NOW}, NOW)
        self.assertEqual((state['label'], state['onShift'], state['tone']), ('Исход', True, 'other'))


class AutoLogoutTests(unittest.TestCase):
    def _latest(self, key='готов', minutes_ago=30, client='wzws-abc'):
        return {'status_key': key, 'event_at': NOW - timedelta(minutes=minutes_ago),
                'client_event_id': client}

    def test_silent_portal_closes_at_last_seen(self):
        last_seen = NOW - timedelta(minutes=11)
        self.assertEqual(shift.auto_logout(last_seen, self._latest(), NOW),
                         (last_seen, shift.AUTO_LOGOUT_NOTE))

    def test_fresh_portal_is_not_closed(self):
        self.assertIsNone(shift.auto_logout(NOW - timedelta(minutes=9), self._latest(), NOW))

    def test_pause_statuses_are_closed_too(self):
        for key in ('перерыв', 'тренинг', 'тех причина'):
            self.assertIsNotNone(
                shift.auto_logout(NOW - timedelta(minutes=11), self._latest(key=key), NOW), key)

    def test_phone_status_is_not_ours_to_close(self):
        latest = self._latest(client='a1b2c3-phone', minutes_ago=60 * 20)
        self.assertIsNone(shift.auto_logout(NOW - timedelta(hours=1), latest, NOW))

    def test_closed_shift_stays_closed(self):
        self.assertIsNone(shift.auto_logout(NOW - timedelta(hours=1),
                                            self._latest(key=shift.LOGOUT_KEY), NOW))

    def test_logout_never_precedes_the_status_it_closes(self):
        latest = self._latest(minutes_ago=20)
        self.assertEqual(shift.auto_logout(NOW - timedelta(minutes=40), latest, NOW)[0],
                         latest['event_at'])

    def test_fresh_status_after_old_presence_is_not_closed(self):
        """Смена начата после вчерашней отметки: статус сам доказывает, что портал жив.
        Иначе проход сторожа между записью статуса и отметкой обнулил бы начало смены."""
        self.assertIsNone(shift.auto_logout(NOW - timedelta(hours=15),
                                            self._latest(minutes_ago=0), NOW))

    def test_open_browser_does_not_stretch_a_status_past_the_cap(self):
        """Компьютер оставлен включённым: отметки идут, а статус 17 часов не менялся."""
        latest = self._latest(minutes_ago=17 * 60)
        moment, note = shift.auto_logout(NOW - timedelta(minutes=1), latest, NOW)
        self.assertEqual(moment, latest['event_at'] + timedelta(hours=shift.MAX_OPEN_STATUS_HOURS))
        self.assertEqual(note, shift.LONG_SHIFT_NOTE)

    def test_long_silence_closes_at_the_cap_not_later(self):
        latest = self._latest(minutes_ago=30 * 60)
        moment, _ = shift.auto_logout(NOW - timedelta(hours=2), latest, NOW)
        self.assertEqual(moment, latest['event_at'] + timedelta(hours=shift.MAX_OPEN_STATUS_HOURS))

    def test_without_presence_only_the_cap_applies(self):
        self.assertIsNone(shift.auto_logout(None, self._latest(minutes_ago=60), NOW))
        self.assertIsNotNone(shift.auto_logout(None, self._latest(minutes_ago=17 * 60), NOW))


# ── ручки ────────────────────────────────────────────────────────────────────

VERIFIER, OTHER_VERIFIER, SV, HEAD, ADMIN, SUPER, SZOV_SV, LINE_OPERATOR = 10, 11, 20, 30, 40, 50, 60, 70
OP, SZOV = 367, 501


def _user(user_id, name, role, supervisor_id=None, telegram_id=None):
    row = [None] * 20
    row[0], row[1], row[2], row[3], row[4], row[6], row[7] = (
        user_id, telegram_id, name, role, 'Верификатор', supervisor_id, f'login{user_id}')
    return tuple(row)


USERS = {
    VERIFIER: _user(VERIFIER, 'Сарсеке Мерей', 'operator', supervisor_id=SV),
    OTHER_VERIFIER: _user(OTHER_VERIFIER, 'Нуржан Перизат', 'operator', supervisor_id=SV),
    SV: _user(SV, 'Мухтар Адилет', 'sv'),
    HEAD: _user(HEAD, 'Глава Продаж', 'admin', telegram_id=7001),
    ADMIN: _user(ADMIN, 'Админ Портала', 'admin'),
    SUPER: _user(SUPER, 'Супер Админ', 'super_admin'),
    SZOV_SV: _user(SZOV_SV, 'СВ Заботы', 'sv'),
    LINE_OPERATOR: _user(LINE_OPERATOR, 'Оператор Основы', 'operator', supervisor_id=SV),
}
DEPARTMENTS = {VERIFIER: OP, OTHER_VERIFIER: OP, SV: OP, HEAD: OP, SZOV_SV: SZOV, LINE_OPERATOR: OP}
HEADED = {HEAD: [OP]}
PRIVILEGED = {'sv', 'admin', 'super_admin'}


class Telegram:
    def __init__(self):
        self.sent, self.status_code, self.raises = [], 200, False

    def send(self, chat_id, text):
        if self.raises:
            raise RuntimeError('network down')
        self.sent.append((chat_id, text))
        return type('Response', (), {'status_code': self.status_code})()

    def code(self, index=-1):
        return re.search(r'<code>(\d{6})</code>', self.sent[index][1]).group(1)


class Env:
    """Блюпринт на хранилище в памяти и часах, которые двигает тест."""

    def __init__(self):
        self.store = MemoryWorkspaceStore()
        self.store.verifiers = {VERIFIER, OTHER_VERIFIER}
        self.store.live_sessions = {(SESSION, VERIFIER)}
        self.store.recipients = {uid: {'id': HEAD, 'name': 'Глава Продаж', 'telegram_id': 7001,
                                       'department_name': 'Отдел продаж'}
                                 for uid in (VERIFIER, OTHER_VERIFIER, LINE_OPERATOR)}
        self.telegram = Telegram()
        self.now, self.clock = NOW, UTC
        self.me = VERIFIER
        self.session = SESSION
        self.codes = iter(f'{n:06d}' for n in range(111111, 999999, 111111))
        perimeter = _bot_names(('_sensitive_access_approval_error',), {
            '_normalize_user_role': lambda role: str(role or '').strip().lower()})

        class _Db:
            @staticmethod
            def get_user(id=None):
                return USERS.get(id)

            @staticmethod
            def get_user_department_id(user_id):
                return DEPARTMENTS.get(user_id)

        def chat_access():
            if self.me is None:
                return {'user_id': None, 'mode': None, 'locked': False, 'can_process': False}
            role = USERS[self.me][3]
            if role in PRIVILEGED:
                return {'user_id': self.me, 'mode': 'full', 'locked': False,
                        'can_process': role == 'super_admin'}
            state = self.store.operator_state(self.me, self.session, None, OP)
            if state['verifier']:
                return {'user_id': self.me, 'mode': 'operator', 'locked': not state['unlocked'],
                        'can_process': state['unlocked']}
            return {'user_id': self.me, 'mode': None, 'locked': False, 'can_process': False}

        def approver_context(approver_id):
            approver = USERS.get(approver_id)
            headed = HEADED.get(approver_id, [])
            if not approver or not (approver[3] in PRIVILEGED or headed):
                return None, ('Подтвердить доступ может администратор, супервайзер или глава отдела', 403)
            return {'approver': approver, 'headed_department_ids': headed,
                    'department_id': DEPARTMENTS.get(approver_id)}, None

        app = Flask(__name__)
        app.testing = True
        self.blueprint = workspace_routes.build_wazzup_workspace_blueprint(
            db=_Db(), require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            chat_access=chat_access, current_session_id=lambda: self.session,
            approver_context=approver_context,
            approval_perimeter_error=perimeter['_sensitive_access_approval_error'],
            send_telegram=self.telegram.send, secret=SECRET, sales_department_id=OP,
            role_label=lambda role: {'sv': 'Супервайзер', 'admin': 'Администратор'}.get(role, ''),
            store=self.store, now=lambda: self.now, clock=lambda: self.clock,
            new_code=lambda: next(self.codes))
        app.register_blueprint(self.blueprint)
        self.client = app.test_client()

    def as_user(self, user_id, session=SESSION):
        self.me, self.session = user_id, session
        return self

    def post(self, path, body=None):
        return self.client.post('/api/wazzup/workspace' + path, json=body or {})

    def state(self):
        return self.client.get('/api/wazzup/workspace')

    def qr(self):
        return self.as_user(VERIFIER).post('/qr').get_json()['qr_payload']

    def scan(self, approver=SV, payload=None):
        payload = payload or self.qr()
        return self.as_user(approver).post('/scan', {'token': payload})

    def unlock(self, approver=SV):
        challenge = self.scan(approver).get_json()['challengeId']
        response = self.as_user(approver).post('/approve', {'challengeId': challenge,
                                                            'code': self.telegram.code()})
        assert response.status_code == 200, response.get_json()
        return self.as_user(VERIFIER)


class StateRouteTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def test_locked_verifier_gets_no_shift(self):
        body = self.env.as_user(VERIFIER).state().get_json()
        self.assertEqual((body['mode'], body['locked'], body['canProcess'], body['shift']),
                         ('operator', True, False, None))

    def test_unlocked_verifier_gets_statuses_and_keys(self):
        body = self.env.unlock().state().get_json()
        self.assertEqual((body['mode'], body['locked'], body['canProcess']), ('operator', False, True))
        self.assertEqual([s['label'] for s in body['shift']['statuses']],
                         ['Активный', 'Перерыв', 'Тренинг', 'Тех. пауза'])
        self.assertEqual((body['shift']['startKey'], body['shift']['logoutKey']),
                         (shift.START_KEY, shift.LOGOUT_KEY))
        self.assertFalse(body['shift']['current']['onShift'])
        self.assertEqual(body['shift']['heartbeatSeconds'], shift.HEARTBEAT_SECONDS)

    def test_full_audience_has_no_shift_and_only_super_admin_processes(self):
        for user_id, can_process in ((SV, False), (ADMIN, False), (HEAD, False), (SUPER, True)):
            body = self.env.as_user(user_id).state().get_json()
            self.assertEqual((body['mode'], body['canProcess'], body['shift']),
                             ('full', can_process, None), user_id)

    def test_outsiders_are_refused(self):
        for user_id in (LINE_OPERATOR, None):
            self.assertEqual(self.env.as_user(user_id).state().status_code, 403, user_id)


class QrRouteTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def test_verifier_gets_a_chat_code_for_his_own_session(self):
        body = self.env.as_user(VERIFIER).post('/qr').get_json()
        self.assertTrue(body['qr_payload'].startswith(access.QR_PREFIX))
        self.assertFalse(body['granted'])
        claims = access.decode_qr_token(SECRET, body['qr_payload'], now=UTC)
        self.assertEqual((claims['session_id'], claims['user_id']), (SESSION, VERIFIER))
        self.assertEqual(body['token_expires_at'],
                         (UTC + timedelta(seconds=access.QR_TTL_SECONDS)).isoformat().replace('+00:00', 'Z'))

    def test_only_verifiers_get_a_code(self):
        for user_id in (SV, ADMIN, SUPER, LINE_OPERATOR, None):
            self.assertEqual(self.env.as_user(user_id).post('/qr').status_code, 403, user_id)

    def test_dead_session_gets_no_code(self):
        self.env.store.live_sessions.clear()
        self.assertEqual(self.env.as_user(VERIFIER).post('/qr').status_code, 401)
        self.assertEqual(self.env.as_user(VERIFIER, session=None).post('/qr').status_code, 401)


class ScanRouteTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def test_scan_sends_the_code_to_the_department_head(self):
        response = self.env.scan(SV)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual((body['scope'], body['operator_id'], body['operator_name'], body['already_granted']),
                         ('wazzup_chats', VERIFIER, 'Сарсеке Мерей', False))
        self.assertEqual((body['codeSentTo'], body['operator_department']), ('Глава Продаж', 'Отдел продаж'))
        self.assertEqual((body['attemptsLeft'], body['canResend']), (access.CODE_MAX_ATTEMPTS, True))
        self.assertEqual(body['resendInSeconds'], access.CODE_RESEND_SECONDS)
        self.assertEqual(body['codeExpiresInSeconds'], access.CODE_TTL_SECONDS)
        (chat_id, text), = self.env.telegram.sent
        self.assertEqual(chat_id, 7001)
        self.assertIn('<code>111111</code>', text)
        self.assertIn('Сарсеке Мерей', text)
        self.assertIn('Мухтар Адилет, супервайзер', text)
        # Сам код подтверждающему не возвращается — он есть только в Telegram главы.
        self.assertNotIn('111111', response.get_data(as_text=True))
        # И в базе лежит не код, а его отпечаток.
        stored = self.env.store.challenges[body['challengeId']]
        self.assertNotIn('111111', stored['code_hash'])

    def test_scan_alone_opens_nothing(self):
        self.env.scan(SV)
        self.assertTrue(self.env.as_user(VERIFIER).state().get_json()['locked'])

    def test_rescan_does_not_send_a_second_code(self):
        first = self.env.scan(SV).get_json()
        self.env.clock += timedelta(seconds=20)
        again = self.env.scan(SV).get_json()
        self.assertEqual(again['challengeId'], first['challengeId'])
        self.assertEqual(again['resendInSeconds'], access.CODE_RESEND_SECONDS - 20)
        self.assertEqual(len(self.env.telegram.sent), 1)

    def test_another_approver_gets_his_own_code(self):
        first = self.env.scan(SV).get_json()
        second = self.env.scan(HEAD).get_json()
        self.assertNotEqual(first['challengeId'], second['challengeId'])
        self.assertEqual(len(self.env.telegram.sent), 2)

    def test_who_may_confirm(self):
        """Круг тот же, что у обычного QR: свой отдел, админ без отдела, супер-админ."""
        payload = self.env.qr()
        for approver, status in ((SV, 200), (HEAD, 200), (ADMIN, 200), (SUPER, 200),
                                 (SZOV_SV, 403), (OTHER_VERIFIER, 403), (LINE_OPERATOR, 403)):
            with self.subTest(approver=approver):
                response = self.env.scan(approver, payload)
                self.assertEqual(response.status_code, status, response.get_json())
        # Отказ не называет сотрудника тому, кто открыть ему доступ не вправе.
        self.assertNotIn('Сарсеке', self.env.scan(SZOV_SV, payload).get_data(as_text=True))

    def test_bad_codes(self):
        expired = self.env.qr()
        cases = [('', 'Это не QR-код доступа к чатам'), ('OTPW:AAAA', 'Это не QR-код доступа к чатам'),
                 ('OTPQ:' + expired[5:], 'Это не QR-код доступа к чатам'),
                 (None, 'Это не QR-код доступа к чатам')]
        for token, message in cases:
            response = self.env.as_user(SV).post('/scan', {'token': token})
            self.assertEqual((response.status_code, response.get_json()['error']), (400, message), token)
        self.env.clock += timedelta(seconds=access.QR_TTL_SECONDS + 1)
        response = self.env.as_user(SV).post('/scan', {'token': expired})
        self.assertEqual((response.status_code, response.get_json()['error']),
                         (400, 'Срок действия кода истёк — попросите обновить QR'))
        self.assertEqual(self.env.telegram.sent, [])

    def test_code_of_someone_who_is_not_a_verifier(self):
        token, _ = access.build_qr_token(SECRET, SESSION, LINE_OPERATOR, now=UTC)
        self.env.store.live_sessions.add((SESSION, LINE_OPERATOR))
        response = self.env.as_user(SV).post('/scan', {'token': access.QR_PREFIX + token})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.env.telegram.sent, [])

    def test_ended_session(self):
        payload = self.env.qr()
        self.env.store.live_sessions.clear()
        self.assertEqual(self.env.scan(SV, payload).status_code, 410)

    def test_no_head_or_no_telegram_means_no_code(self):
        payload = self.env.qr()
        self.env.store.recipients[VERIFIER] = dict(self.env.store.recipients[VERIFIER], telegram_id=None)
        response = self.env.scan(SV, payload)
        self.assertEqual(response.status_code, 409)
        self.assertIn('не привязан Telegram', response.get_json()['error'])
        del self.env.store.recipients[VERIFIER]
        response = self.env.scan(SV, payload)
        self.assertEqual(response.status_code, 409)
        self.assertIn('не назначен глава', response.get_json()['error'])
        self.assertEqual(self.env.store.challenges, {})

    def test_failed_delivery_leaves_no_pending_scan(self):
        payload = self.env.qr()
        for breakage in ('status', 'raise'):
            self.env.telegram.status_code = 403 if breakage == 'status' else 200
            self.env.telegram.raises = breakage == 'raise'
            self.assertEqual(self.env.scan(SV, payload).status_code, 502, breakage)
            self.assertEqual(self.env.store.challenges, {}, breakage)
        # Следующий скан начинает заново, а не упирается в «код уже отправлен».
        self.env.telegram.status_code, self.env.telegram.raises = 200, False
        self.assertEqual(self.env.scan(SV, payload).status_code, 200)

    def test_already_confirmed_session_gets_no_new_code(self):
        self.env.unlock(SV)
        sent = len(self.env.telegram.sent)
        body = self.env.scan(SV).get_json()
        self.assertTrue(body['already_granted'])
        self.assertNotIn('challengeId', body)
        self.assertEqual(len(self.env.telegram.sent), sent)

    def test_scans_per_hour_are_capped(self):
        payload = self.env.qr()
        for _ in range(workspace_routes.SCANS_PER_HOUR):
            body = self.env.scan(SV, payload).get_json()
            # Ожидание сгорает (попытки исчерпаны) — следующий скан заводит новое.
            self.env.store.challenges[body['challengeId']]['attempts'] = access.CODE_MAX_ATTEMPTS
        response = self.env.scan(SV, payload)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(self.env.telegram.sent), workspace_routes.SCANS_PER_HOUR)


class ApproveRouteTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.challenge = self.env.scan(SV).get_json()['challengeId']
        self.code = self.env.telegram.code()

    def approve(self, code, approver=SV, challenge=None):
        return self.env.as_user(approver).post(
            '/approve', {'challengeId': challenge or self.challenge, 'code': code})

    def test_right_code_opens_the_section_for_that_session_only(self):
        response = self.approve(self.code)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['operator_name'], 'Сарсеке Мерей')
        self.assertFalse(self.env.as_user(VERIFIER).state().get_json()['locked'])
        grant = self.env.store.granted[SESSION]
        self.assertEqual((grant['user_id'], grant['granted_by'], grant['code_recipient_id']),
                         (VERIFIER, SV, HEAD))
        # Другое устройство того же человека остаётся закрытым.
        other = '99999999-2222-3333-4444-555555555555'
        self.env.store.live_sessions.add((other, VERIFIER))
        self.assertTrue(self.env.as_user(VERIFIER, session=other).state().get_json()['locked'])

    def test_code_typed_with_spaces(self):
        self.assertEqual(self.approve(f' {self.code[:3]} {self.code[3:]} ').status_code, 200)

    def test_wrong_code_spends_attempts_and_then_the_code_dies(self):
        for left in range(access.CODE_MAX_ATTEMPTS - 1, -1, -1):
            response = self.approve('000000')
            body = response.get_json()
            self.assertEqual((response.status_code, body['code'], body['attemptsLeft']),
                             (400, 'CODE_WRONG', left))
        # Попытки кончились — даже верный код больше не подходит.
        response = self.approve(self.code)
        self.assertEqual((response.status_code, response.get_json()['code']), (429, 'CODE_ATTEMPTS'))
        self.assertTrue(self.env.as_user(VERIFIER).state().get_json()['locked'])

    def test_malformed_input_spends_nothing(self):
        for code in ('', '12345', '1234567', 'abcdef', None):
            self.assertEqual(self.approve(code).status_code, 400, code)
        self.assertEqual(self.env.as_user(SV).post('/approve', {'challengeId': 'нет', 'code': self.code}).status_code, 400)
        self.assertEqual(self.env.store.challenges[self.challenge]['attempts'], 0)
        self.assertEqual(self.approve(self.code).status_code, 200)

    def test_expired_code(self):
        self.env.clock += timedelta(seconds=access.CODE_TTL_SECONDS)
        response = self.approve(self.code)
        self.assertEqual((response.status_code, response.get_json()['code']), (410, 'CODE_EXPIRED'))
        self.assertTrue(self.env.as_user(VERIFIER).state().get_json()['locked'])

    def test_code_is_single_use(self):
        self.assertEqual(self.approve(self.code).status_code, 200)
        self.assertEqual(self.approve(self.code).status_code, 410)

    def test_code_belongs_to_the_one_who_scanned(self):
        """Чужой подтверждающий чужим ожиданием не пользуется — даже зная код."""
        for approver in (HEAD, ADMIN, SUPER, SZOV_SV):
            self.assertEqual(self.approve(self.code, approver=approver).status_code, 410, approver)
        self.assertTrue(self.env.as_user(VERIFIER).state().get_json()['locked'])

    def test_code_of_one_scan_does_not_fit_another(self):
        other = self.env.scan(HEAD).get_json()['challengeId']
        other_code = self.env.telegram.code()
        self.assertNotEqual(other_code, self.code)
        self.assertEqual(self.approve(self.code, approver=HEAD, challenge=other).get_json()['code'],
                         'CODE_WRONG')
        self.assertEqual(self.approve(other_code, approver=HEAD, challenge=other).status_code, 200)

    def test_refusal_on_the_merits_costs_no_attempt(self):
        """Сессию закрыли между сканом и вводом — отказ, и код при этом не сверяется."""
        self.env.store.live_sessions.clear()
        self.assertEqual(self.approve('000000').status_code, 410)
        self.assertEqual(self.env.store.challenges[self.challenge]['attempts'], 0)

    def test_employee_moved_out_of_the_group_after_the_scan(self):
        self.env.store.verifiers.discard(VERIFIER)
        self.assertEqual(self.approve(self.code).status_code, 400)
        self.assertNotIn(SESSION, self.env.store.granted)

    def test_approver_who_lost_the_right_after_the_scan(self):
        DEPARTMENTS[SV] = SZOV
        USERS[VERIFIER] = _user(VERIFIER, 'Сарсеке Мерей', 'operator', supervisor_id=None)
        try:
            self.assertEqual(self.approve(self.code).status_code, 403)
        finally:
            DEPARTMENTS[SV] = OP
            USERS[VERIFIER] = _user(VERIFIER, 'Сарсеке Мерей', 'operator', supervisor_id=SV)
        self.assertNotIn(SESSION, self.env.store.granted)


class ResendRouteTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.challenge = self.env.scan(SV).get_json()['challengeId']
        self.first_code = self.env.telegram.code()

    def resend(self, approver=SV):
        return self.env.as_user(approver).post('/code', {'challengeId': self.challenge})

    def test_cooldown(self):
        response = self.resend()
        self.assertEqual((response.status_code, response.get_json()['resendInSeconds']),
                         (429, access.CODE_RESEND_SECONDS))
        self.assertEqual(len(self.env.telegram.sent), 1)

    def test_new_code_replaces_the_old_one_and_restores_attempts(self):
        self.env.as_user(SV).post('/approve', {'challengeId': self.challenge, 'code': '000000'})
        self.env.clock += timedelta(seconds=access.CODE_RESEND_SECONDS)
        response = self.resend()
        body = response.get_json()
        self.assertEqual((response.status_code, body['attemptsLeft'], body['challengeId']),
                         (200, access.CODE_MAX_ATTEMPTS, self.challenge))
        new_code = self.env.telegram.code()
        self.assertNotEqual(new_code, self.first_code)
        old = self.env.as_user(SV).post('/approve', {'challengeId': self.challenge, 'code': self.first_code})
        self.assertEqual(old.get_json()['code'], 'CODE_WRONG')
        ok = self.env.as_user(SV).post('/approve', {'challengeId': self.challenge, 'code': new_code})
        self.assertEqual(ok.status_code, 200)

    def test_sends_are_capped(self):
        for index in range(access.CODE_MAX_SENDS - 1):
            self.env.clock += timedelta(seconds=access.CODE_RESEND_SECONDS)
            body = self.resend().get_json()
            self.assertEqual(body['canResend'], index < access.CODE_MAX_SENDS - 2)
        self.env.clock += timedelta(seconds=access.CODE_RESEND_SECONDS)
        self.assertEqual(self.resend().status_code, 429)
        self.assertEqual(len(self.env.telegram.sent), access.CODE_MAX_SENDS)

    def test_only_the_scanner_can_resend(self):
        self.env.clock += timedelta(seconds=access.CODE_RESEND_SECONDS)
        self.assertEqual(self.resend(approver=HEAD).status_code, 410)
        self.assertEqual(self.env.as_user(SV).post('/code', {'challengeId': 'нет'}).status_code, 400)

    def test_confirmed_scan_cannot_be_reopened(self):
        self.env.as_user(SV).post('/approve', {'challengeId': self.challenge, 'code': self.first_code})
        self.env.clock += timedelta(seconds=access.CODE_RESEND_SECONDS)
        self.assertEqual(self.resend().status_code, 410)

    def test_failed_delivery_is_reported(self):
        self.env.clock += timedelta(seconds=access.CODE_RESEND_SECONDS)
        self.env.telegram.status_code = 500
        self.assertEqual(self.resend().status_code, 502)


class ShiftRouteTests(unittest.TestCase):
    def setUp(self):
        self.env = Env().unlock()

    def status(self, key, event_id='click-1'):
        return self.env.post('/status', {'status_key': key, 'client_event_id': event_id})

    def test_start_pause_and_finish(self):
        body = self.status('готов').get_json()
        self.assertEqual((body['current']['label'], body['current']['onShift']), ('Активный', True))
        # Между нажатиями портал отмечается раз в минуту — как у живого человека.
        for _ in range(30):
            self.env.now += timedelta(minutes=1)
            self.env.post('/heartbeat')
        self.assertEqual(self.status('тех причина', 'click-2').get_json()['current']['label'], 'Тех. пауза')
        self.env.now += timedelta(minutes=5)
        body = self.status(shift.LOGOUT_KEY, 'click-3').get_json()
        self.assertEqual((body['current']['onShift'], body['current']['auto']), (False, False))
        self.assertEqual([(e['status_key'], e['client_event_id']) for e in self.env.store.events],
                         [('готов', 'wzws-click-1'), ('тех причина', 'wzws-click-2'),
                          (shift.LOGOUT_KEY, 'wzws-click-3')])
        self.assertTrue(all(e['operator_id'] == VERIFIER for e in self.env.store.events))

    def test_repeated_click_writes_once(self):
        self.status('готов')
        self.status('готов')
        self.assertEqual(len(self.env.store.events), 1)

    def test_unknown_status_and_missing_click_id(self):
        for key in ('online', 'busy', '', 'офлайн', 'исход'):
            self.assertEqual(self.status(key).status_code, 400, key)
        self.assertEqual(self.env.post('/status', {'status_key': 'готов'}).status_code, 400)
        self.assertEqual(self.env.store.events, [])

    def test_status_cannot_be_set_for_someone_else(self):
        """Оператор берётся из сессии — чужой id в теле запроса ничего не значит."""
        self.env.post('/status', {'status_key': 'готов', 'client_event_id': 'x',
                                  'operator_id': OTHER_VERIFIER, 'user_id': OTHER_VERIFIER})
        self.assertEqual({e['operator_id'] for e in self.env.store.events}, {VERIFIER})

    def test_only_a_confirmed_verifier_sets_statuses(self):
        locked = Env()
        response = locked.as_user(VERIFIER).post('/status', {'status_key': 'готов', 'client_event_id': 'x'})
        self.assertEqual((response.status_code, response.get_json()['code']), (403, 'CHAT_ACCESS_LOCKED'))
        for user_id in (SV, ADMIN, SUPER, LINE_OPERATOR, None):
            response = locked.as_user(user_id).post('/status', {'status_key': 'готов', 'client_event_id': 'x'})
            self.assertEqual(response.status_code, 403, user_id)
            self.assertEqual(locked.as_user(user_id).post('/heartbeat').status_code, 403, user_id)
        self.assertEqual(locked.store.events, [])

    def test_heartbeat_keeps_the_shift_and_reports_the_status(self):
        self.status('готов')
        self.env.now += timedelta(minutes=1)
        body = self.env.post('/heartbeat').get_json()
        self.assertEqual((body['locked'], body['current']['label']), (False, 'Активный'))
        self.assertEqual(self.env.store.presence[VERIFIER], self.env.now)

    def test_heartbeat_off_shift_marks_nothing(self):
        self.env.post('/heartbeat')
        self.assertEqual(self.env.store.presence, {})

    def test_heartbeat_of_a_locked_session_says_so(self):
        locked = Env()
        body = locked.as_user(VERIFIER).post('/heartbeat').get_json()
        self.assertEqual((body['locked'], body['current']), (True, None))

    def test_late_heartbeat_closes_the_shift_at_last_seen_instead_of_extending_it(self):
        self.status('готов')
        self.env.now += timedelta(minutes=2)
        self.env.post('/heartbeat')
        seen = self.env.now
        self.env.now += timedelta(minutes=25)
        body = self.env.post('/heartbeat').get_json()
        self.assertEqual((body['current']['onShift'], body['current']['auto']), (False, True))
        closing = self.env.store.events[-1]
        self.assertEqual((closing['status_key'], closing['event_at'], closing['state_note']),
                         (shift.LOGOUT_KEY, seen, shift.AUTO_LOGOUT_NOTE))

    def test_new_status_after_silence_starts_a_new_shift(self):
        self.status('готов')
        start = self.env.now
        self.env.now += timedelta(hours=14)
        self.status('готов', 'next-day')
        keys = [(e['status_key'], e['event_at']) for e in self.env.store.events]
        self.assertEqual(keys, [('готов', start), (shift.LOGOUT_KEY, start), ('готов', self.env.now)])

    def test_sweep_closes_abandoned_shifts_once(self):
        self.status('готов')
        self.env.now += timedelta(minutes=1)
        self.env.post('/heartbeat')
        seen = self.env.now
        self.env.now += timedelta(minutes=30)
        self.assertEqual(self.env.blueprint.sweep(), 1)
        self.assertEqual(self.env.blueprint.sweep(), 0)
        self.assertEqual(self.env.store.events[-1]['event_at'], seen)

    def test_sweep_leaves_live_and_foreign_shifts(self):
        self.status('готов')
        self.env.now += timedelta(minutes=5)
        self.env.post('/heartbeat')
        self.env.now += timedelta(minutes=5)
        self.assertEqual(self.env.blueprint.sweep(), 0)
        # Смена, открытая телефоном, — не наша: даже при старой отметке её не трогаем.
        self.env.store.events.append({'operator_id': OTHER_VERIFIER, 'event_at': self.env.now - timedelta(hours=3),
                                      'status_key': 'готов', 'state_note': None, 'client_event_id': 'phone-1'})
        self.env.store.presence[OTHER_VERIFIER] = self.env.now - timedelta(hours=2)
        self.assertEqual(self.env.blueprint.sweep(), 0)

    def test_presence_table_failure_does_not_stop_the_section(self):
        self.env.store.touch_fails = self.env.store.last_seen_fails = True
        self.assertEqual(self.status('готов').status_code, 200)
        self.assertEqual(self.env.post('/heartbeat').status_code, 200)
        self.assertEqual(self.env.state().status_code, 200)


class CodeMessageTests(unittest.TestCase):
    def test_message_is_short_and_escaped(self):
        text = workspace_routes.build_code_message('482915', 'Иван <b>Петров</b>', 'Мухтар & Адилет',
                                                   'Супервайзер', 600)
        self.assertIn('<code>482915</code>', text)
        self.assertIn('Иван &lt;b&gt;Петров&lt;/b&gt;', text)
        self.assertIn('Мухтар &amp; Адилет, супервайзер', text)
        self.assertIn('Действует 10 мин', text)
        self.assertLessEqual(len(text.splitlines()), 8)

    def test_message_without_role_label(self):
        text = workspace_routes.build_code_message('482915', 'Сотрудник', 'Глава', '', 300)
        self.assertIn('<b>Открывает:</b> Глава\n', text)
        self.assertIn('Действует 5 мин', text)


if __name__ == '__main__':
    unittest.main()
