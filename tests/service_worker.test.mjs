import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8');
const origin = 'https://alfa330.github.io';
const moduleUrl = origin + '/OTP/assets/ChatAttachmentViewer-old.js';
const javascript = (text = 'export default {}') => new Response(text, {
    headers: { 'content-type': 'text/javascript; charset=utf-8' },
});
const html = (text) => new Response(text, { headers: { 'content-type': 'text/html' } });

const worker = ({ fetchImpl = async () => javascript(), brokenStorage = false } = {}) => {
    const handlers = new Map();
    const stores = new Map();
    const calls = [];
    let claimed = false;
    let skipped = false;
    const keyFor = (request) => new URL(typeof request === 'string' ? request : request.url, origin).href;
    const storageCheck = () => { if (brokenStorage) throw new Error('CacheStorage unavailable'); };
    const open = async (name) => {
        storageCheck();
        if (!stores.has(name)) stores.set(name, new Map());
        const store = stores.get(name);
        return {
            match: async (request) => store.get(keyFor(request))?.clone(),
            put: async (request, response) => { store.set(keyFor(request), response.clone()); },
            delete: async (request) => store.delete(keyFor(request)),
            keys: async () => [...store.keys()],
        };
    };
    vm.runInNewContext(source, {
        URL,
        setTimeout,
        clearTimeout,
        fetch: async (request, options) => {
            calls.push({ request, options });
            return fetchImpl(request, options);
        },
        caches: {
            open,
            match: async (request, { cacheName } = {}) => {
                storageCheck();
                if (cacheName) return stores.get(cacheName)?.get(keyFor(request))?.clone();
                for (const store of stores.values()) {
                    if (store.has(keyFor(request))) return store.get(keyFor(request)).clone();
                }
            },
            keys: async () => { storageCheck(); return [...stores.keys()]; },
            delete: async (name) => { storageCheck(); return stores.delete(name); },
        },
        self: {
            registration: { scope: origin + '/OTP/' },
            location: { origin },
            addEventListener: (type, callback) => handlers.set(type, callback),
            skipWaiting: async () => { skipped = true; },
            clients: { claim: async () => { claimed = true; } },
        },
    });
    const dispatch = async (type, request) => {
        const work = [];
        let response;
        handlers.get(type)({
            request,
            waitUntil: (promise) => work.push(promise),
            respondWith: (promise) => { response = promise; },
        });
        const result = await response;
        // Fetch handlers can enqueue cache writes after their network response.
        for (let index = 0; index < work.length; index += 1) await work[index];
        return result;
    };
    return {
        calls,
        stores,
        dispatch,
        put: async (cache, request, response) => (await open(cache)).put(request, response),
        fetch: (url = moduleUrl, properties = {}) => dispatch('fetch', {
            url, method: 'GET', mode: 'cors', headers: new Headers(), cache: 'default', ...properties,
        }),
        get claimed() { return claimed; },
        get skipped() { return skipped; },
    };
};

test('navigation bypasses HTTP cache and replaces the cached shell', async () => {
    const sw = worker({ fetchImpl: async () => html('current build') });
    await sw.put('icore-shell-v2', '/OTP/', html('old build'));
    const response = await sw.fetch(origin + '/OTP/?view=wazzup_chats', { mode: 'navigate' });
    assert.equal(await response.text(), 'current build');
    assert.equal(sw.calls[0].options.cache, 'no-store');
    assert.equal(await sw.stores.get('icore-shell-v2').get(origin + '/OTP/').text(), 'current build');
});

test('offline navigation uses only the current shell cache', async () => {
    const sw = worker({ fetchImpl: async () => { throw new TypeError('offline'); } });
    await sw.put('icore-shell-v1', '/OTP/', html('obsolete build'));
    await sw.put('icore-shell-v2', '/OTP/', html('current offline build'));
    const response = await sw.fetch(origin + '/OTP/?view=wazzup_chats', { mode: 'navigate' });
    assert.equal(await response.text(), 'current offline build');
});

test('previously loaded chunks remain available after a deployment', async () => {
    const sw = worker({ fetchImpl: async () => new Response('removed', { status: 404 }) });
    await sw.put('icore-assets-v1', moduleUrl, javascript('old but valid module'));
    const response = await sw.fetch();
    assert.equal(await response.text(), 'old but valid module');
    assert.equal(sw.calls.length, 0);
});

