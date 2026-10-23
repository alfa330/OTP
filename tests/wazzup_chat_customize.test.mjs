import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

// «Кастомизация» «Чатов ОП»: хранилище обоев (chatWallpaper.js), слой обоев,
// превью окна. Сеть подменена: axios — запись вызовов и заготовленные ответы.
const require = createRequire(import.meta.url);
const { build } = require('esbuild');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });

const doubles = { name: 'doubles', setup(builder) {
    builder.onResolve({ filter: /^axios$/ }, () => ({ path: 'axios', namespace: 'doubles' }));
    builder.onResolve({ filter: /\.css$/ }, () => ({ path: 'css', namespace: 'doubles' }));
    builder.onLoad({ filter: /.*/, namespace: 'doubles' }, ({ path }) => ({ loader: 'js', contents: path === 'axios'
        ? `const call = (method) => (url, ...rest) => globalThis.__axios(method, url, ...rest);
           export default { get: call('get'), post: call('post'), put: call('put'), delete: call('delete') };`
        : 'export default {};' }));
} };

// Хранилище читается вне компонента: хуки — заглушки, снимок — прямо из стора.
const hooks = { name: 'hooks', setup(builder) {
    builder.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'hooks' }));
    builder.onLoad({ filter: /.*/, namespace: 'hooks' }, () => ({ loader: 'js',
        contents: 'export const useSyncExternalStore=(subscribe,get)=>{globalThis.__subscribe=subscribe;return get();};'
            + ' export const useEffect=()=>{};' }));
} };
const storeFile = join(cache, 'wazzupChatWallpaper.mjs');
await build({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/chatWallpaper.js')],
    outfile: storeFile, bundle: true, format: 'esm', platform: 'node', target: 'node18',
    plugins: [hooks, doubles],
});
const sheetFile = join(cache, 'wazzupChatCustomizeSheet.mjs');
await build({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/ChatCustomizeSheet.jsx')],
    outfile: sheetFile, bundle: true, format: 'esm', platform: 'node', target: 'node18', jsx: 'transform',
    external: ['react', 'react-dom', 'lucide-react'], plugins: [doubles],
});
const layerFile = join(cache, 'wazzupChatWallpaperLayer.mjs');
await build({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/ChatWallpaperLayer.jsx')],
    outfile: layerFile, bundle: true, format: 'esm', platform: 'node', target: 'node18', jsx: 'transform',
    external: ['react'],
});
const themesFile = join(cache, 'wazzupChatThemesForCustomize.mjs');
await build({
    entryPoints: [join(process.cwd(), 'src/components/wazzup/chatThemes.js')],
    outfile: themesFile, bundle: true, format: 'esm', platform: 'node', target: 'node18',
    external: ['react'], plugins: [doubles],
});
const { default: ChatWallpaperLayer, wallpaperFilter, wallpaperVeil } = await import(pathToFileURL(layerFile).href);
const { PreviewPane, movedPosition, previewOverflow } = await import(pathToFileURL(sheetFile).href);
const { wallpaperAllowed, chatThemesFor } = await import(pathToFileURL(themesFile).href);

const memory = () => {
    const map = new Map();
    return { map, getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, String(v)),
        removeItem: (k) => map.delete(k) };
};

/* Сеть вкладки: каждый вызов axios ждёт, пока тест его не отпустит. */
function network() {
    const calls = [];
    globalThis.__axios = (method, url, ...rest) => new Promise((resolve, reject) => {
        calls.push({ method, url, rest, resolve, reject });
    });
    const find = (method, tail) => calls.find((call) => !call.done && call.method === method && call.url.endsWith(tail));
    return {
        calls,
        pending: (method, tail) => Boolean(find(method, tail)),
        answer(method, tail, data, status = 200) {
            const call = find(method, tail);
            assert.ok(call, `нет запроса ${method} …${tail}; были: ${calls.map((c) => `${c.method} ${c.url}`).join(', ')}`);
            call.done = true;
            if (status >= 400) {
                const error = new Error(`status ${status}`);
                error.response = { status, data };
                call.reject(error);
            } else call.resolve({ data, status });
            return call;
        },
    };
}
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

