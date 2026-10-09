import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    ChevronDown, Loader2, MessageCircle, PhoneIncoming, PhoneOutgoing, Plus, Trash2, UserRound,
} from 'lucide-react';
import {
    APPLE_FONT, IosHint, IosMenu, IosModal, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker } from '../ui/DateRangePicker';
import {
    REGISTRY_HINT, SOURCE_HINT, countsByNumber, dayLabel, daysOf, filterItems, isoDay, itemSummary,
    numberCaption, periodError, periodPresets, shiftDay, splitLabel, testsLabel, timeLabel,
} from './testNumbersMeta';

/*
 * «Реестр тестовых номеров» — номера, с которых сотрудники проверяют линии и
 * чаты. Звонки и чаты с ними не входят ни в один расчёт портала (табло,
 * отбивки, отчёты, ИИ-оценка, планы, успешки, расчёт ресурсов) — это делает
 * сервер (test_numbers/keys.py). Здесь их, наоборот, видно: сколько тестов
 * было за каждый день и какие именно.
 *
 * Реестр ведут админы и главы отделов (решение владельца 09.10.2026); сервер
 * проверяет это на каждой ручке.
 */

const errorOf = (requestError, fallback) => requestError?.response?.data?.error || fallback;

/* Окно добавления номера и смены владельца — одно: у смены владельца поле
   номера просто не показывается. */
const NumberSheet = ({ open, mode, number, people, peopleError, saving, error, onClose, onSave }) => {
    const [phone, setPhone] = useState('');
    const [ownerId, setOwnerId] = useState(null);

    useEffect(() => {
        if (!open) return;
        setPhone('');
        setOwnerId(mode === 'owner' ? (number?.owner?.id ?? null) : null);
    }, [open, mode, number]);

    const options = useMemo(() => (people || []).map((person) => ({
        value: person.id,
        label: person.name,
        meta: person.department_name || '',
    })), [people]);

    const editing = mode === 'owner';
    const canSave = !saving && ownerId != null && (editing || phone.trim().length > 0);

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={editing ? 'Владелец номера' : 'Новый тестовый номер'}
            subtitle={editing ? number?.phone_display : null}
            maxWidth="max-w-md"
            footer={(
                <div className="flex justify-end gap-2">
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                    <button
                        type="button"
                        className={iosBtnPrimary}
                        disabled={!canSave}
                        onClick={() => onSave({ phone, ownerId })}
                    >
                        {saving && <Loader2 size={15} className="animate-spin" />}
                        {editing ? 'Сохранить' : 'Добавить'}
                    </button>
                </div>
            )}
        >
            <form
                className="space-y-4"
                onSubmit={(event) => { event.preventDefault(); if (canSave) onSave({ phone, ownerId }); }}
            >
                {!editing && (
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Номер</span>
                        <input
                            className={iosInput}
                            inputMode="tel"
                            autoComplete="off"
                            placeholder="+7 700 000 00 00"
                            value={phone}
                            onChange={(event) => setPhone(event.target.value)}
                            autoFocus
                        />
                    </label>
                )}
                <div className="space-y-1.5">
                    <span className={iosGroupLabel}>Сотрудник</span>
                    <CustomSelect
                        variant="ios"
                        searchable
                        searchPlaceholder="ФИО"
                        placeholder={people ? 'Кому принадлежит номер' : 'Загружаем сотрудников…'}
                        disabled={!people}
                        options={options}
                        value={ownerId}
                        onChange={setOwnerId}
                        ariaLabel="Сотрудник, которому принадлежит номер"
                        textClassName="text-[14px] text-slate-900"
                    />
                    {peopleError && <div className="px-1 text-[12px] text-rose-600">{peopleError}</div>}
                </div>
                {error && <div className="rounded-xl bg-rose-50 px-3 py-2 text-[13px] text-rose-700">{error}</div>}
            </form>
        </IosModal>
    );
};

const RemoveSheet = ({ number, saving, error, onClose, onConfirm }) => (
    <IosModal
        open={Boolean(number)}
        onClose={onClose}
        title="Убрать номер из реестра?"
        subtitle={numberCaption(number)}
        maxWidth="max-w-md"
        footer={(
            <div className="flex justify-end gap-2">
                <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                <button
                    type="button"
                    className="inline-flex items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 py-2.5 text-[13.5px] font-semibold text-white shadow-sm transition-all hover:bg-rose-700 active:scale-[0.98] disabled:opacity-50"
                    disabled={saving}
                    onClick={onConfirm}
                >
                    {saving && <Loader2 size={15} className="animate-spin" />}
                    Убрать
                </button>
            </div>
        )}
    >
        <p className="text-[14px] leading-relaxed text-slate-600">
            Звонки и чаты с этим номером снова войдут в табло, отчёты и ИИ-оценку.
        </p>
        {error && <div className="mt-3 rounded-xl bg-rose-50 px-3 py-2 text-[13px] text-rose-700">{error}</div>}
    </IosModal>
);

