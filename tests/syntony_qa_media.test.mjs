// Проверка качества чатов СЗоВ по Syntony (SZOV-9): вложения снимка приходят адресом нашей
// ручки /api/syntony_qa/media/… — экран дописывает адрес API; чужие полные адреса и объекты
// ответа не трогаются. Модуль без React — проверяется напрямую.
import test from 'node:test';
import assert from 'node:assert/strict';

import { absolutizeSnapshotMedia, withApiBase } from '../src/components/c2d_eval/syntonyMedia.js';

const API = 'https://api.example.kz/';

test('ссылки ручки получают адрес API, остальные остаются как есть', () => {
    assert.equal(withApiBase('/api/syntony_qa/media/41/m1?u=7&e=1&s=x', API),
        'https://api.example.kz/api/syntony_qa/media/41/m1?u=7&e=1&s=x');
    assert.equal(withApiBase('https://storage-02.chat2desk.kz/a.jpg', API),
        'https://storage-02.chat2desk.kz/a.jpg');
    assert.equal(withApiBase('/api/driver_chats/media/m1', API), '/api/driver_chats/media/m1');
    assert.equal(withApiBase(null, API), null);
});

test('снимок: фото, голос и файл — с адресом API, исходник не меняется', () => {
    const snapshot = {
        id: 41, source: 'syntony',
        messages: [
            { id: 'm1', photo: '/api/syntony_qa/media/41/m1?s=a' },
            { id: 'm2', audio: '/api/syntony_qa/media/41/m2?s=b' },
            { id: 'm3', attachments: [{ name: 'PDF', link: '/api/syntony_qa/media/41/m3?s=c' }] },
            { id: 'm4', text: 'ок' },
        ],
    };
    const out = absolutizeSnapshotMedia(snapshot, API);
    assert.equal(out.messages[0].photo, 'https://api.example.kz/api/syntony_qa/media/41/m1?s=a');
    assert.equal(out.messages[1].audio, 'https://api.example.kz/api/syntony_qa/media/41/m2?s=b');
    assert.equal(out.messages[2].attachments[0].link, 'https://api.example.kz/api/syntony_qa/media/41/m3?s=c');
    assert.deepEqual(out.messages[3], { id: 'm4', text: 'ок' });
    assert.equal(snapshot.messages[0].photo, '/api/syntony_qa/media/41/m1?s=a');
    assert.equal(absolutizeSnapshotMedia(null, API), null);
});
