import test from 'node:test';
import assert from 'node:assert/strict';

import {
  TASK_PROJECT_ALL,
  TASK_PROJECT_NONE,
  buildProjectOptions,
  creatableProjects,
  hasProjectBoards,
  keepWatchersForProject,
  mergeSelectedProject,
  normalizeProjectValue,
  TASK_SECTION_ALL,
  TASK_SECTION_NONE,
  buildSectionOptions,
  resolveSectionFilter,
  sectionQueryParams,
  normalizeTaskKey,
  projectLabel,
  projectQueryParams,
  PROJECT_CODE_RE,
  projectCodeIssue,
  resolveComposerProject,
  resolveInitialProject,
  taskKeyOf,
  watcherCandidates,
} from '../src/components/tasks/taskProjects.js';
import { boardQueryParams, scopeQueryParams } from '../src/components/tasks/boardQuery.js';
import { findFocusTask, planTaskFocus } from '../src/components/tasks/taskFocus.js';

const HR = {
  id: 1, code: 'HR', name: 'HR отдел', can_create: true, is_archived: false, task_count: 3,
  members: [
    { id: 98, name: 'Кадрова Камила', access_level: 'member' },
    { id: 99, name: 'Кадров Кайрат', access_level: 'board' },
    { id: 7, name: 'Бухов Борис', access_level: 'board' },
  ],
};
const ACC = { id: 3, code: 'ACC', name: 'Бухгалтерия', can_create: true, is_archived: false, members: [] };
// Видна только потому, что в ней лежит моя задача: ставить туда нельзя.
const FIN = { id: 2, code: 'FIN', name: 'Фин отдел', can_create: false, is_archived: false, access: null };
const OLD = { id: 9, code: 'MKT', name: 'Маркетинг', can_create: false, is_archived: true, members: [] };
// Архивная, но с задачами — её доску надо где-то смотреть.
const ARCHIVED_WITH_TASKS = { id: 8, code: 'ITH', name: 'IT Helpdesk', can_create: false, is_archived: true, task_count: 4 };
const PROJECTS = [HR, ACC, FIN, OLD];

test('«Все проекты» — это отсутствие фильтра, а не значение для сервера', () => {
  assert.deepEqual(projectQueryParams(TASK_PROJECT_ALL), {});
  assert.deepEqual(projectQueryParams(null), {});
  assert.deepEqual(projectQueryParams(''), {});
});

test('проект и «без проекта» уходят в project_id', () => {
  assert.deepEqual(projectQueryParams(1), { project_id: '1' });
  assert.deepEqual(projectQueryParams('1'), { project_id: '1' });
  assert.deepEqual(projectQueryParams(TASK_PROJECT_NONE), { project_id: 'none' });
});

test('мусор из localStorage не уезжает в запрос', () => {
  assert.equal(normalizeProjectValue('чепуха'), TASK_PROJECT_ALL);
  assert.equal(normalizeProjectValue('-4'), TASK_PROJECT_ALL);
  assert.equal(normalizeProjectValue('1.5'), TASK_PROJECT_ALL);
  assert.deepEqual(projectQueryParams('0'), {});
});

test('доска и выгрузка несут проект одним и тем же правилом', () => {
  assert.deepEqual(scopeQueryParams('all', '3'), { project_id: '3' });
  assert.deepEqual(scopeQueryParams('my', '3'), { project_id: '3', mine: 'any' });
  const column = boardQueryParams({ scope: 'all', mode: 'board', column: 'todo', limit: 20, offset: 0, projectId: 'none' });
  assert.equal(column.project_id, 'none');
  assert.equal(column.status, 'assigned');
  assert.equal(boardQueryParams({ scope: 'all', mode: 'board', limit: 20, offset: 0 }).project_id, undefined);
});

