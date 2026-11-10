"""Пилот «Кадров» (решение владельца 10.11.2026): раздел и всё новое из ТЗ HR «iCore 4» в общих окнах —
пока только суперадминам и учёткам списка rules.PILOT_USER_IDS; остальные видят портал как до ТЗ."""
import contextlib
import re
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask, jsonify

from hr_office import rules
from hr_office.routes import build_hr_office_blueprint

ROOT = Path(__file__).resolve().parents[1]
PILOT_ID = next(iter(rules.PILOT_USER_IDS))


class PilotRuleTests(unittest.TestCase):
    def test_super_admins_and_listed_accounts_only(self):
        self.assertTrue(rules.pilot_allows(1, 'super_admin'))
        self.assertTrue(rules.pilot_allows(1, ' Super_Admin '))
        self.assertTrue(rules.pilot_allows(PILOT_ID, 'hr_manager'))
        self.assertTrue(rules.pilot_allows(str(PILOT_ID), 'hr_manager'))
        for user_id, role in ((2, 'hr_manager'), (3, 'admin'), (4, 'sv'), (None, 'hr_manager'), ('x', 'sv')):
            self.assertFalse(rules.pilot_allows(user_id, role), (user_id, role))

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


def _client(requester_id, role):
    app = Flask(__name__)
    app.register_blueprint(build_hr_office_blueprint(
        db=_Db(),
        require_api_key=lambda handler: handler,
        build_cors_preflight_response=lambda: jsonify({}),
        resolve_requester=lambda: (requester_id, (requester_id, 'Кто-то', 'login', role), None),
        viewer_context=lambda user_id, requester: {
            'user_id': user_id, 'role': requester[3], 'is_hr': role == 'hr_manager', 'is_super': role == 'super_admin',
            'is_admin': False, 'is_global_admin': False, 'is_supervisor': role == 'sv', 'department_id': 1,
            'headed_department_ids': []},
        update_user_field=lambda *_args: None,
    ))
    return app.test_client()


class RouteGateTests(unittest.TestCase):
    """Ручки «Кадров» отвечают только пилоту — для остальных раздела ещё нет, даже по прямой ссылке."""

    def test_other_hr_gets_403(self):
        response = _client(2, 'hr_manager').get('/api/hr_office/journal?section=intake')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'HR_PILOT')

    def test_pilot_accounts_pass_the_gate(self):
        for requester_id, role in ((PILOT_ID, 'hr_manager'), (1, 'super_admin')):
            response = _client(requester_id, role).get('/api/hr_office/journal?section=nonexistent')
            # До ручки дошли: она отвечает своей ошибкой «нет такого раздела», а не пилотной 403.
            self.assertEqual(response.status_code, 400, (requester_id, role))
            self.assertNotEqual(response.get_json().get('code'), 'HR_PILOT')


class ProfileAndWindowsTests(unittest.TestCase):
    def test_profile_flags_follow_the_pilot(self):
        source = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        flags = source[source.index('def _hr_office_flags_for('):]
        flags = flags[:flags.index('\n\n\n')]
        self.assertIn('if not _hr_office_features_for(user_id, role):\n        return False, False', flags)
        features = source[source.index('def _hr_office_features_for('):]
        self.assertIn('hr_office_rules.pilot_allows(user_id, _normalize_user_role(role))', features[:900])

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
