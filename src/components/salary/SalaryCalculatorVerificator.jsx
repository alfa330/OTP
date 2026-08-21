import React, { useState, useEffect, useMemo } from 'react';
import FaIcon from '../common/FaIcon';
import SalaryCalculationResult from './SalaryCalculationResult';
import {
  VERIFICATOR_HOURLY_RATE,
  VERIFICATOR_PLAN_PER_FTE,
  calculateVerificatorMonthlyPlan,
  calculateVerificatorSalary,
  opFteNormHoursForMonth,
} from '../../utils/salaryFormula';

const Field = ({ label, icon, iconColor, children }) => (
  <div className="p-4 sm:p-6 bg-gray-50 rounded-xl shadow-sm hover:shadow-md transition">
    <label className="block mb-2 font-semibold text-gray-700 flex items-center gap-2">
      {icon && <FaIcon className={`fas ${icon} ${iconColor || 'text-blue-500'}`} />}
      {label}
    </label>
    {children}
  </div>
);

const numberInput = (value, onChange, extra = {}) => (
  <input
    type="number"
    value={value}
    onChange={(e) => onChange(e.target.value)}
    className="w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
    {...extra}
  />
);

const fmtPlan = (v) => {
  const n = Number(v) || 0;
  return (Math.round(n * 10) / 10).toString().replace('.', ',');
};

/**
 * Калькулятор зарплаты направления «Верификатор» отдела продаж (op_verificator).
 * Формулы — src/utils/salaryFormula.js, перенесены из файла владельца
 * «Верификаторы_калькулятор_зарплаты_1.xlsx» (лист «Верик») и сверены с
 * презентацией «Мотивационная схема верификатора».
 * prefill: часы/норма/качество/штрафы из «Моих часов» (по смене prefillNonce).
 * month: 'YYYY-MM' — от него зависит норма часов на 1 FTE (176 / 168 / 160).
 */
