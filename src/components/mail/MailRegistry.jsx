import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Download, Loader2, Search } from 'lucide-react';
import { IosPager, iosCard } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import { saveBlobResponse, blobErrorText } from '../hr_office/hrOffice';
import { Empty, Loading, TOOLBAR_BTN } from '../hr_office/hrUi';
import { MetaChip, TonePill } from '../payments/paymentsUi';
import {
    EVENT_FILTERS, QUICK_FILTERS, STATUS_OPTIONS, dateTimeText, deadlineCell, errorText, routeText, statusLabel, statusTone,
} from './mailMeta';

/*
 * Реестр отправлений (ТЗ п. 9) — одна разметка на «Исходящие», «Входящие», «Общий реестр» и
 * «Проблемы и возвраты»: вкладка отличается только областью (scope), которую режет сервер.
 * Колонки — как в макете: № · Откуда → куда · Документы · Передано курьеру · Трек · Срок · Статус.
 * Быстрые вкладки — «Требуют отправки», «В пути», «Ожидают сверки», «Просрочено», «Проблемы»;
 * числа у них — по той же области и тому же поиску, что и список.
 */
const PAGE_SIZE = 50;

const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-white px-3 py-2 '
    + 'text-left text-[12.5px] font-medium text-slate-700 ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all hover:bg-slate-50 '
    + 'active:scale-[0.99] focus:outline-none focus:ring-2 focus:ring-blue-500/60 '
    + '[&>span]:flex-1 [&>span]:text-left [&>span]:truncate';

const shiftDays = (days) => {
    const date = new Date();
    date.setDate(date.getDate() - days);
    return isoDate(date);
};
const DATE_PRESETS = [
    { label: 'Неделя', range: () => ({ from: shiftDays(6), to: isoDate(new Date()) }) },
    { label: 'Месяц', range: () => ({ from: shiftDays(29), to: isoDate(new Date()) }) },
    { label: 'Квартал', range: () => ({ from: shiftDays(89), to: isoDate(new Date()) }) },
];

/* Выбранное — мягкой голубой заливкой (как метки «Продакшна»), а не чёрным. */
const CHIP = 'inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-[12.5px] font-medium ring-1 transition active:scale-[0.97]';
const CHIP_IDLE = 'bg-white text-slate-600 ring-slate-200/70 hover:bg-[#f6f6f4]';
const CHIP_ON = 'bg-[#eef4ff] text-[#1d4ed8] ring-[#bfd4fb]';
const COUNT_TONE = { overdue: 'text-rose-600', problems: 'text-amber-700' };

function QuickChips({ value, onChange, counters }) {
    const options = [{ value: null, label: 'Все' }].concat(QUICK_FILTERS);
    return (
        <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Быстрые вкладки реестра">
            {options.map((option) => {
                const on = value === option.value;
                const count = option.value ? counters?.[option.value] : counters?.all;
                return (
                    <button key={option.value || 'all'} type="button" role="tab" aria-selected={on}
                            className={`${CHIP} ${on ? CHIP_ON : CHIP_IDLE}`} onClick={() => onChange(option.value)}>
                        {option.label}
                        {count !== undefined && (
                            <span className={`tabular-nums ${on ? '' : (count > 0 && COUNT_TONE[option.value]) || 'text-slate-400'}`}>
                                {count}
                            </span>
                        )}
                    </button>
                );
            })}
        </div>
    );
}

const GRID = 'sm:grid sm:grid-cols-[minmax(0,1.05fr)_minmax(0,1.25fr)_minmax(0,1.6fr)_minmax(0,0.9fr)_minmax(0,0.8fr)_minmax(0,1.15fr)_minmax(0,1.1fr)] sm:items-center sm:gap-3';

