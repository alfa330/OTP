import React, { useMemo, useState } from 'react';
import { calculateTezOpMonthlyPlan } from '../../utils/salaryFormula';

/**
 * Ячейка «План успешек» в учёте часов (модель ОП TEZ).
 * Показывает индивидуальный план оператора и тултип с пошаговым расчётом
 * по правилам владельца (ставка / переработка / новичок / пересчитанная норма).
 * Тултип позиционируется fixed — не обрезается overflow-контейнером таблицы.
 */
const TezOpPlanCell = ({ planPerFte, rate, normHours, factHours, hireDate, month }) => {
  const [tip, setTip] = useState(null); // {x, y}

  const result = useMemo(
    () => calculateTezOpMonthlyPlan({ planPerFte, rate, normHours, factHours, hireDate, month }),
    [planPerFte, rate, normHours, factHours, hireDate, month]
  );

  const hasPlan = result.plan != null;
  const badgeClass = !hasPlan
    ? 'bg-gray-100 text-gray-400'
    : result.isNewbie
      ? 'bg-amber-50 text-amber-700 ring-1 ring-amber-200'
      : result.overtime
        ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200'
        : 'bg-teal-50 text-teal-700 ring-1 ring-teal-200';

  const showTip = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const half = 148; // половина ширины тултипа (w-72 ≈ 288px) + отступ
    const x = Math.min(Math.max(r.left + r.width / 2, half + 8), window.innerWidth - half - 8);
    setTip({ x, y: r.bottom + 6 });
  };

  return (
    <span
      className="relative inline-flex w-full justify-center"
      onMouseEnter={showTip}
      onMouseLeave={() => setTip(null)}
    >
      <span className={`inline-flex cursor-default items-center rounded-md px-2 py-1 text-sm font-semibold ${badgeClass}`}>
        {hasPlan ? result.plan.toLocaleString('ru-RU', { maximumFractionDigits: 1 }) : '—'}
      </span>
      {tip && (
        <span
          className="pointer-events-none fixed z-[200] w-72 -translate-x-1/2 rounded-xl border border-slate-200 bg-white px-3.5 py-3 text-left shadow-xl ring-1 ring-slate-900/5"
          style={{ left: tip.x, top: tip.y }}
        >
          <span className="mb-1.5 block text-xs font-semibold text-slate-800">{result.caseLabel}</span>
          {(result.lines || []).map((line, i) => (
            <span key={i} className="mt-1 block text-[11px] leading-4 text-slate-600">
              {line}
            </span>
          ))}
        </span>
      )}
    </span>
  );
};

export default TezOpPlanCell;
