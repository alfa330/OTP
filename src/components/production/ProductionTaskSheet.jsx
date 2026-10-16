import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import axios from 'axios';
import {
    AlertCircle, Check, ChevronDown, Download, ExternalLink, FileText, Film, Image as ImageIcon,
    Loader2, MoreHorizontal, Play, RotateCcw, Trash2, Upload, X,
} from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput, IosHint, IosMenu, IosModal } from '../ui/ios';
import { startFileDownload } from '../../utils/fileDownload';
import { frameSize, productionBriefRows, sizeDimensions } from '../tasks/productionBrief';
import ProductionViewer from './ProductionViewer';
import { uploadOwnerId, uploadStore } from './productionUpload';
import {
    ACCEPT_ATTRIBUTE, STATUS_LABELS, VERSION_STATE_LABELS, dueInfo, filesLabel, formatBytes, formatDateTime,
    formatDuration, formatEta, initialsOf,
} from './productionMeta';

/*
 * Карточка задачи «Продакшн» в разделе «Продакшн» (задача #316).
 *
 * Сверху — что заказали (бриф и описание), ниже — материалы по версиям: та, что
 * собирается сейчас, — первой, отправленные раньше — свёрнутыми под ней, с тем,
 * что ответил постановщик. Внизу — одно-два действия по статусу задачи. Это та
 * же задача, что в «Задачах»: кнопки зовут её обычный маршрут статусов, поэтому
 * права, история и Telegram — общие.
 */

const errorText = (error, fallback) => error?.response?.data?.error || error?.message || fallback;

const KIND_ICON = { video: Film, image: ImageIcon, file: FileText };

export const Avatar = ({ person, size = 22 }) => (
    person?.avatar_url ? (
        <img src={person.avatar_url} alt="" className="shrink-0 rounded-full object-cover ring-2 ring-slate-100"
            style={{ width: size, height: size }} />
    ) : (
        <span className="grid shrink-0 place-items-center rounded-full bg-slate-200 font-semibold text-slate-600 ring-2 ring-slate-100"
            style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }}>
            {initialsOf(person?.name)}
        </span>
    )
);

const SizeFrame = ({ width, height }) => {
    const frame = frameSize(width, height, 14);
    return (
        <span className="inline-grid h-[14px] w-[14px] shrink-0 place-items-center" aria-hidden="true">
            <span className="rounded-[2px] border-[1.5px] border-slate-400" style={{ width: frame.width, height: frame.height }} />
        </span>
    );
};

const Chip = ({ children }) => (
    <span className="inline-flex items-center gap-1.5 rounded-lg bg-slate-100 px-2 py-1 text-[12.5px] text-slate-700">{children}</span>
);

const BriefBlock = ({ brief, description }) => {
    const rows = productionBriefRows(brief);
    if (!rows.length && !description) return null;
    return (
        <section className="rounded-2xl bg-white p-4 ring-1 ring-slate-200/70">
            {rows.length > 0 && (
                <dl className="grid grid-cols-1 gap-x-4 gap-y-2.5 sm:grid-cols-[132px_1fr]">
                    {rows.map((row) => (
                        <React.Fragment key={row.key}>
                            <dt className="text-[12.5px] text-slate-500 sm:pt-1">{row.label}</dt>
                            <dd className="flex flex-wrap gap-1.5 text-[13.5px] text-slate-900">
                                {row.text && !row.colors?.length && <span className="sm:pt-[3px]">{row.text}</span>}
                                {row.colors?.length > 0 && row.colors.map((color) => (
                                    <Chip key={color}>
                                        <i className="h-3 w-3 rounded-full ring-1 ring-black/10" style={{ backgroundColor: color }} aria-hidden="true" />
                                        <span className="tabular-nums">{color}</span>
                                    </Chip>
                                ))}
                                {row.items?.map((item) => <Chip key={item}>{item}</Chip>)}
                                {row.sizes?.map((size) => (
                                    <Chip key={size.key}>
                                        {size.width && size.height && <SizeFrame width={size.width} height={size.height} />}
                                        {size.label && <b className="font-semibold">{size.label}</b>}
                                        {size.width && size.height && (
                                            <span className="tabular-nums text-slate-500">{sizeDimensions(size.width, size.height, size.unit)}</span>
                                        )}
                                    </Chip>
                                ))}
                            </dd>
                        </React.Fragment>
                    ))}
                </dl>
            )}
            {description && (
                <div className={rows.length ? 'mt-3 border-t border-slate-100 pt-3' : ''}>
                    <p className="mb-1 text-[12.5px] text-slate-500">Описание задания</p>
                    <p className="whitespace-pre-wrap break-words text-[13.5px] leading-relaxed text-slate-800">{description}</p>
                </div>
            )}
        </section>
    );
};

