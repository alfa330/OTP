// Чистые функции расчёта ЗП по моделям. Извлечены 1:1 из:
//   - App.jsx calculateSalaryByFormula (модель «оператор»/звонки)
//   - components/salary/SalaryCalculatorChat.jsx calculateSalary (модель «чат-менеджер»)
// НЕ объединять таблицы баллов — у моделей разные пороги/категории; изменение = изменение выплат.
// Ставка 700 ₸/час для ОБЕИХ моделей (подтверждено владельцем продукта).

export const SALARY_HOURLY_RATE = 700;

// Качество для превью ЗП приходит из месячного контракта часов, а не из
// лениво загружаемого журнала «Мои оценки». count отделяет «нет данных» от
// реальной средней оценки 0; month не даёт подмешать качество другого месяца.
export function resolveMonthlySalaryQuality(metrics, expectedMonth = '') {
    const metricsMonth = String(metrics?.month || '').trim();
    const normalizedExpectedMonth = String(expectedMonth || '').trim();
    const monthMatches = !normalizedExpectedMonth || metricsMonth === normalizedExpectedMonth;
    const rawCount = Number(metrics?.quality_evaluation_count);
    const count = Number.isFinite(rawCount) ? Math.max(0, Math.trunc(rawCount)) : 0;
    const hasAverageValue = (
        metrics?.quality_average !== null &&
        metrics?.quality_average !== undefined &&
        String(metrics.quality_average).trim() !== ''
    );
    const average = Number(metrics?.quality_average);
    const available = (
        monthMatches &&
        metrics?.quality_available === true &&
        count > 0 &&
        hasAverageValue &&
        Number.isFinite(average)
    );

    return {
        available,
        count: monthMatches ? count : 0,
        quality: available ? average : 0,
    };
}

// --- Модель ОПЕРАТОР (звонки) — дословно из App.jsx:34530-34577 ---
export function calculateOperatorSalary({
    hoursNorm = 0,
    totalHours = 0,
    quality = 0,
    callsPerHour = 0,
    experience = '',
    bonuses = 0,
} = {}) {
    const normalizedHoursNorm = parseFloat(hoursNorm) || 0;
    const normalizedTotalHours = parseFloat(totalHours) || 0;
    const normalizedQuality = parseFloat(quality) || 0;
    const normalizedCallsPerHour = parseFloat(callsPerHour) || 0;
    const normalizedBonuses = parseFloat(bonuses) || 0;

    let points = 0;

    if (experience === '16+') points += 50;
    else if (experience === '10-15') points += 35;
    else if (experience === '4-9') points += 25;
    else if (experience === '0-3') points += 15;

    if (normalizedQuality >= 99 && normalizedQuality <= 100) points += 50;
    else if (normalizedQuality >= 95 && normalizedQuality < 99) points += 30;
    else if (normalizedQuality >= 90 && normalizedQuality < 95) points += 25;
    else if (normalizedQuality >= 85 && normalizedQuality < 90) points += 20;

    if (normalizedCallsPerHour >= 20) points += 50;
    else if (normalizedCallsPerHour >= 15) points += 30;
    else if (normalizedCallsPerHour >= 10) points += 25;
    else if (normalizedCallsPerHour >= 5) points += 20;

    const hoursPercentage = normalizedHoursNorm > 0 ? (normalizedTotalHours / normalizedHoursNorm) * 100 : 0;
    const premiumCoefficient = hoursPercentage >= 90 ? 1 : 0.75;
    const baseSalary = SALARY_HOURLY_RATE * normalizedTotalHours;
    const premiumPart = baseSalary * (points / 100) * premiumCoefficient;
    const finalSalary = baseSalary + premiumPart + normalizedBonuses;

    return {
        model: 'call',
        points,
        premiumCoefficient,
        hoursNorm: normalizedHoursNorm,
        hoursWorked: normalizedTotalHours,
        hoursPercentage,
        baseSalary,
        premiumPart,
        bonuses: normalizedBonuses,
        finalSalary,
    };
}

