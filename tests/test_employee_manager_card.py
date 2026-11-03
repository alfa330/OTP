# -*- coding: utf-8 -*-
"""Руководитель СВ и выше — поле карточки сотрудника (владелец 03.11.2026).

В «Оплате счетов» работают супервайзеры и выше, а руководителя заявки раздел берёт из
карточки (payments/queries.resolve_manager: users.supervisor_id, иначе глава отдела). У
оператора и стажёра руководитель — СВ их группы, его по-прежнему ставит только перевод в
группу; у СВ и выше группы нет, и руководителя им указывают в карточке админ или кадровик.
"""
import ast
import textwrap
import unittest
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT = source_cache.monolith_flat_path().read_text(encoding='utf-8-sig')
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8-sig')
USER_MODAL = (ROOT / 'src' / 'components' / 'modals' / 'UserEditModal.jsx').read_text(encoding='utf-8-sig')
PAYMENT_FORM = (ROOT / 'src' / 'components' / 'payments' / 'PaymentRequestForm.jsx').read_text(encoding='utf-8-sig')
UPDATE_USER = BOT.split('def admin_update_user():', 1)[1].split('def operator_change_own_rate():', 1)[0]


def _top_level(names):
    """Функции и константы монолита по именам — в одно пространство имён, без импорта."""
    lines = BOT.splitlines(keepends=True)
    namespace = {}
    for node in source_cache.parse(BOT).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            pass
        elif isinstance(node, ast.Assign) and any(getattr(t, 'id', None) in names for t in node.targets):
            pass
        else:
            continue
        exec(textwrap.dedent(''.join(lines[node.lineno - 1:node.end_lineno])), namespace)
    missing = set(names) - set(namespace)
    if missing:
        raise AssertionError('в монолите нет: %s' % ', '.join(sorted(missing)))
    return namespace


class _Users:
    """Подмена db.get_user: строка как у Database.get_user — роль [3], статус [11]."""

    def __init__(self, people):
        self.people = people

    def get_user(self, id):
        person = self.people.get(int(id))
        if person is None:
            return None
        role, status = person
        return (int(id), None, 'Имя', role, 'Отдел', None, None, 'login', None, None, True, status)


class ManagerAssignmentRuleTests(unittest.TestCase):
    def setUp(self):
        self.ns = _top_level({'_manager_assignment_error', '_normalize_user_role', 'GROUP_MANAGED_ROLES'})
        self.ns['db'] = _Users({
            2: ('super_admin', 'working'),
            3: ('supervisor', 'working'),
            4: ('admin', 'fired'),
            5: ('operator', 'working'),
            6: ('trainee', 'working'),
            7: ('accounting_manager', 'working'),
        })
        self.check = self.ns['_manager_assignment_error']

    def test_self_is_not_own_manager(self):
        self.assertEqual(self.check(50, 50), 'Сотрудник не может быть руководителем сам себе')

    def test_unknown_or_fired_manager_is_rejected(self):
        self.assertEqual(self.check(50, 999), 'Руководитель не найден')
        self.assertEqual(self.check(50, 4), 'Руководитель не найден')

    def test_operator_and_trainee_are_not_managers(self):
        self.assertEqual(self.check(50, 5), 'Руководителем назначают супервайзера и выше')
        self.assertEqual(self.check(50, 6), 'Руководителем назначают супервайзера и выше')

    def test_supervisor_and_above_are_accepted(self):
        self.assertIsNone(self.check(50, 2))
        self.assertIsNone(self.check(50, 3))
        self.assertIsNone(self.check(50, 7))

    def test_group_managed_roles_are_operator_and_trainee(self):
        self.assertEqual(self.ns['GROUP_MANAGED_ROLES'], ('operator', 'trainee'))


class _Cursor:
    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, None, None

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows


