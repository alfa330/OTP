import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Camera, Loader2, RotateCw, SwitchCamera, X } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import { FRAME, TOO_DARK_LUMA, frameRegion, meanLuma } from './dressCode';

/*
 * Камера «Дресс-кода» (ТЗ #418, п. 2, 3, 10).
 *
 * Видео — во весь экран, поверх него рамка почти на весь экран: верх — у макушки,
 * низ — у пояса, внутри силуэт «голова и плечи». НА ФОТО ПОПАДАЕТ РОВНО ТО, ЧТО В
 * РАМКЕ: всё вне её затемнено, это подписано, а после снимка в рамке стоит само
 * фото (владелец 13.11.2026: «очень большие поля снизу и сверху, не совсем понятно,
 * снимается ли всё»). Чёрных полос нет: кнопки лежат поверх видео.
 *
 * Загрузки из галереи нет: фото — только с камеры (п. 2). Если камера портала не
 * включилась (запрет, нет камеры, iPhone с иконки домашнего экрана, где запуск
 * камеры иногда висит), на телефоне остаётся «Снять камерой телефона» —
 * системная камера через capture="user": она снимает сразу, без выбора из
 * галереи. На компьютере такого выхода нет — там capture открыл бы выбор файла.
 *
 * Качество кадра проверяет ИИ на сервере (голова не влезла, одежду не видно,
 * размыто) и возвращает короткую подсказку — она приходит сюда `hint`. Сразу
 * здесь отбивается только явная темнота: её видно без ИИ.
 *
 * Окно — порталом в body: корень IosModal несёт backdrop-filter, и fixed внутри
 * него отсчитывался бы от карточки (см. IosLightbox).
 */

const MAX_SIDE = 1280;
const START_HANG_MS = 8000;

const cameraError = (error) => {
    const name = error?.name;
    if (name === 'NotAllowedError' || name === 'SecurityError') {
        return 'Доступ к камере запрещён — разрешите его в настройках браузера';
    }
    if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'Камера не найдена';
    if (name === 'NotReadableError') return 'Камера занята другим приложением';
    return 'Не удалось включить камеру';
};

/* Силуэт «голова и плечи» до пояса — ориентир, как встать: макушка у верха рамки,
   плечи почти во всю ширину, низ рамки — пояс. */
const Silhouette = () => (
    <svg viewBox="0 0 110 200" className="absolute inset-0 h-full w-full" preserveAspectRatio="xMidYMin meet"
         fill="none" stroke="rgba(255,255,255,0.6)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
         aria-hidden="true">
        <ellipse cx="55" cy="37" rx="20" ry="26" vectorEffect="non-scaling-stroke" />
        <path vectorEffect="non-scaling-stroke"
              d="M47 62 L47 70 Q47 77 39 80 L20 87 Q7 92 6 107 L4 200 M106 200 L104 107 Q103 92 90 87 L71 80 Q63 77 63 70 L63 62" />
    </svg>
);

