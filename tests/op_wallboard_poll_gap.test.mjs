import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { snapshotTickDue } from '../src/components/monitoring/szovWallboardShared.js';

/* «Табло ОП» с живым потоком (27.10.2026): пока поток на связи, снимок перечитывается по его
   кадру refresh, а таймер — страховка не чаще раза в 30 с. Поток лёг — снова раз в 10 с. */

test('тик таймера: скрытая вкладка молчит, свежий снимок ждёт свой промежуток', () => {
    assert.equal(snapshotTickDue({ visible: false, now: 100000, lastFetchAt: 0 }), false);
    assert.equal(snapshotTickDue({ visible: true, now: 100000, lastFetchAt: 95000 }), true, 'без промежутка — каждый тик');
    assert.equal(snapshotTickDue({ visible: true, now: 100000, lastFetchAt: 95000, gapMs: 0 }), true);
    assert.equal(snapshotTickDue({ visible: true, now: 100000, lastFetchAt: 80000, gapMs: 30000 }), false);
    assert.equal(snapshotTickDue({ visible: true, now: 110000, lastFetchAt: 80000, gapMs: 30000 }), true);
    assert.equal(snapshotTickDue({ visible: true, now: 100000, lastFetchAt: 0, gapMs: 30000 }), true,
        'снимка не было (или не удался) — опрашиваем сразу');
});

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const shared = read('../src/components/monitoring/szovWallboardShared.js');
const op = read('../src/components/monitoring/opWallboardShared.js');

test('таймер снимка решает через snapshotTickDue — один на все табло', () => {
    const timer = shared.slice(shared.indexOf('store.timer = window.setInterval('), shared.indexOf('}, pollIntervalMs);'));
    assert.match(timer, /if \(!snapshotTickDue\(\{ visible: isTabVisible\(\), now: Date\.now\(\), lastFetchAt: store\.lastFetchAt,/);
    assert.match(timer, /gapMs: pollGapMs \? pollGapMs\(\) : 0 \}\)\) return;/);
});

test('промежуток включён только у «Табло ОП (Линия)» и только при живом потоке', () => {
    assert.match(op, /export const OP_LIVE_POLL_GAP_MS = 30000;/);
    assert.match(op, /pollGapMs: \(\) => \(liveStore\.state\.status === 'live' \? OP_LIVE_POLL_GAP_MS : 0\),/);
    assert.equal((op.match(/pollGapMs:/g) || []).length, 1, 'у «Чата» живого потока нет — опрос прежний');
    assert.doesNotMatch(shared, /= createSnapshotFeed\(\{[^}]*pollGapMs/, 'СЗоВ без промежутка');
    assert.doesNotMatch(read('../src/components/monitoring/tezWallboardShared.js'), /pollGapMs/, 'Тез КЦ без промежутка');
});

test('страховка не реже кэша лидов на сервере', () => {
    const routes = read('../op_wallboard/routes.py');
    const leadTtl = Number(routes.match(/lead_ttl_seconds=(\d+)/)[1]);
    const gap = Number(op.match(/export const OP_LIVE_POLL_GAP_MS = (\d+);/)[1]);
    assert.ok(gap / 1000 <= leadTtl, 'плитки лидов поток не будит — их подбирает только страховка');
});
