// «Дресс-код» (#418): автопроверка кадра — разбор ответа нейросети, подсказки, автоснимок.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';

import {
    ADVICE_TEXT, DEFAULT_RULES, FACE_INPUT, FRAME_FIXED, RULE_LIMITS, cleanRules, createAutoShutter, createHintSmoother,
    decodeFaces, facesInFrame, fitInput, frameAdvice, largestFace, metricChecks, regionLuma, silhouetteGuide, toInputTensor,
} from '../src/components/dress_code/faceMath.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

/* Пустой ответ сети на вход side×side: три сетки (шаг 8, 16, 32). */
const emptyOutputs = (side = FACE_INPUT) => {
    const outputs = {};
    for (const stride of [8, 16, 32]) {
        const cells = (side / stride) ** 2;
        outputs[`cls_${stride}`] = new Float32Array(cells);
        outputs[`obj_${stride}`] = new Float32Array(cells);
        outputs[`bbox_${stride}`] = new Float32Array(cells * 4);
    }
    return outputs;
};

const putFace = (outputs, stride, row, col, { dx = 0.5, dy = 0.5, w, h, cls = 0.9, obj = 0.9 }, side = FACE_INPUT) => {
    const idx = row * (side / stride) + col;
    outputs[`cls_${stride}`][idx] = cls;
    outputs[`obj_${stride}`][idx] = obj;
    outputs[`bbox_${stride}`].set([dx, dy, Math.log(w / stride), Math.log(h / stride)], idx * 4);
};

test('вход сети: каналы B, G, R по плоскостям, значения 0..255', () => {
    const rgba = new Uint8ClampedArray([10, 20, 30, 255, 40, 50, 60, 255, 70, 80, 90, 255, 100, 110, 120, 255]);
    const tensor = toInputTensor(rgba, 2);
    assert.deepEqual(Array.from(tensor), [30, 60, 90, 120, 20, 50, 80, 110, 10, 40, 70, 100]);
});

test('разбор ответа сети: прямоугольник лица из клетки сетки', () => {
    const outputs = emptyOutputs();
    putFace(outputs, 16, 5, 7, { w: 64, h: 80 });
    const faces = decodeFaces(outputs);
    assert.equal(faces.length, 1);
    const [face] = faces;
    assert.ok(Math.abs(face.x - 88) < 1e-3 && Math.abs(face.y - 48) < 1e-3);
    assert.ok(Math.abs(face.w - 64) < 1e-3 && Math.abs(face.h - 80) < 1e-3);
    assert.ok(Math.abs(face.score - 0.9) < 1e-6);
});

test('одно лицо из двух сеток не двоится, слабые клетки отбрасываются', () => {
    const outputs = emptyOutputs();
    putFace(outputs, 16, 5, 7, { w: 64, h: 80 });
    putFace(outputs, 8, 10, 14, { w: 60, h: 78, cls: 0.8, obj: 0.8 });      // то же лицо, шаг 8
    putFace(outputs, 32, 8, 1, { w: 40, h: 50, cls: 0.5, obj: 0.5 });       // ниже порога
    putFace(outputs, 32, 8, 8, { w: 40, h: 50, cls: 0.95, obj: 0.95 });     // второе лицо, далеко
    const faces = decodeFaces(outputs);
    assert.equal(faces.length, 2);
    assert.ok(faces[0].score > faces[1].score);
    assert.deepEqual(decodeFaces({}), []);
    assert.deepEqual(decodeFaces(null), []);
});

test('лицо из входа сети переводится в доли кадра, который попадёт на фото', () => {
    // Кадр телефона 960×1280 вписан во вход сети как 240×320 (слева сверху).
    const fit = fitInput(960, 1280);
    assert.deepEqual([fit.width, fit.height], [240, 320]);
    const [face] = facesInFrame([{ x: 96, y: 40, w: 48, h: 64, score: 0.9 }], fit);
    assert.ok(Math.abs(face.x - 0.4) < 1e-9 && Math.abs(face.y - 0.125) < 1e-9);
    assert.ok(Math.abs(face.w - 0.2) < 1e-9 && Math.abs(face.h - 0.2) < 1e-9);
    assert.equal(face.score, 0.9);
    assert.deepEqual(facesInFrame([{ x: 1, y: 1, w: 1, h: 1 }], { width: 0, height: 0 }), []);
    assert.deepEqual(facesInFrame(null, fit), []);
});

