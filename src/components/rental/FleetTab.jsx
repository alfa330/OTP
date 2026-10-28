import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Car, History, ImagePlus, Loader2, Minus, MoreHorizontal, Pencil, Plus, RefreshCw, Send, Star, Trash2 } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import IosTimePicker from '../ui/TimePicker';
import { IosLightbox, IosMenu, IosModal, IosToggle, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput } from '../ui/ios';
import { PHOTO_ACCEPT, preparePhoto } from '../parcels/parcelPhoto';
import { CARD_FIELDS, INSURANCE_OPTIONS, addDays, errorOf, formatPrice, freeState, shortTime } from './rentalMeta';
import { HistoryList, ICON_BTN, Loading, Notice, Pill, TOOLBAR_BTN } from './rentalUi';

/*
 * Вкладка «Наличие авто» (#391).
 *
 * Карточка — вариант модели с условиями, с фотографиями машины (владелец 28.10.2026: «карточку с
 * фотографиями самих авто… возможность добавлять фото»). Первый снимок — обложка, остальные
 * листаются точками и открываются во весь экран. Снимки грузят сотрудники аренды; кадр
 * уменьшается в браузере, как у «Посылок» (parcelPhoto.js), и уходит по одному.
 *
 * Тарифы на карточке и в отборе — тарифы Яндекса из «Классификатора авто» в вики (владелец
 * 28.10.2026), их считает сервер (rental/tariffs.py) по модели и году выпуска. На карточке —
 * верхний тариф и «Электро»; отбор с мультивыбором смотрит на все подходящие: оператору, которому
 * нужна машина под «Комфорт+», бизнес-машина тоже годится.
 *
 * Свободные машины вводят сотрудники аренды (ТЗ v5, 3.1): у каждой карточки своё число, путевой
 * лист — отдельная карточка. Из Yume приходят только занятые. Свободных 0 — «Нет в наличии»,
 * не указано ни разу — «Наличие не указано»; в обоих случаях направить нельзя. Направление
 * машину не бронирует (ТЗ 3.2).
 */

const FREE_TONES = { free: 'green', none: 'red', unknown: 'slate' };

/* Поля даты и времени — того же роста и цвета, что iosInput: окно читается одной формой
   (так же сделана форма жалоб). Свой класс триггера заменяет весь стандартный, поэтому
   flex и выравнивание — здесь же. */
const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 text-[14px] tabular-nums '
    + 'text-slate-900 transition hover:bg-slate-200/70 focus:outline-none focus:ring-2 focus:ring-blue-500/70 '
    + '[&>span]:flex-1 [&>span]:text-left';
const TIME_INPUT = 'w-full rounded-xl border-0 bg-slate-100 px-6 py-2.5 text-center text-[14px] tabular-nums text-slate-900 '
    + 'placeholder-slate-400 transition hover:bg-slate-200/70 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70';

// Кнопка поверх фотографии: матовое стекло, как у системных наложений iOS.
const OVERLAY_BTN = 'grid h-8 w-8 place-items-center rounded-full bg-white/85 text-slate-700 shadow-sm backdrop-blur '
    + 'transition hover:bg-white active:scale-95';

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

