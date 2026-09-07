# -*- coding: utf-8 -*-
"""Должности ветки: из чего собирается матрица «Кому открыт раздел».

До 04.09.2026 матрица была КОНСТАНТОЙ из четырёх строк — оператор, тренер,
супервайзер, руководитель группы — и рисовалась одинаково во всех ветках.
Для линии это правда: в СЗоВ и ОП людей различает роль OTP. Для отделов без
линии — нет: в «Маркетинге» роль у всех одна (marketing_manager), различает
людей users.job_title, и форма предлагала выдать доступ «супервайзеру
маркетинга», которого не существует, не предлагая при этом ни одной настоящей
должности отдела — видеографа, таргетолога, контекстолога, SMM-менеджера.

Здесь проверяется три вещи:
  * САМ SQL обхода дерева — на боевом соединении, по синтетическим строкам
    через CTE-заглушки (приём из test_wiki_section_perimeter);
  * сборка строк из дерева и кадров — на поддельном курсоре, без базы;
  * то, что фронт больше не держит список должностей константой.
"""

import io
import os
import unittest

from tests import prod_db
from wiki import structure
from wiki.access import ROLE_LEVELS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _source(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding='utf-8') as handle:
        return handle.read()


# ─────────────────────────────────────────────────────────────────────────────
# Сравнение названий
# ─────────────────────────────────────────────────────────────────────────────

class NormalizeTitleTest(unittest.TestCase):
    """«HR-менеджер» в дереве и «HR менеджер» в кадрах — одна должность.

    Без этого строка матрицы указывала бы на должность, которой ни у кого нет:
    правило сохранилось бы и не открыло НИЧЕГО. Ровно тот молчаливый отказ, от
    которого раздел лечили уже дважды.
    """

    def test_dash_and_space_are_the_same(self):
        self.assertEqual(structure.normalize_position_title('HR-менеджер'),
                         structure.normalize_position_title('HR менеджер'))

    def test_case_does_not_matter(self):
        self.assertEqual(structure.normalize_position_title('SMM-менеджер'),
                         structure.normalize_position_title('smm менеджер'))

    def test_yo_is_folded(self):
        self.assertEqual(structure.normalize_position_title('Стажёр'),
                         structure.normalize_position_title('Стажер'))

    def test_double_spaces_collapse(self):
        self.assertEqual(structure.normalize_position_title('  Ведущий   таргетолог '),
                         'ведущий таргетолог')

    def test_different_titles_stay_different(self):
        self.assertNotEqual(structure.normalize_position_title('Видеограф'),
                            structure.normalize_position_title('Таргетолог'))


# ─────────────────────────────────────────────────────────────────────────────
# Обход дерева — боевой SQL по синтетическим строкам
# ─────────────────────────────────────────────────────────────────────────────

# Ветка «Коммерческого отдела», как её строят руками во вкладке «Структура»:
#   1 Коммерческий директор
#   └ 2 СЗоВ (ветка отдела 1)
#     └ 3 Руководитель группы → 4 Супервайзер → 5 Оператор
#   └ 6 ОП (ветка отдела 367)
#     └ 7 Руководитель группы
#   └ 8 Маркетинг (ветка отдела 1041)
#     └ 9 Руководитель → 10 Видеограф, 11 Таргетолог
#     └ 12 Архивная должность
_TREE = [
    "(1,  NULL, 'Коммерческий директор', 'common',     NULL, 'active',   0)",
    "(2,  1,    'СЗоВ',                  'department', 1,    'active',   1)",
    "(3,  2,    'Руководитель группы',   'common',     NULL, 'active',   2)",
    "(4,  3,    'Супервайзер',           'common',     NULL, 'active',   3)",
    "(5,  4,    'Оператор',              'common',     NULL, 'active',   4)",
    "(6,  1,    'ОП',                    'department', 367,  'active',   5)",
    "(7,  6,    'Руководитель группы',   'common',     NULL, 'active',   6)",
    "(8,  1,    'Маркетинг',             'department', 1041, 'active',   7)",
    "(9,  8,    'Руководитель',          'common',     NULL, 'active',   8)",
    "(10, 9,    'Видеограф',             'common',     NULL, 'active',   9)",
    "(11, 9,    'Таргетолог',            'common',     NULL, 'active',  10)",
    "(12, 9,    'Бывшая должность',      'common',     NULL, 'archived', 11)",
]

