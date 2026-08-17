import React, { useState, useEffect, useMemo } from 'react';
import FaIcon from '../common/FaIcon';
import SalaryCalculationResult from './SalaryCalculationResult';
import {
  POTOK_HOURLY_RATE,
  POTOK_PLAN_PER_FTE,
  calculatePotokMonthlyPlan,
  calculatePotokSalary,
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
 * Калькулятор зарплаты направления «Поток» отдела продаж (модель op_potok).
 * Формулы — src/utils/salaryFormula.js, перенесены из файла владельца
 * «Поток_калькулятор_зарплаты.xlsx» и сверены с презентацией отдела продаж.
 * prefill: часы/норма/штрафы из «Моих часов» (по смене prefillNonce).
 * month: 'YYYY-MM' — от него зависит норма часов на 1 FTE (176 / 168 / 160).
 */
const SalaryCalculatorPotok = ({ prefill = null, prefillNonce = 0, month = '' }) => {
  const monthNormFte = opFteNormHoursForMonth(month);
  const [hoursNorm, setHoursNorm] = useState(String(monthNormFte));
  const [hoursWorked, setHoursWorked] = useState('');
  const [hourlyRate, setHourlyRate] = useState(String(POTOK_HOURLY_RATE));
  const [churnSales, setChurnSales] = useState('');
  const [focusSales, setFocusSales] = useState('');
  const [planPerFte, setPlanPerFte] = useState(String(POTOK_PLAN_PER_FTE));
  const [normHoursFte, setNormHoursFte] = useState(String(monthNormFte));
  const [isNewbie, setIsNewbie] = useState(false);
  const [fines, setFines] = useState('');
  const [withholding, setWithholding] = useState('');

  // Смена месяца двигает норму на 1 FTE (22 раб. дня × 8 ч в 31-дневном месяце,
  // 21 × 8 — в 30-дневном): от неё считается план продаж.
  useEffect(() => {
    setNormHoursFte(String(monthNormFte));
  }, [monthNormFte]);

  // Переход из «Моих часов»: часы месяца, норма и штрафы. Продажи по потокам
  // вносятся вручную — их источника в системе нет.
  useEffect(() => {
    if (!prefill) return;
    if (prefill.hoursNorm !== undefined) setHoursNorm(String(prefill.hoursNorm ?? ''));
    if (prefill.hoursWorked !== undefined) setHoursWorked(String(prefill.hoursWorked ?? ''));
    if (prefill.fines !== undefined) setFines(String(prefill.fines ?? ''));
    if (prefill.churnSales !== undefined) setChurnSales(String(prefill.churnSales ?? ''));
    if (prefill.focusSales !== undefined) setFocusSales(String(prefill.focusSales ?? ''));
    if (prefill.planPerFte !== undefined) setPlanPerFte(String(prefill.planPerFte ?? ''));
    if (prefill.normHoursFte !== undefined) setNormHoursFte(String(prefill.normHoursFte ?? ''));
    if (prefill.newbie !== undefined) setIsNewbie(Boolean(prefill.newbie));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillNonce]);

  const planInfo = useMemo(
    () => calculatePotokMonthlyPlan({ planPerFte, normHoursFte, hoursWorked, newbie: isNewbie }),
    [planPerFte, normHoursFte, hoursWorked, isNewbie]
  );

  const result = useMemo(
    () => calculatePotokSalary({
      hoursWorked,
      hoursNorm,
      churnSales,
      focusSales,
      hourlyRate,
      planTarget: planInfo.plan,
      planPerFte,
      normHoursFte,
      newbie: isNewbie,
      fines,
      withholding,
    }),
    [hoursWorked, hoursNorm, churnSales, focusSales, hourlyRate, planInfo.plan, planPerFte, normHoursFte, isNewbie, fines, withholding]
  );

  const reset = () => {
    setHoursNorm(String(monthNormFte));
    setHoursWorked('');
    setHourlyRate(String(POTOK_HOURLY_RATE));
    setChurnSales('');
    setFocusSales('');
    setPlanPerFte(String(POTOK_PLAN_PER_FTE));
    setNormHoursFte(String(monthNormFte));
    setIsNewbie(false);
    setFines('');
    setWithholding('');
  };

  const planPercentText = result.planTarget > 0 ? `${(result.planPercent * 100).toFixed(1).replace('.', ',')}%` : '—';

  return (
    <div>
      <div className="mb-4 inline-flex items-center gap-2 rounded-full bg-orange-50 px-3 py-1.5 text-xs font-semibold text-orange-700 ring-1 ring-orange-100">
        <FaIcon className="fas fa-random" />
        Модель: Оператор ОП «Поток»
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
        <Field label="Норма часов:" icon="fa-bullseye" iconColor="text-purple-500">
          {numberInput(hoursNorm, setHoursNorm, { min: 0, max: 744, step: '0.01' })}
        </Field>
        <Field label="Отработанные часы:" icon="fa-briefcase" iconColor="text-indigo-500">
          {numberInput(hoursWorked, setHoursWorked, { min: 0, max: 744, step: '0.01' })}
          <div className="mt-2 text-xs text-gray-500">
            Сумма за часы: <span className="font-medium text-gray-700">{Math.round(result.oklad).toLocaleString('ru-RU')} ₸</span>
            <span className="ml-1 text-gray-400">— часы × ставку</span>
          </div>
        </Field>

        <Field label="Ставка, ₸/час:" icon="fa-coins" iconColor="text-amber-500">
          {numberInput(hourlyRate, setHourlyRate, { min: 0, step: '1' })}
          <div className="mt-2 text-xs text-gray-500">
            По схеме — {POTOK_HOURLY_RATE} ₸/час за каждый отработанный час.
          </div>
        </Field>
        <Field label="План продаж на 1 FTE:" icon="fa-bullseye" iconColor="text-rose-500">
          {numberInput(planPerFte, setPlanPerFte, { min: 0, step: '0.01' })}
          <label className="mt-2 flex items-center gap-2 text-xs text-gray-600">
            <input
              type="checkbox"
              checked={isNewbie}
              onChange={(e) => setIsNewbie(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-orange-600 focus:ring-orange-500"
            />
            Новичок (план ×0,8)
          </label>
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

        <Field label="Продажи «Отток», шт:" icon="fa-rotate-left" iconColor="text-sky-500">
          {numberInput(churnSales, setChurnSales, { min: 0, step: '1' })}
          <div className="mt-2 text-xs text-gray-500">
            Цена сделки: <span className="font-medium text-gray-700">{result.churnPrice} ₸</span>
            <span className="ml-1 text-gray-400">— {Math.round(result.bonusChurn).toLocaleString('ru-RU')} ₸</span>
          </div>
        </Field>
        <Field label="Продажи «Фокус», шт:" icon="fa-bullseye" iconColor="text-green-500">
          {numberInput(focusSales, setFocusSales, { min: 0, step: '1' })}
          <div className="mt-2 text-xs text-gray-500">
            Цена сделки: <span className="font-medium text-gray-700">{result.focusPrice} ₸</span>
            <span className="ml-1 text-gray-400">— {Math.round(result.bonusFocus).toLocaleString('ru-RU')} ₸</span>
          </div>
        </Field>

        <Field label="Штрафы (₸):" icon="fa-triangle-exclamation" iconColor="text-red-500">
          {numberInput(fines, setFines, { min: 0, step: '0.01' })}
        </Field>
        <Field label="Удержано 50% (₸):" icon="fa-percent" iconColor="text-orange-500">
          {numberInput(withholding, setWithholding, { min: 0, step: '0.01' })}
        </Field>
      </div>

      <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-600">
        Итог продаж: <span className="font-semibold text-gray-800">{result.totalSales}</span> шт ·
        выполнение плана: <span className="font-semibold text-gray-800">{planPercentText}</span>
        <span className="ml-1 text-gray-400">— процент считается по сумме обоих потоков, цена сделки у каждого своя</span>
      </div>

      <div className="flex justify-center mt-6">
        <button
          onClick={reset}
          className="w-full sm:w-auto px-6 py-3 rounded-xl font-bold text-sm bg-red-500 text-white hover:bg-red-600 shadow transition"
        >
          <FaIcon className="fas fa-eraser mr-2" /> Очистить
        </button>
      </div>

      {/* Результат — общая карточка со СЗоВ, TEZ и «Основой» */}
      <SalaryCalculationResult salaryResult={result} label="Оператор ОП «Поток»" />
    </div>
  );
};

export default SalaryCalculatorPotok;
