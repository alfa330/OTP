// Облик помощника «Котик» (наброски владельца 02.11.2026: девять эмоций с
// листа и спящий клубочком котёнок).
//
// Котик меняет позу от того, что человек делает с шариком помощника. Стережём,
// что каждая эмоция набросков достижима жестом и у каждой есть кадр из
// Blender, дремота переходит в сон и не начинается заново, голова
// поворачивается к курсору без дрожи на пороге, значки эмоций стоят у головы,
// а облик есть в выборе и грузится своим чанком.
//
// Запуск: node --test tests/assistant_cat.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, statSync } from 'node:fs';

import {
    CLICK_CYCLE,
    DROWSY_MS,
    EMOTIONS,
    FRAMES,
    LOOK_BACK,
    LOOK_TURN,
    displayEmotion,
    frameFor,
    initialMood,
    lookSide,
    moodReducer,
    nextBlinkDelay,
    nextChangeAt,
} from '../src/components/assistant/cat/catMood.js';
import { CAT_LAYOUT } from '../src/components/assistant/cat/catLayout.js';
import { CLOSE_CLICK_MS, RAPID_CLICKS, SLEEP_AFTER_MS } from '../src/components/assistant/mascot/mood.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const run = (events) => events.reduce(moodReducer, initialMood());
const SKETCH = ['happy', 'excited', 'sad', 'angry', 'surprised', 'scared', 'sleepy', 'confused', 'mischievous'];

/* Ширина и высота WebP из заголовка (VP8X / VP8L / VP8) — без декодера. */
const webpSize = (buf) => {
    assert.equal(buf.toString('ascii', 0, 4), 'RIFF');
    assert.equal(buf.toString('ascii', 8, 12), 'WEBP');
    const chunk = buf.toString('ascii', 12, 16);
    if (chunk === 'VP8X') return [1 + buf.readUIntLE(24, 3), 1 + buf.readUIntLE(27, 3)];
    if (chunk === 'VP8L') {
        const bits = buf.readUInt32LE(21);
        return [1 + (bits & 0x3fff), 1 + ((bits >> 14) & 0x3fff)];
    }
    return [buf.readUInt16LE(26) & 0x3fff, buf.readUInt16LE(28) & 0x3fff];
};

test('девять эмоций листа и клубочек — у каждой кадр из Blender, лёгкий, одного размера', () => {
    for (const name of [...SKETCH, 'asleep']) assert.ok(EMOTIONS.includes(name), `нет эмоции ${name}`);
    for (const frame of [...FRAMES, 'logo']) {
        const file = new URL(`../src/components/assistant/cat/assets/cat-${frame}.webp`, import.meta.url);
        assert.ok(existsSync(file), `нет кадра ${frame}`);
        assert.ok(statSync(file).size < 24000, `кадр ${frame} тяжелее 24 КБ`);
        // Общая рамка: все позы одного размера, иначе котик прыгает при смене эмоции.
        assert.deepEqual(webpSize(readFileSync(file)), frame === 'logo' ? [96, 96] : [256, 256], `размер кадра ${frame}`);
    }
    for (const emotion of CLICK_CYCLE) assert.ok(EMOTIONS.includes(emotion));
    // Сборщик режет те же кадры, что показывает сайт.
    const builder = read('../scripts/assistant_cat/build_assets.py');
    const listed = /FRAMES = \[([^\]]+)\]/.exec(builder)[1].match(/'([a-z-]+)'/g).map((q) => q.slice(1, -1));
    assert.deepEqual([...listed].sort(), [...FRAMES].sort());
});

test('каждая эмоция достижима жестом шарика', () => {
    const seen = new Set();
    let state = initialMood();
    const show = (now) => seen.add(displayEmotion(state, now));
    const step = (event) => { state = moodReducer(state, event); show(event.now + 1); };
    show(0);
    step({ type: 'enter', now: 10 });                               // курсор над котиком
    step({ type: 'leave', now: 20 });
    step({ type: 'press', now: 30 });                               // нажатие
    CLICK_CYCLE.forEach((_, i) => step({ type: 'click', now: 10000 + i * 3000 }));
    for (let i = 0; i < RAPID_CLICKS; i += 1) step({ type: 'click', now: 100000 + i * 100 });
    step({ type: 'dragStart', now: 200000 });                       // тащат
    step({ type: 'dragEnd', now: 210000 });
    step({ type: 'close', now: 300000 });                           // закрыли помощника
    step({ type: 'sleep', now: 400000 });                           // простой
    show(400000 + DROWSY_MS + 1);
    for (const emotion of EMOTIONS) assert.ok(seen.has(emotion), `эмоция ${emotion} недостижима`);
});

