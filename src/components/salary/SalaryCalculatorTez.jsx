import React, { useState, useEffect, useMemo } from 'react';
import FaIcon from '../common/FaIcon';
import { calculateTezLineSalary, calculateTezOpSalary, TEZ_NORM_HOURS } from '../../utils/salaryFormula';

const money = (v) =>
  new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(
    Number.isFinite(Number(v)) ? Number(v) : 0
  ) + ' ₸';

const pct = (v) => `${(Number(v) || 0).toFixed(2)}%`;

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

const Row = ({ label, value, strong, alt }) => (
  <div className={`flex items-center justify-between gap-3 px-3 sm:px-4 py-2 ${alt ? 'bg-gray-50' : 'bg-white'}`}>
    <div className="text-sm text-gray-600">{label}</div>
    <div className={`text-right ${strong ? 'font-semibold text-gray-900' : 'font-medium text-gray-800'}`}>{value}</div>
  </div>
);

/**
 * Калькулятор зарплаты для направлений отдела TEZ.
 * model: 'tez_line' (Линия/тех поддержка) | 'tez_op' (ОП).
 * Формулы — src/utils/salaryFormula.js (выведены из таблиц расчёта владельца).
 * planPrefill (для ОП): { plan_target, plan_fact } из /api/operator_plan, если есть.
 */
