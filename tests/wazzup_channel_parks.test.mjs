import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { channelInitials, chooseParkSpace, loadChannelParks, matchChannelPark } from '../src/components/wazzup/chatChannelParks.js';

test('park branding matches case, taxi suffixes, whole words and verified alternate spellings', () => {
    const parks = ['iTaxi', 'Jana такси', 'Ноль такси', 'Тенге Такси', '2dongelek', 'Честный', 'Global', 'Qazaq', 'Salam такси']
        .map((name, id) => ({ id, name }));
    for (const [channel, expected] of [
        ['iTaxi', 'iTaxi'], ['JANA TAXI', 'Jana такси'], ['Ноль Такси', 'Ноль такси'],
        ['TENGE TAXI', 'Тенге Такси'], ['Eki Dongelek ', '2dongelek'], ['chestniy_taxi', 'Честный'],
        ['global.taxi.kz', 'Global'], ['taxi_qazaq', 'Qazaq'], ['Salam Taxi 77000000000', 'Salam такси'],
    ]) assert.equal(matchChannelPark({ name: channel }, parks)?.name, expected, channel);
    assert.equal(channelInitials({ name: 'Ноль Такси' }), 'НО');
    assert.equal(channelInitials({}), 'К');
});

test('ambiguous, generic, absent and partial names never pick an unrelated logo', () => {
    const parks = [{ id: 1, name: 'iTaxi' }, { id: 2, name: 'iTaxi VIP' }, { id: 3, name: 'Jana Taxi' }];
    assert.equal(matchChannelPark({ name: 'iTaxi VIP' }, parks)?.id, 2);
    for (const name of ['Техподдержка iTaxi VIP', 'Jana Taxi / iTaxi', 'Taxi', 'SuperiTaxi', '77000000000', '']) {
        assert.equal(matchChannelPark({ name }, parks), null, name);
    }
    assert.equal(matchChannelPark({ name: 'iTaxi' }, [...parks, { id: 4, name: 'ITAXI' }]), null);
    assert.equal(matchChannelPark({ name: 'iTaxi' }, [{ ...parks[0], status: 'archived' }]), null);
});

test('space choice uses the taxi directory and never a guest-only or arbitrary first space', () => {
    const taxi = { id: 11, name: 'Таксопарки' }, other = { id: 12, name: 'Тез' };
    assert.equal(chooseParkSpace([other, taxi]), 11);
    assert.equal(chooseParkSpace([{ ...taxi, guest_only: true }]), null);
    assert.equal(chooseParkSpace([{ ...taxi, guest_only: true }, other]), 12);
    assert.equal(chooseParkSpace([other, { id: 13, name: 'Another' }]), null);
    assert.equal(chooseParkSpace([]), null);
});

test('one authenticated directory fetch preserves signed logos and frame while dropping contact details', async () => {
    const controller = new AbortController(), calls = [];
    const logo = 'https://images.invalid/logo.webp?signature=fixture';
    const frame = { ratio: 2, x: .2, y: .5, zoom: 1.5 };
    const parks = await loadChannelParks({ base: '/api/wiki', signal: controller.signal,
        headers: () => ({ Authorization: 'fixture' }), get: async (url, options) => {
            calls.push({ url, options });
            return url.endsWith('/ping') ? { data: { spaces: [{ id: 11, name: 'Таксопарки' }] } }
                : { data: { items: [{ id: 1, name: 'iTaxi', logo_url: logo, logo_frame: frame, phones: ['private'], offices: ['private'] }] } };
        } });
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[1].options.params, { space_id: 11 });
    assert.equal(calls[1].options.headers.Authorization, 'fixture');
    assert.equal(calls[1].options.signal, controller.signal);
    assert.deepEqual(parks, [{ id: 1, name: 'iTaxi', logo_url: logo, logo_frame: frame }]);
});

test('unauthorized or aborted directory requests cannot initiate a parks request', async () => {
    for (const abort of [false, true]) {
        const controller = new AbortController();
        let calls = 0;
        const run = loadChannelParks({ base: '/api/wiki', signal: controller.signal, get: async () => {
            calls += 1;
            if (!abort) throw Object.assign(new Error('denied'), { status: 403 });
            controller.abort();
            return { data: { spaces: [{ id: 11, name: 'Таксопарки' }] } };
        } });
        if (abort) assert.deepEqual(await run, []); else await assert.rejects(run, /denied/);
        assert.equal(calls, 1);
    }
});

