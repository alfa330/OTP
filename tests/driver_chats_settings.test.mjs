import test from 'node:test';
import assert from 'node:assert/strict';

import {
  byName,
  grantNote,
  groupPeople,
  isExhausted,
  matchesName,
  mergeRefresh,
  reachedIds,
  recallTabs,
  rememberTabs,
  sectionTabs,
  stepExtra,
  todayLimit,
} from '../src/components/driver_chats/settingsMeta.js';
import { createExtraSaver } from '../src/components/driver_chats/extraSaver.js';

/* Вкладка «Настройки» раздела «Чаты водителей»: поиски сверх лимита на сегодня.
 *
 * Владелец 27.10.2026: «св и выше добавь в настройках возможность добавлять
 * дополнительные попытки операторам до 5 попыток», на уточнение — только на
 * сегодня. Границу держит сервер (tests/test_driver_chats.py), здесь — правила
 * экрана: шаг степпера, группы, поиск по имени и подпись «кто добавил». */

const person = (overrides = {}) => ({
  user_id: 1, name: 'Абенова Айгерим', role: 'operator',
  used_today: 0, extra_today: 0, granted_by_name: null, granted_at: null,
  ...overrides,
});

test('степпер шагает в пределах от нуля до потолка', () => {
  assert.equal(stepExtra(0, 1, 5), 1);
  assert.equal(stepExtra(4, 1, 5), 5);
  assert.equal(stepExtra(5, 1, 5), 5, 'выше потолка не шагает');
  assert.equal(stepExtra(0, -1, 5), 0, 'ниже нуля не шагает');
  assert.equal(stepExtra(3, -1, 5), 2);
});

test('степпер не верит мусору ни в значении, ни в потолке', () => {
  assert.equal(stepExtra(undefined, 1, 5), 1);
  assert.equal(stepExtra(null, -1, 5), 0);
  assert.equal(stepExtra(2, 1, undefined), 0, 'без потолка добавлять нельзя');
});

test('потолок сегодня — лимит плюс добавленное', () => {
  assert.equal(todayLimit(person(), 40), 40);
  assert.equal(todayLimit(person({ extra_today: 3 }), 40), 43);
});

test('«искать нечем» — когда использован весь сегодняшний потолок', () => {
  assert.equal(isExhausted(person({ used_today: 39 }), 40), false);
  assert.equal(isExhausted(person({ used_today: 40 }), 40), true);
  assert.equal(isExhausted(person({ used_today: 40, extra_today: 2 }), 40), false,
    'добавили — снова есть чем искать');
  assert.equal(isExhausted(person({ used_today: 42, extra_today: 2 }), 40), true);
});

test('группа сверху — те, кто упёрся в ОСНОВНОЙ лимит, и добавка их оттуда не уводит', () => {
  const people = [
    person({ user_id: 1, used_today: 40 }),
    person({ user_id: 2, used_today: 12 }),
    person({ user_id: 3, used_today: 41, extra_today: 3 }),
  ];
  const reached = reachedIds(people, 40);
  assert.deepEqual([...reached].sort(), [1, 3]);

  // СВ добавил первому три поиска — строка остаётся в своей группе.
  const after = people.map((p) => (p.user_id === 1 ? { ...p, extra_today: 3 } : p));
  const groups = groupPeople(after, reached, '');
  assert.deepEqual(groups.map((g) => g.key), ['reached', 'rest']);
  assert.deepEqual(groups[0].people.map((p) => p.user_id), [1, 3]);
  assert.deepEqual(groups[1].people.map((p) => p.user_id), [2]);
});

test('пустая группа не рисуется, а одинокая называется «Сотрудники»', () => {
  const people = [person({ user_id: 1 }), person({ user_id: 2 })];
  const groups = groupPeople(people, reachedIds(people, 40), '');
  assert.equal(groups.length, 1);
  assert.equal(groups[0].title, 'Сотрудники');

  const withTop = [person({ user_id: 1, used_today: 40 }), person({ user_id: 2 })];
  const titles = groupPeople(withTop, reachedIds(withTop, 40), '').map((g) => g.title);
  assert.deepEqual(titles, ['Дошли до лимита сегодня', 'Остальные']);
});

