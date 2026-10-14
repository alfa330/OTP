/*
 * «Сформулировать с ИИ» — не только в очереди ревью (владелец 14.10.2026).
 *
 * Уже проверенную оценку (открытую из «Звонков», «Чатов», поиска) можно исправить
 * ещё раз: новое исправление уходит в базу разборов с блоком «Правило и цитата для
 * ИИ» и кнопкой «Сформулировать», а те, что уже там (ai_review.corrections), второй
 * раз не отправляются и помечены «правило уже в базе». Панель отрисовывается
 * настоящим компонентом через react-dom/server, как ai_qa_panels_render.test.mjs.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { isNewCorrection, validateCorrections } from '../src/components/call_qa/humanReview.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');

async function load(relative) {
    const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
    mkdirSync(dir, { recursive: true });
    const outfile = join(dir, `${relative.replace(/[\\/]/g, '_')}.rereview.mjs`);
    buildSync({
        entryPoints: [fileURLToPath(new URL(`../src/components/${relative}`, import.meta.url))],
        bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
        packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' },
        logLevel: 'silent',
    });
    const mod = await import(`file://${outfile.replace(/\\/g, '/')}`);
    return mod.default;
}

const CriteriaReviewPanel = await load('call_qa/CriteriaReviewPanel.jsx');

const criteria = [
    { idx: 0, criterion_id: 'c0', name: 'Отработка возражений', is_critical: false, weight: 30,
      source: 'transcript', ai: 'Incorrect', conf: 0.45, comment: 'Не предложил решение', evidence: '' },
    { idx: 1, criterion_id: 'c1', name: 'Приветствие', is_critical: false, weight: 10,
      source: 'transcript', ai: 'Correct', conf: 0.92, comment: '', evidence: '' },
];

const card = (aiReview, scores = ['Correct', 'Correct']) => ({
    id: 8989, subject_kind: 'imported_call', direction_id: 73, criteria,
    transcript: [{ speaker: 'operator', seg: [{ t: 'Здравствуйте, служба поддержки водителей.' }] }],
    my_review: { scores, criterion_comments: ['Отработал возражение', ''], comment: '',
                 counted_in_quality: false, comment_visible_to_operator: true },
    ai_review: aiReview, rag_now: { mode: 'active', auto_activation: true }, scale_changed: false,
});

const render = (call) => renderToStaticMarkup(React.createElement(CriteriaReviewPanel, {
    call, transcriptText: 'Здравствуйте, служба поддержки водителей.',
    onSave() {}, onSkip() {}, onRefine() {}, onInteractionChange() {},
}));

const KB_CHIP = 'Правило и цитата для ИИ';

test('новое исправление: «известное» — только тот же критерий с тем же вердиктом', () => {
    const known = new Map([[0, 'Correct']]);
    assert.equal(isNewCorrection(criteria[0], 'Correct', known), false);
    assert.equal(isNewCorrection(criteria[0], 'N/A', known), true, 'исправление исправления — новое');
    assert.equal(isNewCorrection(criteria[0], 'Correct', null), true);
    assert.equal(isNewCorrection(criteria[1], 'Correct', known), false, 'совпало с ИИ — не исправление');
    // Проверка причин — только у новых: старое исправление уже объяснено в базе.
    const check = validateCorrections(criteria, ['Correct', 'Correct'], ['', ''], {}, () => true, known);
    assert.deepEqual(check.reasonsRequired, []);
    const fresh = validateCorrections(criteria, ['Correct', 'Correct'], ['', ''], {}, () => true, null);
    assert.deepEqual(fresh.reasonsRequired, [0]);
});

test('проверенная оценка: у нового исправления есть «Правило и цитата для ИИ»', () => {
    const html = render(card({ outcome: 'confirmed', reviewed_at: '14.10.2026 09:00', reviewer: 'Проверяющий',
                               corrections: [] }));
    assert.ok(html.includes(KB_CHIP), 'чип для правила ИИ');
    assert.ok(html.includes('Исправлений: '), 'строка о том, что исправление уйдёт ИИ');
    assert.ok(!html.includes('правило уже в базе'));
});

test('исправление, которое уже в базе разборов, второй раз не предлагается', () => {
    const html = render(card({ outcome: 'adjudicated', reviewed_at: '14.10.2026 09:00', reviewer: 'Проверяющий',
                               corrections: [{ idx: 0, verdict: 'Correct' }] }));
    assert.ok(!html.includes(KB_CHIP));
    assert.ok(html.includes('правило уже в базе'));
    assert.ok(!html.includes('Исправлений: '), 'нечего отправлять ИИ');
});

test('непроверенная оценка — как раньше: чип у исправления и «Пропустить»', () => {
    const html = render(card(null));
    assert.ok(html.includes(KB_CHIP));
    assert.ok(html.includes('Пропустить'));
    const reviewed = render(card({ outcome: 'confirmed', reviewed_at: '', reviewer: '—', corrections: [] }));
    assert.ok(!reviewed.includes('Пропустить'), '«Пропустить» — только в очереди');
});
