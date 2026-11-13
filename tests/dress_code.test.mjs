// «Дресс-код» (#418): подписи и мелкие расчёты экрана — зеркало dress_code/__init__.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    AI_LABELS, DECISION_LABELS, EMPLOYEE_STATUS, FRAME, QUALITY_LABELS, REVIEW_FILTERS, TOO_DARK_LUMA,
    aiTone, decisionTone, employeeStatus, filterParams, fmtDay, fmtDayFull, fmtTime, frameRegion, meanLuma, ofTotal,
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

test('рамка почти на весь экран: сверху и снизу — только место под кнопки', () => {
    // Владелец 13.11.2026: «очень большие поля снизу и сверху» — на телефоне 844 px высотой
    // рамка занимает больше трёх четвертей экрана и почти всю ширину.
    assert.ok(FRAME.top <= 72 && FRAME.bottom <= 128);
    assert.ok((844 - FRAME.top - FRAME.bottom) / 844 > 0.75);
    assert.ok(FRAME.sideShare >= 0.9 && FRAME.sideShare < 1);
    assert.ok(FRAME.maxAspect < 1);          // и на компьютере рамка портретная
});

test('на фото попадает ровно то, что в рамке', () => {
    // Телефон 390×844, кадр камеры 720×1280: видео растянуто на экран и обрезано по бокам.
    const box = { left: 0, top: 0, width: 390, height: 844 };
    const rect = { left: 16, top: 64, width: 358, height: 662 };
    const [sx, sy, sw, sh] = frameRegion(720, 1280, box, rect);
    const scale = 844 / 1280;
    assert.ok(Math.abs(sw - 358 / scale) < 0.01 && Math.abs(sh - 662 / scale) < 0.01);
    assert.ok(Math.abs(sy - 64 / scale) < 0.01);
    assert.ok(Math.abs(sx - ((720 - 390 / scale) / 2 + 16 / scale)) < 0.01);
    // Пропорции фото — как у рамки на экране.
    assert.ok(Math.abs(sw / sh - 358 / 662) < 0.001);
    // Рамка по центру: зеркальное превью фронтальной камеры даёт ту же область.
    assert.deepEqual(frameRegion(720, 1280, box, rect, true).map(Math.round), [sx, sy, sw, sh].map(Math.round));
    // Рамка не по центру — у зеркального превью область берётся с другой стороны кадра.
    const shifted = { left: 0, top: 64, width: 200, height: 662 };
    const plain = frameRegion(720, 1280, box, shifted);
    const flipped = frameRegion(720, 1280, box, shifted, true);
    assert.ok(flipped[0] > plain[0]);
    assert.ok(Math.abs((plain[0] + plain[2] / 2) + (flipped[0] + flipped[2] / 2) - 720) < 0.01);
});

test('область фото не выходит за кадр, а без размеров отдаётся весь кадр', () => {
    const [sx, sy, sw, sh] = frameRegion(640, 480, { left: 0, top: 0, width: 390, height: 844 },
        { left: -50, top: -50, width: 600, height: 1200 });
    assert.ok(sx >= 0 && sy >= 0 && sx + sw <= 640 && sy + sh <= 480);
    assert.deepEqual(frameRegion(640, 480, { left: 0, top: 0, width: 0, height: 0 }, {}), [0, 0, 640, 480]);
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
