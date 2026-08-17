import React, { useState, useEffect, useMemo } from 'react';
import FaIcon from '../common/FaIcon';
import SalaryCalculationResult from './SalaryCalculationResult';
import {
  OSNOVA_BEST_OPERATOR_PREMIUM,
  OSNOVA_HOURLY_RATE,
  calculateOsnovaMonthlyPlan,
  calculateOsnovaSalary,
  osnovaNormHoursForMonth,
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
 * Калькулятор зарплаты направления «Основа» отдела продаж (модель op_osnova).
 * Формулы — src/utils/salaryFormula.js, перенесены из файла владельца
 * «Основа_калькулятор зарплаты.xlsx» и сверены с мотивационной презентацией.
 * prefill: часы/норма/штрафы/качество, перенесённые из раздела «Мои часы»
 * (применяется по смене prefillNonce, чтобы повторный переход перезаполнял поля).
 * month: 'YYYY-MM' — от него зависит норма часов на 1 FTE (176 / 168 / 160).
 */
const SalaryCalculatorOsnova = ({ prefill = null, prefillNonce = 0, month = '' }) => {
  const monthNormFte = osnovaNormHoursForMonth(month);
  const [hoursNorm, setHoursNorm] = useState(String(monthNormFte));
  const [hoursWorked, setHoursWorked] = useState('');
  const [deals, setDeals] = useState('');
  const [planPerFte, setPlanPerFte] = useState('');
  const [normHoursFte, setNormHoursFte] = useState(String(monthNormFte));
  const [nightShift, setNightShift] = useState(false);
  const [isNewbie, setIsNewbie] = useState(false);
  const [quality, setQuality] = useState('');
  const [fines, setFines] = useState('');
  const [bonuses, setBonuses] = useState('');

  // Смена месяца двигает норму на 1 FTE (22 раб. дня × 8 ч в 31-дневном месяце,
  // 21 × 8 — в 30-дневном): план сделок считается именно от неё.
  useEffect(() => {
    setNormHoursFte(String(monthNormFte));
  }, [monthNormFte]);

  // Переход из «Моих часов»: подставляем реальные часы месяца, норму, штрафы и
  // качество, чтобы оператор видел ровно свой расчёт.
  useEffect(() => {
    if (!prefill) return;
    if (prefill.hoursNorm !== undefined) setHoursNorm(String(prefill.hoursNorm ?? ''));
    if (prefill.hoursWorked !== undefined) setHoursWorked(String(prefill.hoursWorked ?? ''));
    if (prefill.quality !== undefined) setQuality(String(prefill.quality ?? ''));
    if (prefill.fines !== undefined) setFines(String(prefill.fines ?? ''));
    if (prefill.bonuses !== undefined) setBonuses(String(prefill.bonuses ?? ''));
    if (prefill.deals !== undefined) setDeals(String(prefill.deals ?? ''));
    if (prefill.planPerFte !== undefined) setPlanPerFte(String(prefill.planPerFte ?? ''));
    if (prefill.normHoursFte !== undefined) setNormHoursFte(String(prefill.normHoursFte ?? ''));
    if (prefill.newbie !== undefined) setIsNewbie(Boolean(prefill.newbie));
    if (prefill.nightShift !== undefined) setNightShift(Boolean(prefill.nightShift));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillNonce]);

  // Индивидуальный план: план на 1 FTE ÷ норму 1 FTE × отработанные часы.
  // Ставка отдельно не нужна — она уже «зашита» в фактические часы.
  const planInfo = useMemo(
    () => calculateOsnovaMonthlyPlan({ planPerFte, normHoursFte, hoursWorked, newbie: isNewbie, nightShift }),
    [planPerFte, normHoursFte, hoursWorked, isNewbie, nightShift]
  );

  const result = useMemo(
    () => calculateOsnovaSalary({
      hoursWorked,
      hoursNorm,
      deals,
      planTarget: planInfo.plan,
      planPerFte,
      normHoursFte,
      newbie: isNewbie,
      nightShift,
      quality,
      fines,
      bonuses,
    }),
    [hoursWorked, hoursNorm, deals, planInfo.plan, planPerFte, normHoursFte, isNewbie, nightShift, quality, fines, bonuses]
  );

  const reset = () => {
    setHoursNorm(String(monthNormFte));
    setHoursWorked('');
    setDeals('');
    setPlanPerFte('');
    setNormHoursFte(String(monthNormFte));
    setNightShift(false);
    setIsNewbie(false);
    setQuality('');
    setFines('');
    setBonuses('');
  };

  const planPercentText = result.planTarget > 0 ? `${(result.planPercent * 100).toFixed(1).replace('.', ',')}%` : '—';

  return (
    <div>
      <div className="mb-4 inline-flex items-center gap-2 rounded-full bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-700 ring-1 ring-amber-100">
        <FaIcon className="fas fa-layer-group" />
        Модель: Оператор ОП «Основа»
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
        <Field label="Норма часов:" icon="fa-bullseye" iconColor="text-purple-500">
          {numberInput(hoursNorm, setHoursNorm, { min: 0, max: 744, step: '0.01' })}
        </Field>
        <Field label="Отработанные часы:" icon="fa-briefcase" iconColor="text-indigo-500">
          {numberInput(hoursWorked, setHoursWorked, { min: 0, max: 744, step: '0.01' })}
          <div className="mt-2 text-xs text-gray-500">
            Постоянная часть: <span className="font-medium text-gray-700">{Math.round(result.oklad).toLocaleString('ru-RU')} ₸</span>
            <span className="ml-1 text-gray-400">— часы × {OSNOVA_HOURLY_RATE} ₸</span>
          </div>
        </Field>

        <Field label="План сделок на 1 FTE:" icon="fa-bullseye" iconColor="text-rose-500">
          {numberInput(planPerFte, setPlanPerFte, { min: 0, step: '0.01' })}
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
            <label className="flex items-center gap-2 text-xs text-gray-600">
              <input
                type="checkbox"
                checked={nightShift}
                onChange={(e) => setNightShift(e.target.checked)}
                className="h-4 w-4 rounded border-gray-300 text-amber-600 focus:ring-amber-500"
              />
              Ночная смена (план ÷ 2)
            </label>
            <label className="flex items-center gap-2 text-xs text-gray-600">
              <input
                type="checkbox"
                checked={isNewbie}
                onChange={(e) => setIsNewbie(e.target.checked)}
                className="h-4 w-4 rounded border-gray-300 text-amber-600 focus:ring-amber-500"
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
        <Field label="Успешных сделок:" icon="fa-check-circle" iconColor="text-green-500">
          {numberInput(deals, setDeals, { min: 0, step: '1' })}
          <div className="mt-2 text-xs text-gray-500">
            Выполнение плана: <span className="font-medium text-gray-700">{planPercentText}</span>
            <span className="ml-1 text-gray-400">— цена сделки {result.dealPrice} ₸</span>
          </div>
        </Field>

        <Field label="Качество звонков (%):" icon="fa-star" iconColor="text-yellow-500">
          {numberInput(quality, setQuality, { min: 0, max: 100, step: '0.01' })}
          <div className="mt-2 text-xs text-gray-500">
            Удержание с бонуса: <span className="font-medium text-gray-700">{(result.qualityWithholdRate * 100).toFixed(0)}%</span>
            <span className="ml-1 text-gray-400">— {Math.round(result.qualityWithheld).toLocaleString('ru-RU')} ₸</span>
          </div>
        </Field>
        <Field label="Штрафы (₸):" icon="fa-triangle-exclamation" iconColor="text-red-500">
          {numberInput(fines, setFines, { min: 0, step: '0.01' })}
        </Field>
        <Field label="Премии (₸):" icon="fa-gift" iconColor="text-pink-500">
          {numberInput(bonuses, setBonuses, { min: 0, step: '0.01' })}
          <div className="mt-2 text-xs text-gray-500">
            Лучшему по продажам в час — {OSNOVA_BEST_OPERATOR_PREMIUM.toLocaleString('ru-RU')} ₸ на 1 FTE.
          </div>
        </Field>
      </div>

      <div className="flex justify-center mt-6">
        <button
          onClick={reset}
          className="w-full sm:w-auto px-6 py-3 rounded-xl font-bold text-sm bg-red-500 text-white hover:bg-red-600 shadow transition"
        >
          <FaIcon className="fas fa-eraser mr-2" /> Очистить
        </button>
      </div>

      {/* Результат — общая карточка со СЗоВ и TEZ: шапка, сводка, компоненты, итог, детали */}
      <SalaryCalculationResult salaryResult={result} label="Оператор ОП «Основа»" />
    </div>
  );
};

export default SalaryCalculatorOsnova;
