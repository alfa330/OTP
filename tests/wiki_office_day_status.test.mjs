import test from 'node:test';
import assert from 'node:assert/strict';

import {
    DAY_SOURCE_LABELS, formatStamp, formatStampTime, officeDayStatus,
} from '../src/components/wiki/officeDayStatus.js';

/* Колонка ТЗ «Обновлено» — про свежесть данных, а не про выбранный в календаре
 * день. Разница между recorded_on («за какой день запись») и recorded_at
 * («когда её сделали») один раз уже стоила раздела: в колонку уезжал первый, и
 * она повторяла дату из календаря, а у офисов без отметки стоял прочерк.
 */

const WORKDAY = {
    mon: { from: '09:00', to: '19:00' },
    tue: { from: '09:00', to: '19:00' },
    wed: { from: '09:00', to: '19:00' },
    thu: { from: '09:00', to: '19:00' },
    fri: { from: '09:00', to: '19:00' },
    sat: null,
    sun: null,
};

// 2026-08-19 — среда, 2026-08-22 — суббота.
const WED = '2026-08-19';
const SAT = '2026-08-22';

test('расчёт по графику — обновлено берётся из записи офиса', () => {
    const status = officeDayStatus(
        { schedule: WORKDAY, updated_at: '2026-08-14T10:07:11' }, WED);
    assert.equal(status.state, 'open');
    assert.equal(status.source, 'schedule');
    assert.equal(status.recordedOn, null);
    assert.equal(status.updatedAt, '2026-08-14T10:07:11');
});

test('выходной по графику — обновлено тоже есть', () => {
    const status = officeDayStatus(
        { schedule: WORKDAY, updated_at: '2026-08-14T10:07:11' }, SAT);
    assert.equal(status.state, 'closed');
    assert.equal(status.updatedAt, '2026-08-14T10:07:11');
});

test('графика нет вовсе — состояния нет, но дата правки записи остаётся', () => {
    const status = officeDayStatus({ schedule: {}, updated_at: '2026-08-15T09:00:00' }, WED);
    assert.equal(status.state, 'none');
    assert.equal(status.updatedAt, '2026-08-15T09:00:00');
});

test('отметка дежурного — обновлено это КОГДА отметили, а не за какой день', () => {
    const status = officeDayStatus({
        schedule: WORKDAY,
        updated_at: '2026-08-14T10:07:11',
        day: {
            state: 'closed', source: 'manual', note: 'Прорвало трубу',
            recorded_on: WED, recorded_at: '2026-08-19T07:41:02',
        },
    }, WED);
    assert.equal(status.state, 'closed');
    assert.equal(status.source, 'record');
    assert.equal(status.note, 'Прорвало трубу');
    assert.equal(status.recordedOn, WED);
    assert.equal(status.updatedAt, '2026-08-19T07:41:02');
});

test('ночной снимок — источник отличается от отметки человека', () => {
    const status = officeDayStatus({
        schedule: WORKDAY,
        day: { state: 'open', source: 'auto', recorded_on: WED, recorded_at: '2026-08-19T23:45:03' },
    }, WED);
    assert.equal(status.source, 'snapshot');
    assert.equal(status.updatedAt, '2026-08-19T23:45:03');
});

test('отметка без времени (строки до появления recorded_at) — падаем на запись офиса', () => {
    const status = officeDayStatus({
        schedule: WORKDAY,
        updated_at: '2026-08-14T10:07:11',
        day: { state: 'closed', source: 'manual', recorded_on: WED, recorded_at: null },
    }, WED);
    assert.equal(status.updatedAt, '2026-08-14T10:07:11');
});

test('«офиса в городе нет» — обновлять нечего, прочерк (п. 4.3 ТЗ)', () => {
    const status = officeDayStatus(
        { no_office: true, updated_at: '2026-08-14T10:07:11' }, WED);
    assert.equal(status.state, 'absent');
    assert.equal(status.updatedAt, null);
    assert.equal(formatStamp(status.updatedAt), '—');
});

test('у каждого состояния, кроме «нет офиса», есть подпись источника', () => {
    ['record', 'snapshot', 'schedule'].forEach((source) => {
        assert.ok(DAY_SOURCE_LABELS[source], source);
    });
});

test('отметка времени печатается днём и днём с минутами', () => {
    assert.equal(formatStamp('2026-08-19T23:45:03'), '19.08.2026');
    assert.equal(formatStamp('2026-08-19'), '19.08.2026');
    assert.equal(formatStampTime('2026-08-19T23:45:03'), '19.08.2026, 23:45');
    // Без времени — только день, а не «19.08.2026, undefined».
    assert.equal(formatStampTime('2026-08-19'), '19.08.2026');
    assert.equal(formatStamp(null), '—');
    assert.equal(formatStampTime(''), '—');
});
