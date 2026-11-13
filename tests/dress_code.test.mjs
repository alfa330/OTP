// «Дресс-код» (#418): подписи и мелкие расчёты экрана — зеркало dress_code/__init__.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    AI_LABELS, DECISION_LABELS, EMPLOYEE_STATUS, FRAME, QUALITY_LABELS, REVIEW_FILTERS, TOO_DARK_LUMA,
    aiTone, decisionTone, employeeStatus, filterParams, fmtDay, fmtDayFull, fmtTime, meanLuma, ofTotal,
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

test('рамка: верх у макушки, низ выше пояса, по центру', () => {
    assert.ok(FRAME.top > 0 && FRAME.top < 0.15);
    assert.ok(FRAME.bottom > 0.8 && FRAME.bottom < 1);
    assert.ok(FRAME.width > 0.5 && FRAME.width < 1);
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