const face = (over = {}) => ({ x: 0.4, y: 0.12, w: 0.2, h: 0.22, score: 0.9, ...over });

test('подсказки: что не так с кадром', () => {
    const cases = [
        ['ok', [face()]],
        ['none', []],
        ['too_close', [face({ x: 0.25, y: 0.1, w: 0.5, h: 0.45 })]],
        ['head_cropped', [face({ y: 0.03 })]],
        ['off_center', [face({ x: 0.62 })]],
        ['off_center', [face({ x: -0.05 })]],
        ['too_far', [face({ x: 0.47, w: 0.06, h: 0.07 })]],
        ['too_low', [face({ y: 0.42 })]],
    ];
    for (const [code, faces] of cases) {
        const advice = frameAdvice(faces);
        assert.equal(advice.code, code, code);
        assert.equal(advice.ok, code === 'ok');
    }
    assert.equal(frameAdvice([face()], { dark: true }).code, 'too_dark');
    // Вплотную к камере голова не влезает всегда — говорим «дальше», а не «голова не влезает».
    assert.equal(frameAdvice([face({ x: 0.2, y: 0.0, w: 0.6, h: 0.6 })]).code, 'too_close');
});

test('второй человек — только тот, кто попадёт на фото и сравним по размеру', () => {
    const second = face({ x: 0.7, y: 0.3, w: 0.15, h: 0.16 });
    assert.equal(frameAdvice([face(), second]).code, 'several');
    // Мелкое лицо на заднем плане и лицо за краем рамки снимку не мешают.
    assert.equal(frameAdvice([face(), face({ x: 0.8, y: 0.5, w: 0.05, h: 0.06 })]).code, 'ok');
    assert.equal(frameAdvice([face(), face({ x: 1.1, y: 0.3, w: 0.2, h: 0.2 })]).code, 'ok');
});

/* Лицо высотой h с макушкой на доле top от верха кадра, по центру. */
const faceAt = (h, top, over = {}) => ({ x: 0.5 - h * 0.4, y: top + h * FRAME_FIXED.headroom, w: h * 0.8, h, score: 0.9, ...over });

test('автоснимок — только когда пояс помещается в кадр', () => {
    // Владелец 13.11.2026: «авто фотографирование сработало даже без условия по пояс».
    // Селфи «по грудь» (лицо — треть кадра) прежний порог 0,34 пропускал. Теперь нет.
    assert.equal(frameAdvice([faceAt(0.33, 0.03)]).code, 'too_close');
    assert.equal(frameAdvice([faceAt(0.31, 0.02)]).code, 'too_close');
    // Лицо мельче — пояс входит, если голова у верха кадра…
    const fits = frameAdvice([faceAt(0.28, 0.03)]);
    assert.equal(fits.code, 'ok');
    assert.ok(fits.metrics.waist <= 1);
    // …и не входит, если голова ниже: телефон дальше держать не надо, надо поднять голову к контуру.
    const low = frameAdvice([faceAt(0.28, 0.12)]);
    assert.equal(low.code, 'too_low');
    assert.ok(low.metrics.waist > 1);
    // Граница «дальше»: самое крупное лицо, при котором пояс ещё в кадре.
    const limit = largestFace();
    assert.ok(Math.abs(limit - 0.99 / 3.35) < 1e-9);
    assert.equal(frameAdvice([faceAt(limit - 0.004, FRAME_FIXED.minTop + 0.001)]).code, 'ok');
    assert.equal(frameAdvice([faceAt(limit + 0.004, FRAME_FIXED.minTop + 0.001)]).code, 'too_close');
});

