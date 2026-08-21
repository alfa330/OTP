import test from 'node:test';
import assert from 'node:assert/strict';

import {
    VERIFICATOR_HOURLY_RATE,
    VERIFICATOR_PLAN_PER_FTE,
    calculateVerificatorMonthlyPlan,
    calculateVerificatorSalary,
    verificatorPlanBonusPercent,
} from '../src/utils/salaryFormula.js';

// Эталон — файл владельца «Верификаторы_калькулятор_зарплаты_1.xlsx», лист «Верик»:
// план на 1 FTE = 440 продаж, норма 1 FTE = 176 ч (K5), ставка 500 ₸/ч (I12).
const PLAN_PER_FTE = 440;
const NORM_FTE = 176;

test('строка «Оператор со стажем» из таблицы владельца сходится до тенге', () => {
    // C6=176 ч, E6=228 продаж, D22=96,2% качества, штрафов нет.
    const r = calculateVerificatorSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        sales: 228,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        quality: 96.2,
    });

    assert.equal(r.planTarget, 440);                              // D6 = 440/176*176
    assert.equal(Number(r.planPercent.toFixed(6)), 0.518182);     // F6 = 228/440
    assert.equal(r.planBonusPercent, 0);                          // F22 — ступень 0–79,9%
    assert.equal(r.oklad, 88000);                                 // H22 = 500 × 176
    assert.equal(r.totalBonusPercent, 96.2);                      // I22 = качество + премия
    assert.equal(r.bonusTotal, 84656);                            // J22 = 88000 × 96,2 / 100
    assert.equal(r.finalSalary, 172656);                          // M22
});

test('строка «Новичок» из таблицы владельца сходится до тенге', () => {
    // C7=176 ч, E7=300 продаж, план ×0,8, качество 92%, штраф 2045 ₸ (L23).
    const r = calculateVerificatorSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        sales: 300,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        newbie: true,
        quality: 92,
        fines: 2045,
    });

    assert.equal(r.planTarget, 352);                              // D7 = 440/176*176*0,8
    assert.equal(Number(r.planPercent.toFixed(6)), 0.852273);     // F7 = 300/352
    assert.equal(r.planBonusPercent, 5);                          // F23 — ступень 80–89,9%
    assert.equal(r.totalBonusPercent, 97);                        // I23 = 92 + 5
    assert.equal(r.bonusTotal, 85360);                            // J23 = 88000 × 97 / 100
    assert.equal(r.finalSalary, 171315);                          // M23 = 88000 + 85360 − 2045
});

test('шкала премии за план повторяет подписи диапазонов', () => {
    assert.equal(verificatorPlanBonusPercent(0), 0);        // 0–79,9%
    assert.equal(verificatorPlanBonusPercent(0.799), 0);
    assert.equal(verificatorPlanBonusPercent(0.8), 5);      // 80–89,9%
    assert.equal(verificatorPlanBonusPercent(0.899), 5);
    assert.equal(verificatorPlanBonusPercent(0.9), 10);     // 90–99,9%
    assert.equal(verificatorPlanBonusPercent(0.999), 10);
    assert.equal(verificatorPlanBonusPercent(1), 20);       // 100–109,9%
    assert.equal(verificatorPlanBonusPercent(1.099), 20);
    assert.equal(verificatorPlanBonusPercent(1.1), 30);     // 110%+
    assert.equal(verificatorPlanBonusPercent(5), 30);
});

test('план ставки 0,75 совпадает со слайдом презентации (330 продаж)', () => {
    // Слайд «Бонус за выполнение плана продаж»: 1,0 → 440, 0,75 → 330, 0,5 → 220.
    const full = calculateVerificatorMonthlyPlan({ planPerFte: PLAN_PER_FTE, normHoursFte: NORM_FTE, hoursWorked: 176 });
    const threeQuarters = calculateVerificatorMonthlyPlan({ planPerFte: PLAN_PER_FTE, normHoursFte: NORM_FTE, hoursWorked: 132 });
    const half = calculateVerificatorMonthlyPlan({ planPerFte: PLAN_PER_FTE, normHoursFte: NORM_FTE, hoursWorked: 88 });

    assert.equal(full.plan, 440);
    assert.equal(threeQuarters.plan, 330);
    assert.equal(half.plan, 220);
});

test('ночная смена уполовинивает план на 1 FTE (I6 = I5/2)', () => {
    const night = calculateVerificatorMonthlyPlan({
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        hoursWorked: 176,
        nightShift: true,
    });
    assert.equal(night.planPerFte, 220);
    assert.equal(night.plan, 220);
});

test('пример со слайда «Собираем зарплату вместе» разложен на два бонуса', () => {
    // Ставка 1,0, отработано 198 ч, качество 93%, план выполнен на 45,3% → 0% премии.
    const r = calculateVerificatorSalary({
        hoursWorked: 198,
        hoursNorm: 176,
        sales: 224,          // 224/495 = 45,3% плана
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        quality: 93,
        fines: 150,
    });

    assert.equal(r.oklad, 99000);                                 // 500 × 198
    assert.equal(Number(r.planPercent.toFixed(3)), 0.453);
    assert.equal(r.planBonusPercent, 0);
    assert.equal(r.bonusQuality, 92070);                          // 99 000 × 93%
    assert.equal(r.bonusPlan, 0);
    // Итог слайда (190 425 ₸) не сходится с его же слагаемыми: 99 000 + 92 070 − 150
    // даёт 190 920 ₸. Считаем по формуле, а не по подписи на слайде.
    assert.equal(r.finalSalary, 190920);
});

test('штраф за акции и обычный штраф вычитаются раздельно', () => {
    const r = calculateVerificatorSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        sales: 440,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        quality: 100,
        promoFines: 1000,
        fines: 500,
    });

    assert.equal(r.planBonusPercent, 20);                         // ровно 100% плана
    assert.equal(r.totalBonusPercent, 120);
    assert.equal(r.bonusTotal, 105600);                           // 88 000 × 120%
    assert.equal(r.promoFines, 1000);
    assert.equal(r.fines, 500);
    assert.equal(r.finalSalary, 88000 + 105600 - 1000 - 500);
});

test('пустые поля не ломают расчёт и не дают NaN', () => {
    const r = calculateVerificatorSalary({});
    assert.equal(r.oklad, 0);
    assert.equal(r.planTarget, 0);
    assert.equal(r.planPercent, 0);
    assert.equal(r.bonusTotal, 0);
    assert.equal(r.finalSalary, 0);
    assert.equal(r.hourlyRate, VERIFICATOR_HOURLY_RATE);
    assert.equal(r.model, 'op_verificator');
});

test('план на 1 FTE по умолчанию — 440 продаж', () => {
    assert.equal(VERIFICATOR_PLAN_PER_FTE, 440);
    const r = calculateVerificatorMonthlyPlan({ hoursWorked: 176, normHoursFte: 176 });
    assert.equal(r.plan, 440);
});
