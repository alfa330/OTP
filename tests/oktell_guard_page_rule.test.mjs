// Тесты правила «Перезвон дольше нормы», которое живёт внутри окна Oktell.
//
// Правило написано на JS и исполняется в странице клиента, поэтому и проверяем
// его на JS: поднимаем поддельные window/document/localStorage/WebSocket,
// подсовываем кадры статуса и виртуальные часы. Так ловятся ровно те дыры,
// ради которых всё затевалось: обход переключением статуса и обход через F5.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const AGENT_PY = join(here, '..', 'oktell_recall_guard', 'agent.py');

function hookSource(overrides = {}) {
  // Единственный источник правды — agent.py: дублировать JS в тесте нельзя,
  // иначе тест начнёт проверять свою копию, а не то, что уедет на машины.
  const py = readFileSync(AGENT_PY, 'utf8');
  const start = py.indexOf('HOOK_JS_TEMPLATE = r"""');
  assert.ok(start >= 0, 'в agent.py не найден HOOK_JS_TEMPLATE');
  const from = py.indexOf('"""', start + 'HOOK_JS_TEMPLATE = r'.length) + 3;
  const end = py.indexOf('"""', from);
  const template = py.slice(from, end);

  const params = {
    enabled: true,
    thresholdS: 180,
    warnBeforeS: 30,
    recallReasonId: 2,
    message: 'тест',
    sessionKeys: ['___oktellsessionid'],
    callStateStrings: ['talk', 'dial', 'call', 'ring'],
    callStateIds: [],
    ...overrides,
  };
  return template.replace('__RULE_PARAMS__', JSON.stringify(params));
}

// --- поддельное окружение страницы ------------------------------------------

function makeEnv(storage = new Map()) {
  let now = 1_700_000_000_000;
  const intervals = [];
  const timeouts = [];

  const element = () => ({
    id: '',
    style: '',
    textContent: '',
    parentNode: null,
    children: [],
    setAttribute() {},
    appendChild(child) { this.children.push(child); child.parentNode = this; },
    removeChild(child) { this.children = this.children.filter((c) => c !== child); child.parentNode = null; },
  });

  const body = element();
  const doc = {
    body,
    documentElement: body,
    cookie: '',
    createElement: () => element(),
    getElementById: (id) => body.children.find((c) => c.id === id) || null,
    querySelector: () => null,
  };

  class FakeWebSocket {
    constructor() {
      this.readyState = 1;
      this.sent = [];
      this.listeners = {};
    }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    send(data) { this.sent.push(data); }
    emit(data) { (this.listeners.message || []).forEach((fn) => fn({ data })); }
  }

  const env = {
    reloads: 0,
    storage,
    get now() { return now; },
    window: {
      WebSocket: FakeWebSocket,
      localStorage: {
        getItem: (k) => (storage.has(k) ? storage.get(k) : null),
        setItem: (k, v) => storage.set(k, String(v)),
        removeItem: (k) => storage.delete(k),
        clear: () => storage.clear(),
      },
      sessionStorage: { clear() {}, removeItem() {} },
      document: doc,
      location: { hostname: 'oktell.example.local', reload: () => { env.reloads += 1; } },
    },
    advanceSeconds(seconds) {
      for (let i = 0; i < seconds; i += 1) {
        now += 1000;
        intervals.forEach((entry) => entry.fn());
      }
      timeouts.splice(0).forEach((fn) => fn());
    },
    intervals,
    timeouts,
  };

  env.window.window = env.window;
  // Правило зовёт и Date.now(), и new Date(): подменять надо конструктором,
  // иначе запись в localStorage молча уходит в catch и тест ловит пустоту.
  class VirtualDate extends Date {
    constructor(...args) {
      if (args.length === 0) { super(now); } else { super(...args); }
    }
    static now() { return now; }
  }
  env.window.Date = VirtualDate;
  env.window.setInterval = (fn) => { intervals.push({ fn }); return intervals.length; };
  env.window.clearInterval = (id) => { intervals.splice(id - 1, 1); };
  env.window.setTimeout = (fn) => { timeouts.push(fn); return timeouts.length; };
  return env;
}

function runHook(env, overrides) {
  const src = hookSource(overrides);
  const fn = new Function(
    'window', 'document', 'localStorage', 'sessionStorage', 'location',
    'setInterval', 'clearInterval', 'setTimeout', 'Date', 'JSON',
    src,
  );
  fn(
    env.window, env.window.document, env.window.localStorage, env.window.sessionStorage,
    env.window.location, env.window.setInterval, env.window.clearInterval,
    env.window.setTimeout, env.window.Date, JSON,
  );
  const ws = new env.window.WebSocket('ws://oktell.example.local/');
  return { ws, rule: env.window.__oktellGuardRule };
}

