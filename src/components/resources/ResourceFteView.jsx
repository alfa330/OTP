import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
  AlertTriangle,
  BarChart3,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  Clock3,
  Eye,
  FileUp,
  LayoutDashboard,
  ListChecks,
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
  LineChart,
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

const getForecastHistoryDatesForDay = (forecastDateIso) => [
  addDaysIso(forecastDateIso, -21),
  addDaysIso(forecastDateIso, -14),
];

const isForecastDayHistoryComplete = (forecastDateIso, loadedSet) =>
  getForecastHistoryDatesForDay(forecastDateIso).every((date) => loadedSet.has(date));

const isForecastWeekHistoryComplete = (weekStartIso, loadedSet) =>
  getForecastWeekDates(weekStartIso).every((date) => isForecastDayHistoryComplete(date, loadedSet));

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
  { key: 'day', label: 'День', icon: CalendarDays },
  { key: 'next_week', label: 'Прогнозы', icon: TrendingUp },
  { key: 'losses', label: 'Потери', icon: PhoneMissed },
  { key: 'profiles', label: 'Профили', icon: BarChart3 },
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
  profileCalls: true,
  profileAht: true,
  profileDailyFte: true,
  tableReceived: true,
  tableAccepted: true,
  tableLost: true,
  tableNoAnswer: true,
  tableAvgTalk: true,
  tableAvgWait: true,
  tableForecast: true,
  tablePlan: true,
  tableActual: true,
  tableDelta: true,
  tableComments: true,
};

const DISPLAY_GROUPS = [
  {
    title: 'Карточки',
    items: [
      ['metricOperators', 'Операторы с усушкой'],
      ['metricWeeklyFte', 'Недельная потребность'],
      ['metricBaseOperators', 'Без усушки'],
      ['metricHistoryWarnings', 'Недостаток истории'],
      ['metricLostCalls', 'Потерянные звонки'],
      ['metricLossRate', 'Доля потерь'],
    ],
  },
  {
    title: 'Графики',
    items: [
      ['chartCalls', 'Звонки'],
      ['chartFte', 'Прогноз FTE'],
      ['chartActual', 'Факт FTE'],
      ['chartLosses', 'Потери'],
      ['chartLossRate', 'Доля потерь'],
    ],
  },
  {
    title: 'Профиль дня недели',
    items: [
      ['profileCalls', 'Среднее количество звонков'],
      ['profileAht', 'AHT из истории'],
      ['profileDailyFte', 'Суточная FTE'],
    ],
  },
  {
    title: 'Почасовая таблица',
    items: [
      ['tableReceived', 'Получено'],
      ['tableAccepted', 'Принято'],
      ['tableLost', 'Потеряно'],
      ['tableNoAnswer', '% Неотв'],
      ['tableAvgTalk', 'Средняя длительность'],
      ['tableAvgWait', 'Ожидание'],
      ['tableForecast', 'Прогноз FTE'],
      ['tablePlan', 'План'],
      ['tableActual', 'Факт'],
      ['tableDelta', 'Разница'],
      ['tableComments', 'Комментарии'],
    ],
  },
];

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