# Заглушка вклеивается ПЕРВЫМ элементом WITH RECURSIVE, а боевой текст запроса
# приезжает следом как есть — тот же приём, что в test_wiki_section_perimeter.
# RECURSIVE стоит на всём WITH: без него рекурсивные ветки боевых запросов не
# видят сами себя и падают «relation up does not exist».
_SECTIONS_STUB = """WITH RECURSIVE wiki_sections AS (
    SELECT id::int, parent_section_id::int, name::text, section_kind::text,
           department_id::int, status::text, position::int
      FROM (VALUES {tree}) AS t(
        id, parent_section_id, name, section_kind, department_id, status, position)
),"""


class _SqlCapture(object):
    """Курсор, который ничего не делает, но запоминает текст запроса."""

    sql = None

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return None

    def fetchall(self):
        return []


def _sql_of(function, *args):
    """Достаёт БОЕВОЙ текст запроса из самой функции.

    Копия запроса в тесте разошлась бы с оригиналом при первой же правке — и
    тест продолжил бы гореть зелёным, проверяя вчерашний SQL.
    """
    capture = _SqlCapture()
    function(capture, *args)
    return capture.sql


_ANCHOR_SQL = _sql_of(structure._branch_anchor, 0)
_POSITIONS_SQL = _sql_of(structure._branch_position_sections, 0)


class BranchTreeSqlTest(unittest.TestCase):
    """Боевые запросы обхода — на настоящем постгресе, по заглушкам."""

    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()

    def run_sql(self, source_sql, params):
        """Боевой SQL целиком, только с подменённой таблицей разделов."""
        stub = _SECTIONS_STUB.format(tree=', '.join(_TREE))
        patched = source_sql.replace('WITH RECURSIVE', stub, 1)
        cur = self.conn.cursor()
        try:
            cur.execute(patched, params)
            return cur.fetchall()
        finally:
            prod_db.rollback()
            cur.close()

    def anchor(self, section_id):
        rows = self.run_sql(_ANCHOR_SQL, (section_id,))
        return rows[0] if rows else None

    def positions(self, anchor_id):
        return [(row[0], row[1]) for row in self.run_sql(_POSITIONS_SQL, (anchor_id,))]

    # ── Якорь ветки ──────────────────────────────────────────────────────
    def test_anchor_is_the_nearest_department_above(self):
        self.assertEqual(self.anchor(5), (2, 1), 'у «Оператора» ветка — СЗоВ')

    def test_anchor_of_the_branch_itself(self):
        self.assertEqual(self.anchor(8), (8, 1041))

    def test_section_outside_any_branch_has_no_anchor(self):
        self.assertIsNone(self.anchor(1), 'над «Коммерческим директором» отдела нет')

    # ── Должности ветки ──────────────────────────────────────────────────
    def test_line_branch_lists_the_ladder_bottom_first(self):
        self.assertEqual([name for _, name in self.positions(2)],
                         ['Оператор', 'Супервайзер', 'Руководитель группы'])

    def test_marketing_lists_specialists_before_the_head(self):
        self.assertEqual([name for _, name in self.positions(8)],
                         ['Видеограф', 'Таргетолог', 'Руководитель'])

    def test_archived_position_is_not_offered(self):
        self.assertNotIn('Бывшая должность', [name for _, name in self.positions(8)])

    def test_neighbour_branch_is_not_walked_into(self):
        """Из «Коммерческого директора» вниз не собираются должности ОП и СЗоВ.

        Обход останавливается на вложенной ветке отдела — иначе одна матрица
        показала бы вперемешку должности всех отделов сразу.
        """
        self.assertEqual(self.positions(1), [],
                         'ниже идут только ветки отделов, своих должностей нет')


