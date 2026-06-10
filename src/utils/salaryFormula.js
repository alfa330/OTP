// Чистые функции расчёта ЗП по моделям. Извлечены 1:1 из:
//   - App.jsx calculateSalaryByFormula (модель «оператор»/звонки)
//   - components/salary/SalaryCalculatorChat.jsx calculateSalary (модель «чат-менеджер»)
// НЕ объединять таблицы баллов — у моделей разные пороги/категории; изменение = изменение выплат.
// Ставка 700 ₸/час для ОБЕИХ моделей (подтверждено владельцем продукта).

export const SALARY_HOURLY_RATE = 700;

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
