import React, { useState } from 'react';
import { AlertTriangle, ArrowRight, Check, ChevronDown, ListTree, Pencil } from 'lucide-react';
import { iosBtnGhost, iosGroupLabel } from '../ui/ios';
import { formatDate, formatTime } from './cityRules';
import {
    absentText, dispatcherNote, feedersText, parksPreview, registrationHeadline,
} from './registrationRules';

/* «Регистрация водителей» в карточке города (просьба владельца 29.10.2026):
 * «куда зарегать водителя iTaxi, если он обращается с Рудного».
 *
 * Первой строкой — город, куда регистрировать: это и есть ответ, и искать его
 * в списке не нужно. Ниже — парки, у каждого его диспетчерские из кабинета
 * Fleet: оператор знает парк водителя и находит свою строку. Город строки
 * пишется, только когда он другой («Рудный → Костанай»).
 *
 * Решает сервер (wiki/registration.py) тем же правилом, что помощник и поиск,
 * — здесь только показ: две карточки на один город с разным ответом были бы
 * хуже, чем ни одной.
 */

const TONES = {
    own: { Icon: Check, box: 'bg-emerald-50 text-emerald-600 ring-emerald-100' },
    mixed: { Icon: ArrowRight, box: 'bg-blue-50 text-blue-600 ring-blue-100' },
    elsewhere: { Icon: ArrowRight, box: 'bg-blue-50 text-blue-600 ring-blue-100' },
    unset: { Icon: AlertTriangle, box: 'bg-amber-50 text-amber-600 ring-amber-100' },
};

const DispatcherChip = ({ dispatcher }) => {
    const note = dispatcherNote(dispatcher);
    return (
        <span className="inline-flex max-w-full items-center gap-1 rounded-lg bg-slate-100 px-2 py-0.5 text-[12.5px] font-medium text-slate-700">
            <span className="truncate">{dispatcher.name}</span>
            {note && <span className="shrink-0 font-normal text-slate-400">· {note}</span>}
        </span>
    );
};

const ParkRow = ({ entry }) => (
    <div className="flex items-start gap-3 px-4 py-2.5">
        <div className="w-28 shrink-0 pt-px text-[14px] font-medium leading-snug text-slate-900 sm:w-40">
            {entry.name}
        </div>
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-1.5">
            {!entry.own && (
                <span className="inline-flex items-center gap-1 text-[12.5px] font-medium text-blue-700">
                    <ArrowRight size={12} /> {entry.city.name}
                </span>
            )}
            {entry.dispatchers.map((dispatcher) => (
                <DispatcherChip key={dispatcher.id ?? dispatcher.name} dispatcher={dispatcher} />
            ))}
        </div>
    </div>
);

export default function CityRegistration({ city, canManage = false, onEdit = null, onOpenDispatchers = null }) {
    const [expanded, setExpanded] = useState(false);
    const [absentOpen, setAbsentOpen] = useState(false);
    const block = city?.registration;
    if (!block) return null;

    const head = registrationHeadline(city.name, block);
    const { Icon, box } = TONES[head.tone] || TONES.unset;
    const { shown, hidden } = parksPreview(block.parks, expanded);
    const absent = block.absent || [];
    const feeders = feedersText(block.feeders);
    const source = block.source || {};
    const checked = source.fleet_checked_at || source.synced_at;

    return (
        <section className="space-y-2">
            <div className="flex items-end justify-between gap-2">
                <div className={iosGroupLabel}>Регистрация водителей</div>
                {onOpenDispatchers && (
                    <button
                        type="button"
                        onClick={onOpenDispatchers}
                        className="inline-flex items-center gap-1 px-1 text-[12px] font-medium text-slate-500 hover:text-slate-800"
                    >
                        <ListTree size={12} /> Все диспетчерские
                    </button>
                )}
            </div>
            <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                <div className="flex items-center gap-3 px-4 py-3">
                    <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ring-1 ${box}`}>
                        <Icon size={17} />
                    </span>
                    <div className="min-w-0 flex-1">
                        <div className="text-[15.5px] font-semibold leading-tight text-slate-900">{head.title}</div>
                        <div className="mt-0.5 text-[12.5px] leading-snug text-slate-500">{head.subtitle}</div>
                    </div>
                    {head.tone === 'unset' && canManage && onEdit && (
                        <button type="button" className={`${iosBtnGhost} !py-1`} onClick={() => onEdit(city)}>
                            <Pencil size={13} /> Задать
                        </button>
                    )}
                </div>

                {shown.length > 0 && (
                    <div className="divide-y divide-slate-100 border-t border-slate-100">
                        {shown.map((entry) => <ParkRow key={entry.key} entry={entry} />)}
                    </div>
                )}
                {hidden > 0 && (
                    <button
                        type="button"
                        onClick={() => setExpanded(true)}
                        className="flex w-full items-center justify-center gap-1 border-t border-slate-100 px-4 py-2 text-[12.5px] font-medium text-slate-500 transition hover:bg-slate-50 hover:text-slate-800"
                    >
                        Ещё {hidden} <ChevronDown size={13} />
                    </button>
                )}

                {(absent.length > 0 || feeders || checked) && (
                    <div className="space-y-1 border-t border-slate-100 bg-slate-50/60 px-4 py-2.5 text-[12px] leading-relaxed text-slate-500">
                        {feeders && <div>{feeders}</div>}
                        {absent.length > 0 && (
                            <div>
                                {absentOpen ? absentText(city.name, absent, absent.length)
                                    : absentText(city.name, absent)}
                                {!absentOpen && absent.length > 4 && (
                                    <button
                                        type="button"
                                        onClick={() => setAbsentOpen(true)}
                                        className="ml-1 font-medium text-slate-600 underline decoration-dotted underline-offset-2 hover:text-slate-900"
                                    >
                                        показать
                                    </button>
                                )}
                            </div>
                        )}
                        {checked && (
                            <div className="tabular-nums text-slate-400">
                                Диспетчерские — из кабинета Fleet{' '}
                                <span className="whitespace-nowrap">
                                    на {formatDate(checked)} в {formatTime(checked)}
                                </span>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </section>
    );
}
