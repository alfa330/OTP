import React, { useMemo, useState } from 'react';
import { ChevronDown, MapPin, Phone, Plus, StickyNote, Trash2 } from 'lucide-react';
import { iosInput, iosGroupLabel, IosBadge } from '../ui/ios';
import { Field } from './formField';
import {
    PHONE_DIGITS, digitsOf, emptyNumber, formatDigits, parkDraftIssue, toPhone,
} from './parkPoints';

/* Форма таксопарка: о парке, номера по точкам, условия.
 *
 * Разбита на секции, а не в один столбик из восьми полей: у парка разнородные
 * данные, и «Название» рядом с «Комиссией» читается как один список, хотя
 * заполняют их в разное время и разные люди.
 *
 * Номера собраны в одну секцию — по строке на точку, где точка это офис или
 * «онлайн» (парк принимает только по телефону). До этого номер парка жил в
 * поле «Телефон» наверху, а номера офисов — галочками ниже, и один и тот же
 * вопрос «куда звонить» задавался в двух местах формы. Номеров на точке
 * бывает несколько: плюс рядом с полем добавляет следующий.
 */

/* Селектор офиса из справочника. Один на форму: им выбирают и место для
   номера, и адрес самого парка — списки обязаны быть одинаковыми, иначе в
   одной форме появятся два разных перечня офисов. */
const OfficeSelect = ({ value, offices, onChange, placeholder, className = '' }) => {
    // Города в порядке появления: список офисов уже отсортирован сервером, и
    // пересортировка тут развела бы одинаковые списки в двух вкладках.
    const byCity = useMemo(() => {
        const groups = [];
        offices.forEach((item) => {
            const city = item.city || 'Без города';
            const group = groups.find(([name]) => name === city);
            if (group) group[1].push(item);
            else groups.push([city, [item]]);
        });
        return groups;
    }, [offices]);

    return (
        <div className={`relative min-w-0 ${className}`}>
            <select
                className={`${iosInput} h-10 appearance-none py-0 pr-9`}
                value={value ?? ''}
                onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
            >
                <option value="">{placeholder}</option>
                {byCity.map(([city, items]) => (
                    <optgroup key={city} label={city}>
                        {items.map((item) => (
                            <option key={item.id} value={item.id}>
                                {item.name}
                                {item.is_online ? ' · только по телефону' : ''}
                            </option>
                        ))}
                    </optgroup>
                ))}
            </select>
            <ChevronDown
                size={15}
                className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400"
            />
        </div>
    );
};

/* Строка номера: сам номер, его место и записка.
 *
 * Плоско, по строке на номер: место у номера своё, и «офис → его номера»
 * заставляло сначала заводить место, а уже потом номер — хотя заполняют
 * наоборот. Офисный селектор появляется, только когда в справочнике есть что
 * выбирать: на пустом справочнике он говорил «Офисов в справочнике нет» и
 * мешал единственному, что здесь нужно, — вводу номера. */
