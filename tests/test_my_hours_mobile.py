# -*- coding: utf-8 -*-
"""«Мои часы» на телефоне: месяц в шапке, норма карточкой, группы строк,
календарь ячейками и день экраном; настольный вид не тронут — ветки разведены
isMobileShell.

Проверки читают исходники текстом: решения здесь про то, ЧТО рисуется на
телефоне и какими путями. Правила ячеек, форматы и содержимое экрана дня
закреплены в tests/my_hours_phone.test.mjs.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
HOURS = ROOT / 'src' / 'components' / 'hours'
PHONE = (HOURS / 'MyHoursMobile.jsx').read_text(encoding='utf-8')
RULES = (HOURS / 'myHoursPhone.js').read_text(encoding='utf-8')
CSS = (HOURS / 'my-hours-mobile.css').read_text(encoding='utf-8')


def strip_comments(source):
    return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', source, flags=re.S))


def hours_block():
    start = APP.index("                                {view === 'hours' && (\n")
    return APP[start:APP.index("{view === 'evaluation' && (", start)]


class PhoneLayerTests(unittest.TestCase):
    def test_every_rule_is_locked_to_the_phone_shell(self):
        selectors = []
        for rule in re.findall(r'([^{}]+)\{', re.sub(r'/\*.*?\*/', '', CSS, flags=re.S)):
            selectors.extend(part.strip() for part in rule.split(','))
        self.assertTrue(selectors)
        for selector in selectors:
            self.assertTrue(selector.startswith('body.mobile-shell'), selector)

    def test_colours_are_utilities_not_hex(self):
        self.assertIsNone(re.search(r'#[0-9a-fA-F]{3,8}\b', re.sub(r'/\*.*?\*/', '', CSS, flags=re.S)))

    def test_shared_layers_are_loaded_from_the_phone_module(self):
        """Раздел живёт в главном чанке, аукцион — в ленивом: без своего импорта
        заголовок, подписи групп и разделители строк остались бы без стилей."""
        self.assertIn("import '../resources/shift-auction-mobile.css';", PHONE)
        self.assertIn("import './my-hours-mobile.css';", PHONE)

    def test_markup_avoids_the_traps_of_the_shared_section_layer(self):
        code = strip_comments(PHONE)
        for trap in ('min-w-[', 'items-start', 'grid-cols-', 'justify-between', ' -mx-'):
            self.assertNotIn(trap, code, trap)
        self.assertIsNone(re.search(r'className="[^"]*(?:-actions|-tabs|-filters|toolbar|topbar)', code))
        # Сетка календаря — классом из CSS: утилиту общий слой свёл бы к двум колонкам.
        self.assertIn('className="mh-m-grid"', PHONE)
        self.assertIn('grid-template-columns: repeat(7, minmax(0, 1fr));', CSS)

    def test_phone_is_decided_by_the_single_shell_query(self):
        self.assertNotIn('matchMedia', PHONE)
        self.assertNotIn('matchMedia', RULES)


class PhoneBranchTests(unittest.TestCase):
    def test_phone_summary_returns_before_the_desktop_grid(self):
        block = hours_block()
        phone = block.index('if (isMobileShell) {\n')
        self.assertLess(phone, block.index('<MyHoursPhoneSummary'))
        self.assertLess(block.index('<MyHoursPhoneSummary'), block.index('<div className="workhours-main-grid'))

    def test_desktop_markup_stays_as_it_was(self):
        block = hours_block()
        self.assertIn("'workhours-section workhours-container bg-white p-4 sm:p-8 rounded-xl shadow-md mb-8 border border-gray-200 transition-all duration-300 hover:shadow-lg'", block)
        self.assertIn('<span className="text-blue-600">Ваши рабочие часы</span>', block)
        self.assertIn(': <HoursPageSkeleton />', block)
        self.assertIn('<WorkHoursCalendar', block)
        self.assertIn('Нет информации о часах.', block)

    def test_month_picker_moves_into_the_phone_header(self):
        self.assertIn("{((view === 'evaluation' || view === 'hours') && !isMobileShell) && (", APP)
        block = hours_block()
        self.assertIn('<MyHoursPhoneHeader', block)
        self.assertIn('onMonthChange={setSelectedMonth}', block)
        self.assertIn('{getMonthOptions()}', block)

    def test_salary_labels_are_computed_once_for_the_desktop(self):
        """Цепочка моделей считается один раз и живёт на компьютере: кнопку
        калькулятора телефон берёт оттуда же, второй копии цепочки нет."""
        block = hours_block()
        for name in ('salaryPreviewCaption', 'salaryPreviewNote', 'salaryCalculatorLabel'):
            self.assertIn(f'const {name} = ', block)
        self.assertIn('{salaryPreviewCaption}', block)
        self.assertIn('{salaryPreviewNote}', block)
        self.assertGreaterEqual(block.count('salaryCalculatorLabel'), 3)
        self.assertIn('Открыть калькулятор «Поток»', block)

    def test_salary_captions_are_dropped_on_the_phone_for_every_model(self):
        """Пояснения под примерной зарплатой на телефоне сняты у ВСЕХ моделей —
        две строки прозы занимали больше места, чем сама сумма. На компьютере
        подписи остаются, поэтому строки из раздела не удалены."""
        block = hours_block()
        self.assertIn('caption: null,', block)
        self.assertIn('note: null,', block)
        self.assertNotIn('caption: salaryPreviewCaption', block)
        self.assertNotIn('note: salaryPreviewNote', block)
        for line in (
            "'Для чат-модели используется отдельный калькулятор.'",
            "'Чаты, оценка и время ответа уже подтянуты в часы работы.'",
            "'Штрафы в формуле калькулятора не вычитаются.'",
        ):
            self.assertIn(line, block)

    def test_dual_model_banner_is_hidden_on_the_phone(self):
        """Пояснение «вы работали по двум моделям» — та же проза в том же месте:
        на телефоне снято пропом, на компьютере остаётся по умолчанию."""
        block = hours_block()
        self.assertIn('showIntro={false}', block)
        self.assertEqual(block.count('showIntro'), 1)
        dual = (ROOT / 'src' / 'components' / 'salary' / 'DualPeriodBreakdown.jsx').read_text(encoding='utf-8')
        self.assertIn('showIntro = true', dual)
        self.assertIn('{showIntro ? (', dual)
        self.assertIn('В этом месяце вы работали по двум моделям.', dual)

    def test_phone_calendar_reuses_app_helpers_and_endpoint(self):
        block = hours_block()
        self.assertIn('<MyHoursPhoneCalendar', block)
        self.assertIn('resolveDayModel={resolveWorkHoursDayModelCode}', block)
        self.assertIn('countTrainingHours={computeUniqueTrainingDurationHours}', block)
        self.assertIn('`${API_BASE_URL}/api/hours/send_request`', block)
        self.assertIn("onSent={() => showToast('Запрос успешно отправлен супервайзеру', 'success')}", block)

    def test_calendar_state_lives_in_the_module(self):
        """Компонент, объявленный в теле App, пересоздаётся на каждом его рендере —
        экран дня с набранным запросом держит состояние в модуле."""
        self.assertIn('export const MyHoursPhoneCalendar', PHONE)
        self.assertIn("const [message, setMessage] = useState('');", PHONE)
        self.assertNotIn('MyHoursPhoneCalendar = (', APP)

    def test_request_form_is_hidden_behind_a_row(self):
        self.assertIn('{composing ? (', PHONE)
        self.assertIn('title="Запрос супервайзеру" onClick={onCompose}', PHONE)
        self.assertIn('footer={composing ? (', PHONE)

    def test_norm_gauge_stays_on_the_phone(self):
        """Полукруг нормы — визуальный знак раздела: на телефоне он остаётся (SVG)."""
        self.assertIn('const MyHoursNormGauge', PHONE)
        self.assertIn('<MyHoursNormGauge percent={percent} tone={status.tone} />', PHONE)

    def test_notice_empty_and_skeleton_have_phone_variants(self):
        block = hours_block()
        self.assertIn('if (isMobileShell) return <MyHoursPhoneNotice segments={_segs} />;', block)
        self.assertIn('isMobileShell ? <MyHoursPhoneSkeleton /> : <HoursPageSkeleton />', block)
        self.assertIn('<MyHoursPhoneEmpty />', block)


if __name__ == '__main__':
    unittest.main()
