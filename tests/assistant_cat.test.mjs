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
    displayEmotion,
    initialMood,
    moodReducer,
    nextBlinkDelay,
    nextChangeAt,
} from '../src/components/assistant/cat/catMood.js';
import {
    HEAD_HYSTERESIS,
    LOOK_HOLD_MS,
    MAX_TURN_SPEED,
    atRest,
    blendLayers,
    cellAngles,
    centerCell,
    gridPosition,
    headBlend,
    headCell,
    headName,
    nearestIndex,
    springStep,
    targetAngles,
} from '../src/components/assistant/cat/catLook.js';
import { CAT_HEAD_GRID, CAT_LAYOUT } from '../src/components/assistant/cat/catLayout.js';
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

test('голова к курсору: во все стороны, плавно и без дрожи на границе углов', () => {
    /* Владелец 09.11: «чтобы нормально следил за курсором, чтобы голова могла нормально
       двигаться» — было три кадра (влево, прямо, вправо) скачком и только по горизонтали. */
    const grid = CAT_HEAD_GRID;
    assert.ok(grid.yaw.length >= 5 && grid.pitch.length >= 3, 'и поворот, и наклон, не три положения');
    const center = centerCell();
    assert.equal(grid.yaw[center.col], 0);
    assert.equal(grid.pitch[center.row], 0);
    // Курсор слева сверху — голова влево и вверх; справа снизу — вправо и вниз; рядом — прямо.
    assert.deepEqual(headCell(targetAngles(-1, -1)), { col: 0, row: 0 });
    assert.deepEqual(headCell(targetAngles(1, 1)), { col: grid.yaw.length - 1, row: grid.pitch.length - 1 });
    assert.deepEqual(headCell(targetAngles(0, 0)), center);
    const up = targetAngles(0, -0.6);
    assert.ok(up.pitch < 0 && headCell(up).row < center.row, 'курсор выше — смотрит вверх');
    // У границы двух углов голова не дрожит: уходит с угла, только перейдя середину с запасом.
    const step = grid.yaw[center.col + 1] - grid.yaw[center.col];
    const mid = grid.yaw[center.col] + step / 2;
    assert.equal(nearestIndex(grid.yaw, mid + 0.05 * step, center.col), center.col);
    assert.equal(nearestIndex(grid.yaw, mid + HEAD_HYSTERESIS * step, center.col), center.col + 1);
    assert.equal(nearestIndex(grid.yaw, mid - 0.05 * step, center.col + 1), center.col + 1);
    // Между углами соседние кадры перетекают друг в друга: веса по месту в сетке, сумма — 1.
    assert.equal(gridPosition(grid.yaw, grid.yaw[center.col] + step / 2), center.col + 0.5);
    const half = headBlend({ yaw: grid.yaw[center.col] + step / 2, pitch: 0 });
    assert.equal(half.length, 2);
    assert.ok(Math.abs(half[0].weight - 0.5) < 1e-9 && Math.abs(half[1].weight - 0.5) < 1e-9);
    const corner = headBlend({ yaw: grid.yaw[1] + 3, pitch: grid.pitch[0] + 2 });
    assert.equal(corner.length, 4);
    assert.ok(Math.abs(corner.reduce((sum, part) => sum + part.weight, 0) - 1) < 1e-9);
    // На угле сетки — один чёткий кадр, без двоения.
    assert.deepEqual(headBlend(cellAngles(center)), [{ name: headName(center), weight: 1 }]);
    // Наложение без провала прозрачности: нижний слой целиком, итог — взвешенная сумма.
    const layers = blendLayers(corner);
    assert.equal(layers[0].opacity, 1);
    let mix = layers.map(() => 0);
    layers.forEach((layer, i) => {
        mix = mix.map((v, j) => v * (1 - layer.opacity) + (j === i ? layer.opacity : 0));
    });
    corner.forEach((part, i) => assert.ok(Math.abs(mix[i] - part.weight) < 1e-9, `вклад ${part.name}`));
    // Ход — пружина без отскока: трогается мягко, скорость ограничена, у цели — без перелёта.
    const to = cellAngles({ col: 0, row: 0 });
    let state = { yaw: 0, pitch: 0, vy: 0, vp: 0 };
    const first = springStep(state, to, 16);
    assert.ok(first.yaw < 0 && first.yaw > -1, `первый кадр — ${first.yaw.toFixed(2)}°, без рывка`);
    let farthest = 0;
    let frames = 0;
    while (!atRest(state, to) && frames < 200) {
        state = springStep(state, to, 16);
        assert.ok(Math.abs(state.vy) <= MAX_TURN_SPEED + 1e-9);
        farthest = Math.min(farthest, state.yaw);
        frames += 1;
    }
    assert.ok(atRest(state, to), 'дошла и остановилась');
    assert.ok(farthest >= to.yaw - 0.05, 'без перелёта за цель');
    assert.ok(frames * 16 > 350 && frames * 16 < 1500, `поворот через всю сетку — ${frames * 16} мс`);
    // Курсор замер — голова держит его, а не возвращается через 2,5 с, как раньше.
    assert.ok(LOOK_HOLD_MS >= 4000);
    assert.equal(headName({ col: 1, row: 2 }), 'head-1-2');
});