// --- Модель ЧАТ-МЕНЕДЖЕР — дословно из SalaryCalculatorChat.jsx:65-146 ---
// responseTime ожидается в МИНУТАХ (пороги <= 2 … <= 4.5).
export function calculateChatSalary({
    hoursNorm = 0,
    totalHours = 0,
    quality = 0,
    avgScore = 0,
    responseTime = 0,
    chatsPerHour = 0,
    experience = '',
    bonuses = 0,
} = {}) {
    const hn = parseFloat(hoursNorm) || 0;
    const th = parseFloat(totalHours) || 0;
    const qual = parseFloat(quality) || 0;
    const score = parseFloat(avgScore) || 0;
    const respTime = parseFloat(responseTime) || 0;
    const cph = parseFloat(chatsPerHour) || 0;
    const normalizedBonuses = parseFloat(bonuses) || 0;

    let points = 0;

    // Experience points
    if (experience === '18+') points += 50;
    else if (experience === '13-17') points += 35;
    else if (experience === '10-12') points += 25;
    else if (experience === '6-9') points += 15;
    else if (experience === '3-5') points += 10;
    else if (experience === '0-2') points += 5;

    // Quality points
    if (qual >= 97 && qual <= 100) points += 25;
    else if (qual >= 94 && qual < 97) points += 20;
    else if (qual >= 90 && qual < 94) points += 15;
    else if (qual >= 86 && qual < 90) points += 10;
    else if (qual >= 80 && qual < 86) points += 5;

    // Avg score points
    if (score >= 4.9) points += 30;
    else if (score >= 4.8) points += 25;
    else if (score >= 4.7) points += 20;
    else if (score >= 4.6) points += 10;
    else if (score >= 4.5) points += 5;

    // Response time points (minutes)
    if (respTime <= 2) points += 20;
    else if (respTime <= 3) points += 15;
    else if (respTime <= 4) points += 10;
    else if (respTime <= 4.5) points += 5;

    // Chats per hour points
    if (cph >= 25) points += 25;
    else if (cph >= 20) points += 15;
    else if (cph >= 15) points += 10;
    else if (cph >= 10) points += 5;

    const hoursPercentage = hn > 0 ? (th / hn * 100) : 0;
    const premiumCoefficient = hoursPercentage >= 90 ? 1 : 0.75;
    const pointsCoefficient = points / 100;
    const baseSalary = SALARY_HOURLY_RATE * th;
    const premiumPart = baseSalary * pointsCoefficient * premiumCoefficient;
    const finalSalary = baseSalary + premiumPart + normalizedBonuses;

    return {
        model: 'chat',
        points,
        premiumCoefficient,
        hoursNorm: hn,
        hoursWorked: th,
        hoursPercentage,
        baseSalary,
        premiumPart,
        bonuses: normalizedBonuses,
        finalSalary,
        tableData: { experience, quality: qual, avgScore: score, responseTime: respTime, chatsPerHour: cph },
    };
}

// ──────────────────────────────────────────────────────────────────────────
// МОДЕЛИ TEZ. Формулы выведены из таблиц расчёта владельца продукта и сверены
// со строками-примерами (совпадение до округления отображаемых входов).
// Ставка = Оклад_FTE / Норма_FTE(176); оклад = ставка × отработанные часы.
// ──────────────────────────────────────────────────────────────────────────
export const TEZ_NORM_HOURS = 176;          // норма часов на 1 FTE
export const TEZ_LINE_OKLAD = 100000;       // оклад FTE «Линия/ТП (вход/чаты)»
export const TEZ_OP_OKLAD = 150000;         // оклад FTE «ОП»

// Бонус за качество (доля к окладу), модель Линия/ТП.
export function tezLineQualityPercent(quality) {
    const q = parseFloat(quality) || 0;
    if (q >= 96) return 1.0;   // 96-100 → 100%
    if (q >= 86) return 0.8;   // 86-95  → 80%
    if (q >= 76) return 0.6;   // 76-85  → 60%
    if (q >= 70) return 0.4;   // 70-75  → 40%
    return 0.2;                // 0-69   → 20%
}

// Надбавка за стаж (доля), модель Линия/ТП.
export function tezSeniorityPercent(months) {
    const m = parseFloat(months) || 0;
    if (m >= 18) return 0.30;
    if (m >= 13) return 0.25;
    if (m >= 10) return 0.20;
    if (m >= 6) return 0.15;
    if (m >= 3) return 0.10;
    return 0;                  // 0-2 мес → 0%
}

// Модель TEZ — Линия (тех поддержка / вход-чаты).
// Итог = Оклад + Бонус_качество + Бонус_стаж − Штрафы − Удержано50% + Бонусы,
// где Оклад = (100000/176) × часы; Бонус_качество = Оклад × кач%;
// Бонус_стаж = (Оклад + Бонус_качество) × стаж%.
export function calculateTezLineSalary({
    hoursWorked = 0,
    hoursNorm = TEZ_NORM_HOURS,
    quality = 0,
    experienceMonths = 0,
    fines = 0,
    withholding = 0,
    bonuses = 0,
} = {}) {
    const hours = parseFloat(hoursWorked) || 0;
    const norm = parseFloat(hoursNorm) || TEZ_NORM_HOURS;
    const rate = TEZ_LINE_OKLAD / TEZ_NORM_HOURS;
    const oklad = rate * hours;
    const qualityPercent = tezLineQualityPercent(quality);
    const bonusQuality = oklad * qualityPercent;
    const seniorityPercent = tezSeniorityPercent(experienceMonths);
    const bonusSeniority = (oklad + bonusQuality) * seniorityPercent;
    const finesV = parseFloat(fines) || 0;
    const withholdingV = parseFloat(withholding) || 0;
    const bonusesV = parseFloat(bonuses) || 0;
    const finalSalary = oklad + bonusQuality + bonusSeniority - finesV - withholdingV + bonusesV;
    const hoursPercentage = norm > 0 ? (hours / norm) * 100 : 0;
    return {
        model: 'tez_line',
        oklad,
        qualityPercent,
        bonusQuality,
        seniorityPercent,
        bonusSeniority,
        fines: finesV,
        withholding: withholdingV,
        bonuses: bonusesV,
        hoursWorked: hours,
        hoursNorm: norm,
        hoursPercentage,
        finalSalary,
    };
}

