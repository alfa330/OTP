import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import './halloween.css';
import pumpkinsUrl from './assets/pumpkins.webp';
import ghostUrl from './assets/ghost.webp';
import spiderUrl from './assets/spider.webp';
import batStripUrl from './assets/bat-strip.webp';
import faviconUrl from './assets/pumpkin-favicon.png';
import { isHalloweenTime } from './halloweenWindow';
import {
    ART,
    appCorners,
    loginLayout,
    makeBatFlock,
    nextBatDelay,
    nextSpiderDropDelay,
} from './halloweenScene';

/* Хэллоуин-2026 — слой украшений поверх портала (макет владельца 30.10.2026:
 * тёмный портал, паутина по углам, паук, летучие мыши, привидение). Модели —
 * Blender, scripts/halloween/halloween_models.py.
 *
 * Портал слой не трогает: где он сейчас, слой узнаёт по разметке (карточка
 * входа .auth-card, сайдбар .sidebar, оболочка телефона body.mobile-shell).
 * Работать украшения не мешают:
 *   - некликабельны и спрятаны от экранных дикторов;
 *   - уходят от курсора: паутина бледнеет, паук взбирается, привидение
 *     ныряет за кромку — к кнопке под ними человек дотянется сразу;
 *   - паук и привидение не встают туда, где под ними кнопка или поле;
 *   - пока открыто окно с затемнением, весь слой прячется;
 *   - в «Чатах ОП», «Журнале оценок» и «Аукционе смен» у правого края работа —
 *     там нет паутины с пауком.
 */

const ROUTE_POLL_MS = 1000;
const SPIDER_DROP_HOLD_MS = 3600;
const SHY_RADIUS = 140;
const APP_BAT_ZONE = { top: 76, bottom: 250 };
const MOBILE_BAT_ZONE = { top: 120, bottom: 270 };
const BUSY_VIEWS = new Set(['wazzup_chats', 'shift_auction']);
const CONTROL_SELECTOR = 'button, a[href], input, select, textarea, [role="button"], [role="link"], [role="tab"], [contenteditable="true"]';

const isBusyRightEdge = () => {
    const main = document.querySelector('.main-content');
    if (main && (main.classList.contains('wz-main-content')
        || (main.classList.contains('h-screen') && main.classList.contains('overflow-hidden')))) return true;
    try {
        return BUSY_VIEWS.has(new URLSearchParams(window.location.search).get('view') || '');
    } catch (error) {
        return false;
    }
};

/* Открыто окно с затемнением — украшения не должны светиться поверх него. */
const isModalOpen = () => {
    if (document.querySelector('.otp-modal-dim, [aria-modal="true"]')) return true;
    for (const el of document.querySelectorAll('.fixed.inset-0')) {
        if (el.closest('.otp-halloween')) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) < 0.05) continue;
        const match = /rgba?\(([^)]+)\)/.exec(style.backgroundColor);
        const parts = match ? match[1].split(',').map((v) => parseFloat(v)) : [];
        const alpha = parts.length === 4 ? parts[3] : (parts.length === 3 ? 1 : 0);
        if (alpha >= 0.15 || (style.backdropFilter && style.backdropFilter !== 'none')) return true;
    }
    return false;
};

const isMenuOpen = () => Boolean(document.querySelector('[role="menu"], [role="listbox"]'));

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

/* Лежит ли под прямоугольником (с запасом CONTROL_GAP вокруг) кнопка, ссылка
   или поле: украшение не встаёт вплотную к тому, чем работают. Наш слой с
   pointer-events: none в elementFromPoint не попадает. */
const CONTROL_GAP = 8;
const coversControl = (rect) => {
    const xs = [rect.left - CONTROL_GAP, rect.left + rect.width / 2, rect.right + CONTROL_GAP];
    const ys = [rect.top - CONTROL_GAP, rect.top + rect.height / 2, rect.bottom + CONTROL_GAP];
    for (const x of xs) {
        for (const y of ys) {
            if (x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight) continue;
            const top = document.elementFromPoint(x, y);
            if (top && top.closest(CONTROL_SELECTOR)) return true;
        }
    }
    return false;
};

const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);

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

/* Паутина в углу: радиальные нити и провисшие витки между ними. Неровность
   витков — по синусу, без случайности: одна и та же у всех. */
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

