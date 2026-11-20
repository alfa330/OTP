/* Хранилище личной раскладки сайдбара: что и когда уходит на сервер.
 *
 * Настоящий sidebarLayoutStore.js, собранный esbuild'ом с подменённым axios
 * (запросы записываются, ответы задаёт тест) и localStorage из Map. Держим:
 *  - «Изменить меню» оставляет меню как было; «Обнулить» уносит все разделы
 *    и папки на панель, ⌘Z возвращает;
 *  - раздел на раздел — папка из двух сразу с полем имени, опустевшая папка
 *    исчезает тем же шагом; бросок ниже последнего — новая карточка;
 *  - «Готово» ставит оставшееся в конец и отправляет PUT, совпало со
 *    стандартным меню — DELETE; «Отменить» ничего не отправляет;
 *  - меню изменилось посреди правки (сменили отдел) — новый раздел просто
 *    появляется на панели и ставится как любой другой;
 *  - неотправленная копия уходит при следующем входе, только если на сервере
 *    с тех пор ничего не менялось, — иначе выигрывает серверная;
 *  - отказ 4xx не оставляет копию вечно неотправленной;
 *  - выход из учётки посреди правки правку отменяет. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, realpathSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const esbuild = require('esbuild');

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const SIDEBAR = join(ROOT, 'src', 'components', 'sidebar');

const storage = new Map();
globalThis.window = globalThis.window || {};
globalThis.window.localStorage = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
};

const cacheDir = join(ROOT, 'node_modules', '.cache', 'otp-tests');
mkdirSync(cacheDir, { recursive: true });
const stub = join(cacheDir, 'sidebar-axios-stub.mjs');
writeFileSync(stub, `
export const calls = [];
export const replies = { get: [], put: [], delete: [] };
const take = (method) => {
    const reply = replies[method].shift();
    if (!reply) return Promise.resolve({ data: {} });
    return reply.error ? Promise.reject(reply.error) : Promise.resolve({ data: reply.data });
};
export default {
    get: (url) => { calls.push(['get', url]); return take('get'); },
    put: (url, body) => { calls.push(['put', url, body]); return take('put'); },
    delete: (url) => { calls.push(['delete', url]); return take('delete'); },
};
`);
/* Настоящий путь: node_modules бывает ссылкой (копии репозитория делят одну
   папку пакетов), и тогда один и тот же файл, найденный по ссылке и напрямую,
   esbuild собрал бы дважды — у теста и у хранилища оказались бы разные заглушки. */
const stubPath = realpathSync(stub);
const entry = join(cacheDir, 'sidebar-store-entry.js');
writeFileSync(entry, [
    `export { sidebarLayoutStore } from ${JSON.stringify(join(SIDEBAR, 'sidebarLayoutStore.js'))};`,
    `export * as ax from ${JSON.stringify(stubPath)};`,
].join('\n'));
const outfile = join(cacheDir, 'sidebar-store-bundle.mjs');
await esbuild.build({
    entryPoints: [entry],
    bundle: true,
    format: 'esm',
    platform: 'node',
    outfile,
    external: ['react'],
    plugins: [{
        name: 'axios-stub',
        setup(build) {
            build.onResolve({ filter: /^axios$/ }, () => ({ path: stubPath }));
        },
    }],
    logLevel: 'silent',
});
const { sidebarLayoutStore: store, ax } = await import(`file://${outfile}?v=${Date.now()}`);

const API = 'http://api';
const URL_ = `${API}/api/sidebar_layout`;
const DEFAULT = [['А', 'Б'], ['В']];
const layoutOf = (blocks, folders = {}) => ({ v: 1, blocks, folders });
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

const startFor = (userId, { cached = null, blocks = DEFAULT } = {}) => {
    if (cached) storage.set(`otp:sidebarLayout:v1:${userId}`, JSON.stringify(cached));
    store.setContext({ defaultBlocks: blocks, present: new Set(blocks.flat()) });
    store.connect({ userId, apiBaseUrl: API, getHeaders: () => ({}) });
    // Сверку после входа тест зовёт сам (syncNow), отложенную — снимаем.
    store.disconnect();
    ax.calls.length = 0;
};
const cacheOf = (userId) => JSON.parse(storage.get(`otp:sidebarLayout:v1:${userId}`) || 'null');

