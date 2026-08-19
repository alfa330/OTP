# -*- coding: utf-8 -*-
"""Периметр разделов: «своё и всё, что ниже» — проверка САМОГО SQL.

Здесь проверяется то, ради чего заведены min_role_level и субъект
'department_head': чтобы в дереве «Коммерческого отдела» одновременно работало

  * оператор ОП видит свою ветку и НЕ видит ветку ОТП;
  * супервайзер видит раздел СВ и операторские ветки СВОЕГО отдела;
  * супервайзер ЧУЖОГО отдела не видит ни того, ни другого;
  * глава отдела попадает и туда, и туда, а правило на него переезжает вместе
    со сменой назначения;
  * «Общий сотрудник» виден всем без единого правила.

Приём тот же, что в test_wiki_article_visibility: CTE в PostgreSQL перекрывает
одноимённую таблицу, поэтому боевой текст _AUTO_SECTIONS_SQL исполняется над
синтетическими строками. Соединение read-only — боевые таблицы не читаются.
"""

import unittest

from tests import prod_db
from wiki.access import ROLE_LEVELS, collect_subjects
from wiki.queries import _AUTO_SECTIONS_SQL, subject_params

# Разделы дерева: id → как называем в тестах.
DIRECTOR, HEAD, SUPERVISOR, OPERATOR, BRANCH_OP, BRANCH_OTP, COMMON = 1, 2, 3, 4, 5, 6, 7

DEPT_OP, DEPT_OTP = 367, 1

# Без ведущего WITH: заглушки вклеиваются внутрь боевого
# "WITH RECURSIVE ...", а второй WITH в одном запросе недопустим.
_STUBS = """
wiki_section_access_rules AS (
    SELECT section_id::int, subject_type::text, subject_id::int, subject_role::text,
           can_read::boolean, grant_subsections::boolean, min_role_level::int
      FROM (VALUES {rules}) AS t(
        section_id, subject_type, subject_id, subject_role,
        can_read, grant_subsections, min_role_level)
),
wiki_sections AS (
    SELECT id::int, parent_section_id::int, status::text,
           visibility_scope::text, owner_user_id::int
      FROM (VALUES {sections}) AS t(
        id, parent_section_id, status, visibility_scope, owner_user_id)
),
wiki_guest_access AS (
    SELECT section_id::int, user_id::int,
           revoked_at::timestamp, expires_at::timestamp
      FROM (VALUES {guests}) AS t(section_id, user_id, revoked_at, expires_at)
),
-- Кому виден публичный раздел. По умолчанию заглушка ПУСТАЯ: это и есть
-- прежний смысл «публичного» — виден всем, — и все сценарии ниже опираются
-- на него. Наполняется только там, где проверяется само сужение.
wiki_section_public_departments AS (
    SELECT section_id::int, department_id::int
      FROM (VALUES {public_departments}) AS t(section_id, department_id)
     WHERE section_id IS NOT NULL
),
"""

_EMPTY_GUESTS = "(NULL::int, NULL::int, NULL::timestamp, NULL::timestamp)"

# Пустой список отделов у публичных разделов = «виден всем», прежнее поведение.
_EMPTY_PUBLIC_DEPARTMENTS = "(NULL::int, NULL::int)"

# Дерево, которое собирают руками во вкладке «Структура»:
# Коммерческий директор → отдел (СЗоВ / ОП) → должность (рук / СВ / оператор).
_TREE = [
    "(1, NULL, 'active', 'restricted', NULL)",   # Коммерческий директор
    "(2, NULL, 'active', 'restricted', NULL)",   # Руководитель группы
    "(3, NULL, 'active', 'restricted', NULL)",   # Супервайзер
    "(4, NULL, 'active', 'restricted', NULL)",   # Оператор
    "(5, 4,    'active', 'restricted', NULL)",   # └ ОП
    "(6, 4,    'active', 'restricted', NULL)",   # └ ОТП
    "(7, NULL, 'active', 'public',     NULL)",   # Общий сотрудник
]