test('правила приходят с сервера: пояс, размер лица, макушка, центр', () => {
    const selfie = [faceAt(0.3, 0.03)];
    assert.equal(frameAdvice(selfie).code, 'too_close');
    assert.equal(frameAdvice(selfie, { rules: { ...DEFAULT_RULES, waist: 2.6 } }).code, 'ok');
    assert.equal(frameAdvice([faceAt(0.24, 0.05)], { rules: { ...DEFAULT_RULES, waist: 4 } }).code, 'too_close');
    const small = [faceAt(0.08, 0.05)];
    assert.equal(frameAdvice(small).code, 'too_far');
    assert.equal(frameAdvice(small, { rules: { ...DEFAULT_RULES, min_face: 0.06 } }).code, 'ok');
    const lowHead = [faceAt(0.15, 0.3)];
    assert.equal(frameAdvice(lowHead).code, 'too_low');
    assert.equal(frameAdvice(lowHead, { rules: { ...DEFAULT_RULES, max_top: 0.4 } }).code, 'ok');
    const aside = [faceAt(0.2, 0.05, { x: 0.52 })];                     // центр лица на 0,60
    assert.equal(frameAdvice(aside).code, 'ok');
    assert.equal(frameAdvice(aside, { rules: { ...DEFAULT_RULES, max_off_center: 0.05 } }).code, 'off_center');
});

test('стенд видит измеренное: лицо, макушку, пояс, центр — и что из них в правилах', () => {
    const advice = frameAdvice([faceAt(0.2, 0.05, { x: 0.45 })]);        // центр лица на 0,53
    assert.ok(Math.abs(advice.metrics.face - 0.2) < 1e-9);
    assert.ok(Math.abs(advice.metrics.top - 0.05) < 1e-9);
    assert.ok(Math.abs(advice.metrics.waist - (0.05 + 0.2 * (FRAME_FIXED.headroom + DEFAULT_RULES.waist))) < 1e-9);
    assert.ok(Math.abs(advice.metrics.center - 0.03) < 1e-9);
    assert.deepEqual(advice.box, { x: 0.45, y: 0.05 + 0.2 * FRAME_FIXED.headroom, w: 0.2 * 0.8, h: 0.2 });
    assert.deepEqual(metricChecks(advice.metrics), { face: true, top: true, waist: true, center: true });
    // Плохой кадр тоже отдаёт числа — по ним и настраивают.
    const close = frameAdvice([faceAt(0.4, 0.02)]);
    assert.equal(close.ok, false);
    assert.deepEqual(metricChecks(close.metrics), { face: false, top: true, waist: false, center: true });
    assert.deepEqual(metricChecks(frameAdvice([faceAt(0.05, 0.4, { x: 0.8 })]).metrics),
        { face: false, top: false, waist: true, center: false });
    assert.deepEqual(metricChecks(null), { face: false, top: false, waist: false, center: false });
    assert.equal(frameAdvice([]).metrics, undefined);
});

test('правила камеры: зеркало сервера, мусор отбрасывается, числа — в пределах', () => {
    const backend = read('../dress_code/__init__.py');
    const defaults = JSON.parse(backend.match(/^CAMERA_DEFAULTS = (\{.*?\})\s*$/m)[1].replaceAll("'", '"'));
    assert.deepEqual(defaults, { ...DEFAULT_RULES });
    const limits = {};
    const block = backend.slice(backend.indexOf('CAMERA_LIMITS = {'));
    for (const [, key, low, high] of block.matchAll(/'(\w+)': \(([\d.]+), ([\d.]+)\)/g)) limits[key] = [Number(low), Number(high)];
    assert.deepEqual(limits, Object.fromEntries(Object.entries(RULE_LIMITS).map(([key, range]) => [key, [...range]])));
    // Значения по умолчанию сами лежат в пределах.
    for (const [key, [low, high]] of Object.entries(RULE_LIMITS)) assert.ok(DEFAULT_RULES[key] >= low && DEFAULT_RULES[key] <= high, key);
    assert.deepEqual(cleanRules(null), { ...DEFAULT_RULES });
    assert.deepEqual(cleanRules({ waist: 9, min_face: 'x', max_top: NaN, max_off_center: 0.2004, hold_ms: 1234.6, extra: 1 }),
        { ...DEFAULT_RULES, waist: 5, max_off_center: 0.2, hold_ms: 1235 });
    assert.deepEqual(cleanRules({ waist: 0, hold_ms: 1 }), { ...DEFAULT_RULES, waist: 1.5, hold_ms: 400 });
    // При любых разрешённых правилах есть лицо, которое проходит: «не мельче» всегда меньше «не крупнее».
    assert.ok(RULE_LIMITS.min_face[1] < largestFace({ ...DEFAULT_RULES, waist: RULE_LIMITS.waist[1] }));
});

