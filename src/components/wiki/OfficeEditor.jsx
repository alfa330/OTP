import React, { useState } from 'react';
import axios from 'axios';
import { Loader2, MapPin, MapPinOff, Phone, Utensils } from 'lucide-react';
import { iosInput, iosBtnSecondary, IosBadge, IosToggle } from '../ui/ios';
import IosTimePicker from '../ui/TimePicker';
import OfficeMap from './OfficeMap';
import {
    DAY_CODES, DAY_LABELS, DEFAULT_BREAK,
    breakLines, buildSchedule, hasBreaks, hasSchedule, setBreaks,
} from './officeSchedule';
import { Field, CitySelect } from './formField';

/* Форма офиса: адрес, карта и график.
 *
 * Привязки к таксопаркам здесь НЕТ намеренно: связью управляют из карточки
 * парка (WikiParks), где заодно задаётся телефон этого парка в этом офисе.
 * Офис отвечает за место — адрес, точку на карте, часы работы; кому он
 * принадлежит, решает парк.
 *
 * Вынесена из WikiOffices отдельным файлом — в ней два самостоятельных
 * редактора (карта и неделя), и вместе со списком получился бы файл, в
 * котором не найти ни того ни другого.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const WORKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri'];

/* Пресеты собраны по боевым данным: в справочнике офисов часы повторяются
 * горсткой сочетаний. Пн–Пт 09:00–18:00 добавлен по просьбе владельца
 * 25.08.2026 — офисы с таким днём заводили руками по всей неделе.
 *
 * Обеда в пресете нет: он приезжает из тумблера рядом, потому что офис без
 * обеда — такой же обычный случай, как офис с обедом 13:00–14:00. Ручная
 * неделя остаётся ниже — она нужна Костанаю и Караганде, где суббота короткая. */
const PRESETS = [
    { label: 'Пн–Пт 09:00–19:00', days: WORKDAYS, from: '09:00', to: '19:00' },
    { label: 'Пн–Пт 09:00–18:00', days: WORKDAYS, from: '09:00', to: '18:00' },
    { label: 'Пн–Пт 10:00–19:00', days: WORKDAYS, from: '10:00', to: '19:00' },
    { label: 'Пн–Сб 09:00–19:00', days: [...WORKDAYS, 'sat'], from: '09:00', to: '19:00' },
    { label: 'Пн–Вс 09:00–19:00', days: DAY_CODES, from: '09:00', to: '19:00' },
];

/* Поле времени расписания. Снаружи сигнатура прежняя, внутри — пикер раздела
 * вместо `<input type="time">`: системное поле рисовал браузер, и рядом с
 * карточками формы оно читалось как деталь из другой программы. Габариты
 * (h-9, ширина 104px, цифры по центру) у примитива те же по умолчанию,
 * поэтому inputClassName здесь не переопределяем — сетка недели не поедет.
 *
 * min/max не задаём намеренно: закрытие раньше открытия модель понимает как
 * смену через полночь (officeSchedule.dayInterval), и офис 20:00–04:00 обязан
 * остаться набираемым.
 *
 * allowEmpty по умолчанию выключен: день считается рабочим, пока заполнены
 * `from` и `to`, — стёртое поле часов схлопнуло бы строку в «выходной» прямо
 * под пальцами у того, кто всего лишь чистил поле перед перенабором. У обеда
 * пусто — законное значение (день без обеда), там очистку включаем.
 *
 * shrink-0 на обёртке: у поля теперь есть шеврон справа, и сжатие в тесной
 * строке наехало бы им на цифры. Пусть строка переносится, как и раньше.
 *
 * ariaLabel обязателен: в строке дня четыре одинаковых текстовых поля, и без
 * подписи скринридер читает их как четыре безымянных ввода — системное поле
 * хотя бы называло себя временем.
 */
