import React from 'react';
import { Plus, X } from 'lucide-react';
import { iosBtnGhost, iosInput } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { DEFAULT_UNIT, fmtMoney, isBlankItem, itemQuantity, itemTotal, itemsTotal, unitOptions } from './paymentsMeta';
import { AmountInput, QuantityInput } from './paymentsUi';

/*
 * Позиции заявки — таблица как в счёте поставщика: что, сколько, в чём считаем,
 * цена за единицу и сумма строки, чтобы было видно, из чего складывается
 * ориентировочная сумма (ТЗ «Закуп и оплата», п. 4.1: количество и сумма).
 *
 * Единица измерения — выбором из списка, а не полем ввода: в текстовое «Ед.»
 * вписывали второе число и ждали, что оно попадёт в итог.
 */

// Сетка строки на широком экране. До 768 px мобильная оболочка сайта схлопывает
// произвольные сетки в одну колонку, поэтому там строка собрана на flex, а
// сетка включается только с md.
const ITEM_GRID = 'md:grid md:grid-cols-[minmax(0,1fr)_72px_100px_124px_112px_28px] md:items-center md:gap-2';
/* На телефоне шапки таблицы нет — подпись стоит над каждым полем строки: без неё
   «20 · шт · 2 500» — три числа без объяснения, что из них количество, а что цена. */
const ITEM_CAPTION = 'mb-1 block px-1 text-[10.5px] font-medium uppercase tracking-wider text-slate-400 md:hidden';

export const newItem = () => ({
    key: Math.random().toString(36).slice(2, 9), name: '', quantity: '1', unit: DEFAULT_UNIT, unit_price: 0,
});

const PaymentItemsEditor = ({ items, onChange }) => {
    const total = itemsTotal(items);
    const update = (key, patch) => onChange(items.map((item) => (item.key === key ? { ...item, ...patch } : item)));
    const remove = (key) => onChange(items.length > 1 ? items.filter((row) => row.key !== key) : [newItem()]);
    return (
        <div className="overflow-hidden rounded-xl ring-1 ring-slate-200/70">
            <div className={`hidden ${ITEM_GRID} bg-slate-50 px-3 py-1.5 text-[11px] uppercase tracking-wider text-slate-500`}>
                <span>Товар или услуга</span>
                <span>Кол-во</span>
                <span>Ед. изм.</span>
                <span className="text-right">Цена за ед., ₸</span>
                <span className="text-right">Сумма, ₸</span>
                <span />
            </div>
            {items.map((item) => {
                const blank = isBlankItem(item);
                const nameMissing = !blank && !item.name.trim();
                const quantityBad = !blank && !(itemQuantity(item) > 0);
                return (
                    <div key={item.key} className={`border-t border-slate-200/60 px-3 py-2 ${ITEM_GRID}`}>
                        <div className="flex items-center gap-2 md:contents">
                            <input
                                className={`${iosInput} min-w-0 flex-1 !py-2 !text-[13.5px] ${nameMissing ? 'ring-2 ring-rose-300' : ''}`}
                                placeholder="Например: Бумага А4, 500 листов"
                                aria-label="Товар или услуга"
                                aria-invalid={nameMissing || undefined}
                                value={item.name}
                                onChange={(event) => update(item.key, { name: event.target.value })}
                                maxLength={300}
                            />
                            <button type="button" aria-label="Убрать позицию" className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 md:hidden" onClick={() => remove(item.key)}>
                                <X size={14} />
                            </button>
                        </div>
                        <div className="mt-2 flex gap-2 md:contents">
                            <div className="w-[72px] shrink-0 md:w-auto">
                                <span className={ITEM_CAPTION}>Кол-во</span>
                                <QuantityInput className="!py-2 !text-[13.5px]" value={item.quantity} invalid={quantityBad} onChange={(value) => update(item.key, { quantity: value })} />
                            </div>
                            <div className="w-[100px] shrink-0 md:w-auto">
                                <span className={ITEM_CAPTION}>Ед. изм.</span>
                                <CustomSelect
                                    value={item.unit || null}
                                    onChange={(value) => update(item.key, { unit: value || '' })}
                                    options={unitOptions(item.unit)}
                                    placeholder="ед."
                                    variant="ios"
                                    textClassName="text-[13.5px] text-slate-900"
                                    ariaLabel="Единица измерения"
                                />
                            </div>
                            <div className="min-w-0 flex-1 md:flex-none">
                                <span className={`${ITEM_CAPTION} text-right`}>Цена за ед., ₸</span>
                                <AmountInput ariaLabel="Цена за единицу" className="!py-2 text-right !text-[13.5px]" value={item.unit_price} onChange={(value) => update(item.key, { unit_price: value })} />
                            </div>
                        </div>
                        {/* Сумма строки — чтобы формула была на виду: количество × цена. */}
                        <div className="mt-1.5 flex items-baseline justify-between gap-2 text-[12.5px] text-slate-500 md:mt-0 md:block md:text-right">
                            <span className="md:hidden">Сумма: количество × цена</span>
                            {/* На компьютере «₸» стоит в шапке колонки, на телефоне шапки нет — знак у числа. */}
                            <span className="text-[13.5px] tabular-nums text-slate-900">
                                {blank ? '—' : fmtMoney(itemTotal(item), { currency: false })}
                                {!blank && <span className="md:hidden">{' ₸'}</span>}
                            </span>
                        </div>
                        <button type="button" aria-label="Убрать позицию" className="hidden h-7 w-7 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 md:grid" onClick={() => remove(item.key)}>
                            <X size={14} />
                        </button>
                    </div>
                );
            })}
            <div className="flex items-center justify-between gap-2 border-t border-slate-200/60 bg-slate-50 px-3 py-2">
                <button type="button" className={`${iosBtnGhost} -ml-2`} onClick={() => onChange([...items, newItem()])}>
                    <Plus size={14} /> Позиция
                </button>
                <div className="text-[13.5px] text-slate-600">
                    Ориентировочная сумма <span className="font-semibold tabular-nums text-slate-900">{fmtMoney(total)}</span>
                </div>
            </div>
        </div>
    );
};

export default PaymentItemsEditor;
