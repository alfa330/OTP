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
const { build } = require('esbuild');

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
await build({
  entryPoints: [fileURLToPath(new URL('../src/components/tasks/TasksView.jsx', import.meta.url))],
  bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
  packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' },
  logLevel: 'silent',
  // Помощники формы не экспортируются из модуля раздела — достаём их в сборке теста.
  footer: { js: 'export { buildTaskJsonPayload as __payload, taskToTaskForm as __toForm, buildEmptyTaskForm as __empty, appendTaskFormData as __multipart, deadlineIssues as __deadlineIssues };' },
  // PDF.js просмотра вложений грузится лениво, но esbuild всё равно кладёт его в
  // бандл, а его импорт воркера с «?url» понимает только Vite — node падал бы на
  // загрузке модуля раздела. Форме задачи PDF не нужен: подменяем пустышкой.
  plugins: [{
    name: 'stub-pdf-runtime',
    setup(stub) {
      stub.onResolve({ filter: /\/pdfRuntime$/ }, () => ({ path: 'pdfRuntime', namespace: 'stub' }));
      stub.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({
        contents: 'export const getDocument = () => { throw new Error("pdf.js в тесте не нужен"); }; export class TextLayer {}',
        loader: 'js',
      }));
    },
  }],
});
const {
  __payload: payloadOf, __toForm: toForm, __empty: emptyForm, __multipart: appendMultipart,
  __deadlineIssues: deadlineIssuesOf,
} = await import(`file://${outfile.replace(/\\/g, '/')}`);

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

/* ── Дедлайн днём и временем ──
   Форма шлёт срок строкой поля «ГГГГ-ММ-ДДTЧЧ:ММ» — стенными часами Алматы, в
   которых его отдаёт и принимает сервер. И только когда срок меняли: прежняя
   форма слала «через N дней» при каждой правке, сервер отсчитывал его от минуты
   правки, и срок молча уезжал вперёд. */
const DEADLINE_KEYS = ['deadline_days', 'deadline_hours', 'deadline_minutes'];
const DUE_TASK = {
  ...FULL, due_at: '2026-11-27T18:00:00', reminder_minutes_before: 1440, deadline_duration_minutes: 12345,
};

test('форма из задачи показывает её срок теми же стенными часами', () => {
  const form = toForm(DUE_TASK, 22);
  assert.equal(form.dueAt, '2026-11-27T18:00');
  assert.equal(form.reminderMinutes, '1440');
  assert.equal(toForm(SNAPSHOT, 22).dueAt, '');
});

test('правка без смены срока не шлёт ни срок, ни напоминание, ни «через N»', () => {
  const payload = payloadOf({ ...toForm(DUE_TASK, 22), subject: 'Новая тема' });
  [...DEADLINE_KEYS, 'due_at', 'reminder_minutes_before'].forEach((key) => {
    assert.equal(key in payload, false, key);
  });
});

test('быстрая правка из слепка без срока срок и напоминание не снимает', () => {
  const payload = payloadOf({ ...toForm(SNAPSHOT, 22), subject: 'x' });
  assert.equal('due_at' in payload, false);
  assert.equal('reminder_minutes_before' in payload, false);
});

test('новый срок уходит строкой поля вместе с напоминанием', () => {
  const payload = payloadOf({ ...toForm(DUE_TASK, 22), dueAt: '2026-11-30T10:00' });
  assert.equal(payload.due_at, '2026-11-30T10:00');
  assert.equal(payload.reminder_minutes_before, 1440);
  DEADLINE_KEYS.forEach((key) => assert.equal(key in payload, false, key));
});

test('снятый срок — null, напоминание без срока — 0', () => {
  const payload = payloadOf({ ...toForm(DUE_TASK, 22), dueAt: '', reminderMinutes: '' });
  assert.equal(payload.due_at, null);
  assert.equal(payload.reminder_minutes_before, 0);
  // Напоминание, оставшееся в поле, без срока тоже не уходит.
  assert.equal(payloadOf({ ...toForm(DUE_TASK, 22), dueAt: '' }).reminder_minutes_before, 0);
});

