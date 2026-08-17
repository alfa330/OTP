import test from 'node:test';
import assert from 'node:assert/strict';

import {
    POTOK_HOURLY_RATE,
    POTOK_PLAN_PER_FTE,
    calculatePotokMonthlyPlan,
    calculatePotokSalary,
    potokChurnDealPrice,
    potokFocusDealPrice,
} from '../src/utils/salaryFormula.js';

// Эталон — файл владельца «Поток_калькулятор_зарплаты.xlsx», лист «Поток»:
// план на 1 FTE = 150 продаж, норма 1 FTE = 168 ч, ставка 700 ₸/ч.
const PLAN_PER_FTE = 150;
const NORM_FTE = 168;

test('строка «Оператор» из таблицы владельца сходится до тенге', () => {
    // B5=150 Отток, C5=50 Фокус, E5=168 ч.
    const r = calculatePotokSalary({
        hoursWorked: 168,
        hoursNorm: 168,
        churnSales: 150,
        focusSales: 50,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
    });

    assert.equal(r.totalSales, 200);                              // D5 = СУММ(B5:C5)
    assert.equal(r.planTarget, 150);                              // F5 = 150/168*168
    assert.equal(Number(r.planPercent.toFixed(6)), 1.333333);     // G5 = 200/150
    assert.equal(r.oklad, 117600);                                // G28 = 700 × 168
    assert.equal(r.churnPrice, 550);                              // H28 — ступень 120–150%
    assert.equal(r.focusPrice, 950);                              // J28 — ступень 120–140%
    assert.equal(r.bonusChurn, 82500);                            // I28 = 150 × 550
    assert.equal(r.bonusFocus, 47500);                            // K28 = 50 × 950
    assert.equal(r.finalSalary, 247600);                          // N28
});

test('строка «Новичок» из таблицы владельца сходится до тенге', () => {
    // B6=120 Отток, C6=50 Фокус, план ×0,8.
    const r = calculatePotokSalary({
        hoursWorked: 168,
        hoursNorm: 168,
        churnSales: 120,
        focusSales: 50,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        newbie: true,
    });

    assert.equal(r.planTarget, 120);                              // F6 = 150/168*168*0,8
    assert.equal(Number(r.planPercent.toFixed(6)), 1.416667);     // G6 = 170/120
    assert.equal(r.churnPrice, 550);                              // всё ещё ступень 120–150%
    assert.equal(r.focusPrice, 1000);                             // уже ступень 140–160%
    assert.equal(r.bonusChurn, 66000);                            // I29 = 120 × 550
    assert.equal(r.bonusFocus, 50000);                            // K29 = 50 × 1000
    assert.equal(r.finalSalary, 233600);                          // N29
});

test('процент плана считается по сумме обоих потоков, а не по каждому отдельно', () => {
    // 100 Отток + 100 Фокус при плане 150 = 133%, хотя каждый поток по отдельности
    // дал бы 67% и цену сделки нижней ступени.
    const r = calculatePotokSalary({
        hoursWorked: 168, hoursNorm: 168,
        churnSales: 100, focusSales: 100,
        planPerFte: PLAN_PER_FTE, normHoursFte: NORM_FTE,
    });
    assert.equal(r.totalSales, 200);
    assert.equal(Number(r.planPercent.toFixed(6)), 1.333333);
    assert.equal(r.churnPrice, 550);
    assert.equal(r.focusPrice, 950);
    assert.notEqual(r.churnPrice, potokChurnDealPrice(100 / 150));
});

test('ступени «Оттока» берут границы из массива MATCH, а не из подписей', () => {
    assert.equal(potokChurnDealPrice(0), 200);
    assert.equal(potokChurnDealPrice(0.6999), 200);
    assert.equal(potokChurnDealPrice(0.7), 300);
    assert.equal(potokChurnDealPrice(0.8), 400);
    assert.equal(potokChurnDealPrice(0.9), 450);
    assert.equal(potokChurnDealPrice(1), 500);
    assert.equal(potokChurnDealPrice(1.1999), 500);
    assert.equal(potokChurnDealPrice(1.2), 550);
    // Подпись столбца говорит «120%–140%», но в формуле следующий порог — 150%.
    assert.equal(potokChurnDealPrice(1.4999), 550);
    assert.equal(potokChurnDealPrice(1.5), 600);
    assert.equal(potokChurnDealPrice(1.9999), 600);
    assert.equal(potokChurnDealPrice(2), 800);
    assert.equal(potokChurnDealPrice(5), 800);
});

