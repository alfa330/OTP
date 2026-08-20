import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildArticleLink,
  normalizeArticleSlug,
  readArticleSlugFromHref,
  readArticleSlugFromSearch,
  syncArticleDeepLink,
} from '../src/components/wiki/articleLink.js';

/* Портал живёт на GitHub Pages с базовым путём /OTP — ссылка обязана строиться
   поверх него, а не от корня домена. */
const PORTAL = 'https://alfa330.github.io/OTP?view=wiki';

const replaced = [];

const useLocation = (href) => {
  const url = new URL(href);
  globalThis.window = {
    location: {
      href: url.toString(),
      origin: url.origin,
      pathname: url.pathname,
      search: url.search,
    },
    history: {
      state: { key: 'portal' },
      replaceState: (state, title, next) => replaced.push({ state, next }),
    },
  };
  replaced.length = 0;
};

test('ссылка на статью строится поверх текущего адреса портала', () => {
  useLocation(PORTAL);
  assert.equal(
    buildArticleLink('tarify-2026'),
    'https://alfa330.github.io/OTP?view=wiki&article=tarify-2026'
  );
});

test('метки перезагрузки в ссылку не уезжают', () => {
  useLocation(`${PORTAL}&v=1786951163258&auth_reload=1786533936834`);
  assert.equal(
    buildArticleLink('tarify-2026'),
    'https://alfa330.github.io/OTP?view=wiki&article=tarify-2026'
  );
});

test('ссылку строим и из другого раздела портала: view переписывается на вики', () => {
  useLocation('https://alfa330.github.io/OTP?view=tasks&task_id=166');
  assert.equal(
    buildArticleLink('tarify-2026'),
    'https://alfa330.github.io/OTP?view=wiki&task_id=166&article=tarify-2026'
  );
});

test('слаг проверяем: значение уходит в путь запроса к API', () => {
  assert.equal(normalizeArticleSlug('klassifikator-avto'), 'klassifikator-avto');
  assert.equal(normalizeArticleSlug('  auto_list  '), 'auto_list');
  assert.equal(normalizeArticleSlug('../../api/admin/users'), '');
  assert.equal(normalizeArticleSlug('тарифы'), '');
  assert.equal(normalizeArticleSlug('a'.repeat(201)), '');
  assert.equal(normalizeArticleSlug(''), '');
  assert.equal(normalizeArticleSlug(null), '');
});

test('битый слаг ссылки не даёт — вместо неё пустая строка', () => {
  useLocation(PORTAL);
  assert.equal(buildArticleLink('те же тарифы'), '');
  assert.equal(buildArticleLink(''), '');
});

test('открытая статья попадает в адресную строку, закрытая — уходит из неё', () => {
  useLocation(`${PORTAL}&v=1`);
  syncArticleDeepLink('tarify-2026');
  assert.equal(replaced.at(-1).next, '/OTP?view=wiki&article=tarify-2026');
  // Состояние истории переносим как есть: роутер хранит в нём свой ключ.
  assert.deepEqual(replaced.at(-1).state, { key: 'portal' });

  useLocation('https://alfa330.github.io/OTP?view=wiki&article=tarify-2026');
  syncArticleDeepLink(null);
  assert.equal(replaced.at(-1).next, '/OTP?view=wiki');
});

test('хэш адреса переживает и открытие, и закрытие статьи', () => {
  useLocation('https://alfa330.github.io/OTP?view=wiki#wiki-h-2');
  syncArticleDeepLink('tarify-2026');
  assert.equal(replaced.at(-1).next, '/OTP?view=wiki&article=tarify-2026#wiki-h-2');
});

test('ссылка внутри текста статьи разбирается: свой портал — свой слаг', () => {
  useLocation('https://alfa330.github.io/OTP?view=wiki&article=tarify-2026');
  assert.equal(readArticleSlugFromHref('?view=wiki&article=grafik-raboty'), 'grafik-raboty');
  assert.equal(
    readArticleSlugFromHref('https://alfa330.github.io/OTP?view=wiki&article=grafik-raboty'),
    'grafik-raboty'
  );
  // Без view — тоже наша ссылка: метка раздела могла потеряться при пересылке.
  assert.equal(readArticleSlugFromHref('?article=grafik-raboty'), 'grafik-raboty');
  assert.equal(readArticleSlugFromHref('https://alfa330.github.io/OTP/?article=grafik-raboty'), 'grafik-raboty');
});

test('чужая ссылка остаётся чужой — её открывает браузер, а не витрина', () => {
  useLocation('https://alfa330.github.io/OTP?view=wiki&article=tarify-2026');
  assert.equal(readArticleSlugFromHref('https://example.com/OTP?article=grafik-raboty'), '');
  assert.equal(readArticleSlugFromHref('https://alfa330.github.io/other?article=grafik'), '');
  assert.equal(readArticleSlugFromHref('?view=tasks&task_id=166'), '');
  assert.equal(readArticleSlugFromHref('#wiki-h-2'), '');
  assert.equal(readArticleSlugFromHref('mailto:hr@example.com'), '');
  assert.equal(readArticleSlugFromHref(''), '');
});

test('слаг из строки запроса читаем без окна браузера', () => {
  assert.equal(readArticleSlugFromSearch('?view=wiki&article=tarify-2026'), 'tarify-2026');
  assert.equal(readArticleSlugFromSearch('?view=wiki'), '');
  assert.equal(readArticleSlugFromSearch('?article=../secret'), '');
  assert.equal(readArticleSlugFromSearch(''), '');
});
