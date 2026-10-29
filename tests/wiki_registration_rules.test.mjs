/*
 * Регистрация водителей в карточке города и в поиске (просьба владельца
 * 29.10.2026): registrationRules.js, CityRegistration.jsx, строка поиска.
 * Сервер — wiki/registration.py (tests/test_wiki_registration.py).
 *
 * Что стережётся:
 *   1. ПАДЕЖ. «Регистрируем в Костанае», «в Рудном», «в Караганде», а
 *      несклоняемые (Алматы, Кокшетау) — как есть.
 *   2. ПЕРВАЯ СТРОКА — ОТВЕТ. Город, куда регистрировать, стоит в шапке блока;
 *      цепочка «если у парка нет…» — подписью, а не вторым заголовком.
 *   3. ДИСПЕТЧЕРСКИЕ БЕЗ ПЕРЕВРАННЫХ НАЗВАНИЙ. Название — как в кабинете, город
 *      кабинета — только когда он другой («Честный Темиртау» → Караганда).
 *   4. СВЁРНУТОЕ. Семнадцать парков Алматы не превращают карточку в список.
 *   5. ПОИСК. Строка «Регистрация» ведёт во вкладку «Города» на город водителя.
 *
 * JSX здесь не используется намеренно: node --test гоняет .mjs без сборки.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');
const { mkdirSync } = require('node:fs');
const { join } = require('node:path');
const { fileURLToPath } = require('node:url');

async function load(name, file) {
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, `${name}.mjs`);
  buildSync({
    entryPoints: [fileURLToPath(new URL(file, import.meta.url))],
    bundle: true,
    format: 'esm',
    target: 'node18',
    outfile,
    external: ['react', 'react-dom', 'axios', 'lucide-react', 'framer-motion'],
    loader: { '.jsx': 'jsx' },
    logLevel: 'silent',
  });
  return import(`file://${outfile.replace(/\\/g, '/')}`);
}

const rules = await load('registrationRules', '../src/components/wiki/registrationRules.js');
const { default: CityRegistration } = await load(
  'CityRegistration', '../src/components/wiki/CityRegistration.jsx');
const { directoryText, directoryTab, directoryKey } = await load(
  'searchDirectoryRegistration', '../src/components/wiki/searchDirectory.jsx');

const SATELLITE = {
  state: 'elsewhere', own_count: 0, targets: [{ id: 11, name: 'Костанай' }],
  parks: [
    { key: 'p:1', name: 'iTaxi', park_id: 1, own: false, city: { id: 11, name: 'Костанай' },
      dispatchers: [{ id: 5, name: 'iTaxi', fleet_city: 'Костанай', fleet_city_other: null }] },
    { key: 'p:10', name: 'Аманат', park_id: 10, own: false, city: { id: 11, name: 'Костанай' },
      dispatchers: [{ id: 6, name: 'Аманат', fleet_city: 'Костанай', fleet_city_other: null }] },
  ],
  absent: ['Jana такси', 'Global', 'Qazaq', 'Taxi24', 'Честный', 'Халык'],
  feeders: [],
  source: { count: 89, synced_at: '2026-10-29T05:20:00', fleet_checked_at: '2026-10-28T19:47:17' },
};

/* ------------------------------------------------------------- падежи */

test('предложный падеж городов страны', () => {
  const cases = {
    'Костанай': 'Костанае', 'Рудный': 'Рудном', 'Караганда': 'Караганде', 'Астана': 'Астане',
    'Шымкент': 'Шымкенте', 'Усть-Каменогорск': 'Усть-Каменогорске', 'Семей': 'Семее',
    'Алматы': 'Алматы', 'Кокшетау': 'Кокшетау', 'Актобе': 'Актобе', 'Туркестан': 'Туркестане',
  };
  for (const [name, expected] of Object.entries(cases)) {
    assert.equal(rules.cityLocative(name), expected, name);
  }
  assert.equal(rules.dispatcherWord(1), 'диспетчерская');
  assert.equal(rules.dispatcherWord(3), 'диспетчерские');
  assert.equal(rules.dispatcherWord(12), 'диспетчерских');
  assert.equal(rules.dispatcherWord(21), 'диспетчерская');
});

