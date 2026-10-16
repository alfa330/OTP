import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import axios from 'axios';
import {
    ArrowRight, ArrowRightLeft, Check, FileText, Film, FolderInput, Image as ImageIcon, LayoutGrid, Loader2,
    Megaphone, Plus, Search, Smartphone, Sparkles, Trash2, Upload, X,
} from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosGroupLabel, iosInput, IosHint, IosSegmented } from '../ui/ios';
import { startFileDownload } from '../../utils/fileDownload';
import ProductionViewer from './ProductionViewer';
import { Avatar, MaterialTile, UploadRow } from './ProductionTaskSheet';
import { uploadOwnerId, uploadStore } from './productionUpload';
import {
    HISTORY_ACTION_LABELS, LIBRARY_CATEGORIES, fileKindOf, formatBytes, formatShortDate, formatTime, groupByDay,
    libraryAccept, libraryCategory, shortName, videosLabel,
} from './productionMeta';

/*
 * «Медиатека» раздела «Продакшн»: просто загрузить файл, без задачи.
 *
 * Просьбы владельца 16.10.2026: «как можно просто загружать ролики — данная
 * возможность тоже должна быть на сайте»; категории — баннеры (фото),
 * анимационные ролики, рекламные ролики, рилсы; «сделай разделы более красивыми
 * переключатели, историю кто когда добавлял».
 *
 * Категории — плитки, как списки в «Напоминаниях» iOS: значок, число файлов,
 * название и обычный размер файла (его назвал владелец). «История» — кто и когда
 * добавил, перенёс или удалил файл; она отбирается той же плиткой и поиском.
 *
 * Загрузка — тот же путь, что у материалов задачи (productionUpload.js): прямо в
 * облако, с докачкой и сверкой. В выбранную категорию файл ложится сразу; из «Все»
 * картинки уходят в «Баннеры» (других картиночных категорий нет), а про ролики
 * экран спрашивает, куда их положить: анимационный он или рекламный, по файлу не
 * понять.
 *
 * Видит и загружает тот, у кого в разделе охват «всё» — маркетинг и админ со
 * всеми задачами. Переносят и удаляют загрузивший, глава маркетинга и
 * супер-админ (сервер).
 */

const PAGE = 30;
const HISTORY_PAGE = 50;

const errorText = (error, fallback) => error?.response?.data?.error || error?.message || fallback;

/* Значок и цвет кружка у каждой плитки — как у списков «Напоминаний». */
const CATEGORY_LOOK = {
    '': { icon: LayoutGrid, tone: 'bg-slate-500' },
    banner: { icon: ImageIcon, tone: 'bg-orange-500' },
    animation: { icon: Sparkles, tone: 'bg-violet-500' },
    ad: { icon: Megaphone, tone: 'bg-blue-500' },
    reels: { icon: Smartphone, tone: 'bg-pink-500' },
};

const TILES = [
    { value: '', label: 'Все файлы', short: 'Все файлы', note: '' },
    ...LIBRARY_CATEGORIES,
];

const VIDEO_CATEGORIES = LIBRARY_CATEGORIES.filter((category) => category.kind === 'video');

const Dot = ({ value, size = 'md', className = '' }) => {
    const look = CATEGORY_LOOK[value] || CATEGORY_LOOK[''];
    const Icon = look.icon;
    const big = size === 'md';
    return (
        <span className={`grid shrink-0 place-items-center rounded-full text-white ${look.tone} ${
            big ? 'h-7 w-7 sm:h-8 sm:w-8 xl:h-9 xl:w-9' : 'h-[18px] w-[18px]'
        } ${className}`}
        >
            <Icon size={big ? 15 : 10} strokeWidth={big ? 2 : 2.4} />
        </span>
    );
};

/* Плитка-переключатель категории, как списки «Напоминаний»: значок и число
   сверху, название и обычный размер файла снизу. На телефоне — компактной
   строкой «значок · название · число»: пять больших плиток заняли бы пол-экрана
   до самих файлов. Выбор — мягкая голубая заливка с галочкой, наведение —
   лёгкий фон (правило владельца для плиток выбора).

   Раскладка — flex с перестановкой порядка, а не сетка: оболочка телефона
   (mobile-shell.css) схлопывает любую сетку с произвольными колонками в одну, а
   ряд с gap без скругления переводит в перенос. */