test('HTML stored under a JavaScript URL is replaced from the network', async () => {
    const sw = worker();
    await sw.put('icore-assets-v1', moduleUrl, html('proxy error'));
    assert.equal(await (await sw.fetch()).text(), 'export default {}');
    assert.equal(sw.calls[0].options.cache, 'no-store');
    assert.equal(sw.stores.get('icore-assets-v1').get(moduleUrl).headers.get('content-type'), 'text/javascript; charset=utf-8');
});

test('HTML and failed responses cannot poison a missing JavaScript chunk', async () => {
    for (const response of [html('proxy error'), new Response('missing', { status: 404 })]) {
        const sw = worker({ fetchImpl: async () => response.clone() });
        await sw.fetch();
        await sw.fetch();
        assert.equal(sw.calls.length, 2);
        assert.equal(sw.stores.get('icore-assets-v1')?.has(moduleUrl) ?? false, false);
    }
});

test('CSS MIME is validated before using cached content', async () => {
    const url = origin + '/OTP/assets/main-old.css';
    const sw = worker({ fetchImpl: async () => new Response('body {}', { headers: { 'content-type': 'text/css' } }) });
    await sw.put('icore-assets-v1', url, html('proxy error'));
    assert.equal(await (await sw.fetch(url)).text(), 'body {}');
    assert.equal(sw.calls.length, 1);
});

test('an explicit refresh bypasses CacheStorage', async () => {
    for (const cache of ['reload', 'no-cache', 'no-store']) {
        const sw = worker({ fetchImpl: async () => javascript('fresh module') });
        await sw.put('icore-assets-v1', moduleUrl, javascript('stored module'));
        assert.equal(await (await sw.fetch(moduleUrl, { cache })).text(), 'fresh module');
        assert.equal(sw.calls.length, 1);
        assert.equal(sw.calls[0].request.cache, cache);
    }
});

test('unavailable CacheStorage does not break scripts, public assets, or navigation', async () => {
    const sw = worker({ brokenStorage: true });
    for (const [url, properties] of [
        [moduleUrl, {}],
        [origin + '/OTP/favicon.ico', {}],
        [origin + '/OTP/', { mode: 'navigate' }],
    ]) {
        assert.equal((await sw.fetch(url, properties)).status, 200);
    }
    assert.equal(sw.calls.length, 3);
});

test('a cache write failure does not reject an otherwise successful script response', async () => {
    const sw = worker();
    await sw.put('icore-assets-v1', moduleUrl, javascript());
    // Simulate a quota failure while preserving functioning cache reads.
    sw.stores.get('icore-assets-v1').set = () => { throw new Error('QuotaExceededError'); };
    assert.equal((await sw.fetch(moduleUrl, { cache: 'reload' })).status, 200);
});

test('worker installation and activation complete when storage is blocked', async () => {
    const sw = worker({ brokenStorage: true });
    await sw.dispatch('install');
    await sw.dispatch('activate');
    assert.equal(sw.skipped, true);
    assert.equal(sw.claimed, true);
});

test('activation drops the obsolete shell but preserves immutable chunks and foreign caches', async () => {
    const sw = worker();
    await sw.put('icore-shell-v1', '/OTP/', html('obsolete build'));
    await sw.put('icore-shell-v2', '/OTP/', html('current build'));
    await sw.put('icore-assets-v1', moduleUrl, javascript());
    await sw.put('another-application', '/elsewhere/', html('another application'));
    await sw.dispatch('activate');
    assert.deepEqual([...sw.stores.keys()], ['icore-shell-v2', 'icore-assets-v1', 'another-application']);
});

test('API, foreign, and ranged requests bypass the worker', async () => {
    const sw = worker();
    for (const [url, properties] of [
        [origin + '/OTP/api/chats', {}],
        ['https://api.example.com/OTP/assets/data.js', {}],
        [moduleUrl, { method: 'POST' }],
        [moduleUrl, { headers: new Headers({ range: 'bytes=0-99' }) }],
    ]) {
        assert.equal(await sw.fetch(url, properties), undefined);
    }
    assert.equal(sw.calls.length, 0);
});
