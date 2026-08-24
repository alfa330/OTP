import React from 'react';
import {
    AlertCircle, CalendarOff, CalendarPlus, Clock, Loader2, Minus, PenLine, Plus,
} from 'lucide-react';
import { iosGroupLabel } from '../ui/ios';
import {
    shiftHistoryActionLabel,
    shiftHistoryActorLabel,
    shiftHistoryTimeLabel,
    shiftHistoryTone,
    shiftHistoryWhenLabel,
} from './shiftHistoryFormat';

/*
 * Список изменений графика — общий для всплывающей панели у ячейки и для
 * вкладки «История» в карточке смены. Один компонент на оба места намеренно:
 * разъехавшись, они дали бы две разные версии одного и того же журнала.
 */

const TONE_ICON = {
    green: 'bg-emerald-50 text-emerald-600',
    blue: 'bg-blue-50 text-blue-600',
    red: 'bg-rose-50 text-rose-600',
    sky: 'bg-sky-50 text-sky-600',
    slate: 'bg-slate-100 text-slate-500',
};

const ACTION_ICON = {
    added: Plus,
    removed: Minus,
    changed: PenLine,
    day_off_set: CalendarOff,
    day_off_cleared: CalendarPlus,
};

const MONTHS_RU = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

/** «20 августа 2026» из «2026-08-20». */
export const shiftHistoryDayTitle = (dateStr) => {
    const parts = String(dateStr || '').split('-');
    if (parts.length !== 3) return dateStr || '';
    const day = Number(parts[2]);
    const month = Number(parts[1]) - 1;
    if (!Number.isFinite(day) || !MONTHS_RU[month]) return dateStr;
    return `${day} ${MONTHS_RU[month]} ${parts[0]}`;
};

const fullWhen = (iso) => {
    if (!iso) return '';
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return '';
    return parsed.toLocaleString('ru-RU');
};

const Block = ({ children }) => (
    <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">{children}</div>
);

const HistoryRow = ({ entry }) => {
    const Icon = ACTION_ICON[entry.action] || Clock;
    const tone = TONE_ICON[shiftHistoryTone(entry)] || TONE_ICON.slate;
    const times = shiftHistoryTimeLabel(entry);
    const actor = shiftHistoryActorLabel(entry);

    return (
        <li className="flex items-start gap-3 px-3.5 py-2.5 transition hover:bg-slate-50/70">
            <span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full ${tone}`}>
                <Icon size={15} />
            </span>

            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-1.5">
                    <span className="text-[13.5px] font-semibold text-slate-900">
                        {shiftHistoryActionLabel(entry)}
                    </span>
                    {times && (
                        <span className="text-[13.5px] tabular-nums text-slate-700">{times}</span>
                    )}
                </div>
                {/* Либо источник говорит сам за себя («Взята с аукциона»),
                    либо тут стоит ФИО. Никогда и то и другое сразу. */}
                {actor && <div className="mt-0.5 text-[12px] text-slate-500">{actor}</div>}
            </div>

            <time
                className="mt-0.5 shrink-0 text-[11.5px] tabular-nums text-slate-500"
                dateTime={entry.changedAt || undefined}
                title={fullWhen(entry.changedAt)}
            >
                {shiftHistoryWhenLabel(entry.changedAt)}
            </time>
        </li>
    );
};

/**
 * status: 'loading' | 'ready' | 'error'
 * showCount: заголовок «Изменений: N». В карточке смены он не нужен —
 * число уже стоит на самой вкладке, и повторять его на одном экране нельзя.
 */
function ShiftHistoryList({ status, items = [], error = '', showCount = true }) {
    if (status === 'loading') {
        return (
            <Block>
                <Loader2 size={18} className="animate-spin text-slate-400" />
                <div className="text-[13px] text-slate-500">Загружаем историю…</div>
            </Block>
        );
    }

    if (status === 'error') {
        return (
            <Block>
                <AlertCircle size={18} className="text-rose-500" />
                <div className="text-[13px] text-slate-600">{error || 'Не удалось загрузить историю'}</div>
            </Block>
        );
    }

    if (!items.length) {
        return (
            <Block>
                <Clock size={18} className="text-slate-300" />
                <div className="text-[13px] font-medium text-slate-600">Изменений не было</div>
                <div className="text-[12px] text-slate-400">
                    Смены этого дня не добавляли, не меняли и не снимали.
                </div>
            </Block>
        );
    }

    return (
        <>
            {showCount && (
                <div className={`${iosGroupLabel} px-4 pb-1 pt-3`}>
                    Изменений: {items.length}
                </div>
            )}
            <ul className="divide-y divide-slate-100">
                {items.map((entry) => (
                    <HistoryRow key={entry.id} entry={entry} />
                ))}
            </ul>
        </>
    );
}

export default ShiftHistoryList;
