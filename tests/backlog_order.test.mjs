import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  PRIORITY_SCORES,
  awaitsPriorityScore,
  compareBacklogTasks,
  groupBacklogByScore,
  priorityScoreOf,
  priorityScoreTitle,
  rankForPosition,
  resolveBacklogDrop,
  scoreBoardMove,
  scoreMovesToBacklog,
  unscoreMovesToNew,
} from '../src/components/tasks/backlogOrder.js';

const task = (id, score, rank, createdAt = '2026-11-01T10:00:00') => ({
  id, priority_score: score, backlog_rank: rank, created_at: createdAt,
});
const ids = (list) => list.map((item) => item.id);

test('шкала — от 1 до 5, всё прочее баллом не считается', () => {
  assert.deepEqual(PRIORITY_SCORES, [1, 2, 3, 4, 5]);
  assert.equal(priorityScoreOf({ priority_score: 1 }), 1);
  assert.equal(priorityScoreOf({ priority_score: 5 }), 5);
  [null, undefined, 0, 6, -1, 2.5, 'нет'].forEach((value) => {
    assert.equal(priorityScoreOf({ priority_score: value }), null, String(value));
  });
});

test('бэклог идёт по баллу, без балла — в конце, внутри балла — по очереди', () => {
  const sorted = [
    task(1, null, 1),
    task(2, 3, 1),
    task(3, 1, 9),
    task(4, 1, 2),
    task(5, 5, 1),
    task(6, null, 0.5),
  ].sort(compareBacklogTasks);
  assert.deepEqual(ids(sorted), [4, 3, 2, 5, 6, 1]);
});

test('без ранга — после ранжированных своего балла, дальше свежие сверху', () => {
  const sorted = [
    task(1, 2, null, '2026-11-01T10:00:00'),
    task(2, 2, null, '2026-11-02T10:00:00'),
    task(3, 2, 7),
  ].sort(compareBacklogTasks);
  assert.deepEqual(ids(sorted), [3, 2, 1]);
});

test('подписи групп называют смысл шкалы только на её краях', () => {
  assert.equal(priorityScoreTitle(1), 'Балл 1 · важнее всего');
  assert.equal(priorityScoreTitle(3), 'Балл 3');
  assert.equal(priorityScoreTitle(5), 'Балл 5 · можно позже');
  assert.equal(priorityScoreTitle(null), 'Без балла');
});

test('группы идут подряд и помнят место задачи во всей очереди', () => {
  const sorted = [task(1, 1, 1), task(2, 1, 2), task(3, 4, 1), task(4, null, 1)];
  const groups = groupBacklogByScore(sorted);
  assert.deepEqual(groups.map((group) => group.score), [1, 4, null]);
  assert.deepEqual(groups.map((group) => group.title), ['Балл 1 · важнее всего', 'Балл 4', 'Без балла']);
  assert.deepEqual(groups[1].items.map((item) => item.index), [2]);
  assert.deepEqual(groups[2].items.map((item) => item.index), [3]);
});

test('перенос внутри балла считает ранг по соседям этого же балла', () => {
  // Балл 1: ранги 10 и 20; балл 2: ранги 1 и 2 (меньше, но это другой балл).
  const list = [task(1, 1, 10), task(2, 1, 20), task(3, 2, 1), task(4, 2, 2)];
  // Задачу 2 — наверх балла 1.
  assert.deepEqual(resolveBacklogDrop(list, 2, 0), { type: 'move', insertAt: 0, rank: 9 });
  // Задачу 4 — между 3 и… началом балла 2: соседом сверху задачу 1-го балла не считаем.
  assert.deepEqual(resolveBacklogDrop(list, 4, 2), { type: 'move', insertAt: 2, rank: 0 });
});

test('между баллами не переставляют — порядок там задаёт сам балл', () => {
  const list = [task(1, 1, 10), task(2, 1, 20), task(3, 2, 1), task(4, 2, 2), task(5, null, 1)];
  assert.deepEqual(resolveBacklogDrop(list, 3, 0), { type: 'blocked' });
  assert.deepEqual(resolveBacklogDrop(list, 1, 4), { type: 'blocked' });
  assert.deepEqual(resolveBacklogDrop(list, 5, 0), { type: 'blocked' });
  // На стыке своего балла — можно: конец балла 1.
  assert.equal(resolveBacklogDrop(list, 1, 2).type, 'move');
});

test('на своё место — ничего, одиночку в балле переставить некуда', () => {
  const list = [task(1, 1, 1), task(2, 3, 1), task(3, 5, 1)];
  assert.deepEqual(resolveBacklogDrop(list, 2, 1), { type: 'noop' });
  assert.deepEqual(resolveBacklogDrop(list, 2, 2), { type: 'noop' });
  assert.deepEqual(resolveBacklogDrop(list, 2, 0), { type: 'blocked' });
  assert.deepEqual(resolveBacklogDrop(list, 2, 3), { type: 'blocked' });
  assert.deepEqual(resolveBacklogDrop(list, 99, 0), { type: 'noop' });
});