# Правила — те, что выставляются во вкладке «Доступы».
_RULES = [
    # Коммерческий директор — только по уровню.
    "(1, 'otp_role', NULL, 'super_admin', true, true, 50)",
    # Руководитель группы — по назначению главой.
    "(2, 'department_head', 367, NULL, true, true, NULL)",
    "(2, 'department_head', 1,   NULL, true, true, NULL)",
    # Супервайзер — отдел И не ниже уровня СВ.
    "(3, 'department', 367, NULL, true, true, 30)",
    "(3, 'department', 1,   NULL, true, true, 30)",
    # Оператор — узел-родитель, вглубь НЕ раздаёт.
    "(4, 'department', 367, NULL, true, false, NULL)",
    "(4, 'department', 1,   NULL, true, false, NULL)",
    # Ветки отделов.
    "(5, 'department', 367, NULL, true, true, NULL)",
    "(6, 'department', 1,   NULL, true, true, NULL)",
]


class SectionPerimeterSqlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()

    def sections(self, *, role, department_id=None, headed=(), user_id=10,
                 rules=None, tree=None, guests=(), public_departments=()):
        stub = _STUBS.format(
            rules=', '.join(rules if rules is not None else _RULES),
            sections=', '.join(tree if tree is not None else _TREE),
            guests=', '.join(guests) if guests else _EMPTY_GUESTS,
            public_departments=(', '.join(public_departments) if public_departments
                                else _EMPTY_PUBLIC_DEPARTMENTS),
        )
        # Боевой запрос начинается с "WITH RECURSIVE rule_hits AS (" —
        # заглушки встают первыми элементами того же WITH, текст запроса не меняется.
        sql = _AUTO_SECTIONS_SQL.replace(
            'WITH RECURSIVE rule_hits AS (',
            'WITH RECURSIVE ' + stub.strip() + ' rule_hits AS (', 1)

        subjects = collect_subjects(
            user_id=user_id, otp_role=role,
            department_id=department_id, headed_department_ids=headed)
        cur = self.conn.cursor()
        try:
            cur.execute(sql, subject_params(subjects, user_id))
            return {row[0] for row in cur.fetchall()}
        finally:
            prod_db.rollback()
            cur.close()

    # ── Оператор ─────────────────────────────────────────────────────────
    def test_operator_sees_own_branch_only(self):
        got = self.sections(role='operator', department_id=DEPT_OP)
        self.assertEqual(got, {OPERATOR, BRANCH_OP, COMMON})

    def test_operator_does_not_see_other_department_branch(self):
        got = self.sections(role='operator', department_id=DEPT_OP)
        self.assertNotIn(BRANCH_OTP, got, 'оператор ОП не должен видеть ветку ОТП')

    def test_operator_does_not_see_supervisor_section(self):
        got = self.sections(role='operator', department_id=DEPT_OP)
        self.assertNotIn(SUPERVISOR, got,
                         'правило на отдел без уровня открыло бы раздел СВ операторам')

    # ── Супервайзер ──────────────────────────────────────────────────────
    def test_supervisor_sees_own_department_below(self):
        got = self.sections(role='sv', department_id=DEPT_OP)
        self.assertEqual(got, {SUPERVISOR, OPERATOR, BRANCH_OP, COMMON})

    def test_supervisor_does_not_cross_department_border(self):
        got = self.sections(role='sv', department_id=DEPT_OP)
        self.assertNotIn(BRANCH_OTP, got,
                         'правило на роль sv пробило бы границу отдела — его тут быть не должно')

    def test_supervisor_does_not_see_director_section(self):
        got = self.sections(role='sv', department_id=DEPT_OP)
        self.assertNotIn(DIRECTOR, got)

    # ── Глава отдела ─────────────────────────────────────────────────────
    def test_head_sees_own_section_and_everything_below(self):
        got = self.sections(role='admin', department_id=DEPT_OP, headed=[DEPT_OP])
        self.assertEqual(got, {HEAD, SUPERVISOR, OPERATOR, BRANCH_OP, COMMON})

    def test_head_of_other_department_does_not_leak(self):
        got = self.sections(role='admin', department_id=DEPT_OTP, headed=[DEPT_OTP])
        self.assertNotIn(BRANCH_OP, got)

    def test_head_rule_follows_assignment_not_person(self):
        """Тот же человек без назначения главой раздел «Руководитель» не видит."""
        with_assignment = self.sections(role='admin', department_id=DEPT_OP, headed=[DEPT_OP])
        without = self.sections(role='admin', department_id=DEPT_OP, headed=[])
        self.assertIn(HEAD, with_assignment)
        self.assertNotIn(HEAD, without)

    # ── Коммерческий директор ────────────────────────────────────────────
    def test_super_admin_level_opens_director_section(self):
        got = self.sections(role='super_admin', department_id=DEPT_OTP)
        self.assertIn(DIRECTOR, got)

    def test_admin_level_is_below_director_section(self):
        got = self.sections(role='admin', department_id=DEPT_OP, headed=[DEPT_OP])
        self.assertNotIn(DIRECTOR, got,
                         'уровень 40 не должен проходить в раздел с порогом 50')

    # ── Публичный раздел ─────────────────────────────────────────────────
    def test_public_section_visible_without_any_rule(self):
        for role, dept in (('operator', DEPT_OP), ('operator', DEPT_OTP),
                           ('sv', DEPT_OP), ('admin', None)):
            with self.subTest(role=role, department=dept):
                self.assertIn(COMMON, self.sections(role=role, department_id=dept),
                              '«Общий сотрудник» обязан быть виден всем')

    def test_user_without_department_sees_only_public(self):
        got = self.sections(role='operator', department_id=None)
        self.assertEqual(got, {COMMON})

    # ── Кому виден публичный раздел ──────────────────────────────────────
    #
    # «Публичный» перестал автоматически значить «всем в компании»: раздел
    # «Общий сотрудник» открывался в том числе Тез КЦ, которому вики не
    # предназначена. Список отделов сужает публичность; пустой список
    # сохраняет прежний смысл — на нём стоят все остальные сценарии файла.

    def test_public_without_list_stays_visible_to_everyone(self):
        """Обратная совместимость: у существующих публичных разделов списка нет."""
        got = self.sections(role='operator', department_id=999)
        self.assertIn(COMMON, got)

    def test_public_list_opens_only_to_listed_departments(self):
        allowed = self.sections(role='operator', department_id=DEPT_OP,
                                public_departments=['(%d, %d)' % (COMMON, DEPT_OP)])
        self.assertIn(COMMON, allowed)

        denied = self.sections(role='operator', department_id=DEPT_OTP,
                               public_departments=['(%d, %d)' % (COMMON, DEPT_OP)])
        self.assertNotIn(COMMON, denied,
                         'отдел вне списка не должен видеть публичный раздел')

    def test_public_list_does_not_leak_to_user_without_department(self):
        """Сотрудник без отдела под сужение не подпадает.

        subject_params подставляет для пустого списка отделов -1, и раздел
        не должен совпасть с ним по случайности.
        """
        got = self.sections(role='operator', department_id=None,
                            public_departments=['(%d, %d)' % (COMMON, DEPT_OP)])
        self.assertNotIn(COMMON, got)

    def test_public_list_does_not_touch_rule_based_access(self):
        """Сужение публичности не отбирает то, что выдано правилом.

        У оператора ОП ветка открыта правилом; список на «Общем сотруднике»
        к ней отношения не имеет.
        """
        got = self.sections(role='operator', department_id=DEPT_OP,
                            public_departments=['(%d, %d)' % (COMMON, DEPT_OTP)])
        self.assertNotIn(COMMON, got)
        self.assertIn(OPERATOR, got, 'правило отдела должно продолжать работать')

    def test_head_of_listed_department_sees_it(self):
        """Глава отдела подпадает под список так же, как его сотрудники.

        collect_subjects кладёт возглавляемые отделы в тот же список
        departments, и отдельной ветки для главы здесь быть не должно.
        """
        got = self.sections(role='admin', department_id=None, headed=[DEPT_OP],
                            public_departments=['(%d, %d)' % (COMMON, DEPT_OP)])
        self.assertIn(COMMON, got)

    # ── Родитель не раздаёт вглубь ───────────────────────────────────────
    def test_operator_parent_rule_does_not_open_sibling_branch(self):
        """grant_subsections=false на «Операторе» — единственное, что держит границу.

        Если правило на родителе станет глубоким, рекурсивный CTE выдаст ОБЕ
        ветки, и разделение отделов исчезнет молча — тест это ловит.
        """
        deep = [r.replace("(4, 'department', 367, NULL, true, false, NULL)",
                          "(4, 'department', 367, NULL, true, true, NULL)")
                for r in _RULES]
        got = self.sections(role='operator', department_id=DEPT_OP, rules=deep)
        self.assertIn(BRANCH_OTP, got,
                      'проверка самой проверки: с глубоким правилом утечка обязана появиться')

    # ── Уровень как порог, а не как равенство ────────────────────────────
    def test_min_role_level_is_threshold_not_equality(self):
        got = self.sections(role='admin', department_id=DEPT_OP, headed=[DEPT_OP])
        self.assertIn(SUPERVISOR, got,
                      'уровень 40 обязан проходить порог 30 — иначе «всё, что ниже» не работает')
        self.assertEqual(ROLE_LEVELS['admin'] > ROLE_LEVELS['sv'], True)


