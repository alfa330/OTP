"""«Кадры» → «Настройки»: правила оформления по умолчанию (hr_office/defaults.py). Просьба владельца 19.11.2026:
«раздел настройки, чтобы можно было задавать правило — для этих по умолчанию этот ИП и т. д.».

Отделы и компании в тестах выдуманы."""
import contextlib
import unittest
from unittest import mock

from flask import Flask, jsonify

from hr_office import defaults, queries, schema, service
from hr_office.routes import build_hr_office_blueprint

NAMES = {1: 'СЗоВ — Служба заботы о водителях', 2: 'Отдел продаж', 3: 'Тез КЦ', 4: 'Бухгалтерия'}


def rule(rule_id, departments, scope='all', kind=None, company=None, title=None):
    return {'id': rule_id, 'department_ids': list(departments), 'role_scope': scope, 'employment_type': kind,
            'company_id': company, 'job_title': title}


class ResolveTests(unittest.TestCase):
    def test_role_rule_beats_whole_department_and_fields_merge(self):
        rules_list = [rule(1, [1, 2], kind='gph', company=10),
                      rule(2, [1], scope='line', company=20, title='Оператор СЗоВ')]
        operator = defaults.resolve(rules_list, 1, 'operator')
        self.assertEqual((operator['employment_type'], operator['company_id'], operator['job_title']),
                         ('gph', 20, 'Оператор СЗоВ'))            # вид — из «Всем», компания — из «Операторам»
        self.assertEqual(operator['hiring_rule']['id'], 2)
        self.assertEqual(operator['sources'], {'company_id': 2, 'job_title': 2, 'employment_type': 1})
        sv = defaults.resolve(rules_list, 1, 'sv')
        self.assertEqual((sv['employment_type'], sv['company_id'], sv['job_title']), ('gph', 10, None))

    def test_trainees_and_trainers_count_as_line(self):
        rules_list = [rule(1, [3], scope='line', company=5)]
        for role in ('operator', 'trainee', 'trainer', 'OPERATOR'):
            self.assertEqual(defaults.resolve(rules_list, 3, role)['company_id'], 5, role)
        for role in ('sv', 'admin', None):
            self.assertIsNone(defaults.resolve(rules_list, 3, role)['company_id'], role)

    def test_no_department_no_default(self):
        self.assertIsNone(defaults.resolve([rule(1, [1], kind='gph')], None, 'operator')['employment_type'])
        self.assertIsNone(defaults.resolve([rule(1, [1], kind='gph')], 9, 'operator')['employment_type'])

    def test_narrower_rule_wins_within_same_scope(self):
        rules_list = [rule(1, [1, 2, 3], title='Сотрудник'), rule(2, [1], kind='of')]
        self.assertEqual(defaults.ordered(rules_list)[0]['id'], 2)

    def test_inactive_company_falls_through_to_the_next_rule(self):
        # «Операторам» — недействующая компания: операторы получают действующую компанию правила «Всем», а не пусто.
        rules_list = [rule(1, [1, 2], kind='gph', company=10), rule(2, [1], scope='line', company=30)]
        operator = defaults.resolve(rules_list, 1, 'operator', inactive_companies={30})
        self.assertEqual((operator['company_id'], operator['sources']['company_id']), (10, 1))
        self.assertEqual(defaults.resolve(rules_list, 1, 'operator')['company_id'], 30)

    def test_old_supervisor_role_counts_as_sv(self):
        rules_list = [rule(1, [1], scope='sv', title='Супервайзер')]
        for role in ('sv', 'supervisor', 'SUPERVISOR'):
            self.assertEqual(defaults.resolve(rules_list, 1, role)['job_title'], 'Супервайзер', role)


