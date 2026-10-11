import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

// Chat themes of «Чаты ОП» (chatThemes.js): a personal choice kept per person in
// this browser; every theme paints with a complete set of variables. No network.
const require = createRequire(import.meta.url);
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzupChatThemes.mjs');
await build({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/chatThemes.js')],
    outfile: output, bundle: true, format: 'esm', platform: 'node', target: 'node18',
    plugins: [{ name: 'doubles', setup(builder) {
        builder.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'doubles' }));
        builder.onResolve({ filter: /\.css$/ }, () => ({ path: 'css', namespace: 'doubles' }));
        builder.onLoad({ filter: /.*/, namespace: 'doubles' }, ({ path }) => ({ loader: 'js', contents: path === 'react'
            ? 'export const useSyncExternalStore=(subscribe,get)=>{subscribe(()=>{});return get();}; export const useEffect=()=>{}; export const useState=(v)=>[v,()=>{}];'
            : 'export default {};' }));
    } }],
});

const memory = () => {
    const map = new Map();
    return { map, getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, String(v)),
        removeItem: (k) => map.delete(k) };
};
let tab = 0;
const freshTab = async (storage) => {
    globalThis.localStorage = storage;
    const listeners = new Map();
    globalThis.addEventListener = (type, listener) => listeners.set(type, listener);
    tab += 1;
    const module = await import(`${pathToFileURL(output).href}?tab=${tab}`);
    return { ...module, fireStorage: (key) => listeners.get('storage')?.({ key }) };
};

test('the theme is personal: another person in the same browser keeps their own', async () => {
    const storage = memory();
    const themes = await freshTab(storage);
    assert.equal(themes.useChatTheme(7).selected, 'standard');
    themes.useChatTheme(7).choose('lavender');
    assert.equal(themes.useChatTheme(7).applied.id, 'lavender');
    assert.equal(themes.useChatTheme(8).selected, 'standard');
    const reloaded = await freshTab(storage);
    assert.equal(reloaded.useChatTheme(7).selected, 'lavender', 'survives a reload');
    reloaded.useChatTheme(7).choose('standard');
    assert.equal(storage.map.size, 0, 'the standard theme leaves nothing behind');
});

test('an unknown or foreign value falls back to the standard theme; nobody — no choice', async () => {
    const storage = memory();
    storage.setItem('icore.wazzup.chatTheme.7', 'neon');
    const themes = await freshTab(storage);
    assert.equal(themes.useChatTheme(7).selected, 'standard');
    themes.setChatTheme(7, 'neon');
    assert.equal(themes.useChatTheme(7).selected, 'standard');
    themes.setChatTheme(null, 'sky');
    assert.equal(themes.useChatTheme(null).selected, 'standard');
});

test('another tab of the person changes the theme here too', async () => {
    const storage = memory();
    const themes = await freshTab(storage);
    assert.equal(themes.useChatTheme(7).selected, 'standard');
    storage.setItem('icore.wazzup.chatTheme.7', 'sand');
    themes.fireStorage('icore.wazzup.chatTheme.7');
    assert.equal(themes.useChatTheme(7).selected, 'sand');
});

test('Night shows only after its stylesheet arrived: until then the window keeps the previous look', async () => {
    const themes = await freshTab(memory());
    themes.setChatTheme(7, 'night');
    const before = themes.useChatTheme(7);
    assert.equal(before.selected, 'night', 'the menu already marks the choice');
    assert.equal(before.applied.id, 'standard');
    await themes.loadNightTheme();
    assert.equal(themes.useChatTheme(7).applied.id, 'night');
});

