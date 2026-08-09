import React, {
    useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { createPortal } from 'react-dom';
import axios from 'axios';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import {
    AlertTriangle, Car, Check, ChevronRight, CornerDownLeft, FileText,
    Loader2, MapPin, Quote, RotateCw, Search, X,
} from 'lucide-react';
import {
    APPLE_FONT, iosInput, iosBtnPrimary, IosBadge,
    IOS_MODAL_MOTION, IOS_MODAL_MOTION_REDUCED,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { matchBrand, matchCar } from './carMatch';
import useStableCallback from './useStableCallback';

/* Поиск по вики — порт search-modal.tsx исходной вики на примитивы портала.
 *
 * ФОРМА ВЗЯТА ИЗ ОРИГИНАЛА и она принципиальна: на десктопе это НЕ модалка.
 * Поле живёт в шапке раздела, при фокусе растёт (300 мс), а выдача выпадает
 * ПОД ним, ничего не затемняя, и расширяется влево, когда справа открывается
 * бар классификатора. Полноэкранный лист с подложкой остаётся только для
 * телефона — там выпадашка нечитаема, и в оригинале ровно так же.
 *
 * Два результата одного запроса:
 *   1. Статьи — сервер (/api/wiki/search): полнотекст + префикс + опечатки,
 *      строго в периметре видимости пользователя. Сервер отдаёт до трёх
 *      подсвеченных фрагментов на статью (highlights): первый идёт в строку
 *      статьи, остальные — в секцию «Совпадения в тексте», как в оригинале.
 *   2. Машина — локально: запрос прогоняется через варианты написания
 *      (транслит, раскладка, алиасы — searchText.js) по справочнику
 *      классификатора, и если это марка/модель, справа открывается бар
 *      с тарифами по городу и году.
 *
 * Справочник классификатора (106 КБ) грузится динамическим import при первом
 * обращении и кэшируется на модуль — в бандл вики он не входит.
 */

const YEAR_FLOOR = 1980;
const CURRENT_YEAR = new Date().getFullYear();
const MOBILE_WIDTH = 640;
const requiredYear = (baseYear, cityOffset) => Math.max(YEAR_FLOOR, baseYear + cityOffset);

let classifierDataPromise = null;
const loadClassifierData = () => {
    if (!classifierDataPromise) {
        classifierDataPromise = import('../classifier/classifier-data.json')
            .then((module) => module.default || module)
            // Кэшируется ПРОМИС, поэтому отказ без сброса залипал бы навсегда:
            // один сбой сети (или протухший чанк после выката на Pages) — и
            // распознавание машин выключено до перезагрузки вкладки.
            .catch((error) => { classifierDataPromise = null; throw error; });
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

/** Слово, по которому нашли, — из подсветки конкретного фрагмента. */
export const markedWord = (snippet, fallback) => {
    const match = /<mark>(.*?)<\/mark>/i.exec(String(snippet || ''));
    return (match && match[1].trim()) || fallback;
};

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

const SectionLabel = ({ icon: Icon, children }) => (
    <div className="mb-1.5 flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
        <Icon size={11} /> {children}
    </div>
);

/** Левая колонка: модели марки, статьи и совпадения в тексте.
 *
 * Одна и та же для выпадашки и для мобильного листа — иначе две верстки
 * неизбежно разъезжаются.
 */
function ResultsPane({
    term, rows, articleRows, fragmentRows, selectedIndex, onHover, onPick,
    brandModels, matchedBrand, activeCar, onPickCar,
    loading, failed, onRetry, classifierFailed, listRef, maxHeight,
}) {
    const showEmpty = !loading && !failed && term.length >= 2
        && rows.length === 0 && brandModels.length === 0;
    const rowClass = (index) => (index === selectedIndex ? 'bg-indigo-50' : 'hover:bg-slate-50');

    return (
        <div className="min-w-0 flex-1">
            {term.length < 2 && (
                <div className="px-3 py-8 text-center text-[13px] text-slate-400">
                    Введите минимум два символа. Понимает опечатки,
                    раскладку и транслит — «rfvhb» найдёт Camry.
                </div>
            )}

            {failed && (
                <div className="px-3 py-8 text-center text-[13px] text-slate-500">
                    Поиск не ответил.
                    <button
                        type="button"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={onRetry}
                        className="ml-1.5 inline-flex items-center gap-1 font-medium text-indigo-600 hover:text-indigo-700"
                    >
                        <RotateCw size={12} /> Повторить
                    </button>
                </div>
            )}

            {showEmpty && (
                <div className="px-3 py-8 text-center text-[13px] text-slate-400">
                    {/* Без «справа»: на телефоне бар классификатора идёт снизу. */}
                    {activeCar
                        ? <>Статей по запросу «{term}» нет —<br />ответ в карточке классификатора</>
                        : <>Ничего не найдено по запросу «{term}»</>}
                </div>
            )}

            {classifierFailed && term.length >= 2 && (
                <div className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-[11.5px] text-amber-800">
                    Справочник машин не загрузился — ищем только по статьям.
                </div>
            )}

            <div ref={listRef} className="overflow-y-auto" style={{ maxHeight }}>
                {brandModels.length > 1 && (
                    <div className="mb-2">
                        <SectionLabel icon={Car}>
                            Модели {matchedBrand} в классификаторе
                        </SectionLabel>
                        {/* Свой скроллер: у Toyota моделей под сотню, и без
                            ограничения они выдавливали «Статьи» за экран. */}
                        <ul className="max-h-[164px] space-y-0.5 overflow-y-auto pr-0.5">
                            {brandModels.map((car) => {
                                const isActive = activeCar
                                    && activeCar.brand === car.brand
                                    && activeCar.model === car.model;
                                return (
                                    <li key={`${car.brand}-${car.model}`}>
                                        <button
                                            type="button"
                                            onMouseDown={(e) => e.preventDefault()}
                                            onClick={() => onPickCar(car)}
                                            className={`flex w-full items-center justify-between gap-2 rounded-xl px-3 py-1.5 text-left transition ${
                                                isActive ? 'bg-indigo-50' : 'hover:bg-slate-50'
                                            }`}
                                        >
                                            <span className={`truncate text-[13px] ${
                                                isActive ? 'font-semibold text-indigo-700' : 'text-slate-700'
                                            }`}>
                                                {car.brand} {car.model}
                                            </span>
                                            {isActive && (
                                                <span className="shrink-0 rounded-md bg-indigo-100 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700">
                                                    Выбрано
                                                </span>
                                            )}
                                        </button>
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                )}

                {articleRows.length > 0 && (
                    <div>
                        <SectionLabel icon={FileText}>Статьи</SectionLabel>
                        <ul className="space-y-0.5">
                            {articleRows.map((row) => {
                                const index = rows.indexOf(row);
                                const { item } = row;
                                return (
                                    <li key={item.id} data-row={index}>
                                        <button
                                            type="button"
                                            onMouseDown={(e) => e.preventDefault()}
                                            onClick={() => onPick(row)}
                                            onMouseEnter={() => onHover(index)}
                                            className={`flex w-full items-start gap-2.5 rounded-xl px-3 py-2 text-left transition ${rowClass(index)}`}
                                        >
                                            <FileText size={15} className={`mt-0.5 shrink-0 ${
                                                index === selectedIndex ? 'text-indigo-500' : 'text-slate-300'
                                            }`} />
                                            <span className="min-w-0 flex-1">
                                                <span className="block truncate text-[13.5px] font-medium text-slate-900">
                                                    {item.title}
                                                </span>
                                                {item.snippet ? (
                                                    <span
                                                        className="wiki-search-snippet mt-0.5 block text-[12px] leading-snug text-slate-500 [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2] overflow-hidden"
                                                        // Всё, кроме собранного нами <mark>, экранировано в snippetHtml.
                                                        dangerouslySetInnerHTML={{ __html: snippetHtml(item.snippet) }}
                                                    />
                                                ) : item.summary ? (
                                                    /* Сниппета нет — совпало в заголовке или алиасах,
                                                       подсвечивать в тексте нечего. Показываем описание,
                                                       а не выдуманный кусок статьи. */
                                                    <span className="mt-0.5 block truncate text-[12px] leading-snug text-slate-400">
                                                        {item.summary}
                                                    </span>
                                                ) : null}
                                            </span>
                                            {index === selectedIndex && (
                                                <CornerDownLeft size={13} className="mt-1 shrink-0 text-indigo-400" />
                                            )}
                                        </button>
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                )}

                {fragmentRows.length > 0 && (
                    <div className="mt-3">
                        <SectionLabel icon={Quote}>Совпадения в тексте</SectionLabel>
                        <ul className="space-y-1">
                            {fragmentRows.map((row) => {
                                const index = rows.indexOf(row);
                                return (
                                    <li key={`${row.item.id}-${row.index}`} data-row={index}>
                                        <button
                                            type="button"
                                            onMouseDown={(e) => e.preventDefault()}
                                            onClick={() => onPick(row)}
                                            onMouseEnter={() => onHover(index)}
                                            className={`w-full rounded-xl px-3 py-2 text-left transition ${rowClass(index)}`}
                                        >
                                            <span className="mb-0.5 block truncate text-[11px] text-slate-400">
                                                из: <span className="font-medium text-slate-500">{row.item.title}</span>
                                            </span>
                                            <span
                                                className="wiki-search-snippet block border-l-2 border-slate-200 pl-2 text-[12px] leading-snug text-slate-500 [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2] overflow-hidden"
                                                dangerouslySetInnerHTML={{ __html: snippetHtml(row.fragment) }}
                                            />
                                        </button>
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                )}
            </div>
        </div>
    );
}

export default function WikiSearch({ base, headers, onOpenArticle, onOpenClassifier }) {
    const [query, setQuery] = useState('');
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(false);
    const [failed, setFailed] = useState(false);
    const [retryTick, setRetryTick] = useState(0);
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [classifier, setClassifier] = useState(null);
    const [classifierFailed, setClassifierFailed] = useState(false);
    const [pickedCar, setPickedCar] = useState(null);

    // Десктоп — выпадашка под полем; телефон — полноэкранный лист.
    const [focused, setFocused] = useState(false);
    const [sheetOpen, setSheetOpen] = useState(false);

    const inputRef = useRef(null);
    const sheetInputRef = useRef(null);
    const containerRef = useRef(null);
    const listRef = useRef(null);
    const sheetListRef = useRef(null);
    const keyboardRef = useRef(false);
    const requestSeq = useRef(0);

    const reduceMotion = useReducedMotion();
    const motionSet = reduceMotion ? IOS_MODAL_MOTION_REDUCED : IOS_MODAL_MOTION;

    const openArticle = useStableCallback(onOpenArticle);
    const openClassifier = useStableCallback(onOpenClassifier);

    const term = query.trim();
    const active = focused || sheetOpen;

    // Справочник машин подгружается при первом обращении к поиску.
    useEffect(() => {
        if (!active || classifier) return undefined;
        let cancelled = false;
        loadClassifierData()
            .then((data) => { if (!cancelled) { setClassifier(data); setClassifierFailed(false); } })
            .catch(() => { if (!cancelled) setClassifierFailed(true); });
        return () => { cancelled = true; };
    }, [active, classifier]);

    // Закрылись — начинаем следующий поиск с чистого листа, как в оригинале.
    useEffect(() => {
        if (active) return;
        setQuery('');
        setItems([]);
        setSelectedIndex(0);
        setPickedCar(null);
        setFailed(false);
    }, [active]);

    // Статьи — с двух символов, дебаунс 250 мс, гонки отсекаются номером запроса.
    useEffect(() => {
        if (!active || term.length < 2) {
            // Инкремент и здесь: иначе уже летящий ответ на стёртый запрос
            // проходил проверку и рисовал результаты поверх подсказки.
            requestSeq.current += 1;
            setItems([]);
            setLoading(false);
            setFailed(false);
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
                    setFailed(false);
                    keyboardRef.current = false;
                })
                .catch(() => {
                    if (requestSeq.current !== seq) return;
                    // «Ничего не найдено» на сетевой сбой — худший из ответов:
                    // человек решает, что статьи нет, и идёт спрашивать в чат.
                    setItems([]);
                    setFailed(true);
                })
                .finally(() => { if (requestSeq.current === seq) setLoading(false); });
        }, 250);
        return () => { clearTimeout(timer); requestSeq.current += 1; };
    }, [active, term, base, headers, retryTick]);

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

    /* Плоский список того, по чему ходят стрелки: сначала статьи, следом
       дополнительные фрагменты («Совпадения в тексте»). В оригинале клавиатура
       так же шла сквозь обе секции. */
    const rows = useMemo(() => {
        const articles = items.map((item) => ({ kind: 'article', item }));
        const fragments = [];
        items.forEach((item) => {
            (item.highlights || []).slice(1).forEach((fragment, index) => {
                fragments.push({ kind: 'fragment', item, fragment, index });
            });
        });
        return articles.concat(fragments);
    }, [items]);

    const articleRows = useMemo(() => rows.filter((r) => r.kind === 'article'), [rows]);
    const fragmentRows = useMemo(() => rows.filter((r) => r.kind === 'fragment'), [rows]);

    const close = useCallback(() => {
        setFocused(false);
        setSheetOpen(false);
        inputRef.current?.blur();
    }, []);

    const pickRow = useCallback((row) => {
        const source = row.kind === 'fragment' ? row.fragment : row.item.snippet;
        close();
        openArticle(row.item.slug, markedWord(source, term));
    }, [close, openArticle, term]);

    /* Прокрутка выделенного — ТОЛЬКО под стрелками. Без этого условия любая
       новая выдача проматывала список к первой статье, унося за верхний край
       заголовок секции и список моделей марки. */
    useEffect(() => {
        if (!keyboardRef.current) return;
        const box = sheetOpen ? sheetListRef.current : listRef.current;
        box?.querySelector(`[data-row="${selectedIndex}"]`)
            ?.scrollIntoView({ block: 'nearest' });
    }, [selectedIndex, sheetOpen]);

    // Новая выдача — список к началу.
    useEffect(() => {
        const box = sheetOpen ? sheetListRef.current : listRef.current;
        if (box) box.scrollTop = 0;
    }, [items, sheetOpen]);

    const onKeyDown = useCallback((e) => {
        if (e.key === 'Escape') { close(); return; }
        if (!rows.length) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            keyboardRef.current = true;
            setSelectedIndex((i) => (i + 1) % rows.length);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            keyboardRef.current = true;
            setSelectedIndex((i) => (i - 1 + rows.length) % rows.length);
        } else if (e.key === 'Enter' && rows[selectedIndex]) {
            e.preventDefault();
            pickRow(rows[selectedIndex]);
        }
    }, [rows, selectedIndex, pickRow, close]);

    /* ⌘K / Ctrl+K — как в оригинале: на десктопе ставит фокус в поле, на
       телефоне открывает лист. Слушатель живёт только у смонтированного
       раздела, поэтому с другими разделами не конфликтует. Внутри редактора
       статьи сочетание не трогаем: в TipTap Ctrl+K — вставка ссылки. */
    useEffect(() => {
        const onKey = (e) => {
            const isHotkey = (e.metaKey || e.ctrlKey) && String(e.key).toLowerCase() === 'k';
            if (isHotkey) {
                if (e.target?.closest?.('.ProseMirror, [contenteditable="true"]')) return;
                e.preventDefault();
                if (window.innerWidth < MOBILE_WIDTH) {
                    setSheetOpen((prev) => !prev);
                    return;
                }
                setFocused(true);
                inputRef.current?.focus();
                return;
            }
            if (e.key === 'Escape') {
                // Открытый список CustomSelect («Город») гасит себя сам —
                // внутренний слой пропускаем вперёд.
                if (document.querySelector('[role="listbox"]')) return;
                close();
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [close]);

    // Клик мимо — выпадашка закрывается. Внутри неё мышь гасится onMouseDown,
    // поэтому фокус из поля не уходит и выбор строки работает с первого клика.
    useEffect(() => {
        if (!focused) return undefined;
        const onDown = (e) => {
            if (containerRef.current && !containerRef.current.contains(e.target)) setFocused(false);
        };
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
    }, [focused]);

    useEffect(() => {
        if (sheetOpen) setTimeout(() => sheetInputRef.current?.focus(), 40);
    }, [sheetOpen]);

    const onHover = (index) => { if (!keyboardRef.current) setSelectedIndex(index); };
    const retry = () => setRetryTick((n) => n + 1);

    const paneProps = {
        term, rows, articleRows, fragmentRows, selectedIndex,
        onHover, onPick: pickRow, brandModels, matchedBrand, activeCar,
        onPickCar: setPickedCar, loading, failed, onRetry: retry, classifierFailed,
    };

    const carPane = activeCar && classifier ? (
        <CarWidget
            data={classifier}
            car={activeCar}
            onOpenClassifier={onOpenClassifier ? (prefill) => {
                close();
                openClassifier(prefill);
            } : null}
        />
    ) : null;

    return (
        <div
            ref={containerRef}
            className="relative order-3 w-full sm:order-none sm:w-auto"
            onMouseMove={() => { keyboardRef.current = false; }}
        >
            {/* Телефон: поле-заглушка, по нажатию открывается лист */}
            <button
                type="button"
                onClick={() => setSheetOpen(true)}
                aria-haspopup="dialog"
                aria-expanded={sheetOpen}
                className="flex w-full items-center gap-2.5 rounded-xl bg-white px-3.5 py-2.5 text-left shadow-sm ring-1 ring-slate-900/5 transition active:scale-[0.99] sm:hidden"
            >
                <Search size={15} className="shrink-0 text-slate-400" />
                <span className="min-w-0 flex-1 truncate text-[13.5px] text-slate-400">
                    Поиск по вики…
                </span>
            </button>

            {/* Десктоп: настоящее поле, растущее при фокусе — как в оригинале */}
            <div
                className={`hidden items-center gap-2.5 rounded-xl bg-white px-3.5 py-2.5 shadow-sm transition-[width,box-shadow] duration-300 ease-out sm:flex ${
                    focused
                        ? 'w-[380px] ring-2 ring-indigo-500/30 lg:w-[460px]'
                        : 'w-[240px] ring-1 ring-slate-900/5 hover:ring-slate-900/10 lg:w-[280px]'
                }`}
            >
                <Search size={15} className="shrink-0 text-slate-400" />
                <input
                    ref={inputRef}
                    /* wiki-focus-outside: правило доступности в wiki-theme.css
                       рисует контур вокруг САМОГО input, а фокус здесь
                       показывает кольцо всего поля — выходила двойная рамка.
                       Индикация фокуса не теряется, она снаружи. */
                    className="wiki-focus-outside min-w-0 flex-1 bg-transparent text-[13.5px] text-slate-900 outline-none placeholder:text-slate-400"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onFocus={() => setFocused(true)}
                    onKeyDown={onKeyDown}
                    placeholder="Поиск по вики…"
                    aria-label="Поиск по вики"
                    aria-expanded={focused && term.length >= 2}
                />
                {loading && <Loader2 size={14} className="shrink-0 animate-spin text-slate-400" />}
                {query ? (
                    <button
                        type="button"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => { setQuery(''); inputRef.current?.focus(); }}
                        className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200"
                        aria-label="Очистить"
                    >
                        <X size={11} />
                    </button>
                ) : (
                    <kbd className="hidden shrink-0 rounded-md border border-slate-200 px-1.5 py-0.5 text-[10.5px] font-medium text-slate-400 lg:block">
                        ⌘K
                    </kbd>
                )}
            </div>

            {/* Выпадашка: выходит ПОД полем, ничего не затемняя, и расширяется
                влево, когда справа появляется бар классификатора. */}
            <AnimatePresence>
                {focused && term.length >= 2 && (
                    <motion.div
                        initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.99 }}
                        animate={{
                            opacity: 1, y: 0, scale: 1,
                            transition: { duration: reduceMotion ? 0 : 0.16, ease: [0.16, 1, 0.3, 1] },
                        }}
                        exit={{
                            opacity: 0, y: 6, scale: 0.99,
                            transition: { duration: reduceMotion ? 0 : 0.12, ease: 'easeIn' },
                        }}
                        className={`absolute right-0 top-full z-40 mt-2 hidden max-w-[calc(100vw-3rem)] overflow-hidden rounded-2xl bg-white p-2.5 shadow-[0_18px_50px_rgba(15,23,42,0.16)] ring-1 ring-slate-900/10 transition-[width] duration-300 ease-out sm:flex ${
                            activeCar ? 'w-[720px] gap-3' : 'w-[460px]'
                        }`}
                    >
                        <ResultsPane {...paneProps} listRef={listRef} maxHeight="46vh" />
                        {carPane && (
                            <motion.div
                                initial={reduceMotion ? false : { opacity: 0 }}
                                animate={{ opacity: 1 }}
                                transition={{ duration: reduceMotion ? 0 : 0.2, delay: reduceMotion ? 0 : 0.1 }}
                                className="w-[290px] shrink-0"
                            >
                                {carPane}
                            </motion.div>
                        )}
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Телефон: полноэкранный лист. Выпадашка на 360 px нечитаема,
                в оригинале для этого случая тоже отдельная модалка. */}
            {createPortal(
                <AnimatePresence>
                    {sheetOpen && (
                        <div
                            key="wiki-search-sheet"
                            className="wiki-scope fixed inset-0 z-[80] sm:hidden"
                            style={{ fontFamily: APPLE_FONT }}
                        >
                            <motion.div
                                aria-hidden="true"
                                className="absolute inset-0 bg-slate-900/40"
                                onClick={close}
                                {...motionSet.backdrop}
                            />
                            <motion.div
                                {...motionSet.panel}
                                role="dialog"
                                aria-modal="true"
                                aria-label="Поиск по вики"
                                className="absolute inset-0 flex flex-col bg-white"
                            >
                                <div className="flex items-center gap-2.5 border-b border-slate-100 px-4 pb-3 pt-[calc(0.75rem+env(safe-area-inset-top))]">
                                    {loading
                                        ? <Loader2 size={17} className="shrink-0 animate-spin text-slate-400" />
                                        : <Search size={17} className="shrink-0 text-slate-400" />}
                                    <input
                                        ref={sheetInputRef}
                                        className="min-w-0 flex-1 bg-transparent text-[15px] text-slate-900 outline-none placeholder:text-slate-400"
                                        value={query}
                                        onChange={(e) => setQuery(e.target.value)}
                                        onKeyDown={onKeyDown}
                                        placeholder="Статья, марка или модель машины…"
                                        aria-label="Поисковый запрос"
                                    />
                                    <button
                                        type="button"
                                        onClick={close}
                                        className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-500"
                                        aria-label="Закрыть"
                                    >
                                        <X size={13} />
                                    </button>
                                </div>
                                <div className="flex-1 overflow-y-auto p-3">
                                    <ResultsPane {...paneProps} listRef={sheetListRef} maxHeight="none" />
                                    {carPane && <div className="mt-3">{carPane}</div>}
                                </div>
                            </motion.div>
                        </div>
                    )}
                </AnimatePresence>,
                document.body,
            )}
        </div>
    );
}
