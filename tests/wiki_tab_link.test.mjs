import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildWikiTabLink,
  normalizeWikiSpaceId,
  normalizeWikiTab,
  readWikiSpaceFromSearch,
  readWikiTabFromSearch,
  syncWikiTabLink,
  WIKI_TAB_KEYS,
} from '../src/components/wiki/tabLink.js';

/* Портал живёт на GitHub Pages с базовым путём /OTP — ссылка обязана строиться
   поверх него, а не от корня домена (то же правило, что у ссылки на статью). */
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

test('ссылка на вкладку строится поверх текущего адреса портала', () => {
  useLocation(PORTAL);
  assert.equal(
    buildWikiTabLink('offices'),
    'https://alfa330.github.io/OTP?view=wiki&tab=offices'
  );
});

test('ссылку строим и из другого раздела портала: view переписывается на вики', () => {
  useLocation('https://alfa330.github.io/OTP?view=tasks&task_id=166');
  assert.equal(
    buildWikiTabLink('parks'),
    'https://alfa330.github.io/OTP?view=wiki&task_id=166&tab=parks'
  );
});

test('метки перезагрузки в ссылку не уезжают', () => {
  useLocation(`${PORTAL}&v=1786951163258&auth_reload=1786533936834`);
  assert.equal(
    buildWikiTabLink('audit'),
    'https://alfa330.github.io/OTP?view=wiki&tab=audit'
  );
});

/* Ссылку на вкладку копируют из шапки раздела, и открытая в этот момент статья
   к обещанию «вот раздел Офисы» отношения не имеет: с ней получатель попал бы
   в чужой текст вместо вкладки. */
test('открытая статья в ссылку на вкладку не уезжает', () => {
  useLocation('https://alfa330.github.io/OTP?view=wiki&article=tarify-2026');
  assert.equal(
    buildWikiTabLink('offices'),
    'https://alfa330.github.io/OTP?view=wiki&tab=offices'
  );
});

/* Главная — вкладка по умолчанию: '?view=wiki' и так открывает витрину, и
   '&tab=library' был бы лишними буквами в каждой скопированной ссылке. */
test('у главной метки вкладки нет — адрес остаётся коротким', () => {
  useLocation(`${PORTAL}&tab=offices`);
  assert.equal(buildWikiTabLink('library'), 'https://alfa330.github.io/OTP?view=wiki');
});

test('чужой ключ ссылки не подделывает: метка просто не ставится', () => {
  useLocation(`${PORTAL}&tab=offices`);
  for (const bad of ['', null, 'admin', '../secret', 'offices2']) {
    assert.equal(buildWikiTabLink(bad), 'https://alfa330.github.io/OTP?view=wiki');
  }
});

/* Пространство в ссылке — не украшение: вкладки показываются по тумблерам
   пространства, а выбрано у каждого своё. Без метки ссылка на «Офисы»
   Таксопарков открыла бы офисы той вики, в которой получатель был в прошлый
   раз, — или вкладку, которой там нет вовсе. */
test('пространство уезжает в ссылку и читается обратно', () => {
  useLocation(PORTAL);
  const link = buildWikiTabLink('offices', 3);
  assert.equal(link, 'https://alfa330.github.io/OTP?view=wiki&tab=offices&space=3');
  assert.equal(readWikiTabFromSearch(new URL(link).search), 'offices');
  assert.equal(readWikiSpaceFromSearch(new URL(link).search), 3);
});

test('без пространства метки нет, битое — та же пустота', () => {
  useLocation(PORTAL);
  assert.equal(buildWikiTabLink('offices', null), 'https://alfa330.github.io/OTP?view=wiki&tab=offices');
  assert.equal(buildWikiTabLink('offices', 0), 'https://alfa330.github.io/OTP?view=wiki&tab=offices');
  assert.equal(buildWikiTabLink('offices', '2; drop'), 'https://alfa330.github.io/OTP?view=wiki&tab=offices');
});

test('открытая вкладка попадает в адресную строку, главная — убирает метку', () => {
  useLocation(`${PORTAL}&v=1`);
  syncWikiTabLink('offices');
  assert.equal(replaced.at(-1).next, '/OTP?view=wiki&tab=offices');
  // Состояние истории переносим как есть: роутер хранит в нём свой ключ.
  assert.deepEqual(replaced.at(-1).state, { key: 'portal' });

  useLocation('https://alfa330.github.io/OTP?view=wiki&tab=offices');
  syncWikiTabLink('library');
  assert.equal(replaced.at(-1).next, '/OTP?view=wiki');
});

/* Метку статьи ставит и снимает витрина (articleLink.js). Синхронизация вкладки
   обязана пройти мимо неё: иначе первый же рендер раздела погасил бы адрес
   открытой статьи. */
test('адрес открытой статьи синхронизация вкладки не трогает', () => {
  useLocation('https://alfa330.github.io/OTP?view=wiki&article=tarify-2026');
  syncWikiTabLink('library');
  assert.equal(replaced.at(-1).next, '/OTP?view=wiki&article=tarify-2026');
});

test('хэш адреса переживает переключение вкладки', () => {
  useLocation('https://alfa330.github.io/OTP?view=wiki#wiki-h-2');
  syncWikiTabLink('parks');
  assert.equal(replaced.at(-1).next, '/OTP?view=wiki&tab=parks#wiki-h-2');
});

test('вкладку из строки запроса читаем без окна браузера', () => {
  assert.equal(readWikiTabFromSearch('?view=wiki&tab=analytics'), 'analytics');
  assert.equal(readWikiTabFromSearch('?view=wiki'), '');
  assert.equal(readWikiTabFromSearch('?tab=../secret'), '');
  assert.equal(readWikiTabFromSearch(''), '');
});

test('набор ключей закрыт: из адреса приезжает что угодно', () => {
  for (const key of WIKI_TAB_KEYS) assert.equal(normalizeWikiTab(key), key);
  assert.equal(normalizeWikiTab(' offices '), 'offices');
  assert.equal(normalizeWikiTab('structure'), '');   // половина вкладки, не вкладка
  assert.equal(normalizeWikiTab('<script>'), '');
  assert.equal(normalizeWikiTab(undefined), '');
});

test('пространство — только целое положительное', () => {
  assert.equal(normalizeWikiSpaceId('7'), 7);
  assert.equal(normalizeWikiSpaceId(7), 7);
  assert.equal(normalizeWikiSpaceId('0'), 0);
  assert.equal(normalizeWikiSpaceId('-3'), 0);
  assert.equal(normalizeWikiSpaceId('1.5'), 0);
  assert.equal(normalizeWikiSpaceId('abc'), 0);
  assert.equal(normalizeWikiSpaceId(null), 0);
});
