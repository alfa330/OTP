import React, { useMemo, useState } from 'react';
import { APPLE_FONT, iosBtnGhost, iosCard, IosBadge } from '../ui/ios';
import FaIcon from '../common/FaIcon';
import { anomalyText } from './funnelFormat';

/*
 * Находки по подневным строкам (`/overview` → `anomalies`).
 *
 * ПУСТО — ПАНЕЛИ НЕТ ВОВСЕ. Не «Аномалий не найдено», не свёрнутая карточка:
 * пустая панель занимает место, приучает скользить по ней взглядом и тем самым
 * гасит тот единственный день, когда в ней что-то появится.
 *
 * Два тона, и у каждого свой смысл, иначе цвет перестаёт быть сигналом:
 *   янтарный — дыра в ДАННЫХ (часы не приехали, объём удвоился), чинит её тот,
 *              кто отвечает за источник;
 *   розовый  — провал в РАБОТЕ (несколько суток подряд ниже порога), это
 *              разговор супервайзера с человеком.
 * Смешивать их в один цвет нельзя: действия по ним разные.
 */

const TONE = {
    amber: 'bg-amber-50 text-amber-600',
    rose: 'bg-rose-50 text-rose-600',
};

/* Иконки — только существующие токены FaIcon: новый токен заводится в трёх
 * местах, и ради панели на три строки это не делается. */
const KINDS = {
    zero_hours_with_leads: { icon: 'fa-clock', tone: 'amber', order: 1 },
    volume_jump: { icon: 'fa-chart-line', tone: 'amber', order: 2 },
    below_target_streak: { icon: 'fa-bullseye', tone: 'rose', order: 0 },
};

const FALLBACK = { icon: 'fa-circle-exclamation', tone: 'amber', order: 3 };

/* Сколько находок видно до разворота. Шесть — это экран телефона: если их
 * четыре десятка, панель без ограничения становится самим разделом. */
const VISIBLE_LIMIT = 6;

/*
 * 'YYYY-MM-DD' → 'ДД.ММ' простым разбором строки.
 *
 * Через `new Date('2026-09-03')` дата разбирается как полночь UTC и в нашем
 * поясе уезжает на сутки назад — ровно так отчёт Chat2Desk в этом проекте уехал
 * на +5 часов. Дата от API уже местная (Алматы), сдвигать её нечем и незачем.
 */
const shortDay = (value) => {
    if (typeof value !== 'string') return '';
    const parts = value.slice(0, 10).split('-');
    return parts.length === 3 ? `${parts[2]}.${parts[1]}` : value;
};

export default function AnomaliesPanel({ anomalies }) {
    const [expanded, setExpanded] = useState(false);

    const items = useMemo(() => {
        const list = Array.isArray(anomalies) ? anomalies.filter(Boolean) : [];
        // Сначала провалы в работе, потом дыры в данных; внутри вида — свежие
        // дни сверху: вчерашний разрыв чинится, позапрошлогодний уже нет.
        return list.slice().sort((left, right) => {
            const a = (KINDS[left.kind] || FALLBACK).order;
            const b = (KINDS[right.kind] || FALLBACK).order;
            if (a !== b) return a - b;
            return String(right.work_day || '').localeCompare(String(left.work_day || ''));
        });
    }, [anomalies]);

    if (!items.length) return null;

    const shown = expanded ? items : items.slice(0, VISIBLE_LIMIT);
    const hidden = items.length - shown.length;

    return (
        <section style={{ fontFamily: APPLE_FONT }} className={`${iosCard} p-4`}>
            <div className="flex items-center gap-2">
                <FaIcon className="fas fa-triangle-exclamation text-[14px] text-amber-500" aria-hidden="true" />
                <h3 className="text-[13px] font-semibold text-slate-700">Что проверить</h3>
                <IosBadge tone="slate">{items.length}</IosBadge>
            </div>

            <ul className="mt-2 divide-y divide-slate-100">
                {shown.map((item, index) => {
                    const meta = KINDS[item.kind] || FALLBACK;
                    const day = shortDay(item.work_day);
                    return (
                        <li
                            key={`${item.kind}-${item.user_id}-${item.work_day}-${index}`}
                            className="flex items-start gap-2.5 py-2"
                        >
                            <span className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-lg ${TONE[meta.tone]}`}>
                                <FaIcon className={`fas ${meta.icon} text-[12px]`} aria-hidden="true" />
                            </span>
                            <div className="min-w-0">
                                <div className="text-[13px] leading-snug text-slate-700">
                                    {anomalyText(item)}
                                </div>
                                <div className="mt-0.5 text-[11.5px] text-slate-400">
                                    <span className="font-medium text-slate-500">
                                        {item.name || 'Не сопоставлен'}
                                    </span>
                                    {day ? <span className="tabular-nums"> · {day}</span> : null}
                                </div>
                            </div>
                        </li>
                    );
                })}
            </ul>

            {(hidden > 0 || expanded) && (
                <button
                    type="button"
                    className={`${iosBtnGhost} mt-1`}
                    onClick={() => setExpanded((open) => !open)}
                >
                    <FaIcon
                        className={`fas ${expanded ? 'fa-chevron-up' : 'fa-chevron-down'} text-[10px]`}
                        aria-hidden="true"
                    />
                    {expanded ? 'Свернуть' : `Ещё ${hidden}`}
                </button>
            )}
        </section>
    );
}