test('варианты: «Все» сверху, «Без проекта» снизу, архив скрыт, порядок и подпись — по ключу', () => {
  const options = buildProjectOptions(PROJECTS, 2, TASK_PROJECT_ALL);
  assert.deepEqual(options.map((option) => option.value), [TASK_PROJECT_ALL, '3', '2', '1', TASK_PROJECT_NONE]);
  assert.equal(options[1].label, 'ACC · Бухгалтерия');
  assert.equal(projectLabel({ id: 5 }), 'Проект №5');
});

test('выбранный архивный проект и выбранное «Без проекта» не пропадают из списка', () => {
  assert.ok(buildProjectOptions(PROJECTS, 0, '9').some((option) => option.value === '9'));
  assert.ok(buildProjectOptions(PROJECTS, 0, TASK_PROJECT_NONE).some((option) => option.value === TASK_PROJECT_NONE));
  assert.equal(buildProjectOptions(PROJECTS, 0, TASK_PROJECT_ALL).some((option) => option.value === TASK_PROJECT_NONE), false);
});

test('переключатель нужен, только когда есть живая доска', () => {
  assert.equal(hasProjectBoards([]), false);
  assert.equal(hasProjectBoards([OLD]), false);
  assert.equal(hasProjectBoards(PROJECTS), true);
});

test('на входе: сохранённый выбор → доска своего отдела → все', () => {
  assert.equal(resolveInitialProject(PROJECTS, 1, ''), '1');
  assert.equal(resolveInitialProject(PROJECTS, 1, '3'), '3');
  assert.equal(resolveInitialProject(PROJECTS, 1, TASK_PROJECT_ALL), TASK_PROJECT_ALL);
  // Устаревший выбор (проект в архиве, человека сняли с доски) раздел не сужает.
  assert.equal(resolveInitialProject(PROJECTS, 1, '9'), '1');
  assert.equal(resolveInitialProject(PROJECTS, null, '777'), TASK_PROJECT_ALL);
  assert.equal(resolveInitialProject([], 1, '1'), TASK_PROJECT_ALL);
  // «Без проекта» помним, только пока такие задачи есть.
  assert.equal(resolveInitialProject(PROJECTS, null, TASK_PROJECT_NONE, 4), TASK_PROJECT_NONE);
  assert.equal(resolveInitialProject(PROJECTS, null, TASK_PROJECT_NONE, 0), TASK_PROJECT_ALL);
});

test('в форму — только доски, куда можно ставить, и подставляется открытая', () => {
  assert.deepEqual(creatableProjects(PROJECTS).map((project) => project.code), ['ACC', 'HR']);
  assert.equal(resolveComposerProject(PROJECTS, { boardValue: '1' }), '1');
  // Открыта доска, куда ставить нельзя, — берём последнюю, куда ставили.
  assert.equal(resolveComposerProject(PROJECTS, { boardValue: '2', lastUsed: '3' }), '3');
  assert.equal(resolveComposerProject(PROJECTS, { boardValue: 'all', lastUsed: '9', defaultProjectId: 1 }), '1');
  assert.equal(resolveComposerProject(PROJECTS, { boardValue: 'all' }), '');
  assert.equal(resolveComposerProject([], { boardValue: '1' }), '');
});

test('наблюдателей зовут из доступа «доска», без исполнителей и себя', () => {
  assert.deepEqual(watcherCandidates(HR).map((person) => person.id), [99, 7]);
  assert.deepEqual(watcherCandidates(HR, ['7']).map((person) => person.id), [99]);
  assert.deepEqual(watcherCandidates(null), []);
});

test('смена доски оставляет наблюдателей, у кого есть доступ к новой', () => {
  assert.deepEqual(keepWatchersForProject(['99', '7', '55'], HR), ['99', '7']);
  assert.deepEqual(keepWatchersForProject(['99'], ACC), []);
  assert.deepEqual(keepWatchersForProject(['99'], null), []);
});

