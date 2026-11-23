import test from 'node:test';
import assert from 'node:assert/strict';

import {
    YANDEX_REG_HOURLY_RATE,
    YANDEX_REG_TARGET_CONVERSION,
    calculateYandexRegSalary,
    yandexRegDealPrice,
    yandexRegQualityWithholdRate,
} from '../src/utils/salaryFormula.js';

// Эталон — «KPI.xlsx» (лист «ЯР») и пояснение владельца
// «KPI_логика_и_калькулятор_ЗП.xlsx» (лист «Калькулятор ЗП»), с ноября 2026 —
// задача SALES-2 (#427): целевая конверсия 45 %, заявки и успешно закрытые — личные.
// Оплата в час 600 ₸, пример — 41 успешно закрытая из 100 поступивших.

test('промежуточные шаги примера сходятся один в один', () => {
    const r = calculateYandexRegSalary({
        hoursWorked: 176,          // B5
        hoursNorm: 176,
        requests: 100,             // B6 — мои поступившие заявки
        successes: 41,             // B7 — мои успешно закрытые
        quality: 93,               // B8 = 0,93
    });

    assert.equal(r.factConversion, 0.41);                         // B11 = 41/100
    assert.equal(r.targetConversion, 0.45);                       // B12 — SALES-2
    assert.equal(Number(r.planPercent.toFixed(6)), 0.911111);     // B13 = 0,41/0,45
    assert.equal(r.dealPrice, 240);                               // B14 — ступень 90–100%
    assert.equal(r.qualityWithholdRate, 0.1);                     // B15 — качество 91–95%
    assert.equal(r.oklad, 105600);                                // B17 = 600 × 176
});

test('бонус — за каждую свою успешно закрытую заявку', () => {
    // Решение владельца: «Сумма бонуса» из KPI.xlsx — цена за одну успешку, как
    // «Сумма за успешку» у «Основы». С SALES-2 цена умножается на те же успешно
    // закрытые, по которым считается конверсия, — отдельного поля больше нет.
    const r = calculateYandexRegSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        requests: 100,
        successes: 41,
        quality: 93,
    });

    assert.equal(r.successes, 41);
    assert.equal(r.bonusDeals, 9840);                             // 41 × 240
    assert.equal(r.qualityWithheld, 984);                         // 9 840 × 0,1
    assert.equal(r.finalSalary, 114456);                          // 105 600 + 9 840 − 984
});

test('план 45 % вместо 50 %: те же 41 из 100 дают 240 ₸ за успешку вместо 200 ₸', () => {
    assert.equal(YANDEX_REG_TARGET_CONVERSION, 0.45);
    const before = calculateYandexRegSalary({ requests: 100, successes: 41, targetConversion: 0.5 });
    const after = calculateYandexRegSalary({ requests: 100, successes: 41 });

    assert.equal(Number(before.planPercent.toFixed(6)), 0.82);
    assert.equal(before.dealPrice, 200);
    assert.equal(after.dealPrice, 240);
});

test('ровно на пороге ступени цена не проваливается из-за деления на 0,45', () => {
    // 36/100 ÷ 0,45 в двоичной дроби — 0,7999999999999999: без округления оператор
    // с ровно 80 % плана получил бы 0 ₸ за успешку вместо 200 ₸.
    const cases = [
        [100, 36, 0.8, 200],
        [25, 9, 0.8, 200],
        [200, 81, 0.9, 240],
        [100, 45, 1.0, 280],
        [200, 99, 1.1, 320],
        [100, 54, 1.2, 360],
    ];
    for (const [requests, successes, plan, price] of cases) {
        const r = calculateYandexRegSalary({ requests, successes });
        assert.equal(r.planPercent, plan, `${successes} из ${requests}`);
        assert.equal(r.dealPrice, price, `${successes} из ${requests}`);
    }

    // Перебором: доля плана s ÷ (r × 0,45) ровно равна k/10, когда 200·s = 9·r·k.
    // Ни один такой случай не должен уходить на ступень ниже.
    const priceAt = { 8: 200, 9: 240, 10: 280, 11: 320, 12: 360 };
    for (let requests = 1; requests <= 500; requests += 1) {
        for (let successes = 0; successes <= requests; successes += 1) {
            for (const k of [8, 9, 10, 11, 12]) {
                if (200 * successes !== 9 * requests * k) continue;
                const r = calculateYandexRegSalary({ requests, successes });
                assert.equal(r.dealPrice, priceAt[k], `${successes} из ${requests} — ровно ${k * 10}% плана`);
            }
        }
    }
});

