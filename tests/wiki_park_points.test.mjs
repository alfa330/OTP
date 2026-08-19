import assert from 'node:assert/strict';
import test from 'node:test';

import {
    PHONE_DIGITS, digitsOf, emptyNumber, formatDigits, isOnline, numbersFromPark,
    numbersPayload, parkDraftIssue, toPhone,
} from '../src/components/wiki/parkPoints.js';

/* Номера парка: плоский список, у каждого своё место (офис или «онлайн») и
 * необязательная записка.
 *
 * Проверяется то, чего не видно на скриншоте: как номер приводится к единой
 * форме «+7 …» (в старом справочнике был и «8 707…», и «+7 707…»), что уезжает
 * на сервер и почему форма не даёт сохранить парк без номера.
 */

const PARK = {
    phones: [{ phone: '+7 700 111 22 33', note: 'звонить после 10' }],
    offices: [
        { office_id: 1, phones: [{ phone: '+7 727 000 00 00', note: null },
                                 { phone: '+7 727 000 00 01', note: 'бухгалтерия' }] },
    ],
};

test('код страны не набирают: любая запись сводится к десяти цифрам', () => {
    assert.equal(digitsOf('+7 707 705 08 80'), '7077050880');
    assert.equal(digitsOf('8 707 705 08 80'), '7077050880');
    assert.equal(digitsOf('7077050880'), '7077050880');
    assert.equal(digitsOf('707-705-08-80'), '7077050880');
});

test('лишние цифры не влезают', () => {
    assert.equal(digitsOf('+7 707 705 08 80 12345').length, PHONE_DIGITS);
});

test('номер показывается так, как его диктуют', () => {
    assert.equal(formatDigits('7077050880'), '707 705 08 80');
    assert.equal(formatDigits('707705'), '707 705');
    assert.equal(toPhone('7077050880'), '+7 707 705 08 80');
    assert.equal(toPhone(''), '');
});

test('номера без офиса идут первыми, за ними офисные', () => {
    const rows = numbersFromPark(PARK);
    assert.equal(rows.length, 3);
    assert.deepEqual(rows.map((row) => row.office_id), [null, 1, 1]);
    assert.equal(rows[0].note, 'звонить после 10');
    assert.equal(rows[2].note, 'бухгалтерия');
});

test('парк без номеров открывается одной пустой строкой', () => {
    // Номер обязателен, и форма обязана показать это полем: пустая секция
    // читается как «заполнять нечего».
    const rows = numbersFromPark({});
    assert.equal(rows.length, 1);
    assert.equal(rows[0].phone, '');
    assert.ok(isOnline(rows[0]));
});

test('старый формат — просто строки — тоже понимается', () => {
    const rows = numbersFromPark({ phones: ['+7 700 111 22 33'] });
    assert.equal(rows[0].phone, '+7 700 111 22 33');
    assert.equal(rows[0].note, '');
});

test('в тело запроса номера едут в единой форме', () => {
    const payload = numbersPayload([
        { key: 'a', office_id: null, phone: '8 700 111 22 33', note: '  звонить после 10 ' },
        { key: 'b', office_id: 2, phone: '727 000 00 00', note: '' },
    ]);
    assert.deepEqual(payload, [
        { office_id: null, phone: '+7 700 111 22 33', note: 'звонить после 10' },
        { office_id: 2, phone: '+7 727 000 00 00', note: null },
    ]);
});

test('недобранный номер в тело не едет', () => {
    // Строку начали заполнять и бросили — это не номер, а мусор в справочнике.
    assert.deepEqual(numbersPayload([{ key: 'a', office_id: null, phone: '707 70' }]), []);
});

test('один номер в одном месте дважды не пишется', () => {
    const payload = numbersPayload([
        { key: 'a', office_id: 2, phone: '+7 727 000 00 00' },
        { key: 'b', office_id: 2, phone: '8 727 000 00 00' },
        { key: 'c', office_id: null, phone: '+7 727 000 00 00' },
    ]);
    // Тот же номер в другом месте — не повтор: это отдельная точка.
    assert.deepEqual(payload.map((item) => item.office_id), [2, null]);
});

test('парк без единого номера сохранить нельзя', () => {
    assert.match(parkDraftIssue({ name: 'iTaxi', numbers: [emptyNumber()] }), /хотя бы один номер/);
    assert.match(parkDraftIssue({ name: 'iTaxi', numbers: [] }), /хотя бы один номер/);
});

test('недобранный номер сохранить не даёт', () => {
    const draft = { name: 'iTaxi', numbers: [{ key: 'a', office_id: null, phone: '707 705' }] };
    assert.match(parkDraftIssue(draft), /десять цифр/);
});

test('название важнее номеров', () => {
    assert.equal(parkDraftIssue({ name: '  ', numbers: [] }), 'Укажите название парка');
});

test('заполненный парк проходит', () => {
    assert.equal(parkDraftIssue({ name: 'iTaxi', numbers: numbersFromPark(PARK) }), null);
});

test('новая строка по умолчанию без офиса', () => {
    // «Онлайн» — состояние по умолчанию: номер уже принадлежит парку, а офис
    // ему выбирают отдельно, если он есть.
    assert.ok(isOnline(emptyNumber()));
    assert.ok(!isOnline(emptyNumber(3)));
});
