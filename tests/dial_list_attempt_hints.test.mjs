/*
 * Раздел «Удаленный КЦ», история водителя: приписка «сброшен автоматически» под попыткой.
 *
 * Трубку кладёт сам телефон оператора: на гудках (владелец, 11.10.2026) и — с телефона
 * 3.22.34 — на сообщении сети «абонент недоступен» (владелец, 12.10.2026). Причину отдаёт
 * сервер (dial_list/service.py, lead_card): ключ auto_hangup и прежний флаг ring_timeout.
 * Логика приписки — в src/components/dial_list/attemptHints.js (без React).
 *
 * Что защищаем:
 *   • сообщение сети подписано своими словами, а не «водитель не взял трубку»;
 *   • разговор и отменённая попытка приписки не получают — про них она неправда;
 *   • сервер постарше (ключа auto_hangup нет) и сборка сайта постарше (ключа не знает)
 *     вместе с новой стороной показывают то же, что раньше;
 *   • журнал берёт приписку из этого модуля, своей копии правила в нём нет.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { ANNOUNCEMENT_HINT, RING_TIMEOUT_HINT, autoHangupHint } from '../src/components/dial_list/attemptHints.js';

// Без переводов строк Windows: сверки исходника ниже многострочные.
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8').split('\r\n').join('\n');

/* Попытка из GET /api/dial_list/leads/<id> (attempts[]) — только то, что читает приписка. */
const attempt = (result, extra = {}) => ({ id: 'att-1', result, ring_timeout: false, auto_hangup: '', ...extra });

test('сообщение сети подписано своими словами', () => {
    assert.equal(ANNOUNCEMENT_HINT, ' · сброшен автоматически: сообщение сети «абонент недоступен»');
    // Сервер у сообщения сети флаг ring_timeout не ставит — причину несёт auto_hangup.
    assert.equal(autoHangupHint(attempt('busy', { auto_hangup: 'announcement' })), ANNOUNCEMENT_HINT);
    // Binotel ответил ANSWER (автоответчик) — сервер всё равно отдаёт недозвон.
    assert.equal(autoHangupHint(attempt('no_answer', { auto_hangup: 'announcement' })), ANNOUNCEMENT_HINT);
    assert.equal(autoHangupHint(attempt('other', { auto_hangup: 'announcement' })), ANNOUNCEMENT_HINT);
    // Даже если флаг гудков почему-то тоже пришёл — сообщение сети сильнее.
    assert.equal(autoHangupHint(attempt('no_answer', { auto_hangup: 'announcement', ring_timeout: true })),
        ANNOUNCEMENT_HINT);
});

test('сброс на гудках — как раньше', () => {
    assert.equal(RING_TIMEOUT_HINT, ' · сброшен автоматически: водитель не взял трубку');
    assert.equal(autoHangupHint(attempt('no_answer', { ring_timeout: true, auto_hangup: 'ring_timeout' })),
        RING_TIMEOUT_HINT);
    // Сервер постарше ключа auto_hangup не присылает — решает один флаг.
    assert.equal(autoHangupHint({ result: 'no_answer', ring_timeout: true }), RING_TIMEOUT_HINT);
    assert.equal(autoHangupHint({ result: 'busy', ring_timeout: true }), RING_TIMEOUT_HINT);
});

test('разговор и отменённая попытка приписки не получают', () => {
    for (const result of ['answered', 'cancelled']) {
        for (const extra of [{ ring_timeout: true }, { auto_hangup: 'ring_timeout' }, { auto_hangup: 'announcement' },
            { ring_timeout: true, auto_hangup: 'announcement' }]) {
            assert.equal(autoHangupHint(attempt(result, extra)), '', `${result} ${JSON.stringify(extra)}`);
        }
    }
});

test('трубку клал не телефон — приписки нет', () => {
    for (const result of ['no_answer', 'busy', 'other', 'failed', '', undefined]) {
        assert.equal(autoHangupHint(attempt(result)), '', String(result));
    }
    // Незнакомая причина — не повод что-то приписывать.
    assert.equal(autoHangupHint(attempt('no_answer', { auto_hangup: 'voicemail' })), '');
    assert.equal(autoHangupHint(attempt('no_answer', { auto_hangup: null })), '');
    assert.equal(autoHangupHint(null), '');
    assert.equal(autoHangupHint(undefined), '');
    assert.equal(autoHangupHint({}), '');
});

test('журнал берёт приписку из этого модуля', () => {
    const journal = read('../src/components/dial_list/DialListJournal.jsx');
    assert.ok(journal.includes("import { autoHangupHint } from './attemptHints';"));
    assert.ok(journal.includes('const autoHint = autoHangupHint(attempt);'));
    assert.ok(journal.includes('{cancelledHint}\n                    {autoHint}\n'));
    // Своей копии правила и текстов в журнале нет: разойтись им негде.
    assert.equal(journal.includes('сброшен автоматически'), false);
    assert.equal(journal.includes('attempt.ring_timeout'), false);
    assert.equal(journal.includes('auto_hangup'), false);
});
