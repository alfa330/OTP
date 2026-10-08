import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../src/components/wazzup/workspaceStore.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replaceAll('export const ', 'const ');

function fixture() {
    const calls = [];
    const store = new Function('useSyncExternalStore', 'axios', 'newClientEventId', source + `
        return { configureWorkspace, resetWorkspace, loadWorkspace, beatWorkspace,
            setWorkspaceStatus, useWorkspace };`)(
        (_, snapshot) => snapshot(),
        (options) => new Promise((resolve, reject) => calls.push({ options, resolve, reject })),
        () => 'test-click');
    const configure = (ownerId) => store.configureWorkspace({
        apiBaseUrl: '/fixture', ownerId, headers: () => ({ Authorization: `user-${ownerId}` }),
    });
    configure(1);
    const ready = async () => {
        const loading = store.loadWorkspace();
        calls.at(-1).resolve({ data: { locked: false, shift: {
            current: { key: 'work', onShift: true }, startKey: 'work', logoutKey: 'off',
        } } });
        await loading;
    };
    return { ...store, calls, configure, ready, get state() { return store.useWorkspace(1); } };
}

test('late heartbeat and load cannot overwrite a newly selected status', async () => {
    const h = fixture();
    await h.ready();
    const beat = h.beatWorkspace();
    const load = h.loadWorkspace();
    const change = h.setWorkspaceStatus('break');
    const count = h.calls.length;
    await h.beatWorkspace();
    await h.loadWorkspace();
    assert.equal(h.calls.length, count, 'no old-state requests during a status mutation');
    h.calls[3].resolve({ data: { current: { key: 'break' } } });
    assert.equal(await change, true);
    h.calls[1].resolve({ data: { current: { key: 'work' } } });
    h.calls[2].resolve({ data: { locked: true } });
    await Promise.all([beat, load]);
    assert.equal(h.state.current.key, 'break');
    assert.equal(h.state.locked, false);
});

test('requests coalesce and recover after repeated connection failures', async () => {
    const h = fixture();
    for (let attempt = 0; attempt < 3; attempt++) {
        const loading = h.loadWorkspace();
        await h.loadWorkspace();
        assert.equal(h.calls.length, attempt + 1);
        h.calls.at(-1).reject(new Error('offline'));
        await loading;
        assert.equal(h.state.ready, false);
        assert.ok(h.state.error);
    }
    await h.ready();
    assert.equal(h.state.ready, true);
    const beat = h.beatWorkspace();
    const count = h.calls.length;
    await h.beatWorkspace();
    assert.equal(h.calls.length, count);
    h.calls.at(-1).resolve({ data: { current: { key: 'work' } } });
    await beat;
});

test('changing owner hides the previous workspace and ignores late responses', async () => {
    const h = fixture();
    await h.ready();
    assert.equal(h.useWorkspace(2).ready, false);
    const pending = h.setWorkspaceStatus('break');
    h.configure(2);
    const loading = h.loadWorkspace();
    h.calls[1].resolve({ data: { current: { key: 'break' } } });
    assert.equal(await pending, false);
    assert.equal(h.useWorkspace(2).ready, false);
    h.calls[2].resolve({ data: { locked: true } });
    await loading;
    assert.equal(h.useWorkspace(2).locked, true);
    assert.equal(h.useWorkspace(2).current, null);
    assert.equal(h.calls[2].options.headers.Authorization, 'user-2');
    h.resetWorkspace();
    await h.beatWorkspace();
    await h.loadWorkspace();
    assert.equal(h.calls.length, 3, 'logout clears the configured transport');
});

test('revoked access closes the workspace, and locked status changes send nothing', async () => {
    const h = fixture();
    await h.ready();
    const beat = h.beatWorkspace();
    h.calls.at(-1).reject({ response: { status: 403 } });
    await beat;
    assert.equal(h.state.locked, true);
    assert.equal(h.state.current, null);
    assert.equal(await h.setWorkspaceStatus('work'), false);
    assert.equal(h.calls.length, 2);
});
