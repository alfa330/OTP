import test from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_SIZE,
  MIN_SIZE,
  clampSize,
  normalizeAlign,
  sizeFromElement,
  styleFor,
} from '../src/components/wiki/imageSize.js';

/* Счётная часть размера картинки в статье.
 *
 * Три вещи здесь ломаются молча, и каждая — отдельный класс жалобы.
 *
 * 1. «Размер не задан» — это ЗНАЧЕНИЕ, а не пропуск. Подмени пустоту сотней, и
 *    каждый мелкий значок растянется на всю колонку и станет мылом.
 * 2. Ширину из стиля ищет регулярка, и «max-width: 100%» содержит внутри себя
 *    «width: 100%». Без привязки к началу объявления картинка, которой размер
 *    НЕ задавали, при открытии редактора уезжает на всю ширину.
 * 3. Выравнивание пишется двумя отдельными полями, а не сокращённым margin:
 *    сокращённое серверный санитайзер выбрасывает целиком (wiki/sanitize.py
 *    сверяет ИМЯ свойства), и картинка молча возвращается к левому краю.
 */

/* Хватает getAttribute: узел ничего другого у элемента не спрашивает. */
const element = (attrs) => ({ getAttribute: (name) => (name in attrs ? attrs[name] : null) });

test('пустая ширина остаётся пустой, а не превращается в 100 %', () => {
  assert.equal(clampSize(null), null);
  assert.equal(clampSize(undefined), null);
  assert.equal(clampSize(''), null);
  assert.equal(clampSize('нет'), null);
  assert.equal(clampSize(0), null);
  assert.equal(clampSize(-30), null);
});

test('ширина зажимается в диапазон и округляется', () => {
  assert.equal(clampSize(45.4), 45);
  assert.equal(clampSize(45.6), 46);
  assert.equal(clampSize(3), MIN_SIZE);
  assert.equal(clampSize(240), MAX_SIZE);
  assert.equal(clampSize('55'), 55);
});

test('ширина читается из data-width', () => {
  assert.equal(sizeFromElement(element({ 'data-width': '45' })), 45);
});

test('ширина читается из стиля, если атрибут потеряли', () => {
  assert.equal(sizeFromElement(element({ style: 'width: 45%; margin-left: auto' })), 45);
  assert.equal(sizeFromElement(element({ style: 'color: red;width:30%' })), 30);
});

test('max-width и min-width за ширину НЕ принимаются', () => {
  /* Ровно это свойство стоит у картинок из внешних документов: приняв его за
     заданную ширину, редактор растянул бы на всю колонку картинку, которой
     размер никто не задавал. */
  assert.equal(sizeFromElement(element({ style: 'max-width: 100%' })), null);
  assert.equal(sizeFromElement(element({ style: 'min-width: 50%' })), null);
  assert.equal(sizeFromElement(element({ style: 'max-width: 100%; width: 45%' })), 45);
});

test('ширина в пикселях и высота в процентах шириной не считаются', () => {
  assert.equal(sizeFromElement(element({ style: 'width: 600px' })), null);
  assert.equal(sizeFromElement(element({ style: 'height: 20%' })), null);
});

test('картинка без атрибутов остаётся без размера', () => {
  assert.equal(sizeFromElement(element({})), null);
});

test('атрибут важнее стиля', () => {
  assert.equal(
    sizeFromElement(element({ 'data-width': '30', style: 'width: 80%' })),
    30
  );
});

test('выравнивание принимает только три известных значения', () => {
  assert.equal(normalizeAlign('center'), 'center');
  assert.equal(normalizeAlign('justify'), null);
  assert.equal(normalizeAlign(''), null);
  assert.equal(normalizeAlign(null), null);
});

test('выравнивание пишется ОТДЕЛЬНЫМИ полями, а не сокращённым margin', () => {
  const centered = styleFor({ size: 45, align: 'center' });
  assert.equal(centered, 'width: 45%; margin-left: auto; margin-right: auto');
  assert.ok(!/margin\s*:/.test(centered), 'сокращённое margin санитайзер выбросит');
  assert.equal(styleFor({ size: 40, align: 'right' }),
    'width: 40%; margin-left: auto; margin-right: 0');
  assert.equal(styleFor({ size: 40, align: 'left' }),
    'width: 40%; margin-left: 0; margin-right: auto');
});

test('display и float в стиль не попадают', () => {
  /* Обоих НЕТ в белом списке серверного санитайзера, и появиться там они не
     должны: display прячет куски регламента от читателя, float ломает поток
     статьи. Блочность выровненной картинки задаёт правило в wiki-theme.css. */
  const style = styleFor({ size: 45, align: 'center' });
  assert.ok(!style.includes('display'));
  assert.ok(!style.includes('float'));
});

test('нетронутая картинка не получает стиля вовсе', () => {
  assert.equal(styleFor({ size: null, align: null }), '');
});

test('одно выравнивание без размера тоже работает', () => {
  assert.equal(styleFor({ size: null, align: 'center' }),
    'margin-left: auto; margin-right: auto');
});
