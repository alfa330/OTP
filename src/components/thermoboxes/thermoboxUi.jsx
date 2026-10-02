import React from 'react';
import {
    Ban, ChartColumn, CircleCheck, CircleHelp, Clock, PackageOpen, TriangleAlert, Wallet,
} from 'lucide-react';
import { availabilityOf, fmtStamp, memoKindOf } from './thermoboxMeta';

/*
 * Общие детали оформления «Термокоробов» (вид «Светофор», выбран владельцем
 * 01.10.2026): цвет наличия, метка статуса, значок правила памятки, заголовок.
 *
 * Цвет — только со смыслом: зелёный — есть бесплатные коробы, жёлтый — только
 * Б/У или пакеты, красный — пусто; в условиях цветом отмечено лишь отличие
 * от общего правила («Все тарифы», «Без депозита», отдельное условие).
 */

export const TONES = {
    green: {
        dot: 'bg-emerald-500', pill: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200/80',
        edge: 'border-l-emerald-500', tile: 'bg-emerald-500', cell: 'bg-emerald-100 text-emerald-800',
    },
    amber: {
        dot: 'bg-amber-500', pill: 'bg-amber-50 text-amber-700 ring-1 ring-amber-200/80',
        edge: 'border-l-amber-400', tile: 'bg-amber-500', cell: 'bg-amber-100 text-amber-800',
    },
    red: {
        dot: 'bg-rose-500', pill: 'bg-rose-50 text-rose-700 ring-1 ring-rose-200/80',
        edge: 'border-l-rose-500', tile: 'bg-rose-500', cell: 'bg-rose-100 text-rose-800',
    },
    blue: {
        dot: 'bg-blue-500', pill: 'bg-blue-50 text-blue-700 ring-1 ring-blue-200/80',
        edge: 'border-l-blue-500', tile: 'bg-blue-500', cell: 'bg-blue-100 text-blue-800',
    },
    violet: {
        dot: 'bg-violet-500', pill: 'bg-violet-50 text-violet-700 ring-1 ring-violet-200/80',
        edge: 'border-l-violet-500', tile: 'bg-violet-500', cell: 'bg-violet-100 text-violet-800',
    },
    slate: {
        dot: 'bg-slate-400', pill: 'bg-slate-100 text-slate-600 ring-1 ring-slate-200/80',
        edge: 'border-l-slate-300', tile: 'bg-slate-400', cell: 'bg-slate-100 text-slate-800',
    },
};

export const Dot = ({ tone }) => <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${TONES[tone].dot}`} />;

/* withCount — «28 коробов» вместо «Есть коробы»: там, где отдельной колонки с
   числом нет (карточка на телефоне). Рядом с числом метка его не повторяет. */
export const StatusPill = ({ row, compact = false, withCount = false }) => {
    const state = availabilityOf(row);
    return (
        <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full font-semibold ${
            compact ? 'px-2 py-0.5 text-[11.5px]' : 'px-2.5 py-1 text-[12px]'
        } ${TONES[state.tone].pill}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${TONES[state.tone].dot}`} />
            {withCount ? state.counted : state.label}
        </span>
    );
};

/* Отличие от общего правила — пилюлей; обычное значение пишется текстом. */
export const Pill = ({ tone, children, size = 'md' }) => (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full font-semibold ${
        size === 'sm' ? 'px-2 py-0.5 text-[11.5px]' : 'px-2.5 py-0.5 text-[12px]'
    } ${TONES[tone].pill}`}>
        {children}
    </span>
);

export const MEMO_KIND_META = {
    forbidden: { icon: Ban, tone: 'red' },
    money: { icon: Wallet, tone: 'amber' },
    data: { icon: ChartColumn, tone: 'blue' },
    warning: { icon: TriangleAlert, tone: 'violet' },
    check: { icon: CircleCheck, tone: 'green' },
    question: { icon: CircleHelp, tone: 'slate' },
};

export const MemoIcon = ({ item, kind: forcedKind, size = 'md' }) => {
    const meta = MEMO_KIND_META[forcedKind || memoKindOf(item)] || MEMO_KIND_META.data;
    const Icon = meta.icon;
    return (
        <span className={`grid shrink-0 place-items-center text-white shadow-sm ${
            size === 'sm' ? 'h-7 w-7 rounded-lg' : 'h-9 w-9 rounded-[11px]'
        } ${TONES[meta.tone].tile}`}>
            <Icon size={size === 'sm' ? 14 : 17} strokeWidth={2.3} />
        </span>
    );
};

/* Заголовок раздела со значком — как иконка приложения в iOS.

   grow basis-0, а не flex-1: на телефоне оболочка ставит колонкой любой
   .flex.items-start с ребёнком .flex-1 и переносит строки с gap — значок
   уезжал бы над заголовком (mobile-shell.css). */
export const SectionTitle = ({ updated, actions }) => (
    <header className="mb-5 flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
        <div className="flex min-w-0 items-center gap-3">
            <span className="grid h-12 w-12 shrink-0 place-items-center rounded-[14px] bg-gradient-to-br from-sky-500 to-blue-600 text-white shadow-[0_6px_16px_rgba(37,99,235,0.28)]">
                <PackageOpen size={24} strokeWidth={2} />
            </span>
            <div className="min-w-0 grow basis-0">
                <h1 className="text-[22px] font-bold leading-tight tracking-tight text-slate-900">Термокороба</h1>
                <p className="mt-0.5 text-[13px] text-slate-500">Где есть коробы для курьеров и на каких условиях их выдают</p>
            </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
            {updated && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-[12.5px] text-slate-600 ring-1 ring-slate-200/80 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
                    <Clock size={13} className="text-slate-400" />
                    Обновлено {fmtStamp(updated.at)}{updated.by ? ` · ${updated.by}` : ''}
                </span>
            )}
            {actions}
        </div>
    </header>
);
