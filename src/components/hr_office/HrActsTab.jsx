import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Download, History, Loader2, Search } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosPager, IosSegmented, iosCard } from '../ui/ios';
import { blobErrorText, dateText, moneyText } from './hrOffice';
import { Empty, Loading, PAGE_SIZE, TOOLBAR_BTN, TOOLBAR_BTN_PRIMARY, pagerProps, useNarrowScreen, useReloadOnRules, useServerPage, useSheetState } from './hrUi';
import HrJournalSheet from './HrJournalSheet';

/*
 * АВР (ТЗ п. 11–13): форма Р-1 по образцу HR, заказчик и исполнитель — из справочника
 * и карточки, «Услуги <должность>», период по умолчанию — с 1-го числа по сегодня.
 * Датой акта по договору считается последний день периода. Кадровик видит всех,
 * глава отдела и супервайзер — своих (границу держит сервер). Выбранные — одним
 * файлом Excel, лист на человека: печатается пачкой.
 */
const PERIODS = [
    { value: 'previous', label: 'Прошлый месяц' },
    { value: 'current', label: 'С 1-го по сегодня' },
    { value: 'custom', label: 'Свой' },
];

/* Пустая сумма в строке — «по договору»: так её и выгружает сервер (build_acts берёт сумму
   договора). Экран считает так же, иначе «итого» и файл расходились бы. */
function actAmount(person, override) {
    const raw = override?.amount;
    return raw === undefined || String(raw).trim() === '' ? person.amount : raw;
}

/* Строка и шапка — flex, а не сетка с долями: оболочка телефона (mobile-shell.css) схлопывает любые
   grid-cols-[…] в одну колонку, и на планшете и телефоне боком галочка вставала отдельной строкой.
   На телефоне (уже 640 px): галочка с именем — одна строка и одна мишень (label), поля — под ними. */
const COL_PERSON = 'min-w-0 sm:basis-0 sm:grow-[1.2]';
const COL_WORK = 'sm:basis-0 sm:grow-[1.4]';
const COL_AMOUNT = 'w-28 shrink-0 sm:w-36';
const CHECKBOX = 'h-5 w-5 shrink-0 rounded border-slate-300 text-blue-600 focus:ring-blue-500 sm:h-4 sm:w-4';
const ROW_INPUT = 'rounded-lg border-0 bg-slate-100 px-2.5 py-2 text-[14px] text-slate-800 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70 sm:py-1.5 sm:text-[13px]';

function PersonRow({ person, checked, onToggle, override, onOverride }) {
    const warn = [];
    if (!person.company_id) warn.push('нет компании');
    if (!person.contract_number) warn.push('нет номера договора');
    if (person.amount === null && !override.amount) warn.push('нет суммы');
    return (
        <div className={`flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-3 ${checked ? '' : 'opacity-70'}`}>
            <label className={`flex cursor-pointer items-center gap-3 ${COL_PERSON}`}>
                <input type="checkbox" checked={checked} onChange={() => onToggle(person.user_id)}
                       className={CHECKBOX} aria-label={person.name} />
                <span className="min-w-0 grow basis-0">
                    <span className="block truncate text-[14px] font-medium text-slate-900">{person.name}</span>
                    <span className="block truncate text-[12px] text-slate-500">
                        {[person.company, person.contract_number && `№ ${person.contract_number}`].filter(Boolean).join(' · ') || person.department}
                    </span>
                    {warn.length > 0 && (
                        <span className={`block truncate text-[12px] ${checked ? 'text-amber-700' : 'text-slate-400'}`}>{warn.join(', ')}</span>
                    )}
                    {person.last_act && (
                        <span className="block truncate text-[11.5px] text-slate-400">
                            Последний: {person.last_act.number} · {dateText(person.last_act.period_from)} – {dateText(person.last_act.period_to)}
                        </span>
                    )}
                </span>
            </label>
            <div className="flex items-center gap-2 pl-8 sm:contents">
                <input value={override.work_name ?? person.work_name} disabled={!checked}
                       onChange={(event) => onOverride(person.user_id, 'work_name', event.target.value)}
                       className={`min-w-0 grow basis-0 ${COL_WORK} ${ROW_INPUT}`}
                       aria-label="Наименование работ" placeholder={person.work_name || 'Услуги …'} />
                <input value={override.amount ?? (person.amount === null ? '' : String(person.amount))} disabled={!checked}
                       onChange={(event) => onOverride(person.user_id, 'amount', event.target.value)} inputMode="decimal"
                       className={`${COL_AMOUNT} text-right tabular-nums ${ROW_INPUT}`}
                       aria-label="Сумма" placeholder={person.amount === null ? 'сумма, ₸' : moneyText(person.amount)} />
            </div>
        </div>
    );
}

