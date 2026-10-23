import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CameraOff, Crop, Flashlight, FlashlightOff, ImagePlus, Loader2, Plus, RotateCw, Trash2 } from 'lucide-react';
import { APPLE_FONT, IosHint, IosSegmented, iosBtnPrimary, iosBtnSecondary, prefersReducedMotion } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import { holdPageScroll } from '../../utils/pageScrollLock';
import {
    buildPdf, createQuadTracker, cropUsable, defaultCorners, DETECT_SIDE, detectDocument, enhancePage, insetCorners,
    keepScannedPage, outputSize, SCAN_DEFAULT_FILTER, SCAN_FILTER_HINT, SCAN_FILTERS, SCAN_MAX_PAGES, SCAN_QUALITY_STEPS,
    SCAN_TARGET_BYTES, scaleCorners, scanFileName, scannerBack, scanOutputType, toGray, turnedSize, warpPerspective,
} from './docScan';

/*
 * Скан документа камерой (владелец, 23.10.2026: «кнопку приложить документ
 * сделай при нажатии селектор, загрузить или сделать скан, при скане
 * открывается камера и делается скан документа на фото»).
 *
 * Три шага в одном окне, как у сканера iOS:
 *   камера   — живой кадр; найденный лист обведён рамкой, кнопка снимка;
 *   края     — снимок с рамкой листа, углы перетаскиваются (на телефоне под
 *              пальцем — лупа: палец закрывает ровно тот угол, который ставят);
 *   страницы — выпрямленные страницы «как скан», фильтр, поворот, ещё
 *              страница; «Приложить» отдаёт файл кнопке, которая открыла окно.
 * Одна страница уходит фото (JPEG), несколько — одним PDF.
 *
 * Камера — внутри портала (getUserMedia), а не системное приложение: так
 * рамка листа видна до снимка, а страницы снимаются подряд, не выходя из окна.
 * Если камера не включилась (запрет, нет камеры, iPhone с иконки домашнего
 * экрана, где запуск камеры иногда висит), выход один кнопкой — «Снять
 * камерой телефона» / «Выбрать фото»: снимок системной камеры проходит те же
 * шаги. Разрешения на камеру для этого не нужно.
 *
 * Окно — порталом в body: корень IosModal несёт backdrop-filter, и всё
 * fixed внутри карточки отсчитывалось бы от неё (см. IosLightbox). Слой — выше
 * листа выбора телефона (.mobile-actions, 10000): «Сделать скан» открывает
 * сканер, пока лист ещё уезжает вниз, и иначе 240 мс он лежал бы поверх камеры
 * вместе со своим размытием (стенд, 23.10.2026).
 */

/* Какой кадр просим у камеры. 4K «по возможности» (ideal, не exact): лист A4
   на 4K-кадре — ~180 точек на дюйм, на 1080p — вдвое меньше, и подпись
   расплывается. Камера, которая столько не умеет, отдаёт ближайшее.
   focusMode — в advanced: незнакомое требование там просто пропускают. */
const CAMERA_CONSTRAINTS = {
    facingMode: { ideal: 'environment' },
    width: { ideal: 3840 },
    height: { ideal: 2160 },
    advanced: [{ focusMode: 'continuous' }],
};
/* Живой поиск — не чаще раза в 180 мс и не чаще, чем через четыре своих
   длительности: на пёстром кадре слабый телефон тратит на поиск 50–100 мс, и
   при жёстком шаге поиск занял бы весь главный поток. */
const DETECT_INTERVAL_MS = 180;
const DETECT_BUDGET = 4;
/* Камера не ответила за это время — рядом с «Включаю камеру…» появляется
   выход через системную камеру. Ожидание не обрывается: человек мог просто
   ещё не нажать «Разрешить». */
const SLOW_START_MS = 6000;
const STILL_DETECT_SIDE = 480;
const PREVIEW_SIDE = 1200;
const IMPORT_MAX_SIDE = 3000;
/* Предел холста на iPhone — 16,7 млн пикселей; снимок 4K с запасом под ним. */
const SOURCE_MAX_SIDE = 4096;
const HANDLE_PX = 11;
const HANDLE_HIT_PX = 26;
const LOUPE_PX = 112;
const LOUPE_ZOOM = 2.5;

const makeCanvas = (w, h) => {
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    return canvas;
};

const freeCanvas = (canvas) => {
    if (!canvas) return;
    canvas.width = 0;
    canvas.height = 0;
};

const canvasBlob = (canvas, type, quality) => new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('toBlob'))), type, quality);
});

/* Кадр-картинка из Blob. createImageBitmap быстрее и не держит <img>; где его
   нет — обычная картинка. */
const decodeBlob = async (blob) => {
    if (typeof createImageBitmap === 'function') {
        const bitmap = await createImageBitmap(blob);
        return { image: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close?.() };
    }
    const image = await loadImage(URL.createObjectURL(blob), true);
    return { image, width: image.naturalWidth, height: image.naturalHeight, close: () => {} };
};

/* Фото из файла — через <img>: так браузер сам разворачивает снимок по EXIF
   (телефон пишет «повернуть на 90°» в метаданные, а не в пиксели). */
const loadImage = (url, revoke = false) => new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
        if (revoke) URL.revokeObjectURL(url);
        resolve(image);
    };
    image.onerror = () => {
        if (revoke) URL.revokeObjectURL(url);
        reject(new Error('image'));
    };
    image.src = url;
});

/* Дать браузеру нарисовать спиннер до тяжёлой работы на главном потоке. */
const nextFrame = () => new Promise((resolve) => {
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => setTimeout(resolve, 0));
    else setTimeout(resolve, 0);
});

/* Углы листа на снимке: поиск на уменьшенной копии, ответ — в пикселях снимка. */
const findCorners = (canvas, w, h) => {
    const scale = Math.min(1, STILL_DETECT_SIDE / Math.max(w, h));
    const dw = Math.max(1, Math.round(w * scale));
    const dh = Math.max(1, Math.round(h * scale));
    const small = makeCanvas(dw, dh);
    const ctx = small.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(canvas, 0, 0, dw, dh);
    const found = detectDocument(toGray(ctx.getImageData(0, 0, dw, dh).data, dw, dh), dw, dh);
    freeCanvas(small);
    return found ? scaleCorners(found.corners, w / dw, h / dh) : null;
};

