import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Camera, Loader2, RotateCw, SwitchCamera, X } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import { CAMERA_BARS, TOO_DARK_LUMA, fitStage, meanLuma, pct, photoAspect, photoRegion } from './dressCode';
import { sharedFaceDetector } from './faceDetector';
import {
    ADVICE_TEXT, FACE_INPUT, cleanRules, createAutoShutter, createHintSmoother, facesInFrame, fitInput, frameAdvice,
    metricChecks, regionLuma, silhouetteGuide,
} from './faceMath';

/*
 * Камера «Дресс-кода» (ТЗ #418, п. 2, 3, 10).
 *
 * ЧТО ВИДНО — ТО И СНЯТО. На экране стоит ровно та часть кадра камеры, которая попадёт
 * на фото, и ничего больше; она занимает всё место между строкой подсказки и кнопками
 * (владелец 13.11.2026: «очень большие поля снизу и сверху, не совсем понятно, снимается
 * ли всё»). ВЫСОТА КАДРА КАМЕРЫ НЕ РЕЖЕТСЯ НИКОГДА: на вытянутой руке камера видит
 * человека едва до пояса, и каждый отрезанный сверху или снизу процент делает «фото по
 * пояс» недостижимым. Чтобы кадр встал на высокий экран без чёрных полос, у сенсора
 * отрезаются только бока (dressCode.js: photoAspect, photoRegion).
 *
 * АВТОПРОВЕРКА КАДРА (владелец 13.11.2026: «когда он встаёт на идеальный кадр, камера
 * автоматически делает снимок и отправляет его»; вечером: «авто фотографирование сработало
 * даже без условия по пояс — исправь раннее срабатывание»). Несколько раз в секунду кадр
 * проверяет маленькая нейросеть прямо в телефоне (faceWorker.js — лицо и его место в
 * кадре). Главное правило — пояс должен помещаться в кадр: он оценивается по лицу, числа
 * правил приходят с сервера (`rules`) и правятся в стенде. Подсказка сразу говорит, что
 * поправить («Держите телефон чуть дальше», «Голова не влезает в кадр»), а когда человек
 * встал как надо — «Отлично, не двигайтесь», кольцо вокруг кнопки отсчитывает выдержку, и
 * снимок уходит сам. Кнопка снимка остаётся: ею можно снять и вручную (тогда — с
 * просмотром перед отправкой). Если нейросеть на этом телефоне не запустилась, камера
 * работает как обычная — без подсказок вживую и без автоснимка.
 *
 * СТЕНД СУПЕРАДМИНА (DressCodeStand.jsx) — эта же камера с `debug`: поверх кадра рамка
 * найденного лица, линия пояса и измеренные числа; автоснимок никуда не уходит
 * (`autoSend={false}`), а под кадром — ползунки правил (`panel`).
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

/* Кадр 4:3 — весь сенсор камеры: у телефона это самый высокий и самый широкий обзор
   (16:9 — тот же сенсор, обрезанный по бокам). resizeMode 'none' — брать родной режим
   камеры, а не вырезать 4:3 из 16:9: такая вырезка срезала бы как раз высоту. Числа — в
   альбомной записи, как их понимают браузеры телефонов; в портрете придёт 960×1280. */
const VIDEO_WISH = { width: { ideal: 1280 }, height: { ideal: 960 }, resizeMode: 'none' };

const cameraError = (error) => {
    const name = error?.name;
    if (name === 'NotAllowedError' || name === 'SecurityError') {
        return 'Доступ к камере запрещён — разрешите его в настройках браузера';
    }
    if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'Камера не найдена';
    if (name === 'NotReadableError') return 'Камера занята другим приложением';
    return 'Не удалось включить камеру';
};

/* Контур «голова и плечи» — ориентир, куда встать: голова у верха кадра, плечи под ней,
   низ кадра — пояс. Размер головы считается из правил (silhouetteGuide): совместил голову с
   контуром — пояс в кадре с запасом. Рисунок в единицах «1 = 1 % высоты кадра». */
