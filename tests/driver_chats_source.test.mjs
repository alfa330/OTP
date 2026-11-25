// «Чаты водителей» на Syntony (25.11.2026): адреса вложений, привязка своей отметки к водителю,
// тексты про «Передан». Модуль без React — проверяется напрямую.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    absolutizeMediaLinks, driverKeyOf, handoffTexts,
} from '../src/components/driver_chats/chatSource.js';

test('ссылки ручки /media получают адрес API, чужие полные адреса не трогаются', () => {
    const chats = [{
        key: 'cabc',
        messages: [
            { id: 'm1', photo: '/api/driver_chats/media/m1?u=7&e=1&s=x', attachments: [] },
            { id: 'm2', audio: 'https://storage-02.chat2desk.kz/a.ogg', attachments: [] },
            {
                id: 'm3',
                attachments: [{ name: 'техпаспорт.pdf', link: '/api/driver_chats/media/m3?s=y' }],
            },
        ],
    }];
    const out = absolutizeMediaLinks(chats, 'https://api.icorehub.kz/');
    assert.equal(out[0].messages[0].photo,
        'https://api.icorehub.kz/api/driver_chats/media/m1?u=7&e=1&s=x');
    assert.equal(out[0].messages[1].audio, 'https://storage-02.chat2desk.kz/a.ogg');
    assert.equal(out[0].messages[2].attachments[0].link,
        'https://api.icorehub.kz/api/driver_chats/media/m3?s=y');
    // Исходный ответ не мутируется: он живёт и в других местах экрана.
    assert.equal(chats[0].messages[0].photo, '/api/driver_chats/media/m1?u=7&e=1&s=x');
});

test('пустой ответ и сообщения без вложений проходят как есть', () => {
    assert.deepEqual(absolutizeMediaLinks(undefined, 'x'), []);
    const out = absolutizeMediaLinks([{ messages: [{ id: 1, text: 'привет' }] }], 'https://a');
    assert.deepEqual(out[0].messages[0], { id: 1, text: 'привет' });
});

test('водитель — клиент Chat2Desk, а без него — номер', () => {
    assert.equal(driverKeyOf({ clientId: 555, phone: '77011234567' }), 'c555');
    assert.equal(driverKeyOf({ clientId: 0, phone: '77011234567' }), 'c0');
    assert.equal(driverKeyOf({ clientId: null, phone: '77011234567' }), 'p77011234567');
    assert.equal(driverKeyOf({ clientId: null, phone: '' }), null);
    // Два водителя одного парка у Syntony — разные ключи: чужая отметка к ним не прилипнет.
    assert.notEqual(driverKeyOf({ clientId: null, phone: '77011234567' }),
        driverKeyOf({ clientId: null, phone: '77017654321' }));
});

test('у Syntony «Передан» не обещает заметку в чате', () => {
    const syntony = handoffTexts(false);
    assert.match(syntony.subtitle, /журнале раздела/);
    assert.doesNotMatch(syntony.subtitle + syntony.sent + syntony.failed, /уйдёт внутренний комментарий/);
    const chat2desk = handoffTexts(true);
    assert.match(chat2desk.subtitle, /В чат уйдёт внутренний комментарий/);
    // Пока /context не ответил — осторожная формулировка, как у Syntony.
    assert.deepEqual(handoffTexts(undefined), syntony);
});