let tab = 0;
async function freshTab(storage = memory()) {
    globalThis.localStorage = storage;
    tab += 1;
    const store = await import(`${pathToFileURL(storeFile).href}?tab=${tab}`);
    return { store, storage };
}
const revoked = [];
URL.revokeObjectURL = (url) => revoked.push(url);
const blob = (text = 'x') => new Blob([text], { type: 'image/webp' });
const WALL = { id: '11111111-2222-4333-8444-555555555555', width: 1600, height: 1000, updatedAt: '2026-10-23T09:00:00+00:00' };
const API = { apiBaseUrl: 'https://api.example', owner: 7, headers: () => ({ Authorization: 'Bearer test' }) };

test('a remembered wallpaper starts loading at once, before the server says which it is', async () => {
    const storage = memory();
    storage.setItem('icore.wazzup.chatWallpaper.7', JSON.stringify({ id: WALL.id, width: 1600, height: 1000, settings: { blur: 4 } }));
    const net = network();
    const { store } = await freshTab(storage);
    store.configureChatWallpaper(API);
    store.loadChatWallpaper();
    assert.ok(net.pending('get', `/api/wazzup/appearance/wallpaper/${WALL.id}`), 'картинка запрошена сразу');
    const image = net.calls.find((call) => call.url.includes('/wallpaper/'));
    assert.equal(image.rest[0].responseType, 'blob');
    assert.deepEqual(image.rest[0].headers, { Authorization: 'Bearer test' }, 'картинка под входом — с заголовками');
    net.answer('get', `/wallpaper/${WALL.id}`, blob());
    await settle();
    let state = store.useChatWallpaper(7, API);
    assert.match(state.url, /^blob:/);
    assert.equal(state.settings.blur, 4);
    assert.equal(state.ready, false, 'пока сервер не ответил — показываем то, что помнили');
    net.answer('get', '/api/wazzup/appearance', { wallpaper: WALL, settings: { brightness: -20 }, mayUpload: true });
    await settle();
    state = store.useChatWallpaper(7, API);
    assert.equal(state.ready, true);
    assert.equal(state.settings.brightness, -20);
    assert.equal(state.settings.blur, 0, 'вид — с сервера, а не из памяти браузера');
    assert.equal(JSON.parse(storage.getItem('icore.wazzup.chatWallpaper.7')).settings.brightness, -20);
    assert.equal(net.calls.filter((call) => call.url.includes('/wallpaper/')).length, 1, 'та же картинка второй раз не качается');
});

test('the same wallpaper again keeps its picture on screen; a late picture of replaced wallpaper is not shown', async () => {
    const net = network();
    const { store } = await freshTab();
    store.configureChatWallpaper(API);
    store.loadChatWallpaper();
    net.answer('get', '/api/wazzup/appearance', { wallpaper: WALL, settings: {}, mayUpload: true });
    await settle();
    net.answer('get', `/wallpaper/${WALL.id}`, blob());
    await settle();
    const shown = store.useChatWallpaper(7, API).url;
    const frames = [];
    const unsubscribe = globalThis.__subscribe(() => frames.push(store.useChatWallpaper(7, API).url));
    store.loadChatWallpaper();
    net.answer('get', '/api/wazzup/appearance', { wallpaper: WALL, settings: { blur: 2 }, mayUpload: true });
    await settle();
    unsubscribe();
    assert.ok(frames.length > 0);
    assert.deepEqual([...new Set(frames)], [shown], 'обои не мигают пустым кадром при каждом ответе');
    assert.equal(store.useChatWallpaper(7, API).settings.blur, 2);
    // Картинка новых обоев ещё грузится, а обои уже сменили ещё раз.
    const next = { ...WALL, id: 'cccccccc-2222-4333-8444-555555555555' };
    const last = { ...WALL, id: 'dddddddd-2222-4333-8444-555555555555' };
    store.loadChatWallpaper();
    net.answer('get', '/api/wazzup/appearance', { wallpaper: next, settings: {}, mayUpload: true });
    await settle();
    store.loadChatWallpaper();
    net.answer('get', '/api/wazzup/appearance', { wallpaper: last, settings: {}, mayUpload: true });
    await settle();
    net.answer('get', `/wallpaper/${next.id}`, blob('late'));
    await settle();
    const state = store.useChatWallpaper(7, API);
    assert.equal(state.wallpaper.id, last.id);
    assert.equal(state.url, '', 'картинка сменённых обоев не встаёт под новые');
});