test('поиск по имени: регистр, «ё» и порядок слов не важны', () => {
  const p = person({ name: 'Абенова Айгерим Ерлановна' });
  assert.equal(matchesName(p, ''), true);
  assert.equal(matchesName(p, '  '), true);
  assert.equal(matchesName(p, 'айгерим абен'), true);
  assert.equal(matchesName(p, 'ЕРЛАН'), true);
  assert.equal(matchesName(person({ name: 'Королёва Анна' }), 'королева'), true);
  assert.equal(matchesName(p, 'айгерим динара'), false, 'каждое слово обязано найтись');
});

test('группы фильтруются поиском, пустые после фильтра пропадают', () => {
  const people = [
    person({ user_id: 1, name: 'Абенова Айгерим', used_today: 40 }),
    person({ user_id: 2, name: 'Бекова Динара' }),
  ];
  const groups = groupPeople(people, reachedIds(people, 40), 'динара');
  assert.equal(groups.length, 1);
  assert.equal(groups[0].title, 'Сотрудники', 'группа сверху пуста — вторая снова одна');
  assert.deepEqual(groups[0].people.map((p) => p.user_id), [2]);
  assert.deepEqual(groupPeople(people, new Set(), 'никто'), []);
});

test('порядок — по-русски, а не по локали базы', () => {
  // В «C» базы «Ё» встала бы после «Я», а строчная — после всех заглавных.
  // По-русски «ё» идёт как «е»: «Ёлкина» (е-л) раньше «Ербол» (е-р).
  const names = byName([
    person({ name: 'Ёлкина Мария' }),
    person({ name: 'Яковлева Анна' }),
    person({ name: 'Ербол Нур' }),
    person({ name: 'абдуллаева Айгуль' }),
  ]).map((p) => p.name);
  assert.deepEqual(names, ['абдуллаева Айгуль', 'Ёлкина Мария', 'Ербол Нур', 'Яковлева Анна']);
});

test('«кто добавил» показывается, только пока добавка есть', () => {
  const fmt = () => '14:05';
  assert.equal(grantNote(person({ extra_today: 2, granted_by_name: 'Иванова Анна',
    granted_at: '2026-10-27T14:05:00' }), fmt), 'Добавлено в 14:05 · Иванова Анна');
  assert.equal(grantNote(person({ extra_today: 2, granted_by_name: 'Иванова Анна' }), fmt),
    'Добавлено · Иванова Анна');
  assert.equal(grantNote(person({ extra_today: 0, granted_by_name: 'Иванова Анна',
    granted_at: '2026-10-27T14:05:00' }), fmt), '', 'сняли добавку — подпись пропала');
  assert.equal(grantNote(person({ extra_today: 1 }), fmt), '');
});

/* ── Фоновое обновление открытого списка ─────────────────────────────────── */

test('обновление открытого списка: числа свежие, порядок прежний', () => {
  const before = [person({ user_id: 2, name: 'Бекова Динара' }),
    person({ user_id: 1, name: 'Абенова Айгерим' }),
    person({ user_id: 3, name: 'Ербол Нур' })];
  const fresh = [person({ user_id: 1, name: 'Абенова Айгерим', used_today: 40 }),
    person({ user_id: 3, name: 'Ербол Нур', used_today: 7 }),
    person({ user_id: 5, name: 'Яковлева Анна' }),
    person({ user_id: 4, name: 'Ганиева Асель' })];
  const merged = mergeRefresh(before, fresh);
  assert.deepEqual(merged.map((p) => p.user_id), [1, 3, 4, 5],
    'ушедший пропал, прежние на своих местах, новые — в конце по имени');
  assert.equal(merged[0].used_today, 40, 'числа — из свежего ответа');
});

/* ── Полоса вкладок раздела ──────────────────────────────────────────────── */

const memoryStorage = () => {
  const data = new Map();
  return {
    data,
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => { data.set(key, String(value)); },
  };
};

test('вкладки — по правам сервера, а до его ответа — по памяти прошлого входа', () => {
  const values = (tabs) => tabs.map((t) => t.value);
  assert.deepEqual(values(sectionTabs(null, null)), ['search']);
  assert.deepEqual(values(sectionTabs(null, { journal: true, settings: true })),
    ['search', 'journal', 'settings']);
  assert.deepEqual(values(sectionTabs({ can_view_journal: true, can_grant_extra_searches: false },
    { journal: true, settings: true })), ['search', 'journal'], 'ответ сервера главнее памяти');
  assert.deepEqual(values(sectionTabs({ can_view_journal: false, can_grant_extra_searches: true },
    { journal: true, settings: false })), ['search', 'settings'], 'в обе стороны и по каждой вкладке');
  assert.deepEqual(sectionTabs({ can_grant_extra_searches: true }, null).map((t) => t.label),
    ['Поиск чатов', 'Настройки']);
});