test('смена одного напоминания уходит без срока', () => {
  const payload = payloadOf({ ...toForm(DUE_TASK, 22), reminderMinutes: '60' });
  assert.equal('due_at' in payload, false);
  assert.equal(payload.reminder_minutes_before, 60);
});

test('новая задача: срок и напоминание уходят, без срока — ни того, ни другого', () => {
  const base = { subject: 'н', assigneeIds: ['98'] };
  const created = payloadOf(emptyForm({ ...base, dueAt: '2026-11-20T18:00', reminderMinutes: '1440' }));
  assert.equal(created.due_at, '2026-11-20T18:00');
  assert.equal(created.reminder_minutes_before, 1440);
  const plain = payloadOf(emptyForm({ ...base, reminderMinutes: '1440' }));
  assert.equal('due_at' in plain, false);
  assert.equal('reminder_minutes_before' in plain, false);
});

const multipartOf = (values) => {
  const fields = [];
  appendMultipart({ append: (key, value) => fields.push([key, value]) }, values);
  return fields;
};

test('создание формой раздела (multipart) несёт due_at и не несёт «через N»', () => {
  const fields = multipartOf(emptyForm({
    subject: 'н', assigneeIds: ['98'], dueAt: '2026-11-20T18:00', reminderMinutes: '1440',
  }));
  const keys = fields.map(([key]) => key);
  assert.deepEqual(fields.filter(([key]) => key === 'due_at'), [['due_at', '2026-11-20T18:00']]);
  assert.deepEqual(fields.filter(([key]) => key === 'reminder_minutes_before'), [['reminder_minutes_before', '1440']]);
  DEADLINE_KEYS.forEach((key) => assert.equal(keys.includes(key), false, key));
  // Без срока — ни срока, ни напоминания: сервер оставит задачу без дедлайна.
  const plainKeys = multipartOf(emptyForm({ subject: 'н', assigneeIds: ['98'] })).map(([key]) => key);
  assert.equal(plainKeys.includes('due_at'), false);
  assert.equal(plainKeys.includes('reminder_minutes_before'), false);
});

test('срок в прошлом — ошибка формы, но только если его меняли', () => {
  const now = new Date(2026, 10, 19, 19, 40);
  const issue = deadlineIssuesOf(emptyForm({ dueAt: '2026-11-19T18:00' }), now);
  assert.deepEqual(issue.map((item) => item.key), ['deadline']);
  assert.match(issue[0].text, /прошёл/);
  assert.deepEqual(deadlineIssuesOf(emptyForm({ dueAt: '2026-11-19T20:00' }), now), []);
  assert.deepEqual(deadlineIssuesOf(emptyForm({ dueAt: '' }), now), []);
  // Просроченная задача: правка темы не упирается в её прошедший срок…
  const overdue = toForm({ ...DUE_TASK, due_at: '2026-11-01T18:00:00' }, 22);
  assert.deepEqual(deadlineIssuesOf({ ...overdue, subject: 'x' }, now), []);
  // …а перенос на другое прошедшее время — упирается.
  assert.equal(deadlineIssuesOf({ ...overdue, dueAt: '2026-11-19T09:00' }, now).length, 1);
});

/* ── Повторяющаяся задача (шаблон регламента) ──
   Сервер ставит каждому повтору срок «начало повтора + deadline_duration_minutes».
   Дата из календаря ушла бы смещением от создания шаблона — у шаблона месячной
   давности каждый повтор получил бы срок через месяц. */
const REPEATING = {
  ...FULL, recurrence_type: 'daily', recurrence_interval: 1, is_regulation: true,
  created_at: '2026-10-01T10:00:37.190563', due_at: '2026-10-01T18:00:37',
  recurrence_next_at: '2026-11-20T10:00:37.190563', deadline_duration_minutes: 480,
  reminder_minutes_before: 60,
};

