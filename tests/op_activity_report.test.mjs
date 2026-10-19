import test from 'node:test';
import assert from 'node:assert/strict';

import {
    OP_ACTIVITY_COLUMNS,
    OP_ACTIVITY_GROUP_ALL,
    daysLabel,
    defaultFilters,
    exportQuery,
    filterShifts,
    formatClockHms,
    formatHms,
    journalBar,
    journalQuery,
    keepOperators,
    monthGrid,
    opActivityColumns,
    operatorOptions,
    operatorsLabel,
    pendingCallsNotice,
    sameFilters,
    summaryLabel,
} from '../src/components/monitoring/opActivityShared.js';

/*
 * «Активность операторов» (ТЗ #392). Колонки — строго в порядке ТЗ, длительности — ЧЧ:ММ:СС,
 * фильтр экрана — тот же, что у выгрузки на сервере (activity.filter_shifts): разойдись они,
 * файл показал бы не те строки, что таблица.
 */

const report = {
    month: '2026-10',
    months: [{ value: '2026-10', label: 'Октябрь 2026' }, { value: '2026-09', label: 'Сентябрь 2026' }],
    groups: [{ key: 'osnova', label: 'Основа' }, { key: 'yar', label: 'ЯР' }, { key: 'potok', label: 'Поток' }],
    operators: [
        { id: 1, name: 'Иванов', group_keys: ['yar'] },
        { id: 2, name: 'Петрова', group_keys: ['osnova'] },
        { id: 3, name: 'Сидоров', group_keys: ['yar', 'potok'] },
    ],
    shifts: [
        { key: 'a', date: '2026-10-05', operator_id: 1, group_key: 'yar' },
        { key: 'b', date: '2026-10-05', operator_id: 2, group_key: 'osnova' },
        { key: 'c', date: '2026-10-06', operator_id: 3, group_key: 'potok' },
    ],
};

test('колонки ТЗ строго по порядку', () => {
    assert.deepEqual(OP_ACTIVITY_COLUMNS.map((c) => c.label), [
        'Дата', 'Оператор', 'Группа', 'Вход в систему', 'Среднее время разговора', 'Общее время разговора',
        'Время на исходе / Активный', 'Время в ожидании', 'Время перерыва', 'Время тех. перерыва',
        'Количество звонков (все попытки)', 'Количество дозвонов (успешные разговоры)',
        'Время выхода из системы', 'Фактическое отработанное время',
    ]);
    assert.match(opActivityColumns(15).find((c) => c.key === 'connected').hint, /15 секунд/);
    assert.equal(opActivityColumns(10), OP_ACTIVITY_COLUMNS);
});

test('длительность и время — ЧЧ:ММ:СС', () => {
    assert.equal(formatHms(0), '00:00:00');
    assert.equal(formatHms(3725), '01:02:05');
    assert.equal(formatHms(90061), '25:01:01');
    assert.equal(formatHms(null), '—');
    assert.equal(formatClockHms('2026-10-05T09:01:12'), '09:01:12');
    assert.equal(formatClockHms(''), '—');
});

test('фильтр: группа, операторы и дни, пустое — «все»', () => {
    assert.equal(filterShifts(report.shifts, defaultFilters(report)).length, 3);
    assert.deepEqual(filterShifts(report.shifts, { group: 'yar' }).map((r) => r.key), ['a']);
    assert.deepEqual(filterShifts(report.shifts, { operatorIds: [2, 3] }).map((r) => r.key), ['b', 'c']);
    assert.deepEqual(filterShifts(report.shifts, { days: ['2026-10-06'] }).map((r) => r.key), ['c']);
    assert.deepEqual(filterShifts(report.shifts, { group: 'osnova', days: ['2026-10-06'] }), []);
});

