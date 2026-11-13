import React, { useMemo, useRef, useState } from 'react';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import { IosModal, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosGroupLabel } from '../ui/ios';
import IosTimePicker from '../ui/TimePicker';
import {
    Choice, Field, FormDate, FormInput, FormSelect, FormText, IssueNav, QuantityInput, useIssueNav,
} from '../payments/paymentsUi';
import {
    COPY_KIND_LABELS, DELIVERY_TYPE_LABELS, DOC_TYPE_OPTIONS, URGENCY_LABELS, blankItem, blankForm, economyReady,
    errorText, formIssues, formOrder, formToPayload, itemsLockText, officeLine, officeOptions, recipientDefaults,
    serverIssues, shipmentToForm,
} from './mailMeta';

/*
 * Новое отправление и правка (ТЗ п. 3, 5; макет «Новое отправление»). На виду — обязательное:
 * маршрут, отправитель, получатель, содержимое, вес и плательщик. Необязательное (объёмный вес,
 * объявленная ценность, комментарий, номер документа, конфиденциальность) — кнопками.
 *
 * «Сохранить черновик» проверяет только форму строк: «Иное» без описания не сохраняется даже
 * черновиком (ТЗ п. 3). «Готово к отправке» — полноту. Не хватает — поле краснеет, окно подводит
 * к первому, стрелки «Не хватает: N» водят по остальным (как в «Оплате счетов»).
 * После передачи курьеру правка — только с причиной; офисы тогда не меняются (сервер держит то же).
 */
const DELIVERY_OPTIONS = Object.entries(DELIVERY_TYPE_LABELS).map(([value, label]) => ({ value, label }));
const URGENCY_OPTIONS = Object.entries(URGENCY_LABELS).map(([value, label]) => ({ value, label }));
const COPY_OPTIONS = Object.entries(COPY_KIND_LABELS).map(([value, label]) => ({ value, label }));
const BEFORE_HANDOVER = ['draft', 'ready', 'courier_requested'];

const HINTS = {
    sender_office_id: 'Отправлять можно из своего офиса — его задаёт администратор раздела во вкладке «Участники».',
    recipient_office_id: 'Офис из справочника офисов. Адрес подставится сам — поправьте, если везут не в офис.',
    delivery_type: 'Стандарт — срок по зоне договора. Эконом — дольше; его срок задаётся в «Справочниках».',
    urgency: 'Срочная — подсказка координатору отправить первой. На срок по договору не влияет.',
    weight_kg: 'Фактический вес с упаковкой — он нужен для заявки курьеру.',
    volume_weight_kg: 'Для крупных коробок: курьер считает по большему из фактического и объёмного веса.',
    payer_entity_id: 'Чьё юрлицо оплачивает доставку. Список — из справочника «Оплаты счетов».',
    copy_kind: 'Оригинал — получатель сверяет подписи и печати; копия — только содержание.',
    is_confidential: 'Вложения такого отправления не откроются бухгалтерии.',
};

const CHIP_BTN = `${iosBtnGhost} -ml-2 !px-2.5 !py-1.5 text-[12.5px]`;
const TOGGLE = 'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-medium ring-1 transition active:scale-[0.97]';

