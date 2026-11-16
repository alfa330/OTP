import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import axios from 'axios';
import { AlertCircle, ChevronDown, ChevronUp, Download, Loader2, RefreshCw, Search, X } from 'lucide-react';
import { APPLE_FONT, IosHint, IosSegmented, iosBtnGhost, iosCard, iosInput } from '../ui/ios';
import { IosDateRangePicker } from '../ui/DateRangePicker';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import { statusDotClass } from './workspaceStatus';
import {
    DEFAULT_NORMS, DEFAULT_SORT, GROUP_ALL, MAX_DAYS_FALLBACK, PERIOD_PRESETS, RATE_MIN_ACTIVE_SECONDS,
    ROW_ABSENT, ROW_NO_SHIFT, ROW_SHIFT, SHIFT_REPORT_PATH, VIEW_PEOPLE, VIEW_SHIFTS, activePreset,
    clampRange, clock, csvFileName, earliestDay, endNote, filterRows, firstReply, formatDay,
    formatDuration, formatRate, formatReply, groupOptions, hourBars, innerReply, isoDay, journalBar,
    journalSegments, journalTotals, minutesLabel, nextSort, offlineNote, overNorm, peopleColumns,
    peopleCsv, peopleRows, planLabel, presetRange, shiftColumns, shiftCsv, sortRows, spanOf, sumRows,
    unlinkedText,
} from './shiftReportShared';

/*
 * «Операторы → Смены» раздела «Чаты ОП» (просьба владельца 16.11.2026: «у кого какая смена была и
 * кто как обрабатывал чаты, интенсивность»). Строка — смена менеджера по кнопке смены раздела:
 * график, фактические начало и конец, время в статусах, чаты, сообщения, чатов в час и время
 * ответа по правилам «Табло ОП · Чат». «По сотрудникам» — то же одной строкой на человека за
 * период. Нажатие на смену открывает её журнал справа: статусы подряд и нагрузку по часам.
 *
 * Цвет — только со смыслом: опоздание, ранний уход, «не вышел» и ответ дольше нормы — красным;
 * нули — серым прочерком; остальное нейтральное. Как считается колонка — под «i» у заголовка.
 * Расчёт на сервере (wazzup/shift_report.py), фильтры и итоги — здесь (shiftReportShared.js).
 */

const SORTABLE_KEYS = new Set(['date', 'name', 'plan', 'shift', 'active', 'breakTime', 'training',
    'chats', 'messages', 'chatsPerHour', 'firstReply', 'innerReply', 'shifts']);

const Dash = () => <span className="text-slate-300">—</span>;

const durationCell = (seconds) => (Number(seconds) > 0 ? formatDuration(seconds) : <Dash />);

const CELL = 'px-2.5 py-2';
const NUM = `${CELL} whitespace-nowrap text-right tabular-nums`;

/* Чаты и под ними сообщения: обе цифры нужны рядом (сколько разговоров и насколько плотных), а
   отдельная колонка сообщений не давала таблице поместиться рядом с меню сайта. */
function ChatsCell({ chats, messages }) {
    // Ноль — тоже ответ: час в «Активном» без единого чата виден сразу, а не прочерком.
    return (
        <>
            <div className="font-semibold text-slate-900">{chats || 0}</div>
            {messages > 0 && <div className="text-[11px] text-slate-400">{messages} сообщ.</div>}
        </>
    );
}

function ReplyCell({ seconds, norm }) {
    if (seconds === null || seconds === undefined) return <Dash />;
    return <span className={overNorm(seconds, norm) ? 'text-rose-600' : 'text-slate-900'}>{formatReply(seconds)}</span>;
}

/* Заголовок: подпись (в две строки, если узко) и «i» за ней. Стрелка — только у колонки, по
   которой отсортировано: как в Finder, остальные сортируются нажатием на подпись. Подсказка
   живёт вне прописных букв заголовка — иначе её текст тоже стал бы КАПСОМ. */
