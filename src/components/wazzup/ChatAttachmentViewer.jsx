import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import axios from 'axios';
import { Check, ChevronLeft, ChevronRight, Copy, Download, FileText, Loader2, Maximize2, Minimize2, PictureInPicture2, ScanText, Undo2, ZoomIn, ZoomOut } from 'lucide-react';
import { IosModal } from '../ui/ios';
import { ATTACHMENT_MAX_BYTES, attachmentName, attachmentPreviewKind, boundedCanvasSize, pdfPageText, saveAttachment } from './chatAttachments';
import ChatAttachmentStrip from './ChatAttachmentStrip';
import { attachmentCache } from './attachmentCache';
import useAttachmentPip from './useAttachmentPip';
import { moveAttachmentHost } from './moveAttachmentHost';
import { renderPdfCanvas } from './renderPdfCanvas';

const iconButton = 'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100 disabled:opacity-35';
const actionButton = 'inline-flex items-center justify-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-40';

async function requestError(error, fallback) {
    const body = error?.response?.data;
    if (body instanceof Blob && body.size < 32000) {
        try { return JSON.parse(await body.text()).error || fallback; } catch { return fallback; }
    }
    return typeof body?.error === 'string' ? body.error : fallback;
}

function PdfPage({ document: pdf, library, number, zoom, onReady, onError, hostWindow }) {
    const box = useRef(null);
    const [width, setWidth] = useState(740);
    const canvas = useRef(null);
    const textLayer = useRef(null);
    const [size, setSize] = useState(null);
    useEffect(() => {
        const element = box.current;
        let timer;
        const resize = () => {
            clearTimeout(timer);
            timer = setTimeout(() => {
                if (element.clientWidth > 24) setWidth(Math.max(180, element.clientWidth - 24));
            }, 80);
        };
        if (element.clientWidth > 24) setWidth(Math.max(180, element.clientWidth - 24));
        const observer = new hostWindow.ResizeObserver(resize);
        observer.observe(element);
        return () => { observer.disconnect(); clearTimeout(timer); };
    }, [hostWindow]);
    useEffect(() => {
        let cancelled = false;
        let render;
        let selection;
        // A fresh canvas per render prevents a cancelled PDF.js task from
        // racing with its replacement when zoom changes quickly.
        const ownerDocument = box.current.ownerDocument;
        const target = ownerDocument.createElement('canvas');
        const layer = ownerDocument.createElement('div');
        layer.className = 'textLayer wazzup-pdf-text';
        canvas.current.replaceChildren(target);
        textLayer.current.replaceChildren(layer);
        onReady(null);
        (async () => {
            const page = await pdf.getPage(number);
            if (cancelled) return;
            const natural = page.getViewport({ scale: 1 });
            const view = page.getViewport({ scale: Math.min(1.4, width / natural.width) * zoom });
            const pixels = boundedCanvasSize(view.width, view.height, Math.min(ownerDocument.defaultView.devicePixelRatio || 1, 2));
            target.width = pixels.width;
            target.height = pixels.height;
            target.style.width = `${view.width}px`;
            target.style.height = `${view.height}px`;
            target.style.display = 'block';
            target.setAttribute('aria-label', `Страница ${number}`);
            layer.style.setProperty('--scale-factor', view.scale);
            layer.style.setProperty('--total-scale-factor', view.scale * (view.userUnit || 1));
            setSize({ width: view.width, height: view.height });
            render = renderPdfCanvas(page, { canvas: target, viewport: view,
                transform: [pixels.scale, 0, 0, pixels.scale, 0, 0], background: '#fff' });
            const [content] = await Promise.all([page.getTextContent(), render.promise]);
            if (cancelled) return;
            selection = new library.TextLayer({ textContentSource: content, container: layer, viewport: view });
            await selection.render();
            if (!cancelled) onReady({ page, text: pdfPageText(content) });
        })().catch((error) => {
            if (!cancelled && error?.name !== 'RenderingCancelledException') {
                onError('Не удалось отобразить эту страницу. Попробуйте другую страницу или скачайте файл.');
            }
        });
        return () => { cancelled = true; render?.cancel(); selection?.cancel(); };
    }, [pdf, library, number, zoom, width, onReady, onError, hostWindow]);
    return <div ref={box} className="wazzup-scrollbar h-full overflow-auto p-3">
        <div className="relative mx-auto bg-white shadow-sm" style={size || { minHeight: 200 }}>
            <div ref={canvas} />
            <div ref={textLayer} />
        </div>
    </div>;
}

