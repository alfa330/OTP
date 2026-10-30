import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, X } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosModal, IosSegmented, iosBtnSecondary, iosCard } from '../ui/ios';
import {
    FUNNEL_LABELS, MOTIVATION_LABELS, MOTIVATION_TONES, STAGE_LABELS, currentMonth,
    errorOf, fullDate, monthLabel, percent, shortTime,
} from './hiringMeta';

/* Файл приходит ответом API (не подписанной ссылкой), поэтому — ссылка на объект в этом же
   окне, как у выгрузки «Аренды авто». */
const saveBlob = (blob, name) => {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
};
import { DATE_TRIGGER, Empty, Group, Loading, MonthSwitch, Pill, Stat, TOOLBAR_BTN, Table } from './hiringUi';

/*
 * Вкладка «Отчёты» (ТЗ 8.1, 8.2): итоговый дашборд воронки одним экраном — плитки, воронка
 * по этапам (ширина пропорциональна количеству, под ней таблица), потери, разрез по
 * рекрутерам и источникам (с партиями), время на этапах; отдельно — мотивация за месяц.
 *
 * Когорта — карточки, взятые в работу в отчётном месяце; «достиг этапа» — хоть раз был в
 * колонке; фильтры действуют на все блоки разом. Клик по любой цифре открывает список
 * карточек, из которых она сложилась. Рекрутеру сервер показывает отчёт только по нему.
 */

const VIEWS = [
    { value: 'dashboard', label: 'Дашборд' },
    { value: 'motivation', label: 'Мотивация' },
];

const FunnelBars = ({ rows, onCell }) => {
    const max = Math.max(1, ...rows.map((row) => row.count));
    return (
        <div className="space-y-1">
            {rows.map((row) => (
                <button key={row.stage} type="button" onClick={() => onCell(row.key, row.label)} className="group block w-full text-left">
                    <div className="flex items-center gap-3">
                        <span className="w-44 shrink-0 truncate text-[12.5px] text-slate-600">{FUNNEL_LABELS[row.stage] || row.label}</span>
                        <div className="relative h-7 grow overflow-hidden rounded-lg bg-slate-100">
                            <div className="h-full rounded-lg bg-blue-500 transition group-hover:bg-blue-600" style={{ width: `${Math.max(1.5, (row.count / max) * 100)}%` }} />
                            <span className="absolute inset-y-0 left-2 flex items-center text-[12px] font-semibold tabular-nums text-white mix-blend-difference">{row.count}</span>
                        </div>
                        <span className="w-16 shrink-0 text-right text-[12px] tabular-nums text-slate-500">{row.stage === 'in_work' ? '' : percent(row.from_previous)}</span>
                    </div>
                </button>
            ))}
        </div>
    );
};

