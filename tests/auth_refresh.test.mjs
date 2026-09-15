import test from 'node:test';
import assert from 'node:assert/strict';

import {
  AUTH_REFRESH_OUTCOME,
  classifyAuthRefreshResult,
  createAuthRetryingFetch,
  createAxiosAuthErrorHandler,
  createSharedAuthRefresh,
  isRecoverableAuthBody,
  readBearerToken,
  watchAuthTokensFromOtherTabs,
} from '../src/utils/authRefresh.js';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const API = 'https://otp-2-fos4.onrender.com';
const LOGGED_OUT = 'LOGGED_OUT';
const EXPIRED_BODY = { error: 'JWT authentication failed', code: 'TOKEN_EXPIRED' };

/* Один сервер и одна вкладка. Токен A1 уже истёк: сервер принимает только state.valid.
   Обновление выдаёт A2, A3… и кладёт его в «хранилище» вкладки state.stored.
   refresh: 'ok' | 'network' | <HTTP-статус ответа /api/auth/refresh> | 'revoked'
   ('revoked' — токен выдан, но сервер его уже не принимает: сессию отозвали). */
const makeTab = ({ refreshDelayMs = 40, refresh = 'ok' } = {}) => {
  const state = { valid: null, stored: 'A1', refreshCalls: 0, logouts: 0 };

  const refreshAuthSession = createSharedAuthRefresh(async () => {
    state.refreshCalls += 1;
    await sleep(refreshDelayMs);
    if (refresh === 'network') throw new TypeError('Failed to fetch');
    if (typeof refresh === 'number') return { status: refresh };
    state.stored = `A${state.refreshCalls + 1}`;
    if (refresh === 'ok') state.valid = state.stored;
    return { status: 200 };
  });
  const onSessionRejected = () => {
    state.logouts += 1;
    return LOGGED_OUT;
  };
  const nativeFetch = async (_input, init = {}) => {
    const token = readBearerToken(init.headers);
    if (token && token === state.valid) return new Response('{"status":"success"}', { status: 200 });
    return new Response(JSON.stringify(EXPIRED_BODY), { status: 401 });
  };

  return {
    state,
    refreshAuthSession,
    fetch: createAuthRetryingFetch({
      nativeFetch,
      refreshAuthSession,
      onSessionRejected,
      getCurrentAccessToken: () => state.stored,
      buildRetryHeaders: (headers) => ({ ...(headers || {}), Authorization: `Bearer ${state.stored}` }),
    }),
    onAxiosError: createAxiosAuthErrorHandler({
      refreshAuthSession,
      onSessionRejected,
      getCurrentAccessToken: () => state.stored,
      replay: async (config) => ({ status: 200, config, token: state.stored }),
    }),
    axios401: (path, token = state.stored, extra = {}) => ({
      config: { url: `${API}${path}`, headers: { Authorization: `Bearer ${token}` }, ...extra },
      response: { status: 401, data: EXPIRED_BODY },
    }),
    bearer: (token = state.stored) => ({ headers: { Authorization: `Bearer ${token}` } }),
  };
};

test('Яндекс: поток колокола (fetch) получил 401, пока токен обновляет axios, — сессия остаётся', async () => {
  const tab = makeTab({ refreshDelayMs: 60 });

  const summary = tab.onAxiosError(tab.axios401('/api/notifications'));
  await sleep(10);
  const stream = await tab.fetch(`${API}/api/notifications/stream`, tab.bearer('A1'));
  const replayed = await summary;

  assert.equal(stream.status, 200);
  assert.equal(replayed.token, 'A2');
  assert.equal(tab.state.refreshCalls, 1);
  assert.equal(tab.state.logouts, 0);
  assert.equal(tab.state.stored, 'A2');
});

test('и наоборот: обновление начал fetch, axios дожидается его же', async () => {
  const tab = makeTab({ refreshDelayMs: 60 });

  const stream = tab.fetch(`${API}/api/notifications/stream`, tab.bearer('A1'));
  await sleep(10);
  const replayed = await tab.onAxiosError(tab.axios401('/api/news/pending', 'A1'));

  assert.equal((await stream).status, 200);
  assert.equal(replayed.token, 'A2');
  assert.equal(tab.state.refreshCalls, 1);
  assert.equal(tab.state.logouts, 0);
});

