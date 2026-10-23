/*
 * «Дисциплина»: скан документа камерой (владелец, 23.10.2026: «при скане
 * открывается камера и делается скан документа на фото»).
 *
 * Математика скана — src/components/discipline/docScan.js — проверяется на
 * синтетических кадрах: лист с «текстом» в перспективе на столе разного вида,
 * с шумом и неровным светом. Кадр рисуется здесь же, точной моделью
 * проектирования: углы листа известны, и найденные сверяются с ними.
 *
 * Запуск: node --test tests/discipline_doc_scan.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    applyHomography, buildPdf, createQuadTracker, cropUsable, defaultCorners, detectDocument, documentAspect, enhancePage,
    homography, insetCorners, isConvexQuad, keepScannedPage, orderCorners, outputSize, quadArea, SCAN_FILTERS,
    SCAN_FILTER_HINT, SCAN_MAX_SIDE, SCAN_QUALITY_STEPS, scanFileName, scanFormats, scannerBack, scanOutputType, toGray,
    turnedSize, warpPerspective,
} from '../src/components/discipline/docScan.js';

/* ── Синтетический кадр ───────────────────────────────────────────────── */

const rng = (seed) => {
    let s = seed >>> 0;
    return () => {
        s = (s * 1664525 + 1013904223) >>> 0;
        return s / 4294967296;
    };
};

const DOC_W = 210;
const DOC_H = 297;

/* Строки «текста» листа в его координатах (мм): слова — тёмные полоски. */
const docInk = (seed, { table = false, blue = false } = {}) => {
    const rand = rng(seed);
    const boxes = [];
    for (let v = 28; v < 270; v += 11) {
        let u = 22;
        while (u < 186) {
            const len = 6 + rand() * 22;
            boxes.push({ u0: u, u1: Math.min(188, u + len), v0: v, v1: v + 3.2, blue: blue && rand() < 0.25 });
            u += len + 3 + rand() * 4;
        }
    }
    if (table) {
        for (const [u0, v0, u1, v1] of [[30, 150, 180, 151.2], [30, 230, 180, 231.2], [30, 150, 31.2, 231.2], [178.8, 150, 180, 231.2]]) {
            boxes.push({ u0, u1, v0, v1, blue: false });
        }
    }
    return boxes;
};

/* Кадр w×h RGBA: стол (base, текстура), лист по углам corners, «текст», свет и шум.
   occlude(x, y) — цвет предмета поверх кадра (палец, клавиатура) или null. */
const renderScene = ({ w, h, corners, base = 70, texture = 'noise', paper = 228, seed = 1, table = false, light = 0,
    occlude = null }) => {
    const rand = rng(seed);
    const data = new Uint8ClampedArray(w * h * 4);
    const toDoc = corners ? homography(corners, [[0, 0], [DOC_W, 0], [DOC_W, DOC_H], [0, DOC_H]]) : null;
    const ink = corners ? docInk(seed, { table }) : [];
    for (let y = 0; y < h; y += 1) {
        for (let x = 0; x < w; x += 1) {
            let r;
            let g;
            let b;
            let docPoint = null;
            if (toDoc) {
                // 2×2 подвыборка — край листа сглажен, как у настоящей оптики.
                let hits = 0;
                for (const [sx, sy] of [[0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]]) {
                    const [u, v] = applyHomography(toDoc, x + sx, y + sy);
                    if (u >= 0 && v >= 0 && u <= DOC_W && v <= DOC_H) hits += 1;
                }
                if (hits) docPoint = { cover: hits / 4, uv: applyHomography(toDoc, x + 0.5, y + 0.5) };
            }
            let deskR = base;
            let deskG = base;
            let deskB = base;
            if (texture === 'wood') {
                const grain = 18 * Math.sin(y * 0.21 + Math.sin(x * 0.03) * 3) + 10 * Math.sin(y * 0.05);
                deskR = base + 25 + grain;
                deskG = base + 8 + grain * 0.8;
                deskB = base - 15 + grain * 0.5;
            } else if (texture === 'stripes') {
                // Резкие прямые полосы во всю ширину, как у стола на стенде: их
                // прямые набирают больше голосов, чем кромки листа.
                const band = 22 * Math.sin(y * 0.18) + 9 * Math.sin(y * 0.75);
                deskR = base + 28 + band;
                deskG = base + 6 + band * 0.8;
                deskB = base - 18 + band * 0.5;
            }
            const n = (rand() - 0.5) * 14;
            deskR += n; deskG += n; deskB += n;
            if (docPoint) {
                const [u, v] = docPoint.uv;
                const shade = 1 - light * (u / DOC_W);
                let pr = paper * shade;
                let pg = paper * shade;
                let pb = (paper - 6) * shade;
                const box = ink.find((item) => u >= item.u0 && u <= item.u1 && v >= item.v0 && v <= item.v1);
                if (box) {
                    if (box.blue) { pr = 45; pg = 70; pb = 175; } else { pr = 35; pg = 35; pb = 45; }
                }
                const c = docPoint.cover;
                r = pr * c + deskR * (1 - c);
                g = pg * c + deskG * (1 - c);
                b = pb * c + deskB * (1 - c);
            } else {
                r = deskR; g = deskG; b = deskB;
            }
            const over = occlude ? occlude(x, y) : null;
            if (over) [r, g, b] = over;
            const i = (y * w + x) * 4;
            data[i] = r; data[i + 1] = g; data[i + 2] = b; data[i + 3] = 255;
        }
    }
    return data;
};

