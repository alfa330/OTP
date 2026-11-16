import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    AlertTriangle, Car, Check, ChevronDown, Clock, Loader2, MapPin, Search, X,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosGroupLabel, iosInput, iosBtnSecondary, IosBadge,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import InfoHint from '../common/InfoHint';
import {
    carsOf, cityTariffs, conditionOf, dateWords, listRows, loadClassifier, plural, requirementOf,
    shortRequirement, tariffSummary, upcomingOf, verdictOf,
} from './classifierData';

/* Классификатор автомобилей: подходит ли машина под тариф в конкретном городе.
 *
 * Живёт статьёй вики (slug klassifikator-avto): вместо тела статьи рисуется этот
 * калькулятор. Данные — справочник базы знаний Яндекс Про ПО ГОРОДАМ
 * (GET /api/wiki/classifier, wiki/routes_classifier.py): у каждого города свои
 * таблицы тарифов, и год берётся из таблицы города, а не «Алматы плюс
 * поправка», как было в справочнике из Wiki 2.0. Ночная сверка обновляет
 * справочник сама; экран показывает дату правки Яндекса и ведёт на страницу
 * города.
 *
 * У тарифа города бывает таблица (строка машины; правило марки «LADA (кроме
 * указанных моделей)»; строка «Другие модели») или правило «любая модель не
 * старше N года». Строка таблицы бывает с оговоркой: «не допускается (кроме
 * машин 2024–2026 годов, зарегистрированных в сервисе не позднее 30 июня
 * 2026)» — тогда машина такого года подходит только при этом условии, и экран
 * говорит именно это. Расчёт — classifierData.js, двойник помощника вики.
 */

const CURRENT_YEAR = new Date().getFullYear();
const DEFAULT_CITY = 'almaty';

const yearOf = (value) => (/^\d{4}$/.test(String(value || '')) ? Number(value) : null);

const Skeleton = () => (
    <div className="space-y-4">
        {[0, 1, 2].map((i) => (
            <div key={i} className={`${iosCard} h-[96px]`}>
                <div className="sk-shimmer h-full w-full rounded-2xl" />
            </div>
        ))}
    </div>
);

/* Что показать по тарифу: тон значка и строка под названием. year === null —
   год ещё не введён целиком: тогда показывается само требование, без
   «подходит / не подходит». */
function present(req, year) {
    const state = year === null ? null : verdictOf(req, year);
    const from = req.year ? `от ${req.year} года` : '';
    const own = req.kind === 'any'
        ? `любая модель ${from}`
        : req.kind === 'other' ? `по строке «Другие модели»: ${req.year ? from : 'не допускается'}` : from;
    if (state === null) {
        if (req.kind === 'absent') return { tone: 'neutral', line: 'Модели нет в списке тарифа' };
        if (req.year === null || req.year === undefined) return { tone: 'neutral', line: req.text || '' };
        if (req.year) return { tone: 'neutral', line: own.charAt(0).toUpperCase() + own.slice(1) };
        return {
            tone: 'neutral',
            line: req.except
                ? `Не допускается, кроме машин ${req.except[0]}–${req.except[1]} годов, `
                  + `зарегистрированных в сервисе не позднее ${dateWords(req.except[2])}`
                : 'Не допускается',
        };
    }
    if (state === 'fits') {
        const condition = conditionOf(req);
        return condition
            ? { tone: 'warn', line: `Подходит с оговоркой — ${own}: ${condition}` }
            : { tone: 'ok', line: `Подходит — ${own}` };
    }
    if (state === 'except') {
        return {
            tone: 'warn',
            line: `Только если машина зарегистрирована в сервисе не позднее ${dateWords(req.except[2])}`,
        };
    }
    if (state === 'old') return { tone: 'off', line: `Не подходит — нужен ${req.year} год и новее` };
    if (state === 'unknown') return { tone: 'warn', line: `Требование: ${req.text || 'не указано'}` };
    if (req.kind === 'absent') return { tone: 'off', line: 'Не подходит — модели нет в списке тарифа' };
    return { tone: 'off', line: 'Не допускается' };
}

const TONE_ICON = {
    ok: { box: 'bg-emerald-50 text-emerald-600', icon: <Check size={13} /> },
    warn: { box: 'bg-amber-50 text-amber-600', icon: <Clock size={13} /> },
    off: { box: 'bg-slate-100 text-slate-400', icon: <X size={13} /> },
    neutral: { box: 'bg-slate-100 text-slate-500', icon: <Car size={12} /> },
};

