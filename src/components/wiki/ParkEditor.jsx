import React, { useMemo } from 'react';
import { ChevronDown, MapPin, Phone, Plus, Trash2, Wifi, X } from 'lucide-react';
import { iosInput, iosGroupLabel, iosBtnGhost, IosBadge } from '../ui/ios';
import { Field } from './formField';
import { ONLINE, emptyPoint, parkDraftIssue } from './parkPoints';

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

const PointRow = ({ point, offices, taken, onChange, onRemove }) => {
    const office = offices.find((item) => item.id === point.office_id);
    const unset = point.office_id === undefined;

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

    const setPhone = (index, value) => onChange({
        phones: point.phones.map((phone, position) => (position === index ? value : phone)),
    });

    const addPhone = () => onChange({ phones: [...point.phones, ''] });

    const dropPhone = (index) => onChange({
        phones: point.phones.filter((phone, position) => position !== index),
    });

    return (
        <div className="space-y-1.5 rounded-xl border border-slate-200 bg-white p-2.5">
            <div className="flex items-center gap-1.5">
                <div className="relative min-w-0 flex-1">
                    <select
                        className={`${iosInput} h-10 appearance-none py-0 pr-9 ${
                            unset ? 'ring-2 ring-amber-400/80' : ''
                        }`}
                        value={point.office_id === null ? ONLINE : (point.office_id ?? '')}
                        onChange={(e) => {
                            const { value } = e.target;
                            onChange({
                                office_id: value === ONLINE ? null
                                    : (value === '' ? undefined : Number(value)),
                            });
                        }}
                    >
                        {unset && <option value="">Куда звонят? Выберите офис или онлайн</option>}
                        <option value={ONLINE} disabled={taken(ONLINE)}>
                            Онлайн — без офиса
                        </option>
                        {byCity.map(([city, items]) => (
                            <optgroup key={city} label={city}>
                                {items.map((item) => (
                                    <option key={item.id} value={item.id} disabled={taken(item.id)}>
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
                <button
                    type="button"
                    onClick={onRemove}
                    className="grid h-9 w-9 shrink-0 place-items-center rounded-xl text-slate-400 transition hover:bg-rose-50 hover:text-rose-500"
                    aria-label="Убрать строку"
                >
                    <Trash2 size={15} />
                </button>
            </div>

            {/* Адрес под селектором: названия офисов в справочнике похожи
                («Алматы Навигатор» и «Алматы Навигатор 2»), и адрес — это
                единственная подпись, по которой видно, что выбран тот. */}
            {point.office_id === null ? (
                <p className="flex items-center gap-1.5 px-1 text-[11.5px] text-slate-400">
                    <Wifi size={11} /> Номер без адреса — парк принимает только по телефону
                </p>
            ) : office?.address && (
                <p className="flex items-start gap-1.5 px-1 text-[11.5px] leading-relaxed text-slate-400">
                    <MapPin size={11} className="mt-0.5 shrink-0" /> {office.address}
                </p>
            )}

            {point.phones.map((phone, index) => (
                // eslint-disable-next-line react/no-array-index-key
                <div key={index} className="flex items-center gap-1.5">
                    <div className="relative min-w-0 flex-1">
                        <Phone
                            size={13}
                            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
                        />
                        <input
                            className={`${iosInput} h-10 pl-8 tabular-nums`}
                            inputMode="tel"
                            value={phone}
                            placeholder={office?.phone
                                ? `телефон офиса: ${office.phone}`
                                : '+7 700 000 00 00'}
                            onChange={(e) => setPhone(index, e.target.value)}
                        />
                    </div>
                    {/* Обе кнопки занимают место всегда, даже когда не нужны:
                        иначе строка с двумя кнопками становится короче
                        соседней, и поля перестают стоять в столбик. */}
                    <button
                        type="button"
                        onClick={() => dropPhone(index)}
                        className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl text-slate-400 transition hover:bg-rose-50 hover:text-rose-500 ${
                            point.phones.length > 1 ? '' : 'invisible'
                        }`}
                        tabIndex={point.phones.length > 1 ? 0 : -1}
                        aria-label="Убрать номер"
                        title="Убрать номер"
                    >
                        <X size={15} />
                    </button>
                    <button
                        type="button"
                        onClick={addPhone}
                        className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500 transition hover:bg-blue-50 hover:text-blue-600 ${
                            index === point.phones.length - 1 ? '' : 'invisible'
                        }`}
                        tabIndex={index === point.phones.length - 1 ? 0 : -1}
                        aria-label="Добавить ещё номер"
                        title="Ещё номер на эту точку"
                    >
                        <Plus size={15} />
                    </button>
                </div>
            ))}
        </div>
    );
};

const ParkPhones = ({ draft, setDraft, offices }) => {
    const points = draft.points || [];

    const used = useMemo(() => new Set(points.map((point) => (
        point.office_id === null ? ONLINE : point.office_id))), [points]);

    // Занята точка или нет, считаем без учёта самой строки: иначе выбранный
    // офис оказывался бы недоступен в собственном селекторе.
    const takenBy = (key) => (value) => used.has(value) && value !== key;

    const update = (key, patch) => setDraft((prev) => ({
        ...prev,
        points: prev.points.map((point) => (point.key === key ? { ...point, ...patch } : point)),
    }));

    const remove = (key) => setDraft((prev) => ({
        ...prev,
        points: prev.points.filter((point) => point.key !== key),
    }));

    const add = () => setDraft((prev) => ({
        ...prev,
        points: [...prev.points, emptyPoint(undefined)],
    }));

    const free = offices.length + 1 - used.size;

    return (
        <div className="space-y-2">
            {points.length === 0 && (
                <p className="rounded-xl bg-slate-50 px-3 py-2.5 text-[12.5px] leading-relaxed text-slate-500">
                    Номеров пока нет. Добавьте строку и выберите, где по номеру отвечают:
                    в офисе из справочника или «онлайн» — если у парка адреса нет.
                </p>
            )}

            {points.map((point) => (
                <PointRow
                    key={point.key}
                    point={point}
                    offices={offices}
                    taken={takenBy(point.office_id === null ? ONLINE : point.office_id)}
                    onChange={(patch) => update(point.key, patch)}
                    onRemove={() => remove(point.key)}
                />
            ))}

            {/* «Место», а не «номер»: кнопка заводит СТРОКУ (офис или онлайн), а
                второй номер той же точке добавляет плюс внутри строки. Пока
                она называлась «Добавить номер», в справочнике без офисов она
                гасла ровно там, где номер добавить как раз можно. */}
            <button
                type="button"
                className={`${iosBtnGhost} w-full justify-center border border-dashed border-slate-300 disabled:opacity-40`}
                onClick={add}
                disabled={free <= 0}
                title={free <= 0
                    ? 'Все офисы справочника уже в списке. Ещё один номер той же точке добавляет плюс в её строке'
                    : undefined}
            >
                <Plus size={14} /> Добавить офис или онлайн
            </button>
        </div>
    );
};

export default function ParkEditor({ draft, setDraft, offices }) {
    const set = (key) => (e) => setDraft((prev) => ({ ...prev, [key]: e.target.value }));
    const issue = parkDraftIssue(draft);
    const points = draft.points || [];

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
                        <Phone size={12} /> Номера и офисы
                    </div>
                    {points.length > 0 && (
                        <IosBadge tone="blue">точек: {points.length}</IosBadge>
                    )}
                </div>
                <ParkPhones draft={draft} setDraft={setDraft} offices={offices} />
                {issue && issue !== 'Укажите название парка' ? (
                    <p className="px-1 text-[11.5px] leading-relaxed text-amber-600">{issue}</p>
                ) : (
                    <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                        Строка — это место, куда звонят: офис из справочника или «онлайн»,
                        если у парка адреса нет. Плюс рядом с полем добавляет второй номер
                        той же точке. Адрес, карта и график живут в самом офисе — на
                        вкладке «Офисы».
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
                <Field label="Адрес" hint="Юридический или головной. Адреса, куда ходят водители, — в офисах.">
                    <input
                        className={iosInput}
                        value={draft.address}
                        placeholder="Алматы, улица Жамбыла, 172"
                        onChange={set('address')}
                    />
                </Field>
            </section>
        </div>
    );
}