/* ------------------------------------------------------------- шапка блока */

test('шапка: город регистрации первым, своё «нет» — подписью', () => {
  assert.deepEqual(rules.registrationHeadline('Рудный', SATELLITE), {
    tone: 'elsewhere', title: 'Регистрируем в Костанае', subtitle: 'Своих диспетчерских в Рудном нет',
  });
  const chain = rules.registrationHeadline('Кентау', {
    ...SATELLITE, targets: [{ id: 20, name: 'Туркестан' }, { id: 23, name: 'Шымкент' }],
  });
  assert.equal(chain.title, 'Регистрируем в Туркестане');
  assert.equal(chain.subtitle,
    'Своих диспетчерских в Кентау нет · если у парка нет диспетчерской в Туркестане — в Шымкенте');
  assert.deepEqual(rules.registrationHeadline('Костанай', { state: 'own', own_count: 3 }), {
    tone: 'own', title: 'Регистрируем в Костанае', subtitle: '3 диспетчерские в городе',
  });
  assert.equal(rules.registrationHeadline('Аксай', { state: 'unset', targets: [] }).tone, 'unset');
  assert.equal(rules.registrationHeadline('Аксай', null), null);
});

test('смешанный город: своя диспетчерская не у всех — оба города в шапке', () => {
  const block = {
    state: 'mixed', own_count: 1, targets: [{ id: 9, name: 'Караганда' }],
    parks: [
      { key: 'p:1', name: 'iTaxi', own: false, city: { id: 9, name: 'Караганда' }, dispatchers: [] },
      { key: 'p:4', name: 'Честный', own: true, city: { id: 19, name: 'Темиртау' }, dispatchers: [] },
      { key: 'p:10', name: 'Аманат', own: false, city: { id: 9, name: 'Караганда' }, dispatchers: [] },
    ],
  };
  assert.deepEqual(rules.otherCities(block), ['Караганда']);
  assert.deepEqual(rules.registrationHeadline('Темиртау', block), {
    tone: 'mixed',
    title: 'Регистрируем в Темиртау и Караганде',
    subtitle: 'Своя диспетчерская — у 1 парка, у остальных — в Караганде',
  });
});

test('подписи: город кабинета, кто регистрируется сюда, кого нет', () => {
  assert.equal(rules.dispatcherNote({ name: 'Честный Темиртау', fleet_city_other: 'Караганда' }),
    'в кабинете — Караганда');
  assert.equal(rules.dispatcherNote({ name: 'iTaxi', fleet_city_other: null }), '');
  assert.equal(rules.feedersText([{ name: 'Темиртау' }, { name: 'Жезказган' }]),
    'Сюда же регистрируют водителей из Темиртау и Жезказгана');
  assert.equal(rules.feedersText([]), '');
  assert.equal(rules.absentText('Рудный', SATELLITE.absent),
    'Нет диспетчерской для Рудного: Jana такси, Global, Qazaq, Taxi24 и ещё 2');
});

test('семнадцать парков сворачиваются, один лишний — нет', () => {
  const parks = Array.from({ length: 17 }, (_, index) => ({ key: `p:${index}` }));
  assert.deepEqual(
    [rules.parksPreview(parks, false).shown.length, rules.parksPreview(parks, false).hidden],
    [rules.PARKS_PREVIEW, 17 - rules.PARKS_PREVIEW]);
  assert.equal(rules.parksPreview(parks, true).hidden, 0);
  // Кнопка «Ещё 1» вместо одной строки — хуже самой строки.
  assert.equal(rules.parksPreview(parks.slice(0, rules.PARKS_PREVIEW + 1), false).hidden, 0);
});

/* ------------------------------------------------------------- блок карточки */