test('у повторяющейся в форме — срок ближайшего повтора, а не давний срок шаблона', () => {
  const form = toForm(REPEATING, 22);
  assert.equal(form.repeatFrom, '2026-11-20T10:00');
  assert.equal(form.dueAt, '2026-11-20T18:00');
  // Без срока у повторов — без срока и в форме.
  assert.equal(toForm({ ...REPEATING, deadline_duration_minutes: null }, 22).dueAt, '');
  // Повтор задачи (итерация) — обычная задача со своим сроком.
  const iteration = { ...FULL, is_regulation: true, regulation_parent_id: 7, due_at: '2026-11-19T18:00:00' };
  assert.equal(toForm(iteration, 22).repeatFrom, '');
  assert.equal(toForm(iteration, 22).dueAt, '2026-11-19T18:00');
});

test('новый срок повтора уходит смещением от его начала, без даты', () => {
  const payload = payloadOf({ ...toForm(REPEATING, 22), dueAt: '2026-11-21T12:30' });
  assert.equal('due_at' in payload, false);
  assert.deepEqual(
    [payload.deadline_days, payload.deadline_hours, payload.deadline_minutes],
    ['1', '2', '30'],
  );
  assert.equal(payload.reminder_minutes_before, 60);
  // Не трогали — ничего не уходит.
  const untouched = payloadOf({ ...toForm(REPEATING, 22), subject: 'тема' });
  ['due_at', ...DEADLINE_KEYS, 'reminder_minutes_before'].forEach((key) => assert.equal(key in untouched, false, key));
  // Сняли срок — нулевое смещение, сервер снимет срок у шаблона.
  const cleared = payloadOf({ ...toForm(REPEATING, 22), dueAt: '' });
  assert.deepEqual([cleared.deadline_days, cleared.deadline_hours, cleared.deadline_minutes], ['0', '0', '0']);
  assert.equal(cleared.reminder_minutes_before, 0);
});

test('сняли «Повтор» в той же правке — срок снова дата самой задачи', () => {
  const payload = payloadOf({ ...toForm(REPEATING, 22), isRegulation: false, dueAt: '2026-11-25T18:00' });
  assert.equal(payload.due_at, '2026-11-25T18:00');
  DEADLINE_KEYS.forEach((key) => assert.equal(key in payload, false, key));
});

test('срок повтора раньше его начала — ошибка формы с началом повтора', () => {
  const now = new Date(2026, 10, 19, 19, 40);
  const early = deadlineIssuesOf({ ...toForm(REPEATING, 22), dueAt: '2026-11-20T09:00' }, now);
  assert.deepEqual(early.map((item) => item.key), ['deadline']);
  assert.match(early[0].text, /повтора/);
  assert.match(early[0].text, /10:00/);
  assert.deepEqual(deadlineIssuesOf({ ...toForm(REPEATING, 22), dueAt: '2026-11-20T10:00' }, now).length, 1);
  assert.deepEqual(deadlineIssuesOf({ ...toForm(REPEATING, 22), dueAt: '2026-11-20T10:05' }, now), []);
});

/* ── Повтор уходит, только когда его меняли ──
   Прежняя форма слала повтор всегда. У повтора регламента (итерации) — задача
   «регламентная», но без своего периода — это уходило «ежедневно», и правка одной
   темы делала повтор вторым шаблоном; а быстрая правка закреплённой из слепка
   без повтора выключала повтор у шаблона. */
const RECURRENCE_KEYS = ['is_regulation', 'recurrence_type', 'recurrence_interval'];
const REPEAT_COPY = {
  ...FULL, is_regulation: true, recurrence_type: null, recurrence_interval: null,
  regulation_parent_id: 171, due_at: '2026-11-20T18:00:00',
};

test('правка повтора регламента (итерации) повтор не шлёт и в форме его не показывает', () => {
  const form = toForm(REPEAT_COPY, 22);
  assert.equal(form.regulationParentId, '171');
  const payload = payloadOf({ ...form, subject: 'тема повтора' });
  RECURRENCE_KEYS.forEach((key) => assert.equal(key in payload, false, key));
});

