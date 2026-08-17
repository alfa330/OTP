import test from 'node:test';
import assert from 'node:assert/strict';

import {
    OSNOVA_HOURLY_RATE,
    OSNOVA_NORM_HOURS_FTE,
    calculateOsnovaMonthlyPlan,
    calculateOsnovaSalary,
    osnovaDealPrice,
    osnovaNormHoursForMonth,
    osnovaQualityWithholdRate,
} from '../src/utils/salaryFormula.js';

// Эталон — файл владельца «Основа_калькулятор зарплаты.xlsx», лист «Основа»:
// план на 1 FTE (день) = 691 сделка, норма 1 FTE = 176 ч, оплата 600 ₸/ч.
const PLAN_PER_FTE = 691;

test('строка «Оператор со стажем» из таблицы владельца сходится до тенге', () => {
    // B5=691 сделка, C5=176 ч, B24=98% качества, штрафов нет.
    const r = calculateOsnovaSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        deals: 691,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: OSNOVA_NORM_HOURS_FTE,
        quality: 98,
    });

    assert.equal(r.planTarget, 691);          // D5 = 691/176*176
    assert.equal(r.planPercent, 1);           // E5 = 100%
    assert.equal(r.dealPrice, 450);           // J24: ровно 100% → ступень 100–120%
    assert.equal(r.oklad, 105600);            // I24 = 176 × 600
    assert.equal(r.bonusDeals, 310950);       // K24 = 691 × 450
    assert.equal(r.qualityWithholdRate, 0);   // C24: качество 98 ≥ 96
    assert.equal(r.qualityWithheld, 0);       // L24
    assert.equal(r.finalSalary, 416550);      // N24
});

test('строка «Новичок» из таблицы владельца сходится до тенге', () => {
    // B6=300 сделок, C6=176 ч, план ×0,8, B25=70% качества → удержание 50%.
    const r = calculateOsnovaSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        deals: 300,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: OSNOVA_NORM_HOURS_FTE,
        newbie: true,
        quality: 70,
    });

    assert.equal(Number(r.planTarget.toFixed(4)), 552.8);        // D6 = 691/176*176*0,8
    assert.equal(Number(r.planPercent.toFixed(6)), 0.542692);    // E6
    assert.equal(r.dealPrice, 200);                              // 50% ≤ % < 80% → 200 ₸
    assert.equal(r.bonusDeals, 60000);                           // K25 = 300 × 200
    assert.equal(r.qualityWithholdRate, 0.5);                    // C25: 70 < 74
    assert.equal(r.qualityWithheld, 30000);                      // L25
    assert.equal(r.finalSalary, 135600);                         // N25
});

test('ступени цены сделки повторяют формулу J24, а не подписи слайдов', () => {
    assert.equal(osnovaDealPrice(0), 100);
    assert.equal(osnovaDealPrice(0.4999), 100);
    assert.equal(osnovaDealPrice(0.5), 200);
    assert.equal(osnovaDealPrice(0.7999), 200);
    assert.equal(osnovaDealPrice(0.8), 400);
    assert.equal(osnovaDealPrice(0.9999), 400);
    // Ровно 100% плана — это ещё 450 ₸, а не 500: в J24 стоит E24<120%.
    assert.equal(osnovaDealPrice(1), 450);
    assert.equal(osnovaDealPrice(1.1999), 450);
    assert.equal(osnovaDealPrice(1.2), 500);
    assert.equal(osnovaDealPrice(1.3999), 500);
    assert.equal(osnovaDealPrice(1.4), 600);
    // Выше 140% цена не растёт — в таблице 160% и 180% тоже 600 ₸.
    assert.equal(osnovaDealPrice(3), 600);
});

test('шкала удержания за качество берёт границы из формулы C24', () => {
    assert.equal(osnovaQualityWithholdRate(0), 0.5);     // пустое поле = 0 → как в таблице
    assert.equal(osnovaQualityWithholdRate(73.9), 0.5);
    assert.equal(osnovaQualityWithholdRate(74), 0.4);
    assert.equal(osnovaQualityWithholdRate(79.9), 0.4);
    assert.equal(osnovaQualityWithholdRate(80), 0.3);
    assert.equal(osnovaQualityWithholdRate(85.9), 0.3);
    assert.equal(osnovaQualityWithholdRate(86), 0.2);
    assert.equal(osnovaQualityWithholdRate(90.9), 0.2);
    assert.equal(osnovaQualityWithholdRate(91), 0.1);
    assert.equal(osnovaQualityWithholdRate(95.9), 0.1);
    assert.equal(osnovaQualityWithholdRate(96), 0);
    assert.equal(osnovaQualityWithholdRate(100), 0);
});

