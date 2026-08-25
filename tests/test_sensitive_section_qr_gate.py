# -*- coding: utf-8 -*-
"""QR-подтверждение сессии на входе в «Обращения», «Вики» и «Посылки».

Раздел «Мои оценки» давно открывает записи и переписки только после того, как
админ или супервайзер подтвердил QR-код оператора: доступ живёт до конца ЭТОЙ
сессии. Тем же ключом закрыты теперь три раздела целиком — «Обращения»
(переписка по живым водителям) и «Вики» (база знаний компании) с 19.08.2026 по
решению владельца, «Посылки» (ФИО и телефоны водителей в реестре) с 25.08.2026
по постановке задачи #240.

Проверяется именно HTTP-гейт, а не намерение: спрятанный пункт меню доступом не
является, оба раздела открываются прямым адресом. Отдельно фиксируются две
вещи, которые молча ломаются при рефакторинге:

  * порядок проверок — «раздел вам не выдан» обязан отвечать раньше, чем «нужен
    QR», иначе человеку предложат подтвердить доступ к тому, чего у него нет;
  * вики спрашивает подтверждение НА СВОЁМ курсоре: свой коннект стоил бы двух
    слотов пула на один запрос, а пул общий и небольшой.
"""

import ast
import sys
import textwrap
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BOT_PATH = ROOT / 'bot_schedule2.py'

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from crm import access as crm_access  # noqa: E402
from crm import queries as crm_queries  # noqa: E402
from crm import schema as crm_schema  # noqa: E402
from crm.routes import build_crm_blueprint  # noqa: E402
from parcels import access as parcels_access  # noqa: E402
from parcels import queries as parcels_queries  # noqa: E402
from parcels import schema as parcels_schema  # noqa: E402
from parcels.routes import build_parcels_blueprint  # noqa: E402
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


