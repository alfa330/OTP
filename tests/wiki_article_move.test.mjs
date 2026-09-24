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
  assert.match(html, /Сейчас в разделе «Супервайзер › Оператор › Стандарты ведения диалога»/);
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
  assert.match(html, /Переместить в «Супервайзер › Оператор › Водитель»\?/);
  assert.match(html, /пропадёт из «Супервайзер › Оператор › Стандарты ведения диалога»/);
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

// ── Одна статья в нескольких разделах: добавить и убрать ────────────────────
//
// Статью показывают в нескольких местах привязками, а не копиями. Тихая ошибка
// здесь та же, что у переноса: не та привязка — и у соседнего отдела пропадает
// регламент. Плюс своя: последняя привязка, снятая по ошибке, делает статью
// невидимой всем, кроме автора.

const { addPlan, alsoIn, detachPlan, detachSources, mayAdd, mayDetach } = logic;

test('добавить можно, пока есть разрешённый раздел, где статьи ещё нет', () => {
  assert.equal(mayAdd(ONE, SECTIONS), true);
  // Всё разрешённое уже занято, остался лишь закрытый на запись «ФРОД».
  const everywhere = { id: 1, section_ids: [10, 11, 12, 13, 15] };
  assert.equal(mayAdd(everywhere, SECTIONS), false);
});

test('добавление ничего не забирает — и все нынешние разделы остаются', () => {
  const plan = addPlan(SECTIONS, TWO, 15);
  assert.equal(plan.ready, true);
  assert.equal(plan.from, null);
  assert.deepEqual(plan.kept.map((s) => s.id), [12, 13]);
});

test('добавить туда, где статья уже лежит, или в закрытый раздел — нельзя', () => {
  assert.equal(addPlan(SECTIONS, TWO, 13).ready, false);
  assert.equal(addPlan(SECTIONS, ONE, 14).ready, false);
  assert.equal(addPlan(SECTIONS, ONE, null).ready, false);
});

test('из единственного раздела статью не убрать — для этого есть архив', () => {
  assert.equal(mayDetach(ONE, SECTIONS, NAMES), false);
  assert.equal(detachPlan(SECTIONS, ONE, 12).ready, false);
  assert.deepEqual(detachSources(ONE, SECTIONS, NAMES).map((s) => s.allowed), [false]);
});

test('из одного из двух разделов убрать можно, второй остаётся и назван', () => {
  assert.equal(mayDetach(TWO, SECTIONS, NAMES), true);
  const plan = detachPlan(SECTIONS, TWO, 13);
  assert.equal(plan.ready, true);
  assert.equal(plan.from.name, 'Клиент');
  assert.equal(keptNote(plan), 'Статья останется ещё в разделе «Стандарты ведения диалога».');
});

test('закрытый на запись раздел не снимается, даже если разделов два', () => {
  const inFrod = { id: 3, title: 'Мошенники', section_ids: [12, 14] };
  assert.equal(detachPlan(SECTIONS, inFrod, 14).ready, false);
  assert.deepEqual(
    detachSources(inFrod, SECTIONS, NAMES).map((s) => [s.id, s.allowed]),
    [[12, true], [14, false]]);
});

test('второй раздел может быть скрыт от человека — убрать из видимого всё равно можно', () => {
  // Раздел 99 — чужая закрытая ветка: назвать нельзя, но статья там лежит.
  const hidden = { id: 4, title: 'Общий регламент', section_ids: [12, 99] };
  assert.equal(mayDetach(hidden, SECTIONS, NAMES), true);
  const plan = detachPlan(SECTIONS, hidden, 12);
  assert.equal(plan.ready, true);
  assert.equal(keptNote(plan), 'Статья останется ещё в одном разделе.');
});

test('подпись «также в …» называет другие видимые разделы статьи', () => {
  assert.equal(alsoIn(ONE, NAMES, 12), null);
  assert.equal(alsoIn(TWO, NAMES, 12), 'Также в «Клиент»');
  assert.equal(alsoIn({ section_ids: [12, 13, 15] }, NAMES, 12), 'Также в «Клиент» и ещё 1');
  // Закрытую ветку в подпись не выдаём.
  assert.equal(alsoIn({ section_ids: [12, 99] }, NAMES, 12), null);
});

