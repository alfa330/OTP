import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CameraOff, Check, ChevronRight, Flashlight, FlashlightOff, ImagePlus, Loader2, ScanBarcode } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import { SHARPEN_PASSES, scanGray, scanStill, toGray } from './barcodeScan';
import { errorText, placeText, shortName } from './fixedAssetsMeta';
import { FIELD, FIELD_INVALID, Thumb } from './fixedAssetsUi';

/*
 * Сканер наклейки ОС — камера портала (getUserMedia), как сканер документов «Дисциплин».
 * Штрихкод читает распознавание браузера (BarcodeDetector: Android, macOS), а где его нет
 * (Windows, iPhone) — своё (barcodeScan.js: EAN-13, EAN-8, Code 128).
 *
 * Без камеры тоже работает: «Фото наклейки» (снимок из файла или системной камерой телефона —
 * разрешение не нужно) и поле номера, в которое печатает и USB-сканер бухгалтерии (он вводит код
 * и Enter, как клавиатура).
 *
 * onCode(text, more) — прочитанный код; more — другие коды с того же фото (родитель пробует их, если
 * первого в учёте нет). Пока родитель ищет и показывает ответ (children), сканер стоит на паузе
 * (paused); тот же код сразу после «Сканировать ещё» не повторяется.
 */
const FORMATS = ['ean_13', 'ean_8', 'code_128', 'upc_a', 'upc_e', 'code_39', 'itf', 'qr_code'];
const GUIDE = { width: 0.8, height: 0.34 };        // рамка — доля видимого кадра
const TICK_MS = 110;
const REPEAT_MS = 2500;
const FRAME_WIDTH = 1600;                           // шире кадр не читаем: дольше, а точнее не станет
const STILL_WIDTHS = [1600, 2800];
const FOUND_HOLD_MS = 650;                          // «нашлось» видно, прежде чем откроется карточка

const darkButton = 'rounded-full px-3 py-1.5 text-[15px] font-medium text-[rgba(255,255,255,0.92)] transition hover:bg-[rgba(255,255,255,0.1)] active:opacity-60 disabled:opacity-40';
const darkIcon = 'grid h-9 w-9 place-items-center rounded-full bg-[rgba(255,255,255,0.1)] text-[#fff] transition hover:bg-[rgba(255,255,255,0.2)] disabled:opacity-40';

let detectorPromise = null;
const nativeDetector = () => {
    if (!detectorPromise) {
        detectorPromise = (async () => {
            try {
                if (typeof window === 'undefined' || !('BarcodeDetector' in window)) return null;
                const supported = (await window.BarcodeDetector.getSupportedFormats?.()) || [];
                const formats = FORMATS.filter((format) => supported.includes(format));
                return formats.length ? new window.BarcodeDetector({ formats }) : null;
            } catch {
                return null;
            }
        })();
    }
    return detectorPromise;
};

const cameraMessage = (error) => {
    const name = error?.name;
    if (name === 'NotAllowedError' || name === 'SecurityError') return 'Нет доступа к камере — разрешите её для портала в настройках браузера';
    if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'Камера не найдена';
    if (name === 'NotReadableError') return 'Камера занята другим приложением';
    return 'Камера не включилась';
};

/* Та часть кадра, что видна в рамке (видео растянуто object-cover), с запасом по краям. */
const guideCrop = (video, box) => {
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    const bw = box?.clientWidth || vw;
    const bh = box?.clientHeight || vh;
    const scale = Math.max(bw / vw, bh / vh);
    const w = Math.min(vw, (bw / scale) * GUIDE.width * 1.12);
    const h = Math.min(vh, (bh / scale) * GUIDE.height * 1.3);
    return { x: (vw - w) / 2, y: (vh - h) / 2, w, h };
};

