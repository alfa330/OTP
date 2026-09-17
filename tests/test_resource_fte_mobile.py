"""«Расчет ресурсов» (линия и чат) и планировщик графиков на телефоне.

Владелец 17.09.2026: «необходимо сделать адаптив под разделы расчёт ресурсов
линия/чат и аукцион смен тоже, сделай все аккуратно, удобно в стиле ios/macos»,
«применить только к мобильной версии».

Проверки читают исходники текстом: решения здесь про то, ЧТО рисуется на
телефоне и какими путями. Правила без React (периоды, календарь, время смены)
закреплены в tests/resource_fte_phone.test.mjs.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / 'src' / 'components' / 'resources'
VIEW = (RESOURCES / 'ResourceFteView.jsx').read_text(encoding='utf-8')
PLANNER = (RESOURCES / 'ResourceSchedulePlanner.jsx').read_text(encoding='utf-8')
PHONE = (RESOURCES / 'ResourceFteMobile.jsx').read_text(encoding='utf-8')
RULES = (RESOURCES / 'resourceFtePhone.js').read_text(encoding='utf-8')
CSS = (RESOURCES / 'resource-fte-mobile.css').read_text(encoding='utf-8')


def strip_comments(source):
    return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', source, flags=re.S))


def css_selectors(css):
    selectors = []
    for block in re.findall(r'([^{}]+)\{', re.sub(r'/\*.*?\*/', '', css, flags=re.S)):
        selectors.extend(part.strip() for part in block.split(','))
    return selectors


class PhoneLayerTests(unittest.TestCase):
    def test_every_rule_is_locked_to_the_phone_shell(self):
        """Компьютер не должен получить ни одного правила слоя."""
        selectors = css_selectors(CSS)
        self.assertTrue(selectors)
        for selector in selectors:
            self.assertTrue(selector.startswith('body.mobile-shell'), selector)

    def test_layer_is_loaded_by_the_section(self):
        self.assertIn("import './resource-fte-mobile.css';", VIEW)

    def test_colours_are_utilities_not_hex(self):
        """Тёмный слой портала перекрашивает утилиты и переменные листов, а hex
        из файла стилей раздела он не видит."""
        self.assertIsNone(re.search(r'#[0-9a-fA-F]{3,8}\b', re.sub(r'/\*.*?\*/', '', CSS, flags=re.S)))

    def test_nothing_sticks_to_the_top_of_the_screen(self):
        """iOS 26 заливает полосу с часами сплошным цветом, если у верхней грани
        стоит липкий или fixed-элемент во всю ширину (правило соседней сессии
        17.09.2026, см. tests/test_mobile_status_bar_glass.py). Телефонный вид
        к верху не прибивает ничего, а «Сохранить график» липнет к НИЖНЕЙ грани."""
        code = strip_comments(PHONE + PLANNER[PLANNER.index('if (isMobileShell) {'):])
        self.assertNotIn('sticky top-', code)
        self.assertNotIn('fixed top-', code)
        start = CSS.index('body.mobile-shell .rf-m-root .rf-m-savebar {')
        block = CSS[start:CSS.index('}', start)]
        self.assertIn('position: sticky;', block)
        self.assertIn('bottom: calc(var(--mtb-lift-bottom, 0px) + 10px);', block)
        self.assertNotRegex(block, r'\btop:')

    def test_markup_avoids_the_traps_of_the_shared_section_layer(self):
        """Общий слой разделов обнуляет min-w-[…], переносит .flex-nowrap и ряды
        с «-actions/-tabs/-filters/toolbar/topbar» в имени класса, ставит колонкой
        .flex.items-start с .flex-1 внутри, переписывает -mx-* и сводит
        grid-cols-3…6 к двум колонкам."""
        code = strip_comments(PHONE)
        for trap in ('min-w-[', 'flex-nowrap', ' -mx-', 'items-start', 'grid-cols-'):
            self.assertNotIn(trap, code, trap)
        self.assertIsNone(re.search(r'className="[^"]*(?:-actions|-tabs|-filters|toolbar|topbar)', code))

    def test_wide_tables_get_their_width_back(self):
        """Общий слой обнуляет min-w-[…] у таблиц биллинга — двенадцать колонок
        сжимались в экран. Слой возвращает ширину и закрепляет первую колонку."""
        self.assertIn('width: max-content !important;', CSS)
        self.assertIn('.rf-m-table th:first-child', CSS)
        self.assertIn('position: sticky;', CSS[CSS.index('.rf-m-table th:first-child'):])

    def test_phone_is_decided_by_the_single_shell_query(self):
        self.assertIn('const isMobileShell = useIsMobileShell();', VIEW)
        self.assertIn('const isMobileShell = useIsMobileShell();', PLANNER)
        for source in (PHONE, RULES):
            self.assertNotIn('matchMedia', source)

    def test_rules_never_build_dates_through_utc(self):
        """toISOString в Asia/Almaty до пяти утра отдаёт вчерашний день."""
        self.assertNotIn('toISOString', strip_comments(RULES))
        self.assertNotIn('toISOString', strip_comments(PHONE))


class DesktopUntouchedTests(unittest.TestCase):
    def test_section_branches_before_the_desktop_markup(self):
        """Телефон уходит ранним return: настольная разметка раздела не переписана.
        Все хуки объявлены выше ветки — иначе порядок хуков разъедется при повороте."""
        phone_at = VIEW.index('  if (isMobileShell) {\n    let phoneTab = null;')
        desktop_at = VIEW.index('  return (\n    <div className="min-h-screen bg-slate-50">')
        self.assertLess(phone_at, desktop_at)
        tail = VIEW[phone_at:desktop_at]
        self.assertIsNone(re.search(r'\buse(State|Effect|Memo|Callback|Ref|RfPhoneLastPresent)\(', tail))
        # Шапка компьютера — прежняя, липкая: к ней на телефоне дело не доходит.
        self.assertIn("activeDashboardView === 'schedule_planner' ? 'relative' : 'sticky top-0'", VIEW[desktop_at:])

    def test_planner_branches_before_the_desktop_canvas(self):
        phone_at = PLANNER.index('  if (isMobileShell) {\n    const rates = templateRatesFor(apiPrefix);')
        desktop_at = PLANNER.index("  return (\n    <div className={`space-y-4 ${hasScheduleToSave ? 'pb-24' : ''}`}>")
        self.assertLess(phone_at, desktop_at)
        self.assertNotIn('<PlannerDayRow', PLANNER[phone_at:desktop_at], 'полотно на телефоне не монтируется')

    def test_desktop_overlays_do_not_open_on_the_phone(self):
        """Самодельные окна компьютера (загрузка CSV, Oktell, детали расчёта) на
        телефоне заменены экранами IosModal: ранний return их не монтирует."""
        phone = VIEW[VIEW.index('  const renderPhoneScreens = () => {'):VIEW.index('  if (isMobileShell) {\n    let phoneTab = null;')]
        for screen in ('open={isUploadModalOpen}', 'open={isOktellSyncModalOpen}', 'open={isOperatorDetailsOpen}'):
            self.assertIn(screen, phone)
        self.assertNotIn('fixed inset-0', phone)


class PhoneSectionTests(unittest.TestCase):
    @staticmethod
    def phone_block():
        return VIEW[VIEW.index('  // ── Телефон ──'):VIEW.index('  return (\n    <div className="min-h-screen bg-slate-50">')]

    def test_every_tab_has_a_phone_view(self):
        block = self.phone_block()
        for render in ('renderPhoneOverview()', 'renderPhoneForecast()', 'renderPhoneChats()',
                       'renderPhoneLosses()', 'renderPhoneBilling()', 'renderPhoneSettings()'):
            self.assertIn(render, block)
        self.assertIn("activeDashboardView === 'schedule_planner'", block)
        self.assertIn('<ResourceSchedulePlanner', block)

    def test_phone_uses_the_same_data_and_handlers(self):
        """Ни одного своего запроса: те же обработчики, что у компьютера."""
        block = self.phone_block()
        self.assertNotIn('axios.', block)
        self.assertNotIn('fetch(', block)
        for handler in ('fetchOverview', 'handleRecalculate', 'buildBillingReport', 'exportBillingExcel',
                        'handleSaveSettings', 'handleUpload', 'handleOktellSync', 'handleOperatorRateChange',
                        'saveBillingGroupingComment', 'toggleDisplayOption'):
            self.assertIn(handler, block, handler)

    def test_telephony_metrics_stay_behind_capability_flags(self):
        """Чат рисуется той же веткой: телефонные показатели закрыты признаками
        направления, как и на компьютере."""
        block = self.phone_block()
        for guard in ('cfg.hasAht && displayOptions.forecastKpiAht',
                      'cfg.hasAnswerRate && displayOptions.forecastKpiAnswerRate',
                      'cfg.hasOccUr && displayOptions.forecastKpiOccUr',
                      'cfg.hasWorkloadMinutes && displayOptions.forecastTableWorkload',
                      'cfg.hasUpload ? {', 'cfg.hasOktellSync ? {'):
            self.assertIn(guard, block, guard)
        self.assertIn("{ key: 'ar', label: 'AR'", block)
        self.assertIn('if (!cfg.hasBillingTalkTime) {', block)
        self.assertIn('cfg.unit.', block, 'подписи чата — из словаря направления')

    def test_tabs_are_a_scrolling_strip_not_three_rows(self):
        self.assertIn('<RfPhonePills', self.phone_block())
        self.assertIn("items={cfg.tabs.map((tab) => ({ value: tab.key, label: phoneTabLabel(tab) }))}", self.phone_block())

    def test_windows_are_screens(self):
        block = self.phone_block()
        for title in ("title=\"Загрузка отчета\"", "title=\"Синхронизация с Oktell\"", "title=\"Детализация доступного FTE\"",
                      "title=\"Показатели прогноза\"", "title=\"Время отчета\"", "title=\"Период отчета\""):
            self.assertIn(title, block, title)
        self.assertIn('<BillingGroupingCommentPhoneScreen', block)
        self.assertIn('<MobileActionSheet', block)

    def test_rate_and_contribution_are_not_two_equal_numbers_in_a_row(self):
        """В «Деталях расчёта» справа — только ставка, вклад — строкой под именем:
        два «1,00» подряд читались как одно число, повторённое дважды."""
        block = self.phone_block()
        self.assertIn('value={canEditOperatorRates ? null : formatNumber(operator.rate, 2)}', block)
        self.assertIn("`вклад ${formatNumber(operator.fteContribution, 2)}`", block)

    def test_number_fields_accept_a_comma(self):
        """Русская раскладка iOS даёт на цифровой клавиатуре запятую, а
        type="number" на «0,9» отдаёт пустую строку."""
        row = PHONE[PHONE.index('export const RfPhoneInputRow'):PHONE.index('export const RfPhoneSelectRow')]
        self.assertIn('type="text"', row)
        self.assertIn(".replace(',', '.')", row)
        self.assertNotIn('type="number"', strip_comments(PHONE))

    def test_tiles_never_leave_an_empty_cell(self):
        self.assertIn('phoneTileSpans(visible.length)', PHONE)
        self.assertIn("style={spans[index] ? { gridColumn: '1 / -1' } : undefined}", PHONE)


class PhonePlannerTests(unittest.TestCase):
    @staticmethod
    def phone_block():
        return PLANNER[PLANNER.index('  if (isMobileShell) {\n    const rates'):PLANNER.index("  return (\n    <div className={`space-y-4")]

    def test_shift_time_goes_through_the_same_history_and_update(self):
        """Правка смены на телефоне — те же updateShift и история правок, что у
        перетаскивания: «Отменить» работает одинаково."""
        block = self.phone_block()
        self.assertIn('phoneShiftEditRange(editShown.start, editShown.end)', block)
        save = block[block.index('const saveShiftEdit = () => {'):block.index('// Правка шаблонов')]
        self.assertIn('pushHistorySnapshot();', save)
        self.assertIn('updateShift(phoneShiftEdit.dayIndex, phoneShiftEdit.shiftId, () => ({', save)
        self.assertIn('isLockedPlannerShift(current)', save)
        for action in ('undoPlannerChange();', 'redoPlannerChange();', 'sortSelectedDayShifts();',
                       'deleteShift(phoneShiftEdit.dayIndex, phoneShiftEdit.shiftId)', 'addShift(activeDayIndex, template.id)'):
            self.assertIn(action, block, action)

    def test_locked_and_auction_shifts_are_not_editable(self):
        block = self.phone_block()
        self.assertIn('const locked = isAuctionShift || isLockedPlannerShift(shift);', block)
        self.assertIn('const canEdit = editableDay && !locked;', block)
        self.assertIn('const editableDay = !activeDayIsReadOnly && activeDayIndex < plannerDays.length;', block)

    def test_save_button_appears_only_when_there_is_something_to_save(self):
        """«График сохранен» висел бы поверх смен погашенной плашкой."""
        block = self.phone_block()
        self.assertIn('{hasUnsavedScheduleChanges ? (\n          <div className="rf-m-savebar">', block)
        self.assertIn('onClick={saveCurrentSchedule}', block)

    def test_phone_shifts_stay_a_chat_only_choice(self):
        block = self.phone_block()
        self.assertIn('{phoneShiftsEnabled ? (', block)
        self.assertIn('addShift(activeDayIndex, item.id, { phoneTemplate: item });', block)

    def test_chat_auction_stays_behind_its_flag(self):
        block = self.phone_block()
        self.assertIn("enableShiftAuction && typeof onOpenShiftAuction === 'function'", block)
        self.assertIn('direction: auctionDirectionFor(apiPrefix),', block)


if __name__ == '__main__':
    unittest.main()