class CleanTests(unittest.TestCase):
    def clean(self, payload):
        return defaults.clean(payload, department_ids={1, 2, 3}, company_ids={10, 20})

    def test_valid_rule(self):
        fields, errors = self.clean({'department_ids': ['2', 1, 2, 99], 'role_scope': 'line', 'employment_type': 'gph',
                                     'company_id': '10', 'job_title': '  Оператор   колл-центра '})
        self.assertEqual(errors, {})
        self.assertEqual(fields, {'department_ids': [1, 2], 'role_scope': 'line', 'employment_type': 'gph',
                                  'company_id': 10, 'job_title': 'Оператор колл-центра'})

    def test_errors_are_per_field(self):
        _fields, errors = self.clean({'department_ids': [], 'role_scope': 'boss', 'employment_type': 'xx',
                                      'company_id': 77, 'job_title': 'я' * 201})
        self.assertEqual(set(errors), {'department_ids', 'role_scope', 'employment_type', 'company_id', 'job_title'})

    def test_rule_must_set_something(self):
        _fields, errors = self.clean({'department_ids': [1], 'role_scope': 'all'})
        self.assertIn('Задайте', errors['employment_type'])

    def test_departments_must_be_a_list(self):
        # Строка «12» иначе разобралась бы по символам — в отделы 1 и 2.
        fields, errors = self.clean({'department_ids': '12', 'employment_type': 'gph'})
        self.assertEqual(fields['department_ids'], [])
        self.assertIn('department_ids', errors)


class ConflictTests(unittest.TestCase):
    def test_same_scope_same_field_same_department_is_a_clash(self):
        fields = {'department_ids': [1, 4], 'role_scope': 'all', 'employment_type': None, 'company_id': 20,
                  'job_title': None}
        text = defaults.conflicts(fields, [rule(7, [1, 2], company=10)], NAMES)
        # Названо мешающее правило — как в списке, и что сделать.
        self.assertEqual(text, 'Компанию в отделе «СЗоВ» уже задаёт правило «СЗоВ и Отдел продаж (всем)». '
                               'Поменяйте то правило или выберите другое «Кому в отделе».')
        two = defaults.conflicts(dict(fields, job_title='Оператор'),
                                 [rule(7, [1, 2], company=10), rule(8, [4], title='Бухгалтер')], NAMES)
        self.assertIn('; должность в отделе «Бухгалтерия» уже задаёт правило «Бухгалтерия (всем)»', two)
        self.assertTrue(two.endswith('Поменяйте те правила или выберите другое «Кому в отделе».'))

    def test_other_scope_or_other_field_is_fine(self):
        fields = {'department_ids': [1], 'role_scope': 'line', 'employment_type': None, 'company_id': 20,
                  'job_title': None}
        self.assertEqual(defaults.conflicts(fields, [rule(7, [1], company=10)], NAMES), '')
        fields = dict(fields, role_scope='all', company_id=None, job_title='Оператор')
        self.assertEqual(defaults.conflicts(fields, [rule(7, [1], company=10)], NAMES), '')


class TextTests(unittest.TestCase):
    def test_label_and_description(self):
        # «для операторов отдела «Тез КЦ»»: названия отделов не склоняются, поэтому — в кавычках.
        self.assertEqual(defaults.label(rule(1, [3], scope='line'), NAMES), 'операторов отдела «Тез КЦ»')
        self.assertEqual(defaults.label(rule(1, [1, 2]), NAMES), 'отделов «СЗоВ» и «Отдел продаж»')
        self.assertEqual(defaults.label(rule(1, [1, 2, 4], scope='sv'), NAMES),
                         'супервайзеров отделов «СЗоВ», «Отдел продаж» и «Бухгалтерия»')
        companies = {10: {'id': 10, 'name': 'Eazy Go Rent', 'kind': 'ip'}}
        text = defaults.describe(rule(1, [1, 2], kind='gph', company=10, title='Оператор'), NAMES, companies)
        self.assertEqual(text, 'СЗоВ и Отдел продаж (всем) → ГПХ, ИП «Eazy Go Rent», должность «Оператор»')


