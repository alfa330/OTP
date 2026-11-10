import React from 'react';
import { ArrowRight } from 'lucide-react';
import { initials } from './hrOffice';

/*
 * «Было → стало» в сверке загрузки и в журнале «Приема»: одна и та же запись в обоих местах.
 * Строка — подпись поля слева в своей колонке, справа прежнее значение зачёркнуто, новое — тёмным.
 * Было пусто — «—» и пометка «заполнится» не нужна: пустое слева и так читается как «появится».
 * Личные данные (ИИН, удостоверение, адреса, сумма) приходят без значений — «изменено».
 */

export function Avatar({ name, className = '' }) {
    return (
        <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-full bg-slate-100 text-[12.5px] font-semibold text-slate-600 ${className}`}>
            {initials(name)}
        </span>
    );
}

/* derived — не поле, которое HR меняет, а следствие (новый договор: номер, дата и сумма прежнего
   в него не переходят) — янтарной подписью, чтобы его не проглядели; note — пояснение вместо значения. */
export function ChangeLine({ change }) {
    const { label, old, new: value, hidden, legacy, derived, note } = change || {};
    return (
        <div className="grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)] items-baseline gap-x-3 py-[3px] text-[13px] leading-snug">
            <span className={`truncate ${derived ? 'text-amber-700' : 'text-slate-500'}`}>{label}</span>
            {hidden ? (
                <span className={derived ? 'text-amber-700' : 'text-slate-600'}>{note || 'изменено'}</span>
            ) : legacy ? (
                /* Запись до сверки: прежнее значение тогда не сохранялось — стрелкой «было → стало» не врём. */
                <span className="min-w-0 break-words text-slate-800">{value || '—'}</span>
            ) : (
                <span className="min-w-0 break-words">
                    {old
                        ? <span className="text-slate-400 line-through decoration-slate-300">{old}</span>
                        : <span className="text-slate-400">—</span>}
                    <ArrowRight size={13} aria-label="стало" className="mx-1.5 inline-block -translate-y-px text-slate-400" />
                    <span className="font-medium text-slate-900">{value || '—'}</span>
                    {change.new_company && (
                        <span className="ml-1.5 inline-block rounded-full bg-blue-50 px-1.5 py-px align-[1px] text-[11px] font-medium text-blue-700 ring-1 ring-blue-100">
                            новая в справочнике
                        </span>
                    )}
                </span>
            )}
        </div>
    );
}

export function ChangeList({ changes, className = '' }) {
    if (!changes?.length) return null;
    return (
        <div className={className}>
            {changes.map((change, index) => <ChangeLine key={`${change.field || change.label}-${index}`} change={change} />)}
        </div>
    );
}

/* Заметки строки сверки: почему что-то из таблицы не записывается (компания в архиве и т. п.). */
export function RowNotes({ notes }) {
    if (!notes?.length) return null;
    return (
        <div className="mt-1 space-y-0.5">
            {notes.map((note) => <div key={note} className="text-[12.5px] leading-snug text-amber-700">{note}</div>)}
        </div>
    );
}

/* Старые записи журнала (до сверки) хранили только новые значения — объектом {подпись: значение}. */
export function changesOf(person) {
    const raw = person?.changes;
    if (Array.isArray(raw)) return raw;
    if (raw && typeof raw === 'object') return Object.entries(raw).map(([label, value]) => ({ label, new: String(value), legacy: true }));
    return [];
}

/* Заготовка строк, пока список грузится: того же роста, что настоящие строки, — содержимое
   встаёт на место без сдвига. Тон полос — как у заготовок досок (bg-slate-200/70). */
export function Bone({ className = '' }) {
    return <span aria-hidden="true" className={`block animate-pulse rounded-full bg-slate-200/70 motion-reduce:animate-none ${className}`} />;
}

const WIDTHS = ['w-3/5', 'w-2/5', 'w-1/2', 'w-1/3', 'w-2/3', 'w-1/4'];

export function SkeletonRows({ rows = 6, label = 'Загружаем' }) {
    return (
        <div role="status" aria-busy="true" aria-label={label} className="divide-y divide-slate-100">
            {Array.from({ length: rows }, (_, index) => (
                <div key={index} className="flex items-start gap-3 px-4 py-3">
                    <span aria-hidden="true" className="h-8 w-8 shrink-0 animate-pulse rounded-[10px] bg-slate-200/70 motion-reduce:animate-none" />
                    <div className="min-w-0 flex-1 space-y-2 pt-1">
                        <Bone className={`h-2.5 ${WIDTHS[index % WIDTHS.length]}`} />
                        <Bone className={`h-2 ${WIDTHS[(index + 3) % WIDTHS.length]}`} />
                    </div>
                    <Bone className="mt-1 h-2 w-9" />
                </div>
            ))}
        </div>
    );
}
