# -*- coding: utf-8 -*-
"""Витрина чтения показывает ЛИЧНЫЙ периметр, а не всё содержимое портала.

Дефект, который фиксируют эти тесты. Роль OTP 'admin' автоматически даёт все
способности вики, включая can_manage_access, а мастер-ключ в SQL видимости
открывал сразу все статьи. Роль 'admin' в портале носят руководители разных
служб (чат-менеджер, HR), и в блоке «Все статьи» им выкладывалось содержимое
чужих отделов вместе с черновиками. Замер на боевых данных 10.08.2026: восемь
человек видели все 36 статей при периметре в 16.

Второй дефект — черновики: гейтом было can_edit, а её по умолчанию получают
супервайзер и тренер, то есть чужой незаконченный текст попадал в список чтения.

Мастер-ключ никуда не делся: администратор доступов по-прежнему может посмотреть
всё, но по явной просьбе (?scope=all), и точечные пути (статья по слагу, файл,
правка) считают периметр как раньше — иначе администратор не починит статью.
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
from wiki import queries  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402

WIKI_ADMIN_CAPS = {
    'can_read': True, 'can_create': True, 'can_edit': True, 'can_delete': True,
    'can_publish': True, 'can_approve': True, 'can_manage_users': True,
    'can_manage_structure': True, 'can_manage_access': True,
}

EDITOR_CAPS = {'can_read': True, 'can_create': True, 'can_edit': True}

EMPTY_SUBJECTS = {'department': [], 'direction': [], 'group': [],
                  'otp_role': ['operator'], 'wiki_role': [], 'user': [42]}


def ctx(caps, role='admin'):
    return {'user_id': 42, 'otp_role': role, 'capabilities': dict(caps),
            'department_id': None, 'direction_id': None,
            'headed_department_ids': [], 'group_ids': [], 'wiki_roles': [],
            'access_mode': 'auto'}


class SubjectParamsTest(unittest.TestCase):
    """Флаги, которые уходят в SQL видимости."""

    def params(self, caps, role='admin', master_key=True):
        return wiki_articles._subject_params(ctx(caps, role), EMPTY_SUBJECTS,
                                            {1}, master_key)

    def test_master_key_reaches_sql_by_default(self):
        self.assertTrue(self.params(WIKI_ADMIN_CAPS)['is_wiki_admin'])

    def test_master_key_withheld_from_browsing(self):
        self.assertFalse(self.params(WIKI_ADMIN_CAPS, master_key=False)['is_wiki_admin'])

    def test_super_admin_bypass_withheld_from_browsing(self):
        """Обход строгого режима — тоже мастер-ключ, и в витрине его нет."""
        self.assertTrue(self.params(WIKI_ADMIN_CAPS, 'super_admin')['is_super_admin'])
        self.assertFalse(
            self.params(WIKI_ADMIN_CAPS, 'super_admin', master_key=False)['is_super_admin'])

    def test_editor_does_not_see_foreign_drafts(self):
        """can_edit есть у каждого супервайзера и тренера — гейтом она быть не может."""
        self.assertFalse(self.params(EDITOR_CAPS, 'sv')['can_see_drafts'])

    def test_publisher_still_sees_drafts(self):
        caps = dict(EDITOR_CAPS, can_publish=True)
        self.assertTrue(self.params(caps, 'admin')['can_see_drafts'])


class AllowedSectionsTest(unittest.TestCase):
    """Короткое замыкание «администратор видит все разделы»."""

    def run_query(self, caps, master_key=True):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        queries.allowed_section_ids(cursor, ctx(caps), EMPTY_SUBJECTS,
                                    master_key=master_key)
        return cursor.execute.call_args[0][0]

    def test_admin_short_circuit_by_default(self):
        sql = self.run_query(WIKI_ADMIN_CAPS)
        self.assertIn("FROM wiki_sections WHERE status = 'active'", sql)

    def test_browsing_admin_goes_through_rules(self):
        sql = self.run_query(WIKI_ADMIN_CAPS, master_key=False)
        self.assertIn('wiki_section_access_rules', sql)


@unittest.skipIf(Flask is None, 'flask не установлен')
class BrowseScopeRouteTest(unittest.TestCase):
    """Какой периметр запрашивает каждый эндпоинт."""

    def build(self, caps, role='admin'):
        self.calls = []
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        context = {'user_id': 42, 'otp_role': role, 'department_id': None,
                   'direction_id': None, 'headed_department_ids': [],
                   'group_ids': [], 'wiki_roles': [
                       dict(WIKI_ADMIN_CAPS, id=5, code='wiki_admin')
                       if caps is WIKI_ADMIN_CAPS else dict(caps, id=6, code='editor')],
                   'access_mode': 'auto'}

        original_load = queries.load_access_context
        queries.load_access_context = lambda _cursor, _uid: dict(context)
        self.addCleanup(setattr, queries, 'load_access_context', original_load)

        def spy_sections(_cursor, _ctx, _subjects, master_key=True):
            self.calls.append(('sections', master_key))
            return {1}

        def spy_visible(_cursor, _ctx, _subjects, _sections, master_key=True):
            self.calls.append(('articles', master_key))
            return set()

        for module, name, spy in ((queries, 'allowed_section_ids', spy_sections),
                                  (wiki_articles, 'visible_article_ids', spy_visible)):
            original = getattr(module, name)
            setattr(module, name, spy)
            self.addCleanup(setattr, module, name, original)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (42, None, None),
            client_ip=lambda: '127.0.0.1',
        ))
        app.config['TESTING'] = True
        return app.test_client()

    def master_keys(self):
        return {flag for _kind, flag in self.calls}

    def test_list_is_personal_for_admin(self):
        client = self.build(WIKI_ADMIN_CAPS)
        self.assertEqual(client.get('/api/wiki/articles').status_code, 200)
        self.assertEqual(self.master_keys(), {False})

    def test_search_and_home_are_personal(self):
        client = self.build(WIKI_ADMIN_CAPS)
        for url in ('/api/wiki/search?q=абв', '/api/wiki/suggest?q=абв', '/api/wiki/home'):
            self.calls = []
            self.assertEqual(client.get(url).status_code, 200, url)
            self.assertEqual(self.master_keys(), {False}, url)

    def test_scope_all_opens_everything_for_admin(self):
        client = self.build(WIKI_ADMIN_CAPS)
        self.assertEqual(client.get('/api/wiki/articles?scope=all').status_code, 200)
        self.assertEqual(self.master_keys(), {True})

    def test_scope_all_is_ignored_without_capability(self):
        """Параметр в адресной строке ничего не открывает сам по себе."""
        client = self.build(EDITOR_CAPS, role='sv')
        self.assertEqual(client.get('/api/wiki/articles?scope=all').status_code, 200)
        self.assertEqual(self.master_keys(), {False})

    def test_direct_article_keeps_master_key(self):
        """Точечное открытие статьи — прежний периметр: администратор чинит статьи."""
        client = self.build(WIKI_ADMIN_CAPS)
        self.assertEqual(client.get('/api/wiki/articles/klassifikator-avto').status_code, 404)
        self.assertEqual(self.master_keys(), {True})

    def test_structure_tree_marks_personal_access(self):
        """Дерево разделов на витрине — тоже личный периметр."""
        client = self.build(WIKI_ADMIN_CAPS)
        self.assertEqual(client.get('/api/wiki/structure').status_code, 200)
        self.assertEqual(self.master_keys(), {False})


if __name__ == '__main__':
    unittest.main()