test('память вкладок — своя у каждой учётки и переживает перезагрузку', () => {
  const storage = memoryStorage();
  rememberTabs(2, { can_view_journal: true, can_grant_extra_searches: true }, storage);
  rememberTabs(4, { can_view_journal: false, can_grant_extra_searches: false }, storage);
  rememberTabs(5, { can_view_journal: true, can_grant_extra_searches: false }, storage);
  assert.deepEqual(recallTabs(2, storage), { journal: true, settings: true });
  assert.deepEqual(recallTabs(4, storage), { journal: false, settings: false });
  assert.deepEqual(recallTabs(5, storage), { journal: true, settings: false },
    'каждая вкладка помнится по своему праву');
  assert.equal(recallTabs(99, storage), null, 'чужой памяти нет');
  assert.equal(recallTabs(null, storage), null, 'без учётки не вспоминаем');
});

test('запрещённое хранилище не ломает раздел', () => {
  const broken = {
    getItem: () => { throw new Error('SecurityError'); },
    setItem: () => { throw new Error('QuotaExceededError'); },
  };
  assert.equal(recallTabs(2, broken), null);
  assert.doesNotThrow(() => rememberTabs(2, { can_view_journal: true }, broken));
  const garbage = memoryStorage();
  garbage.setItem('otp.driverChats.tabs.2', '{не json');
  assert.equal(recallTabs(2, garbage), null, 'испорченная запись — как пустая');
});

/* ── Очередь сохранения степпера ─────────────────────────────────────────── */

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
};

const fakeServer = () => {
  const calls = [];
  const send = (userId, extra, expected) => {
    const call = { userId, extra, expected, ...deferred() };
    calls.push(call);
    return call.promise;
  };
  return { calls, send };
};

const settle = () => new Promise((resolve) => setImmediate(resolve));

const row = (extra, at, name = 'Супервайзер') => ({
  status: 200,
  payload: { extra_today: extra, granted_by_name: name, granted_at: at, used_today: 40 },
});

const setup = (seed = [{ user_id: 4, extra_today: 0, used_today: 40 }]) => {
  const saver = createExtraSaver();
  const server = fakeServer();
  const events = [];
  saver.configure({ send: server.send, listener: (event) => events.push(event) });
  saver.seed(seed);
  return { saver, server, events };
};

test('«+ + +» подряд — два запроса: один летит, второй уносит набранный итог', async () => {
  const { saver, server, events } = setup();
  saver.set(4, 1);
  saver.set(4, 2);
  saver.set(4, 3);
  assert.equal(server.calls.length, 1, 'по человеку летит один запрос');
  assert.deepEqual([server.calls[0].extra, server.calls[0].expected], [1, 0]);
  assert.equal(saver.view({ user_id: 4, extra_today: 0 }).extra_today, 3, 'на экране — набранное');

  server.calls[0].resolve(row(1, '2026-10-27T10:00:00'));
  await settle();
  assert.equal(server.calls.length, 2);
  assert.deepEqual([server.calls[1].extra, server.calls[1].expected], [3, 1],
    'итог, а не «+1», и ожидание — то, что подтвердил сервер');

  server.calls[1].resolve(row(3, '2026-10-27T10:00:01'));
  await settle();
  assert.equal(server.calls.length, 2, 'третьего запроса нет');
  assert.deepEqual(events.map((e) => e.type), ['saved']);
  assert.equal(saver.view({ user_id: 4, extra_today: 0 }).extra_today, 3);
  assert.equal(saver.pending(4), undefined);
});

test('отказ сервера — откат к подтверждённому и текст сервера', async () => {
  const { saver, server, events } = setup([{ user_id: 4, extra_today: 2, used_today: 40 }]);
  saver.set(4, 3);
  server.calls[0].resolve({ status: 403, payload: { error: 'Добавить поиски можно только операторам' } });
  await settle();
  assert.equal(events[0].type, 'failed');
  assert.equal(events[0].message, 'Добавить поиски можно только операторам');
  assert.equal(saver.view({ user_id: 4, extra_today: 2 }).extra_today, 2, 'на экране снова +2');
  assert.equal(saver.pending(4), undefined, 'набранное не висит до следующего нажатия');
});