const SalaryCalculatorVerificator = ({ prefill = null, prefillNonce = 0, month = '' }) => {
  const monthNormFte = opFteNormHoursForMonth(month);
  const [hoursNorm, setHoursNorm] = useState(String(monthNormFte));
  const [hoursWorked, setHoursWorked] = useState('');
  const [hourlyRate, setHourlyRate] = useState(String(VERIFICATOR_HOURLY_RATE));
  const [sales, setSales] = useState('');
  const [planPerFte, setPlanPerFte] = useState(String(VERIFICATOR_PLAN_PER_FTE));
  const [normHoursFte, setNormHoursFte] = useState(String(monthNormFte));
  const [nightShift, setNightShift] = useState(false);
  const [isNewbie, setIsNewbie] = useState(false);
  const [quality, setQuality] = useState('');
  const [promoFines, setPromoFines] = useState('');
  const [fines, setFines] = useState('');

  // Смена месяца двигает норму на 1 FTE (22 раб. дня × 8 ч в 31-дневном месяце,
  // 21 × 8 — в 30-дневном): от неё считается план продаж.
  useEffect(() => {
    setNormHoursFte(String(monthNormFte));
  }, [monthNormFte]);

  // Переход из «Моих часов»: часы месяца, норма, качество и штрафы. Продажи
  // вносятся вручную — их источника в системе нет.
  useEffect(() => {
    if (!prefill) return;
    if (prefill.hoursNorm !== undefined) setHoursNorm(String(prefill.hoursNorm ?? ''));
    if (prefill.hoursWorked !== undefined) setHoursWorked(String(prefill.hoursWorked ?? ''));
    if (prefill.quality !== undefined) setQuality(String(prefill.quality ?? ''));
    if (prefill.fines !== undefined) setFines(String(prefill.fines ?? ''));
    if (prefill.sales !== undefined) setSales(String(prefill.sales ?? ''));
    if (prefill.promoFines !== undefined) setPromoFines(String(prefill.promoFines ?? ''));
    if (prefill.planPerFte !== undefined) setPlanPerFte(String(prefill.planPerFte ?? ''));
    if (prefill.normHoursFte !== undefined) setNormHoursFte(String(prefill.normHoursFte ?? ''));
    if (prefill.newbie !== undefined) setIsNewbie(Boolean(prefill.newbie));
    if (prefill.nightShift !== undefined) setNightShift(Boolean(prefill.nightShift));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillNonce]);

  const planInfo = useMemo(
    () => calculateVerificatorMonthlyPlan({ planPerFte, normHoursFte, hoursWorked, newbie: isNewbie, nightShift }),
    [planPerFte, normHoursFte, hoursWorked, isNewbie, nightShift]
  );

  const result = useMemo(
    () => calculateVerificatorSalary({
      hoursWorked,
      hoursNorm,
      hourlyRate,
      sales,
      planTarget: planInfo.plan,
      planPerFte,
      normHoursFte,
      newbie: isNewbie,
      nightShift,
      quality,
      promoFines,
      fines,
    }),
    [hoursWorked, hoursNorm, hourlyRate, sales, planInfo.plan, planPerFte, normHoursFte, isNewbie, nightShift, quality, promoFines, fines]
  );

  const reset = () => {
    setHoursNorm(String(monthNormFte));
    setHoursWorked('');
    setHourlyRate(String(VERIFICATOR_HOURLY_RATE));
    setSales('');
    setPlanPerFte(String(VERIFICATOR_PLAN_PER_FTE));
    setNormHoursFte(String(monthNormFte));
    setNightShift(false);
    setIsNewbie(false);
    setQuality('');
    setPromoFines('');
    setFines('');
  };

  const planPercentText = result.planTarget > 0 ? `${(result.planPercent * 100).toFixed(1).replace('.', ',')}%` : '—';

  return (
    <div>
      <div className="mb-4 inline-flex items-center gap-2 rounded-full bg-teal-50 px-3 py-1.5 text-xs font-semibold text-teal-700 ring-1 ring-teal-100">
        <FaIcon className="fas fa-user-check" />
        Модель: Оператор ОП «Верификатор»
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
        <Field label="Норма часов:" icon="fa-bullseye" iconColor="text-purple-500">
          {numberInput(hoursNorm, setHoursNorm, { min: 0, max: 744, step: '0.01' })}
        </Field>
        <Field label="Отработанные часы:" icon="fa-briefcase" iconColor="text-indigo-500">
          {numberInput(hoursWorked, setHoursWorked, { min: 0, max: 744, step: '0.01' })}
          <div className="mt-2 text-xs text-gray-500">
            Оклад: <span className="font-medium text-gray-700">{Math.round(result.oklad).toLocaleString('ru-RU')} ₸</span>
            <span className="ml-1 text-gray-400">— часы × ставку, оба бонуса считаются от него</span>
          </div>
        </Field>

        <Field label="Ставка, ₸/час:" icon="fa-coins" iconColor="text-amber-500">
          {numberInput(hourlyRate, setHourlyRate, { min: 0, step: '1' })}
          <div className="mt-2 text-xs text-gray-500">
            По схеме — {VERIFICATOR_HOURLY_RATE} ₸/час за каждый фактически отработанный час.
          </div>
        </Field>
        <Field label="План продаж на 1 FTE:" icon="fa-bullseye" iconColor="text-rose-500">
          {numberInput(planPerFte, setPlanPerFte, { min: 0, step: '0.01' })}
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
            <label className="flex items-center gap-2 text-xs text-gray-600">
              <input
                type="checkbox"
                checked={nightShift}
                onChange={(e) => setNightShift(e.target.checked)}
                className="h-4 w-4 rounded border-gray-300 text-teal-600 focus:ring-teal-500"
              />
              Ночная смена (план ÷ 2)
            </label>
            <label className="flex items-center gap-2 text-xs text-gray-600">
              <input
                type="checkbox"
                checked={isNewbie}
                onChange={(e) => setIsNewbie(e.target.checked)}
                className="h-4 w-4 rounded border-gray-300 text-teal-600 focus:ring-teal-500"
              />
              Новичок (план ×0,8)
            </label>
          </div>
          <div className="mt-2 text-xs text-gray-500">
            Норма часов на 1 FTE:
            <input
              type="number"
              value={normHoursFte}
              onChange={(e) => setNormHoursFte(e.target.value)}
              min={0}
              max={744}
              step="0.01"
              className="ml-2 w-24 rounded border px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <span className="ml-1 text-gray-400">ч</span>
          </div>
          <div className="mt-2 text-xs text-gray-500">
            Индивидуальный план: <span className="font-medium text-gray-700">{fmtPlan(planInfo.plan)}</span>
            <span className="ml-1 text-gray-400">— {fmtPlan(planInfo.planPerFte)} ÷ {fmtPlan(planInfo.normHoursFte)} × отработанные часы</span>
          </div>
        </Field>

        <Field label="Факт продаж, шт:" icon="fa-check-circle" iconColor="text-green-500">
          {numberInput(sales, setSales, { min: 0, step: '1' })}
          <div className="mt-2 text-xs text-gray-500">
            Выполнение плана: <span className="font-medium text-gray-700">{planPercentText}</span>
            <span className="ml-1 text-gray-400">— бонус за план {result.planBonusPercent}% оклада</span>
          </div>
        </Field>
        <Field label="Качество (%):" icon="fa-star" iconColor="text-yellow-500">
          {numberInput(quality, setQuality, { min: 0, max: 100, step: '0.01' })}
          <div className="mt-2 text-xs text-gray-500">
            Бонус за качество: <span className="font-medium text-gray-700">{Math.round(result.bonusQuality).toLocaleString('ru-RU')} ₸</span>
            <span className="ml-1 text-gray-400">— оклад × % качества, ступеней нет</span>
          </div>
        </Field>

        <Field label="Штраф за акции (₸):" icon="fa-ban" iconColor="text-orange-500">
          {numberInput(promoFines, setPromoFines, { min: 0, step: '0.01' })}
        </Field>
        <Field label="Штрафы (₸):" icon="fa-triangle-exclamation" iconColor="text-red-500">
          {numberInput(fines, setFines, { min: 0, step: '0.01' })}
        </Field>
      </div>

      <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-600">
        Итого баллов: <span className="font-semibold text-gray-800">{fmtPlan(result.totalBonusPercent)}%</span>
        <span className="ml-1 text-gray-400">
          — качество {fmtPlan(result.quality)}% + план {result.planBonusPercent}%; бонус = оклад × эти баллы
        </span>
      </div>

      <div className="flex justify-center mt-6">
        <button
          onClick={reset}
          className="w-full sm:w-auto px-6 py-3 rounded-xl font-bold text-sm bg-red-500 text-white hover:bg-red-600 shadow transition"
        >
          <FaIcon className="fas fa-eraser mr-2" /> Очистить
        </button>
      </div>

      {/* Результат — общая карточка со СЗоВ, TEZ и остальными моделями ОП */}
      <SalaryCalculationResult salaryResult={result} label="Оператор ОП «Верификатор»" />
    </div>
  );
};

export default SalaryCalculatorVerificator;
