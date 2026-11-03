import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    FileSpreadsheet, History, ListChecks, Loader2, Pencil, RefreshCw, RotateCcw, Search, Trash2,
} from 'lucide-react';
import {
    iosCard, iosInput, iosGroupLabel, iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosBadge, IosHint, IosMenu, IosModal, IosPager, IosSegmented, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import MonthPicker from '../trainings/MonthPicker';
import { Metric, Td, Th } from '../wiki/reportKit';
import { monthLabel } from './dialListPeriods';
import {
    BATCH_REASON_MAX, PLAN_HINT, STATUS_FILTER_OPTIONS, STATUS_LABELS, STATUS_TONES, WEEKDAYS,
    batchActionSummary, batchCheckPending, batchCheckText, batchEventText, batchReasonError, calendarCells,
    calendarTone, dayTitle, exportFileName, fmtClock, fmtDay, fmtSeconds, fmtWhen, funnelTiles, monthParts, num,
    planFormError, planNote, planPayload, planTiles, plural, recomputeSummary, ruleLabel,
} from './tezSuccesses';

/*
 * Вкладка «Успешки» раздела «Воронка ОП Tez» (03.11.2026) — бывшее окно «База лидов и
 * успешки» из «Учёта часов», перенесённое по просьбе владельца целиком: план отдела,
 * воронка базы месяца, успешки по операторам и по дням, детализация по лидам, загрузки
 * с удалением и восстановлением, выгрузка в Excel и «Сверить сейчас».
 *
 * Своей загрузки базы здесь нет: файл грузят во вкладке «База водителей», и сервер сам
 * кладёт его и в базу успешек этого месяца (решение владельца 15.10.2026 — «одна загрузка
 * на обе»; 03.11.2026 — «загрузка базы по сути уже там есть»). Разбор файла у обеих баз
 * общий (common/leads_file.py), поэтому файл, принятый разделом, примет и база успешек.
 *
 * Колонки «План успешек», «Выполнение» и «Успешки в час» остались в таблице «Учёта часов»
 * (выбор владельца 03.11.2026): они считаются от часов той же таблицы.
 *
 * Данные — те же ручки, что у окна в «Учёте часов» (/api/tez_leads/*, /api/department_plan),
 * сервер не менялся. Разрезы «Операторы» и «По дням» — по всему отделу: выбора группы, как в
 * «Учёте часов», в разделе нет, а база лидов и так общая на отдел.
 *
 * Ответ, пришедший после смены месяца, не должен лечь на новый месяц: у каждого запроса
 * свой счётчик, и ответ принимается, только если он последний и месяц тот же (scopeRef).
 */

const VIEWS = [
    { value: 'operators', label: 'Операторы' },
    { value: 'days', label: 'По дням' },
    { value: 'leads', label: 'Лиды' },
    { value: 'batches', label: 'Загрузки' },
];

const LEADS_PAGE_SIZE = 50;
// Проверка «уже работающих» по свежей загрузке идёт на сервере фоном; пока она идёт,
// перечитываем сводку сами, но не дольше трёх минут: поток проверки мог и умереть
// (перезапуск сервера), и опрашивать его вечно незачем.
const CHECK_POLL_MS = 5000;
const CHECK_POLL_MAX = 36;

// Разрушительное действие — своей строкой, как в журнале раздела (DialListJournal).
const BTN_DANGER = 'inline-flex items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 py-2.5 text-[13.5px] '
    + 'font-semibold text-white shadow-sm transition-all hover:bg-rose-700 active:scale-[0.98] '
    + 'disabled:cursor-not-allowed disabled:opacity-50';

const FIELD_HEAD = `flex h-5 items-center ${iosGroupLabel}`;

const CALENDAR_TONES = {
    empty: 'bg-slate-50 text-slate-300 ring-1 ring-slate-100',
    low: 'bg-emerald-50 text-emerald-800 ring-1 ring-emerald-100',
    mid: 'bg-emerald-100 text-emerald-900 ring-1 ring-emerald-200',
    high: 'bg-emerald-200 text-emerald-950 ring-1 ring-emerald-300',
};

const todayIso = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

/* Почему файла нет — словами: отказ сервера (4xx) его же текстом, сбой — без «Internal server error». */
const exportFailure = (status, data) => (status && status < 500 && data?.error
    ? data.error
    : 'Сервер не собрал файл — попробуйте ещё раз');

const Spinner = ({ text }) => (
    <div className="flex items-center justify-center gap-2 px-4 py-8 text-[13px] text-slate-500">
        <Loader2 size={15} className="animate-spin" /> {text}
    </div>
);

const Failed = ({ text, onRetry }) => (
    <div className="px-4 py-6 text-center text-[13px] text-rose-600">
        {text}
        {onRetry && <button type="button" onClick={onRetry} className="ml-2 font-medium text-blue-600">Повторить</button>}
    </div>
);

/* Две поездки лида: прошлый месяц (если по нему водитель «уже работал» — это и есть
   причина статуса, она выделена) и отчётный. Обе видны в строке, а не только в title. */
const LeadTripDates = ({ row }) => {
    const previousTrip = row?.prev_month_first_order_at;
    // first_order_at — запасное имя прежнего ответа сервера; новое однозначно — отчётный месяц.
    const currentTrip = row?.month_first_order_at || row?.first_order_at;
    if (!previousTrip && !currentTrip) return <span className="text-slate-400">—</span>;
    const previousCausedStatus = row?.status === 'already_working' && row?.status_rule === 'active_prev_month';
    return (
        <div className="space-y-1 whitespace-nowrap">
            {previousTrip && (
                <div className={previousCausedStatus ? 'text-amber-700' : 'text-slate-500'}>
                    <div className="text-[10.5px] font-semibold uppercase tracking-wide">
                        Предыдущий месяц{previousCausedStatus ? ' · причина статуса' : ''}
                    </div>
                    <div className={previousCausedStatus ? 'font-medium' : ''}>{fmtWhen(previousTrip)}</div>
                </div>
            )}
            {currentTrip && (
                <div className="text-slate-500">
                    <div className="text-[10.5px] font-semibold uppercase tracking-wide">Отчётный месяц</div>
                    <div>{fmtWhen(currentTrip)}</div>
                </div>
            )}
        </div>
    );
};

/* Календарь месяца: где густо, а где пусто, видно одним взглядом; день с успешками
   раскрывается окном с самими контактами. Заливка — загрузка дня от пикового. */
const SuccessCalendar = ({ year, month, byDay, activeDate, onPick }) => {
    const cells = useMemo(() => calendarCells(year, month, byDay), [year, month, byDay]);
    const peak = cells.reduce((max, cell) => Math.max(max, cell?.successes || 0), 0);
    const today = todayIso();
    return (
        <div className="mx-auto max-w-2xl">
            <div className="mb-1.5 grid grid-cols-7 gap-1.5 sm:gap-2">
                {WEEKDAYS.map((label) => (
                    <div key={label} className="text-center text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
                ))}
            </div>
            <div className="grid grid-cols-7 gap-1.5 sm:gap-2">
                {cells.map((cell, idx) => {
                    if (!cell) return <div key={`pad-${idx}`} aria-hidden="true" />;
                    const empty = !cell.successes;
                    const active = activeDate === cell.date;
                    return (
                        <button
                            key={cell.date}
                            type="button"
                            disabled={empty}
                            onClick={() => onPick?.(cell.date)}
                            aria-label={`${cell.day} число, успешек ${cell.successes}`}
                            className={`flex aspect-square flex-col items-center justify-center rounded-xl transition ${CALENDAR_TONES[calendarTone(cell.successes, peak)]} ${
                                empty ? 'cursor-default' : 'hover:-translate-y-0.5 hover:shadow-sm active:scale-[0.97]'
                            } ${active ? 'ring-2 ring-emerald-500' : ''}`}
                        >
                            <span className={`text-[11px] tabular-nums ${cell.date === today ? 'font-semibold text-blue-600' : empty ? 'text-slate-300' : 'text-slate-500'}`}>
                                {cell.day}
                            </span>
                            <span className={`text-[16px] font-semibold leading-none tabular-nums sm:text-[18px] ${empty ? 'text-slate-200' : ''}`}>
                                {cell.successes || '·'}
                            </span>
                        </button>
                    );
                })}
            </div>
        </div>
    );
};

const Fact = ({ label, children }) => (
    <div className="min-w-0">
        <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
        <div className="text-[12.5px] tabular-nums text-slate-700">{children}</div>
    </div>
);

/* Одна успешка в окне дня: кто позвонил → кто выехал. */
const DaySuccessRow = ({ row }) => (
    <div className="rounded-xl bg-slate-50 px-3.5 py-3 ring-1 ring-slate-200/60">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <div className="min-w-0">
                <div className="truncate text-[13.5px] font-semibold text-slate-900">{row.full_name || 'Без имени'}</div>
                <div className="text-[12px] tabular-nums text-slate-500">{row.phone}</div>
            </div>
            <div className="min-w-0 text-right">
                <div className="text-[11px] uppercase tracking-wide text-slate-400">Оператор</div>
                <div className="truncate text-[13px] font-medium text-slate-800">{row.operator_name || '—'}</div>
            </div>
        </div>
        <div className="mt-2.5 grid grid-cols-2 gap-x-3 gap-y-2 border-t border-slate-200/70 pt-2.5 sm:grid-cols-4">
            <Fact label="Звонок">{fmtWhen(row.call_at)}</Fact>
            <Fact label="Разговор, с">{fmtSeconds(row.talk_duration_seconds)}</Fact>
            <Fact label="Первый заказ">{fmtClock(row.first_order_at)}</Fact>
            <Fact label="Засчитано">{ruleLabel(row.rule_code) || row.rule_code || '—'}</Fact>
        </div>
        {(row.is_late || row.source_file_name) && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {row.is_late && <IosBadge tone="amber">найдена после закрытия месяца</IosBadge>}
                {row.source_file_name && <span className="truncate text-[11.5px] text-slate-500">из базы «{row.source_file_name}»</span>}
            </div>
        )}
    </div>
);

