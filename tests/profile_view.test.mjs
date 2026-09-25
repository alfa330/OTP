// «Профиль»: даты и стаж словами, тон показателей (src/components/profile/profileFormat.js).
import test from 'node:test';
import assert from 'node:assert/strict';

import { formatHireDate, formatTenure, pluralRu, toneClass } from '../src/components/profile/profileFormat.js';

test('окончания: год/года/лет, месяц/месяца/месяцев', () => {
    const years = (n) => pluralRu(n, 'год', 'года', 'лет');
    assert.deepEqual([1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 111].map(years),
        ['год', 'года', 'года', 'лет', 'лет', 'лет', 'лет', 'год', 'года', 'лет', 'лет']);
});

test('дата найма — словами, непонятное значение — как есть', () => {
    assert.equal(formatHireDate('2025-07-20'), '20 июля 2025');
    assert.equal(formatHireDate('2024-03-01T00:00:00'), '1 марта 2024');
    assert.equal(formatHireDate(''), '');
    assert.equal(formatHireDate(null), '');
    assert.equal(formatHireDate('20.07.2025'), '20.07.2025');
});

test('стаж считается по календарным месяцам, как прежняя плитка', () => {
    const today = new Date(2026, 8, 25); // 25.09.2026
    assert.equal(formatTenure('2025-07-20', today), '1 год 2 месяца');
    assert.equal(formatTenure('2026-09-01', today), 'Меньше месяца');
    assert.equal(formatTenure('2026-04-10', today), '5 месяцев');
    assert.equal(formatTenure('2021-09-03', today), '5 лет');
    assert.equal(formatTenure('2024-08-15', today), '2 года 1 месяц');
    assert.equal(formatTenure('', today), '');
});

test('цвет числа — только у значимого тона, иначе нейтральный', () => {
    assert.equal(toneClass('good'), 'text-emerald-600');
    assert.equal(toneClass('bad'), 'text-rose-600');
    assert.equal(toneClass(null), 'text-slate-900');
    assert.equal(toneClass(undefined, 'text-slate-400'), 'text-slate-400');
});
