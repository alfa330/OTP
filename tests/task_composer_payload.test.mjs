// Что форма задачи отправляет на сервер про проект и наблюдателей (задача #401).
//
// Исполняется НАСТОЯЩИЙ buildTaskJsonPayload из TasksView.jsx: модуль собирается
// esbuild'ом, как в соседних *_render-тестах, а заглушка document нужна потому,
// что TasksView на загрузке кладёт свои стили в <head>.
//
// Главное правило: проект и наблюдатели уходят, только когда их правда меняли.
// Быстрая правка закреплённой задачи собирает форму из слепка без проекта, и
// отправь форма project_id всегда — правка одной темы вынимала бы задачу с доски.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { buildSync } = require('esbuild');

const stubNode = () => ({
  setAttribute() {}, appendChild() {}, style: {}, dataset: {}, classList: { add() {}, remove() {} },
});
globalThis.document = globalThis.document || {
  createElement: stubNode, createTextNode: () => ({}), querySelector: () => null, querySelectorAll: () => [],
  getElementById: () => null, addEventListener() {}, removeEventListener() {},
  head: { appendChild() {}, querySelector: () => null },
  body: { appendChild() {}, classList: { add() {}, remove() {} } },
  documentElement: { style: {}, classList: { add() {}, remove() {} } },
};

const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(dir, { recursive: true });
const outfile = join(dir, 'tasks_TasksView_payload.mjs');
buildSync({
  entryPoints: [fileURLToPath(new URL('../src/components/tasks/TasksView.jsx', import.meta.url))],
  bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
  packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' },
  logLevel: 'silent',
  // Помощники формы не экспортируются из модуля раздела — достаём их в сборке теста.
  footer: { js: 'export { buildTaskJsonPayload as __payload, taskToTaskForm as __toForm, buildEmptyTaskForm as __empty };' },
});
const { __payload: payloadOf, __toForm: toForm, __empty: emptyForm } = await import(`file://${outfile.replace(/\\/g, '/')}`);

const SNAPSHOT = {
  id: 116, subject: 'Шаблон приказа', status: 'assigned', tag: 'task',
  creator: { id: 22 }, assignees: [{ id: 98 }],
};
const FULL = { ...SNAPSHOT, project: { id: 1, code: 'HR', name: 'HR отдел' }, watchers: [{ id: 99 }] };

test('быстрая правка из слепка без проекта не трогает проект и наблюдателей', () => {
  const payload = payloadOf({ ...toForm(SNAPSHOT, 22), subject: 'Новая тема' });
  assert.equal('project_id' in payload, false);
  assert.equal('watcher_ids' in payload, false);
  assert.equal(payload.subject, 'Новая тема');
});

test('правка задачи проекта без смены доски не шлёт ни проект, ни наблюдателей', () => {
  const payload = payloadOf({ ...toForm(FULL, 22), subject: 'Ещё тема' });
  assert.equal('project_id' in payload, false);
  assert.equal('watcher_ids' in payload, false);
});

test('перенос на другую доску и смена наблюдателей уходят', () => {
  const moved = payloadOf({ ...toForm(FULL, 22), projectId: '3', watcherIds: [] });
  assert.equal(moved.project_id, 3);
  assert.deepEqual(moved.watcher_ids, []);
  const watched = payloadOf({ ...toForm(FULL, 22), watcherIds: ['99', '7'] });
  assert.equal('project_id' in watched, false);
  assert.deepEqual(watched.watcher_ids, [99, 7]);
  // Порядок наблюдателей — не изменение.
  const reordered = payloadOf({ ...toForm({ ...FULL, watchers: [{ id: 99 }, { id: 7 }] }, 22), watcherIds: ['7', '99'] });
  assert.equal('watcher_ids' in reordered, false);
});

test('новая задача несёт выбранную доску и наблюдателей, без доски — ничего', () => {
  const created = payloadOf(emptyForm({ subject: 'н', assigneeIds: ['98'], projectId: '1', watcherIds: ['99'] }));
  assert.equal(created.project_id, 1);
  assert.deepEqual(created.watcher_ids, [99]);
  const plain = payloadOf(emptyForm({ subject: 'н', assigneeIds: ['98'], watcherIds: ['99'] }));
  assert.equal('project_id' in plain, false);
  assert.equal('watcher_ids' in plain, false);
});

test('раздел уходит, только когда его меняли; «Без раздела» в правке — null', () => {
  const inSection = { ...FULL, section: { id: 11, name: 'Подбор' } };
  const untouched = payloadOf({ ...toForm(inSection, 22), subject: 'тема' });
  assert.equal('section_id' in untouched, false);
  const moved = payloadOf({ ...toForm(inSection, 22), sectionId: '12' });
  assert.equal(moved.section_id, 12);
  const cleared = payloadOf({ ...toForm(inSection, 22), sectionId: '' });
  assert.equal(cleared.section_id, null);
  // Быстрая правка из слепка без раздела раздел не трогает.
  assert.equal('section_id' in payloadOf({ ...toForm(SNAPSHOT, 22), subject: 'x' }), false);
  const created = payloadOf(emptyForm({ subject: 'н', assigneeIds: ['98'], projectId: '1', sectionId: '11' }));
  assert.equal(created.section_id, 11);
});