/* Детализация по лидам базы месяца: поиск, статус, оператор и страницы. Отбор живёт у
   вкладки — вернувшись из другого разреза, человек видит тот же отбор и ту же страницу. */
const LeadsView = ({ request, scope, year, month, operators, filter, onFilter, page, onPage, reloadKey }) => {
    const [q, setQ] = useState(filter.q);
    // scope — месяц, к которому относятся строки: после смены месяца строки прошлого не показываем.
    const [state, setState] = useState({ scope: '', rows: [], total: 0, pages: 1, loading: true, error: '' });
    const [retry, setRetry] = useState(0);

    // Поиск — с паузой: запрос на каждую букву перечитывал бы страницу посреди набора.
    useEffect(() => {
        const next = q.trim();
        if (next === filter.q) return undefined;
        const timer = setTimeout(() => { onFilter((f) => ({ ...f, q: next })); onPage(1); }, 300);
        return () => clearTimeout(timer);
    }, [q, filter.q, onFilter, onPage]);

    useEffect(() => {
        let alive = true;
        setState((s) => ({ ...s, loading: true, error: '' }));
        request('/api/tez_leads/detail', {
            params: {
                year, month, status: filter.status, operator_id: filter.operator, search: filter.q,
                page, page_size: LEADS_PAGE_SIZE,
            },
        })
            .then((data) => {
                if (!alive) return;
                setState({
                    scope, rows: data.leads || [], total: Number(data.total) || 0, pages: Number(data.pages) || 1, loading: false, error: '',
                });
            })
            .catch((e) => {
                if (!alive) return;
                setState({ scope, rows: [], total: 0, pages: 1, loading: false, error: e.message || 'Не удалось загрузить лиды' });
            });
        return () => { alive = false; };
    }, [request, scope, year, month, filter.status, filter.operator, filter.q, page, reloadKey, retry]);

    const operatorOptions = useMemo(() => [
        { value: '', label: 'Все операторы' },
        ...operators.filter((o) => o.operator_id).map((o) => ({
            value: String(o.operator_id), label: o.operator_name || `#${o.operator_id}`, meta: num(o.successes),
        })),
    ], [operators]);

    const hasFilters = Boolean(filter.status || filter.operator || filter.q || q.trim());
    const reset = () => { setQ(''); onFilter({ status: '', operator: '', q: '' }); onPage(1); };
    const fresh = state.scope === scope;
    const rows = fresh ? state.rows : [];
    const total = fresh ? state.total : 0;
    const pages = fresh ? state.pages : 1;
    const loading = state.loading || !fresh;
    const error = fresh ? state.error : '';

    return (
        <div className="space-y-2">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <div className="relative sm:flex-1">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        type="search"
                        value={q}
                        onChange={(e) => setQ(e.target.value)}
                        placeholder="ФИО или номер"
                        className={`${iosInput} pl-9`}
                        aria-label="Поиск по лидам"
                    />
                </div>
                <div className="flex items-center gap-2">
                    <CustomSelect
                        value={filter.status}
                        onChange={(v) => { onFilter((f) => ({ ...f, status: String(v) })); onPage(1); }}
                        options={STATUS_FILTER_OPTIONS}
                        variant="ios"
                        className="min-w-0 flex-1 sm:w-48 sm:flex-none"
                        ariaLabel="Статус"
                    />
                    <CustomSelect
                        value={filter.operator}
                        onChange={(v) => { onFilter((f) => ({ ...f, operator: String(v) })); onPage(1); }}
                        options={operatorOptions}
                        variant="ios"
                        searchable={operatorOptions.length > 9}
                        className="min-w-0 flex-1 sm:w-56 sm:flex-none"
                        ariaLabel="Оператор"
                    />
                </div>
            </div>

            <div className="flex items-center justify-between gap-3 px-1 text-[13px] text-slate-500">
                <span className="flex items-center gap-2">
                    <span><span className="font-semibold tabular-nums text-slate-900">{num(total)}</span> {plural(total, 'лид', 'лида', 'лидов')}</span>
                    {loading && rows.length > 0 && <Loader2 size={13} className="animate-spin text-slate-400" />}
                </span>
                {hasFilters && (
                    <button type="button" onClick={reset} className="text-[12.5px] text-slate-500 underline decoration-slate-300 underline-offset-2 transition hover:text-slate-700">
                        сбросить отбор
                    </button>
                )}
            </div>

            <div className={`${iosCard} overflow-hidden`}>
                {error ? (
                    <Failed text={error} onRetry={() => setRetry((n) => n + 1)} />
                ) : loading && rows.length === 0 ? (
                    <Spinner text="Загружаем лиды…" />
                ) : rows.length === 0 ? (
                    <div className="px-4 py-10 text-center text-[13px] text-slate-500">
                        {hasFilters ? 'Под выбранный отбор лидов нет' : 'В базе этого месяца лидов нет — её загружают во вкладке «База водителей»'}
                    </div>
                ) : (
                    <div className={`overflow-x-auto transition-opacity ${loading ? 'opacity-60' : ''}`}>
                        <table className="w-full border-collapse">
                            <thead className="border-b border-slate-100">
                                <tr><Th>Водитель</Th><Th>Статус</Th><Th>Оператор</Th><Th>Звонок</Th><Th>Поездки</Th></tr>
                            </thead>
                            <tbody className="divide-y divide-slate-50">
                                {rows.map((row) => (
                                    <tr key={row.lead_id} className="align-top">
                                        <Td>
                                            <div className="font-medium text-slate-800">
                                                {row.full_name || '—'}
                                                {row.upload_count > 1 && (
                                                    <span className="ml-1.5 text-[11.5px] font-normal text-slate-400" title="Сколько раз номер загружали в этом месяце">×{row.upload_count}</span>
                                                )}
                                            </div>
                                            <div className="text-[12px] tabular-nums text-slate-500">{row.phone}</div>
                                        </Td>
                                        <Td>
                                            <IosBadge tone={STATUS_TONES[row.status] || 'slate'}>{STATUS_LABELS[row.status] || row.status || '—'}</IosBadge>
                                            {ruleLabel(row.status_rule) && (
                                                <div className="mt-1 max-w-[240px] text-[11.5px] leading-snug text-slate-500">{ruleLabel(row.status_rule)}</div>
                                            )}
                                        </Td>
                                        <Td>{row.operator_name || <span className="text-slate-400">—</span>}</Td>
                                        <Td muted><span className="whitespace-nowrap">{fmtWhen(row.call_at)}</span></Td>
                                        <Td><LeadTripDates row={row} /></Td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
            <IosPager
                page={page}
                pageCount={pages}
                total={total}
                from={(page - 1) * LEADS_PAGE_SIZE + 1}
                to={Math.min(page * LEADS_PAGE_SIZE, total)}
                onPage={onPage}
                unit="лиды"
            />
        </div>
    );
};

/* История удалений и восстановлений загрузки — раскрывается под её строкой. */
const BatchHistory = ({ state, onRetry }) => (
    <div className="border-t border-slate-100 bg-slate-50/70 px-4 py-2.5">
        {state.loading ? (
            <span className="inline-flex items-center gap-1.5 text-[12px] text-slate-500">
                <Loader2 size={13} className="animate-spin" /> Загружаем историю…
            </span>
        ) : state.error ? (
            <div className="text-[12px] text-rose-600">
                {state.error}
                <button type="button" onClick={onRetry} className="ml-2 font-medium text-blue-600">Повторить</button>
            </div>
        ) : state.events.length ? (
            <ul className="space-y-1">
                {state.events.map((e) => (
                    <li key={e.id} className="flex flex-wrap gap-x-2 text-[12px] text-slate-600">
                        <span className="tabular-nums text-slate-400">{fmtWhen(e.created_at)}</span>
                        <span className={`font-medium ${e.action === 'delete' ? 'text-rose-600' : 'text-emerald-700'}`}>
                            {e.action === 'delete' ? 'удаление' : 'восстановление'}
                        </span>
                        <span>{e.actor_name || '—'}</span>
                        <span className="text-slate-500">{batchEventText(e)}</span>
                        {e.reason && <span className="italic">«{e.reason}»</span>}
                    </li>
                ))}
            </ul>
        ) : (
            <span className="text-[12px] text-slate-500">Загрузку не удаляли — история пуста.</span>
        )}
    </div>
);

const TezSuccessesPanel = ({ apiBaseUrl, authHeaders, departmentId, month, onMonthChange, canEdit = true, showToast }) => {
    const parts = monthParts(month);
    const year = parts?.year;
    const monthNum = parts?.month;
    const scope = `${departmentId}|${month}`;
    const scopeRef = useRef(scope);
    scopeRef.current = scope;
    // showToast — новая функция на каждый рендер App: держим её в ref, а не в зависимостях.
    const toastRef = useRef(showToast);
    toastRef.current = showToast;
    const toast = useCallback((text, kind = 'success') => toastRef.current?.(text, kind), []);

    const request = useCallback(async (path, { method = 'GET', params, body } = {}) => {
        const query = params
            ? new URLSearchParams(Object.entries(params)
                .filter(([, v]) => v !== undefined && v !== null && v !== '')
                .map(([k, v]) => [k, String(v)])).toString()
            : '';
        const resp = await fetch(`${apiBaseUrl}${path}${query ? `?${query}` : ''}`, {
            method,
            credentials: 'include',
            headers: authHeaders(body === undefined ? {} : { 'Content-Type': 'application/json' }),
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            const error = new Error(data?.error || `HTTP ${resp.status}`);
            error.status = resp.status;
            throw error;
        }
        return data;
    }, [apiBaseUrl, authHeaders]);

    /* ─── сводка месяца: воронка, операторы, по дням, загрузки ───────────── */
    const [stats, setStats] = useState(null);
    const [statsScope, setStatsScope] = useState('');
    const [statsLoading, setStatsLoading] = useState(false);
    const [statsError, setStatsError] = useState('');
    const statsSeq = useRef(0);
    const pollCount = useRef(0);

    // quiet — опрос проверки загрузки: без мигания и без ошибки на экране.
    const loadStats = useCallback(async ({ quiet = false } = {}) => {
        if (!year) return null;
        const my = ++statsSeq.current;
        const at = scope;
        if (!quiet) setStatsLoading(true);
        try {
            const data = await request('/api/tez_leads/stats', { params: { year, month: monthNum } });
            if (my !== statsSeq.current || at !== scopeRef.current) return null;
            setStats(data);
            setStatsScope(at);
            setStatsError('');
            return data;
        } catch (e) {
            if (my !== statsSeq.current || at !== scopeRef.current || quiet) return null;
            setStatsError(e.message || 'Не удалось загрузить успешки');
            return null;
        } finally {
            if (my === statsSeq.current && at === scopeRef.current) setStatsLoading(false);
        }
    }, [request, scope, year, monthNum]);

    useEffect(() => {
        pollCount.current = 0;
        setStatsError('');
        loadStats();
    }, [loadStats]);

    const current = statsScope === scope ? stats : null;
    const operators = useMemo(() => (Array.isArray(current?.operators) ? current.operators : []), [current]);
    const batches = useMemo(() => (Array.isArray(current?.batches) ? current.batches : []), [current]);
    const checking = batches.some(batchCheckPending);

    useEffect(() => {
        if (!checking || pollCount.current >= CHECK_POLL_MAX) return undefined;
        const timer = setTimeout(() => {
            pollCount.current += 1;
            loadStats({ quiet: true });
        }, CHECK_POLL_MS);
        return () => clearTimeout(timer);
    }, [checking, current, loadStats]);

    /* ─── план отдела ────────────────────────────────────────────────────── */
    const [planState, setPlanState] = useState({ scope: '', plan: null, summary: null, error: '' });
    const planSeq = useRef(0);
    const loadPlan = useCallback(async () => {
        if (!year || !departmentId) return;
        const my = ++planSeq.current;
        const at = scope;
        try {
            const data = await request('/api/department_plan', { params: { department_id: departmentId, year, month: monthNum } });
            if (my !== planSeq.current || at !== scopeRef.current) return;
            setPlanState({ scope: at, plan: data.plan || null, summary: data.summary || null, error: '' });
        } catch (e) {
            if (my !== planSeq.current || at !== scopeRef.current) return;
            setPlanState({ scope: at, plan: null, summary: null, error: e.message || 'Не удалось загрузить план' });
        }
    }, [request, scope, departmentId, year, monthNum]);
    useEffect(() => { loadPlan(); }, [loadPlan]);
    const planInfo = planState.scope === scope ? planState : null;

    const [planEdit, setPlanEdit] = useState(null); // { year, month, plan, norm, error, busy }
    const openPlanEdit = () => {
        const saved = planInfo?.plan || {};
        setPlanEdit({
            year,
            month: monthNum,
            // Не заданный план сервер отдаёт нулём — поле лучше пустым, чем «0».
            plan: Number(saved.plan_per_fte) > 0 ? String(saved.plan_per_fte) : '',
            norm: saved.norm_hours_fte != null ? String(saved.norm_hours_fte) : '',
            error: '',
            busy: false,
        });
    };
    const savePlan = async () => {
        if (!planEdit || planEdit.busy) return;
        const error = planFormError(planEdit.plan, planEdit.norm);
        if (error) { setPlanEdit((p) => ({ ...p, error })); return; }
        setPlanEdit((p) => ({ ...p, busy: true, error: '' }));
        try {
            await request('/api/department_plan', {
                method: 'POST',
                body: planPayload(departmentId, planEdit.year, planEdit.month, planEdit.plan, planEdit.norm),
            });
            setPlanEdit(null);
            toast('План сохранён');
            loadPlan();
        } catch (e) {
            setPlanEdit((p) => (p ? { ...p, busy: false, error: e.message || 'Не удалось сохранить план' } : p));
        }
    };

    /* ─── действия месяца ────────────────────────────────────────────────── */
    const [recomputing, setRecomputing] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [leadsReload, setLeadsReload] = useState(0);

    const reloadAll = () => {
        loadStats();
        loadPlan();
        setLeadsReload((n) => n + 1);
    };

    const recompute = async () => {
        if (!year || recomputing) return;
        setRecomputing(true);
        const at = scope;
        try {
            const data = await request('/api/tez_leads/recompute', { method: 'POST', params: { year, month: monthNum } });
            toast(recomputeSummary(data));
            if (at === scopeRef.current) reloadAll();
        } catch (e) {
            toast(e.message || 'Не удалось выполнить сверку', 'error');
        } finally {
            setRecomputing(false);
        }
    };

    // Файл качаем запросом (blob), а не ссылкой: ссылка не несёт заголовков авторизации.
    const exportExcel = async () => {
        if (!year || exporting) return;
        setExporting(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/tez_leads/export?year=${year}&month=${monthNum}`, {
                credentials: 'include', headers: authHeaders(),
            });
            if (!resp.ok) {
                const data = await resp.json().catch(() => ({}));
                throw new Error(exportFailure(resp.status, data));
            }
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = exportFileName(year, monthNum);
            document.body.appendChild(link);
            link.click();
            link.remove();
            // Не сразу: часть браузеров ещё читает ссылку после click().
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (e) {
            toast(e.message && e.message !== 'Failed to fetch' ? e.message : 'Нет связи с сервером — попробуйте ещё раз', 'error');
        } finally {
            setExporting(false);
        }
    };

    /* ─── разрезы ────────────────────────────────────────────────────────── */
    const [view, setView] = useState('operators');
    const [leadsFilter, setLeadsFilter] = useState({ status: '', operator: '', q: '' });
    // Страница помнит свой месяц: другой месяц начинается с первой страницы сразу, без
    // лишнего запроса старой страницы под новый месяц.
    const [leadsPaging, setLeadsPaging] = useState({ scope: '', page: 1 });
    const leadsPage = leadsPaging.scope === scope ? leadsPaging.page : 1;
    const setLeadsPage = useCallback((page) => setLeadsPaging({ scope: scopeRef.current, page }), []);

    // Окно дня: данные — по нажатию, а не со сводкой: успешек за месяц сотни.
    const [day, setDay] = useState(null); // { date, loading, rows, error }
    const daySeq = useRef(0);
    const openDay = async (date) => {
        if (!year || !date) return;
        const my = ++daySeq.current;
        const at = scope;
        setDay({ date, loading: true, rows: [], error: '' });
        try {
            const data = await request('/api/tez_leads/day', { params: { year, month: monthNum, date } });
            if (my !== daySeq.current || at !== scopeRef.current) return;
            setDay({ date, loading: false, rows: data.successes || [], error: '' });
        } catch (e) {
            if (my !== daySeq.current || at !== scopeRef.current) return;
            setDay({ date, loading: false, rows: [], error: e.message || 'Не удалось загрузить успешки дня' });
        }
    };

    /* ─── загрузки: история, удаление и восстановление ────────────────────── */
    const [showDeleted, setShowDeleted] = useState(true);
    const [history, setHistory] = useState(null); // { batchId, loading, events, error }
    const historySeq = useRef(0);
    const [batchAction, setBatchAction] = useState(null); // { mode, batch, reason, error, busy }

    // Смена месяца делает чужими открытый день, историю и начатое действие.
    useEffect(() => {
        daySeq.current += 1;
        historySeq.current += 1;
        setDay(null);
        setHistory(null);
        setBatchAction(null);
    }, [scope]);

    const loadHistory = async (batchId) => {
        const my = ++historySeq.current;
        setHistory({ batchId, loading: true, events: [], error: '' });
        try {
            const data = await request(`/api/tez_leads/batches/${batchId}/history`);
            if (my !== historySeq.current) return;
            setHistory({ batchId, loading: false, events: data.events || [], error: '' });
        } catch (e) {
            if (my !== historySeq.current) return;
            setHistory({ batchId, loading: false, events: [], error: e.message || 'Не удалось загрузить историю' });
        }
    };
    const toggleHistory = (batchId) => {
        if (history?.batchId === batchId) {
            historySeq.current += 1;
            setHistory(null);
            return;
        }
        loadHistory(batchId);
    };

    const confirmBatchAction = async () => {
        if (!batchAction || batchAction.busy) return;
        const { mode, batch, reason } = batchAction;
        const error = batchReasonError(mode, reason);
        if (error) { setBatchAction((a) => ({ ...a, error })); return; }
        setBatchAction((a) => ({ ...a, busy: true, error: '' }));
        try {
            const data = await request(`/api/tez_leads/batches/${batch.id}/${mode}`, {
                method: 'POST',
                body: mode === 'delete' ? { reason: reason.trim() } : {},
            });
            setBatchAction(null);
            historySeq.current += 1;
            setHistory(null);
            toast(batchActionSummary(mode, batch, data));
            reloadAll();
        } catch (e) {
            if (e.status === 409) {
                // Загрузку уже удалил или вернул кто-то другой — показываем, как есть сейчас.
                setBatchAction(null);
                toast(`${e.message}. Список загрузок обновлён`, 'error');
                loadStats();
                return;
            }
            setBatchAction((a) => (a ? { ...a, busy: false, error: e.message || 'Не удалось выполнить действие' } : a));
        }
    };

    const deletedCount = batches.filter((b) => b.is_deleted).length;
    const visibleBatches = showDeleted ? batches : batches.filter((b) => !b.is_deleted);
    const monthTitle = monthLabel(month);
    const tiles = funnelTiles(current?.funnel);
    const planShown = Boolean(planInfo && !planInfo.error);
    const planTileList = planShown ? planTiles(planInfo.plan, planInfo.summary) : [];
    const planNoteText = planShown ? planNote(planInfo.summary) : '';
    const planKeyDown = (e) => { if (e.key === 'Enter') { e.preventDefault(); savePlan(); } };

    return (
        <section className="space-y-4">
            {/* Полоса: месяц слева, действия справа */}
            <div className="flex flex-wrap items-center justify-between gap-2">
                <MonthPicker value={month} onChange={(v) => onMonthChange?.(v)} allowFuture />
                <div className="flex flex-wrap items-center gap-2">
                    <button
                        type="button"
                        onClick={exportExcel}
                        disabled={exporting || !year}
                        className={iosBtnSecondary}
                        title="Лиды месяца со статусами, звонками и поездками — для разбора спорных случаев"
                    >
                        {exporting ? <Loader2 size={15} className="animate-spin" /> : <FileSpreadsheet size={15} />}
                        Excel
                    </button>
                    {canEdit && (
                        <button
                            type="button"
                            onClick={recompute}
                            disabled={recomputing || !year}
                            className={iosBtnSecondary}
                            title="То же, что ночной пересчёт: поездки в TEZ APP, звонки Binotel и успешки за выбранный месяц"
                        >
                            {recomputing ? <Loader2 size={15} className="animate-spin" /> : <ListChecks size={15} />}
                            {recomputing ? 'Сверяем…' : 'Сверить сейчас'}
                        </button>
                    )}
                    <button
                        type="button"
                        onClick={reloadAll}
                        disabled={statsLoading}
                        className={`${iosBtnGhost} shrink-0`}
                        aria-label="Обновить"
                        title="Обновить"
                    >
                        <RefreshCw size={15} className={statsLoading ? 'animate-spin' : ''} />
                    </button>
                </div>
            </div>

            {/* План отдела: что задано на месяц и как отдел его закрывает */}
            <section className="space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5">
                        <span className={iosGroupLabel}>План отдела</span>
                        <IosHint text={PLAN_HINT} />
                    </div>
                    {canEdit && planShown && (
                        <button type="button" onClick={openPlanEdit} className={iosBtnGhost}>
                            <Pencil size={14} /> Изменить
                        </button>
                    )}
                </div>
                <div className={`${iosCard} p-2`}>
                    {planInfo?.error ? (
                        <Failed text={planInfo.error} onRetry={loadPlan} />
                    ) : !planShown ? (
                        <Spinner text="Загружаем план…" />
                    ) : (
                        <>
                            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                                {planTileList.map((t, i) => (
                                    <Metric
                                        key={t.key}
                                        label={t.label}
                                        value={t.value}
                                        help={t.help || null}
                                        tone={t.tone || null}
                                        helpAlign={i >= planTileList.length - 2 ? 'right' : 'left'}
                                    />
                                ))}
                            </div>
                            {planNoteText && <div className="px-2 pb-1 pt-2 text-[12px] text-slate-500">{planNoteText}</div>}
                        </>
                    )}
                </div>
            </section>

            {/* Воронка базы месяца */}
            <section className="space-y-1.5">
                <div className="flex items-center gap-1.5">
                    <span className={iosGroupLabel}>Воронка базы месяца</span>
                    <IosHint text="Воронка по базе лидов выбранного месяца — её загружают во вкладке «База водителей». Успешка считается в месяце первой поездки водителя, поэтому здесь и успешки лидов прошлого месяца, выехавших в этом." />
                </div>
                {/* Обновить не вышло, а прежние цифры месяца есть — оставляем их и говорим, что они не свежие. */}
                {statsError && current && (
                    <div className="rounded-xl bg-rose-50 px-3.5 py-2 text-[12.5px] text-rose-700">Не удалось обновить: {statsError}</div>
                )}
                {statsError && !current ? (
                    <div className={iosCard}><Failed text={statsError} onRetry={() => loadStats()} /></div>
                ) : !current ? (
                    <div className={iosCard}><Spinner text="Считаем воронку…" /></div>
                ) : (
                    <div className={`${iosCard} grid grid-cols-2 gap-2 p-2 transition-opacity sm:grid-cols-3 lg:grid-cols-5 ${statsLoading ? 'opacity-60' : ''}`}>
                        {tiles.map((t, i) => (
                            <Metric
                                key={t.key}
                                label={t.label}
                                value={t.value}
                                hint={t.hint}
                                help={t.help}
                                tone={t.tone || null}
                                helpAlign={i >= tiles.length - 2 ? 'right' : 'left'}
                            />
                        ))}
                    </div>
                )}
            </section>

            {/* Разрезы */}
            <div className="space-y-3">
                <div className="-mx-1 overflow-x-auto px-1" style={{ scrollbarWidth: 'none' }}>
                    <IosSegmented value={view} onChange={setView} options={VIEWS} className="min-w-max" ariaLabel="Разрезы успешек" />
                </div>

                {view === 'operators' && (
                    <div className={`${iosCard} overflow-hidden`}>
                        {!current ? (
                            statsError ? <Failed text={statsError} onRetry={() => loadStats()} /> : <Spinner text="Загружаем…" />
                        ) : operators.length === 0 ? (
                            <div className="px-4 py-8 text-center text-[13px] text-slate-500">Успешек за {monthTitle || 'месяц'} пока нет</div>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full border-collapse">
                                    <thead className="border-b border-slate-100">
                                        <tr><Th>Оператор</Th><Th right>Успешки</Th><Th right>Первая</Th><Th right>Последняя</Th></tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-50">
                                        {operators.map((o) => (
                                            <tr key={o.operator_id ?? o.operator_name}>
                                                <Td><span className="whitespace-nowrap font-medium text-slate-800">{o.operator_name || '—'}</span></Td>
                                                <Td right><span className="font-semibold text-slate-900">{num(o.successes)}</span></Td>
                                                <Td right muted>{fmtDay(o.first_success) || '—'}</Td>
                                                <Td right muted>{fmtDay(o.last_success) || '—'}</Td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                )}

                {view === 'days' && (
                    <div className={`${iosCard} p-3`}>
                        <div className="mb-3 flex items-center gap-1.5 px-1 text-[12.5px] text-slate-500">
                            День — дата первой поездки водителя
                            <IosHint text="Успешка датируется днём, когда водитель выполнил первый заказ, — поэтому звонок мог быть и в прошлом месяце. Заливка показывает загрузку дня относительно самого результативного. Нажмите на день — откроются сами контакты, дошедшие до заказа." />
                        </div>
                        {!current || !year ? (
                            statsError ? <Failed text={statsError} onRetry={() => loadStats()} /> : <Spinner text="Загружаем…" />
                        ) : (
                            <SuccessCalendar
                                year={year}
                                month={monthNum}
                                byDay={current.by_day}
                                activeDate={day?.date || null}
                                onPick={openDay}
                            />
                        )}
                    </div>
                )}

                {view === 'leads' && year && (
                    <LeadsView
                        request={request}
                        scope={scope}
                        year={year}
                        month={monthNum}
                        operators={operators}
                        filter={leadsFilter}
                        onFilter={setLeadsFilter}
                        page={leadsPage}
                        onPage={setLeadsPage}
                        reloadKey={leadsReload}
                    />
                )}

                {view === 'batches' && (
                    <div className="space-y-1.5">
                        {deletedCount > 0 && (
                            <div className="flex flex-wrap items-center justify-between gap-2 px-1">
                                <span className="text-[12.5px] text-slate-500">
                                    Удалённых загрузок: {deletedCount} — они остаются в истории и возвращаются в один клик
                                </span>
                                <label className="flex shrink-0 cursor-pointer items-center gap-2 text-[12.5px] text-slate-600">
                                    Показывать удалённые
                                    <IosToggle checked={showDeleted} onChange={setShowDeleted} />
                                </label>
                            </div>
                        )}
                        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                            {!current ? (
                                statsError ? <Failed text={statsError} onRetry={() => loadStats()} /> : <Spinner text="Загружаем список…" />
                            ) : visibleBatches.length === 0 ? (
                                <div className="px-4 py-8 text-center text-[13px] text-slate-500">
                                    {deletedCount
                                        ? 'Все загрузки этого месяца удалены — включите «Показывать удалённые»'
                                        : 'Базу за этот месяц ещё не загружали — её загружают во вкладке «База водителей»'}
                                </div>
                            ) : visibleBatches.map((b) => (
                                <div key={b.id} className={b.is_deleted ? 'bg-rose-50/40' : ''}>
                                    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 py-3">
                                        <div className="min-w-0">
                                            <div className="flex min-w-0 items-center gap-2">
                                                <span className={`truncate text-[13.5px] font-medium ${b.is_deleted ? 'text-slate-400 line-through' : 'text-slate-800'}`}>
                                                    {b.file_name || 'файл'}
                                                </span>
                                                {b.is_deleted && <IosBadge tone="red">удалена</IosBadge>}
                                                {!b.is_deleted && b.restored_at && <IosBadge tone="green">восстановлена</IosBadge>}
                                            </div>
                                            <div className="text-[11.5px] text-slate-500">
                                                {fmtWhen(b.created_at)} · {b.uploaded_by_name || '—'} · строк {num(b.rows_total)}
                                            </div>
                                        </div>
                                        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                                            <IosBadge tone="green" title="Новых лидов в базе месяца">+{num(b.rows_new)}</IosBadge>
                                            {b.rows_duplicate > 0 && <IosBadge tone="slate" title="Номер уже был в базе месяца">повтор {num(b.rows_duplicate)}</IosBadge>}
                                            {b.rows_invalid > 0 && <IosBadge tone="red" title="Пустой или не казахстанский номер — в базу не попал">без номера {num(b.rows_invalid)}</IosBadge>}
                                            {!b.is_deleted && (
                                                <IosBadge
                                                    tone={b.check_status === 'error' ? 'red' : 'slate'}
                                                    title={b.check_status === 'error'
                                                        ? (b.check_error || 'Проверка не прошла')
                                                        : 'Сколько водителей этой загрузки выехали ещё до звонка — проверка по TEZ APP сразу после загрузки'}
                                                >
                                                    {batchCheckPending(b) && <Loader2 size={11} className="animate-spin" />}
                                                    {batchCheckText(b)}
                                                </IosBadge>
                                            )}
                                            <IosMenu
                                                label={`Действия с загрузкой ${b.file_name || ''}`}
                                                items={[
                                                    {
                                                        key: 'history',
                                                        label: history?.batchId === b.id ? 'Скрыть историю' : 'История удалений',
                                                        icon: History,
                                                        onSelect: () => toggleHistory(b.id),
                                                    },
                                                    canEdit && b.is_deleted && {
                                                        key: 'restore',
                                                        label: 'Восстановить',
                                                        icon: RotateCcw,
                                                        onSelect: () => setBatchAction({ mode: 'restore', batch: b, reason: '', error: '', busy: false }),
                                                    },
                                                    canEdit && !b.is_deleted && {
                                                        key: 'delete',
                                                        label: 'Удалить базу',
                                                        icon: Trash2,
                                                        danger: true,
                                                        separatorBefore: true,
                                                        onSelect: () => setBatchAction({ mode: 'delete', batch: b, reason: '', error: '', busy: false }),
                                                    },
                                                ]}
                                            />
                                        </div>
                                    </div>
                                    {b.is_deleted && (
                                        <div className="-mt-1 px-4 pb-3 text-[12px] text-rose-700">
                                            Удалил: {b.deleted_by_name || '—'}, {fmtWhen(b.deleted_at)}
                                            {b.delete_reason ? ` · причина: ${b.delete_reason}` : ' · причина не указана'}
                                        </div>
                                    )}
                                    {history?.batchId === b.id && <BatchHistory state={history} onRetry={() => loadHistory(b.id)} />}
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>

            {/* Окно дня */}
            <IosModal
                open={Boolean(day)}
                onClose={() => { daySeq.current += 1; setDay(null); }}
                title={dayTitle(day?.date)}
                subtitle={day?.loading ? 'Загружаем…' : `Успешек: ${num(day?.rows?.length)}`}
                maxWidth="max-w-2xl"
                sizeKey={day?.loading ? 'loading' : 'ready'}
            >
                {day?.loading ? (
                    <Spinner text="Загружаем успешки дня…" />
                ) : day?.error ? (
                    <div className="rounded-xl bg-rose-50 px-3.5 py-3 text-[13px] text-rose-700">{day.error}</div>
                ) : (day?.rows || []).length ? (
                    <div className="space-y-2">
                        {day.rows.map((row) => <DaySuccessRow key={`${row.phone}-${row.call_at || ''}`} row={row} />)}
                    </div>
                ) : (
                    <div className="py-10 text-center text-[13px] text-slate-500">За этот день успешек нет</div>
                )}
            </IosModal>

            {/* План на месяц */}
            <IosModal
                open={Boolean(planEdit)}
                onClose={() => { if (!planEdit?.busy) setPlanEdit(null); }}
                title="План отдела"
                subtitle={planEdit ? monthLabel(`${planEdit.year}-${String(planEdit.month).padStart(2, '0')}`) : undefined}
                maxWidth="max-w-md"
                footer={(
                    <>
                        <button type="button" onClick={() => setPlanEdit(null)} disabled={planEdit?.busy} className={iosBtnSecondary}>Отмена</button>
                        <button type="button" onClick={savePlan} disabled={planEdit?.busy} className={iosBtnPrimary}>
                            {planEdit?.busy && <Loader2 size={14} className="animate-spin" />}
                            Сохранить
                        </button>
                    </>
                )}
            >
                {planEdit && (
                    <div className="space-y-3">
                        <label className="block space-y-1.5">
                            <span className={`${FIELD_HEAD} gap-1.5`}>
                                План продаж на 1 FTE
                                <IosHint text={PLAN_HINT} label="Как считается индивидуальный план" />
                            </span>
                            <input
                                value={planEdit.plan}
                                onChange={(e) => setPlanEdit((p) => ({ ...p, plan: e.target.value, error: '' }))}
                                onKeyDown={planKeyDown}
                                inputMode="decimal"
                                placeholder="Например, 80"
                                className={iosInput}
                                autoFocus
                            />
                        </label>
                        <label className="block space-y-1.5">
                            <span className={FIELD_HEAD}>Норма часов на 1 FTE</span>
                            <input
                                value={planEdit.norm}
                                onChange={(e) => setPlanEdit((p) => ({ ...p, norm: e.target.value, error: '' }))}
                                onKeyDown={planKeyDown}
                                inputMode="decimal"
                                className={iosInput}
                            />
                        </label>
                        {planEdit.error &&<div role="alert" className="rounded-xl bg-rose-50 px-3 py-2 text-[12.5px] text-rose-700">{planEdit.error}</div>}
                    </div>
                )}
            </IosModal>

            {/* Удаление / восстановление загрузки */}
            <IosModal
                open={Boolean(batchAction)}
                onClose={() => { if (!batchAction?.busy) setBatchAction(null); }}
                title={batchAction?.mode === 'delete' ? 'Удалить загруженную базу?' : 'Восстановить базу?'}
                subtitle={batchAction?.batch?.file_name || undefined}
                maxWidth="max-w-md"
                footer={(
                    <>
                        <button type="button" onClick={() => setBatchAction(null)} disabled={batchAction?.busy} className={iosBtnSecondary}>Отмена</button>
                        <button
                            type="button"
                            onClick={confirmBatchAction}
                            disabled={batchAction?.busy}
                            className={batchAction?.mode === 'delete' ? BTN_DANGER : iosBtnPrimary}
                        >
                            {batchAction?.busy && <Loader2 size={14} className="animate-spin" />}
                            {batchAction?.mode === 'delete' ? 'Удалить' : 'Восстановить'}
                        </button>
                    </>
                )}
            >
                {batchAction && (
                    <div className="space-y-3">
                        <div className="text-[13px] text-slate-600">
                            {num(batchAction.batch.rows_total)} строк, загрузил {batchAction.batch.uploaded_by_name || '—'} {fmtWhen(batchAction.batch.created_at)}.
                        </div>
                        <div className={`rounded-xl px-3.5 py-2.5 text-[12.5px] leading-snug ${
                            batchAction.mode === 'delete' ? 'bg-amber-50 text-amber-800 ring-1 ring-amber-100' : 'bg-slate-50 text-slate-700 ring-1 ring-slate-200/70'
                        }`}>
                            {batchAction.mode === 'delete'
                                ? 'Лиды, пришедшие только из этой загрузки, уйдут из базы вместе с их успешками — воронка и операторы пересчитаются. Лиды, которые есть и в других загрузках, останутся. Действие обратимо: загрузка останется в списке с действием «Восстановить».'
                                : 'Лиды и успешки вернутся из архива. Если за это время тот же номер завела другая загрузка, лид не задвоится — строки подклеятся к существующему. После восстановления нажмите «Сверить сейчас».'}
                        </div>
                        {batchAction.mode === 'delete' && (
                            <label className="block space-y-1.5">
                                <span className={FIELD_HEAD}>Причина — попадёт в историю</span>
                                <input
                                    value={batchAction.reason}
                                    onChange={(e) => setBatchAction((a) => ({ ...a, reason: e.target.value, error: '' }))}
                                    onKeyDown={(e) => { if (e.key === 'Enter') confirmBatchAction(); }}
                                    maxLength={BATCH_REASON_MAX}
                                    placeholder="Например: загрузили не тот файл"
                                    className={iosInput}
                                    autoFocus
                                />
                            </label>
                        )}
                        {batchAction.error && <div role="alert" className="rounded-xl bg-rose-50 px-3 py-2 text-[12.5px] text-rose-700">{batchAction.error}</div>}
                    </div>
                )}
            </IosModal>
        </section>
    );
};

export default TezSuccessesPanel;