// Модель TEZ — ОП. Качество в выплату не входит (по таблице владельца).
// Итог = Оклад + Бонус_успешки − Штрафы − Удержано50% + Бонусы,
// где Оклад = (150000/176) × часы; % сделок = факт/цель;
// Бонус_успешки = Оклад × % сделок.
export function calculateTezOpSalary({
    hoursWorked = 0,
    hoursNorm = TEZ_NORM_HOURS,
    planTarget = 0,
    planFact = 0,
    fines = 0,
    withholding = 0,
    bonuses = 0,
} = {}) {
    const hours = parseFloat(hoursWorked) || 0;
    const norm = parseFloat(hoursNorm) || TEZ_NORM_HOURS;
    const rate = TEZ_OP_OKLAD / TEZ_NORM_HOURS;
    const oklad = rate * hours;
    const target = parseFloat(planTarget) || 0;
    const fact = parseFloat(planFact) || 0;
    const dealPercent = target > 0 ? fact / target : 0;
    const bonusDeals = oklad * dealPercent;
    const finesV = parseFloat(fines) || 0;
    const withholdingV = parseFloat(withholding) || 0;
    const bonusesV = parseFloat(bonuses) || 0;
    const finalSalary = oklad + bonusDeals - finesV - withholdingV + bonusesV;
    const hoursPercentage = norm > 0 ? (hours / norm) * 100 : 0;
    return {
        model: 'tez_op',
        oklad,
        planTarget: target,
        planFact: fact,
        dealPercent,
        bonusDeals,
        fines: finesV,
        withholding: withholdingV,
        bonuses: bonusesV,
        hoursWorked: hours,
        hoursNorm: norm,
        hoursPercentage,
        finalSalary,
    };
}

// ──────────────────────────────────────────────────────────────────────────
// Индивидуальный план успешных сделок на месяц, модель ОП TEZ.
// Правила владельца (июль 2026). Норма_FTE месяца = раб.дни × 8 ч, где
// раб.дни = округл(дни месяца ÷ 7 × 5) — НЕ календарные (июль: 22 р.д. → 176 ч).
//  2) стандарт (полный месяц, ≤100% выработки): план_FTE × ставка;
//  3) переработка (факт > нормы сотрудника):    план_FTE ÷ норма_FTE × факт;
//  4) новичок (принят в отчётном месяце): ×0,8; неполный месяц — пропорционально
//     раб. дням: план_FTE ÷ раб.дни месяца × ((конец месяца − дата приёма) ÷ 7 × 5) × ставка × 0,8;
//  5) новичок с переработкой:                   план_FTE ÷ норма_FTE × факт × 0,8;
//  6) увольнение/выход на БС (норма сотрудника пересчитана за фактический
//     период вручную):                          план_FTE ÷ норма_FTE × пересчитанная норма.
// ──────────────────────────────────────────────────────────────────────────
export const TEZ_OP_NEWBIE_COEF = 0.8;

// Рабочие дни месяца для плана ОП: округл(кол-во дней месяца ÷ 7 × 5),
// не календарные пн–пт — так считает владелец (31 д → 22; 30 д → 21; 28 д → 20).
export function tezWorkdaysInMonth(year, monthNum) {
    const days = new Date(year, monthNum, 0).getDate();
    return Math.round((days / 7) * 5);
}

