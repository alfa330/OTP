"""Пилот «Кадров»: раздел и всё новое из ТЗ HR «iCore 4» в общих окнах — суперадминам, отделу кадров и
учёткам списка rules.PILOT_USER_IDS; остальные видят портал как до ТЗ.

Решения владельца: 10.11.2026 — пилот на суперадминов и кадровика; 27.11.2026 — «Раздел кадры, нужно
открыть доступ для hr», «предоставь полный доступ»: весь отдел кадров (членство в отделе, роль любая —
глава admin, кадровики hr_manager, рекрутер operator) получает раздел целиком. Главам и СВ других отделов
«АВР» пока закрыт."""
import ast
import contextlib
import logging
import re
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask, jsonify

from hr_office import rules
from hr_office.routes import build_hr_office_blueprint
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / 'bot_schedule2.py'
# Роли отдела кадров на проде (27.11.2026): глава — admin, кадровики — hr_manager, рекрутер — operator.
HR_ROLES = ('hr_manager', 'admin', 'operator')
EVERY_CAPABILITY = ('hiring', 'timesheet', 'acts', 'acts_all', 'companies', 'statuses', 'settings')


class PilotRuleTests(unittest.TestCase):
    def test_super_admins_pass(self):
        self.assertTrue(rules.pilot_allows(1, 'super_admin'))
        self.assertTrue(rules.pilot_allows(1, ' Super_Admin '))

    def test_hr_department_passes_whatever_the_role(self):
        for role in HR_ROLES:
            self.assertTrue(rules.pilot_allows(5, role, is_hr=True), role)

    def test_role_without_the_department_is_not_hr(self):
        """Кадровика переведут в другой отдел — роль hr_manager у него останется, а раздела не будет."""
        self.assertFalse(rules.pilot_allows(2, 'hr_manager'))
        self.assertFalse(rules.pilot_allows(2, 'hr_manager', is_hr=False))

    def test_heads_and_supervisors_of_other_departments_stay_outside(self):
        for user_id, role in ((3, 'admin'), (4, 'sv'), (6, 'operator'), (None, 'hr_manager'), ('x', 'sv')):
            self.assertFalse(rules.pilot_allows(user_id, role), (user_id, role))

    def test_no_accounts_beyond_the_hr_department(self):
        """ИИН и суммы договоров вне отдела кадров — только суперадминам; новая учётка в списке — решение владельца."""
        self.assertEqual(rules.PILOT_USER_IDS, frozenset())

    def test_listed_accounts_pass(self):
        with mock.patch.object(rules, 'PILOT_USER_IDS', frozenset({777})):
            self.assertTrue(rules.pilot_allows(777, 'admin'))
            self.assertTrue(rules.pilot_allows('777', 'sv'))
            self.assertFalse(rules.pilot_allows(778, 'admin'))

    def test_end_of_pilot_opens_by_tz_roles(self):
        with mock.patch.object(rules, 'PILOT_USER_IDS', None):
            self.assertTrue(rules.pilot_allows(2, 'hr_manager'))
            self.assertTrue(rules.pilot_allows(4, 'sv'))


class _Cursor:
    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _Db:
    @contextlib.contextmanager
    def _get_cursor(self):
        yield _Cursor()


def _client(requester_id, role, *, is_hr=False, headed=()):
    app = Flask(__name__)
    app.register_blueprint(build_hr_office_blueprint(
        db=_Db(),
        require_api_key=lambda handler: handler,
        build_cors_preflight_response=lambda: jsonify({}),
        resolve_requester=lambda: (requester_id, (requester_id, 'Кто-то', 'login', role), None),
        viewer_context=lambda user_id, requester: {
            'user_id': user_id, 'role': requester[3], 'is_hr': is_hr, 'is_super': role == 'super_admin',
            'is_admin': role in ('admin', 'super_admin'), 'is_global_admin': False, 'is_supervisor': role == 'sv',
            'department_id': 1, 'headed_department_ids': list(headed)},
        update_user_field=lambda *_args: None,
    ))
    return app.test_client()