test('наведение — Happy, нажатие — Surprised, шквал щелчков — Angry', () => {
    const hover = run([{ type: 'enter', now: 0 }]);
    assert.equal(displayEmotion(hover, 5), 'happy');
    assert.equal(displayEmotion(moodReducer(hover, { type: 'leave', now: 6 }), 7), 'normal');
    const pressed = run([{ type: 'press', now: 1000 }]);
    assert.equal(displayEmotion(pressed, 1100), 'surprised');
    assert.equal(displayEmotion(pressed, 1700), 'normal');
    let state = initialMood();
    for (let i = 0; i < RAPID_CLICKS; i += 1) state = moodReducer(state, { type: 'click', now: 5000 + i * 200 });
    assert.equal(displayEmotion(state, 5000 + RAPID_CLICKS * 200), 'angry');
});

test('тащат шарик — Scared всё перетаскивание, отпустили — Happy; закрыли — Sad ненадолго', () => {
    const dragging = run([{ type: 'dragStart', now: 0 }]);
    assert.equal(displayEmotion(dragging, 60000), 'scared');
    const dropped = moodReducer(dragging, { type: 'dragEnd', now: 61000 });
    assert.equal(displayEmotion(dropped, 61100), 'happy');
    assert.equal(displayEmotion(dropped, 63000), 'normal');
    const closed = run([{ type: 'close', now: 0 }]);
    assert.equal(displayEmotion(closed, 100), 'sad');
    assert.equal(displayEmotion(closed, 2000), 'normal');
});

test('закрыли щелчком по шарику — грусть, а не следующая эмоция череды', () => {
    /* Порядок событий настоящего шарика (стенд 02.11): pointerdown → «нажатие»,
       pointerup переключает помощника → aria-expanded=false → «закрыли», и только
       потом приходит click. Он не должен перебить грусть и сдвинуть череду. */
    const closedByOrb = run([
        { type: 'press', now: 0 },
        { type: 'close', now: 80 },
        { type: 'click', now: 85 },
    ]);
    assert.equal(displayEmotion(closedByOrb, 200), 'sad');
    assert.equal(closedByOrb.cycle, 0, 'череда не сдвинулась');
    // Следующий щелчок (открыть снова) — уже обычный, первая эмоция череды.
    const reopened = moodReducer(closedByOrb, { type: 'click', now: 80 + CLOSE_CLICK_MS + 500 });
    assert.equal(displayEmotion(reopened, 80 + CLOSE_CLICK_MS + 510), CLICK_CYCLE[0]);
});

test('закрыли с клавиатуры (click помечен closing) — грусть, череда стоит', () => {
    const state = run([{ type: 'click', now: 100, closing: true }, { type: 'close', now: 101 }]);
    assert.equal(displayEmotion(state, 200), 'sad');
    assert.equal(state.cycle, 0);
    assert.equal(state.clicks.length, 1, 'в счёт шквала щелчок идёт');
});

test('шквал щелчков злит, хотя каждый второй щелчок закрывает помощника', () => {
    /* Стенд 02.11: открыть-закрыть-открыть-закрыть подряд. Закрывающие щелчки
       приходят сразу после «закрыли» — без них в счёте злость не наступала. */
    let state = initialMood();
    for (let i = 0; i < RAPID_CLICKS; i += 1) {
        const now = 1000 + i * 150;
        state = moodReducer(state, { type: 'press', now });
        if (i % 2 === 1) state = moodReducer(state, { type: 'close', now: now + 60 });
        state = moodReducer(state, { type: 'click', now: now + 65 });
    }
    assert.equal(displayEmotion(state, 1000 + RAPID_CLICKS * 150), 'angry');
});

