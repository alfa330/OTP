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

/* Обращение НЫНЕШНЕГО формата (ТЗ задачи #206): данные водителя подписанными
 * строками, проверенные пункты — поштучно со знаком. */
const FULL = [
  '⚠️ Возможный массовый сбой',
  '',
  'ИИН: 060606060606',
  'Таксопарк: iTaxi',
  'Город: Алматы',
  'Отчётный период: февраль 2026',
  '',
  'Тип ошибки: Сайт не загружается',
  '',
  '🔍 Проверено оператором: 1 из 3',
  '✅ Комиссия парка списывалась',
  '❌ Провайдер менялся',
  '❔ Документы в Sapar отображаются: неизвестно',
  '',
  '✅ Выполнено: перезашёл · сменил устройство',
  '❗ Не выполнено: связался с парком',
].join('\n');

/* Обращения, заведённые ДО #206, лежат в базе прежним текстом и задним числом
 * не пересобираются — карточка обязана читать и его. */
const LEGACY = [
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
                   [BLOCK_WARNING, BLOCK_LIST, BLOCK_LIST, BLOCK_CHECKS, BLOCK_CHECKS]);
});

test('старый формат обращения разбирается по-прежнему', () => {
  assert.deepEqual(describeBody(LEGACY).map((block) => block.kind),
                   [BLOCK_WARNING, BLOCK_CONTEXT, BLOCK_LIST, BLOCK_CHECKS]);
  assert.deepEqual(describeBody(LEGACY)[1].chips,
                   ['iTaxi Sapar', 'Алматы', 'период февраль 2026']);
});

test('данные водителя остаются подписанными строками, а не метками', () => {
  const data = describeBody(FULL)[1];
  assert.equal(data.kind, BLOCK_LIST);
  assert.deepEqual(data.rows.map((row) => row.label),
                   ['ИИН:', 'Таксопарк:', 'Город:', 'Отчётный период:']);
});

test('подтвердилось и не подтвердилось различаются не только цветом', () => {
  const checks = describeBody(FULL)[3];
  assert.equal(checks.rows[0].tone, 'slate');
  assert.deepEqual(checks.rows[0].value, '1 из 3');
  assert.deepEqual(checks.rows.slice(1).map((row) => row.tone),
                   ['green', 'rose', 'slate']);
  // «Неизвестно» остаётся словом: знак его не передаёт.
  assert.equal(checks.rows[3].value, 'неизвестно');
});

test('метка сбоя отдаётся текстом без самого значка', () => {
  const [warning] = describeBody(FULL);
  assert.deepEqual(warning.rows, [{
    tone: 'amber', label: null, value: 'Возможный массовый сбой', items: null,
  }]);
});

test('перечень остаётся парами «подпись / ответ»', () => {
  const list = describeBody(FULL)[2];
  assert.deepEqual(list.rows, [
    { label: 'Тип ошибки:', value: 'Сайт не загружается' },
  ]);
});

test('хвост несёт тон и разбивается на элементы', () => {
  const checks = describeBody(FULL)[4];
  assert.deepEqual(checks.rows[0], {
    tone: 'green', label: 'Выполнено', value: 'перезашёл · сменил устройство',
    items: ['перезашёл', 'сменил устройство'],
  });
  assert.equal(checks.rows[1].tone, 'red');
  assert.equal(checks.rows[1].items, null);
  // «3 из 3» на элементы НЕ дробится — это одна величина.
  assert.deepEqual(describeBody(LEGACY)[3].rows[2],
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

test('дайджест — ответы первого перечня, у старых обращений — контекст', () => {
  assert.equal(bodyDigest(FULL), '060606060606 · iTaxi · Алматы · февраль 2026');
  assert.equal(bodyDigest(LEGACY), 'iTaxi Sapar · Алматы · период февраль 2026');
  assert.equal(bodyDigest('Тип ошибки: Сайт не загружается\nБраузер: Chrome'),
               'Сайт не загружается · Chrome');
  // Одна строка — подпись нужна: без неё «Астана» ничего не объясняет.
  assert.equal(bodyDigest('Город: Астана'), 'Город: Астана');
});
