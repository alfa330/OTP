# -*- coding: utf-8 -*-
"""Право строить дерево ВНУТРИ своей ветки (тумблер «Может заводить подразделы»).

Решение владельца 27.08.2026. До него дерево разделов правил только носитель
способности can_manage_structure — директор и назначенный руками администратор
вики. Коммерческому директору выписали личное правило на его ветку со всеми
шестью правами и «вместе с подразделами», и он всё равно не мог завести
подраздел: шесть прав правила описывают СОДЕРЖИМОЕ, а способностью они не
становятся по построению (wiki/access.py: capabilities_from_grants). Отказ был
молчаливым — право выписано, в «Структуре» видно, а кнопки нет.

Почему право живёт в ПРАВИЛЕ, а не в должности. Способность глобальна: включив
can_manage_structure роли 'admin', мы открыли бы на запись всё пространство
целиком — чужие ветки, «Старую вики», корень пространства, — то есть отменили
бы решение владельца 21.08.2026 («к другому отделу он не может притронуться»).
Оно остаётся в силе, и первый набор здесь это сторожит.

Границы права (решение владельца 27.08.2026 — «заводить и править»):
  * заводить подразделы — внутри выданного раздела и всего, что под ним;
  * править — сами подразделы, то есть разделы, чей РОДИТЕЛЬ в этой ветке;
    выданный раздел-якорь не его: его открыл вышестоящий;
  * архив, публичность, владелец и перенос в другую ветку — не его.

Наборы герметичные: боевая база не читается. Распространение права по дереву
проверяет отдельный набор над настоящим SQL — tests/test_wiki_section_rights.py
(ManageSubsectionsSqlTest).
"""

import ast
import re
import unittest
from pathlib import Path

from tests.test_wiki_routes import ADMIN_ROLE, _RouteHarness, make_context
from wiki import queries, schema as wiki_schema, structure

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

ROOT = Path(__file__).resolve().parents[1]

# Дерево прода: 32 «Коммерческий отдел» → 1 «Коммерческий директор» → 19 «СЗоВ».
# Директору выдан раздел 1, значит строить он вправе в 1, 19 и ниже.
ANCHOR, BRANCH, OUTSIDE = 1, 19, 33

# SELECT в начале PATCH/DELETE: имя, отдел пространства, space_id, родитель,
# отдел раздела, публичность, статус, владелец.
def section_row(*, parent, scope='restricted', status='active', owner=None,
                department=None, name='Оператор'):
    return (name, None, 11, parent, department, scope, status, owner)


