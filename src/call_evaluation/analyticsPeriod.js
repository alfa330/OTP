/* Период «Аналитики» журнала оценок (задача #389) — правила без React.
 *
 * Раньше «Аналитика» жила по месяцам: селектор месяца, данные и план за месяц.
 * Теперь период любой — день, неделя, диапазон, месяц, год или «Весь период»,
 * но часть журнала по-прежнему помесячная: выгрузки отчётов, журнал оператора
 * по кнопке «Оценки», «ОС от ИИ». Для них — analyticsPeriodMonth. Проверяет
 * правила node-тест tests/call_evaluation_analytics_period.test.mjs. */
import { isoDate, monthRange, wholeMonthOf } from '../components/ui/periodRange.js';

/** Период целого месяца из «ГГГГ-ММ»; null — не месяц. */
export const analyticsMonthPeriod = (month) => {
    const match = /^(\d{4})-(\d{2})$/.exec(String(month || ''));
    if (!match || Number(match[2]) < 1 || Number(match[2]) > 12) return null;
    return monthRange(Number(match[1]), Number(match[2]) - 1);
};

/** Период по умолчанию — текущий месяц по местному времени. Не toISOString():
    та берёт UTC, и с полуночи до пяти утра 1-го числа по Алматы дала бы
    прошлый месяц. */
export const currentMonthPeriod = (now = new Date()) => analyticsMonthPeriod(isoDate(now).slice(0, 7));

/** Месяц для помесячного: выбран целый месяц — он сам; иначе месяц конца
    периода, но не позже текущего; «Весь период» — текущий месяц. */
export const analyticsPeriodMonth = (period, now = new Date()) => {
    const whole = wholeMonthOf(period?.from, period?.to);
    if (whole) return whole;
    const current = isoDate(now).slice(0, 7);
    const end = String(period?.to || '').slice(0, 7);
    return end && end < current ? end : current;
};

/** Параметры запроса состава за период: даты или period=all («Весь период»). */
export const analyticsPeriodQuery = (period) => (period?.from && period?.to
    ? { date_from: period.from, date_to: period.to }
    : { period: 'all' });

/** Как делить и подписывать строки «Аналитики» — по самому ответу, а не по
    селекторам: пока грузится новый период, на экране ещё прежние строки, и
    вкладки, чипы и план считаются по их же периоду. Месяц — только у ответа за
    один целый месяц: тогда вкладки и план прежние, месячные. Ответа ещё нет —
    по выбранному периоду. Ответ старого сервера (без plan_available) — с планом,
    как было.
    → { dataMonth, tabsContext: { month } | { period: { from, to } }, planAvailable } */
export const analyticsDataView = (svData, period) => {
    const month = svData
        ? (svData.requested_month || null)
        : wholeMonthOf(period?.from, period?.to);
    const range = svData ? (svData.requested_period || {}) : (period || {});
    return {
        dataMonth: month,
        tabsContext: month ? { month } : { period: { from: range.from || '', to: range.to || '' } },
        planAvailable: svData ? svData.plan_available !== false : Boolean(month),
    };
};
