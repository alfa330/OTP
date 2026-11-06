import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { CARD_RECIPIENT_OPTIONS, HINTS, cardDigits, cardLuhnOk, cardNumberLabel, cardProblem } from './paymentsMeta';
import { Choice, Field, FormInput, FormSelect, UserSelect } from './paymentsUi';

/*
 * Пополнение карты (ТЗ «Закуп и оплата», пп. 5.2–5.4): чью карту пополнить, чья она, номер и
 * назначение перевода. Один блок на два места: форма заявки инициатора и этап Helpdesk IT —
 * у IT-закупа способ оплаты выбирает он, вместе с поставщиком (владелец 06.11.2026). Правила
 * полноты — paymentsMeta.cardIssues (сервер: workflow.card_missing).
 */

export const NEW_CARD = '__new__';

/* Сохранённые карты получателя (справочник карт, только маски): сотрудника — по нему,
   поставщика — по поставщику заявки. */
export const useSavedCards = ({ enabled, apiBaseUrl, headers, recipient, userId, counterpartyId }) => {
    const [options, setOptions] = useState([]);
    const ownerUser = recipient === 'employee' ? userId : null;
    const ownerSupplier = recipient === 'supplier' ? counterpartyId : null;
    useEffect(() => {
        if (!enabled || !(ownerUser || ownerSupplier)) {
            setOptions([]);
            return undefined;
        }
        let cancelled = false;
        const params = new URLSearchParams({ owner_kind: recipient });
        if (ownerUser) params.set('user_id', ownerUser);
        if (ownerSupplier) params.set('counterparty_id', ownerSupplier);
        axios.get(`${apiBaseUrl}/api/payments/cards/options?${params}`, { headers: headers() })
            .then((response) => { if (!cancelled) setOptions(response.data?.items || []); })
            .catch(() => { if (!cancelled) setOptions([]); });
        return () => { cancelled = true; };
    }, [enabled, recipient, ownerUser, ownerSupplier, apiBaseUrl, headers]);
    return options;
};

/* `onPatch` — дописать поля в черновик; `keptMask` — маска номера, что уже лежит в заявке и годится
   для этого получателя (тогда вместо ввода — маска и «Изменить»). */
const PaymentCardFields = ({ draft, onPatch, issueOf, users, keptMask = null, onEditKept, cardOptions = [], digitsRange }) => {
    const lunaWarn = !draft.card_id && cardDigits(draft.card_number).length >= 13 && !cardProblem(draft.card_number, digitsRange)
        && !cardLuhnOk(draft.card_number);
    return (
        <>
            <Field as="div" label="Получатель" required optionalMark={false} hint={HINTS.cardRecipient} anchor="card_recipient" issue={issueOf('card_recipient')}>
                <Choice value={draft.card_recipient} options={CARD_RECIPIENT_OPTIONS} onChange={(value) => onPatch({ card_recipient: value, card_id: null })} ariaLabel="Чью карту пополнить" />
            </Field>
            {draft.card_recipient === 'employee' && (
                <Field label="Сотрудник" required anchor="card_holder_user_id" issue={issueOf('card_holder_user_id')}>
                    <UserSelect users={users} value={draft.card_holder_user_id} onChange={(value) => onPatch({ card_holder_user_id: value, card_id: null })} placeholder="Чья карта" />
                </Field>
            )}
            {draft.card_recipient === 'supplier' && (
                <Field label="ФИО владельца карты" required anchor="card_holder_name" issue={issueOf('card_holder_name')}>
                    <FormInput value={draft.card_holder_name} onChange={(event) => onPatch({ card_holder_name: event.target.value })} maxLength={200} placeholder="Как на карте" />
                </Field>
            )}
            {draft.card_recipient && (keptMask ? (
                <Field as="div" label="Номер карты" required optionalMark={false} hint={HINTS.cardNumber}>
                    <div className="flex flex-wrap items-center gap-2 text-[14px] tabular-nums text-slate-900">
                        {keptMask}
                        <button type="button" className="text-[12.5px] font-medium text-blue-600 hover:underline" onClick={onEditKept}>Изменить</button>
                    </div>
                </Field>
            ) : (
                <div className="grid gap-3 sm:grid-cols-2">
                    {cardOptions.length > 0 && (
                        <Field label="Карта" required hint="Сохранённые карты получателя из справочника. Показаны последние четыре цифры.">
                            <FormSelect
                                value={draft.card_id ?? NEW_CARD}
                                onChange={(value) => onPatch({ card_id: value === NEW_CARD ? null : value })}
                                options={[...cardOptions.map((item) => ({ value: item.id, label: `${item.mask} · ${item.holder_name}` })), { value: NEW_CARD, label: 'Другая карта — ввести номер' }]}
                                ariaLabel="Карта получателя"
                            />
                        </Field>
                    )}
                    {!draft.card_id && (
                        <Field label="Номер карты" required hint={HINTS.cardNumber} anchor="card_number" issue={issueOf('card_number')}>
                            <FormInput
                                className={`tabular-nums ${lunaWarn ? 'ring-2 ring-amber-300' : ''}`}
                                inputMode="numeric"
                                autoComplete="off"
                                value={cardNumberLabel(draft.card_number)}
                                onChange={(event) => onPatch({ card_number: cardDigits(event.target.value).slice(0, 19) })}
                                placeholder="0000 0000 0000 0000"
                                aria-label="Номер карты"
                            />
                        </Field>
                    )}
                </div>
            ))}
            {lunaWarn && (
                <div className="px-1 text-[12.5px] text-amber-700">Проверьте номер: контрольная цифра не сходится — возможно, опечатка.</div>
            )}
            <Field label="Назначение перевода" required hint={HINTS.paymentPurpose} anchor="payment_purpose" issue={issueOf('payment_purpose')}>
                <FormInput value={draft.payment_purpose} onChange={(event) => onPatch({ payment_purpose: event.target.value })} maxLength={2000} placeholder="На что пойдут деньги" />
            </Field>
        </>
    );
};

export default PaymentCardFields;