class ManagerCandidatesEndpointTests(unittest.TestCase):
    def _call(self, *, global_admin=False, hr=False):
        ns = _top_level({'get_manager_candidates', 'GROUP_MANAGED_ROLES', '_normalize_user_role'})
        cursor = _Cursor([(61, 'Директор', 'super_admin', 'Отдел'), (62, 'Глава отдела', 'admin', 'Отдел')])

        class _Db:
            def _get_cursor(self):
                from contextlib import contextmanager

                @contextmanager
                def manager():
                    yield cursor
                return manager()

        ns.update(
            db=_Db(), logging=__import__('logging'),
            jsonify=lambda payload: payload,
            _get_authenticated_requester=lambda: (70, (70, None, 'Админ', 'super_admin'), None),
            _is_global_admin_requester=lambda role, user_id: global_admin,
            _is_employee_accounting_manager=lambda user_id: hr,
        )
        # Декоратор маршрута при вырезке не нужен: функция зовётся напрямую.
        return ns['get_manager_candidates'](), cursor

    def test_global_admin_and_hr_get_supervisors_and_above_with_super_admins(self):
        for flags in ({'global_admin': True}, {'hr': True}):
            with self.subTest(**flags):
                result, cursor = self._call(**flags)
                self.assertEqual(result['status'], 'success')
                self.assertEqual([item['id'] for item in result['managers']], [61, 62])
                self.assertEqual(result['managers'][0]['role'], 'super_admin')
                self.assertEqual(cursor.params, (['operator', 'trainee'],))
                self.assertIn("COALESCE(u.status, 'working') <> 'fired'", cursor.sql)
                self.assertIn("lower(COALESCE(u.role, '')) <> ALL(%s)", cursor.sql)

    def test_others_are_forbidden(self):
        result, cursor = self._call()
        self.assertEqual(result, ({'error': 'Forbidden'}, 403))
        self.assertIsNone(cursor.sql)


class UpdateUserManagerFieldTests(unittest.TestCase):
    def test_operators_still_get_supervisor_only_from_group(self):
        self.assertIn("elif field == 'supervisor_id' and target_role in GROUP_MANAGED_ROLES:", UPDATE_USER)
        self.assertIn('Супервайзер назначается группой оператора', UPDATE_USER)

    def test_manager_value_is_checked_before_saving(self):
        self.assertIn('manager_error = _manager_assignment_error(user_id, value)', UPDATE_USER)
        check = UPDATE_USER.index('manager_error = _manager_assignment_error(user_id, value)')
        self.assertLess(check, UPDATE_USER.index('db.update_user(user_id, field, value, changed_by=requester_id)'))

    def test_only_global_admin_or_hr_sets_manager(self):
        self.assertIn('Руководителя сотрудника указывает администратор или кадровик', UPDATE_USER)
        rule = UPDATE_USER.split("if (field == 'supervisor_id' and target_role not in GROUP_MANAGED_ROLES", 1)[1]
        rule = rule.split('return jsonify', 1)[0]
        self.assertIn("target_role != 'trainer'", rule)
        self.assertIn('not _is_global_admin_requester(requester_role, requester_id)', rule)
        self.assertIn('not personnel_manager', rule)
        # Проверка прав стоит после того, как известен спрашивающий.
        self.assertLess(UPDATE_USER.index('personnel_manager = _is_employee_accounting_manager(requester_id)'),
                        UPDATE_USER.index('Руководителя сотрудника указывает администратор или кадровик'))