/* Кадр в рамке → серый снимок для своего распознавания. */
const grabFrame = (video, box, canvas, context) => {
    const crop = guideCrop(video, box);
    const scale = Math.min(1, FRAME_WIDTH / crop.w);
    const width = Math.max(1, Math.round(crop.w * scale));
    const height = Math.max(1, Math.round(crop.h * scale));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    context.drawImage(video, crop.x, crop.y, crop.w, crop.h, 0, 0, width, height);
    return { gray: toGray(context.getImageData(0, 0, width, height).data, width, height), width, height };
};

/* Все коды в рамке, чаще прочитанные первыми: наклейку в рамке пересекает больше линий. Резкость —
   одним проходом на кадр, по кругу: кадры идут потоком, а четыре прохода разом удвоили бы работу. */
const frameOptions = (frame) => ({
    lines: 15, band: [0.1, 0.9], all: true, sharpen: [SHARPEN_PASSES[frame % SHARPEN_PASSES.length]],
});
const readFrame = (shot, frame) => scanGray(shot.gray, shot.width, shot.height, frameOptions(frame))
    .map((found) => found.text);

/* Картинка из файла: createImageBitmap, а где его нет — через <img>. */
const withImage = async (file, use) => {
    if (typeof createImageBitmap === 'function') {
        try {
            const bitmap = await createImageBitmap(file);
            try {
                return await use(bitmap, bitmap.width, bitmap.height);
            } finally {
                bitmap.close?.();
            }
        } catch { /* ниже — через <img> */ }
    }
    const url = URL.createObjectURL(file);
    try {
        const image = new Image();
        image.src = url;
        await image.decode();
        return await use(image, image.naturalWidth, image.naturalHeight);
    } catch {
        return null;
    } finally {
        URL.revokeObjectURL(url);
    }
};

const boxArea = (code) => (code.boundingBox?.width || 0) * (code.boundingBox?.height || 0);

/* Код из живого кадра берём, только если он в рамке: распознавание браузера видит весь кадр, и наклейка
   соседнего ОС с краю иначе открыла бы чужую карточку. */
const insideGuide = (code, video, box) => {
    const bounds = code.boundingBox;
    if (!bounds) return false;
    const crop = guideCrop(video, box);
    const x = bounds.x + bounds.width / 2;
    const y = bounds.y + bounds.height / 2;
    return x >= crop.x && x <= crop.x + crop.w && y >= crop.y && y <= crop.y + crop.h;
};

/* USB-сканер на русской раскладке печатает латиницу кода русскими буквами тех же клавиш (K → Л):
   такой код пробуем ещё и в латинской раскладке. null — русских букв нет. */
const RU_KEYS = 'йцукенгшщзфывапролдячсмить';
const EN_KEYS = 'qwertyuiopasdfghjklzxcvbnm';
const latinKeys = (text) => {
    let changed = false;
    const out = [...text].map((char) => {
        const lower = char.toLowerCase();
        const at = RU_KEYS.indexOf(lower);
        if (at < 0) return char;
        changed = true;
        return char === lower ? EN_KEYS[at] : EN_KEYS[at].toUpperCase();
    }).join('');
    return changed ? out : null;
};

/* Чтение фото — в своём потоке (barcodeScanWorker.js): на большом нечитаемом снимке своё распознавание
   идёт секунды, и камера, «Закрыть» и поле номера всё это время замирали бы. Нет потоков — здесь же. */
const photoWorkers = new Set();
let workerBroken = false;
const scanStillOffThread = (gray, width, height) => new Promise((resolve) => {
    if (workerBroken || typeof Worker === 'undefined') {
        resolve(scanStill(gray, width, height));
        return;
    }
    let worker;
    try {
        worker = new Worker(new URL('./barcodeScanWorker.js', import.meta.url), { type: 'module' });
    } catch {
        workerBroken = true;
        resolve(scanStill(gray, width, height));
        return;
    }
    photoWorkers.add(worker);
    const finish = (found) => {
        photoWorkers.delete(worker);
        worker.terminate();
        resolve(found);
    };
    worker.onmessage = (event) => finish(event.data?.found || []);
    worker.onerror = () => {
        workerBroken = true;
        finish(scanStill(gray, width, height));
    };
    // Копией: если поток упадёт, тот же снимок читается здесь.
    worker.postMessage({ kind: 'still', gray, width, height });
});
// Сканер закрыли — чтение фото прекращается, а не досчитывается впустую.
const stopPhotoWorkers = () => {
    photoWorkers.forEach((worker) => worker.terminate());
    photoWorkers.clear();
};

