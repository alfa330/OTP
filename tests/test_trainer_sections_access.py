# -*- coding: utf-8 -*-
"""Разделы, открытые тренеру по задаче #360 (25.09.2026), — со стороны фронта.

Тренер просил «Обращения», «Посылки», «Ссылку на подписание», «Чаты водителей»
и «Калькулятор зарплаты (ОП по группам и СЗоВ)». Серверные границы первых
четырёх проверяются в тестах самих разделов; здесь — то, что живёт только во
фронте и ломается молча:

* гард вида «чистого» тренера выкидывает в «Опросы» всё, чего нет в
  TRAINER_ALLOWED_VIEWS, — пункт меню есть, а раздел не открывается;
* калькулятор тренеру не рисовался отдельной проверкой роли, а пункта меню в
  его ветке не было вовсе;
* выбор отдела в калькуляторе у тренера у́же, чем у админа: СЗоВ и ОП, без ТЭЗ.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JSX = ROOT / 'src' / 'App.jsx'

SECTIONS = {
    'crm_tickets': 'canAccessCrmSectionForUser',
    'parcels': 'canAccessParcelsSectionForUser',
    'sign_links': 'canAccessSignLinksSectionForUser',
    'driver_chats': 'canAccessDriverChatsSectionForUser',
}


def _app():
    return APP_JSX.read_text(encoding='utf-8')


def _trainer_views(app):
    block = re.search(r'TRAINER_ALLOWED_VIEWS = Object\.freeze\(\[(.*?)\]\)', app, re.S)
    return re.findall(r"'([a-z_]+)'", block.group(1))


class TrainerViewListTests(unittest.TestCase):
    def test_requested_sections_are_in_the_trainer_list(self):
        views = _trainer_views(_app())
        for view in (*SECTIONS, 'salary'):
            self.assertIn(view, views)

    def test_old_sections_stay_and_nothing_is_duplicated(self):
        views = _trainer_views(_app())
        for view in ('surveys', 'manage_operators', 'tasks', 'lms', 'shift_auction',
                     'work_schedules', 'wiki', 'events'):
            self.assertIn(view, views)
        self.assertEqual(len(views), len(set(views)))
        # «Опросы» — раздел по умолчанию: гарды шлют тренера именно туда.
        self.assertEqual(views[0], 'surveys')


class SectionPredicateTests(unittest.TestCase):
    def test_no_trainer_exclusion_left_in_the_four_predicates(self):
        """Пункт меню и сервер обязаны сходиться: на сервере исключение снято,
        значит и здесь его быть не должно — иначе раздел открывается только
        прямым адресом, а в меню его нет."""
        app = _app()
        for view, name in SECTIONS.items():
            with self.subTest(view=view):
                body = app.split(f'const {name} = (userLike) => {{')[1].split('\n};')[0]
                self.assertNotIn("'trainer'", body)

    def test_sections_other_than_requested_stay_closed_to_the_trainer(self):
        """«Касания», «Воронка ОП» и «Лиды OLX» тренер не просил — правило
        владельца: реализовывать буквально и не расширять."""
        app = _app()
        for name in ('canAccessTouchesSectionForUser', 'canAccessOpFunnelSectionForUser',
                     'canAccessOlxLeadsForUser'):
            with self.subTest(name=name):
                body = app.split(f'const {name} = (userLike) => {{')[1].split('\n};')[0]
                self.assertIn("if (role === 'trainer') return false;", body)


class TrainerSalaryTests(unittest.TestCase):
    def test_salary_screen_is_not_hidden_from_the_trainer(self):
        app = _app()
        self.assertNotIn("user.role !== 'trainer' && view === 'salary'", app)
        self.assertIn("{view === 'salary' && (", app)

    def test_trainer_menu_has_the_salary_item(self):
        """Ветка меню «чистого» тренера — ВТОРОЕ вхождение `{isPlainTrainer && (`
        (первое — «Учет сотрудников» в верхнем блоке)."""
        app = _app()
        start = app.index('const sidebarTree = useMemo(')
        first = app.index('{isPlainTrainer && (', start)
        branch_start = app.index('{isPlainTrainer && (', first + 1)
        branch_end = app.index('{isRankAndFileRole(currentUserRole) && !isScopedDepartmentHead && (',
                               branch_start)
        branch = app[branch_start:branch_end]
        self.assertIn("handleSidebarViewNavigation(e, 'surveys')", branch)
        self.assertEqual(branch.count("handleSidebarViewNavigation(e, 'salary')"), 1)
        self.assertIn('<span className="sidebar-text">Калькулятор зарплаты</span>', branch)
        # Отдельным блоком за разделителем — как «Оплата и мотивация» у админа.
        salary_at = branch.index("handleSidebarViewNavigation(e, 'salary')")
        auction_at = branch.index("handleSidebarViewNavigation(e, 'shift_auction')")
        self.assertLess(auction_at, salary_at)
        self.assertIn('renderSidebarDividerInner()', branch[auction_at:salary_at])

    def test_trainer_picks_between_szov_and_op_only(self):
        app = _app()
        self.assertIn(
            "const TRAINER_SALARY_CALCULATOR_CATALOG = SALARY_CALCULATOR_CATALOG.filter(\n"
            "    (entry) => entry.code === 'szov' || entry.code === 'op'\n"
            ");", app)
        self.assertIn(
            "const salaryPickerCatalog = isAdminLikeRole\n"
            "                ? SALARY_CALCULATOR_CATALOG\n"
            "                : (isPlainTrainer ? TRAINER_SALARY_CALCULATOR_CATALOG : null);", app)
        self.assertIn('const canPickSalaryDepartment = Boolean(salaryPickerCatalog);', app)

    def test_stored_department_is_looked_up_in_the_pickers_own_catalog(self):
        """В localStorage мог остаться «ТЭЗ» от админа на том же компьютере:
        поиск по общему каталогу открыл бы тренеру отдел, которого у него нет."""
        app = _app()
        start = app.index('const canPickSalaryDepartment = Boolean(salaryPickerCatalog);')
        block = app[start:app.index('const opAutoPrefillKeyRef', start)]
        self.assertIn('salaryPickerEntry(salaryDeptCode)?.code', block)
        self.assertIn('salaryPickerEntry(ownSalaryDeptCode)?.code', block)
        self.assertIn('salaryPickerCatalog[0].code', block)
        self.assertNotIn('salaryCatalogEntry(salaryDeptCode)', block)
        pick = block[block.index('const pickSalaryDepartment = (code) => {'):]
        self.assertIn('const entry = salaryPickerEntry(code);', pick)
        self.assertIn('? salaryPickerCatalog\n', block)

    def test_catalog_codes_exist(self):
        """Фильтр по кодам молча дал бы пустой каталог, переименуй кто-то отдел."""
        app = _app()
        catalog = app[app.index('const SALARY_CALCULATOR_CATALOG = ['):
                      app.index('const SALARY_CALCULATOR_TYPES')]
        self.assertIn("code: 'szov',", catalog)
        self.assertIn("code: 'op',", catalog)
        self.assertLess(catalog.index("code: 'szov',"), catalog.index("code: 'op',"))


if __name__ == '__main__':
    unittest.main()
