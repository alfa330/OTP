/*
 * Плавное раскрытие и сворачивание раздела (src/utils/foldMotion.js).
 *
 * Просьба владельца 13.10.2026 об окне заявки «Оплаты счетов»: «открытие табов — ход
 * заявки, документы и историю — сделать более плавным, аккуратно и удобно, в стиле
 * ios/macos». Раньше содержимое раздела возникало целиком, строки под ним перескакивали,
 * а при сворачивании всё исчезало за кадр. Теперь тело раздела растёт по высоте.
 *
 * Здесь — расчёт движения: кадры, длительность, кривая и подкрутка окна вслед за
 * раскрывающимся разделом. Само движение по кадрам снимает проход стенда.
 *
 * Запуск: node --test tests/fold_motion.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    FOLD_CLOSE_RATIO, FOLD_EASING, FOLD_FADE_IN_END, FOLD_FADE_OUT_END, FOLD_MAX_MS, FOLD_MIN_DELTA, FOLD_MIN_MS,
    FOLD_VIEW_MARGIN, foldFollowStep, foldMotion,
} from '../src/utils/foldMotion.js';
import { WINDOW_RESIZE_EASING } from '../src/utils/windowResize.js';

test('раскрытие: тело растёт от нуля до своего роста, содержимое проявляется по ходу', () => {
    const motion = foldMotion(0, 147);
    assert.equal(motion.opening, true);
    assert.deepEqual(motion.frames, [
        { height: '0px', opacity: 0, offset: 0 },
        { opacity: 1, offset: FOLD_FADE_IN_END },
        { height: '147px', opacity: 1, offset: 1 },
    ]);
    // Текст чёткий раньше, чем раздел встал в полный рост.
    assert.ok(FOLD_FADE_IN_END > 0.4 && FOLD_FADE_IN_END < 1);
});

test('сворачивание: тело сходит на нет, содержимое гаснет по ходу — без пустой полосы под конец', () => {
    const motion = foldMotion(273, 0);
    assert.equal(motion.opening, false);
    assert.deepEqual(motion.frames, [
        { height: '273px', opacity: 1, offset: 0 },
        { opacity: 0, offset: FOLD_FADE_OUT_END },
        { height: '0px', opacity: 0, offset: 1 },
    ]);
    // Погаснуть раньше — значит досворачиваться пустым местом; ровно в конце — мигнуть последним кадром.
    assert.ok(FOLD_FADE_OUT_END >= 0.8 && FOLD_FADE_OUT_END < 1);
});

test('длительность — по росту раздела: короткий быстро, длинный дольше, но не тягуче', () => {
    assert.equal(foldMotion(0, 40).duration, FOLD_MIN_MS + 10);
    assert.equal(foldMotion(0, 147).duration, Math.round(FOLD_MIN_MS + 147 * 0.25));
    assert.equal(foldMotion(0, 640).duration, FOLD_MAX_MS);
    assert.equal(foldMotion(0, 2900).duration, FOLD_MAX_MS);
    for (const height of [8, 60, 147, 273, 442, 900, 2900]) {
        const open = foldMotion(0, height);
        const close = foldMotion(height, 0);
        assert.ok(open.duration >= FOLD_MIN_MS && open.duration <= FOLD_MAX_MS, `раскрытие ${height}: ${open.duration} мс`);
        // Сворачивается чуть быстрее: уходящее не задерживают.
        assert.equal(close.duration, Math.round(Math.min(FOLD_MAX_MS, FOLD_MIN_MS + height * 0.25) * FOLD_CLOSE_RATIO));
        assert.ok(close.duration < open.duration, `сворачивание ${height} не быстрее раскрытия`);
        assert.ok(close.duration >= 200, `сворачивание ${height}: ${close.duration} мс — мелькание`);
    }
    assert.ok(FOLD_MIN_MS >= 200 && FOLD_MAX_MS <= 450);
});

test('кривая — разгон и торможение, та же, что у рамки окна: раздел и рамка едут одним ходом', () => {
    assert.equal(FOLD_EASING, WINDOW_RESIZE_EASING);
    assert.equal(foldMotion(0, 300).easing, 'cubic-bezier(0.4, 0, 0.2, 1)');
    assert.equal(foldMotion(300, 0).easing, FOLD_EASING);
});

test('передумали на полпути: движение продолжается с того роста и той прозрачности, на которых застали', () => {
    // Раздел раскрывался (дошёл до 80 px, содержимое проявилось на 0,55) — нажали ещё раз.
    const back = foldMotion(80, 0, 0.55);
    assert.equal(back.opening, false);
    assert.deepEqual(back.frames[0], { height: '80px', opacity: 0.55, offset: 0 });
    assert.equal(back.frames[2].height, '0px');
    // Раздел сворачивался (осталось 60 px, уже прозрачен) — раскрывают обратно.
    const again = foldMotion(60, 273, 0);
    assert.equal(again.opening, true);
    assert.deepEqual(again.frames[0], { height: '60px', opacity: 0, offset: 0 });
    assert.equal(again.frames[2].height, '273px');
    // Длительность — по оставшемуся пути, а не по полному росту.
    assert.equal(again.duration, Math.round(FOLD_MIN_MS + 213 * 0.25));
    // Прозрачность вне [0, 1] или не число — берётся край по смыслу движения.
    assert.equal(foldMotion(80, 0, 7).frames[0].opacity, 1);
    assert.equal(foldMotion(80, 0, -1).frames[0].opacity, 0);
    assert.equal(foldMotion(80, 0, NaN).frames[0].opacity, 1);
    assert.equal(foldMotion(0, 80, 'x').frames[0].opacity, 0);
    assert.equal(foldMotion(0, 80, undefined).frames[0].opacity, 0);
});

test('двигать нечего — движения нет', () => {
    assert.equal(foldMotion(0, 0), null);
    assert.equal(foldMotion(120, 120), null);
    assert.equal(foldMotion(120, 120 + FOLD_MIN_DELTA - 0.5), null);
    assert.ok(foldMotion(120, 120 + FOLD_MIN_DELTA));
    for (const junk of [[NaN, 100], [0, NaN], [undefined, 100], [0, 'много'], [-5, 100], [100, -1], [Infinity, 0]]) {
        assert.equal(foldMotion(...junk), null, JSON.stringify(junk));
    }
});

test('раскрывающийся раздел держится на виду: окно едет следом, пока видно название раздела', () => {
    // Окно показывает экран с 100 по 700; раздел начался на 500, вырос до 760 — низ ушёл под край.
    assert.equal(foldFollowStep(500, 760, 100, 700), 760 - (700 - FOLD_VIEW_MARGIN));
    // Раздел целиком на виду — ничего не двигаем.
    assert.equal(foldFollowStep(300, 600, 100, 700), 0);
    assert.equal(foldFollowStep(300, 700 - FOLD_VIEW_MARGIN, 100, 700), 0);
    // Раздел длиннее окна: едем, только пока название не упрётся в верхний край, — историю читают с начала.
    assert.equal(foldFollowStep(160, 1400, 100, 700), 160 - (100 + FOLD_VIEW_MARGIN));
    assert.equal(foldFollowStep(100 + FOLD_VIEW_MARGIN, 1400, 100, 700), 0);
    assert.equal(foldFollowStep(40, 1400, 100, 700), 0);
    // Свой отступ от края.
    assert.equal(foldFollowStep(500, 760, 100, 700, 0), 60);
    // Мусор на входе окно не дёргает.
    assert.equal(foldFollowStep(NaN, 760, 100, 700), 0);
    assert.equal(foldFollowStep(500, undefined, 100, 700), 0);
});
