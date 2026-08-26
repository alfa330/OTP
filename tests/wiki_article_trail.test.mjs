import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
  TRAIL_LIMIT,
  backLabel,
  openTrail,
  popTrail,
  pushTrail,
  trailBack,
  trailTop,
} from '../src/components/wiki/articleTrail.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel) => readFileSync(join(ROOT, rel), 'utf8');

/* ── Правила цепочки ──────────────────────────────────────────────────────── */

test('открытие из списка начинает цепочку заново', () => {
  const trail = pushTrail(openTrail('tarify'), 'shtrafy');
  assert.equal(trail.length, 2);
  // Открыли третью статью из оглавления — путь через «Тарифы» больше не при чём.
  const fresh = openTrail('grafiki');
  assert.deepEqual(fresh, [{ slug: 'grafiki' }]);
  assert.equal(trailBack(fresh), null);
});

test('пустой слаг закрывает статью', () => {
  assert.deepEqual(openTrail(null), []);
  assert.equal(trailTop([]), null);
});

test('переход по сети статей помнит, откуда ушли', () => {
  const trail = pushTrail(openTrail('tarify'), 'shtrafy',
                          { title: 'Тарифы', scrollTop: 1840 });
  assert.equal(trailTop(trail).slug, 'shtrafy');
  assert.equal(trailBack(trail).slug, 'tarify');
  assert.equal(trailBack(trail).title, 'Тарифы');
  // Позиция чтения нужна возврату: человек ушёл по ссылке из середины текста.
  assert.equal(trailBack(trail).scrollTop, 1840);
});

test('возврат снимает ровно один шаг, а с последнего уводит в список', () => {
  const trail = pushTrail(pushTrail(openTrail('a'), 'b'), 'c');
  const back = popTrail(trail);
  assert.equal(trailTop(back).slug, 'b');
  assert.equal(trailTop(popTrail(back)).slug, 'a');
  assert.deepEqual(popTrail(popTrail(back)), []);
  assert.deepEqual(popTrail([]), []);
});

test('подсветка и заготовка классификатора возвращаются вместе со статьёй', () => {
  const found = openTrail('tarify', { highlight: 'комиссия', prefill: { model: 'Vento' } });
  const deeper = pushTrail(found, 'shtrafy', { title: 'Тарифы' });
  const back = popTrail(deeper);
  assert.equal(trailTop(back).highlight, 'комиссия');
  assert.deepEqual(trailTop(back).prefill, { model: 'Vento' });
  // Шаг вперёд своей подсветки не наследует — в новой статье искать нечего.
  assert.equal(trailTop(deeper).highlight, undefined);
});

test('взаимная пара статей не наматывает цепочку, а обрезает её', () => {
  /* «Взаимная» связь в вике — штатное состояние (блок «Связанные материалы»
     помечает такие строки), поэтому ходить туда-обратно будут. */
  let trail = openTrail('tarify');
  for (let i = 0; i < 6; i += 1) {
    trail = pushTrail(trail, i % 2 === 0 ? 'shtrafy' : 'tarify');
    assert.ok(trail.length <= 2, `шаг ${i}: цепочка выросла до ${trail.length}`);
  }
  // Вернулись в «Тарифы» — путь закольцевался, дальше идти назад некуда.
  assert.deepEqual(trail.map((entry) => entry.slug), ['tarify']);
  assert.equal(trailBack(trail), null);
});

test('шаг на статью, которая уже на экране, ничего не меняет', () => {
  const trail = pushTrail(openTrail('tarify'), 'shtrafy');
  assert.deepEqual(pushTrail(trail, 'shtrafy').map((e) => e.slug), ['tarify', 'shtrafy']);
});

test('цепочка не растёт бесконечно — старое отрезается с хвоста', () => {
  let trail = openTrail('s0');
  for (let i = 1; i < TRAIL_LIMIT + 5; i += 1) trail = pushTrail(trail, `s${i}`);
  assert.equal(trail.length, TRAIL_LIMIT);
  assert.equal(trailTop(trail).slug, `s${TRAIL_LIMIT + 4}`);
  assert.equal(trail[0].slug, `s${5}`);
});

test('пустой слаг цепочку не трогает', () => {
  const trail = openTrail('tarify');
  assert.equal(pushTrail(trail, ''), trail);
  assert.equal(pushTrail(trail, null), trail);
});

/* ── Подпись кнопки ───────────────────────────────────────────────────────── */

test('без предыдущей статьи кнопка обещает список', () => {
  assert.equal(backLabel(null), 'К списку');
  assert.equal(backLabel({ slug: 'tarify' }), 'К списку');
});

test('с предыдущей статьёй кнопка называет её, длинный заголовок режется', () => {
  assert.equal(backLabel({ title: 'Тарифы' }), 'Назад: «Тарифы»');
  const long = backLabel({ title: 'Регламент подключения водителей на личном авто' });
  assert.ok(long.endsWith('…»'), long);
  assert.ok(long.length < 34, long);
});

/* ── Стражи: правило дороже реализации ───────────────────────────────────── */

test('витрина ведёт цепочку, а не одну открытую статью', () => {
  const src = read('src/components/wiki/WikiLibrary.jsx');
  assert.ok(src.includes("from './articleTrail'"), 'WikiLibrary обязана брать правила из articleTrail');
  // Возврат — шаг назад, а не «закрыть статью».
  assert.ok(src.includes('popTrail'), 'возврат обязан снимать шаг цепочки');
  assert.ok(!src.includes('setOpenSlug'),
            'открытая статья больше не отдельное значение — иначе цепочка разойдётся с экраном');
  // Переход из статьи и открытие из списка — РАЗНЫЕ действия.
  assert.ok(src.includes('onOpenArticle={openLinkedArticle}'),
            'ссылка из статьи обязана быть шагом вперёд, а не открытием с нуля');
});

test('подпись кнопки возврата приходит из цепочки, а не зашита в статью', () => {
  const src = read('src/components/wiki/WikiArticle.jsx');
  assert.ok(src.includes('backLabel(backTo)'),
            'подпись обязана считаться по цепочке, иначе кнопка обещает не то, что делает');
  assert.ok(!/>\s*К списку\s*</.test(src),
            'жёстко вписанная подпись «К списку» вернула бы кнопке старое обещание');
  // Заголовок статьи-источника — единственное, чем витрина может подписать кнопку.
  assert.ok(src.includes('onOpenArticle(target, { title:'),
            'переход обязан нести наверх заголовок статьи, из которой уходят');
});

test('список после статьи показывается сверху', () => {
  const lib = read('src/components/wiki/WikiLibrary.jsx');
  assert.ok(lib.includes('scrollPortalTo(0)'),
            'возврат в витрину обязан сбрасывать прокрутку — она остаётся от статьи');
  const article = read('src/components/wiki/WikiArticle.jsx');
  assert.ok(article.includes('restoreScroll'),
            'возврат в статью обязан ставить человека туда, где он оборвал чтение');
});
