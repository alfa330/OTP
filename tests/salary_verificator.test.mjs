import test from 'node:test';
import assert from 'node:assert/strict';

import {
    VERIFICATOR_HOURLY_RATE,
    VERIFICATOR_PLAN_PER_FTE,
    calculateVerificatorMonthlyPlan,
    calculateVerificatorSalary,
    verificatorChatPoints,
    verificatorPlanPoints,
    verificatorQualityPoints,
} from '../src/utils/salaryFormula.js';

// Эталон — файл заказчика из задачи #296, лист «Верик»: план на 1 FTE = 440 продаж
// (H5), норма 1 FTE = 176 ч (J5), ставка 500 ₸/ч (K13). Шкал баллов три:
// качество (F24), выполнение плана (G24) и «чаты/час» (H24).
const PLAN_PER_FTE = 440;
const NORM_FTE = 176;

test('строка «Новичок» из файла заказчика сходится до тенге', () => {
    // B7=176 ч, D7=300 продаж, план ×0,8 → 352; C25=88% качества, E25=22 чата/час.
    const r = calculateVerificatorSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        sales: 300,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        newbie: true,
        quality: 88,
        chatsPerHour: 22,
    });

    assert.equal(r.planTarget, 352);                              // C7 = 440/176*176*0,8
    assert.equal(Number(r.planPercent.toFixed(6)), 0.852273);     // E7 = 300/352
    assert.equal(r.qualityPoints, 20);                            // F25 — ступень 85–89,9%
    assert.equal(Number(r.planPoints.toFixed(6)), 85.227273);     // G25 = 0,8522…×100
    assert.equal(r.chatPoints, 15);                               // H25 — ступень 20–24,99
    assert.equal(r.totalBonusPercent, 120.22727272727273);        // I25 = 20 + 85,2273 + 15
    assert.equal(r.oklad, 88000);                                 // K25 = 500 × 176
    assert.equal(r.bonusTotal, 105800);                           // L25 = 88 000 × 120,2273 / 100
    assert.equal(r.finalSalary, 193800);                          // O25
});

test('строка «Ночник» из файла заказчика сходится до тенге', () => {
    // B8=176 ч, D8=198 продаж, план ночной ÷2 → 220; C26=91% качества, E26=27 чатов/час.
    const r = calculateVerificatorSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        sales: 198,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        nightShift: true,
        quality: 91,
        chatsPerHour: 27,
    });

    assert.equal(r.planTarget, 220);                              // C8 = 220/176*176
    assert.equal(r.planPercent, 0.9);                             // E8 = 198/220
    assert.equal(r.qualityPoints, 30);                            // F26 — ступень 90–94,9%
    assert.equal(r.planPoints, 90);                               // G26 = 0,9×100
    assert.equal(r.chatPoints, 25);                               // H26 — ступень «от 25»
    assert.equal(r.totalBonusPercent, 145);                       // I26 = 30 + 90 + 25
    assert.equal(r.bonusTotal, 127600);                           // L26 = 88 000 × 145 / 100
    assert.equal(r.finalSalary, 215600);                          // O26
});

test('строка «Оператор со стажем» сходится при её же проценте плана', () => {
    // В файле заказчика D24 (% выполнения) вбит РУКАМИ как 0,93 — на ссылку E6
    // (383/440 = 87,05%) он не опирается, в отличие от строк «Новичок» и «Ночник».
    // Поэтому итог 218 240 ₸ воспроизводится только при проценте плана 93%.
    const r = calculateVerificatorSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        sales: 409.2,
        planTarget: 440,
        quality: 97,
        chatsPerHour: 11,
    });

    assert.equal(r.qualityPoints, 50);                            // F24 — ступень 95–100%
    assert.equal(r.chatPoints, 5);                                // H24 — ступень 10–14,99
    assert.equal(Number(r.planPoints.toFixed(2)), 93);            // G24 = 0,93×100
    assert.equal(Number(r.totalBonusPercent.toFixed(2)), 148);    // I24 = 50 + 93 + 5
    assert.equal(r.bonusTotal, 130240);                           // L24
    assert.equal(r.finalSalary, 218240);                          // O24
});

