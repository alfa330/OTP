import React, { useMemo, useRef, useState } from 'react';
import {
    Building2, ImagePlus, Loader2, MapPin, Phone, Plus, StickyNote, Trash2,
} from 'lucide-react';
import { iosInput, iosGroupLabel, iosBtnGhost, IosBadge } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { Field, CitySelect } from './formField';
import {
    PHONE_DIGITS, digitsOf, emptyNumber, formatDigits, parkDraftIssue, toPhone,
} from './parkPoints';
import { LOGO_MAX_BYTES, LOGO_TYPES } from './parkLogo';

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
    const options = useMemo(() => {
        // Города в порядке появления: список офисов уже отсортирован сервером, и
        // пересортировка тут развела бы одинаковые списки в двух вкладках.
        // Офисы одного города складываем в одну группу, даже если в ответе они
        // идут вразбивку: иначе заголовок города повторился бы ниже по списку.
        const groups = [];
        offices.forEach((item) => {
            const city = item.city || 'Без города';
            const option = {
                value: item.id,
                label: `${item.name}${item.is_online ? ' · только по телефону' : ''}`,
                groupLabel: city,
            };
            const group = groups.find(([name]) => name === city);
            if (group) group[1].push(option);
            else groups.push([city, [option]]);
        });
        /* «Не выбрано» — первая строка списка, а не только подпись на кнопке:
           без неё выбранный офис нечем снять. Значение у неё пустая строка, а
           не null: выбранное ищется сравнением значений по строке, и null
           совпал бы не с пустотой. */
        return [{ value: '', label: placeholder }, ...groups.flatMap(([, items]) => items)];
    }, [offices, placeholder]);

    return (
        <CustomSelect
            variant="ios"
            /* h-10 на кнопке: селектор стоит в строке номера рядом с полем
               телефона той же высоты, и своя высота списка увела бы строку. */
            className={`min-w-0 [&>button]:h-10 ${className}`}
            value={value ?? ''}
            options={options}
            /* Значение приходит из опции как есть, а не строкой из события:
               id остаётся числом, и поиск офиса по === продолжает работать.
               Пустая строка обязана превратиться именно в null — по нему
               считается «номер только по телефону» (isOnline в parkPoints). */
            onChange={(next) => onChange(next === '' ? null : next)}
            placeholder={placeholder}
            /* Поиск включаем только на длинном справочнике: у системного списка
               была подсказка по набранным буквам, здесь её заменяет строка
               поиска. На коротком списке она была бы лишней деталью. */
            searchable={offices.length > 12}
            searchPlaceholder="Поиск по названию офиса…"
            ariaLabel="Офис"
        />
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

/* Логотип парка.
 *
 * Плитка, а не строка «выберите файл»: тем же квадратом логотип и показывается
 * — в рельсе витрины, в карточке справочника и в шапке страницы парка, — и
 * форма обязана показывать результат, а не обещание. Нажатие на саму плитку
 * открывает выбор файла: это первое, что человек пробует сделать с аватаркой.
 *
 * Файл уходит на сервер СРАЗУ, а в парк потом ложится один id. Иначе картинку
 * пришлось бы тащить в теле сохранения парка вместе с номерами и адресом —
 * то есть держать в форме файл и обрабатывать отказ загрузки как отказ всей
 * правки, хотя это разные события.
 */
const ParkLogo = ({ draft, setDraft, onUpload }) => {
    const inputRef = useRef(null);
    const [busy, setBusy] = useState(false);

    const pick = (file) => {
        if (!file) return;
        setBusy(true);
        Promise.resolve(onUpload(file))
            .then((result) => {
                if (!result) return;
                setDraft((prev) => ({ ...prev, logo_file_id: result.file_id,
                                      logo_url: result.url }));
            })
            .finally(() => {
                setBusy(false);
                // Сбрасываем значение поля: без этого повторный выбор ТОГО ЖЕ
                // файла (после «Убрать») не даёт события change вовсе.
                if (inputRef.current) inputRef.current.value = '';
            });
    };

    return (
        <div className="flex items-center gap-3">
            <input
                ref={inputRef}
                type="file"
                className="hidden"
                accept={LOGO_TYPES.join(',')}
                onChange={(e) => pick(e.target.files?.[0])}
            />

            <button
                type="button"
                onClick={() => inputRef.current?.click()}
                disabled={busy}
                className="group relative grid h-[68px] w-[68px] shrink-0 place-items-center overflow-hidden rounded-2xl bg-slate-100 text-slate-400 transition hover:bg-slate-200 active:scale-[0.98] disabled:opacity-60"
                aria-label={draft.logo_url ? 'Заменить логотип' : 'Загрузить логотип'}
            >
                {draft.logo_url && !busy && (
                    <img src={draft.logo_url} alt="" className="h-full w-full object-cover" />
                )}
                {busy && <Loader2 size={20} className="animate-spin text-slate-500" />}
                {!draft.logo_url && !busy && <Building2 size={22} />}
                {/* Подсказка «сюда можно нажать» появляется поверх готовой
                    картинки: без неё плитка с логотипом выглядит картинкой, а
                    не кнопкой. */}
                {draft.logo_url && !busy && (
                    <span className="absolute inset-0 grid place-items-center bg-slate-900/45 text-white opacity-0 transition group-hover:opacity-100">
                        <ImagePlus size={18} />
                    </span>
                )}
            </button>

            <div className="min-w-0">
                <div className="text-[13px] font-medium text-slate-900">Логотип</div>
                <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-400">
                    Виден на главной в рельсе парков и в карточке.
                    PNG, JPEG или WebP до {Math.round(LOGO_MAX_BYTES / (1024 * 1024))} МБ.
                </p>
                <div className="mt-1.5 flex flex-wrap items-center gap-1">
                    <button
                        type="button"
                        className={`${iosBtnGhost} px-2 py-1 text-[12px] text-blue-600 hover:bg-blue-50`}
                        onClick={() => inputRef.current?.click()}
                        disabled={busy}
                    >
                        <ImagePlus size={13} /> {draft.logo_url ? 'Заменить' : 'Загрузить'}
                    </button>
                    {draft.logo_url && (
                        <button
                            type="button"
                            className={`${iosBtnGhost} px-2 py-1 text-[12px] hover:bg-rose-50 hover:text-rose-500`}
                            onClick={() => setDraft((prev) => ({
                                ...prev, logo_file_id: null, logo_url: null,
                            }))}
                            disabled={busy}
                        >
                            <Trash2 size={13} /> Убрать
                        </button>
                    )}
                </div>
            </div>
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

export default function ParkEditor({ draft, setDraft, offices, onUploadLogo }) {
    const set = (key) => (e) => setDraft((prev) => ({ ...prev, [key]: e.target.value }));
    const issue = parkDraftIssue(draft);
    const numbers = draft.numbers || [];
    const headOffice = offices.find((office) => office.id === draft.head_office_id);

    return (
        <div className="space-y-5">
            <section className="space-y-3">
                <div className={iosGroupLabel}>О парке</div>
                {onUploadLogo && (
                    <ParkLogo draft={draft} setDraft={setDraft} onUpload={onUploadLogo} />
                )}
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
                        {/* Тот же перечень, что у офиса: город парка и город
                            его офиса читают рядом — в карточке парка и в
                            списке офисов, — и написаны они обязаны быть
                            одинаково. */}
                        <CitySelect
                            value={draft.city}
                            onChange={(city) => setDraft((prev) => ({ ...prev, city }))}
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
