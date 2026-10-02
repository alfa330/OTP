import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ChevronLeft, ChevronRight, List, Loader2, Undo2, X } from 'lucide-react';
import { APPLE_FONT, iosBtnSecondary } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import { openEpub } from './epubLoader';
import {
    CLOSE_OVERLAP, OPENING_EASING, OPENING_MS, coverBox, flightFrames, hingeFrames, reverseFrames,
} from './bookOpening';
import { FlightPlaceholder } from './LibraryCover';
import { PALETTES, ReaderEngine, bookMetrics } from './readerEngine';
import {
    STATUS_FINISHED, currentTocIndex, formatPercent, formatPosition, parsePosition, progressFromPosition,
} from './libraryMeta';

/*
 * Ридер «Библиотеки» (задача #282): книга, которую листают.
 *
 * На широком экране — открытая книга, разворот из двух страниц; на телефоне и
 * в узком окне — одна страница. Страницы переворачиваются загибом листа, у
 * каждой — колонтитул сверху и номер снизу, как в печатной книге. Всё это
 * рисует движок (readerEngine.js), книга распаковывается в браузере
 * (epubLoader.js); здесь — только рамка ридера, жесты и сохранение места.
 *
 * КАК ЛИСТАТЬ:
 *   разворот  — нажать на правую страницу (вперёд) или левую (назад), потянуть
 *               лист за край, стрелки, пробел, колесо или трекпад;
 *   телефон   — нажать у правого или левого края, провести пальцем (лист идёт
 *               за пальцем и доворачивается сам), «назад» системы закрывает.
 *
 * НОМЕРА СТРАНИЦ — НАСТОЯЩИЕ для этого экрана (движок считает вёрстку всей
 * книги в фоне), а ПРОЦЕНТ — от 1800-знаковых страниц сервера: он одинаков на
 * любом устройстве, и по нему ведётся мониторинг (libraryMeta.js).
 *
 * МЕСТО СОХРАНЯЕТСЯ САМО (ТЗ 4.2): через секунду после хода, сразу — на
 * последней странице, и в последний момент при уходе со страницы или закрытии
 * (fetch с keepalive). Переход по сноске местом чтения не считается: читатель
 * вернётся кнопкой «Вернуться на стр. N».
 */

const SAVE_DELAY_MS = 1200;
const DRAG_START_PX = 8;
const WHEEL_STEP = 50;
/* Слой ридера: выше сайдбара (100), мобильного бара (70) и IosModal (90), но
   НИЖЕ окна «Новость дня» (120) — объявление, пришедшее во время чтения,
   обязано быть видно, а не открываться под книгой. */
const READER_Z = 110;

/* Тёмная тема портала живёт атрибутом на <html> (src/utils/darkTheme.js):
   тогда и книга ночная — тёмная бумага, светлый текст. */
const isNightTheme = () => typeof document !== 'undefined'
    && document.documentElement.getAttribute('data-otp-theme') === 'dark';

/* Окно поверх ридера (новость дня и подобные): пока оно открыто, клавиши
   принадлежат ему — Escape закрыл бы книгу и то окно разом. */
const coveredByModal = (root) => [...document.querySelectorAll('[aria-modal="true"]')]
    .some((element) => element !== root && !root?.contains(element)
        && Number(window.getComputedStyle(element).zIndex) > READER_Z);

/* Текст ошибки сервера для файла книги: ответ пришёл arraybuffer'ом (так
   запрашивается файл), и JSON с причиной лежит в нём байтами. */
const errorText = (reason) => {
    const data = reason?.response?.data;
    if (typeof data?.error === 'string') return data.error;
    if (data instanceof ArrayBuffer) {
        try {
            const parsed = JSON.parse(new TextDecoder().decode(data));
            if (typeof parsed?.error === 'string') return parsed.error;
        } catch { /* не JSON */ }
    }
    return 'Не удалось открыть книгу';
};

/* Ссылка внутри страницы книги. Теневой DOM отдаёт её только через
   composedPath; своего href у ссылок нет — адрес в data-lr-href/-ext. */
const linkOf = (event) => {
    const path = (event.nativeEvent || event).composedPath?.() || [];
    return path.find((node) => node?.getAttribute
        && (node.hasAttribute('data-lr-href') || node.hasAttribute('data-lr-ext'))) || null;
};

/* Web Animations: fill both — первый кадр стоит до старта, последний — после
   конца. null, где анимировать нечего или браузер не умеет. */
const play = (element, keyframes, options) => (
    element && keyframes && typeof element.animate === 'function'
        ? element.animate(keyframes, { fill: 'both', ...options })
        : null
);

/* Дождаться анимаций: true — все доиграли, false — какую-то прервали. */
const settled = async (list) => {
    const results = await Promise.all(list.filter(Boolean)
        .map((animation) => animation.finished.then(() => true, () => false)));
    return results.every(Boolean);
};

/* Запустить с одного кадра: обложка и лист движка — одно движение. */
const together = (list) => {
    const start = document.timeline?.currentTime;
    if (start !== null && start !== undefined) list.forEach((animation) => { animation.startTime = start; });
    return list;
};

const onScreen = (rect) => Boolean(rect) && rect.width > 0
    && rect.left < window.innerWidth && rect.left + rect.width > 0
    && rect.top < window.innerHeight && rect.top + rect.height > 0;

/* «ещё 3 стр. в главе» — с правильным окончанием не нужно: «стр.» не склоняется.
   null — где кончается глава, пока не известно: тогда только процент. */
const chapterLeft = (left) => {
    if (!Number.isFinite(left)) return '';
    return left > 0 ? `Ещё ${left} стр. в главе` : 'Последняя страница главы';
};

