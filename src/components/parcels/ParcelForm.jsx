import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Check, Loader2, Plus, Search, X } from 'lucide-react';
import {
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput, IosModal, IosSegmented, IosSection,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import {
    KIND_META, PARCEL_KINDS, extractAccountId, fmtPhone, officeChoiceFor, todayISO,
} from './parcelMeta';

/*
 * Форма посылки — заведение и правка.
 *
 * На виду только обязательное: дата, место, водитель, тип и описание. Всё
 * остальное из ТЗ («Отправитель», «Получатель», «Номер заказа», «Комментарий»)
 * помечено как необязательное и открывается чипами — так же, как в
 * TaskComposerForm. Форма из десяти полей, где половина обычно пустая, читается
 * как анкета, а посылку заводят между двумя водителями у стойки.
 *
 * Офис спрашивается ТОЛЬКО там, где офисов в городе несколько (просьба
 * владельца). Где офис один — на его месте стоит строка с адресом, а не пустой
 * селектор с единственным пунктом: выбирать не из чего, а показать, куда
 * записали, надо.
 */

// Необязательные поля. Порядок — как в ТЗ.
const EXTRA_FIELDS = [
    { key: 'sender', label: 'Отправитель', placeholder: 'Если известен' },
    { key: 'recipient', label: 'Получатель', placeholder: 'Если известен' },
    { key: 'order_number', label: 'Номер заказа', placeholder: 'Если известен' },
    { key: 'comment', label: 'Комментарий', placeholder: 'Дополнительная информация', multiline: true },
];

const emptyDraft = (defaultCity = '') => ({
    received_on: todayISO(),
    city: defaultCity || '',
    office_id: null,
    driver_link: '',
    driver_name: '',
    kind: 'parcel',
    description: '',
    sender: '',
    recipient: '',
    order_number: '',
    comment: '',
});

const draftFromParcel = (parcel) => ({
    received_on: String(parcel.received_on || '').slice(0, 10),
    city: parcel.city || '',
    office_id: parcel.office_id ?? null,
    driver_link: parcel.driver_account_id || '',
    driver_name: parcel.driver_name || '',
    kind: parcel.kind || 'parcel',
    description: parcel.description || '',
    sender: parcel.sender || '',
    recipient: parcel.recipient || '',
    order_number: parcel.order_number || '',
    comment: parcel.comment || '',
});

const Field = ({ label, hint, children, required = false }) => (
    <label className="block space-y-1.5">
        <span className="flex items-baseline gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            {label}
            {!required && <span className="font-normal normal-case tracking-normal text-slate-400">необязательно</span>}
        </span>
        {children}
        {hint && <span className="block px-1 text-[11.5px] leading-relaxed text-slate-500">{hint}</span>}
    </label>
);

const ParcelForm = ({
    open, onClose, apiBaseUrl, headers, offices, defaultCity = '', parcel = null, onSaved, showToast,
}) => {
    const editing = Boolean(parcel);
    const [draft, setDraft] = useState(() => emptyDraft(defaultCity));
    const [extras, setExtras] = useState([]);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    // Найденный водитель. `null` — ещё не искали; объект — CRM ответила.
    const [driver, setDriver] = useState(null);
    const [driverLoading, setDriverLoading] = useState(false);
    const [driverError, setDriverError] = useState('');
    const [nameOverride, setNameOverride] = useState(false);

    useEffect(() => {
        if (!open) return;
        if (parcel) {
            setDraft(draftFromParcel(parcel));
            // У сохранённой карточки поле открыто, только если в нём что-то есть:
            // пустые «Отправитель» и «Получатель» в правке не нужны так же, как
            // при заведении.
            setExtras(EXTRA_FIELDS.filter((field) => parcel[field.key]).map((field) => field.key));
            setDriver(parcel.driver_name ? {
                name: parcel.driver_name,
                phone: parcel.driver_phone,
                park: parcel.driver_park,
                car: parcel.driver_car,
                callsign: parcel.driver_callsign,
            } : null);
            setNameOverride(false);
        } else {
            setDraft(emptyDraft(defaultCity));
            setExtras([]);
            setDriver(null);
            setNameOverride(false);
        }
        setError('');
        setDriverError('');
    }, [open, parcel, defaultCity]);

    const set = useCallback((patch) => setDraft((prev) => ({ ...prev, ...patch })), []);

    const cityOptions = useMemo(() => {
        const seen = new Map();
        (offices || []).forEach((office) => {
            if (!seen.has(office.city)) seen.set(office.city, 0);
            seen.set(office.city, seen.get(office.city) + 1);
        });
        return [...seen.keys()].sort((a, b) => a.localeCompare(b, 'ru'))
            .map((city) => ({ value: city, label: city }));
    }, [offices]);

    const choice = useMemo(() => officeChoiceFor(offices, draft.city), [offices, draft.city]);

    // Единственный офис города подставляем сами — и в состояние тоже, чтобы
    // сохранение не зависело от того, посмотрел человек на подпись или нет.
    useEffect(() => {
        if (choice.autoOfficeId && draft.office_id !== choice.autoOfficeId) {
            set({ office_id: choice.autoOfficeId });
        }
        if (choice.asks && draft.office_id
            && !choice.options.some((office) => office.id === draft.office_id)) {
            // Сменили город — офис прежнего города здесь не к месту.
            set({ office_id: null });
        }
    }, [choice, draft.office_id, set]);

    const pickedOffice = useMemo(
        () => (choice.options || []).find((office) => office.id === draft.office_id) || null,
        [choice.options, draft.office_id],
    );

    const accountId = useMemo(() => extractAccountId(draft.driver_link), [draft.driver_link]);

    const lookupDriver = useCallback(async () => {
        if (!accountId) {
            setDriverError('Вставьте ссылку на аккаунт водителя или его ID');
            return;
        }
        setDriverLoading(true);
        setDriverError('');
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/parcels/driver-lookup`,
                { link: draft.driver_link },
                { headers: headers() },
            );
            const found = response.data?.driver || null;
            setDriver(found);
            if (found?.name && !nameOverride) set({ driver_name: found.name });
        } catch (requestError) {
            setDriver(null);
            setDriverError(requestError?.response?.data?.error
                || 'Не удалось получить данные водителя');
        } finally {
            setDriverLoading(false);
        }
    }, [accountId, apiBaseUrl, draft.driver_link, headers, nameOverride, set]);

    // Ищем сами, как только строка стала разбираемой: отдельное нажатие на
    // «Найти» после вставки ссылки — лишний шаг, а ссылку вставляют целиком, а
    // не набирают по символу.
    useEffect(() => {
        if (!open || !accountId) return undefined;
        if (driver && accountId === parcel?.driver_account_id) return undefined;
        const timer = setTimeout(() => { lookupDriver(); }, 250);
        return () => clearTimeout(timer);
        // lookupDriver намеренно вне зависимостей: он меняется на каждый ввод,
        // и таймер сбрасывался бы, ни разу не выстрелив.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, accountId]);

    const canSave = Boolean(
        draft.received_on && draft.city && (draft.office_id || !choice.asks)
        && accountId && draft.kind && draft.description.trim(),
    );

    const save = useCallback(async () => {
        if (!canSave || saving) return;
        setSaving(true);
        setError('');
        const body = {
            received_on: draft.received_on,
            city: draft.city,
            office_id: draft.office_id,
            driver_link: draft.driver_link,
            // Отправляем ФИО, только если человек его правил: иначе снимок из
            // CRM затирался бы тем, что мы же от него и получили.
            ...(nameOverride && draft.driver_name.trim()
                ? { driver_name: draft.driver_name.trim() } : {}),
            kind: draft.kind,
            description: draft.description.trim(),
            sender: draft.sender.trim(),
            recipient: draft.recipient.trim(),
            order_number: draft.order_number.trim(),
            comment: draft.comment.trim(),
        };
        try {
            const response = editing
                ? await axios.patch(`${apiBaseUrl}/api/parcels/${parcel.id}`, body, { headers: headers() })
                : await axios.post(`${apiBaseUrl}/api/parcels`, body, { headers: headers() });
            showToast?.(editing ? 'Карточка обновлена' : 'Посылка добавлена в реестр', 'success');
            onSaved?.(response.data?.item || null);
            onClose?.();
        } catch (requestError) {
            setError(requestError?.response?.data?.error || 'Не удалось сохранить посылку');
        } finally {
            setSaving(false);
        }
    }, [apiBaseUrl, canSave, draft, editing, headers, nameOverride, onClose, onSaved, parcel, saving, showToast]);

    const restExtras = EXTRA_FIELDS.filter((field) => !extras.includes(field.key));

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={editing ? 'Карточка посылки' : 'Добавить посылку'}
            subtitle={editing ? `№${parcel.id}` : 'Запись появится в общем реестре'}
            maxWidth="max-w-xl"
            footer={(
                <>
                    {error && <span className="mr-auto text-[12.5px] text-red-600">{error}</span>}
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                    <button type="button" className={iosBtnPrimary} disabled={!canSave || saving} onClick={save}>
                        {saving ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                        Сохранить
                    </button>
                </>
            )}
        >
            <div className="space-y-5">
                <IosSection title="Где и когда">
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Дата приёма" required>
                            <input
                                type="date"
                                className={iosInput}
                                max={todayISO()}
                                value={draft.received_on}
                                onChange={(event) => set({ received_on: event.target.value })}
                            />
                        </Field>
                        <Field label="Город" required>
                            <CustomSelect
                                value={draft.city}
                                onChange={(value) => set({ city: value, office_id: null })}
                                options={cityOptions}
                                placeholder="Выберите город"
                                variant="ios"
                                searchable
                                ariaLabel="Город офиса"
                            />
                        </Field>
                    </div>

                    {choice.asks ? (
                        <Field label="Офис" required>
                            <CustomSelect
                                value={draft.office_id}
                                onChange={(value) => set({ office_id: value })}
                                options={choice.options.map((office) => ({
                                    value: office.id,
                                    label: office.address ? `${office.name} · ${office.address}` : office.name,
                                }))}
                                placeholder="Выберите офис"
                                variant="ios"
                                ariaLabel="Офис"
                            />
                        </Field>
                    ) : (
                        /* Офис один — выбирать не из чего, но человек должен видеть,
                           куда записали. Строка вместо селектора с одним пунктом. */
                        draft.city && (
                            <div className="rounded-xl bg-slate-100 px-3.5 py-2.5 text-[13px] text-slate-600">
                                {pickedOffice
                                    ? <>Офис: <span className="text-slate-900">{pickedOffice.name}</span>
                                        {pickedOffice.address && <span className="text-slate-500">, {pickedOffice.address}</span>}</>
                                    : 'В этом городе нет офисов в справочнике «Вики»'}
                            </div>
                        )
                    )}
                </IosSection>

                <IosSection title="Водитель">
                    <Field
                        label="Ссылка на аккаунт или ID"
                        required
                        hint="Вставьте адрес карточки водителя во Флите — ФИО и телефон подтянутся сами"
                    >
                        <div className="flex gap-2">
                            <input
                                type="text"
                                className={iosInput}
                                placeholder="https://fleet.yandex.kz/contractors/…"
                                value={draft.driver_link}
                                onChange={(event) => set({ driver_link: event.target.value })}
                            />
                            <button
                                type="button"
                                className={iosBtnSecondary}
                                onClick={lookupDriver}
                                disabled={!accountId || driverLoading}
                                aria-label="Найти водителя"
                            >
                                {driverLoading ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
                            </button>
                        </div>
                    </Field>

                    {driverError && (
                        <p className="px-1 text-[12.5px] text-red-600">{driverError}</p>
                    )}

                    {driver && (
                        <div className="rounded-xl bg-slate-100 px-3.5 py-3 text-[13px]">
                            {nameOverride ? (
                                <input
                                    type="text"
                                    className={`${iosInput} bg-white`}
                                    placeholder="ФИО водителя"
                                    value={draft.driver_name}
                                    onChange={(event) => set({ driver_name: event.target.value })}
                                />
                            ) : (
                                <div className="font-medium text-slate-900">{draft.driver_name || driver.name || '—'}</div>
                            )}
                            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[12.5px] text-slate-500">
                                {driver.phone && <span className="tabular-nums">{fmtPhone(driver.phone)}</span>}
                                {driver.park && <span>{driver.park}</span>}
                                {driver.car && <span>{driver.car}</span>}
                            </div>
                            <button
                                type="button"
                                className={`${iosBtnGhost} mt-1.5 px-1`}
                                onClick={() => setNameOverride((prev) => !prev)}
                            >
                                {nameOverride ? 'Вернуть ФИО из CRM' : 'Исправить ФИО'}
                            </button>
                        </div>
                    )}
                </IosSection>

                <IosSection title="Что оставили">
                    <Field label="Тип посылки" required>
                        <IosSegmented
                            value={draft.kind}
                            onChange={(value) => set({ kind: value })}
                            options={PARCEL_KINDS.map((code) => ({ value: code, label: KIND_META[code].label }))}
                            stretch
                            ariaLabel="Тип посылки"
                        />
                    </Field>
                    <Field label="Описание" required>
                        <textarea
                            rows={2}
                            className={`${iosInput} resize-none`}
                            placeholder="Коробка с одеждой, документы…"
                            value={draft.description}
                            onChange={(event) => set({ description: event.target.value })}
                        />
                    </Field>

                    {EXTRA_FIELDS.filter((field) => extras.includes(field.key)).map((field) => (
                        <Field key={field.key} label={field.label}>
                            <div className="flex gap-2">
                                {field.multiline ? (
                                    <textarea
                                        rows={2}
                                        className={`${iosInput} resize-none`}
                                        placeholder={field.placeholder}
                                        value={draft[field.key]}
                                        onChange={(event) => set({ [field.key]: event.target.value })}
                                    />
                                ) : (
                                    <input
                                        type="text"
                                        className={iosInput}
                                        placeholder={field.placeholder}
                                        value={draft[field.key]}
                                        onChange={(event) => set({ [field.key]: event.target.value })}
                                    />
                                )}
                                <button
                                    type="button"
                                    className={iosBtnSecondary}
                                    aria-label={`Убрать «${field.label}»`}
                                    onClick={() => {
                                        setExtras((prev) => prev.filter((key) => key !== field.key));
                                        set({ [field.key]: '' });
                                    }}
                                >
                                    <X size={15} />
                                </button>
                            </div>
                        </Field>
                    ))}

                    {restExtras.length > 0 && (
                        <div className="flex flex-wrap gap-2 pt-0.5">
                            {restExtras.map((field) => (
                                <button
                                    key={field.key}
                                    type="button"
                                    className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-3 py-1.5 text-[12.5px] font-medium text-slate-600 transition hover:bg-slate-200 active:scale-[0.98]"
                                    onClick={() => setExtras((prev) => [...prev, field.key])}
                                >
                                    <Plus size={13} />
                                    {field.label}
                                </button>
                            ))}
                        </div>
                    )}
                </IosSection>
            </div>
        </IosModal>
    );
};

export default ParcelForm;