/* Размытие 3×3 — оптика настоящей камеры: кромка листа расплывается на пару пикселей. */
const boxBlur = (src, w, h) => {
    const out = new Uint8ClampedArray(src.length);
    for (let y = 0; y < h; y += 1) {
        for (let x = 0; x < w; x += 1) {
            for (let c = 0; c < 4; c += 1) {
                let sum = 0;
                for (let dy = -1; dy <= 1; dy += 1) {
                    for (let dx = -1; dx <= 1; dx += 1) {
                        const yy = Math.min(h - 1, Math.max(0, y + dy));
                        const xx = Math.min(w - 1, Math.max(0, x + dx));
                        sum += src[(yy * w + xx) * 4 + c];
                    }
                }
                out[(y * w + x) * 4 + c] = sum / 9;
            }
        }
    }
    return out;
};

/* Прямоугольник листа (cx, cy, ширина, высота), повёрнутый на deg градусов. */
const rotated = (cx, cy, w, h, deg) => {
    const a = (deg * Math.PI) / 180;
    return orderCorners([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]]
        .map(([x, y]) => [cx + x * Math.cos(a) - y * Math.sin(a), cy + x * Math.sin(a) + y * Math.cos(a)]));
};

const diag = (w, h) => Math.hypot(w, h);
const cornerError = (found, truth) => Math.max(...found.map((p, i) => Math.hypot(p[0] - truth[i][0], p[1] - truth[i][1])));

const detectIn = (scene) => {
    const rgba = renderScene(scene);
    return detectDocument(toGray(rgba, scene.w, scene.h), scene.w, scene.h);
};

/* ── Поиск листа ─────────────────────────────────────────────────────── */

const SCENES = [
    { name: 'A4 прямо на тёмном столе', w: 320, h: 240, corners: [[105, 22], [215, 22], [215, 218], [105, 218]] },
    { name: 'в перспективе и с поворотом', w: 320, h: 240, corners: [[118, 30], [222, 44], [236, 214], [92, 205]], seed: 7 },
    { name: 'на деревянном столе', w: 320, h: 240, corners: [[96, 18], [210, 30], [218, 226], [84, 214]], texture: 'wood', base: 110, seed: 3 },
    { name: 'на светлом столе', w: 320, h: 240, corners: [[100, 20], [214, 26], [222, 222], [94, 216]], base: 168, paper: 236, seed: 5 },
    { name: 'кадр стоя (телефон)', w: 240, h: 320, corners: [[34, 42], [204, 52], [214, 290], [24, 280]], seed: 11 },
    { name: 'тень на листе', w: 320, h: 240, corners: [[104, 20], [218, 24], [222, 220], [100, 216]], light: 0.45, seed: 13 },
    { name: 'таблица внутри листа', w: 320, h: 240, corners: [[102, 20], [216, 20], [218, 220], [100, 220]], table: true, seed: 17 },
    { name: 'белый лист на белом столе', w: 320, h: 240, corners: [[100, 20], [214, 26], [222, 222], [94, 216]], base: 205, paper: 238, seed: 5 },
    { name: 'сильная перспектива', w: 320, h: 240, corners: [[130, 40], [196, 40], [250, 225], [70, 225]], seed: 8 },
    { name: 'угол листа за кадром', w: 320, h: 240, corners: [[110, -10], [230, 10], [236, 230], [96, 222]], seed: 4 },
    { name: 'повёрнут на 30°', w: 320, h: 240, corners: rotated(160, 120, 130, 184, 30), seed: 6 },
    { name: 'маленький лист', w: 320, h: 240, corners: [[140, 70], [196, 72], [198, 150], [138, 148]], seed: 12 },
    { name: 'лист во весь кадр', w: 320, h: 240, corners: [[6, 4], [314, 6], [316, 236], [4, 234]], seed: 14 },
    { name: 'полосы стола у кромок листа', w: 320, h: 180, corners: [[117, 23], [203, 27], [211, 157], [107, 152]],
        texture: 'stripes', base: 95, seed: 19 },
    { name: 'полосы стола, кадр стоя', w: 180, h: 320, corners: [[30, 47], [150, 53], [158, 270], [21, 265]],
        texture: 'stripes', base: 95, seed: 23 },
    { name: 'палец на кромке', w: 320, h: 240, corners: [[104, 20], [218, 24], [222, 220], [100, 216]], seed: 15,
        occlude: (x, y) => ((x - 226) ** 2 / 400 + (y - 120) ** 2 / 1600 < 1 ? [200, 150, 120] : null) },
    // Нижняя строка кадра чёрная (так режут кадр некоторые веб-камеры): у края
    // рождалась «кромка» во всю ширину, и рамка дотягивалась до неё.
    { name: 'чёрная строка у края кадра', w: 180, h: 320, corners: [[30, 47], [150, 53], [158, 262], [21, 257]],
        texture: 'stripes', base: 95, seed: 29, occlude: (x, y) => (y >= 319 ? [8, 8, 10] : null) },
    // Белый лист у светлого стола (236 на 215): рамка садилась на первую и
    // последнюю строку текста и отрезала подпись с датой (разбор, 23.10.2026).
    { name: 'белый лист у светлого стола (236 на 215)', w: 320, h: 240, corners: [[104, 20], [218, 24], [222, 220], [100, 216]],
        base: 215, paper: 236, seed: 5 },
    { name: 'клавиатура рядом на светлом столе', w: 320, h: 240, corners: [[60, 20], [170, 24], [174, 220], [56, 216]],
        base: 175, paper: 236, seed: 16, occlude: (x, y) => (x > 200 && x < 310 && y > 40 && y < 200 ? [40, 40, 44] : null) },
];

