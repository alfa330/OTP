// «Дресс-код» (#418): подписи и мелкие расчёты экрана — зеркало dress_code/__init__.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    AI_LABELS, CAMERA_BARS, DECISION_LABELS, EMPLOYEE_STATUS, PHOTO_ASPECT, QUALITY_LABELS, REVIEW_FILTERS, TOO_DARK_LUMA,
    aiTone, decisionTone, employeeStatus, filterParams, fitStage, fmtDay, fmtDayFull, fmtTime, meanLuma, ofTotal, pct,
    photoAspect, photoRegion,
} from '../src/components/dress_code/dressCode.js';

const backend = readFileSync(new URL('../dress_code/__init__.py', import.meta.url), 'utf8');

test('подписи статусов и решений совпадают с сервером', () => {
    for (const [key, label] of Object.entries({ ...AI_LABELS, ...DECISION_LABELS, ...QUALITY_LABELS })) {
        assert.ok(backend.includes(`'${label}'`), `нет на сервере: ${key} → ${label}`);
    }
});

test('цвет только со смыслом: нарушение красное, спорное жёлтое, ожидание без цвета', () => {
    assert.equal(aiTone('violation'), 'red');
    assert.equal(aiTone('unclear'), 'amber');
    assert.equal(aiTone('ok'), 'green');
    assert.equal(aiTone('pending'), 'slate');
    assert.equal(decisionTone('confirmed'), 'red');
    assert.equal(decisionTone('no_violation'), 'green');
    assert.equal(decisionTone(null), 'slate');
});

test('сотрудник до решения видит нейтральное «фото принято»', () => {
    assert.equal(employeeStatus('review').tone, 'slate');
    assert.match(employeeStatus('review').label, /руководитель/);
    assert.equal(employeeStatus('неизвестно'), EMPLOYEE_STATUS.review);
    assert.equal(employeeStatus('confirmed').label, 'Нарушение подтверждено');
});

test('фильтры вкладки: по умолчанию очередь «ждут решения»', () => {
    assert.equal(REVIEW_FILTERS[0].value, 'waiting');
    assert.deepEqual(filterParams('waiting'), { decision: 'waiting' });
    assert.deepEqual(filterParams('confirmed'), { decision: 'confirmed' });
    assert.deepEqual(filterParams('all'), {});
    assert.deepEqual(filterParams('missing'), {});
});

test('время сервера (Алматы, без пояса) не сдвигается поясом браузера', () => {
    assert.equal(fmtTime('2026-11-13T09:03:12'), '09:03');
    assert.equal(fmtTime('2026-11-13T23:59:00.123'), '23:59');
    assert.equal(fmtTime(null), '');
    assert.equal(fmtDay('2026-11-13'), '13.11');
    assert.equal(fmtDayFull('2026-11-13T09:00:00'), '13.11.2026');
});

test('доля — с явным знаменателем', () => {
    assert.equal(ofTotal(3, 20), '3 из 20');
    assert.equal(ofTotal(0, 0), '0');
});

test('яркость кадра: тёмный ниже порога, светлый выше', () => {
    const dark = new Uint8ClampedArray(4 * 16).fill(10);
    const light = new Uint8ClampedArray(4 * 16).fill(200);
    assert.ok(meanLuma(dark) < TOO_DARK_LUMA);
    assert.ok(meanLuma(light) > TOO_DARK_LUMA);
    assert.equal(meanLuma(new Uint8ClampedArray(0)), 0);
});

test('на фото попадает ровно то, что видно: высота кадра камеры не режется никогда', () => {
    // Владелец 13.11.2026: «авто фотографирование сработало даже без условия по пояс». На
    // вытянутой руке камера видит человека едва до пояса — сверху и снизу не теряется ни пикселя.
    assert.deepEqual(photoRegion(960, 1280), [0, 0, 960, 1280]);                 // 3:4 на месте 3:4 — весь сенсор
    assert.deepEqual(photoRegion(960, 1280, 9 / 16), [120, 0, 720, 1280]);       // высокий телефон: только бока
    assert.deepEqual(photoRegion(720, 1280, 0.6), [0, 0, 720, 1280]);            // кадр уже нужного — весь
    // Веб-камера и телефон на боку: портретная середина во всю высоту.
    assert.deepEqual(photoRegion(1280, 720), [370, 0, 540, 720]);
    assert.deepEqual(photoRegion(1280, 960), [280, 0, 720, 960]);
    for (const [width, height, aspect] of [[960, 1280, 0.57], [1280, 720, 0.75], [720, 1280, 0.75], [1920, 1080, 0.6]]) {
        const [sx, sy, sw, sh] = photoRegion(width, height, aspect);
        assert.deepEqual([sy, sh], [0, height]);
        assert.ok(sx >= 0 && sx + sw <= width);
        assert.ok(Math.abs((sx + sw / 2) - width / 2) <= 0.5);                   // середина кадра
    }
    assert.deepEqual(photoRegion(0, 0), [0, 0, 0, 0]);
});