test('контур-ориентир: совместил голову с контуром — кадр проходит при любом «поясе»', () => {
    for (const waist of [RULE_LIMITS.waist[0], 2.4, DEFAULT_RULES.waist, 4, RULE_LIMITS.waist[1]]) {
        const rules = { ...DEFAULT_RULES, waist };
        const guide = silhouetteGuide(rules);
        assert.ok(guide.crown > 0 && guide.chin > guide.crown && guide.chin < 0.7, String(waist));
        assert.ok(guide.waist <= 1);
        const advice = frameAdvice([faceAt(guide.face, guide.crown)], { rules });
        assert.equal(advice.code, 'ok', String(waist));
        assert.ok(advice.metrics.waist < 0.95, String(waist));           // с запасом
    }
    // Чем строже «пояс», тем мельче голова контура.
    assert.ok(silhouetteGuide({ ...DEFAULT_RULES, waist: 4 }).chin < silhouetteGuide().chin);
});

test('в «не двигайтесь» допуски шире: дрожание у границы отсчёт не срывает', () => {
    // Пояс вышел за край на 2 % высоты кадра.
    const h = 0.27;
    const edge = [faceAt(h, (1.02 - h * DEFAULT_RULES.waist) - h * FRAME_FIXED.headroom)];
    assert.equal(frameAdvice(edge).code, 'too_low');
    assert.equal(frameAdvice(edge, { relaxed: true }).code, 'ok');
    // На 5 % — уже нет: послабление маленькое.
    const far = [faceAt(h, (1.05 - h * DEFAULT_RULES.waist) - h * FRAME_FIXED.headroom)];
    assert.equal(frameAdvice(far, { relaxed: true }).code, 'too_low');
});

test('тексты подсказок: коротко, без точки и без «левее/правее»', () => {
    for (const [code, text] of Object.entries(ADVICE_TEXT)) {
        assert.ok(text.length <= 28, `${code}: ${text}`);
        assert.ok(!text.endsWith('.'), code);
        assert.ok(!/лев|прав/i.test(text), code);      // превью зеркальное
    }
    // Те же слова, что отдаёт сервер после проверки ИИ.
    const backend = read('../dress_code/__init__.py');
    for (const code of ['several', 'head_cropped', 'too_close', 'too_dark']) {
        assert.ok(backend.includes(`'${ADVICE_TEXT[code]}'`), code);
    }
});

test('подсказка не мигает: новая — после двух проверок подряд, «отлично» — сразу', () => {
    const smoother = createHintSmoother();
    assert.equal(smoother.push('none'), 'none');
    assert.equal(smoother.push('too_close'), 'none');
    assert.equal(smoother.push('none'), 'none');
    assert.equal(smoother.push('too_close'), 'none');
    assert.equal(smoother.push('too_close'), 'too_close');
    assert.equal(smoother.push('ok'), 'ok');
    smoother.reset();
    assert.equal(smoother.push('too_far'), 'too_far');
});

const okAt = (cx = 0.5, cy = 0.23, h = 0.22) => ({ code: 'ok', ok: true, face: { cx, cy, h } });
const bad = { code: 'too_close', ok: false };

