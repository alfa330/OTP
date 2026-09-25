import test from 'node:test';
import assert from 'node:assert/strict';

import {
  authHeadersFor,
  hasPhoneHost,
  isTokenFresh,
  parseHostMessage,
  parseTokenMessage,
  postToPhone,
  readJwtExpiry,
  requestPhoneToken,
  waitForViewport,
} from '../src/trainers_embed/phoneBridge.js';

/* Мост страницы тренажёров с iCORE Phone. Проверяем то, из-за чего учёт попыток
   мог бы молча пропасть: разбор ответа телефона, срок токена, поведение вне
   телефона (в обычном браузере моста нет — страница обязана жить и без него). */

const jwtWithExp = (exp) => {
  const b64url = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64')
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url({ alg: 'HS256', typ: 'JWT' })}.${b64url({ sub: '42', exp })}.sig`;
};

const fakeHost = ({ reply, delayMs = 0 } = {}) => {
  const listeners = new Set();
  const sent = [];
  const win = {
    chrome: {
      webview: {
        postMessage(message) {
          sent.push(message);
          if (reply === undefined) return;
          setTimeout(() => {
            for (const fn of listeners) fn({ data: typeof reply === 'function' ? reply(message) : reply });
          }, delayMs);
        },
        addEventListener(_type, fn) { listeners.add(fn); },
        removeEventListener(_type, fn) { listeners.delete(fn); },
      },
    },
  };
  return { win, sent, listeners };
};

test('вне телефона моста нет: сообщения не уходят, токен не запрашивается', async () => {
  assert.equal(hasPhoneHost({}), false);
  assert.equal(hasPhoneHost(undefined), false);
  assert.equal(postToPhone({ type: 'ready' }, {}), false);
  assert.equal(await requestPhoneToken({}, 50), null);
});

test('срок токена читается из exp, запас учитывается', () => {
  const now = 1_800_000_000_000; // мс
  const soon = jwtWithExp(now / 1000 + 60);
  const later = jwtWithExp(now / 1000 + 600);
  assert.equal(readJwtExpiry(later), now / 1000 + 600);
  assert.equal(isTokenFresh(later, 120, now), true);
  assert.equal(isTokenFresh(soon, 120, now), false, 'до истечения меньше запаса — токен не свежий');
  assert.equal(isTokenFresh('', 120, now), false);
  // Не JWT — срок не разобрать, решает сервер.
  assert.equal(readJwtExpiry('opaque-token'), 0);
  assert.equal(isTokenFresh('opaque-token', 120, now), true);
});

test('ответ телефона разбирается и объектом, и строкой; чужие сообщения — null', () => {
  const parsed = parseTokenMessage({ type: 'auth:token', token: ' abc ', user_id: '7', phone_version: '3.22.25' });
  assert.deepEqual(parsed, { token: 'abc', userId: 7, phoneVersion: '3.22.25', error: '' });
  assert.deepEqual(parseTokenMessage(JSON.stringify({ type: 'auth:token', token: '', error: 'Нет сессии' })),
    { token: '', userId: 0, phoneVersion: '', error: 'Нет сессии' });
  assert.equal(parseTokenMessage({ type: 'something' }), null);
  assert.equal(parseTokenMessage('not json'), null);
  assert.equal(parseTokenMessage(null), null);
});

test('запрос токена: страница шлёт auth:request и получает ответ телефона', async () => {
  const { win, sent, listeners } = fakeHost({ reply: { type: 'auth:token', token: 'tok', user_id: 42 } });
  const reply = await requestPhoneToken(win, 500);
  assert.deepEqual(sent, [{ type: 'auth:request' }]);
  assert.equal(reply.token, 'tok');
  assert.equal(reply.userId, 42);
  assert.equal(listeners.size, 0, 'слушатель снят после ответа');
});

test('запрос токена: телефон молчит — таймаут, страница работает без записи', async () => {
  const { win, listeners } = fakeHost();
  const reply = await requestPhoneToken(win, 30);
  assert.equal(reply, null);
  assert.equal(listeners.size, 0, 'слушатель снят и по таймауту');
});

test('сообщения телефона: объект или строка с type; остальное — null', () => {
  assert.deepEqual(parseHostMessage({ type: 'trainer:host', framed: true, width: 1900, height: 1000 }),
    { type: 'trainer:host', framed: true, width: 1900, height: 1000 });
  assert.deepEqual(parseHostMessage('{"type":"trainer:close-request"}'), { type: 'trainer:close-request' });
  assert.equal(parseHostMessage({ framed: true }), null);
  assert.equal(parseHostMessage({ type: '' }), null);
  assert.equal(parseHostMessage('oops'), null);
  assert.equal(parseHostMessage(undefined), null);
});

/* Тренажёр монтируется после того, как телефон вынес страницу в большое окно:
   проигрыватель меряет ширину один раз, и смонтированный раньше времени он остался
   бы сжатым. Три исхода: ширина уже другая, resize пришёл, resize не пришёл. */
const fakeViewport = (width) => {
  const listeners = new Set();
  const win = {
    innerWidth: width,
    addEventListener(_type, fn) { listeners.add(fn); },
    removeEventListener(_type, fn) { listeners.delete(fn); },
    resize(nextWidth) { win.innerWidth = nextWidth; for (const fn of listeners) fn(); },
  };
  return { win, listeners };
};

test('ожидание окна: ширина уже сменилась — сразу', async () => {
  const { win } = fakeViewport(1500);
  assert.equal(await waitForViewport(480, 50, win), true);
});

test('ожидание окна: пришёл resize — продолжаем, слушатель снят', async () => {
  const { win, listeners } = fakeViewport(480);
  const waiting = waitForViewport(480, 500, win);
  setTimeout(() => win.resize(1500), 5);
  assert.equal(await waiting, true);
  assert.equal(listeners.size, 0);
});

test('ожидание окна: телефон окно не открыл — по таймауту монтируем как есть', async () => {
  const { win, listeners } = fakeViewport(480);
  assert.equal(await waitForViewport(480, 20, win), false);
  assert.equal(listeners.size, 0);
  // Вне браузера (нет окна) — тоже не виснем.
  assert.equal(await waitForViewport(480, 20, undefined), false);
});

test('заголовки для API вики только при токене', () => {
  assert.deepEqual(authHeadersFor('tok'), { Authorization: 'Bearer tok', 'X-Auth-Transport': 'bearer' });
  assert.equal(authHeadersFor(''), null);
});
