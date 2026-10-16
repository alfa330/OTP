import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import axios from 'axios';
import { Film, Image as ImageIcon, Loader2, MonitorSmartphone, Package, Printer, Search, Upload, X } from 'lucide-react';
import { APPLE_FONT, iosBtnSecondary, iosInput, IosHint, IosSegmented } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import ProductionLibrary from './ProductionLibrary';
import ProductionTaskSheet from './ProductionTaskSheet';
import { uploadStore } from './productionUpload';
import {
    BOARD_COLUMNS, DONE_GROUPS, dueInfo, filesLabel, formatDuration, formatShortDate, groupBoard, initialsOf,
    itemGroupOf,
} from './productionMeta';

/*
 * Раздел «Продакшн» (задача #316): рабочее место маркетинга поверх задач типа
 * «Продакшн» (#354).
 *
 * «Доска» — задачи по статусам: «К выполнению», «В работе», «На проверке».
 * «Готовое» — принятые задачи с их итоговыми материалами: поиск по названию,
 * отбор по виду (видео, digital, полиграфия, бренд). «Медиатека» — просто
 * загрузить баннер или ролик, без задачи, по категориям и с историей (видна
 * маркетингу и админам со всеми задачами).
 *
 * Задачу ставят в «Задачах» (там форма с брифом), а работают с ней здесь: в
 * карточке исполнитель загружает ролики и макеты и отправляет на проверку,
 * постановщик смотрит, принимает или возвращает. Это та же задача — её статус
 * виден в обоих разделах.
 */

// Края раздела — те же, что у «Оплаты счетов» и «Задач».
const SECTION_COLUMN = 'mx-auto w-full max-w-[2270px] px-3 sm:px-5 lg:px-7';
const DONE_PAGE = 30;

const GROUP_ICON = { video: Film, digital: MonitorSmartphone, print: Printer, brand: Package };

const errorText = (error, fallback) => error?.response?.data?.error || error?.message || fallback;

const Faces = ({ people = [] }) => {
    if (!people.length) return null;
    const shown = people.slice(0, 3);
    return (
        <span className="flex -space-x-1.5" title={people.map((person) => person.name).join(', ')}>
            {shown.map((person) => (person.avatar_url ? (
                <img key={person.id} src={person.avatar_url} alt="" className="h-[22px] w-[22px] rounded-full object-cover ring-2 ring-slate-100" />
            ) : (
                <span key={person.id} className="grid h-[22px] w-[22px] place-items-center rounded-full bg-slate-200 text-[9.5px] font-semibold text-slate-600 ring-2 ring-slate-100">
                    {initialsOf(person.name)}
                </span>
            )))}
            {people.length > shown.length && (
                <span className="grid h-[22px] min-w-[22px] place-items-center rounded-full bg-slate-100 px-1 text-[10px] font-semibold tabular-nums text-slate-500 ring-2 ring-slate-100">
                    +{people.length - shown.length}
                </span>
            )}
        </span>
    );
};

const Cover = ({ task }) => {
    const [broken, setBroken] = useState(false);
    const cover = task.cover;
    const poster = !broken && cover?.poster_url;
    const Icon = GROUP_ICON[itemGroupOf(task.item)] || ImageIcon;
    return (
        <span className="relative block aspect-[16/9] overflow-hidden bg-slate-100">
            {poster ? (
                <img src={poster} alt="" loading="lazy" onError={() => setBroken(true)} className="h-full w-full object-cover" />
            ) : (
                <span className="grid h-full w-full place-items-center text-slate-300">
                    <Icon size={28} strokeWidth={1.5} />
                </span>
            )}
            {cover?.kind === 'video' && cover.duration_seconds > 0 && (
                <span className="absolute bottom-1.5 right-1.5 rounded-md bg-black/60 px-1.5 py-[1px] text-[11px] font-medium tabular-nums text-white">
                    {formatDuration(cover.duration_seconds)}
                </span>
            )}
        </span>
    );
};