async function rasterPage(asset, currentPage, task) {
    const target = document.createElement('canvas');
    let image;
    let page;
    let natural;
    if (asset.kind === 'pdf') {
        page = currentPage.page;
        natural = page.getViewport({ scale: 1 });
    } else {
        image = new Image();
        image.src = asset.url;
        await image.decode();
        natural = { width: image.naturalWidth, height: image.naturalHeight };
    }
    if (task.controller.signal.aborted) return null;
    const size = boundedCanvasSize(natural.width, natural.height, asset.kind === 'pdf' ? 2.5 : 1,
        1920 * 1920, 1920);
    target.width = size.width;
    target.height = size.height;
    const context = target.getContext('2d');
    context.fillStyle = '#fff';
    context.fillRect(0, 0, size.width, size.height);
    try {
        if (page) {
            task.render = page.render({ canvasContext: context,
                viewport: page.getViewport({ scale: size.scale }), background: '#fff' });
            await task.render.promise;
        } else context.drawImage(image, 0, 0, size.width, size.height);
        if (task.controller.signal.aborted) return null;
        return target.toDataURL('image/jpeg', 0.88);
    } finally { target.width = 1; target.height = 1; }
}

// The download slots on the server are shared by the whole team: a busy answer
// is retried quietly a couple of times before the operator is told about it.
const BUSY_RETRY_DELAYS = [600, 1200];

async function downloadAttachment(get, signal) {
    for (let attempt = 0; ; attempt += 1) {
        try {
            return await get();
        } catch (failure) {
            const delay = BUSY_RETRY_DELAYS[attempt];
            if (failure?.response?.status !== 429 || delay === undefined || signal.aborted) throw failure;
            await new Promise((resolve) => { setTimeout(resolve, delay); });
            if (signal.aborted) throw failure;
        }
    }
}

