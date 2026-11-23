import React, { useState, useEffect, useMemo } from 'react';
import FaIcon from '../common/FaIcon';
import SalaryCalculationResult from './SalaryCalculationResult';
import {
  YANDEX_REG_HOURLY_RATE,
  YANDEX_REG_TARGET_CONVERSION,
  calculateYandexRegSalary,
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

const fmtPercent = (share) => `${(Number(share) * 100).toFixed(1).replace('.', ',')}%`;

/**
 * Калькулятор зарплаты направления «Яндекс Регистрация» отдела продаж
 * (модель op_yandex_reg). Формулы — src/utils/salaryFormula.js, перенесены из
 * «KPI.xlsx» (лист «ЯР») и пояснения владельца «KPI_логика_и_калькулятор_ЗП.xlsx».
 * С SALES-2 (#427) всё личное: конверсия — свои успешно закрытые ÷ свои заявки
 * (план 45 %), те же успешно закрытые умножаются на цену успешки.
 * prefill: часы/норма/качество/штрафы из «Моих часов» (по смене prefillNonce).
 * month: 'YYYY-MM' — от него зависит норма часов на 1 FTE (176 / 168 / 160).
 */
const SalaryCalculatorYandexReg = ({ prefill = null, prefillNonce = 0, month = '' }) => {
  const monthNormFte = opFteNormHoursForMonth(month);
  const [hoursNorm, setHoursNorm] = useState(String(monthNormFte));
  const [hoursWorked, setHoursWorked] = useState('');
  const [hourlyRate, setHourlyRate] = useState(String(YANDEX_REG_HOURLY_RATE));
  const [requests, setRequests] = useState('');
  const [successes, setSuccesses] = useState('');
  const [targetConversion, setTargetConversion] = useState(String(YANDEX_REG_TARGET_CONVERSION * 100));
  const [quality, setQuality] = useState('');
  const [fines, setFines] = useState('');
  const [bonuses, setBonuses] = useState('');

  // Переход из «Моих часов»: часы месяца, норма, качество, штрафы и премии.
  // Заявки и успешно закрытые оператор вносит руками — источника в системе нет.
  useEffect(() => {
    if (!prefill) return;
    if (prefill.hoursNorm !== undefined) setHoursNorm(String(prefill.hoursNorm ?? ''));
    if (prefill.hoursWorked !== undefined) setHoursWorked(String(prefill.hoursWorked ?? ''));
    if (prefill.quality !== undefined) setQuality(String(prefill.quality ?? ''));
    if (prefill.fines !== undefined) setFines(String(prefill.fines ?? ''));
    if (prefill.bonuses !== undefined) setBonuses(String(prefill.bonuses ?? ''));
    if (prefill.requests !== undefined) setRequests(String(prefill.requests ?? ''));
    if (prefill.successes !== undefined) setSuccesses(String(prefill.successes ?? ''));
    if (prefill.targetConversion !== undefined) setTargetConversion(String(prefill.targetConversion ?? ''));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillNonce]);

  // Целевая конверсия вводится в процентах, в формулу уходит долей.
  const targetShare = useMemo(() => {
    const n = parseFloat(targetConversion);
    return Number.isFinite(n) ? n / 100 : 0;
  }, [targetConversion]);

  const result = useMemo(
    () => calculateYandexRegSalary({
      hoursWorked,
      hoursNorm,
      hourlyRate,
      requests,
      successes,
      targetConversion: targetShare,
      quality,
      fines,
      bonuses,
    }),
    [hoursWorked, hoursNorm, hourlyRate, requests, successes, targetShare, quality, fines, bonuses]
  );

  const reset = () => {
    setHoursNorm(String(monthNormFte));
    setHoursWorked('');
    setHourlyRate(String(YANDEX_REG_HOURLY_RATE));
    setRequests('');
    setSuccesses('');
    setTargetConversion(String(YANDEX_REG_TARGET_CONVERSION * 100));
    setQuality('');
    setFines('');
    setBonuses('');
  };

  const hasConversionInputs = result.requests > 0;

  return (
    <div>
      <div className="mb-4 inline-flex items-center gap-2 rounded-full bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 ring-1 ring-red-100">
        <FaIcon className="fas fa-id-card" />
        Модель: Оператор ОП «Яндекс Регистрация»
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
        <Field label="Норма часов:" icon="fa-bullseye" iconColor="text-purple-500">
          {numberInput(hoursNorm, setHoursNorm, { min: 0, max: 744, step: '0.01' })}
        </Field>
        <Field label="Отработанные часы:" icon="fa-briefcase" iconColor="text-indigo-500">
          {numberInput(hoursWorked, setHoursWorked, { min: 0, max: 744, step: '0.01' })}
          <div className="mt-2 text-xs text-gray-500">
            Оклад: <span className="font-medium text-gray-700">{Math.round(result.oklad).toLocaleString('ru-RU')} ₸</span>
            <span className="ml-1 text-gray-400">— часы × ставку</span>
          </div>
        </Field>

        <Field label="Ставка, ₸/час:" icon="fa-coins" iconColor="text-amber-500">
          {numberInput(hourlyRate, setHourlyRate, { min: 0, step: '1' })}
          <div className="mt-2 text-xs text-gray-500">
            По схеме — {YANDEX_REG_HOURLY_RATE} ₸/час за каждый отработанный час.
          </div>
        </Field>
        <Field label="Целевая конверсия (%):" icon="fa-flag-checkered" iconColor="text-rose-500">
          {numberInput(targetConversion, setTargetConversion, { min: 0, max: 100, step: '0.1' })}
          <div className="mt-2 text-xs text-gray-500">
            По схеме — {YANDEX_REG_TARGET_CONVERSION * 100}%.
          </div>
        </Field>

        <Field label="Поступило заявок:" icon="fa-inbox" iconColor="text-sky-500">
          {numberInput(requests, setRequests, { min: 0, step: '1' })}
        </Field>
        <Field label="Успешно закрыто:" icon="fa-circle-check" iconColor="text-green-500">
          {numberInput(successes, setSuccesses, { min: 0, step: '1' })}
          <div className="mt-2 text-xs text-gray-500">
            Факт. конверсия: <span className="font-medium text-gray-700">{hasConversionInputs ? fmtPercent(result.factConversion) : '—'}</span>
            <span className="ml-1 text-gray-400">— успешные ÷ поступившие</span>
          </div>
          <div className="mt-1 text-xs text-gray-500">
            Цена успешки: <span className="font-medium text-gray-700">{result.dealPrice} ₸</span>
            <span className="ml-1 text-gray-400">— ступень по % плана, бонус {Math.round(result.bonusDeals).toLocaleString('ru-RU')} ₸</span>
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
        </Field>
      </div>

      <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-600">
        Выполнение плана конверсии:
        <span className="font-semibold text-gray-800"> {result.targetConversion > 0 && hasConversionInputs ? fmtPercent(result.planPercent) : '—'}</span>
        <span className="ml-1 text-gray-400">
          — факт. конверсия ÷ целевую; заявки, успешки и качество — личные
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
      <SalaryCalculationResult salaryResult={result} label="Оператор ОП «Яндекс Регистрация»" />
    </div>
  );
};

export default SalaryCalculatorYandexReg;