test('простой: сначала зевает, через DROWSY_MS — клубочком; шевельнули мышь — Surprised', () => {
    assert.ok(SLEEP_AFTER_MS >= 30000, 'засыпает не раньше чем через полминуты');
    const drowsy = run([{ type: 'sleep', now: 0 }]);
    assert.equal(displayEmotion(drowsy, 10), 'sleepy');
    assert.equal(nextChangeAt(drowsy, 10), DROWSY_MS, 'перерисовка ровно к засыпанию');
    assert.equal(displayEmotion(drowsy, DROWSY_MS + 1), 'asleep');
    assert.equal(nextChangeAt(drowsy, DROWSY_MS + 1), null);
    // Таймер простоя шлёт «сон» раз в секунду — дремота не начинается заново.
    assert.equal(moodReducer(drowsy, { type: 'sleep', now: 3000 }), drowsy);
    const awake = moodReducer(drowsy, { type: 'wake', now: 9000 });
    assert.equal(displayEmotion(awake, 9100), 'surprised');
    assert.equal(displayEmotion(awake, 10000), 'normal');
    const idle = initialMood();
    assert.equal(moodReducer(idle, { type: 'wake', now: 5 }), idle, 'бодрого котика мышь не перерисовывает');
});

test('голова к курсору — с зазором между порогами, без дрожи на границе', () => {
    assert.ok(LOOK_BACK < LOOK_TURN);
    assert.equal(lookSide('center', 0.2), 'center');
    assert.equal(lookSide('center', LOOK_TURN), 'right');
    assert.equal(lookSide('center', -LOOK_TURN), 'left');
    const between = (LOOK_TURN + LOOK_BACK) / 2;
    assert.equal(lookSide('right', between), 'right', 'в зазоре голова остаётся, где была');
    assert.equal(lookSide('center', between), 'center');
    assert.equal(lookSide('right', LOOK_BACK - 0.01), 'center');
    assert.equal(lookSide('left', -between), 'left');
    assert.equal(lookSide('right', -LOOK_TURN), 'left');
});

test('моргает и вертит головой только в покое; моргает раз в 3–6 с', () => {
    assert.equal(frameFor('normal', 'center', false), 'normal');
    assert.equal(frameFor('normal', 'center', true), 'blink');
    assert.equal(frameFor('normal', 'left', true), 'look-left', 'повёрнутая голова не моргает кадром «прямо»');
    assert.equal(frameFor('normal', 'right', false), 'look-right');
    assert.equal(frameFor('happy', 'left', true), 'happy');
    assert.equal(frameFor('asleep', 'center', true), 'asleep');
    assert.equal(nextBlinkDelay(() => 0), 3000);
    assert.equal(nextBlinkDelay(() => 1), 6000);
    for (const frame of FRAMES) assert.ok(frameFor(frame, 'center', false) === frame || frame.startsWith('look-') || frame === 'blink');
});

test('значки эмоций стоят у головы, в кадре', () => {
    for (const frame of FRAMES) {
        const p = CAT_LAYOUT[frame];
        assert.ok(p, `нет опорных точек ${frame}`);
        for (const [key, value] of Object.entries(p)) {
            for (const v of value) assert.ok(v >= 0 && v <= 100, `${frame}.${key} вне кадра: ${value}`);
        }
        const [left, top, right, bottom] = p.box;
        assert.ok(left < p.head[0] && p.head[0] < right && top < p.head[1] && p.head[1] < bottom, `${frame}: голова вне силуэта`);
        assert.ok(p.left[0] < p.head[0] && p.head[0] < p.right[0], `${frame}: бока головы перепутаны`);
        assert.ok(p.top[1] < p.head[1] && p.head[1] < p.mouth[1], `${frame}: макушка и рот перепутаны`);
        assert.ok(bottom > 80, `${frame}: лапы не на общей линии`);
        // Под лапами запас на запечённую тень: у клубочка её срезал край кадра (разбор 02.11).
        assert.ok(bottom <= 92.5, `${frame}: тени под лапами негде лечь (низ силуэта ${bottom}%)`);
    }
    // Лежачие позы ниже сидячих: голова клубочка и испуганного — в нижней половине.
    assert.ok(CAT_LAYOUT.asleep.head[1] > 60 && CAT_LAYOUT.scared.head[1] > 55);
});

/* Хранилище браузера для модуля облика: window.localStorage в node — заглушка. */
const storage = new Map();
globalThis.window = globalThis.window || {};
globalThis.window.localStorage = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
};

const appearance = await import('../src/components/assistant/assistantAppearance.js');

