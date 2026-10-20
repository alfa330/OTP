import React, { useEffect, useState } from 'react';
import { ChevronRight, ShieldAlert, ShieldCheck } from 'lucide-react';
import { iosCard } from '../ui/ios';
import { apiError } from './disciplineApi';
import { Chip, EmptyState, Group, LoadingRows, RecordRow, T } from './disciplineUi';
import { controlReason, daysLeftText, fmtDate, fmtShort, pluralViolations } from './disciplineRules';

/*
 * «Сводка» — главный экран (ТЗ п. 4.1). Порядок блоков сверху вниз задан ТЗ и
 * «принципиален»:
 *   1. «На особом контроле» — красный, всегда развёрнут, всегда первый; пусто —
 *      зелёный «никого нет» (п. 10.1);
 *   2. счётчики за текущий период;
 *   3. «Следующее нарушение — акт» — жёлтое предупреждение (п. 4.1.3; в ТЗ —
 *      «два нарушения», а при настраиваемой лестнице это «шаг до акта»);
 *   4. последние 20 записей.
 */

const people = (n) => {
    const v = Math.abs(n) % 100;
    if (v % 10 === 1 && v !== 11) return 'человек';
    if (v % 10 >= 2 && v % 10 <= 4 && (v < 12 || v > 14)) return 'человека';
    return 'человек';
};

const ROW_TONES = {
    red: 'bg-rose-100/70',
    yellow: 'bg-amber-50',
    normal: 'bg-white/70',
};

export const ControlBlock = ({ rows, onOpenOperator, onShowAll }) => {
    if (!rows.length) {
        return (
            <div className="flex items-center gap-3 rounded-2xl border-l-4 border-emerald-500 bg-emerald-50 px-4 py-3.5 ring-1 ring-emerald-200/70 xl:px-5 2xl:py-4">
                <ShieldCheck size={18} className="shrink-0 text-emerald-600" />
                <span className={`text-[14px] font-semibold text-emerald-900 ${T.body}`}>На особом контроле никого нет</span>
            </div>
        );
    }
    return (
        <section className="overflow-hidden rounded-2xl border-l-4 border-rose-500 bg-rose-50/80 ring-1 ring-rose-200/80">
            <div className="flex items-center justify-between gap-3 px-4 py-3 xl:px-5 2xl:py-4">
                <div className="flex items-center gap-2.5">
                    <ShieldAlert size={18} className="shrink-0 text-rose-600" />
                    <h2 className={`text-[14.5px] font-semibold text-rose-900 ${T.body}`}>
                        На особом контроле — <span className="tabular-nums">{rows.length}</span> {people(rows.length)}
                    </h2>
                </div>
                {onShowAll && (
                    <button type="button" onClick={onShowAll} className={`hidden shrink-0 text-[12.5px] font-medium text-rose-700 hover:underline sm:inline ${T.sub}`}>
                        Весь список
                    </button>
                )}
            </div>
            <ul className="divide-y divide-rose-200/60 border-t border-rose-200/60">
                {rows.map((row) => (
                    <li key={row.id} className={`${ROW_TONES[row.tone] || ROW_TONES.normal}`}>
                        <button type="button" onClick={() => onOpenOperator(row.operator_id)}
                                className="grid w-full grid-cols-1 gap-1 px-4 py-3 text-left sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1.4fr)_auto] sm:items-center sm:gap-4 xl:px-5 2xl:py-3.5">
                            <span className="min-w-0">
                                <span className={`block truncate text-[13.5px] font-semibold text-slate-900 ${T.body}`}>{row.operator_name}</span>
                                <span className={`block truncate text-[12px] text-slate-500 ${T.sub}`}>
                                    {[row.group_name, row.direction_name].filter(Boolean).join(' · ') || 'Без группы'}
                                </span>
                            </span>
                            <span className="min-w-0">
                                <span className={`block truncate text-[12.5px] text-slate-700 ${T.sub}`}>
                                    {controlReason(row)}
                                </span>
                                <span className={`block truncate text-[12px] tabular-nums text-slate-500 ${T.note}`}>
                                    {fmtShort(row.start_date)} – {fmtDate(row.end_date)}
                                    {row.responsible_name ? ` · ${row.responsible_name}` : ''}
                                </span>
                            </span>
                            <span className="flex flex-wrap items-center gap-1.5 sm:justify-end">
                                {row.violations > 0 && (
                                    <Chip tone="red">{row.violations} {pluralViolations(row.violations)} в период</Chip>
                                )}
                                <Chip tone={row.tone === 'yellow' || row.days_left < 0 ? 'amber' : 'slate'}>
                                    <span className="tabular-nums">{daysLeftText(row.days_left)}</span>
                                </Chip>
                                <ChevronRight size={15} className="hidden text-slate-300 sm:block" />
                            </span>
                        </button>
                    </li>
                ))}
            </ul>
        </section>
    );
};