const NumberRow = ({ number, offices, onChange, onRemove, onAdd, canRemove, isLast, showAddress }) => {
    const office = offices.find((item) => item.id === number.office_id);
    const digits = digitsOf(number.phone);
    const short = digits.length > 0 && digits.length < PHONE_DIGITS;
    const [noteOpen, setNoteOpen] = useState(!!number.note);

    return (
        <div className="space-y-1.5 rounded-xl border border-slate-200 bg-white p-2.5">
            <div className="flex flex-wrap items-center gap-1.5">
                {/* +7 не редактируется: у всех номеров справочника один код
                    страны, а руками его писали то как «+7», то как «8». */}
                <div className={`flex h-10 min-w-[190px] flex-1 items-center gap-2 rounded-xl bg-slate-100 px-3 transition focus-within:bg-white focus-within:ring-2 focus-within:ring-blue-500/70 ${
                    short ? 'ring-2 ring-amber-400/80' : ''
                }`}
                >
                    <span className="shrink-0 text-[14px] font-medium tabular-nums text-slate-500">+7</span>
                    <input
                        className="min-w-0 flex-1 border-0 bg-transparent p-0 text-[14px] tabular-nums text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-0"
                        inputMode="numeric"
                        autoComplete="off"
                        value={formatDigits(digits)}
                        placeholder="707 705 08 80"
                        onChange={(e) => onChange({ phone: toPhone(digitsOf(e.target.value)) })}
                    />
                </div>

                {offices.length > 0 && (
                    <OfficeSelect
                        className="flex-1 basis-[170px]"
                        value={number.office_id}
                        offices={offices}
                        placeholder="Без офиса (онлайн)"
                        onChange={(office_id) => onChange({ office_id })}
                    />
                )}

                <button
                    type="button"
                    onClick={() => setNoteOpen((open) => !open)}
                    className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl transition ${
                        number.note || noteOpen
                            ? 'bg-amber-50 text-amber-600'
                            : 'text-slate-400 hover:bg-slate-100 hover:text-slate-600'
                    }`}
                    aria-label="Записка к номеру"
                    title="Записка к номеру"
                >
                    <StickyNote size={15} />
                </button>

                <button
                    type="button"
                    onClick={onRemove}
                    disabled={!canRemove}
                    className="grid h-9 w-9 shrink-0 place-items-center rounded-xl text-slate-400 transition hover:bg-rose-50 hover:text-rose-500 disabled:invisible"
                    aria-label="Убрать номер"
                    title="Убрать номер"
                >
                    <Trash2 size={15} />
                </button>

                <button
                    type="button"
                    onClick={onAdd}
                    className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500 transition hover:bg-blue-50 hover:text-blue-600 ${
                        isLast ? '' : 'invisible'
                    }`}
                    tabIndex={isLast ? 0 : -1}
                    aria-label="Добавить ещё номер"
                    title="Добавить ещё номер"
                >
                    <Plus size={15} />
                </button>
            </div>

            {noteOpen && (
                <input
                    className={`${iosInput} h-9 text-[13px]`}
                    value={number.note || ''}
                    maxLength={200}
                    placeholder="Записка: «звонить после 10», «только WhatsApp»"
                    onChange={(e) => onChange({ note: e.target.value })}
                />
            )}

            {/* Адрес под селектором: названия офисов в справочнике похожи
                («Алматы Навигатор» и «Алматы Навигатор 2»), и адрес — это
                единственная подпись, по которой видно, что выбран тот. Показываем
                его один раз на офис: у второго номера того же офиса он ничего не
                добавляет, только удлиняет список. */}
            {showAddress && office?.address && (
                <p className="flex items-start gap-1.5 px-1 text-[11.5px] leading-relaxed text-slate-400">
                    <MapPin size={11} className="mt-0.5 shrink-0" /> {office.address}
                </p>
            )}
        </div>
    );
};

const ParkNumbers = ({ draft, setDraft }) => {
    const numbers = draft.numbers || [];

    const update = (key, patch) => setDraft((prev) => ({
        ...prev,
        numbers: prev.numbers.map((item) => (item.key === key ? { ...item, ...patch } : item)),
    }));

    const remove = (key) => setDraft((prev) => ({
        ...prev,
        numbers: prev.numbers.filter((item) => item.key !== key),
    }));

    // Новая строка наследует место соседней: номера одного офиса обычно вводят
    // подряд, и переспрашивать про офис на каждом — лишняя работа.
    const add = (afterKey) => setDraft((prev) => {
        const index = prev.numbers.findIndex((item) => item.key === afterKey);
        const source = index >= 0 ? prev.numbers[index] : null;
        const fresh = emptyNumber(source ? source.office_id : null);
        const next = [...prev.numbers];
        next.splice(index >= 0 ? index + 1 : next.length, 0, fresh);
        return { ...prev, numbers: next };
    });

    return (
        <div className="space-y-2">
            {numbers.map((number, index) => (
                <NumberRow
                    key={number.key}
                    number={number}
                    offices={draft.offices || []}
                    onChange={(patch) => update(number.key, patch)}
                    onRemove={() => remove(number.key)}
                    onAdd={() => add(number.key)}
                    canRemove={numbers.length > 1}
                    isLast={index === numbers.length - 1}
                    showAddress={numbers.findIndex(
                        (item) => item.office_id === number.office_id) === index}
                />
            ))}
        </div>
    );
};

