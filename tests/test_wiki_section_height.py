# -*- coding: utf-8 -*-
"""Высота раздела на лестнице должностей — проверка САМОГО SQL.

Ради чего заведён этот запрос: 25.08.2026 владелец обнаружил, что супервайзер
раздаёт чтение В РАЗДЕЛЕ РУКОВОДИТЕЛЯ, и потребовал «свой раздел и ниже, но
никак не выше» — и для супервайзеров, и для руководителей группы. Двух прежних
границ на это не хватало: раздел руководителя лежит в ветке ТОГО ЖЕ отдела
(дерево вики повторяет оргструктуру), а правило без порога весит как оператор и
проходит потолок GRANT_CEILING насквозь.

Высоту раздела меряет section_role_levels: минимальный порог правил НА ЧТЕНИЕ,
а если своих порогов нет — порог ближайшего предка. Здесь проверяется именно
это, потому что от одного числа зависит, кто настраивает раздел.

Приём тот же, что в test_wiki_section_perimeter: CTE в PostgreSQL перекрывает
одноимённую таблицу, поэтому боевой текст исполняется над синтетическими
строками. Соединение read-only — боевые таблицы не читаются.
"""

import unittest

from tests import prod_db
from wiki.structure import _SECTION_ROLE_LEVELS_SQL

# Дерево прода: 1 «Коммерческий директор» → 19 «СЗоВ» (ветка отдела)
# → 2 «Руководитель группы» → 3 «Супервайзер» → 4 «Оператор».
DIRECTOR, BRANCH, HEAD, SUPERVISOR, OPERATOR, SUB = 1, 19, 2, 3, 4, 7

_TREE = [
    "(1,  NULL, 'active')",
    "(19, 1,    'active')",
    "(2,  19,   'active')",
    "(3,  2,    'active')",
    "(4,  3,    'active')",
]

# Без ведущего WITH: заглушки вклеиваются внутрь боевого "WITH RECURSIVE ...".
_STUBS = """
wiki_section_access_rules AS (
    SELECT section_id::int, can_read::boolean, min_role_level::int
      FROM (VALUES {rules}) AS t(section_id, can_read, min_role_level)
),
wiki_sections AS (
    SELECT id::int, parent_section_id::int, status::text
      FROM (VALUES {sections}) AS t(id, parent_section_id, status)
),
"""


def rule(section, level, *, can_read=True):
    return "(%d, %s, %s)" % (section, 'true' if can_read else 'false',
                             'NULL' if level is None else level)


class SectionRoleLevelSqlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()

    def levels(self, rules, tree=None):
        sql = _SECTION_ROLE_LEVELS_SQL.replace(
            'WITH RECURSIVE own AS (',
            'WITH RECURSIVE ' + _STUBS.format(
                rules=', '.join(rules),
                sections=', '.join(tree or _TREE)).strip() + ' own AS (',
            1)
        cur = self.conn.cursor()
        try:
            cur.execute(sql)
            return {row[0]: row[1] for row in cur.fetchall()}
        finally:
            prod_db.rollback()
            cur.close()

    # ── Ровно то дерево, на котором нашли дыру ───────────────────────────
    def test_prod_ladder_puts_everyone_on_his_step(self):
        """Порядок с прода: 40 у руководителя, 30 у СВ, 20 у оператора.

        По этим числам и решается спор: супервайзер (30) до раздела 40 не
        дотягивается, а руководитель (40) настраивает и свой, и оба нижних.
        """
        got = self.levels([
            rule(HEAD, 40),
            rule(SUPERVISOR, 30), rule(SUPERVISOR, 40),
            rule(OPERATOR, None), rule(OPERATOR, 20),
            rule(OPERATOR, 30), rule(OPERATOR, 40),
        ])
        self.assertEqual(got.get(HEAD), 40)
        self.assertEqual(got.get(SUPERVISOR), 30)
        self.assertEqual(got.get(OPERATOR), 20)

    def test_lowest_threshold_wins(self):
        """Высота — МИНИМУМ порогов: раздел читают с самой нижней ступени."""
        self.assertEqual(self.levels([rule(HEAD, 40), rule(HEAD, 30)]).get(HEAD), 30)

    def test_personal_rule_does_not_lower_the_section(self):
        """Правило без порога — именное исключение, а не ступень раздела.

        Считай мы его за уровень оператора, одна выдача «покажите регламент
        Иванову» обнуляла бы ранг руководительского раздела — то есть возвращала
        бы ровно ту дыру, ради которой всё и написано.
        """
        got = self.levels([rule(HEAD, 40), rule(HEAD, None)])
        self.assertEqual(got.get(HEAD), 40)

    def test_write_only_rule_is_not_a_step(self):
        """Ступень определяют правила НА ЧТЕНИЕ: высота — про то, кто раздел видит."""
        got = self.levels([rule(HEAD, 40), rule(HEAD, 20, can_read=False)])
        self.assertEqual(got.get(HEAD), 40)

    # ── Наследство вниз по дереву ────────────────────────────────────────
    def test_new_subsection_inherits_the_step_of_its_branch(self):
        """Подраздел без своих правил — той же высоты, что и раздел над ним.

        Дыра в одно движение: директор заводит подраздел внутри «Руководителя
        группы», своих правил у того ещё нет — и супервайзер настраивает его как
        свой, пока правила не выпишут.
        """
        tree = _TREE + ["(7, 2, 'active')"]
        got = self.levels([rule(HEAD, 40)], tree=tree)
        self.assertEqual(got.get(SUB), 40)

    def test_own_threshold_beats_the_inherited_one(self):
        """Собственный порог сильнее наследства — иначе СВ потерял бы свой раздел.

        В дереве вики «Супервайзер» лежит ВНУТРИ «Руководителя группы»: наследуй
        он высоту предка, супервайзер оказался бы отрезан от собственной ветки.
        """
        got = self.levels([rule(HEAD, 40), rule(SUPERVISOR, 30), rule(OPERATOR, 20)])
        self.assertEqual(got.get(SUPERVISOR), 30)
        self.assertEqual(got.get(OPERATOR), 20)

    def test_branch_without_thresholds_has_no_height(self):
        """Ветка отдела и витрина верхнего уровня в ответе не значатся вовсе.

        Их граница — отдел, как и была: иначе супервайзер, которому чтение никто
        не выписывал, лишился бы вкладки «Структура» целиком.
        """
        got = self.levels([rule(OPERATOR, 20)])
        self.assertNotIn(DIRECTOR, got)
        self.assertNotIn(BRANCH, got)
        self.assertNotIn(HEAD, got)

    def test_archived_section_is_out_of_the_map(self):
        """Архив не считаем: настраивать в нём нечего."""
        tree = ["(1, NULL, 'active')", "(2, 1, 'archived')"]
        got = self.levels([rule(HEAD, 40)], tree=tree)
        self.assertNotIn(HEAD, got)


if __name__ == '__main__':
    unittest.main()
