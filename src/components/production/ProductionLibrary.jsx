import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import axios from 'axios';
import { Loader2, Search, Upload, X } from 'lucide-react';
import { iosBtnPrimary, iosBtnSecondary, iosInput, IosHint } from '../ui/ios';
import { startFileDownload } from '../../utils/fileDownload';
import ProductionViewer from './ProductionViewer';
import { MaterialTile, UploadRow } from './ProductionTaskSheet';
import { uploadOwnerId, uploadStore } from './productionUpload';
import { VIDEO_ACCEPT_ATTRIBUTE, formatShortDate, shortName } from './productionMeta';

/*
 * Вкладка «Ролики» раздела «Продакшн»: просто загрузить ролик, без задачи.
 *
 * Просьба владельца 16.10.2026: «как можно просто загружать ролики — данная
 * возможность тоже должна быть на сайте». Задача продакшна нужна, когда ролик
 * заказывают и проверяют; когда видеограф просто складывает готовые ролики,
 * задача лишняя. Путь файла тот же, что у материалов задачи (productionUpload.js):
 * прямо в облако, с докачкой и сверкой, обложка — кадр из ролика.
 *
 * Видит и загружает тот, у кого в разделе охват «всё» — маркетинг и админ со
 * всеми задачами. Удаляют загрузивший, глава маркетинга и супер-админ (сервер).
 */

const PAGE = 30;

const errorText = (error, fallback) => error?.response?.data?.error || error?.message || fallback;

