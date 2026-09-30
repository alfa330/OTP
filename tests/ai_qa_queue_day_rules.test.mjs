import test from 'node:test';
import assert from 'node:assert/strict';

import {
    plural, dayTitle, timeOf, itemKey, commonReasons, rowReasons, applyReviewed, queueSummary, NO_DAY,
} from '../src/components/call_qa/queueDayRules.js';

/**
 * Очередь ревью по дням: подписи дня, какие причины показывать у строки и как
 * сводка дня меняется, когда разговор только что проверили.
 */

const NOW = new Date(2026, 8, 30, 12, 0);   // среда, 30 сентября 2026

test('склонения', () => {
    assert.equal(plural(1, 'день', 'дня', 'дней'), 'день');
    assert.equal(plural(3, 'день', 'дня', 'дней'), 'дня');
    assert.equal(plural(5, 'день', 'дня', 'дней'), 'дней');
    assert.equal(plural(11, 'день', 'дня', 'дней'), 'дней');
    assert.equal(plural(21, 'день', 'дня', 'дней'), 'день');
    assert.equal(plural(112, 'день', 'дня', 'дней'), 'дней');
});

test('заголовок дня: день недели, «сегодня/вчера», год — только чужой', () => {
    assert.deepEqual(dayTitle('2026-09-30', NOW), { title: 'Среда, 30 сентября', relative: 'Сегодня' });
    assert.deepEqual(dayTitle('2026-09-29', NOW), { title: 'Вторник, 29 сентября', relative: 'Вчера' });
    assert.deepEqual(dayTitle('2026-09-23', NOW), { title: 'Среда, 23 сентября', relative: null });
    assert.equal(dayTitle('2025-12-31', NOW).title, 'Среда, 31 декабря 2025');
    // Первое число месяца: «вчера» считается через границу месяца.
    assert.equal(dayTitle('2026-09-30', new Date(2026, 9, 1)).relative, 'Вчера');
    assert.deepEqual(dayTitle(NO_DAY, NOW), { title: 'Без даты', relative: null });
});

test('время строки берётся из подписи; у заявки Chat2Desk его нет', () => {
    assert.equal(timeOf('23.09 12:04'), '12:04');
    assert.equal(timeOf('23.09'), null);
    assert.equal(timeOf(null), null);
    assert.equal(itemKey({ subject: 'imported_call', id: 5 }), 'imported_call-5');
    assert.equal(itemKey({ id: 5 }), 'call-5');
});

test('причина, которая есть у всех ждущих, у строки не повторяется', () => {
    const day = { open: 3, reasons: { critical: 1, lowconf: 3, pending: 3 } };
    assert.deepEqual(commonReasons(day).sort(), ['lowconf', 'pending']);
    assert.deepEqual(rowReasons(['critical', 'lowconf', 'pending'], commonReasons(day)), ['critical']);
    assert.deepEqual(rowReasons(['lowconf', 'pending'], commonReasons(day)), []);
    // Один разговор за день: его причины уже названы на карточке дня — в строке не повторяем.
    assert.deepEqual(commonReasons({ open: 1, reasons: { lowconf: 1 } }), ['lowconf']);
    assert.deepEqual(commonReasons({ open: 0, reasons: {} }), []);
    // «Без флагов» у строки остаётся — иначе строка выглядела бы недогруженной.
    assert.deepEqual(rowReasons(['ok'], ['ok']), ['ok']);
});

test('проверенный разговор меняет сводку своего дня на месте', () => {
    const days = [
        { day: '2026-09-23', open: 2, critical: 1, reasons: { critical: 1, lowconf: 2 }, evaluated: 4, reviewed: 2, corrected: 0 },
        { day: '2026-09-22', open: 1, critical: 0, reasons: { lowconf: 1 }, evaluated: 1, reviewed: 0, corrected: 0 },
    ];
    const next = applyReviewed(days, [
        { key: 'call-1', day: '2026-09-23', reasons: ['critical', 'lowconf'], corrected: true },
        { key: 'call-9', day: null, reasons: ['lowconf'] },      // открыт не из очереди — день неизвестен
    ]);
    assert.deepEqual(next[0], { day: '2026-09-23', open: 1, critical: 0, reasons: { lowconf: 1 },
                                evaluated: 4, reviewed: 3, corrected: 1 });
    assert.equal(next[1], days[1]);
    // Проверили всех — день остаётся, но ждущих нет.
    const done = applyReviewed(days, [{ key: 'call-2', day: '2026-09-22', reasons: ['lowconf'] }]);
    assert.equal(done[1].open, 0);
    assert.equal(done[1].reviewed, 1);
    assert.deepEqual(queueSummary(done), { open: 2, critical: 1, days: 1 });
});
