import test from 'node:test';
import assert from 'node:assert/strict';
import { buildAttachmentGroup } from '../src/components/wazzup/chatAttachmentGroups.js';

const media = (id, minute = 0, extra = {}) => ({
    messageId: id, account: 'op', channelId: 'synthetic-channel', chatId: 'synthetic-driver',
    isEcho: false, type: 'image', contentUri: `https://example.invalid/${id}.png`,
    dt: new Date(Date.UTC(2026, 9, 8, 12, minute)).toISOString(), ...extra,
});
const ids = (messages) => messages.map((message) => message.messageId);

test('consecutive photos, PDF and document files share one chronological group including captions', () => {
    const thread = [
        media('photo1'), media('pdf', 1, { type: 'document', contentUri: 'https://example.invalid/test.pdf' }),
        media('docx', 2, { type: 'document', contentUri: 'https://example.invalid/test.docx' }),
        media('photo2', 3, { text: 'Synthetic caption' }),
    ];
    for (const selected of thread) assert.deepEqual(buildAttachmentGroup(thread, selected), thread);
    assert.deepEqual(ids(buildAttachmentGroup(thread, { ...thread[2] })), ['photo1', 'pdf', 'docx', 'photo2']);
});

test('text, internal comments, day separators and deleted messages interrupt a group', () => {
    for (const boundary of [
        { messageId: 'text', type: 'text', text: 'Synthetic text' },
        { messageId: 'note', _note: true }, { messageId: 'day', _day: 'Synthetic day' },
        media('deleted', 1, { isDeleted: true }),
        media('audio', 1, { type: 'audio', contentUri: 'https://example.invalid/test.ogg' }),
        null,
    ]) {
        const thread = [media('before1'), media('before2', 1), boundary, media('after1', 2), media('after2', 3)];
        assert.deepEqual(ids(buildAttachmentGroup(thread, thread[1])), ['before1', 'before2']);
        assert.deepEqual(ids(buildAttachmentGroup(thread, thread[3])), ['after1', 'after2']);
    }
});

test('scope and message direction boundaries prevent mixing chats, channels, accounts or senders', () => {
    for (const changed of [{ account: 'other' }, { channelId: 'other' }, { chatId: 'other' }, { isEcho: true }]) {
        const thread = [media('first'), media('other', 1, changed), media('last', 2)];
        for (const selected of thread) assert.deepEqual(buildAttachmentGroup(thread, selected), [selected]);
    }
});

test('outgoing media groups by author ID, with normalized names as fallback', () => {
    const first = media('first', 0, { isEcho: true, authorId: '1', authorName: 'Operator' });
    const same = media('same', 1, { isEcho: true, authorId: 1, authorName: 'Renamed' });
    const different = media('different', 2, { isEcho: true, authorId: '2', authorName: 'Renamed' });
    const unnamed = media('unnamed', 3, { isEcho: true });
    const thread = [first, same, different, unnamed];
    assert.deepEqual(buildAttachmentGroup(thread, same), [first, same]);
    assert.deepEqual(buildAttachmentGroup(thread, different), [different]);
    const byName = [media('name1', 0, { isEcho: true, authorName: ' Тест  Оператор ' }),
        media('name2', 1, { isEcho: true, authorName: 'тест оператор' })];
    assert.deepEqual(buildAttachmentGroup(byName, byName[0]), byName);
    const unknown = [media('unknown1', 0, { isEcho: true }), media('unknown2', 1, { isEcho: true })];
    assert.deepEqual(buildAttachmentGroup(unknown, unknown[0]), [unknown[0]]);
});

test('only consecutive messages no more than ten minutes apart can join', () => {
    const thread = [media('first', 0), media('second', 10), media('third', 20), media('later', 31)];
    assert.deepEqual(ids(buildAttachmentGroup(thread, thread[1])), ['first', 'second', 'third']);
    assert.deepEqual(buildAttachmentGroup(thread, thread[3]), [thread[3]]);
    for (const dt of [undefined, null, '', 'invalid', '2026-10-08T11:59:00Z']) {
        const invalid = media('invalid', 0, { dt });
        assert.deepEqual(buildAttachmentGroup([thread[0], invalid], thread[0]), [thread[0]]);
    }
});

test('missing selections and unsupported attachments retain a standalone fallback without mutation', () => {
    const selected = media('missing');
    const thread = Object.freeze([Object.freeze(media('first')), Object.freeze(media('second', 1))]);
    assert.deepEqual(buildAttachmentGroup(thread, selected), [selected]);
    assert.deepEqual(buildAttachmentGroup(null, selected), [selected]);
    assert.deepEqual(buildAttachmentGroup(thread, null), []);
    const deleted = media('deleted', 0, { isDeleted: true });
    assert.deepEqual(buildAttachmentGroup([deleted], deleted), [deleted]);
    assert.deepEqual(buildAttachmentGroup(thread, thread[0]), [...thread]);
    assert.deepEqual(buildAttachmentGroup(thread, { ...thread[0], account: 'other' }), [{ ...thread[0], account: 'other' }]);
});

test('long media bursts retain the selected file and up to one hundred neighbors in order', () => {
    const thread = Array.from({ length: 250 }, (_, index) => media(`image-${index}`, index));
    for (const index of [0, 45, 125, 210, 249]) {
        const group = buildAttachmentGroup(thread, thread[index]);
        assert.equal(group.length, 100);
        assert.ok(group.includes(thread[index]));
        assert.deepEqual(group, thread.slice(thread.indexOf(group[0]), thread.indexOf(group[0]) + 100));
    }
});