test('моргает раз в 3–6 с; целиком кадрами — только эмоции', () => {
    assert.equal(nextBlinkDelay(() => 0), 3000);
    assert.equal(nextBlinkDelay(() => 1), 6000);
    assert.deepEqual(FRAMES, EMOTIONS);
});

test('спокойная поза слоями: тело, голова под каждым углом сетки и моргание у каждого угла', () => {
    const asset = (name) => new URL(`../src/components/assistant/cat/assets/cat-${name}.webp`, import.meta.url);
    assert.ok(existsSync(asset('calm-body')), 'нет тела');
    assert.deepEqual(webpSize(readFileSync(asset('calm-body'))), [256, 256]);
    let total = statSync(asset('calm-body')).size;
    for (let row = 0; row < CAT_HEAD_GRID.pitch.length; row += 1) {
        for (let col = 0; col < CAT_HEAD_GRID.yaw.length; col += 1) {
            for (const [suffix, limit] of [['', 12000], ['-blink', 4000]]) {
                const name = `${headName({ col, row })}${suffix}`;
                assert.ok(existsSync(asset(name)), `нет кадра ${name}`);
                const size = statSync(asset(name)).size;
                assert.ok(size < limit, `${name}: ${size} байт — тяжелее ${limit}`);
                // Слои одной рамки с телом: голова садится на шею без подгонки.
                assert.deepEqual(webpSize(readFileSync(asset(name))), [256, 256], `размер ${name}`);
                total += size;
            }
        }
    }
    assert.ok(total < 160000, `слои спокойной позы — ${total} байт`);
    // Сетка сайта — та же, что в рендере Blender.
    const model = read('../scripts/assistant_cat/cat_models.py');
    const tuple = (key) => /\(([^)]*)\)/.exec(new RegExp(`^${key} = .*$`, 'm').exec(model)[0])[1]
        .split(',').map((v) => Number(v.trim()));
    assert.deepEqual(tuple('HEAD_YAW'), CAT_HEAD_GRID.yaw);
    assert.deepEqual(tuple('HEAD_PITCH'), CAT_HEAD_GRID.pitch);
    // Прежних кадров «три положения» больше нет.
    for (const old of ['look-left', 'look-right', 'blink']) assert.ok(!existsSync(asset(old)), `лишний кадр ${old}`);
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
    assert.equal(appearance.normalizeAssistantAppearance('cat', 448), 'cat');
    assert.equal(appearance.normalizeAssistantEffect('cat'), appearance.DEFAULT_ASSISTANT_EFFECT);
    storage.clear();
    const key = appearance.assistantAppearanceKey(448);
    appearance.storeAssistantEffect(key, 'cat', false);
    assert.equal(appearance.readAssistantEffect(key, false), 'cat', 'выбор котика переживает перезагрузку');
});

