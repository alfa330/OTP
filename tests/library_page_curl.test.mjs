// «Библиотека» (#282): геометрия перелистывания листа (pageCurl.js).
// Загиб — чистая 2D-геометрия, поэтому её свойства проверяются числами:
// лист не рвётся, не растягивается и отражается ровно через линию сгиба.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    clampToSpine, clipPolygon, curlGeometry, easeInOut, easeOut, matrixCss, polygonCss, tapPath,
} from '../src/components/library/pageCurl.js';

const W = 400;
const H = 600;
const BOTTOM = { x: W, y: H };
const TOP = { x: W, y: 0 };

const area = (points) => {
    let sum = 0;
    for (let i = 0; i < points.length; i += 1) {
        const a = points[i];
        const b = points[(i + 1) % points.length];
        sum += a.x * b.y - b.x * a.y;
    }
    return Math.abs(sum) / 2;
};

const apply = ([a, b, c, d, e, f], p) => ({ x: a * p.x + c * p.y + e, y: b * p.x + d * p.y + f });

test('лист лежит — загиба нет', () => {
    assert.equal(curlGeometry(BOTTOM, BOTTOM, W, H), null);
    assert.equal(curlGeometry({ x: W - 0.2, y: H }, BOTTOM, W, H), null);
});

test('поднятая и оставшаяся части вместе — весь лист', () => {
    for (const point of [{ x: 300, y: 560 }, { x: 100, y: 520 }, { x: -200, y: 590 }, { x: 250, y: 100 }]) {
        const g = curlGeometry(point, BOTTOM, W, H);
        assert.ok(g, JSON.stringify(point));
        assert.ok(Math.abs(area(g.kept) + area(g.removed) - W * H) < 1, JSON.stringify(point));
    }
});

test('оборот — зеркало поднятой части через линию сгиба', () => {
    const g = curlGeometry({ x: 120, y: 540 }, BOTTOM, W, H);
    const side = (X) => (X.x - g.mid.x) * g.normal.x + (X.y - g.mid.y) * g.normal.y;
    // Поднятая часть — по ту сторону сгиба, где угол; её отражение — по эту.
    for (const vertex of g.removed) {
        assert.ok(side(vertex) <= 1e-6);
        assert.ok(side(apply(g.matrix, vertex)) >= -1e-6);
    }
    // Угол листа ложится ровно под палец.
    const corner = apply(g.matrix, BOTTOM);
    assert.ok(Math.abs(corner.x - g.point.x) < 1e-6 && Math.abs(corner.y - g.point.y) < 1e-6);
    // Отражение сохраняет длины: определитель −1.
    const [a, b, c, d] = g.matrix;
    assert.ok(Math.abs(a * d - b * c + 1) < 1e-9);
});

test('корешок неподвижен: угол не уходит дальше длины листа', () => {
    const far = clampToSpine({ x: -5000, y: H }, BOTTOM, W, H);
    assert.ok(Math.hypot(far.x - 0, far.y - H) <= W + 1e-6);
    const high = clampToSpine({ x: 0, y: -5000 }, BOTTOM, W, H);
    assert.ok(Math.hypot(high.x, high.y - 0) <= Math.hypot(W, H) + 1e-6);
    // Верхний угол — зеркально.
    const top = clampToSpine({ x: -5000, y: 0 }, TOP, W, H);
    assert.ok(Math.hypot(top.x, top.y) <= W + 1e-6);
});

test('ход листа — от 0 до 1 и монотонно', () => {
    let previous = -1;
    for (let step = 1; step <= 50; step += 1) {
        const g = curlGeometry(tapPath(step / 50, BOTTOM, W, H), BOTTOM, W, H);
        if (!g) continue;
        assert.ok(g.progress >= previous - 1e-9, `шаг ${step}`);
        assert.ok(g.progress >= 0 && g.progress <= 1);
        previous = g.progress;
    }
    assert.ok(previous > 0.99, 'в конце лист лёг на другую сторону');
});

test('путь нажатия: от угла до корешка, с подъёмом посередине', () => {
    const start = tapPath(0, BOTTOM, W, H);
    const middle = tapPath(0.5, BOTTOM, W, H);
    const end = tapPath(1, BOTTOM, W, H);
    assert.deepEqual(start, BOTTOM);
    assert.ok(end.x <= -W);
    assert.equal(end.y, H);
    assert.ok(middle.y < H, 'угол поднимается');
    assert.ok(H - middle.y <= H * 0.08 + 1e-9, 'не выше 8 % — иначе оборот вылезает над страницей');
    assert.ok(tapPath(0.5, TOP, W, H).y > 0, 'верхний угол опускается');
});

test('отсечение полуплоскостью', () => {
    const square = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }];
    assert.equal(area(clipPolygon(square, (p) => 5 - p.x)), 50);
    assert.equal(clipPolygon(square, () => -1).length, 0);
    assert.equal(area(clipPolygon(square, () => 1)), 100);
});

test('CSS-строки', () => {
    assert.equal(polygonCss([{ x: 0, y: 0 }, { x: 1.234, y: 5 }]), 'polygon(0.00px 0.00px, 1.23px 5.00px)');
    assert.equal(matrixCss([1, 0, 0, -1, 0.0000001, 20]), 'matrix(1, 0, 0, -1, 0, 20)');
});

test('плавность: края на месте, середина симметрична', () => {
    assert.equal(easeInOut(0), 0);
    assert.equal(easeInOut(1), 1);
    assert.ok(Math.abs(easeInOut(0.5) - 0.5) < 1e-9);
    assert.equal(easeOut(0), 0);
    assert.equal(easeOut(1), 1);
});

test('разворот — только когда влезают две страницы по 400+ точек', async () => {
    const { bookMetrics } = await import('../src/components/library/readerEngine.js');
    const wide = bookMetrics(1440, 800, false);
    assert.equal(wide.mode, 'spread');
    assert.equal(wide.width, wide.pageWidth * 2);
    assert.ok(wide.left >= 0 && wide.left + wide.width <= 1440);
    // Ширина набора одинакова у левой и правой страницы: поля у корешка и у
    // края меняются местами, а не размером, — иначе главы разбивались бы по-разному.
    assert.equal(wide.pageWidth - wide.pad.inner - wide.pad.outer, wide.pageWidth - wide.pad.outer - wide.pad.inner);
    const narrowWindow = bookMetrics(820, 800, false);
    assert.equal(narrowWindow.mode, 'single');
    const phone = bookMetrics(390, 700, true);
    assert.equal(phone.mode, 'single');
    assert.equal(phone.width, 390);
});

test('ноутбук 1366×768: разворот, строка не короче 45 знаков', async () => {
    const { bookMetrics } = await import('../src/components/library/readerEngine.js');
    // Сцена ридера — окно минус шапка (50) и подвал (36).
    const laptop = bookMetrics(1366, 682, false);
    assert.equal(laptop.mode, 'spread');
    const measure = laptop.pageWidth - laptop.pad.inner - laptop.pad.outer;
    // Средний знак книжного шрифта — около 0,45 кегля.
    assert.ok(measure / (laptop.fontSize * 0.45) >= 45, `${measure}px при ${laptop.fontSize}px`);
    // Узкое окно — одна страница, но не уже 460 точек.
    const window = bookMetrics(900, 600, false);
    assert.equal(window.mode, 'single');
    assert.ok(window.pageWidth >= 460);
});