const ItemIcon = ({ item }) => {
    if (item.kind === 'chat') return <MessageCircle size={15} className="text-slate-400" />;
    return item.direction === 'out'
        ? <PhoneOutgoing size={15} className="text-slate-400" />
        : <PhoneIncoming size={15} className="text-slate-400" />;
};

const ItemRow = ({ item, numbersByKey }) => {
    const number = numbersByKey.get(item.phone_key);
    const summary = itemSummary(item);
    return (
        <li className="flex items-start gap-3 px-4 py-2.5">
            <span className="w-11 shrink-0 pt-0.5 text-[13px] tabular-nums text-slate-500">{timeLabel(item.at)}</span>
            <span className="pt-0.5"><ItemIcon item={item} /></span>
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2 text-[13.5px]">
                    <span className="font-medium text-slate-900">{item.channel}</span>
                    {item.note && <span className="text-slate-500">{item.note}</span>}
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-2 text-[12.5px] text-slate-500">
                    <span className="tabular-nums">{number ? numberCaption(number) : item.phone_key}</span>
                    {item.operator && <span>оператор: {item.operator}</span>}
                    {summary && <span>{summary}</span>}
                </div>
            </div>
        </li>
    );
};

const TestNumbersView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );
    // showToast из App.jsx — новая функция на каждый рендер: держим её в ref,
    // иначе загрузка перезапускалась бы после каждого тоста.
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((...args) => toastRef.current?.(...args), []);

    const today = useMemo(() => isoDay(new Date()), []);
    const [data, setData] = useState(null);
    const [loadError, setLoadError] = useState('');
    const [people, setPeople] = useState(null);
    const [peopleError, setPeopleError] = useState('');
    const [sheet, setSheet] = useState(null);           // { mode: 'add' | 'owner', number? }
    const [sheetError, setSheetError] = useState('');
    const [removing, setRemoving] = useState(null);
    const [saving, setSaving] = useState(false);

    const [period, setPeriod] = useState({ from: shiftDay(today, -13), to: today });
    const [activity, setActivity] = useState(null);
    const [activityError, setActivityError] = useState('');
    const [activityLoading, setActivityLoading] = useState(false);
    const [numberFilter, setNumberFilter] = useState('');
    const [openDay, setOpenDay] = useState(null);

    const loadRegistry = useCallback(() => {
        setLoadError('');
        return axios.get(`${apiBaseUrl}/api/test_numbers`, { headers: headers() })
            .then((response) => setData(response.data || null))
            .catch((requestError) => setLoadError(errorOf(requestError, 'Не удалось открыть раздел')));
    }, [apiBaseUrl, headers]);

    // Ответ принимается, только если он на последний запрос: при быстрой смене периода
    // долгий ответ за 30 дней (Oktell читается живьём) иначе затёр бы ответ за 7.
    const activityRequestRef = useRef(0);
    const loadActivity = useCallback((range) => {
        const request = activityRequestRef.current + 1;
        activityRequestRef.current = request;
        const latest = () => request === activityRequestRef.current;
        const problem = periodError(range.from, range.to);
        if (problem) {
            // Дни прежнего периода под новым пикером — неправда: список убираем.
            setActivity(null);
            setActivityLoading(false);
            setActivityError(problem);
            return Promise.resolve();
        }
        setActivityLoading(true);
        setActivityError('');
        return axios.get(`${apiBaseUrl}/api/test_numbers/activity`, {
            headers: headers(),
            params: { from: range.from, to: range.to },
        })
            .then((response) => { if (latest()) setActivity(response.data || null); })
            .catch((requestError) => {
                if (latest()) setActivityError(errorOf(requestError, 'Не удалось загрузить тесты'));
            })
            .finally(() => { if (latest()) setActivityLoading(false); });
    }, [apiBaseUrl, headers]);

    useEffect(() => { loadRegistry(); }, [loadRegistry]);
    useEffect(() => { loadActivity(period); }, [loadActivity, period]);

    const ensurePeople = useCallback(() => {
        if (people) return;
        setPeopleError('');
        axios.get(`${apiBaseUrl}/api/test_numbers/people`, { headers: headers() })
            .then((response) => setPeople(response.data?.items || []))
            .catch((requestError) => setPeopleError(errorOf(requestError, 'Не удалось загрузить сотрудников')));
    }, [apiBaseUrl, headers, people]);

    const numbers = useMemo(() => data?.numbers || [], [data]);
    const canEdit = Boolean(data?.capabilities?.edit);
    const numbersByKey = useMemo(() => new Map(numbers.map((n) => [n.phone_key, n])), [numbers]);
    const allItems = useMemo(() => activity?.items || [], [activity]);
    const counts = useMemo(() => countsByNumber(allItems), [allItems]);
    const items = useMemo(() => filterItems(allItems, numberFilter), [allItems, numberFilter]);
    const days = useMemo(() => daysOf(items), [items]);
    const missing = activity?.missing || [];

    const openSheet = (mode, number = null) => {
        setSheetError('');
        setSheet({ mode, number });
        ensurePeople();
    };

    const saveSheet = ({ phone, ownerId }) => {
        if (!sheet) return;
        setSaving(true);
        setSheetError('');
        const request = sheet.mode === 'owner'
            ? axios.patch(`${apiBaseUrl}/api/test_numbers/numbers/${sheet.number.id}`,
                { owner_user_id: ownerId }, { headers: headers() })
            : axios.post(`${apiBaseUrl}/api/test_numbers/numbers`,
                { phone, owner_user_id: ownerId }, { headers: headers() });
        request
            .then(() => {
                const added = sheet.mode !== 'owner';
                setSheet(null);
                toast(added ? 'Номер добавлен в реестр' : 'Владелец сохранён', 'success');
                return Promise.all([loadRegistry(), added ? loadActivity(period) : null]);
            })
            .catch((requestError) => setSheetError(errorOf(requestError, 'Не удалось сохранить')))
            .finally(() => setSaving(false));
    };

    const confirmRemove = () => {
        if (!removing) return;
        setSaving(true);
        setSheetError('');
        axios.delete(`${apiBaseUrl}/api/test_numbers/numbers/${removing.id}`, { headers: headers() })
            .then(() => {
                if (numberFilter === removing.phone_key) setNumberFilter('');
                setRemoving(null);
                toast('Номер убран из реестра', 'success');
                return Promise.all([loadRegistry(), loadActivity(period)]);
            })
            .catch((requestError) => setSheetError(errorOf(requestError, 'Не удалось убрать номер')))
            .finally(() => setSaving(false));
    };

    const filterOptions = useMemo(() => [
        { value: '', label: 'Все номера' },
        ...numbers.map((number) => ({ value: number.phone_key, label: numberCaption(number) })),
    ], [numbers]);

    if (loadError) {
        return (
            <div className="mx-auto max-w-5xl p-4 sm:p-6" style={{ fontFamily: APPLE_FONT }}>
                <div className={`${iosCard} p-6 text-center text-[14px] text-rose-600`}>{loadError}</div>
            </div>
        );
    }
    if (!data) {
        return (
            <div className="flex min-h-[240px] items-center justify-center text-sm text-slate-500">
                <Loader2 size={18} className="mr-2 animate-spin" /> Загрузка раздела…
            </div>
        );
    }

    return (
        <div className="relative mx-auto max-w-5xl space-y-6 p-4 sm:p-6" style={{ fontFamily: APPLE_FONT }}>
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                    <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">Реестр тестовых номеров</h1>
                    <IosHint text={REGISTRY_HINT} />
                </div>
                {canEdit && (
                    <button type="button" className={iosBtnPrimary} onClick={() => openSheet('add')}>
                        <Plus size={16} /> Добавить номер
                    </button>
                )}
            </header>

            {!data.schema_ready && (
                <div className={`${iosCard} p-4 text-[14px] text-slate-600`}>
                    Раздел ещё разворачивается — обновите страницу через минуту.
                </div>
            )}

            <section className={`${iosCard} overflow-hidden`}>
                {numbers.length === 0 ? (
                    <div className="px-5 py-8 text-center text-[14px] text-slate-500">
                        Номеров пока нет. Добавьте номер, с которого вы проверяете линии или чаты.
                    </div>
                ) : (
                    <ul className="divide-y divide-slate-100">
                        {numbers.map((number) => (
                            <li key={number.id} className="flex items-center gap-3 px-4 py-3">
                                <div className="min-w-0 flex-1">
                                    <div className="text-[15px] font-semibold tabular-nums text-slate-900">
                                        {number.phone_display}
                                    </div>
                                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[13px] text-slate-500">
                                        <span className="inline-flex items-center gap-1">
                                            <UserRound size={13} className="text-slate-400" />
                                            {number.owner?.name || 'Владелец не указан'}
                                        </span>
                                        {number.owner?.department && <span>{number.owner.department}</span>}
                                        {number.owner?.fired && <span className="text-amber-600">уволен</span>}
                                    </div>
                                </div>
                                {Boolean(counts[number.phone_key]) && (
                                    <button
                                        type="button"
                                        className="shrink-0 rounded-lg px-2 py-1 text-[13px] tabular-nums text-slate-500 transition hover:bg-slate-100"
                                        onClick={() => setNumberFilter(number.phone_key)}
                                        title="Показать тесты этого номера"
                                    >
                                        {testsLabel(counts[number.phone_key])}
                                    </button>
                                )}
                                {canEdit && (
                                    <IosMenu
                                        label={`Действия с номером ${number.phone_display}`}
                                        items={[
                                            { key: 'owner', label: 'Сменить владельца', icon: UserRound,
                                              onSelect: () => openSheet('owner', number) },
                                            { key: 'remove', label: 'Убрать из реестра', icon: Trash2, danger: true,
                                              onSelect: () => { setSheetError(''); setRemoving(number); } },
                                        ]}
                                    />
                                )}
                            </li>
                        ))}
                    </ul>
                )}
            </section>

            <section className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                        <h2 className="text-[17px] font-semibold text-slate-900">Тесты по дням</h2>
                        <IosHint text={SOURCE_HINT} />
                        {activityLoading && <Loader2 size={15} className="animate-spin text-slate-400" />}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        {numbers.length > 1 && (
                            <CustomSelect
                                variant="ios"
                                className="w-64 max-w-full"
                                options={filterOptions}
                                value={numberFilter}
                                onChange={(value) => { setNumberFilter(value || ''); setOpenDay(null); }}
                                ariaLabel="Номер"
                            />
                        )}
                        <IosDateRangePicker
                            from={period.from}
                            to={period.to}
                            max={today}
                            presets={periodPresets(today)}
                            onChange={(next) => {
                                if (!next?.from || !next?.to) return;
                                setOpenDay(null);
                                setPeriod({ from: next.from, to: next.to });
                            }}
                        />
                    </div>
                </div>

                {missing.length > 0 && (
                    <div className="rounded-xl bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
                        Не показаны: {missing.map((m) => `${m.label} — ${m.reason}`).join('; ')}.
                    </div>
                )}
                {activityError && (
                    <div className="rounded-xl bg-rose-50 px-3 py-2 text-[13px] text-rose-700">{activityError}</div>
                )}

                {/* Ошибка периода или загрузки — без списка: «тестов не было» под ней было бы неправдой. */}
                {!(activityError && !activity) && (
                    <div className={`${iosCard} overflow-hidden`}>
                        {numbers.length === 0 ? (
                            <div className="px-5 py-6 text-center text-[14px] text-slate-500">
                                Здесь появятся звонки и чаты с номерами реестра.
                            </div>
                        ) : !activity && activityLoading ? (
                            <div className="flex items-center justify-center px-5 py-6 text-[14px] text-slate-500">
                                <Loader2 size={16} className="mr-2 animate-spin" /> Загружаем тесты…
                            </div>
                        ) : days.length === 0 ? (
                            <div className="px-5 py-6 text-center text-[14px] text-slate-500">
                                За выбранный период тестов не было.
                            </div>
                        ) : (
                            <ul className="divide-y divide-slate-100">
                                {days.map((day) => {
                                    const expanded = openDay === day.day;
                                    return (
                                        <li key={day.day}>
                                            <button
                                                type="button"
                                                className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50"
                                                aria-expanded={expanded}
                                                onClick={() => setOpenDay(expanded ? null : day.day)}
                                            >
                                                <span className="min-w-0 flex-1 text-[14.5px] font-medium text-slate-900">
                                                    {dayLabel(day.day, today)}
                                                </span>
                                                <span className="hidden text-[13px] tabular-nums text-slate-500 sm:inline">
                                                    {splitLabel(day)}
                                                </span>
                                                <span className="text-right text-[14px] font-semibold tabular-nums text-slate-900">
                                                    {testsLabel(day.total)}
                                                </span>
                                                <ChevronDown
                                                    size={16}
                                                    className={`shrink-0 text-slate-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
                                                />
                                            </button>
                                            {expanded && (
                                                <ul className="divide-y divide-slate-100 bg-slate-50/60">
                                                    {day.items.map((item) => (
                                                        <ItemRow key={item.id} item={item} numbersByKey={numbersByKey} />
                                                    ))}
                                                </ul>
                                            )}
                                        </li>
                                    );
                                })}
                            </ul>
                        )}
                    </div>
                )}
            </section>

            <NumberSheet
                open={Boolean(sheet)}
                mode={sheet?.mode}
                number={sheet?.number}
                people={people}
                peopleError={peopleError}
                saving={saving}
                error={sheetError}
                onClose={() => { if (!saving) setSheet(null); }}
                onSave={saveSheet}
            />
            <RemoveSheet
                number={removing}
                saving={saving}
                error={sheetError}
                onClose={() => { if (!saving) setRemoving(null); }}
                onConfirm={confirmRemove}
            />
        </div>
    );
};

export default TestNumbersView;
