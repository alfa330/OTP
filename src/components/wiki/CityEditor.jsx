import React, { useMemo, useRef, useState } from 'react';
import { Check, Eye, EyeOff, Plus, Search, Trash2 } from 'lucide-react';
import { iosBtnGhost, iosGroupLabel, iosInput } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { KAZAKHSTAN_CITY_OPTIONS } from '../../utils/kazakhstanCities';
import { Field } from './formField';

/* Редактор города — всё, чего нет на странице Яндекса.
 *
 * Тарифы Яндекса здесь только читаются (их кладёт сверка), а к каждому можно
 * дописать то, что Яндекс публично не показывает: комиссию и требования к
 * авто, — и спрятать тариф, который в этом городе не нужен. Свои тарифы —
 * для того, чего у Яндекса нет вовсе (у пространства Тез ссылки на Яндекс
 * может не быть совсем).
 *
 * На виду — обязательное и главное; свои тарифы, услуги парка и заметка
 * открываются кнопками, пока пусты (требование владельца к формам: не выкладывать все
 * поля сразу).
 */

/* Геометрия триггера CustomSelect под соседний iosInput — тот же приём, что у
   CitySelect в formField.jsx: разная высота полей в одной строке сразу видна. */
const selectTrigger = '[&>button]:bg-slate-100 [&>button]:px-3.5 [&>button]:py-2.5 '
    + '[&>button]:text-[14px] [&>button]:font-normal [&>button]:text-slate-900 '
    + '[&>button:hover]:bg-slate-200/70';

/* Без w-full намеренно: ширину каждому полю строки задаёт место — у комиссии
   узкое 72 px, у требований остаток строки. Общий w-full перебивал бы узкую
   ширину (у Tailwind выигрывает порядок в CSS, а не в атрибуте), и поле «%»
   растягивалось на всю строку. */
const compactInput = 'rounded-lg bg-white px-2.5 py-1.5 text-[13px] text-slate-900 '
    + 'ring-1 ring-slate-200 placeholder-slate-400 transition focus:outline-none '
    + 'focus:ring-2 focus:ring-blue-500/70';

const iconButton = 'grid h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-400 '
    + 'transition hover:bg-slate-100 hover:text-slate-700';

const chip = 'inline-flex items-center gap-1.5 rounded-xl bg-slate-100 px-3 py-1.5 text-[12.5px] '
    + 'font-medium text-slate-600 transition hover:bg-slate-200 active:scale-[0.98]';

const EMPTY_EXTRA = { name: '', commission: '', requirement: '', price: '' };
const EMPTY_SERVICE = { title: '', note: '' };

