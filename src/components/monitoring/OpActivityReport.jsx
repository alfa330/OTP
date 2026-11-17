import React, { useCallback } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import FullscreenSheet from '../common/FullscreenSheet';
import { SegmentedSwitch } from './SzovWallboardTiles';
import { OP_WALLBOARD_VIEWS } from './opWallboardShared';
import OpActivityJournalPanel from './OpActivityJournal';
import { CHAT_SHIFT_JOURNAL, CHAT_SHIFT_REPORT } from './OpChatShiftReport';
import {
    EASE_APPEAR,
    MissingMoment,
    ShiftReportBody,
    ShiftReportExcelButton,
    useShiftReport,
    zeroTone,
} from './opShiftReportParts';
import {
    OP_ACTIVITY_EXPORT_PATH,
    OP_ACTIVITY_PATH,
    exportFileName,
    formatClockHms,
    formatDayRu,
    formatHms,
    opActivityColumns,
    pendingCallsNotice,
} from './opActivityShared';

/*
 * «Отчёт по сменам» табло ОП — полноэкранное окно, которое открывает кнопка «Отчёт по сменам» в
 * шапке табло. Не вкладка «Линия / Чат» и не отдельный раздел меню (оба варианта владелец отклонил
 * 19.10.2026): стена живёт «сейчас», а здесь — закончившиеся смены за месяц, поверх стены и с меню
 * сайта слева, закрытие — крестиком или Esc.
 *
 * Отчётов два, переключатель «Линия / Чат» — в шапке окна, как у самого табло:
 *   * «Линия» — «Активность операторов» (ТЗ #392): смены операторов Основы, ЯР и Потока, статусы
 *     iCORE Phone и звонки станции;
 *   * «Чат» — «Отчёт по сменам операторов чата» (SALES-1, #424): смены верификаторов по кнопке смены
 *     «Чатов ОП» и переписка Wazzup (OpChatShiftReport.jsx).
 * Окно открывается на том направлении, на котором стоит табло. У каждого отчёта свои фильтры,
 * страница и журнал; ушедший с экрана их помнит, а отчёт, на который не переключались, сервер не
 * спрашивает вовсе. Общие части обоих — opShiftReportParts.jsx.
 *
 * Цвет — только со смыслом: нули светло-серые, выход без события — серым с пунктиром, остальное
 * нейтральное. Как считается каждая колонка — под «i» у её заголовка.
 */

/* Ячейки строки линии — в порядке колонок ТЗ #392. */
const lineCells = (row, { nameCell }) => {
    const cell = 'px-2.5 py-2.5 tabular-nums whitespace-nowrap';
    return (
        <>
            <td className={`${cell} text-slate-600`}>{formatDayRu(row.date)}</td>
            {nameCell}
            <td className="whitespace-nowrap px-2.5 py-2.5 text-slate-500">{row.group_label}</td>
            <td className={`${cell} text-slate-700`}>{formatClockHms(row.login_at)}</td>
            <td className={`${cell} text-right ${row.avg_talk_seconds ? 'text-slate-700' : 'text-slate-300'}`}>{formatHms(row.avg_talk_seconds)}</td>
            <td className={`${cell} text-right ${zeroTone(row.talk_seconds)}`}>{formatHms(row.talk_seconds)}</td>
            <td className={`${cell} text-right ${zeroTone(row.active_seconds)}`}>{formatHms(row.active_seconds)}</td>
            <td className={`${cell} text-right ${zeroTone(row.waiting_seconds)}`}>{formatHms(row.waiting_seconds)}</td>
            <td className={`${cell} text-right ${zeroTone(row.break_seconds)}`}>{formatHms(row.break_seconds)}</td>
            <td className={`${cell} text-right ${zeroTone(row.tech_seconds)}`}>{formatHms(row.tech_seconds)}</td>
            <td className={`${cell} text-right ${zeroTone(row.calls)}`}>{row.calls}</td>
            <td className={`${cell} text-right ${zeroTone(row.connected)}`}>{row.connected}</td>
            <td className={`${cell} ${row.logout_missing ? 'text-slate-400' : 'text-slate-700'}`}>
                <MissingMoment missing={row.logout_missing}
                               title={row.logout_missing ? 'Телефон не прислал выход — показано последнее событие смены' : undefined}>
                    {formatClockHms(row.logout_at)}
                </MissingMoment>
                {row.logout_next_day ? <sup className="ml-0.5 text-[10px] font-semibold text-slate-400" title="Следующего дня">+1</sup> : null}
            </td>
            <td className={`${cell} text-right font-medium ${row.worked_seconds ? 'text-slate-900' : 'text-slate-300'}`}>{formatHms(row.worked_seconds)}</td>
        </>
    );
};

