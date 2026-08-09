import test from 'node:test';
import assert from 'node:assert/strict';

import { createCoalescedReload } from '../src/components/notifications/coalescedReload.js';

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};

test('overlapping reload ставит ровно один повтор', async () => {
  const firstRequest = deferred();
  let calls = 0;
  const reload = createCoalescedReload(async () => {
    calls += 1;
    if (calls === 1) await firstRequest.promise;
  });

  const active = reload();
  const overlapping = reload();

  assert.strictEqual(overlapping, active);
  assert.equal(calls, 1);
  firstRequest.resolve();
  await active;
  assert.equal(calls, 2);
});

test('несколько overlapping-сигналов склеиваются в один повтор', async () => {
  const firstRequest = deferred();
  let calls = 0;
  const reload = createCoalescedReload(async () => {
    calls += 1;
    if (calls === 1) await firstRequest.promise;
  });

  const active = reload();
  reload();
  reload();
  reload();

  firstRequest.resolve();
  await active;
  assert.equal(calls, 2);
});

test('после ошибки gate освобождается для следующего reload', async () => {
  let calls = 0;
  let fail = true;
  const reload = createCoalescedReload(async () => {
    calls += 1;
    if (fail) throw new Error('snapshot failed');
  });

  await assert.rejects(reload(), /snapshot failed/);
  fail = false;
  await reload();
  assert.equal(calls, 2);
});
