/*
 * Перенос статьи в другой раздел: арифметика выбора и сама панель.
 *
 * Зачем этот файл. Перенос отличается от «сменить раздел» ровно одним: статья
 * лежит сразу в НЕСКОЛЬКИХ разделах, и трогать надо один. Ошибка здесь тихая —
 * статья остаётся на экране, просто у соседнего отдела пропадает регламент, и
 * узнают об этом через неделю. Поэтому правила выбора вынесены в чистые функции
 * (articleMove.js) и проверяются числами, а не глазами.
 *
 * Панель отрисовывается НАСТОЯЩАЯ, через react-dom/server: браузерного
 * окружения в проекте нет, а сломать её можно молча — обращение к значению,
 * объявленному ниже по телу компонента, даёт ReferenceError на первом рендере,
 * и сборка это пропускает.
 *
 * Чего файл НЕ проверяет: сам запрос переноса и права на сервере — это
 * tests/test_wiki_article_move.py, там и дверь, и порядок записи.
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

async function loadModule(name) {
  /* Собранный модуль кладём ВНУТРЬ проекта: из системной временной папки
     `import 'react'` не разрешается — node ищет node_modules вверх от файла. */
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, `${name}.mjs`);
  buildSync({
    entryPoints: [fileURLToPath(new URL(`../src/components/wiki/${name}`,
                                        import.meta.url))],
    bundle: true,
    format: 'esm',
    target: 'node18',
    outfile,
    external: ['react', 'react-dom', 'axios', 'lucide-react'],
    loader: { '.jsx': 'jsx' },
    logLevel: 'silent',
  });
  return import(`file://${outfile.replace(/\\/g, '/')}`);
}

const logic = await loadModule('articleMove.js');
const { currentSectionIds, keptNote, mayMove, movePlan, moveSources } = logic;
const Panel = (await loadModule('ArticleMovePanel.jsx')).default;

const SPACE = { id: 1, name: 'Тез', status: 'active' };

/* Дерево как на бою: «Оператор» внутри «Супервайзера», внутри — четыре ветки.
   «ФРОД» закрыт на запись — им и проверяется бледная строка. */
const SECTIONS = [
  { id: 10, space_id: 1, parent_section_id: null, name: 'Супервайзер',
    status: 'active', permissions: { can_create: true } },
  { id: 11, space_id: 1, parent_section_id: 10, name: 'Оператор',
    status: 'active', permissions: { can_create: true } },
  { id: 12, space_id: 1, parent_section_id: 11, name: 'Стандарты ведения диалога',
    status: 'active', permissions: { can_create: true } },
  { id: 13, space_id: 1, parent_section_id: 11, name: 'Клиент',
    status: 'active', permissions: { can_create: true } },
  { id: 14, space_id: 1, parent_section_id: 11, name: 'ФРОД',
    status: 'active', permissions: { can_create: false } },
  { id: 15, space_id: 1, parent_section_id: 11, name: 'Водитель',
    status: 'active', permissions: { can_create: true } },
];

const NAMES = new Map(SECTIONS.map((s) => [s.id, s.name]));
const ONE = { id: 7, title: 'Прощание', section_ids: [12] };
const TWO = { id: 8, title: 'Удержание', section_ids: [12, 13] };
const ORPHAN = { id: 9, title: 'Старый регламент', section_ids: [] };

// ── Откуда переносим ────────────────────────────────────────────────────────

test('разделы статьи приходят числами, даже если API отдал строки', () => {
  assert.deepEqual(currentSectionIds({ section_ids: ['12', 13] }), [12, 13]);
  assert.deepEqual(currentSectionIds({}), []);
});

test('источник — только тот раздел, который человеку показывают', () => {
  // 99 закрыт правами или лежит в соседней вике: назвать его значит
  // рассказать о содержимом чужого раздела.
  const sources = moveSources({ section_ids: [12, 99] }, SECTIONS, NAMES);
  assert.deepEqual(sources.map((s) => s.id), [12]);
});