test('нет сети — тоже откат, а текст подставит экран', async () => {
  const { saver, server, events } = setup();
  saver.set(4, 1);
  server.calls[0].reject(new TypeError('Failed to fetch'));
  await settle();
  assert.deepEqual([events[0].type, events[0].message], ['failed', '']);
  assert.equal(saver.view({ user_id: 4, extra_today: 0 }).extra_today, 0);
});

test('список устарел — сервер вернул настоящее число, экран его и показывает', async () => {
  const { saver, server, events } = setup();
  saver.set(4, 1);
  server.calls[0].resolve({ status: 409, payload: {
    error: 'Пока список был открыт, добавку уже поменяли: сейчас +3 (Другой).',
    extra_today: 3, granted_by_name: 'Другой', granted_at: '2026-10-27T10:00:00', used_today: 41 } });
  await settle();
  assert.equal(events[0].type, 'conflict');
  assert.match(events[0].message, /сейчас \+3/);
  const view = saver.view({ user_id: 4, extra_today: 0, used_today: 40 });
  assert.equal(view.extra_today, 3);
  assert.equal(view.used_today, 41);

  saver.set(4, 4);
  assert.equal(server.calls[1].expected, 3, 'следующее нажатие ждёт уже настоящее число');
});

test('свежий список не затирает ни летящее нажатие, ни ответ новее себя', async () => {
  const { saver, server } = setup();
  saver.set(4, 2);
  saver.seed([{ user_id: 4, extra_today: 0, used_today: 40 }]);
  assert.equal(saver.view({ user_id: 4, extra_today: 0 }).extra_today, 2, 'нажатие летит — не трогаем');

  server.calls[0].resolve(row(2, '2026-10-27T10:00:05'));
  await settle();
  // GET прочитал базу ДО этого PUT и пришёл позже него.
  saver.seed([{ user_id: 4, extra_today: 0, used_today: 41, granted_at: null }]);
  const view = saver.view({ user_id: 4, extra_today: 0 });
  assert.equal(view.extra_today, 2, 'старый список не откатывает сохранённое');
  assert.equal(view.used_today, 41, 'а расход берём свежий');

  saver.seed([{ user_id: 4, extra_today: 5, used_today: 41, granted_at: '2026-10-27T10:00:09' }]);
  assert.equal(saver.view({ user_id: 4, extra_today: 0 }).extra_today, 5, 'новее — принимаем');
});

test('ушёл с вкладки посреди сохранения — новые нажатия встают в ту же очередь', async () => {
  const { saver, server } = setup();
  saver.set(4, 1);
  saver.configure({ listener: null });            // вкладку закрыли
  const events = [];
  saver.configure({ listener: (e) => events.push(e) });   // и открыли снова
  saver.set(4, 2);
  assert.equal(server.calls.length, 1, 'второй запрос не обгоняет первый');
  server.calls[0].resolve(row(1, '2026-10-27T10:00:00'));
  await settle();
  assert.deepEqual([server.calls[1].extra, server.calls[1].expected], [2, 1]);
  server.calls[1].resolve(row(2, '2026-10-27T10:00:01'));
  await settle();
  assert.deepEqual(events.map((e) => e.type), ['saved'], 'новый экран узнал об итоге');
});

test('серия нажатий — один запрос с итогом: в журнал ложится одна строка, а не шесть', async () => {
  const saver = createExtraSaver({ settleMs: 20 });
  const server = fakeServer();
  saver.configure({ send: server.send, listener: () => {} });
  saver.seed([{ user_id: 4, extra_today: 0, used_today: 40 }]);
  saver.set(4, 1);
  saver.set(4, 2);
  saver.set(4, 3);
  assert.equal(server.calls.length, 0, 'пока человек жмёт — ждём');
  assert.equal(saver.view({ user_id: 4, extra_today: 0 }).extra_today, 3, 'а экран уже показывает итог');
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(server.calls.length, 1);
  assert.deepEqual([server.calls[0].extra, server.calls[0].expected], [3, 0]);
});
