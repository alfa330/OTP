import React, { useCallback, useEffect, useRef, useState } from 'react';
import { isLowEndDevice } from '../../../utils/devicePerformance';
import useMascotMood from '../mascot/useMascotMood.js';
import catMood, { BLINK_MS, FRAMES, nextBlinkDelay } from './catMood.js';
import {
    LOOK_HOLD_MS,
    atRest,
    blendLayers,
    cellAngles,
    centerCell,
    headBlend,
    headCell,
    headName,
    springStep,
    targetAngles,
} from './catLook.js';
import { CAT_LAYOUT } from './catLayout.js';
import './cat-mascot.css';
import logoUrl from './assets/cat-logo.webp';
import normalUrl from './assets/cat-normal.webp';
import calmBodyUrl from './assets/cat-calm-body.webp';
import happyUrl from './assets/cat-happy.webp';
import excitedUrl from './assets/cat-excited.webp';
import sadUrl from './assets/cat-sad.webp';
import angryUrl from './assets/cat-angry.webp';
import surprisedUrl from './assets/cat-surprised.webp';
import scaredUrl from './assets/cat-scared.webp';
import sleepyUrl from './assets/cat-sleepy.webp';
import confusedUrl from './assets/cat-confused.webp';
import mischievousUrl from './assets/cat-mischievous.webp';
import asleepUrl from './assets/cat-asleep.webp';

/* Облик помощника «Котик» — вислоухий котёнок по наброскам владельца
 * (02.11.2026): девять эмоций с листа и спящий клубочек. Каждая эмоция — своя
 * поза, отрендеренная в Blender (scripts/assistant_cat/cat_models.py, сборка —
 * build_assets.py), поэтому эмоция меняется подменой кадра целиком. В покое
 * котик слоями: тело и голова, которая плавно ведёт за курсором во все стороны
 * (сетка углов: пружина без отскока, соседние кадры перетекают друг в друга —
 * catLook.js), моргает при любом повороте, а весь он чуть наклоняется следом.
 *
 * Помощника котик не трогает: жесты шарика он только слушает — общим для
 * талисманов хуком ../mascot/useMascotMood.js.
 *
 * Дёшево для любого компьютера: в разметке только уже показанные кадры,
 * остальные грузятся в простое; кадр, который ещё не доехал, не гасит
 * прежний; тень запечена в картинки — без CSS-фильтров; голова двигается
 * только пока догоняет цель (requestAnimationFrame гаснет, когда догнала), а
 * смешение кадров — непрозрачность слоёв прямо в узлах, мимо React; на слабой
 * машине котик не дышит, а значки эмоций стоят (isLowEndDevice).
 */

const LOW_END = isLowEndDevice();
// «Уменьшить движение»: котик не моргает и не вертит головой — только эмоции.
const REDUCED = typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);

const FRAME_URLS = {
    normal: normalUrl,
    happy: happyUrl,
    excited: excitedUrl,
    sad: sadUrl,
    angry: angryUrl,
    surprised: surprisedUrl,
    scared: scaredUrl,
    sleepy: sleepyUrl,
    confused: confusedUrl,
    mischievous: mischievousUrl,
    asleep: asleepUrl,
};

/* Головы спокойной позы (cat-head-<столбец>-<строка>.webp) и их накладки моргания. */
const assetName = (path) => path.replace(/^.*\/cat-/, '').replace(/\.webp$/, '');
const HEAD_URLS = {};
const BLINK_URLS = {};
Object.entries(import.meta.glob('./assets/cat-head-*.webp', { eager: true, query: '?url', import: 'default' }))
    .forEach(([path, url]) => {
        const name = assetName(path);
        if (name.endsWith('-blink')) BLINK_URLS[name.slice(0, -'-blink'.length)] = url;
        else HEAD_URLS[name] = url;
    });
const HEAD_NAMES = Object.keys(HEAD_URLS).sort();
const CALM_BODY = 'calm-body';
const START_CELL = centerCell();

const HEART = 'M0 -2.2C-1.1 -4.6 -4.6 -4.2 -4.6 -1.4C-4.6 0.8 -2.3 2.5 0 4.2C2.3 2.5 4.6 0.8 4.6 -1.4C4.6 -4.2 1.1 -4.6 0 -2.2Z';
const SPARK = 'M0 -5C0.6 -1.6 1.6 -0.6 5 0C1.6 0.6 0.6 1.6 0 5C-0.6 1.6 -1.6 0.6 -5 0C-1.6 -0.6 -0.6 -1.6 0 -5Z';
const BURST = 'M0.8 -4.6L3 -7.2M1.6 0H5M0.8 4.6L3 7.2';
const VEIN = 'M-1.2 -4.2Q-1.2 -1.2 -4.2 -1.2M1.2 -4.2Q1.2 -1.2 4.2 -1.2M-1.2 4.2Q-1.2 1.2 -4.2 1.2M1.2 4.2Q1.2 1.2 4.2 1.2';
const PUFF = 'M-3.6 2.6A2.4 2.4 0 0 1 -4.4 -2A2.9 2.9 0 0 1 0.8 -3.6A2.5 2.5 0 0 1 4.6 -0.6A2.3 2.3 0 0 1 2.4 3.2A2.4 2.4 0 0 1 -1.2 3.6A2.2 2.2 0 0 1 -3.6 2.6Z';
const TREMBLE = 'M0 -5Q1.6 -4 0 -3T0 -1T0 1T0 3T0 5';