class SeedTests(unittest.TestCase):
    def test_old_code_defaults_become_rules(self):
        index = queries.company_index([{'id': 10, 'name': 'Eazy Go Rent', 'kind': 'ip', 'is_active': True},
                                       {'id': 20, 'name': 'GaBa', 'kind': 'ip', 'is_active': True}])
        rows = defaults.seed_rows({'szov': 1, 'op': 2, 'tez': 3}, index)
        hiring = [row for row in rows if row['company_id']]
        self.assertEqual([(row['department_ids'], row['role_scope'], row['employment_type'], row['company_id'])
                          for row in hiring], [([1, 2], 'all', 'gph', 10), ([3], 'line', 'gph', 20)])
        titles = {tuple(row['department_ids']): row['job_title'] for row in rows if row['job_title']}
        self.assertEqual(titles[(1,)], 'Оператор службы заботы о водителях')
        self.assertTrue(all(row['role_scope'] == 'line' for row in rows if row['job_title']))
        # Отделов, которых в базе нет (remote_cc, front_office), правила не получают.
        self.assertFalse([row for row in rows if not set(row['department_ids']) <= {1, 2, 3}])

    def test_seeded_departments_are_in_form_order(self):
        # Форма сохраняет отделы по порядку — засеянное правило так же, иначе «Сохранить» без правок было бы правкой.
        rows = defaults.seed_rows({'szov': 9, 'op': 4}, queries.company_index([]))
        self.assertEqual(rows[0]['department_ids'], [4, 9])


def ctx(**fields):
    base = {'user_id': 7, 'role': 'admin', 'is_hr': False, 'is_super': False, 'is_admin': False,
            'is_global_admin': False, 'is_supervisor': False, 'department_id': None, 'headed_department_ids': []}
    base.update(fields)
    return base


