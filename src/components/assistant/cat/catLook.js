/* Голова котика к курсору (владелец 09.11.2026: «чтобы нормально следил за курсором,
 * чтобы голова могла нормально двигаться», затем «сделай более плавной, а не такой
 * резкой»). Спокойная поза слоями: тело без головы и голова под сеткой углов поворота
 * и наклона (CAT_HEAD_GRID, рендер scripts/assistant_cat).
 *
 *   цель   — ближайший угол сетки к курсору (с запасом на границе — без дрожи);
 *   ход    — пружина без отскока: трогается мягко, тормозит у цели, скорость ограничена;
 *   кадр   — между углами соседние кадры головы перетекают друг в друга (смешение по
 *            весам, нижний слой непрозрачный — тело сквозь голову не просвечивает);
 *            в покое голова всегда на угле сетки — один чёткий кадр, без двоения.
 *
 * Проверяет tests/assistant_cat.test.mjs; применяет CatMascot.jsx.
 */
import { CAT_HEAD_GRID } from './catLayout.js';

/* Курсор (lookVector шарика: x, y в [-1, 1], y > 0 — курсор ниже) → углы головы, градусы:
   поворот + — к правому краю, наклон − — вверх. Шарик обычно у нижнего края, поэтому
   вверх голова поднимается сильнее, чем опускается. */
export const targetAngles = (x, y, grid = CAT_HEAD_GRID) => {
    const yawMax = Math.max(...grid.yaw.map(Math.abs));
    const up = Math.min(...grid.pitch);
    const down = Math.max(...grid.pitch);
    return { yaw: x * yawMax, pitch: y < 0 ? -y * up : y * down };
};

/* Ближайший угол сетки. С соседнего угла уходим, только перейдя середину с запасом
   HEAD_HYSTERESIS доли шага, — иначе на границе цель металась бы между двумя углами. */
export const HEAD_HYSTERESIS = 0.18;

export const nearestIndex = (values, v, current = -1) => {
    let best = 0;
    values.forEach((value, i) => {
        if (Math.abs(value - v) < Math.abs(values[best] - v)) best = i;
    });
    if (current >= 0 && current < values.length && Math.abs(best - current) === 1) {
        const step = Math.abs(values[best] - values[current]);
        if (Math.abs(values[current] - v) - Math.abs(values[best] - v) < HEAD_HYSTERESIS * step) return current;
    }
    return best;
};

export const headCell = (angles, current = null, grid = CAT_HEAD_GRID) => ({
    col: nearestIndex(grid.yaw, angles.yaw, current ? current.col : -1),
    row: nearestIndex(grid.pitch, angles.pitch, current ? current.row : -1),
});

export const cellAngles = (cell, grid = CAT_HEAD_GRID) => ({ yaw: grid.yaw[cell.col], pitch: grid.pitch[cell.row] });

export const headName = (cell) => `head-${cell.col}-${cell.row}`;

export const centerCell = (grid = CAT_HEAD_GRID) => headCell({ yaw: 0, pitch: 0 }, null, grid);

/* Дробное место угла в сетке (сетка может быть неравномерной): 1.5 — посередине между
   вторым и третьим углом. За краями — край. */
export const gridPosition = (values, v) => {
    if (v <= values[0]) return 0;
    for (let i = 1; i < values.length; i += 1) {
        if (v <= values[i]) return i - 1 + (v - values[i - 1]) / (values[i] - values[i - 1]);
    }
    return values.length - 1;
};

/* Смешение соседних углов: до четырёх кадров с весами (билинейно по повороту и наклону),
   сумма весов — 1, самый весомый первым. На угле сетки — один кадр с весом 1. */
export const headBlend = (angles, grid = CAT_HEAD_GRID) => {
    const fx = gridPosition(grid.yaw, angles.yaw);
    const fy = gridPosition(grid.pitch, angles.pitch);
    const c0 = Math.floor(fx);
    const r0 = Math.floor(fy);
    const c1 = Math.min(c0 + 1, grid.yaw.length - 1);
    const r1 = Math.min(r0 + 1, grid.pitch.length - 1);
    const tx = fx - c0;
    const ty = fy - r0;
    const parts = new Map();
    const add = (col, row, weight) => {
        if (weight < 1e-3) return;
        const name = headName({ col, row });
        parts.set(name, (parts.get(name) || 0) + weight);
    };
    add(c0, r0, (1 - tx) * (1 - ty));
    add(c1, r0, tx * (1 - ty));
    add(c0, r1, (1 - tx) * ty);
    add(c1, r1, tx * ty);
    const total = [...parts.values()].reduce((a, b) => a + b, 0);
    return [...parts].map(([name, weight]) => ({ name, weight: weight / total })).sort((a, b) => b.weight - a.weight);
};

/* Как наложить кадры смешения без провала прозрачности: самый весомый — снизу целиком,
   каждый следующий сверху с непрозрачностью w / (сумма весов до него включительно). В итоге
   ровно взвешенная сумма кадров, а нижний слой всегда непрозрачный — тело не просвечивает. */
export const blendLayers = (blend) => {
    let sum = 0;
    return blend.map(({ name, weight }, i) => {
        sum += weight;
        return { name, opacity: i === 0 ? 1 : weight / sum, z: i + 1 };
    });
};

/* Ход головы — пружина с критическим затуханием (без отскока): трогается мягко, тормозит у
   цели, поворот на всю ширину сетки — около 0,6 с; скорость не выше MAX_TURN_SPEED, чтобы
   резкий рывок мыши не превращался в рывок головы. */
export const SPRING_OMEGA = 8;
export const MAX_TURN_SPEED = 130;

export const springStep = (state, target, dt) => {
    const t = Math.min(Math.max(dt, 0), 64) / 1000;    // секунды; паузу вкладки — одним шагом
    const w = SPRING_OMEGA;
    const axis = (x, v, to) => {
        const nv = Math.max(-MAX_TURN_SPEED, Math.min(MAX_TURN_SPEED, v + (w * w * (to - x) - 2 * w * v) * t));
        return [x + nv * t, nv];
    };
    const [yaw, vy] = axis(state.yaw, state.vy, target.yaw);
    const [pitch, vp] = axis(state.pitch, state.vp, target.pitch);
    return { yaw, pitch, vy, vp };
};

export const atRest = (state, target) => Math.abs(state.yaw - target.yaw) < 0.05
    && Math.abs(state.pitch - target.pitch) < 0.05
    && Math.abs(state.vy) < 0.5 && Math.abs(state.vp) < 0.5;

/* Курсор замер на LOOK_HOLD_MS — котик снова смотрит на человека. Моргает он при любом
   повороте головы: у каждого угла сетки своя накладка моргания. */
export const LOOK_HOLD_MS = 6000;