const CategoryTile = ({ tile, count, active, onSelect, className = '' }) => (
    <button
        type="button"
        role="tab"
        aria-selected={active}
        onClick={() => onSelect(tile.value)}
        className={`flex min-w-0 items-center gap-x-2.5 rounded-2xl px-3 py-2.5 text-left ring-1 transition active:scale-[0.97] sm:flex-wrap sm:items-start sm:p-3 xl:p-3.5 ${
            active ? 'bg-blue-50 ring-blue-200' : 'bg-white ring-slate-200/70 hover:bg-slate-50'
        } ${className}`}
    >
        <Dot value={tile.value} />
        <span className={`order-2 ml-auto text-[17px] font-semibold leading-none tabular-nums sm:order-none sm:text-[22px] xl:text-[26px] ${
            active ? 'text-blue-700' : 'text-slate-900'
        }`}
        >
            {Number.isFinite(count) ? count : ''}
        </span>
        <span className="order-1 min-w-0 grow basis-0 sm:order-none sm:mt-2.5 sm:basis-full">
            <span className="flex min-w-0 items-center">
                <span className={`min-w-0 truncate text-[14px] font-semibold xl:text-[15px] ${active ? 'text-blue-700' : 'text-slate-800'}`}>
                    <span className="lg:hidden">{tile.short}</span>
                    <span className="hidden lg:inline">{tile.label}</span>
                </span>
                {active && <Check size={15} strokeWidth={2.4} className="ml-1 shrink-0 text-blue-600" />}
            </span>
            <span className="mt-0.5 hidden min-h-[17px] truncate text-[12px] text-slate-500 sm:block xl:text-[12.5px]">{tile.note}</span>
        </span>
    </button>
);

/* Метка категории поверх кадра — только в «Все файлы»: в категории она очевидна. */
const CategoryBadge = ({ value }) => {
    const category = libraryCategory(value);
    if (!category) return null;
    return (
        <span className="inline-flex items-center gap-1 rounded-full bg-white/90 py-[2px] pl-[3px] pr-2 text-[10.5px] font-semibold text-slate-700 shadow-sm ring-1 ring-black/5 backdrop-blur">
            <Dot value={value} size="sm" />
            {category.short}
        </span>
    );
};

const CategoryChip = ({ value }) => {
    const category = libraryCategory(value);
    if (!category) return null;
    return (
        <span className="inline-flex max-w-full items-center gap-1 rounded-full bg-slate-100 py-[1px] pl-[2px] pr-2 text-[12px] font-medium text-slate-600">
            <Dot value={value} size="sm" />
            <span className="truncate">{category.short}</span>
        </span>
    );
};

const ACTION_LOOK = {
    added: { icon: Plus, tone: 'bg-blue-600' },
    moved: { icon: ArrowRightLeft, tone: 'bg-slate-500' },
    deleted: { icon: Trash2, tone: 'bg-rose-500' },
};

