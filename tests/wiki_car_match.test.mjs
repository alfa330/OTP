import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { carKey, matchBrand, matchCar } from '../src/components/wiki/carMatch.js';

/* Матчинг идёт по НАСТОЯЩЕМУ справочнику (1502 машины), а не по фикстуре:
 * все три бага, которые здесь закреплены, воспроизводились только на нём —
 * побеждала не самая точная машина, а самая ранняя в файле. */
const DATA = JSON.parse(readFileSync(
    fileURLToPath(new URL('../src/components/classifier/classifier-data.json', import.meta.url)),
    'utf-8',
));
const CARS = DATA.cars;

const name = (car) => (car ? `${car.brand} ${car.model}` : null);

test('справочник загрузился', () => {
    assert.ok(CARS.length > 1000, `машин в справочнике: ${CARS.length}`);
});

test('апостроф не разрывает имя модели', () => {
    // Раньше «Cee'd» нормализовался в «cee d», однобуквенное «d» входило и в
    // «datsun», и в «on-do» — и поиск отвечал Datsun on-DO.
    assert.equal(carKey("Cee'd"), 'ceed');
    for (const query of ["Cee'd", 'ceed', 'kia ceed', "kia cee'd"]) {
        const car = matchCar(CARS, query);
        assert.equal(car?.brand, 'Kia', `${query} -> ${name(car)}`);
        assert.ok(carKey(car.model).startsWith('ceed'), `${query} -> ${name(car)}`);
    }
});

test('модель не проигрывает подстроке в чужом имени', () => {
    // «рио» — подстрока «te-RIO-s», и Daihatsu Terios стоит в справочнике раньше.
    for (const query of ['рио', 'rio', 'киа рио', 'kia rio']) {
        assert.equal(name(matchCar(CARS, query)), 'Kia Rio', `${query}`);
    }
});

test('марка + модель через алиасы дают точную пару', () => {
    // Алиасы приходят отдельными словами ('hyundai', 'solaris'); одинокое
    // 'hyundai' равно марке и раньше срабатывало на первой машине Hyundai.
    assert.equal(name(matchCar(CARS, 'хундай солярис')), 'Hyundai Solaris');
    assert.equal(name(matchCar(CARS, 'хендай солярис')), 'Hyundai Solaris');
    assert.equal(name(matchCar(CARS, 'солярис')), 'Hyundai Solaris');
});

test('поиск по началу слова продолжает работать', () => {
    assert.equal(name(matchCar(CARS, 'камри')), 'Toyota Camry');
    assert.equal(name(matchCar(CARS, 'rfvhb')), 'Toyota Camry', 'раскладка');
    // Префикс из трёх букв: варианты написания дают латиницу, и совпадать
    // обязано НАЧАЛО слова, а не вхождение куда угодно.
    assert.equal(name(matchCar(CARS, 'сол')), 'Hyundai Solaris');
    assert.equal(carKey(matchCar(CARS, 'тиг')?.model || '').startsWith('tig'), true);
    assert.equal(carKey(matchCar(CARS, 'kam')?.model || '').startsWith('kam'), true);
});

test('марка без модели отдаёт машину этой марки и саму марку', () => {
    const car = matchCar(CARS, 'toyota');
    assert.equal(car?.brand, 'Toyota');
    assert.equal(matchBrand(CARS, 'toyota'), 'Toyota');
    assert.equal(matchBrand(CARS, 'хундай'), 'Hyundai', 'марка через алиас');
});

test('мусор не выдаёт машину', () => {
    assert.equal(matchCar(CARS, 'зарплата ведомость'), null);
    assert.equal(matchCar(CARS, 'a'), null, 'один символ');
    assert.equal(matchCar(CARS, ''), null);
});

test('каждая пара «марка модель» из справочника находит саму себя', () => {
    let missed = 0;
    for (const car of CARS) {
        const found = matchCar(CARS, `${car.brand} ${car.model}`);
        if (name(found) !== name(car)) missed += 1;
    }
    assert.equal(missed, 0, `не нашлись сами по себе: ${missed} из ${CARS.length}`);
});