const LINE_REPORT = {
    key: 'line',
    path: OP_ACTIVITY_PATH,
    exportPath: OP_ACTIVITY_EXPORT_PATH,
    exportFileName,
    columns: (report) => opActivityColumns(report?.talk_min_seconds),
    renderCells: lineCells,
    groupFilter: true,
    filtersGrid: 'xl:grid-cols-[minmax(230px,auto)_auto_minmax(220px,1.3fr)_minmax(190px,1fr)_auto]',
    pendingNotice: pendingCallsNotice,
    icon: 'fa-clock-rotate-left',
    title: 'Активность операторов',
    subtitle: 'Завершённые смены отдела продаж: статусы iCORE Phone и звонки станции',
    emptyTitle: 'За выбранные дни завершённых смен нет',
    emptyHint: 'Смена появляется в отчёте, когда оператор выходит из iCORE Phone',
};

const REPORTS = { line: LINE_REPORT, chat: CHAT_SHIFT_REPORT };

export default function OpActivityReportSheet({
    open, onClose, direction = 'line', onDirectionChange, user, apiBaseUrl, withAccessTokenHeader, showToast, z = 150,
}) {
    const shown = direction === 'chat' ? 'chat' : 'line';
    const config = REPORTS[shown];
    const shared = { apiBaseUrl, withAccessTokenHeader, userId: user?.id ?? null, showToast };
    const line = useShiftReport(LINE_REPORT, { ...shared, active: open && shown === 'line' });
    const chat = useShiftReport(CHAT_SHIFT_REPORT, { ...shared, active: open && shown === 'chat' });
    const ctl = shown === 'chat' ? chat : line;
    const journalShift = ctl.journalShift;
    const reduceMotion = useReducedMotion();
    const closeLineJournal = line.closeJournal;
    const closeChatJournal = chat.closeJournal;

    // Окно закрывают — журнал уходит вместе с ним, а не остаётся висеть над стеной.
    const closeSheet = useCallback(() => {
        closeLineJournal();
        closeChatJournal();
        onClose?.();
    }, [closeLineJournal, closeChatJournal, onClose]);
    // Другой отчёт — журнал прежнего закрывается: он про смену, которой на экране больше нет.
    const switchReport = useCallback((next) => {
        closeLineJournal();
        closeChatJournal();
        onDirectionChange?.(next);
    }, [closeLineJournal, closeChatJournal, onDirectionChange]);

    const actions = (
        <>
            {onDirectionChange ? <SegmentedSwitch value={shown} options={OP_WALLBOARD_VIEWS} onChange={switchReport} /> : null}
            <ShiftReportExcelButton ctl={ctl} />
        </>
    );

    // Окно — в корень страницы, как «На стену» у табло: внутри столбика `space-y-*` раздела
    // fixed-окно получило бы его верхний отступ и встало бы на 20 px ниже края.
    return (
        <>
        {createPortal(<FullscreenSheet
            open={open}
            onClose={closeSheet}
            wide
            z={z}
            icon={config.icon}
            title={config.title}
            subtitle={config.subtitle}
            actions={actions}
            // Esc при открытом журнале закрывает журнал, а не всё окно разом.
            closeOnEscape={!journalShift}
            // Окно встаёт в области раздела: меню сайта слева остаётся видно и работает.
            offsetLeft="var(--app-sidebar-offset, 0px)"
        >
            {/* Другой отчёт проявляется на месте прежнего, а не подменяет его рывком. */}
            <AnimatePresence mode="wait" initial={false}>
                <motion.div
                    key={shown}
                    initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0, transition: { duration: 0.26, ease: EASE_APPEAR } }}
                    exit={{ opacity: 0, transition: { duration: 0.12 } }}
                >
                    <ShiftReportBody ctl={ctl} config={config} />
                </motion.div>
            </AnimatePresence>
        </FullscreenSheet>, document.body)}
        <OpActivityJournalPanel
            shift={open && shown === 'line' ? line.journalShift : null}
            apiBaseUrl={apiBaseUrl}
            withAccessTokenHeader={withAccessTokenHeader}
            onClose={line.closeJournal}
            z={z + 10}
        />
        <OpActivityJournalPanel
            shift={open && shown === 'chat' ? chat.journalShift : null}
            config={CHAT_SHIFT_JOURNAL}
            apiBaseUrl={apiBaseUrl}
            withAccessTokenHeader={withAccessTokenHeader}
            onClose={chat.closeJournal}
            z={z + 10}
        />
        </>
    );
}
