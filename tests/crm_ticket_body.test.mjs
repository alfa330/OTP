import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BLOCK_CHECKS, BLOCK_CONTEXT, BLOCK_LIST, BLOCK_WARNING, MAX_LABEL,
  bodyDigest, describeBody, describeMarkedLine, formatTicketBody, splitBodyLine,
} from '../src/components/crm/ticketBody.js';

/* Разбор готового текста обращения для карточки. Формат задаёт сервер
 * (crm/scenarios.py::render_body), здесь проверяется, что карточка читает его
 * так же, как crm/telegram.py::format_body. */

test('строка перечня делится на подпись и ответ', () => {
  assert.deepEqual(splitBodyLine('Устройство: Android'),
                   { label: 'Устройство:', value: 'Android' });
});

test('строка без двоеточия остаётся целой', () => {
  assert.deepEqual(splitBodyLine('iTaxi · Алматы · период февраль 2026'),
                   { text: 'iTaxi · Алматы · период февраль 2026' });
  assert.deepEqual(splitBodyLine('⚠️ Возможный массовый сбой'),
                   { text: '⚠️ Возможный массовый сбой' });
});

test('двоеточие внутри длинной фразы подписью не считается', () => {
  const long = `${'о'.repeat(MAX_LABEL + 5)}: ответ`;
  assert.deepEqual(splitBodyLine(long), { text: long });
});

test('двоеточие в самом начале не делает пустую подпись', () => {
  assert.deepEqual(splitBodyLine(': значение'), { text: ': значение' });
});

test('делится только ПЕРВОЕ двоеточие — в ответе оно уцелеет', () => {
  assert.deepEqual(splitBodyLine('Текст ошибки: ошибка: 500'),
                   { label: 'Текст ошибки:', value: 'ошибка: 500' });
});

test('пустая строка разделяет блоки', () => {
  const body = [
    'iTaxi · Алматы',
    '',
    'Тип ошибки: Сайт не загружается',
    'Браузер: Chrome',
    '',
    '✅ Проверено: повторный вход',
  ].join('\n');
  const blocks = formatTicketBody(body);
  assert.equal(blocks.length, 3);
  assert.deepEqual(blocks[0], [{ text: 'iTaxi · Алматы' }]);
  assert.equal(blocks[1].length, 2);
  assert.deepEqual(blocks[2], [{ label: '✅ Проверено:', value: 'повторный вход' }]);
});

test('подряд идущие пустые строки не рождают пустых блоков', () => {
  const blocks = formatTicketBody('раз\n\n\n\nдва');
  assert.equal(blocks.length, 2);
});

test('старые обращения без пустых строк остаются одним блоком', () => {
  const blocks = formatTicketBody('ИИН водителя: 060606202020\nПарк: iTaxi');
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].length, 2);
});

test('пустой текст не роняет карточку', () => {
  assert.deepEqual(formatTicketBody(''), []);
  assert.deepEqual(formatTicketBody(null), []);
  assert.deepEqual(formatTicketBody(undefined), []);
});

/* ─── Смысловые виды блоков ──────────────────────────────────────────────────
 * Раньше карточка рисовала все блоки одинаково, и обращение выглядело полотном
 * текста. Теперь вид блока выводится из содержимого — и это ровно то, что здесь
 * проверяется: формат задаёт сервер (crm/scenarios.py::render_body), и разойтись
 * с ним нельзя молча. */

const FULL = [
  '⚠️ Возможный массовый сбой',
  '',
  'iTaxi Sapar · Алматы · период февраль 2026',
  '',
  'Тип ошибки: Сайт не загружается',
  'Нет: акт подписан · документ загружен',
  '',
  '✅ Проверено: перезашёл · сменил устройство',
  '❗ Не выполнено: связался с парком',
  '✔️ Чек-лист выполнен: 3 из 3',
].join('\n');

test('блоки опознаются по содержимому, а не по номеру', () => {
  assert.deepEqual(describeBody(FULL).map((block) => block.kind),
                   [BLOCK_WARNING, BLOCK_CONTEXT, BLOCK_LIST, BLOCK_CHECKS]);
});

test('метка сбоя отдаётся текстом без самого значка', () => {
  const [warning] = describeBody(FULL);
  assert.deepEqual(warning.rows, [{
    tone: 'amber', label: null, value: 'Возможный массовый сбой', items: null,
  }]);
});

test('контекст разбирается на метки поштучно', () => {
  const context = describeBody(FULL)[1];
  assert.deepEqual(context.chips, ['iTaxi Sapar', 'Алматы', 'период февраль 2026']);
});

test('перечень остаётся парами «подпись / ответ»', () => {
  const list = describeBody(FULL)[2];
  assert.deepEqual(list.rows, [
    { label: 'Тип ошибки:', value: 'Сайт не загружается' },
    { label: 'Нет:', value: 'акт подписан · документ загружен' },
  ]);
});

test('хвост несёт тон и разбивается на элементы', () => {
  const checks = describeBody(FULL)[3];
  assert.deepEqual(checks.rows[0], {
    tone: 'green', label: 'Проверено', value: 'перезашёл · сменил устройство',
    items: ['перезашёл', 'сменил устройство'],
  });
  assert.equal(checks.rows[1].tone, 'red');
  assert.equal(checks.rows[1].items, null);
  // «3 из 3» на элементы НЕ дробится — это одна величина.
  assert.deepEqual(checks.rows[2],
                   { tone: 'blue', label: 'Чек-лист выполнен', value: '3 из 3', items: null });
});

test('строка без маркера остаётся собой', () => {
  assert.deepEqual(describeMarkedLine('Просто строка'),
                   { tone: null, label: null, value: 'Просто строка', items: null });
});

test('тематика без метки, контекста и чек-листа даёт один перечень', () => {
  const blocks = describeBody('ИИН водителя: 060606202020\nПарк: iTaxi');
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].kind, BLOCK_LIST);
  assert.equal(blocks[0].rows.length, 2);
});

test('свободный текст тематики со своим шаблоном не считается контекстом', () => {
  const blocks = describeBody('Просим подтвердить выдачу термокороба.');
  assert.deepEqual(blocks.map((b) => b.kind), [BLOCK_LIST]);
});

test('пустой текст не роняет разбор', () => {
  assert.deepEqual(describeBody(''), []);
  assert.deepEqual(describeBody(null), []);
  assert.equal(bodyDigest(''), '');
});

test('дайджест — это контекст, а без него первая строка перечня', () => {
  assert.equal(bodyDigest(FULL), 'iTaxi Sapar · Алматы · период февраль 2026');
  assert.equal(bodyDigest('Тип ошибки: Сайт не загружается\nБраузер: Chrome'),
               'Тип ошибки: Сайт не загружается');
});
