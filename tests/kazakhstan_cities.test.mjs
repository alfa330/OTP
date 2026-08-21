import assert from 'node:assert/strict';
import test from 'node:test';

import {
    KAZAKHSTAN_CITY_OPTIONS, OPERATING_CITIES, isKnownKazakhstanCity,
} from '../src/utils/kazakhstanCities.js';

/* Справочник написан руками, поэтому проверяем его как данные: дубль города
 * ломает CustomSelect (у опций key = value), а город без области выпадает
 * из группировки и «прилипает» к чужому заголовку. */

test('справочник заполнен и покрывает все области', () => {
    assert.ok(KAZAKHSTAN_CITY_OPTIONS.length > 70, `городов: ${KAZAKHSTAN_CITY_OPTIONS.length}`);
    const regions = new Set(KAZAKHSTAN_CITY_OPTIONS.map((option) => option.groupLabel));
    // 17 областей + группа городов республиканского значения.
    assert.equal(regions.size, 18, [...regions].join(', '));
});

test('города не дублируются', () => {
    const names = KAZAKHSTAN_CITY_OPTIONS.map((option) => option.value);
    const duplicates = names.filter((name, index) => names.indexOf(name) !== index);
    assert.deepEqual(duplicates, []);
});

test('у каждой опции есть непустые value/label/groupLabel', () => {
    for (const option of KAZAKHSTAN_CITY_OPTIONS) {
        assert.equal(option.value, option.label, JSON.stringify(option));
        assert.ok(option.value.trim(), JSON.stringify(option));
        assert.ok(option.groupLabel?.trim(), JSON.stringify(option));
    }
});

test('города одной области идут подряд — иначе заголовок повторится', () => {
    // CustomSelect рисует заголовок при СМЕНЕ groupLabel соседних опций.
    const seen = new Set();
    let previous = null;
    for (const { groupLabel } of KAZAKHSTAN_CITY_OPTIONS) {
        if (groupLabel === previous) continue;
        assert.ok(!seen.has(groupLabel), `область встречается разрывами: ${groupLabel}`);
        seen.add(groupLabel);
        previous = groupLabel;
    }
});

test('крупные города на месте, произвольная строка городом не считается', () => {
    for (const city of ['Астана', 'Алматы', 'Шымкент', 'Актобе', 'Караганда', 'Усть-Каменогорск']) {
        assert.ok(isKnownKazakhstanCity(city), city);
    }
    assert.ok(isKnownKazakhstanCity('  Астана  '), 'пробелы по краям должны обрезаться');
    assert.equal(isKnownKazakhstanCity('Ташкент'), false);
    assert.equal(isKnownKazakhstanCity(''), false);
    assert.equal(isKnownKazakhstanCity(null), false);
});

/* Города присутствия выбирают из закрытого списка в справочнике офисов и
 * парков, поэтому опечатка тут — это не «кривая подпись», а город, которого
 * в разделе больше не завести. */

test('города присутствия написаны как в справочнике Казахстана', () => {
    for (const city of OPERATING_CITIES) {
        assert.ok(isKnownKazakhstanCity(city), city);
    }
});

test('города присутствия не дублируются и идут А–Я', () => {
    const duplicates = OPERATING_CITIES.filter(
        (city, index) => OPERATING_CITIES.indexOf(city) !== index,
    );
    assert.deepEqual(duplicates, []);
    assert.deepEqual(
        OPERATING_CITIES,
        [...OPERATING_CITIES].sort((a, b) => a.localeCompare(b, 'ru')),
    );
});

test('перечень тот, что задал владелец', () => {
    // Список закрытый: город вне его в разделе не заведут, поэтому он
    // проверяется целиком, а не «на месте ли Алматы».
    assert.deepEqual([...OPERATING_CITIES].sort((a, b) => a.localeCompare(b, 'ru')), [
        'Актау', 'Актобе', 'Алматы', 'Астана', 'Атырау', 'Жанаозен', 'Караганда',
        'Кокшетау', 'Костанай', 'Кызылорда', 'Павлодар', 'Петропавловск', 'Семей',
        'Талдыкорган', 'Тараз', 'Туркестан', 'Уральск', 'Шымкент', 'Экибастуз',
    ]);
});
