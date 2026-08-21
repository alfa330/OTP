# -*- coding: utf-8 -*-
"""«Вместе с подразделами» — проверка САМОГО SQL, которым считаются права записи.

Тумблер в интерфейсе обещает дословно: «Те же права во всех вложенных разделах,
включая созданные позже» (WikiSectionAccess.jsx). До 21.08.2026 вглубь уходило
только ЧТЕНИЕ: рекурсия по потомкам жила в периметре (_AUTO_SECTIONS_SQL), а
права записи брались плоским запросом по точному section_id. Правка подразделов
молча не работала — тот же молчаливый отказ, что и в инциденте с персональным
правилом, только с другой стороны.

Приём тот же, что в test_wiki_section_perimeter: CTE в PostgreSQL перекрывает
одноимённую таблицу, поэтому боевой текст _SECTION_RIGHTS_CTE исполняется над
синтетическими строками. Соединение read-only — боевые таблицы не читаются.
"""

import unittest

from tests import prod_db
from wiki.access import collect_subjects
from wiki.queries import _SECTION_RIGHTS_CTE, subject_params

# Дерево: 1 «Директор» → 2 «Руководитель» → 3 «Супервайзер» → 4 «Оператор».
DIRECTOR, HEAD, SUPERVISOR, OPERATOR = 1, 2, 3, 4
DEPT = 1
USER = 158

_TREE = [
    "(1, NULL, 'active', 1)",
    "(2, 1,    'active', 1)",
    "(3, 2,    'active', 1)",
    "(4, 3,    'active', 1)",
]

_NO_SPACE_DEPARTMENTS = "(NULL::int, NULL::int)"

# Без ведущего WITH: заглушки вклеиваются внутрь боевого "WITH RECURSIVE ...".
_STUBS = """
wiki_section_access_rules AS (
    SELECT section_id::int, subject_type::text, subject_id::int, subject_role::text,
           can_read::boolean, can_create::boolean, can_edit::boolean,
           can_delete::boolean, can_publish::boolean, can_approve::boolean,
           grant_subsections::boolean, min_role_level::int
      FROM (VALUES {rules}) AS t(
        section_id, subject_type, subject_id, subject_role,
        can_read, can_create, can_edit, can_delete, can_publish, can_approve,
        grant_subsections, min_role_level)
),
wiki_sections AS (
    SELECT id::int, parent_section_id::int, status::text, space_id::int
      FROM (VALUES {sections}) AS t(id, parent_section_id, status, space_id)
),
-- Пустая заглушка = пространство видно всем: границу проверяет отдельный тест.
wiki_space_departments AS (
    SELECT space_id::int, department_id::int
      FROM (VALUES {space_departments}) AS t(space_id, department_id)
     WHERE space_id IS NOT NULL
),
"""

_TAIL = """
SELECT section_id, can_read, can_create, can_edit,
       can_delete, can_publish, can_approve
  FROM section_rights
 WHERE section_id = ANY(%(sections)s)
"""


def rule(section, *, deep, subject="'user', 158, NULL", level='NULL', **flags):
    values = ['true' if flags.get(name) else 'false'
              for name in ('can_read', 'can_create', 'can_edit',
                           'can_delete', 'can_publish', 'can_approve')]
    return "(%d, %s, %s, %s, %s)" % (section, subject, ', '.join(values),
                                     'true' if deep else 'false', level)


class SectionRightsSqlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()

    def rights(self, rules, sections, *, role='operator', user_id=USER, tree=None,
               space_departments=()):
        sql = _SECTION_RIGHTS_CTE.replace(
            'WITH RECURSIVE section_rights_all AS (',
            'WITH RECURSIVE ' + _STUBS.format(
                rules=', '.join(rules),
                sections=', '.join(tree or _TREE),
                space_departments=(', '.join(space_departments) if space_departments
                                   else _NO_SPACE_DEPARTMENTS)).strip()
            + ' section_rights_all AS (',
            1) + _TAIL

        subjects = collect_subjects(user_id=user_id, otp_role=role, department_id=DEPT)
        params = dict(subject_params(subjects, user_id), sections=list(sections))
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            out = {}
            for row in cur.fetchall():
                out.setdefault(row[0], set()).update(
                    name for name, value in zip(
                        ('can_read', 'can_create', 'can_edit',
                         'can_delete', 'can_publish', 'can_approve'), row[1:]) if value)
            return out
        finally:
            prod_db.rollback()
            cur.close()

    # ── Инцидент: правило БЕЗ подразделов ────────────────────────────────
    def test_shallow_rule_stays_in_its_section(self):
        """Ровно правило id=42 с прода: раздел «Супервайзер», вглубь не идёт."""
        got = self.rights([rule(SUPERVISOR, deep=False, can_read=True, can_edit=True)],
                          [SUPERVISOR, OPERATOR])
        self.assertEqual(got.get(SUPERVISOR), {'can_read', 'can_edit'})
        self.assertNotIn(OPERATOR, got, 'правило без тумблера ушло вглубь')

    # ── Тумблер «вместе с подразделами» ──────────────────────────────────
    def test_deep_rule_carries_write_rights_down(self):
        """Главное, ради чего заведён тест: вглубь уходит не только чтение."""
        got = self.rights([rule(HEAD, deep=True, can_read=True, can_edit=True,
                                can_publish=True)],
                          [HEAD, SUPERVISOR, OPERATOR])
        for section in (HEAD, SUPERVISOR, OPERATOR):
            self.assertEqual(got.get(section), {'can_read', 'can_edit', 'can_publish'},
                             'раздел %d' % section)

    def test_deep_rule_does_not_climb_up(self):
        """Вглубь — да, вверх — нет: правило на «Супервайзере» не открывает «Директора»."""
        got = self.rights([rule(SUPERVISOR, deep=True, can_read=True, can_edit=True)],
                          [DIRECTOR, HEAD, SUPERVISOR, OPERATOR])
        self.assertNotIn(DIRECTOR, got)
        self.assertNotIn(HEAD, got)
        self.assertEqual(got.get(OPERATOR), {'can_read', 'can_edit'})

    def test_archived_subsection_is_skipped(self):
        """Архивный раздел рекурсию обрывает — как и в периметре чтения."""
        archived = list(_TREE)
        archived[2] = "(3, 2, 'archived', 1)"
        got = self.rights([rule(HEAD, deep=True, can_read=True, can_edit=True)],
                          [SUPERVISOR, OPERATOR], tree=archived)
        self.assertNotIn(SUPERVISOR, got)
        self.assertNotIn(OPERATOR, got, 'ветка за архивным разделом обязана оборваться')

    # ── Границы, которые тумблер не отменяет ─────────────────────────────
    def test_level_threshold_still_applies(self):
        """Порог должности проверяется у самого правила, до всякой рекурсии."""
        got = self.rights([rule(HEAD, deep=True, subject="'department', 1, NULL",
                                level='30', can_read=True, can_edit=True)],
                          [HEAD, SUPERVISOR, OPERATOR], role='operator')
        self.assertEqual(got, {}, 'оператор прошёл под порог супервайзера')

    def test_rule_of_another_person_is_invisible(self):
        got = self.rights([rule(HEAD, deep=True, subject="'user', 999, NULL",
                                can_read=True, can_edit=True)],
                          [HEAD, SUPERVISOR, OPERATOR])
        self.assertEqual(got, {})

    def test_space_border_beats_the_rule(self):
        """Пространство закрыто от отдела — правило внутри него не действует.

        Иначе забытое правило вечно поднимало бы способность: вкладки редактора
        открылись бы, а править было бы нечего (queries.load_capabilities).
        """
        got = self.rights([rule(SUPERVISOR, deep=False, can_read=True, can_edit=True)],
                          [SUPERVISOR],
                          space_departments=["(1, 367)"])
        self.assertEqual(got, {}, 'правило пробило границу пространства')

    def test_space_border_lets_own_department_through(self):
        got = self.rights([rule(SUPERVISOR, deep=False, can_read=True, can_edit=True)],
                          [SUPERVISOR],
                          space_departments=["(1, %d)" % DEPT])
        self.assertEqual(got.get(SUPERVISOR), {'can_read', 'can_edit'})


if __name__ == '__main__':
    unittest.main()
