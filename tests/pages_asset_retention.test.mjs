import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { findAssetReferences, INVENTORY, MARKER, preservePagesAssets } from '../scripts/preserve_pages_assets.mjs';

const BASE = 'https://example.test/OTP/';
const DAY = 86400000;
const NOW = Date.parse('2026-10-09T10:00:00Z');

async function fixture(t, live = {}) {
  const root = await mkdtemp(join(tmpdir(), 'otp-pages-assets-'));
  t.after(async () => {
    assert.equal(dirname(root), resolve(tmpdir()));
    assert.ok(basename(root).startsWith('otp-pages-assets-'));
    await rm(root, { recursive: true, force: true });
  });
  const requests = [];
  return {
    root, live, requests,
    async build(name, extra = {}) {
      const directory = join(root, name);
      const files = { 'index.html': `<html><head><script src="/OTP/assets/${name}-12345678.js"></script></head></html>`,
        [`assets/${name}-12345678.js`]: 'export default 1;', ...extra };
      for (const [path, content] of Object.entries(files)) {
        await mkdir(dirname(join(directory, path)), { recursive: true });
        await writeFile(join(directory, path), content);
      }
      return directory;
    },
    async publish(directory, inventory) {
      for (const key of Object.keys(live)) delete live[key];
      for (const path of ['index.html', INVENTORY, ...inventory.assets.map((asset) => asset.path)]) {
        live[path] = await readFile(join(directory, path));
      }
    },
    async run(directory, options = {}) {
      return preservePagesAssets({
        distDir: directory, siteUrl: BASE, now: NOW, retentionDays: 7, retries: 1, log() {}, sleep: async () => {},
        fetchImpl: async (url) => {
          assert.ok(url.searchParams.has('__asset_retention'));
          const path = url.pathname.slice('/OTP/'.length);
          requests.push(path);
          const content = live[path];
          if (content instanceof Response) return content.clone();
          return new Response(content ?? 'missing', { status: content == null ? 404 : 200,
            headers: { 'content-type': path.endsWith('.json') ? 'application/json' : path.endsWith('.html') ? 'text/html' : 'application/javascript' } });
        },
        ...options,
      });
    },
  };
}

function oldSite() {
  return {
    'index.html': '<html><head><script src="/OTP/assets/main-abcdefgh.js"></script></head></html>',
    'assets/main-abcdefgh.js': 'const deps=["assets/viewer-abcdefgh.js","assets/shared-abcdefgh.js"];export const open=()=>import("./viewer-abcdefgh.js");',
    'assets/viewer-abcdefgh.js': 'import "./shared-abcdefgh.js";import "./viewer-abcdefgh.css";',
    'assets/shared-abcdefgh.js': 'export default "shared";',
    'assets/viewer-abcdefgh.css': '.icon{background:url(./image-abcdefgh.png)}',
    'assets/image-abcdefgh.png': Buffer.from([137, 80, 78, 71]),
  };
}

test('bootstrap follows lazy imports, Vite preload tables, CSS and multiple entrypoints', async (t) => {
  const live = { ...oldSite(), 'trainers.html': '<script src="/OTP/assets/trainers-abcdefgh.js"></script>',
    'assets/trainers-abcdefgh.js': 'export default "trainer";' };
  const f = await fixture(t, live);
  const directory = await f.build('fresh', { 'trainers.html': '<html><head></head></html>' });
  const inventory = await f.run(directory);
  assert.equal(inventory.assets.filter((asset) => !asset.current).length, 6);
  assert.equal(await readFile(join(directory, 'assets/viewer-abcdefgh.js'), 'utf8'), live['assets/viewer-abcdefgh.js']);
  assert.ok((await readFile(join(directory, 'index.html'), 'utf8')).includes(MARKER));
  assert.equal(new Set(f.requests).size, f.requests.length, 'each dependency is fetched once');
});

