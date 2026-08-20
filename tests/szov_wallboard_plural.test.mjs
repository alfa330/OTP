import test from 'node:test';
import assert from 'node:assert/strict';

import { pluralRu } from '../src/components/monitoring/szovWallboardShared.js';

/*
 * Склонение по числу на табло СЗоВ. Проверяется отдельным тестом, потому что ошибку в нём
 * видно только на конкретных числах: наивное «одно слово на все случаи» даёт «1 чатов в
 * работе», а наивное «остаток от 10» — «11 чат» и «12 чата».
 */

const chats = (count) => `${count} ${pluralRu(count, 'чат', 'чата', 'чатов')}`;

test('единственное число — только у настоящей единицы', () => {
    assert.equal(chats(1), '1 чат');
    assert.equal(chats(21), '21 чат');
    assert.equal(chats(101), '101 чат');
    // 11 — ловушка остатка от 10: там «чатов», а не «чат».
    assert.equal(chats(11), '11 чатов');
    assert.equal(chats(111), '111 чатов');
});

test('от двух до четырёх — «чата», но не в подростковом десятке', () => {
    for (const count of [2, 3, 4, 22, 33, 104]) {
        assert.equal(chats(count), `${count} чата`);
    }
    for (const count of [12, 13, 14, 112]) {
        assert.equal(chats(count), `${count} чатов`);
    }
});

test('ноль и пятёрки — «чатов»', () => {
    for (const count of [0, 5, 9, 10, 20, 25, 100]) {
        assert.equal(chats(count), `${count} чатов`);
    }
});

test('мусор вместо числа не роняет строку', () => {
    // В снимке значение может не приехать вовсе; на стене это ноль, а не исключение.
    assert.equal(pluralRu(null, 'чат', 'чата', 'чатов'), 'чатов');
    assert.equal(pluralRu(undefined, 'чат', 'чата', 'чатов'), 'чатов');
    assert.equal(pluralRu('3', 'чат', 'чата', 'чатов'), 'чата');
});
