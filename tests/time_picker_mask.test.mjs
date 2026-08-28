/*
 * Маска ввода в поле времени: двоеточие ставится само.
 *
 * Примитив общий — им набирают расписание офиса (до 28 полей на экране),
 * выгрузки и заявки по сменам, поэтому у ввода есть контракт, который легко
 * сломать «улучшением»:
 *   - «1300» на четвёртой цифре обязано стать «13:00»;
 *   - «930» обязано ОСТАТЬСЯ «930» — три цифры без разделителя это 9:30
 *     (см. parseTimeInput), и маска после двух цифр сделала бы из них «93:0»,
 *     то есть негодный набор вместо нормального времени;
 *   - явно набранный разделитель не трогаем: «9:3» это 09:03.
 * Плюс каретка: она должна вставать по числу цифр слева, иначе курсор прыгает
 * в конец при правке середины — ровно то, из-за чего живое форматирование
 * обычно и не делают.
 *
 * Браузерного окружения в проекте нет, поэтому проверяем чистую функцию:
 * она и вынесена из компонента ради этого.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { transformSync } = require('esbuild');
const { readFileSync, writeFileSync, mkdirSync } = require('node:fs');
const { join } = require('node:path');

const SOURCE = new URL('../src/components/ui/TimePicker.jsx', import.meta.url);

async function loadPicker() {
  const jsx = readFileSync(SOURCE, 'utf8');
  const { code } = transformSync(jsx, { loader: 'jsx', format: 'esm', target: 'node18' });
  const patched = code
    .replace(
      /import\s*\{[^}]*\}\s*from\s*['"]lucide-react['"];?/,
      'const Stub = () => null; const ChevronUp = Stub;',
    )
    .replace(/import\s*\{[^}]*\}\s*from\s*['"]react-dom['"];?/, 'const createPortal = () => null;')
    .replace(/import\s+React[^;]*from\s*['"]react['"];?/, 'const React = { createElement: () => null };');
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const file = join(dir, 'TimePickerMask.mjs');
  writeFileSync(file, patched, 'utf8');
  return import(`file://${file.replace(/\\/g, '/')}`);
}

const { maskTimeInput, parseTimeInput } = await loadPicker();

test('четыре цифры подряд получают двоеточие сразу', () => {
  assert.equal(maskTimeInput('1300', 4).text, '13:00');
  assert.equal(maskTimeInput('0930', 4).text, '09:30');
  assert.equal(maskTimeInput('2359', 4).text, '23:59');
});

test('набор до четвёртой цифры не трогаем', () => {
  assert.equal(maskTimeInput('1', 1).text, '1');
  assert.equal(maskTimeInput('13', 2).text, '13');
  assert.equal(maskTimeInput('130', 3).text, '130');
});

test('«930» остаётся собой и читается как 9:30', () => {
  // Маска после двух цифр сделала бы «93:0» — набор, который откатится.
  assert.equal(maskTimeInput('930', 3).text, '930');
  assert.equal(parseTimeInput('930'), 9 * 60 + 30);
});

test('явный разделитель маска не переписывает', () => {
  assert.equal(maskTimeInput('9:3', 3).text, '9:3');
  assert.equal(maskTimeInput('9:30', 4).text, '9:30');
  assert.equal(parseTimeInput('9:3'), 9 * 60 + 3);
});

test('каретка встаёт по числу цифр слева от неё', () => {
  // Курсор в конце — после форматирования тоже в конце.
  assert.equal(maskTimeInput('1300', 4).caret, 5);
  // Курсор после первой цифры — цифр слева одна, двоеточие правее.
  assert.equal(maskTimeInput('1300', 1).caret, 1);
  // Курсор после второй цифры — двоеточие уже слева, сдвигаемся на него.
  assert.equal(maskTimeInput('1300', 2).caret, 3);
});

test('мусор отсекается, длина ограничена', () => {
  assert.equal(maskTimeInput('1a3b0c0', 7).text, '13:00');
  assert.equal(maskTimeInput('130055', 6).text, '13:00');
});

test('пустой ввод остаётся пустым', () => {
  assert.equal(maskTimeInput('', 0).text, '');
  assert.equal(maskTimeInput(null, null).text, '');
});
