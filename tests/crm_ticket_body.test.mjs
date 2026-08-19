import test from 'node:test';
import assert from 'node:assert/strict';

import { MAX_LABEL, formatTicketBody, splitBodyLine }
  from '../src/components/crm/ticketBody.js';

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