# ─────────────────────────────────────────────────────────────────────────────
# Сборка строк — без базы, на поддельном курсоре
# ─────────────────────────────────────────────────────────────────────────────

class FakeCursor(object):
    """Отдаёт заготовленные ответы по порядку запросов branch_positions.

    Порядок закреплён намеренно: якорь ветки → должности кадров → численность
    ролей → должности дерева. Перестановка запросов местами уронит этот тест, и
    это правильно: от неё зависит, какой ответ куда попадёт.
    """

    def __init__(self, anchor, sections, titles, roles):
        self._answers = [
            [anchor] if anchor else [],
            titles,
            roles,
            sections,
        ]
        self._current = []

    def execute(self, sql, params=None):
        self._current = self._answers.pop(0) if self._answers else []

    def fetchone(self):
        return self._current[0] if self._current else None

    def fetchall(self):
        return list(self._current)


def marketing_cursor(anchor=(8, 1041)):
    return FakeCursor(
        anchor=anchor,
        sections=[(10, 'Видеограф', 2, 9), (11, 'Таргетолог', 2, 10),
                  (9, 'Руководитель', 1, 8)],
        titles=[('Видеограф', 'marketing_manager', 1),
                ('Таргетолог', 'marketing_manager', 1),
                ('SMM-менеджер', 'marketing_manager', 2)],
        roles=[('marketing_manager', 4), ('admin', 1)],
    )


def line_cursor():
    return FakeCursor(
        anchor=(2, 1),
        sections=[(5, 'Оператор', 3, 4), (4, 'Супервайзер', 2, 3),
                  (3, 'Руководитель группы', 1, 2)],
        titles=[],
        roles=[('operator', 65), ('sv', 4), ('admin', 5), ('trainer', 1)],
    )