class _Cursor:
    def __init__(self):
        self.log = []

    def execute(self, sql, params=None):
        self.log.append((' '.join(sql.split()), params))

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class SaveRuleTests(unittest.TestCase):
    def setUp(self):
        self.audit = []
        self.saved = []
        companies = [{'id': 10, 'name': 'Eazy Go Rent', 'kind': 'ip', 'is_active': True},
                     {'id': 20, 'name': 'GaBa', 'kind': 'ip', 'is_active': True}]
        departments = [{'id': key, 'name': value, 'code': '', 'active': True} for key, value in NAMES.items()]
        self.rules = [rule(1, [1, 2], kind='gph', company=10)]
        patches = [
            mock.patch.object(queries, 'list_default_rules', lambda _c: [dict(item) for item in self.rules]),
            mock.patch.object(queries, 'department_rows', lambda _c, active_only=False: departments),
            mock.patch.object(queries, 'list_companies', lambda _c, include_inactive=True: companies),
            mock.patch.object(queries, 'save_default_rule',
                              lambda _c, rule_id, fields, actor: self.saved.append((rule_id, fields)) or (rule_id or 5)),
            mock.patch.object(queries, 'delete_default_rule', lambda _c, rule_id: True),
            mock.patch.object(queries, 'audit', lambda _c, **kwargs: self.audit.append(kwargs)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_only_hr_and_superadmins_edit(self):
        with self.assertRaises(service.HrError) as caught:
            service.save_default_rule(_Cursor(), ctx(is_supervisor=True), None, {})
        self.assertEqual(caught.exception.status, 403)
        self.assertTrue(service.capabilities(ctx(is_hr=True))['settings'])
        self.assertFalse(service.capabilities(ctx(is_global_admin=True))['settings'])

    def test_clash_is_reported_at_departments(self):
        with self.assertRaises(service.HrError) as caught:
            service.save_default_rule(_Cursor(), ctx(is_hr=True), None,
                                      {'department_ids': [2], 'role_scope': 'all', 'company_id': 20})
        self.assertEqual(caught.exception.code, 'HR_RULE_INVALID')
        self.assertIn('Отдел продаж', caught.exception.fields['department_ids'])
        self.assertEqual(self.saved, [])

    def test_new_rule_is_saved_and_logged(self):
        service.save_default_rule(_Cursor(), ctx(is_hr=True), None,
                                  {'department_ids': [3], 'role_scope': 'line', 'employment_type': 'gph',
                                   'company_id': 20})
        self.assertEqual(self.saved[0][1]['department_ids'], [3])
        entry = self.audit[-1]
        self.assertEqual(entry['section'], schema.SETTINGS_AUDIT_SECTION[0])
        self.assertEqual(entry['summary'], 'Правило оформления добавлено: Тез КЦ (операторам) → ГПХ, ИП «GaBa»')

    def test_unchanged_edit_is_not_logged_and_delete_is(self):
        service.save_default_rule(_Cursor(), ctx(is_super=True), 1,
                                  {'department_ids': [1, 2], 'role_scope': 'all', 'employment_type': 'gph',
                                   'company_id': 10})
        self.assertEqual(self.audit, [])
        service.delete_default_rule(_Cursor(), ctx(is_super=True), 1)
        self.assertIn('Правило оформления удалено: СЗоВ и Отдел продаж (всем)', self.audit[-1]['summary'])

    def test_edit_is_logged_as_was_and_now(self):
        service.save_default_rule(_Cursor(), ctx(is_hr=True), 1,
                                  {'department_ids': [1], 'role_scope': 'all', 'employment_type': 'gph', 'company_id': 10})
        self.assertEqual(self.audit[-1]['summary'],
                         'Правило оформления изменено. Было: СЗоВ и Отдел продаж (всем) → ГПХ, ИП «Eazy Go Rent». '
                         'Стало: СЗоВ (всем) → ГПХ, ИП «Eazy Go Rent»')

    def test_unchanged_edit_writes_nothing_even_if_departments_were_stored_out_of_order(self):
        self.rules = [rule(1, [2, 1], kind='gph', company=10)]
        service.save_default_rule(_Cursor(), ctx(is_hr=True), 1,
                                  {'department_ids': [1, 2], 'role_scope': 'all', 'employment_type': 'gph',
                                   'company_id': 10})
        self.assertEqual((self.saved, self.audit), ([], []))

    def test_waiting_counts_only_people_the_rule_fills_now(self):
        self.rules = [rule(1, [1], kind='gph', company=10), rule(2, [1], scope='line', title='Оператор СЗоВ')]
        base = {'department_id': 1, 'role': 'operator', 'contract_id': None, 'company_name': None,
                'employment_type': None, 'job_title': None, 'contract_job_title': None}
        people = [
            dict(base),                                               # оба правила
            dict(base, job_title='Старший оператор'),                 # своя должность — только правило 1
            dict(base, company_name='ИП Алиева'),                     # компания уже есть — только должность
            dict(base, contract_id=5),                                # договор без должности — должность в табель/АВР
            dict(base, contract_id=6, contract_job_title='Оператор'), # ничего
        ]
        with mock.patch.object(queries, 'list_people', lambda _c: people):
            payload = service.settings_payload(_Cursor(), ctx(is_hr=True))
        self.assertEqual({item['id']: item['waiting'] for item in payload['rules']}, {1: 2, 2: 3})

    def test_missing_rule_is_404(self):
        with self.assertRaises(service.HrError) as caught:
            service.delete_default_rule(_Cursor(), ctx(is_hr=True), 99)
        self.assertEqual(caught.exception.status, 404)

    def test_rules_with_type_or_company_come_first(self):
        # «Этим — этот ИП» — главное в настройках: такие правила наверху, правила с одной должностью — ниже.
        self.rules = [rule(1, [1], scope='line', title='Оператор'), rule(2, [3], scope='line', company=20),
                      rule(3, [2], title='Менеджер'), rule(4, [1, 2], kind='gph', company=10)]
        with mock.patch.object(queries, 'list_people', lambda _c: []):
            payload = service.settings_payload(_Cursor(), ctx(is_hr=True))
        self.assertEqual([item['id'] for item in payload['rules']], [4, 2, 3, 1])


class AuditSectionTests(unittest.TestCase):
    def test_alter_moves_fallback_rows_to_settings(self):
        cursor = _Cursor()
        self.assertTrue(schema.ensure_audit_sections(cursor))
        sql = [statement for statement, _params in cursor.log]
        self.assertTrue(any('ADD CONSTRAINT hr_audit_log_section_check' in statement and "'settings'" in statement
                            for statement in sql))
        self.assertIn("UPDATE hr_audit_log SET section = 'settings' WHERE section = 'intake' AND action = 'rule'", sql)

    def test_rules_table_has_no_foreign_key_on_users(self):
        # REFERENCES users при первой выкладке взял бы блокировку users — входы и правки учёток ждали бы CREATE.
        self.assertNotIn('REFERENCES users', schema.DEFAULT_RULES_TABLE)


class _Db:
    @contextlib.contextmanager
    def _get_cursor(self):
        yield _Cursor()


class RouteTests(unittest.TestCase):
    def client(self, viewer):
        app = Flask(__name__)
        app.register_blueprint(build_hr_office_blueprint(
            db=_Db(), require_api_key=lambda handler: handler, build_cors_preflight_response=lambda: jsonify({}),
            resolve_requester=lambda: (1, (1, 'Кто-то', 'login', viewer['role']), None),
            viewer_context=lambda user_id, requester: dict(viewer, user_id=user_id),
            update_user_field=lambda *_args: None))
        return app.test_client()

    def test_supervisor_has_no_settings(self):
        response = self.client(ctx(role='super_admin', is_supervisor=True)).get('/api/hr_office/settings/rules')
        self.assertEqual((response.status_code, response.get_json()['code']), (403, 'HR_FORBIDDEN'))

    def test_settings_page_for_superadmin(self):
        with mock.patch.object(service, 'settings_payload', lambda _c, _ctx: {'rules': []}):
            response = self.client(ctx(role='super_admin', is_super=True)).get('/api/hr_office/settings/rules')
        self.assertEqual((response.status_code, response.get_json()), (200, {'rules': [], 'saved_id': None}))

    def test_saved_rule_id_comes_back(self):
        # «Настройки» оставляют только что сохранённое правило выбранным в списке.
        with mock.patch.object(service, 'settings_payload', lambda _c, _ctx: {'rules': []}), \
                mock.patch.object(service, 'save_default_rule', lambda _c, _ctx, rule_id, _body: rule_id or 42):
            client = self.client(ctx(role='super_admin', is_super=True))
            created = client.post('/api/hr_office/settings/rules', json={})
            edited = client.put('/api/hr_office/settings/rules/7', json={})
        self.assertEqual((created.get_json()['saved_id'], edited.get_json()['saved_id']), (42, 7))

    def test_defaults_for_create_window(self):
        seen = {}

        def fake(_cursor, _ctx, department_id, role):
            seen.update(department_id=department_id, role=role)
            return {'company': 'ИП «GaBa»'}
        with mock.patch.object(service, 'default_for_new', fake):
            response = self.client(ctx(role='super_admin', is_super=True)).get(
                '/api/hr_office/defaults?department_id=3&role=operator')
        self.assertEqual(response.get_json(), {'default': {'company': 'ИП «GaBa»'}})
        self.assertEqual(seen, {'department_id': '3', 'role': 'operator'})

    def test_only_hr_changes_rules(self):
        client = self.client(ctx(role='super_admin', is_supervisor=True, is_global_admin=True))
        for method, url in (('post', '/api/hr_office/settings/rules'), ('put', '/api/hr_office/settings/rules/1'),
                            ('delete', '/api/hr_office/settings/rules/1')):
            response = getattr(client, method)(url, json={'department_ids': [1], 'company_id': 10})
            self.assertEqual(response.status_code, 403, method)

    def test_settings_journal_reads_the_fallback_section(self):
        seen = {}

        def fake(_cursor, **kwargs):
            seen.update(kwargs)
            return [], 0
        # Первый запрос поднимает схему (ensure) — здесь она «не смогла» расширить CHECK журнала.
        with mock.patch.object(queries, 'list_audit', fake), mock.patch.object(schema, 'ensure', lambda _db: None), \
                mock.patch.object(schema, 'SETTINGS_AUDIT_SECTION', ['intake']):
            self.client(ctx(role='super_admin', is_super=True)).get('/api/hr_office/journal?section=settings')
        self.assertEqual((seen['section'], seen['action']), ('intake', 'rule'))

    def test_settings_journal_section_exists(self):
        with mock.patch.object(queries, 'list_audit', lambda *_a, **_k: ([], 0)):
            ok = self.client(ctx(role='super_admin', is_super=True)).get('/api/hr_office/journal?section=settings')
        self.assertEqual(ok.status_code, 200)
        denied = self.client(ctx(role='super_admin', is_supervisor=True)).get('/api/hr_office/journal?section=settings')
        self.assertEqual(denied.status_code, 403)


if __name__ == '__main__':
    unittest.main()