/* Верх карточки: обложка (или заглушка), метка наличия, меню и точки — по одной на снимок. */
const PhotoHeader = ({ card, state, canEdit, menu, onOpen, onAddPhoto }) => {
    const photos = card.photos || [];
    const [index, setIndex] = useState(0);
    const current = index < photos.length ? index : 0;
    const photo = photos[current];
    return (
        <div className="relative aspect-[16/10] w-full overflow-hidden bg-slate-100">
            {photo ? (
                <button type="button" className="block h-full w-full" aria-label={`Фото: ${card.title}`}
                        onClick={() => onOpen(current)}>
                    <img src={photo.thumb_url || photo.url} alt="" loading="lazy" className="h-full w-full object-cover" />
                </button>
            ) : (
                <div className="grid h-full w-full place-items-center">
                    <div className="flex flex-col items-center gap-2 text-slate-300">
                        <Car size={44} strokeWidth={1.25} />
                        {canEdit ? (
                            <button type="button" onClick={onAddPhoto}
                                    className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-[12.5px] font-medium text-slate-700 shadow-sm transition hover:bg-slate-50">
                                <ImagePlus size={14} /> Добавить фото
                            </button>
                        ) : <span className="text-[12px] text-slate-400">Фото пока нет</span>}
                    </div>
                </div>
            )}
            <div className="pointer-events-none absolute left-3 top-3">
                <Pill tone={FREE_TONES[state.kind]} className="shadow-sm">{state.label}</Pill>
            </div>
            {menu && <div className="absolute right-3 top-3">{menu}</div>}
            {photos.length > 1 && (
                <div className="absolute inset-x-0 bottom-2 flex justify-center gap-1.5">
                    {photos.map((item, i) => (
                        <button key={item.id} type="button" aria-label={`Фото ${i + 1} из ${photos.length}`}
                                onClick={() => setIndex(i)}
                                className={`h-1.5 rounded-full shadow-sm transition-all ${i === current ? 'w-4 bg-white' : 'w-1.5 bg-white/60 hover:bg-white/90'}`} />
                    ))}
                </div>
            )}
        </div>
    );
};

// Разделитель приклеен к пункту: при переносе точка остаётся в конце строки, а не открывает новую.
const Term = ({ children, last }) => (
    <span className="whitespace-nowrap">{children}{!last && <span className="pl-1.5 text-slate-300">·</span>}</span>
);

