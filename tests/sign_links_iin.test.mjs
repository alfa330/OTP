// Проверка ИИН на фронте — двойник sign_links/iin.py.
//
// Векторы те же, что в tests/test_sign_links.py (класс IinTests): разойдись
// правила, поле подсказывало бы одно, а сервер отвечал другое. Контрольные
// ИИН здесь синтетические — собраны по алгоритму, живых людей за ними нет.
//
// Запуск: node --test tests/sign_links_iin.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    IIN_ERRORS, IIN_LENGTH, formatIin, iinErrorMessage, normalizeIin, validateIin,
} from '../src/components/sign_links/iin.js';

// 1990-01-01, мужчина (7-я цифра 3), контрольная 7 — сходится с первого прохода.
const VALID = '900101300007';
// Первый проход даёт остаток 10, контрольная берётся со второго прохода.
const VALID_SECOND_PASS = '900101300811';
// Оба прохода дают 10 — такого ИИН не существует ни с какой контрольной цифрой.
const IMPOSSIBLE_PREFIX = '90010130080';

test('верный ИИН проходит и возвращается голыми цифрами', () => {
    assert.deepEqual(validateIin(VALID), { iin: VALID, error: null });
    assert.deepEqual(validateIin(VALID_SECOND_PASS), { iin: VALID_SECOND_PASS, error: null });
});

test('разделители снимаются молча, буквы — ошибка', () => {
    assert.equal(validateIin('900101 300 007').iin, VALID);
    assert.equal(validateIin('900101-300-007').iin, VALID);
    assert.equal(validateIin(' 900101300007 ').iin, VALID);
    assert.equal(validateIin('9001O1300007').error, 'digits');
    assert.equal(normalizeIin(' 12 34 '), '1234');
});

test('длина, дата, век и контрольная цифра — каждая своим кодом', () => {
    assert.equal(validateIin('').error, 'empty');
    assert.equal(validateIin('90010130000').error, 'length');
    assert.equal(validateIin('9001013000071').error, 'length');
    assert.equal(validateIin('901301300007').error, 'date');      // 13-й месяц
    assert.equal(validateIin('900230300007').error, 'date');      // 30 февраля
    assert.equal(validateIin('000229500000').error, 'checksum');  // 29.02.2000 — дата есть, сумма нет
    assert.equal(validateIin('900101700007').error, 'century');   // 7-я цифра 7
    assert.equal(validateIin('900101300008').error, 'checksum');
    assert.equal(validateIin('090101300007').error, 'checksum');  // перестановка соседних цифр
});

test('29 февраля невисокосного года не бывает, а без века — допускается', () => {
    // 1900 — не високосный (век известен по цифре 3).
    assert.equal(validateIin('000229300000').error, 'date');
    // Век не проставлен (0) — дату не отвергаем, дальше решает контрольная сумма.
    assert.notEqual(validateIin('000229000000').error, 'date');
});

test('ИИН, у которого оба прохода дают 10, не проходит ни с одной контрольной', () => {
    for (let control = 0; control <= 9; control += 1) {
        assert.equal(validateIin(`${IMPOSSIBLE_PREFIX}${control}`).error, 'checksum', String(control));
    }
});

test('БИН юридического лица не проходит за ИИН', () => {
    // У БИН пятая цифра 4–6 — «день» 40+ не бывает.
    assert.equal(validateIin('230140006818').error, 'date');
});

test('сообщения — по-русски и на каждый код', () => {
    for (const code of Object.keys(IIN_ERRORS)) {
        assert.ok(iinErrorMessage(code).length > 5, code);
        assert.match(iinErrorMessage(code), /[А-Яа-яЁё]/, code);
    }
    // Незнакомый код — самое общее объяснение, а не undefined.
    assert.equal(iinErrorMessage('whatever'), IIN_ERRORS.checksum);
});

test('показ — тройками после даты, только для чтения', () => {
    assert.equal(formatIin(VALID), '900101 300 007');
    assert.equal(formatIin('123'), '123');
    assert.equal(formatIin(''), '—');
    assert.equal(IIN_LENGTH, 12);
});

test('коды ошибок совпадают с серверным двойником', () => {
    const python = readFileSync(new URL('../sign_links/iin.py', import.meta.url), 'utf8');
    for (const code of Object.keys(IIN_ERRORS)) {
        assert.ok(python.includes(`'${code}':`), `кода '${code}' нет в sign_links/iin.py`);
        assert.ok(python.includes(IIN_ERRORS[code]), `текст для '${code}' расходится с sign_links/iin.py`);
    }
});
