import React, { useMemo } from 'react';

import { APPLE_FONT, iosCard } from '../ui/ios';
import { formatHours, formatNumber, formatPercent, heatAlpha } from './funnelFormat';

/*
 * Тепловая карта «Оператор × День» (поле `heatmap` ответа `/api/op_funnel/days`).
 *
 * ЗАЧЕМ ОНА, КОГДА ЕСТЬ ТАБЛИЦА ОПЕРАТОРОВ. Таблица отвечает «кто сколько за
 * период», карта — «когда именно просело». Провал одного человека в три
 * конкретных дня в средних за месяц не виден вовсе, а здесь это дырка в строке,
 * по которой сразу открывают день.
 *
 * ОДИН ТОН С АЛЬФА-РАМПОЙ, А НЕ РАДУГА. Радужная шкала (красный → жёлтый →
 * зелёный) заставляет держать в голове легенду и врёт на краях: у жёлтого и
 * зелёного разная светлота, и одинаковые по смыслу клетки читаются как разные.
 * Насыщенность одного синего читается без легенды — темнее значит больше, и
 * рампу считает `heatAlpha`, общая с остальным разделом.
 *
 * ЧИСЛО В КЛЕТКЕ — ДОЗВОНЫ, ЗАЛИВКА — ВЫПОЛНЕНИЕ ПЛАНА. Это разные величины
 * намеренно: 60 дозвонов у человека на полставки и у человека на ставке — не
 * одно и то же, и без плана карта сравнивала бы несравнимое. Часы, обработанное
 * и сам план лежат в подсказке клетки, чтобы не превращать сетку в таблицу.
 */

/* Синий из палитры раздела (blue-600). Компонентами, а не строкой класса:
   альфа считается на лету, а Tailwind классы генерирует на сборке. */
const HEAT_RGB = '37, 99, 235';

/* С какой насыщенности подпись переводится на белую: на тёмной клетке
   slate-700 сливается с заливкой, и число перестаёт читаться. */
const LIGHT_TEXT_FROM = 0.55;

const WEEKDAYS = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];

/* Дату разбираем по частям, а не через `new Date('2026-09-01')`: строка без
   времени читается движком как UTC-полночь, и западнее Гринвича день уезжал бы
   на предыдущий — ровно тот сорт сдвига, на котором проект уже горел. */
function dayParts(iso) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    if (!match) return null;
    return { y: Number(match[1]), m: Number(match[2]), d: Number(match[3]) };
}

function pad2(value) {
    return String(value).padStart(2, '0');
}

function dayInfo(iso) {
    const parts = dayParts(iso);
    if (!parts) return { key: String(iso || ''), short: String(iso || ''), full: String(iso || ''), weekend: false, weekday: '' };
    const weekday = new Date(parts.y, parts.m - 1, parts.d).getDay();
    return {
        key: String(iso),
        short: `${pad2(parts.d)}.${pad2(parts.m)}`,
        full: `${pad2(parts.d)}.${pad2(parts.m)}.${parts.y}`,
        weekday: WEEKDAYS[weekday],
        weekend: weekday === 0 || weekday === 6,
    };
}

function alphaOf(rate) {
    if (rate === null || rate === undefined) return 0;
    const value = Number(heatAlpha(rate));
    if (!Number.isFinite(value)) return 0;
    return Math.min(1, Math.max(0, value));
}

/* План в клетке не приходит отдельным полем: `/days` отдаёт дозвоны и долю
   выполнения (`routes._cell`), а план из них восстанавливается делением. При
   нулевом дозвоне доля тоже ноль, и плана из неё не достать — тогда честное
   «—», а не выдуманный ноль. */
function planOf(cell) {
    if (!cell) return null;
    const explicit = cell.plan_reached ?? cell.plan;
    if (explicit !== undefined && explicit !== null) return Number(explicit);
    const rate = Number(cell.plan_rate);
    const reached = Number(cell.reached);
    if (!Number.isFinite(rate) || rate <= 0 || !Number.isFinite(reached)) return null;
    return reached / rate;
}

function cellTitle(name, day, cell) {
    const plan = planOf(cell);
    return [
        `${name}, ${day.full}`,
        `Часы: ${formatHours(cell.hours)}`,
        `Обработано: ${formatNumber(cell.handled)}`,
        `Дозвон: ${formatNumber(cell.reached)}`,
        `План: ${plan === null ? '—' : formatNumber(Math.round(plan))}`,
        `Выполнение: ${formatPercent(cell.plan_rate)}`,
    ].join('\n');
}

