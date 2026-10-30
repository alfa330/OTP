// Хэллоуин-2026: настроение тыквы-помощника (наброски владельца «Тыква (эмоции)»).
//
// Тыква меняет лицо от того, что человек делает с шариком помощника: наведение,
// щелчки, перетаскивание, закрытие, простой. Стережём, что каждая из 12 эмоций
// достижима, временные эмоции гаснут вовремя, а взгляд следует за курсором.
//
// Запуск: node --test tests/halloween_mascot.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';

import {
    CLICK_CYCLE,
    EMOTIONS,
    RAPID_CLICKS,
    RAPID_WINDOW_MS,
    displayEmotion,
    initialMood,
    lookVector,
    moodReducer,
} from '../src/components/seasonal/halloween/mascotMood.js';

const run = (events) => events.reduce(moodReducer, initialMood());

test('у каждой из 12 эмоций есть лицо из Blender', () => {
    assert.equal(EMOTIONS.length, 12);
    for (const emotion of EMOTIONS) {
        const file = new URL(`../src/components/seasonal/halloween/assets/mascot-face-${emotion}.webp`, import.meta.url);
        assert.ok(existsSync(file), `нет лица ${emotion}`);
    }
    for (const emotion of CLICK_CYCLE) assert.ok(EMOTIONS.includes(emotion));
});

test('спокойно — обычная, курсор над тыквой — радость, ушёл — снова обычная', () => {
    assert.equal(displayEmotion(initialMood(), 0), 'normal');
    const hover = run([{ type: 'enter', now: 0 }]);
    assert.equal(displayEmotion(hover, 10), 'joy');
    assert.equal(displayEmotion(moodReducer(hover, { type: 'leave', now: 20 }), 30), 'normal');
});

test('нажатие — удивление на мгновение', () => {
    const pressed = run([{ type: 'press', now: 1000 }]);
    assert.equal(displayEmotion(pressed, 1100), 'surprise');
    assert.equal(displayEmotion(pressed, 1700), 'normal');
});

test('щелчки идут по кругу эмоций и гаснут через пару секунд', () => {
    let state = initialMood();
    const seen = [];
    for (let i = 0; i < CLICK_CYCLE.length + 1; i += 1) {
        const now = 10000 + i * 3000;            // реже, чем «шквал»
        state = moodReducer(state, { type: 'click', now });
        seen.push(displayEmotion(state, now + 10));
        assert.equal(displayEmotion(state, now + 2500), 'normal', 'эмоция щелчка не погасла');
    }
    assert.deepEqual(seen, [...CLICK_CYCLE, CLICK_CYCLE[0]]);
});

test('шквал щелчков — злость, а щелчки вразнобой злости не дают', () => {
    let state = initialMood();
    for (let i = 0; i < RAPID_CLICKS; i += 1) state = moodReducer(state, { type: 'click', now: 5000 + i * 200 });
    assert.equal(displayEmotion(state, 5000 + RAPID_CLICKS * 200), 'anger');
    let calm = initialMood();
    for (let i = 0; i < RAPID_CLICKS; i += 1) calm = moodReducer(calm, { type: 'click', now: i * (RAPID_WINDOW_MS / 2 + 1) });
    assert.notEqual(displayEmotion(calm, RAPID_CLICKS * (RAPID_WINDOW_MS / 2 + 1)), 'anger');
});

test('тащат шарик — страх до конца перетаскивания, отпустили — радость', () => {
    const dragging = run([{ type: 'dragStart', now: 0 }]);
    assert.equal(displayEmotion(dragging, 60000), 'fear', 'страх держится всё перетаскивание');
    const dropped = moodReducer(dragging, { type: 'dragEnd', now: 61000 });
    assert.equal(displayEmotion(dropped, 61100), 'joy');
    assert.equal(displayEmotion(dropped, 63000), 'normal');
});

test('закрыли помощника — грусть ненадолго', () => {
    const closed = run([{ type: 'close', now: 0 }]);
    assert.equal(displayEmotion(closed, 100), 'sad');
    assert.equal(displayEmotion(closed, 2000), 'normal');
});

test('простой — сон, шевельнули мышь — удивление и снова обычная', () => {
    const asleep = run([{ type: 'sleep', now: 0 }]);
    assert.equal(displayEmotion(asleep, 10), 'sleep');
    const awake = moodReducer(asleep, { type: 'wake', now: 100 });
    assert.equal(displayEmotion(awake, 150), 'surprise');
    assert.equal(displayEmotion(awake, 1200), 'normal');
    // Неспящую тыкву движение мыши не трогает — и React не перерисовывает.
    const idle = initialMood();
    assert.equal(moodReducer(idle, { type: 'wake', now: 5 }), idle);
});

test('все 12 эмоций достижимы', () => {
    const reached = new Set(['normal', 'joy', 'surprise', 'anger', 'fear', 'sad', 'sleep', ...CLICK_CYCLE]);
    for (const emotion of EMOTIONS) assert.ok(reached.has(emotion), `эмоция ${emotion} недостижима`);
});

test('взгляд следует за курсором и упирается в край', () => {
    assert.deepEqual(lookVector(0, 0), { x: 0, y: 0 });
    const right = lookVector(300, 0);
    const left = lookVector(-300, 0);
    assert.ok(right.x > 0.5 && left.x < -0.5);
    assert.equal(right.x, -left.x);
    assert.ok(lookVector(0, 400).y > 0 && lookVector(0, -400).y < 0);
    const far = lookVector(100000, -100000);
    assert.ok(far.x <= 1 && far.y >= -1);
    assert.ok(lookVector(40, 0).x < lookVector(400, 0).x, 'дальше — сильнее');
});
