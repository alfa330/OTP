import React from 'react';
import { CircleAlert, Loader2 } from 'lucide-react';

/*
 * Общие детали интерфейса раздела «Аренда авто» — одни на все вкладки.
 *
 * Язык — тот же, что у «Кодов 2FA» и «Термокоробов»: белые поверхности с тонким кольцом,
 * кнопки строки инструментов 36 px. Цвет — только со смыслом: красный у того, что мешает
 * (офлайн, «нет в наличии»), янтарный у того, что ждёт внимания, зелёный у «есть свободные».
 * Градиентов нет (владелец 27.10.2026: «не нравится градиент»).
 */

export const TOOLBAR_BTN =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-white px-3.5 text-[13px] font-medium text-slate-700 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition hover:bg-slate-50 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';

export const TOOLBAR_BTN_PRIMARY =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-blue-600 px-3.5 text-[13px] font-semibold text-white shadow-sm transition hover:bg-blue-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';

export const ICON_BTN =
    'grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 active:scale-95 disabled:opacity-40';

const PILL_TONES = {
    green: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100',
    red: 'bg-rose-50 text-rose-700 ring-1 ring-rose-200/80',
    amber: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200/80',
    blue: 'bg-blue-50 text-blue-700 ring-1 ring-blue-100',
    slate: 'bg-slate-100 text-slate-600',
};

export const Pill = ({ tone = 'slate', children, className = '' }) => (
    <span className={`inline-flex max-w-full items-center gap-1 truncate rounded-full px-2 py-0.5 text-[12px] font-medium ${PILL_TONES[tone] || PILL_TONES.slate} ${className}`}>
        {children}
    </span>
);

export const Notice = ({ tone = 'amber', title, children, action }) => {
    const tones = {
        amber: 'bg-amber-50/80 ring-amber-200/70 text-amber-900',
        red: 'bg-rose-50/80 ring-rose-200/70 text-rose-900',
        slate: 'bg-white ring-slate-200/70 text-slate-700',
    };
    return (
        <div className={`flex flex-wrap items-center gap-x-4 gap-y-2 rounded-2xl px-4 py-3 ring-1 ${tones[tone] || tones.slate}`}>
            <div className="flex min-w-0 grow basis-0 items-start gap-2.5">
                {tone === 'slate'
                    ? <Loader2 size={16} className="mt-0.5 shrink-0 animate-spin text-slate-400" />
                    : <CircleAlert size={16} className="mt-0.5 shrink-0 opacity-70" />}
                <div className="min-w-0 text-[13px] leading-snug">
                    {title && <div className="font-semibold">{title}</div>}
                    {children && <div className="opacity-90">{children}</div>}
                </div>
            </div>
            {action}
        </div>
    );
};

export const Loading = ({ text }) => (
    <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-slate-500">
        <Loader2 size={15} className="animate-spin" /> {text}
    </div>
);

/* История правок: кто, когда, что было и что стало — одна строка на изменение. */
export const HistoryList = ({ items, shortTime }) => {
    if (!items) return <Loading text="Загружаем историю…" />;
    if (!items.length) return <div className="py-8 text-center text-[13px] text-slate-500">Изменений пока не было</div>;
    return (
        <ul className="divide-y divide-slate-100">
            {items.map((item, index) => (
                <li key={`${item.at}-${index}`} className="flex flex-col gap-0.5 py-2.5">
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-[12.5px] text-slate-500">
                        <span className="font-medium text-slate-700">{item.label}</span>
                        <span className="tabular-nums">{item.by ? `${item.by} · ` : ''}{shortTime(item.at)}</span>
                    </div>
                    <div className="text-[13px] text-slate-800">
                        {item.old ? <span className="text-slate-400 line-through decoration-slate-300">{item.old}</span> : null}
                        {item.old && item.new ? <span className="px-1.5 text-slate-300">→</span> : null}
                        {item.new ?? (item.old ? '' : '—')}
                    </div>
                </li>
            ))}
        </ul>
    );
};
