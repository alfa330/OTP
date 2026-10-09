import React, { useRef } from 'react';
import { Check, FileText, Paperclip, Plus, X } from 'lucide-react';
import { iosBtnGhost, iosInput } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { fileSizeLabel, isBlankOffer } from './paymentsMeta';
import { AmountInput } from './paymentsUi';

/*
 * Варианты поставщиков заявки (ТЗ «Закуп и оплата», п. 4.2): у каждого —
 * наименование, стоимость, условия, ссылка, комментарий и вложение. Один из
 * вариантов отмечается рекомендуемым — он и станет поставщиком заявки.
 *
 * На виду у варианта только то, без чего его нет: кто и сколько. Условия,
 * ссылка, комментарий и коммерческое предложение открываются чипами «+ …» —
 * как дополнительные поля в форме задачи: у трёх вариантов по шесть полей на
 * виду вышла бы простыня.
 *
 * Поставщика выбирают из справочника; того, кого в нём нет, вписывают названием
 * («Нет в списке»). Альтернативу заводить в справочник незачем — туда попадёт
 * только рекомендуемый, когда заявка сохранится.
 *
 * Когда альтернативные предложения отсутствуют (п. 4.3), вариант один, и он же рекомендуемый.
 */

const NEW_SUPPLIER = '__new__';
const CAPTION = 'mb-1 block px-1 text-[10.5px] font-medium uppercase tracking-wider text-slate-400';
const ACCEPT = '.pdf,.jpg,.jpeg,.png,.webp,.heic,.doc,.docx,.xls,.xlsx,.csv,.txt,.zip';

const EXTRAS = [
    { key: 'terms', label: 'Условия' },
    { key: 'link', label: 'Ссылка' },
    { key: 'comment', label: 'Комментарий' },
    { key: 'file', label: 'Вложение' },
];

export const newOffer = (patch = {}) => ({
    key: Math.random().toString(36).slice(2, 9), id: null, counterparty_id: null, supplier_name: '',
    custom: false, amount: 0, terms: '', link: '', comment: '', is_recommended: false, file: null, extras: [],
    ...patch,
});

export const offerFromRow = (row) => newOffer({
    id: row.id ?? null,
    counterparty_id: row.counterparty_id ?? null,
    supplier_name: row.supplier_name || '',
    custom: !row.counterparty_id && Boolean(row.supplier_name),
    amount: Number(row.amount) || 0,
    terms: row.terms || '',
    link: row.link || '',
    comment: row.comment || '',
    is_recommended: Boolean(row.is_recommended),
    extras: ['terms', 'link', 'comment'].filter((key) => row[key]),
});

/* Вариант → то, что уходит на сервер. Файл едет отдельно, рядом с номером варианта. */
export const offerPayload = (offer) => ({
    id: offer.id || null,
    counterparty_id: offer.custom ? null : (offer.counterparty_id || null),
    supplier_name: offer.custom ? offer.supplier_name.trim() : '',
    amount: offer.amount || null,
    terms: offer.terms.trim() || null,
    link: offer.link.trim() || null,
    comment: offer.comment.trim() || null,
    is_recommended: Boolean(offer.is_recommended),
});

