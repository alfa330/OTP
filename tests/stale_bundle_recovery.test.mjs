import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import React, { Suspense } from 'react';
import { renderToString } from 'react-dom/server';
import { createStaleBundleRecovery, isStaleBundleError } from '../src/staleBundleRecovery.js';

const href = 'https://alfa330.github.io/OTP/?view=wazzup_chats&chat=771c4257%2F77072150101#file';
const chunkError = new TypeError('Failed to fetch dynamically imported module: https://alfa330.github.io/OTP/assets/ChatAttachmentViewer-old.js');
const makeBrowser = ({ storage = new Map(), url = href, blocked = false, online = true } = {}) => {
  const listeners = new Map();
  const navigations = [];
  const browser = {
    navigator: { onLine: online },
    location: { href: url, replace: (value) => navigations.push(value) },
    sessionStorage: {
      getItem: (key) => { if (blocked) throw new Error('SecurityError'); return storage.get(key) || null; },
      setItem: (key, value) => { if (blocked) throw new Error('SecurityError'); storage.set(key, value); },
      removeItem: (key) => { if (blocked) throw new Error('SecurityError'); storage.delete(key); },
    },
    addEventListener: (name, handler) => listeners.set(name, handler),
    removeEventListener: (name, handler) => { if (listeners.get(name) === handler) listeners.delete(name); },
  };
  return { browser, storage, navigations, listeners };
};

test('recognizes Chrome, Safari, Firefox and CSS load failures; leaves application errors alone', () => {
  for (const message of [chunkError.message, 'Importing a module script failed.',
    'error loading dynamically imported module: /assets/view.js', 'Loading chunk 123 failed',
    'Unable to preload CSS for /assets/view.css']) {
    assert.equal(isStaleBundleError(new Error(message)), true, message);
  }
  assert.equal(isStaleBundleError(new Error('Failed to fetch')), false);
  assert.equal(isStaleBundleError(new TypeError('Cannot read properties of null')), false);
});

test('one navigation shared by Vite, lazy rejection and boundary preserves the selected chat', () => {
  const tab = makeBrowser();
  const recovery = createStaleBundleRecovery(tab.browser, 'build-a', () => 100000);
  const uninstall = recovery.install();
  tab.listeners.get('vite:preloadError')({ payload: chunkError, preventDefault() { assert.fail('must not swallow import rejection'); } });
  assert.equal(recovery.recover(chunkError), true);
  tab.listeners.get('unhandledrejection')({ reason: chunkError });
  tab.listeners.get('error')({ error: chunkError });
  assert.equal(tab.navigations.length, 1);
  const target = new URL(tab.navigations[0]);
  assert.equal(target.pathname, '/OTP/');
  assert.equal(target.searchParams.get('view'), 'wazzup_chats');
  assert.equal(target.searchParams.get('chat'), '771c4257/77072150101');
  assert.equal(target.hash, '#file');
  assert.match(target.searchParams.get('v'), /^chunk\.100000\.build-a$/);
  uninstall();
  assert.equal(tab.listeners.size, 0);
});

test('same broken build cannot loop, even long after the first recovery', () => {
  const first = makeBrowser();
  assert.equal(createStaleBundleRecovery(first.browser, 'build-a', () => 100000).recover(chunkError), true);
  const second = makeBrowser({ storage: first.storage });
  assert.equal(createStaleBundleRecovery(second.browser, 'build-a', () => 99999999).recover(chunkError), false);
  assert.equal(second.navigations.length, 0);
});

test('a later deployment can recover but a cross-build failure immediately after reload cannot loop', () => {
  const first = makeBrowser();
  createStaleBundleRecovery(first.browser, 'build-a', () => 100000).recover(chunkError);
  const second = makeBrowser({ storage: first.storage });
  let clock = 101000;
  const recovery = createStaleBundleRecovery(second.browser, 'build-b', () => clock);
  assert.equal(recovery.recover(chunkError), false);
  clock = 131000;
  assert.equal(recovery.recover(chunkError), true);
  assert.equal(second.navigations.length, 1);
});

test('blocked sessionStorage uses the initial URL marker even after address-bar cleanup', () => {
  const first = makeBrowser({ blocked: true });
  assert.equal(createStaleBundleRecovery(first.browser, 'build-a', () => 100000).recover(chunkError), true);
  const second = makeBrowser({ blocked: true, url: first.navigations[0] });
  const recovery = createStaleBundleRecovery(second.browser, 'build-a', () => 200000);
  second.browser.location.href = href;
  assert.equal(recovery.recover(chunkError), false);
  assert.equal(second.navigations.length, 0);
});

test('invalid stored state and ordinary numeric v do not disable recovery', () => {
  for (const raw of ['{', 'null', '{"build":"build-a","ts":"yesterday"}']) {
    const tab = makeBrowser({ storage: new Map([['otp_stale_bundle_recovery_v2', raw]]), url: href.replace('#file', '&v=123#file') });
    assert.equal(createStaleBundleRecovery(tab.browser, 'build-a', () => 100000).recover(chunkError), true);
  }
});

test('offline and ordinary application errors do not trigger navigation or consume recovery', () => {
  const tab = makeBrowser({ online: false });
  const recovery = createStaleBundleRecovery(tab.browser, 'build-a', () => 100000);
  assert.equal(recovery.recover(chunkError), false);
  tab.browser.navigator.onLine = true;
  assert.equal(recovery.recover(new Error('Failed to fetch')), false);
  assert.equal(tab.storage.size, 0);
  assert.equal(recovery.recover(chunkError), true);
});

test('unavailable navigation and non-browser callers fail safely', () => {
  assert.equal(createStaleBundleRecovery(null).recover(chunkError), false);
  const tab = makeBrowser();
  tab.browser.location.replace = () => { throw new Error('navigation blocked'); };
  assert.equal(createStaleBundleRecovery(tab.browser, 'build-a', () => 100000).recover(chunkError), false);
});

const result = await build({
  entryPoints: ['src/utils/lazyWithRetry.js'], bundle: true, write: false,
  platform: 'node', format: 'cjs', external: ['react'],
});
const loadLazy = (browser) => {
  const context = vm.createContext({ window: browser, document: { querySelector: () => ({ src: 'build-a' }) },
    URL, console, require: createRequire(import.meta.url), module: { exports: {} } });
  vm.runInContext(result.outputFiles[0].text, context);
  return context.module.exports.default;
};
const renderLazy = async (lazy, importer) => {
  renderToString(React.createElement(Suspense, { fallback: 'loading' }, React.createElement(lazy(importer))));
  await new Promise(setImmediate);
};

test('actual React.lazy: an unrelated successful import does not reset the recovery guard', async () => {
  const first = makeBrowser();
  await renderLazy(loadLazy(first.browser), async () => { throw chunkError; });
  assert.equal(first.navigations.length, 1);
  const second = makeBrowser({ storage: first.storage, url: first.navigations[0] });
  const lazy = loadLazy(second.browser);
  await renderLazy(lazy, async () => ({ default: () => null }));
  await renderLazy(lazy, async () => { throw chunkError; });
  assert.equal(second.navigations.length, 0);
});

test('actual React.lazy succeeds with blocked storage and keeps ordinary errors intact', async () => {
  const tab = makeBrowser({ blocked: true });
  const lazy = loadLazy(tab.browser);
  await renderLazy(lazy, async () => ({ default: () => null }));
  await renderLazy(lazy, async () => { throw new Error('application error'); });
  assert.equal(tab.navigations.length, 0);
});