function HeaderCell({ column, sort, onSort }) {
    const sortable = SORTABLE_KEYS.has(column.key);
    const active = sort.key === column.key;
    return (
        <th scope="col" aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
            className={`px-2.5 py-2.5 align-bottom ${column.numeric ? 'text-right' : 'text-left'}`}>
            <span className={`inline-flex items-end gap-1 ${column.numeric ? 'justify-end' : ''}`}>
                <button type="button" disabled={!sortable} onClick={() => onSort(column.key)}
                    className={`inline-flex items-center gap-1 text-[11px] font-semibold uppercase leading-tight tracking-wide transition ${
                        active ? 'text-slate-800' : 'text-slate-500'} ${sortable ? 'hover:text-slate-800' : 'cursor-default'} ${
                        column.numeric ? 'text-right' : 'text-left'}`}>
                    <span className="max-w-[92px]">{column.label}</span>
                    {active && (sort.dir === 'asc'
                        ? <ChevronUp size={12} className="shrink-0 text-blue-500" />
                        : <ChevronDown size={12} className="shrink-0 text-blue-500" />)}
                </button>
                {column.hint && (
                    <span className="normal-case tracking-normal">
                        <IosHint text={column.hint} label={`Как считается «${column.label}»`}
                            align={column.numeric ? 'right' : 'left'} />
                    </span>
                )}
            </span>
        </th>
    );
}

function PersonName({ row, showGroup }) {
    return (
        <div className="min-w-0" title={row.name}>
            <div className="truncate font-semibold text-slate-900">{row.name}</div>
            {showGroup && <div className="truncate text-[11px] text-slate-400">{row.groupLabel}</div>}
        </div>
    );
}

/* Тренинг — своим числом, тех. пауза — строкой под ним, только когда была: она редкая, а сложенная с
   тренингом выдавала бы паузу за обучение. */
function TrainingCell({ training, tech }) {
    return (
        <>
            <div>{durationCell(training)}</div>
            {tech >= 60 && <div className="text-[11px] text-slate-400">тех. пауза {formatDuration(tech)}</div>}
        </>
    );
}

function ShiftCell({ row, withShifts }) {
    if (row.kind === ROW_ABSENT) return <span className="font-medium text-rose-600">не вышел</span>;
    if (row.kind === ROW_NO_SHIFT) {
        // В аккаунте без смен «без смены» — не отклонение, а единственный вид работы: только часы.
        return (
            <div className="whitespace-nowrap">
                {withShifts && <div className="text-slate-500">без смены</div>}
                <div className={withShifts ? 'text-[11px] tabular-nums text-slate-400' : 'tabular-nums text-slate-600'}>
                    писал {clock(row.start)}–{clock(row.end)}
                </div>
            </div>
        );
    }
    const span = spanOf(row);
    const notes = [endNote(row), offlineNote(row)].filter(Boolean);
    return (
        <div className="max-w-[140px]">
            <div className="whitespace-nowrap tabular-nums text-slate-900">
                {span.start}–{span.end}
                {span.nextDay && <sup className="ml-0.5 text-[10px] text-slate-400" title="Следующего дня">+1</sup>}
                {row.open && <span className="ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-emerald-500 align-middle" title="Смена идёт" />}
            </div>
            {row.lateSeconds > 0 && <div className="text-[11px] text-rose-600">опоздал на {minutesLabel(row.lateSeconds)}</div>}
            {row.earlySeconds > 0 && <div className="text-[11px] text-rose-600">ушёл на {minutesLabel(row.earlySeconds)} раньше</div>}
            {notes.map((note) => <div key={note} className="text-[11px] text-slate-400">{note}</div>)}
        </div>
    );
}