test('ключ задачи: регистр и решётка не важны, мусор — пусто', () => {
  assert.equal(normalizeTaskKey('hr-125'), 'HR-125');
  assert.equal(normalizeTaskKey(' #SZOV-7 '), 'SZOV-7');
  assert.equal(normalizeTaskKey('HR-007'), 'HR-7');
  assert.equal(normalizeTaskKey('HR-0'), '');
  assert.equal(normalizeTaskKey('ХР-1'), '');
  assert.equal(normalizeTaskKey('1HR-1'), '');
  assert.equal(normalizeTaskKey('125'), '');
  assert.equal(taskKeyOf({ key: 'acc-4' }), 'ACC-4');
  assert.equal(taskKeyOf({ id: 5 }), '');
});

test('ссылка по ключу: открываем из набора, а нет — идём на сервер по task_key', () => {
  const pools = [[{ id: 401, key: 'HR-1' }, { id: 402, key: null }]];
  assert.equal(findFocusTask(0, pools, 'hr-1')?.id, 401);
  const open = planTaskFocus({ request: { requestId: 3, taskKey: 'hr-1' }, pools });
  assert.equal(open.kind, 'open');
  assert.equal(open.taskId, 401);
  const fetch = planTaskFocus({ request: { requestId: 4, taskKey: 'ACC-9' }, pools });
  assert.deepEqual([fetch.kind, fetch.taskKey, fetch.taskId], ['fetch', 'ACC-9', 0]);
  // Номер точнее ключа: пришли оба — ищем по номеру.
  const both = planTaskFocus({ request: { requestId: 5, taskId: 402, taskKey: 'HR-1' }, pools });
  assert.equal(both.task?.id, 402);
  assert.equal(planTaskFocus({ request: { requestId: 6, taskKey: 'мусор' }, pools }).kind, 'idle');
});

test('архивная доска с задачами остаётся в переключателе с пометкой, пустая — нет', () => {
  const options = buildProjectOptions([...PROJECTS, ARCHIVED_WITH_TASKS], 0, TASK_PROJECT_ALL);
  const archived = options.find((option) => option.value === '8');
  assert.equal(archived?.label, 'ITH · IT Helpdesk · архив');
  assert.equal(options.some((option) => option.value === '9'), false);
  // На такую доску можно и войти по сохранённому выбору.
  assert.equal(resolveInitialProject([...PROJECTS, ARCHIVED_WITH_TASKS], null, '8'), '8');
  assert.equal(resolveInitialProject(PROJECTS, null, '9'), TASK_PROJECT_ALL);
});

test('выбранная доска не пропадает из списка, когда человека сняли с неё', () => {
  const refreshed = PROJECTS.filter((project) => project.id !== 1);
  const merged = mergeSelectedProject(refreshed, PROJECTS, '1');
  const kept = merged.find((project) => project.id === 1);
  assert.ok(kept);
  // Ставить туда больше нельзя и состава в ней не видно.
  assert.equal(kept.can_create, false);
  assert.equal(kept.members, undefined);
  assert.equal(mergeSelectedProject(refreshed, PROJECTS, TASK_PROJECT_ALL), refreshed);
  assert.equal(mergeSelectedProject(refreshed, PROJECTS, TASK_PROJECT_NONE), refreshed);
  assert.equal(mergeSelectedProject(refreshed, [], '1'), refreshed);
});

const HR_WITH_SECTIONS = {
  ...HR, unsectioned_count: 2,
  sections: [{ id: 11, name: 'Подбор', task_count: 3 }, { id: 12, name: 'Адаптация', task_count: 0 }],
};

