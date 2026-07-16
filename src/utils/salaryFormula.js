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
