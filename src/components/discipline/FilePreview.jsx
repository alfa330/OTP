import React, { useEffect, useRef, useState } from 'react';
import { Download, Minus, Plus, X } from 'lucide-react';
import { IosLightbox } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import { apiError, saveBlob } from './disciplineApi';
import { isPreviewable } from './disciplineRules';
import { FileName, Spinner } from './disciplineUi';

/*
 * Предпросмотр файла без скачивания (ТЗ п. 13): картинки — в общем окне
 * просмотра портала (IosLightbox), PDF — во встроенном просмотрщике браузера,
 * а на телефоне — страницами через pdf.js (тот же, что в чатах): Safari на
 * iPhone во встроенном окне показывает только первую страницу, и акт на трёх
 * листах там не прочитать. Файл приходит с авторизацией через ручку раздела, а
 * не по ссылке хранилища: так каждое открытие попадает в журнал действий
 * (п. 14). Остальные виды (DOC, HEIC, видео) браузер показать не может — они
 * сразу скачиваются.
 *
 * Выход есть всегда — и пока файл грузится: крестик, Esc и жест «назад» на
 * телефоне; загрузка при этом обрывается (на зависшей мобильной сети экран
 * иначе стоял бы до таймаута). Страницы PDF на телефоне увеличиваются кнопками
 * «−» и «+»: щипок в портале выключен, а рукописную объяснительную или скан
 * акта надо читать.
 */

const ZOOMS = [1, 1.5, 2, 3];

const MAX_PAGES = 40;

const PdfPages = ({ blob, zoom = 1, onFailed }) => {
    const hostRef = useRef(null);
    const [ready, setReady] = useState(false);
    useEffect(() => {
        let cancelled = false;
        let task = null;
        const host = hostRef.current;
        setReady(false);
        host?.replaceChildren();
        (async () => {
            const library = await import('../wazzup/pdfRuntime');
            const data = new Uint8Array(await blob.arrayBuffer());
            if (cancelled) return;
            task = library.getDocument({ data, isEvalSupported: false, enableXfa: false, verbosity: 0 });
            const pdf = await task.promise;
            const width = Math.max(240, (host?.parentElement?.clientWidth || 360) - 8) * zoom;
            const ratio = Math.min(window.devicePixelRatio || 1, 2);
            for (let number = 1; number <= Math.min(pdf.numPages, MAX_PAGES); number += 1) {
                if (cancelled) return;
                const page = await pdf.getPage(number);
                const viewport = page.getViewport({ scale: (width / page.getViewport({ scale: 1 }).width) * ratio });
                const canvas = host.ownerDocument.createElement('canvas');
                canvas.width = Math.floor(viewport.width);
                canvas.height = Math.floor(viewport.height);
                canvas.style.width = `${Math.floor(viewport.width / ratio)}px`;
                canvas.className = 'mx-auto mb-2 block rounded-lg bg-white shadow';
                host.appendChild(canvas);
                await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
                if (number === 1 && !cancelled) setReady(true);
            }
        })().catch(() => { if (!cancelled) onFailed?.(); });
        return () => {
            cancelled = true;
            task?.destroy?.()?.catch?.(() => {});
            host?.replaceChildren();
        };
    }, [blob, zoom]); // eslint-disable-line react-hooks/exhaustive-deps
    return (
        <div className="relative h-full overflow-auto overscroll-contain px-1 pb-4">
            {!ready && <div className="absolute inset-0 grid place-items-center"><Spinner size={28} className="!text-white" /></div>}
            <div ref={hostRef} className={zoom > 1 ? 'w-max' : ''} />
        </div>
    );
};

const CloseButton = ({ onClose }) => (
    <button type="button" onClick={onClose} aria-label="Закрыть"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-white/10 text-white hover:bg-white/20">
        <X size={18} />
    </button>
);