for (const scene of SCENES) {
    test(`лист находится: ${scene.name}`, () => {
        const found = detectIn(scene);
        assert.ok(found, 'лист не найден');
        const error = cornerError(found.corners, scene.corners);
        assert.ok(error < 0.025 * diag(scene.w, scene.h), `угол дальше допуска: ${error.toFixed(1)} px`);
        assert.ok(found.coverage > 0.5);
    });
}

/* Клавиатура во весь кадр: тёмные клавиши со светлыми щелями. */
const keyboard = (x, y) => {
    const kx = x % 26;
    const ky = y % 24;
    return kx < 22 && ky < 20 ? [48, 50, 56] : [176, 178, 182];
};
/* Тетрадный лист в клетку во весь кадр — края листа за кадром. */
const gridPaper = (cell) => (x, y) => (x % cell < 1 || y % cell < 1 ? [150, 170, 200] : [232, 232, 228]);

test('пёстрый кадр без листа: клавиатура, клетка тетради — рамки нет', () => {
    for (const occlude of [keyboard, gridPaper(9), gridPaper(14)]) {
        assert.equal(detectIn({ w: 320, h: 240, corners: null, base: 120, seed: 3, occlude }), null);
    }
});

test('на кадре без листа рамки нет', () => {
    for (const seed of [2, 4, 6]) {
        assert.equal(detectIn({ w: 320, h: 240, corners: null, base: 100, seed }), null);
        assert.equal(detectIn({ w: 320, h: 240, corners: null, texture: 'wood', base: 110, seed }), null);
        assert.equal(detectIn({ w: 320, h: 180, corners: null, texture: 'stripes', base: 95, seed }), null);
    }
});

test('пустой и крошечный кадр не роняют поиск', () => {
    assert.equal(detectDocument(null, 0, 0), null);
    assert.equal(detectDocument(new Uint8Array(16 * 16), 16, 16), null);
    assert.equal(detectDocument(new Uint8Array(320 * 240).fill(128), 320, 240), null);
});

test('живой поиск укладывается в бюджет кадра', () => {
    const scene = SCENES[1];
    const gray = toGray(renderScene(scene), scene.w, scene.h);
    detectDocument(gray, scene.w, scene.h);
    const started = performance.now();
    for (let i = 0; i < 10; i += 1) detectDocument(gray, scene.w, scene.h);
    const each = (performance.now() - started) / 10;
    // На стенде ~5–15 мс; телефон медленнее в 3–5 раз, кадр поиска — раз в 180 мс.
    assert.ok(each < 40, `${each.toFixed(1)} мс на кадр`);
});

/* ── Геометрия ───────────────────────────────────────────────────────── */

test('углы упорядочиваются: ЛВ, ПВ, ПН, ЛН', () => {
    const expected = [[10, 10], [90, 12], [95, 80], [5, 85]];
    for (const shuffled of [[expected[2], expected[0], expected[3], expected[1]], [...expected].reverse()]) {
        assert.deepEqual(orderCorners(shuffled), expected);
    }
});

test('выпуклость и площадь рамки', () => {
    assert.ok(isConvexQuad([[0, 0], [10, 0], [10, 10], [0, 10]]));
    assert.ok(!isConvexQuad([[0, 0], [10, 10], [10, 0], [0, 10]]), 'перекрещенные углы');
    assert.ok(!isConvexQuad([[0, 0], [10, 0], [3, 3], [0, 10]]), 'вдавленный угол');
    assert.ok(!isConvexQuad([[0, 0], [10, 0], [20, 0], [0, 10]]), 'три угла на прямой');
    assert.equal(quadArea([[0, 0], [10, 0], [10, 10], [0, 10]]), 100);
    assert.deepEqual(defaultCorners(100, 200, 0.1), [[10, 20], [90, 20], [90, 180], [10, 180]]);
});

test('проективное преобразование переводит углы в углы', () => {
    const from = [[0, 0], [100, 0], [100, 140], [0, 140]];
    const to = [[12, 30], [210, 18], [240, 300], [4, 280]];
    const H = homography(from, to);
    from.forEach((p, i) => {
        const [x, y] = applyHomography(H, p[0], p[1]);
        assert.ok(Math.abs(x - to[i][0]) < 1e-6 && Math.abs(y - to[i][1]) < 1e-6);
    });
});

