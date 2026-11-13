import React, { useEffect, useRef, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { iosCard, iosGroupLabel } from '../ui/ios';
import { Empty, Loading } from '../hr_office/hrUi';
import { MetaChip, TonePill } from '../payments/paymentsUi';
import {
    ATTENTION_LABELS, QUICK_FILTERS, QUICK_TONE, deadlineCell, errorText, routeText, statusLabel, statusTone,
} from './mailMeta';

/*
 * «Обзор» (ТЗ п. 2: «KPI и просрочки»). Плитки — как в макете: «Готовы к отправке», «В пути»,
 * «Ожидают сверки», «Просрочено», «Проблемы»; нажатие открывает «Общий реестр» с этой
 * быстрой вкладкой. Ниже — что ждёт именно этого человека: сверить входящие, передать
 * курьеру, свои черновики, просрочено, проблемы. Пустые группы не рисуются.
 */
const TILE_LABELS = { to_send: 'Готовы к отправке' };

function Tile({ code, label, value, onClick }) {
    const tone = value > 0 ? QUICK_TONE[code] : '';
    return (
        <button type="button" onClick={onClick} data-mail-tile={code}
                className={`${iosCard} flex min-h-[96px] flex-col justify-between p-4 text-left transition hover:bg-slate-50/80 active:scale-[0.99]`}>
            <span className="text-[13px] font-medium text-slate-500">{label}</span>
            <span className={`text-[30px] font-semibold leading-none tabular-nums ${tone || 'text-slate-900'}`}>{value ?? 0}</span>
        </button>
    );
}

function AttentionRow({ shipment, onOpen }) {
    const deadline = deadlineCell(shipment);
    return (
        <button type="button" onClick={() => onOpen(shipment.id)}
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition hover:bg-slate-50/80">
            <span className="w-[132px] shrink-0 text-[13px] font-semibold tabular-nums text-slate-900">{shipment.number}</span>
            <span className="min-w-0 flex-1">
                <span className="block truncate text-[13.5px] text-slate-800">{routeText(shipment)}</span>
                <span className="block truncate text-[12px] text-slate-500">{shipment.items_summary}</span>
            </span>
            {deadline && <span className="max-sm:hidden"><MetaChip tone={deadline.tone}>{deadline.text}</MetaChip></span>}
            <TonePill tone={statusTone(shipment)} className="max-sm:hidden">{statusLabel(shipment.status)}</TonePill>
            <ChevronRight size={15} className="shrink-0 text-slate-300" />
        </button>
    );
}

export default function MailOverview({ api, version, onOpen, onQuick }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const ticketRef = useRef(0);

    useEffect(() => {
        const ticket = ticketRef.current + 1;
        ticketRef.current = ticket;
        api.get('/overview')
            .then(({ data: payload }) => { if (ticketRef.current === ticket) { setData(payload); setError(''); } })
            .catch((requestError) => { if (ticketRef.current === ticket) setError(errorText(requestError, 'Обзор не загрузился')); });
    }, [api, version]);

    if (error && !data) return <div className={`${iosCard} p-4 text-[13.5px] text-rose-600`}>{error}</div>;
    if (!data) return <Loading text="Считаем отправления…" />;

    return (
        <div className="flex flex-col gap-5">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
                {QUICK_FILTERS.map((filter) => (
                    <Tile key={filter.value} code={filter.value} label={TILE_LABELS[filter.value] || filter.label}
                          value={data.counters?.[filter.value]} onClick={() => onQuick(filter.value)} />
                ))}
            </div>
            <section className="space-y-2">
                <div className={iosGroupLabel}>Требуют внимания</div>
                {data.attention.length === 0 ? (
                    <div className={iosCard}><Empty>Сейчас ничего не ждёт вашего шага</Empty></div>
                ) : data.attention.map((group) => (
                    <div key={group.reason} className={`${iosCard} overflow-hidden`} data-mail-attention={group.reason}>
                        <div className="flex items-baseline justify-between border-b border-slate-100 px-4 py-2.5">
                            <span className="text-[14px] font-semibold text-slate-900">{ATTENTION_LABELS[group.reason]}</span>
                            <span className="text-[12.5px] tabular-nums text-slate-500">
                                {group.total > group.items.length ? `${group.items.length} из ${group.total}` : group.total}
                            </span>
                        </div>
                        <div className="divide-y divide-slate-100">
                            {group.items.map((shipment) => <AttentionRow key={shipment.id} shipment={shipment} onOpen={onOpen} />)}
                        </div>
                    </div>
                ))}
            </section>
        </div>
    );
}
