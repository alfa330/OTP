import React, { useMemo, useState } from 'react';
import axios from 'axios';
import { Building2, Check, Loader2, MapPin, Phone, Wifi } from 'lucide-react';
import { iosInput, iosBtnSecondary, IosBadge, IosToggle } from '../ui/ios';
import OfficeMap from './OfficeMap';
import { DAY_CODES, DAY_LABELS, buildSchedule } from './officeSchedule';

/* Форма офиса: адрес, карта, график и парки.
 *
 * Вынесена из WikiOffices отдельным файлом — в ней три самостоятельных
 * редактора (карта, неделя, переопределения по паркам), и вместе со списком
 * получился бы файл, в котором не найти ни того ни другого.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const WORKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri'];

/* Пресеты собраны по боевым данным: в справочнике офисов ровно три сочетания
 * часов и один и тот же обед 13:00–14:00. Ручная неделя остаётся ниже — она
 * нужна Костанаю и Караганде, где суббота короткая. */
const PRESETS = [
    { label: 'Пн–Пт 09:00–19:00', days: WORKDAYS, from: '09:00', to: '19:00' },
    { label: 'Пн–Сб 09:00–19:00', days: [...WORKDAYS, 'sat'], from: '09:00', to: '19:00' },
    { label: 'Пн–Вс 09:00–19:00', days: DAY_CODES, from: '09:00', to: '19:00' },
    { label: 'Пн–Пт 10:00–19:00', days: WORKDAYS, from: '10:00', to: '19:00' },
];

const Field = ({ label, hint, children }) => (
    <div>
        <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">{label}</label>
        {children}
        {hint && <p className="mt-1 px-1 text-[11.5px] leading-relaxed text-slate-400">{hint}</p>}
    </div>
);

const TimeInput = ({ value, onChange, disabled }) => (
    <input
        type="time"
        step={300}
        disabled={disabled}
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        className={`${iosInput} h-9 w-[104px] px-2 text-center tabular-nums disabled:opacity-40`}
    />
);

function ScheduleEditor({ schedule, onChange }) {
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
                            ...preset, breakFrom: '13:00', breakTo: '14:00',
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
                                onChange={(next) => setDay(code, next
                                    ? { from: '09:00', to: '19:00', break_from: '13:00', break_to: '14:00' }
                                    : null)}
                            />
                            {isOpen ? (
                                <>
                                    <TimeInput value={day.from} onChange={(v) => setDay(code, { from: v })} />
                                    <span className="text-slate-300">–</span>
                                    <TimeInput value={day.to} onChange={(v) => setDay(code, { to: v })} />
                                    <span className="ml-1 text-[11.5px] text-slate-400">обед</span>
                                    <TimeInput value={day.break_from} onChange={(v) => setDay(code, { break_from: v })} />
                                    <span className="text-slate-300">–</span>
                                    <TimeInput value={day.break_to} onChange={(v) => setDay(code, { break_to: v })} />
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
                    <OfficeMap lat={draft.lat} lon={draft.lon} height={140} />
                    <div className="mt-1 px-1 text-[11.5px] tabular-nums text-slate-400">
                        {draft.lat.toFixed(6)}, {draft.lon.toFixed(6)}
                    </div>
                </div>
            )}
        </Field>
    );
}