test('no wallpaper on the server clears the remembered one; nobody — nothing is asked', async () => {
    const storage = memory();
    storage.setItem('icore.wazzup.chatWallpaper.7', JSON.stringify({ id: WALL.id, width: 1, height: 1, settings: {} }));
    const net = network();
    const { store } = await freshTab(storage);
    assert.equal(store.useChatWallpaper(null, API).wallpaper, null);
    assert.equal(net.calls.length, 0);
    store.configureChatWallpaper(API);
    store.loadChatWallpaper();
    net.answer('get', '/api/wazzup/appearance', { wallpaper: null, settings: {}, mayUpload: true });
    await settle();
    const state = store.useChatWallpaper(7, API);
    assert.equal(state.wallpaper, null);
    assert.equal(state.url, '');
    assert.equal(state.mayUpload, true);
    assert.equal(storage.getItem('icore.wazzup.chatWallpaper.7'), null);
});

test('another person in the tab: the previous wallpaper is released and late answers are ignored', async () => {
    const net = network();
    const { store } = await freshTab();
    store.configureChatWallpaper(API);
    store.loadChatWallpaper();
    net.answer('get', '/api/wazzup/appearance', { wallpaper: WALL, settings: {}, mayUpload: true });
    await settle();
    net.answer('get', `/wallpaper/${WALL.id}`, blob());
    await settle();
    const firstUrl = store.useChatWallpaper(7, API).url;
    assert.match(firstUrl, /^blob:/);
    // Повторный запрос прежнего человека ещё в пути, когда в вкладку входит другой.
    store.loadChatWallpaper();
    const stale = net.calls.find((call) => call.method === 'get' && call.url.endsWith('/api/wazzup/appearance') && !call.done);
    revoked.length = 0;
    store.configureChatWallpaper({ ...API, owner: 8 });
    assert.deepEqual(revoked, [firstUrl], 'картинка прежнего человека освобождена');
    store.loadChatWallpaper();
    assert.equal(store.useChatWallpaper(7, API).wallpaper, null, 'чужое состояние не отдаётся');
    stale.done = true;
    stale.resolve({ data: { wallpaper: WALL, settings: { blur: 9 }, mayUpload: true } });
    await settle();
    const other = { ...API, owner: 8 };
    assert.equal(store.useChatWallpaper(8, other).wallpaper, null, 'запоздавший ответ прежнего не лёг новому');
    assert.equal(net.calls.filter((call) => call.url.includes('/wallpaper/')).length, 1, 'и его картинка не качается');
    net.answer('get', '/api/wazzup/appearance', { wallpaper: { ...WALL, id: 'aaaaaaaa-2222-4333-8444-555555555555' },
        settings: {}, mayUpload: true });
    await settle();
    assert.equal(store.useChatWallpaper(8, other).wallpaper?.id, 'aaaaaaaa-2222-4333-8444-555555555555');
});

test('saving a new picture sends it once with its view and shows it without downloading it back', async () => {
    const net = network();
    const { store } = await freshTab();
    store.configureChatWallpaper(API);
    const image = { blob: blob('new'), url: 'blob:local-picture' };
    const saving = store.saveChatWallpaper({ image, settings: { brightness: 25, x: 10, y: 90, blur: 99 } });
    const post = net.calls.find((call) => call.method === 'post');
    assert.ok(post.url.endsWith('/api/wazzup/appearance/wallpaper'));
    const form = post.rest[0];
    assert.equal(form.get('file').size, 3);
    assert.equal(form.get('file').name, 'wallpaper.webp');
    assert.deepEqual(JSON.parse(form.get('settings')), { brightness: 25, blur: 0, saturation: 100, x: 10, y: 90 },
        'вне границ — по умолчанию, как и на сервере');
    net.answer('post', '/wallpaper', { wallpaper: WALL, settings: { brightness: 25, x: 10, y: 90 }, mayUpload: true }, 201);
    await saving;
    await settle();
    const state = store.useChatWallpaper(7, API);
    assert.equal(state.url, 'blob:local-picture');
    assert.equal(net.calls.filter((call) => call.url.includes('/wallpaper/')).length, 0);
});

