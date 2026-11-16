import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
    admitted, allows, carsOf, cityTariffs, conditionOf, dateWords, forgetClassifier, listRows,
    loadClassifier, requirementOf, shortRequirement, tariffSummary, upcomingOf, verdictOf,
} from '../src/components/classifier/classifierData.js';

/* Расчёт экрана — двойник помощника вики (wiki/classifier.requirement и verdict).
 * Ожидания посчитаны ПИТОНОМ на настоящем справочнике (tests/data/classifier_parity.json,
 * пересчёт — tests/gen_classifier_parity.py); здесь их обязан повторить JS. */

const read = (path) => JSON.parse(readFileSync(fileURLToPath(new URL(path, import.meta.url)), 'utf-8'));
const DATA = read('../wiki/data/classifier-data.json');
const CASES = read('./data/classifier_parity.json').cases;

/* Копия wiki/routes_classifier.city_payload — ответ сервера экрану по городу. */
function payloadOf(cityId) {
    const city = DATA.cities.find((item) => item.id === cityId);
    const entryOf = (raw) => {
        const out = { notes: (raw.notes || []).map((index) => DATA.notes[index]) };
        if (Number.isInteger(raw.list)) {
            const table = DATA.lists[raw.list];
            Object.assign(out, {
                kind: 'list', rows: table.rows || [], other: table.other || null,
                brands: table.brands || {}, count: (table.rows || []).filter((row) => row[1] !== 0).length,
            });
        } else {
            Object.assign(out, { kind: 'any', any: raw.any });
        }
        return out;
    };
    const tariffs = {};
    DATA.tariffs.forEach(({ key }) => {
        const raw = city.tariffs[key];
        if (!raw) return;
        const entry = entryOf(raw);
        if (raw.next) entry.next = { ...entryOf(raw.next), from: raw.next.from };
        tariffs[key] = entry;
    });
    return {
        tariffs: DATA.tariffs, cities: DATA.cities.map(({ id, name }) => ({ id, name })),
        cars: DATA.cars, reqs: DATA.reqs, source: DATA.source,
        city: { id: city.id, name: city.name, url: city.url, updated: city.updated, tariffs },
    };
}

const payloads = new Map();
const payload = (cityId) => {
    if (!payloads.has(cityId)) payloads.set(cityId, payloadOf(cityId));
    return payloads.get(cityId);
};

test('выборка паритета на месте и большая', () => {
    assert.ok(CASES.length > 500, `случаев: ${CASES.length}`);
});

test('requirementOf и verdictOf повторяют питон на всей выборке', () => {
    const wrong = [];
    for (const item of CASES) {
        const req = requirementOf(payload(item.city), { brand: item.brand, model: item.model }, item.tariff);
        const label = `${item.city} ${item.tariff} ${item.brand} ${item.model}`;
        if (!req || req.kind !== item.kind || req.year !== item.year) {
            wrong.push(`${label}: ${JSON.stringify(req)} ≠ ${item.kind} ${item.year}`);
            continue;
        }
        for (const [year, expected] of Object.entries(item.verdicts)) {
            const got = verdictOf(req, Number(year));
            if (got !== expected) wrong.push(`${label} ${year}: ${got} ≠ ${expected}`);
        }
    }
    assert.deepEqual(wrong.slice(0, 10), []);
});

test('Chevrolet Cobalt 2025 в «Комфорт» Актау — только зарегистрированные до 30 июня 2026', () => {
    const req = requirementOf(payload('aktau'), { brand: 'Chevrolet', model: 'Cobalt' }, 'comfort');
    assert.equal(req.kind, 'row');
    assert.equal(req.year, 0);
    assert.deepEqual(req.except, [2024, 2026, '2026-06-30']);
    assert.equal(verdictOf(req, 2025), 'except');
    assert.equal(verdictOf(req, 2023), 'no');
    assert.equal(dateWords(req.except[2]), '30 июня 2026');
    assert.equal(conditionOf(req), '');                   // исключение — не условие «подходит»
});

test('правило «любая модель» и тариф, которого в городе нет', () => {
    const aksay = payload('aksay');
    const car = { brand: 'Toyota', model: 'Camry' };
    const req = requirementOf(aksay, car, 'econom');
    assert.deepEqual([req.kind, req.year], ['any', 1988]);
    assert.equal(verdictOf(req, 1988), 'fits');
    assert.equal(verdictOf(req, 1987), 'old');
    assert.equal(requirementOf(aksay, car, 'comfort'), null);
    assert.deepEqual(cityTariffs(aksay).map((item) => item.key), ['econom']);
    assert.equal(tariffSummary(aksay.city.tariffs.econom), 'Любая модель от 1988 г.');
});

test('«Другие модели» и правило марки в Межгороде Алматы', () => {
    const almaty = payload('almaty');
    const other = requirementOf(almaty, { brand: 'BYD', model: 'Han' }, 'intercity');
    assert.deepEqual([other.kind, other.year], ['other', 1999]);
    const lada = carsOf(almaty).find((car) => car.brand === 'LADA (ВАЗ)'
        && !almaty.city.tariffs.intercity.rows.some((row) => row[0] === car.index));
    assert.ok(lada, 'нет LADA вне таблицы Межгорода');
    const rule = requirementOf(almaty, lada, 'intercity');
    assert.deepEqual([rule.kind, rule.year], ['brand', 0]);
    assert.equal(verdictOf(rule, 2026), 'no');
    assert.match(tariffSummary(almaty.city.tariffs.intercity), /модел.*, остальные — от 1999 г\.$/);
});

