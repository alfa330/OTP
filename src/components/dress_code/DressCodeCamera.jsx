import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Camera, Loader2, RotateCw, SwitchCamera, X } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import { FRAME, TOO_DARK_LUMA, meanLuma } from './dressCode';

/*
 * Камера «Дресс-кода» (ТЗ #418, п. 2, 3, 10).
 *
 * Живой кадр с рамкой: верх рамки — у макушки, низ — чуть выше пояса, по центру
 * экрана; силуэт внутри — ориентир. Снимается ровно то, что человек видит на
 * экране (кадр камеры обрезается так же, как его обрезает object-cover), поэтому
 * рамка на экране и границы фото совпадают.
 *
 * Загрузки из галереи нет: фото — только с камеры (п. 2). Если камера портала не
 * включилась (запрет, нет камеры, iPhone с иконки домашнего экрана, где запуск
 * камеры иногда висит), на телефоне остаётся «Снять камерой телефона» —
 * системная камера через capture="user": она снимает сразу, без выбора из
 * галереи. На компьютере такого выхода нет — там capture открыл бы выбор файла.
 *
 * Качество кадра проверяет ИИ на сервере (обрезанная голова, не видно одежду,
 * размыто) и возвращает конкретную подсказку — она приходит сюда `hint`. Сразу
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

/* Какая часть кадра видна на экране при object-cover: (sx, sy, sw, sh) в пикселях кадра. */
const visibleRegion = (video, box) => {
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    if (!vw || !vh || !box.width || !box.height) return [0, 0, vw, vh];
    const scale = Math.max(box.width / vw, box.height / vh);
    const sw = Math.min(vw, box.width / scale);
    const sh = Math.min(vh, box.height / scale);
    return [(vw - sw) / 2, (vh - sh) / 2, sw, sh];
};

const Silhouette = () => (
    <svg viewBox="0 0 100 130" className="absolute inset-0 h-full w-full" preserveAspectRatio="xMidYMin meet"
         aria-hidden="true">
        <ellipse cx="50" cy="30" rx="15" ry="19" fill="none" stroke="rgba(255,255,255,0.55)"
                 strokeWidth="0.9" strokeDasharray="3 2.5" />
        <path d="M14 128 C14 92 24 70 50 66 C76 70 86 92 86 128" fill="none" stroke="rgba(255,255,255,0.55)"
              strokeWidth="0.9" strokeDasharray="3 2.5" />
    </svg>
);