test('список операторов зависит от группы, лишние уходят из выбора', () => {
    assert.deepEqual(operatorOptions(report, 'yar').map((o) => o.value), [1, 3]);
    assert.deepEqual(operatorOptions(report, OP_ACTIVITY_GROUP_ALL).map((o) => o.value), [1, 2, 3]);
    assert.deepEqual(keepOperators(report, 'osnova', [1, 2, 3]), [2]);
    assert.equal(operatorsLabel([], []), 'Все операторы');
    assert.equal(operatorsLabel([2], operatorOptions(report, 'all')), 'Петрова');
    assert.equal(operatorsLabel([1, 2], []), '2 оператора');
});

test('дни месяца и подпись', () => {
    const grid = monthGrid('2026-10');
    assert.equal(grid.lead, 3);                 // 1 октября 2026 — четверг
    assert.equal(grid.days.length, 31);
    assert.equal(grid.days[0], '2026-10-01');
    assert.equal(daysLabel([]), 'Весь месяц');
    assert.equal(daysLabel(['2026-10-05']), '5 октября');
    assert.equal(daysLabel(['2026-10-05', '2026-10-06']), '2 дня');
});

test('черновик фильтров сравнивается без учёта порядка', () => {
    const a = { group: 'all', month: '2026-10', operatorIds: [1, 2], days: ['2026-10-02', '2026-10-01'] };
    const b = { group: 'all', month: '2026-10', operatorIds: [2, 1], days: ['2026-10-01', '2026-10-02'] };
    assert.ok(sameFilters(a, b));
    assert.ok(!sameFilters(a, { ...b, group: 'yar' }));
});

test('выгрузка уходит с теми же фильтрами, журнал — со сменой', () => {
    assert.equal(exportQuery({ month: '2026-10', group: 'all', operatorIds: [], days: [] }), 'month=2026-10');
    assert.equal(exportQuery({ month: '2026-10', group: 'yar', operatorIds: [3, 1], days: ['2026-10-06', '2026-10-05'] }),
        'month=2026-10&group=yar&operator_ids=3%2C1&days=2026-10-05%2C2026-10-06');
    assert.equal(journalQuery({ operator_id: 7, login_at: '2026-10-05T09:00:00', logout_at: '2026-10-05T18:00:00' }),
        'operator_id=7&start=2026-10-05T09%3A00%3A00&end=2026-10-05T18%3A00%3A00');
});

test('подписи итогов и догрузки звонков', () => {
    assert.equal(summaryLabel(report.shifts), '3 смены · 3 оператора');
    assert.equal(summaryLabel([]), '');
    assert.equal(pendingCallsNotice({ calls_pending_days: [] }), null);
    assert.match(pendingCallsNotice({ calls_pending_days: ['2026-10-17', '2026-10-18'] }), /за 2 дня/);
});

test('полоса смены — доли по длительности, соседние одного цвета склеены', () => {
    const parts = journalBar([
        { style: 'wait', seconds: 60 }, { style: 'wait', seconds: 60 }, { style: 'talk', seconds: 120 },
        { style: 'logout', seconds: 0 },
    ]);
    assert.deepEqual(parts.map((p) => [p.style, p.seconds]), [['wait', 120], ['talk', 120]]);
    assert.equal(parts[0].share, 0.5);
});

test('страницы: номер в пределах, срез и подпись «с–по»', async () => {
    const { pageSlice } = await import('../src/components/monitoring/opActivityShared.js');
    const rows = Array.from({ length: 120 }, (_, i) => ({ key: i }));
    const second = pageSlice(rows, 2, 50);
    assert.deepEqual([second.page, second.pageCount, second.from, second.to, second.rows.length], [2, 3, 51, 100, 50]);
    const last = pageSlice(rows, 9, 50);
    assert.deepEqual([last.page, last.from, last.to, last.rows.length], [3, 101, 120, 20]);
    const empty = pageSlice([], 1, 50);
    assert.deepEqual([empty.page, empty.pageCount, empty.from, empty.to], [1, 1, 0, 0]);
});