/* Кадр пустой — камера «замолчала» (трек остановлен или без кадров, а события
   ended не было): чёрная страница не должна уйти объяснительной (разбор,
   23.10.2026). */
const frameIsBlank = (canvas) => {
    const probe = makeCanvas(24, 24);
    const ctx = probe.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(canvas, 0, 0, 24, 24);
    const { data } = ctx.getImageData(0, 0, 24, 24);
    freeCanvas(probe);
    let max = 0;
    for (let i = 0; i < data.length; i += 4) max = Math.max(max, data[i], data[i + 1], data[i + 2]);
    return max < 16;
};

const longestSide = (corners) => {
    let best = 1;
    for (let i = 0; i < 4; i += 1) {
        const a = corners[i];
        const b = corners[(i + 1) % 4];
        best = Math.max(best, Math.hypot(b[0] - a[0], b[1] - a[1]));
    }
    return best;
};

/* Страница целиком: снимок → выпрямление по углам → фильтр → поворот. Снимок
   перед выпрямлением уменьшается под размер страницы средствами браузера:
   выпрямление выбирает точки билинейно, и сжатие в разы им самим давало бы
   «лесенку» на буквах. */
const renderPage = async (page, filter, maxSide) => {
    const decoded = await decodeBlob(page.blob);
    try {
        const size = outputSize(page.corners, page.width, page.height, maxSide);
        const scale = Math.min(1, (Math.max(size.width, size.height) / longestSide(page.corners)) * 1.25,
            SOURCE_MAX_SIDE / Math.max(page.width, page.height));
        const sw = Math.max(1, Math.round(page.width * scale));
        const sh = Math.max(1, Math.round(page.height * scale));
        const source = makeCanvas(sw, sh);
        const sctx = source.getContext('2d', { willReadFrequently: true });
        sctx.imageSmoothingQuality = 'high';
        sctx.drawImage(decoded.image, 0, 0, sw, sh);
        const pixels = sctx.getImageData(0, 0, sw, sh).data;
        freeCanvas(source);
        const corners = insetCorners(scaleCorners(page.corners, sw / page.width, sh / page.height), size.width, size.height);
        const flat = warpPerspective(pixels, sw, sh, corners, size.width, size.height);
        enhancePage(flat, size.width, size.height, filter);
        const straight = makeCanvas(size.width, size.height);
        straight.getContext('2d').putImageData(new ImageData(flat, size.width, size.height), 0, 0);
        const turned = turnedSize(size.width, size.height, page.rotation);
        const { turn } = turned;
        if (!turn) return straight;
        const out = makeCanvas(turned.width, turned.height);
        const octx = out.getContext('2d');
        octx.translate(out.width / 2, out.height / 2);
        octx.rotate((turn * Math.PI) / 180);
        octx.drawImage(straight, -size.width / 2, -size.height / 2);
        freeCanvas(straight);
        return out;
    } finally {
        decoded.close();
    }
};

const previewKey = (page, filter) => `${page.id}:${filter}:${page.rotation}`;