const PaymentOffersEditor = ({ offers, onChange, counterpartyOptions = [], savedFilesByOffer = {}, single = false }) => {
    const fileRefs = useRef({});
    const shown = single ? offers.slice(0, 1) : offers;
    const update = (key, patch) => onChange(offers.map((offer) => (offer.key === key ? { ...offer, ...patch } : offer)));
    const recommend = (key) => onChange(offers.map((offer) => ({ ...offer, is_recommended: offer.key === key })));
    const remove = (key) => {
        const rest = offers.filter((offer) => offer.key !== key);
        onChange(rest.length ? rest : [newOffer()]);
    };
    const options = [...counterpartyOptions, { value: NEW_SUPPLIER, label: 'Нет в списке — вписать название' }];

    return (
        <div className="space-y-2">
            {shown.map((offer, index) => {
                const blank = isBlankOffer(offer);
                const recommended = single || offer.is_recommended;
                const saved = (offer.id && savedFilesByOffer[offer.id]) || [];
                const open = (key) => offer.extras.includes(key) || Boolean(key === 'file' ? offer.file : offer[key]);
                return (
                    <div
                        key={offer.key}
                        data-offer={index + 1}
                        className={`rounded-xl p-3 ring-1 transition ${recommended && !blank ? 'bg-blue-50/40 ring-blue-200' : 'bg-white ring-slate-200/70'}`}
                    >
                        {/* На телефоне поставщик занимает строку целиком, стоимость и «Рекомендуемый»
                            встают под ним: оболочка телефона обнуляет min-w-[…], и в одну строку
                            список поставщика сжимался до полоски в букву шириной. */}
                        <div className="flex flex-wrap items-end gap-2">
                            <div className="w-full sm:w-auto sm:min-w-[200px] sm:flex-1">
                                <span className={CAPTION}>Поставщик {index + 1}</span>
                                {offer.custom ? (
                                    <div className="flex items-center gap-1.5">
                                        <input
                                            className={`${iosInput} !py-2 !text-[13.5px]`}
                                            placeholder="Название поставщика"
                                            aria-label={`Поставщик ${index + 1}: название`}
                                            value={offer.supplier_name}
                                            onChange={(event) => update(offer.key, { supplier_name: event.target.value })}
                                            maxLength={200}
                                        />
                                        <button type="button" className={`${iosBtnGhost} shrink-0`} onClick={() => update(offer.key, { custom: false, supplier_name: '' })}>
                                            из списка
                                        </button>
                                    </div>
                                ) : (
                                    <CustomSelect
                                        value={offer.counterparty_id}
                                        onChange={(value) => (value === NEW_SUPPLIER
                                            ? update(offer.key, { custom: true, counterparty_id: null })
                                            : update(offer.key, { counterparty_id: value }))}
                                        options={options}
                                        placeholder="Выберите поставщика"
                                        variant="ios"
                                        searchable
                                        textClassName="text-[13.5px] text-slate-900"
                                        ariaLabel={`Поставщик ${index + 1}`}
                                    />
                                )}
                            </div>
                            <div className="min-w-0 grow basis-0 sm:w-[150px] sm:shrink-0 sm:grow-0 sm:basis-auto">
                                <span className={`${CAPTION} text-right`}>Стоимость, ₸</span>
                                <AmountInput ariaLabel={`Поставщик ${index + 1}: стоимость`} className="!py-2 text-right !text-[13.5px]" value={offer.amount} onChange={(value) => update(offer.key, { amount: value })} />
                            </div>
                            {!single && (
                                <button
                                    type="button"
                                    role="radio"
                                    aria-checked={offer.is_recommended}
                                    onClick={() => recommend(offer.key)}
                                    title="У этого поставщика предлагаю купить"
                                    className={`inline-flex shrink-0 items-center gap-1.5 rounded-xl px-3 py-2 text-[12.5px] font-medium transition active:scale-[0.98] ${
                                        offer.is_recommended ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                                >
                                    {offer.is_recommended && <Check size={13} strokeWidth={3} />}
                                    Рекомендуемый
                                </button>
                            )}
                            {!single && (
                                <button type="button" aria-label={`Убрать поставщика ${index + 1}`} className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700" onClick={() => remove(offer.key)}>
                                    <X size={14} />
                                </button>
                            )}
                        </div>

                        {(open('terms') || open('link') || open('comment')) && (
                            <div className="mt-2 grid gap-2 sm:grid-cols-2">
                                {open('terms') && (
                                    <div>
                                        <span className={CAPTION}>Условия</span>
                                        <input className={`${iosInput} !py-2 !text-[13.5px]`} aria-label={`Поставщик ${index + 1}: условия`} placeholder="Срок поставки, оплата, гарантия" value={offer.terms} onChange={(event) => update(offer.key, { terms: event.target.value })} maxLength={2000} />
                                    </div>
                                )}
                                {open('link') && (
                                    <div>
                                        <span className={CAPTION}>Ссылка на товар или услугу</span>
                                        <input className={`${iosInput} !py-2 !text-[13.5px]`} aria-label={`Поставщик ${index + 1}: ссылка`} placeholder="https://…" inputMode="url" value={offer.link} onChange={(event) => update(offer.key, { link: event.target.value })} maxLength={1000} />
                                    </div>
                                )}
                                {open('comment') && (
                                    <div className="sm:col-span-2">
                                        <span className={CAPTION}>Комментарий</span>
                                        <input className={`${iosInput} !py-2 !text-[13.5px]`} aria-label={`Поставщик ${index + 1}: комментарий`} value={offer.comment} onChange={(event) => update(offer.key, { comment: event.target.value })} maxLength={2000} />
                                    </div>
                                )}
                            </div>
                        )}

                        {(saved.length > 0 || offer.file) && (
                            <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                {saved.map((file) => (
                                    <span key={file.id} className="inline-flex max-w-[260px] items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-[12.5px] text-slate-700" title={file.file_name}>
                                        <FileText size={12} className="shrink-0 text-slate-400" />
                                        <span className="truncate">{file.file_name}</span>
                                    </span>
                                ))}
                                {offer.file && (
                                    <span className="inline-flex max-w-[280px] items-center gap-1.5 rounded-full bg-slate-100 py-1 pl-2.5 pr-1 text-[12.5px] text-slate-700">
                                        <FileText size={12} className="shrink-0 text-slate-400" />
                                        <span className="truncate">{offer.file.name}</span>
                                        <span className="shrink-0 text-slate-400">{fileSizeLabel(offer.file.size)}</span>
                                        <button type="button" aria-label="Убрать вложение" className="grid h-5 w-5 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-700" onClick={() => update(offer.key, { file: null })}>
                                            <X size={11} />
                                        </button>
                                    </span>
                                )}
                            </div>
                        )}

                        <input
                            ref={(node) => { fileRefs.current[offer.key] = node; }}
                            type="file"
                            accept={ACCEPT}
                            className="hidden"
                            onChange={(event) => { update(offer.key, { file: event.target.files?.[0] || null }); event.target.value = ''; }}
                        />
                        <div className="mt-2 flex flex-wrap gap-1.5">
                            {EXTRAS.filter((extra) => !open(extra.key)).map((extra) => (
                                <button
                                    key={extra.key}
                                    type="button"
                                    onClick={() => (extra.key === 'file'
                                        ? fileRefs.current[offer.key]?.click()
                                        : update(offer.key, { extras: [...offer.extras, extra.key] }))}
                                    className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[12px] text-slate-600 transition hover:bg-slate-200 active:scale-[0.98]"
                                >
                                    {extra.key === 'file' ? <Paperclip size={11} /> : <Plus size={11} />} {extra.label}
                                </button>
                            ))}
                        </div>
                    </div>
                );
            })}
            {!single && (
                <button type="button" className={`${iosBtnGhost} -ml-1`} onClick={() => onChange([...offers, newOffer()])}>
                    <Plus size={14} /> Поставщик
                </button>
            )}
        </div>
    );
};

export default PaymentOffersEditor;
