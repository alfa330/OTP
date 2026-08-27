import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  CalendarDays,
  Clock3,
  LayoutDashboard,
  MessageSquare,
  RefreshCw,
  Save,
  SlidersHorizontal,
  TrendingUp,
  Users,
} from 'lucide-react';
import ResourceSchedulePlanner from './ResourceSchedulePlanner';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

// Раздел живёт на том же визуале и функционале, что «Расчет ресурсов · Линия»:
// те же вкладки, те же карточки, тот же планировщик графиков. Вкладок «Звонки»
// и «Биллинг Oktell» здесь нет — они про телефонию.
const VIEW_TABS = [
  { key: 'overview', label: 'Обзор', icon: LayoutDashboard },
  { key: 'next_week', label: 'Прогнозы', icon: TrendingUp },
  { key: 'schedule_planner', label: 'Графики', icon: CalendarDays },
  { key: 'settings', label: 'Настройки', icon: SlidersHorizontal },
];

const CHAT_API_PREFIX = '/api/resource_fte/chat';


/*
  Расчет ресурсов · Чат.

  Модель принципиально отличается от линии: среднего времени обработки НЕТ.
  request_time в Chat2Desk — это время жизни обращения до закрытия (медиана ~1,6 часа),
  а не работа оператора, поэтому умножать объём на него нельзя.

  Считаем от цели по сервису:
      Нужно чатников в час = Чаты в час / Чатов в час на одного чатника
  Ёмкость («чатов в час») — вводная: сколько чатов держит один чатник, не роняя
  «ответ внутри чата» ниже цели.
*/

