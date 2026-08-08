# -*- coding: utf-8 -*-
"""Видимость статей: проверка САМОГО SQL на синтетических данных.

Зачем отдельный тест. Логика доступа неизбежно живёт в двух местах: в Python
(wiki.access.resolve_article_permissions — считает права на одну статью) и в SQL
(wiki.articles.visible_article_ids — считает множество читаемых статей одним
запросом, потому что иначе список статей стоил бы N запросов). Ровно на таком
раздвоении сломалась исходная вика: getRuleAllowedSectionIds и
getUserAllowedSections расходились, и дерево навигации показывало не то же
самое, что список статей.

Поэтому SQL проверяется напрямую, а не через «доверяем, что он повторяет Python».

Приём: в PostgreSQL CTE перекрывает одноимённую таблицу. Подставляем перед
запросом CTE-заглушки с именами wiki_articles / wiki_article_access_rules /
wiki_article_sections / wiki_guest_access — и тот же самый боевой текст запроса
исполняется над синтетическими строками. Соединение read-only, боевые таблицы
не читаются и тем более не изменяются.

Тест пропускается, если нет DATABASE_URL_READONLY (например, в CI без секретов).
"""

import os
import re
import unittest
from pathlib import Path

try:
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None

from wiki.articles import _VISIBLE_ARTICLES_SQL

ROOT = Path(__file__).resolve().parents[1]


def _dsn():
    env = os.environ.get('DATABASE_URL_READONLY')
    if env:
        return env
    local = ROOT / '.env.codex.local'
    if not local.exists():
        return None
    text = local.read_text(encoding='utf-8', errors='replace')
    match = re.search(r'^DATABASE_URL_READONLY\s*=\s*(.+)$', text, re.M)
    return match.group(1).strip().strip('"\'') if match else None


DSN = _dsn()

# Колонки заглушек — ровно те, что читает боевой запрос.
# Типы приводим явно: NULL в VALUES без приведения Postgres считает text,
# и сравнение owner_user_id = 10 падает на «operator does not exist».
_STUBS = """
WITH wiki_articles AS (
    SELECT id::int, status::text, visibility_mode::text, strict_mode::boolean,
           author_id::int, owner_user_id::int
      FROM (VALUES {articles}) AS t(
        id, status, visibility_mode, strict_mode, author_id, owner_user_id)
),
wiki_article_access_rules AS (
    SELECT article_id::int, subject_type::text, subject_id::int,
           subject_role::text, mode::text, can_read::boolean
      FROM (VALUES {rules}) AS t(
        article_id, subject_type, subject_id, subject_role, mode, can_read)
),
wiki_article_sections AS (
    SELECT article_id::int, section_id::int
      FROM (VALUES {sections}) AS t(article_id, section_id)
),
wiki_guest_access AS (
    SELECT article_id::int, user_id::int,
           revoked_at::timestamp, expires_at::timestamp
      FROM (VALUES {guests}) AS t(
        article_id, user_id, revoked_at, expires_at)
),
"""

_EMPTY = {
    'articles': "(NULL::int, NULL::text, NULL::text, NULL::bool, NULL::int, NULL::int)",
    'rules': "(NULL::int, NULL::text, NULL::int, NULL::text, NULL::text, NULL::bool)",
    'sections': "(NULL::int, NULL::int)",
    'guests': "(NULL::int, NULL::int, NULL::timestamp, NULL::timestamp)",
}


def _values(rows, fallback):
    return ', '.join(rows) if rows else fallback