test('условие «подходит» видно: BYD Han в Ultima Алматы', () => {
    const req = requirementOf(payload('almaty'), { brand: 'BYD', model: 'Han' }, 'ultima');
    assert.equal(verdictOf(req, 2023), 'fits');
    assert.equal(conditionOf(req), 'только комплектации с сидениями, не в спортивном стиле');
    assert.equal(shortRequirement(req), 'от 2022');
});

test('машины нет в указателе — «нет в списке», а не чужая строка', () => {
    const req = requirementOf(payload('almaty'), { brand: 'Нет', model: 'Такой' }, 'comfort');
    assert.deepEqual([req.kind, req.year], ['absent', 0]);
    assert.equal(allows(req), false);
});

test('номер машины ищется в том ответе, по которому считают', () => {
    // Два слепка с разным порядком машин: строка таблицы обязана найтись по имени.
    const base = payload('almaty');
    const shuffled = { ...base, cars: [['Нет', 'Такой'], ...base.cars] };
    shuffled.city = { ...base.city, tariffs: {
        comfort: { ...base.city.tariffs.comfort, rows: base.city.tariffs.comfort.rows.map(([i, ...rest]) => [i + 1, ...rest]) },
    } };
    const car = { brand: 'Toyota', model: 'Camry' };
    assert.equal(requirementOf(shuffled, car, 'comfort').year, requirementOf(base, car, 'comfort').year);
});

test('список тарифа: без фильтра — допущенные, с фильтром — и «не допускается»', () => {
    const almaty = payload('almaty');
    const all = listRows(almaty, 'comfort');
    assert.equal(all.length, almaty.city.tariffs.comfort.count);
    assert.ok(all.every((row) => admitted(row.req)));
    const found = listRows(almaty, 'comfort', 'cobalt');
    assert.deepEqual(found.map((row) => [row.brand, row.model]), [['Chevrolet', 'Cobalt']]);
    assert.equal(found[0].req.year, 0);
    assert.deepEqual(listRows(almaty, 'nope'), []);              // тарифа в городе нет
    assert.deepEqual(listRows(payload('aksay'), 'econom'), []);   // у правила таблицы нет
});

test('новые правила, которые ещё не вступили, видны отдельно', () => {
    const base = payload('almaty');
    const withNext = {
        ...base,
        city: { ...base.city, tariffs: { ...base.city.tariffs, econom: {
            ...base.city.tariffs.econom, next: { kind: 'any', any: 2000, from: '2027-01-01', notes: [] },
        } } },
    };
    const upcoming = upcomingOf(withNext, { brand: 'Toyota', model: 'Camry' }, 'econom');
    assert.equal(upcoming.from, '2027-01-01');
    assert.deepEqual([upcoming.req.kind, upcoming.req.year], ['any', 2000]);
    assert.equal(upcomingOf(base, { brand: 'Toyota', model: 'Camry' }, 'econom'), null);
});

test('loadClassifier: один запрос на город, отказ из кэша вычищается, 404 — тихо', async () => {
    forgetClassifier();
    let calls = 0;
    const fetcher = async (url, options) => {
        calls += 1;
        assert.equal(url, '/api/wiki/classifier');
        return { city: { id: options.params.city } };
    };
    const first = await loadClassifier({ base: '/api/wiki', city: 'aktau', fetcher });
    const second = await loadClassifier({ base: '/api/wiki', city: 'aktau', fetcher });
    assert.equal(first, second);
    assert.equal(calls, 1);

    let fail = true;
    const flaky = async () => { if (fail) { fail = false; throw new Error('сеть'); } return { ok: true }; };
    await assert.rejects(loadClassifier({ base: '/x', city: 'semey', fetcher: flaky }));
    assert.deepEqual(await loadClassifier({ base: '/x', city: 'semey', fetcher: flaky }), { ok: true });

    const hidden = async () => {
        const error = new Error('404');
        error.response = { status: 404, data: { code: 'WIKI_CLASSIFIER_HIDDEN' } };
        throw error;
    };
    await assert.rejects(loadClassifier({ base: '/y', city: 'almaty', fetcher: hidden }),
        (error) => error.hidden === true);
    // Голый 404 — ручки ещё нет (фронт выкатился раньше сервера): сбой, не «скрыто».
    const missing = async () => { const error = new Error('404'); error.response = { status: 404 }; throw error; };
    await assert.rejects(loadClassifier({ base: '/z', city: 'almaty', fetcher: missing }),
        (error) => !error.hidden);
    forgetClassifier();
});

test('тарифы города идут в порядке справочника', () => {
    assert.deepEqual(cityTariffs(payload('almaty')).map((item) => item.key),
        ['econom', 'intercity', 'comfort', 'comfort_plus', 'business', 'electro', 'ultima']);
    assert.deepEqual(cityTariffs(payload('aktau')).map((item) => item.key),
        ['econom', 'intercity', 'comfort', 'comfort_plus']);
});