const ProductionLibrary = ({ apiBaseUrl, headers, showToast }) => {
    const [items, setItems] = useState(null);
    const [total, setTotal] = useState(0);
    const [error, setError] = useState('');
    const [loadingMore, setLoadingMore] = useState(false);
    const [query, setQuery] = useState('');
    const [dragOver, setDragOver] = useState(false);
    const [viewer, setViewer] = useState({ ids: [], index: -1 });
    const fileRef = useRef(null);
    const ticketRef = useRef(0);
    const itemsRef = useRef(null);
    itemsRef.current = items;
    const reloadTimerRef = useRef(null);

    const uploads = useSyncExternalStore(uploadStore.subscribe, uploadStore.snapshot, uploadStore.snapshot);
    const myUploads = useMemo(() => {
        const owner = uploadOwnerId();
        return uploads.filter((item) => item.library && (!item.ownerId || item.ownerId === owner));
    }, [uploads]);

    const load = useCallback(async ({ append = false } = {}) => {
        const ticket = ticketRef.current + 1;
        ticketRef.current = ticket;
        if (append) setLoadingMore(true);
        try {
            const params = { limit: PAGE, offset: append ? (itemsRef.current || []).length : 0 };
            if (query.trim()) params.q = query.trim();
            const response = await axios.get(`${apiBaseUrl}/api/production/library`, { headers: headers(), params });
            if (ticketRef.current !== ticket) return;
            const rows = response.data?.items || [];
            setItems((prev) => (append ? [...(prev || []), ...rows] : rows));
            setTotal(Number(response.data?.total || 0));
            setError('');
        } catch (err) {
            if (ticketRef.current !== ticket) return;
            // Список уже на экране — фоновое обновление не прошло, но показанное верно.
            if (!itemsRef.current) setError(errorText(err, 'Не удалось загрузить ролики'));
        } finally {
            if (ticketRef.current === ticket) setLoadingMore(false);
        }
    }, [apiBaseUrl, headers, query]);

    const loadRef = useRef(load);
    loadRef.current = load;
    useEffect(() => {
        const timer = setTimeout(() => loadRef.current(), query ? 300 : 0);
        return () => clearTimeout(timer);
    }, [query]);
    useEffect(() => () => window.clearTimeout(reloadTimerRef.current), []);

    const addFiles = (files) => {
        const list = Array.from(files || []);
        if (!list.length) return;
        uploadStore.add({
            library: true,
            files: list,
            apiBaseUrl,
            headers,
            // Пачка роликов — одно обновление списка, а не по запросу на каждый файл.
            onDone: () => {
                window.clearTimeout(reloadTimerRef.current);
                reloadTimerRef.current = window.setTimeout(() => loadRef.current(), 700);
            },
        });
    };

    const download = async (material) => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/production/materials/${material.id}/download`, { headers: headers() });
            startFileDownload(response.data?.url);
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось скачать ролик'), 'error');
        }
    };

    const remove = async (material) => {
        try {
            await axios.delete(`${apiBaseUrl}/api/production/materials/${material.id}`, { headers: headers() });
            showToast?.('Ролик удалён', 'success');
            setItems((prev) => (prev || []).filter((item) => item.id !== material.id));
            setTotal((prev) => Math.max(0, prev - 1));
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось удалить ролик'), 'error');
        }
    };

    const viewerItems = useMemo(() => {
        const byId = new Map((items || []).map((item) => [item.id, item]));
        return viewer.ids.map((id) => byId.get(id)).filter(Boolean);
    }, [items, viewer.ids]);

    const empty = items && !items.length && !myUploads.length;

    return (
        <div
            className="space-y-3"
            onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
            onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setDragOver(false); }}
            onDrop={(event) => { event.preventDefault(); setDragOver(false); addFiles(event.dataTransfer?.files); }}
        >
            <input
                ref={fileRef}
                type="file"
                multiple
                accept={VIDEO_ACCEPT_ATTRIBUTE}
                className="hidden"
                onChange={(event) => { addFiles(event.target.files); event.target.value = ''; }}
            />
            <div className="flex flex-col gap-2.5 md:flex-row md:items-center md:justify-between">
                <label className="relative block md:w-80">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        type="text"
                        inputMode="search"
                        enterKeyHint="search"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="Поиск по названию ролика"
                        className={`${iosInput} pl-9 pr-9`}
                    />
                    {query && (
                        <button type="button" onClick={() => setQuery('')} aria-label="Очистить поиск"
                            className="absolute right-2 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-full text-slate-400 hover:bg-slate-200 hover:text-slate-600">
                            <X size={13} />
                        </button>
                    )}
                </label>
                {!empty && (
                    <button type="button" className={`${iosBtnPrimary} w-full md:w-auto`} onClick={() => fileRef.current?.click()}>
                        <Upload size={15} /> Загрузить ролик
                    </button>
                )}
            </div>

            {myUploads.length > 0 && (
                <ul className="space-y-2">
                    {myUploads.map((item) => <UploadRow key={item.key} item={item} />)}
                </ul>
            )}

            {dragOver && !empty && (
                <div className="rounded-2xl border-2 border-dashed border-blue-300 bg-blue-50/70 px-4 py-3 text-center text-[13px] font-medium text-blue-700">
                    Отпустите, чтобы загрузить
                </div>
            )}

            {error && !items && (
                <div className="flex items-center justify-between gap-3 rounded-2xl bg-rose-50 px-4 py-3 text-[13.5px] text-rose-700">
                    <span>{error}</span>
                    <button type="button" className={`${iosBtnSecondary} shrink-0 py-1.5`} onClick={() => load()}>Повторить</button>
                </div>
            )}

            {!items && !error && (
                <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5" aria-busy="true">
                    {[0, 1, 2, 3].map((index) => <div key={index} className="aspect-[4/3] animate-pulse rounded-2xl bg-slate-200/60" />)}
                </div>
            )}

            {empty && !query && (
                <div className={`relative flex w-full flex-col items-center gap-2 rounded-2xl border-2 border-dashed px-6 py-12 text-center transition ${
                    dragOver ? 'border-blue-300 bg-blue-50/70' : 'border-slate-200 bg-slate-50/60'
                }`}
                >
                    <button type="button" onClick={() => fileRef.current?.click()} className="flex flex-col items-center gap-2 transition active:scale-[0.98]">
                        <span className="grid h-12 w-12 place-items-center rounded-full bg-white text-blue-600 shadow-sm ring-1 ring-slate-200/70">
                            <Upload size={20} />
                        </span>
                        <span className="text-[15px] font-semibold text-slate-800">Перетащите ролики сюда или выберите</span>
                    </button>
                    <span className="absolute right-3 top-3">
                        <IosHint align="right" text="Ролик без задачи — просто сохранить и показать коллегам. MP4, MOV, WebM, MKV или AVI до 2 ГБ; оборвётся связь — загрузка продолжится с того же места." />
                    </span>
                </div>
            )}
            {empty && query && (
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
                                onOpen={(material) => setViewer({ ids: items.map((row) => row.id), index: items.findIndex((row) => row.id === material.id) })}
                                onDownload={download}
                                onDelete={remove}
                            />
                        ))}
                    </div>
                    {items.length < total && (
                        <div className="flex justify-center pt-1">
                            <button type="button" className={iosBtnSecondary} disabled={loadingMore} onClick={() => load({ append: true })}>
                                {loadingMore && <Loader2 size={15} className="animate-spin" />}
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
                onClose={() => setViewer({ ids: [], index: -1 })}
                onDownload={download}
                onExpired={() => load()}
            />
        </div>
    );
};

export default ProductionLibrary;