test('выпрямление возвращает лист в исходный вид', () => {
    const w = 360;
    const h = 300;
    const corners = [[90, 30], [262, 52], [280, 280], [70, 262]];
    const scene = renderScene({ w, h, corners, seed: 21 });
    const ow = 210;
    const oh = 297;
    const flat = warpPerspective(scene, w, h, corners, ow, oh);
    // Сверка с тем же листом, нарисованным прямо (без перспективы и стола).
    const reference = renderScene({ w: ow, h: oh, corners: [[0, 0], [ow, 0], [ow, oh], [0, oh]], seed: 21 });
    let diff = 0;
    let n = 0;
    for (let y = 8; y < oh - 8; y += 1) {
        for (let x = 8; x < ow - 8; x += 1) {
            const i = (y * ow + x) * 4;
            diff += Math.abs(flat[i] - reference[i]);
            n += 1;
        }
    }
    assert.ok(diff / n < 22, `среднее расхождение ${(diff / n).toFixed(1)}`);
});

test('край страницы без полоски стола: поля срезаются по проекции', () => {
    // Прямоугольник: отступ одинаковый со всех сторон.
    const flat = insetCorners([[0, 0], [200, 0], [200, 300], [0, 300]], 200, 300, 0.01);
    assert.deepEqual(flat.map((p) => p.map((v) => Math.round(v * 100) / 100)), [[3, 3], [197, 3], [197, 297], [3, 297]]);
    // В перспективе ближний (широкий) край срезается шире дальнего.
    const persp = insetCorners([[100, 20], [180, 20], [230, 300], [50, 300]], 210, 297, 0.02);
    assert.ok(persp[3][1] - 300 < persp[0][1] - 20 && 300 - persp[3][1] > persp[0][1] - 20, 'низ срезан шире верха');
    // Скан на тёмном столе: кадр размыт, как у настоящей оптики, а рамка стоит
    // на середине размытой кромки — на 1,5 px снаружи листа. Без среза по краю
    // страницы после фильтра — тёмная полоса стола, со срезом — белое поле.
    const w = 360;
    const h = 300;
    const corners = [[90, 30], [262, 52], [280, 280], [70, 262]];
    const soft = boxBlur(boxBlur(renderScene({ w, h, corners, seed: 31 }), w, h), w, h);
    const cx = corners.reduce((sum, p) => sum + p[0], 0) / 4;
    const cy = corners.reduce((sum, p) => sum + p[1], 0) / 4;
    const detected = corners.map(([x, y]) => {
        const d = Math.hypot(x - cx, y - cy);
        return [x + ((x - cx) / d) * 1.5, y + ((y - cy) / d) * 1.5];
    });
    const edgeMin = (cornersUsed) => {
        const page = warpPerspective(soft, w, h, cornersUsed, 210, 297);
        enhancePage(page, 210, 297, 'gray');
        let min = 255;
        for (let x = 0; x < 210; x += 1) {
            for (const y of [0, 296]) min = Math.min(min, page[(y * 210 + x) * 4]);
        }
        for (let y = 0; y < 297; y += 1) {
            for (const x of [0, 209]) min = Math.min(min, page[(y * 210 + x) * 4]);
        }
        return min;
    };
    assert.ok(edgeMin(detected) < 100, 'без среза кромка светлая — проверка устарела');
    assert.ok(edgeMin(insetCorners(detected, 210, 297)) > 200, 'после среза по краю осталась полоска стола');
});

test('за краем снимка выпрямление даёт белое, а не растянутый край', () => {
    const src = new Uint8ClampedArray(10 * 10 * 4).fill(0);
    const out = warpPerspective(src, 10, 10, [[-20, -20], [30, -20], [30, 30], [-20, 30]], 50, 50);
    assert.equal(out[0], 255);
    const mid = (25 * 50 + 25) * 4;
    assert.equal(out[mid], 0);
});

/* Модель камеры-обскуры: прямоугольник W×H на столе под наклоном. */
const project = ({ W, H, f, tiltX, tiltY, z, w, h }) => {
    const cx = Math.cos(tiltX);
    const sx = Math.sin(tiltX);
    const cy = Math.cos(tiltY);
    const sy = Math.sin(tiltY);
    return [[0, 0], [W, 0], [W, H], [0, H]].map(([X0, Y0]) => {
        const X = X0 - W / 2;
        const Y = Y0 - H / 2;
        // Поворот вокруг оси x, затем вокруг y.
        const y1 = Y * cx;
        const z1 = Y * sx;
        const x2 = X * cy + z1 * sy;
        const z2 = -X * sy + z1 * cy;
        const depth = z2 + z;
        return [w / 2 + (f * x2) / depth, h / 2 + (f * y1) / depth];
    });
};

