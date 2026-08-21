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

    def build(self, context, *, section_rules, granted=None):
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
            # Права, УЖЕ выписанные человеку правилами: с 21.08.2026 из них
            # считается способность (queries.load_capabilities). По умолчанию
            # берём те же правила раздела — ровно так это и работает на боевом
            # пути, где выписанное правило поднимает способность само.
            (queries, 'granted_rule_rights',
             lambda _c, _s, _u: (dict(granted if granted is not None
                                      else (section_rules[0] if section_rules else {})),
                                 [])),
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

    def test_rule_may_grant_beyond_the_role(self):
        """Правило сильнее умолчания должности — но ровно там, где выписано.

        До 21.08.2026 тест требовал обратного: правило разрешает удаление, а у
        роли такой способности нет — значит нельзя. Эта трактовка и гасила
        выданное право молча (инцидент разобран в шапке
        access.capabilities_from_grants): способность — не потолок, а «вправе ли
        в принципе», и выписанное правило её поднимает.

        Прежний страж «СВ не сносит ЛЮБУЮ статью» никуда не делся, он просто
        стоит в двух других местах: удаление работает только в разделе, где оно
        выписано (следующий тест), а поставить галочку «Удалять» может лишь тот,
        у кого право удалять есть у самого (routes_structure:
        WIKI_GRANT_BEYOND_SELF).
        """
        rules = [{'can_read': True, 'can_create': True, 'can_edit': True,
                  'can_delete': True, 'can_publish': True, 'can_approve': False}]
        client = self.build(make_context('sv', [EDITOR_ROLE]), section_rules=rules)

        self.assertEqual(client.patch('/api/wiki/articles/7',
                                      json={'title': 'Новое'}).status_code, 200)
        self.assertEqual(client.delete('/api/wiki/articles/7').status_code, 200)

    def test_rule_without_delete_still_refuses(self):
        """Обратная половина: чего в правиле нет, того нет и после починки."""
        rules = [{'can_read': True, 'can_create': True, 'can_edit': True,
                  'can_delete': False, 'can_publish': True, 'can_approve': False}]
        client = self.build(make_context('sv', [EDITOR_ROLE]), section_rules=rules)

        self.assertEqual(client.patch('/api/wiki/articles/7',
                                      json={'title': 'Новое'}).status_code, 200)
        response = client.delete('/api/wiki/articles/7')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('required'), 'can_delete')

    # ── Инцидент 21.08.2026: оператор с персональным правилом ────────────
    #
    # Оператору СЗоВ выписали правило на раздел «Супервайзер» со всеми шестью
    # правами. Правило сохранилось и показывалось, а человек не мог ни завести
    # статью, ни поправить букву: способность выводилась из одной лишь
    # должности, у 'operator' сверх чтения нет ничего, и право гасло молча —
    # в декораторе роута (403 на POST /articles) и в расчёте прав на статью.
    #
    # Прежний test_create_requires_capability ставил section_rules=[] и этот
    # сценарий не видел вовсе, поэтому ловушка вернулась второй раз подряд.
    FULL_RULE = {'can_read': True, 'can_create': True, 'can_edit': True,
                 'can_delete': True, 'can_publish': True, 'can_approve': True}

    def test_operator_with_personal_rule_may_edit(self):
        client = self.build(make_context('operator'), section_rules=[self.FULL_RULE])
        self.assertEqual(client.patch('/api/wiki/articles/7',
                                      json={'title': 'Новое'}).status_code, 200)

    def test_operator_with_personal_rule_passes_the_route_gate(self):
        """POST /articles обязан дойти до проверки раздела, а не упасть в декораторе."""
        client = self.build(make_context('operator'), section_rules=[self.FULL_RULE])
        response = client.post('/api/wiki/articles',
                               json={'title': 'Новая', 'section_ids': [3]})
        self.assertNotEqual(response.status_code, 403, response.get_json())

    def test_create_checks_every_chosen_section(self):
        """Право нужно на КАЖДОМ выбранном разделе, а не «хоть на одном».

        set_sections кладёт статью во все переданные разом (wiki/edit.py),
        поэтому прежняя проверка any() пропускала её в соседнюю ветку заодно с
        разрешённой. Стенд отдаёт правила только для раздела 3 — раздел 9
        остаётся без прав.
        """
        client = self.build(make_context('operator'), section_rules=[self.FULL_RULE])
        response = client.post('/api/wiki/articles',
                               json={'title': 'Новая', 'section_ids': [3, 9]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SECTION_FORBIDDEN')

    def test_create_without_a_section_checks_the_fallback(self):
        """Не выбрать раздел — не способ обойти проверку.

        Статья без разделов падает в запасной «Общий сотрудник»
        (wiki/edit.py: default_section_id), и право спрашивается на него.
        """
        self.addCleanup(setattr, wiki_edit, 'default_section_id',
                        wiki_edit.default_section_id)
        wiki_edit.default_section_id = lambda *a, **k: 9

        client = self.build(make_context('operator'), section_rules=[self.FULL_RULE])
        response = client.post('/api/wiki/articles', json={'title': 'Новая'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SECTION_FORBIDDEN')

    def test_move_to_a_foreign_section_is_refused(self):
        """Статью нельзя молча увезти в раздел, к которому прав нет."""
        client = self.build(make_context('sv', [EDITOR_ROLE]),
                            section_rules=[self.FULL_RULE])
        response = client.patch('/api/wiki/articles/7', json={'section_ids': [9]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SECTION_FORBIDDEN')

    def test_move_inside_allowed_sections_passes(self):
        """Раздел статьи не меняется — проверять нечего, правка проходит."""
        client = self.build(make_context('sv', [EDITOR_ROLE]),
                            section_rules=[self.FULL_RULE])
        self.assertEqual(
            client.patch('/api/wiki/articles/7', json={'section_ids': [3]}).status_code,
            200)

    def test_rule_in_another_section_does_not_travel(self):
        """Способность поднялась, но раздел решает сам.

        Право выписано где-то ещё (granted), а на разделе этой статьи правил
        нет — значит правки нет. Иначе объединение способностей превратилось бы
        в «можно везде», а решает по-прежнему правило объекта.
        """
        client = self.build(make_context('operator'), section_rules=[],
                            granted=self.FULL_RULE)
        self.assertEqual(client.patch('/api/wiki/articles/7',
                                      json={'title': 'x'}).status_code, 403)

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
