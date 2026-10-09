import { createHash, randomUUID } from 'node:crypto';
import { lstat, mkdir, readdir, readFile, stat, unlink, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

// Pages replaces the entire site on deployment. Keep immutable build assets from
// recently retired builds alongside the new build, including their dependencies.
// The inventory lives on Pages itself: an evicted Actions cache or expired build
// artifact must never silently discard files that open browser tabs still need.
export const INVENTORY = 'pages-assets.json';
export const MARKER = '<meta name="icore-asset-inventory" content="pages-assets.json">';
const DAY = 24 * 60 * 60 * 1000;
const SHA256 = /^[a-f0-9]{64}$/;
const SAFE_ASSET = /^assets\/(?:[a-zA-Z0-9_-]+\/)*[a-zA-Z0-9_@.-]+$/;
const HASHED_FILE = /-[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9.]+$/;
const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');

function assertAssetPath(path) {
  if (typeof path !== 'string' || !SAFE_ASSET.test(path) || path.split('/').some((part) => part === '.' || part === '..')) {
    throw new Error(`Invalid build asset path: ${path}`);
  }
}

export function findAssetReferences(source, sourceUrl, siteUrl) {
  const base = new URL(siteUrl);
  const assetPrefix = `${base.pathname}assets/`;
  const candidates = [
    ...Array.from(source.matchAll(/["'`]([^"'`\s<>]+)["'`]/g), (match) => match[1])
      .filter((path) => /^(?:\.{1,2}\/|\/|assets\/|https?:\/\/)/.test(path)),
    ...Array.from(source.matchAll(/url\(\s*["']?([^\s"')]+)["']?\s*\)/g), (match) => match[1]),
  ];
  const paths = new Set();
  for (const candidate of candidates) {
    if (!HASHED_FILE.test(candidate.split(/[?#]/)[0]) || candidate.includes('\\')) continue;
    let url;
    try {
      // Vite's preload dependency table uses "assets/name-hash.js" even inside
      // an assets/*.js module. Static imports use "./name-hash.js" instead.
      url = new URL(candidate, candidate.startsWith('assets/') ? base : sourceUrl);
    } catch { continue; }
    if (url.origin !== base.origin || !url.pathname.startsWith(assetPrefix)) continue;
    const path = url.pathname.slice(base.pathname.length);
    assertAssetPath(path);
    paths.add(path);
  }
  return [...paths];
}

async function walkFiles(directory, prefix = '') {
  const result = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isSymbolicLink()) throw new Error(`Symlink in Pages output: ${relative}`);
    if (entry.isDirectory()) result.push(...await walkFiles(join(directory, entry.name), relative));
    else if (entry.isFile()) result.push(relative);
  }
  return result;
}

async function parallel(items, task, concurrency) {
  let cursor = 0;
  let failed = false;
  const results = await Promise.allSettled(Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    try {
      while (!failed && cursor < items.length) await task(items[cursor++]);
    } catch (error) { failed = true; throw error; }
  }));
  const rejection = results.find((result) => result.status === 'rejected');
  if (rejection) throw rejection.reason;
}

function validateInventory(inventory) {
  if (inventory?.version !== 1 || !Array.isArray(inventory.assets) || inventory.assets.length > 50000) {
    throw new Error('Invalid deployed asset inventory');
  }
  const seen = new Set();
  for (const asset of inventory.assets) {
    assertAssetPath(asset.path);
    if (seen.has(asset.path) || !SHA256.test(asset.sha256) || !Number.isSafeInteger(asset.size) || asset.size < 0
        || !Number.isFinite(Date.parse(asset.lastSeenAt)) || typeof asset.current !== 'boolean') {
      throw new Error(`Invalid deployed inventory entry: ${asset.path}`);
    }
    seen.add(asset.path);
  }
  if (!inventory.assets.some((asset) => asset.current)) throw new Error('Deployed inventory has no current assets');
  return inventory;
}

export async function preservePagesAssets({
  // At 15-37 releases/day even the 4 MiB main module alone exceeds Pages'
  // 1 GiB limit in a week. One full day covers shifts across many releases;
  // older tabs use the application's bounded stale-bundle recovery instead.
  distDir = 'dist', siteUrl, cacheDir, now = Date.now(), retentionDays = 1,
  maxSiteBytes = 850 * 1024 * 1024, concurrency = 8, retries = 3,
  fetchImpl = fetch, sleep = (ms) => new Promise((done) => setTimeout(done, ms)),
  log = console.log,
}) {
  if (typeof siteUrl !== 'string' || !siteUrl) throw new Error('PAGES_BASE_URL is required');
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 32 || !Number.isInteger(retries) || retries < 1 || retries > 5) {
    throw new Error('Invalid download limits');
  }
  const root = resolve(distDir);
  const cacheRoot = cacheDir ? resolve(cacheDir) : null;
  const cacheRelative = cacheRoot ? relative(root, cacheRoot) : null;
  if (cacheRoot && !isAbsolute(cacheRelative) && !cacheRelative.startsWith(`..${sep}`)) throw new Error('Asset cache must be outside published dist');
  const base = new URL(siteUrl.endsWith('/') ? siteUrl : `${siteUrl}/`);
  if (!['https:', 'http:'].includes(base.protocol) || base.search || base.hash || base.username || base.password) {
    throw new Error('Expected a public Pages base URL without credentials or query');
  }
  if (!(retentionDays > 0) || !Number.isSafeInteger(maxSiteBytes) || maxSiteBytes <= 0) throw new Error('Invalid retention limits');
  const timestamp = new Date(now).toISOString();
  const files = await walkFiles(root);
  const entrypoints = files.filter((file) => !file.includes('/') && file.endsWith('.html') && file !== '404.html' && file !== 'offline.html');
  if (!entrypoints.includes('index.html')) throw new Error('Missing built index.html');
  const current = new Map();
  let totalBytes = 0;
  for (const path of files) {
    totalBytes += (await stat(join(root, path))).size;
    // public/assets also contains mutable files such as 1.jpg. Only Vite's
    // content-hashed output can be carried forward under an immutable URL.
    if (!path.startsWith('assets/') || !HASHED_FILE.test(path)) continue;
    assertAssetPath(path);
    const bytes = await readFile(join(root, path));
    current.set(path, { path, size: bytes.length, sha256: digest(bytes), current: true, lastSeenAt: timestamp });
  }
  if (!current.size) throw new Error('No built assets to publish');
  const checkSize = () => {
    if (totalBytes > maxSiteBytes) throw new Error(`Pages output exceeds ${maxSiteBytes} bytes; refusing to remove still-retained assets`);
  };
  checkSize();

  async function request(path, { optional = false, kind = 'asset' } = {}) {
    let lastError;
    for (let attempt = 0; attempt < retries; attempt++) {
      const url = new URL(path, base);
      url.searchParams.set('__asset_retention', randomUUID());
      try {
        const response = await fetchImpl(url, {
          cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(30000),
          headers: { 'Cache-Control': 'no-cache' },
        });
        if (optional && response.status === 404) return null;
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const declaredSize = Number(response.headers.get('content-length') || 0);
        if (declaredSize > maxSiteBytes) throw new Error('Response exceeds site size limit');
        const contentType = response.headers.get('content-type') || '';
        if (kind === 'asset' && /text\/html/i.test(contentType)) throw new Error('Received HTML instead of a build asset');
        if (kind === 'json' && !/application\/json/i.test(contentType)) throw new Error('Received non-JSON asset inventory');
        const bytes = Buffer.from(await response.arrayBuffer());
        if (bytes.length > maxSiteBytes) throw new Error('Response exceeds site size limit');
        if (kind === 'asset' && /\.(?:m?js|css)$/.test(path) && /^\s*<(?:!doctype|html)/i.test(bytes.toString('utf8', 0, 100))) {
          throw new Error('Received HTML instead of a build asset');
        }
        return bytes;
      } catch (error) {
        lastError = error;
        if (attempt + 1 < retries) await sleep(500 * (attempt + 1));
      }
    }
    throw new Error(`Cannot preserve deployed ${path}: ${lastError?.message}`, { cause: lastError });
  }

  // Read live entrypoints even when there is an inventory, so an inconsistent
  // CDN response cannot publish a graph missing the current HTML's imports.
  const liveEntrypoints = new Map();
  await parallel(entrypoints, async (path) => {
    const bytes = await request(path, { optional: path !== 'index.html', kind: 'html' });
    if (bytes) liveEntrypoints.set(path, bytes.toString('utf8'));
  }, concurrency);
  const inventoryBytes = await request(INVENTORY, { optional: true, kind: 'json' });
  if (!inventoryBytes && [...liveEntrypoints.values()].some((html) => html.includes('name="icore-asset-inventory"'))) {
    throw new Error('Deployed asset inventory disappeared; refusing to discard retained builds');
  }
  const liveRoots = new Set([...liveEntrypoints].flatMap(([path, html]) => findAssetReferences(html, new URL(path, base), base)));
  if (!liveRoots.size) throw new Error('Cannot find build assets in deployed HTML');
  const retained = new Map();
  let cacheHits = 0;

  async function loadPrevious(asset) {
    if (cacheRoot) {
      try {
        const path = join(cacheRoot, asset.sha256);
        const info = await lstat(path);
        if (info.isFile() && info.size === asset.size) {
          const bytes = await readFile(path);
          if (digest(bytes) === asset.sha256) { cacheHits++; return bytes; }
        }
      } catch { /* Cache is an optional accelerator; Pages remains authoritative. */ }
    }
    return request(asset.path);
  }

  async function savePrevious(path, bytes, previous) {
    const sha256 = digest(bytes);
    if (previous && (previous.size !== bytes.length || previous.sha256 !== sha256)) {
      throw new Error(`Deployed asset integrity mismatch: ${path}`);
    }
    const fresh = current.get(path);
    if (fresh && fresh.sha256 !== sha256) throw new Error(`Immutable build asset changed contents: ${path}`);
    if (!fresh) {
      totalBytes += bytes.length;
      checkSize();
      await mkdir(dirname(join(root, path)), { recursive: true });
      await writeFile(join(root, path), bytes);
      retained.set(path, { path, size: bytes.length, sha256, current: false, lastSeenAt: previous?.lastSeenAt || timestamp });
    }
  }

  if (inventoryBytes) {
    const inventory = validateInventory(JSON.parse(inventoryBytes.toString('utf8')));
    const previousByPath = new Map(inventory.assets.map((asset) => [asset.path, asset]));
    for (const path of liveRoots) {
      // Fail rather than trust mismatched cached HTML/inventory generations.
      if (!previousByPath.get(path)?.current) throw new Error(`Deployed HTML and inventory disagree: ${path}`);
    }
    const cutoff = now - retentionDays * DAY;
    const keep = inventory.assets.filter((asset) => asset.current || Date.parse(asset.lastSeenAt) >= cutoff);
    if (totalBytes + keep.reduce((bytes, asset) => bytes + (current.has(asset.path) ? 0 : asset.size), 0) > maxSiteBytes) {
      throw new Error(`Pages output exceeds ${maxSiteBytes} bytes; refusing to remove still-retained assets`);
    }
    await parallel(keep, async (asset) => {
      const previous = { ...asset, lastSeenAt: asset.current ? timestamp : asset.lastSeenAt };
      const fresh = current.get(asset.path);
      if (fresh) {
        if (fresh.sha256 !== asset.sha256 || fresh.size !== asset.size) throw new Error(`Immutable build asset changed contents: ${asset.path}`);
        return;
      }
      await savePrevious(asset.path, await loadPrevious(asset), previous);
    }, concurrency);
    log(`Restored ${retained.size} deployed assets (${cacheHits} verified cache hits); expired ${inventory.assets.length - keep.length} inventory entries.`);
  } else {
    // One-time migration for deployments predating the inventory. Traverse all
    // Vite imports/preload tables and CSS URLs, not just the modules in HTML.
    const visited = new Set();
    let pending = [...liveRoots];
    while (pending.length) {
      const batch = pending.filter((path) => !visited.has(path));
      pending = [];
      batch.forEach((path) => visited.add(path));
      if (visited.size > 50000) throw new Error('Deployed asset graph exceeds inventory limit');
      await parallel(batch, async (path) => {
        const bytes = await request(path);
        await savePrevious(path, bytes);
        if (/\.(?:m?js|css)$/.test(path)) {
          pending.push(...findAssetReferences(bytes.toString('utf8'), new URL(path, base), base));
        }
      }, concurrency);
      pending = [...new Set(pending)];
    }
    log(`Bootstrapped ${visited.size} assets from the currently published module graph.`);
  }

  const assets = [...current.values(), ...retained.values()].sort((left, right) => left.path.localeCompare(right.path));
  const inventory = { version: 1, generatedAt: timestamp, retentionDays, assets };
  const inventoryText = `${JSON.stringify(inventory)}\n`;
  totalBytes += Buffer.byteLength(inventoryText);
  for (const path of entrypoints) {
    const html = await readFile(join(root, path), 'utf8');
    if (!html.includes('</head>')) throw new Error(`Cannot mark built HTML: ${path}`);
    const marked = html.replace('</head>', `    ${MARKER}\n  </head>`);
    totalBytes += Buffer.byteLength(marked) - Buffer.byteLength(html);
    await writeFile(join(root, path), marked);
  }
  checkSize();
  await writeFile(join(root, INVENTORY), inventoryText);
  if (cacheRoot) {
    try {
      await mkdir(cacheRoot, { recursive: true });
      const hashes = new Set(assets.map((asset) => asset.sha256));
      await parallel(assets, async (asset) => {
        // Files are keyed by full SHA-256, then verified against the live
        // inventory before reuse. A stale/missing cache never changes retention.
        const target = join(cacheRoot, asset.sha256);
        const info = await lstat(target).catch(() => null);
        if (info && !info.isFile()) throw new Error('Non-regular file in asset cache');
        await writeFile(target, await readFile(join(root, asset.path)));
      }, concurrency);
      for (const entry of await readdir(cacheRoot, { withFileTypes: true })) {
        // Delete only individual known cache files, never a directory tree.
        if (entry.isFile() && SHA256.test(entry.name) && !hashes.has(entry.name)) await unlink(join(cacheRoot, entry.name));
      }
    } catch (error) { log(`Optional asset cache could not be updated: ${error.message}`); }
  }
  log(`Pages output: ${current.size} current + ${retained.size} retained assets, ${(totalBytes / 1024 / 1024).toFixed(1)} MiB.`);
  return inventory;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  preservePagesAssets({ distDir: process.argv[2] || 'dist', siteUrl: process.env.PAGES_BASE_URL || process.argv[3], cacheDir: process.env.PAGES_ASSET_CACHE_DIR })
    .catch((error) => { console.error(error); process.exitCode = 1; });
}
