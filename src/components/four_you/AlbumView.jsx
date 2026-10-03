import React, {
    forwardRef, memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
} from 'react';
import FaIcon from '../common/FaIcon';
import AnnotationLayer from './AnnotationLayer';
import { hasVisibleAnnotations, normalizeAnnotations, userName } from './annotations';
import {
    curlGeometry, easeInOut, easeOut, matrixCss, polygonCss, tapPath,
} from '../library/pageCurl';
import { OPENING_EASING, OPENING_MS, hingeFrames, reverseFrames } from '../library/bookOpening';
import {
    albumMetrics, clampPlace, pageLayout, pageSide, photoOfPlace, placeBackground, placeCount,
    placeLabel, placeOfPhoto, placePages, stackWidths, turnPlan, windowPages,
} from './albumLayout';
import './album.css';

/*
 * «Альбом» 4 You: те же фото, что в ленте, но книгой. Нажали «Альбом» —
 * карточки ленты разлетаются (lenta.jsx), на стол выходит закрытая книга с
 * обложкой «4 you» и раскрывается. Листают, как книгу «Библиотеки»: лист
 * загибается за пальцем или мышью (геометрия — library/pageCurl.js),
 * обложка раскрывается теми же кадрами (library/bookOpening.js). На
 * странице — фото с разметкой и последними комментариями; «Декор» открывает
 * тот же редактор, что и в ленте, и сохранённое сразу видно на странице.
 *
 * Этапы книги: entering → closed → opening → open → (shutting → closed) …
 * → leaving. Листать можно только в open. Пролистали назад с первой
 * страницы — книга закрывается и лежит, пока её не откроют снова.
 *
 * Страницы рисует React (фото, разметка, подписи), а где они лежат и как
 * загибаются — пишется прямо в стили, по кадру: React ведает только
 * содержимым и размером страницы, места и обрезку не трогает.
 */

const ENTER_MS = 560;
const ENTER_DELAY_MS = 140;
const COVER_HOLD_MS = 650;      // обложку успевают разглядеть до раскрытия
const LEAVE_MS = 340;
const TURN_MS = { spread: 640, single: 520 };
const WHEEL_STEP = 40;
const WHEEL_QUIET_MS = 220;     // жест колеса кончился — можно листать снова

const css = (element, styles) => { if (element) Object.assign(element.style, styles); };
const setClip = (element, value) => {
    if (!element) return;
    element.style.clipPath = value;
    element.style.webkitClipPath = value;
};
const HIDDEN_CLIP = 'polygon(0px 0px, 0px 0px, 0px 0px)';

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

/* Запустить с одного кадра: обложка и лист — одно движение. */
const together = (list) => {
    const start = document.timeline?.currentTime;
    if (start !== null && start !== undefined) list.forEach((animation) => { animation.startTime = start; });
    return list;
};

const prefersReducedMotion = () => (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
);

const keyOf = (page, images) => {
    if (!page) return '';
    if (page.kind === 'photo') return `p:${images[page.index]?.id ?? page.index}`;
    return page.kind;
};

const HeartGlyph = () => (
    <svg viewBox="0 0 24 22" aria-hidden="true">
        <path
            d="M12 21.4 10.6 20C5.4 15.4 2 12.3 2 8.5 2 5.4 4.4 3 7.5 3c1.7 0 3.4.8 4.5 2.1C13.1 3.8 14.8 3 16.5 3 19.6 3 22 5.4 22 8.5c0 3.8-3.4 6.9-8.6 11.5L12 21.4z"
            fill="currentColor"
        />
    </svg>
);

/* Одна страница книги: фото в рамке с разметкой, подпись, номер; или форзац. */
const AlbumPage = memo(forwardRef(({
    page, image, side, layout, width, height, canDelete, deleting, onDecorate, onDelete,
}, ref) => {
    const annotations = image?.annotations;
    const comments = Array.isArray(annotations?.comments) ? annotations.comments.slice(-2) : [];
    return (
        <div ref={ref} className={`fy-album-page fy-album-page--${side} fy-album-page--${page.kind}`} style={{ width, height }}>
            {page.kind === 'photo' && image && layout && (
                <>
                    <div
                        className="fy-album-photo"
                        style={{
                            left: layout.photo.left, top: layout.photo.top,
                            width: layout.photo.width, height: layout.photo.height,
                        }}
                    >
                        <img className="fy-album-photo-img" src={image.preview_url} alt="" decoding="async" draggable="false" />
                        {image.display_url && image.display_url !== image.preview_url && (
                            <img
                                className="fy-album-photo-img fy-album-photo-hi"
                                src={image.display_url}
                                alt=""
                                decoding="async"
                                draggable="false"
                                onLoad={(event) => event.currentTarget.classList.add('is-ready')}
                            />
                        )}
                        {hasVisibleAnnotations(annotations) && (
                            <AnnotationLayer annotations={normalizeAnnotations(annotations)} />
                        )}
                        <div className="fy-album-page-actions" data-album-control>
                            <button type="button" className="fy-album-action" onClick={() => onDecorate(image)} title="Декорировать фото">
                                <FaIcon className="fas fa-pencil" />
                                <span>Декор</span>
                            </button>
                            {canDelete && (
                                <button
                                    type="button"
                                    className="fy-album-action is-danger"
                                    onClick={() => onDelete(image)}
                                    disabled={deleting}
                                    title="Удалить это фото"
                                >
                                    <FaIcon className={`fas ${deleting ? 'fa-spinner fa-spin' : 'fa-trash-alt'}`} />
                                    <span>{deleting ? 'Удаление…' : 'Удалить'}</span>
                                </button>
                            )}
                        </div>
                    </div>
                    {comments.length > 0 && (
                        <div
                            className="fy-album-caption"
                            style={{ left: layout.caption.left, top: layout.caption.top, width: layout.caption.width }}
                        >
                            {comments.map((comment, index) => (
                                <p key={`${comment.at || ''}-${index}`}>
                                    <b>{userName(comment.by)}</b>
                                    {comment.text}
                                </p>
                            ))}
                        </div>
                    )}
                    <span className="fy-album-folio">{page.index + 1}</span>
                </>
            )}
            {(page.kind === 'front' || page.kind === 'back') && (
                <div className="fy-album-endpaper">
                    {page.kind === 'front' && <span className="fy-album-endpaper-mark">4 you</span>}
                </div>
            )}
        </div>
    );
}));

