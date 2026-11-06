// «Доска почёта»: арифметика вырезания фона (src/components/events/cutout/cutoutMath.js).
// Нейросеть здесь не запускается — только то, что готовит ей вход и разбирает её выход.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    MODEL_SHORT_SIDE, alphaBounds, coverage, cropAround, fitSize, matteToRGBA, modelInputSize, toModelInput,
} from '../src/components/events/cutout/cutoutMath.js';

test('вход модели: короткая сторона 512, обе кратны 32, длинная не больше 1024', () => {
    assert.deepEqual(modelInputSize(1200, 1600), { width: 512, height: 672 });
    assert.deepEqual(modelInputSize(1600, 1200), { width: 672, height: 512 });
    assert.deepEqual(modelInputSize(512, 512), { width: 512, height: 512 });
    // Маленький снимок тоже растягивается до 512: так модель обучена.
    assert.deepEqual(modelInputSize(200, 300), { width: 512, height: 768 });
    // Панорама 4:1 — длинная сторона упирается в потолок, короткая сжимается.
    assert.deepEqual(modelInputSize(4000, 1000), { width: 1024, height: 256 });
    for (const [w, h] of [[1067, 1600], [1600, 1067], [999, 1777], [3000, 4000]]) {
        const size = modelInputSize(w, h);
        assert.equal(size.width % 32, 0);
        assert.equal(size.height % 32, 0);
        assert.ok(Math.max(size.width, size.height) <= 1024);
        assert.ok(Math.abs(Math.min(size.width, size.height) - MODEL_SHORT_SIDE) <= 16 || Math.max(w, h) / Math.min(w, h) > 2);
    }
    assert.deepEqual(modelInputSize(0, 0), { width: 512, height: 512 });
});

test('уменьшение по длинной стороне, меньшее не растягивается', () => {
    assert.deepEqual(fitSize(4000, 3000, 1600), { width: 1600, height: 1200 });
    assert.deepEqual(fitSize(3000, 4000, 1600), { width: 1200, height: 1600 });
    assert.deepEqual(fitSize(800, 600, 1600), { width: 800, height: 600 });
});

test('вход модели — каналы раздельно, цвет в [-1, 1]', () => {
    // Две точки: красная и серая.
    const rgba = new Uint8ClampedArray([255, 0, 0, 255, 128, 128, 128, 255]);
    const input = toModelInput(rgba, 2, 1);
    assert.equal(input.length, 6);
    assert.deepEqual([...input].map((value) => Math.round(value * 1000) / 1000), [1, 0.004, -1, 0.004, -1, 0.004]);
});

test('маска модели — в альфа-канал, за краями [0, 1] — обрезка', () => {
    const rgba = matteToRGBA(new Float32Array([0, 0.5, 1, 1.7, -0.2, Number.NaN]), 6, 1);
    assert.deepEqual([...rgba].filter((_, index) => index % 4 === 3), [0, 128, 255, 255, 0, 0]);
    assert.deepEqual([...rgba].filter((_, index) => index % 4 !== 3).every((value) => value === 255), true);
});

const square = (width, height, box, alpha = 255) => {
    const rgba = new Uint8ClampedArray(width * height * 4);
    for (let y = box.top; y < box.bottom; y += 1) {
        for (let x = box.left; x < box.right; x += 1) rgba[(y * width + x) * 4 + 3] = alpha;
    }
    return rgba;
};

test('рамка непрозрачной части и доля человека в кадре', () => {
    const rgba = square(10, 8, { left: 3, top: 2, right: 7, bottom: 8 });
    assert.deepEqual(alphaBounds(rgba, 10, 8), { left: 3, top: 2, right: 7, bottom: 8 });
    assert.equal(coverage(rgba, 10, 8), (4 * 6) / 80);
    // Полупрозрачная дымка края (до порога) рамку не расширяет.
    const haze = square(10, 8, { left: 0, top: 0, right: 10, bottom: 8 }, 20);
    assert.equal(alphaBounds(haze, 10, 8), null);
    assert.equal(coverage(haze, 10, 8), 0);
    assert.equal(alphaBounds(new Uint8ClampedArray(10 * 8 * 4), 10, 8), null);
});

test('обрезка вокруг человека: воздух сбоку и сверху, снизу — ни точки', () => {
    const bounds = { left: 300, top: 200, right: 700, bottom: 1200 };
    // Фигура 1000 px высотой: 3 % — 30 px воздуха сбоку и сверху.
    assert.deepEqual(cropAround(bounds, 1000, 1200), { left: 270, top: 170, width: 460, height: 1030 });
    // У края кадра воздух упирается в край.
    assert.deepEqual(cropAround({ left: 5, top: 10, right: 995, bottom: 1200 }, 1000, 1200),
        { left: 0, top: 0, width: 1000, height: 1200 });
    assert.equal(cropAround(null, 10, 10), null);
    // Человек целиком в кадре, под ним ещё 400 px пола: низ рамки — ровно по
    // фигуре, иначе на доске под ней была бы щель.
    assert.deepEqual(cropAround({ left: 300, top: 200, right: 700, bottom: 1000 }, 1000, 1400),
        { left: 276, top: 176, width: 448, height: 824 });
});