const require = createRequire(import.meta.url);
const React = require('react');
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'WazzupChannelSidebar.mjs');
await build({ entryPoints: [join(process.cwd(), 'src/components/wazzup/ChatChannelsSidebar.jsx')],
    outfile: output, bundle: true, format: 'esm', platform: 'node', external: ['lucide-react'],
    plugins: [{ name: 'channel-sidebar-harness', setup(builder) {
        builder.onResolve({ filter: /^(react|axios)$/ }, ({ path }) => ({ path, namespace: 'fixture' }));
        builder.onLoad({ filter: /.*/, namespace: 'fixture' }, ({ path }) => ({ loader: 'js', contents: path === 'axios'
            ? 'export default {get:(...args)=>globalThis.__sidebarHarness.get(...args)};'
            : `const h=()=>globalThis.__sidebarHarness;
                export const useState=(...v)=>h().useState(...v); export const useRef=(...v)=>h().useRef(...v);
                export const useEffect=(...v)=>h().useEffect(...v); export const useMemo=(...v)=>h().useMemo(...v);
                export default {createElement:(...v)=>h().createElement(...v)};` }));
    } }],
});
const { default: Sidebar } = await import(pathToFileURL(output));
const tick = () => new Promise((resolve) => setTimeout(resolve, 5));
const find = (node, predicate) => {
    if (!node || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const child of React.Children.toArray(node.props?.children)) { const found = find(child, predicate); if (found) return found; }
    return null;
};

test('collapse persists per operator/account and live chat rerenders do not refetch logos', async () => {
    const previous = globalThis.localStorage, storage = new Map(), calls = [], slots = [], selected = [];
    globalThis.localStorage = { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) };
    let index = 0, effects = [];
    const props = { user: { id: 1 }, apiBaseUrl: '/test', account: 'op', channels: [{ channelId: 'a', name: 'iTaxi' }],
        onSelect: (id) => selected.push(id), headers: () => ({ Authorization: 'fixture' }) };
    const harness = {
        createElement: React.createElement,
        useState(initial) { const i = index++; slots[i] ??= { value: typeof initial === 'function' ? initial() : initial };
            return [slots[i].value, (value) => { slots[i].value = value; }]; },
        useRef(initial) { const i = index++; slots[i] ??= { current: initial }; return slots[i]; },
        useMemo(fn, deps) { const i = index++; if (!slots[i] || !deps.every((v, k) => Object.is(v, slots[i].deps[k]))) slots[i] = { value: fn(), deps }; return slots[i].value; },
        useEffect(fn, deps) { const i = index++, old = slots[i]; if (old && deps.every((v, k) => Object.is(v, old.deps[k]))) return;
            slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); }); },
        async get(url, options) { calls.push({ url, options }); return url.endsWith('/ping')
            ? { data: { spaces: [{ id: 11, name: 'Таксопарки' }] } }
            : { data: { items: [{ id: 7, name: 'iTaxi', logo_url: '/api/wiki/file/fixture' }] } }; },
        render(next) { Object.assign(props, next); index = 0; const tree = Sidebar(props); const pending = effects; effects = []; pending.forEach((fn) => fn()); return tree; },
    };
    globalThis.__sidebarHarness = harness;
    try {
        let tree = harness.render(); await tick(); tree = harness.render();
        assert.equal(calls.length, 2);
        find(tree, (n) => n.props?.['aria-label'] === 'Свернуть каналы').props.onClick();
        tree = harness.render({ headers: () => ({ Authorization: 'updated' }) });
        assert.match(tree.props.className, /w-\[68px\]/);
        assert.equal(storage.get('wazzup:channels-collapsed:1:op'), '1');
        find(tree, (n) => n.type === 'button' && n.props?.title === 'iTaxi').props.onClick();
        assert.deepEqual(selected, ['a']); await tick(); assert.equal(calls.length, 2);
        tree = harness.render({ user: { id: 2 } });
        assert.match(tree.props.className, /w-56/);
        assert.equal(calls[0].options.signal.aborted, true);
        const avatar = find(tree, (n) => typeof n.type === 'function' && n.props?.channel?.channelId === 'a');
        assert.equal(avatar.props.park, null, 'previous operator logo must not survive context change');
        await tick(); assert.equal(calls.length, 4);
        assert.equal(calls[2].options.headers.Authorization, 'updated');
    } finally { slots.forEach((slot) => slot?.cleanup?.()); globalThis.localStorage = previous; delete globalThis.__sidebarHarness; }
});
