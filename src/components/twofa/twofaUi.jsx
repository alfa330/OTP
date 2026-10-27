import React from 'react';
import { Search, ShieldCheck, X } from 'lucide-react';
import { IosHint } from '../ui/ios';
import { edgeMask, monogramOf } from './twofaMeta';

/*
 * Мелкие детали интерфейса «Кодов 2FA» — одни на экран и окна.
 *
 * Язык — тот же, что у «Списков Байги» и «Термокоробов»: белые поверхности с тонким
 * кольцом `ring-slate-200/70`, скругление `rounded-xl`/`rounded-2xl`, кнопки строки
 * инструментов высотой 36 px. Цвет — только со смыслом: янтарный у того, что ждёт,
 * зелёный у «готово», красный у того, что мешает получить код.
 */

export const TOOLBAR_BTN =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-white px-3.5 text-[13px] font-medium text-slate-700 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition hover:bg-slate-50 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';

export const TOOLBAR_BTN_PRIMARY =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-blue-600 px-3.5 text-[13px] font-semibold text-white shadow-sm transition hover:bg-blue-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';

/* Поля окон: подпись над полем, текст того, чего не хватает, — под ним, красным. */
export const FIELD_LABEL = 'mb-1.5 flex items-center gap-1.5 px-1 text-[12.5px] font-medium text-slate-600';
export const FIELD_ERROR = 'mt-1.5 px-1 text-[12px] text-rose-600';
export const INVALID_RING = 'ring-2 ring-rose-300';

const PILL_TONES = {
    amber: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200/80',
    green: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100',
    red: 'bg-rose-50 text-rose-700 ring-1 ring-rose-200/80',
    blue: 'bg-blue-50 text-blue-700 ring-1 ring-blue-100',
    slate: 'bg-slate-100 text-slate-600',
};

export const Pill = ({ tone = 'slate', children, className = '' }) => (
    <span className={`inline-flex max-w-full items-center gap-1 truncate rounded-full px-2 py-0.5 text-[12px] font-medium ${PILL_TONES[tone] || PILL_TONES.slate} ${className}`}>
        {children}
    </span>
);

/* Строка состояния с точкой — под аккаунтом и в карточке запроса. Переносится, а не
   режется многоточием: «код не дошёл: бот заблокирован» на телефоне длиннее строки, а
   причину человек должен прочитать целиком. */