test('several deployments keep entire old graphs, then expire only retired assets', async (t) => {
  const f = await fixture(t, oldSite());
  const first = await f.build('first');
  const firstInventory = await f.run(first);
  await f.publish(first, firstInventory);
  const second = await f.build('second');
  const secondInventory = await f.run(second, { now: NOW + 2 * DAY });
  await f.publish(second, secondInventory);
  const third = await f.build('third');
  const thirdInventory = await f.run(third, { now: NOW + 8 * DAY });
  assert.deepEqual(thirdInventory.assets.map((asset) => asset.path).sort(),
    ['assets/first-12345678.js', 'assets/second-12345678.js', 'assets/third-12345678.js']);
  const firstEntry = thirdInventory.assets.find((asset) => asset.path.startsWith('assets/first-'));
  assert.equal(firstEntry.lastSeenAt, new Date(NOW + 2 * DAY).toISOString());
  assert.equal(thirdInventory.assets.filter((asset) => asset.current).length, 1);
});

test('a deployment still active after months gets a full retirement window', async (t) => {
  const f = await fixture(t, oldSite());
  const first = await f.build('first');
  await f.publish(first, await f.run(first));
  const next = await f.build('next');
  const inventory = await f.run(next, { now: NOW + 90 * DAY });
  const prior = inventory.assets.find((asset) => asset.path === 'assets/first-12345678.js');
  assert.equal(prior.lastSeenAt, new Date(NOW + 90 * DAY).toISOString());
  assert.equal(prior.current, false);
});

test('shared assets keep the newer retirement date without losing old dependencies', async (t) => {
  const f = await fixture(t, oldSite());
  const first = await f.build('first', { 'assets/shared-abcdefgh.js': oldSite()['assets/shared-abcdefgh.js'] });
  await f.publish(first, await f.run(first));
  const next = await f.build('next');
  const inventory = await f.run(next, { now: NOW + 6 * DAY });
  assert.equal(inventory.assets.find((asset) => asset.path === 'assets/shared-abcdefgh.js').lastSeenAt,
    new Date(NOW + 6 * DAY).toISOString());
  assert.ok(inventory.assets.some((asset) => asset.path === 'assets/viewer-abcdefgh.js'));
});

test('missing transitive asset stops publication', async (t) => {
  const live = oldSite();
  delete live['assets/shared-abcdefgh.js'];
  const f = await fixture(t, live);
  await assert.rejects(f.run(await f.build('fresh')), /Cannot preserve deployed assets\/shared-abcdefgh.js: HTTP 404/);
});

test('HTML with status 200 cannot become a retained JavaScript asset', async (t) => {
  const f = await fixture(t, { ...oldSite(), 'assets/shared-abcdefgh.js': new Response('<!doctype html>', { headers: { 'content-type': 'text/html' } }) });
  await assert.rejects(f.run(await f.build('fresh')), /Received HTML instead of a build asset/);
});

test('a missing inventory after migration stops publication', async (t) => {
  const f = await fixture(t, { ...oldSite(), 'index.html': `<head>${MARKER}<script src="/OTP/assets/main-abcdefgh.js"></script></head>` });
  await assert.rejects(f.run(await f.build('fresh')), /inventory disappeared/);
});

test('corrupt retained bytes stop publication', async (t) => {
  const f = await fixture(t, oldSite());
  const first = await f.build('first');
  await f.publish(first, await f.run(first));
  f.live['assets/viewer-abcdefgh.js'] = 'corrupt';
  await assert.rejects(f.run(await f.build('next')), /integrity mismatch/);
});

test('inconsistent cached HTML and inventory stop publication', async (t) => {
  const f = await fixture(t, oldSite());
  const first = await f.build('first');
  await f.publish(first, await f.run(first));
  f.live['index.html'] = oldSite()['index.html'];
  await assert.rejects(f.run(await f.build('next')), /HTML and inventory disagree/);
});

test('size cap fails instead of deleting unexpired modules', async (t) => {
  const f = await fixture(t, oldSite());
  await assert.rejects(f.run(await f.build('fresh'), { maxSiteBytes: 300 }), /exceeds 300 bytes/);
});

