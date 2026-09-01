/*
 * Оформительские блоки статьи вики — счётная часть узла схемы.
 *
 * Здесь проверяется ровно то, что можно проверить без браузера: перечни
 * значений, шаблоны вставки и правило «чужое значение не сохраняется».
 * Браузерного окружения в проекте нет (ни jsdom, ни puppeteer — см. шапку
 * tests/custom_select_render.test.mjs), поэтому круговорот «разобрал HTML →
 * собрал обратно» сторожат текстовые проверки в tests/test_wiki_blocks.py:
 * узел подключён в массив extensions, разбор идёт по data-атрибуту, стили
 * импортируются обеими поверхностями.
 *
 * Что ломается молча и почему за этим стоит следить.
 *
 * 1. ЧУЖОЕ ЗНАЧЕНИЕ. data-tone="фиолетовый" проходит санитайзер целым: он
 *    сверяет ИМЯ атрибута, а не значение. Нарисовать такой тон нечем — в CSS
 *    его нет, — и блок молча становится нейтральным. Хуже того, значение
 *    остаётся в теле статьи навсегда, и через полгода уже не установить, чьё
 *    оно и можно ли его трогать. Поэтому чужое значение НЕ сохраняется.
 *
 * 2. ШАБЛОН ВСТАВКИ. Он же — единственный образец правильной разметки внутри
 *    фронта. Опечатка в нём (data-wiki-blok, tone вместо data-tone) даёт блок,
 *    который вставляется, показывается в редакторе абзацем и сохраняется
 *    абзацем: разбор схемой не поймает то, чего не узнал.
 *
 * 3. ДЕЙСТВИЕ ПУНКТА МЕНЮ. «Вводка» вставляет пустую заготовку, а «Шаги»
 *    ПРЕВРАЩАЮТ уже выделенный текст. Перепутай action — и человек, выделивший
 *    три абзаца и выбравший «Шаги», получит рядом с ними ещё три чужих.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    BLOCK_COLS, BLOCK_KINDS, BLOCK_MENU, BLOCK_TONES, GRID_ITEMS, INSERT_BLOCKS, LIST_VARIANTS, TONE_VALUES, VARIANT_VALUES, pickAllowed,
} from '../src/components/wiki/WikiBlockNode.js';

test('чужое значение атрибута не сохраняется', () => {
  assert.equal(pickAllowed(TONE_VALUES, 'фиолетовый'), null);
  assert.equal(pickAllowed(TONE_VALUES, 'purple'), null);
  assert.equal(pickAllowed(BLOCK_KINDS, 'hero', 'note'), 'note');
  assert.equal(pickAllowed(BLOCK_COLS, '7'), null);
  assert.equal(pickAllowed(VARIANT_VALUES, 'steps '), null);
});

test('своё значение проходит как есть', () => {
  for (const tone of TONE_VALUES) assert.equal(pickAllowed(TONE_VALUES, tone), tone);
  for (const kind of BLOCK_KINDS) assert.equal(pickAllowed(BLOCK_KINDS, kind, 'note'), kind);
  for (const cols of BLOCK_COLS) assert.equal(pickAllowed(BLOCK_COLS, cols), cols);
});

test('пустота и мусор не превращаются в значение', () => {
  // Атрибута нет — getAttribute отдаёт null; пустая строка приходит от
  // вычищенного атрибута. Ни то, ни другое не должно стать тоном.
  for (const empty of [null, undefined, '', 0, false]) {
    assert.equal(pickAllowed(TONE_VALUES, empty), null);
  }
});

test('шаблон каждого блока написан теми же атрибутами, что читает схема', () => {
  for (const block of INSERT_BLOCKS) {
    assert.match(block.template, /data-wiki-block="[a-z]+"/,
      `шаблон «${block.label}» без ключа блока`);
    for (const found of block.template.matchAll(/data-wiki-block="([^"]+)"/g)) {
      assert.ok(BLOCK_KINDS.includes(found[1]),
        `шаблон «${block.label}» ставит неизвестный вид ${found[1]}`);
    }
    for (const found of block.template.matchAll(/data-tone="([^"]+)"/g)) {
      assert.ok(TONE_VALUES.includes(found[1]),
        `шаблон «${block.label}» ставит неизвестный тон ${found[1]}`);
    }
    for (const found of block.template.matchAll(/data-cols="([^"]+)"/g)) {
      assert.ok(BLOCK_COLS.includes(found[1]),
        `шаблон «${block.label}» ставит неизвестное число колонок ${found[1]}`);
    }
  }
});

test('шаблон заканчивается абзацем — иначе после блока некуда писать', () => {
  // Блок вставляется последним узлом документа чаще, чем кажется: статью
  // начинают с вводки, а плашку ставят в конец раздела. Без завершающего
  // абзаца курсору дальше некуда деться, и текст уходит внутрь блока.
  for (const block of INSERT_BLOCKS) {
    assert.ok(block.template.endsWith('<p></p>'),
      `шаблон «${block.label}» не оставляет абзаца после блока`);
  }
});

test('заголовок внутри блока — только h4', () => {
  // h1-h3 попали бы в оглавление статьи наравне с разделами: витрина
  // собирает его querySelectorAll('h1, h2, h3') по всему телу.
  for (const block of INSERT_BLOCKS) {
    assert.ok(!/<h[123]\b/.test(block.template),
      `шаблон «${block.label}» несёт заголовок уровня раздела`);
  }
});

test('сетка карточек вставляется сразу с двумя карточками', () => {
  // Одна карточка в сетке — это плашка, а не сетка: колонка во всю ширину с
  // рамкой. Пустая сетка ещё хуже — выглядит поломкой вёрстки.
  const cards = INSERT_BLOCKS.find((item) => item.key === 'cards');
  assert.equal((cards.template.match(/data-wiki-block="card"/g) || []).length, 2);
});

const MENU_KEYS = new Set(BLOCK_MENU.map((i) => i.key));

test('меню знает все блоки и различает вставку и превращение', () => {
  // Забытый здесь пункт — самая тихая поломка из возможных: блок есть в
  // схеме, в санитайзере, в стилях и в наставлении для ИИ, статья с ним
  // открывается правильно, но поставить его руками автор не может.
  assert.deepEqual(BLOCK_MENU.map((item) => item.key),
    ['lead', 'note', 'steps', 'cards', 'stats', 'chips', 'checks', 'crosses']);
  const byAction = (action) => BLOCK_MENU.filter((i) => i.action === action).map((i) => i.key);
  assert.deepEqual(byAction('insert'), ['lead', 'note', 'cards', 'stats']);
  assert.deepEqual(byAction('variant'), ['steps', 'chips', 'checks', 'crosses']);

  // Ни один вид блока не должен остаться без пункта меню.
  const missing = [...INSERT_BLOCKS.map((b) => b.key),
    ...LIST_VARIANTS.map((v) => v.value)].filter((k) => !MENU_KEYS.has(k));
  assert.deepEqual(missing, [], `нет в меню: ${missing.join(', ')}`);
});

test('у каждого пункта меню есть подпись и объяснение', () => {
  for (const item of BLOCK_MENU) {
    assert.ok(item.label && item.label.length <= 12, `подпись «${item.label}» не годится`);
    assert.ok(item.hint && item.hint.length > 10, `у пункта ${item.key} нет объяснения`);
  }
});

test('кнопка «+» кладёт в сетку ЕЁ ячейку', () => {
  // Пары «сетка → ячейка» продублированы на сервере (GRIDS в
  // wiki/ai/markup.py). Разойдись они — кнопка положила бы в сетку
  // показателей карточку, а ремонт разметки при первой же правке через ИИ
  // выкинул бы её из сетки наружу: человек увидел бы, что добавленное
  // «выпало» само.
  assert.deepEqual(Object.keys(GRID_ITEMS).sort(), ['cards', 'stats']);
  assert.equal(GRID_ITEMS.cards.item, 'card');
  assert.equal(GRID_ITEMS.stats.item, 'stat');
  for (const [grid, spec] of Object.entries(GRID_ITEMS)) {
    assert.ok(spec.template.includes(`data-wiki-block="${spec.item}"`),
      `шаблон сетки ${grid} кладёт не свою ячейку`);
  }
});

test('шаблон показателей несёт и значение, и подпись', () => {
  // Показатель без подписи — это просто крупное число посреди статьи:
  // читатель видит «4,75» и не знает, что это.
  const stats = INSERT_BLOCKS.find((b) => b.key === 'stats');
  assert.ok(stats, 'показателей нет в меню вставки');
  assert.ok(stats.template.includes('<h4>'), 'в шаблоне нет значения');
  assert.ok(stats.template.includes('<p>'), 'в шаблоне нет подписи');
  assert.equal((stats.template.match(/data-wiki-block="stat"/g) || []).length, 3);
});

test('вид списка привязан к своему тегу', () => {
  // «Шаги» на <ul> и «чипы» на <ol> не нарисуются: правила CSS написаны под
  // конкретный тег. Привязка обязана быть объявлена рядом со значением.
  const byValue = Object.fromEntries(LIST_VARIANTS.map((v) => [v.value, v.type]));
  assert.equal(byValue.steps, 'orderedList');
  assert.equal(byValue.chips, 'bulletList');
  assert.equal(byValue.checks, 'bulletList');
  assert.equal(byValue.crosses, 'bulletList');
});

test('у каждого тона есть подпись и объяснение, когда его ставить', () => {
  // Панель подписывает тона словами, а не только кружками: индиговый
  // «Обычная» и фиолетовый «Совет» по цвету не различаются.
  //
  // Числа тонов здесь нет намеренно: список растёт, и сверка с константой
  // ломала бы тест на каждом добавлении, ничего при этом не проверяя.
  // Настоящее правило — что значения не повторяются: два тона с одним
  // value дали бы в панели две кнопки, из которых работает только первая.
  assert.equal(new Set(TONE_VALUES).size, BLOCK_TONES.length);
  for (const tone of BLOCK_TONES) {
    assert.ok(tone.label, `тон ${tone.value} без подписи`);
    assert.ok(tone.hint && tone.hint.length > 5, `тон ${tone.value} без объяснения`);
  }
});