function ItemRow({ item, index, count, issueOf, onChange, onRemove, locked }) {
    const key = (name) => `items.${index}.${name}`;
    return (
        <div className="space-y-3 rounded-2xl bg-slate-50 p-3 ring-1 ring-slate-200/60" data-mail-item={index}>
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1.4fr)_7rem_auto_auto] sm:items-end">
                <Field label="Тип документа" required anchor={key('doc_type')} issue={issueOf(key('doc_type'))} as="div">
                    <FormSelect value={item.doc_type} options={DOC_TYPE_OPTIONS} disabled={locked}
                                onChange={(value) => onChange({ doc_type: value })} ariaLabel="Тип документа" />
                </Field>
                <Field label="Количество" required anchor={key('qty')} issue={issueOf(key('qty'))}>
                    {locked ? <FormInput value={item.qty} disabled readOnly aria-label="Количество" />
                        : <QuantityInput value={item.qty} onChange={(value) => onChange({ qty: value.replace(/[^\d]/g, '') })} />}
                </Field>
                <Field label="Вид" hint={HINTS.copy_kind} required as="div">
                    <Choice value={item.copy_kind} options={COPY_OPTIONS} onChange={(value) => onChange({ copy_kind: value })}
                            ariaLabel="Оригинал или копия" disabled={locked} />
                </Field>
                {count > 1 && !locked && (
                    <button type="button" onClick={onRemove} aria-label="Убрать строку"
                            className="grid h-10 w-10 place-items-center self-end rounded-xl text-slate-400 transition hover:bg-slate-200/70 hover:text-rose-600">
                        <Trash2 size={16} />
                    </button>
                )}
            </div>
            {item.doc_type === 'other' && (
                <Field label="Укажите, что именно отправляется" required anchor={key('other_description')}
                       issue={issueOf(key('other_description'))}>
                    <FormInput value={item.other_description} maxLength={500} disabled={locked}
                               placeholder="Например: фирменные кепки — 10 шт."
                               onChange={(event) => onChange({ other_description: event.target.value })} />
                </Field>
            )}
            <Field label="Период документа" as="div" anchor={key('period_to')} issue={issueOf(key('period_to'))}
                   hint="За какой период документ — например, АВР за сентябрь: с 01.09 по 30.09. По нему ищут в реестре.">
                <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2">
                    <FormDate value={item.period_from} disabled={locked} ariaLabel="Период с" placeholder="с"
                              onChange={(value) => onChange({ period_from: value || '' })} />
                    <span className="text-slate-400">—</span>
                    <FormDate value={item.period_to} min={item.period_from || undefined} disabled={locked} ariaLabel="Период по"
                              placeholder="по" onChange={(value) => onChange({ period_to: value || '' })} />
                </div>
            </Field>
            {item.showNumber && (
                <Field label="№ документа" optionalMark={false}>
                    <FormInput value={item.doc_number} maxLength={64} disabled={locked}
                               onChange={(event) => onChange({ doc_number: event.target.value })} />
                </Field>
            )}
            {!locked && (
                <div className="flex flex-wrap items-center gap-2">
                    {!item.showNumber && (
                        <button type="button" className={CHIP_BTN} onClick={() => onChange({ showNumber: true })}>
                            <Plus size={13} /> № документа
                        </button>
                    )}
                    <button type="button" title={HINTS.is_confidential} aria-pressed={item.is_confidential}
                            onClick={() => onChange({ is_confidential: !item.is_confidential })}
                            className={`${TOGGLE} ${item.is_confidential
                                ? 'bg-[#eef4ff] text-[#1d4ed8] ring-[#bfd4fb]'
                                : 'bg-white text-slate-600 ring-slate-200/70 hover:bg-[#f6f6f4]'}`}>
                        {item.is_confidential ? '✓ ' : ''}Конфиденциально
                    </button>
                </div>
            )}
        </div>
    );
}