function ParkLinks({ draft, setDraft, parks }) {
    const [expanded, setExpanded] = useState(() => new Set());

    const linkFor = (parkId) => draft.parks.find((link) => link.park_id === parkId);

    const toggle = (parkId) => setDraft((prev) => ({
        ...prev,
        parks: linkFor(parkId)
            ? prev.parks.filter((link) => link.park_id !== parkId)
            : [...prev.parks, { park_id: parkId, phone: '' }],
    }));

    const setPhone = (parkId, phone) => setDraft((prev) => ({
        ...prev,
        parks: prev.parks.map((link) => (link.park_id === parkId ? { ...link, phone } : link)),
    }));

    if (parks.length === 0) {
        return (
            <p className="rounded-xl bg-amber-50 px-3 py-2 text-[12.5px] leading-relaxed text-amber-700">
                Справочник таксопарков пуст — заполните вкладку «Парки», тогда офис можно будет
                к ним привязать.
            </p>
        );
    }

    return (
        <div className="space-y-2">
            <label className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-2">
                <span className="text-[13px] text-slate-700">
                    Офис у всех таксопарков
                    <span className="mt-0.5 block text-[11.5px] leading-relaxed text-slate-400">
                        Новые парки получат его автоматически. Отдельные парки ниже можно отметить,
                        чтобы задать им свой телефон.
                    </span>
                </span>
                <IosToggle
                    checked={!!draft.all_parks}
                    onChange={(next) => setDraft((prev) => ({ ...prev, all_parks: next }))}
                />
            </label>

            <div className="max-h-[240px] divide-y divide-slate-100 overflow-y-auto rounded-xl border border-slate-200">
                {parks.map((park) => {
                    const link = linkFor(park.id);
                    const open = expanded.has(park.id) || !!link?.phone;
                    return (
                        <div key={park.id} className="px-3 py-2">
                            <div className="flex items-center gap-2.5">
                                <button
                                    type="button"
                                    onClick={() => toggle(park.id)}
                                    className={`grid h-5 w-5 shrink-0 place-items-center rounded-md border transition ${
                                        link
                                            ? 'border-indigo-600 bg-indigo-600 text-white'
                                            : 'border-slate-300 bg-white text-transparent'
                                    }`}
                                    aria-label={link ? `Убрать парк ${park.name}` : `Добавить парк ${park.name}`}
                                >
                                    <Check size={13} />
                                </button>
                                <span className="min-w-0 flex-1 truncate text-[13px] text-slate-700">
                                    {park.name}
                                </span>
                                {link && (
                                    <button
                                        type="button"
                                        onClick={() => setExpanded((prev) => {
                                            const next = new Set(prev);
                                            if (next.has(park.id)) next.delete(park.id);
                                            else next.add(park.id);
                                            return next;
                                        })}
                                        className="shrink-0 text-[11.5px] font-medium text-indigo-600 hover:underline"
                                    >
                                        {open ? 'скрыть' : 'свой телефон'}
                                    </button>
                                )}
                            </div>
                            {link && open && (
                                <input
                                    className={`${iosInput} mt-1.5 h-9`}
                                    value={link.phone || ''}
                                    placeholder={draft.phone ? `как у офиса: ${draft.phone}` : 'Телефон для этого парка'}
                                    onChange={(e) => setPhone(park.id, e.target.value)}
                                />
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

export default function OfficeEditor({ draft, setDraft, parks, base, headers, showToast }) {
    const linkedNames = useMemo(() => {
        if (draft.all_parks) return 'все таксопарки';
        const names = draft.parks
            .map((link) => parks.find((park) => park.id === link.park_id)?.name)
            .filter(Boolean);
        return names.length ? names.join(', ') : 'ни одного парка';
    }, [draft.all_parks, draft.parks, parks]);

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
                    <input
                        className={iosInput}
                        value={draft.city}
                        placeholder="Алматы"
                        onChange={(e) => setDraft((prev) => ({ ...prev, city: e.target.value }))}
                    />
                </Field>
            </div>

            <label className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-2">
                <span className="flex items-center gap-2 text-[13px] text-slate-700">
                    <Wifi size={15} className="text-slate-400" />
                    Только по телефону (ОНЛАЙН)
                </span>
                <IosToggle
                    checked={!!draft.is_online}
                    onChange={(next) => setDraft((prev) => ({ ...prev, is_online: next }))}
                />
            </label>

            {!draft.is_online && (
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
                </>
            )}

            <Field label="Телефон">
                <input
                    className={iosInput}
                    value={draft.phone}
                    placeholder="+7 707 705 08 80"
                    onChange={(e) => setDraft((prev) => ({ ...prev, phone: e.target.value }))}
                />
            </Field>

            {!draft.is_online && (
                <Field label="График работы">
                    <ScheduleEditor
                        schedule={draft.schedule}
                        onChange={(schedule) => setDraft((prev) => ({ ...prev, schedule }))}
                    />
                </Field>
            )}

            <Field label="Таксопарки">
                <ParkLinks draft={draft} setDraft={setDraft} parks={parks} />
                <p className="mt-1.5 flex items-center gap-1.5 px-1 text-[11.5px] text-slate-400">
                    <Building2 size={12} /> Сейчас: {linkedNames}
                </p>
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
        </div>
    );
}
