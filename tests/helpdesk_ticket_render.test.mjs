// Заявка в IT в форме задачи (владелец 27.11.2026) — отрисовывается НАСТОЯЩИЙ
// TaskComposerForm из TasksView.jsx с HelpdeskTicketForm внутри
// (react-dom/server; модуль раздела собирается esbuild'ом, как в
// task_composer_payload.test.mjs). Что защищаем:
//   • доска IT Helpdesk в окне «Новая задача» превращает форму в заявку, а
//     правка задачи (без helpdesk) остаётся формой задачи;
//   • строки «Проект» и «Исполнители» на месте — по ним видно, куда уйдёт заявка;
//   • ответ ИИ показан блоками: советы с отметками, поля со звёздочкой,
//     схема кабинета, текст заявки без сырых тегов; устаревший текст — с
//     «Пересобрать» и строками «Детали»;
//   • заявка уходит задачей: только её поля, без скрытых полей формы задачи.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { build } = require('esbuild');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

// «useLayoutEffect на сервере ничего не делает» — про подсказки и списки, к форме
// не относится; без фильтра им забит весь вывод теста.
const printError = console.error;
console.error = (...args) => {
  if (String(args[0] || '').includes('useLayoutEffect does nothing on the server')) return;
  printError(...args);
};

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
const outfile = join(dir, 'tasks_TasksView_helpdesk.mjs');
await build({
  entryPoints: [fileURLToPath(new URL('../src/components/tasks/TasksView.jsx', import.meta.url))],
  bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
  packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' },
  logLevel: 'silent',
  footer: { js: 'export { TaskComposerForm as __Composer, buildEmptyTaskForm as __empty, appendTaskFormData as __multipart };' },
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
const { __Composer: Composer, __empty: emptyForm, __multipart: appendMultipart } = await import(`file://${outfile.replace(/\\/g, '/')}`);
const { ticketSignature } = await import('../src/components/tasks/helpdeskTicket.js');

const ME = 22;
const PEOPLE = [
  { id: 517, name: 'Лугинин С.', role: 'admin', is_executor: true, access_level: 'member' },
  { id: 2, name: 'Ядигаров Р.', role: 'super_admin', is_executor: true, access_level: 'member' },
  { id: ME, name: 'А Супервайзер', role: 'sv', is_executor: false, access_level: 'board' },
];
const PROJECTS = [
  { id: 4, code: 'ITH', name: 'IT Helpdesk', can_create: true, members: PEOPLE, sections: [] },
  { id: 1, code: 'HR', name: 'HR отдел', can_create: true, members: [], sections: [] },
];
const helpdesk = {
  apiBaseUrl: 'http://api.test',
  buildHeaders: () => ({}),
  notify: () => {},
  onTicketChange: () => {},
  onOpenSettings: () => {},
  settingsVersion: 0,
};

const render = (values, extra = {}) => renderToStaticMarkup(React.createElement(Composer, {
  values,
  onChange: () => {},
  recipients: [],
  currentUserId: ME,
  projects: PROJECTS,
  files: [],
  onFilesChange: () => {},
  ...extra,
}));

const textOf = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

test('доска IT Helpdesk в окне «Новая задача» — форма заявки', () => {
  const html = render(emptyForm({ projectId: '4', assignAll: true, assigneeIds: ['517', '2'] }), { helpdesk });
  assert.match(html, /class="tv-composer is-helpdesk"/);
  assert.match(html, /aria-label="Что случилось"/);
  assert.match(html, /Разобрать с ИИ/);
  // Без описания ИИ звать не с чем — кнопка неактивна.
  assert.match(html, /<button type="button" class="tv-btn tv-hd-ai-btn" disabled="">/);
  const text = textOf(html);
  assert.match(text, /Проект/);
  assert.match(text, /ITH · IT Helpdesk/);
  assert.match(text, /Исполнители/);
  for (const chip of ['Тема', 'Срочность', 'Файлы']) assert.match(text, new RegExp(`\\+ ${chip}`));
  // У заявки нет полей задачи: ни заголовка «Что нужно сделать?», ни «В бэклог», ни «Дедлайна».
  assert.doesNotMatch(html, /Что нужно сделать\?/);
  assert.doesNotMatch(text, /В бэклог/);
  assert.doesNotMatch(text, /Дедлайн/);
});

test('правка задачи на доске IT Helpdesk остаётся формой задачи', () => {
  const html = render(emptyForm({ projectId: '4', subject: 'Нет звука' }));
  assert.doesNotMatch(html, /is-helpdesk/);
  assert.match(html, /Что нужно сделать\?/);
});

test('другая доска — обычная задача и в окне «Новая задача»', () => {
  const html = render(emptyForm({ projectId: '1' }), { helpdesk });
  assert.doesNotMatch(html, /is-helpdesk/);
});

const AI_TICKET = {
  profile: 'szov',
  category: 'Телефония и Oktell',
  subcategory: 'Не работают наушники/гарнитура',
  note: 'Это про гарнитуру, а не про интернет',
  description: 'не слышно водителя на 27 месте',
  fields: [
    { key: 'workplace', label: 'Где (№ РМ)', type: 'workplace', required: true, placeholder: '', hint: '', options: [], value: '' },
    { key: 'when', label: 'Когда началось', type: 'text', required: false, placeholder: 'сегодня утром', hint: '', options: [], value: '' },
  ],
  values: { workplace: 'РМ 27 (Тех поддержка)', when: '' },
  checks: ['Переподключить гарнитуру в другой USB-порт', 'Выбрать гарнитуру устройством вывода'],
  done: [1],
  priority: 'urgent',
  aiPriority: 'high',
  title: 'Не работают наушники, РМ 27',
  titleEdited: false,
  text: '🔧 <b>Что:</b> не слышно водителя\n📍 <b>Где:</b> РМ 27',
  composed: true,
  composedSig: '',
};

test('ответ ИИ: тема, советы, поля со схемой и текст заявки', () => {
  const ticket = { ...AI_TICKET };
  ticket.composedSig = ticketSignature(ticket);
  const html = render(emptyForm({ projectId: '4', ticket }), { helpdesk });
  const text = textOf(html);
  assert.match(text, /Разобрать заново/);
  // Тема и срочность раскрыты сами: их поставил ИИ. Тема — списками в панели,
  // на капсуле её не повторяем (длинная строка вытолкнула бы соседей).
  // (Каталог при серверной отрисовке не загружен — списки показывают «Загрузка…».)
  assert.match(text, /× Тема × Срочность Срочная \+ Файлы/);
  assert.doesNotMatch(text, /Телефония и Oktell → /);
  assert.match(text, /Это про гарнитуру, а не про интернет/);
  assert.match(text, /× Срочность/);
  // Советы: отмеченный — aria-checked="true".
  assert.match(html, /role="checkbox" aria-checked="false" class="tv-hd-check"[^>]*>.*Переподключить гарнитуру/s);
  assert.match(html, /role="checkbox" aria-checked="true" class="tv-hd-check is-done"/);
  // Поля: обязательное со звёздочкой, РМ — на схеме кабинета, выбранное место нажато.
  assert.match(html, /Где \(№ РМ\)<\/span><span class="tv-hd-required"[^>]*>\*<\/span>/);
  assert.match(html, /class="tv-hd-seat is-selected"[^>]*aria-pressed="true" aria-label="РМ 27"/);
  assert.match(text, /РМ 27 \(Тех поддержка\)/);
  // Текст заявки — без сырых тегов: <b> отрисован, а не напечатан.
  assert.match(html, /<b>Что:<\/b> не слышно водителя/);
  assert.doesNotMatch(html, /&lt;b&gt;/);
  assert.match(html, /value="Не работают наушники, РМ 27"/);
  // Текст свежий — «Пересобрать» не предлагаем, «Детали» не дописываем.
  assert.doesNotMatch(text, /Пересобрать заявку/);
  assert.doesNotMatch(html, /tv-hd-preview-extra/);
});

test('дозаполнили после сборки — «Пересобрать» и строки «Детали»', () => {
  const ticket = { ...AI_TICKET, values: { ...AI_TICKET.values, when: 'с 9 утра' } };
  ticket.composedSig = ticketSignature(AI_TICKET);
  const html = render(emptyForm({ projectId: '4', ticket }), { helpdesk });
  const text = textOf(html);
  assert.match(text, /Пересобрать заявку/);
  assert.match(html, /class="tv-hd-preview-extra"/);
  assert.match(text, /Когда началось: с 9 утра/);
});

test('заявка уходит задачей доски: только её поля', () => {
  const sent = new Map();
  const body = { append: (key, value) => sent.set(key, value) };
  appendMultipart(body, emptyForm({
    subject: 'Не работают наушники, РМ 27',
    description: 'Тема: Телефония\n\n🔧 Что: не слышно',
    priority: 'urgent',
    assigneeIds: ['517', '2'],
    projectId: '4',
    sectionId: '',
  }));
  assert.equal(sent.get('subject'), 'Не работают наушники, РМ 27');
  assert.equal(sent.get('priority'), 'urgent');
  assert.equal(sent.get('project_id'), '4');
  assert.equal(sent.get('assignee_ids'), '517,2');
  assert.equal(sent.get('is_backlog'), '0');
  assert.equal(sent.get('tag'), 'task');
  assert.equal(sent.has('due_at'), false);
  assert.equal(sent.has('section_id'), false);
  assert.equal(sent.has('watcher_ids'), false);
});
