import React, {
    useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { createPortal } from 'react-dom';
import axios from 'axios';
import {
    AlertTriangle, Car, Check, ChevronRight, CornerDownLeft, FileText,
    Loader2, MapPin, Search, X,
} from 'lucide-react';
import {
    APPLE_FONT, iosInput, iosBtnPrimary, IosBadge,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { queryVariants } from './searchText';

/* Поиск по вики — порт search-modal.tsx исходной вики на примитивы портала.
 *
 * Два результата одного запроса:
 *   1. Статьи — сервер (/api/wiki/search): полнотекст + префикс + опечатки,
 *      строго в периметре видимости пользователя.
 *   2. Машина — локально: запрос прогоняется через варианты написания
 *      (транслит, раскладка, алиасы — searchText.js) по справочнику
 *      классификатора, и если это марка/модель, справа открывается бар
 *      с тарифами по городу и году. Ровно то, что в оригинале называлось
 *      «Классификатор Яндекс Про» внутри поисковой строки.
 *
 * Справочник классификатора (106 КБ) грузится динамическим import при первом
 * открытии модалки и кэшируется на модуль — в бандл вики он не входит.
 */

const YEAR_FLOOR = 1980;
const CURRENT_YEAR = new Date().getFullYear();
const requiredYear = (baseYear, cityOffset) => Math.max(YEAR_FLOOR, baseYear + cityOffset);

let classifierDataPromise = null;
const loadClassifierData = () => {
    if (!classifierDataPromise) {
        classifierDataPromise = import('../classifier/classifier-data.json')
            .then((module) => module.default || module);
    }
    return classifierDataPromise;
};

/* Сниппет приходит с сервера: ts_headline над plain-текстом, единственный
   допустимый тег — <mark>. Всё прочее вычищается, mark пересобирается руками —
   dangerouslySetInnerHTML не получает ничего, что не собрали мы сами. */
const escapeHtml = (value) => String(value || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const snippetHtml = (snippet) => escapeHtml(snippet)
    .replace(/&lt;mark&gt;/g, '<mark>')
    .replace(/&lt;\/mark&gt;/g, '</mark>');

const markedWord = (snippet, fallback) => {
    const match = /<mark>(.*?)<\/mark>/i.exec(String(snippet || ''));
    return (match && match[1].trim()) || fallback;
};

/** Поиск машины по вариантам написания запроса — алгоритм оригинала. */
export function matchCar(cars, query) {
    const trimmed = String(query || '').trim();
    if (trimmed.length < 2 || !cars?.length) return null;
    const variants = queryVariants(trimmed).map((v) => v.toLowerCase().trim());

    for (const car of cars) {
        const brand = car.brand.toLowerCase();
        const model = car.model.toLowerCase();
        const fullName = `${brand} ${model}`;

        if (variants.some((v) => v === brand || v === model || v === fullName
                || fullName.includes(v))) {
            return car;
        }
        for (const variant of variants) {
            const words = variant.split(/\s+/);
            if (words.length < 2) continue;
            const hasBrand = words.some((w) => brand.includes(w) || w.includes(brand));
            const hasModel = words.some((w) => model.includes(w) || w.includes(model));
            if (hasBrand && hasModel) return car;
        }
    }
    // Марка без модели: показываем первую модель марки, список — рядом.
    for (const car of cars) {
        const brand = car.brand.toLowerCase();
        if (variants.some((v) => v === brand || brand.includes(v))) return car;
    }
    return null;
}

export function matchBrand(cars, query) {
    const trimmed = String(query || '').trim();
    if (trimmed.length < 2 || !cars?.length) return null;
    const variants = queryVariants(trimmed).map((v) => v.toLowerCase().trim());
    const brands = Array.from(new Set(cars.map((c) => c.brand)));
    for (const brand of brands) {
        const lower = brand.toLowerCase();
        if (variants.some((v) => lower.includes(v) || v.includes(lower))) return brand;
    }
    return null;
}

/** Бар машины: город, год и вердикты по тарифам. */
function CarWidget({ data, car, onOpenClassifier }) {
    const [cityId, setCityId] = useState('almaty');
    const [year, setYear] = useState(String(CURRENT_YEAR - 5));

    const city = useMemo(
        () => (data.cities || []).find((c) => c.id === cityId) || data.cities[0],
        [data, cityId],
    );
    const cityOptions = useMemo(
        () => (data.cities || []).map((c) => ({ value: c.id, label: c.name })),
        [data],
    );

    const numericYear = Number(year);
    const verdicts = useMemo(() => {
        if (!car || !city) return [];
        return (city.tariffs || [])
            .map((key) => {
                const tariff = (data.tariffs || []).find((t) => t.key === key);
                const base = car.years?.[key];
                if (!tariff || base === undefined) return null;
                const minYear = requiredYear(base, city.offset);
                return {
                    tariff,
                    fits: Number.isFinite(numericYear) && numericYear >= minYear,
                    minYear,
                    warning: car.warnings?.[key],
                };
            })
            .filter(Boolean);
    }, [data, car, city, numericYear]);

    return (
        <div className="flex min-h-0 flex-col rounded-xl bg-slate-50 p-3">
            <div className="mb-2.5 flex items-center gap-2.5 px-0.5">
                <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-indigo-600 text-white">
                    <Car size={15} />
                </div>
                <div className="min-w-0">
                    <div className="truncate text-[13.5px] font-semibold text-slate-900">
                        {car.brand} {car.model}
                    </div>
                    <div className="text-[11px] text-slate-500">Классификатор авто</div>
                </div>
            </div>

            <div className="mb-2.5 grid grid-cols-2 gap-2">
                <div>
                    <label className="mb-1 flex items-center gap-1 px-0.5 text-[11px] font-medium text-slate-500">
                        <MapPin size={10} /> Город
                    </label>
                    <CustomSelect
                        variant="ios" value={cityId} onChange={setCityId}
                        options={cityOptions} searchable ariaLabel="Город"
                    />
                </div>
                <div>
                    <label className="mb-1 block px-0.5 text-[11px] font-medium text-slate-500">Год авто</label>
                    <input
                        className={`${iosInput} tabular-nums`}
                        inputMode="numeric"
                        value={year}
                        onChange={(e) => setYear(e.target.value.replace(/\D/g, '').slice(0, 4))}
                        placeholder={String(CURRENT_YEAR - 5)}
                    />
                </div>
            </div>

            <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-0.5">
                {verdicts.length === 0 && (
                    <div className="rounded-lg bg-white px-3 py-4 text-center text-[12px] text-slate-400">
                        Автомобиль не поддерживается тарифами в г. {city?.name}
                    </div>
                )}
                {verdicts.map(({ tariff, fits, minYear, warning }) => (
                    <div key={tariff.key} className="rounded-lg bg-white px-2.5 py-2">
                        <div className="flex items-center justify-between gap-2">
                            <span className="truncate text-[12.5px] font-medium text-slate-800">
                                {tariff.name}
                            </span>
                            {warning ? (
                                <IosBadge tone="amber"><AlertTriangle size={10} /> внимание</IosBadge>
                            ) : fits ? (
                                <IosBadge tone="green"><Check size={10} /> подходит</IosBadge>
                            ) : (
                                <IosBadge tone="red">от {minYear} г.</IosBadge>
                            )}
                        </div>
                        {warning && (
                            <div className="mt-1 rounded-md bg-amber-50 px-2 py-1 text-[11px] leading-relaxed text-amber-800">
                                {warning}
                            </div>
                        )}
                    </div>
                ))}
            </div>

            {onOpenClassifier && (
                <button
                    type="button"
                    className={`${iosBtnPrimary} mt-2.5 w-full`}
                    onClick={() => onOpenClassifier({
                        brand: car.brand,
                        model: car.model,
                        year,
                        cityId,
                    })}
                >
                    Открыть в классификаторе <ChevronRight size={14} />
                </button>
            )}
        </div>
    );
}

export default function WikiSearchModal({ open, onClose, base, headers,
                                          onOpenArticle, onOpenClassifier }) {
    const [query, setQuery] = useState('');
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(false);
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [classifier, setClassifier] = useState(null);
    const [pickedCar, setPickedCar] = useState(null);
    const inputRef = useRef(null);
    const requestSeq = useRef(0);

    // Справочник машин подгружается при первом открытии.
    useEffect(() => {
        if (!open || classifier) return;
        let cancelled = false;
        loadClassifierData()
            .then((data) => { if (!cancelled) setClassifier(data); })
            .catch(() => {});   // без справочника поиск статей работает как обычно
        return () => { cancelled = true; };
    }, [open, classifier]);

    // Сброс при закрытии — следующий поиск начинается с чистого листа.
    useEffect(() => {
        if (open) return;
        setQuery('');
        setItems([]);
        setSelectedIndex(0);
        setPickedCar(null);
    }, [open]);

    useEffect(() => {
        if (open) setTimeout(() => inputRef.current?.focus(), 30);
    }, [open]);

    const term = query.trim();

    // Статьи — с двух символов, дебаунс 250 мс, гонки отсекаются номером запроса.
    useEffect(() => {
        if (!open) return undefined;
        if (term.length < 2) {
            setItems([]);
            setLoading(false);
            return undefined;
        }
        setLoading(true);
        const seq = ++requestSeq.current;
        const timer = setTimeout(() => {
            axios.get(`${base}/search`, { headers, params: { q: term } })
                .then((r) => {
                    if (requestSeq.current !== seq) return;
                    setItems(r.data?.items || []);
                    setSelectedIndex(0);
                })
                .catch(() => { if (requestSeq.current === seq) setItems([]); })
                .finally(() => { if (requestSeq.current === seq) setLoading(false); });
        }, 250);
        return () => clearTimeout(timer);
    }, [open, term, base, headers]);

    const matchedCar = useMemo(
        () => (classifier ? matchCar(classifier.cars, term) : null),
        [classifier, term],
    );
    const matchedBrand = useMemo(
        () => (classifier ? matchBrand(classifier.cars, term) : null),
        [classifier, term],
    );
    const brandModels = useMemo(() => {
        if (!classifier || !matchedBrand) return [];
        return classifier.cars.filter((c) => c.brand === matchedBrand);
    }, [classifier, matchedBrand]);

    // Клик по модели из списка марки перекрывает автоматический матч.
    useEffect(() => { setPickedCar(null); }, [term]);
    const activeCar = pickedCar || matchedCar;

    const openArticle = useCallback((item) => {
        onClose();
        onOpenArticle?.(item.slug, markedWord(item.snippet, term));
    }, [onClose, onOpenArticle, term]);

    const onKeyDown = useCallback((e) => {
        if (e.key === 'Escape') { onClose(); return; }
        if (!items.length) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setSelectedIndex((i) => (i + 1) % items.length);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setSelectedIndex((i) => (i - 1 + items.length) % items.length);
        } else if (e.key === 'Enter' && items[selectedIndex]) {
            e.preventDefault();
            openArticle(items[selectedIndex]);
        }
    }, [items, selectedIndex, onClose, openArticle]);

    if (!open) return null;

    const showEmpty = !loading && term.length >= 2 && items.length === 0
        && !activeCar && brandModels.length === 0;

    return createPortal(
        <div
            className="wiki-scope fixed inset-0 z-[80] overflow-y-auto bg-slate-900/40 p-3 backdrop-blur-[2px] sm:p-6"
            style={{ fontFamily: APPLE_FONT }}
            onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
            role="dialog"
            aria-modal="true"
            aria-label="Поиск по вики"
        >
            <div className={`mx-auto mt-[4vh] w-full ${activeCar ? 'max-w-3xl' : 'max-w-xl'} overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-slate-900/5`}>

                {/* Строка ввода */}
                <div className="flex items-center gap-2.5 border-b border-slate-100 px-4 py-3">
                    {loading
                        ? <Loader2 size={17} className="shrink-0 animate-spin text-slate-400" />
                        : <Search size={17} className="shrink-0 text-slate-400" />}
                    <input
                        ref={inputRef}
                        className="min-w-0 flex-1 bg-transparent text-[15px] text-slate-900 outline-none placeholder:text-slate-400"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        onKeyDown={onKeyDown}
                        placeholder="Статья, марка или модель машины…"
                        aria-label="Поисковый запрос"
                    />
                    {query && (
                        <button
                            type="button"
                            onClick={() => { setQuery(''); inputRef.current?.focus(); }}
                            className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200"
                            aria-label="Очистить"
                        >
                            <X size={12} />
                        </button>
                    )}
                    <button
                        type="button"
                        onClick={onClose}
                        className="hidden shrink-0 rounded-md border border-slate-200 px-1.5 py-0.5 text-[10.5px] font-medium text-slate-400 sm:block"
                    >
                        Esc
                    </button>
                </div>

                {/* Тело: слева результаты, справа бар машины (на мобильном — сверху) */}
                <div className={`flex flex-col gap-3 p-3 ${activeCar ? 'md:flex-row' : ''}`}>
                    {activeCar && classifier && (
                        <div className="md:order-2 md:w-[300px] md:shrink-0">
                            <CarWidget
                                data={classifier}
                                car={activeCar}
                                onOpenClassifier={onOpenClassifier ? (prefill) => {
                                    onClose();
                                    onOpenClassifier(prefill);
                                } : null}
                            />
                        </div>
                    )}

                    <div className="min-w-0 flex-1 md:order-1">
                        {term.length < 2 && (
                            <div className="px-3 py-10 text-center text-[13px] text-slate-400">
                                Введите минимум два символа. Понимает опечатки,
                                раскладку и транслит — «rfvhb» найдёт Camry.
                            </div>
                        )}

                        {showEmpty && (
                            <div className="px-3 py-10 text-center text-[13px] text-slate-400">
                                Ничего не найдено по запросу «{term}»
                            </div>
                        )}

                        {brandModels.length > 1 && (
                            <div className="mb-2">
                                <div className="mb-1.5 flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                    <Car size={11} /> Модели {matchedBrand} в классификаторе
                                </div>
                                <div className="flex max-h-[96px] flex-wrap gap-1.5 overflow-y-auto px-1">
                                    {brandModels.map((car) => {
                                        const isActive = activeCar
                                            && activeCar.brand === car.brand
                                            && activeCar.model === car.model;
                                        return (
                                            <button
                                                key={`${car.brand}-${car.model}`}
                                                type="button"
                                                onClick={() => setPickedCar(car)}
                                                className={`rounded-lg px-2.5 py-1 text-[12px] font-medium transition ${
                                                    isActive
                                                        ? 'bg-indigo-600 text-white'
                                                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                                }`}
                                            >
                                                {car.model}
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>
                        )}

                        {items.length > 0 && (
                            <div>
                                <div className="mb-1.5 flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                    <FileText size={11} /> Статьи
                                </div>
                                <ul className="max-h-[52vh] space-y-0.5 overflow-y-auto">
                                    {items.map((item, index) => (
                                        <li key={item.id}>
                                            <button
                                                type="button"
                                                onClick={() => openArticle(item)}
                                                onMouseEnter={() => setSelectedIndex(index)}
                                                className={`flex w-full items-start gap-2.5 rounded-xl px-3 py-2 text-left transition ${
                                                    index === selectedIndex ? 'bg-indigo-50' : 'hover:bg-slate-50'
                                                }`}
                                            >
                                                <FileText size={15} className={`mt-0.5 shrink-0 ${
                                                    index === selectedIndex ? 'text-indigo-500' : 'text-slate-300'
                                                }`} />
                                                <span className="min-w-0 flex-1">
                                                    <span className="block truncate text-[13.5px] font-medium text-slate-900">
                                                        {item.title}
                                                    </span>
                                                    {item.snippet && (
                                                        <span
                                                            className="wiki-search-snippet mt-0.5 block text-[12px] leading-snug text-slate-500 [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2] overflow-hidden"
                                                            // Всё, кроме собранного нами <mark>, экранировано в snippetHtml.
                                                            dangerouslySetInnerHTML={{ __html: snippetHtml(item.snippet) }}
                                                        />
                                                    )}
                                                </span>
                                                {index === selectedIndex && (
                                                    <CornerDownLeft size={13} className="mt-1 shrink-0 text-indigo-400" />
                                                )}
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}
                    </div>
                </div>

                <div className="hidden items-center gap-3 border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400 sm:flex">
                    <span>↑↓ — по списку</span>
                    <span>Enter — открыть</span>
                    <span>Esc — закрыть</span>
                    <span className="ml-auto">Опечатки, раскладка и транслит — понимает</span>
                </div>
            </div>
        </div>,
        document.body,
    );
}
