# -*- coding: utf-8 -*-
"""Ограничитель ИИ-помощника в настройках раздела (решение владельца 04.11.2026).

Владелец: «создать ограничитель, чтобы помощник их не читал; в настройках папки
сделать так, чтобы можно было ставить ограничитель» — первой закрывается
«Старая вики», где статьи лежат и в корне, и в восьми подпапках. Колонка
wiki_sections.ai_opt_out была в схеме с 10.08.2026, но поставить её было нечем:
ни ручка раздела, ни форма о ней не знали, а закрывала она только статьи,
лежащие прямо в помеченном разделе.

Наборы герметичные: боевая база не читается. Сам SQL наследования проверяет
tests/test_wiki_ai_perimeter.py (над настоящим Postgres), здесь — двери:

  * ставит и снимает ограничитель тот же, кто делает раздел публичным, —
    носитель способности; держателю ветки отказ в обе стороны, но обычное
    сохранение формы (флаг тот же) проходит;
  * правкой считается ОТЛИЧИЕ от текущего: форма шлёт флаг при каждом
    сохранении, и переименование не должно гонять пересборку индекса;
  * переключение и переезд раздела пересобирают индекс помощника под ВСЁ
    поддерево — иначе куски закрытых статей остались бы в индексе, а открытые
    остались бы без кусков;
  * сбой индекса не отменяет правку раздела;
  * ответ /structure говорит каждой строке, кто закрыл её сверху.
"""

import re
import unittest
from pathlib import Path

from tests.test_wiki_routes import _RouteHarness, _section_row, make_context
from wiki import articles as wiki_articles
from wiki import queries, structure
from wiki.ai import index as ai_index

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

ROOT = Path(__file__).resolve().parents[1]

# Дерево прода: 39 «Старая вики», внутри 43 «Информацияпотаксопаркам».
OLD_WIKI, SUBFOLDER = 39, 43
# Держатель ветки: ему выдан 1 «Коммерческий директор» и всё, что под ним.
ANCHOR, BRANCH = 1, 19


def section_row(*, parent=None, ai_off=False, scope='restricted', status='active',
                owner=None, department=None, name='Старая вики'):
    """Первый SELECT ручки: имя, отдел пространства, space_id, родитель, отдел
    раздела, публичность, статус, владелец, рубильник помощника."""
    return (name, None, 11, parent, department, scope, status, owner, ai_off)


class _Stubs:
    """Подмены вокруг ручек раздела: запись раздела, поддерево и индекс."""

    def stub(self, *, subtree=(523, 604), reindex=None):
        self.updated, self.created, self.logged = [], [], []
        self.subtree_asked, self.reindexed = [], []

        def _update(cursor, section_id, fields):
            self.updated.append((section_id, dict(fields)))
            return bool(fields)

        def _subtree(cursor, section_id):
            self.subtree_asked.append(section_id)
            return list(subtree)

        def _reindex(cursor, article_id, **kw):
            self.reindexed.append(article_id)
            if reindex:
                return reindex(article_id)
            return {'article_id': article_id, 'action': 'removed', 'chunks': 0}

        def _log(cursor, **kw):
            self.logged.append(kw)

        for owner, name, value in (
            (structure, 'update_section', _update),
            (structure, 'subtree_article_ids', _subtree),
            (structure, 'section_would_cycle', lambda cursor, sid, parent: False),
            (structure, 'create_section',
             lambda cursor, **kw: (self.created.append(kw), 77)[1]),
            (structure, 'department_branch_taken', lambda cursor, **kw: None),
            (structure, 'free_section_slug', lambda cursor, space_id, base, **kw: base),
            (structure, 'space_open_to', lambda cursor, space_id, departments: True),
            (structure, 'section_exists', lambda cursor, sid: 11),
            (ai_index, 'reindex_article', _reindex),
            (queries, 'log_action', _log),
        ):
            self.addCleanup(setattr, owner, name, getattr(owner, name))
            setattr(owner, name, value)

    def logged_details(self, action):
        return [entry['details'] for entry in self.logged if entry['action'] == action]