const StatCard = ({ icon: Icon, label, value, hint, tone = 'blue' }) => {
  const toneClass = {
    blue: 'bg-blue-50 text-blue-700 ring-blue-100',
    emerald: 'bg-emerald-50 text-emerald-700 ring-emerald-100',
    amber: 'bg-amber-50 text-amber-700 ring-amber-100',
    rose: 'bg-rose-50 text-rose-700 ring-rose-100',
    slate: 'bg-slate-100 text-slate-700 ring-slate-200',
  }[tone];

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
          <div className="mt-2 text-2xl font-semibold text-slate-950">{value}</div>
          {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
        </div>
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ring-1 ${toneClass}`}>
          <Icon size={18} />
        </div>
      </div>
    </div>
  );
};

const EmptyState = ({ title, text }) => (
  <div className="flex min-h-[220px] flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center">
    <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-slate-100 text-slate-500">
      <BarChart3 size={22} />
    </div>
    <h3 className="mt-4 text-base font-semibold text-slate-900">{title}</h3>
    <p className="mt-1 max-w-md text-sm text-slate-500">{text}</p>
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

const WeekForecastPicker = ({ value, onChange, loadedDates = [] }) => {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);
  const loadedSet = useMemo(() => new Set(loadedDates), [loadedDates]);
  const selectedWeekStart = getWeekStartIso(value || getNextWeekStartIso());
  const selectedWeekEnd = addDaysIso(selectedWeekStart, 6);
  const selectedWeekComplete = isForecastWeekHistoryComplete(selectedWeekStart, loadedSet);
  const initialDate = parseIsoDate(selectedWeekStart) || new Date();
  const [visibleMonth, setVisibleMonth] = useState(new Date(initialDate.getFullYear(), initialDate.getMonth(), 1));
  const historyWeeks = getForecastHistoryWeeks(selectedWeekStart);

  useEffect(() => {
    const next = parseIsoDate(selectedWeekStart);
    if (next) setVisibleMonth(new Date(next.getFullYear(), next.getMonth(), 1));
  }, [selectedWeekStart]);

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event) => {
      if (anchorRef.current && !anchorRef.current.contains(event.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [open]);

  const moveMonth = (delta) => {
    setVisibleMonth((current) => new Date(current.getFullYear(), current.getMonth() + delta, 1));
  };

  const calendarDays = useMemo(() => buildCalendarDays(visibleMonth), [visibleMonth]);

  const selectWeek = (iso) => {
    onChange?.(getWeekStartIso(iso));
    setOpen(false);
  };

  return (
    <div ref={anchorRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className={`flex min-h-16 w-full items-center justify-between gap-3 rounded-xl border-2 bg-white px-4 py-3 text-left text-sm shadow-sm transition hover:bg-slate-50 ${
          selectedWeekComplete ? 'border-emerald-200 hover:border-emerald-300' : 'border-slate-200 hover:border-slate-300'
        }`}
      >
        <span className="min-w-0">
          <span className="block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Неделя прогноза</span>
          <span className="block truncate font-semibold text-slate-900">
            {formatDate(selectedWeekStart)} - {formatDate(selectedWeekEnd)}
          </span>
          <span className={`mt-1 inline-flex rounded-md px-2 py-0.5 text-[11px] font-semibold ${
            selectedWeekComplete ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
          }`}>
            {selectedWeekComplete ? 'истории хватает' : 'истории не хватает'}
          </span>
        </span>
        <CalendarDays size={17} className={selectedWeekComplete ? 'shrink-0 text-emerald-600' : 'shrink-0 text-blue-600'} />
      </button>

      <div className="mt-2 rounded-xl border border-slate-200 bg-slate-100 px-3 py-2 text-xs text-slate-600">
        <div className="font-semibold text-slate-700">История для расчета</div>
        <div className="mt-1 grid gap-1">
          {historyWeeks.map((week, index) => (
            <div key={`${week.start}-${week.end}`}>
              {index + 1}. {formatDate(week.start)} - {formatDate(week.end)}
            </div>
          ))}
        </div>
      </div>

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

          <div className="mt-3 grid grid-cols-7 gap-1 text-center text-[11px] font-semibold uppercase text-slate-400">
            {['ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС'].map((day) => (
              <div key={day} className="py-1">{day}</div>
            ))}
          </div>

          <div className="mt-1 grid grid-cols-7 gap-1">
            {calendarDays.map((date) => {
              const iso = toIsoDate(date);
              const weekStart = getWeekStartIso(iso);
              const weekComplete = isForecastWeekHistoryComplete(weekStart, loadedSet);
              const dayComplete = isForecastDayHistoryComplete(iso, loadedSet);
              const isOutside = date.getMonth() !== visibleMonth.getMonth();
              const isSelectedWeek = weekStart === selectedWeekStart;
              return (
                <button
                  key={iso}
                  type="button"
                  title={`${formatDate(iso)}: ${dayComplete ? 'истории хватает' : 'истории не хватает'}`}
                  onClick={() => selectWeek(iso)}
                  className={`relative flex h-10 items-center justify-center rounded-lg border text-sm font-semibold transition ${
                    isSelectedWeek
                      ? weekComplete
                        ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                        : 'border-blue-200 bg-blue-50 text-blue-800'
                      : dayComplete
                        ? 'border-emerald-100 bg-emerald-50 text-emerald-700 hover:border-emerald-300'
                        : isOutside
                          ? 'border-transparent text-slate-300 hover:bg-slate-50'
                          : 'border-transparent text-slate-700 hover:bg-slate-100'
                  }`}
                >
                  {date.getDate()}
                  {weekComplete && (
                    <span className={`absolute top-1 h-1.5 w-1.5 rounded-full ${isSelectedWeek ? 'bg-emerald-600' : 'bg-emerald-500'}`} />
                  )}
                </button>
              );
            })}
          </div>

          <div className="mt-3 rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-600">
            Зеленый день означает, что для него загружены оба исторических дня: минус 21 и минус 14 дней.
          </div>
        </div>
      )}
    </div>
  );
};

const ResourceFteView = ({ apiBaseUrl, withAccessTokenHeader, user, showToast }) => {
  const apiRoot = String(apiBaseUrl || '').replace(/\/+$/, '');
  const fileInputRef = useRef(null);
  const showToastRef = useRef(showToast);
  const authHeaderRef = useRef(withAccessTokenHeader);
  const [overview, setOverview] = useState(null);
  const [selectedDate, setSelectedDate] = useState('');
  const [selectedDay, setSelectedDay] = useState(null);
  const [activeWeekday, setActiveWeekday] = useState(0);
  const [dateFrom, setDateFrom] = useState(monthStartIso);
  const [dateTo, setDateTo] = useState(todayIso);
  const [uploadDate, setUploadDate] = useState(todayIso);
  const [uploadFile, setUploadFile] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isRecalculating, setIsRecalculating] = useState(false);
  const [settingsDraft, setSettingsDraft] = useState(null);
  const [savingHourKey, setSavingHourKey] = useState('');
  const [activeDashboardView, setActiveDashboardView] = useState('overview');
  const [displayOptions, setDisplayOptions] = useState(loadDisplayOptions);
  const [selectedForecastWeekStart, setSelectedForecastWeekStart] = useState(() => getNextWeekStartIso());
  const [selectedForecastWeekday, setSelectedForecastWeekday] = useState(0);
  const [showForecastActualLoad, setShowForecastActualLoad] = useState(false);
  const [hoveredForecastHour, setHoveredForecastHour] = useState(null);
  const [pinnedForecastHour, setPinnedForecastHour] = useState(null);
  const [loadedDateCache, setLoadedDateCache] = useState([]);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const userId = user?.id || '';

  useEffect(() => {
    showToastRef.current = showToast;
  }, [showToast]);

  useEffect(() => {
    authHeaderRef.current = withAccessTokenHeader;
  }, [withAccessTokenHeader]);

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
          forecast_week_start: selectedForecastWeekStart || undefined,
        },
        headers: buildHeaders(),
      });
      const payload = response.data || {};
      setOverview(payload);
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
  }, [apiRoot, buildHeaders, dateFrom, dateTo, notify, selectedForecastWeekStart]);

  const fetchDay = useCallback(
    async (date) => {
      if (!apiRoot || !date) {
        setSelectedDay(null);
        return;
      }
      try {
        const response = await axios.get(`${apiRoot}/api/resource_fte/day/${date}`, {
          headers: buildHeaders(),
        });
        setSelectedDay(response.data?.day || null);
      } catch (error) {
        setSelectedDay(null);
        notify(error?.response?.data?.error || 'Не удалось открыть день', 'error');
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

  const activeProfile = useMemo(() => {
    const profiles = overview?.profiles || [];
    return profiles.find((item) => Number(item.weekday) === Number(activeWeekday)) || profiles[0] || null;
  }, [activeWeekday, overview?.profiles]);

  const dayChartData = useMemo(
    () =>
      (selectedDay?.hours || []).map((row) => ({
        hour: row.hour_label,
        received: row.received_calls,
        forecast: Number(row.forecast_calls || 0),
        actual: Number(row.actual_fte || 0),
        fte: Number(row.forecast_fte || 0),
      })),
    [selectedDay],
  );

  const profileChartData = useMemo(
    () =>
      (activeProfile?.hourly_profile || []).map((row) => ({
        hour: `${String(row.hour).padStart(2, '0')}:00`,
        calls: Number(row.avg_calls || 0),
        fte: Number(row.fte || 0),
      })),
    [activeProfile],
  );

  const historyTrendData = useMemo(
    () =>
      (overview?.history || [])
        .slice(0, 21)
        .reverse()
        .map((item) => ({
          date: formatDate(item.report_date).slice(0, 5),
          calls: Number(item.total_received || 0),
          accepted: Number(item.total_accepted || 0),
          lost: Number(item.total_lost || 0),
          lossRate: Number(item.no_answer_rate || 0) * 100,
          forecastFte: Number(item.forecast_fte_total || 0),
          actualFte: Number(item.actual_fte_total || 0),
        })),
    [overview?.history],
  );

  const periodLossSummary = useMemo(() => {
    const rows = overview?.history || [];
    const totalReceived = rows.reduce((sum, row) => sum + Number(row.total_received || 0), 0);
    const totalAccepted = rows.reduce((sum, row) => sum + Number(row.total_accepted || 0), 0);
    const totalLost = rows.reduce((sum, row) => sum + Number(row.total_lost || 0), 0);
    const worstDay = rows.reduce((worst, row) => {
      if (!worst) return row;
      return Number(row.no_answer_rate || 0) > Number(worst.no_answer_rate || 0) ? row : worst;
    }, null);
    return {
      totalReceived,
      totalAccepted,
      totalLost,
      lossRate: totalReceived > 0 ? totalLost / totalReceived : 0,
      worstDay,
    };
  }, [overview?.history]);

  const dayLossHotspots = useMemo(() => {
    const rows = selectedDay?.hours || [];
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
  }, [selectedDay?.hours]);

  const dayStaffingHotspots = useMemo(() => {
    const rows = selectedDay?.hours || [];
    return rows
      .map((row) => ({
        ...row,
        delta: Number(row.fact_forecast_delta || 0),
        gap: Math.abs(Number(row.fact_forecast_delta || 0)),
      }))
      .filter((row) => row.gap > 0)
      .sort((a, b) => b.gap - a.gap)
      .slice(0, 4);
  }, [selectedDay?.hours]);

  const dayPeakHours = useMemo(() => {
    const rows = selectedDay?.hours || [];
    return rows
      .filter((row) => Number(row.received_calls || 0) > 0)
      .sort((a, b) => Number(b.received_calls || 0) - Number(a.received_calls || 0))
      .slice(0, 4);
  }, [selectedDay?.hours]);

  const dayFteDeltaTotal = useMemo(() => {
    if (!selectedDay?.summary) return 0;
    return Number(selectedDay.summary.actual_fte_total || 0) - Number(selectedDay.summary.forecast_fte_total || 0);
  }, [selectedDay?.summary]);

  const dayAcceptedLostData = useMemo(
    () =>
      (selectedDay?.hours || []).map((row) => ({
        hour: row.hour_label,
        accepted: Number(row.accepted_calls || 0),
        lost: Number(row.lost_calls || 0),
        lossRate: Number(row.no_answer_rate || 0) * 100,
      })),
    [selectedDay?.hours],
  );

  const nextWeekForecast = overview?.next_week_forecast || {
    days: [],
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
    historyComplete: false,
    history_weeks: getForecastHistoryWeeks(selectedForecastWeekStart),
  };

  const selectedForecastDay = useMemo(
    () =>
      (nextWeekForecast.days || []).find((day) => Number(day.weekday) === Number(selectedForecastWeekday)) ||
      (nextWeekForecast.days || [])[0] ||
      null,
    [nextWeekForecast.days, selectedForecastWeekday],
  );

  const selectedForecastHourlyData = useMemo(
    () =>
      (selectedForecastDay?.hourly_forecast || []).map((row) => ({
        hourNumber: Number(row.hour),
        hour: `${String(row.hour).padStart(2, '0')}:00`,
        calls: Number(row.forecast_calls || 0),
        fte: Number(row.forecast_fte || 0),
        workload: Number(row.forecast_workload_minutes || 0),
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
        <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
          <div className="mb-2 font-semibold text-slate-900">{String(row.hour).padStart(2, '0')}:00</div>
          <div className="space-y-1 text-slate-600">
            <div className="flex justify-between gap-6"><span>Прогноз звонков</span><b className="text-slate-900">{formatNumber(row.forecast_calls, 1)}</b></div>
            <div className="flex justify-between gap-6"><span>Прогноз минут</span><b className="text-blue-700">{formatNumber(row.forecast_workload_minutes, 1)}</b></div>
            <div className="flex justify-between gap-6"><span>Прогноз FTE</span><b className="text-blue-700">{formatNumber(row.forecast_fte, 2)}</b></div>
            {showForecastActualLoad && selectedForecastHasActualLoad ? (
              <>
                <div className="flex justify-between gap-6"><span>Факт минут</span><b className="text-emerald-700">{row.has_actual_report ? formatNumber(row.actual_workload_minutes, 1) : '-'}</b></div>
                <div className="flex justify-between gap-6"><span>FTE из отчета</span><b className="text-emerald-700">{row.has_actual_report ? formatNumber(row.actual_report_fte, 2) : '-'}</b></div>
              </>
            ) : null}
          </div>
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
    if (!uploadFile || !uploadDate) {
      notify('Выберите дату и CSV-файл', 'error');
      return;
    }
    const formData = new FormData();
    formData.append('report_date', uploadDate);
    formData.append('file', uploadFile);
    setIsUploading(true);
    try {
      const response = await axios.post(`${apiRoot}/api/resource_fte/upload`, formData, {
        headers: buildHeaders(),
      });
      notify('Отчет загружен и пересчитан');
      setUploadFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      setSelectedDate(response.data?.report_date || uploadDate);
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

  const updateHourLocal = (hour, key, value) => {
    setSelectedDay((current) => {
      if (!current) return current;
      return {
        ...current,
        hours: current.hours.map((row) => (row.hour === hour ? { ...row, [key]: value } : row)),
      };
    });
  };

  const saveHour = async (row) => {
    if (!selectedDay?.summary?.report_date) return;
    const key = `${selectedDay.summary.report_date}-${row.hour}`;
    setSavingHourKey(key);
    try {
      const response = await axios.patch(
        `${apiRoot}/api/resource_fte/day/${selectedDay.summary.report_date}/hours/${row.hour}`,
        {
          planned_fte: row.planned_fte,
          actual_fte: row.actual_fte,
          comments: row.comments,
        },
        {
          headers: buildHeaders({
            'Content-Type': 'application/json',
          }),
        },
      );
      setSelectedDay(response.data?.day || selectedDay);
      await fetchOverview();
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось сохранить строку', 'error');
    } finally {
      setSavingHourKey('');
    }
  };

  const weekly = overview?.weekly_totals || {};
  const resourceDirections = overview?.directions || [];
  const selectedSummary = selectedDay?.summary;
  const loadedReportDates = useMemo(
    () => Array.from(new Set([
      ...loadedDateCache,
      ...(overview?.loaded_report_dates || []),
      ...(overview?.history || []).map((item) => item.report_date).filter(Boolean),
    ])).sort(),
    [loadedDateCache, overview?.history, overview?.loaded_report_dates],
  );
  const loadedReportDateSet = useMemo(() => new Set(loadedReportDates), [loadedReportDates]);
  const forecastHistoryWeeks = nextWeekForecast.history_weeks || getForecastHistoryWeeks(selectedForecastWeekStart);
  const forecastWeekComplete = Boolean(nextWeekForecast.historyComplete) ||
    isForecastWeekHistoryComplete(nextWeekForecast.week_start || selectedForecastWeekStart, loadedReportDateSet);
  const uploadDateAlreadyLoaded = loadedReportDates.includes(uploadDate);
  const selectedFileName = uploadFile?.name || 'Файл не выбран';
  const selectedDirectionIds = (settingsDraft?.selected_direction_ids || []).map((item) => Number(item)).filter(Boolean);
  const selectedDirectionSet = new Set(selectedDirectionIds);

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
            <div className="w-full sm:w-[240px]">
              <CalendarPicker
                label="Загрузить отчет"
                value={uploadDate}
                onChange={(date) => {
                  setUploadDate(date);
                  setUploadFile(null);
                  if (fileInputRef.current) fileInputRef.current.value = '';
                  setIsUploadModalOpen(true);
                }}
                loadedDates={loadedReportDates}
                hint="выберите день"
              />
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
                <p className="mt-1 text-sm text-slate-500">Дата выбрана в календаре. Приложите CSV-отчет за 24 часа.</p>
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
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Дата отчета</div>
                  <div className="mt-1 text-xl font-semibold text-slate-950">{formatDate(uploadDate)}</div>
                </div>
                <div className={`inline-flex w-fit items-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold ${
                  uploadDateAlreadyLoaded ? 'bg-emerald-50 text-emerald-700' : 'bg-white text-slate-600'
                }`}>
                  {uploadDateAlreadyLoaded ? <CheckCircle2 size={14} /> : <CalendarDays size={14} />}
                  {uploadDateAlreadyLoaded ? 'Отчет уже есть, можно обновить' : 'Новая дата отчета'}
                </div>
              </div>
            </div>

            <div className="mt-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">CSV-отчет за 24 часа</div>
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
                {isUploading ? 'Загрузка...' : uploadDateAlreadyLoaded ? 'Обновить отчет' : 'Загрузить отчет'}
              </button>
            </div>
          </form>
        </div>
      )}

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

        {activeDashboardView !== 'settings' && activeDashboardView !== 'next_week' && visibleMetricCount > 0 && (
          <div className={`grid gap-3 md:grid-cols-2 ${visibleMetricCount >= 5 ? 'xl:grid-cols-6' : visibleMetricCount >= 4 ? 'xl:grid-cols-4' : 'xl:grid-cols-3'}`}>
            {displayOptions.metricOperators && (
              <StatCard
                icon={Users}
                label="Операторы с усушкой"
                value={formatNumber(weekly.operators_with_shrinkage, 2)}
                hint={`Текущий FTE: ${formatNumber(weekly.current_operator_fte, 2)} · разница: ${formatSignedNumber(weekly.operator_fte_gap, 2)}`}
                tone={Number(weekly.operator_fte_gap || 0) < 0 ? 'rose' : 'blue'}
              />
            )}
            {displayOptions.metricWeeklyFte && (
              <StatCard icon={Clock3} label="Недельная потребность" value={formatNumber(weekly.weekly_fte_hours, 1)} hint="Сумма ПН-ВС в FTE-часах" tone="emerald" />
            )}
            {displayOptions.metricBaseOperators && (
              <StatCard icon={TrendingUp} label="Без усушки" value={formatNumber(weekly.base_operators, 2)} hint="Расчет от 40 часов в неделю" tone="slate" />
            )}
            {displayOptions.metricHistoryWarnings && (
              <StatCard icon={AlertTriangle} label="Недостаток истории" value={(overview?.profiles || []).filter((item) => item.insufficient_history).length} hint="Дни недели с менее чем 2 значениями" tone="amber" />
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
                    <Tooltip formatter={(value, name) => {
                      const labelMap = { calls: 'Звонки', lost: 'Потеряно', lossRate: 'Доля потерь', actualFte: 'Факт FTE', forecastFte: 'Прогноз FTE' };
                      return [name === 'lossRate' ? `${formatNumber(value, 1)}%` : formatNumber(value, name === 'calls' || name === 'lost' ? 0 : 2), labelMap[name] || name];
                    }} />
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

        {activeDashboardView === 'losses' && (
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">Аналитика потерь</h2>
                <p className="text-sm text-slate-500">Потерянные звонки, доля неответов и часы с максимальным риском.</p>
              </div>
              {periodLossSummary.worstDay ? (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
                  Худший день: <b>{formatDate(periodLossSummary.worstDay.report_date)}</b> · {formatPercent(periodLossSummary.worstDay.no_answer_rate)}
                </div>
              ) : null}
            </div>

            <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
              <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-3">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <PhoneMissed size={16} />
                  Потери по дням
                </div>
                {historyTrendData.length ? (
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={historyTrendData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                        <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value, name) => [name === 'lossRate' ? `${formatNumber(value, 1)}%` : formatNumber(value, 0), name === 'lost' ? 'Потеряно' : name === 'accepted' ? 'Принято' : 'Доля потерь']} />
                        {displayOptions.chartCalls && <Bar yAxisId="left" dataKey="accepted" stackId="calls" fill="#bbf7d0" radius={[0, 0, 0, 0]} />}
                        {displayOptions.chartLosses && <Bar yAxisId="left" dataKey="lost" stackId="calls" fill="#fecdd3" radius={[4, 4, 0, 0]} />}
                        {displayOptions.chartLossRate && <Line yAxisId="right" type="monotone" dataKey="lossRate" stroke="#e11d48" strokeWidth={2} dot={false} />}
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState title="Нет данных по потерям" text="Загрузите ежедневные отчеты, чтобы увидеть динамику потерь." />
                )}
              </div>

              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <ShieldAlert size={16} />
                  Сводка периода
                </div>
                <dl className="mt-4 space-y-3 text-sm">
                  <div className="flex justify-between gap-3"><dt className="text-slate-500">Поступило</dt><dd className="font-medium text-slate-900">{formatInt(periodLossSummary.totalReceived)}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-slate-500">Принято</dt><dd className="font-medium text-emerald-700">{formatInt(periodLossSummary.totalAccepted)}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-slate-500">Потеряно</dt><dd className="font-medium text-rose-700">{formatInt(periodLossSummary.totalLost)}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-slate-500">Доля потерь</dt><dd className="font-medium text-rose-700">{formatPercent(periodLossSummary.lossRate)}</dd></div>
                </dl>
                <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                  Высокая доля потерь в часы с большим входящим потоком обычно указывает на недобор факта или неверное распределение смен.
                </div>
              </div>
            </div>

            {selectedSummary ? (
              <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
                <div className="rounded-lg border border-slate-200 bg-white p-3">
                  <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <PhoneCall size={16} />
                    Принято / потеряно по часам: {formatDate(selectedSummary.report_date)}
                  </div>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={dayAcceptedLostData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={2} />
                        <YAxis tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value, name) => [name === 'lossRate' ? `${formatNumber(value, 1)}%` : formatNumber(value, 0), name === 'accepted' ? 'Принято' : name === 'lost' ? 'Потеряно' : 'Доля потерь']} />
                        <Area type="monotone" dataKey="accepted" stackId="1" stroke="#16a34a" fill="#bbf7d0" />
                        <Area type="monotone" dataKey="lost" stackId="1" stroke="#e11d48" fill="#fecdd3" />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="rounded-lg border border-slate-200 bg-white p-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <AlertTriangle size={16} />
                    Топ часов риска
                  </div>
                  <div className="mt-4 space-y-3">
                    {dayLossHotspots.length ? (
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
            ) : null}
          </section>
        )}

        <div className={`grid gap-6 ${
          activeDashboardView === 'overview' || activeDashboardView === 'day' || activeDashboardView === 'settings'
            ? 'xl:grid-cols-[320px_minmax(0,1fr)]'
            : 'xl:grid-cols-1'
        }`}>
          {(activeDashboardView === 'overview' || activeDashboardView === 'day' || activeDashboardView === 'settings') && (
          <aside className="space-y-4">
            {(activeDashboardView === 'overview' || activeDashboardView === 'day') && (
            <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
              <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <CalendarDays size={16} />
                  История загрузок
                </div>
              </div>
              <div className="max-h-[420px] overflow-y-auto p-2">
                {(overview?.history || []).length ? (
                  overview.history.map((item) => (
                    <button
                      key={item.report_date}
                      type="button"
                      onClick={() => setSelectedDate(item.report_date)}
                      className={`mb-2 w-full rounded-lg border p-3 text-left transition ${
                        selectedDate === item.report_date
                          ? 'border-blue-300 bg-blue-50'
                          : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="font-semibold text-slate-950">{formatDate(item.report_date)}</div>
                        <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-600">{item.weekday_short}</span>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-slate-500">
                        <span>Получено: <b className="text-slate-800">{formatInt(item.total_received)}</b></span>
                        <span>FTE: <b className="text-slate-800">{formatNumber(item.forecast_fte_total, 1)}</b></span>
                      </div>
                    </button>
                  ))
                ) : (
                  <div className="p-5 text-sm text-slate-500">Загрузок пока нет.</div>
                )}
              </div>
            </div>
            )}

            {activeDashboardView === 'settings' && (
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
            )}
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
                    <h2 className="text-lg font-semibold text-slate-950">Прогноз FTE по выбранной неделе</h2>
                    <p className="text-sm text-slate-500">
                      Для выбранной недели берутся две исторические недели до нее, один AHT недели и единые коэффициенты ПН-ВС.
                    </p>
                  </div>
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <button
                      type="button"
                      onClick={() => setShowForecastActualLoad((current) => !current)}
                      disabled={!forecastActualLoadAvailable && !showForecastActualLoad}
                      className={`inline-flex h-10 items-center justify-center gap-2 rounded-lg border px-3 text-sm font-medium transition ${
                        showForecastActualLoad
                          ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                          : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-100'
                      } ${!forecastActualLoadAvailable && !showForecastActualLoad ? 'cursor-not-allowed opacity-50' : ''}`}
                      title={forecastActualLoadAvailable ? 'Показать факт нагрузки из загруженных отчетов' : 'Для выбранной недели нет прошедших дней с загруженным отчетом'}
                    >
                      <span
                        className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition ${
                          showForecastActualLoad ? 'bg-emerald-600' : 'bg-slate-300'
                        }`}
                        aria-hidden="true"
                      >
                        <span
                          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition ${
                            showForecastActualLoad ? 'left-4' : 'left-0.5'
                          }`}
                        />
                      </span>
                      Показать факт нагрузки
                    </button>
                    <button
                      type="button"
                      onClick={handleRecalculate}
                      disabled={isRecalculating}
                      className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100"
                    >
                      <RefreshCw size={16} className={isRecalculating ? 'animate-spin' : ''} />
                      Пересчитать
                    </button>
                  </div>
                </div>

                <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                  <StatCard icon={Clock3} label="AHT недели" value={formatSeconds(nextWeekForecast.weeklyAhtSeconds)} hint="Взвешенно по профилям ПН-ВС" tone="blue" />
                  <StatCard icon={PhoneCall} label="Принято" value={formatPercent(nextWeekForecast.answerRate)} hint="Коэффициент для всей недели" tone="slate" />
                  <StatCard icon={Users} label="OCC / UR" value={`${formatPercent(nextWeekForecast.occ, 0)} / ${formatPercent(nextWeekForecast.ur, 0)}`} hint={`Эфф. мин/час: ${formatNumber(nextWeekForecast.effectiveMinutes, 1)}`} tone="emerald" />
                  <StatCard icon={ShieldAlert} label="Усушка" value={formatPercent(nextWeekForecast.shrinkage, 0)} hint="Коэффициент недели" tone="amber" />
                  <StatCard icon={TrendingUp} label="FTE-часы недели" value={formatNumber(nextWeekForecast.weeklyFteHours, 1)} hint="Сумма ПН-ВС" tone="blue" />
                  <StatCard
                    icon={Users}
                    label="Операторы"
                    value={formatNumber(nextWeekForecast.operatorsWithShrinkage, 2)}
                    hint={`Без усушки: ${formatNumber(nextWeekForecast.baseOperators, 2)} · текущий FTE: ${formatNumber(nextWeekForecast.currentOperatorFte, 2)} · разница: ${formatSignedNumber(nextWeekForecast.operatorFteGap, 2)}`}
                    tone={Number(nextWeekForecast.operatorFteGap || 0) < 0 ? 'rose' : 'emerald'}
                  />
                </div>

                <div className="mt-5 grid gap-5 xl:grid-cols-[300px_minmax(0,1fr)]">
                  <aside className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <WeekForecastPicker
                      value={nextWeekForecast.week_start || selectedForecastWeekStart}
                      onChange={(weekStart) => setSelectedForecastWeekStart(weekStart)}
                      loadedDates={loadedReportDates}
                    />
                    <div className={`mt-3 rounded-xl border px-3 py-2 text-xs ${
                      forecastWeekComplete
                        ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
                        : 'border-amber-200 bg-amber-50 text-amber-800'
                    }`}>
                      <div className="flex items-center gap-1 font-semibold">
                        {forecastWeekComplete ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
                        {forecastWeekComplete ? 'Неделе хватает истории' : 'Неделе не хватает истории'}
                      </div>
                      <div className="mt-1 text-slate-600">
                        Исторические периоды: {(forecastHistoryWeeks || []).map((week) => `${formatDate(week.start)}-${formatDate(week.end)}`).join(', ')}
                      </div>
                    </div>
                    <div className="mb-3 mt-5 text-sm font-semibold text-slate-900">Выберите день</div>
                    <div className="space-y-2">
                      {(nextWeekForecast.days || []).map((profile) => (
                        <button
                          key={profile.weekday}
                          type="button"
                          onClick={() => setSelectedForecastWeekday(profile.weekday)}
                          className={`w-full rounded-lg border p-3 text-left transition ${
                            Number(selectedForecastWeekday) === Number(profile.weekday)
                              ? profile.insufficient_history
                                ? 'border-amber-300 bg-amber-50'
                                : 'border-emerald-300 bg-emerald-50'
                              : profile.insufficient_history
                                ? 'border-slate-200 bg-white hover:border-amber-200 hover:bg-amber-50'
                                : 'border-emerald-100 bg-white hover:border-emerald-200 hover:bg-emerald-50'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <div>
                              <div className="font-semibold text-slate-950">{profile.short}</div>
                              <div className="text-xs text-slate-500">{formatDate(profile.forecast_date)}</div>
                            </div>
                            <div className="text-right">
                              <div className={`text-sm font-semibold ${profile.insufficient_history ? 'text-amber-700' : 'text-emerald-700'}`}>{formatNumber(profile.forecast_daily_fte, 2)}</div>
                              <div className="text-[11px] text-slate-500">FTE</div>
                            </div>
                          </div>
                          <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-500">
                            <span>Звонки: <b className="text-slate-800">{formatInt(profile.forecast_calls)}</b></span>
                            <span>История: <b className={profile.insufficient_history ? 'text-amber-700' : 'text-emerald-700'}>{profile.history_count}/2</b></span>
                            {profile.has_actual_report && profile.forecast_date <= todayValue ? (
                              <span className="col-span-2 text-emerald-700">Факт отчета: <b>{formatNumber(profile.actual_report_fte, 2)} FTE</b></span>
                            ) : null}
                          </div>
                        </button>
                      ))}
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
                              <p className="text-sm text-slate-500">Разбивка использует AHT недели {formatSeconds(nextWeekForecast.weeklyAhtSeconds)} и единые коэффициенты.</p>
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

                          <div className={`mt-4 grid gap-3 md:grid-cols-2 ${showForecastActualLoad && selectedForecastHasActualLoad ? 'xl:grid-cols-7' : 'xl:grid-cols-4'}`}>
                            <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Звонки</div><b>{formatInt(selectedForecastDay.forecast_calls)}</b></div>
                            <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Минут нагрузки</div><b>{formatNumber(selectedForecastDay.forecast_workload_minutes, 1)}</b></div>
                            <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">FTE дня</div><b>{formatNumber(selectedForecastDay.forecast_daily_fte, 2)}</b></div>
                            <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Пиковый час</div><b>{selectedForecastPeakHours[0] ? `${String(selectedForecastPeakHours[0].hour).padStart(2, '0')}:00` : '-'}</b></div>
                            {showForecastActualLoad && selectedForecastHasActualLoad ? (
                              <>
                                <div className="rounded-lg bg-emerald-50 px-3 py-2"><div className="text-xs text-emerald-700">Факт звонков</div><b>{formatInt(selectedForecastDay.actual_received_calls)}</b></div>
                                <div className="rounded-lg bg-emerald-50 px-3 py-2"><div className="text-xs text-emerald-700">Факт нагрузки</div><b>{formatNumber(selectedForecastDay.actual_workload_minutes, 1)}</b></div>
                                <div className="rounded-lg bg-emerald-50 px-3 py-2"><div className="text-xs text-emerald-700">FTE из отчета</div><b>{formatNumber(selectedForecastDay.actual_report_fte, 2)}</b></div>
                              </>
                            ) : null}
                          </div>

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
                                <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                                <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                                <Tooltip content={<ForecastHourlyTooltip />} />
                                {activeForecastHourLabel ? (
                                  <ReferenceLine yAxisId="left" x={activeForecastHourLabel} stroke={pinnedForecastHour !== null ? '#0f172a' : '#64748b'} strokeDasharray="4 4" />
                                ) : null}
                                <Bar yAxisId="left" dataKey="calls" fill="#bfdbfe" radius={[4, 4, 0, 0]}>
                                  {selectedForecastHourlyData.map((item) => (
                                    <Cell
                                      key={item.hour}
                                      fill={activeForecastHour !== null && Number(item.hourNumber) === Number(activeForecastHour) ? '#60a5fa' : '#bfdbfe'}
                                    />
                                  ))}
                                </Bar>
                                <Line yAxisId="left" type="monotone" dataKey="workload" stroke="#3b82f6" strokeWidth={2} dot={false} activeDot={{ r: 5 }} />
                                <Line yAxisId="right" type="monotone" dataKey="fte" stroke="#2563eb" strokeWidth={2} dot={false} activeDot={{ r: 5 }} />
                                {showForecastActualLoad && selectedForecastHasActualLoad && (
                                  <>
                                    <Line yAxisId="left" type="monotone" dataKey="actualWorkload" stroke="#10b981" strokeWidth={2} dot={false} activeDot={{ r: 5 }} />
                                    <Line yAxisId="right" type="monotone" dataKey="actualFte" stroke="#059669" strokeWidth={2} strokeDasharray="5 4" dot={false} activeDot={{ r: 5 }} />
                                  </>
                                )}
                              </ComposedChart>
                            </ResponsiveContainer>
                          </div>
                          {showForecastActualLoad && !selectedForecastHasActualLoad ? (
                            <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
                              Для выбранного дня нет загруженного отчета или день еще не прошел, поэтому факт нагрузки не отображается.
                            </div>
                          ) : null}
                        </div>

                        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
                          <div className="overflow-x-auto rounded-lg border border-slate-200">
                            <table className={`${showForecastActualLoad && selectedForecastHasActualLoad ? 'min-w-[980px]' : 'min-w-[760px]'} w-full divide-y divide-slate-200 text-sm`}>
                              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                                <tr>
                                  <th className="px-3 py-3 text-left">Час</th>
                                  <th className="px-3 py-3 text-right">Звонки</th>
                                  <th className="px-3 py-3 text-right">AHT недели</th>
                                  <th className="px-3 py-3 text-right">Минут нагрузки</th>
                                  <th className="px-3 py-3 text-right">FTE</th>
                                  {showForecastActualLoad && selectedForecastHasActualLoad ? (
                                    <>
                                      <th className="px-3 py-3 text-right">Факт нагрузки</th>
                                      <th className="px-3 py-3 text-right">FTE из отчета</th>
                                    </>
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
                                        <span className={rowIsPinned ? 'rounded-md bg-slate-900 px-2 py-1 text-white' : ''}>{String(row.hour).padStart(2, '0')}:00</span>
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
                                      <td className="px-3 py-2 text-right font-semibold text-blue-700">{formatNumber(row.forecast_fte, 2)}</td>
                                      {showForecastActualLoad && selectedForecastHasActualLoad ? (
                                        <>
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
                                          <td className="px-3 py-2 text-right font-semibold text-emerald-700">
                                            {row.has_actual_report ? formatNumber(row.actual_report_fte, 2) : '-'}
                                          </td>
                                        </>
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
                                <TrendingUp size={16} />
                                Пиковые часы прогноз
                              </div>
                              <div className="mt-4 space-y-3">
                                {selectedForecastPeakHours.map((row) => {
                                  const rowIsActive = activeForecastHour !== null && Number(row.hour) === Number(activeForecastHour);
                                  const rowIsPinned = pinnedForecastHour !== null && Number(row.hour) === Number(pinnedForecastHour);
                                  return (
                                    <button
                                      key={row.hour}
                                      type="button"
                                      onMouseEnter={() => setHoveredForecastHour(Number(row.hour))}
                                      onMouseLeave={() => setHoveredForecastHour(null)}
                                      onClick={() => togglePinnedForecastSlice(Number(row.hour))}
                                      className={`w-full rounded-lg p-3 text-left transition ${
                                        rowIsPinned
                                          ? 'bg-slate-100 ring-1 ring-inset ring-slate-300'
                                          : rowIsActive
                                            ? 'bg-blue-50'
                                            : 'bg-slate-50 hover:bg-blue-50'
                                      }`}
                                    >
                                      <div className="flex items-center justify-between">
                                        <span className="font-semibold text-slate-900">{String(row.hour).padStart(2, '0')}:00</span>
                                        <span className="rounded-md bg-blue-100 px-2 py-1 text-xs font-semibold text-blue-700">{formatNumber(row.forecast_fte, 2)} FTE</span>
                                      </div>
                                      <div className="mt-2 text-xs text-slate-500">Звонки: {formatNumber(row.forecast_calls, 1)} · нагрузка: {formatNumber(row.forecast_workload_minutes, 1)} мин</div>
                                      <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
                                        <div className="h-full rounded-full bg-blue-500" style={{ width: `${Math.min(100, Number(row.forecast_fte || 0) * 25)}%` }} />
                                      </div>
                                    </button>
                                  );
                                })}
                              </div>
                            </div>

                            {showForecastActualLoad && selectedForecastHasActualLoad ? (
                              <div className="rounded-lg border border-emerald-100 bg-white p-4">
                                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                                  <TrendingUp size={16} className="text-emerald-600" />
                                  Пиковые часы факт
                                </div>
                                <div className="mt-4 space-y-3">
                                  {selectedActualPeakHours.map((row) => {
                                    const rowIsActive = activeForecastHour !== null && Number(row.hour) === Number(activeForecastHour);
                                    const rowIsPinned = pinnedForecastHour !== null && Number(row.hour) === Number(pinnedForecastHour);
                                    return (
                                      <button
                                        key={row.hour}
                                        type="button"
                                        onMouseEnter={() => setHoveredForecastHour(Number(row.hour))}
                                        onMouseLeave={() => setHoveredForecastHour(null)}
                                        onClick={() => togglePinnedForecastSlice(Number(row.hour))}
                                        className={`w-full rounded-lg p-3 text-left transition ${
                                          rowIsPinned
                                            ? 'bg-slate-100 ring-1 ring-inset ring-slate-300'
                                            : rowIsActive
                                              ? 'bg-emerald-50'
                                              : 'bg-slate-50 hover:bg-emerald-50'
                                        }`}
                                      >
                                        <div className="flex items-center justify-between">
                                          <span className="font-semibold text-slate-900">{String(row.hour).padStart(2, '0')}:00</span>
                                          <span className="rounded-md bg-emerald-100 px-2 py-1 text-xs font-semibold text-emerald-700">{formatNumber(row.actual_report_fte, 2)} FTE</span>
                                        </div>
                                        <div className="mt-2 text-xs text-slate-500">Звонки: {formatInt(row.actual_received_calls)} · нагрузка: {formatNumber(row.actual_workload_minutes, 1)} мин</div>
                                        <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
                                          <div className="h-full rounded-full bg-emerald-500" style={{ width: `${Math.min(100, Number(row.actual_report_fte || 0) * 25)}%` }} />
                                        </div>
                                      </button>
                                    );
                                  })}
                                </div>
                              </div>
                            ) : null}
                          </div>
                        </div>
                      </>
                    ) : (
                      <EmptyState title="Нет прогноза" text="Загрузите исторические отчеты, чтобы построить прогноз выбранной недели." />
                    )}
                  </div>
                </div>
              </section>
            )}

            {(activeDashboardView === 'overview' || activeDashboardView === 'profiles') && (
            <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-slate-950">Профили по дням недели</h2>
                  <p className="text-sm text-slate-500">Скользящее окно последних 14 календарных дней, максимум два одинаковых дня недели.</p>
                </div>
                <button
                  type="button"
                  onClick={handleRecalculate}
                  disabled={isRecalculating}
                  className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100"
                >
                  <RefreshCw size={16} className={isRecalculating ? 'animate-spin' : ''} />
                  Пересчитать
                </button>
              </div>
              <div className="mt-4 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-900">
                Профиль - это типовой рисунок нагрузки конкретного дня недели: сколько звонков приходит по часам, какой AHT получается из истории и сколько FTE нужно. Поэтому прогноз следующей недели строится не как один общий средний день, а отдельно для ПН, ВТ, СР, ЧТ, ПТ, СБ и ВС.
              </div>
              <div className="mt-4 flex gap-2 overflow-x-auto pb-1">
                {(overview?.profiles || []).map((profile) => (
                  <button
                    type="button"
                    key={profile.weekday}
                    onClick={() => setActiveWeekday(profile.weekday)}
                    className={`min-w-[112px] rounded-lg border px-3 py-2 text-left transition ${
                      Number(activeWeekday) === Number(profile.weekday)
                        ? 'border-blue-300 bg-blue-50 text-blue-900'
                        : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold">{profile.short}</span>
                      {profile.insufficient_history ? <AlertTriangle size={14} className="text-amber-500" /> : <CheckCircle2 size={14} className="text-emerald-500" />}
                    </div>
                    <div className="mt-1 text-xs text-slate-500">{formatNumber(profile.daily_fte, 1)} FTE</div>
                  </button>
                ))}
              </div>

              {activeProfile ? (
                <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
                  {(displayOptions.chartCalls || displayOptions.chartFte) && (
                  <div className="h-72 min-w-0">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={profileChartData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={2} />
                        <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value, name) => [formatNumber(value, name === 'fte' ? 2 : 0), name === 'fte' ? 'FTE' : 'Звонки']} />
                        {displayOptions.chartCalls && <Line yAxisId="left" type="monotone" dataKey="calls" stroke="#2563eb" strokeWidth={2} dot={false} />}
                        {displayOptions.chartFte && <Line yAxisId="right" type="monotone" dataKey="fte" stroke="#059669" strokeWidth={2} dot={false} />}
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                  )}
                  <div className="rounded-lg bg-slate-50 p-4">
                    <div className="text-sm font-semibold text-slate-900">{activeProfile.label}</div>
                    <dl className="mt-4 space-y-3 text-sm">
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">История</dt><dd className="font-medium text-slate-900">{activeProfile.history_count}/2</dd></div>
                      {displayOptions.profileCalls && <div className="flex justify-between gap-3"><dt className="text-slate-500">Сред. звонков</dt><dd className="font-medium text-slate-900">{formatInt(activeProfile.avg_daily_calls)}</dd></div>}
                      {displayOptions.profileAht && <div className="flex justify-between gap-3"><dt className="text-slate-500">AHT из истории</dt><dd className="font-medium text-slate-900">{formatSeconds(activeProfile.aht_seconds)}</dd></div>}
                      {displayOptions.profileDailyFte && <div className="flex justify-between gap-3"><dt className="text-slate-500">Суточная FTE</dt><dd className="font-medium text-slate-900">{formatNumber(activeProfile.daily_fte, 2)}</dd></div>}
                    </dl>
                    {activeProfile.insufficient_history ? (
                      <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                        Недостаточно истории: расчет построен по доступным данным.
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : (
                <EmptyState title="Нет профилей" text="Загрузите ежедневные CSV-отчеты, чтобы система построила недельный профиль." />
              )}
            </section>
            )}

            {(activeDashboardView === 'overview' || activeDashboardView === 'day') && (
            <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              {selectedSummary ? (
                <>
                  <div className="flex flex-col gap-2">
                    <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
                      <div>
                        <h2 className="text-lg font-semibold text-slate-950">
                          День {formatDate(selectedSummary.report_date)} · {selectedSummary.weekday_short}
                        </h2>
                        <p className="text-sm text-slate-500">Звонки, потери и отклонение факта от прогноза по часам.</p>
                      </div>
                      <span className={`inline-flex w-fit items-center rounded-lg px-3 py-2 text-sm font-semibold ${
                        dayFteDeltaTotal < -0.5
                          ? 'bg-rose-50 text-rose-700'
                          : dayFteDeltaTotal > 0.5
                            ? 'bg-emerald-50 text-emerald-700'
                            : 'bg-slate-100 text-slate-700'
                      }`}>
                        Разница факта: {formatNumber(dayFteDeltaTotal, 1)} FTE
                      </span>
                    </div>

                    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                      {displayOptions.tableReceived && (
                        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                          <div className="text-xs font-medium text-slate-500">Получено</div>
                          <div className="mt-1 text-xl font-semibold text-slate-950">{formatInt(selectedSummary.total_received)}</div>
                        </div>
                      )}
                      {displayOptions.tableAccepted && (
                        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                          <div className="text-xs font-medium text-slate-500">Принято</div>
                          <div className="mt-1 text-xl font-semibold text-emerald-700">{formatInt(selectedSummary.total_accepted)}</div>
                        </div>
                      )}
                      {displayOptions.tableLost && (
                        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                          <div className="text-xs font-medium text-slate-500">Потеряно</div>
                          <div className="mt-1 text-xl font-semibold text-rose-700">{formatInt(selectedSummary.total_lost)}</div>
                        </div>
                      )}
                      {displayOptions.tableNoAnswer && (
                        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                          <div className="text-xs font-medium text-slate-500">Доля потерь</div>
                          <div className="mt-1 text-xl font-semibold text-slate-950">{formatPercent(selectedSummary.no_answer_rate)}</div>
                        </div>
                      )}
                      {displayOptions.tableForecast && (
                        <div className="rounded-lg border border-slate-200 bg-blue-50 p-3">
                          <div className="text-xs font-medium text-blue-700">Прогноз FTE</div>
                          <div className="mt-1 text-xl font-semibold text-blue-800">{formatNumber(selectedSummary.forecast_fte_total, 1)}</div>
                        </div>
                      )}
                      {displayOptions.tableActual && (
                        <div className="rounded-lg border border-slate-200 bg-emerald-50 p-3">
                          <div className="text-xs font-medium text-emerald-700">Факт FTE</div>
                          <div className="mt-1 text-xl font-semibold text-emerald-800">{formatNumber(selectedSummary.actual_fte_total, 1)}</div>
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
                    {(displayOptions.chartCalls || displayOptions.chartFte || displayOptions.chartActual) && (
                      <div className="rounded-lg border border-slate-200 p-3">
                        <div className="mb-3 flex items-center justify-between gap-3">
                          <div>
                            <h3 className="text-sm font-semibold text-slate-950">Нагрузка по часам</h3>
                            <p className="text-xs text-slate-500">Столбцы — звонки, линии — FTE.</p>
                          </div>
                          <div className="flex flex-wrap justify-end gap-2 text-xs">
                            {displayOptions.chartCalls && <span className="rounded bg-blue-100 px-2 py-1 text-blue-700">Звонки</span>}
                            {displayOptions.chartFte && <span className="rounded bg-blue-600 px-2 py-1 text-white">Прогноз</span>}
                            {displayOptions.chartActual && <span className="rounded bg-emerald-600 px-2 py-1 text-white">Факт</span>}
                          </div>
                        </div>
                        <div className="h-72">
                          <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart data={dayChartData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                              <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={2} />
                              <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                              <Tooltip formatter={(value, name) => [formatNumber(value, name === 'received' ? 0 : 2), name === 'received' ? 'Получено' : name === 'actual' ? 'Факт FTE' : 'Прогноз FTE']} />
                              {displayOptions.chartCalls && <Bar yAxisId="left" dataKey="received" fill="#bfdbfe" radius={[4, 4, 0, 0]} />}
                              {displayOptions.chartFte && <Line yAxisId="right" type="monotone" dataKey="fte" stroke="#2563eb" strokeWidth={2} dot={false} />}
                              {displayOptions.chartActual && <Line yAxisId="right" type="monotone" dataKey="actual" stroke="#059669" strokeWidth={2} dot={false} />}
                            </ComposedChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    )}

                    <div className="space-y-3">
                      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                        <div className="mb-2 text-sm font-semibold text-slate-950">Часы риска</div>
                        <div className="space-y-2">
                          {dayStaffingHotspots.length ? (
                            dayStaffingHotspots.map((row) => (
                              <div key={row.hour} className="rounded-md bg-white p-2 text-sm">
                                <div className="flex items-center justify-between gap-2">
                                  <b className="text-slate-900">{row.hour_label}</b>
                                  <span className={row.delta < 0 ? 'font-semibold text-rose-700' : 'font-semibold text-emerald-700'}>
                                    {formatNumber(row.delta, 2)} FTE
                                  </span>
                                </div>
                                <div className="mt-1 text-xs text-slate-500">
                                  Прогноз {formatNumber(row.forecast_fte, 2)} · факт {formatNumber(row.actual_fte, 2)}
                                </div>
                              </div>
                            ))
                          ) : (
                            <div className="rounded-md border border-dashed border-slate-300 bg-white p-3 text-sm text-slate-500">Отклонений по FTE нет.</div>
                          )}
                        </div>
                      </div>

                      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                        <div className="mb-2 text-sm font-semibold text-slate-950">Пиковые часы</div>
                        <div className="space-y-2">
                          {dayPeakHours.length ? (
                            dayPeakHours.map((row) => (
                              <div key={row.hour} className="flex items-center justify-between rounded-md bg-white px-2 py-2 text-sm">
                                <span className="font-medium text-slate-900">{row.hour_label}</span>
                                <span className="text-slate-600">{formatInt(row.received_calls)} звонков</span>
                              </div>
                            ))
                          ) : (
                            <div className="rounded-md border border-dashed border-slate-300 bg-white p-3 text-sm text-slate-500">Звонков за день нет.</div>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="mt-5 overflow-x-auto rounded-lg border border-slate-200">
                    <table className="min-w-[1180px] w-full divide-y divide-slate-200 text-sm">
                      <thead className="sticky top-0 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                        <tr>
                          <th className="px-3 py-3 text-left">Час</th>
                          {displayOptions.tableReceived && <th className="px-3 py-3 text-right">Получено</th>}
                          {displayOptions.tableAccepted && <th className="px-3 py-3 text-right">Принято</th>}
                          {displayOptions.tableLost && <th className="px-3 py-3 text-right">Потеряно</th>}
                          {displayOptions.tableNoAnswer && <th className="px-3 py-3 text-right">% Неотв</th>}
                          {displayOptions.tableAvgTalk && <th className="px-3 py-3 text-right">Средн. прод.</th>}
                          {displayOptions.tableAvgWait && <th className="px-3 py-3 text-right">Ожидание</th>}
                          {displayOptions.tableForecast && <th className="px-3 py-3 text-right">Прогноз FTE</th>}
                          {displayOptions.tablePlan && <th className="px-3 py-3 text-right">План</th>}
                          {displayOptions.tableActual && <th className="px-3 py-3 text-right">Факт</th>}
                          {displayOptions.tableDelta && <th className="px-3 py-3 text-right">Разница</th>}
                          {displayOptions.tableComments && <th className="px-3 py-3 text-left">Комментарий</th>}
                          <th className="px-3 py-3 text-right"></th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100 bg-white">
                        {(selectedDay.hours || []).map((row) => {
                          const delta = Number(row.fact_forecast_delta || 0);
                          const deltaClass = delta < -0.25 ? 'text-rose-700 bg-rose-50' : delta > 0.25 ? 'text-emerald-700 bg-emerald-50' : 'text-slate-700 bg-slate-50';
                          const rowKey = `${selectedSummary.report_date}-${row.hour}`;
                          return (
                            <tr key={row.hour} className="hover:bg-slate-50/60">
                              <td className="whitespace-nowrap px-3 py-2 font-medium text-slate-900">{row.hour_label}</td>
                              {displayOptions.tableReceived && <td className="px-3 py-2 text-right">{formatInt(row.received_calls)}</td>}
                              {displayOptions.tableAccepted && <td className="px-3 py-2 text-right">{formatInt(row.accepted_calls)}</td>}
                              {displayOptions.tableLost && <td className="px-3 py-2 text-right">{formatInt(row.lost_calls)}</td>}
                              {displayOptions.tableNoAnswer && <td className="px-3 py-2 text-right">{formatPercent(row.no_answer_rate)}</td>}
                              {displayOptions.tableAvgTalk && <td className="px-3 py-2 text-right">{formatSeconds(row.avg_talk_seconds)}</td>}
                              {displayOptions.tableAvgWait && <td className="px-3 py-2 text-right">{formatSeconds(row.avg_wait_seconds)}</td>}
                              {displayOptions.tableForecast && <td className="px-3 py-2 text-right font-semibold text-blue-700">{formatNumber(row.forecast_fte, 2)}</td>}
                              {displayOptions.tablePlan && <td className="px-3 py-2 text-right">
                                <input type="number" step="0.25" value={row.planned_fte ?? 0} onChange={(event) => updateHourLocal(row.hour, 'planned_fte', event.target.value)} className="h-8 w-20 rounded-md border border-slate-200 px-2 text-right text-sm" />
                              </td>}
                              {displayOptions.tableActual && <td className="px-3 py-2 text-right">
                                <input type="number" step="0.25" value={row.actual_fte ?? 0} onChange={(event) => updateHourLocal(row.hour, 'actual_fte', event.target.value)} className="h-8 w-20 rounded-md border border-slate-200 px-2 text-right text-sm" />
                              </td>}
                              {displayOptions.tableDelta && <td className="px-3 py-2 text-right">
                                <span className={`inline-flex min-w-16 justify-center rounded-md px-2 py-1 text-xs font-semibold ${deltaClass}`}>
                                  {formatNumber(delta, 2)}
                                </span>
                              </td>}
                              {displayOptions.tableComments && <td className="px-3 py-2">
                                <input value={row.comments || ''} onChange={(event) => updateHourLocal(row.hour, 'comments', event.target.value)} className="h-8 w-56 rounded-md border border-slate-200 px-2 text-sm" placeholder="Комментарий" />
                              </td>}
                              <td className="px-3 py-2 text-right">
                                <button type="button" onClick={() => saveHour(row)} className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-slate-900" title="Сохранить строку">
                                  <Save size={15} className={savingHourKey === rowKey ? 'animate-pulse' : ''} />
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : (
                <EmptyState title="Выберите или загрузите день" text="После загрузки CSV здесь появится почасовой расчет FTE, план, факт и отклонения." />
              )}
            </section>
            )}
          </main>
        </div>
      </div>
    </div>
  );
};

export default ResourceFteView;
