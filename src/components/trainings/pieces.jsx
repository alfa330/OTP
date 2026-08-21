import React from 'react';
import { Loader2, LayoutGrid, Rows3, CalendarDays, Search } from 'lucide-react';
import { iosCard, iosInput } from '../ui/ios';
import { VIEW_CARDS, VIEW_ROWS, VIEW_CALENDAR } from './constants';

/* Мелкие общие блоки раздела «Тренинги».
 *
 * Вынесены сюда, чтобы вкладки «По темам» и «По группам» выглядели одним
 * разделом, а не двумя похожими: пустое состояние, загрузка, переключатель
 * вида и полоса охвата в обеих используются буквально одинаково.
 */

/* Пустое состояние. Текст ОБЯЗАН различать «ничего не нашлось по фильтрам» и
 * «данных нет вовсе»: в первом случае человеку надо снять фильтр, во втором —
 * завести запись, и одна формулировка на два случая всегда врёт одному из них. */
export const EmptyBlock = ({ icon: Icon, title, text, children }) => (
    <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
        {Icon && (
            <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                <Icon size={22} />
            </div>
        )}
        <div className="text-[15px] font-semibold text-slate-900">{title}</div>
        {text && <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">{text}</p>}
        {children}
    </div>
);

export const LoadingBlock = ({ label = 'Загружаем…' }) => (
    <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-slate-400`}>
        <Loader2 size={16} className="animate-spin" />
        <span className="text-[13px]">{label}</span>
    </div>
);

export const ErrorBlock = ({ text, onRetry }) => (
    <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-12 text-center`}>
        <div className="text-[14px] font-semibold text-rose-600">Не удалось загрузить</div>
        <p className="max-w-sm text-[12.5px] leading-relaxed text-slate-500">{text}</p>
        {onRetry && (
            <button
                type="button"
                onClick={onRetry}
                className="mt-1 rounded-xl bg-slate-100 px-3.5 py-2 text-[12.5px] font-semibold text-slate-600 transition hover:bg-slate-200 active:scale-[0.98]"
            >
                Повторить
            </button>
        )}
    </div>
);

const VIEW_META = {
    [VIEW_CARDS]: { label: 'Карточки', icon: LayoutGrid },
    [VIEW_ROWS]: { label: 'Строки', icon: Rows3 },
    [VIEW_CALENDAR]: { label: 'Календарь', icon: CalendarDays },
};

/* Переключатель вида — иконками, без подписей: он стоит в плотной строке
 * фильтров, и три слова там были бы шумом. Подпись живёт в title/aria-label. */
export const ViewSwitcher = ({ value, onChange, views }) => (
    <div className="flex shrink-0 rounded-xl bg-slate-100 p-1">
        {views.map((key) => {
            const meta = VIEW_META[key];
            if (!meta) return null;
            const Icon = meta.icon;
            const active = value === key;
            return (
                <button
                    key={key}
                    type="button"
                    onClick={() => onChange(key)}
                    title={meta.label}
                    aria-label={meta.label}
                    aria-pressed={active}
                    className={`grid h-[34px] w-[34px] place-items-center rounded-[9px] transition-all ${
                        active
                            ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                            : 'text-slate-400 hover:text-slate-700'
                    }`}
                >
                    <Icon size={15} />
                </button>
            );
        })}
    </div>
);

export const SearchField = ({ value, onChange, placeholder, className = '' }) => (
    <div className={`relative min-w-[180px] flex-1 sm:max-w-[280px] ${className}`}>
        <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder={placeholder}
            className={`${iosInput} bg-white pl-9 ring-1 ring-slate-200/70`}
        />
    </div>
);

/* Полоса охвата. Цветом отмечено ровно одно состояние — «всё пройдено»:
 * незакрытый охват это не проблема, а обычный ход работы, и красить его
 * значило бы кричать на человека каждый раз, когда он открыл раздел. */
export const CoverageBar = ({ covered, audience, className = '' }) => {
    const total = Math.max(0, Number(audience) || 0);
    const done = Math.min(Math.max(0, Number(covered) || 0), total || Number.MAX_SAFE_INTEGER);
    const percent = total > 0 ? Math.round((done / total) * 100) : 0;
    const complete = total > 0 && done >= total;

    return (
        <div className={className}>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                <div
                    className={`h-full rounded-full transition-all ${complete ? 'bg-emerald-500' : 'bg-slate-400'}`}
                    style={{ width: `${total > 0 ? percent : 0}%` }}
                />
            </div>
        </div>
    );
};

/* Числовая плитка сводки. Числа — tabular-nums: иначе они «прыгают» при
 * пересчёте, и глаз каждый раз ищет строку заново. */
export const StatPair = ({ label, value, hint }) => (
    <div className="min-w-0">
        <div className="text-[11px] uppercase tracking-wider text-slate-400">{label}</div>
        <div className="truncate text-[13.5px] font-semibold tabular-nums text-slate-900" title={hint}>{value}</div>
    </div>
);
