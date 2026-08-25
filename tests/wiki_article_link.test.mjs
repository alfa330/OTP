import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildArticleLink,
  buildRelativeArticleLink,
  linkAttrsForSaving,
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
  // Потолок — 255, как VARCHAR(255) у wiki_articles.slug. Стояло 200, и это
  // расходилось со схемой молча (см. отдельную проверку ниже).
  assert.equal(normalizeArticleSlug('a'.repeat(256)), '');
  assert.equal(normalizeArticleSlug(''), '');
  assert.equal(normalizeArticleSlug(null), '');
  // Служебные символы не пускаем ни в каком алфавите.
  assert.equal(normalizeArticleSlug('забытые вещи'), '');
  assert.equal(normalizeArticleSlug('забытые/вещи'), '');
  assert.equal(normalizeArticleSlug('файл.doc'), '');
  assert.equal(normalizeArticleSlug('%D1%81'), '');
});

/* 25 статей из 41 на проде пришли миграцией со СТАРЫМИ, кириллическими
   слагами: проверка на латиницу оставила бы без ссылки почти всю вики. */
test('кириллический слаг — тоже слаг: ссылка есть у статей из старой вики', () => {
  assert.equal(normalizeArticleSlug('структура-отделов'), 'структура-отделов');
  assert.equal(normalizeArticleSlug('справкизаморозки-по-счетам'), 'справкизаморозки-по-счетам');
  // Казахские буквы — тот же случай.
  assert.equal(normalizeArticleSlug('қала-ережелері'), 'қала-ережелері');
});

test('ссылка на статью с кириллическим слагом собирается и читается обратно', () => {
  useLocation(PORTAL);
  const link = buildArticleLink('структура-отделов');
  assert.equal(
    link,
    'https://alfa330.github.io/OTP?view=wiki&article=%D1%81%D1%82%D1%80%D1%83%D0%BA%D1%82%D1%83%D1%80%D0%B0-%D0%BE%D1%82%D0%B4%D0%B5%D0%BB%D0%BE%D0%B2'
  );
  assert.equal(readArticleSlugFromHref(link), 'структура-отделов');
  assert.equal(readArticleSlugFromSearch(new URL(link).search), 'структура-отделов');
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

/* ── Ссылка ДЛЯ ТЕКСТА СТАТЬИ ──────────────────────────────────────────────
   То, что уходит в тело статьи, остаётся в базе навсегда, поэтому оно обязано
   быть относительным: абсолютный адрес забетонировал бы там сегодняшний домен
   фронта (github.io — чужое пространство имён), и после переезда портала все
   внутренние ссылки разом повели бы на старое место. */

test('ссылка для текста статьи — относительная, без домена', () => {
  useLocation(PORTAL);
  assert.equal(buildRelativeArticleLink('tarify-2026'), '?view=wiki&article=tarify-2026');
});

test('кириллица в теле статьи остаётся читаемой, а не уезжает в проценты', () => {
  useLocation(PORTAL);
  assert.equal(buildRelativeArticleLink('тарифы'), '?view=wiki&article=тарифы');
});

test('битый слаг ссылки не даёт', () => {
  useLocation(PORTAL);
  for (const bad of ['', null, 'a/b', 'a.b', 'a%20b']) {
    assert.equal(buildRelativeArticleLink(bad), '');
  }
});

test('относительную ссылку из тела портал узнаёт как свою', () => {
  useLocation(PORTAL);
  assert.equal(readArticleSlugFromHref(buildRelativeArticleLink('тарифы')), 'тарифы');
});

test('слаг длиной до 255 — как VARCHAR(255) у статьи', () => {
  /* Раньше потолок был 200 и молча расходился со схемой: у статьи со слагом
     длиннее ссылка не строилась и не разбиралась, без единой ошибки. */
  assert.equal(normalizeArticleSlug('a'.repeat(255)), 'a'.repeat(255));
  assert.equal(normalizeArticleSlug('a'.repeat(256)), '');
});

/* ── target="_blank" у ВНУТРЕННЕЙ ссылки ──────────────────────────────────
   Расширение Link в TipTap ставит target="_blank" каждой ссылке, а витрина
   ровно на этот признак отказывается открывать статью внутри портала. TipTap
   разбирает тело статьи и собирает обратно, поэтому без правки при первом же
   сохранении признак дописался бы всем 253 внутренним ссылкам, уже лежащим в
   базе: фича сломала бы работающее раньше, чем добавила новое. */

test('своя ссылка сохраняется без target — портал откроет статью сам', () => {
  useLocation(PORTAL);
  assert.deepEqual(
    linkAttrsForSaving({ href: '?view=wiki&article=tarify', target: '_blank', rel: 'noopener noreferrer' }),
    { href: '?view=wiki&article=tarify' }
  );
});

test('чужой ссылке новая вкладка полагается — target остаётся', () => {
  useLocation(PORTAL);
  assert.deepEqual(
    linkAttrsForSaving({ href: 'https://example.com/docs', target: '_blank', rel: 'noopener noreferrer' }),
    { href: 'https://example.com/docs', target: '_blank', rel: 'noopener noreferrer' }
  );
});

test('абсолютная ссылка на свой же портал — тоже своя', () => {
  useLocation(PORTAL);
  const attrs = linkAttrsForSaving({
    href: 'https://alfa330.github.io/OTP?view=wiki&article=tarify', target: '_blank',
  });
  assert.equal(attrs.target, undefined);
});

test('якорь оглавления не трогаем: это не ссылка на статью', () => {
  useLocation(PORTAL);
  assert.equal(linkAttrsForSaving({ href: '#glava-2', target: '_blank' }).target, '_blank');
});

test('прочие атрибуты ссылки переживают правку, исходный объект не портится', () => {
  useLocation(PORTAL);
  const original = { href: '?article=tarify', target: '_blank', class: 'x' };
  const attrs = linkAttrsForSaving(original);
  assert.equal(attrs.class, 'x');
  assert.equal(original.target, '_blank', 'входной объект менять нельзя');
});