function ShipmentRow({ shipment, onOpen }) {
    const deadline = deadlineCell(shipment);
    const received = shipment.received_total !== null && shipment.received_total !== undefined
        && shipment.received_total !== shipment.qty_total;
    return (
        <button type="button" onClick={() => onOpen(shipment.id)} data-mail-row={shipment.number}
                className={`block w-full px-4 py-3 text-left transition hover:bg-slate-50/80 active:bg-slate-100/70 ${GRID}`}>
            <div className="flex items-center justify-between gap-2 sm:block">
                <div className="text-[13.5px] font-semibold tabular-nums text-slate-900">{shipment.number}</div>
                <div className="text-[12px] tabular-nums text-slate-400 max-sm:hidden">{dateTimeText(shipment.created_at).slice(0, 10)}</div>
                <TonePill tone={statusTone(shipment)} className="sm:hidden">{statusLabel(shipment.status)}</TonePill>
            </div>
            <div className="min-w-0">
                <div className="truncate text-[13.5px] text-slate-900">{routeText(shipment)}</div>
                <div className="truncate text-[12px] text-slate-500">
                    {[shipment.sender_office_name, shipment.recipient_office_name].filter(Boolean).join(' → ')}
                </div>
            </div>
            <div className="min-w-0">
                <div className="truncate text-[13.5px] text-slate-800" title={shipment.items_summary}>{shipment.items_summary || '—'}</div>
                {received && (
                    <div className="text-[12px] tabular-nums text-amber-700">получено {shipment.received_total} из {shipment.qty_total}</div>
                )}
            </div>
            <div className="text-[13px] tabular-nums text-slate-700 max-sm:hidden">{dateTimeText(shipment.handed_at) || '—'}</div>
            <div className="truncate text-[13px] tabular-nums text-slate-700 max-sm:hidden">{shipment.track_number || '—'}</div>
            <div className="min-w-0 max-sm:mt-1">
                {deadline ? <MetaChip tone={deadline.tone}>{deadline.text}</MetaChip>
                    : <span className="text-[13px] text-slate-400 max-sm:hidden">—</span>}
            </div>
            <div className="max-sm:hidden"><TonePill tone={statusTone(shipment)}>{statusLabel(shipment.status)}</TonePill></div>
        </button>
    );
}