test('«Изменить меню» оставляет меню как было; «Обнулить» — всё на панель, ⌘Z возвращает', () => {
    const mine = layoutOf([['folder:fa1', 'В'], ['А', 'Б']], { fa1: { name: 'Своё', items: ['Г'] } });
    startFor(6, { cached: { layout: mine, dirty: false, base: 'T0' } });
    store.setContext({ defaultBlocks: [['А', 'Б'], ['В', 'Г']], present: new Set(['А', 'Б', 'В', 'Г']) });
    store.startEditing();
    assert.deepEqual(store.workingLayout(), mine, 'ничего не разлетелось');
    assert.equal(store.canReset(), true);
    assert.equal(store.reset(), true);
    assert.deepEqual(store.workingLayout(), layoutOf([]), 'и разделы, и папки — на панели');
    assert.equal(store.canReset(), false, 'обнулять уже нечего');
    store.undo();
    assert.deepEqual(store.workingLayout(), mine);
    store.cancelEditing();
});

test('раздел на раздел — папка с полем имени; унесли последний — папка исчезает; ⌘Z возвращает', () => {
    startFor(17);
    store.startEditing();
    const id = store.stack('А', 'В');
    assert.ok(id, 'папка создана');
    assert.deepEqual(store.workingLayout(), layoutOf([[`folder:${id}`, 'Б']], { [id]: { name: 'Новая папка', items: ['А', 'В'] } }));
    assert.equal(store.getState().renamingId, id, 'сразу с полем имени — как на iPhone');
    store.finishRename(id, 'Каждый день');
    assert.equal(store.workingLayout().folders[id].name, 'Каждый день');
    assert.equal(store.move('А', { block: 0, folder: null, before: 'Б' }), true);
    assert.equal(store.move('В', { newBlock: true }), true, 'ниже последнего — своей карточкой');
    assert.deepEqual(store.workingLayout(), layoutOf([['А', 'Б'], ['В']]), 'опустевшая папка исчезла тем же шагом');
    store.undo();
    assert.deepEqual(store.workingLayout().folders[id], { name: 'Каждый день', items: ['В'] }, '⌘Z — папка вернулась вместе с разделом');
    // Раздел с панели — на раздел в меню.
    store.reset();
    store.place('Б');
    const second = store.stack('Б', 'А');
    assert.deepEqual(store.workingLayout().folders[second].items, ['Б', 'А']);
    assert.equal(store.stack('Б', 'Б'), null, 'на самого себя — нет');
    store.cancelEditing();
});

test('«Готово» — собранное сверху, остальное в конец в обычном порядке; PUT с отметкой версии', async () => {
    startFor(7);
    store.startEditing();
    store.reset();
    assert.equal(store.place('В'), true, 'щелчок на панели — в конец меню');
    assert.equal(store.place('А', { block: 0, folder: null, before: 'В' }), true, 'перетащили перед «В»');
    ax.replies.put.push({ data: { updated_at: 'T1' } });
    store.finishEditing();
    await settle();
    const expected = layoutOf([['А', 'В'], ['Б']]);
    assert.deepEqual(ax.calls, [['put', URL_, { layout: expected }]]);
    assert.deepEqual(cacheOf(7), { layout: expected, dirty: false, base: 'T1' });
    assert.deepEqual(store.getState().saved, expected);
});

test('ничего не меняли — без запроса; «Обнулить» и сразу «Готово» — стандартное меню (DELETE)', async () => {
    startFor(8);
    store.startEditing();
    store.finishEditing();
    await settle();
    assert.deepEqual(ax.calls, [], 'стандартное осталось стандартным — на сервер незачем');

    const mine = layoutOf([['В', 'А', 'Б']]);
    startFor(9, { cached: { layout: mine, dirty: false, base: 'T2' } });
    store.startEditing();
    store.finishEditing();
    await settle();
    assert.deepEqual(ax.calls, [], '«Изменить меню» и «Готово» без перестановок свою раскладку не трогают');
    assert.deepEqual(store.getState().saved, mine);

    store.startEditing();
    store.reset();
    ax.replies.delete.push({ data: { layout: null, updated_at: null } });
    store.finishEditing();
    await settle();
    assert.deepEqual(ax.calls, [['delete', URL_]]);
    assert.equal(cacheOf(9), null);
    assert.equal(store.getState().saved, null);
});

