/*
 * Направление оператора следует за группой (src/utils/groupDirection.js).
 *
 * Сервер при переводе сам ставит направление группы. Здесь закреплено, что
 * показывает карточка сотрудника и когда она отправляет направление сама.
 * Почти каждый сценарий — дефект, найденный ревью первой версии правки.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { directionForPickedGroup, shouldSendDirectionUpdate } from '../src/utils/groupDirection.js';

// Исходная группа 3 держит направление 71, а у оператора записано 70 — ровно та
// расстыковка, ради которой правка. Возврат к исходной группе обязан вернуть
// именно 70, а не подставить 71.
const groups = [
    { id: 3, department_id: 1, effective_direction_id: 71 },
    { id: 6, department_id: 1, effective_direction_id: 69 },
    { id: 40, department_id: 1, effective_direction_id: null },
    { id: 50, department_id: 2, effective_direction_id: null },
];
const directions = [
    { id: 69, department_id: 1 },
    { id: 70, department_id: 1 },
    { id: 71, department_id: 1 },
    { id: 80, department_id: 2 },
];

const pick = (overrides) => directionForPickedGroup({
    groups,
    directions,
    originalGroupId: 3,
    originalDirectionId: 70,
    currentDirectionId: 70,
    pickedByHand: false,
    ...overrides,
});

test('выбор группы подставляет её направление', () => {
    assert.equal(pick({ groupId: '6' }), 69);
});

test('возврат к исходной группе возвращает записанное направление, а не направление группы', () => {
    assert.equal(pick({ groupId: '3', currentDirectionId: 69 }), 70);
});

test('группа без направления возвращает исходное, а не прошлую подстановку', () => {
    // Иначе у СВ, который поля направления не видит, сохранение упало бы с 403.
    assert.equal(pick({ groupId: '40', currentDirectionId: 69 }), 70);
});

test('выбранное руками направление выбор группы не трогает', () => {
    assert.equal(pick({ groupId: '6', currentDirectionId: '70', pickedByHand: true }), '70');
    assert.equal(pick({ groupId: '3', currentDirectionId: '69', pickedByHand: true }), '69');
});

test('после смены отдела группа без направления оставляет поле пустым', () => {
    // Исходное направление принадлежит прежнему отделу: подставить его — увезти
    // оператора в новый отдел со старым направлением, минуя «Направление обязательно».
    assert.equal(pick({ groupId: '50', currentDirectionId: '' }), '');
    // и прошлая подстановка из группы нового отдела тоже не остаётся
    assert.equal(pick({ groupId: '50', currentDirectionId: 80 }), '');
});

test('без справочника направлений отдел не проверяется — исходное возвращается', () => {
    assert.equal(pick({ groupId: '50', directions: undefined, currentDirectionId: '' }), 70);
});

test('форма создания: подставляет направление группы и очищает его у группы без направления', () => {
    const create = (groupId, currentDirectionId) => directionForPickedGroup({
        groupId,
        groups,
        directions,
        originalGroupId: undefined,
        originalDirectionId: '',
        currentDirectionId,
        pickedByHand: false,
    });
    assert.equal(create('6', ''), 69);
    assert.equal(create('40', 69), '');
    assert.equal(create('50', 69), '');
});

test('направление, которое сервер уже записал при переводе, повторно не отправляется', () => {
    assert.equal(
        shouldSendDirectionUpdate({ nextDirectionId: 69, directionAfterMove: 69, canEditDirection: true }),
        false,
    );
});

test('админ при переводе оставил прежнее направление — оно не теряется', () => {
    // Карточка попросила не синхронизировать: сервер направление не менял, опора — исходное.
    assert.equal(
        shouldSendDirectionUpdate({ nextDirectionId: '70', directionAfterMove: 70, canEditDirection: true }),
        false,
    );
    // Сервер всё же записал направление группы, а в карточке другое — отправляем выбранное.
    assert.equal(
        shouldSendDirectionUpdate({ nextDirectionId: '70', directionAfterMove: 69, canEditDirection: true }),
        true,
    );
});

test('обычному СВ направление не отправляется никогда', () => {
    assert.equal(
        shouldSendDirectionUpdate({ nextDirectionId: 69, directionAfterMove: 70, canEditDirection: false }),
        false,
    );
});

test('пустое направление не отправляется', () => {
    assert.equal(
        shouldSendDirectionUpdate({ nextDirectionId: '', directionAfterMove: 70, canEditDirection: true }),
        false,
    );
});

test('раздел «Группы» просит синхронизировать направление при добавлении оператора', () => {
    const source = readFileSync(new URL('../src/components/groups/GroupsView.jsx', import.meta.url), 'utf8');
    assert.match(
        source,
        /mutateMember\('operators', \{ operator_id: Number\(addOpId\), start_date: effDate \|\| null, sync_direction: true \}\)/,
    );
});
