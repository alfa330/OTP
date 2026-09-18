# -*- coding: utf-8 -*-
"""«Графики работы» (планировщик смен) на телефоне: период переключателем, дни
полосой, люди списком по направлениям, день человека — экраном с четырьмя
вкладками настольного окна правки.

Проверки читают исходники текстом: решения здесь про то, ЧТО рисуется на
телефоне и какими путями. Правила подписей и сводок закреплены отдельно —
tests/work_schedules_phone.test.mjs.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
SCHEDULE = ROOT / 'src' / 'components' / 'schedule'
PHONE = (SCHEDULE / 'WorkSchedulesMobile.jsx').read_text(encoding='utf-8')
RULES = (SCHEDULE / 'workSchedulesPhone.js').read_text(encoding='utf-8')
CSS = (SCHEDULE / 'work-schedules-mobile.css').read_text(encoding='utf-8')

PHONE_BRANCH_START = "            if (isNarrowShell) {\n                const wsToday"
DESKTOP_GRID_START = '                <div className="px-4 py-2 min-h-screen bg-slate-50">'


def strip_css_comments(css):
    return re.sub(r'/\*.*?\*/', '', css, flags=re.S)


def strip_js_comments(code):
    return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', code, flags=re.S))


def block(source, start_marker, end_marker):
    start = source.index(start_marker)
    return source[start:source.index(end_marker, start)]


PHONE_BRANCH = block(APP, PHONE_BRANCH_START, DESKTOP_GRID_START)


class PhoneLayerTests(unittest.TestCase):
    def test_every_rule_is_locked_to_the_phone_shell(self):
        selectors = []
        for rule in re.findall(r'([^{}]+)\{', strip_css_comments(CSS)):
            selectors.extend(part.strip() for part in rule.split(','))
        self.assertTrue(selectors)
        for selector in selectors:
            self.assertTrue(selector.startswith('body.mobile-shell'), selector)

    def test_colours_are_utilities_not_hex(self):
        """Тёмный слой портала перекрашивает утилиты Tailwind, а hex из файла
        раздела он не видит."""
        self.assertIsNone(re.search(r'#[0-9a-fA-F]{3,8}\b', strip_css_comments(CSS)))

    def test_shared_auction_layer_is_loaded_from_the_phone_module(self):
        """Планировщик живёт в главном чанке, аукцион — в ленивом: без своего
        импорта полоса дней осталась бы без стекла и прилипания."""
        self.assertIn("import '../resources/shift-auction-mobile.css';", PHONE)
        self.assertIn("import './work-schedules-mobile.css';", PHONE)
        self.assertIn('className="sa-m-root ws-m-root min-h-screen bg-slate-100"', APP)

    def test_native_select_outweighs_the_shared_motion_layer(self):
        """`.main-content select` из mobile-motion.css весит (0,2,2): правило в
        два класса проиграло бы, и прозрачное поле строки получило бы рамку и
        высоту 40 px."""
        self.assertIn(
            'body.mobile-shell .main-content .sa-m-root select.ws-m-select {',
            CSS,
        )

    def test_markup_avoids_the_traps_of_the_shared_section_layer(self):
        """Общий слой разделов (mobile-shell.css) переносит ряды с gap, ставит
        колонкой .flex.items-start, режет ширину у «w-[», обнуляет «min-w-[» и
        прячет всё, чей класс содержит «-actions», «-tabs», «-filters»,
        «toolbar» или «topbar»."""
        code = strip_js_comments(PHONE)
        for trap in ('min-w-[', 'flex-nowrap', ' -mx-', 'items-start'):
            self.assertNotIn(trap, code, trap)
        self.assertIsNone(re.search(r'className="[^"]*(?:-actions|-tabs|-filters|toolbar|topbar)"', code))

    def test_phone_rules_have_no_react(self):
        """Правила раздела — чистые функции: их гоняет node-тест, а не браузер."""
        code = strip_js_comments(RULES).lower()
        self.assertNotIn('react', code)
        self.assertNotIn('document', code)
        self.assertNotIn('window', code)


class PhoneBranchTests(unittest.TestCase):
    def test_phone_branch_returns_before_the_desktop_grid(self):
        """Настольная сетка на телефоне не монтируется вовсе — у неё свой
        return, и ни одна её строка в телефонную ветку не попадает."""
        self.assertIn('return (', PHONE_BRANCH)
        self.assertNotIn('table-scroll', PHONE_BRANCH)
        self.assertNotIn('SmallCalendar', PHONE_BRANCH)

    def test_phone_is_decided_by_the_single_shell_query(self):
        self.assertIn('const isNarrowShell = useIsMobileShell();', APP)
        self.assertNotIn('matchMedia', PHONE)

    def test_period_switch_and_day_strip(self):
        """Период — тот же viewMode, что у сетки; полоса дней есть только в
        «Дне»: в «Неделе» и «Месяце» строка человека показывает весь период."""
        self.assertIn('options={PLANNER_VIEW_MODE_OPTIONS}', PHONE_BRANCH)
        self.assertIn("const wsIsDayMode = viewMode === 'day';", PHONE_BRANCH)
        self.assertIn('{wsIsDayMode ? (\n                                <WsPhoneDayStrip', PHONE_BRANCH)

    def test_day_screen_reuses_the_desktop_handlers(self):
        """Сохранение, удаление и выходной идут теми же функциями, что у
        настольного окна: вторая точка правды разъехалась бы с первой."""
        for marker in ('openEditModal(opId, date)', 'saveSegment({', 'removeSegment({', 'toggleDayOff(modalState.opId, modalState.date)'):
            self.assertIn(marker, PHONE_BRANCH, marker)

    def test_day_screen_keeps_all_four_desktop_tabs(self):
        for tab in ("value: 'shifts'", "value: 'status'", "value: 'control'", "value: 'history'"):
            self.assertIn(tab, PHONE_BRANCH, tab)

    def test_read_only_trainer_cannot_edit_from_the_phone(self):
        """Тренеру раздел открыт только на просмотр: нажатие по человеку ведёт
        на экран дней, а не в правку, и подвал экрана дня пуст."""
        self.assertIn('if (plannerReadOnly) return;\n                    setModalActiveTab', PHONE_BRANCH)
        self.assertIn('if (plannerReadOnly) return null;', PHONE_BRANCH)
        self.assertIn('plannerReadOnly\n                                    ? setPlannerPhoneOperatorId(op.id)', PHONE_BRANCH)

    def test_side_modals_are_shared_between_both_trees(self):
        """Окна тренинга, тех. причины, офлайн-активности и штрафа, очередь
        «Запросы», подписка на сводку и «Перерывы за день» вынесены в
        переменные и рисуются обоими деревьями — копии разъехались бы."""
        self.assertIn('const plannerSideModals = (', APP)
        self.assertIn('const plannerRequestModals = (', APP)
        self.assertEqual(APP.count('{plannerSideModals}'), 2)
        self.assertEqual(APP.count('{plannerRequestModals}'), 2)
        self.assertIn('{plannerSideModals}', PHONE_BRANCH)
        self.assertIn('{plannerRequestModals}', PHONE_BRANCH)

    def test_phone_state_is_not_read_by_the_desktop_tree(self):
        desktop = APP[APP.index(DESKTOP_GRID_START):]
        for name in ('plannerPhoneOperatorId', 'plannerPhoneFilterScreen', 'plannerPhoneFilterQuery'):
            self.assertNotIn(name, desktop, name)


class PhoneScopeTests(unittest.TestCase):
    """Что на телефон сознательно НЕ вынесено — работа с файлами и
    многоколоночные панели: импорт графика и статусов, синхронизации телефонии,
    отчёты статусов, почасовая группировка, журнал замен, настройка перерывов и
    массовая правка нескольких ячеек. За ними садятся за компьютер."""

    def test_file_and_report_panels_stay_on_the_desktop(self):
        for marker in (
            'triggerPlannerExcelImportSelect',
            'openPlannerOktellSyncModal',
            'syncPlannerChat2DeskStatuses',
            'setShowPlannerStatusAnomalyModal',
            'setShowPlannerStatusGroupingModal',
            'setShowSwapJournalModal',
            'openPlannerBreakRulesSettings',
            'saveSegmentToMultipleTargets',
        ):
            self.assertNotIn(marker, PHONE_BRANCH, marker)

    def test_excel_export_is_the_one_file_action_on_the_phone(self):
        self.assertIn('handlePlannerExcelExport()', PHONE_BRANCH)


if __name__ == '__main__':
    unittest.main()
