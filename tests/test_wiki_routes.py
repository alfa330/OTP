# -*- coding: utf-8 -*-
"""Гейтинг прав на уровне HTTP раздела «Вики».

Проверяем не SQL, а то, что декоратор wiki_route действительно закрывает
эндпоинты: оператор не должен уметь ни создать раздел, ни выдать себе правило,
ни прочитать журнал. В оригинальной вике управление правилами не имело CRUD
вообще — единственным писателем был сид, поэтому проверять там было нечего;
у нас эндпоинты есть, и их гейтинг надо фиксировать тестом.

Отдельно проверяется граница главы отдела: админ с возглавляемым отделом НЕ
является глобальным админом (штатное правило портала), и структуру чужого
отдела править не может.
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

from wiki import queries, structure  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402


def make_context(role, *, wiki_roles=(), headed=(), department_id=None, mode='auto'):
    return {
        'user_id': 42,
        'otp_role': role,
        'department_id': department_id,
        'direction_id': None,
        'headed_department_ids': list(headed),
        'group_ids': [],
        'wiki_roles': list(wiki_roles),
        'access_mode': mode,
    }


ADMIN_ROLE = {'id': 5, 'code': 'wiki_admin', 'can_read': True, 'can_create': True,
              'can_edit': True, 'can_delete': True, 'can_publish': True,
              'can_approve': True, 'can_manage_users': True,
              'can_manage_structure': True, 'can_manage_access': True}


@unittest.skipIf(Flask is None, 'flask не установлен')
class WikiRouteGuardTest(unittest.TestCase):
    def build(self, context):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        cursor.rowcount = 0

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        self._orig_load = queries.load_access_context
        queries.load_access_context = lambda _cursor, _uid: dict(context)
        self.addCleanup(setattr, queries, 'load_access_context', self._orig_load)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            client_ip=lambda: '127.0.0.1',
        ))
        app.config['TESTING'] = True
        return app.test_client(), cursor

    # ── Оператор не должен уметь ничего из управления ────────────────────
    def test_operator_cannot_manage_structure(self):
        client, _ = self.build(make_context('operator'))
        for method, url in (('post', '/api/wiki/spaces'),
                            ('post', '/api/wiki/sections'),
                            ('patch', '/api/wiki/spaces/1'),
                            ('delete', '/api/wiki/sections/1')):
            response = getattr(client, method)(url, json={'name': 'x', 'space_id': 1})
            self.assertEqual(response.status_code, 403, '%s %s' % (method, url))
            self.assertEqual(response.get_json().get('code'), 'WIKI_FORBIDDEN')

    def test_operator_cannot_touch_access_rules(self):
        client, _ = self.build(make_context('operator'))
        for method, url in (('get', '/api/wiki/access/section-rules'),
                            ('post', '/api/wiki/access/section-rules'),
                            ('delete', '/api/wiki/access/section-rules/1'),
                            ('get', '/api/wiki/access/subjects'),
                            ('get', '/api/wiki/access/effective'),
                            ('get', '/api/wiki/audit')):
            response = getattr(client, method)(url, json={})
            self.assertEqual(response.status_code, 403, '%s %s' % (method, url))

    def test_operator_can_read_own_perimeter(self):
        client, _ = self.build(make_context('operator'))
        for url in ('/api/wiki/ping', '/api/wiki/me', '/api/wiki/structure'):
            self.assertEqual(client.get(url).status_code, 200, url)

    # ── Супервайзер: правит, но не раздаёт права ─────────────────────────
    def test_supervisor_cannot_manage_access(self):
        client, _ = self.build(make_context('sv'))
        self.assertEqual(client.get('/api/wiki/audit').status_code, 403)
        self.assertEqual(client.post('/api/wiki/spaces', json={'name': 'x'}).status_code, 403)

    # ── Администратор вики ───────────────────────────────────────────────
    def test_wiki_admin_passes_guards(self):
        client, cursor = self.build(make_context('admin', wiki_roles=[ADMIN_ROLE]))
        self.assertEqual(client.get('/api/wiki/access/subjects').status_code, 200)
        self.assertEqual(client.get('/api/wiki/audit').status_code, 200)
        # без user_id эндпоинт объяснения прав обязан ругаться, а не падать
        self.assertEqual(client.get('/api/wiki/access/effective').status_code, 400)

    def test_create_space_requires_name(self):
        client, _ = self.build(make_context('admin', wiki_roles=[ADMIN_ROLE]))
        response = client.post('/api/wiki/spaces', json={})
        self.assertEqual(response.status_code, 400)

    # ── Граница главы отдела ─────────────────────────────────────────────
    def test_department_head_cannot_create_space_in_foreign_department(self):
        """Админ с возглавляемым отделом — не глобальный админ.

        Если это правило не повторить, глава одного отдела получил бы власть
        над структурой всех остальных.
        """
        client, _ = self.build(make_context('admin', headed=[7], department_id=7))
        response = client.post('/api/wiki/spaces', json={'name': 'Чужое', 'department_id': 9})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_DEPARTMENT_SCOPE')

    def test_department_head_can_create_space_in_own_department(self):
        client, cursor = self.build(make_context('admin', headed=[7], department_id=7))
        cursor.fetchone.return_value = (123,)
        response = client.post('/api/wiki/spaces', json={'name': 'Своё', 'department_id': 7})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json().get('id'), 123)

    # ── Preflight ────────────────────────────────────────────────────────
    def test_options_never_requires_permissions(self):
        client, _ = self.build(make_context('operator'))
        for url in ('/api/wiki/audit', '/api/wiki/spaces', '/api/wiki/access/section-rules'):
            self.assertEqual(client.options(url).status_code, 204, url)


class SlugTest(unittest.TestCase):
    def test_cyrillic_is_transliterated(self):
        from wiki.routes_structure import _slugify
        self.assertEqual(_slugify('Общий отдел'), 'obschiy-otdel')
        self.assertEqual(_slugify('Аренда транспорта'), 'arenda-transporta')

    def test_never_empty(self):
        from wiki.routes_structure import _slugify
        self.assertTrue(_slugify('!!!'))
        self.assertTrue(_slugify(''))

    def test_no_leading_or_trailing_dashes(self):
        from wiki.routes_structure import _slugify
        slug = _slugify('  — Тест — ')
        self.assertFalse(slug.startswith('-'))
        self.assertFalse(slug.endswith('-'))


class RulePermissionNormalisationTest(unittest.TestCase):
    """Право записи без чтения бессмысленно — правило должно само это чинить."""

    @unittest.skipIf(Flask is None, 'flask не установлен')
    def test_write_permission_implies_read(self):
        captured = {}

        def fake_upsert(cursor, **kwargs):
            captured.update(kwargs)
            return 1

        original = structure.upsert_section_rule
        structure.upsert_section_rule = fake_upsert
        self.addCleanup(setattr, structure, 'upsert_section_rule', original)

        original_exists = structure.section_exists
        structure.section_exists = lambda cursor, sid: 1
        self.addCleanup(setattr, structure, 'section_exists', original_exists)

        guard = WikiRouteGuardTest('test_options_never_requires_permissions')
        guard.addCleanup = self.addCleanup
        client, _ = guard.build(make_context('admin', wiki_roles=[ADMIN_ROLE]))

        response = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'otp_role', 'subject_role': 'operator',
            'can_read': False, 'can_edit': True,
        })
        self.assertEqual(response.status_code, 201)
        self.assertTrue(captured['permissions']['can_read'],
                        'правка без чтения — противоречие, can_read должен включиться сам')


if __name__ == '__main__':
    unittest.main()