test('автоснимок: человек стоит в рамке 1,2 секунды — снимок', () => {
    const shutter = createAutoShutter();
    assert.deepEqual(shutter.update(okAt(), 1000), { progress: 0, fire: false });
    assert.equal(shutter.holding(), true);
    assert.ok(Math.abs(shutter.update(okAt(), 1600).progress - 0.5) < 1e-9);
    assert.equal(shutter.update(okAt(), 2190).fire, false);
    assert.deepEqual(shutter.update(okAt(), 2200), { progress: 1, fire: true });
});

test('автоснимок: сдвинулся — отсчёт заново, кадр испортился — сброс', () => {
    const shutter = createAutoShutter();
    shutter.update(okAt(), 0);
    shutter.update(okAt(), 600);
    assert.equal(shutter.update(okAt(0.58), 900).progress, 0);          // шагнул в сторону
    assert.equal(shutter.update(okAt(0.58), 2099).fire, false);
    assert.equal(shutter.update(okAt(0.58), 2100).fire, true);
    const other = createAutoShutter();
    other.update(okAt(), 0);
    other.update(okAt(), 500);
    assert.equal(other.update(bad, 1000).progress, 0);                  // плохой кадр дольше допуска
    assert.equal(other.holding(), false);
    assert.equal(other.update(okAt(), 1100).progress, 0);
});

test('автоснимок: выдержка — из правил, стенд меняет её на ходу', () => {
    const shutter = createAutoShutter();
    shutter.update(okAt(), 0, 600);
    assert.ok(Math.abs(shutter.update(okAt(), 300, 600).progress - 0.5) < 1e-9);
    assert.equal(shutter.update(okAt(), 600, 600).fire, true);
    const slow = createAutoShutter();
    slow.update(okAt(), 0, 3000);
    assert.equal(slow.update(okAt(), 1300, 3000).fire, false);          // прежних 1,2 с мало
    assert.equal(slow.update(okAt(), 3000, 3000).fire, true);
    // Без третьего аргумента и с мусором — выдержка по умолчанию.
    const plain = createAutoShutter();
    plain.update(okAt(), 0);
    assert.equal(plain.update(okAt(), DEFAULT_RULES.hold_ms - 1, 0).fire, false);
    assert.equal(plain.update(okAt(), DEFAULT_RULES.hold_ms, NaN).fire, true);
});

test('автоснимок: одна сорвавшаяся проверка отсчёт не обнуляет', () => {
    const shutter = createAutoShutter();
    shutter.update(okAt(), 0);
    shutter.update(okAt(), 600);
    const blink = shutter.update(bad, 730);                              // нейросеть моргнула
    assert.equal(blink.fire, false);
    assert.ok(blink.progress > 0.4);
    assert.equal(shutter.holding(), true);
    assert.equal(shutter.update(okAt(), 1200).fire, true);
    // Плохой кадр сам по себе снимка не даёт, сколько бы ни прошло.
    const never = createAutoShutter();
    never.update(okAt(), 0);
    assert.equal(never.update(bad, 100).fire, false);
    assert.equal(never.update(bad, 5000).fire, false);
});

test('яркость части кадра', () => {
    const side = 8;
    const rgba = new Uint8ClampedArray(side * side * 4);
    for (let i = 0; i < side * side; i += 1) {
        const bright = (i % side) >= 4 ? 220 : 8;                         // правая половина светлая
        rgba.set([bright, bright, bright, 255], i * 4);
    }
    assert.ok(regionLuma(rgba, side, 0, 0, 4, 8, 1) < 20);
    assert.ok(regionLuma(rgba, side, 4, 0, 8, 8, 1) > 200);
    assert.equal(regionLuma(rgba, side, 3, 3, 3, 3), 0);
});

test('нейросеть: модель на месте, один поток, движок общий с «Доской почёта»', () => {
    const worker = read('../src/components/dress_code/faceWorker.js');
    assert.ok(worker.includes("from 'onnxruntime-web/wasm'"));
    assert.ok(worker.includes('ort.env.wasm.numThreads = 1'));
    const cutout = read('../src/components/events/cutout/cutoutWorker.js');
    const wasm = /from '(onnxruntime-web\/[^']+\.wasm\?url)'/;
    assert.equal(worker.match(wasm)[1], cutout.match(wasm)[1]);          // один файл — один кэш
    assert.equal(statSync(new URL('../src/components/dress_code/yunet-320.onnx', import.meta.url)).size, 232557);
    assert.ok(read('../src/components/dress_code/YUNET-LICENSE.txt').includes('MIT License'));
});

