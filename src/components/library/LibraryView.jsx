import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Bookmark, BookOpen, CheckCircle2, Loader2, Trash2, Upload } from 'lucide-react';
import { CoverPlaceholder } from './LibraryCover';
import {
    APPLE_FONT, IosMenu, IosModal, IosSegmented, iosBtnPrimary, iosBtnSecondary, iosCard,
} from '../ui/ios';
import lazyWithRetry from '../../utils/lazyWithRetry';
import LibraryMonitoring from './LibraryMonitoring';
import {
    EPUB_ACCEPT, LIBRARY_TABS, STATUS_FINISHED, STATUS_IN_PROGRESS, STATUS_LABELS,
    filterBooks, formatPercent, isEpubFile,
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
 * подборка, «Мониторинг» — только супер-админу и тренеру. Первые две — один и
 * тот же список с разным фильтром, поэтому каталог приходит одним запросом и
 * делится на месте, без второго похода на сервер.
 *
 * Карточка книги — как в ТЗ (п. 3): обложка, название и автор, прогресс в
 * процентах, статус и кнопка «Сохранить». Цвет только у «Закончено»: это
 * единственное состояние, о котором стоит сказать отдельно.
 */

const DEFAULT_MAX_MB = 50;

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

const BookCard = ({ book, canManage, reading, onOpen, onLift, onToggleSaved, onDelete }) => {
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
                {canManage && (
                    <IosMenu
                        label="Действия с книгой"
                        items={[{ key: 'delete', label: 'Удалить книгу', icon: Trash2, danger: true, onSelect: () => onDelete(book) }]}
                    />
                )}
            </div>
            <button type="button" onClick={open} className="block w-full text-left focus:outline-none" tabIndex={-1}>
                <div className="line-clamp-2 text-[13.5px] font-semibold leading-snug text-slate-900">{book.title}</div>
                {book.author && <div className="mt-0.5 truncate text-[12px] text-slate-500">{book.author}</div>}
            </button>
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
    const [canManage, setCanManage] = useState(false);
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
    const [toDelete, setToDelete] = useState(null);
    const [deleting, setDeleting] = useState(false);
    const [monitoringKey, setMonitoringKey] = useState(0);
    const fileInputRef = useRef(null);
    const loadedAtRef = useRef(0);

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

    /* Вкладку «Мониторинг» у того, кому она не положена, не держим. */
    useEffect(() => {
        if (!canManage && tab === LIBRARY_TABS.monitoring) setTab(LIBRARY_TABS.all);
    }, [canManage, tab]);

    const shown = useMemo(() => filterBooks(books, tab), [books, tab]);
    const savedCount = useMemo(() => books.filter((book) => book.saved).length, [books]);

    const tabs = useMemo(() => [
        { value: LIBRARY_TABS.all, label: 'Общий доступ', count: books.length },
        { value: LIBRARY_TABS.saved, label: 'Сохранённые', count: savedCount },
        canManage && { value: LIBRARY_TABS.monitoring, label: 'Мониторинг' },
    ], [books.length, canManage, savedCount]);

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

    const uploadFiles = useCallback(async (files) => {
        const list = Array.from(files || []);
        for (let index = 0; index < list.length; index += 1) {
            const file = list[index];
            if (!isEpubFile(file)) {
                toast(`«${file.name}» — не EPUB. Загрузить можно только файлы .epub`, 'error');
                continue;
            }
            if (file.size > maxMb * 1024 * 1024) {
                toast(`«${file.name}» больше ${maxMb} МБ`, 'error');
                continue;
            }
            const form = new FormData();
            form.append('file', file);
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
                    toast(`Книга «${added.title}» добавлена`);
                }
            } catch (error) {
                toast(error?.response?.data?.error || `Не удалось загрузить «${file.name}»`, 'error');
            }
        }
        setUpload(null);
    }, [apiBaseUrl, headers, maxMb, toast]);

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

    const uploadLabel = upload
        ? (upload.percent < 100
            ? `Загрузка${upload.total > 1 ? ` ${upload.index} из ${upload.total}` : ''} · ${upload.percent} %`
            : 'Обрабатываю книгу…')
        : 'Загрузить книгу';

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
                                uploadFiles(files);
                                event.target.value = '';
                            }}
                        />
                        <button
                            type="button"
                            className={`${iosBtnPrimary} min-w-[172px] tabular-nums`}
                            onClick={() => fileInputRef.current?.click()}
                            disabled={Boolean(upload)}
                        >
                            {upload ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
                            {uploadLabel}
                        </button>
                        {/* В каком виде нужна книга — до выбора файла, а не
                            сообщением об ошибке после. */}
                        <span className="text-[11.5px] text-slate-400">Формат EPUB, до {maxMb} МБ</span>
                    </div>
                )}
            </header>

            <div className="mt-4">
                <IosSegmented value={tab} options={tabs} onChange={setTab} ariaLabel="Вкладки библиотеки" />
            </div>

            <div className="mt-4">
                {tab === LIBRARY_TABS.monitoring && canManage ? (
                    <LibraryMonitoring
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        reloadKey={monitoringKey}
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
                        <BookOpen size={28} className="mx-auto text-slate-300" />
                        <p className="mt-3 text-[14px] font-medium text-slate-700">
                            {tab === LIBRARY_TABS.saved ? 'Сохранённых книг пока нет' : 'В библиотеке пока нет книг'}
                        </p>
                        <p className="mt-1 text-[12.5px] text-slate-500">
                            {tab === LIBRARY_TABS.saved
                                ? 'Нажмите значок закладки под книгой — она появится здесь'
                                : (canManage ? 'Загрузите первую книгу в формате EPUB' : 'Книги появятся, когда их загрузит тренер')}
                        </p>
                    </div>
                ) : (
                    <div className="grid grid-cols-2 gap-x-4 gap-y-7 min-[520px]:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
                        {shown.map((book) => (
                            <BookCard
                                key={book.id}
                                book={book}
                                canManage={canManage}
                                reading={book.id === liftedId}
                                onOpen={(item, origin) => { setReaderOrigin(origin); setReaderId(item.id); }}
                                onLift={setLiftedId}
                                onToggleSaved={toggleSaved}
                                onDelete={setToDelete}
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

            <IosModal
                open={Boolean(toDelete)}
                onClose={() => { if (!deleting) setToDelete(null); }}
                title="Удалить книгу?"
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
                    Книга пропадёт из каталога у всех, а вместе с ней — закладки и прогресс чтения
                    сотрудников по ней.
                </p>
            </IosModal>
        </div>
    );
};

export default LibraryView;