function ShiftRow({ row, selected, onOpen, norms, showGroup, withShifts }) {
    const worked = row.kind !== ROW_ABSENT;
    const shift = row.kind === ROW_SHIFT;
    return (
        <tr onClick={worked ? () => onOpen(row.key) : undefined}
            className={`align-top transition-colors ${worked ? 'cursor-pointer' : ''} ${
                selected ? 'bg-blue-50/70' : 'hover:bg-slate-50/80'}`}>
            <td className={`${CELL} whitespace-nowrap tabular-nums text-slate-600`}>{formatDay(row.date)}</td>
            <td className={`${CELL} max-w-[210px]`}><PersonName row={row} showGroup={showGroup} /></td>
            <td className={`${CELL} whitespace-nowrap tabular-nums text-slate-600`}>
                {planLabel(row.plan) || (shift ? <span className="text-slate-400">вне графика</span> : <Dash />)}
            </td>
            <td className={CELL}><ShiftCell row={row} withShifts={withShifts} /></td>
            <td className={`${NUM} text-slate-900`}>{shift ? durationCell(row.active) : <Dash />}</td>
            <td className={`${NUM} text-slate-600`}>{shift ? durationCell(row.breakTime) : <Dash />}</td>
            <td className={`${NUM} text-slate-600`}>{shift ? <TrainingCell training={row.training} tech={row.tech} /> : <Dash />}</td>
            <td className={NUM}>{worked ? <ChatsCell chats={row.chats} messages={row.messages} /> : <Dash />}</td>
            <td className={`${NUM} text-slate-900`}>{shift && row.chatsPerHour !== null ? formatRate(row.chatsPerHour) : <Dash />}</td>
            <td className={NUM}><ReplyCell seconds={firstReply(row)} norm={norms.firstReplySeconds} /></td>
            <td className={NUM}><ReplyCell seconds={innerReply(row)} norm={norms.innerReplySeconds} /></td>
        </tr>
    );
}

function PersonRow({ row, onOpen, norms, showGroup }) {
    return (
        <tr onClick={() => onOpen(row)} className="cursor-pointer align-top transition-colors hover:bg-slate-50/80"
            title="Показать смены человека">
            <td className={`${CELL} max-w-[240px]`}><PersonName row={row} showGroup={showGroup} /></td>
            <td className={NUM}>
                <div className="font-semibold text-slate-900">{row.shifts || <Dash />}</div>
                {row.lateCount > 0 && (
                    <div className="text-[11px] text-rose-600">опоздания: {row.lateCount} · {minutesLabel(row.lateMinutes * 60)}</div>
                )}
                {row.earlyCount > 0 && (
                    <div className="text-[11px] text-rose-600">ранний уход: {row.earlyCount} · {minutesLabel(row.earlyMinutes * 60)}</div>
                )}
                {row.noShiftDays > 0 && <div className="text-[11px] text-slate-400">без смены: {row.noShiftDays} дн.</div>}
                {row.absent > 0 && <div className="text-[11px] text-rose-600">не вышел: {row.absent}</div>}
            </td>
            <td className={`${NUM} text-slate-900`}>{durationCell(row.active)}</td>
            <td className={`${NUM} text-slate-600`}>{durationCell(row.breakTime)}</td>
            <td className={`${NUM} text-slate-600`}><TrainingCell training={row.training} tech={row.tech} /></td>
            <td className={NUM}><ChatsCell chats={row.chats} messages={row.messages} /></td>
            <td className={`${NUM} text-slate-900`}>{row.chatsPerHour !== null ? formatRate(row.chatsPerHour) : <Dash />}</td>
            <td className={NUM}><ReplyCell seconds={firstReply(row)} norm={norms.firstReplySeconds} /></td>
            <td className={NUM}><ReplyCell seconds={innerReply(row)} norm={norms.innerReplySeconds} /></td>
        </tr>
    );
}

function TotalRow({ total, view, norms, label }) {
    const cell = 'whitespace-nowrap px-2.5 py-2.5 text-right tabular-nums';
    const lead = view === VIEW_PEOPLE ? 1 : 4;
    return (
        <tr className="border-t border-slate-200/70 text-[12.5px] font-semibold text-slate-600">
            <td colSpan={lead} className="px-2.5 py-2.5">{label}</td>
            {view === VIEW_PEOPLE && <td className={cell}>{total.shifts || <Dash />}</td>}
            <td className={cell}>{durationCell(total.active)}</td>
            <td className={cell}>{durationCell(total.breakTime)}</td>
            <td className={cell}><TrainingCell training={total.training} tech={total.tech} /></td>
            <td className={cell}><ChatsCell chats={total.chats} messages={total.messages} /></td>
            <td className={cell}>{total.chatsPerHour !== null ? formatRate(total.chatsPerHour) : <Dash />}</td>
            <td className={cell}><ReplyCell seconds={firstReply(total)} norm={norms.firstReplySeconds} /></td>
            <td className={cell}><ReplyCell seconds={innerReply(total)} norm={norms.innerReplySeconds} /></td>
        </tr>
    );
}

