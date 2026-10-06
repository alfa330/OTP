import test from 'node:test';
import assert from 'node:assert/strict';
import { firstVisibleMessage, messageQuote } from '../src/components/wazzup/threadPresentation.js';

test('quote uses the current loaded original, including edits and deletions', () => {
    const answer = { messageId: 'answer', replyToMessageId: 'original', replyText: 'Old text' };
    const original = { messageId: 'original', text: 'Corrected text', authorName: 'Айгуль', isEcho: true };
    assert.deepEqual(messageQuote(answer, new Map([['original', original]])), {
        messageId: 'original', text: 'Corrected text', author: 'Айгуль',
    });
    assert.equal(messageQuote(answer, new Map([['original', { ...original, isDeleted: true }]])).text,
        'Сообщение удалено');
});

test('quote remains readable when original is outside loaded history', () => {
    assert.deepEqual(messageQuote({ messageId: 'reply', replyToMessageId: 'old',
        replyText: 'Документы получены', replyAuthorName: 'Асхат' }, new Map()), {
        messageId: 'old', text: 'Документы получены', author: 'Асхат',
    });
    assert.equal(messageQuote({ messageId: 'reply', replyToMessageId: 'unknown' }, new Map()).text,
        'Исходное сообщение');
});

test('quote labels attachment-only messages and does not quote the message itself', () => {
    const original = { messageId: 'photo', type: 'image', text: '', isEcho: false };
    assert.deepEqual(messageQuote({ messageId: 'reply', replyToMessageId: 'photo' }, new Map([['photo', original]])), {
        messageId: 'photo', text: 'Фото', author: 'Клиент',
    });
    assert.equal(messageQuote({ messageId: 'same', replyToMessageId: 'same' }, new Map()), null);
    assert.equal(messageQuote({ messageId: 'normal' }, new Map()), null);
});

test('floating date follows the first partly visible message with bounded layout reads', () => {
    let measurements = 0;
    const nodes = Array.from({ length: 2000 }, (_, index) => ({
        id: index,
        getBoundingClientRect() { measurements += 1; return { bottom: (index + 1) * 50 }; },
    }));
    assert.equal(firstVisibleMessage(nodes, 75024).id, 1500);
    assert.ok(measurements <= 12, `Expected logarithmic lookup, got ${measurements} layout reads`);
    assert.equal(firstVisibleMessage(nodes, 75050).id, 1501);
    assert.equal(firstVisibleMessage(nodes, -10).id, 0);
    assert.equal(firstVisibleMessage(nodes, 100000), null);
    assert.equal(firstVisibleMessage([], 0), null);
});