test('unsafe inventory paths are rejected before downloading', async (t) => {
  const f = await fixture(t, oldSite());
  const first = await f.build('first');
  const inventory = await f.run(first);
  await f.publish(first, inventory);
  inventory.assets[0].path = 'assets/../../escape.js';
  f.live[INVENTORY] = JSON.stringify(inventory);
  await assert.rejects(f.run(await f.build('next')), /Invalid build asset path/);
  assert.ok(!f.requests.includes('../escape.js'));
});

test('references are confined to hashed same-site assets', () => {
  const source = 'import "./viewer-abcdefgh.js";["assets/shared-abcdefgh.js","https://elsewhere.test/OTP/assets/external-abcdefgh.js","../../../escape-abcdefgh.js","./unhashed.js","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]';
  assert.deepEqual(findAssetReferences(source, `${BASE}assets/main-abcdefgh.js`, BASE),
    ['assets/viewer-abcdefgh.js', 'assets/shared-abcdefgh.js']);
});

test('production default retains assets for a full day after retirement', async (t) => {
  const f = await fixture(t, oldSite());
  const first = await f.build('first');
  await f.publish(first, await f.run(first, { retentionDays: undefined }));
  const second = await f.build('second');
  const inventory = await f.run(second, { now: NOW + DAY + 1, retentionDays: undefined });
  assert.equal(inventory.retentionDays, 1);
  assert.deepEqual(inventory.assets.map((asset) => asset.path), ['assets/first-12345678.js', 'assets/second-12345678.js']);
});

test('mutable files copied from public/assets are excluded from retention', async (t) => {
  const f = await fixture(t, oldSite());
  const first = await f.build('first', { 'assets/1.jpg': 'old image' });
  const inventory = await f.run(first);
  assert.ok(!inventory.assets.some((asset) => asset.path === 'assets/1.jpg'));
  await f.publish(first, inventory);
  const second = await f.build('second', { 'assets/1.jpg': 'new image' });
  await f.run(second);
  assert.equal(await readFile(join(second, 'assets/1.jpg'), 'utf8'), 'new image');
});

test('verified optional cache avoids downloading retained bytes from Pages', async (t) => {
  const f = await fixture(t, oldSite());
  const cacheDir = join(f.root, 'cache');
  const first = await f.build('first');
  const inventory = await f.run(first, { cacheDir });
  await f.publish(first, inventory);
  for (const asset of inventory.assets) delete f.live[asset.path];
  f.requests.length = 0;
  const result = await f.run(await f.build('second'), { cacheDir });
  assert.equal(result.assets.length, inventory.assets.length + 1);
  assert.ok(f.requests.every((path) => !path.startsWith('assets/')));
});

test('cache corruption and eviction fall back to authoritative deployed bytes', async (t) => {
  const f = await fixture(t, oldSite());
  const cacheDir = join(f.root, 'cache');
  const first = await f.build('first');
  const inventory = await f.run(first, { cacheDir });
  await f.publish(first, inventory);
  const corrupt = inventory.assets[0];
  await writeFile(join(cacheDir, corrupt.sha256), Buffer.alloc(corrupt.size, 42));
  const directory = await f.build('second');
  f.requests.length = 0;
  await f.run(directory, { cacheDir });
  assert.ok(f.requests.includes(corrupt.path));
  assert.deepEqual(await readFile(join(directory, corrupt.path)), f.live[corrupt.path]);
  f.requests.length = 0;
  await f.run(await f.build('third'), { cacheDir: join(f.root, 'empty-cache') });
  assert.equal(f.requests.filter((path) => path.startsWith('assets/')).length, inventory.assets.length);
});

test('cache export drops expired digests along with their inventory entries', async (t) => {
  const f = await fixture(t, oldSite());
  const cacheDir = join(f.root, 'cache');
  const first = await f.build('first');
  const inventory = await f.run(first, { cacheDir });
  await f.publish(first, inventory);
  const old = inventory.assets.find((asset) => asset.path === 'assets/viewer-abcdefgh.js');
  await f.run(await f.build('second'), { cacheDir, now: NOW + 8 * DAY });
  await assert.rejects(readFile(join(cacheDir, old.sha256)), { code: 'ENOENT' });
});
