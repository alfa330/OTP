import test from 'node:test';
import assert from 'node:assert/strict';

import {
  GRID_COLUMNS,
  gridPalette,
  hexToHsv,
  hsvToHex,
  isLightColor,
  normalizeHex,
} from '../src/components/ui/colorMath.js';

test('код цвета приводится к #RRGGBB, мусор — пустая строка', () => {
  assert.equal(normalizeHex('#ffd400'), '#FFD400');
  assert.equal(normalizeHex('FFD400'), '#FFD400');
  assert.equal(normalizeHex('#fd4'), '#FFDD44');
  assert.equal(normalizeHex(' #0aF '), '#00AAFF');
  for (const broken of ['', 'red', '#12345', '#GGGGGG', null, undefined]) assert.equal(normalizeHex(broken), '');
});

test('HEX → HSV → HEX возвращает тот же цвет', () => {
  for (const hex of ['#FFD400', '#1A1916', '#000000', '#FFFFFF', '#3B82F6', '#86198F', '#7F7F7F']) {
    assert.equal(hsvToHex(hexToHsv(hex)), hex);
  }
  const yellow = hexToHsv('#FFFF00');
  assert.equal(Math.round(yellow.h), 60);
  assert.equal(yellow.s, 1);
  assert.equal(yellow.v, 1);
  assert.equal(hsvToHex({ h: 0, s: 0, v: 0.5 }), '#808080');
});

test('сетка как в iOS: 10 строк по 12, первая — серые от белого к чёрному, без повторов', () => {
  const grid = gridPalette();
  assert.equal(grid.length, 10);
  assert.ok(grid.every((row) => row.length === GRID_COLUMNS));
  assert.equal(grid[0][0], '#FFFFFF');
  assert.equal(grid[0][GRID_COLUMNS - 1], '#000000');
  const flat = grid.flat();
  assert.ok(flat.every((hex) => normalizeHex(hex) === hex));
  assert.equal(new Set(flat).size, flat.length);
});

test('светлый ли цвет — для галочки на образце', () => {
  assert.equal(isLightColor('#FFFFFF'), true);
  assert.equal(isLightColor('#FFD400'), true);
  assert.equal(isLightColor('#1A1916'), false);
  assert.equal(isLightColor('#2563EB'), false);
});
