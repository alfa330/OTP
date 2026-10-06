import test from 'node:test';
import assert from 'node:assert/strict';

import {
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

/* Адрес вкладки пишет ОДНА функция — синхронизация адресной строки. Кнопку
   «Ссылка» в шапке раздела владелец убрал 06.10.2026 («они вообще не нужны»),
   и функция, собиравшая ссылку для буфера, ушла вместе с ней; правила самого
   адреса остались те же и проверяются на том, что осталось. */
const synced = (tab, spaceId) => {
  syncWikiTabLink(tab, spaceId);
  return replaced.at(-1).next;
};

test('адрес вкладки строится поверх текущего адреса портала', () => {
  useLocation(PORTAL);
  assert.equal(synced('offices'), '/OTP?view=wiki&tab=offices');
});

test('чужие метки адреса переживают, view переписывается на вики', () => {
  useLocation('https://alfa330.github.io/OTP?view=tasks&task_id=166');
  assert.equal(synced('parks'), '/OTP?view=wiki&task_id=166&tab=parks');
});

test('метки перезагрузки в адрес вкладки не уезжают', () => {
  useLocation(`${PORTAL}&v=1786951163258&auth_reload=1786533936834`);
  assert.equal(synced('audit'), '/OTP?view=wiki&tab=audit');
});

test('чужой ключ адрес не подделывает: метка просто не ставится', () => {
  for (const bad of ['', null, 'admin', '../secret', 'offices2']) {
    useLocation(`${PORTAL}&tab=offices`);
    assert.equal(synced(bad), '/OTP?view=wiki');
  }
});

/* Пространство в адресе — не украшение: вкладки показываются по тумблерам
   пространства, а выбрано у каждого своё. Без метки адрес «Офисов»
   Таксопарков открыл бы офисы той вики, в которой получатель был в прошлый
   раз, — или вкладку, которой там нет вовсе. */
test('пространство уезжает в адрес и читается обратно', () => {
  useLocation(PORTAL);
  const next = synced('offices', 3);
  assert.equal(next, '/OTP?view=wiki&tab=offices&space=3');
  const search = next.slice(next.indexOf('?'));
  assert.equal(readWikiTabFromSearch(search), 'offices');
  assert.equal(readWikiSpaceFromSearch(search), 3);
});

test('без пространства метки нет, битое — та же пустота', () => {
  for (const space of [null, 0, '2; drop']) {
    useLocation(`${PORTAL}&space=7`);
    assert.equal(synced('offices', space), '/OTP?view=wiki&tab=offices');
  }
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
