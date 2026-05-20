import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import ResourceSchedulePlanner from './ResourceSchedulePlanner';
import {
  AlertTriangle,
  BarChart3,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ChevronDown,
  CheckCircle2,
  Clock3,
  Eye,
  EyeOff,
  FileUp,
  Gavel,
  LayoutDashboard,
  ListChecks,
  Minus,
  RefreshCw,
  Save,
  Settings,
  SlidersHorizontal,
  PhoneCall,
  PhoneMissed,
  ShieldAlert,
  TrendingUp,
  UploadCloud,
  Users,
  X,
} from 'lucide-react';
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  Area,
  AreaChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

const todayIso = () => new Date().toISOString().slice(0, 10);
const monthStartIso = () => {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
};

const formatNumber = (value, digits = 1) => {
  const number = Number(value || 0);
  return new Intl.NumberFormat('ru-RU', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(number);
};

const formatSignedNumber = (value, digits = 1) => {
  const number = Number(value || 0);
  const sign = number > 0 ? '+' : '';
  return `${sign}${formatNumber(number, digits)}`;
};

const formatInt = (value) => new Intl.NumberFormat('ru-RU').format(Math.round(Number(value || 0)));

const formatPercent = (value, digits = 1) => `${formatNumber(Number(value || 0) * 100, digits)}%`;

const calculateForecastMatchPercent = (fact, forecast) => {
  const forecastNumber = Number(forecast || 0);
  if (forecastNumber <= 0) return 0;
  return Math.max(0, 100 - (Math.abs(Number(fact || 0) - forecastNumber) / forecastNumber) * 100);
};

const formatDate = (iso) => {
  if (!iso) return '-';
  const [year, month, day] = String(iso).split('-');
  return day && month && year ? `${day}.${month}.${year}` : iso;
};

const parseIsoDate = (iso) => {
  const [year, month, day] = String(iso || '').split('-').map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
};

const toIsoDate = (date) => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const monthLabel = (date) =>
  new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric' }).format(date);

const daysBetweenInclusive = (startIso, endIso) => {
  const start = parseIsoDate(startIso);
  const end = parseIsoDate(endIso);
  if (!start || !end) return 0;
  return Math.round((end - start) / 86400000) + 1;
};

const buildCalendarDays = (monthDate) => {
  const first = new Date(monthDate.getFullYear(), monthDate.getMonth(), 1);
  const startOffset = (first.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(first.getDate() - startOffset);
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    return date;
  });
};

const isIsoInRange = (iso, startIso, endIso) => {
  if (!iso || !startIso || !endIso) return false;
  return iso >= startIso && iso <= endIso;
};

const addDaysIso = (iso, days) => {
  const [year, month, day] = String(iso || todayIso()).split('-').map(Number);
  const date = new Date(year, (month || 1) - 1, day || 1);
  date.setDate(date.getDate() + days);
  const nextYear = date.getFullYear();
  const nextMonth = String(date.getMonth() + 1).padStart(2, '0');
  const nextDay = String(date.getDate()).padStart(2, '0');
  return `${nextYear}-${nextMonth}-${nextDay}`;
};

const hourFromChartLabel = (label) => {
  const match = String(label || '').match(/^(\d{1,2})/);
  if (!match) return null;
  const hour = Number(match[1]);
  return Number.isFinite(hour) ? hour : null;
};

const getWeekStartIso = (iso) => {
  const date = parseIsoDate(iso) || new Date();
  const dayOffset = (date.getDay() + 6) % 7;
  date.setDate(date.getDate() - dayOffset);
  return toIsoDate(date);
};

const getNextWeekStartIso = (iso = todayIso()) => addDaysIso(getWeekStartIso(iso), 7);

const getForecastWeekDates = (weekStartIso) =>
  Array.from({ length: 7 }, (_, index) => addDaysIso(weekStartIso, index));

const getForecastHistoryWeeks = (weekStartIso) => [
  { start: addDaysIso(weekStartIso, -21), end: addDaysIso(weekStartIso, -15) },
  { start: addDaysIso(weekStartIso, -14), end: addDaysIso(weekStartIso, -8) },
];

const getForecastPeriodDates = (startIso, endIso) => {
  const days = daysBetweenInclusive(startIso, endIso);
  if (days <= 0) return [];
  return Array.from({ length: days }, (_, index) => addDaysIso(startIso, index));
};

const getForecastHistoryPeriods = (startIso, endIso) => [
  { start: addDaysIso(startIso, -21), end: addDaysIso(endIso, -21) },
  { start: addDaysIso(startIso, -14), end: addDaysIso(endIso, -14) },
];

const getForecastHistoryDatesForDay = (forecastDateIso) => [
  addDaysIso(forecastDateIso, -21),
  addDaysIso(forecastDateIso, -14),
];

const isForecastDayHistoryComplete = (forecastDateIso, loadedSet) =>
  getForecastHistoryDatesForDay(forecastDateIso).every((date) => loadedSet.has(date));

const isForecastWeekHistoryComplete = (weekStartIso, loadedSet) =>
  getForecastWeekDates(weekStartIso).every((date) => isForecastDayHistoryComplete(date, loadedSet));

const isForecastPeriodHistoryComplete = (startIso, endIso, loadedSet) =>
  getForecastPeriodDates(startIso, endIso).every((date) => isForecastDayHistoryComplete(date, loadedSet));

const formatSeconds = (seconds) => {
  const total = Math.round(Number(seconds || 0));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, '0')}`;
};

const formatPreciseNumber = (value, digits = 6) =>
  new Intl.NumberFormat('ru-RU', {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  }).format(Number(value || 0));

const formatSourceCallsTooltip = (sources = []) => {
  if (!sources.length) return 'Нет исторических значений для расчета среднего';
  const total = sources.reduce((sum, item) => sum + Number(item.calls || 0), 0);
  const avg = total / sources.length;
  return [
    'Использовано для среднего:',
    ...sources.map((item) => `${formatDate(item.date)}: ${formatInt(item.calls)} звонков`),
    `Среднее: ${formatNumber(avg, 1)}`,
  ].join('\n');
};

const formatAhtTooltip = (seconds) => [
  `AHT отображается как ${formatSeconds(seconds)}`,
  `Точное значение: ${formatPreciseNumber(seconds, 6)} сек`,
].join('\n');

const formatWorkloadTooltip = (row, answerRate) => {
  const calls = Number(row.forecast_calls || 0);
  const aht = Number(row.forecast_aht_seconds || 0);
  const acceptedRate = Number(answerRate || 0);
  const calculated = calls * acceptedRate * aht / 60;
  return [
    'Минуты нагрузки считаются без визуального округления:',
    `звонки: ${formatPreciseNumber(calls, 6)}`,
    `AHT: ${formatPreciseNumber(aht, 6)} сек`,
    `процент принятых: ${formatPreciseNumber(acceptedRate, 6)}`,
    `${formatPreciseNumber(calls, 6)} * ${formatPreciseNumber(aht, 6)} * ${formatPreciseNumber(acceptedRate, 6)} / 60 = ${formatPreciseNumber(calculated, 6)}`,
    `значение из расчета: ${formatPreciseNumber(row.forecast_workload_minutes, 6)} мин`,
  ].join('\n');
};

const formatIncidentUpliftTooltip = (row) => {
  const sources = row?.incident_uplift_sources || row?.incidentUpliftSources || [];
  const futureWeight = Number(row?.incident_future_weight ?? row?.incidentFutureWeight ?? 1);
  const confidence = Number(row?.incident_uplift_confidence ?? row?.incidentUpliftConfidence ?? 0);
  const baseRatio = Number(row?.incident_base_uplift_ratio ?? row?.incidentBaseUpliftRatio ?? 0);
  const rawRatio = Number(row?.incident_raw_uplift_ratio ?? row?.incidentRawUpliftRatio ?? 0);
  const modelLines = [
    Number.isFinite(rawRatio) && rawRatio > 0 ? `сырой риск: ${formatPercent(rawRatio, 0)}` : null,
    Number.isFinite(confidence) && confidence > 0 ? `надежность часа: ${formatPercent(confidence, 0)}` : null,
    Number.isFinite(baseRatio) && baseRatio > 0 ? `после сглаживания: ${formatPercent(baseRatio, 0)}` : null,
    Number.isFinite(futureWeight) && futureWeight > 0 ? `вес будущего дня: ${formatPercent(futureWeight, 0)}` : null,
  ].filter(Boolean);
  if (!sources.length) {
    return [
      ...modelLines,
      'Нет данных последних 6 дней для этого часа',
    ].join('\n');
  }
  return [
    ...modelLines,
    'Прирост считается только по превышению факта над прогнозом:',
    ...sources.map((item) => {
      const delta = Number(item.delta_calls || 0);
      const ratio = Number(item.growth_ratio || 0);
      return `${formatDate(item.date)} · вес ${formatNumber(item.weight, 0)} · факт ${formatNumber(item.actual_calls, 1)} / прогноз ${formatNumber(item.forecast_calls, 1)} · +${formatNumber(delta, 1)} (${formatPercent(ratio, 0)})`;
    }),
  ].join('\n');
};

const formatActualLoadTooltip = (row, effectiveMinutes) => {
  const accepted = Number(row.actual_accepted_calls || 0);
  const talkSeconds = Number(row.actual_talk_time_seconds || 0);
  const aht = accepted > 0 ? talkSeconds / accepted : 0;
  const workload = talkSeconds / 60;
  const fte = Number(effectiveMinutes || 0) > 0 ? workload / Number(effectiveMinutes || 0) : 0;
  return [
    'Факт нагрузки считается по загруженному отчету за этот день:',
    `принятые звонки: ${formatPreciseNumber(accepted, 6)}`,
    `сумма времени разговора: ${formatPreciseNumber(talkSeconds, 6)} сек`,
    `AHT факта: ${formatPreciseNumber(talkSeconds, 6)} / ${formatPreciseNumber(accepted, 6)} = ${formatPreciseNumber(aht, 6)} сек`,
    `минуты нагрузки: ${formatPreciseNumber(talkSeconds, 6)} / 60 = ${formatPreciseNumber(workload, 6)}`,
    `FTE из отчета: ${formatPreciseNumber(workload, 6)} / ${formatPreciseNumber(effectiveMinutes, 6)} = ${formatPreciseNumber(fte, 6)}`,
  ].join('\n');
};

const inputClass =
  'h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100';

const DISPLAY_PREFERENCES_STORAGE_KEY = 'otp_resource_fte_display_v1';

const VIEW_TABS = [
  { key: 'overview', label: 'Обзор', icon: LayoutDashboard },
  { key: 'next_week', label: 'Прогнозы', icon: TrendingUp },
  { key: 'schedule_planner', label: 'Графики', icon: CalendarDays },
  { key: 'losses', label: 'Звонки', icon: PhoneCall },
  { key: 'settings', label: 'Настройки', icon: SlidersHorizontal },
];

const DEFAULT_DISPLAY_OPTIONS = {
  metricOperators: true,
  metricWeeklyFte: true,
  metricBaseOperators: true,
  metricHistoryWarnings: true,
  metricLostCalls: true,
  metricLossRate: true,
  chartCalls: true,
  chartFte: true,
  chartActual: true,
  chartLosses: true,
  chartLossRate: true,
  // Прогнозы — карточки KPI периода (главные: FTE-часы + Операторы)
  forecastKpiFteHours: true,
  forecastKpiOperators: true,
  forecastKpiAht: false,
  forecastKpiAnswerRate: false,
  forecastKpiOccUr: false,
  forecastKpiShrinkage: false,
  forecastKpiUplift: false,
  // Прогнозы — серии графика
  forecastChartCalls: true,
  forecastChartUplift: true,
  forecastChartWorkload: true,
  forecastChartFte: true,
  forecastChartAdjustedFte: true,
  forecastChartActualWorkload: true,
  forecastChartActualFte: true,
  // Прогнозы — колонки часовой таблицы (главные: Час/Звонки/FTE)
  forecastTableAht: false,
  forecastTableWorkload: false,
  forecastTableUplift: false,
  forecastTableAdjustedFte: false,
  forecastTableActualCalls: false,
  forecastTableActualWorkload: false,
  forecastTableActualFte: false,
  // Прогнозы — побочные блоки
  forecastShowActualLoad: false,
  forecastShowActualPeakHours: false,
};

const DISPLAY_GROUPS = [
  {
    title: 'Карточки',
    items: [
      ['metricOperators', 'Прогноз FTE периода'],
      ['metricWeeklyFte', 'Факт FTE периода'],
      ['metricBaseOperators', 'Разница FTE'],
      ['metricHistoryWarnings', 'Дни с отчетами'],
      ['metricLostCalls', 'Потерянные звонки'],
      ['metricLossRate', 'Доля потерь'],
    ],
  },
  {
    title: 'Графики',
    items: [
      ['chartCalls', 'Звонки'],
      ['chartFte', 'Сумма FTE в час - прогноз'],
      ['chartActual', 'Сумма FTE в час - факт'],
      ['chartLosses', 'Потери'],
      ['chartLossRate', 'Доля потерь'],
    ],
  },
  {
    title: 'Прогнозы · KPI',
    items: [
      ['forecastKpiFteHours', 'FTE-часы периода'],
      ['forecastKpiOperators', 'Операторы'],
      ['forecastKpiUplift', 'Возможный прирост'],
      ['forecastKpiAht', 'AHT периода'],
      ['forecastKpiAnswerRate', 'Принято'],
      ['forecastKpiOccUr', 'OCC / UR'],
      ['forecastKpiShrinkage', 'Усушка'],
    ],
  },
  {
    title: 'Прогнозы · График',
    items: [
      ['forecastChartCalls', 'Звонки (бар)'],
      ['forecastChartUplift', 'Прирост звонков'],
      ['forecastChartWorkload', 'Минуты нагрузки'],
      ['forecastChartFte', 'Прогноз FTE'],
      ['forecastChartAdjustedFte', 'FTE с приростом'],
      ['forecastChartActualWorkload', 'Факт нагрузки'],
      ['forecastChartActualFte', 'Факт FTE'],
    ],
  },
  {
    title: 'Прогнозы · Таблица',
    items: [
      ['forecastTableAht', 'AHT дня'],
      ['forecastTableWorkload', 'Минут нагрузки'],
      ['forecastTableUplift', 'Прирост'],
      ['forecastTableAdjustedFte', 'FTE с приростом'],
      ['forecastTableActualCalls', 'Факт звонков'],
      ['forecastTableActualWorkload', 'Факт нагрузки'],
      ['forecastTableActualFte', 'Факт FTE'],
    ],
  },
  {
    title: 'Прогнозы · Доп.',
    items: [
      ['forecastShowActualLoad', 'Сравнивать с фактом'],
      ['forecastShowActualPeakHours', 'Пиковые часы факт'],
    ],
  },
];

const OVERVIEW_TREND_TOOLTIP_CONFIG = {
  calls: { group: 'Звонки', label: 'Факт', digits: 0, groupOrder: 1, itemOrder: 1 },
  lost: { group: 'Потери звонков', label: 'Факт', digits: 0, groupOrder: 2, itemOrder: 1 },
  lossRate: { group: 'Доля потерь', label: 'Факт', percent: true, groupOrder: 3, itemOrder: 1 },
  actualFte: { group: 'Сумма FTE в час', label: 'Факт', digits: 2, groupOrder: 4, itemOrder: 1 },
  forecastFte: { group: 'Сумма FTE в час', label: 'Прогноз', digits: 2, groupOrder: 4, itemOrder: 2 },
};

const loadDisplayOptions = () => {
  if (typeof window === 'undefined') return { ...DEFAULT_DISPLAY_OPTIONS };
  try {
    const parsed = JSON.parse(window.localStorage.getItem(DISPLAY_PREFERENCES_STORAGE_KEY) || '{}');
    return { ...DEFAULT_DISPLAY_OPTIONS, ...(parsed && typeof parsed === 'object' ? parsed : {}) };
  } catch {
    return { ...DEFAULT_DISPLAY_OPTIONS };
  }
};

const apiHeaders = (withAccessTokenHeader, extra = {}) =>
  typeof withAccessTokenHeader === 'function' ? withAccessTokenHeader(extra) : extra;

const STATCARD_TONE = {
  blue: { iconBg: 'bg-blue-50 text-blue-700 ring-blue-100', accent: 'bg-blue-500', value: 'text-slate-950' },
  emerald: { iconBg: 'bg-emerald-50 text-emerald-700 ring-emerald-100', accent: 'bg-emerald-500', value: 'text-slate-950' },
  amber: { iconBg: 'bg-amber-50 text-amber-700 ring-amber-100', accent: 'bg-amber-500', value: 'text-slate-950' },
  rose: { iconBg: 'bg-rose-50 text-rose-700 ring-rose-100', accent: 'bg-rose-500', value: 'text-slate-950' },
  slate: { iconBg: 'bg-slate-100 text-slate-700 ring-slate-200', accent: 'bg-slate-400', value: 'text-slate-950' },
};

const StatCard = ({
  icon: Icon,
  label,
  value,
  hint,
  tone = 'blue',
  emphasis = 'default',
  delta = null,
  deltaTone = 'auto',
  accent = false,
}) => {
  const toneConf = STATCARD_TONE[tone] || STATCARD_TONE.blue;
  const isCompact = emphasis === 'compact';
  const isPrimary = emphasis === 'primary';

  const deltaNumber = typeof delta === 'number' ? delta : Number(delta);
  const deltaIsNumeric = Number.isFinite(deltaNumber);
  const resolvedDeltaTone = deltaTone === 'auto'
    ? deltaIsNumeric
      ? Math.abs(deltaNumber) < 0.005 ? 'slate' : deltaNumber > 0 ? 'emerald' : 'rose'
      : 'slate'
    : deltaTone;
  const deltaClass = {
    emerald: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
    rose: 'bg-rose-50 text-rose-700 ring-rose-200',
    slate: 'bg-slate-100 text-slate-700 ring-slate-200',
    blue: 'bg-blue-50 text-blue-700 ring-blue-200',
    amber: 'bg-amber-50 text-amber-700 ring-amber-200',
  }[resolvedDeltaTone] || 'bg-slate-100 text-slate-700 ring-slate-200';

  return (
    <div className={`relative overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm ${isCompact ? 'p-3' : 'p-4'}`}>
      {accent ? <span className={`pointer-events-none absolute left-0 top-0 h-full w-1 ${toneConf.accent}`} aria-hidden="true" /> : null}
      <div className={`flex items-start justify-between gap-3 ${accent ? 'pl-1.5' : ''}`}>
        <div className="min-w-0">
          <p className={`font-semibold uppercase tracking-wide text-slate-500 ${isCompact ? 'text-[11px]' : 'text-xs'}`}>{label}</p>
          <div className={`mt-1.5 font-semibold tabular-nums ${toneConf.value} ${isPrimary ? 'text-3xl' : isCompact ? 'text-xl' : 'text-2xl'}`}>{value}</div>
          {hint ? <p className={`mt-1 text-xs text-slate-500 ${isCompact ? 'truncate' : ''}`} title={isCompact && typeof hint === 'string' ? hint : undefined}>{hint}</p> : null}
          {delta != null && delta !== '' ? (
            <span className={`mt-2 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${deltaClass}`}>
              {typeof delta === 'string' ? delta : (deltaIsNumeric && deltaNumber > 0 ? `+${formatNumber(deltaNumber, 2)}` : formatNumber(deltaNumber, 2))}
            </span>
          ) : null}
        </div>
        {Icon ? (
          <div className={`flex shrink-0 items-center justify-center rounded-lg ring-1 ${toneConf.iconBg} ${isCompact ? 'h-8 w-8' : 'h-10 w-10'}`}>
            <Icon size={isCompact ? 16 : 18} aria-hidden="true" />
          </div>
        ) : null}
      </div>
    </div>
  );
};

const OPERATOR_STATUS_LABELS = {
  working: 'Working',
  bs: 'Б/С',
  unpaid_leave: 'Б/С',
  sick_leave: 'БЛ',
  annual_leave: 'Отпуск',
  dismissal: 'Увол.',
  fired: 'Увол.',
};

const OPERATOR_STATUS_CHIP_CLASSES = {
  working: 'bg-emerald-50 text-emerald-700 ring-emerald-100',
  bs: 'bg-amber-50 text-amber-700 ring-amber-100',
  unpaid_leave: 'bg-amber-50 text-amber-700 ring-amber-100',
  sick_leave: 'bg-rose-50 text-rose-700 ring-rose-100',
  annual_leave: 'bg-sky-50 text-sky-700 ring-sky-100',
  dismissal: 'bg-slate-100 text-slate-700 ring-slate-200',
  fired: 'bg-slate-100 text-slate-700 ring-slate-200',
};

const OPERATOR_DETAILS_PAGE_SIZE = 100;

const operatorStatusEntries = (statusDays = {}) =>
  Object.entries(statusDays || {})
    .map(([status, days]) => ({ status, days: Number(days || 0) }))
    .filter((item) => item.days > 0)
    .sort((a, b) => {
      if (a.status === 'working') return -1;
      if (b.status === 'working') return 1;
      return b.days - a.days;
    });

const OperatorStatusChips = ({ statusDays }) => {
  const entries = operatorStatusEntries(statusDays);
  if (!entries.length) return <span className="text-slate-400">-</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map((item) => (
        <span
          key={item.status}
          className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold ring-1 ${OPERATOR_STATUS_CHIP_CLASSES[item.status] || 'bg-slate-100 text-slate-700 ring-slate-200'}`}
        >
          {OPERATOR_STATUS_LABELS[item.status] || item.status}: {formatInt(item.days)}
        </span>
      ))}
    </div>
  );
};