test('у источника есть признак «отсюда вправе забрать»', () => {
  // Право то же, что и у раздела-получателя: сервер проверяет оба.
  const sources = moveSources({ section_ids: [12, 14] }, SECTIONS, NAMES);
  assert.deepEqual(sources.map((s) => [s.name, s.allowed]),
                   [['Стандарты ведения диалога', true], ['ФРОД', false]]);
});

test('статью без разделов переносить можно всегда — забирать не из чего', () => {
  assert.equal(mayMove(ORPHAN, SECTIONS, NAMES), true);
});

test('пункт «Переместить» не появляется там, где забрать нельзя', () => {
  // Единственный раздел статьи закрыт на запись: панель открылась бы только
  // затем, чтобы любое нажатие в ней закончилось отказом сервера.
  assert.equal(mayMove({ section_ids: [14] }, SECTIONS, NAMES), false);
  assert.equal(mayMove({ section_ids: [14, 12] }, SECTIONS, NAMES), true);
});

test('права не приехали — не гасим ничего', () => {
  // Ответ /catalog прав по разделам не несёт. Пустая панель хуже лишней
  // строки: отказ всё равно скажет сервер.
  const bare = SECTIONS.map(({ permissions, ...rest }) => rest);
  assert.equal(mayMove({ section_ids: [14] }, bare, NAMES), true);
});

// ── Что случится ────────────────────────────────────────────────────────────

test('перенос готов, когда выбран другой разрешённый раздел', () => {
  assert.equal(movePlan(SECTIONS, ONE, 12, 15).ready, true);
});

test('в тот же раздел — не перенос', () => {
  assert.equal(movePlan(SECTIONS, ONE, 12, 12).ready, false);
});

test('в раздел, где статья уже лежит, — тоже не перенос', () => {
  // Это была бы отвязка от источника, а не перенос, и сервер отвечает 409.
  assert.equal(movePlan(SECTIONS, TWO, 12, 13).ready, false);
});

test('в закрытый на запись раздел кнопка не загорается', () => {
  // Строка дерева погашена, но выбор живёт в состоянии экрана: раздел мог
  // закрыться правами уже после того, как его выбрали.
  assert.equal(movePlan(SECTIONS, ONE, 12, 14).ready, false);
});

test('из закрытого на запись раздела — тоже не загорается', () => {
  assert.equal(movePlan(SECTIONS, { section_ids: [14] }, 14, 15).ready, false);
});

test('статья без разделов: источника нет, а перенос готов', () => {
  const plan = movePlan(SECTIONS, ORPHAN, null, 15);
  assert.equal(plan.from, null);
  assert.equal(plan.ready, true);
});

test('остальные разделы статьи остаются — и об этом сказано', () => {
  const plan = movePlan(SECTIONS, TWO, 12, 15);
  assert.deepEqual(plan.kept.map((s) => s.name), ['Клиент']);
  assert.equal(keptNote(plan), 'Статья останется ещё в разделе «Клиент».');
});

test('раздел, который нельзя назвать, из подписи не пропадает', () => {
  // 99 человеку не показывают, но статья в нём остаётся: умолчать об этом
  // значит обещать переезд там, где будет копия.
  const plan = movePlan(SECTIONS, { section_ids: [12, 99] }, 12, 15);
  assert.equal(plan.keptHidden, 1);
  assert.equal(keptNote(plan), 'Статья останется ещё в одном разделе.');
});

test('у статьи в одном разделе приписки нет вовсе', () => {
  assert.equal(keptNote(movePlan(SECTIONS, ONE, 12, 15)), null);
});

// ── Панель ──────────────────────────────────────────────────────────────────

const render = (props = {}) => renderToStaticMarkup(React.createElement(Panel, {
  article: ONE,
  sections: SECTIONS,
  spaces: [SPACE],
  names: NAMES,
  fromId: 12,
  toId: null,
  ...props,
}));

