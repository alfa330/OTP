import React from 'react';
import InfoHint from '../common/InfoHint';

const fmt = (value, digits = 1) => value == null ? '—'
  : Number(value).toLocaleString('ru-RU', { maximumFractionDigits: digits });

export default function TezDepartmentPlanSummary({ summary }) {
  if (!summary) return null;
  const percentClass = summary.closure_pct == null ? 'text-slate-400'
    : summary.closure_pct >= 100 ? 'text-emerald-700'
      : summary.closure_pct >= 60 ? 'text-amber-600' : 'text-rose-600';
  return (
    <div className="rounded-xl bg-slate-50 px-4 py-3">
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4">
        <div>
          <div className="flex items-center gap-1 text-xs text-slate-500">
            Групповой план на месяц
            <InfoHint title="План на начало месяца" side="right">
              План на 1 FTE ({fmt(summary.plan_per_fte)}) × FTE на 1 число
              {' '}({fmt(summary.fte_total, 2)}) × 0,8. Состав и ставки зафиксированы:
              приём, увольнение и отсутствие сотрудника в течение месяца этот FTE не меняют.
            </InfoHint>
          </div>
          <div className="mt-1 text-xl font-semibold tabular-nums text-slate-800">{fmt(summary.plan_total)}</div>
        </div>
        <div>
          <div className="flex items-center gap-1 text-xs text-slate-500">
            Фактический план отдела
            <InfoHint title="План по отработанным часам" side="right">
              Групповой план ÷ FTE на 1 число × FTE по отработанным часам.
              {' '}Учтено {fmt(summary.actual_hours, 2)} ч ÷ {fmt(summary.norm_hours_fte, 2)} ч на 1 FTE
              {' '}= {fmt(summary.actual_fte, 4)} FTE. Учитываются часы всех операторов отдела,
              включая принятых и уволенных в течение месяца.
            </InfoHint>
          </div>
          <div className="mt-1 text-xl font-semibold tabular-nums text-slate-800">{fmt(summary.actual_plan)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500">Факт продаж</div>
          <div className="mt-1 text-xl font-semibold tabular-nums text-slate-800">{fmt(summary.successes_total, 0)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500">Выполнение фактического плана</div>
          <div className={`mt-1 text-xl font-semibold tabular-nums ${percentClass}`}>
            {summary.closure_pct == null ? '—' : `${fmt(summary.closure_pct)}%`}
          </div>
        </div>
      </div>
      {summary.fte_total == null ? (
        <p className="mt-2 text-xs text-slate-500">FTE будет зафиксирован при наступлении месяца.</p>
      ) : summary.fte_total === 0 ? (
        <p className="mt-2 text-xs text-slate-500">На 1 число в отделе нет FTE — фактический план не рассчитывается.</p>
      ) : !(summary.plan_per_fte > 0) && (
        <p className="mt-2 text-xs text-slate-500">План на 1 FTE за этот месяц не задан.</p>
      )}
    </div>
  );
}
