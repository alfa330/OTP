import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    AlertTriangle, Car, Check, ChevronDown, Loader2, MapPin, Search, X,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosGroupLabel, iosInput, iosBtnSecondary, IosBadge,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';

/* Классификатор автомобилей: подходит ли машина под тариф в конкретном городе.
 *
 * Перенесён из Wiki 2.0, но вынесен ОТДЕЛЬНЫМ разделом, а не оставлен внутри
 * вики. В оригинале он жил статьёй-призраком: рендер статьи подменялся
 * компонентом по слагу auto-list-*, то есть в базе лежала пустышка, а на
 * экране показывалось совсем другое. Такую связку переносить незачем.
 *
 * Данные (20 городов, 7 тарифов, 1502 модели, 106 КБ) грузятся динамическим
 * import при первом открытии раздела — в основной бандл портала они не входят.
 *
 * Правило подбора ровно как в оригинале:
 *     требуемый год = max(1980, базовый год модели + поправка города)
 * Базовые годы заданы для Алматы, поправка сдвигает планку: в Астане строже
 * на год, в Туркестане мягче на три.
 */

const YEAR_FLOOR = 1980;
const CURRENT_YEAR = new Date().getFullYear();

const requiredYear = (baseYear, cityOffset) =>
    Math.max(YEAR_FLOOR, baseYear + cityOffset);

const Skeleton = () => (
    <div className="space-y-4">
        {[0, 1, 2].map((i) => (
            <div key={i} className={`${iosCard} h-[96px]`}>
                <div className="sk-shimmer h-full w-full rounded-2xl" />
            </div>
        ))}
    </div>
);

