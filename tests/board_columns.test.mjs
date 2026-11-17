import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BOARD_COLUMNS,
  BOARD_COLUMN_STATE,
  columnOfTask,
  hasFiredAssignee,
  mergeBoardRows,
  overrideFromServer,
  resolveBoardDrop,
  sendBoardMove,
  transientColumnsKnown,
  visibleBoardColumns,
} from '../src/components/tasks/boardColumns.js';
import { BOARD_COLUMN_QUERY } from '../src/components/tasks/boardQuery.js';

const ids = (columns) => columns.map((column) => column.id);

const ASSIGNEE = 10;
const BOSS = 20;
const STRANGER = 30;

// Задачу поручил BOSS, делает ASSIGNEE.
const taskIn = (status, extra = {}) => ({
  id: 401,
  status,
  is_backlog: false,
  creator: { id: BOSS },
  assignees: [{ id: ASSIGNEE }],
  ...extra,
});
const as = (userId, extra = {}) => ({ currentUserId: userId, isAdmin: false, isSupervisor: false, ...extra });

test('колонки идут в порядке владельца, «Готово» — последней', () => {
  assert.deepEqual(
    BOARD_COLUMNS.map((column) => column.title),
    ['Новая', 'Бэклог', 'В работе', 'Возвращено на доработку', 'На проверке', 'Готово'],
  );
});

test('запросы колонок идут в том же порядке, что и колонки', () => {
  assert.deepEqual(Object.keys(BOARD_COLUMN_QUERY), ids(BOARD_COLUMNS));
});

test('задача ложится в колонку по статусу, возвращённая — во временную', () => {
  assert.equal(columnOfTask({ status: 'assigned' }), 'todo');
  assert.equal(columnOfTask({ status: 'assigned', is_backlog: true }), 'backlog');
  assert.equal(columnOfTask({ status: 'in_progress' }), 'progress');
  assert.equal(columnOfTask({ status: 'returned' }), 'returned');
  assert.equal(columnOfTask({ status: 'completed' }), 'review');
  assert.equal(columnOfTask({ status: 'accepted' }), 'done');
});

test('сервер отдаёт задачу ровно той колонке, в которую её кладёт доска', () => {
  // Разъедься срез и раскладка — карточка пропала бы с доски или встала в двух местах.
  const selects = (query, task) => {
    if (query.backlog === 'only' && !task.is_backlog) return false;
    if (query.backlog === 'exclude' && task.is_backlog) return false;
    return !query.status || query.status.split(',').includes(task.status);
  };
  [
    { status: 'assigned', is_backlog: false },
    { status: 'assigned', is_backlog: true },
    { status: 'in_progress', is_backlog: false },
    { status: 'returned', is_backlog: false },
    { status: 'completed', is_backlog: false },
    { status: 'accepted', is_backlog: false },
  ].forEach((task) => {
    const holders = Object.entries(BOARD_COLUMN_QUERY)
      .filter(([, query]) => selects(query, task))
      .map(([columnId]) => columnId);
    assert.deepEqual(holders, [columnOfTask(task)], JSON.stringify(task));
  });
});

test('«Возвращено на доработку» стоит на доске, только пока в ней есть задачи', () => {
  assert.deepEqual(ids(visibleBoardColumns(() => 0)), ['todo', 'backlog', 'progress', 'review', 'done']);
  assert.deepEqual(
    ids(visibleBoardColumns((columnId) => (columnId === 'returned' ? 1 : 0))),
    ['todo', 'backlog', 'progress', 'returned', 'review', 'done'],
  );
  // Временная — только она: пустые постоянные колонки остаются на месте.
  assert.deepEqual(
    BOARD_COLUMNS.filter((column) => column.transient).map((column) => column.id),
    ['returned'],
  );
});

test('после полной загрузки доска ждёт ответа по временной колонке — без сдвига соседей', () => {
  const reset = Object.fromEntries(BOARD_COLUMNS.map((column) => [
    column.id, { tasks: [], total: 0, loading: true, fetched: false },
  ]));
  assert.equal(transientColumnsKnown(reset), false);
  // Ответ пришёл — пустой или нет, раскладка колонок уже известна.
  assert.equal(transientColumnsKnown({ ...reset, returned: { tasks: [], total: 0, loading: false, fetched: true } }), true);
  // Запрос сорвался — доску не держим вечным скелетоном.
  assert.equal(transientColumnsKnown({ ...reset, returned: { tasks: [], total: 0, loading: false, fetched: false } }), true);
  // Перезагрузка после действия: ответ уже был, скелетон не нужен.
  assert.equal(transientColumnsKnown({ ...reset, returned: { tasks: [], total: 1, loading: true, fetched: true } }), true);
  // Постоянные колонки раскладку не меняют и доску не держат.
  assert.equal(transientColumnsKnown({ ...reset, todo: { tasks: [], total: 3, loading: false, fetched: true } }), false);
});

