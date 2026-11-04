import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { readArticleSlugFromHref } from '../src/components/wiki/articleLink.js';

/* Свой домен icorehub.kz (04.11.2026): портал живёт в корне, а ссылки, вставленные в статьи
   со старого адреса alfa330.github.io/OTP, остаются своими. */

const useLocation = (href) => {
  const url = new URL(href);
  globalThis.window = { location: { href: url.toString(), origin: url.origin, pathname: url.pathname, search: url.search } };
};

test('на своём домене ссылка со старого адреса открывается внутри портала', () => {
  useLocation('https://icorehub.kz/?view=wiki');
  assert.equal(readArticleSlugFromHref('https://alfa330.github.io/OTP?view=wiki&article=tarify'), 'tarify');
  assert.equal(readArticleSlugFromHref('https://alfa330.github.io/OTP/?view=wiki&article=tarify'), 'tarify');
  assert.equal(readArticleSlugFromHref('https://icorehub.kz/?view=wiki&article=grafik'), 'grafik');
  assert.equal(readArticleSlugFromHref('?view=wiki&article=grafik'), 'grafik');
});

test('чужие адреса и чужие пути на старом хосте своими не становятся', () => {
  useLocation('https://icorehub.kz/?view=wiki');
  assert.equal(readArticleSlugFromHref('https://alfa330.github.io/other?view=wiki&article=x'), '');
  assert.equal(readArticleSlugFromHref('https://evil.example/OTP?view=wiki&article=x'), '');
  assert.equal(readArticleSlugFromHref('https://icorehub.kz/OTP?view=wiki&article=x'), '');
});

test('на github.io всё как раньше', () => {
  useLocation('https://alfa330.github.io/OTP/?view=wiki');
  assert.equal(readArticleSlugFromHref('https://alfa330.github.io/OTP?view=wiki&article=x'), 'x');
  assert.equal(readArticleSlugFromHref('https://icorehub.kz/?view=wiki&article=x'), '');
});

/* 404.html GitHub Pages сохраняет путь и уводит на корень портала. На github.io корень —
   /OTP/, на своём домене — «/»; переход на «//» был бы адресом без хоста. */
const runFallback = (hostname, pathname) => {
  const html = readFileSync(new URL('../public/404.html', import.meta.url), 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const stored = {};
  const location = { hostname, pathname, search: '?view=wiki&article=a', hash: '#h', replace(target) { this.target = target; } };
  new Function('window', 'sessionStorage', script)({ location }, { setItem: (key, value) => { stored[key] = value; } });
  return { target: location.target, saved: stored.__spa_redirect };
};

test('404.html: на github.io портал остаётся в /OTP/', () => {
  assert.deepEqual(runFallback('alfa330.github.io', '/OTP/wiki/page'),
    { target: '/OTP/', saved: '/wiki/page?view=wiki&article=a#h' });
});

test('404.html: на своём домене портал в корне, без «//»', () => {
  assert.deepEqual(runFallback('icorehub.kz', '/wiki/page'),
    { target: '/', saved: '/wiki/page?view=wiki&article=a#h' });
  assert.deepEqual(runFallback('www.icorehub.kz', '/x'), { target: '/', saved: '/x?view=wiki&article=a#h' });
});

test('подсказка установки не зашивает адрес портала', () => {
  const source = readFileSync(new URL('../src/components/common/InstallGuideSheet.jsx', import.meta.url), 'utf8');
  assert.equal(/alfa330\.github\.io|icorehub\.kz/.test(source.replace(/\/\*[\s\S]*?\*\//g, '')), false);
  assert.match(source, /window\.location\.host/);
});