export const StateLine = ({ tone = 'slate', children }) => {
    const dot = { amber: 'bg-amber-500', green: 'bg-emerald-500', red: 'bg-rose-500', slate: 'bg-slate-300' }[tone] || 'bg-slate-300';
    const text = { amber: 'text-amber-800', green: 'text-emerald-700', red: 'text-rose-700', slate: 'text-slate-500' }[tone] || 'text-slate-500';
    return (
        <span className={`inline-flex min-w-0 items-start gap-1.5 text-[12.5px] leading-snug ${text}`}>
            <span className={`mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
            <span className="min-w-0">{children}</span>
        </span>
    );
};

/* Шапка раздела: плитка-значок, заголовок, подзаголовок и действия справа.
   grow basis-0, а не flex-1: на телефоне оболочка ставит колонкой любой
   .flex.items-start с ребёнком .flex-1 (mobile-shell.css). */
export const SectionHeader = ({ subtitle, hint, actions }) => (
    <header className="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
        <div className="flex min-w-0 items-center gap-3">
            <span className="grid h-12 w-12 shrink-0 place-items-center rounded-[14px] bg-gradient-to-br from-sky-500 to-indigo-600 text-white shadow-[0_6px_16px_rgba(79,70,229,0.25)]">
                <ShieldCheck size={24} strokeWidth={2} />
            </span>
            <div className="min-w-0 grow basis-0">
                <h1 className="flex items-center gap-1.5 text-[22px] font-bold leading-tight tracking-tight text-slate-900">
                    Коды 2FA
                    {hint && <IosHint text={hint} label="Как это работает" />}
                </h1>
                <p className="mt-0.5 text-[13px] text-slate-500">{subtitle}</p>
            </div>
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
);

/* Подпись группы в стиле iOS «inset grouped»: мелко, заглавными, над карточкой. */
export const GroupLabel = ({ children, right }) => (
    <div className="mb-1.5 flex items-baseline justify-between gap-3 px-1">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{children}</span>
        {right}
    </div>
);

/* Плашка над содержимым: то, что мешает разделу работать, и что сделать. */
export const Notice = ({ tone = 'amber', icon: Icon, title, children, action }) => {
    const tones = {
        amber: 'bg-amber-50 text-amber-900 ring-amber-200',
        red: 'bg-rose-50 text-rose-900 ring-rose-200',
    };
    return (
        <div className={`mb-4 flex flex-wrap items-start gap-3 rounded-2xl px-4 py-3 ring-1 ${tones[tone] || tones.amber}`}>
            {Icon && <Icon size={18} className="mt-0.5 shrink-0" />}
            <div className="min-w-0 grow basis-0 text-[13px] leading-snug">
                {title && <div className="font-semibold">{title}</div>}
                {children && <div className={title ? 'mt-0.5' : ''}>{children}</div>}
            </div>
            {action}
        </div>
    );
};

export const EmptyState = ({ icon: Icon, title, children, action }) => (
    <div className="flex flex-col items-center px-6 py-12 text-center">
        {Icon && (
            <span className="mb-3 grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                <Icon size={22} />
            </span>
        )}
        <div className="text-[15px] font-semibold text-slate-800">{title}</div>
        {children && <p className="mt-1 max-w-sm text-[13px] text-slate-500">{children}</p>}
        {action && <div className="mt-4">{action}</div>}
    </div>
);

/* Карточка, которая открывается щелчком: под мышью приподнимается с тенью, под нажимом
   чуть проседает — как ячейки macOS. «Уменьшить движение» — без сдвига, только тень. */
export const LIFT = 'transition duration-200 ease-out hover:shadow-[0_8px_24px_rgba(15,23,42,0.08)] '
    + 'motion-safe:hover:-translate-y-px motion-safe:active:scale-[0.99]';

/* Плитка аккаунта: буква сервиса на мягком цвете (twofaMeta: monogramOf), как у сайтов в
   «Паролях» iOS. */
export const Monogram = ({ account, size = 'md' }) => {
    const { letter, tone } = monogramOf(account);
    const box = size === 'sm' ? 'h-8 w-8 rounded-[10px] text-[13px]' : 'h-10 w-10 rounded-xl text-[15px]';
    return <span aria-hidden="true" className={`grid shrink-0 place-items-center font-semibold ${box} ${tone}`}>{letter}</span>;
};

/* Поиск в строке инструментов — как поле поиска в панели macOS: лупа слева, крестик
   «очистить», когда что-то набрано. */
export const SearchField = ({ value, onChange, placeholder, className = '' }) => (
    <label className={`relative flex min-w-0 items-center ${className}`}>
        <Search size={15} className="pointer-events-none absolute left-3 text-slate-400" />
        <input type="text" inputMode="search" enterKeyHint="search" value={value} placeholder={placeholder}
               aria-label={placeholder} onChange={(event) => onChange(event.target.value)}
               className="h-9 w-full rounded-xl border-0 bg-white pl-9 pr-8 text-[13.5px] text-slate-900 placeholder-slate-400 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition focus:outline-none focus:ring-2 focus:ring-blue-500/60" />
        {value && (
            <button type="button" onClick={() => onChange('')} aria-label="Очистить поиск"
                    className="absolute right-2 grid h-5 w-5 place-items-center rounded-full bg-slate-300 text-white transition hover:bg-slate-400">
                <X size={11} strokeWidth={3} />
            </button>
        )}
    </label>
);

/* Разделы плитками: плитка — и отбор, и счётчик. Выбранная — мягкой голубой заливкой
   (владелец 16.10.2026: выбранное голубым, не чёрным), наведение — лёгкий фон. На телефоне
   — одна строка с прокруткой вбок: оболочка телефона сама переносит ряды с gap, поэтому
   !flex-nowrap; с sm — переносятся рядами. */
export const SectionTiles = ({ tiles, value, onChange }) => {
    const rowRef = React.useRef(null);
    const [edges, setEdges] = React.useState({ left: false, right: false });
    const measure = React.useCallback(() => {
        const row = rowRef.current;
        if (!row) return;
        const left = row.scrollLeft > 1;
        const right = row.scrollLeft + row.clientWidth < row.scrollWidth - 1;
        setEdges((prev) => (prev.left === left && prev.right === right ? prev : { left, right }));
    }, []);
    // До отрисовки: первый кадр уже с маской, а не без неё.
    React.useLayoutEffect(() => {
        measure();
        const row = rowRef.current;
        if (!row || typeof ResizeObserver === 'undefined') return undefined;
        const observer = new ResizeObserver(measure);
        observer.observe(row);
        return () => observer.disconnect();
    }, [measure, tiles]);
    // Выбор сменился не касанием («Искать во всех разделах», вход с запомненным разделом) —
    // выбранная плитка доезжает в вид. Двигается только лента, страница стоит: scrollIntoView
    // подтянул бы и её, если человек прокрутил ниже плиток.
    React.useEffect(() => {
        const row = rowRef.current;
        const tile = row?.querySelector('[aria-selected="true"]');
        if (!row || !tile || row.scrollWidth <= row.clientWidth) return;
        const box = row.getBoundingClientRect();
        const place = tile.getBoundingClientRect();
        const shift = place.left < box.left ? place.left - box.left - 12 : place.right > box.right ? place.right - box.right + 12 : 0;
        if (shift) row.scrollBy({ left: shift, behavior: 'smooth' });
    }, [value]);
    return (
        <div ref={rowRef} role="tablist" aria-label="Разделы" onScroll={measure} style={edgeMask(edges.left, edges.right)}
             className="thin-scroll flex !flex-nowrap gap-2 overflow-x-auto pb-1 sm:!flex-wrap sm:overflow-visible sm:pb-0">
            {tiles.map((tile) => {
                const selected = tile.key === value;
                return (
                    <button key={tile.key} type="button" role="tab" aria-selected={selected} onClick={() => onChange(tile.key)}
                            className={`flex max-w-[280px] shrink-0 items-center gap-2 rounded-xl px-3 py-2 text-left ring-1 transition duration-200 motion-safe:active:scale-[0.97] sm:max-w-[400px] ${
                                selected ? 'bg-[#eef4ff] text-[#1d4ed8] ring-[#bfd4fb]' : 'bg-white text-slate-700 ring-slate-200/70 hover:bg-slate-50'
                            }`}>
                        <span className="min-w-0 truncate text-[13px] font-semibold">{tile.title}</span>
                        <span className={`shrink-0 rounded-full px-1.5 text-[12px] font-medium tabular-nums ${
                            selected ? 'bg-white text-[#1d4ed8]' : 'bg-slate-100 text-slate-500'
                        }`}>
                            {tile.count}
                        </span>
                    </button>
                );
            })}
        </div>
    );
};

/* Заготовка до первого ответа сервера — того же роста, что настоящее: раздел при входе не
   перестраивается на глазах (память «раздел при входе не перестраивается», 13.10.2026). */
export const Shimmer = ({ className = '' }) => (
    <span aria-hidden="true" className={`block rounded-xl bg-slate-200/70 motion-safe:animate-pulse ${className}`} />
);
