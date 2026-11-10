import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, History, Loader2, Search } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosSegmented, iosCard } from '../ui/ios';
import { blobErrorText, dateText, errorText, moneyText } from './hrOffice';
import { Empty, Loading, TOOLBAR_BTN, TOOLBAR_BTN_PRIMARY, useSheetState } from './hrUi';
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

function PersonRow({ person, checked, onToggle, override, onOverride }) {
    const warn = [];
    if (!person.company_id) warn.push('нет компании');
    if (!person.contract_number) warn.push('нет номера договора');
    if (person.amount === null && !override.amount) warn.push('нет суммы');
    return (
        <div className={`grid gap-2 px-4 py-3 sm:grid-cols-[auto_minmax(0,1.2fr)_minmax(0,1.4fr)_9rem] sm:items-center sm:gap-3 ${checked ? '' : 'opacity-70'}`}>
            <input type="checkbox" checked={checked} onChange={() => onToggle(person.user_id)}
                   className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" aria-label={person.name} />
            <div className="min-w-0">
                <div className="truncate text-[14px] font-medium text-slate-900">{person.name}</div>
                <div className="truncate text-[12px] text-slate-500">
                    {[person.company, person.contract_number && `№ ${person.contract_number}`].filter(Boolean).join(' · ') || person.department}
                </div>
                {warn.length > 0 && (
                    <div className={`truncate text-[12px] ${checked ? 'text-amber-700' : 'text-slate-400'}`}>{warn.join(', ')}</div>
                )}
                {person.last_act && (
                    <div className="truncate text-[11.5px] text-slate-400">
                        Последний: {person.last_act.number} · {dateText(person.last_act.period_from)} – {dateText(person.last_act.period_to)}
                    </div>
                )}
            </div>
            <input value={override.work_name ?? person.work_name} disabled={!checked}
                   onChange={(event) => onOverride(person.user_id, 'work_name', event.target.value)}
                   className="w-full rounded-lg border-0 bg-slate-100 px-2.5 py-1.5 text-[13px] text-slate-800 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70"
                   aria-label="Наименование работ" placeholder={person.work_name || 'Услуги …'} />
            <input value={override.amount ?? (person.amount === null ? '' : String(person.amount))} disabled={!checked}
                   onChange={(event) => onOverride(person.user_id, 'amount', event.target.value)} inputMode="decimal"
                   className="w-full rounded-lg border-0 bg-slate-100 px-2.5 py-1.5 text-right text-[13px] tabular-nums text-slate-800 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70"
                   aria-label="Сумма" placeholder={person.amount === null ? 'сумма, ₸' : moneyText(person.amount)} />
        </div>
    );
}

