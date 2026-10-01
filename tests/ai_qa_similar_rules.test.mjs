import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

/* «Такой разбор уже был?» под исправленным критерием и итог сохранения разбора.
 * Сервер — call_qa/api.py · adjudication_similar / save_adjudications. */

const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const require = createRequire(import.meta.url);
const build = (entry, name) => {
    const outfile = join(cache, name);
    buildSync({ entryPoints: [entry], outfile, bundle: true, platform: 'node',
        format: 'cjs', packages: 'external' });
    return require(outfile);
};
const { SimilarRulesView, runNote, headline, adjudicationSummary } = build(
    'src/components/call_qa/SimilarRules.jsx', 'SimilarRules.cjs');
const { AdjudicationCard } = build(
    'src/components/call_qa/AdjudicationsRag.jsx', 'AdjudicationsRagSimilar.cjs');

const rule = (extra = {}) => ({
    rule_id: 'r1', rule_status: 'active', rule_text: 'Оператор не обязан уточнять, удобно ли говорить',
    correct_verdict: 'Correct', score: 0.97, verdict: 'duplicate', same_verdict: true,
    run: { code: 'included', similarity: 0.81, threshold: 0.68 }, ...extra,
});
const render = (data, fresh) => renderToStaticMarkup(
    React.createElement(SimilarRulesView, { data, fresh }));

test('nothing found renders nothing — no "похожих нет" noise under every correction', () => {
    assert.equal(render(null), '');
    assert.equal(render({ items: [] }), '');
});

test('a rule the model had in its prompt and still got wrong is called out', () => {
    const html = render({ items: [rule()], duplicate_of: 'r1' });
    assert.match(html, /Такой разбор уже есть/);
    assert.match(html, /ИИ получил это правило в этой оценке — и всё равно ошибся/);
    assert.match(html, /Действует/);
    assert.match(html, /дубль 97%/);
    assert.match(html, /Новое правило не создастся/);
    assert.match(html, /Сюда добавится разбор/);           // цель привязки отмечена
});

test('answer to an older text or verdict never promises the link', () => {
    // Пока человек правит текст или вердикт, ответ на прежний запрос не говорит
    // «новое правило не создастся»: сохранение может решить уже иначе.
    const html = render({ items: [rule()], duplicate_of: 'r1' }, false);
    assert.match(html, /Такой разбор уже есть/);
    assert.doesNotMatch(html, /Новое правило не создастся|Сюда добавится разбор/);
});

test('rules that never reached the model explain why', () => {
    assert.match(runNote(rule({ run: { code: 'below_threshold', similarity: 0.61, threshold: 0.68 } })).text,
        /сходство с разговором 0,61 ниже порога 0,68/);
    // Почти у порога: «0,68 ниже порога 0,68» было бы противоречием.
    assert.match(runNote(rule({ run: { code: 'below_threshold', similarity: 0.6799, threshold: 0.68 } })).text,
        /сходство с разговором 0,67 ниже порога 0,68/);
    assert.match(runNote(rule({ run: { code: 'below_threshold', similarity: 0.6749, threshold: 0.675 } })).text,
        /0,674 ниже порога 0,675/);
    assert.match(runNote(rule({ run: { code: 'not_retrieved' } })).text, /другие правила критерия ближе/);
    assert.doesNotMatch(runNote(rule({ run: { code: 'not_retrieved' } })).text, /далеко/);
    assert.match(runNote(rule({ run: { code: 'rag_off' } })).text, /без базы разборов/);
    assert.match(runNote(rule({ run: { code: 'retrieval_failed' } })).text, /не сработал/);
    assert.match(runNote(rule({ run: { code: 'top_k' } })).text, /три самых близких/);
    assert.match(runNote(rule({ run: { code: 'missing_from_snapshot' } })).text,
        /Действовало, но в базу знаний этой оценки не попало/);
    assert.match(runNote(rule({ run: { code: 'inactive_at_run' } })).text, /Включено после этой оценки/);
    assert.match(runNote(rule({ rule_status: 'draft', run: { code: 'inactive_at_run' } })).text,
        /Черновик — ИИ его не получает/);
    assert.match(runNote(rule({ rule_status: 'deprecated', run: { code: 'inactive_at_run' } })).text,
        /удалено/);
    // Получил, но правило о другом вердикте — это не «всё равно ошибся».
    assert.doesNotMatch(runNote(rule({ same_verdict: false })).text, /всё равно ошибся/);
});

test('headline is the strongest finding; conflicting verdict is visible', () => {
    assert.equal(headline([rule({ verdict: 'similar' })]), 'Похожий разбор уже есть');
    assert.equal(headline([rule({ verdict: 'in_prompt', score: null })]),
        'ИИ получал правила по этому критерию');
    const html = render({ items: [rule({ verdict: 'in_prompt', score: null, same_verdict: false })] });
    assert.match(html, /там другой вердикт/);
    assert.doesNotMatch(html, /Новое правило не создастся/);
    assert.match(render({ items: [rule()], degraded: true }), /сравнили только по словам/);
});

test('save toast says the corrections go straight into evaluation', () => {
    assert.equal(adjudicationSummary(2, { activated: 2, rag_mode: 'active' }),
        'Исправлений: 2 — ИИ учтёт их в следующих оценках');
    assert.match(adjudicationSummary(2, { linked: 1, pending_index: 1, rag_mode: 'active',
        rules: [{ status: 'draft', ready: false }, { status: 'linked', ready: true }] }),
    /ИИ учтёт их в следующих оценках · повтор правила, которое ИИ уже получал: 1 · включится после подготовки к поиску: 1/);
    assert.doesNotMatch(adjudicationSummary(1, null), /черновик|ждёт/);
});

test('save toast reports what was actually switched on, not a blanket promise', () => {
    // Включение не удалось — правило осталось черновиком: «ИИ учтёт» было бы неправдой.
    const failed = adjudicationSummary(2, { activated: 1, rag_mode: 'active',
        rules: [{ status: 'active', ready: true }, { status: 'draft', ready: true }] });
    assert.equal(failed, 'Исправлений: 2 сохранено · уже в оценке: 1 · ждёт включения в «Базе разборов»: 1');
    // Направление на контрольной проверке: правила включает администратор.
    const held = adjudicationSummary(1, { activated: 0, pending_index: 1, rag_mode: 'active',
        auto_activation: false, rules: [{ status: 'draft', ready: false }] });
    assert.equal(held, 'Исправлений: 1 сохранено · ждёт включения в «Базе разборов»: 1');
});

test('save toast does not promise evaluation when the direction runs without rules', () => {
    for (const mode of ['shadow', 'off']) {
        const text = adjudicationSummary(1, { activated: 1, rag_mode: mode });
        assert.doesNotMatch(text, /ИИ учтёт/);
        assert.match(text, /без базы разборов/);
    }
});

test('catalog card shows corrections after the rule reached the model and repeats', () => {
    const card = (item) => renderToStaticMarkup(React.createElement(AdjudicationCard, {
        item: { id: 'r1', criterion: 'Приветствие', direction: 'Поток', ai: 'Incorrect',
            correct: 'Correct', reason: 'Правило.', rule_status: 'active',
            index_status: 'indexed', use_count: 7, ...item } }));
    assert.doesNotMatch(card({}), /исправляли после этого|повторов разбора/);
    const html = card({ corrected_after_count: 3, repeat_count: 2 });
    assert.match(html, /Передавалось ИИ: 7/);
    assert.match(html, /ИИ исправляли после этого: 3/);
    assert.match(html, /повторов разбора: 2/);
});