class RouteGateTests(unittest.TestCase):
    """Ручки «Кадров» отвечают только пилоту — для остальных раздела ещё нет, даже по прямой ссылке."""

    def test_hr_department_and_super_admins_pass_the_gate(self):
        clients = [(5, role, True) for role in HR_ROLES] + [(1, 'super_admin', False)]
        for requester_id, role, is_hr in clients:
            response = _client(requester_id, role, is_hr=is_hr).get('/api/hr_office/journal?section=nonexistent')
            # До ручки дошли: она отвечает своей ошибкой «нет такого раздела», а не пилотной 403.
            self.assertEqual(response.status_code, 400, role)
            self.assertNotEqual(response.get_json().get('code'), 'HR_PILOT', role)

    def test_hr_department_gets_the_whole_section(self):
        """«Полный доступ»: каждому из отдела кадров — все вкладки и права, как у суперадмина."""
        for role in HR_ROLES:
            response = _client(5, role, is_hr=True).get('/api/hr_office/context')
            self.assertEqual(response.status_code, 200, role)
            capabilities = response.get_json()['capabilities']
            self.assertEqual({key for key in EVERY_CAPABILITY if capabilities.get(key) is True},
                             set(EVERY_CAPABILITY), role)

    def test_outside_the_hr_department_gets_403(self):
        clients = ((2, 'hr_manager', ()), (3, 'admin', (10,)), (4, 'sv', ()))
        for requester_id, role, headed in clients:
            response = _client(requester_id, role, headed=headed).get('/api/hr_office/context')
            self.assertEqual(response.status_code, 403, role)
            self.assertEqual(response.get_json()['code'], 'HR_PILOT', role)


def _profile_flags(role, *, is_hr, headed=()):
    """Настоящие _hr_office_features_for и _hr_office_flags_for из монолита (флаги профиля: пункт меню
    «Кадры» и новое в общих окнах). Заглушка одна — членство в отделе кадров, оно читает базу."""
    source_cache.function_node(BOT, '_is_employee_accounting_manager')  # заглушка подменяет живую функцию
    names = ('_normalize_user_role', '_is_super_admin_role', '_is_supervisor_role',
             '_hr_office_features_for', '_hr_office_flags_for')
    module = ast.Module(body=[source_cache.function_copy(BOT, name) for name in names], type_ignores=[])
    namespace = {'logging': logging, '_is_employee_accounting_manager': lambda _user_id: is_hr}
    exec(compile(ast.fix_missing_locations(module), str(BOT), 'exec'), namespace)
    features = namespace['_hr_office_features_for'](5, role)
    return features, namespace['_hr_office_flags_for'](5, role, list(headed))


class ProfileFlagsTests(unittest.TestCase):
    def test_hr_department_sees_the_whole_section_and_the_new_windows(self):
        for role, headed in (('hr_manager', ()), ('admin', (1499,)), ('operator', ())):
            self.assertEqual(_profile_flags(role, is_hr=True, headed=headed), (True, (True, True)), role)

    def test_super_admin_as_before(self):
        self.assertEqual(_profile_flags('super_admin', is_hr=False), (True, (True, True)))

    def test_others_see_the_portal_as_before(self):
        for role, headed in (('admin', (10,)), ('sv', ()), ('supervisor', ()), ('hr_manager', ()), ('operator', ())):
            self.assertEqual(_profile_flags(role, is_hr=False, headed=headed), (False, (False, False)), role)


class SharedWindowsTests(unittest.TestCase):
    def test_shared_windows_stay_as_before_outside_the_pilot(self):
        modal = (ROOT / 'src' / 'components' / 'modals' / 'UserEditModal.jsx').read_text(encoding='utf-8')
        self.assertIn("const hrFeatures = user?.hr_office_features === true;", modal)
        self.assertIn(': LEGACY_STATUS_OPTIONS;', modal)
        self.assertEqual(modal.count('options={statusOptions}') + modal.count('{statusOptions.map('), 2)
        otmetki = (ROOT / 'src' / 'components' / 'group_late' / 'GroupLateBotView.jsx').read_text(encoding='utf-8')
        self.assertIn("{hrStatuses && row.employee && row.employee !== '—' && (", otmetki)
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        self.assertIn('hrStatuses={user?.hr_office_features === true}', app)

    def test_legacy_statuses_are_the_pre_tz_list(self):
        source = (ROOT / 'src' / 'utils' / 'employmentStatus.js').read_text(encoding='utf-8')
        block = source[source.index('export const LEGACY_STATUS_OPTIONS'):]
        block = block[:block.index('];')]
        self.assertEqual(re.findall(r"label: '([^']+)'", block),
                         ['Работает', 'Уволен', 'Б/С', 'Больничный', 'Ежегодный отпуск'])

    def test_no_names_in_code(self):
        """Репозиторий публичный: учётки пилота — только id прода, без ФИО."""
        source = (ROOT / 'hr_office' / 'rules.py').read_text(encoding='utf-8')
        pilot = source[source.index('# ── Пилот «Кадров»'):source.index('def pilot_allows')]
        self.assertIsNone(re.search(r'[А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+(?:ов|ев|ин|хан|ова|ева|ина)\b', pilot))


if __name__ == '__main__':
    unittest.main()
