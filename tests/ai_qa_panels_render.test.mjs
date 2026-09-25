/*
 * Панели «ИИ-оценки», которые трогал «Маркетинговый мониторинг» (ТЗ #317),
 * отрисовываются настоящими компонентами через react-dom/server — так же, как
 * custom_select_render.test.mjs.
 *
 * Зачем: ошибка области видимости (переменная из соседнего компонента)
 * проходит сборку и падает только при отрисовке. Так и было — сброс страницы
 * «Базы разборов» по отбору сделки попал в панель раскатки RAG, которую видит
 * лишь супер-админ, и вкладка у него падала ReferenceError. Эффекты при
 * серверной отрисовке не выполняются, но тело компонента — выполняется, и
 * именно там такая ошибка и живёт.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');

/* Собираем компонент вместе с его локальными импортами; пакеты из
   node_modules остаются внешними. Результат — внутри проекта, чтобы node
   нашёл react (см. custom_select_render.test.mjs). */
async function load(relative) {
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, `${relative.replace(/[\\/]/g, '_')}.mjs`);
  buildSync({
    entryPoints: [new URL(`../src/components/${relative}`, import.meta.url).pathname],
    bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
    packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' },
    logLevel: 'silent',
  });
  const mod = await import(`file://${outfile.replace(/\\/g, '/')}`);
  return mod.default;
}

const AdjudicationsRag = await load('call_qa/AdjudicationsRag.jsx');
const QaFilters = await load('call_qa/QaFilters.jsx');
const QaDashboard = await load('call_qa/QaDashboard.jsx');
const { EMPTY_FILTERS } = await import('../src/components/call_qa/filters.js');

const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));

test('«База разборов» рисуется и у супер-админа (с панелью раскатки), и у остальных', () => {
  const filters = { ...EMPTY_FILTERS, parks: ['itaxi'] };
  for (const canManage of [true, false]) {
    const html = render(AdjudicationsRag, {
      apiBaseUrl: 'http://stub', canManage, department: 'op', filters, showToast() {},
    });
    assert.ok(html.length > 0, `canManage=${canManage}`);
  }
});

test('панель фильтров: обычный режим и режим «только сделка»', () => {
  const base = { filters: EMPTY_FILTERS, onChange() {}, apiBaseUrl: 'http://stub', department: 'op' };
  assert.ok(render(QaFilters, base).includes('Фильтры'));
  // Справочник сделки ещё не пришёл — в «Базе разборов» панели нет вовсе.
  assert.equal(render(QaFilters, { ...base, marketingOnly: true }), '');
});

test('«Обзор» принимает отбор панели', () => {
  const html = render(QaDashboard, { apiBaseUrl: 'http://stub', department: 'op',
                                     filters: { ...EMPTY_FILTERS, channels: ['youtube'] } });
  assert.ok(html.includes('Загрузка статистики'));
});