/* corner — угол экрана: 'tl' (вход), 'tr' и 'bl' (портал). */
const Cobweb = React.forwardRef(function Cobweb({ size, corner = 'tl', style }, ref) {
    return (
        <div ref={ref} className={`hw-fixed hw-web hw-web--${corner} hw-appear`} style={{ width: size, height: size, ...style }}>
            <svg viewBox="0 0 100 100" aria-hidden="true" focusable="false">
                <path d={WEB_PATH} fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
            </svg>
        </div>
    );
});

/* Обёртка шире паука на SPIDER_SWAY_ROOM с каждой стороны: она обрезает нить
   выше точки крепления, а качание не должно подрезать лапки. */
const SPIDER_SWAY_ROOM = 18;

const Spider = React.forwardRef(function Spider({ placement, dropping, app = false }, ref) {
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
        <div ref={ref} className={`hw-fixed hw-spider hw-appear${app ? ' hw-spider--app' : ''}${dropping ? ' is-dropping' : ''}`} style={style}>
            <div className="hw-spider__drop">
                <div className="hw-spider__bob">
                    <span className="hw-spider__thread" />
                    <img className="hw-spider__body" src={spiderUrl} alt="" width={width} height={height} draggable={false} />
                </div>
            </div>
        </div>
    );
});

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
    const [screen, setScreen] = useState(() => ({ mode: 'none', mobile: false, busyRight: false, modal: false }));
    const [layout, setLayout] = useState(null);
    const [corners, setCorners] = useState(null);
    const [flocks, setFlocks] = useState([]);
    const [dropping, setDropping] = useState(false);
    const cardRef = useRef(null);
    const zoneRef = useRef(null);
    const flockSeq = useRef(0);
    const shyRefs = useRef({});
    const screenRef = useRef(screen);
    screenRef.current = screen;

    /* Метка на <html> — для шляпки на аватаре и логотипа в CSS. main.jsx ставит
       её ещё до отрисовки; слой держит её и снимает вместе с собой. */
    useLayoutEffect(() => {
        const root = document.documentElement;
        root.setAttribute('data-otp-halloween', '');
        return () => root.removeAttribute('data-otp-halloween');
    }, []);

    /* Значок вкладки — тыква; по сроку возвращается прежний. */
    useEffect(() => {
        const links = [...document.querySelectorAll('link[rel~="icon"]')];
        const saved = links.map((link) => [link, link.getAttribute('href'), link.getAttribute('type')]);
        links.forEach((link) => {
            link.setAttribute('href', faviconUrl);
            link.setAttribute('type', 'image/png');
        });
        return () => saved.forEach(([link, href, type]) => {
            if (href) link.setAttribute('href', href); else link.removeAttribute('href');
            if (type) link.setAttribute('type', type); else link.removeAttribute('type');
        });
    }, []);

    /* Срок: проверка в каждом опросе и при возвращении на вкладку — таймеры
       спят вместе с ноутбуком, и один таймер до полуночи мог бы опоздать. */
    useEffect(() => {
        const check = () => {
            if (!isHalloweenTime()) onExpire();
        };
        check();
        document.addEventListener('visibilitychange', check);
        window.addEventListener('focus', check);
        window.addEventListener('pageshow', check);
        return () => {
            document.removeEventListener('visibilitychange', check);
            window.removeEventListener('focus', check);
            window.removeEventListener('pageshow', check);
        };
    }, [onExpire]);

    /* Где сейчас человек и что вокруг. Дёшево — несколько querySelector в
       секунду; подписка на мутации всего портала обошлась бы дороже. */
    const remeasure = useCallback(() => {
        if (!isHalloweenTime()) {
            onExpire();
            return;
        }
        const next = readScreen();
        const modal = next.mode !== 'none' && isModalOpen();
        cardRef.current = next.card;
        setScreen((prev) => (prev.mode === next.mode && prev.mobile === next.mobile
            && prev.busyRight === next.busyRight && prev.modal === modal
            ? prev
            : { mode: next.mode, mobile: next.mobile, busyRight: next.busyRight, modal }));
        const nextLayout = next.card ? measureLogin(next.card) : null;
        setLayout((prev) => (sameJson(prev, nextLayout) ? prev : nextLayout));

        let nextCorners = null;
        if (next.mode === 'app' && !next.mobile) {
            const orbEl = document.querySelector('.aorb-button');
            const orb = orbEl ? orbEl.getBoundingClientRect() : null;
            nextCorners = appCorners({
                width: window.innerWidth,
                height: window.innerHeight,
                // Свёрнутый сайдбар объявляет ширину на body (body.sidebar-collapsed), а не на :root.
                sidebar: pxVar(document.body, '--app-sidebar-offset') || 80,
                busyRight: next.busyRight,
                orb: orb && orb.width ? { left: orb.left, top: orb.top, right: orb.right, bottom: orb.bottom } : null,
            });
            // Паук и привидение не встают туда, где под ними кнопка или поле.
            if (nextCorners.spider) {
                const s = nextCorners.spider;
                const left = window.innerWidth - s.right - s.width;
                nextCorners.spider.blocked = coversControl({ left, right: left + s.width, top: s.top, bottom: s.top + s.height, width: s.width, height: s.height });
            }
            if (nextCorners.ghost) {
                const g = nextCorners.ghost;
                const top = window.innerHeight - g.peek;
                nextCorners.ghost.blocked = coversControl({ left: g.left, right: g.left + g.width, top, bottom: window.innerHeight, width: g.width, height: g.peek });
            }
        }
        setCorners((prev) => (sameJson(prev, nextCorners) ? prev : nextCorners));
    }, [onExpire]);

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

    /* Украшения уходят от курсора — мимо React: класс на узле раз в кадр. */
    useEffect(() => {
        if (screen.mode !== 'app') return undefined;
        let frame = 0;
        let pointer = null;
        const apply = () => {
            frame = 0;
            for (const node of Object.values(shyRefs.current)) {
                if (!node || !pointer) continue;
                const rect = node.getBoundingClientRect();
                const dx = Math.max(rect.left - pointer.x, 0, pointer.x - rect.right);
                const dy = Math.max(rect.top - pointer.y, 0, pointer.y - rect.bottom);
                node.classList.toggle('is-shy', Math.hypot(dx, dy) < SHY_RADIUS);
            }
        };
        const onMove = (event) => {
            pointer = { x: event.clientX, y: event.clientY };
            if (!frame) frame = window.requestAnimationFrame(apply);
        };
        window.addEventListener('pointermove', onMove, { passive: true });
        return () => {
            window.removeEventListener('pointermove', onMove);
            window.cancelAnimationFrame(frame);
        };
    }, [screen.mode]);

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
       через пару секунд после входа и дальше раз в 4–7 минут. Не взлетают,
       пока открыто окно или выпадающий список. */
    useEffect(() => {
        if (reduced || screen.mode === 'none') return undefined;
        const login = screen.mode === 'login';
        let timer = 0;
        const launch = () => {
            const zone = zoneRef.current;
            if (!document.hidden && zone && !screenRef.current.modal && !isMenuOpen()) {
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

    /* Паук время от времени спускается и забирается обратно. */
    const hasSpider = screen.mode === 'login'
        ? Boolean(layout?.spider)
        : Boolean(corners?.spider && !corners.spider.blocked);
    useEffect(() => {
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
    }, [reduced, screen.mode, hasSpider]);

    const app = screen.mode === 'app' && corners;
    const shyRef = (key) => (node) => {
        shyRefs.current[key] = node;
    };

    return (
        <div className={`otp-halloween${screen.modal ? ' is-muted' : ''}`} aria-hidden="true">
            {screen.mode === 'login' && layout && (
                <>
                    {layout.web && <Cobweb size={layout.web.size} corner="tl" style={{ left: 0, top: 0 }} />}
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
            {app && corners.webTop && (
                <Cobweb ref={shyRef('webTop')} size={corners.webTop.size} corner="tr" style={{ right: 0, top: 0 }} />
            )}
            {app && corners.webBottom && (
                <Cobweb ref={shyRef('webBottom')} size={corners.webBottom.size} corner="bl" style={{ left: corners.webBottom.left, bottom: 0 }} />
            )}
            {app && corners.spider && !corners.spider.blocked && (
                <Spider ref={shyRef('spider')} placement={corners.spider} dropping={dropping} app />
            )}
            {app && corners.ghost && !corners.ghost.blocked && (
                <div
                    ref={shyRef('ghost')}
                    className="hw-fixed hw-ghost-peek hw-appear"
                    style={{ left: corners.ghost.left, width: corners.ghost.width, '--hw-peek': `${corners.ghost.peek}px` }}
                >
                    <img src={ghostUrl} alt="" draggable={false} />
                </div>
            )}
            {flocks.map((flock) => flock.bats.map((bat) => (
                <Bat key={`${flock.id}-${bat.key}`} bat={bat} onDone={() => batDone(flock.id)} />
            )))}
        </div>
    );
}