test('settings go with the wallpaper id they were made for; removing is its own request', async () => {
    const net = network();
    const { store } = await freshTab();
    store.configureChatWallpaper(API);
    store.loadChatWallpaper();
    net.answer('get', '/api/wazzup/appearance', { wallpaper: WALL, settings: {}, mayUpload: true });
    await settle();
    const saving = store.saveChatWallpaper({ settings: { saturation: 0 } });
    const put = net.calls.find((call) => call.method === 'put');
    assert.deepEqual(put.rest[0], { wallpaperId: WALL.id, settings: { brightness: 0, blur: 0, saturation: 0, x: 50, y: 50 } });
    net.answer('put', '/api/wazzup/appearance', { wallpaper: WALL, settings: { saturation: 0 }, mayUpload: true });
    await saving;
    const removing = store.saveChatWallpaper({ remove: true, settings: {} });
    net.answer('delete', '/api/wazzup/appearance/wallpaper', { wallpaper: null, settings: {}, mayUpload: true });
    await removing;
    assert.equal(store.useChatWallpaper(7, API).wallpaper, null);
});

test('a wallpaper changed in another tab: the answer shows the current one and the error is readable', async () => {
    const net = network();
    const { store } = await freshTab();
    store.configureChatWallpaper(API);
    store.loadChatWallpaper();
    net.answer('get', '/api/wazzup/appearance', { wallpaper: WALL, settings: {}, mayUpload: true });
    await settle();
    const other = { ...WALL, id: 'bbbbbbbb-2222-4333-8444-555555555555' };
    const saving = store.saveChatWallpaper({ settings: { blur: 3 } });
    net.answer('put', '/api/wazzup/appearance',
        { error: 'Обои уже сменились. Откройте кастомизацию ещё раз.', wallpaper: other, settings: {}, mayUpload: true }, 409);
    await assert.rejects(saving, /Обои уже сменились/);
    assert.equal(store.useChatWallpaper(7, API).wallpaper.id, other.id);
    const failing = store.saveChatWallpaper({ settings: {} });
    net.answer('put', '/api/wazzup/appearance', {}, 503);
    await assert.rejects(failing, /Не удалось сохранить обои/);
});

test('the staff list is normalized for the cards', async () => {
    const net = network();
    const { store } = await freshTab();
    store.configureChatWallpaper(API);
    const loading = store.loadStaffWallpapers();
    net.answer('get', '/api/wazzup/appearance/wallpapers',
        { items: [{ userId: 3, name: 'Сотрудник', department: '', wallpaper: WALL, settings: { blur: 50 } }] });
    const items = await loading;
    assert.equal(items[0].settings.blur, 0);
    assert.equal(items[0].settings.saturation, 100);
});

test('settings outside the limits fall back to defaults', async () => {
    const { store } = await freshTab();
    assert.deepEqual(store.cleanWallpaperSettings({ brightness: -61, blur: '7', saturation: true, x: null, y: 33.3 }),
        { brightness: 0, blur: 7, saturation: 100, x: 50, y: 33.3 });
    assert.deepEqual(store.cleanWallpaperSettings('nope'), store.DEFAULT_WALLPAPER_SETTINGS);
});

test('standard-only accounts get no wallpaper and one theme; others may have their own', () => {
    for (const owner of [241, '241']) {
        assert.equal(wallpaperAllowed(owner), false, String(owner));
        assert.deepEqual(chatThemesFor(owner).map((theme) => theme.id), ['standard']);
    }
    for (const owner of [null, undefined, '']) assert.equal(wallpaperAllowed(owner), false, String(owner));
    assert.equal(wallpaperAllowed(7), true);
});

