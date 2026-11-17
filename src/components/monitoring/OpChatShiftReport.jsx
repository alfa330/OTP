import React from 'react';
import { MissingMoment, zeroTone } from './opShiftReportParts';
import { formatClockHms, formatDayRu, formatHms } from './opActivityShared';
import {
    OP_CHAT_SHIFTS_EXPORT_PATH,
    OP_CHAT_SHIFTS_JOURNAL_PATH,
    OP_CHAT_SHIFTS_PATH,
    OP_CHAT_SHIFT_COLUMNS,
    chatJournalFilterChips,
    chatJournalQuery,
    chatJournalStyle,
    chatLogoutTitle,
    chatShiftsExportFileName,
    filterChatJournal,
} from './opChatShiftsShared';

/*
 * «Отчёт по сменам операторов чата» (SALES-1, #424) — второй отчёт окна «Отчёт по сменам» табло ОП,
 * переключатель «Линия / Чат» в шапке окна. Устроен как отчёт линии (общие части —
 * opShiftReportParts.jsx): месяц, операторы и дни, таблица по 50 строк, журнал смены, Excel.
 * Отличаются колонки — 14 колонок ТЗ его словами — и источник: смены верификаторов по кнопке смены
 * «Чатов ОП» и переписка Wazzup (op_wallboard/chat_shifts.py). Группа у отчёта одна — «Верификаторы»,
 * поэтому фильтра группы нет: сегмент из одного варианта был бы шумом, а колонка «Группа» — по ТЗ.
 */

const chatCells = (row, { nameCell }) => {
    const cell = 'px-2.5 py-2.5 tabular-nums whitespace-nowrap';
    return (
        <>
            <td className={`${cell} text-slate-600`}>{formatDayRu(row.date)}</td>
            {nameCell}
            <td className="whitespace-nowrap px-2.5 py-2.5 text-slate-500">{row.group_label}</td>
            <td className={`${cell} ${row.login_missing ? 'text-slate-400' : 'text-slate-700'}`}>
                <MissingMoment missing={row.login_missing}
                               title={row.login_missing ? 'Прошлую смену не закрыли — показано первое сообщение после перерыва в работе' : undefined}>
                    {formatClockHms(row.login_at)}
                </MissingMoment>
            </td>
            <td className={`${cell} text-right ${row.first_reply_seconds == null ? 'text-slate-300' : 'text-slate-700'}`}>{formatHms(row.first_reply_seconds)}</td>
            <td className={`${cell} text-right ${zeroTone(row.handling_seconds)}`}>{formatHms(row.handling_seconds)}</td>
            <td className={`${cell} text-right ${zeroTone(row.active_seconds)}`}>{formatHms(row.active_seconds)}</td>
            <td className={`${cell} text-right ${zeroTone(row.waiting_seconds)}`}>{formatHms(row.waiting_seconds)}</td>
            <td className={`${cell} text-right ${zeroTone(row.break_seconds)}`}>{formatHms(row.break_seconds)}</td>
            <td className={`${cell} text-right ${zeroTone(row.tech_seconds)}`}>{formatHms(row.tech_seconds)}</td>
            <td className={`${cell} text-right ${zeroTone(row.taken_chats)}`}>{row.taken_chats}</td>
            <td className={`${cell} text-right ${zeroTone(row.processed_chats)}`}>{row.processed_chats}</td>
            <td className={`${cell} ${row.logout_missing ? 'text-slate-400' : 'text-slate-700'}`}>
                <MissingMoment missing={row.logout_missing} title={chatLogoutTitle(row)}>
                    {formatClockHms(row.logout_at)}
                </MissingMoment>
                {row.logout_next_day ? <sup className="ml-0.5 text-[10px] font-semibold text-slate-400" title="Следующего дня">+1</sup> : null}
            </td>
            <td className={`${cell} text-right font-medium ${row.worked_seconds ? 'text-slate-900' : 'text-slate-300'}`}>{formatHms(row.worked_seconds)}</td>
        </>
    );
};

export const CHAT_SHIFT_REPORT = {
    key: 'chat',
    path: OP_CHAT_SHIFTS_PATH,
    exportPath: OP_CHAT_SHIFTS_EXPORT_PATH,
    exportFileName: chatShiftsExportFileName,
    columns: () => OP_CHAT_SHIFT_COLUMNS,
    renderCells: chatCells,
    groupFilter: false,
    filtersGrid: 'xl:grid-cols-[minmax(230px,auto)_minmax(220px,1.3fr)_minmax(190px,1fr)_auto]',
    pendingNotice: null,
    icon: 'fa-comments',
    title: 'Смены операторов чата',
    subtitle: 'Завершённые смены верификаторов: статусы «Чатов ОП» и переписка Wazzup',
    emptyTitle: 'За выбранные дни завершённых смен нет',
    emptyHint: 'Смена появляется в отчёте, когда верификатор заканчивает её в «Чатах ОП»',
};

/* Журнал смены чата — та же панель, что у линии (OpActivityJournal.jsx), со своими статусами. */
export const CHAT_SHIFT_JOURNAL = {
    path: OP_CHAT_SHIFTS_JOURNAL_PATH,
    query: chatJournalQuery,
    chips: chatJournalFilterChips,
    filter: filterChatJournal,
    style: chatJournalStyle,
    title: 'Журнал смены',
    emptyText: 'В этой смене нет статусов.',
    notes: (data, shift) => [
        data?.login_missing ? `Прошлую смену не закрыли: вход — первое сообщение в ${formatClockHms(shift?.login_at)}.` : null,
        data?.logout_missing ? `Смену не закрыли: она показана до последнего действия в ${formatClockHms(shift?.logout_at)}.` : null,
    ].filter(Boolean),
};