const TaskCard = ({ task, uploadsActive, onOpen }) => {
    const due = dueInfo(task);
    return (
        <button
            type="button"
            onClick={() => onOpen(task.id)}
            className="group block w-full overflow-hidden rounded-2xl bg-white text-left ring-1 ring-slate-200/70 transition hover:shadow-[0_2px_10px_rgba(15,23,42,0.06)] hover:ring-slate-300 active:scale-[0.99]"
        >
            {/* Обложка — только когда есть что показать: пустой серый прямоугольник
                у ещё не начатой задачи — это шум размером в полкарточки. */}
            {task.cover?.poster_url && <Cover task={task} />}
            <span className="block p-3">
                <span className="block text-[13.5px] font-semibold leading-snug text-slate-900 line-clamp-2">{task.subject}</span>
                {task.item_label && <span className="mt-0.5 block truncate text-[12px] text-slate-500">{task.item_label}</span>}
                <span className="mt-2.5 flex items-center gap-2">
                    <Faces people={task.assignees} />
                    <span className="flex min-w-0 flex-1 items-center justify-end gap-1.5">
                        {uploadsActive && (
                            <span className="inline-flex items-center gap-1 text-[11px] text-blue-600" title="Идёт загрузка файлов">
                                <Upload size={12} /> грузится
                            </span>
                        )}
                        {task.status === 'returned' && (
                            <span className="rounded-md bg-rose-50 px-1.5 py-[2px] text-[10.5px] font-semibold text-rose-600">Возврат</span>
                        )}
                        {task.version > 1 && <span className="text-[11px] tabular-nums text-slate-400">v{task.version}</span>}
                        {task.materials_count > 0 && (
                            <span className="text-[11px] tabular-nums text-slate-400">{filesLabel(task.materials_count)}</span>
                        )}
                        {due && (
                            <span className={`rounded-md px-1.5 py-[2px] text-[11px] font-medium tabular-nums ${
                                due.overdue ? 'bg-rose-50 text-rose-600' : 'bg-slate-100 text-slate-500'
                            }`}
                            >
                                {due.label}
                            </span>
                        )}
                    </span>
                </span>
            </span>
        </button>
    );
};

const BoardSkeleton = ({ narrow }) => (
    <div className={narrow ? 'space-y-3' : 'grid grid-cols-3 gap-3'} aria-busy="true">
        {(narrow ? [0, 1] : [0, 1, 2]).map((index) => (
            <div key={index} className={narrow ? 'h-56 animate-pulse rounded-2xl bg-slate-200/60' : 'h-80 animate-pulse rounded-2xl bg-slate-200/50'} />
        ))}
    </div>
);