test('401 пришёл уже после обновления — повтор со свежим токеном, без второй ротации', async () => {
  const tab = makeTab();
  await tab.refreshAuthSession();

  const late = await tab.fetch(`${API}/api/wiki/home`, tab.bearer('A1'));
  const lateAxios = await tab.onAxiosError(tab.axios401('/api/news/pending', 'A1'));

  assert.equal(late.status, 200);
  assert.equal(lateAxios.token, 'A2');
  assert.equal(tab.state.refreshCalls, 1);
  assert.equal(tab.state.logouts, 0);
});

test('обрыв сети на обновлении — вход сохраняется, токены на месте', async () => {
  const tab = makeTab({ refresh: 'network' });

  const response = await tab.fetch(`${API}/api/notifications`, tab.bearer());
  assert.equal(response.status, 401);

  await assert.rejects(tab.onAxiosError(tab.axios401('/api/news/pending')), (error) => {
    assert.equal(error.code, 'ERR_NETWORK');
    // Без response: restoreSession при старте оставит сохранённого пользователя.
    assert.equal(error.response, undefined);
    assert.equal(error.config.url, `${API}/api/news/pending`);
    return true;
  });
  assert.equal(tab.state.logouts, 0);
  assert.equal(tab.state.stored, 'A1');
});

test('5xx и 429 на обновлении (деплой на Render, перегрузка) — тоже не повод выкидывать на вход', async () => {
  for (const status of [500, 502, 503, 429]) {
    const tab = makeTab({ refresh: status });
    assert.equal((await tab.fetch(`${API}/api/notifications`, tab.bearer())).status, 401, `fetch при ${status}`);
    await assert.rejects(tab.onAxiosError(tab.axios401('/api/notifications')), { code: 'ERR_NETWORK' });
    assert.equal(tab.state.logouts, 0, `выход при ${status}`);
  }
});

test('сервер отверг сессию (401 на обновлении) — выход, и обновление одно на всю пачку', async () => {
  const tab = makeTab({ refresh: 401 });

  const results = await Promise.all([
    tab.fetch(`${API}/api/notifications/stream`, tab.bearer()),
    tab.onAxiosError(tab.axios401('/api/notifications')),
    tab.onAxiosError(tab.axios401('/api/news/pending')),
  ]);

  assert.deepEqual(results, [LOGGED_OUT, LOGGED_OUT, LOGGED_OUT]);
  assert.equal(tab.state.refreshCalls, 1);
});

test('свежий токен тоже не принят — сессию отозвали, выход', async () => {
  const tab = makeTab({ refresh: 'revoked' });
  assert.equal(await tab.fetch(`${API}/api/notifications`, tab.bearer()), LOGGED_OUT);

  const retried = tab.axios401('/api/notifications', 'A2', { __isRetryRequest: true });
  assert.equal(await tab.onAxiosError(retried), LOGGED_OUT);
});

test('вход, само обновление и 401 не про токен не переигрываются', async () => {
  const tab = makeTab();

  assert.equal((await tab.fetch(`${API}/api/login`, tab.bearer())).status, 401);
  await assert.rejects(tab.onAxiosError(tab.axios401('/api/auth/refresh')));
  await assert.rejects(tab.onAxiosError({
    config: { url: `${API}/api/notifications`, headers: {} },
    response: { status: 401, data: { error: 'Unauthorized' } },
  }));
  await assert.rejects(tab.onAxiosError({
    config: { url: `${API}/api/notifications`, headers: {} },
    response: { status: 403, data: EXPIRED_BODY },
  }));

  assert.equal(tab.state.refreshCalls, 0);
  assert.equal(tab.state.logouts, 0);
});

