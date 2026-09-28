/*
 * Геометрия перелистывания страницы («загиб листа», как в Apple Books).
 * Чистая математика без DOM — её грузит Node в tests/library_page_curl.test.mjs.
 *
 * КАК УСТРОЕН ЗАГИБ. Лист W×H лежит корешком по левой кромке (x = 0). Палец
 * тянет угол C (правый нижний или правый верхний) в точку P. Линия сгиба —
 * серединный перпендикуляр к отрезку CP: всё, что по ту сторону от неё, где
 * угол C, отгибается и ложится зеркально поверх листа. Отсюда три фигуры:
 *
 *   kept     — часть листа, что осталась лежать (обрезка текущей страницы);
 *   removed  — часть, что поднялась (там открывается следующая страница);
 *   flap     — та же removed, отражённая через линию сгиба: оборот листа.
 *
 * Отражение — аффинная матрица с определителем −1: CSS рисует оборот
 * элементом той же страницы с `transform: matrix(...)` и `clip-path` по
 * removed в его собственных координатах. Никакого WebGL и 3D — только 2D, и
 * потому 60 кадров даже на телефоне.
 *
 * БУМАГА НЕ РВЁТСЯ. Точки корешка неподвижны, поэтому угол не может уйти
 * дальше длины листа от своего угла корешка и дальше диагонали — от
 * противоположного (clampToSpine). Без этого на быстром свайпе лист
 * «растягивался» и сгиб отрывался от корешка.
 */

const EPS = 1e-6;

const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

/* Удерживает точку угла там, куда её может довести целый лист. */
export const clampToSpine = (point, corner, width, height) => {
    let p = { x: point.x, y: point.y };
    const sameSide = { x: 0, y: corner.y };
    const across = { x: 0, y: height - corner.y };
    const d1 = dist(p, sameSide);
    if (d1 > width) {
        p = { x: sameSide.x + (p.x - sameSide.x) * (width / d1), y: sameSide.y + (p.y - sameSide.y) * (width / d1) };
    }
    const diagonal = Math.hypot(width, height);
    const d2 = dist(p, across);
    if (d2 > diagonal) {
        p = { x: across.x + (p.x - across.x) * (diagonal / d2), y: across.y + (p.y - across.y) * (diagonal / d2) };
    }
    return p;
};

/* Отсечение многоугольника полуплоскостью side(X) >= 0 (Сазерленд — Ходжман). */
export const clipPolygon = (points, side) => {
    const out = [];
    for (let i = 0; i < points.length; i += 1) {
        const a = points[i];
        const b = points[(i + 1) % points.length];
        const sa = side(a);
        const sb = side(b);
        if (sa >= 0) out.push(a);
        if ((sa >= 0) !== (sb >= 0)) {
            const t = sa / (sa - sb);
            out.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
        }
    }
    return out;
};

const polygonArea = (points) => {
    let area = 0;
    for (let i = 0; i < points.length; i += 1) {
        const a = points[i];
        const b = points[(i + 1) % points.length];
        area += a.x * b.y - b.x * a.y;
    }
    return Math.abs(area) / 2;
};

/*
 * Всё, что нужно для одного кадра загиба.
 *
 * corner — поднимаемый угол листа, point — где сейчас палец (уже в
 * координатах листа). Возвращает null, если лист лежит плоско (палец у
 * самого угла) — тогда страница рисуется без обрезки и без оборота.
 *
 * gradient.start — где линия сгиба пересекает ось CSS-градиента
 * (linear-gradient(angle ...)) у прямоугольника W×H: цвета оборота и тени
 * отсчитываются в пикселях от сгиба, а не в процентах от диагонали.
 */
export const curlGeometry = (point, corner, width, height) => {
    const p = clampToSpine(point, corner, width, height);
    const dx = p.x - corner.x;
    const dy = p.y - corner.y;
    const length = Math.hypot(dx, dy);
    if (length < 0.5) return null;
    const n = { x: dx / length, y: dy / length };
    const mid = { x: (p.x + corner.x) / 2, y: (p.y + corner.y) / 2 };
    const side = (X) => (X.x - mid.x) * n.x + (X.y - mid.y) * n.y;
    const rect = [{ x: 0, y: 0 }, { x: width, y: 0 }, { x: width, y: height }, { x: 0, y: height }];
    const kept = clipPolygon(rect, side);
    const removed = clipPolygon(rect, (X) => -side(X));
    if (removed.length < 3 || polygonArea(removed) < EPS) return null;

    const k = mid.x * n.x + mid.y * n.y;
    const matrix = [
        1 - 2 * n.x * n.x, -2 * n.x * n.y,
        -2 * n.x * n.y, 1 - 2 * n.y * n.y,
        2 * k * n.x, 2 * k * n.y,
    ];
    const depth = Math.max(1, ...removed.map((X) => -side(X)));

    // Ось градиента смотрит от сгиба в поднятую часть: d = −n.
    const d = { x: -n.x, y: -n.y };
    const angle = Math.atan2(d.x, -d.y);
    const gradientLength = Math.abs(width * Math.sin(angle)) + Math.abs(height * Math.cos(angle));
    const start = (mid.x - width / 2) * d.x + (mid.y - height / 2) * d.y + gradientLength / 2;

    return {
        point: p,
        normal: n,
        mid,
        kept,
        removed,
        matrix,
        depth,
        gradient: { angle: (angle * 180) / Math.PI, start, length: gradientLength },
        // Насколько лист перевёрнут: 0 — лежит, 1 — лёг на левую сторону.
        progress: Math.max(0, Math.min(1, (corner.x - p.x) / (2 * width))),
    };
};

export const polygonCss = (points) => (
    `polygon(${points.map((pt) => `${pt.x.toFixed(2)}px ${pt.y.toFixed(2)}px`).join(', ')})`
);

export const matrixCss = (m) => `matrix(${m.map((v) => (Math.abs(v) < EPS ? 0 : Number(v.toFixed(6)))).join(', ')})`;

/*
 * Путь угла при перелистывании нажатием (без пальца): от угла через середину
 * листа к корешку, с подъёмом посередине — сгиб при этом наклоняется, и лист
 * загибается по диагонали, а не складывается ровной вертикалью.
 * t — 0…1, уже после смягчения (easing).
 */
export const tapPath = (t, corner, width, height) => {
    const top = corner.y < height / 2;
    // Подъём угла посередине хода. Больше 8 % высоты — и оборот листа на
    // крутом сгибе вылезал над верхним краем страницы.
    const lift = height * 0.08 * Math.sin(Math.PI * t);
    return {
        x: corner.x - t * (2 * width + 2),
        y: top ? corner.y + lift : corner.y - lift,
    };
};

/* Плавность хода листа: быстрый старт, мягкая посадка (как у iOS). */
export const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - ((-2 * t + 2) ** 3) / 2);
export const easeOut = (t) => 1 - ((1 - t) ** 3);
