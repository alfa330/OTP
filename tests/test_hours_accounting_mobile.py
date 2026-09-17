# -*- coding: utf-8 -*-
"""«Учет часов» на телефоне: раздел разложен по уровням вместо широкой таблицы,
настольный вид не тронут — ветки разведены isMobileShell.

Проверки читают исходники текстом: решения здесь про то, ЧТО рисуется на
телефоне и какими путями. Арифметика строки, подвала и ячеек закреплена в
tests/hours_accounting_phone.test.mjs.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
HOURS = ROOT / 'src' / 'components' / 'hours'
PHONE = (HOURS / 'HoursAccountingMobile.jsx').read_text(encoding='utf-8')
RULES = (HOURS / 'hoursAccountingPhone.js').read_text(encoding='utf-8')
CSS = (HOURS / 'hours-accounting-mobile.css').read_text(encoding='utf-8')


def strip_comments(source):
    return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', source, flags=re.S))


def hours_view():
    """Тело HoursAccountingView — от объявления до настольного return."""
    start = APP.index('const HoursAccountingView = ({')
    end = APP.index('/* Раздел лежит прямо на полотне страницы', start)
    return APP[start:end]


class PhoneLayerTests(unittest.TestCase):
    def test_every_rule_is_locked_to_the_phone_shell(self):
        selectors = []
        for rule in re.findall(r'([^{}]+)\{', re.sub(r'/\*.*?\*/', '', CSS, flags=re.S)):
            selectors.extend(part.strip() for part in rule.split(','))
        self.assertTrue(selectors)
        for selector in selectors:
            self.assertTrue(selector.startswith('body.mobile-shell'), selector)

    def test_colours_are_utilities_not_own_values(self):
        """Тёмный слой портала перекрашивает утилиты Tailwind и токены --sheet-*,
        а свой цвет из этого файла он не видит."""
        body = re.sub(r'/\*.*?\*/', '', CSS, flags=re.S)
        self.assertIsNone(re.search(r'#[0-9a-fA-F]{3,8}\b', body))
        self.assertIsNone(re.search(r'\brgba?\(', body))

    def test_selectors_outweigh_the_shared_section_layer(self):
        """Общий слой и слой моторики задают поля и вид полей ввода весом (0,2,2):
        правило с двумя классами просто не сработает."""
        body = re.sub(r'/\*.*?\*/', '', CSS, flags=re.S)
        for rule in re.findall(r'([^{}]+)\{', body):
            for selector in rule.split(','):
                selector = selector.strip()
                if not selector or selector.startswith('@'):
                    continue
                self.assertIn('.main-content .ha-m-root', selector, selector)

    def test_shared_layers_are_loaded_from_the_phone_module(self):
        """Раздел живёт в ленивом чанке: без своего импорта заголовок, подписи
        групп и разделители строк остались бы без стилей."""
        self.assertIn("import '../resources/shift-auction-mobile.css';", PHONE)
        self.assertIn("import './hours-accounting-mobile.css';", PHONE)

    def test_markup_avoids_the_traps_of_the_shared_section_layer(self):
        code = strip_comments(PHONE)
        # min-w-[ обнуляется, grid-cols-[ сводится к одной колонке, items-start с
        # flex-1 разворачивает ряд в колонку, -mx- переписывается, justify-between
        # получает flex-wrap.
        for trap in ('min-w-[', 'items-start', 'grid-cols-', 'justify-between', ' -mx-'):
            self.assertNotIn(trap, code, trap)
        # Подстрочный отбор общего слоя: такие слова в классе дают flex-wrap.
        self.assertIsNone(re.search(r'className="[^"]*(?:-actions|-tabs|-filters|toolbar|topbar)', code))
        # Сетка календаря — классом из CSS, иначе общий слой оставит две колонки.
        self.assertIn('className="ha-m-grid"', PHONE)
        self.assertIn('grid-template-columns: repeat(7, minmax(0, 1fr));', CSS)

    def test_phone_is_decided_by_the_single_shell_query(self):
        self.assertNotIn('matchMedia', PHONE)
        self.assertNotIn('matchMedia', RULES)

    def test_rules_module_is_free_of_react(self):
        """Правила живут отдельным модулем, чтобы их можно было прогонять узлом
        без браузера — импорт React закрыл бы эту дверь."""
        self.assertNotIn('react', strip_comments(RULES).lower())


class PhoneBranchTests(unittest.TestCase):
    def test_phone_branch_returns_before_the_desktop_table(self):
        body = hours_view()
        self.assertIn('const isHoursPhone = useIsMobileShell();', body)
        self.assertLess(body.index('const isHoursPhone'), body.index('if (isHoursPhone) {'))
        self.assertIn('<HoursAccountingMobileView', body)

    def test_phone_view_is_a_lazy_chunk(self):
        """Раздел супервайзерский: обычным импортом его разметка и правила
        въезжали бы в главный бандл всем, включая операторов."""
        self.assertIn(
            "const HoursAccountingMobileView = lazyWithRetry(() => import('./components/hours/HoursAccountingMobile'));",
            APP,
        )
        self.assertIn('<Suspense fallback={null}>', hours_view())

    def test_desktop_table_stays_as_it_was(self):
        """Настольный вид — та же таблица: широкая сетка, липкая первая колонка,
        подвал «Итого» и окно ячейки."""
        self.assertIn('/* Раздел лежит прямо на полотне страницы', APP)
        self.assertIn('{/* Table */}', APP)
        self.assertIn('min-h-[240px] overflow-auto rounded-2xl bg-white', APP)
        self.assertIn('{/* FOOTER: итоговые строки */}', APP)
        self.assertIn('Мультивыбор ячеек:', APP)
        self.assertIn('{selectedCell && cellModel && (', APP)

    def test_phone_reuses_the_desktop_handlers_and_helpers(self):
        """Ни одной своей формулы и ни одного своего запроса: телефон зовёт те же
        функции раздела, что и таблица."""
        body = hours_view()
        branch = body[body.index('if (isHoursPhone) {'):]
        for handler in (
            'onOpenDay: openCellDetail',
            'onSave: saveCell',
            'updateField: updateCellField',
            'bonusAmount: computeBonusAmountByType',
            'onRefresh: fetchDailyHoursAndTrainings',
            'onDownloadReport: downloadMonthlyReport',
            'onNormChange: handleNormChange',
            'trainingHours: computeUniqueTrainingDurationHours',
            'noPhoneHours: computeNoPhoneHoursFromDaily',
            'onDeleteTraining: handleTrainingDeleteFromModal',
            'onSave: handleOfflineActivitySaveFromModal',
        ):
            self.assertIn(handler, branch, handler)
        self.assertNotIn('axios.', branch, 'запросы остаются в разделе')

    def test_phone_keeps_the_group_side_effect_of_the_desktop_picker(self):
        """Выбор группы на компьютере заодно ставит СВ группы: без него
        вспомогательные данные и загрузка часов работают не так."""
        branch = hours_view()
        branch = branch[branch.index('if (isHoursPhone) {'):]
        self.assertIn('setSelectedSvId(svId ? String(svId) : \'\')', branch)

    def test_foreign_day_switches_the_group_like_the_desktop_lock(self):
        branch = hours_view()
        branch = branch[branch.index('if (isHoursPhone) {'):]
        self.assertIn('onOpenForeignDay:', branch)
        self.assertIn('setSelectedGroupId(String(segment.group_id))', branch)


class PhoneScreenTests(unittest.TestCase):
    def test_section_is_three_levels_deep(self):
        """Список операторов → месяц оператора → день: три экрана, а не таблица."""
        self.assertIn('<AuctionPhoneMonthHeader', PHONE)
        self.assertIn('<HoursPhoneCalendar', PHONE)
        self.assertIn('HoursPhoneDayScreen', PHONE)
        self.assertEqual(PHONE.count('<IosModal'), 4, 'отбор, оператор, день и офлайн-активность')

    def test_day_screen_carries_every_block_of_the_desktop_cell_modal(self):
        for label in ('Показатели дня', 'Тренинги', 'Технические причины', 'Офлайн активность', 'Штрафы', 'Бонусы'):
            self.assertIn(f'label="{label}"', PHONE, label)
        self.assertIn('Очистить показатели дня', PHONE)

    def test_training_modal_is_raised_above_the_day_screen(self):
        """Окно тренинга — чужая разметка со своим z-50, а экран дня стоит на 120:
        без подъёма «Добавить тренинг» открывается невидимым."""
        self.assertIn('className="ha-m-training"', PHONE)
        self.assertIn('.ha-m-training > div', CSS)
        self.assertIn('z-index: 130;', CSS)

    def test_empty_groups_keep_the_card(self):
        """Пять подписей «записей нет» без карточек читаются как обрыв списка."""
        self.assertIn('HoursPhoneEmptyRow', PHONE)
        self.assertGreaterEqual(PHONE.count('<HoursPhoneEmptyRow'), 5)

    def test_metrics_are_a_strip_not_a_dropdown(self):
        """Показателей до четырнадцати; выпадающими меню, как на компьютере, до
        нужного надо добираться в два нажатия."""
        self.assertIn('<AuctionPhoneChips', PHONE)
        self.assertIn('ariaLabel="Показатель"', PHONE)

    def test_fired_tab_is_a_segmented_control(self):
        self.assertIn('ariaLabel="Сотрудники"', PHONE)
        self.assertIn("{ value: 'fired', label: 'Уволенные', count: scope.firedCount }", PHONE)


if __name__ == '__main__':
    unittest.main()