test('план считается от фактических часов, а не от ставки', () => {
    // Недоработка: 88 ч из 176 → план ровно вдвое меньше плана на 1 FTE.
    const half = calculateOsnovaMonthlyPlan({ planPerFte: PLAN_PER_FTE, hoursWorked: 88 });
    assert.equal(half.plan, 345.5);

    // Переработка: 200 ч → план выше 1 FTE (в таблице формула та же, без потолка).
    const over = calculateOsnovaMonthlyPlan({ planPerFte: PLAN_PER_FTE, hoursWorked: 200 });
    assert.equal(Number(over.plan.toFixed(4)), Number((691 / 176 * 200).toFixed(4)));
});

test('ночная смена делит план на 1 FTE пополам (H6 = H5/2)', () => {
    const night = calculateOsnovaMonthlyPlan({ planPerFte: PLAN_PER_FTE, hoursWorked: 176, nightShift: true });
    assert.equal(night.planPerFte, 345.5);
    assert.equal(night.plan, 345.5);

    // Те же 691 сделка ночью — это уже 200% плана, цена сделки максимальная.
    const r = calculateOsnovaSalary({
        hoursWorked: 176, hoursNorm: 176, deals: 691,
        planPerFte: PLAN_PER_FTE, nightShift: true, quality: 100,
    });
    assert.equal(r.planPercent, 2);
    assert.equal(r.dealPrice, 600);
    assert.equal(r.finalSalary, 105600 + 691 * 600);
});

test('норма часов на 1 FTE зависит от месяца: 31 день → 176 ч, 30 → 168 ч', () => {
    assert.equal(osnovaNormHoursForMonth('2026-07'), 176); // 31 день → 22 р.д.
    assert.equal(osnovaNormHoursForMonth('2026-06'), 168); // 30 дней → 21 р.д.
    assert.equal(osnovaNormHoursForMonth('2026-02'), 160); // 28 дней → 20 р.д.
    assert.equal(osnovaNormHoursForMonth(''), OSNOVA_NORM_HOURS_FTE);
    assert.equal(osnovaNormHoursForMonth('мусор'), OSNOVA_NORM_HOURS_FTE);
});

test('штрафы вычитаются из итога, премия прибавляется', () => {
    const base = {
        hoursWorked: 176, hoursNorm: 176, deals: 691,
        planPerFte: PLAN_PER_FTE, quality: 98,
    };
    const clean = calculateOsnovaSalary(base);
    const withFines = calculateOsnovaSalary({ ...base, fines: 5000, bonuses: 50000 });

    assert.equal(withFines.finalSalary, clean.finalSalary - 5000 + 50000);
    assert.equal(withFines.fines, 5000);
    assert.equal(withFines.bonuses, 50000);
});

test('оклад не зависит от продаж и качества — только часы × 600', () => {
    const r = calculateOsnovaSalary({ hoursWorked: 150, hoursNorm: 176, deals: 0, planPerFte: PLAN_PER_FTE, quality: 0 });
    assert.equal(r.oklad, 150 * OSNOVA_HOURLY_RATE);   // 90 000 ₸ — пример со слайда 5
    assert.equal(r.bonusDeals, 0);
    assert.equal(r.qualityWithheld, 0);                // удерживать не с чего
    assert.equal(r.finalSalary, 90000);
    assert.equal(Number(r.hoursPercentage.toFixed(4)), Number((150 / 176 * 100).toFixed(4)));
});

test('без плана бонус не начисляется, а не делится на ноль', () => {
    const r = calculateOsnovaSalary({ hoursWorked: 176, hoursNorm: 176, deals: 500, planPerFte: 0, quality: 100 });
    assert.equal(r.planTarget, 0);
    assert.equal(r.planPercent, 0);
    assert.equal(r.dealPrice, 100);          // ступень «ниже 50%»
    assert.equal(r.bonusDeals, 50000);       // 500 × 100 — как посчитала бы таблица
    assert.ok(Number.isFinite(r.finalSalary));
});
