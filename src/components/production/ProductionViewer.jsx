import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Download, FileText, X } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import { formatBytes, formatDuration } from './productionMeta';

/*
 * Просмотр материала «Продакшна»: ролик — плеером, картинка — целиком, остальное —
 * карточкой файла с «Скачать». Вид тот же, что у просмотра фото в «Задачах»
 * (IosLightbox): затемнение, круглые кнопки на стекле, листание стрелками.
 * Отдельный компонент, а не IosLightbox: тот рисует только <img>, а его разметку
 * сторожат тесты фото задач и посылок.
 *
 * Ролик берётся прямо из хранилища по подписанной ссылке; перемотка — обычные
 * запросы Range по той же ссылке, сервер портала в них не участвует.
 */

const roundBtn = 'grid h-9 w-9 place-items-center rounded-full bg-white/15 text-white backdrop-blur-md transition '
    + 'hover:bg-white/25 active:scale-95 disabled:pointer-events-none disabled:opacity-35';

const Glyph = ({ d }) => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d={d} />
    </svg>
);

const ProductionViewer = ({ items = [], index = -1, onIndex, onClose, onDownload, onExpired }) => {
    const isNarrow = useIsMobileShell();
    const rootRef = useRef(null);
    const item = index >= 0 ? items[index] : null;
    const open = Boolean(item);
    const [failed, setFailed] = useState(false);
    // Ссылка на файл подписана на 4 часа: если карточку держали открытой дольше,
    // ошибка — это истёкшая подпись, а не «браузер не умеет». Один раз просим свежие
    // ссылки и только после второй ошибки говорим «скачайте».
    const retriedRef = useRef(null);
    useScreenBackGesture(isNarrow && open, onClose);

    useEffect(() => { setFailed(false); }, [item?.id, item?.view_url]);

    const mediaError = () => {
        if (onExpired && retriedRef.current !== item?.id) {
            retriedRef.current = item?.id;
            onExpired();
            return;
        }
        setFailed(true);
    };

    useEffect(() => {
        if (!open) return undefined;
        const opener = document.activeElement;
        rootRef.current?.focus({ preventScroll: true });
        return () => {
            if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus({ preventScroll: true });
        };
    }, [open]);

    useEffect(() => {
        if (!open) return undefined;
        const onKey = (event) => {
            // Внутри плеера стрелки перематывают — листаем, только когда фокус не на нём.
            const inVideo = event.target?.tagName === 'VIDEO';
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopImmediatePropagation();
                onClose?.();
            } else if (!inVideo && event.key === 'ArrowLeft' && index > 0) {
                event.preventDefault();
                onIndex?.(index - 1);
            } else if (!inVideo && event.key === 'ArrowRight' && index < items.length - 1) {
                event.preventDefault();
                onIndex?.(index + 1);
            }
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [open, index, items.length, onClose, onIndex]);

    if (!item || typeof document === 'undefined') return null;
    const stop = (event) => event.stopPropagation();
    const showVideo = item.kind === 'video' && item.view_url && !failed;
    const showImage = item.kind === 'image' && item.view_url && !failed;
    const counter = items.length > 1 ? `${index + 1} из ${items.length}` : '';
    const meta = [formatBytes(item.size), item.kind === 'video' ? formatDuration(item.duration_seconds) : '']
        .filter(Boolean).join(' · ');

    return createPortal(
        <div
            ref={rootRef}
            className="fixed inset-0 z-[300] flex flex-col bg-slate-950/90 outline-none"
            style={{ fontFamily: APPLE_FONT }}
            onClick={onClose}
            role="dialog"
            aria-modal="true"
            aria-label={item.name}
            tabIndex={-1}
        >
            <div className="flex items-center gap-3 px-4 pb-2 pt-[max(14px,env(safe-area-inset-top))]" onClick={stop}>
                <div className="min-w-0 flex-1">
                    <p className="truncate text-[14px] font-semibold text-white">{item.name}</p>
                    {meta && <p className="truncate text-[12px] tabular-nums text-white/60">{meta}</p>}
                </div>
                {onDownload && (
                    <button
                        type="button"
                        onClick={() => onDownload(item)}
                        className="inline-flex h-9 items-center gap-1.5 rounded-full bg-white/15 px-3.5 text-[13px] font-medium text-white backdrop-blur-md transition hover:bg-white/25 active:scale-[.97]"
                    >
                        <Download size={16} /> Скачать
                    </button>
                )}
                <button type="button" className={roundBtn} onClick={onClose} aria-label="Закрыть">
                    <X size={18} />
                </button>
            </div>

            <div className="flex min-h-0 flex-1 items-center justify-center px-3 pb-3">
                {showVideo && (
                    // eslint-disable-next-line jsx-a11y/media-has-caption
                    <video
                        key={item.id}
                        src={item.view_url}
                        poster={item.poster_url || undefined}
                        controls
                        autoPlay
                        playsInline
                        preload="metadata"
                        onClick={stop}
                        // Кодек видео не поддержан (HEVC без аппаратного декодера на Windows):
                        // браузер часто не даёт ошибку, а играет один звук на чёрном.
                        onLoadedMetadata={(event) => { if (!event.currentTarget.videoWidth) setFailed(true); }}
                        onError={mediaError}
                        className="max-h-full max-w-full rounded-2xl bg-black shadow-2xl"
                    />
                )}
                {showImage && (
                    <img
                        key={item.id}
                        src={item.view_url}
                        alt={item.name}
                        onClick={stop}
                        onError={mediaError}
                        className="max-h-full max-w-full rounded-2xl object-contain shadow-2xl"
                    />
                )}
                {!showVideo && !showImage && (
                    <div onClick={stop} className="flex w-full max-w-sm flex-col items-center gap-3 rounded-3xl bg-white/10 px-6 py-8 text-center backdrop-blur-md">
                        <span className="grid h-14 w-14 place-items-center rounded-2xl bg-white/15 text-white">
                            <FileText size={26} />
                        </span>
                        <p className="max-w-full break-words text-[15px] font-semibold text-white">{item.name}</p>
                        <p className="text-[13px] leading-snug text-white/70">
                            {failed || item.kind === 'video'
                                ? 'Этот файл браузер показать не может — скачайте его, он откроется в программе на компьютере.'
                                : 'Предпросмотра у этого файла нет — его можно скачать.'}
                        </p>
                        {onDownload && (
                            <button
                                type="button"
                                onClick={() => onDownload(item)}
                                className="mt-1 inline-flex h-10 items-center gap-2 rounded-full bg-white px-5 text-[14px] font-semibold text-slate-900 transition hover:bg-white/90 active:scale-[.97]"
                            >
                                <Download size={16} /> Скачать
                            </button>
                        )}
                    </div>
                )}
            </div>

            {items.length > 1 && (
                <div
                    className="mx-auto mb-[max(16px,env(safe-area-inset-bottom))] flex items-center gap-1 rounded-full bg-white/15 p-1 text-white backdrop-blur-md"
                    onClick={stop}
                    role="group"
                    aria-label="Листать материалы"
                >
                    <button type="button" className="grid h-8 w-8 place-items-center rounded-full transition hover:bg-white/15 active:scale-95 disabled:opacity-35"
                        disabled={index <= 0} aria-label="Предыдущий файл" onClick={() => onIndex?.(index - 1)}>
                        <Glyph d="M15 18l-6-6 6-6" />
                    </button>
                    <span className="min-w-[64px] px-1 text-center text-[13px] font-medium tabular-nums text-white/85">{counter}</span>
                    <button type="button" className="grid h-8 w-8 place-items-center rounded-full transition hover:bg-white/15 active:scale-95 disabled:opacity-35"
                        disabled={index >= items.length - 1} aria-label="Следующий файл" onClick={() => onIndex?.(index + 1)}>
                        <Glyph d="M9 18l6-6-6-6" />
                    </button>
                </div>
            )}
        </div>,
        document.body,
    );
};

export default ProductionViewer;