test('настоящие пропорции листа под наклоном камеры — устойчиво к шуму углов', () => {
    // 1000 снимков A4: разные кадры, настоящий фокус камеры 0,6–1,0 длинной
    // стороны, наклон до ±23°, поворот до ±14°, шум углов ±0,3 % кадра (4 px на 1280).
    // Оценка фокуса по самим углам здесь давала хвост до 164 % — лист вдвое уже.
    const rand = rng(41);
    const errors = [];
    let snapped = 0;
    for (const [w, h] of [[1280, 720], [720, 1280], [3840, 2160], [1080, 1440]]) {
        const long = Math.max(w, h);
        for (const share of [0.6, 0.72, 0.85, 1.0]) {
            for (let k = 0; k < 63; k += 1) {
                const f = share * long;
                const z = (f * 297) / (0.75 * Math.min(w, h)) * (0.85 + rand() * 0.5);
                const clean = project({ W: 210, H: 297, f, tiltX: (rand() - 0.5) * 0.8, tiltY: (rand() - 0.5) * 0.6, z, w, h });
                const corners = clean.map(([x, y]) => [x + (rand() - 0.5) * 0.006 * long, y + (rand() - 0.5) * 0.006 * long]);
                errors.push(Math.abs(documentAspect(corners, w, h) / (210 / 297) - 1));
                const size = outputSize(corners, w, h, 10000);
                if (Math.abs(size.width / size.height - 210 / 297) < 0.002) snapped += 1;
            }
        }
    }
    errors.sort((a, b) => a - b);
    assert.ok(errors[Math.floor(errors.length * 0.9)] < 0.03, `p90 ${errors[Math.floor(errors.length * 0.9)]}`);
    assert.ok(errors[errors.length - 1] < 0.08, `худший ${errors[errors.length - 1]}`);
    assert.ok(snapped / errors.length > 0.98, `приведено к A4: ${snapped} из ${errors.length}`);
    // Без перспективы — пропорции сторон.
    assert.ok(Math.abs(documentAspect([[150, 125], [250, 125], [250, 175], [150, 175]], 400, 300) - 2) < 1e-6);
});

test('размер страницы: потолок по длинной стороне и не A4 остаётся как есть', () => {
    const big = outputSize([[0, 0], [3000, 0], [3000, 4243], [0, 4243]], 4000, 5000, 2400);
    assert.equal(Math.max(big.width, big.height), 2400);
    const receipt = outputSize([[0, 0], [100, 0], [100, 300], [0, 300]], 400, 400, 2400);
    assert.ok(Math.abs(receipt.width / receipt.height - 1 / 3) < 0.01);
});

/* ── Живая рамка ─────────────────────────────────────────────────────── */

test('живая рамка не мигает: два согласных кадра, сглаживание, угасание', () => {
    const tracker = createQuadTracker({ hold: 3 });
    const quad = [[10, 10], [100, 10], [100, 140], [10, 140]];
    const shifted = quad.map(([x, y]) => [x + 4, y + 2]);
    const elsewhere = quad.map(([x, y]) => [x + 120, y]);
    assert.equal(tracker.push(quad, 320, 240), null, 'одного кадра мало');
    assert.deepEqual(tracker.push(quad, 320, 240), quad);
    assert.deepEqual(tracker.push(shifted, 320, 240), quad.map(([x, y]) => [x + 2, y + 1]), 'близкое — сглаживается');
    const before = tracker.current;
    assert.deepEqual(tracker.push(elsewhere, 320, 240), before, 'одиночный выброс не двигает рамку');
    assert.deepEqual(tracker.push(elsewhere, 320, 240), elsewhere);
    tracker.push(null, 320, 240);
    tracker.push(null, 320, 240);
    assert.ok(tracker.current, 'два пустых кадра — рамка ещё видна');
    assert.equal(tracker.push(null, 320, 240), null);
});

/* ── «Как скан» ──────────────────────────────────────────────────────── */

const page = (w, h, light) => {
    const data = new Uint8ClampedArray(w * h * 4);
    const kind = new Uint8Array(w * h); // 0 — бумага, 1 — чёрные чернила, 2 — синие
    for (let y = 0; y < h; y += 1) {
        for (let x = 0; x < w; x += 1) {
            const shade = 1 - light * (x / w) - 0.15 * (y / h);
            const i = (y * w + x) * 4;
            let value = [214 * shade, 208 * shade, 190 * shade]; // тёплый свет лампы
            const row = Math.floor(y / 14);
            if (y % 14 >= 5 && y % 14 <= 7 && x > 30 && x < w - 30 && row % 3 !== 2) {
                value = row % 4 === 1 ? [40 * shade, 62 * shade, 160 * shade] : [30 * shade, 30 * shade, 38 * shade];
                kind[y * w + x] = row % 4 === 1 ? 2 : 1;
            }
            data[i] = value[0]; data[i + 1] = value[1]; data[i + 2] = value[2]; data[i + 3] = 255;
        }
    }
    return { data, kind };
};

test('фильтр «Цвет»: лист белый без тени, синяя подпись остаётся синей', () => {
    const w = 420;
    const h = 594;
    const { data, kind } = page(w, h, 0.5);
    enhancePage(data, w, h, 'color');
    let paperMin = 255;
    let paperSum = 0;
    let paperN = 0;
    let blackMax = 0;
    let blueGap = Infinity;
    for (let k = 0; k < w * h; k += 1) {
        const i = k * 4;
        const lum = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        if (kind[k] === 0) {
            paperMin = Math.min(paperMin, lum);
            paperSum += lum;
            paperN += 1;
        } else if (kind[k] === 1) blackMax = Math.max(blackMax, lum);
        else blueGap = Math.min(blueGap, data[i + 2] - data[i]);
    }
    assert.ok(paperSum / paperN > 250, `бумага в среднем ${(paperSum / paperN).toFixed(0)}`);
    assert.ok(paperMin > 225, `самое тёмное место бумаги ${paperMin.toFixed(0)} — тень осталась`);
    assert.ok(blackMax < 90, `чёрные чернила посерели до ${blackMax.toFixed(0)}`);
    assert.ok(blueGap > 60, `синий отличается от красного на ${blueGap}`);
});