test('тот же оператор от фактических продаж даёт 87,05% плана, а не 93%', () => {
    // Считаем так, как задумано ШАГОМ 1 файла: % выполнения = факт ÷ план.
    const r = calculateVerificatorSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        sales: 383,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        quality: 97,
        chatsPerHour: 11,
    });

    assert.equal(r.planTarget, 440);
    assert.equal(Number(r.planPercent.toFixed(6)), 0.870455);
    assert.equal(Number(r.planPoints.toFixed(4)), 87.0455);
    assert.equal(Math.round(r.bonusTotal), 125000);
    assert.equal(Math.round(r.finalSalary), 213000);
});

test('шкала баллов за качество повторяет подписи диапазонов', () => {
    assert.equal(verificatorQualityPoints(0), 5);        // 0–79,9%
    assert.equal(verificatorQualityPoints(79.99), 5);
    assert.equal(verificatorQualityPoints(80), 15);      // 80–84,9%
    assert.equal(verificatorQualityPoints(84.99), 15);
    assert.equal(verificatorQualityPoints(85), 20);      // 85–89,9%
    assert.equal(verificatorQualityPoints(89.99), 20);
    assert.equal(verificatorQualityPoints(90), 30);      // 90–94,9%
    assert.equal(verificatorQualityPoints(94.99), 30);
    assert.equal(verificatorQualityPoints(95), 50);      // 95–100%
    assert.equal(verificatorQualityPoints(100), 50);
});

test('шкала баллов за «чаты/час» повторяет подписи диапазонов', () => {
    assert.equal(verificatorChatPoints(0), 0);           // 0–9,9
    assert.equal(verificatorChatPoints(9.99), 0);
    assert.equal(verificatorChatPoints(10), 5);          // 10–14,99
    assert.equal(verificatorChatPoints(14.99), 5);
    assert.equal(verificatorChatPoints(15), 10);         // 15–19,99
    assert.equal(verificatorChatPoints(19.99), 10);
    assert.equal(verificatorChatPoints(20), 15);         // 20–24,99
    assert.equal(verificatorChatPoints(24.99), 15);
    assert.equal(verificatorChatPoints(25), 25);         // от 25
    assert.equal(verificatorChatPoints(40), 25);
});

test('баллы за план: пропорционально, кроме плоского коридора 100–110%', () => {
    assert.equal(verificatorPlanPoints(0), 0);
    assert.equal(verificatorPlanPoints(0.5), 50);
    assert.equal(verificatorPlanPoints(0.87), 87);
    assert.equal(verificatorPlanPoints(0.999), 99.9);
    // Коридор включительно с двух сторон — E15.
    assert.equal(verificatorPlanPoints(1), 100);
    assert.equal(verificatorPlanPoints(1.05), 100);
    assert.equal(verificatorPlanPoints(1.1), 100);
    // Свыше 110% — снова пропорционально (пример из подписи: 122% = 122 балла).
    assert.equal(Number(verificatorPlanPoints(1.101).toFixed(1)), 110.1);
    assert.equal(verificatorPlanPoints(1.22), 122);
    assert.equal(verificatorPlanPoints(2), 200);
});

test('план ставки 0,75 совпадает со схемой (330 продаж)', () => {
    // 1,0 → 440, 0,75 → 330, 0,5 → 220.
    const full = calculateVerificatorMonthlyPlan({ planPerFte: PLAN_PER_FTE, normHoursFte: NORM_FTE, hoursWorked: 176 });
    const threeQuarters = calculateVerificatorMonthlyPlan({ planPerFte: PLAN_PER_FTE, normHoursFte: NORM_FTE, hoursWorked: 132 });
    const half = calculateVerificatorMonthlyPlan({ planPerFte: PLAN_PER_FTE, normHoursFte: NORM_FTE, hoursWorked: 88 });

    assert.equal(full.plan, 440);
    assert.equal(threeQuarters.plan, 330);
    assert.equal(half.plan, 220);
});

test('ночная смена уполовинивает план на 1 FTE (H6 = H5/2)', () => {
    const night = calculateVerificatorMonthlyPlan({
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        hoursWorked: 176,
        nightShift: true,
    });
    assert.equal(night.planPerFte, 220);
    assert.equal(night.plan, 220);
});