test('блок карточки: ответ, парки, кого нет и откуда список', () => {
  const html = renderToStaticMarkup(React.createElement(CityRegistration, {
    city: { id: 15, name: 'Рудный', registration: SATELLITE },
  }));
  assert.match(html, /Регистрация водителей/);
  assert.match(html, /Регистрируем в Костанае/);
  assert.match(html, /Своих диспетчерских в Рудном нет/);
  assert.match(html, />iTaxi</);
  assert.match(html, />Аманат</);
  assert.match(html, /Нет диспетчерской для Рудного: Jana такси/);
  // Дата и время — одним неразрывным куском: на телефоне «в / 11:49» рвалось.
  assert.match(html, /из кабинета Fleet <span class="whitespace-nowrap">на 28 октября в 19:47<\/span>/);
  // Другой город строки виден у каждого парка — «Рудный → Костанай».
  assert.equal((html.match(/Костанай/g) || []).length >= 2, true);
});

test('блока нет без данных; «Задать» — только тому, кто правит', () => {
  assert.equal(renderToStaticMarkup(React.createElement(CityRegistration, {
    city: { id: 1, name: 'Алматы' } })), '');
  const unset = { state: 'unset', own_count: 0, targets: [], parks: [], absent: [], feeders: [] };
  const reader = renderToStaticMarkup(React.createElement(CityRegistration, {
    city: { id: 1, name: 'Аксай', registration: unset }, onEdit: () => {} }));
  assert.doesNotMatch(reader, /Задать/);
  const manager = renderToStaticMarkup(React.createElement(CityRegistration, {
    city: { id: 1, name: 'Аксай', registration: unset }, canManage: true, onEdit: () => {} }));
  assert.match(manager, /Задать/);
  assert.match(manager, /Куда регистрировать — не задано/);
});

test('город кабинета у диспетчерской — когда он другой', () => {
  const block = {
    state: 'own', own_count: 1, targets: [{ id: 9, name: 'Караганда' }], feeders: [], absent: [],
    parks: [{ key: 'p:4', name: 'Честный', park_id: 4, own: true, city: { id: 19, name: 'Темиртау' },
      dispatchers: [{ id: 7, name: 'Честный Темиртау', fleet_city: 'Караганда',
        fleet_city_other: 'Караганда' }] }],
  };
  const html = renderToStaticMarkup(React.createElement(CityRegistration, {
    city: { id: 19, name: 'Темиртау', registration: block } }));
  assert.match(html, /Честный Темиртау/);
  assert.match(html, /в кабинете — Караганда/);
});

/* ------------------------------------------------------------- поиск */

test('строка поиска «Регистрация»: парк, город и диспетчерская', () => {
  const entry = {
    kind: 'registration', id: 15, ref_id: 15, space_id: 11, name: 'Рудный', park: 'iTaxi',
    target: 'Костанай', own: false, dispatchers: [{ name: 'iTaxi' }], state: null, targets: [],
  };
  assert.equal(directoryTab(entry), 'cities');
  assert.deepEqual(directoryText(entry), {
    title: 'Регистрация iTaxi · Рудный', subtitle: 'В Костанае · «iTaxi»',
  });
  assert.deepEqual(directoryText({ ...entry, target: null, dispatchers: [] }), {
    title: 'Регистрация iTaxi · Рудный', subtitle: 'Диспетчерской iTaxi для этого города нет',
  });
  assert.deepEqual(directoryText({ ...entry, park: null, target: null, state: 'elsewhere',
    targets: ['Туркестан', 'Шымкент'], dispatchers: [] }), {
    title: 'Регистрация водителей · Рудный',
    subtitle: 'Своих диспетчерских нет — в Туркестане, затем в Шымкенте',
  });
  assert.deepEqual(directoryText({ ...entry, park: null, target: null, state: 'mixed',
    others: ['Караганда'], dispatchers: [] }), {
    title: 'Регистрация водителей · Рудный',
    subtitle: 'Зависит от парка: здесь или в Караганде',
  });
  // Две строки одного города с разными парками — два ключа, а не один.
  assert.notEqual(directoryKey(entry), directoryKey({ ...entry, park: 'Аманат' }));
});
