import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Archive, ArchiveRestore, Bookmark, BookOpen, CheckCircle2, Loader2, Tags, Trash2, Upload,
} from 'lucide-react';
import { CoverPlaceholder } from './LibraryCover';
import {
    APPLE_FONT, IosMenu, IosModal, IosSegmented, iosBtnPrimary, iosBtnSecondary, iosCard,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import lazyWithRetry from '../../utils/lazyWithRetry';
import LibraryMonitoring from './LibraryMonitoring';
import LibraryBookModal from './LibraryBookModal';
import LibraryGenresModal from './LibraryGenresModal';
import {
    DEPARTMENT_STORAGE_KEY, EPUB_ACCEPT, LIBRARY_TABS, STATUS_FINISHED, STATUS_IN_PROGRESS, STATUS_LABELS,
    bookInGenre, filterBooks, formatPercent, genreBookCounts, genresOnShelf, isEpubFile, restoreDepartment,
    shelfCountByDepartment,
} from './libraryMeta';

/* Ридер (распаковка EPUB, движок страниц) — отдельным чанком, не вместе с
   каталогом. С повтором: вкладка, открытая до выкладки, просила бы чанк,
   которого на сервере уже нет, и ронять портал из-за этого нельзя. */
const importReader = () => import('./LibraryReader');
const LibraryReader = lazyWithRetry(importReader);
const warmReader = () => { importReader().catch(() => {}); };

/* Подпись адреса обложки живёт три часа. Каталог, пролежавший открытым дольше
   часа, перечитывается при возвращении на вкладку — со свежими адресами. */
const CATALOG_STALE_MS = 60 * 60 * 1000;

/*
 * Раздел «Библиотека» (задача #282).
 *
 * Вкладки по ТЗ: «Общий доступ» — весь каталог, «Сохранённые» — личная
 * подборка, «Мониторинг» — только тем, кто ведёт библиотеку. Первые две — один и
 * тот же список с разным фильтром, поэтому каталог приходит одним запросом и
 * делится на месте, без второго похода на сервер.
 *
 * Отделы (29.09.2026): у каждого отдела своя библиотека — книга выдаётся
 * одному или нескольким отделам при публикации. Те, кто ведёт библиотеку, выбирают
 * отдел справа от вкладок («Все отделы» — вся библиотека); выбор действует и
 * на «Мониторинг». Читатель видит только книги своего отдела — их отбирает
 * сервер, выбирать ему нечего.
 *
 * Архив — вкладка управляющих: книга убрана с полки, но не удалена (прогресс
 * читателей цел, её можно вернуть). Удалить насовсем можно только отсюда.
 *
 * Жанры (02.10.2026): общий справочник, у книги — сколько угодно жанров.
 * Полка делится строкой жанров под вкладками (как подборки в «Книгах» Apple):
 * в строке только жанры, у которых на этой полке есть книги. Справочник
 * ведут управляющие — кнопка «Жанры» в шапке и «Новый жанр» в окне книги.
 *
 * Карточка книги — как в ТЗ (п. 3): обложка, название и автор, прогресс в
 * процентах, статус и кнопка «Сохранить». Цвет только у «Закончено»: это
 * единственное состояние, о котором стоит сказать отдельно.
 */

const DEFAULT_MAX_MB = 50;

/* Пилюля строки жанров. Выбранная — тёмная, как выбранная страница в
   IosPager: это навигация по полке, а не отметка. */
const GenreChip = ({ active, onClick, children }) => (
    <button
        type="button"
        role="radio"
        aria-checked={active}
        onClick={onClick}
        className={`inline-flex h-[30px] max-w-[220px] items-center rounded-full px-3.5 text-[12.5px] font-medium transition active:scale-[0.97] ${
            active ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200 hover:text-slate-800'}`}
    >
        <span className="truncate">{children}</span>
    </button>
);

const BookStatus = ({ progress }) => {
    const status = progress?.status;
    if (status === STATUS_FINISHED) {
        return (
            <span className="inline-flex min-w-0 items-center gap-1 text-[12px] font-medium text-emerald-600">
                <CheckCircle2 size={13} className="shrink-0" />
                <span className="truncate">{STATUS_LABELS[STATUS_FINISHED]}</span>
            </span>
        );
    }
    if (status === STATUS_IN_PROGRESS) {
        return (
            <span className="truncate text-[12px] tabular-nums text-slate-600">
                {STATUS_LABELS[STATUS_IN_PROGRESS]} · {formatPercent(progress.percent)}
            </span>
        );
    }
    return <span className="truncate text-[12px] text-slate-400">{STATUS_LABELS.not_started}</span>;
};

const BookCard = ({ book, menuItems, reading, onOpen, onLift, onToggleSaved }) => {
    const inProgress = book.progress?.status === STATUS_IN_PROGRESS;
    /* Обложка не загрузилась (подпись истекла, файла нет) — рисуем заглушку,
       а не серый пустой прямоугольник. Новый адрес — новая попытка. */
    const [coverFailed, setCoverFailed] = useState(false);
    useEffect(() => { setCoverFailed(false); }, [book.cover_url]);
    const coverRef = useRef(null);
    /* Откуда «вылетает» обложка в ридер: её место на экране в момент нажатия,
       картинка и её пропорции. Книга без картинки летит заглушкой. Куда
       возвращаться при закрытии, ридер спросит заново (locate): каталог за это
       время мог сдвинуться. */
    const open = () => {
        const node = coverRef.current;
        const rect = node?.getBoundingClientRect();
        const img = node?.querySelector('img');
        const hasImage = Boolean(book.cover_url && !coverFailed && img?.naturalWidth);
        onOpen(book, rect ? {
            rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
            coverUrl: hasImage ? book.cover_url : null,
            aspect: hasImage ? img.naturalWidth / img.naturalHeight : 2 / 3,
            title: book.title,
            author: book.author,
            percent: Number(book.progress?.percent) || 0,
            onLift: () => onLift(book.id),
            locate: () => {
                const box = coverRef.current?.getBoundingClientRect();
                return box && box.width > 0 ? { left: box.left, top: box.top, width: box.width, height: box.height } : null;
            },
        } : null);
    };
    return (
        <div className="min-w-0">
            <button
                type="button"
                onClick={open}
                // Код ридера — уже при наведении и нажатии, если не успел загрузиться.
                onPointerEnter={warmReader}
                onPointerDown={warmReader}
                className="group block w-full text-left focus:outline-none"
                aria-label={`Читать «${book.title}»`}
            >
                {/* Пока книга открыта, её обложка — в ридере: здесь пусто,
                    чтобы при закрытии она легла на своё место, а не на копию. */}
                <div ref={coverRef} style={reading ? { visibility: 'hidden' } : undefined} className="relative aspect-[2/3] w-full overflow-hidden rounded-[8px] bg-slate-100 shadow-[0_6px_18px_rgba(15,23,42,0.16)] ring-1 ring-black/5 transition duration-200 group-hover:-translate-y-0.5 group-hover:shadow-[0_12px_28px_rgba(15,23,42,0.22)] group-focus-visible:ring-2 group-focus-visible:ring-blue-500 group-active:scale-[0.98]">
                    {book.cover_url && !coverFailed ? (
                        <img src={book.cover_url} alt="" loading="lazy" className="h-full w-full object-cover"
                            onError={() => setCoverFailed(true)} />
                    ) : (
                        <CoverPlaceholder title={book.title} author={book.author} />
                    )}
                    {inProgress && (
                        <div className="absolute inset-x-0 bottom-0 h-[3px] bg-black/20">
                            <div className="h-full bg-blue-500" style={{ width: `${Math.max(2, book.progress.percent)}%` }} />
                        </div>
                    )}
                </div>
            </button>
            {/* Статус — сразу под обложкой, как на макете постановки: название
                бывает в одну строку и в две, и строка статуса под ним гуляла
                бы по высоте от карточки к карточке. */}
            <div className="mt-1.5 flex items-center gap-0.5">
                <div className="min-w-0 flex-1"><BookStatus progress={book.progress} /></div>
                <button
                    type="button"
                    onClick={() => onToggleSaved(book)}
                    className={`grid h-8 w-8 shrink-0 place-items-center rounded-full transition active:scale-95 ${
                        book.saved ? 'text-blue-600 hover:bg-blue-50' : 'text-slate-400 hover:bg-slate-100 hover:text-slate-700'}`}
                    aria-label={book.saved ? 'Убрать из сохранённых' : 'Сохранить'}
                    aria-pressed={book.saved}
                    title={book.saved ? 'Убрать из сохранённых' : 'Сохранить'}
                >
                    <Bookmark size={16} fill={book.saved ? 'currentColor' : 'none'} />
                </button>
                {menuItems?.length > 0 && <IosMenu label="Действия с книгой" items={menuItems} />}
            </div>
            <button type="button" onClick={open} className="block w-full text-left focus:outline-none" tabIndex={-1}>
                <div className="line-clamp-2 text-[13.5px] font-semibold leading-snug text-slate-900">{book.title}</div>
                {book.author && <div className="mt-0.5 truncate text-[12px] text-slate-500">{book.author}</div>}
            </button>
            {/* Книга, которой не осталось ни одного отдела (отдел удалили),
                не видна никому, кроме управляющих, — об этом и говорит строка. */}
            {menuItems?.length > 0 && !book.archived && !(book.department_ids?.length > 0) && (
                <div className="mt-0.5 text-[11.5px] text-amber-600">Не выдана ни одному отделу</div>
            )}
        </div>
    );
};

const LibraryView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );
    /* showToast из App.jsx — новая функция на каждый его рендер; в зависимостях
       колбэков она пересоздавала бы их вместе с эффектами. */
    const toastRef = useRef(showToast);
    toastRef.current = showToast;
    const toast = useCallback((message, type) => toastRef.current?.(message, type), []);

    const [books, setBooks] = useState([]);
    const [departments, setDepartments] = useState([]);
    const [genres, setGenres] = useState([]);
    /* '' — «Все жанры». Не запоминается: жанр — способ найти книгу сейчас,
       а не «своя полка», как отдел. */
    const [genreId, setGenreId] = useState('');
    const [genresOpen, setGenresOpen] = useState(false);
    /* '' — «Все отделы». Восстанавливается из памяти браузера, когда
       приходит список отделов (restoreDepartment). */
    const [departmentId, setDepartmentId] = useState('');
    const [canManage, setCanManage] = useState(false);
    /* Ведёт все отделы (тренер, руководитель, супер-админ). СВ ведёт один
       свой — сервер присылает ему только его отдел. */
    const [manageAll, setManageAll] = useState(false);
    const [schemaReady, setSchemaReady] = useState(true);
    const [maxMb, setMaxMb] = useState(DEFAULT_MAX_MB);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState('');
    const [tab, setTab] = useState(LIBRARY_TABS.all);
    const [readerId, setReaderId] = useState(null);
    const [readerOrigin, setReaderOrigin] = useState(null);
    /* Чья обложка сейчас в ридере (улетела из карточки). */
    const [liftedId, setLiftedId] = useState(null);
    const [upload, setUpload] = useState(null);
    /* Окно книги (отделы и жанры): { mode: 'publish', files } после выбора
       файла или { mode: 'edit', book } из меню карточки. */
    const [sheet, setSheet] = useState(null);
    const [sheetBusy, setSheetBusy] = useState(false);
    const [toDelete, setToDelete] = useState(null);
    const [deleting, setDeleting] = useState(false);
    const [monitoringKey, setMonitoringKey] = useState(0);
    const fileInputRef = useRef(null);
    const loadedAtRef = useRef(0);
    const departmentRestoredRef = useRef(false);
    const tabsRef = useRef(null);

    /* silent — тихое обновление (свежие адреса обложек после долгого чтения):
       без спиннера вместо сетки и без потери прокрутки. Прогресс карточки
       берётся более свежий из двух: ридер мог прислать место позже, чем
       сервер собрал этот ответ. */
    const load = useCallback(({ silent = false } = {}) => {
        if (!silent) {
            setLoading(true);
            setLoadError('');
        }
        axios.get(`${apiBaseUrl}/api/library`, { headers: headers() })
            .then((response) => {
                loadedAtRef.current = Date.now();
                const body = response.data || {};
                const incoming = Array.isArray(body.books) ? body.books : [];
                setBooks((previous) => {
                    if (!silent) return incoming;
                    const local = new Map(previous.map((book) => [book.id, book]));
                    return incoming.map((book) => {
                        const mine = local.get(book.id);
                        const fresher = mine?.progress?.updated_at
                            && String(mine.progress.updated_at) > String(book.progress?.updated_at || '');
                        return fresher ? { ...book, progress: mine.progress } : book;
                    });
                });
                setCanManage(Boolean(body.can_manage));
                setManageAll(Boolean(body.manage_all));
                setDepartments(Array.isArray(body.departments) ? body.departments : []);
                setGenres(Array.isArray(body.genres) ? body.genres : []);
                setSchemaReady(body.schema_ready !== false);
                if (body.max_upload_mb) setMaxMb(body.max_upload_mb);
            })
            .catch((error) => { if (!silent) setLoadError(error?.response?.data?.error || 'Не удалось загрузить библиотеку'); })
            .finally(() => { if (!silent) setLoading(false); });
    }, [apiBaseUrl, headers]);

    useEffect(() => { load(); }, [load]);

    /* Код ридера — заранее, пока каталог на экране: иначе первое нажатие на
       книгу ждало бы загрузки чанка, и обложка вылетала бы с запинкой. */
    useEffect(() => {
        if (typeof window.requestIdleCallback === 'function') {
            const id = window.requestIdleCallback(warmReader, { timeout: 3000 });
            return () => window.cancelIdleCallback(id);
        }
        const id = window.setTimeout(warmReader, 1500);
        return () => window.clearTimeout(id);
    }, []);

    useEffect(() => {
        const onVisible = () => {
            if (document.visibilityState !== 'visible' || readerId) return;
            if (loadedAtRef.current && Date.now() - loadedAtRef.current > CATALOG_STALE_MS) load({ silent: true });
        };
        document.addEventListener('visibilitychange', onVisible);
        return () => document.removeEventListener('visibilitychange', onVisible);
    }, [load, readerId]);

    /* Вкладки «Архив» и «Мониторинг» у того, кому они не положены, не держим. */
    useEffect(() => {
        if (!canManage && (tab === LIBRARY_TABS.monitoring || tab === LIBRARY_TABS.archive)) setTab(LIBRARY_TABS.all);
    }, [canManage, tab]);

    /* Отдел, выбранный в прошлый раз, — как только известен список отделов.
       Один раз: дальше выбор ведёт человек, а тихое обновление каталога не
       должно сбрасывать его обратно. */
    useEffect(() => {
        if (departmentRestoredRef.current || !departments.length) return;
        departmentRestoredRef.current = true;
        let stored = '';
        try { stored = window.localStorage.getItem(DEPARTMENT_STORAGE_KEY) || ''; } catch { /* хранилище закрыто */ }
        setDepartmentId(restoreDepartment(stored, departments));
    }, [departments]);

    /* Выбранная вкладка на телефоне докручивается в видимую часть полосы:
       иначе «Мониторинг» оставался бы обрезанным у края и после нажатия. */
    useEffect(() => {
        const strip = tabsRef.current;
        const active = strip?.querySelector('[aria-selected="true"]');
        if (!strip || !active || strip.scrollWidth <= strip.clientWidth) return;
        const left = active.offsetLeft; // от края полосы: у неё position: relative
        if (left < strip.scrollLeft || left + active.offsetWidth > strip.scrollLeft + strip.clientWidth) {
            strip.scrollTo({ left: Math.max(0, left - 8), behavior: 'smooth' });
        }
    }, [tab]);

    const pickDepartment = useCallback((value) => {
        const next = value === '' || value === null || value === undefined ? '' : Number(value);
        setDepartmentId(next);
        try { window.localStorage.setItem(DEPARTMENT_STORAGE_KEY, String(next)); } catch { /* хранилище закрыто */ }
    }, []);

    /* Строка жанров — по полке без учёта жанра: иначе выбранный жанр оставлял
       бы в строке только себя. Жанр, которого на этой полке нет (сменили
       вкладку или отдел), не держит пустой экран — показываются все книги. */
    const shelfBooks = useMemo(() => filterBooks(books, tab, departmentId), [books, tab, departmentId]);
    const genreChips = useMemo(() => genresOnShelf(genres, shelfBooks), [genres, shelfBooks]);
    const activeGenreId = genreChips.some((genre) => genre.id === genreId) ? genreId : '';
    const shown = useMemo(
        () => shelfBooks.filter((book) => bookInGenre(book, activeGenreId)),
        [activeGenreId, shelfBooks],
    );
    /* Сколько книг у жанра — во всей библиотеке, с архивом: для окна «Жанры». */
    const genreCounts = useMemo(() => genreBookCounts(books), [books]);
    const counts = useMemo(() => ({
        all: filterBooks(books, LIBRARY_TABS.all, departmentId).length,
        saved: filterBooks(books, LIBRARY_TABS.saved, departmentId).length,
        archive: filterBooks(books, LIBRARY_TABS.archive, departmentId).length,
    }), [books, departmentId]);

    const tabs = useMemo(() => [
        { value: LIBRARY_TABS.all, label: 'Общий доступ', count: counts.all },
        { value: LIBRARY_TABS.saved, label: 'Сохранённые', count: counts.saved },
        canManage && { value: LIBRARY_TABS.archive, label: 'Архив', count: counts.archive },
        canManage && { value: LIBRARY_TABS.monitoring, label: 'Мониторинг' },
    ], [canManage, counts]);

    /* Выбор отдела: рядом с каждым — сколько книг у него на полке. Отдел без
       книг приглушён: выбрать можно (чтобы опубликовать туда первую), но
       видно, что там пусто. В списке — действующие отделы (им книги
       выдаются) и выбранный: иначе кнопка показала бы «Все отделы» над
       данными одного отдела. Книги выключенных отделов видны во «Всех
       отделах». */
    const departmentOptions = useMemo(() => {
        const shelf = shelfCountByDepartment(books);
        const onShelf = books.filter((book) => !book.archived).length;
        return [
            { value: '', label: 'Все отделы', meta: onShelf ? String(onShelf) : undefined },
            ...departments
                .filter((item) => item.active !== false || item.id === departmentId)
                .map((item) => ({
                    value: item.id,
                    label: item.name,
                    meta: shelf.get(item.id) ? String(shelf.get(item.id)) : undefined,
                    muted: !shelf.get(item.id),
                })),
        ];
    }, [books, departmentId, departments]);
    /* Отдел СВ: переключателя у него нет, мониторинг и окно книги — всегда
       этого отдела. */
    const ownDepartment = canManage && !manageAll ? departments[0] || null : null;
    const showDepartments = manageAll && departments.length > 0;
    /* Какие отделы можно выбрать — для строк «По отделам» в мониторинге. */
    const selectableDepartmentIds = useMemo(
        () => new Set(departmentOptions.map((item) => item.value).filter((value) => value !== '')),
        [departmentOptions],
    );

    /* Отдел пропал из списка (удалён, пока раздел был открыт) — назад во «Все
       отделы», а не пустая полка под чужим названием. */
    useEffect(() => {
        if (departmentId !== '' && departments.length && !departments.some((item) => item.id === departmentId)) {
            pickDepartment('');
        }
    }, [departmentId, departments, pickDepartment]);

    const toggleSaved = useCallback((book) => {
        const saved = !book.saved;
        setBooks((prev) => prev.map((item) => (item.id === book.id ? { ...item, saved } : item)));
        axios.put(`${apiBaseUrl}/api/library/books/${book.id}/saved`, { saved }, { headers: headers() })
            .catch((error) => {
                setBooks((prev) => prev.map((item) => (item.id === book.id ? { ...item, saved: !saved } : item)));
                toast(error?.response?.data?.error || 'Не удалось сохранить книгу', 'error');
            });
    }, [apiBaseUrl, headers, toast]);

    /* Ридер сообщает свежий прогресс — карточка обновляется без перезапроса
       каталога. */
    const applyProgress = useCallback((bookId, progress) => {
        setBooks((prev) => prev.map((item) => (item.id === bookId
            ? { ...item, progress: { ...item.progress, ...progress } }
            : item)));
    }, []);

    const closeReader = useCallback(() => {
        setReaderId(null);
        setLiftedId(null);
        setMonitoringKey((value) => value + 1);
        // Читали дольше часа — адреса обложек в каталоге могли истечь.
        if (loadedAtRef.current && Date.now() - loadedAtRef.current > CATALOG_STALE_MS) load({ silent: true });
    }, [load]);

    /* Файлы выбраны — сначала отсеять не те, потом спросить отделы. Окно
       публикации открывается с отделом, выбранным сейчас в разделе: тренер,
       стоящий на полке СЗоВ, публикует в СЗоВ, не отмечая его заново. */
    const chooseFiles = useCallback((files) => {
        const accepted = [];
        for (const file of Array.from(files || [])) {
            if (!isEpubFile(file)) {
                toast(`«${file.name}» — не EPUB. Загрузить можно только файлы .epub`, 'error');
            } else if (file.size > maxMb * 1024 * 1024) {
                toast(`«${file.name}» больше ${maxMb} МБ`, 'error');
            } else {
                accepted.push(file);
            }
        }
        if (accepted.length) setSheet((prev) => (prev?.mode === 'publish' ? { ...prev, files: accepted } : { mode: 'publish', files: accepted }));
    }, [maxMb, toast]);

    const uploadFiles = useCallback(async (list, departmentIds, genreIds) => {
        setSheetBusy(true);
        const failed = [];
        for (let index = 0; index < list.length; index += 1) {
            const file = list[index];
            const form = new FormData();
            form.append('file', file);
            departmentIds.forEach((id) => form.append('department_ids', String(id)));
            genreIds.forEach((id) => form.append('genre_ids', String(id)));
            setUpload({ name: file.name, percent: 0, index: index + 1, total: list.length });
            try {
                // eslint-disable-next-line no-await-in-loop
                const response = await axios.post(`${apiBaseUrl}/api/library/books`, form, {
                    headers: headers(),
                    onUploadProgress: (event) => {
                        if (!event.total) return;
                        setUpload((prev) => prev && ({ ...prev, percent: Math.round((event.loaded / event.total) * 100) }));
                    },
                });
                const added = response.data?.book;
                if (added) {
                    setBooks((prev) => [added, ...prev.filter((item) => item.id !== added.id)]);
                    toast(`Книга «${added.title}» опубликована`);
                }
            } catch (error) {
                failed.push(file);
                toast(error?.response?.data?.error || `Не удалось загрузить «${file.name}»`, 'error');
            }
        }
        setUpload(null);
        setSheetBusy(false);
        setMonitoringKey((value) => value + 1);
        /* Не легли — окно остаётся с ними и с теми же отделами и жанрами:
           повторить одним нажатием, а не выбирать всё заново. */
        setSheet(failed.length ? { mode: 'publish', files: failed, departmentIds, genreIds } : null);
    }, [apiBaseUrl, headers, toast]);

    /* Правка книги (отделы, жанры, архив) — ответ сервера заменяет карточку целиком. */
    const updateBook = useCallback((book, changes, message) => axios
        .patch(`${apiBaseUrl}/api/library/books/${book.id}`, changes, { headers: headers() })
        .then((response) => {
            const updated = response.data?.book;
            if (updated) {
                setBooks((prev) => prev.map((item) => (item.id === updated.id
                    ? { ...updated, progress: item.progress } : item)));
            }
            toast(message);
            setMonitoringKey((value) => value + 1);
            return true;
        })
        .catch((error) => {
            toast(error?.response?.data?.error || 'Не удалось изменить книгу', 'error');
            return false;
        }), [apiBaseUrl, headers, toast]);

    const submitSheet = useCallback(({ departmentIds, genreIds }) => {
        if (!sheet) return;
        if (sheet.mode === 'publish') {
            uploadFiles(sheet.files, departmentIds, genreIds);
            return;
        }
        setSheetBusy(true);
        updateBook(sheet.book, { department_ids: departmentIds, genre_ids: genreIds },
            `Книга «${sheet.book.title}» сохранена`)
            .then((ok) => { if (ok) setSheet(null); })
            .finally(() => setSheetBusy(false));
    }, [sheet, updateBook, uploadFiles]);

    /* Меню «···» — у книги, которую смотрящему можно менять (решает сервер):
       общая с другими отделами книга у СВ только для чтения. */
    const menuFor = useCallback((book) => {
        if (!canManage || !book.can_edit) return null;
        const departmentsItem = {
            key: 'departments', label: ownDepartment ? 'Жанры' : 'Отделы и жанры', icon: Tags,
            onSelect: () => setSheet({ mode: 'edit', book }),
        };
        if (book.archived) {
            return [
                {
                    key: 'restore', label: 'Вернуть в библиотеку', icon: ArchiveRestore,
                    onSelect: () => updateBook(book, { archived: false }, `Книга «${book.title}» снова в библиотеке`),
                },
                departmentsItem,
                { key: 'delete', label: 'Удалить навсегда', icon: Trash2, danger: true, onSelect: () => setToDelete(book) },
            ];
        }
        return [
            departmentsItem,
            {
                key: 'archive', label: 'В архив', icon: Archive,
                onSelect: () => updateBook(book, { archived: true }, `Книга «${book.title}» убрана в архив`),
            },
        ];
    }, [canManage, ownDepartment, updateBook]);

    /* Справочник жанров. Каждая ручка отвечает жанром — список правится на
       месте, без перезапроса каталога. null/false — отказ, тост уже показан. */
    const genreError = useCallback((error, fallback) => {
        toast(error?.response?.data?.error || fallback, 'error');
    }, [toast]);

    const createGenre = useCallback((name) => axios
        .post(`${apiBaseUrl}/api/library/genres`, { name }, { headers: headers() })
        .then((response) => {
            const genre = response.data?.genre;
            if (!genre) return null;
            setGenres((prev) => (prev.some((item) => item.id === genre.id) ? prev : [...prev, genre]));
            return genre;
        })
        .catch((error) => { genreError(error, 'Не удалось создать жанр'); return null; }), [apiBaseUrl, genreError, headers]);

    /* Удалённый жанр уходит и из книг: сервер снял его каскадом, карточкам
       незачем ждать перезапроса каталога. */
    const dropGenre = useCallback((genre) => {
        setGenres((prev) => prev.filter((item) => item.id !== genre.id));
        setBooks((prev) => prev.map((book) => (book.genre_ids?.includes(genre.id)
            ? { ...book, genre_ids: book.genre_ids.filter((id) => id !== genre.id) }
            : book)));
        setGenreId((current) => (current === genre.id ? '' : current));
    }, []);

    /* Не найден — жанр удалили с другого компьютера: строка уходит из
       списка, а не остаётся «живой», отказывая на каждую правку. */
    const renameGenre = useCallback((genre, name) => axios
        .patch(`${apiBaseUrl}/api/library/genres/${genre.id}`, { name }, { headers: headers() })
        .then((response) => {
            const updated = response.data?.genre;
            if (updated) setGenres((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
            return true;
        })
        .catch((error) => {
            if (error?.response?.status === 404) dropGenre(genre);
            genreError(error, 'Не удалось переименовать жанр');
            return false;
        }), [apiBaseUrl, dropGenre, genreError, headers]);

    /* Не найден (удалили с другого компьютера) — для этого экрана то же,
       что удалён. */
    const deleteGenre = useCallback((genre) => axios
        .delete(`${apiBaseUrl}/api/library/genres/${genre.id}`, { headers: headers() })
        .catch((error) => {
            if (error?.response?.status === 404) return null;
            throw error;
        })
        .then(() => {
            dropGenre(genre);
            toast(`Жанр «${genre.name}» удалён`);
            return true;
        })
        .catch((error) => { genreError(error, 'Не удалось удалить жанр'); return false; }), [apiBaseUrl, dropGenre, genreError, headers, toast]);

    const confirmDelete = useCallback(() => {
        if (!toDelete) return;
        setDeleting(true);
        axios.delete(`${apiBaseUrl}/api/library/books/${toDelete.id}`, { headers: headers() })
            .then(() => {
                setBooks((prev) => prev.filter((item) => item.id !== toDelete.id));
                toast(`Книга «${toDelete.title}» удалена`);
                setToDelete(null);
                setMonitoringKey((value) => value + 1);
            })
            .catch((error) => toast(error?.response?.data?.error || 'Не удалось удалить книгу', 'error'))
            .finally(() => setDeleting(false));
    }, [apiBaseUrl, headers, toDelete, toast]);

    /* На телефоне окно ещё 300 мс уезжает после закрытия — всё это время оно
       рисует последнее содержимое, а не «Отделы книги» без файлов. */
    const lastSheetRef = useRef(null);
    if (sheet) lastSheetRef.current = sheet;
    const shownSheet = sheet || lastSheetRef.current;

    /* Окно публикации заранее отмечает отдел, выбранный в разделе, — только
       тот, которому книги выдаются: другому сервер новую книгу не выдаст. И
       жанр, выбранный на полке: тренер, листающий «Психологию», публикует в неё. */
    const publishDefaultIds = useMemo(() => {
        if (ownDepartment) return [ownDepartment.id];
        const current = departments.find((item) => item.id === departmentId);
        return current && current.active !== false ? [departmentId] : [];
    }, [departmentId, departments, ownDepartment]);
    const publishDefaultGenreIds = useMemo(() => (activeGenreId === '' ? [] : [activeGenreId]), [activeGenreId]);

    const uploadLabel = upload
        ? (upload.percent < 100
            ? `Загрузка${upload.total > 1 ? ` ${upload.index} из ${upload.total}` : ''} · ${upload.percent} %`
            : 'Обрабатываю книгу…')
        : '';

    return (
        <div className="mx-auto w-full max-w-[1180px] px-3 py-4 sm:px-5 sm:py-6" style={{ fontFamily: APPLE_FONT }}>
            <header className="flex flex-col gap-3 sm:flex-row sm:items-center">
                <div className="min-w-0 sm:flex-1">
                    <h1 className="text-[19px] font-semibold leading-tight text-slate-900">Библиотека</h1>
                    <p className="mt-0.5 text-[12.5px] text-slate-500">
                        Книги для чтения прямо на портале — место и прогресс сохраняются сами
                    </p>
                </div>
                {canManage && schemaReady && (
                    <div className="flex shrink-0 flex-col gap-1 sm:items-end">
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept={EPUB_ACCEPT}
                            multiple
                            className="hidden"
                            onChange={(event) => {
                                const { files } = event.target;
                                chooseFiles(files);
                                event.target.value = '';
                            }}
                        />
                        <div className="flex gap-2">
                            {/* Справочник жанров общий для всех отделов —
                                переименовать и удалить жанр может тот, кто
                                ведёт все отделы. СВ создаёт жанр в окне книги. */}
                            {manageAll && (
                                <button type="button" className={iosBtnSecondary} onClick={() => setGenresOpen(true)}>
                                    <Tags size={15} />
                                    Жанры
                                </button>
                            )}
                            <button
                                type="button"
                                className={`${iosBtnPrimary} min-w-[172px] flex-1 sm:flex-none`}
                                onClick={() => fileInputRef.current?.click()}
                                disabled={sheetBusy}
                            >
                                <Upload size={15} />
                                Загрузить книгу
                            </button>
                        </div>
                        {/* В каком виде нужна книга — до выбора файла, а не
                            сообщением об ошибке после. */}
                        <span className="text-[11.5px] text-slate-400">Формат EPUB, до {maxMb} МБ</span>
                    </div>
                )}
            </header>

            {/* Вкладки прокручиваются вбок на узком телефоне: у управляющего их
                четыре, и в 360 точек они не входят. min-w-max — иначе кнопки
                ужимаются под текст, и счётчик вылезает за край вкладки.
                Без -mx-*: оболочка телефона гасит отрицательные поля
                (mobile-shell.css), и полоса съезжала вправо от заголовка. */}
            <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
                <div ref={tabsRef} className="relative min-w-0 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
                    <IosSegmented className="min-w-max" value={tab} options={tabs} onChange={setTab} ariaLabel="Вкладки библиотеки" />
                </div>
                {showDepartments && (
                    <CustomSelect
                        className="sm:ml-auto sm:w-[280px] sm:shrink-0"
                        variant="ios"
                        value={departmentId}
                        onChange={pickDepartment}
                        options={departmentOptions}
                        searchable={departmentOptions.length > 8}
                        searchPlaceholder="Поиск по названию отдела…"
                        placeholder="Все отделы"
                        ariaLabel="Отдел"
                    />
                )}
            </div>

            {/* Жанры полки — пилюлями в одну строку, на телефоне она листается
                вбок. Нет ни одного жанра с книгами — строки нет вовсе. */}
            {tab !== LIBRARY_TABS.monitoring && !loading && genreChips.length > 0 && (
                <div className="mt-3 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
                    <div role="radiogroup" aria-label="Жанр" className="flex min-w-max gap-1.5">
                        <GenreChip active={activeGenreId === ''} onClick={() => setGenreId('')}>Все жанры</GenreChip>
                        {genreChips.map((genre) => (
                            <GenreChip key={genre.id} active={activeGenreId === genre.id} onClick={() => setGenreId(genre.id)}>
                                {genre.name}
                            </GenreChip>
                        ))}
                    </div>
                </div>
            )}

            <div className="mt-4">
                {tab === LIBRARY_TABS.monitoring && canManage ? (
                    <LibraryMonitoring
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        reloadKey={monitoringKey}
                        departmentId={ownDepartment ? ownDepartment.id : departmentId}
                        selectableDepartmentIds={selectableDepartmentIds}
                        onPickDepartment={pickDepartment}
                    />
                ) : loading ? (
                    <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-slate-500">
                        <Loader2 size={15} className="animate-spin" /> Загружаем библиотеку…
                    </div>
                ) : loadError ? (
                    <div className={`${iosCard} flex flex-col items-center gap-3 px-4 py-10 text-center text-[13.5px] text-slate-600`}>
                        {loadError}
                        <button type="button" className={iosBtnSecondary} onClick={() => load()}>Повторить</button>
                    </div>
                ) : !schemaReady ? (
                    <div className={`${iosCard} px-4 py-10 text-center text-[13.5px] text-slate-500`}>
                        Раздел ещё разворачивается — загляните через несколько минут
                    </div>
                ) : !shown.length ? (
                    <div className={`${iosCard} px-4 py-12 text-center`}>
                        {tab === LIBRARY_TABS.archive
                            ? <Archive size={28} className="mx-auto text-slate-300" />
                            : <BookOpen size={28} className="mx-auto text-slate-300" />}
                        <p className="mt-3 text-[14px] font-medium text-slate-700">
                            {tab === LIBRARY_TABS.saved ? 'Сохранённых книг пока нет'
                                : tab === LIBRARY_TABS.archive ? 'В архиве пусто'
                                    : departmentId ? 'У отдела пока нет книг' : 'В библиотеке пока нет книг'}
                        </p>
                        <p className="mt-1 text-[12.5px] text-slate-500">
                            {tab === LIBRARY_TABS.saved
                                ? 'Нажмите значок закладки под книгой — она появится здесь'
                                : tab === LIBRARY_TABS.archive
                                    ? 'Сюда попадают книги, убранные с полки: их можно вернуть или удалить насовсем'
                                    : (canManage
                                        ? (departmentId ? 'Загрузите книгу и отметьте этот отдел при публикации' : 'Загрузите первую книгу в формате EPUB')
                                        : 'Книги появятся, когда их загрузят')}
                        </p>
                    </div>
                ) : (
                    <div className="grid grid-cols-2 gap-x-4 gap-y-7 min-[520px]:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
                        {shown.map((book) => (
                            <BookCard
                                key={book.id}
                                book={book}
                                menuItems={menuFor(book)}
                                reading={book.id === liftedId}
                                onOpen={(item, origin) => { setReaderOrigin(origin); setReaderId(item.id); }}
                                onLift={setLiftedId}
                                onToggleSaved={toggleSaved}
                            />
                        ))}
                    </div>
                )}
            </div>

            {/* Ридер закрывается сам — захлопывает книгу и возвращает обложку в
                карточку — и только потом зовёт onClose. */}
            {readerId && (
                <Suspense key={readerId} fallback={null}>
                    <LibraryReader
                        bookId={readerId}
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        onClose={closeReader}
                        onProgress={applyProgress}
                        origin={readerOrigin}
                    />
                </Suspense>
            )}

            <LibraryBookModal
                open={Boolean(sheet)}
                mode={shownSheet?.mode}
                files={shownSheet?.files}
                book={shownSheet?.book}
                departments={departments}
                lockedDepartment={ownDepartment}
                initialIds={shownSheet?.mode === 'edit'
                    ? shownSheet.book.department_ids
                    : (shownSheet?.departmentIds || publishDefaultIds)}
                genres={genres}
                initialGenreIds={shownSheet?.mode === 'edit'
                    ? shownSheet.book.genre_ids
                    : (shownSheet?.genreIds || publishDefaultGenreIds)}
                onCreateGenre={createGenre}
                busy={sheetBusy}
                busyLabel={uploadLabel}
                onSubmit={submitSheet}
                onClose={() => setSheet(null)}
                onPickFiles={() => fileInputRef.current?.click()}
            />

            <LibraryGenresModal
                open={genresOpen}
                genres={genres}
                counts={genreCounts}
                onClose={() => setGenresOpen(false)}
                onCreate={createGenre}
                onRename={renameGenre}
                onDelete={deleteGenre}
            />

            <IosModal
                open={Boolean(toDelete)}
                onClose={() => { if (!deleting) setToDelete(null); }}
                title="Удалить книгу навсегда?"
                subtitle={toDelete?.title}
                maxWidth="max-w-md"
                footer={(
                    <>
                        <button type="button" className={iosBtnSecondary} onClick={() => setToDelete(null)} disabled={deleting}>
                            Отмена
                        </button>
                        <button
                            type="button"
                            className={`${iosBtnPrimary} !bg-rose-600 hover:!bg-rose-700`}
                            onClick={confirmDelete}
                            disabled={deleting}
                        >
                            {deleting && <Loader2 size={15} className="animate-spin" />}
                            Удалить
                        </button>
                    </>
                )}
            >
                <p className="text-[13.5px] leading-relaxed text-slate-600">
                    Книга удалится вместе с закладками и прогрессом чтения сотрудников по ней, и в
                    мониторинге её больше не будет. Вернуть её будет нельзя.
                </p>
            </IosModal>
        </div>
    );
};

export default LibraryView;
