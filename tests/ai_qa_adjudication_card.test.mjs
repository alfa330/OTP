import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const outfile = join(cache, 'AdjudicationsRag.cjs');
buildSync({ entryPoints: ['src/components/call_qa/AdjudicationsRag.jsx'], outfile,
    bundle: true, platform: 'node', format: 'cjs', packages: 'external' });
const require = createRequire(import.meta.url);
const { AdjudicationCard } = require(outfile);
const base = { id: 'r1', criterion: 'Приветствие', direction: 'Поток',
    ai: 'Incorrect', correct: 'Correct', reason: 'Проверенное правило.',
    rule_status: 'draft', index_status: 'indexed', use_count: 0 };
const render = (item = {}, props = {}) => renderToStaticMarkup(
    React.createElement(AdjudicationCard, { item: { ...base, ...item }, ...props }));

test('indexed draft clearly says it is not applied; preparing an index is not approval', () => {
    const html = render();
    assert.match(html, /ИИ ещё не применяет это правило/);
    assert.doesNotMatch(html, /Готово к применению/);
    assert.match(html, /Оценка ИИ/);
    assert.match(html, /Решение проверяющего/);
});

test('Deficiency is shown as a partial deduction and missing verdict is not N\/A', () => {
    const html = render({ ai: null, correct: 'Deficiency' });
    assert.match(html, /Недочёт/);
    assert.match(html, /Не указано/);
    assert.doesNotMatch(html, /Не применимо/);
});

test('readers have no mutation controls and deleted rules cannot be deleted again', () => {
    const readonly = render();
    assert.doesNotMatch(readonly, /Удалить правило|>Проверить<|>Изменить</);
    const admin = render({}, { canManage: true });
    assert.match(admin, /Удалить правило/);
    assert.match(admin, /Проверить/);
    assert.doesNotMatch(render({ rule_status: 'deprecated' }, { canManage: true }), /Удалить правило/);
});

test('approved rule readiness depends on its index; does not claim actual production use', () => {
    assert.match(render({ rule_status: 'active' }), /Готово к применению в похожих ситуациях/);
    assert.match(render({ rule_status: 'active', index_status: 'error' }), /подготовка к поиску не завершена/);
    assert.doesNotMatch(render({ rule_status: 'active', index_status: 'error' }), /Готово к применению/);
    assert.match(render({ id: 'legacy:1', rule_status: 'active' }), /Исторический разбор · нужна проверка/);
});