test('шкала цены успешки повторяет пороги таблицы ЯР', () => {
    assert.equal(yandexRegDealPrice(0), 0);        // ниже 70% — MATCH промахивается, бонуса нет
    assert.equal(yandexRegDealPrice(0.69), 0);
    assert.equal(yandexRegDealPrice(0.7), 0);      // C4 → D4 = 0
    assert.equal(yandexRegDealPrice(0.79), 0);
    assert.equal(yandexRegDealPrice(0.8), 200);    // C5 → D5
    assert.equal(yandexRegDealPrice(0.89), 200);
    assert.equal(yandexRegDealPrice(0.9), 240);    // C6 → D6
    assert.equal(yandexRegDealPrice(1), 280);      // C7 → D7
    assert.equal(yandexRegDealPrice(1.1), 320);    // C8 → D8
    assert.equal(yandexRegDealPrice(1.2), 360);    // C9 → D9 «120%+»
    assert.equal(yandexRegDealPrice(3), 360);
});

test('шкала удержания по качеству повторяет вспомогательную таблицу', () => {
    assert.equal(yandexRegQualityWithholdRate(0), 0.5);     // «74% и ниже»
    assert.equal(yandexRegQualityWithholdRate(74), 0.5);
    assert.equal(yandexRegQualityWithholdRate(75), 0.4);    // 75-80%
    assert.equal(yandexRegQualityWithholdRate(79.9), 0.4);
    assert.equal(yandexRegQualityWithholdRate(80), 0.3);    // 80-85%
    assert.equal(yandexRegQualityWithholdRate(86), 0.2);    // 86-90%
    assert.equal(yandexRegQualityWithholdRate(91), 0.1);    // 91-95%
    assert.equal(yandexRegQualityWithholdRate(96), 0);      // 96-100%
    assert.equal(yandexRegQualityWithholdRate(100), 0);
});

test('конверсия ниже 80% плана оставляет только оклад', () => {
    const r = calculateYandexRegSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        requests: 100,
        successes: 35,             // 35% / 45% = 77,8% плана
        quality: 100,
    });

    assert.equal(Number(r.planPercent.toFixed(3)), 0.778);
    assert.equal(r.dealPrice, 0);
    assert.equal(r.bonusDeals, 0);
    assert.equal(r.finalSalary, 105600);
});

test('готовую конверсию можно передать вместо заявок', () => {
    const r = calculateYandexRegSalary({
        hoursWorked: 100,
        hoursNorm: 176,
        factConversion: 0.54,      // 54% при цели 45% → 120% плана
        successes: 10,
        quality: 96,
    });

    assert.equal(r.factConversion, 0.54);
    assert.equal(r.planPercent, 1.2);
    assert.equal(r.dealPrice, 360);
    assert.equal(r.qualityWithholdRate, 0);
    assert.equal(r.bonusDeals, 3600);
    assert.equal(r.finalSalary, 100 * 600 + 3600);
});

test('штрафы вычитаются, премии прибавляются', () => {
    const r = calculateYandexRegSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        requests: 100,
        successes: 50,             // 50% / 45% = 111% плана → 320 ₸
        quality: 96,
        fines: 5000,
        bonuses: 3000,
    });

    assert.equal(r.dealPrice, 320);
    assert.equal(r.bonusDeals, 16000);
    assert.equal(r.qualityWithheld, 0);
    assert.equal(r.finalSalary, 105600 + 16000 - 5000 + 3000);
});

test('пустые поля не ломают расчёт и не дают NaN', () => {
    const r = calculateYandexRegSalary({});
    assert.equal(r.oklad, 0);
    assert.equal(r.factConversion, 0);
    assert.equal(r.planPercent, 0);
    assert.equal(r.bonusDeals, 0);
    assert.equal(r.finalSalary, 0);
    assert.equal(r.hourlyRate, YANDEX_REG_HOURLY_RATE);
    assert.equal(r.targetConversion, YANDEX_REG_TARGET_CONVERSION);
    assert.equal(r.model, 'op_yandex_reg');
});
