import test from 'node:test';
import assert from 'node:assert/strict';

import { MIN_HEIGHT, fitHeight } from '../src/components/crm/layout.js';

/* Высота карточки раздела. Раньше это была константа `calc(100vh-300px)`, и
 * ошибка в вычитаемом выглядела как полоса пустоты под разделом. Теперь считаем
 * по факту — и арифметику проверяем здесь, потому что в разметке её никто не
 * проверит, а ошибка в ней читается как «раздел криво сверстан». */

test('карточка занимает всё, что осталось до низа видимой области', () => {
  assert.equal(fitHeight({ viewport: 900, offsetTop: 141, paddingBottom: 32 }), 727);
});

test('нижний отступ прокрутчика тоже занимает место', () => {
  const withPadding = fitHeight({ viewport: 900, offsetTop: 141, paddingBottom: 32 });
  const without = fitHeight({ viewport: 900, offsetTop: 141, paddingBottom: 0 });
  assert.equal(without - withPadding, 32);
});

test('высота шапки вычитается: чем больше рядов фильтров, тем ниже карточка', () => {
  const oneRow = fitHeight({ viewport: 844, offsetTop: 165, paddingBottom: 32 });
  const threeRows = fitHeight({ viewport: 844, offsetTop: 319, paddingBottom: 32 });
  assert.equal(oneRow, 647);
  assert.equal(threeRows, 493);
});

test('на низком экране карточка не проваливается ниже рабочего минимума', () => {
  assert.equal(fitHeight({ viewport: 500, offsetTop: 400, paddingBottom: 32 }), MIN_HEIGHT);
  // Даже если места не осталось вовсе или ушло в минус.
  assert.equal(fitHeight({ viewport: 200, offsetTop: 400, paddingBottom: 32 }), MIN_HEIGHT);
});

test('без замеров высоту не навязываем — иначе мигнули бы неправильной', () => {
  assert.equal(fitHeight(), null);
  assert.equal(fitHeight({}), null);
  assert.equal(fitHeight({ viewport: 0, offsetTop: 100 }), null);
  assert.equal(fitHeight({ viewport: 900 }), null);
  assert.equal(fitHeight({ viewport: 900, offsetTop: NaN }), null);
});

test('воздух под карточкой, если его попросят, тоже вычитается', () => {
  assert.equal(fitHeight({ viewport: 900, offsetTop: 141, paddingBottom: 32, gap: 12 }), 715);
});