test('разделы доски: «Все» сверху, «Без раздела» — только когда такие задачи есть', () => {
  const options = buildSectionOptions(HR_WITH_SECTIONS, TASK_SECTION_ALL);
  assert.deepEqual(options.map((option) => option.label), ['Все разделы', 'Подбор', 'Адаптация', 'Без раздела']);
  const noLoose = buildSectionOptions({ ...HR_WITH_SECTIONS, unsectioned_count: 0 }, TASK_SECTION_ALL);
  assert.equal(noLoose.some((option) => option.value === TASK_SECTION_NONE), false);
  // Выбранное «Без раздела» не пропадает, даже когда такие задачи кончились.
  const kept = buildSectionOptions({ ...HR_WITH_SECTIONS, unsectioned_count: 0 }, TASK_SECTION_NONE);
  assert.equal(kept.at(-1).value, TASK_SECTION_NONE);
});

test('фильтр раздела уходит в section_id, «Все» — без параметра', () => {
  assert.deepEqual(sectionQueryParams(TASK_SECTION_ALL), {});
  assert.deepEqual(sectionQueryParams('11'), { section_id: '11' });
  assert.deepEqual(sectionQueryParams(TASK_SECTION_NONE), { section_id: 'none' });
  assert.deepEqual(sectionQueryParams('чепуха'), {});
  const params = boardQueryParams({ scope: 'all', mode: 'board', column: 'todo', limit: 20, offset: 0, projectId: '1', sectionId: '11' });
  assert.equal(params.project_id, '1');
  assert.equal(params.section_id, '11');
});

test('удалённый раздел возвращает фильтр на «Все разделы»', () => {
  assert.equal(resolveSectionFilter(HR_WITH_SECTIONS, '11'), '11');
  assert.equal(resolveSectionFilter(HR_WITH_SECTIONS, '99'), TASK_SECTION_ALL);
  assert.equal(resolveSectionFilter(HR_WITH_SECTIONS, TASK_SECTION_NONE), TASK_SECTION_NONE);
  assert.equal(resolveSectionFilter(null, '11'), TASK_SECTION_ALL);
});

/* Ключ доски в окне «Проекты» (владелец 06.11.2026: ключ можно менять). */
test('ключ доски: занятый — сразу, шаблон — после «Сохранить», отказ сервера — пока ключ тот же', () => {
  const boards = [{ id: 1, code: 'HR' }, { id: 2, code: 'ACC' }];
  // Свой ключ — не «занят».
  assert.equal(projectCodeIssue({ code: 'HR', projectId: 1 }, boards), '');
  // Чужой — сразу, без нажатия «Сохранить», и у новой доски тоже.
  assert.equal(projectCodeIssue({ code: 'ACC', projectId: 1 }, boards), 'Проект с таким ключом уже есть');
  assert.equal(projectCodeIssue({ code: 'HR', projectId: null }, boards), 'Проект с таким ключом уже есть');
  // Пока печатают — шаблоном не ругаемся; после попытки сохранить — да.
  assert.equal(projectCodeIssue({ code: 'H', projectId: 1 }, boards), '');
  assert.match(projectCodeIssue({ code: 'H', projectId: 1, strict: true }, boards), /латинских/);
  assert.match(projectCodeIssue({ code: '1HR', projectId: 1, strict: true }, boards), /латинских/);
  // Бывший ключ чужой доски знает только сервер: его отказ — у поля, пока ключ тот же.
  const refusal = { code: 'OLD', text: 'Этим ключом раньше назывались задачи другой доски' };
  assert.equal(projectCodeIssue({ code: 'OLD', projectId: 1, refusal, strict: true }, boards), refusal.text);
  assert.equal(projectCodeIssue({ code: 'NEW', projectId: 1, refusal, strict: true }, boards), '');
  // Тот же шаблон, что CHECK на task_projects.code.
  for (const ok of ['HR', 'HRD', 'IC', 'SZOV2', 'A123456789']) assert.ok(PROJECT_CODE_RE.test(ok), ok);
  for (const bad of ['H', 'hr', '1HR', 'HR-1', 'A1234567890', '']) assert.ok(!PROJECT_CODE_RE.test(bad), bad);
});
