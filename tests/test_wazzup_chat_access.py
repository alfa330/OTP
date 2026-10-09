"""«Чаты ОП» в режиме обработки: кому раздел открыт и чем это подтверждается.

Правила — wazzup/access.py, проводка — bot_schedule2.py (_wazzup_chat_access и
гарды). Функции монолита исполняются настоящие, вырезанные из исходника: копия
правила в тесте проверяла бы саму себя.
"""
import ast
import base64
import hashlib
import hmac
import unittest
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from tests import source_cache
from wazzup import access

ROOT = Path(__file__).resolve().parents[1]
BOT_SOURCE = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')
APP_SOURCE = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')

SECRET = 'unit-test-secret'
SESSION = '11111111-2222-3333-4444-555555555555'
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)


def _load(names, namespace):
    """Объявления верхнего уровня bot_schedule2.py — в namespace, как они написаны."""
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
    assert not wanted, f'не найдено в bot_schedule2.py: {sorted(wanted)}'
    return namespace


class VerifierRuleTests(unittest.TestCase):
    def test_who_is_a_verifier(self):
        cases = [
            # роль, отдел, модель дня, статус -> верификатор?
            (('operator', 'op', 'op_verificator', 'working'), True),
            (('Operator', ' OP ', 'OP_VERIFICATOR', None), True),
            # Группа решает, а не отдел: «Основа» и «Поток» пишут клиентам из своих программ.
            (('operator', 'op', 'op_osnova', 'working'), False),
            (('operator', 'op', 'op_potok', 'working'), False),
            (('operator', 'op', '', 'working'), False),
            # Модель та же, отдел чужой — такого быть не должно, и доступа он не даёт.
            (('operator', 'szov', 'op_verificator', 'working'), False),
            # Стажёру QR портал не выдаёт; супервайзер и глава идут своей дорогой.
            (('trainee', 'op', 'op_verificator', 'working'), False),
            (('sv', 'op', 'op_verificator', 'working'), False),
            (('admin', 'op', 'op_verificator', 'working'), False),
            (('operator', 'op', 'op_verificator', 'fired'), False),
            (('operator', 'op', 'op_verificator', 'dismissal'), False),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(access.is_verifier(*args), expected)

    def test_only_super_admins_process_without_the_gate(self):
        """Решение владельца 08.10.2026: «пока что суперадмины»."""
        self.assertTrue(access.processes_without_gate('super_admin', 'working'))
        self.assertTrue(access.processes_without_gate('SUPER_ADMIN'))
        for role in ('admin', 'sv', 'operator', 'trainer', '', None):
            self.assertFalse(access.processes_without_gate(role, 'working'), role)
        for status in ('fired', 'dismissal'):
            self.assertFalse(access.processes_without_gate('super_admin', status), status)

    def test_icore_author_key(self):
        self.assertEqual(access.icore_author_id(241), 'icore:241')
        self.assertTrue(access.is_icore_author('icore:241'))
        # Автор самого Wazzup — число строкой, «Поток» — 'potok:<имя>'.
        for other in ('12345678', 'potok:иванов иван', '', None):
            self.assertFalse(access.is_icore_author(other), other)


class QrTokenTests(unittest.TestCase):
    def test_round_trip(self):
        token, expires_at = access.build_qr_token(SECRET, SESSION, 241, now=NOW)
        self.assertEqual(expires_at, NOW + timedelta(seconds=access.QR_TTL_SECONDS))
        claims = access.decode_qr_token(SECRET, access.QR_PREFIX + token, now=NOW)
        self.assertEqual(claims, {'session_id': SESSION, 'user_id': 241, 'expires_at': expires_at})

    def test_prefix_is_optional_and_case_insensitive(self):
        """В поле ручного ввода приносят и голый токен, и строку в другом регистре."""
        token, _ = access.build_qr_token(SECRET, SESSION, 241, now=NOW)
        for raw in (token, 'otpw:' + token, '  OTPW:' + token + '  ', 'OTPW:' + token.lower()):
            with self.subTest(raw=raw[:12]):
                self.assertEqual(access.decode_qr_token(SECRET, raw, now=NOW)['user_id'], 241)
        self.assertTrue(access.is_chat_qr('otpw:abc'))
        self.assertFalse(access.is_chat_qr('OTPQ:abc'))

    def test_expired_code_is_named_as_such(self):
        token, expires_at = access.build_qr_token(SECRET, SESSION, 241, now=NOW)
        for moment in (expires_at, expires_at + timedelta(seconds=1)):
            with self.assertRaises(access.QrError) as caught:
                access.decode_qr_token(SECRET, token, now=moment)
            self.assertEqual(caught.exception.reason, 'expired')
        access.decode_qr_token(SECRET, token, now=expires_at - timedelta(seconds=1))

    def test_tampered_and_foreign_codes_are_rejected(self):
        token, _ = access.build_qr_token(SECRET, SESSION, 241, now=NOW)
        raw = bytearray(base64.b32decode(token + '=' * (-len(token) % 8)))
        raw[18] ^= 0x01                              # чужой сотрудник в том же коде
        forged = base64.b32encode(bytes(raw)).decode().rstrip('=')
        for bad, reason in ((forged, 'signature'), ('', 'format'), ('OTPW:', 'format'),
                            ('не base32!', 'format'), (token[:-4], 'format'), (token + 'AAAA', 'format')):
            with self.subTest(bad=bad[:10]):
                with self.assertRaises(access.QrError) as caught:
                    access.decode_qr_token(SECRET, bad, now=NOW)
                self.assertEqual(caught.exception.reason, reason)
        with self.assertRaises(access.QrError) as caught:
            access.decode_qr_token('another-secret', token, now=NOW)
        self.assertEqual(caught.exception.reason, 'signature')

    def test_general_qr_does_not_open_chats_and_vice_versa(self):
        """Два ключа — два кода. Иначе требование о коде главы отдела обходилось бы
        подтверждением любого другого раздела: тот же скан, только без кода."""
        namespace = {'hmac': hmac, 'hashlib': hashlib, 'base64': base64, 'uuid': uuid,
                     'datetime': datetime, 'timezone': timezone, 'timedelta': timedelta,
                     'SENSITIVE_QR_SECRET': SECRET, 'SENSITIVE_QR_TTL_SECONDS': 300,
                     '_decode_legacy_sensitive_qr_token': lambda token: (_ for _ in ()).throw(
                         ValueError('Invalid QR token format'))}
        _load(('SENSITIVE_QR_BODY_BYTES', 'SENSITIVE_QR_SIGNATURE_BYTES', '_sensitive_qr_signature',
               '_build_sensitive_qr_token', '_decode_sensitive_qr_token'), namespace)
        general, _ = namespace['_build_sensitive_qr_token'](SESSION, 241)
        self.assertEqual(namespace['_decode_sensitive_qr_token'](general)['user_id'], 241)
        with self.assertRaises(access.QrError) as caught:
            access.decode_qr_token(SECRET, general)
        self.assertEqual(caught.exception.reason, 'signature')

        chat, _ = access.build_qr_token(SECRET, SESSION, 241)
        with self.assertRaises(ValueError):
            namespace['_decode_sensitive_qr_token'](chat)


class TelegramCodeTests(unittest.TestCase):
    def test_code_is_six_digits(self):
        for _ in range(200):
            code = access.new_code()
            self.assertRegex(code, r'^\d{6}$')

    def test_typed_code_is_normalised(self):
        for raw, expected in (('482915', '482915'), (' 482 915 ', '482915'), ('48-29-15', '482915'),
                              (482915, '482915'), ('48291', None), ('4829155', None), ('', None),
                              (None, None), ('abcdef', None), ('4 8 2 9 1 5' + ' ' * 20, '482915'),
                              # Шесть цифр, выуженные из чужого длинного текста, кодом не считаем.
                              ('заказ 48 от 29 числа, 15 тенге', None)):
            with self.subTest(raw=raw):
                self.assertEqual(access.normalize_code(raw), expected)

    def test_hash_is_bound_to_its_scan(self):
        first, second = uuid.uuid4(), uuid.uuid4()
        stored = access.hash_code(SECRET, first, '482915')
        self.assertNotIn('482915', stored)
        self.assertTrue(access.code_matches(SECRET, first, '482915', stored))
        self.assertFalse(access.code_matches(SECRET, first, '482916', stored))
        # Тот же код к другому скану не подходит.
        self.assertFalse(access.code_matches(SECRET, second, '482915', stored))
        self.assertFalse(access.code_matches('other-secret', first, '482915', stored))
        self.assertFalse(access.code_matches(SECRET, first, '482915', None))


class _StateCursor:
    """Отвечает на единственный запрос load_operator_state заранее заданной строкой."""

    def __init__(self, rows, calls):
        self.rows, self.calls, self.row = rows, calls, None

    def execute(self, sql, params):
        self.calls.append(params)
        self.row = self.rows.get(params['user_id'])

    def fetchone(self):
        return self.row


class OperatorStateTests(unittest.TestCase):
    def _state(self, row, session_id=SESSION):
        calls = []
        state = access.load_operator_state(_StateCursor({7: row}, calls), 7, session_id,
                                           date(2026, 10, 8), 367)
        return state, calls[0]

    def test_verifier_with_confirmed_session(self):
        state, params = self._state(('operator', 'working', 'op', 'op_verificator', True))
        self.assertEqual(state, {'verifier': True, 'unlocked': True})
        self.assertEqual((params['day'], params['sales_department_id'], params['session_id']),
                         (date(2026, 10, 8), 367, SESSION))

    def test_verifier_without_confirmation_is_locked(self):
        state, _ = self._state(('operator', 'working', 'op', 'op_verificator', False))
        self.assertEqual(state, {'verifier': True, 'unlocked': False})

    def test_confirmation_does_not_make_anyone_a_verifier(self):
        """Строка доступа осталась, а человека перевели в другую группу."""
        state, _ = self._state(('operator', 'working', 'op', 'op_osnova', True))
        self.assertEqual(state, {'verifier': False, 'unlocked': False})

    def test_session_id_is_cleaned(self):
        self.assertEqual(access.clean_session_id(SESSION.upper()), SESSION)
        self.assertEqual(access.clean_session_id(uuid.UUID(SESSION)), SESSION)
        for missing in (None, '', 'None', 'not-a-session', 12345, '1234'):
            self.assertIsNone(access.clean_session_id(missing), missing)

    def test_no_session_means_locked_and_does_not_break_the_query(self):
        for missing in (None, '', 'None', 'not-a-session'):
            state, params = self._state(('operator', 'working', 'op', 'op_verificator', True), missing)
            self.assertEqual(state, {'verifier': True, 'unlocked': False})
            uuid.UUID(params['session_id'])          # в запрос ушёл разбираемый uuid, не пустая строка

    def test_unknown_user(self):
        state, _ = self._state(None)
        self.assertEqual(state, {'verifier': False, 'unlocked': False})

    def test_query_reads_the_group_of_the_day_first(self):
        sql = ' '.join(access._OPERATOR_STATE_SQL.split())
        self.assertIn('COALESCE(gm.calculation_model_code, dir.calculation_model_code', sql)
        self.assertIn('gom.start_date <= %(day)s::date', sql)
        # Доступ живёт только вместе с живой сессией.
        self.assertIn('s.revoked_at IS NULL', sql)
        self.assertIn("s.expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')", sql)
        self.assertIn('a.revoked_at IS NULL', sql)
        # Обычный QR портала тоже открывает чаты (09.10.2026) — но только своей сессии.
        self.assertIn('AND s.user_id = u.id', sql)
        self.assertIn('AND (s.sensitive_data_unlocked OR EXISTS (SELECT 1 FROM wazzup_chat_access a', sql)


# ── _wazzup_chat_access и гарды: настоящие функции монолита ───────────────────

USERS = {
    # id: (роль, статус)
    1: ('super_admin', 'working'),
    2: ('admin', 'working'),            # глобальный админ
    3: ('admin', 'working'),            # глава ОП
    4: ('sv', 'working'),               # СВ ОП
    5: ('operator', 'working'),         # верификатор, доступ подтверждён
    6: ('operator', 'working'),         # верификатор, доступ не подтверждён
    7: ('operator', 'working'),         # оператор «Основы»
    8: ('operator', 'working'),         # оператор СЗоВ
    9: ('super_admin', 'fired'),
}
FULL_AUDIENCE = {1, 2, 3, 4, 9}
OPERATOR_STATES = {5: {'verifier': True, 'unlocked': True},
                   6: {'verifier': True, 'unlocked': False}}


def _user_tuple(user_id):
    role, status = USERS[user_id]
    row = [None] * 20
    row[0], row[2], row[3], row[11] = user_id, f'User {user_id}', role, status
    return tuple(row)


class ChatAccessWiringTests(unittest.TestCase):
    def setUp(self):
        self.state_calls = []
        self.account = 'op'
        self.broken_state = False
        test = self

        class _Db:
            @staticmethod
            def get_user(id=None):
                return _user_tuple(id) if id in USERS else None

            @contextmanager
            def _get_cursor(self):
                yield object()

        def load_operator_state(cursor, user_id, session_id, day, sales_department_id):
            test.state_calls.append((user_id, session_id, sales_department_id))
            if test.broken_state:
                raise RuntimeError('pool timeout')
            return OPERATOR_STATES.get(user_id, {'verifier': False, 'unlocked': False})

        wazzup_access = SimpleNamespace(
            MODE_FULL=access.MODE_FULL, MODE_OPERATOR=access.MODE_OPERATOR,
            processes_without_gate=access.processes_without_gate,
            load_operator_state=load_operator_state)
        self.g = SimpleNamespace(user_id=None)
        self.namespace = {
            'g': self.g, 'db': _Db(), 'wazzup_access': wazzup_access,
            'jsonify': lambda payload: payload,
            'logging': SimpleNamespace(exception=lambda *a, **k: None),
            'datetime': datetime, 'ZoneInfo': __import__('zoneinfo').ZoneInfo,
            'AI_QA_OP_DEPARTMENT_ID': 367,
            '_normalize_user_role': lambda role: str(role or '').strip().lower(),
            '_current_session_id_from_access_token': lambda: SESSION,
            '_verifier_chats_guard': lambda: (
                (self.g.user_id, None) if self.g.user_id in FULL_AUDIENCE
                else (None, ({'error': 'forbidden'}, 403))),
            '_wazzup_account_arg': lambda: self.account,
        }
        _load(('_wazzup_chat_access', '_wazzup_chat_reader_guard'), self.namespace)

    def _as(self, user_id):
        self.g.__dict__.clear()
        self.g.user_id = user_id
        return self.namespace['_wazzup_chat_access']()

    def test_modes_table(self):
        expected = {
            # кто: (режим, закрыт, может писать)
            1: ('full', False, True),           # супер-админ пишет без подтверждения
            2: ('full', False, False),          # админ — просмотр, как раньше
            3: ('full', False, False),          # глава ОП — просмотр
            4: ('full', False, False),          # СВ ОП — просмотр
            5: ('operator', False, True),
            6: ('operator', True, False),
            7: (None, False, False),
            8: (None, False, False),
            9: ('full', False, False),          # уволенный супер-админ не пишет
            None: (None, False, False),
        }
        for user_id, (mode, locked, can_process) in expected.items():
            with self.subTest(user_id=user_id):
                state = self._as(user_id)
                self.assertEqual((state['mode'], state['locked'], state['can_process']),
                                 (mode, locked, can_process))
                self.assertEqual(state['user_id'], user_id)

    def test_full_audience_is_not_asked_about_the_gate(self):
        """Прежней аудитории раздела запрос про группу и подтверждение не нужен."""
        self._as(2)
        self.assertEqual(self.state_calls, [])
        self._as(5)
        self.assertEqual(self.state_calls, [(5, SESSION, 367)])

    def test_computed_once_per_request(self):
        self._as(5)
        for _ in range(3):
            self.namespace['_wazzup_chat_access']()
            self.namespace['_wazzup_chat_reader_guard']()
        self.assertEqual(len(self.state_calls), 1)

    def test_database_failure_closes_the_section(self):
        self.broken_state = True
        state = self._as(5)
        self.assertEqual((state['mode'], state['can_process']), (None, False))

    def test_reader_guard(self):
        guard = self.namespace['_wazzup_chat_reader_guard']
        forbidden = (None, ({'error': 'forbidden'}, 403))
        for user_id in (1, 2, 3, 4):
            self._as(user_id)
            self.assertEqual(guard(), (user_id, None), user_id)
        self._as(5)
        self.assertEqual(guard(), (5, None))
        self._as(6)
        requester, error = guard()
        self.assertIsNone(requester)
        self.assertEqual(error[1], 403)
        self.assertEqual(error[0]['code'], 'CHAT_ACCESS_LOCKED')
        for user_id in (7, 8, None):
            self._as(user_id)
            self.assertEqual(guard(), forbidden, user_id)

    def test_verifier_reads_only_the_op_account(self):
        """«Поток» ведут другие люди — верификатору его нет ни на экране, ни в ручках."""
        guard = self.namespace['_wazzup_chat_reader_guard']
        for account in ('potok', None):
            self.account = account
            self._as(5)
            self.assertEqual(guard(), (None, ({'error': 'forbidden'}, 403)), account)
            self._as(2)
            self.assertEqual(guard(), (2, None), account)


class WiringTextTests(unittest.TestCase):
    """Места, где правило подключено. Сломанная проводка не роняет ни один
    поведенческий тест выше — поэтому сверяем её отдельно."""

    def test_processing_blueprint_uses_the_shared_rule(self):
        call = BOT_SOURCE[BOT_SOURCE.index('app.register_blueprint(build_pilot_blueprint('):]
        call = call[:call.index('wazzup_syntony.start_worker(db)')]
        self.assertIn('guard=_wazzup_chat_reader_guard', call)
        self.assertIn('access=_wazzup_chat_access', call)
        self.assertIn("stream_limit=_env_int('WAZZUP_PILOT_STREAM_LIMIT'", call)

    def test_workspace_blueprint_reuses_the_qr_approval_perimeter(self):
        call = BOT_SOURCE[BOT_SOURCE.index('build_wazzup_workspace_blueprint('):]
        call = call[:call.index('app.register_blueprint(_wazzup_workspace_bp)')]
        self.assertIn('chat_access=_wazzup_chat_access', call)
        self.assertIn('approval_perimeter_error=_sensitive_access_approval_error', call)
        self.assertIn('approver_context=_wazzup_chat_approver_context', call)
        self.assertIn('secret=SENSITIVE_QR_SECRET', call)
        self.assertIn('sales_department_id=AI_QA_OP_DEPARTMENT_ID', call)

    def test_shift_watcher_is_scheduled(self):
        self.assertIn("id='wazzup_workspace_sweep_2min'", BOT_SOURCE)
        self.assertIn('_wazzup_workspace_sweep = _wazzup_workspace_bp.sweep', BOT_SOURCE)

    def test_profile_flag_reaches_the_portal(self):
        self.assertIn('"wazzup_chat_operator": wazzup_chat_operator,', BOT_SOURCE)
        payload = BOT_SOURCE[BOT_SOURCE.index('def _get_user_payload(user):'):]
        payload = payload[:payload.index('\ndef ', 10)]
        # Флаг считается тем же правилом, что и доступ, — без сессии, только «верификатор ли».
        self.assertIn("wazzup_access.load_operator_state(", payload)
        self.assertIn("['verifier']", payload)
        self.assertIn("str(role or '').strip().lower() == 'operator'", payload)

    def test_frontend_predicate_reads_the_flag(self):
        predicate = APP_SOURCE.split('const canAccessVerifierChatsForUser = (userLike) => {', 1)[1]
        predicate = predicate.split('\n};', 1)[0]
        self.assertIn('if (isWazzupChatOperator(userLike)) return true;', predicate)
        self.assertIn('userLike?.wazzup_chat_operator ?? userLike?.wazzupChatOperator', APP_SOURCE)
        # Сторож смены смонтирован на весь портал, а не только в разделе.
        self.assertIn('{isWazzupChatOperator(user) && (', APP_SOURCE)
        self.assertIn('<WazzupShiftKeeper', APP_SOURCE)


if __name__ == '__main__':
    unittest.main()