test('a personal theme belongs to its owners only: not in the menu, not from a saved value, not by choice', async () => {
    const storage = memory();
    storage.setItem('icore.wazzup.chatTheme.7', 'rose');
    storage.setItem('icore.wazzup.chatTheme.personal.7', 'rose');
    const themes = await freshTab(storage);
    assert.deepEqual(themes.chatThemesFor(241).map((theme) => theme.id),
        ['standard', 'sky', 'sand', 'lavender', 'graphite', 'rose', 'night']);
    assert.deepEqual(themes.chatThemesFor('241').map((theme) => theme.id), themes.chatThemesFor(241).map((theme) => theme.id));
    for (const owner of [7, null, undefined, 0]) {
        assert.equal(themes.chatThemesFor(owner).some((theme) => theme.id === 'rose'), false, String(owner));
    }
    assert.equal(themes.CHAT_THEMES.some((theme) => theme.owners), false, 'the shared list has no personal themes');
    const other = themes.useChatTheme(7);
    assert.equal(other.selected, 'standard', 'a saved value does not give the theme');
    assert.equal(other.themes.some((theme) => theme.id === 'rose'), false);
    themes.setChatTheme(7, 'rose');
    assert.equal(themes.useChatTheme(7).selected, 'standard', 'nor does choosing it');
});

test('the personal theme turns on by itself once; afterwards the owner chooses', async () => {
    const storage = memory();
    const themes = await freshTab(storage);
    assert.equal(themes.useChatTheme(241).selected, 'rose');
    themes.useChatTheme(241).choose('sky');
    let reloaded = await freshTab(storage);
    assert.equal(reloaded.useChatTheme(241).selected, 'sky', 'the choice survives a reload');
    reloaded.useChatTheme(241).choose('standard');
    reloaded = await freshTab(storage);
    assert.equal(reloaded.useChatTheme(241).selected, 'standard', 'the standard one too — the personal does not come back');
    reloaded.useChatTheme(241).choose('rose');
    assert.equal((await freshTab(storage)).useChatTheme(241).selected, 'rose');
});

test('an owner who had picked a theme before still gets the personal one once', async () => {
    const storage = memory();
    storage.setItem('icore.wazzup.chatTheme.241', 'sand');
    const themes = await freshTab(storage);
    assert.equal(themes.useChatTheme(241).selected, 'rose');
    themes.useChatTheme(241).choose('sand');
    assert.equal((await freshTab(storage)).useChatTheme(241).selected, 'sand');
});

test('storage refusing the one-time mark leaves the owner\'s own choice alone', async () => {
    const storage = memory();
    storage.setItem('icore.wazzup.chatTheme.241', 'sand');
    const setItem = storage.setItem;
    storage.setItem = (key, value) => {
        if (key.startsWith('icore.wazzup.chatTheme.personal.')) throw new Error('QuotaExceededError');
        setItem(key, value);
    };
    const themes = await freshTab(storage);
    themes.useChatTheme(241);
    assert.equal(storage.map.get('icore.wazzup.chatTheme.241'), 'sand');
});

test('Rose, like Night, shows only after its stylesheet arrived', async () => {
    const themes = await freshTab(memory());
    const before = themes.useChatTheme(241);
    assert.equal(before.selected, 'rose');
    assert.equal(before.applied.id, 'standard');
    await themes.loadThemeLayer('rose');
    assert.equal(themes.useChatTheme(241).applied.id, 'rose');
    assert.equal(themes.useChatTheme(7).applied.id, 'standard', 'another person keeps the standard window');
});

test('five themes besides the standard one, each with every colour the stylesheet reads', async () => {
    const themes = await freshTab(memory());
    assert.equal(themes.CHAT_THEMES.length, 6);
    assert.equal(new Set(themes.CHAT_THEMES.map((theme) => theme.name)).size, 6);
    assert.deepEqual(themes.chatThemeStyle(themes.chatThemeById('standard')), {});
    for (const theme of themes.chatThemesFor(241).filter((item) => item.id !== 'standard')) {
        const style = themes.chatThemeStyle(theme);
        assert.equal(Object.keys(style).length, 11, theme.id);
        for (const [name, value] of Object.entries(style)) assert.ok(value, `${theme.id}: ${name}`);
    }
});
