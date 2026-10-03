// Лента 4 You идёт по кругу при любом числе фото от двух: если фото мало для
// круга без видимого шва на этой ширине экрана, лента повторяет их копиями.
// 03.10.2026 на мониторе ~1640 px круг требовал 22 карточки, а фото было 18 —
// лента упиралась в край («раньше раздел был цикличным»).
import test from 'node:test';
import assert from 'node:assert/strict';

import { buildCards, copiesFor, cullFor, loopProgress } from '../src/components/four_you/lentaLoop.js';

const WIDTHS = [320, 360, 390, 768, 1024, 1280, 1366, 1440, 1638, 1920, 2560, 3840];

test('карточек всегда хватает на круг без шва', () => {
    for (const width of WIDTHS) {
        for (let count = 2; count <= 60; count += 1) {
            const cards = count * copiesFor(count, width);
            assert.ok(cards >= 2 * cullFor(width), `ширина ${width}, фото ${count}: карточек ${cards}`);
        }
    }
});

test('18 фото на широком мониторе — две копии, 36 карточек', () => {
    assert.equal(cullFor(1638), 11);
    assert.equal(copiesFor(18, 1638), 2);
});

test('фото хватает на круг — копий нет; одно фото по кругу не гоняем', () => {
    for (const width of WIDTHS) {
        assert.equal(copiesFor(2 * cullFor(width), width), 1, `ширина ${width}`);
        assert.equal(copiesFor(100, width), 1, `ширина ${width}`);
        assert.equal(copiesFor(1, width), 1);
        assert.equal(copiesFor(0, width), 1);
    }
});

test('у каждой копии свой ключ, фото и порядок те же', () => {
    const images = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    const cards = buildCards(images, 3);
    assert.equal(cards.length, 9);
    assert.equal(new Set(cards.map((card) => card.key)).size, 9);
    assert.deepEqual(cards.map((card) => card.image.id), ['a', 'b', 'c', 'a', 'b', 'c', 'a', 'b', 'c']);
    assert.deepEqual(cards.map((card) => card.photo), [0, 1, 2, 0, 1, 2, 0, 1, 2]);
    // Первая копия — ключ самого фото: без копий лента такая же, как была.
    assert.deepEqual(buildCards(images, 1).map((card) => card.key), ['a', 'b', 'c']);
    assert.deepEqual(buildCards([], 4), []);
});

test('полоска внизу — по фото, а не по копиям', () => {
    assert.equal(loopProgress(0, 18), 0);
    assert.equal(loopProgress(9, 18), 50);
    assert.equal(loopProgress(18 + 9, 18), 50);
    assert.equal(loopProgress(-9, 18), 50);
    assert.ok(Math.abs(loopProgress(35.5, 18) - (17.5 / 18) * 100) < 1e-9);
});
