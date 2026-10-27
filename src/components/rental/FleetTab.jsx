import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { History, Loader2, Minus, Pencil, Plus, RefreshCw, Send, Trash2 } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import IosTimePicker from '../ui/TimePicker';
import { IosMenu, IosModal, IosToggle, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput } from '../ui/ios';
import { CARD_FIELDS, INSURANCE_OPTIONS, addDays, errorOf, formatPrice, freeState, shortTime } from './rentalMeta';
import { HistoryList, ICON_BTN, Loading, Notice, Pill, TOOLBAR_BTN } from './rentalUi';

/*
 * Вкладка «Наличие авто» (#391).
 *
 * Карточка — вариант модели с условиями. Свободные машины вводят сотрудники аренды (ТЗ v5, 3.1):
 * у каждой карточки своё число, и путевой лист — отдельная карточка со своим. Из Yume приходят
 * только занятые — в аренде и в брони, по модели. Свободных 0 — «Нет в наличии», не указано ни
 * разу — «Наличие не указано»; в обоих случаях направить нельзя. Направление машину не бронирует
 * (ТЗ 3.2): число свободных после него прежнее.
 */

const FREE_TONES = { free: 'green', none: 'red', unknown: 'slate' };

const Spec = ({ label, children }) => (
    <>
        <dt className="text-slate-500">{label}</dt>
        <dd className="text-right font-medium text-slate-800">{children || '—'}</dd>
    </>
);

/* Поля даты и времени — того же роста и цвета, что iosInput: окно читается одной формой
   (так же сделана форма жалоб). Свой класс триггера заменяет весь стандартный, поэтому
   flex и выравнивание — здесь же. */
const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 text-[14px] tabular-nums '
    + 'text-slate-900 transition hover:bg-slate-200/70 focus:outline-none focus:ring-2 focus:ring-blue-500/70 '
    + '[&>span]:flex-1 [&>span]:text-left';
const TIME_INPUT = 'w-full rounded-xl border-0 bg-slate-100 px-6 py-2.5 text-center text-[14px] tabular-nums text-slate-900 '
    + 'placeholder-slate-400 transition hover:bg-slate-200/70 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70';

/* Свободные для сотрудника аренды: −/+ и «Сохранить», когда число изменилось. Сохранение —
   явное, а не на каждый щелчок: иначе каждый промах ложился бы в историю карточки.
   Ни разу не указанное число — пустое поле с «—», а не ноль: ноль значит «нет в наличии»,
   и пятнадцать кнопок «Сохранить» на нетронутых карточках были бы шумом. */
const FreeStepper = ({ card, onSave }) => {
    const saved = card.free_count;
    const [value, setValue] = useState(saved);
    const [saving, setSaving] = useState(false);
    useEffect(() => { setValue(card.free_count); }, [card.free_count]);
    const changed = value !== null && value !== undefined && value !== saved;
    const save = async () => {
        setSaving(true);
        try {
            await onSave(card, value);
        } finally {
            setSaving(false);
        }
    };
    return (
        <div className="flex items-center gap-2">
            <span className="text-[12.5px] text-slate-500">Свободно</span>
            <div className="flex items-center rounded-xl bg-slate-100">
                <button type="button" className="grid h-8 w-8 place-items-center rounded-xl text-slate-600 hover:bg-slate-200 disabled:opacity-40"
                        aria-label="Меньше" disabled={!value || saving} onClick={() => setValue((v) => Math.max(0, (v || 0) - 1))}>
                    <Minus size={14} />
                </button>
                <input
                    value={value ?? ''}
                    placeholder="—"
                    inputMode="numeric"
                    aria-label={`Свободно: ${card.title}`}
                    onChange={(event) => {
                        const digits = event.target.value.replace(/\D/g, '').slice(0, 3);
                        setValue(digits === '' ? null : Number(digits));
                    }}
                    // Стёрли и ушли из поля — возвращаем сохранённое: пустое поле при числе в базе путало бы.
                    onBlur={() => { if (value === null) setValue(saved); }}
                    onKeyDown={(event) => { if (event.key === 'Enter' && changed) save(); }}
                    className="h-8 w-10 bg-transparent text-center text-[14px] font-semibold tabular-nums text-slate-900 outline-none placeholder:text-slate-400"
                />
                <button type="button" className="grid h-8 w-8 place-items-center rounded-xl text-slate-600 hover:bg-slate-200 disabled:opacity-40"
                        aria-label="Больше" disabled={value >= 999 || saving} onClick={() => setValue((v) => Math.min(999, (v || 0) + 1))}>
                    <Plus size={14} />
                </button>
            </div>
            {changed && (
                <button type="button" onClick={save} disabled={saving}
                        className="inline-flex h-8 items-center gap-1 rounded-xl bg-blue-600 px-3 text-[12.5px] font-semibold text-white hover:bg-blue-700 disabled:opacity-50">
                    {saving ? <Loader2 size={13} className="animate-spin" /> : null} Сохранить
                </button>
            )}
        </div>
    );
};

