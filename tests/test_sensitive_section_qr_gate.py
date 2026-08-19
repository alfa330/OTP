# -*- coding: utf-8 -*-
"""QR-подтверждение сессии на входе в «Обращения» и «Вики».

Раздел «Мои оценки» давно открывает записи и переписки только после того, как
админ или супервайзер подтвердил QR-код оператора: доступ живёт до конца ЭТОЙ
сессии. Тем же ключом закрыты теперь два раздела целиком (решение владельца
19.08.2026) — «Обращения» (переписка по живым водителям) и «Вики» (база знаний
компании).

Проверяется именно HTTP-гейт, а не намерение: спрятанный пункт меню доступом не
является, оба раздела открываются прямым адресом. Отдельно фиксируются две
вещи, которые молча ломаются при рефакторинге:

  * порядок проверок — «раздел вам не выдан» обязан отвечать раньше, чем «нужен
    QR», иначе человеку предложат подтвердить доступ к тому, чего у него нет;
  * вики спрашивает подтверждение НА СВОЁМ курсоре: свой коннект стоил бы двух
    слотов пула на один запрос, а пул общий и небольшой.
"""

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from crm import access as crm_access  # noqa: E402
from crm import queries as crm_queries  # noqa: E402
from crm import schema as crm_schema  # noqa: E402
from crm.routes import build_crm_blueprint  # noqa: E402
from wiki import access as wiki_access  # noqa: E402
from wiki import queries as wiki_queries  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402


QR_CODE = 'SENSITIVE_ACCESS_REQUIRED'


def crm_ctx(role='operator', user_id=10, department_code='szov', headed=()):
    return {
        'user_id': user_id,
        'name': 'Тест',
        'role': role,
        'department_id': 1,
        'department_code': department_code,
        'headed_department_ids': list(headed),
        'headed_department_codes': ['szov'] if headed else [],
        'group_ids': [],
    }


def wiki_ctx(role='operator', user_id=10, headed=(), wiki_enabled=True):
    return {
        'user_id': user_id,
        'otp_role': role,
        'department_id': 1,
        'direction_id': None,
        'headed_department_ids': list(headed),
        'group_ids': [],
        'wiki_roles': [],
        'access_mode': 'auto',
        'wiki_enabled': wiki_enabled,
    }


class _GateRecorder:
    """Подменяет ключ портала и запоминает, о ком и как его спросили."""

    def __init__(self, granted):
        self.granted = granted
        self.calls = []

    def __call__(self, user_id, cursor=None):
        self.calls.append({'user_id': user_id, 'cursor': cursor})
        return self.granted


class _Harness:
    def _cursor_and_db(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        cursor.rowcount = 0

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor
        return cursor, db

    def _patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)


class CrmGateHarness(_Harness):
    def build(self, context, granted=False):
        _cursor, db = self._cursor_and_db()
        self._patch(crm_queries, 'load_access_context', lambda _c, _uid: dict(context))
        # Схема «не развёрнута»: /ping тогда не считает счётчики по моку и
        # отвечает валидным JSON. Здесь важен код ответа, а не сводка.
        self._patch(crm_schema, 'schema_is_ready', lambda _c: False)

        gate = _GateRecorder(granted)
        app = Flask(__name__)
        app.register_blueprint(build_crm_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=gate,
        ))
        app.config['TESTING'] = True
        return app.test_client(), gate


class WikiGateHarness(_Harness):
    def build(self, context, granted=False):
        _cursor, db = self._cursor_and_db()
        self._patch(wiki_queries, 'load_access_context', lambda _c, _uid: dict(context))

        gate = _GateRecorder(granted)
        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=gate,
            client_ip=lambda: '127.0.0.1',
        ))
        app.config['TESTING'] = True
        return app.test_client(), gate


@unittest.skipIf(Flask is None, 'flask не установлен')
class CrmQrGateTest(CrmGateHarness, unittest.TestCase):
    def test_operator_without_confirmation_is_stopped_everywhere(self):
        """Закрыт весь раздел, а не только чтение списка."""
        client, _gate = self.build(crm_ctx())
        for method, url in (('get', '/api/crm/ping'),
                            ('get', '/api/crm/tickets'),
                            ('get', '/api/crm/meta'),
                            ('post', '/api/crm/tickets'),
                            ('post', '/api/crm/tickets/1/messages'),
                            ('get', '/api/crm/tickets/1')):
            response = getattr(client, method)(url, json={})
            self.assertEqual(response.status_code, 403, '%s %s' % (method, url))
            self.assertEqual(response.get_json().get('code'), QR_CODE, url)

    def test_operator_with_confirmation_passes(self):
        client, gate = self.build(crm_ctx(), granted=True)
        response = client.get('/api/crm/ping')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([call['user_id'] for call in gate.calls], [10])

    def test_supervisor_and_admin_are_not_asked_for_qr(self):
        """У СВ, админа и главы отдела подтверждать доступ не у кого."""
        for context in (crm_ctx(role='sv'),
                        crm_ctx(role='admin', department_code='op'),
                        crm_ctx(role='super_admin', department_code='op'),
                        crm_ctx(role='operator', headed=[1])):
            with self.subTest(role=context['role'], headed=context['headed_department_ids']):
                client, gate = self.build(context, granted=False)
                self.assertEqual(client.get('/api/crm/ping').status_code, 200)
                self.assertEqual(gate.calls, [], 'ключ спрашивали зря')

    def test_closed_section_answers_before_the_qr_gate(self):
        """Тренеру и чужому отделу — «раздел не открыт», а не «покажите QR»."""
        for context in (crm_ctx(role='trainer'), crm_ctx(role='operator', department_code='op')):
            with self.subTest(role=context['role'], dept=context['department_code']):
                client, gate = self.build(context, granted=False)
                response = client.get('/api/crm/ping')
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.get_json().get('code'), 'CRM_SECTION_CLOSED')
                self.assertEqual(gate.calls, [])

    def test_preflight_is_never_gated(self):
        """OPTIONS обязан отвечать без ключа: иначе браузер не пустит и сам отказ."""
        client, gate = self.build(crm_ctx(), granted=False)
        self.assertEqual(client.options('/api/crm/tickets').status_code, 204)
        self.assertEqual(gate.calls, [])