export const Thumb = ({ material }) => {
    const Icon = KIND_ICON[material.kind] || FileText;
    // Сломанной считается конкретная ссылка: истёкшую подпись сменит свежая, и кадр вернётся.
    const [brokenUrl, setBrokenUrl] = useState(null);
    const source = material.poster_url || (material.kind === 'image' && material.view_url) || null;
    const poster = source && source !== brokenUrl ? source : null;
    return (
        <span className="relative block aspect-[16/10] overflow-hidden rounded-xl bg-slate-100">
            {poster ? (
                <img src={poster} alt="" loading="lazy" onError={() => setBrokenUrl(source)}
                    className="h-full w-full object-cover" />
            ) : (
                <span className="flex h-full w-full flex-col items-center justify-center gap-1.5 text-slate-400">
                    <Icon size={22} strokeWidth={1.6} />
                    <span className="rounded-md bg-white px-1.5 py-[1px] text-[10.5px] font-semibold uppercase tracking-wide text-slate-500 ring-1 ring-slate-200">
                        {material.extension}
                    </span>
                </span>
            )}
            {material.kind === 'video' && poster && (
                <span className="absolute inset-0 grid place-items-center">
                    <span className="grid h-9 w-9 place-items-center rounded-full bg-black/45 text-white backdrop-blur-sm">
                        <Play size={16} className="translate-x-[1px]" fill="currentColor" />
                    </span>
                </span>
            )}
            {material.kind === 'video' && material.duration_seconds > 0 && (
                <span className="absolute bottom-1.5 right-1.5 rounded-md bg-black/60 px-1.5 py-[1px] text-[11px] font-medium tabular-nums text-white">
                    {formatDuration(material.duration_seconds)}
                </span>
            )}
        </span>
    );
};

export const MaterialTile = ({ material, onOpen, onDownload, onDelete, meta = '' }) => {
    /* Удаление подтверждается на самой плитке, а не вторым окном поверх карточки:
       окно на окне — двойное затемнение, а на телефоне целый экран ради одной строки. */
    const [confirming, setConfirming] = useState(false);
    return (
    <div className="group relative">
        <button type="button" onClick={() => onOpen(material)} className="block w-full text-left transition active:scale-[0.98]">
            <Thumb material={material} />
            <span className="mt-1.5 block truncate px-0.5 text-[12.5px] font-medium text-slate-800" title={material.name}>{material.name}</span>
            <span className="block truncate px-0.5 text-[11.5px] tabular-nums text-slate-400">
                {[formatBytes(material.size), meta].filter(Boolean).join(' · ')}
            </span>
        </button>
        {confirming && (
            <div className="absolute inset-x-0 top-0 flex aspect-[16/10] flex-col items-center justify-center gap-2 rounded-xl bg-slate-900/75 px-2 text-center backdrop-blur-sm">
                <span className="text-[12.5px] font-semibold text-white">Удалить файл?</span>
                <span className="flex gap-1.5">
                    <button type="button" onClick={() => setConfirming(false)}
                        className="rounded-lg bg-white/20 px-2.5 py-1 text-[12px] font-semibold text-white transition hover:bg-white/30 active:scale-95">
                        Отмена
                    </button>
                    <button type="button" onClick={() => { setConfirming(false); onDelete(material); }}
                        className="rounded-lg bg-rose-600 px-2.5 py-1 text-[12px] font-semibold text-white transition hover:bg-rose-700 active:scale-95">
                        Удалить
                    </button>
                </span>
            </div>
        )}
        {!confirming && (
        <span className="absolute right-1.5 top-1.5 opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
            <IosMenu
                label="Действия с файлом"
                /* Меню принимает описание кнопки, а не готовый элемент: класс и значок. */
                trigger={{
                    className: 'grid h-7 w-7 place-items-center rounded-full bg-white/90 text-slate-600 shadow-sm ring-1 ring-black/5 backdrop-blur transition active:scale-95',
                    icon: <MoreHorizontal size={15} />,
                }}
                items={[
                    { key: 'download', label: 'Скачать', icon: Download, onSelect: () => onDownload(material) },
                    material.can_delete && { key: 'delete', label: 'Удалить', icon: Trash2, danger: true, separatorBefore: true, onSelect: () => setConfirming(true) },
                ]}
            />
        </span>
        )}
    </div>
    );
};