test('счётчики на сегментах групп считают остальные фильтры, но не саму группу', async () => {
    const { groupCounts, groupOptions } = await import('../src/components/monitoring/opActivityShared.js');
    const counts = groupCounts(report.shifts, { days: ['2026-10-05'] });
    assert.deepEqual(counts, { all: 2, yar: 1, osnova: 1 });
    const options = groupOptions(report, counts);
    assert.deepEqual(options.map((o) => [o.value, o.count]), [['all', 2], ['osnova', 1], ['yar', 1], ['potok', 0]]);
});

test('у оператора в списке — число его смен в выбранной группе', async () => {
    const { operatorOptions } = await import('../src/components/monitoring/opActivityShared.js');
    assert.deepEqual(operatorOptions(report, 'all').map((o) => [o.value, o.meta]), [[1, '1'], [2, '1'], [3, '1']]);
    assert.deepEqual(operatorOptions(report, 'yar').map((o) => [o.value, o.meta]), [[1, '1'], [3, undefined]]);
});

test('«Сбросить» видно только при фильтре сверх «весь последний месяц»', async () => {
    const { isFiltered, defaultFilters } = await import('../src/components/monitoring/opActivityShared.js');
    assert.equal(isFiltered(defaultFilters(report), report), false);
    assert.equal(isFiltered({ ...defaultFilters(report), group: 'yar' }, report), true);
    assert.equal(isFiltered({ ...defaultFilters(report), month: '2026-09' }, report), true);
    assert.equal(isFiltered({ ...defaultFilters(report), days: ['2026-10-01'] }, report), true);
});

test('месяц листается стрелками по списку сервера', async () => {
    const { monthNeighbours, monthLabelOf } = await import('../src/components/monitoring/opActivityShared.js');
    assert.deepEqual(monthNeighbours(report.months, '2026-10'), { newer: null, older: report.months[1] });
    assert.deepEqual(monthNeighbours(report.months, '2026-09'), { newer: report.months[0], older: null });
    assert.equal(monthLabelOf('2026-09'), 'Сентябрь 2026');
});

test('быстрые дни: вчера и семь дней — только внутри месяца и не в будущем', async () => {
    const { dayPresets } = await import('../src/components/monitoring/opActivityShared.js');
    const october = dayPresets('2026-10', '2026-10-19');
    assert.deepEqual(october.map((p) => p.key), ['yesterday', 'week']);
    assert.deepEqual(october[0].days, ['2026-10-18']);
    assert.equal(october[1].days[0], '2026-10-12');
    assert.equal(october[1].days.at(-1), '2026-10-18');
    // Первое число: «вчера» — уже другой месяц, а от недели остаётся ноль дней.
    assert.deepEqual(dayPresets('2026-10', '2026-10-01'), []);
    assert.deepEqual(dayPresets('2026-09', '2026-10-19'), []);
});

test('метки журнала — итоги смены по статусам и фильтр ленты', async () => {
    const { journalFilterChips, filterJournal } = await import('../src/components/monitoring/opActivityShared.js');
    const segments = [
        { kind: 'wait', seconds: 40 }, { kind: 'dial', seconds: 20 }, { kind: 'talk', seconds: 120 },
        { kind: 'break', seconds: 900 }, { kind: 'tech', seconds: 300 }, { kind: 'logout', seconds: 0 },
    ];
    const chips = journalFilterChips(segments);
    assert.deepEqual(chips.map((c) => [c.key, c.seconds]),
        [['all', null], ['talk', 120], ['dial', 20], ['wait', 40], ['pause', 1200], ['offline', 0]]);
    assert.equal(filterJournal(segments, 'pause').length, 2);
    assert.equal(filterJournal(segments, 'all').length, 6);
    assert.equal(journalFilterChips([{ kind: 'talk', seconds: 5 }]).some((c) => c.key === 'pause'), false);
});