test('поручитель возвращает сданное или принятое переносом во временную колонку', () => {
  assert.deepEqual(resolveBoardDrop(taskIn('completed'), 'returned', as(BOSS)), { type: 'status', action: 'returned' });
  assert.deepEqual(resolveBoardDrop(taskIn('accepted'), 'returned', as(BOSS)), { type: 'status', action: 'returned' });
});

test('пока временной колонки нет, сданное, брошенное в «В работе», тоже уходит на доработку', () => {
  assert.deepEqual(resolveBoardDrop(taskIn('completed'), 'progress', as(BOSS)), { type: 'status', action: 'returned' });
  // А принятое оттуда же — возобновляется, как и раньше.
  assert.deepEqual(resolveBoardDrop(taskIn('accepted'), 'progress', as(BOSS)), { type: 'status', action: 'reopened' });
});

test('вернуть на доработку нельзя ни своё, ни не сданное', () => {
  const own = resolveBoardDrop(taskIn('completed'), 'returned', as(ASSIGNEE));
  assert.equal(own.type, 'blocked');
  assert.match(own.reason, /кто поручил/);
  ['assigned', 'in_progress'].forEach((status) => {
    const decision = resolveBoardDrop(taskIn(status), 'returned', as(BOSS));
    assert.equal(decision.type, 'blocked', status);
    assert.match(decision.reason, /только сданную/);
  });
  assert.equal(resolveBoardDrop(taskIn('assigned', { is_backlog: true }), 'returned', as(BOSS)).type, 'blocked');
});

test('возвращённую исполнитель берёт в работу или сразу сдаёт', () => {
  assert.deepEqual(resolveBoardDrop(taskIn('returned'), 'progress', as(ASSIGNEE)), { type: 'status', action: 'in_progress' });
  assert.deepEqual(resolveBoardDrop(taskIn('returned'), 'review', as(ASSIGNEE)), { type: 'status', action: 'completed' });
  // Не исполнитель — ни то, ни другое.
  assert.equal(resolveBoardDrop(taskIn('returned'), 'progress', as(BOSS)).type, 'blocked');
  assert.equal(resolveBoardDrop(taskIn('returned'), 'review', as(STRANGER, { isAdmin: true })).type, 'blocked');
});

test('из временной колонки не уйти в «Новую», «Бэклог» и «Готово»', () => {
  ['todo', 'backlog', 'done'].forEach((column) => {
    assert.equal(resolveBoardDrop(taskIn('returned'), column, as(BOSS)).type, 'blocked', column);
    assert.equal(resolveBoardDrop(taskIn('returned'), column, as(ASSIGNEE)).type, 'blocked', column);
  });
  assert.deepEqual(resolveBoardDrop(taskIn('returned'), 'returned', as(ASSIGNEE)), { type: 'noop' });
});

test('«Новая» принимает задачу только из бэклога', () => {
  assert.deepEqual(
    resolveBoardDrop(taskIn('assigned', { is_backlog: true }), 'todo', as(ASSIGNEE)),
    { type: 'board', patch: { is_backlog: false } },
  );
  const started = resolveBoardDrop(taskIn('in_progress'), 'todo', as(BOSS, { isAdmin: true }));
  assert.equal(started.type, 'blocked');
  assert.match(started.reason, /возврат на доработку/);
  assert.deepEqual(
    resolveBoardDrop(taskIn('assigned'), 'backlog', as(BOSS)),
    { type: 'board', patch: { is_backlog: true } },
  );
});

test('на проверку — только из работы, и только исполнителем', () => {
  assert.deepEqual(resolveBoardDrop(taskIn('in_progress'), 'review', as(ASSIGNEE)), { type: 'status', action: 'completed' });
  assert.equal(resolveBoardDrop(taskIn('assigned'), 'review', as(ASSIGNEE)).type, 'blocked');
  assert.equal(resolveBoardDrop(taskIn('in_progress'), 'review', as(BOSS)).type, 'blocked');
});

test('задачу коллеги-СВ не двигают никуда, даже во временную колонку', () => {
  const observed = taskIn('completed', { viewer_is_observer: true });
  BOARD_COLUMNS.forEach((column) => {
    if (column.id === 'review') return;
    assert.equal(resolveBoardDrop(observed, column.id, as(BOSS)).type, 'blocked', column.id);
  });
});

/* Правка карточки после ответа сервера держится его значениями: задачу в ту же
   секунду могли взять в работу — балл записан, а в бэклог она не ушла. С догадкой
   фронта правка не сошлась бы со строкой доски и держала бы карточку в «Бэклоге». */