test('фильтр «Ч/б» — серый лист, «Фото» — без изменений', () => {
    const w = 210;
    const h = 297;
    const { data } = page(w, h, 0.3);
    const original = Uint8ClampedArray.from(data);
    enhancePage(data, w, h, 'photo');
    assert.deepEqual(data, original);
    enhancePage(data, w, h, 'gray');
    for (let i = 0; i < data.length; i += 4 * 97) {
        assert.equal(data[i], data[i + 1]);
        assert.equal(data[i], data[i + 2]);
    }
});

test('фильтры и подсказка «i» называют одни и те же варианты', () => {
    assert.deepEqual(SCAN_FILTERS.map((f) => f.value), ['color', 'gray', 'photo']);
    for (const { label } of SCAN_FILTERS) assert.ok(SCAN_FILTER_HINT.includes(`${label} —`), label);
});

/* ── Файл ────────────────────────────────────────────────────────────── */

// JPEG 8×8 (серый), снятый с canvas: настоящие маркеры SOI/SOF0/EOI.
const TINY_JPEG = Uint8Array.from(Buffer.from(
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/'
    + '2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAIAAgDASIAAhEBAxEB/8QAFQAB'
    + 'AQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhED'
    + 'EQA/AKpgB//Z', 'base64'));

test('PDF: страницы, размер A4 и таблица смещений сходятся', async () => {
    const pdf = buildPdf([{ jpeg: TINY_JPEG, width: 1700, height: 2404 }, { jpeg: TINY_JPEG, width: 2404, height: 1700 }]);
    const text = Buffer.from(pdf).toString('latin1');
    assert.ok(text.startsWith('%PDF-1.4\n'));
    assert.ok(text.trimEnd().endsWith('%%EOF'));
    // Таблица смещений: ровно 2 + 3·страниц записей «n», каждая строка — 20 байт,
    // и каждое смещение указывает на начало своего объекта.
    const startxref = Number(text.match(/startxref\n(\d+)\n/)[1]);
    const table = text.slice(startxref);
    assert.ok(table.startsWith('xref\n0 9\n'), table.slice(0, 20));
    const rows = table.split('trailer')[0].split('\n').slice(2, -1);
    assert.equal(rows.length, 9);
    for (const row of rows) assert.equal(Buffer.byteLength(`${row}\n`, 'latin1'), 20, JSON.stringify(row));
    assert.ok(rows[0].endsWith(' f '));
    rows.slice(1).forEach((row, index) => {
        assert.ok(row.endsWith(' n '), row);
        const offset = Number(row.slice(0, 10));
        assert.ok(text.slice(offset).startsWith(`${index + 1} 0 obj\n`), `объект ${index + 1}`);
    });
    assert.match(text, /trailer\n<< \/Size 9 \/Root 1 0 R >>/);
    // /Length каждого потока — ровно его байты.
    const streams = [...text.matchAll(/\/Length (\d+)[^>]*>>\nstream\n/g)];
    assert.equal(streams.length, 4);
    for (const match of streams) {
        const start = match.index + match[0].length;
        assert.equal(text.indexOf('\nendstream', start), start + Number(match[1]), `поток в ${start}`);
    }
    // И разбирает его тот же pdf.js, что показывает файлы в разделе.
    const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs');
    const task = pdfjs.getDocument({ data: pdf.slice(), isEvalSupported: false, verbosity: 0 });
    const doc = await task.promise;
    assert.equal(doc.numPages, 2);
    const first = (await doc.getPage(1)).view;
    const second = (await doc.getPage(2)).view;
    assert.ok(Math.abs(first[3] - 841.89) < 0.01 && Math.abs(first[2] - 595.35) < 0.5, `портрет ${first}`);
    assert.ok(Math.abs(second[2] - 841.89) < 0.01 && Math.abs(second[3] - 595.35) < 0.5, `альбом ${second}`);
    await task.destroy();
});

test('куда скан годится: по accept поля', () => {
    assert.deepEqual(scanFormats('.pdf,.jpg,.jpeg,.png,.heic,.doc,.docx,image/*,application/pdf'), { jpeg: true, pdf: true, any: true });
    assert.deepEqual(scanFormats('.pdf,.jpg,.jpeg,.png,image/jpeg,image/png,application/pdf'), { jpeg: true, pdf: true, any: true });
    assert.deepEqual(scanFormats('.docx'), { jpeg: false, pdf: false, any: false });
    assert.deepEqual(scanFormats('.pdf'), { jpeg: false, pdf: true, any: true });
    assert.deepEqual(scanFormats('image/*'), { jpeg: true, pdf: false, any: true });
    assert.deepEqual(scanFormats(' application/pdf '), { jpeg: false, pdf: true, any: true });
    assert.deepEqual(scanFormats(''), { jpeg: false, pdf: false, any: false });
});