const CarCard = ({ card, caps, onSend, onFree, onEdit, onHistory, onHide }) => {
    const state = freeState(card);
    return (
        <article className={`${iosCard} flex flex-col gap-3 p-4`}>
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <h3 className="truncate text-[15.5px] font-semibold text-slate-900">{card.title}</h3>
                    <div className="truncate text-[12.5px] text-slate-500">
                        {[card.car_class, card.fuel].filter(Boolean).join(' · ')}
                    </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                    <Pill tone={FREE_TONES[state.kind]}>{state.label}</Pill>
                    {caps.edit_cards && (
                        <IosMenu
                            label={`Действия: ${card.title}`}
                            items={[
                                { key: 'edit', label: 'Изменить карточку', icon: Pencil, onSelect: () => onEdit(card) },
                                { key: 'log', label: 'История правок', icon: History, onSelect: () => onHistory(card) },
                                { key: 'hide', label: 'Скрыть карточку', icon: Trash2, danger: true, separatorBefore: true,
                                  onSelect: () => onHide(card) },
                            ]}
                        />
                    )}
                </div>
            </div>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[13px]">
                <Spec label="Стоимость">{formatPrice(card.price)}</Spec>
                <Spec label="Год выпуска">{card.year}</Spec>
                <Spec label="Цвет">{card.color}</Spec>
                <Spec label="Возраст">{card.age}</Spec>
                <Spec label="Класс страховки">{card.insurance_class}</Spec>
                <Spec label="Страховка">{card.insurance}</Spec>
                <Spec label="Первый день">{card.first_day}</Spec>
            </dl>
            <div className="mt-auto flex flex-col gap-2 border-t border-slate-100 pt-3">
                {caps.edit_cards && <FreeStepper card={card} onSave={onFree} />}
                {/* Без свободных кнопка серая и не нажимается; почему — уже сказано меткой в углу карточки. */}
                {caps.send && (
                    <button type="button" className={`${state.kind === 'free' ? iosBtnPrimary : iosBtnSecondary} w-full`}
                            disabled={state.kind !== 'free'} onClick={() => onSend(card)}>
                        <Send size={15} /> Направить в офис
                    </button>
                )}
                <div className="flex flex-col gap-0.5 text-[12px] text-slate-500">
                    <span>По Yume: в аренде {card.busy.in_rent}{card.busy.booked ? `, в брони ${card.busy.booked}` : ''}</span>
                    {card.free_at && <span>Свободные: {card.free_by || 'без имени'}, {shortTime(card.free_at)}</span>}
                </div>
            </div>
        </article>
    );
};

const EMPTY_SEND = { phone: '', visit_date: '', visit_time: '', park: '', comment: '' };