// cache keeps downloaded files and recognized text for the tab's lifetime
// (attachmentCache.js): reopening an attachment neither downloads it again nor
// repeats the paid recognition.
export default function ChatAttachmentViewer({ apiBaseUrl, headers, chat, message, items = [], onSelect, onClose,
    onDetachedChange, cache = attachmentCache }) {
    // Moving this host preserves the actual video, PDF canvas, selection and
    // React subtree. Changing createPortal's target would remount them all.
    const homeDocument = useRef(document);
    const [portalHost] = useState(() => document.createElement('div'));
    const cancelMediaTransfer = useRef(null);
    const moveHost = useCallback((destination) => {
        if (portalHost.parentNode !== destination) {
            cancelMediaTransfer.current = moveAttachmentHost(portalHost, destination);
        }
    }, [portalHost]);
    const returnHost = useCallback(() => {
        if (portalHost.isConnected) moveHost(homeDocument.current.body);
    }, [portalHost, moveHost]);
    const pip = useAttachmentPip({ onReturn: returnHost });
    useLayoutEffect(() => {
        portalHost.style.height = pip.pipWindow ? '100%' : '';
        moveHost((pip.pipWindow?.document || homeDocument.current).body);
    }, [pip.pipWindow, portalHost, moveHost]);
    useLayoutEffect(() => () => portalHost.remove(), [portalHost]);
    const detachedCallback = useRef(onDetachedChange);
    detachedCallback.current = onDetachedChange;
    useLayoutEffect(() => {
        detachedCallback.current?.(Boolean(pip.pipWindow));
    }, [pip.pipWindow]);
    const [loadedAsset, setAsset] = useState(null);
    const [loadedDownload, setDownload] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [page, setPage] = useState(1);
    const [zoom, setZoom] = useState(1);
    const [currentPage, setCurrentPage] = useState(null);
    const [extracting, setExtracting] = useState(false);
    const [extracted, setExtracted] = useState(null);
    const [textView, setTextView] = useState(false);
    const [expandedText, setExpandedText] = useState(false);
    const [copied, setCopied] = useState(false);
    const [retry, setRetry] = useState(0);
    const [failedPreview, setFailedPreview] = useState(null);
    const latest = useRef({});
    // A replacement file or another API must not inherit the old file's bytes
    // or recognized text, even if the message identifiers are unchanged.
    const sourceKey = JSON.stringify([apiBaseUrl, chat.channelId, chat.chatId, message.messageId, message.contentUri]);
    useLayoutEffect(() => () => cancelMediaTransfer.current?.(), [sourceKey]);
    latest.current = { headers, onClose, items, onSelect, sourceKey };
    const navigation = useRef({ sourceKey, messageId: message.messageId });
    if (navigation.current.sourceKey !== sourceKey) {
        navigation.current = { sourceKey, messageId: message.messageId };
    }
    const request = useRef(null);
    const contentSource = useRef(null);
    const currentSource = contentSource.current === sourceKey;
    const asset = loadedAsset?.sourceKey === sourceKey ? loadedAsset : null;
    const download = loadedDownload?.sourceKey === sourceKey ? loadedDownload : null;
    const isVideo = attachmentPreviewKind(message) === 'video';
    // The conversation has already displayed this URL. Reuse the browser's
    // image cache immediately while the authenticated OCR/download copy loads.
    const directImage = attachmentPreviewKind(message) === 'image' && failedPreview !== sourceKey
        ? message.contentUri : null;
    const imageUrl = directImage || (asset?.kind === 'image' ? asset.url : null);
    const knownUnsupported = attachmentPreviewKind(message) === 'document' && Boolean(attachmentName(message));
    // A cached image is on screen one frame after opening: showing the progress
    // indicator for that frame would only make the viewer flicker.
    const instant = !knownUnsupported && Boolean(cache.peekMedia(sourceKey)?.type.startsWith('image/'));
    const extraction = useRef(null);
    const copyTimer = useRef(null);
    const copyState = useRef({ mounted: false, version: 0 });
    const container = useRef(null);
    const resultBox = useRef(null);
    const video = useRef(null);
    const close = useCallback(() => {
        video.current?.pause();
        latest.current.onClose();
    }, []);
    const ids = { account: 'op', channelId: chat.channelId, chatId: chat.chatId, messageId: message.messageId };
    const cancelExtraction = useCallback(() => {
        extraction.current?.controller.abort();
        extraction.current?.render?.cancel();
        extraction.current = null;
    }, []);
    const select = useCallback((item) => {
        if (!latest.current.onSelect || !item || item.messageId === navigation.current.messageId) return;
        navigation.current.messageId = item.messageId;
        copyState.current.version += 1;
        clearTimeout(copyTimer.current);
        request.current?.abort();
        video.current?.pause();
        cancelExtraction();
        setRetry((value) => value + 1);
        setExtracting(false); setExtracted(null); setCopied(false);
        latest.current.onSelect(item);
    }, [cancelExtraction]);
    const step = useCallback((offset) => {
        const group = latest.current.items;
        const index = group.findIndex((item) => item.messageId === navigation.current.messageId);
        if (index >= 0 && group[index + offset]) select(group[index + offset]);
    }, [select]);
    useEffect(() => {
        copyState.current.mounted = true;
        copyState.current.version += 1;
        clearTimeout(copyTimer.current);
        const controller = new AbortController();
        request.current = controller;
        contentSource.current = sourceKey;
        let loadingTask;
        let url;
        const cached = knownUnsupported || isVideo ? null : cache.getMedia(sourceKey);
        const currentVideo = isVideo ? video.current : null;
        // React StrictMode reruns setup after releasing the same media node.
        if (currentVideo && !currentVideo.getAttribute('src')) currentVideo.src = message.contentUri;
        // A cached image appears at once, without the progress indicator; a PDF
        // still has to be parsed, so it keeps the indicator.
        setLoading(!isVideo && !knownUnsupported && !(cached && cached.type.startsWith('image/')));
        // Text recognized earlier for this page is shown again without a new request.
        setAsset(null); setDownload(null); setError(''); setExtracted(cache.getText(sourceKey, 1)); setPage(1); setZoom(1); setCurrentPage(null); setExtracting(false); setCopied(false);
        setTextView(false); setExpandedText(false);
        (async () => {
            // Video uses the same native streaming URL already available in the
            // authorized conversation. It never hits the image/PDF proxy or OCR.
            if (knownUnsupported || isVideo) return;
            let blob = cached;
            if (!blob) {
                ({ data: blob } = await downloadAttachment(() => axios.get(`${apiBaseUrl}/api/wazzup/pilot/attachment`, {
                    headers: latest.current.headers(), params: ids, responseType: 'blob',
                    signal: controller.signal, timeout: 35000,
                }), controller.signal));
                if (controller.signal.aborted) return;
                if (!(blob instanceof Blob) || !blob.size || blob.size > ATTACHMENT_MAX_BYTES) {
                    throw new Error('Файл пустой или превышает 20 МБ.');
                }
            }
            const mime = blob.type.split(';')[0].toLowerCase();
            const kind = mime === 'application/pdf' ? 'pdf' : /^image\/(jpeg|png|webp|gif|bmp)$/.test(mime) ? 'image' : null;
            if (!kind) throw new Error('Этот формат пока нельзя просмотреть в чате. Откройте оригинал.');
            url = URL.createObjectURL(blob);
            const name = attachmentName(message) || (kind === 'pdf' ? 'Документ.pdf' : `Изображение.${mime.split('/')[1]}`);
            setDownload({ url, name, sourceKey });
            let pdf;
            let library;
            if (kind === 'pdf') {
                library = await import('./pdfRuntime');
                if (controller.signal.aborted) return;
                const data = new Uint8Array(await blob.arrayBuffer());
                if (controller.signal.aborted) return;
                loadingTask = library.getDocument({ data, isEvalSupported: false, enableXfa: false,
                    maxImageSize: 16_000_000, verbosity: 0 });
                pdf = await loadingTask.promise;
                if (controller.signal.aborted) return;
            }
            // Parse a PDF before keeping it: otherwise a partial or broken
            // download would make every retry reuse the same unreadable file.
            if (blob !== cached) cache.putMedia(sourceKey, blob);
            setAsset({ kind, url, pdf, library, name, sourceKey });
        })().catch(async (failure) => {
            const message = failure?.name === 'PasswordException' ? 'PDF защищён паролем. Скачайте его для открытия.'
                : await requestError(failure, failure?.response ? 'Не удалось загрузить вложение.'
                    : failure?.message || 'Не удалось открыть вложение.');
            if (!controller.signal.aborted) setError(message);
        }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => {
            copyState.current.mounted = false;
            copyState.current.version += 1;
            clearTimeout(copyTimer.current);
            controller.abort();
            currentVideo?.pause();
            currentVideo?.removeAttribute('src');
            currentVideo?.load();
            cancelExtraction();
            loadingTask?.destroy()?.catch(() => {});
            if (url) URL.revokeObjectURL(url);
        };
        // headers is refreshed by SSE renders; it must not reload the document.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sourceKey, retry, cancelExtraction, knownUnsupported, isVideo, cache]);
    useEffect(() => {
        const ownerDocument = pip.pipWindow?.document || homeDocument.current;
        const previousFocus = ownerDocument.activeElement;
        container.current?.focus();
        const escape = (event) => {
            if (event.key === 'Escape') { event.stopPropagation(); close(); }
            if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey
                && !event.defaultPrevented && !event.isComposing && !event.target?.isContentEditable
                && !event.target?.closest?.('input, textarea, select, video, [contenteditable], [role="slider"], [role="textbox"]')
                && (!event.target?.closest?.('button, a') || event.target?.closest?.('[data-attachment-navigation]'))
                && latest.current.items.length > 1 && latest.current.onSelect) {
                event.preventDefault(); step(event.key === 'ArrowLeft' ? -1 : 1);
            }
        };
        ownerDocument.addEventListener('keydown', escape);
        return () => {
            ownerDocument.removeEventListener('keydown', escape);
            if (previousFocus?.isConnected) previousFocus.focus();
        };
    }, [step, close, pip.pipWindow]);
    const ready = useCallback((value) => { if (latest.current.sourceKey === sourceKey && navigation.current.messageId === message.messageId) setCurrentPage(value); }, [sourceKey, message.messageId]);
    const pageError = useCallback((value) => { if (latest.current.sourceKey === sourceKey && navigation.current.messageId === message.messageId) setError(value); }, [sourceKey, message.messageId]);
    const changePage = (value) => {
        copyState.current.version += 1;
        cancelExtraction(); setExtracting(false); setError(''); setCurrentPage(null);
        setCopied(false); setPage(value); setExtracted(cache.getText(sourceKey, value));
        setTextView(Boolean(cache.getText(sourceKey, value))); setExpandedText(false);
    };
    const extract = async (forceOcr = false) => {
        if (!asset || extraction.current || navigation.current.messageId !== message.messageId || (asset.kind === 'pdf' && !currentPage)) return;
        setError(''); setCopied(false);
        setTextView(true);
        if (!forceOcr && currentPage?.text) {
            const result = { text: currentPage.text, source: 'pdf', page };
            cache.putText(sourceKey, page, result);
            setExtracted(result); return;
        }
        // Recognition already on screen and asked for again: the operator wants a
        // new attempt, not the stored one, so the server cache is bypassed too.
        const refresh = extracted?.source === 'ocr' && extracted.page === page;
        const task = { controller: new AbortController(), render: null };
        extraction.current = task;
        setExtracting(true);
        try {
            const imageDataUrl = await rasterPage(asset, currentPage, task);
            if (!imageDataUrl || task.controller.signal.aborted) return;
            const { data } = await axios.post(`${apiBaseUrl}/api/wazzup/pilot/attachment-text`, {
                ...ids, page, imageDataUrl, ...(refresh ? { refresh: true } : {}),
            }, { headers: latest.current.headers(), signal: task.controller.signal, timeout: 65000 });
            if (task.controller.signal.aborted || extraction.current !== task) return;
            const result = { text: typeof data.text === 'string' ? data.text : '', source: 'ocr', page };
            cache.putText(sourceKey, page, result);
            setExtracted(result);
        } catch (failure) {
            const detail = await requestError(failure, 'Не удалось распознать текст. Попробуйте ещё раз.');
            if (!task.controller.signal.aborted) setError(detail);
        } finally {
            if (extraction.current === task) { extraction.current = null; setExtracting(false); }
        }
    };
    const copyText = async () => {
        if (!extracted?.text) return;
        const version = copyState.current.version;
        try {
            const ownerDocument = container.current?.ownerDocument || document;
            // Copy inside PiP during the actual click, avoiding a separate
            // clipboard permission prompt. Keep the async API as a fallback.
            let copiedSelection = false;
            if (pip.pipWindow && resultBox.current) {
                resultBox.current.focus(); resultBox.current.select();
                copiedSelection = ownerDocument.execCommand?.('copy') === true;
            }
            if (!copiedSelection) {
                const clipboard = ownerDocument.defaultView?.navigator?.clipboard || navigator.clipboard;
                await clipboard.writeText(extracted.text);
            }
            if (!copyState.current.mounted || version !== copyState.current.version) return;
            setCopied(true); clearTimeout(copyTimer.current);
            copyTimer.current = setTimeout(() => setCopied(false), 1800);
        } catch {
            if (!copyState.current.mounted || version !== copyState.current.version) return;
            resultBox.current?.focus(); resultBox.current?.select();
            setError('Текст выделен. Нажмите Ctrl+C для копирования.');
        }
    };
    const canExtract = Boolean(asset) && !loading && !extracting && (asset.kind !== 'pdf' || Boolean(currentPage));
    const hasText = Boolean(asset && (extracted || extracting));
    const textOnly = hasText && expandedText;
    return createPortal(<IosModal open embedded={Boolean(pip.pipWindow)} onClose={close} title={download?.name || attachmentName(message) || (isVideo ? 'Видео' : 'Просмотр вложения')}
        headerActions={pip.pipWindow ? <button type="button" className={actionButton} onClick={pip.returnToChat}
            aria-label="Вернуть в чат" title="Вернуть в чат"><Undo2 size={16} /><span className="hidden sm:inline">В чат</span></button>
            : pip.supported && <button type="button" className={iconButton} onClick={pip.open} disabled={pip.opening}
                aria-label="Картинка в картинке" title="Картинка в картинке — поверх других окон">
                {pip.opening ? <Loader2 size={16} className="animate-spin" /> : <PictureInPicture2 size={17} />}
            </button>}
        subtitle={asset?.kind === 'pdf' ? `${asset.pdf.numPages} стр. · Текст можно выделять на странице` : undefined}
        maxWidth="max-w-6xl" bodyClassName="thin-scroll flex min-h-0 flex-1 flex-col p-0">
        <div ref={container} tabIndex={-1} className="flex min-h-0 flex-1 flex-col outline-none" style={{ height: pip.pipWindow ? undefined : 'min(78vh, 900px)' }}>
            {pip.error && <div role="alert" className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800">{pip.error}</div>}
            <div className={`flex-wrap items-center gap-1.5 border-b border-slate-200 bg-white px-3 py-2 ${hasText && textView ? 'hidden md:flex' : 'flex'}`}>
                {asset?.kind === 'pdf' && <>
                    <button type="button" className={iconButton} disabled={page <= 1} aria-label="Предыдущая страница"
                        title="Предыдущая страница" onClick={() => changePage(page - 1)}><ChevronLeft size={17} /></button>
                    <span className="min-w-16 text-center text-xs tabular-nums text-slate-600">{page} / {asset.pdf.numPages}</span>
                    <button type="button" className={iconButton} disabled={page >= asset.pdf.numPages} aria-label="Следующая страница"
                        title="Следующая страница" onClick={() => changePage(page + 1)}><ChevronRight size={17} /></button>
                </>}
                {(asset || imageUrl) && <>
                    <button type="button" className={iconButton} disabled={zoom <= 0.5} aria-label="Уменьшить" title="Уменьшить"
                        onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}><ZoomOut size={16} /></button>
                    <span className="text-xs tabular-nums text-slate-500">{Math.round(zoom * 100)}%</span>
                    <button type="button" className={iconButton} disabled={zoom >= 3} aria-label="Увеличить" title="Увеличить"
                        onClick={() => setZoom((value) => Math.min(3, value + 0.25))}><ZoomIn size={16} /></button>
                </>}
                <div className="ml-auto flex flex-wrap items-center gap-1">
                    {!knownUnsupported && !isVideo && <button type="button" className={actionButton} disabled={!canExtract} onClick={() => extract(false)}>
                        {extracting ? <Loader2 size={15} className="animate-spin" /> : <FileText size={15} />} Извлечь текст
                    </button>}
                    {asset?.kind === 'pdf' && currentPage?.text && <button type="button" className={actionButton}
                        disabled={!canExtract} onClick={() => extract(true)} title="Распознать изображение текущей страницы с помощью ИИ">
                        <ScanText size={15} /> Распознать скан
                    </button>}
                    {knownUnsupported || isVideo ? <a href={message.contentUri} target="_blank" rel="noopener noreferrer" download={attachmentName(message)}
                        className={iconButton} aria-label="Скачать оригинал" title="Скачать оригинал"><Download size={16} /></a>
                        : <button type="button" className={iconButton} disabled={!download} aria-label="Скачать файл" title="Скачать файл"
                            onClick={() => saveAttachment(download.url, download.name, container.current?.ownerDocument)}><Download size={16} /></button>}
                </div>
            </div>
            {currentSource && error && <div role="alert" className="flex flex-wrap items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800">
                <span>{error}</span>
                {!asset && !loading && <button type="button" className="font-semibold underline" onClick={() => setRetry((value) => value + 1)}>Повторить</button>}
                {message.contentUri && <a href={message.contentUri} target="_blank" rel="noopener noreferrer" className="font-semibold underline">Открыть оригинал</a>}
            </div>}
            {hasText && <div className={`shrink-0 items-center gap-1 border-b border-slate-200 bg-white px-3 py-1.5 ${textOnly ? 'flex' : 'flex md:hidden'}`}
                role="group" aria-label="Режим просмотра">
                <button type="button" aria-pressed={!textView} onClick={() => { setTextView(false); setExpandedText(false); }}
                    className={`flex-1 rounded-lg px-3 py-1.5 text-xs font-medium ${!textView ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-50'}`}>Файл</button>
                <button type="button" aria-pressed={textView} onClick={() => setTextView(true)}
                    className={`flex-1 rounded-lg px-3 py-1.5 text-xs font-medium ${textView ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-50'}`}>Текст</button>
            </div>}
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">
                <div data-attachment-file className={`relative min-h-0 min-w-0 flex-1 bg-slate-200/70 ${textOnly ? 'hidden' : hasText && textView ? 'hidden md:block' : ''}`}>
                    {!knownUnsupported && !isVideo && !imageUrl && !instant && (!currentSource || loading) && <div role="status" className="flex h-full items-center justify-center gap-2 text-sm text-slate-500"><Loader2 size={18} className="animate-spin" /> Открываем вложение…</div>}
                    {isVideo && <div className="flex h-full items-center justify-center bg-slate-950 p-1 sm:p-3">
                        <video key={`${sourceKey}:${retry}`} ref={video} src={message.contentUri} controls playsInline autoPlay preload="metadata"
                            aria-label="Видео из сообщения" className="h-full max-h-full w-full object-contain"
                            onError={(event) => {
                                if (event.currentTarget === video.current && copyState.current.mounted && !request.current?.signal.aborted)
                                    pageError('Не удалось воспроизвести видео. Попробуйте ещё раз или откройте оригинал.');
                            }} />
                    </div>}
                    {knownUnsupported && <div className="flex h-full flex-col items-center justify-center gap-3 px-6 py-10 text-center">
                        <FileText size={42} className="text-slate-400" aria-hidden="true" />
                        <p className="max-w-full break-words text-sm font-medium text-slate-700">{attachmentName(message)}</p>
                        <p className="text-xs text-slate-500">Предпросмотр этого формата пока недоступен.</p>
                        <a href={message.contentUri} target="_blank" rel="noopener noreferrer" className="text-xs font-medium text-blue-600 hover:underline">Открыть оригинал</a>
                    </div>}
                    {asset?.kind === 'pdf' && <PdfPage key={sourceKey} document={asset.pdf} library={asset.library} number={page} zoom={zoom}
                        onReady={ready} onError={pageError} hostWindow={pip.pipWindow || homeDocument.current.defaultView} />}
                    {imageUrl && <div className="wazzup-scrollbar flex h-full overflow-auto p-3">
                        <img key={imageUrl} src={imageUrl} alt="Вложение из сообщения" className="m-auto shrink-0 object-contain"
                            style={{ width: `${zoom * 100}%`, maxWidth: 'none', maxHeight: zoom === 1 ? '100%' : undefined }}
                            onError={() => {
                                if (directImage) setFailedPreview(sourceKey);
                                else pageError('Не удалось отобразить изображение. Скачайте файл.');
                            }} />
                    </div>}
                </div>
                {hasText && <div data-attachment-text className={`min-h-0 min-w-0 flex-1 flex-col bg-white ${textView ? 'flex' : 'hidden md:flex'} ${textOnly ? '' : 'md:w-80 md:flex-none md:border-l md:border-slate-200'}`}>
                    <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-100 px-3 py-2">
                        <span className="text-xs font-semibold text-slate-600">{asset?.kind === 'pdf' ? `Текст страницы ${page}` : 'Текст изображения'}</span>
                        <div className="flex items-center gap-1">
                        <button type="button" className={`${iconButton} hidden md:inline-flex`} onClick={() => { setExpandedText(!expandedText); setTextView(true); }}
                            aria-label={textOnly ? 'Показать файл рядом с текстом' : 'Развернуть текст'} title={textOnly ? 'Показать файл рядом с текстом' : 'Развернуть текст'}>
                            {textOnly ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>
                        <button type="button" className={iconButton} disabled={!extracted?.text || extracting} onClick={copyText}
                            aria-label="Копировать текст" title={copied ? 'Скопировано' : 'Копировать текст'}>{copied ? <Check size={16} /> : <Copy size={16} />}</button>
                        </div>
                    </div>
                    {extracting ? <div role="status" className="flex items-center justify-center gap-2 px-3 py-10 text-sm text-slate-500"><Loader2 size={16} className="animate-spin" /> Распознаём текущую страницу…</div>
                        : <textarea ref={resultBox} readOnly value={extracted?.text || ''} aria-label="Извлечённый текст"
                            placeholder="На этой странице текст не найден" className="wazzup-scrollbar min-h-0 flex-1 resize-none border-0 p-3 text-sm leading-relaxed text-slate-700 outline-none" />}
                    {extracted?.source === 'ocr' && !extracting && <p className="shrink-0 border-t border-slate-100 px-3 py-2 text-[11px] text-slate-400">Распознано ИИ. Проверьте текст.</p>}
                </div>}
            </div>
            {onSelect && <ChatAttachmentStrip items={items} selectedId={message.messageId} onSelect={select}
                compact={hasText && textView}
                onPrevious={() => step(-1)} onNext={() => step(1)} />}
        </div>
    </IosModal>, portalHost);
}
