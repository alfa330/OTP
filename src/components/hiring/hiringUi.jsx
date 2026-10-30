import React, { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, CircleAlert, Loader2, SlidersHorizontal } from 'lucide-react';
import { IosHint, IosModal, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosGroupLabel } from '../ui/ios';
import { monthLabel, shiftMonth } from './hiringMeta';

/*
 * Общие детали интерфейса раздела «Воронка найма» — одни на все вкладки.
 *
 * Язык — тот же, что у «Аренды авто» и «Кодов 2FA»: белые поверхности с тонким кольцом,
 * кнопки строки инструментов 36 px, сплошные цвета без градиентов (владелец 27.10.2026).
 * Цвет — только со смыслом: красный у завершений и просрочки, зелёный у закрытого найма и
 * дозвона, янтарный у того, что ждёт внимания. Нейтральное не красится.
 */

export const TOOLBAR_BTN =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-white px-3.5 text-[13px] font-medium text-slate-700 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition hover:bg-slate-50 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';

export const TOOLBAR_BTN_PRIMARY =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-blue-600 px-3.5 text-[13px] font-semibold text-white shadow-sm transition hover:bg-blue-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';

export const ICON_BTN =
    'grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 active:scale-95 disabled:opacity-40';

export const DANGER_BTN =
    'inline-flex items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 py-2.5 text-[13.5px] font-semibold text-white shadow-sm transition-all hover:bg-rose-700 active:scale-[0.98] disabled:opacity-50';

/* Действие строки, которое нужно не всегда: там, где есть наведение, появляется по нему (и по
   фокусу с клавиатуры), на сенсорных экранах видно сразу — наведения там нет. Родитель — `group`. */
export const HOVER_REVEAL =
    '[@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 [@media(hover:hover)]:group-focus-within:opacity-100';

export const FIELD =
    'w-full rounded-xl border-0 bg-slate-100 px-3.5 py-2.5 text-[14px] text-slate-900 placeholder-slate-400 transition focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70';

export const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 text-[14px] tabular-nums '
    + 'text-slate-900 transition hover:bg-slate-200/70 focus:outline-none focus:ring-2 focus:ring-blue-500/70 '
    + '[&>span]:flex-1 [&>span]:text-left';
export const TIME_INPUT = 'w-full rounded-xl border-0 bg-slate-100 px-6 py-2.5 text-center text-[14px] tabular-nums text-slate-900 '
    + 'placeholder-slate-400 transition hover:bg-slate-200/70 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70';

const PILL_TONES = {
    green: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100',
    red: 'bg-rose-50 text-rose-700 ring-1 ring-rose-200/80',
    amber: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200/80',
    blue: 'bg-blue-50 text-blue-700 ring-1 ring-blue-100',
    slate: 'bg-slate-100 text-slate-600',
};