const SendModal = ({ card, data, api, toast, onClose, onSent }) => {
    const today = String(data.server_time || '').slice(0, 10);
    const [form, setForm] = useState({ ...EMPTY_SEND, visit_date: today });
    const [saving, setSaving] = useState(false);
    const set = (patch) => setForm((prev) => ({ ...prev, ...patch }));
    const ready = form.phone.replace(/\D/g, '').length >= 10 && form.visit_date && form.visit_time && form.park;
    const submit = async () => {
        setSaving(true);
        try {
            await api.post('/directions', { ...form, card_id: card.id });
            toast('Клиент направлен — запись «Ожидаем» в реестре', 'success');
            onSent();
        } catch (requestError) {
            toast(errorOf(requestError, 'Направление не сохранилось'), 'error');
        } finally {
            setSaving(false);
        }
    };
    return (
        <IosModal
            open
            onClose={onClose}
            title="Направить в офис"
            subtitle={[card.title, card.insurance, card.insurance_class].filter(Boolean).join(' · ')}
            footer={(
                <div className="flex justify-end gap-2">
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                    <button type="button" className={iosBtnPrimary} disabled={!ready || saving} onClick={submit}>
                        {saving ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />} Направить
                    </button>
                </div>
            )}
        >
            <div className="space-y-3.5">
                <div className="rounded-xl bg-slate-50 px-3.5 py-2.5 text-[12.5px] leading-snug text-slate-600">
                    {[card.car_class, formatPrice(card.price)].filter(Boolean).join(' · ')}
                    <br />
                    Офис: {card.office || data.office?.name}
                </div>
                <label className="block space-y-1.5">
                    <span className="text-[12.5px] font-medium text-slate-600">Телефон клиента</span>
                    <input className={iosInput} inputMode="tel" placeholder="+7 7__ ___ __ __" value={form.phone}
                           onChange={(event) => set({ phone: event.target.value })} autoFocus />
                </label>
                <div className="grid grid-cols-[1fr_120px] gap-3">
                    <div className="space-y-1.5">
                        <span className="block text-[12.5px] font-medium text-slate-600">Дата визита</span>
                        <IosDatePicker value={form.visit_date} min={today} max={addDays(today, 31)}
                                       onChange={(value) => set({ visit_date: value || '' })} ariaLabel="Дата визита"
                                       className="w-full" triggerClassName={DATE_TRIGGER} />
                    </div>
                    <div className="space-y-1.5">
                        <span className="block text-[12.5px] font-medium text-slate-600">Время</span>
                        <IosTimePicker value={form.visit_time} onChange={(value) => set({ visit_time: value || '' })}
                                       step={15} placeholder="--:--" ariaLabel="Время визита"
                                       className="w-full" inputClassName={TIME_INPUT} />
                    </div>
                </div>
                <div className="space-y-1.5">
                    <span className="block text-[12.5px] font-medium text-slate-600">Парк, который предложили клиенту</span>
                    <CustomSelect variant="ios" value={form.park} onChange={(value) => set({ park: value })}
                                  options={(data.parks || []).map((park) => ({ value: park, label: park }))}
                                  placeholder="Выберите парк" searchable ariaLabel="Парк" />
                </div>
                <label className="block space-y-1.5">
                    <span className="text-[12.5px] font-medium text-slate-600">Комментарий</span>
                    <textarea className={`${iosInput} min-h-[72px] resize-y`} value={form.comment} maxLength={500}
                              placeholder="Необязательно — например, будет после 17:00"
                              onChange={(event) => set({ comment: event.target.value })} />
                </label>
            </div>
        </IosModal>
    );
};