const OperatorSummaryCard = ({
  requiredFte,
  requiredWithUplift,
  baseFte,
  availableFte,
  currentFte,
  gap,
  availableCount,
  totalCount,
  partialCount,
  unavailableCount,
  onOpen,
}) => {
  const requiredNumber = Number(requiredFte || 0);
  const requiredWithUpliftNumber = Number(requiredWithUplift ?? requiredFte ?? 0);
  const availableNumber = Number(availableFte || 0);
  const hasUpliftRequirement = Math.abs(requiredWithUpliftNumber - requiredNumber) > 0.005;
  const effectiveGap = hasUpliftRequirement
    ? availableNumber - requiredWithUpliftNumber
    : Number(gap ?? availableNumber - requiredNumber);
  const isDeficit = effectiveGap < -0.005;
  const isBalanced = Math.abs(effectiveGap) <= 0.005;
  const effectiveRequired = hasUpliftRequirement ? requiredWithUpliftNumber : requiredNumber;
  const coverage = effectiveRequired > 0
    ? Math.min(150, (availableNumber / effectiveRequired) * 100)
    : 100;
  const coverageBarWidth = Math.min(100, coverage);
  const statusLabel = isBalanced ? 'В балансе' : isDeficit ? 'Дефицит' : 'Профицит';
  const statusTone = isBalanced
    ? 'bg-slate-100 text-slate-700 ring-slate-200'
    : isDeficit
      ? 'bg-rose-50 text-rose-700 ring-rose-200'
      : 'bg-emerald-50 text-emerald-700 ring-emerald-200';
  const gapTextClass = isBalanced ? 'text-slate-600' : isDeficit ? 'text-rose-700' : 'text-emerald-700';
  const gapDisplay = isBalanced
    ? '±0.00'
    : effectiveGap > 0 ? `+${formatNumber(effectiveGap, 2)}` : formatNumber(effectiveGap, 2);
  const barTone = isBalanced ? 'bg-slate-400' : isDeficit ? 'bg-rose-500' : 'bg-emerald-500';
  return (
    <div className={`relative overflow-hidden rounded-xl border bg-white p-4 shadow-sm xl:col-span-2 ${isDeficit ? 'border-rose-200' : isBalanced ? 'border-slate-200' : 'border-emerald-200'}`}>
      <span className={`pointer-events-none absolute left-0 top-0 h-full w-1 ${barTone}`} aria-hidden="true" />
      <div className="flex items-start justify-between gap-3 pl-1.5">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Операторы</p>
            <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${statusTone}`}>
              {isDeficit ? <AlertTriangle size={11} aria-hidden="true" /> : isBalanced ? <Minus size={11} aria-hidden="true" /> : <CheckCircle2 size={11} aria-hidden="true" />}
              {statusLabel}
            </span>
          </div>
          <div className="mt-1.5 flex items-baseline gap-2">
            <div className={`text-3xl font-semibold tabular-nums ${gapTextClass}`}>{gapDisplay}</div>
            <div className="text-xs uppercase tracking-wide text-slate-500">FTE</div>
          </div>
          <p className="mt-1 text-xs text-slate-500 tabular-nums">
            Доступно <b className="text-slate-900">{formatNumber(availableNumber, 2)}</b> / нужно <b className="text-slate-900">{formatNumber(effectiveRequired, 2)}</b>
            {hasUpliftRequirement ? <span className="text-emerald-700"> с приростом</span> : null}
          </p>
        </div>
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ring-1 ${isDeficit ? 'bg-rose-50 text-rose-700 ring-rose-100' : isBalanced ? 'bg-slate-100 text-slate-700 ring-slate-200' : 'bg-emerald-50 text-emerald-700 ring-emerald-100'}`}>
          <Users size={18} aria-hidden="true" />
        </div>
      </div>

      <div className="mt-3 pl-1.5">
        <div className="flex items-center justify-between text-[11px] text-slate-500">
          <span className="tabular-nums">{formatPercent(coverage / 100, 0)} покрытия</span>
          <span className="tabular-nums">{coverage > 100 ? `+${formatPercent((coverage - 100) / 100, 0)}` : ''}</span>
        </div>
        <div
          className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100"
          role="progressbar"
          aria-valuenow={Math.round(coverageBarWidth)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Покрытие потребности доступным FTE"
        >
          <div className={`h-full rounded-full transition-[width] duration-300 motion-reduce:transition-none ${barTone}`} style={{ width: `${coverageBarWidth}%` }} />
        </div>
      </div>

      {hasUpliftRequirement ? (
        <div className="mt-3 flex items-center justify-between gap-2 rounded-md bg-emerald-50 px-2 py-1.5 text-[11px] text-emerald-800 ring-1 ring-inset ring-emerald-100 ml-1.5">
          <span className="inline-flex items-center gap-1">
            <TrendingUp size={11} aria-hidden="true" />
            Без прироста хватило бы <b className="tabular-nums">{formatNumber(requiredNumber, 2)}</b> FTE
          </span>
          <span className="tabular-nums">+{formatNumber(requiredWithUpliftNumber - requiredNumber, 2)} к потребности</span>
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 pl-1.5 text-[11px] text-slate-500 tabular-nums">
        <span><b className="text-slate-700">{formatInt(availableCount)}</b> / {formatInt(totalCount)} сотр.</span>
        {Number(partialCount) > 0 ? <span>· <b className="text-slate-700">{formatInt(partialCount)}</b> частично</span> : null}
        {Number(unavailableCount) > 0 ? <span>· <b className="text-slate-700">{formatInt(unavailableCount)}</b> не работает</span> : null}
        <span className="text-slate-400">·</span>
        <span>Текущий FTE <b className="text-slate-700">{formatNumber(currentFte, 2)}</b></span>
        <span>· Без усушки <b className="text-slate-700">{formatNumber(baseFte, 2)}</b></span>
      </div>

      <button
        type="button"
        onClick={onOpen}
        className="mt-3 inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold text-blue-700 transition hover:bg-blue-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-300 ml-1.5"
      >
        <Eye size={14} aria-hidden="true" />
        Подробнее о расчёте
      </button>
    </div>
  );
};

const OperatorAvailabilityDetailsModal = ({ open, onClose, forecast, isLoading = false, error = '' }) => {
  const details = Array.isArray(forecast?.periodOperatorAvailabilityDetails)
    ? forecast.periodOperatorAvailabilityDetails
    : [];
  const [page, setPage] = useState(1);
  useEffect(() => {
    if (open) setPage(1);
  }, [details.length, forecast?.period_end, forecast?.period_start, open]);

  if (!open) return null;

  const rates = Array.isArray(forecast?.periodAvailableOperatorRates)
    ? forecast.periodAvailableOperatorRates
    : [];
  const statusSummary = forecast?.periodOperatorStatusSummary || {};
  const requiredFte = Number(forecast?.operatorsWithShrinkage || 0);
  const baseFte = Number(forecast?.baseOperators || 0);
  const availableFte = Number(forecast?.periodAvailableOperatorFte || 0);
  const gap = Number(forecast?.periodAvailableOperatorFteGap ?? (availableFte - requiredFte));
  const periodDays = Number(forecast?.periodDays || forecast?.period_day_count || details[0]?.totalDays || 0);
  const threshold = Number(forecast?.periodWorkingDaysThreshold || (periodDays ? periodDays / 2 : 0));
  const isDeficit = gap < 0;
  const statusEntries = operatorStatusEntries(statusSummary);
  const totalPages = Math.max(1, Math.ceil(details.length / OPERATOR_DETAILS_PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const visibleDetails = details.slice(
    (currentPage - 1) * OPERATOR_DETAILS_PAGE_SIZE,
    currentPage * OPERATOR_DETAILS_PAGE_SIZE,
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border-2 border-slate-200 bg-white shadow-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
          <div>
            <div className="flex items-center gap-2 text-base font-semibold text-slate-950">
              <Users size={19} className={isDeficit ? 'text-rose-600' : 'text-emerald-600'} />
              Детализация доступного FTE
            </div>
            <p className="mt-1 text-sm text-slate-500">
              {formatDate(forecast?.period_start || forecast?.week_start)} - {formatDate(forecast?.period_end || forecast?.week_end)} · ставка входит, если Working больше {formatNumber(threshold, 1)} из {formatInt(periodDays)} дн.
            </p>
            {isLoading ? (
              <div className="mt-2 inline-flex items-center gap-2 rounded-lg bg-blue-50 px-2 py-1 text-xs font-semibold text-blue-700">
                <RefreshCw size={13} className="animate-spin" />
                Загрузка детализации
              </div>
            ) : null}
            {error ? (
              <div className="mt-2 rounded-lg bg-rose-50 px-2 py-1 text-xs font-semibold text-rose-700">{error}</div>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border-2 border-slate-200 text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
          >
            <X size={16} />
          </button>
        </div>

        <div className="overflow-y-auto px-5 py-4">
          <div className="grid gap-3 md:grid-cols-4">
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Нужно с усушкой</div>
              <div className="mt-1 text-2xl font-semibold text-slate-950">{formatNumber(requiredFte, 2)}</div>
              <div className="mt-1 text-xs text-slate-500">Без усушки: {formatNumber(baseFte, 2)}</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Доступно</div>
              <div className={`mt-1 text-2xl font-semibold ${isDeficit ? 'text-rose-700' : 'text-emerald-700'}`}>{formatNumber(availableFte, 2)}</div>
              <div className="mt-1 text-xs text-slate-500">{formatInt(forecast?.periodAvailableOperatorCount)} сотрудников</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Разница</div>
              <div className={`mt-1 text-2xl font-semibold ${isDeficit ? 'text-rose-700' : 'text-emerald-700'}`}>{formatSignedNumber(gap, 2)}</div>
              <div className="mt-1 text-xs text-slate-500">Доступно - нужно</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Текущий FTE</div>
              <div className="mt-1 text-2xl font-semibold text-slate-950">{formatNumber(forecast?.currentOperatorFte, 2)}</div>
              <div className="mt-1 text-xs text-slate-500">Сумма на текущий момент</div>
            </div>
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
            <section className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-950">
                <ListChecks size={16} className="text-blue-600" />
                Разбивка по ставкам
              </div>
              <div className="space-y-2">
                {rates.map((item) => (
                  <div key={item.rate} className="grid grid-cols-[70px_1fr_auto] items-center gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm">
                    <div className="font-semibold text-slate-900">{formatNumber(item.rate, 2)}</div>
                    <div className="h-2 overflow-hidden rounded-full bg-slate-200">
                      <div
                        className="h-full rounded-full bg-emerald-500"
                        style={{ width: `${Math.min(100, (Number(item.count || 0) / Math.max(1, Number(item.total_count || item.count || 0))) * 100)}%` }}
                      />
                    </div>
                    <div className="text-right text-xs text-slate-600">
                      <b className="text-slate-950">{formatInt(item.count)}</b> / {formatInt(item.total_count ?? item.count)} чел.
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-950">
                <CalendarDays size={16} className="text-blue-600" />
                Дни по статусам
              </div>
              <OperatorStatusChips statusDays={statusSummary} />
              {statusEntries.length ? (
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {statusEntries.map((item) => (
                    <div key={item.status} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                      <span className="text-slate-600">{OPERATOR_STATUS_LABELS[item.status] || item.status}</span>
                      <b className="text-slate-950">{formatInt(item.days)} дн.</b>
                    </div>
                  ))}
                </div>
              ) : null}
            </section>
          </div>

          <section className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-white">
            <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
              <div className="text-sm font-semibold text-slate-950">Операторы в расчете</div>
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <span>{formatInt(details.length)} строк</span>
                {details.length > OPERATOR_DETAILS_PAGE_SIZE ? (
                  <span className="rounded-md bg-slate-100 px-2 py-1">
                    {formatInt((currentPage - 1) * OPERATOR_DETAILS_PAGE_SIZE + 1)}-{formatInt(Math.min(currentPage * OPERATOR_DETAILS_PAGE_SIZE, details.length))}
                  </span>
                ) : null}
              </div>
            </div>
            <div className="max-h-[420px] overflow-auto">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead className="sticky top-0 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-4 py-3 text-left font-semibold">Оператор</th>
                    <th className="px-4 py-3 text-right font-semibold">Ставка</th>
                    <th className="px-4 py-3 text-right font-semibold">Working</th>
                    <th className="px-4 py-3 text-left font-semibold">Статусы</th>
                    <th className="px-4 py-3 text-center font-semibold">Итог</th>
                    <th className="px-4 py-3 text-right font-semibold">Вклад</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {visibleDetails.map((operator) => (
                    <tr key={operator.operatorId} className={operator.included ? 'bg-white' : 'bg-slate-50/70'}>
                      <td className="px-4 py-3">
                        <div className="font-semibold text-slate-900">{operator.name || `ID ${operator.operatorId}`}</div>
                        <div className="text-xs text-slate-500">
                          {[operator.directionName, operator.supervisorName].filter(Boolean).join(' · ') || '-'}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right font-semibold text-slate-900">{formatNumber(operator.rate, 2)}</td>
                      <td className="px-4 py-3 text-right text-slate-700">
                        <b>{formatInt(operator.workingDays)}</b> / {formatInt(operator.totalDays)}
                      </td>
                      <td className="px-4 py-3">
                        <OperatorStatusChips statusDays={operator.statusDays} />
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className={`inline-flex rounded-md px-2 py-1 text-xs font-semibold ring-1 ${operator.included ? 'bg-emerald-50 text-emerald-700 ring-emerald-100' : 'bg-slate-100 text-slate-600 ring-slate-200'}`}>
                          {operator.included ? 'Засчитан' : 'Не засчитан'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right font-semibold text-slate-900">{formatNumber(operator.fteContribution, 2)}</td>
                    </tr>
                  ))}
                  {isLoading ? (
                    <tr>
                      <td className="px-4 py-8 text-center text-sm text-slate-500" colSpan={6}>
                        <span className="inline-flex items-center gap-2">
                          <RefreshCw size={15} className="animate-spin" />
                          Загрузка детализации...
                        </span>
                      </td>
                    </tr>
                  ) : null}
                  {!isLoading && !details.length ? (
                    <tr>
                      <td className="px-4 py-8 text-center text-sm text-slate-500" colSpan={6}>Нет данных по операторам.</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            {details.length > OPERATOR_DETAILS_PAGE_SIZE ? (
              <div className="flex items-center justify-between gap-3 border-t border-slate-200 px-4 py-3">
                <button
                  type="button"
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                  disabled={currentPage <= 1}
                  className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <ChevronLeft size={15} />
                  Назад
                </button>
                <div className="text-sm font-semibold text-slate-700">
                  {formatInt(currentPage)} / {formatInt(totalPages)}
                </div>
                <button
                  type="button"
                  onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                  disabled={currentPage >= totalPages}
                  className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Вперед
                  <ChevronRight size={15} />
                </button>
              </div>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
};

const EmptyState = ({ title, text, action }) => (
  <div className="flex min-h-[220px] flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center">
    <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-slate-100 text-slate-500">
      <BarChart3 size={22} aria-hidden="true" />
    </div>
    <h3 className="mt-4 text-base font-semibold text-slate-900">{title}</h3>
    <p className="mt-1 max-w-md text-sm text-slate-500">{text}</p>
    {action ? <div className="mt-4">{action}</div> : null}
  </div>
);

const ToggleSwitch = ({ checked, label, onChange }) => (
  <button
    type="button"
    onClick={() => onChange(!checked)}
    className="flex w-full items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-left text-sm transition hover:bg-slate-50"
  >
    <span className="font-medium text-slate-700">{label}</span>
    <span
      className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition ${
        checked ? 'bg-blue-600' : 'bg-slate-300'
      }`}
      aria-hidden="true"
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition ${
          checked ? 'left-4' : 'left-0.5'
        }`}
      />
    </span>
  </button>
);

const FORECAST_CHART_LEGEND_ITEMS = [
  { key: 'forecastChartCalls', label: 'Звонки', color: '#60a5fa', shape: 'bar' },
  { key: 'forecastChartUplift', label: 'Прирост звонков', color: '#34d399', shape: 'bar', requires: 'uplift' },
  { key: 'forecastChartWorkload', label: 'Минуты нагрузки', color: '#3b82f6', shape: 'line' },
  { key: 'forecastChartFte', label: 'Прогноз FTE', color: '#2563eb', shape: 'line' },
  { key: 'forecastChartAdjustedFte', label: 'FTE с приростом', color: '#059669', shape: 'dashed', requires: 'uplift' },
  { key: 'forecastChartActualWorkload', label: 'Факт нагрузки', color: '#10b981', shape: 'line', requires: 'actual' },
  { key: 'forecastChartActualFte', label: 'Факт FTE', color: '#059669', shape: 'dashed', requires: 'actual' },
];

const ForecastChartLegend = ({ displayOptions, toggleDisplayOption, incidentUpliftAvailable, showActualLoad }) => {
  const items = FORECAST_CHART_LEGEND_ITEMS.filter((item) => {
    if (item.requires === 'uplift') return incidentUpliftAvailable;
    if (item.requires === 'actual') return showActualLoad;
    return true;
  });

  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5">
      {items.map((item) => {
        const active = Boolean(displayOptions[item.key]);
        return (
          <button
            key={item.key}
            type="button"
            onClick={() => toggleDisplayOption(item.key, !active)}
            title={active ? 'Скрыть серию на графике' : 'Показать серию на графике'}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium transition ${
              active ? 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50' : 'border-slate-200 bg-slate-50 text-slate-400 line-through hover:bg-slate-100'
            }`}
          >
            <span
              className={`inline-block h-2.5 ${item.shape === 'bar' ? 'w-2.5 rounded-sm' : 'w-4 rounded-full'}`}
              style={{
                background: item.shape === 'dashed'
                  ? `repeating-linear-gradient(90deg, ${item.color} 0 4px, transparent 4px 7px)`
                  : item.shape === 'bar' ? item.color : 'transparent',
                borderTop: item.shape === 'line' ? `2px solid ${item.color}` : undefined,
                opacity: active ? 1 : 0.35,
              }}
            />
            {item.label}
          </button>
        );
      })}
    </div>
  );
};

const FORECAST_PANEL_GROUPS = [
  {
    title: 'KPI периода',
    items: [
      ['forecastKpiFteHours', 'FTE-часы периода'],
      ['forecastKpiOperators', 'Операторы'],
      ['forecastKpiUplift', 'Возможный прирост'],
      ['forecastKpiAht', 'AHT периода'],
      ['forecastKpiAnswerRate', 'Принято'],
      ['forecastKpiOccUr', 'OCC / UR'],
      ['forecastKpiShrinkage', 'Усушка'],
    ],
  },
  {
    title: 'Серии графика',
    items: [
      ['forecastChartCalls', 'Звонки (бар)'],
      ['forecastChartUplift', 'Прирост звонков', 'uplift'],
      ['forecastChartWorkload', 'Минуты нагрузки'],
      ['forecastChartFte', 'Прогноз FTE'],
      ['forecastChartAdjustedFte', 'FTE с приростом', 'uplift'],
      ['forecastChartActualWorkload', 'Факт нагрузки', 'actual'],
      ['forecastChartActualFte', 'Факт FTE', 'actual'],
    ],
  },
  {
    title: 'Колонки таблицы',
    items: [
      ['forecastTableAht', 'AHT дня'],
      ['forecastTableWorkload', 'Минут нагрузки'],
      ['forecastTableUplift', 'Прирост', 'uplift'],
      ['forecastTableAdjustedFte', 'FTE с приростом', 'uplift'],
      ['forecastTableActualCalls', 'Факт звонков', 'actual'],
      ['forecastTableActualWorkload', 'Факт нагрузки', 'actual'],
      ['forecastTableActualFte', 'Факт FTE', 'actual'],
    ],
  },
  {
    title: 'Дополнительно',
    items: [
      ['forecastShowActualLoad', 'Сравнивать с фактом'],
      ['forecastShowActualPeakHours', 'Пиковые часы факт', 'actual'],
    ],
  },
];

const ForecastDisplayPanel = ({
  isOpen,
  onToggleOpen,
  displayOptions,
  toggleDisplayOption,
  incidentUpliftAvailable,
  showActualLoad,
  forecastActualLoadAvailable,
}) => {
  const hiddenCount = FORECAST_PANEL_GROUPS.reduce((acc, group) => (
    acc + group.items.filter(([key, , requires]) => {
      if (requires === 'uplift' && !incidentUpliftAvailable) return false;
      if (requires === 'actual' && !showActualLoad) return false;
      return !displayOptions[key];
    }).length
  ), 0);

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-40 flex max-w-[calc(100vw-2rem)] flex-col items-end gap-2">
      {isOpen ? (
        <div className="pointer-events-auto w-[340px] max-w-full rounded-xl border border-slate-200 bg-white shadow-xl">
          <div className="flex items-center justify-between gap-2 border-b border-slate-200 px-3 py-2">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <SlidersHorizontal size={16} />
              Отображение прогнозов
            </div>
            <button
              type="button"
              onClick={onToggleOpen}
              className="rounded-md p-1 text-slate-500 hover:bg-slate-100"
              aria-label="Свернуть"
            >
              <ChevronDown size={16} />
            </button>
          </div>
          <div className="max-h-[60vh] space-y-3 overflow-y-auto p-3">
            {FORECAST_PANEL_GROUPS.map((group) => {
              const visibleItems = group.items.filter(([, , requires]) => {
                if (requires === 'uplift' && !incidentUpliftAvailable) return false;
                if (requires === 'actual' && !showActualLoad) return false;
                return true;
              });
              if (!visibleItems.length) return null;
              return (
                <div key={group.title}>
                  <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{group.title}</div>
                  <div className="space-y-1.5">
                    {visibleItems.map(([key, label, requires]) => {
                      const disabled = key === 'forecastShowActualLoad' && !forecastActualLoadAvailable && !displayOptions[key];
                      const checked = !!displayOptions[key];
                      return (
                        <button
                          key={key}
                          type="button"
                          role="switch"
                          aria-checked={checked}
                          aria-label={label}
                          onClick={() => !disabled && toggleDisplayOption(key, !checked)}
                          disabled={disabled}
                          title={disabled ? 'Для выбранного периода нет прошедших дней с загруженным отчетом' : undefined}
                          className={`flex min-h-9 w-full items-center justify-between gap-3 rounded-md border border-slate-200 bg-white px-2.5 py-2 text-left text-xs transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 ${
                            disabled ? 'cursor-not-allowed opacity-50' : 'hover:bg-slate-50'
                          }`}
                        >
                          <span className="font-medium text-slate-700">{label}</span>
                          <span
                            className={`relative inline-flex h-4 w-7 shrink-0 rounded-full transition ${
                              displayOptions[key] ? 'bg-blue-600' : 'bg-slate-300'
                            }`}
                            aria-hidden="true"
                          >
                            <span
                              className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow-sm transition ${
                                displayOptions[key] ? 'left-3.5' : 'left-0.5'
                              }`}
                            />
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
      <button
        type="button"
        onClick={onToggleOpen}
        className="pointer-events-auto inline-flex h-10 items-center gap-2 rounded-full border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 shadow-lg transition hover:bg-slate-50"
      >
        <SlidersHorizontal size={16} />
        Отображение
        {hiddenCount > 0 ? (
          <span className="inline-flex h-5 min-w-[20px] items-center justify-center rounded-full bg-slate-900 px-1.5 text-[11px] font-semibold text-white">{hiddenCount}</span>
        ) : null}
        {isOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
      </button>
    </div>
  );
};

const OverviewTrendTooltip = ({ active, label, payload }) => {
  if (!active || !payload?.length) return null;

  const groups = payload.reduce((acc, entry) => {
    const key = entry.dataKey || entry.name;
    const config = OVERVIEW_TREND_TOOLTIP_CONFIG[key];
    if (!config) return acc;
    if (!acc[config.group]) {
      acc[config.group] = {
        order: config.groupOrder,
        items: [],
      };
    }
    acc[config.group].items.push({
      ...config,
      value: entry.value,
      color: entry.color || entry.stroke || entry.fill || '#64748b',
    });
    return acc;
  }, {});

  const orderedGroups = Object.entries(groups)
    .map(([group, groupData]) => ({
      group,
      ...groupData,
      items: groupData.items.sort((a, b) => a.itemOrder - b.itemOrder),
    }))
    .sort((a, b) => a.order - b.order);

  return (
    <div className="min-w-56 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
      <div className="mb-2 font-semibold text-slate-900">{label}</div>
      <div className="space-y-2">
        {orderedGroups.map(({ group, items }) => (
          <div key={group} className="rounded-md bg-slate-50 px-2 py-1.5">
            <div className="mb-1 font-medium text-slate-500">{group}</div>
            <div className="space-y-1">
              {items.map((item) => (
                <div key={`${group}-${item.label}`} className="flex items-center justify-between gap-6">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full" style={{ backgroundColor: item.color }} />
                    {item.label}
                  </span>
                  <b className="text-slate-900">
                    {item.percent ? `${formatNumber(item.value, 1)}%` : formatNumber(item.value, item.digits)}
                  </b>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

const IncidentRiskTooltip = ({ active, label, payload }) => {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload || {};
  return (
    <div className="min-w-60 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
      <div className="mb-2 font-semibold text-slate-900">{label}</div>
      <div className="space-y-1.5">
        <div className="flex justify-between gap-6"><span className="text-slate-500">Прогноз</span><b className="text-blue-700">{formatNumber(row.forecastCalls, 0)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-slate-500">Факт</span><b className="text-slate-900">{formatNumber(row.actualCalls, 0)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-slate-500">Факт - прогноз</span><b className={Number(row.deltaCalls || 0) > 0 ? 'text-rose-700' : 'text-emerald-700'}>{formatSignedNumber(row.deltaCalls, 0)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-slate-500">Превышение по часам</span><b className="text-rose-700">+{formatNumber(row.positiveDeltaCalls, 0)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-slate-500">Часы риска</span><b className="text-slate-900">{formatInt(row.positiveHourCount)} / {formatInt(row.sourceHourCount)}</b></div>
      </div>
    </div>
  );
};

const IncidentProjectionTooltip = ({ active, label, payload }) => {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload || {};
  return (
    <div className="min-w-60 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
      <div className="mb-2 font-semibold text-slate-900">{label}</div>
      <div className="space-y-1.5">
        <div className="flex justify-between gap-6"><span className="text-slate-500">Прогноз звонков</span><b className="text-blue-700">{formatNumber(row.forecastCalls, 0)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-slate-500">Возможный прирост</span><b className="text-emerald-700">+{formatNumber(row.upliftCalls, 0)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-slate-500">С учетом прироста</span><b className="text-slate-900">{formatNumber(row.adjustedCalls, 0)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-slate-500">Доп. FTE дня</span><b className="text-emerald-700">+{formatNumber(row.upliftFte, 2)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-slate-500">Вес дня</span><b className="text-slate-900">{formatPercent(row.futureWeight, 0)}</b></div>
      </div>
    </div>
  );
};

const CallsTrendTooltip = ({ active, label, payload, mode }) => {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload || {};
  const forecastCalls = Number(row.forecastCalls || 0);
  const factCalls = Number(row.calls || 0);
  const delta = factCalls - forecastCalls;
  const completion = forecastCalls > 0 ? factCalls / forecastCalls : 0;
  const matchPercent = Number(row.forecastMatchPercent || 0);

  if (mode === 'forecastFact') {
    return (
      <div className="min-w-60 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
        <div className="mb-2 font-semibold text-slate-900">{label}</div>
        <div className="space-y-2">
          <div className="rounded-md bg-slate-50 px-2 py-1.5">
            <div className="mb-1 font-medium text-slate-500">Количество звонков</div>
            <div className="flex items-center justify-between gap-6">
              <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-blue-400" />Прогноз</span>
              <b className="text-blue-700">{formatNumber(forecastCalls, 0)}</b>
            </div>
            <div className="mt-1 flex items-center justify-between gap-6">
              <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-emerald-500" />Факт</span>
              <b className="text-emerald-700">{formatInt(factCalls)}</b>
            </div>
          </div>
          <div className="rounded-md bg-slate-50 px-2 py-1.5">
            <div className="flex items-center justify-between gap-6">
              <span className="text-slate-500">Разница факт - прогноз</span>
              <b className={delta < 0 ? 'text-rose-700' : delta > 0 ? 'text-emerald-700' : 'text-slate-900'}>{formatSignedNumber(delta, 0)}</b>
            </div>
            <div className="mt-1 flex items-center justify-between gap-6">
              <span className="text-slate-500">Выполнение</span>
              <b className="text-slate-900">{forecastCalls > 0 ? formatPercent(completion, 0) : '-'}</b>
            </div>
            <div className="mt-1 flex items-center justify-between gap-6">
              <span className="text-slate-500">Совпадение прогноза</span>
              <b className="text-violet-700">{forecastCalls > 0 ? `${formatNumber(matchPercent, 1)}%` : '-'}</b>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-w-56 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
      <div className="mb-2 font-semibold text-slate-900">{label}</div>
      <div className="space-y-1.5">
        <div className="flex justify-between gap-6"><span className="text-slate-500">Принято</span><b className="text-emerald-700">{formatInt(row.accepted)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-slate-500">Потеряно</span><b className="text-rose-700">{formatInt(row.lost)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-slate-500">Доля потерь</span><b className="text-rose-700">{formatNumber(row.lossRate, 1)}%</b></div>
      </div>
    </div>
  );
};

const DayCallsTooltip = ({ active, label, payload }) => {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload || {};
  const forecastCalls = Number(row.forecastCalls || 0);
  const factCalls = Number(row.factCalls || 0);
  const delta = factCalls - forecastCalls;
  const matchPercent = Number(row.matchPercent || 0);
  return (
    <div className="min-w-56 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
      <div className="mb-2 font-semibold text-slate-900">{label}</div>
      <div className="space-y-1.5">
        <div className="flex justify-between gap-6"><span className="text-blue-700">Прогноз</span><b>{formatNumber(forecastCalls, 0)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-emerald-700">Факт</span><b>{formatInt(factCalls)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-slate-500">Разница</span><b className={delta < 0 ? 'text-rose-700' : delta > 0 ? 'text-emerald-700' : 'text-slate-900'}>{formatSignedNumber(delta, 0)}</b></div>
        <div className="flex justify-between gap-6"><span className="text-slate-500">Совпадение</span><b className="text-violet-700">{forecastCalls > 0 ? `${formatNumber(matchPercent, 1)}%` : '-'}</b></div>
      </div>
    </div>
  );
};

const CalendarPicker = ({
  label,
  value,
  startValue,
  endValue,
  onChange,
  onRangeChange,
  loadedDates = [],
  mode = 'single',
  hint,
}) => {
  const [open, setOpen] = useState(false);
  const [draftStart, setDraftStart] = useState('');
  const anchorRef = useRef(null);
  const loadedSet = useMemo(() => new Set(loadedDates), [loadedDates]);
  const initialDate = parseIsoDate(value || startValue || endValue) || new Date();
  const [visibleMonth, setVisibleMonth] = useState(new Date(initialDate.getFullYear(), initialDate.getMonth(), 1));

  useEffect(() => {
    const next = parseIsoDate(value || startValue || endValue);
    if (next) setVisibleMonth(new Date(next.getFullYear(), next.getMonth(), 1));
  }, [endValue, startValue, value]);

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event) => {
      if (anchorRef.current && !anchorRef.current.contains(event.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [open]);

  const calendarDays = useMemo(() => buildCalendarDays(visibleMonth), [visibleMonth]);
  const displayStart = draftStart || startValue;
  const displayEnd = draftStart ? '' : endValue;
  const periodLength = mode === 'range' ? daysBetweenInclusive(displayStart, displayEnd) : 0;
  const selectedText = mode === 'range'
    ? `${formatDate(startValue)} — ${formatDate(endValue)}`
    : formatDate(value);

  const moveMonth = (delta) => {
    setVisibleMonth((current) => new Date(current.getFullYear(), current.getMonth() + delta, 1));
  };

  const selectDay = (iso) => {
    if (mode === 'range') {
      if (!draftStart) {
        setDraftStart(iso);
      } else if (iso < draftStart) {
        setDraftStart(iso);
      } else {
        onRangeChange?.(draftStart, iso);
        setDraftStart('');
        setOpen(false);
      }
      return;
    }
    onChange?.(iso);
    setOpen(false);
  };

  const setLastTwoWeeks = () => {
    const end = todayIso();
    onRangeChange?.(addDaysIso(end, -13), end);
    setDraftStart('');
    setOpen(false);
  };

  return (
    <div ref={anchorRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex h-14 w-full items-center justify-between gap-3 rounded-xl border-2 border-slate-200 bg-white px-4 text-left text-sm shadow-sm transition hover:border-slate-300 hover:bg-slate-50"
      >
        <span className="min-w-0">
          <span className="block text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</span>
          <span className="block truncate font-semibold text-slate-900">{selectedText}</span>
        </span>
        <CalendarDays size={17} className="shrink-0 text-blue-600" />
      </button>

      {open && (
        <div className="absolute right-0 z-40 mt-2 w-[330px] rounded-2xl border-2 border-slate-200 bg-white p-4 shadow-xl">
          <div className="flex items-center justify-between gap-2">
            <button type="button" onClick={() => moveMonth(-1)} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100">
              <ChevronLeft size={16} />
            </button>
            <div className="text-sm font-semibold capitalize text-slate-950">{monthLabel(visibleMonth)}</div>
            <button type="button" onClick={() => moveMonth(1)} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100">
              <ChevronRight size={16} />
            </button>
          </div>

          {mode === 'range' && (
            <div className="mt-3 flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
              <span>{periodLength > 0 ? `${periodLength} дней в периоде` : 'Выберите начало периода'}</span>
              <button type="button" onClick={setLastTwoWeeks} className="font-semibold text-blue-700 hover:text-blue-800">
                Последние 14 дней
              </button>
            </div>
          )}

          <div className="mt-3 grid grid-cols-7 gap-1 text-center text-[11px] font-semibold uppercase text-slate-400">
            {['ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС'].map((day) => (
              <div key={day} className="py-1">{day}</div>
            ))}
          </div>

          <div className="mt-1 grid grid-cols-7 gap-1">
            {calendarDays.map((date) => {
              const iso = toIsoDate(date);
              const isOutside = date.getMonth() !== visibleMonth.getMonth();
              const isSelected = mode === 'single' ? iso === value : iso === displayStart || iso === displayEnd;
              const inRange = mode === 'range' && isIsoInRange(iso, displayStart, displayEnd);
              const hasUpload = loadedSet.has(iso);
              return (
                <button
                  key={iso}
                  type="button"
                  onClick={() => selectDay(iso)}
                  className={`relative flex h-9 items-center justify-center rounded-lg text-sm font-medium transition ${
                    isSelected
                      ? 'bg-slate-900 text-white shadow-sm'
                      : inRange
                        ? 'bg-blue-50 text-blue-800'
                        : isOutside
                          ? 'text-slate-300 hover:bg-slate-50'
                          : 'text-slate-700 hover:bg-slate-100'
                  }`}
                >
                  {date.getDate()}
                  {hasUpload && (
                    <span className={`absolute bottom-1 h-1.5 w-1.5 rounded-full ${isSelected ? 'bg-white' : 'bg-emerald-500'}`} />
                  )}
                </button>
              );
            })}
          </div>

          <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3 text-xs text-slate-500">
            <span className="inline-flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> отчет загружен</span>
            {hint ? <span>{hint}</span> : null}
          </div>
        </div>
      )}
    </div>
  );
};

const WeekForecastPicker = ({
  value,
  startValue,
  endValue,
  onChange,
  onRangeChange,
  loadedDates = [],
  compact = false,
}) => {
  const [open, setOpen] = useState(false);
  const [draftStart, setDraftStart] = useState('');
  const anchorRef = useRef(null);
  const loadedSet = useMemo(() => new Set(loadedDates), [loadedDates]);
  const selectedPeriodStart = startValue || getWeekStartIso(value || getNextWeekStartIso());
  const selectedPeriodEnd = endValue || addDaysIso(selectedPeriodStart, 6);
  const displayStart = draftStart || selectedPeriodStart;
  const displayEnd = draftStart ? '' : selectedPeriodEnd;
  const periodLength = daysBetweenInclusive(displayStart, displayEnd);
  const selectedPeriodComplete = isForecastPeriodHistoryComplete(selectedPeriodStart, selectedPeriodEnd, loadedSet);
  const initialDate = parseIsoDate(selectedPeriodStart) || new Date();
  const [visibleMonth, setVisibleMonth] = useState(new Date(initialDate.getFullYear(), initialDate.getMonth(), 1));
  const historyPeriods = getForecastHistoryPeriods(selectedPeriodStart, selectedPeriodEnd);

  useEffect(() => {
    const next = parseIsoDate(selectedPeriodStart);
    if (next) setVisibleMonth(new Date(next.getFullYear(), next.getMonth(), 1));
  }, [selectedPeriodStart]);

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event) => {
      if (anchorRef.current && !anchorRef.current.contains(event.target)) setOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        setOpen(false);
        setDraftStart('');
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  const moveMonth = (delta) => {
    setVisibleMonth((current) => new Date(current.getFullYear(), current.getMonth() + delta, 1));
  };

  const calendarDays = useMemo(() => buildCalendarDays(visibleMonth), [visibleMonth]);

  const selectDay = (iso) => {
    if (!draftStart) {
      setDraftStart(iso);
      return;
    }
    const start = iso < draftStart ? iso : draftStart;
    const end = iso < draftStart ? draftStart : iso;
    onRangeChange?.(start, end);
    onChange?.(start);
    setDraftStart('');
    setOpen(false);
  };

  const selectWeek = () => {
    const weekStart = getWeekStartIso(selectedPeriodStart);
    const weekEnd = addDaysIso(weekStart, 6);
    onRangeChange?.(weekStart, weekEnd);
    onChange?.(weekStart);
    setDraftStart('');
    setOpen(false);
  };

  return (
    <div ref={anchorRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className={`flex w-full items-center justify-between gap-3 rounded-xl border-2 border-blue-300 bg-white text-left text-sm shadow-sm transition hover:border-blue-400 hover:bg-slate-50 ${
          compact ? 'h-10 px-3 py-2' : 'min-h-16 px-4 py-3'
        }`}
      >
        <span className="min-w-0">
          {!compact ? <span className="block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Период прогноза</span> : null}
          <span className={`block truncate font-semibold text-slate-900 ${compact ? 'text-sm' : ''}`}>
            {formatDate(selectedPeriodStart)} - {formatDate(selectedPeriodEnd)}
          </span>
          {!compact ? <span className={`mt-1 inline-flex rounded-md px-2 py-0.5 text-[11px] font-semibold ${
            selectedPeriodComplete ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
          }`}>
            {selectedPeriodComplete ? 'истории хватает' : 'истории не хватает'}
          </span> : null}
        </span>
        <CalendarDays size={17} className="shrink-0 text-blue-600" />
      </button>

      {!compact ? <div className="mt-2 rounded-xl border border-slate-200 bg-slate-100 px-3 py-2 text-xs text-slate-600">
        <div className="font-semibold text-slate-700">История для расчета</div>
        <div className="mt-1 grid gap-1">
          {historyPeriods.map((period, index) => (
            <div key={`${period.start}-${period.end}`}>
              {index + 1}. {formatDate(period.start)} - {formatDate(period.end)}
            </div>
          ))}
        </div>
      </div> : null}

      {open && (
        <div className="absolute left-0 z-40 mt-2 w-[330px] rounded-2xl border-2 border-slate-200 bg-white p-4 shadow-xl">
          <div className="flex items-center justify-between gap-2">
            <button type="button" onClick={() => moveMonth(-1)} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100">
              <ChevronLeft size={16} />
            </button>
            <div className="text-sm font-semibold capitalize text-slate-950">{monthLabel(visibleMonth)}</div>
            <button type="button" onClick={() => moveMonth(1)} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100">
              <ChevronRight size={16} />
            </button>
          </div>

          <div className="mt-3 flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
            <span>{periodLength > 0 ? `${periodLength} дней в периоде` : 'Выберите конец периода'}</span>
            <button type="button" onClick={selectWeek} className="font-semibold text-blue-700 hover:text-blue-800">
              Неделя
            </button>
          </div>

          <div className="mt-3 grid grid-cols-7 gap-1 text-center text-[11px] font-semibold uppercase text-slate-500">
            {['ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС'].map((day) => (
              <div key={day} className="py-1">{day}</div>
            ))}
          </div>

          <div className="mt-1 grid grid-cols-7 gap-1">
            {calendarDays.map((date) => {
              const iso = toIsoDate(date);
              const dayComplete = isForecastDayHistoryComplete(iso, loadedSet);
              const isOutside = date.getMonth() !== visibleMonth.getMonth();
              const isSelected = iso === displayStart || iso === displayEnd;
              const inRange = isIsoInRange(iso, displayStart, displayEnd);
              return (
                <button
                  key={iso}
                  type="button"
                  title={compact ? formatDate(iso) : `${formatDate(iso)}: ${dayComplete ? 'истории хватает' : 'истории не хватает'}`}
                  onClick={() => selectDay(iso)}
                  aria-pressed={isSelected}
                  className={`relative flex h-11 items-center justify-center rounded-lg border text-sm font-semibold transition tabular-nums ${
                    isSelected
                      ? 'border-blue-500 bg-white text-blue-800 ring-1 ring-blue-500'
                      : inRange
                        ? 'border-blue-100 bg-blue-50 text-blue-800'
                      : dayComplete
                        ? 'border-emerald-100 bg-emerald-50 text-emerald-700 hover:border-emerald-300'
                        : isOutside
                          ? 'border-transparent text-slate-400 hover:bg-slate-50'
                          : 'border-transparent text-slate-700 hover:bg-slate-100'
                  }`}
                >
                  {date.getDate()}
                  {dayComplete && (
                    <span className={`absolute top-1 h-1.5 w-1.5 rounded-full ${isSelected ? 'bg-emerald-600' : 'bg-emerald-500'}`} />
                  )}
                </button>
              );
            })}
          </div>

          {!compact ? <div className="mt-3 rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-600">
            Зеленый день означает, что для него загружены оба исторических дня: минус 21 и минус 14 дней.
          </div> : null}
        </div>
      )}
    </div>
  );
};

const ResourceFteView = ({ apiBaseUrl, withAccessTokenHeader, user, showToast, initialDashboardView, onOpenShiftAuction }) => {
  const apiRoot = String(apiBaseUrl || '').replace(/\/+$/, '');
  const fileInputRef = useRef(null);
  const showToastRef = useRef(showToast);
  const authHeaderRef = useRef(withAccessTokenHeader);
  const [overview, setOverview] = useState(null);
  const [selectedDate, setSelectedDate] = useState('');
  const [selectedDay, setSelectedDay] = useState(null);
  const [isDayLoading, setIsDayLoading] = useState(false);
  const [dateFrom, setDateFrom] = useState(monthStartIso);
  const [dateTo, setDateTo] = useState(todayIso);
  const [uploadFile, setUploadFile] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isRecalculating, setIsRecalculating] = useState(false);
  const [settingsDraft, setSettingsDraft] = useState(null);
  const [activeDashboardView, setActiveDashboardView] = useState(initialDashboardView || 'overview');
  const [displayOptions, setDisplayOptions] = useState(loadDisplayOptions);
  const [selectedForecastWeekStart, setSelectedForecastWeekStart] = useState(() => getNextWeekStartIso());
  const [selectedForecastPeriodEnd, setSelectedForecastPeriodEnd] = useState(() => addDaysIso(getNextWeekStartIso(), 6));
  const [selectedForecastDate, setSelectedForecastDate] = useState('');
  const [isForecastPanelOpen, setIsForecastPanelOpen] = useState(false);
  const showForecastActualLoad = Boolean(displayOptions.forecastShowActualLoad);
  const [hoveredForecastHour, setHoveredForecastHour] = useState(null);
  const [pinnedForecastHour, setPinnedForecastHour] = useState(null);
  const [callsChartMode, setCallsChartMode] = useState('losses');
  const [loadedDateCache, setLoadedDateCache] = useState([]);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [isOperatorDetailsOpen, setIsOperatorDetailsOpen] = useState(false);
  const [operatorAvailabilityDetailsByKey, setOperatorAvailabilityDetailsByKey] = useState({});
  const [isOperatorDetailsLoading, setIsOperatorDetailsLoading] = useState(false);
  const [operatorDetailsError, setOperatorDetailsError] = useState('');
  const userId = user?.id || '';

  useEffect(() => {
    showToastRef.current = showToast;
  }, [showToast]);

  useEffect(() => {
    authHeaderRef.current = withAccessTokenHeader;
  }, [withAccessTokenHeader]);

  useEffect(() => {
    if (initialDashboardView) setActiveDashboardView(initialDashboardView);
  }, [initialDashboardView]);

  const notify = useCallback((message, type = 'success') => {
    if (typeof showToastRef.current === 'function') showToastRef.current(message, type);
  }, []);

  const buildHeaders = useCallback(
    (extra = {}) => apiHeaders(authHeaderRef.current, { ...extra, 'X-User-Id': String(userId) }),
    [userId],
  );

  const fetchOverview = useCallback(async () => {
    if (!apiRoot) return;
    setIsLoading(true);
    try {
      const response = await axios.get(`${apiRoot}/api/resource_fte/overview`, {
        params: {
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
          forecast_date_from: selectedForecastWeekStart || undefined,
          forecast_date_to: selectedForecastPeriodEnd || undefined,
        },
        headers: buildHeaders(),
      });
      const payload = response.data || {};
      setOverview(payload);
      setOperatorAvailabilityDetailsByKey({});
      setSettingsDraft(payload.settings || null);
      setLoadedDateCache((current) => {
        const next = new Set(current);
        (payload.loaded_report_dates || []).forEach((reportDate) => {
          if (reportDate) next.add(reportDate);
        });
        (payload.history || []).forEach((item) => {
          if (item?.report_date) next.add(item.report_date);
        });
        return Array.from(next).sort();
      });
      const firstDate = payload.history?.[0]?.report_date || '';
      setSelectedDate((current) => current || firstDate);
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось загрузить расчет ресурсов', 'error');
    } finally {
      setIsLoading(false);
    }
  }, [apiRoot, buildHeaders, dateFrom, dateTo, notify, selectedForecastPeriodEnd, selectedForecastWeekStart]);

  const fetchDay = useCallback(
    async (date) => {
      if (!apiRoot || !date) {
        setSelectedDay(null);
        setIsDayLoading(false);
        return;
      }
      setIsDayLoading(true);
      try {
        const response = await axios.get(`${apiRoot}/api/resource_fte/day/${date}`, {
          headers: buildHeaders(),
        });
        setSelectedDay(response.data?.day || null);
      } catch (error) {
        setSelectedDay(null);
        notify(error?.response?.data?.error || 'Не удалось открыть день', 'error');
      } finally {
        setIsDayLoading(false);
      }
    },
    [apiRoot, buildHeaders, notify],
  );

  useEffect(() => {
    fetchOverview();
  }, [fetchOverview]);

  useEffect(() => {
    fetchDay(selectedDate);
  }, [fetchDay, selectedDate]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(DISPLAY_PREFERENCES_STORAGE_KEY, JSON.stringify(displayOptions));
  }, [displayOptions]);

  const selectedDayMatchesDate = Boolean(selectedDate && selectedDay?.summary?.report_date === selectedDate);
  const selectedSummary = selectedDayMatchesDate ? selectedDay?.summary : null;

  const historyTrendData = useMemo(
    () =>
      (overview?.history || [])
        .slice(0, 21)
        .reverse()
        .map((item) => {
          const calls = Number(item.total_received || 0);
          const forecastCalls = Number(item.forecast_calls_total || 0);
          return {
            reportDate: item.report_date,
            date: formatDate(item.report_date).slice(0, 5),
            calls,
            accepted: Number(item.total_accepted || 0),
            lost: Number(item.total_lost || 0),
            lossRate: Number(item.no_answer_rate || 0) * 100,
            forecastCalls,
            forecastMatchPercent: calculateForecastMatchPercent(calls, forecastCalls),
            forecastFte: Number(item.forecast_fte_total || 0),
            actualFte: Number(item.actual_report_fte_total || 0),
          };
        }),
    [overview?.history],
  );

  const periodLossSummary = useMemo(() => {
    const rows = overview?.history || [];
    const totalReceived = rows.reduce((sum, row) => sum + Number(row.total_received || 0), 0);
    const totalAccepted = rows.reduce((sum, row) => sum + Number(row.total_accepted || 0), 0);
    const totalLost = rows.reduce((sum, row) => sum + Number(row.total_lost || 0), 0);
    const totalForecastCalls = rows.reduce((sum, row) => sum + Number(row.forecast_calls_total || 0), 0);
    const worstDay = rows.reduce((worst, row) => {
      if (!worst) return row;
      return Number(row.no_answer_rate || 0) > Number(worst.no_answer_rate || 0) ? row : worst;
    }, null);
    return {
      totalReceived,
      totalAccepted,
      totalLost,
      totalForecastCalls,
      callsDelta: totalReceived - totalForecastCalls,
      callsCompletion: totalForecastCalls > 0 ? totalReceived / totalForecastCalls : 0,
      callsMatchPercent: calculateForecastMatchPercent(totalReceived, totalForecastCalls),
      lossRate: totalReceived > 0 ? totalLost / totalReceived : 0,
      worstDay,
    };
  }, [overview?.history]);

  const overviewPeriodSummary = useMemo(() => {
    const rows = overview?.history || [];
    const forecastFteTotal = rows.reduce((sum, row) => sum + Number(row.forecast_fte_total || 0), 0);
    const actualFteTotal = rows.reduce((sum, row) => sum + Number(row.actual_report_fte_total || 0), 0);
    return {
      days: rows.length,
      forecastFteTotal,
      actualFteTotal,
      fteDelta: actualFteTotal - forecastFteTotal,
    };
  }, [overview?.history]);

  const selectedDayHours = selectedDayMatchesDate ? selectedDay?.hours || [] : [];

  const dayLossHotspots = useMemo(() => {
    const rows = selectedDayHours;
    return rows
      .filter((row) => Number(row.received_calls || 0) > 0)
      .map((row) => ({
        ...row,
        lossScore: Number(row.lost_calls || 0) * Number(row.no_answer_rate || 0),
      }))
      .sort((a, b) => {
        const byLost = Number(b.lost_calls || 0) - Number(a.lost_calls || 0);
        if (byLost !== 0) return byLost;
        return Number(b.no_answer_rate || 0) - Number(a.no_answer_rate || 0);
      })
      .slice(0, 5);
  }, [selectedDayHours]);

  const dayAcceptedLostData = useMemo(
    () =>
      selectedDayHours.map((row) => ({
        hour: row.hour_label,
        accepted: Number(row.accepted_calls || 0),
        lost: Number(row.lost_calls || 0),
        lossRate: Number(row.no_answer_rate || 0) * 100,
      })),
    [selectedDayHours],
  );

  const dayForecastFactData = useMemo(
    () =>
      selectedDayHours.map((row) => {
        const forecastCalls = Number(row.forecast_calls || 0);
        const factCalls = Number(row.received_calls || 0);
        return {
          hour: row.hour_label,
          forecastCalls,
          factCalls,
          delta: factCalls - forecastCalls,
          matchPercent: calculateForecastMatchPercent(factCalls, forecastCalls),
        };
      }),
    [selectedDayHours],
  );

  const dayCallDeltaHotspots = useMemo(
    () =>
      dayForecastFactData
        .filter((row) => row.forecastCalls > 0 || row.factCalls > 0)
        .map((row) => ({
          ...row,
          absDelta: Math.abs(row.delta),
          completion: row.forecastCalls > 0 ? row.factCalls / row.forecastCalls : 0,
        }))
        .sort((a, b) => b.absDelta - a.absDelta)
        .slice(0, 5),
    [dayForecastFactData],
  );

  const selectedLossSummary = useMemo(() => {
    const overviewRow = (overview?.history || []).find((item) => item.report_date === selectedDate);
    const source = selectedSummary || overviewRow;
    if (!source) return null;
    const peakLossHour = dayLossHotspots[0] || null;
    const forecastCalls = selectedSummary
      ? selectedDayHours.reduce((sum, row) => sum + Number(row.forecast_calls || 0), 0)
      : Number(source.forecast_calls_total || 0);
    const received = Number(source.total_received || 0);
    const callDelta = received - forecastCalls;
    const peakCallDeltaHour = dayCallDeltaHotspots[0] || null;
    return {
      reportDate: source.report_date,
      weekday: source.weekday_short,
      forecastCalls,
      received,
      accepted: Number(source.total_accepted || 0),
      lost: Number(source.total_lost || 0),
      callDelta,
      callsCompletion: forecastCalls > 0 ? received / forecastCalls : 0,
      callsMatchPercent: calculateForecastMatchPercent(received, forecastCalls),
      lossRate: Number(source.no_answer_rate || 0),
      peakLossHour,
      peakCallDeltaHour,
    };
  }, [dayCallDeltaHotspots, dayLossHotspots, overview?.history, selectedDate, selectedDayHours, selectedSummary]);

  const selectedLossTrendPoint = useMemo(
    () => historyTrendData.find((item) => item.reportDate === selectedDate) || null,
    [historyTrendData, selectedDate],
  );

  const selectLossReportDate = useCallback((reportDate) => {
    if (reportDate) setSelectedDate(reportDate);
  }, []);

  const selectLossChartDay = useCallback((state) => {
    const reportDate =
      state?.activePayload?.[0]?.payload?.reportDate ||
      state?.payload?.reportDate ||
      state?.reportDate;
    selectLossReportDate(reportDate);
  }, [selectLossReportDate]);

  const nextWeekForecast = overview?.next_week_forecast || {
    days: [],
    period_start: selectedForecastWeekStart,
    period_end: selectedForecastPeriodEnd,
    periodAhtSeconds: 0,
    weeklyAhtSeconds: 0,
    answerRate: 0,
    occ: 0,
    ur: 0,
    shrinkage: 0,
    weeklyHours: 0,
    effectiveMinutes: 0,
    weeklyFteHours: 0,
    baseOperators: 0,
    operatorsWithShrinkage: 0,
    currentOperatorFte: 0,
    operatorFteGap: 0,
    periodAvailableOperatorFte: 0,
    periodAvailableOperatorCount: 0,
    periodAvailableOperatorFteGap: 0,
    periodOperatorCount: 0,
    periodPartialOperatorCount: 0,
    periodUnavailableOperatorCount: 0,
    periodWorkingDaysThreshold: 0,
    periodAvailableOperatorRates: [],
    periodOperatorStatusSummary: {},
    periodOperatorAvailabilityDetails: [],
    historyComplete: false,
    history_periods: getForecastHistoryPeriods(selectedForecastWeekStart, selectedForecastPeriodEnd),
  };

  const selectedForecastDay = useMemo(
    () =>
      (nextWeekForecast.days || []).find((day) => day.forecast_date === selectedForecastDate) ||
      (nextWeekForecast.days || [])[0] ||
      null,
    [nextWeekForecast.days, selectedForecastDate],
  );

  useEffect(() => {
    const days = nextWeekForecast.days || [];
    if (!days.length) {
      setSelectedForecastDate('');
      return;
    }
    setSelectedForecastDate((current) => (
      days.some((day) => day.forecast_date === current) ? current : days[0].forecast_date
    ));
  }, [nextWeekForecast.days]);

  const selectedForecastHourlyData = useMemo(
    () =>
      (selectedForecastDay?.hourly_forecast || []).map((row) => ({
        hourNumber: Number(row.hour),
        hour: `${String(row.hour).padStart(2, '0')}:00`,
        calls: Number(row.forecast_calls || 0),
        upliftCalls: Number(row.incident_uplift_calls || 0),
        adjustedCalls: Number(row.incident_adjusted_calls ?? row.forecast_calls ?? 0),
        fte: Number(row.forecast_fte || 0),
        upliftFte: Number(row.incident_uplift_fte || 0),
        adjustedFte: Number(row.incident_adjusted_fte ?? row.forecast_fte ?? 0),
        workload: Number(row.forecast_workload_minutes || 0),
        upliftWorkload: Number(row.incident_uplift_workload_minutes || 0),
        adjustedWorkload: Number(row.incident_adjusted_workload_minutes ?? row.forecast_workload_minutes ?? 0),
        actualWorkload: row.has_actual_report ? Number(row.actual_workload_minutes || 0) : null,
        actualFte: row.has_actual_report ? Number(row.actual_report_fte || 0) : null,
      })),
    [selectedForecastDay],
  );

  const selectedForecastPeakHours = useMemo(
    () =>
      [...(selectedForecastDay?.hourly_forecast || [])]
        .sort((a, b) => Number(b.forecast_fte || 0) - Number(a.forecast_fte || 0))
        .slice(0, 5),
    [selectedForecastDay],
  );

  const selectedActualPeakHours = useMemo(
    () =>
      [...(selectedForecastDay?.hourly_forecast || [])]
        .filter((row) => row.has_actual_report)
        .sort((a, b) => Number(b.actual_report_fte || 0) - Number(a.actual_report_fte || 0))
        .slice(0, 5),
    [selectedForecastDay],
  );

  const todayValue = todayIso();
  const selectedForecastHasActualLoad = Boolean(
    selectedForecastDay?.has_actual_report && selectedForecastDay?.forecast_date <= todayValue,
  );
  const forecastActualLoadAvailable = (nextWeekForecast.days || []).some(
    (day) => day?.has_actual_report && day?.forecast_date <= todayValue,
  );
  const incidentUpliftAvailable = Number(nextWeekForecast.incidentUpliftFteHours || 0) > 0.01;
  const incidentRiskProfile = overview?.incident_uplift_dashboard || nextWeekForecast.incidentUplift || {};
  const incidentRiskDailyData = useMemo(
    () =>
      [...(incidentRiskProfile.daily || [])]
        .reverse()
        .map((row) => {
          const forecastCalls = Number(row.forecast_calls || 0);
          const actualCalls = Number(row.actual_calls || 0);
          const positiveDeltaCalls = Number(row.positive_delta_calls || 0);
          const deltaCalls = Number(row.delta_calls ?? (actualCalls - forecastCalls));
          const sourceHourCount = Number(row.source_hour_count || 0);
          const positiveHourCount = Number(row.positive_hour_count || 0);
          return {
            date: row.date,
            dateLabel: formatDate(row.date).slice(0, 5),
            forecastCalls,
            actualCalls,
            deltaCalls,
            positiveDeltaCalls,
            growthRatio: Number(row.growth_ratio || 0),
            completionRatio: Number(row.completion_ratio || 0),
            positiveHourCount,
            sourceHourCount,
            positiveHourShare: Number(row.positive_hour_share || 0),
            weight: Number(row.weight || 0),
            status: row.status || (positiveDeltaCalls > 0 ? 'overload' : 'held'),
          };
        }),
    [incidentRiskProfile.daily],
  );
  const incidentRiskTopHours = useMemo(
    () =>
      [...(incidentRiskProfile.hourly || [])]
        .map((row) => ({
          hour: Number(row.hour || 0),
          hourLabel: `${String(row.hour || 0).padStart(2, '0')}:00`,
          growthRatio: Number(row.growth_ratio || 0),
          rawGrowthRatio: Number(row.raw_growth_ratio || 0),
          weightedDeltaCalls: Number(row.weighted_delta_calls || 0),
          confidence: Number(row.confidence || 0),
          positiveSourceCount: Number(row.positive_source_count || 0),
          sourceCount: Number(row.source_count || 0),
          persistenceFactor: Number(row.persistence_factor || 0),
        }))
        .filter((row) => row.growthRatio > 0 || row.weightedDeltaCalls > 0)
        .sort((a, b) => {
          const byDelta = b.weightedDeltaCalls - a.weightedDeltaCalls;
          if (byDelta !== 0) return byDelta;
          return b.growthRatio - a.growthRatio;
        })
        .slice(0, 6),
    [incidentRiskProfile.hourly],
  );
  const incidentRiskSummary = useMemo(() => {
    const rawSummary = incidentRiskProfile.daily_summary || {};
    const sourceDayCount = Number(rawSummary.source_day_count ?? incidentRiskDailyData.length);
    const overloadDayCount = Number(
      rawSummary.overload_day_count ?? incidentRiskDailyData.filter((row) => row.status === 'overload').length,
    );
    const heldDayCount = Number(rawSummary.held_day_count ?? Math.max(0, sourceDayCount - overloadDayCount));
    const totalForecastCalls = Number(
      rawSummary.total_forecast_calls ?? incidentRiskDailyData.reduce((sum, row) => sum + row.forecastCalls, 0),
    );
    const totalActualCalls = Number(
      rawSummary.total_actual_calls ?? incidentRiskDailyData.reduce((sum, row) => sum + row.actualCalls, 0),
    );
    const totalPositiveDeltaCalls = Number(
      rawSummary.total_positive_delta_calls ?? incidentRiskDailyData.reduce((sum, row) => sum + row.positiveDeltaCalls, 0),
    );
    return {
      sourceDayCount,
      overloadDayCount,
      heldDayCount,
      totalForecastCalls,
      totalActualCalls,
      totalDeltaCalls: Number(rawSummary.total_delta_calls ?? (totalActualCalls - totalForecastCalls)),
      totalPositiveDeltaCalls,
      weightedDailyGrowthRatio: Number(rawSummary.weighted_daily_growth_ratio || 0),
      averageGrowthRatio: Number(incidentRiskProfile.average_growth_ratio || 0),
      rawAverageGrowthRatio: Number(incidentRiskProfile.raw_average_growth_ratio || 0),
    };
  }, [incidentRiskDailyData, incidentRiskProfile.average_growth_ratio, incidentRiskProfile.daily_summary, incidentRiskProfile.raw_average_growth_ratio]);
  const incidentProjection = incidentRiskProfile.projection || {};
  const incidentProjectionData = useMemo(
    () =>
      (incidentProjection.days || []).map((row) => {
        const forecastCalls = Number(row.forecast_calls || 0);
        const upliftCalls = Number(row.incident_uplift_calls || 0);
        return {
          date: row.date,
          dateLabel: formatDate(row.date).slice(0, 5),
          weekday: row.weekday_short || '',
          forecastCalls,
          upliftCalls,
          adjustedCalls: Number(row.incident_adjusted_calls ?? (forecastCalls + upliftCalls)),
          forecastFte: Number(row.forecast_daily_fte || 0),
          upliftFte: Number(row.incident_uplift_fte || 0),
          adjustedFte: Number(row.incident_adjusted_daily_fte || 0),
          upliftRatio: Number(row.incident_uplift_ratio || 0),
          futureWeight: Number(row.incident_future_weight || 0),
        };
      }),
    [incidentProjection.days],
  );
  const activeForecastHour = hoveredForecastHour ?? pinnedForecastHour;
  const activeForecastHourLabel = activeForecastHour !== null ? `${String(activeForecastHour).padStart(2, '0')}:00` : null;
  useEffect(() => {
    setHoveredForecastHour(null);
    setPinnedForecastHour(null);
  }, [selectedForecastDay?.forecast_date]);

  const hoverForecastSlice = useCallback((label) => {
    const hour = hourFromChartLabel(label);
    setHoveredForecastHour(hour);
  }, []);

  const togglePinnedForecastSlice = useCallback((labelOrHour) => {
    const hour = typeof labelOrHour === 'number' ? labelOrHour : hourFromChartLabel(labelOrHour);
    if (hour === null) return;
    setPinnedForecastHour((current) => (Number(current) === Number(hour) ? null : hour));
  }, []);

  const ForecastHourlyTooltip = useCallback(
    ({ active, label }) => {
      if (!active) return null;
      const hour = hourFromChartLabel(label);
      const row = (selectedForecastDay?.hourly_forecast || []).find((item) => Number(item.hour) === Number(hour));
      if (!row) return null;
      return (
        <div className="min-w-56 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
          <div className="mb-2 font-semibold text-slate-900">{String(row.hour).padStart(2, '0')}:00</div>
          {showForecastActualLoad && selectedForecastHasActualLoad ? (
            <div className="space-y-2">
              <div className="rounded-md bg-slate-50 px-2 py-1.5">
                <div className="mb-1 font-medium text-slate-500">Звонки</div>
                <div className="flex justify-between gap-6"><span>Прогноз</span><b className="text-blue-700">{formatNumber(row.forecast_calls, 1)}</b></div>
                <div className="flex justify-between gap-6"><span>Возможный прирост</span><b className="text-emerald-700">+{formatNumber(row.incident_uplift_calls, 1)}</b></div>
                <div className="flex justify-between gap-6"><span>С учетом прироста</span><b className="text-slate-900">{formatNumber(row.incident_adjusted_calls ?? row.forecast_calls, 1)}</b></div>
                <div className="flex justify-between gap-6"><span>Факт</span><b className="text-emerald-700">{row.has_actual_report ? formatInt(row.actual_received_calls) : '-'}</b></div>
              </div>
              <div className="rounded-md bg-slate-50 px-2 py-1.5">
                <div className="mb-1 font-medium text-slate-500">Минуты нагрузки</div>
                <div className="flex justify-between gap-6"><span>Прогноз</span><b className="text-blue-700">{formatNumber(row.forecast_workload_minutes, 1)}</b></div>
                <div className="flex justify-between gap-6"><span>Прирост</span><b className="text-emerald-700">+{formatNumber(row.incident_uplift_workload_minutes, 1)}</b></div>
                <div className="flex justify-between gap-6"><span>Факт</span><b className="text-emerald-700">{row.has_actual_report ? formatNumber(row.actual_workload_minutes, 1) : '-'}</b></div>
              </div>
              <div className="rounded-md bg-slate-50 px-2 py-1.5">
                <div className="mb-1 font-medium text-slate-500">FTE</div>
                <div className="flex justify-between gap-6"><span>Прогноз</span><b className="text-blue-700">{formatNumber(row.forecast_fte, 2)}</b></div>
                <div className="flex justify-between gap-6"><span>Прирост</span><b className="text-emerald-700">+{formatNumber(row.incident_uplift_fte, 2)}</b></div>
                <div className="flex justify-between gap-6"><span>Факт</span><b className="text-emerald-700">{row.has_actual_report ? formatNumber(row.actual_report_fte, 2) : '-'}</b></div>
              </div>
            </div>
          ) : (
            <div className="space-y-1 text-slate-600">
              <div className="flex justify-between gap-6"><span>Прогноз звонков</span><b className="text-slate-900">{formatNumber(row.forecast_calls, 1)}</b></div>
              <div className="flex justify-between gap-6"><span>Возможный прирост</span><b className="text-emerald-700">+{formatNumber(row.incident_uplift_calls, 1)}</b></div>
              <div className="flex justify-between gap-6"><span>Звонков с приростом</span><b className="text-slate-900">{formatNumber(row.incident_adjusted_calls ?? row.forecast_calls, 1)}</b></div>
              <div className="flex justify-between gap-6"><span>Прогноз минут</span><b className="text-blue-700">{formatNumber(row.forecast_workload_minutes, 1)}</b></div>
              <div className="flex justify-between gap-6"><span>Прогноз FTE</span><b className="text-blue-700">{formatNumber(row.forecast_fte, 2)}</b></div>
              <div className="flex justify-between gap-6"><span>FTE с приростом</span><b className="text-emerald-700">{formatNumber(row.incident_adjusted_fte ?? row.forecast_fte, 2)}</b></div>
            </div>
          )}
          {pinnedForecastHour !== null && Number(pinnedForecastHour) === Number(row.hour) ? (
            <div className="mt-2 rounded bg-slate-100 px-2 py-1 font-medium text-slate-600">Срез закреплен</div>
          ) : null}
        </div>
      );
    },
    [pinnedForecastHour, selectedForecastDay?.hourly_forecast, selectedForecastHasActualLoad, showForecastActualLoad],
  );

  const visibleMetricCount = [
    displayOptions.metricOperators,
    displayOptions.metricWeeklyFte,
    displayOptions.metricBaseOperators,
    displayOptions.metricHistoryWarnings,
    displayOptions.metricLostCalls,
    displayOptions.metricLossRate,
  ].filter(Boolean).length;

  const toggleDisplayOption = useCallback((key, value) => {
    setDisplayOptions((current) => ({ ...current, [key]: Boolean(value) }));
  }, []);

  const handleUpload = async (event) => {
    event.preventDefault();
    if (!uploadFile) {
      notify('Выберите CSV-файл', 'error');
      return;
    }
    const formData = new FormData();
    formData.append('file', uploadFile);
    setIsUploading(true);
    try {
      const response = await axios.post(`${apiRoot}/api/resource_fte/upload`, formData, {
        headers: buildHeaders(),
      });
      const uploadedDaysCount = Number(response.data?.uploaded_days_count || 0);
      notify(uploadedDaysCount > 1 ? `Загружено дней: ${uploadedDaysCount}` : 'Отчет загружен и пересчитан');
      setUploadFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      setSelectedDate(response.data?.report_date || '');
      setIsUploadModalOpen(false);
      await fetchOverview();
    } catch (error) {
      const data = error?.response?.data || {};
      const missing = Array.isArray(data.missing) ? `: ${data.missing.join(', ')}` : '';
      notify((data.error || 'Не удалось загрузить CSV') + missing, 'error');
    } finally {
      setIsUploading(false);
    }
  };

  const handleRecalculate = async () => {
    setIsRecalculating(true);
    try {
      await axios.post(
        `${apiRoot}/api/resource_fte/recalculate`,
        {},
        { headers: buildHeaders() },
      );
      notify('Прогноз пересчитан');
      await fetchOverview();
      await fetchDay(selectedDate);
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось пересчитать прогноз', 'error');
    } finally {
      setIsRecalculating(false);
    }
  };

  const handleSaveSettings = async () => {
    try {
      const response = await axios.put(`${apiRoot}/api/resource_fte/settings`, settingsDraft, {
        headers: buildHeaders({
          'Content-Type': 'application/json',
        }),
      });
      notify('Настройки сохранены');
      setOverview(response.data?.overview || overview);
      setSettingsDraft(response.data?.settings || settingsDraft);
      await fetchDay(selectedDate);
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось сохранить настройки', 'error');
    }
  };

  const resourceDirections = overview?.directions || [];
  const loadedReportDates = useMemo(
    () => Array.from(new Set([
      ...loadedDateCache,
      ...(overview?.loaded_report_dates || []),
      ...(overview?.history || []).map((item) => item.report_date).filter(Boolean),
    ])).sort(),
    [loadedDateCache, overview?.history, overview?.loaded_report_dates],
  );
  const loadedReportDateSet = useMemo(() => new Set(loadedReportDates), [loadedReportDates]);
  const forecastHistoryPeriods = nextWeekForecast.history_periods ||
    nextWeekForecast.history_weeks ||
    getForecastHistoryPeriods(selectedForecastWeekStart, selectedForecastPeriodEnd);
  const forecastPeriodStart = selectedForecastWeekStart || nextWeekForecast.period_start || nextWeekForecast.week_start;
  const forecastPeriodEnd = selectedForecastPeriodEnd || nextWeekForecast.period_end || nextWeekForecast.week_end;
  const forecastPeriodComplete = Boolean(nextWeekForecast.historyComplete) ||
    isForecastPeriodHistoryComplete(forecastPeriodStart, forecastPeriodEnd, loadedReportDateSet);
  const selectedFileName = uploadFile?.name || 'Файл не выбран';
  const selectedDirectionIds = (settingsDraft?.selected_direction_ids || []).map((item) => Number(item)).filter(Boolean);
  const selectedDirectionSet = new Set(selectedDirectionIds);
  const availabilityDirectionIds = (overview?.settings?.selected_direction_ids || []).map((item) => Number(item)).filter(Boolean);
  const periodAvailableOperatorFte = Number(
    nextWeekForecast.periodAvailableOperatorFte ?? nextWeekForecast.currentOperatorFte ?? 0,
  );
  const periodAvailableOperatorCount = Number(nextWeekForecast.periodAvailableOperatorCount ?? 0);
  const periodOperatorCount = Number(nextWeekForecast.periodOperatorCount ?? periodAvailableOperatorCount);
  const periodPartialOperatorCount = Number(nextWeekForecast.periodPartialOperatorCount ?? 0);
  const periodUnavailableOperatorCount = Number(nextWeekForecast.periodUnavailableOperatorCount ?? 0);
  const periodAvailableOperatorFteGap = Number(
    nextWeekForecast.periodAvailableOperatorFteGap ?? (
      periodAvailableOperatorFte - Number(nextWeekForecast.operatorsWithShrinkage || 0)
    ),
  );
  const operatorAvailabilityCacheKey = [
    forecastPeriodStart || '',
    forecastPeriodEnd || '',
    availabilityDirectionIds.join(','),
  ].join('|');
  const operatorAvailabilityDetailsPayload = operatorAvailabilityDetailsByKey[operatorAvailabilityCacheKey] || null;
  const operatorDetailsForecast = operatorAvailabilityDetailsPayload
    ? { ...nextWeekForecast, ...operatorAvailabilityDetailsPayload }
    : nextWeekForecast;

  const fetchOperatorAvailabilityDetails = useCallback(async () => {
    if (!apiRoot || !forecastPeriodStart || !forecastPeriodEnd) return null;
    if (operatorAvailabilityDetailsByKey[operatorAvailabilityCacheKey]) {
      setOperatorDetailsError('');
      return operatorAvailabilityDetailsByKey[operatorAvailabilityCacheKey];
    }
    setIsOperatorDetailsLoading(true);
    setOperatorDetailsError('');
    try {
      const response = await axios.get(`${apiRoot}/api/resource_fte/operator_availability`, {
        params: {
          forecast_date_from: forecastPeriodStart,
          forecast_date_to: forecastPeriodEnd,
        },
        headers: buildHeaders(),
      });
      const payload = response.data?.availability || {};
      setOperatorAvailabilityDetailsByKey((current) => ({
        ...current,
        [operatorAvailabilityCacheKey]: payload,
      }));
      return payload;
    } catch (error) {
      const message = error?.response?.data?.error || 'Не удалось загрузить детализацию операторов';
      setOperatorDetailsError(message);
      notify(message, 'error');
      return null;
    } finally {
      setIsOperatorDetailsLoading(false);
    }
  }, [
    apiRoot,
    buildHeaders,
    forecastPeriodEnd,
    forecastPeriodStart,
    notify,
    operatorAvailabilityCacheKey,
    operatorAvailabilityDetailsByKey,
  ]);

  const openOperatorDetails = useCallback(() => {
    setIsOperatorDetailsOpen(true);
    fetchOperatorAvailabilityDetails();
  }, [fetchOperatorAvailabilityDetails]);

  useEffect(() => {
    if (isOperatorDetailsOpen) fetchOperatorAvailabilityDetails();
  }, [fetchOperatorAvailabilityDetails, isOperatorDetailsOpen]);

  const toggleResourceDirection = (directionId, checked) => {
    setSettingsDraft((current) => {
      const currentIds = (current?.selected_direction_ids || []).map((item) => Number(item)).filter(Boolean);
      const next = new Set(currentIds);
      if (checked) next.add(Number(directionId));
      else next.delete(Number(directionId));
      return { ...current, selected_direction_ids: Array.from(next) };
    });
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 px-4 py-4 backdrop-blur md:px-6">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-950">Расчет ресурсов / FTE</h1>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {activeDashboardView === 'overview' || activeDashboardView === 'losses' ? (
              <div className="w-full sm:w-[330px]">
                <CalendarPicker
                  mode="range"
                  label="Период анализа"
                  startValue={dateFrom}
                  endValue={dateTo}
                  onRangeChange={(start, end) => {
                    setDateFrom(start);
                    setDateTo(end);
                  }}
                  loadedDates={loadedReportDates}
                  hint="точка = есть отчет"
                />
              </div>
            ) : null}
            <div className="w-full sm:w-[240px]">
              <button
                type="button"
                onClick={() => {
                  setUploadFile(null);
                  if (fileInputRef.current) fileInputRef.current.value = '';
                  setIsUploadModalOpen(true);
                }}
                className="flex h-14 w-full items-center justify-between gap-3 rounded-xl border-2 border-slate-200 bg-white px-4 text-left text-sm shadow-sm transition hover:border-slate-300 hover:bg-slate-50"
              >
                <span className="min-w-0">
                  <span className="block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Загрузка</span>
                  <span className="block truncate font-semibold text-slate-900">CSV по датам</span>
                </span>
                <UploadCloud size={17} className="shrink-0 text-blue-600" />
              </button>
            </div>
            <button
              type="button"
              onClick={fetchOverview}
              className="inline-flex h-14 items-center gap-2 rounded-xl border-2 border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-100"
            >
              <RefreshCw size={16} className={isLoading ? 'animate-spin' : ''} />
              Обновить
            </button>
          </div>
        </div>
      </div>

      {isUploadModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4 backdrop-blur-sm">
          <form onSubmit={handleUpload} className="w-full max-w-xl rounded-2xl border-2 border-slate-200 bg-white px-5 py-7 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-base font-semibold text-slate-950">
                  <UploadCloud size={19} className="text-blue-600" />
                  Загрузка отчета
                </div>
                <p className="mt-1 text-sm text-slate-500">Загрузите CSV, где каждая строка содержит дату и час. Система сама обновит все даты из файла.</p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setUploadFile(null);
                  if (fileInputRef.current) fileInputRef.current.value = '';
                  setIsUploadModalOpen(false);
                }}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border-2 border-slate-200 text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
              >
                <X size={16} />
              </button>
            </div>

            <div className="mt-6 rounded-xl border-2 border-slate-200 bg-slate-50 px-4 py-5">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Формат файла</div>
                  <div className="mt-1 text-xl font-semibold text-slate-950">Дата + час</div>
                </div>
                <div className="inline-flex w-fit items-center gap-2 rounded-lg bg-white px-3 py-2 text-xs font-semibold text-slate-600">
                  <CalendarDays size={14} />
                  Старый формат без колонки Дата не принимается
                </div>
              </div>
            </div>

            <div className="mt-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">CSV-отчет за период</div>
              <div className="flex min-h-20 items-center justify-between gap-3 rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-5">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-slate-900">{selectedFileName}</div>
                  <div className="text-xs text-slate-500">Поддерживается .csv</div>
                </div>
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="inline-flex h-12 shrink-0 items-center gap-2 rounded-xl border-2 border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-100"
                >
                  <FileUp size={15} />
                  Выбрать
                </button>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,text/csv"
                onChange={(event) => setUploadFile(event.target.files?.[0] || null)}
                className="hidden"
              />
            </div>

            <div className="mt-7 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button
                type="button"
                onClick={() => {
                  setUploadFile(null);
                  if (fileInputRef.current) fileInputRef.current.value = '';
                  setIsUploadModalOpen(false);
                }}
                className="inline-flex h-12 items-center justify-center rounded-xl border-2 border-slate-200 bg-white px-5 text-sm font-semibold text-slate-700 transition hover:bg-slate-100"
              >
                Отмена
              </button>
              <button
                type="submit"
                disabled={isUploading}
                className="inline-flex h-12 items-center justify-center gap-2 rounded-xl bg-blue-600 px-5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-300"
              >
                <FileUp size={16} />
                {isUploading ? 'Загрузка...' : 'Загрузить отчет'}
              </button>
            </div>
          </form>
        </div>
      )}

      <OperatorAvailabilityDetailsModal
        open={isOperatorDetailsOpen}
        onClose={() => setIsOperatorDetailsOpen(false)}
        forecast={operatorDetailsForecast}
        isLoading={isOperatorDetailsLoading}
        error={operatorDetailsError}
      />

      <div className="space-y-6 p-4 md:p-6">
        <div className="flex flex-col gap-3 rounded-xl border-2 border-slate-200 bg-white p-2 shadow-sm lg:flex-row lg:items-center lg:justify-between">
          <div className="flex gap-1 overflow-x-auto">
            {VIEW_TABS.map((tab) => {
              const Icon = tab.icon;
              const active = activeDashboardView === tab.key;
              return (
                <button
                  key={tab.key}
                  type="button"
                  onClick={() => setActiveDashboardView(tab.key)}
                  className={`inline-flex h-10 shrink-0 items-center gap-2 rounded-lg px-3 text-sm font-semibold transition ${
                    active ? 'bg-slate-900 text-white shadow-sm' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                  }`}
                >
                  <Icon size={16} />
                  {tab.label}
                </button>
              );
            })}
          </div>
          <button
            type="button"
            onClick={() => setActiveDashboardView('settings')}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
          >
            <Eye size={16} />
            Показатели
          </button>
        </div>

        {activeDashboardView !== 'settings' && activeDashboardView !== 'next_week' && activeDashboardView !== 'schedule_planner' && visibleMetricCount > 0 && (
          <div className={`grid gap-3 md:grid-cols-2 ${visibleMetricCount >= 5 ? 'xl:grid-cols-6' : visibleMetricCount >= 4 ? 'xl:grid-cols-4' : 'xl:grid-cols-3'}`}>
            {displayOptions.metricOperators && (
              <StatCard
                icon={TrendingUp}
                label="Прогноз FTE периода"
                value={formatNumber(overviewPeriodSummary.forecastFteTotal, 1)}
                hint="Сумма прогнозных FTE по загруженным дням"
                tone="blue"
              />
            )}
            {displayOptions.metricWeeklyFte && (
              <StatCard icon={Users} label="Факт FTE периода" value={formatNumber(overviewPeriodSummary.actualFteTotal, 1)} hint="Из разговорной нагрузки отчетов, без смен" tone="emerald" />
            )}
            {displayOptions.metricBaseOperators && (
              <StatCard
                icon={Clock3}
                label="Разница FTE"
                value={formatSignedNumber(overviewPeriodSummary.fteDelta, 1)}
                hint="Факт минус прогноз за период"
                tone={overviewPeriodSummary.fteDelta < -0.5 ? 'rose' : overviewPeriodSummary.fteDelta > 0.5 ? 'emerald' : 'slate'}
              />
            )}
            {displayOptions.metricHistoryWarnings && (
              <StatCard icon={CalendarDays} label="Дни с отчетами" value={overviewPeriodSummary.days} hint="В выбранном периоде анализа" tone="slate" />
            )}
            {displayOptions.metricLostCalls && (
              <StatCard icon={PhoneMissed} label="Потерянные звонки" value={formatInt(periodLossSummary.totalLost)} hint={`Принято: ${formatInt(periodLossSummary.totalAccepted)}`} tone="rose" />
            )}
            {displayOptions.metricLossRate && (
              <StatCard icon={ShieldAlert} label="Доля потерь" value={formatPercent(periodLossSummary.lossRate)} hint={periodLossSummary.worstDay ? `Пик: ${formatDate(periodLossSummary.worstDay.report_date)}` : 'За выбранный период'} tone={periodLossSummary.lossRate > 0.08 ? 'rose' : 'amber'} />
            )}
          </div>
        )}

        {activeDashboardView === 'overview' && (
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">Сводка по периоду</h2>
                <p className="text-sm text-slate-500">Динамика звонков и FTE по загруженным дням в выбранном диапазоне.</p>
              </div>
              <div className="text-sm text-slate-500">{(overview?.history || []).length} дней в истории</div>
            </div>
            {historyTrendData.length ? (
              <div className="mt-5 h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={historyTrendData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                    <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                    <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                    <Tooltip content={<OverviewTrendTooltip />} />
                    {displayOptions.chartCalls && <Bar yAxisId="left" dataKey="calls" fill="#bfdbfe" radius={[4, 4, 0, 0]} />}
                    {displayOptions.chartLosses && <Bar yAxisId="left" dataKey="lost" fill="#fecdd3" radius={[4, 4, 0, 0]} />}
                    {displayOptions.chartFte && <Line yAxisId="right" type="monotone" dataKey="forecastFte" stroke="#2563eb" strokeWidth={2} dot={false} />}
                    {displayOptions.chartActual && <Line yAxisId="right" type="monotone" dataKey="actualFte" stroke="#059669" strokeWidth={2} dot={false} />}
                    {displayOptions.chartLossRate && <Line yAxisId="right" type="monotone" dataKey="lossRate" stroke="#e11d48" strokeWidth={2} dot={false} />}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <EmptyState title="Нет данных для сводки" text="Загрузите первый ежедневный CSV, чтобы увидеть динамику." />
            )}
          </section>
        )}

        {activeDashboardView === 'overview' && (
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">Прирост и выдержка прогноза</h2>
                <p className="text-sm text-slate-500">Последние 6 дней до текущего дня формируют риск, а ближайшие 7 дней показывают уже построенный прирост.</p>
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                Источник: {incidentRiskProfile.source_start ? formatDate(incidentRiskProfile.source_start) : '-'} - {incidentRiskProfile.source_end ? formatDate(incidentRiskProfile.source_end) : '-'}
              </div>
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
              <div className={`rounded-lg border px-3 py-3 ${incidentRiskSummary.overloadDayCount > 0 ? 'border-rose-200 bg-rose-50' : 'border-emerald-200 bg-emerald-50'}`}>
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Выдержка</div>
                <div className={`mt-1 text-2xl font-semibold ${incidentRiskSummary.overloadDayCount > 0 ? 'text-rose-700' : 'text-emerald-700'}`}>
                  {formatInt(incidentRiskSummary.heldDayCount)} / {formatInt(incidentRiskSummary.sourceDayCount)}
                </div>
                <div className="mt-1 text-xs text-slate-500">дней без почасового превышения</div>
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Факт - прогноз</div>
                <div className={`mt-1 text-2xl font-semibold ${incidentRiskSummary.totalDeltaCalls > 0 ? 'text-rose-700' : incidentRiskSummary.totalDeltaCalls < 0 ? 'text-emerald-700' : 'text-slate-900'}`}>{formatSignedNumber(incidentRiskSummary.totalDeltaCalls, 0)}</div>
                <div className="mt-1 text-xs text-slate-500">{formatInt(incidentRiskSummary.totalActualCalls)} факт / {formatInt(incidentRiskSummary.totalForecastCalls)} прогноз</div>
              </div>
              <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-rose-600">Превышение</div>
                <div className="mt-1 text-2xl font-semibold text-rose-700">+{formatInt(incidentRiskSummary.totalPositiveDeltaCalls)}</div>
                <div className="mt-1 text-xs text-rose-700">только часы выше прогноза</div>
              </div>
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-emerald-700">Прирост 7 дней</div>
                <div className="mt-1 text-2xl font-semibold text-emerald-700">+{formatInt(incidentProjection.incident_uplift_calls)}</div>
                <div className="mt-1 text-xs text-emerald-700">звонков: {formatDate(incidentProjection.period_start)} - {formatDate(incidentProjection.period_end)}</div>
              </div>
              <div className="rounded-lg border border-emerald-200 bg-white px-3 py-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-emerald-700">Доп. FTE</div>
                <div className="mt-1 text-2xl font-semibold text-emerald-700">+{formatNumber(incidentProjection.incident_uplift_fte_hours, 1)}</div>
                <div className="mt-1 text-xs text-slate-500">FTE-ч на ближайшие 7 дней</div>
              </div>
            </div>

            <div className="mt-5 grid gap-4 xl:grid-cols-2">
              <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-3">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <ShieldAlert size={16} />
                    Последние 6 дней
                  </div>
                  <span className="text-xs font-medium text-slate-500">ближайшие дни в истории имеют больший вес</span>
                </div>
                {incidentRiskDailyData.length ? (
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={incidentRiskDailyData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="dateLabel" tick={{ fontSize: 11 }} />
                        <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                        <Tooltip content={<IncidentRiskTooltip />} />
                        <Bar yAxisId="left" dataKey="forecastCalls" fill="#bfdbfe" radius={[4, 4, 0, 0]} />
                        <Bar yAxisId="left" dataKey="positiveDeltaCalls" fill="#fecdd3" radius={[4, 4, 0, 0]} />
                        <Line yAxisId="right" type="monotone" dataKey="actualCalls" stroke="#0f172a" strokeWidth={2} dot={{ r: 3 }} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState title="Нет данных для риска" text="Нужно загрузить отчеты за последние дни, чтобы увидеть выдержку прогноза." />
                )}
              </div>

              <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-3">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <TrendingUp size={16} />
                    Построенный прирост на 7 дней
                  </div>
                  <span className="text-xs font-medium text-slate-500">от текущего дня, без влияния выбранного периода</span>
                </div>
                {incidentProjectionData.length ? (
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={incidentProjectionData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="dateLabel" tick={{ fontSize: 11 }} />
                        <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                        <Tooltip content={<IncidentProjectionTooltip />} />
                        <Bar yAxisId="left" dataKey="forecastCalls" stackId="calls" fill="#bfdbfe" radius={[0, 0, 0, 0]} />
                        <Bar yAxisId="left" dataKey="upliftCalls" stackId="calls" fill="#86efac" radius={[4, 4, 0, 0]} />
                        <Line yAxisId="right" type="monotone" dataKey="upliftFte" stroke="#059669" strokeWidth={2} dot={{ r: 3 }} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState title="Нет прогноза прироста" text="После расчета FTE здесь появится разложение риска на ближайшие 7 дней." />
                )}
              </div>
            </div>

            <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="mb-3 text-sm font-semibold text-slate-900">Дни, которые сформировали риск</div>
                <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                  {incidentRiskDailyData.length ? incidentRiskDailyData.map((row) => (
                    <div key={row.date} className={`rounded-lg border bg-white px-3 py-2 ${row.status === 'overload' ? 'border-rose-200' : 'border-emerald-200'}`}>
                      <div className="flex items-center justify-between gap-2">
                        <div className="font-semibold text-slate-900">{formatDate(row.date)}</div>
                        <span className={`rounded-md px-2 py-0.5 text-[11px] font-semibold ${row.status === 'overload' ? 'bg-rose-100 text-rose-700' : 'bg-emerald-100 text-emerald-700'}`}>
                          {row.status === 'overload' ? 'не выдержал' : 'выдержал'}
                        </span>
                      </div>
                      <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-slate-500">
                        <span>прогноз <b className="text-blue-700">{formatNumber(row.forecastCalls, 0)}</b></span>
                        <span>факт <b className="text-slate-900">{formatNumber(row.actualCalls, 0)}</b></span>
                        <span>вес <b className="text-slate-900">{formatNumber(row.weight, 0)}</b></span>
                      </div>
                      <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
                        <div className={`h-full rounded-full ${row.status === 'overload' ? 'bg-rose-500' : 'bg-emerald-500'}`} style={{ width: `${Math.min(100, Math.max(6, row.positiveHourShare * 100))}%` }} />
                      </div>
                    </div>
                  )) : (
                    <div className="rounded-lg border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500 md:col-span-2 xl:col-span-3">Нет загруженных дней для расчета риска.</div>
                  )}
                </div>
              </div>

              <div className="rounded-lg border border-slate-200 bg-white p-4">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <Clock3 size={16} />
                  Часы прироста
                </div>
                <div className="space-y-3">
                  {incidentRiskTopHours.length ? incidentRiskTopHours.map((row) => (
                    <div key={row.hour} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <div className="font-semibold text-slate-900">{row.hourLabel}</div>
                        <div className="rounded-md bg-emerald-100 px-2 py-1 text-xs font-semibold text-emerald-700">+{formatNumber(row.weightedDeltaCalls, 1)} зв.</div>
                      </div>
                      <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-slate-500">
                        <span>риск <b className="text-emerald-700">{formatPercent(row.growthRatio, 0)}</b></span>
                        <span>надежн. <b className="text-slate-900">{formatPercent(row.confidence, 0)}</b></span>
                        <span>дней <b className="text-slate-900">{formatInt(row.positiveSourceCount)}/{formatInt(row.sourceCount)}</b></span>
                      </div>
                    </div>
                  )) : (
                    <div className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500">За последние 6 дней нет часов, где факт был выше прогноза.</div>
                  )}
                </div>
              </div>
            </div>
          </section>
        )}

        {activeDashboardView === 'losses' && (
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">Аналитика звонков</h2>
                <p className="text-sm text-slate-500">Факт, прогноз, потери и принятые звонки в выбранном периоде.</p>
              </div>
              {periodLossSummary.worstDay ? (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
                  Худший день: <b>{formatDate(periodLossSummary.worstDay.report_date)}</b> · {formatPercent(periodLossSummary.worstDay.no_answer_rate)}
                </div>
              ) : null}
            </div>

            <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
              <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-3">
                <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <PhoneCall size={16} />
                    Звонки по дням
                  </div>
                  <div className="inline-flex w-fit rounded-lg border border-slate-200 bg-slate-50 p-1">
                    {[
                      ['losses', 'Потери/Принятые'],
                      ['forecastFact', 'Факт кол-во/Прогноз кол-во'],
                    ].map(([mode, label]) => (
                      <button
                        key={mode}
                        type="button"
                        onClick={() => setCallsChartMode(mode)}
                        className={`h-8 rounded-md px-3 text-xs font-semibold transition ${
                          callsChartMode === mode
                            ? 'bg-slate-900 text-white shadow-sm'
                            : 'text-slate-600 hover:bg-white hover:text-slate-900'
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
                {historyTrendData.length ? (
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart
                        data={historyTrendData}
                        margin={{ top: 10, right: 18, left: 0, bottom: 0 }}
                        onClick={selectLossChartDay}
                        className="cursor-pointer"
                      >
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                        <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                        <YAxis
                          yAxisId="right"
                          orientation="right"
                          tick={{ fontSize: 11 }}
                          domain={callsChartMode === 'forecastFact' ? [0, 100] : undefined}
                          tickFormatter={callsChartMode === 'forecastFact' ? (value) => `${Math.round(value)}%` : undefined}
                        />
                        <Tooltip content={<CallsTrendTooltip mode={callsChartMode} />} />
                        {selectedLossTrendPoint ? (
                          <ReferenceLine yAxisId="left" x={selectedLossTrendPoint.date} stroke="#0f172a" strokeDasharray="4 4" />
                        ) : null}
                        {callsChartMode === 'losses' && displayOptions.chartCalls && (
                          <Bar yAxisId="left" dataKey="accepted" stackId="calls" fill="#bbf7d0" radius={[0, 0, 0, 0]} onClick={selectLossChartDay}>
                            {historyTrendData.map((item) => (
                              <Cell
                                key={`accepted-${item.reportDate}`}
                                fill={item.reportDate === selectedDate ? '#22c55e' : '#bbf7d0'}
                                className="cursor-pointer"
                                onClick={() => selectLossReportDate(item.reportDate)}
                              />
                            ))}
                          </Bar>
                        )}
                        {callsChartMode === 'losses' && displayOptions.chartLosses && (
                          <Bar yAxisId="left" dataKey="lost" stackId="calls" fill="#fecdd3" radius={[4, 4, 0, 0]} onClick={selectLossChartDay}>
                            {historyTrendData.map((item) => (
                              <Cell
                                key={`lost-${item.reportDate}`}
                                fill={item.reportDate === selectedDate ? '#fb7185' : '#fecdd3'}
                                className="cursor-pointer"
                                onClick={() => selectLossReportDate(item.reportDate)}
                              />
                            ))}
                          </Bar>
                        )}
                        {callsChartMode === 'forecastFact' && displayOptions.chartCalls && (
                          <>
                            <Bar yAxisId="left" dataKey="forecastCalls" fill="#bfdbfe" radius={[4, 4, 0, 0]} onClick={selectLossChartDay}>
                              {historyTrendData.map((item) => (
                                <Cell
                                  key={`forecast-calls-${item.reportDate}`}
                                  fill={item.reportDate === selectedDate ? '#60a5fa' : '#bfdbfe'}
                                  className="cursor-pointer"
                                  onClick={() => selectLossReportDate(item.reportDate)}
                                />
                              ))}
                            </Bar>
                            <Bar yAxisId="left" dataKey="calls" fill="#22c55e" radius={[4, 4, 0, 0]} onClick={selectLossChartDay}>
                              {historyTrendData.map((item) => (
                                <Cell
                                  key={`fact-calls-${item.reportDate}`}
                                  fill={item.reportDate === selectedDate ? '#16a34a' : '#22c55e'}
                                  className="cursor-pointer"
                                  onClick={() => selectLossReportDate(item.reportDate)}
                                />
                              ))}
                            </Bar>
                          </>
                        )}
                        {callsChartMode === 'forecastFact' && (
                          <Line
                            yAxisId="right"
                            type="monotone"
                            dataKey="forecastMatchPercent"
                            stroke="#7c3aed"
                            strokeWidth={2}
                            dot={{ r: 3, strokeWidth: 2, fill: '#fff' }}
                            activeDot={{ r: 5, strokeWidth: 2, onClick: selectLossChartDay }}
                          />
                        )}
                        {callsChartMode === 'losses' && displayOptions.chartLossRate && (
                          <Line
                            yAxisId="right"
                            type="monotone"
                            dataKey="lossRate"
                            stroke="#e11d48"
                            strokeWidth={2}
                            dot={(props) => {
                              const isSelected = props.payload?.reportDate === selectedDate;
                              return (
                                <circle
                                  cx={props.cx}
                                  cy={props.cy}
                                  r={isSelected ? 5 : 3.5}
                                  fill={isSelected ? '#be123c' : '#fff'}
                                  stroke="#e11d48"
                                  strokeWidth={2}
                                  className="cursor-pointer"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    selectLossChartDay(props);
                                  }}
                                />
                              );
                            }}
                            activeDot={{ r: 6, strokeWidth: 2, onClick: selectLossChartDay }}
                          />
                        )}
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState title="Нет данных по звонкам" text="Загрузите ежедневные отчеты, чтобы увидеть динамику звонков." />
                )}
              </div>

              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  {callsChartMode === 'forecastFact' ? <PhoneCall size={16} /> : <ShieldAlert size={16} />}
                  Сводка периода
                </div>
                {callsChartMode === 'forecastFact' ? (
                  <>
                    <dl className="mt-4 space-y-3 text-sm">
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">Прогноз кол-во</dt><dd className="font-medium text-blue-700">{formatNumber(periodLossSummary.totalForecastCalls, 0)}</dd></div>
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">Факт кол-во</dt><dd className="font-medium text-emerald-700">{formatInt(periodLossSummary.totalReceived)}</dd></div>
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">Разница</dt><dd className={`font-medium ${periodLossSummary.callsDelta < 0 ? 'text-rose-700' : periodLossSummary.callsDelta > 0 ? 'text-emerald-700' : 'text-slate-900'}`}>{formatSignedNumber(periodLossSummary.callsDelta, 0)}</dd></div>
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">Выполнение</dt><dd className="font-medium text-slate-900">{periodLossSummary.totalForecastCalls > 0 ? formatPercent(periodLossSummary.callsCompletion, 0) : '-'}</dd></div>
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">Совпадение</dt><dd className="font-medium text-violet-700">{periodLossSummary.totalForecastCalls > 0 ? `${formatNumber(periodLossSummary.callsMatchPercent, 1)}%` : '-'}</dd></div>
                    </dl>
                    <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">
                      Режим сравнивает фактически поступившие звонки с прогнозом по выбранному периоду.
                    </div>
                  </>
                ) : (
                  <>
                    <dl className="mt-4 space-y-3 text-sm">
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">Поступило</dt><dd className="font-medium text-slate-900">{formatInt(periodLossSummary.totalReceived)}</dd></div>
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">Принято</dt><dd className="font-medium text-emerald-700">{formatInt(periodLossSummary.totalAccepted)}</dd></div>
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">Потеряно</dt><dd className="font-medium text-rose-700">{formatInt(periodLossSummary.totalLost)}</dd></div>
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">Доля потерь</dt><dd className="font-medium text-rose-700">{formatPercent(periodLossSummary.lossRate)}</dd></div>
                    </dl>
                    <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                      Высокая доля потерь в часы с большим входящим потоком обычно указывает на недобор факта или неверное распределение смен.
                    </div>
                  </>
                )}
              </div>
            </div>

            {selectedLossSummary ? (
              <>
              <div className="mt-5 rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <div className="text-sm font-semibold text-slate-950">Сводка выбранного дня</div>
                    <div className="text-sm text-slate-500">{formatDate(selectedLossSummary.reportDate)} · {selectedLossSummary.weekday}</div>
                  </div>
                  <span className="inline-flex h-9 w-fit items-center justify-center rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-600">
                    {selectedSummary ? 'Детализация ниже' : isDayLoading ? 'Загружаем часы' : 'Нет почасовой детализации'}
                  </span>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
                  {callsChartMode === 'forecastFact' ? (
                    <>
                      <div className="rounded-lg bg-white px-3 py-2"><div className="text-xs text-blue-700">Прогноз кол-во</div><b>{formatNumber(selectedLossSummary?.forecastCalls, 0)}</b></div>
                      <div className="rounded-lg bg-white px-3 py-2"><div className="text-xs text-emerald-700">Факт кол-во</div><b>{formatInt(selectedLossSummary?.received)}</b></div>
                      <div className="rounded-lg bg-white px-3 py-2"><div className="text-xs text-slate-500">Разница</div><b className={selectedLossSummary.callDelta < 0 ? 'text-rose-700' : selectedLossSummary.callDelta > 0 ? 'text-emerald-700' : ''}>{formatSignedNumber(selectedLossSummary?.callDelta, 0)}</b></div>
                      <div className="rounded-lg bg-white px-3 py-2"><div className="text-xs text-slate-500">Выполнение</div><b>{selectedLossSummary.forecastCalls > 0 ? formatPercent(selectedLossSummary?.callsCompletion, 0) : '-'}</b></div>
                      <div className="rounded-lg bg-white px-3 py-2">
                        <div className="text-xs text-violet-700">Совпадение</div>
                        <b>{selectedLossSummary.forecastCalls > 0 ? `${formatNumber(selectedLossSummary?.callsMatchPercent, 1)}%` : '-'}</b>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="rounded-lg bg-white px-3 py-2"><div className="text-xs text-slate-500">Поступило</div><b>{formatInt(selectedLossSummary?.received)}</b></div>
                      <div className="rounded-lg bg-white px-3 py-2"><div className="text-xs text-emerald-700">Принято</div><b>{formatInt(selectedLossSummary?.accepted)}</b></div>
                      <div className="rounded-lg bg-white px-3 py-2"><div className="text-xs text-rose-700">Потеряно</div><b>{formatInt(selectedLossSummary?.lost)}</b></div>
                      <div className="rounded-lg bg-white px-3 py-2"><div className="text-xs text-rose-700">Доля потерь</div><b>{formatPercent(selectedLossSummary?.lossRate)}</b></div>
                      <div className="rounded-lg bg-white px-3 py-2">
                        <div className="text-xs text-slate-500">Пиковый час потерь</div>
                        <b>{selectedLossSummary?.peakLossHour ? `${selectedLossSummary.peakLossHour.hour_label} · ${formatInt(selectedLossSummary.peakLossHour.lost_calls)}` : '-'}</b>
                      </div>
                    </>
                  )}
                </div>
              </div>

              {selectedSummary ? (
              <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
                <div className="rounded-lg border border-slate-200 bg-white p-3">
                  <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <PhoneCall size={16} />
                    {callsChartMode === 'forecastFact' ? 'Прогноз / факт по часам' : 'Принято / потеряно по часам'}: {formatDate(selectedSummary.report_date)}
                  </div>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      {callsChartMode === 'forecastFact' ? (
                        <ComposedChart data={dayForecastFactData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                          <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={2} />
                          <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                          <Tooltip content={<DayCallsTooltip />} />
                          <Area yAxisId="left" type="monotone" dataKey="forecastCalls" stroke="#2563eb" strokeWidth={2} fill="#bfdbfe" fillOpacity={0.75} />
                          <Area yAxisId="left" type="monotone" dataKey="factCalls" stroke="#16a34a" strokeWidth={2} fill="#22c55e" fillOpacity={0.38} />
                        </ComposedChart>
                      ) : (
                        <AreaChart data={dayAcceptedLostData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                          <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={2} />
                          <YAxis tick={{ fontSize: 11 }} />
                          <Tooltip formatter={(value, name) => [name === 'lossRate' ? `${formatNumber(value, 1)}%` : formatNumber(value, 0), name === 'accepted' ? 'Принято' : name === 'lost' ? 'Потеряно' : 'Доля потерь']} />
                          <Area type="monotone" dataKey="accepted" stackId="1" stroke="#16a34a" fill="#bbf7d0" />
                          <Area type="monotone" dataKey="lost" stackId="1" stroke="#e11d48" fill="#fecdd3" />
                        </AreaChart>
                      )}
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="rounded-lg border border-slate-200 bg-white p-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                    {callsChartMode === 'forecastFact' ? <BarChart3 size={16} /> : <AlertTriangle size={16} />}
                    {callsChartMode === 'forecastFact' ? 'Отклонения факт/прогноз' : 'Топ часов риска'}
                  </div>
                  <div className="mt-4 space-y-3">
                    {callsChartMode === 'forecastFact' ? (
                      dayCallDeltaHotspots.length ? (
                        dayCallDeltaHotspots.map((row) => (
                          <div key={row.hour} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                            <div className="flex items-center justify-between gap-3">
                              <div className="font-semibold text-slate-900">{row.hour}</div>
                              <div className={`rounded-md px-2 py-1 text-xs font-semibold ${row.delta < 0 ? 'bg-rose-100 text-rose-700' : row.delta > 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-700'}`}>{formatSignedNumber(row.delta, 0)}</div>
                            </div>
                            <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-slate-500">
                              <span>Прогноз: <b className="text-blue-700">{formatNumber(row.forecastCalls, 0)}</b></span>
                              <span>Факт: <b className="text-emerald-700">{formatInt(row.factCalls)}</b></span>
                              <span>Вып.: <b className="text-slate-800">{row.forecastCalls > 0 ? formatPercent(row.completion, 0) : '-'}</b></span>
                            </div>
                            <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
                              <div className={`h-full rounded-full ${row.delta < 0 ? 'bg-rose-500' : 'bg-emerald-500'}`} style={{ width: `${Math.min(100, row.absDelta)}%` }} />
                            </div>
                          </div>
                        ))
                      ) : (
                        <div className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500">По выбранному дню нет данных для сравнения прогноза и факта.</div>
                      )
                    ) : dayLossHotspots.length ? (
                      dayLossHotspots.map((row) => (
                        <div key={row.hour} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                          <div className="flex items-center justify-between gap-3">
                            <div className="font-semibold text-slate-900">{row.hour_label}</div>
                            <div className="rounded-md bg-rose-100 px-2 py-1 text-xs font-semibold text-rose-700">{formatPercent(row.no_answer_rate)}</div>
                          </div>
                          <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-slate-500">
                            <span>Вход: <b className="text-slate-800">{formatInt(row.received_calls)}</b></span>
                            <span>Потери: <b className="text-rose-700">{formatInt(row.lost_calls)}</b></span>
                            <span>Факт: <b className="text-slate-800">{formatNumber(row.actual_fte, 1)}</b></span>
                          </div>
                          <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
                            <div className="h-full rounded-full bg-rose-500" style={{ width: `${Math.min(100, Number(row.no_answer_rate || 0) * 100)}%` }} />
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500">По выбранному дню потерь нет.</div>
                    )}
                  </div>
                </div>
              </div>
              ) : (
                <div className="mt-5 rounded-lg border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
                  {isDayLoading ? 'Загружаем почасовую детализацию выбранной даты...' : 'Для выбранной даты нет почасовой детализации.'}
                </div>
              )}
              </>
            ) : null}
          </section>
        )}

        {activeDashboardView === 'schedule_planner' && (
          <ResourceSchedulePlanner
            apiRoot={apiRoot}
            buildHeaders={buildHeaders}
            selectedWeekStart={selectedForecastWeekStart}
            selectedPeriodEnd={selectedForecastPeriodEnd}
            onWeekStartChange={(value) => setSelectedForecastWeekStart(value)}
            onPeriodChange={(start, end) => {
              setSelectedForecastWeekStart(start);
              setSelectedForecastPeriodEnd(end);
              setSelectedForecastDate(start);
            }}
            weekPicker={(
              <WeekForecastPicker
                startValue={selectedForecastWeekStart}
                endValue={selectedForecastPeriodEnd}
                onRangeChange={(start, end) => {
                  setSelectedForecastWeekStart(start);
                  setSelectedForecastPeriodEnd(end);
                  setSelectedForecastDate(start);
                }}
                loadedDates={loadedReportDates}
                compact
              />
            )}
            notify={notify}
            onOpenShiftAuction={onOpenShiftAuction}
          />
        )}

        {(activeDashboardView === 'settings' || activeDashboardView === 'next_week') && (
        <div className={`grid gap-6 ${activeDashboardView === 'settings' ? 'xl:grid-cols-[320px_minmax(0,1fr)]' : 'xl:grid-cols-1'}`}>
          {activeDashboardView === 'settings' && (
          <aside className="space-y-4">
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
                <Settings size={16} />
                Настройки расчета
              </div>
              {settingsDraft ? (
                <div className="grid grid-cols-2 gap-3">
                  {[
                    ['answer_rate', 'Принято'],
                    ['occ', 'OCC'],
                    ['ur', 'UR'],
                    ['shrinkage_coeff', 'Усушка'],
                    ['weekly_hours_per_operator', 'Час/нед'],
                  ].map(([key, label]) => (
                    <label key={key} className="block">
                      <span className="text-xs font-medium text-slate-500">{label}</span>
                      <input
                        type="number"
                        step="0.01"
                        value={settingsDraft[key] ?? ''}
                        onChange={(event) => setSettingsDraft((current) => ({ ...current, [key]: event.target.value }))}
                        className={`${inputClass} mt-1 w-full`}
                      />
                    </label>
                  ))}
                  <label className="block">
                    <span className="text-xs font-medium text-slate-500">FTE</span>
                    <select value={settingsDraft.fte_rounding || 'none'} onChange={(event) => setSettingsDraft((current) => ({ ...current, fte_rounding: event.target.value }))} className={`${inputClass} mt-1 w-full`}>
                      <option value="none">без округл.</option>
                      <option value="ceil">вверх</option>
                      <option value="round">матем.</option>
                      <option value="floor">вниз</option>
                    </select>
                  </label>
                  <label className="block">
                    <span className="text-xs font-medium text-slate-500">Смены</span>
                    <select value={settingsDraft.shift_rounding || 'ceil'} onChange={(event) => setSettingsDraft((current) => ({ ...current, shift_rounding: event.target.value }))} className={`${inputClass} mt-1 w-full`}>
                      <option value="ceil">вверх</option>
                      <option value="none">без округл.</option>
                      <option value="round">матем.</option>
                      <option value="floor">вниз</option>
                    </select>
                  </label>
                  <div className="col-span-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-semibold text-slate-900">Направления для текущего FTE</div>
                        <p className="text-xs text-slate-500">Если ничего не выбрано, считается сумма ставок всех активных операторов.</p>
                      </div>
                      <button
                        type="button"
                        onClick={() => setSettingsDraft((current) => ({ ...current, selected_direction_ids: [] }))}
                        className="text-xs font-semibold text-blue-700 hover:text-blue-800"
                      >
                        Все
                      </button>
                    </div>
                    <div className="mt-3 max-h-44 space-y-2 overflow-y-auto pr-1">
                      {resourceDirections.length ? (
                        resourceDirections.map((direction) => (
                          <label key={direction.id} className="flex items-center justify-between gap-3 rounded-md bg-white px-3 py-2 text-sm">
                            <span className="font-medium text-slate-700">{direction.name}</span>
                            <input
                              type="checkbox"
                              checked={selectedDirectionSet.has(Number(direction.id))}
                              onChange={(event) => toggleResourceDirection(direction.id, event.target.checked)}
                              className="h-4 w-4 rounded border-slate-300 text-blue-600"
                            />
                          </label>
                        ))
                      ) : (
                        <div className="rounded-md border border-dashed border-slate-300 bg-white p-3 text-sm text-slate-500">Активные направления не найдены.</div>
                      )}
                    </div>
                  </div>
                  <button type="button" onClick={handleSaveSettings} className="col-span-2 inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 text-sm font-semibold text-white transition hover:bg-slate-800">
                    <Save size={16} />
                    Сохранить
                  </button>
                </div>
              ) : null}
            </div>
          </aside>
          )}

          <main className="space-y-6 min-w-0">
            {activeDashboardView === 'settings' && (
              <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div>
                    <h2 className="text-lg font-semibold text-slate-950">Параметры отображения</h2>
                    <p className="text-sm text-slate-500">Отключайте лишние показатели для быстрых ежедневных сценариев.</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDisplayOptions({ ...DEFAULT_DISPLAY_OPTIONS })}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                  >
                    <RefreshCw size={16} />
                    Сбросить
                  </button>
                </div>
                <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  {DISPLAY_GROUPS.map((group) => (
                    <div key={group.title} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <ListChecks size={16} />
                        {group.title}
                      </div>
                      <div className="space-y-2">
                        {group.items.map(([key, label]) => (
                          <ToggleSwitch
                            key={key}
                            checked={Boolean(displayOptions[key])}
                            label={label}
                            onChange={(value) => toggleDisplayOption(key, value)}
                          />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {activeDashboardView === 'next_week' && (
              <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div>
                    <h2 className="text-lg font-semibold text-slate-950">Прогноз FTE по выбранному периоду</h2>
                    <p className="text-sm text-slate-500">
                      Для каждого дня берутся две исторические даты: минус 21 и минус 14 дней. AHT считается отдельно по дню.
                    </p>
                  </div>
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <button
                      type="button"
                      onClick={handleRecalculate}
                      disabled={isRecalculating}
                      aria-busy={isRecalculating}
                      className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
                    >
                      <RefreshCw size={16} className={isRecalculating ? 'animate-spin motion-reduce:animate-none' : ''} aria-hidden="true" />
                      {isRecalculating ? 'Пересчитываем…' : 'Пересчитать'}
                    </button>
                  </div>
                </div>

                {(displayOptions.forecastKpiFteHours || displayOptions.forecastKpiOperators) ? (
                  <div className="mt-4 grid gap-3 grid-cols-1 xl:grid-cols-4 [&>*]:min-w-0">
                    {displayOptions.forecastKpiFteHours ? (
                      <div className="xl:col-span-2">
                        <StatCard
                          icon={TrendingUp}
                          label="FTE-часы периода"
                          value={formatNumber(nextWeekForecast.periodFteHours ?? nextWeekForecast.weeklyFteHours, 1)}
                          hint={`${formatInt(nextWeekForecast.periodDays || (nextWeekForecast.days || []).length)} дн. в периоде`}
                          tone="blue"
                          emphasis="primary"
                          accent
                        />
                      </div>
                    ) : null}
                    {displayOptions.forecastKpiOperators ? (
                      <OperatorSummaryCard
                        requiredFte={nextWeekForecast.operatorsWithShrinkage}
                        requiredWithUplift={nextWeekForecast.incidentAdjustedOperatorsWithShrinkage}
                        baseFte={nextWeekForecast.baseOperators}
                        availableFte={periodAvailableOperatorFte}
                        currentFte={nextWeekForecast.currentOperatorFte}
                        gap={periodAvailableOperatorFteGap}
                        availableCount={periodAvailableOperatorCount}
                        totalCount={periodOperatorCount}
                        partialCount={periodPartialOperatorCount}
                        unavailableCount={periodUnavailableOperatorCount}
                        onOpen={openOperatorDetails}
                      />
                    ) : null}
                  </div>
                ) : null}

                {(displayOptions.forecastKpiUplift || displayOptions.forecastKpiAht || displayOptions.forecastKpiAnswerRate || displayOptions.forecastKpiOccUr || displayOptions.forecastKpiShrinkage) ? (
                  <div className="mt-3 grid gap-2 grid-cols-2 md:grid-cols-3 xl:grid-cols-5 [&>*]:min-w-0">
                    {displayOptions.forecastKpiUplift ? (
                      <StatCard
                        icon={TrendingUp}
                        label="Возможный прирост"
                        value={`+${formatInt(nextWeekForecast.incidentUpliftCalls)} зв.`}
                        hint={`+${formatNumber(nextWeekForecast.incidentUpliftFteHours, 1)} FTE-ч · ${Number(nextWeekForecast.incidentUplift?.source_day_count || 0)}/6 дн.`}
                        tone="emerald"
                        emphasis="compact"
                      />
                    ) : null}
                    {displayOptions.forecastKpiAht ? (
                      <StatCard
                        icon={Clock3}
                        label="AHT периода"
                        value={formatSeconds(nextWeekForecast.periodAhtSeconds ?? nextWeekForecast.weeklyAhtSeconds)}
                        hint="Среднее по дням"
                        tone="blue"
                        emphasis="compact"
                      />
                    ) : null}
                    {displayOptions.forecastKpiAnswerRate ? (
                      <StatCard
                        icon={PhoneCall}
                        label="Принято"
                        value={formatPercent(nextWeekForecast.answerRate)}
                        hint="Коэф. периода"
                        tone="slate"
                        emphasis="compact"
                      />
                    ) : null}
                    {displayOptions.forecastKpiOccUr ? (
                      <StatCard
                        icon={Users}
                        label="OCC / UR"
                        value={`${formatPercent(nextWeekForecast.occ, 0)} / ${formatPercent(nextWeekForecast.ur, 0)}`}
                        hint={`Эфф. мин/час ${formatNumber(nextWeekForecast.effectiveMinutes, 1)}`}
                        tone="emerald"
                        emphasis="compact"
                      />
                    ) : null}
                    {displayOptions.forecastKpiShrinkage ? (
                      <StatCard
                        icon={ShieldAlert}
                        label="Усушка"
                        value={formatPercent(nextWeekForecast.shrinkage, 0)}
                        hint="Коэф. периода"
                        tone="amber"
                        emphasis="compact"
                      />
                    ) : null}
                  </div>
                ) : null}

                <div className="mt-5 grid gap-5 xl:grid-cols-[300px_minmax(0,1fr)]">
                  <aside className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <WeekForecastPicker
                      startValue={forecastPeriodStart}
                      endValue={forecastPeriodEnd}
                      onRangeChange={(start, end) => {
                        setSelectedForecastWeekStart(start);
                        setSelectedForecastPeriodEnd(end);
                        setSelectedForecastDate(start);
                      }}
                      loadedDates={loadedReportDates}
                    />
                    {!forecastPeriodComplete ? (
                      <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                        <div className="flex items-center gap-1 font-semibold">
                          <AlertTriangle size={13} />
                          Периоду не хватает истории
                        </div>
                        <div className="mt-1 text-slate-600">
                          Исторические периоды: {(forecastHistoryPeriods || []).map((period) => `${formatDate(period.start)}-${formatDate(period.end)}`).join(', ')}
                        </div>
                      </div>
                    ) : null}
                    <div className="mb-3 mt-5 flex items-center justify-between text-sm font-semibold text-slate-900">
                      <span>Выберите день</span>
                      <span className="text-[11px] font-medium text-slate-500 tabular-nums">{(nextWeekForecast.days || []).length} дн.</span>
                    </div>
                    <div className="space-y-2">
                      {(() => {
                        const tomorrowValue = addDaysIso(todayValue, 1);
                        const maxDailyCalls = Math.max(1, ...(nextWeekForecast.days || []).map((d) => Number(d.forecast_calls || 0)));
                        return (nextWeekForecast.days || []).map((profile) => {
                          const isActiveProfile = selectedForecastDay?.forecast_date === profile.forecast_date;
                          const isPast = profile.forecast_date && profile.forecast_date < todayValue;
                          const isToday = profile.forecast_date === todayValue;
                          const isTomorrow = profile.forecast_date === tomorrowValue;
                          const hasActual = profile.has_actual_report && profile.forecast_date <= todayValue;
                          const forecastFte = Number(profile.forecast_daily_fte || 0);
                          const actualFte = Number(profile.actual_report_fte || 0);
                          const factDelta = hasActual ? actualFte - forecastFte : null;
                          const hasUplift = Number(profile.incident_uplift_calls || 0) > 0.01;
                          const callShare = Math.min(100, (Number(profile.forecast_calls || 0) / maxDailyCalls) * 100);
                          const accentClass = profile.insufficient_history ? 'bg-amber-400' : 'bg-emerald-500';
                          const ariaLabel = `${profile.short} ${formatDate(profile.forecast_date)}, прогноз ${formatNumber(forecastFte, 2)} FTE${profile.insufficient_history ? ', истории не хватает' : ''}${hasActual ? `, факт ${formatNumber(actualFte, 2)} FTE` : ''}`;
                          return (
                            <button
                              key={profile.forecast_date || profile.weekday}
                              type="button"
                              aria-pressed={isActiveProfile}
                              aria-label={ariaLabel}
                              onClick={() => setSelectedForecastDate(profile.forecast_date)}
                              className={`group relative w-full overflow-hidden rounded-lg border p-3 pl-4 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 ${
                                isActiveProfile
                                  ? 'border-blue-400 bg-blue-50/60 shadow-sm'
                                  : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
                              }`}
                            >
                              <span className={`pointer-events-none absolute left-0 top-0 h-full w-1 ${accentClass}`} aria-hidden="true" />
                              <div className="flex items-start justify-between gap-2">
                                <div className="min-w-0">
                                  <div className="flex flex-wrap items-center gap-1.5">
                                    <span className="font-semibold text-slate-950">{profile.short}</span>
                                    {isToday ? (
                                      <span className="rounded-md bg-blue-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-800">Сегодня</span>
                                    ) : isTomorrow ? (
                                      <span className="rounded-md bg-sky-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-800">Завтра</span>
                                    ) : isPast ? (
                                      <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-600">Прошёл</span>
                                    ) : null}
                                  </div>
                                  <div className="text-xs text-slate-500 tabular-nums">{formatDate(profile.forecast_date)}</div>
                                </div>
                                <div className="text-right">
                                  <div className="text-base font-semibold tabular-nums text-slate-950">{formatNumber(forecastFte, 2)}</div>
                                  <div className="text-[10px] uppercase tracking-wide text-slate-500">FTE прогноз</div>
                                </div>
                              </div>

                              <div className="mt-2.5 flex items-center gap-2 text-xs">
                                <span className="inline-flex min-w-0 items-center gap-1 text-slate-600">
                                  <PhoneCall size={11} className="shrink-0 text-slate-400" aria-hidden="true" />
                                  <b className="text-slate-900 tabular-nums">{formatInt(profile.forecast_calls)}</b>
                                  <span className="text-slate-400">зв.</span>
                                </span>
                                <span
                                  className={`ml-auto inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold ${
                                    profile.insufficient_history
                                      ? 'bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200'
                                      : 'bg-emerald-50 text-emerald-800 ring-1 ring-inset ring-emerald-200'
                                  }`}
                                  title={profile.insufficient_history ? 'Для дня не хватает исторических точек' : 'Обе исторические точки в наличии'}
                                >
                                  {profile.insufficient_history
                                    ? <AlertTriangle size={11} aria-hidden="true" />
                                    : <CheckCircle2 size={11} aria-hidden="true" />}
                                  <span className="tabular-nums">{profile.history_count}/2</span>
                                </span>
                              </div>

                              <div className="mt-2 h-1 overflow-hidden rounded-full bg-slate-100" role="presentation" aria-hidden="true">
                                <div
                                  className="h-full rounded-full bg-blue-500/70 transition-[width] duration-300 motion-reduce:transition-none"
                                  style={{ width: `${callShare}%` }}
                                />
                              </div>

                              {hasUplift ? (
                                <div className="mt-2 flex items-center justify-between gap-2 rounded-md bg-emerald-50 px-2 py-1 text-[11px] text-emerald-800 ring-1 ring-inset ring-emerald-100">
                                  <span className="inline-flex items-center gap-1">
                                    <TrendingUp size={11} aria-hidden="true" />
                                    Возможный прирост
                                  </span>
                                  <span className="tabular-nums">
                                    +{formatInt(profile.incident_uplift_calls)} зв. · +{formatNumber(profile.incident_uplift_fte, 2)} FTE
                                  </span>
                                </div>
                              ) : null}

                              {hasActual ? (
                                <div className="mt-2 flex items-center justify-between gap-2 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px]">
                                  <span className="inline-flex items-center gap-1 text-slate-600">
                                    <CheckCircle2 size={11} className="text-emerald-600" aria-hidden="true" />
                                    Факт <b className="text-slate-900 tabular-nums">{formatNumber(actualFte, 2)}</b>
                                  </span>
                                  {factDelta !== null ? (
                                    <span
                                      className={`tabular-nums font-semibold ${
                                        Math.abs(factDelta) < 0.005
                                          ? 'text-slate-600'
                                          : factDelta < 0 ? 'text-rose-700' : 'text-emerald-700'
                                      }`}
                                      title="Факт − прогноз"
                                    >
                                      {Math.abs(factDelta) < 0.005
                                        ? '±0.00'
                                        : factDelta > 0
                                          ? `+${formatNumber(factDelta, 2)}`
                                          : formatNumber(factDelta, 2)}
                                    </span>
                                  ) : null}
                                </div>
                              ) : null}
                            </button>
                          );
                        });
                      })()}
                    </div>
                  </aside>

                  <div className="min-w-0 space-y-4">
                    {selectedForecastDay ? (
                      <>
                        <div className="rounded-lg border border-slate-200 bg-white p-4">
                          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                            <div>
                              <h3 className="text-base font-semibold text-slate-950">
                                Почасовой FTE: {selectedForecastDay.short} · {formatDate(selectedForecastDay.forecast_date)}
                              </h3>
                              <p className="text-sm text-slate-500">Разбивка использует AHT дня {formatSeconds(selectedForecastDay.forecast_aht_seconds)} и единые коэффициенты.</p>
                            </div>
                            <div className="flex flex-wrap gap-2">
                              <span className={`inline-flex w-fit items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold ${selectedForecastDay.insufficient_history ? 'bg-amber-50 text-amber-700' : 'bg-emerald-50 text-emerald-700'}`}>
                                {selectedForecastDay.insufficient_history ? <AlertTriangle size={13} /> : <CheckCircle2 size={13} />}
                                История {selectedForecastDay.history_count}/2
                              </span>
                              {showForecastActualLoad ? (
                                <span className={`inline-flex w-fit items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold ${selectedForecastHasActualLoad ? 'bg-blue-50 text-blue-700' : 'bg-slate-100 text-slate-500'}`}>
                                  {selectedForecastHasActualLoad ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
                                  {selectedForecastHasActualLoad ? 'Факт отчета загружен' : 'Факта отчета нет'}
                                </span>
                              ) : null}
                            </div>
                          </div>

                          {showForecastActualLoad && selectedForecastHasActualLoad ? (
                            <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                              <div className="rounded-lg bg-slate-50 px-3 py-2">
                                <div className="text-xs text-slate-500">Звонки</div>
                                <div className="mt-1 grid grid-cols-2 gap-2">
                                  <div><span className="block text-[11px] text-blue-700">Прогноз</span><b>{formatInt(selectedForecastDay.forecast_calls)}</b></div>
                                  <div><span className="block text-[11px] text-emerald-700">Факт</span><b>{formatInt(selectedForecastDay.actual_received_calls)}</b></div>
                                </div>
                                <div className="mt-1 text-[11px] font-semibold text-emerald-700">+{formatInt(selectedForecastDay.incident_uplift_calls)} возможный прирост · вес {formatPercent(selectedForecastDay.incident_future_weight ?? 1, 0)}</div>
                              </div>
                              <div className="rounded-lg bg-slate-50 px-3 py-2">
                                <div className="text-xs text-slate-500">Минуты нагрузки</div>
                                <div className="mt-1 grid grid-cols-2 gap-2">
                                  <div><span className="block text-[11px] text-blue-700">Прогноз</span><b>{formatNumber(selectedForecastDay.forecast_workload_minutes, 1)}</b></div>
                                  <div><span className="block text-[11px] text-emerald-700">Факт</span><b>{formatNumber(selectedForecastDay.actual_workload_minutes, 1)}</b></div>
                                </div>
                              </div>
                              <div className="rounded-lg bg-slate-50 px-3 py-2">
                                <div className="text-xs text-slate-500">FTE</div>
                                <div className="mt-1 grid grid-cols-2 gap-2">
                                  <div><span className="block text-[11px] text-blue-700">Прогноз</span><b>{formatNumber(selectedForecastDay.forecast_daily_fte, 2)}</b></div>
                                  <div><span className="block text-[11px] text-emerald-700">Факт</span><b>{formatNumber(selectedForecastDay.actual_report_fte, 2)}</b></div>
                                </div>
                                <div className="mt-1 text-[11px] font-semibold text-emerald-700">с приростом {formatNumber(selectedForecastDay.incident_adjusted_daily_fte ?? selectedForecastDay.forecast_daily_fte, 2)}</div>
                              </div>
                              <div className="rounded-lg bg-slate-50 px-3 py-2">
                                <div className="text-xs text-slate-500">Пиковый час</div>
                                <div className="mt-1 grid grid-cols-2 gap-2">
                                  <div><span className="block text-[11px] text-blue-700">Прогноз</span><b>{selectedForecastPeakHours[0] ? `${String(selectedForecastPeakHours[0].hour).padStart(2, '0')}:00` : '-'}</b></div>
                                  <div><span className="block text-[11px] text-emerald-700">Факт</span><b>{selectedActualPeakHours[0] ? `${String(selectedActualPeakHours[0].hour).padStart(2, '0')}:00` : '-'}</b></div>
                                </div>
                              </div>
                            </div>
                          ) : (
                            <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                              <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Звонки</div><b>{formatInt(selectedForecastDay.forecast_calls)}</b><div className="mt-1 text-[11px] font-semibold text-emerald-700">+{formatInt(selectedForecastDay.incident_uplift_calls)} возможный прирост · вес {formatPercent(selectedForecastDay.incident_future_weight ?? 1, 0)}</div></div>
                              <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Минут нагрузки</div><b>{formatNumber(selectedForecastDay.forecast_workload_minutes, 1)}</b></div>
                              <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">FTE дня</div><b>{formatNumber(selectedForecastDay.forecast_daily_fte, 2)}</b><div className="mt-1 text-[11px] font-semibold text-emerald-700">с приростом {formatNumber(selectedForecastDay.incident_adjusted_daily_fte ?? selectedForecastDay.forecast_daily_fte, 2)}</div></div>
                              <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Пиковый час</div><b>{selectedForecastPeakHours[0] ? `${String(selectedForecastPeakHours[0].hour).padStart(2, '0')}:00` : '-'}</b></div>
                            </div>
                          )}

                          <div className="mt-5 h-72">
                            <ResponsiveContainer width="100%" height="100%">
                              <ComposedChart
                                data={selectedForecastHourlyData}
                                margin={{ top: 10, right: 18, left: 0, bottom: 0 }}
                                onMouseMove={(state) => hoverForecastSlice(state?.activeLabel)}
                                onMouseLeave={() => setHoveredForecastHour(null)}
                                onClick={(state) => togglePinnedForecastSlice(state?.activeLabel)}
                              >
                                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                                <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={2} />
                                <YAxis
                                  yAxisId="left"
                                  tick={{ fontSize: 11 }}
                                  label={{ value: 'звонки / мин', angle: -90, position: 'insideLeft', offset: 12, style: { fontSize: 11, fill: '#64748b' } }}
                                />
                                <YAxis
                                  yAxisId="right"
                                  orientation="right"
                                  tick={{ fontSize: 11 }}
                                  label={{ value: 'FTE', angle: 90, position: 'insideRight', offset: 8, style: { fontSize: 11, fill: '#64748b' } }}
                                />
                                <Tooltip content={<ForecastHourlyTooltip />} />
                                {activeForecastHourLabel ? (
                                  <ReferenceLine yAxisId="left" x={activeForecastHourLabel} stroke={pinnedForecastHour !== null ? '#0f172a' : '#64748b'} strokeDasharray="4 4" />
                                ) : null}
                                {displayOptions.forecastChartCalls ? (
                                  <Bar yAxisId="left" dataKey="calls" stackId="calls" fill="#bfdbfe" radius={incidentUpliftAvailable && displayOptions.forecastChartUplift ? [0, 0, 0, 0] : [4, 4, 0, 0]}>
                                    {selectedForecastHourlyData.map((item) => (
                                      <Cell
                                        key={item.hour}
                                        fill={activeForecastHour !== null && Number(item.hourNumber) === Number(activeForecastHour) ? '#60a5fa' : '#bfdbfe'}
                                      />
                                    ))}
                                  </Bar>
                                ) : null}
                                {incidentUpliftAvailable && displayOptions.forecastChartUplift ? (
                                  <Bar yAxisId="left" dataKey="upliftCalls" stackId="calls" fill="#bbf7d0" radius={[4, 4, 0, 0]}>
                                    {selectedForecastHourlyData.map((item) => (
                                      <Cell
                                        key={`uplift-${item.hour}`}
                                        fill={activeForecastHour !== null && Number(item.hourNumber) === Number(activeForecastHour) ? '#34d399' : '#bbf7d0'}
                                      />
                                    ))}
                                  </Bar>
                                ) : null}
                                {displayOptions.forecastChartWorkload ? (
                                  <Line yAxisId="left" type="monotone" dataKey="workload" stroke="#3b82f6" strokeWidth={2} dot={false} activeDot={{ r: 5 }} />
                                ) : null}
                                {displayOptions.forecastChartFte ? (
                                  <Line yAxisId="right" type="monotone" dataKey="fte" stroke="#2563eb" strokeWidth={2} dot={false} activeDot={{ r: 5 }} />
                                ) : null}
                                {incidentUpliftAvailable && displayOptions.forecastChartAdjustedFte ? (
                                  <Line yAxisId="right" type="monotone" dataKey="adjustedFte" stroke="#059669" strokeWidth={2} strokeDasharray="4 3" dot={false} activeDot={{ r: 5 }} />
                                ) : null}
                                {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastChartActualWorkload ? (
                                  <Line yAxisId="left" type="monotone" dataKey="actualWorkload" stroke="#10b981" strokeWidth={2} dot={false} activeDot={{ r: 5 }} />
                                ) : null}
                                {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastChartActualFte ? (
                                  <Line yAxisId="right" type="monotone" dataKey="actualFte" stroke="#059669" strokeWidth={2} strokeDasharray="5 4" dot={false} activeDot={{ r: 5 }} />
                                ) : null}
                              </ComposedChart>
                            </ResponsiveContainer>
                          </div>
                          <ForecastChartLegend
                            displayOptions={displayOptions}
                            toggleDisplayOption={toggleDisplayOption}
                            incidentUpliftAvailable={incidentUpliftAvailable}
                            showActualLoad={showForecastActualLoad && selectedForecastHasActualLoad}
                          />
                          {showForecastActualLoad && !selectedForecastHasActualLoad ? (
                            <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                              Для выбранного дня нет загруженного отчета или день еще не прошел, поэтому факт нагрузки не отображается.
                            </div>
                          ) : null}
                        </div>

                        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
                          <div className="overflow-x-auto rounded-lg border border-slate-200">
                            <table className="w-full divide-y divide-slate-200 text-sm tabular-nums">
                              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                                <tr>
                                  <th className="px-3 py-3 text-left">Час</th>
                                  <th className="px-3 py-3 text-right">
                                    <span className="inline-flex items-center justify-end gap-1.5">
                                      <span className="inline-block h-2 w-2 rounded-full bg-blue-400" />
                                      Звонки
                                    </span>
                                  </th>
                                  {incidentUpliftAvailable && displayOptions.forecastTableUplift ? (
                                    <th className="px-3 py-3 text-right">
                                      <span className="inline-flex items-center justify-end gap-1.5">
                                        <span className="inline-block h-2 w-2 rounded-full bg-emerald-400" />
                                        Прирост
                                      </span>
                                    </th>
                                  ) : null}
                                  {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastTableActualCalls ? (
                                    <th className="px-3 py-3 text-right">
                                      <span className="inline-flex items-center justify-end gap-1.5">
                                        <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />
                                        Факт звонков
                                      </span>
                                    </th>
                                  ) : null}
                                  {displayOptions.forecastTableAht ? (
                                    <th className="px-3 py-3 text-right">AHT дня</th>
                                  ) : null}
                                  {displayOptions.forecastTableWorkload ? (
                                    <th className="px-3 py-3 text-right">Минут нагрузки</th>
                                  ) : null}
                                  {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastTableActualWorkload ? (
                                    <th className="px-3 py-3 text-right">
                                      <span className="inline-flex items-center justify-end gap-1.5">
                                        <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />
                                        Факт нагрузки
                                      </span>
                                    </th>
                                  ) : null}
                                  <th className="px-3 py-3 text-right">
                                    <span className="inline-flex items-center justify-end gap-1.5">
                                      <span className="inline-block h-2 w-2 rounded-full bg-blue-600" />
                                      FTE
                                    </span>
                                  </th>
                                  {incidentUpliftAvailable && displayOptions.forecastTableAdjustedFte ? (
                                    <th className="px-3 py-3 text-right">
                                      <span className="inline-flex items-center justify-end gap-1.5">
                                        <span className="inline-block h-2 w-2 rounded-full bg-emerald-600" />
                                        FTE с приростом
                                      </span>
                                    </th>
                                  ) : null}
                                  {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastTableActualFte ? (
                                    <th className="px-3 py-3 text-right">
                                      <span className="inline-flex items-center justify-end gap-1.5">
                                        <span className="inline-block h-2 w-2 rounded-full bg-emerald-600" />
                                        Факт FTE
                                      </span>
                                    </th>
                                  ) : null}
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-100 bg-white">
                                {(selectedForecastDay.hourly_forecast || []).map((row) => {
                                  const rowIsActive = activeForecastHour !== null && Number(row.hour) === Number(activeForecastHour);
                                  const rowIsPinned = pinnedForecastHour !== null && Number(row.hour) === Number(pinnedForecastHour);
                                  return (
                                    <tr
                                      key={row.hour}
                                      onMouseEnter={() => setHoveredForecastHour(Number(row.hour))}
                                      onMouseLeave={() => setHoveredForecastHour(null)}
                                      onClick={() => togglePinnedForecastSlice(Number(row.hour))}
                                      className={`cursor-pointer transition ${
                                        rowIsPinned
                                          ? 'bg-slate-100 ring-1 ring-inset ring-slate-300'
                                          : rowIsActive
                                            ? 'bg-blue-50/80'
                                            : 'hover:bg-slate-50/60'
                                      }`}
                                    >
                                      <td className="px-3 py-2 font-medium text-slate-900">
                                        <span className={rowIsPinned ? 'inline-flex items-center rounded-md bg-blue-100 px-2 py-1 text-blue-900 ring-1 ring-blue-300' : ''}>{String(row.hour).padStart(2, '0')}:00</span>
                                      </td>
                                      <td className="px-3 py-2 text-right">
                                        <span
                                          title={formatSourceCallsTooltip(row.source_calls)}
                                          className={`inline-flex cursor-help items-center justify-end rounded-md border px-2 py-1 font-medium transition ${
                                            rowIsActive ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-transparent text-slate-900 hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700'
                                          }`}
                                        >
                                          {formatNumber(row.forecast_calls, 1)}
                                        </span>
                                      </td>
                                      {incidentUpliftAvailable && displayOptions.forecastTableUplift ? (
                                        <td className="px-3 py-2 text-right">
                                          <span
                                            title={formatIncidentUpliftTooltip(row)}
                                            className={`inline-flex cursor-help items-center justify-end rounded-md border px-2 py-1 font-medium text-emerald-700 transition ${
                                              rowIsActive ? 'border-emerald-200 bg-emerald-50' : 'border-transparent hover:border-emerald-200 hover:bg-emerald-50'
                                            }`}
                                          >
                                            +{formatNumber(row.incident_uplift_calls, 1)}
                                          </span>
                                        </td>
                                      ) : null}
                                      {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastTableActualCalls ? (
                                        <td className="px-3 py-2 text-right">
                                          <span
                                            className={`inline-flex items-center justify-end rounded-md border px-2 py-1 font-medium text-emerald-700 transition ${
                                              rowIsActive ? 'border-emerald-200 bg-emerald-50' : 'border-transparent'
                                            }`}
                                          >
                                            {row.has_actual_report ? formatInt(row.actual_received_calls) : '-'}
                                          </span>
                                        </td>
                                      ) : null}
                                      {displayOptions.forecastTableAht ? (
                                        <td className="px-3 py-2 text-right">
                                          <span
                                            title={formatAhtTooltip(row.forecast_aht_seconds)}
                                            className={`inline-flex cursor-help items-center justify-end rounded-md border px-2 py-1 transition ${
                                              rowIsActive ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-transparent hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700'
                                            }`}
                                          >
                                            {formatSeconds(row.forecast_aht_seconds)}
                                          </span>
                                        </td>
                                      ) : null}
                                      {displayOptions.forecastTableWorkload ? (
                                        <td className="px-3 py-2 text-right">
                                          <span
                                            title={formatWorkloadTooltip(row, nextWeekForecast.answerRate)}
                                            className={`inline-flex cursor-help items-center justify-end rounded-md border px-2 py-1 transition ${
                                              rowIsActive ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-transparent hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700'
                                            }`}
                                          >
                                            {formatNumber(row.forecast_workload_minutes, 1)}
                                          </span>
                                        </td>
                                      ) : null}
                                      {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastTableActualWorkload ? (
                                        <td className="px-3 py-2 text-right">
                                          <span
                                            title={formatActualLoadTooltip(row, nextWeekForecast.effectiveMinutes)}
                                            className={`inline-flex cursor-help items-center justify-end rounded-md border px-2 py-1 font-medium text-emerald-700 transition ${
                                              rowIsActive ? 'border-emerald-200 bg-emerald-50' : 'border-transparent hover:border-emerald-200 hover:bg-emerald-50'
                                            }`}
                                          >
                                            {row.has_actual_report ? formatNumber(row.actual_workload_minutes, 1) : '-'}
                                          </span>
                                        </td>
                                      ) : null}
                                      <td className="px-3 py-2 text-right font-semibold text-blue-700">{formatNumber(row.forecast_fte, 2)}</td>
                                      {incidentUpliftAvailable && displayOptions.forecastTableAdjustedFte ? (
                                        <td className="px-3 py-2 text-right font-semibold text-emerald-700">{formatNumber(row.incident_adjusted_fte ?? row.forecast_fte, 2)}</td>
                                      ) : null}
                                      {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastTableActualFte ? (
                                        <td className="px-3 py-2 text-right font-semibold text-emerald-700">
                                          {row.has_actual_report ? formatNumber(row.actual_report_fte, 2) : '-'}
                                        </td>
                                      ) : null}
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>

                          <div className="space-y-4">
                            <div className="rounded-lg border border-slate-200 bg-white p-4">
                              <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                                <TrendingUp size={16} aria-hidden="true" />
                                Пиковые часы прогноз
                              </div>
                              <div className="mt-4 space-y-3">
                                {(() => {
                                  const peakMaxForecast = Math.max(1e-6, ...selectedForecastPeakHours.map((r) => Number(r.forecast_fte || 0)));
                                  return selectedForecastPeakHours.map((row) => {
                                    const rowIsActive = activeForecastHour !== null && Number(row.hour) === Number(activeForecastHour);
                                    const rowIsPinned = pinnedForecastHour !== null && Number(row.hour) === Number(pinnedForecastHour);
                                    const barWidth = Math.min(100, Math.max(0, (Number(row.forecast_fte || 0) / peakMaxForecast) * 100));
                                    return (
                                      <button
                                        key={row.hour}
                                        type="button"
                                        aria-pressed={rowIsPinned}
                                        onMouseEnter={() => setHoveredForecastHour(Number(row.hour))}
                                        onMouseLeave={() => setHoveredForecastHour(null)}
                                        onClick={() => togglePinnedForecastSlice(Number(row.hour))}
                                        className={`w-full rounded-lg p-3 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 ${
                                          rowIsPinned
                                            ? 'bg-blue-50 ring-1 ring-inset ring-blue-300'
                                            : rowIsActive
                                              ? 'bg-blue-50'
                                              : 'bg-slate-50 hover:bg-blue-50'
                                        }`}
                                      >
                                        <div className="flex items-center justify-between">
                                          <span className="font-semibold text-slate-900 tabular-nums">{String(row.hour).padStart(2, '0')}:00</span>
                                          <span className="rounded-md bg-blue-100 px-2 py-1 text-xs font-semibold text-blue-800 tabular-nums">{formatNumber(row.forecast_fte, 2)} FTE</span>
                                        </div>
                                        <div className="mt-2 text-xs text-slate-500 tabular-nums">Звонки: {formatNumber(row.forecast_calls, 1)} · нагрузка: {formatNumber(row.forecast_workload_minutes, 1)} мин</div>
                                        <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-valuenow={Math.round(barWidth)} aria-valuemin={0} aria-valuemax={100}>
                                          <div className="h-full rounded-full bg-blue-600 transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${barWidth}%` }} />
                                        </div>
                                      </button>
                                    );
                                  });
                                })()}
                              </div>
                            </div>

                            {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastShowActualPeakHours ? (
                              <div className="rounded-lg border border-emerald-100 bg-white p-4">
                                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                                  <TrendingUp size={16} className="text-emerald-600" aria-hidden="true" />
                                  Пиковые часы факт
                                </div>
                                <div className="mt-4 space-y-3">
                                  {(() => {
                                    const peakMaxActual = Math.max(1e-6, ...selectedActualPeakHours.map((r) => Number(r.actual_report_fte || 0)));
                                    return selectedActualPeakHours.map((row) => {
                                      const rowIsActive = activeForecastHour !== null && Number(row.hour) === Number(activeForecastHour);
                                      const rowIsPinned = pinnedForecastHour !== null && Number(row.hour) === Number(pinnedForecastHour);
                                      const barWidth = Math.min(100, Math.max(0, (Number(row.actual_report_fte || 0) / peakMaxActual) * 100));
                                      return (
                                        <button
                                          key={row.hour}
                                          type="button"
                                          aria-pressed={rowIsPinned}
                                          onMouseEnter={() => setHoveredForecastHour(Number(row.hour))}
                                          onMouseLeave={() => setHoveredForecastHour(null)}
                                          onClick={() => togglePinnedForecastSlice(Number(row.hour))}
                                          className={`w-full rounded-lg p-3 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                                            rowIsPinned
                                              ? 'bg-emerald-50 ring-1 ring-inset ring-emerald-300'
                                              : rowIsActive
                                                ? 'bg-emerald-50'
                                                : 'bg-slate-50 hover:bg-emerald-50'
                                          }`}
                                        >
                                          <div className="flex items-center justify-between">
                                            <span className="font-semibold text-slate-900 tabular-nums">{String(row.hour).padStart(2, '0')}:00</span>
                                            <span className="rounded-md bg-emerald-100 px-2 py-1 text-xs font-semibold text-emerald-800 tabular-nums">{formatNumber(row.actual_report_fte, 2)} FTE</span>
                                          </div>
                                          <div className="mt-2 text-xs text-slate-500 tabular-nums">Звонки: {formatInt(row.actual_received_calls)} · нагрузка: {formatNumber(row.actual_workload_minutes, 1)} мин</div>
                                          <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-valuenow={Math.round(barWidth)} aria-valuemin={0} aria-valuemax={100}>
                                            <div className="h-full rounded-full bg-emerald-600 transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${barWidth}%` }} />
                                          </div>
                                        </button>
                                      );
                                    });
                                  })()}
                                </div>
                              </div>
                            ) : null}
                          </div>
                        </div>
                      </>
                    ) : (
                      <EmptyState
                        title="Нет прогноза"
                        text="Загрузите исторические отчеты, чтобы построить прогноз выбранного периода."
                        action={(
                          <button
                            type="button"
                            onClick={() => setIsUploadModalOpen(true)}
                            className="inline-flex h-10 items-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-300"
                          >
                            <UploadCloud size={16} aria-hidden="true" />
                            Загрузить отчёт
                          </button>
                        )}
                      />
                    )}
                  </div>
                </div>
              </section>
            )}

            {activeDashboardView === 'next_week' ? (
              <ForecastDisplayPanel
                isOpen={isForecastPanelOpen}
                onToggleOpen={() => setIsForecastPanelOpen((current) => !current)}
                displayOptions={displayOptions}
                toggleDisplayOption={toggleDisplayOption}
                incidentUpliftAvailable={incidentUpliftAvailable}
                showActualLoad={showForecastActualLoad && selectedForecastHasActualLoad}
                forecastActualLoadAvailable={forecastActualLoadAvailable}
              />
            ) : null}

          </main>
        </div>
        )}
      </div>
    </div>
  );
};

export default ResourceFteView;