const AlbumView = ({
    images,
    canDelete = false,
    deletingId = '',
    editorOpen = false,
    closing = false,
    onDecorate,
    onDelete,
    onBackground,
    onLeave,
    onExited,
    onRequestClose,
}) => {
    const reduced = useMemo(prefersReducedMotion, []);

    const rootRef = useRef(null);
    const stageRef = useRef(null);
    const hostRef = useRef(null);          // раскрытая книга (сдвиг pan при раскрытии)
    const clipperRef = useRef(null);
    const shadowRef = useRef(null);
    const edgeLeftRef = useRef(null);
    const edgeRightRef = useRef(null);
    const shadeRef = useRef(null);
    const flapShadowRef = useRef(null);
    const flapPaperRef = useRef(null);
    const flapPhotoRef = useRef(null);
    const flapGlossRef = useRef(null);
    const leafShadeRef = useRef(null);
    const closedRef = useRef(null);        // закрытая книга: выход на стол и уход
    const coverRef = useRef(null);
    const coverLightRef = useRef(null);
    const castRef = useRef(null);
    const coverEdgeRef = useRef(null);

    const pageEls = useRef(new Map());     // ключ страницы → её элемент
    const refCallbacks = useRef(new Map());
    const metricsRef = useRef(null);
    const pendingMetricsRef = useRef(null);
    const placeRef = useRef(0);
    const imagesRef = useRef(images);
    const countRef = useRef(images.length);
    const layoutRef = useRef(null);
    const stageNowRef = useRef(reduced ? 'open' : 'entering');
    const leaveRef = useRef(false);
    const autoOpenRef = useRef(true);
    const holdTimerRef = useRef(0);
    const animsRef = useRef([]);
    const turnRef = useRef(null);
    const rafRef = useRef(0);
    const runRef = useRef(0);
    const gestureRef = useRef(null);
    const editorOpenRef = useRef(editorOpen);
    const callbacksRef = useRef({});

    const [metrics, setMetrics] = useState(null);
    const [stage, setStage] = useState(stageNowRef.current);
    const [place, setPlace] = useState(0);
    const [resting, setResting] = useState(false);
    // Страницы (фото, разметка) монтируются, когда закрытая книга уже легла
    // на стол: монтаж — заметная работа, а пока карточки ленты разлетаются и
    // книга выходит, кадры не должны останавливаться. Раскрыть книгу раньше
    // нельзя — обложка до этого не нажимается.
    const [pagesReady, setPagesReady] = useState(reduced);

    imagesRef.current = images;
    countRef.current = images.length;
    editorOpenRef.current = editorOpen;
    callbacksRef.current = { onDecorate, onDelete, onBackground, onLeave, onExited, onRequestClose };

    const goTo = (next) => {
        stageNowRef.current = next;
        setStage(next);
    };

    const layout = useMemo(() => (metrics ? pageLayout(metrics.pageWidth, metrics.height) : null), [metrics]);
    layoutRef.current = layout;

    const refFor = (key) => {
        let callback = refCallbacks.current.get(key);
        if (!callback) {
            callback = (node) => {
                if (node) pageEls.current.set(key, node);
                else pageEls.current.delete(key);
            };
            refCallbacks.current.set(key, callback);
        }
        return callback;
    };

    const elOf = (page) => (page ? pageEls.current.get(keyOf(page, imagesRef.current)) || null : null);

    /* ---------- раскладка страниц ---------- */

    const hideCurl = () => {
        css(flapShadowRef.current, { visibility: 'hidden' });
        css(flapGlossRef.current, { visibility: 'hidden' });
        css(shadeRef.current, { visibility: 'hidden' });
    };

    const paintStacks = () => {
        const m = metricsRef.current;
        if (!m) return;
        const spread = m.mode === 'spread';
        const { left, right } = stackWidths(placeRef.current, placeCount(m.mode, countRef.current));
        css(edgeLeftRef.current, { width: `${left}px`, display: spread ? '' : 'none' });
        css(edgeRightRef.current, { width: `${right}px`, display: spread ? '' : 'none' });
    };

    /* Страницы места — на свои стороны, остальные спрятаны (они готовы к
       ходу, но лишняя отрисовка на каждом кадре ни к чему). */
    const arrange = () => {
        const m = metricsRef.current;
        if (!m) return;
        const shown = placePages(m.mode, placeRef.current, countRef.current)
            .map((page) => keyOf(page, imagesRef.current));
        pageEls.current.forEach((node, key) => {
            setClip(node, '');
            const at = shown.indexOf(key);
            if (at < 0) {
                css(node, { visibility: 'hidden', zIndex: '0', transform: '', opacity: '', transformOrigin: '0 0', left: '0px' });
                return;
            }
            css(node, {
                left: `${m.mode === 'spread' && at === 1 ? m.pageWidth : 0}px`,
                visibility: 'visible', zIndex: '2', transform: '', opacity: '', transformOrigin: '0 0',
            });
        });
        hideCurl();
        paintStacks();
    };

    /* ---------- загиб (как paintCurl в readerEngine.js) ---------- */

    const paintCurl = (geometry) => {
        const turn = turnRef.current;
        if (!turn) return;
        if (!geometry) {
            hideCurl();
            if (turn.els.flap) css(turn.els.flap, { visibility: 'hidden' });
            setClip(turn.els.leaf, '');
            return;
        }
        const W = turn.W;
        setClip(turn.els.leaf, geometry.kept.length >= 3 ? polygonCss(geometry.kept) : HIDDEN_CLIP);

        const removed = polygonCss(geometry.removed);
        const { angle, start } = geometry.gradient;
        const depth = geometry.depth;
        const lift = Math.sin(Math.PI * geometry.progress);      // 0 у краёв хода, 1 посередине
        // Свет гаснет к краям хода: первый и последний кадры совпадают с
        // лежащими страницами, и в конце хода ничего не «щёлкает».
        const k = Math.min(1, 6.4 * geometry.progress * (1 - geometry.progress));
        const [a, b, c, d, e, f] = geometry.matrix;
        const reflect = matrixCss([a, b, c, d, e, f]);
        const left = `${turn.leafX}px`;

        // Бумага оборота — с тенью от всего листа.
        css(flapPaperRef.current, { left, transform: reflect });
        setClip(flapPaperRef.current, removed);
        if (flapShadowRef.current) {
            flapShadowRef.current.style.filter = `drop-shadow(0 2px 10px rgba(0,0,0,${(0.24 * k).toFixed(3)}))`;
            flapShadowRef.current.style.visibility = 'visible';
        }

        // На развороте оборот листа — следующая левая страница, зеркалом внутри
        // отражения (x → W − x), чтобы фото на ней не было перевёрнутым.
        if (turn.spread && turn.els.flap) {
            css(turn.els.flap, {
                left, transform: matrixCss([-a, -b, c, d, a * W + e, b * W + f]), zIndex: '5', visibility: 'visible',
            });
            setClip(turn.els.flap, polygonCss(geometry.removed.map((point) => ({ x: W - point.x, y: point.y }))));
        }

        // Свет на обороте: тень у сгиба, блик по изгибу, тень к краю.
        const gloss = flapGlossRef.current;
        if (gloss) {
            css(gloss, { left, transform: reflect, visibility: 'visible' });
            setClip(gloss, removed);
            const sheen = turn.spread ? 0.12 : 0.5;
            gloss.style.backgroundImage = `linear-gradient(${angle}deg,`
                + ` rgba(0,0,0,${((0.12 + 0.10 * lift) * k).toFixed(3)}) ${start}px,`
                + ` rgba(255,255,255,0) ${start + Math.min(24, depth * 0.1)}px,`
                + ` rgba(255,255,255,${(sheen * k).toFixed(3)}) ${start + depth * 0.42}px,`
                + ` rgba(0,0,0,${(0.06 * k).toFixed(3)}) ${start + depth}px)`;
        }

        // Тень поднятого листа на открывшейся странице — у самого сгиба.
        const shade = shadeRef.current;
        if (shade) {
            const reach = Math.min(W * 0.3, 20 + depth * 0.24);
            shade.style.left = left;
            setClip(shade, removed);
            shade.style.backgroundImage = `linear-gradient(${angle}deg,`
                + ` rgba(0,0,0,${((0.14 + 0.2 * lift) * k).toFixed(3)}) ${start}px,`
                + ` rgba(0,0,0,0) ${start + reach}px)`;
            shade.style.visibility = 'visible';
        }
    };

    const setPoint = (point) => {
        const turn = turnRef.current;
        if (!turn) return;
        turn.point = point;
        paintCurl(curlGeometry(point, turn.corner, turn.W, turn.H));
    };

    /* Разложить страницы на время хода: места, слои и видимость. */
    const stagePages = (turn) => {
        const { els, spread, W } = turn;
        const used = new Set([els.still, els.under, els.leaf, els.flap].filter(Boolean));
        pageEls.current.forEach((node) => {
            setClip(node, '');
            if (!used.has(node)) css(node, { visibility: 'hidden', transform: '', zIndex: '0' });
        });
        const put = (node, left, z) => css(node, {
            left: `${left}px`, zIndex: String(z), visibility: 'visible', transform: '', opacity: '', transformOrigin: '0 0',
        });
        if (spread) {
            put(els.still, 0, 2);
            put(els.under, W, 1);
            put(els.leaf, W, 3);
            css(els.flap, { left: `${W}px`, zIndex: '5', visibility: 'hidden', transform: '', opacity: '', transformOrigin: '0 0' });
        } else {
            put(els.under, 0, 1);
            put(els.leaf, 0, 3);
        }
        // На одной странице сквозь бумагу оборота чуть просвечивает то же фото.
        const photo = flapPhotoRef.current;
        const box = layoutRef.current?.photo;
        const image = !spread && turn.leaf?.kind === 'photo' ? imagesRef.current[turn.leaf.index] : null;
        if (photo && image && box) {
            if (photo.getAttribute('src') !== image.preview_url) photo.setAttribute('src', image.preview_url);
            css(photo, {
                display: '', left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px`,
            });
        } else {
            css(photo, { display: 'none' });
        }
    };

    /* Начать ход. -> false, если листать некуда или книга не раскрыта. */
    const beginTurn = (forward, fromTop) => {
        const m = metricsRef.current;
        if (!m || stageNowRef.current !== 'open' || turnRef.current) return false;
        const plan = turnPlan(m.mode, placeRef.current, forward, countRef.current);
        if (!plan) return false;
        const spread = m.mode === 'spread';
        const els = {
            leaf: elOf(plan.leaf),
            under: elOf(plan.under),
            still: spread ? elOf(plan.still) : null,
            flap: spread ? elOf(plan.flap) : null,
        };
        if (!els.leaf || !els.under || (spread && (!els.still || !els.flap))) return false;
        const corner = { x: m.pageWidth, y: fromTop ? 0 : m.height };
        const turn = {
            ...plan,
            els,
            forward,
            spread,
            corner,
            W: m.pageWidth,
            H: m.height,
            leafX: spread ? m.pageWidth : 0,
            flat: { x: corner.x - 0.6, y: corner.y },
            turned: { x: -m.pageWidth - 2, y: corner.y },
            completed: undefined,
        };
        turn.point = forward ? turn.flat : turn.turned;
        turnRef.current = turn;
        stagePages(turn);
        rootRef.current?.classList.add('is-turning');
        setPoint(turn.point);
        return true;
    };

    /* Ход закончен: страницы встают на новые (или прежние) места. */
    const settle = (completed) => {
        const turn = turnRef.current;
        if (!turn) return;
        cancelAnimationFrame(rafRef.current);
        turnRef.current = null;
        rootRef.current?.classList.remove('is-turning');
        if (completed) placeRef.current = turn.target;
        arrange();
        if (completed) setPlace(turn.target);
    };

    /* -> Promise<boolean>: true — дошла до конца, false — её прервали. */
    const animate = (from, to, duration, easing, path = null) => {
        cancelAnimationFrame(rafRef.current);
        runRef.current += 1;
        const run = runRef.current;
        if (reduced || duration <= 0) {
            setPoint(to);
            return Promise.resolve(true);
        }
        return new Promise((resolve) => {
            const started = performance.now();
            const step = (now) => {
                if (runRef.current !== run || !turnRef.current) { resolve(false); return; }
                const t = Math.min(1, (now - started) / duration);
                const k = easing(t);
                const point = path ? path(k) : { x: from.x + (to.x - from.x) * k, y: from.y + (to.y - from.y) * k };
                setPoint(point);
                if (t < 1) rafRef.current = requestAnimationFrame(step);
                else resolve(true);
            };
            rafRef.current = requestAnimationFrame(step);
        });
    };

    /* Довести ход: completed — перевернуть до конца, иначе вернуть лист. */
    const finishTurn = (completed, duration) => {
        const turn = turnRef.current;
        if (!turn) return Promise.resolve();
        turn.completed = completed;
        const goal = turn.forward === completed ? turn.turned : turn.flat;
        return animate(turn.point, goal, duration, easeOut).then((finished) => {
            if (finished && turnRef.current === turn) settle(completed);
        });
    };

    /* Прервать ход сейчас же: доворачивающийся — тем исходом, к которому шёл;
       ход под пальцем — вернуть лист. */
    const abortTurn = () => {
        if (!turnRef.current) return;
        runRef.current += 1;
        settle(turnRef.current.completed === true);
    };

    /* Ход, который доворачивается сам (палец отпущен), досчитать мгновенно. */
    const completeNow = () => {
        const turn = turnRef.current;
        if (!turn || turn.completed === undefined) return false;
        runRef.current += 1;
        settle(turn.completed);
        return true;
    };

    /* ---------- обложка ---------- */

    const openBook = () => {
        if (stageNowRef.current !== 'closed' || leaveRef.current) return;
        clearTimeout(holdTimerRef.current);
        autoOpenRef.current = false;
        setResting(false);
        goTo('opening');
    };

    /* Пролистали назад с первой страницы — книга закрывается и лежит. */
    const shutBook = () => {
        if (stageNowRef.current !== 'open' || turnRef.current) return;
        goTo('shutting');
    };

    /* Нажатие, клавиша, колесо: полный ход по дуге. Быстрые нажатия листают
       быстро: доворачивающийся ход досчитывается сразу. */
    const flip = (forward) => {
        if (stageNowRef.current === 'closed') {
            if (forward) openBook();
            return;
        }
        if (stageNowRef.current !== 'open') return;
        if (turnRef.current && !completeNow()) return;
        if (!beginTurn(forward, false)) {
            if (!forward && placeRef.current === 0) shutBook();
            return;
        }
        const turn = turnRef.current;
        turn.completed = true;
        const duration = turn.spread ? TURN_MS.spread : TURN_MS.single;
        const path = forward
            ? (k) => tapPath(k, turn.corner, turn.W, turn.H)
            : (k) => tapPath(1 - k, turn.corner, turn.W, turn.H);
        animate(null, forward ? turn.turned : turn.flat, duration, easeInOut, path).then((finished) => {
            if (finished && turnRef.current === turn) settle(true);
        });
    };

    /* dx, dy — сдвиг пальца от точки касания. Вперёд палец держит угол;
       назад лист приходит из перевёрнутого положения вдвое быстрее пальца. */
    const dragMove = (dx, dy) => {
        const turn = turnRef.current;
        if (!turn || turn.completed !== undefined) return;
        if (turn.forward) setPoint({ x: turn.corner.x + dx, y: turn.corner.y + dy });
        else setPoint({ x: turn.turned.x + 2 * Math.max(0, dx), y: turn.corner.y + dy * 0.5 });
    };

    const dragEnd = (velocityX) => {
        const turn = turnRef.current;
        if (!turn || turn.completed !== undefined) return;
        const geometry = curlGeometry(turn.point, turn.corner, turn.W, turn.H);
        const progress = geometry ? geometry.progress : (turn.forward ? 0 : 1);
        const completed = turn.forward
            ? (progress > 0.22 || velocityX < -0.35) && velocityX < 0.35
            : (progress < 0.78 || velocityX > 0.35) && velocityX > -0.35;
        const remaining = completed === turn.forward ? 1 - progress : progress;
        finishTurn(completed, Math.max(170, Math.round(480 * remaining)));
    };

    /* Где нажали: левая или правая страница разворота, точка на странице. */
    const locate = (clientX, clientY) => {
        const m = metricsRef.current;
        const host = hostRef.current;
        if (!m || !host) return null;
        const rect = host.getBoundingClientRect();
        const x = clientX - rect.left;
        const y = clientY - rect.top;
        const spread = m.mode === 'spread';
        const side = spread ? (x < rect.width / 2 ? 'left' : 'right') : 'single';
        return {
            side,
            y,
            pageX: side === 'right' ? x - rect.width / 2 : x,
            inside: x >= 0 && y >= 0 && x <= rect.width && y <= rect.height,
        };
    };

    const engine = useRef({});
    engine.current = { flip, beginTurn, dragMove, dragEnd, completeNow, shutBook, openBook, locate, abortTurn, arrange };

    /* ---------- размер сцены ---------- */

    const applyMetrics = (next) => {
        if (turnRef.current) abortTurn();
        const previous = metricsRef.current;
        metricsRef.current = next;
        if (previous && previous.mode !== next.mode && countRef.current > 0) {
            const photo = Math.min(countRef.current - 1, photoOfPlace(previous.mode, placeRef.current));
            const moved = clampPlace(next.mode, placeOfPhoto(next.mode, photo), countRef.current);
            placeRef.current = moved;
            setPlace(moved);
        }
        setMetrics(next);
    };
    const applyMetricsRef = useRef(applyMetrics);
    applyMetricsRef.current = applyMetrics;

    useLayoutEffect(() => {
        const node = stageRef.current;
        if (!node) return undefined;
        const measure = () => {
            const next = albumMetrics(node.clientWidth, node.clientHeight);
            const now = metricsRef.current;
            if (now && now.mode === next.mode && now.pageWidth === next.pageWidth && now.height === next.height
                && now.left === next.left && now.top === next.top) return;
            // Книга в движении (выход, раскрытие, закрытие): перекладка — после.
            if (now && ['entering', 'opening', 'shutting', 'leaving'].includes(stageNowRef.current)) {
                pendingMetricsRef.current = next;
                return;
            }
            applyMetricsRef.current(next);
        };
        measure();
        if (typeof ResizeObserver === 'undefined') {
            window.addEventListener('resize', measure);
            return () => window.removeEventListener('resize', measure);
        }
        const observer = new ResizeObserver(measure);
        observer.observe(node);
        return () => observer.disconnect();
    }, []);

    // Отложенная перекладка — как только книга встала.
    useEffect(() => {
        if ((stage === 'open' || stage === 'closed') && pendingMetricsRef.current) {
            const next = pendingMetricsRef.current;
            pendingMetricsRef.current = null;
            applyMetricsRef.current(next);
        }
    }, [stage]);

    // Фото добавили или удалили: ход досчитать, место прижать к краю книги.
    const structureKey = images.map((image) => image.id).join('|');
    useLayoutEffect(() => {
        if (turnRef.current) abortTurn();
        const m = metricsRef.current;
        if (!m) return;
        const clamped = clampPlace(m.mode, placeRef.current, countRef.current);
        if (clamped !== placeRef.current) {
            placeRef.current = clamped;
            setPlace(clamped);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [structureKey]);

    useEffect(() => {
        if (images.length === 0) callbacksRef.current.onRequestClose?.();
    }, [images.length]);

    // После каждой отрисовки — страницы по местам (кроме хода и раскрытия:
    // там листом ведает кадр анимации).
    useLayoutEffect(() => {
        if (!metricsRef.current || turnRef.current) return;
        if (stageNowRef.current === 'opening' || stageNowRef.current === 'shutting') return;
        arrange();
    });

    /* ---------- этапы книги ---------- */

    // Выход закрытой книги на стол; обложку дают разглядеть и раскрывают.
    const enteredRef = useRef(false);
    useLayoutEffect(() => {
        if (stage !== 'entering' || !metrics || enteredRef.current) return;
        enteredRef.current = true;
        const animation = play(closedRef.current, [
            { opacity: 0, transform: 'translateY(26px) scale(0.96)' },
            { opacity: 1, transform: 'translateY(0px) scale(1)' },
        ], { duration: ENTER_MS, delay: ENTER_DELAY_MS, easing: OPENING_EASING.flyIn });
        animsRef.current = [animation].filter(Boolean);
        settled(animsRef.current).then((done) => {
            if (!done) return;
            if (leaveRef.current) { goTo('leaving'); return; }
            setPagesReady(true);
            goTo('closed');
            holdTimerRef.current = setTimeout(() => {
                if (stageNowRef.current === 'closed' && autoOpenRef.current && !leaveRef.current) {
                    autoOpenRef.current = false;
                    goTo('opening');
                }
            }, COVER_HOLD_MS);
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [stage, metrics]);

    // Сразу раскрытая книга (меньше движения): обложки нет вовсе.
    useLayoutEffect(() => {
        if (!reduced || !metrics) return;
        if (stageNowRef.current === 'open') {
            css(coverRef.current, { visibility: 'hidden' });
            css(castRef.current, { visibility: 'hidden' });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [metrics]);

    const hingeParts = () => {
        const m = metricsRef.current;
        const spread = m.mode === 'spread';
        const [leftPage] = placePages(m.mode, placeRef.current, countRef.current);
        const leaf = spread ? elOf(leftPage) : null;
        css(clipperRef.current, { overflow: 'visible' });   // лист в полёте крупнее книги
        if (leaf) css(leaf, { transformOrigin: '100% 50%', zIndex: '3', visibility: 'visible' });
        css(leafShadeRef.current, { visibility: leaf ? 'visible' : 'hidden' });
        css(coverRef.current, { visibility: 'visible' });
        css(castRef.current, { visibility: 'visible' });
        const frames = hingeFrames({ spread, pageWidth: m.pageWidth, shift: spread ? m.pageWidth / 2 : 0 });
        return { spread, leaf, frames };
    };

    const finishOpening = (list) => {
        // Обложку прячем раньше, чем снимаем её анимации: иначе на кадр она
        // вернулась бы на место закрытой книги.
        css(coverRef.current, { visibility: 'hidden' });
        css(castRef.current, { visibility: 'hidden' });
        list.forEach((animation) => animation.cancel());
        animsRef.current = [];
        css(clipperRef.current, { overflow: '' });
        css(leafShadeRef.current, { visibility: 'hidden' });
        pageEls.current.forEach((node) => css(node, { transformOrigin: '0 0' }));
        goTo('open');
        arrange();
        if (leaveRef.current) goTo('shutting');
    };

    // Раскрытие: обложка поворачивается вокруг корешка, на развороте тот же
    // лист ложится налево форзацем — одним движением, с одного кадра.
    useLayoutEffect(() => {
        if (stage !== 'opening' || !metricsRef.current) return;
        arrange();
        css(coverEdgeRef.current, { visibility: 'hidden' });   // срез страниц дальше рисует книга
        animsRef.current.forEach((animation) => animation.cancel());
        if (reduced) { finishOpening([]); return; }
        const { spread, leaf, frames } = hingeParts();
        const options = {
            duration: spread ? OPENING_MS.hingeSpread : OPENING_MS.hingeSingle,
            easing: OPENING_EASING.hinge,
        };
        const list = together([
            play(coverRef.current, frames.cover, options),
            play(coverLightRef.current, frames.coverLight, options),
            play(castRef.current, frames.cast, options),
            play(hostRef.current, frames.pan, options),
            play(leaf, frames.leaf, options),
            play(leafShadeRef.current, frames.leafShade, options),
            play(shadowRef.current, frames.bookShadow, options),
            play(edgeLeftRef.current, frames.edgeLeft, options),
        ].filter(Boolean));
        animsRef.current = list;
        settled(list).then((done) => { if (done && stageNowRef.current === 'opening') finishOpening(list); });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [stage]);

    // Закрытие — те же кадры в обратную сторону. Последние кадры держатся,
    // пока книгу не откроют снова: под обложкой страниц не видно.
    useLayoutEffect(() => {
        if (stage !== 'shutting' || !metricsRef.current) return;
        if (turnRef.current) abortTurn();
        arrange();
        animsRef.current.forEach((animation) => animation.cancel());
        const done = () => {
            css(clipperRef.current, { overflow: '' });
            css(coverEdgeRef.current, { visibility: '' });
            if (leaveRef.current) {
                goTo('leaving');
                return;
            }
            setResting(true);
            goTo('closed');
        };
        if (reduced) {
            css(coverRef.current, { visibility: 'visible' });
            done();
            return;
        }
        const { spread, leaf, frames } = hingeParts();
        const options = {
            duration: spread ? OPENING_MS.closeSpread : OPENING_MS.closeSingle,
            easing: OPENING_EASING.close,
        };
        const back = (keyframes) => (keyframes ? reverseFrames(keyframes) : null);
        const list = together([
            play(coverRef.current, back(frames.cover), options),
            play(coverLightRef.current, back(frames.coverLight), options),
            play(castRef.current, back(frames.cast), options),
            play(hostRef.current, back(frames.pan), options),
            play(leaf, back(frames.leaf), options),
            play(leafShadeRef.current, back(frames.leafShade), options),
            play(shadowRef.current, back(frames.bookShadow), options),
            play(edgeLeftRef.current, back(frames.edgeLeft), options),
        ].filter(Boolean));
        animsRef.current = list;
        settled(list).then((finished) => { if (finished && stageNowRef.current === 'shutting') done(); });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [stage]);

    // Уход: закрытая книга тает и опускается, карточки ленты возвращаются.
    useLayoutEffect(() => {
        if (stage !== 'leaving') return;
        callbacksRef.current.onLeave?.();
        if (reduced) {
            callbacksRef.current.onExited?.();
            return;
        }
        const list = animsRef.current;
        animsRef.current = [];
        const animation = play(closedRef.current, [
            { opacity: 1, transform: 'translateY(0px) scale(1)' },
            { opacity: 0, transform: 'translateY(16px) scale(0.97)' },
        ], { duration: LEAVE_MS, easing: 'cubic-bezier(0.4, 0, 0.2, 1)' });
        // Кадры закрытия держали обложку на месте — снимаем их уже под уходом.
        list.forEach((item) => item.cancel());
        settled([animation]).then(() => callbacksRef.current.onExited?.());
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [stage]);

    // Просьба вернуться в ленту (кнопка «Лента», Escape).
    useEffect(() => {
        if (!closing) return;
        leaveRef.current = true;
        clearTimeout(holdTimerRef.current);
        const now = stageNowRef.current;
        if (now === 'open') {
            if (turnRef.current) abortTurn();
            if (reduced) goTo('leaving');
            else goTo('shutting');
        } else if (now === 'closed') {
            goTo('leaving');
        }
        // entering, opening, shutting — их окончание само уведёт книгу.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [closing]);

    useEffect(() => () => {
        clearTimeout(holdTimerRef.current);
        cancelAnimationFrame(rafRef.current);
        runRef.current += 1;
    }, []);

    /* ---------- фон сцены ---------- */

    const mode = metrics?.mode || 'spread';
    const count = images.length;
    const safePlace = clampPlace(mode, place, count);
    const background = metrics ? placeBackground(mode, safePlace, images) : 'none';
    useEffect(() => {
        // Раскрытая книга несёт фон своих фото; закрытая — без фона. Пока
        // книга закрывается и уходит, фон остаётся и гаснет вместе с ней.
        if (stage === 'opening' || stage === 'open') callbacksRef.current.onBackground?.(background);
        else if (stage === 'closed' || stage === 'entering') callbacksRef.current.onBackground?.('none');
    }, [stage, background]);

    /* ---------- жесты, клавиши, колесо ---------- */

    const onPointerDown = (event) => {
        if (!event.isPrimary || (event.pointerType === 'mouse' && event.button !== 0)) return;
        if (event.target.closest?.('[data-album-control]')) return;
        if (stageNowRef.current !== 'open') return;
        const spot = locate(event.clientX, event.clientY);
        if (!spot || !spot.inside) return;
        if (turnRef.current) completeNow();
        const now = performance.now();
        gestureRef.current = {
            id: event.pointerId, x: event.clientX, y: event.clientY, spot,
            started: false, lastX: event.clientX, lastT: now, vx: 0,
        };
    };

    useEffect(() => {
        const onMove = (event) => {
            const gesture = gestureRef.current;
            if (!gesture || event.pointerId !== gesture.id) return;
            const now = performance.now();
            const dt = now - gesture.lastT;
            if (dt > 0) {
                gesture.vx = 0.7 * ((event.clientX - gesture.lastX) / dt) + 0.3 * gesture.vx;
                gesture.lastX = event.clientX;
                gesture.lastT = now;
            }
            const dx = event.clientX - gesture.x;
            const dy = event.clientY - gesture.y;
            if (!gesture.started) {
                if (Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(dy)) return;
                const m = metricsRef.current;
                const forward = dx < 0;
                // Разворот: вперёд тянут правую страницу, назад — левую.
                if (!m || (m.mode === 'spread' && gesture.spot.side !== (forward ? 'right' : 'left'))) {
                    gestureRef.current = null;
                    return;
                }
                if (!engine.current.beginTurn(forward, gesture.spot.y < m.height / 2)) {
                    gestureRef.current = null;
                    if (!forward && placeRef.current === 0) engine.current.shutBook();
                    return;
                }
                gesture.started = true;
            }
            engine.current.dragMove(dx, dy);
        };
        const onUp = (event) => {
            const gesture = gestureRef.current;
            if (!gesture || event.pointerId !== gesture.id) return;
            gestureRef.current = null;
            if (gesture.started) {
                engine.current.dragEnd(event.type === 'pointercancel' ? 0 : gesture.vx);
                return;
            }
            if (event.type === 'pointercancel') return;
            if (Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) > 8) return;
            const m = metricsRef.current;
            if (!m) return;
            const forward = m.mode === 'spread'
                ? gesture.spot.side === 'right'
                : gesture.spot.pageX >= m.pageWidth * 0.4;
            engine.current.flip(forward);
        };
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
        window.addEventListener('pointercancel', onUp);
        return () => {
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', onUp);
            window.removeEventListener('pointercancel', onUp);
        };
    }, []);

    useEffect(() => {
        const onKey = (event) => {
            if (editorOpenRef.current || event.defaultPrevented) return;
            if (event.target?.closest?.('input, textarea, select, [contenteditable="true"]')) return;
            if (event.key === 'ArrowRight') { event.preventDefault(); engine.current.flip(true); }
            else if (event.key === 'ArrowLeft') { event.preventDefault(); engine.current.flip(false); }
            else if (event.key === 'Escape') callbacksRef.current.onRequestClose?.();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, []);

    // Колесо и свайп трекпада: один жест — один лист. Нативный слушатель с
    // passive: false — иначе свайп трекпада уводил бы браузер назад по истории.
    useEffect(() => {
        const root = rootRef.current;
        if (!root) return undefined;
        let sum = 0;
        let locked = false;
        let quietTimer = 0;
        const onWheel = (event) => {
            if (event.ctrlKey) return;              // масштаб страницы, не листание
            event.preventDefault();
            clearTimeout(quietTimer);
            quietTimer = setTimeout(() => { locked = false; sum = 0; }, WHEEL_QUIET_MS);
            if (locked) return;
            sum += Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
            if (Math.abs(sum) < WHEEL_STEP) return;
            locked = true;
            const forward = sum > 0;
            sum = 0;
            engine.current.flip(forward);
        };
        root.addEventListener('wheel', onWheel, { passive: false });
        return () => {
            root.removeEventListener('wheel', onWheel);
            clearTimeout(quietTimer);
        };
    }, []);

    const handleDecorate = useCallback((image) => callbacksRef.current.onDecorate?.(image), []);
    const handleDelete = useCallback((image) => callbacksRef.current.onDelete?.(image), []);

    /* ---------- разметка ---------- */

    const total = placeCount(mode, count);
    const pages = metrics && pagesReady ? windowPages(mode, safePlace, count) : [];
    const closedBox = metrics ? {
        left: metrics.left + (metrics.mode === 'spread' ? metrics.pageWidth / 2 : 0),
        top: metrics.top,
        width: metrics.pageWidth,
        height: metrics.height,
    } : null;
    const coverActive = stage === 'closed';
    const label = stage === 'closed' && resting
        ? 'Нажмите на обложку, чтобы открыть'
        : placeLabel(mode, safePlace, count);
    const canBack = stage === 'open';
    const canForward = stage === 'closed' || (stage === 'open' && safePlace < total - 1);

    return (
        <div
            ref={rootRef}
            className="fy-album"
            data-stage={stage}
            data-mode={mode}
            role="region"
            aria-label="Альбом 4 You"
            style={metrics ? { '--fy-page-w': `${metrics.pageWidth}px` } : undefined}
        >
            <div ref={stageRef} className="fy-album-stage">
                {metrics && (
                    <>
                        <div
                            ref={hostRef}
                            className="fy-album-host"
                            style={{ left: metrics.left, top: metrics.top, width: metrics.width, height: metrics.height }}
                            onPointerDown={onPointerDown}
                        >
                            <div ref={shadowRef} className="fy-album-book-shadow" />
                            <div ref={edgeLeftRef} className="fy-album-edge fy-album-edge--left" />
                            <div ref={edgeRightRef} className="fy-album-edge fy-album-edge--right" />
                            <div ref={clipperRef} className="fy-album-clipper">
                                {pages.map((page) => {
                                    const key = keyOf(page, images);
                                    const image = page.kind === 'photo' ? images[page.index] : null;
                                    return (
                                        <AlbumPage
                                            key={key}
                                            ref={refFor(key)}
                                            page={page}
                                            image={image}
                                            side={pageSide(mode, page)}
                                            layout={layout}
                                            width={metrics.pageWidth}
                                            height={metrics.height}
                                            canDelete={canDelete}
                                            deleting={Boolean(image && deletingId && deletingId === image.id)}
                                            onDecorate={handleDecorate}
                                            onDelete={handleDelete}
                                        />
                                    );
                                })}
                                <div ref={shadeRef} className="fy-album-shade" style={{ width: metrics.pageWidth, height: metrics.height }} />
                                <div ref={flapShadowRef} className="fy-album-flap-shadow">
                                    <div ref={flapPaperRef} className="fy-album-flap-paper" style={{ width: metrics.pageWidth, height: metrics.height }}>
                                        <img ref={flapPhotoRef} className="fy-album-flap-photo" alt="" draggable="false" />
                                    </div>
                                </div>
                                <div ref={flapGlossRef} className="fy-album-flap-gloss" style={{ width: metrics.pageWidth, height: metrics.height }} />
                                <div ref={leafShadeRef} className="fy-album-leaf-shade" style={{ width: metrics.pageWidth, height: metrics.height }} />
                            </div>
                        </div>

                        <div ref={closedRef} className="fy-album-closed">
                            {/* Тень поднятой обложки на странице под ней. */}
                            <div ref={castRef} className="fy-album-cast" style={closedBox} />
                            <div
                                ref={coverRef}
                                className={`fy-album-cover${coverActive ? ' is-active' : ''}`}
                                style={closedBox}
                                role={coverActive ? 'button' : undefined}
                                tabIndex={coverActive ? 0 : undefined}
                                aria-label={coverActive ? 'Открыть альбом' : undefined}
                                aria-hidden={coverActive ? undefined : 'true'}
                                onClick={coverActive ? () => engine.current.openBook() : undefined}
                                onKeyDown={coverActive ? (event) => {
                                    if (event.key === 'Enter' || event.key === ' ') {
                                        event.preventDefault();
                                        engine.current.openBook();
                                    }
                                } : undefined}
                            >
                                <div className="fy-album-cover-face">
                                    <span className="fy-album-cover-spine" />
                                    <span className="fy-album-cover-frame" />
                                    <span className="fy-album-cover-title">4 you</span>
                                    <span className="fy-album-cover-ornament"><HeartGlyph /></span>
                                    <span ref={coverLightRef} className="fy-album-cover-light" />
                                </div>
                                {mode === 'spread' && <span ref={coverEdgeRef} className="fy-album-cover-edge" />}
                            </div>
                        </div>
                    </>
                )}
            </div>

            <div className="fy-album-nav" data-album-control>
                <div className="fy-album-nav-pill">
                    <button type="button" onClick={() => engine.current.flip(false)} disabled={!canBack} aria-label="Назад">
                        <FaIcon className="fas fa-chevron-left" />
                    </button>
                    <span className="fy-album-nav-label" aria-live="polite">{label}</span>
                    <button type="button" onClick={() => engine.current.flip(true)} disabled={!canForward} aria-label="Вперёд">
                        <FaIcon className="fas fa-chevron-right" />
                    </button>
                </div>
            </div>
        </div>
    );
};

export default AlbumView;