test('the wallpaper layer: veil instead of a brightness filter, blur grows the picture past the edges', () => {
    assert.equal(wallpaperFilter({ blur: 0, saturation: 100 }), 'none');
    assert.equal(wallpaperFilter({ blur: 10, saturation: 40 }, 0.5), 'blur(5px) saturate(0.4)');
    assert.equal(wallpaperVeil(0), '');
    assert.equal(wallpaperVeil(-30), 'rgba(0, 0, 0, 0.3)');
    assert.equal(wallpaperVeil(45), 'rgba(255, 255, 255, 0.45)');
    assert.equal(renderToStaticMarkup(React.createElement(ChatWallpaperLayer, { url: '', settings: {} })), '');
    const html = renderToStaticMarkup(React.createElement(ChatWallpaperLayer, {
        url: 'blob:picture', settings: { brightness: -20, blur: 6, saturation: 100, x: 30, y: 70 } }));
    assert.match(html, /background-image:url\(&quot;blob:picture&quot;\)/);
    assert.match(html, /background-position:30% 70%/);
    assert.match(html, /inset:-12px/);
    assert.match(html, /rgba\(0, 0, 0, 0.2\)/);
    const flat = renderToStaticMarkup(React.createElement(ChatWallpaperLayer, {
        url: 'blob:picture', settings: { brightness: 0, blur: 0, saturation: 100, x: 50, y: 50 } }));
    assert.match(flat, /inset:0/);
    assert.doesNotMatch(flat, /rgba\(/);
});

test('dragging the preview moves the visible part the way the finger goes and stays inside the picture', () => {
    const pane = { width: 1000, height: 600 };
    const wide = { width: 3000, height: 1000 };   // шире окна: двигается только по горизонтали
    const over = previewOverflow(pane, wide);
    assert.equal(Math.round(over.x), 800);
    assert.equal(over.y, 0);
    assert.deepEqual(movedPosition({ x: 50, y: 50 }, 80, 40, over), { x: 40, y: 50 });
    assert.deepEqual(movedPosition({ x: 50, y: 50 }, -2000, 0, over), { x: 100, y: 50 });
    assert.deepEqual(movedPosition({ x: 5, y: 50 }, 2000, 0, over), { x: 0, y: 50 });
    // Размытый слой шире окна на 2×20 px — сдвиг считается от него.
    const tall = previewOverflow(pane, { width: 600, height: 1200 }, 10);
    assert.equal(Math.round(tall.x), 0);
    assert.equal(Math.round(tall.y), 1440);
    assert.equal(Math.round(previewOverflow(pane, { width: 600, height: 1200 }).y), 1400);
    assert.deepEqual(previewOverflow(pane, null), { x: 0, y: 0 });
});

test('the preview copies the classes of the real chat pane, so themes and glass land the same way', () => {
    const html = renderToStaticMarkup(React.createElement(PreviewPane, {
        image: { url: 'blob:x' }, settings: { brightness: 0, blur: 0, saturation: 100, x: 50, y: 50 }, author: 'Оператор' }));
    const view = readFileSync(join(process.cwd(), 'src/components/wazzup/WazzupChatsView.jsx'), 'utf8');
    const classOf = (marker) => {
        const at = view.indexOf(marker);
        assert.ok(at >= 0, marker);
        return view.slice(view.lastIndexOf('className=', at), view.indexOf('\n', at));
    };
    for (const [marker, tokens] of [
        ["'wazzup-message-outgoing", ['wazzup-message-outgoing', 'rounded-br-md', 'bg-[#dcf8c6]']],
        ["'wazzup-message-incoming", ['wazzup-message-incoming', 'rounded-bl-md', 'ring-slate-200/60']],
        ['wz-day-pill rounded-full bg-slate-500/10', ['wz-day-pill', 'bg-slate-500/10', 'text-slate-500']],
        ['wz-chat-pane relative isolate', ['wz-chat-pane', 'relative', 'isolate', 'bg-[#f2f2f7]']],
        ['wz-wall-label mb-1 max-w-full truncate', ['wz-wall-label', 'text-[11px]']],
    ]) {
        const source = classOf(marker);
        for (const token of tokens) {
            assert.ok(source.includes(token), `окно: ${token}`);
            assert.ok(html.includes(token), `превью: ${token}`);
        }
    }
    assert.match(html, /wz-thread-surface/);
    assert.match(html, /data-chat-wallpaper-layer/);
    assert.match(html, /Оператор/);
});
