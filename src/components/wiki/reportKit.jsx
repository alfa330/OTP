import React from 'react';
import { iosCard, iosGroupLabel } from '../ui/ios';

/* Кирпичи отчётных экранов раздела «Вики»: плитка-показатель и таблица.
 *
 * Жили внутри WikiTrainerStats и оттуда не экспортировались. Со вторым отчётом
 * («Аналитика») выбор был из двух: скопировать их или вынести. Скопировать
 * значило бы завести две таблицы с одинаковым названием и разной вёрсткой —
 * расходиться они начали бы с первой же правки отступа, и это ровно тот сорт
 * расхождения, который замечают не разработчики, а заказчик на созвоне.
 *
 * Поэтому вынесено сюда, и оба экрана берут отсюда. Правило простое: правка
 * этого файла меняет ОБА отчёта раздела, и проверять надо оба.
 */

/** Плитка-показатель: подпись, число, необязательная оговорка под ним. */
export const Metric = ({ label, value, hint = null, tone = null }) => (
    <div className="rounded-xl bg-slate-50 px-3 py-2.5">
        <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
        <div className={`mt-0.5 text-[19px] font-semibold leading-none ${
            tone === 'bad' ? 'text-rose-600'
                : tone === 'warn' ? 'text-amber-600'
                    : tone === 'good' ? 'text-emerald-600' : 'text-slate-900'}`}>
            {value}
        </div>
        {hint && <div className="mt-1 text-[11.5px] text-slate-400">{hint}</div>}
    </div>
);

export const Th = ({ children, right = false }) => (
    <th className={`whitespace-nowrap px-3 py-2 text-[11.5px] font-medium uppercase
                    tracking-wide text-slate-400 ${right ? 'text-right' : 'text-left'}`}>
        {children}
    </th>
);

export const Td = ({ children, right = false, muted = false }) => (
    <td className={`px-3 py-2 text-[12.5px] ${right ? 'text-right tabular-nums' : ''}
                    ${muted ? 'text-slate-400' : 'text-slate-700'}`}>
        {children}
    </td>
);

/** Таблица с подписью и «пусто». */
export const Table = ({ title, icon: Icon, count, empty, head, children, hint = null }) => (
    <section className="space-y-1.5">
        <div className={iosGroupLabel}>
            {Icon && <Icon size={12} className="mr-1 inline align-[-1px]" />}
            {title}{count !== undefined ? ` · ${count}` : ''}
        </div>
        <div className={`${iosCard} overflow-hidden`}>
            {count === 0 ? (
                <p className="px-4 py-3 text-[12.5px] text-slate-400">{empty}</p>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full border-collapse">
                        <thead className="border-b border-slate-100">{head}</thead>
                        <tbody className="divide-y divide-slate-50">{children}</tbody>
                    </table>
                </div>
            )}
        </div>
        {hint && <div className="px-1 text-[11.5px] leading-relaxed text-slate-400">{hint}</div>}
    </section>
);

/** Доля в виде тонкой полосы. Число рядом обязательно: полоса показывает
 *  соотношение, но не величину, и «почти полная» при трёх строках из четырёх
 *  читается как успех. */
export const Bar = ({ done, total, tone = 'indigo' }) => {
    const pct = total ? Math.round((100 * done) / total) : 0;
    return (
        <div className="flex items-center justify-end gap-2">
            <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
                <div
                    className={`h-full rounded-full ${
                        tone === 'rose' ? 'bg-rose-400'
                            : tone === 'emerald' ? 'bg-emerald-400' : 'bg-indigo-400'}`}
                    style={{ width: `${pct}%` }}
                />
            </div>
            <span className="tabular-nums text-slate-500">{pct}%</span>
        </div>
    );
};
