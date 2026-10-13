import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { officePhones, phoneSegments } from '../src/components/wiki/officePhones.js';

/* Поле «Телефон» офиса — свободный текст: два номера через запятую, добавочный,
   подпись рядом с номером. Ссылку на него раньше строили вычёркиванием всего, кроме
   цифр и плюса, и номера склеивались в один несуществующий — он набирался в портале
   и копировался во вкладке iCORE Phone с тостом «скопирован». Стережём разбор:
   сколько номеров, какой у каждого набираемый номер и где добавочный. */

const numbers = (text) => officePhones(text).map((part) => part.number);

test('один номер — один номер, как бы его ни записали', () => {
  assert.deepEqual(numbers('+7 707 705 08 80'), ['+77077050880']);
  assert.deepEqual(numbers('+7 (727) 355-12-34'), ['+77273551234']);
  assert.deepEqual(numbers('8 (7212) 50-60-70'), ['87212506070']);
  assert.deepEqual(numbers('8-701-123-45-67'), ['87011234567']);
  assert.deepEqual(numbers('  87011234567  '), ['87011234567']);
  // Городской без кода — шесть цифр: тоже номер.
  assert.deepEqual(numbers('50-60-70'), ['506070']);
});

test('два номера не склеиваются в один', () => {
  assert.deepEqual(numbers('+7 707 705 08 80, +7 727 333 22 11'), ['+77077050880', '+77273332211']);
  assert.deepEqual(numbers('+7 707 705 08 80 / 8 727 333 22 11'), ['+77077050880', '87273332211']);
  assert.deepEqual(numbers('8-701-123-45-67; 8-702-765-43-21'), ['87011234567', '87027654321']);
  assert.deepEqual(numbers('+7 701 111 22 33\n+7 702 222 33 44'), ['+77011112233', '+77022223344']);
  assert.deepEqual(numbers('+7 701 123 45 67 или 8 727 222 33 44'), ['+77011234567', '87272223344']);
  // Ни один из номеров не длиннее обычного: склейки нет.
  for (const number of numbers('+7 707 705 08 80, +7 727 333 22 11')) {
    assert.ok(number.replace(/\D/g, '').length === 11, number);
  }
});

test('добавочный — отдельно от набираемого номера', () => {
  assert.deepEqual(officePhones('8 (7212) 50-60-70 доб. 12'),
    [{ text: '8 (7212) 50-60-70 доб. 12', number: '87212506070', ext: '12' }]);
  // Один добавочный знак не прирастает к номеру (12 цифр выглядели бы правдоподобно).
  assert.deepEqual(numbers('+7 707 705 08 80 доб. 5'), ['+77077050880']);
  assert.equal(officePhones('+7 707 705 08 80 доб. 5')[0].ext, '5');
  // Как только ни пишут добавочный.
  assert.equal(officePhones('8 727 355 12 34 вн.205')[0].ext, '205');
  assert.equal(officePhones('8 727 355 12 34 внутр. 3')[0].ext, '3');
  assert.equal(officePhones('8 727 355 12 34 (ДОБ 7)')[0].ext, '7');
  assert.equal(officePhones('8 727 355 12 34 доп.: 41')[0].ext, '41');
  assert.equal(officePhones('+7 701 123 45 67#3')[0].ext, '3');
  assert.equal(officePhones('+7 701 123 45 67 ext. 15')[0].ext, '15');
  for (const text of ['8 727 355 12 34 вн.205', '8 727 355 12 34 (ДОБ 7)', '8 727 355 12 34 доп.: 41']) {
    assert.deepEqual(numbers(text), ['87273551234'], text);
  }
});

test('добавочный через запятую принадлежит номеру перед ним', () => {
  const [phone, ...rest] = officePhones('8 (7212) 50-60-70, доб. 12');
  assert.deepEqual(phone, { text: '8 (7212) 50-60-70, доб. 12', number: '87212506070', ext: '12' });
  assert.equal(rest.length, 0);
  // У второго номера свой добавочный, у первого — нет.
  const pair = officePhones('8-701-123-45-67; 8-702-765-43-21 доб 7');
  assert.deepEqual(pair.map((p) => [p.number, p.ext]), [['87011234567', ''], ['87027654321', '7']]);
});

test('подписи и посторонние цифры в номер не попадают', () => {
  assert.deepEqual(numbers('Офис 2: +7 701 123 45 67'), ['+77011234567']);
  assert.deepEqual(numbers('+7 (727) 355-12-34 Айгерим'), ['+77273551234']);
  assert.deepEqual(numbers('с 9 до 18: 8 727 355 12 34'), ['87273551234']);
  // «…34/35» — второй номер сокращён до хвоста: набрать его нельзя, ссылкой не делаем.
  assert.deepEqual(numbers('8 (727) 355-12-34/35'), ['87273551234']);
  // Слишком коротко для телефона и слишком длинно для одного номера.
  assert.deepEqual(numbers('офис 204'), []);
  assert.deepEqual(numbers('1414'), []);
  assert.deepEqual(numbers('87077050880 87273332211 87011234567'), [], 'три номера без разделителя — не номер');
  assert.deepEqual(numbers('Доб. 5'), []);
  assert.deepEqual(numbers(''), []);
  assert.deepEqual(numbers(null), []);
  assert.deepEqual(numbers(undefined), []);
});

test('куски складываются обратно в исходный текст', () => {
  const samples = [
    '+7 707 705 08 80, +7 727 333 22 11',
    '8 (7212) 50-60-70, доб. 12',
    '8 (727) 355-12-34/35',
    'Офис 2: +7 701 123 45 67 ; звонить после 10',
    '+7 701 111 22 33\n+7 702 222 33 44',
    'нет телефона',
  ];
  for (const text of samples) {
    assert.equal(phoneSegments(text).map((part) => part.text).join(''), text, text);
  }
  // Разделители и то, что номером не читается, — куски без number: они остаются текстом.
  assert.deepEqual(phoneSegments('+7 707 705 08 80, +7 727 333 22 11').map((p) => !!p.number), [true, false, true]);
  assert.deepEqual(phoneSegments('нет телефона'), [{ text: 'нет телефона' }]);
});

test('модуль разбирается старым Safari: без просмотра назад в регулярных выражениях', () => {
  /* Литерал с (?<=…) или (?<!…) — синтаксическая ошибка для Safari до 16.4: модуль не
     загрузился бы целиком, а с ним и вкладка «Офисы» портала на таком телефоне. */
  const source = readFileSync(new URL('../src/components/wiki/officePhones.js', import.meta.url), 'utf8');
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.doesNotMatch(code, /\(\?<[=!]/);
});
