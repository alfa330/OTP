import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BOARD_COLUMN_QUERY,
  boardQueryParams,
  normalizeBoardChunk,
  scopeQueryParams,
} from '../src/components/tasks/boardQuery.js';

const columnParams = (column, extra = {}) =>
  boardQueryParams({ scope: 'all', mode: 'board', column, limit: 20, offset: 0, ...extra });

test('у каждой колонки свой срез — запросы не совпадают между собой', () => {
  const columns = Object.keys(BOARD_COLUMN_QUERY);
  const signatures = columns.map((column) => JSON.stringify(columnParams(column)));
  assert.equal(new Set(signatures).size, columns.length);
});

test('колонки бьются по статусам, «В работе» — это два статуса', () => {
  assert.equal(columnParams('todo').status, 'assigned');
  assert.equal(columnParams('progress').status, 'in_progress,returned');
  assert.equal(columnParams('review').status, 'completed');
  assert.equal(columnParams('done').status, 'accepted');
  // Бэклог — не статус, а флаг: его колонка ходит своим фильтром.
  assert.equal(columnParams('backlog').backlog, 'only');
  assert.equal(columnParams('backlog').status, undefined);
});

test('бэклог исключён из всех колонок, кроме своей', () => {
  ['todo', 'progress', 'review', 'done'].forEach((column) => {
    assert.equal(columnParams(column).backlog, 'exclude');
  });
});

test('вкладка «Бэклог» просит только бэклог, даже без колонки', () => {
  const params = boardQueryParams({ scope: 'my', mode: 'backlog', limit: 20, offset: 0 });
  assert.equal(params.backlog, 'only');
  assert.equal(params.status, undefined);
});

test('доска сотрудника уходит в person_id, свои доски — в mine', () => {
  assert.deepEqual(
    boardQueryParams({ scope: 'person:57', mode: 'board', column: 'todo', limit: 20, offset: 0 }),
    { limit: 20, offset: 0, status: 'assigned', backlog: 'exclude', person_id: 57, person_scope: 'any' }
  );
  assert.equal(boardQueryParams({ scope: 'my', mode: 'board', limit: 20, offset: 0 }).mine, 'any');
  assert.equal(boardQueryParams({ scope: 'assigned', mode: 'board', limit: 20, offset: 0 }).mine, 'assignee');
  // Общая доска не сужается ничем, кроме прав на сервере.
  const all = boardQueryParams({ scope: 'all', mode: 'board', limit: 20, offset: 0 });
  assert.equal(all.mine, undefined);
  assert.equal(all.person_id, undefined);
});

test('порядок и сводка передаются явно', () => {
  assert.equal(columnParams('todo', { sort: 'importance' }).sort, 'importance');
  assert.equal(columnParams('todo', { sort: 'freshness' }).sort, undefined);
  // Сводку просит одна колонка на загрузку, остальные её глушат.
  assert.equal(columnParams('todo').summary, undefined);
  assert.equal(columnParams('done', { withSummary: false }).summary, 0);
});

test('догрузка колонки просит следующий кусок, а не всё заново', () => {
  const more = columnParams('todo', { limit: 20, offset: 40, withSummary: false });
  assert.equal(more.offset, 40);
  assert.equal(more.limit, 20);
  assert.equal(more.status, 'assigned');
});

test('выгрузка просит тот же охват, что и доска — без колонок и порций', () => {
  // Excel собирается по всем колонкам сразу, поэтому из запроса уходит всё,
  // кроме «чьи задачи»: разъедься это с доской — в файл уехало бы лишнее.
  ['my', 'assigned', 'all', 'person:57'].forEach((scope) => {
    const board = boardQueryParams({ scope, mode: 'board', column: 'todo', limit: 20, offset: 0 });
    const exportParams = scopeQueryParams(scope);
    Object.entries(exportParams).forEach(([key, value]) => {
      assert.equal(board[key], value, `${scope}: ${key}`);
    });
    assert.equal(exportParams.limit, undefined);
    assert.equal(exportParams.status, undefined);
    assert.equal(exportParams.backlog, undefined);
  });
  assert.deepEqual(scopeQueryParams('all'), {});
});

test('размер порции — закрытый список, чужое значение не проходит', () => {
  assert.equal(normalizeBoardChunk(40), 40);
  assert.equal(normalizeBoardChunk('60'), 60);
  assert.equal(normalizeBoardChunk(500), 20);
  assert.equal(normalizeBoardChunk('чепуха'), 20);
  assert.equal(normalizeBoardChunk(null), 20);
});