test('пропорции снимка — по месту на экране, от 9:16 до 3:4', () => {
    assert.deepEqual({ ...PHOTO_ASPECT }, { min: 9 / 16, max: 3 / 4 });
    assert.equal(photoAspect(390, 500), 0.75);                                   // место шире 3:4 — весь сенсор
    assert.ok(Math.abs(photoAspect(390, 680) - 390 / 680) < 1e-12);              // высокий телефон — по месту
    assert.equal(photoAspect(390, 900), 9 / 16);                                 // уже 9:16 снимок не становится
    assert.equal(photoAspect(1280, 636), 0.75);                                  // компьютер
    assert.equal(photoAspect(0, 0), 0.75);
});

test('кадр занимает всё место между подсказкой и кнопками — без чёрных полос сверху и снизу', () => {
    // Владелец 13.11.2026: «очень большие поля снизу и сверху».
    assert.ok(CAMERA_BARS.top + CAMERA_BARS.bottom <= 170);
    const bars = CAMERA_BARS.top + CAMERA_BARS.bottom;
    const onScreen = (videoWidth, videoHeight, screenWidth, screenHeight) => {
        const stage = [screenWidth, screenHeight - bars];
        const [, , sw, sh] = photoRegion(videoWidth, videoHeight, photoAspect(...stage));
        return { stage, region: [sw, sh], frame: fitStage(sw, sh, ...stage) };
    };
    // Телефон 390×844, камера 960×1280: кадр — на всё место, у сенсора отрезаны бока.
    const tall = onScreen(960, 1280, 390, 844);
    assert.equal(tall.frame.height, tall.stage[1]);
    assert.ok(tall.frame.width >= 388);
    assert.deepEqual(tall.region, [734, 1280]);
    // Та же камера на невысоком экране (390×664): весь сенсор, узкие поля только по бокам.
    const low = onScreen(960, 1280, 390, 664);
    assert.deepEqual(low.region, [960, 1280]);
    assert.deepEqual(low.frame, { width: 375, height: 500 });
    // Камера 9:16 — вся, во всю высоту.
    const wide = onScreen(720, 1280, 390, 844);
    assert.deepEqual(wide.region, [720, 1280]);
    assert.equal(wide.frame.height, wide.stage[1]);
    // Пропорции не искажаются, за место кадр не выходит.
    for (const [w, h, sw, sh] of [[960, 1280, 360, 500], [720, 1280, 412, 700], [540, 720, 1440, 640]]) {
        const frame = fitStage(w, h, sw, sh);
        assert.ok(frame.width <= sw && frame.height <= sh);
        assert.ok(Math.abs(frame.width / frame.height - w / h) < 0.01);
    }
    assert.deepEqual(fitStage(960, 1280, 0, 0), { width: 0, height: 0 });
});

test('проценты стенда: с пробелом и настоящим минусом', () => {
    assert.equal(pct(0.234), '23 %');
    assert.equal(pct(-0.031), '−3 %');
    assert.equal(pct(-0.001), '0 %');
    assert.equal(pct(undefined), '0 %');
});

test('камера: галереи нет — только живой кадр, запасной вход — системная камера', () => {
    const camera = readFileSync(new URL('../src/components/dress_code/DressCodeCamera.jsx', import.meta.url), 'utf8');
    assert.ok(camera.includes('getUserMedia'));
    // Единственный выбор файла — системная камера (capture), и только на телефоне.
    const inputs = camera.match(/<input[^>]*type="file"[^>]*>/g) || [];
    assert.equal(inputs.length, 1);
    assert.ok(inputs[0].includes('capture="user"'));
    assert.ok(camera.includes('isMobileShell && !shot && (error || hang)'));
});