test('имя файла скана — без двоеточия, с секундами', () => {
    const name = scanFileName(new Date(2026, 9, 23, 7, 5, 9), 'pdf');
    assert.equal(name, 'Скан 23.10.2026 07-05-09.pdf');
    assert.ok(!name.includes(':'));
    // Исправленная версия через 20 секунд — уже другое имя.
    assert.notEqual(scanFileName(new Date(2026, 9, 23, 7, 5, 29), 'pdf'), name);
});

/* ── Решения окна скана ──────────────────────────────────────────────── */

test('одна страница — JPEG, несколько — один PDF', () => {
    const both = { jpeg: true, pdf: true };
    assert.equal(scanOutputType(1, both), 'jpeg');
    assert.equal(scanOutputType(2, both), 'pdf');
    assert.equal(scanOutputType(3, both), 'pdf', 'три страницы — не JPEG первой');
    assert.equal(scanOutputType(1, { jpeg: false, pdf: true }), 'pdf');
});

test('ступени сжатия: качество и размер только убывают, первая — без потерь размера', () => {
    assert.deepEqual(SCAN_QUALITY_STEPS[0], { quality: 0.85, maxSide: SCAN_MAX_SIDE });
    for (let i = 1; i < SCAN_QUALITY_STEPS.length; i += 1) {
        assert.ok(SCAN_QUALITY_STEPS[i].quality < SCAN_QUALITY_STEPS[i - 1].quality);
        assert.ok(SCAN_QUALITY_STEPS[i].maxSide <= SCAN_QUALITY_STEPS[i - 1].maxSide);
    }
    assert.ok(SCAN_QUALITY_STEPS.at(-1).quality >= 0.5, 'ниже 0,5 подпись рассыпается');
});

test('поворот страницы: 90 и 270 меняют стороны холста', () => {
    assert.deepEqual(turnedSize(200, 300, 0), { width: 200, height: 300, turn: 0 });
    assert.deepEqual(turnedSize(200, 300, 90), { width: 300, height: 200, turn: 90 });
    assert.deepEqual(turnedSize(200, 300, 180), { width: 200, height: 300, turn: 180 });
    assert.deepEqual(turnedSize(200, 300, 270), { width: 300, height: 200, turn: 270 });
    assert.deepEqual(turnedSize(200, 300, -90), { width: 300, height: 200, turn: 270 });
    assert.deepEqual(turnedSize(200, 300, 450), { width: 300, height: 200, turn: 90 });
});

test('«назад»: шаг за шагом, снятое не теряется без вопроса', () => {
    assert.equal(scannerBack({ step: 'review', pages: 2, busy: 'save', confirm: false }), 'stay');
    assert.equal(scannerBack({ step: 'review', pages: 2, busy: '', confirm: true }), 'close-confirm');
    assert.equal(scannerBack({ step: 'crop', pages: 0, busy: '', confirm: false }), 'leave-crop');
    assert.equal(scannerBack({ step: 'camera', pages: 1, busy: '', confirm: false }), 'to-review');
    assert.equal(scannerBack({ step: 'review', pages: 1, busy: '', confirm: false }), 'confirm');
    assert.equal(scannerBack({ step: 'camera', pages: 0, busy: '', confirm: false }), 'close');
});

test('рамку можно оставить: выпуклая, из чисел, не щель', () => {
    assert.ok(cropUsable([[10, 10], [190, 12], [195, 280], [5, 285]], 200, 300));
    assert.ok(!cropUsable([[10, 10], [195, 280], [190, 12], [5, 285]], 200, 300), 'перекрещенные углы');
    assert.ok(!cropUsable([[NaN, NaN], [NaN, NaN], [NaN, NaN], [NaN, NaN]], 200, 300), 'NaN');
    assert.ok(!cropUsable([[0, 0], [200, 0], [200, 0.5], [0, 1]], 200, 300), 'щель');
    assert.ok(!isConvexQuad([[0, 0], [Infinity, 0], [10, 10], [0, 10]]));
});

test('«Оставить»: новая страница в конец, правка краёв — новый id, углы по порядку', () => {
    const shot = { blob: 'b1', url: 'u1', width: 100, height: 140, editing: null,
        corners: [[90, 10], [10, 10], [10, 130], [90, 130]] }; // против часовой — зеркально
    const first = keepScannedPage([], shot, 'p1');
    assert.equal(first.current, 0);
    assert.equal(first.pages.length, 1);
    assert.deepEqual(first.pages[0].corners, [[10, 10], [90, 10], [90, 130], [10, 130]], 'страница не зеркальная');
    assert.equal(first.pages[0].rotation, 0);
    const second = keepScannedPage(first.pages, { ...shot, blob: 'b2' }, 'p2');
    assert.equal(second.current, 1);
    assert.deepEqual(second.pages.map((p) => p.id), ['p1', 'p2']);
    const turned = second.pages.map((p, i) => (i === 0 ? { ...p, rotation: 90 } : p));
    const recrop = keepScannedPage(turned, { ...shot, editing: 0, corners: [[12, 14], [88, 12], [90, 128], [10, 126]] }, 'p3');
    assert.equal(recrop.current, 0);
    assert.deepEqual(recrop.pages.map((p) => p.id), ['p3', 'p2'], 'новый id — превью из кэша не покажется');
    assert.deepEqual(recrop.pages[0].corners, [[12, 14], [88, 12], [90, 128], [10, 126]]);
    assert.equal(recrop.pages[0].rotation, 90, 'поворот страницы сохранился');
    assert.equal(recrop.pages[0].blob, 'b1', 'снимок тот же');
});

