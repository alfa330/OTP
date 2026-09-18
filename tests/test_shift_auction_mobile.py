"""«Аукцион смен» на телефоне: сетка «ставки × дни», как на сайте, собрана под
палец — неделя в ширину экрана, смена — ячейка, шапка дней и часы оператора
липнут сверху; окна стали экранами портала.

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
        """«На этот день уже выбрана смена» у каждой из восьми серых ячеек —
        шум: общая причина дня — одна строка в экране дня, причина отдельной
        ячейки — в её листе."""
        self.assertIn('dayBlockedReasons.size === 1', VIEW)
        self.assertIn('{dayReason ? <p className="px-1 text-[15px] leading-snug text-slate-500">{dayReason}</p> : null}', VIEW)
        self.assertIn('cellState = cellRow.reason;', VIEW)

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

    def test_own_shift_is_green_like_on_the_site(self):
        """Своя смена в сетке — зелёная ячейка, как на сайте; нажатие на неё
        открывает лист «Вернуть смену», а в экране дня она в «Моих сменах».
        Смена, в которой мой только КУСОК, красится тем же зелёным — см.
        PhonePartClaimReleaseTests."""
        self.assertIn('if (row.kind === K.MINE || row.kind === K.MINE_PART) {', VIEW)
        self.assertIn("return { className: 'border-emerald-600 bg-emerald-600 text-white' };", VIEW)
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


class PhoneGridTests(unittest.TestCase):
    """Владелец 15.09.2026: «сделай как на сайте, чтобы смены отображались
    ячейками, сделай удобно и аккуратно»; до этого — «норма за неделю должна
    быть закреплена у оператора или у чатника», «таймлайны не убирай»,
    «применить только к мобильной версии»."""

    @staticmethod
    def grid_block():
        return VIEW[VIEW.index('const getPhoneLotRow = (lot) => {'):VIEW.index('const renderPhoneScreens = () => {')]

    def test_week_is_a_grid_of_cells_like_the_site(self):
        self.assertIn('<AuctionPhoneGrid', VIEW)
        self.assertIn('buildAuctionPhoneGridRows(group.lotsByDate, lotDates)', VIEW)
        for gone in ('renderPhoneWeek', 'renderPhoneDay()', 'AuctionPhoneWeek'):
            self.assertNotIn(gone, VIEW + PHONE, gone)
        # Неделя — ровно в ширину экрана, без боковой прокрутки.
        self.assertIn('`repeat(${days.length}, minmax(0, 1fr))`', PHONE)
        self.assertIn('const GRID_MAX_FIT_DAYS = 7;', PHONE)

    def test_cell_colours_come_from_the_site_scale(self):
        block = self.grid_block()
        for helper in ('getAuctionLotStartTone(lot)', 'getAuctionLotPostAuctionTone(lot)', 'getAuctionLotPhoneTone(lot)'):
            self.assertIn(helper, block)
        # Фиолетовый добавленной смены — одна константа у сайта и у телефона.
        self.assertIn('const addedToneStyle = AUCTION_ADDED_LOT_TONE;', VIEW)
        self.assertIn('return { style: AUCTION_ADDED_LOT_TONE };', block)

    def test_tap_on_a_cell_goes_through_one_decision(self):
        """Что откроет нажатие, решает проверенный модуль, а не разметка: линия —
        лист с «Взять», чат и добор — экран выбора, своя смена — «Вернуть»."""
        self.assertIn('pickAuctionPhoneCellAction({ kind: row.kind, canManage: canMonitor, supportsPartialClaim, releasable })', VIEW)
        self.assertIn('label: `Взять смену ${cellStart}–${cellEnd}`', VIEW)

    def test_norm_is_pinned_above_the_days(self):
        self.assertIn('header={normBar}', self.grid_block())
        self.assertIn('const normBar = phoneWorkload ? <AuctionPhoneNormBar {...phoneWorkload} /> : null;', VIEW)
        grid = PHONE[PHONE.index('export const AuctionPhoneGrid'):]
        self.assertIn('{header}', grid)
        self.assertIn("data-stuck={stuck ? '' : undefined}", grid)
        self.assertIn('useAuctionPhoneStuck(stripRef, hasDays)', grid)
        # Часы на экране одни: в карточке статуса их больше нет.
        card = VIEW[VIEW.index('<AuctionPhoneStatusCard'):]
        self.assertNotIn('workload', card[:card.index('/>')])
        card_component = PHONE[PHONE.index('export const AuctionPhoneStatusCard'):PHONE.index('const DAY_CAPTION_TONES')]
        self.assertNotIn('workload', card_component)

    def test_top_glass_is_no_taller_than_the_bell_row(self):
        """Владелец 15.09.2026: «прозрачная чёлка сверху слишком большая,
        укороти её». Часы оператора стоят в строке колокола, у выреза экрана, а
        не под пустой полосой в 46 px; колокол не накрывает их правым полем."""
        grid = PHONE[PHONE.index('export const AuctionPhoneGrid'):]
        self.assertIn("className={header ? 'sa-m-strip sa-m-strip--bell' : 'sa-m-strip'}", grid)
        start = CSS.index('body.mobile-shell .sa-m-root .sa-m-strip.sa-m-strip--bell {')
        self.assertIn('top: max(10px, env(safe-area-inset-top));', CSS[start:CSS.index('}', start)])
        self.assertIn('.sa-m-strip.sa-m-strip--bell[data-stuck] .sa-m-strip__glass', CSS)
        self.assertIn('.sa-m-strip.sa-m-strip--bell[data-stuck] .sa-m-norm', CSS)
        # Строка часов ровно в рост колокола: ниже — дни заехали бы под него.
        self.assertIn('className="sa-m-norm flex h-11 items-center gap-3 px-4"', PHONE)

    def test_timelines_stay_on_the_phone(self):
        """«таймлайны не убирай»: лента суток в экране дня, лента выбора части
        смены и лента карточки смены у руководителя."""
        self.assertIn("import { MyShiftsTimeline } from '../schedule/MyShiftsMobile';", VIEW)
        self.assertIn('<MyShiftsTimeline parts={timelineParts}', VIEW)
        self.assertIn('buildAuctionPhoneDayTimelineParts({ date: dayDate, entries: timelineEntries })', VIEW)
        self.assertIn('key={`phone-available-${segment.start}-${segment.end}`}', VIEW)
        self.assertIn('key={`phone-detail-bar-${si}`}', VIEW)

    def test_day_header_opens_the_day(self):
        self.assertIn('onDaySelect={openPhoneDay}', VIEW)
        # Та же карточка дня, что на компьютере, но экраном — и только на телефоне.
        self.assertIn('const dayOpen = Boolean(isMobileShell && isDayDetailsOpen && dayItem);', VIEW)

    def test_day_off_quota_is_said_once(self):
        self.assertNotIn('Выходных на период', self.grid_block())
        self.assertEqual(VIEW.count('Выходных на период — до {dayOffQuota}'), 1)

    def test_only_the_phone_gets_the_grid(self):
        self.assertIn(
            "{isMobileShell && canUseAuction && (!canMonitor || monitorTab === 'monitoring') ? renderPhoneGrid() : null}",
            VIEW,
        )
        self.assertEqual(VIEW.count('renderPhoneGrid()'), 1)


class PhoneManagerTabsTests(unittest.TestCase):
    """Владелец 17.09.2026: «сделать адаптив под разделы … аукцион смен тоже»,
    «на данный момент пусть ячейки в аукционе смен останутся такими же». Сетка
    ячеек не тронута; телефонными стали вкладки руководителя «Настройки»,
    «Таблица», «Прогресс» и «Журнал» — до этого на телефоне стояла их настольная
    разметка."""

    def test_tabs_are_a_strip_not_a_squeezed_segmented_control(self):
        """Пять вкладок со счётчиками в сегментный переключатель на 358 px не
        помещались: «Прогресс 3Журнал 16»."""
        tabs = VIEW[VIEW.index('const renderPhoneTabs = () => ('):VIEW.index('const renderPhoneProgress = () => (')]
        self.assertIn('<AuctionPhoneTabs', tabs)
        self.assertNotIn('IosSegmented', tabs)
        self.assertIn('export const AuctionPhoneTabs', PHONE)

    def test_manager_tabs_branch_on_the_phone(self):
        for tab, render, guard in (
            ('progress', 'renderPhoneProgress()', 'canMonitor'),
            ('settings', 'renderPhoneSettings()', 'canManage'),
            ('journal', 'renderPhoneJournal()', 'canMonitor'),
        ):
            self.assertIn("{%s && monitorTab === '%s' && isMobileShell ? %s : null}" % (guard, tab, render), VIEW)
            self.assertIn("{%s && monitorTab === '%s' && !isMobileShell && (" % (guard, tab), VIEW)

    def test_shifts_table_keeps_its_actions_on_the_phone(self):
        """Таблица смен на телефоне — день лентой, операторы списком, ячейка
        экраном; назначают и снимают смену те же handleClaim/handleUnclaim с
        историей отмены, снятие спрашивает листом."""
        table = VIEW[VIEW.index('const ShiftAuctionShiftsTable = ('):VIEW.index('const ShiftAuctionView = (')]
        phone = table[table.index('  if (isMobileShell) {'):table.index('<section className="rounded-lg border border-slate-200 bg-white shadow-sm">\n      <div className="border-b')]
        for marker in ('handleClaim(lot, phoneCell.operator.id)', 'await handleUnclaim(lot);', 'onClick={performUndo}',
                       'onClick={performRedo}', '<MobileActionSheet', "label: 'Убрать смену'", '<AuctionPhoneChips'):
            self.assertIn(marker, phone, marker)
        self.assertNotIn('fixed inset-0', phone)
        # Хуки телефона объявлены до раннего выхода «нет смен».
        self.assertLess(table.index('const isMobileShell = useIsMobileShell();'), table.index('if (!rows.length || !dates.length) {'))

    def test_progress_and_journal_open_the_operator_screen(self):
        progress = VIEW[VIEW.index('const renderPhoneProgress = () => ('):VIEW.index('const renderPhoneJournal = () => {')]
        journal = VIEW[VIEW.index('const renderPhoneJournal = () => {'):VIEW.index('const renderPhoneSettings = () => {')]
        self.assertIn('setDrilldownOperatorId(Number(row.operator_id))', progress)
        self.assertIn('setOperatorWorkloadFilter', progress)
        self.assertIn('<AuctionPhoneSearch value={operatorWorkloadQuery}', progress)
        self.assertIn('setDrilldownOperatorId(Number(entry.claimed_by))', journal)
        self.assertIn('fetchJournalPage(journalPage + 1)', journal)

    def test_settings_use_the_desktop_rules_and_requests(self):
        settings = VIEW[VIEW.index('const renderPhoneSettings = () => {'):VIEW.index('const renderPhonePeriods = () => {')]
        for handler in ('handleSave', 'handleRestartAuction', 'handlePublishAuction', 'handleExportAuctionReport',
                        "handleAuctionControl('pause')", "handleAuctionControl('finish')", 'handleToggleTopup',
                        'handleToggleRateLock', 'updateDraftEnabled', 'updateDraftSchedulePlanId', 'addDraftTimeGroup',
                        'patchDraftTimeGroup', 'removeDraftTimeGroup', 'toggleTimeGroupMember', 'toggleOperator',
                        'selectAllFilteredOperators', 'clearSelectedOperators', '<AuctionRangeCalendar'):
            self.assertIn(handler, settings, handler)
        self.assertNotIn('axios.', settings)
        self.assertIn('title="Участники"', settings)
        self.assertIn('className={`${iosCard} sa-m-window p-3`}', settings)
        self.assertIn('.sa-m-window > div', CSS)


class PhonePartClaimReleaseTests(unittest.TestCase):
    """Владелец 18.09.2026: «нужно исправить возврат смены во время аукциона
    смен — чат… данная логика перестала работать, кроме добора».

    Взятая В ХОДЕ аукциона часть смены не закрывает лот: он остаётся
    `available` с пустым `claimed_by`, иначе остаток пропал бы у остальных. В
    сетке недели такая смена поэтому выглядела свободной — нажатие звало взять
    её ещё раз, а «Вернуть» жило только на экране дня, куда без подсказки не
    заходят. Правило веток — в tests/shift_auction_phone_rows.test.mjs, здесь —
    что разметка его слушает и что лист делает.
    """

    @staticmethod
    def screens_block():
        return VIEW[VIEW.index('const renderPhoneScreens = () => {'):VIEW.index('const dayDate = activeDayDate;')]

    def test_cell_of_my_part_is_mine_not_free(self):
        grid = PhoneGridTests.grid_block()
        # Цвет — тот же, что у взятой целиком: смена с моим куском такая же моя.
        self.assertIn('if (row.kind === K.MINE || row.kind === K.MINE_PART) {', grid)
        self.assertIn("[AUCTION_PHONE_ROW_KIND.MINE_PART]: 'ваша часть смены'", VIEW)
        # Время в ячейке — МОЁ окно: у смены, закрытой коллегой, post_claim_*
        # хранит его время, а не моё.
        self.assertIn('if (row?.kind === AUCTION_PHONE_ROW_KIND.MINE_PART && myClaim?.claimLot) {', grid)

    def test_my_parts_are_collected_for_the_cell(self):
        grid = PhoneGridTests.grid_block()
        self.assertIn(
            'row.kind === AUCTION_PHONE_ROW_KIND.MINE || row.kind === AUCTION_PHONE_ROW_KIND.MINE_PART',
            grid,
        )
        # Своих кусков в одной смене бывает несколько — берём список целиком.
        self.assertIn('const mine = mineRows[0] || null;', grid)
        self.assertIn('else if (action === AUCTION_PHONE_CELL_ACTION.PART) setPhoneCellKey(lotKey);', grid)

    def test_sheet_returns_the_part_and_lets_take_the_rest(self):
        screens = self.screens_block()
        self.assertIn('label: `Вернуть часть ${formatAuctionLotEffectiveTimeRangeLabel(mine.claimLot)}`', screens)
        self.assertIn('handleReleaseLot(mine.claimLot)', screens)
        # Возврат уходит по нажатию, поэтому лист обязан сказать последствие.
        self.assertIn('Вернёте — свободным станет только ваш кусок', screens)
        # Смену не обязательно отдавать целиком: остаток можно добрать отсюда же.
        self.assertIn('label: `Взять ещё ${cellFreeSegment.start_time}–${cellFreeSegment.end_time}`', screens)
        self.assertIn("disabled: releasingLotId !== null", screens)

    def test_hours_and_breaks_of_a_part_are_counted_by_the_part(self):
        screens = self.screens_block()
        self.assertIn('const cellFactsLot = cellIsMyPart && cellMineRows[0] ? cellMineRows[0].claimLot : cellLot;', screens)
        self.assertIn('getAuctionLotNetMinutes(cellFactsLot)', screens)
        self.assertIn('getAuctionLotBreakMinutes(cellFactsLot)', screens)
        # Перерыв вне взятого окна — не мой: подпись обязана совпасть с часами.
        label = VIEW[VIEW.index('const formatAuctionBreaksLabel = (lot) => {'):]
        label = label[:label.index('const getAuctionLotDurationMinutes')]
        self.assertIn('const activeRange = getAuctionLotEffectiveMinuteRange(lot);', label)
        self.assertIn('return start < activeRange[1] && activeRange[0] < end;', label)


if __name__ == '__main__':
    unittest.main()
