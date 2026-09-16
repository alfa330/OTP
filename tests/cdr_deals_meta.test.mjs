import test from 'node:test';
import assert from 'node:assert/strict';

import {
    SOURCE_LABELS, countActiveFilters, dealsFileName, dealsQuery, reactionLabel, subjectOf,
} from '../src/components/cdr/dealsMeta.js';

/* Подписи режима «Сделки». Имя файла обязано совпадать с серверным
 * cdr/lead_report.py:report_filename — набор случаев тот же, что в
 * tests/test_cdr_lead_report.py. */

test('имя файла — источник, период с годом, суффикс «касания ОП»', () => {
    assert.equal(dealsFileName('crm_paid_hire', '2026-09-01', '2026-09-14'),
        'Платный найм 01.09.2026-14.09.2026 + касания ОП.xlsx');
    assert.equal(dealsFileName('crm_paid_hire', '2026-09-14', '2026-09-14', 'json'),
        'Платный найм 14.09.2026 + касания ОП.json');
    assert.equal(dealsFileName('amo', '2026-09-01', '2026-09-03'),
        'Основа 01.09.2026-03.09.2026 + касания ОП.xlsx');
});

test('подписи источников и субъектов', () => {
    assert.equal(SOURCE_LABELS.amo, 'Основа');
    assert.equal(subjectOf('crm_stream').many, 'лидов');
    assert.equal(subjectOf('неизвестно').one, 'сделка');
});

test('реакция ОП читается по-человечески, минус сохраняется', () => {
    assert.equal(reactionLabel(null), '—');
    assert.equal(reactionLabel(0.1), '6 с');
    assert.equal(reactionLabel(-0.5), '−30 с');
    assert.equal(reactionLabel(12.4), '12 мин');
    assert.equal(reactionLabel(150), '2.5 ч');
    assert.equal(reactionLabel(3000), '2.1 сут');
});

test('счётчик фильтров не считает период и «все»', () => {
    assert.equal(countActiveFilters({ presence: 'all' }), 0);
    assert.equal(countActiveFilters({ presence: 'with', park: 'iTaxi', talkedOnly: true }), 3);
});

test('параметры запроса — только заданное', () => {
    const params = dealsQuery('amo', { from: '2026-09-01', to: '2026-09-03' },
        { presence: 'without', ownOnly: true, park: '', phone: '5550001' }, { page: 2 });
    assert.deepEqual(params, {
        source: 'amo', date_from: '2026-09-01', date_to: '2026-09-03', page: 2,
        phone: '5550001', presence: 'without', own_only: 1,
    });
});