const CardsModal = ({ api, toast, request, onClose, onOpenCard, filters }) => {
    const [rows, setRows] = useState(null);
    useEffect(() => {
        if (!request) return undefined;
        let alive = true;
        setRows(null);
        api.get('/reports/cards', { ...filters, key: request.key })
            .then(({ data }) => { if (alive) setRows(data.items); })
            .catch((error) => toast(errorOf(error, 'Список не открылся'), 'error'));
        return () => { alive = false; };
    }, [api, request, filters, toast]);
    return (
        <IosModal open={Boolean(request)} onClose={onClose} title={request?.label || ''} subtitle={rows ? `${rows.length} карточек` : ''}
                  footer={<div className="flex justify-end"><button type="button" className={iosBtnSecondary} onClick={onClose}>Закрыть</button></div>}>
            {!rows ? <Loading text="Собираем список…" /> : rows.length === 0 ? <Empty>Карточек нет</Empty> : (
                <ul className="divide-y divide-slate-100">
                    {rows.map((card) => (
                        <li key={card.id}>
                            <button type="button" onClick={() => onOpenCard(card.id)} className="flex w-full items-center gap-3 py-2 text-left hover:bg-slate-50">
                                <div className="min-w-0 grow basis-0">
                                    <div className="truncate text-[13.5px] font-medium text-slate-900">{card.full_name || 'Без имени'}{card.candidate_no ? <span className="ml-1.5 text-[12px] font-normal text-slate-400">№{card.candidate_no}</span> : null}</div>
                                    <div className="truncate text-[12px] text-slate-500">{[card.recruiter_name, card.source_name, card.vacancy_name || card.vacancy_text, card.city].filter(Boolean).join(' · ')}</div>
                                </div>
                                <Pill tone={card.stage === 'hired' ? 'green' : ['refused', 'dropped'].includes(card.stage) ? 'red' : 'slate'}>{STAGE_LABELS[card.stage]}</Pill>
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </IosModal>
    );
};

const ReportsTab = ({ api, toast, boot, caps, onOpenCard, version }) => {
    const [view, setView] = useState('dashboard');
    const [month, setMonth] = useState(currentMonth);
    // Период вместо месяца (ТЗ 8.2 «Воронка»: фильтр «период»): когорта — взятые в работу в эти даты.
    const [periodMode, setPeriodMode] = useState('month');
    const [periodFrom, setPeriodFrom] = useState('');
    const [periodTo, setPeriodTo] = useState('');
    const [recruiters, setRecruiters] = useState([]);
    const [sources, setSources] = useState([]);
    const [vacancyId, setVacancyId] = useState('');
    const [city, setCity] = useState('');
    const [stage, setStage] = useState('');
    const [stageFrom, setStageFrom] = useState('');
    const [stageTo, setStageTo] = useState('');
    const [data, setData] = useState(null);
    const [motivation, setMotivation] = useState(null);
    const [loading, setLoading] = useState(false);
    const [request, setRequest] = useState(null);

    // Период есть только у дашборда: на «Мотивации» показан переключатель месяца — по месяцу и запрос.
    const byPeriod = view === 'dashboard' && periodMode === 'period' && periodFrom && periodTo;
    const filters = useMemo(() => ({
        month: byPeriod ? undefined : month,
        from: byPeriod ? periodFrom : undefined,
        to: byPeriod ? periodTo : undefined,
        recruiters: recruiters.length ? recruiters.join(',') : undefined,
        sources: sources.length ? sources.join(',') : undefined,
        vacancy_id: vacancyId || undefined,
        city: city || undefined,
        stage: stage && (stageFrom || stageTo) ? stage : undefined,
        stage_from: stage && stageFrom ? stageFrom : undefined,
        stage_to: stage && stageTo ? stageTo : undefined,
    }), [month, byPeriod, periodFrom, periodTo, recruiters, sources, vacancyId, city, stage, stageFrom, stageTo]);

    const load = useCallback(() => {
        setLoading(true);
        const call = view === 'dashboard' ? api.get('/reports/dashboard', filters).then(({ data: payload }) => setData(payload))
            : api.get('/reports/motivation', filters).then(({ data: payload }) => setMotivation(payload));
        return call.catch((error) => toast(errorOf(error, 'Отчёт не загрузился'), 'error')).finally(() => setLoading(false));
    }, [api, filters, view, toast]);
    useEffect(() => { load(); }, [load, version]);

    const download = async () => {
        try {
            const path = view === 'dashboard' ? '/reports/dashboard/export' : '/reports/motivation/export';
            const response = await api.blob(path, filters);
            const header = response.headers?.['content-disposition'] || '';
            const match = /filename\*=UTF-8''([^;]+)/.exec(header);
            const name = match ? decodeURIComponent(match[1]) : `Воронка найма ${month}.xlsx`;
            saveBlob(response.data, name);
        } catch (error) {
            toast(errorOf(error, 'Выгрузка не удалась'), 'error');
        }
    };

    const onCell = (key, label, row) => setRequest({ key, label: row?.label ? `${row.label} · ${label}` : label });
    const dictionaries = boot.dictionaries || {};
    const stageColumns = (boot.meta?.stages ? ['in_work', 'dialed', 'invited_office', 'invited_training', 'on_training', 'on_line', 'hired'] : [])
        .map((code) => ({ key: code, label: FUNNEL_LABELS[code], align: 'right', render: (row) => row.stages[code].count, keyOf: (row) => row.stages[code].key }));
    const groupColumns = (withMotivation, extra = []) => [
        { key: 'label', label: '' },
        { key: 'taken', label: 'Взято', align: 'right', render: (row) => row.taken },
        ...stageColumns.slice(1),
        { key: 'through', label: 'Сквозная', align: 'right', render: (row) => percent(row.through) },
        { key: 'refused', label: 'Отказы', align: 'right', render: (row) => row.refused.count, keyOf: (row) => row.refused.key },
        { key: 'dropped', label: 'Отсев', align: 'right', render: (row) => row.dropped.count, keyOf: (row) => row.dropped.key },
        ...(withMotivation ? [{ key: 'motivation', label: 'Мотивация', align: 'right', render: (row) => row.motivation.count, keyOf: (row) => row.motivation.key }] : []),
        ...extra,
    ];

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
                <IosSegmented value={view} onChange={setView} options={VIEWS} ariaLabel="Отчёт" />
                {view === 'dashboard' && (
                    <IosSegmented size="xs" value={periodMode} onChange={setPeriodMode} ariaLabel="Месяц или период"
                                  options={[{ value: 'month', label: 'Месяц' }, { value: 'period', label: 'Период' }]} />
                )}
                {periodMode === 'period' && view === 'dashboard' ? (
                    <>
                        <div className="w-36"><IosDatePicker value={periodFrom} onChange={setPeriodFrom} allowEmpty placeholder="с" triggerClassName={DATE_TRIGGER} ariaLabel="Период с" /></div>
                        <div className="w-36"><IosDatePicker value={periodTo} onChange={setPeriodTo} allowEmpty placeholder="по" triggerClassName={DATE_TRIGGER} ariaLabel="Период по" /></div>
                    </>
                ) : <MonthSwitch value={month} onChange={setMonth} max={currentMonth()} />}
                {view === 'dashboard' && (
                    <>
                        <CustomSelect variant="ios" value={vacancyId} onChange={setVacancyId} className="w-44" ariaLabel="Вакансия"
                                      options={[{ value: '', label: 'Все вакансии' }, ...(dictionaries.vacancies || []).map((v) => ({ value: String(v.id), label: v.name }))]} />
                        <CustomSelect variant="ios" value={city} onChange={setCity} className="w-40" ariaLabel="Город" searchable
                                      options={[{ value: '', label: 'Все города' }, ...(dictionaries.cities || []).map((c) => ({ value: c, label: c }))]} />
                    </>
                )}
                {caps.reports && (
                    <CustomSelect variant="ios" multiple value={recruiters} onChange={setRecruiters} className="w-48" ariaLabel="Рекрутеры" placeholder="Все рекрутеры"
                                  options={(dictionaries.recruiters || []).map((r) => ({ value: String(r.id), label: r.name }))} bulkActions searchable
                                  renderValue={(vals) => (vals.length ? `Рекрутеры: ${vals.length}` : 'Все рекрутеры')} />
                )}
                <CustomSelect variant="ios" multiple value={sources} onChange={setSources} className="w-48" ariaLabel="Источники" placeholder="Все источники"
                              options={(dictionaries.sources || []).map((s) => ({ value: String(s.id), label: s.name }))} bulkActions
                              renderValue={(vals) => (vals.length ? `Источники: ${vals.length}` : 'Все источники')} />
                {view === 'dashboard' && (
                    <>
                        <CustomSelect variant="ios" value={stage} onChange={setStage} className="w-48" ariaLabel="Дата этапа" placeholder="Дата этапа"
                                      options={[{ value: '', label: 'Без фильтра по этапу' }, ...(boot.meta?.stage_dates || []).map((s) => ({ value: s.code, label: s.label }))]} />
                        {stage && (
                            <>
                                <div className="w-36"><IosDatePicker value={stageFrom} onChange={setStageFrom} allowEmpty placeholder="с" triggerClassName={DATE_TRIGGER} ariaLabel="Этап с" /></div>
                                <div className="w-36"><IosDatePicker value={stageTo} onChange={setStageTo} allowEmpty placeholder="по" triggerClassName={DATE_TRIGGER} ariaLabel="Этап по" /></div>
                                <button type="button" className={TOOLBAR_BTN} onClick={() => { setStage(''); setStageFrom(''); setStageTo(''); }} aria-label="Снять фильтр"><X size={14} /></button>
                            </>
                        )}
                    </>
                )}
                <button type="button" className={`${TOOLBAR_BTN} ml-auto`} onClick={download}><Download size={14} /> Excel</button>
            </div>

            {loading && !data && !motivation && <Loading text="Считаем…" />}

            {view === 'dashboard' && data && (
                <>
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
                        {data.totals.map((tile, index) => (
                            <Stat key={`${tile.key}-${index}`} label={tile.label} value={tile.value} unit={tile.unit ? ' %' : ''} hint={tile.hint}
                                  sub={tile.plan ? `план ${tile.plan} FTE` : undefined}
                                  tone={tile.plan ? (tile.value >= tile.plan ? 'green' : undefined) : undefined}
                                  onClick={() => onCell(tile.key, tile.label)} />
                        ))}
                    </div>

                    <div className="grid gap-4 xl:grid-cols-2">
                        <Group title={`Воронка по этапам · когорта ${monthLabel(data.filters.month || '')}`}>
                            <div className={`${iosCard} p-4`}>
                                {data.cohort_size === 0 ? <Empty>В этом месяце карточек в работу не брали</Empty> : (
                                    <>
                                        <FunnelBars rows={data.funnel} onCell={onCell} />
                                        <div className="mt-4">
                                            <Table rows={data.funnel} keyOf={(row) => row.stage} onCell={onCell} columns={[
                                                { key: 'label', label: 'Этап', render: (row) => FUNNEL_LABELS[row.stage] || row.label },
                                                { key: 'count', label: 'Кол-во', align: 'right', render: (row) => row.count, keyOf: (row) => row.key },
                                                { key: 'from_previous', label: 'От предыдущего', align: 'right', render: (row) => (row.stage === 'in_work' ? '—' : percent(row.from_previous)) },
                                                { key: 'through', label: 'Сквозная', align: 'right', render: (row) => percent(row.through) },
                                            ]} />
                                        </div>
                                    </>
                                )}
                            </div>
                        </Group>

                        <Group title="Потери">
                            <div className={`${iosCard} p-4`}>
                                {['refused', 'dropped'].map((kind) => {
                                    const block = data.losses[kind];
                                    return (
                                        <div key={kind} className="mb-4 last:mb-0">
                                            <div className="flex items-center justify-between">
                                                <button type="button" onClick={() => onCell(block.key, block.label)} className="text-[13.5px] font-semibold text-slate-900 hover:underline">
                                                    {block.label} · {block.count}
                                                </button>
                                                <span className="text-[12px] tabular-nums text-slate-500">{percent(block.share_of_received)} от взятых</span>
                                            </div>
                                            <div className="mt-1 flex flex-wrap gap-1.5">
                                                {Object.values(block.by_initiator).map((item) => (
                                                    <button key={item.key} type="button" onClick={() => onCell(item.key, item.label)} className="rounded-full bg-slate-100 px-2.5 py-0.5 text-[12px] text-slate-700 hover:bg-slate-200">
                                                        {item.label}: <span className="font-semibold tabular-nums">{item.count}</span>
                                                    </button>
                                                ))}
                                            </div>
                                            {block.reasons.length > 0 && (
                                                <ul className="mt-2 space-y-0.5">
                                                    {block.reasons.slice(0, 6).map((row) => (
                                                        <li key={row.key} className="flex items-center justify-between gap-2 text-[12.5px]">
                                                            <span className="truncate text-slate-600">{row.initiator_label} · {row.reason}</span>
                                                            <button type="button" onClick={() => onCell(row.key, row.reason)} className="shrink-0 tabular-nums text-blue-700 hover:underline">{row.count}</button>
                                                        </li>
                                                    ))}
                                                </ul>
                                            )}
                                            {kind === 'dropped' && block.by_day?.length > 0 && (
                                                <div className="mt-2 flex flex-wrap gap-1.5">
                                                    {block.by_day.map((row) => (
                                                        <button key={row.key} type="button" onClick={() => onCell(row.key, `${row.stage_label}, день ${row.day}`)} className="rounded-full bg-rose-50 px-2.5 py-0.5 text-[12px] text-rose-700 ring-1 ring-rose-100 hover:bg-rose-100">
                                                            {row.stage_label} · день {row.day}: <span className="font-semibold tabular-nums">{row.count}</span>
                                                        </button>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        </Group>
                    </div>

                    {caps.reports && (
                        <Group title="Разрез по рекрутерам">
                            <Table rows={data.by_recruiter} keyOf={(row) => String(row.id)} onCell={onCell} empty="В когорте нет карточек"
                                   columns={groupColumns(true, [
                                       { key: 'touches', label: 'Касаний', align: 'right', render: (row) => row.touches },
                                       { key: 'returned', label: 'В пул', align: 'right', render: (row) => row.returned },
                                   ])} />
                        </Group>
                    )}
                    <Group title="Разрез по источникам">
                        <Table rows={[...data.by_source, ...data.by_batch.map((row) => ({ ...row, label: `↳ ${row.label}` }))]}
                               keyOf={(row, index) => `${row.id}-${index}`} onCell={onCell} empty="В когорте нет карточек" columns={groupColumns(false)} />
                    </Group>
                    <Group title="Время на этапах">
                        <Table rows={data.time_on_stages} keyOf={(row) => row.label} columns={[
                            { key: 'label', label: 'Отрезок' },
                            { key: 'count', label: 'Карточек', align: 'right' },
                            { key: 'avg_days', label: 'Среднее, дней', align: 'right', render: (row) => (row.avg_days ?? '—') },
                            { key: 'median_days', label: 'Медиана, дней', align: 'right', render: (row) => (row.median_days ?? '—') },
                        ]} />
                    </Group>
                </>
            )}

            {view === 'motivation' && motivation && (
                <Group title={`Начисления · ${monthLabel(motivation.month)}`}>
                    {motivation.items.length === 0 ? <div className={`${iosCard}`}><Empty>Начислений за месяц нет</Empty></div> : motivation.items.map((group) => (
                        <div key={String(group.recruiter_id)} className={`${iosCard} mb-3 overflow-hidden`}>
                            <div className="flex flex-wrap items-center gap-3 px-4 py-2.5">
                                <span className="text-[13.5px] font-semibold text-slate-900">{group.recruiter_name}</span>
                                <span className="text-[12px] tabular-nums text-slate-500">засчитано {group.counted} · выплачено {group.paid} · ожидают {group.pending}</span>
                            </div>
                            <ul className="divide-y divide-slate-100">
                                {group.items.map((item) => (
                                    <li key={item.id}>
                                        <button type="button" onClick={() => onOpenCard(item.card_id)} className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-slate-50">
                                            <div className="min-w-0 grow basis-0">
                                                <div className="truncate text-[13px] text-slate-900">{item.full_name || 'Без имени'} <span className="text-slate-400">№{item.candidate_no}</span></div>
                                                <div className="truncate text-[12px] text-slate-500">{[item.vacancy_name, item.line_started_at ? `на линии с ${fullDate(item.line_started_at)}` : null, item.confirmed_at ? `подтверждено ${shortTime(item.confirmed_at)}` : null].filter(Boolean).join(' · ')}</div>
                                            </div>
                                            <Pill tone={MOTIVATION_TONES[item.status]}>{MOTIVATION_LABELS[item.status]}</Pill>
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    ))}
                </Group>
            )}

            <CardsModal api={api} toast={toast} request={request} onClose={() => setRequest(null)} onOpenCard={onOpenCard} filters={filters} />
        </div>
    );
};

export default ReportsTab;