const FilePreview = ({ file, api, toast, onClose }) => {
    const mobile = useIsMobileShell();
    const [state, setState] = useState({ url: null, blob: null });
    const [pdfFailed, setPdfFailed] = useState(false);
    const [zoom, setZoom] = useState(1);
    const isImage = /^image\//.test(file?.content_type || '');
    // Жест «назад» закрывает просмотр, а не окно записи под ним. У готовой
    // картинки запись в истории кладёт само окно просмотра (IosLightbox).
    useScreenBackGesture(mobile && Boolean(file) && !(isImage && state.url), onClose);

    useEffect(() => {
        if (!file) { setState({ url: null, blob: null }); return undefined; }
        const controller = new AbortController();
        let objectUrl = null;
        const inline = isPreviewable(file);
        setState({ url: null, blob: null });
        setPdfFailed(false);
        setZoom(1);
        api.file(file.id, inline, controller.signal)
            .then((blob) => {
                if (controller.signal.aborted) return;
                if (!inline) {
                    saveBlob(blob, file.name);
                    onClose();
                    return;
                }
                objectUrl = URL.createObjectURL(blob);
                setState({ url: objectUrl, blob });
            })
            .catch((err) => {
                if (controller.signal.aborted) return;
                toast(apiError(err, 'Файл не открылся'), 'error');
                onClose();
            });
        return () => {
            controller.abort();
            if (objectUrl) setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
        };
    }, [file?.id]); // eslint-disable-line react-hooks/exhaustive-deps

    // Esc закрывает просмотр — и пока файл ещё грузится.
    useEffect(() => {
        if (!file) return undefined;
        const onKey = (event) => { if (event.key === 'Escape') onClose(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [file, onClose]);

    if (!file) return null;
    const download = () => state.blob && saveBlob(state.blob, file.name);

    if (isImage) {
        if (!state.url) {
            return (
                <div className="fixed inset-0 z-[300] flex flex-col bg-slate-900/70 backdrop-blur-sm" role="dialog" aria-label={file.name}>
                    <div className="flex justify-end px-4 py-3" style={{ paddingTop: 'max(12px, env(safe-area-inset-top))' }}>
                        <CloseButton onClose={onClose} />
                    </div>
                    <div className="grid flex-1 place-items-center"><Spinner size={28} className="!text-white" /></div>
                </div>
            );
        }
        return <IosLightbox url={state.url} alt={file.name} onClose={onClose} onDownload={download} />;
    }

    return (
        <div className="fixed inset-0 z-[300] flex flex-col bg-slate-900/85 backdrop-blur-sm" role="dialog" aria-label={file.name}>
            <div className="flex items-center gap-3 px-4 py-3 text-white" style={{ paddingTop: 'max(12px, env(safe-area-inset-top))' }}>
                <FileName name={file.name} className="flex-1 text-[14px] font-medium" />
                {mobile && !pdfFailed && state.url ? (
                    <span className="flex items-center gap-1">
                        <button type="button" aria-label="Мельче" disabled={zoom <= ZOOMS[0]}
                                onClick={() => setZoom((z) => ZOOMS[Math.max(0, ZOOMS.indexOf(z) - 1)])}
                                className="grid h-9 w-9 place-items-center rounded-full bg-white/10 hover:bg-white/20 disabled:opacity-40">
                            <Minus size={16} />
                        </button>
                        <span className="w-11 text-center text-[12.5px] tabular-nums">{Math.round(zoom * 100)}%</span>
                        <button type="button" aria-label="Крупнее" disabled={zoom >= ZOOMS[ZOOMS.length - 1]}
                                onClick={() => setZoom((z) => ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(z) + 1)])}
                                className="grid h-9 w-9 place-items-center rounded-full bg-white/10 hover:bg-white/20 disabled:opacity-40">
                            <Plus size={16} />
                        </button>
                    </span>
                ) : null}
                <button type="button" onClick={download} disabled={!state.blob} aria-label="Скачать"
                        className="inline-flex items-center gap-1.5 rounded-xl bg-white/10 px-3 py-2 text-[13px] font-medium hover:bg-white/20 disabled:opacity-40">
                    <Download size={15} />
                    {/* На телефоне рядом масштаб — подпись уступает место значку. */}
                    {mobile ? null : 'Скачать'}
                </button>
                <CloseButton onClose={onClose} />
            </div>
            <div className="min-h-0 flex-1 px-2 pb-2 sm:px-6 sm:pb-6">
                {!state.url ? (
                    <div className="grid h-full place-items-center"><Spinner size={28} className="!text-white" /></div>
                ) : mobile && !pdfFailed ? (
                    <PdfPages blob={state.blob} zoom={zoom} onFailed={() => setPdfFailed(true)} />
                ) : (
                    <iframe title={file.name} src={state.url} className="h-full w-full rounded-xl bg-white" />
                )}
            </div>
        </div>
    );
};

export default FilePreview;