const HistoryRow = ({ event, onOpen }) => {
    const look = ACTION_LOOK[event.action] || ACTION_LOOK.added;
    const ActionIcon = look.icon;
    const material = event.material;
    const poster = material?.poster_url || (material?.kind === 'image' && material?.view_url) || null;
    const KindIcon = event.kind === 'video' ? Film : event.kind === 'image' ? ImageIcon : FileText;
    const deleted = event.action === 'deleted';
    const openable = Boolean(material) && !deleted;
    const Row = openable ? 'button' : 'div';
    return (
        <li>
            <Row
                {...(openable ? { type: 'button', onClick: () => onOpen(material) } : {})}
                className={`flex w-full items-center gap-3 px-3.5 py-2.5 text-left xl:gap-4 xl:px-4 xl:py-3 ${openable ? 'transition hover:bg-slate-50 active:bg-slate-100' : ''}`}
            >
                <span className="relative shrink-0">
                    <span className="grid h-11 w-16 place-items-center overflow-hidden rounded-lg bg-slate-100 text-slate-400 xl:h-12 xl:w-[72px]">
                        {poster ? <img src={poster} alt="" loading="lazy" className="h-full w-full object-cover" /> : <KindIcon size={18} strokeWidth={1.7} />}
                    </span>
                    <span className={`absolute -bottom-1 -right-1 grid h-[18px] w-[18px] place-items-center rounded-full text-white ring-2 ring-white ${look.tone}`}>
                        <ActionIcon size={10} strokeWidth={2.6} />
                    </span>
                </span>
                {/* На телефоне время — справа от имени файла, кто и размер — строкой ниже;
                    с планшета у них свои колонки справа. */}
                <span className="min-w-0 grow basis-0">
                    <span className="flex min-w-0 items-baseline">
                        <span className={`min-w-0 grow basis-0 truncate text-[14px] font-medium xl:text-[15px] ${deleted ? 'text-slate-500' : 'text-slate-900'}`} title={event.name}>
                            {event.name}
                        </span>
                        <span className="ml-2 shrink-0 text-[12.5px] tabular-nums text-slate-400 md:hidden">{formatTime(event.at)}</span>
                    </span>
                    <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-[12.5px] text-slate-500 xl:text-[13px]">
                        <span className={deleted ? 'text-rose-600' : 'text-slate-600'}>{HISTORY_ACTION_LABELS[event.action] || ''}</span>
                        {event.action === 'moved' && event.from_category && (
                            <>
                                <CategoryChip value={event.from_category} />
                                <ArrowRight size={12} className="text-slate-400" />
                            </>
                        )}
                        <CategoryChip value={event.category} />
                        <span className="hidden tabular-nums text-slate-400 md:inline">{formatBytes(event.size)}</span>
                    </span>
                    <span className="mt-0.5 block truncate text-[12px] tabular-nums text-slate-400 md:hidden">
                        {[event.actor ? shortName(event.actor.name) : '', formatBytes(event.size)].filter(Boolean).join(' · ')}
                    </span>
                </span>
                <span className="hidden w-[200px] shrink-0 items-center gap-2 md:flex xl:w-[240px]">
                    {event.actor ? (
                        <>
                            <Avatar person={event.actor} size={26} />
                            <span className="truncate text-[13px] text-slate-700 xl:text-[14px]">{event.actor.name}</span>
                        </>
                    ) : <span className="text-[13px] text-slate-400">—</span>}
                </span>
                <span className="hidden w-11 shrink-0 text-right text-[12.5px] tabular-nums text-slate-400 md:block xl:text-[13px]">{formatTime(event.at)}</span>
            </Row>
        </li>
    );
};

