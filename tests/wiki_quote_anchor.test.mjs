import assert from 'node:assert/strict';
import test from 'node:test';

import { distinctiveTokens } from '../src/components/wiki/searchText.js';

/* Переход по источнику ответа помощника прямо на текст.
 *
 * Первый проход подсветки ищет фразу целиком — так работает поиск, и для него
 * этого достаточно. Помощнику не достаточно: у табличного куска цитата это
 * служебная сборка «Акция: Лимонопад; Условия: …», которой в тексте статьи не
 * существует вовсе. Поэтому есть второй проход — по опознавательным словам, и
 * здесь проверяется именно их подбор.
 */

test('служебные слова шапки таблицы не берутся в опознаватели', () => {
    const quote = 'Акция: Лимонопад; Условия: Механика: 50 заказов в Такси.Про = 1 купон';
    const tokens = distinctiveTokens(quote);
    assert.ok(tokens.includes('лимонопад'), 'название акции обязано попасть');
    assert.ok(!tokens.includes('акция'));
    assert.ok(!tokens.includes('условия'));
});

test('числа идут первыми: в справочнике именно они опознают строку', () => {
    const tokens = distinctiveTokens('Депозит: 12 000 тг; Город: Алматы');
    assert.equal(tokens[0], '12 000');
});

test('короткие числа не берём — это нумерация, а не факт', () => {
    const tokens = distinctiveTokens('пункт 5 таблицы');
    assert.deepEqual(tokens.filter((t) => /^\d/.test(t)), []);
});

test('дата и телефон сохраняются как есть', () => {
    const tokens = distinctiveTokens('Даты: 03.08.2026 – 31.08.2026, тел. +7 707 705 08 80');
    assert.ok(tokens.includes('03.08.2026'));
    assert.ok(tokens.some((t) => t.includes('707')));
});

test('повторы не дублируются, а предел соблюдается', () => {
    const tokens = distinctiveTokens('Лимонопад Лимонопад Лимонопад брендирование '
        + 'термопакет регистрация оператору инструкция расписание документы поддержка', 4);
    assert.equal(tokens.length, 4);
    assert.equal(new Set(tokens).size, 4);
});

test('на пустой цитате не падает и ничего не выдумывает', () => {
    assert.deepEqual(distinctiveTokens(''), []);
    assert.deepEqual(distinctiveTokens(null), []);
});

test('латиница и цифробуквенные названия парков сохраняются', () => {
    const tokens = distinctiveTokens('Парки: все, кроме Eki Dongelek и Tenge');
    assert.ok(tokens.includes('dongelek'));
});
