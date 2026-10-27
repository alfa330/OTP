// «Аренда авто» (#398): форматирование экрана ночного отчёта — rentalMeta.js.
// Время с сервера — наивное время Алматы: разбирается по цифрам строки, без поясов браузера.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    FILTERS, exportFileName, failedSources, gpsSourceFailed, matchesQuery, normalizePlate,
    shortTime, silenceLabel, slotLabel,
} from '../src/components/rental/rentalMeta.js';

test('slot and signal time keep Almaty digits as they are', () => {
    assert.equal(slotLabel('2026-10-28T01:00:00'), '28 октября, 01:00');
    assert.equal(shortTime('2026-07-07T18:39:12'), '07.07 18:39');
    assert.equal(shortTime(null), '');
});

test('silence is counted to the moment of collection', () => {
    assert.equal(silenceLabel('2026-10-27T11:02:00', '2026-10-27T15:25:00'), '4 ч');
    assert.equal(silenceLabel('2026-10-27T15:00:00', '2026-10-27T15:25:00'), '');
    assert.equal(silenceLabel('2026-07-07T18:39:00', '2026-10-27T15:25:00'), '111 дн');
    assert.equal(silenceLabel(null, '2026-10-27T15:25:00'), '');
});

test('plate search ignores spaces, case and cyrillic twins', () => {
    const row = { plate: '501ABE08', address: 'Утеген Батыра ул., 15А', gps_site: 'Wialon' };
    assert.equal(normalizePlate('501 аве 08'), '501ABE08');
    assert.ok(matchesQuery(row, '501 АВЕ'));
    assert.ok(matchesQuery(row, 'утеген'));
    assert.ok(!matchesQuery(row, '999'));
    assert.ok(matchesQuery(row, '   '));
});

test('filters match the four groups of the screen', () => {
    const rows = [
        { gps_status: 'offline', needs_check: false },
        { gps_status: 'none', needs_check: false },
        { gps_status: 'online', needs_check: true },
        { gps_status: 'online', needs_check: false },
    ];
    const counts = Object.fromEntries(FILTERS.map((f) => [f.value, rows.filter(f.test).length]));
    assert.deepEqual(counts, { all: 4, offline: 1, none: 1, check: 1 });
});

test('silent sources and file name', () => {
    const sources = { yume: { ok: true }, efir: { ok: false, error: 'не ответил' } };
    assert.deepEqual(failedSources(sources), [{ name: 'efir', label: 'Efir', error: 'не ответил' }]);
    assert.equal(gpsSourceFailed(sources), true);
    assert.equal(gpsSourceFailed({ yume: { ok: false } }), false);
    assert.equal(exportFileName({ slot_at: '2026-10-28T01:00:00' }), 'Аренда авто 28.10.2026 01-00.xlsx');
});
