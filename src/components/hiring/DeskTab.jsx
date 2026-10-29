import React, { useEffect, useMemo, useState } from 'react';
import { CalendarClock, PhoneMissed, Building2, AlertTriangle } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { iosCard } from '../ui/ios';
import {
    FUNNEL_LABELS, FUNNEL_STAGES, MOTIVATION_LABELS, MOTIVATION_TONES, STAGE_LABELS, SUBSTAGE_LABELS, SUBSTAGE_TONES,
    currentMonth, errorOf, fullDate, percent, plural, shortTime,
} from './hiringMeta';
import { Empty, Group, Loading, MonthSwitch, Pill, Stat } from './hiringUi';

/*
 * Вкладка «Рабочий стол» (ТЗ 3): план найма из месячных задач, задачи на сегодня, моя воронка
 * за месяц и моя мотивация. Рекрутер видит своё, руководитель рекрутинга — всех с отбором по
 * рекрутеру. Канбан — на соседней вкладке «Воронка»: на одном экране с четырьмя блоками доска
 * из десяти колонок не читается.
 */

const TASK_GROUPS = [
    { key: 'overdue', label: 'Просроченные', icon: AlertTriangle, tone: 'red' },
    { key: 'today', label: 'Перезвонить сегодня', icon: CalendarClock },
    { key: 'retry', label: 'Повторный дозвон', icon: PhoneMissed },
    { key: 'office', label: 'Приглашены в офис сегодня', icon: Building2 },
];

const TaskRow = ({ card, onOpen }) => (
    <li>
        <button type="button" onClick={() => onOpen(card.id)}
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition hover:bg-slate-50">
            <div className="min-w-0 grow basis-0">
                <div className="truncate text-[13.5px] font-medium text-slate-900">
                    {card.full_name || 'Без имени'}{card.candidate_no ? <span className="ml-1.5 text-[12px] font-normal text-slate-400">№{card.candidate_no}</span> : null}
                </div>
                <div className="truncate text-[12px] text-slate-500">
                    {[card.vacancy_name || card.vacancy_text, card.city].filter(Boolean).join(' · ') || STAGE_LABELS[card.stage]}
                </div>
            </div>
            {card.substage && <Pill tone={SUBSTAGE_TONES[card.substage]}>{SUBSTAGE_LABELS[card.substage]}</Pill>}
            <span className="shrink-0 text-[12px] tabular-nums text-slate-500">
                {card.stage === 'invited_office' && card.office_visit_at ? shortTime(card.office_visit_at) : shortTime(card.next_contact_at)}
            </span>
        </button>
    </li>
);

