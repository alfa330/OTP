import React, { useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ChevronRight, Download, Paperclip, SlidersHorizontal } from 'lucide-react';
import { IosSegmented, IosToggle } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker } from '../ui/DateRangePicker';
import { apiError, saveBlob } from './disciplineApi';
import {
    Chip, dBtnSecondary, dLabel, EmptyState, Group, LoadingRows, RecordRow, SearchField, StateBadge, StepChip, T,
} from './disciplineUi';
import {
    fmtDateTime, fmtShort, groupSummary, groupTone, ROW_STATES_BESIDE_STEP, shiftIsoDays, STATE_LABELS, todayIso,
} from './disciplineRules';

/*
 * «Журнал нарушений» (ТЗ п. 4.2): все записи с фильтрами — оператор (поиск по
 * ФИО и номеру), период, тип, группа, кто зафиксировал (СВ), статус, наличие
 * объяснительной и акта; сортировка по любому столбцу; выгрузка в Excel.
 * Варианты в списках — по записям периода без самих этих фильтров: выбрали
 * один тип — остальные в списке остаются.
 *
 * Второй вид — «По сотрудникам», как на скриншоте заказчика: записи человека
 * собраны под шапкой «N нарушений · акт составлен», цвет шапки — красный
 * (дошли до акта), жёлтый (следующее — акт), обычный.
 */

const DATE_PRESETS = [
    { label: '30 дней', range: () => ({ from: shiftIsoDays(todayIso(), -29), to: todayIso() }) },
    { label: '90 дней', range: () => ({ from: shiftIsoDays(todayIso(), -89), to: todayIso() }) },
    { label: 'Год', range: () => ({ from: shiftIsoDays(todayIso(), -364), to: todayIso() }) },
];

const EXPLANATION_OPTIONS = [
    { value: '', label: 'Любая' },
    { value: 'yes', label: 'Есть' },
    { value: 'no', label: 'Ждём' },
    { value: 'overdue', label: 'Срок прошёл' },
];

const ACT_OPTIONS = [
    { value: '', label: 'Любой' },
    { value: 'yes', label: 'Есть' },
    { value: 'no', label: 'Нет' },
];

const COLUMNS = [
    { key: 'occurred_at', label: 'Дата' },
    { key: 'operator_name', label: 'Сотрудник' },
    { key: 'group_name', label: 'Группа' },
    { key: 'type_title', label: 'Нарушение' },
    { key: 'ordinal', label: '№' },
    { key: 'state', label: 'Статус' },
    { key: 'created_by_name', label: 'Зафиксировал' },
];

const compare = (key) => (a, b) => {
    const left = a[key] ?? '';
    const right = b[key] ?? '';
    if (typeof left === 'number' || typeof right === 'number') return (Number(left) || 0) - (Number(right) || 0);
    if (key === 'state') return (STATE_LABELS[left] || '').localeCompare(STATE_LABELS[right] || '', 'ru');
    return String(left).localeCompare(String(right), 'ru');
};

const GROUP_STYLES = {
    red: 'border-rose-500 bg-rose-50 text-rose-900',
    yellow: 'border-amber-400 bg-amber-50 text-amber-900',
    normal: 'border-emerald-500 bg-emerald-50/70 text-emerald-900',
};

// Без периода сервер берёт последние 90 дней — экран показывает то же.
const defaultFrom = () => shiftIsoDays(todayIso(), -89);

