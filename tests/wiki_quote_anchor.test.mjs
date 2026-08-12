import assert from 'node:assert/strict';
import test from 'node:test';

import {
    distinctiveTokens, foldKazakh, normalizeText, queryVariants,
} from '../src/components/wiki/searchText.js';

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

test('казахские буквы сворачиваются к русским двойникам', () => {
    assert.equal(foldKazakh('Қазына'), 'Казына');
    assert.equal(foldKazakh('Азаттық'), 'Азаттык');
    assert.equal(foldKazakh('Тіркеуге'), 'Тиркеуге');
    assert.equal(foldKazakh('отчёт'), 'отчет');
    assert.equal(foldKazakh('Аренда 14 дней'), 'Аренда 14 дней');
});

test('свёрнутый вариант запроса есть в списке вариантов', () => {
    /* normalizeText свёртку НЕ делает намеренно: следом идёт транслитерация, и
       по оригиналу «Қарағанды» она даёт «qaraghandy». Свёртка живёт отдельным
       вариантом — так же, как на сервере (wiki/text.py: query_variants). */
    assert.equal(normalizeText('7 Қазына'), '7 қазына');
    assert.ok(queryVariants('7 Қазына').includes('7 казына'),
        'свёрнутый вариант обязан попасть в список');
    assert.ok(queryVariants('7 Казына').includes('7 казына'));
});

test('прокрутка умеет догонять совпадение по горизонтали', async () => {
    /* Совпадение в пятой колонке из одиннадцати остаётся за правым краем
       прокручиваемой обёртки таблицы: вертикально страница доезжает до строки, а
       подсвеченное слово человек не видит — и это читается как «нет перехода».
       Проверяем, что функция для этого есть и экспортирована. */
    const module = await import('../src/components/wiki/scrollContainer.js');
    assert.equal(typeof module.getHorizontalScroller, 'function');
    assert.equal(module.getHorizontalScroller(null), null);
});