export default function HrActsTab({ api, boot, toast }) {
    const [periodKind, setPeriodKind] = useState('current');
    const [custom, setCustom] = useState({ from: boot.act_periods?.current?.from || '', to: boot.act_periods?.current?.to || '' });
    const [people, setPeople] = useState(null);
    const [error, setError] = useState('');
    const [query, setQuery] = useState('');
    const [department, setDepartment] = useState('');
    const [project, setProject] = useState('');
    const [selected, setSelected] = useState(new Set());
    const [overrides, setOverrides] = useState({});
    const [busy, setBusy] = useState(false);
    const [journal, keptJournal, openJournal, closeJournal, , journalToken] = useSheetState(false);

    const load = useCallback(() => {
        api.get('/acts/people')
            .then(({ data }) => { setPeople(data.people || []); setError(''); })
            .catch((requestError) => setError(errorText(requestError, 'Список не загрузился')));
    }, [api]);
    useEffect(() => { load(); }, [load]);

    const period = periodKind === 'custom' ? custom : (boot.act_periods?.[periodKind] || custom);
    // Сумма в строке — месячная по договору; за неполный месяц её поправляют руками.
    const wholeMonth = (() => {
        if (!period.from || !period.to || period.from.slice(0, 7) !== period.to.slice(0, 7)) return false;
        const [year, month] = period.from.split('-').map(Number);
        return period.from.endsWith('-01') && Number(period.to.slice(8, 10)) === new Date(year, month, 0).getDate();
    })();
    const departments = useMemo(() => {
        const seen = new Map();
        (people || []).forEach((person) => { if (person.department_id) seen.set(person.department_id, person.department); });
        return [{ value: '', label: 'Все отделы' }].concat([...seen.entries()]
            .sort((a, b) => String(a[1]).localeCompare(String(b[1]), 'ru')).map(([value, label]) => ({ value, label })));
    }, [people]);
    const projectOptions = [{ value: '', label: 'Все проекты' }].concat((boot.projects || []).map((item) => ({ value: item, label: item })));

    const visible = useMemo(() => {
        const needle = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
        return (people || []).filter((person) => {
            if (department && person.department_id !== department) return false;
            if (project && person.project !== project) return false;
            if (needle.length && !needle.every((part) => person.name.toLowerCase().includes(part))) return false;
            return true;
        });
    }, [people, query, department, project]);
    const allChecked = visible.length > 0 && visible.every((person) => selected.has(person.user_id));

    const toggle = (id) => setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
    });
    const toggleAll = () => setSelected((prev) => {
        const next = new Set(prev);
        visible.forEach((person) => (allChecked ? next.delete(person.user_id) : next.add(person.user_id)));
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
            const items = (people || []).filter((person) => selected.has(person.user_id)).map((person) => ({
                user_id: person.user_id,
                work_name: overrides[person.user_id]?.work_name,
                amount: overrides[person.user_id]?.amount,
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
                <IosSegmented slide value={periodKind} onChange={setPeriodKind} options={PERIODS} ariaLabel="Период" />
                {periodKind === 'custom' && (
                    <div className="flex items-center gap-2">
                        <IosDatePicker value={custom.from} onChange={(value) => setCustom((prev) => ({ from: value, to: prev.to < value ? value : prev.to }))}
                                       ariaLabel="С" className="w-40" />
                        <span className="text-slate-400">—</span>
                        <IosDatePicker value={custom.to} min={custom.from} onChange={(value) => setCustom((prev) => ({ ...prev, to: value }))}
                                       ariaLabel="По" className="w-40" />
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
                <div className="ml-auto flex gap-2">
                    <button type="button" className={TOOLBAR_BTN} onClick={() => openJournal(true)}>
                        <History size={15} /> Журнал
                    </button>
                    <button type="button" className={TOOLBAR_BTN_PRIMARY} onClick={build} disabled={busy || selected.size === 0}>
                        {busy ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                        {selected.size ? `Скачать АВР · ${selected.size}` : 'Скачать АВР'}
                    </button>
                </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-[220px] flex-1 sm:max-w-sm">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по ФИО"
                           className="h-9 w-full rounded-xl border-0 bg-white pl-9 pr-3 text-[13.5px] text-slate-900 ring-1 ring-slate-200/70 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70" />
                </div>
                {departments.length > 2 && (
                    <CustomSelect variant="ios" value={department} onChange={setDepartment} options={departments}
                                  className="w-56" ariaLabel="Отдел" searchable />
                )}
                {boot.capabilities?.acts_all && (
                    <CustomSelect variant="ios" value={project} onChange={setProject} options={projectOptions}
                                  className="w-44" ariaLabel="Проект" />
                )}
            </div>

            {error && <div className={`${iosCard} p-4 text-[13.5px] text-rose-600`}>{error}</div>}
            {!people && !error && <Loading text="Загружаем сотрудников…" />}
            {people && (
                <div className={`${iosCard} overflow-hidden`}>
                    <div className="hidden items-center gap-3 border-b border-slate-100 px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400 sm:grid sm:grid-cols-[auto_minmax(0,1.2fr)_minmax(0,1.4fr)_9rem]">
                        <input type="checkbox" checked={allChecked} onChange={toggleAll} aria-label="Выбрать всех"
                               className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
                        <span>Сотрудник</span>
                        <span>Наименование работ</span>
                        <span className="text-right">Сумма, ₸</span>
                    </div>
                    <div className="divide-y divide-slate-100">
                        {visible.length === 0
                            ? <Empty>{people.length ? 'Под отбор никто не попал' : 'Сотрудников в вашем охвате нет'}</Empty>
                            : visible.map((person) => (
                                <PersonRow key={person.user_id} person={person} checked={selected.has(person.user_id)}
                                           onToggle={toggle} override={overrides[person.user_id] || {}} onOverride={override} />
                            ))}
                    </div>
                </div>
            )}
            {people && selected.size > 0 && (
                <div className="px-1 text-[12px] text-slate-500">
                    Выбрано {selected.size} · итого {moneyText((people || []).filter((person) => selected.has(person.user_id))
                        .reduce((sum, person) => sum + (Number(String(actAmount(person, overrides[person.user_id]) ?? 0).replace(/\s/g, '').replace(',', '.')) || 0), 0))} ₸
                </div>
            )}
            {keptJournal && <HrJournalSheet key={journalToken} open={journal} api={api} section="acts" today={boot.today} onClose={closeJournal} />}
        </div>
    );
}
