import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import './halloween.css';
import pumpkinsUrl from './assets/pumpkins.webp';
import ghostUrl from './assets/ghost.webp';
import spiderUrl from './assets/spider.webp';
import batStripUrl from './assets/bat-strip.webp';
import { HALLOWEEN_ENDS_AT, isHalloweenTime } from './halloweenWindow';
import {
    ART,
    loginLayout,
    makeBatFlock,
    nextBatDelay,
    nextGhostDelay,
    nextSpiderDropDelay,
} from './halloweenScene';

/* Хэллоуин-2026 — слой украшений поверх портала. Модели (тыквы, привидение,
 * паук, летучая мышь, шляпа) построены и отрендерены в Blender, см.
 * scripts/halloween/halloween_models.py.
 *
 * Портал слой не трогает: где он сейчас, слой узнаёт по разметке (карточка
 * входа .auth-card, сайдбар .sidebar, оболочка телефона body.mobile-shell).
 * Всё некликабельно (pointer-events: none) и спрятано от экранных дикторов.
 *
 *   вход    — паутина с пауком, тыквы с дрожащим огоньком у карточки,
 *             привидение, шляпа на «i» в логотипе, мыши над карточкой;
 *   портал  — шляпа на аватаре и логотипе, на компьютере паук у правой
 *             кромки (кроме «Чатов ОП» и «Журнала оценок»); мыши пролетают
 *             раз в 4–7 минут, привидение поднимается раз в 10–15.
 */

const ROUTE_POLL_MS = 1000;
const SPIDER_DROP_HOLD_MS = 3600;
const APP_BAT_ZONE = { top: 76, bottom: 250 };
const MOBILE_BAT_ZONE = { top: 120, bottom: 270 };
const APP_SPIDER = { width: 26, top: 64, drop: 58 };

/* «Чаты ОП» и «Журнал оценок» работают вплотную к правому краю — там
   паук не висит (мыши и привидение мимолётны и остаются). */
const isBusyRightEdge = () => {
    const main = document.querySelector('.main-content');
    if (!main) return false;
    return main.classList.contains('wz-main-content')
        || (main.classList.contains('h-screen') && main.classList.contains('overflow-hidden'));
};

const readScreen = () => {
    const card = document.querySelector('.auth-screen .auth-card');
    const mobile = document.body.classList.contains('mobile-shell');
    if (card) return { mode: 'login', mobile, busyRight: false, card };
    if (document.querySelector('.sidebar')) return { mode: 'app', mobile, busyRight: isBusyRightEdge(), card: null };
    return { mode: 'none', mobile, busyRight: false, card: null };
};

const pxVar = (element, name) => {
    const value = parseFloat(getComputedStyle(element).getPropertyValue(name));
    return Number.isFinite(value) ? value : 0;
};

const measureLogin = (card) => {
    const rect = card.getBoundingClientRect();
    const screen = card.closest('.auth-screen');
    const offer = document.body.classList.contains('has-install-offer')
        ? pxVar(document.body, '--install-offer-height')
        : 0;
    const topPad = screen ? parseFloat(getComputedStyle(screen).paddingTop) || 0 : 0;
    return loginLayout({
        width: window.innerWidth,
        height: window.innerHeight,
        card: {
            left: rect.left,
            top: rect.top,
            right: rect.right,
            bottom: rect.bottom,
            width: rect.width,
            height: rect.height,
        },
        bottomInset: offer,
        topInset: topPad,
    });
};

const sameLayout = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const useReducedMotion = () => {
    const [reduced, setReduced] = useState(() => Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches));
    useEffect(() => {
        const media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
        if (!media) return undefined;
        const onChange = () => setReduced(media.matches);
        media.addEventListener?.('change', onChange);
        return () => media.removeEventListener?.('change', onChange);
    }, []);
    return reduced;
};

/* Паутина в углу: радиальные нити и провисшие витки между ними. Небольшая
   неровность витков — по синусу, без случайности: одна и та же у всех. */
