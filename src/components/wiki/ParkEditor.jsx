import React, { useMemo } from 'react';
import { ChevronDown, MapPin, Phone, Plus, Trash2, Wifi, X } from 'lucide-react';
import { iosInput, iosGroupLabel, iosBtnGhost, IosBadge, IosToggle } from '../ui/ios';
import { Field } from './formField';
import { ONLINE, emptyPoint, isOnline, parkDraftIssue } from './parkPoints';

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
const OfficeSelect = ({ value, offices, onChange, placeholder, disabledId, invalid = false }) => {
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
        <div className="relative min-w-0 flex-1">
            <select
                className={`${iosInput} h-10 appearance-none py-0 pr-9 ${
                    invalid ? 'ring-2 ring-amber-400/80' : ''
                }`}
                value={value ?? ''}
                onChange={(e) => onChange(e.target.value === '' ? undefined : Number(e.target.value))}
            >
                <option value="">{offices.length ? placeholder : 'Офисов в справочнике нет'}</option>
                {byCity.map(([city, items]) => (
                    <optgroup key={city} label={city}>
                        {items.map((item) => (
                            <option key={item.id} value={item.id} disabled={disabledId?.(item.id)}>
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

const PointRow = ({ point, offices, taken, onChange, onRemove, canRemove }) => {
    const office = offices.find((item) => item.id === point.office_id);
    const online = isOnline(point);
    const unset = point.office_id === undefined;
    // Онлайн-строка у парка одна: все номера без офиса лежат в одной пачке
    // (wiki_park_phones с office_id = NULL), второй такой строке некуда деться.
    const onlineTaken = !online && taken(ONLINE);

    const setPhone = (index, value) => onChange({
        phones: point.phones.map((phone, position) => (position === index ? value : phone)),
    });

    const addPhone = () => onChange({ phones: [...point.phones, ''] });

    const dropPhone = (index) => onChange({
        phones: point.phones.filter((phone, position) => position !== index),
    });

    return (
        <div className="space-y-1.5 rounded-xl border border-slate-200 bg-white p-2.5">
            <div className="flex items-center gap-2">
                {/* Онлайн — переключатель, а не пункт в списке офисов: это не
                    ещё один адрес, а его отсутствие, и в одном перечне с
                    «Алматы Навигатор» он читался как офис с таким названием. */}
                {online ? (
                    <div className="flex h-10 min-w-0 flex-1 items-center gap-1.5 rounded-xl bg-blue-50 px-3 text-[13px] font-medium text-blue-700">
                        <Wifi size={13} className="shrink-0" />
                        <span className="truncate">Без офиса — принимают только по телефону</span>
                    </div>
                ) : (
                    <OfficeSelect
                        value={point.office_id}
                        offices={offices}
                        placeholder="Выберите офис"
                        invalid={unset}
                        disabledId={taken}
                        onChange={(office_id) => onChange({ office_id })}
                    />
                )}

                <div
                    className={`flex shrink-0 items-center gap-1.5 ${onlineTaken ? 'opacity-40' : ''}`}
                    title={onlineTaken
                        ? 'Строка без офиса уже есть — все номера без адреса собираются в ней'
                        : 'Номер без офиса: парк принимает только по телефону'}
                >
                    <span className="text-[12.5px] text-slate-500">Онлайн</span>
                    <IosToggle
                        checked={online}
                        disabled={onlineTaken}
                        onChange={(next) => onChange({ office_id: next ? null : undefined })}
                    />
                </div>

                {canRemove && (
                    <button
                        type="button"
                        onClick={onRemove}
                        className="grid h-9 w-9 shrink-0 place-items-center rounded-xl text-slate-400 transition hover:bg-rose-50 hover:text-rose-500"
                        aria-label="Убрать строку"
                        title="Убрать строку целиком"
                    >
                        <Trash2 size={15} />
                    </button>
                )}
            </div>

            {/* Адрес под селектором: названия офисов в справочнике похожи
                («Алматы Навигатор» и «Алматы Навигатор 2»), и адрес — это
                единственная подпись, по которой видно, что выбран тот. */}
            {!online && office?.address && (
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

    // Считаем по числу строк, а не по занятым местам: две строки без выбранного
    // офиса — это два undefined, и в Set они схлопываются в одну. По used.size
    // кнопка оставалась бы живой, а строки уводили бы в тупик, где в селекторе
    // всё занято. Мест ровно столько, сколько офисов, плюс одно «без офиса».
    const free = offices.length + 1 - points.length;

    return (
        <div className="space-y-2">
            {/* Строка есть всегда: номер обязателен, и пустая секция с одной
                кнопкой об этом молчала — выглядела как «заполнять нечего».
                Поэтому же у единственной строки нет корзины. */}
            {points.map((point) => (
                <PointRow
                    key={point.key}
                    point={point}
                    offices={offices}
                    taken={takenBy(point.office_id === null ? ONLINE : point.office_id)}
                    onChange={(patch) => update(point.key, patch)}
                    onRemove={() => remove(point.key)}
                    canRemove={points.length > 1}
                />
            ))}

            <button
                type="button"
                className={`${iosBtnGhost} w-full justify-center border border-dashed border-slate-300 disabled:opacity-40`}
                onClick={add}
                disabled={free <= 0}
                title={free <= 0
                    ? 'Мест больше нет: все офисы справочника уже в списке, строка без офиса тоже. '
                      + 'Ещё один номер той же точке добавляет плюс в её строке'
                    : 'Номер в другом офисе или без офиса'}
            >
                <Plus size={14} /> Ещё номер в другом месте
            </button>
        </div>
    );
};

export default function ParkEditor({ draft, setDraft, offices }) {
    const set = (key) => (e) => setDraft((prev) => ({ ...prev, [key]: e.target.value }));
    const issue = parkDraftIssue(draft);
    const points = draft.points || [];
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
                        В строке выбирается офис, куда звонят по этому номеру, либо
                        включается «Онлайн» — если у парка адреса нет и принимают только
                        по телефону. Плюс рядом с полем добавляет этой же точке второй
                        номер. Адрес, карта и график живут в самом офисе — на вкладке
                        «Офисы».
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
