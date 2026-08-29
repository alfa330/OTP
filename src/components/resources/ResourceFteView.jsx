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
  FileDown,
  FileUp,
  Gavel,
  Gauge,
  LayoutDashboard,
  ListChecks,
  MessageSquare,
  Receipt,
  RefreshCw,
  Save,
  Settings,
  SlidersHorizontal,
  PhoneCall,
  PhoneMissed,
  ShieldAlert,
  Target,
  Timer,
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

const formatDurationHms = (totalSeconds) => {
  const seconds = Math.max(0, Math.round(Number(totalSeconds || 0)));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return `${hours}:${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
};

const timeStringToMinutes = (value) => {
  const [hours, minutes] = String(value || '').split(':').map(Number);
  return (Number.isFinite(hours) ? hours : 0) * 60 + (Number.isFinite(minutes) ? minutes : 0);
};

const safeRatio = (numerator, denominator) => (Number(denominator) > 0 ? Number(numerator || 0) / Number(denominator) : null);

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

// Ключ вкладки биллинга общий у обоих направлений: на него завязан внешний
// переход initialDashboardView, и переименовать его в одном направлении нельзя.
const BILLING_VIEW_KEY = 'oktell_billing';

const VIEW_TABS = [
  { key: 'overview', label: 'Обзор', icon: LayoutDashboard },
  { key: 'next_week', label: 'Прогнозы', icon: TrendingUp },
  { key: 'schedule_planner', label: 'Графики', icon: CalendarDays },
  { key: 'losses', label: 'Звонки', icon: PhoneCall },
  // Ту же вкладку показывает чат (VIEW_TABS_CHAT, подпись «Биллинг»). Ключ у них
  // один: сокращать его до key: 'billing' в одном из направлений нельзя — тогда
  // внешний переход initialDashboardView привёл бы в пустоту.
  { key: 'oktell_billing', label: 'Биллинг Oktell', icon: Receipt },
  { key: 'settings', label: 'Настройки', icon: SlidersHorizontal },
];

// «Биллинг Oktell»: подписи таксопарков как в договорах (значение = код в Call_Systems_hst.taxi_park)
const BILLING_PARK_LABELS = {
  Dongelek: 'Eki Dongelek',
  Jana: 'Jana такси',
  Taxi24: 'Такси 24 (Нур)',
  Tenge_taxi: 'Тенге Такси',
  Halyk: 'Халык',
  Regions: 'Регионы',
  iTaxiVip: 'iTaxi VIP',
  'Ноль Такси': 'Ноль такси',
  'Бизнес партнер': 'Бизнес Партнер',
};

const billingParkLabel = (park) => BILLING_PARK_LABELS[park] || park;

const BILLING_TIME_PRESETS = [
  { label: 'Весь день', from: '00:00', to: '23:59' },
  { label: 'Утро', from: '06:00', to: '12:00' },
  { label: 'День', from: '08:00', to: '20:00' },
  { label: 'Вечер', from: '18:00', to: '23:59' },
];

const billingDayLabel = (iso) => {
  const date = parseIsoDate(iso);
  if (!date) return iso;
  const weekday = new Intl.DateTimeFormat('ru-RU', { weekday: 'long' }).format(date);
  return `${formatDate(iso)} · ${weekday}`;
};

const billingArClass = (ratio) => {
  if (ratio === null) return 'text-slate-400';
  if (ratio <= 0.05) return 'text-emerald-600';
  if (ratio <= 0.1) return 'text-amber-600';
  return 'text-rose-600';
};

const billingSlClass = (ratio) => {
  if (ratio === null) return 'text-slate-400';
  if (ratio >= 0.8) return 'text-emerald-600';
  if (ratio >= 0.6) return 'text-amber-600';
  return 'text-rose-600';
};

const BILLING_MODES = [
  { key: 'park', label: 'Таксопарки' },
  { key: 'line', label: 'Номера' },
  { key: 'operator', label: 'Операторы' },
  { key: 'detail', label: 'Детализация' },
];

const BILLING_DETAIL_PAGE_SIZE = 25;

// Подписи SIP-линий из договоров, ключ = последние 10 цифр набранного номера
const BILLING_LINE_LABELS = {
  7470951111: 'Amanat',
  7470939729: 'Eki Dongelek',
  7005556100: 'Global',
  7470540094: 'iPartner',
  7075050880: 'iTaxi',
  7085872762: 'iTaxi 2',
  7001222322: 'Jana такси',
  7078544502: 'Jana такси 2',
  7470958988: 'Qazaq',
  7007442288: 'Бизнес Партнер',
  7470942010: 'Бизнес Партнер 2',
  7771442288: 'Бизнес Партнер Фин',
  7470947777: 'Департамент',
  7005554222: 'Стабильный',
  7074777639: 'Такси 24 (Нур)',
  7004568543: 'Тенге Такси',
  7001587070: 'Халык',
  7072147584: 'Регионы',
  7003330402: 'Честный',
  7001110702: 'Честный 1',
  7001110200: 'Честный 2',
  7009214222: 'Ноль такси',
  7082675487: 'Ноль такси2',
  7001551198: 'СТ 1',
  7078931501: 'СТ 2',
  7080881541: 'Wolt',
  7003838240: 'iTaxi VIP',
};

const billingLineLabel = (line) => {
  const digits = String(line || '').replace(/\D/g, '').slice(-10);
  return BILLING_LINE_LABELS[digits] || '';
};

const billingLineDisplayNumber = (line) => {
  const digits = String(line || '').replace(/\D/g, '').slice(-10);
  return digits ? `8${digits}` : '';
};

const billingDetailPhoneDisplayNumber = (value) => {
  const raw = String(value ?? '').trim();
  if (!raw) return '—';
  const digits = raw.replace(/\D/g, '');
  return digits.length >= 10 ? `8${digits.slice(-10)}` : raw;
};

const billingOccurredAtLabel = (value) => {
  const raw = String(value ?? '').trim();
  if (!raw) return '—';
  const parsed = new Date(raw.includes('T') ? raw : raw.replace(' ', 'T'));
  if (Number.isNaN(parsed.getTime())) return raw;
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(parsed);
};

const billingCallFlagLabel = (value) => (value === true || Number(value) > 0 ? '1' : '—');

const billingTalkDurationLabel = (value) => (Number(value) > 0 ? formatDurationHms(value) : '—');

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

// Ключ и набор приходят из конфигурации направления: у линии в её ключе лежат
// показатели, которых в чате нет вовсе, и на общем ключе они подмешались бы
// мёртвыми тумблерами.
const loadDisplayOptions = (storageKey = DISPLAY_PREFERENCES_STORAGE_KEY, defaults = DEFAULT_DISPLAY_OPTIONS) => {
  if (typeof window === 'undefined') return { ...defaults };
  try {
    const parsed = JSON.parse(window.localStorage.getItem(storageKey) || '{}');
    return { ...defaults, ...(parsed && typeof parsed === 'object' ? parsed : {}) };
  } catch {
    return { ...defaults };
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
  label = 'Операторы',
  fteWord = 'FTE',
  excludedCount = 0,
  excludedFte = 0,
}) => {
  const requiredNumber = Number(requiredFte || 0);
  const requiredWithUpliftNumber = Number(requiredWithUplift ?? requiredFte ?? 0);
  const hasUpliftRequirement = Math.abs(requiredWithUpliftNumber - requiredNumber) > 0.005;
  const upliftGap = Number(availableFte || 0) - requiredWithUpliftNumber;
  const isDeficit = Number(hasUpliftRequirement ? upliftGap : gap || 0) < 0;
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`rounded-xl border bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-blue-100 xl:col-span-2 ${
        isDeficit ? 'border-rose-200' : 'border-emerald-200'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
          <div className={`mt-2 grid gap-3 ${hasUpliftRequirement ? 'grid-cols-3' : 'grid-cols-2'}`}>
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Нужно</div>
              <div className="text-xl font-semibold text-slate-950 sm:text-2xl tabular-nums">{formatNumber(requiredFte, 2)}</div>
            </div>
            {hasUpliftRequirement ? (
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-wide text-emerald-600">С приростом</div>
                <div className="text-xl font-semibold text-emerald-700 sm:text-2xl tabular-nums">{formatNumber(requiredWithUpliftNumber, 2)}</div>
              </div>
            ) : null}
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Доступно</div>
              <div className={`text-xl font-semibold sm:text-2xl tabular-nums ${isDeficit ? 'text-rose-700' : 'text-emerald-700'}`}>
                {formatNumber(availableFte, 2)}
              </div>
            </div>
          </div>
        </div>
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ring-1 ${isDeficit ? 'bg-rose-50 text-rose-700 ring-rose-100' : 'bg-emerald-50 text-emerald-700 ring-emerald-100'}`}>
          <Users size={18} aria-hidden="true" />
        </div>
      </div>
      <div className={`mt-3 grid gap-2 text-xs text-slate-600 ${hasUpliftRequirement ? 'sm:grid-cols-3' : 'sm:grid-cols-2'}`}>
        <div className="rounded-lg bg-slate-50 px-3 py-2">
          <span className="block text-slate-500">Разница</span>
          <b className={`tabular-nums ${Number(gap || 0) < 0 ? 'text-rose-700' : 'text-emerald-700'}`}>{formatSignedNumber(gap, 2)} {fteWord}</b>
        </div>
        {hasUpliftRequirement ? (
          <div className="rounded-lg bg-emerald-50 px-3 py-2">
            <span className="block text-emerald-700">Разница с приростом</span>
            <b className={`tabular-nums ${upliftGap < 0 ? 'text-rose-700' : 'text-emerald-700'}`}>{formatSignedNumber(upliftGap, 2)} {fteWord}</b>
          </div>
        ) : null}
        <div className="rounded-lg bg-slate-50 px-3 py-2">
          <span className="block text-slate-500">Сотрудники</span>
          <b className="text-slate-900 tabular-nums">{formatInt(availableCount)} / {formatInt(totalCount)}</b>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500 tabular-nums">
        <span>Без усушки: {formatNumber(baseFte, 2)}</span>
        <span>Текущий {fteWord}: {formatNumber(currentFte, 2)}</span>
        <span>Часть периода: {formatInt(partialCount)}</span>
        <span>Не работают: {formatInt(unavailableCount)}</span>
      </div>
      {Number(excludedCount || 0) > 0 ? (
        <div className="mt-2 text-xs text-amber-700 tabular-nums">
          Вне ставок направления: {formatInt(excludedCount)} чел. ({formatSignedNumber(-Math.abs(Number(excludedFte || 0)), 2)})
        </div>
      ) : null}
      <div className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-blue-700">
        <Eye size={14} aria-hidden="true" />
        Детали расчета
      </div>
    </button>
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

const ForecastChartLegend = ({
  displayOptions,
  toggleDisplayOption,
  incidentUpliftAvailable,
  showActualLoad,
  legendItems = FORECAST_CHART_LEGEND_ITEMS,
}) => {
  const items = legendItems.filter((item) => {
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
  panelGroups = FORECAST_PANEL_GROUPS,
}) => {
  const hiddenCount = panelGroups.reduce((acc, group) => (
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
            {panelGroups.map((group) => {
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

// ===========================================================================
// НАПРАВЛЕНИЯ РАЗДЕЛА: линия и чат — один компонент.
//
// Решение владельца 28.08.2026: «переиспользовать раздел линии, но под чат
// направление». Значит ни копии, ни параллельной реализации: всё, что
// различается, лежит здесь декларативно, а разметка спрашивает флаг из cfg,
// а не имя направления. При direction='line' конфигурация отдаёт ровно
// сегодняшние значения, поэтому каждое условие сворачивается в текущую ветку
// и линия не меняется ни на йоту.
// ===========================================================================

// >>> CHAT_DIRECTION_CONFIG_START
// Всё чатовое живёт между этими метками. Страж чатовых показателей читает
// только этот кусок файла: телефонные показатели линии в общем файле законны,
// а вот протечь в чат они не должны.

// Вкладок у чата шесть. Ключи общие с линией — иначе внешний переход на
// 'losses' привёл бы в пустоту, а initialDashboardView перестал бы работать.
const VIEW_TABS_CHAT = [
  { key: 'overview', label: 'Обзор', icon: LayoutDashboard },
  { key: 'next_week', label: 'Прогнозы', icon: TrendingUp },
  { key: 'schedule_planner', label: 'Графики', icon: CalendarDays },
  { key: 'losses', label: 'Чаты', icon: MessageSquare },
  // Биллинг. Ключ вкладки приходит константой BILLING_VIEW_KEY: он общий с
  // линией, и свой короткий key: 'billing' развёл бы направления, а на общий
  // ключ завязан внешний переход initialDashboardView. Подпись при этом своя —
  // имени телефонии в чате не место, здесь считаются обращения.
  { key: BILLING_VIEW_KEY, label: 'Биллинг', icon: Receipt },
  { key: 'settings', label: 'Настройки', icon: SlidersHorizontal },
];

const CHAT_API_PREFIX = '/api/resource_fte/chat';

const CHAT_DISPLAY_PREFERENCES_STORAGE_KEY = 'otp_resource_chat_display_v1';

// В расчёт чата идут только ставки 1 и 0,75 — тот же набор, что CHAT_RATES на
// сервере. Плашка сверху обещает это прямым текстом, значит и карточка
// доступности обязана считать по нему же, иначе на экране два разных числа
// про один и тот же штат.
const CHAT_RATE_VALUES = [1, 0.75];

const isChatRate = (rate) => CHAT_RATE_VALUES.some((allowed) => Math.abs(Number(rate || 0) - allowed) < 0.001);

const CHAT_DISPLAY_GROUPS = [
  {
    title: 'Обзор · Карточки',
    items: [
      ['metricForecastChats', 'Прогноз чатов'],
      ['metricForecastFteHours', 'Чатнико-часы прогноза'],
      ['metricActualFteHours', 'Факт чатнико-часов'],
      ['metricFteDelta', 'Разница с прогнозом'],
      ['metricInTargetShare', 'Первый ответ в цель'],
      ['metricCoveredDays', 'Дней с чатами'],
    ],
  },
  {
    title: 'Обзор · Тренд',
    items: [
      ['trendChats', 'Чаты'],
      ['trendNeedFte', 'Потребность, чатнико-часы'],
      ['trendActualFte', 'Факт, чатнико-часы'],
    ],
  },
  {
    title: 'Прогнозы · KPI',
    items: [
      ['forecastKpiFteHours', 'Чатнико-часы периода'],
      ['forecastKpiOperators', 'Чатники'],
      ['forecastKpiCapacity', 'Ёмкость'],
      ['forecastKpiTarget', 'Цель ответа'],
      ['forecastKpiShrinkage', 'Усушка'],
      ['forecastKpiUplift', 'Возможный прирост'],
    ],
  },
  {
    title: 'Прогнозы · График',
    items: [
      ['forecastChartChats', 'Чаты (бар)'],
      ['forecastChartUplift', 'Прирост чатов'],
      ['forecastChartFte', 'Потребность'],
      ['forecastChartAdjustedFte', 'Потребность с приростом'],
      ['forecastChartActualChats', 'Факт чатов'],
    ],
  },
  {
    title: 'Прогнозы · Таблица',
    items: [
      ['forecastTableUplift', 'Прирост'],
      ['forecastTableAdjustedFte', 'С приростом'],
      ['forecastTableActualChats', 'Факт чатов'],
      ['forecastTableActualHours', 'Факт чатнико-часов'],
      ['forecastTableFirstReply', 'Первый ответ'],
    ],
  },
];

const CHAT_DEFAULT_DISPLAY_OPTIONS = CHAT_DISPLAY_GROUPS.reduce((acc, group) => {
  group.items.forEach(([key]) => {
    // По умолчанию включено всё, кроме побочных колонок и редких карточек:
    // раздел должен открываться читаемым, а не полотном из двух десятков столбцов.
    acc[key] = ![
      'metricCoveredDays',
      'forecastKpiTarget',
      'forecastKpiShrinkage',
      'forecastTableUplift',
      'forecastTableAdjustedFte',
      'forecastTableActualHours',
      'forecastTableFirstReply',
    ].includes(key);
  });
  return acc;
}, {});

const CHAT_FORECAST_CHART_LEGEND_ITEMS = [
  { key: 'forecastChartChats', label: 'Чаты', color: '#60a5fa', shape: 'bar' },
  { key: 'forecastChartUplift', label: 'Прирост чатов', color: '#34d399', shape: 'bar', requires: 'uplift' },
  { key: 'forecastChartActualChats', label: 'Факт чатов', color: '#10b981', shape: 'bar', requires: 'actual' },
  { key: 'forecastChartFte', label: 'Нужно чатников', color: '#2563eb', shape: 'line' },
  { key: 'forecastChartAdjustedFte', label: 'Нужно чатников с приростом', color: '#059669', shape: 'dashed', requires: 'uplift' },
];

const CHAT_FORECAST_PANEL_GROUPS = [
  {
    title: 'KPI периода',
    items: [
      ['forecastKpiFteHours', 'Чатнико-часы периода'],
      ['forecastKpiOperators', 'Чатники'],
      ['forecastKpiCapacity', 'Ёмкость'],
      ['forecastKpiTarget', 'Цель ответа'],
      ['forecastKpiShrinkage', 'Усушка'],
      ['forecastKpiUplift', 'Возможный прирост'],
    ],
  },
  {
    title: 'Серии графика',
    items: [
      ['forecastChartChats', 'Чаты (бар)'],
      ['forecastChartUplift', 'Прирост чатов', 'uplift'],
      ['forecastChartActualChats', 'Факт чатов', 'actual'],
      ['forecastChartFte', 'Потребность'],
      ['forecastChartAdjustedFte', 'Потребность с приростом', 'uplift'],
    ],
  },
  {
    title: 'Колонки таблицы',
    items: [
      ['forecastTableUplift', 'Прирост', 'uplift'],
      ['forecastTableAdjustedFte', 'С приростом', 'uplift'],
      ['forecastTableActualChats', 'Факт чатов', 'actual'],
      ['forecastTableActualHours', 'Факт чатнико-часов', 'actual'],
      ['forecastTableFirstReply', 'Первый ответ', 'actual'],
    ],
  },
];

const CHAT_OVERVIEW_TREND_TOOLTIP_CONFIG = {
  calls: { group: 'Чаты', label: 'Факт', digits: 0, groupOrder: 1, itemOrder: 1 },
  actualFte: { group: 'Чатнико-часы', label: 'Факт', digits: 2, groupOrder: 2, itemOrder: 1 },
  forecastFte: { group: 'Чатнико-часы', label: 'Потребность', digits: 2, groupOrder: 2, itemOrder: 2 },
};

const CHAT_FTE_ROUNDING_LABELS = [
  ['half', 'до половины'],
  ['exact', 'без округления'],
  ['ceil', 'вверх'],
];

const describeChatTarget = (seconds) => {
  const total = Math.max(0, Math.round(Number(seconds || 0)));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (!minutes) return `${rest} сек`;
  return rest ? `${minutes} мин ${rest} сек` : `${minutes} мин`;
};

const formatReplySeconds = (value) => (
  value === null || value === undefined || value === '' ? '—' : `${formatNumber(value, 0)} с`
);

const chatCapacityPerHour = (payload) => {
  const settings = payload?.settings || {};
  const totals = payload?.forecast?.totals || {};
  return Math.max(0.01, Number(settings.capacity_per_hour || totals.capacity_per_hour || 17));
};

// Дерево рендера остаётся направлением-слепым: чатовый ответ приводится к той же
// форме, что отдаёт линейный бэкенд. Единицы при этом НЕ переименовываются в
// «звонки» — подписи берутся из cfg.unit, поэтому на экране чата везде «чаты»
// и «чатнико-часы». Всё, чего у чата нет, остаётся undefined, а рендер этих
// блоков закрыт флагами cfg — пустые нули просочиться не могут.
const adaptChatUpliftSources = (sources) => (Array.isArray(sources) ? sources : []).map((item) => ({
  ...item,
  calls: item.chats ?? item.calls,
  delta_calls: item.delta_chats ?? item.delta_calls,
  actual_calls: item.actual_chats ?? item.actual_calls,
  forecast_calls: item.forecast_chats ?? item.forecast_calls,
}));

const adaptChatForecastDay = (day) => ({
  ...day,
  forecast_calls: Number(day.forecast_chats || 0),
  forecast_daily_fte: Number(day.forecast_fte_hours || 0),
  incident_uplift_calls: Number(day.incident_uplift_chats || 0),
  incident_uplift_fte: Number(day.incident_uplift_fte_hours || 0),
  incident_adjusted_daily_fte: Number(day.forecast_fte_hours || 0) + Number(day.incident_uplift_fte_hours || 0),
  has_actual_report: Boolean(day.has_actual),
  actual_received_calls: Number(day.actual_chats || 0),
  actual_report_fte: Number(day.actual_online_hours || 0),
  hourly_forecast: (day.hourly_forecast || []).map((row) => ({
    ...row,
    forecast_calls: Number(row.forecast_chats || 0),
    incident_uplift_calls: Number(row.incident_uplift_chats || 0),
    incident_adjusted_calls: Number(row.incident_adjusted_chats ?? row.forecast_chats ?? 0),
    source_calls: adaptChatUpliftSources(row.source_chats || row.source_calls),
    incident_uplift_sources: adaptChatUpliftSources(row.incident_uplift_sources),
  })),
});

const adaptChatOverview = (payload) => {
  const data = payload || {};
  const forecast = data.forecast || {};
  const totals = forecast.totals || {};
  const settings = data.settings || {};
  const capacity = chatCapacityPerHour(data);
  const actualDays = data.actual?.days || [];
  const uplift = data.uplift || null;
  const shrink = Math.min(Math.max(Number(settings.shrinkage_coeff || 0.9), 0.01), 1);
  const perOperator = Number(totals.period_hours_per_operator || 0);
  // «Требуется с приростом» чат готовым не отдаёт — считаем по той же формуле,
  // что и сервер: часы с приростом ÷ норма ÷ усушка.
  const adjustedOperators = perOperator > 0
    ? (Number(totals.forecast_fte_hours || 0) + Number(totals.uplift_fte_hours || 0)) / perOperator / shrink
    : Number(totals.operators_with_shrinkage || 0);
  const sourceDates = Array.isArray(uplift?.source_dates) ? uplift.source_dates : [];
  return {
    ...data,
    settings,
    directions: [],
    loaded_report_dates: data.covered_days || [],
    history: actualDays.map((row) => ({
      report_date: row.date,
      weekday_short: row.short,
      total_received: Number(row.chats || 0),
      total_accepted: Number(row.answered || 0),
      forecast_calls_total: Number(row.forecast_chats || 0),
      // Потребность дня — та же формула часа, только сложенная: объём ÷ ёмкость.
      forecast_fte_total: Number(row.chats || 0) / capacity,
      actual_report_fte_total: Number(row.actual_online_hours || 0),
      in_target: Number(row.in_target || 0),
      in_target_share: Number(row.in_target_share || 0),
    })),
    next_week_forecast: {
      days: (forecast.days || []).map(adaptChatForecastDay),
      period_start: forecast.period_start || '',
      period_end: forecast.period_end || '',
      periodFteHours: Number(totals.forecast_fte_hours || 0),
      periodCalls: Number(totals.forecast_chats || 0),
      periodDays: Number(totals.period_days || 0),
      baseOperators: Number(totals.operators || 0),
      operatorsWithShrinkage: Number(totals.operators_with_shrinkage || 0),
      incidentAdjustedOperatorsWithShrinkage: adjustedOperators,
      currentOperatorFte: Number(totals.current_operator_fte || 0),
      operatorFteGap: Number(totals.operator_fte_gap || 0),
      shrinkage: Number(settings.shrinkage_coeff || 0),
      incidentUpliftCalls: Number(totals.uplift_chats || 0),
      incidentUpliftFteHours: Number(totals.uplift_fte_hours || 0),
      incidentUplift: { source_day_count: Number(uplift?.source_day_count || 0) },
      capacityPerHour: capacity,
      periodOperatorCount: Number(totals.head_count || 0),
      historyComplete: true,
      history_periods: [],
      base_week_starts: forecast.base_week_starts || [],
      skipped_base_weeks: forecast.skipped_base_weeks || [],
      operator_capacity: forecast.operator_capacity || {},
    },
    incident_uplift_dashboard: uplift ? {
      daily: (uplift.daily || []).map((row) => ({
        ...row,
        forecast_calls: Number(row.forecast_chats || 0),
        actual_calls: Number(row.actual_chats || 0),
        positive_delta_calls: Number(row.positive_delta_chats || 0),
        delta_calls: Number(row.actual_chats || 0) - Number(row.forecast_chats || 0),
        positive_hour_share: Number(row.source_hour_count || 0) > 0
          ? Number(row.positive_hour_count || 0) / Number(row.source_hour_count || 1)
          : 0,
      })),
      hourly: (uplift.hourly || []).map((row) => ({
        ...row,
        weighted_delta_calls: Number(row.weighted_delta_chats || 0),
      })),
      daily_summary: {
        held_day_count: Number(uplift.daily_summary?.held_day_count || 0),
        overload_day_count: Number(uplift.daily_summary?.overload_day_count || 0),
        source_day_count: Number(uplift.daily_summary?.source_day_count || 0),
        total_forecast_calls: Number(uplift.daily_summary?.total_forecast_chats || 0),
        total_actual_calls: Number(uplift.daily_summary?.total_actual_chats || 0),
        total_delta_calls: Number(uplift.daily_summary?.total_delta_chats || 0),
        total_positive_delta_calls: Number(uplift.daily_summary?.total_positive_delta_chats || 0),
      },
      // Даты источника приходят от свежей к старой.
      source_start: sourceDates.length ? sourceDates[sourceDates.length - 1] : '',
      source_end: sourceDates.length ? sourceDates[0] : '',
      window_start: uplift.forecast_window_start || '',
      window_end: uplift.forecast_window_end || '',
      projection: {},
    } : {},
  };
};

const adaptChatDay = (day) => (day ? {
  ...day,
  summary: { ...(day.summary || {}), report_date: day.date },
} : day);

// ── Биллинг чата ──────────────────────────────────────────────────────────
// Тот же экран, что у линии, но модель другая: среднего времени обработки в
// чате нет, поэтому колонок про длительность здесь тоже нет. Считаются объём
// обращений и скорость первого ответа.

const CHAT_BILLING_MODES = [
  { key: 'park', label: 'Таксопарки' },
  { key: 'operator', label: 'Чатники' },
  { key: 'detail', label: 'Детализация' },
];

// Порог «в цель» по умолчанию — тот же, что у ручки биллинга чата на сервере.
const CHAT_BILLING_SL_DEFAULT_SECONDS = 60;

// Средний первый ответ: сумма реакций делится на тех, кому ответили. Делить на
// все обращения нельзя — оставшиеся без ответа не имеют времени реакции вовсе.
const chatBillingReplyLabel = (item) => {
  const seconds = safeRatio(item?.first_reply_seconds, item?.answered);
  return seconds === null ? '—' : formatDurationHms(seconds);
};

// Обе доли в чате «чем больше, тем лучше», поэтому и AR красится шкалой SL:
// у линии AR — доля потерянных, у чата — доля отвеченных.
const chatBillingShareClass = billingSlClass;

const chatBillingRowKey = (item, mode) => (mode === 'operator'
  ? String(item.operator || '')
  : String(item.park || ''));

const ChatBillingTable = ({ rows, totals, totalsLabel = 'Итого', mode = 'park' }) => {
  const renderMetricsCells = (item) => {
    const slRatio = safeRatio(item.answered_sl, item.chats);
    return (
      <>
        <td className="px-3 py-2.5 text-right font-semibold text-slate-900">{formatInt(item.chats)}</td>
        <td className="px-3 py-2.5 text-right text-emerald-700">{formatInt(item.answered)}</td>
        <td className="px-3 py-2.5 text-right text-rose-600">{formatInt(item.no_reply)}</td>
        <td className="px-3 py-2.5 text-right text-slate-700">{chatBillingReplyLabel(item)}</td>
        <td className={`px-3 py-2.5 text-right font-semibold ${chatBillingShareClass(slRatio)}`}>
          {slRatio === null ? '—' : formatPercent(slRatio, 1)}
        </td>
      </>
    );
  };

  const firstColumnLabel = mode === 'operator'
    ? 'Чатник'
    : 'Таксопарк';

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] divide-y divide-slate-200 text-sm tabular-nums">
        <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-3 py-2.5 text-left font-semibold">{firstColumnLabel}</th>
            {CHAT_BILLING_COLUMNS.map((column) => (
              <th key={column.key} className="px-3 py-2.5 text-right font-semibold">{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((item) => (
            <tr key={chatBillingRowKey(item, mode)} className="transition hover:bg-slate-50/70">
              <td className="px-3 py-2.5 font-medium text-slate-900">
                {mode === 'operator' ? (item.operator || '—') : billingParkLabel(item.park)}
              </td>
              {renderMetricsCells(item)}
            </tr>
          ))}
        </tbody>
        {totals ? (
          <tfoot>
            <tr className="bg-slate-50 font-semibold text-slate-950">
              <td className="px-3 py-2.5">{totalsLabel}</td>
              {renderMetricsCells(totals)}
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
};

// Разрез по людям — та же таблица: показатели у чатника и у таксопарка одни.
const ChatBillingOperatorTable = ({ rows, totals, totalsLabel = 'Итого' }) => (
  <ChatBillingTable rows={rows} totals={totals} totalsLabel={totalsLabel} mode="operator" />
);

const ChatBillingDetailTable = ({ rows }) => (
  <div className="overflow-x-auto">
    <table className="w-full min-w-[980px] divide-y divide-slate-200 text-sm tabular-nums">
      <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
        <tr>
          <th className="px-3 py-2.5 text-left font-semibold">Дата</th>
          <th className="px-3 py-2.5 text-left font-semibold">Таксопарк</th>
          <th className="px-3 py-2.5 text-left font-semibold">Номер</th>
          <th className="px-3 py-2.5 text-left font-semibold">Клиент</th>
          <th className="px-3 py-2.5 text-left font-semibold">Чатник</th>
          <th className="px-3 py-2.5 text-right font-semibold" title="Время до первого ответа оператора">Первый ответ</th>
          <th className="px-3 py-2.5 text-right font-semibold" title="Первый ответ уложился в цель">В цель</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {rows.map((item) => (
          <tr key={item.id} className="transition hover:bg-slate-50/70">
            <td className="whitespace-nowrap px-3 py-2.5 text-slate-700">{billingOccurredAtLabel(item.started_at)}</td>
            <td className="px-3 py-2.5 font-medium text-slate-900">{billingParkLabel(item.park)}</td>
            <td className="px-3 py-2.5 text-slate-700">{item.client_number || '—'}</td>
            <td className="px-3 py-2.5 text-slate-700">{item.client || '—'}</td>
            <td className="px-3 py-2.5 text-slate-700">{item.operator || '—'}</td>
            <td className="whitespace-nowrap px-3 py-2.5 text-right font-medium text-slate-900">
              {item.first_reply_seconds === null || item.first_reply_seconds === undefined
                ? '—'
                : formatDurationHms(item.first_reply_seconds)}
            </td>
            <td className="px-3 py-2.5 text-right text-emerald-700">{Number(item.answered_sl) > 0 ? '1' : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

// Способности линии, которые держатся на телефонии. У чата выключены все —
// объявляем их одним набором, чтобы новый признак линии нельзя было забыть.
const CHAT_TELEPHONY_OFF = {
  hasAht: false,
  hasOccUr: false,
  hasAnswerRate: false,
  hasWorkloadMinutes: false,
  hasLosses: false,
  hasUpload: false,
  hasOktellSync: false,
  // Аукцион смен теперь двунаправленный: у чата свой прогон на том же разделе
  // (переключатель «Линия / Чат» в его шапке). Планировщик чата открывает раздел
  // сразу на чатовом направлении — см. auctionDirectionFor в ResourceSchedulePlanner.
  hasShiftAuction: true,
  hasDirectionPicker: false,
  // Биллинг здесь — телефонный: очереди, время разговора, выгрузка по
  // эффективности операторов. Он у чата выключен, а свой, по обращениям,
  // включается поверх набора в CHAT_DIRECTION.
  hasBilling: false,
  hasBillingTalkTime: false,
  hasBillingExportTypes: false,
};

const CHAT_DIRECTION = {
  apiPrefix: CHAT_API_PREFIX,
  storageKey: CHAT_DISPLAY_PREFERENCES_STORAGE_KEY,
  tabs: VIEW_TABS_CHAT,
  displayGroups: CHAT_DISPLAY_GROUPS,
  defaultDisplayOptions: CHAT_DEFAULT_DISPLAY_OPTIONS,
  chartLegend: CHAT_FORECAST_CHART_LEGEND_ITEMS,
  forecastPanelGroups: CHAT_FORECAST_PANEL_GROUPS,
  trendTooltipConfig: CHAT_OVERVIEW_TREND_TOOLTIP_CONFIG,
  rates: CHAT_RATE_VALUES,
  title: 'Расчет ресурсов · Чат',
  subtitle: (
    'Считаем от цели по сервису, а не от среднего времени обработки: в чатах оно '
    + 'меряет ожидание клиента, а не работу оператора. Потребность — это объём чатов, '
    + 'делённый на то, сколько чатов в час держит один человек при цели «ответ внутри чата».'
  ),
  historyPickerLabel: 'Период истории',
  historyPickerHint: 'точка = есть чаты',
  overviewTrendTitle: 'Сводка по периоду',
  overviewTrendText: 'Объём чатов, потребность по модели и фактически отработанные чатнико-часы.',
  forecastTitle: 'Прогноз чатнико-часов по выбранному периоду',
  forecastText: 'Для каждого дня берётся среднее того же дня недели по базовым неделям. Ёмкость выведена из цели по сервису.',
  lossesTabTitle: 'Аналитика чатов',
  unit: {
    manyCap: 'Чаты',
    many: 'чатов',
    short: 'чат.',
    fteWord: 'чатники',
    fteHoursCap: 'Чатнико-часы периода',
    fteHoursShort: 'чатнико-часов',
    dayFteCaption: 'чатнико-часов',
    operators: 'Чатники',
    leftAxis: 'чаты',
    rightAxis: 'чатники',
    needFte: 'Нужно чатников',
    tooltipForecastMany: 'Прогноз чатов',
    tooltipAdjustedMany: 'Чатов с приростом',
    tooltipForecastFte: 'Нужно чатников',
    tooltipAdjustedFte: 'Чатников с приростом',
  },
  trendKeys: { calls: 'trendChats', forecastFte: 'trendNeedFte', actualFte: 'trendActualFte' },
  forecastChartKeys: { calls: 'forecastChartChats', actualCalls: 'forecastChartActualChats' },
  forecastTableKeys: { actualCalls: 'forecastTableActualChats', actualFte: 'forecastTableActualHours' },
  overviewParams: { forecastFrom: 'week_start', forecastTo: 'period_end' },
  billing: {
    // Ручки: `${apiPrefix}/billing`, `_operators`, `_details`, `_export`.
    endpoint: 'billing',
    modes: CHAT_BILLING_MODES,
    slDefaultSeconds: CHAT_BILLING_SL_DEFAULT_SECONDS,
    arClass: chatBillingShareClass,
    title: 'Биллинг чатов',
    text: 'Обращения из базы: объём и скорость первого ответа по дням за выбранный период и окно времени.',
    loadingText: 'Собираем обращения...',
    errorText: 'Не удалось получить данные по обращениям',
    detailTitle: 'Детализация обращений',
    detailNote: 'Одна строка — одно обращение; «первый ответ» — время реакции оператора, «в цель» — попадание в порог.',
    emptyText: 'За выбранный период и окно времени обращений не нашлось.',
    idleText: 'Выберите период и окно времени, затем нажмите «Сформировать» — обращения придут из базы.',
    summaryTitle: (mode) => (mode === 'operator'
      ? 'Итоги за период по чатникам'
      : 'Итоги за период по таксопаркам'),
    daySummary: (mode, day) => (mode === 'operator'
      ? `Чатников ${formatInt((day.operators || []).length)} · Обслужено ${formatInt(day.totals?.answered)} · Ср. первый ответ ${chatBillingReplyLabel(day.totals)}`
      : `Поступило ${formatInt(day.totals?.chats)} · Обслужено ${formatInt(day.totals?.answered)} · Потеряно ${formatInt(day.totals?.no_reply)}`),
    modeHint: (mode, slSeconds) => (mode === 'detail'
      ? 'Одна строка — одно обращение; на странице 25 строк'
      : `SL — доля обращений, где первый ответ уложился в ≤ ${slSeconds} сек; AR — доля отвеченных`),
    exportFileName: (mode, applied) => `chat_billing_${applied.from}_${applied.to}.xlsx`,
  },
  ...CHAT_TELEPHONY_OFF,
  // Единственное исключение из набора выше: вкладка биллинга у чата своя — те же
  // четыре ручки, но по обращениям. Времени разговора и обработки в чатовой
  // модели нет, поэтому hasBillingTalkTime остаётся выключенным: ни карточек, ни
  // колонок о длительности. Выгрузки «по эффективности операторов» у чата тоже
  // нет — hasBillingExportTypes из набора не переопределяется.
  hasBilling: true,
  hasHistoryPairs: false,
  hasUpliftProjection: false,
  hasActualPeakHours: false,
  hasWeekPicker: false,
  localDates: true,
  adaptOverview: adaptChatOverview,
  adaptDay: adaptChatDay,
};
// <<< CHAT_DIRECTION_CONFIG_END

const LINE_DIRECTION = {
  apiPrefix: '/api/resource_fte',
  storageKey: DISPLAY_PREFERENCES_STORAGE_KEY,
  tabs: VIEW_TABS,
  displayGroups: DISPLAY_GROUPS,
  defaultDisplayOptions: DEFAULT_DISPLAY_OPTIONS,
  chartLegend: FORECAST_CHART_LEGEND_ITEMS,
  forecastPanelGroups: FORECAST_PANEL_GROUPS,
  trendTooltipConfig: OVERVIEW_TREND_TOOLTIP_CONFIG,
  rates: null,
  title: 'Расчет ресурсов / FTE',
  subtitle: '',
  historyPickerLabel: 'Период анализа',
  historyPickerHint: 'точка = есть отчет',
  overviewTrendTitle: 'Сводка по периоду',
  overviewTrendText: 'Динамика звонков и FTE по загруженным дням в выбранном диапазоне.',
  forecastTitle: 'Прогноз FTE по выбранному периоду',
  forecastText: 'Для каждого дня берутся две исторические даты: минус 21 и минус 14 дней. AHT считается отдельно по дню.',
  lossesTabTitle: 'Аналитика звонков',
  unit: {
    manyCap: 'Звонки',
    many: 'звонков',
    short: 'зв.',
    fteWord: 'FTE',
    fteHoursCap: 'FTE-часы периода',
    fteHoursShort: 'FTE-ч',
    dayFteCaption: 'FTE прогноз',
    operators: 'Операторы',
    leftAxis: 'звонки / мин',
    rightAxis: 'FTE',
    needFte: 'FTE',
    tooltipForecastMany: 'Прогноз звонков',
    tooltipAdjustedMany: 'Звонков с приростом',
    tooltipForecastFte: 'Прогноз FTE',
    tooltipAdjustedFte: 'FTE с приростом',
  },
  trendKeys: { calls: 'chartCalls', forecastFte: 'chartFte', actualFte: 'chartActual' },
  forecastChartKeys: { calls: 'forecastChartCalls', actualCalls: 'forecastChartActualWorkload' },
  forecastTableKeys: { actualCalls: 'forecastTableActualCalls', actualFte: 'forecastTableActualFte' },
  overviewParams: { forecastFrom: 'forecast_date_from', forecastTo: 'forecast_date_to' },
  billing: {
    // Ручки: `${apiPrefix}/oktell_billing`, `_operators`, `_details`, `_export`.
    endpoint: 'oktell_billing',
    modes: BILLING_MODES,
    slDefaultSeconds: 20,
    arClass: billingArClass,
    title: 'Биллинг Oktell',
    text: 'Входящие звонки напрямую из базы Oktell: детализация по дням за выбранный период и окно времени.',
    loadingText: 'Получаем данные из Oktell...',
    errorText: 'Не удалось получить данные из Oktell',
    detailTitle: 'Детализация звонков',
    detailNote: 'IVR — не дошли до очереди; очередь — не дождались оператора; время разговора — только отвеченные звонки.',
    emptyText: 'Oktell не вернул входящих звонков за указанный период и окно времени.',
    idleText: 'Выберите период и окно времени, затем нажмите «Сформировать» — данные придут напрямую из Oktell.',
    summaryTitle: (mode) => (mode === 'operator'
      ? 'Итоги за период по операторам'
      : mode === 'line' ? 'Итоги за период по номерам' : 'Итоги за период по таксопаркам'),
    daySummary: (mode, day) => (mode === 'operator'
      ? `Операторов ${formatInt((day.operators || []).length)} · Обслужено ${formatInt(day.totals?.served)} · Разговоры ${formatDurationHms(day.totals?.talk_in_seconds)}`
      : `Поступило ${formatInt(day.totals?.arrived)} · Обслужено ${formatInt(day.totals?.served)} · Потеряно ${formatInt(day.totals?.lost)}`),
    // Подсказки режимов у линии остаются в разметке: в подсказке про SL стоит
    // число из ответа, и вынос её в строку конфигурации сменил бы разметку.
    modeHint: null,
    exportFileName: (mode, applied) => `oktell_billing_${mode}_${applied.from}_${applied.to}.xlsx`,
  },
  hasAht: true,
  hasOccUr: true,
  hasAnswerRate: true,
  hasWorkloadMinutes: true,
  hasLosses: true,
  hasUpload: true,
  hasOktellSync: true,
  hasShiftAuction: true,
  hasDirectionPicker: true,
  hasBilling: true,
  hasBillingTalkTime: true,
  hasBillingExportTypes: true,
  hasHistoryPairs: true,
  hasUpliftProjection: true,
  hasActualPeakHours: true,
  hasWeekPicker: true,
  localDates: false,
  adaptOverview: (payload) => payload,
  adaptDay: (day) => day,
};

const DIRECTION_CONFIG = { line: LINE_DIRECTION, chat: CHAT_DIRECTION };

const OverviewTrendTooltip = ({ active, label, payload, config = OVERVIEW_TREND_TOOLTIP_CONFIG }) => {
  if (!active || !payload?.length) return null;

  const groups = payload.reduce((acc, entry) => {
    const key = entry.dataKey || entry.name;
    const entryConfig = config[key];
    if (!entryConfig) return acc;
    if (!acc[entryConfig.group]) {
      acc[entryConfig.group] = {
        order: entryConfig.groupOrder,
        items: [],
      };
    }
    acc[entryConfig.group].items.push({
      ...entryConfig,
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

const TIME_PICKER_HOURS = Array.from({ length: 24 }, (_, index) => index);
const TIME_PICKER_MINUTES = [...Array.from({ length: 12 }, (_, index) => index * 5), 59];

const TimeRangePicker = ({ label, startValue, endValue, onRangeChange }) => {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event) => {
      if (anchorRef.current && !anchorRef.current.contains(event.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [open]);

  const parseParts = (value, fallbackHour, fallbackMinute) => {
    const [hours, minutes] = String(value || '').split(':').map(Number);
    return [
      Number.isFinite(hours) ? Math.max(0, Math.min(23, hours)) : fallbackHour,
      Number.isFinite(minutes) ? Math.max(0, Math.min(59, minutes)) : fallbackMinute,
    ];
  };
  const [startHour, startMinute] = parseParts(startValue, 0, 0);
  const [endHour, endMinute] = parseParts(endValue, 23, 59);

  const emitTime = (which, hour, minute) => {
    const pad = (value) => String(value).padStart(2, '0');
    const next = `${pad(hour)}:${pad(minute)}`;
    if (which === 'start') {
      onRangeChange?.(next, timeStringToMinutes(next) > timeStringToMinutes(endValue) ? next : endValue);
    } else {
      onRangeChange?.(timeStringToMinutes(next) < timeStringToMinutes(startValue) ? next : startValue, next);
    }
  };

  const scrollSelectedIntoView = useCallback((node) => {
    if (node) node.scrollIntoView({ block: 'nearest' });
  }, []);

  const renderColumn = (options, selected, onSelect) => (
    <div className="max-h-40 flex-1 overflow-y-auto rounded-lg border border-slate-200 bg-white p-1">
      {options.map((option) => {
        const active = option === selected;
        return (
          <button
            key={option}
            type="button"
            ref={active ? scrollSelectedIntoView : undefined}
            onClick={() => onSelect(option)}
            className={`block w-full rounded-md px-2 py-1 text-center text-sm tabular-nums transition ${
              active ? 'bg-slate-900 font-semibold text-white shadow-sm' : 'text-slate-700 hover:bg-slate-100'
            }`}
          >
            {String(option).padStart(2, '0')}
          </button>
        );
      })}
    </div>
  );

  const presetActive = (preset) => preset.from === startValue && preset.to === endValue;

  return (
    <div ref={anchorRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex h-14 w-full items-center justify-between gap-3 rounded-xl border-2 border-slate-200 bg-white px-4 text-left text-sm shadow-sm transition hover:border-slate-300 hover:bg-slate-50"
      >
        <span className="min-w-0">
          <span className="block text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</span>
          <span className="block truncate font-semibold tabular-nums text-slate-900">{startValue} — {endValue}</span>
        </span>
        <Clock3 size={17} className="shrink-0 text-blue-600" />
      </button>

      {open && (
        <div className="absolute right-0 z-40 mt-2 w-[320px] rounded-2xl border-2 border-slate-200 bg-white p-4 shadow-xl">
          <div className="flex flex-wrap gap-1.5">
            {BILLING_TIME_PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() => onRangeChange?.(preset.from, preset.to)}
                className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                  presetActive(preset)
                    ? 'border-slate-900 bg-slate-900 text-white shadow-sm'
                    : 'border-slate-200 text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>

          <div className="mt-3 grid grid-cols-2 gap-3">
            {[
              { key: 'start', title: 'С', hour: startHour, minute: startMinute },
              { key: 'end', title: 'По', hour: endHour, minute: endMinute },
            ].map((side) => (
              <div key={side.key} className="rounded-xl border border-slate-200 bg-slate-50 p-2">
                <div className="px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{side.title}</div>
                <div className="mt-1.5 flex gap-1.5">
                  {renderColumn(TIME_PICKER_HOURS, side.hour, (hour) => emitTime(side.key, hour, side.minute))}
                  {renderColumn(TIME_PICKER_MINUTES, side.minute, (minute) => emitTime(side.key, side.hour, minute))}
                </div>
              </div>
            ))}
          </div>

          <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3">
            <span className="text-xs text-slate-500">Границы включительно, по минутам</span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-slate-800"
            >
              Готово
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

const BillingTable = ({ rows, totals, totalsLabel = 'Итого', mode = 'park' }) => {
  const renderMetricsCells = (item) => {
    const arRatio = safeRatio(item.lost, item.arrived);
    // SL: отвеченные за порог ожидания ко ВСЕМ попавшим в очередь (обслуженные + потерянные)
    const slRatio = safeRatio(item.served_sl, item.arrived);
    const attSeconds = safeRatio(item.talk_seconds, item.served);
    const waitSeconds = safeRatio(item.wait_ok_seconds, item.served);
    return (
      <>
        <td className="px-3 py-2.5 text-right font-semibold text-slate-900">{formatInt(item.arrived)}</td>
        <td className="px-3 py-2.5 text-right text-emerald-700">{formatInt(item.served)}</td>
        <td className="px-3 py-2.5 text-right text-rose-600">{formatInt(item.lost)}</td>
        <td className={`px-3 py-2.5 text-right font-semibold ${billingArClass(arRatio)}`}>
          {arRatio === null ? '—' : formatPercent(arRatio, 1)}
        </td>
        <td className={`px-3 py-2.5 text-right font-semibold ${billingSlClass(slRatio)}`}>
          {slRatio === null ? '—' : formatPercent(slRatio, 1)}
        </td>
        <td className="px-3 py-2.5 text-right text-slate-700">{attSeconds === null ? '—' : formatDurationHms(attSeconds)}</td>
        <td className="px-3 py-2.5 text-right text-slate-700">{waitSeconds === null ? '—' : formatDurationHms(waitSeconds)}</td>
        <td className="px-3 py-2.5 text-right text-slate-900">{formatDurationHms(item.talk_seconds)}</td>
        <td className="px-3 py-2.5 text-right text-slate-500">{formatDurationHms(item.total_seconds)}</td>
        <td className="px-3 py-2.5 text-right text-slate-500">{formatInt(item.greet_drop)}</td>
      </>
    );
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1120px] divide-y divide-slate-200 text-sm tabular-nums">
        <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-3 py-2.5 text-left font-semibold">{mode === 'line' ? 'Номер' : 'Таксопарк'}</th>
            <th className="px-3 py-2.5 text-right font-semibold">Поступило</th>
            <th className="px-3 py-2.5 text-right font-semibold">Обслужено</th>
            <th className="px-3 py-2.5 text-right font-semibold">Потеряно</th>
            <th className="px-3 py-2.5 text-right font-semibold">AR</th>
            <th className="px-3 py-2.5 text-right font-semibold">SL</th>
            <th className="px-3 py-2.5 text-right font-semibold">Ср. разговор</th>
            <th className="px-3 py-2.5 text-right font-semibold">Ср. ожидание</th>
            <th className="px-3 py-2.5 text-right font-semibold">Время разговора</th>
            <th className="px-3 py-2.5 text-right font-semibold">Общее время</th>
            <th className="px-3 py-2.5 text-right font-semibold">Сброс на приветствии</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((item) => (
            <tr key={`${item.park}|${item.line || ''}`} className="transition hover:bg-slate-50/70">
              {mode === 'line' ? (
                <td className="px-3 py-2.5">
                  <div className="font-medium text-slate-900">
                    {billingLineLabel(item.line) || billingLineDisplayNumber(item.line) || `${billingParkLabel(item.park)} (без номера)`}
                  </div>
                  <div className="text-xs text-slate-400">
                    {billingLineDisplayNumber(item.line) ? `${billingLineDisplayNumber(item.line)} · ${billingParkLabel(item.park)}` : billingParkLabel(item.park)}
                  </div>
                </td>
              ) : (
                <td className="px-3 py-2.5 font-medium text-slate-900">{billingParkLabel(item.park)}</td>
              )}
              {renderMetricsCells(item)}
            </tr>
          ))}
        </tbody>
        {totals ? (
          <tfoot>
            <tr className="bg-slate-50 font-semibold text-slate-950">
              <td className="px-3 py-2.5">{totalsLabel}</td>
              {renderMetricsCells(totals)}
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
};

const BillingDetailTable = ({ rows }) => (
  <div className="overflow-x-auto">
    <table className="w-full min-w-[1180px] divide-y divide-slate-200 text-sm tabular-nums">
      <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
        <tr>
          <th className="px-3 py-2.5 text-left font-semibold">Дата</th>
          <th className="px-3 py-2.5 text-left font-semibold">Парк на который звонят</th>
          <th className="px-3 py-2.5 text-left font-semibold">Номер на который звонят</th>
          <th className="px-3 py-2.5 text-left font-semibold">Номер водителя</th>
          <th className="px-3 py-2.5 text-right font-semibold" title="Звонки, которые не дошли до очереди после IVR">Сброс на IVR</th>
          <th className="px-3 py-2.5 text-right font-semibold" title="Оператор не принял звонок, водитель сбросил">Сброс в очереди/пропущенные</th>
          <th className="px-3 py-2.5 text-right font-semibold" title="Время разговора, если звонок дошёл до оператора">Время разговора</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {rows.map((item) => (
          <tr key={item.id} className="transition hover:bg-slate-50/70">
            <td className="whitespace-nowrap px-3 py-2.5 text-slate-700">{billingOccurredAtLabel(item.occurred_at)}</td>
            <td className="px-3 py-2.5 font-medium text-slate-900">{billingParkLabel(item.park)}</td>
            <td className="whitespace-nowrap px-3 py-2.5 text-slate-700">
              {billingDetailPhoneDisplayNumber(item.line)}
            </td>
            <td className="whitespace-nowrap px-3 py-2.5 text-slate-700">{billingDetailPhoneDisplayNumber(item.driver_number)}</td>
            <td className="px-3 py-2.5 text-right text-amber-700">{billingCallFlagLabel(item.ivr_drop)}</td>
            <td className="px-3 py-2.5 text-right text-rose-600">{billingCallFlagLabel(item.queue_drop)}</td>
            <td className="px-3 py-2.5 text-right font-medium text-slate-900">{billingTalkDurationLabel(item.talk_seconds)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

// Занятость оператора: активное = разговоры (вх+исх) + постобработка + удержание + распределение
const billingOperatorActivity = (item) => {
  const active = Number(item.talk_in_seconds || 0) + Number(item.talk_out_seconds || 0)
    + Number(item.postproc_seconds || 0) + Number(item.hold_seconds || 0) + Number(item.dial_seconds || 0);
  const wait = Number(item.wait_seconds || 0);
  const pause = Number(item.pause_seconds || 0);
  const total = active + wait + pause;
  return {
    occ: total > 0 ? active / total : null,
    utz: total > 0 ? (active + wait) / total : null,
  };
};

const BillingOperatorTable = ({ rows, totals, totalsLabel = 'Итого' }) => {
  const renderMetricsCells = (item) => {
    const attSeconds = safeRatio(item.talk_seconds, item.served);
    const ahtSeconds = safeRatio(
      Number(item.talk_seconds || 0) + Number(item.hold_seconds || 0) + Number(item.postproc_seconds || 0),
      item.served,
    );
    const { occ, utz } = billingOperatorActivity(item);
    return (
      <>
        <td className="px-3 py-2.5 text-right font-semibold text-slate-900">{formatInt(item.served)}</td>
        <td className="px-3 py-2.5 text-right text-slate-700">{attSeconds === null ? '—' : formatDurationHms(attSeconds)}</td>
        <td className="px-3 py-2.5 text-right text-slate-700">{ahtSeconds === null ? '—' : formatDurationHms(ahtSeconds)}</td>
        <td className="px-3 py-2.5 text-right text-slate-900">{formatDurationHms(item.talk_in_seconds)}</td>
        <td className="px-3 py-2.5 text-right text-slate-500">{formatDurationHms(item.talk_out_seconds)}</td>
        <td className="px-3 py-2.5 text-right text-slate-500">{formatDurationHms(item.postproc_seconds)}</td>
        <td className="px-3 py-2.5 text-right text-slate-500">{formatDurationHms(item.wait_seconds)}</td>
        <td className="px-3 py-2.5 text-right text-slate-500">{formatDurationHms(item.pause_seconds)}</td>
        <td className={`px-3 py-2.5 text-right font-semibold ${occ === null ? 'text-slate-400' : 'text-slate-900'}`}>
          {occ === null ? '—' : formatPercent(occ, 1)}
        </td>
        <td className={`px-3 py-2.5 text-right font-semibold ${utz === null ? 'text-slate-400' : 'text-slate-900'}`}>
          {utz === null ? '—' : formatPercent(utz, 1)}
        </td>
      </>
    );
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1120px] divide-y divide-slate-200 text-sm tabular-nums">
        <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-3 py-2.5 text-left font-semibold">Оператор</th>
            <th className="px-3 py-2.5 text-right font-semibold">Обслужено</th>
            <th className="px-3 py-2.5 text-right font-semibold">АТТ</th>
            <th className="px-3 py-2.5 text-right font-semibold">АНТ</th>
            <th className="px-3 py-2.5 text-right font-semibold">Разговоры вх.</th>
            <th className="px-3 py-2.5 text-right font-semibold">Разговоры исх.</th>
            <th className="px-3 py-2.5 text-right font-semibold">Постобработка</th>
            <th className="px-3 py-2.5 text-right font-semibold">Ожидание</th>
            <th className="px-3 py-2.5 text-right font-semibold">Пауза</th>
            <th className="px-3 py-2.5 text-right font-semibold">OCC</th>
            <th className="px-3 py-2.5 text-right font-semibold">UTZ</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((item) => (
            <tr key={item.operator} className="transition hover:bg-slate-50/70">
              <td className="px-3 py-2.5 font-medium text-slate-900">{item.operator}</td>
              {renderMetricsCells(item)}
            </tr>
          ))}
        </tbody>
        {totals ? (
          <tfoot>
            <tr className="bg-slate-50 font-semibold text-slate-950">
              <td className="px-3 py-2.5">{totalsLabel}</td>
              {renderMetricsCells(totals)}
            </tr>
          </tfoot>
        ) : null}
      </table>
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

const ResourceFteView = ({
  direction = 'line',
  apiBaseUrl,
  withAccessTokenHeader,
  user,
  showToast,
  initialDashboardView,
  onOpenShiftAuction,
}) => {
  const cfg = DIRECTION_CONFIG[direction] || DIRECTION_CONFIG.line;
  const isChat = cfg === DIRECTION_CONFIG.chat;
  // Даты собираем из локальных компонент там, где направление это требует:
  // toISOString в Asia/Almaty (UTC+5) с полуночи до пяти утра отдаёт ВЧЕРА.
  const readToday = () => (cfg.localDates ? toIsoDate(new Date()) : todayIso());
  const readMonthStart = () => {
    if (!cfg.localDates) return monthStartIso();
    const now = new Date();
    return toIsoDate(new Date(now.getFullYear(), now.getMonth(), 1));
  };
  const apiRoot = String(apiBaseUrl || '').replace(/\/+$/, '');
  const fileInputRef = useRef(null);
  const showToastRef = useRef(showToast);
  const authHeaderRef = useRef(withAccessTokenHeader);
  const [overview, setOverview] = useState(null);
  const [selectedDate, setSelectedDate] = useState('');
  const [selectedDay, setSelectedDay] = useState(null);
  const [isDayLoading, setIsDayLoading] = useState(false);
  const [dateFrom, setDateFrom] = useState(readMonthStart);
  const [dateTo, setDateTo] = useState(readToday);
  const [uploadFile, setUploadFile] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isRecalculating, setIsRecalculating] = useState(false);
  const [settingsDraft, setSettingsDraft] = useState(null);
  const [activeDashboardView, setActiveDashboardView] = useState(initialDashboardView || 'overview');
  const [displayOptions, setDisplayOptions] = useState(
    () => loadDisplayOptions(cfg.storageKey, cfg.defaultDisplayOptions),
  );
  // «Сегодня» берём через readToday: у направления с локальными датами ночная
  // смена суток иначе увела бы стартовую неделю прогноза на неделю назад.
  const [selectedForecastWeekStart, setSelectedForecastWeekStart] = useState(() => getNextWeekStartIso(readToday()));
  const [selectedForecastPeriodEnd, setSelectedForecastPeriodEnd] = useState(() => addDaysIso(getNextWeekStartIso(readToday()), 6));
  const [selectedForecastDate, setSelectedForecastDate] = useState('');
  const [isForecastPanelOpen, setIsForecastPanelOpen] = useState(false);
  const showForecastActualLoadOption = Boolean(displayOptions.forecastShowActualLoad);
  const [hoveredForecastHour, setHoveredForecastHour] = useState(null);
  const [pinnedForecastHour, setPinnedForecastHour] = useState(null);
  // Раздел живёт открытым сутками: после полуночи текущим остался бы вчерашний
  // день — ни бейджа, ни подгрузки факта. Дату пересматриваем по таймеру.
  const [tickToday, setTickToday] = useState(readToday);
  const [callsChartMode, setCallsChartMode] = useState('losses');
  const [loadedDateCache, setLoadedDateCache] = useState([]);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const _yesterdayIso = () => {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };
  const [isOktellSyncModalOpen, setIsOktellSyncModalOpen] = useState(false);
  const [oktellSyncFrom, setOktellSyncFrom] = useState(_yesterdayIso);
  const [oktellSyncTo, setOktellSyncTo] = useState(_yesterdayIso);
  const [isOktellSyncing, setIsOktellSyncing] = useState(false);
  const [billingFrom, setBillingFrom] = useState(() => addDaysIso(todayIso(), -6));
  const [billingTo, setBillingTo] = useState(todayIso);
  const [billingTimeFrom, setBillingTimeFrom] = useState('00:00');
  const [billingTimeTo, setBillingTimeTo] = useState('23:59');
  const [billingMode, setBillingMode] = useState('park');
  // Применённые параметры: фетч идёт только по ним (по кнопке «Сформировать»),
  // изменение пикеров само по себе не дергает Oktell.
  const [billingApplied, setBillingApplied] = useState(() => ({
    from: addDaysIso(todayIso(), -6),
    to: todayIso(),
    timeFrom: '00:00',
    timeTo: '23:59',
  }));
  const [billingReports, setBillingReports] = useState({ park: null, line: null, operator: null, detail: null });
  const [isBillingLoading, setIsBillingLoading] = useState(false);
  const [isBillingExporting, setIsBillingExporting] = useState(false);
  const [billingExportType, setBillingExportType] = useState('general');
  const [billingErrors, setBillingErrors] = useState({ park: '', line: '', operator: '', detail: '' });
  const [billingExpandedDays, setBillingExpandedDays] = useState(() => new Set());
  const [billingDetailPage, setBillingDetailPage] = useState(1);
  const billingAttemptedRef = useRef({});
  // Метки актуальности загрузок. Быстрый перещёлк периода держит в полёте
  // несколько запросов, и поздний ответ на РАННИЙ запрос затирал свежие данные.
  const overviewRequestRef = useRef(0);
  const dayRequestRef = useRef(0);
  const analyticsRequestRef = useRef(0);
  // У линии аналитика приходит внутри витрины, у чата — отдельной ручкой:
  // это структурная разница, поэтому у неё свой период и своё состояние.
  const [analyticsFrom, setAnalyticsFrom] = useState('');
  const [analyticsTo, setAnalyticsTo] = useState('');
  const [analytics, setAnalytics] = useState(null);
  const [isAnalyticsLoading, setIsAnalyticsLoading] = useState(false);
  const [chatsChartMode, setChatsChartMode] = useState('volume');
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

  useEffect(() => {
    // Ключи вкладок общие, но набор у направлений разный: внешний переход на
    // чужую вкладку (например, на биллинг из чата) иначе оставил бы пустой экран.
    if (cfg.tabs.some((tab) => tab.key === activeDashboardView)) return;
    setActiveDashboardView('overview');
  }, [activeDashboardView, cfg.tabs]);

  useEffect(() => {
    if (!cfg.localDates) return undefined;
    const timer = setInterval(() => {
      setTickToday((current) => {
        const next = toIsoDate(new Date());
        return next === current ? current : next;
      });
    }, 60000);
    return () => clearInterval(timer);
  }, [cfg.localDates]);

  const notify = useCallback((message, type = 'success') => {
    if (typeof showToastRef.current === 'function') showToastRef.current(message, type);
  }, []);

  const buildHeaders = useCallback(
    (extra = {}) => apiHeaders(authHeaderRef.current, { ...extra, 'X-User-Id': String(userId) }),
    [userId],
  );

  const fetchOverview = useCallback(async () => {
    if (!apiRoot) return;
    const requestId = overviewRequestRef.current + 1;
    overviewRequestRef.current = requestId;
    setIsLoading(true);
    try {
      const response = await axios.get(`${apiRoot}${cfg.apiPrefix}/overview`, {
        params: {
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
          [cfg.overviewParams.forecastFrom]: selectedForecastWeekStart || undefined,
          [cfg.overviewParams.forecastTo]: selectedForecastPeriodEnd || undefined,
        },
        headers: buildHeaders(),
      });
      if (overviewRequestRef.current !== requestId) return;
      const payload = cfg.adaptOverview(response.data || {});
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
      if (overviewRequestRef.current !== requestId) return;
      notify(error?.response?.data?.error || 'Не удалось загрузить расчет ресурсов', 'error');
    } finally {
      if (overviewRequestRef.current === requestId) setIsLoading(false);
    }
  }, [apiRoot, buildHeaders, cfg, dateFrom, dateTo, notify, selectedForecastPeriodEnd, selectedForecastWeekStart]);

  const fetchDay = useCallback(
    async (date) => {
      if (!apiRoot || !date) {
        setSelectedDay(null);
        setIsDayLoading(false);
        return;
      }
      const requestId = dayRequestRef.current + 1;
      dayRequestRef.current = requestId;
      setIsDayLoading(true);
      try {
        const response = await axios.get(`${apiRoot}${cfg.apiPrefix}/day/${date}`, {
          headers: buildHeaders(),
        });
        if (dayRequestRef.current !== requestId) return;
        setSelectedDay(cfg.adaptDay(response.data?.day || null));
      } catch (error) {
        if (dayRequestRef.current !== requestId) return;
        setSelectedDay(null);
        notify(error?.response?.data?.error || 'Не удалось открыть день', 'error');
      } finally {
        if (dayRequestRef.current === requestId) setIsDayLoading(false);
      }
    },
    [apiRoot, buildHeaders, cfg, notify],
  );

  const fetchChatAnalytics = useCallback(async (from, to) => {
    if (!apiRoot) return;
    const requestId = analyticsRequestRef.current + 1;
    analyticsRequestRef.current = requestId;
    setIsAnalyticsLoading(true);
    try {
      const response = await axios.get(`${apiRoot}${cfg.apiPrefix}/analytics`, {
        params: { date_from: from || undefined, date_to: to || undefined },
        headers: buildHeaders(),
      });
      if (analyticsRequestRef.current !== requestId) return;
      setAnalytics(response.data || null);
    } catch (error) {
      if (analyticsRequestRef.current !== requestId) return;
      notify(error?.response?.data?.error || 'Не удалось загрузить аналитику чатов', 'error');
    } finally {
      if (analyticsRequestRef.current === requestId) setIsAnalyticsLoading(false);
    }
  }, [apiRoot, buildHeaders, cfg, notify]);

  const billingAppliedKey = `${billingApplied.from}|${billingApplied.to}|${billingApplied.timeFrom}|${billingApplied.timeTo}`;

  const fetchBillingReport = useCallback(async (mode, { page = 1, snapshotId = '' } = {}) => {
    if (!apiRoot) return;
    const targetMode = mode || 'park';
    billingAttemptedRef.current[targetMode] = billingAppliedKey;
    setIsBillingLoading(true);
    setBillingErrors((current) => ({ ...current, [targetMode]: '' }));
    try {
      // Ручки биллинга у направлений разные (`oktell_billing…` против
      // `chat/billing…`), а хвосты одинаковые — поэтому в конфигурации лежит
      // только основа пути.
      const endpoint = targetMode === 'detail'
        ? `${apiRoot}${cfg.apiPrefix}/${cfg.billing.endpoint}_details`
        : targetMode === 'operator'
          ? `${apiRoot}${cfg.apiPrefix}/${cfg.billing.endpoint}_operators`
          : `${apiRoot}${cfg.apiPrefix}/${cfg.billing.endpoint}`;
      const params = {
        date_from: billingApplied.from,
        date_to: billingApplied.to,
        time_from: billingApplied.timeFrom,
        time_to: billingApplied.timeTo,
      };
      if (targetMode === 'detail') {
        params.page = page;
        params.per_page = BILLING_DETAIL_PAGE_SIZE;
        if (snapshotId) params.snapshot_id = snapshotId;
      } else if (targetMode !== 'operator') {
        params.group_by = targetMode;
      }
      const response = await axios.get(endpoint, { params, headers: buildHeaders() });
      const payload = response.data || {};
      setBillingReports((current) => ({ ...current, [targetMode]: payload }));
      if (targetMode === 'detail') {
        setBillingDetailPage(Math.max(1, Number(payload.pagination?.page || page)));
      } else {
        const dayKeys = (payload.days || []).map((day) => day.date);
        setBillingExpandedDays(new Set(dayKeys.length <= 3 ? dayKeys : []));
      }
    } catch (error) {
      setBillingReports((current) => ({ ...current, [targetMode]: null }));
      const message = error?.response?.data?.error || cfg.billing.errorText;
      setBillingErrors((current) => ({ ...current, [targetMode]: message }));
    } finally {
      setIsBillingLoading(false);
    }
  }, [apiRoot, billingApplied, billingAppliedKey, buildHeaders, cfg]);

  const buildBillingReport = useCallback(() => {
    billingAttemptedRef.current = {};
    setBillingReports({ park: null, line: null, operator: null, detail: null });
    setBillingErrors({ park: '', line: '', operator: '', detail: '' });
    setBillingDetailPage(1);
    setBillingApplied({ from: billingFrom, to: billingTo, timeFrom: billingTimeFrom, timeTo: billingTimeTo });
  }, [billingFrom, billingTimeFrom, billingTimeTo, billingTo]);

  const exportBillingExcel = useCallback(async () => {
    if (!apiRoot) return;
    setIsBillingExporting(true);
    try {
      const response = await axios.get(`${apiRoot}${cfg.apiPrefix}/${cfg.billing.endpoint}_export`, {
        params: {
          date_from: billingApplied.from,
          date_to: billingApplied.to,
          time_from: billingApplied.timeFrom,
          time_to: billingApplied.timeTo,
          mode: billingMode,
          report_type: billingExportType,
        },
        headers: buildHeaders(),
        responseType: 'blob',
      });
      const url = window.URL.createObjectURL(response.data);
      const link = document.createElement('a');
      link.href = url;
      link.download = billingExportType === 'efficiency'
        ? `operator_efficiency_${billingApplied.from}_${billingApplied.to}.xlsx`
        : cfg.billing.exportFileName(billingMode, billingApplied);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (error) {
      let message = 'Не удалось выгрузить Excel';
      try {
        const text = await error?.response?.data?.text?.();
        if (text) message = JSON.parse(text)?.error || message;
      } catch (parseError) {
        // ответ не JSON — оставляем общее сообщение
      }
      notify(message, 'error');
    } finally {
      setIsBillingExporting(false);
    }
  }, [apiRoot, billingApplied, billingExportType, billingMode, buildHeaders, cfg, notify]);

  useEffect(() => {
    fetchOverview();
  }, [fetchOverview]);

  useEffect(() => {
    if (isChat) return;
    fetchDay(selectedDate);
  }, [fetchDay, isChat, selectedDate]);

  useEffect(() => {
    if (!isChat || activeDashboardView !== 'losses') return;
    if (!analyticsFrom || !analyticsTo) {
      const latest = overview?.latest_chat_day || '';
      if (!latest) return;
      setAnalyticsTo(latest);
      setAnalyticsFrom(addDaysIso(latest, -13));
      return;
    }
    fetchChatAnalytics(analyticsFrom, analyticsTo);
  }, [activeDashboardView, analyticsFrom, analyticsTo, fetchChatAnalytics, isChat, overview?.latest_chat_day]);

  useEffect(() => {
    if (activeDashboardView !== 'oktell_billing' || isBillingLoading) return;
    if (billingReports[billingMode]) return;
    if (billingAttemptedRef.current[billingMode] === billingAppliedKey) return;
    fetchBillingReport(billingMode);
  }, [activeDashboardView, billingAppliedKey, billingMode, billingReports, fetchBillingReport, isBillingLoading]);

  const billingPeriodDays = daysBetweenInclusive(billingFrom, billingTo);
  const billingRangeError = !billingPeriodDays
    ? 'Выберите период отчета'
    : billingPeriodDays > 31
      ? 'Период отчета не может быть больше 31 дня'
      : '';
  const billingReport = billingReports[billingMode];
  const billingError = billingErrors[billingMode] || '';
  const billingDays = billingReport?.days || [];
  const billingTotals = billingReport?.totals || null;
  const billingDetailRows = billingMode === 'detail' && Array.isArray(billingReport?.rows) ? billingReport.rows : [];
  const billingDetailPagination = billingMode === 'detail' ? billingReport?.pagination || {} : {};
  const billingDetailTotal = Math.max(0, Number(billingDetailPagination.total || 0));
  const billingDetailTotalPages = Math.max(1, Number(billingDetailPagination.total_pages || 1));
  const billingDetailCurrentPage = Math.min(
    Math.max(1, Number(billingDetailPagination.page || billingDetailPage)),
    billingDetailTotalPages,
  );
  const billingDetailPageStart = billingDetailTotal > 0
    ? (billingDetailCurrentPage - 1) * BILLING_DETAIL_PAGE_SIZE + 1
    : 0;
  const billingDetailPageEnd = billingDetailTotal > 0
    ? Math.min(billingDetailTotal, billingDetailPageStart + billingDetailRows.length - 1)
    : 0;
  const billingArRatio = billingTotals ? safeRatio(billingTotals.lost, billingTotals.arrived) : null;
  const billingSlRatio = billingTotals ? safeRatio(billingTotals.served_sl, billingTotals.arrived) : null;
  const billingOperatorTotals = billingMode === 'operator' && billingTotals ? billingOperatorActivity(billingTotals) : null;
  const billingAttSeconds = billingTotals ? safeRatio(billingTotals.talk_seconds, billingTotals.served) : null;
  const billingAhtSeconds = billingTotals
    ? safeRatio(
      Number(billingTotals.talk_seconds || 0) + Number(billingTotals.hold_seconds || 0) + Number(billingTotals.postproc_seconds || 0),
      billingTotals.served,
    )
    : null;
  // Чат: доли считаются от числа обращений, а средний первый ответ — от тех,
  // кому ответили. Времени разговора и обработки в чатовой модели нет.
  const billingChatArRatio = billingTotals ? safeRatio(billingTotals.answered, billingTotals.chats) : null;
  const billingChatSlRatio = billingTotals ? safeRatio(billingTotals.answered_sl, billingTotals.chats) : null;
  const billingChatReplySeconds = billingTotals ? safeRatio(billingTotals.first_reply_seconds, billingTotals.answered) : null;
  const billingSlSeconds = billingReport?.sl_threshold_seconds ?? cfg.billing.slDefaultSeconds;
  // Таблицы биллинга у направлений свои: у чата в них нет колонок о длительности.
  const BillingSummaryTable = cfg.hasBillingTalkTime ? BillingTable : ChatBillingTable;
  const BillingPeopleTable = cfg.hasBillingTalkTime ? BillingOperatorTable : ChatBillingOperatorTable;
  const BillingRowsTable = cfg.hasBillingTalkTime ? BillingDetailTable : ChatBillingDetailTable;
  const billingAllExpanded = billingDays.length > 0 && billingDays.every((day) => billingExpandedDays.has(day.date));
  const toggleBillingDay = (date) => {
    setBillingExpandedDays((current) => {
      const next = new Set(current);
      if (next.has(date)) next.delete(date);
      else next.add(date);
      return next;
    });
  };
  const setAllBillingDays = (expand) => {
    setBillingExpandedDays(expand ? new Set(billingDays.map((day) => day.date)) : new Set());
  };

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(cfg.storageKey, JSON.stringify(displayOptions));
  }, [cfg.storageKey, displayOptions]);

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

  // Ёмкость чата ВЫВОДИТСЯ из цели по сервису; здесь она только показывается
  // вместе с тем, из какой именно цели получена.
  const chatCapacityExplain = overview?.capacity_explain || {};
  const chatCapacityPerHourValue = Math.max(0.01, Number(
    overview?.settings?.capacity_per_hour || overview?.forecast?.totals?.capacity_per_hour || 17,
  ));
  const chatTargetFirstSeconds = Number(overview?.settings?.target_first_reply_seconds || 60);
  const chatCapacityIsManual = settingsDraft?.capacity_manual !== null
    && settingsDraft?.capacity_manual !== undefined
    && settingsDraft?.capacity_manual !== '';
  const chatSkippedBaseWeeks = overview?.forecast?.skipped_base_weeks || [];
  const chatBaseWeekStarts = overview?.forecast?.base_week_starts || [];

  // Итоги периода у чата: потерь нет (обработки требуют 100 % чатов), зато есть
  // доля первого ответа в цель и отработанные чатнико-часы.
  const chatOverviewSummary = useMemo(() => {
    const rows = overview?.history || [];
    const chats = rows.reduce((sum, row) => sum + Number(row.total_received || 0), 0);
    const inTarget = rows.reduce((sum, row) => sum + Number(row.in_target || 0), 0);
    const online = rows.reduce((sum, row) => sum + Number(row.actual_report_fte_total || 0), 0);
    const need = rows.reduce((sum, row) => sum + Number(row.forecast_fte_total || 0), 0);
    return {
      chats,
      inTarget,
      online,
      need,
      days: rows.length,
      inTargetShare: chats > 0 ? inTarget / chats : 0,
    };
  }, [overview?.history]);

  const analyticsTotals = analytics?.totals || {};
  const analyticsDays = analytics?.days || [];
  const analyticsRiskHours = analytics?.risk_hours || [];
  const analyticsChannels = analytics?.channels || [];
  const analyticsChartData = useMemo(() => analyticsDays.map((row) => {
    const chats = Number(row.chats || 0);
    const inTarget = Number(row.in_target || 0);
    return {
      date: formatDate(row.date).slice(0, 5),
      chats,
      forecastChats: Number(row.forecast_chats || 0),
      inTarget,
      outOfTarget: Math.max(0, chats - inTarget),
      firstReply: row.avg_first_reply_seconds === null ? null : Number(row.avg_first_reply_seconds),
    };
  }), [analyticsDays]);

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

  // Факт часа у чата лежит НЕ в прогнозе, а в детализации дня: склеиваем по номеру
  // часа, чтобы дальше дерево рендера читало одни и те же поля для обоих направлений.
  const chatDayHoursByHour = useMemo(() => {
    if (!isChat) return null;
    const matches = selectedDay?.date && selectedDay.date === selectedForecastDay?.forecast_date;
    const rows = matches ? (selectedDay?.hours || []) : [];
    return rows.reduce((acc, row) => ({ ...acc, [Number(row.hour)]: row }), {});
  }, [isChat, selectedDay, selectedForecastDay]);

  const chatDayActualAvailable = Boolean(
    chatDayHoursByHour && Object.keys(chatDayHoursByHour).length > 0 && selectedForecastDay?.has_actual_report,
  );

  const forecastHourlyRows = useMemo(() => {
    const rows = selectedForecastDay?.hourly_forecast || [];
    if (!isChat || !chatDayHoursByHour) return rows;
    return rows.map((row) => {
      const actual = chatDayHoursByHour[Number(row.hour)] || null;
      if (!actual) return row;
      return {
        ...row,
        has_actual_report: true,
        actual_received_calls: Number(actual.chats || 0),
        actual_report_fte: Number(actual.actual_online_hours || 0),
        actual_first_reply_seconds: actual.avg_first_reply_seconds,
      };
    });
  }, [chatDayHoursByHour, isChat, selectedForecastDay]);

  // Тумблер «сравнивать с фактом» — телефонный: у чата факт часа показывается,
  // как только детализация дня приехала.
  const showForecastActualLoad = isChat ? chatDayActualAvailable : showForecastActualLoadOption;

  const selectedForecastHourlyData = useMemo(
    () =>
      forecastHourlyRows.map((row) => ({
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
        actualCalls: row.has_actual_report ? Number(row.actual_received_calls || 0) : null,
        actualFte: row.has_actual_report ? Number(row.actual_report_fte || 0) : null,
      })),
    [forecastHourlyRows],
  );

  const selectedForecastPeakHours = useMemo(
    () =>
      [...forecastHourlyRows]
        .sort((a, b) => Number(b.forecast_fte || 0) - Number(a.forecast_fte || 0))
        .slice(0, 5),
    [forecastHourlyRows],
  );

  const selectedActualPeakHours = useMemo(
    () =>
      [...forecastHourlyRows]
        .filter((row) => row.has_actual_report)
        .sort((a, b) => Number(b.actual_report_fte || 0) - Number(a.actual_report_fte || 0))
        .slice(0, 5),
    [forecastHourlyRows],
  );

  const todayValue = cfg.localDates ? tickToday : todayIso();
  useEffect(() => {
    if (!isChat) return;
    if (!selectedForecastDate || selectedForecastDate > todayValue) {
      // Сдвигаем метку: ответ уже улетевшего запроса не должен вернуть на экран
      // детализацию прежнего дня поверх пустоты будущего.
      dayRequestRef.current += 1;
      setSelectedDay(null);
      return;
    }
    fetchDay(selectedForecastDate);
  }, [fetchDay, isChat, selectedForecastDate, todayValue]);
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
  // Проекция на 7 дней есть только у линии; у чата прирост приходит вместе с
  // прогнозом периода, и окно берётся из ответа наплыва.
  const upliftPeriodCalls = cfg.hasUpliftProjection
    ? Number(incidentProjection.incident_uplift_calls || 0)
    : Number(nextWeekForecast.incidentUpliftCalls || 0);
  const upliftPeriodFteHours = cfg.hasUpliftProjection
    ? Number(incidentProjection.incident_uplift_fte_hours || 0)
    : Number(nextWeekForecast.incidentUpliftFteHours || 0);
  const upliftWindowStart = cfg.hasUpliftProjection ? incidentProjection.period_start : incidentRiskProfile.window_start;
  const upliftWindowEnd = cfg.hasUpliftProjection ? incidentProjection.period_end : incidentRiskProfile.window_end;

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
      const row = forecastHourlyRows.find((item) => Number(item.hour) === Number(hour));
      if (!row) return null;
      return (
        <div className="min-w-56 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
          <div className="mb-2 font-semibold text-slate-900">{String(row.hour).padStart(2, '0')}:00</div>
          {showForecastActualLoad && selectedForecastHasActualLoad ? (
            <div className="space-y-2">
              <div className="rounded-md bg-slate-50 px-2 py-1.5">
                <div className="mb-1 font-medium text-slate-500">{cfg.unit.manyCap}</div>
                <div className="flex justify-between gap-6"><span>Прогноз</span><b className="text-blue-700">{formatNumber(row.forecast_calls, 1)}</b></div>
                <div className="flex justify-between gap-6"><span>Возможный прирост</span><b className="text-emerald-700">+{formatNumber(row.incident_uplift_calls, 1)}</b></div>
                <div className="flex justify-between gap-6"><span>С учетом прироста</span><b className="text-slate-900">{formatNumber(row.incident_adjusted_calls ?? row.forecast_calls, 1)}</b></div>
                <div className="flex justify-between gap-6"><span>Факт</span><b className="text-emerald-700">{row.has_actual_report ? formatInt(row.actual_received_calls) : '-'}</b></div>
              </div>
              {cfg.hasWorkloadMinutes ? (
              <div className="rounded-md bg-slate-50 px-2 py-1.5">
                <div className="mb-1 font-medium text-slate-500">Минуты нагрузки</div>
                <div className="flex justify-between gap-6"><span>Прогноз</span><b className="text-blue-700">{formatNumber(row.forecast_workload_minutes, 1)}</b></div>
                <div className="flex justify-between gap-6"><span>Прирост</span><b className="text-emerald-700">+{formatNumber(row.incident_uplift_workload_minutes, 1)}</b></div>
                <div className="flex justify-between gap-6"><span>Факт</span><b className="text-emerald-700">{row.has_actual_report ? formatNumber(row.actual_workload_minutes, 1) : '-'}</b></div>
              </div>
              ) : null}
              <div className="rounded-md bg-slate-50 px-2 py-1.5">
                <div className="mb-1 font-medium text-slate-500">{cfg.unit.rightAxis}</div>
                <div className="flex justify-between gap-6"><span>Прогноз</span><b className="text-blue-700">{formatNumber(row.forecast_fte, 2)}</b></div>
                <div className="flex justify-between gap-6"><span>Прирост</span><b className="text-emerald-700">+{formatNumber(row.incident_uplift_fte, 2)}</b></div>
                <div className="flex justify-between gap-6"><span>Факт</span><b className="text-emerald-700">{row.has_actual_report ? formatNumber(row.actual_report_fte, 2) : '-'}</b></div>
                {!cfg.hasWorkloadMinutes && row.has_actual_report ? (
                  <div className="flex justify-between gap-6"><span>Первый ответ</span><b className="text-slate-900">{formatReplySeconds(row.actual_first_reply_seconds)}</b></div>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="space-y-1 text-slate-600">
              <div className="flex justify-between gap-6"><span>{cfg.unit.tooltipForecastMany}</span><b className="text-slate-900">{formatNumber(row.forecast_calls, 1)}</b></div>
              <div className="flex justify-between gap-6"><span>Возможный прирост</span><b className="text-emerald-700">+{formatNumber(row.incident_uplift_calls, 1)}</b></div>
              <div className="flex justify-between gap-6"><span>{cfg.unit.tooltipAdjustedMany}</span><b className="text-slate-900">{formatNumber(row.incident_adjusted_calls ?? row.forecast_calls, 1)}</b></div>
              {cfg.hasWorkloadMinutes ? (
                <div className="flex justify-between gap-6"><span>Прогноз минут</span><b className="text-blue-700">{formatNumber(row.forecast_workload_minutes, 1)}</b></div>
              ) : null}
              <div className="flex justify-between gap-6"><span>{cfg.unit.tooltipForecastFte}</span><b className="text-blue-700">{formatNumber(row.forecast_fte, 2)}</b></div>
              <div className="flex justify-between gap-6"><span>{cfg.unit.tooltipAdjustedFte}</span><b className="text-emerald-700">{formatNumber(row.incident_adjusted_fte ?? row.forecast_fte, 2)}</b></div>
            </div>
          )}
          {pinnedForecastHour !== null && Number(pinnedForecastHour) === Number(row.hour) ? (
            <div className="mt-2 rounded bg-slate-100 px-2 py-1 font-medium text-slate-600">Срез закреплен</div>
          ) : null}
        </div>
      );
    },
    [cfg, forecastHourlyRows, pinnedForecastHour, selectedForecastHasActualLoad, showForecastActualLoad],
  );

  const visibleMetricCount = (isChat
    ? [
      'metricForecastChats',
      'metricForecastFteHours',
      'metricActualFteHours',
      'metricFteDelta',
      'metricInTargetShare',
      'metricCoveredDays',
    ]
    : [
      'metricOperators',
      'metricWeeklyFte',
      'metricBaseOperators',
      'metricHistoryWarnings',
      'metricLostCalls',
      'metricLossRate',
    ]
  ).filter((key) => displayOptions[key]).length;

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

  const handleOktellSync = async () => {
    const s = String(oktellSyncFrom || '').trim();
    const e = String(oktellSyncTo || '').trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s) || !/^\d{4}-\d{2}-\d{2}$/.test(e)) {
      notify('Выберите период синхронизации', 'error');
      return;
    }
    if (e < s) {
      notify('Дата окончания должна быть не раньше даты начала', 'error');
      return;
    }
    const days = Math.round((new Date(`${e}T00:00:00`) - new Date(`${s}T00:00:00`)) / 86400000) + 1;
    if (!Number.isFinite(days) || days < 1 || days > 31) {
      notify('Период синхронизации не может быть больше 31 дня', 'error');
      return;
    }
    setIsOktellSyncing(true);
    try {
      const response = await axios.post(
        `${apiRoot}/api/resource_fte/sync_oktell`,
        s === e ? { date: s } : { date_from: s, date_to: e },
        { headers: buildHeaders() }
      );
      const sync = response.data?.sync || {};
      notify(`${response.data?.message || 'Синхронизировано из Oktell'} (дней ${sync.days_count ?? 0}, часов ${sync.hours_count ?? 0})`);
      setIsOktellSyncModalOpen(false);
      const uploaded = Array.isArray(sync.uploaded_dates) ? sync.uploaded_dates : [];
      if (uploaded.length) setSelectedDate(uploaded[uploaded.length - 1]);
      await fetchOverview();
    } catch (error) {
      const data = error?.response?.data || {};
      notify(data.error || 'Не удалось синхронизировать данные из Oktell', 'error');
    } finally {
      setIsOktellSyncing(false);
    }
  };

  const handleRecalculate = async () => {
    setIsRecalculating(true);
    try {
      const response = await axios.post(
        `${apiRoot}${cfg.apiPrefix}/recalculate`,
        {},
        { headers: buildHeaders() },
      );
      if (isChat) {
        const payload = response.data || {};
        notify(`Пересчитано дней: ${formatInt(payload.days)}, часов: ${formatInt(payload.rows)}`);
      } else {
        notify('Прогноз пересчитан');
      }
      await fetchOverview();
      if (!isChat) await fetchDay(selectedDate);
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось пересчитать прогноз', 'error');
    } finally {
      setIsRecalculating(false);
    }
  };

  const handleSaveSettings = async () => {
    try {
      // У чата свой список полей: answer_rate/occ/ur/shift_rounding здесь нет, а
      // общий объект настроек утянул бы в запрос вводные линии.
      const body = isChat ? {
        target_reply_seconds: settingsDraft?.target_reply_seconds,
        target_first_reply_seconds: settingsDraft?.target_first_reply_seconds,
        capacity_manual: settingsDraft?.capacity_manual,
        shrinkage_coeff: settingsDraft?.shrinkage_coeff,
        weekly_hours_per_operator: settingsDraft?.weekly_hours_per_operator,
        base_weeks: settingsDraft?.base_weeks,
        fte_rounding: settingsDraft?.fte_rounding,
        week_start: selectedForecastWeekStart || undefined,
      } : settingsDraft;
      const response = await axios.put(`${apiRoot}${cfg.apiPrefix}/settings`, body, {
        headers: buildHeaders({
          'Content-Type': 'application/json',
        }),
      });
      notify('Настройки сохранены');
      if (isChat) {
        // Витрину из ответа брать нельзя: PUT считает свои 7 дней, а календари
        // показывают выбранный период — цифры одного отрезка встали бы под
        // датами другого. Перезапрашиваем витрину выбранными периодами.
        if (response.data?.settings) setSettingsDraft({ ...response.data.settings });
        await fetchOverview();
      } else {
        setOverview(response.data?.overview || overview);
        setSettingsDraft(response.data?.settings || settingsDraft);
        await fetchDay(selectedDate);
      }
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось сохранить настройки', 'error');
    }
  };

  // Замер кривой первого ответа: ёмкость чата выводится из цели, а не вводится руками.
  const [isFittingFirstReply, setIsFittingFirstReply] = useState(false);
  const handleFitFirstReplyCurve = async () => {
    setIsFittingFirstReply(true);
    try {
      const response = await axios.post(
        `${apiRoot}${cfg.apiPrefix}/fit_first_reply`,
        {},
        { headers: buildHeaders() },
      );
      const payload = response.data || {};
      if (!payload.fitted) {
        notify(payload.reason === 'NO_CHAT_DATA'
          ? 'Замерить не по чему: чатов за период нет'
          : `Точек для замера мало: ${formatInt(payload.points)}`, 'error');
      } else if (payload.target_unreachable) {
        notify('Замер выполнен: цель первого ответа на этой кривой недостижима', 'error');
      } else {
        notify(`Кривая замерена по ${formatInt(payload.points)} часам, связь ${formatNumber(payload.correlation, 2)}`);
      }
      await fetchOverview();
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось замерить кривую', 'error');
    } finally {
      setIsFittingFirstReply(false);
    }
  };

  const shiftForecastPeriod = (deltaWeeks) => {
    if (!selectedForecastWeekStart) return;
    // Только addDaysIso: приведение к UTC уводило неделю на сутки назад.
    const start = addDaysIso(selectedForecastWeekStart, deltaWeeks * 7);
    const end = selectedForecastPeriodEnd
      ? addDaysIso(selectedForecastPeriodEnd, deltaWeeks * 7)
      : addDaysIso(start, 6);
    setSelectedForecastWeekStart(start);
    setSelectedForecastPeriodEnd(end);
    setSelectedForecastDate(start);
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
  const operatorAvailabilityCacheKey = [
    forecastPeriodStart || '',
    forecastPeriodEnd || '',
    availabilityDirectionIds.join(','),
  ].join('|');
  const operatorAvailabilityDetailsPayload = operatorAvailabilityDetailsByKey[operatorAvailabilityCacheKey] || null;
  // Витрина линии приносит доступность вместе с прогнозом; у чата её отдаёт
  // отдельная ручка, и разбивку по ставкам надо пересобрать: ручка считает ВСЕ
  // ставки направления, включая 0,5, которой в чате нет. Из-за этого «Есть
  // сейчас» и «Доступно» показывали два разных числа про один штат.
  const availabilityBase = cfg.rates ? (operatorAvailabilityDetailsPayload || {}) : nextWeekForecast;
  const restrictedAvailability = useMemo(() => {
    if (!cfg.rates) return null;
    const rows = Array.isArray(operatorAvailabilityDetailsPayload?.periodAvailableOperatorRates)
      ? operatorAvailabilityDetailsPayload.periodAvailableOperatorRates
      : [];
    if (!rows.length) return null;
    return rows.reduce((acc, row) => {
      const rate = Number(row.rate || 0);
      const count = Number(row.count || 0);
      const fte = Number(row.fte ?? rate * count);
      const totalCount = Number(row.total_count ?? count);
      if (isChatRate(rate)) {
        acc.fte += fte;
        acc.count += count;
        acc.totalCount += totalCount;
      } else {
        acc.excludedFte += fte;
        acc.excludedCount += count;
      }
      return acc;
    }, { fte: 0, count: 0, totalCount: 0, excludedFte: 0, excludedCount: 0 });
  }, [cfg.rates, operatorAvailabilityDetailsPayload]);
  const periodAvailableOperatorFte = restrictedAvailability
    ? restrictedAvailability.fte
    : Number(availabilityBase.periodAvailableOperatorFte ?? nextWeekForecast.currentOperatorFte ?? 0);
  const periodAvailableOperatorCount = restrictedAvailability
    ? restrictedAvailability.count
    : Number(availabilityBase.periodAvailableOperatorCount ?? 0);
  const periodOperatorCount = restrictedAvailability
    ? restrictedAvailability.totalCount
    : Number(availabilityBase.periodOperatorCount ?? periodAvailableOperatorCount);
  const periodPartialOperatorCount = Number(availabilityBase.periodPartialOperatorCount ?? 0);
  const periodUnavailableOperatorCount = Number(availabilityBase.periodUnavailableOperatorCount ?? 0);
  const periodAvailableOperatorFteGap = restrictedAvailability
    ? restrictedAvailability.fte - Number(nextWeekForecast.operatorsWithShrinkage || 0)
    : Number(
      availabilityBase.periodAvailableOperatorFteGap ?? (
        periodAvailableOperatorFte - Number(nextWeekForecast.operatorsWithShrinkage || 0)
      ),
    );
  const operatorDetailsForecast = useMemo(() => {
    const merged = operatorAvailabilityDetailsPayload
      ? { ...nextWeekForecast, ...operatorAvailabilityDetailsPayload }
      : nextWeekForecast;
    if (!cfg.rates) return merged;
    // Люди вне ставок направления помечаются незасчитанными — иначе сумма
    // вкладов в модальном окне не сходилась бы с «Доступно» на карточке.
    return {
      ...merged,
      periodOperatorAvailabilityDetails: (Array.isArray(merged.periodOperatorAvailabilityDetails)
        ? merged.periodOperatorAvailabilityDetails
        : []).map((operator) => (
        isChatRate(operator?.rate) ? operator : { ...operator, included: false, fteContribution: 0 }
      )),
      periodAvailableOperatorFte,
      periodAvailableOperatorCount,
      periodAvailableOperatorFteGap,
    };
  }, [
    cfg.rates,
    nextWeekForecast,
    operatorAvailabilityDetailsPayload,
    periodAvailableOperatorCount,
    periodAvailableOperatorFte,
    periodAvailableOperatorFteGap,
  ]);

  const fetchOperatorAvailabilityDetails = useCallback(async () => {
    if (!apiRoot || !forecastPeriodStart || !forecastPeriodEnd) return null;
    if (operatorAvailabilityDetailsByKey[operatorAvailabilityCacheKey]) {
      setOperatorDetailsError('');
      return operatorAvailabilityDetailsByKey[operatorAvailabilityCacheKey];
    }
    setIsOperatorDetailsLoading(true);
    setOperatorDetailsError('');
    try {
      const response = await axios.get(`${apiRoot}${cfg.apiPrefix}/operator_availability`, {
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
    cfg,
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

  useEffect(() => {
    // Витрина чата не приносит доступность внутри прогноза — запрашиваем сразу,
    // иначе карточка «Чатники» откроется с нулями.
    if (!cfg.rates) return;
    fetchOperatorAvailabilityDetails();
  }, [cfg.rates, fetchOperatorAvailabilityDetails]);

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
      <div className={`${activeDashboardView === 'schedule_planner' ? 'relative' : 'sticky top-0'} z-20 border-b border-slate-200 bg-white/95 px-4 py-4 backdrop-blur md:px-6`}>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <h1 className={`text-2xl font-semibold text-slate-950${isChat ? ' flex items-center gap-2' : ''}`}>
              {isChat ? <MessageSquare className="h-5 w-5 text-blue-600" aria-hidden="true" /> : null}
              {cfg.title}
            </h1>
            {cfg.subtitle ? (
              <p className="mt-1 max-w-3xl text-sm text-slate-600">{cfg.subtitle}</p>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {activeDashboardView === 'overview' || activeDashboardView === 'losses' ? (
              <div className="w-full sm:w-[330px]">
                <CalendarPicker
                  mode="range"
                  label={cfg.historyPickerLabel}
                  startValue={dateFrom}
                  endValue={dateTo}
                  onRangeChange={(start, end) => {
                    setDateFrom(start);
                    setDateTo(end);
                  }}
                  loadedDates={loadedReportDates}
                  hint={cfg.historyPickerHint}
                />
              </div>
            ) : null}
            {cfg.hasUpload ? (
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
            ) : null}
            {cfg.hasOktellSync ? (
            <div className="w-full sm:w-[240px]">
              <button
                type="button"
                onClick={() => {
                  setOktellSyncFrom(_yesterdayIso());
                  setOktellSyncTo(_yesterdayIso());
                  setIsOktellSyncModalOpen(true);
                }}
                className="flex h-14 w-full items-center justify-between gap-3 rounded-xl border-2 border-slate-200 bg-white px-4 text-left text-sm shadow-sm transition hover:border-slate-300 hover:bg-slate-50"
              >
                <span className="min-w-0">
                  <span className="block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Из телефонии</span>
                  <span className="block truncate font-semibold text-slate-900">Синхронизация с Oktell</span>
                </span>
                <RefreshCw size={17} className={`shrink-0 text-sky-600 ${isOktellSyncing ? 'animate-spin' : ''}`} />
              </button>
            </div>
            ) : null}
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

      {cfg.hasUpload && isUploadModalOpen && (
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

      {cfg.hasOktellSync && isOktellSyncModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border-2 border-slate-200 bg-white px-5 py-7 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-base font-semibold text-slate-950">
                  <RefreshCw size={19} className="text-sky-600" />
                  Синхронизация с Oktell
                </div>
                <p className="mt-1 text-sm text-slate-500">Часовая статистика входящих за выбранный период (до 31 дня). Прогноз пересчитается автоматически.</p>
              </div>
              <button
                type="button"
                onClick={() => setIsOktellSyncModalOpen(false)}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border-2 border-slate-200 text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
              >
                <X size={16} />
              </button>
            </div>

            <div className="mt-6">
              <CalendarPicker
                mode="range"
                label="Период синхронизации"
                startValue={oktellSyncFrom}
                endValue={oktellSyncTo}
                onRangeChange={(start, end) => {
                  setOktellSyncFrom(start);
                  setOktellSyncTo(end);
                }}
                loadedDates={loadedReportDates}
                hint="точка = есть отчет"
              />
            </div>

            <div className="mt-7 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button
                type="button"
                onClick={() => setIsOktellSyncModalOpen(false)}
                className="inline-flex h-12 items-center justify-center rounded-xl border-2 border-slate-200 bg-white px-5 text-sm font-semibold text-slate-700 transition hover:bg-slate-100"
              >
                Отмена
              </button>
              <button
                type="button"
                onClick={handleOktellSync}
                disabled={isOktellSyncing}
                className="inline-flex h-12 items-center justify-center gap-2 rounded-xl bg-sky-600 px-5 text-sm font-semibold text-white shadow-sm transition hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-sky-300"
              >
                <RefreshCw size={16} className={isOktellSyncing ? 'animate-spin' : ''} />
                {isOktellSyncing ? 'Синхронизация...' : 'Синхронизировать'}
              </button>
            </div>
          </div>
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
            {cfg.tabs.map((tab) => {
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

        {activeDashboardView !== 'settings' && activeDashboardView !== 'next_week' && activeDashboardView !== 'schedule_planner' && activeDashboardView !== 'oktell_billing' && visibleMetricCount > 0 && (
          <div className={`grid gap-3 md:grid-cols-2 ${visibleMetricCount >= 5 ? 'xl:grid-cols-6' : visibleMetricCount >= 4 ? 'xl:grid-cols-4' : 'xl:grid-cols-3'}`}>
            {isChat ? (
              <>
                {displayOptions.metricForecastChats ? (
                  <StatCard
                    icon={MessageSquare}
                    label="Прогноз чатов"
                    value={formatInt(nextWeekForecast.periodCalls)}
                    hint={`Период ${formatDate(nextWeekForecast.period_start)} — ${formatDate(nextWeekForecast.period_end)}`}
                    tone="blue"
                  />
                ) : null}
                {displayOptions.metricForecastFteHours ? (
                  <StatCard
                    icon={TrendingUp}
                    label="Чатнико-часы прогноза"
                    value={formatNumber(nextWeekForecast.periodFteHours, 1)}
                    hint="Объём ÷ ёмкость, по часам"
                    tone="blue"
                  />
                ) : null}
                {displayOptions.metricActualFteHours ? (
                  <StatCard
                    icon={Clock3}
                    label="Факт чатнико-часов"
                    value={formatNumber(chatOverviewSummary.online, 1)}
                    hint="Онлайн-сегменты чатников за период истории"
                    tone="emerald"
                  />
                ) : null}
                {displayOptions.metricFteDelta ? (
                  <StatCard
                    icon={ShieldAlert}
                    label="Разница с прогнозом"
                    value={formatSignedNumber(chatOverviewSummary.online - chatOverviewSummary.need, 1)}
                    hint="Факт минус потребность за период истории"
                    tone={chatOverviewSummary.online - chatOverviewSummary.need < -0.5 ? 'rose' : 'emerald'}
                  />
                ) : null}
                {displayOptions.metricInTargetShare ? (
                  <StatCard
                    icon={Target}
                    label="Первый ответ в цель"
                    value={formatPercent(chatOverviewSummary.inTargetShare)}
                    hint={`Цель ${describeChatTarget(overview?.settings?.target_first_reply_seconds || 60)} до первой реплики`}
                    tone={chatOverviewSummary.inTargetShare < 0.8 ? 'amber' : 'emerald'}
                  />
                ) : null}
                {displayOptions.metricCoveredDays ? (
                  <StatCard
                    icon={CalendarDays}
                    label="Дней с чатами"
                    value={formatInt(chatOverviewSummary.days)}
                    hint="В выбранном периоде истории"
                    tone="slate"
                  />
                ) : null}
              </>
            ) : null}
            {!isChat && displayOptions.metricOperators && (
              <StatCard
                icon={TrendingUp}
                label="Прогноз FTE периода"
                value={formatNumber(overviewPeriodSummary.forecastFteTotal, 1)}
                hint="Сумма прогнозных FTE по загруженным дням"
                tone="blue"
              />
            )}
            {!isChat && displayOptions.metricWeeklyFte && (
              <StatCard icon={Users} label="Факт FTE периода" value={formatNumber(overviewPeriodSummary.actualFteTotal, 1)} hint="Из разговорной нагрузки отчетов, без смен" tone="emerald" />
            )}
            {!isChat && displayOptions.metricBaseOperators && (
              <StatCard
                icon={Clock3}
                label="Разница FTE"
                value={formatSignedNumber(overviewPeriodSummary.fteDelta, 1)}
                hint="Факт минус прогноз за период"
                tone={overviewPeriodSummary.fteDelta < -0.5 ? 'rose' : overviewPeriodSummary.fteDelta > 0.5 ? 'emerald' : 'slate'}
              />
            )}
            {!isChat && displayOptions.metricHistoryWarnings && (
              <StatCard icon={CalendarDays} label="Дни с отчетами" value={overviewPeriodSummary.days} hint="В выбранном периоде анализа" tone="slate" />
            )}
            {!isChat && displayOptions.metricLostCalls && (
              <StatCard icon={PhoneMissed} label="Потерянные звонки" value={formatInt(periodLossSummary.totalLost)} hint={`Принято: ${formatInt(periodLossSummary.totalAccepted)}`} tone="rose" />
            )}
            {!isChat && displayOptions.metricLossRate && (
              <StatCard icon={ShieldAlert} label="Доля потерь" value={formatPercent(periodLossSummary.lossRate)} hint={periodLossSummary.worstDay ? `Пик: ${formatDate(periodLossSummary.worstDay.report_date)}` : 'За выбранный период'} tone={periodLossSummary.lossRate > 0.08 ? 'rose' : 'amber'} />
            )}
          </div>
        )}

        {activeDashboardView === 'overview' && (
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">{cfg.overviewTrendTitle}</h2>
                <p className="text-sm text-slate-500">{cfg.overviewTrendText}</p>
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
                    <Tooltip content={<OverviewTrendTooltip config={cfg.trendTooltipConfig} />} />
                    {displayOptions[cfg.trendKeys.calls] && <Bar yAxisId="left" dataKey="calls" fill="#bfdbfe" radius={[4, 4, 0, 0]} />}
                    {cfg.hasLosses && displayOptions.chartLosses && <Bar yAxisId="left" dataKey="lost" fill="#fecdd3" radius={[4, 4, 0, 0]} />}
                    {displayOptions[cfg.trendKeys.forecastFte] && <Line yAxisId="right" type="monotone" dataKey="forecastFte" stroke="#2563eb" strokeWidth={2} dot={false} />}
                    {displayOptions[cfg.trendKeys.actualFte] && <Line yAxisId="right" type="monotone" dataKey="actualFte" stroke="#059669" strokeWidth={2} dot={false} />}
                    {cfg.hasLosses && displayOptions.chartLossRate && <Line yAxisId="right" type="monotone" dataKey="lossRate" stroke="#e11d48" strokeWidth={2} dot={false} />}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <EmptyState
                title="Нет данных для сводки"
                text={cfg.hasUpload
                  ? 'Загрузите первый ежедневный CSV, чтобы увидеть динамику.'
                  : 'За выбранный период данных не нашлось. Выберите другой отрезок в календаре.'}
              />
            )}
          </section>
        )}

        {activeDashboardView === 'overview' && (
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">Прирост и выдержка прогноза</h2>
                <p className="mt-1 max-w-2xl text-sm text-slate-500">Последние 6 дней до текущего дня формируют риск, а ближайшие 7 дней показывают уже построенный прирост.</p>
              </div>
              <div className="shrink-0 text-sm text-slate-500 sm:text-right">
                Источник
                <div className="font-medium text-slate-600">{incidentRiskProfile.source_start ? formatDate(incidentRiskProfile.source_start) : '—'} — {incidentRiskProfile.source_end ? formatDate(incidentRiskProfile.source_end) : '—'}</div>
              </div>
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
              <div className="rounded-2xl bg-slate-50 p-4">
                <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                  <span className={`h-1.5 w-1.5 rounded-full ${incidentRiskSummary.overloadDayCount > 0 ? 'bg-rose-500' : 'bg-emerald-500'}`} aria-hidden="true" />
                  Выдержка
                </div>
                <div className={`mt-2 text-[26px] font-semibold leading-none tabular-nums ${incidentRiskSummary.overloadDayCount > 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                  {formatInt(incidentRiskSummary.heldDayCount)} / {formatInt(incidentRiskSummary.sourceDayCount)}
                </div>
                <div className="mt-1.5 text-xs text-slate-500">дней без почасового превышения</div>
              </div>
              <div className="rounded-2xl bg-slate-50 p-4">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Факт − прогноз</div>
                <div className={`mt-2 text-[26px] font-semibold leading-none tabular-nums ${incidentRiskSummary.totalDeltaCalls > 0 ? 'text-rose-600' : incidentRiskSummary.totalDeltaCalls < 0 ? 'text-emerald-600' : 'text-slate-900'}`}>{formatSignedNumber(incidentRiskSummary.totalDeltaCalls, 0)}</div>
                <div className="mt-1.5 text-xs text-slate-500">{formatInt(incidentRiskSummary.totalActualCalls)} факт · {formatInt(incidentRiskSummary.totalForecastCalls)} прогноз</div>
              </div>
              <div className="rounded-2xl bg-slate-50 p-4">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Превышение</div>
                <div className="mt-2 text-[26px] font-semibold leading-none tabular-nums text-rose-600">+{formatInt(incidentRiskSummary.totalPositiveDeltaCalls)}</div>
                <div className="mt-1.5 text-xs text-slate-500">только часы выше прогноза</div>
              </div>
              <div className="rounded-2xl bg-slate-50 p-4">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{cfg.hasUpliftProjection ? 'Прирост 7 дней' : 'Прирост периода'}</div>
                <div className="mt-2 text-[26px] font-semibold leading-none tabular-nums text-emerald-600">+{formatInt(upliftPeriodCalls)}</div>
                <div className="mt-1.5 text-xs text-slate-500">{cfg.unit.many} · {formatDate(upliftWindowStart)} — {formatDate(upliftWindowEnd)}</div>
              </div>
              <div className="col-span-2 rounded-2xl bg-slate-50 p-4 md:col-span-1">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{cfg.hasUpliftProjection ? 'Доп. FTE' : 'Доп. чатнико-часы'}</div>
                <div className="mt-2 text-[26px] font-semibold leading-none tabular-nums text-emerald-600">+{formatNumber(upliftPeriodFteHours, 1)}</div>
                <div className="mt-1.5 text-xs text-slate-500">{cfg.hasUpliftProjection ? 'FTE-ч на ближайшие 7 дней' : 'на окно прироста'}</div>
              </div>
            </div>

            <div className={`mt-5 grid gap-4 ${cfg.hasUpliftProjection ? 'xl:grid-cols-2' : ''}`}>
              <div className="min-w-0 rounded-2xl bg-slate-50 p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <ShieldAlert size={16} className="text-slate-400" />
                    Последние 6 дней
                  </div>
                  <span className="text-xs text-slate-500">ближайшие дни имеют больший вес</span>
                </div>
                {incidentRiskDailyData.length ? (
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={incidentRiskDailyData} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
                        <XAxis dataKey="dateLabel" tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} />
                        <YAxis yAxisId="left" tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} width={36} />
                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} width={36} />
                        <Tooltip content={<IncidentRiskTooltip />} cursor={{ fill: 'rgba(148, 163, 184, 0.08)' }} />
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

              {cfg.hasUpliftProjection ? (
              <div className="min-w-0 rounded-2xl bg-slate-50 p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <TrendingUp size={16} className="text-slate-400" />
                    Построенный прирост на 7 дней
                  </div>
                  <span className="text-xs text-slate-500">от текущего дня, без влияния периода</span>
                </div>
                {incidentProjectionData.length ? (
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={incidentProjectionData} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
                        <XAxis dataKey="dateLabel" tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} />
                        <YAxis yAxisId="left" tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} width={36} />
                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} width={36} />
                        <Tooltip content={<IncidentProjectionTooltip />} cursor={{ fill: 'rgba(148, 163, 184, 0.08)' }} />
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
              ) : null}
            </div>

            <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
              <div className="min-w-0">
                <div className="mb-3 text-sm font-semibold text-slate-900">Дни, которые сформировали риск</div>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {incidentRiskDailyData.length ? incidentRiskDailyData.map((row) => (
                    <div key={row.date} className="rounded-2xl bg-slate-50 p-3.5">
                      <div className="flex items-center justify-between gap-2">
                        <div className="font-semibold text-slate-900">{formatDate(row.date)}</div>
                        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${row.status === 'overload' ? 'bg-rose-100 text-rose-700' : 'bg-emerald-100 text-emerald-700'}`}>
                          <span className={`h-1.5 w-1.5 rounded-full ${row.status === 'overload' ? 'bg-rose-500' : 'bg-emerald-500'}`} aria-hidden="true" />
                          {row.status === 'overload' ? 'не выдержал' : 'выдержал'}
                        </span>
                      </div>
                      <div className="mt-2.5 grid grid-cols-3 gap-2 text-xs text-slate-500">
                        <span>прогноз <b className="font-semibold text-slate-900">{formatNumber(row.forecastCalls, 0)}</b></span>
                        <span>факт <b className="font-semibold text-slate-900">{formatNumber(row.actualCalls, 0)}</b></span>
                        <span>вес <b className="font-semibold text-slate-900">{formatNumber(row.weight, 0)}</b></span>
                      </div>
                      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-200/80">
                        <div className={`h-full rounded-full ${row.status === 'overload' ? 'bg-rose-500' : 'bg-emerald-500'}`} style={{ width: `${Math.min(100, Math.max(6, row.positiveHourShare * 100))}%` }} />
                      </div>
                    </div>
                  )) : (
                    <div className="rounded-2xl bg-slate-50 p-6 text-center text-sm text-slate-500 sm:col-span-2 xl:col-span-3">Нет загруженных дней для расчёта риска.</div>
                  )}
                </div>
              </div>

              <div className="min-w-0">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <Clock3 size={16} className="text-slate-400" />
                  Часы прироста
                </div>
                <div className="space-y-2.5">
                  {incidentRiskTopHours.length ? incidentRiskTopHours.map((row) => (
                    <div key={row.hour} className="rounded-2xl bg-slate-50 p-3.5">
                      <div className="flex items-center justify-between gap-3">
                        <div className="font-semibold text-slate-900">{row.hourLabel}</div>
                        <div className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-700">+{formatNumber(row.weightedDeltaCalls, 1)} {cfg.unit.short}</div>
                      </div>
                      <div className="mt-2.5 grid grid-cols-3 gap-2 text-xs text-slate-500">
                        <span>риск <b className="font-semibold text-emerald-700">{formatPercent(row.growthRatio, 0)}</b></span>
                        <span>надёжн. <b className="font-semibold text-slate-900">{formatPercent(row.confidence, 0)}</b></span>
                        <span>дней <b className="font-semibold text-slate-900">{formatInt(row.positiveSourceCount)}/{formatInt(row.sourceCount)}</b></span>
                      </div>
                    </div>
                  )) : (
                    <div className="rounded-2xl bg-slate-50 p-6 text-center text-sm text-slate-500">За последние 6 дней нет часов, где факт был выше прогноза.</div>
                  )}
                </div>
              </div>
            </div>
          </section>
        )}

        {isChat && activeDashboardView === 'overview' && (
          <div className="grid gap-6 xl:grid-cols-2">
            <section className="min-w-0 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="text-lg font-semibold text-slate-950">Профиль по дням недели</h2>
              <p className="text-sm text-slate-500">Средний объём и пиковый час по базовым неделям.</p>
              {(overview?.weekday_profile || []).length ? (
                <div className="mt-4 overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                        <th className="px-3 py-2">День</th>
                        <th className="px-3 py-2 text-right">В среднем чатов</th>
                        <th className="px-3 py-2 text-right">Пик в часе</th>
                        <th className="px-3 py-2 text-right">Дней в выборке</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(overview?.weekday_profile || []).map((row) => (
                        <tr key={row.weekday} className="border-b border-slate-100">
                          <td className="px-3 py-2 font-medium text-slate-800">{row.short}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{formatInt(row.avg_chats)}</td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {row.peak_hour === null || row.peak_hour === undefined ? '—' : `${String(row.peak_hour).padStart(2, '0')}:00`}
                          </td>
                          <td className="px-3 py-2 text-right text-slate-500 tabular-nums">{formatInt(row.days_in_sample)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState title="Профиль пуст" text="Для базовых недель нет данных по чатам." />
              )}
            </section>

            <section className="min-w-0 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="text-lg font-semibold text-slate-950">Каналы</h2>
              <p className="text-sm text-slate-500">Откуда приходят обращения за базовые недели.</p>
              {(overview?.channels || []).length ? (
                <div className="mt-4 overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                        <th className="px-3 py-2">Канал</th>
                        <th className="px-3 py-2 text-right">Чатов</th>
                        <th className="px-3 py-2 text-right">Доля</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(overview?.channels || []).map((channel) => (
                        <tr key={channel.channel} className="border-b border-slate-100">
                          <td className="px-3 py-2 text-slate-800">{channel.channel}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{formatInt(channel.chats)}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{formatPercent(channel.share)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState title="Каналов нет" text="За базовые недели обращения не найдены." />
              )}
            </section>
          </div>
        )}

        {cfg.hasLosses && activeDashboardView === 'losses' && (
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

        {isChat && activeDashboardView === 'losses' && (
          <div className="space-y-6">
            <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-slate-950">Аналитика чатов</h2>
                  <p className="text-sm text-slate-500">
                    «В цель» здесь и везде на вкладке — про ПЕРВЫЙ ответ клиенту, единственную
                    измеримую по нашей базе метрику сервиса.
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <div className="inline-flex w-fit rounded-lg border border-slate-200 bg-slate-50 p-1">
                    {[
                      ['volume', 'Факт/Прогноз'],
                      ['reply', 'Первый ответ вне цели'],
                    ].map(([mode, label]) => (
                      <button
                        key={mode}
                        type="button"
                        onClick={() => setChatsChartMode(mode)}
                        className={`h-8 rounded-md px-3 text-xs font-semibold transition ${
                          chatsChartMode === mode
                            ? 'bg-slate-900 text-white shadow-sm'
                            : 'text-slate-600 hover:bg-white hover:text-slate-900'
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className="w-full sm:w-[300px]">
                    <CalendarPicker
                      mode="range"
                      label="Период аналитики"
                      startValue={analyticsFrom}
                      endValue={analyticsTo}
                      onRangeChange={(start, end) => {
                        setAnalyticsFrom(start);
                        setAnalyticsTo(end);
                      }}
                      loadedDates={loadedReportDates}
                      hint={cfg.historyPickerHint}
                    />
                  </div>
                </div>
              </div>

              <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
                <StatCard
                  icon={MessageSquare}
                  label="Чатов"
                  value={formatInt(analyticsTotals.chats)}
                  hint={`${formatInt(analytics?.range?.days)} дн. в периоде`}
                  tone="blue"
                  emphasis="compact"
                />
                <StatCard
                  icon={Target}
                  label="Первый ответ в цель"
                  value={formatPercent(analyticsTotals.in_target_share)}
                  hint={`Цель ${describeChatTarget(analyticsTotals.target_first_reply_seconds || chatTargetFirstSeconds)}`}
                  tone={Number(analyticsTotals.in_target_share || 0) < 0.8 ? 'amber' : 'emerald'}
                  emphasis="compact"
                />
                <StatCard
                  icon={AlertTriangle}
                  label="Без ответа"
                  value={formatInt(analyticsTotals.no_reply)}
                  hint="Оператор так и не написал первым"
                  tone="rose"
                  emphasis="compact"
                />
                <StatCard
                  icon={Timer}
                  label="Среднее время первого ответа"
                  value={formatReplySeconds(analyticsTotals.avg_first_reply_seconds)}
                  hint="По отвеченным чатам периода"
                  tone="slate"
                  emphasis="compact"
                />
                <StatCard
                  icon={Clock3}
                  label="Факт чатнико-часов"
                  value={formatNumber(analyticsTotals.actual_online_hours, 1)}
                  hint="Онлайн-сегменты чатников"
                  tone="emerald"
                  emphasis="compact"
                />
              </div>

              <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-3">
                  <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <BarChart3 size={16} />
                    {chatsChartMode === 'volume' ? 'Объём по дням: факт против прогноза' : 'Первый ответ по дням'}
                  </div>
                  {analyticsChartData.length ? (
                    <div className="h-72">
                      <ResponsiveContainer width="100%" height="100%">
                        <ComposedChart data={analyticsChartData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                          <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                          <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                          <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                          <Tooltip formatter={(value, name) => [formatNumber(value, 0), name]} />
                          {chatsChartMode === 'volume' ? (
                            <>
                              <Bar yAxisId="left" dataKey="forecastChats" name="Прогноз" fill="#bfdbfe" radius={[4, 4, 0, 0]} />
                              <Bar yAxisId="left" dataKey="chats" name="Факт" fill="#22c55e" radius={[4, 4, 0, 0]} />
                            </>
                          ) : (
                            <>
                              <Bar yAxisId="left" dataKey="inTarget" name="В цель" stackId="reply" fill="#bbf7d0" radius={[0, 0, 0, 0]} />
                              <Bar yAxisId="left" dataKey="outOfTarget" name="Вне цели" stackId="reply" fill="#fecdd3" radius={[4, 4, 0, 0]} />
                              <Line yAxisId="right" type="monotone" dataKey="firstReply" name="Среднее, с" stroke="#e11d48" strokeWidth={2} dot={false} />
                            </>
                          )}
                        </ComposedChart>
                      </ResponsiveContainer>
                    </div>
                  ) : (
                    <EmptyState
                      title="Нет данных по чатам"
                      text={isAnalyticsLoading ? 'Загружаем период…' : 'За выбранный период обращений не нашлось.'}
                    />
                  )}
                  {chatsChartMode === 'volume' ? (
                    <div className="mt-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">
                      Прогноз берётся из сохранённой истории пересчётов. Пока «Пересчитать» не нажимали,
                      столбец прогноза пуст — это не дефект, а незаполненная история.
                    </div>
                  ) : null}
                </div>

                <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <ShieldAlert size={16} />
                    Топ часов риска
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    Худшая доля НА ОБЪЁМЕ: провалить ночной час с тремя чатами дешевле, чем дневной с сотней.
                  </p>
                  <div className="mt-4 space-y-3">
                    {analyticsRiskHours.length ? analyticsRiskHours.map((row) => (
                      <div key={row.hour} className="rounded-lg border border-slate-200 bg-white p-3">
                        <div className="flex items-center justify-between gap-3">
                          <div className="font-semibold text-slate-900 tabular-nums">{row.hour_label}</div>
                          <div className="rounded-md bg-rose-100 px-2 py-1 text-xs font-semibold text-rose-700">
                            {formatPercent(1 - Number(row.in_target_share || 0))}
                          </div>
                        </div>
                        <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-slate-500">
                          <span>чаты: <b className="text-slate-800">{formatInt(row.chats)}</b></span>
                          <span>без ответа: <b className="text-rose-700">{formatInt(row.no_reply)}</b></span>
                          <span>ответ: <b className="text-slate-800">{formatReplySeconds(row.avg_first_reply_seconds)}</b></span>
                        </div>
                        <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
                          <div className="h-full rounded-full bg-rose-500" style={{ width: `${Math.min(100, (1 - Number(row.in_target_share || 0)) * 100)}%` }} />
                        </div>
                      </div>
                    )) : (
                      <div className="rounded-lg border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
                        За период нет часов с провалом первого ответа.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </section>

            <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
              <section className="min-w-0 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <h2 className="text-lg font-semibold text-slate-950">По дням</h2>
                <p className="text-sm text-slate-500">Объём, первый ответ и отработанные чатнико-часы.</p>
                <div className="mt-4 overflow-x-auto">
                  <table className="min-w-full text-sm tabular-nums">
                    <thead>
                      <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                        <th className="px-3 py-2">День</th>
                        <th className="px-3 py-2">Дата</th>
                        <th className="px-3 py-2 text-right">Чаты</th>
                        <th className="px-3 py-2 text-right">Прогноз</th>
                        <th className="px-3 py-2 text-right">В цель</th>
                        <th className="px-3 py-2 text-right">Без ответа</th>
                        <th className="px-3 py-2 text-right">Первый ответ</th>
                        <th className="px-3 py-2 text-right">Чатнико-часы</th>
                      </tr>
                    </thead>
                    <tbody>
                      {analyticsDays.map((row) => (
                        <tr key={row.date} className="border-b border-slate-100">
                          <td className="px-3 py-2 font-medium text-slate-800">{row.short}</td>
                          <td className="px-3 py-2 text-slate-600">{formatDate(row.date)}</td>
                          <td className="px-3 py-2 text-right">{formatInt(row.chats)}</td>
                          <td className="px-3 py-2 text-right text-blue-700">{formatNumber(row.forecast_chats, 0)}</td>
                          <td className={`px-3 py-2 text-right font-semibold ${Number(row.in_target_share || 0) < 0.8 ? 'text-rose-700' : 'text-emerald-700'}`}>
                            {formatPercent(row.in_target_share)}
                          </td>
                          <td className="px-3 py-2 text-right text-rose-700">{formatInt(row.no_reply)}</td>
                          <td className="px-3 py-2 text-right">{formatReplySeconds(row.avg_first_reply_seconds)}</td>
                          <td className="px-3 py-2 text-right">{formatNumber(row.actual_online_hours, 1)}</td>
                        </tr>
                      ))}
                      {!analyticsDays.length ? (
                        <tr>
                          <td colSpan={8} className="px-3 py-6 text-center text-sm text-slate-500">
                            {isAnalyticsLoading ? 'Загружаем период…' : 'За выбранный период данных нет'}
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <h2 className="text-lg font-semibold text-slate-950">Каналы периода</h2>
                <p className="text-sm text-slate-500">Доля обращений по источникам.</p>
                <div className="mt-4 space-y-2">
                  {analyticsChannels.length ? analyticsChannels.map((channel) => (
                    <div key={channel.channel} className="rounded-lg bg-slate-50 p-3">
                      <div className="flex items-center justify-between gap-3 text-sm">
                        <span className="font-medium text-slate-800">{channel.channel}</span>
                        <span className="tabular-nums text-slate-600">{formatInt(channel.chats)} · {formatPercent(channel.share)}</span>
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200">
                        <div className="h-full rounded-full bg-blue-500" style={{ width: `${Math.min(100, Number(channel.share || 0) * 100)}%` }} />
                      </div>
                    </div>
                  )) : (
                    <div className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500">
                      За выбранный период каналов нет.
                    </div>
                  )}
                </div>
              </section>
            </div>
          </div>
        )}

        {cfg.hasBilling && activeDashboardView === 'oktell_billing' && (
          <>
            <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                <div className="flex items-start gap-3">
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-sky-500 to-blue-600 text-white shadow-lg shadow-sky-500/20">
                    <Receipt size={20} />
                  </span>
                  <div>
                    <h2 className="text-lg font-semibold text-slate-950">{cfg.billing.title}</h2>
                    <p className="text-sm text-slate-500">{cfg.billing.text}</p>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <div className="w-full sm:w-[300px]">
                    <CalendarPicker
                      mode="range"
                      label="Период отчета"
                      startValue={billingFrom}
                      endValue={billingTo}
                      onRangeChange={(start, end) => {
                        setBillingFrom(start);
                        setBillingTo(end);
                      }}
                    />
                  </div>
                  <div className="w-full sm:w-[220px]">
                    <TimeRangePicker
                      label="Время"
                      startValue={billingTimeFrom}
                      endValue={billingTimeTo}
                      onRangeChange={(start, end) => {
                        setBillingTimeFrom(start);
                        setBillingTimeTo(end);
                      }}
                    />
                  </div>
                  <button
                    type="button"
                    onClick={buildBillingReport}
                    disabled={isBillingLoading || Boolean(billingRangeError)}
                    className="inline-flex h-14 items-center gap-2 rounded-xl bg-sky-600 px-5 text-sm font-semibold text-white shadow-sm transition hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-sky-300"
                  >
                    <RefreshCw size={16} className={isBillingLoading ? 'animate-spin' : ''} />
                    {isBillingLoading ? 'Загрузка...' : 'Сформировать'}
                  </button>
                  {cfg.hasBillingExportTypes && (
                  <select
                    value={billingExportType}
                    onChange={(event) => setBillingExportType(event.target.value)}
                    disabled={isBillingExporting || isBillingLoading}
                    aria-label="Вид выгрузки Excel"
                    title="Выберите формат Excel-отчёта"
                    className="h-14 max-w-[250px] rounded-xl border-2 border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 shadow-sm outline-none transition hover:border-slate-300 focus:border-sky-400 focus:ring-2 focus:ring-sky-100 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <option value="general">Общая (текущая)</option>
                    <option value="efficiency">По эффективности операторов</option>
                  </select>
                  )}
                  <button
                    type="button"
                    onClick={exportBillingExcel}
                    disabled={
                      isBillingExporting
                      || isBillingLoading
                      || Boolean(billingRangeError)
                      || (billingExportType === 'general' && !billingReport)
                    }
                    title={billingExportType === 'efficiency'
                      ? 'Скачать эффективность операторов по группам'
                      : 'Скачать текущий разрез в Excel за применённый период'}
                    className="inline-flex h-14 items-center gap-2 rounded-xl border-2 border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <FileDown size={16} className={isBillingExporting ? 'animate-pulse text-emerald-600' : 'text-emerald-600'} />
                    {isBillingExporting ? 'Выгрузка...' : 'Excel'}
                  </button>
                </div>
              </div>
              <div className="mt-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div className="inline-flex max-w-full w-fit overflow-x-auto rounded-xl bg-slate-100 p-1">
                  {cfg.billing.modes.map((item) => (
                    <button
                      key={item.key}
                      type="button"
                      onClick={() => setBillingMode(item.key)}
                      className={`h-9 rounded-lg px-4 text-sm font-semibold transition ${
                        billingMode === item.key
                          ? 'bg-white text-slate-950 shadow-sm'
                          : 'text-slate-500 hover:text-slate-800'
                      }`}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
                  <span className="tabular-nums">{billingPeriodDays > 0 ? `${billingPeriodDays} дн. · ${formatDate(billingFrom)} — ${formatDate(billingTo)}` : 'Период не выбран'}</span>
                  <span className="tabular-nums">время {billingTimeFrom}–{billingTimeTo} включительно</span>
                  {cfg.hasBillingExportTypes && billingExportType === 'efficiency' ? (
                    <span>Excel по эффективности считается за полные дни; фильтр времени не применяется</span>
                  ) : null}
                  {cfg.billing.modeHint ? (
                    <span>{cfg.billing.modeHint(billingMode, billingSlSeconds)}</span>
                  ) : billingMode === 'operator' ? (
                    <span>OCC — разговоры и обработка ко всему времени в системе; UTZ — время без пауз</span>
                  ) : billingMode === 'detail' ? (
                    <span>Одна строка — один звонок; на странице 25 звонков</span>
                  ) : (
                    <span>SL — отвечено за ≤ {billingReport?.sl_threshold_seconds ?? 20} сек ожидания в очереди ко всем звонкам, попавшим в очередь</span>
                  )}
                  {billingRangeError ? <span className="font-semibold text-rose-600">{billingRangeError}</span> : null}
                </div>
              </div>
            </section>

            {billingError ? (
              <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
                <AlertTriangle size={16} className="shrink-0" />
                {billingError}
              </div>
            ) : null}

            {isBillingLoading && (
              <div className="flex min-h-[220px] items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white p-8 text-sm text-slate-500 shadow-sm">
                <RefreshCw size={16} className="animate-spin" />
                {cfg.billing.loadingText}
              </div>
            )}

            {!isBillingLoading && billingReport && (
              (billingMode === 'detail' && billingDetailRows.length > 0)
              || (billingMode !== 'detail' && billingTotals && billingDays.length > 0)
            ) && (
              <>
                {billingMode !== 'detail' && (!cfg.hasBillingTalkTime ? (
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                    <StatCard icon={MessageSquare} label="Поступило" value={formatInt(billingTotals.chats)} hint="Обращения за период" tone="blue" />
                    <StatCard icon={CheckCircle2} label="Обслужено" value={formatInt(billingTotals.answered)} hint="Получили ответ оператора" tone="emerald" />
                    <StatCard icon={ShieldAlert} label="Потеряно" value={formatInt(billingTotals.no_reply)} hint="Остались без ответа" tone="rose" />
                    <StatCard
                      icon={Clock3}
                      label="Ср. первый ответ"
                      value={billingChatReplySeconds === null ? '—' : formatDurationHms(billingChatReplySeconds)}
                      hint="Реакция на обращение"
                      tone="slate"
                    />
                    <StatCard
                      icon={Target}
                      label="AR"
                      value={billingChatArRatio === null ? '—' : formatPercent(billingChatArRatio, 1)}
                      hint="Доля отвеченных обращений"
                      tone={billingChatArRatio !== null && billingChatArRatio >= 0.9 ? 'emerald' : billingChatArRatio !== null && billingChatArRatio >= 0.75 ? 'amber' : 'rose'}
                    />
                    <StatCard
                      icon={TrendingUp}
                      label="SL"
                      value={billingChatSlRatio === null ? '—' : formatPercent(billingChatSlRatio, 1)}
                      hint={`Первый ответ за ≤ ${billingSlSeconds} сек от поступивших`}
                      tone={billingChatSlRatio !== null && billingChatSlRatio >= 0.8 ? 'emerald' : billingChatSlRatio !== null && billingChatSlRatio >= 0.6 ? 'amber' : 'rose'}
                    />
                  </div>
                ) : billingMode === 'operator' ? (
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                    <StatCard icon={CheckCircle2} label="Обслужено" value={formatInt(billingTotals.served)} hint="Входящие, отвеченные оператором" tone="emerald" />
                    <StatCard icon={Clock3} label="Время разговора" value={formatDurationHms(billingTotals.talk_in_seconds)} hint={`Исходящие ${formatDurationHms(billingTotals.talk_out_seconds)}`} tone="blue" />
                    <StatCard icon={PhoneCall} label="АТТ" value={billingAttSeconds === null ? '—' : formatDurationHms(billingAttSeconds)} hint="Ср. время разговора" tone="slate" />
                    <StatCard icon={ListChecks} label="АНТ" value={billingAhtSeconds === null ? '—' : formatDurationHms(billingAhtSeconds)} hint="Разговор + удержание + постобработка" tone="slate" />
                    <StatCard
                      icon={TrendingUp}
                      label="OCC"
                      value={billingOperatorTotals?.occ == null ? '—' : formatPercent(billingOperatorTotals.occ, 1)}
                      hint="Занятость операторов"
                      tone="amber"
                    />
                    <StatCard
                      icon={Users}
                      label="UTZ"
                      value={billingOperatorTotals?.utz == null ? '—' : formatPercent(billingOperatorTotals.utz, 1)}
                      hint="Время без пауз"
                      tone="slate"
                    />
                  </div>
                ) : (
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                    <StatCard icon={PhoneCall} label="Поступило" value={formatInt(billingTotals.arrived)} hint="Звонки, дошедшие до очереди" tone="blue" />
                    <StatCard icon={CheckCircle2} label="Обслужено" value={formatInt(billingTotals.served)} hint="Отвечены оператором" tone="emerald" />
                    <StatCard icon={PhoneMissed} label="Потеряно" value={formatInt(billingTotals.lost)} hint="Не дождались ответа" tone="rose" />
                    <StatCard
                      icon={ShieldAlert}
                      label="AR"
                      value={billingArRatio === null ? '—' : formatPercent(billingArRatio, 1)}
                      hint="Доля потерянных звонков"
                      tone={billingArRatio !== null && billingArRatio > 0.1 ? 'rose' : billingArRatio !== null && billingArRatio > 0.05 ? 'amber' : 'emerald'}
                    />
                    <StatCard
                      icon={TrendingUp}
                      label="SL"
                      value={billingSlRatio === null ? '—' : formatPercent(billingSlRatio, 1)}
                      hint={`Ответ за ≤ ${billingReport?.sl_threshold_seconds ?? 20} сек от поступивших`}
                      tone={billingSlRatio !== null && billingSlRatio >= 0.8 ? 'emerald' : billingSlRatio !== null && billingSlRatio >= 0.6 ? 'amber' : 'rose'}
                    />
                    <StatCard icon={Clock3} label="Время разговора" value={formatDurationHms(billingTotals.talk_seconds)} hint={`Общее время ${formatDurationHms(billingTotals.total_seconds)}`} tone="slate" />
                  </div>
                ))}

                {billingMode === 'detail' ? (
                  <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
                    <div className="border-b border-slate-100 px-4 py-3">
                      <h3 className="text-base font-semibold text-slate-950">{cfg.billing.detailTitle}</h3>
                      <p className="text-xs tabular-nums text-slate-500">
                        {formatDate(billingReport.date_from)} — {formatDate(billingReport.date_to)} · {billingReport.time_from}–{billingReport.time_to}
                      </p>
                      <p className="mt-1 text-xs text-slate-500">
                        {cfg.billing.detailNote}
                      </p>
                    </div>
                    <BillingRowsTable rows={billingDetailRows} />
                    <div className="flex flex-col gap-3 border-t border-slate-200 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className="text-xs tabular-nums text-slate-500">
                        Строки {formatInt(billingDetailPageStart)}–{formatInt(billingDetailPageEnd)} из {formatInt(billingDetailTotal)}
                      </div>
                      <div className="flex items-center justify-between gap-3 sm:justify-end">
                        <button
                          type="button"
                          onClick={() => fetchBillingReport('detail', {
                            page: billingDetailCurrentPage - 1,
                            snapshotId: billingReport.snapshot_id,
                          })}
                          disabled={billingDetailCurrentPage <= 1}
                          className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <ChevronLeft size={15} />
                          Назад
                        </button>
                        <span className="min-w-16 text-center text-sm font-semibold tabular-nums text-slate-700">
                          {formatInt(billingDetailCurrentPage)} / {formatInt(billingDetailTotalPages)}
                        </span>
                        <button
                          type="button"
                          onClick={() => fetchBillingReport('detail', {
                            page: billingDetailCurrentPage + 1,
                            snapshotId: billingReport.snapshot_id,
                          })}
                          disabled={billingDetailCurrentPage >= billingDetailTotalPages}
                          className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          Вперед
                          <ChevronRight size={15} />
                        </button>
                      </div>
                    </div>
                  </section>
                ) : (
                  <>
                    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
                      <div className="border-b border-slate-100 px-4 py-3">
                        <h3 className="text-base font-semibold text-slate-950">
                          {cfg.billing.summaryTitle(billingMode)}
                        </h3>
                        <p className="text-xs tabular-nums text-slate-500">{formatDate(billingReport.date_from)} — {formatDate(billingReport.date_to)} · {billingReport.time_from}–{billingReport.time_to}</p>
                      </div>
                      {billingMode === 'operator' ? (
                        <BillingPeopleTable rows={billingReport.operators || []} totals={billingTotals} totalsLabel="Итого за период" />
                      ) : (
                        <BillingSummaryTable rows={billingReport.parks || []} totals={billingTotals} totalsLabel="Итого за период" mode={billingMode} />
                      )}
                    </section>

                    <div className="flex items-center justify-between">
                      <h3 className="text-base font-semibold text-slate-950">По дням</h3>
                      <button
                        type="button"
                        onClick={() => setAllBillingDays(!billingAllExpanded)}
                        className="text-sm font-semibold text-blue-700 transition hover:text-blue-800"
                      >
                        {billingAllExpanded ? 'Свернуть все' : 'Развернуть все'}
                      </button>
                    </div>

                    <div className="space-y-3">
                      {billingDays.map((day) => {
                        const expanded = billingExpandedDays.has(day.date);
                        const dayAr = billingMode === 'operator' ? null : cfg.hasBillingTalkTime
                          ? safeRatio(day.totals?.lost, day.totals?.arrived)
                          : safeRatio(day.totals?.answered, day.totals?.chats);
                        const daySl = billingMode === 'operator' ? null : cfg.hasBillingTalkTime
                          ? safeRatio(day.totals?.served_sl, day.totals?.arrived)
                          : safeRatio(day.totals?.answered_sl, day.totals?.chats);
                        const dayOcc = billingMode === 'operator' ? billingOperatorActivity(day.totals || {}).occ : null;
                        return (
                          <section key={day.date} className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
                            <button
                              type="button"
                              onClick={() => toggleBillingDay(day.date)}
                              className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-slate-50"
                            >
                              <div className="flex min-w-0 items-center gap-3">
                                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-sky-50 text-sky-700">
                                  <CalendarDays size={17} />
                                </span>
                                <div className="min-w-0">
                                  <div className="truncate text-sm font-semibold capitalize text-slate-950">{billingDayLabel(day.date)}</div>
                                  <div className="truncate text-xs tabular-nums text-slate-500">
                                    {cfg.billing.daySummary(billingMode, day)}
                                  </div>
                                </div>
                              </div>
                              <div className="flex shrink-0 items-center gap-2">
                                {billingMode === 'operator' ? (
                                  <span className="hidden rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold tabular-nums text-slate-700 sm:inline">
                                    OCC {dayOcc === null ? '—' : formatPercent(dayOcc, 1)}
                                  </span>
                                ) : (
                                  <>
                                    <span className={`hidden rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold tabular-nums sm:inline ${cfg.billing.arClass(dayAr)}`}>
                                      AR {dayAr === null ? '—' : formatPercent(dayAr, 1)}
                                    </span>
                                    <span className={`hidden rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold tabular-nums sm:inline ${billingSlClass(daySl)}`}>
                                      SL {daySl === null ? '—' : formatPercent(daySl, 1)}
                                    </span>
                                  </>
                                )}
                                {expanded ? <ChevronUp size={17} className="text-slate-400" /> : <ChevronDown size={17} className="text-slate-400" />}
                              </div>
                            </button>
                            {expanded ? (
                              <div className="border-t border-slate-100">
                                {billingMode === 'operator' ? (
                                  <BillingPeopleTable rows={day.operators || []} totals={day.totals} totalsLabel="Итого за день" />
                                ) : (
                                  <BillingSummaryTable rows={day.parks || []} totals={day.totals} totalsLabel="Итого за день" mode={billingMode} />
                                )}
                              </div>
                            ) : null}
                          </section>
                        );
                      })}
                    </div>
                  </>
                )}
              </>
            )}

            {!isBillingLoading && billingReport && (
              billingMode === 'detail' ? billingDetailRows.length === 0 : billingDays.length === 0
            ) && (
              <EmptyState
                title="Нет данных за выбранный период"
                text={cfg.billing.emptyText}
              />
            )}

            {!isBillingLoading && !billingReport && !billingError && (
              <EmptyState
                title="Отчет еще не сформирован"
                text={cfg.billing.idleText}
              />
            )}
          </>
        )}

        {activeDashboardView === 'schedule_planner' && (
          <ResourceSchedulePlanner
            apiRoot={apiRoot}
            apiPrefix={cfg.apiPrefix}
            enableShiftAuction={cfg.hasShiftAuction}
            buildHeaders={buildHeaders}
            selectedWeekStart={selectedForecastWeekStart}
            selectedPeriodEnd={selectedForecastPeriodEnd}
            onWeekStartChange={(value) => setSelectedForecastWeekStart(value)}
            onPeriodChange={(start, end) => {
              setSelectedForecastWeekStart(start);
              setSelectedForecastPeriodEnd(end);
              setSelectedForecastDate(start);
            }}
            weekPicker={cfg.hasWeekPicker ? (
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
            ) : undefined}
            notify={notify}
            onOpenShiftAuction={cfg.hasShiftAuction ? onOpenShiftAuction : undefined}
          />
        )}

        {(activeDashboardView === 'settings' || activeDashboardView === 'next_week') && (
        <div className={`grid gap-6 ${activeDashboardView === 'settings' && !isChat ? 'xl:grid-cols-[320px_minmax(0,1fr)]' : 'xl:grid-cols-1'}`}>
          {activeDashboardView === 'settings' && isChat && (
          <aside className="space-y-4">
            <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-1 flex items-center gap-2 text-lg font-semibold text-slate-950">
                <Target size={18} />
                Две цели по сервису
              </div>
              <p className="text-sm text-slate-500">
                Слева — рычаг расчёта, справа — то, что мы реально меряем по базе. Путать их нельзя.
              </p>
              <div className="mt-4 grid gap-4 xl:grid-cols-2">
                <label className="block rounded-xl border-2 border-amber-300 bg-amber-50 p-4">
                  <span className="text-sm font-semibold text-amber-900">Среднее время внутри чата, сек</span>
                  <p className="mt-1 text-xs leading-snug text-amber-800">
                    Главная вводная: из неё выводится ёмкость. Ниже цель — больше людей.
                    Факта по ней в базе нет, он живёт только в API Chat2Desk.
                  </p>
                  <input
                    type="number"
                    step={10}
                    min={30}
                    max={3600}
                    value={settingsDraft?.target_reply_seconds ?? ''}
                    onChange={(event) => setSettingsDraft((current) => ({
                      ...(current || {}),
                      target_reply_seconds: event.target.value === '' ? '' : Number(event.target.value),
                    }))}
                    className={`${inputClass} mt-3 w-full border-amber-300 bg-white`}
                  />
                  <span className="mt-2 block text-xs font-medium text-amber-900">
                    Сейчас: {describeChatTarget(settingsDraft?.target_reply_seconds)}
                  </span>
                </label>

                <label className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                  <span className="text-sm font-semibold text-slate-900">Первый ответ (реакция), сек</span>
                  <p className="mt-1 text-xs leading-snug text-slate-500">
                    Время до первой реплики оператора. Единственная цель, факт по которой
                    есть у нас в базе — по ней и считается «в цель» на вкладке «Чаты».
                  </p>
                  <input
                    type="number"
                    step={10}
                    min={10}
                    max={3600}
                    value={settingsDraft?.target_first_reply_seconds ?? ''}
                    onChange={(event) => setSettingsDraft((current) => ({
                      ...(current || {}),
                      target_first_reply_seconds: event.target.value === '' ? '' : Number(event.target.value),
                    }))}
                    className={`${inputClass} mt-3 w-full`}
                  />
                  <span className="mt-2 block text-xs font-medium text-slate-600">
                    Сейчас: {describeChatTarget(settingsDraft?.target_first_reply_seconds)}
                  </span>
                </label>
              </div>

              <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                      <Gauge size={16} className="text-slate-500" />
                      Ёмкость: {formatNumber(chatCapacityExplain.used ?? chatCapacityPerHourValue, 2)} чатов в час на человека
                    </div>
                    <p className="mt-1 max-w-2xl text-xs leading-snug text-slate-600">
                      {chatCapacityExplain.source === 'manual'
                        ? 'Задана вручную и перебивает вывод из цели.'
                        : chatCapacityExplain.source === 'first_reply'
                          ? 'Связала цель первого ответа — она жёстче «ответа внутри чата».'
                          : 'Выведена из цели «ответ внутри чата» по замеренной кривой.'}
                      {' '}Из цели: {formatNumber(chatCapacityExplain.derived, 2)}
                      {chatCapacityExplain.derived_first_reply
                        ? `; по первому ответу: ${formatNumber(chatCapacityExplain.derived_first_reply, 2)}`
                        : '; кривая первого ответа ещё не замерена'}.
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {chatCapacityIsManual ? (
                      <>
                        <input
                          type="number"
                          step={0.5}
                          min={0.5}
                          max={60}
                          value={settingsDraft?.capacity_manual ?? ''}
                          onChange={(event) => setSettingsDraft((current) => ({
                            ...(current || {}),
                            capacity_manual: event.target.value === '' ? '' : Number(event.target.value),
                          }))}
                          className={`${inputClass} w-32`}
                        />
                        <button
                          type="button"
                          onClick={() => setSettingsDraft((current) => ({ ...(current || {}), capacity_manual: null }))}
                          className="inline-flex h-10 items-center rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100"
                        >
                          Вернуть вывод из цели
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setSettingsDraft((current) => ({
                          ...(current || {}),
                          capacity_manual: Number(chatCapacityExplain.used ?? chatCapacityPerHourValue),
                        }))}
                        className="inline-flex h-10 items-center rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100"
                      >
                        Задать вручную
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={handleFitFirstReplyCurve}
                      disabled={isFittingFirstReply}
                      className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
                    >
                      <RefreshCw size={16} className={isFittingFirstReply ? 'animate-spin' : ''} />
                      {isFittingFirstReply ? 'Замеряем…' : 'Замерить кривую первого ответа'}
                    </button>
                  </div>
                </div>

                {chatCapacityExplain.first_reply_target_unreachable ? (
                  <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
                    <div className="flex items-center gap-1.5 font-semibold">
                      <AlertTriangle size={14} />
                      Цель первого ответа не достигается наращиванием людей
                    </div>
                    <p className="mt-1 text-xs leading-snug">
                      Замер показал: даже в самой разгруженной полосе первый ответ не укладывается
                      в {describeChatTarget(chatTargetFirstSeconds)}. Причина не в общей нехватке штата, а в
                      ночных часах с одним-двумя чатниками, шаблонах и переключении между каналами.
                      Число мы не подгоняем — рычагом остаётся «ответ внутри чата».
                    </p>
                  </div>
                ) : null}

                <div className="mt-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800">
                  Обработки требуют 100 % чатов. На линии допустимы 5 % потерь, в чате — нет:
                  обращение не «теряется», оно висит открытым, пока клиент не получит ответ.
                  Скидки на непринятые в чатовой модели нет — это объяснение, а не поле ввода.
                </div>
              </div>
            </section>

            <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-1 text-lg font-semibold text-slate-950">Вводные расчёта</div>
              <p className="text-sm text-slate-500">Пересчёт людей из чатнико-часов.</p>
              <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <label className="block">
                  <span className="text-xs font-medium text-slate-600">Коэффициент усушки</span>
                  <input
                    type="number"
                    step={0.05}
                    min={0.1}
                    max={1}
                    value={settingsDraft?.shrinkage_coeff ?? ''}
                    onChange={(event) => setSettingsDraft((current) => ({
                      ...(current || {}),
                      shrinkage_coeff: event.target.value === '' ? '' : Number(event.target.value),
                    }))}
                    className={`${inputClass} mt-1 w-full`}
                  />
                  <span className="mt-1 block text-[11px] leading-snug text-slate-500">Отпуска, больничные, обучение. 0,9 — как на линии</span>
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-slate-600">Часов в неделю на человека</span>
                  <input
                    type="number"
                    step={1}
                    min={1}
                    max={168}
                    value={settingsDraft?.weekly_hours_per_operator ?? ''}
                    onChange={(event) => setSettingsDraft((current) => ({
                      ...(current || {}),
                      weekly_hours_per_operator: event.target.value === '' ? '' : Number(event.target.value),
                    }))}
                    className={`${inputClass} mt-1 w-full`}
                  />
                  <span className="mt-1 block text-[11px] leading-snug text-slate-500">Норма для пересчёта чатнико-часов в людей</span>
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-slate-600">Недель в базе прогноза</span>
                  <input
                    type="number"
                    step={1}
                    min={1}
                    max={8}
                    value={settingsDraft?.base_weeks ?? ''}
                    onChange={(event) => setSettingsDraft((current) => ({
                      ...(current || {}),
                      base_weeks: event.target.value === '' ? '' : Number(event.target.value),
                    }))}
                    className={`${inputClass} mt-1 w-full`}
                  />
                  <span className="mt-1 block text-[11px] leading-snug text-slate-500">Сколько последних ПОЛНЫХ недель усредняем по дням недели</span>
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-slate-600">Округление потребности</span>
                  <select
                    value={settingsDraft?.fte_rounding || 'half'}
                    onChange={(event) => setSettingsDraft((current) => ({ ...(current || {}), fte_rounding: event.target.value }))}
                    className={`${inputClass} mt-1 w-full`}
                  >
                    {CHAT_FTE_ROUNDING_LABELS.map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                  <span className="mt-1 block text-[11px] leading-snug text-slate-500">Как показывать потребность часа при раскладке смен</span>
                </label>
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={handleSaveSettings}
                  disabled={!settingsDraft}
                  className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:opacity-60"
                >
                  <Save size={16} />
                  Сохранить и пересчитать
                </button>
                <span className="text-xs text-slate-500">
                  Ёмкость пересчитается сама: она следует за целью, а не вводится рядом с ней.
                </span>
              </div>
            </section>
          </aside>
          )}
          {activeDashboardView === 'settings' && !isChat && (
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
                  {cfg.hasDirectionPicker ? (
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
                  ) : null}
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
                    onClick={() => setDisplayOptions({ ...cfg.defaultDisplayOptions })}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                  >
                    <RefreshCw size={16} />
                    Сбросить
                  </button>
                </div>
                <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  {cfg.displayGroups.map((group) => (
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
                    <h2 className="text-lg font-semibold text-slate-950">{cfg.forecastTitle}</h2>
                    <p className="text-sm text-slate-500">{cfg.forecastText}</p>
                  </div>
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    {isChat ? (
                      <>
                        <button
                          type="button"
                          onClick={() => shiftForecastPeriod(-1)}
                          className="inline-flex h-10 items-center rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                        >
                          ← Неделя назад
                        </button>
                        <button
                          type="button"
                          onClick={() => shiftForecastPeriod(1)}
                          className="inline-flex h-10 items-center rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                        >
                          Неделя вперёд →
                        </button>
                      </>
                    ) : null}
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
                          label={cfg.unit.fteHoursCap}
                          value={formatNumber(nextWeekForecast.periodFteHours ?? nextWeekForecast.weeklyFteHours, 1)}
                          hint={isChat
                            ? `${formatInt(nextWeekForecast.periodDays)} дн. · ${formatInt(nextWeekForecast.periodCalls)} ${cfg.unit.many}`
                            : `${formatInt(nextWeekForecast.periodDays || (nextWeekForecast.days || []).length)} дн. в периоде`}
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
                        label={cfg.unit.operators}
                        fteWord={cfg.unit.fteWord}
                        excludedCount={restrictedAvailability?.excludedCount || 0}
                        excludedFte={restrictedAvailability?.excludedFte || 0}
                        onOpen={openOperatorDetails}
                      />
                    ) : null}
                  </div>
                ) : null}

                {(displayOptions.forecastKpiUplift || displayOptions.forecastKpiAht || displayOptions.forecastKpiAnswerRate || displayOptions.forecastKpiOccUr || displayOptions.forecastKpiShrinkage || displayOptions.forecastKpiCapacity || displayOptions.forecastKpiTarget) ? (
                  <div className="mt-3 grid gap-2 grid-cols-2 md:grid-cols-3 xl:grid-cols-5 [&>*]:min-w-0">
                    {isChat && displayOptions.forecastKpiCapacity ? (
                      <StatCard
                        icon={Gauge}
                        label="Ёмкость"
                        value={`${formatNumber(chatCapacityPerHourValue, 1)} чат/ч`}
                        hint={chatCapacityExplain.source === 'manual'
                          ? 'Задана вручную'
                          : chatCapacityExplain.source === 'first_reply'
                            ? 'Связала цель первого ответа'
                            : 'Выведена из «ответа внутри чата»'}
                        tone="amber"
                        emphasis="compact"
                      />
                    ) : null}
                    {isChat && displayOptions.forecastKpiTarget ? (
                      <StatCard
                        icon={Target}
                        label="Цель ответа"
                        value={describeChatTarget(overview?.settings?.target_reply_seconds)}
                        hint={`Первый ответ ${describeChatTarget(chatTargetFirstSeconds)}`}
                        tone="blue"
                        emphasis="compact"
                      />
                    ) : null}
                    {displayOptions.forecastKpiUplift ? (
                      <StatCard
                        icon={TrendingUp}
                        label="Возможный прирост"
                        value={`+${formatInt(nextWeekForecast.incidentUpliftCalls)} ${cfg.unit.short}`}
                        hint={`+${formatNumber(nextWeekForecast.incidentUpliftFteHours, 1)} ${cfg.unit.fteHoursShort} · ${Number(nextWeekForecast.incidentUplift?.source_day_count || 0)}/6 дн.`}
                        tone="emerald"
                        emphasis="compact"
                      />
                    ) : null}
                    {cfg.hasAht && displayOptions.forecastKpiAht ? (
                      <StatCard
                        icon={Clock3}
                        label="AHT периода"
                        value={formatSeconds(nextWeekForecast.periodAhtSeconds ?? nextWeekForecast.weeklyAhtSeconds)}
                        hint="Среднее по дням"
                        tone="blue"
                        emphasis="compact"
                      />
                    ) : null}
                    {cfg.hasAnswerRate && displayOptions.forecastKpiAnswerRate ? (
                      <StatCard
                        icon={PhoneCall}
                        label="Принято"
                        value={formatPercent(nextWeekForecast.answerRate)}
                        hint="Коэф. периода"
                        tone="slate"
                        emphasis="compact"
                      />
                    ) : null}
                    {cfg.hasOccUr && displayOptions.forecastKpiOccUr ? (
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
                    {cfg.hasWeekPicker ? (
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
                    ) : (
                      <CalendarPicker
                        mode="range"
                        label="Период прогноза"
                        startValue={forecastPeriodStart}
                        endValue={forecastPeriodEnd}
                        onRangeChange={(start, end) => {
                          setSelectedForecastWeekStart(start);
                          setSelectedForecastPeriodEnd(end);
                          setSelectedForecastDate(start);
                        }}
                        loadedDates={loadedReportDates}
                        hint={chatBaseWeekStarts.length
                          ? `База: ${chatBaseWeekStarts.map((item) => formatDate(item).slice(0, 5)).join(', ')}`
                          : undefined}
                      />
                    )}
                    {cfg.hasHistoryPairs && !forecastPeriodComplete ? (
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
                    {!cfg.hasHistoryPairs && chatSkippedBaseWeeks.length ? (
                      <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                        <div className="flex items-center gap-1 font-semibold">
                          <AlertTriangle size={13} />
                          Неполные недели пропущены
                        </div>
                        <div className="mt-1 text-slate-600">
                          {chatSkippedBaseWeeks.map((item) => formatDate(item.week_start)).join(', ')}
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
                          const enoughSources = cfg.hasHistoryPairs
                            ? !profile.insufficient_history
                            : Number(profile.used_source_count || 0) > 1;
                          const accentClass = enoughSources ? 'bg-emerald-500' : 'bg-amber-400';
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
                                  <div className="text-base font-semibold tabular-nums text-slate-950">{formatNumber(forecastFte, cfg.hasHistoryPairs ? 2 : 1)}</div>
                                  <div className="text-[10px] uppercase tracking-wide text-slate-500">{cfg.unit.dayFteCaption}</div>
                                </div>
                              </div>

                              <div className="mt-2.5 flex items-center gap-2 text-xs">
                                <span className="inline-flex min-w-0 items-center gap-1 text-slate-600">
                                  {isChat
                                    ? <MessageSquare size={11} className="shrink-0 text-slate-400" aria-hidden="true" />
                                    : <PhoneCall size={11} className="shrink-0 text-slate-400" aria-hidden="true" />}
                                  <b className="text-slate-900 tabular-nums">{formatInt(profile.forecast_calls)}</b>
                                  <span className="text-slate-400">{cfg.unit.short}</span>
                                </span>
                                {cfg.hasHistoryPairs ? (
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
                                ) : (
                                  <span className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-semibold text-slate-700">
                                    пик {formatNumber(profile.peak_fte, 1)}
                                  </span>
                                )}
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
                                    +{formatInt(profile.incident_uplift_calls)} {cfg.unit.short} · +{formatNumber(profile.incident_uplift_fte, 2)} {cfg.unit.fteWord}
                                  </span>
                                </div>
                              ) : null}

                              {hasActual ? (
                                <div className="mt-2 flex items-center justify-between gap-2 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px]">
                                  <span className="inline-flex items-center gap-1 text-slate-600">
                                    <CheckCircle2 size={11} className="text-emerald-600" aria-hidden="true" />
                                    Факт <b className="text-slate-900 tabular-nums">{formatNumber(actualFte, cfg.hasHistoryPairs ? 2 : 1)}</b>
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
                                {isChat ? 'Потребность по часам' : 'Почасовой FTE'}: {selectedForecastDay.short} · {formatDate(selectedForecastDay.forecast_date)}
                              </h3>
                              <p className="text-sm text-slate-500">
                                {isChat
                                  ? `Час считается как чаты часа ÷ ${formatNumber(chatCapacityPerHourValue, 1)} чатов в час на человека.`
                                  : `Разбивка использует AHT дня ${formatSeconds(selectedForecastDay.forecast_aht_seconds)} и единые коэффициенты.`}
                              </p>
                            </div>
                            <div className="flex flex-wrap gap-2">
                              {cfg.hasHistoryPairs ? (
                                <span className={`inline-flex w-fit items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold ${selectedForecastDay.insufficient_history ? 'bg-amber-50 text-amber-700' : 'bg-emerald-50 text-emerald-700'}`}>
                                  {selectedForecastDay.insufficient_history ? <AlertTriangle size={13} /> : <CheckCircle2 size={13} />}
                                  История {selectedForecastDay.history_count}/2
                                </span>
                              ) : (
                                <span className={`inline-flex w-fit items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold ${selectedForecastHasActualLoad ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
                                  {selectedForecastHasActualLoad ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
                                  {selectedForecastHasActualLoad ? 'Факт дня есть' : 'День ещё не прошёл'}
                                </span>
                              )}
                              {cfg.hasHistoryPairs && showForecastActualLoad ? (
                                <span className={`inline-flex w-fit items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold ${selectedForecastHasActualLoad ? 'bg-blue-50 text-blue-700' : 'bg-slate-100 text-slate-500'}`}>
                                  {selectedForecastHasActualLoad ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
                                  {selectedForecastHasActualLoad ? 'Факт отчета загружен' : 'Факта отчета нет'}
                                </span>
                              ) : null}
                            </div>
                          </div>

                          {isChat ? (
                            <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                              <div className="rounded-lg bg-slate-50 px-3 py-2">
                                <div className="text-xs text-slate-500">Чаты</div>
                                <b className="tabular-nums">{formatInt(selectedForecastDay.forecast_calls)}</b>
                                {selectedForecastHasActualLoad ? (
                                  <div className="mt-1 text-[11px] font-semibold text-emerald-700">факт {formatInt(selectedForecastDay.actual_received_calls)}</div>
                                ) : null}
                              </div>
                              <div className="rounded-lg bg-slate-50 px-3 py-2">
                                <div className="text-xs text-slate-500">Чатнико-часы</div>
                                <b className="tabular-nums">{formatNumber(selectedForecastDay.forecast_daily_fte, 1)}</b>
                                {selectedForecastHasActualLoad ? (
                                  <div className="mt-1 text-[11px] font-semibold text-emerald-700">факт {formatNumber(selectedForecastDay.actual_report_fte, 1)}</div>
                                ) : null}
                              </div>
                              <div className="rounded-lg bg-slate-50 px-3 py-2">
                                <div className="text-xs text-slate-500">Пиковый час</div>
                                <b className="tabular-nums">{selectedForecastPeakHours[0] ? `${String(selectedForecastPeakHours[0].hour).padStart(2, '0')}:00` : '—'}</b>
                              </div>
                              <div className="rounded-lg bg-slate-50 px-3 py-2">
                                <div className="text-xs text-slate-500">Первый ответ в цель</div>
                                <b className="tabular-nums">
                                  {selectedForecastDay.has_actual ? formatPercent(selectedForecastDay.in_target_share) : '—'}
                                </b>
                              </div>
                            </div>
                          ) : showForecastActualLoad && selectedForecastHasActualLoad ? (
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
                                  label={{ value: cfg.unit.leftAxis, angle: -90, position: 'insideLeft', offset: 12, style: { fontSize: 11, fill: '#64748b' } }}
                                />
                                <YAxis
                                  yAxisId="right"
                                  orientation="right"
                                  tick={{ fontSize: 11 }}
                                  label={{ value: cfg.unit.rightAxis, angle: 90, position: 'insideRight', offset: 8, style: { fontSize: 11, fill: '#64748b' } }}
                                />
                                <Tooltip content={<ForecastHourlyTooltip />} />
                                {activeForecastHourLabel ? (
                                  <ReferenceLine yAxisId="left" x={activeForecastHourLabel} stroke={pinnedForecastHour !== null ? '#0f172a' : '#64748b'} strokeDasharray="4 4" />
                                ) : null}
                                {displayOptions[cfg.forecastChartKeys.calls] ? (
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
                                {cfg.hasWorkloadMinutes && displayOptions.forecastChartWorkload ? (
                                  <Line yAxisId="left" type="monotone" dataKey="workload" stroke="#3b82f6" strokeWidth={2} dot={false} activeDot={{ r: 5 }} />
                                ) : null}
                                {isChat && showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastChartActualChats ? (
                                  <Bar yAxisId="left" dataKey="actualCalls" fill="#34d399" radius={[4, 4, 0, 0]} />
                                ) : null}
                                {displayOptions.forecastChartFte ? (
                                  <Line yAxisId="right" type="monotone" dataKey="fte" stroke="#2563eb" strokeWidth={2} dot={false} activeDot={{ r: 5 }} />
                                ) : null}
                                {incidentUpliftAvailable && displayOptions.forecastChartAdjustedFte ? (
                                  <Line yAxisId="right" type="monotone" dataKey="adjustedFte" stroke="#059669" strokeWidth={2} strokeDasharray="4 3" dot={false} activeDot={{ r: 5 }} />
                                ) : null}
                                {cfg.hasWorkloadMinutes && showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastChartActualWorkload ? (
                                  <Line yAxisId="left" type="monotone" dataKey="actualWorkload" stroke="#10b981" strokeWidth={2} dot={false} activeDot={{ r: 5 }} />
                                ) : null}
                                {cfg.hasWorkloadMinutes && showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastChartActualFte ? (
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
                            legendItems={cfg.chartLegend}
                          />
                          {cfg.hasWorkloadMinutes && showForecastActualLoad && !selectedForecastHasActualLoad ? (
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
                                      {cfg.unit.manyCap}
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
                                  {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions[cfg.forecastTableKeys.actualCalls] ? (
                                    <th className="px-3 py-3 text-right">
                                      <span className="inline-flex items-center justify-end gap-1.5">
                                        <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />
                                        Факт {cfg.unit.many}
                                      </span>
                                    </th>
                                  ) : null}
                                  {cfg.hasAht && displayOptions.forecastTableAht ? (
                                    <th className="px-3 py-3 text-right">AHT дня</th>
                                  ) : null}
                                  {cfg.hasWorkloadMinutes && displayOptions.forecastTableWorkload ? (
                                    <th className="px-3 py-3 text-right">Минут нагрузки</th>
                                  ) : null}
                                  {cfg.hasWorkloadMinutes && showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastTableActualWorkload ? (
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
                                      {cfg.unit.needFte}
                                    </span>
                                  </th>
                                  {incidentUpliftAvailable && displayOptions.forecastTableAdjustedFte ? (
                                    <th className="px-3 py-3 text-right">
                                      <span className="inline-flex items-center justify-end gap-1.5">
                                        <span className="inline-block h-2 w-2 rounded-full bg-emerald-600" />
                                        {isChat ? 'С приростом' : 'FTE с приростом'}
                                      </span>
                                    </th>
                                  ) : null}
                                  {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions[cfg.forecastTableKeys.actualFte] ? (
                                    <th className="px-3 py-3 text-right">
                                      <span className="inline-flex items-center justify-end gap-1.5">
                                        <span className="inline-block h-2 w-2 rounded-full bg-emerald-600" />
                                        {isChat ? 'Факт чатнико-часов' : 'Факт FTE'}
                                      </span>
                                    </th>
                                  ) : null}
                                  {isChat && showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastTableFirstReply ? (
                                    <th className="px-3 py-3 text-right">Первый ответ</th>
                                  ) : null}
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-100 bg-white">
                                {forecastHourlyRows.map((row) => {
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
                                      {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions[cfg.forecastTableKeys.actualCalls] ? (
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
                                      {cfg.hasAht && displayOptions.forecastTableAht ? (
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
                                      {cfg.hasWorkloadMinutes && displayOptions.forecastTableWorkload ? (
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
                                      {cfg.hasWorkloadMinutes && showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastTableActualWorkload ? (
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
                                      {showForecastActualLoad && selectedForecastHasActualLoad && displayOptions[cfg.forecastTableKeys.actualFte] ? (
                                        <td className="px-3 py-2 text-right font-semibold text-emerald-700">
                                          {row.has_actual_report ? formatNumber(row.actual_report_fte, 2) : '-'}
                                        </td>
                                      ) : null}
                                      {isChat && showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastTableFirstReply ? (
                                        <td className="px-3 py-2 text-right text-slate-700">
                                          {formatReplySeconds(row.actual_first_reply_seconds)}
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
                                {isChat ? 'Пиковые часы' : 'Пиковые часы прогноз'}
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
                                          <span className="rounded-md bg-blue-100 px-2 py-1 text-xs font-semibold text-blue-800 tabular-nums">{formatNumber(row.forecast_fte, 2)}{cfg.hasWorkloadMinutes ? ' FTE' : ''}</span>
                                        </div>
                                        <div className="mt-2 text-xs text-slate-500 tabular-nums">
                                          {cfg.unit.manyCap}: {formatNumber(row.forecast_calls, 1)}
                                          {cfg.hasWorkloadMinutes ? ` · нагрузка: ${formatNumber(row.forecast_workload_minutes, 1)} мин` : ''}
                                        </div>
                                        <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-valuenow={Math.round(barWidth)} aria-valuemin={0} aria-valuemax={100}>
                                          <div className="h-full rounded-full bg-blue-600 transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${barWidth}%` }} />
                                        </div>
                                      </button>
                                    );
                                  });
                                })()}
                              </div>
                            </div>

                            {cfg.hasActualPeakHours && showForecastActualLoad && selectedForecastHasActualLoad && displayOptions.forecastShowActualPeakHours ? (
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
                        text={cfg.hasUpload
                          ? 'Загрузите исторические отчеты, чтобы построить прогноз выбранного периода.'
                          : 'Для выбранного периода не нашлось базовых недель с чатами. Сдвиньте период или выберите другой отрезок.'}
                        action={cfg.hasUpload ? (
                          <button
                            type="button"
                            onClick={() => setIsUploadModalOpen(true)}
                            className="inline-flex h-10 items-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-300"
                          >
                            <UploadCloud size={16} aria-hidden="true" />
                            Загрузить отчёт
                          </button>
                        ) : undefined}
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
                panelGroups={cfg.forecastPanelGroups}
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
