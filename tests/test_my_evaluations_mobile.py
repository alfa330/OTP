# -*- coding: utf-8 -*-
"""«Мои оценки» на телефоне: месяц в шапке, балл карточкой, оценки строками,
оценка и мониторинговая шкала — экранами; настольный вид не тронут — ветки
разведены isMobileShell.

Проверки читают исходники текстом: решения здесь про то, ЧТО рисуется на
телефоне и какими путями. Форматы и содержимое экрана оценки закреплены в
tests/my_evaluations_phone.test.mjs.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
SECTION = ROOT / 'src' / 'components' / 'evaluation'
PHONE = (SECTION / 'MyEvaluationsMobile.jsx').read_text(encoding='utf-8')
RULES = (SECTION / 'myEvaluationsPhone.js').read_text(encoding='utf-8')
CSS = (SECTION / 'my-evaluations-mobile.css').read_text(encoding='utf-8')
MONTH = (ROOT / 'src' / 'utils' / 'phoneMonth.js').read_text(encoding='utf-8')
SHARED = (ROOT / 'src' / 'components' / 'resources' / 'ShiftAuctionMobile.jsx').read_text(encoding='utf-8')
DISPUTE = (ROOT / 'src' / 'components' / 'modals' / 'DisputeModal.jsx').read_text(encoding='utf-8')


def strip_comments(source):
    return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', source, flags=re.S))


def evaluation_block():
    start = APP.index("                                    {view === 'evaluation' && (\n")
    return APP[start:APP.index("view === 'salary' && (", start)]


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
        self.assertIn("import './my-evaluations-mobile.css';", PHONE)

    def test_markup_avoids_the_traps_of_the_shared_section_layer(self):
        code = strip_comments(PHONE)
        for trap in ('min-w-[', 'items-start', 'grid-cols-', 'justify-between', ' -mx-', 'flex-nowrap'):
            self.assertNotIn(trap, code, trap)
        self.assertIsNone(re.search(r'className="[^"]*(?:-actions|-tabs|-filters|toolbar|topbar)', code))

    def test_phone_is_decided_by_the_single_shell_query(self):
        self.assertNotIn('matchMedia', PHONE)
        self.assertNotIn('matchMedia', RULES)


class SharedWithMyHoursTests(unittest.TestCase):
    """Шапка с месяцем и полукруг — одни на «Мои часы» и «Мои оценки»."""

    def test_month_header_and_gauge_live_in_one_place(self):
        self.assertIn('export const AuctionPhoneMonthHeader', SHARED)
        self.assertIn('export const AuctionPhoneGauge', SHARED)
        hours = (ROOT / 'src' / 'components' / 'hours' / 'MyHoursMobile.jsx').read_text(encoding='utf-8')
        self.assertIn('<AuctionPhoneMonthHeader', hours)
        self.assertIn('<AuctionPhoneMonthHeader', PHONE)
        self.assertIn('<AuctionPhoneGauge', hours)
        self.assertIn('<AuctionPhoneGauge', PHONE)

    def test_month_step_walks_the_same_list_as_the_desktop_picker(self):
        """getMonthOptions отдаёт текущий месяц и одиннадцать прошлых — за краем
        списка стрелка гаснет, иначе раздел запросил бы месяц, которого в нём нет."""
        self.assertIn('export const PHONE_MONTHS_BACK = 11;', MONTH)
        self.assertIn("for (let i = 0; i < 12; i++)", APP)
        self.assertIn('shiftPhoneMonth(month, -1)', PHONE)
        self.assertIn('prevDisabled={!prev}', PHONE)
        hours_rules = (ROOT / 'src' / 'components' / 'hours' / 'myHoursPhone.js').read_text(encoding='utf-8')
        self.assertIn("from '../../utils/phoneMonth.js'", hours_rules)


class PhoneBranchTests(unittest.TestCase):
    def test_phone_view_returns_before_the_desktop_tree(self):
        block = evaluation_block()
        phone = block.index('if (isMobileShell) {\n')
        view = block.index('<MyEvaluationsPhone\n')
        self.assertLess(phone, view)
        self.assertLess(view, block.index('return isLoading ? ('))

    def test_desktop_markup_stays_as_it_was(self):
        block = evaluation_block()
        self.assertIn("'bg-white p-4 sm:p-6 lg:p-8 rounded-xl shadow-md mb-8 border border-gray-200 transition-all duration-300 hover:shadow-lg'", block)
        self.assertIn('<span className="text-blue-600">Ваши оценки</span>', block)
        self.assertIn('{showEvaluationMonitoringScale ? \'Скрыть шкалу\' : \'Мониторинговая шкала\'}', block)
        self.assertIn('{/* Desktop table layout */}', block)
        self.assertIn('{/* Mobile card layout */}', block)
        self.assertIn('<SemiCircleProgress', block)

    def test_month_picker_moves_into_the_phone_header(self):
        self.assertIn("{((view === 'evaluation' || view === 'hours') && !isMobileShell) && (", APP)
        block = evaluation_block()
        self.assertIn('<MyEvaluationsPhoneHeader', block)
        self.assertIn('onMonthChange={setSelectedMonth}', block)
        self.assertIn('{getMonthOptions()}', block)

    def test_skeleton_and_lock_have_phone_variants(self):
        block = evaluation_block()
        self.assertIn('isMobileShell ? <MyEvaluationsPhoneSkeleton /> : <EvaluationPageSkeleton />', block)
        self.assertIn("{!isMobileShell && user.role === 'operator' && (", block)

    def test_checkpoint_low_ratings_and_dispute_are_shared_by_both_views(self):
        """Срок повторной проверки, блок низких оценок и окно спора считаются один
        раз: у телефона нет второй копии ни одного из них."""
        block = evaluation_block()
        for name in ('openCheckpoint', 'lowRatingsBlock', 'disputeBlock'):
            self.assertIn('const %s = ' % name, block)
            self.assertGreaterEqual(block.count(name), 3, name)
        self.assertEqual(block.count('<MyLowRatings'), 1)
        self.assertEqual(block.count('<DisputeModal'), 1)

    def test_audio_is_loaded_by_the_app_helper_once_per_evaluation(self):
        block = evaluation_block()
        self.assertIn('if (expandedEvaluation?.id !== ev.id) handleEvaluationClick(ev);', block)
        self.assertIn('player: audioUrl ? <AudioPlayer audioSrc={audioUrl}', block)

    def test_scale_state_stays_in_the_app(self):
        """Состояние шкалы живёт в App: компонент, объявленный в его теле,
        пересоздаётся на каждом рендере вместе с состоянием."""
        block = evaluation_block()
        self.assertIn('open: showEvaluationMonitoringScale,', block)
        self.assertIn('onClose: closeEvaluationMonitoringScale,', block)
        self.assertIn('onToggleCriterion: (index) => setSelectedMonitoringScaleCriterionIndex(', block)
        self.assertNotIn('useState', RULES)


class PhoneRulesMirrorTheDesktopTests(unittest.TestCase):
    """Правила телефона повторяют настольные дословно — иначе оценка выглядела бы
    по-разному на двух экранах."""

    def test_score_thresholds_are_the_same(self):
        desktop = APP[APP.index('const getScoreColor = (score) => {'):]
        desktop = desktop[:desktop.index('};')]
        self.assertIn('score >= 90', desktop)
        self.assertIn('score >= 60', desktop)
        self.assertIn('if (value >= 90) return \'green\';', RULES)
        self.assertIn('if (value >= 60) return \'amber\';', RULES)

    def test_reevaluation_condition_is_the_same(self):
        block = evaluation_block()
        self.assertIn("ev.score < 100 && !['pending', 'approved'].includes(requestMeta.status)", block)
        self.assertIn('Number(evaluation?.score) < 100', RULES)
        self.assertIn("!['pending', 'approved'].includes(String(requestStatus || 'none'))", RULES)

    def test_verdict_labels_match_the_reviewers_screen(self):
        reviewer = (ROOT / 'src' / 'components' / 'call_qa' / 'CallReviewCard.jsx').read_text(encoding='utf-8')
        for label in ("'Верно'", "'Неверно'", "'Недочёт'"):
            self.assertIn(label, reviewer)
            self.assertIn(label, RULES)


class PhoneScreensTests(unittest.TestCase):
    def test_evaluation_and_scale_open_as_screens(self):
        """На телефоне IosModal — это экран с шевроном «назад» и системным жестом:
        раскрытие оценки на месте прятало бы критерии под вторую вложенную таблицу."""
        self.assertEqual(PHONE.count('<IosModal'), 2)
        self.assertIn('<EvaluationScreen', PHONE)
        self.assertIn('<MonitoringScaleScreen', PHONE)

    def test_leaving_screen_keeps_its_evaluation(self):
        self.assertIn('const shownRow = useLastPresent(openRow);', PHONE)

    def test_audio_and_chat_stay_behind_the_qr_gate(self):
        self.assertIn('Скрыта до QR-подтверждения доступа', PHONE)
        self.assertIn('Откроется после QR-подтверждения доступа.', PHONE)

    def test_request_button_lives_in_the_screen_footer(self):
        self.assertIn('footer={canRequest ? (', PHONE)
        self.assertIn("'Повторить запрос' : 'Запросить переоценку'", PHONE)

    def test_request_form_is_a_phone_screen_too(self):
        """Форма запроса открывается из этого же раздела: на телефоне она экран со
        строками и одной кнопкой внизу, а настольное окно оставлено прежним."""
        self.assertIn('if (isMobileShell) {', DISPUTE)
        self.assertIn('<IosModal', DISPUTE)
        self.assertIn('className={AUCTION_PHONE_BUTTON.blue}', DISPUTE)
        self.assertIn('otp-modal-card workhours-modal bg-white rounded-2xl', DISPUTE)
        # Запись в стеке «назад» кладёт IosModal — своя была бы второй.
        self.assertNotIn('useScreenBackGesture', DISPUTE)
        # Клавиатура посреди въезда экрана дёргает его на середине пути.
        self.assertIn('isMobileShell ? 420 : 80', DISPUTE)


if __name__ == '__main__':
    unittest.main()