const at = (point, dx = 0, dy = 0) => `translate(${(point[0] + dx).toFixed(1)} ${(point[1] + dy).toFixed(1)})`;

/* Значки вокруг котика — как на набросках: сердечки, искры, «!!», «?», «Zzz»,
   венка и пар у сердитого, дрожь у испуганного. Стоят по опорным точкам головы
   из Blender (catLayout.js), поэтому не съезжают с позой. */
function Extras({ emotion }) {
    const p = CAT_LAYOUT[emotion];
    if (!p) return null;
    let marks = null;
    switch (emotion) {
        case 'happy':
            marks = (
                <>
                    <path className="cat-x cat-x--heart cat-x--float" transform={`${at(p.left, -7, -9)} scale(1.25)`} d={HEART} />
                    <path className="cat-x cat-x--burst-thin cat-x--late" transform={`${at(p.left, -10, 0)} rotate(160)`} d={BURST} />
                    <path className="cat-x cat-x--heart cat-x--float cat-x--late" transform={`${at(p.right, 7, 9)} scale(1.05)`} d={HEART} />
                </>
            );
            break;
        case 'excited':
            marks = (
                <>
                    <path className="cat-x cat-x--spark cat-x--twinkle" transform={at(p.right, 4, -8)} d={SPARK} />
                    <path className="cat-x cat-x--burst cat-x--pulse" transform={`${at(p.left, -5, -1)} scale(-1 1)`} d={BURST} />
                    <path className="cat-x cat-x--burst cat-x--pulse cat-x--late" transform={at(p.right, 5, 7)} d={BURST} />
                </>
            );
            break;
        case 'angry':
            marks = (
                <>
                    <path className="cat-x cat-x--vein cat-x--pulse" transform={`${at(p.forehead, -4, 3)} scale(1.2)`} d={VEIN} />
                    <g className="cat-x--puff" transform={`${at([p.box[0], (p.box[1] + p.box[3]) / 2], -4, 2)} scale(1.3)`}>
                        <path className="cat-x cat-x--ink" d={PUFF} />
                        <path className="cat-x cat-x--ink" d="M5.6 -1.4H8.4M5.8 1.2H8.8" />
                    </g>
                </>
            );
            break;
        case 'surprised':
            marks = (
                <g className="cat-x--pop" transform={at(p.right, 3, -10)}>
                    <path className="cat-x cat-x--bang" d="M-1.6 -5.4L-2.6 0.6M2.8 -4.2L1.4 1.4" />
                    <circle className="cat-x cat-x--bang-dot" cx="-3" cy="3.6" r="1.2" />
                    <circle className="cat-x cat-x--bang-dot" cx="1" cy="4.2" r="1.2" />
                </g>
            );
            break;
        case 'scared':
            marks = (
                <>
                    <path className="cat-x cat-x--ink cat-x--jitter" transform={at(p.left, -4, -7)} d={TREMBLE} />
                    <path className="cat-x cat-x--ink cat-x--jitter cat-x--late" transform={at(p.right, 4, -7)} d={TREMBLE} />
                    <path className="cat-x cat-x--ink cat-x--jitter cat-x--late" transform={at(p.left, -7, 7)} d={TREMBLE} />
                    <path className="cat-x cat-x--ink cat-x--jitter" transform={at(p.right, 7, 7)} d={TREMBLE} />
                </>
            );
            break;
        case 'sleepy':
        case 'asleep': {
            const soft = emotion === 'asleep' ? ' cat-x--soft' : '';
            const base = emotion === 'asleep' ? at(p.right, -4, -8) : at(p.right, 0, -6);
            marks = (
                <g transform={base}>
                    <text className={`cat-x cat-x--zzz${soft}`} x="0" y="0">z</text>
                    <text className={`cat-x cat-x--zzz cat-x--late${soft}`} x="6" y="-6.5" fontSize="9.5">z</text>
                    <text className={`cat-x cat-x--zzz cat-x--later${soft}`} x="12.5" y="-14" fontSize="12">Z</text>
                </g>
            );
            break;
        }
        case 'confused':
            marks = <text className="cat-x cat-x--ask cat-x--wobble" transform={at(p.right, 3, -8)}>?</text>;
            break;
        case 'mischievous':
            marks = <path className="cat-x cat-x--burst cat-x--pulse" transform={at(p.right, 4, -5)} d={BURST} />;
            break;
        default:
            return null;
    }
    return (
        <svg className="cat-mascot__extras" viewBox="0 0 100 100" aria-hidden="true">
            {marks}
        </svg>
    );
}