export default function MailShipmentForm({ open, card, api, context, toast, onClose, onSaved }) {
    const editing = Boolean(card);
    const status = card?.shipment?.status || 'draft';
    const isDraft = !editing || status === 'draft';
    const reasonRequired = editing && Boolean(card.edit?.reason_required);
    const itemsLocked = editing && !card.edit?.items;
    const officesLocked = editing && !BEFORE_HANDOVER.includes(status);

    const [form, setForm] = useState(() => (editing ? shipmentToForm(card) : blankForm(context)));
    const [extras, setExtras] = useState(() => ({
        volume: Boolean(form.volume_weight_kg), value: Boolean(form.declared_value), comment: Boolean(form.comment),
    }));
    const [reason, setReason] = useState('');
    const [tried, setTried] = useState(null);
    const [fromServer, setFromServer] = useState([]);
    const [busy, setBusy] = useState('');
    const rootRef = useRef(null);
    // Правка передачи курьеру (трек и время) — стороне отправки, с причиной (ТЗ п. 11).
    const canFixHandover = editing && Boolean(card.can_fix_handover);
    const handedAt = String(card?.shipment?.handed_at || '');
    const [handover, setHandover] = useState(() => ({
        track_number: card?.shipment?.track_number || '', date: handedAt.slice(0, 10), time: handedAt.slice(11, 16),
    }));
    const [duplicate, setDuplicate] = useState(null);
    const economyAllowed = economyReady(context?.zones);

    const offices = useMemo(() => context?.offices || [], [context]);
    /* Офис карточки, которого нет среди вариантов (чужой для правящего или ушедший в архив), всё равно
       виден в поле: иначе «Откуда» выглядело бы пустым, хотя значение есть. */
    const withCurrent = (options, id, name, city) => (id && !options.some((option) => option.value === id)
        ? [{ value: id, label: `${officeLine(name, city)}${offices.some((office) => office.id === id) ? '' : ' (в архиве)'}` },
            ...options]
        : options);
    const senderOptions = useMemo(() => withCurrent(officeOptions(offices, context?.sender_office_ids || []),
        card?.shipment?.sender_office_id, card?.shipment?.sender_office_name, card?.shipment?.sender_city),
    [offices, context, card]); // eslint-disable-line react-hooks/exhaustive-deps
    const recipientOptions = useMemo(() => withCurrent(officeOptions(offices), card?.shipment?.recipient_office_id,
        card?.shipment?.recipient_office_name, card?.shipment?.recipient_city)
        .filter((option) => option.value !== form.sender_office_id),
    [offices, form.sender_office_id, card]); // eslint-disable-line react-hooks/exhaustive-deps
    const payerOptions = useMemo(() => (context?.legal_entities || []).map((item) => ({ value: item.id, label: item.name })),
        [context]);

    const handoverIssues = () => {
        if (!canFixHandover) return [];
        const issues = [];
        if (!handover.track_number.trim()) issues.push({ key: 'track_number', text: 'Трек-номер или согласованный временный номер' });
        if (!handover.date || !handover.time) issues.push({ key: 'handed_at', text: 'Укажите дату и время передачи' });
        return issues;
    };
    const issues = useMemo(() => {
        if (!tried) return [];
        const local = formIssues(form, { ready: tried === 'ready', economyAllowed }).concat(handoverIssues());
        if (reasonRequired && !reason.trim()) local.push({ key: 'reason', text: 'Укажите причину изменения' });
        const keys = new Set(local.map((item) => item.key));
        return local.concat(fromServer.filter((item) => !keys.has(item.key)));
    }, [tried, form, reason, reasonRequired, fromServer, handover, economyAllowed]); // eslint-disable-line react-hooks/exhaustive-deps
    const issueOf = (key) => issues.find((item) => item.key === key)?.text || '';
    const order = useMemo(() => formOrder(form).concat(['reason']), [form]);
    const issueKeys = useMemo(() => {
        const keys = [...new Set(issues.map((item) => item.key))];
        return keys.sort((a, b) => (order.indexOf(a) < 0 ? 999 : order.indexOf(a)) - (order.indexOf(b) < 0 ? 999 : order.indexOf(b)));
    }, [issues, order]);
    const nav = useIssueNav(rootRef, issueKeys);

    const patch = (changes) => {
        setForm((prev) => ({ ...prev, ...changes }));
        setFromServer((prev) => prev.filter((item) => !Object.keys(changes).includes(item.key)));
    };
    const patchItem = (index, changes) => {
        setForm((prev) => ({ ...prev, items: prev.items.map((item, at) => (at === index ? { ...item, ...changes } : item)) }));
        setFromServer((prev) => prev.filter((item) => !Object.keys(changes).some((name) => item.key === `items.${index}.${name}`)));
    };
    const chooseRecipient = (officeId) => {
        const office = offices.find((item) => item.id === officeId);
        setForm((prev) => recipientDefaults({ ...prev, recipient_office_id: officeId }, office));
        setFromServer((prev) => prev.filter((item) => !['recipient_office_id', 'recipient_address'].includes(item.key)));
    };

    const submit = async (mode) => {
        // mode: 'draft' — сохранить черновик; 'ready' — «Готово к отправке»; 'save' — правка не черновика.
        const ready = mode === 'ready' || (editing && !isDraft);
        const local = formIssues(form, { ready, economyAllowed }).concat(handoverIssues());
        if (reasonRequired && !reason.trim()) local.push({ key: 'reason', text: 'Укажите причину изменения' });
        setTried(ready ? 'ready' : 'draft');
        setFromServer([]);
        if (local.length) {
            nav.first();
            return;
        }
        setBusy(mode);
        let patched = null;
        try {
            const payload = formToPayload(form);
            // Закрытые строки не отправляем вовсе: сервер их и так не трогает без ключа items.
            if (itemsLocked) delete payload.items;
            let saved;
            if (editing) {
                const body = { ...payload, reason };
                if (canFixHandover) {
                    const nextHanded = `${handover.date}T${handover.time}`;
                    if (handover.track_number.trim() !== (card.shipment.track_number || '')) body.track_number = handover.track_number;
                    if (nextHanded !== handedAt.slice(0, 16)) body.handed_at = nextHanded;
                    if (duplicate) body.confirm_duplicate = true;
                }
                const { data } = await api.patch(`/shipments/${card.shipment.id}`, body);
                saved = data;
                patched = data;
                if (mode === 'ready' && isDraft) {
                    const response = await api.post(`/shipments/${card.shipment.id}/actions/ready`, {});
                    saved = response.data;
                }
            } else {
                const { data } = await api.post('/shipments', { ...payload, submit: mode === 'ready' });
                saved = data;
            }
            toast?.(editing ? 'Изменения сохранены' : `Отправление ${saved.shipment.number} создано`, 'success');
            onSaved?.(saved);
        } catch (requestError) {
            const body = requestError?.response?.data;
            if (body?.code === 'MAIL_TRACK_DUPLICATE') {
                setDuplicate(body.duplicates || []);
            } else {
                const fields = serverIssues(requestError);
                if (fields.length && !patched) {
                    setFromServer(fields);
                    nav.first();
                }
                toast?.(errorText(requestError, 'Не сохранилось'), 'error');
            }
            // Правка уже сохранена, а «Готово к отправке» не прошло: карточка и списки обновляются,
            // чтобы на экране не осталось старых данных.
            if (patched) onSaved?.(patched);
        } finally {
            setBusy('');
        }
    };

    const footer = (
        <div className="flex flex-wrap items-center justify-end gap-2">
            <IssueNav count={issueKeys.length} onStep={(step) => nav.step(step)} />
            <button type="button" className={iosBtnGhost} onClick={onClose}>Отмена</button>
            {isDraft && (
                <button type="button" className={iosBtnSecondary} onClick={() => submit('draft')} disabled={Boolean(busy)}>
                    {busy === 'draft' && <Loader2 size={15} className="animate-spin" />}
                    {editing ? 'Сохранить' : 'Сохранить черновик'}
                </button>
            )}
            <button type="button" className={iosBtnPrimary} onClick={() => submit(isDraft ? 'ready' : 'save')} disabled={Boolean(busy)}>
                {(busy === 'ready' || busy === 'save') && <Loader2 size={15} className="animate-spin" />}
                {isDraft ? 'Готово к отправке' : 'Сохранить'}
            </button>
        </div>
    );

    const title = editing ? `Правка · ${card.shipment.number}` : 'Новое отправление';
    return (
        <IosModal open={open} onClose={onClose} title={title} maxWidth="max-w-3xl" footer={footer}
                  subtitle={editing && reasonRequired ? 'Отправление уже у курьера — изменения сохраняются с причиной' : ''}>
            <div ref={rootRef} className="space-y-6" data-mail-form>
                <section className="space-y-3">
                    <div className={iosGroupLabel}>Маршрут</div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Откуда" required hint={HINTS.sender_office_id} as="div" anchor="sender_office_id"
                               issue={issueOf('sender_office_id')}>
                            <FormSelect value={form.sender_office_id} options={senderOptions} placeholder="Офис отправки"
                                        searchable={senderOptions.length > 6} ariaLabel="Офис отправки"
                                        disabled={officesLocked || senderOptions.length === 0}
                                        onChange={(value) => patch({ sender_office_id: value })} />
                        </Field>
                        <Field label="Куда" required hint={HINTS.recipient_office_id} as="div" anchor="recipient_office_id"
                               issue={issueOf('recipient_office_id')}>
                            <FormSelect value={form.recipient_office_id} options={recipientOptions} placeholder="Офис получения"
                                        searchable ariaLabel="Офис получения" disabled={officesLocked}
                                        onChange={chooseRecipient} />
                        </Field>
                    </div>
                </section>

                <section className="space-y-3">
                    <div className={iosGroupLabel}>Отправитель</div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="ФИО" required anchor="sender_name" issue={issueOf('sender_name')}>
                            <FormInput value={form.sender_name} maxLength={200} onChange={(event) => patch({ sender_name: event.target.value })} />
                        </Field>
                        <Field label="Телефон" required anchor="sender_phone" issue={issueOf('sender_phone')}>
                            <FormInput value={form.sender_phone} maxLength={32} inputMode="tel" placeholder="+7"
                                       onChange={(event) => patch({ sender_phone: event.target.value })} />
                        </Field>
                    </div>
                </section>

                <section className="space-y-3">
                    <div className={iosGroupLabel}>Получатель</div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="ФИО" required anchor="recipient_name" issue={issueOf('recipient_name')}>
                            <FormInput value={form.recipient_name} maxLength={200}
                                       onChange={(event) => patch({ recipient_name: event.target.value })} />
                        </Field>
                        <Field label="Телефон" required anchor="recipient_phone" issue={issueOf('recipient_phone')}>
                            <FormInput value={form.recipient_phone} maxLength={32} inputMode="tel" placeholder="+7"
                                       onChange={(event) => patch({ recipient_phone: event.target.value })} />
                        </Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_8rem]">
                        <Field label="Адрес" required anchor="recipient_address" issue={issueOf('recipient_address')}>
                            <FormInput value={form.recipient_address} maxLength={1000}
                                       onChange={(event) => patch({ recipient_address: event.target.value })} />
                        </Field>
                        <Field label="Индекс" anchor="recipient_postcode" issue={issueOf('recipient_postcode')} optionalMark={false}>
                            <FormInput value={form.recipient_postcode} maxLength={12} inputMode="numeric"
                                       onChange={(event) => patch({ recipient_postcode: event.target.value.replace(/[^\d ]/g, '') })} />
                        </Field>
                    </div>
                </section>

                <section className="space-y-3" data-issue-anchor="items">
                    <div className="flex items-center justify-between">
                        <span className={iosGroupLabel}>Содержимое</span>
                        {itemsLocked && <span className="text-[12px] text-slate-500">{itemsLockText(card.shipment)}</span>}
                    </div>
                    {issueOf('items') && <div className="px-1 text-[12.5px] text-rose-600">{issueOf('items')}</div>}
                    {form.items.map((item, index) => (
                        <ItemRow key={item.key} item={item} index={index} count={form.items.length} issueOf={issueOf}
                                 locked={itemsLocked} onChange={(changes) => patchItem(index, changes)}
                                 onRemove={() => setForm((prev) => ({ ...prev, items: prev.items.filter((_x, at) => at !== index) }))} />
                    ))}
                    {!itemsLocked && form.items.length < 50 && (
                        <button type="button" className={`${iosBtnGhost} -ml-2`}
                                onClick={() => setForm((prev) => ({ ...prev, items: [...prev.items, blankItem()] }))}>
                            <Plus size={14} /> Добавить строку
                        </button>
                    )}
                </section>

                <section className="space-y-3">
                    <div className={iosGroupLabel}>Доставка и оплата</div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Тип доставки" required hint={HINTS.delivery_type} as="div" anchor="delivery_type"
                               issue={issueOf('delivery_type')}>
                            <Choice value={form.delivery_type} options={DELIVERY_OPTIONS} ariaLabel="Тип доставки"
                                    onChange={(value) => patch({ delivery_type: value })} />
                        </Field>
                        <Field label="Срочность" required hint={HINTS.urgency} as="div">
                            <Choice value={form.urgency} options={URGENCY_OPTIONS} ariaLabel="Срочность"
                                    onChange={(value) => patch({ urgency: value })} />
                        </Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-[7rem_9rem_minmax(0,1fr)]">
                        <Field label="Мест" required anchor="places" issue={issueOf('places')}>
                            <QuantityInput value={form.places} onChange={(value) => patch({ places: value.replace(/[^\d]/g, '') })} />
                        </Field>
                        <Field label="Вес, кг" required hint={HINTS.weight_kg} anchor="weight_kg" issue={issueOf('weight_kg')}>
                            <QuantityInput value={form.weight_kg} ariaLabel="Вес, кг" onChange={(value) => patch({ weight_kg: value })} />
                        </Field>
                        <Field label="Юрлицо-плательщик" required hint={HINTS.payer_entity_id} as="div" anchor="payer_entity_id"
                               issue={issueOf('payer_entity_id')}>
                            <FormSelect value={form.payer_entity_id} options={payerOptions} placeholder="Кто оплачивает"
                                        searchable={payerOptions.length > 6} ariaLabel="Юрлицо-плательщик"
                                        onChange={(value) => patch({ payer_entity_id: value })} />
                        </Field>
                    </div>
                    {(extras.volume || extras.value) && (
                        <div className="grid gap-3 sm:grid-cols-2">
                            {extras.volume && (
                                <Field label="Объёмный вес, кг" hint={HINTS.volume_weight_kg} optionalMark={false}
                                       anchor="volume_weight_kg" issue={issueOf('volume_weight_kg')}>
                                    <QuantityInput value={form.volume_weight_kg} ariaLabel="Объёмный вес"
                                                   onChange={(value) => patch({ volume_weight_kg: value })} />
                                </Field>
                            )}
                            {extras.value && (
                                <Field label="Объявленная ценность, ₸" optionalMark={false} anchor="declared_value"
                                       issue={issueOf('declared_value')}>
                                    <QuantityInput value={form.declared_value} ariaLabel="Объявленная ценность"
                                                   onChange={(value) => patch({ declared_value: value })} />
                                </Field>
                            )}
                        </div>
                    )}
                    {extras.comment && (
                        <Field label="Комментарий" optionalMark={false}>
                            <FormText rows={2} value={form.comment} maxLength={2000}
                                      onChange={(event) => patch({ comment: event.target.value })} />
                        </Field>
                    )}
                    <div className="flex flex-wrap gap-1">
                        {!extras.volume && (
                            <button type="button" className={CHIP_BTN} onClick={() => setExtras((prev) => ({ ...prev, volume: true }))}>
                                <Plus size={13} /> Объёмный вес
                            </button>
                        )}
                        {!extras.value && (
                            <button type="button" className={CHIP_BTN} onClick={() => setExtras((prev) => ({ ...prev, value: true }))}>
                                <Plus size={13} /> Объявленная ценность
                            </button>
                        )}
                        {!extras.comment && (
                            <button type="button" className={CHIP_BTN} onClick={() => setExtras((prev) => ({ ...prev, comment: true }))}>
                                <Plus size={13} /> Комментарий
                            </button>
                        )}
                    </div>
                </section>

                {canFixHandover && (
                    <section className="space-y-3">
                        <div className={iosGroupLabel}>Передача курьеру</div>
                        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_9rem]">
                            <Field label="Трек-номер" required anchor="track_number" issue={issueOf('track_number')}>
                                <FormInput value={handover.track_number} maxLength={64}
                                           onChange={(event) => {
                                               setHandover((prev) => ({ ...prev, track_number: event.target.value }));
                                               setDuplicate(null);
                                           }} />
                            </Field>
                            <Field label="Дата передачи" required as="div" anchor="handed_at" issue={issueOf('handed_at')}
                                   hint="Срок доставки пересчитается от нового времени передачи — в рабочих днях по зоне.">
                                <FormDate value={handover.date} onChange={(value) => setHandover((prev) => ({ ...prev, date: value || '' }))} />
                            </Field>
                            <Field label="Время" required as="div">
                                <IosTimePicker value={handover.time} step={5} allowEmpty={false} ariaLabel="Время передачи"
                                               onChange={(value) => setHandover((prev) => ({ ...prev, time: value || '' }))} />
                            </Field>
                        </div>
                        {duplicate && (
                            <div className="rounded-xl bg-amber-50 px-3.5 py-2.5 text-[13px] text-amber-900 ring-1 ring-amber-200/70">
                                Этот трек уже есть у {duplicate.join(', ')}. Если это не ошибка — сохраните ещё раз.
                            </div>
                        )}
                    </section>
                )}

                {reasonRequired && (
                    <section className="space-y-3">
                        <Field label="Причина изменения" required anchor="reason" issue={issueOf('reason')}>
                            <FormText rows={2} value={reason} maxLength={2000} placeholder="Что и почему меняете — увидят в истории"
                                      onChange={(event) => setReason(event.target.value)} />
                        </Field>
                    </section>
                )}
            </div>
        </IosModal>
    );
}
