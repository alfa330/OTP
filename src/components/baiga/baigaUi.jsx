import React, { useEffect, useRef, useState } from 'react';
import { Check, Copy, Trophy } from 'lucide-react';
import { formatMoney } from './baigaMeta';

/*
 * Мелкие детали интерфейса «Списков Байги» — одни на экран, карточку водителя
 * и журнал, чтобы кнопки, пилюли и шапка выглядели одинаково везде.
 *
 * Язык — тот же, что у «Термокоробов» и «Посылок»: белые поверхности с тонким
 * кольцом `ring-slate-200/70`, скругление `rounded-xl`/`rounded-2xl`, мягкие
 * тени. Высота строки инструментов — 36 px, ровно как у CustomSelect в виде
 * ios: поиск, выбор недели и кнопки стоят одной линией.
 */

/* Кнопка строки инструментов. Своя строка классов, а не утилита поверх
   iosBtnSecondary: поверх готового ios-класса Tailwind высоту не перебивает. */
export const TOOLBAR_BTN =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-white px-3.5 text-[13px] font-medium text-slate-700 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition hover:bg-slate-50 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';

/* Включённое состояние — подсветка, как у нажатой кнопки панели macOS, а не
   сплошная синяя заливка: «Фильтры · 2» — состояние, а не главное действие. */
export const TOOLBAR_BTN_ON =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-blue-50 px-3.5 text-[13px] font-semibold text-blue-700 ring-1 ring-blue-200 transition hover:bg-blue-100 active:scale-[0.98]';

/* Главное действие той же высоты (36 px) — «Загрузить неделю» в шапке. */
export const TOOLBAR_BTN_PRIMARY =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-blue-600 px-3.5 text-[13px] font-semibold text-white shadow-sm transition hover:bg-blue-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';

/* Шапка раздела: плитка-значок, заголовок, подзаголовок и действия справа.
   grow basis-0, а не flex-1: на телефоне оболочка ставит колонкой любой
   .flex.items-start с ребёнком .flex-1 (mobile-shell.css), и значок уезжал бы
   над заголовком. */
export const SectionHeader = ({ subtitle, badge, actions }) => (
    <header className="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
        <div className="flex min-w-0 items-center gap-3">
            <span className="grid h-12 w-12 shrink-0 place-items-center rounded-[14px] bg-gradient-to-br from-amber-400 to-orange-500 text-white shadow-[0_6px_16px_rgba(245,158,11,0.28)]">
                <Trophy size={24} strokeWidth={2} />
            </span>
            <div className="min-w-0 grow basis-0">
                <h1 className="text-[22px] font-bold leading-tight tracking-tight text-slate-900">Списки Байги</h1>
                <p className="mt-0.5 text-[13px] text-slate-500">{subtitle}</p>
            </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
            {badge}
            {actions}
        </div>
    </header>
);

/* Пилюля в шапке — «какая неделя загружена последней», как «Обновлено» у
   «Термокоробов». */
export const HeaderPill = ({ icon: Icon, children }) => (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-[12.5px] text-slate-600 ring-1 ring-slate-200/80 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        {Icon && <Icon size={13} className="text-slate-400" />}
        {children}
    </span>
);

/* Приз — единственное, что в разделе окрашено: в акции это и есть результат. */
export const PrizePill = ({ row, size = 'md' }) => {
    if (!row?.has_prize) return <span className="text-slate-300">—</span>;
    const text = row.prize_amount ? formatMoney(row.prize_amount) : row.prize_name;
    return (
        <span
            className={`inline-flex max-w-[200px] items-center truncate rounded-lg bg-emerald-50 font-semibold tabular-nums text-emerald-700 ring-1 ring-emerald-100 ${
                size === 'sm' ? 'px-1.5 py-0.5 text-[12px]' : 'px-2 py-0.5 text-[12.5px]'
            }`}
            title={row.prize_name}
        >
            {text}
        </span>
    );
};

/* Кнопка «скопировать»: на нажатие значок на секунду становится галочкой —
   как в iOS, без тоста на каждое копирование. */
export const CopyButton = ({ value, label = 'Скопировать', children, className = '', onCopied }) => {
    const [done, setDone] = useState(false);
    const timer = useRef(null);
    useEffect(() => () => clearTimeout(timer.current), []);
    const copy = async (event) => {
        event.stopPropagation();
        try {
            await navigator.clipboard.writeText(String(value || ''));
            setDone(true);
            clearTimeout(timer.current);
            timer.current = setTimeout(() => setDone(false), 1200);
            onCopied?.(true);
        } catch (error) {
            onCopied?.(false);
        }
    };
    return (
        <button type="button" onClick={copy} title={label} aria-label={label}
                className={`group inline-flex items-center gap-1.5 rounded-md transition hover:text-slate-900 ${className}`}>
            {children}
            {done
                ? <Check size={13} className="shrink-0 text-emerald-500" />
                : <Copy size={12} className="shrink-0 text-slate-300 transition group-hover:text-slate-500" />}
        </button>
    );
};

/* Пилюля статуса недели в журнале: зелёная — неделя в поиске; остальные — серые. */
export const StatusPill = ({ status }) => {
    const active = status === 'active';
    const label = { active: 'В поиске', replaced: 'Заменена', deleted: 'Удалена' }[status] || status;
    return (
        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11.5px] font-medium ${
            active ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100' : 'bg-slate-100 text-slate-500'
        }`}>
            {label}
        </span>
    );
};

/* Подпись группы в стиле iOS «inset grouped»: мелко, заглавными, над карточкой. */
export const GroupLabel = ({ children, right }) => (
    <div className="mb-1.5 flex items-baseline justify-between gap-3 px-1">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{children}</span>
        {right}
    </div>
);