export default function HeatmapGrid({ heatmap = [], days = [], onOpenCell = null }) {
    /* Колонки — из `days`, а не из ключей клеток: день, в который не работал
       никто, обязан остаться пустым столбцом. Схлопни его — и в сетке
       «понедельник, среда, четверг» пропажа вторника выглядит как норма. */
    const columns = useMemo(() => {
        const list = Array.isArray(days) ? days : [];
        const keys = list.map((item) => String((item && item.day) || item || '')).filter(Boolean);
        if (keys.length) return keys.map(dayInfo);
        const seen = new Set();
        (Array.isArray(heatmap) ? heatmap : []).forEach((row) => {
            Object.keys((row && row.cells) || {}).forEach((key) => seen.add(key));
        });
        return [...seen].sort().map(dayInfo);
    }, [days, heatmap]);

    /* Несопоставленный (user_id = 0) — последней строкой: это не человек, а
       сигнал о пробеле в сопоставлении, и в алфавите ему места нет. */
    const rows = useMemo(() => {
        const list = (Array.isArray(heatmap) ? heatmap : []).filter(Boolean);
        return [...list].sort((a, b) => {
            const aUnmapped = !a.user_id;
            const bUnmapped = !b.user_id;
            if (aUnmapped !== bUnmapped) return aUnmapped ? 1 : -1;
            return String(a.name || '').localeCompare(String(b.name || ''), 'ru');
        });
    }, [heatmap]);

    // Пустая карта не рисуется вовсе: рамка с шапкой и без клеток — это шум.
    if (!rows.length || !columns.length) return null;

    const clickable = typeof onOpenCell === 'function';

    return (
        <section className={`${iosCard} overflow-hidden`} style={{ fontFamily: APPLE_FONT }}>
            <div className="flex flex-wrap items-center justify-between gap-2 px-4 pb-2 pt-3.5">
                <div>
                    <div className="text-[13px] font-semibold text-slate-800">Дозвоны по дням</div>
                    <div className="text-[11.5px] text-slate-500">
                        В клетке — дозвоны, заливка — выполнение плана
                        {clickable ? '. Нажмите на клетку, чтобы открыть день' : ''}
                    </div>
                </div>
                <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
                    <span>0</span>
                    {[0, 0.4, 0.7, 1, 1.3].map((rate) => (
                        <span
                            key={rate}
                            className="h-3.5 w-5 rounded-[4px] ring-1 ring-inset ring-slate-200/70"
                            style={{ background: `rgba(${HEAT_RGB}, ${alphaOf(rate)})` }}
                        />
                    ))}
                    <span>план и выше</span>
                </div>
            </div>

            {/* Прокрутка живёт ВНУТРИ контейнера: сетка на месяц шире телефона,
                и без этого по горизонтали ездила бы вся страница. */}
            <div className="max-h-[62vh] overflow-auto">
                <table className="w-max border-separate border-spacing-0 text-[12px]">
                    <thead>
                        <tr>
                            {/* Угол закреплён по обеим осям, иначе при прокрутке он
                                уезжает из-под шапки и та повисает без подписи. */}
                            <th className="sticky left-0 top-0 z-30 min-w-[150px] max-w-[190px] border-b border-slate-200/70 bg-white px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500 shadow-[1px_0_0_rgba(226,232,240,0.9)]">
                                Оператор
                            </th>
                            {columns.map((day) => (
                                <th
                                    key={day.key}
                                    scope="col"
                                    className={`sticky top-0 z-20 min-w-[46px] border-b border-slate-200/70 px-1 py-1.5 text-center text-[11px] font-medium ${
                                        day.weekend ? 'bg-slate-50 text-slate-400' : 'bg-white text-slate-500'
                                    }`}
                                >
                                    <div className="tabular-nums">{day.short}</div>
                                    <div className="text-[10px] text-slate-400">{day.weekday}</div>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row) => {
                            const name = row.name || 'Не сопоставлен';
                            const unmapped = !row.user_id;
                            const cells = row.cells || {};
                            return (
                                <tr key={row.user_id || `unmapped-${name}`}>
                                    <th
                                        scope="row"
                                        title={name}
                                        className={`sticky left-0 z-10 max-w-[190px] truncate border-b border-slate-100 bg-white px-3 py-1 text-left text-[12px] font-medium shadow-[1px_0_0_rgba(226,232,240,0.9)] ${
                                            unmapped ? 'text-amber-700' : 'text-slate-700'
                                        }`}
                                    >
                                        {name}
                                    </th>
                                    {columns.map((day) => {
                                        const cell = cells[day.key];
                                        if (!cell) {
                                            return (
                                                <td key={day.key}
                                                    className="border-b border-slate-100 bg-white px-1 py-1" />
                                            );
                                        }
                                        const alpha = alphaOf(cell.plan_rate);
                                        const light = alpha >= LIGHT_TEXT_FROM;
                                        const title = cellTitle(name, day, cell);
                                        const body = (
                                            <span className={`block rounded-[6px] py-1.5 text-center tabular-nums ${
                                                light ? 'text-white' : 'text-slate-700'
                                            }`}
                                                  style={{ background: `rgba(${HEAT_RGB}, ${alpha})` }}>
                                                {formatNumber(cell.reached)}
                                            </span>
                                        );
                                        return (
                                            <td key={day.key}
                                                className="border-b border-slate-100 bg-white p-0.5">
                                                {clickable ? (
                                                    <button
                                                        type="button"
                                                        title={title}
                                                        aria-label={title.replace(/\n/g, ', ')}
                                                        onClick={() => onOpenCell({ userId: row.user_id, day: day.key })}
                                                        className="block w-full rounded-[6px] transition active:scale-[0.98] hover:ring-2 hover:ring-blue-500/40"
                                                    >
                                                        {body}
                                                    </button>
                                                ) : (
                                                    <span title={title} className="block w-full">{body}</span>
                                                )}
                                            </td>
                                        );
                                    })}
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </section>
    );
}
