import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, PackagePlus } from 'lucide-react';
import { IosModal, IosToggle, iosBtnPrimary, iosBtnSecondary } from '../ui/ios';
import { PHOTO_EXTRA, PHOTO_SLOTS, errorFields, errorText, intakeIssues, shortName, todayIso } from './fixedAssetsMeta';
import {
    ErrorNote, FIELD, FIELD_INVALID, Field, FormDate, FormSelect, GroupTitle, PersonSelect, PhotoSlot,
} from './fixedAssetsUi';
import { BarcodeField } from './FaScanner';

/*
 * «Принять в учёт» — единственный вход ОС в систему (ТЗ, процесс 1): бухгалтерия заводит
 * карточку, присваивает инвентарный номер и сразу закрепляет ОС за МОЛ. ОС становится «На
 * учёте» в момент сохранения; рядом заводится запрос приёма — он ведёт акт приёма до
 * оригинала. Фото — те же два обязательных снимка, что у любого запроса.
 *
 * source — запрос «Новое ОС» от IT: форма открыта по нему, и приём этот запрос закроет.
 */
const MAX_EXTRA = 4;

export default function FaIntakeForm({ open, source = null, api, context, people, toast, onClose, onSaved }) {
    const need = source?.request || null;
    const [form, setForm] = useState(() => ({
        name: need?.need_title || '', category_id: need?.need_category_id || null, inventory_number: '',
        serial_number: '', barcode: '', cost: '', acquired_on: todayIso(), note: '',
        to_user_id: need?.need_for_user_id || null, to_location: '', to_stock: false,
    }));
    const [photos, setPhotos] = useState({ extra: [] });
    const [issues, setIssues] = useState({});
    const [tried, setTried] = useState(false);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const bottomRef = useRef(null);
    /* Поле заполнили — его красная рамка и подпись «чего не хватает» уходят сразу. */
    const clearIssues = (keys) => setIssues((prev) => {
        if (!keys.some((key) => prev[key])) return prev;
        const next = { ...prev };
        keys.forEach((key) => { delete next[key]; });
        return next;
    });
    const set = (patch) => { setForm((prev) => ({ ...prev, ...patch })); clearIssues(Object.keys(patch)); };

    /* Номер подставляется следующим после последнего заведённого. Бухгалтер вправе вписать свой. */
    useEffect(() => {
        let alive = true;
        api.get('/assets/next-number').then(({ data }) => {
            if (!alive) return;
            if (data.suggestion) setForm((prev) => (prev.inventory_number ? prev : { ...prev, inventory_number: data.suggestion }));
        }).catch(() => {});
        return () => { alive = false; };
    }, [api]);

    const categoryOptions = useMemo(() => (context.categories || []).map((item) => ({ value: item.id, label: item.name })), [context.categories]);

    const submit = async () => {
        const missing = intakeIssues(form, photos);
        setTried(true);
        setIssues(missing);
        setError('');
        if (Object.keys(missing).length) {
            requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
            return;
        }
        const files = [];
        const meta = [];
        PHOTO_SLOTS.forEach((slot) => { files.push(photos[slot.kind].file); meta.push({ kind: slot.kind }); });
        (photos.extra || []).forEach((photo) => { files.push(photo.file); meta.push({ kind: PHOTO_EXTRA }); });
        const body = {
            name: form.name.trim(), category_id: form.category_id, inventory_number: form.inventory_number.trim(),
            serial_number: form.serial_number.trim(), barcode: form.barcode.trim() || null,
            cost: form.cost === '' ? null : form.cost,
            acquired_on: form.acquired_on || null, note: form.note.trim(),
            to_user_id: form.to_user_id, to_location: form.to_location.trim(), to_stock: form.to_stock,
            photos: meta, source_request_id: need?.id || null,
        };
        setBusy(true);
        try {
            const response = await api.post('/assets', body, files);
            toast('ОС принято в учёт', 'success');
            onSaved(response.data.asset);
        } catch (requestError) {
            setIssues(errorFields(requestError));
            setError(errorText(requestError, 'ОС не принято'));
            requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
        } finally {
            setBusy(false);
        }
    };

    const extras = photos.extra || [];
    return (
        <IosModal
            open={open}
            onClose={onClose}
            title="Принять в учёт"
            subtitle={need ? `По запросу № ${need.id} от ${shortName(need.created_by_name)}` : 'Новое основное средство'}
            maxWidth="max-w-2xl"
            footer={(
                <>
                    <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={busy}>Отмена</button>
                    <button type="button" className={iosBtnPrimary} onClick={submit} disabled={busy}>
                        {busy ? <Loader2 size={14} className="animate-spin" /> : <PackagePlus size={14} />} Принять в учёт
                    </button>
                </>
            )}
        >
            <div className="space-y-5">
                <section className="space-y-3">
                    <Field label="Наименование" required error={issues.name}>
                        <input className={`${FIELD} ${issues.name ? FIELD_INVALID : ''}`} value={form.name} maxLength={300}
                               placeholder="Например: Ноутбук Lenovo ThinkPad E14" onChange={(event) => set({ name: event.target.value })} />
                    </Field>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Категория" required error={issues.category_id}>
                            <FormSelect value={form.category_id || ''} onChange={(value) => set({ category_id: value || null })} options={categoryOptions}
                                        placeholder="Выберите категорию" invalid={Boolean(issues.category_id)} ariaLabel="Категория" />
                        </Field>
                        <Field label="Инвентарный номер" required error={issues.inventory_number}>
                            <input className={`${FIELD} tabular-nums ${issues.inventory_number ? FIELD_INVALID : ''}`} value={form.inventory_number} maxLength={64}
                                   onChange={(event) => set({ inventory_number: event.target.value })} />
                        </Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Серийный номер">
                            <input className={FIELD} value={form.serial_number} maxLength={120} onChange={(event) => set({ serial_number: event.target.value })} />
                        </Field>
                        {/* Наклейка со штрихкодом: по ней ОС потом находят кнопкой «Сканировать». */}
                        <Field label="Штрихкод наклейки" error={issues.barcode}>
                            <BarcodeField value={form.barcode} onChange={(value) => set({ barcode: value })} invalid={Boolean(issues.barcode)} />
                        </Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Стоимость, ₸">
                            <input className={`${FIELD} tabular-nums`} inputMode="decimal" value={form.cost} placeholder="0"
                                   onChange={(event) => set({ cost: event.target.value.replace(/[^\d.,\s]/g, '') })} />
                        </Field>
                        <Field label="Принято к учёту">
                            <FormDate value={form.acquired_on} onChange={(value) => set({ acquired_on: value || todayIso() })} max={todayIso()} ariaLabel="Дата принятия к учёту" />
                        </Field>
                    </div>
                </section>

                <section className="space-y-3">
                    <Field label="За кем числится (МОЛ)" required error={issues.to_user_id}>
                        <PersonSelect people={people || []} value={form.to_user_id} invalid={Boolean(issues.to_user_id)}
                                      onChange={(value) => set({ to_user_id: value })} ariaLabel="МОЛ" />
                    </Field>
                    <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
                        <Field label="Место">
                            <input className={FIELD} value={form.to_location} maxLength={300} placeholder="Город, офис, кабинет"
                                   onChange={(event) => set({ to_location: event.target.value })} />
                        </Field>
                        <label className="flex h-[41px] items-center gap-2.5 rounded-xl bg-slate-100 px-3.5 text-[14px] text-slate-700">
                            <IosToggle checked={form.to_stock} onChange={(value) => set({ to_stock: value })} />
                            На склад
                        </label>
                    </div>
                </section>

                <section>
                    <GroupTitle>Фото</GroupTitle>
                    <div className="flex flex-wrap gap-2">
                        {PHOTO_SLOTS.map((slot) => (
                            <PhotoSlot key={slot.kind} label={slot.label} value={photos[slot.kind] || null}
                                       invalid={tried && !photos[slot.kind]}
                                       onPick={(value) => { setPhotos((prev) => ({ ...prev, [slot.kind]: value })); clearIssues(['photos']); }}
                                       onClear={() => setPhotos((prev) => ({ ...prev, [slot.kind]: null }))} />
                        ))}
                        {extras.map((photo, index) => (
                            // eslint-disable-next-line react/no-array-index-key
                            <PhotoSlot key={`extra-${index}`} label="Ещё фото" optional value={photo}
                                       onPick={(value) => setPhotos((prev) => ({ ...prev, extra: prev.extra.map((item, at) => (at === index ? value : item)) }))}
                                       onClear={() => setPhotos((prev) => ({ ...prev, extra: prev.extra.filter((_, at) => at !== index) }))} />
                        ))}
                        {extras.length < MAX_EXTRA && (
                            <PhotoSlot label="Ещё фото" optional value={null}
                                       onPick={(value) => setPhotos((prev) => ({ ...prev, extra: [...(prev.extra || []), value] }))} />
                        )}
                    </div>
                </section>

                <Field label="Примечание">
                    <input className={FIELD} value={form.note} maxLength={2000} placeholder="Комплектация, состояние"
                           onChange={(event) => set({ note: event.target.value })} />
                </Field>

                <div ref={bottomRef} className="space-y-2">
                    <ErrorNote text={issues.photos ? `${issues.photos}. К ОС нужны два снимка: ОС целиком и инвентарный номер.` : ''} />
                    <ErrorNote text={error} />
                </div>
            </div>
        </IosModal>
    );
}
