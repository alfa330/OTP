// Подписи журнала «Приема» и сверки загрузки: склонения и дни — то, что HR читает каждый раз.
import test from 'node:test';
import assert from 'node:assert/strict';

import { dayTitle, plural, timeOf } from '../src/components/hr_office/hrOffice.js';

const PEOPLE_GEN = ['сотрудника', 'сотрудников', 'сотрудников'];
const FIELDS = ['поле', 'поля', 'полей'];

test('склонения: 1, 2–4, 5+, 11–14 и 21', () => {
    assert.equal(plural(1, FIELDS), 'поле');
    assert.equal(plural(3, FIELDS), 'поля');
    assert.equal(plural(5, FIELDS), 'полей');
    assert.equal(plural(11, FIELDS), 'полей');
    assert.equal(plural(14, FIELDS), 'полей');
    assert.equal(plural(21, FIELDS), 'поле');
    assert.equal(plural(41, FIELDS), 'поле');
    assert.equal(plural(1, PEOPLE_GEN), 'сотрудника');     // «у 1 сотрудника»
    assert.equal(plural(35, PEOPLE_GEN), 'сотрудников');
    assert.equal(plural(0, PEOPLE_GEN), 'сотрудников');
});

test('день в журнале: сегодня, вчера, дата, другой год', () => {
    assert.equal(dayTitle('2026-11-10T15:42:10', '2026-11-10'), 'Сегодня');
    assert.equal(dayTitle('2026-11-09T23:59:00', '2026-11-10'), 'Вчера');
    assert.equal(dayTitle('2026-11-01T00:00:00', '2026-11-02'), 'Вчера');
    assert.equal(dayTitle('2026-10-31T10:00:00', '2026-11-01'), 'Вчера');    // через границу месяца
    assert.equal(dayTitle('2026-11-08T10:00:00', '2026-11-10'), '8 ноября');
    assert.equal(dayTitle('2025-12-31T10:00:00', '2026-01-02'), '31 декабря 2025');
    assert.equal(dayTitle('', '2026-11-10'), '');
});

test('время записи — из строки по Алматы, без пересчёта поясом браузера', () => {
    assert.equal(timeOf('2026-11-10T15:42:10.793076'), '15:42');
    assert.equal(timeOf('2026-11-10'), '');
});
