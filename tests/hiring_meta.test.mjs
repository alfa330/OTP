// «Воронка найма» (#404): форматирование и подписи раздела — hiringMeta.js.
// Время с сервера — наивное время Алматы: разбирается по цифрам строки, без поясов браузера.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    STAGES, STAGE_LABELS, FUNNEL_STAGES, addDays, daysSince, joinDateTime, longTime, matchesQuery, monthLabel, percent,
    plural, prettyPhone, reasonKindFor, shiftMonth, shortTime, splitDateTime,
} from '../src/components/hiring/hiringMeta.js';

test('ten stages in the order of the spec', () => {
    assert.equal(STAGES.length, 10);
    assert.deepEqual(STAGES.slice(0, 3), ['contacts', 'responses', 'in_work']);
    assert.equal(STAGE_LABELS.hired, 'Отработал 7 дней');
    assert.deepEqual(FUNNEL_STAGES, ['in_work', 'dialed', 'invited_office', 'invited_training', 'on_training', 'on_line', 'hired']);
});

test('times keep Almaty digits as they are', () => {
    assert.equal(shortTime('2026-10-30T14:05:00'), '30.10 14:05');
    assert.equal(shortTime('2026-10-30'), '30.10');
    assert.equal(shortTime(null), '');
    assert.equal(longTime('2026-10-30T14:05:00'), '30 октября, 14:05');
    assert.equal(longTime('2026-10-30'), '30 октября');
    assert.deepEqual(splitDateTime('2026-10-30T14:05:00'), { date: '2026-10-30', time: '14:05' });
    assert.equal(joinDateTime('2026-10-30', '09:30'), '2026-10-30T09:30');
    assert.equal(joinDateTime('', '09:30'), '');
});

test('month helpers', () => {
    assert.equal(monthLabel('2026-10'), 'Октябрь 2026');
    assert.equal(shiftMonth('2026-01', -1), '2025-12');
    assert.equal(shiftMonth('2026-12', 1), '2027-01');
    assert.equal(addDays('2026-10-30', 3), '2026-11-02');
    assert.equal(daysSince('2026-10-01', '2026-10-15'), 14);
    assert.equal(daysSince('2026-10-20T10:00:00', '2026-10-15'), -5);
});

test('phones, percents, plurals', () => {
    assert.equal(prettyPhone('+77051234567'), '+7 705 123 45 67');
    assert.equal(prettyPhone('x'), 'x');
    assert.equal(percent(33.3), '33,3 %');
    assert.equal(percent(null), '—');
    assert.equal(plural(1, 'смена', 'смены', 'смен'), 'смена');
    assert.equal(plural(3, 'смена', 'смены', 'смен'), 'смены');
    assert.equal(plural(11, 'смена', 'смены', 'смен'), 'смен');
    assert.equal(plural(25, 'смена', 'смены', 'смен'), 'смен');
});

test('reason kind follows initiator', () => {
    assert.equal(reasonKindFor('refused', 'ours'), 'refusal_ours');
    assert.equal(reasonKindFor('dropped', 'we'), 'drop_we');
    assert.equal(reasonKindFor('refused', 'self'), null);
});

test('client-side search matches name, phone and candidate number', () => {
    const card = { full_name: 'Иванов Иван', phone: '+77051234567', city: 'Алматы', candidate_no: 17 };
    assert.ok(matchesQuery(card, 'иван'));
    assert.ok(matchesQuery(card, '123 45'));
    assert.ok(matchesQuery(card, '17'));
    assert.ok(matchesQuery(card, ''));
    assert.ok(!matchesQuery(card, 'Астана'));
    assert.ok(!matchesQuery(card, '99'));
});
