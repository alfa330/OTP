/*
 * Открыть задачу по ссылке: ?view=tasks&task_id=N из Telegram, переход из
 * колокола, клик по закреплённой задаче — всё это один и тот же focusTaskRequest,
 * и раздел обязан открыть карточку, где бы задача ни лежала.
 *
 * Правила вынесены отдельным модулем, потому что ломается здесь не отрисовка, а
 * ПОРЯДОК. Наборы раздела («мои», страница списка, задачи сотрудника) приезжают
 * несколькими ответами, эффект перезапускается на каждый из них, а запрос за
 * самой задачей идёт вдогонку — и решение «открыть / сходить за задачей /
 * промолчать» невозможно проверить тестом, не отделив его от эффекта.
 *
 * Два счётчика вместо одного — суть правила:
 *   handled — ссылка ОТРАБОТАНА, карточку открыли. Закрыл человек — не открываем
 *             заново, сколько бы раз наборы ни обновились;
 *   fetched — запрос за задачей уже ушёл. Второй раз не идём, но и «отработанной»
 *             ссылку не считаем: ответ может не прийти или прийти пустым, а
 *             задача — догрузиться в наборах раздела.
 */

const normalizeId = (value) => Number(value || 0);

/** Задача из любого загруженного набора раздела: наборы приезжают вразнобой. */
export const findFocusTask = (taskId, pools = []) => {
  const normalizedTaskId = normalizeId(taskId);
  if (!normalizedTaskId) return null;
  for (const pool of pools) {
    if (!Array.isArray(pool)) continue;
    const found = pool.find((task) => normalizeId(task?.id) === normalizedTaskId);
    if (found) return found;
  }
  return null;
};

/**
 * Что делать с текущим focusTaskRequest: 'open' — карточка уже в наборах,
 * 'fetch' — идти за ней на сервер, 'idle' — делать нечего.
 */
export const planTaskFocus = ({
  request = null,
  handledRequestId = 0,
  fetchedRequestId = 0,
  pools = [],
} = {}) => {
  const requestId = normalizeId(request?.requestId);
  const taskId = normalizeId(request?.taskId);
  const idle = { kind: 'idle', requestId, taskId, task: null };
  if (!requestId || !taskId) return idle;
  if (normalizeId(handledRequestId) === requestId) return idle;

  const task = findFocusTask(taskId, pools);
  if (task) return { kind: 'open', requestId, taskId, task };

  if (normalizeId(fetchedRequestId) === requestId) return idle;
  return { kind: 'fetch', requestId, taskId, task: null };
};

/**
 * Пришёл ответ на запрос за задачей. Пока он шёл, наборы раздела могли
 * догрузиться (эффект перезапустился и открыл карточку сам), а ссылка — сменить
 * задачу: и то и другое делает этот ответ ненужным.
 */
export const shouldOpenFetchedTask = ({
  task = null,
  requestId = 0,
  handledRequestId = 0,
  fetchedRequestId = 0,
} = {}) => {
  const normalizedRequestId = normalizeId(requestId);
  if (!task || !normalizedRequestId) return false;
  if (normalizeId(fetchedRequestId) !== normalizedRequestId) return false;
  return normalizeId(handledRequestId) !== normalizedRequestId;
};