const Silhouette = ({ ok, guide, aspect }) => {
    const k = ((guide.chin - guide.crown) * 100) / 52;
    const cx = 50 * aspect;
    const chin = guide.chin * 100;
    const x = (value) => (cx + (value - 55) * k).toFixed(2);
    const y = (value) => (chin + (value - 68) * k).toFixed(2);
    const body = `M${x(47)} ${y(67)} L${x(47)} ${y(75)} Q${x(47)} ${y(82)} ${x(39)} ${y(85)} L${x(20)} ${y(92)} `
        + `Q${x(7)} ${y(97)} ${x(6)} ${y(112)} L${x(4)} 100 `
        + `M${x(106)} 100 L${x(104)} ${y(112)} Q${x(103)} ${y(97)} ${x(90)} ${y(92)} L${x(71)} ${y(85)} `
        + `Q${x(63)} ${y(82)} ${x(63)} ${y(75)} L${x(63)} ${y(67)}`;
    return (
        <svg viewBox={`0 0 ${(100 * aspect).toFixed(2)} 100`} className="pointer-events-none absolute inset-0 h-full w-full"
             fill="none" stroke={ok ? 'rgba(52,211,153,0.95)' : 'rgba(255,255,255,0.6)'} strokeWidth="2"
             strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <ellipse cx={cx} cy={chin - 26 * k} rx={20 * k} ry={26 * k} vectorEffect="non-scaling-stroke" />
            <path vectorEffect="non-scaling-stroke" d={body} />
        </svg>
    );
};

/* Стенд: что нейросеть нашла в кадре — рамка лица, макушка и пояс. Пояс зелёный, когда
   помещается в кадр, красный — когда ушёл за нижний край. Превью зеркальное — рамка тоже. */
const DebugOverlay = ({ box, metrics, mirrored, waistOk }) => {
    const left = mirrored ? 1 - box.x - box.w : box.x;
    const waist = Math.min(metrics.waist, 0.994);
    const tone = waistOk ? '#34d399' : '#fb7185';
    return (
        <>
            <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">
                <rect x={left} y={box.y} width={box.w} height={box.h} fill="none" stroke="#fff" strokeWidth="2" vectorEffect="non-scaling-stroke" />
                <line x1="0" x2="1" y1={metrics.top} y2={metrics.top} stroke="#fff" strokeWidth="1.5" strokeDasharray="5 5" vectorEffect="non-scaling-stroke" />
                <line x1="0" x2="1" y1={waist} y2={waist} stroke={tone} strokeWidth="2.5" strokeDasharray="8 5" vectorEffect="non-scaling-stroke" />
            </svg>
            <span className="pointer-events-none absolute right-2 -translate-y-full rounded-md px-1.5 py-0.5 text-[12px] font-semibold text-slate-900"
                  style={{ top: `${waist * 100}%`, backgroundColor: tone }}>
                {waistOk ? 'пояс' : 'пояс ниже кадра'}
            </span>
        </>
    );
};

const READOUT = [
    { key: 'face', label: 'Лицо' },
    { key: 'top', label: 'Макушка' },
    { key: 'waist', label: 'Пояс' },
    { key: 'center', label: 'Центр' },
];

const RING_R = 39;
const RING_LENGTH = 2 * Math.PI * RING_R;

