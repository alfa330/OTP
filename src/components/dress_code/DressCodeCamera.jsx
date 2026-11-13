import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Camera, Loader2, RotateCw, SwitchCamera, X } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import { FRAME, TOO_DARK_LUMA, frameRegion, meanLuma } from './dressCode';
import { sharedFaceDetector } from './faceDetector';
import {
    ADVICE_TEXT, FACE_INPUT, createAutoShutter, createHintSmoother, facesInFrame, fitInput, frameAdvice, regionLuma,
} from './faceMath';

/*
 * Камера «Дресс-кода» (ТЗ #418, п. 2, 3, 10).
 *
 * Видео — во весь экран, поверх него рамка почти на весь экран: верх — у макушки,
 * низ — у пояса, внутри силуэт «голова и плечи». НА ФОТО ПОПАДАЕТ РОВНО ТО, ЧТО В
 * РАМКЕ: всё вне её затемнено, это подписано, а после снимка в рамке стоит само
 * фото (владелец 13.11.2026: «очень большие поля снизу и сверху, не совсем понятно,
 * снимается ли всё»). Чёрных полос нет: кнопки лежат поверх видео.
 *
 * АВТОПРОВЕРКА КАДРА (владелец 13.11.2026: «когда он встаёт на идеальный кадр, камера
 * автоматически делает снимок и отправляет его»). Несколько раз в секунду кадр
 * проверяет маленькая нейросеть прямо в телефоне (faceWorker.js — лицо и его место в
 * рамке): подсказка в рамке сразу говорит, что поправить («Держите телефон чуть
 * дальше», «Голова не влезает в кадр»), а когда человек встал как надо — «Отлично, не
 * двигайтесь», кольцо вокруг кнопки отсчитывает чуть больше секунды, и снимок уходит
 * сам. Кнопка снимка остаётся: ею можно снять и вручную (тогда — с просмотром перед
 * отправкой). Если нейросеть на этом телефоне не запустилась, камера работает как
 * обычная — без подсказок вживую и без автоснимка.
 *
 * Загрузки из галереи нет: фото — только с камеры (п. 2). Если камера портала не
 * включилась (запрет, нет камеры, iPhone с иконки домашнего экрана, где запуск
 * камеры иногда висит), на телефоне остаётся «Снять камерой телефона» —
 * системная камера через capture="user": она снимает сразу, без выбора из
 * галереи. На компьютере такого выхода нет — там capture открыл бы выбор файла.
 *
 * После отправки кадр ещё раз смотрит ИИ на сервере (одежду не видно, размыто) и
 * при плохом кадре возвращает короткую подсказку — она приходит сюда `hint`.
 *
 * Окно — порталом в body: корень IosModal несёт backdrop-filter, и fixed внутри
 * него отсчитывался бы от карточки (см. IosLightbox).
 */

const MAX_SIDE = 1280;
const START_HANG_MS = 8000;
// Как часто проверяется кадр. Чаще — жарче телефон, реже — подсказка отстаёт от движения.
const DETECT_EVERY_MS = 130;
// Подсказка сервера («одежду не видно») держится на экране столько, и столько же автоснимок молчит.
const NOTICE_MS = 3000;
// Нейросеть не отвечает подряд столько раз — автопроверка выключается до конца съёмки.
const DETECT_FAILS_LIMIT = 5;

const cameraError = (error) => {
    const name = error?.name;
    if (name === 'NotAllowedError' || name === 'SecurityError') {
        return 'Доступ к камере запрещён — разрешите его в настройках браузера';
    }
    if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'Камера не найдена';
    if (name === 'NotReadableError') return 'Камера занята другим приложением';
    return 'Не удалось включить камеру';
};

/* Силуэт «голова и плечи» до пояса — ориентир, как встать: макушка чуть ниже верха
   рамки (над ней — подсказка), плечи почти во всю ширину, низ рамки — пояс. */
const Silhouette = ({ ok }) => (
    <svg viewBox="0 0 110 200" className="absolute inset-0 h-full w-full" preserveAspectRatio="xMidYMin meet"
         fill="none" stroke={ok ? 'rgba(52,211,153,0.95)' : 'rgba(255,255,255,0.6)'} strokeWidth="2"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <ellipse cx="55" cy="42" rx="20" ry="26" vectorEffect="non-scaling-stroke" />
        <path vectorEffect="non-scaling-stroke"
              d="M47 67 L47 75 Q47 82 39 85 L20 92 Q7 97 6 112 L4 200 M106 200 L104 112 Q103 97 90 92 L71 85 Q63 82 63 75 L63 67" />
    </svg>
);

