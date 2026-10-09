import React, { useEffect, useMemo, useState } from 'react';
import { Search, SlidersHorizontal, X } from 'lucide-react';
import { iosBtnPrimary, iosBtnSecondary, iosCard, iosInput } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import {
    EMPTY_FILTERS, HINTS, KIND_SHORT_OPTIONS, METHOD_SHORT_OPTIONS, PAYMENT_METHOD_OPTIONS, REQUEST_KIND_OPTIONS,
    STATE_OPTIONS, activeFilterCount, fmtDateShort, fmtMoney,
} from './paymentsMeta';
import { AmountInput, Choice, Field } from './paymentsUi';

/*
 * Поиск и фильтры — одни на реестр и на все три доски (ТЗ «Закуп и оплата», п. 19).
 *
 * Фильтры: компания, отдел, инициатор, исполнитель, согласующий, поставщик, тип
 * заявки, способ оплаты, статус, сумма, дата, «просрочено», «только мои».
 * Последние три — «Статус», «Срок» и «Чьи» — в панели есть у досок (`board`); у
 * реестра их роль играет полоса-легенда над таблицей («Ждут меня», «Просрочены»,
 * «Закрыты»…), и вторых таких же переключателей в панели там нет.
 * Поиск — по номеру заявки, номеру счёта, поставщику и назначению платежа.
 *
 * На виду только строка поиска и кнопка «Фильтры»: тринадцать полей над доской
 * съели бы пол-экрана. Выбранное остаётся видно чипами — их же щелчком снимают.
 */

const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-white px-3 py-2 '
    + 'text-left text-[12.5px] font-medium text-slate-700 ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all hover:bg-slate-50 '
    + 'active:scale-[0.99] focus:outline-none focus:ring-2 focus:ring-blue-500/60 '
    + '[&>span]:flex-1 [&>span]:text-left [&>span]:truncate';

const shiftDays = (days) => {
    const value = new Date();
    value.setDate(value.getDate() - days);
    return isoDate(value);
};
const DATE_PRESETS = [
    { label: 'Неделя', range: () => ({ from: shiftDays(6), to: isoDate(new Date()) }) },
    { label: 'Месяц', range: () => ({ from: shiftDays(29), to: isoDate(new Date()) }) },
    { label: 'Квартал', range: () => ({ from: shiftDays(90), to: isoDate(new Date()) }) },
];

const OVERDUE_OPTIONS = [{ value: false, label: 'Все' }, { value: true, label: 'Просроченные' }];
const MINE_OPTIONS = [{ value: false, label: 'Все' }, { value: true, label: 'Только мои' }];
const ALL = { value: '', label: 'Все' };