test('без баллов бэклог переставляется как раньше — одной очередью', () => {
  const list = [task(1, null, 1), task(2, null, 2), task(3, null, 3)];
  assert.deepEqual(resolveBacklogDrop(list, 3, 0), { type: 'move', insertAt: 0, rank: 0 });
  assert.deepEqual(resolveBacklogDrop(list, 1, 3), { type: 'move', insertAt: 2, rank: 4 });
  assert.equal(rankForPosition([task(1, null, 1), task(2, null, 2)], 1), 1.5);
});

/* «Новая» — ещё не разобранное на совещании, бэклог — очередь оценённых: первый балл
   переводит задачу из «Новой» в бэклог (решение владельца 06.11.2026). То же правило
   у сервера — tests/test_task_score_moves_to_backlog.py. */
const fresh = (extra = {}) => ({ id: 7, status: 'assigned', is_backlog: false, priority_score: null, ...extra });

test('первый балл уводит задачу из «Новой» в бэклог', () => {
  assert.equal(awaitsPriorityScore(fresh()), true);
  for (const score of PRIORITY_SCORES) assert.equal(scoreMovesToBacklog(fresh(), score), true);
  // Повтор регламента — обычная задача.
  assert.equal(scoreMovesToBacklog(fresh({ is_regulation: true, regulation_parent_id: 5 }), 2), true);
});

test('балл без переезда: уже оценённая, снятие балла, бэклог, начатая, шаблон регламента', () => {
  assert.equal(scoreMovesToBacklog(fresh({ priority_score: 3 }), 1), false);
  assert.equal(scoreMovesToBacklog(fresh(), null), false);
  assert.equal(scoreMovesToBacklog(fresh(), 0), false);
  assert.equal(scoreMovesToBacklog(fresh(), 6), false);
  assert.equal(scoreMovesToBacklog(fresh({ is_backlog: true }), 2), false);
  for (const status of ['in_progress', 'returned', 'completed', 'accepted']) {
    assert.equal(scoreMovesToBacklog(fresh({ status }), 2), false, status);
  }
  // Шаблон в бэклоге встал бы на паузу и перестал рождать повторы.
  assert.equal(awaitsPriorityScore(fresh({ is_regulation: true, regulation_parent_id: null })), false);
  assert.equal(scoreMovesToBacklog(fresh({ is_regulation: true }), 1), false);
});

/* Снятый в карточке задачи балл возвращает её из бэклога в «Новую» — снова к разбору. */
const queued = (extra = {}) => fresh({ is_backlog: true, priority_score: 2, ...extra });

test('снятый балл возвращает задачу из бэклога в «Новую»', () => {
  assert.equal(unscoreMovesToNew(queued(), null), true);
  assert.equal(unscoreMovesToNew(queued(), undefined), true);
  assert.equal(scoreBoardMove(queued(), null), 'new');
  // Повтор регламента — как обычная задача.
  assert.equal(scoreBoardMove(queued({ is_regulation: true, regulation_parent_id: 5 }), null), 'new');
});

test('без переезда: смена балла, «Новая», начатая, шаблон регламента', () => {
  assert.equal(scoreBoardMove(queued(), 4), null);                    // поменяли балл в бэклоге
  assert.equal(scoreBoardMove(queued({ is_backlog: false }), null), null); // оценённая в «Новой»
  assert.equal(scoreBoardMove(queued({ priority_score: null }), null), null);
  for (const status of ['in_progress', 'returned', 'completed', 'accepted']) {
    assert.equal(scoreBoardMove(queued({ status }), null), null, status);
  }
  // Шаблон в бэклоге на паузе: вне бэклога он догнал бы пропущенные повторы.
  assert.equal(scoreBoardMove(queued({ is_regulation: true, regulation_parent_id: null }), null), null);
  assert.equal(scoreBoardMove(fresh(), 3), 'backlog');
});

/* Та же таблица случаев, что у сервера (tests/test_task_score_moves_to_backlog.py):
   правило «куда балл уводит задачу» живёт и во фронте, и в update_task_board_state. */
const SHARED = JSON.parse(readFileSync(new URL('./fixtures/score_moves.json', import.meta.url), 'utf8')).cases;

test('фронт двигает карточку так же, как сервер — по общей таблице', () => {
  assert.ok(SHARED.length > 10);
  for (const item of SHARED) {
    assert.equal(scoreBoardMove(fresh(item.task), item.score), item.move, item.name);
  }
});
