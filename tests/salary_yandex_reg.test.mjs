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
// «KPI_логика_и_калькулятор_ЗП.xlsx» (лист «Калькулятор ЗП»):
// оплата в час 600 ₸, целевая конверсия 50%, пример — 41 успешная заявка из 100.

test('промежуточные шаги примера владельца сходятся один в один', () => {
    const r = calculateYandexRegSalary({
        hoursWorked: 176,          // B5
        hoursNorm: 176,
        groupRequests: 100,        // B6
        groupSuccesses: 41,        // B7
        quality: 93,               // B8 = 0,93
        deals: 60,                 // успешки самого оператора
    });

    assert.equal(r.factConversion, 0.41);                         // B11 = 41/100
    assert.equal(r.targetConversion, 0.5);                        // B12
    assert.equal(Number(r.planPercent.toFixed(6)), 0.82);         // B13 = 0,41/0,5
    assert.equal(r.dealPrice, 200);                               // B14 — ступень 80–90%
    assert.equal(r.qualityWithholdRate, 0.1);                     // B15 — качество 91–95%
    assert.equal(r.oklad, 105600);                                // B17 = 600 × 176
});

test('бонус считается за каждую успешную заявку оператора', () => {
    // Решение владельца: «Сумма бонуса» из KPI.xlsx — цена за одну успешку, как
    // «Сумма за успешку» у «Основы», а не фиксированная выплата за месяц.
    const r = calculateYandexRegSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        groupRequests: 100,
        groupSuccesses: 41,
        quality: 93,
        deals: 60,
    });

    assert.equal(r.bonusDeals, 12000);                            // 60 × 200
    assert.equal(r.qualityWithheld, 1200);                        // 12 000 × 0,1
    assert.equal(r.finalSalary, 116400);                          // 105 600 + 12 000 − 1 200
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
        groupRequests: 100,
        groupSuccesses: 39,        // 39% / 50% = 78% плана
        quality: 100,
        deals: 80,
    });

    assert.equal(Number(r.planPercent.toFixed(6)), 0.78);
    assert.equal(r.dealPrice, 0);
    assert.equal(r.bonusDeals, 0);
    assert.equal(r.finalSalary, 105600);
});

test('готовую конверсию можно передать вместо заявок группы', () => {
    const r = calculateYandexRegSalary({
        hoursWorked: 100,
        hoursNorm: 176,
        factConversion: 0.6,       // 60% при цели 50% → 120% плана
        deals: 10,
        quality: 96,
    });

    assert.equal(r.factConversion, 0.6);
    assert.equal(Number(r.planPercent.toFixed(6)), 1.2);
    assert.equal(r.dealPrice, 360);
    assert.equal(r.qualityWithholdRate, 0);
    assert.equal(r.bonusDeals, 3600);
    assert.equal(r.finalSalary, 100 * 600 + 3600);
});

test('штрафы вычитаются, премии прибавляются', () => {
    const r = calculateYandexRegSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        groupRequests: 100,
        groupSuccesses: 55,        // 110% плана → 320 ₸
        deals: 50,
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
