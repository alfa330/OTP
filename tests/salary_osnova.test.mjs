import test from 'node:test';
import assert from 'node:assert/strict';

import {
    OSNOVA_DEAL_PRICE_STEPS,
    OSNOVA_HOURLY_RATE,
    OSNOVA_NORM_HOURS_FTE,
    calculateOsnovaMonthlyPlan,
    calculateOsnovaSalary,
    osnovaDealPrice,
    opFteNormHoursForMonth,
    osnovaQualityWithholdRate,
} from '../src/utils/salaryFormula.js';

// Входные строки — файл владельца «Основа_калькулятор зарплаты.xlsx», лист «Основа»:
// план на 1 FTE (день) = 691 сделка, норма 1 FTE = 176 ч, оплата 600 ₸/ч.
// Цена сделки — уже по новой шкале п. 3.1 (11.11.2026), поэтому бонус и итог
// отличаются от N24/N25 файла: там ещё ступени 100/200/400/450/500/600 ₸.
const PLAN_PER_FTE = 691;

test('строка «Оператор со стажем»: ровно 100% плана — 800 ₸ за продажу', () => {
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
    assert.equal(r.dealPrice, 800);           // ступень 100–119,9%
    assert.equal(r.oklad, 105600);            // I24 = 176 × 600
    assert.equal(r.bonusDeals, 552800);       // 691 × 800
    assert.equal(r.qualityWithholdRate, 0);   // C24: качество 98 ≥ 96
    assert.equal(r.qualityWithheld, 0);
    assert.equal(r.finalSalary, 658400);      // 105 600 + 552 800
});

test('строка «Новичок»: 54% плана — 500 ₸ за продажу, удержание 50% за качество', () => {
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
    assert.equal(r.dealPrice, 500);                              // ступень 50–79,9%
    assert.equal(r.bonusDeals, 150000);                          // 300 × 500
    assert.equal(r.qualityWithholdRate, 0.5);                    // C25: 70 < 74
    assert.equal(r.qualityWithheld, 75000);                      // 150 000 × 0,5
    assert.equal(r.finalSalary, 180600);                         // 105 600 + 150 000 − 75 000
});

test('ставка за продажу — ступени п. 3.1 мотивационной шкалы', () => {
    // Каждая граница документа: последнее значение ступени и первое следующей.
    const cases = [
        [0, 200], [0.499, 200], [0.4999, 200],
        [0.5, 500], [0.799, 500],
        [0.8, 700], [0.999, 700], [0.9999, 700],
        [1, 800], [1.199, 800],
        [1.2, 900], [1.399, 900],
        [1.4, 1000], [1.599, 1000],
        [1.6, 1100], [1.799, 1100],
        [1.8, 1200], [1.999, 1200],
        [2, 1300], [5, 1300],          // выше 200% ставка не растёт
    ];
    for (const [ratio, price] of cases) {
        assert.equal(osnovaDealPrice(ratio), price, `${ratio * 100}% плана → ${price} ₸`);
    }
    // Пустое и мусор — как 0% плана (нижняя ступень), без NaN.
    assert.equal(osnovaDealPrice(''), 200);
    assert.equal(osnovaDealPrice(undefined), 200);
});

test('шкала ставок — девять ступеней документа, подписи как в п. 3.1', () => {
    assert.deepEqual(
        OSNOVA_DEAL_PRICE_STEPS.map((s) => [s.label, s.price]),
        [
            ['0–49,9%', 200],
            ['50–79,9%', 500],
            ['80–99,9%', 700],
            ['100–119,9%', 800],
            ['120–139,9%', 900],
            ['140–159,9%', 1000],
            ['160–179,9%', 1100],
            ['180–199,9%', 1200],
            ['200% и выше', 1300],
        ],
    );
    // Границы идут по возрастанию — иначе цикл выбора ступени ошибётся молча.
    OSNOVA_DEAL_PRICE_STEPS.forEach((s, i, all) => {
        if (i > 0) assert.ok(s.from > all[i - 1].from, `ступень ${s.label} выше предыдущей`);
    });
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
    assert.equal(r.dealPrice, 1300);
    assert.equal(r.finalSalary, 105600 + 691 * 1300);
});

test('норма часов на 1 FTE зависит от месяца: 31 день → 176 ч, 30 → 168 ч', () => {
    assert.equal(opFteNormHoursForMonth('2026-07'), 176); // 31 день → 22 р.д.
    assert.equal(opFteNormHoursForMonth('2026-06'), 168); // 30 дней → 21 р.д.
    assert.equal(opFteNormHoursForMonth('2026-02'), 160); // 28 дней → 20 р.д.
    assert.equal(opFteNormHoursForMonth(''), OSNOVA_NORM_HOURS_FTE);
    assert.equal(opFteNormHoursForMonth('мусор'), OSNOVA_NORM_HOURS_FTE);
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

test('без плана процент не делится на ноль — продажи идут по нижней ступени', () => {
    const r = calculateOsnovaSalary({ hoursWorked: 176, hoursNorm: 176, deals: 500, planPerFte: 0, quality: 100 });
    assert.equal(r.planTarget, 0);
    assert.equal(r.planPercent, 0);
    assert.equal(r.dealPrice, 200);          // ступень 0–49,9%
    assert.equal(r.bonusDeals, 100000);      // 500 × 200
    assert.ok(Number.isFinite(r.finalSalary));
});
