import test from 'node:test';
import assert from 'node:assert/strict';

import {
    applyOpLivePatch, opClockNow, opQueueTone, opQueueWait, opRowKey, opStatusElapsed, wallSeconds, withOpClock,
} from '../src/components/monitoring/opWallboardLive.js';

/* «Табло ОП» в реальном времени: кадр потока поверх снимка (op_wallboard/snapshot.py:live_patch). */

const snapshot = {
    captured_at: '2026-10-22T12:00:50',
    served_at: '2026-10-22T12:00:55',
    now: { operators_talking: 0, operators_free: 2, queue: 0, queue_since: null },
    operators: [
        { id: 1, ext: '6650', status_key: 'free', status_label: 'Активный', status_at: '2026-10-22 12:00:10', live: null, group_id: 36 },
        { id: null, ext: '6999', status_key: 'unknown', status_label: 'Нет в составе', live: null },
    ],
    groups: [{ id: 36, label: 'Основа', now: { operators_talking: 0, queue: 0 } }],
};

const patch = {
    at: '2026-10-22T12:01:00',
    online: true,
    operators: {
        1: { live: { status_key: 'talking', status_label: 'В разговоре', since: '2026-10-22T12:00:58' },
             status_key: 'free', status_label: 'Активный', status_at: '2026-10-22 12:00:10' },
        'ext:6999': { live: null, status_key: 'unknown' },
    },
    now: { operators_talking: 1, operators_free: 1, queue: 1, queue_since: '2026-10-22T12:00:40', queue_max_wait_seconds: 20 },
    groups_now: { 36: { operators_talking: 1, queue: 1 } },
};

test('кадр подставляется по ключам строк, «сейчас» и группы — целиком', () => {
    const merged = applyOpLivePatch(snapshot, patch, { snapshotReceivedMs: 1000, patchReceivedMs: 6000 });
    assert.equal(merged.operators[0].live.status_key, 'talking');
    assert.equal(merged.operators[0].status_key, 'free', 'статус телефона в строке остаётся');
    assert.equal(merged.now.queue, 1);
    assert.deepEqual(merged.groups[0].now, { operators_talking: 1, queue: 1 });
    assert.deepEqual(merged.live, { online: true, at: '2026-10-22T12:01:00' });
    assert.equal(snapshot.operators[0].live, null, 'исходный снимок не тронут');
    assert.deepEqual(merged.clock_anchor, { at: '2026-10-22T12:01:00', receivedMs: 6000 });
});

test('кадр старше снимка не применяется', () => {
    const old = { ...patch, at: '2026-10-22T12:00:30' };
    const merged = applyOpLivePatch(snapshot, old, { snapshotReceivedMs: 1000, patchReceivedMs: 6000 });
    assert.equal(merged.operators[0].live, null);
    assert.equal(merged.now.queue, 0);
    assert.deepEqual(merged.clock_anchor, { at: '2026-10-22T12:00:55', receivedMs: 1000 });
});

test('часы идут от отметки сервера, а не от часов браузера', () => {
    const merged = applyOpLivePatch(snapshot, patch, { snapshotReceivedMs: 1000, patchReceivedMs: 6000 });
    assert.equal(opClockNow(merged, 6000), wallSeconds('2026-10-22T12:01:00'));
    const ticked = withOpClock(merged, 9500);
    assert.equal(ticked.clock_now, wallSeconds('2026-10-22T12:01:03'));
    assert.equal(opStatusElapsed(ticked.operators[0], ticked), 5, 'разговор — от ответа на станции');
    assert.equal(opStatusElapsed({ status_at: '2026-10-22 12:00:10' }, ticked), 53, 'статус телефона — от события');
    assert.equal(opQueueWait(ticked), 23, 'ждут дольше всех — от входа в очередь');
});

test('без часов — то, что прислал сервер', () => {
    assert.equal(opStatusElapsed({ status_seconds: 40 }, {}), 40);
    assert.equal(opQueueWait({ now: { queue: 2, queue_max_wait_seconds: 31 } }), 31);
    assert.equal(opQueueWait({ now: { queue: 0 } }), null, 'никто не ждёт — прочерк');
    assert.equal(opQueueWait({ now: { queue: null } }), null);
});

test('цвет очереди как у табло СЗоВ', () => {
    assert.equal(opQueueTone({ queue: 0, operators_free: 3 }), 'good');
    assert.equal(opQueueTone({ queue: 2, operators_free: 1 }), 'warn');
    assert.equal(opQueueTone({ queue: 2, operators_free: 0 }), 'bad');
    assert.equal(opQueueTone({ queue: null }), 'neutral', 'коннектор не на связи — без оценки');
});

test('ключ строки совпадает с серверным', () => {
    assert.equal(opRowKey({ id: 7, ext: '6650' }), '7');
    assert.equal(opRowKey({ id: null, ext: '6999' }), 'ext:6999');
    assert.equal(wallSeconds('2026-10-22 12:00:10.123456'), wallSeconds('2026-10-22T12:00:10'));
    assert.equal(wallSeconds('мусор'), null);
});
