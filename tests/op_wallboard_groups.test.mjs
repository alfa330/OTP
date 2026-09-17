import test from 'node:test';
import assert from 'node:assert/strict';

import {
    OP_GROUP_ALL,
    opEntryTime,
    opGroupOptions,
    opGroupView,
    opSelectedGroup,
} from '../src/components/monitoring/opWallboardGroups.js';

/*
 * Табло ОП, ТЗ #339. Фильтр по группам подменяет куски снимка, а плитки, график и список читают
 * его как раньше; время входа ночной смены помечается «вчера». Проверяется отдельно, потому что
 * ошибка здесь не падает, а тихо показывает цифры отдела под названием группы.
 */

const snapshot = {
    day: '2026-09-17',
    totals: { arrived: 10 },
    hourly: [{ hour: 9, arrived: 10 }],
    now: { operators_online: 5 },
    operators: [
        { id: 1, name: 'Основин', group_id: 36 },
        { id: 2, name: 'Яров', group_id: 15 },
        { id: null, name: 'Номер 6999', group_id: null },
    ],
    groups: [
        { id: 36, label: 'Основа', totals: { arrived: 10 }, hourly: [{ hour: 9, arrived: 10 }], now: { operators_online: 3 } },
        { id: 15, label: 'ЯР', totals: { arrived: 0 }, hourly: [{ hour: 9, arrived: 0 }], now: { operators_online: 2 } },
    ],
};

test('варианты фильтра — «Все» и группы снимка по порядку', () => {
    assert.deepEqual(opGroupOptions(snapshot).map((o) => o.label), ['Все', 'Основа', 'ЯР']);
    assert.deepEqual(opGroupOptions(null).map((o) => o.value), [OP_GROUP_ALL]);
});

test('выбранная группа подменяет итоги, часы, «сейчас» и список', () => {
    const view = opGroupView(snapshot, opSelectedGroup(snapshot, '15'));
    assert.equal(view.totals.arrived, 0);
    assert.equal(view.now.operators_online, 2);
    assert.equal(view.hourly[0].arrived, 0);
    assert.deepEqual(view.operators.map((r) => r.name), ['Яров']);
    assert.equal(view.day, '2026-09-17');
});

test('«Все» и исчезнувшая группа — снимок отдела как есть', () => {
    assert.equal(opGroupView(snapshot, opSelectedGroup(snapshot, OP_GROUP_ALL)), snapshot);
    assert.equal(opSelectedGroup(snapshot, '38'), null);
});

test('время входа: часы и минуты, вчерашнее — с пометкой', () => {
    assert.deepEqual(opEntryTime('2026-09-17T08:28:08', '2026-09-17'), { time: '08:28', previousDay: false });
    assert.deepEqual(opEntryTime('2026-09-16T19:53:44', '2026-09-17'), { time: '19:53', previousDay: true });
    assert.deepEqual(opEntryTime(null, '2026-09-17'), { time: '—', previousDay: false });
});
