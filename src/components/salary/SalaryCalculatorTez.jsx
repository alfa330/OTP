import React, { useState, useEffect, useMemo } from 'react';
import FaIcon from '../common/FaIcon';
import SalaryCalculationResult from './SalaryCalculationResult';
import { calculateTezLineSalary, calculateTezOpSalary, calculateTezOpMonthlyPlan, TEZ_NORM_HOURS } from '../../utils/salaryFormula';

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

/**
 * Калькулятор зарплаты для направлений отдела TEZ.
 * model: 'tez_line' (Линия/тех поддержка) | 'tez_op' (ОП).
 * Формулы — src/utils/salaryFormula.js (выведены из таблиц расчёта владельца).
 * planPrefill (для ОП): { plan_target, plan_fact } из /api/operator_plan, если есть.
 * hoursPrefill: часы/норма/план/факт, перенесённые из раздела «Мои часы»
 * (применяется по смене hoursPrefillNonce, чтобы повторный переход перезаполнял поля).
 */
const SalaryCalculatorTez = ({ model = 'tez_line', planPrefill = null, hoursPrefill = null, hoursPrefillNonce = 0 }) => {
  const isOp = model === 'tez_op';
  const [hoursNorm, setHoursNorm] = useState(String(TEZ_NORM_HOURS));
  const [hoursWorked, setHoursWorked] = useState('');
  const [quality, setQuality] = useState('');
  const [experienceMonths, setExperienceMonths] = useState('');
  const [planPerFte, setPlanPerFte] = useState('');
  const [planFact, setPlanFact] = useState('');
  const [isNewbie, setIsNewbie] = useState(false);
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

  // Переход из «Моих часов»: подставляем реальные часы месяца, норму и — для ОП —
  // план с фактом успешек, чтобы оператор видел ровно свой расчёт.
  useEffect(() => {
    if (!hoursPrefill) return;
    if (hoursPrefill.model && hoursPrefill.model !== model) return;
    if (hoursPrefill.hoursNorm !== undefined) setHoursNorm(String(hoursPrefill.hoursNorm ?? ''));
    if (hoursPrefill.hoursWorked !== undefined) setHoursWorked(String(hoursPrefill.hoursWorked ?? ''));
    if (hoursPrefill.fines !== undefined) setFines(String(hoursPrefill.fines ?? ''));
    if (hoursPrefill.bonuses !== undefined) setBonuses(String(hoursPrefill.bonuses ?? ''));
    if (isOp) {
      if (hoursPrefill.planPerFte !== undefined) setPlanPerFte(String(hoursPrefill.planPerFte ?? ''));
      if (hoursPrefill.planFact !== undefined) setPlanFact(String(hoursPrefill.planFact ?? ''));
      setIsNewbie(Boolean(hoursPrefill.newbie));
    } else {
      if (hoursPrefill.quality !== undefined) setQuality(String(hoursPrefill.quality ?? ''));
      if (hoursPrefill.experienceMonths !== undefined) setExperienceMonths(String(hoursPrefill.experienceMonths ?? ''));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hoursPrefillNonce]);

  // Индивидуальный план по правилам владельца: ставка / переработка / новичок ×0,8
  // (см. calculateTezOpMonthlyPlan). Ставка выводится из нормы (норма / 176).
  const planResult = useMemo(() => {
    const norm = parseFloat(hoursNorm) || TEZ_NORM_HOURS;
    return calculateTezOpMonthlyPlan({
      planPerFte,
      rate: norm / TEZ_NORM_HOURS,
      normHours: norm,
      factHours: hoursWorked,
      newbie: isNewbie,
    });
  }, [planPerFte, hoursNorm, hoursWorked, isNewbie]);
  const individualPlan = planResult.plan || 0;

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
    setIsNewbie(false);
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
              <label className="mt-2 flex items-center gap-2 text-xs text-gray-600">
                <input
                  type="checkbox"
                  checked={isNewbie}
                  onChange={(e) => setIsNewbie(e.target.checked)}
                  className="h-4 w-4 rounded border-gray-300 text-teal-600 focus:ring-teal-500"
                />
                Новичок (план ×0,8)
              </label>
              <div className="mt-2 text-xs text-gray-500">
                Индивидуальный план: <span className="font-medium text-gray-700">{individualPlan.toFixed(1)}</span>
                {planResult.caseCode !== 'no_plan' && (
                  <span className="ml-1 text-gray-400">— {planResult.caseLabel.toLowerCase()}</span>
                )}
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

      {/* Результат — общая карточка со СЗоВ: шапка, сводка, компоненты, итог, детали */}
      <SalaryCalculationResult
        salaryResult={result}
        label={isOp ? 'Оператор ОП TEZ' : 'Оператор Линия TEZ'}
      />
    </div>
  );
};

export default SalaryCalculatorTez;
