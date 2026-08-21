# -*- coding: utf-8 -*-
"""Перенос раздела вики в другое пространство.

Раньше этого не было вовсе, и отказ был МОЛЧАЛИВЫМ: форма слала space_id,
обработчик PATCH его не читал, а отвечал «Раздел обновлён». Человек уходил
уверенным, что перенёс, — а раздел оставался на месте. Поэтому здесь проверяются
две вещи сразу: что переезд действительно происходит и что он не происходит там,
где происходить не должен.

Ключевое свойство переезда — групповой характер. space_id хранится у КАЖДОГО
раздела своим полем, а не выводится из родителя: увезти одну строку значит
оставить её подразделы числиться в старом пространстве, и ветка исчезнет с обеих
сторон — у родителя нет детей, у детей нет родителя.

Тесты герметичные: боевая база не читается ни здесь, ни в маршрутной части.
"""

import unittest
from unittest.mock import patch

from tests.test_wiki_routes import ADMIN_ROLE, _RouteHarness, make_context
from wiki import structure

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None


class _MoveCursor:
    """Курсор ровно под move_section_to_space: отвечает по тексту запроса.

    MagicMock тут не годится — подбор слага крутится в цикле, пока запрос не
    вернёт «свободно», и одного return_value на все запросы не хватает.
    """

    def __init__(self, subtree, slugs, taken=()):
        self.subtree = list(subtree)
        self.slugs = dict(slugs)
        self.taken = set(taken)          # пары (space_id, slug), занятые в базе
        self.updates = []                # (id, space_id, slug, parent | KEEP)
        self._result = None
        self.rowcount = 0

    KEEP = object()

    def execute(self, sql, params=None):
        text = ' '.join(sql.split())
        if 'WITH RECURSIVE down' in text:
            self._result = [(i,) for i in self.subtree]
        elif text.startswith('SELECT id, slug FROM wiki_sections'):
            self._result = [(i, self.slugs[i]) for i in params[0]]
        elif text.startswith('SELECT 1 FROM wiki_sections WHERE space_id'):
            space_id, slug = params[0], params[1]
            self._result = [(1,)] if (space_id, slug) in self.taken else []
        elif text.startswith('UPDATE wiki_sections'):
            if 'parent_section_id = %s' in text:
                space_id, parent, slug, member = params
            else:
                space_id, slug, member = params
                parent = self.KEEP
            self.updates.append((member, space_id, slug, parent))
            # Занятый слаг обязан стать видимым следующему разделу поддерева.
            self.taken.add((space_id, slug))
            self._result = []
        else:                                        # pragma: no cover
            raise AssertionError('неожиданный запрос: %s' % text)

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result or [])


class MoveSectionTest(unittest.TestCase):
    def test_subtree_moves_whole(self):
        """Подразделы едут вместе с родителем, и только у корня меняется родитель."""
        cursor = _MoveCursor(subtree=[10, 11, 12],
                             slugs={10: 'operator', 11: 'chat', 12: 'calls'})
        moved = structure.move_section_to_space(cursor, 10, space_id=5,
                                                parent_section_id=None)
        self.assertEqual(moved, 3)
        self.assertEqual([u[0] for u in cursor.updates], [10, 11, 12])
        self.assertTrue(all(u[1] == 5 for u in cursor.updates))
        # Корню родителя переписали, потомкам — нет: их родитель переехал вместе
        # с ними, и трогать его значило бы разобрать поддерево.
        self.assertIsNone(cursor.updates[0][3])
        self.assertEqual(cursor.updates[1][3], _MoveCursor.KEEP)
        self.assertEqual(cursor.updates[2][3], _MoveCursor.KEEP)

    def test_root_keeps_given_parent(self):
        cursor = _MoveCursor(subtree=[10], slugs={10: 'operator'})
        structure.move_section_to_space(cursor, 10, space_id=5, parent_section_id=77)
        self.assertEqual(cursor.updates[0][3], 77)

    def test_busy_slug_gets_number(self):
        """Слаг уникален в пространстве: занятый в целевом обязан подвинуться."""
        cursor = _MoveCursor(subtree=[10], slugs={10: 'operator'},
                             taken={(5, 'operator')})
        structure.move_section_to_space(cursor, 10, space_id=5)
        self.assertEqual(cursor.updates[0][2], 'operator-2')

    def test_siblings_do_not_take_the_same_slug(self):
        """Одноимённые разделы поддерева не должны схлопнуться в один слаг.

        Слаг подбирается по одному и сразу записывается — иначе оба брата
        получили бы 'operator' и переезд упал бы в UNIQUE (space_id, slug).
        """
        cursor = _MoveCursor(subtree=[10, 11], slugs={10: 'operator', 11: 'operator'})
        structure.move_section_to_space(cursor, 10, space_id=5)
        self.assertEqual([u[2] for u in cursor.updates], ['operator', 'operator-2'])


