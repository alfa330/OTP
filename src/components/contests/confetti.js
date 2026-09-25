/*
 * Конфетти для церемонии итогов: один canvas поверх окна, без зависимостей.
 *
 * Холст живёт, только пока летит хоть одна бумажка: появляется на первом
 * rain()/burst() и сам убирается из DOM, когда последняя упала за край.
 * pointer-events: none — под дождём страница кликается как обычно.
 *
 * Физика в «кадрах по 16,7 мс»: dt нормирован, поэтому на 120 Гц экранах
 * конфетти падает с той же скоростью, что и на 60 Гц, а после фоновой
 * вкладки (rAF стоял) не телепортируется — шаг зажат сверху.
 */

// Палитра сайта — та же, что у конфетти на иллюстрации правил конкурса:
// янтарь, изумруд, синий, розовый, плюс фиолетовый для разнообразия.
const RAIN_COLORS = ['#FBBF24', '#F59E0B', '#34D399', '#10B981', '#60A5FA', '#3B82F6', '#F472B6', '#A78BFA'];
// Салют первого места — только золото.
const BURST_COLORS = ['#FBBF24', '#F59E0B', '#FCD34D', '#E8B10E', '#FDE68A'];

const GRAVITY = 0.11;
const TERMINAL = 3.6;
const DRAG = 0.986;
const MAX_DPR = 2;

const rnd = (min, max) => min + Math.random() * (max - min);
const pick = (list) => list[Math.floor(Math.random() * list.length)];

function makePiece(x, y, vx, vy, colors, drag = DRAG, dragY = 1) {
    const roll = Math.random();
    // Три формы: ленточка, кружок и длинная тонкая «серпантинка».
    const shape = roll < 0.6 ? 'rect' : roll < 0.85 ? 'dot' : 'strip';
    return {
        x, y, vx, vy,
        shape,
        w: shape === 'strip' ? rnd(3, 4) : rnd(6, 10),
        h: shape === 'strip' ? rnd(14, 20) : rnd(8, 14),
        r: rnd(2.5, 4),
        rot: rnd(0, Math.PI * 2),
        vrot: rnd(-0.12, 0.12),
        tilt: rnd(0, Math.PI * 2),
        vtilt: rnd(0.05, 0.14),
        sway: rnd(0.3, 0.9),
        phase: rnd(0, Math.PI * 2),
        vphase: rnd(0.03, 0.08),
        color: pick(colors),
        alpha: 1,
        drag,
        dragY,
    };
}

export function createConfetti({ zIndex = 45 } = {}) {
    let canvas = null;
    let ctx = null;
    let raf = 0;
    let last = 0;
    let width = 0;
    let height = 0;
    let pieces = [];
    // Дождь: сколько бумажек ещё выпустить и до какого момента.
    let rain = null;

    const resize = () => {
        if (!canvas) return;
        const dpr = Math.min(MAX_DPR, window.devicePixelRatio || 1);
        width = window.innerWidth;
        height = window.innerHeight;
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const mount = () => {
        if (canvas) return;
        canvas = document.createElement('canvas');
        canvas.setAttribute('aria-hidden', 'true');
        Object.assign(canvas.style, {
            position: 'fixed', inset: '0', width: '100%', height: '100%',
            pointerEvents: 'none', zIndex: String(zIndex),
        });
        document.body.appendChild(canvas);
        ctx = canvas.getContext('2d');
        resize();
        window.addEventListener('resize', resize);
    };

    const unmount = () => {
        if (raf) cancelAnimationFrame(raf);
        raf = 0;
        window.removeEventListener('resize', resize);
        if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
        canvas = null;
        ctx = null;
        pieces = [];
        rain = null;
    };

    const frame = (now) => {
        const dt = last ? Math.min(2, (now - last) / 16.667) : 1;
        last = now;

        if (rain) {
            if (now >= rain.until || rain.left <= 0) {
                rain = null;
            } else {
                // Равномерно по времени: столько, сколько «положено» к этому кадру.
                const due = Math.ceil(rain.left * Math.min(1, (now - rain.at) / (rain.until - rain.at)));
                for (let i = 0; i < due; i += 1) {
                    pieces.push(makePiece(rnd(-20, width + 20), rnd(-40, -10),
                        rnd(-1.2, 1.2), rnd(1.5, 3.5), RAIN_COLORS));
                }
                rain.left -= due;
                rain.at = now;
            }
        }

        ctx.clearRect(0, 0, width, height);
        const alive = [];
        for (const p of pieces) {
            p.vx *= Math.pow(p.drag, dt);
            p.vy = Math.min(TERMINAL, p.vy * Math.pow(p.dragY, dt) + GRAVITY * dt);
            p.phase += p.vphase * dt;
            p.x += (p.vx + Math.sin(p.phase) * p.sway) * dt;
            p.y += p.vy * dt;
            p.rot += p.vrot * dt;
            p.tilt += p.vtilt * dt;
            // Внизу окна бумажка тает, а не обрывается о край.
            if (p.y > height - 60) p.alpha = Math.max(0, p.alpha - 0.04 * dt);
            if (p.y > height + 30 || p.alpha <= 0) continue;
            alive.push(p);

            ctx.save();
            ctx.globalAlpha = p.alpha;
            ctx.translate(p.x, p.y);
            ctx.rotate(p.rot);
            // Переворот в 3D: косинус наклона сплющивает бумажку по вертикали.
            ctx.scale(1, Math.cos(p.tilt));
            ctx.fillStyle = p.color;
            if (p.shape === 'dot') {
                ctx.beginPath();
                ctx.arc(0, 0, p.r, 0, Math.PI * 2);
                ctx.fill();
            } else {
                ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
            }
            ctx.restore();
        }
        pieces = alive;

        if (!pieces.length && !rain) {
            unmount();
            return;
        }
        raf = requestAnimationFrame(frame);
    };

    const start = () => {
        mount();
        if (!raf) {
            last = 0;
            raf = requestAnimationFrame(frame);
        }
    };

    return {
        /** Дождь сверху на duration мс; плотность — от ширины окна. */
        rain(duration = 2600) {
            if (typeof window === 'undefined') return;
            start();
            const count = Math.round(Math.max(70, Math.min(220, window.innerWidth / 7)));
            const now = performance.now();
            rain = { left: count, at: now, until: now + duration };
        },
        /** Золотой салют из точки (координаты окна) — вверх веером. */
        burst(x, y, count = 42) {
            if (typeof window === 'undefined') return;
            start();
            for (let i = 0; i < count; i += 1) {
                const angle = -Math.PI / 2 + rnd(-1.15, 1.15);
                const speed = rnd(5, 10.5);
                // Сопротивление и по вертикали: салют раскрывается на ~150 px
                // и дальше кружится вниз, а не улетает за шапку страницы.
                pieces.push(makePiece(x, y, Math.cos(angle) * speed, Math.sin(angle) * speed,
                    BURST_COLORS, 0.95, 0.95));
            }
        },
        /** Больше не выпускать новых; уже летящие долетают. */
        stopRain() {
            rain = null;
        },
        destroy: unmount,
    };
}