const WEB_PATH = (() => {
    const angles = [4, 17, 31, 45, 59, 73, 86].map((deg) => (deg * Math.PI) / 180);
    const radii = [13, 25, 38, 52, 66, 80, 93];
    const point = (angle, radius) => [Math.cos(angle) * radius, Math.sin(angle) * radius];
    const fmt = ([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`;
    const parts = angles.map((angle, i) => `M0 0L${fmt(point(angle, 100 - (i % 3) * 3))}`);
    radii.forEach((radius, k) => {
        for (let i = 0; i < angles.length - 1; i += 1) {
            const r1 = radius * (1 + 0.04 * Math.sin(i * 1.7 + k * 2.3));
            const r2 = radius * (1 + 0.04 * Math.sin((i + 1) * 1.7 + k * 2.3));
            const mid = (angles[i] + angles[i + 1]) / 2;
            const sag = ((r1 + r2) / 2) * 0.86;
            parts.push(`M${fmt(point(angles[i], r1))}Q${fmt(point(mid, sag))} ${fmt(point(angles[i + 1], r2))}`);
        }
    });
    return parts.join('');
})();

function Cobweb({ size }) {
    return (
        <div className="hw-fixed hw-web hw-appear" style={{ left: 0, top: 0, width: size, height: size }}>
            <svg viewBox="0 0 100 100" aria-hidden="true" focusable="false">
                <path d={WEB_PATH} fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
            </svg>
        </div>
    );
}

/* Обёртка шире паука на SPIDER_SWAY_ROOM с каждой стороны: она обрезает нить
   выше точки крепления, а качание не должно подрезать лапки. */
const SPIDER_SWAY_ROOM = 18;

function Spider({ placement, dropping, app = false }) {
    const { left, right, anchor, top, width, height, drop } = placement;
    const style = {
        left: left === undefined ? undefined : left - SPIDER_SWAY_ROOM,
        right: right === undefined ? undefined : right - SPIDER_SWAY_ROOM,
        top: anchor,
        width: width + SPIDER_SWAY_ROOM * 2,
        height: top - anchor + height + drop + 14,
        '--hw-hang': `${top - anchor}px`,
        '--hw-drop': `${drop}px`,
        '--hw-room': `${SPIDER_SWAY_ROOM}px`,
    };
    return (
        <div className={`hw-fixed hw-spider hw-appear${app ? ' hw-spider--app' : ''}${dropping ? ' is-dropping' : ''}`} style={style}>
            <div className="hw-spider__drop">
                <div className="hw-spider__bob">
                    <span className="hw-spider__thread" />
                    <img className="hw-spider__body" src={spiderUrl} alt="" width={width} height={height} draggable={false} />
                </div>
            </div>
        </div>
    );
}

function Bat({ bat, onDone }) {
    const height = Math.round(bat.size / ART.bat);
    return (
        <div
            className="hw-bat"
            style={{
                top: bat.y,
                width: bat.size,
                '--hw-from': `${bat.from}px`,
                '--hw-to': `${bat.to}px`,
                '--hw-dur': `${bat.duration}s`,
                '--hw-delay': `${bat.delay}s`,
            }}
            onAnimationEnd={(event) => {
                if (event.target === event.currentTarget) onDone();
            }}
        >
            <div className="hw-bat__wave" style={{ '--hw-wave': `${bat.wave}px`, '--hw-wave-dur': `${bat.waveDuration}s` }}>
                <div className={`hw-bat__frame${bat.leftToRight ? '' : ' is-mirrored'}`} style={{ height }}>
                    <span
                        className="hw-bat__strip"
                        style={{
                            backgroundImage: `url(${batStripUrl})`,
                            '--hw-flap': `${bat.flap}s`,
                            '--hw-flap-delay': `-${bat.flapDelay}s`,
                        }}
                    />
                </div>
            </div>
        </div>
    );
}

export default function HalloweenLayer({ onExpire }) {
    const reduced = useReducedMotion();
    const [screen, setScreen] = useState(() => ({ mode: 'none', mobile: false, busyRight: false }));
    const [layout, setLayout] = useState(null);
    const [flocks, setFlocks] = useState([]);
    const [ghostVisit, setGhostVisit] = useState(null);
    const [dropping, setDropping] = useState(false);
    const cardRef = useRef(null);
    const zoneRef = useRef(null);
    const flockSeq = useRef(0);

    /* Метка на <html> — для шляпок в CSS. Снимается вместе со слоем. */
    useLayoutEffect(() => {
        const root = document.documentElement;
        root.setAttribute('data-otp-halloween', '');
        return () => root.removeAttribute('data-otp-halloween');
    }, []);

    /* Срок: в открытой вкладке слой гаснет сам ровно в полночь 2 ноября
       (и при возвращении на вкладку, если она спала дольше). */
    useEffect(() => {
        let timer = 0;
        const check = () => {
            if (!isHalloweenTime()) {
                onExpire();
                return;
            }
            timer = window.setTimeout(check, Math.min(HALLOWEEN_ENDS_AT - Date.now() + 50, 6 * 60 * 60 * 1000));
        };
        check();
        const onVisible = () => {
            if (document.visibilityState === 'visible' && !isHalloweenTime()) onExpire();
        };
        document.addEventListener('visibilitychange', onVisible);
        return () => {
            window.clearTimeout(timer);
            document.removeEventListener('visibilitychange', onVisible);
        };
    }, [onExpire]);

    /* Где сейчас человек: вход или портал, компьютер или телефон. Дёшево —
       два querySelector в секунду; подписка на мутации всего портала
       обошлась бы дороже. */
    const remeasure = useCallback(() => {
        const next = readScreen();
        cardRef.current = next.card;
        setScreen((prev) => (prev.mode === next.mode && prev.mobile === next.mobile && prev.busyRight === next.busyRight
            ? prev
            : { mode: next.mode, mobile: next.mobile, busyRight: next.busyRight }));
        const nextLayout = next.card ? measureLogin(next.card) : null;
        setLayout((prev) => (sameLayout(prev, nextLayout) ? prev : nextLayout));
    }, []);

    useEffect(() => {
        remeasure();
        const poll = window.setInterval(remeasure, ROUTE_POLL_MS);
        let frame = 0;
        const onResize = () => {
            window.cancelAnimationFrame(frame);
            frame = window.requestAnimationFrame(remeasure);
        };
        window.addEventListener('resize', onResize);
        window.visualViewport?.addEventListener('resize', onResize);
        return () => {
            window.clearInterval(poll);
            window.cancelAnimationFrame(frame);
            window.removeEventListener('resize', onResize);
            window.visualViewport?.removeEventListener('resize', onResize);
        };
    }, [remeasure]);

    /* Карточка входа двигается и без resize: панель установки поднимает её,
       ошибка входа меняет высоту. Следим за её размером и за экраном входа. */
    useEffect(() => {
        if (screen.mode !== 'login' || !cardRef.current || typeof ResizeObserver === 'undefined') return undefined;
        const observer = new ResizeObserver(() => remeasure());
        observer.observe(cardRef.current);
        const authScreen = cardRef.current.closest('.auth-screen');
        if (authScreen) observer.observe(authScreen);
        const onTransitionEnd = () => remeasure();
        authScreen?.addEventListener('transitionend', onTransitionEnd);
        return () => {
            observer.disconnect();
            authScreen?.removeEventListener('transitionend', onTransitionEnd);
        };
    }, [screen.mode, remeasure]);

    zoneRef.current = (() => {
        if (screen.mode === 'login') {
            return layout?.bats ? { zoneTop: layout.bats.top, zoneBottom: layout.bats.bottom, compact: layout.compact } : null;
        }
        if (screen.mode === 'app') {
            const zone = screen.mobile ? MOBILE_BAT_ZONE : APP_BAT_ZONE;
            return { zoneTop: zone.top, zoneBottom: zone.bottom, compact: screen.mobile };
        }
        return null;
    })();

    /* Летучие мыши: на входе — сразу и потом раз в 15–27 секунд, в портале —
       через пару секунд после входа и дальше раз в 4–7 минут. */
    useEffect(() => {
        if (reduced || screen.mode === 'none') return undefined;
        const login = screen.mode === 'login';
        let timer = 0;
        const launch = () => {
            const zone = zoneRef.current;
            if (!document.hidden && zone) {
                flockSeq.current += 1;
                const id = flockSeq.current;
                const bats = makeBatFlock(Math.random, { ...zone, width: window.innerWidth });
                if (bats.length) {
                    setFlocks((list) => [...list.slice(-2), { id, bats, left: bats.length }]);
                }
            }
            timer = window.setTimeout(launch, nextBatDelay(Math.random, { login }));
        };
        timer = window.setTimeout(launch, login ? 900 : 3500);
        return () => window.clearTimeout(timer);
    }, [reduced, screen.mode]);

    const batDone = useCallback((id) => {
        setFlocks((list) => list
            .map((flock) => (flock.id === id ? { ...flock, left: flock.left - 1 } : flock))
            .filter((flock) => flock.left > 0));
    }, []);

    /* Привидение в портале — только на компьютере и редко. */
    useEffect(() => {
        if (reduced || screen.mode !== 'app' || screen.mobile) return undefined;
        let timer = 0;
        const visit = () => {
            if (!document.hidden) setGhostVisit({ id: Date.now(), x: Math.round(36 + Math.random() * 140) });
            timer = window.setTimeout(visit, nextGhostDelay(Math.random));
        };
        timer = window.setTimeout(visit, 60000);
        return () => window.clearTimeout(timer);
    }, [reduced, screen.mode, screen.mobile]);

    /* Паук время от времени спускается и забирается обратно. */
    useEffect(() => {
        const hasSpider = screen.mode === 'login'
            ? Boolean(layout?.spider)
            : screen.mode === 'app' && !screen.mobile && !screen.busyRight;
        if (reduced || !hasSpider) return undefined;
        const login = screen.mode === 'login';
        let timer = 0;
        let holdTimer = 0;
        const drop = () => {
            if (!document.hidden) {
                setDropping(true);
                holdTimer = window.setTimeout(() => setDropping(false), SPIDER_DROP_HOLD_MS);
            }
            timer = window.setTimeout(drop, nextSpiderDropDelay(Math.random, { login }));
        };
        timer = window.setTimeout(drop, nextSpiderDropDelay(Math.random, { login }));
        return () => {
            window.clearTimeout(timer);
            window.clearTimeout(holdTimer);
            setDropping(false);
        };
    }, [reduced, screen.mode, screen.mobile, screen.busyRight, layout?.spider]);

    const appSpider = screen.mode === 'app' && !screen.mobile && !screen.busyRight
        ? {
            right: 1,
            anchor: 0,
            top: APP_SPIDER.top,
            width: APP_SPIDER.width,
            height: Math.round(APP_SPIDER.width / ART.spider),
            drop: APP_SPIDER.drop,
        }
        : null;

    return (
        <div className="otp-halloween" aria-hidden="true">
            {screen.mode === 'login' && layout && (
                <>
                    {layout.web && <Cobweb size={layout.web.size} />}
                    {layout.spider && <Spider placement={layout.spider} dropping={dropping} />}
                    {layout.ghost && (
                        <div
                            className="hw-fixed hw-ghost hw-appear"
                            style={{ left: layout.ghost.left, top: layout.ghost.top, width: layout.ghost.width }}
                        >
                            <span className="hw-ghost__shadow" />
                            <img src={ghostUrl} alt="" draggable={false} />
                        </div>
                    )}
                    {layout.pumpkins && (
                        <div
                            className="hw-fixed hw-pumpkins hw-appear"
                            style={{ left: layout.pumpkins.left, top: layout.pumpkins.top, width: layout.pumpkins.width }}
                        >
                            <span className="hw-pumpkins__glow" />
                            <span className="hw-pumpkins__shadow" />
                            <img src={pumpkinsUrl} alt="" draggable={false} />
                        </div>
                    )}
                </>
            )}
            {appSpider && <Spider placement={appSpider} dropping={dropping} app />}
            {ghostVisit && screen.mode === 'app' && (
                <img
                    key={ghostVisit.id}
                    className="hw-ghost-visit"
                    src={ghostUrl}
                    alt=""
                    draggable={false}
                    style={{ left: `calc(var(--app-sidebar-offset, 0px) + ${ghostVisit.x}px)` }}
                    onAnimationEnd={() => setGhostVisit(null)}
                />
            )}
            {flocks.map((flock) => flock.bats.map((bat) => (
                <Bat key={`${flock.id}-${bat.key}`} bat={bat} onDone={() => batDone(flock.id)} />
            )))}
        </div>
    );
}