export default function DressCodeCamera({ open, onClose, onCapture, busy = false, hint = '', attempt = 1, attempts = 2 }) {
    const isMobileShell = useIsMobileShell();
    const videoRef = useRef(null);
    const boxRef = useRef(null);
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
                video: { facingMode: { ideal: mode }, width: { ideal: 1280 }, height: { ideal: 1280 } },
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
            return undefined;
        }
        start(facing);
        return stop;
    }, [open, facing, start, stop]);

    // Сервер попросил переснять — возвращаемся к живому кадру.
    useEffect(() => {
        if (hint) setShot((prev) => { if (prev) URL.revokeObjectURL(prev.url); return null; });
    }, [hint]);

    const capture = () => {
        const video = videoRef.current;
        const box = boxRef.current?.getBoundingClientRect();
        if (!video || !box || !video.videoWidth) return;
        const [sx, sy, sw, sh] = visibleRegion(video, box);
        const scale = Math.min(1, MAX_SIDE / Math.max(sw, sh));
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(sw * scale);
        canvas.height = Math.round(sh * scale);
        const ctx = canvas.getContext('2d');
        ctx.drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
        const probe = document.createElement('canvas');
        probe.width = 32;
        probe.height = 32;
        const probeCtx = probe.getContext('2d', { willReadFrequently: true });
        probeCtx.drawImage(canvas, 0, 0, 32, 32);
        if (meanLuma(probeCtx.getImageData(0, 0, 32, 32).data) < TOO_DARK_LUMA) {
            setLocalHint('Слишком темно — встаньте к свету.');
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
    const mirrored = facing === 'user';
    const shownHint = localHint || hint;
    const frameStyle = {
        top: `${FRAME.top * 100}%`,
        bottom: `${(1 - FRAME.bottom) * 100}%`,
        left: `${((1 - FRAME.width) / 2) * 100}%`,
        right: `${((1 - FRAME.width) / 2) * 100}%`,
    };

    return createPortal(
        <div className="fixed inset-0 z-[300] flex flex-col bg-black text-white" style={{ fontFamily: APPLE_FONT }}
             role="dialog" aria-modal="true" aria-label="Фото для дресс-кода">
            <div className="flex items-center justify-between px-4 pb-2 pt-[max(14px,env(safe-area-inset-top))]">
                <button type="button" onClick={onClose} disabled={busy}
                        className="grid h-9 w-9 place-items-center rounded-full bg-white/10 transition hover:bg-white/20 active:scale-95"
                        aria-label="Закрыть камеру">
                    <X size={18} />
                </button>
                <div className="text-[13px] font-medium text-white/80">
                    {attempts > 1 && attempt > 1 ? `Попытка ${attempt} из ${attempts}` : 'Фото для дресс-кода'}
                </div>
                <span className="w-9" />
            </div>

            <div ref={boxRef} className="relative mx-auto w-full max-w-[560px] flex-1 overflow-hidden">
                {/* Видео не снимается с экрана, пока виден снимок, а только прячется: новый
                    <video> пришёл бы без потока камеры, и «Переснять» показало бы чёрный кадр. */}
                <video ref={videoRef} playsInline muted autoPlay
                       className={`absolute inset-0 h-full w-full object-cover ${mirrored ? '-scale-x-100' : ''} ${shot ? 'invisible' : ''}`} />
                {shot && <img src={shot.url} alt="Снимок" className="absolute inset-0 h-full w-full object-cover" />}

                {/* Рамка: всё вне её притемнено, внутри — силуэт-ориентир. */}
                <div className="pointer-events-none absolute rounded-[28px] ring-2 ring-white/90"
                     style={{ ...frameStyle, boxShadow: '0 0 0 9999px rgba(2,6,23,0.45)' }}>
                    {!shot && <Silhouette />}
                </div>

                <div className="pointer-events-none absolute inset-x-0 top-3 flex justify-center px-6">
                    <div className={`max-w-[440px] rounded-full px-3.5 py-1.5 text-center text-[13px] font-medium backdrop-blur-md ${
                        shownHint ? 'bg-amber-400/90 text-slate-900' : 'bg-black/45 text-white'}`}>
                        {shownHint || (shot ? 'Проверьте снимок' : 'Голова и одежда до пояса — внутри рамки')}
                    </div>
                </div>

                {(starting || error) && !shot && (
                    <div className="absolute inset-0 grid place-items-center px-8 text-center">
                        {starting && !error && <Loader2 size={26} className="animate-spin text-white/80" />}
                        {error && (
                            <div className="space-y-3">
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

            <div className="flex items-center justify-center gap-6 px-6 pb-[max(22px,env(safe-area-inset-bottom))] pt-4">
                {shot ? (
                    <>
                        <button type="button" onClick={retake} disabled={busy}
                                className="inline-flex h-11 items-center gap-2 rounded-full bg-white/12 px-5 text-[14px] font-semibold ring-1 ring-white/25 transition hover:bg-white/20 active:scale-[0.97] disabled:opacity-50">
                            <RotateCw size={16} /> Переснять
                        </button>
                        <button type="button" onClick={send} disabled={busy}
                                className="inline-flex h-11 items-center gap-2 rounded-full bg-white px-6 text-[14px] font-semibold text-slate-900 transition hover:bg-white/90 active:scale-[0.97] disabled:opacity-50">
                            {busy && <Loader2 size={16} className="animate-spin" />} Отправить
                        </button>
                    </>
                ) : (
                    <>
                        <span className="w-11" />
                        <button type="button" onClick={capture} disabled={busy || starting || Boolean(error)}
                                aria-label="Сделать снимок"
                                className="grid h-[70px] w-[70px] place-items-center rounded-full ring-4 ring-white/85 transition active:scale-95 disabled:opacity-40">
                            <span className="h-[56px] w-[56px] rounded-full bg-white" />
                        </button>
                        {cameras > 1 ? (
                            <button type="button" onClick={() => setFacing((mode) => (mode === 'user' ? 'environment' : 'user'))}
                                    disabled={busy} aria-label="Другая камера"
                                    className="grid h-11 w-11 place-items-center rounded-full bg-white/12 transition hover:bg-white/20 active:scale-95">
                                <SwitchCamera size={19} />
                            </button>
                        ) : <span className="w-11" />}
                    </>
                )}
            </div>

            {isMobileShell && !shot && (error || hang) && (
                <div className="px-6 pb-[max(18px,env(safe-area-inset-bottom))] text-center">
                    <button type="button" onClick={() => fileRef.current?.click()} disabled={busy}
                            className="inline-flex items-center gap-2 rounded-full bg-white/12 px-4 py-2 text-[13px] font-medium ring-1 ring-white/25 active:scale-[0.97]">
                        <Camera size={15} /> Снять камерой телефона
                    </button>
                    <input ref={fileRef} type="file" accept="image/*" capture="user" className="hidden" onChange={onSystemFile} />
                </div>
            )}
        </div>,
        document.body,
    );
}