const pagesWord = (n) => {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return `${n} страница`;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} страницы`;
    return `${n} страниц`;
};

let pageSeq = 0;

/* ── Края листа: снимок, рамка и углы ─────────────────────────────────── */

const CropEditor = ({ shot, onCorners }) => {
    const svgRef = useRef(null);
    const imgRef = useRef(null);
    const loupeRef = useRef(null);
    const [scale, setScale] = useState(0);
    const [drag, setDrag] = useState(null);
    const valid = cropUsable(shot.corners, shot.width, shot.height);

    useLayoutEffect(() => {
        const svg = svgRef.current;
        if (!svg) return undefined;
        const read = () => {
            const matrix = svg.getScreenCTM();
            if (matrix && matrix.a) setScale(matrix.a);
        };
        read();
        const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(read) : null;
        observer?.observe(svg);
        window.addEventListener('resize', read);
        return () => {
            observer?.disconnect();
            window.removeEventListener('resize', read);
        };
    }, [shot.width, shot.height]);

    const toImage = (event) => {
        const svg = svgRef.current;
        const matrix = svg?.getScreenCTM();
        if (!matrix) return null;
        const point = svg.createSVGPoint();
        point.x = event.clientX;
        point.y = event.clientY;
        const local = point.matrixTransform(matrix.inverse());
        return [local.x, local.y];
    };

    const begin = (index) => (event) => {
        const at = toImage(event);
        if (!at) return;
        event.preventDefault();
        event.stopPropagation();
        svgRef.current?.setPointerCapture?.(event.pointerId);
        const corner = shot.corners[index];
        setDrag({
            index,
            pointerId: event.pointerId,
            touch: event.pointerType !== 'mouse',
            offset: [corner[0] - at[0], corner[1] - at[1]],
            client: [event.clientX, event.clientY],
        });
    };

    const move = (event) => {
        if (!drag || event.pointerId !== drag.pointerId) return;
        const at = toImage(event);
        if (!at) return;
        const x = Math.min(shot.width, Math.max(0, at[0] + drag.offset[0]));
        const y = Math.min(shot.height, Math.max(0, at[1] + drag.offset[1]));
        onCorners(shot.corners.map((corner, i) => (i === drag.index ? [x, y] : corner)));
        setDrag((prev) => (prev ? { ...prev, client: [event.clientX, event.clientY] } : prev));
    };

    const end = (event) => {
        if (drag && event.pointerId === drag.pointerId) setDrag(null);
    };

    // Лупа: увеличенный кусок снимка вокруг угла, с перекрестьем.
    useEffect(() => {
        if (!drag?.touch || !scale) return;
        const canvas = loupeRef.current;
        const image = imgRef.current;
        if (!canvas || !image?.complete) return;
        const ratio = Math.min(window.devicePixelRatio || 1, 3);
        const side = Math.round(LOUPE_PX * ratio);
        if (canvas.width !== side) {
            canvas.width = side;
            canvas.height = side;
        }
        const ctx = canvas.getContext('2d');
        const [x, y] = shot.corners[drag.index];
        const span = LOUPE_PX / (LOUPE_ZOOM * scale);
        ctx.fillStyle = '#0f172a';
        ctx.fillRect(0, 0, side, side);
        ctx.drawImage(image, x - span / 2, y - span / 2, span, span, 0, 0, side, side);
        ctx.strokeStyle = 'rgba(59,130,246,0.95)';
        ctx.lineWidth = 1.5 * ratio;
        ctx.beginPath();
        ctx.moveTo(side / 2, side / 2 - 14 * ratio);
        ctx.lineTo(side / 2, side / 2 + 14 * ratio);
        ctx.moveTo(side / 2 - 14 * ratio, side / 2);
        ctx.lineTo(side / 2 + 14 * ratio, side / 2);
        ctx.stroke();
    }, [drag, scale, shot.corners]);

    const r = scale ? HANDLE_PX / scale : 0;
    const hit = scale ? HANDLE_HIT_PX / scale : 0;
    const path = shot.corners.map((p) => `${p[0]},${p[1]}`).join(' ');
    const outline = valid ? '#3b82f6' : '#f43f5e';
    /* Лупа — над УГЛОМ, а не над пальцем: угол может упереться в край снимка,
       пока палец едет дальше. Сверху места нет — сбоку от угла, по ту сторону
       от середины экрана, как в iOS: внизу её закрыли бы палец и ладонь. */
    let loupeStyle = null;
    const matrix = drag?.touch && typeof window !== 'undefined' ? svgRef.current?.getScreenCTM() : null;
    if (drag?.touch && typeof window !== 'undefined') {
        const [ix, iy] = shot.corners[drag.index];
        const sx = matrix ? matrix.a * ix + matrix.c * iy + matrix.e : drag.client[0];
        const sy = matrix ? matrix.b * ix + matrix.d * iy + matrix.f : drag.client[1];
        const gap = 44;
        let left = sx - LOUPE_PX / 2;
        let top = sy - LOUPE_PX - gap;
        if (top < 8) {
            top = Math.min(window.innerHeight - LOUPE_PX - 8, Math.max(8, sy - LOUPE_PX / 2));
            left = sx < window.innerWidth / 2 ? sx + gap : sx - LOUPE_PX - gap;
        }
        left = Math.min(window.innerWidth - LOUPE_PX - 8, Math.max(8, left));
        loupeStyle = { left, top, width: LOUPE_PX, height: LOUPE_PX };
    }

    return (
        <div className="relative h-full w-full">
            <img ref={imgRef} src={shot.url} alt="" draggable={false}
                 className="pointer-events-none absolute inset-0 h-full w-full select-none object-contain" />
            <svg ref={svgRef} viewBox={`0 0 ${shot.width} ${shot.height}`} preserveAspectRatio="xMidYMid meet"
                 className="absolute inset-0 h-full w-full touch-none select-none"
                 onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
                <path d={`M0 0H${shot.width}V${shot.height}H0Z M${shot.corners.map((p) => `${p[0]} ${p[1]}`).join(' L')}Z`}
                      fill="rgba(2,6,23,0.5)" fillRule="evenodd" />
                <polygon points={path} fill="none" stroke={outline} strokeWidth={2.5} strokeLinejoin="round"
                         vectorEffect="non-scaling-stroke" />
                {scale ? shot.corners.map((corner, index) => (
                    <g key={index} onPointerDown={begin(index)} className="cursor-grab">
                        <circle cx={corner[0]} cy={corner[1]} r={hit} fill="transparent" />
                        <circle cx={corner[0]} cy={corner[1]} r={drag?.index === index ? r * 1.25 : r}
                                fill="#ffffff" stroke={outline} strokeWidth={2.5} vectorEffect="non-scaling-stroke" />
                    </g>
                )) : null}
            </svg>
            {loupeStyle && (
                <canvas ref={loupeRef} aria-hidden="true" style={{ ...loupeStyle, position: 'fixed' }}
                        className="pointer-events-none z-10 rounded-full shadow-[0_10px_30px_rgba(0,0,0,0.45)] ring-2 ring-white" />
            )}
        </div>
    );
};

/* ── Окно сканера ─────────────────────────────────────────────────────── */

/* formats — что примет поле (scanFormats): несколько страниц только в PDF.
   onDone(file) — готовый файл; onClose — выйти без файла; returnFocus — куда
   вернуть фокус после окна (кнопка, которая его открыла: пункт меню или листа,
   на котором был фокус, к этому времени уже снят). */
const DocumentScanner = ({ formats, onClose, onDone, returnFocus = null }) => {
    const mobile = useIsMobileShell();
    const multi = Boolean(formats?.pdf);
    const [step, setStep] = useState('camera');
    const [pages, setPages] = useState([]);
    const [current, setCurrent] = useState(0);
    const [shot, setShot] = useState(null);
    const [filter, setFilter] = useState(SCAN_DEFAULT_FILTER);
    const [camera, setCamera] = useState({ state: 'idle', message: '' });
    const [slow, setSlow] = useState(false);
    const [live, setLive] = useState(null);
    const [torch, setTorch] = useState(null);
    const [busy, setBusy] = useState('');
    const [error, setError] = useState('');
    const [confirmDiscard, setConfirmDiscard] = useState(false);
    const [previews, setPreviews] = useState({});

    const rootRef = useRef(null);
    const videoRef = useRef(null);
    const streamRef = useRef(null);
    const startingRef = useRef(false);
    const aliveRef = useRef(true);
    const stepRef = useRef(step);
    stepRef.current = step;
    const trackerRef = useRef(createQuadTracker());
    const fileRef = useRef(null);
    const galleryRef = useRef(null);
    const flashRef = useRef(null);
    const urlsRef = useRef(new Set());

    const makeUrl = (blob) => {
        const url = URL.createObjectURL(blob);
        urlsRef.current.add(url);
        return url;
    };

    /* ── камера ── */

    const stopCamera = useCallback(() => {
        const stream = streamRef.current;
        streamRef.current = null;
        stream?.getTracks().forEach((track) => track.stop());
        if (videoRef.current) videoRef.current.srcObject = null;
        trackerRef.current.reset();
        setLive(null);
        setTorch(null);
        setCamera((prev) => (prev.state === 'error' ? prev : { state: 'idle', message: '' }));
    }, []);

    const startCamera = useCallback(async () => {
        if (streamRef.current || startingRef.current) return;
        if (!navigator.mediaDevices?.getUserMedia) {
            setCamera({ state: 'error', message: 'Камера в этом браузере недоступна' });
            return;
        }
        startingRef.current = true;
        setCamera({ state: 'starting', message: '' });
        try {
            let stream;
            try {
                stream = await navigator.mediaDevices.getUserMedia({ video: CAMERA_CONSTRAINTS, audio: false });
            } catch (err) {
                if (err?.name !== 'OverconstrainedError') throw err;
                stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
            }
            if (!aliveRef.current || stepRef.current === 'review' || !videoRef.current) {
                stream.getTracks().forEach((track) => track.stop());
                return;
            }
            streamRef.current = stream;
            const video = videoRef.current;
            video.srcObject = stream;
            await video.play();
            const track = stream.getVideoTracks()[0];
            track?.addEventListener?.('ended', () => {
                if (streamRef.current !== stream) return;
                stopCamera();
                setCamera({ state: 'error', message: 'Камера отключилась' });
            });
            // Кадры перестали идти (звонок, камеру забрало другое приложение) —
            // спуск недоступен, пока они не вернутся.
            track?.addEventListener?.('mute', () => {
                if (streamRef.current === stream) setCamera((prev) => (prev.state === 'live' ? { state: 'paused', message: '' } : prev));
            });
            track?.addEventListener?.('unmute', () => {
                if (streamRef.current === stream) setCamera((prev) => (prev.state === 'paused' ? { state: 'live', message: '' } : prev));
            });
            setTorch(track?.getCapabilities?.().torch ? false : null);
            setCamera({ state: 'live', message: '' });
        } catch (err) {
            stopCamera();
            const name = err?.name;
            let message = 'Камера не включилась';
            if (name === 'NotAllowedError' || name === 'SecurityError') message = 'Нет доступа к камере — разрешите её для портала в настройках браузера';
            else if (name === 'NotFoundError' || name === 'OverconstrainedError') message = 'Камера не найдена';
            else if (name === 'NotReadableError') message = 'Камера занята другим приложением';
            if (aliveRef.current) setCamera({ state: 'error', message });
        } finally {
            startingRef.current = false;
        }
    }, [stopCamera]);

    useEffect(() => {
        aliveRef.current = true;
        const urls = urlsRef.current;
        return () => {
            aliveRef.current = false;
            const stream = streamRef.current;
            streamRef.current = null;
            stream?.getTracks().forEach((track) => track.stop());
            urls.forEach((url) => URL.revokeObjectURL(url));
            urls.clear();
        };
    }, []);

    // Камера живёт на шагах «камера» и «края» (переснять — сразу), на просмотре
    // страниц гаснет: незачем греть телефон и жечь индикатор.
    useEffect(() => {
        if (step === 'review') { stopCamera(); return; }
        if (step !== 'camera') return;
        // Спрятанное видео Safari ставит на паузу — после «Переснять» запускаем снова.
        if (streamRef.current) videoRef.current?.play?.()?.catch?.(() => {});
        else startCamera();
    }, [step, startCamera, stopCamera]);

    // Вернулись в портал после блокировки экрана — iOS к этому времени камеру глушит.
    useEffect(() => {
        const onVisible = () => {
            if (document.visibilityState !== 'visible' || stepRef.current !== 'camera') return;
            const track = streamRef.current?.getVideoTracks?.()[0];
            if (!track || track.readyState === 'ended') {
                stopCamera();
                startCamera();
            }
        };
        document.addEventListener('visibilitychange', onVisible);
        return () => document.removeEventListener('visibilitychange', onVisible);
    }, [startCamera, stopCamera]);

    useEffect(() => {
        setSlow(false);
        if (camera.state !== 'starting') return undefined;
        const timer = setTimeout(() => setSlow(true), SLOW_START_MS);
        return () => clearTimeout(timer);
    }, [camera.state]);

    // Живой поиск листа в видоискателе.
    useEffect(() => {
        if (step !== 'camera' || camera.state !== 'live') return undefined;
        const canvas = makeCanvas(1, 1);
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        trackerRef.current.reset();
        let timer = 0;
        let stopped = false;
        const tick = () => {
            if (stopped) return;
            const video = videoRef.current;
            const started = performance.now();
            if (!video || video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
                timer = setTimeout(tick, DETECT_INTERVAL_MS);
                return;
            }
            try {
                const vw = video.videoWidth;
                const vh = video.videoHeight;
                const scale = DETECT_SIDE / Math.max(vw, vh);
                const dw = Math.max(1, Math.round(vw * scale));
                const dh = Math.max(1, Math.round(vh * scale));
                if (canvas.width !== dw || canvas.height !== dh) {
                    canvas.width = dw;
                    canvas.height = dh;
                }
                ctx.drawImage(video, 0, 0, dw, dh);
                const found = detectDocument(toGray(ctx.getImageData(0, 0, dw, dh).data, dw, dh), dw, dh);
                const shown = trackerRef.current.push(found ? found.corners : null, dw, dh);
                setLive(shown ? { corners: scaleCorners(shown, vw / dw, vh / dh), w: vw, h: vh } : null);
            } catch {
                /* битый кадр — ждём следующий */
            }
            const spent = performance.now() - started;
            timer = setTimeout(tick, Math.max(DETECT_INTERVAL_MS, spent * DETECT_BUDGET));
        };
        timer = setTimeout(tick, DETECT_INTERVAL_MS);
        return () => {
            stopped = true;
            clearTimeout(timer);
            freeCanvas(canvas);
        };
    }, [step, camera.state]);

    const toggleTorch = async () => {
        const track = streamRef.current?.getVideoTracks?.()[0];
        if (!track || torch === null) return;
        try {
            await track.applyConstraints({ advanced: [{ torch: !torch }] });
            setTorch(!torch);
        } catch {
            setTorch(null);
        }
    };

    /* ── снимок ── */

    const openCrop = (next) => {
        setShot(next);
        setError('');
        setStep('crop');
    };

    const flash = () => {
        if (prefersReducedMotion()) return;
        flashRef.current?.animate?.([{ opacity: 0.85 }, { opacity: 0 }], { duration: 240, easing: 'ease-out' });
    };

    const capture = async () => {
        const video = videoRef.current;
        if (busy || !video?.videoWidth || camera.state !== 'live') return;
        const track = streamRef.current?.getVideoTracks?.()[0];
        if (!track || track.readyState !== 'live' || track.muted) {
            // Трек встал без события (stop() его не шлёт): карточка ошибки с
            // «Повторить» и запасным путём, а не чёрная страница.
            stopCamera();
            setCamera({ state: 'error', message: 'Камера не даёт кадр' });
            return;
        }
        setBusy('capture');
        try {
            const vw = video.videoWidth;
            const vh = video.videoHeight;
            const frame = makeCanvas(vw, vh);
            frame.getContext('2d').drawImage(video, 0, 0, vw, vh);
            if (frameIsBlank(frame)) {
                freeCanvas(frame);
                setError('Кадр пустой — камера не даёт изображения');
                return;
            }
            try { navigator.vibrate?.(15); } catch { /* вибрации может не быть */ }
            flash();
            const liveCorners = live && live.w === vw && live.h === vh ? live.corners : null;
            const corners = findCorners(frame, vw, vh) || liveCorners || defaultCorners(vw, vh);
            const blob = await canvasBlob(frame, 'image/jpeg', 0.92);
            freeCanvas(frame);
            openCrop({ blob, url: makeUrl(blob), width: vw, height: vh, corners, editing: null });
        } catch {
            setError('Снимок не получился — попробуйте ещё раз');
        } finally {
            if (aliveRef.current) setBusy('');
        }
    };

    const importPhoto = async (file) => {
        if (!file) return;
        setBusy('capture');
        try {
            const url = URL.createObjectURL(file);
            const image = await loadImage(url, true);
            const scale = Math.min(1, IMPORT_MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
            const w = Math.max(1, Math.round(image.naturalWidth * scale));
            const h = Math.max(1, Math.round(image.naturalHeight * scale));
            const canvas = makeCanvas(w, h);
            const ctx = canvas.getContext('2d');
            ctx.imageSmoothingQuality = 'high';
            ctx.drawImage(image, 0, 0, w, h);
            const corners = findCorners(canvas, w, h) || defaultCorners(w, h);
            const blob = await canvasBlob(canvas, 'image/jpeg', 0.92);
            freeCanvas(canvas);
            openCrop({ blob, url: makeUrl(blob), width: w, height: h, corners, editing: null });
        } catch {
            setError('Фото не открылось — выберите снимок JPEG или PNG');
        } finally {
            if (aliveRef.current) setBusy('');
        }
    };

    /* ── края ── */

    const keepShot = () => {
        if (!shot || !cropUsable(shot.corners, shot.width, shot.height)) return;
        pageSeq += 1;
        const next = keepScannedPage(pages, shot, `p${pageSeq}`);
        setPages(next.pages);
        setCurrent(next.current);
        setShot(null);
        setStep('review');
    };

    const leaveCrop = () => {
        const editing = shot?.editing ?? null;
        setShot(null);
        setStep(editing !== null ? 'review' : 'camera');
    };

    /* ── страницы ── */

    const page = pages[current] || null;

    useEffect(() => {
        if (step !== 'review' || !pages.length) return undefined;
        let cancelled = false;
        const order = [pages[current], ...pages.filter((_, index) => index !== current)].filter(Boolean);
        (async () => {
            for (const item of order) {
                const key = previewKey(item, filter);
                if (cancelled) return;
                if (previews[key]) continue;
                await nextFrame();
                if (cancelled) return;
                try {
                    const canvas = await renderPage(item, filter, PREVIEW_SIDE);
                    const blob = await canvasBlob(canvas, 'image/jpeg', 0.85);
                    freeCanvas(canvas);
                    if (cancelled) return;
                    const url = makeUrl(blob);
                    setPreviews((prev) => ({ ...prev, [key]: url }));
                } catch {
                    if (!cancelled) setError('Страница не обработалась — снимите её ещё раз');
                }
            }
        })();
        return () => { cancelled = true; };
    }, [step, pages, current, filter]); // eslint-disable-line react-hooks/exhaustive-deps

    const rotatePage = () => {
        setPages((prev) => prev.map((item, index) => (index === current ? { ...item, rotation: (item.rotation + 90) % 360 } : item)));
    };

    const deletePage = () => {
        const next = pages.filter((_, index) => index !== current);
        setPages(next);
        setCurrent((index) => Math.max(0, Math.min(index, next.length - 1)));
        if (!next.length) setStep('camera');
    };

    const recropPage = () => {
        if (!page) return;
        openCrop({ blob: page.blob, url: page.url, width: page.width, height: page.height, corners: page.corners, editing: current });
    };

    const addPage = () => {
        setError('');
        setStep('camera');
    };

    const save = async () => {
        if (!pages.length || busy) return;
        setBusy('save');
        setError('');
        await nextFrame();
        try {
            let encoded = [];
            for (const { quality, maxSide } of SCAN_QUALITY_STEPS) {
                encoded = [];
                for (const item of pages) {
                    const canvas = await renderPage(item, filter, maxSide);
                    const blob = await canvasBlob(canvas, 'image/jpeg', quality);
                    encoded.push({ blob, width: canvas.width, height: canvas.height });
                    freeCanvas(canvas);
                    await nextFrame();
                }
                if (encoded.reduce((sum, item) => sum + item.blob.size, 0) <= SCAN_TARGET_BYTES) break;
            }
            const now = new Date();
            let file;
            if (scanOutputType(encoded.length, formats) === 'jpeg') {
                file = new File([encoded[0].blob], scanFileName(now, 'jpg'), { type: 'image/jpeg' });
            } else {
                const parts = await Promise.all(encoded.map(async (item) => ({
                    jpeg: new Uint8Array(await item.blob.arrayBuffer()), width: item.width, height: item.height,
                })));
                file = new File([buildPdf(parts)], scanFileName(now, 'pdf'), { type: 'application/pdf' });
            }
            if (!aliveRef.current) return;
            onDone(file);
        } catch {
            if (aliveRef.current) {
                setError('Скан не собрался — попробуйте ещё раз');
                setBusy('');
            }
        }
    };

    /* ── выход, «назад», Esc ── */

    // false — окно остаётся (шаг назад внутри), иначе — закрыто.
    const goBack = () => {
        const action = scannerBack({ step, pages: pages.length, busy, confirm: confirmDiscard });
        if (action === 'stay') return false;
        if (action === 'close-confirm') { setConfirmDiscard(false); return false; }
        if (action === 'leave-crop') { leaveCrop(); return false; }
        if (action === 'to-review') { setStep('review'); return false; }
        if (action === 'confirm') { setConfirmDiscard(true); return false; }
        onClose();
        return true;
    };
    useScreenBackGesture(mobile, goBack);

    const goBackRef = useRef(goBack);
    goBackRef.current = goBack;
    useEffect(() => {
        const onKey = (event) => {
            // Tab ходит по кругу внутри окна: иначе фокус уходил бы на кнопки
            // раздела под ним, невидимые за сканером.
            if (event.key === 'Tab') {
                const root = rootRef.current;
                const items = root ? [...root.querySelectorAll('button:not([disabled])')].filter((el) => el.offsetParent !== null) : [];
                if (!items.length) return;
                const at = items.indexOf(document.activeElement);
                event.preventDefault();
                const next = event.shiftKey ? items[at <= 0 ? items.length - 1 : at - 1] : items[at < 0 || at === items.length - 1 ? 0 : at + 1];
                next.focus();
                return;
            }
            if (event.key !== 'Escape') return;
            event.preventDefault();
            event.stopImmediatePropagation();
            // Закреплённая щелчком подсказка «i» закрывается первым Esc (она
            // гаснет при потере фокуса), окно — следующим. Подсказку под мышью
            // не трогаем: она погаснет сама, а Esc не должен «залипать».
            const hint = rootRef.current?.querySelector('button[aria-expanded="true"]');
            if (hint && !hint.parentElement?.matches(':hover')) {
                hint.focus({ preventScroll: true });
                hint.blur();
                return;
            }
            goBackRef.current();
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, []);

    useEffect(() => holdPageScroll(true), []);

    const returnFocusRef = useRef(returnFocus);
    returnFocusRef.current = returnFocus;
    useEffect(() => {
        const opener = document.activeElement;
        rootRef.current?.focus({ preventScroll: true });
        return () => {
            if (returnFocusRef.current) returnFocusRef.current();
            else if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus({ preventScroll: true });
        };
    }, []);

    /* ── разметка ── */

    const dark = step !== 'review';
    const shell = mobile
        ? `fixed inset-0 z-[10050] flex flex-col outline-none motion-safe:animate-fade-in-soft ${dark ? 'bg-black text-white' : 'bg-slate-50 text-slate-900'}`
        : 'fixed inset-0 z-[10050] flex items-center justify-center bg-slate-900/70 p-6 outline-none backdrop-blur-sm motion-safe:animate-window-dim-in';
    const panel = mobile ? 'contents'
        // overflow-y-auto, а не hidden: подсказка «i» у фильтров меряет место до
        // ближайшего прокручиваемого предка и с hidden раскрывалась вниз, за край
        // окна (разбор, 23.10.2026). Прокрутки не бывает — всё помещается.
        : `relative flex h-[min(88vh,820px)] w-[min(980px,94vw)] flex-col overflow-y-auto rounded-[28px] shadow-2xl ring-1 ring-white/10 motion-safe:animate-window-in ${dark ? 'bg-black text-white' : 'bg-slate-50 text-slate-900'}`;
    const topPad = { paddingTop: 'max(12px, env(safe-area-inset-top))' };
    const bottomPad = { paddingBottom: 'max(18px, env(safe-area-inset-bottom))' };
    const fallbackLabel = mobile ? 'Снять камерой телефона' : 'Выбрать фото';
    const darkButton = 'rounded-full px-3 py-1.5 text-[15px] font-medium text-white/90 transition hover:bg-white/10 active:opacity-60 disabled:opacity-40';
    const lastPreview = pages.length ? previews[previewKey(pages[pages.length - 1], filter)] || pages[pages.length - 1].url : null;
    const currentPreview = page ? previews[previewKey(page, filter)] : null;
    const cropValid = shot ? cropUsable(shot.corners, shot.width, shot.height) : false;

    const cameraStep = (
        <>
            <div className="flex items-center justify-between gap-3 px-3 pb-2" style={mobile ? topPad : { paddingTop: 14 }}>
                <button type="button" className={darkButton} onClick={goBack}>{pages.length ? 'Назад' : 'Отмена'}</button>
                <span className="truncate text-[15px] font-semibold">Скан документа</span>
                {torch !== null ? (
                    <button type="button" onClick={toggleTorch} aria-label={torch ? 'Выключить фонарик' : 'Включить фонарик'}
                            className={`grid h-9 w-9 place-items-center rounded-full transition ${torch ? 'bg-amber-300 text-[#0f172a]' : 'bg-white/10 text-white hover:bg-white/20'}`}>
                        {torch ? <Flashlight size={17} /> : <FlashlightOff size={17} />}
                    </button>
                ) : <span className="w-[68px]" aria-hidden="true" />}
            </div>
            <div className="relative min-h-0 flex-1 overflow-hidden">
                <video ref={videoRef} playsInline muted autoPlay aria-label="Камера"
                       className={`absolute inset-0 h-full w-full object-contain ${camera.state === 'live' ? '' : 'opacity-0'}`} />
                {live && camera.state === 'live' && (
                    <svg viewBox={`0 0 ${live.w} ${live.h}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true"
                         className="pointer-events-none absolute inset-0 h-full w-full">
                        <polygon points={live.corners.map((p) => `${p[0]},${p[1]}`).join(' ')} fill="rgba(59,130,246,0.2)"
                                 stroke="#3b82f6" strokeWidth={3} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
                    </svg>
                )}
                {camera.state === 'live' && !live && (
                    <div className="pointer-events-none absolute inset-x-0 top-3 flex justify-center">
                        <span className="rounded-full bg-black/55 px-3.5 py-1.5 text-[13px] text-white/90 backdrop-blur">
                            Наведите камеру на лист
                        </span>
                    </div>
                )}
                {(camera.state === 'starting' || camera.state === 'idle') && (
                    <div className="absolute inset-0 grid place-items-center px-6 text-center">
                        <div className="flex flex-col items-center gap-3">
                            <Loader2 size={26} className="animate-spin text-white/70" />
                            <span className="text-[14px] text-white/80">Включаю камеру…</span>
                            {/* На компьютере выход «выбрать фото» и так есть значком
                                внизу — вторая такая же кнопка была бы шумом. */}
                            {slow && mobile && (
                                <button type="button" className={`${darkButton} mt-1 bg-white/10`} onClick={() => fileRef.current?.click()}>
                                    {fallbackLabel}
                                </button>
                            )}
                        </div>
                    </div>
                )}
                {camera.state === 'paused' && (
                    <div className="absolute inset-0 grid place-items-center px-6 text-center">
                        <div className="flex flex-col items-center gap-3">
                            <span className="text-[14px] text-white/85">Камера приостановлена</span>
                            <button type="button" className={`${darkButton} bg-white/10`} onClick={() => { stopCamera(); startCamera(); }}>
                                Включить снова
                            </button>
                        </div>
                    </div>
                )}
                {camera.state === 'error' && (
                    <div className="absolute inset-0 grid place-items-center px-6 text-center">
                        <div className="flex max-w-xs flex-col items-center gap-3">
                            <span className="grid h-12 w-12 place-items-center rounded-2xl bg-white/10 text-white/80"><CameraOff size={22} /></span>
                            <span className="text-[14px] leading-snug text-white/85">{camera.message}</span>
                            {/* Нижняя панель (спуск, фото из галереи) на время ошибки
                                спрятана: все выходы — здесь, по одному разу. */}
                            <button type="button" className="mt-1 rounded-full bg-[#fff] px-4 py-2 text-[15px] font-semibold text-[#0f172a] transition active:opacity-70"
                                    onClick={() => fileRef.current?.click()}>
                                {fallbackLabel}
                            </button>
                            <div className="flex flex-wrap justify-center gap-1">
                                {mobile && (
                                    <button type="button" className={darkButton} onClick={() => galleryRef.current?.click()}>
                                        Выбрать фото
                                    </button>
                                )}
                                <button type="button" className={darkButton} onClick={() => { stopCamera(); setCamera({ state: 'idle', message: '' }); startCamera(); }}>
                                    Повторить
                                </button>
                            </div>
                        </div>
                    </div>
                )}
                {error && (
                    <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center px-4">
                        <span className="rounded-full bg-rose-600/90 px-3.5 py-1.5 text-[13px] text-white">{error}</span>
                    </div>
                )}
                {/* Белое на тёмных шагах — не утилитой bg-white: её перекрашивает
                    тёмный слой портала (theme-dark.css), а камера тёмная всегда. */}
                <div ref={flashRef} className="pointer-events-none absolute inset-0 bg-[#fff] opacity-0" aria-hidden="true" />
            </div>
            <div className={`grid grid-cols-3 items-center px-6 pt-4 ${camera.state === 'error' ? 'invisible' : ''}`}
                 style={mobile ? bottomPad : { paddingBottom: 20 }} aria-hidden={camera.state === 'error' || undefined}>
                <div className="justify-self-start">
                    {lastPreview && (
                        <button type="button" onClick={() => setStep('review')} aria-label={`Снятые страницы: ${pages.length}`}
                                className="relative block h-14 w-11 overflow-hidden rounded-md bg-[#fff] ring-2 ring-white/80">
                            <img src={lastPreview} alt="" className="h-full w-full object-cover" />
                            <span className="absolute right-0 top-0 grid h-5 min-w-[20px] place-items-center rounded-bl-md bg-blue-600 px-1 text-[11px] font-semibold text-white">
                                {pages.length}
                            </span>
                        </button>
                    )}
                </div>
                <button type="button" onClick={capture} disabled={camera.state !== 'live' || Boolean(busy)} aria-label="Снять страницу"
                        className="grid h-[74px] w-[74px] place-items-center justify-self-center rounded-full border-[4px] border-white/90 transition disabled:opacity-40">
                    <span className={`block h-[58px] w-[58px] rounded-full bg-[#fff] transition active:scale-90 ${busy === 'capture' ? 'scale-90 opacity-70' : ''}`} />
                </button>
                <div className="justify-self-end">
                    <button type="button" onClick={() => galleryRef.current?.click()} aria-label="Выбрать фото документа" disabled={Boolean(busy)}
                            className="grid h-11 w-11 place-items-center rounded-full bg-white/10 text-white transition hover:bg-white/20 disabled:opacity-40">
                        <ImagePlus size={19} />
                    </button>
                </div>
            </div>
        </>
    );

    const cropStep = shot && (
        <>
            <div className="flex items-center justify-center px-3 pb-2" style={mobile ? topPad : { paddingTop: 14 }}>
                <div className="text-center">
                    <div className="text-[15px] font-semibold">Края листа</div>
                    <div className={`text-[12.5px] ${cropValid ? 'text-white/60' : 'text-rose-300'}`}>
                        {cropValid ? 'Перетащите углы к краям листа' : 'Углы перепутаны — поправьте рамку'}
                    </div>
                </div>
            </div>
            <div className="relative min-h-0 flex-1 px-4 py-3">
                <CropEditor shot={shot} onCorners={(corners) => setShot((prev) => (prev ? { ...prev, corners } : prev))} />
            </div>
            <div className="flex items-center justify-between gap-3 px-5 pt-3" style={mobile ? bottomPad : { paddingBottom: 20 }}>
                <button type="button" className={darkButton} onClick={leaveCrop}>
                    {shot.editing !== null ? 'Отмена' : 'Переснять'}
                </button>
                <button type="button" onClick={keepShot} disabled={!cropValid}
                        className="rounded-full bg-[#fff] px-5 py-2 text-[15px] font-semibold text-[#0f172a] transition active:opacity-70 disabled:opacity-40">
                    {shot.editing !== null ? 'Готово' : 'Оставить'}
                </button>
            </div>
        </>
    );

    const tool = 'inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[13px] font-medium transition active:scale-[0.98] disabled:opacity-40';
    const reviewStep = (
        <>
            <div className="flex items-center gap-3 border-b border-slate-200/70 bg-white/80 px-3 pb-2.5"
                 style={mobile ? topPad : { paddingTop: 12 }}>
                <button type="button" onClick={goBack} disabled={busy === 'save'}
                        className="rounded-full px-3 py-1.5 text-[15px] font-medium text-blue-600 transition hover:bg-blue-50 active:opacity-60 disabled:opacity-40">
                    Отмена
                </button>
                <div className="min-w-0 flex-1 text-center">
                    <div className="truncate text-[15px] font-semibold text-slate-900">Скан документа</div>
                    <div className="text-[12px] text-slate-500">{pagesWord(pages.length)}</div>
                </div>
                <span className="w-[76px]" aria-hidden="true" />
            </div>
            <div className="relative flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-4 py-4">
                <div className="relative flex min-h-0 w-full flex-1 items-center justify-center">
                    {currentPreview ? (
                        <img src={currentPreview} alt={`Страница ${current + 1}`}
                             className="max-h-full max-w-full rounded-[6px] bg-white object-contain shadow-[0_10px_30px_rgba(15,23,42,0.16)] ring-1 ring-slate-900/5" />
                    ) : (
                        <div className="grid h-full max-h-[420px] w-full max-w-[300px] place-items-center rounded-[6px] bg-white shadow-[0_10px_30px_rgba(15,23,42,0.12)]">
                            <Loader2 size={24} className="animate-spin text-slate-400" />
                        </div>
                    )}
                </div>
                {pages.length > 1 && (
                    <div className="thin-scroll flex max-w-full gap-2 overflow-x-auto px-1 py-1">
                        {pages.map((item, index) => {
                            const thumb = previews[previewKey(item, filter)] || item.url;
                            const selected = index === current;
                            return (
                                <button key={item.id} type="button" onClick={() => setCurrent(index)}
                                        aria-label={`Страница ${index + 1}`} aria-pressed={selected}
                                        className={`relative h-16 w-12 shrink-0 overflow-hidden rounded-md bg-white transition ${selected ? 'ring-2 ring-blue-500 ring-offset-2 ring-offset-blue-50' : 'ring-1 ring-slate-200 hover:ring-slate-300'}`}>
                                    <img src={thumb} alt="" className="h-full w-full object-cover" />
                                    <span className="absolute bottom-0 right-0 rounded-tl-md bg-slate-900/60 px-1 text-[10px] font-semibold text-white">{index + 1}</span>
                                </button>
                            );
                        })}
                    </div>
                )}
                <div className="flex flex-wrap items-center justify-center gap-1">
                    <button type="button" className={`${tool} text-slate-600 hover:bg-slate-200/70`} onClick={rotatePage} disabled={!page || Boolean(busy)}>
                        <RotateCw size={15} />
                        Повернуть
                    </button>
                    <button type="button" className={`${tool} text-slate-600 hover:bg-slate-200/70`} onClick={recropPage} disabled={!page || Boolean(busy)}>
                        <Crop size={15} />
                        Края
                    </button>
                    <button type="button" className={`${tool} text-rose-600 hover:bg-rose-50`} onClick={deletePage} disabled={!page || Boolean(busy)}>
                        <Trash2 size={15} />
                        Удалить
                    </button>
                </div>
            </div>
            <div className="space-y-3 border-t border-slate-200/70 bg-white px-4 pt-3" style={mobile ? bottomPad : { paddingBottom: 16 }}>
                <div className="flex items-center justify-center gap-1.5">
                    <IosSegmented value={filter} options={SCAN_FILTERS} onChange={setFilter} slide ariaLabel="Вид скана" />
                    <IosHint text={SCAN_FILTER_HINT} />
                </div>
                {error && <div className="text-center text-[12.5px] font-medium text-rose-600">{error}</div>}
                <div className="flex gap-2">
                    {multi && pages.length < SCAN_MAX_PAGES && (
                        <button type="button" className={`${iosBtnSecondary} ${mobile ? 'flex-1' : ''}`} onClick={addPage} disabled={Boolean(busy)}>
                            <Plus size={16} />
                            Ещё страница
                        </button>
                    )}
                    <button type="button" className={`${iosBtnPrimary} flex-1`} onClick={save} disabled={!pages.length || Boolean(busy)}>
                        {busy === 'save' ? <Loader2 size={16} className="animate-spin" /> : null}
                        {busy === 'save' ? 'Готовлю файл…' : 'Приложить'}
                    </button>
                </div>
            </div>
        </>
    );

    let body = null;
    if (step === 'crop' && shot) body = cropStep;
    else if (step === 'review') body = reviewStep;

    return createPortal(
        <div ref={rootRef} role="dialog" aria-modal="true" aria-label="Скан документа" tabIndex={-1}
             className={shell} style={{ fontFamily: APPLE_FONT }}>
            <div className={panel}>
                {/* Видео держится в дереве и на шаге «края»: переснять — без нового
                    включения камеры. Прячется вместе со своим шагом. */}
                <div className={body ? 'hidden' : 'contents'}>{cameraStep}</div>
                {body}
                {confirmDiscard && (
                    <div className="absolute inset-0 z-20 grid place-items-center bg-slate-900/45 p-6 motion-safe:animate-fade-in-soft">
                        <div className="w-full max-w-[300px] rounded-2xl bg-white p-5 text-center text-slate-900 shadow-2xl">
                            <div className="text-[16px] font-semibold">Удалить скан?</div>
                            <div className="mt-1 text-[13px] text-slate-500">Снятые страницы не сохранятся.</div>
                            <div className="mt-4 grid gap-2">
                                <button type="button" onClick={onClose}
                                        className="rounded-xl bg-rose-600 px-4 py-2.5 text-[14px] font-semibold text-white transition hover:bg-rose-700 active:scale-[0.98]">
                                    Удалить
                                </button>
                                <button type="button" className={iosBtnSecondary} onClick={() => setConfirmDiscard(false)}>
                                    Продолжить
                                </button>
                            </div>
                        </div>
                    </div>
                )}
            </div>
            {/* Запасной путь: снимок системной камерой (на телефоне) и фото из
                галереи. Разрешения на камеру для них не нужно. */}
            <input ref={fileRef} type="file" accept="image/*" className="hidden" capture={mobile ? 'environment' : undefined}
                   onChange={(event) => {
                       const file = event.target.files?.[0];
                       event.target.value = '';
                       importPhoto(file);
                   }} />
            <input ref={galleryRef} type="file" accept="image/*" className="hidden"
                   onChange={(event) => {
                       const file = event.target.files?.[0];
                       event.target.value = '';
                       importPhoto(file);
                   }} />
        </div>,
        document.body,
    );
};

export default DocumentScanner;
