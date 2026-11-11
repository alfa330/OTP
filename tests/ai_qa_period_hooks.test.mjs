/*
 * Период раздела «ИИ-оценка» (задача #389) — ПОВЕДЕНИЕ хуков и эффектов:
 * перечитывание под новый период, порядок ответов, сброс страницы, подписи
 * по периоду ответа.
 *
 * Зачем: react-dom/server эффектов не выполняет, а вся логика периода в
 * useDigest, QaDashboard и AdjudicationsRag живёт именно в эффектах. Браузерного
 * окружения в проекте нет, поэтому верхний компонент (или хук) вызывается
 * маленьким исполнителем хуков: useState/useRef/useMemo/useCallback/useEffect
 * по правилам React (эффекты — после отрисовки, по изменению зависимостей,
 * cleanup перед повтором). Дерево, которое он вернул, рисует настоящий
 * renderToStaticMarkup — дочерние компоненты идут обычным путём React.
 * axios — подменён: каждый запрос ждёт, пока тест сам не ответит на него.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { renderToStaticMarkup } = require('react-dom/server');
const { build } = require('esbuild');

const CACHE = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(CACHE, { recursive: true });

/* Подменный react: хуки — у исполнителя, пока он вызывает верхний компонент;
   всё остальное время (дочерние компоненты в renderToStaticMarkup) — настоящие. */
const FAKE_REACT = join(CACHE, 'period-hooks-fake-react.mjs');
writeFileSync(FAKE_REACT, `
import { createRequire } from 'node:module';
const Real = createRequire(import.meta.url)('react');
const pick = (name) => (...args) => (globalThis.__hookHarness ? globalThis.__hookHarness[name](...args) : Real[name](...args));
export const useState = pick('useState');
export const useRef = pick('useRef');
export const useMemo = pick('useMemo');
export const useCallback = pick('useCallback');
export const useEffect = pick('useEffect');
export const useLayoutEffect = pick('useLayoutEffect');
export const useContext = pick('useContext');
export const useId = pick('useId');
export const { createElement, Fragment, forwardRef, memo, createContext, Children, cloneElement, isValidElement } = Real;
export default { ...Real, useState, useRef, useMemo, useCallback, useEffect, useLayoutEffect, useContext, useId };
`);
const FAKE_AXIOS = join(CACHE, 'period-hooks-fake-axios.mjs');
writeFileSync(FAKE_AXIOS, `
const calls = (globalThis.__axiosCalls ||= []);
const request = (method) => (url, ...rest) => {
    const cfg = method === 'get' ? rest[0] : rest[1];
    let resolve; let reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    const call = { method, url, params: cfg?.params || {}, resolve, reject };
    cfg?.signal?.addEventListener?.('abort', () => { call.aborted = true; reject({ __cancel: true }); });
    calls.push(call);
    return promise;
};
const axios = { get: request('get'), post: request('post'), put: request('put'), delete: request('delete'),
                isCancel: (e) => Boolean(e && e.__cancel) };
export default axios;
`);

async function load(relative, exportName = 'default') {
  const outfile = join(CACHE, `period-hooks-${relative.replace(/[\\/]/g, '_')}.mjs`);
  await build({
    entryPoints: [fileURLToPath(new URL(`../src/components/${relative}`, import.meta.url))],
    bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
    packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' }, logLevel: 'silent',
    plugins: [{
      name: 'fake-react-axios',
      setup(build) {
        build.onResolve({ filter: /^react$/ }, (args) => (args.importer === FAKE_REACT
          ? { path: 'react', external: true } : { path: FAKE_REACT }));
        build.onResolve({ filter: /^axios$/ }, () => ({ path: FAKE_AXIOS }));
      },
    }],
  });
  const mod = await import(`file://${outfile.replace(/\\/g, '/')}?v=${Date.now()}`);
  return exportName === '*' ? mod : mod[exportName];
}

const depsEqual = (a, b) => Boolean(a && b) && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));