const Counter = ({ label, value }) => (
    <div className={`${iosCard} px-4 py-3 xl:px-5 xl:py-4 2xl:py-5`}>
        <div className={`text-[11px] font-semibold uppercase tracking-wider text-slate-500 ${T.caps}`}>{label}</div>
        <div className="mt-1 text-[22px] font-semibold tabular-nums text-slate-900 xl:text-[26px] 2xl:text-[30px]">{value}</div>
    </div>
);

const SummaryPanel = ({ api, toast, departmentId, version, openRecord, openOperator, onShowControls }) => {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => {
        let alive = true;
        api.summary(departmentId ? { department_id: departmentId } : undefined)
            .then((payload) => { if (alive) { setData(payload); setError(null); } })
            .catch((err) => { if (alive) setError(apiError(err, 'Сводка не загрузилась')); });
        return () => { alive = false; };
    }, [api, departmentId, version]);

    if (error) return <div className={`${iosCard} p-2`}><EmptyState title={error} /></div>;
    if (!data) return <LoadingRows rows={6} />;

    const periodLabel = data.period?.kind === 'month' ? 'с начала месяца' : `за ${data.period?.days || 30} дней`;
    const counters = data.counters || {};

    return (
        <div className="space-y-5 xl:space-y-6">
            <ControlBlock rows={data.controls || []} onOpenOperator={openOperator} onShowAll={onShowControls} />

            <section className="space-y-1.5">
                <div className={`px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500 ${T.caps}`}>
                    Текущий период — {periodLabel}
                </div>
                <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4 xl:gap-3">
                    <Counter label="Нарушений" value={counters.total ?? 0} />
                    <Counter label="С объяснительной" value={counters.with_explanation ?? 0} />
                    <Counter label="Актов составлено" value={counters.acts ?? 0} />
                    <Counter label="На контроле" value={counters.on_control ?? 0} />
                </div>
            </section>

            {(data.pre_act || []).length > 0 && (
                <section className="overflow-hidden rounded-2xl border-l-4 border-amber-400 bg-amber-50/80 ring-1 ring-amber-200/80">
                    <h2 className={`px-4 py-3 text-[14px] font-semibold text-amber-900 xl:px-5 ${T.body}`}>
                        Следующее нарушение — акт
                    </h2>
                    <ul className="divide-y divide-amber-200/60 border-t border-amber-200/60">
                        {data.pre_act.map((row) => (
                            <li key={row.operator_id}>
                                <button type="button" onClick={() => openOperator(row.operator_id)}
                                        className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-amber-100/50 xl:px-5">
                                    <span className="min-w-0 flex-1">
                                        <span className={`block truncate text-[13.5px] font-semibold text-slate-900 ${T.body}`}>{row.operator_name}</span>
                                        <span className={`block truncate text-[12px] text-slate-500 ${T.sub}`}>
                                            {row.group_name || 'Без группы'} · последнее {fmtShort(row.last_at)}, {row.last_type}
                                        </span>
                                    </span>
                                    <Chip tone="amber">
                                        <span className="tabular-nums">{row.count}</span> {pluralViolations(row.count)}
                                    </Chip>
                                </button>
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            <Group title="Последние записи" cardClassName="p-0 overflow-hidden">
                {(data.latest || []).length ? (
                    <div className="divide-y divide-slate-100">
                        {data.latest.map((item) => <RecordRow key={item.id} item={item} onOpen={openRecord} />)}
                    </div>
                ) : (
                    <EmptyState title="Записей пока нет" text="Зафиксированные нарушения появятся здесь." />
                )}
            </Group>
        </div>
    );
};

export default SummaryPanel;