function parsePlanDate(value) {
    if (!value) return null;
    if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
    const m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return null;
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

const fmtPlanNum = (v, digits = 2) => {
    const n = Number(v) || 0;
    const rounded = Math.round(n * 10 ** digits) / 10 ** digits;
    return String(rounded).replace('.', ',');
};

const fmtPlanDate = (d) =>
    `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}.${d.getFullYear()}`;

/**
 * Расчёт индивидуального плана ОП TEZ.
 * @param planPerFte план успешек на 1 FTE (общий по отделу)
 * @param rate       ставка сотрудника (0..1+); если не задана — выводится из нормы
 * @param normHours  норма часов сотрудника (уже с учётом ставки и ручного
 *                   пересчёта при увольнении/БС/неполном периоде)
 * @param factHours  фактически отработанные часы за месяц
 * @param hireDate   дата приёма ('YYYY-MM-DD' | Date | null)
 * @param month      отчётный месяц 'YYYY-MM'
 * @param newbie     принудительный признак новичка (true/false); null — по дате приёма
 * @returns { plan, caseCode, caseLabel, lines[], isNewbie, overtime, opNorm, rate }
 */
export function calculateTezOpMonthlyPlan({
    planPerFte = 0,
    rate = 0,
    normHours = 0,
    factHours = 0,
    hireDate = null,
    month = '',
    newbie = null,
} = {}) {
    const planFte = parseFloat(planPerFte) || 0;
    const fact = Math.max(0, parseFloat(factHours) || 0);
    const normRaw = Math.max(0, parseFloat(normHours) || 0);

    const [yStr, mStr] = String(month || '').split('-');
    const year = parseInt(yStr, 10);
    const monthNum = parseInt(mStr, 10);
    const hasPeriod = Number.isFinite(year) && monthNum >= 1 && monthNum <= 12;
    const monthStart = hasPeriod ? new Date(year, monthNum - 1, 1) : null;
    const monthEnd = hasPeriod ? new Date(year, monthNum, 0) : null;

    // Норма на 1 FTE этого месяца: округл(дни ÷ 7 × 5) раб. дней по 8 ч.
    // Без месяца (калькулятор) — 22 р.д. → 176 ч.
    const fteWorkdays = hasPeriod ? tezWorkdaysInMonth(year, monthNum) : Math.round(TEZ_NORM_HOURS / 8);
    const fteNorm = fteWorkdays * 8;

    let rateV = parseFloat(rate) || 0;
    if (rateV <= 0) rateV = normRaw > 0 ? normRaw / fteNorm : 1;
    const opNorm = normRaw > 0 ? normRaw : fteNorm * rateV;

    const base = { isNewbie: false, overtime: false, opNorm, rate: rateV, fteNorm, fteWorkdays };
    if (planFte <= 0) {
        return {
            ...base,
            plan: null,
            caseCode: 'no_plan',
            caseLabel: 'План на 1 FTE не задан',
            lines: ['Внесите план отдела в панели «План ОП TEZ».'],
        };
    }

    const hire = parsePlanDate(hireDate);
    if (hasPeriod && hire && hire > monthEnd) {
        return {
            ...base,
            plan: null,
            caseCode: 'not_hired',
            caseLabel: 'Принят после отчётного месяца',
            lines: [`Дата приёма: ${fmtPlanDate(hire)}.`],
        };
    }
    const isNewbie = newbie === true
        || (newbie !== false && !!(hasPeriod && hire && hire >= monthStart && hire <= monthEnd));
    const overtime = opNorm > 0 && fact > opNorm;
    const round1 = (v) => Math.round(v * 10) / 10;

    if (isNewbie && overtime) {
        const plan = round1((planFte / fteNorm) * fact * TEZ_OP_NEWBIE_COEF);
        return {
            ...base, isNewbie, overtime, plan,
            caseCode: 'newbie_overtime',
            caseLabel: 'Новичок с переработкой (×0,8)',
            lines: [
                hire ? `Принят ${fmtPlanDate(hire)} — новичок, коэффициент 0,8.` : 'Новичок — коэффициент 0,8.',
                `Факт ${fmtPlanNum(fact)} ч > нормы ${fmtPlanNum(opNorm)} ч — расчёт по факту.`,
                `Норма на 1 FTE: ${fteWorkdays} р.д. × 8 = ${fteNorm} ч.`,
                `План = ${fmtPlanNum(planFte)} ÷ ${fteNorm} × ${fmtPlanNum(fact)} × 0,8 = ${fmtPlanNum(plan, 1)}`,
            ],
        };
    }

    if (isNewbie) {
        // Полный месяц (приём 1-го числа или ручной признак без даты) — по ставке ×0,8.
        const hiredFirstDay = !hire || !hasPeriod || hire.getTime() <= monthStart.getTime();
        if (hiredFirstDay) {
            const plan = round1(planFte * rateV * TEZ_OP_NEWBIE_COEF);
            return {
                ...base, isNewbie, plan,
                caseCode: 'newbie_full',
                caseLabel: 'Новичок, полный месяц (×0,8)',
                lines: [
                    hire ? `Принят ${fmtPlanDate(hire)} — новичок, коэффициент 0,8.` : 'Новичок — коэффициент 0,8.',
                    `План = ${fmtPlanNum(planFte)} × ${fmtPlanNum(rateV)} × 0,8 = ${fmtPlanNum(plan, 1)}`,
                ],
            };
        }
        const calendarDays = Math.max(0, Math.round((monthEnd.getTime() - hire.getTime()) / 86400000));
        const newbieDays = (calendarDays / 7) * 5;
        const plan = round1((planFte / fteWorkdays) * newbieDays * rateV * TEZ_OP_NEWBIE_COEF);
        return {
            ...base, isNewbie, plan,
            caseCode: 'newbie_partial',
            caseLabel: 'Новичок, неполный месяц (×0,8)',
            lines: [
                `Принят ${fmtPlanDate(hire)} — новичок, коэффициент 0,8.`,
                `Раб. дней в месяце: округл(${monthEnd.getDate()} ÷ 7 × 5) = ${fteWorkdays}.`,
                `Раб. дни новичка: (${fmtPlanDate(monthEnd)} − ${fmtPlanDate(hire)}) ÷ 7 × 5 = ${fmtPlanNum(newbieDays)}.`,
                `План = ${fmtPlanNum(planFte)} ÷ ${fteWorkdays} × ${fmtPlanNum(newbieDays)} × ${fmtPlanNum(rateV)} × 0,8 = ${fmtPlanNum(plan, 1)}`,
            ],
        };
    }

    if (overtime) {
        const plan = round1((planFte / fteNorm) * fact);
        return {
            ...base, overtime, plan,
            caseCode: 'overtime',
            caseLabel: 'Переработка — расчёт по факт-часам',
            lines: [
                `Факт ${fmtPlanNum(fact)} ч > нормы ${fmtPlanNum(opNorm)} ч.`,
                `Норма на 1 FTE: ${fteWorkdays} р.д. × 8 = ${fteNorm} ч.`,
                `План = ${fmtPlanNum(planFte)} ÷ ${fteNorm} × ${fmtPlanNum(fact)} = ${fmtPlanNum(plan, 1)}`,
            ],
        };
    }

    // Норма заметно отличается от «норма_FTE × ставка» → пересчитана вручную
    // (увольнение/БС/неполный период) — план пропорционально норме (правило 6).
    const fullNormForRate = fteNorm * rateV;
    if (Math.abs(opNorm - fullNormForRate) > 0.5) {
        const plan = round1((planFte / fteNorm) * opNorm);
        return {
            ...base, plan,
            caseCode: 'partial_norm',
            caseLabel: 'Пропорционально пересчитанной норме',
            lines: [
                `Норма сотрудника ${fmtPlanNum(opNorm)} ч отличается от ${fteNorm} × ${fmtPlanNum(rateV)} = ${fmtPlanNum(fullNormForRate)} ч (пересчитана за фактический период — увольнение/БС/неполный месяц).`,
                `План = ${fmtPlanNum(planFte)} ÷ ${fteNorm} × ${fmtPlanNum(opNorm)} = ${fmtPlanNum(plan, 1)}`,
            ],
        };
    }

    const plan = round1(planFte * rateV);
    return {
        ...base, plan,
        caseCode: 'standard',
        caseLabel: 'Стандартный расчёт по ставке',
        lines: [
            `Полный месяц, выработка в пределах нормы (${fmtPlanNum(fact)} ч ≤ ${fmtPlanNum(opNorm)} ч).`,
            `План = ${fmtPlanNum(planFte)} × ${fmtPlanNum(rateV)} = ${fmtPlanNum(plan, 1)}`,
        ],
    };
}

// ──────────────────────────────────────────────────────────────────────────
// МОДЕЛЬ ОП «ОСНОВА» (op_osnova).
// Перенесена 1:1 из файла владельца «Основа_калькулятор зарплаты.xlsx»
// (лист «Основа») и сверена с презентацией «Мотивационная схема ЗП Основа»:
//   Оклад      = отработанные часы × 600 ₸/ч                        (I24 = G24×H24)
//   План сделок= план_1FTE ÷ норма_1FTE × отработанные часы          (D5)
//                новичку ×0,8                                       (D6)
//   % плана    = сделки ÷ план                                      (E5)
//   Цена сделки= ступень по % плана                                 (J24)
//   Бонус      = сделки × цена сделки                               (K24)
//   Удержано   = бонус × коэффициент по качеству звонков            (L24 = K24×C24)
//   Итого      = оклад + бонус − удержано − штрафы + премии         (N24 + столбец M)
// ВАЖНО: в N24 исходной таблицы штрафы (столбец M) не вычитались, хотя столбец
// заполняется вручную и ограничен валидацией. Здесь они вычитаются — как во
// всех остальных моделях; поле видно в расчёте отдельной строкой.
// ──────────────────────────────────────────────────────────────────────────
export const OSNOVA_HOURLY_RATE = 600;             // G12 «Оплата в час»
export const OSNOVA_NORM_HOURS_FTE = 176;          // I5 «Норма часов» на 1 FTE
export const OSNOVA_NEWBIE_COEF = 0.8;             // D6 — план новичка ×0,8
export const OSNOVA_NIGHT_PLAN_COEF = 0.5;         // H6 = H5 ÷ 2 «План на 1FTE Ночь»
export const OSNOVA_BEST_OPERATOR_PREMIUM = 50000; // I12 — премия лучшему по продажам в час (1 FTE)

// Норма часов на 1 FTE для месяца: раб. дни × 8, где раб. дни = округл(дни ÷ 7 × 5).
// Тот же счёт, что у плана ОП TEZ (31 д → 22 → 176 ч; 30 д → 21 → 168 ч) — ровно
// как в презентации владельца.
export function opFteNormHoursForMonth(month = '') {
    const [yStr, mStr] = String(month || '').split('-');
    const year = parseInt(yStr, 10);
    const monthNum = parseInt(mStr, 10);
    if (!Number.isFinite(year) || !(monthNum >= 1 && monthNum <= 12)) return OSNOVA_NORM_HOURS_FTE;
    return tezWorkdaysInMonth(year, monthNum) * 8;
}

// Коэффициент удержания с бонуса по качеству звонков (A13:B18, формула C24).
// Качество приходит числом 0..100. Границы взяты из формулы, а не из подписей:
// 74 ≤ q < 80 → 0,4 … q ≥ 96 → 0. Пустое поле = 0 → максимальное удержание,
// как в таблице владельца.
export function osnovaQualityWithholdRate(quality) {
    const q = parseFloat(quality) || 0;
    if (q < 74) return 0.5;
    if (q < 80) return 0.4;
    if (q < 86) return 0.3;
    if (q < 91) return 0.2;
    if (q < 96) return 0.1;
    return 0;
}

// Цена одной успешной сделки от % выполнения плана (C13:D20, формула J24).
// planRatio — доля (1 = 100%). Выше 140% цена не растёт: в таблице 160% и 180%
// тоже дают 600 ₸.
export function osnovaDealPrice(planRatio) {
    const r = parseFloat(planRatio) || 0;
    if (r < 0.5) return 100;
    if (r < 0.8) return 200;
    if (r < 1.0) return 400;
    if (r < 1.2) return 450;
    if (r < 1.4) return 500;
    return 600;
}

/**
 * Индивидуальный план сделок за месяц, модель ОП «Основа».
 * План растёт от ФАКТИЧЕСКИ отработанных часов (D5 = $H$5/$I$5*C5), поэтому
 * ставка сотрудника отдельно не участвует — она уже в часах.
 * @param planPerFte   план сделок на 1 FTE за месяц (дневная смена)
 * @param normHoursFte норма часов на 1 FTE (176 / 168 — зависит от месяца)
 * @param hoursWorked  фактически отработанные часы
 * @param newbie       новичок — план ×0,8
 * @param nightShift   ночная смена — план на 1 FTE вдвое меньше (H6)
 */
export function calculateOsnovaMonthlyPlan({
    planPerFte = 0,
    normHoursFte = OSNOVA_NORM_HOURS_FTE,
    hoursWorked = 0,
    newbie = false,
    nightShift = false,
} = {}) {
    const planFteDay = Math.max(0, parseFloat(planPerFte) || 0);
    const normFte = parseFloat(normHoursFte) || OSNOVA_NORM_HOURS_FTE;
    const hours = Math.max(0, parseFloat(hoursWorked) || 0);
    const planFte = nightShift ? planFteDay * OSNOVA_NIGHT_PLAN_COEF : planFteDay;
    const newbieCoef = newbie ? OSNOVA_NEWBIE_COEF : 1;
    const plan = normFte > 0 ? (planFte / normFte) * hours * newbieCoef : 0;
    return {
        plan,
        planPerFte: planFte,
        planPerFteDay: planFteDay,
        normHoursFte: normFte,
        hoursWorked: hours,
        isNewbie: Boolean(newbie),
        nightShift: Boolean(nightShift),
        newbieCoef,
    };
}

/**
 * Зарплата оператора ОП «Основа» за месяц.
 * planTarget — уже посчитанный индивидуальный план (calculateOsnovaMonthlyPlan);
 * если не передан, считается из planPerFte/normHoursFte/часов прямо здесь.
 */
export function calculateOsnovaSalary({
    hoursWorked = 0,
    hoursNorm = 0,
    deals = 0,
    planTarget = null,
    planPerFte = 0,
    normHoursFte = OSNOVA_NORM_HOURS_FTE,
    newbie = false,
    nightShift = false,
    quality = 0,
    fines = 0,
    bonuses = 0,
} = {}) {
    const hours = Math.max(0, parseFloat(hoursWorked) || 0);
    const norm = Math.max(0, parseFloat(hoursNorm) || 0);
    const dealsV = Math.max(0, parseFloat(deals) || 0);
    const planInfo = calculateOsnovaMonthlyPlan({ planPerFte, normHoursFte, hoursWorked: hours, newbie, nightShift });
    const parsedTarget = planTarget === null || planTarget === '' ? null : parseFloat(planTarget);
    const target = Number.isFinite(parsedTarget) ? Math.max(0, parsedTarget) : planInfo.plan;

    const oklad = hours * OSNOVA_HOURLY_RATE;
    const planPercent = target > 0 ? dealsV / target : 0;
    const dealPrice = osnovaDealPrice(planPercent);
    const bonusDeals = dealsV * dealPrice;
    const qualityWithholdRate = osnovaQualityWithholdRate(quality);
    const qualityWithheld = bonusDeals * qualityWithholdRate;
    const finesV = parseFloat(fines) || 0;
    const bonusesV = parseFloat(bonuses) || 0;
    const finalSalary = oklad + bonusDeals - qualityWithheld - finesV + bonusesV;

    return {
        model: 'op_osnova',
        hourlyRate: OSNOVA_HOURLY_RATE,
        oklad,
        deals: dealsV,
        planTarget: target,
        planPercent,                       // доля: 1 = 100%
        planPerFte: planInfo.planPerFte,
        planPerFteDay: planInfo.planPerFteDay,
        normHoursFte: planInfo.normHoursFte,
        isNewbie: planInfo.isNewbie,
        nightShift: planInfo.nightShift,
        dealPrice,
        bonusDeals,
        quality: parseFloat(quality) || 0,
        qualityWithholdRate,
        qualityWithheld,
        fines: finesV,
        bonuses: bonusesV,
        hoursWorked: hours,
        hoursNorm: norm,
        hoursPercentage: norm > 0 ? (hours / norm) * 100 : 0,
        finalSalary,
    };
}

// ──────────────────────────────────────────────────────────────────────────
// МОДЕЛЬ ОП «ПОТОК» (op_potok).
// Перенесена из файла владельца «Поток_калькулятор_зарплаты.xlsx» (лист «Поток»)
// и сверена с презентацией отдела продаж (слайды «Из чего состоит твоя зарплата»,
// «Точные цифры», «Детализация переменного KPI»):
//   Сумма за часы = отработанные часы × ставка (700 ₸/ч)        (G28 = F28×E28)
//   Итог продаж   = Отток + Фокус                               (D5)
//   План продаж   = план_1FTE ÷ норма_1FTE × отработанные часы   (F5), новичку ×0,8 (F6)
//   % плана       = итог продаж ÷ план — ОДИН на оба потока      (G5)
//   Цена сделки   — своя ступень у «Оттока» и у «Фокуса»        (H28 / J28)
//   Бонусы        = продажи потока × его цену сделки            (I28 / K28)
//   Итого         = часы + бонус Отток + бонус Фокус − штрафы − удержано 50%  (N28)
// Качество звонков в этой модели на выплату не влияет (в отличие от «Основы»).
// ──────────────────────────────────────────────────────────────────────────
export const POTOK_HOURLY_RATE = 700;        // F28 «Ставка, ₸/час» + слайд «Гарантированный оклад»
export const POTOK_NORM_HOURS_FTE = 176;     // ставка 1,0 = 22 раб. дня × 8 ч
export const POTOK_PLAN_PER_FTE = 150;       // J4 «План» на 1 FTE
export const POTOK_NEWBIE_COEF = 0.8;        // F6 — план новичка ×0,8

// Ступени цены сделки. В файле это INDEX/MATCH по массиву порогов, поэтому
// границы берём из массива формулы, а не из подписей столбца.
const potokPriceFromLadder = (ladder, planRatio) => {
    const r = parseFloat(planRatio) || 0;
    let price = ladder[0][1];
    for (const [threshold, value] of ladder) {
        if (r >= threshold) price = value;
        else break;
    }
    return price;
};

// «Отток»: MATCH(%, {0;0,7;0,8;0,9;1;1,2;1,5;2}) → E13:E20.
const POTOK_CHURN_LADDER = [
    [0, 200], [0.7, 300], [0.8, 400], [0.9, 450], [1, 500], [1.2, 550], [1.5, 600], [2, 800],
];

// «Фокус»: MATCH(%, {0;0,7;0,8;0,9;1;1,2;1,4;1,6;1,8;2;2,2}) → B13:B23.
// ВАЖНО: в файле последний порог задан как 2 (дубль предыдущего), из-за чего
// ступень 1400 ₸ недостижима и при 200% сразу платится 1600 ₸. В презентации
// таблица читается однозначно: 200% → 1400, 220% → 1600 — берём её.
const POTOK_FOCUS_LADDER = [
    [0, 700], [0.7, 750], [0.8, 800], [0.9, 850], [1, 900], [1.2, 950],
    [1.4, 1000], [1.6, 1100], [1.8, 1200], [2, 1400], [2.2, 1600],
];

export function potokChurnDealPrice(planRatio) {
    return potokPriceFromLadder(POTOK_CHURN_LADDER, planRatio);
}

export function potokFocusDealPrice(planRatio) {
    return potokPriceFromLadder(POTOK_FOCUS_LADDER, planRatio);
}

/**
 * Индивидуальный план продаж за месяц, модель ОП «Поток».
 * Как и в «Основе», план растёт от ФАКТИЧЕСКИ отработанных часов (F5), поэтому
 * ставка сотрудника отдельно не участвует.
 */
export function calculatePotokMonthlyPlan({
    planPerFte = POTOK_PLAN_PER_FTE,
    normHoursFte = POTOK_NORM_HOURS_FTE,
    hoursWorked = 0,
    newbie = false,
} = {}) {
    const planFte = Math.max(0, parseFloat(planPerFte) || 0);
    const normFte = parseFloat(normHoursFte) || POTOK_NORM_HOURS_FTE;
    const hours = Math.max(0, parseFloat(hoursWorked) || 0);
    const newbieCoef = newbie ? POTOK_NEWBIE_COEF : 1;
    const plan = normFte > 0 ? (planFte / normFte) * hours * newbieCoef : 0;
    return {
        plan,
        planPerFte: planFte,
        normHoursFte: normFte,
        hoursWorked: hours,
        isNewbie: Boolean(newbie),
        newbieCoef,
    };
}

/**
 * Зарплата оператора ОП «Поток» за месяц.
 * Проценты выполнения плана считаются по СУММЕ обоих потоков продаж, а цена
 * сделки после этого берётся по своей ступени для «Оттока» и для «Фокуса».
 */
export function calculatePotokSalary({
    hoursWorked = 0,
    hoursNorm = 0,
    churnSales = 0,
    focusSales = 0,
    hourlyRate = POTOK_HOURLY_RATE,
    planTarget = null,
    planPerFte = POTOK_PLAN_PER_FTE,
    normHoursFte = POTOK_NORM_HOURS_FTE,
    newbie = false,
    fines = 0,
    withholding = 0,
} = {}) {
    const hours = Math.max(0, parseFloat(hoursWorked) || 0);
    const norm = Math.max(0, parseFloat(hoursNorm) || 0);
    const churn = Math.max(0, parseFloat(churnSales) || 0);
    const focus = Math.max(0, parseFloat(focusSales) || 0);
    const rate = parseFloat(hourlyRate);
    const rateV = Number.isFinite(rate) ? rate : POTOK_HOURLY_RATE;

    const planInfo = calculatePotokMonthlyPlan({ planPerFte, normHoursFte, hoursWorked: hours, newbie });
    const parsedTarget = planTarget === null || planTarget === '' ? null : parseFloat(planTarget);
    const target = Number.isFinite(parsedTarget) ? Math.max(0, parsedTarget) : planInfo.plan;

    const totalSales = churn + focus;
    const oklad = hours * rateV;
    const planPercent = target > 0 ? totalSales / target : 0;
    const churnPrice = potokChurnDealPrice(planPercent);
    const focusPrice = potokFocusDealPrice(planPercent);
    const bonusChurn = churn * churnPrice;
    const bonusFocus = focus * focusPrice;
    const finesV = parseFloat(fines) || 0;
    const withholdingV = parseFloat(withholding) || 0;
    const finalSalary = oklad + bonusChurn + bonusFocus - finesV - withholdingV;

    return {
        model: 'op_potok',
        hourlyRate: rateV,
        oklad,
        churnSales: churn,
        focusSales: focus,
        totalSales,
        planTarget: target,
        planPercent,                       // доля: 1 = 100%
        planPerFte: planInfo.planPerFte,
        normHoursFte: planInfo.normHoursFte,
        isNewbie: planInfo.isNewbie,
        churnPrice,
        focusPrice,
        bonusChurn,
        bonusFocus,
        bonusDeals: bonusChurn + bonusFocus,
        fines: finesV,
        withholding: withholdingV,
        hoursWorked: hours,
        hoursNorm: norm,
        hoursPercentage: norm > 0 ? (hours / norm) * 100 : 0,
        finalSalary,
    };
}
