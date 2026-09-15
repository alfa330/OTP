// Куда встаёт лента помощника после смены сообщений.
//
// Жалоба 15.09.2026: ответ помощника появлялся, а лента уезжала к концу — к
// источникам, и сам ответ приходилось искать прокруткой вверх. Правило чистое и
// проверяется без браузера; прыжок к началу реплики исполняет useReplyScroll
// (src/components/assistant/assistantThread.jsx).
//
// Запуск: node --test tests/assistant_thread_scroll.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';

import { threadScrollIntent } from '../src/components/assistant/threadScroll.js';

const question = (id) => ({
    id, role: 'user', kind: 'question', text: 'Какой депозит в Алматы?', sources: [],
});
const answer = (id, extra = {}) => ({
    id, role: 'assistant', kind: 'answer', text: 'Депозит — 20 000 ₸.',
    sources: [{ slug: 'parks', title: 'Таксопарки Алматы' }], ...extra,
});

test('новый ответ встаёт к началу, а не к источникам', () => {
    const asked = [question('local-1')];
    assert.equal(threadScrollIntent(asked, [...asked, answer(7)]), 'reply');
});

test('свой вопрос — к концу: под ним появится «Читаю доступные вам статьи…»', () => {
    const thread = [question(1), answer(2)];
    assert.equal(threadScrollIntent(thread, [...thread, question('local-3')]), 'end');
    assert.equal(threadScrollIntent([], [question('local-1')]), 'end');
    assert.equal(threadScrollIntent(null, [question('local-1')]), 'end');
});

test('ответ супервайзера встаёт так же, как ответ помощника', () => {
    const thread = [question(1), answer(2, { kind: 'no_answer', escalation: { status: 'open' } })];
    const reread = [...thread, { id: 3, role: 'assistant', kind: 'supervisor', text: 'Депозит 15 000 ₸.' }];
    assert.equal(threadScrollIntent(thread, reread), 'reply');
});

test('оценка, передача супервайзеру и перечитка ленту не двигают', () => {
    const thread = [question('local-1'), answer(2)];
    const voted = thread.map((m) => (m.id === 2 ? { ...m, feedback: 1 } : m));
    assert.equal(threadScrollIntent(thread, voted), null);
    const escalated = thread.map((m) => (m.id === 2 ? { ...m, escalation: { status: 'open' } } : m));
    assert.equal(threadScrollIntent(thread, escalated), null);
    // Перечитка с сервера: локальный id вопроса сменился на настоящий.
    assert.equal(threadScrollIntent(thread, [question(1), answer(2)]), null);
});

test('снятый упавший вопрос не уводит ленту к прошлому ответу', () => {
    const before = [question(1), answer(2)];
    const asked = [...before, question('local-3')];
    assert.equal(threadScrollIntent(asked, before), null);
});

test('открытый разговор — к концу; пока грузится или пуст — никуда', () => {
    const thread = [question(1), answer(2)];
    assert.equal(threadScrollIntent(thread, null), null);
    assert.equal(threadScrollIntent(null, thread), 'end');
    assert.equal(threadScrollIntent(null, []), null);
    assert.equal(threadScrollIntent(thread, []), null);   // «Новый вопрос»
});
