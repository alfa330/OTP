# -*- coding: utf-8 -*-
"""Тумблер «Уведомления о доступе» в разделе «Сессии» (владелец, 24.11.2026).

Сообщение «Открыт доступ к чувствительным данным» раньше безусловно уходило
всем супер-админам и ещё на SUPER_ADMIN_ID из окружения — на каждый QR каждого
оператора. Теперь его получают только админы, которые сами включили тумблер в
разделе; по умолчанию он выключен у всех.

Сторожится:
  * получатели — только включившие и только пока они админы (уровень admin и
    выше, тем же `_is_admin_role`, что пускает в раздел); SUPER_ADMIN_ID без
    тумблера больше не получает ничего;
  * без получателей фоновая отправка не читает ни сессию, ни супервайзера и
    ничего не шлёт — это теперь обычный случай, а не сбой настройки;
  * ручка: тумблер у каждого свой, только админам раздела, `enabled` — только
    настоящий bool (строка «false» в Python истинна);
  * обе выдачи доступа — по QR и кнопкой из раздела — зовут новую отправку.

Функции берутся НАСТОЯЩИЕ из монолита; подменяется только внешнее (база,
Telegram, Flask).
"""

import ast
import copy
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = source_cache.monolith_flat_path()
MODULE = source_cache.parse(BOT_PATH.read_text(encoding='utf-8-sig'))


def _functions():
    return {node.name: node for node in MODULE.body if isinstance(node, ast.FunctionDef)}


FUNCTIONS = _functions()


def _load(names, namespace, assignments=()):
    """Исполняет выбранные функции (и присваивания верхнего уровня) монолита в namespace."""
    body = []
    for node in MODULE.body:
        if (isinstance(node, ast.Assign)
                and any(getattr(t, 'id', None) in assignments for t in node.targets)):
            body.append(copy.deepcopy(node))
    for name in names:
        node = copy.deepcopy(FUNCTIONS[name])
        node.decorator_list = []
        body.append(node)
    scope = dict(namespace)
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
                 str(BOT_PATH), 'exec'), scope)
    return scope


# Правило «кто админ» — настоящее: подставь мы своё, тест перестал бы замечать,
# что раздел и рассылка разошлись в понимании роли.
ROLE_RULE = ('_normalize_user_role', '_get_role_level', '_has_min_role', '_is_admin_role')