const ProductionView = ({ apiBaseUrl, withAccessTokenHeader, showToast, focusRequest = null, onOpenTasks }) => {
    const headers = useCallback(() => (withAccessTokenHeader ? withAccessTokenHeader() : {}), [withAccessTokenHeader]);
    const isNarrow = useIsMobileShell();
    const [tab, setTab] = useState('board');
    // Есть ли у человека «Медиатека» — решает сервер (охват «всё»).
    const [libraryOpen, setLibraryOpen] = useState(false);
    const [board, setBoard] = useState(null);
    const [boardError, setBoardError] = useState('');
    const [mobileColumn, setMobileColumn] = useState('progress');
    const [openTaskId, setOpenTaskId] = useState(null);

    const [done, setDone] = useState(null);
    const [doneTotal, setDoneTotal] = useState(0);
    const [doneError, setDoneError] = useState('');
    const [doneLoading, setDoneLoading] = useState(false);
    const [query, setQuery] = useState('');
    const [group, setGroup] = useState('');
    const doneTicket = useRef(0);
    const boardTicket = useRef(0);
    const boardRef = useRef(null);
    boardRef.current = board;

    const uploads = useSyncExternalStore(uploadStore.subscribe, uploadStore.snapshot, uploadStore.snapshot);
    const uploadingTasks = useMemo(() => new Set(
        uploads.filter((item) => !['failed', 'done'].includes(item.state)).map((item) => item.taskId),
    ), [uploads]);

    const loadBoard = useCallback(async () => {
        const ticket = boardTicket.current + 1;
        boardTicket.current = ticket;
        try {
            const response = await axios.get(`${apiBaseUrl}/api/production/board`, { headers: headers() });
            if (boardTicket.current !== ticket) return;
            setBoard(response.data?.tasks || []);
            setBoardError('');
        } catch (error) {
            if (boardTicket.current !== ticket) return;
            // Доска уже на экране — фоновое обновление не удалось, но показанное верно
            // на момент прошлого ответа: красной плашки над рабочей доской не вешаем.
            if (!boardRef.current) setBoardError(errorText(error, 'Не удалось загрузить задачи'));
        }
    }, [apiBaseUrl, headers]);

    const loadDone = useCallback(async ({ append = false } = {}) => {
        const ticket = doneTicket.current + 1;
        doneTicket.current = ticket;
        setDoneLoading(true);
        try {
            const params = { limit: DONE_PAGE, offset: append ? (done || []).length : 0 };
            if (query.trim()) params.q = query.trim();
            if (group) params.group = group;
            const response = await axios.get(`${apiBaseUrl}/api/production/done`, { headers: headers(), params });
            if (doneTicket.current !== ticket) return;
            const rows = response.data?.tasks || [];
            setDone((prev) => (append ? [...(prev || []), ...rows] : rows));
            setDoneTotal(Number(response.data?.total || 0));
            setDoneError('');
        } catch (error) {
            if (doneTicket.current !== ticket) return;
            setDoneError(errorText(error, 'Не удалось загрузить готовое'));
        } finally {
            if (doneTicket.current === ticket) setDoneLoading(false);
        }
    }, [apiBaseUrl, headers, query, group, done]);

    useEffect(() => { loadBoard(); }, [loadBoard]);

    useEffect(() => {
        let alive = true;
        axios.get(`${apiBaseUrl}/api/production/access`, { headers: headers() })
            .then((response) => { if (alive) setLibraryOpen(Boolean(response.data?.library)); })
            .catch(() => {});
        return () => { alive = false; };
    }, [apiBaseUrl, headers]);

    // Поиск и отбор «Готового» — с паузой на набор, чтобы не слать запрос на каждую букву.
    const loadDoneRef = useRef(loadDone);
    loadDoneRef.current = loadDone;
    useEffect(() => {
        if (tab !== 'done') return undefined;
        const timer = setTimeout(() => loadDoneRef.current(), query ? 300 : 0);
        return () => clearTimeout(timer);
    }, [tab, query, group]);

    // Вернулись на вкладку браузера — доска могла измениться (сдали, приняли). Только
    // visibilitychange: focus срабатывает ещё и после окна выбора файла — лишний запрос.
    useEffect(() => {
        const onVisible = () => { if (!document.hidden) loadBoard(); };
        document.addEventListener('visibilitychange', onVisible);
        return () => document.removeEventListener('visibilitychange', onVisible);
    }, [loadBoard]);

    // Переход из «Задач» или из ссылки: открыть карточку нужной задачи.
    useEffect(() => {
        const id = Number(focusRequest?.taskId || 0);
        if (id > 0) setOpenTaskId(id);
    }, [focusRequest]);

    const grouped = useMemo(() => groupBoard(board || []), [board]);
    const refresh = useCallback(() => {
        loadBoard();
        if (tab === 'done' || done) loadDoneRef.current();
    }, [loadBoard, tab, done]);

    // «Доска», а не «В работе»: так называется одна из её колонок.
    const tabs = [
        { value: 'board', label: 'Доска' },
        { value: 'done', label: 'Готовое' },
        libraryOpen && { value: 'media', label: 'Медиатека' },
    ].filter(Boolean);

    const renderColumn = (column, { single = false } = {}) => {
        const items = grouped[column.id] || [];
        return (
            <div key={column.id} className={single ? 'flex flex-col gap-3' : 'flex min-w-0 flex-col rounded-2xl bg-slate-100/70 p-2'}>
                {!single && (
                    <header className="flex items-baseline gap-1.5 px-1.5 pb-2 pt-1">
                        <span className="text-[12.5px] font-semibold text-slate-700">{column.title}</span>
                        <span className="text-[11.5px] tabular-nums text-slate-400">{items.length}</span>
                    </header>
                )}
                {/* Широкая колонка (большой монитор) ставит карточки по две-три в ряд,
                    а не растягивает одну во всю ширину. */}
                <div className={single ? 'grid grid-cols-1 gap-3 min-[560px]:grid-cols-2' : 'grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-2'}>
                    {items.map((task) => (
                        <TaskCard key={task.id} task={task} uploadsActive={uploadingTasks.has(task.id)} onOpen={setOpenTaskId} />
                    ))}
                </div>
                {!items.length && (
                    <p className={`px-2 py-6 text-center text-[12.5px] text-slate-400 ${single ? 'rounded-2xl bg-white ring-1 ring-slate-200/70' : ''}`}>
                        {column.id === 'review' ? 'На проверке ничего нет' : 'Пусто'}
                    </p>
                )}
            </div>
        );
    };

    const boardEmpty = board && !board.length;

    return (
        <div className={`${SECTION_COLUMN} min-h-screen bg-gray-50 pb-12 pt-3 md:min-h-0`} style={{ fontFamily: APPLE_FONT }}>
            <header className="flex flex-wrap items-center justify-between gap-3 sm:min-h-[40px]">
                <h1 className="min-w-0 text-[22px] font-semibold tracking-tight text-slate-900">Продакшн</h1>
            </header>

            <div className="mt-4">
                {/* Крупный и с переездом плашки: это навигация раздела, а не мелкая настройка. */}
                <IosSegmented value={tab} options={tabs} onChange={setTab} ariaLabel="Раздел" size="lg" slide stretch={isNarrow} />
            </div>

            {tab === 'board' && (
                <div className="mt-4">
                    {boardError && !board && (
                        <div className="mb-3 flex items-center justify-between gap-3 rounded-2xl bg-rose-50 px-4 py-3 text-[13.5px] text-rose-700">
                            <span>{boardError}</span>
                            <button type="button" className={`${iosBtnSecondary} shrink-0 py-1.5`} onClick={loadBoard}>Повторить</button>
                        </div>
                    )}
                    {!board && !boardError && <BoardSkeleton narrow={isNarrow} />}
                    {boardEmpty && (
                        <div className="grid place-items-center rounded-2xl border border-dashed border-slate-200 px-6 py-14 text-center">
                            <p className="inline-flex items-center gap-2 text-[15px] font-semibold text-slate-800">
                                Задач продакшна пока нет
                                <IosHint text="Их ставят в «Задачах» с типом «Продакшн» — здесь они появятся сами." />
                            </p>
                            {onOpenTasks && (
                                <button type="button" className={`${iosBtnSecondary} mt-4`} onClick={() => onOpenTasks(null)}>
                                    Открыть «Задачи»
                                </button>
                            )}
                        </div>
                    )}
                    {board && board.length > 0 && (isNarrow ? (
                        <div className="space-y-3">
                            <IosSegmented
                                value={mobileColumn}
                                options={BOARD_COLUMNS.map((column) => ({
                                    value: column.id,
                                    label: `${column.title} ${(grouped[column.id] || []).length}`,
                                }))}
                                onChange={setMobileColumn}
                                stretch
                                slide
                                ariaLabel="Колонка"
                            />
                            {renderColumn(BOARD_COLUMNS.find((column) => column.id === mobileColumn) || BOARD_COLUMNS[0], { single: true })}
                        </div>
                    ) : (
                        <div className="grid grid-cols-3 items-start gap-3">
                            {BOARD_COLUMNS.map((column) => renderColumn(column))}
                        </div>
                    ))}
                </div>
            )}

            {tab === 'done' && (
                <div className="mt-4 space-y-3">
                    <div className="flex flex-col gap-2.5 md:flex-row md:items-center">
                        <label className="relative block md:w-80">
                            <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input
                                type="text"
                                inputMode="search"
                                enterKeyHint="search"
                                value={query}
                                onChange={(event) => setQuery(event.target.value)}
                                placeholder="Поиск по названию или файлу"
                                className={`${iosInput} pl-9 pr-9`}
                            />
                            {query && (
                                <button type="button" onClick={() => setQuery('')} aria-label="Очистить поиск"
                                    className="absolute right-2 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-full text-slate-400 hover:bg-slate-200 hover:text-slate-600">
                                    <X size={13} />
                                </button>
                            )}
                        </label>
                        <div className="overflow-x-auto [scrollbar-width:none]">
                            <IosSegmented value={group} options={DONE_GROUPS} onChange={setGroup} ariaLabel="Вид" className="min-w-max" slide />
                        </div>
                    </div>

                    {doneError && !done && (
                        <div className="flex items-center justify-between gap-3 rounded-2xl bg-rose-50 px-4 py-3 text-[13.5px] text-rose-700">
                            <span>{doneError}</span>
                            <button type="button" className={`${iosBtnSecondary} shrink-0 py-1.5`} onClick={() => loadDone()}>Повторить</button>
                        </div>
                    )}
                    {!done && !doneError && (
                        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5" aria-busy="true">
                            {[0, 1, 2, 3].map((index) => <div key={index} className="aspect-[4/3] animate-pulse rounded-2xl bg-slate-200/60" />)}
                        </div>
                    )}
                    {done && !done.length && (
                        <div className="grid place-items-center rounded-2xl border border-dashed border-slate-200 px-6 py-14 text-center">
                            <p className="inline-flex items-center gap-2 text-[15px] font-semibold text-slate-800">
                                {query || group ? 'Ничего не нашлось' : 'Готового пока нет'}
                                {!(query || group) && <IosHint text="Сюда попадает всё, что постановщик принял." />}
                            </p>
                        </div>
                    )}
                    {done && done.length > 0 && (
                        <>
                            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
                                {done.map((task) => (
                                    <button
                                        key={task.id}
                                        type="button"
                                        onClick={() => setOpenTaskId(task.id)}
                                        className="group block overflow-hidden rounded-2xl bg-white text-left ring-1 ring-slate-200/70 transition hover:shadow-[0_2px_10px_rgba(15,23,42,0.06)] hover:ring-slate-300 active:scale-[0.99]"
                                    >
                                        <Cover task={task} />
                                        <span className="block p-3">
                                            <span className="block text-[13px] font-semibold leading-snug text-slate-900 line-clamp-2">{task.subject}</span>
                                            <span className="mt-0.5 block truncate text-[11.5px] text-slate-500">
                                                {[task.item_label, task.accepted_at ? formatShortDate(task.accepted_at) : ''].filter(Boolean).join(' · ')}
                                            </span>
                                        </span>
                                    </button>
                                ))}
                            </div>
                            {done.length < doneTotal && (
                                <div className="flex justify-center pt-1">
                                    <button type="button" className={iosBtnSecondary} disabled={doneLoading} onClick={() => loadDone({ append: true })}>
                                        {doneLoading && <Loader2 size={15} className="animate-spin" />}
                                        Показать ещё
                                    </button>
                                </div>
                            )}
                        </>
                    )}
                </div>
            )}

            {tab === 'media' && libraryOpen && (
                <div className="mt-4">
                    <ProductionLibrary apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
                </div>
            )}

            <ProductionTaskSheet
                taskId={openTaskId}
                open={Boolean(openTaskId)}
                onClose={() => setOpenTaskId(null)}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                showToast={showToast}
                onChanged={refresh}
                onOpenInTasks={onOpenTasks ? (id) => { setOpenTaskId(null); onOpenTasks(id); } : null}
            />
        </div>
    );
};

export default ProductionView;
