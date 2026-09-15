# -*- coding: utf-8 -*-
"""«Мои смены» на телефоне: вкладки переключателем, неделя полосой дней, день
списком, запрос на замену — экраном с одной кнопкой; окна запроса вынесены из
вкладки «Замены» (иначе «Обменять смену» из «Смен» открывал вопрос в никуда).

Проверки читают исходники текстом: решения здесь про то, ЧТО рисуется на
телефоне и какими путями; подписи дней и порядок коллег закреплены в
tests/my_shifts_phone_days.test.mjs.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
SCHEDULE = ROOT / 'src' / 'components' / 'schedule'
PHONE = (SCHEDULE / 'MyShiftsMobile.jsx').read_text(encoding='utf-8')
CSS = (SCHEDULE / 'my-shifts-mobile.css').read_text(encoding='utf-8')
STRIP = (ROOT / 'src' / 'components' / 'resources' / 'ShiftAuctionMobile.jsx').read_text(encoding='utf-8')


def strip_comments(css):
    return re.sub(r'/\*.*?\*/', '', css, flags=re.S)


def block(source, start_marker, end_marker):
    start = source.index(start_marker)
    return source[start:source.index(end_marker, start)]


class PhoneLayerTests(unittest.TestCase):
    def test_every_rule_is_locked_to_the_phone_shell(self):
        selectors = []
        for rule in re.findall(r'([^{}]+)\{', strip_comments(CSS)):
            selectors.extend(part.strip() for part in rule.split(','))
        self.assertTrue(selectors)
        for selector in selectors:
            self.assertTrue(selector.startswith('body.mobile-shell'), selector)

    def test_colours_are_utilities_not_hex(self):
        self.assertIsNone(re.search(r'#[0-9a-fA-F]{3,8}\b', strip_comments(CSS)))

    def test_shared_auction_layer_is_loaded_from_the_phone_module(self):
        """Раздел живёт в главном чанке, аукцион — в ленивом: без своего импорта
        полоса дней осталась бы без стекла и прилипания."""
        self.assertIn("import '../resources/shift-auction-mobile.css';", PHONE)
        self.assertIn("import './my-shifts-mobile.css';", PHONE)
        self.assertIn('className="sa-m-root min-h-screen bg-slate-100"', APP)

    def test_markup_avoids_the_traps_of_the_shared_section_layer(self):
        code = re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', PHONE, flags=re.S))
        for trap in ('min-w-[', 'flex-nowrap', ' -mx-', 'items-start'):
            self.assertNotIn(trap, code, trap)
        self.assertIsNone(re.search(r'className="[^"]*(?:-actions|-tabs|-filters|toolbar|topbar)"', code))

    def test_request_card_does_not_truncate_the_period(self):
        """Бейдж статуса рядом с заголовком резал и заголовок, и период
        («Сокращение см…», «09:00 — 1…»): статус живёт в начале второй строки,
        период за ним переносится."""
        card = block(PHONE, 'export const MyShiftsRequestCard', 'export const MyShiftsCandidateRow')
        self.assertIn('<p className="mt-1 pl-[42px] text-[14px] leading-snug tabular-nums text-slate-500">', card)
        self.assertIn('{badge ? <IosBadge tone={badgeTone} className="mr-1.5 align-[1px]">{badge}</IosBadge> : null}', card)
        self.assertNotIn('block truncate text-[14px] tabular-nums text-slate-500">{subtitle}', card)

    def test_day_strip_accepts_a_header_row(self):
        """Строка «‹ › 14 — 20 сентября · Сегодня» липнет вместе с днями."""
        self.assertIn('AuctionPhoneDayStrip = ({ days = [], activeDate, onSelect, header = null })', STRIP)
        self.assertIn('{header}', STRIP)
        self.assertIn('header={(', APP)
        self.assertIn('<MyShiftsWeekHeader', APP)


class PhoneBranchTests(unittest.TestCase):
    def test_phone_branch_returns_before_the_desktop_tree(self):
        """Настольная разметка на телефоне не монтируется вовсе — у неё свой return."""
        branch = block(APP, '                if (isNarrowShell) {\n                    const phoneToday', '                return (\n                    <div className="px-0 sm:px-4 py-2 min-h-screen bg-slate-50">')
        for marker in ('renderPhoneSchedule()', 'renderPhoneSwaps()', 'renderPhoneRequests()', 'renderPhoneDirection()', 'renderPhoneSwapScreens()', '{shiftChangeModal}'):
            self.assertIn(marker, branch, marker)

    def test_phone_is_decided_by_the_single_shell_query(self):
        self.assertIn('const isNarrowShell = useIsMobileShell();', APP)
        self.assertNotIn('matchMedia', PHONE)

    def test_phone_lives_in_week_mode_only(self):
        self.assertIn("if (!isNarrowShell || !isOperatorSelfSchedules || viewMode === 'week') return;\n                setViewMode('week');", APP)
        # Дневной запрос не уходит впустую, пока телефон переключает период.
        self.assertIn("if (isNarrowShell && viewMode !== 'week') return;\n                const startDate = visibleRange?.[0];", APP)

    def test_selecting_a_day_does_not_refetch_the_week(self):
        """Выбор дня внутри недели не трогает currentDate: weekRange пересчитался
        бы в новый массив и перезапросил ту же неделю."""
        self.assertIn('onSelect={setPhoneSelectedDate}', APP)
        self.assertIn("if (!weekRange.includes(date)) setCurrentDate(parseDateStr(date));", APP)

    def test_swap_windows_are_mounted_outside_the_swaps_tab(self):
        """«Обменять смену» из вкладки «Смены» ждёт ответа окна «замена или
        обмен?» — оно обязано быть в дереве при любой вкладке."""
        choice = APP.index('open={!!swapExchangeChoiceModal.open && !isNarrowShell}')
        tab = APP.index("{operatorSelfTab === 'swaps' && (\n                                        <div className=\"space-y-0 sm:space-y-3 pb-2\">")
        self.assertLess(choice, tab)
        self.assertIn('open={showSwapCreateModal && !isNarrowShell}', APP)
        self.assertIn('open={showSwapTargetSegmentsModal && showSwapCreateModal && isSwapExchangeMode && !isNarrowShell}', APP)

    def test_phone_asks_with_a_sheet_not_a_window(self):
        branch = block(APP, 'const renderPhoneSwapScreens = () => (', 'const phoneSwapListsExpandedUnused' if 'phoneSwapListsExpandedUnused' in APP else '                    return (\n                        <div className="sa-m-root')
        self.assertIn('open={!!swapExchangeChoiceModal.open}', branch)
        self.assertIn("{ key: 'replacement', label: 'Обычная замена', onClick: () => closeSwapExchangeChoiceModal(false) }", branch)
        self.assertIn("{ key: 'exchange', label: 'Обмен сменами', onClick: () => closeSwapExchangeChoiceModal(true) }", branch)
        # Отказ и отмена — тоже листом, а не window.confirm.
        self.assertIn("handleRespondSwapRequest(req, action, { skipConfirm: true });", branch)
        self.assertIn("if (!skipConfirm && (actionNorm === 'reject' || actionNorm === 'cancel')) {", APP)
        # Тип запроса на телефоне выбирают переключателем, вопрос сам не всплывает.
        self.assertIn("if (!showSwapCreateModal) return;\n                // На телефоне тип запроса — переключатель в форме, вопрос листом не нужен.\n                if (isNarrowShell) return;", APP)

    def test_phone_swap_screen_sends_the_current_form_only(self):
        """Списка к отправке на телефоне нет: одна форма — одна кнопка."""
        branch = block(APP, 'const renderPhoneSwapScreens = () => (', '                    return (\n                        <div className="sa-m-root')
        self.assertIn('disabled={swapSubmitting || !canSubmitCurrentSwapDraft}', branch)
        self.assertNotIn('handleAddSwapDraft', branch)
        self.assertIn('onClick={handleCreateSwapRequest}', branch)

    def test_timeline_stays_on_the_phone(self):
        """Владелец 14.09.2026: «ты убрал таймлайн, он пусть остается» — лента дня
        в «Сменах» и «Ваша смена в этот день» в запросе рисуются и на телефоне."""
        branch = block(APP, '                if (isNarrowShell) {\n                    const phoneToday', '                return (\n                    <div className="px-0 sm:px-4 py-2 min-h-screen bg-slate-50">')
        self.assertGreaterEqual(branch.count('<MyShiftsTimeline'), 3)
        self.assertIn('getShiftPartsForDate(myTimelineOperator, phoneDayDate)', branch)
        self.assertIn("label=\"Ваша смена в этот день\"", branch)
        self.assertIn('export const MyShiftsTimeline', PHONE)

    def test_front_office_keeps_colleagues_hidden_on_the_phone(self):
        branch = block(APP, '                if (isNarrowShell) {\n                    const phoneToday', '                return (\n                    <div className="px-0 sm:px-4 py-2 min-h-screen bg-slate-50">')
        self.assertIn("operatorColleagueShiftsHidden ? null : { value: 'swaps', label: 'Замены', count: swapPendingIncomingCount }", branch)
        self.assertIn("operatorColleagueShiftsHidden ? null : { value: 'direction', label: 'Коллеги' }", branch)
        self.assertIn('if (!operatorColleagueShiftsHidden) {\n                                        rows.push(', branch)

    def test_shift_change_form_is_shared_and_cancel_is_hidden_on_the_phone(self):
        self.assertIn('const shiftChangeModal = (', APP)
        self.assertEqual(APP.count('open={showShiftChangeModal}'), 1)
        footer = block(APP, 'const shiftChangeModal = (', 'ariaLabel="Что нужно изменить"')
        self.assertIn('{!isNarrowShell && (\n                                                <button', footer)


if __name__ == '__main__':
    unittest.main()