const Group = ({ title, hint, children, right = null }) => (
    <section className="space-y-2">
        <div className="flex items-end justify-between gap-2">
            <div className={iosGroupLabel}>{title}</div>
            {right}
        </div>
        {hint && <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">{hint}</p>}
        {children}
    </section>
);

/** Черновик формы из карточки города. */
export const draftFromCity = (city) => {
    const meta = {};
    Object.entries(city?.tariff_meta || {}).forEach(([code, entry]) => {
        meta[code] = {
            commission: entry.commission ?? '',
            requirement: entry.requirement || '',
            hidden: !!entry.hidden,
        };
    });
    return {
        id: city?.id || null,
        name: city?.name || '',
        yandex_url: city?.yandex_url || '',
        serving_office_id: city?.serving_office_id || '',
        driver_office_ids: [...(city?.driver_office_ids || [])],
        park_commission: city?.park_commission ?? '',
        tariff_meta: meta,
        extra_tariffs: (city?.extra_tariffs || []).map((item) => ({
            name: item.name || '',
            commission: item.commission ?? '',
            requirement: item.requirement || '',
            price: item.price || '',
        })),
        services: (city?.services || []).map((item) => ({ title: item.title || '', note: item.note || '' })),
        note: city?.note || '',
        yandexTariffs: (city?.yandex_data?.tariffs || []).map((tariff) => ({
            code: tariff.class, name: tariff.name, from: tariff.from,
        })),
        showExtra: (city?.extra_tariffs || []).length > 0,
        showServices: (city?.services || []).length > 0,
        showNote: !!city?.note,
    };
};

/** Тело запроса из черновика. Пустые строки уходят как null — сервер их чистит. */
export const payloadFromDraft = (draft) => ({
    name: draft.name.trim(),
    yandex_url: draft.yandex_url.trim() || null,
    serving_office_id: draft.serving_office_id || null,
    driver_office_ids: draft.driver_office_ids || [],
    park_commission: String(draft.park_commission ?? '').trim() || null,
    tariff_meta: Object.fromEntries(
        Object.entries(draft.tariff_meta || {}).map(([code, entry]) => [code, {
            commission: String(entry.commission ?? '').trim() || null,
            requirement: (entry.requirement || '').trim() || null,
            hidden: !!entry.hidden,
        }]),
    ),
    extra_tariffs: (draft.extra_tariffs || [])
        .filter((item) => item.name.trim())
        .map((item) => ({
            name: item.name.trim(),
            commission: String(item.commission ?? '').trim() || null,
            requirement: item.requirement.trim() || null,
            price: item.price.trim() || null,
        })),
    services: (draft.services || [])
        .filter((item) => item.title.trim())
        .map((item) => ({ title: item.title.trim(), note: item.note.trim() || null })),
    note: draft.note.trim() || null,
});

const fold = (value) => String(value || '').toLowerCase().replace(/ё/g, 'е').trim();

/* «Куда направлять водителя» — офисы, которые оператор увидит в карточке
 * города (просьба владельца 24.09.2026). Список всех офисов пространства с
 * отметками: офисы самого города — первой группой, остальные по городам.
 * Порядок отметок и есть порядок показа — номер у отмеченного говорит, кого
 * оператор увидит первым. Ничего не отмечено — карточка берёт офисы самого
 * города, как и раньше (cityRules.cityOffices). */
const DriverOffices = ({ offices, cityName, value, onChange }) => {
    const [query, setQuery] = useState('');
    const chosen = useMemo(() => new Set(value), [value]);
    /* Города, где офисы уже отмечены, — наверх, чтобы выбор был виден сразу
       при открытии. Считается ОДИН раз, по отметкам на момент открытия:
       пересчёт на каждое нажатие двигал бы группы под пальцем. */
    const initiallyChosen = useRef(new Set(value)).current;

    const groups = useMemo(() => {
        const collator = new Intl.Collator('ru');
        const own = fold(cityName);
        const needle = fold(query);
        const usable = (offices || []).filter((office) => (
            office.status === 'active' && !office.no_office
            && (!needle || [office.name, office.city, office.address].some((text) => fold(text).includes(needle)))
        ));
        const byCity = new Map();
        usable.forEach((office) => {
            const key = office.city || 'Без города';
            if (!byCity.has(key)) byCity.set(key, []);
            byCity.get(key).push(office);
        });
        return [...byCity.entries()]
            .map(([city, items]) => ({
                city,
                own: fold(city) === own,
                picked: items.some((office) => initiallyChosen.has(office.id)),
                items: items.sort((a, b) => collator.compare(a.name || '', b.name || '')),
            }))
            .sort((a, b) => (b.own - a.own) || (b.picked - a.picked)
                || collator.compare(a.city, b.city));
    }, [offices, cityName, query, initiallyChosen]);

    const total = (offices || []).filter((office) => office.status === 'active' && !office.no_office).length;
    const toggle = (id) => onChange(chosen.has(id) ? value.filter((item) => item !== id) : [...value, id]);

    return (
        <Group
            title="Куда направлять водителя"
            right={value.length > 0 ? (
                <button type="button" className={`${iosBtnGhost} !py-1`} onClick={() => onChange([])}>
                    Сбросить · {value.length}
                </button>
            ) : null}
            hint={value.length
                ? 'Оператор увидит отмеченные офисы в этом порядке — номер показывает, какой первым.'
                : 'Отметьте офисы, которые оператор увидит в карточке города. Ничего не отмечено — показываются офисы самого города.'}
        >
            {total === 0 ? (
                <div className="rounded-2xl bg-slate-50 px-4 py-3 text-[13px] text-slate-500 ring-1 ring-slate-200/70">
                    В справочнике «Офисы» этого пространства пока нет офисов.
                </div>
            ) : (
                <div className="overflow-hidden rounded-2xl bg-slate-50 ring-1 ring-slate-200/70">
                    {total > 6 && (
                        <div className="relative border-b border-slate-200/70 bg-white/70 p-2">
                            <Search size={14} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input
                                className="w-full rounded-lg bg-slate-100 py-1.5 pl-8 pr-2.5 text-[13px] text-slate-900 placeholder-slate-400 transition focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70"
                                value={query}
                                onChange={(e) => setQuery(e.target.value)}
                                placeholder="Поиск офиса или города"
                                aria-label="Поиск офиса"
                            />
                        </div>
                    )}
                    <div className="max-h-72 overflow-y-auto overscroll-contain thin-scroll">
                        {groups.length === 0 && (
                            <div className="px-4 py-3 text-[13px] text-slate-500">Ничего не найдено</div>
                        )}
                        {groups.map((group) => (
                            <div key={group.city}>
                                <div className="sticky top-0 z-[1] bg-slate-50/95 px-3.5 pb-1 pt-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500 backdrop-blur">
                                    {group.city}{group.own ? ' · этот город' : ''}
                                </div>
                                {group.items.map((office) => {
                                    const checked = chosen.has(office.id);
                                    const order = value.indexOf(office.id) + 1;
                                    return (
                                        <button
                                            key={office.id}
                                            type="button"
                                            role="checkbox"
                                            aria-checked={checked}
                                            onClick={() => toggle(office.id)}
                                            className="flex w-full items-center gap-3 px-3.5 py-2 text-left transition hover:bg-slate-100/80 active:bg-slate-100"
                                        >
                                            <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-full transition ${
                                                checked ? 'bg-blue-600 text-white' : 'bg-white ring-[1.5px] ring-inset ring-slate-300'
                                            }`}
                                            >
                                                {checked && <Check size={12} strokeWidth={3} />}
                                            </span>
                                            <span className="min-w-0 flex-1">
                                                <span className="block truncate text-[13.5px] font-medium text-slate-900">
                                                    {office.name}
                                                </span>
                                                {office.address && (
                                                    <span className="block truncate text-[12px] text-slate-500">{office.address}</span>
                                                )}
                                            </span>
                                            {office.kind === 'partner' && (
                                                <span className="shrink-0 text-[11.5px] text-slate-400">
                                                    {office.partner_label || 'партнёр'}
                                                </span>
                                            )}
                                            {checked && value.length > 1 && (
                                                <span className="grid h-5 min-w-5 shrink-0 place-items-center rounded-full bg-blue-50 px-1.5 text-[11px] font-semibold tabular-nums text-blue-700">
                                                    {order}
                                                </span>
                                            )}
                                        </button>
                                    );
                                })}
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </Group>
    );
};

export default function CityEditor({ draft, setDraft, offices = [], takenNames = [] }) {
    const patch = (fields) => setDraft((current) => ({ ...current, ...fields }));

    /* Город — из общего справочника с областями (тот же, что в карточке
       сотрудника): у карточки города есть область и точка на схеме, и
       «Экбастуз» с опечаткой не нашёл бы ни того, ни другого. Уже заведённые
       города недоступны — второй карточки того же города быть не должно. */
    const cityOptions = useMemo(() => {
        const taken = new Set(takenNames.map((name) => name.toLowerCase()));
        const current = draft.name.trim();
        const known = KAZAKHSTAN_CITY_OPTIONS.some((option) => option.value === current);
        const options = KAZAKHSTAN_CITY_OPTIONS.map((option) => ({
            ...option,
            disabled: taken.has(option.value.toLowerCase())
                && option.value.toLowerCase() !== current.toLowerCase(),
        }));
        return current && !known
            ? [{ value: current, label: current, groupLabel: 'Сейчас в карточке' }, ...options]
            : options;
    }, [takenNames, draft.name]);

    /* Офисы — своего пространства (список пришёл той же выборкой, что во
       вкладке «Офисы»), по городам. Партнёрские точки и записи «офиса нет»
       водителя не принимают — их в выборе нет. */
    const officeOptions = useMemo(() => {
        const collator = new Intl.Collator('ru');
        const usable = offices
            .filter((office) => office.status === 'active' && !office.no_office && office.kind !== 'partner')
            .sort((a, b) => collator.compare(a.city || '', b.city || '')
                || collator.compare(a.name || '', b.name || ''));
        return [
            { value: '', label: 'Не выбрана' },
            ...usable.map((office) => ({
                value: office.id,
                label: office.address ? `${office.name} — ${office.address}` : office.name,
                groupLabel: office.city || 'Без города',
            })),
        ];
    }, [offices]);

    const setMeta = (code, fields) => setDraft((current) => ({
        ...current,
        tariff_meta: {
            ...current.tariff_meta,
            [code]: { commission: '', requirement: '', hidden: false,
                      ...(current.tariff_meta[code] || {}), ...fields },
        },
    }));

    const setListItem = (key, index, fields) => setDraft((current) => ({
        ...current,
        [key]: current[key].map((item, i) => (i === index ? { ...item, ...fields } : item)),
    }));
    const addListItem = (key, empty) => setDraft((current) => ({
        ...current, [key]: [...current[key], { ...empty }],
    }));
    const removeListItem = (key, index) => setDraft((current) => ({
        ...current, [key]: current[key].filter((_, i) => i !== index),
    }));

    return (
        <div className="space-y-6">
            <Group title="Город">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_160px]">
                    <Field label="Город">
                        <CustomSelect
                            variant="ios"
                            className={`min-w-0 ${selectTrigger}`}
                            value={draft.name}
                            onChange={(value) => patch({ name: value })}
                            options={cityOptions}
                            placeholder="Выберите город"
                            searchable
                            searchPlaceholder="Поиск по городу…"
                            ariaLabel="Город"
                        />
                    </Field>
                    <Field label="Комиссия парка, %">
                        <input
                            className={`${iosInput} tabular-nums`}
                            inputMode="decimal"
                            value={draft.park_commission}
                            onChange={(e) => patch({ park_commission: e.target.value })}
                            placeholder="Например, 4"
                        />
                    </Field>
                </div>
                <Field
                    label="Ссылка на тарифы Яндекс Go"
                    hint="Тарифы, цены заказа и номер для заказа по телефону подтянутся сами и будут обновляться каждую ночь."
                >
                    <input
                        className={iosInput}
                        value={draft.yandex_url}
                        onChange={(e) => patch({ yandex_url: e.target.value })}
                        placeholder="https://taxi.yandex.kz/ru_kz/almaty/tariff"
                        inputMode="url"
                        autoComplete="off"
                        spellCheck={false}
                    />
                </Field>
                {/* Зона — это другой вопрос, чем «куда направлять водителя»:
                    она красит город на схеме и пишет «Обслуживает офис …».
                    Офисы для оператора выбираются внизу формы. */}
                <Field
                    label="Зона — офис, который обслуживает город"
                    hint="Задаёт цвет зоны на карте. Офисы, куда направлять водителя, — внизу формы."
                >
                    <CustomSelect
                        variant="ios"
                        className={`min-w-0 ${selectTrigger}`}
                        value={draft.serving_office_id}
                        onChange={(value) => patch({ serving_office_id: value })}
                        options={officeOptions}
                        placeholder="Не выбрана"
                        searchable
                        searchPlaceholder="Поиск по офису…"
                        ariaLabel="Обслуживающий офис"
                    />
                </Field>
            </Group>

            <Group
                title="Тарифы Яндекса"
                hint={draft.yandexTariffs.length
                    ? 'Комиссию и требования к авто («Авто от 2007 года») Яндекс публично не показывает — впишите их здесь. Скрытый тариф в карточке не показывается.'
                    : 'Тарифы появятся здесь после сохранения ссылки на Яндекс.'}
            >
                {draft.yandexTariffs.length > 0 && (
                    <div className="overflow-hidden rounded-2xl bg-slate-50 ring-1 ring-slate-200/70 divide-y divide-slate-200/70">
                        {draft.yandexTariffs.map((tariff) => {
                            const meta = draft.tariff_meta[tariff.code] || {};
                            const hidden = !!meta.hidden;
                            return (
                                <div key={tariff.code} className={`flex flex-wrap items-center gap-2 px-3 py-2 ${hidden ? 'opacity-55' : ''}`}>
                                    <div className="w-full min-w-0 sm:w-36">
                                        <div className="truncate text-[13.5px] font-medium text-slate-900">{tariff.name}</div>
                                        {tariff.from && (
                                            <div className="text-[11.5px] tabular-nums text-slate-400">от {tariff.from}</div>
                                        )}
                                    </div>
                                    <input
                                        className={`${compactInput} w-[72px] tabular-nums`}
                                        inputMode="decimal"
                                        value={meta.commission ?? ''}
                                        onChange={(e) => setMeta(tariff.code, { commission: e.target.value })}
                                        placeholder="%"
                                        aria-label={`Комиссия тарифа ${tariff.name}, %`}
                                    />
                                    <input
                                        className={`${compactInput} min-w-0 flex-1`}
                                        value={meta.requirement || ''}
                                        onChange={(e) => setMeta(tariff.code, { requirement: e.target.value })}
                                        placeholder="Требования к авто"
                                        aria-label={`Требования к авто, тариф ${tariff.name}`}
                                    />
                                    <button
                                        type="button"
                                        className={iconButton}
                                        onClick={() => setMeta(tariff.code, { hidden: !hidden })}
                                        title={hidden ? 'Показывать в карточке' : 'Скрыть из карточки'}
                                        aria-label={hidden ? `Показывать тариф ${tariff.name}` : `Скрыть тариф ${tariff.name}`}
                                        aria-pressed={hidden}
                                    >
                                        {hidden ? <EyeOff size={15} /> : <Eye size={15} />}
                                    </button>
                                </div>
                            );
                        })}
                    </div>
                )}
            </Group>

            {draft.showExtra && (
            <Group
                title="Свои тарифы"
                hint="Тарифы, которых нет у Яндекса, — например, свои тарифы парка."
                right={(
                    <button type="button" className={`${iosBtnGhost} !py-1`} onClick={() => addListItem('extra_tariffs', EMPTY_EXTRA)}>
                        <Plus size={14} /> Тариф
                    </button>
                )}
            >
                    <div className="space-y-2">
                        {draft.extra_tariffs.map((item, index) => (
                            // eslint-disable-next-line react/no-array-index-key
                            <div key={index} className="flex flex-wrap items-center gap-2 rounded-2xl bg-slate-50 p-2.5 ring-1 ring-slate-200/70">
                                <input
                                    className={`${compactInput} min-w-0 flex-1 sm:max-w-[180px]`}
                                    value={item.name}
                                    onChange={(e) => setListItem('extra_tariffs', index, { name: e.target.value })}
                                    placeholder="Название"
                                    aria-label="Название тарифа"
                                />
                                <input
                                    className={`${compactInput} w-[72px] tabular-nums`}
                                    inputMode="decimal"
                                    value={item.commission}
                                    onChange={(e) => setListItem('extra_tariffs', index, { commission: e.target.value })}
                                    placeholder="%"
                                    aria-label="Комиссия, %"
                                />
                                <input
                                    className={`${compactInput} w-[120px]`}
                                    value={item.price}
                                    onChange={(e) => setListItem('extra_tariffs', index, { price: e.target.value })}
                                    placeholder="Цена, «от 400 ₸»"
                                    aria-label="Цена"
                                />
                                <input
                                    className={`${compactInput} min-w-[160px] flex-1`}
                                    value={item.requirement}
                                    onChange={(e) => setListItem('extra_tariffs', index, { requirement: e.target.value })}
                                    placeholder="Требования к авто"
                                    aria-label="Требования к авто"
                                />
                                <button type="button" className={iconButton}
                                        onClick={() => removeListItem('extra_tariffs', index)}
                                        aria-label="Убрать тариф">
                                    <Trash2 size={15} />
                                </button>
                            </div>
                        ))}
                    </div>
            </Group>
            )}

            {draft.showServices && (
                <Group
                    title="Услуги нашего парка"
                    right={(
                        <button type="button" className={`${iosBtnGhost} !py-1`} onClick={() => addListItem('services', EMPTY_SERVICE)}>
                            <Plus size={14} /> Услуга
                        </button>
                    )}
                >
                    <div className="space-y-2">
                        {draft.services.map((item, index) => (
                            // eslint-disable-next-line react/no-array-index-key
                            <div key={index} className="flex flex-wrap items-center gap-2 rounded-2xl bg-slate-50 p-2.5 ring-1 ring-slate-200/70">
                                <input
                                    className={`${compactInput} min-w-0 flex-1 sm:max-w-[220px]`}
                                    value={item.title}
                                    onChange={(e) => setListItem('services', index, { title: e.target.value })}
                                    placeholder="Услуга, например «Аренда авто»"
                                    aria-label="Услуга"
                                />
                                <input
                                    className={`${compactInput} min-w-[160px] flex-1`}
                                    value={item.note}
                                    onChange={(e) => setListItem('services', index, { note: e.target.value })}
                                    placeholder="Подробность, «от 9 000 ₸ в сутки»"
                                    aria-label="Подробность"
                                />
                                <button type="button" className={iconButton}
                                        onClick={() => removeListItem('services', index)}
                                        aria-label="Убрать услугу">
                                    <Trash2 size={15} />
                                </button>
                            </div>
                        ))}
                    </div>
                </Group>
            )}

            {draft.showNote && (
                <Group title="Заметка">
                    <textarea
                        className={`${iosInput} min-h-[88px] resize-y leading-relaxed`}
                        value={draft.note}
                        onChange={(e) => patch({ note: e.target.value })}
                        placeholder="Что ещё важно знать о заказах в этом городе"
                    />
                </Group>
            )}

            {/* Незаполненные необязательные блоки — кнопками, а не пустыми
                полями на виду. */}
            {(!draft.showExtra || !draft.showServices || !draft.showNote) && (
                <div className="flex flex-wrap gap-2">
                    {!draft.showExtra && (
                        <button
                            type="button"
                            className={chip}
                            onClick={() => setDraft((current) => ({
                                ...current,
                                showExtra: true,
                                extra_tariffs: current.extra_tariffs.length
                                    ? current.extra_tariffs : [{ ...EMPTY_EXTRA }],
                            }))}
                        >
                            <Plus size={13} /> Свой тариф
                        </button>
                    )}
                    {!draft.showServices && (
                        <button
                            type="button"
                            className={chip}
                            onClick={() => setDraft((current) => ({
                                ...current,
                                showServices: true,
                                services: current.services.length ? current.services : [{ ...EMPTY_SERVICE }],
                            }))}
                        >
                            <Plus size={13} /> Услуги парка
                        </button>
                    )}
                    {!draft.showNote && (
                        <button
                            type="button"
                            className={chip}
                            onClick={() => patch({ showNote: true })}
                        >
                            <Plus size={13} /> Заметка
                        </button>
                    )}
                </div>
            )}

            <DriverOffices
                offices={offices}
                cityName={draft.name}
                value={draft.driver_office_ids || []}
                onChange={(ids) => patch({ driver_office_ids: ids })}
            />
        </div>
    );
}