@unittest.skipIf(Flask is None, 'flask не установлен')
class BranchHolderBuildsTest(_RouteHarness, unittest.TestCase):
    """Держатель ветки: роль 'admin', способности управлять структурой НЕТ."""

    def _client(self, manage=(ANCHOR, BRANCH)):
        client, cursor = self.build(
            make_context('admin', department_id=1), manage_sections=manage)
        cursor.rowcount = 1
        self.created = []
        self.addCleanup(setattr, structure, 'create_section', structure.create_section)
        structure.create_section = lambda cursor, **kwargs: (
            self.created.append(kwargs), 77)[1]
        self.addCleanup(setattr, structure, 'department_branch_taken',
                        structure.department_branch_taken)
        structure.department_branch_taken = lambda cursor, **kwargs: None
        self.addCleanup(setattr, structure, 'free_section_slug',
                        structure.free_section_slug)
        structure.free_section_slug = lambda cursor, space_id, base, **kw: base
        self.addCleanup(setattr, structure, 'space_open_to', structure.space_open_to)
        structure.space_open_to = lambda cursor, space_id, departments: True
        # Родитель лежит в том же пространстве 11 — обычный случай.
        self.addCleanup(setattr, structure, 'section_exists', structure.section_exists)
        structure.section_exists = lambda cursor, sid: 11
        self.addCleanup(setattr, structure, 'section_would_cycle',
                        structure.section_would_cycle)
        structure.section_would_cycle = lambda cursor, sid, parent: False
        self.updated = []
        self.addCleanup(setattr, structure, 'update_section', structure.update_section)
        structure.update_section = lambda cursor, sid, fields: (
            self.updated.append((sid, fields)), True)[1]
        return client, cursor

    # ── Заводит подразделы ───────────────────────────────────────────────
    def test_creates_a_subsection_inside_the_branch(self):
        """То, ради чего всё и делалось."""
        client, cursor = self._client()
        cursor.fetchone.return_value = ('active',)   # пространство живо
        response = client.post('/api/wiki/sections', json={
            'name': 'Наставник', 'space_id': 11, 'parent_section_id': BRANCH})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(self.created[0]['parent_section_id'], BRANCH)

    def test_creates_inside_the_anchor_itself(self):
        """Выданный раздел — тоже место для подраздела, а не только его потомки."""
        client, cursor = self._client()
        cursor.fetchone.return_value = ('active',)
        response = client.post('/api/wiki/sections', json={
            'name': 'ОП', 'space_id': 11, 'parent_section_id': ANCHOR})
        self.assertEqual(response.status_code, 201)

    def test_refuses_a_section_at_the_top_of_the_space(self):
        """Корень пространства — новая ветка, а не подраздел.

        Право называется «может заводить подразделы» и читается буквально.
        """
        client, cursor = self._client()
        cursor.fetchone.return_value = ('active',)
        response = client.post('/api/wiki/sections',
                               json={'name': 'Своё', 'space_id': 11})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SECTION_OUTSIDE_BRANCH')
        self.assertEqual(self.created, [])

    def test_refuses_a_foreign_parent(self):
        """Соседняя ветка того же пространства — чужая."""
        client, cursor = self._client()
        cursor.fetchone.return_value = ('active',)
        response = client.post('/api/wiki/sections', json={
            'name': 'Чужое', 'space_id': 11, 'parent_section_id': OUTSIDE})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SECTION_OUTSIDE_BRANCH')
        self.assertEqual(self.created, [])

    def test_new_subsection_is_never_public(self):
        """Публичный раздел виден МИМО ветки — это выдача доступа всей компании.

        Отказом здесь отвечать нельзя: форма шлёт visibility_scope всегда.
        Поэтому значение принудительно приводится к restricted.
        """
        client, cursor = self._client()
        cursor.fetchone.return_value = ('active',)
        response = client.post('/api/wiki/sections', json={
            'name': 'Наставник', 'space_id': 11, 'parent_section_id': BRANCH,
            'visibility_scope': 'public', 'owner_user_id': 42})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.created[0]['visibility_scope'], 'restricted')
        self.assertIsNone(self.created[0]['owner_user_id'])

    def test_branch_department_must_have_the_space(self):
        """Ветку чужого клиента внутрь своей не заводят."""
        client, cursor = self._client()
        # Заглушка ИЗБИРАТЕЛЬНАЯ: границу пространства для самого человека
        # (departments=[1]) она пропускает, а отдел 560 — нет. Иначе отказ
        # приходил бы раньше, от _may_manage_section_here, и тест мерил бы
        # не ту проверку — код отказа у них общий.
        structure.space_open_to = lambda cursor, space_id, departments: (
            560 not in departments)
        cursor.fetchone.return_value = ('active',)
        response = client.post('/api/wiki/sections', json={
            'name': 'Тез КЦ', 'space_id': 11, 'parent_section_id': BRANCH,
            'department_id': 560})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_DEPARTMENT_SCOPE')

    # ── Правит подразделы ────────────────────────────────────────────────
    def test_renames_a_subsection(self):
        client, cursor = self._client()
        cursor.fetchone.return_value = section_row(parent=BRANCH)
        response = client.patch('/api/wiki/sections/30', json={
            'space_id': 11, 'name': 'Наставник', 'parent_section_id': BRANCH,
            'visibility_scope': 'restricted', 'department_id': None})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.updated[0][1]['name'], 'Наставник')

    def test_does_not_rename_the_anchor(self):
        """Раздел-якорь ему открыл вышестоящий — переписывать свою границу нельзя."""
        client, cursor = self._client()
        # У якоря родитель — 32 «Коммерческий отдел», его в выдаче нет.
        cursor.fetchone.return_value = section_row(parent=32, name='Коммерческий директор')
        response = client.patch('/api/wiki/sections/%d' % ANCHOR,
                                json={'space_id': 11, 'name': 'Моё'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SECTION_OUTSIDE_BRANCH')
        self.assertEqual(self.updated, [])

    def test_does_not_archive(self):
        """«Заводить и править» — архив уносит раздел вместе со статьями внутри."""
        client, cursor = self._client()
        cursor.fetchone.return_value = section_row(parent=BRANCH)
        response = client.delete('/api/wiki/sections/30')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'),
                         'WIKI_SECTION_ARCHIVE_FORBIDDEN')
        self.assertEqual(self.updated, [])

    def test_does_not_make_a_subsection_public(self):
        client, cursor = self._client()
        cursor.fetchone.return_value = section_row(parent=BRANCH)
        response = client.patch('/api/wiki/sections/30', json={
            'space_id': 11, 'name': 'Оператор', 'visibility_scope': 'public'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'),
                         'WIKI_SECTION_PUBLIC_FORBIDDEN')
        self.assertEqual(self.updated, [])

    def test_does_not_move_a_subsection_to_another_branch(self):
        client, cursor = self._client()
        cursor.fetchone.return_value = section_row(parent=BRANCH)
        response = client.patch('/api/wiki/sections/30', json={
            'space_id': 11, 'name': 'Оператор', 'parent_section_id': OUTSIDE})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SECTION_MOVE_FORBIDDEN')
        self.assertEqual(self.updated, [])

    def test_does_not_reassign_the_owner(self):
        client, cursor = self._client()
        cursor.fetchone.return_value = section_row(parent=BRANCH)
        response = client.patch('/api/wiki/sections/30', json={
            'space_id': 11, 'name': 'Оператор', 'owner_user_id': 42})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SECTION_OWNER_FORBIDDEN')

    def test_does_not_restore_from_archive(self):
        client, cursor = self._client()
        cursor.fetchone.return_value = section_row(parent=BRANCH, status='archived')
        response = client.patch('/api/wiki/sections/30',
                                json={'space_id': 11, 'status': 'active'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'),
                         'WIKI_SECTION_ARCHIVE_FORBIDDEN')

    def test_does_not_list_every_section_of_the_space(self):
        """GET /sections — справочник целиком, а не своя ветка."""
        client, _ = self._client()
        response = client.get('/api/wiki/sections?space_id=11')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('required'), 'can_manage_structure')

    def test_flat_refusal_without_any_branch(self):
        """Решение владельца 21.08.2026 в силе: должность 'admin' сама не строит.

        Пустая выдача — это и есть обычный руководитель. Отказ обязан назвать
        недостающую способность, а не «раздел не найден»: по required его
        разбирает фронт, и тексты остальных дверей структуры те же.
        """
        client, _ = self._client(manage=())
        for method, url in (('post', '/api/wiki/sections'),
                            ('patch', '/api/wiki/sections/30'),
                            ('delete', '/api/wiki/sections/30')):
            response = getattr(client, method)(
                url, json={'name': 'Своё', 'space_id': 11, 'parent_section_id': BRANCH})
            self.assertEqual(response.status_code, 403, '%s %s' % (method, url))
            self.assertEqual(response.get_json().get('required'),
                             'can_manage_structure', '%s %s' % (method, url))


@unittest.skipIf(Flask is None, 'flask не установлен')
class DirectorStillBuildsEverywhereTest(_RouteHarness, unittest.TestCase):
    """Носителю способности правка ничего не сузила."""

    def test_structure_manager_ignores_the_branch_set(self):
        client, cursor = self.build(make_context('admin', wiki_roles=[ADMIN_ROLE]),
                                    manage_sections=())
        cursor.rowcount = 1
        cursor.fetchone.return_value = ('active',)
        self.addCleanup(setattr, structure, 'create_section', structure.create_section)
        created = []
        structure.create_section = lambda cursor, **kwargs: (created.append(kwargs), 5)[1]
        self.addCleanup(setattr, structure, 'department_branch_taken',
                        structure.department_branch_taken)
        structure.department_branch_taken = lambda cursor, **kwargs: None
        self.addCleanup(setattr, structure, 'free_section_slug',
                        structure.free_section_slug)
        structure.free_section_slug = lambda cursor, space_id, base, **kw: base

        response = client.post('/api/wiki/sections',
                               json={'name': 'В корень', 'space_id': 11})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertIsNone(created[0]['parent_section_id'])

    def test_manage_set_is_not_even_computed_for_him(self):
        """None вместо множества: лишний запрос на каждом обращении ни к чему."""
        calls = []
        self.addCleanup(setattr, queries, 'manage_section_ids', queries.manage_section_ids)
        queries.manage_section_ids = lambda *a, **k: (calls.append(1), frozenset())[1]
        client, cursor = self.build(make_context('admin', wiki_roles=[ADMIN_ROLE]))
        cursor.fetchone.return_value = None
        client.post('/api/wiki/sections', json={'name': 'x', 'space_id': 11})
        self.assertEqual(calls, [], 'носителю способности выдачу считать незачем')


@unittest.skipIf(Flask is None, 'flask не установлен')
class GrantingTheToggleTest(_RouteHarness, unittest.TestCase):
    """Кто вправе ПЕРЕДАТЬ управление веткой."""

    def _grant(self, context, manage_sections=(), **extra):
        client, cursor = self.build(context, manage_sections=manage_sections)
        self.saved = []
        self.addCleanup(setattr, structure, 'upsert_section_rule',
                        structure.upsert_section_rule)
        structure.upsert_section_rule = lambda cursor, **kwargs: (
            self.saved.append(kwargs), 1)[1]
        self.addCleanup(setattr, structure, 'section_exists', structure.section_exists)
        structure.section_exists = lambda cursor, sid: 11
        # Раздел лежит в ветке СВОЕГО отдела: три границы выдачи (потолок,
        # отдел ветки, высота раздела) проверяет отдельный набор — здесь они
        # открыты, иначе тест мерил бы не тумблер, а их.
        self.addCleanup(setattr, structure, 'section_branch_department',
                        structure.section_branch_department)
        structure.section_branch_department = lambda cursor, sid: 1
        self.addCleanup(setattr, structure, 'section_role_levels',
                        structure.section_role_levels)
        structure.section_role_levels = lambda cursor: {}
        self.addCleanup(setattr, structure, 'subject_department',
                        structure.subject_department)
        structure.subject_department = lambda cursor, kind, sid: None
        cursor.fetchone.return_value = ('admin', 1)
        body = {'section_id': ANCHOR, 'subject_type': 'user', 'subject_id': 414,
                'can_read': True, 'can_create': True, 'can_edit': True,
                'grant_subsections': True}
        body.update(extra)
        return client.post('/api/wiki/access/section-rules', json=body)

    def test_structure_manager_grants_it(self):
        response = self._grant(make_context('admin', wiki_roles=[ADMIN_ROLE]),
                               manage_subsections=True)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertTrue(self.saved[0]['manage_subsections'])

    def test_branch_holder_cannot_pass_it_on(self):
        """Право, полученное из правила, дальше не передаётся.

        Иначе одна выдача сверху расползлась бы вниз по всей ветке сама, без
        чьего-либо решения, — та же лестница, что у шести прав («выписать можно
        только то, что умеешь сам»).
        """
        response = self._grant(make_context('admin', department_id=1),
                               manage_sections=(ANCHOR, BRANCH),
                               manage_subsections=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_GRANT_BEYOND_SELF')
        self.assertEqual(self.saved, [])

    def test_branch_holder_still_grants_ordinary_rights(self):
        """Отказ касается только тумблера дерева, а не выдачи доступа вообще."""
        response = self._grant(make_context('admin', department_id=1),
                               manage_sections=(ANCHOR, BRANCH))
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertFalse(self.saved[0]['manage_subsections'])


class SchemaTest(unittest.TestCase):
    """Миграция колонки. Образец и мотивы — tests/test_wiki_copy_protection.py."""

    def test_column_is_added_by_migration_not_only_by_create_table(self):
        """На проде таблица есть, и CREATE TABLE IF NOT EXISTS её не тронет."""
        altered = [s for s in wiki_schema._ORG_STATEMENTS
                   if 'manage_subsections' in s and 'ADD COLUMN IF NOT EXISTS' in s]
        self.assertEqual(len(altered), 1, 'ожидали ровно один ALTER на колонку')

    def test_default_is_off(self):
        """Правила в проде уже написаны, и миграция не смеет раздать по ним право."""
        statement = next(s for s in wiki_schema._ORG_STATEMENTS
                         if 'manage_subsections' in s)
        self.assertIn('DEFAULT FALSE', statement)
        self.assertIn('NOT NULL', statement)

    def test_statement_has_no_percent_sign(self):
        """_ORG_STATEMENTS уходит в execute СЫРОЙ: '%' уронил бы всю схему вики."""
        statement = next(s for s in wiki_schema._ORG_STATEMENTS
                         if 'manage_subsections' in s)
        self.assertNotIn('%', statement)

    def test_toggle_never_becomes_a_capability(self):
        """Правило не выдаёт can_manage_*, и новый тумблер этого не меняет.

        Тумблер живёт в таблице правил, но НЕ в PERMISSION_COLUMNS —
        capabilities_from_grants перебирает их поимённо, и попади он туда,
        держатель ветки получил бы способность на всё пространство сразу.
        """
        self.assertNotIn('manage_subsections', wiki_schema.PERMISSION_COLUMNS)
        self.assertNotIn('manage_subsections', wiki_schema.CAPABILITY_COLUMNS)

    def test_rule_keys_match_the_select(self):
        """Ключи и колонки разбираются ПОЗИЦИОННО (dict(zip(...))).

        Добавить ключ и забыть колонку — значит сдвинуть все поля за ним:
        порог должности встал бы в подпись субъекта. Экран при этом не падает,
        он просто показывает чужие значения.
        """
        source = (ROOT / 'wiki' / 'structure.py').read_text(encoding='utf-8')
        select = re.search(r'def list_section_rules.*?SELECT (.*?)\n\s+FROM '
                           r'wiki_section_access_rules r', source, re.S).group(1)
        # CASE ... END AS subject_label — одна колонка со скобками внутри.
        columns, depth, current = [], 0, ''
        for char in select:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            if char == ',' and depth == 0:
                columns.append(current)
                current = ''
            else:
                current += char
        columns.append(current)
        names = [re.sub(r'.*\bAS\s+', '', c.strip().split('\n')[-1].strip(),
                        flags=re.I).strip()
                 if ' AS ' in c.upper() else c.strip().split('.')[-1]
                 for c in columns]
        self.assertEqual(len(names), len(structure._RULE_KEYS),
                         'число колонок разошлось со списком ключей')
        self.assertIn('manage_subsections', structure._RULE_KEYS)
        self.assertEqual(names[structure._RULE_KEYS.index('manage_subsections')],
                         'r.manage_subsections'.split('.')[-1])


class UpsertGuardTest(unittest.TestCase):
    """Страж: колонка не должна потеряться на записи."""

    def test_upsert_writes_and_refreshes_the_column(self):
        """INSERT без DO UPDATE SET — правило теряет тумблер при повторной правке."""
        source = (ROOT / 'wiki' / 'structure.py').read_text(encoding='utf-8')
        body = re.search(r'def upsert_section_rule.*?\n    return ', source, re.S).group(0)
        self.assertIn('manage_subsections, min_role_level', body,
                      'колонки нет в списке INSERT')
        self.assertIn('manage_subsections = EXCLUDED.manage_subsections', body,
                      'колонка не обновляется при ON CONFLICT')

    def test_keyword_only_argument(self):
        """Позиционный аргумент однажды встал бы не туда молча — все флаги булевы."""
        tree = ast.parse((ROOT / 'wiki' / 'structure.py').read_text(encoding='utf-8'))
        func = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef)
                    and node.name == 'upsert_section_rule')
        self.assertIn('manage_subsections', [a.arg for a in func.args.kwonlyargs])


class StructureScreenSourceTest(unittest.TestCase):
    """Экран «Структура» читается текстом: сборка ложь на экране пропускает молча.

    До этой правки WikiStructure.jsx не читал ни один тест, а весь набор кнопок
    висел на ОДНОМ глобальном флаге. Теперь права построчные, и разойтись с
    сервером им нельзя: расходится это всегда в сторону «кнопку показали, а API
    ответил 403».
    """

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / 'src' / 'components' / 'wiki'
                   / 'WikiStructure.jsx').read_text(encoding='utf-8')
        # Без комментариев: объяснения ссылаются на прежние решения, и
        # проверка «слова нет в файле» ловила бы рассказ о том, чего в коде уже
        # нет (приём из tests/test_wiki_catalog.py).
        cls.code = re.sub(r'/\*.*?\*/', '', cls.src, flags=re.S)
        cls.code = re.sub(r'//[^\n]*', '', cls.code)
        cls.access = (ROOT / 'src' / 'components' / 'wiki'
                      / 'WikiSectionAccess.jsx').read_text(encoding='utf-8')

    def test_add_and_edit_are_per_row(self):
        """Кнопки берут признак СТРОКИ, а не глобальный флаг."""
        self.assertIn("section.can_add_subsection && {", self.code)
        self.assertIn("section.can_edit_section && {", self.code)

    def test_archive_stays_on_the_global_capability(self):
        """Архив уносит статьи — он остался у того, кто ветку выдал."""
        archive = re.search(r"[^\n]*key: 'archive'[^\n]*", self.code).group(0)
        self.assertIn('canManageStructure', archive)

    def test_top_level_section_button_stays_global(self):
        """«+ Раздел» у пространства заводит НОВУЮ ветку, а не подраздел."""
        self.assertIn("{canManageStructure && (", self.code)

    def test_parent_select_offers_only_allowed_parents(self):
        """Иначе человек подставит чужого родителя и получит 403 на форме."""
        self.assertIn('canManageStructure || s.can_add_subsection', self.code)

    def test_department_catalog_loads_for_branch_holders(self):
        """Форме раздела нужен справочник отделов, а способности у них нет."""
        self.assertIn('can_manage_some_sections', self.code)
        self.assertIn('if (!canBuildSomewhere)', self.code)

    def test_toggle_is_hidden_from_those_who_cannot_grant_it(self):
        """Тумблер, на который сервер ответит 403, хуже отсутствующего."""
        self.assertIn('grantableStructure', self.access)
        self.assertIn('grantable_structure', self.access)
        self.assertIn('Может заводить подразделы', self.access)

    def test_toggle_reaches_the_payload(self):
        """Галочка, которая никуда не уезжает, — молчаливый отказ."""
        self.assertIn('manage_subsections: !!draft.manage_subsections', self.access)


if __name__ == '__main__':
    unittest.main()