/* Остальные кадры — в простое после первой отрисовки: браузер положит их в
   кэш, и смена эмоции или поворот головы не будут ждать сеть. */
let framesPreloaded = false;
const preloadFrames = () => {
    if (framesPreloaded || typeof window === 'undefined') return;
    framesPreloaded = true;
    const urls = [...Object.values(FRAME_URLS), calmBodyUrl, ...Object.values(HEAD_URLS), ...Object.values(BLINK_URLS)];
    const run = () => urls.forEach((url) => {
        const img = new Image();
        img.decoding = 'async';
        img.src = url;
    });
    if ('requestIdleCallback' in window) window.requestIdleCallback(run, { timeout: 4000 });
    else window.setTimeout(run, 1500);
};

export default function CatMascot({ variant = 'orb', animated = true }) {
    const rootRef = useRef(null);
    const leanRef = useRef(null);
    const headRef = useRef(null);
    const shownRef = useRef(new Set(['normal']));
    const lastLoadedRef = useRef('normal');
    const [dominant, setDominant] = useState(headName(START_CELL));
    const [allHeads, setAllHeads] = useState(false);
    const [blink, setBlink] = useState(false);
    const [loaded, setLoaded] = useState(() => new Set());
    const interactive = variant !== 'mini';

    useEffect(() => {
        if (interactive) preloadFrames();
    }, [interactive]);

    /* Голова к курсору: цель — ближайший угол сетки (headCell, с запасом на границе); голова
       идёт к ней пружиной без отскока (springStep) в requestAnimationFrame и гаснет, когда
       дошла. Между углами соседние кадры смешиваются по весам (headBlend → blendLayers) —
       непрозрачность слоёв пишется прямо в узлы, мимо React; в покое — один чёткий кадр.
       Весь котик чуть наклоняется следом. Курсор замер на LOOK_HOLD_MS — котик спокойно
       поворачивается к человеку. */
    const motionRef = useRef({ yaw: 0, pitch: 0, vy: 0, vp: 0 });
    const cellRef = useRef(START_CELL);
    const targetRef = useRef(cellAngles(START_CELL));
    const loadedHeadsRef = useRef(new Set());
    const dominantRef = useRef(headName(START_CELL));
    const rafRef = useRef(0);
    const tickAtRef = useRef(0);
    const holdRef = useRef(0);
    const paint = useCallback((angles) => {
        const node = headRef.current;
        if (!node) return;
        // Только доехавшие кадры: недоехавший дал бы дыру, сквозь которую видно тело.
        const ready = loadedHeadsRef.current;
        let blend = headBlend(angles).filter((part) => ready.has(part.name));
        if (!blend.length) blend = [{ name: dominantRef.current, weight: 1 }];
        const total = blend.reduce((sum, part) => sum + part.weight, 0);
        const layers = new Map(blendLayers(blend.map((part) => ({ ...part, weight: part.weight / total })))
            .map((layer) => [layer.name, layer]));
        node.querySelectorAll('img[data-head]').forEach((img) => {
            const layer = layers.get(img.dataset.head);
            img.style.opacity = layer ? layer.opacity.toFixed(3) : '0';
            img.style.zIndex = layer ? String(layer.z) : '0';
        });
        if (blend[0].name !== dominantRef.current) {
            dominantRef.current = blend[0].name;
            setDominant(blend[0].name);
        }
    }, []);
    const tick = useCallback((at) => {
        const dt = tickAtRef.current ? at - tickAtRef.current : 16;
        tickAtRef.current = at;
        const target = targetRef.current;
        let next = springStep(motionRef.current, target, dt);
        const done = atRest(next, target);
        if (done) next = { yaw: target.yaw, pitch: target.pitch, vy: 0, vp: 0 };
        motionRef.current = next;
        paint(next);
        if (done) {
            rafRef.current = 0;
            tickAtRef.current = 0;
            return;
        }
        rafRef.current = window.requestAnimationFrame(tick);
    }, [paint]);
    const aim = useCallback((cell) => {
        cellRef.current = cell;
        targetRef.current = cellAngles(cell);
        if (!rafRef.current) rafRef.current = window.requestAnimationFrame(tick);
    }, [tick]);
    const onLook = useCallback((x, y) => {
        if (REDUCED) return;
        if (leanRef.current) leanRef.current.style.transform = `rotate(${(x * 2).toFixed(2)}deg)`;
        aim(headCell(targetAngles(x, y), cellRef.current));
        window.clearTimeout(holdRef.current);
        holdRef.current = window.setTimeout(() => {
            if (leanRef.current) leanRef.current.style.transform = '';
            aim(START_CELL);
        }, LOOK_HOLD_MS);
    }, [aim]);
    const onHeadLoad = useCallback((name) => {
        loadedHeadsRef.current.add(name);
        setLoaded((prev) => (prev.has(name) ? prev : new Set(prev).add(name)));
        paint(motionRef.current);
    }, [paint]);
    useEffect(() => () => {
        window.clearTimeout(holdRef.current);
        window.cancelAnimationFrame(rafRef.current);
    }, []);
    const emotion = useMascotMood(rootRef, catMood, { interactive, onLook });

    // Моргает при любом повороте головы: у каждого угла своя накладка моргания.
    const canBlink = interactive && animated && !REDUCED && emotion === 'normal';
    useEffect(() => {
        if (!canBlink) return undefined;
        let timer = 0;
        const schedule = () => {
            timer = window.setTimeout(() => {
                setBlink(true);
                timer = window.setTimeout(() => {
                    setBlink(false);
                    schedule();
                }, BLINK_MS);
            }, nextBlinkDelay());
        };
        schedule();
        return () => {
            window.clearTimeout(timer);
            setBlink(false);
        };
    }, [canBlink]);

    const markLoaded = useCallback((name) => {
        setLoaded((prev) => (prev.has(name) ? prev : new Set(prev).add(name)));
    }, []);
    // Остальные углы головы — когда показались тело и голова прямо: первый кадр не ждёт их.
    useEffect(() => {
        if (!allHeads && loaded.has(CALM_BODY) && loaded.has(headName(START_CELL))) setAllHeads(true);
    }, [loaded, allHeads]);

    if (!interactive) {
        return (
            <span className="cat-mascot cat-mascot--mini" aria-hidden="true">
                <img src={logoUrl} alt="" draggable={false} />
            </span>
        );
    }

    const frame = emotion;
    shownRef.current.add(frame);
    // Пока новый кадр не доехал, остаётся прежний — котик не мигает пустотой.
    if (loaded.has(frame)) lastLoadedRef.current = frame;
    const visible = loaded.has(frame) ? frame : lastLoadedRef.current;
    // Спокойная поза слоями — когда доехали тело и голова; до того — кадр normal целиком.
    const calm = !REDUCED && emotion === 'normal' && loaded.has(CALM_BODY) && loaded.has(dominant);
    const blinking = calm && blink && canBlink;
    // Сначала — голова прямо; остальные углы монтируются, когда спокойная поза показалась.
    const heads = allHeads ? HEAD_NAMES : [headName(START_CELL)];
    return (
        <span
            ref={rootRef}
            className={`cat-mascot cat-mascot--${variant} is-${emotion}${animated ? '' : ' is-paused'}${LOW_END ? ' is-lite' : ''}`}
            aria-hidden="true"
        >
            <span className="cat-mascot__breathe">
                <span ref={leanRef} className="cat-mascot__lean">
                    {FRAMES.filter((name) => shownRef.current.has(name)).map((name) => (
                        <img
                            key={name}
                            className={name === visible && !calm ? 'is-on' : undefined}
                            src={FRAME_URLS[name]}
                            alt=""
                            draggable={false}
                            onLoad={() => markLoaded(name)}
                        />
                    ))}
                    {!REDUCED && (
                        <span className={`cat-mascot__calm${calm ? ' is-on' : ''}`}>
                            <img className="is-on" src={calmBodyUrl} alt="" draggable={false}
                                onLoad={() => markLoaded(CALM_BODY)} />
                            <span ref={headRef} className="cat-mascot__head">
                                {heads.map((name) => (
                                    <img
                                        key={name}
                                        data-head={name}
                                        src={HEAD_URLS[name]}
                                        alt=""
                                        draggable={false}
                                        onLoad={() => onHeadLoad(name)}
                                    />
                                ))}
                                {BLINK_URLS[dominant] && (
                                    <img
                                        key={`${dominant}-blink`}
                                        className={`cat-mascot__blink${blinking ? ' is-on' : ''}`}
                                        src={BLINK_URLS[dominant]}
                                        alt=""
                                        draggable={false}
                                    />
                                )}
                            </span>
                        </span>
                    )}
                </span>
            </span>
            <Extras key={emotion} emotion={emotion} />
        </span>
    );
}