def parcels_ctx(role='operator', user_id=10, department_code='front_office', headed=()):
    """Портрет сотрудника раздела «Посылки». По умолчанию — менеджер фронт-офиса."""
    return {
        'user_id': user_id,
        'name': 'Тест',
        'role': role,
        'department_id': 909,
        'department_code': department_code,
        'city': 'Тараз',
        'headed_department_ids': list(headed),
        'headed_department_codes': [department_code] if headed else [],
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


class ParcelsGateHarness(_Harness):
    def build(self, context, granted=False):
        _cursor, db = self._cursor_and_db()
        self._patch(parcels_queries, 'load_access_context', lambda _c, _uid: dict(context))
        # Схема «не развёрнута»: /ping тогда не считает счётчики по моку и
        # отвечает валидным JSON. Здесь важен код ответа, а не сводка.
        self._patch(parcels_schema, 'schema_is_ready', lambda _c: False)

        gate = _GateRecorder(granted)
        app = Flask(__name__)
        app.register_blueprint(build_parcels_blueprint(
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


@unittest.skipIf(Flask is None, 'flask не установлен')
class ParcelsQrGateTest(ParcelsGateHarness, unittest.TestCase):
    def test_operator_without_confirmation_is_stopped_everywhere(self):
        """Закрыт весь раздел, а не только запись: СЗоВ приходит сюда читать."""
        client, _gate = self.build(parcels_ctx())
        for method, url in (('get', '/api/parcels/ping'),
                            ('get', '/api/parcels'),
                            ('get', '/api/parcels/offices'),
                            ('get', '/api/parcels/filters'),
                            ('get', '/api/parcels/1'),
                            ('post', '/api/parcels'),
                            ('post', '/api/parcels/driver-lookup'),
                            ('post', '/api/parcels/1/status'),
                            ('patch', '/api/parcels/1')):
            response = getattr(client, method)(url, json={})
            self.assertEqual(response.status_code, 403, '%s %s' % (method, url))
            self.assertEqual(response.get_json().get('code'), QR_CODE, url)

    def test_szov_operator_is_asked_for_qr_too(self):
        """Требование постановки: QR у ОБОИХ отделов, а не только у пишущего."""
        client, _gate = self.build(parcels_ctx(department_code='szov'))
        response = client.get('/api/parcels')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), QR_CODE)

    def test_operator_with_confirmation_passes(self):
        client, gate = self.build(parcels_ctx(), granted=True)
        self.assertEqual(client.get('/api/parcels/ping').status_code, 200)
        self.assertEqual([call['user_id'] for call in gate.calls], [10])

    def test_supervisor_and_admin_are_not_asked_for_qr(self):
        for context in (parcels_ctx(role='sv', department_code='szov'),
                        parcels_ctx(role='admin', department_code='op'),
                        parcels_ctx(role='super_admin', department_code='op'),
                        parcels_ctx(role='operator', headed=[909])):
            with self.subTest(role=context['role'], headed=context['headed_department_ids']):
                client, gate = self.build(context, granted=False)
                self.assertEqual(client.get('/api/parcels/ping').status_code, 200)
                self.assertEqual(gate.calls, [], 'ключ спрашивали зря')

    def test_closed_section_answers_before_the_qr_gate(self):
        """Тренеру и чужому отделу — «раздел не открыт», а не «покажите QR».

        Иначе человеку предлагают подтвердить доступ к тому, чего ему не
        выдавали, — тупик, из которого он не выйдет.
        """
        for context in (parcels_ctx(role='trainer'),
                        parcels_ctx(role='operator', department_code='op')):
            with self.subTest(role=context['role'], dept=context['department_code']):
                client, gate = self.build(context, granted=False)
                response = client.get('/api/parcels/ping')
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.get_json().get('code'), 'PARCELS_SECTION_CLOSED')
                self.assertEqual(gate.calls, [])

    def test_reader_passes_the_qr_gate_but_not_the_write_gate(self):
        """Порядок гейтов: сначала QR, потом «только чтение».

        Оператор СЗоВ с подтверждённой сессией обязан получить именно
        PARCELS_READ_ONLY: «покажите QR» на подтверждённой сессии читалось бы как
        сбой подтверждения.
        """
        client, _gate = self.build(parcels_ctx(department_code='szov'), granted=True)
        for method, url in (('post', '/api/parcels'),
                            ('post', '/api/parcels/driver-lookup'),
                            ('post', '/api/parcels/1/status'),
                            ('patch', '/api/parcels/1'),
                            ('delete', '/api/parcels/1')):
            response = getattr(client, method)(url, json={})
            self.assertEqual(response.status_code, 403, '%s %s' % (method, url))
            self.assertEqual(response.get_json().get('code'), 'PARCELS_READ_ONLY', url)

    def test_reader_still_reads(self):
        client, _gate = self.build(parcels_ctx(department_code='szov'), granted=True)
        self.assertEqual(client.get('/api/parcels/ping').status_code, 200)

    def test_preflight_is_never_gated(self):
        """OPTIONS обязан отвечать без ключа: иначе браузер не пустит и сам отказ."""
        client, gate = self.build(parcels_ctx(), granted=False)
        self.assertEqual(client.options('/api/parcels').status_code, 204)
        self.assertEqual(client.options('/api/parcels/1').status_code, 204)
        self.assertEqual(gate.calls, [])


class GatePolicyTest(unittest.TestCase):
    """Правило «кому нужен QR» — одинаковое во всех трёх разделах."""

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

    def test_parcels_policy(self):
        self.assertTrue(parcels_access.requires_sensitive_qr(parcels_ctx()))
        self.assertTrue(parcels_access.requires_sensitive_qr(
            parcels_ctx(department_code='szov')))
        self.assertFalse(parcels_access.requires_sensitive_qr(parcels_ctx(role='sv')))
        self.assertFalse(parcels_access.requires_sensitive_qr(
            parcels_ctx(role='admin', department_code='op')))
        # Глава отдела — даже с базовой ролью оператора.
        self.assertFalse(parcels_access.requires_sensitive_qr(parcels_ctx(headed=[909])))

    def test_capabilities_tell_the_front_about_the_gate(self):
        """Фронт рисует замок по одному источнику правды, а не по роли."""
        self.assertTrue(crm_access.capabilities(crm_ctx())['requires_qr'])
        self.assertFalse(crm_access.capabilities(crm_ctx(role='sv'))['requires_qr'])
        self.assertTrue(parcels_access.capabilities(parcels_ctx())['requires_qr'])
        self.assertFalse(parcels_access.capabilities(parcels_ctx(role='sv'))['requires_qr'])