/* В живом кадре бывает несколько кодов (наклейка и заводской штрихкод рядом): первым — тот, что ближе
   к середине рамки, в него и целятся; остальные — запасом для поиска. */
const nearestCodes = (codes, video) => {
    const cx = video.videoWidth / 2;
    const cy = video.videoHeight / 2;
    const distance = (code) => {
        const box = code.boundingBox;
        return box ? Math.hypot(box.x + box.width / 2 - cx, box.y + box.height / 2 - cy) : Number.MAX_VALUE;
    };
    const texts = [];
    codes.filter((code) => code.rawValue).sort((a, b) => distance(a) - distance(b)).forEach((code) => {
        const value = String(code.rawValue).trim();
        if (value && !texts.includes(value)) texts.push(value);
    });
    return texts;
};

/* Фото наклейки → все коды на нём, крупные первыми (наклейку снимают крупно); какой из них в учёте,
   решает поиск. */
const readPhoto = (file) => withImage(file, async (source, sourceWidth, sourceHeight) => {
    const texts = [];
    const add = (text) => {
        const value = String(text || '').trim();
        if (value && !texts.includes(value)) texts.push(value);
    };
    const detector = await nativeDetector();
    if (detector) {
        const codes = await detector.detect(source).catch(() => []);
        codes.filter((code) => code.rawValue).sort((a, b) => boxArea(b) - boxArea(a)).forEach((code) => add(code.rawValue));
    }
    for (const limit of STILL_WIDTHS) {
        if (texts.length) break;
        const scale = Math.min(1, limit / sourceWidth);
        const width = Math.max(1, Math.round(sourceWidth * scale));
        const height = Math.max(1, Math.round(sourceHeight * scale));
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        context.drawImage(source, 0, 0, width, height);
        const gray = toGray(context.getImageData(0, 0, width, height).data, width, height);
        (await scanStillOffThread(gray, width, height)).forEach((found) => add(found.text));
        if (scale === 1) break;
    }
    return texts;
});