@unittest.skipIf(psycopg2 is None or not DSN, 'нет psycopg2 или DATABASE_URL_READONLY')
class ArticleVisibilitySqlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg2.connect(DSN, connect_timeout=30)
        cls.conn.set_session(readonly=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def visible(self, *, articles, rules=(), sections=(), guests=(),
                user_id=10, role='operator', allowed_sections=(),
                subjects=None, is_wiki_admin=False, can_see_drafts=False,
                can_see_archived=False):
        stub = _STUBS.format(
            articles=_values(articles, _EMPTY['articles']),
            rules=_values(rules, _EMPTY['rules']),
            sections=_values(sections, _EMPTY['sections']),
            guests=_values(guests, _EMPTY['guests']),
        )
        # Боевой запрос начинается с "WITH my_rules AS (" — приклеиваем заглушки
        # перед ним, ничего не меняя в самом тексте.
        sql = _VISIBLE_ARTICLES_SQL.replace('WITH my_rules AS (', stub + 'my_rules AS (', 1)
        subjects = subjects or {}
        params = {
            'user_id': user_id,
            'sections': list(allowed_sections) or [-1],
            'departments': subjects.get('department') or [-1],
            'directions': subjects.get('direction') or [-1],
            'groups': subjects.get('group') or [-1],
            'roles': subjects.get('otp_role') or [''],
            'wiki_roles': subjects.get('wiki_role') or [-1],
            'is_wiki_admin': is_wiki_admin,
            'is_super_admin': role == 'super_admin',
            'can_see_drafts': can_see_drafts,
            'can_see_archived': can_see_archived,
        }
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            return {row[0] for row in cur.fetchall()}
        finally:
            self.conn.rollback()
            cur.close()

    # ── Наследование от разделов ─────────────────────────────────────────
    def test_inherit_visible_through_allowed_section(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
        )
        self.assertEqual(got, {1})

    def test_inherit_hidden_when_section_not_allowed(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[7],
        )
        self.assertEqual(got, set())

    # ── Режим «только по списку» ─────────────────────────────────────────
    def test_restricted_ignores_sections(self):
        """Главное требование владельца: некоторые статьи нельзя даже читать."""
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
        )
        self.assertEqual(got, set(), 'режим restricted обязан игнорировать разделы')

    def test_restricted_visible_to_listed_role(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL)"],
            rules=["(1, 'otp_role', NULL, 'operator', 'grant', true)"],
            subjects={'otp_role': ['operator']},
        )
        self.assertEqual(got, {1})

    # ── Запрет сильнее разрешения ────────────────────────────────────────
    def test_deny_beats_section_grant(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL)"],
            sections=["(1, 5)"],
            rules=["(1, 'user', 10, NULL, 'deny', true)"],
            allowed_sections=[5],
        )
        self.assertEqual(got, set(), 'персональный запрет должен перекрывать доступ по разделу')

    def test_deny_for_other_user_does_not_affect_me(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL)"],
            sections=["(1, 5)"],
            rules=["(1, 'user', 777, NULL, 'deny', true)"],
            allowed_sections=[5],
        )
        self.assertEqual(got, {1})

    # ── Строгий режим ────────────────────────────────────────────────────
    def test_strict_hidden_from_wiki_admin(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', true, 99, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
            is_wiki_admin=True, can_see_drafts=True, can_see_archived=True,
        )
        self.assertEqual(got, set(), 'строгий режим закрыт даже администратору вики')

    def test_strict_open_to_super_admin(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', true, 99, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
            role='super_admin', is_wiki_admin=True,
            can_see_drafts=True, can_see_archived=True,
        )
        self.assertEqual(got, {1})

    def test_strict_open_to_explicit_grant(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', true, 99, NULL)"],
            rules=["(1, 'user', 10, NULL, 'grant', true)"],
        )
        self.assertEqual(got, {1})

    # ── Администратор доступов ───────────────────────────────────────────
    def test_wiki_admin_sees_everything_not_strict(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL)",
                      "(2, 'published', 'inherit', false, 99, NULL)"],
            is_wiki_admin=True, can_see_drafts=True, can_see_archived=True,
        )
        self.assertEqual(got, {1, 2})

    def test_wiki_admin_overrides_deny(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL)"],
            rules=["(1, 'user', 10, NULL, 'deny', true)"],
            is_wiki_admin=True, can_see_drafts=True, can_see_archived=True,
        )
        self.assertEqual(got, {1}, 'администратор не должен уметь заблокировать сам себя')

    # ── Статусы ──────────────────────────────────────────────────────────
    def test_draft_hidden_from_reader(self):
        got = self.visible(
            articles=["(1, 'draft', 'inherit', false, 99, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
        )
        self.assertEqual(got, set(), 'черновик не должен попадать в выдачу читателю')

    def test_own_draft_visible_to_author(self):
        got = self.visible(
            articles=["(1, 'draft', 'inherit', false, 10, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
        )
        self.assertEqual(got, {1})

    def test_archived_hidden_unless_managing_structure(self):
        hidden = self.visible(
            articles=["(1, 'archived', 'inherit', false, 99, NULL)"],
            sections=["(1, 5)"], allowed_sections=[5], can_see_drafts=True,
        )
        self.assertEqual(hidden, set())

        shown = self.visible(
            articles=["(1, 'archived', 'inherit', false, 99, NULL)"],
            sections=["(1, 5)"], allowed_sections=[5],
            can_see_drafts=True, can_see_archived=True,
        )
        self.assertEqual(shown, {1})

    # ── Гостевой доступ ──────────────────────────────────────────────────
    def test_active_guest_grant_opens_article(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL)"],
            guests=["(1, 10, NULL::timestamp, (CURRENT_TIMESTAMP + interval '1 day')::timestamp)"],
        )
        self.assertEqual(got, {1})

    def test_expired_guest_grant_is_ignored(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL)"],
            guests=["(1, 10, NULL::timestamp, (CURRENT_TIMESTAMP - interval '1 day')::timestamp)"],
        )
        self.assertEqual(got, set())

    def test_revoked_guest_grant_is_ignored(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL)"],
            guests=["(1, 10, CURRENT_TIMESTAMP::timestamp, (CURRENT_TIMESTAMP + interval '1 day')::timestamp)"],
        )
        self.assertEqual(got, set())

    # ── Субъекты ─────────────────────────────────────────────────────────
    def test_grant_by_department(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL)"],
            rules=["(1, 'department', 3, NULL, 'grant', true)"],
            subjects={'department': [3]},
        )
        self.assertEqual(got, {1})

    def test_grant_by_group_does_not_leak_to_other_group(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL)"],
            rules=["(1, 'group', 3, NULL, 'grant', true)"],
            subjects={'group': [4]},
        )
        self.assertEqual(got, set())

    def test_author_always_sees_own_article(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', true, 10, NULL)"],
        )
        self.assertEqual(got, {1}, 'автор не должен терять доступ к собственной статье')


if __name__ == '__main__':
    unittest.main()