test('ступени «Фокуса» доходят до 1400 ₸ на 200% и 1600 ₸ на 220%', () => {
    assert.equal(potokFocusDealPrice(0), 700);
    assert.equal(potokFocusDealPrice(0.6999), 700);
    assert.equal(potokFocusDealPrice(0.7), 750);
    assert.equal(potokFocusDealPrice(0.8), 800);
    assert.equal(potokFocusDealPrice(0.9), 850);
    assert.equal(potokFocusDealPrice(1), 900);
    assert.equal(potokFocusDealPrice(1.2), 950);
    assert.equal(potokFocusDealPrice(1.4), 1000);
    assert.equal(potokFocusDealPrice(1.6), 1100);
    assert.equal(potokFocusDealPrice(1.8), 1200);
    // В XLSX последний порог задан как 2 дважды, из-за чего 1400 ₸ недостижимы;
    // презентация читается однозначно — 200% → 1400, 220% → 1600.
    assert.equal(potokFocusDealPrice(2), 1400);
    assert.equal(potokFocusDealPrice(2.1999), 1400);
    assert.equal(potokFocusDealPrice(2.2), 1600);
    assert.equal(potokFocusDealPrice(10), 1600);
});

test('оклад — часы × ставку, ставка редактируется', () => {
    const base = calculatePotokSalary({ hoursWorked: 176, hoursNorm: 176 });
    assert.equal(base.oklad, 176 * POTOK_HOURLY_RATE);   // 123 200 ₸ — слайд «Точные цифры»
    assert.equal(calculatePotokSalary({ hoursWorked: 88, hoursNorm: 176 }).oklad, 61600);
    assert.equal(calculatePotokSalary({ hoursWorked: 132, hoursNorm: 176 }).oklad, 92400);

    const custom = calculatePotokSalary({ hoursWorked: 100, hoursNorm: 176, hourlyRate: 800 });
    assert.equal(custom.oklad, 80000);
    assert.equal(custom.hourlyRate, 800);
});

test('штрафы и удержание 50% вычитаются из итога', () => {
    const common = {
        hoursWorked: 168, hoursNorm: 168, churnSales: 150, focusSales: 50,
        planPerFte: PLAN_PER_FTE, normHoursFte: NORM_FTE,
    };
    const clean = calculatePotokSalary(common);
    const withDeductions = calculatePotokSalary({ ...common, fines: 3000, withholding: 20000 });

    assert.equal(withDeductions.finalSalary, clean.finalSalary - 3000 - 20000);
    assert.equal(withDeductions.fines, 3000);
    assert.equal(withDeductions.withholding, 20000);
});

test('план растёт от фактических часов и падает у новичка', () => {
    const half = calculatePotokMonthlyPlan({ planPerFte: PLAN_PER_FTE, normHoursFte: NORM_FTE, hoursWorked: 84 });
    assert.equal(half.plan, 75);

    const newbie = calculatePotokMonthlyPlan({ planPerFte: PLAN_PER_FTE, normHoursFte: NORM_FTE, hoursWorked: 168, newbie: true });
    assert.equal(newbie.plan, 120);
    assert.equal(newbie.newbieCoef, 0.8);
});

test('без плана бонус считается по нижним ступеням, без деления на ноль', () => {
    const r = calculatePotokSalary({
        hoursWorked: 168, hoursNorm: 168, churnSales: 10, focusSales: 10, planPerFte: 0,
    });
    assert.equal(r.planTarget, 0);
    assert.equal(r.planPercent, 0);
    assert.equal(r.churnPrice, 200);
    assert.equal(r.focusPrice, 700);
    assert.ok(Number.isFinite(r.finalSalary));
});

test('качество звонков на выплату «Потока» не влияет', () => {
    const r = calculatePotokSalary({
        hoursWorked: 168, hoursNorm: 168, churnSales: 150, focusSales: 50,
        planPerFte: PLAN_PER_FTE, normHoursFte: NORM_FTE, quality: 10,
    });
    assert.equal(r.finalSalary, 247600);
    assert.equal(r.qualityWithheld, undefined);
});