export default function DressCodeCamera({
    open, onClose, onCapture, busy = false, hint = '', rules = null,
    autoShot = true, autoSend = true, sendLabel = 'Отправить', debug = false, panel = null, leftSlot = null,
    onShotChange = null,
}) {
    const isMobileShell = useIsMobileShell();
    const videoRef = useRef(null);
    const stageRef = useRef(null);
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
    const [live, setLive] = useState({ code: null, progress: 0, metrics: null, box: null });
    const [videoSize, setVideoSize] = useState({ width: 0, height: 0 });
    const [stageSize, setStageSize] = useState({ width: 0, height: 0 });

    const activeRules = useMemo(() => cleanRules(rules), [rules]);

    // Цикл проверки кадра читает свежие значения отсюда, а не из замыкания: ползунки
    // стенда меняют правила на ходу, и цикл из-за этого не перезапускается.
    const shotRef = useRef(null);
    const busyRef = useRef(false);
    const pausedUntilRef = useRef(0);
    const firingRef = useRef(false);
    const noticeTimerRef = useRef(null);
    const onCaptureRef = useRef(onCapture);
    const onShotChangeRef = useRef(onShotChange);
    const rulesRef = useRef(activeRules);
    const aspectRef = useRef(photoAspect(0, 0));
    const autoShotRef = useRef(autoShot);
    const autoSendRef = useRef(autoSend);
    useEffect(() => { shotRef.current = shot; }, [shot]);
    useEffect(() => { busyRef.current = busy; }, [busy]);
    useEffect(() => { onCaptureRef.current = onCapture; }, [onCapture]);
    useEffect(() => { onShotChangeRef.current = onShotChange; }, [onShotChange]);
    useEffect(() => { rulesRef.current = activeRules; }, [activeRules]);
    // Пропорции снимка — по месту на экране; цикл проверки и сам снимок берут их отсюда.
    const viewAspect = photoAspect(stageSize.width, stageSize.height);
    useEffect(() => { aspectRef.current = viewAspect; }, [viewAspect]);
    useEffect(() => { autoShotRef.current = autoShot; }, [autoShot]);
    useEffect(() => { autoSendRef.current = autoSend; }, [autoSend]);
    useEffect(() => { onShotChangeRef.current?.(Boolean(shot)); }, [shot]);

    useScreenBackGesture(isMobileShell && open, onClose);

    const mirrored = facing === 'user';

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
                video: { facingMode: { ideal: mode }, ...VIDEO_WISH },
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
            setLive({ code: null, progress: 0, metrics: null, box: null });
            return undefined;
        }
        start(facing);
        return stop;
    }, [open, facing, start, stop, dropShot]);

    /* Размер кадра камеры: приходит с первым кадром и меняется, когда телефон повернули. */
    useEffect(() => {
        if (!open) return undefined;
        const video = videoRef.current;
        if (!video) return undefined;
        const read = () => setVideoSize((prev) => (
            prev.width === video.videoWidth && prev.height === video.videoHeight
                ? prev : { width: video.videoWidth, height: video.videoHeight }));
        read();
        video.addEventListener('loadedmetadata', read);
        video.addEventListener('resize', read);
        return () => {
            video.removeEventListener('loadedmetadata', read);
            video.removeEventListener('resize', read);
        };
    }, [open]);

    /* Место под кадр — всё между строкой подсказки и кнопками. */
    useLayoutEffect(() => {
        if (!open) return undefined;
        const stage = stageRef.current;
        if (!stage) return undefined;
        const read = () => setStageSize((prev) => (
            prev.width === stage.clientWidth && prev.height === stage.clientHeight
                ? prev : { width: stage.clientWidth, height: stage.clientHeight }));
        read();
        const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(read) : null;
        observer?.observe(stage);
        window.addEventListener('resize', read);
        return () => {
            observer?.disconnect();
            window.removeEventListener('resize', read);
        };
    }, [open]);

    /* Снимаемая часть кадра → canvas: на фото — ровно то, что видно на экране. */
    const grabFrame = useCallback(() => {
        const video = videoRef.current;
        if (!video || !video.videoWidth) return null;
        const [sx, sy, sw, sh] = photoRegion(video.videoWidth, video.videoHeight, aspectRef.current);
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
            setLive((prev) => (prev.code === null && prev.progress === 0 ? prev : { code: null, progress: 0, metrics: null, box: null }));
        };

        const autoShoot = async () => {
            if (firingRef.current) return;
            firingRef.current = true;
            try {
                // Стенд (autoSend выключен): автоснимок никуда не уходит и остаётся обычным просмотром.
                const sending = autoSendRef.current;
                const canvas = grabFrame();
                const next = canvas ? await toShot(canvas, sending) : null;
                if (!alive || !next) return;
                if (shotRef.current || busyRef.current) {       // успели снять вручную
                    URL.revokeObjectURL(next.url);
                    return;
                }
                shotRef.current = next;
                setShot(next);
                if (sending) onCaptureRef.current?.(next.blob, 'camera');
            } finally {
                firingRef.current = false;
            }
        };

        const tick = async () => {
            if (!alive) return;
            const video = videoRef.current;
            const waiting = !video || !video.videoWidth || video.paused || document.hidden
                || shotRef.current || busyRef.current || firingRef.current || performance.now() < pausedUntilRef.current;
            if (waiting) {
                idle();
            } else {
                try {
                    // Нейросети — ровно то, что попадёт на фото.
                    const [sx, sy, sw, sh] = photoRegion(video.videoWidth, video.videoHeight, aspectRef.current);
                    const fit = fitInput(sw, sh, FACE_INPUT);
                    ctx.fillStyle = '#000';
                    ctx.fillRect(0, 0, FACE_INPUT, FACE_INPUT);
                    ctx.drawImage(video, sx, sy, sw, sh, 0, 0, fit.width, fit.height);
                    const image = ctx.getImageData(0, 0, FACE_INPUT, FACE_INPUT);
                    const dark = regionLuma(image.data, FACE_INPUT, 0, 0, fit.width, fit.height) < TOO_DARK_LUMA;
                    const faces = await detector.detect(image.data.buffer);
                    fails = 0;
                    if (!alive) return;
                    if (shotRef.current || busyRef.current || performance.now() < pausedUntilRef.current) {
                        idle();
                    } else {
                        const current = rulesRef.current;
                        const advice = frameAdvice(facesInFrame(faces, fit), { dark, relaxed: shutter.holding(), rules: current });
                        let hold = { progress: 0, fire: false };
                        if (autoShotRef.current) hold = shutter.update(advice, performance.now(), current.hold_ms);
                        else shutter.reset();
                        setLive({
                            code: smoother.push(advice.code), progress: hold.progress,
                            metrics: advice.metrics || null, box: advice.box || null,
                        });
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
    // Что написано над кадром: подсказка сервера → живая подсказка → общее «как встать».
    const pill = notice
        ? { text: notice, tone: 'bg-amber-400 text-slate-900' }
        : liveOn
            ? { text: ADVICE_TEXT[live.code] || ADVICE_TEXT.none,
                tone: aligned ? 'bg-emerald-500 text-white'
                    : live.code === 'none' ? 'bg-white/15 text-white' : 'bg-amber-400 text-slate-900' }
            : { text: shot ? (shot.auto ? 'Снято' : 'Проверьте фото') : 'В кадре — от головы до пояса', tone: 'bg-white/15 text-white' };
    const safeTop = 'env(safe-area-inset-top, 0px)';
    const safeBottom = 'env(safe-area-inset-bottom, 0px)';
    // Кадр на экране: снимаемая часть, вписанная целиком. Пока камера не ответила — пустой
    // кадр тех же пропорций.
    const [, , regionWidth, regionHeight] = photoRegion(videoSize.width, videoSize.height, viewAspect);
    const frame = fitStage(regionWidth || viewAspect, regionHeight || 1, stageSize.width, stageSize.height);
    const aspect = frame.height ? frame.width / frame.height : 0.75;
    const videoWidth = videoSize.height ? frame.height * (videoSize.width / videoSize.height) : frame.width;
    const guide = silhouetteGuide(activeRules);
    const checks = metricChecks(live.metrics, activeRules);
    const roundBtn = 'grid h-11 w-11 place-items-center rounded-full bg-white/15 transition hover:bg-white/25 active:scale-95 disabled:opacity-50';
    const autoSending = Boolean(shot?.auto);
    const viewing = !shot && !starting && !error;

    return createPortal(
        <div className="fixed inset-0 z-[300] flex flex-col overflow-hidden bg-black text-white" style={{ fontFamily: APPLE_FONT }}
             role="dialog" aria-modal="true" aria-label={debug ? 'Стенд камеры дресс-кода' : 'Фото для дресс-кода'}>
            {/* Верх: закрыть и подсказка — прямо над головой, куда человек и так смотрит. */}
            <div className="grid shrink-0 grid-cols-[44px_minmax(0,1fr)_44px] items-center gap-2 px-3"
                 style={{ paddingTop: safeTop, height: `calc(${safeTop} + ${CAMERA_BARS.top}px)` }}>
                <button type="button" onClick={onClose} disabled={busy} aria-label="Закрыть камеру" className={roundBtn}>
                    <X size={19} />
                </button>
                <div className="flex min-w-0 justify-center">
                    {!starting && !error && !busy && (
                        <span className={`max-w-full rounded-full px-3.5 py-1.5 text-center text-[14px] font-semibold leading-tight transition-colors ${pill.tone}`}
                              role="status" aria-live="polite">
                            {pill.text}
                        </span>
                    )}
                </div>
                <span />
            </div>

            {/* Кадр: на экране ровно то, что попадёт на фото. */}
            <div ref={stageRef} className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden">
                <div className="relative shrink-0 overflow-hidden rounded-[22px] bg-slate-900"
                     style={{ width: frame.width, height: frame.height }}>
                    {/* Видео не снимается с экрана, пока виден снимок, а только прячется: новый
                        <video> пришёл бы без потока камеры, и «Переснять» показало бы чёрный кадр. */}
                    <video ref={videoRef} playsInline muted autoPlay
                           className={`absolute top-0 h-full max-w-none object-cover ${shot ? 'invisible' : ''}`}
                           style={{ left: '50%', width: videoWidth, transform: `translateX(-50%)${mirrored ? ' scaleX(-1)' : ''}` }} />
                    {shot && (
                        <img src={shot.url} alt="Снимок" className={`absolute inset-0 h-full w-full object-cover ${mirrored ? '-scale-x-100' : ''}`} />
                    )}
                    {viewing && <Silhouette ok={aligned} guide={guide} aspect={aspect} />}
                    {debug && viewing && live.box && live.metrics && (
                        <DebugOverlay box={live.box} metrics={live.metrics} mirrored={mirrored} waistOk={checks.waist} />
                    )}
                    {debug && viewing && (
                        <span className="absolute left-2 top-2 rounded-md bg-black/60 px-1.5 py-0.5 text-[11.5px] font-medium tabular-nums text-white/85">
                            камера {videoSize.width}×{videoSize.height}
                            {regionWidth && regionWidth !== videoSize.width ? ` · на фото ${regionWidth}×${regionHeight}` : ''}
                            {auto === 'loading' ? ' · нейросеть грузится' : auto === 'off' ? ' · нейросеть не запустилась' : ''}
                        </span>
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
                    {/* Обводка поверх видео: зелёная — кадр годится. */}
                    <div className={`pointer-events-none absolute inset-0 rounded-[22px] ring-2 ring-inset transition-colors ${aligned ? 'ring-emerald-400' : 'ring-white/60'}`} />
                </div>
            </div>

            {/* Стенд: что измерила нейросеть. Зелёное — в правилах, красное — нет. Строкой под
                кадром, а не поверх него: линию пояса и одежду ничто не закрывает. Высота строки
                постоянная: если бы она росла вместе с числами, менялось бы место под кадр, а с
                ним и сам кадр. */}
            {debug && (
                <div className="mx-auto grid h-[38px] w-full max-w-md shrink-0 grid-cols-4 items-center gap-1 px-2" data-readout>
                    {viewing && live.metrics ? READOUT.map(({ key, label }) => (
                        <span key={key}
                              className={`truncate rounded-full px-1 py-1 text-center text-[12px] font-semibold tabular-nums text-white ${checks[key] ? 'bg-emerald-600' : 'bg-rose-600'}`}>
                            {label} {pct(live.metrics[key])}
                        </span>
                    )) : (
                        <span className="col-span-4 text-center text-[13px] font-medium text-white/60">
                            {viewing && auto === 'ready' ? 'Лицо не найдено' : ''}
                        </span>
                    )}
                </div>
            )}

            {panel}

            {/* Низ: кнопки под кадром. */}
            <div className="flex shrink-0 flex-col items-center justify-center gap-2 px-6"
                 style={{ minHeight: `calc(${safeBottom} + ${CAMERA_BARS.bottom}px)`, paddingBottom: `calc(${safeBottom} + 8px)`, paddingTop: 8 }}>
                {isMobileShell && !shot && (error || hang) && (
                    <>
                        <button type="button" onClick={() => fileRef.current?.click()} disabled={busy}
                                className="inline-flex items-center gap-2 rounded-full bg-white/15 px-4 py-2 text-[13px] font-medium ring-1 ring-white/25 active:scale-[0.97]">
                            <Camera size={15} /> Снять камерой телефона
                        </button>
                        <input ref={fileRef} type="file" accept="image/*" capture="user" className="hidden" onChange={onSystemFile} />
                    </>
                )}
                <div className="flex h-[78px] items-center justify-center gap-6">
                    {shot && !autoSending && (
                        <>
                            <button type="button" onClick={dropShot} disabled={busy}
                                    className="inline-flex h-12 items-center gap-2 rounded-full bg-white/15 px-5 text-[14.5px] font-semibold ring-1 ring-white/30 transition hover:bg-white/25 active:scale-[0.97] disabled:opacity-50">
                                <RotateCw size={16} /> Переснять
                            </button>
                            <button type="button" onClick={send} disabled={busy}
                                    className="inline-flex h-12 items-center gap-2 rounded-full bg-white px-7 text-[14.5px] font-semibold text-slate-900 transition hover:bg-white/90 active:scale-[0.97] disabled:opacity-50">
                                {busy && <Loader2 size={16} className="animate-spin" />} {sendLabel}
                            </button>
                        </>
                    )}
                    {!shot && (
                        <>
                            {leftSlot || <span className="w-11" />}
                            <button type="button" onClick={capture} disabled={busy || starting || Boolean(error)}
                                    aria-label="Сделать снимок"
                                    className="relative grid h-[78px] w-[78px] place-items-center rounded-full transition active:scale-95 disabled:opacity-40">
                                {/* Кольцо: белое — кнопка снимка; зелёная дуга — отсчёт автоснимка. */}
                                <svg viewBox="0 0 86 86" className="absolute inset-0 h-full w-full -rotate-90" aria-hidden="true">
                                    <circle cx="43" cy="43" r={RING_R} fill="rgba(255,255,255,0.08)" stroke="#fff" strokeWidth="4" />
                                    {aligned && autoShot && (
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
