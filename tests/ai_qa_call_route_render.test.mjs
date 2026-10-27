/*
 * Маршрут звонка в шапке карточки «ИИ-оценки» (call.call_route) рисуется настоящим
 * компонентом через react-dom/server — так же, как ai_qa_call_card_render.test.mjs.
 *
 * Стережём, что карточка говорит, кто кому звонил: входящий или исходящий, номер
 * клиента в привычном виде и наш номер с подписью по направлению. Чего телефония не
 * сообщила (у исходящих СЗоВ нашего номера нет), того нет и на экране, а у чата
 * строки маршрута нет вовсе — там свой контакт.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');

async function load(relative) {
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, `${relative.replace(/[\\/]/g, '_')}.route.mjs`);
  buildSync({
    entryPoints: [fileURLToPath(new URL(`../src/components/${relative}`, import.meta.url))],
    bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
    packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' },
    logLevel: 'silent',
  });
  const mod = await import(`file://${outfile.replace(/\\/g, '/')}`);
  return mod.default;
}

const CallReviewCard = await load('call_qa/CallReviewCard.jsx');

const card = (extra = {}) => renderToStaticMarkup(React.createElement(CallReviewCard, {
  call: {
    id: 12090, subject_kind: 'imported_call', operator: 'Оператор Тестовый', direction: 'Поток',
    datetime: '25.10.2026, 14:03', languages: { ru: 100 }, asr_mean_conf: 0.95,
    criteria: [], ai_score: 90,
    transcript: [{ spk: '1', speaker: 'operator', start_ms: 0, seg: [{ t: 'Здравствуйте!' }] }],
    ...extra,
  },
  onSave() {}, onSkip() {},
}));

const routeRow = (html) => {
  const match = html.match(/<div[^>]*data-qa="call-route"[^>]*>([\s\S]*?)<\/div>/);
  return match ? match[0] : null;
};

test('входящий: номер клиента и номер, который он набрал', () => {
  const row = routeRow(card({ call_route: { direction: 'in', client: '7012345678', line: '7272350000' } }));
  assert.ok(row, 'строка маршрута есть');
  assert.match(row, /Входящий/);
  assert.match(row, /Клиент/);
  assert.match(row, /\+7 701 234 56 78/);
  assert.match(row, /На номер/);
  assert.match(row, /\+7 727 235 00 00/);
  assert.match(row, /title="Номер, который набрал клиент"/);
  assert.match(row, /aria-label="Скопировать номер клиента"/);
});

test('исходящий: наш номер подписан «С номера»', () => {
  const row = routeRow(card({ call_route: { direction: 'out', client: '7012345678', line: '7272350000' } }));
  assert.match(row, /Исходящий/);
  assert.match(row, /С номера/);
  assert.match(row, /title="Номер, с которого позвонили клиенту"/);
  assert.doesNotMatch(row, /На номер/);
});

test('чего телефония не сообщила, того на экране нет', () => {
  const row = routeRow(card({ call_route: { direction: 'out', client: '7012345678', line: null } }));
  assert.doesNotMatch(row, /С номера|На номер|Наш номер/);
  const noDirection = routeRow(card({ call_route: { direction: null, client: '7012345678', line: null } }));
  assert.doesNotMatch(noDirection, /Входящий|Исходящий/);
  assert.match(noDirection, /\+7 701 234 56 78/);
});

test('без маршрута и у чата строки нет', () => {
  assert.equal(routeRow(card({ call_route: null })), null);
  assert.equal(routeRow(card({})), null);
  assert.equal(routeRow(card({
    subject_kind: 'wz_episode', chat: { contact_name: 'Водитель' },
    call_route: { direction: 'in', client: '7012345678', line: null },
  })), null);
});