const formatNumber = (value, digits = 1) =>
  new Intl.NumberFormat('ru-RU', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(Number(value || 0));

const formatInt = (value) => new Intl.NumberFormat('ru-RU').format(Math.round(Number(value || 0)));

const formatDate = (iso) => {
  if (!iso) return '—';
  const [year, month, day] = String(iso).split('-');
  return `${day}.${month}.${year}`;
};

const formatDateShort = (iso) => {
  if (!iso) return '—';
  const [, month, day] = String(iso).split('-');
  return `${day}.${month}`;
};

const hourLabel = (hour) => `${String(hour).padStart(2, '0')}:00`;

const describeTarget = (seconds) => {
  const total = Math.max(0, Math.round(Number(seconds || 0)));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (!minutes) return `${rest} сек`;
  return rest ? `${minutes} мин ${rest} сек` : `${minutes} мин`;
};

const SETTING_FIELDS = [
  {
    key: 'target_reply_seconds',
    label: 'Цель «ответ внутри чата», сек',
    hint: 'Среднее время ответа внутри диалога, к которому стремимся',
    step: 10,
    min: 30,
    max: 3600,
  },
  {
    key: 'capacity_per_hour',
    label: 'Чатов в час на одного чатника',
    hint: 'Сколько чатов держит один человек, не роняя цель. Главная вводная',
    step: 0.5,
    min: 0.5,
    max: 60,
    primary: true,
  },
  {
    key: 'shrinkage_coeff',
    label: 'Коэффициент усушки',
    hint: 'Отпуска, больничные, обучение. 0,9 — как на линии',
    step: 0.05,
    min: 0.1,
    max: 1,
  },
  {
    key: 'weekly_hours_per_operator',
    label: 'Часов в неделю на человека',
    hint: 'Норма для пересчёта чатнико-часов в людей',
    step: 1,
    min: 1,
    max: 168,
  },
  {
    key: 'base_weeks',
    label: 'Недель в базе прогноза',
    hint: 'Сколько последних ПОЛНЫХ недель усредняем по дням недели',
    step: 1,
    min: 1,
    max: 8,
  },
];

const StatCard = ({ icon: Icon, label, value, suffix, hint, tone = 'slate' }) => {
  const tones = {
    slate: 'border-slate-200 bg-white',
    blue: 'border-blue-200 bg-blue-50',
    amber: 'border-amber-200 bg-amber-50',
    green: 'border-emerald-200 bg-emerald-50',
  };
  return (
    <div className={`rounded-xl border p-4 shadow-sm ${tones[tone] || tones.slate}`}>
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-500">
        {Icon ? <Icon className="h-4 w-4" /> : null}
        <span>{label}</span>
      </div>
      <div className="mt-2 flex items-baseline gap-1">
        <span className="text-2xl font-semibold text-slate-900">{value}</span>
        {suffix ? <span className="text-sm text-slate-500">{suffix}</span> : null}
      </div>
      {hint ? <div className="mt-1 text-xs text-slate-500">{hint}</div> : null}
    </div>
  );
};

const ResourceChatFteView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader }) => {
  const [overview, setOverview] = useState(null);
  const [settingsDraft, setSettingsDraft] = useState(null);
  const [weekStart, setWeekStart] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [selectedDayIndex, setSelectedDayIndex] = useState(0);
  const [activeTab, setActiveTab] = useState('overview');
  const [plannerPeriodEnd, setPlannerPeriodEnd] = useState('');

  const apiRoot = String(apiBaseUrl || '').replace(/\/+$/, '');
  // Планировщик просит именно функцию сборки заголовков, а не готовый конфиг.
  const buildHeaders = useCallback(
    (extra = {}) => (typeof withAccessTokenHeader === 'function'
      ? withAccessTokenHeader({ ...extra })
      : { ...extra }),
    [withAccessTokenHeader],
  );

  const authConfig = useCallback(() => {
    const headers = typeof withAccessTokenHeader === 'function' ? withAccessTokenHeader({}) : {};
    return { headers, withCredentials: true };
  }, [withAccessTokenHeader]);

  const loadOverview = useCallback(async (targetWeek) => {
    setIsLoading(true);
    setLoadError('');
    try {
      const params = new URLSearchParams();
      if (targetWeek) params.set('week_start', targetWeek);
      const query = params.toString();
      const response = await axios.get(
        `${apiBaseUrl}/api/resource_fte/chat/overview${query ? `?${query}` : ''}`,
        authConfig(),
      );
      const payload = response?.data || {};
      setOverview(payload);
      setSettingsDraft(payload?.forecast?.settings ? { ...payload.forecast.settings } : null);
      setWeekStart(payload?.forecast?.week_start || '');
      setPlannerPeriodEnd(payload?.forecast?.period_end || payload?.forecast?.week_end || '');
      setSelectedDayIndex(0);
    } catch (error) {
      const message = error?.response?.data?.error || error?.message || 'Не удалось загрузить данные';
      setLoadError(message);
      showToast?.(message, 'error');
    } finally {
      setIsLoading(false);
    }
  }, [apiBaseUrl, authConfig, showToast]);

  useEffect(() => {
    loadOverview();
    // Первый заход — неделя определяется на сервере по свежим данным.
  }, [loadOverview]);

  const saveSettings = useCallback(async () => {
    if (!settingsDraft) return;
    setIsSaving(true);
    try {
      const response = await axios.put(
        `${apiBaseUrl}/api/resource_fte/chat/settings`,
        { ...settingsDraft, week_start: weekStart || undefined },
        authConfig(),
      );
      const payload = response?.data || {};
      setOverview((prev) => ({ ...(prev || {}), ...payload }));
      if (payload?.settings) setSettingsDraft({ ...payload.settings });
      showToast?.('Настройки сохранены, расчёт обновлён', 'success');
    } catch (error) {
      const message = error?.response?.data?.error || error?.message || 'Не удалось сохранить';
      showToast?.(message, 'error');
    } finally {
      setIsSaving(false);
    }
  }, [apiBaseUrl, authConfig, settingsDraft, showToast, weekStart]);

  const forecast = overview?.forecast || null;
  const days = forecast?.days || [];
  const totals = forecast?.totals || {};
  const settings = forecast?.settings || {};
  const selectedDay = days[selectedDayIndex] || days[0] || null;

  const hourlyChart = useMemo(() => {
    if (!selectedDay) return [];
    return (selectedDay.hourly_forecast || []).map((row) => ({
      hour: hourLabel(row.hour),
      chats: Number(row.forecast_chats || 0),
      needed: Number(row.forecast_fte || 0),
    }));
  }, [selectedDay]);

  const historyChart = useMemo(() => {
    const history = overview?.history || [];
    return history.map((row) => ({
      date: formatDateShort(row.date),
      chats: Number(row.chats || 0),
      short: row.short,
    }));
  }, [overview]);

  const shiftWeek = useCallback((deltaWeeks) => {
    if (!forecast?.week_start) return;
    const base = new Date(`${forecast.week_start}T00:00:00`);
    base.setDate(base.getDate() + deltaWeeks * 7);
    const next = base.toISOString().slice(0, 10);
    setWeekStart(next);
    loadOverview(next);
  }, [forecast, loadOverview]);

  const skipped = forecast?.skipped_base_weeks || [];

  return (
    <div className="space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-slate-900">
            <MessageSquare className="h-5 w-5 text-blue-600" />
            Расчет ресурсов · Чат
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-600">
            Считаем от цели по сервису, а не от среднего времени обработки: в чатах оно
            измеряет время жизни обращения, а не работу оператора. Потребность —
            это объём чатов, делённый на то, сколько чатов в час держит один человек
            при цели «ответ внутри чата» {describeTarget(settings.target_reply_seconds)}.
          </p>
        </div>
        <button
          type="button"
          onClick={() => loadOverview(weekStart)}
          disabled={isLoading}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
        >
          <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
          Обновить
        </button>
      </div>

      {loadError ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {loadError}
        </div>
      ) : null}

      <div className="flex flex-col gap-3 rounded-xl border-2 border-slate-200 bg-white p-2 shadow-sm lg:flex-row lg:items-center lg:justify-between">
        <div className="flex gap-1 overflow-x-auto">
          {VIEW_TABS.map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
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
      </div>

      {activeTab !== 'schedule_planner' ? (
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white p-3">
        <button
          type="button"
          onClick={() => shiftWeek(-1)}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
        >
          ← Неделя назад
        </button>
        <div className="flex items-center gap-2 text-sm font-medium text-slate-800">
          <CalendarDays className="h-4 w-4 text-slate-500" />
          {forecast ? `${formatDate(forecast.week_start)} — ${formatDate(forecast.week_end)}` : '—'}
        </div>
        <button
          type="button"
          onClick={() => shiftWeek(1)}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
        >
          Неделя вперёд →
        </button>
        {forecast?.base_week_starts?.length ? (
          <div className="text-xs text-slate-500">
            База: {forecast.base_week_starts.map((item) => formatDateShort(item)).join(', ')}
            {skipped.length ? (
              <span className="ml-2 text-amber-700">
                (пропущены неполные: {skipped.map((item) => formatDateShort(item.week_start)).join(', ')})
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
      ) : null}

      {activeTab !== 'schedule_planner' && activeTab !== 'settings' ? (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          icon={MessageSquare}
          label="Прогноз чатов"
          value={formatInt(totals.forecast_chats)}
          hint="за неделю"
          tone="blue"
        />
        <StatCard
          icon={Clock3}
          label="Чатнико-часов"
          value={formatNumber(totals.forecast_fte_hours, 1)}
          hint="объём ÷ ёмкость, по часам"
        />
        <StatCard
          icon={Users}
          label="Чатников с усушкой"
          value={formatNumber(totals.operators_with_shrinkage, 1)}
          hint={`без усушки ${formatNumber(totals.operators, 1)}`}
          tone="green"
        />
        <StatCard
          icon={TrendingUp}
          label="Пик в час"
          value={formatNumber(totals.peak_fte, 1)}
          hint="самый нагруженный час недели"
          tone="amber"
        />
      </div>
      ) : null}

      {activeTab === 'settings' ? (
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-800">
          <SlidersHorizontal className="h-4 w-4 text-slate-500" />
          Вводные
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {SETTING_FIELDS.map((field) => (
            <label key={field.key} className="block">
              <span className={`block text-xs font-medium ${field.primary ? 'text-amber-800' : 'text-slate-600'}`}>
                {field.label}
              </span>
              <input
                type="number"
                step={field.step}
                min={field.min}
                max={field.max}
                value={settingsDraft?.[field.key] ?? ''}
                onChange={(event) => setSettingsDraft((prev) => ({
                  ...(prev || {}),
                  [field.key]: event.target.value === '' ? '' : Number(event.target.value),
                }))}
                className={`mt-1 w-full rounded-lg border px-3 py-2 text-sm ${
                  field.primary ? 'border-amber-300 bg-amber-50' : 'border-slate-300'
                }`}
              />
              <span className="mt-1 block text-[11px] leading-snug text-slate-500">{field.hint}</span>
            </label>
          ))}
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            onClick={saveSettings}
            disabled={isSaving || !settingsDraft}
            className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
          >
            <Save className="h-4 w-4" />
            {isSaving ? 'Сохраняю...' : 'Сохранить и пересчитать'}
          </button>
          <span className="text-xs text-slate-500">
            Ниже ёмкость — больше людей. Это главный рычаг расчёта.
          </span>
        </div>
      </div>
      ) : null}

      {activeTab === 'schedule_planner' ? (
        <ResourceSchedulePlanner
          apiRoot={apiRoot}
          apiPrefix={CHAT_API_PREFIX}
          buildHeaders={buildHeaders}
          selectedWeekStart={weekStart}
          selectedPeriodEnd={plannerPeriodEnd || weekStart}
          onWeekStartChange={(value) => setWeekStart(value)}
          onPeriodChange={(start, end) => {
            setWeekStart(start);
            setPlannerPeriodEnd(end);
          }}
          notify={(message, tone) => showToast?.(message, tone)}
        />
      ) : null}

      {activeTab === 'overview' || activeTab === 'next_week' ? (
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="mb-3 text-sm font-semibold text-slate-800">Прогноз по дням недели</div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="px-3 py-2">День</th>
                <th className="px-3 py-2">Дата</th>
                <th className="px-3 py-2 text-right">Прогноз чатов</th>
                <th className="px-3 py-2 text-right">Чатнико-часов</th>
                <th className="px-3 py-2 text-right">Пик в час</th>
                <th className="px-3 py-2">База</th>
              </tr>
            </thead>
            <tbody>
              {days.map((day, index) => (
                <tr
                  key={day.forecast_date}
                  onClick={() => setSelectedDayIndex(index)}
                  className={`cursor-pointer border-b border-slate-100 hover:bg-slate-50 ${
                    index === selectedDayIndex ? 'bg-blue-50' : ''
                  }`}
                >
                  <td className="px-3 py-2 font-medium text-slate-800">{day.short}</td>
                  <td className="px-3 py-2 text-slate-600">{formatDate(day.forecast_date)}</td>
                  <td className="px-3 py-2 text-right">{formatInt(day.forecast_chats)}</td>
                  <td className="px-3 py-2 text-right">{formatNumber(day.forecast_fte_hours, 1)}</td>
                  <td className="px-3 py-2 text-right">{formatNumber(day.peak_fte, 1)}</td>
                  <td className="px-3 py-2 text-xs text-slate-500">
                    {(day.sources || [])
                      .filter((source) => source.has_data)
                      .map((source) => `${formatDateShort(source.date)} — ${formatInt(source.chats)}`)
                      .join(' · ') || 'нет данных'}
                  </td>
                </tr>
              ))}
              {!days.length && !isLoading ? (
                <tr>
                  <td colSpan={6} className="px-3 py-6 text-center text-sm text-slate-500">
                    Нет данных за базовые недели
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
      ) : null}

      {selectedDay && (activeTab === 'overview' || activeTab === 'next_week') ? (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-3 text-sm font-semibold text-slate-800">
            {selectedDay.label} · {formatDate(selectedDay.forecast_date)} — чаты и потребность по часам
          </div>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={hourlyChart} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={1} />
                <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                <Tooltip
                  formatter={(value, name) => [
                    formatNumber(value, name === 'Нужно чатников' ? 2 : 0),
                    name,
                  ]}
                />
                <Bar yAxisId="left" dataKey="chats" name="Чатов" fill="#93c5fd" radius={[3, 3, 0, 0]} />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="needed"
                  name="Нужно чатников"
                  stroke="#1d4ed8"
                  strokeWidth={2}
                  dot={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : null}

      {historyChart.length && activeTab === 'overview' ? (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-1 text-sm font-semibold text-slate-800">История объёма</div>
          <div className="mb-3 text-xs text-slate-500">
            {overview?.history_coverage?.from
              ? `${formatDate(overview.history_coverage.from)} — ${formatDate(overview.history_coverage.to)}, ${overview.history_coverage.days} дн.`
              : ''}
          </div>
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={historyChart} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} interval={2} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip formatter={(value) => [formatInt(value), 'Чатов']} />
                <Bar dataKey="chats" name="Чатов" fill="#bfdbfe" radius={[3, 3, 0, 0]} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : null}

      {overview?.channels?.length && activeTab === 'overview' ? (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-3 text-sm font-semibold text-slate-800">Каналы за базовые недели</div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-3 py-2">Канал</th>
                  <th className="px-3 py-2 text-right">Чатов</th>
                  <th className="px-3 py-2 text-right">Доля</th>
                </tr>
              </thead>
              <tbody>
                {overview.channels.map((channel) => (
                  <tr key={channel.channel} className="border-b border-slate-100">
                    <td className="px-3 py-2 text-slate-800">{channel.channel}</td>
                    <td className="px-3 py-2 text-right">{formatInt(channel.chats)}</td>
                    <td className="px-3 py-2 text-right">{formatNumber(channel.share * 100, 1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default ResourceChatFteView;