const UPLOAD_STATE_TEXT = {
    queued: 'В очереди',
    starting: 'Начинаем…',
    verifying: 'Проверяем файл…',
    waiting: 'Нет связи — продолжим, как только появится',
    done: 'Загружено',
};

export const UploadRow = ({ item }) => {
    const percent = item.size > 0 ? Math.min(100, Math.floor((item.loaded / item.size) * 100)) : 0;
    const failed = item.state === 'failed';
    const status = failed ? item.error : (item.state === 'uploading'
        ? [`${percent}%`, formatEta(item.eta)].filter(Boolean).join(' · ')
        : UPLOAD_STATE_TEXT[item.state] || '');
    return (
        <li className="flex items-center gap-3 rounded-xl bg-white px-3 py-2.5 ring-1 ring-slate-200/70">
            <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg ${failed ? 'bg-rose-50 text-rose-500' : 'bg-blue-50 text-blue-600'}`}>
                {failed ? <AlertCircle size={16} /> : item.state === 'done' ? <Check size={16} /> : <Upload size={15} />}
            </span>
            <span className="min-w-0 flex-1">
                <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-[13px] font-medium text-slate-800" title={item.name}>{item.name}</span>
                    <span className="shrink-0 text-[11.5px] tabular-nums text-slate-400">{formatBytes(item.size)}</span>
                </span>
                {!failed && (
                    <span className="mt-1.5 block h-1.5 overflow-hidden rounded-full bg-slate-100">
                        <span
                            className={`block h-full rounded-full transition-[width] duration-300 ${item.state === 'waiting' ? 'bg-amber-400' : 'bg-blue-500'}`}
                            style={{ width: `${['verifying', 'done'].includes(item.state) ? 100 : percent}%` }}
                        />
                    </span>
                )}
                {status && (
                    <span className={`mt-1 block text-[11.5px] tabular-nums ${failed ? 'text-rose-600' : 'text-slate-500'}`}>{status}</span>
                )}
            </span>
            {failed ? (
                <span className="flex shrink-0 items-center gap-1">
                    {item.retryable !== false && (
                        <button type="button" className={`${iosBtnGhost} px-2`} onClick={() => uploadStore.retry(item.key)} title="Повторить">
                            <RotateCcw size={15} />
                        </button>
                    )}
                    <button type="button" className={`${iosBtnGhost} px-2`} onClick={() => uploadStore.dismiss(item.key)} title="Убрать">
                        <X size={15} />
                    </button>
                </span>
            ) : item.state !== 'done' && (
                <button type="button" className={`${iosBtnGhost} shrink-0 px-2`} onClick={() => uploadStore.cancel(item.key)} title="Отменить загрузку">
                    <X size={15} />
                </button>
            )}
        </li>
    );
};

const RoundNote = ({ version }) => {
    if (version.state === 'returned') {
        return (
            <div className="rounded-xl bg-amber-50 px-3 py-2.5 text-[13px] text-amber-900">
                <p className="font-semibold">
                    Вернули на доработку{version.outcome_at ? ` · ${formatDateTime(version.outcome_at)}` : ''}
                    {version.outcome_by_name ? ` · ${version.outcome_by_name}` : ''}
                </p>
                {version.outcome_comment && <p className="mt-0.5 whitespace-pre-wrap break-words">{version.outcome_comment}</p>}
            </div>
        );
    }
    if (version.state === 'accepted') {
        return (
            <p className="text-[12.5px] text-emerald-700">
                Принято{version.outcome_at ? ` · ${formatDateTime(version.outcome_at)}` : ''}
                {version.outcome_by_name ? ` · ${version.outcome_by_name}` : ''}
            </p>
        );
    }
    if (version.state === 'review' && (version.submitted_at || version.comment)) {
        return (
            <div className="rounded-xl bg-slate-50 px-3 py-2.5 text-[13px] text-slate-700">
                <p className="text-[12.5px] text-slate-500">
                    Отправлено{version.submitted_at ? ` · ${formatDateTime(version.submitted_at)}` : ''}
                    {version.submitted_by_name ? ` · ${version.submitted_by_name}` : ''}
                </p>
                {version.comment && version.comment !== 'Работа выполнена' && (
                    <p className="mt-0.5 whitespace-pre-wrap break-words">{version.comment}</p>
                )}
            </div>
        );
    }
    return null;
};

const ProductionTaskSheet = ({
    taskId, open, onClose, apiBaseUrl, headers, showToast, onChanged, onOpenInTasks,
}) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [loadError, setLoadError] = useState('');
    const [mode, setMode] = useState(null); // 'submit' | 'return'
    const [text, setText] = useState('');
    const [busy, setBusy] = useState('');
    const [issue, setIssue] = useState(null); // {where: 'materials'|'text', text}
    const [openOlder, setOpenOlder] = useState(() => new Set());
    // В просмотре — номера материалов, а сами строки берутся из свежего ответа: после
    // обновления (истёкшая подпись) просмотр получает новые ссылки, а не старый снимок.
    const [viewer, setViewer] = useState({ ids: [], index: -1 });
    const [dragOver, setDragOver] = useState(false);
    const fileRef = useRef(null);
    const materialsRef = useRef(null);
    const textRef = useRef(null);
    const requestRef = useRef(0);
    /* Какая задача открыта СЕЙЧАС. Ответ на запрос другой задачи (догрузился файл
       задачи, которую уже закрыли) в карточку не кладём: иначе в карточке B
       оказались бы бриф и файлы задачи A, а кнопки били бы в B. */
    const taskIdRef = useRef(taskId);
    taskIdRef.current = taskId;
    const reloadTimerRef = useRef(null);

    const uploads = useSyncExternalStore(uploadStore.subscribe, uploadStore.snapshot, uploadStore.snapshot);
    // Только свои строки этой задачи: после выхода и входа другого человека чужие не видны.
    const myUploads = useMemo(() => {
        const owner = uploadOwnerId();
        return uploads.filter((item) => item.taskId === Number(taskId) && (!item.ownerId || item.ownerId === owner));
    }, [uploads, taskId]);
    const uploading = myUploads.some((item) => !['failed', 'done'].includes(item.state));

    const load = useCallback(async ({ quiet = false } = {}) => {
        const forTask = Number(taskId);
        if (!forTask) return;
        const ticket = requestRef.current + 1;
        requestRef.current = ticket;
        if (!quiet) setLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/production/tasks/${forTask}`, { headers: headers() });
            if (requestRef.current !== ticket || Number(taskIdRef.current) !== forTask) return;
            if (Number(response.data?.task?.id) !== forTask) return;
            setData(response.data);
            setLoadError('');
            return response.data;
        } catch (error) {
            if (requestRef.current !== ticket || Number(taskIdRef.current) !== forTask) return;
            // Тихое обновление открытой карточки не ломает её: старое содержимое остаётся.
            if (!quiet) setLoadError(errorText(error, 'Не удалось открыть задачу'));
        } finally {
            if (requestRef.current === ticket) setLoading(false);
        }
    }, [apiBaseUrl, headers, taskId]);
    const loadRef = useRef(load);
    loadRef.current = load;

    useEffect(() => {
        if (!open) return;
        setData(null);
        setLoadError('');
        setMode(null);
        setText('');
        setIssue(null);
        setOpenOlder(new Set());
        setViewer({ ids: [], index: -1 });
        load();
    }, [open, taskId, load]);

    useEffect(() => () => window.clearTimeout(reloadTimerRef.current), []);

    const task = data?.task;
    const permissions = data?.permissions || {};
    const versions = data?.versions || [];
    const working = versions.find((version) => version.state === 'working') || null;
    const current = working || versions[0] || null;
    const older = versions.filter((version) => version !== current);
    // Что просили поправить: возврат прошлой версии показываем над той, что в работе.
    const returnedBefore = working ? versions.find((version) => version.version === working.version - 1 && version.state === 'returned') : null;
    const othersUploading = data?.others_uploading || [];
    const currentReady = (current?.materials || []).filter((material) => material.status === 'ready');
    const showDropzone = permissions.can_upload && !currentReady.length && !myUploads.length;

    const viewerItems = useMemo(() => {
        const byId = new Map(versions.flatMap((version) => version.materials || []).map((material) => [material.id, material]));
        return viewer.ids.map((id) => byId.get(id)).filter(Boolean);
    }, [versions, viewer.ids]);

    /* Пачка файлов — одно обновление: каждый догрузившийся файл звал бы и карточку,
       и доску, и при 60 файлах это сотня запросов подряд к общему серверу. */
    const scheduleReload = (doneTaskId) => {
        window.clearTimeout(reloadTimerRef.current);
        reloadTimerRef.current = window.setTimeout(() => {
            if (Number(taskIdRef.current) === Number(doneTaskId)) loadRef.current({ quiet: true });
            onChanged?.();
        }, 700);
    };

    const addFiles = (files) => {
        const list = Array.from(files || []);
        if (!list.length || !permissions.can_upload) return;
        setIssue(null);
        uploadStore.add({
            taskId,
            files: list,
            apiBaseUrl,
            headers,
            onDone: (doneTaskId) => scheduleReload(doneTaskId),
        });
    };

    const callStatus = async (action, body, success) => {
        setBusy(action);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/tasks/${taskId}/status`, { action, ...body }, { headers: headers() });
            showToast?.(success, 'success');
            if (response.data?.warning) showToast?.(response.data.warning, 'warning');
            setMode(null);
            setText('');
            setIssue(null);
            await load({ quiet: true });
            onChanged?.();
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось сохранить'), 'error');
        } finally {
            setBusy('');
        }
    };

    const flagIssue = (where, message) => {
        setIssue({ where, text: message });
        window.requestAnimationFrame(() => {
            const target = where === 'materials' ? materialsRef.current : textRef.current;
            target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
            if (where === 'text') textRef.current?.focus({ preventScroll: true });
        });
    };

    const submit = async () => {
        if (uploading) {
            flagIssue('materials', 'Дождитесь, пока файлы догрузятся');
            return;
        }
        // Карточку могли открыть давно: свежим ответом проверяем, не грузит ли кто-то
        // из соисполнителей файл в эту же версию прямо сейчас.
        setBusy('checking');
        const fresh = (await load({ quiet: true })) || data;
        setBusy('');
        const freshWorking = (fresh?.versions || []).find((version) => version.state === 'working');
        const ready = (freshWorking?.materials || []).filter((material) => material.status === 'ready');
        const others = fresh?.others_uploading || [];
        if (others.length) {
            const first = others[0];
            flagIssue('materials', `У ${first.uploaded_by_name || 'соисполнителя'} ещё грузится «${first.name}» — дождитесь, чтобы он попал в эту версию`);
            return;
        }
        if (!ready.length) {
            flagIssue('materials', 'Загрузите хотя бы один файл — постановщику нечего будет проверить');
            return;
        }
        if (mode !== 'submit') {
            setMode('submit');
            setIssue(null);
            window.requestAnimationFrame(() => textRef.current?.focus());
            return;
        }
        callStatus('completed', { report: text.trim() }, 'Отправлено на проверку');
    };

    const sendBack = () => {
        if (mode !== 'return') {
            setMode('return');
            setIssue(null);
            window.requestAnimationFrame(() => textRef.current?.focus());
            return;
        }
        if (!text.trim()) {
            flagIssue('text', 'Напишите, что поправить');
            return;
        }
        callStatus('returned', { comment: text.trim() }, 'Вернули на доработку');
    };

    const download = async (material) => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/production/materials/${material.id}/download`, { headers: headers() });
            startFileDownload(response.data?.url);
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось скачать файл'), 'error');
        }
    };

    const remove = async (material) => {
        try {
            await axios.delete(`${apiBaseUrl}/api/production/materials/${material.id}`, { headers: headers() });
            showToast?.('Файл удалён', 'success');
            await load({ quiet: true });
            onChanged?.();
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось удалить файл'), 'error');
        }
    };

    const openViewer = (list, material) => {
        const ready = list.filter((item) => item.status === 'ready');
        setViewer({ ids: ready.map((item) => item.id), index: Math.max(0, ready.findIndex((item) => item.id === material.id)) });
    };

    const renderMaterials = (version, { droppable = false } = {}) => {
        const ready = (version.materials || []).filter((material) => material.status === 'ready');
        return (
            <>
                {ready.length > 0 && (
                    <div className="grid grid-cols-2 gap-x-2.5 gap-y-3 sm:grid-cols-3">
                        {ready.map((material) => (
                            <MaterialTile
                                key={material.id}
                                material={material}
                                onOpen={(item) => openViewer(ready, item)}
                                onDownload={download}
                                onDelete={remove}
                            />
                        ))}
                    </div>
                )}
                {!ready.length && !droppable && (
                    <p className="text-[13px] text-slate-400">Файлов в этой версии нет</p>
                )}
            </>
        );
    };

    const due = task ? dueInfo(task) : null;
    const assignees = task?.assignees || [];

    const footer = task && (permissions.can_take || permissions.can_submit || permissions.can_review) ? (
        <div className="w-full space-y-2.5">
            {mode && (
                <div>
                    <textarea
                        ref={textRef}
                        value={text}
                        onChange={(event) => { setText(event.target.value); if (issue?.where === 'text') setIssue(null); }}
                        rows={3}
                        maxLength={4000}
                        placeholder={mode === 'submit'
                            ? 'Комментарий для постановщика — что сделано, на что обратить внимание'
                            : 'Что поправить'}
                        className={`${iosInput} resize-none ${issue?.where === 'text' ? 'ring-2 ring-rose-400 focus:ring-rose-400' : ''}`}
                    />
                    {issue?.where === 'text' && <p className="mt-1 px-1 text-[12px] text-rose-600">{issue.text}</p>}
                </div>
            )}
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                {mode && (
                    <button type="button" className={iosBtnSecondary} onClick={() => { setMode(null); setText(''); setIssue(null); }} disabled={Boolean(busy)}>
                        Отмена
                    </button>
                )}
                {permissions.can_take && (
                    <button type="button" className={iosBtnPrimary} disabled={Boolean(busy)}
                        onClick={() => callStatus('in_progress', {}, 'Задача в работе')}>
                        {busy === 'in_progress' && <Loader2 size={15} className="animate-spin" />} Взять в работу
                    </button>
                )}
                {permissions.can_submit && (
                    <button type="button" className={iosBtnPrimary} disabled={Boolean(busy)} onClick={submit}>
                        {busy === 'completed' && <Loader2 size={15} className="animate-spin" />}
                        {mode === 'submit' ? 'Отправить' : 'Отправить на проверку'}
                    </button>
                )}
                {permissions.can_review && mode !== 'submit' && (
                    <>
                        <button type="button" className={mode === 'return' ? iosBtnPrimary : iosBtnSecondary} disabled={Boolean(busy)} onClick={sendBack}>
                            {busy === 'returned' && <Loader2 size={15} className="animate-spin" />}
                            {mode === 'return' ? 'Вернуть' : 'Вернуть на доработку'}
                        </button>
                        {mode !== 'return' && (
                            <button type="button" className={iosBtnPrimary} disabled={Boolean(busy)}
                                onClick={() => callStatus('accepted', { comment: '' }, 'Принято')}>
                                {busy === 'accepted' && <Loader2 size={15} className="animate-spin" />} Принять
                            </button>
                        )}
                    </>
                )}
            </div>
        </div>
    ) : null;

    return (
        <>
            <IosModal
                open={open}
                onClose={onClose}
                title={task?.subject || 'Задача'}
                // Наименование уже в брифе, а статус — только здесь: заголовок «Материалы»
                // его не повторяет.
                subtitle={task ? STATUS_LABELS[task.status] || '' : ''}
                maxWidth="max-w-3xl"
                footer={footer}
                headerActions={task && onOpenInTasks ? (
                    <button type="button" className={`${iosBtnGhost} whitespace-nowrap`} onClick={() => onOpenInTasks(task.id)} title="Открыть задачу в «Задачах»">
                        <ExternalLink size={15} /> <span className="hidden sm:inline">В «Задачах»</span>
                    </button>
                ) : null}
            >
                {loading && !data && (
                    <div className="space-y-3 py-1" aria-busy="true">
                        {['h-24', 'h-40'].map((height) => <div key={height} className={`${height} animate-pulse rounded-2xl bg-slate-100`} />)}
                    </div>
                )}
                {loadError && !data && (
                    <div className="flex items-center justify-between gap-3 rounded-2xl bg-rose-50 px-4 py-3 text-[13.5px] text-rose-700">
                        <span>{loadError}</span>
                        <button type="button" className={`${iosBtnSecondary} shrink-0 py-1.5`} onClick={() => load()}>Повторить</button>
                    </div>
                )}
                {task && (
                    <div
                        className="space-y-4"
                        onDragOver={(event) => {
                            if (!permissions.can_upload) return;
                            event.preventDefault();
                            setDragOver(true);
                        }}
                        onDragLeave={(event) => {
                            if (event.currentTarget.contains(event.relatedTarget)) return;
                            setDragOver(false);
                        }}
                        onDrop={(event) => {
                            if (!permissions.can_upload) return;
                            event.preventDefault();
                            setDragOver(false);
                            addFiles(event.dataTransfer?.files);
                        }}
                    >
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px] text-slate-500">
                            {assignees.length > 0 && (
                                <span className="flex items-center gap-2">
                                    <span className="flex -space-x-1.5">
                                        {assignees.slice(0, 3).map((person) => <Avatar key={person.id} person={person} />)}
                                    </span>
                                    <span className="text-slate-700">{assignees.map((person) => person.name).join(', ')}</span>
                                </span>
                            )}
                            {(task.requested_by_name || task.created_by_name) && (
                                <span>Поставил: <span className="text-slate-700">{task.requested_by_name || task.created_by_name}</span></span>
                            )}
                            {due && (
                                <span className={due.overdue ? 'font-medium text-rose-600' : ''}>
                                    Срок: <span className={due.overdue ? '' : 'text-slate-700'}>{due.label}</span>
                                </span>
                            )}
                        </div>

                        <BriefBlock brief={task.brief} description={task.description} />

                        <section ref={materialsRef} className="space-y-3">
                            <div className="flex items-baseline justify-between gap-3">
                                <h3 className="text-[15px] font-semibold text-slate-900">
                                    Материалы
                                    {current && (
                                        <span className="ml-2 text-[13px] font-normal tabular-nums text-slate-500">версия {current.version}</span>
                                    )}
                                </h3>
                                {permissions.can_upload && !showDropzone && (
                                    <button type="button" className={`${iosBtnSecondary} py-2`} onClick={() => fileRef.current?.click()}>
                                        <Upload size={15} /> Загрузить
                                    </button>
                                )}
                            </div>

                            {returnedBefore && <RoundNote version={returnedBefore} />}
                            {current && <RoundNote version={current} />}

                            {permissions.can_upload && (
                                <>
                                    <input
                                        ref={fileRef}
                                        type="file"
                                        multiple
                                        accept={ACCEPT_ATTRIBUTE}
                                        className="hidden"
                                        onChange={(event) => { addFiles(event.target.files); event.target.value = ''; }}
                                    />
                                    {showDropzone && (
                                        <div
                                            className={`relative flex w-full flex-col items-center gap-2 rounded-2xl border-2 border-dashed px-6 py-8 text-center transition ${
                                                dragOver ? 'border-blue-300 bg-blue-50/70' : 'border-slate-200 bg-slate-50/60'
                                            } ${issue?.where === 'materials' ? 'border-rose-300 bg-rose-50/50' : ''}`}
                                        >
                                            <button
                                                type="button"
                                                onClick={() => fileRef.current?.click()}
                                                className="flex flex-col items-center gap-2 transition active:scale-[0.98]"
                                            >
                                                <span className="grid h-11 w-11 place-items-center rounded-full bg-white text-blue-600 shadow-sm ring-1 ring-slate-200/70">
                                                    <Upload size={19} />
                                                </span>
                                                <span className="text-[14px] font-semibold text-slate-800">Перетащите файлы сюда или выберите</span>
                                            </button>
                                            {/* Что можно загружать — под «i», а не строкой: нужно один раз. */}
                                            <span className="absolute right-3 top-3">
                                                <IosHint text="Ролики до 2 ГБ (MP4, MOV, WebM), картинки, PDF, PSD, AI, EPS, архивы и звук. Файл едет прямо в облако; оборвётся связь — загрузка продолжится с того же места." align="right" />
                                            </span>
                                        </div>
                                    )}
                                    {dragOver && currentReady.length > 0 && (
                                        <div className="rounded-2xl border-2 border-dashed border-blue-300 bg-blue-50/70 px-4 py-3 text-center text-[13px] font-medium text-blue-700">
                                            Отпустите, чтобы загрузить
                                        </div>
                                    )}
                                </>
                            )}

                            {othersUploading.length > 0 && (
                                <p className="flex items-center gap-1.5 px-1 text-[12.5px] text-slate-500">
                                    <Loader2 size={13} className="animate-spin" />
                                    {othersUploading.map((item) => `${item.uploaded_by_name || 'Соисполнитель'} грузит «${item.name}»`).join(', ')}
                                </p>
                            )}

                            {myUploads.length > 0 && (
                                <ul className="space-y-2">
                                    {myUploads.map((item) => <UploadRow key={item.key} item={item} />)}
                                </ul>
                            )}

                            {issue?.where === 'materials' && (
                                <p className="flex items-center gap-1.5 px-1 text-[12.5px] text-rose-600">
                                    <AlertCircle size={14} /> {issue.text}
                                </p>
                            )}

                            {current && renderMaterials(current, { droppable: permissions.can_upload })}
                            {!current && !permissions.can_upload && (
                                <p className="text-[13px] text-slate-400">
                                    {task.status === 'assigned' ? 'Исполнитель ещё не взял задачу в работу' : 'Материалов пока нет'}
                                </p>
                            )}
                        </section>

                        {older.length > 0 && (
                            <section className="space-y-2">
                                <h3 className="px-1 text-[12.5px] font-semibold uppercase tracking-wider text-slate-500">Прошлые версии</h3>
                                {older.map((version) => {
                                    const expanded = openOlder.has(version.version);
                                    const count = (version.materials || []).filter((material) => material.status === 'ready').length;
                                    return (
                                        <div key={version.version} className="rounded-2xl bg-white ring-1 ring-slate-200/70">
                                            <button
                                                type="button"
                                                className="flex w-full items-center gap-3 px-4 py-3 text-left"
                                                onClick={() => setOpenOlder((prev) => {
                                                    const next = new Set(prev);
                                                    if (next.has(version.version)) next.delete(version.version);
                                                    else next.add(version.version);
                                                    return next;
                                                })}
                                                aria-expanded={expanded}
                                            >
                                                <span className="min-w-0 flex-1">
                                                    <span className="block text-[13.5px] font-semibold text-slate-800">
                                                        Версия {version.version}
                                                        <span className="ml-2 font-normal text-slate-500">{VERSION_STATE_LABELS[version.state]}</span>
                                                    </span>
                                                    <span className="block text-[12px] tabular-nums text-slate-400">
                                                        {[filesLabel(count), version.submitted_at ? formatDateTime(version.submitted_at) : ''].filter(Boolean).join(' · ')}
                                                    </span>
                                                </span>
                                                <ChevronDown size={16} className={`shrink-0 text-slate-400 transition-transform ${expanded ? 'rotate-180' : ''}`} />
                                            </button>
                                            {expanded && (
                                                <div className="space-y-3 border-t border-slate-100 px-4 pb-4 pt-3">
                                                    <RoundNote version={version} />
                                                    {renderMaterials(version)}
                                                </div>
                                            )}
                                        </div>
                                    );
                                })}
                            </section>
                        )}
                    </div>
                )}
            </IosModal>

            <ProductionViewer
                items={viewerItems}
                index={viewerItems.length ? Math.min(viewer.index, viewerItems.length - 1) : -1}
                onIndex={(index) => setViewer((prev) => ({ ...prev, index }))}
                onClose={() => setViewer({ ids: [], index: -1 })}
                onDownload={download}
                onExpired={() => load({ quiet: true })}
            />
        </>
    );
};

export default ProductionTaskSheet;