/** Исполнитель хуков одного компонента: mount → rerender(props) → unmount. */
function mount(Component, initialProps) {
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
    useMemo(fn, deps) {
      const i = index++;
      if (slots[i] && depsEqual(slots[i].deps, deps)) return slots[i].value;
      slots[i] = { value: fn(), deps };
      return slots[i].value;
    },
    useCallback(fn, deps) { return harness.useMemo(() => fn, deps); },
    useEffect(fn, deps) {
      const i = index++;
      const prev = slots[i];
      if (prev && deps && depsEqual(prev.deps, deps)) return;
      const slot = { deps, cleanup: prev?.cleanup || null };
      slots[i] = slot;
      pending.push(() => { slot.cleanup?.(); const c = fn(); slot.cleanup = typeof c === 'function' ? c : null; });
    },
    useLayoutEffect(fn, deps) { harness.useEffect(fn, deps); },
    useContext(ctx) { return ctx._currentValue; },
    useId() { return ':period-hooks:'; },
  };
  function run() {
    rendering = true;
    try {
      for (let pass = 0; pass < 50; pass += 1) {
        dirty = false; index = 0; pending = [];
        globalThis.__hookHarness = harness;
        try { output = Component(props); } finally { globalThis.__hookHarness = null; }
        const effects = pending; pending = [];
        effects.forEach((effect) => effect());
        if (!dirty) return;
      }
      throw new Error('бесконечная перерисовка');
    } finally { rendering = false; }
  }
  run();
  return {
    get output() { return output; },
    html: () => renderToStaticMarkup(output),
    rerender(next) { props = { ...props, ...next }; run(); },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
  };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
const calls = () => (globalThis.__axiosCalls ||= []);
const takeCalls = (fragment) => calls().filter((call) => call.url.includes(fragment) && !call.taken)
  .map((call) => { call.taken = true; return call; });
const reply = async (call, data) => { call.resolve({ data }); await flush(); };

globalThis.window ||= globalThis;

/* ── «Сводка»: useDigest ─────────────────────────────────────────────────── */

const useDigest = await load('call_qa/useDigest.js');
const HookHost = (props) => useDigest(props);
const DAYS_A = { days: [{ day: '2026-11-01', evaluated: 3 }], months: {} };
const DAYS_B = { days: [{ day: '2026-11-08', evaluated: 5 }], months: {} };
const digestProps = { apiBaseUrl: 'http://stub', headers: () => ({}), department: 'op', enabled: true,
                      showToast() {}, dateFrom: '', dateTo: '' };

test('«Сводка»: новый период после загруженного — новый запрос с датами периода', async () => {
  const host = mount(HookHost, digestProps);
  const [first] = takeCalls('/api/ai-qa/digests');
  assert.deepEqual(first.params, { department: 'op' });
  await reply(first, DAYS_A);
  assert.deepEqual(host.output.days, DAYS_A.days);

  host.rerender({ dateFrom: '2026-11-04', dateTo: '2026-11-10' });
  const next = takeCalls('/api/ai-qa/digests');
  assert.equal(next.length, 1, 'смена периода перечитывает список');
  assert.deepEqual(next[0].params, { department: 'op', date_from: '2026-11-04', date_to: '2026-11-10' });
  assert.equal(host.output.daysLoading, true);
  assert.deepEqual(host.output.days, DAYS_A.days, 'прежний список на месте, пока грузится новый');
  await reply(next[0], DAYS_B);
  assert.deepEqual(host.output.days, DAYS_B.days);
  assert.deepEqual(host.output.daysPeriod, { from: '2026-11-04', to: '2026-11-10' });
  assert.equal(host.output.daysLoading, false);
  host.unmount();
});

