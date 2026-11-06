/*
 * Порядок бэклога «Задач»: балл приоритета, внутри балла — ручная очередь.
 *
 * Балл — число от 1 до 5: 1 — важнее всего, 5 — можно позже. Ставят его только
 * суперадмины (сервер держит то же правило), в основном на совещании по бэклогу.
 * Задачи без балла идут после всех оценённых. Тот же порядок отдаёт сервер
 * (database.py, get_tasks_for_requester при backlog=only): разъедься они — первая
 * страница бэклога показала бы не те задачи, что стоят в очереди первыми.
 *
 * Модуль отдельный, чтобы гонять его тестами через node --test.
 */

export const PRIORITY_SCORES = [1, 2, 3, 4, 5];

/** Балл задачи или null, если его нет (или пришло что-то не из шкалы). */
export const priorityScoreOf = (task) => {
  const value = Number(task?.priority_score);
  return PRIORITY_SCORES.includes(value) ? value : null;
};

// Задачи без балла — после всех оценённых.
const scoreKey = (task) => priorityScoreOf(task) ?? PRIORITY_SCORES.length + 1;

const rankOf = (task) => (Number.isFinite(task?.backlog_rank) ? task.backlog_rank : Number.POSITIVE_INFINITY);

const createdAtOf = (task) => {
  const time = new Date(task?.created_at || 0).getTime();
  return Number.isNaN(time) ? 0 : time;
};

/** Порядок бэклога: балл (1 → 5, без балла — в конце), ручная очередь, свежесть, id. */
export const compareBacklogTasks = (left, right) => {
  const byScore = scoreKey(left) - scoreKey(right);
  if (byScore) return byScore;
  const leftRank = rankOf(left);
  const rightRank = rankOf(right);
  if (leftRank !== rightRank) return leftRank < rightRank ? -1 : 1;
  const byCreated = createdAtOf(right) - createdAtOf(left);
  if (byCreated) return byCreated;
  return Number(right?.id || 0) - Number(left?.id || 0);
};

/** Подпись группы: смысл шкалы подписан на её краях, середина — просто балл. */
export const priorityScoreTitle = (score) => {
  if (!score) return 'Без балла';
  if (score === PRIORITY_SCORES[0]) return `Балл ${score} · важнее всего`;
  if (score === PRIORITY_SCORES[PRIORITY_SCORES.length - 1]) return `Балл ${score} · можно позже`;
  return `Балл ${score}`;
};

export const PRIORITY_SCORE_HINT = '1 — важнее всего, 5 — можно позже.';

// Шаблон регламента: в бэклоге он стоит на паузе и не рождает повторы, вне бэклога —
// рождает. Балл его туда-обратно не двигает, а балл шаблона на повторы не переходит.
const isRegulationTemplate = (task) => Boolean(task?.is_regulation) && !task?.regulation_parent_id;

/**
 * Задача ждёт балла: лежит в «Новой» (назначена, не начата, не в бэклоге) и ещё
 * не оценена. «Новая» — то, что ещё не разобрали на совещании, бэклог — очередь
 * уже оценённых (решение владельца 06.11.2026).
 */
export const awaitsPriorityScore = (task) => (
  priorityScoreOf(task) === null
  && task?.status === 'assigned'
  && !task?.is_backlog
  && !isRegulationTemplate(task)
);

/**
 * Первый балл переводит задачу из «Новой» в бэклог — то же правило, что у сервера
 * (update_task_board_state). Смена балла у оценённой задачу не двигает.
 */
export const scoreMovesToBacklog = (task, score) => (
  PRIORITY_SCORES.includes(Number(score)) && awaitsPriorityScore(task)
);

/**
 * Снятый балл возвращает задачу из бэклога в «Новую» — снова к разбору. Снимают его
 * в карточке задачи, повторным нажатием на выбранный балл.
 */
export const unscoreMovesToNew = (task, score) => (
  (score === null || score === undefined)
  && priorityScoreOf(task) !== null
  && task?.status === 'assigned'
  && Boolean(task?.is_backlog)
  && !isRegulationTemplate(task)
);

/** Куда балл уводит задачу: 'backlog', 'new' или null — чтобы карточка переехала сразу. */
export const scoreBoardMove = (task, score) => {
  if (scoreMovesToBacklog(task, score)) return 'backlog';
  if (unscoreMovesToNew(task, score)) return 'new';
  return null;
};

/**
 * Бэклог группами по баллу (список уже отсортирован compareBacklogTasks).
 * index — место задачи во всей очереди: по нему считается перенос мышью.
 */
export const groupBacklogByScore = (tasks) => {
  const groups = [];
  tasks.forEach((task, index) => {
    const score = priorityScoreOf(task);
    const last = groups[groups.length - 1];
    if (last && last.score === score) last.items.push({ task, index });
    else groups.push({ score, title: priorityScoreTitle(score), items: [{ task, index }] });
  });
  return groups;
};

/** Новый ранг для позиции index в уже отфильтрованном списке (без перетаскиваемой карточки). */
export const rankForPosition = (listWithoutDragged, index) => {
  const prev = listWithoutDragged[index - 1]?.backlog_rank;
  const next = listWithoutDragged[index]?.backlog_rank;
  const prevNum = Number.isFinite(prev) ? prev : null;
  const nextNum = Number.isFinite(next) ? next : null;
  if (prevNum === null && nextNum === null) return index + 1;
  if (prevNum === null) return nextNum - 1;
  if (nextNum === null) return prevNum + 1;
  if (nextNum - prevNum < 1e-6) return prevNum + 1e-6;
  return (prevNum + nextNum) / 2;
};

/**
 * Перенос карточки в бэклоге. dropIndex — место вставки во всём списке (до
 * удаления перетаскиваемой). Порядок между баллами задаёт сам балл, поэтому
 * переставлять можно только внутри своего балла: иначе после сохранения карточка
 * «отпрыгнула» бы обратно к своим.
 * Возвращает {type:'noop'} | {type:'blocked'} | {type:'move', insertAt, rank}.
 */
export const resolveBacklogDrop = (tasks, draggedId, dropIndex) => {
  const currentIndex = tasks.findIndex((task) => task.id === draggedId);
  if (currentIndex < 0 || dropIndex === null || dropIndex === undefined) return { type: 'noop' };
  const without = tasks.filter((task) => task.id !== draggedId);
  const insertAt = dropIndex > currentIndex ? dropIndex - 1 : dropIndex;
  if (insertAt === currentIndex) return { type: 'noop' };

  const key = scoreKey(tasks[currentIndex]);
  const prev = without[insertAt - 1];
  const next = without[insertAt];
  if ((prev && scoreKey(prev) > key) || (next && scoreKey(next) < key)) return { type: 'blocked' };

  // Ранг считаем по соседям из того же балла: ранги других баллов к порядку внутри не относятся.
  const group = without.filter((task) => scoreKey(task) === key);
  const before = without.slice(0, insertAt).filter((task) => scoreKey(task) === key).length;
  return { type: 'move', insertAt, rank: rankForPosition(group, before) };
};