@unittest.skipIf(Flask is None, 'flask не установлен')
class WikiQrGateTest(WikiGateHarness, unittest.TestCase):
    def test_operator_without_confirmation_is_stopped_everywhere(self):
        client, _gate = self.build(wiki_ctx())
        for url in ('/api/wiki/ping', '/api/wiki/me', '/api/wiki/structure'):
            response = client.get(url)
            self.assertEqual(response.status_code, 403, url)
            self.assertEqual(response.get_json().get('code'), QR_CODE, url)

    def test_operator_with_confirmation_passes(self):
        client, gate = self.build(wiki_ctx(), granted=True)
        self.assertEqual(client.get('/api/wiki/ping').status_code, 200)
        self.assertEqual([call['user_id'] for call in gate.calls], [10])

    def test_gate_reuses_the_request_cursor(self):
        """Пул общий: свой коннект под проверку — два слота на один запрос."""
        client, gate = self.build(wiki_ctx(), granted=True)
        client.get('/api/wiki/ping')
        self.assertTrue(gate.calls)
        self.assertIsNotNone(gate.calls[0]['cursor'],
                             'вики обязана спрашивать ключ на своём курсоре')

    def test_supervisor_and_admin_are_not_asked_for_qr(self):
        for context in (wiki_ctx(role='sv'), wiki_ctx(role='admin'),
                        wiki_ctx(role='super_admin'), wiki_ctx(role='operator', headed=[1])):
            with self.subTest(role=context['otp_role'], headed=context['headed_department_ids']):
                client, gate = self.build(context, granted=False)
                self.assertEqual(client.get('/api/wiki/ping').status_code, 200)
                self.assertEqual(gate.calls, [], 'ключ спрашивали зря')

    def test_department_toggle_answers_before_the_qr_gate(self):
        """Отделу раздел не выдан — подтверждать нечего."""
        client, gate = self.build(wiki_ctx(wiki_enabled=False), granted=False)
        response = client.get('/api/wiki/ping')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_DEPARTMENT_DISABLED')
        self.assertEqual(gate.calls, [])

    def test_preflight_is_never_gated(self):
        client, gate = self.build(wiki_ctx(), granted=False)
        self.assertEqual(client.options('/api/wiki/structure').status_code, 204)
        self.assertEqual(gate.calls, [])


class GatePolicyTest(unittest.TestCase):
    """Правило «кому нужен QR» — одинаковое в обоих разделах."""

    def test_crm_policy(self):
        self.assertTrue(crm_access.requires_sensitive_qr(crm_ctx()))
        self.assertFalse(crm_access.requires_sensitive_qr(crm_ctx(role='sv')))
        self.assertFalse(crm_access.requires_sensitive_qr(crm_ctx(role='trainer')))
        self.assertFalse(crm_access.requires_sensitive_qr(
            crm_ctx(role='admin', department_code='op')))
        # Глава отдела — даже с базовой ролью оператора.
        self.assertFalse(crm_access.requires_sensitive_qr(crm_ctx(headed=[1])))

    def test_wiki_policy(self):
        self.assertTrue(wiki_access.requires_sensitive_qr('operator'))
        self.assertFalse(wiki_access.requires_sensitive_qr('sv'))
        self.assertFalse(wiki_access.requires_sensitive_qr('trainer'))
        self.assertFalse(wiki_access.requires_sensitive_qr('admin'))
        self.assertFalse(wiki_access.requires_sensitive_qr('superadmin'))
        self.assertFalse(wiki_access.requires_sensitive_qr('operator', is_department_head=True))

    def test_capabilities_tell_the_front_about_the_gate(self):
        """Фронт рисует замок по одному источнику правды, а не по роли."""
        self.assertTrue(crm_access.capabilities(crm_ctx())['requires_qr'])
        self.assertFalse(crm_access.capabilities(crm_ctx(role='sv'))['requires_qr'])


if __name__ == '__main__':
    unittest.main()
