// «Библиотека» (#282): раскрытие книги (bookOpening.js) — полёт обложки без
// искажения и раскрытие через корешок одним движением.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    OPENING_MS, coverBox, flightFrames, hingeFrames, perspectiveFor, reverseFrames,
} from '../src/components/library/bookOpening.js';

const CARD = { left: 350, top: 150, width: 140, height: 210 };      // 2:3, как в каталоге
const SPREAD_PAGE = { left: 720, top: 72, width: 539, height: 770 }; // правая страница разворота
const PHONE_PAGE = { left: 0, top: 50, width: 390, height: 758 };    // экран телефона

const parseFrame = (transform) => {
    const [, tx, ty, sx, sy] = /translate\((-?[\d.]+)px, (-?[\d.]+)px\) scale\(([\d.]+), ([\d.]+)\)/.exec(transform).map(Number);
    return { tx, ty, sx, sy };
};
const parseScale = (transform) => {
    const [, x, y] = /scale\(([\d.]+), ([\d.]+)\)/.exec(transform).map(Number);
    return { x, y };
};
const near = (a, b, eps = 1e-3) => Math.abs(a - b) <= eps * Math.max(1, Math.abs(b));

test('рамка вылетает ровно из карточки и садится ровно на место книги', () => {
    for (const to of [SPREAD_PAGE, PHONE_PAGE]) {
        const { frame } = flightFrames(CARD, to, 2 / 3);
        const start = parseFrame(frame[0].transform);
        assert.ok(near(to.left + start.tx, CARD.left) && near(to.top + start.ty, CARD.top));
        assert.ok(near(to.width * start.sx, CARD.width) && near(to.height * start.sy, CARD.height));
        const end = parseFrame(frame[1].transform);
        assert.deepEqual([end.tx, end.ty, end.sx, end.sy], [0, 0, 1, 1]);
    }
});

test('обложка в полёте не сплющивается и всегда закрывает рамку', () => {
    // Телефон — самый тяжёлый случай: 2:3 → 0.51.
    for (const [to, aspect] of [[SPREAD_PAGE, 2 / 3], [PHONE_PAGE, 2 / 3], [PHONE_PAGE, 0.8], [SPREAD_PAGE, 0.55]]) {
        const { art, anchor } = flightFrames(CARD, to, aspect);
        const box = coverBox(aspect, to);
        for (const [index, key] of art.entries()) {
            const t = key.offset;
            const w = CARD.width + (to.width - CARD.width) * t;
            const h = CARD.height + (to.height - CARD.height) * t;
            const sx = w / to.width;
            const sy = h / to.height;
            const s = parseScale(key.transform);
            // Итоговый масштаб картинки по осям одинаков — пропорции целы.
            assert.ok(near(sx * s.x, sy * s.y), `кадр ${t}: ${sx * s.x} ≠ ${sy * s.y}`);
            // И она не меньше рамки ни по одной оси — без полос по краям.
            assert.ok(box.width * sx * s.x >= w - 0.5 && box.height * sy * s.y >= h - 0.5, `кадр ${t} не закрывает рамку`);
            // Значок и название заглушки — в масштабе ширины рамки.
            const a = parseScale(anchor[index].transform);
            assert.ok(near(sy * a.y, sx), `anchor ${t}`);
        }
    }
});

test('раскрытие разворота: обложка и левая страница — один лист', () => {
    const frames = hingeFrames({ spread: true, pageWidth: 539 });
    const at = (list, offset) => list.filter((key) => key.offset === offset);
    const deg = (key) => Number(/rotateY\((-?[\d.]+)deg\)/.exec(key.transform)[1]);
    // Обложка видна до середины хода, лист — после; в середине оба ребром.
    assert.equal(frames.cover[0].opacity, 1);
    assert.equal(deg(frames.cover[0]), 0);
    assert.deepEqual(at(frames.cover, 0.5).map((key) => [deg(key), key.opacity]), [[-90, 1], [-90, 0]]);
    assert.deepEqual(at(frames.leaf, 0.5).map((key) => [deg(key), key.opacity]), [[90, 0], [90, 1]]);
    assert.equal(frames.leaf[0].opacity, 0);
    const last = frames.leaf[frames.leaf.length - 1];
    assert.deepEqual([deg(last), last.opacity], [0, 1]);
    // Одна точка взгляда — одна перспектива у обоих.
    const p = `perspective(${perspectiveFor(539)}px)`;
    assert.ok(frames.cover.every((key) => key.transform.startsWith(p)));
    assert.ok(frames.leaf.every((key) => key.transform.startsWith(p)));
    // Свет листа гаснет, когда он лёг; тень книги в конце — во весь разворот.
    assert.equal(frames.leafShade[frames.leafShade.length - 1].opacity, 0);
    assert.equal(frames.bookShadow[frames.bookShadow.length - 1].transform, 'scaleX(1)');
});