class CardManagerFieldTests(unittest.TestCase):
    def test_field_only_for_existing_non_operators_and_unscoped_requesters(self):
        rule = USER_MODAL.split('const canEditManager =', 1)[1].split(';', 1)[0]
        for part in ('isExistingUserEdit', 'isUnscopedRequester', '!isOperatorDraft(editedUser)',
                     '!isTraineeDraft(editedUser)', '!isTrainerDraft(editedUser)'):
            self.assertIn(part, rule)

    def test_candidates_exclude_self_operators_and_fired(self):
        options = USER_MODAL.split('const managerOptions = React.useMemo(', 1)[1].split('}, [managerCandidates', 1)[0]
        self.assertIn('Number(person.id) !== selfId', options)
        self.assertIn("!['operator', 'trainee'].includes(normalizeRole(person.role))", options)
        self.assertIn("String(person.status || '').toLowerCase() !== 'fired'", options)
        # записанный руководитель вне списка не пропадает из поля
        self.assertIn('options.unshift(', options)

    def test_pick_marks_draft_and_hint_lives_under_i(self):
        field = USER_MODAL.split('{canEditManager && (', 1)[1].split('{canShowOperatorRateControls && (', 1)[0]
        self.assertIn('manager_picked_by_hand: true', field)
        self.assertIn('<InfoHint text="Согласует заявки сотрудника в «Оплате счетов».', field)
        self.assertIn("import InfoHint from '../common/InfoHint';", USER_MODAL)

    def test_candidates_come_from_their_own_endpoint_with_card_lists(self):
        # /api/admin/users супер-админов не отдаёт, а руководитель часто именно он.
        self.assertIn('managerCandidates,', APP)
        self.assertNotIn('managerCandidates: adminUsers', APP)
        fetch = APP.split('const fetchManagerCandidates = async () => {', 1)[1].split('const fetchUserModalGroups', 1)[0]
        self.assertIn('/api/admin/manager_candidates', fetch)
        self.assertIn('!(isAdminLikeRole || isEmployeeAccountingManager)', fetch)
        on_open = APP.split("title: 'Изменение данных',", 1)[1].split('render:', 1)[0]
        self.assertIn('fetchManagerCandidates();', on_open)
        self.assertIn('if (showUserEditModal) {\n                    fetchManagerCandidates();', APP)

    def test_save_sends_manager_only_when_picked_by_hand(self):
        block = APP.split('if (editedUser.manager_picked_by_hand) {', 1)[1].split('// Department reassignment', 1)[0]
        self.assertIn("field: 'supervisor_id',", block)
        self.assertIn('value: nextManagerId', block)
        self.assertIn('if (nextManagerId !== prevManagerId) {', block)


class ManagerLabelTests(unittest.TestCase):
    """Записанный руководитель СВ и выше не должен называться «Супервайзером»."""

    ROLE_LABEL = "['operator', 'trainee'].includes(normalizeRole("

    def test_card_tile_label_by_role(self):
        summary = APP.split('const employeeCardSummary = (employee) => {', 1)[1].split("key: 'rate'", 1)[1]
        tile = summary.split("key: 'lead',", 1)[1].split('value: employee.supervisor_name', 1)[0]
        self.assertIn(self.ROLE_LABEL + "employee?.role)) ? 'Супервайзер' : 'Руководитель'", tile)

    def test_profile_label_by_role(self):
        props = APP.split('const profileViewProps = {', 1)[1].split('};', 1)[0]
        self.assertIn("supervisorLabel: " + self.ROLE_LABEL + "user?.role)) ? 'Супервайзер' : 'Руководитель'", props)
        profile = (ROOT / 'src' / 'components' / 'profile' / 'ProfileView.jsx').read_text(encoding='utf-8-sig')
        legacy = (ROOT / 'src' / 'components' / 'profile' / 'LegacyProfileView.jsx').read_text(encoding='utf-8-sig')
        self.assertEqual(profile.count("supervisorLabel = 'Супервайзер',"), 2)   # телефон и компьютер
        self.assertIn('workRow(supervisorLabel, profile?.supervisor_name)', profile)
        self.assertIn("label: supervisorLabel, value: profile.supervisor_name", profile)
        self.assertIn('>{supervisorLabel}</p>', legacy)


class PaymentFormManagerSourceTests(unittest.TestCase):
    def test_form_says_where_manager_comes_from(self):
        self.assertIn("dictionaries?.manager?.source === 'supervisor' ? 'из карточки сотрудника'", PAYMENT_FORM)
        self.assertIn("dictionaries?.manager?.source === 'department_head' ? 'глава отдела'", PAYMENT_FORM)
        self.assertIn('{managerName && <span className="text-slate-500"> · {managerSource}</span>}', PAYMENT_FORM)


if __name__ == '__main__':
    unittest.main()