const CarCard = ({ card, caps, onSend, onFree, onEdit, onHistory, onHide, onPhotos, onOpenPhoto }) => {
    const state = freeState(card);
    const shown = card.tariffs?.shown || [];
    const specs = [card.year, card.fuel, card.color].filter(Boolean);
    const terms = [
        card.insurance && `Страховка ${card.insurance}`,
        card.insurance_class && `класс ${card.insurance_class}`,
        card.first_day && `первый день ${card.first_day}`,
        card.age && `возраст ${card.age}`,
    ].filter(Boolean);
    const menu = caps.edit_cards ? (
        <IosMenu
            label={`Действия: ${card.title}`}
            trigger={{ className: OVERLAY_BTN, text: <MoreHorizontal size={16} /> }}
            items={[
                { key: 'photos', label: 'Фотографии', icon: ImagePlus, onSelect: () => onPhotos(card) },
                { key: 'edit', label: 'Изменить карточку', icon: Pencil, onSelect: () => onEdit(card) },
                { key: 'log', label: 'История правок', icon: History, onSelect: () => onHistory(card) },
                { key: 'hide', label: 'Скрыть карточку', icon: Trash2, danger: true, separatorBefore: true,
                  onSelect: () => onHide(card) },
            ]}
        />
    ) : null;
    return (
        <article className={`${iosCard} flex flex-col overflow-hidden`}>
            <PhotoHeader card={card} state={state} canEdit={Boolean(caps.edit_cards)} menu={menu}
                         onOpen={(index) => onOpenPhoto(card, index)} onAddPhoto={() => onPhotos(card)} />
            <div className="flex grow flex-col gap-3 p-4">
                <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                        <h3 className="truncate text-[16px] font-semibold leading-tight text-slate-900">{card.title}</h3>
                        {specs.length > 0 && <div className="mt-0.5 truncate text-[12.5px] text-slate-500">{specs.join(' · ')}</div>}
                    </div>
                    {card.price ? (
                        <div className="shrink-0 text-right leading-tight">
                            <div className="text-[16px] font-semibold tabular-nums text-slate-900">{formatPrice(card.price).replace(' / сутки', '')}</div>
                            <div className="text-[11.5px] text-slate-400">в сутки</div>
                        </div>
                    ) : null}
                </div>
                {(shown.length > 0 || card.car_class) && (
                    <div className="flex flex-wrap gap-1.5">
                        {shown.length > 0
                            ? shown.map((tariff) => <Pill key={tariff.key} tone="blue">{tariff.name}</Pill>)
                            : <Pill tone="slate">{card.car_class}</Pill>}
                    </div>
                )}
                {terms.length > 0 && (
                    <div className="flex flex-wrap gap-x-1.5 gap-y-0.5 text-[12.5px] leading-snug text-slate-600">
                        {terms.map((term, i) => <Term key={term} last={i === terms.length - 1}>{term}</Term>)}
                    </div>
                )}
                <div className="mt-auto flex flex-col gap-2 border-t border-slate-100 pt-3">
                    {caps.edit_cards && <FreeStepper card={card} onSave={onFree} />}
                    {/* Без свободных кнопка серая и не нажимается; почему — уже сказано меткой на фото. */}
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
    const tariffs = (card.tariffs?.shown || []).map((tariff) => tariff.name).join(', ');
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
                    {[tariffs || card.car_class, formatPrice(card.price)].filter(Boolean).join(' · ')}
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

const FIELD_HINTS = {
    classifier_key: 'Нужно, только если тариф не определился сам: имя модели так, как в «Классификаторе авто».',
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
    const tariffs = card?.tariffs;
    const tariffNote = !card ? null : tariffs?.model
        ? `В классификаторе: ${tariffs.model} → ${(tariffs.shown || []).map((t) => t.name).join(', ') || 'тарифов по году нет'}`
        : 'В «Классификаторе авто» модель не нашлась — впишите её имя ниже.';
    return (
        <IosModal
            open
            onClose={onClose}
            title={card ? 'Изменить карточку' : 'Новая карточка'}
            subtitle={card ? card.title : 'Модель с условиями: цена, страховка, год'}
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
                {tariffNote && (
                    <div className="rounded-xl bg-slate-50 px-3.5 py-2.5 text-[12.5px] leading-snug text-slate-600 sm:col-span-2">
                        {tariffNote}
                    </div>
                )}
                {CARD_FIELDS.map(({ key, label }) => (
                    <label key={key} className={`block space-y-1.5 ${key === 'title' || key === 'office' || key === 'classifier_key' ? 'sm:col-span-2' : ''}`}>
                        <span className="text-[12.5px] font-medium text-slate-600">{label}</span>
                        {key === 'insurance' ? (
                            <CustomSelect variant="ios" value={form.insurance} onChange={(value) => set({ insurance: value })}
                                          options={INSURANCE_OPTIONS} ariaLabel={label} />
                        ) : (
                            <input className={iosInput} value={form[key]} inputMode={key === 'price' ? 'numeric' : undefined}
                                   placeholder={key === 'classifier_key' ? 'Например, Kia Soluto' : undefined}
                                   onChange={(event) => set({ [key]: event.target.value })} />
                        )}
                        {FIELD_HINTS[key] && <span className="block text-[12px] text-slate-400">{FIELD_HINTS[key]}</span>}
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

/* Фотографии карточки: сетка снимков, обложка, удаление и загрузка новых по одному. */
const PhotosModal = ({ card, max, api, toast, onClose, onChanged }) => {
    const [step, setStep] = useState('');
    const [busyId, setBusyId] = useState(null);
    const inputRef = useRef(null);
    const photos = card.photos || [];
    const room = Math.max(0, max - photos.length);

    const upload = async (files) => {
        const list = Array.from(files || []);
        if (!list.length) return;
        if (!room) {
            toast(`Больше ${max} фотографий к одной карточке не добавить`, 'error');
            return;
        }
        if (list.length > room) toast(`Поместится ещё ${room} — остальные не добавлены`, 'error');
        const queue = list.slice(0, room);
        for (let index = 0; index < queue.length; index += 1) {
            setStep(`Загружаю фото ${index + 1} из ${queue.length}`);
            // eslint-disable-next-line no-await-in-loop
            const prepared = await preparePhoto(queue[index]);
            if (!prepared.ok) {
                toast(prepared.issue, 'error');
                continue;
            }
            const form = new FormData();
            // Content-Type руками не ставим: его с boundary проставляет браузер.
            form.append('file', prepared.blob, prepared.name);
            try {
                // eslint-disable-next-line no-await-in-loop
                const { data } = await api.post(`/cards/${card.id}/photos`, form);
                onChanged(data.card);
            } catch (requestError) {
                toast(errorOf(requestError, 'Фото не загрузилось'), 'error');
                break;
            }
        }
        setStep('');
    };

    const act = async (photo, request, fallback) => {
        setBusyId(photo.id);
        try {
            const { data } = await request();
            onChanged(data.card);
        } catch (requestError) {
            toast(errorOf(requestError, fallback), 'error');
        } finally {
            setBusyId(null);
        }
    };

    return (
        <IosModal
            open
            onClose={onClose}
            title="Фотографии"
            subtitle={`${card.title} · первая — обложка карточки`}
            maxWidth="max-w-2xl"
            footer={(
                <div className="flex items-center justify-between gap-3">
                    <span className="text-[12.5px] text-slate-500">{step || `${photos.length} из ${max}`}</span>
                    <div className="flex gap-2">
                        <button type="button" className={iosBtnSecondary} onClick={onClose}>Готово</button>
                        <button type="button" className={iosBtnPrimary} disabled={Boolean(step) || !room}
                                onClick={() => inputRef.current?.click()}>
                            {step ? <Loader2 size={15} className="animate-spin" /> : <ImagePlus size={15} />} Добавить фото
                        </button>
                    </div>
                </div>
            )}
        >
            <input ref={inputRef} type="file" accept={PHOTO_ACCEPT} multiple className="hidden" aria-label="Выбрать фотографии"
                   onChange={(event) => { upload(event.target.files); event.target.value = ''; }} />
            {photos.length ? (
                <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                    {photos.map((photo, index) => (
                        <li key={photo.id} className="group relative aspect-[4/3] overflow-hidden rounded-2xl bg-slate-100">
                            <img src={photo.thumb_url || photo.url} alt="" className="h-full w-full object-cover" />
                            {index === 0 && (
                                <span className="absolute left-2 top-2 rounded-full bg-white/90 px-2 py-0.5 text-[11.5px] font-medium text-slate-700 shadow-sm">
                                    Обложка
                                </span>
                            )}
                            <div className="absolute right-2 top-2 flex gap-1.5">
                                {index > 0 && (
                                    <button type="button" className={OVERLAY_BTN} aria-label="Сделать обложкой" disabled={busyId === photo.id}
                                            onClick={() => act(photo, () => api.put(`/cards/${card.id}/photos/${photo.id}/cover`), 'Не получилось')}>
                                        <Star size={15} />
                                    </button>
                                )}
                                <button type="button" className={`${OVERLAY_BTN} text-rose-600`} aria-label="Удалить фото" disabled={busyId === photo.id}
                                        onClick={() => act(photo, () => api.delete(`/cards/${card.id}/photos/${photo.id}`), 'Фото не удалилось')}>
                                    {busyId === photo.id ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
                                </button>
                            </div>
                        </li>
                    ))}
                </ul>
            ) : (
                <div className="grid place-items-center rounded-2xl bg-slate-50 py-12 text-center">
                    <Car size={40} strokeWidth={1.25} className="text-slate-300" />
                    <div className="mt-2 text-[13.5px] text-slate-600">Снимков пока нет</div>
                    <div className="text-[12.5px] text-slate-400">JPEG, PNG или WebP — до {max} на карточку</div>
                </div>
            )}
        </IosModal>
    );
};

const FleetTab = ({ api, toast }) => {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [tariffKeys, setTariffKeys] = useState([]);
    const [onlyFree, setOnlyFree] = useState(null);
    const [sending, setSending] = useState(null);
    const [editing, setEditing] = useState(null);
    const [history, setHistory] = useState(null);
    const [hiding, setHiding] = useState(null);
    const [photosOf, setPhotosOf] = useState(null);
    const [viewer, setViewer] = useState(null);

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
    const tariffOptions = useMemo(() => (data?.tariffs || []).map((tariff) => ({ value: tariff.key, label: tariff.name })), [data]);
    const cards = useMemo(() => (data?.cards || []).filter((card) => (
        (!tariffKeys.length || (card.tariffs?.fits || []).some((tariff) => tariffKeys.includes(tariff.key)))
        && (!showOnlyFree || freeState(card).kind === 'free')
    )), [data, tariffKeys, showOnlyFree]);

    // Ответ правки — карточка целиком (тарифы, фото); занятые по Yume в нём нет, берём прежние.
    const replaceCard = useCallback((card) => {
        if (!card) return;
        setData((prev) => ({ ...prev, cards: prev.cards.map((item) => (item.id === card.id ? { ...item, ...card, busy: item.busy } : item)) }));
    }, []);

    const saveFree = async (card, value) => {
        try {
            const { data: result } = await api.put(`/cards/${card.id}/free`, { free_count: value });
            replaceCard(result.card);
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
    const photosCard = photosOf ? data.cards.find((card) => card.id === photosOf) : null;
    const viewerPhotos = viewer ? (data.cards.find((card) => card.id === viewer.cardId)?.photos || []) : [];
    const viewerPhoto = viewer ? viewerPhotos[viewer.index] : null;
    const tariffNames = (values) => tariffOptions.filter((option) => values.includes(option.value)).map((option) => option.label).join(', ');
    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0 text-[13px] leading-snug text-slate-600">
                    <span className="font-medium text-slate-800">Офис выдачи:</span> {data.office?.name}
                    {data.office?.phone && <span className="whitespace-nowrap"> · {data.office.phone}</span>}
                </div>
                <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
                    {tariffOptions.length > 0 && (
                        <CustomSelect variant="ios" multiple bulkActions className="min-w-[160px] flex-1 sm:w-[220px] sm:flex-none"
                                      value={tariffKeys} onChange={(values) => setTariffKeys(values || [])}
                                      options={tariffOptions} placeholder="Все тарифы" renderValue={tariffNames}
                                      ariaLabel="Тарифы Яндекса" />
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
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                    {cards.map((card) => (
                        <CarCard key={card.id} card={card} caps={caps} onSend={setSending} onFree={saveFree}
                                 onEdit={(item) => setEditing({ card: item })} onHistory={openHistory} onHide={setHiding}
                                 onPhotos={(item) => setPhotosOf(item.id)}
                                 onOpenPhoto={(item, index) => setViewer({ cardId: item.id, index })} />
                    ))}
                </div>
            ) : (
                <div className={`${iosCard} px-6 py-12 text-center`}>
                    <div className="text-[15px] font-semibold text-slate-800">
                        {showOnlyFree ? 'Свободных машин по этому отбору нет' : 'Карточек по этому отбору нет'}
                    </div>
                    <div className="mt-1 text-[13px] text-slate-500">
                        {tariffKeys.length ? 'Снимите отбор по тарифам.' : 'Свободные машины указывают сотрудники аренды.'}
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
            {photosCard && (
                <PhotosModal card={photosCard} max={data.photos_max || 6} api={api} toast={toast}
                             onClose={() => setPhotosOf(null)} onChanged={replaceCard} />
            )}
            {viewerPhoto && (
                <IosLightbox
                    url={viewerPhoto.url}
                    alt="Фотография машины"
                    counter={viewerPhotos.length > 1 ? `${viewer.index + 1} / ${viewerPhotos.length}` : ''}
                    onClose={() => setViewer(null)}
                    onPrev={viewer.index > 0 ? () => setViewer({ ...viewer, index: viewer.index - 1 }) : undefined}
                    onNext={viewer.index < viewerPhotos.length - 1 ? () => setViewer({ ...viewer, index: viewer.index + 1 }) : undefined}
                />
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