test('панель — это панель, а не окно поверх списка', () => {
  const html = render();
  assert.doesNotMatch(html, /aria-modal/);
  assert.doesNotMatch(html, /fixed inset-0/);
});

test('дерево раскрыто до того места, где статья лежит сейчас', () => {
  // Ветка «Супервайзер › Оператор» открыта, иначе человек искал бы заново
  // то место, откуда пришёл.
  const html = render();
  assert.match(html, /Супервайзер/);
  assert.match(html, /Оператор/);
  assert.match(html, /Клиент/);
  assert.match(html, /здесь сейчас/);
});

test('раздел, где статья уже лежит, помечен словами, а не цветом', () => {
  const html = render({ article: TWO });
  assert.match(html, /уже там/);
});

test('закрытый на запись раздел показан и не нажимается', () => {
  const html = render();
  // Строка есть, но кнопка погашена и объясняет себя наведением; пояснение
  // про бледные строки стоит под деревом — словами, а не намёком цветом.
  assert.match(html, /ФРОД/);
  assert.match(html, /disabled="" title="Класть статьи в этот раздел вам нельзя"/);
  assert.match(html, /Бледные разделы выбрать нельзя/);
});

test('у статьи в нескольких разделах спрашивают, из какого переносим', () => {
  const html = render({ article: TWO });
  assert.match(html, /из какого переносим/);
  assert.match(html, /aria-pressed="true"/);
});

test('у статьи в одном разделе выбора нет — только строка «сейчас в…»', () => {
  const html = render();
  assert.match(html, /Сейчас в разделе «Стандарты ведения диалога»/);
  assert.doesNotMatch(html, /из какого переносим/);
});

test('статья вне дерева зовёт положить её куда-нибудь', () => {
  const html = render({ article: ORPHAN, fromId: null });
  assert.match(html, /не привязана ни к одному разделу/);
});

test('подтверждения нет, пока раздел не выбран', () => {
  const html = render();
  assert.match(html, /grid-rows-\[0fr\]/);
  assert.match(html, /aria-hidden="true"/);
  // Кнопка свёрнутой полосы погашена: иначе Tab уводил бы на невидимое.
  const strip = html.slice(html.indexOf('grid-rows-[0fr]'));
  assert.match(strip, /disabled/);
});

test('выбрали раздел — полоса раскрылась и назвала оба раздела', () => {
  const html = render({ toId: 15 });
  assert.match(html, /grid-rows-\[1fr\]/);
  assert.match(html, /Переместить в «Водитель»\?/);
  assert.match(html, /пропадёт из «Стандарты ведения диалога»/);
  assert.match(html, /правила нового раздела/);
});

test('пока идёт запрос, кнопка говорит об этом и не нажимается второй раз', () => {
  const html = render({ toId: 15, busy: true });
  assert.match(html, /Переносим…/);
});

test('поиск появляется только у большого дерева', () => {
  assert.doesNotMatch(render({ sections: SECTIONS.slice(0, 3) }), /Найти раздел/);
  const big = [...SECTIONS, ...Array.from({ length: 6 }, (_, i) => ({
    id: 100 + i, space_id: 1, parent_section_id: 11, name: `Ветка ${i}`,
    status: 'active', permissions: { can_create: true },
  }))];
  assert.match(render({ sections: big }), /Найти раздел/);
});

test('архивный раздел в дерево не попадает', () => {
  // Архивируют обычно дубль с тем же именем, и в списке он неотличим от
  // живого: статья уехала бы туда, откуда её не видно (см. sectionPicker.js).
  const html = render({
    sections: [...SECTIONS, {
      id: 20, space_id: 1, parent_section_id: 11, name: 'Старая ветка',
      status: 'archived', permissions: { can_create: true },
    }],
  });
  assert.doesNotMatch(html, /Старая ветка/);
});
