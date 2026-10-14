import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    EXCLUSION_REASON_MIN, EXCLUSION_REASON_MAX, CATEGORY_CHIPS, exclusionReady, isOtherCategory,
    visibleCategories, exclusionLabel, exclusionAuthor,
} from '../src/components/call_qa/exclusionRules.js';
import { applyReviewed } from '../src/components/call_qa/queueDayRules.js';

/**
 * «Исключить оценку»: когда окно пускает исключение, какие категории видны
 * сразу, подписи причины — и как сводка дня очереди меняется на месте, когда
 * разговор исключили (он уходит из дня совсем, его баллы — из средних).
 */

const py = readFileSync(new URL('../call_qa/exclusions.py', import.meta.url), 'utf8');

test('пороги причины — те же, что у сервера', () => {
    assert.equal(EXCLUSION_REASON_MIN, Number(py.match(/^REASON_MIN = (\d+)/m)[1]));
    assert.equal(EXCLUSION_REASON_MAX, Number(py.match(/^REASON_MAX = (\d+)/m)[1]));
    const titles = [...py.match(/^OTHER_TITLES = \(([^)]*)\)/m)[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
    assert.ok(titles.length >= 3);
    titles.forEach((title) => assert.ok(isOtherCategory(title), title));
});

test('причина обязательна: категория или свои слова', () => {
    assert.equal(exclusionReady({}), false);
    assert.equal(exclusionReady({ reason: '  ок ' }), false);
    assert.equal(exclusionReady({ reason: 'ошибся номером' }), true);
    assert.equal(exclusionReady({ categoryId: 7, categoryTitle: 'Ошибся номером' }), true);
    // id категории 0 — тоже выбор (null/undefined — нет).
    assert.equal(exclusionReady({ categoryId: 0, categoryTitle: 'Спам' }), true);
});

test('у «Другое» свои слова обязательны', () => {
    assert.equal(exclusionReady({ categoryId: 3, categoryTitle: 'Другое' }), false);
    assert.equal(exclusionReady({ categoryId: 3, categoryTitle: 'Другое', reason: 'ок' }), false);
    assert.equal(exclusionReady({ categoryId: 3, categoryTitle: ' другое. ', reason: 'звонил коллега' }), true);
    assert.equal(isOtherCategory('Прочее'), true);
    assert.equal(isOtherCategory('Другой отдел'), false);
});

const cats = (n) => Array.from({ length: n }, (_, i) => ({ id: i + 1, title: `Категория ${i + 1}` }));

test('сразу видно не больше CATEGORY_CHIPS категорий, остальные — за «Ещё N»', () => {
    assert.deepEqual(visibleCategories(cats(3)), { shown: cats(3), hidden: 0 });
    const many = cats(CATEGORY_CHIPS + 4);
    const { shown, hidden } = visibleCategories(many);
    assert.equal(shown.length, CATEGORY_CHIPS);
    assert.equal(hidden, 4);
    assert.deepEqual(visibleCategories(many, { expanded: true }), { shown: many, hidden: 0 });
    assert.deepEqual(visibleCategories(null), { shown: [], hidden: 0 });
});

test('выбранная категория видна, даже если она за «Ещё»', () => {
    const many = cats(CATEGORY_CHIPS + 4);
    const last = many[many.length - 1];
    const { shown, hidden } = visibleCategories(many, { selectedId: last.id });
    assert.equal(shown.length, CATEGORY_CHIPS);
    assert.equal(shown[shown.length - 1].id, last.id);
    assert.equal(hidden, many.length - CATEGORY_CHIPS);
    // Выбранная и так в первых — список не меняется.
    assert.deepEqual(visibleCategories(many, { selectedId: 2 }).shown, many.slice(0, CATEGORY_CHIPS));
});

test('подпись причины и автора', () => {
    assert.equal(exclusionLabel({ category: 'Ошибся номером', reason: ' звонил в такси ' }),
        'Ошибся номером · звонил в такси');
    assert.equal(exclusionLabel({ category: null, reason: 'тест' }), 'тест');
    assert.equal(exclusionLabel({ category: 'Спам', reason: '' }), 'Спам');
    assert.equal(exclusionLabel(null), '');
    assert.equal(exclusionAuthor({ excluded_by: 'Иванов Иван', excluded_at: '14.10.2026 12:30' }),
        'Иванов Иван, 14.10.2026 12:30');
    assert.equal(exclusionAuthor({ excluded_at: '14.10.2026 12:30' }), '14.10.2026 12:30');
});

const DAY = {
    day: '2026-10-13', open: 33, critical: 2, reasons: { critical: 2, lowconf: 30, pending: 33 },
    evaluated: 120, reviewed: 87, corrected: 3, ai_avg: 92.1, ai_n: 120, human_avg: 80, human_n: 10,
};

test('исключённый разговор уходит из дня очереди совсем — как его не посчитает сервер', () => {
    const [day] = applyReviewed([DAY], [
        { key: 'imported_call-1', day: DAY.day, reasons: ['critical', 'pending'], excluded: true, ai: 0, humanRemoved: null },
    ]);
    assert.equal(day.open, 32);
    assert.equal(day.evaluated, 119);
    assert.equal(day.reviewed, 87, 'исключённый не становится проверенным');
    assert.equal(day.corrected, 3);
    assert.equal(day.critical, 1);
    assert.deepEqual(day.reasons, { critical: 1, lowconf: 30, pending: 32 });
    // Балл ИИ ноль выходит из среднего: (92,1 × 120 − 0) / 119.
    assert.equal(day.ai_n, 119);
    assert.equal(day.ai_avg, Math.round(((92.1 * 120) / 119) * 10) / 10);
    assert.equal(day.human_avg, 80);
    assert.equal(day.human_n, 10);
});

test('исключили разговор с баллом человека — он выходит и из среднего человека', () => {
    const [day] = applyReviewed([DAY], [
        { key: 'call-2', day: DAY.day, reasons: ['lowconf'], excluded: true, ai: 50, humanRemoved: 30 },
    ]);
    assert.equal(day.human_n, 9);
    assert.equal(day.human_avg, Math.round(((80 * 10 - 30) / 9) * 10) / 10);
    assert.equal(day.ai_avg, Math.round(((92.1 * 120 - 50) / 119) * 10) / 10);
});

test('проверили один и исключили другой в том же дне — каждое по своим правилам', () => {
    const [day] = applyReviewed([DAY], [
        { key: 'call-3', day: DAY.day, reasons: ['lowconf'], corrected: true, human: 100 },
        { key: 'call-4', day: DAY.day, reasons: ['lowconf'], excluded: true, ai: 90 },
    ]);
    assert.equal(day.open, 31);
    assert.equal(day.evaluated, 119);
    assert.equal(day.reviewed, 88);
    assert.equal(day.corrected, 4);
    assert.equal(day.human_n, 11);
    assert.equal(day.human_avg, Math.round(((80 * 10 + 100) / 11) * 10) / 10);
});

test('без ai_n средний ИИ не выдумывается, а последний исключённый обнуляет его', () => {
    const { ai_n: _drop, ...noCount } = DAY;
    const [kept] = applyReviewed([noCount], [{ key: 'call-5', day: DAY.day, reasons: [], excluded: true, ai: 10 }]);
    assert.equal(kept.ai_avg, 92.1);
    assert.equal('ai_n' in kept, false);
    const single = { ...DAY, open: 1, evaluated: 1, reviewed: 0, ai_avg: 40, ai_n: 1, reasons: { lowconf: 1 } };
    const [empty] = applyReviewed([single], [{ key: 'call-6', day: DAY.day, reasons: ['lowconf'], excluded: true, ai: 40 }]);
    assert.equal(empty.open, 0);
    assert.equal(empty.evaluated, 0);
    assert.equal(empty.ai_avg, null);
    assert.equal(empty.ai_n, 0);
});

test('файлы окна и экрана подключены: кнопка, плашка, окно и «Исключённые»', () => {
    const view = readFileSync(new URL('../src/components/call_qa/CallQaView.jsx', import.meta.url), 'utf8');
    assert.match(view, /<ExcludeModal open=\{excludeOpen && Boolean\(callData\)\}/);
    assert.match(view, /callData\.can_exclude !== false && !callData\.exclusion/);
    assert.match(view, /Оценка исключена/);
    assert.match(view, /api\/ai-qa\/exclusion`/);
    assert.match(view, /excluded: true/);
    const list = readFileSync(new URL('../src/components/call_qa/EvaluationsList.jsx', import.meta.url), 'utf8');
    assert.match(list, /list\?\.excludedOpen/);
    assert.match(list, /list\.openExcluded/);
    const hook = readFileSync(new URL('../src/components/call_qa/useDayList.js', import.meta.url), 'utf8');
    assert.match(hook, /setExcluded\(Number\(r\.data\?\.excluded\) \|\| 0\)/);
});