const LibraryReader = ({ bookId, apiBaseUrl, headers, onClose, onProgress, origin = null }) => {
    const isNarrow = useIsMobileShell();
    const reduceMotion = useReducedMotion();
    const [night] = useState(isNightTheme);
    const palette = night ? PALETTES.night : PALETTES.day;

    const rootRef = useRef(null);
    const stageRef = useRef(null);
    const hostRef = useRef(null);
    const engineRef = useRef(null);
    const bookRef = useRef(null);
    const pendingRef = useRef(null);
    const finishRef = useRef(null);
    const timerRef = useRef(null);
    const gestureRef = useRef(null);
    const wheelRef = useRef({ sum: 0, locked: false, timer: 0 });
    const onProgressRef = useRef(onProgress);
    onProgressRef.current = onProgress;
    const escapeRef = useRef(null);
    const placeRef = useRef(null);
    const sideTripRef = useRef(false);
    const deskPressRef = useRef(false);

    const [book, setBook] = useState(null);
    const [phase, setPhase] = useState('loading');
    const [error, setError] = useState('');
    const [position, setPosition] = useState(null);
    const [pagination, setPagination] = useState(null);
    const [finished, setFinished] = useState(false);
    const [tocOpen, setTocOpen] = useState(false);
    const [metrics, setMetrics] = useState(null);
    const [stageBox, setStageBox] = useState(null);
    /*
     * Раскрытие книги (bookOpening.js):
     *   flying   — обложка летит из карточки на место закрытой книги;
     *   closed   — закрытая книга лежит, пока книга распаковывается, а в
     *              начале книги — пока её не откроют (restAtStart);
     *   opening  — обложка раскрывается, левая страница ложится;
     *   open     — читаем (только здесь книга слушает клавиши и касания);
     *   shutting — пролистали назад с первой страницы: книга закрывается и
     *              остаётся лежать (closed);
     *   closing  — книга захлопывается перед уходом;
     *   leaving  — закрытая книга летит обратно в карточку.
     * Без обложки из каталога и при «уменьшить движение» — сразу open.
     */
    const flight = Boolean(origin?.rect && !reduceMotion);
    const [stage, setStage] = useState(flight ? 'flying' : 'open');
    const stageNowRef = useRef(stage);
    stageNowRef.current = stage;
    const closingRef = useRef(false);
    /* Обложка — не страница: в начале книги закрытая книга лежит, пока её не
       откроют (нажатием, свайпом, стрелкой, пунктом оглавления). Место при
       этом не сохраняется — заглянувший на обложку книгу ещё не начал. */
    const [restAtStart, setRestAtStart] = useState(false);
    const restingRef = useRef(false);
    const animsRef = useRef([]);
    const coverGoneRef = useRef(false);
    const pendingMetricsRef = useRef(null);
    const hingeRef = useRef(null);
    const frameRef = useRef(null);
    const artRef = useRef(null);
    const iconRef = useRef(null);
    const textRef = useRef(null);
    const lightRef = useRef(null);
    const edgeRef = useRef(null);
    const castRef = useRef(null);
    /* «Открываю книгу…» — только если открытие затянулось: мелькнувшая на
       долю секунды надпись — это шум. */
    const [slow, setSlow] = useState(false);
    /* Куда вернуться после перехода по сноске. */
    const [returnTo, setReturnTo] = useState(null);

    const progressUrl = `${apiBaseUrl}/api/library/books/${bookId}/progress`;

    /* ---------- сохранение места ---------- */

    const send = useCallback((payload, keepalive) => {
        const deliver = (progress) => { if (progress) onProgressRef.current?.(bookId, progress); };
        const failed = () => {
            // Отметку «Закончено» не теряем: следующий ход перезаписал бы её
            // обычным местом, и книга не стала бы прочитанной никогда.
            if (payload.at_end) finishRef.current = payload;
            else if (!pendingRef.current) pendingRef.current = payload;
        };
        if (keepalive && typeof fetch === 'function') {
            return fetch(progressUrl, {
                method: 'PUT',
                keepalive: true,
                credentials: 'include',
                headers: { ...headers(), 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            }).then((response) => (response.ok ? response.json() : Promise.reject(new Error('save'))))
                .then((data) => deliver(data?.progress))
                .catch(failed);
        }
        return axios.put(progressUrl, payload, { headers: headers() })
            .then((response) => deliver(response.data?.progress))
            .catch(failed);
    }, [bookId, headers, progressUrl]);

    const flush = useCallback((keepalive = false) => {
        clearTimeout(timerRef.current);
        timerRef.current = null;
        const finish = finishRef.current;
        const payload = pendingRef.current;
        finishRef.current = null;
        pendingRef.current = null;
        // Сначала несостоявшаяся отметка конца, потом текущее место: сервер
        // отметку не снимает, а место и процент берёт из последнего запроса.
        const chain = finish ? send(finish, keepalive) : Promise.resolve();
        if (payload) chain.then(() => send(payload, keepalive));
    }, [send]);

    const queueSave = useCallback((payload) => {
        pendingRef.current = payload;
        clearTimeout(timerRef.current);
        if (payload.at_end) { flush(); return; }
        timerRef.current = setTimeout(() => flush(), SAVE_DELAY_MS);
    }, [flush]);

    /* Движок сообщил новое место: процент от страниц сервера, номер — от
       вёрстки, копим сохранение. */
    const handlePosition = useCallback((place) => {
        const progress = progressFromPosition(place, bookRef.current);
        if (!progress) return;
        placeRef.current = { ...place, ...progress };
        setPosition({ ...place, ...progress, onLastPage: place.atEnd });
        if (progress.atEnd) setFinished(true);
        // Сноска — не место чтения; перекладка страниц во время визита к
        // сноске — тоже. Ход страницы или переход по оглавлению — уже чтение.
        if (place.kind === 'link') { sideTripRef.current = true; return; }
        if (place.kind === 'refresh') return;   // пересчитались только номера
        // Книга лежит закрытой: на обложку заглянули, но книгу не открыли —
        // «В процессе» она станет, когда её откроют (openBook сохранит место).
        if (restingRef.current) return;
        if (place.kind === 'layout' && sideTripRef.current) return;
        if (place.kind !== 'layout') sideTripRef.current = false;
        queueSave({
            position: formatPosition(place.section, place.fraction),
            percent: progress.percent,
            page: progress.page,
            at_end: progress.atEnd,
        });
    }, [queueSave]);
    const handlePositionRef = useRef(handlePosition);
    handlePositionRef.current = handlePosition;

    /* ---------- размер сцены ---------- */

    /* Прокрутку страницы под ридером — прочь, и ДО первого замера сцены: с
       обычной полосой прокрутки (Windows) её исчезновение расширяло бы сцену
       уже после того, как обложка полетела к месту книги. */
    useLayoutEffect(() => {
        const html = document.documentElement;
        const previous = [html.style.overscrollBehaviorX, document.body.style.overscrollBehaviorX, document.body.style.overflow];
        html.style.overscrollBehaviorX = 'none';
        document.body.style.overscrollBehaviorX = 'none';
        document.body.style.overflow = 'hidden';
        return () => {
            [html.style.overscrollBehaviorX, document.body.style.overscrollBehaviorX, document.body.style.overflow] = previous;
        };
    }, []);

    useLayoutEffect(() => {
        const stage = stageRef.current;
        if (!stage) return undefined;
        const measure = () => {
            const rect = stage.getBoundingClientRect();
            if (rect.width < 10 || rect.height < 10) return;
            setStageBox({ left: rect.left, top: rect.top, width: rect.width, height: rect.height });
            setMetrics((previous) => {
                const next = bookMetrics(Math.round(rect.width), Math.round(rect.height), isNarrow);
                return previous && JSON.stringify(previous) === JSON.stringify(next) ? previous : next;
            });
        };
        measure();
        const observer = new ResizeObserver(measure);
        observer.observe(stage);
        return () => observer.disconnect();
    }, [isNarrow]);

    const metricsRef = useRef(null);
    metricsRef.current = metrics;
    // Пока книга раскрывается или закрывается, раскладка ждёт: лист в полёте —
    // страница движка, и перекладка подменила бы её посреди движения.
    useEffect(() => {
        if (!metrics || !engineRef.current) return;
        if (['opening', 'shutting', 'closing', 'leaving'].includes(stageNowRef.current)) {
            pendingMetricsRef.current = metrics;
            return;
        }
        engineRef.current.layout(metrics);
    }, [metrics]);


    /* ---------- раскрытие книги (bookOpening.js) ---------- */

    // Место закрытой книги на экране: на развороте — по центру стола, на
    // половину страницы левее правой (раскрываясь, книга съезжает на место —
    // hingeFrames: shift), на одной странице — она сама.
    const closedShift = metrics?.mode === 'spread' ? metrics.pageWidth / 2 : 0;
    const closedShiftRef = useRef(0);
    closedShiftRef.current = closedShift;
    const closedRect = useMemo(() => (metrics && stageBox ? {
        left: stageBox.left + metrics.left + (metrics.mode === 'spread' ? metrics.pageWidth / 2 : 0),
        top: stageBox.top + metrics.top,
        width: metrics.pageWidth,
        height: metrics.height,
    } : null), [metrics, stageBox]);
    const closedRectRef = useRef(null);
    closedRectRef.current = closedRect;
    const artAspect = origin?.aspect > 0 ? origin.aspect : 2 / 3;

    // Фон ридера проявляется, пока обложка летит.
    useLayoutEffect(() => {
        const animation = play(rootRef.current, [{ opacity: 0 }, { opacity: 1 }], {
            duration: reduceMotion ? 120 : OPENING_MS.fadeIn, easing: OPENING_EASING.fadeIn,
        });
        return () => animation?.cancel();
        // Один раз — при открытии ридера.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Полёт — один раз, как только известно место книги, и до первой
    // отрисовки: первый кадр обложки стоит ровно на карточке.
    const flownRef = useRef(false);
    useLayoutEffect(() => {
        if (!flight || flownRef.current || !closedRect) return;
        flownRef.current = true;
        const frames = flightFrames(origin.rect, closedRect, artAspect);
        const options = { duration: OPENING_MS.flyIn, easing: OPENING_EASING.flyIn };
        const list = together([
            play(frameRef.current, frames.frame, options),
            play(artRef.current, frames.art, options),
            play(iconRef.current, frames.anchor, options),
            play(textRef.current, frames.anchor, options),
        ].filter(Boolean));
        animsRef.current = list;
        // Карточка отдаёт обложку в том же кадре, где её подхватила летящая:
        // раньше — на месте карточки мелькнула бы пустота.
        origin.onLift?.();
        settled(list).then((done) => { if (done && !closingRef.current) setStage('closed'); });
    }, [flight, closedRect, origin, artAspect]);

    useEffect(() => {
        if (phase !== 'loading') return undefined;
        const timer = setTimeout(() => setSlow(true), 500);
        return () => clearTimeout(timer);
    }, [phase]);

    // Книга не открылась: закрытая книга тает, под ней — текст ошибки.
    useEffect(() => {
        if (phase !== 'error' || !flight || coverGoneRef.current) return;
        coverGoneRef.current = true;
        play(hingeRef.current, [{ opacity: 1 }, { opacity: 0 }], { duration: OPENING_MS.coverFade, easing: 'ease-out' });
    }, [phase, flight]);

    // Книга распакована, закрытая книга лежит — раскрыть. В начале книги — нет:
    // её откроет читатель (openBook).
    useEffect(() => {
        if (stage === 'closed' && phase === 'ready' && engineRef.current && !closingRef.current && !restAtStart) {
            setStage('opening');
        }
    }, [stage, phase, restAtStart]);

    const finishOpening = useCallback((list) => {
        // Обложку прячем раньше, чем снимаем её анимации: иначе на кадр она
        // вернулась бы на правую страницу.
        if (hingeRef.current) hingeRef.current.style.visibility = 'hidden';
        if (castRef.current) castRef.current.style.visibility = 'hidden';
        list.forEach((animation) => animation.cancel());
        engineRef.current?.endOpening();
        if (stageRef.current) stageRef.current.style.overflow = '';
        setStage('open');
        rootRef.current?.focus({ preventScroll: true });
        const pending = pendingMetricsRef.current;
        pendingMetricsRef.current = null;
        if (pending) engineRef.current?.layout(pending);
    }, []);

    // Раскрытие: обложка и левая страница движка — одним движением, с одного
    // кадра. Лист в полёте крупнее книги (перспектива) — сцена его не режет.
    useLayoutEffect(() => {
        if (stage !== 'opening') return;
        const engine = engineRef.current;
        if (!engine) return;
        const parts = engine.openingParts();
        const frames = hingeFrames({ ...parts, shift: closedShiftRef.current });
        const options = {
            duration: parts.spread ? OPENING_MS.hingeSpread : OPENING_MS.hingeSingle,
            easing: OPENING_EASING.hinge,
        };
        if (stageRef.current) stageRef.current.style.overflow = 'visible';
        // Срез страниц дальше рисует сама книга — на том же месте.
        if (edgeRef.current) edgeRef.current.style.visibility = 'hidden';
        // Прошлые движения (полёт или закрытие с первой страницы) кончились
        // ровно в начальном кадре этого — снимаем их, чтобы не складывались.
        animsRef.current.forEach((animation) => animation.cancel());
        const list = together([
            play(hingeRef.current, frames.cover, options),
            play(lightRef.current, frames.coverLight, options),
            play(castRef.current, frames.cast, options),
            play(hostRef.current, frames.pan, options),
            play(parts.leaf, frames.leaf, options),
            play(parts.leafShade, frames.leafShade, options),
            play(parts.shadow, frames.bookShadow, options),
            play(parts.edgeLeft, frames.edgeLeft, options),
        ].filter(Boolean));
        animsRef.current = list;
        settled(list).then((done) => { if (done && !closingRef.current) finishOpening(list); });
    }, [stage, finishOpening]);

    /* Открыть закрытую книгу (она лежит в начале): обложка раскрывается, а
       место — начало книги — сохраняется только теперь. */
    const openBook = useCallback(() => {
        if (closingRef.current || stageNowRef.current !== 'closed' || !restingRef.current) return;
        restingRef.current = false;
        setRestAtStart(false);
        setStage('opening');
        if (placeRef.current) handlePositionRef.current({ ...placeRef.current, kind: 'open' });
    }, []);

    /* Пролистали назад с первой страницы — книга закрывается и остаётся лежать
       закрытой, как настоящая: дальше назад — только её обложка. */
    const shutBook = useCallback(() => {
        if (!flight || closingRef.current || stageNowRef.current !== 'open' || !engineRef.current) return;
        setTocOpen(false);
        setStage('shutting');
    }, [flight]);

    useLayoutEffect(() => {
        if (stage !== 'shutting') return;
        const engine = engineRef.current;
        if (!engine) return;
        const parts = engine.openingParts();
        const frames = hingeFrames({ ...parts, shift: closedShiftRef.current });
        const options = {
            duration: parts.spread ? OPENING_MS.closeSpread : OPENING_MS.closeSingle,
            easing: OPENING_EASING.close,
        };
        if (stageRef.current) stageRef.current.style.overflow = 'visible';
        if (hingeRef.current) hingeRef.current.style.visibility = 'visible';
        if (castRef.current) castRef.current.style.visibility = 'visible';
        animsRef.current.forEach((animation) => animation.cancel());
        const back = (keyframes) => (keyframes ? reverseFrames(keyframes) : null);
        const list = together([
            play(hingeRef.current, back(frames.cover), options),
            play(lightRef.current, back(frames.coverLight), options),
            play(castRef.current, back(frames.cast), options),
            play(hostRef.current, back(frames.pan), options),
            play(parts.leaf, back(frames.leaf), options),
            play(parts.leafShade, back(frames.leafShade), options),
            play(parts.shadow, back(frames.bookShadow), options),
            play(parts.edgeLeft, back(frames.edgeLeft), options),
        ].filter(Boolean));
        animsRef.current = list;
        // Последние кадры держатся (fill), пока книгу не откроют снова: под
        // обложкой страниц не видно (closed прячет ридер), а снимет их раскрытие.
        settled(list).then((done) => {
            if (!done || closingRef.current) return;
            if (stageRef.current) stageRef.current.style.overflow = '';
            restingRef.current = true;
            engine.pinStart();
            setRestAtStart(true);
            setStage('closed');
            rootRef.current?.focus({ preventScroll: true });
        });
    }, [stage]);

    /* ---------- открытие книги ---------- */

    const hasMetrics = Boolean(metrics);
    useEffect(() => {
        if (!hasMetrics) return undefined;
        let cancelled = false;
        let loader = null;
        setPhase('loading');
        setError('');

        const open = async () => {
            const [meta, file] = await Promise.all([
                axios.get(`${apiBaseUrl}/api/library/books/${bookId}`, { headers: headers() }),
                axios.get(`${apiBaseUrl}/api/library/books/${bookId}/file`,
                    { headers: headers(), responseType: 'arraybuffer' }),
            ]);
            if (cancelled) return;
            const info = meta.data?.book;
            if (!info) throw new Error('empty');
            bookRef.current = info;
            setBook(info);
            setFinished(info.progress?.status === STATUS_FINISHED);

            loader = await openEpub(file.data);
            if (cancelled) { loader.destroy(); return; }
            const engine = new ReaderEngine({
                loader,
                book: info,
                container: hostRef.current,
                lang: info.language || 'ru',
                cacheKey: `${bookId}:${info.total_chars}`,
                onPosition: (place) => { if (!cancelled) handlePositionRef.current(place); },
                onPagination: (state) => { if (!cancelled) setPagination({ ...state }); },
                reducedMotion: Boolean(reduceMotion),
                palette,
            });
            engineRef.current = engine;
            if (flight) engine.hold();
            engine.layout(metricsRef.current);
            const saved = parsePosition(info.progress?.position);
            // Новая книга ляжет закрытой — её место не сохраняется заранее.
            restingRef.current = flight && !saved;
            await engine.openAt(saved ? saved.section : -1, saved ? saved.fraction : 0);
            // Открытие, прерванное сменой размера, движок повторяет сам —
            // раскрывать книгу можно, только когда страницы действительно легли.
            await engine.placed;
            if (!cancelled) {
                // В начале книги она лежит закрытой, пока её не откроют:
                // новая книга и та, где остановились на первой странице.
                const rest = flight && (!saved || engine.atBeginning());
                restingRef.current = rest;
                if (rest) engine.pinStart();
                setRestAtStart(rest);
                setPhase('ready');
                rootRef.current?.focus({ preventScroll: true });
            }
        };

        open().catch((reason) => {
            if (cancelled) return;
            setError(errorText(reason));
            setPhase('error');
        });

        return () => {
            cancelled = true;
            flush(true);
            engineRef.current?.destroy();
            engineRef.current = null;
            loader?.destroy();
        };
        // Книга открывается один раз — на первом известном размере сцены;
        // дальше размер меняет только раскладку (engine.layout выше).
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, bookId, hasMetrics]);

    /* Уход со страницы: вкладку закрыли, телефон свернул браузер. */
    useEffect(() => {
        const onHide = () => { if (document.visibilityState === 'hidden') flush(true); };
        const onPageHide = () => flush(true);
        document.addEventListener('visibilitychange', onHide);
        window.addEventListener('pagehide', onPageHide);
        return () => {
            document.removeEventListener('visibilitychange', onHide);
            window.removeEventListener('pagehide', onPageHide);
        };
    }, [flush]);

    /* Закрыть книгу: сохранить место, захлопнуть обложку, вернуть её в
       карточку — и только потом убрать ридер (onClose). Закрыли посреди
       раскрытия — идущее движение разворачивается назад с того же места. */
    const close = useCallback(() => {
        if (closingRef.current) return;
        closingRef.current = true;
        flush(true);
        setTocOpen(false);
        gestureRef.current?.detach?.();
        gestureRef.current = null;

        const fadeRoot = (delay = 0) => play(rootRef.current, [{ opacity: 1 }, { opacity: 0 }], {
            duration: reduceMotion ? 120 : OPENING_MS.fadeOut, delay, easing: 'cubic-bezier(0.4, 0, 1, 1)',
        });

        const run = async () => {
            const engine = engineRef.current;
            // Движок замирает: начатый ход досчитан, начатый переход доделан, новых
            // нет — иначе они подменили бы страницу посреди захлопывания.
            if (engine) await engine.beginClosing();
            // Книга уже закрывается с первой страницы — дать ей закрыться: дальше
            // путь тот же, что у лежащей закрытой.
            if (stageNowRef.current === 'shutting') await settled(animsRef.current);
            const now = stageNowRef.current;
            if (!flight || coverGoneRef.current) {
                await settled([fadeRoot()]);
                return;
            }
            // 1. Захлопнуть.
            if (now === 'opening') {
                setStage('closing');
                animsRef.current.forEach((animation) => animation.reverse());
                await settled(animsRef.current);
            } else if (now === 'open' && engine) {
                setStage('closing');
                const parts = engine.openingParts();
                const frames = hingeFrames({ ...parts, shift: closedShiftRef.current });
                const options = {
                    duration: parts.spread ? OPENING_MS.closeSpread : OPENING_MS.closeSingle,
                    easing: OPENING_EASING.close,
                };
                if (stageRef.current) stageRef.current.style.overflow = 'visible';
                if (hingeRef.current) hingeRef.current.style.visibility = 'visible';
                if (castRef.current) castRef.current.style.visibility = 'visible';
                const back = (keyframes) => (keyframes ? reverseFrames(keyframes) : null);
                const list = together([
                    play(hingeRef.current, back(frames.cover), options),
                    play(lightRef.current, back(frames.coverLight), options),
                    play(castRef.current, back(frames.cast), options),
                    play(hostRef.current, back(frames.pan), options),
                    play(parts.leaf, back(frames.leaf), options),
                    play(parts.leafShade, back(frames.leafShade), options),
                    play(parts.shadow, back(frames.bookShadow), options),
                    play(parts.edgeLeft, back(frames.edgeLeft), options),
                ].filter(Boolean));
                animsRef.current = list;
                await Promise.race([
                    settled(list),
                    new Promise((resolve) => { setTimeout(resolve, options.duration * CLOSE_OVERLAP); }),
                ]);
            }
            // 2. Книга закрыта: страницы под обложкой больше не нужны, срез
            //    страниц — снова у обложки.
            if (hostRef.current) hostRef.current.style.opacity = '0';
            if (edgeRef.current) edgeRef.current.style.visibility = 'visible';
            // Тень от обложки падала на страницу — страницы больше нет, а
            // обложка улетает: на пустом столе тени не место.
            if (castRef.current) castRef.current.style.visibility = 'hidden';
            setStage('leaving');
            // 3. Обратно в карточку — туда, где она сейчас: каталог мог
            //    сдвинуться. Карточки не видно — книга тает на месте.
            const rect = closedRectRef.current;
            const target = origin?.locate?.();
            let list;
            if (now === 'flying') {
                list = animsRef.current;
                list.forEach((animation) => animation.reverse());
            } else if (rect && onScreen(target)) {
                const frames = flightFrames(target, rect, artAspect);
                const options = { duration: OPENING_MS.flyOut, easing: OPENING_EASING.flyOut };
                list = together([
                    play(frameRef.current, reverseFrames(frames.frame), options),
                    play(artRef.current, reverseFrames(frames.art), options),
                    play(iconRef.current, reverseFrames(frames.anchor), options),
                    play(textRef.current, reverseFrames(frames.anchor), options),
                ].filter(Boolean));
            } else {
                list = [play(frameRef.current, [{ transform: 'none', opacity: 1 }, { transform: 'scale(0.94)', opacity: 0 }], {
                    duration: OPENING_MS.coverFade, easing: 'ease-in',
                })];
            }
            await settled([...list, fadeRoot(60)]);
        };
        run().catch(() => {}).finally(() => onClose?.());
    }, [artAspect, flight, flush, onClose, origin, reduceMotion]);

    const ready = phase === 'ready' && stage === 'open';
    // Закрытая книга в начале ждёт читателя: вперёд — открыть её.
    const resting = phase === 'ready' && stage === 'closed' && restAtStart;

    const flip = useCallback((forward) => {
        if (resting) {
            if (forward) openBook();
            return;
        }
        if (!ready) return;
        const engine = engineRef.current;
        // Назад с первой страницы — закрыть книгу: дальше только обложка.
        if (!forward && flight && engine?.isFirstPage()) {
            shutBook();
            return;
        }
        engine?.flip(forward);
    }, [flight, openBook, ready, resting, shutBook]);

    escapeRef.current = () => { if (tocOpen) setTocOpen(false); else close(); };

    /* ---------- клавиатура ---------- */

    useEffect(() => {
        const onKey = (event) => {
            if (coveredByModal(rootRef.current)) return;
            if (event.key === 'Escape') { escapeRef.current?.(); return; }
            if (tocOpen || event.metaKey || event.ctrlKey || event.altKey) return;
            const tag = event.target?.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA') return;
            const forward = event.key === 'ArrowRight' || event.key === 'PageDown' || (event.key === ' ' && !event.shiftKey);
            const back = event.key === 'ArrowLeft' || event.key === 'PageUp' || (event.key === ' ' && event.shiftKey);
            if (forward || back) {
                event.preventDefault();
                flip(forward);
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [flip, tocOpen]);

    /* ---------- колесо и трекпад ----------
       Слушатель свой, не React: только так можно отменить событие (React вешает
       wheel пассивным). Горизонтальный свайп двумя пальцами иначе ещё и уводил
       бы браузер «назад» по истории. Один жест — одна страница: инерция
       трекпада шлёт события ещё полсекунды. Щипок (ctrl + колесо) — масштаб. */
    useEffect(() => {
        const stage = stageRef.current;
        if (!stage) return undefined;
        const onWheel = (event) => {
            if (event.ctrlKey || event.metaKey) return;
            const horizontal = Math.abs(event.deltaX) > Math.abs(event.deltaY);
            if (horizontal) event.preventDefault();
            if (tocOpen || !(ready || resting)) return;
            const state = wheelRef.current;
            state.sum += horizontal ? event.deltaX : event.deltaY;
            clearTimeout(state.timer);
            state.timer = setTimeout(() => { state.locked = false; state.sum = 0; }, 180);
            if (!state.locked && Math.abs(state.sum) >= WHEEL_STEP) {
                state.locked = true;
                flip(state.sum > 0);
                state.sum = 0;
            }
        };
        stage.addEventListener('wheel', onWheel, { passive: false });
        return () => stage.removeEventListener('wheel', onWheel);
    }, [flip, ready, resting, tocOpen]);

    /* ---------- касания и мышь по книге ---------- */

    const openLink = (link) => {
        const external = link.getAttribute('data-lr-ext');
        if (external) {
            if (/^(https?|mailto):/i.test(external)) window.open(external, '_blank', 'noopener,noreferrer');
            return;
        }
        const [section, anchor = ''] = String(link.getAttribute('data-lr-href') || '').split('#');
        if (section === '' || !engineRef.current) return;
        const here = placeRef.current;
        // Обратная ссылка из сноски («↩», номер сноски) в ту главу, откуда
        // пришли, — это и есть возвращение: кнопка «Вернуться» больше не нужна.
        // Ссылка из сноски в другую сноску кнопку не перезаписывает: вернуться
        // надо к тексту, а не к первой сноске.
        if (returnTo && Number(section) === returnTo.section) {
            setReturnTo(null);
            engineRef.current.jumpTo(Number(section), anchor, 0, 'jump');
            return;
        }
        engineRef.current.jumpTo(Number(section), anchor, 0, 'link').then((moved) => {
            if (moved && here && !returnTo) setReturnTo({ section: here.section, fraction: here.fraction, folio: here.folio });
        });
    };

    const goBack = () => {
        const target = returnTo;
        setReturnTo(null);
        if (target) engineRef.current?.jumpTo(target.section, '', target.fraction, 'jump');
    };

    /* Жест кончился: отпустили палец, кнопку мыши увели за книгу, система
       отобрала касание. Лист, взятый за угол, обязательно доворачивается или
       возвращается — застрявший полузагнутым он запирал бы все свайпы. */
    const endGesture = (event, cancelled = false) => {
        const gesture = gestureRef.current;
        if (!gesture || gesture.id !== event.pointerId) return;
        gestureRef.current = null;
        gesture.detach?.();
        if (gesture.dragging) {
            const velocity = cancelled ? 0 : gesture.velocity;
            gesture.ready?.then((ok) => { if (ok) engineRef.current?.dragEnd(velocity); });
            return;
        }
        if (cancelled) return;
        const moved = Math.hypot(event.clientX - gesture.startX, event.clientY - gesture.startY);
        if (moved > DRAG_START_PX || performance.now() - gesture.time > 450) return;
        if (gesture.link) { openLink(gesture.link); return; }
        // Нажатие, снимающее выделение, страницу не листает.
        if (gesture.hadSelection || String(window.getSelection?.() || '').trim()) return;
        const { side, pageX, pageWidth, margin } = gesture.spot;
        // Мышью листают полем страницы (и столом вокруг книги): щелчок по
        // тексту — это щелчок по тексту, как в любой книге на компьютере.
        if (gesture.mouse) {
            if (side === 'single') {
                if (margin === 'left') flip(false);
                else if (margin === 'right') flip(true);
            } else if (margin === 'outer') {
                flip(side === 'right');
            }
            return;
        }
        // Разворот: правая страница — вперёд, левая — назад, как в книге.
        if (side === 'right') { flip(true); return; }
        if (side === 'left') { flip(false); return; }
        const share = pageX / pageWidth;
        if (share < 0.3) flip(false);
        else if (share > 0.7) flip(true);
    };
    const endGestureRef = useRef(endGesture);
    endGestureRef.current = endGesture;

    const onPointerDown = (event) => {
        if (!ready || event.button > 0 || !event.isPrimary || !engineRef.current) return;
        if (gestureRef.current?.dragging) return;   // второй палец не перехватывает лист
        const spot = engineRef.current.locate(event.clientX, event.clientY);
        if (!spot.inside) return;
        const share = spot.pageX / spot.pageWidth;
        // Мышью лист тянут за внешний край страницы — в середине мышь выделяет
        // текст, как в любой книге на компьютере.
        const edge = spot.side === 'left' ? share < 0.25 : spot.side === 'right' ? share > 0.75 : (share < 0.22 || share > 0.78);
        const gesture = {
            id: event.pointerId,
            mouse: event.pointerType === 'mouse',
            startX: event.clientX,
            startY: event.clientY,
            spot,
            time: performance.now(),
            link: linkOf(event),
            hadSelection: Boolean(String(window.getSelection?.() || '').trim()),
            canDrag: event.pointerType !== 'mouse' || edge,
            dragging: false,
            ready: null,
            started: false,
            move: null,
            last: { x: event.clientX, t: performance.now() },
            velocity: 0,
        };
        // Конец жеста слушаем на окне: кнопку мыши могут отпустить за книгой.
        const up = (upEvent) => endGestureRef.current(upEvent);
        const cancel = (cancelEvent) => endGestureRef.current(cancelEvent, true);
        window.addEventListener('pointerup', up);
        window.addEventListener('pointercancel', cancel);
        gesture.detach = () => {
            window.removeEventListener('pointerup', up);
            window.removeEventListener('pointercancel', cancel);
        };
        gestureRef.current = gesture;
    };

    const onPointerMove = (event) => {
        const gesture = gestureRef.current;
        if (!gesture || gesture.id !== event.pointerId) return;
        if (gesture.mouse && event.buttons === 0) { endGesture(event, true); return; }
        const dx = event.clientX - gesture.startX;
        const dy = event.clientY - gesture.startY;
        if (!gesture.dragging) {
            if (!gesture.canDrag || Math.abs(dx) < DRAG_START_PX || Math.abs(dx) < Math.abs(dy)) return;
            // Назад с первой страницы листа нет — книга закрывается.
            if (dx > 0 && flight && engineRef.current?.isFirstPage()) {
                gestureRef.current = null;
                gesture.detach?.();
                shutBook();
                return;
            }
            gesture.dragging = true;
            gesture.forward = dx < 0;
            try { hostRef.current?.setPointerCapture(event.pointerId); } catch { /* уже отпущен */ }
            window.getSelection?.()?.removeAllRanges?.();
            gesture.ready = engineRef.current
                ? engineRef.current.dragStart(gesture.forward, gesture.spot.y < gesture.spot.height / 2)
                : Promise.resolve(false);
            gesture.ready.then((ok) => {
                gesture.started = ok;
                if (ok && gesture.move) engineRef.current?.dragMove(...gesture.move);
            });
        }
        const now = performance.now();
        const dt = Math.max(1, now - gesture.last.t);
        gesture.velocity = 0.7 * ((event.clientX - gesture.last.x) / dt) + 0.3 * gesture.velocity;
        gesture.last = { x: event.clientX, t: now };
        gesture.move = [dx, dy];
        if (gesture.started) engineRef.current?.dragMove(dx, dy);
    };

    useEffect(() => () => gestureRef.current?.detach?.(), []);

    /* Закрытая книга: свайп влево — открыть, как перелистнуть обложку. */
    const coverSwipeRef = useRef(null);
    const onCoverPointerDown = (event) => {
        if (!event.isPrimary) return;
        coverSwipeRef.current = { x: event.clientX, y: event.clientY };
    };
    const onCoverPointerUp = (event) => {
        const start = coverSwipeRef.current;
        coverSwipeRef.current = null;
        if (!start) return;
        const dx = event.clientX - start.x;
        if (dx < -40 && Math.abs(dx) > Math.abs(event.clientY - start.y)) openBook();
    };

    /* Нажатие по «столу» вокруг книги — тоже перелистывание. Только если и
       нажали на столе: выделение текста, отпущенное за краем книги, шлёт
       щелчок общему предку — это не нажатие по столу. */
    const onDeskPress = (event) => {
        const onDesk = event.target === stageRef.current || event.target === hostRef.current;
        deskPressRef.current = onDesk && Boolean(engineRef.current)
            && !engineRef.current.locate(event.clientX, event.clientY).inside;
    };

    const onDeskClick = (event) => {
        const pressed = deskPressRef.current;
        deskPressRef.current = false;
        if (!pressed || !ready || !engineRef.current) return;
        if (event.target !== stageRef.current && event.target !== hostRef.current) return;
        const spot = engineRef.current.locate(event.clientX, event.clientY);
        if (spot.inside) return;
        flip(spot.x > spot.pageWidth * (metrics?.mode === 'spread' ? 1 : 0.5));
    };

    /* ---------- «назад» на телефоне ---------- */

    useScreenBackGesture(isNarrow, close);
    useScreenBackGesture(isNarrow && tocOpen, () => setTocOpen(false));

    /* ---------- что показать ---------- */

    const toc = useMemo(() => (Array.isArray(book?.toc) ? book.toc : []), [book]);
    // Глава — по вёрстке (пункты оглавления внутри файла), пока она не
    // досчитана — по знакам сервера.
    const tocIndex = position?.chapterIndex >= 0
        ? position.chapterIndex
        : currentTocIndex(toc, position?.page || 1, position ? position.offset : null);
    const leftText = position ? chapterLeft(position.leftInChapter) : '';
    const percent = position ? position.percent : (book?.progress?.percent || 0);
    const showDone = finished && (percent >= 100 || position?.onLastPage);
    const folio = position?.folio ?? null;
    const total = pagination?.total ?? null;

    const goTo = useCallback((item) => {
        setTocOpen(false);
        const engine = engineRef.current;
        if (!Number.isInteger(item?.spine) || !engine) return;
        setReturnTo(null);
        // «Обложка» в оглавлении — это закрытая книга, а не страница: в начало
        // книги и закрыть её (раскроется она уже на первой странице).
        if (engine.isCover(item.spine)) {
            if (stageNowRef.current !== 'open' || !flight) {
                if (!restingRef.current) engine.jumpTo(item.spine);
                return;
            }
            if (engine.isFirstPage()) shutBook();
            else engine.jumpTo(item.spine).then((moved) => { if (moved) shutBook(); });
            return;
        }
        // Глава, выбранная на закрытой книге, — книга раскрывается на ней.
        if (restingRef.current) {
            engine.jumpTo(item.spine, item.anchor || '').then((moved) => { if (moved) openBook(); });
            return;
        }
        engine.jumpTo(item.spine, item.anchor || '');
    }, [flight, openBook, shutBook]);

    // «Вернуться» не закрывает номер страницы: на одной странице номер —
    // посередине внизу, кнопка встаёт над ним; на развороте посередине корешок.
    const pillBottom = !metrics ? 12
        : metrics.mode === 'spread' ? metrics.top + 10 : metrics.top + metrics.pad.bottom + 8;

    const deskChevron = 'absolute top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full opacity-0 transition hover:bg-black/[0.05] focus-visible:opacity-100 group-hover/stage:opacity-100';
    const chromeText = night ? '#a39d93' : '#8b857b';

    return (
        <>
            <div
                ref={rootRef}
                tabIndex={-1}
                className="fixed inset-0 flex flex-col outline-none"
                style={{
                    fontFamily: APPLE_FONT,
                    zIndex: READER_Z,
                    background: isNarrow ? palette.paper : palette.desk,
                    // Горизонтально на iPhone книга не уходит под «чёлку».
                    paddingLeft: 'env(safe-area-inset-left)',
                    paddingRight: 'env(safe-area-inset-right)',
                }}
                role="dialog"
                aria-modal="true"
                aria-label={book?.title || origin?.title || 'Книга'}
            >
                <header
                    className="relative flex shrink-0 items-center gap-1 px-2 sm:px-3"
                    style={{ paddingTop: 'env(safe-area-inset-top)', height: 'calc(50px + env(safe-area-inset-top))' }}
                >
                    <button
                        type="button"
                        onClick={close}
                        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition hover:bg-black/[0.05] active:scale-95"
                        style={{ color: chromeText }}
                        aria-label="Закрыть книгу"
                    >
                        <X size={19} strokeWidth={2.1} />
                    </button>
                    <div className="min-w-0 flex-1 truncate px-1 text-center text-[13px] font-medium tracking-[-0.005em]" style={{ color: chromeText }}>
                        {book?.title || origin?.title || ' '}
                    </div>
                    <button
                        type="button"
                        onClick={() => setTocOpen(true)}
                        disabled={!(ready || resting) || !toc.length}
                        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition hover:bg-black/[0.05] active:scale-95 disabled:opacity-35"
                        style={{ color: chromeText }}
                        aria-label="Оглавление"
                    >
                        <List size={19} strokeWidth={2.1} />
                    </button>
                </header>

                <div
                    ref={stageRef}
                    className="group/stage relative min-h-0 flex-1 overflow-hidden"
                    onPointerDown={onDeskPress}
                    onClick={onDeskClick}
                >
                    {/* Книгу рисует движок. Касания и мышь — здесь: страницы
                        лежат в нашем DOM, без iframe, и события доходят в любом
                        браузере, включая Safari. */}
                    <div
                        ref={hostRef}
                        className={`absolute inset-0 touch-none ${isNarrow ? 'select-none' : ''}`}
                        style={{
                            WebkitTouchCallout: 'none',
                            visibility: phase === 'error' ? 'hidden' : 'visible',
                            // С полётом страницы видны с начала раскрытия (до того их
                            // закрывает обложка); без него — проявляются, когда готовы.
                            opacity: flight
                                ? (['opening', 'open', 'closing'].includes(stage) ? 1 : 0)
                                : (phase === 'ready' ? 1 : 0),
                            transition: flight ? 'none' : 'opacity 260ms ease',
                        }}
                        onPointerDown={onPointerDown}
                        onPointerMove={onPointerMove}
                        onLostPointerCapture={(event) => {
                            // Только если захват потерял сам лист. Касание на телефоне
                            // неявно захвачено элементом текста, и перенос захвата на
                            // книгу шлёт «потерю» от того элемента — это не отмена.
                            if (event.target === event.currentTarget && gestureRef.current?.dragging) endGesture(event, true);
                        }}
                        onDragStart={(event) => event.preventDefault()}
                        onContextMenu={(event) => { if (isNarrow) event.preventDefault(); }}
                    />

                    {(ready || resting) && metrics && !metrics.narrow && metrics.left > 70 && (
                        <>
                            {/* Закрытую книгу назад не листают — стрелка только вперёд. */}
                            {ready && (
                                <button
                                    type="button"
                                    onClick={() => flip(false)}
                                    className={deskChevron}
                                    style={{ left: Math.max(8, metrics.left / 2 - 22), color: chromeText }}
                                    aria-label="Предыдущая страница"
                                >
                                    <ChevronLeft size={26} />
                                </button>
                            )}
                            <button
                                type="button"
                                onClick={() => flip(true)}
                                className={deskChevron}
                                style={{ right: Math.max(8, metrics.left / 2 - 22), color: chromeText }}
                                aria-label={resting ? 'Открыть книгу' : 'Следующая страница'}
                            >
                                <ChevronRight size={26} />
                            </button>
                        </>
                    )}

                    {/* Центрирует контейнер, а не сдвиг кнопки: transform кнопки
                        занят анимацией появления. */}
                    <div className="pointer-events-none absolute inset-x-0 z-[6] flex justify-center" style={{ bottom: pillBottom }}>
                        <AnimatePresence>
                            {returnTo && ready && (
                                <motion.button
                                    key="return"
                                    type="button"
                                    onClick={goBack}
                                    className="pointer-events-auto flex items-center gap-1.5 rounded-full bg-slate-900/80 px-3.5 py-2 text-[12.5px] font-medium text-white shadow-lg backdrop-blur-md active:scale-95"
                                    initial={{ opacity: 0, y: 10, scale: 0.96 }}
                                    animate={{ opacity: 1, y: 0, scale: 1 }}
                                    exit={{ opacity: 0, y: 10, scale: 0.96 }}
                                    transition={{ type: 'spring', stiffness: 420, damping: 32 }}
                                >
                                    <Undo2 size={14} strokeWidth={2.2} />
                                    {returnTo.folio ? `Вернуться на стр. ${returnTo.folio}` : 'Вернуться'}
                                </motion.button>
                            )}
                        </AnimatePresence>
                    </div>

                    {phase === 'loading' && !flight && slow && (
                        <div className="absolute inset-0 z-[5] flex items-center justify-center gap-2 text-[13px]" style={{ color: chromeText }}>
                            <Loader2 size={16} className="animate-spin" /> Открываю книгу…
                        </div>
                    )}
                    {phase === 'error' && (
                        <div className="absolute inset-0 z-[5] flex flex-col items-center justify-center gap-4 px-6 text-center">
                            <p className="max-w-sm text-[14px]" style={{ color: chromeText }}>{error}</p>
                            <button type="button" className={iosBtnSecondary} onClick={close}>Закрыть</button>
                        </div>
                    )}
                </div>

                <footer
                    className="flex shrink-0 items-center justify-center text-[12px] tabular-nums"
                    style={{ height: 'calc(36px + env(safe-area-inset-bottom))', paddingBottom: 'env(safe-area-inset-bottom)', color: chromeText }}
                >
                    <AnimatePresence mode="wait" initial={false}>
                        <motion.span
                            key={phase === 'loading' ? 'loading' : resting ? 'resting' : 'ready'}
                            className="flex items-center gap-1.5"
                            initial={{ opacity: 0, y: 3 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -3 }}
                            transition={{ duration: 0.18 }}
                        >
                            {/* Без полёта обложки «Открываю книгу…» стоит посреди
                                сцены — второй раз внизу он не нужен. */}
                            {phase === 'loading' && flight && slow && (<><Loader2 size={13} className="animate-spin" /> Открываю книгу…</>)}
                            {/* Закрытая книга: процент «0 %» ни о чём, а как её
                                открыть — не очевидно. */}
                            {phase === 'ready' && resting && <span>Нажмите на обложку, чтобы открыть</span>}
                            {phase === 'ready' && !resting && (
                                <>
                                    {leftText && !showDone && <span>{leftText}</span>}
                                    {leftText && !showDone && <span className="opacity-40">·</span>}
                                    {showDone
                                        ? <span className="font-medium text-emerald-600">Закончено</span>
                                        : <span>{formatPercent(percent)}</span>}
                                </>
                            )}
                        </motion.span>
                    </AnimatePresence>
                </footer>

            </div>

            {/* Закрытая книга (bookOpening.js). Слой над ридером: при раскрытии
                обложка поднимается к зрителю и выходит за шапку. */}
            {flight && closedRect && (
                <>
                    {/* Тень поднятой обложки на странице под ней. */}
                    <div
                        ref={castRef}
                        aria-hidden="true"
                        className="pointer-events-none fixed"
                        style={{
                            left: closedRect.left, top: closedRect.top, width: closedRect.width, height: closedRect.height,
                            zIndex: READER_Z + 1,
                            opacity: 0,
                            transformOrigin: '0 50%',
                            background: 'linear-gradient(90deg, rgba(0,0,0,0.30), rgba(0,0,0,0.12) 38%, rgba(0,0,0,0) 100%)',
                            visibility: stage === 'open' ? 'hidden' : 'visible',
                        }}
                    />
                    <div
                        ref={hingeRef}
                        // В начале книги закрытая книга — кнопка: её открывают
                        // нажатием (и клавишами — Enter, пробел, стрелка).
                        role={resting ? 'button' : undefined}
                        tabIndex={resting ? 0 : undefined}
                        aria-hidden={resting ? undefined : 'true'}
                        aria-label={resting ? 'Открыть книгу' : undefined}
                        onClick={resting ? openBook : undefined}
                        onPointerDown={resting ? onCoverPointerDown : undefined}
                        onPointerUp={resting ? onCoverPointerUp : undefined}
                        onKeyDown={resting ? (event) => {
                            if (event.key === 'Enter') { event.preventDefault(); openBook(); }
                        } : undefined}
                        className={`fixed outline-none ${resting ? 'cursor-pointer' : 'pointer-events-none'}`}
                        style={{
                            left: closedRect.left, top: closedRect.top, width: closedRect.width, height: closedRect.height,
                            zIndex: READER_Z + 1,
                            transformOrigin: '0 50%',
                            willChange: 'transform, opacity',
                            visibility: stage === 'open' ? 'hidden' : 'visible',
                            touchAction: resting ? 'none' : undefined,
                        }}
                    >
                        <div ref={frameRef} className="absolute inset-0" style={{ transformOrigin: '0 0', willChange: 'transform' }}>
                            <div
                                className="absolute inset-0 overflow-hidden"
                                style={{
                                    // Переплёт: у корешка угол прямой, у обреза — чуть скруглён.
                                    borderRadius: '1px 4px 4px 1px',
                                    background: '#44403c',
                                    boxShadow: isNarrow ? 'none'
                                        : '0 1px 2px rgba(0,0,0,0.10), 0 16px 34px -10px rgba(0,0,0,0.40), 0 40px 64px -32px rgba(0,0,0,0.32)',
                                }}
                            >
                                {origin.coverUrl ? (
                                    <img
                                        ref={artRef}
                                        src={origin.coverUrl}
                                        alt=""
                                        draggable={false}
                                        decoding="sync"
                                        className="absolute"
                                        style={{
                                            ...coverBox(artAspect, closedRect),
                                            maxWidth: 'none',
                                            transformOrigin: '50% 50%',
                                            willChange: 'transform',
                                        }}
                                    />
                                ) : (
                                    <FlightPlaceholder
                                        title={origin.title}
                                        author={origin.author}
                                        scale={closedRect.width / origin.rect.width}
                                        iconRef={iconRef}
                                        textRef={textRef}
                                    />
                                )}
                                {/* Сгиб переплёта у корешка. */}
                                <div
                                    className="absolute inset-y-0 left-0 w-[9%]"
                                    style={{ background: 'linear-gradient(90deg, rgba(0,0,0,0.34), rgba(0,0,0,0.10) 18%, rgba(255,255,255,0.10) 30%, rgba(0,0,0,0.05) 48%, rgba(0,0,0,0) 100%)' }}
                                />
                                {/* Свет: обложка темнеет, отворачиваясь от него. */}
                                <div ref={lightRef} className="absolute inset-0 bg-black" style={{ opacity: 0 }} />
                            </div>
                            {/* Срез страниц у закрытой книги на столе — там же, где его
                                потом нарисует раскрытая книга. */}
                            {metrics?.mode === 'spread' && (
                                <div
                                    ref={edgeRef}
                                    className="absolute"
                                    style={{
                                        left: '100%', top: 2, bottom: 2,
                                        width: Math.round(1 + 5 * (1 - Math.min(100, Math.max(0, origin.percent || 0)) / 100)),
                                        borderRadius: '0 2px 2px 0',
                                        background: `repeating-linear-gradient(90deg, ${palette.edge} 0 1px, ${palette.paper} 1px 2px)`,
                                        boxShadow: '0 2px 6px rgba(0,0,0,0.10)',
                                        visibility: ['flying', 'closed', 'leaving'].includes(stage) ? 'visible' : 'hidden',
                                    }}
                                />
                            )}
                        </div>
                    </div>
                </>
            )}

            {/* Оглавление — своим слоем над закрытой книгой: она лежит выше
                ридера (при раскрытии поднимается над шапкой), и открытое на
                закрытой книге оглавление иначе оказалось бы под обложкой. */}
            <div
                className="fixed inset-0"
                style={{ zIndex: READER_Z + 2, fontFamily: APPLE_FONT, pointerEvents: tocOpen ? 'auto' : 'none' }}
            >
                <AnimatePresence>
                    {tocOpen && (
                        <LibraryToc
                            key="toc"
                            narrow={isNarrow}
                            night={night}
                            book={book}
                            toc={toc}
                            pages={pagination?.toc || []}
                            folio={folio}
                            total={total}
                            currentIndex={tocIndex}
                            onSelect={goTo}
                            onClose={() => setTocOpen(false)}
                        />
                    )}
                </AnimatePresence>
            </div>
        </>
    );
};

/* Оглавление — как в Apple Books: обложка, название, «Страница N из M», главы
   с номерами страниц справа. Текущая глава — единственный цвет в списке.
   Номера — из вёрстки под этот экран; пока она считается, номера появляются
   по мере готовности. На телефоне — шторка снизу (тянется вниз, чтобы
   закрыть), на компьютере — панель слева. */
const LibraryToc = ({ narrow, night, book, toc, pages, folio, total, currentIndex, onSelect, onClose }) => {
    const listRef = useRef(null);

    useEffect(() => {
        const current = listRef.current?.querySelector('[data-current="true"]');
        current?.scrollIntoView?.({ block: 'center' });
    }, []);

    const panelMotion = narrow
        ? { initial: { y: '100%' }, animate: { y: 0 }, exit: { y: '100%' } }
        : { initial: { x: '-100%' }, animate: { x: 0 }, exit: { x: '-100%' } };

    return (
        <div className="absolute inset-0 z-10" role="dialog" aria-label="Оглавление">
            <motion.button
                type="button"
                className="absolute inset-0 bg-black/25"
                onClick={onClose}
                aria-label="Закрыть оглавление"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
            />
            <motion.aside
                className={narrow
                    ? 'absolute inset-x-0 bottom-0 flex h-[88%] flex-col overflow-hidden rounded-t-[16px] shadow-[0_-10px_40px_rgba(0,0,0,0.18)] backdrop-blur-xl'
                    : 'absolute inset-y-0 left-0 flex w-[400px] max-w-[88vw] flex-col shadow-[0_0_50px_rgba(0,0,0,0.16)] backdrop-blur-xl'}
                style={{ background: night ? 'rgba(28,28,30,0.95)' : 'rgba(255,255,255,0.95)' }}
                {...panelMotion}
                transition={{ type: 'spring', stiffness: 380, damping: 38 }}
                drag={narrow ? 'y' : false}
                dragConstraints={{ top: 0, bottom: 0 }}
                dragElastic={{ top: 0, bottom: 0.6 }}
                dragListener={narrow}
                onDragEnd={(_event, info) => { if (info.offset.y > 110 || info.velocity.y > 600) onClose(); }}
            >
                {narrow && <div className="mx-auto mt-2 h-[5px] w-9 shrink-0 rounded-full bg-slate-300" aria-hidden="true" />}
                <div className={`flex items-center gap-3 border-b border-black/[0.06] px-4 pb-3 ${narrow ? 'pt-2' : 'pt-4'}`}>
                    {book?.cover_url ? (
                        <img src={book.cover_url} alt="" className="h-[62px] w-[42px] shrink-0 rounded-[4px] object-cover shadow-md ring-1 ring-black/5" />
                    ) : null}
                    <div className="min-w-0 flex-1">
                        <div className="truncate text-[15px] font-semibold text-slate-900">{book?.title}</div>
                        {book?.author && <div className="truncate text-[12.5px] text-slate-500">{book.author}</div>}
                        {folio && total ? (
                            <div className="text-[12px] tabular-nums text-slate-400">Страница {folio} из {total}</div>
                        ) : null}
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-black/[0.05] text-slate-500 transition hover:bg-black/[0.09] active:scale-95"
                        aria-label="Закрыть оглавление"
                    >
                        <X size={16} strokeWidth={2.2} />
                    </button>
                </div>
                {/* Список прокручивается сам: протяжка шторки вниз начинается
                    только с шапки, иначе прокрутка оглавления закрывала бы его. */}
                <ol
                    ref={listRef}
                    className="thin-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 py-1"
                    style={{ paddingBottom: 'max(4px, env(safe-area-inset-bottom))' }}
                    onPointerDownCapture={(event) => event.stopPropagation()}
                >
                    {toc.map((item, index) => {
                        const current = index === currentIndex;
                        const number = pages[index];
                        return (
                            <motion.li
                                key={`${item.href}-${index}`}
                                className="border-b border-black/[0.05] last:border-0"
                                initial={{ opacity: 0, x: narrow ? 0 : -8, y: narrow ? 6 : 0 }}
                                animate={{ opacity: 1, x: 0, y: 0 }}
                                transition={{ duration: 0.22, delay: Math.min(index, 14) * 0.018 }}
                            >
                                <button
                                    type="button"
                                    data-current={current ? 'true' : undefined}
                                    onClick={() => onSelect(item)}
                                    className={`flex w-full items-baseline gap-3 rounded-lg py-3 pr-2 text-left transition hover:bg-black/[0.03] active:bg-black/[0.06] ${current ? 'text-blue-600' : 'text-slate-800'}`}
                                    style={{ paddingLeft: `${10 + Math.min(item.depth || 0, 4) * 14}px` }}
                                >
                                    <span className={`min-w-0 flex-1 text-[14.5px] leading-snug ${item.depth ? '' : 'font-semibold'}`}>
                                        {item.title}
                                    </span>
                                    <span className={`shrink-0 text-[13px] tabular-nums transition-opacity ${current ? 'text-blue-500' : 'text-slate-400'} ${number ? 'opacity-100' : 'opacity-0'}`}>
                                        {number || '0'}
                                    </span>
                                </button>
                            </motion.li>
                        );
                    })}
                </ol>
            </motion.aside>
        </div>
    );
};

export default LibraryReader;
