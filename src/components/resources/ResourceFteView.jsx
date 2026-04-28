import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
  AlertTriangle,
  BarChart3,
  CalendarDays,
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
  Users,
} from 'lucide-react';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  Area,
  AreaChart,
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

const formatInt = (value) => new Intl.NumberFormat('ru-RU').format(Math.round(Number(value || 0)));

const formatPercent = (value, digits = 1) => `${formatNumber(Number(value || 0) * 100, digits)}%`;

const formatDate = (iso) => {
  if (!iso) return '-';
  const [year, month, day] = String(iso).split('-');
  return day && month && year ? `${day}.${month}.${year}` : iso;
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

const getNextWeekDates = (asOfIso) => {
  const [year, month, day] = String(asOfIso || todayIso()).split('-').map(Number);
  const date = new Date(year, (month || 1) - 1, day || 1);
  const mondayIndex = (date.getDay() + 6) % 7;
  const nextMonday = addDaysIso(asOfIso || todayIso(), 7 - mondayIndex);
  return Array.from({ length: 7 }, (_, index) => addDaysIso(nextMonday, index));
};

const formatSeconds = (seconds) => {
  const total = Math.round(Number(seconds || 0));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, '0')}`;
};

const inputClass =
  'h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100';

const DISPLAY_PREFERENCES_STORAGE_KEY = 'otp_resource_fte_display_v1';

const VIEW_TABS = [
  { key: 'overview', label: 'Обзор', icon: LayoutDashboard },
  { key: 'day', label: 'День', icon: CalendarDays },
  { key: 'next_week', label: 'След. неделя', icon: TrendingUp },
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

const ResourceFteView = ({ apiBaseUrl, withAccessTokenHeader, user, showToast }) => {
  const apiRoot = String(apiBaseUrl || '').replace(/\/+$/, '');
  const fileInputRef = useRef(null);
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
  const [selectedForecastWeekday, setSelectedForecastWeekday] = useState(0);

  const notify = useCallback(
    (message, type = 'success') => {
      if (typeof showToast === 'function') showToast(message, type);
    },
    [showToast],
  );

  const fetchOverview = useCallback(async () => {
    if (!apiRoot) return;
    setIsLoading(true);
    try {
      const response = await axios.get(`${apiRoot}/api/resource_fte/overview`, {
        params: { date_from: dateFrom || undefined, date_to: dateTo || undefined },
        headers: apiHeaders(withAccessTokenHeader, { 'X-User-Id': String(user?.id || '') }),
      });
      const payload = response.data || {};
      setOverview(payload);
      setSettingsDraft(payload.settings || null);
      const firstDate = payload.history?.[0]?.report_date || '';
      setSelectedDate((current) => current || firstDate);
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось загрузить расчет ресурсов', 'error');
    } finally {
      setIsLoading(false);
    }
  }, [apiRoot, dateFrom, dateTo, notify, user?.id, withAccessTokenHeader]);

  const fetchDay = useCallback(
    async (date) => {
      if (!apiRoot || !date) {
        setSelectedDay(null);
        return;
      }
      try {
        const response = await axios.get(`${apiRoot}/api/resource_fte/day/${date}`, {
          headers: apiHeaders(withAccessTokenHeader, { 'X-User-Id': String(user?.id || '') }),
        });
        setSelectedDay(response.data?.day || null);
      } catch (error) {
        setSelectedDay(null);
        notify(error?.response?.data?.error || 'Не удалось открыть день', 'error');
      }
    },
    [apiRoot, notify, user?.id, withAccessTokenHeader],
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

  const nextWeekForecast = useMemo(() => {
    const dates = getNextWeekDates(overview?.as_of_date || todayIso());
    const profiles = overview?.profiles || [];
    const totalCalls = profiles.reduce((sum, profile) => sum + Number(profile.avg_daily_calls || 0), 0);
    const weeklyAhtSeconds = totalCalls > 0
      ? profiles.reduce((sum, profile) => sum + Number(profile.avg_daily_calls || 0) * Number(profile.aht_seconds || 0), 0) / totalCalls
      : 0;
    const settings = overview?.settings || {};
    const answerRate = Number(settings.answer_rate || 0);
    const occ = Number(settings.occ || 0);
    const ur = Number(settings.ur || 0);
    const shrinkage = Number(settings.shrinkage_coeff || 0);
    const weeklyHours = Number(settings.weekly_hours_per_operator || 40);
    const effectiveMinutes = 60 * occ * ur;
    const days = profiles.map((profile) => {
      const calls = Number(profile.avg_daily_calls || 0);
      const workloadMinutes = calls * answerRate * weeklyAhtSeconds / 60;
      const dailyFte = effectiveMinutes > 0 ? workloadMinutes / effectiveMinutes : 0;
      const hourly = (profile.hourly_profile || []).map((hourRow) => {
        const hourCalls = Number(hourRow.avg_calls || 0);
        const hourWorkloadMinutes = hourCalls * answerRate * weeklyAhtSeconds / 60;
        const hourFte = effectiveMinutes > 0 ? hourWorkloadMinutes / effectiveMinutes : 0;
        return {
          ...hourRow,
          forecast_calls: hourCalls,
          forecast_aht_seconds: weeklyAhtSeconds,
          forecast_workload_minutes: hourWorkloadMinutes,
          forecast_fte: hourFte,
        };
      });
      return {
        ...profile,
        forecast_date: dates[Number(profile.weekday || 0)] || '',
        forecast_calls: calls,
        forecast_aht_seconds: weeklyAhtSeconds,
        forecast_workload_minutes: workloadMinutes,
        forecast_daily_fte: dailyFte,
        operators_equivalent: dailyFte / 8,
        hourly_forecast: hourly,
      };
    });
    const weeklyFteHours = days.reduce((sum, day) => sum + Number(day.forecast_daily_fte || 0), 0);
    const baseOperators = weeklyHours > 0 ? weeklyFteHours / weeklyHours : 0;
    const operatorsWithShrinkage = shrinkage > 0 ? baseOperators / shrinkage : baseOperators;
    return {
      days,
      weeklyAhtSeconds,
      answerRate,
      occ,
      ur,
      shrinkage,
      weeklyHours,
      effectiveMinutes,
      weeklyFteHours,
      baseOperators,
      operatorsWithShrinkage,
    };
  }, [overview?.as_of_date, overview?.profiles, overview?.settings]);

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
        hour: `${String(row.hour).padStart(2, '0')}:00`,
        calls: Number(row.forecast_calls || 0),
        fte: Number(row.forecast_fte || 0),
        workload: Number(row.forecast_workload_minutes || 0),
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
        headers: apiHeaders(withAccessTokenHeader, { 'X-User-Id': String(user?.id || '') }),
      });
      notify('Отчет загружен и пересчитан');
      setUploadFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      setSelectedDate(response.data?.report_date || uploadDate);
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
        { headers: apiHeaders(withAccessTokenHeader, { 'X-User-Id': String(user?.id || '') }) },
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
        headers: apiHeaders(withAccessTokenHeader, {
          'Content-Type': 'application/json',
          'X-User-Id': String(user?.id || ''),
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
          headers: apiHeaders(withAccessTokenHeader, {
            'Content-Type': 'application/json',
            'X-User-Id': String(user?.id || ''),
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
  const selectedSummary = selectedDay?.summary;

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 px-4 py-4 backdrop-blur md:px-6">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-medium text-blue-700">
              <Users size={16} />
              OTP / КЦ
            </div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-950">Расчет ресурсов / FTE</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} className={inputClass} />
            <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} className={inputClass} />
            <button
              type="button"
              onClick={fetchOverview}
              className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-100"
            >
              <RefreshCw size={16} className={isLoading ? 'animate-spin' : ''} />
              Обновить
            </button>
          </div>
        </div>
      </div>

      <div className="space-y-6 p-4 md:p-6">
        <form onSubmit={handleUpload} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="grid gap-3 lg:grid-cols-[180px_minmax(240px,1fr)_auto] lg:items-end">
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Дата отчета</span>
              <input type="date" value={uploadDate} onChange={(event) => setUploadDate(event.target.value)} className={`${inputClass} mt-1 w-full`} />
            </label>
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">CSV-отчет за 24 часа</span>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,text/csv"
                onChange={(event) => setUploadFile(event.target.files?.[0] || null)}
                className="mt-1 block h-10 w-full rounded-lg border border-slate-200 bg-white text-sm text-slate-700 shadow-sm file:mr-3 file:h-10 file:border-0 file:bg-slate-900 file:px-4 file:text-sm file:font-medium file:text-white"
              />
            </label>
            <button
              type="submit"
              disabled={isUploading}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-300"
            >
              <FileUp size={16} />
              {isUploading ? 'Загрузка...' : 'Загрузить'}
            </button>
          </div>
        </form>

        <div className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-2 shadow-sm lg:flex-row lg:items-center lg:justify-between">
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

        {activeDashboardView !== 'settings' && visibleMetricCount > 0 && (
          <div className={`grid gap-3 md:grid-cols-2 ${visibleMetricCount >= 5 ? 'xl:grid-cols-6' : visibleMetricCount >= 4 ? 'xl:grid-cols-4' : 'xl:grid-cols-3'}`}>
            {displayOptions.metricOperators && (
              <StatCard icon={Users} label="Операторы с усушкой" value={formatNumber(weekly.operators_with_shrinkage, 2)} hint={`Округление: ${formatNumber(weekly.operators_rounded, 0)}`} tone="blue" />
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
                    <h2 className="text-lg font-semibold text-slate-950">Прогноз FTE на следующую неделю</h2>
                    <p className="text-sm text-slate-500">Один AHT недели и единые коэффициенты применяются ко всем дням ПН-ВС.</p>
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

                <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                  <StatCard icon={Clock3} label="AHT недели" value={formatSeconds(nextWeekForecast.weeklyAhtSeconds)} hint="Взвешенно по профилям ПН-ВС" tone="blue" />
                  <StatCard icon={PhoneCall} label="Принято" value={formatPercent(nextWeekForecast.answerRate)} hint="Коэффициент для всей недели" tone="slate" />
                  <StatCard icon={Users} label="OCC / UR" value={`${formatPercent(nextWeekForecast.occ, 0)} / ${formatPercent(nextWeekForecast.ur, 0)}`} hint={`Эфф. мин/час: ${formatNumber(nextWeekForecast.effectiveMinutes, 1)}`} tone="emerald" />
                  <StatCard icon={ShieldAlert} label="Усушка" value={formatPercent(nextWeekForecast.shrinkage, 0)} hint="Коэффициент недели" tone="amber" />
                  <StatCard icon={TrendingUp} label="FTE-часы недели" value={formatNumber(nextWeekForecast.weeklyFteHours, 1)} hint="Сумма ПН-ВС" tone="blue" />
                  <StatCard icon={Users} label="Операторы" value={formatNumber(nextWeekForecast.operatorsWithShrinkage, 2)} hint={`Без усушки: ${formatNumber(nextWeekForecast.baseOperators, 2)}`} tone="emerald" />
                </div>

                <div className="mt-5 grid gap-5 xl:grid-cols-[300px_minmax(0,1fr)]">
                  <aside className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <div className="mb-3 text-sm font-semibold text-slate-900">Выберите день</div>
                    <div className="space-y-2">
                      {(nextWeekForecast.days || []).map((profile) => (
                        <button
                          key={profile.weekday}
                          type="button"
                          onClick={() => setSelectedForecastWeekday(profile.weekday)}
                          className={`w-full rounded-lg border p-3 text-left transition ${
                            Number(selectedForecastWeekday) === Number(profile.weekday)
                              ? 'border-blue-300 bg-blue-50'
                              : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <div>
                              <div className="font-semibold text-slate-950">{profile.short}</div>
                              <div className="text-xs text-slate-500">{formatDate(profile.forecast_date)}</div>
                            </div>
                            <div className="text-right">
                              <div className="text-sm font-semibold text-blue-700">{formatNumber(profile.forecast_daily_fte, 2)}</div>
                              <div className="text-[11px] text-slate-500">FTE</div>
                            </div>
                          </div>
                          <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-500">
                            <span>Звонки: <b className="text-slate-800">{formatInt(profile.forecast_calls)}</b></span>
                            <span>История: <b className={profile.insufficient_history ? 'text-amber-700' : 'text-emerald-700'}>{profile.history_count}/2</b></span>
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
                            <span className={`inline-flex w-fit items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold ${selectedForecastDay.insufficient_history ? 'bg-amber-50 text-amber-700' : 'bg-emerald-50 text-emerald-700'}`}>
                              {selectedForecastDay.insufficient_history ? <AlertTriangle size={13} /> : <CheckCircle2 size={13} />}
                              История {selectedForecastDay.history_count}/2
                            </span>
                          </div>

                          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                            <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Звонки</div><b>{formatInt(selectedForecastDay.forecast_calls)}</b></div>
                            <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Минут нагрузки</div><b>{formatNumber(selectedForecastDay.forecast_workload_minutes, 1)}</b></div>
                            <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">FTE дня</div><b>{formatNumber(selectedForecastDay.forecast_daily_fte, 2)}</b></div>
                            <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Пиковый час</div><b>{selectedForecastPeakHours[0] ? `${String(selectedForecastPeakHours[0].hour).padStart(2, '0')}:00` : '-'}</b></div>
                          </div>

                          <div className="mt-5 h-72">
                            <ResponsiveContainer width="100%" height="100%">
                              <ComposedChart data={selectedForecastHourlyData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                                <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={2} />
                                <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                                <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                                <Tooltip formatter={(value, name) => [formatNumber(value, name === 'calls' ? 0 : 2), name === 'calls' ? 'Звонки' : name === 'workload' ? 'Минут нагрузки' : 'FTE']} />
                                <Bar yAxisId="left" dataKey="calls" fill="#bfdbfe" radius={[4, 4, 0, 0]} />
                                <Line yAxisId="right" type="monotone" dataKey="fte" stroke="#2563eb" strokeWidth={2} dot={false} />
                              </ComposedChart>
                            </ResponsiveContainer>
                          </div>
                        </div>

                        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
                          <div className="overflow-x-auto rounded-lg border border-slate-200">
                            <table className="min-w-[760px] w-full divide-y divide-slate-200 text-sm">
                              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                                <tr>
                                  <th className="px-3 py-3 text-left">Час</th>
                                  <th className="px-3 py-3 text-right">Звонки</th>
                                  <th className="px-3 py-3 text-right">AHT недели</th>
                                  <th className="px-3 py-3 text-right">Минут нагрузки</th>
                                  <th className="px-3 py-3 text-right">FTE</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-100 bg-white">
                                {(selectedForecastDay.hourly_forecast || []).map((row) => (
                                  <tr key={row.hour} className="hover:bg-slate-50/60">
                                    <td className="px-3 py-2 font-medium text-slate-900">{String(row.hour).padStart(2, '0')}:00</td>
                                    <td className="px-3 py-2 text-right">{formatNumber(row.forecast_calls, 1)}</td>
                                    <td className="px-3 py-2 text-right">{formatSeconds(row.forecast_aht_seconds)}</td>
                                    <td className="px-3 py-2 text-right">{formatNumber(row.forecast_workload_minutes, 1)}</td>
                                    <td className="px-3 py-2 text-right font-semibold text-blue-700">{formatNumber(row.forecast_fte, 2)}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>

                          <div className="rounded-lg border border-slate-200 bg-white p-4">
                            <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                              <TrendingUp size={16} />
                              Пиковые часы
                            </div>
                            <div className="mt-4 space-y-3">
                              {selectedForecastPeakHours.map((row) => (
                                <div key={row.hour} className="rounded-lg bg-slate-50 p-3">
                                  <div className="flex items-center justify-between">
                                    <span className="font-semibold text-slate-900">{String(row.hour).padStart(2, '0')}:00</span>
                                    <span className="rounded-md bg-blue-100 px-2 py-1 text-xs font-semibold text-blue-700">{formatNumber(row.forecast_fte, 2)} FTE</span>
                                  </div>
                                  <div className="mt-2 text-xs text-slate-500">Звонки: {formatNumber(row.forecast_calls, 1)} · нагрузка: {formatNumber(row.forecast_workload_minutes, 1)} мин</div>
                                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
                                    <div className="h-full rounded-full bg-blue-500" style={{ width: `${Math.min(100, Number(row.forecast_fte || 0) * 25)}%` }} />
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        </div>
                      </>
                    ) : (
                      <EmptyState title="Нет прогноза" text="Загрузите исторические отчеты, чтобы построить прогноз следующей недели." />
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
                  <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                    <div>
                      <h2 className="text-lg font-semibold text-slate-950">
                        Детализация дня: {formatDate(selectedSummary.report_date)} · {selectedSummary.weekday_short}
                      </h2>
                      <p className="text-sm text-slate-500">Почасовая нормализованная таблица и сравнение факта с прогнозом.</p>
                    </div>
                    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                      {displayOptions.tableReceived && <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Получено</div><b>{formatInt(selectedSummary.total_received)}</b></div>}
                      {displayOptions.tableNoAnswer && <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">% Неотв</div><b>{formatPercent(selectedSummary.no_answer_rate)}</b></div>}
                      {displayOptions.tableForecast && <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Прогноз FTE</div><b>{formatNumber(selectedSummary.forecast_fte_total, 1)}</b></div>}
                      {displayOptions.tableActual && <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Факт</div><b>{formatNumber(selectedSummary.actual_fte_total, 1)}</b></div>}
                    </div>
                  </div>

                  {(displayOptions.chartCalls || displayOptions.chartFte || displayOptions.chartActual) && (
                  <div className="mt-5 h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={dayChartData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={2} />
                        <YAxis tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value, name) => [formatNumber(value, name === 'received' ? 0 : 2), name === 'received' ? 'Получено' : name === 'actual' ? 'Факт FTE' : 'Прогноз FTE']} />
                        {displayOptions.chartCalls && <Bar dataKey="received" fill="#bfdbfe" radius={[4, 4, 0, 0]} />}
                        {displayOptions.chartFte && <Line type="monotone" dataKey="fte" stroke="#2563eb" strokeWidth={2} dot={false} />}
                        {displayOptions.chartActual && <Line type="monotone" dataKey="actual" stroke="#059669" strokeWidth={2} dot={false} />}
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                  )}

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