test('«Сводка»: ответ на прежний период, пришедший позже нового, на экран не попадает', async () => {
  const host = mount(HookHost, digestProps);
  await reply(takeCalls('/api/ai-qa/digests')[0], DAYS_A);
  host.rerender({ dateFrom: '2026-11-01', dateTo: '2026-11-01' });
  const [older] = takeCalls('/api/ai-qa/digests');
  host.rerender({ dateFrom: '2026-11-08', dateTo: '2026-11-08' });
  const [newer] = takeCalls('/api/ai-qa/digests');
  await reply(newer, DAYS_B);
  await reply(older, DAYS_A);
  assert.deepEqual(host.output.days, DAYS_B.days);
  assert.deepEqual(host.output.daysPeriod, { from: '2026-11-08', to: '2026-11-08' });
  host.unmount();
});

/* ── «Обзор»: QaDashboard ────────────────────────────────────────────────── */

const QaDashboard = await load('call_qa/QaDashboard.jsx');
const { EMPTY_FILTERS } = await import('../src/components/call_qa/filters.js');
const STATS = (runs) => ({ alarm_precision: null, recall: null, by_criterion: [], reviewed: null,
                           rag: { status: 'ready', runs, rollout: [] } });
const dashProps = { apiBaseUrl: 'http://stub', department: 'op', filters: { ...EMPTY_FILTERS } };

test('«Обзор»: новый период — прежние цифры на месте и приглушены, а не заглушка', async () => {
  const host = mount(QaDashboard, dashProps);
  await reply(takeCalls('/api/ai-qa/stats')[0], STATS(30));
  host.rerender({ filters: { ...EMPTY_FILTERS, date_from: '2026-11-04', date_to: '2026-11-10' } });
  const [weekly] = takeCalls('/api/ai-qa/stats');
  assert.equal(weekly.params.date_from, '2026-11-04');
  const html = host.html();
  assert.ok(!html.includes('Загрузка статистики'), 'смена периода не сменяет цифры заглушкой');
  assert.ok(html.includes('aria-busy="true"'));
  host.unmount();
});

test('«Обзор»: ответ на прежний период, пришедший позже нового, не перерисовывает новый', async () => {
  const host = mount(QaDashboard, dashProps);
  const [all] = takeCalls('/api/ai-qa/stats');
  host.rerender({ filters: { ...EMPTY_FILTERS, date_from: '2026-11-04', date_to: '2026-11-10' } });
  const [week] = takeCalls('/api/ai-qa/stats');
  await reply(week, STATS(7));
  // Прежний запрос отменён (abort); даже если ответ всё же пришёл — он чужой.
  all.resolve({ data: STATS(999) }); await flush();
  assert.ok(host.html().includes('>7<') || !host.html().includes('999'));
  assert.ok(!host.html().includes('999'));
  host.unmount();
});

test('«Обзор»: подпись блока RAG — по периоду загруженных цифр, а не по выбранному, пока он грузится', async () => {
  const host = mount(QaDashboard, dashProps);
  await reply(takeCalls('/api/ai-qa/stats')[0], STATS(30));
  assert.match(host.html(), /Наблюдаемость RAG · <!-- -->30 дней|Наблюдаемость RAG · 30 дней/);
  host.rerender({ filters: { ...EMPTY_FILTERS, date_from: '2026-11-04', date_to: '2026-11-10' } });
  // Новый период ещё читается (на проде «Обзор» за неделю — ~43 с): на экране
  // прежние цифры за 30 дней — и подпись обязана говорить «30 дней».
  assert.ok(host.html().includes('30 дней'), 'над цифрами за 30 дней стоит подпись нового периода');
  host.unmount();
});

/* ── «База разборов»: AdjudicationsRag ───────────────────────────────────── */

const AdjudicationsRag = await load('call_qa/AdjudicationsRag.jsx');
const CATALOG = (page, extra = {}) => ({ items: [], total: 40, page, page_size: 12,
  facets: { directions: [], statuses: [{ value: 'active', label: 'active', count: 2 },
                                       { value: 'draft', label: 'draft', count: 5 }] },
  knowledge: { active_count: 77 }, health: { status: 'healthy' }, ...extra });
const ragProps = { apiBaseUrl: 'http://stub', department: 'op', canManage: false, showToast() {},
                   dateFrom: '', dateTo: '' };