const CardEditModal = ({ card, api, toast, onClose, onSaved }) => {
    const [form, setForm] = useState(() => {
        const base = {};
        CARD_FIELDS.forEach(({ key }) => { base[key] = card?.[key] ?? ''; });
        base.price = card?.price ? String(card.price) : '';
        base.yume_keys = (card?.yume_keys || []).join(', ');
        return base;
    });
    const [saving, setSaving] = useState(false);
    const set = (patch) => setForm((prev) => ({ ...prev, ...patch }));
    const submit = async () => {
        setSaving(true);
        try {
            if (card) await api.put(`/cards/${card.id}`, form);
            else await api.post('/cards', form);
            toast(card ? 'Карточка сохранена' : 'Карточка добавлена', 'success');
            onSaved();
        } catch (requestError) {
            toast(errorOf(requestError, 'Карточка не сохранилась'), 'error');
        } finally {
            setSaving(false);
        }
    };
    return (
        <IosModal
            open
            onClose={onClose}
            title={card ? 'Изменить карточку' : 'Новая карточка'}
            subtitle={card ? card.title : 'Модель с условиями: класс, цена, страховка'}
            maxWidth="max-w-xl"
            footer={(
                <div className="flex justify-end gap-2">
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                    <button type="button" className={iosBtnPrimary} disabled={saving} onClick={submit}>
                        {saving ? <Loader2 size={15} className="animate-spin" /> : null} Сохранить
                    </button>
                </div>
            )}
        >
            <div className="grid gap-3 sm:grid-cols-2">
                {CARD_FIELDS.map(({ key, label }) => (
                    <label key={key} className={`block space-y-1.5 ${key === 'title' || key === 'office' ? 'sm:col-span-2' : ''}`}>
                        <span className="text-[12.5px] font-medium text-slate-600">{label}</span>
                        {key === 'insurance' ? (
                            <CustomSelect variant="ios" value={form.insurance} onChange={(value) => set({ insurance: value })}
                                          options={INSURANCE_OPTIONS} ariaLabel={label} />
                        ) : (
                            <input className={iosInput} value={form[key]} inputMode={key === 'price' ? 'numeric' : undefined}
                                   onChange={(event) => set({ [key]: event.target.value })} />
                        )}
                    </label>
                ))}
                <label className="block space-y-1.5 sm:col-span-2">
                    <span className="text-[12.5px] font-medium text-slate-600">Как модель записана в Yume — через запятую</span>
                    <input className={iosInput} value={form.yume_keys} placeholder="Hongqi E-QM5, HONGQI EQM5"
                           onChange={(event) => set({ yume_keys: event.target.value })} />
                    <span className="block text-[12px] text-slate-400">По этим названиям карточка находит занятые машины в Yume.</span>
                </label>
            </div>
        </IosModal>
    );
};