test('«Отменить» возвращает сохранённую раскладку и ничего не отправляет', async () => {
    const mine = layoutOf([['В', 'А', 'Б']]);
    startFor(15, { cached: { layout: mine, dirty: false, base: 'T3' } });
    store.startEditing();
    store.reset();
    store.place('Б');
    store.cancelEditing();
    await settle();
    assert.deepEqual(ax.calls, []);
    assert.deepEqual(store.getState().saved, mine);
    assert.equal(store.getState().editing, false);
});

test('раздел уносят на панель, ⌘Z его возвращает', () => {
    startFor(16);
    store.startEditing();
    assert.equal(store.unplace('А'), true);
    assert.deepEqual(store.workingLayout().blocks, [['Б'], ['В']]);
    store.undo();
    assert.deepEqual(store.workingLayout().blocks, [['А', 'Б'], ['В']]);
    store.cancelEditing();
});

test('меню изменилось посреди правки — новый раздел просто появляется на панели', () => {
    startFor(10);
    store.startEditing();
    store.reset();
    store.place('А');
    // Админ сменил отдел: в меню появился раздел «Н».
    store.setContext({ defaultBlocks: [['А', 'Н', 'Б'], ['В']], present: new Set(['А', 'Н', 'Б', 'В']) });
    assert.equal(store.place('Н', { block: 0, folder: null, before: 'А' }), true);
    assert.deepEqual(store.workingLayout().blocks, [['Н', 'А']]);
    assert.equal(store.move('А', { block: 0, folder: null, before: 'Н' }), true);
    assert.deepEqual(store.workingLayout().blocks, [['А', 'Н']]);
    store.cancelEditing();
});

test('неотправленная копия уходит, только если на сервере с тех пор ничего не менялось', async () => {
    const mine = layoutOf([['Б', 'А'], ['В']]);
    const theirs = layoutOf([['В'], ['А', 'Б']]);

    startFor(11, { cached: { layout: mine, dirty: true, base: 'T5' } });
    ax.replies.get.push({ data: { layout: layoutOf([['А', 'Б'], ['В']]), updated_at: 'T5' } });
    ax.replies.put.push({ data: { updated_at: 'T6' } });
    await store.syncNow();
    await settle();
    assert.deepEqual(ax.calls.map((c) => c[0]), ['get', 'put']);
    assert.deepEqual(ax.calls[1][2], { layout: mine });
    assert.deepEqual(cacheOf(11), { layout: mine, dirty: false, base: 'T6' });

    // На сервере уже другая раскладка (сохранили на другом устройстве) — её не затираем.
    startFor(12, { cached: { layout: mine, dirty: true, base: 'T5' } });
    ax.replies.get.push({ data: { layout: theirs, updated_at: 'T9' } });
    await store.syncNow();
    await settle();
    assert.deepEqual(ax.calls.map((c) => c[0]), ['get']);
    assert.deepEqual(store.getState().saved, theirs);
    assert.deepEqual(cacheOf(12), { layout: theirs, dirty: false, base: 'T9' });
});

test('отказ 4xx не оставляет копию вечно неотправленной', async () => {
    startFor(13);
    store.startEditing();
    store.reset();
    store.place('В');
    ax.replies.put.push({ error: { response: { status: 400 } } });
    store.finishEditing();
    await settle();
    assert.equal(cacheOf(13).dirty, false);
    assert.equal(ax.calls.filter((c) => c[0] === 'put').length, 1, 'форму сервер не примет и при повторе');
});

test('выход из учётки посреди правки отменяет правку', () => {
    startFor(14);
    store.startEditing();
    store.move('В', { block: 0, folder: null, before: 'А' });
    store.disconnect();
    assert.equal(store.getState().editing, false);
    assert.equal(store.getState().saved, null, 'несохранённое не сохранилось');
});
