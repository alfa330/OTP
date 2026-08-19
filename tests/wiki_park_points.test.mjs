import assert from 'node:assert/strict';
import test from 'node:test';

import {
    ONLINE, emptyPoint, parkDraftIssue, pointsFromPark, pointsPayload,
} from '../src/components/wiki/parkPoints.js';

/* Точки парка: строка формы = «куда звонят» + номера этой точки.
 *
 * Проверяется то, что нельзя увидеть на скриншоте: чем строка «онлайн»
 * отличается от строки офиса при отправке на сервер и что форма не даёт
 * сохранить точку без номера — номер обязателен (решение владельца 19.08.2026),
 * а пустая строка в справочнике выглядит как рабочая.
 */

const PARK = {
    phones: ['+7 700 111 22 33'],
    offices: [
        { office_id: 1, phones: ['+7 727 000 00 00', '+7 727 000 00 01'] },
        { office_id: 3, phones: ['+7 717 200 30 40'] },
    ],
};

test('онлайн-номер идёт первой строкой, за ним офисы', () => {
    const points = pointsFromPark(PARK);
    assert.equal(points.length, 3);
    assert.equal(points[0].office_id, null);
    assert.deepEqual(points.map((point) => point.office_id), [null, 1, 3]);
    assert.deepEqual(points[1].phones, ['+7 727 000 00 00', '+7 727 000 00 01']);
});

test('офис без номеров открывается с пустым полем, а не без строки ввода', () => {
    const points = pointsFromPark({ offices: [{ office_id: 5 }] });
    assert.deepEqual(points[0].phones, ['']);
});

test('парк без номеров даёт пустой список точек', () => {
    assert.deepEqual(pointsFromPark({}), []);
});

test('тело запроса делит точки на офисы и номера без офиса', () => {
    const payload = pointsPayload(pointsFromPark(PARK));
    assert.deepEqual(payload.phones, ['+7 700 111 22 33']);
    assert.deepEqual(payload.offices, [
        { office_id: 1, phones: ['+7 727 000 00 00', '+7 727 000 00 01'] },
        { office_id: 3, phones: ['+7 717 200 30 40'] },
    ]);
});

test('пустые поля и пробелы в теле не едут', () => {
    const payload = pointsPayload([
        { key: 'a', office_id: null, phones: ['  +7 700 111 22 33 ', '', '   '] },
        { key: 'b', office_id: 2, phones: ['+7 727 000 00 00', ''] },
    ]);
    assert.deepEqual(payload.phones, ['+7 700 111 22 33']);
    assert.deepEqual(payload.offices, [{ office_id: 2, phones: ['+7 727 000 00 00'] }]);
});

test('парк без онлайн-номера шлёт пустой список, а не undefined', () => {
    // PATCH с undefined не тронул бы номера, и снятый последним онлайн-номер
    // остался бы в базе.
    assert.deepEqual(pointsPayload([]).phones, []);
});

test('строка без выбранного места сохранить не даёт', () => {
    const draft = { name: 'iTaxi', points: [emptyPoint(undefined)] };
    assert.match(parkDraftIssue(draft), /не выбрано/);
});

test('строка без номера сохранить не даёт', () => {
    const draft = { name: 'iTaxi', points: [{ key: 'a', office_id: 1, phones: ['  '] }] };
    assert.match(parkDraftIssue(draft), /номер/);
});

test('название важнее строк с номерами', () => {
    assert.equal(parkDraftIssue({ name: '  ', points: [] }), 'Укажите название парка');
});

test('заполненный парк проходит', () => {
    assert.equal(parkDraftIssue({ name: 'iTaxi', points: pointsFromPark(PARK) }), null);
    assert.equal(parkDraftIssue({ name: 'iTaxi', points: [] }), null);
});

test('онлайн — отдельное значение селектора, а не офис', () => {
    assert.equal(ONLINE, 'online');
    assert.equal(emptyPoint(null).office_id, null);
});

test('один офис в двух строках сливается, а не теряется', () => {
    // Селектор такого не даст, но пара «парк + офис» на сервере одна: вторая
    // строка ушла бы в никуда вместе со своими номерами.
    const payload = pointsPayload([
        { key: 'a', office_id: 2, phones: ['+7 727 000 00 00'] },
        { key: 'b', office_id: 2, phones: ['+7 727 333 22 11', '+7 727 000 00 00'] },
        { key: 'c', office_id: null, phones: ['+7 700 111 22 33'] },
        { key: 'd', office_id: null, phones: ['+7 700 111 22 44'] },
    ]);
    assert.deepEqual(payload.offices, [
        { office_id: 2, phones: ['+7 727 000 00 00', '+7 727 333 22 11'] },
    ]);
    assert.deepEqual(payload.phones, ['+7 700 111 22 33', '+7 700 111 22 44']);
});

test('повтор места форма сохранить не даёт', () => {
    const draft = {
        name: 'iTaxi',
        points: [
            { key: 'a', office_id: 2, phones: ['+7 727 000 00 00'] },
            { key: 'b', office_id: 2, phones: ['+7 727 333 22 11'] },
        ],
    };
    assert.match(parkDraftIssue(draft), /дважды/);
});