export function BarcodeScanner({
    title = 'Сканирование', hint = 'Наведите камеру на штрихкод', paused = false, manual = true,
    manualLabel = 'Номер или штрихкод', onCode, onClose, children = null,
}) {
    const mobile = useIsMobileShell();
    const videoRef = useRef(null);
    const boxRef = useRef(null);
    const streamRef = useRef(null);
    const fileRef = useRef(null);
    const inputRef = useRef(null);
    const pausedRef = useRef(paused);
    const lastRef = useRef({ text: '', at: 0 });
    const onCodeRef = useRef(onCode);
    const hitTimer = useRef(0);
    const mountedRef = useRef(true);
    const [camera, setCamera] = useState({ state: 'starting', message: '' });
    const [torch, setTorch] = useState(null);
    const [hit, setHit] = useState(false);
    const [typed, setTyped] = useState('');
    const [photo, setPhoto] = useState('');         // '' | 'busy' | 'miss'

    useEffect(() => { onCodeRef.current = onCode; }, [onCode]);
    // Фото, дочитанное после закрытия сканера, ничего не открывает.
    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            stopPhotoWorkers();
        };
    }, []);
    // На телефоне «назад» закрывает сканер, а не форму или карточку под ним.
    useScreenBackGesture(mobile, onClose);
    useEffect(() => {
        pausedRef.current = paused;
        if (paused) return;
        // «Сканировать ещё»: наклейка всё ещё перед камерой — тот же код не повторяем сразу.
        lastRef.current = { ...lastRef.current, at: Date.now() };
        // На компьютере курсор — в поле номера, текст выделен: USB-сканер заменит его, а не допишет.
        if (!mobile && manual) {
            inputRef.current?.focus();
            inputRef.current?.select();
        }
    }, [paused, mobile, manual]);

    const deliver = useCallback((value, more = []) => {
        if (!mountedRef.current) return;
        setPhoto('');
        lastRef.current = { text: value, at: Date.now() };
        navigator.vibrate?.(30);
        setHit(true);
        window.clearTimeout(hitTimer.current);
        hitTimer.current = window.setTimeout(() => setHit(false), 700);
        onCodeRef.current?.(value, more);
    }, []);

    const fromCamera = useCallback((texts) => {
        const value = texts[0];
        if (!value || pausedRef.current) return;
        if (value === lastRef.current.text && Date.now() - lastRef.current.at < REPEAT_MS) return;
        deliver(value, texts.slice(1));
    }, [deliver]);

    useEffect(() => {
        let alive = true;
        let timer = 0;
        const start = async () => {
            if (!navigator.mediaDevices?.getUserMedia) {
                setCamera({
                    state: 'error',
                    message: window.isSecureContext === false
                        ? 'Камера открывается только на защищённом адресе (https)'
                        : 'Камера в этом браузере недоступна',
                });
                return;
            }
            let stream;
            try {
                stream = await navigator.mediaDevices.getUserMedia({
                    video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false,
                });
            } catch (error) {
                if (error?.name !== 'OverconstrainedError') {
                    if (alive) setCamera({ state: 'error', message: cameraMessage(error) });
                    return;
                }
                try {
                    stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
                } catch (again) {
                    if (alive) setCamera({ state: 'error', message: cameraMessage(again) });
                    return;
                }
            }
            const video = videoRef.current;
            if (!alive || !video) {
                stream.getTracks().forEach((track) => track.stop());
                return;
            }
            streamRef.current = stream;
            video.srcObject = stream;
            try {
                await video.play();
            } catch { /* muted + playsInline — видео идёт и без play() */ }
            const track = stream.getVideoTracks()[0];
            track?.applyConstraints?.({ advanced: [{ focusMode: 'continuous' }] })?.catch?.(() => {});
            setTorch(track?.getCapabilities?.().torch ? false : null);
            track?.addEventListener?.('ended', () => { if (alive) setCamera({ state: 'error', message: 'Камера отключилась' }); });
            setCamera({ state: 'live', message: '' });
            const detector = await nativeDetector();
            const canvas = document.createElement('canvas');
            const context = canvas.getContext('2d', { willReadFrequently: true });
            let frame = 0;
            // Своё распознавание — в отдельном потоке: кадр там считается, пока камера и кнопки живут.
            // Кадр отдаётся, только когда поток свободен; поток упал — дальше считаем здесь же.
            if (!detector && !workerBroken && typeof Worker !== 'undefined') {
                try {
                    liveWorker = new Worker(new URL('./barcodeScanWorker.js', import.meta.url), { type: 'module' });
                    liveWorker.onmessage = (event) => {
                        liveBusy = false;
                        const texts = event.data?.texts || [];
                        if (texts.length && alive) fromCamera(texts);
                    };
                    liveWorker.onerror = () => {
                        liveWorker?.terminate();
                        liveWorker = null;
                        liveBusy = false;
                    };
                } catch {
                    liveWorker = null;
                }
            }
            const tick = async () => {
                if (!alive) return;
                if (!pausedRef.current && video.readyState >= 2 && video.videoWidth) {
                    try {
                        if (detector) {
                            const codes = (await detector.detect(video)).filter((code) => insideGuide(code, video, boxRef.current));
                            const texts = nearestCodes(codes, video);
                            if (texts.length && alive) fromCamera(texts);
                        } else if (liveWorker) {
                            if (!liveBusy) {
                                const shot = grabFrame(video, boxRef.current, canvas, context);
                                liveBusy = true;
                                liveWorker.postMessage({ kind: 'frame', gray: shot.gray, width: shot.width, height: shot.height,
                                    options: frameOptions(frame) }, [shot.gray.buffer]);
                                frame += 1;
                            }
                        } else {
                            const texts = readFrame(grabFrame(video, boxRef.current, canvas, context), frame);
                            frame += 1;
                            if (texts.length && alive) fromCamera(texts);
                        }
                    } catch { /* кадр не прочитался — следующий */ }
                }
                timer = window.setTimeout(tick, TICK_MS);
            };
            tick();
        };
        let liveWorker = null;
        let liveBusy = false;
        start();
        return () => {
            alive = false;
            liveWorker?.terminate();
            window.clearTimeout(timer);
            window.clearTimeout(hitTimer.current);
            streamRef.current?.getTracks().forEach((track) => track.stop());
            streamRef.current = null;
        };
    }, [fromCamera]);

    // Escape закрывает только сканер: окно под ним (карточка, форма) остаётся.
    useEffect(() => {
        const onKey = (event) => {
            if (event.key !== 'Escape') return;
            event.preventDefault();
            event.stopImmediatePropagation();
            onClose();
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [onClose]);

    const toggleTorch = async () => {
        const track = streamRef.current?.getVideoTracks()[0];
        if (!track || torch === null) return;
        try {
            await track.applyConstraints({ advanced: [{ torch: !torch }] });
            setTorch(!torch);
        } catch {
            setTorch(null);
        }
    };
    const pickPhoto = async (file) => {
        if (!file) return;
        setPhoto('busy');
        const texts = (await readPhoto(file).catch(() => null)) || [];
        if (texts.length) {
            setPhoto('');
            deliver(texts[0], texts.slice(1));
        } else {
            setPhoto('miss');
        }
    };
    const submitTyped = (event) => {
        event.preventDefault();
        const value = typed.trim();
        if (!value) return;
        const latin = latinKeys(value);
        deliver(value, latin ? [latin] : []);
        // Следующий код USB-сканера заменит этот, а не допишется к нему.
        inputRef.current?.select();
    };

    let pill = '';
    if (photo === 'busy') pill = 'Читаю штрихкод на фото…';
    else if (photo === 'miss') pill = 'На фото штрихкод не прочитался — снимите ближе и ровнее';
    else if (camera.state === 'live' && !paused) pill = hint;

    const shell = mobile
        ? 'fixed inset-0 z-[10050] flex flex-col bg-black text-white outline-none motion-safe:animate-fade-in-soft'
        : 'fixed inset-0 z-[10050] flex items-center justify-center bg-slate-900/70 p-6 outline-none backdrop-blur-sm motion-safe:animate-window-dim-in';
    const panel = mobile ? 'contents'
        : 'relative flex max-h-[92vh] w-[min(560px,94vw)] flex-col overflow-y-auto rounded-[28px] bg-black text-white shadow-2xl ring-1 ring-white/10 motion-safe:animate-window-in';

    return createPortal(
        <div role="dialog" aria-modal="true" aria-label={title} className={shell} style={{ fontFamily: APPLE_FONT }}>
            <div className={panel}>
                <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 px-3 pb-2"
                     style={{ paddingTop: mobile ? 'max(12px, env(safe-area-inset-top))' : 14 }}>
                    <button type="button" className={`${darkButton} justify-self-start`} onClick={onClose}>Закрыть</button>
                    <div className="truncate text-[16px] font-semibold">{title}</div>
                    <div className="flex items-center gap-1.5 justify-self-end">
                        {torch !== null && (
                            <button type="button" onClick={toggleTorch} aria-label={torch ? 'Выключить фонарик' : 'Включить фонарик'}
                                    className={torch ? 'grid h-9 w-9 place-items-center rounded-full bg-amber-300 text-[#0f172a] transition' : darkIcon}>
                                {torch ? <Flashlight size={17} /> : <FlashlightOff size={17} />}
                            </button>
                        )}
                        <button type="button" className={darkIcon} onClick={() => fileRef.current?.click()} disabled={photo === 'busy'}
                                aria-label="Прочитать штрихкод с фото">
                            <ImagePlus size={17} />
                        </button>
                    </div>
                </div>

                <div ref={boxRef} className={`relative shrink-0 overflow-hidden bg-black ${mobile ? 'min-h-[240px] flex-1' : 'aspect-[4/3]'}`}>
                    <video ref={videoRef} playsInline muted autoPlay aria-label="Камера"
                           className={`absolute inset-0 h-full w-full object-cover ${camera.state === 'live' ? '' : 'opacity-0'}`} />
                    {camera.state === 'live' && (
                        <div aria-hidden="true" className="pointer-events-none absolute inset-0 grid place-items-center">
                            <div className={`rounded-[22px] border-2 transition-colors duration-200 ${hit ? 'border-emerald-400' : 'border-[rgba(255,255,255,0.9)]'}`}
                                 style={{ width: `${GUIDE.width * 100}%`, height: `${GUIDE.height * 100}%`, boxShadow: '0 0 0 9999px rgba(0,0,0,0.42)' }} />
                        </div>
                    )}
                    <div className="pointer-events-none absolute inset-x-0 top-3 flex justify-center px-4" aria-live="polite">
                        {pill && (
                            <span className="rounded-full bg-[rgba(0,0,0,0.55)] px-3.5 py-1.5 text-center text-[13px] text-[rgba(255,255,255,0.92)] backdrop-blur">
                                {pill}
                            </span>
                        )}
                    </div>
                    {camera.state === 'starting' && (
                        <div className="absolute inset-0 grid place-items-center">
                            <div className="flex flex-col items-center gap-3">
                                <Loader2 size={26} className="animate-spin text-[rgba(255,255,255,0.7)]" />
                                <span className="text-[14px] text-[rgba(255,255,255,0.8)]">Включаю камеру…</span>
                            </div>
                        </div>
                    )}
                    {camera.state === 'error' && (
                        <div className="absolute inset-0 grid place-items-center px-6 text-center">
                            <div className="flex max-w-xs flex-col items-center gap-3">
                                <span className="grid h-12 w-12 place-items-center rounded-2xl bg-[rgba(255,255,255,0.1)] text-[rgba(255,255,255,0.8)]">
                                    <CameraOff size={22} />
                                </span>
                                <span className="text-[14px] leading-snug text-[rgba(255,255,255,0.85)]">{camera.message}</span>
                                <button type="button" className={`${darkButton} bg-[rgba(255,255,255,0.1)]`} onClick={() => fileRef.current?.click()}>
                                    {mobile ? 'Сфотографировать наклейку' : 'Выбрать фото наклейки'}
                                </button>
                            </div>
                        </div>
                    )}
                </div>

                {(children || manual) && (
                    <div className="space-y-3 px-4 pt-3.5" style={{ paddingBottom: mobile ? 'max(18px, env(safe-area-inset-bottom))' : 18 }}>
                        {children}
                        {manual && (
                            <form onSubmit={submitTyped} className="flex items-center gap-2">
                                <input ref={inputRef} value={typed} onChange={(event) => setTyped(event.target.value)}
                                       placeholder={manualLabel} aria-label={manualLabel} enterKeyHint="search"
                                       autoComplete="off" autoCapitalize="characters" spellCheck={false}
                                       className="h-11 min-w-0 flex-1 rounded-full bg-[rgba(255,255,255,0.12)] px-4 text-[15px] text-white placeholder-[rgba(255,255,255,0.5)] outline-none transition focus:bg-[rgba(255,255,255,0.18)] focus:ring-2 focus:ring-blue-500/70" />
                                <button type="submit" disabled={!typed.trim()}
                                        className="h-11 shrink-0 rounded-full bg-blue-600 px-5 text-[15px] font-semibold text-white transition hover:bg-blue-700 active:scale-[0.98] disabled:opacity-40">
                                    Найти
                                </button>
                            </form>
                        )}
                    </div>
                )}
            </div>
            {/* Фото наклейки: из галереи или системной камерой телефона — разрешение на камеру не нужно. */}
            <input ref={fileRef} type="file" accept="image/*" className="hidden" capture={mobile ? 'environment' : undefined}
                   onChange={(event) => {
                       const file = event.target.files?.[0];
                       event.target.value = '';
                       pickPhoto(file);
                   }} />
        </div>,
        document.body,
    );
}

/* Поиск ОС по наклейке: одно найденное — его карточка (после короткого «нашлось»), несколько — выбор,
   ничего — объяснение. */
export function FaScanFind({ api, canBind = false, onClose, onOpenAsset }) {
    const [result, setResult] = useState(null);     // { busy | found | items, code, hidden, error }
    const askRef = useRef(0);
    // Сканер закрыли, пока шёл поиск или «нашлось», — карточка уже не откроется.
    useEffect(() => () => { askRef.current += 1; }, []);
    const find = useCallback(async (code, more = []) => {
        const ask = askRef.current + 1;
        askRef.current = ask;
        const started = Date.now();
        setResult({ busy: true, code });
        try {
            // С фото пришло несколько кодов — первый, что есть в учёте; не нашлось — ответ по первому.
            let answer = null;
            for (const candidate of [code, ...more].slice(0, 4)) {
                const { data } = await api.get('/assets/lookup', { code: candidate });
                if (askRef.current !== ask) return;
                const found = { code: data.code || candidate, items: data.items || [], hidden: Number(data.hidden) || 0 };
                if (!answer) answer = found;
                if (found.items.length || found.hidden) {
                    answer = found;
                    break;
                }
            }
            if (answer.items.length === 1) {
                const item = answer.items[0];
                setResult({ code: answer.code, found: item });
                window.setTimeout(() => { if (askRef.current === ask) onOpenAsset(item.id); },
                    Math.max(0, FOUND_HOLD_MS - (Date.now() - started)));
                return;
            }
            setResult(answer);
        } catch (requestError) {
            if (askRef.current === ask) setResult({ code, items: [], error: errorText(requestError, 'Поиск не удался — попробуйте ещё раз') });
        }
    }, [api, onOpenAsset]);
    const again = () => { askRef.current += 1; setResult(null); };

    let card = null;
    if (result?.busy) {
        card = (
            <div className="flex items-center gap-2.5 rounded-2xl bg-white px-4 py-3.5 text-[14px] text-slate-700">
                <Loader2 size={17} className="animate-spin text-slate-400" /> Ищу ОС <span className="font-semibold tabular-nums text-slate-900">{result.code}</span>
            </div>
        );
    } else if (result?.found) {
        card = (
            <div className="flex items-center gap-3 rounded-2xl bg-white px-4 py-3 text-slate-900">
                <Thumb url={result.found.cover_url} size={40} />
                <span className="min-w-0 flex-1">
                    <span className="block truncate text-[14.5px] font-semibold">{result.found.name}</span>
                    <span className="block truncate text-[12.5px] tabular-nums text-slate-500">
                        {[result.found.inventory_number, shortName(result.found.mol_name)].filter(Boolean).join(' · ')}
                    </span>
                </span>
                <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-emerald-500 text-white" aria-label="Нашлось">
                    <Check size={16} strokeWidth={3} />
                </span>
            </div>
        );
    } else if (result?.items?.length > 1) {
        card = (
            <div className="overflow-hidden rounded-2xl bg-white text-slate-900">
                <div className="px-4 pb-1 pt-3 text-[13px] text-slate-500">
                    Код <span className="font-semibold tabular-nums text-slate-700">{result.code}</span> — у нескольких ОС
                </div>
                <div className="divide-y divide-slate-100">
                    {result.items.map((item) => (
                        <button key={item.id} type="button" onClick={() => onOpenAsset(item.id)}
                                className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition hover:bg-slate-50 active:bg-slate-100">
                            <Thumb url={item.cover_url} size={40} />
                            <span className="min-w-0 flex-1">
                                <span className="block truncate text-[14.5px] font-medium">{item.name}</span>
                                <span className="block truncate text-[12.5px] tabular-nums text-slate-500">
                                    {[item.inventory_number, shortName(item.mol_name), placeText(item)].filter(Boolean).join(' · ')}
                                </span>
                            </span>
                            <ChevronRight size={16} className="shrink-0 text-slate-300" />
                        </button>
                    ))}
                </div>
                <button type="button" onClick={again} className="w-full border-t border-slate-100 px-4 py-3 text-[14.5px] font-semibold text-blue-600 transition hover:bg-slate-50">
                    Сканировать ещё
                </button>
            </div>
        );
    } else if (result) {
        let text = result.error;
        let note = '';
        if (!text && result.hidden) {
            text = 'Это ОС есть в учёте, но вам не открыто';
            note = 'Карточку видят бухгалтерия, IT, руководитель отдела и тот, за кем ОС числится.';
        } else if (!text) {
            text = 'ОС с таким кодом не нашлось';
            note = canBind ? 'Если наклейку ещё не связали с ОС: карточка ОС → «Штрихкод» → «Привязать».'
                : 'Проверьте наклейку или введите инвентарный номер.';
        }
        card = (
            <div className="rounded-2xl bg-white px-4 pb-2 pt-3.5 text-slate-900">
                <div className="text-[13px] tabular-nums text-slate-500">{result.code}</div>
                <div className="mt-0.5 text-[15px] font-semibold leading-snug">{text}</div>
                {note && <div className="mt-1 text-[13px] leading-snug text-slate-500">{note}</div>}
                <button type="button" onClick={again} className="-mx-1 mt-1.5 rounded-lg px-1 py-1.5 text-[14.5px] font-semibold text-blue-600 hover:underline">
                    Сканировать ещё
                </button>
            </div>
        );
    }

    return (
        <BarcodeScanner title="Найти ОС" hint="Наведите камеру на штрихкод наклейки ОС" paused={Boolean(result)}
                        onCode={find} onClose={onClose}>
            {card}
        </BarcodeScanner>
    );
}

/* Поле штрихкода в формах бухгалтерии: ввести руками (или USB-сканером) либо снять камерой. */
export function BarcodeField({ value, onChange, invalid = false, autoFocus = false }) {
    const [scanning, setScanning] = useState(false);
    return (
        <div className="relative">
            <input className={`${FIELD} pr-12 tabular-nums ${invalid ? FIELD_INVALID : ''}`} value={value || ''} maxLength={64}
                   placeholder="Отсканируйте или введите" autoComplete="off" spellCheck={false} autoFocus={autoFocus}
                   aria-label="Штрихкод наклейки" onChange={(event) => onChange(event.target.value.replace(/\s+/g, ''))} />
            <button type="button" onClick={() => setScanning(true)} aria-label="Снять штрихкод камерой"
                    className="absolute right-1.5 top-1/2 grid h-8 w-8 -translate-y-1/2 place-items-center rounded-lg text-blue-600 transition hover:bg-blue-50 active:scale-95">
                <ScanBarcode size={18} />
            </button>
            {scanning && (
                <BarcodeScanner title="Штрихкод наклейки" manual={false} onClose={() => setScanning(false)}
                                onCode={(code) => { onChange(code.replace(/\s+/g, '')); setScanning(false); }} />
            )}
        </div>
    );
}