test('оптимистичная правка после ответа — значениями сервера', () => {
  const guess = { priority_score: 2, is_backlog: true };
  const server = { task_id: 7, priority_score: 2, is_backlog: false, status: 'in_progress', score_move: null };
  assert.deepEqual(overrideFromServer(guess, server), { priority_score: 2, is_backlog: false });
  // Карточка с такой правкой стоит там же, где задача на деле.
  assert.equal(columnOfTask({ status: 'in_progress', ...overrideFromServer(guess, server) }), 'progress');
  // Поля, которых сервер не вернул, остаются как были; лишних не добавляется.
  assert.deepEqual(overrideFromServer({ backlog_rank: 2.5 }, { task_id: 7 }), { backlog_rank: 2.5 });
  assert.deepEqual(overrideFromServer({ is_backlog: true }, { is_backlog: true, backlog_rank: 9 }), { is_backlog: true });
  // Нечего сверять — правка как есть.
  assert.deepEqual(overrideFromServer(guess, null), guess);
  assert.equal(overrideFromServer(null, server), null);
});

/* Карточки уволенных (владелец, 17.11.2026): двигать их по циклу некому — брать в
   работу и сдавать может только исполнитель, — поэтому суперадмин переносит их в
   любую колонку одним шагом, и карточка встаёт ровно туда, куда её бросили. */
const FIRED = 11;
const ACTIVE = 12;
const firedTaskIn = (status, extra = {}) => taskIn(status, {
  assignees: [{ id: FIRED, is_fired: true }],
  ...extra,
});
const superAdmin = (extra = {}) => as(STRANGER, { isAdmin: true, isSuperAdmin: true, ...extra });
const STARTS = [
  ['assigned', {}],
  ['assigned', { is_backlog: true }],
  ['in_progress', {}],
  ['returned', {}],
  ['completed', {}],
  ['accepted', {}],
];

test('состояние колонки ставит карточку ровно в неё', () => {
  assert.deepEqual(Object.keys(BOARD_COLUMN_STATE), ids(BOARD_COLUMNS));
  BOARD_COLUMNS.forEach((column) => {
    assert.equal(columnOfTask(BOARD_COLUMN_STATE[column.id]), column.id, column.id);
    // И это тот же срез, что сервер отдаёт колонке.
    const query = BOARD_COLUMN_QUERY[column.id];
    const state = BOARD_COLUMN_STATE[column.id];
    assert.equal(query.backlog === 'only', state.is_backlog, column.id);
    if (query.status) assert.equal(query.status, state.status, column.id);
  });
});

test('уволенный — хоть один из исполнителей; признак у исполнителя, а не у постановщика', () => {
  assert.equal(hasFiredAssignee(firedTaskIn('assigned')), true);
  assert.equal(hasFiredAssignee(taskIn('assigned', {
    assignees: [{ id: ACTIVE }, { id: FIRED, is_fired: true }],
  })), true);
  assert.equal(hasFiredAssignee(taskIn('assigned')), false);
  assert.equal(hasFiredAssignee(taskIn('assigned', { creator: { id: BOSS, is_fired: true } })), false);
  // Ответ без состава — по единственному исполнителю.
  assert.equal(hasFiredAssignee({ status: 'assigned', assignee: { id: FIRED, is_fired: true } }), true);
  assert.equal(hasFiredAssignee(null), false);
});

test('суперадмин переносит карточку уволенного из любой колонки в любую другую', () => {
  STARTS.forEach(([status, extra]) => {
    const task = firedTaskIn(status, extra);
    const from = columnOfTask(task);
    BOARD_COLUMNS.forEach((column) => {
      const decision = resolveBoardDrop(task, column.id, superAdmin());
      const label = `${from} → ${column.id}`;
      if (column.id === from) assert.deepEqual(decision, { type: 'noop' }, label);
      else assert.deepEqual(decision, { type: 'move', column: column.id }, label);
    });
  });
});

test('суперадмин переносит и свою задачу с уволенным соисполнителем, и любую чужую', () => {
  const shared = taskIn('in_progress', { assignees: [{ id: STRANGER }, { id: FIRED, is_fired: true }] });
  assert.deepEqual(resolveBoardDrop(shared, 'done', superAdmin()), { type: 'move', column: 'done' });
  assert.deepEqual(resolveBoardDrop(firedTaskIn('assigned'), 'review', superAdmin({ currentUserId: BOSS })),
    { type: 'move', column: 'review' });
});

