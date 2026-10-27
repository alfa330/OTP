import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { History, Loader2, MessageSquare, Search, X } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker } from '../ui/DateRangePicker';
import { IosMenu, IosModal, IosPager, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput } from '../ui/ios';
import { STATUS_TONES, errorOf, formatPhone, shortTime } from './rentalMeta';
import { HistoryList, ICON_BTN, Loading, Pill } from './rentalUi';

/*
 * Вкладка «Реестр направленных» (#391, ТЗ 3.3 и 4).
 *
 * Оператор ОП видит свои направления; сотрудник аренды и руководители — все и ставят итоговый
 * статус и комментарий. Каждое изменение — в историю записи: кто, когда, было и стало.
 * Поиск по телефону, отборы по периоду визита, статусу и оператору. Офис один — отбора по офису
 * нет, появится, когда офисов станет больше.
 */

const STATUS_TRIGGER =
    'inline-flex h-8 max-w-full items-center rounded-lg px-1.5 transition hover:bg-slate-100 disabled:opacity-50';

// Период — белый с тонким кольцом, как поиск и выпадающие списки рядом (у пикера по умолчанию серый).
const PERIOD_TRIGGER =
    'flex h-9 items-center gap-2 rounded-xl bg-white px-3 text-[13px] font-medium text-slate-700 ring-1 ring-slate-200/70 transition hover:bg-slate-50';

const StatusCell = ({ item, statuses, canWork, saving, onChange }) => {
    const pill = <Pill tone={STATUS_TONES[item.status] || 'slate'}>{item.status_label}</Pill>;
    if (!canWork) return pill;
    return (
        <IosMenu
            label={`Статус направления ${formatPhone(item.phone)}`}
            align="left"
            disabled={saving}
            trigger={{ className: STATUS_TRIGGER, text: saving ? <Loader2 size={14} className="animate-spin text-slate-400" /> : pill }}
            items={statuses.map((status) => ({
                key: status.value, label: status.label, checked: item.status === status.value,
                onSelect: () => onChange(item, { status: status.value }),
            }))}
        />
    );
};

const CommentModal = ({ item, onClose, onSave }) => {
    const [text, setText] = useState(item.comment || '');
    const [saving, setSaving] = useState(false);
    const save = async () => {
        setSaving(true);
        try {
            await onSave(item, { comment: text });
            onClose();
        } finally {
            setSaving(false);
        }
    };
    return (
        <IosModal
            open
            onClose={onClose}
            title="Комментарий"
            subtitle={`${formatPhone(item.phone)} · ${item.card_title}`}
            footer={(
                <div className="flex justify-end gap-2">
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                    <button type="button" className={iosBtnPrimary} disabled={saving} onClick={save}>
                        {saving ? <Loader2 size={15} className="animate-spin" /> : null} Сохранить
                    </button>
                </div>
            )}
        >
            <textarea className={`${iosInput} min-h-[110px] resize-y`} value={text} maxLength={500} autoFocus
                      onChange={(event) => setText(event.target.value)} />
        </IosModal>
    );
};

