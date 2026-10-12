// Плавная смена высоты окна (src/utils/windowResize.js).
//
// Окно портала растёт по содержимому. Когда содержимое подменяют разом —
// догрузилась заявка, открылся второй шаг формы, — рамка перескакивала на новую
// высоту за один кадр (жалоба владельца 12.10.2026: «мини карточка загрузка и
// резко она увеличивается»). Расчёт движения вынесен из ui/ios.jsx, чтобы его
// правила держал тест, а не глаз.
//
// Запуск: node --test tests/window_resize.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';

import {
    WINDOW_RESIZE_EASING, WINDOW_RESIZE_MAX_MS, WINDOW_RESIZE_MIN_DELTA, WINDOW_RESIZE_MIN_MS, windowResizeMotion,
} from '../src/utils/windowResize.js';

test('рамка едет от прежней высоты к новой — в обе стороны', () => {
    const grow = windowResizeMotion(354, 920);
    assert.deepEqual(grow.frames, [{ height: '354px' }, { height: '920px' }]);
    const shrink = windowResizeMotion(920, 203);
    assert.deepEqual(shrink.frames, [{ height: '920px' }, { height: '203px' }]);
    assert.equal(grow.easing, WINDOW_RESIZE_EASING);
});

test('высота не изменилась или изменилась на пару пикселей — движения нет', () => {
    assert.equal(windowResizeMotion(920, 920), null);
    assert.equal(windowResizeMotion(920, 920 + WINDOW_RESIZE_MIN_DELTA - 1), null);
    assert.notEqual(windowResizeMotion(920, 920 + WINDOW_RESIZE_MIN_DELTA), null);
});

test('двигать не из чего: окно ещё не стояло на экране', () => {
    // Первый показ окна: прежней высоты нет — оно просто открывается, а не «вырастает из нуля».
    for (const from of [null, undefined, 0, -10, NaN, 'abc']) {
        assert.equal(windowResizeMotion(from, 800), null, String(from));
    }
    for (const to of [null, undefined, 0, NaN]) {
        assert.equal(windowResizeMotion(800, to), null, String(to));
    }
});

test('короткая дорога быстрее длинной, и обе укладываются в границы', () => {
    const short = windowResizeMotion(500, 540);
    const long = windowResizeMotion(200, 920);
    assert.ok(short.duration < long.duration);
    assert.ok(short.duration >= WINDOW_RESIZE_MIN_MS);
    assert.equal(long.duration, WINDOW_RESIZE_MAX_MS);
    // Движение не длиннее появления окна (window-in — 0,32 с), иначе окно «догоняло» бы само себя.
    assert.ok(WINDOW_RESIZE_MAX_MS <= 320);
});

test('кривая ровная: первый кадр не проходит заметную часть пути', () => {
    // У кривой появления окна (0.16, 1, 0.3, 1) первый кадр из 17 мс проходил треть
    // дороги — на 700 px роста это тот же рывок, только короче.
    const [x1, y1, x2, y2] = WINDOW_RESIZE_EASING.match(/[\d.]+/g).map(Number);
    const bezier = (t, a, b) => 3 * (1 - t) * (1 - t) * t * a + 3 * (1 - t) * t * t * b + t * t * t;
    const progressAt = (x) => {
        let low = 0;
        let high = 1;
        for (let i = 0; i < 40; i += 1) {
            const mid = (low + high) / 2;
            if (bezier(mid, x1, x2) < x) low = mid; else high = mid;
        }
        return bezier((low + high) / 2, y1, y2);
    };
    const firstFrame = progressAt(17 / WINDOW_RESIZE_MAX_MS);
    assert.ok(firstFrame < 0.05, `первый кадр проходит ${Math.round(firstFrame * 100)}% пути`);
    // И ни один кадр, даже на середине дороги, не проходит пятую часть пути.
    let worst = 0;
    for (let ms = 17; ms <= WINDOW_RESIZE_MAX_MS; ms += 17) {
        worst = Math.max(worst, progressAt(ms / WINDOW_RESIZE_MAX_MS) - progressAt((ms - 17) / WINDOW_RESIZE_MAX_MS));
    }
    assert.ok(worst < 0.2, `самый быстрый кадр — ${Math.round(worst * 100)}% пути`);
});