@unittest.skipIf(Flask is None, 'flask не установлен')
class ManagerTogglesTest(_Stubs, _RouteHarness, unittest.TestCase):
    """Супер-админ: носитель способности управлять структурой."""

    def _client(self, row, **stub):
        client, cursor = self.build(make_context('super_admin'))
        cursor.fetchone.return_value = row
        cursor.rowcount = 1
        self.stub(**stub)
        return client, cursor

    def test_closing_a_section_reaches_its_subtree_index(self):
        """Ради этого всё и делалось: «Старая вики» закрыта целиком."""
        client, _ = self._client(section_row())
        response = client.patch('/api/wiki/sections/%d' % OLD_WIKI,
                                json={'name': 'Старая вики', 'ai_opt_out': True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.updated[-1], (OLD_WIKI, {'name': 'Старая вики',
                                                       'ai_opt_out': True}))
        self.assertEqual(self.subtree_asked, [OLD_WIKI])
        self.assertEqual(self.reindexed, [523, 604])
        self.assertEqual(response.get_json()['ai_index']['removed'], 2)
        self.assertEqual(self.logged_details('section.update'),
                         [{'name': 'Старая вики', 'ai_opt_out': True}])

    def test_reopening_a_section_reindexes_it_back(self):
        client, _ = self._client(
            section_row(ai_off=True),
            reindex=lambda aid: {'article_id': aid, 'action': 'indexed', 'chunks': 3})
        response = client.patch('/api/wiki/sections/%d' % OLD_WIKI,
                                json={'ai_opt_out': False})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.updated[-1], (OLD_WIKI, {'ai_opt_out': False}))
        self.assertEqual(self.reindexed, [523, 604])
        self.assertEqual(response.get_json()['ai_index']['indexed'], 2)

    def test_unchanged_flag_is_not_a_change(self):
        """Форма шлёт флаг при каждом сохранении — переименование индекс не трогает."""
        client, _ = self._client(section_row(ai_off=True))
        response = client.patch('/api/wiki/sections/%d' % OLD_WIKI,
                                json={'name': 'Старая вики (архив)', 'ai_opt_out': True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertNotIn('ai_opt_out', self.updated[-1][1])
        self.assertEqual(self.subtree_asked, [])
        self.assertNotIn('ai_index', response.get_json())

    def test_a_string_is_not_a_switch(self):
        """'false' строкой — истина в Python: такой ключ считается «не трогали»."""
        for raw in ('false', 'true', 1, None):
            with self.subTest(raw=raw):
                client, _ = self._client(section_row())
                client.patch('/api/wiki/sections/%d' % OLD_WIKI,
                             json={'name': 'Старая вики', 'ai_opt_out': raw})
                self.assertNotIn('ai_opt_out', self.updated[-1][1])
                self.assertEqual(self.subtree_asked, [])

    def test_moving_under_another_parent_reindexes_the_subtree(self):
        """Переезд под закрытый раздел закрывает поддерево — индекс обязан пойти следом."""
        client, _ = self._client(section_row(parent=None, name='Подпапка'))
        response = client.patch('/api/wiki/sections/%d' % SUBFOLDER,
                                json={'name': 'Подпапка', 'parent_section_id': OLD_WIKI})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.subtree_asked, [SUBFOLDER])

    def test_same_parent_does_not_reindex(self):
        client, _ = self._client(section_row(parent=OLD_WIKI, name='Подпапка'))
        response = client.patch('/api/wiki/sections/%d' % SUBFOLDER,
                                json={'name': 'Подпапка', 'parent_section_id': OLD_WIKI})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.subtree_asked, [])

    def test_index_failure_keeps_the_section_change(self):
        """Тот же обмен, что при сохранении статьи: правка раздела дороже индекса."""
        def _boom(article_id):
            raise RuntimeError('индекс недоступен')
        client, cursor = self._client(section_row(), reindex=_boom)
        response = client.patch('/api/wiki/sections/%d' % OLD_WIKI,
                                json={'ai_opt_out': True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn('failed', response.get_json()['ai_index'])
        self.assertEqual(self.updated[-1], (OLD_WIKI, {'ai_opt_out': True}))
        executed = [str(call.args[0]) for call in cursor.execute.call_args_list]
        self.assertIn('SAVEPOINT wiki_section_ai', executed)
        self.assertIn('ROLLBACK TO SAVEPOINT wiki_section_ai', executed)
        self.assertNotIn('RELEASE SAVEPOINT wiki_section_ai', executed)

    def test_new_section_can_start_closed(self):
        client, cursor = self._client(None)
        cursor.fetchone.return_value = ('active',)        # пространство живо
        response = client.post('/api/wiki/sections', json={
            'name': 'Архив', 'space_id': 11, 'ai_opt_out': True})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertIs(self.created[0]['ai_opt_out'], True)
        self.assertIs(self.logged_details('section.create')[0]['ai_opt_out'], True)

    def test_new_section_is_open_by_default(self):
        """И запись журнала молчит о состоянии по умолчанию."""
        for payload in ({}, {'ai_opt_out': 'true'}, {'ai_opt_out': False}):
            with self.subTest(payload=payload):
                client, cursor = self._client(None)
                cursor.fetchone.return_value = ('active',)
                response = client.post('/api/wiki/sections', json=dict(
                    {'name': 'Регламенты', 'space_id': 11}, **payload))
                self.assertEqual(response.status_code, 201)
                self.assertIs(self.created[0]['ai_opt_out'], False)
                self.assertNotIn('ai_opt_out', self.logged_details('section.create')[0])


@unittest.skipIf(Flask is None, 'flask не установлен')
class BranchHolderTest(_Stubs, _RouteHarness, unittest.TestCase):
    """Держатель ветки: строит подразделы, но что читает помощник — не его решение."""

    def _client(self, row):
        client, cursor = self.build(make_context('admin', department_id=1),
                                    manage_sections=(ANCHOR, BRANCH))
        cursor.fetchone.return_value = row
        cursor.rowcount = 1
        self.stub()
        return client, cursor

    def test_cannot_close_a_subsection(self):
        client, _ = self._client(section_row(parent=BRANCH, name='Оператор'))
        response = client.patch('/api/wiki/sections/4', json={'ai_opt_out': True})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'WIKI_SECTION_AI_FORBIDDEN')
        self.assertEqual(self.updated, [])
        self.assertEqual(self.subtree_asked, [])

    def test_cannot_reopen_what_was_closed_above(self):
        client, _ = self._client(section_row(parent=BRANCH, name='Оператор', ai_off=True))
        response = client.patch('/api/wiki/sections/4', json={'ai_opt_out': False})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'WIKI_SECTION_AI_FORBIDDEN')

    def test_ordinary_save_still_passes(self):
        """Форма шлёт флаг всегда: отказ «по ключу» сломал бы обычное переименование."""
        client, _ = self._client(section_row(parent=BRANCH, name='Оператор', ai_off=True))
        response = client.patch('/api/wiki/sections/4',
                                json={'name': 'Оператор линии', 'ai_opt_out': True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.updated[-1], (4, {'name': 'Оператор линии'}))

    def test_new_subsection_flag_does_not_reach_the_base(self):
        """Подраздел закрытого раздела закрыт и так — наследованием."""
        client, cursor = self._client(None)
        cursor.fetchone.return_value = ('active',)
        response = client.post('/api/wiki/sections', json={
            'name': 'Наставник', 'space_id': 11, 'parent_section_id': BRANCH,
            'ai_opt_out': True})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertIs(self.created[0]['ai_opt_out'], False)


@unittest.skipIf(Flask is None, 'flask не установлен')
class StructureClosedByTest(_RouteHarness, unittest.TestCase):
    """Ответ /structure: кто закрыл раздел сверху — именем.

    Форме это нужно, чтобы отличить «закрыт здесь» (можно вернуть) от «закрыт
    выше» (возвращать здесь нечего): иначе подпапка «Старой вики» показывала бы
    «Читает», а помощник её не читал бы.
    """

    # (id, родитель, название, свой флаг)
    TREE = (
        (39, None, 'Старая вики', True),
        (43, 39, 'Информацияпотаксопаркам', False),
        (90, 43, 'Внутри подпапки', False),
        (50, None, 'Общий сотрудник', False),
        (60, 50, 'Закрытая сама', True),
        (61, 60, 'Под закрытой', False),
    )

    def _sections(self):
        rows = []
        for ident, parent, name, closed in self.TREE:
            row = _section_row(ident, parent, name, None)
            row['ai_opt_out'] = closed
            rows.append(row)
        for owner, name, value in (
            (structure, 'list_sections', lambda cursor, **kw: [dict(r) for r in rows]),
            (structure, 'list_spaces', lambda cursor, **kw: [{'id': 11, 'name': 'Таксопарки'}]),
            (structure, 'public_departments_by_section', lambda cursor, ids: {}),
            (structure, 'article_counts_by_section', lambda cursor, ids: {}),
            (structure, 'section_role_levels', lambda cursor: {}),
            (queries, 'allowed_section_ids', lambda cursor, ctx, subjects, **kw: set()),
            (queries, 'section_rules_for_user', lambda *a, **kw: {}),
            (wiki_articles, 'visible_article_ids', lambda *a, **kw: set()),
        ):
            self.addCleanup(setattr, owner, name, getattr(owner, name))
            setattr(owner, name, value)
        client, _ = self.build(make_context('super_admin'))
        response = client.get('/api/wiki/structure')
        self.assertEqual(response.status_code, 200)
        return {s['id']: s for s in response.get_json()['sections']}

    def test_closed_by_names_the_nearest_closed_ancestor(self):
        sections = self._sections()
        self.assertEqual(sections[43]['ai_closed_by'], 'Старая вики')
        self.assertEqual(sections[90]['ai_closed_by'], 'Старая вики')
        self.assertEqual(sections[61]['ai_closed_by'], 'Закрытая сама')

    def test_own_flag_is_not_closed_by(self):
        """Свой флаг приезжает полем ai_opt_out — «закрыт выше» он не означает."""
        sections = self._sections()
        self.assertIsNone(sections[39]['ai_closed_by'])
        self.assertIs(sections[39]['ai_opt_out'], True)
        self.assertIsNone(sections[60]['ai_closed_by'])

    def test_open_branch_stays_open(self):
        sections = self._sections()
        self.assertIsNone(sections[50]['ai_closed_by'])


class SqlShapeTest(unittest.TestCase):
    """Колонка и её место: dict(zip(...)) молча перепутал бы поля при сдвиге."""

    @staticmethod
    def _top_level(text):
        """Части списка через запятую верхнего уровня (скобки не режем)."""
        parts, depth, current = [], 0, ''
        for char in text:
            depth += char == '('
            depth -= char == ')'
            if char == ',' and depth == 0:
                parts.append(current.strip())
                current = ''
            else:
                current += char
        parts.append(current.strip())
        return parts

    def test_list_sections_reads_the_flag_in_its_place(self):
        captured = {}

        class _Cursor:
            def execute(self, sql, params=None):
                captured['sql'] = sql

            def fetchall(self):
                return []

        structure.list_sections(_Cursor())
        select = re.search(r'SELECT(.*?)\n\s*FROM wiki_sections s', captured['sql'], re.S).group(1)
        columns = self._top_level(select)
        self.assertEqual(len(columns), len(structure._SECTION_KEYS))
        self.assertEqual(columns.index('s.ai_opt_out'),
                         structure._SECTION_KEYS.index('ai_opt_out'))

    def test_create_section_writes_the_flag_in_its_place(self):
        captured = {}

        class _Cursor:
            def execute(self, sql, params=None):
                captured['sql'], captured['params'] = sql, params

            def fetchone(self):
                return (1,)

        structure.create_section(
            _Cursor(), space_id=11, parent_section_id=None, name='Архив', slug='arhiv',
            description=None, icon=None, visibility_scope='restricted', owner_user_id=None,
            created_by=2, ai_opt_out=True)
        sql = captured['sql']
        columns = self._top_level(re.search(r'INSERT INTO wiki_sections \((.*?)\)\s*VALUES',
                                            sql, re.S).group(1))
        values = self._top_level(re.search(r'VALUES \((.*)\)\s*RETURNING', sql, re.S).group(1))
        self.assertEqual(len(columns), len(values))
        self.assertEqual(values[columns.index('ai_opt_out')], '%(ai_off)s')
        self.assertIs(captured['params']['ai_off'], True)

    def test_flag_is_updatable(self):
        self.assertIn('ai_opt_out', structure._SECTION_UPDATABLE)

    def test_subtree_query_walks_down_and_survives_loops(self):
        """UNION, а не UNION ALL: на петле обход обязан остановиться."""
        captured = {}

        class _Cursor:
            def execute(self, sql, params=None):
                captured['sql'], captured['params'] = sql, params

            def fetchall(self):
                return [(523,), (604,)]

        self.assertEqual(structure.subtree_article_ids(_Cursor(), OLD_WIKI), [523, 604])
        sql = captured['sql']
        self.assertEqual(captured['params'], (OLD_WIKI,))
        self.assertIn('child.parent_section_id = parent.id', sql)
        self.assertRegex(sql, r'\bUNION\b(?!\s+ALL)')


class ScreenSourceTest(unittest.TestCase):
    """Экран читается текстом: молчаливый отказ на клиенте тестом иначе не поймать."""

    @classmethod
    def setUpClass(cls):
        wiki = ROOT / 'src' / 'components' / 'wiki'
        cls.structure = (wiki / 'WikiStructure.jsx').read_text(encoding='utf-8')
        cls.editor = (wiki / 'WikiEditor.jsx').read_text(encoding='utf-8')
        cls.draft = (wiki / 'WikiAiDraft.jsx').read_text(encoding='utf-8')

    def test_flag_reaches_the_payload(self):
        """Переключатель, который никуда не уезжает, — молчаливый отказ."""
        self.assertIn('ai_opt_out: !!sectionModal.ai_opt_out', self.structure)

    def test_form_starts_from_the_saved_flag(self):
        """Иначе открытая форма показывала бы «Читает» у закрытой папки."""
        self.assertIn('ai_opt_out: !!x.ai_opt_out', self.structure)

    def test_choice_is_only_for_structure_managers(self):
        """Держателю ветки сервер отвечает 403 — блока у него быть не должно."""
        block = re.search(r'\{canManageStructure && \(\s*<div className="space-y-1\.5">'
                          r'.*?ИИ-помощник', self.structure, re.S)
        self.assertIsNotNone(block)

    def test_closed_above_is_said_in_words(self):
        self.assertIn('aiClosedAbove(sections, sectionModal.parent_section_id)', self.structure)
        self.assertIn('так задано у раздела', self.structure)

    def test_own_flag_keeps_the_choice_even_when_closed_above(self):
        """Свой флаг снимается и под закрытым родителем — иначе снять его нечем."""
        self.assertIn('modalAiClosedAbove && !sectionModal.ai_opt_out ?', self.structure)

    def test_editor_tells_that_the_section_is_closed(self):
        self.assertIn('closedBy={aiClosedBy}', self.editor)
        self.assertIn('enabled && closedBy', self.draft)


if __name__ == '__main__':
    unittest.main()