export const Pill = ({ tone = 'slate', children, className = '', title }) => (
    <span title={title} className={`inline-flex max-w-full items-center gap-1 truncate rounded-full px-2 py-0.5 text-[12px] font-medium ${PILL_TONES[tone] || PILL_TONES.slate} ${className}`}>
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

export const Empty = ({ children }) => (
    <div className="py-10 text-center text-[13px] text-slate-500">{children}</div>
);

/* Подпись поля формы с необязательной подсказкой под «i» (владелец: пояснение — под «i»
   у метки, не строкой под полем). */
export const Label = ({ children, hint, htmlFor, required }) => (
    <label htmlFor={htmlFor} className="mb-1 flex items-center gap-1 text-[12px] font-medium text-slate-500">
        {children}{required && <span className="text-rose-500">*</span>}
        {hint && <IosHint text={hint} label="Что это" />}
    </label>
);

export const Group = ({ title, right, children }) => (
    <div className="space-y-1.5">
        {(title || right) && (
            <div className="flex items-end justify-between gap-2">
                {title ? <div className={iosGroupLabel}>{title}</div> : <span />}
                {right}
            </div>
        )}
        {children}
    </div>
);

/* Плитка показателя: число tabular-nums, подпись серым; tone — только когда число что-то
   значит (просрочка, перерасход). */
export const Stat = ({ label, value, unit = '', hint, tone, onClick, sub }) => {
    const Tag = onClick ? 'button' : 'div';
    const toneClass = tone === 'red' ? 'text-rose-600' : tone === 'green' ? 'text-emerald-600' : tone === 'amber' ? 'text-amber-600' : 'text-slate-900';
    return (
        <Tag type={onClick ? 'button' : undefined} onClick={onClick}
             className={`flex min-w-0 flex-col rounded-2xl bg-white px-4 py-3 text-left ring-1 ring-slate-200/70 ${onClick ? 'transition hover:bg-slate-50 active:scale-[0.99]' : ''}`}>
            <span className="flex items-center gap-1 truncate text-[12px] text-slate-500">{label}{hint && <IosHint text={hint} label="Как считается" />}</span>
            <span className={`mt-0.5 text-[22px] font-semibold leading-tight tabular-nums ${toneClass}`}>
                {value === null || value === undefined ? '—' : value}{unit}
            </span>
            {sub && <span className="mt-0.5 truncate text-[11.5px] text-slate-400">{sub}</span>}
        </Tag>
    );
};

/* Переключатель месяца «‹ Октябрь 2026 ›». */
export const MonthSwitch = ({ value, onChange, max }) => (
    <div className="inline-flex h-9 items-center rounded-xl bg-white ring-1 ring-slate-200/70">
        <button type="button" aria-label="Предыдущий месяц" onClick={() => onChange(shiftMonth(value, -1))}
                className="grid h-9 w-9 place-items-center rounded-l-xl text-slate-500 hover:bg-slate-50">
            <ChevronLeft size={16} />
        </button>
        <span className="min-w-[128px] px-1 text-center text-[13px] font-medium tabular-nums text-slate-800">{monthLabel(value)}</span>
        <button type="button" aria-label="Следующий месяц" disabled={max ? value >= max : false}
                onClick={() => onChange(shiftMonth(value, 1))}
                className="grid h-9 w-9 place-items-center rounded-r-xl text-slate-500 hover:bg-slate-50 disabled:opacity-40">
            <ChevronRight size={16} />
        </button>
    </div>
);

/* Фильтры — за кнопкой (владелец: поля формы прятать за кнопки, шум = брак). Кнопка носит число
   включённых отборов, сами поля раскрываются строкой под панелью инструментов. */
export const FiltersToggle = ({ open, count = 0, onToggle }) => (
    <button type="button" aria-expanded={open} onClick={onToggle}
            className={`${TOOLBAR_BTN} ${open ? 'bg-slate-100 text-slate-900' : ''}`}>
        <SlidersHorizontal size={14} /> Фильтры
        {count > 0 && (
            <span className="grid h-[18px] min-w-[18px] place-items-center rounded-full bg-blue-600 px-1 text-[11px] font-semibold leading-none text-white">{count}</span>
        )}
    </button>
);

export const FiltersRow = ({ open, count = 0, onReset, children }) => (open ? (
    <div className="flex flex-wrap items-center gap-2 rounded-2xl bg-white p-2.5 ring-1 ring-slate-200/70">
        {children}
        {count > 0 && onReset && (
            <button type="button" className={`${iosBtnGhost} ml-auto`} onClick={onReset}>Сбросить</button>
        )}
    </div>
) : null);

/* Подтверждение в стиле раздела — вместо системного window.confirm, который рисует ОС. */
export const ConfirmDialog = ({ open, onClose, title, text, confirmLabel = 'Подтвердить', danger = false, busy = false, onConfirm }) => (
    <IosModal open={open} onClose={onClose} title={title} maxWidth="max-w-md"
              footer={(
                  <div className="flex justify-end gap-2">
                      <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={busy}>Отмена</button>
                      <button type="button" className={danger ? DANGER_BTN : iosBtnPrimary} onClick={onConfirm} disabled={busy}>{confirmLabel}</button>
                  </div>
              )}>
        <div className="text-[13.5px] leading-relaxed text-slate-700">{text}</div>
    </IosModal>
);

/* Окно с одним обязательным текстом (причина) — вместо window.prompt. onSubmit(text) может быть
   асинхронным: окно закрывается после успешного ответа. */
export const ReasonDialog = ({ open, onClose, title, subtitle, label = 'Причина', placeholder = '', confirmLabel = 'Готово', danger = false, onSubmit }) => {
    const [value, setValue] = useState('');
    const [busy, setBusy] = useState(false);
    useEffect(() => { if (open) setValue(''); }, [open]);
    const submit = async () => {
        if (!value.trim()) return;
        setBusy(true);
        try {
            await onSubmit(value.trim());
            onClose();
        } finally {
            setBusy(false);
        }
    };
    return (
        <IosModal open={open} onClose={onClose} title={title} subtitle={subtitle} maxWidth="max-w-md"
                  footer={(
                      <div className="flex justify-end gap-2">
                          <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={busy}>Отмена</button>
                          <button type="button" className={danger ? DANGER_BTN : iosBtnPrimary} onClick={submit} disabled={busy || !value.trim()}>{confirmLabel}</button>
                      </div>
                  )}>
            <Label required>{label}</Label>
            <textarea className={`${FIELD} min-h-[84px]`} value={value} onChange={(event) => setValue(event.target.value)} placeholder={placeholder} />
        </IosModal>
    );
};

/* Заголовок группы со счётчиком: иконка серая, число — таблеткой; красится только просрочка. */
export const GroupHeading = ({ icon: Icon, label, count, tone }) => (
    <div className={`flex items-center gap-1.5 px-4 pb-1 pt-3 text-[11.5px] font-semibold uppercase tracking-wide ${tone === 'red' ? 'text-rose-600' : 'text-slate-500'}`}>
        {Icon && <Icon size={13} />}
        <span>{label}</span>
        {count !== undefined && (
            <span className={`ml-0.5 rounded-full px-1.5 py-px text-[11px] tabular-nums ${tone === 'red' ? 'bg-rose-50 text-rose-700' : 'bg-slate-100 text-slate-600'}`}>{count}</span>
        )}
    </div>
);

/* Таблица отчёта: шапка серым, числа в колонку, кликабельные цифры — кнопками. */
export const Table = ({ columns, rows, keyOf, empty = 'Данных нет', onCell }) => (
    <div className="overflow-x-auto rounded-2xl bg-white ring-1 ring-slate-200/70">
        <table className="w-full min-w-[640px] text-[13px]">
            <thead>
                <tr className="text-left text-[11.5px] uppercase tracking-wide text-slate-500">
                    {columns.map((column) => (
                        <th key={column.key} className={`px-3 py-2.5 font-semibold ${column.align === 'right' ? 'text-right' : ''}`}>{column.label}</th>
                    ))}
                </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
                {rows.length === 0 && (
                    <tr><td colSpan={columns.length} className="px-3 py-8 text-center text-slate-500">{empty}</td></tr>
                )}
                {rows.map((row, index) => (
                    <tr key={keyOf ? keyOf(row, index) : index} className="hover:bg-slate-50/60">
                        {columns.map((column) => {
                            const cell = column.render ? column.render(row) : row[column.key];
                            const key = column.keyOf ? column.keyOf(row) : null;
                            const clickable = key && onCell && cell !== '—' && cell !== 0 && cell !== null && cell !== undefined;
                            return (
                                <td key={column.key} className={`px-3 py-2 ${column.align === 'right' ? 'text-right tabular-nums' : ''}`}>
                                    {clickable
                                        ? <button type="button" onClick={() => onCell(key, column.label, row)} className="rounded-md px-1 tabular-nums text-blue-700 underline-offset-2 hover:underline">{cell}</button>
                                        : cell}
                                </td>
                            );
                        })}
                    </tr>
                ))}
            </tbody>
        </table>
    </div>
);