if __name__ == '__main__':
    unittest.main()


class SectionSlugTest(unittest.TestCase):
    """Повтор названия раздела не должен падать в «Внутреннюю ошибку».

    На (space_id, slug) висит UNIQUE, и до фикса вторая попытка создать раздел
    с тем же названием доходила до обработчика ошибок как 500 — человек в
    конструкторе видел красное уведомление без единого намёка на причину.
    Слаг может занимать и АРХИВНЫЙ раздел: архивируют обычно одноимённый дубль.
    """

    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()

    def free_slug(self, taken, base, exclude_id=None):
        """Гоняем боевую функцию на подставном наборе занятых слагов."""
        rows = ', '.join("(%d, %d, '%s')" % (i + 1, 1, slug) for i, slug in enumerate(taken))
        stub = """
        WITH wiki_sections AS (
            SELECT id::int, space_id::int, slug::text
              FROM (VALUES %s) AS t(id, space_id, slug)
        )
        """ % (rows or "(NULL::int, NULL::int, NULL::text)")

        cur = self.conn.cursor()
        try:
            slug, suffix = base, 2
            while True:
                cur.execute(
                    stub + 'SELECT 1 FROM wiki_sections WHERE space_id = %s AND slug = %s '
                           'AND (%s::int IS NULL OR id <> %s::int) LIMIT 1',
                    (1, slug, exclude_id, exclude_id),
                )
                if cur.fetchone() is None:
                    return slug
                slug = '%s-%d' % (base, suffix)
                suffix += 1
        finally:
            prod_db.rollback()
            cur.close()

    def test_free_slug_is_returned_as_is(self):
        self.assertEqual(self.free_slug(['op'], 'szov'), 'szov')

    def test_taken_slug_gets_a_number(self):
        self.assertEqual(self.free_slug(['op'], 'op'), 'op-2')

    def test_numbering_continues_past_existing_copies(self):
        self.assertEqual(self.free_slug(['op', 'op-2', 'op-3'], 'op'), 'op-4')

    def test_own_slug_does_not_block_its_own_update(self):
        """Правка раздела без смены названия не должна плодить «op-2»."""
        self.assertEqual(self.free_slug(['op'], 'op', exclude_id=1), 'op')