export default function HrActsTab({ api, boot, toast, rulesVersion }) {
    const [periodKind, setPeriodKind] = useState('current');
    const [custom, setCustom] = useState({ from: boot.act_periods?.current?.from || '', to: boot.act_periods?.current?.to || '' });
    const [query, setQuery] = useState('');
    const [department, setDepartment] = useState('');
    const [project, setProject] = useState('');
    const [selected, setSelected] = useState(new Set());
    const [overrides, setOverrides] = useState({});
    const [busy, setBusy] = useState(false);
    const [journal, keptJournal, openJournal, closeJournal, , journalToken] = useSheetState(false);
    const narrow = useNarrowScreen();

    /* Страницами с сервера, как «Учет сотрудников». Вместе со страницей сервер отдаёт выборку всех
       подходящих (id и сумма): «Выбрать всех» и «итого» берут и тех, кто на других страницах. */
    const list = useServerPage(api, '/acts/people', { q: query, project, department_id: department });
    const people = list.data?.people || null;
    const total = list.data?.total || 0;
    const error = list.data ? '' : list.error;
    const load = list.reload;
    useReloadOnRules(rulesVersion, load);
    const amounts = useRef(new Map());
    useEffect(() => {
        (list.data?.selection || []).forEach((item) => amounts.current.set(item.user_id, item.amount));
    }, [list.data]);
    const matching = useMemo(() => (list.data?.selection || []).map((item) => item.user_id), [list.data]);
    /* Отделы охвата — с первого ответа и дальше те же: отбор по отделу список отделов не сужает. */
    const [departmentList, setDepartmentList] = useState(null);
    useEffect(() => {
        if (list.data?.departments && !departmentList) setDepartmentList(list.data.departments);
    }, [list.data, departmentList]);

    const period = periodKind === 'custom' ? custom : (boot.act_periods?.[periodKind] || custom);
    // Сумма в строке — месячная по договору; за неполный месяц её поправляют руками.
    const wholeMonth = (() => {
        if (!period.from || !period.to || period.from.slice(0, 7) !== period.to.slice(0, 7)) return false;
        const [year, month] = period.from.split('-').map(Number);
        return period.from.endsWith('-01') && Number(period.to.slice(8, 10)) === new Date(year, month, 0).getDate();
    })();
    const departments = useMemo(() => [{ value: '', label: 'Все отделы' }]
        .concat((departmentList || []).map((item) => ({ value: item.value, label: item.label }))), [departmentList]);
    const projectOptions = [{ value: '', label: 'Все проекты' }].concat((boot.projects || []).map((item) => ({ value: item, label: item })));

    const allChecked = matching.length > 0 && matching.every((id) => selected.has(id));

    const toggle = (id) => setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
    });
    // «Выбрать всех» — всех под отбор, со всех страниц, а не только видимую.
    const toggleAll = () => setSelected((prev) => {
        const next = new Set(prev);
        matching.forEach((id) => (allChecked ? next.delete(id) : next.add(id)));
        return next;
    });
    const override = (id, key, value) => setOverrides((prev) => ({ ...prev, [id]: { ...(prev[id] || {}), [key]: value } }));

    const build = async () => {
        if (!period.from || !period.to) {
            toast?.('Укажите период', 'warning');
            return;
        }
        setBusy(true);
        try {
            const items = [...selected].map((id) => ({
                user_id: id,
                work_name: overrides[id]?.work_name,
                amount: overrides[id]?.amount,
            }));
            await api.download('post', '/acts/export', {
                body: { period_from: period.from, period_to: period.to, items }, fallback: 'АВР.xlsx',
            });
            toast?.(`АВР собраны: ${items.length}`, 'success');
            load();
        } catch (requestError) {
            toast?.(await blobErrorText(requestError, 'АВР не собрались'), 'error');
        } finally {
            setBusy(false);
        }
    };

    return (
        /* Столбик — flex/gap, а не space-y: окно журнала — его ребёнок, и space-y дал бы ему
           margin-top — белую полосу над затемнением. */
        <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
                <div className="w-full sm:w-auto">
                    <IosSegmented slide stretch={narrow} value={periodKind} onChange={setPeriodKind} options={PERIODS} ariaLabel="Период" />
                </div>
                {periodKind === 'custom' && (
                    <div className="flex w-full items-center gap-2 sm:w-auto">
                        <IosDatePicker value={custom.from} onChange={(value) => setCustom((prev) => ({ from: value, to: prev.to < value ? value : prev.to }))}
                                       ariaLabel="С" className="min-w-0 grow basis-0 sm:w-40 sm:grow-0 sm:basis-auto" />
                        <span className="text-slate-400">—</span>
                        <IosDatePicker value={custom.to} min={custom.from} onChange={(value) => setCustom((prev) => ({ ...prev, to: value }))}
                                       ariaLabel="По" className="min-w-0 grow basis-0 sm:w-40 sm:grow-0 sm:basis-auto" />
                    </div>
                )}
                {periodKind !== 'custom' && (
                    <span className="text-[13px] tabular-nums text-slate-500">{dateText(period.from)} – {dateText(period.to)}</span>
                )}
                {!wholeMonth && period.from && period.to && (
                    <span className="flex items-center gap-1 text-[12.5px] text-amber-700">
                        не полный месяц <InfoHint text="В строке — месячная сумма по договору. За неполный период поправьте её перед выгрузкой." />
                    </span>
                )}
                <div className="flex w-full gap-2 sm:ml-auto sm:w-auto">
                    <button type="button" className={TOOLBAR_BTN} onClick={() => openJournal(true)}>
                        <History size={15} /> Журнал
                    </button>
                    <button type="button" className={`${TOOLBAR_BTN_PRIMARY} min-w-0 grow sm:grow-0`} onClick={build} disabled={busy || selected.size === 0}>
                        {busy ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                        {selected.size ? `Скачать АВР · ${selected.size}` : 'Скачать АВР'}
                    </button>
                </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <div className="relative w-full sm:w-auto sm:min-w-56 sm:flex-1 sm:max-w-sm">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по ФИО"
                           className="h-10 w-full rounded-xl border-0 bg-white pl-9 pr-3 text-[14px] text-slate-900 ring-1 ring-slate-200/70 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70 sm:h-9 sm:text-[13.5px]" />
                </div>
                {departments.length > 2 && (
                    <div className="min-w-0 grow basis-0 sm:grow-0 sm:basis-auto">
                        <CustomSelect variant="ios" value={department} onChange={setDepartment} options={departments}
                                      className="w-full sm:w-56" ariaLabel="Отдел" searchable />
                    </div>
                )}
                {boot.capabilities?.acts_all && (
                    <div className="min-w-0 grow basis-0 sm:grow-0 sm:basis-auto">
                        <CustomSelect variant="ios" value={project} onChange={setProject} options={projectOptions}
                                      className="w-full sm:w-44" ariaLabel="Проект" />
                    </div>
                )}
            </div>

            {error && <div className={`${iosCard} p-4 text-[13.5px] text-rose-600`}>{error}</div>}
            {!people && !error && <Loading text="Загружаем сотрудников…" />}
            {people && (
                <div className={`${iosCard} overflow-hidden transition-opacity duration-200 ${list.loading || list.searching ? 'opacity-60' : ''}`}>
                    <div className="hidden items-center gap-3 border-b border-slate-100 px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400 sm:flex">
                        <label className={`flex cursor-pointer items-center gap-3 ${COL_PERSON}`}>
                            <input type="checkbox" checked={allChecked} onChange={toggleAll} disabled={!matching.length}
                                   aria-label={`Выбрать всех: ${matching.length}`} title={`Выбрать всех под отбор — ${matching.length}`}
                                   className={CHECKBOX} />
                            <span>Сотрудник</span>
                        </label>
                        <span className={`min-w-0 ${COL_WORK}`}>Наименование работ</span>
                        <span className={`${COL_AMOUNT} text-right`}>Сумма, ₸</span>
                    </div>
                    {/* Телефон: шапки колонок нет — «Выбрать всех» строкой над списком, мишень во всю ширину. */}
                    {matching.length > 0 && (
                        <label className="flex cursor-pointer items-center gap-3 border-b border-slate-100 px-4 py-3 sm:hidden">
                            <input type="checkbox" checked={allChecked} onChange={toggleAll} className={CHECKBOX}
                                   aria-label={`Выбрать всех: ${matching.length}`} />
                            <span className="text-[14px] font-medium text-slate-700">Выбрать всех · {matching.length}</span>
                        </label>
                    )}
                    <div className="divide-y divide-slate-100">
                        {people.length === 0
                            ? <Empty>{departmentList && departmentList.length ? 'Под отбор никто не попал' : 'Сотрудников в вашем охвате нет'}</Empty>
                            : people.map((person) => (
                                <PersonRow key={person.user_id} person={person} checked={selected.has(person.user_id)}
                                           onToggle={toggle} override={overrides[person.user_id] || {}} onOverride={override} />
                            ))}
                    </div>
                    {total > PAGE_SIZE && (
                        <div className="border-t border-slate-100 px-4 py-2.5">
                            <IosPager {...pagerProps(list.page, total)} onPage={list.setPage} unit="сотрудники" />
                        </div>
                    )}
                </div>
            )}
            {selected.size > 0 && (
                <div className="px-1 text-[12px] text-slate-500">
                    Выбрано {selected.size} · итого {moneyText([...selected].reduce((sum, id) => {
                        const raw = actAmount({ amount: amounts.current.get(id) ?? null }, overrides[id]);
                        return sum + (Number(String(raw ?? 0).replace(/\s/g, '').replace(',', '.')) || 0);
                    }, 0))} ₸
                </div>
            )}
            {keptJournal && <HrJournalSheet key={journalToken} open={journal} api={api} section="acts" today={boot.today} onClose={closeJournal} />}
        </div>
    );
}
