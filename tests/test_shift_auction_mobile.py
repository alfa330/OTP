"""«Аукцион смен» на телефоне: сетка «ставки × дни» заменена полосой дней и
списком смен выбранного дня, окна стали экранами портала.

Проверки читают исходники текстом: решения здесь про то, ЧТО рисуется на
телефоне и какими путями, а поведение самих правил (какая ветка у смены)
закреплено в tests/shift_auction_phone_rows.test.mjs.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / 'src' / 'components' / 'resources'
VIEW = (RESOURCES / 'ShiftAuctionView.jsx').read_text(encoding='utf-8')
PHONE = (RESOURCES / 'ShiftAuctionMobile.jsx').read_text(encoding='utf-8')
CSS = (RESOURCES / 'shift-auction-mobile.css').read_text(encoding='utf-8')
SHELL_CSS = (ROOT / 'src' / 'components' / 'common' / 'mobile-shell.css').read_text(encoding='utf-8')


def strip_comments(css):
    return re.sub(r'/\*.*?\*/', '', css, flags=re.S)


class PhoneLayerTests(unittest.TestCase):
    def test_every_rule_is_locked_to_the_phone_shell(self):
        """Компьютер не должен получить ни одного правила слоя."""
        selectors = []
        for block in re.findall(r'([^{}]+)\{', strip_comments(CSS)):
            selectors.extend(part.strip() for part in block.split(','))
        self.assertTrue(selectors)
        for selector in selectors:
            self.assertTrue(selector.startswith('body.mobile-shell'), selector)

    def test_layer_is_loaded_by_the_section(self):
        self.assertIn("import './shift-auction-mobile.css';", VIEW)

    def test_colours_are_utilities_not_hex(self):
        """Тёмный слой портала перекрашивает утилиты и переменные листов, а hex
        из файла стилей раздела он не видит — полоса дней осталась бы светлой."""
        self.assertIsNone(re.search(r'#[0-9a-fA-F]{3,8}\b', strip_comments(CSS)))

    def test_sticky_strip_sits_under_the_top_glass(self):
        """Полоса дней липнет ровно под стеклом шапки: к нулю — ушла бы под
        колокол. Число общее с полем .main-content — разойдясь, они дадут либо
        щель, либо наезд."""
        start = CSS.index('body.mobile-shell .sa-m-root .sa-m-strip {')
        block = CSS[start:CSS.index('}', start)]
        self.assertIn('position: sticky;', block)
        self.assertIn('top: calc(46px + max(10px, env(safe-area-inset-top)));', block)
        self.assertIn('padding-top: calc(46px + max(10px, env(safe-area-inset-top))) !important;', SHELL_CSS)

    def test_markup_avoids_the_traps_of_the_shared_section_layer(self):
        """Общий слой разделов обнуляет min-w-[…], переносит .flex-nowrap и
        ряды с «-actions/-tabs/-filters/toolbar/topbar» в имени класса, ставит
        колонкой .flex.items-start с .flex-1 внутри и переписывает -mx-*."""
        # Сами ловушки перечислены в комментарии файла — проверяем код без него.
        code = re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', PHONE, flags=re.S))
        for trap in ('min-w-[', 'flex-nowrap', ' -mx-', 'items-start'):
            self.assertNotIn(trap, code, trap)
        self.assertIsNone(re.search(r'className="[^"]*(?:-actions|-tabs|-filters|toolbar|topbar)', code))


class PhoneBranchTests(unittest.TestCase):
    def test_phone_is_decided_by_the_single_shell_query(self):
        self.assertIn('const isMobileShell = useIsMobileShell();', VIEW)
        self.assertNotIn('matchMedia', PHONE)

    def test_desktop_markup_is_branched_not_rewritten(self):
        """На телефоне сетка, полоса дней у нижнего края и карточка дня не
        монтируются вовсе: сетка на неделю — это сотни узлов впустую."""
        for marker in (
            '{isMobileShell ? renderPhoneTop() : (<>',
            '{canMonitor && !isMobileShell && (',
            "{!isMobileShell && canUseAuction && (!canMonitor || monitorTab === 'monitoring') && (",
            "{!isMobileShell && canUseAuction && (!canMonitor || monitorTab === 'monitoring') && availablePeriods.length ? (",
        ):
            self.assertIn(marker, VIEW)
        for name in ('shiftDetailData', 'addShiftTarget', 'selfScheduleDate', 'drilldownData', 'releaseConfirmLot'):
            self.assertIn('{%s && !isMobileShell ? (' % name, VIEW)
        self.assertIn('{isMobileShell ? renderPhoneScreens() : null}', VIEW)

    def test_phone_rows_use_the_same_rules_as_the_grid(self):
        self.assertIn('classifyAuctionLotForPhone({', VIEW)
        self.assertIn("claimBlockReasonByLotId.get(lotKey) || ''", VIEW)
        self.assertIn('postAuctionClaimOptionsByLotId.get(lotKey)', VIEW)

    def test_shared_day_reason_is_said_once(self):
        """«На этот день уже выбрана смена» под каждой из восьми смен дня —
        шум: одна причина на весь день говорится под его заголовком."""
        self.assertIn('row.reason !== dayReason ? row.reason : null', VIEW)
        self.assertIn('dayBlockedReasons.size === 1', VIEW)

    def test_stuck_strip_extends_its_glass_only_when_stuck(self):
        """Продолжение стекла вверх — только у прилипшей полосы: в потоке оно
        накрыло бы карточку статуса над ней."""
        self.assertIn("data-stuck={stuck ? '' : undefined}", PHONE)
        self.assertIn('IntersectionObserver', PHONE)
        self.assertIn('.sa-m-strip[data-stuck] .sa-m-strip__glass', CSS)
        # Фильтр — у слоя, а не у полосы: элемент с backdrop-filter — корень
        # подложки для потомков, и продолжение стекла размывало бы пустоту.
        start = CSS.index('body.mobile-shell .sa-m-root .sa-m-strip {')
        self.assertNotIn('backdrop-filter', CSS[start:CSS.index('}', start)])

    def test_own_shift_is_listed_once(self):
        """Своя смена стоит группой «Мои смены» с кнопкой «Вернуть» и не
        повторяется строкой ниже, в списке ставок."""
        self.assertIn(
            "if (!canMonitor && lot.status === 'claimed' && getMyAuctionClaimEntry(lot, user?.id)) return;",
            VIEW,
        )
        self.assertIn('openReleaseConfirm([mine.claimLot || mine.lot])', VIEW)

    def test_return_is_confirmed_by_a_sheet_naming_the_shift(self):
        self.assertIn('<MobileActionSheet', VIEW)
        self.assertIn('onClick: () => handleReleaseLot(option)', VIEW)
        self.assertIn('const lot = lotOverride && lotOverride.id ? lotOverride : releaseConfirmLot;', VIEW)

    def test_screens_refuse_to_close_while_saving(self):
        """Жест «назад» посреди сохранения закрыл бы экран, а запрос остался бы
        висеть без места, куда показать его итог."""
        self.assertIn("onClose={() => (isSelfScheduling ? false : setSelfScheduleDate(''))}", VIEW)
        self.assertIn('onClose={() => (isAddingShift ? false : setAddShiftTarget(null))}', VIEW)
        self.assertIn('onClose={() => (inProgress ? false : onClose())}', VIEW)

    def test_phone_instruction_describes_the_phone(self):
        steps = VIEW[VIEW.index('const OPERATOR_PHONE_INSTRUCTION_STEPS'):VIEW.index('const ADMIN_INSTRUCTION_STEPS')]
        for word in ('Выходной', 'Взять', 'Вернуть', 'Добрать', 'Мои доп. смены'):
            self.assertIn(word, steps)
        for desktop_only in ('нижней панели', 'левой панели', 'правом верхнем углу', 'Кликните'):
            self.assertNotIn(desktop_only, steps)
        self.assertIn('isMobileShell ? OPERATOR_PHONE_INSTRUCTION_STEPS : OPERATOR_INSTRUCTION_STEPS', VIEW)


if __name__ == '__main__':
    unittest.main()
