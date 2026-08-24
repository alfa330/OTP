import test from 'node:test';
import assert from 'node:assert/strict';

import {
    DAY_SOURCE_LABELS, closureCovers, formatDayShort, formatStamp, formatStampTime,
    officeDayStatus, statusUntil,
} from '../src/components/wiki/officeDayStatus.js';
import { officeStatus } from '../src/components/wiki/officeSchedule.js';

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

/* Закрытие на СРОК (задача #236). До него срок закрытия писать было некуда, и
 * дежурные писали его словами в причину («с 17.08 по 03.09 по тех.причинам»), а
 * отметка держалась ровно один день: 24.08.2026 Атырау и Костанай показывались
 * ОТКРЫТЫМИ, хотя стояли на ремонте. Границы — близнец серверного
 * closure_covers, набор случаев там и здесь одинаковый.
 */

const CLOSED = { closed_from: '2026-08-19', closed_until: '2026-08-29', schedule: WORKDAY };

test('границы срока: с — включительно, до — это уже рабочий день', () => {
    assert.equal(closureCovers(CLOSED, '2026-08-18'), false);
    assert.equal(closureCovers(CLOSED, '2026-08-19'), true);
    assert.equal(closureCovers(CLOSED, '2026-08-28'), true);
    assert.equal(closureCovers(CLOSED, '2026-08-29'), false);
});

test('срок не известен — правой границы нет', () => {
    const open = { closed_from: '2026-08-19', closed_until: null };
    assert.equal(closureCovers(open, '2027-01-01'), true);
});

test('без закрытия и с мусорной датой — не покрыт', () => {
    assert.equal(closureCovers({}, WED), false);
    assert.equal(closureCovers(CLOSED, 'позавчера'), false);
});

test('срок держит состояние во ВСЕ свои дни, а не один', () => {
    // Среда 26.08 — рабочий день по графику, но офис на ремонте.
    const status = officeDayStatus(CLOSED, '2026-08-26');
    assert.equal(status.state, 'closed');
    assert.equal(status.source, 'closure');
    assert.equal(status.closedUntil, '2026-08-29');
});

test('отметка за конкретный день сильнее срока — можно открыть на один день', () => {
    const status = officeDayStatus({
        ...CLOSED,
        day: { state: 'open', source: 'manual', recorded_on: '2026-08-26',
               recorded_at: '2026-08-26T09:00:00' },
    }, '2026-08-26');
    assert.equal(status.state, 'open');
    assert.equal(status.source, 'record');
});

test('срок сильнее ночного снимка — иначе назавтра офис «открывался» сам', () => {
    const status = officeDayStatus({
        ...CLOSED,
        day: { state: 'open', source: 'auto', recorded_on: '2026-08-26',
               recorded_at: '2026-08-26T23:45:00' },
    }, '2026-08-26');
    assert.equal(status.state, 'closed');
    assert.equal(status.source, 'closure');
});

test('после срока офис снова считается по графику', () => {
    // 29.08 — суббота, у этого графика выходной: важно, что закрыт он уже НЕ
    // сроком, а расписанием, иначе «до 29.08» тянулось бы дальше.
    assert.equal(officeDayStatus(CLOSED, '2026-08-29').source, 'schedule');
    // 31.08 — понедельник: офис работает.
    assert.equal(officeDayStatus(CLOSED, '2026-08-31').state, 'open');
});

test('срок словами: до даты, срок не известен, часы прошедшего дня', () => {
    assert.equal(statusUntil(officeDayStatus(CLOSED, '2026-08-26'), null, '2026-08-26'),
                 'до 29.08');
    // Другой год печатается целиком — иначе «до 05.01» врёт на год.
    assert.equal(statusUntil(officeDayStatus({ ...CLOSED, closed_until: '2027-01-05' },
                                             '2026-08-26'), null, '2026-08-26'),
                 'до 05.01.2027');
    const openEnded = officeDayStatus({ closed_from: '2026-08-19', schedule: WORKDAY }, WED);
    assert.equal(statusUntil(openEnded, null, WED), 'срок не известен');
    // Прошедший рабочий день: минут закрытия никто не писал, остаются часы дня.
    assert.equal(statusUntil(officeDayStatus({ schedule: WORKDAY }, WED), null, WED),
                 '09:00–19:00');
});

test('живой расчёт не спорит с отметкой дежурного', () => {
    // По графику среда рабочая до 19:00, но дежурный отметил «закрыт». Написать
    // рядом «до 19:00» значило бы отменить отметку надписью.
    const marked = officeDayStatus({
        schedule: WORKDAY,
        day: { state: 'closed', source: 'manual', recorded_on: WED, recorded_at: null },
    }, WED);
    const live = officeStatus(WORKDAY, new Date('2026-08-19T06:00:00Z'));
    assert.equal(statusUntil(marked, live, WED), null);
});

test('короткая дата: свой год без года, чужой — с годом', () => {
    assert.equal(formatDayShort('2026-08-29', '2026-08-24'), '29.08');
    assert.equal(formatDayShort('2027-01-05', '2026-08-24'), '05.01.2027');
    assert.equal(formatDayShort(null, '2026-08-24'), '—');
});