export default function DressCodeCamera({ open, onClose, onCapture, busy = false, hint = '' }) {
    const isMobileShell = useIsMobileShell();
    const videoRef = useRef(null);
    const frameRef = useRef(null);
    const streamRef = useRef(null);
    const fileRef = useRef(null);
    const [facing, setFacing] = useState('user');
    const [cameras, setCameras] = useState(0);
    const [starting, setStarting] = useState(false);
    const [error, setError] = useState('');
    const [hang, setHang] = useState(false);
    const [shot, setShot] = useState(null);           // { blob, url }
    const [localHint, setLocalHint] = useState('');

    useScreenBackGesture(isMobileShell && open, onClose);

    const stop = useCallback(() => {
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        if (videoRef.current) videoRef.current.srcObject = null;
    }, []);

    const start = useCallback(async (mode) => {
        stop();
        setError('');
        setHang(false);
        if (!navigator.mediaDevices?.getUserMedia) {
            setError('Камера в этом браузере недоступна');
            return;
        }
        setStarting(true);
        const hangTimer = setTimeout(() => setHang(true), START_HANG_MS);
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: mode }, width: { ideal: 1920 }, height: { ideal: 1080 } },
                audio: false,
            });
            streamRef.current = stream;
            if (videoRef.current) {
                videoRef.current.srcObject = stream;
                await videoRef.current.play().catch(() => {});
            }
            const devices = await navigator.mediaDevices.enumerateDevices?.().catch(() => []) || [];
            setCameras(devices.filter((device) => device.kind === 'videoinput').length);
        } catch (err) {
            setError(cameraError(err));
        } finally {
            clearTimeout(hangTimer);
            setHang(false);
            setStarting(false);
        }
    }, [stop]);

    useEffect(() => {
        if (!open) {
            stop();
            setShot((prev) => { if (prev) URL.revokeObjectURL(prev.url); return null; });
            setLocalHint('');
            return undefined;
        }
        start(facing);
        return stop;
    }, [open, facing, start, stop]);

    // Сервер попросил переснять — возвращаемся к живому кадру.
    useEffect(() => {
        if (hint) setShot((prev) => { if (prev) URL.revokeObjectURL(prev.url); return null; });
    }, [hint]);

    const mirrored = facing === 'user';

    const capture = () => {
        const video = videoRef.current;
        const frame = frameRef.current;
        if (!video || !frame || !video.videoWidth) return;
        // На фото — ровно то, что в рамке.
        const [sx, sy, sw, sh] = frameRegion(
            video.videoWidth, video.videoHeight, video.getBoundingClientRect(), frame.getBoundingClientRect(), mirrored);
        const scale = Math.min(1, MAX_SIDE / Math.max(sw, sh));
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(sw * scale);
        canvas.height = Math.round(sh * scale);
        canvas.getContext('2d').drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
        const probe = document.createElement('canvas');
        probe.width = 32;
        probe.height = 32;
        const probeCtx = probe.getContext('2d', { willReadFrequently: true });
        probeCtx.drawImage(canvas, 0, 0, 32, 32);
        if (meanLuma(probeCtx.getImageData(0, 0, 32, 32).data) < TOO_DARK_LUMA) {
            setLocalHint('Темно — встаньте к свету');
            return;
        }
        setLocalHint('');
        canvas.toBlob((blob) => {
            if (!blob) return;
            setShot({ blob, url: URL.createObjectURL(blob) });
        }, 'image/jpeg', 0.88);
    };

    const retake = () => {
        setShot((prev) => { if (prev) URL.revokeObjectURL(prev.url); return null; });
    };

    const send = () => {
        if (shot && !busy) onCapture(shot.blob, 'camera');
    };

    const onSystemFile = (event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (file && !busy) onCapture(file, 'system');
    };

    if (!open || typeof document === 'undefined') return null;
    const shownHint = localHint || hint;
    const safeTop = 'env(safe-area-inset-top, 0px)';
    const safeBottom = 'env(safe-area-inset-bottom, 0px)';
    const frameStyle = {
        top: `calc(${safeTop} + ${FRAME.top}px)`,
        bottom: `calc(${safeBottom} + ${FRAME.bottom}px)`,
        left: '50%',
        transform: 'translateX(-50%)',
        width: `min(${FRAME.sideShare * 100}vw, ${FRAME.maxWidth}px, calc((100dvh - ${FRAME.top + FRAME.bottom}px - ${safeTop} - ${safeBottom}) * ${FRAME.maxAspect}))`,
        // Всё вне рамки на фото не попадёт: при съёмке оно притемнено, у готового снимка — скрыто.
        boxShadow: shot ? '0 0 0 9999px #000' : '0 0 0 9999px rgba(2,6,23,0.6)',
    };
    const roundBtn = 'grid h-11 w-11 place-items-center rounded-full bg-black/45 backdrop-blur-md transition hover:bg-black/60 active:scale-95 disabled:opacity-50';

    return createPortal(
        <div className="fixed inset-0 z-[300] overflow-hidden bg-black text-white" style={{ fontFamily: APPLE_FONT }}
             role="dialog" aria-modal="true" aria-label="Фото для дресс-кода">
            {/* Видео не снимается с экрана, пока виден снимок, а только прячется: новый
                <video> пришёл бы без потока камеры, и «Переснять» показало бы чёрный кадр. */}
            <video ref={videoRef} playsInline muted autoPlay
                   className={`absolute inset-0 h-full w-full object-cover ${mirrored ? '-scale-x-100' : ''} ${shot ? 'invisible' : ''}`} />

            <div ref={frameRef} className="pointer-events-none absolute overflow-hidden rounded-[26px] ring-2 ring-white"
                 style={frameStyle}>
                {shot ? (
                    <img src={shot.url} alt="Снимок" className={`h-full w-full object-cover ${mirrored ? '-scale-x-100' : ''}`} />
                ) : (
                    <>
                        {!starting && !error && <Silhouette />}
                        {!starting && !error && (
                            <div className="absolute inset-x-0 bottom-3 flex justify-center">
                                <span className="rounded-full bg-black/55 px-3 py-1 text-[12.5px] font-medium text-white backdrop-blur-md">
                                    На фото попадёт то, что в рамке
                                </span>
                            </div>
                        )}
                    </>
                )}
                {(starting || error) && !shot && (
                    <div className="absolute inset-0 grid place-items-center px-8 text-center">
                        {starting && !error && <Loader2 size={26} className="animate-spin text-white/80" />}
                        {error && (
                            <div className="space-y-2">
                                <div className="text-[15px] font-semibold">{error}</div>
                                {!isMobileShell && (
                                    <div className="text-[13px] text-white/70">
                                        Фото снимается только камерой: загрузить готовое нельзя.
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                )}
                {busy && (
                    <div className="absolute inset-0 grid place-items-center bg-black/50">
                        <div className="flex items-center gap-2 rounded-full bg-black/60 px-4 py-2 text-[14px] font-medium">
                            <Loader2 size={16} className="animate-spin" /> Проверяем фото…
                        </div>
                    </div>
                )}
            </div>

            {/* Верх: закрыть и подсказка одной строкой — что сделать. */}
            <div className="absolute inset-x-0 top-0 flex items-center justify-center px-[60px]"
                 style={{ paddingTop: `calc(${safeTop} + 12px)`, minHeight: `calc(${safeTop} + ${FRAME.top}px)` }}>
                <button type="button" onClick={onClose} disabled={busy} aria-label="Закрыть камеру"
                        className={`${roundBtn} absolute left-3`} style={{ top: `calc(${safeTop} + 10px)` }}>
                    <X size={19} />
                </button>
                <div className={`rounded-full px-3.5 py-1.5 text-center text-[13.5px] font-semibold leading-tight backdrop-blur-md ${
                    shownHint ? 'bg-amber-400 text-slate-900' : 'bg-black/45 text-white'}`} role="status">
                    {shownHint || (shot ? 'Проверьте фото' : 'В рамке — от головы до пояса')}
                </div>
            </div>

            {/* Низ: кнопки поверх видео. */}
            <div className="absolute inset-x-0 bottom-0 flex flex-col items-center gap-3 px-6"
                 style={{ paddingBottom: `calc(${safeBottom} + 20px)` }}>
                {isMobileShell && !shot && (error || hang) && (
                    <>
                        <button type="button" onClick={() => fileRef.current?.click()} disabled={busy}
                                className="inline-flex items-center gap-2 rounded-full bg-black/55 px-4 py-2 text-[13px] font-medium ring-1 ring-white/25 backdrop-blur-md active:scale-[0.97]">
                            <Camera size={15} /> Снять камерой телефона
                        </button>
                        <input ref={fileRef} type="file" accept="image/*" capture="user" className="hidden" onChange={onSystemFile} />
                    </>
                )}
                <div className="flex items-center justify-center gap-6">
                    {shot ? (
                        <>
                            <button type="button" onClick={retake} disabled={busy}
                                    className="inline-flex h-12 items-center gap-2 rounded-full bg-white/15 px-5 text-[14.5px] font-semibold ring-1 ring-white/30 backdrop-blur-md transition hover:bg-white/25 active:scale-[0.97] disabled:opacity-50">
                                <RotateCw size={16} /> Переснять
                            </button>
                            <button type="button" onClick={send} disabled={busy}
                                    className="inline-flex h-12 items-center gap-2 rounded-full bg-white px-7 text-[14.5px] font-semibold text-slate-900 transition hover:bg-white/90 active:scale-[0.97] disabled:opacity-50">
                                {busy && <Loader2 size={16} className="animate-spin" />} Отправить
                            </button>
                        </>
                    ) : (
                        <>
                            <span className="w-11" />
                            <button type="button" onClick={capture} disabled={busy || starting || Boolean(error)}
                                    aria-label="Сделать снимок"
                                    className="grid h-[72px] w-[72px] place-items-center rounded-full bg-black/25 ring-4 ring-white backdrop-blur-sm transition active:scale-95 disabled:opacity-40">
                                <span className="h-[56px] w-[56px] rounded-full bg-white" />
                            </button>
                            {cameras > 1 ? (
                                <button type="button" onClick={() => setFacing((mode) => (mode === 'user' ? 'environment' : 'user'))}
                                        disabled={busy} aria-label="Другая камера" className={roundBtn}>
                                    <SwitchCamera size={19} />
                                </button>
                            ) : <span className="w-11" />}
                        </>
                    )}
                </div>
            </div>
        </div>,
        document.body,
    );
}
