import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Check, Loader2, Plus, Search, X } from 'lucide-react';
import {
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput, IosModal, IosSegmented, IosSection,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import {
    KIND_META, PARCEL_KINDS, extractAccountId, fmtPhone, officeChoiceFor, todayISO,
} from './parcelMeta';
import { PHOTO_MAX_COUNT, countIssue, preparePhoto, sortPhotos } from './parcelPhoto';
import { PhotoPicker } from './ParcelPhotos';

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
/* «Номер заказа» заменён ссылкой на заказ (решение владельца 25.08.2026).
   Свободный номер спрашивали, пока сослаться на сам заказ было нечем; API по
   водителю отдаёт последние три заказа, но без адресов — одни id, цены и
   пробеги, — и владелец счёл их лишними данными в карточке. Колонка
   `order_number` в базе осталась: заполнять её больше нечем, но записанное
   раньше карточка по-прежнему покажет. */
const EXTRA_FIELDS = [
    { key: 'sender', label: 'Отправитель', placeholder: 'Если известен' },
    { key: 'recipient', label: 'Получатель', placeholder: 'Если известен' },
    { key: 'order_url', label: 'Ссылка на заказ', placeholder: 'https://fleet.yandex.kz/orders/…',
      hint: 'Вставьте адрес карточки заказа — по нему посылка потом найдётся' },
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
    order_url: '',
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
    order_url: parcel.order_url || '',
    comment: parcel.comment || '',
});

/* Вид кнопки календаря — как у остального поля формы, а не чипом: в одном ряду
   с селектором города чип читался бы как кнопка, а не как поле. Класс взят
   дословно из OfficeDayModal (вики): второй способ одеть тот же примитив
   разошёлся бы с первым на первой же правке отступа. `[&>span]:flex-1` нужен
   потому, что triggerClassName ЗАМЕНЯЕТ класс кнопки целиком — без него дата
   не растягивается и шеврон уезжает к тексту. */