test('камера: без нейросети остаётся обычной, ручной снимок есть всегда', () => {
    const camera = read('../src/components/dress_code/DressCodeCamera.jsx');
    assert.ok(camera.includes(".catch(() => { if (alive) setAuto('off'); })"));
    assert.ok(camera.includes('aria-label="Сделать снимок"'));
    // Автоснимок уходит сам, ручной — через «Отправить».
    assert.ok(camera.includes("onCaptureRef.current?.(next.blob, 'camera')"));
    assert.ok(camera.includes('{shot && !autoSending && ('));
    // Автоснимок не отправился (нет связи) — остаётся обычным просмотром, по кругу камера не шлёт.
    assert.ok(camera.includes('setShot((prev) => (prev ? { ...prev, auto: false } : prev))'));
});

test('камера: правила с сервера, кадр без обрезки, весь сенсор', () => {
    const camera = read('../src/components/dress_code/DressCodeCamera.jsx');
    // Правила — из /me, цикл читает свежие (ползунки стенда) и передаёт выдержку.
    assert.ok(read('../src/components/dress_code/DressCodeView.jsx').includes('rules={state?.camera || null}'));
    assert.ok(camera.includes('rules: current'));
    assert.ok(camera.includes('shutter.update(advice, performance.now(), current.hold_ms)'));
    // Нейросети и на фото — одна и та же часть кадра.
    assert.equal(camera.match(/photoRegion\(video\.videoWidth, video\.videoHeight, aspectRef\.current\)/g).length, 2);
    const videoTag = camera.match(/<video[\s\S]*?\/>/)[0];
    assert.ok(!videoTag.includes('inset-0'));                            // видео больше не растянуто на экран с обрезкой
    assert.ok(videoTag.includes('width: videoWidth'));
    // 4:3 — весь сенсор; родной режим камеры, а не вырезка из 16:9.
    assert.ok(camera.includes("height: { ideal: 960 }, resizeMode: 'none'"));
});

test('стенд суперадмина: ничего не отправляет и не сохраняет, кнопка — только суперадмину', () => {
    const stand = read('../src/components/dress_code/DressCodeStand.jsx');
    assert.ok(stand.includes('autoSend={false}'));
    assert.ok(stand.includes('/stand/analyze'));
    assert.ok(!stand.includes('/photo'));                                // ручка приёма фото сотрудника
    assert.ok(stand.includes("axios.put(`${base}/settings`, { camera: draft }"));
    // Камера в стенде: автоснимок без autoSend остаётся просмотром и наружу не уходит.
    const camera = read('../src/components/dress_code/DressCodeCamera.jsx');
    assert.ok(camera.includes("if (sending) onCaptureRef.current?.(next.blob, 'camera')"));
    assert.ok(camera.includes('toShot(canvas, sending)'));
    const tab = read('../src/components/dress_code/DressCodeReviewTab.jsx');
    assert.match(tab, /\{data\?\.is_super_admin && \(\s+<button type="button" onClick=\{\(\) => setStand\(true\)\}/);
    assert.ok(tab.includes('{data?.is_super_admin && (confirmDelete ? ('));
    assert.ok(tab.includes("lazy(() => import('./DressCodeStand'))"));
});

test('нейросеть грузится заранее, когда сотрудник открыл раздел, и выгружается с ним', () => {
    const view = read('../src/components/dress_code/DressCodeView.jsx');
    assert.ok(view.includes('if (canCapture) warmFaceDetector()'));
    assert.ok(view.includes('useEffect(() => releaseFaceDetector, [])'));
    // Камера берёт общий детектор и сама его не выгружает.
    const camera = read('../src/components/dress_code/DressCodeCamera.jsx');
    assert.ok(camera.includes('sharedFaceDetector()'));
    assert.ok(!camera.includes('detector.dispose()'));
});
