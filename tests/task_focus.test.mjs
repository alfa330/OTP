import test from 'node:test';
import assert from 'node:assert/strict';

import {
  findFocusTask,
  planTaskFocus,
  shouldOpenFetchedTask,
} from '../src/components/tasks/taskFocus.js';

const TASK = { id: 812, subject: 'Ссылка из Telegram' };
const REQUEST = { taskId: 812, requestId: 1 };

/*
 * Эффект раздела прогоняется здесь так же, как в браузере: план → действие →
 * следующий прогон с обновлёнными наборами. Ровно этот порядок и был сломан.
 */
const runSection = ({ request, pools = [] }) => {
  const state = { handled: 0, fetched: 0, opened: null, fetchCalls: 0 };
  const step = (nextPools = pools) => {
    const plan = planTaskFocus({
      request,
      handledRequestId: state.handled,
      fetchedRequestId: state.fetched,
      pools: nextPools,
    });
    if (plan.kind === 'open') {
      state.handled = plan.requestId;
      state.opened = plan.task;
    }
    if (plan.kind === 'fetch') {
      state.fetched = plan.requestId;
      state.fetchCalls += 1;
    }
    return plan;
  };
  const answerFetch = (task, requestId = request.requestId) => {
    if (!shouldOpenFetchedTask({
      task,
      requestId,
      handledRequestId: state.handled,
      fetchedRequestId: state.fetched,
    })) return false;
    state.handled = requestId;
    state.opened = task;
    return true;
  };
  return { state, step, answerFetch };
};

test('задача из загруженного набора открывается сразу, без запроса', () => {
  const section = runSection({ request: REQUEST, pools: [[], [TASK], []] });
  assert.equal(section.step().kind, 'open');
  assert.equal(section.state.opened, TASK);
  assert.equal(section.state.fetchCalls, 0);
});

test('задачи нет в наборах — идём за ней на сервер ровно один раз', () => {
  const section = runSection({ request: REQUEST, pools: [[], [], []] });
  assert.equal(section.step().kind, 'fetch');
  // Приехали «мои» и страница списка — эффект перезапускается, но запрос уже в пути.
  assert.equal(section.step([[], [], []]).kind, 'idle');
  assert.equal(section.step([[], [], []]).kind, 'idle');
  assert.equal(section.state.fetchCalls, 1);
});

test('«Доска»: ответ по ссылке приходит последним и всё равно открывает карточку', () => {
  const section = runSection({ request: REQUEST, pools: [[], [], []] });
  assert.equal(section.step().kind, 'fetch');
  // Пока запрос шёл, приехали колонки доски и наборы раздела — перезапуски эффекта.
  section.step([[{ id: 5 }], [{ id: 7 }], []]);
  section.step([[{ id: 5 }], [{ id: 7 }, { id: 9 }], []]);
  assert.equal(section.answerFetch(TASK), true);
  assert.equal(section.state.opened, TASK);
});

test('набор догрузился раньше ответа — карточку открывает набор, ответ уже не нужен', () => {
  const section = runSection({ request: REQUEST, pools: [[], [], []] });
  assert.equal(section.step().kind, 'fetch');
  assert.equal(section.step([[TASK], [], []]).kind, 'open');
  assert.equal(section.state.opened, TASK);
  // Ответ на тот же запрос приходит следом и не должен открывать карточку заново.
  assert.equal(section.answerFetch(TASK), false);
});

test('закрытую карточку обновление наборов не открывает заново', () => {
  const section = runSection({ request: REQUEST, pools: [[TASK], [], []] });
  assert.equal(section.step().kind, 'open');
  assert.equal(section.step([[TASK], [TASK], []]).kind, 'idle');
});

test('пустой ответ не считает ссылку отработанной, но и не зовёт сервер снова', () => {
  const section = runSection({ request: REQUEST, pools: [[], [], []] });
  section.step();
  assert.equal(section.answerFetch(null), false);
  assert.equal(section.state.handled, 0);
  assert.equal(section.step([[], [], []]).kind, 'idle');
  assert.equal(section.state.fetchCalls, 1);
});

test('новая ссылка перебивает старую: опоздавший ответ игнорируется', () => {
  const section = runSection({ request: { taskId: 812, requestId: 2 }, pools: [[], [], []] });
  section.step();
  assert.equal(section.answerFetch(TASK, 1), false);
  assert.equal(section.answerFetch(TASK, 2), true);
});

test('без ссылки и без id раздел ничего не делает', () => {
  assert.equal(planTaskFocus().kind, 'idle');
  assert.equal(planTaskFocus({ request: { requestId: 3 } }).kind, 'idle');
  assert.equal(planTaskFocus({ request: { taskId: 812 } }).kind, 'idle');
});

test('поиск по наборам терпим к мусору и сравнивает id числом', () => {
  assert.equal(findFocusTask(812, [null, undefined, [{ id: '812' }]])?.id, '812');
  assert.equal(findFocusTask(0, [[TASK]]), null);
});