test('без суперадмина и без уволенного — прежние правила цикла', () => {
  // Админ, не суперадмин: карточка уволенного по-прежнему ждёт исполнителя.
  const admin = as(STRANGER, { isAdmin: true });
  assert.equal(resolveBoardDrop(firedTaskIn('assigned'), 'progress', admin).type, 'blocked');
  assert.equal(resolveBoardDrop(firedTaskIn('in_progress'), 'review', admin).type, 'blocked');
  assert.equal(resolveBoardDrop(firedTaskIn('assigned'), 'done', admin).type, 'blocked');
  // Приёмка админом — как была: это его право и без увольнения.
  assert.deepEqual(resolveBoardDrop(firedTaskIn('completed'), 'done', admin), { type: 'status', action: 'accepted' });
  // Суперадмин и работающий исполнитель: цикл двигает исполнитель.
  assert.equal(resolveBoardDrop(taskIn('assigned'), 'progress', superAdmin()).type, 'blocked');
  assert.equal(resolveBoardDrop(taskIn('assigned'), 'done', superAdmin()).type, 'blocked');
  assert.deepEqual(resolveBoardDrop(taskIn('completed'), 'done', superAdmin()), { type: 'status', action: 'accepted' });
  // Уволен постановщик, исполнитель работает — тоже цикл.
  const firedBoss = taskIn('assigned', { creator: { id: BOSS, is_fired: true } });
  assert.equal(resolveBoardDrop(firedBoss, 'done', superAdmin()).type, 'blocked');
  // Неизвестная колонка — не перенос.
  assert.equal(resolveBoardDrop(firedTaskIn('assigned'), 'archive', superAdmin()).type, 'blocked');
});

test('задача коллеги-СВ остаётся только на просмотр', () => {
  const observed = firedTaskIn('assigned', { viewer_is_observer: true });
  assert.equal(resolveBoardDrop(observed, 'done', superAdmin()).type, 'blocked');
});

/* Колонки после действия перечитываются каждая своим запросом. Замер на стенде
   (17.11.2026): перенесённая карточка уходила из ответа старой колонки на 10–33 мс
   раньше, чем приходила в ответе новой, и на кадр-два пропадала с доски. */
test('карточка с правкой не пропадает с доски, пока колонки дочитываются', () => {
  const stale = { id: 906, status: 'in_progress', is_backlog: false };
  const other = { id: 5, status: 'in_progress', is_backlog: false };
  const kept = new Map([['906', stale]]);
  const overrides = { 906: { status: 'completed', is_backlog: false } };
  const state = (progress, review, reviewLoading) => ({
    todo: { tasks: [], loading: false },
    progress: { tasks: progress, loading: false },
    review: { tasks: review, loading: reviewLoading },
  });
  const ids906 = (rows) => rows.filter((row) => row.id === 906);

  // «В работе» уже перечитана без неё, «На проверке» ещё в пути — строка держится.
  assert.deepEqual(ids906(mergeBoardRows(state([other], [], true), overrides, kept)), [stale]);
  // Пришла и «На проверке» — на доске свежая строка сервера, прежняя не нужна.
  const fresh = { id: 906, status: 'completed', is_backlog: false };
  assert.deepEqual(ids906(mergeBoardRows(state([other], [fresh], false), overrides, kept)), [fresh]);
  // Пока свежая строка есть хоть в одной колонке, прежняя её не заменяет.
  assert.deepEqual(ids906(mergeBoardRows(state([other], [fresh], true), overrides, kept)), [fresh]);
  // Все колонки дочитаны, а её нет — ушла за порцию колонки: не показываем.
  assert.deepEqual(ids906(mergeBoardRows(state([other], [], false), overrides, kept)), []);
  // Без правки прежняя строка не нужна — даже пока колонки грузятся.
  assert.deepEqual(ids906(mergeBoardRows(state([other], [], true), {}, kept)), []);
  // Без запомненных строк — просто объединение колонок.
  assert.deepEqual(mergeBoardRows(state([other], [], true), overrides, null), [other]);
});

test('перенос уходит на сервер с той колонкой, куда бросили; отказ снимает правку', async () => {
  const calls = [];
  const item = { task_id: 401, status: 'accepted', is_backlog: false };
  const task = firedTaskIn('assigned');
  assert.deepEqual(await sendBoardMove(async (...args) => { calls.push(args); return item; }, task, 'done'), [item]);
  assert.deepEqual(calls, [[task, 'done']]);
  // Ручка вернула false (сервер отказал) — для доски это отказ, а не «записано».
  assert.equal(await sendBoardMove(async () => false, task, 'done'), false);
  assert.equal(await sendBoardMove(async () => null, task, 'done'), false);
});

test('уволенный поручитель — не карточка уволенного (K1)', () => {
  assert.equal(hasFiredAssignee(taskIn('assigned', { requested_by: { id: BOSS, is_fired: true } })), false);
});