test('котик — облик в выборе, но не эффект знака iCORE', () => {
    const ids = appearance.ASSISTANT_APPEARANCES.map((item) => item.id);
    assert.ok(ids.includes('cat'));
    assert.equal(new Set(ids).size, ids.length, 'облики не повторяются');
    assert.ok(!appearance.ICORE_ASSISTANT_EFFECTS.some((item) => item.id === 'cat'), 'знак с эффектом «cat» нарисовался бы пустым');
    assert.equal(appearance.normalizeAssistantAppearance('cat'), 'cat');
    assert.equal(appearance.normalizeAssistantEffect('cat'), appearance.DEFAULT_ASSISTANT_EFFECT);
    storage.clear();
    const key = appearance.assistantAppearanceKey(11);
    appearance.storeAssistantEffect(key, 'cat', false);
    assert.equal(appearance.readAssistantEffect(key, false), 'cat', 'выбор котика переживает перезагрузку');
});

/* Хранилище браузера для mascotsToPreload: ключи по порядку, как у localStorage. */
const fakeStorage = (entries) => ({
    get length() { return entries.length; },
    key: (i) => entries[i]?.[0] ?? null,
    getItem: (key) => entries.find(([k]) => k === key)?.[1] ?? null,
});

test('со стартом портала котик грузится только тем, кто его выбрал', () => {
    const { mascotsToPreload } = appearance;
    assert.deepEqual([...mascotsToPreload(fakeStorage([['otp:assistant:appearance:5', 'cat']]))], ['cat']);
    assert.deepEqual([...mascotsToPreload(fakeStorage([['otp:assistant:appearance:5', 'aurora']]))], [],
        'кто остался на знаке, котика не качает');
    // Праздничный выбор после праздника не в счёт; чужие ключи и мусор — тоже.
    assert.deepEqual([...mascotsToPreload(fakeStorage([
        ['otp:assistant:appearance:5:halloween-2026', 'cat'],
        ['wiki:space', 'cat'],
        ['otp:assistant:appearance:6', 'constructor'],
    ]))], []);
    assert.deepEqual([...mascotsToPreload(fakeStorage([['otp:assistant:appearance:7', 'pumpkin']]), true)], ['pumpkin']);
    assert.deepEqual([...mascotsToPreload(null, true)], ['pumpkin'], 'закрытое хранилище — только праздничная тыква');
    assert.deepEqual([...mascotsToPreload({ get length() { throw new Error('blocked'); } })], []);
});

test('шарик грузит котика своим чанком, при сбое остаётся знак', () => {
    const orb = read('../src/components/assistant/Orb.jsx');
    assert.match(orb, /\[CAT_APPEARANCE,\s*\(\)\s*=>\s*import\('\.\/cat\/CatMascot\.jsx'\)\]/);
    // Map, а не объект: облик «constructor» из хранилища не должен звать Object().
    assert.match(orb, /MASCOT_CHUNKS\s*=\s*new Map\(/);
    assert.match(orb, /mascotsToPreload\(storage,\s*isHalloweenTime\(\)\)\.forEach\(loadMascot\)/);
    assert.match(orb, /:\s*<IcoreAssistantMark /, 'пока талисман не доехал — прежний знак');
    assert.doesNotMatch(orb, /<Suspense|lazy\(/);
    const picker = read('../src/components/assistant/AssistantAppearancePicker.jsx');
    assert.match(picker, /\[CAT_APPEARANCE,\s*catLogo\]/, 'в выборе — неподвижный портрет, живой котик только в шарике');
});

test('котик дёшев для слабого компьютера', () => {
    const css = read('../src/components/assistant/cat/cat-mascot.css');
    const jsx = read('../src/components/assistant/cat/CatMascot.jsx');
    assert.doesNotMatch(css, /filter:/, 'тень запечена в картинки');
    assert.match(css, /\.cat-mascot\.is-lite \.cat-mascot__breathe,/);
    assert.match(css, /\.cat-mascot\.is-paused \*/);
    // В разметке только показанные кадры, недоехавший кадр не гасит прежний.
    assert.match(jsx, /FRAMES\.filter\(\(name\)\s*=>\s*shownRef\.current\.has\(name\)\)/);
    assert.match(jsx, /visible\s*=\s*loaded\.has\(frame\)\s*\?\s*frame\s*:\s*lastLoadedRef\.current/);
    assert.match(jsx, /requestIdleCallback/);
});