class _RecipientsDB:
    def __init__(self, rows=None, fail=False):
        self.rows = list(rows or [])
        self.fail = fail
        self.calls = 0

    def list_session_access_notify_recipients(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError('база недоступна')
        return [dict(row) for row in self.rows]


def _resolver(fake_db, super_admin_id=777001):
    scope = _load(('_resolve_session_access_notify_chat_ids',) + ROLE_RULE, {
        'db': fake_db,
        'logging': logging,
        # Переменная окружения монолита. Раньше её чат получал каждое
        # уведомление; положили сюда, чтобы заметить, если она вернётся.
        'super_admin_id': super_admin_id,
    }, assignments=('ROLE_HIERARCHY',))
    return scope['_resolve_session_access_notify_chat_ids']


class RecipientsTests(unittest.TestCase):
    def test_nobody_subscribed_means_nobody_is_notified(self):
        """По умолчанию тумблер выключен у всех — и у супер-админов, и у SUPER_ADMIN_ID."""
        self.assertEqual(_resolver(_RecipientsDB())(), [])

    def test_only_subscribed_admins_get_the_message(self):
        fake_db = _RecipientsDB([
            {'user_id': 1, 'role': 'super_admin', 'telegram_id': 9001},
            {'user_id': 2, 'role': 'admin', 'telegram_id': 9002},
            {'user_id': 3, 'role': 'sv', 'telegram_id': 9003},
            {'user_id': 4, 'role': 'operator', 'telegram_id': 9004},
            {'user_id': 5, 'role': 'trainer', 'telegram_id': 9005},
        ])
        self.assertEqual(_resolver(fake_db)(), [9001, 9002],
                         'подписку разжалованного админа рассылка не исполняет')

    def test_role_spellings_follow_the_section_rule(self):
        fake_db = _RecipientsDB([
            {'user_id': 1, 'role': 'SuperAdmin', 'telegram_id': 9001},
            {'user_id': 2, 'role': ' ADMIN ', 'telegram_id': 9002},
            {'user_id': 3, 'role': None, 'telegram_id': 9003},
        ])
        self.assertEqual(_resolver(fake_db)(), [9001, 9002])

    def test_super_admin_id_from_env_is_not_added(self):
        fake_db = _RecipientsDB([{'user_id': 2, 'role': 'admin', 'telegram_id': 9002}])
        self.assertEqual(_resolver(fake_db, super_admin_id=9999)(), [9002])

    def test_one_message_per_chat_and_garbage_is_skipped(self):
        fake_db = _RecipientsDB([
            {'user_id': 1, 'role': 'admin', 'telegram_id': 9001},
            {'user_id': 2, 'role': 'admin', 'telegram_id': '9001'},
            {'user_id': 3, 'role': 'admin', 'telegram_id': 'не число'},
            {'user_id': 4, 'role': 'admin', 'telegram_id': -100500},
            {'user_id': 5, 'role': 'admin', 'telegram_id': None},
        ])
        self.assertEqual(_resolver(fake_db)(), [9001])

    def test_broken_database_means_no_recipients_not_a_crash(self):
        fake_db = _RecipientsDB(fail=True)
        with self.assertLogs(level='ERROR'):
            self.assertEqual(_resolver(fake_db)(), [])


class _NotifyDB:
    def __init__(self):
        self.calls = []

    def get_user_session(self, session_id, user_id=None):
        self.calls.append(('session', session_id, user_id))
        return {'session_id': session_id, 'ip_address': '212.154.168.10', 'user_agent': 'Chrome'}

    def get_user(self, id):  # noqa: A002 — сигнатура монолита
        self.calls.append(('user', id))
        return (id, None, 'Элекова Арайлым', 'sv')


APPROVER = (2, 555001, 'Ядигаров Руслан', 'super_admin', None, None, None, 'ruslan')
OPERATOR = (448, 555002, 'Дуанаева Айша', 'operator', 'СЗоВ', None, 77, 'aisha')
SESSION_ID = '3dbab764-1234-4b3c-9aaa-0011223344ff'


def _notifier(chat_ids, fake_db, sends, built, fail_on=()):
    def send(chat_id, text, parse_mode=None):
        if chat_id in fail_on:
            raise RuntimeError('Telegram недоступен')
        sends.append((chat_id, text, parse_mode))
        return SimpleNamespace(status_code=200)

    def build(**kwargs):
        built.append(kwargs)
        return 'текст'

    scope = _load(('_notify_sensitive_access_approved',), {
        'db': fake_db,
        'logging': logging,
        '_resolve_session_access_notify_chat_ids': lambda: list(chat_ids),
        '_build_sensitive_access_approved_message_html': build,
        '_send_telegram_text_message': send,
        '_get_telegram_error_text': lambda response: 'ошибка',
    })
    return scope['_notify_sensitive_access_approved']


class NotifyTests(unittest.TestCase):
    def test_without_subscribers_nothing_is_read_and_nothing_is_sent(self):
        """Обычный случай: тумблер выключен у всех — ни лишних запросов, ни сообщений."""
        fake_db, sends, built = _NotifyDB(), [], []
        notify = _notifier([], fake_db, sends, built)
        notify(APPROVER, OPERATOR, {'session_id': SESSION_ID}, {})
        self.assertEqual(fake_db.calls, [])
        self.assertEqual(built, [])
        self.assertEqual(sends, [])

    def test_every_subscriber_gets_the_message(self):
        fake_db, sends, built = _NotifyDB(), [], []
        notify = _notifier([9001, 9002], fake_db, sends, built)
        notify(APPROVER, OPERATOR, {'session_id': SESSION_ID}, {'request_ip': '10.0.0.1'},
               source='manual')
        self.assertEqual([chat for chat, _, _ in sends], [9001, 9002])
        self.assertTrue(all(mode == 'HTML' for _, _, mode in sends))
        self.assertEqual(len(built), 1, 'текст собирается один раз на всех')

    def test_session_and_supervisor_are_read_for_the_text(self):
        fake_db, sends, built = _NotifyDB(), [], []
        notify = _notifier([9001], fake_db, sends, built)
        notify(APPROVER, OPERATOR, {'session_id': SESSION_ID}, {}, source='manual')
        self.assertIn(('session', SESSION_ID, 448), fake_db.calls,
                      'сессия — именно этого оператора')
        self.assertIn(('user', 77), fake_db.calls, 'супервайзер — из карточки оператора')
        self.assertEqual(built[0]['operator_session']['ip_address'], '212.154.168.10')
        self.assertEqual(built[0]['operator_supervisor_name'], 'Элекова Арайлым')
        self.assertEqual(built[0]['source'], 'manual')
        self.assertIs(built[0]['approver'], APPROVER)
        self.assertIs(built[0]['operator'], OPERATOR)

    def test_qr_is_the_default_source(self):
        fake_db, sends, built = _NotifyDB(), [], []
        _notifier([9001], fake_db, sends, built)(APPROVER, OPERATOR, {'session_id': SESSION_ID}, {})
        self.assertEqual(built[0]['source'], 'qr')

    def test_operator_without_supervisor_is_not_a_lookup(self):
        fake_db, sends, built = _NotifyDB(), [], []
        operator = OPERATOR[:6] + (None,) + OPERATOR[7:]
        _notifier([9001], fake_db, sends, built)(APPROVER, operator, {'session_id': SESSION_ID}, {})
        self.assertNotIn('user', [call[0] for call in fake_db.calls])
        self.assertIsNone(built[0]['operator_supervisor_name'])

    def test_one_failed_chat_does_not_silence_the_rest(self):
        fake_db, sends, built = _NotifyDB(), [], []
        notify = _notifier([9001, 9002], fake_db, sends, built, fail_on=(9001,))
        with self.assertLogs(level='ERROR'):
            notify(APPROVER, OPERATOR, {'session_id': SESSION_ID}, {})
        self.assertEqual([chat for chat, _, _ in sends], [9002])

    def test_failure_stays_inside_the_background_thread(self):
        """Поток фоновый: исключение из него никто не поймает, кроме него самого."""
        fake_db = _NotifyDB()
        fake_db.get_user_session = lambda **kwargs: (_ for _ in ()).throw(RuntimeError('нет базы'))
        notify = _notifier([9001], fake_db, [], [])
        with self.assertLogs(level='ERROR'):
            notify(APPROVER, OPERATOR, {'session_id': SESSION_ID}, {})


class _ToggleDB:
    def __init__(self, role='admin', state=None):
        self.role = role
        self.state = dict(state or {'enabled': False, 'telegram_linked': True})
        self.reads = []
        self.writes = []

    def get_user(self, id):  # noqa: A002 — сигнатура монолита
        return (id, None, 'Кто-то', self.role, None, None, None)

    def get_session_access_notify_state(self, user_id):
        self.reads.append(user_id)
        return dict(self.state)

    def set_session_access_notify(self, user_id, enabled):
        self.writes.append((user_id, enabled))
        self.state['enabled'] = enabled
        return dict(self.state)


def _toggle_route(fake_db, method='GET', body=None, user_id=5):
    scope = _load(('admin_sessions_notify', '_admin_sessions_guard') + ROLE_RULE, {
        'db': fake_db,
        'g': SimpleNamespace(user_id=user_id),
        'request': SimpleNamespace(method=method, get_json=lambda silent=False: body),
        'jsonify': lambda payload: payload,
        'logging': logging,
        '_build_cors_preflight_response': lambda: ('preflight', 200),
    }, assignments=('ROLE_HIERARCHY',))
    return scope['admin_sessions_notify']


def _status(result):
    return result[1] if isinstance(result, tuple) else 200


class ToggleRouteTests(unittest.TestCase):
    def test_reads_own_state(self):
        fake_db = _ToggleDB(state={'enabled': False, 'telegram_linked': True})
        payload, status = _toggle_route(fake_db)()
        self.assertEqual(status, 200)
        self.assertEqual((payload['enabled'], payload['telegram_linked']), (False, True))
        self.assertEqual(fake_db.reads, [5])
        self.assertEqual(fake_db.writes, [])

    def test_switches_on_and_off(self):
        fake_db = _ToggleDB()
        payload, status = _toggle_route(fake_db, 'PUT', {'enabled': True})()
        self.assertEqual((status, payload['enabled']), (200, True))
        payload, status = _toggle_route(fake_db, 'PUT', {'enabled': False})()
        self.assertEqual((status, payload['enabled']), (200, False))
        self.assertEqual(fake_db.writes, [(5, True), (5, False)])

    def test_tells_when_telegram_is_not_linked(self):
        fake_db = _ToggleDB(state={'enabled': False, 'telegram_linked': False})
        payload, _ = _toggle_route(fake_db, 'PUT', {'enabled': True})()
        self.assertEqual((payload['enabled'], payload['telegram_linked']), (True, False))

    def test_only_a_real_bool_is_accepted(self):
        """Строка «false» в Python истинна: «выключить» молча включало бы."""
        for body in ({'enabled': 'false'}, {'enabled': 'true'}, {'enabled': 0}, {'enabled': 1},
                     {'enabled': None}, {}, None):
            fake_db = _ToggleDB()
            result = _toggle_route(fake_db, 'PUT', body)()
            self.assertEqual(_status(result), 400, body)
            self.assertEqual(fake_db.writes, [], body)

    def test_everyone_switches_only_his_own(self):
        fake_db = _ToggleDB()
        _toggle_route(fake_db, 'PUT', {'enabled': True, 'user_id': 1}, user_id=5)()
        self.assertEqual(fake_db.writes, [(5, True)], 'чужой тумблер отсюда не переключить')

    def test_super_admin_has_it_too(self):
        fake_db = _ToggleDB(role='super_admin')
        self.assertEqual(_status(_toggle_route(fake_db, 'PUT', {'enabled': True})()), 200)

    def test_closed_for_everyone_outside_the_section(self):
        for role in ('sv', 'operator', 'trainer', 'hr_manager'):
            fake_db = _ToggleDB(role=role)
            self.assertEqual(_status(_toggle_route(fake_db)()), 403, role)
            self.assertEqual(_status(_toggle_route(fake_db, 'PUT', {'enabled': True})()), 403, role)
            self.assertEqual((fake_db.reads, fake_db.writes), ([], []), role)

    def test_preflight_answers_without_touching_the_database(self):
        fake_db = _ToggleDB(role='operator')
        self.assertEqual(_toggle_route(fake_db, 'OPTIONS')(), ('preflight', 200))
        self.assertEqual((fake_db.reads, fake_db.writes), ([], []))

    def test_route_is_registered_for_both_methods(self):
        node = FUNCTIONS['admin_sessions_notify']
        routes = [d for d in node.decorator_list
                  if isinstance(d, ast.Call) and getattr(d.func, 'attr', '') == 'route']
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].args[0].value, '/api/admin/sessions/notify')
        methods = [elt.value for elt in routes[0].keywords[0].value.elts]
        self.assertEqual(sorted(methods), ['GET', 'OPTIONS', 'PUT'])
        self.assertIn('require_api_key', [getattr(d, 'id', None) for d in node.decorator_list])