const ProductionLibrary = ({ apiBaseUrl, headers, showToast }) => {
    const [category, setCategory] = useState('');
    const [view, setView] = useState('files');
    const [query, setQuery] = useState('');
    const [counts, setCounts] = useState(null);

    const [items, setItems] = useState(null);
    const [total, setTotal] = useState(0);
    const [error, setError] = useState('');
    const [loadingMore, setLoadingMore] = useState(false);

    const [events, setEvents] = useState(null);
    const [eventsTotal, setEventsTotal] = useState(0);
    const [eventsError, setEventsError] = useState('');
    const [eventsLoadingMore, setEventsLoadingMore] = useState(false);

    const [dragOver, setDragOver] = useState(false);
    // Ролики, брошенные в «Все файлы»: ждут, пока человек скажет, в какую они категорию.
    const [pendingVideos, setPendingVideos] = useState([]);
    // Просмотр помнит id и откуда открыт, а сами файлы берёт из свежего списка:
    // истекла подпись ссылки — список перечитан, и плеер получает новую.
    const [viewer, setViewer] = useState({ ids: [], index: -1, from: 'files' });

    const fileRef = useRef(null);
    const ticketRef = useRef(0);
    const historyTicketRef = useRef(0);
    const itemsRef = useRef(null);
    itemsRef.current = items;
    const eventsRef = useRef(null);
    eventsRef.current = events;
    const reloadTimerRef = useRef(null);

    const uploads = useSyncExternalStore(uploadStore.subscribe, uploadStore.snapshot, uploadStore.snapshot);
    const myUploads = useMemo(() => {
        const owner = uploadOwnerId();
        return uploads.filter((item) => item.library && (!item.ownerId || item.ownerId === owner));
    }, [uploads]);

    const params = useCallback((extra) => {
        const out = { ...extra };
        if (query.trim()) out.q = query.trim();
        if (category) out.category = category;
        return out;
    }, [query, category]);

    const loadFiles = useCallback(async ({ append = false } = {}) => {
        const ticket = ticketRef.current + 1;
        ticketRef.current = ticket;
        if (append) setLoadingMore(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/production/library`, {
                headers: headers(),
                params: params({ limit: PAGE, offset: append ? (itemsRef.current || []).length : 0 }),
            });
            if (ticketRef.current !== ticket) return;
            const rows = response.data?.items || [];
            setItems((prev) => (append ? [...(prev || []), ...rows] : rows));
            setTotal(Number(response.data?.total || 0));
            if (response.data?.counts) setCounts(response.data.counts);
            setError('');
        } catch (err) {
            if (ticketRef.current !== ticket) return;
            // Список уже на экране — фоновое обновление не прошло, но показанное верно.
            if (!itemsRef.current) setError(errorText(err, 'Не удалось загрузить медиатеку'));
        } finally {
            if (ticketRef.current === ticket) setLoadingMore(false);
        }
    }, [apiBaseUrl, headers, params]);

    const loadHistory = useCallback(async ({ append = false } = {}) => {
        const ticket = historyTicketRef.current + 1;
        historyTicketRef.current = ticket;
        if (append) setEventsLoadingMore(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/production/library/history`, {
                headers: headers(),
                params: params({ limit: HISTORY_PAGE, offset: append ? (eventsRef.current || []).length : 0 }),
            });
            if (historyTicketRef.current !== ticket) return;
            const rows = response.data?.events || [];
            setEvents((prev) => (append ? [...(prev || []), ...rows] : rows));
            setEventsTotal(Number(response.data?.total || 0));
            if (response.data?.counts) setCounts(response.data.counts);
            setEventsError('');
        } catch (err) {
            if (historyTicketRef.current !== ticket) return;
            if (!eventsRef.current) setEventsError(errorText(err, 'Не удалось загрузить историю'));
        } finally {
            if (historyTicketRef.current === ticket) setEventsLoadingMore(false);
        }
    }, [apiBaseUrl, headers, params]);

    const loadRef = useRef(null);
    loadRef.current = view === 'history' ? loadHistory : loadFiles;

    // Другая плитка или вкладка — другой список: прежний не показываем ни мгновения,
    // чтобы баннеры не мелькнули под выбранными «Рилсами». Поиск — с паузой на набор,
    // и найденное остаётся на экране, пока не пришло новое.
    useEffect(() => {
        // Ответ, который ещё летит за прежней плиткой, уже не годится: он лёг бы под новую.
        ticketRef.current += 1;
        historyTicketRef.current += 1;
        if (view === 'history') { setEvents(null); setEventsError(''); } else { setItems(null); setError(''); }
    }, [view, category]);
    useEffect(() => {
        const timer = setTimeout(() => loadRef.current(), query ? 300 : 0);
        return () => clearTimeout(timer);
    }, [view, category, query]);
    useEffect(() => () => window.clearTimeout(reloadTimerRef.current), []);

    const addFiles = (files, target) => {
        const list = Array.from(files || []);
        if (!list.length) return;
        uploadStore.add({
            library: true,
            category: target || null,
            files: list,
            apiBaseUrl,
            headers,
            // Пачка файлов — одно обновление списка, а не по запросу на каждый файл.
            onDone: () => {
                window.clearTimeout(reloadTimerRef.current);
                reloadTimerRef.current = window.setTimeout(() => loadRef.current(), 700);
            },
        });
    };

    /* Файлы без выбранной категории (выбор из «Все файлы» или брошенные туда):
       картинки — в «Баннеры», ролики ждут ответа «куда», прочее сразу получает
       понятный отказ строкой загрузки. */
    const sortIntoCategories = (files) => {
        const list = Array.from(files || []);
        const images = list.filter((file) => fileKindOf(file) === 'image');
        const videos = list.filter((file) => fileKindOf(file) === 'video');
        const others = list.filter((file) => !['image', 'video'].includes(fileKindOf(file)));
        if (images.length) addFiles(images, 'banner');
        if (others.length) addFiles(others, null);
        if (videos.length) setPendingVideos((prev) => [...prev, ...videos]);
    };

    const takeFiles = (files) => {
        if (category) addFiles(files, category);
        else sortIntoCategories(files);
    };

    const pickFiles = () => {
        const input = fileRef.current;
        if (!input) return;
        // accept ставится прямо перед открытием: React перерисовал бы его позже, чем откроется окно.
        input.accept = libraryAccept(category);
        input.click();
    };

    const placePending = (target) => {
        addFiles(pendingVideos, target);
        setPendingVideos([]);
    };

    const download = async (material) => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/production/materials/${material.id}/download`, { headers: headers() });
            startFileDownload(response.data?.url);
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось скачать файл'), 'error');
        }
    };

    const bump = (changes) => setCounts((prev) => {
        if (!prev) return prev;
        const next = { ...prev };
        Object.entries(changes).forEach(([key, delta]) => { next[key] = Math.max(0, Number(next[key] || 0) + delta); });
        return next;
    });

    const remove = async (material) => {
        try {
            await axios.delete(`${apiBaseUrl}/api/production/materials/${material.id}`, { headers: headers() });
            showToast?.('Файл удалён', 'success');
            setItems((prev) => (prev || []).filter((item) => item.id !== material.id));
            setTotal((prev) => Math.max(0, prev - 1));
            bump({ '': -1, ...(material.category ? { [material.category]: -1 } : {}) });
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось удалить файл'), 'error');
        }
    };

    const move = async (material, target) => {
        try {
            await axios.patch(`${apiBaseUrl}/api/production/library/${material.id}`, { category: target }, { headers: headers() });
            showToast?.(`Перенесено в «${libraryCategory(target)?.label || target}»`, 'success');
            if (category && category !== target) {
                // Файл ушёл из открытой категории — убираем его отсюда.
                setItems((prev) => (prev || []).filter((item) => item.id !== material.id));
                setTotal((prev) => Math.max(0, prev - 1));
            } else {
                setItems((prev) => (prev || []).map((item) => (item.id === material.id ? { ...item, category: target } : item)));
            }
            bump({ [target]: 1, ...(material.category ? { [material.category]: -1 } : {}) });
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось перенести файл'), 'error');
        }
    };

    const moveActions = (material) => {
        if (!material.can_delete) return [];
        return LIBRARY_CATEGORIES
            .filter((target) => target.kind === material.kind && target.value !== material.category)
            .map((target, index) => ({
                key: `move-${target.value}`,
                label: `В категорию «${target.short}»`,
                icon: FolderInput,
                separatorBefore: index === 0,
                onSelect: () => move(material, target.value),
            }));
    };

    const historyFiles = useMemo(() => {
        // Листать из истории — по файлам этой страницы, каждый по разу, в её порядке.
        const seen = new Set();
        return (events || []).map((event) => event.material).filter((row) => {
            if (!row || seen.has(row.id)) return false;
            seen.add(row.id);
            return true;
        });
    }, [events]);
    const openFile = (list, material, from) => {
        setViewer({ ids: list.map((row) => row.id), index: Math.max(0, list.findIndex((row) => row.id === material.id)), from });
    };
    const viewerItems = useMemo(() => {
        const byId = new Map((viewer.from === 'history' ? historyFiles : items || []).map((row) => [row.id, row]));
        return viewer.ids.map((id) => byId.get(id)).filter(Boolean);
    }, [viewer, historyFiles, items]);

    const tileCount = (value) => (counts ? Number(counts[value] || 0) : undefined);
    const current = libraryCategory(category);
    const filesEmpty = items && !items.length && !myUploads.length;
    const historyGroups = useMemo(() => groupByDay(events || []), [events]);

    const uploadHint = `${current
        ? `${current.label} — ${current.kind === 'image' ? 'картинки JPG, PNG, WebP, GIF, SVG, HEIC или TIFF' : 'ролики MP4, MOV, WebM, MKV или AVI'}.`
        : 'Картинки лягут в «Баннеры», а про ролики спросим, в какую они категорию.'
    } Файл до 2 ГБ едет прямо в облако; оборвётся связь — загрузка продолжится с того же места.`;

    return (
        <div
            className="space-y-4"
            onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
            onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setDragOver(false); }}
            onDrop={(event) => { event.preventDefault(); setDragOver(false); takeFiles(event.dataTransfer?.files); }}
        >
            <input
                ref={fileRef}
                type="file"
                multiple
                className="hidden"
                onChange={(event) => {
                    const files = Array.from(event.target.files || []);
                    event.target.value = '';
                    takeFiles(files);
                }}
            />

            <div role="tablist" aria-label="Категории" className="grid grid-cols-2 gap-2 sm:grid-cols-5 sm:gap-2.5 xl:gap-3">
                {TILES.map((tile) => (
                    <CategoryTile
                        key={tile.value || 'all'}
                        tile={tile}
                        count={tileCount(tile.value)}
                        active={category === tile.value}
                        onSelect={setCategory}
                        className={tile.value ? '' : 'col-span-2 sm:col-span-1'}
                    />
                ))}
            </div>

            <div className="flex flex-col gap-2.5 md:flex-row md:items-center">
                <label className="relative block md:w-80 xl:w-96">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        type="text"
                        inputMode="search"
                        enterKeyHint="search"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder={view === 'history' ? 'Поиск по файлу или сотруднику' : 'Поиск по названию файла'}
                        className={`${iosInput} pl-9 pr-9`}
                    />
                    {query && (
                        <button type="button" onClick={() => setQuery('')} aria-label="Очистить поиск"
                            className="absolute right-2 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-full text-slate-400 hover:bg-slate-200 hover:text-slate-600">
                            <X size={13} />
                        </button>
                    )}
                </label>
                <div className="flex items-center gap-2.5 md:contents">
                    <div className="min-w-0 flex-1 md:w-60 md:flex-none">
                        <IosSegmented
                            value={view}
                            onChange={setView}
                            options={[{ value: 'files', label: 'Файлы' }, { value: 'history', label: 'История' }]}
                            ariaLabel="Вид"
                            stretch
                            slide
                        />
                    </div>
                    <button type="button" className={`${iosBtnPrimary} shrink-0 md:ml-auto`} onClick={pickFiles}>
                        <Upload size={15} /> Загрузить
                    </button>
                </div>
            </div>

            {pendingVideos.length > 0 && (
                <div className="rounded-2xl bg-white p-4 ring-1 ring-blue-200 shadow-[0_2px_10px_rgba(37,99,235,0.08)]">
                    <div className="flex items-start justify-between gap-3">
                        <p className="text-[14.5px] font-semibold text-slate-900">
                            {pendingVideos.length === 1 ? `Куда положить «${pendingVideos[0].name}»?` : `Куда положить ${videosLabel(pendingVideos.length)}?`}
                        </p>
                        <button type="button" className={`${iosBtnGhost} -mr-2 -mt-1.5 shrink-0 px-2.5`} onClick={() => setPendingVideos([])}>
                            Отмена
                        </button>
                    </div>
                    <div role="group" aria-label="Категория для роликов" className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
                        {VIDEO_CATEGORIES.map((target) => (
                            <button
                                key={target.value}
                                type="button"
                                onClick={() => placePending(target.value)}
                                className="flex items-center gap-3 rounded-xl bg-slate-50 px-3 py-2.5 text-left ring-1 ring-slate-200/70 transition hover:bg-slate-100 active:scale-[0.98]"
                            >
                                <Dot value={target.value} />
                                <span className="min-w-0">
                                    <span className="block truncate text-[14px] font-semibold text-slate-800">{target.label}</span>
                                    <span className="block truncate text-[12px] text-slate-500">{target.note}</span>
                                </span>
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {myUploads.length > 0 && (
                <ul className="space-y-2">
                    {myUploads.map((item) => <UploadRow key={item.key} item={item} note={libraryCategory(item.category)?.short || ''} />)}
                </ul>
            )}

            {dragOver && !(view === 'files' && filesEmpty && !query) && (
                <div className="rounded-2xl border-2 border-dashed border-blue-300 bg-blue-50/70 px-4 py-3 text-center text-[13px] font-medium text-blue-700">
                    {current ? `Отпустите — файлы лягут в «${current.label}»` : 'Отпустите, чтобы загрузить'}
                </div>
            )}

            {view === 'files' && (
                <>
                    {error && !items && (
                        <div className="flex items-center justify-between gap-3 rounded-2xl bg-rose-50 px-4 py-3 text-[13.5px] text-rose-700">
                            <span>{error}</span>
                            <button type="button" className={`${iosBtnSecondary} shrink-0 py-1.5`} onClick={() => loadFiles()}>Повторить</button>
                        </div>
                    )}
                    {!items && !error && (
                        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5" aria-busy="true">
                            {[0, 1, 2, 3].map((index) => <div key={index} className="aspect-[4/3] animate-pulse rounded-2xl bg-slate-200/60" />)}
                        </div>
                    )}
                    {filesEmpty && !query && (
                        <div className={`relative rounded-2xl border-2 border-dashed transition ${
                            dragOver ? 'border-blue-300 bg-blue-50/70' : 'border-slate-200 bg-slate-50/60'
                        }`}
                        >
                            <button type="button" onClick={pickFiles} className="flex w-full flex-col items-center gap-2 px-6 py-12 text-center transition active:scale-[0.99]">
                                <span className="grid h-12 w-12 place-items-center rounded-full bg-white text-blue-600 shadow-sm ring-1 ring-slate-200/70">
                                    <Upload size={20} />
                                </span>
                                <span className="text-[15px] font-semibold text-slate-800">
                                    {current ? `В «${current.label}» пока пусто` : 'В медиатеке пока пусто'}
                                </span>
                                <span className="text-[13px] text-slate-500">Перетащите файлы сюда или нажмите, чтобы выбрать</span>
                            </button>
                            <span className="absolute right-3 top-3">
                                <IosHint align="right" text={uploadHint} />
                            </span>
                        </div>
                    )}
                    {filesEmpty && query && (
                        <div className="grid place-items-center rounded-2xl border border-dashed border-slate-200 px-6 py-14 text-center">
                            <p className="text-[15px] font-semibold text-slate-800">Ничего не нашлось</p>
                        </div>
                    )}
                    {items && items.length > 0 && (
                        <>
                            <div className="grid grid-cols-2 gap-x-3 gap-y-4 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
                                {items.map((item) => (
                                    <MaterialTile
                                        key={item.id}
                                        material={item}
                                        meta={[shortName(item.uploaded_by_name), formatShortDate(item.ready_at || item.created_at)].filter(Boolean).join(' · ')}
                                        badge={category ? null : <CategoryBadge value={item.category} />}
                                        actions={moveActions(item)}
                                        onOpen={(material) => openFile(items, material, 'files')}
                                        onDownload={download}
                                        onDelete={remove}
                                    />
                                ))}
                            </div>
                            {items.length < total && (
                                <div className="flex justify-center pt-1">
                                    <button type="button" className={iosBtnSecondary} disabled={loadingMore} onClick={() => loadFiles({ append: true })}>
                                        {loadingMore && <Loader2 size={15} className="animate-spin" />}
                                        Показать ещё
                                    </button>
                                </div>
                            )}
                        </>
                    )}
                </>
            )}

            {view === 'history' && (
                <>
                    {eventsError && !events && (
                        <div className="flex items-center justify-between gap-3 rounded-2xl bg-rose-50 px-4 py-3 text-[13.5px] text-rose-700">
                            <span>{eventsError}</span>
                            <button type="button" className={`${iosBtnSecondary} shrink-0 py-1.5`} onClick={() => loadHistory()}>Повторить</button>
                        </div>
                    )}
                    {!events && !eventsError && (
                        <div className="space-y-2" aria-busy="true">
                            {[0, 1, 2].map((index) => <div key={index} className="h-[66px] animate-pulse rounded-2xl bg-slate-200/60" />)}
                        </div>
                    )}
                    {events && !events.length && (
                        <div className="grid place-items-center rounded-2xl border border-dashed border-slate-200 px-6 py-14 text-center">
                            <p className="inline-flex items-center gap-2 text-[15px] font-semibold text-slate-800">
                                {query ? 'Ничего не нашлось' : 'История пока пуста'}
                                {!query && <IosHint text="Здесь видно, кто и когда добавил, перенёс или удалил файл медиатеки." />}
                            </p>
                        </div>
                    )}
                    {historyGroups.map((group) => (
                        <section key={group.label}>
                            <h3 className={iosGroupLabel}>{group.label}</h3>
                            <ul className="mt-1.5 divide-y divide-slate-100 overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                                {group.events.map((event) => <HistoryRow key={event.id} event={event} onOpen={(material) => openFile(historyFiles, material, 'history')} />)}
                            </ul>
                        </section>
                    ))}
                    {events && events.length < eventsTotal && (
                        <div className="flex justify-center pt-1">
                            <button type="button" className={iosBtnSecondary} disabled={eventsLoadingMore} onClick={() => loadHistory({ append: true })}>
                                {eventsLoadingMore && <Loader2 size={15} className="animate-spin" />}
                                Показать ещё
                            </button>
                        </div>
                    )}
                </>
            )}

            <ProductionViewer
                items={viewerItems}
                index={viewerItems.length ? Math.min(viewer.index, viewerItems.length - 1) : -1}
                onIndex={(index) => setViewer((prev) => ({ ...prev, index }))}
                onClose={() => setViewer({ ids: [], index: -1, from: 'files' })}
                onDownload={download}
                onExpired={() => loadRef.current()}
            />
        </div>
    );
};

export default ProductionLibrary;