test('панель добавления: без выбора источника, занятые разделы помечены', () => {
  const html = render({ article: TWO, mode: 'add', fromId: null });
  assert.match(html, /Где ещё показать статью/);
  assert.match(html, /Сейчас в разделах «Супервайзер › Оператор › Стандарты ведения диалога», «Супервайзер › Оператор › Клиент»/);
  assert.doesNotMatch(html, /из какого/);
  assert.match(html, /уже там/);
  assert.doesNotMatch(html, /здесь сейчас/);
});

test('подтверждение добавления говорит, что это не копия', () => {
  const html = render({ mode: 'add', fromId: null, toId: 15 });
  assert.match(html, /Добавить в «Супервайзер › Оператор › Водитель»\?/);
  assert.match(html, /а не копия/);
  assert.match(html, /остаётся/);
  assert.match(html, /Добавить<\/button>/);
});

test('панель снятия: без дерева, с выбором раздела и остатком', () => {
  const html = render({ article: TWO, mode: 'detach', fromId: 13 });
  assert.match(html, /Из какого раздела убрать статью/);
  assert.match(html, /из какого убрать/);
  assert.doesNotMatch(html, /Бледные разделы/);
  assert.doesNotMatch(html, /Водитель/);   // дерева всей вики нет
  assert.match(html, /Убрать из «Супервайзер › Оператор › Клиент»\?/);
  assert.match(html, /пропадёт только из «Супервайзер › Оператор › Клиент»/);
  assert.match(html, /не удаляется/);
  assert.match(html, /Статья останется ещё в разделе «Супервайзер › Оператор › Стандарты ведения диалога»/);
});

test('ветки-близнецы СЗоВ и ОП различаются путём, а не одним именем', () => {
  // Одну статью в двух местах держат как раз в одноимённых ветках: «Сейчас в
  // «Супервайзер», «Супервайзер»» не сказало бы ничего.
  const twins = [
    { id: 40, space_id: 1, parent_section_id: null, name: 'СЗоВ', status: 'active',
      permissions: { can_create: true } },
    { id: 41, space_id: 1, parent_section_id: 40, name: 'Супервайзер', status: 'active',
      permissions: { can_create: true } },
    { id: 50, space_id: 1, parent_section_id: null, name: 'ОП', status: 'active',
      permissions: { can_create: true } },
    { id: 51, space_id: 1, parent_section_id: 50, name: 'Супервайзер', status: 'active',
      permissions: { can_create: true } },
  ];
  const names = new Map(twins.map((x) => [x.id, x.name]));
  const both = { id: 216, title: 'Инструкция для супервайзера', section_ids: [41, 51] };
  const html = render({ article: both, sections: twins, names, mode: 'detach', fromId: 51 });
  assert.match(html, /СЗоВ › Супервайзер/);
  assert.match(html, /Убрать из «ОП › Супервайзер»\?/);
  assert.match(html, /останется ещё в разделе «СЗоВ › Супервайзер»/);
});

// ── Выбор нескольких разделов в редакторе ───────────────────────────────────

const TreeSelect = (await loadModule('SectionTreeSelect.jsx')).default;
const renderSelect = (props = {}) => renderToStaticMarkup(React.createElement(TreeSelect, {
  sections: SECTIONS, spaces: [SPACE], multiple: true, value: [], ...props,
}));

test('выбранные разделы стоят плашками с путём, кнопка зовёт добавить ещё', () => {
  const html = renderSelect({ value: [12, 13] });
  assert.match(html, /Супервайзер › Оператор › Стандарты ведения диалога/);
  assert.match(html, /Супервайзер › Оператор › Клиент/);
  assert.match(html, /Добавить раздел/);
  assert.match(html, /без копий: правка видна везде/);
});

test('раздел, откуда убирать нельзя, — плашка без крестика', () => {
  const html = renderSelect({ value: [12, 14], locked: [14] });
  assert.match(html, /aria-label="Убрать из раздела «Супервайзер › Оператор › Стандарты ведения диалога»"/);
  assert.doesNotMatch(html, /aria-label="Убрать из раздела «Супервайзер › Оператор › ФРОД»"/);
});

test('разделы вне списка не называются, но о них сказано', () => {
  const html = renderSelect({ value: [12, 98, 99] });
  assert.match(html, /Ещё в 2 разделах, которых нет в этом списке, — они сохранятся/);
});

test('пустой выбор — приглашение выбрать, без плашек', () => {
  const html = renderSelect();
  assert.match(html, /Выберите…/);
  assert.doesNotMatch(html, /Убрать из раздела/);
});