const SalaryCalculatorTez = ({ model = 'tez_line', planPrefill = null }) => {
  const isOp = model === 'tez_op';
  const [hoursNorm, setHoursNorm] = useState(String(TEZ_NORM_HOURS));
  const [hoursWorked, setHoursWorked] = useState('');
  const [quality, setQuality] = useState('');
  const [experienceMonths, setExperienceMonths] = useState('');
  const [planPerFte, setPlanPerFte] = useState('');
  const [planFact, setPlanFact] = useState('');
  const [fines, setFines] = useState('');
  const [withholding, setWithholding] = useState('');
  const [bonuses, setBonuses] = useState('');

  // Подтягиваем общий (на 1 FTE) план месяца, внесённый СВ/главой (только для ОП).
  useEffect(() => {
    if (!isOp || !planPrefill) return;
    if (planPrefill.plan_per_fte !== undefined && planPrefill.plan_per_fte !== null) {
      setPlanPerFte(String(planPrefill.plan_per_fte));
    }
  }, [isOp, planPrefill]);

  // Индивидуальный план = план на 1 FTE × (норма часов / 176).
  const individualPlan = useMemo(() => {
    const perFte = parseFloat(planPerFte) || 0;
    const norm = parseFloat(hoursNorm) || TEZ_NORM_HOURS;
    return perFte * (norm / TEZ_NORM_HOURS);
  }, [planPerFte, hoursNorm]);

  const result = useMemo(() => {
    const common = {
      hoursWorked,
      hoursNorm,
      fines,
      withholding,
      bonuses,
    };
    return isOp
      ? calculateTezOpSalary({ ...common, planTarget: individualPlan, planFact })
      : calculateTezLineSalary({ ...common, quality, experienceMonths });
  }, [isOp, hoursWorked, hoursNorm, fines, withholding, bonuses, quality, experienceMonths, individualPlan, planFact]);

  const reset = () => {
    setHoursNorm(String(TEZ_NORM_HOURS));
    setHoursWorked('');
    setQuality('');
    setExperienceMonths('');
    setPlanPerFte('');
    setPlanFact('');
    setFines('');
    setWithholding('');
    setBonuses('');
  };

  return (
    <div>
      <div className="mb-4 inline-flex items-center gap-2 rounded-full bg-teal-50 px-3 py-1.5 text-xs font-semibold text-teal-700 ring-1 ring-teal-100">
        <FaIcon className="fas fa-headset" />
        {isOp ? 'Модель: Оператор ОП TEZ' : 'Модель: Оператор Линия TEZ'}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
        <Field label="Норма часов:" icon="fa-bullseye" iconColor="text-purple-500">
          {numberInput(hoursNorm, setHoursNorm, { min: 0, max: 744, step: '0.01' })}
        </Field>
        <Field label="Отработанные часы:" icon="fa-briefcase" iconColor="text-indigo-500">
          {numberInput(hoursWorked, setHoursWorked, { min: 0, max: 744, step: '0.01' })}
        </Field>

        {!isOp && (
          <>
            <Field label="Качество (%):" icon="fa-star" iconColor="text-yellow-500">
              {numberInput(quality, setQuality, { min: 0, max: 100, step: '0.01' })}
            </Field>
            <Field label="Стаж (месяцев):" icon="fa-user-clock" iconColor="text-blue-500">
              {numberInput(experienceMonths, setExperienceMonths, { min: 0, step: '0.1' })}
            </Field>
          </>
        )}

        {isOp && (
          <>
            <Field label="План успешек (на 1 FTE):" icon="fa-bullseye" iconColor="text-rose-500">
              {numberInput(planPerFte, setPlanPerFte, { min: 0, step: '0.01' })}
              <div className="mt-2 text-xs text-gray-500">
                Индивидуальный план (× норма/176): <span className="font-medium text-gray-700">{individualPlan.toFixed(1)}</span>
              </div>
            </Field>
            <Field label="Факт успешек:" icon="fa-check-circle" iconColor="text-green-500">
              {numberInput(planFact, setPlanFact, { min: 0, step: '0.01' })}
            </Field>
          </>
        )}

        <Field label="Штрафы (₸):" icon="fa-triangle-exclamation" iconColor="text-red-500">
          {numberInput(fines, setFines, { min: 0, step: '0.01' })}
        </Field>
        <Field label="Удержано 50% (₸):" icon="fa-percent" iconColor="text-orange-500">
          {numberInput(withholding, setWithholding, { min: 0, step: '0.01' })}
        </Field>
        <Field label="Бонусы (₸):" icon="fa-gift" iconColor="text-pink-500">
          {numberInput(bonuses, setBonuses, { min: 0, step: '0.01' })}
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

      {/* Результат */}
      <div className="mt-6 p-4 sm:p-6 bg-gray-50 rounded-lg shadow-sm border border-gray-200">
        <h3 className="text-lg font-semibold mb-1 text-gray-800">Результат расчёта</h3>
        <p className="text-sm text-gray-500 mb-4">{isOp ? 'Оклад + бонус за успешки' : 'Оклад + бонус за качество + бонус за стаж'}</p>

        <div className="divide-y divide-gray-100 rounded-lg overflow-hidden border bg-white">
          <Row label={`Оклад (${money(isOp ? 150000 / 176 : 100000 / 176)}/ч × ${(Number(hoursWorked) || 0)} ч)`} value={money(result.oklad)} />
          {!isOp && (
            <>
              <Row alt label={`Бонус за качество (${pct((result.qualityPercent || 0) * 100)} к окладу)`} value={money(result.bonusQuality)} />
              <Row label={`Бонус за стаж (${pct((result.seniorityPercent || 0) * 100)})`} value={money(result.bonusSeniority)} />
            </>
          )}
          {isOp && (
            <Row alt label={`Бонус за успешки (% сделок ${pct((result.dealPercent || 0) * 100)})`} value={money(result.bonusDeals)} />
          )}
          <Row alt={!isOp} label="Штрафы" value={`− ${money(result.fines)}`} />
          <Row alt={isOp} label="Удержано 50%" value={`− ${money(result.withholding)}`} />
          <Row label="Бонусы" value={`+ ${money(result.bonuses)}`} />
        </div>

        <div className="mt-4 bg-white p-4 rounded border-l-4 border-l-green-500 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
          <div>
            <div className="text-sm text-gray-600">Итого к выплате</div>
            <div className="text-xl sm:text-2xl font-bold text-green-600 mt-1 break-words">{money(result.finalSalary)}</div>
          </div>
          <div className="text-sm text-gray-600 text-left sm:text-right">
            <div>Норма часов: <span className="font-medium text-gray-800">{(Number(hoursNorm) || 0).toFixed(2)}</span></div>
            <div className="mt-1">Выполнение нормы: <span className="font-medium text-gray-800">{pct(result.hoursPercentage)}</span></div>
            {isOp && (
              <div className="mt-1">% сделок: <span className="font-medium text-gray-800">{pct((result.dealPercent || 0) * 100)}</span></div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default SalaryCalculatorTez;