const dateTrigger = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 '
    + 'text-[14px] tabular-nums text-slate-900 border-0 transition hover:bg-slate-200/70 '
    + 'focus:outline-none focus:ring-2 focus:ring-blue-500/70 [&>span]:flex-1 [&>span]:text-left';

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
    photos = [], photosEnabled = false,
}) => {
    const editing = Boolean(parcel);
    const [draft, setDraft] = useState(() => emptyDraft(defaultCity));
    const [extras, setExtras] = useState([]);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    /* Фотографии применяются на «Сохранить» — и добавленные, и снятые.
     *
     * Так, а не «грузим сразу при выборе», по двум причинам. Первая: пока
     * карточки нет, привязывать снимок не к чему, а «ничьи» файлы — это сироты
     * в бакете, вопрос «кому их видно» и уборщик, которого в проекте нет ни у
     * одного раздела (в вике за двухфазную загрузку заплачено ровно этим).
     * Вторая: рядом стоит кнопка «Отмена». Если снятие применялось бы сразу,
     * на одном экране «Отмена» откатывала бы текст и НЕ откатывала бы
     * фотографию — а она удаляется вместе с файлом, то есть насовсем. Промах
     * по крестику на телефоне — обычное дело.
     *
     * Цена названа честно: сохранение с тремя снимками занимает секунды, и всё
     * это время на кнопке стоит «Загружаю фото 2 из 3».
     */
    const [queue, setQueue] = useState([]);       // новые снимки: {key, blob, name, previewUrl}
    const [removals, setRemovals] = useState([]); // id уже сохранённых, помеченных к снятию
    const [savedId, setSavedId] = useState(null); // карточка уже создана — второй раз не заводим
    const [photoStep, setPhotoStep] = useState('');
    const [preparing, setPreparing] = useState(false);

    /* Выданные createObjectURL — отзывать их в эффекте сброса нельзя: он
       начинается с `if (!open) return`, то есть при закрытии формы не
       выполняется вовсе, а к следующему открытию в замыкании лежит прошлая
       очередь. Поэтому список живёт в ref и чистится в cleanup. */
    const previewUrls = useRef([]);

    const releasePreviews = useCallback((urls) => {
        (urls || []).forEach((url) => {
            try { URL.revokeObjectURL(url); } catch { /* браузер уже всё отдал */ }
        });
    }, []);

    useEffect(() => () => {
        releasePreviews(previewUrls.current);
        previewUrls.current = [];
    }, [releasePreviews]);

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
        // Сброс фотографий — в ОБЕИХ ветках: компонент смонтирован всегда, его
        // прячет только `if (!open) return null` внутри модалки. Без сброса
        // очередь из прошлой формы всплыла бы в следующей открытой.
        releasePreviews(previewUrls.current);
        previewUrls.current = [];
        setQueue([]);
        setRemovals([]);
        setSavedId(null);
        setPhotoStep('');
    }, [open, parcel, defaultCity, releasePreviews]);

    const set = useCallback((patch) => setDraft((prev) => ({ ...prev, ...patch })), []);

    // Уже сохранённые снимки за вычетом помеченных к снятию — то, что человек
    // видит на экране прямо сейчас.
    const kept = useMemo(
        () => sortPhotos(photos).filter((photo) => !removals.includes(photo.id)),
        [photos, removals],
    );

    const addFiles = useCallback(async (files) => {
        const incoming = [...(files || [])];
        if (!incoming.length) return;

        // Счёт места — ОДИН раз на всю пачку и ДО цикла. Проверка внутри цикла
        // по длине очереди не сработала бы: счётчик из замыкания рендера не
        // растёт по ходу пачки, и двадцать брошенных файлов прошли бы все
        // двадцать.
        const already = kept.length + queue.length;
        const limit = countIssue(already, incoming.length);
        if (limit) showToast?.(limit, 'error');
        const free = Math.max(0, PHOTO_MAX_COUNT - already);
        if (!free) return;

        setPreparing(true);
        try {
            const prepared = [];
            for (const file of incoming.slice(0, free)) {
                // eslint-disable-next-line no-await-in-loop
                const ready = await preparePhoto(file);
                if (!ready.ok) {
                    showToast?.(`${file.name || 'Файл'}: ${ready.issue}`, 'error');
                    continue;
                }
                const previewUrl = URL.createObjectURL(ready.blob);
                previewUrls.current.push(previewUrl);
                prepared.push({
                    key: `${file.name || 'photo'}:${file.size}:${prepared.length}:${file.lastModified || ''}`,
                    blob: ready.blob,
                    name: ready.name,
                    previewUrl,
                });
            }
            if (prepared.length) setQueue((prev) => [...prev, ...prepared]);
        } finally {
            setPreparing(false);
        }
    }, [kept.length, queue.length, showToast]);

    const detach = useCallback((tile) => {
        if (tile.photoId) {
            // Уже сохранённое: только помечаем. Снимется на «Сохранить».
            setRemovals((prev) => (prev.includes(tile.photoId) ? prev : [...prev, tile.photoId]));
            return;
        }
        setQueue((prev) => prev.filter((item) => item.key !== tile.key));
        if (tile.src) {
            previewUrls.current = previewUrls.current.filter((url) => url !== tile.src);
            releasePreviews([tile.src]);
        }
    }, [releasePreviews]);

    // Плитки в одном списке: сначала уже сохранённые, потом ждущие отправки.
    const tiles = useMemo(() => [
        ...kept.map((photo) => ({ key: `saved:${photo.id}`, photoId: photo.id, src: photo.thumb_url })),
        ...queue.map((item) => ({ key: item.key, src: item.previewUrl })),
    ], [kept, queue]);

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
            order_url: draft.order_url.trim(),
            comment: draft.comment.trim(),
        };
        try {
            // savedId — защита от второй карточки: если фото не долили и
            // человек нажал «Сохранить» повторно, идём PATCH'ем в уже
            // созданную запись, а не заводим её заново.
            const existingId = parcel?.id || savedId;
            const response = existingId
                ? await axios.patch(`${apiBaseUrl}/api/parcels/${existingId}`, body, { headers: headers() })
                : await axios.post(`${apiBaseUrl}/api/parcels`, body, { headers: headers() });
            const item = response.data?.item || null;
            const targetId = existingId || item?.id;
            if (!existingId && targetId) setSavedId(targetId);

            // Снятие — до загрузки: место в лимите освобождается раньше, чем
            // его занимают новые снимки.
            for (const photoId of removals) {
                try {
                    // eslint-disable-next-line no-await-in-loop
                    await axios.delete(`${apiBaseUrl}/api/parcels/${targetId}/photos/${photoId}`,
                                       { headers: headers() });
                } catch (dropError) {
                    // 404 — снимок уже сняли: это успех, а не сбой. Иначе форму
                    // нельзя было бы сохранить до перезагрузки страницы.
                    if (dropError?.response?.status !== 404) throw dropError;
                }
            }
            setRemovals([]);

            for (let index = 0; index < queue.length; index += 1) {
                const tile = queue[index];
                setPhotoStep(`Загружаю фото ${index + 1} из ${queue.length}`);
                const form = new FormData();
                // Content-Type руками НЕ ставим: его вместе с boundary
                // проставляет браузер, иначе request.files на сервере пуст, а
                // выглядит это как «файл не выбран».
                form.append('file', tile.blob, tile.name);
                // eslint-disable-next-line no-await-in-loop
                await axios.post(`${apiBaseUrl}/api/parcels/${targetId}/photos`, form,
                                 { headers: headers() });
                // Уехавшее убираем из очереди сразу: повторное «Сохранить»
                // после осечки не должно загрузить его второй раз.
                setQueue((prev) => prev.filter((entry) => entry.key !== tile.key));
            }
            setPhotoStep('');

            showToast?.(editing ? 'Карточка обновлена' : 'Посылка добавлена в реестр', 'success');
            // onSaved зовём ОДИН раз и в самом конце: он перезапрашивает список
            // и фильтры, и два вызова на одно сохранение стоили бы четырёх
            // лишних запросов.
            onSaved?.(item);
            onClose?.();
        } catch (requestError) {
            setPhotoStep('');
            setError(requestError?.response?.data?.error || 'Не удалось сохранить посылку');
        } finally {
            setSaving(false);
        }
    }, [apiBaseUrl, canSave, draft, editing, headers, nameOverride, onClose, onSaved, parcel,
        queue, removals, savedId, saving, showToast]);

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
                        {photoStep || 'Сохранить'}
                    </button>
                </>
            )}
        >
            <div className="space-y-5">
                <IosSection title="Где и когда">
                    <div className="grid gap-3 sm:grid-cols-2">
                        {/* Календарь раздела, а не системный `input[type=date]`:
                            раскрытый системный рисует браузер — своя шапка, свои
                            кнопки «Удалить / Сегодня», деталь из другой программы
                            рядом с rounded-2xl. Примитив уходит в портал, поэтому
                            внутри модалки его не обрезает. */}
                        <Field label="Дата приёма" required>
                            <IosDatePicker
                                value={draft.received_on}
                                max={todayISO()}
                                onChange={(iso) => set({ received_on: iso })}
                                placeholder="Выберите день"
                                triggerClassName={dateTrigger}
                                ariaLabel="Дата приёма посылки"
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

                    {/* Фотография вещи — такой же ответ на вопрос «что это»,
                        как описание, поэтому она стоит здесь, а не отдельной
                        секцией: четвёртый заголовок ради одной коробки был бы
                        шумом. Обёртка — не <label>: внутри лежит скрытый
                        <input type="file">, и щелчок по любой точке блока
                        открывал бы системный выбор файла. */}
                    {photosEnabled && (
                        <div className="block space-y-1.5">
                            <span className="flex items-baseline gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                Фото
                                <span className="font-normal normal-case tracking-normal text-slate-400">
                                    необязательно
                                </span>
                            </span>
                            <PhotoPicker
                                tiles={tiles}
                                active={open}
                                disabled={saving || preparing}
                                onAdd={addFiles}
                                onRemove={detach}
                            />
                        </div>
                    )}

                    {EXTRA_FIELDS.filter((field) => extras.includes(field.key)).map((field) => (
                        <Field key={field.key} label={field.label} hint={field.hint}>
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
