import test from 'node:test';
import assert from 'node:assert/strict';

import { lastSeenLabel } from '../src/components/sessions/userAgent.js';

/**
 * «Когда был в сети» в строке раздела «Сессии» на телефоне.
 *
 * Дата из API — стенные часы Алматы без пояса («2026-09-14T11:43:15»), и
 * читать её обязано как местное время: так же её читает formatDate в
 * карточке сотрудника, и время в строке с карточкой обязано совпадать.
 */

const now = new Date(2026, 8, 14, 12, 0);

test('сегодня — часы и минуты', () => {
    assert.equal(lastSeenLabel('2026-09-14T09:05:00', now), '09:05');
    assert.equal(lastSeenLabel('2026-09-14T00:00:00', now), '00:00', 'полночь — ещё сегодня');
    assert.equal(lastSeenLabel('2026-09-14T11:43:15.334979', now), '11:43', 'микросекунды из API');
});

test('вчера — словом, в том числе поздним вечером', () => {
    assert.equal(lastSeenLabel('2026-09-13T23:59:00', now), 'вчера');
    assert.equal(lastSeenLabel('2026-09-13T00:01:00', now), 'вчера');
});

test('вчера на границе месяца и года', () => {
    assert.equal(lastSeenLabel('2026-09-30T22:00:00', new Date(2026, 9, 1, 8, 0)), 'вчера');
    assert.equal(lastSeenLabel('2025-12-31T21:00:00', new Date(2026, 0, 1, 9, 0)), 'вчера');
});

test('раньше в этом году — число и месяц', () => {
    assert.equal(lastSeenLabel('2026-09-12T10:00:00', now), '12 сент.');
    assert.equal(lastSeenLabel('2026-08-24T17:16:55', now), '24 авг.');
    assert.equal(lastSeenLabel('2026-05-02T08:00:00', now), '2 мая', 'родительный падеж, а не «май»');
});

test('прошлый год — короткая дата', () => {
    assert.equal(lastSeenLabel('2025-12-30T10:00:00', now), '30.12.25');
});

test('пусто и мусор не ломают строку', () => {
    assert.equal(lastSeenLabel(null, now), '');
    assert.equal(lastSeenLabel('', now), '');
    assert.equal(lastSeenLabel('не дата', now), '');
});