test('одна страница: обложка растаяла раньше, чем встала ребром', () => {
    const { cover } = hingeFrames({ spread: false, pageWidth: 390 });
    const edge = cover.find((key) => /rotateY\(-90deg\)/.test(key.transform));
    assert.equal(edge.opacity, 0);
    assert.equal(cover[0].opacity, 1);
});

test('кадры — только transform и opacity: без вёрстки на каждом кадре', () => {
    const allowed = new Set(['offset', 'transform', 'opacity']);
    const sets = [
        ...Object.values(hingeFrames({ spread: true, pageWidth: 539 })),
        ...Object.values(hingeFrames({ spread: false, pageWidth: 390 })),
        ...Object.values(flightFrames(CARD, SPREAD_PAGE, 2 / 3)),
    ];
    for (const list of sets) {
        for (const key of list) assert.deepEqual(Object.keys(key).filter((name) => !allowed.has(name)), []);
    }
});

test('закрытие — те же кадры задом наперёд', () => {
    const { cover } = hingeFrames({ spread: true, pageWidth: 539 });
    const back = reverseFrames(cover);
    assert.deepEqual(back.map((key) => key.offset), [0, 0.5, 0.5, 1]);
    // На середине порядок пары тоже обратный: до неё обложки нет, после — есть.
    assert.deepEqual(back.slice(1, 3).map((key) => key.opacity), [0, 1]);
    assert.equal(back[3].transform, cover[0].transform);
    // Двухкадровая рамка полёта без offset тоже разворачивается.
    const { frame } = flightFrames(CARD, SPREAD_PAGE, 2 / 3);
    assert.deepEqual(reverseFrames(frame).map((key) => key.transform), [frame[1].transform, frame[0].transform]);
});

test('открытие и закрытие укладываются в секунду с небольшим', () => {
    assert.ok(OPENING_MS.flyIn + OPENING_MS.hingeSpread <= 1500);
    assert.ok(OPENING_MS.closeSpread * 0.8 + OPENING_MS.flyOut <= 950);
});

/* Закрытая книга лежит по центру стола (02.10.2026): раскрываясь, она съезжает
   на место правой страницы, а весь ридер — следом. Правая страница всё время
   ровно под обложкой, и к середине хода (обложка ребром над корешком) обе уже
   на месте — левая страница сменяет обложку прямо над корешком. */
const shiftOf = (transform) => Number((/translateX\((-?[\d.]+)px\)/.exec(transform) || [])[1] || 0);

test('закрытая книга по центру: обложка и ридер съезжают вместе к середине хода', () => {
    const pageWidth = 539;
    const shift = pageWidth / 2;
    const frames = hingeFrames({ spread: true, pageWidth, shift });
    // Обложка лежит на shift левее правой страницы; ридер сдвинут на −shift.
    for (const offset of [0, 0.5, 1]) {
        const cover = frames.cover.find((key) => key.offset === offset);
        const pan = frames.pan.find((key) => key.offset === offset);
        assert.equal(shiftOf(cover.transform) - shift, shiftOf(pan.transform), `offset ${offset}`);
    }
    assert.equal(shiftOf(frames.pan[0].transform), -shift);
    assert.equal(shiftOf(frames.pan.find((key) => key.offset === 0.5).transform), 0);
    // Тень под поднятой обложкой едет с ней.
    assert.equal(shiftOf(frames.cast.find((key) => key.offset === 0.5).transform), shift);
    // Списки функций у всех кадров обложки одинаковые — иначе нет интерполяции.
    const shape = (transform) => transform.replace(/-?[\d.]+/g, 'N');
    assert.equal(new Set(frames.cover.map((key) => shape(key.transform))).size, 1);
});

test('закрытие с первой страницы — те же кадры сдвига задом наперёд', () => {
    const frames = hingeFrames({ spread: true, pageWidth: 539, shift: 269.5 });
    const back = reverseFrames(frames.pan);
    assert.equal(back[0].transform, frames.pan[frames.pan.length - 1].transform);
    assert.equal(back[back.length - 1].transform, frames.pan[0].transform);
});

test('без сдвига кадры прежние: на телефоне и без центрирования двигать нечего', () => {
    assert.equal(hingeFrames({ spread: true, pageWidth: 539 }).pan, undefined);
    assert.equal(hingeFrames({ spread: false, pageWidth: 390, shift: 100 }).pan, undefined);
    assert.ok(!hingeFrames({ spread: true, pageWidth: 539 }).cover[0].transform.includes('translateX'));
});