const recall = { userlogin: '6612', onlunch: true, lunchreasonid: 2, userstate: 2, userstatestr: 'usLunch' };
const ready = { userlogin: '6612', onlunch: false, lunchreasonid: null, userstate: 1, userstatestr: 'usReady' };
const talking = { userlogin: '6612', onlunch: false, lunchreasonid: null, userstate: 4, userstatestr: 'usTalk' };

const frame = (payload) => JSON.stringify(['getuserstateresult', payload]);

// --- сами проверки ------------------------------------------------------------

test('подряд в «Перезвоне» — срабатывает на пороге', () => {
  const env = makeEnv();
  const { ws, rule } = runHook(env);
  ws.emit(frame(recall));
  env.advanceSeconds(179);
  assert.equal(rule.fired, false, 'до порога срабатывать не должно');
  env.advanceSeconds(2);
  assert.equal(rule.fired, true);
  assert.equal(env.reloads, 1, 'страница должна перезагрузиться');
  assert.ok(ws.sent.some((m) => m.includes('logout')), 'в сокет должен уйти штатный logout');
});

test('переключение статуса туда-обратно НЕ обнуляет отсчёт', () => {
  const env = makeEnv();
  const { ws, rule } = runHook(env);
  ws.emit(frame(recall));
  env.advanceSeconds(170);
  ws.emit(frame(ready));      // мигнул на «Готов»
  env.advanceSeconds(2);
  ws.emit(frame(recall));     // и сразу обратно
  env.advanceSeconds(12);
  assert.equal(rule.fired, true, 'накопленное должно сохраниться, иначе обход в один клик');
});

test('звонок обнуляет накопленное', () => {
  const env = makeEnv();
  const { ws, rule } = runHook(env);
  ws.emit(frame(recall));
  env.advanceSeconds(170);
  ws.emit(frame(talking));    // состоялся разговор
  env.advanceSeconds(5);
  ws.emit(frame(recall));
  env.advanceSeconds(100);
  assert.equal(rule.fired, false, 'после звонка отсчёт должен начаться заново');
  assert.equal(rule.budget, 0);
});

test('плашка появляется за warnBefore до порога и только один раз', () => {
  const env = makeEnv();
  const { ws, rule } = runHook(env);
  ws.emit(frame(recall));
  env.advanceSeconds(149);
  assert.equal(rule.warned, false);
  env.advanceSeconds(2);
  assert.equal(rule.warned, true);
  const banners = env.window.document.body.children.filter((c) => c.id === '__oktell_guard_banner');
  assert.equal(banners.length, 1);
});

test('перезагрузка страницы не обнуляет накопленное', () => {
  const storage = new Map();
  const first = makeEnv(storage);
  const a = runHook(first);
  a.ws.emit(frame(recall));
  first.advanceSeconds(150);
  a.ws.emit(frame(ready));            // ушёл из «Перезвона» — накопленное сохранилось
  assert.ok(storage.has('__oktell_guard_budget'), 'счётчик обязан лечь в localStorage');

  const second = makeEnv(storage);    // как будто нажали F5
  const b = runHook(second);
  b.ws.emit(frame(recall));
  second.advanceSeconds(35);
  assert.equal(b.rule.fired, true, 'после F5 отсчёт должен продолжиться, а не начаться заново');
});

test('запись о нарушении переживает разлогин', () => {
  const env = makeEnv();
  const { ws } = runHook(env);
  ws.emit(frame(recall));
  env.advanceSeconds(181);
  const raw = env.storage.get('__oktell_guard_violations');
  assert.ok(raw, 'нарушение должно сохраниться');
  const list = JSON.parse(raw);
  assert.equal(list[0].login, '6612');
  assert.equal(list[0].reason, 'recall_timeout');
  assert.ok(list[0].seconds >= 180);
});

test('другой перерыв (не «Перезвон») не считается', () => {
  const env = makeEnv();
  const { ws, rule } = runHook(env);
  ws.emit(frame({ ...recall, lunchreasonid: 4 }));   // обычный перерыв
  env.advanceSeconds(200);
  assert.equal(rule.fired, false);
});

test('выключенное правило ничего не делает', () => {
  const env = makeEnv();
  const { ws } = runHook(env, { enabled: false });
  ws.emit(frame(recall));
  env.advanceSeconds(300);
  assert.equal(env.reloads, 0);
});