test('окончательный отказ axios проходит через redact, повторяемый — нет', async () => {
  const redacted = [];
  const handler = createAxiosAuthErrorHandler({
    replay: async () => ({ status: 200 }),
    refreshAuthSession: async () => ({ outcome: AUTH_REFRESH_OUTCOME.UNAVAILABLE }),
    getCurrentAccessToken: () => 'A1',
    onSessionRejected: () => LOGGED_OUT,
    redact: (error) => {
      redacted.push(error.config.url);
      error.config.data = '<скрыто>';
      return error;
    },
  });
  const error = {
    config: { url: `${API}/api/login`, data: '{"password":"secret"}', headers: {} },
    response: { status: 401, data: { error: 'Invalid credentials' } },
  };
  await assert.rejects(handler(error));
  assert.deepEqual(redacted, [`${API}/api/login`]);
  assert.equal(error.config.data, '<скрыто>');
});

test('итог обновления по ответу сервера', () => {
  const cases = [
    [{ status: 200 }, AUTH_REFRESH_OUTCOME.REFRESHED],
    [{ status: 204 }, AUTH_REFRESH_OUTCOME.REFRESHED],
    [{ status: 200, sessionAccepted: false }, AUTH_REFRESH_OUTCOME.REJECTED],
    [{ status: 401 }, AUTH_REFRESH_OUTCOME.REJECTED],
    [{ status: 403 }, AUTH_REFRESH_OUTCOME.UNAVAILABLE],
    [{ status: 429 }, AUTH_REFRESH_OUTCOME.UNAVAILABLE],
    [{ status: 500 }, AUTH_REFRESH_OUTCOME.UNAVAILABLE],
    [{ status: 502 }, AUTH_REFRESH_OUTCOME.UNAVAILABLE],
    [{ status: 0 }, AUTH_REFRESH_OUTCOME.UNAVAILABLE],
    [null, AUTH_REFRESH_OUTCOME.UNAVAILABLE],
  ];
  for (const [result, expected] of cases) {
    assert.equal(classifyAuthRefreshResult(result), expected, JSON.stringify(result));
  }
});

test('общее обновление: одно на всех, пока в полёте, и новое после завершения', async () => {
  let calls = 0;
  const refresh = createSharedAuthRefresh(async () => {
    calls += 1;
    await sleep(20);
    return { status: 200 };
  });

  const [a, b] = await Promise.all([refresh(), refresh()]);
  assert.equal(calls, 1);
  assert.equal(a, b);
  assert.deepEqual(a, { outcome: AUTH_REFRESH_OUTCOME.REFRESHED, status: 200 });

  await refresh();
  assert.equal(calls, 2);
});

test('две вкладки обновляются под одним замком — вторая идёт уже с токеном первой', async () => {
  // Web Locks в Node нет: замок изображает очередь на одно имя, как navigator.locks.
  const queues = new Map();
  const request = (name, task) => {
    const prev = queues.get(name) || Promise.resolve();
    const run = prev.then(() => task());
    queues.set(name, run.catch(() => {}));
    return run;
  };
  // В Node `navigator` — глобальный геттер, присваиванием его не подменить.
  const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', { value: { locks: { request } }, configurable: true });
  try {
    // Общее хранилище (localStorage) и сервер: принимает текущий токен и одно предыдущее поколение.
    const storage = { refresh: 'R1' };
    const server = { current: 'R1', previous: null, rotations: 0 };
    const rotate = (sent) => {
      if (sent !== server.current && sent !== server.previous) return { status: 401 };
      server.rotations += 1;
      server.previous = server.current;
      server.current = `R${server.rotations + 1}`;
      storage.refresh = server.current;
      return { status: 200 };
    };
    const makeTab = () => {
      const tab = { refresh: 'R1' };
      tab.refreshAuthSession = createSharedAuthRefresh(async () => {
        if (storage.refresh !== tab.refresh) tab.refresh = storage.refresh; // подхват под замком
        await sleep(20);
        const result = rotate(tab.refresh);
        if (result.status === 200) tab.refresh = server.current;
        return result;
      }, { lockName: 'otp-auth-refresh' });
      return tab;
    };
    const a = makeTab();
    const b = makeTab();

    const [ra, rb] = await Promise.all([a.refreshAuthSession(), b.refreshAuthSession()]);
    assert.equal(ra.outcome, AUTH_REFRESH_OUTCOME.REFRESHED);
    assert.equal(rb.outcome, AUTH_REFRESH_OUTCOME.REFRESHED);
    assert.equal(server.rotations, 2, 'вторая вкладка обновилась токеном первой, а не тем же R1');
    // Обе вкладки на актуальном поколении: 90 секунд запаса не понадобятся.
    assert.equal(a.refresh, 'R2');
    assert.equal(b.refresh, 'R3');
    assert.equal(storage.refresh, 'R3');
  } finally {
    if (originalNavigator) Object.defineProperty(globalThis, 'navigator', originalNavigator);
    else delete globalThis.navigator;
  }
});

