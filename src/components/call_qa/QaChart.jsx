import React, { useMemo, useState } from 'react';
import {
    Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { Table2 } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';

/* График раздела «ИИ-оценка»: ряд по дням, сравнение значений — одной формой
 * для ответов ИИ (блок data-qa-chart с таблицей внутри) и для экрана периода
 * (ряды считает сервер).
 *
 * Спецификация: { kind: 'line' | 'bar' | 'hbar', title, categories: [подпись],
 * series: [{ name, values: [число | null] }] }. Для ответа ИИ она разбирается
 * из таблицы (parseChartTable): первая колонка — подписи, остальные — ряды.
 *
 * Правила вида — из методики dataviz проекта: тонкие метки (столбец ≤ 24 px со
 * скруглённым концом, линия 2 px с точками ≥ 8 px), волосяная сплошная сетка,
 * без второй оси, один ряд — один цвет без легенды, два ряда — легенда рядом с
 * заголовком, подсказка при наведении на всю колонку, и всегда таблица той же
 * формы по кнопке: цвет никогда не единственный носитель значения. Палитра
 * проверена валидатором (светлая и тёмная поверхности): синий, оранжевый,
 * зелёный, жёлтый — и только четыре ряда, больше график не принимает.
 */

export const CHART_KINDS = ['line', 'bar', 'hbar'];
const PALETTE = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100'];
const INK = '#64748b';          // подписи осей — текст, не цвет ряда
const GRID = '#e8edf3';
const MAX_SERIES = 4;
const MAX_ROWS = 40;

const toNumber = (text) => {
    const raw = String(text ?? '').replace(/ /g, '').replace(/\s+/g, '').replace('−', '-').replace('%', '')
        .replace(',', '.');
    if (!raw) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
};

/** Таблица (DOM-элемент) → спецификация графика; негодная — null. */
export const parseChartTable = (table, kind = 'bar', title = '') => {
    if (!table) return null;
    const rows = [...table.querySelectorAll('tr')].map((tr) => [...tr.querySelectorAll('th,td')]
        .map((cell) => (cell.textContent || '').trim()));
    const filled = rows.filter((cells) => cells.length >= 2);
    if (filled.length < 3) return null;
    const [head, ...body] = filled;
    const seriesCount = Math.min(head.length, MAX_SERIES + 1) - 1;
    if (seriesCount < 1 || body.length > MAX_ROWS) return null;
    const categories = body.map((cells) => cells[0]);
    const series = [];
    for (let i = 1; i <= seriesCount; i += 1) {
        series.push({ name: head[i] || `Ряд ${i}`, values: body.map((cells) => toNumber(cells[i])) });
    }
    if (!series.some((s) => s.values.some((v) => v != null))) return null;
    return { kind: CHART_KINDS.includes(kind) ? kind : 'bar', title, categories, series };
};

const truncate = (text, max) => {
    const value = String(text ?? '');
    return value.length > max ? `${value.slice(0, max - 1).trimEnd()}…` : value;
};

const fmt = (value) => {
    if (value == null) return '—';
    const rounded = Math.round(value * 10) / 10;
    return Number.isInteger(rounded) ? String(rounded) : String(rounded).replace('.', ',');
};

const ChartTooltip = ({ active, payload, label }) => {
    if (!active || !payload || !payload.length) return null;
    return (
        <div className="rounded-xl bg-white/95 px-3 py-2 text-[12px] shadow-[0_10px_30px_rgba(15,23,42,0.14)] ring-1 ring-slate-200/80 backdrop-blur-xl"
             style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-1 font-semibold text-slate-800">{label}</div>
            {payload.map((item) => (
                <div key={item.dataKey} className="flex items-center gap-2">
                    <span className="h-[3px] w-3.5 rounded-full" style={{ background: item.color || item.fill }} aria-hidden="true" />
                    <span className="text-slate-500">{item.name}</span>
                    <span className="ml-auto font-medium tabular-nums text-slate-900">{fmt(item.value)}</span>
                </div>
            ))}
        </div>
    );
};

function ChartTable({ spec }) {
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
                <thead>
                    <tr className="text-left text-[11px] uppercase tracking-wide text-slate-400">
                        <th className="py-1.5 pr-3 font-medium">&nbsp;</th>
                        {spec.series.map((s) => <th key={s.name} className="py-1.5 pr-3 text-right font-medium">{s.name}</th>)}
                    </tr>
                </thead>
                <tbody>
                    {spec.categories.map((category, row) => (
                        <tr key={`${category}-${row}`} className="border-t border-slate-100">
                            <td className="py-1.5 pr-3 text-slate-700">{category}</td>
                            {spec.series.map((s) => (
                                <td key={s.name} className="py-1.5 pr-3 text-right tabular-nums text-slate-900">{fmt(s.values[row])}</td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

/* Высота: ряд по дням и столбцы — 220 px; горизонтальные столбцы растут с числом
   строк (24 px на строку), чтобы подписи не слипались. */
const heightOf = (spec) => (spec.kind === 'hbar' ? Math.min(560, Math.max(140, spec.categories.length * 26 + 24)) : 220);

export default function QaChart({ spec, className = '', compact = false, colorOf = null }) {
    const [table, setTable] = useState(false);
    const data = useMemo(() => (spec?.categories || []).map((category, row) => {
        const point = { category };
        (spec.series || []).forEach((s, i) => { point[`s${i}`] = s.values[row]; });
        return point;
    }), [spec]);
    if (!spec || !spec.series?.length) return null;
    const multi = spec.series.length > 1;
    const height = heightOf(spec);
    const tick = { fontSize: 11, fill: INK, fontFamily: APPLE_FONT };
    const many = spec.categories.length > 16;
    return (
        <figure className={`qa-chart ${className}`} aria-label={spec.title || 'График'}>
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    {spec.title && <figcaption className="text-[13px] font-semibold text-slate-900">{spec.title}</figcaption>}
                    {multi && (
                        <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-slate-500" aria-label="Ряды">
                            {spec.series.map((s, i) => (
                                <li key={s.name} className="inline-flex items-center gap-1.5">
                                    <span className={`inline-block ${spec.kind === 'line' ? 'h-[3px] w-3.5 rounded-full' : 'h-2.5 w-2.5 rounded-[3px]'}`}
                                          style={{ background: PALETTE[i] }} aria-hidden="true" />
                                    {s.name}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
                <button type="button" onClick={() => setTable((v) => !v)} aria-pressed={table}
                        title={table ? 'Показать график' : 'Показать таблицей'} aria-label={table ? 'Показать график' : 'Показать таблицей'}
                        className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                            table ? 'bg-slate-200/70 text-slate-700' : 'text-slate-400 hover:bg-slate-100 hover:text-slate-600'}`}>
                    <Table2 size={14} aria-hidden="true" />
                </button>
            </div>
            {table ? (
                <div className="mt-2"><ChartTable spec={spec} /></div>
            ) : (
                <div className="mt-2" style={{ height }} data-qa-chart-kind={spec.kind}>
                    <ResponsiveContainer width="100%" height="100%">
                        {spec.kind === 'line' ? (
                            <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
                                <CartesianGrid stroke={GRID} vertical={false} />
                                <XAxis dataKey="category" tick={tick} axisLine={{ stroke: GRID }} tickLine={false}
                                       interval={many ? 'preserveStartEnd' : 0} minTickGap={18} />
                                <YAxis tick={tick} axisLine={false} tickLine={false} width={44} domain={['auto', 'auto']} />
                                <Tooltip content={<ChartTooltip />} cursor={{ stroke: '#cbd5e1', strokeWidth: 1 }} />
                                {spec.series.map((s, i) => (
                                    <Line key={s.name} type="monotone" dataKey={`s${i}`} name={s.name} stroke={PALETTE[i]}
                                          strokeWidth={2} connectNulls={false} isAnimationActive={false}
                                          dot={{ r: 3.5, strokeWidth: 2, stroke: '#fff', fill: PALETTE[i] }}
                                          activeDot={{ r: 5, strokeWidth: 2, stroke: '#fff' }} />
                                ))}
                            </LineChart>
                        ) : spec.kind === 'hbar' ? (
                            <BarChart data={data} layout="vertical" barCategoryGap={6} barGap={2}
                                      margin={{ top: 4, right: 28, bottom: 0, left: 4 }}>
                                <CartesianGrid stroke={GRID} horizontal={false} />
                                <XAxis type="number" tick={tick} axisLine={false} tickLine={false} domain={[0, 'auto']} />
                                {/* Длинная подпись режется с многоточием, а не переносится в три
                                    строки; целиком она в подсказке и в таблице. */}
                                <YAxis type="category" dataKey="category" tick={{ ...tick, fontSize: 11.5 }} axisLine={false}
                                       tickLine={false} width={compact ? 124 : 176} interval={0}
                                       tickFormatter={(value) => truncate(value, compact ? 17 : 24)} />
                                <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(148,163,184,0.12)' }} />
                                {spec.series.map((s, i) => (
                                    <Bar key={s.name} dataKey={`s${i}`} name={s.name} fill={PALETTE[i]} maxBarSize={18}
                                         radius={[0, 4, 4, 0]} isAnimationActive={false}>
                                        {!multi && colorOf && data.map((point, row) => (
                                            <Cell key={`c-${row}`} fill={colorOf(point.s0, row) || PALETTE[0]} />
                                        ))}
                                    </Bar>
                                ))}
                            </BarChart>
                        ) : (
                            <BarChart data={data} barCategoryGap={many ? 2 : 8} barGap={2}
                                      margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
                                <CartesianGrid stroke={GRID} vertical={false} />
                                <XAxis dataKey="category" tick={tick} axisLine={{ stroke: GRID }} tickLine={false}
                                       interval={many ? 'preserveStartEnd' : 0} minTickGap={18} />
                                <YAxis tick={tick} axisLine={false} tickLine={false} width={44} domain={[0, 'auto']} allowDecimals={false} />
                                <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(148,163,184,0.12)' }} />
                                {spec.series.map((s, i) => (
                                    <Bar key={s.name} dataKey={`s${i}`} name={s.name} fill={PALETTE[i]} maxBarSize={24}
                                         radius={[4, 4, 0, 0]} isAnimationActive={false}>
                                        {!multi && colorOf && data.map((point, row) => (
                                            <Cell key={`c-${row}`} fill={colorOf(point.s0, row) || PALETTE[0]} />
                                        ))}
                                    </Bar>
                                ))}
                            </BarChart>
                        )}
                    </ResponsiveContainer>
                </div>
            )}
        </figure>
    );
}
