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

/* Поиск по вики — порт search-modal.tsx исходной вики на примитивы портала.
 *
 * Два результата одного запроса:
 *   1. Статьи — сервер (/api/wiki/search): полнотекст + префикс + опечатки,
 *      строго в периметре видимости пользователя. Сервер отдаёт до трёх
 *      подсвеченных фрагментов на статью (highlights) — первый идёт в строку
 *      статьи, остальные собираются в секцию «Совпадения в тексте», как в
 *      оригинале: каждый фрагмент — свой переход в статью со своим словом.
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

export default function WikiSearchModal({ open, onClose, base, headers,
                                          onOpenArticle, onOpenClassifier }) {
    const [query, setQuery] = useState('');
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(false);
    const [failed, setFailed] = useState(false);
    const [retryTick, setRetryTick] = useState(0);
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [classifier, setClassifier] = useState(null);
    const [classifierFailed, setClassifierFailed] = useState(false);
    const [pickedCar, setPickedCar] = useState(null);
    const inputRef = useRef(null);
    const listRef = useRef(null);
    const keyboardRef = useRef(false);
    const requestSeq = useRef(0);

    const reduceMotion = useReducedMotion();
    const motionSet = reduceMotion ? IOS_MODAL_MOTION_REDUCED : IOS_MODAL_MOTION;

    // Справочник машин подгружается при первом открытии.
    useEffect(() => {
        if (!open || classifier) return undefined;
        let cancelled = false;
        loadClassifierData()
            .then((data) => { if (!cancelled) { setClassifier(data); setClassifierFailed(false); } })
            .catch(() => { if (!cancelled) setClassifierFailed(true); });
        return () => { cancelled = true; };
    }, [open, classifier]);

    /* Сброс — только когда панель ДОсхлопнулась (onExitComplete у
       AnimatePresence). Со сбросом по !open пользователь 120 мс наблюдал бы,
       как из уезжающего окна исчезает его же запрос, а панель одновременно
       сужается обратно с 48rem до 36rem. */
    const resetState = useCallback(() => {
        setQuery('');
        setItems([]);
        setSelectedIndex(0);
        setPickedCar(null);
        setFailed(false);
    }, []);

    const term = query.trim();

    // Статьи — с двух символов, дебаунс 250 мс, гонки отсекаются номером запроса.
    useEffect(() => {
        if (!open || term.length < 2) {
            // Инкремент и здесь: раньше счётчик двигался ТОЛЬКО при отправке,
            // поэтому уже летящий ответ на стёртый запрос проходил проверку и
            // рисовал результаты рядом с подсказкой «введите два символа».
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
        // Инвалидация в cleanup закрывает и смену base/headers, и перебивку
        // запроса новым на полпути.
        return () => { clearTimeout(timer); requestSeq.current += 1; };
    }, [open, term, base, headers, retryTick]);

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

    const openRow = useCallback((row) => {
        const source = row.kind === 'fragment' ? row.fragment : row.item.snippet;
        onClose();
        onOpenArticle?.(row.item.slug, markedWord(source, term));
    }, [onClose, onOpenArticle, term]);

    // Стрелки обязаны подтягивать выделенное в видимую часть: список — свой
    // скроллер (max-h-[52vh]), и с восьмого нажатия человек жал вслепую.
    useEffect(() => {
        listRef.current?.querySelector(`[data-row="${selectedIndex}"]`)
            ?.scrollIntoView({ block: 'nearest' });
    }, [selectedIndex, rows]);

    const onKeyDown = useCallback((e) => {
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
            openRow(rows[selectedIndex]);
        }
    }, [rows, selectedIndex, openRow]);

    /* Escape на документе, а не на поле ввода: стоило кликнуть в «Год авто»
       или в список моделей — и фокус уходил из input, после чего Escape не
       делал ничего. Открытый список CustomSelect гасит себя сам, поэтому
       внутренний слой пропускаем вперёд. */
    useEffect(() => {
        if (!open) return undefined;
        const onEscape = (e) => {
            if (e.key !== 'Escape') return;
            if (document.querySelector('[role="listbox"]')) return;
            onClose();
        };
        document.addEventListener('keydown', onEscape);
        return () => document.removeEventListener('keydown', onEscape);
    }, [open, onClose]);

    // Возврат фокуса на то, откуда открыли: иначе клавиатурный пользователь
    // после закрытия оказывается в начале документа.
    useEffect(() => {
        if (!open) return undefined;
        const trigger = document.activeElement;
        return () => { if (trigger instanceof HTMLElement) trigger.focus(); };
    }, [open]);

    /* Пустую выдачу показываем И когда справа открыт бар машины: иначе левая
       колонка остаётся немой пустотой шириной в пол-окна. В оригинале строка
       «Ничего не найдено» жила в том же списке и рисовалась рядом с баром. */
    const showEmpty = open && !loading && !failed && term.length >= 2
        && rows.length === 0 && brandModels.length === 0;

    const articleRows = rows.filter((row) => row.kind === 'article');
    const fragmentRows = rows.filter((row) => row.kind === 'fragment');
    const rowIndex = (row) => rows.indexOf(row);

    const rowClass = (index) => (index === selectedIndex ? 'bg-indigo-50' : 'hover:bg-slate-50');
    const onRowHover = (index) => {
        // Сброс по РЕАЛЬНОМУ движению мыши: при прокрутке колесом под
        // неподвижным курсором mousemove не приходит, а mouseenter приходит —
        // и выделение телепортировалось под курсор посреди навигации стрелками.
        if (!keyboardRef.current) setSelectedIndex(index);
    };

    return createPortal(
        <AnimatePresence onExitComplete={resetState}>
            {open && (
                <div
                    key="wiki-search"
                    className="wiki-scope fixed inset-0 z-[80] overflow-y-auto p-3 sm:p-6"
                    style={{ fontFamily: APPLE_FONT }}
                    onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
                    onMouseMove={() => { keyboardRef.current = false; }}
                >
                    {/* Затемнение отдельным слоем: прозрачность общего корня
                        перемножилась бы с панелью и та выцветала бы вместе с
                        фоном. pointer-events-none — клик мимо панели обязан
                        долетать до корня, который его и ловит. */}
                    <motion.div
                        aria-hidden="true"
                        className="pointer-events-none fixed inset-0 bg-slate-900/40 backdrop-blur-[2px]"
                        {...motionSet.backdrop}
                    />

                    {/* Ширина едет только по max-width и только 300 мс — как в
                        оригинале. transition-all здесь запрещён: он потянул бы
                        за собой тень и кольцо, а это уже шум. */}
                    <motion.div
                        {...motionSet.panel}
                        onAnimationComplete={(definition) => {
                            // Фокус ровно тогда, когда окно приехало: на iOS
                            // фокус в поле посреди transform-анимации иногда
                            // дёргает вьюпорт под клавиатуру.
                            if (definition === 'visible') inputRef.current?.focus();
                        }}
                        role="dialog"
                        aria-modal="true"
                        aria-label="Поиск по вики"
                        className={`relative mx-auto mt-[4vh] w-full overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-slate-900/5 transition-[max-width] duration-300 ease-out ${
                            activeCar ? 'max-w-3xl' : 'max-w-xl'
                        }`}
                    >

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

                        {/* Тело: слева результаты, справа бар машины.
                            На мобильном результаты идут ПЕРВЫМИ — бар машины
                            занимает почти весь экран и выдавливал статьи за
                            нижнюю границу. */}
                        <div className={`flex flex-col gap-3 p-3 ${activeCar ? 'md:flex-row' : ''}`}>
                            <div className="min-w-0 flex-1">
                                {term.length < 2 && (
                                    <div className="px-3 py-10 text-center text-[13px] text-slate-400">
                                        Введите минимум два символа. Понимает опечатки,
                                        раскладку и транслит — «rfvhb» найдёт Camry.
                                    </div>
                                )}

                                {failed && (
                                    <div className="px-3 py-10 text-center text-[13px] text-slate-500">
                                        Поиск не ответил.
                                        <button
                                            type="button"
                                            onClick={() => setRetryTick((n) => n + 1)}
                                            className="ml-1.5 inline-flex items-center gap-1 font-medium text-indigo-600 hover:text-indigo-700"
                                        >
                                            <RotateCw size={12} /> Повторить
                                        </button>
                                    </div>
                                )}

                                {showEmpty && (
                                    <div className="px-3 py-10 text-center text-[13px] text-slate-400">
                                        {activeCar
                                            ? <>Статей по запросу «{term}» нет —<br />ответ справа, в классификаторе</>
                                            : <>Ничего не найдено по запросу «{term}»</>}
                                    </div>
                                )}

                                {classifierFailed && term.length >= 2 && (
                                    <div className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-[11.5px] text-amber-800">
                                        Справочник машин не загрузился — ищем только по статьям.
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

                                <div ref={listRef} className="max-h-[52vh] overflow-y-auto">
                                    {articleRows.length > 0 && (
                                        <div>
                                            <div className="mb-1.5 flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                                <FileText size={11} /> Статьи
                                            </div>
                                            <ul className="space-y-0.5">
                                                {articleRows.map((row) => {
                                                    const index = rowIndex(row);
                                                    const { item } = row;
                                                    return (
                                                        <li key={item.id} data-row={index}>
                                                            <button
                                                                type="button"
                                                                onClick={() => openRow(row)}
                                                                onMouseEnter={() => onRowHover(index)}
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
                                            <div className="mb-1.5 flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                                <Quote size={11} /> Совпадения в тексте
                                            </div>
                                            <ul className="space-y-1">
                                                {fragmentRows.map((row) => {
                                                    const index = rowIndex(row);
                                                    return (
                                                        <li key={`${row.item.id}-${row.index}`} data-row={index}>
                                                            <button
                                                                type="button"
                                                                onClick={() => openRow(row)}
                                                                onMouseEnter={() => onRowHover(index)}
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

                            {activeCar && classifier && (
                                /* Только прозрачность: пока панель 300 мс едет по
                                   ширине, любое движение бара вбок читалось бы
                                   как второй, конкурирующий жест. */
                                <motion.div
                                    initial={reduceMotion ? false : { opacity: 0 }}
                                    animate={{ opacity: 1 }}
                                    transition={{ duration: reduceMotion ? 0 : 0.2, delay: reduceMotion ? 0 : 0.1 }}
                                    className="md:w-[300px] md:shrink-0"
                                >
                                    <CarWidget
                                        data={classifier}
                                        car={activeCar}
                                        onOpenClassifier={onOpenClassifier ? (prefill) => {
                                            onClose();
                                            onOpenClassifier(prefill);
                                        } : null}
                                    />
                                </motion.div>
                            )}
                        </div>

                        <div className="hidden items-center gap-3 border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400 sm:flex">
                            <span>↑↓ — по списку</span>
                            <span>Enter — открыть</span>
                            <span>Esc — закрыть</span>
                            <span className="ml-auto">Опечатки, раскладку и транслит — понимает</span>
                        </div>
                    </motion.div>
                </div>
            )}
        </AnimatePresence>,
        document.body,
    );
}