const FleetTab = ({ api, toast }) => {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [carClass, setCarClass] = useState('');
    const [onlyFree, setOnlyFree] = useState(null);
    const [sending, setSending] = useState(null);
    const [editing, setEditing] = useState(null);
    const [history, setHistory] = useState(null);
    const [hiding, setHiding] = useState(null);

    const load = useCallback(async () => {
        try {
            const { data: fleet } = await api.get('/fleet');
            setData(fleet);
            setError('');
        } catch (requestError) {
            setError(errorOf(requestError, 'Наличие не открылось — попробуйте обновить страницу'));
        }
    }, [api]);

    useEffect(() => { load(); }, [load]);
    // Пока Yume обновляется — перечитываем через 4 секунды: снимок занятых готов за секунды.
    useEffect(() => {
        if (!data?.sync?.refreshing) return undefined;
        const timer = setTimeout(load, 4000);
        return () => clearTimeout(timer);
    }, [data, load]);

    const caps = data?.capabilities || {};
    // По умолчанию оператору — только доступные, аренде — все: она ведёт и те, где свободных нет.
    const showOnlyFree = onlyFree ?? !caps.edit_cards;
    const classes = useMemo(() => [...new Set((data?.cards || []).map((card) => card.car_class).filter(Boolean))], [data]);
    const cards = useMemo(() => (data?.cards || []).filter((card) => (
        (!carClass || card.car_class === carClass) && (!showOnlyFree || freeState(card).kind === 'free')
    )), [data, carClass, showOnlyFree]);

    const saveFree = async (card, value) => {
        try {
            const { data: result } = await api.put(`/cards/${card.id}/free`, { free_count: value });
            setData((prev) => ({ ...prev, cards: prev.cards.map((item) => (item.id === card.id ? { ...result.card, busy: item.busy } : item)) }));
        } catch (requestError) {
            toast(errorOf(requestError, 'Не сохранилось'), 'error');
        }
    };

    const openHistory = async (card) => {
        setHistory({ card, items: null });
        try {
            const { data: log } = await api.get(`/cards/${card.id}/log`);
            setHistory({ card, items: log.items || [] });
        } catch (requestError) {
            toast(errorOf(requestError, 'История не открылась'), 'error');
            setHistory(null);
        }
    };

    const hide = async () => {
        try {
            await api.delete(`/cards/${hiding.id}`);
            toast('Карточка скрыта', 'success');
            setHiding(null);
            load();
        } catch (requestError) {
            toast(errorOf(requestError, 'Не получилось'), 'error');
        }
    };

    const refreshYume = async () => {
        try {
            await api.post('/fleet/refresh');
        } catch (requestError) {
            toast(errorOf(requestError, 'Обновление не началось'), 'error');
        }
        load();
    };

    if (error && !data) return <div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>;
    if (!data) return <Loading text="Открываем наличие…" />;

    const sync = data.sync || {};
    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0 text-[13px] leading-snug text-slate-600">
                    <span className="font-medium text-slate-800">Офис выдачи:</span> {data.office?.name}
                    {data.office?.phone && <span className="whitespace-nowrap"> · {data.office.phone}</span>}
                </div>
                <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
                    {classes.length > 1 && (
                        <CustomSelect variant="ios" className="min-w-[140px] flex-1 sm:w-[190px] sm:flex-none" value={carClass} onChange={setCarClass}
                                      options={[{ value: '', label: 'Все классы' }, ...classes.map((item) => ({ value: item, label: item }))]}
                                      ariaLabel="Класс" />
                    )}
                    <label className="inline-flex h-9 items-center gap-2 rounded-xl bg-white px-3 text-[13px] text-slate-700 ring-1 ring-slate-200/70">
                        Только доступные
                        <IosToggle checked={showOnlyFree} onChange={(value) => setOnlyFree(value)} />
                    </label>
                    {caps.edit_cards && (
                        <button type="button" className={TOOLBAR_BTN} onClick={() => setEditing({ card: null })}>
                            <Plus size={15} /> Карточка
                        </button>
                    )}
                </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-slate-500">
                <span>
                    {sync.synced_at ? `Занятые по Yume — на ${shortTime(sync.synced_at)}` : 'Занятые по Yume ещё не загружены'}
                </span>
                <button type="button" className={ICON_BTN} aria-label="Обновить занятые по Yume" onClick={refreshYume}
                        disabled={sync.refreshing}>
                    <RefreshCw size={14} className={sync.refreshing ? 'animate-spin' : ''} />
                </button>
            </div>
            {sync.error && (
                <Notice tone="amber" title="Yume не ответил">
                    {sync.error}. Свободные машины это не трогает — их ведёт аренда.
                </Notice>
            )}
            {cards.length ? (
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                    {cards.map((card) => (
                        <CarCard key={card.id} card={card} caps={caps} onSend={setSending} onFree={saveFree}
                                 onEdit={(item) => setEditing({ card: item })} onHistory={openHistory} onHide={setHiding} />
                    ))}
                </div>
            ) : (
                <div className={`${iosCard} px-6 py-12 text-center`}>
                    <div className="text-[15px] font-semibold text-slate-800">
                        {showOnlyFree ? 'Свободных машин сейчас нет' : 'Карточек нет'}
                    </div>
                    <div className="mt-1 text-[13px] text-slate-500">
                        {showOnlyFree ? 'Свободные машины указывают сотрудники аренды.' : 'Сбросьте отбор по классу.'}
                    </div>
                </div>
            )}

            {sending && (
                <SendModal card={sending} data={data} api={api} toast={toast} onClose={() => setSending(null)}
                           onSent={() => setSending(null)} />
            )}
            {editing && (
                <CardEditModal card={editing.card} api={api} toast={toast} onClose={() => setEditing(null)}
                               onSaved={() => { setEditing(null); load(); }} />
            )}
            {history && (
                <IosModal open onClose={() => setHistory(null)} title="История правок" subtitle={history.card.title}>
                    <HistoryList items={history.items} shortTime={shortTime} />
                </IosModal>
            )}
            {hiding && (
                <IosModal
                    open
                    onClose={() => setHiding(null)}
                    title="Скрыть карточку?"
                    subtitle={hiding.title}
                    footer={(
                        <div className="flex justify-end gap-2">
                            <button type="button" className={iosBtnSecondary} onClick={() => setHiding(null)}>Отмена</button>
                            <button type="button" className={`${iosBtnPrimary} !bg-rose-600 hover:!bg-rose-700`} onClick={hide}>Скрыть</button>
                        </div>
                    )}
                >
                    <p className="text-[13.5px] text-slate-600">
                        Карточка пропадёт из наличия. Прошлые направления на неё останутся в реестре.
                    </p>
                </IosModal>
            )}
        </div>
    );
};

export default FleetTab;