const pluralRu = (n, one, few, many) => {
    const abs = Math.abs(Number(n)) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

function totalLabel(view, total, people, withShifts) {
    if (view === VIEW_PEOPLE) return `Итого · ${people} ${pluralRu(people, 'сотрудник', 'сотрудника', 'сотрудников')}`;
    const parts = [];
    if (total.shifts) parts.push(`${total.shifts} ${pluralRu(total.shifts, 'смена', 'смены', 'смен')}`);
    if (total.noShiftDays) {
        parts.push(withShifts ? `${total.noShiftDays} без смены`
            : `${total.noShiftDays} ${pluralRu(total.noShiftDays, 'день', 'дня', 'дней')} работы`);
    }
    if (total.absent) parts.push(`${total.absent} не ${pluralRu(total.absent, 'вышел', 'вышли', 'вышли')}`);
    return parts.length ? `Итого · ${parts.join(' · ')}` : 'Итого';
}

/* ── журнал смены ───────────────────────────────────────────────────────── */

function Stat({ label, children }) {
    return (
        <div className="rounded-xl bg-white px-3 py-2 ring-1 ring-slate-200/70">
            <div className="text-[11px] text-slate-400">{label}</div>
            <div className="mt-0.5 text-[15px] font-semibold tabular-nums text-slate-900">{children}</div>
        </div>
    );
}

function HourChart({ row }) {
    const bars = hourBars(row);
    if (!bars.length) return null;
    return (
        <section className="space-y-2">
            <div className="text-[12px] font-semibold uppercase tracking-wider text-slate-400">Чаты по часам</div>
            <div className="rounded-2xl bg-white px-3 pb-2 pt-3 ring-1 ring-slate-200/70">
                <div className="flex h-28 items-end gap-[3px]">
                    {bars.map((bar) => (
                        <div key={bar.key} className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1"
                            title={`${bar.label}:00 — ${bar.chats} ${pluralRu(bar.chats, 'чат', 'чата', 'чатов')}, ${bar.messages} ${pluralRu(bar.messages, 'сообщение', 'сообщения', 'сообщений')}`}>
                            <span className={`text-[10px] tabular-nums ${bar.chats ? 'text-slate-500' : 'text-transparent'}`}>{bar.chats}</span>
                            <div className={`w-full rounded-t-[4px] ${bar.chats ? 'bg-blue-500/80' : 'bg-slate-100'}`}
                                style={{ height: `${Math.max(bar.share * 72, 3)}px` }} />
                        </div>
                    ))}
                </div>
                <div className="mt-1 flex gap-[3px]">
                    {bars.map((bar, index) => (
                        <span key={bar.key} className="min-w-0 flex-1 text-center text-[10px] tabular-nums text-slate-400">
                            {bars.length <= 14 || index % 2 === 0 ? bar.label : ''}
                        </span>
                    ))}
                </div>
            </div>
        </section>
    );
}

function ShiftJournal({ row, onClose, norms, withShifts, themeScope }) {
    const open = Boolean(row);
    const isNarrow = useIsMobileShell();
    useScreenBackGesture(isNarrow && open, onClose);
    useEffect(() => {
        if (!open) return undefined;
        const onKey = (event) => { if (event.key === 'Escape') onClose(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [open, onClose]);
    const scrollRef = useRef(null);
    useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = 0; }, [row?.key]);
    if (!row) return null;

    const shift = row.kind === ROW_SHIFT;
    const span = spanOf(row);
    const plan = planLabel(row.plan);
    const bar = journalBar(row);
    const totals = journalTotals(row);
    const segments = journalSegments(row);

    // Панель — в портале, вне окна раздела, поэтому тему окна («Ночь», «Роза», «Вишня») она несёт
    // сама: тот же класс и атрибут, что у окна, и его переменные — иначе над тёмным окном встала
    // бы светлая панель, второй вид на одном экране. Слой темы красит ПОТОМКОВ окна, поэтому фон
    // панели — на вложенном блоке, а не на самой обёртке.
    return createPortal(
        <aside role="dialog" aria-label="Журнал смены" data-chat-theme={themeScope?.id}
            className={`${themeScope ? 'wz-workspace ' : ''}fixed inset-y-0 right-0 z-[85] w-full animate-push-in sm:w-[420px]`}
            style={{ fontFamily: APPLE_FONT, ...(themeScope?.style || {}) }}>
          <div className="flex h-full flex-col border-l border-slate-200/80 bg-slate-50/95 shadow-[-12px_0_32px_rgba(15,23,42,0.10)] backdrop-blur-xl">
            <div className="border-b border-slate-200/70 bg-white/80 px-5 pb-3.5 pt-4">
                <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                        <div className="text-[17px] font-semibold text-slate-900">
                            {shift ? 'Журнал смены' : (withShifts ? 'Без смены' : 'Работа за день')}
                        </div>
                        <div className="truncate text-[13px] text-slate-500">
                            {row.name}{row.groupLabel ? ` · ${row.groupLabel}` : ''} · {formatDay(row.date)}
                        </div>
                        <div className="mt-0.5 text-[12.5px] tabular-nums text-slate-400">
                            {shift ? `Смена ${span.start} — ${span.end}${span.nextDay ? ' следующего дня' : ''}`
                                : `Писал ${clock(row.start)} — ${clock(row.end)}${withShifts ? ', смены в разделе не было' : ''}`}
                            {plan ? ` · по графику ${plan}` : (shift ? ' · вне графика' : '')}
                        </div>
                    </div>
                    <button type="button" onClick={onClose} aria-label="Закрыть"
                        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-[0.96]">
                        <X size={15} />
                    </button>
                </div>
                {bar.length > 0 && (
                    <div className="mt-3 space-y-2.5">
                        <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
                            {bar.map((part, index) => (
                                <div key={`${part.kind}-${index}`} className={statusDotClass(part.tone)}
                                    style={{ width: `${(part.share * 100).toFixed(3)}%` }} />
                            ))}
                        </div>
                        <div className="flex flex-wrap gap-x-3 gap-y-1.5">
                            {totals.map((item) => (
                                <span key={item.kind} className="inline-flex items-center gap-1.5 text-[12px] text-slate-600">
                                    <span className={`h-2 w-2 rounded-full ${statusDotClass(item.tone)}`} />
                                    {item.label}
                                    <span className="tabular-nums text-slate-400">{formatDuration(item.seconds)}</span>
                                </span>
                            ))}
                        </div>
                    </div>
                )}
            </div>
            <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 pb-6 pt-3">
                <div className="grid grid-cols-3 gap-2">
                    <Stat label="Чаты">{row.chats}</Stat>
                    <Stat label="Сообщения">{row.messages}</Stat>
                    <Stat label="Чатов в час">{shift && row.chatsPerHour !== null ? formatRate(row.chatsPerHour) : '—'}</Stat>
                    <Stat label="Первый ответ">
                        <ReplyCell seconds={firstReply(row)} norm={norms.firstReplySeconds} />
                    </Stat>
                    <Stat label="Ответ в чате">
                        <ReplyCell seconds={innerReply(row)} norm={norms.innerReplySeconds} />
                    </Stat>
                    <Stat label="Первых ответов">{row.firstCount}</Stat>
                </div>
                <HourChart row={row} />
                {segments.length > 0 && (
                    <section className="space-y-2">
                        <div className="text-[12px] font-semibold uppercase tracking-wider text-slate-400">Статусы</div>
                        <div className="divide-y divide-slate-100 overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                            {segments.map((segment, index) => (
                                <div key={`${segment.start}-${index}`} className="px-3.5 py-2.5">
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="inline-flex items-center gap-2 text-[13px] font-medium text-slate-800">
                                            <span className={`h-2 w-2 rounded-full ${statusDotClass(segment.tone)}`} />
                                            {segment.label}
                                        </span>
                                        <span className="text-[13px] font-medium tabular-nums text-slate-700">{formatDuration(segment.seconds)}</span>
                                    </div>
                                    <div className="mt-0.5 pl-4 text-[12px] tabular-nums text-slate-500">
                                        {segment.start} — {segment.end}{segment.nextDay ? ' след. дня' : ''}
                                        {segment.note ? <span className="text-slate-400"> · {segment.note}</span> : null}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </section>
                )}
            </div>
          </div>
        </aside>,
        document.body,
    );
}

/* ── вкладка ───────────────────────────────────────────────────────────── */

export default function ChatShiftReport({ apiBaseUrl, headers, showToast, account, onGoToMapping, downloadCsv,
    themeScope = null }) {
    const [range, setRange] = useState(() => presetRange('week'));
    const [view, setView] = useState(VIEW_SHIFTS);
    const [group, setGroup] = useState(GROUP_ALL);
    const [query, setQuery] = useState('');
    const [sorts, setSorts] = useState(DEFAULT_SORT);
    const [selectedKey, setSelectedKey] = useState(null);
    // Переход «человек → его смены»: по id, чипом с крестиком, а не подменой поиска.
    const [person, setPerson] = useState(null);
    const [state, setState] = useState({ data: null, error: null, loading: true });
    const requestRef = useRef(0);
    const shownRef = useRef(null);
    shownRef.current = state.data;
    const headersRef = useRef(headers);
    headersRef.current = headers;

    const load = useCallback((next) => {
        const requestId = requestRef.current + 1;
        requestRef.current = requestId;
        // Прежний период остаётся на экране приглушённым, пока едет новый: таблица не мигает пустотой.
        setState((prev) => ({ data: prev.data, error: null, loading: true }));
        axios.get(`${apiBaseUrl}${SHIFT_REPORT_PATH}`, {
            headers: headersRef.current?.(),
            params: { from: next.from, to: next.to, account },
        }).then((response) => {
            if (requestId !== requestRef.current) return;
            const data = response.data || {};
            // Период на экране — тот, что посчитал сервер: пустой выбор он заменяет неделей, а хвост
            // в будущем обрезает сегодняшним днём; иначе календарь и таблица говорили бы о разном.
            if (data.from && data.to && (data.from !== next.from || data.to !== next.to)) {
                setRange({ from: data.from, to: data.to });
            }
            setState({ data, error: null, loading: false });
        }).catch((error) => {
            if (requestId !== requestRef.current) return;
            // Не удалось — на экране остаётся прежний период того же аккаунта, и календарь
            // возвращается к нему: иначе под датами нового периода стояли бы строки старого, а
            // «Выгрузить» сохранил бы их под чужим именем. Данные другого аккаунта не показываем.
            const shown = shownRef.current;
            const keep = shown && shown.account === account ? shown : null;
            if (keep?.from && keep?.to) setRange({ from: keep.from, to: keep.to });
            setState({ data: keep, error: error?.response?.data?.error || 'Не удалось загрузить смены',
                loading: false });
        });
    }, [apiBaseUrl, account]);

    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { load(range); }, [load]);

    const maxDays = state.data?.maxDays || MAX_DAYS_FALLBACK;
    const changeRange = (next) => {
        const { range: limited, clipped } = clampRange(next, maxDays);
        if (clipped) showToast?.(`Период — не больше ${maxDays} суток: показаны последние ${maxDays}`, 'info');
        setRange(limited);
        load(limited);
    };

    const data = state.data;
    const withShifts = data?.withShifts !== false;
    const norms = data?.norms || DEFAULT_NORMS;
    const minActive = data?.rateMinActiveSeconds ?? RATE_MIN_ACTIVE_SECONDS;
    const rows = useMemo(() => data?.rows || [], [data]);
    const personId = person?.id ?? null;
    const options = useMemo(() => groupOptions(data?.groups, rows, query, personId), [data, rows, query, personId]);
    // Группа, которой в новом периоде нет, не оставляет таблицу пустой без объяснения.
    const activeGroup = options.some((option) => option.value === group) ? group : GROUP_ALL;
    const filtered = useMemo(() => filterRows(rows, { group: activeGroup, query, personId }),
        [rows, activeGroup, query, personId]);
    const people = useMemo(() => peopleRows(filtered, minActive), [filtered, minActive]);
    const sort = sorts[view];
    const shown = useMemo(() => sortRows(view === VIEW_PEOPLE ? people : filtered, sort),
        [view, people, filtered, sort]);
    const total = useMemo(() => sumRows(view === VIEW_PEOPLE ? people : filtered, minActive),
        [view, people, filtered, minActive]);
    const columns = view === VIEW_PEOPLE ? peopleColumns(norms) : shiftColumns(norms);
    const selected = selectedKey ? rows.find((row) => row.key === selectedKey) || null : null;
    const showGroup = activeGroup === GROUP_ALL && (data?.groups || []).length > 1;
    const preset = activePreset(range);
    const today = isoDay(new Date());
    const unlinked = data?.unlinked;
    const closeJournal = useCallback(() => setSelectedKey(null), []);

    const toggleSort = (key) => setSorts((prev) => ({ ...prev, [view]: nextSort(prev[view], key) }));

    const changeView = (next) => {
        setSelectedKey(null);
        setView(next);
    };

    const openPerson = (row) => {
        setPerson({ id: row.userId, name: row.name });
        changeView(VIEW_SHIFTS);
    };

    const exportCsv = () => {
        if (!shown.length || !downloadCsv || state.loading) return;
        // Имя файла — по периоду строк, а не календаря: это одно и то же, пока ничего не грузится.
        downloadCsv(csvFileName(view, { from: data?.from, to: data?.to }),
            view === VIEW_PEOPLE ? peopleCsv(shown) : shiftCsv(shown));
        showToast?.(`Выгружено ${shown.length} строк`, 'success');
    };

    let emptyText = withShifts ? 'За выбранный период смен и сообщений нет' : 'За выбранный период сообщений нет';
    if (query.trim() || personId !== null) emptyText = 'Никого не найдено';
    else if (activeGroup !== GROUP_ALL) {
        const label = options.find((option) => option.value === activeGroup)?.label;
        emptyText = `В группе «${label}» за период ничего нет`;
    }

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    <IosSegmented value={preset} ariaLabel="Период"
                        options={PERIOD_PRESETS.map((item) => ({ value: item.key, label: item.label }))}
                        onChange={(key) => changeRange(presetRange(key))} />
                    <IosDateRangePicker from={range.from} to={range.to} max={today} min={earliestDay()}
                        onChange={({ from, to }) => changeRange({ from: from || to, to: to || from })} />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <div className="relative">
                        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={query} onChange={(event) => setQuery(event.target.value)}
                            placeholder="Поиск менеджера…" aria-label="Поиск менеджера"
                            className={`${iosInput} w-52 py-2 pl-8 pr-7 text-[13px]`} />
                        {query && (
                            <button type="button" onClick={() => setQuery('')} aria-label="Очистить поиск"
                                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-0.5 text-slate-400 hover:text-slate-600">
                                <X size={12} />
                            </button>
                        )}
                    </div>
                    <button type="button" onClick={exportCsv} disabled={!shown.length || state.loading}
                        className={iosBtnGhost}>
                        <Download size={13} /> Выгрузить
                    </button>
                    <button type="button" onClick={() => load(range)} className={iosBtnGhost}>
                        <RefreshCw size={13} className={state.loading && data ? 'animate-spin' : ''} /> Обновить
                    </button>
                </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
                <IosSegmented value={view} ariaLabel="Вид отчёта" onChange={changeView}
                    options={[{ value: VIEW_SHIFTS, label: 'По сменам' }, { value: VIEW_PEOPLE, label: 'По сотрудникам' }]} />
                {/* Групп бывает пять, а сегменты не переносятся: на телефоне ряд листается сам, а не
                    растягивает раздел вбок. */}
                {(data?.groups || []).length > 1 && (
                    <div className="max-w-full overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
                        <IosSegmented value={activeGroup} ariaLabel="Группа" onChange={setGroup} options={options} />
                    </div>
                )}
                {person && (
                    <span className="inline-flex max-w-full items-center gap-1 rounded-full bg-blue-50 py-1 pl-3 pr-1 text-[12.5px] font-medium text-blue-700 ring-1 ring-blue-100">
                        <span className="truncate">{person.name}</span>
                        <button type="button" onClick={() => setPerson(null)} aria-label="Показать всех"
                            className="rounded-full p-1 text-blue-500 hover:bg-blue-100 hover:text-blue-700">
                            <X size={12} />
                        </button>
                    </span>
                )}
            </div>

            {state.error && (
                <div className={`${iosCard} flex items-center gap-2 px-4 py-3 text-[13px] text-rose-600`} role="alert">
                    <AlertCircle size={15} /> {state.error}
                </div>
            )}

            {/* Ответы непривязанных авторов не засчитаны никому — говорим прямо и ведём туда, где
                это чинится, как «Аналитика». */}
            {unlinked?.messages > 0 && (
                <div className={`${iosCard} flex flex-wrap items-center gap-2 px-4 py-3 text-[12.5px] text-slate-600`}>
                    <AlertCircle size={14} className="text-amber-500" />
                    <span>{unlinkedText(unlinked)}</span>
                    {onGoToMapping && (
                        <button type="button" onClick={onGoToMapping} className="font-semibold text-blue-600 hover:underline">
                            Привязать
                        </button>
                    )}
                </div>
            )}

            {!data && state.loading && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-[13px] text-slate-400`}>
                    <Loader2 size={15} className="animate-spin" /> Загрузка…
                </div>
            )}

            {data && (
                <div className={`${iosCard} overflow-hidden transition-opacity duration-200 ${state.loading ? 'opacity-60' : ''}`}
                    aria-busy={state.loading}>
                    {/* Высота не меньше самой длинной подсказки «i»: пузырёк живёт внутри прокрутки и при
                        одной-двух строках обрезался бы её краем. */}
                    <div className="max-h-[calc(100vh-300px)] min-h-[240px] overflow-auto">
                        <table className="w-full border-collapse text-[13px]">
                            <thead className="sticky top-0 z-10 bg-white/90 backdrop-blur-xl">
                                <tr className="border-b border-slate-200/70">
                                    {columns.map((column) => (
                                        <HeaderCell key={column.key} column={column} sort={sort} onSort={toggleSort} />
                                    ))}
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {shown.length === 0 && (
                                    <tr>
                                        <td colSpan={columns.length} className="px-4 py-10 text-center text-[13px] text-slate-400">
                                            {emptyText}
                                        </td>
                                    </tr>
                                )}
                                {view === VIEW_PEOPLE
                                    ? shown.map((row) => (
                                        <PersonRow key={row.key} row={row} onOpen={openPerson} norms={norms}
                                            showGroup={showGroup} />
                                    ))
                                    : shown.map((row) => (
                                        <ShiftRow key={row.key} row={row} selected={row.key === selectedKey}
                                            onOpen={setSelectedKey} norms={norms} showGroup={showGroup}
                                            withShifts={withShifts} />
                                    ))}
                            </tbody>
                            {shown.length > 0 && (
                                <tfoot className="sticky bottom-0 bg-slate-50/90 backdrop-blur-xl">
                                    <TotalRow total={total} view={view} norms={norms}
                                        label={totalLabel(view, total, people.length, withShifts)} />
                                </tfoot>
                            )}
                        </table>
                    </div>
                </div>
            )}

            <ShiftJournal row={view === VIEW_SHIFTS ? selected : null} onClose={closeJournal} norms={norms}
                withShifts={withShifts} themeScope={themeScope} />
        </div>
    );
}