/** Результат по одному тарифу. */
const TariffVerdict = ({ tariff, verdict }) => (
    <div className={`flex items-start gap-3 px-4 py-3 ${verdict.fits ? '' : 'opacity-60'}`}>
        <div className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full ${
            verdict.fits ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-400'
        }`}>
            {verdict.fits ? <Check size={13} /> : <X size={13} />}
        </div>
        <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[14px] font-medium text-slate-900">{tariff.name}</span>
                {verdict.warning && (
                    <IosBadge tone="amber">
                        <AlertTriangle size={11} /> с оговоркой
                    </IosBadge>
                )}
            </div>
            <div className="mt-0.5 text-[12.5px] text-slate-500">
                {verdict.fits
                    ? <>Подходит — требуется <span className="tabular-nums">{verdict.minYear}</span> год и новее</>
                    : <>Не подходит — нужен <span className="tabular-nums">{verdict.minYear}</span> год и новее</>}
            </div>
            {verdict.warning && (
                <div className="mt-1 rounded-lg bg-amber-50 px-2.5 py-1.5 text-[12px] leading-relaxed text-amber-800">
                    {verdict.warning}
                </div>
            )}
        </div>
    </div>
);

export default function ClassifierView({ prefill = null }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');

    const [cityId, setCityId] = useState('almaty');
    const [brand, setBrand] = useState('');
    const [model, setModel] = useState('');
    const [year, setYear] = useState(String(CURRENT_YEAR - 5));
    const [tableQuery, setTableQuery] = useState('');
    const [openTariff, setOpenTariff] = useState(null);

    /* Модель из предзаполнения нельзя ставить сразу: эффект сброса модели при
       смене марки сработает ПОСЛЕ и затёр бы её. Кладём в ref — сброс заберёт
       её вместо пустой строки. */
    const pendingModelRef = useRef(null);
    const consumedPrefillRef = useRef(null);

    useEffect(() => {
        let cancelled = false;
        import('./classifier-data.json')
            .then((module) => { if (!cancelled) setData(module.default || module); })
            .catch(() => { if (!cancelled) setError('Не удалось загрузить справочник'); });
        return () => { cancelled = true; };
    }, []);

    /* Предзаполнение из поиска вики: «Открыть в классификаторе» на баре машины
       переносит сюда марку/модель/год/город. nonce отличает повторное нажатие
       от уже применённого значения. */
    useEffect(() => {
        if (!data || !prefill || consumedPrefillRef.current === prefill.nonce) return;
        consumedPrefillRef.current = prefill.nonce;

        if (prefill.cityId && data.cities.some((c) => c.id === prefill.cityId)) {
            setCityId(prefill.cityId);
        }
        if (/^\d{4}$/.test(String(prefill.year || ''))) {
            setYear(String(prefill.year));
        }
        const car = data.cars.find(
            (c) => c.brand === prefill.brand && c.model === prefill.model,
        );
        if (!car) return;
        setBrand((current) => {
            if (current === car.brand) {
                setModel(car.model);       // марка не меняется — сброс не сработает
                return current;
            }
            pendingModelRef.current = car.model;
            return car.brand;
        });
    }, [data, prefill]);

    const city = useMemo(
        () => (data?.cities || []).find((c) => c.id === cityId) || null,
        [data, cityId],
    );

    const brands = useMemo(() => {
        if (!data) return [];
        return Array.from(new Set(data.cars.map((c) => c.brand))).sort((a, b) => a.localeCompare(b, 'ru'));
    }, [data]);

    const models = useMemo(() => {
        if (!data || !brand) return [];
        return data.cars
            .filter((c) => c.brand === brand)
            .map((c) => c.model)
            .sort((a, b) => a.localeCompare(b, 'ru'));
    }, [data, brand]);

    // Сброс модели при смене марки — иначе на экране остаётся невозможная
    // пара. Модель из предзаполнения (ref) переживает этот сброс.
    useEffect(() => {
        setModel(pendingModelRef.current || '');
        pendingModelRef.current = null;
    }, [brand]);

    const verdicts = useMemo(() => {
        if (!data || !city || !brand || !model) return null;
        const car = data.cars.find((c) => c.brand === brand && c.model === model);
        if (!car) return null;

        const numericYear = Number(year);
        return (city.tariffs || [])
            .map((key) => {
                const tariff = data.tariffs.find((t) => t.key === key);
                const base = car.years?.[key];
                if (!tariff || base === undefined) return null;
                const minYear = requiredYear(base, city.offset);
                return {
                    tariff,
                    verdict: {
                        fits: Number.isFinite(numericYear) && numericYear >= minYear,
                        minYear,
                        warning: car.warnings?.[key],
                    },
                };
            })
            .filter(Boolean);
    }, [data, city, brand, model, year]);

    const carsForTariff = useCallback((tariffKey) => {
        if (!data || !city) return [];
        const needle = tableQuery.trim().toLowerCase();
        return data.cars
            .filter((car) => car.years?.[tariffKey] !== undefined)
            .filter((car) => !needle
                || car.brand.toLowerCase().includes(needle)
                || car.model.toLowerCase().includes(needle))
            .map((car) => ({
                ...car,
                minYear: requiredYear(car.years[tariffKey], city.offset),
                warning: car.warnings?.[tariffKey],
            }))
            .sort((a, b) => a.brand.localeCompare(b.brand, 'ru')
                || a.model.localeCompare(b.model, 'ru'));
    }, [data, city, tableQuery]);

    const cityOptions = useMemo(
        () => (data?.cities || []).map((c) => ({ value: c.id, label: c.name })),
        [data],
    );

    const fitCount = verdicts ? verdicts.filter((v) => v.verdict.fits).length : 0;

    return (
        <div
            className="min-h-full bg-slate-50 px-4 pb-10 pt-[68px] sm:px-6 min-[769px]:pt-8"
            style={{ fontFamily: APPLE_FONT }}
        >
            <div className="mx-auto w-full max-w-4xl space-y-5">

                <header className="flex items-center gap-3">
                    <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-indigo-600 text-white shadow-sm">
                        <Car size={21} />
                    </div>
                    <div>
                        <h1 className="text-[22px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
                            Классификатор авто
                        </h1>
                        <p className="text-[13px] text-slate-500">
                            Какие тарифы доступны машине в выбранном городе
                        </p>
                    </div>
                </header>

                {!data && !error && <Skeleton />}

                {error && (
                    <div className={`${iosCard} px-6 py-12 text-center`}>
                        <div className="text-[15px] font-semibold text-slate-900">{error}</div>
                    </div>
                )}

                {data && (
                    <>
                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Подбор</div>
                            <div className={`${iosCard} space-y-3 p-4`}>
                                <div>
                                    <label className="mb-1 flex items-center gap-1.5 px-1 text-[12px] font-medium text-slate-500">
                                        <MapPin size={12} /> Город
                                    </label>
                                    <CustomSelect
                                        variant="ios" value={cityId} onChange={setCityId}
                                        options={cityOptions} searchable ariaLabel="Город"
                                    />
                                    {city && city.offset !== 0 && (
                                        <p className="mt-1 px-1 text-[11.5px] text-slate-400">
                                            Требования {city.offset > 0 ? 'строже' : 'мягче'} базовых
                                            на <span className="tabular-nums">{Math.abs(city.offset)}</span> {Math.abs(city.offset) === 1 ? 'год' : 'года'}
                                        </p>
                                    )}
                                </div>

                                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                                    <div>
                                        <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Марка</label>
                                        <CustomSelect
                                            variant="ios" value={brand} onChange={setBrand}
                                            options={brands.map((b) => ({ value: b, label: b }))}
                                            searchable ariaLabel="Марка автомобиля"
                                        />
                                    </div>
                                    <div>
                                        <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Модель</label>
                                        <CustomSelect
                                            variant="ios" value={model} onChange={setModel}
                                            options={models.map((m) => ({ value: m, label: m }))}
                                            searchable ariaLabel="Модель автомобиля"
                                        />
                                    </div>
                                    <div>
                                        <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Год выпуска</label>
                                        <input
                                            className={`${iosInput} tabular-nums`}
                                            inputMode="numeric"
                                            value={year}
                                            onChange={(e) => setYear(e.target.value.replace(/\D/g, '').slice(0, 4))}
                                            placeholder={String(CURRENT_YEAR - 5)}
                                        />
                                    </div>
                                </div>
                            </div>
                        </section>

                        {verdicts && (
                            <section className="space-y-1.5">
                                <div className="flex items-center justify-between gap-2">
                                    <div className={iosGroupLabel}>Результат</div>
                                    <IosBadge tone={fitCount ? 'green' : 'red'}>
                                        подходит тарифов: {fitCount} из {verdicts.length}
                                    </IosBadge>
                                </div>
                                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                    {verdicts.length === 0 && (
                                        <div className="px-4 py-8 text-center text-[13px] text-slate-400">
                                            Для этой модели нет данных по городу
                                        </div>
                                    )}
                                    {verdicts.map(({ tariff, verdict }) => (
                                        <TariffVerdict key={tariff.key} tariff={tariff} verdict={verdict} />
                                    ))}
                                </div>
                            </section>
                        )}

                        {!verdicts && brand && !model && (
                            <div className={`${iosCard} px-6 py-10 text-center text-[13px] text-slate-400`}>
                                Выберите модель — и увидите, какие тарифы доступны
                            </div>
                        )}

                        {/* Полные списки по тарифам: нужны, когда вопрос звучит
                            наоборот — «а какие машины вообще берут в Комфорт». */}
                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Списки по тарифам</div>
                            <div className="relative">
                                <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                                <input
                                    className={`${iosInput} pl-10`}
                                    value={tableQuery}
                                    onChange={(e) => setTableQuery(e.target.value)}
                                    placeholder="Фильтр по марке или модели"
                                />
                            </div>

                            <div className="space-y-2">
                                {(city?.tariffs || []).map((key) => {
                                    const tariff = data.tariffs.find((t) => t.key === key);
                                    if (!tariff) return null;
                                    const isOpen = openTariff === key;
                                    const cars = isOpen ? carsForTariff(key) : [];
                                    return (
                                        <div key={key} className={`${iosCard} overflow-hidden`}>
                                            <button
                                                type="button"
                                                onClick={() => setOpenTariff(isOpen ? null : key)}
                                                className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50"
                                            >
                                                <div className="min-w-0 flex-1">
                                                    <div className="text-[14px] font-medium text-slate-900">{tariff.name}</div>
                                                    <div className="truncate text-[12px] text-slate-500">{tariff.description}</div>
                                                </div>
                                                <ChevronDown
                                                    size={16}
                                                    className={`shrink-0 text-slate-400 transition-transform ${isOpen ? 'rotate-180' : ''}`}
                                                />
                                            </button>

                                            {isOpen && (
                                                <div className="border-t border-slate-100">
                                                    <div className="max-h-[380px] overflow-y-auto">
                                                        <table className="w-full text-[13px]">
                                                            <thead className="sticky top-0 bg-slate-50">
                                                                <tr className="text-left text-[11px] uppercase tracking-wider text-slate-400">
                                                                    <th className="px-4 py-2 font-semibold">Марка</th>
                                                                    <th className="px-4 py-2 font-semibold">Модель</th>
                                                                    <th className="px-4 py-2 text-right font-semibold">Год и новее</th>
                                                                </tr>
                                                            </thead>
                                                            <tbody>
                                                                {cars.length === 0 && (
                                                                    <tr>
                                                                        <td colSpan={3} className="px-4 py-8 text-center text-slate-400">
                                                                            Ничего не найдено
                                                                        </td>
                                                                    </tr>
                                                                )}
                                                                {cars.map((car) => (
                                                                    <tr key={`${car.brand}-${car.model}`} className="border-t border-slate-50">
                                                                        <td className="px-4 py-2 text-slate-700">{car.brand}</td>
                                                                        <td className="px-4 py-2 text-slate-900">
                                                                            <span className="flex items-center gap-1.5">
                                                                                {car.model}
                                                                                {car.warning && (
                                                                                    <AlertTriangle size={12} className="shrink-0 text-amber-500" title={car.warning} />
                                                                                )}
                                                                            </span>
                                                                        </td>
                                                                        <td className="px-4 py-2 text-right tabular-nums text-slate-700">
                                                                            {car.minYear}
                                                                        </td>
                                                                    </tr>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                    <div className="border-t border-slate-100 px-4 py-2 text-[11.5px] text-slate-400">
                                                        Моделей в списке: <span className="tabular-nums">{cars.length}</span>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        </section>
                    </>
                )}
            </div>
        </div>
    );
}