@unittest.skipIf(Flask is None, 'flask не установлен')
class MoveSectionRouteTest(_RouteHarness, unittest.TestCase):
    """PATCH /sections/<id> со сменой пространства."""

    # (name, department_id пространства, space_id, parent_section_id, department_id)
    SECTION_ROW = ('Оператор', None, 1, None, None)

    def _client(self, context=None):
        client, cursor = self.build(
            context or make_context('admin', wiki_roles=[ADMIN_ROLE]))
        cursor.rowcount = 1
        self.moves = []
        patcher = patch.object(
            structure, 'move_section_to_space',
            side_effect=lambda _c, sid, **kw: self.moves.append((sid, kw)) or 3)
        patcher.start()
        self.addCleanup(patcher.stop)
        return client, cursor

    def test_moves_to_another_space(self):
        client, cursor = self._client()
        cursor.fetchone.side_effect = [
            self.SECTION_ROW,
            (None, 'active'),        # целевое пространство
        ]
        response = client.patch('/api/wiki/sections/10',
                                json={'space_id': 2, 'parent_section_id': ''})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json().get('space_id'), 2)
        self.assertEqual(self.moves, [(10, {'space_id': 2, 'parent_section_id': None})])

    def test_same_space_is_not_a_move(self):
        """Форма шлёт space_id всегда — правка названия не должна ронять раздел в корень."""
        client, cursor = self._client()
        cursor.fetchone.side_effect = [self.SECTION_ROW]
        response = client.patch('/api/wiki/sections/10',
                                json={'space_id': 1, 'name': 'Оператор ОП'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.moves, [])

    def test_parent_from_another_space_rejected(self):
        client, cursor = self._client()
        cursor.fetchone.side_effect = [
            self.SECTION_ROW,
            (None, 'active'),        # целевое пространство
            None,                    # section_would_cycle: предком не является
            (9,),                    # section_exists(родитель) — чужое пространство
        ]
        response = client.patch('/api/wiki/sections/10',
                                json={'space_id': 2, 'parent_section_id': 33})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SECTION_PARENT_SPACE')
        self.assertEqual(self.moves, [])

    def test_archived_target_space_rejected(self):
        client, cursor = self._client()
        cursor.fetchone.side_effect = [self.SECTION_ROW, (None, 'archived')]
        response = client.patch('/api/wiki/sections/10', json={'space_id': 2})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SPACE_ARCHIVED')
        self.assertEqual(self.moves, [])

    def test_unknown_target_space(self):
        client, cursor = self._client()
        cursor.fetchone.side_effect = [self.SECTION_ROW, None]
        response = client.patch('/api/wiki/sections/10', json={'space_id': 999})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.moves, [])

    def test_head_does_not_move_sections_at_all(self):
        """Руководитель дерево не двигает — ни у себя, ни к соседям.

        До 21.08.2026 он переносил разделы внутри своего отдела, и тест
        сторожил границу второго пространства. Теперь структура целиком за
        директором, и отказ приходит раньше — на способности.
        """
        client, _cursor = self._client(make_context('admin', headed=[7], department_id=7))
        response = client.patch('/api/wiki/sections/10', json={'space_id': 2})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('required'), 'can_manage_structure')
        self.assertEqual(self.moves, [])

    def test_department_branch_conflict_in_target_space(self):
        """Ветка отдела уникальна в пределах (пространство, родитель, отдел)."""
        client, cursor = self._client()
        cursor.fetchone.side_effect = [
            ('ОП', None, 1, None, 367),       # переезжает ветка отдела 367
            (None, 'active'),                 # целевое пространство
            ('ОП',),                          # там такая ветка уже есть
        ]
        response = client.patch('/api/wiki/sections/10', json={'space_id': 2})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json().get('code'), 'WIKI_DEPARTMENT_BRANCH_TAKEN')
        self.assertEqual(self.moves, [])


if __name__ == '__main__':   # pragma: no cover
    unittest.main()