const RegistryTab = ({ api, toast, caps }) => {
    const [filters, setFilters] = useState({ q: '', from: '', to: '', status: '', operator: '', page: 1 });
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState({});
    const [commenting, setCommenting] = useState(null);
    const [history, setHistory] = useState(null);

    const load = useCallback(async () => {
        const params = {};
        Object.entries(filters).forEach(([key, value]) => { if (value) params[key] = value; });
        try {
            const { data: page } = await api.get('/directions', params);
            setData(page);
            setError('');
        } catch (requestError) {
            setError(errorOf(requestError, 'Реестр не открылся — попробуйте обновить страницу'));
        }
    }, [api, filters]);

    // Поиск по телефону — с паузой: запрос на каждую цифру был бы лишним.
    useEffect(() => {
        const timer = setTimeout(load, filters.q ? 350 : 0);
        return () => clearTimeout(timer);
    }, [load, filters.q]);

    const set = (patch) => setFilters((prev) => ({ ...prev, ...patch, page: patch.page || 1 }));

    const update = async (item, patch) => {
        setBusy((prev) => ({ ...prev, [item.id]: true }));
        try {
            const { data: result } = await api.patch(`/directions/${item.id}`, patch);
            setData((prev) => ({ ...prev, items: prev.items.map((row) => (row.id === item.id ? result.direction : row)) }));
        } catch (requestError) {
            toast(errorOf(requestError, 'Не сохранилось'), 'error');
            throw requestError;
        } finally {
            setBusy((prev) => ({ ...prev, [item.id]: false }));
        }
    };
    const changeStatus = (item, patch) => update(item, patch).catch(() => {});

    const openHistory = async (item) => {
        setHistory({ item, items: null });
        try {
            const { data: log } = await api.get(`/directions/${item.id}/log`);
            setHistory({ item, items: log.items || [] });
        } catch (requestError) {
            toast(errorOf(requestError, 'История не открылась'), 'error');
            setHistory(null);
        }
    };

    const statuses = useMemo(() => data?.statuses || [], [data]);
    const canWork = Boolean(caps.work_directions);
    const all = Boolean(data?.all);

    if (error && !data) return <div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>;
    if (!data) return <Loading text="Открываем реестр…" />;

    const pageCount = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 50)));
    const fromIndex = data.total ? (data.page - 1) * data.page_size + 1 : 0;
    const toIndex = Math.min(data.total, data.page * data.page_size);
    const columns = all ? 8 : 7;

    const commentCell = (item) => {
        const text = item.comment ? <span className="line-clamp-2">{item.comment}</span> : <span className="text-slate-300">—</span>;
        if (!canWork) return text;
        return (
            <button type="button" className="w-full rounded-lg px-1.5 py-1 text-left hover:bg-slate-100" onClick={() => setCommenting(item)}>
                {text}
            </button>
        );
    };

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                <label className="relative">
                    <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={filters.q} onChange={(event) => set({ q: event.target.value })} inputMode="tel"
                           placeholder="Телефон клиента"
                           className="h-9 w-[190px] rounded-xl bg-white pl-8 pr-7 text-[13px] text-slate-800 outline-none ring-1 ring-slate-200/70 placeholder:text-slate-400 focus:ring-2 focus:ring-blue-500/30" />
                    {filters.q && (
                        <button type="button" aria-label="Очистить поиск" onClick={() => set({ q: '' })}
                                className="absolute right-1.5 top-1/2 grid h-5 w-5 -translate-y-1/2 place-items-center rounded-full text-slate-400 hover:bg-slate-200">
                            <X size={12} />
                        </button>
                    )}
                </label>
                <IosDateRangePicker from={filters.from} to={filters.to} triggerClassName={PERIOD_TRIGGER}
                                    onChange={({ from, to }) => set({ from: from || '', to: to || '' })} />
                <CustomSelect variant="ios" className="w-[180px]" value={filters.status} onChange={(value) => set({ status: value })}
                              options={[{ value: '', label: 'Все статусы' }, ...statuses]} ariaLabel="Статус" />
                {all && data.operators.length > 1 && (
                    <CustomSelect variant="ios" className="w-[200px]" value={filters.operator} searchable
                                  onChange={(value) => set({ operator: value })} ariaLabel="Оператор"
                                  options={[{ value: '', label: 'Все операторы' },
                                      ...data.operators.map((person) => ({ value: String(person.id), label: person.name }))]} />
                )}
                {(filters.from || filters.status || filters.operator) && (
                    <button type="button" className="h-9 rounded-xl px-3 text-[13px] text-slate-500 hover:bg-slate-100"
                            onClick={() => set({ from: '', to: '', status: '', operator: '' })}>
                        Сбросить
                    </button>
                )}
            </div>

            <div className={`${iosCard} overflow-hidden`}>
                <div className="hidden overflow-x-auto md:block">
                    <table className="w-full min-w-[980px] text-left text-[13px]">
                        <thead>
                            <tr className="border-b border-slate-100 text-[11.5px] font-medium uppercase tracking-wide text-slate-400">
                                <th className="px-4 py-2.5 font-medium">Визит</th>
                                <th className="px-3 py-2.5 font-medium">Телефон</th>
                                <th className="px-3 py-2.5 font-medium">Авто</th>
                                <th className="px-3 py-2.5 font-medium">Парк</th>
                                {all && <th className="px-3 py-2.5 font-medium">Оператор</th>}
                                <th className="px-3 py-2.5 font-medium">Статус</th>
                                <th className="px-3 py-2.5 font-medium">Комментарий</th>
                                <th className="w-10 px-2 py-2.5" aria-label="История" />
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                            {data.items.map((item) => (
                                <tr key={item.id} className="align-middle hover:bg-slate-50/60">
                                    <td className="whitespace-nowrap px-4 py-2 tabular-nums text-slate-700" title={`Направлен ${shortTime(item.created_at)}`}>
                                        {shortTime(item.visit_at)}
                                    </td>
                                    <td className="whitespace-nowrap px-3 py-2 font-medium tabular-nums text-slate-900">{formatPhone(item.phone)}</td>
                                    <td className="px-3 py-2">
                                        <div className="font-medium text-slate-800">{item.card_title}</div>
                                        {item.card_terms && <div className="text-[12px] text-slate-500">{item.card_terms}</div>}
                                    </td>
                                    <td className="px-3 py-2 text-slate-700">{item.park}</td>
                                    {all && <td className="px-3 py-2 text-slate-600">{item.created_by_name}</td>}
                                    <td className="px-1 py-1">
                                        <StatusCell item={item} statuses={statuses} canWork={canWork} saving={busy[item.id]} onChange={changeStatus} />
                                    </td>
                                    <td className="max-w-[260px] px-1 py-1 text-slate-600">{commentCell(item)}</td>
                                    <td className="px-2 py-1">
                                        <button type="button" className={ICON_BTN} aria-label="История" onClick={() => openHistory(item)}>
                                            <History size={15} />
                                        </button>
                                    </td>
                                </tr>
                            ))}
                            {!data.items.length && (
                                <tr><td colSpan={columns} className="px-4 py-10 text-center text-[13px] text-slate-500">
                                    {all ? 'Направлений по этому отбору нет' : 'Вы ещё никого не направляли'}
                                </td></tr>
                            )}
                        </tbody>
                    </table>
                </div>

                <ul className="divide-y divide-slate-100 md:hidden">
                    {data.items.map((item) => (
                        <li key={item.id} className="flex flex-col gap-1.5 px-4 py-3">
                            <div className="flex items-center justify-between gap-3">
                                <span className="text-[15px] font-semibold tabular-nums text-slate-900">{formatPhone(item.phone)}</span>
                                <StatusCell item={item} statuses={statuses} canWork={canWork} saving={busy[item.id]} onChange={changeStatus} />
                            </div>
                            <div className="text-[13px] text-slate-700">{item.card_title}{item.card_terms ? ` · ${item.card_terms}` : ''}</div>
                            <div className="flex flex-wrap gap-x-3 gap-y-1 text-[12.5px] text-slate-500">
                                <span className="tabular-nums">Визит {shortTime(item.visit_at)}</span>
                                <span>{item.park}</span>
                                {all && <span>{item.created_by_name}</span>}
                            </div>
                            <div className="flex items-center justify-between gap-2 text-[13px] text-slate-600">
                                <div className="-ml-1.5 min-w-0 flex-1">
                                    {canWork ? commentCell(item) : (item.comment && <span>{item.comment}</span>)}
                                </div>
                                <button type="button" className={ICON_BTN} aria-label="История" onClick={() => openHistory(item)}>
                                    <History size={15} />
                                </button>
                            </div>
                        </li>
                    ))}
                    {!data.items.length && (
                        <li className="px-4 py-10 text-center text-[13px] text-slate-500">
                            {all ? 'Направлений по этому отбору нет' : 'Вы ещё никого не направляли'}
                        </li>
                    )}
                </ul>
                {data.total > data.page_size && (
                    <div className="border-t border-slate-100 px-4 py-2.5">
                        <IosPager page={data.page} pageCount={pageCount} total={data.total} from={fromIndex} to={toIndex}
                                  onPage={(page) => set({ page })} unit="направления" />
                    </div>
                )}
            </div>

            {commenting && (
                <CommentModal item={commenting} onClose={() => setCommenting(null)} onSave={update} />
            )}
            {history && (
                <IosModal open onClose={() => setHistory(null)} title="История направления"
                          subtitle={`${formatPhone(history.item.phone)} · ${history.item.card_title} · направил ${history.item.created_by_name || '—'}`}>
                    <div className="mb-2 flex items-center gap-1.5 text-[12.5px] text-slate-500">
                        <MessageSquare size={13} /> Направлен {shortTime(history.item.created_at)}
                    </div>
                    <HistoryList items={history.items} shortTime={shortTime} />
                </IosModal>
            )}
        </div>
    );
};

export default RegistryTab;
