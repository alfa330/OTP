import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
  AlertTriangle,
  BarChart3,
  CalendarDays,
  CheckCircle2,
  Clock3,
  FileUp,
  RefreshCw,
  Save,
  Settings,
  TrendingDown,
  TrendingUp,
  Users,
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
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

const formatSeconds = (seconds) => {
  const total = Math.round(Number(seconds || 0));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, '0')}`;
};

const inputClass =
  'h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100';

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

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <StatCard icon={Users} label="Операторы с усушкой" value={formatNumber(weekly.operators_with_shrinkage, 2)} hint={`Округление: ${formatNumber(weekly.operators_rounded, 0)}`} tone="blue" />
          <StatCard icon={Clock3} label="Недельная потребность" value={formatNumber(weekly.weekly_fte_hours, 1)} hint="Сумма ПН-ВС в FTE-часах" tone="emerald" />
          <StatCard icon={TrendingUp} label="Без усушки" value={formatNumber(weekly.base_operators, 2)} hint="Расчет от 40 часов в неделю" tone="slate" />
          <StatCard icon={AlertTriangle} label="Недостаток истории" value={(overview?.profiles || []).filter((item) => item.insufficient_history).length} hint="Дни недели с менее чем 2 значениями" tone="amber" />
        </div>

        <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="space-y-4">
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

            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
                <Settings size={16} />
                Настройки расчета
              </div>
              {settingsDraft ? (
                <div className="grid grid-cols-2 gap-3">
                  {[
                    ['aht_seconds', 'AHT, сек'],
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
          </aside>

          <main className="space-y-6 min-w-0">
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
                  <div className="h-72 min-w-0">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={profileChartData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={2} />
                        <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value, name) => [formatNumber(value, name === 'fte' ? 2 : 0), name === 'fte' ? 'FTE' : 'Звонки']} />
                        <Line yAxisId="left" type="monotone" dataKey="calls" stroke="#2563eb" strokeWidth={2} dot={false} />
                        <Line yAxisId="right" type="monotone" dataKey="fte" stroke="#059669" strokeWidth={2} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="rounded-lg bg-slate-50 p-4">
                    <div className="text-sm font-semibold text-slate-900">{activeProfile.label}</div>
                    <dl className="mt-4 space-y-3 text-sm">
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">История</dt><dd className="font-medium text-slate-900">{activeProfile.history_count}/2</dd></div>
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">Сред. звонков</dt><dd className="font-medium text-slate-900">{formatInt(activeProfile.avg_daily_calls)}</dd></div>
                      <div className="flex justify-between gap-3"><dt className="text-slate-500">Суточная FTE</dt><dd className="font-medium text-slate-900">{formatNumber(activeProfile.daily_fte, 2)}</dd></div>
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
                      <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Получено</div><b>{formatInt(selectedSummary.total_received)}</b></div>
                      <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">% Неотв</div><b>{formatPercent(selectedSummary.no_answer_rate)}</b></div>
                      <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Прогноз FTE</div><b>{formatNumber(selectedSummary.forecast_fte_total, 1)}</b></div>
                      <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Факт</div><b>{formatNumber(selectedSummary.actual_fte_total, 1)}</b></div>
                    </div>
                  </div>

                  <div className="mt-5 h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={dayChartData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={2} />
                        <YAxis tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value, name) => [formatNumber(value, name === 'received' ? 0 : 2), name === 'received' ? 'Получено' : name === 'actual' ? 'Факт FTE' : 'Прогноз FTE']} />
                        <Bar dataKey="received" fill="#bfdbfe" radius={[4, 4, 0, 0]} />
                        <Line type="monotone" dataKey="fte" stroke="#2563eb" strokeWidth={2} dot={false} />
                        <Line type="monotone" dataKey="actual" stroke="#059669" strokeWidth={2} dot={false} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>

                  <div className="mt-5 overflow-x-auto rounded-lg border border-slate-200">
                    <table className="min-w-[1180px] w-full divide-y divide-slate-200 text-sm">
                      <thead className="sticky top-0 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                        <tr>
                          <th className="px-3 py-3 text-left">Час</th>
                          <th className="px-3 py-3 text-right">Получено</th>
                          <th className="px-3 py-3 text-right">Принято</th>
                          <th className="px-3 py-3 text-right">Потеряно</th>
                          <th className="px-3 py-3 text-right">% Неотв</th>
                          <th className="px-3 py-3 text-right">Средн. прод.</th>
                          <th className="px-3 py-3 text-right">Ожидание</th>
                          <th className="px-3 py-3 text-right">Прогноз FTE</th>
                          <th className="px-3 py-3 text-right">План</th>
                          <th className="px-3 py-3 text-right">Факт</th>
                          <th className="px-3 py-3 text-right">Разница</th>
                          <th className="px-3 py-3 text-left">Комментарий</th>
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
                              <td className="px-3 py-2 text-right">{formatInt(row.received_calls)}</td>
                              <td className="px-3 py-2 text-right">{formatInt(row.accepted_calls)}</td>
                              <td className="px-3 py-2 text-right">{formatInt(row.lost_calls)}</td>
                              <td className="px-3 py-2 text-right">{formatPercent(row.no_answer_rate)}</td>
                              <td className="px-3 py-2 text-right">{formatSeconds(row.avg_talk_seconds)}</td>
                              <td className="px-3 py-2 text-right">{formatSeconds(row.avg_wait_seconds)}</td>
                              <td className="px-3 py-2 text-right font-semibold text-blue-700">{formatNumber(row.forecast_fte, 2)}</td>
                              <td className="px-3 py-2 text-right">
                                <input type="number" step="0.25" value={row.planned_fte ?? 0} onChange={(event) => updateHourLocal(row.hour, 'planned_fte', event.target.value)} className="h-8 w-20 rounded-md border border-slate-200 px-2 text-right text-sm" />
                              </td>
                              <td className="px-3 py-2 text-right">
                                <input type="number" step="0.25" value={row.actual_fte ?? 0} onChange={(event) => updateHourLocal(row.hour, 'actual_fte', event.target.value)} className="h-8 w-20 rounded-md border border-slate-200 px-2 text-right text-sm" />
                              </td>
                              <td className="px-3 py-2 text-right">
                                <span className={`inline-flex min-w-16 justify-center rounded-md px-2 py-1 text-xs font-semibold ${deltaClass}`}>
                                  {formatNumber(delta, 2)}
                                </span>
                              </td>
                              <td className="px-3 py-2">
                                <input value={row.comments || ''} onChange={(event) => updateHourLocal(row.hour, 'comments', event.target.value)} className="h-8 w-56 rounded-md border border-slate-200 px-2 text-sm" placeholder="Комментарий" />
                              </td>
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
          </main>
        </div>
      </div>
    </div>
  );
};

export default ResourceFteView;
