// Поле дедлайна задачи (TaskDeadlineField → TaskDateTimeField → IosDatePicker)
// и секция «Дедлайн» окна задачи — исполняется НАСТОЯЩИЙ код компонентов.
//
// Оба компонента — функции без хуков, поэтому их можно вызвать напрямую и
// пройти по дереву элементов: нажать быструю кнопку (props.onClick) и посмотреть,
// что дошло до календаря (props.min). DOM для этого не нужен.
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
// Как в task_composer_payload: ленивый pdf.js раздела подменяется пустышкой.
const PDFJS_STUB = {
  name: 'stub-pdf-runtime',
  setup(stub) {
    stub.onResolve({ filter: /\/pdfRuntime$/ }, () => ({ path: 'pdfRuntime', namespace: 'stub' }));
    stub.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({
      contents: 'export const getDocument = () => { throw new Error("pdf.js в тесте не нужен"); }; export class TextLayer {}',
      loader: 'js',
    }));
  },
};
const bundle = async (entry, outName, footer) => {
  const outfile = join(dir, outName);
  await build({
    entryPoints: [fileURLToPath(new URL(entry, import.meta.url))],
    bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
    packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' },
    logLevel: 'silent', plugins: [PDFJS_STUB], ...(footer ? { footer: { js: footer } } : {}),
  });
  return import(`file://${outfile.replace(/\\/g, '/')}?t=${Date.now()}`);
};

const { default: TaskDeadlineField } = await bundle('../src/components/tasks/TaskDeadlineField.jsx', 'TaskDeadlineField_exec.mjs');
const { dayAfter } = await import('../src/components/tasks/taskDateTime.js');

// Обход дерева элементов: функции-компоненты из нашего кода раскрываем вызовом.
const walk = (node, visit, expand = new Set()) => {
  if (node == null || typeof node !== 'object') return;
  if (Array.isArray(node)) { node.forEach((child) => walk(child, visit, expand)); return; }
  visit(node);
  if (expand.has(node.type)) walk(node.type(node.props), visit, expand);
  walk(node.props?.children, visit, expand);
};
const fieldTree = (props) => TaskDeadlineField({ onChange: () => {}, ...props });
const presetButton = (tree, label) => {
  let found = null;
  walk(tree, (el) => { if (el.type === 'button' && el.props.children === label) found = el; });
  assert.ok(found, label);
  return found;
};

test('быстрая кнопка в поле меняет только день: выбранное время остаётся', () => {
  const seen = [];
  const tree = fieldTree({ value: '2030-05-05T10:30', onChange: (next) => seen.push(next) });
  presetButton(tree, 'Завтра').props.onClick();
  presetButton(tree, 'Неделя').props.onClick();
  assert.deepEqual(seen, [`${dayAfter(1)}T10:30`, `${dayAfter(7)}T10:30`]);
});

test('без срока быстрая кнопка ставит конец рабочего дня', () => {
  const seen = [];
  presetButton(fieldTree({ value: '', onChange: (next) => seen.push(next) }), 'Завтра').props.onClick();
  assert.deepEqual(seen, [`${dayAfter(1)}T18:00`]);
});

test('календарь срока гасит прошедшие дни: до календаря доходит min = сегодня', () => {
  const tree = fieldTree({ value: '' });
  // TaskDateTimeField — функция из бандла: находим её элемент и раскрываем вызовом.
  let dateTime = null;
  walk(tree, (el) => { if (typeof el.type === 'function' && 'minDate' in el.props) dateTime = el; });
  assert.ok(dateTime, 'TaskDateTimeField получил minDate');
  assert.equal(dateTime.props.minDate, dayAfter(0));
  let dayPicker = null;
  walk(dateTime.type(dateTime.props), (el) => {
    if (typeof el.props?.ariaLabel === 'string' && el.props.ariaLabel.endsWith(': день')) dayPicker = el;
  });
  assert.ok(dayPicker, 'календарь дня');
  assert.equal(dayPicker.props.min, dayAfter(0));
});

test('у повторяющейся задачи календарь начинается с дня ближайшего повтора, быстрых кнопок нет', () => {
  const later = dayAfter(2);
  const tree = fieldTree({ value: `${later}T18:00`, presets: false, fromDate: later });
  let buttons = 0;
  walk(tree, (el) => { if (el.type === 'button') buttons += 1; });
  assert.equal(buttons, 0, 'быстрые сроки считают от сегодня — у повтора их нет');
  let dateTime = null;
  walk(tree, (el) => { if (typeof el.type === 'function' && 'minDate' in el.props) dateTime = el; });
  assert.equal(dateTime.props.minDate, later);
  // День начала раньше сегодняшнего (повтор просрочен) — граница всё равно сегодня.
  let past = null;
  walk(fieldTree({ value: '', fromDate: '2020-01-01' }), (el) => {
    if (typeof el.type === 'function' && 'minDate' in el.props) past = el;
  });
  assert.equal(past.props.minDate, dayAfter(0));
});

test('подсвечена кнопка дня срока, и только она', () => {
  const tree = fieldTree({ value: `${dayAfter(3)}T12:00` });
  const pressed = ['Сегодня', 'Завтра', '3 дня', 'Неделя'].filter((label) => presetButton(tree, label).props['aria-pressed']);
  assert.deepEqual(pressed, ['3 дня']);
});

/* Секция «Дедлайн» окна задачи: крестик на чипе снимает срок целиком. */
const view = await bundle(
  '../src/components/tasks/TasksView.jsx', 'tasks_TasksView_sections.mjs',
  'export { COMPOSER_SECTIONS as __sections, buildTaskJsonPayload as __payload, taskToTaskForm as __toForm };',
);

test('у чипа «Дедлайн» нет подписи: день и время видны в открытом поле под ним', () => {
  const section = view.__sections.find((item) => item.id === 'deadline');
  assert.equal(section.summary, undefined);
});

test('у повтора регламента (итерации) в окне задачи нет раздела «Повтор»', () => {
  const section = view.__sections.find((item) => item.id === 'recurrence');
  assert.equal(section.hiddenForRepeatCopy, true);
  // Остальные разделы у повтора остаются.
  assert.deepEqual(view.__sections.filter((item) => item.hiddenForRepeatCopy).map((item) => item.id), ['recurrence']);
});

test('снять секцию «Дедлайн» — срок уходит null, напоминание 0', () => {
  const section = view.__sections.find((item) => item.id === 'deadline');
  const form = view.__toForm({
    id: 1, subject: 'т', status: 'assigned', tag: 'task', assigned_to: { id: 98 }, assignees: [{ id: 98 }],
    due_at: '2030-11-27T18:00:00', reminder_minutes_before: 1440,
  }, 22);
  assert.equal(section.hasValue(form), true);
  const cleared = { ...form, ...section.clear(form) };
  assert.equal(section.hasValue(cleared), false);
  const payload = view.__payload(cleared);
  assert.equal(payload.due_at, null);
  assert.equal(payload.reminder_minutes_before, 0);
});