class BranchPositionsTest(unittest.TestCase):

    def rows(self, cursor, rules=()):
        return structure.branch_positions(cursor, 999, existing_rules=rules)

    # ── Отдел без линии ──────────────────────────────────────────────────
    def test_marketing_rows_are_its_own_positions(self):
        rows = self.rows(marketing_cursor())
        self.assertEqual([r['label'] for r in rows],
                         ['Видеограф', 'Таргетолог', 'Руководитель'])

    def test_specialists_are_addressed_by_job_title(self):
        rows = {r['label']: r for r in self.rows(marketing_cursor())}
        self.assertEqual(rows['Видеограф']['job_title'], 'Видеограф')
        self.assertEqual(rows['Видеограф']['min_role_level'], ROLE_LEVELS['operator'])

    def test_head_is_addressed_by_level_not_by_title(self):
        """«Руководитель» — слово лестницы, а не должность из кадров."""
        rows = {r['label']: r for r in self.rows(marketing_cursor())}
        self.assertIsNone(rows['Руководитель']['job_title'])
        self.assertEqual(rows['Руководитель']['min_role_level'], ROLE_LEVELS['admin'])

    def test_every_row_is_addressed_to_the_branch_department(self):
        """Голая роль пробила бы границу отдела — СВ продаж увидел бы СЗоВ."""
        for row in self.rows(marketing_cursor()):
            self.assertEqual(row['subject_type'], 'department')
            self.assertEqual(row['subject_id'], 1041)

    def test_rows_of_one_branch_have_distinct_keys(self):
        """Ключ — АДРЕСАТ строки. Совпав, четыре должности слились бы в одну."""
        keys = [r['key'] for r in self.rows(marketing_cursor())]
        self.assertEqual(len(keys), len(set(keys)))

    def test_headcount_comes_from_the_department(self):
        rows = {r['label']: r for r in self.rows(marketing_cursor())}
        self.assertEqual(rows['Видеограф']['people'], 1)
        self.assertEqual(rows['Руководитель']['people'], 1)

    # ── Отдел с линией: ничего не поменялось ─────────────────────────────
    def test_line_branch_keeps_the_ladder(self):
        rows = self.rows(line_cursor())
        self.assertEqual([r['label'] for r in rows],
                         ['Оператор', 'Супервайзер', 'Руководитель группы'])
        self.assertEqual([r['job_title'] for r in rows], [None, None, None])

    def test_bottom_of_the_ladder_keeps_an_empty_threshold(self):
        """Порог 10 отрезал бы людей с ролью вне шкалы от раздела «всего отдела»."""
        rows = {r['label']: r for r in self.rows(line_cursor())}
        self.assertIsNone(rows['Оператор']['min_role_level'])
        self.assertEqual(rows['Супервайзер']['min_role_level'], ROLE_LEVELS['sv'])

    # ── Раздел вне ветки отдела ──────────────────────────────────────────
    def test_section_outside_a_branch_gets_no_rows(self):
        """Пустой список — сигнал форме остаться на прежнем поведении."""
        self.assertEqual(self.rows(marketing_cursor(anchor=None)), [])

    # ── Должность, которой нет в кадрах ──────────────────────────────────
    def test_unknown_position_is_shown_with_zero_people(self):
        """Опечатка в названии не должна выглядеть рабочей выдачей."""
        cursor = FakeCursor(
            anchor=(8, 1041),
            sections=[(10, 'Видеографф', 2, 9)],
            titles=[('Видеограф', 'marketing_manager', 1)],
            roles=[('marketing_manager', 1)],
        )
        row = self.rows(cursor)[0]
        self.assertEqual(row['people'], 0)
        self.assertEqual(row['job_title'], 'Видеографф')

    def test_title_matches_across_a_dash(self):
        """В дереве «HR-менеджер», в кадрах «HR менеджер» — одна должность."""
        cursor = FakeCursor(
            anchor=(35, 1499),
            sections=[(7, 'HR-менеджер', 1, 0)],
            titles=[('HR менеджер', 'hr_manager', 1)],
            roles=[('hr_manager', 2), ('admin', 1)],
        )
        row = self.rows(cursor)[0]
        # В правило уходит значение ИЗ КАДРОВ: сравнение в SQL идёт на равенство.
        self.assertEqual(row['job_title'], 'HR менеджер')
        self.assertEqual(row['people'], 1)

    # ── Правило, которому в дереве места не нашлось ──────────────────────
    def test_existing_rule_without_a_position_stays_visible(self):
        """Сузив список, мы иначе молча спрятали бы боевую выдачу.

        Так на проде выписано правило «Тренер» на разделе «Оператор» СЗоВ:
        должности «Тренер» в дереве отдела нет, а правило есть и работает.
        """
        rules = [{'subject_type': 'department', 'subject_id': 1,
                  'min_role_level': ROLE_LEVELS['trainer'], 'job_title': None}]
        rows = self.rows(line_cursor(), rules=rules)
        self.assertEqual(rows[-1]['label'], 'Тренер')
        self.assertEqual(rows[-1]['source'], 'rule')

    def test_rule_matching_a_position_does_not_double_the_row(self):
        rules = [{'subject_type': 'department', 'subject_id': 1,
                  'min_role_level': ROLE_LEVELS['sv'], 'job_title': None}]
        rows = self.rows(line_cursor(), rules=rules)
        self.assertEqual([r['label'] for r in rows].count('Супервайзер'), 1)

    def test_rule_of_another_department_is_ignored(self):
        """Чужое правило не строка этой ветки — ему место в точечных."""
        rules = [{'subject_type': 'department', 'subject_id': 367,
                  'min_role_level': None, 'job_title': None}]
        self.assertEqual(len(self.rows(line_cursor(), rules=rules)), 3)

    def test_personal_rule_is_ignored(self):
        rules = [{'subject_type': 'user', 'subject_id': 1,
                  'min_role_level': None, 'job_title': None}]
        self.assertEqual(len(self.rows(line_cursor(), rules=rules)), 3)


# ─────────────────────────────────────────────────────────────────────────────
# Сервер и форма
# ─────────────────────────────────────────────────────────────────────────────

