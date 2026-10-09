import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { isChatAccessQr } from '../src/components/wazzup/workspaceStatus.js';

// Execute the actual scanner callback: role decisions remain server-side.
const source = readFileSync(new URL('../src/components/qr/QrAccessView.jsx', import.meta.url), 'utf8');
const callback = source.match(/const catchCode = useCallback\(([\s\S]*?)\n    \}, \[apiBaseUrl, authHeaders, stopScanner\]\);/);
assert.ok(callback, 'scanner callback is available');

async function scan(data, token = 'OTPW:fixture') {
    const state = { candidate: null, granted: null };
    const requests = [];
    const values = {
        caughtRef: { current: false }, pendingTokenRef: { current: '' }, mountedRef: { current: true },
        apiBaseUrl: '/fixture', authHeaders: () => ({ headers: { Authorization: 'test' } }),
        stopScanner() {}, isChatAccessQr,
        axios: { post: async (...args) => { requests.push(args); return { data }; } },
    };
    for (const name of ['Failure', 'Candidate', 'AccessCode', 'CodeError', 'Checking', 'ResendAt', 'Granted']) {
        values[`set${name}`] = (value) => { state[name[0].toLowerCase() + name.slice(1)] = value; };
    }
    const run = new Function(...Object.keys(values), `return (${callback[1]}\n});`)(...Object.values(values));
    await run(token);
    assert.equal(state.checking, false);
    return { state, requests };
}

test('scan that grants chat access immediately shows success without a code candidate', async () => {
    const { state, requests } = await scan({ status: 'success', scope: 'wazzup_chats',
        operator_name: 'Верификатор', already_granted: true, granted_now: true });
    assert.deepEqual(state.granted, { name: 'Верификатор', chat: true });
    assert.equal(state.candidate, null);
    assert.equal(state.accessCode, '');
    assert.equal(requests.length, 1);
    assert.equal(requests[0][0], '/fixture/api/wazzup/workspace/scan');
});

test('supervisor scan keeps the challenge for Telegram code entry', async () => {
    const candidate = { status: 'success', scope: 'wazzup_chats', operator_name: 'Верификатор',
        challengeId: 'challenge', resendInSeconds: 60, already_granted: false };
    const { state } = await scan(candidate);
    assert.equal(state.granted, null);
    assert.deepEqual(state.candidate, candidate);
    assert.ok(state.resendAt > Date.now());
});

test('previously granted chat access retains the already-open notice', async () => {
    const candidate = { status: 'success', scope: 'wazzup_chats', already_granted: true };
    const { state } = await scan(candidate);
    assert.equal(state.granted, null);
    assert.deepEqual(state.candidate, candidate);
});

test('ordinary access QR still uses preview and requires confirmation', async () => {
    const candidate = { status: 'success', operator_name: 'Сотрудник' };
    const { state, requests } = await scan(candidate, 'OTPQ:fixture');
    assert.equal(state.granted, null);
    assert.deepEqual(state.candidate, candidate);
    assert.equal(requests[0][0], '/fixture/api/sensitive-access/qr/preview');
});