export default function MailRegistry({ api, scope, version, onOpen, toast, quickRequest }) {
    const [quick, setQuick] = useState(null);
    const [status, setStatus] = useState('');
    const [query, setQuery] = useState('');
    const [debounced, setDebounced] = useState('');
    // Отбор по дате события: что именно произошло в выбранные дни (создано, передано, вручено, сверено).
    const [eventKind, setEventKind] = useState('created');
    const [created, setCreated] = useState({ from: '', to: '' });
    const [docPeriod, setDocPeriod] = useState({ from: '', to: '' });
    const [page, setPage] = useState(1);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const [exporting, setExporting] = useState(false);
    const requestRef = useRef(0);

    useEffect(() => {
        const timer = window.setTimeout(() => setDebounced(query.trim()), 300);
        return () => window.clearTimeout(timer);
    }, [query]);
    useEffect(() => { setPage(1); }, [quick, status, debounced, eventKind, created.from, created.to, docPeriod.from, docPeriod.to]);
    useEffect(() => {
        if (quickRequest?.token) setQuick(quickRequest.value);
    }, [quickRequest?.token]); // eslint-disable-line react-hooks/exhaustive-deps

    const params = useCallback((extra = {}) => {
        const result = { scope, ...extra };
        if (quick) result.quick = quick;
        if (status) result.status = status;
        if (debounced) result.q = debounced;
        if (created.from || created.to) result.event = eventKind;
        if (created.from) result.event_from = created.from;
        if (created.to) result.event_to = created.to;
        if (docPeriod.from) result.doc_from = docPeriod.from;
        if (docPeriod.to) result.doc_to = docPeriod.to;
        return result;
    }, [scope, quick, status, debounced, eventKind, created, docPeriod]);

    useEffect(() => {
        const ticket = requestRef.current + 1;
        requestRef.current = ticket;
        setLoading(true);
        api.get('/shipments', params({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }))
            .then(({ data: payload }) => {
                if (requestRef.current !== ticket) return;
                setData(payload);
                setError('');
            })
            .catch((requestError) => {
                if (requestRef.current === ticket) setError(errorText(requestError, 'Реестр не загрузился'));
            })
            .finally(() => { if (requestRef.current === ticket) setLoading(false); });
    }, [api, params, page, version]);

    const exportXlsx = async () => {
        setExporting(true);
        try {
            const response = await api.blob('/export', params());
            saveBlobResponse(response, 'Почта.xlsx');
        } catch (requestError) {
            toast?.(await blobErrorText(requestError, 'Файл не собрался'), 'error');
        } finally {
            setExporting(false);
        }
    };

    const total = data?.total || 0;
    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
    const filtered = Boolean(quick || status || debounced || created.from || created.to || docPeriod.from || docPeriod.to);

    return (
        <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
                <QuickChips value={quick} onChange={setQuick} counters={data?.counters} />
                <button type="button" className={`${TOOLBAR_BTN} ml-auto`} onClick={exportXlsx} disabled={exporting || !total}>
                    {exporting ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />} Excel
                </button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <div className="relative w-full sm:w-auto sm:min-w-[220px] sm:max-w-sm sm:flex-1">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)}
                           placeholder="Поиск: номер, трек, город, ФИО, документ"
                           className="h-9 w-full rounded-xl border-0 bg-white pl-9 pr-3 text-[13.5px] text-slate-900 ring-1 ring-slate-200/70 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70" />
                </div>
                <CustomSelect variant="ios" value={status} options={STATUS_OPTIONS} onChange={(value) => setStatus(value || '')}
                              className="w-[200px]" ariaLabel="Статус" />
                <div className="flex items-center gap-1.5">
                    <CustomSelect variant="ios" value={eventKind} options={EVENT_FILTERS} onChange={(value) => setEventKind(value || 'created')}
                                  className="w-[170px]" ariaLabel="Событие для отбора по дате" />
                    <div className="w-[210px]">
                        <IosDateRangePicker from={created.from} to={created.to} max={isoDate(new Date())} presets={DATE_PRESETS}
                                            triggerClassName={DATE_TRIGGER}
                                            onChange={({ from, to }) => setCreated({ from: from || '', to: to || '' })} />
                    </div>
                </div>
                <div className="flex items-center gap-1.5">
                    <span className="text-[12px] text-slate-500">Период документа</span>
                    <div className="w-[210px]">
                        <IosDateRangePicker from={docPeriod.from} to={docPeriod.to} triggerClassName={DATE_TRIGGER}
                                            onChange={({ from, to }) => setDocPeriod({ from: from || '', to: to || '' })} />
                    </div>
                </div>
                {loading && data && <Loader2 size={15} className="animate-spin text-slate-400" aria-label="Обновляем" />}
            </div>

            {error && <div className={`${iosCard} p-4 text-[13.5px] text-rose-600`}>{error}</div>}
            {!data && !error && <Loading text="Загружаем реестр…" />}
            {data && (
                <div className={`${iosCard} overflow-hidden`}>
                    <div className={`hidden border-b border-slate-100 px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400 ${GRID}`}>
                        <span>№ отправления</span>
                        <span>Откуда → куда</span>
                        <span>Документы</span>
                        <span>Передано курьеру</span>
                        <span>Трек</span>
                        <span>Срок</span>
                        <span>Статус</span>
                    </div>
                    <div className="divide-y divide-slate-100">
                        {data.items.length === 0
                            ? <Empty>{filtered ? 'Под отбор ничего не попало' : 'Отправлений пока нет'}</Empty>
                            : data.items.map((shipment) => <ShipmentRow key={shipment.id} shipment={shipment} onOpen={onOpen} />)}
                    </div>
                </div>
            )}
            {data && pageCount > 1 && (
                <IosPager page={page} pageCount={pageCount} total={total} from={(page - 1) * PAGE_SIZE + 1}
                          to={Math.min(total, page * PAGE_SIZE)} onPage={setPage} unit="отправления" />
            )}
        </div>
    );
}