class RouteContractTest(unittest.TestCase):
    """Строки матрицы обязаны ехать с ответом, а должность — приниматься."""

    def setUp(self):
        self.source = _source('wiki', 'routes_structure.py')

    def test_get_returns_the_positions(self):
        self.assertIn('"positions": positions,', self.source)
        self.assertIn('structure.branch_positions(', self.source)

    def test_locked_is_decided_by_the_same_ceiling_check(self):
        """Иначе форма покажет строку, на которую сервер ответит 403."""
        self.assertIn("row['locked'] = not wiki_access.may_grant_with_ceiling(",
                      self.source)

    def test_post_accepts_a_job_title(self):
        self.assertIn("job_title = str(data.get('job_title') or '').strip()",
                      self.source)
        self.assertIn('job_title=job_title or None,', self.source)

    def test_job_title_is_written_to_the_audit_log(self):
        """Без должности журнал не отвечает на вопрос «кому выдали»."""
        self.assertIn("'job_title': job_title or None,", self.source)

    def test_job_title_narrows_only_a_department_rule(self):
        self.assertIn("if job_title and subject_type != 'department':", self.source)

    def test_job_title_rule_always_carries_a_threshold(self):
        """«И все, кто выше» держится сравнением уровня, а с NULL оно не истинно."""
        self.assertIn("if job_title and min_role_level is None:", self.source)


class SubjectMatchTest(unittest.TestCase):
    """Совпадение правила с человеком — ОДНО определение на весь раздел."""

    def setUp(self):
        self.source = _source('wiki', 'queries.py')

    def test_job_title_is_part_of_the_single_condition(self):
        self.assertIn('OR r.job_title = %(job_title)s', self.source)

    def test_higher_positions_still_see_it(self):
        self.assertIn('OR %(role_level)s > r.min_role_level', self.source)

    def test_job_title_travels_with_the_other_subjects(self):
        self.assertIn("'job_title': subjects.get('job_title') or ''", self.source)

    def test_context_carries_the_job_title(self):
        self.assertIn("'job_title': job_title,", self.source)


class AccessFormSourceTest(unittest.TestCase):
    """Форма больше не держит список должностей константой.

    Проверяется исходником, потому что решение об этом принимается во фронте, а
    сборка и node-тесты его не сторожат: константа вернулась бы молча.
    """

    def setUp(self):
        self.source = _source('src', 'components', 'wiki', 'WikiSectionAccess.jsx')

    def test_rows_come_from_the_server(self):
        self.assertIn('setPositions(r.data?.positions || [])', self.source)

    def test_constant_is_only_a_fallback(self):
        self.assertIn('FALLBACK_ROWS', self.source)
        self.assertNotIn('ROLE_ROWS', self.source,
                         'прежняя константа матрицы вернулась во фронт')

    def test_matrix_walks_the_server_rows(self):
        self.assertIn('const rows = useMemo(', self.source)
        self.assertIn('rows.forEach((row) => {', self.source)

    def test_rule_is_matched_by_the_whole_subject(self):
        """По одному порогу четыре должности «Маркетинга» слились бы в одну."""
        self.assertIn('const ruleMatchesRow = (rule, row) => (', self.source)
        self.assertIn("(rule.job_title || null) === (row.job_title || null))",
                      self.source)

    def test_payload_carries_the_job_title(self):
        self.assertIn('job_title: row.job_title || null', self.source)

    def test_locked_prefers_the_server_answer(self):
        self.assertIn('const isLocked = (row) => (row.locked != null', self.source)

    def test_empty_branch_explains_itself(self):
        """Пустая карточка без объяснения читается как «сломалось»."""
        self.assertIn('В этой ветке ещё нет разделов-должностей.', self.source)

    def test_zero_headcount_is_flagged(self):
        self.assertIn('нет таких сотрудников', self.source)


class AuditLabelTest(unittest.TestCase):
    """Журнал обязан называть должность, а не «от уровня 10»."""

    def test_audit_prints_the_job_title(self):
        source = _source('src', 'components', 'wiki', 'auditEvents.js')
        self.assertIn('if (details.job_title) {', source)
        self.assertIn('должность «${details.job_title}»', source)


if __name__ == '__main__':
    unittest.main()