test('шаблон: без смены повтора — не шлётся, сменили период или шаг — уходит целиком', () => {
  const untouched = payloadOf({ ...toForm(REPEATING, 22), subject: 'тема шаблона' });
  RECURRENCE_KEYS.forEach((key) => assert.equal(key in untouched, false, key));
  const weekly = payloadOf({ ...toForm(REPEATING, 22), recurrenceType: 'weekly' });
  assert.deepEqual([weekly.is_regulation, weekly.recurrence_type, weekly.recurrence_interval], [true, 'weekly', '1']);
  const everyTwo = payloadOf({ ...toForm(REPEATING, 22), recurrenceInterval: '2' });
  assert.deepEqual([everyTwo.recurrence_type, everyTwo.recurrence_interval], ['daily', '2']);
  // Сняли «Повтор» — уходит «не регламентная».
  const off = payloadOf({ ...toForm(REPEATING, 22), isRegulation: false, recurrenceType: '' });
  assert.deepEqual([off.is_regulation, off.recurrence_type], [false, '']);
});

test('быстрая правка закреплённой из слепка повтор шаблона не выключает', () => {
  const payload = payloadOf({ ...toForm(SNAPSHOT, 22), subject: 'тема из слепка' });
  RECURRENCE_KEYS.forEach((key) => assert.equal(key in payload, false, key));
});

test('новая задача повтор шлёт всегда — и без повтора, и с ним', () => {
  const plain = payloadOf(emptyForm({ subject: 'н', assigneeIds: ['98'] }));
  assert.deepEqual([plain.is_regulation, plain.recurrence_type, plain.recurrence_interval], [false, '', '1']);
  const daily = payloadOf(emptyForm({ subject: 'н', assigneeIds: ['98'], isRegulation: true, recurrenceType: 'daily' }));
  assert.deepEqual([daily.is_regulation, daily.recurrence_type], [true, 'daily']);
  const fields = multipartOf(emptyForm({ subject: 'н', assigneeIds: ['98'] }));
  assert.deepEqual(fields.filter(([key]) => RECURRENCE_KEYS.includes(key)),
    [['is_regulation', '0'], ['recurrence_type', ''], ['recurrence_interval', '1']]);
});

/* ── Чек-лист (HR-2, 24.11.2026) ──
   Форма слала чек-лист при каждой правке, сервер пересоздавал пункты, и добавленный
   исполнитель или поправленное описание снимали все отметки, а у пунктов менялись id.
   Теперь чек-лист уходит, только когда его меняли, и с id пунктов, пришедших с сервера:
   по ним сервер узнаёт пункт и оставляет его отметку. */
const CHECKLIST_TASK = {
  ...FULL,
  checklist: [
    { id: 117, title: 'диспетчерская', position: 0, is_required: true, is_done: true, completed_by: 2 },
    { id: 118, title: 'бэкенд', position: 1, is_required: true, is_done: true, completed_by: 2 },
    { id: 119, title: 'корп почта', position: 2, is_required: true, is_done: false },
  ],
};

test('правка без смены чек-листа его не шлёт: ни тема, ни новый исполнитель', () => {
  const form = toForm(CHECKLIST_TASK, 22);
  assert.deepEqual(form.checklistItems, [
    { id: 117, title: 'диспетчерская', is_required: true },
    { id: 118, title: 'бэкенд', is_required: true },
    { id: 119, title: 'корп почта', is_required: true },
  ]);
  assert.equal('checklist_items' in payloadOf({ ...form, subject: 'Увольнение' }), false);
  assert.equal('checklist_items' in payloadOf({ ...form, assigneeIds: ['98', '99'] }), false);
  assert.equal('checklist_items' in payloadOf({ ...form, description: 'закрыть доступы' }), false);
});