const JournalPanel = ({ api, toast, departmentId, version, openRecord, openOperator, caps }) => {
    const [filters, setFilters] = useState({
        date_from: defaultFrom(), date_to: todayIso(), search: '', type_title: '', group_id: '', created_by: '',
        state: '', explanation: '', act: '', include_cancelled: false,
    });
    const [searchDraft, setSearchDraft] = useState('');
    const [moreOpen, setMoreOpen] = useState(false);
    const [mode, setMode] = useState('list');
    const [sort, setSort] = useState({ key: 'occurred_at', dir: 'desc' });
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [exporting, setExporting] = useState(false);

    useEffect(() => {
        const timer = setTimeout(() => setFilters((prev) => (prev.search === searchDraft ? prev : { ...prev, search: searchDraft })), 300);
        return () => clearTimeout(timer);
    }, [searchDraft]);

    const params = useMemo(() => {
        const result = {};
        Object.entries(filters).forEach(([key, value]) => {
            if (value === '' || value === false || value == null) return;
            result[key] = value === true ? '1' : value;
        });
        if (departmentId) result.department_id = departmentId;
        return result;
    }, [filters, departmentId]);

    useEffect(() => {
        let alive = true;
        setData((prev) => (prev ? { ...prev, loading: true } : prev));
        api.records(params)
            .then((payload) => { if (alive) { setData(payload); setError(null); } })
            .catch((err) => { if (alive) setError(apiError(err, 'Журнал не загрузился')); });
        return () => { alive = false; };
    }, [api, params, version]);

    const items = useMemo(() => {
        const list = [...(data?.items || [])];
        list.sort(compare(sort.key));
        if (sort.dir === 'desc') list.reverse();
        return list;
    }, [data, sort]);

    const groups = useMemo(() => {
        if (mode !== 'operators') return [];
        const map = new Map();
        (data?.items || []).forEach((item) => {
            if (!map.has(item.operator_id)) map.set(item.operator_id, []);
            map.get(item.operator_id).push(item);
        });
        const order = { red: 0, yellow: 1, normal: 2 };
        return [...map.entries()].map(([operatorId, list]) => {
            const sorted = [...list].sort((a, b) => String(a.occurred_at).localeCompare(String(b.occurred_at)));
            return {
                operatorId, items: sorted, name: sorted[0]?.operator_name, group: sorted[sorted.length - 1]?.group_name,
                tone: groupTone(sorted), summary: groupSummary([...sorted].reverse()),
            };
        }).sort((a, b) => (order[a.tone] - order[b.tone]) || b.items.length - a.items.length
            || String(a.name).localeCompare(String(b.name), 'ru'));
    }, [data, mode]);

    const typeOptions = useMemo(() => [{ value: '', label: 'Все виды нарушений' },
        ...(data?.types || []).map((title) => ({ value: title, label: title }))], [data?.types]);
    const groupOptions = useMemo(() => [{ value: '', label: 'Все группы' },
        ...(data?.groups || []).map((g) => ({ value: String(g.id), label: g.name }))], [data?.groups]);
    const creatorOptions = useMemo(() => [{ value: '', label: 'Все' },
        ...(data?.creators || []).map((c) => ({ value: String(c.id), label: c.name }))], [data?.creators]);
    const stateOptions = useMemo(() => [{ value: '', label: 'Все статусы' },
        ...Object.entries(STATE_LABELS).filter(([key]) => key !== 'cancelled')
            .map(([value, label]) => ({ value, label }))], []);

    const setSortKey = (key) => setSort((prev) => ({ key, dir: prev.key === key && prev.dir === 'desc' ? 'asc' : 'desc' }));

    const exportExcel = async () => {
        setExporting(true);
        try {
            const blob = await api.exportRecords(params);
            saveBlob(blob, `Дисциплина_журнал_${todayIso()}.xlsx`);
        } catch (err) {
            toast(apiError(err, 'Выгрузка не получилась'), 'error');
        } finally {
            setExporting(false);
        }
    };

    const activeExtra = ['group_id', 'created_by', 'explanation', 'act'].filter((key) => filters[key]).length
        + (filters.include_cancelled ? 1 : 0);

    return (
        <div className="space-y-3 xl:space-y-4">
            <div className="flex flex-wrap items-center gap-2">
                <SearchField className="min-w-[220px] flex-1" placeholder="ФИО или номер записи" value={searchDraft}
                             onChange={(e) => setSearchDraft(e.target.value)} />
                <IosDateRangePicker roomy from={filters.date_from} to={filters.date_to} max={todayIso()} presets={DATE_PRESETS}
                                    onChange={({ from, to }) => setFilters((prev) => ({ ...prev, date_from: from || defaultFrom(),
                                        date_to: to || todayIso() }))} />
                <div className="w-[230px] max-w-full">
                    <CustomSelect roomy variant="ios" value={filters.type_title} options={typeOptions} ariaLabel="Вид нарушения"
                                  searchable onChange={(value) => setFilters((prev) => ({ ...prev, type_title: value || '' }))} />
                </div>
                <div className="w-[210px] max-w-full">
                    <CustomSelect roomy variant="ios" value={filters.state} options={stateOptions} ariaLabel="Статус"
                                  onChange={(value) => setFilters((prev) => ({ ...prev, state: value || '' }))} />
                </div>
                <button type="button" className={dBtnSecondary} onClick={() => setMoreOpen((v) => !v)} aria-expanded={moreOpen}>
                    <SlidersHorizontal size={15} />
                    Ещё{activeExtra ? <span className="tabular-nums"> · {activeExtra}</span> : null}
                </button>
            </div>

            {moreOpen && (
                <div className="grid gap-3 rounded-2xl bg-white p-3.5 ring-1 ring-slate-200/70 sm:grid-cols-3 xl:p-4">
                    {(groupOptions.length > 2 || filters.group_id) && (
                        <div className="space-y-1.5">
                            <div className={`${dLabel} px-1`}>Группа</div>
                            <CustomSelect variant="ios" value={filters.group_id} options={groupOptions} ariaLabel="Группа" searchable
                                          onChange={(value) => setFilters((prev) => ({ ...prev, group_id: value || '' }))} />
                        </div>
                    )}
                    {(creatorOptions.length > 2 || filters.created_by) && (
                        <div className="space-y-1.5">
                            <div className={`${dLabel} px-1`}>Зафиксировал</div>
                            <CustomSelect variant="ios" value={filters.created_by} options={creatorOptions} ariaLabel="Зафиксировал" searchable
                                          onChange={(value) => setFilters((prev) => ({ ...prev, created_by: value || '' }))} />
                        </div>
                    )}
                    <div className="space-y-1.5">
                        <div className={`${dLabel} px-1`}>Объяснительная</div>
                        <IosSegmented value={filters.explanation} options={EXPLANATION_OPTIONS} stretch
                                      onChange={(value) => setFilters((prev) => ({ ...prev, explanation: value }))} />
                    </div>
                    <div className="space-y-1.5">
                        <div className={`${dLabel} px-1`}>Акт</div>
                        <IosSegmented value={filters.act} options={ACT_OPTIONS} stretch
                                      onChange={(value) => setFilters((prev) => ({ ...prev, act: value }))} />
                    </div>
                    <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3.5 py-2.5 sm:self-end">
                        <span className={`text-[13px] text-slate-700 ${T.sub}`}>Показывать отменённые</span>
                        <IosToggle checked={filters.include_cancelled}
                                   onChange={(value) => setFilters((prev) => ({ ...prev, include_cancelled: value }))} />
                    </div>
                </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2">
                <IosSegmented value={mode} onChange={setMode} slide ariaLabel="Вид журнала"
                              options={[{ value: 'list', label: 'Записи', count: items.length },
                                  { value: 'operators', label: 'По сотрудникам' }]} />
                {!caps?.is_operator && (
                    <button type="button" className={dBtnSecondary} onClick={exportExcel} disabled={exporting}>
                        <Download size={15} />
                        {exporting ? 'Готовим файл…' : 'Excel'}
                    </button>
                )}
            </div>

            {error ? (
                <Group><EmptyState title={error} /></Group>
            ) : !data ? (
                <LoadingRows rows={8} />
            ) : !items.length ? (
                <Group><EmptyState title="Записей не найдено" text="Измените период или фильтры." /></Group>
            ) : mode === 'operators' ? (
                <div className="space-y-3">
                    {groups.map((group) => (
                        <section key={group.operatorId} className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                            <button type="button" onClick={() => openOperator(group.operatorId)}
                                    className={`flex w-full flex-wrap items-center justify-between gap-2 border-l-4 px-4 py-3 text-left xl:px-5 ${GROUP_STYLES[group.tone]}`}>
                                <span className={`text-[14px] font-semibold ${T.body}`}>
                                    {group.name}{group.group ? <span className="font-normal opacity-70"> — {group.group}</span> : null}
                                </span>
                                <span className={`text-[12.5px] font-medium ${T.sub}`}>{group.summary}</span>
                            </button>
                            <ul className="divide-y divide-slate-100">
                                {group.items.map((item) => (
                                    <li key={item.id}>
                                        <button type="button" onClick={() => openRecord(item.id)}
                                                className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-slate-50 xl:px-5">
                                            <span className={`w-[44px] shrink-0 text-[12.5px] tabular-nums text-slate-500 ${T.sub}`}>{fmtShort(item.occurred_at)}</span>
                                            <span className={`min-w-0 flex-1 truncate text-[13px] text-slate-800 ${T.sub} ${item.state === 'cancelled' ? 'line-through opacity-60' : ''}`}>
                                                {item.type_title}
                                            </span>
                                            <StepChip item={item} />
                                            {item.files_count > 0
                                                ? <Paperclip size={14} className="shrink-0 text-slate-400" aria-label="Есть файлы" />
                                                : <span className="w-[14px] shrink-0" />}
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        </section>
                    ))}
                </div>
            ) : (
                <>
                    <div className="hidden overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70 xl:block">
                        <table className="w-full text-left text-[13px] 2xl:text-[15px]">
                            <thead className="bg-slate-50/80 text-[11.5px] uppercase tracking-wide text-slate-500 2xl:text-[12.5px]">
                                <tr>
                                    {COLUMNS.map((column) => (
                                        <th key={column.key} className="px-4 py-2.5 font-semibold 2xl:px-5 2xl:py-3.5">
                                            <button type="button" onClick={() => setSortKey(column.key)}
                                                    className="inline-flex items-center gap-1 hover:text-slate-800">
                                                {column.label}
                                                {sort.key === column.key && (sort.dir === 'desc'
                                                    ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}
                                            </button>
                                        </th>
                                    ))}
                                    <th className="w-8" />
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {items.map((item) => (
                                    <tr key={item.id} onClick={() => openRecord(item.id)}
                                        className={`cursor-pointer transition hover:bg-slate-50 ${item.state === 'cancelled' ? 'opacity-60' : ''}`}>
                                        <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-slate-600 2xl:px-5 2xl:py-3.5">{fmtDateTime(item.occurred_at)}</td>
                                        <td className="px-4 py-2.5 font-medium text-slate-900 2xl:px-5">
                                            <span className="block max-w-[220px] truncate">{item.operator_name}</span>
                                            <span className="block text-[11.5px] font-normal text-slate-400">{item.number}</span>
                                        </td>
                                        <td className="px-4 py-2.5 text-slate-600 2xl:px-5">{item.group_name || '—'}</td>
                                        <td className="px-4 py-2.5 text-slate-800 2xl:px-5">
                                            <span className="block max-w-[280px] truncate">{item.type_title}</span>
                                            {item.explanation_overdue ? <span className="text-[11.5px] font-medium text-rose-600">срок объяснительной прошёл</span> : null}
                                        </td>
                                        <td className="px-4 py-2.5 tabular-nums text-slate-600 2xl:px-5">{item.ordinal ?? '—'}</td>
                                        <td className="px-4 py-2.5 2xl:px-5">
                                            <span className="flex flex-wrap items-center gap-1.5">
                                                <StepChip item={item} />
                                                {ROW_STATES_BESIDE_STEP.includes(item.state)
                                                    ? <StateBadge state={item.state} /> : null}
                                                {item.under_control && item.state !== 'cancelled' ? <Chip tone="red">Контроль</Chip> : null}
                                            </span>
                                        </td>
                                        <td className="px-4 py-2.5 text-slate-500 2xl:px-5">
                                            <span className="block max-w-[180px] truncate">{item.created_by_name}</span>
                                        </td>
                                        <td className="pr-3 text-slate-300"><ChevronRight size={15} /></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <div className="space-y-2 xl:hidden">
                        <div className="flex items-center justify-end gap-2 px-1">
                            <span className={`text-[12px] text-slate-500 ${T.note}`}>Сортировка</span>
                            <div className="w-[190px]">
                                <CustomSelect variant="ios" value={`${sort.key}:${sort.dir}`} ariaLabel="Сортировка"
                                              onChange={(value) => { const [key, dir] = String(value).split(':'); setSort({ key, dir }); }}
                                              options={[
                                                  { value: 'occurred_at:desc', label: 'Сначала новые' },
                                                  { value: 'occurred_at:asc', label: 'Сначала старые' },
                                                  { value: 'operator_name:asc', label: 'По сотруднику' },
                                                  { value: 'type_title:asc', label: 'По нарушению' },
                                                  { value: 'state:asc', label: 'По статусу' },
                                              ]} />
                            </div>
                        </div>
                        <div className="divide-y divide-slate-100 overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                            {items.map((item) => <RecordRow key={item.id} item={item} onOpen={openRecord} />)}
                        </div>
                    </div>
                    {data.truncated && (
                        <div className={`px-1 text-[12px] text-slate-500 ${T.note}`}>
                            Показаны первые записи — сузьте период, чтобы увидеть остальные.
                        </div>
                    )}
                </>
            )}
        </div>
    );
};

export default JournalPanel;