def _thread_targets(function_name):
    """(target, число позиционных аргументов) у каждого threading.Thread(...) в функции."""
    found = []
    for node in ast.walk(FUNCTIONS[function_name]):
        if (isinstance(node, ast.Call) and getattr(node.func, 'attr', '') == 'Thread'
                and getattr(node.func.value, 'id', '') == 'threading'):
            keywords = {kw.arg: kw.value for kw in node.keywords}
            target = getattr(keywords.get('target'), 'id', None)
            args = keywords.get('args')
            found.append((target, len(args.elts) if isinstance(args, ast.Tuple) else None))
    return found


class WiringTests(unittest.TestCase):
    def test_both_grants_wake_the_subscription_notify(self):
        for handler in ('approve_sensitive_access', 'grant_admin_session_sensitive_access'):
            self.assertEqual(_thread_targets(handler), [('_notify_sensitive_access_approved', 4)],
                             handler)

    def test_old_unconditional_broadcast_is_gone(self):
        for name in ('_notify_super_admin_sensitive_access_approved', '_resolve_super_admin_chat_ids'):
            self.assertNotIn(name, FUNCTIONS, f'вернулась рассылка всем супер-админам: {name}')

    def test_notify_signature_matches_the_handlers(self):
        args = [a.arg for a in FUNCTIONS['_notify_sensitive_access_approved'].args.args]
        self.assertEqual(args, ['approver', 'operator', 'claims', 'approval_context', 'source'])

    def test_schema_keeps_one_row_per_subscriber(self):
        schema = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8')
        start = schema.index('CREATE TABLE IF NOT EXISTS session_access_notify_optin')
        ddl = schema[start:schema.index(');', start)]
        self.assertIn('user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE', ddl)
        self.assertNotIn('enabled BOOLEAN', ddl, '«не подписан» — отсутствие строки, а не FALSE')


if __name__ == '__main__':
    unittest.main()
