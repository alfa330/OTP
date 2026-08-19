# -*- coding: utf-8 -*-
"""Правка статей: кто и что вправе сделать.

Главное, что здесь проверяется, — что решение принимают ЭФФЕКТИВНЫЕ права на
конкретную статью, а не роль. В исходной вике удаление гейтилось только
`requireRole(['Admin','Editor'])`, причём 'Editor' разворачивался в восемь
ролей: любой супервайзер мог снести любую статью, включая чужого отдела.
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

from wiki import articles as wiki_articles  # noqa: E402
from wiki import edit as wiki_edit  # noqa: E402
from wiki import queries  # noqa: E402
from wiki.edit import normalize_session_id  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402


class SessionIdTest(unittest.TestCase):
    """_current_session_id_from_access_token возвращает строку 'None', а не None."""

    def test_string_none_becomes_null(self):
        self.assertIsNone(normalize_session_id('None'))
        self.assertIsNone(normalize_session_id('none'))
        self.assertIsNone(normalize_session_id('null'))
        self.assertIsNone(normalize_session_id('undefined'))
        self.assertIsNone(normalize_session_id(''))
        self.assertIsNone(normalize_session_id('   '))
        self.assertIsNone(normalize_session_id(None))

    def test_real_uuid_survives(self):
        value = '3f2504e0-4f89-11d3-9a0c-0305e82c3301'
        self.assertEqual(normalize_session_id(value), value)
        self.assertEqual(normalize_session_id('  %s  ' % value), value)


ADMIN_ROLE = {'id': 5, 'code': 'wiki_admin', 'can_read': True, 'can_create': True,
              'can_edit': True, 'can_delete': True, 'can_publish': True,
              'can_approve': True, 'can_manage_users': True,
              'can_manage_structure': True, 'can_manage_access': True}

EDITOR_ROLE = {'id': 2, 'code': 'editor', 'can_read': True, 'can_create': True,
               'can_edit': True, 'can_delete': False, 'can_publish': False,
               'can_approve': False, 'can_manage_users': False,
               'can_manage_structure': False, 'can_manage_access': False}


def make_context(role, wiki_roles=()):
    return {
        'user_id': 42, 'otp_role': role, 'department_id': None, 'direction_id': None,
        'headed_department_ids': [], 'group_ids': [], 'wiki_roles': list(wiki_roles),
        'access_mode': 'auto',
    }


ARTICLE = {
    'id': 7, 'slug': 'test', 'title': 'Тест', 'summary': None, 'content': '<p>x</p>',
    'article_type': 'general', 'status': 'published', 'visibility_mode': 'inherit',
    'strict_mode': False, 'toc': [], 'views': 0, 'author_id': 99, 'author_name': None,
    'owner_user_id': None, 'updated_by': None, 'updated_at': None, 'created_at': None,
    'published_at': None, 'review_due_at': None, 'section_ids': [3], 'tags': [],
}


@unittest.skipIf(Flask is None, 'flask не установлен')
class EditGuardTest(unittest.TestCase):
    """Каркас: подменяем слой данных, проверяем только решения о правах."""

    def build(self, context, *, section_rules):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        cursor.rowcount = 1

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        patches = [
            (queries, 'load_access_context', lambda _c, _u: dict(context)),
            (queries, 'allowed_section_ids', lambda _c, _ctx, _s: {3}),
            (queries, 'section_rules_for_user',
             lambda _c, ids, _s, _u: ({3: section_rules} if ids else {})),
            (queries, 'log_action', lambda *a, **k: None),
            (wiki_articles, 'visible_article_ids', lambda *a, **k: {7}),
            (wiki_articles, 'get_article', lambda *a, **k: dict(ARTICLE)),
            (wiki_articles, 'article_rules_for_user', lambda *a, **k: {}),
            (wiki_edit, 'delete_article', lambda *a, **k: True),
            (wiki_edit, 'update_article', lambda *a, **k: True),
            (wiki_edit, 'set_sections', lambda *a, **k: None),
            (wiki_edit, 'set_tags', lambda *a, **k: None),
        ]
        for module, name, replacement in patches:
            original = getattr(module, name)
            setattr(module, name, replacement)
            self.addCleanup(setattr, module, name, original)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db, require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            # Гейт QR-подтверждения здесь всегда открыт: эти наборы
            # проверяют права раздела, а сам гейт — test_sensitive_section_qr_gate.
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            client_ip=lambda: '127.0.0.1',
            gcs={'signed_url': lambda *a, **k: 'https://x'},
            session_id_provider=lambda: 'None',
        ))
        app.config['TESTING'] = True
        return app.test_client()

    def test_editor_can_edit_but_not_delete(self):
        """Ровно тот случай, который в оригинале позволял СВ снести любую статью."""
        rules = [{'can_read': True, 'can_create': True, 'can_edit': True,
                  'can_delete': True, 'can_publish': True, 'can_approve': False}]
        client = self.build(make_context('sv', [EDITOR_ROLE]), section_rules=rules)

        self.assertEqual(client.patch('/api/wiki/articles/7',
                                      json={'title': 'Новое'}).status_code, 200)

        response = client.delete('/api/wiki/articles/7')
        self.assertEqual(response.status_code, 403,
                         'правило разрешает удаление, но у роли нет такой способности')
        self.assertEqual(response.get_json().get('required'), 'can_delete')

    def test_delete_needs_rule_too(self):
        """Способность есть, а правило на разделе — нет."""
        rules = [{'can_read': True, 'can_create': False, 'can_edit': False,
                  'can_delete': False, 'can_publish': False, 'can_approve': False}]
        client = self.build(make_context('operator', [ADMIN_ROLE]), section_rules=rules)
        # can_manage_access у админа вики перекрывает правило — это осознанное
        # короткое замыкание, проверяем именно его.
        self.assertEqual(client.delete('/api/wiki/articles/7').status_code, 200)

    def test_publish_requires_publish_permission(self):
        rules = [{'can_read': True, 'can_create': False, 'can_edit': True,
                  'can_delete': False, 'can_publish': False, 'can_approve': False}]
        client = self.build(make_context('sv', [EDITOR_ROLE]), section_rules=rules)
        response = client.patch('/api/wiki/articles/7', json={'status': 'published'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('required'), 'can_publish')

    def test_visibility_mode_needs_access_manager(self):
        """Режим «только по списку» — не косметика, его меняет тот, кто раздаёт доступы."""
        rules = [{'can_read': True, 'can_create': True, 'can_edit': True,
                  'can_delete': True, 'can_publish': True, 'can_approve': True}]
        client = self.build(make_context('sv', [EDITOR_ROLE]), section_rules=rules)
        for payload in ({'visibility_mode': 'restricted'}, {'strict_mode': True}):
            response = client.patch('/api/wiki/articles/7', json=payload)
            self.assertEqual(response.status_code, 403, payload)
            self.assertEqual(response.get_json().get('required'), 'can_manage_access')

    def test_reader_cannot_edit_at_all(self):
        rules = [{'can_read': True, 'can_create': False, 'can_edit': False,
                  'can_delete': False, 'can_publish': False, 'can_approve': False}]
        client = self.build(make_context('operator'), section_rules=rules)
        self.assertEqual(client.patch('/api/wiki/articles/7',
                                      json={'title': 'x'}).status_code, 403)
        self.assertEqual(client.delete('/api/wiki/articles/7').status_code, 403)

    def test_create_requires_capability(self):
        client = self.build(make_context('operator'), section_rules=[])
        response = client.post('/api/wiki/articles',
                               json={'title': 'Новая', 'section_ids': [3]})
        self.assertEqual(response.status_code, 403)

    def test_article_rules_are_admin_only(self):
        rules = [{'can_read': True, 'can_create': True, 'can_edit': True,
                  'can_delete': True, 'can_publish': True, 'can_approve': True}]
        client = self.build(make_context('sv', [EDITOR_ROLE]), section_rules=rules)
        self.assertEqual(client.get('/api/wiki/articles/7/access-rules').status_code, 403)
        self.assertEqual(client.post('/api/wiki/articles/7/access-rules',
                                     json={'subject_type': 'user', 'subject_id': 1}
                                     ).status_code, 403)


class DenyRuleShapeTest(unittest.TestCase):
    """Запрет читать обязан закрывать и всё остальное: править невидимое нельзя."""

    @unittest.skipIf(Flask is None, 'flask не установлен')
    def test_deny_read_closes_everything(self):
        captured = {}

        original = wiki_edit.upsert_article_rule
        wiki_edit.upsert_article_rule = lambda cursor, **kw: (captured.update(kw), 1)[1]
        self.addCleanup(setattr, wiki_edit, 'upsert_article_rule', original)

        guard = EditGuardTest('test_reader_cannot_edit_at_all')
        guard.addCleanup = self.addCleanup
        client = guard.build(make_context('admin', [ADMIN_ROLE]), section_rules=[])

        response = client.post('/api/wiki/articles/7/access-rules', json={
            'subject_type': 'user', 'subject_id': 55,
            'mode': 'deny', 'can_read': True,
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(captured['mode'], 'deny')
        self.assertTrue(all(captured['permissions'].values()),
                        'запрет чтения должен запрещать и правку, и удаление')


if __name__ == '__main__':
    unittest.main()
