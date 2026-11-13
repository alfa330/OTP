// «Дресс-код» (#418): автопроверка кадра — разбор ответа нейросети, подсказки, автоснимок.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';

import {
    ADVICE_TEXT, FACE_INPUT, FRAME_RULES, createAutoShutter, createHintSmoother, decodeFaces, facesInFrame,
    fitInput, frameAdvice, regionLuma, toInputTensor,
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

test('лицо из кадра сети переводится в доли рамки на экране', () => {
    // Телефон 390×844: видимая часть кадра 591,6×1280 вписана во вход как 148×320.
    const fit = fitInput(591.6, 1280);
    assert.deepEqual([fit.width, fit.height], [148, 320]);
    const box = { left: 0, top: 0, width: 390, height: 844 };
    const rect = { left: 15.5, top: 64, width: 359, height: 662 };
    const [face] = facesInFrame([{ x: 59, y: 40, w: 30, h: 40, score: 0.9 }], fit, box, rect);
    assert.ok(Math.abs(face.x - (59 / 148 * 390 - 15.5) / 359) < 1e-6);
    assert.ok(Math.abs(face.y - (40 / 320 * 844 - 64) / 662) < 1e-6);
    assert.ok(Math.abs(face.w - (30 / 148 * 390) / 359) < 1e-6);
    assert.ok(Math.abs(face.h - (40 / 320 * 844) / 662) < 1e-6);
    assert.deepEqual(facesInFrame([{ x: 1, y: 1, w: 1, h: 1 }], { width: 0, height: 0 }, box, rect), []);
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

test('в «не двигайтесь» допуски шире: дрожание у границы отсчёт не срывает', () => {
    const edge = [face({ x: 0.32, w: 0.36, h: FRAME_RULES.maxFace + 0.015 })];
    assert.equal(frameAdvice(edge).code, 'too_close');
    assert.equal(frameAdvice(edge, { relaxed: true }).code, 'ok');
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

test('нейросеть грузится заранее, когда сотрудник открыл раздел, и выгружается с ним', () => {
    const view = read('../src/components/dress_code/DressCodeView.jsx');
    assert.ok(view.includes('if (canCapture) warmFaceDetector()'));
    assert.ok(view.includes('useEffect(() => releaseFaceDetector, [])'));
    // Камера берёт общий детектор и сама его не выгружает.
    const camera = read('../src/components/dress_code/DressCodeCamera.jsx');
    assert.ok(camera.includes('sharedFaceDetector()'));
    assert.ok(!camera.includes('detector.dispose()'));
});
