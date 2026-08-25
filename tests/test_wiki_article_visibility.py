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

Тест пропускается, если базы нет (например, в CI без секретов). Соединение —
общее на весь набор, см. tests/prod_db.py: у роли лимит в два подключения.
"""

import unittest
from pathlib import Path

from tests import prod_db
from wiki.access import collect_subjects  # noqa: E402
from wiki.articles import _VISIBLE_ARTICLES_SQL
from wiki.queries import subject_params  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

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
           subject_role::text, mode::text, can_read::boolean,
           min_role_level::int
      FROM (VALUES {rules}) AS t(
        article_id, subject_type, subject_id, subject_role, mode, can_read,
        min_role_level)
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
-- Разделы и границы пространств. Синтетическое дерево: любой section_id из
-- теста лежит в пространстве 1. Заглушки обязательны — без них запрос читал бы
-- БОЕВУЮ wiki_sections, и тест зависел бы от того, в каком пространстве сегодня
-- лежит раздел с этим номером на проде.
wiki_sections AS (
    SELECT i::int AS id, 1::int AS space_id FROM generate_series(1, 100) AS i
),
-- Пустая заглушка = пространство видно всем (прежнее поведение). Границу
-- проверяет отдельный тест, передавая space_departments.
wiki_space_departments AS (
    SELECT space_id::int, department_id::int
      FROM (VALUES {space_departments}) AS t(space_id, department_id)
     WHERE space_id IS NOT NULL
),
"""

_EMPTY = {
    'articles': "(NULL::int, NULL::text, NULL::text, NULL::bool, NULL::int, NULL::int)",
    'rules': "(NULL::int, NULL::text, NULL::int, NULL::text, NULL::text, NULL::bool, NULL::int)",
    'sections': "(NULL::int, NULL::int)",
    'guests': "(NULL::int, NULL::int, NULL::timestamp, NULL::timestamp)",
    'space_departments': "(NULL::int, NULL::int)",
}


def _values(rows, fallback):
    return ', '.join(rows) if rows else fallback


class ArticleVisibilitySqlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()

    def visible(self, *, articles, rules=(), sections=(), guests=(),
                user_id=10, role='operator', allowed_sections=(),
                subjects=None, is_wiki_admin=False, can_see_drafts=False,
                can_see_archived=False, draft_sections=(), space_departments=()):
        stub = _STUBS.format(
            articles=_values(articles, _EMPTY['articles']),
            rules=_values(rules, _EMPTY['rules']),
            sections=_values(sections, _EMPTY['sections']),
            guests=_values(guests, _EMPTY['guests']),
            space_departments=_values(space_departments, _EMPTY['space_departments']),
        )
        # Боевой запрос начинается с "WITH my_rules AS (" — приклеиваем заглушки
        # перед ним, ничего не меняя в самом тексте.
        sql = _VISIBLE_ARTICLES_SQL.replace('WITH my_rules AS (', stub + 'my_rules AS (', 1)
        # Субъекты собирает БОЕВОЙ collect_subjects, а параметры — боевой
        # subject_params: иначе тест проверял бы свою копию правил подстановки,
        # а не ту, что работает в проде.
        given = subjects or {}
        subj = collect_subjects(
            user_id=user_id,
            otp_role=role,
            department_id=(given.get('department') or [None])[0],
            headed_department_ids=given.get('department_head') or (),
            direction_id=(given.get('direction') or [None])[0],
            group_ids=given.get('group') or (),
            wiki_role_ids=given.get('wiki_role') or (),
        )
        # Тест адресует правила ролями напрямую (в том числе «только оператор»),
        # поэтому список ролей берём из теста, а не из раскрытия иерархии.
        if 'otp_role' in given:
            subj['otp_role'] = given['otp_role']
        if 'department' in given:
            subj['department'] = given['department']
        params = dict(
            subject_params(subj, user_id),
            sections=list(allowed_sections) or [-1],
            is_wiki_admin=is_wiki_admin,
            is_super_admin=role == 'super_admin',
            can_see_drafts=can_see_drafts,
            can_see_archived=can_see_archived,
            # Разделы, где право выпускать выписано ПРАВИЛОМ: черновик виден
            # тому, кому поручили его выпустить, и только там (wiki/queries.py:
            # granted_rule_rights). Пусто — заведомо непопадающее значение.
            draft_sections=list(draft_sections) or [-1],
        )
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            return {row[0] for row in cur.fetchall()}
        finally:
            prod_db.rollback()
            cur.close()

    # ── Наследование от разделов ─────────────────────────────────────────
    def test_inherit_visible_through_allowed_section(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
        )
        self.assertEqual(got, {1})

    def test_inherit_hidden_when_section_not_allowed(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[7],
        )
        self.assertEqual(got, set())

    # ── Черновик и тот, кому поручили выпуск ─────────────────────────────
    #
    # can_see_drafts — флаг на ВСЮ витрину, и считается он по способностям
    # должности. Право выпускать, выписанное правилом на ОДИН раздел, всю
    # витрину открывать не должно, но и молчать о черновиках этого раздела
    # нельзя: выпустить можно только то, что видишь.

    def test_draft_visible_where_publishing_was_granted(self):
        got = self.visible(
            articles=["(1, 'draft', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
            draft_sections=[5],
        )
        self.assertEqual(got, {1})

    def test_draft_stays_hidden_in_other_sections(self):
        got = self.visible(
            articles=["(1, 'draft', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 7)"],
            allowed_sections=[5, 7],
            draft_sections=[5],
        )
        self.assertEqual(got, set(),
                         'право выпускать в одном разделе открыло черновик соседнего')

    def test_granted_publisher_does_not_get_the_archive(self):
        """Архив — отдельная дверь (can_manage_structure), правилом не открывается."""
        got = self.visible(
            articles=["(1, 'archived', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
            draft_sections=[5],
        )
        self.assertEqual(got, set())

    # ── Режим «только по списку» ─────────────────────────────────────────
    def test_restricted_ignores_sections(self):
        """Главное требование владельца: некоторые статьи нельзя даже читать."""
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
        )
        self.assertEqual(got, set(), 'режим restricted обязан игнорировать разделы')

    def test_restricted_visible_to_listed_role(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL, NULL)"],
            rules=["(1, 'otp_role', NULL, 'operator', 'grant', true, NULL)"],
            subjects={'otp_role': ['operator']},
        )
        self.assertEqual(got, {1})

    # ── Запрет сильнее разрешения ────────────────────────────────────────
    def test_deny_beats_section_grant(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            rules=["(1, 'user', 10, NULL, 'deny', true, NULL)"],
            allowed_sections=[5],
        )
        self.assertEqual(got, set(), 'персональный запрет должен перекрывать доступ по разделу')

    def test_deny_for_other_user_does_not_affect_me(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            rules=["(1, 'user', 777, NULL, 'deny', true, NULL)"],
            allowed_sections=[5],
        )
        self.assertEqual(got, {1})

    # ── Строгий режим ────────────────────────────────────────────────────
    def test_strict_hidden_from_wiki_admin(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', true, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
            is_wiki_admin=True, can_see_drafts=True, can_see_archived=True,
        )
        self.assertEqual(got, set(), 'строгий режим закрыт даже администратору вики')

    def test_strict_open_to_super_admin(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', true, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
            role='super_admin', is_wiki_admin=True,
            can_see_drafts=True, can_see_archived=True,
        )
        self.assertEqual(got, {1})

    def test_strict_open_to_explicit_grant(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', true, 99, NULL, NULL)"],
            rules=["(1, 'user', 10, NULL, 'grant', true, NULL)"],
        )
        self.assertEqual(got, {1})

    # ── Администратор доступов ───────────────────────────────────────────
    def test_wiki_admin_sees_everything_not_strict(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL, NULL)",
                      "(2, 'published', 'inherit', false, 99, NULL, NULL)"],
            is_wiki_admin=True, can_see_drafts=True, can_see_archived=True,
        )
        self.assertEqual(got, {1, 2})

    def test_wiki_admin_overrides_deny(self):
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL, NULL)"],
            rules=["(1, 'user', 10, NULL, 'deny', true, NULL)"],
            is_wiki_admin=True, can_see_drafts=True, can_see_archived=True,
        )
        self.assertEqual(got, {1}, 'администратор не должен уметь заблокировать сам себя')

    # ── Статусы ──────────────────────────────────────────────────────────
    def test_draft_hidden_from_reader(self):
        got = self.visible(
            articles=["(1, 'draft', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
        )
        self.assertEqual(got, set(), 'черновик не должен попадать в выдачу читателю')

    def test_own_draft_visible_to_author(self):
        got = self.visible(
            articles=["(1, 'draft', 'inherit', false, 10, NULL, NULL)"],
            sections=["(1, 5)"],
            allowed_sections=[5],
        )
        self.assertEqual(got, {1})

    def test_archived_hidden_unless_managing_structure(self):
        hidden = self.visible(
            articles=["(1, 'archived', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 5)"], allowed_sections=[5], can_see_drafts=True,
        )
        self.assertEqual(hidden, set())

        shown = self.visible(
            articles=["(1, 'archived', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 5)"], allowed_sections=[5],
            can_see_drafts=True, can_see_archived=True,
        )
        self.assertEqual(shown, {1})

    # ── Гостевой доступ ──────────────────────────────────────────────────
    def test_active_guest_grant_opens_article(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL, NULL)"],
            guests=["(1, 10, NULL::timestamp, (CURRENT_TIMESTAMP + interval '1 day')::timestamp)"],
        )
        self.assertEqual(got, {1})

    def test_expired_guest_grant_is_ignored(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL, NULL)"],
            guests=["(1, 10, NULL::timestamp, (CURRENT_TIMESTAMP - interval '1 day')::timestamp)"],
        )
        self.assertEqual(got, set())

    def test_revoked_guest_grant_is_ignored(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL, NULL)"],
            guests=["(1, 10, CURRENT_TIMESTAMP::timestamp, (CURRENT_TIMESTAMP + interval '1 day')::timestamp)"],
        )
        self.assertEqual(got, set())

    # ── Гостевой доступ и граница пространства ───────────────────────────
    #
    # Границ ДВЕ, и они независимы: одна отсекает разделы (queries), вторая —
    # сами статьи (этот запрос). Решение владельца 25.08.2026 открыло гостю
    # чужое пространство, и открыть его надо в ОБЕИХ: пусти мы гостя только к
    # разделам, раздел из чужого отдела появился бы в дереве, а статьи в нём
    # остались бы отфильтрованы здесь. Пустая папка вместо регламента — тот же
    # молчаливый отказ, ради которого исключение и делали.
    def test_guest_article_grant_crosses_the_space_border(self):
        """Выданную СТАТЬЮ гость видит, даже если пространство закрыто его отделу."""
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            guests=["(1, 10, NULL::timestamp, (CURRENT_TIMESTAMP + interval '1 day')::timestamp)"],
            space_departments=['(1, 367)'],
        )
        self.assertEqual(got, {1})

    def test_guest_section_grant_crosses_the_space_border(self):
        """И статьи выданного РАЗДЕЛА тоже.

        Раздел сюда приезжает уже посчитанным (allowed_sections): его пустила
        гостевая выдача, пробившая границу разделов. Здесь проверяется, что
        вторая граница на этом же не споткнётся.
        """
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 5)"], allowed_sections=[5],
            space_departments=['(1, 367)'],
        )
        self.assertEqual(got, {1})

    def test_article_rule_still_does_not_cross_the_space_border(self):
        """А ПРАВИЛО на статью — по-прежнему нет. Это и есть смысл границы.

        Тот самый инцидент, ради которого границу на статью и завели:
        статья-классификатор роздана всем ролям OTP (restricted, семь grant-правил),
        разделы у неё игнорируются по определению режима — и она открывалась Тез
        КЦ, которому вика не выдана ни одним пространством. Исключение для гостя
        эту дверь открыть не должно: гостевая выдача именная и с часами, а
        правило на должность действует по всей компании и бессрочно.
        """
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            rules=["(1, 'otp_role', NULL, 'operator', 'grant', true, NULL)"],
            space_departments=['(1, 367)'],
        )
        self.assertEqual(got, set())

    def test_authorship_still_does_not_cross_the_space_border(self):
        """Авторство — тоже нет: «но это же моя статья» границу не отменяет."""
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 10, NULL, NULL)"],
            sections=["(1, 5)"],
            space_departments=['(1, 367)'],
        )
        self.assertEqual(got, set())

    def test_expired_guest_grant_does_not_cross_the_space_border(self):
        """Щель закрывается вместе с выдачей, а не остаётся открытой навсегда."""
        got = self.visible(
            articles=["(1, 'published', 'inherit', false, 99, NULL, NULL)"],
            sections=["(1, 5)"],
            guests=["(1, 10, NULL::timestamp, (CURRENT_TIMESTAMP - interval '1 day')::timestamp)"],
            space_departments=['(1, 367)'],
        )
        self.assertEqual(got, set())

    # ── Субъекты ─────────────────────────────────────────────────────────
    def test_grant_by_department(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL, NULL)"],
            rules=["(1, 'department', 3, NULL, 'grant', true, NULL)"],
            subjects={'department': [3]},
        )
        self.assertEqual(got, {1})

    def test_grant_by_group_does_not_leak_to_other_group(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', false, 99, NULL, NULL)"],
            rules=["(1, 'group', 3, NULL, 'grant', true, NULL)"],
            subjects={'group': [4]},
        )
        self.assertEqual(got, set())

    def test_author_always_sees_own_article(self):
        got = self.visible(
            articles=["(1, 'published', 'restricted', true, 10, NULL, NULL)"],
        )
        self.assertEqual(got, {1}, 'автор не должен терять доступ к собственной статье')


if __name__ == '__main__':
    unittest.main()