test('новичок в ночную смену получает оба коэффициента', () => {
    // Такой комбинации в файле заказчика НЕТ (коэффициент 0,8 вписан прямо в
    // формулу строки «Новичок», а ночной план — в строку «Ночник»). Наш расчёт
    // применяет оба: план ночи 220, затем ×0,8. Тест закрепляет это поведение,
    // чтобы оно не поехало молча.
    const plan = calculateVerificatorMonthlyPlan({
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        hoursWorked: 176,
        newbie: true,
        nightShift: true,
    });
    assert.equal(plan.planPerFte, 220);
    assert.equal(plan.plan, 176);
});

test('перевыполнение выше 110% снова растёт пропорционально', () => {
    // 550 продаж при плане 440 — это 125%, значит 125 баллов за план, а не 100.
    const r = calculateVerificatorSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        sales: 550,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        quality: 96,
        chatsPerHour: 30,
    });
    assert.equal(r.planPercent, 1.25);
    assert.equal(r.planPoints, 125);
    assert.equal(r.totalBonusPercent, 200);      // 50 + 125 + 25
    assert.equal(r.bonusTotal, 176000);          // 88 000 × 200%
    assert.equal(r.finalSalary, 264000);
});

test('на границе коридора выплата не падает при росте выполнения', () => {
    // Проверяем монотонность вокруг 110%: коридор не должен создавать провал.
    const at = (ratio) => calculateVerificatorSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        sales: 440 * ratio,
        planTarget: 440,
        quality: 96,
        chatsPerHour: 30,
    }).finalSalary;
    assert.ok(at(0.99) < at(1.0));
    // Коридор ПЛОСКИЙ: 100% и 110% обязаны дать одну и ту же выплату. Нестрогое
    // «<=» здесь прошло бы и без коридора вовсе, поэтому сверяем равенство.
    assert.equal(at(1.0), at(1.1));
    assert.equal(at(1.05), at(1.1));
    assert.ok(at(1.1) < at(1.15));
});

test('штрафы могут увести итог в минус — потолка снизу в файле нет', () => {
    const r = calculateVerificatorSalary({
        hoursWorked: 10,
        hoursNorm: 176,
        sales: 0,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        quality: 50,
        chatsPerHour: 0,
        promoFines: 5000,
        fines: 5000,
    });
    assert.equal(r.oklad, 5000);                 // 10 ч × 500
    assert.equal(r.totalBonusPercent, 5);        // качество 50% — нижняя ступень
    assert.equal(r.bonusTotal, 250);
    assert.equal(r.finalSalary, -4750);          // O24 не ограничен нулём
});

test('разложение бонуса сходится с L24 и не уходит в минус', () => {
    // Слагаемые в UI показываются отдельно, а в файле заказчика денежная колонка
    // одна (L24). Проверяем на широкой сетке: каждое слагаемое неотрицательно
    // (иначе на экране «Бонус за чаты −0,00 ₸») и сумма сходится с L24 до копейки.
    let checked = 0;
    for (const hours of [0, 88, 132, 168, 176, 198]) {
        for (const quality of [0, 79.99, 80, 85, 90, 94.99, 95, 100]) {
            for (const chatsPerHour of [0, 9.99, 10, 15, 20, 25, 40]) {
                for (const sales of [0, 137, 300, 383, 440, 550]) {
                    const r = calculateVerificatorSalary({
                        hoursWorked: hours,
                        hoursNorm: 176,
                        sales,
                        planPerFte: PLAN_PER_FTE,
                        normHoursFte: NORM_FTE,
                        quality,
                        chatsPerHour,
                    });
                    checked += 1;
                    assert.ok(r.bonusQuality >= 0, `бонус за качество ушёл в минус: ${r.bonusQuality}`);
                    assert.ok(r.bonusPlan >= 0, `бонус за план ушёл в минус: ${r.bonusPlan}`);
                    assert.ok(r.bonusChats >= 0, `бонус за чаты ушёл в минус: ${r.bonusChats}`);
                    const sum = r.bonusQuality + r.bonusPlan + r.bonusChats;
                    assert.ok(
                        Math.abs(sum - r.bonusTotal) < 0.005,
                        `слагаемые ${sum} разошлись с L24 ${r.bonusTotal}`,
                    );
                }
            }
        }
    }
    assert.equal(checked, 6 * 8 * 7 * 6);
});