export default function ParkEditor({ draft, setDraft, offices }) {
    const set = (key) => (e) => setDraft((prev) => ({ ...prev, [key]: e.target.value }));
    const issue = parkDraftIssue(draft);
    const numbers = draft.numbers || [];
    const headOffice = offices.find((office) => office.id === draft.head_office_id);

    return (
        <div className="space-y-5">
            <section className="space-y-3">
                <div className={iosGroupLabel}>О парке</div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Field label="Название">
                        <input
                            className={iosInput}
                            autoFocus
                            value={draft.name}
                            placeholder="iTaxi"
                            onChange={set('name')}
                        />
                    </Field>
                    <Field label="Город">
                        <input
                            className={iosInput}
                            value={draft.city}
                            placeholder="Алматы"
                            onChange={set('city')}
                        />
                    </Field>
                </div>
                <Field label="Описание">
                    <textarea
                        className={`${iosInput} min-h-[72px] resize-y`}
                        value={draft.description}
                        placeholder="Чем этот парк отличается — коротко, для оператора"
                        onChange={set('description')}
                    />
                </Field>
            </section>

            <section className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                    <div className={`${iosGroupLabel} flex items-center gap-1.5`}>
                        <Phone size={12} /> Номера
                    </div>
                    {numbers.length > 1 && (
                        <IosBadge tone="blue">номеров: {numbers.length}</IosBadge>
                    )}
                </div>
                <ParkNumbers draft={{ ...draft, offices }} setDraft={setDraft} />
                {issue && issue !== 'Укажите название парка' ? (
                    <p className="px-1 text-[11.5px] leading-relaxed text-amber-600">{issue}</p>
                ) : (
                    <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                        Плюс справа добавляет следующий номер. Если по номеру отвечают в
                        офисе — выберите его в строке; без офиса номер считается общим,
                        по нему принимают только по телефону. Значок записки вешает на
                        номер пометку вроде «звонить после 10».
                    </p>
                )}
            </section>

            <section className="space-y-3">
                <div className={iosGroupLabel}>Прочее</div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Field label="Сайт">
                        <input
                            className={iosInput}
                            type="url"
                            value={draft.website}
                            placeholder="https://"
                            onChange={set('website')}
                        />
                    </Field>
                    <Field label="Комиссия">
                        <div className="relative">
                            <input
                                className={`${iosInput} pr-8 tabular-nums`}
                                inputMode="decimal"
                                value={draft.commission}
                                placeholder="3.5"
                                onChange={(e) => setDraft((prev) => ({
                                    ...prev, commission: e.target.value.replace(/[^\d.]/g, ''),
                                }))}
                            />
                            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[13px] text-slate-400">
                                %
                            </span>
                        </div>
                    </Field>
                </div>
                {/* Адрес выбирается офисом, а не набирается: тот же адрес уже
                    записан в справочнике офисов, и второй его экземпляр здесь
                    неизбежно разошёлся бы с первым. */}
                <Field
                    label="Адрес"
                    hint={headOffice
                        ? 'Адрес и карта берутся из карточки офиса — правятся на вкладке «Офисы»'
                        : 'Главный офис парка из справочника. Нужного нет — заведите его на вкладке «Офисы»'}
                >
                    <OfficeSelect
                        value={draft.head_office_id}
                        offices={offices}
                        placeholder="Офис не выбран"
                        onChange={(head_office_id) => setDraft((prev) => ({ ...prev, head_office_id }))}
                    />
                    {headOffice && (
                        <p className="mt-1 flex items-start gap-1.5 px-1 text-[11.5px] leading-relaxed text-slate-500">
                            <MapPin size={11} className="mt-0.5 shrink-0 text-slate-400" />
                            {headOffice.address || 'Адрес у этого офиса не заполнен'}
                        </p>
                    )}
                    {/* Старый свободный адрес не выбрасываем молча: пока офис не
                        выбран, показываем, что было записано руками. */}
                    {!headOffice && draft.address && (
                        <p className="mt-1 px-1 text-[11.5px] leading-relaxed text-amber-600">
                            Записано вручную: {draft.address}. Выберите офис — текст заменится
                            адресом из справочника.
                        </p>
                    )}
                </Field>
            </section>
        </div>
    );
}
