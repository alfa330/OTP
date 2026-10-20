/*
 * Время сообщения в ленте «Чатов ОП» (src/components/wazzup/messageTime.js).
 *
 * 1. ДЕНЬ ЛЕНТЫ — МЕСТНЫЙ. Сервер отдаёт время строкой в UTC (`…+00:00`), и срез
 *    `slice(0, 10)` давал день по UTC: переписка с 00:00 до 05:00 по Алматы
 *    вставала под разделитель предыдущей даты.
 *
 * 2. ОПОЗДАВШЕЕ ВХОДЯЩЕЕ ОБЪЯСНЯЕТ СЕБЯ. Wazzup досылает накопленное за простой
 *    канала со временем отправки; в ленте такое сообщение стоит там, где его
 *    увидел оператор, а когда клиент его написал — в подсказке. У исходящих и у
 *    сообщений без wazzupDt подсказки нет.
 *
 * Часовой пояс закреплён: прогон в CI идёт в UTC, у пользователей — Алматы.
 */
process.env.TZ = 'Asia/Almaty';

import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  compareMessageTime, lateDeliveryNote, localDayKey, messageTimeKey,
} from '../src/components/wazzup/messageTime.js';
import { mergePilotMessages } from '../src/components/wazzup/chatPilot.js';

test('день ленты считается по местному времени, а не по UTC-строке', () => {
  assert.equal(localDayKey('2026-10-05T18:59:00+00:00'), '2026-10-05'); // 23:59 Алматы
  assert.equal(localDayKey('2026-10-05T19:00:00+00:00'), '2026-10-06'); // 00:00 Алматы
  assert.equal(localDayKey('2026-10-05T23:30:00.000102+00:00'), '2026-10-06'); // 04:30
  assert.equal(localDayKey(''), '');
  assert.equal(localDayKey(null), '');
  assert.equal(localDayKey('не дата'), '');
});

test('опоздавшее входящее: в подсказке — когда клиент его отправил', () => {
  const msg = {
    isEcho: false,
    dt: '2026-10-05T07:35:00.000102+00:00', // 12:35 — минута, когда дошло до Wazzup
    wazzupDt: '2026-09-25T13:45:00.002+00:00', // 25.09 18:45 по WhatsApp
  };
  assert.equal(lateDeliveryNote(msg), 'Клиент отправил 25.09 в 18:45, в Wazzup сообщение пришло позже');
});

test('опоздание в тот же день — без даты, через год — с годом', () => {
  assert.equal(
    lateDeliveryNote({ isEcho: false, dt: '2026-10-05T07:35:00+00:00', wazzupDt: '2026-10-05T07:27:00.001+00:00' }),
    'Клиент отправил в 12:27, в Wazzup сообщение пришло позже',
  );
  assert.equal(
    lateDeliveryNote({ isEcho: false, dt: '2026-01-02T05:00:00+00:00', wazzupDt: '2025-12-30T13:45:00.001+00:00' }),
    'Клиент отправил 30.12.2025 в 18:45, в Wazzup сообщение пришло позже',
  );
});

test('без подсказки: исходящее, нет wazzupDt, мусор', () => {
  const late = '2026-09-25T13:45:00.002+00:00';
  assert.equal(lateDeliveryNote({ isEcho: true, dt: '2026-10-05T07:35:00+00:00', wazzupDt: late }), '');
  assert.equal(lateDeliveryNote({ isEcho: false, dt: '2026-10-05T07:35:00+00:00', wazzupDt: null }), '');
  assert.equal(lateDeliveryNote({ isEcho: false, dt: '2026-10-05T07:35:00+00:00' }), '');
  assert.equal(lateDeliveryNote({ isEcho: false, dt: '2026-10-05T07:35:00+00:00', wazzupDt: 'не дата' }), '');
  assert.equal(lateDeliveryNote(null), '');
});

test('лента делит дни местным днём и показывает подсказку у опоздавших', () => {
  const view = readFileSync(new URL('../src/components/wazzup/WazzupChatsView.jsx', import.meta.url), 'utf8');
  assert.match(view, /const day = localDayKey\(m\.dt\);/);
  assert.doesNotMatch(view, /\(m\.dt \|\| ''\)\.slice\(0, 10\)/);
  assert.match(view, /const lateNote = lateDeliveryNote\(msg\);/);
  // подсказка рисуется именно по lateNote — а не лежит в файле мёртвой веткой
  assert.match(view, /\{lateNote \? \(\s*<span[^>]*title=\{lateNote\}/);
});

/*
 * 3. ПОРЯДОК ЛЕНТЫ — ДО МИКРОСЕКУНДЫ, как у сервера. Ответ клиента «2ГИС»,
 *    пришедший после ответа оператора из окна iCORE, сервер ставит на 1 мкс позже
 *    ответа (wazzup/delivery.py). Date хранит миллисекунды: по нему они равны, и
 *    порядок решал бы случайный messageId (жалоба 19.10.2026).
 */
test('ключ времени различает микросекунды и понимает любую дробь', () => {
  const base = Date.parse('2026-10-19T06:23:29.565Z') * 1000;
  assert.equal(messageTimeKey('2026-10-19T06:23:29.565821+00:00'), base + 821);
  assert.equal(messageTimeKey('2026-10-19T06:23:29.565Z'), base, 'миллисекунды браузера');
  assert.equal(messageTimeKey('2026-10-19T06:23:29.56501+00:00'), base + 10);
  assert.equal(messageTimeKey('2026-10-19T06:23:29+00:00'), base - 565000, 'isoformat без дроби');
  assert.ok(Number.isSafeInteger(messageTimeKey('2026-10-19T06:23:29.565821+00:00')));
  assert.ok(Number.isNaN(messageTimeKey('не дата')));
});

test('ответы клиента идут за вопросом оператора, хотя их id «меньше»', () => {
  const question = { messageId: 'f716', dt: '2026-10-19T06:23:29.565821+00:00', isEcho: true };
  const replies = [
    { messageId: 'd226', dt: '2026-10-19T06:23:29.565822+00:00' },
    { messageId: '0d7e', dt: '2026-10-19T06:23:29.565823+00:00' },
    { messageId: '1c52', dt: '2026-10-19T06:23:29.565824+00:00' },
  ];
  const expected = ['f716', 'd226', '0d7e', '1c52'];
  assert.deepEqual([...replies, question].sort(compareMessageTime).map((m) => m.messageId), expected);
  assert.deepEqual(mergePilotMessages([question], replies.slice().reverse()).map((m) => m.messageId), expected);
  // равное время — по id, как ORDER BY dt, message_id
  assert.deepEqual([{ messageId: 'b', dt: question.dt }, { messageId: 'a', dt: question.dt }]
    .sort(compareMessageTime).map((m) => m.messageId), ['a', 'b']);
});

test('лента в разделе сортируется тем же порядком', () => {
  const view = readFileSync(new URL('../src/components/wazzup/WazzupChatsView.jsx', import.meta.url), 'utf8');
  assert.match(view, /\]\.sort\(compareMessageTime\);/);
  assert.doesNotMatch(view, /new Date\(a\.dt\) - new Date\(b\.dt\)/);
});