test('правленный чек-лист уходит целиком, с id пунктов с сервера', () => {
  const form = toForm(CHECKLIST_TASK, 22);
  const renamed = payloadOf({
    ...form,
    checklistItems: form.checklistItems.map((item) => (item.id === 119 ? { ...item, title: 'корпоративная почта' } : item)),
  });
  assert.deepEqual(renamed.checklist_items, [
    { id: 117, title: 'диспетчерская', is_required: true },
    { id: 118, title: 'бэкенд', is_required: true },
    { id: 119, title: 'корпоративная почта', is_required: true },
  ]);
  const added = payloadOf({ ...form, checklistItems: [...form.checklistItems, { title: 'прокси', is_required: true }] });
  assert.deepEqual(added.checklist_items.map((item) => [item.id || null, item.title]),
    [[117, 'диспетчерская'], [118, 'бэкенд'], [119, 'корп почта'], [null, 'прокси']]);
  const optional = payloadOf({
    ...form,
    checklistItems: form.checklistItems.map((item) => (item.id === 118 ? { ...item, is_required: false } : item)),
  });
  assert.equal(optional.checklist_items[1].is_required, false);
  // Убрали все пункты — уходит пустой список: так чек-лист снимают.
  const cleared = payloadOf({ ...form, checklistItems: [{ title: '', is_required: true }] });
  assert.deepEqual(cleared.checklist_items, []);
});

test('пустая строка, добавленная и не заполненная, — не правка чек-листа', () => {
  const form = toForm(CHECKLIST_TASK, 22);
  const payload = payloadOf({ ...form, checklistItems: [...form.checklistItems, { title: '  ', is_required: true }] });
  assert.equal('checklist_items' in payload, false);
});

test('быстрая правка закреплённой из слепка без чек-листа его не стирает', () => {
  const unknown = toForm(SNAPSHOT, 22);
  assert.equal(unknown.initialChecklist, null);
  assert.equal('checklist_items' in payloadOf({ ...unknown, subject: 'тема из слепка' }), false);
  // Даже если в такой форме что-то вписали в чек-лист — не уходит: сервер принял бы
  // это за весь чек-лист и убрал бы пункты, которых слепок не знал.
  const typed = payloadOf({ ...unknown, checklistItems: [{ title: 'акт', is_required: true }] });
  assert.equal('checklist_items' in typed, false);
  // Слепок с чек-листом: добавленный пункт уходит вместе с нынешними, а не вместо них.
  const form = toForm({ ...SNAPSHOT, checklist: CHECKLIST_TASK.checklist }, 22);
  const added = payloadOf({ ...form, checklistItems: [...form.checklistItems, { title: 'акт', is_required: true }] });
  assert.deepEqual(added.checklist_items.map((item) => item.id || null), [117, 118, 119, null]);
  // Пустой чек-лист — известно, что пунктов нет: первый пункт уходит.
  const empty = toForm({ ...SNAPSHOT, checklist: [] }, 22);
  assert.equal(empty.initialChecklist, '[]');
  assert.deepEqual(payloadOf({ ...empty, checklistItems: [{ title: 'акт', is_required: true }] }).checklist_items,
    [{ title: 'акт', is_required: true }]);
});

test('новая задача чек-лист шлёт всегда — и в JSON, и формой раздела', () => {
  const plain = payloadOf(emptyForm({ subject: 'н', assigneeIds: ['98'] }));
  assert.deepEqual(plain.checklist_items, []);
  const withItems = payloadOf(emptyForm({
    subject: 'н', assigneeIds: ['98'], checklistItems: [{ title: 'а', is_required: true }, { title: 'б', is_required: false }],
  }));
  assert.deepEqual(withItems.checklist_items, [{ title: 'а', is_required: true }, { title: 'б', is_required: false }]);
  const fields = multipartOf(emptyForm({ subject: 'н', assigneeIds: ['98'], checklistItems: [{ title: 'а', is_required: true }] }));
  assert.deepEqual(fields.filter(([key]) => key === 'checklist_items'),
    [['checklist_items', JSON.stringify([{ title: 'а', is_required: true }])]]);
});