def _approval_perimeter():
    """Функция периметра из монолита. Импортировать bot_schedule2 нельзя — он на
    старте поднимает пул к боевой базе, поэтому берём функции через ast."""
    from tests import source_cache
    source = BOT_PATH.read_text(encoding='utf-8-sig')
    module = source_cache.parse(source)
    namespace = {}
    for name in ('_normalize_user_role', '_sensitive_access_approval_error'):
        node = next(n for n in module.body
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(textwrap.dedent(ast.get_source_segment(source, node)), namespace)
    return namespace['_sensitive_access_approval_error']


SZOV, OP = 1, 367


class ApprovalPerimeterTest(unittest.TestCase):
    """Кто кому вправе подтвердить QR. Периметр — свой отдел."""

    @classmethod
    def setUpClass(cls):
        cls.check = staticmethod(_approval_perimeter())

    def verdict(self, *, role, approver_id=100, approver_dept=SZOV, headed=(),
                operator_dept=SZOV, operator_supervisor=None):
        return self.check(
            approver_role=role,
            approver_id=approver_id,
            approver_department_id=approver_dept,
            approver_headed_department_ids=list(headed),
            operator_department_id=operator_dept,
            operator_supervisor_id=operator_supervisor,
        )

    def test_super_admin_approves_anyone(self):
        self.assertIsNone(self.verdict(role='super_admin', operator_dept=OP))

    def test_global_admin_approves_anyone(self):
        """Админ без своего отдела — вне отделов вовсе."""
        self.assertIsNone(self.verdict(role='admin', operator_dept=OP))

    def test_supervisor_approves_the_whole_department(self):
        """Главное изменение: не «свои операторы», а весь свой отдел."""
        self.assertIsNone(self.verdict(role='sv', operator_supervisor=999))

    def test_supervisor_stops_at_the_department_border(self):
        error = self.verdict(role='sv', operator_dept=OP)
        self.assertIsNotNone(error)
        self.assertEqual(error[1], 403)
        self.assertIn('своего отдела', error[0])

    def test_supervisor_without_department_keeps_own_operators(self):
        """Прежнее право остаётся: иначе СВ без отдела в профиле потерял бы всех."""
        self.assertIsNone(self.verdict(role='sv', approver_dept=None,
                                       operator_supervisor=100))
        error = self.verdict(role='sv', approver_dept=None, operator_supervisor=999)
        self.assertIsNotNone(error)
        self.assertIn('не указан отдел', error[0])

    def test_supervisor_cannot_approve_operator_without_department(self):
        error = self.verdict(role='sv', operator_dept=None)
        self.assertIsNotNone(error)

    def test_department_head_approves_own_department(self):
        self.assertIsNone(self.verdict(role='admin', headed=[SZOV], operator_dept=SZOV))
        # Глава с базовой ролью оператора — тоже глава.
        self.assertIsNone(self.verdict(role='operator', headed=[SZOV], operator_dept=SZOV))

    def test_department_head_stops_at_the_department_border(self):
        """Назначение главой заменяет базовую роль: чужой отдел ему закрыт."""
        error = self.verdict(role='admin', headed=[SZOV], operator_dept=OP)
        self.assertIsNotNone(error)
        self.assertEqual(error[1], 403)
        self.assertIn('своего отдела', error[0])

    def test_head_of_two_departments_covers_both(self):
        self.assertIsNone(self.verdict(role='admin', headed=[SZOV, OP], operator_dept=OP))

    def test_trainer_and_operator_approve_nobody(self):
        for role in ('trainer', 'operator', 'trainee', ''):
            with self.subTest(role=role):
                error = self.verdict(role=role)
                self.assertIsNotNone(error)
                self.assertEqual(error[1], 403)
                self.assertIn('администратор', error[0])


if __name__ == '__main__':
    unittest.main()
