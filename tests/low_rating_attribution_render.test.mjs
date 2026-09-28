/*
 * Разбор низкой оценки (задача #286): «Кому засчитать», история изменений и
 * лента, разделённая по менеджерам, — рендер настоящих компонентов через
 * react-dom/server (тот же приём, что в wiki_history_render).
 *
 * JSX здесь не используется намеренно: node --test гоняет .mjs без сборки.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');
const { mkdirSync } = require('node:fs');
const { join } = require('node:path');
const { fileURLToPath } = require('node:url');

async function load(relative, name) {
  // Собранный модуль — внутри проекта: из системной временной папки
  // `import 'react'` не разрешается.
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, `${name}.mjs`);
  buildSync({
    entryPoints: [fileURLToPath(new URL(relative, import.meta.url))],
    bundle: true,
    format: 'esm',
    target: 'node18',
    outfile,
    external: ['react', 'react-dom', 'axios', 'lucide-react'],
    loader: { '.jsx': 'jsx' },
    logLevel: 'silent',
  });
  return import(`file://${outfile.replace(/\\/g, '/')}`);
}

const attributionModule = await load('../src/components/c2d_eval/LowRatingAttribution.jsx', 'LowRatingAttribution');
const historyModule = await load('../src/components/c2d_eval/LowRatingHistory.jsx', 'LowRatingHistory');
const threadModule = await load('../src/components/c2d_eval/ChatThread.jsx', 'ChatThread');

const LowRatingAttribution = attributionModule.default;
const { participantMeta, mergeParticipants } = attributionModule;
const LowRatingHistory = historyModule.default;
const { historyLines, historyStamp } = historyModule;
const ChatThread = threadModule.default;

const render = (element) => renderToStaticMarkup(element);

const ASEL = { id: 190, name: 'Асель Тестбаева', first_at: '2026-09-25T16:09:36', last_at: '2026-09-25T16:31:06', replies: 3, is_default: true };
const DANIYAR = { id: 227, name: 'Данияр Примеров', first_at: '2026-09-25T16:09:47', last_at: '2026-09-25T16:09:47', replies: 1, is_default: false };

test('два менеджера в чате — у каждого отметка и отрезок, кто вёл', () => {
  const html = render(React.createElement(LowRatingAttribution, {
    participants: [ASEL, DANIYAR],
    attributed: [{ id: 190, name: 'Асель Тестбаева' }],
    value: [190],
    onChange: () => {},
    verdict: 'valid',
  }));
  assert.match(html, /Кому засчитать/);
  assert.match(html, /Асель Тестбаева/);
  assert.match(html, /Данияр Примеров/);
  assert.match(html, /16:09–16:31 · 3 ответа/);
  assert.match(html, /16:09 · 1 ответ/);
  // Отмечен тот, кому оценку отдал Chat2Desk, второй — нет.
  const checks = [...html.matchAll(/aria-checked="(true|false)"/g)].map((m) => m[1]);
  assert.deepEqual(checks, ['true', 'false']);
});

test('можно отметить обоих — отметки идут как есть', () => {
  const html = render(React.createElement(LowRatingAttribution, {
    participants: [ASEL, DANIYAR],
    value: [190, 227],
    onChange: () => {},
    verdict: 'valid',
  }));
  const checks = [...html.matchAll(/aria-checked="(true|false)"/g)].map((m) => m[1]);
  assert.deepEqual(checks, ['true', 'true']);
});

test('необоснованная не засчитывается никому — выбирать некого', () => {
  const html = render(React.createElement(LowRatingAttribution, {
    participants: [ASEL, DANIYAR],
    value: [190],
    onChange: () => {},
    verdict: 'invalid',
  }));
  assert.match(html, /не засчитывается никому/);
  assert.doesNotMatch(html, /role="checkbox"/);
});

test('один менеджер — просто его имя, без лишних отметок', () => {
  const html = render(React.createElement(LowRatingAttribution, {
    participants: [ASEL],
    attributed: [{ id: 190, name: 'Асель Тестбаева' }],
    value: [190],
    onChange: () => {},
    verdict: '',
  }));
  assert.match(html, /Засчитывается/);
  assert.match(html, /Асель Тестбаева/);
  assert.doesNotMatch(html, /role="checkbox"/);
});

const ADMIN_ACCOUNT = { id: null, name: 'Служебная учётка', first_at: '2026-09-25T16:10:00', last_at: '2026-09-25T16:10:00', replies: 1 };

test('учётку без сотрудника видно, но засчитать ей нельзя', () => {
  const html = render(React.createElement(LowRatingAttribution, {
    participants: [ASEL, DANIYAR, ADMIN_ACCOUNT],
    value: [190],
    onChange: () => {},
    verdict: 'valid',
  }));
  assert.match(html, /Служебная учётка/);
  assert.equal((html.match(/disabled=""/g) || []).length, 1);
});

test('менеджер чужого отдела виден, но выбрать его нельзя — с объяснением', () => {
  const html = render(React.createElement(LowRatingAttribution, {
    participants: [ASEL, DANIYAR, { id: 500, c2d_id: 41005, name: 'СВ другого отдела', first_at: '2026-09-25T16:20:00', last_at: '2026-09-25T16:20:00', replies: 1, selectable: false }],
    value: [190],
    onChange: () => {},
    verdict: 'valid',
  }));
  assert.match(html, /СВ другого отдела/);
  assert.match(html, /Менеджер другого отдела/);
  assert.equal((html.match(/disabled=""/g) || []).length, 1);
});

test('выбирать не из кого, кроме одного сотрудника, — отметок нет', () => {
  // Служебная учётка без сотрудника выбора не создаёт: засчитать ей нельзя.
  const html = render(React.createElement(LowRatingAttribution, {
    participants: [ASEL, ADMIN_ACCOUNT],
    value: [190],
    onChange: () => {},
    verdict: 'valid',
  }));
  assert.match(html, /Засчитывается/);
  assert.doesNotMatch(html, /role="checkbox"/);
});

test('засчитанный раньше, но не найденный в переписке остаётся в списке', () => {
  const merged = mergeParticipants([ASEL], [{ id: 190, name: 'Асель Тестбаева' }, { id: 5, name: 'Ранее засчитанный' }]);
  assert.deepEqual(merged.map((p) => p.id), [190, 5]);
});

test('подпись отрезка: одно сообщение, без ответов, нет времени', () => {
  assert.equal(participantMeta({ first_at: '2026-09-25T08:01:40', last_at: '2026-09-25T08:01:46', replies: 0 }), '08:01 · без ответов');
  assert.equal(participantMeta({ first_at: null, last_at: null, replies: 0 }), '');
  assert.equal(participantMeta({ first_at: '2026-09-25T10:00:00', last_at: '2026-09-25T11:30:00', replies: 5 }), '10:00–11:30 · 5 ответов');
});

test('история: вердикт, атрибуция «было/стало», комментарий и итог', () => {
  const lines = historyLines({
    action: 'review', status: 'valid', prev_status: 'invalid',
    operators: [{ id: 190, name: 'Асель' }, { id: 227, name: 'Данияр' }],
    prev_operators: [{ id: 190, name: 'Асель' }],
    comment: 'вели оба', final_status: 'valid', prev_final_status: null,
  }).map((line) => line.text);
  assert.deepEqual(lines, [
    'Обоснованно · было: Необоснованно',
    'Засчитана: Асель, Данияр · было: Асель',
    '«вели оба»',
    'Итог: Обоснованно',
  ]);
});

test('история: решение руководителя и снятый комментарий', () => {
  const lines = historyLines({
    action: 'final', status: 'invalid', prev_status: null, operators: null,
    comment: '', final_status: 'invalid', prev_final_status: null,
  }).map((line) => line.text);
  assert.deepEqual(lines, ['Итог руководителя: Необоснованно', 'Комментарий удалён']);
});

test('время истории показывается как пришло — уже по Алматы', () => {
  assert.equal(historyStamp('2026-09-28T09:05:12'), '28.09 09:05');
});

test('пустая история не рисуется вовсе', () => {
  const html = render(React.createElement(LowRatingHistory, { reviewId: 'x', count: 0, loadHistory: async () => [] }));
  assert.equal(html, '');
  const shown = render(React.createElement(LowRatingHistory, { reviewId: 'x', count: 3, loadHistory: async () => [] }));
  assert.match(shown, /История изменений/);
  assert.match(shown, />3</);
});

const SNAPSHOT = {
  operator_name: 'Асель Тестбаева',
  messages: [
    { id: 1, type: 'system', text: 'Чат передан от Айгерим Образцова к Асель Тестбаева. Причина — автоназначение чата системой.', created: '2026-09-25T16:09:39' },
    { id: 2, type: 'autoreply', text: 'Здравствуйте! Чем помочь?', created: '2026-09-25T16:09:40' },
    { id: 3, type: 'to_client', text: 'Добрый день', created: '2026-09-25T16:09:47', operatorId: 41002, author: 'Данияр Примеров' },
    { id: 4, type: 'from_client', text: 'Жду ответа', created: '2026-09-25T16:14:56', operatorId: 41001 },
    { id: 5, type: 'to_client', text: 'Смотрю', created: '2026-09-25T16:28:21', operatorId: 41001, author: 'Асель Тестбаева' },
    { id: 6, type: 'to_client', text: 'Готово', created: '2026-09-25T16:30:59', operatorId: 41001, author: 'Асель Тестбаева' },
    { id: 7, type: 'system', text: 'Чат закрыт. Инициатор — Асель Тестбаева.', created: '2026-09-25T16:31:06' },
  ],
};

test('лента по менеджерам: передача — разделителем, имя — при смене автора', () => {
  const html = render(React.createElement(ChatThread, { snapshot: SNAPSHOT, managerSegments: true }));
  // Имя над каждой первой репликой после смены автора: Данияр, затем Асель — один раз.
  assert.equal((html.match(/Данияр Примеров/g) || []).length, 1);
  const asel = (html.match(/Асель Тестбаева/g) || []).length;
  // Два раза в текстах системных сообщений + один раз подписью реплики.
  assert.equal(asel, 3);
  assert.match(html, /h-px/);
});

test('«Без автоответов» прячет автоответы, но не передачу чата', () => {
  const html = render(React.createElement(ChatThread, { snapshot: SNAPSHOT, managerSegments: true, hideService: true }));
  assert.match(html, /Чат передан от Айгерим/);
  assert.doesNotMatch(html, /Чем помочь/);
  assert.doesNotMatch(html, /Чат закрыт/);
});

test('в разборе реплика без известного автора не подписывается чужим именем', () => {
  // Старый снапшот, учётки дописать не удалось: подпись по оператору снапшота
  // назвала бы автором того, кому Chat2Desk отдал оценку.
  const snapshot = { operator_name: 'Асель Тестбаева', messages: [
    { id: 1, type: 'system', text: 'Чат передан от Асель Тестбаева к Данияр Примеров. Причина — обед.', created: '2026-09-25T12:00:00' },
    { id: 2, type: 'to_client', text: 'Здравствуйте', created: '2026-09-25T12:01:00' },
  ] };
  const reviewer = render(React.createElement(ChatThread, { snapshot, managerSegments: true }));
  assert.doesNotMatch(reviewer.replace(/Чат передан[^<]*/, ''), /Асель Тестбаева/);
  // В остальных местах подпись прежняя.
  const elsewhere = render(React.createElement(ChatThread, { snapshot }));
  assert.match(elsewhere, /Асель Тестбаева/);
});

test('без флага лента прежняя: подпись у каждой реплики, передача прячется', () => {
  const html = render(React.createElement(ChatThread, { snapshot: SNAPSHOT, hideService: true }));
  assert.doesNotMatch(html, /Чат передан/);
  // Подпись автора у каждой исходящей — как было до задачи.
  assert.equal((html.match(/Асель Тестбаева/g) || []).length, 2);
});
