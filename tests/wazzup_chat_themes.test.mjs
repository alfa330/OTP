import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

// Chat themes of «Чаты ОП» (chatThemes.js): a personal choice kept per person in
// this browser; every theme paints with a complete set of variables. No network.
const require = createRequire(import.meta.url);
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzupChatThemes.mjs');
// The standard-only rule is checked on a made-up account put into the list at
// build time: the rule must hold whoever is listed today (the list may be empty).
const LIMITED = 4242;
const STANDARD_ONLY_LINE = /const STANDARD_ONLY = Object\.freeze\(\[[^\]]*\]\);/;
await build({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/chatThemes.js')],
    outfile: output, bundle: true, format: 'esm', platform: 'node', target: 'node18',
    plugins: [{ name: 'doubles', setup(builder) {
        builder.onLoad({ filter: /chatThemes\.js$/ }, ({ path }) => {
            const source = readFileSync(path, 'utf8');
            assert.match(source, STANDARD_ONLY_LINE, 'STANDARD_ONLY declaration not found');
            return { loader: 'js', contents: source.replace(STANDARD_ONLY_LINE,
                `const STANDARD_ONLY = Object.freeze([${LIMITED}]);`) };
        });
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

test('Rose stays only with its listed accounts and never switches on by itself', async () => {
    const storage = memory();
    let themes = await freshTab(storage);
    const rose = themes.chatThemeById('rose');
    assert.ok(rose.owners.length > 0 && rose.owners.every((owner) => Number.isInteger(owner) && owner > 0));
    assert.equal(themes.CHAT_THEMES.some((theme) => theme.id === 'rose'), false);
    const [owner] = rose.owners;
    const foreign = [7, 8, 9].find((candidate) => !rose.owners.includes(candidate));
    for (const id of [owner, String(owner)]) {
        assert.equal(themes.chatThemesFor(id).some((theme) => theme.id === 'rose'), true, String(id));
    }
    for (const id of [foreign, String(foreign), null, undefined, 0]) {
        assert.equal(themes.chatThemesFor(id).some((theme) => theme.id === 'rose'), false, String(id));
    }
    assert.equal(themes.useChatTheme(owner).selected, 'standard', 'nobody gets it without choosing');
    assert.equal(storage.map.size, 0, 'no one-time mark either');
    storage.setItem(`icore.wazzup.chatTheme.${owner}`, 'sand');
    themes = await freshTab(storage);
    assert.equal(themes.useChatTheme(owner).selected, 'sand', 'an own earlier choice is kept');
    themes.useChatTheme(owner).choose('rose');
    assert.equal((await freshTab(storage)).useChatTheme(owner).selected, 'rose', 'the choice survives a reload');
});

test('an account outside the Rose list loses it: a saved Rose falls back to the standard theme', async () => {
    const storage = memory();
    let themes = await freshTab(storage);
    const rose = themes.chatThemeById('rose');
    const foreign = [7, 8, 9].find((candidate) => !rose.owners.includes(candidate));
    storage.setItem(`icore.wazzup.chatTheme.${foreign}`, 'rose');
    themes = await freshTab(storage);
    assert.equal(themes.useChatTheme(foreign).selected, 'standard');
    assert.equal(themes.useChatTheme(foreign).themes.some((theme) => theme.id === 'rose'), false);
    themes.setChatTheme(foreign, 'rose');
    assert.equal(themes.useChatTheme(foreign).selected, 'standard');
    await themes.loadThemeLayer('rose');
    assert.equal(themes.useChatTheme(foreign).applied.id, 'standard');
});

test('a standard-only account gets no other theme: not in the menu, not from a saved value, not by choice', async () => {
    const storage = memory();
    storage.setItem(`icore.wazzup.chatTheme.${LIMITED}`, 'rose');
    storage.setItem(`icore.wazzup.chatTheme.personal.${LIMITED}`, 'rose');
    const themes = await freshTab(storage);
    for (const owner of [LIMITED, String(LIMITED)]) {
        assert.deepEqual(themes.chatThemesFor(owner).map((theme) => theme.id), ['standard'], String(owner));
    }
    const limited = themes.useChatTheme(LIMITED);
    assert.equal(limited.selected, 'standard', 'a theme saved earlier no longer applies');
    assert.deepEqual(limited.themes.map((theme) => theme.id), ['standard']);
    for (const id of ['rose', 'sky', 'night', 'cherry']) {
        themes.setChatTheme(LIMITED, id);
        assert.equal(themes.useChatTheme(LIMITED).selected, 'standard', id);
    }
    await themes.loadThemeLayer('rose');
    assert.equal(themes.useChatTheme(LIMITED).applied.id, 'standard');
    assert.equal(themes.wallpaperAllowed(LIMITED), false, 'and no wallpaper of their own');
    assert.equal(themes.wallpaperAllowed(7), true);
    assert.deepEqual(themes.useChatTheme(7).themes, themes.CHAT_THEMES, 'other accounts keep every shared theme');
});

test('storage refusing the one-time mark leaves the owner\'s own choice alone', async () => {
    const owner = (await freshTab(memory())).chatThemeById('cherry').owners[0];
    const storage = memory();
    storage.setItem(`icore.wazzup.chatTheme.${owner}`, 'sand');
    const setItem = storage.setItem;
    storage.setItem = (key, value) => {
        if (key.startsWith('icore.wazzup.chatTheme.personal.')) throw new Error('QuotaExceededError');
        setItem(key, value);
    };
    const themes = await freshTab(storage);
    themes.useChatTheme(owner);
    assert.equal(storage.map.get(`icore.wazzup.chatTheme.${owner}`), 'sand');
});

test('Rose, like Night, shows only after its stylesheet arrived', async () => {
    const themes = await freshTab(memory());
    const [owner] = themes.chatThemeById('rose').owners;
    const foreign = [7, 8, 9].find((candidate) => candidate !== owner);
    themes.setChatTheme(owner, 'rose');
    const before = themes.useChatTheme(owner);
    assert.equal(before.selected, 'rose');
    assert.equal(before.applied.id, 'standard');
    await themes.loadThemeLayer('rose');
    assert.equal(themes.useChatTheme(owner).applied.id, 'rose');
    assert.equal(themes.useChatTheme(foreign).applied.id, 'standard', 'another person keeps the standard window');
});

test('Cherry stays personal even when another account saves or requests its id', async () => {
    const storage = memory();
    const themes = await freshTab(storage);
    const cherry = themes.chatThemeById('cherry');
    assert.equal(cherry.id, 'cherry');
    assert.equal(cherry.owners.length, 1, 'the theme has one configured owner');
    assert.ok(cherry.owners.every((owner) => Number.isInteger(owner) && owner > 0));
    assert.equal(themes.CHAT_THEMES.some((theme) => theme.id === 'cherry'), false);
    for (const owner of cherry.owners) {
        assert.equal(themes.chatThemesFor(owner).some((theme) => theme.id === 'cherry'), true);
        assert.deepEqual(themes.chatThemesFor(String(owner)), themes.chatThemesFor(owner));
    }
    const foreign = [7, 8, 9].find((owner) => !cherry.owners.includes(owner));
    storage.setItem(`icore.wazzup.chatTheme.${foreign}`, 'cherry');
    storage.setItem(`icore.wazzup.chatTheme.personal.${foreign}`, 'cherry');
    for (const owner of [foreign, null, undefined, 0]) {
        assert.equal(themes.chatThemesFor(owner).some((theme) => theme.id === 'cherry'), false);
    }
    assert.equal(themes.useChatTheme(foreign).selected, 'standard');
    themes.setChatTheme(foreign, 'cherry');
    assert.equal(themes.useChatTheme(foreign).selected, 'standard');
});

test('Cherry is the initial personal choice once and respects later choices across reloads', async () => {
    const storage = memory();
    let themes = await freshTab(storage);
    const owner = themes.chatThemeById('cherry').owners[0];
    const foreign = [7, 8, 9].find((candidate) => candidate !== owner);
    storage.setItem(`icore.wazzup.chatTheme.${owner}`, 'sand');
    themes.setChatTheme(foreign, 'lavender');
    assert.equal(themes.useChatTheme(owner).selected, 'cherry');
    themes.useChatTheme(owner).choose('sky');
    themes = await freshTab(storage);
    assert.equal(themes.useChatTheme(owner).selected, 'sky');
    themes.useChatTheme(owner).choose('standard');
    themes = await freshTab(storage);
    assert.equal(themes.useChatTheme(owner).selected, 'standard', 'the personal default does not return');
    themes.useChatTheme(owner).choose('cherry');
    themes = await freshTab(storage);
    assert.equal(themes.useChatTheme(owner).selected, 'cherry');
    assert.equal(themes.useChatTheme(foreign).selected, 'lavender', 'another account retains its own preference');
});

test('Cherry is applied only after its own stylesheet loads', async () => {
    const themes = await freshTab(memory());
    const owner = themes.chatThemeById('cherry').owners[0];
    assert.equal(themes.useChatTheme(owner).selected, 'cherry');
    assert.equal(themes.useChatTheme(owner).applied.id, 'standard');
    await themes.loadThemeLayer('rose');
    assert.equal(themes.useChatTheme(owner).applied.id, 'standard', 'another layer cannot enable Cherry');
    const loading = themes.loadThemeLayer('cherry');
    assert.strictEqual(themes.loadThemeLayer('cherry'), loading, 'concurrent requests share a stylesheet load');
    await loading;
    assert.equal(themes.useChatTheme(owner).applied.id, 'cherry');
});

test('five shared themes besides the standard one, each with every colour the stylesheet reads', async () => {
    const themes = await freshTab(memory());
    assert.deepEqual(themes.CHAT_THEMES.map((theme) => theme.id),
        ['standard', 'sky', 'sand', 'lavender', 'graphite', 'night']);
    assert.equal(new Set(themes.CHAT_THEMES.map((theme) => theme.name)).size, 6);
    assert.deepEqual(themes.chatThemeStyle(themes.chatThemeById('standard')), {});
    const available = [...themes.CHAT_THEMES, themes.chatThemeById('rose'), themes.chatThemeById('cherry')];
    for (const theme of available.filter((item) => item.id !== 'standard')) {
        const style = themes.chatThemeStyle(theme);
        assert.equal(Object.keys(style).length, 11, theme.id);
        for (const [name, value] of Object.entries(style)) assert.ok(value, `${theme.id}: ${name}`);
    }
});