test('без Web Locks обновление работает как раньше', async () => {
  assert.equal(typeof globalThis.navigator?.locks, 'undefined');
  const refresh = createSharedAuthRefresh(async () => ({ status: 200 }), { lockName: 'otp-auth-refresh' });
  assert.deepEqual(await refresh(), { outcome: AUTH_REFRESH_OUTCOME.REFRESHED, status: 200 });
});

test('токены из другой вкладки подхватываются событием storage, выход там — тоже', async () => {
  const listeners = new Map();
  globalThis.window = {
    addEventListener: (type, fn) => listeners.set(type, fn),
    removeEventListener: (type) => listeners.delete(type),
  };
  try {
    const applied = [];
    const stop = watchAuthTokensFromOtherTabs(['otp_access_token', 'otp_refresh_token'], (key, value) => {
      applied.push([key, value]);
    });
    const fire = (key, newValue) => listeners.get('storage')({ key, newValue });

    fire('otp_access_token', 'A2');
    fire('otp_refresh_token', ' R2 ');
    fire('user', '{"id":1}');          // чужой ключ — мимо
    fire('otp_access_token', null);    // выход в другой вкладке
    assert.deepEqual(applied, [
      ['otp_access_token', 'A2'],
      ['otp_refresh_token', 'R2'],
      ['otp_access_token', ''],
    ]);

    stop();
    assert.equal(listeners.has('storage'), false);
  } finally {
    delete globalThis.window;
  }
});

test('общее обновление не отклоняется, даже если процедура бросила синхронно', async () => {
  const refresh = createSharedAuthRefresh(() => {
    throw new Error('boom');
  });
  assert.deepEqual(await refresh(), { outcome: AUTH_REFRESH_OUTCOME.UNAVAILABLE, status: 0 });
});

test('какие 401 лечатся обновлением', () => {
  for (const code of ['TOKEN_EXPIRED', 'INVALID_TOKEN', 'MISSING_TOKEN', 'REFRESH_TOKEN_MISMATCH', 'SESSION_REVOKED']) {
    assert.equal(isRecoverableAuthBody({ code }), true, code);
  }
  assert.equal(isRecoverableAuthBody({ error: 'JWT authentication failed' }), true);
  assert.equal(isRecoverableAuthBody({ error: 'Unauthorized' }), false);
  assert.equal(isRecoverableAuthBody(null), false);
});

test('токен читается из любых заголовков fetch и axios', () => {
  assert.equal(readBearerToken({ Authorization: 'Bearer abc.def' }), 'abc.def');
  assert.equal(readBearerToken({ authorization: 'bearer xyz' }), 'xyz');
  assert.equal(readBearerToken(new Headers({ Authorization: 'Bearer from-headers' })), 'from-headers');
  const axiosLike = { get: (name) => (name === 'Authorization' ? 'Bearer from-axios' : undefined) };
  assert.equal(readBearerToken(axiosLike), 'from-axios');
  assert.equal(readBearerToken({ Authorization: 'Basic dXNlcg==' }), '');
  assert.equal(readBearerToken({}), '');
  assert.equal(readBearerToken(undefined), '');
});