const PaymentsFilters = ({ search, onSearch, filters, onChange, dictionaries, users, board = false, children = null }) => {
    const [open, setOpen] = useState(false);
    /* Панель живёт в разметке, только пока открыта или сворачивается: смонтировали
       свёрнутой, следующим кадром развернули (переход высоты), после сворачивания
       сняли. Раскрылась до конца — обрезку снимаем (см. разметку панели ниже). */
    const [mounted, setMounted] = useState(false);
    const [expanded, setExpanded] = useState(false);
    const [settled, setSettled] = useState(false);
    useEffect(() => {
        if (open) {
            setMounted(true);
            let second = 0;
            const first = requestAnimationFrame(() => { second = requestAnimationFrame(() => setExpanded(true)); });
            const timer = setTimeout(() => setSettled(true), 340);
            return () => { cancelAnimationFrame(first); cancelAnimationFrame(second); clearTimeout(timer); };
        }
        setExpanded(false);
        setSettled(false);
        const timer = setTimeout(() => setMounted(false), 320);
        return () => clearTimeout(timer);
    }, [open]);
    const count = activeFilterCount(filters);
    const set = (patch) => onChange({ ...filters, ...patch });

    const userOptions = useMemo(() => [{ value: '', label: 'Все' }, ...(users || []).map((user) => ({ value: user.id, label: user.name }))], [users]);
    const entityOptions = useMemo(() => [{ value: '', label: 'Все компании' }, ...(dictionaries?.legal_entities || []).map((item) => ({ value: item.id, label: item.name }))], [dictionaries]);
    const departmentOptions = useMemo(() => [{ value: '', label: 'Все отделы' }, ...(dictionaries?.departments || []).map((item) => ({ value: item.id, label: item.name }))], [dictionaries]);
    const supplierOptions = useMemo(() => [{ value: '', label: 'Все поставщики' }, ...(dictionaries?.counterparties || []).map((item) => ({ value: item.id, label: item.name }))], [dictionaries]);

    const chips = useMemo(() => {
        const list = [];
        const nameOf = (options, id) => options.find((item) => item.value === id)?.label || id;
        const add = (key, name, label, patch) => list.push({ key, name, label, patch });
        if (filters.legal_entity_id) add('legal_entity_id', 'Компания', nameOf(entityOptions, filters.legal_entity_id), { legal_entity_id: null });
        if (filters.department_id) add('department_id', 'Отдел', nameOf(departmentOptions, filters.department_id), { department_id: null });
        if (filters.initiator_id) add('initiator_id', 'Инициатор', nameOf(userOptions, filters.initiator_id), { initiator_id: null });
        if (filters.assignee_id) add('assignee_id', 'Исполнитель', nameOf(userOptions, filters.assignee_id), { assignee_id: null });
        if (filters.approver_id) add('approver_id', 'Согласующий', nameOf(userOptions, filters.approver_id), { approver_id: null });
        if (filters.counterparty_id) add('counterparty_id', 'Поставщик', nameOf(supplierOptions, filters.counterparty_id), { counterparty_id: null });
        if (filters.request_kind) add('request_kind', 'Тип', REQUEST_KIND_OPTIONS.find((o) => o.value === filters.request_kind)?.label, { request_kind: '' });
        if (filters.payment_method) add('payment_method', 'Оплата', PAYMENT_METHOD_OPTIONS.find((o) => o.value === filters.payment_method)?.label, { payment_method: '' });
        if (filters.state) add('state', 'Статус', STATE_OPTIONS.find((o) => o.value === filters.state)?.label, { state: '' });
        if (filters.amount_from !== '' || filters.amount_to !== '') {
            add('amount', 'Сумма', `${filters.amount_from !== '' ? `от ${fmtMoney(filters.amount_from)}` : ''}${filters.amount_to !== '' ? ` до ${fmtMoney(filters.amount_to)}` : ''}`.trim(), { amount_from: '', amount_to: '' });
        }
        if (filters.date_from || filters.date_to) {
            add('dates', 'Создана', `${filters.date_from ? fmtDateShort(filters.date_from) : '…'} — ${filters.date_to ? fmtDateShort(filters.date_to) : '…'}`, { date_from: '', date_to: '' });
        }
        if (filters.overdue) add('overdue', 'Только', 'просроченные', { overdue: false });
        if (filters.mine) add('mine', 'Только', 'мои', { mine: false });
        return list;
    }, [filters, entityOptions, departmentOptions, userOptions, supplierOptions]);

    return (
        <div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <div className="relative sm:flex-1">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        type="search"
                        className={`${iosInput} pl-9`}
                        placeholder="№ заявки, номер счёта, поставщик, назначение платежа…"
                        aria-label="Поиск заявок"
                        value={search}
                        onChange={(event) => onSearch(event.target.value)}
                    />
                </div>
                <div className="flex items-center gap-2 self-start sm:self-auto">
                    <button type="button" className={`${count ? iosBtnPrimary : iosBtnSecondary} shrink-0`} aria-expanded={open} onClick={() => setOpen((prev) => !prev)}>
                        <SlidersHorizontal size={15} />
                        Фильтры
                        {count > 0 && <span className="tabular-nums">· {count}</span>}
                    </button>
                    {children}
                </div>
            </div>

            {chips.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {chips.map((chip) => (
                        <button key={chip.key} type="button" onClick={() => set(chip.patch)} title={`Убрать: ${chip.label}`} className="group inline-flex max-w-full items-center gap-1.5 rounded-full bg-white px-2.5 py-1 text-[12.5px] text-slate-700 ring-1 ring-slate-200/80 transition hover:ring-slate-300 active:scale-[0.98]">
                            <span className="text-slate-400">{chip.name}</span>
                            <span className="truncate font-medium">{chip.label}</span>
                            <X size={12} className="shrink-0 text-slate-400 group-hover:text-slate-600" />
                        </button>
                    ))}
                    <button type="button" onClick={() => onChange(EMPTY_FILTERS)} className="px-1.5 text-[12.5px] text-slate-500 underline decoration-slate-300 underline-offset-2 transition hover:text-slate-700">сбросить всё</button>
                </div>
            )}

            {/* Панель раскрывается по высоте и сворачивается обратно (переход
                grid-template-rows 0fr ↔ 1fr), а не выпрыгивает: то, что под ней, едет
                плавно. Пока сворачивается — недоступна (inert), Tab по ней не ходит.
                Обрезка — только пока панель движется: календарь «Создана» рисуется
                внутри панели, и раскрытая её обрезать не должна. */}
            {mounted && (
                <div
                    className="grid transition-[grid-template-rows,opacity] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none"
                    style={{ gridTemplateRows: expanded ? '1fr' : '0fr', opacity: expanded ? 1 : 0 }}
                    aria-hidden={!open}
                    {...(open ? {} : { inert: '' })}
                >
                    <div className={`min-h-0 ${open && settled ? 'overflow-visible' : 'overflow-hidden'}`}>
                        <div className={`${iosCard} mx-px mb-1 mt-3 p-3.5`}>
                            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                                <Field label="Компания" optionalMark={false}>
                                    <CustomSelect value={filters.legal_entity_id} onChange={(value) => set({ legal_entity_id: value || null })} options={entityOptions} placeholder="Все компании" variant="ios" ariaLabel="Компания" />
                                </Field>
                                <Field label="Отдел" optionalMark={false}>
                                    <CustomSelect value={filters.department_id} onChange={(value) => set({ department_id: value || null })} options={departmentOptions} placeholder="Все отделы" variant="ios" ariaLabel="Отдел" />
                                </Field>
                                <Field label="Инициатор" optionalMark={false}>
                                    <CustomSelect value={filters.initiator_id} onChange={(value) => set({ initiator_id: value || null })} options={userOptions} placeholder="Все" variant="ios" searchable ariaLabel="Инициатор" />
                                </Field>
                                <Field label="Исполнитель" optionalMark={false} hint="У кого заявка сейчас: человек либо его подразделение.">
                                    <CustomSelect value={filters.assignee_id} onChange={(value) => set({ assignee_id: value || null })} options={userOptions} placeholder="Все" variant="ios" searchable ariaLabel="Исполнитель" />
                                </Field>
                                <Field label="Согласующий" optionalMark={false} hint="Кому заявка ушла на согласование или кто её согласовал.">
                                    <CustomSelect value={filters.approver_id} onChange={(value) => set({ approver_id: value || null })} options={userOptions} placeholder="Все" variant="ios" searchable ariaLabel="Согласующий" />
                                </Field>
                                <Field label="Поставщик" optionalMark={false}>
                                    <CustomSelect value={filters.counterparty_id} onChange={(value) => set({ counterparty_id: value || null })} options={supplierOptions} placeholder="Все поставщики" variant="ios" searchable ariaLabel="Поставщик" />
                                </Field>
                                <Field as="div" label="Тип заявки" optionalMark={false} hint={HINTS.filterKind}>
                                    <Choice value={filters.request_kind} options={[ALL, ...KIND_SHORT_OPTIONS]} onChange={(value) => set({ request_kind: value })} stretch ariaLabel="Тип заявки" />
                                </Field>
                                <Field as="div" label="Способ оплаты" optionalMark={false} hint={HINTS.filterMethod}>
                                    <Choice value={filters.payment_method} options={[ALL, ...METHOD_SHORT_OPTIONS]} onChange={(value) => set({ payment_method: value })} stretch ariaLabel="Способ оплаты" />
                                </Field>
                                {board && (
                                    <Field label="Статус" optionalMark={false} hint="Состояние заявки. Колонки доски показывают, на каком шаге задача; статус — что с самой заявкой.">
                                        <CustomSelect value={filters.state} onChange={(value) => set({ state: value || '' })} options={[{ value: '', label: 'Все' }, ...STATE_OPTIONS]} placeholder="Все" variant="ios" ariaLabel="Статус" />
                                    </Field>
                                )}
                                {/* Кегль и рост — как у списков панели (35 px): иначе ряд стоит на пиксель вразнобой. */}
                                <Field as="div" label="Сумма, ₸" optionalMark={false}>
                                    <div className="flex items-center gap-1.5">
                                        <AmountInput className="!py-2 !text-[12.5px]" placeholder="от" ariaLabel="Сумма от" value={filters.amount_from} onChange={(value) => set({ amount_from: value || '' })} />
                                        <AmountInput className="!py-2 !text-[12.5px]" placeholder="до" ariaLabel="Сумма до" value={filters.amount_to} onChange={(value) => set({ amount_to: value || '' })} />
                                    </div>
                                </Field>
                                <Field label="Создана" optionalMark={false}>
                                    <IosDateRangePicker
                                        from={filters.date_from || ''}
                                        to={filters.date_to || ''}
                                        max={isoDate(new Date())}
                                        onChange={({ from, to }) => set({ date_from: from || '', date_to: to || '' })}
                                        presets={DATE_PRESETS}
                                        triggerClassName={DATE_TRIGGER}
                                    />
                                </Field>
                                {board && (
                                    <>
                                        <Field as="div" label="Срок" optionalMark={false} hint="Просроченные — срок оплаты прошёл, а заявка не оплачена.">
                                            <Choice value={Boolean(filters.overdue)} options={OVERDUE_OPTIONS} onChange={(value) => set({ overdue: value })} stretch ariaLabel="Просроченные" />
                                        </Field>
                                        <Field as="div" label="Чьи" optionalMark={false} hint="Только мои — заявки, которые сейчас ждут вас или ваше подразделение.">
                                            <Choice value={Boolean(filters.mine)} options={MINE_OPTIONS} onChange={(value) => set({ mine: value })} stretch ariaLabel="Только мои" />
                                        </Field>
                                    </>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default PaymentsFilters;