/* ── Выпрямление и «как скан»: детали ─────────────────────────────────── */

test('выпрямление без сдвига на полпикселя: линейный перепад остаётся линейным', () => {
    const w = 32;
    const h = 8;
    const src = new Uint8ClampedArray(w * h * 4);
    for (let y = 0; y < h; y += 1) {
        for (let x = 0; x < w; x += 1) {
            const i = (y * w + x) * 4;
            src[i] = x * 8; src[i + 1] = x * 8; src[i + 2] = x * 8; src[i + 3] = 255;
        }
    }
    const same = warpPerspective(src, w, h, [[0, 0], [w, 0], [w, h], [0, h]], w, h);
    for (let i = 0; i < src.length; i += 1) assert.ok(Math.abs(same[i] - src[i]) <= 1, `копия 1:1, байт ${i}`);
    // Вдвое крупнее: середина между пикселями — среднее соседей, а не ступенька.
    const big = warpPerspective(src, w, h, [[0, 0], [w, 0], [w, h], [0, h]], w * 2, h * 2);
    for (let x = 2; x < w * 2 - 2; x += 1) {
        const expected = ((x + 0.5) / 2 - 0.5) * 8;
        assert.ok(Math.abs(big[(4 * w * 2 + x) * 4] - expected) <= 1.5, `x=${x}: ${big[(4 * w * 2 + x) * 4]} против ${expected}`);
    }
});

test('размер страницы не раздувается выше снимка', () => {
    const small = outputSize([[150, 50], [250, 50], [250, 350], [150, 350]], 400, 400, SCAN_MAX_SIDE);
    assert.ok(Math.max(small.width, small.height) <= 300 * 1.02, JSON.stringify(small));
});

test('«Цвет»: синий остаётся ярко-синим, тёмная печать не выбеливается, тёмный кадр не вытягивается', () => {
    const w = 420;
    const h = 594;
    const page = new Uint8ClampedArray(w * h * 4);
    const ink = new Uint8Array(w * h);
    for (let y = 0; y < h; y += 1) {
        for (let x = 0; x < w; x += 1) {
            const i = (y * w + x) * 4;
            const blue = y % 14 >= 5 && y % 14 <= 7 && x > 30 && x < w - 30;
            const v = blue ? [40, 62, 160] : [214, 208, 190];
            ink[y * w + x] = blue ? 1 : 0;
            page[i] = v[0]; page[i + 1] = v[1]; page[i + 2] = v[2]; page[i + 3] = 255;
        }
    }
    enhancePage(page, w, h, 'color');
    let gap = Infinity;
    for (let k = 0; k < w * h; k += 1) if (ink[k]) gap = Math.min(gap, page[k * 4 + 2] - page[k * 4]);
    assert.ok(gap >= 215, `синий отличается от красного всего на ${gap}`);

    // Серый блок 8×8 клеток (фото на анкете) не становится белым.
    const W = 480;
    const H = 680;
    const block = Math.round(Math.max(W, H) / 40);
    const photo = new Uint8ClampedArray(W * H * 4);
    for (let y = 0; y < H; y += 1) {
        for (let x = 0; x < W; x += 1) {
            const i = (y * W + x) * 4;
            const inside = x >= block * 4 && x < block * 12 && y >= block * 6 && y < block * 14;
            const v = inside ? 70 : 210;
            photo[i] = v; photo[i + 1] = v; photo[i + 2] = v; photo[i + 3] = 255;
        }
    }
    enhancePage(photo, W, H, 'color');
    let sum = 0;
    let n = 0;
    for (let y = block * 9; y < block * 11; y += 1) {
        for (let x = block * 7; x < block * 9; x += 1) { sum += photo[(y * W + x) * 4]; n += 1; }
    }
    assert.ok(sum / n < 200, `середина серого блока выбелена до ${(sum / n).toFixed(0)}`);

    // Почти чёрный кадр (крышка объектива) остаётся тёмным.
    const dark = new Uint8ClampedArray(200 * 280 * 4);
    for (let k = 0; k < 200 * 280; k += 1) {
        const v = 18 + (k % 5);
        dark[k * 4] = v; dark[k * 4 + 1] = v; dark[k * 4 + 2] = v; dark[k * 4 + 3] = 255;
    }
    enhancePage(dark, 200, 280, 'gray');
    let total = 0;
    for (let k = 0; k < 200 * 280; k += 1) total += dark[k * 4];
    assert.ok(total / (200 * 280) < 40, `тёмный кадр вытянут до ${(total / (200 * 280)).toFixed(0)}`);
});