const RING_R = 39;
const RING_LENGTH = 2 * Math.PI * RING_R;

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
    const [shot, setShot] = useState(null);           // { blob, url, auto }
    const [notice, setNotice] = useState('');         // подсказка сервера или «темно» при ручном снимке
    const [auto, setAuto] = useState('loading');      // loading | ready | off — автопроверка кадра
    const [live, setLive] = useState({ code: null, progress: 0 });

    // Цикл проверки кадра читает свежие значения отсюда, а не из замыкания.
    const shotRef = useRef(null);
    const busyRef = useRef(false);
    const pausedUntilRef = useRef(0);
    const firingRef = useRef(false);
    const noticeTimerRef = useRef(null);
    const onCaptureRef = useRef(onCapture);
    useEffect(() => { shotRef.current = shot; }, [shot]);
    useEffect(() => { busyRef.current = busy; }, [busy]);
    useEffect(() => { onCaptureRef.current = onCapture; }, [onCapture]);

    useScreenBackGesture(isMobileShell && open, onClose);

    const mirrored = facing === 'user';
    const mirroredRef = useRef(mirrored);
    useEffect(() => { mirroredRef.current = mirrored; }, [mirrored]);

    const dropShot = useCallback(() => {
        setShot((prev) => { if (prev) URL.revokeObjectURL(prev.url); return null; });
    }, []);

    const showNotice = useCallback((text) => {
        clearTimeout(noticeTimerRef.current);
        setNotice(text);
        pausedUntilRef.current = performance.now() + NOTICE_MS;
        noticeTimerRef.current = setTimeout(() => setNotice(''), NOTICE_MS);
    }, []);

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
            dropShot();
            clearTimeout(noticeTimerRef.current);
            setNotice('');
            setLive({ code: null, progress: 0 });
            return undefined;
        }
        start(facing);
        return stop;
    }, [open, facing, start, stop, dropShot]);

    /* Кадр из рамки → canvas: на фото — ровно то, что в рамке. */
    const grabFrame = useCallback(() => {
        const video = videoRef.current;
        const frame = frameRef.current;
        if (!video || !frame || !video.videoWidth) return null;
        const [sx, sy, sw, sh] = frameRegion(
            video.videoWidth, video.videoHeight, video.getBoundingClientRect(), frame.getBoundingClientRect(),
            mirroredRef.current);
        const scale = Math.min(1, MAX_SIDE / Math.max(sw, sh));
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(sw * scale);
        canvas.height = Math.round(sh * scale);
        canvas.getContext('2d').drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
        return canvas;
    }, []);

    const toShot = (canvas, isAuto) => new Promise((resolve) => {
        canvas.toBlob((blob) => resolve(blob ? { blob, url: URL.createObjectURL(blob), auto: isAuto } : null),
            'image/jpeg', 0.88);
    });

    /* Ручной снимок: с просмотром перед отправкой. */
    const capture = async () => {
        const canvas = grabFrame();
        if (!canvas) return;
        const probe = document.createElement('canvas');
        probe.width = 32;
        probe.height = 32;
        const probeCtx = probe.getContext('2d', { willReadFrequently: true });
        probeCtx.drawImage(canvas, 0, 0, 32, 32);
        if (meanLuma(probeCtx.getImageData(0, 0, 32, 32).data) < TOO_DARK_LUMA) {
            showNotice(ADVICE_TEXT.too_dark);
            return;
        }
        const next = await toShot(canvas, false);
        if (next) setShot(next);
    };

    const send = () => {
        if (shot && !busy) onCapture(shot.blob, 'camera');
    };

    const onSystemFile = (event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (file && !busy) onCapture(file, 'system');
    };

    // Сервер попросил переснять — возвращаемся к живому кадру, его подсказка — на экран.
    useEffect(() => {
        if (!hint) return;
        dropShot();
        showNotice(hint);
    }, [hint, dropShot, showNotice]);

    /* Автоснимок ушёл и вернулся отказом (нет связи): снимок остаётся на экране обычным
       просмотром — «Переснять» или «Отправить» решает человек. Снимать и слать заново по
       кругу, пока нет сети, камера не должна. Сервер попросил переснять — снимок уже убран
       эффектом выше, и здесь менять нечего. */
    const wasBusy = useRef(false);
    useEffect(() => {
        if (wasBusy.current && !busy && shotRef.current?.auto) {
            setShot((prev) => (prev ? { ...prev, auto: false } : prev));
        }
        wasBusy.current = busy;
    }, [busy]);

    /* Автопроверка кадра. Нейросеть общая на раздел и обычно уже загружена к открытию
       камеры (DressCodeView греет её заранее). Не загрузилась — камера остаётся обычной. */
    useEffect(() => {
        if (!open) return undefined;
        const detector = sharedFaceDetector();
        const smoother = createHintSmoother();
        const shutter = createAutoShutter();
        const input = document.createElement('canvas');
        input.width = FACE_INPUT;
        input.height = FACE_INPUT;
        const ctx = input.getContext('2d', { willReadFrequently: true });
        let alive = true;
        let timer = null;
        let fails = 0;
        setAuto('loading');

        const idle = () => {
            shutter.reset();
            smoother.reset();
            setLive((prev) => (prev.code === null && prev.progress === 0 ? prev : { code: null, progress: 0 }));
        };

        const autoShoot = async () => {
            if (firingRef.current) return;
            firingRef.current = true;
            try {
                const canvas = grabFrame();
                const next = canvas ? await toShot(canvas, true) : null;
                if (!alive || !next) return;
                if (shotRef.current || busyRef.current) {       // успели снять вручную
                    URL.revokeObjectURL(next.url);
                    return;
                }
                shotRef.current = next;
                setShot(next);
                onCaptureRef.current?.(next.blob, 'camera');
            } finally {
                firingRef.current = false;
            }
        };

        const tick = async () => {
            if (!alive) return;
            const video = videoRef.current;
            const frame = frameRef.current;
            const waiting = !video || !frame || !video.videoWidth || video.paused || document.hidden
                || shotRef.current || busyRef.current || firingRef.current || performance.now() < pausedUntilRef.current;
            if (waiting) {
                idle();
            } else {
                try {
                    const box = video.getBoundingClientRect();
                    const rect = frame.getBoundingClientRect();
                    // Нейросети — всё видимое на экране, не только рамка: так видно и голову,
                    // вылезшую за её край.
                    const [vx, vy, vw, vh] = frameRegion(video.videoWidth, video.videoHeight, box, box);
                    const fit = fitInput(vw, vh, FACE_INPUT);
                    ctx.fillStyle = '#000';
                    ctx.fillRect(0, 0, FACE_INPUT, FACE_INPUT);
                    ctx.drawImage(video, vx, vy, vw, vh, 0, 0, fit.width, fit.height);
                    const image = ctx.getImageData(0, 0, FACE_INPUT, FACE_INPUT);
                    const dark = regionLuma(image.data, FACE_INPUT,
                        ((rect.left - box.left) / box.width) * fit.width, ((rect.top - box.top) / box.height) * fit.height,
                        ((rect.right - box.left) / box.width) * fit.width, ((rect.bottom - box.top) / box.height) * fit.height,
                    ) < TOO_DARK_LUMA;
                    const faces = await detector.detect(image.data.buffer);
                    fails = 0;
                    if (!alive) return;
                    if (shotRef.current || busyRef.current || performance.now() < pausedUntilRef.current) {
                        idle();
                    } else {
                        const advice = frameAdvice(facesInFrame(faces, fit, box, rect), { dark, relaxed: shutter.holding() });
                        const hold = shutter.update(advice, performance.now());
                        setLive({ code: smoother.push(advice.code), progress: hold.progress });
                        if (hold.fire) {
                            shutter.reset();
                            autoShoot();
                        }
                    }
                } catch (err) {
                    fails += 1;
                    if (fails >= DETECT_FAILS_LIMIT) {
                        setAuto('off');
                        idle();
                        return;                                   // цикл останавливается
                    }
                }
            }
            if (alive) timer = setTimeout(tick, DETECT_EVERY_MS);
        };

        detector.init().then(() => {
            if (!alive) return;
            setAuto('ready');
            tick();
        }).catch(() => { if (alive) setAuto('off'); });

        // Детектор общий на раздел (faceDetector.js) — здесь останавливается только цикл.
        return () => {
            alive = false;
            clearTimeout(timer);
        };
    }, [open, grabFrame]);

    if (!open || typeof document === 'undefined') return null;
    const liveOn = auto === 'ready' && !shot && !starting && !error && live.code;
    const aligned = Boolean(liveOn && live.code === 'ok');
    // Что написано в рамке: подсказка сервера → живая подсказка → общее «как встать».
    const pill = notice
        ? { text: notice, tone: 'bg-amber-400 text-slate-900' }
        : liveOn
            ? { text: ADVICE_TEXT[live.code] || ADVICE_TEXT.none,
                tone: aligned ? 'bg-emerald-500 text-white'
                    : live.code === 'none' ? 'bg-black/55 text-white' : 'bg-amber-400 text-slate-900' }
            : { text: shot ? (shot.auto ? 'Снято' : 'Проверьте фото') : 'В рамке — от головы до пояса', tone: 'bg-black/55 text-white' };
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
    const autoSending = Boolean(shot?.auto);

    return createPortal(
        <div className="fixed inset-0 z-[300] overflow-hidden bg-black text-white" style={{ fontFamily: APPLE_FONT }}
             role="dialog" aria-modal="true" aria-label="Фото для дресс-кода">
            {/* Видео не снимается с экрана, пока виден снимок, а только прячется: новый
                <video> пришёл бы без потока камеры, и «Переснять» показало бы чёрный кадр. */}
            <video ref={videoRef} playsInline muted autoPlay
                   className={`absolute inset-0 h-full w-full object-cover ${mirrored ? '-scale-x-100' : ''} ${shot ? 'invisible' : ''}`} />

            <div ref={frameRef}
                 className={`pointer-events-none absolute overflow-hidden rounded-[26px] ring-2 transition-colors ${aligned ? 'ring-emerald-400' : 'ring-white'}`}
                 style={frameStyle}>
                {shot ? (
                    <img src={shot.url} alt="Снимок" className={`h-full w-full object-cover ${mirrored ? '-scale-x-100' : ''}`} />
                ) : (
                    !starting && !error && <Silhouette ok={aligned} />
                )}
                {/* Подсказка — в рамке над головой: там, куда человек и так смотрит. */}
                {!starting && !error && !busy && (
                    <div className="absolute inset-x-0 top-2.5 flex justify-center px-3">
                        <span className={`rounded-full px-3.5 py-1.5 text-center text-[14px] font-semibold leading-tight backdrop-blur-md transition-colors ${pill.tone}`}
                              role="status" aria-live="polite">
                            {pill.text}
                        </span>
                    </div>
                )}
                {!shot && !starting && !error && (
                    <div className="absolute inset-x-0 bottom-3 flex justify-center">
                        <span className="rounded-full bg-black/55 px-3 py-1 text-[12.5px] font-medium text-white backdrop-blur-md">
                            На фото попадёт то, что в рамке
                        </span>
                    </div>
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

            <button type="button" onClick={onClose} disabled={busy} aria-label="Закрыть камеру"
                    className={`${roundBtn} absolute left-3`} style={{ top: `calc(${safeTop} + 10px)` }}>
                <X size={19} />
            </button>

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
                <div className="flex h-[78px] items-center justify-center gap-6">
                    {shot && !autoSending && (
                        <>
                            <button type="button" onClick={dropShot} disabled={busy}
                                    className="inline-flex h-12 items-center gap-2 rounded-full bg-white/15 px-5 text-[14.5px] font-semibold ring-1 ring-white/30 backdrop-blur-md transition hover:bg-white/25 active:scale-[0.97] disabled:opacity-50">
                                <RotateCw size={16} /> Переснять
                            </button>
                            <button type="button" onClick={send} disabled={busy}
                                    className="inline-flex h-12 items-center gap-2 rounded-full bg-white px-7 text-[14.5px] font-semibold text-slate-900 transition hover:bg-white/90 active:scale-[0.97] disabled:opacity-50">
                                {busy && <Loader2 size={16} className="animate-spin" />} Отправить
                            </button>
                        </>
                    )}
                    {!shot && (
                        <>
                            <span className="w-11" />
                            <button type="button" onClick={capture} disabled={busy || starting || Boolean(error)}
                                    aria-label="Сделать снимок"
                                    className="relative grid h-[78px] w-[78px] place-items-center rounded-full transition active:scale-95 disabled:opacity-40">
                                {/* Кольцо: белое — кнопка снимка; зелёная дуга — отсчёт автоснимка. */}
                                <svg viewBox="0 0 86 86" className="absolute inset-0 h-full w-full -rotate-90" aria-hidden="true">
                                    <circle cx="43" cy="43" r={RING_R} fill="rgba(0,0,0,0.25)" stroke="#fff" strokeWidth="4" />
                                    {aligned && (
                                        <circle cx="43" cy="43" r={RING_R} fill="none" stroke="#34d399" strokeWidth="4" strokeLinecap="round"
                                                strokeDasharray={RING_LENGTH} strokeDashoffset={RING_LENGTH * (1 - live.progress)}
                                                style={{ transition: `stroke-dashoffset ${DETECT_EVERY_MS + 60}ms linear` }} />
                                    )}
                                </svg>
                                <span className="relative h-[58px] w-[58px] rounded-full bg-white" />
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