const DeskTab = ({ api, toast, boot, caps, onOpenCard, version }) => {
    const [month, setMonth] = useState(currentMonth);
    const [recruiterId, setRecruiterId] = useState('');
    // Фильтры рабочего стола (ТЗ 3): вакансия, город, источник, этап — на задачи и воронку.
    const [filters, setFilters] = useState({ vacancy_id: '', city: '', source_id: '', stage: '' });
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let alive = true;
        setLoading(true);
        api.get('/desk', {
            month, recruiter_id: caps.lead && recruiterId ? recruiterId : undefined,
            vacancy_id: filters.vacancy_id || undefined, city: filters.city || undefined,
            source_id: filters.source_id || undefined, stage: filters.stage || undefined,
        })
            .then(({ data: payload }) => { if (alive) setData(payload); })
            .catch((error) => toast(errorOf(error, 'Не удалось загрузить рабочий стол'), 'error'))
            .finally(() => { if (alive) setLoading(false); });
        return () => { alive = false; };
    }, [api, month, recruiterId, filters, caps.lead, version, toast]);

    const dictionaries = boot.dictionaries || {};
    const recruiterOptions = useMemo(() => [{ value: '', label: 'Все рекрутеры' },
        ...(dictionaries.recruiters || []).map((r) => ({ value: String(r.id), label: r.name }))], [dictionaries]);
    const filterOptions = useMemo(() => ({
        vacancies: [{ value: '', label: 'Все вакансии' }, ...(dictionaries.vacancies || []).map((v) => ({ value: String(v.id), label: v.name }))],
        cities: [{ value: '', label: 'Все города' }, ...(dictionaries.cities || []).map((c) => ({ value: c, label: c }))],
        sources: [{ value: '', label: 'Все источники' }, ...(dictionaries.sources || []).map((s) => ({ value: String(s.id), label: s.name }))],
        stages: [{ value: '', label: 'Все этапы' }, ...(boot.meta?.stages || []).map((s) => ({ value: s.code, label: s.label }))],
    }), [dictionaries, boot.meta]);
    const setFilter = (key) => (value) => setFilters((prev) => ({ ...prev, [key]: value }));

    const tasks = data?.tasks || {};
    const taskTotal = Object.values(tasks).reduce((sum, items) => sum + items.length, 0);
    const funnel = data?.funnel || [];
    const funnelMax = Math.max(1, ...funnel.map((row) => row.count));

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
                <MonthSwitch value={month} onChange={setMonth} max={currentMonth()} />
                {caps.lead && (
                    <CustomSelect variant="ios" value={recruiterId} onChange={setRecruiterId} options={recruiterOptions}
                                  className="w-56" ariaLabel="Рекрутер" searchable />
                )}
                <CustomSelect variant="ios" value={filters.vacancy_id} onChange={setFilter('vacancy_id')} options={filterOptions.vacancies} className="w-44" ariaLabel="Вакансия" />
                <CustomSelect variant="ios" value={filters.city} onChange={setFilter('city')} options={filterOptions.cities} className="w-40" ariaLabel="Город" searchable />
                <CustomSelect variant="ios" value={filters.source_id} onChange={setFilter('source_id')} options={filterOptions.sources} className="w-44" ariaLabel="Источник" />
                <CustomSelect variant="ios" value={filters.stage} onChange={setFilter('stage')} options={filterOptions.stages} className="w-48" ariaLabel="Этап" />
            </div>

            {loading && !data ? <Loading text="Собираем рабочий стол…" /> : null}
            {data && (
                <div className="grid gap-4 lg:grid-cols-2">
                    {/* План найма (ТЗ 1.2, 3): месячные задачи — план FTE, факт, остаток, срок. */}
                    <Group title="План найма">
                        <div className={`${iosCard} overflow-hidden`}>
                            {data.plans.length === 0
                                ? <Empty>На этот месяц задач на найм нет{caps.manage ? ' — они заводятся в «Настройках»' : ''}</Empty>
                                : (
                                    <ul className="divide-y divide-slate-100">
                                        {data.plans.map((plan) => (
                                            <li key={plan.id} className="flex items-center gap-3 px-4 py-2.5">
                                                <div className="min-w-0 grow basis-0">
                                                    <div className="truncate text-[13.5px] font-medium text-slate-900">
                                                        {plan.vacancy_name || 'Любая вакансия'}{plan.department_name ? ` · ${plan.department_name}` : ''}
                                                    </div>
                                                    <div className="truncate text-[12px] text-slate-500">
                                                        {plan.recruiter_name ? plan.recruiter_name : 'Общий план'}
                                                        {plan.planned_date ? ` · до ${fullDate(plan.planned_date)}` : ''}
                                                    </div>
                                                </div>
                                                <div className="text-right tabular-nums">
                                                    <div className="text-[14px] font-semibold text-slate-900">
                                                        {plan.fact} <span className="font-normal text-slate-400">/ {Number(plan.plan_fte)}</span>
                                                    </div>
                                                    <div className={`text-[11.5px] ${plan.overdue ? 'text-rose-600' : 'text-slate-400'}`}>
                                                        {plan.remaining > 0 ? `осталось ${plan.remaining}` : 'закрыт'}{plan.overdue ? ' · просрочен' : ''}
                                                    </div>
                                                </div>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                        </div>
                    </Group>

                    {/* Задачи на сегодня (ТЗ 3). */}
                    <Group title={`Задачи на сегодня${taskTotal ? ` · ${taskTotal}` : ''}`}>
                        <div className={`${iosCard} overflow-hidden`}>
                            {taskTotal === 0 ? <Empty>На сегодня задач нет</Empty> : TASK_GROUPS.map((group) => (tasks[group.key] || []).length ? (
                                <div key={group.key}>
                                    <div className={`flex items-center gap-1.5 px-4 pt-3 pb-1 text-[11.5px] font-semibold uppercase tracking-wide ${group.tone === 'red' ? 'text-rose-600' : 'text-slate-500'}`}>
                                        <group.icon size={13} /> {group.label} · {tasks[group.key].length}
                                    </div>
                                    <ul className="divide-y divide-slate-100">
                                        {tasks[group.key].map((card) => <TaskRow key={card.id} card={card} onOpen={onOpenCard} />)}
                                    </ul>
                                </div>
                            ) : null)}
                        </div>
                    </Group>

                    {/* Моя воронка (ТЗ 3): взятые в работу за месяц и конверсия между этапами. */}
                    <Group title={recruiterId || !caps.lead ? 'Моя воронка' : 'Воронка за месяц'}>
                        <div className={`${iosCard} p-4`}>
                            {funnel.every((row) => !row.count)
                                ? <Empty>За этот месяц карточек в работу не брали</Empty>
                                : (
                                    <ul className="space-y-2">
                                        {funnel.map((row) => (
                                            <li key={row.stage} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0.5">
                                                <div className="flex items-center justify-between text-[12.5px] text-slate-600">
                                                    <span className="truncate">{FUNNEL_LABELS[row.stage] || row.label}</span>
                                                    <span className="tabular-nums text-slate-400">{row.stage === 'in_work' ? '' : percent(row.from_previous)}</span>
                                                </div>
                                                <span className="w-10 text-right text-[13.5px] font-semibold tabular-nums text-slate-900">{row.count}</span>
                                                <div className="col-span-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                                                    <div className="h-full rounded-full bg-blue-500" style={{ width: `${Math.max(2, (row.count / funnelMax) * 100)}%` }} />
                                                </div>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                        </div>
                    </Group>

                    {/* Моя мотивация (ТЗ 7.2): засчитано за месяц, ожидают подтверждения 7 смен. */}
                    <Group title={recruiterId || !caps.lead ? 'Моя мотивация' : 'Мотивация за месяц'}>
                        <div className="grid grid-cols-2 gap-3">
                            <Stat label="Засчитано за месяц" value={data.motivation.counted} tone={data.motivation.counted ? 'green' : undefined} />
                            <Stat label="Ожидают 7 смен" value={data.motivation.pending} />
                        </div>
                        {data.motivation.items.length > 0 && (
                            <div className={`${iosCard} mt-3 overflow-hidden`}>
                                <ul className="divide-y divide-slate-100">
                                    {data.motivation.items.map((item) => (
                                        <li key={item.id}>
                                            <button type="button" onClick={() => onOpenCard(item.card_id)}
                                                    className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition hover:bg-slate-50">
                                                <div className="min-w-0 grow basis-0">
                                                    <div className="truncate text-[13.5px] font-medium text-slate-900">
                                                        {item.full_name || 'Без имени'}<span className="ml-1.5 text-[12px] font-normal text-slate-400">№{item.candidate_no}</span>
                                                    </div>
                                                    <div className="truncate text-[12px] text-slate-500">
                                                        {[item.vacancy_name, caps.lead && !recruiterId ? item.recruiter_name : null,
                                                            item.status === 'pending' ? `${item.shifts_done} из ${boot.meta.shifts_to_hire} ${plural(boot.meta.shifts_to_hire, 'смены', 'смен', 'смен')}` : null,
                                                            item.confirmed_at ? `подтверждено ${shortTime(item.confirmed_at)}` : null].filter(Boolean).join(' · ')}
                                                    </div>
                                                </div>
                                                <Pill tone={MOTIVATION_TONES[item.status]}>{MOTIVATION_LABELS[item.status]}</Pill>
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}
                    </Group>
                </div>
            )}
        </div>
    );
};

export default DeskTab;
