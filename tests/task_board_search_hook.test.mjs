/*
 * Поиск по доске «Задач» (27.11.2026) — ПОВЕДЕНИЕ хука useTaskBoardSearch:
 * задержка набора, отмена устаревшего запроса, сбой и «Повторить», «Показать
 * ещё», перечитка после действий с задачей, смена доски.
 *
 * Хук гоняется настоящим кодом: сборка esbuild с подменным react и маленьким
 * исполнителем хуков — тот же приём, что в ai_qa_period_hooks.test.mjs (там же
 * объяснено, зачем: браузерного окружения в проекте нет). Запрос к серверу —
 * подменный: каждый ждёт, пока тест сам на него не ответит.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { build } = require('esbuild');

const CACHE = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(CACHE, { recursive: true });

const FAKE_REACT = join(CACHE, 'board-search-fake-react.mjs');
writeFileSync(FAKE_REACT, `
const pick = (name) => (...args) => globalThis.__hookHarness[name](...args);
export const useState = pick('useState');
export const useRef = pick('useRef');
export const useCallback = pick('useCallback');
export const useEffect = pick('useEffect');
export default { useState, useRef, useCallback, useEffect };
`);

const outfile = join(CACHE, 'board-search-hook.mjs');
await build({
  entryPoints: [fileURLToPath(new URL('../src/components/tasks/useTaskBoardSearch.js', import.meta.url))],
  bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile, logLevel: 'silent',
  plugins: [{
    name: 'fake-react',
    setup(builder) { builder.onResolve({ filter: /^react$/ }, () => ({ path: FAKE_REACT })); },
  }],
});
const { default: useTaskBoardSearch } = await import(`file://${outfile.replace(/\\/g, '/')}?v=${Date.now()}`);
const { SEARCH_DEBOUNCE_MS } = await import('../src/components/tasks/taskSearch.js');

const depsEqual = (a, b) => Boolean(a && b) && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));

/** Исполнитель хуков одного компонента (как в ai_qa_period_hooks.test.mjs). */
function mount(hook, initialProps) {
  const slots = [];
  let props = initialProps;
  let index = 0;
  let rendering = false;
  let dirty = false;
  let pending = [];
  let output = null;
  const harness = {
    useState(init) {
      const i = index++;
      if (!(i in slots)) slots[i] = { value: typeof init === 'function' ? init() : init };
      const slot = slots[i];
      slot.set ||= (next) => {
        const value = typeof next === 'function' ? next(slot.value) : next;
        if (Object.is(value, slot.value)) return;
        slot.value = value;
        if (rendering) dirty = true; else run();
      };
      return [slot.value, slot.set];
    },
    useRef(init) { const i = index++; if (!(i in slots)) slots[i] = { current: init }; return slots[i]; },
    useCallback(fn, deps) {
      const i = index++;
      if (slots[i] && depsEqual(slots[i].deps, deps)) return slots[i].value;
      slots[i] = { value: fn, deps };
      return fn;
    },
    useEffect(fn, deps) {
      const i = index++;
      const prev = slots[i];
      if (prev && deps && depsEqual(prev.deps, deps)) return;
      const slot = { deps, cleanup: prev?.cleanup || null };
      slots[i] = slot;
      pending.push(() => { slot.cleanup?.(); const c = fn(); slot.cleanup = typeof c === 'function' ? c : null; });
    },
  };
  function run() {
    rendering = true;
    try {
      for (let pass = 0; pass < 50; pass += 1) {
        dirty = false; index = 0; pending = [];
        globalThis.__hookHarness = harness;
        try { output = hook(props); } finally { globalThis.__hookHarness = null; }
        const effects = pending; pending = [];
        effects.forEach((effect) => effect());
        if (!dirty) return;
      }
      throw new Error('бесконечная перерисовка');
    } finally { rendering = false; }
  }
  run();
  return {
    get state() { return output; },
    rerender(next) { props = { ...props, ...next }; run(); },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
  };
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const settle = () => wait(SEARCH_DEBOUNCE_MS + 40);
const flush = () => new Promise((resolve) => setImmediate(resolve));

/** Сервер под рукой у теста: запросы копятся, отвечает тест. ignoreAbort — как
    сеть, которая отмену не услышала и всё равно ответила. */
function server({ ignoreAbort = false } = {}) {
  const calls = [];
  const searchTasks = (params, { signal } = {}) => new Promise((resolve, reject) => {
    const call = { params, resolve, reject, aborted: false };
    signal?.addEventListener?.('abort', () => {
      call.aborted = true;
      if (!ignoreAbort) reject(Object.assign(new Error('canceled'), { name: 'CanceledError', code: 'ERR_CANCELED' }));
    });
    calls.push(call);
  });
  const page = (query, from, count, total) => ({
    tasks: Array.from({ length: count }, (_, index) => ({ id: `${query}-${from + index}` })), total,
  });
  return { calls, searchTasks, page };
}

const props = (searchTasks, extra = {}) => ({
  searchTasks, scopeKey: 'my|all|all', scopeParams: { mine: 'any' }, reloadToken: 0, ...extra,
});

test('один запрос на строку: пока человек печатает, сервер не дёргается', async () => {
  const api = server();
  const host = mount(useTaskBoardSearch, props(api.searchTasks));
  for (const text of ['т', 'та', 'таб', 'табе', 'табель']) host.state.setText(text);
  assert.equal(api.calls.length, 0);
  // Одна буква — не поиск, но строка уже «в работе»: выдача не прячется зря.
  assert.equal(host.state.isActive, true);
  await settle();
  assert.equal(api.calls.length, 1);
  assert.deepEqual(api.calls[0].params, { mine: 'any', q: 'табель', limit: 30, offset: 0, summary: 0 });
  assert.equal(host.state.isPending, true);
  api.calls[0].resolve(api.page('табель', 0, 3, 3));
  await flush();
  assert.equal(host.state.isPending, false);
  assert.deepEqual(host.state.tasks.map((task) => task.id), ['табель-0', 'табель-1', 'табель-2']);
  host.unmount();
});

test('новая строка отменяет прежний запрос, его поздний ответ в выдачу не попадает', async () => {
  const api = server();
  const host = mount(useTaskBoardSearch, props(api.searchTasks));
  host.state.setText('табель');
  await settle();
  host.state.setText('отчет');
  await settle();
  assert.equal(api.calls[0].aborted, true);
  api.calls[0].resolve(api.page('табель', 0, 2, 2));
  api.calls[1].resolve(api.page('отчет', 0, 1, 1));
  await flush();
  assert.equal(host.state.query, 'отчет');
  assert.deepEqual(host.state.tasks.map((task) => task.id), ['отчет-0']);
  host.unmount();
});

test('ответ, пришедший вопреки отмене, всё равно не подменяет выдачу новой строки', async () => {
  const api = server({ ignoreAbort: true });
  const host = mount(useTaskBoardSearch, props(api.searchTasks));
  host.state.setText('табель');
  await settle();
  host.state.setText('отчет');
  await settle();
  api.calls[1].resolve(api.page('отчет', 0, 1, 1));
  await flush();
  api.calls[0].resolve(api.page('табель', 0, 2, 2));
  await flush();
  assert.deepEqual(host.state.tasks.map((task) => task.id), ['отчет-0']);
  host.unmount();
});

test('сбой — ответ по этой строке: «Ищу…» не висит, прежние находки уходят, «Повторить» ищет заново', async () => {
  const api = server();
  const host = mount(useTaskBoardSearch, props(api.searchTasks));
  host.state.setText('табель');
  await settle();
  api.calls[0].resolve(api.page('табель', 0, 2, 2));
  await flush();
  host.state.setText('отчет');
  await settle();
  api.calls[1].reject(new Error('500'));
  await flush();
  assert.equal(host.state.isPending, false);
  assert.equal(host.state.query, 'отчет');
  assert.deepEqual(host.state.tasks, []);
  assert.match(host.state.error, /Не удалось/);
  host.state.retry();
  assert.equal(api.calls.length, 3);
  assert.equal(api.calls[2].params.q, 'отчет');
  assert.equal(host.state.error, '');
  api.calls[2].resolve(api.page('отчет', 0, 1, 1));
  await flush();
  assert.deepEqual(host.state.tasks.map((task) => task.id), ['отчет-0']);
  host.unmount();
});

test('«Показать ещё»: следующая страница без повторов; сбой догрузки повторяет догрузку', async () => {
  const api = server();
  const host = mount(useTaskBoardSearch, props(api.searchTasks));
  host.state.setText('табель');
  await settle();
  api.calls[0].resolve(api.page('табель', 0, 30, 45));
  await flush();
  assert.equal(host.state.hasMore, true);
  host.state.loadMore();
  assert.deepEqual([api.calls[1].params.offset, api.calls[1].params.limit], [30, 30]);
  api.calls[1].reject(new Error('502'));
  await flush();
  assert.equal(host.state.loadingMore, false);
  assert.equal(host.state.tasks.length, 30);
  host.state.retry();
  assert.equal(api.calls[2].params.offset, 30);
  // Сервер успел сдвинуть одну задачу — повтор не задваивает её.
  const next = api.page('табель', 29, 16, 45);
  api.calls[2].resolve(next);
  await flush();
  assert.equal(host.state.tasks.length, 45);
  assert.equal(new Set(host.state.tasks.map((task) => task.id)).size, 45);
  assert.equal(host.state.hasMore, false);
  host.unmount();
});

test('после действия с задачей выдача перечитывается целиком, «Показать ещё» не схлопывается', async () => {
  const api = server();
  const host = mount(useTaskBoardSearch, props(api.searchTasks));
  host.state.setText('табель');
  await settle();
  api.calls[0].resolve(api.page('табель', 0, 30, 70));
  await flush();
  host.state.loadMore();
  api.calls[1].resolve(api.page('табель', 30, 30, 70));
  await flush();
  host.rerender({ reloadToken: 1 });
  assert.deepEqual([api.calls[2].params.offset, api.calls[2].params.limit], [0, 60]);
  api.calls[2].resolve(api.page('табель', 0, 60, 70));
  await flush();
  assert.equal(host.state.tasks.length, 60);
  host.unmount();
});

test('другая доска или проект — поиск заново с их охватом; стёрли строку — запрос отменён', async () => {
  const api = server();
  const host = mount(useTaskBoardSearch, props(api.searchTasks));
  host.state.setText('акт');
  await settle();
  host.rerender({ scopeKey: 'all|5|all', scopeParams: { project_id: '5' } });
  assert.equal(api.calls[0].aborted, true);
  assert.deepEqual(api.calls[1].params, { project_id: '5', q: 'акт', limit: 30, offset: 0, summary: 0 });
  host.state.setText('');
  await flush();
  assert.equal(api.calls[1].aborted, true);
  assert.equal(host.state.isActive, false);
  assert.deepEqual(host.state.tasks, []);
  host.unmount();
});