test('поля приходят из формы строками — расчёт от этого не меняется', () => {
    // Компонент хранит ввод в состоянии как строки и передаёт их как есть.
    const asStrings = calculateVerificatorSalary({
        hoursWorked: '176', hoursNorm: '176', hourlyRate: '500', sales: '198',
        planPerFte: '440', normHoursFte: '176', quality: '91', chatsPerHour: '27',
        promoFines: '0', fines: '0', nightShift: true,
    });
    const asNumbers = calculateVerificatorSalary({
        hoursWorked: 176, hoursNorm: 176, hourlyRate: 500, sales: 198,
        planPerFte: 440, normHoursFte: 176, quality: 91, chatsPerHour: 27,
        promoFines: 0, fines: 0, nightShift: true,
    });
    assert.equal(asStrings.finalSalary, asNumbers.finalSalary);
    assert.equal(asStrings.finalSalary, 215600);
    assert.equal(asStrings.totalBonusPercent, 145);
});

test('пустое качество — это «оценок нет», а введённый ноль — нижняя ступень', () => {
    const common = {
        hoursWorked: 176, hoursNorm: 176, sales: 0,
        planPerFte: PLAN_PER_FTE, normHoursFte: NORM_FTE, chatsPerHour: 0,
    };
    // Пустая строка из формы и отсутствие оценок за месяц (null) — баллов нет.
    for (const empty of ['', null, undefined]) {
        const r = calculateVerificatorSalary({ ...common, quality: empty });
        assert.equal(r.qualityKnown, false, `качество ${JSON.stringify(empty)} должно быть «не задано»`);
        assert.equal(r.qualityPoints, 0);
        assert.equal(r.bonusTotal, 0);
        assert.equal(r.finalSalary, 88000);          // только сумма за часы
    }
    // А настоящий ноль — это ступень «0–79,9%» из файла заказчика: 5 баллов.
    const zero = calculateVerificatorSalary({ ...common, quality: 0 });
    assert.equal(zero.qualityKnown, true);
    assert.equal(zero.qualityPoints, 5);
    assert.equal(zero.bonusTotal, 4400);             // 88 000 × 5%
    assert.equal(zero.finalSalary, 92400);
});

test('штраф за акции и обычный штраф вычитаются раздельно', () => {
    const r = calculateVerificatorSalary({
        hoursWorked: 176,
        hoursNorm: 176,
        sales: 440,
        planPerFte: PLAN_PER_FTE,
        normHoursFte: NORM_FTE,
        quality: 100,
        chatsPerHour: 25,
        promoFines: 1000,
        fines: 500,
    });

    assert.equal(r.planPoints, 100);                              // ровно 100% плана — коридор
    assert.equal(r.totalBonusPercent, 175);                       // 50 + 100 + 25
    assert.equal(r.bonusTotal, 154000);                           // 88 000 × 175%
    assert.equal(r.promoFines, 1000);
    assert.equal(r.fines, 500);
    assert.equal(r.finalSalary, 88000 + 154000 - 1000 - 500);
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
    // Качество не передано — это «оценок нет», а не «качество ноль»: баллов не
    // начисляем вовсе, иначе пустой калькулятор обещал бы 5% бонуса.
    assert.equal(r.qualityKnown, false);
    assert.equal(r.qualityPoints, 0);
    assert.equal(r.chatPoints, 0);
    assert.equal(r.planPoints, 0);
    assert.equal(r.totalBonusPercent, 0);
});

test('план на 1 FTE по умолчанию — 440 продаж', () => {
    assert.equal(VERIFICATOR_PLAN_PER_FTE, 440);
    const r = calculateVerificatorMonthlyPlan({ hoursWorked: 176, normHoursFte: 176 });
    assert.equal(r.plan, 440);
});