/* Пояснение к тарифу под «i»: как требование записано в таблице (когда в нём
   оговорка), условия тарифа текстом (минивены, цвет «Бизнеса», ГБО) и сноска
   Яндекса про «зарегистрированные*». Отдельной строкой под тарифом они
   занимали бы место всё время, а нужны раз — когда человек не понял ответ. */
function TariffHint({ name, req, notes, footnote }) {
    const parts = [];
    if (req?.text) parts.push({ title: 'В таблице Яндекса', text: req.text });
    (notes || []).forEach((note) => parts.push({ title: null, text: note }));
    if (req?.text && req.text.includes('*') && footnote) parts.push({ title: null, text: `*${footnote}` });
    if (!parts.length) return null;
    return (
        <InfoHint title={`Тариф «${name}»`} side="left">
            <div className="space-y-2">
                {parts.map((part, index) => (
                    <div key={index} className="whitespace-pre-line">
                        {part.title && <div className="font-semibold text-slate-800">{part.title}</div>}
                        {part.text}
                    </div>
                ))}
            </div>
        </InfoHint>
    );
}

/** Результат по одному тарифу. */
const TariffVerdict = ({ item, footnote }) => {
    const { name, req, view, upcoming, entry } = item;
    const tone = TONE_ICON[view.tone];
    return (
        <div className={`flex items-start gap-3 px-4 py-3 ${view.tone === 'off' ? 'opacity-60' : ''}`}>
            <div className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full ${tone.box}`}>
                {tone.icon}
            </div>
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[14px] font-medium text-slate-900">{name}</span>
                    <TariffHint name={name} req={req} notes={entry.notes} footnote={footnote} />
                </div>
                <div className={`mt-0.5 text-[12.5px] ${view.tone === 'warn' ? 'text-amber-700' : 'text-slate-500'}`}>
                    {view.line}
                </div>
                {upcoming && (
                    <div className="mt-0.5 text-[12px] text-slate-400">
                        С {dateWords(upcoming.from)} — новые правила: {shortRequirement(upcoming.req)}
                    </div>
                )}
            </div>
        </div>
    );
};

/* embedded — классификатор открыт внутри статьи вики, а не отдельным разделом.
   Тогда своя страничная обёртка и заголовок не нужны: фон, отступы и шапку уже
   дала статья, а второй заголовок «Классификатор авто» под таким же заголовком
   статьи читался бы как дубль. */
export default function ClassifierView({ prefill = null, embedded = false, base = '', headers = undefined }) {
    const [cityId, setCityId] = useState(DEFAULT_CITY);
    // Последний пришедший справочник города. Пока грузится следующий город,
    // на экране остаётся прежний (притушенный) — без прыжка разметки к скелету.
    const [data, setData] = useState(null);
    const [loadingCity, setLoadingCity] = useState(null);
    const [error, setError] = useState('');
    const [retry, setRetry] = useState(0);

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
    // Заголовки приходят новым объектом на каждую отрисовку статьи; запрос от
    // этого повторяться не должен — берём последние через ref.
    const headersRef = useRef(headers);
    headersRef.current = headers;

    useEffect(() => {
        let cancelled = false;
        setLoadingCity(cityId);
        loadClassifier({ base, headers: headersRef.current, city: cityId })
            .then((payload) => {
                if (cancelled) return;
                setData(payload);
                setError('');
                setLoadingCity(null);
            })
            .catch((failure) => {
                if (cancelled) return;
                setLoadingCity(null);
                setError(failure?.hidden ? 'Классификатор авто вам недоступен' : 'Не удалось загрузить справочник');
            });
        return () => { cancelled = true; };
    }, [base, cityId, retry]);

    const cars = useMemo(() => carsOf(data), [data]);

    /* Предзаполнение из поиска вики и из источника под ответом помощника:
       марка, модель, год, город. nonce отличает повторное нажатие от уже
       применённого значения. */
    useEffect(() => {
        if (!data || !prefill || consumedPrefillRef.current === prefill.nonce) return;
        consumedPrefillRef.current = prefill.nonce;

        if (prefill.cityId && (data.cities || []).some((c) => c.id === prefill.cityId)) {
            setCityId(prefill.cityId);
        }
        if (/^\d{4}$/.test(String(prefill.year || ''))) {
            setYear(String(prefill.year));
        }
        const car = cars.find((c) => c.brand === prefill.brand && c.model === prefill.model);
        if (!car) return;
        setBrand((current) => {
            if (current === car.brand) {
                setModel(car.model);       // марка не меняется — сброс не сработает
                return current;
            }
            pendingModelRef.current = car.model;
            return car.brand;
        });
    }, [data, prefill, cars]);

    // Сброс модели при смене марки — иначе на экране остаётся невозможная
    // пара. Модель из предзаполнения (ref) переживает этот сброс.
    useEffect(() => {
        setModel(pendingModelRef.current || '');
        pendingModelRef.current = null;
    }, [brand]);

    const brands = useMemo(
        () => Array.from(new Set(cars.map((c) => c.brand))).sort((a, b) => a.localeCompare(b, 'ru')),
        [cars],
    );
    const models = useMemo(() => {
        if (!brand) return [];
        return cars.filter((c) => c.brand === brand).map((c) => c.model)
            .sort((a, b) => a.localeCompare(b, 'ru', { numeric: true }));
    }, [cars, brand]);

    const tariffs = useMemo(() => cityTariffs(data), [data]);
    const numericYear = yearOf(year);
    const known = !!brand && !!model && cars.some((c) => c.brand === brand && c.model === model);

    const verdicts = useMemo(() => {
        if (!data || !known) return null;
        const car = { brand, model };
        return tariffs.map(({ key, name, entry }) => {
            const req = requirementOf(data, car, key);
            return {
                key, name, entry, req,
                state: numericYear === null ? null : verdictOf(req, numericYear),
                view: present(req, numericYear),
                upcoming: upcomingOf(data, car, key),
            };
        });
    }, [data, known, brand, model, tariffs, numericYear]);

    const rowsFor = useCallback((key) => listRows(data, key, tableQuery), [data, tableQuery]);

    const cityOptions = useMemo(
        () => (data?.cities || []).map((c) => ({ value: c.id, label: c.name })),
        [data],
    );
    // Подходит — и с условием таблицы («только комплектации с сидениями») тоже;
    // «если уже зарегистрирована» не в счёт: новую такую машину не подключить.
    const fitCount = verdicts ? verdicts.filter((v) => v.state === 'fits').length : 0;
    const city = data?.city || null;
    const source = data?.source || {};
    const switching = !!loadingCity && !!data && data.city?.id !== loadingCity;

    return (
        <div
            className={embedded
                ? ''
                : 'min-h-full bg-slate-50 px-4 pb-10 pt-[68px] sm:px-6 min-[769px]:pt-8'}
            style={embedded ? undefined : { fontFamily: APPLE_FONT }}
        >
            <div className={embedded ? 'w-full space-y-5' : 'mx-auto w-full max-w-4xl space-y-5'}>

                {!embedded && (
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
                )}

                {!data && !error && <Skeleton />}

                {error && !data && (
                    <div className={`${iosCard} px-6 py-12 text-center`}>
                        <div className="text-[15px] font-semibold text-slate-900">{error}</div>
                        {error !== 'Классификатор авто вам недоступен' && (
                            <button type="button" className={`${iosBtnSecondary} mt-4`} onClick={() => setRetry((n) => n + 1)}>
                                Повторить
                            </button>
                        )}
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
                                        {switching && <Loader2 size={12} className="animate-spin text-slate-400" />}
                                    </label>
                                    <CustomSelect
                                        variant="ios" value={cityId} onChange={setCityId}
                                        options={cityOptions} searchable ariaLabel="Город"
                                    />
                                    {error && (
                                        <p className="mt-1 px-1 text-[11.5px] text-rose-600">
                                            {error}.{' '}
                                            <button type="button" className="underline underline-offset-2" onClick={() => setRetry((n) => n + 1)}>
                                                Повторить
                                            </button>
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
                            <section className={`space-y-1.5 transition-opacity ${switching ? 'opacity-60' : ''}`}>
                                <div className="flex items-center justify-between gap-2">
                                    <div className={iosGroupLabel}>Результат · {city?.name}</div>
                                    {numericYear !== null && verdicts.length > 0 && (
                                        <IosBadge tone={fitCount ? 'green' : 'red'}>
                                            подходит тарифов: {fitCount} из {verdicts.length}
                                        </IosBadge>
                                    )}
                                </div>
                                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                    {verdicts.length === 0 && (
                                        <div className="px-4 py-8 text-center text-[13px] text-slate-400">
                                            В этом городе классификатор не ведёт ни одного тарифа
                                        </div>
                                    )}
                                    {verdicts.map((item) => (
                                        <TariffVerdict key={item.key} item={item} footnote={source.footnote} />
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
                        <section className={`space-y-1.5 transition-opacity ${switching ? 'opacity-60' : ''}`}>
                            <div className={iosGroupLabel}>Списки по тарифам · {city?.name}</div>
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
                                {tariffs.map(({ key, name, entry }) => {
                                    const isOpen = openTariff === key;
                                    const rows = isOpen && entry.kind === 'list' ? rowsFor(key) : [];
                                    return (
                                        <div key={key} className={`${iosCard} overflow-hidden`}>
                                            <button
                                                type="button"
                                                onClick={() => setOpenTariff(isOpen ? null : key)}
                                                className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50"
                                            >
                                                <div className="min-w-0 flex-1">
                                                    <div className="text-[14px] font-medium text-slate-900">{name}</div>
                                                    <div className="truncate text-[12px] text-slate-500">{tariffSummary(entry)}</div>
                                                </div>
                                                <ChevronDown
                                                    size={16}
                                                    className={`shrink-0 text-slate-400 transition-transform ${isOpen ? 'rotate-180' : ''}`}
                                                />
                                            </button>

                                            {isOpen && entry.kind === 'any' && (
                                                <div className="space-y-2 border-t border-slate-100 px-4 py-3 text-[13px] leading-relaxed text-slate-600">
                                                    <p>
                                                        Отдельной таблицы моделей у тарифа в городе нет: подходит любая
                                                        модель не старше <span className="tabular-nums">{entry.any}</span> года выпуска.
                                                    </p>
                                                    {(entry.notes || []).map((note) => (
                                                        <p key={note} className="whitespace-pre-line text-[12.5px] text-slate-500">{note}</p>
                                                    ))}
                                                </div>
                                            )}

                                            {isOpen && entry.kind === 'list' && (
                                                <div className="border-t border-slate-100">
                                                    <div className="max-h-[380px] overflow-y-auto">
                                                        <table className="w-full text-[13px]">
                                                            <thead className="sticky top-0 bg-slate-50">
                                                                <tr className="text-left text-[11px] uppercase tracking-wider text-slate-400">
                                                                    <th className="px-4 py-2 font-semibold">Марка</th>
                                                                    <th className="px-4 py-2 font-semibold">Модель</th>
                                                                    <th className="px-4 py-2 text-right font-semibold">Год выпуска</th>
                                                                </tr>
                                                            </thead>
                                                            <tbody>
                                                                {rows.length === 0 && (
                                                                    <tr>
                                                                        <td colSpan={3} className="px-4 py-8 text-center text-slate-400">
                                                                            {tableQuery.trim()
                                                                                ? 'Такой модели в таблице тарифа нет'
                                                                                : 'В таблице нет допущенных моделей'}
                                                                        </td>
                                                                    </tr>
                                                                )}
                                                                {rows.map((row) => (
                                                                    <tr key={`${row.brand}-${row.model}`} className="border-t border-slate-50">
                                                                        <td className="px-4 py-2 text-slate-700">{row.brand}</td>
                                                                        <td className="px-4 py-2 text-slate-900">
                                                                            <span className="flex items-center gap-1.5">
                                                                                {row.model}
                                                                                {row.req.text && (
                                                                                    <span title={row.req.text} className="inline-flex shrink-0">
                                                                                        <AlertTriangle size={12} className="text-amber-500" />
                                                                                    </span>
                                                                                )}
                                                                            </span>
                                                                        </td>
                                                                        <td className={`px-4 py-2 text-right tabular-nums ${row.req.year ? 'text-slate-700' : 'text-slate-400'}`}>
                                                                            {shortRequirement(row.req)}
                                                                        </td>
                                                                    </tr>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                    <div className="border-t border-slate-100 px-4 py-2 text-[11.5px] text-slate-400">
                                                        {tableQuery.trim() ? 'Найдено' : 'Допущено'}:{' '}
                                                        <span className="tabular-nums">{rows.length}</span>{' '}
                                                        {plural(rows.length, 'модель', 'модели', 'моделей')}
                                                        {entry.other && !tableQuery.trim() && (
                                                            <> · остальные модели — {entry.other[0] ? `от ${entry.other[0]} г.` : 'не допускаются'}</>
                                                        )}
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        </section>

                        <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                            Данные базы знаний Яндекс Про
                            {source.updated ? `, обновлены ${source.updated}` : ''}
                            {city?.url && (
                                <>
                                    {' · '}
                                    <a
                                        href={city.url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="underline-offset-2 transition hover:text-slate-600 hover:underline"
                                    >
                                        страница города на сайте Яндекса
                                    </a>
                                </>
                            )}
                        </p>
                    </>
                )}
            </div>
        </div>
    );
}