test('котик пока только у владельца (448) и Руслана (2), у остальных — знак по умолчанию', () => {
    const { assistantAppearancesFor, normalizeAssistantAppearance, readAssistantEffect, storeAssistantEffect, assistantAppearanceKey } = appearance;
    const ids = (userId) => assistantAppearancesFor(userId).map((item) => item.id);
    assert.ok(ids(448).includes('cat'));
    assert.ok(ids(2).includes('cat'));
    assert.ok(ids('2').includes('cat'), 'id из ключа хранилища — строкой');
    for (const userId of [11, 3, null, undefined, '448x']) {
        assert.ok(!ids(userId).includes('cat'), `у ${userId} котика в выборе нет`);
        assert.ok(ids(userId).includes('pumpkin'), 'остальные облики на месте');
        assert.equal(normalizeAssistantAppearance('cat', userId), appearance.DEFAULT_ASSISTANT_EFFECT);
    }
    assert.equal(normalizeAssistantAppearance('pumpkin', 11), 'pumpkin', 'тыква — как была, всем');
    storage.clear();
    const key = assistantAppearanceKey(11);
    storeAssistantEffect(key, 'cat', false);
    assert.equal(readAssistantEffect(key, false), appearance.DEFAULT_ASSISTANT_EFFECT, 'выбранный раньше котик уступает знаку');
    assert.equal(storage.get(key), 'cat', 'сам выбор не стёрт — вернётся, когда котика откроют');
    storeAssistantEffect(key + ':halloween-2026', 'cat', false);
    assert.equal(readAssistantEffect(key, true), appearance.DEFAULT_ASSISTANT_EFFECT, 'и праздничный выбор тоже');

    const picker = read('../src/components/assistant/AssistantAppearancePicker.jsx');
    assert.match(picker, /options\.map\(item =>/, 'выбор рисует переданный список, а не все облики');
    assert.match(picker, /options = PUBLIC_APPEARANCES/, 'без списка — только облики для всех');
    const orb = read('../src/components/assistant/AssistantOrb.jsx');
    assert.match(orb, /assistantAppearancesFor\(userId\)/);
    assert.match(orb, /appearanceOptions=\{appearanceOptions\}/);
    assert.match(read('../src/components/assistant/AssistantPanel.jsx'), /options=\{appearanceOptions\}/);
    assert.match(read('../src/components/assistant/useAssistantAppearance.js'), /normalizeAssistantAppearance\(value, userId\)/);
});

/* Хранилище браузера для mascotsToPreload: ключи по порядку, как у localStorage. */
const fakeStorage = (entries) => ({
    get length() { return entries.length; },
    key: (i) => entries[i]?.[0] ?? null,
    getItem: (key) => entries.find(([k]) => k === key)?.[1] ?? null,
});

test('со стартом портала котик грузится только тем, кто его выбрал', () => {
    const { mascotsToPreload } = appearance;
    assert.deepEqual([...mascotsToPreload(fakeStorage([['otp:assistant:appearance:448', 'cat']]))], ['cat']);
    assert.deepEqual([...mascotsToPreload(fakeStorage([['otp:assistant:appearance:2', 'cat']]))], ['cat']);
    assert.deepEqual([...mascotsToPreload(fakeStorage([['otp:assistant:appearance:5', 'cat']]))], [],
        'кому котик закрыт, тот его не качает, даже если выбрал раньше');
    assert.deepEqual([...mascotsToPreload(fakeStorage([['otp:assistant:appearance:448', 'aurora']]))], [],
        'кто остался на знаке, котика не качает');
    // Праздничный выбор после праздника не в счёт; чужие ключи и мусор — тоже.
    assert.deepEqual([...mascotsToPreload(fakeStorage([
        ['otp:assistant:appearance:448:halloween-2026', 'cat'],
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
    // Голова: цикл кадров гаснет, когда дошла до цели; смешение — прямо в узлы, мимо React.
    assert.match(jsx, /if \(done\) \{\s*rafRef\.current = 0;/);
    assert.match(jsx, /img\.style\.opacity = layer \? layer\.opacity/);
    assert.match(css, /\.cat-mascot__head img \{\s*transition: none;/, 'смешение без запаздывающих CSS-переходов');
});