test('«База разборов»: новый период — снова с первой страницы', async () => {
  const host = mount(AdjudicationsRag, ragProps);
  const [first] = takeCalls('/api/ai-qa/adjudications');
  await reply(first, CATALOG(3));                       // сервер вернул третью страницу
  host.rerender({ dateFrom: '2026-11-04', dateTo: '2026-11-10' });
  const live = takeCalls('/api/ai-qa/adjudications').filter((call) => !call.aborted);
  assert.equal(live.length, 1);
  assert.equal(live[0].params.page, 1, 'период сужает выборку — пятая страница была бы пустой');
  assert.equal(live[0].params.date_from, '2026-11-04');
  host.unmount();
});

test('«База разборов»: «Одобрено» и подпись — по периоду ответа', async () => {
  const host = mount(AdjudicationsRag, ragProps);
  await reply(takeCalls('/api/ai-qa/adjudications')[0], CATALOG(1));
  let html = host.html();
  assert.ok(html.includes('Разборов в отделе'));
  assert.ok(html.includes('77'), 'без периода «Одобрено» — из состояния базы');
  host.rerender({ dateFrom: '2026-11-04', dateTo: '2026-11-10' });
  html = host.html();
  assert.ok(html.includes('Разборов в отделе'), 'пока период грузится, подпись — прежняя');
  const live = takeCalls('/api/ai-qa/adjudications').filter((call) => !call.aborted);
  await reply(live[live.length - 1], CATALOG(1));
  html = host.html();
  assert.ok(html.includes('Разборов за период'));
  assert.ok(!html.includes('>77<'), 'с периодом «Одобрено» — из счётчика статусов за период');
  host.unmount();
});

/* Правка открыта, а отбор сменился (у маркетинга «Отбор по сделке» остаётся
   доступным и во время правки): если правила нет в новом ответе, правка
   снимается — иначе раздел оставался бы серым без кнопки «Отмена» на экране. */
const findWithProp = (node, prop) => {
  if (!node || typeof node !== 'object') return null;
  if (Array.isArray(node)) {
    for (const child of node) { const found = findWithProp(child, prop); if (found) return found; }
    return null;
  }
  if (node.props && typeof node.props[prop] === 'function') return node;
  return findWithProp(node.props?.children, prop);
};

test('«База разборов»: правка правила, которого нет в новом ответе, снимается', async () => {
  const states = [];
  const onInteractionChange = (state) => states.push(state);
  const host = mount(AdjudicationsRag, { ...ragProps, canManage: true, onInteractionChange });
  await reply(takeCalls('/api/ai-qa/adjudications')[0], CATALOG(1, { items: [{ id: 'rule-1' }], total: 1 }));
  findWithProp(host.output, 'onEdit').props.onEdit();
  assert.equal(states.at(-1).editing, true);

  host.rerender({ dateFrom: '2026-11-04', dateTo: '2026-11-10' });
  const live = takeCalls('/api/ai-qa/adjudications').filter((call) => !call.aborted);
  await reply(live.at(-1), CATALOG(1, { items: [], total: 0 }));
  assert.equal(states.at(-1).editing, false, 'раздел не остаётся заблокированным правкой невидимого правила');
  host.unmount();
});

test('«База разборов»: правка правила, оставшегося в ответе, не сбрасывается', async () => {
  const states = [];
  const onInteractionChange = (state) => states.push(state);
  const host = mount(AdjudicationsRag, { ...ragProps, canManage: true, onInteractionChange });
  await reply(takeCalls('/api/ai-qa/adjudications')[0], CATALOG(1, { items: [{ id: 'rule-1' }], total: 1 }));
  findWithProp(host.output, 'onEdit').props.onEdit();
  host.rerender({ dateFrom: '2026-11-04', dateTo: '2026-11-10' });
  const live = takeCalls('/api/ai-qa/adjudications').filter((call) => !call.aborted);
  await reply(live.at(-1), CATALOG(1, { items: [{ id: 'rule-1' }], total: 1 }));
  assert.equal(states.at(-1).editing, true);
  host.unmount();
});