const TimeInput = ({ value, onChange, disabled, allowEmpty = false, defaultTime, ariaLabel }) => (
    <IosTimePicker
        value={value}
        onChange={onChange}
        disabled={disabled}
        allowEmpty={allowEmpty}
        defaultTime={defaultTime}
        ariaLabel={ariaLabel}
        className="shrink-0"
    />
);

function ScheduleEditor({ schedule, onChange }) {
    /* Обед — один тумблер на всю неделю: стирать его в десяти полях там, где
       обеда нет, дольше, чем заполнить сам график.

       Состояние тумблера читается из НЕДЕЛИ, пока в ней есть рабочие дни, —
       иначе после ручной правки полей он показывал бы своё, а не то, что
       сохранится. Отдельная память нужна ровно для пустого графика: снимать
       там нечего, а выбор обязан дожить до нажатия пресета. */
    const [breakPref, setBreakPref] = useState(true);
    const breakOn = hasSchedule(schedule) ? hasBreaks(schedule) : breakPref;

    /* Подпись тумблера — та же свёртка, что в карточке офиса: одинаковый во всей
       неделе обед показывается временем, разный — словом. */
    const breakRuns = breakLines(schedule);
    const breakNote = !breakOn ? 'без обеда'
        : breakRuns.length === 1 && !breakRuns[0].days ? breakRuns[0].time
            : breakRuns.length ? 'по дням'
                : `${DEFAULT_BREAK.from}–${DEFAULT_BREAK.to}`;

    // Новый день заводится с обедом ровно тогда, когда обед включён: иначе
    // включённая суббота возвращала бы в неделю только что снятый обед.
    const newDay = breakOn
        ? { from: '09:00', to: '19:00', break_from: DEFAULT_BREAK.from, break_to: DEFAULT_BREAK.to }
        : { from: '09:00', to: '19:00' };

    const setDay = (code, patch) => {
        const current = schedule[code] || {};
        onChange({ ...schedule, [code]: patch === null ? null : { ...current, ...patch } });
    };

    return (
        <div className="space-y-2">
            <div className="flex flex-wrap gap-1.5">
                {PRESETS.map((preset) => (
                    <button
                        key={preset.label}
                        type="button"
                        onClick={() => onChange(buildSchedule({
                            ...preset,
                            breakFrom: breakOn ? DEFAULT_BREAK.from : null,
                            breakTo: breakOn ? DEFAULT_BREAK.to : null,
                        }))}
                        className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11.5px] font-medium text-slate-600 transition hover:border-indigo-300 hover:text-indigo-600"
                    >
                        {preset.label}
                    </button>
                ))}
                <button
                    type="button"
                    onClick={() => onChange({})}
                    className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11.5px] font-medium text-slate-400 transition hover:border-rose-200 hover:text-rose-500"
                >
                    Очистить
                </button>
            </div>

            {/* Тумблер стоит НАД неделей, а не в строке дня: он про весь график
                сразу, а обед одного дня по-прежнему правится или стирается
                своими полями ниже. */}
            <label className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-2">
                <span className="flex items-center gap-2 text-[13px] text-slate-700">
                    <Utensils size={15} className="text-slate-400" />
                    Обед
                    <IosBadge tone="slate">{breakNote}</IosBadge>
                </span>
                <IosToggle
                    checked={breakOn}
                    onChange={(next) => { setBreakPref(next); onChange(setBreaks(schedule, next)); }}
                />
            </label>

            <div className="divide-y divide-slate-100 rounded-xl border border-slate-200">
                {DAY_CODES.map((code) => {
                    const day = schedule[code];
                    const isOpen = !!(day && day.from && day.to);
                    return (
                        <div key={code} className="flex flex-wrap items-center gap-2 px-3 py-2">
                            <span className="w-7 shrink-0 text-[13px] font-medium text-slate-700">
                                {DAY_LABELS[code]}
                            </span>
                            <IosToggle
                                checked={isOpen}
                                onChange={(next) => setDay(code, next ? newDay : null)}
                            />
                            {isOpen ? (
                                <>
                                    <TimeInput
                                        value={day.from}
                                        ariaLabel={`${DAY_LABELS[code]}, открытие`}
                                        onChange={(v) => setDay(code, { from: v })}
                                    />
                                    <span className="text-slate-300">–</span>
                                    <TimeInput
                                        value={day.to}
                                        ariaLabel={`${DAY_LABELS[code]}, закрытие`}
                                        onChange={(v) => setDay(code, { to: v })}
                                    />
                                    <span className="ml-1 text-[11.5px] text-slate-400">обед</span>
                                    {/* Обед разрешено стирать — так и задаётся день без обеда.
                                        Пустому полю стрелка подставляет свой конец обеда, а не
                                        рабочее утро примитива. */}
                                    <TimeInput
                                        value={day.break_from}
                                        allowEmpty
                                        defaultTime={DEFAULT_BREAK.from}
                                        ariaLabel={`${DAY_LABELS[code]}, начало обеда`}
                                        onChange={(v) => setDay(code, { break_from: v })}
                                    />
                                    <span className="text-slate-300">–</span>
                                    <TimeInput
                                        value={day.break_to}
                                        allowEmpty
                                        defaultTime={DEFAULT_BREAK.to}
                                        ariaLabel={`${DAY_LABELS[code]}, конец обеда`}
                                        onChange={(v) => setDay(code, { break_to: v })}
                                    />
                                </>
                            ) : (
                                <span className="text-[12.5px] text-slate-400">выходной</span>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

function MapField({ draft, setDraft, base, headers, showToast }) {
    const [checking, setChecking] = useState(false);

    const resolve = () => {
        const url = (draft.map_url || '').trim();
        if (!url) return;
        setChecking(true);
        axios.post(`${base}/offices/resolve-map`, { url }, { headers })
            .then((r) => {
                setDraft((prev) => ({
                    ...prev,
                    lat: r.data.lat,
                    lon: r.data.lon,
                    map_resolved_url: r.data.resolved_url || url,
                }));
                showToast('Точка найдена', 'success');
            })
            .catch((e) => {
                setDraft((prev) => ({ ...prev, lat: null, lon: null }));
                showToast(errText(e, 'Не удалось разобрать ссылку'), 'error');
            })
            .finally(() => setChecking(false));
    };

    return (
        <Field
            label="Ссылка 2ГИС"
            hint="Короткая ссылка go.2gis.com подходит — развернём её и покажем точку на карте."
        >
            <div className="flex gap-2">
                <input
                    className={iosInput}
                    value={draft.map_url || ''}
                    placeholder="https://go.2gis.com/xrzn2"
                    onChange={(e) => setDraft((prev) => ({
                        // Точка относится к прежней ссылке — сбрасываем вместе с ней,
                        // иначе карта показывала бы один офис, а клик открывал другой.
                        ...prev, map_url: e.target.value, lat: null, lon: null, map_resolved_url: null,
                    }))}
                />
                <button
                    type="button"
                    className={iosBtnSecondary}
                    disabled={checking || !(draft.map_url || '').trim()}
                    onClick={resolve}
                >
                    {checking ? <Loader2 size={14} className="animate-spin" /> : <MapPin size={14} />}
                    Проверить
                </button>
            </div>

            {draft.lat != null && draft.lon != null && (
                <div className="mt-2">
                    <OfficeMap base={base} lat={draft.lat} lon={draft.lon} height={140} />
                    <div className="mt-1 px-1 text-[11.5px] tabular-nums text-slate-400">
                        {draft.lat.toFixed(6)}, {draft.lon.toFixed(6)}
                    </div>
                </div>
            )}
        </Field>
    );
}

export default function OfficeEditor({ draft, setDraft, base, headers, showToast }) {
    return (
        <div className="space-y-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Название">
                    <input
                        className={iosInput}
                        autoFocus
                        value={draft.name}
                        placeholder="Алматы Навигатор"
                        onChange={(e) => setDraft((prev) => ({ ...prev, name: e.target.value }))}
                    />
                </Field>
                <Field label="Город">
                    {/* Выбор, а не строка: группы в списке офисов бьются по
                        строке города, и «Астана» с «Нур-Султаном» разъехались
                        бы в два города. Перечень — города присутствия, общий с
                        карточкой парка. */}
                    <CitySelect
                        value={draft.city}
                        onChange={(city) => setDraft((prev) => ({ ...prev, city }))}
                    />
                </Field>
            </div>

            {/* «Офиса в городе нет» — такая же запись справочника, как офис:
                иначе город, где офиса нет, виден только тому, кто и так это
                знает. Всё остальное у неё гасится — адреса и графика у
                отсутствующего офиса не бывает. */}
            <label className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-2">
                <span className="flex items-center gap-2 text-[13px] text-slate-700">
                    <MapPinOff size={15} className="text-slate-400" />
                    Офиса в городе нет
                    <IosBadge tone="slate">строка о городе</IosBadge>
                </span>
                <IosToggle
                    checked={!!draft.no_office}
                    onChange={(next) => setDraft((prev) => ({ ...prev, no_office: next }))}
                />
            </label>

            {draft.no_office ? (
                <p className="px-1 text-[12px] leading-relaxed text-slate-500">
                    В списке город получит статус «Офиса в городе нет» вместо адреса.
                    Телефон, карта и график для такой записи не нужны.
                </p>
            ) : (
                <>
                    <Field label="Адрес">
                        <input
                            className={iosInput}
                            value={draft.address}
                            placeholder="Проспект Сарыарка, 31, угол улицы Алиби Жангельдин"
                            onChange={(e) => setDraft((prev) => ({ ...prev, address: e.target.value }))}
                        />
                    </Field>

                    <Field label="Ориентиры" hint="Каждый с новой строки — как в справочнике: вход, этаж, кабинет, что рядом.">
                        <textarea
                            className={`${iosInput} min-h-[72px] resize-y`}
                            value={draft.address_note}
                            placeholder={'Головной офис\nвход со стороны улицы Сарыарка'}
                            onChange={(e) => setDraft((prev) => ({ ...prev, address_note: e.target.value }))}
                        />
                    </Field>

                    <MapField draft={draft} setDraft={setDraft} base={base} headers={headers} showToast={showToast} />

                    <Field label="Телефон">
                        <input
                            className={iosInput}
                            value={draft.phone}
                            placeholder="+7 707 705 08 80"
                            onChange={(e) => setDraft((prev) => ({ ...prev, phone: e.target.value }))}
                        />
                    </Field>

                    <Field label="График работы">
                        <ScheduleEditor
                            schedule={draft.schedule}
                            onChange={(schedule) => setDraft((prev) => ({ ...prev, schedule }))}
                        />
                    </Field>

                    <label className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-2">
                        <span className="flex items-center gap-2 text-[13px] text-slate-700">
                            <Phone size={15} className="text-slate-400" />
                            Партнёрский офис
                            <IosBadge tone="slate">не наш парк</IosBadge>
                        </span>
                        <IosToggle
                            checked={draft.kind === 'partner'}
                            onChange={(next) => setDraft((prev) => ({ ...prev, kind: next ? 'partner' : 'park' }))}
                        />
                    </label>

                    {draft.kind === 'partner' && (
                        <Field label="Чей офис" hint="Показывается плашкой на карточке.">
                            <input
                                className={iosInput}
                                value={draft.partner_label}
                                placeholder="Яндекс для водителей / Тариф Wolt"
                                onChange={(e) => setDraft((prev) => ({ ...prev, partner_label: e.target.value }))}
                            />
                        </Field>
                    )}
                </>
            )}
        </div>
    );
}
