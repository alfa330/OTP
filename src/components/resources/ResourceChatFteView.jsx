import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
  AlertTriangle,
  BarChart3,
  CalendarDays,
  CheckCircle2,
  Clock3,
  Gauge,
  LayoutDashboard,
  ListChecks,
  MessageSquare,
  RefreshCw,
  Save,
  ShieldAlert,
  SlidersHorizontal,
  Target,
  Timer,
  TrendingUp,
} from 'lucide-react';
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
import ResourceSchedulePlanner from './ResourceSchedulePlanner';
import {
  CalendarPicker,
  EmptyState,
  OperatorAvailabilityDetailsModal,
  OperatorSummaryCard,
  SectionCard,
  StatCard,
  ToggleSwitch,
  addDaysIso,
  formatDate,
  formatDateShort,
  formatInt,
  formatNumber,
  formatPercent,
  formatSignedNumber,
  todayIso,
} from './resourceChatShared';

/*
  Расчет ресурсов · Чат.

  Модель принципиально отличается от линии: среднего времени обработки НЕТ.
  Время жизни обращения в Chat2Desk (медиана ~1,6 часа) меряет ожидание клиента,
  а не работу оператора, поэтому умножать объём на него нельзя.

  Считаем от цели по сервису:
      Нужно чатников в час = Чаты в час / Чатов в час на одного чатника
  Ёмкость руками больше не вводится — она ВЫВОДИТСЯ из цели по замеренной кривой
  (сервер, resolve_chat_capacity). Здесь ёмкость только показывается, вместе с тем,
  из какой именно цели она получена.

  Цели две, и путать их нельзя:
    • «ответ внутри чата» (5 мин) — рычаг, из него выводится ёмкость;
    • «первый ответ (реакция)» (1 мин) — единственная измеримая по нашей базе,
      именно по ней считается «в цель» на вкладке «Чаты».
*/

// Вкладок пять. Телефонных («Звонки», «Биллинг Oktell») здесь нет и быть не может:
// у чата другая единица работы и другой источник факта.
const VIEW_TABS = [
  { key: 'overview', label: 'Обзор', icon: LayoutDashboard },
  { key: 'next_week', label: 'Прогнозы', icon: TrendingUp },
  { key: 'chats', label: 'Чаты', icon: MessageSquare },
  { key: 'schedule_planner', label: 'Графики', icon: CalendarDays },
  { key: 'settings', label: 'Настройки', icon: SlidersHorizontal },
];

const CHAT_API_PREFIX = '/api/resource_fte/chat';

// В расчёт чата идут только ставки 1 и 0,75 — тот же набор, что CHAT_RATES на сервере.
// Плашка сверху обещает это прямым текстом, значит и карточка доступности обязана
// считать по нему же, иначе на экране два разных числа про один и тот же штат.
const CHAT_RATE_VALUES = [1, 0.75];

const isChatRate = (rate) => CHAT_RATE_VALUES.some((allowed) => Math.abs(Number(rate || 0) - allowed) < 0.001);

// Свой ключ хранения: у линии в её ключе лежат показатели, которых в чате нет вовсе,
// и при переиспользовании они бы подмешались мёртвыми тумблерами.
const DISPLAY_PREFERENCES_STORAGE_KEY = 'otp_resource_chat_display_v1';

// Единственный источник правды по показателям: и тумблеры настроек, и условия показа
// читают эти же ключи. Разъехаться им негде.
const DISPLAY_GROUPS = [
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

const DEFAULT_DISPLAY_OPTIONS = DISPLAY_GROUPS.reduce((acc, group) => {
  group.items.forEach(([key]) => {
    // По умолчанию включено всё, кроме побочных колонок и редких карточек: раздел
    // должен открываться читаемым, а не полотном из двух десятков столбцов.
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

const loadDisplayOptions = () => {
  if (typeof window === 'undefined') return { ...DEFAULT_DISPLAY_OPTIONS };
  try {
    const parsed = JSON.parse(window.localStorage.getItem(DISPLAY_PREFERENCES_STORAGE_KEY) || '{}');
    return { ...DEFAULT_DISPLAY_OPTIONS, ...(parsed && typeof parsed === 'object' ? parsed : {}) };
  } catch {
    return { ...DEFAULT_DISPLAY_OPTIONS };
  }
};

const hourLabel = (hour) => `${String(hour).padStart(2, '0')}:00`;

const describeTarget = (seconds) => {
  const total = Math.max(0, Math.round(Number(seconds || 0)));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (!minutes) return `${rest} сек`;
  return rest ? `${minutes} мин ${rest} сек` : `${minutes} мин`;
};

const formatSeconds = (value) => (
  value === null || value === undefined || value === '' ? '—' : `${formatNumber(value, 0)} с`
);

const CHAT_FTE_ROUNDING_LABELS = [
  ['half', 'до половины'],
  ['exact', 'без округления'],
  ['ceil', 'вверх'],
];

const errorText = (error, fallback) => (
  error?.response?.data?.error || error?.message || fallback
);

const inputClass =
  'h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100';

const ResourceChatFteView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader }) => {
  const [overview, setOverview] = useState(null);
  const [settingsDraft, setSettingsDraft] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isRecalculating, setIsRecalculating] = useState(false);
  const [isFitting, setIsFitting] = useState(false);
  const [loadError, setLoadError] = useState('');

  const [forecastStart, setForecastStart] = useState('');
  const [forecastEnd, setForecastEnd] = useState('');
  const [historyFrom, setHistoryFrom] = useState('');
  const [historyTo, setHistoryTo] = useState('');
  // День храним ДАТОЙ, а не номером в списке: при произвольном периоде номер
  // после смены дат указывает на чужой день.
  const [selectedForecastDate, setSelectedForecastDate] = useState('');
  const [dayDetails, setDayDetails] = useState(null);

  const [analyticsFrom, setAnalyticsFrom] = useState('');
  const [analyticsTo, setAnalyticsTo] = useState('');
  const [analytics, setAnalytics] = useState(null);
  const [isAnalyticsLoading, setIsAnalyticsLoading] = useState(false);
  const [chatsChartMode, setChatsChartMode] = useState('volume');

  const [availability, setAvailability] = useState(null);
  const [availabilityError, setAvailabilityError] = useState('');
  const [isAvailabilityLoading, setIsAvailabilityLoading] = useState(false);
  const [isOperatorDetailsOpen, setIsOperatorDetailsOpen] = useState(false);

  const [displayOptions, setDisplayOptions] = useState(loadDisplayOptions);

  // Раздел живёт открытым сутками. Посчитай «сегодня» один раз при монтировании — после
  // полуночи текущим остался бы вчерашний день: новый не получил бы ни бейджа, ни
  // загрузки факта. Поэтому дату пересматриваем по таймеру; при совпадении состояние
  // не меняется, и лишнего рендера не будет.
  const [todayValue, setTodayValue] = useState(todayIso);
  useEffect(() => {
    const timer = setInterval(() => {
      setTodayValue((current) => {
        const next = todayIso();
        return next === current ? current : next;
      });
    }, 60000);
    return () => clearInterval(timer);
  }, []);
  const apiRoot = String(apiBaseUrl || '').replace(/\/+$/, '');

  // showToast приходит новой функцией на каждый рендер родителя. Попади он в массив
  // зависимостей загрузки — витрина запрашивала бы себя по кругу.
  const showToastRef = useRef(showToast);
  const withAccessTokenHeaderRef = useRef(withAccessTokenHeader);

  // Метки актуальности загрузок. Быстрый перещёлк (двойной клик «Неделя вперёд», день A
  // сразу после дня B, смена периода аналитики) держит в полёте несколько запросов, и
  // поздний ответ на РАННИЙ запрос затирал свежие данные. У витрины вместе с ними
  // откатывались и календари: loadOverview выставляет период из ответа.
  const overviewRequestRef = useRef(0);
  const dayRequestRef = useRef(0);
  const analyticsRequestRef = useRef(0);
  useEffect(() => {
    showToastRef.current = showToast;
    withAccessTokenHeaderRef.current = withAccessTokenHeader;
  });

  const notify = useCallback((message, tone) => {
    showToastRef.current?.(message, tone);
  }, []);

  // Планировщик просит именно функцию сборки заголовков, а не готовый конфиг.
  const buildHeaders = useCallback((extra = {}) => (
    typeof withAccessTokenHeaderRef.current === 'function'
      ? withAccessTokenHeaderRef.current({ ...extra })
      : { ...extra }
  ), []);

  const authConfig = useCallback(() => ({ headers: buildHeaders(), withCredentials: true }), [buildHeaders]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(DISPLAY_PREFERENCES_STORAGE_KEY, JSON.stringify(displayOptions));
  }, [displayOptions]);

  const toggleDisplayOption = useCallback((key, value) => {
    setDisplayOptions((current) => ({ ...current, [key]: value }));
  }, []);

  const loadOverview = useCallback(async (options = {}) => {
    const requestId = overviewRequestRef.current + 1;
    overviewRequestRef.current = requestId;
    setIsLoading(true);
    setLoadError('');
    try {
      const params = new URLSearchParams();
      if (options.weekStart) params.set('week_start', options.weekStart);
      if (options.periodEnd) params.set('period_end', options.periodEnd);
      if (options.dateFrom) params.set('date_from', options.dateFrom);
      if (options.dateTo) params.set('date_to', options.dateTo);
      const query = params.toString();
      const response = await axios.get(
        `${apiBaseUrl}${CHAT_API_PREFIX}/overview${query ? `?${query}` : ''}`,
        authConfig(),
      );
      if (overviewRequestRef.current !== requestId) return;
      const payload = response?.data || {};
      setOverview(payload);
      setSettingsDraft(payload?.settings ? { ...payload.settings } : null);
      const days = payload?.forecast?.days || [];
      setForecastStart(payload?.forecast?.period_start || '');
      setForecastEnd(payload?.forecast?.period_end || '');
      setHistoryFrom(payload?.history_coverage?.from || '');
      setHistoryTo(payload?.history_coverage?.to || '');
      setSelectedForecastDate((current) => {
        // Дату «сегодня» берём в момент выбора, а не из зависимостей: иначе смена суток
        // пересоздавала бы загрузку, и первый эффект перезапросил бы витрину без
        // периода — выбранные пользователем даты слетели бы в полночь.
        if (current && days.some((day) => day.forecast_date === current)) return current;
        const today = days.find((day) => day.forecast_date === todayIso());
        return today?.forecast_date || days[0]?.forecast_date || '';
      });
    } catch (error) {
      if (overviewRequestRef.current !== requestId) return;
      const message = errorText(error, 'Не удалось загрузить данные');
      setLoadError(message);
      notify(message, 'error');
    } finally {
      if (overviewRequestRef.current === requestId) setIsLoading(false);
    }
  }, [apiBaseUrl, authConfig, notify]);

  useEffect(() => {
    // Первый заход: период определяет сервер по свежим данным чатов.
    loadOverview();
  }, [loadOverview]);

  const reload = useCallback(() => loadOverview({
    weekStart: forecastStart,
    periodEnd: forecastEnd,
    dateFrom: historyFrom,
    dateTo: historyTo,
  }), [forecastEnd, forecastStart, historyFrom, historyTo, loadOverview]);

  const applyForecastPeriod = useCallback((start, end) => {
    setForecastStart(start);
    setForecastEnd(end);
    setSelectedForecastDate(start);
    loadOverview({ weekStart: start, periodEnd: end, dateFrom: historyFrom, dateTo: historyTo });
  }, [historyFrom, historyTo, loadOverview]);

  const applyHistoryPeriod = useCallback((from, to) => {
    setHistoryFrom(from);
    setHistoryTo(to);
    loadOverview({ weekStart: forecastStart, periodEnd: forecastEnd, dateFrom: from, dateTo: to });
  }, [forecastEnd, forecastStart, loadOverview]);

  const shiftForecastWeek = useCallback((deltaWeeks) => {
    if (!forecastStart) return;
    // Только addDaysIso: приведение к UTC уводило неделю на сутки назад.
    const start = addDaysIso(forecastStart, deltaWeeks * 7);
    const end = forecastEnd ? addDaysIso(forecastEnd, deltaWeeks * 7) : addDaysIso(start, 6);
    applyForecastPeriod(start, end);
  }, [applyForecastPeriod, forecastEnd, forecastStart]);

  const loadDay = useCallback(async (dayValue) => {
    if (!dayValue) return;
    const requestId = dayRequestRef.current + 1;
    dayRequestRef.current = requestId;
    try {
      const response = await axios.get(
        `${apiBaseUrl}${CHAT_API_PREFIX}/day/${dayValue}`,
        authConfig(),
      );
      if (dayRequestRef.current !== requestId) return;
      setDayDetails(response?.data?.day || null);
    } catch (error) {
      if (dayRequestRef.current !== requestId) return;
      setDayDetails(null);
    }
  }, [apiBaseUrl, authConfig]);

  useEffect(() => {
    if (!selectedForecastDate || selectedForecastDate > todayValue) {
      // Сдвигаем метку: ответ уже улетевшего запроса не должен вернуть на экран
      // детализацию прежнего дня поверх пустоты будущего.
      dayRequestRef.current += 1;
      setDayDetails(null);
      return;
    }
    loadDay(selectedForecastDate);
  }, [loadDay, selectedForecastDate, todayValue]);

  const loadAnalytics = useCallback(async (from, to) => {
    const requestId = analyticsRequestRef.current + 1;
    analyticsRequestRef.current = requestId;
    setIsAnalyticsLoading(true);
    try {
      const params = new URLSearchParams();
      if (from) params.set('date_from', from);
      if (to) params.set('date_to', to);
      const query = params.toString();
      const response = await axios.get(
        `${apiBaseUrl}${CHAT_API_PREFIX}/analytics${query ? `?${query}` : ''}`,
        authConfig(),
      );
      if (analyticsRequestRef.current !== requestId) return;
      setAnalytics(response?.data || null);
    } catch (error) {
      if (analyticsRequestRef.current !== requestId) return;
      notify(errorText(error, 'Не удалось загрузить аналитику чатов'), 'error');
    } finally {
      if (analyticsRequestRef.current === requestId) setIsAnalyticsLoading(false);
    }
  }, [apiBaseUrl, authConfig, notify]);

  const latestChatDay = overview?.latest_chat_day || '';

  useEffect(() => {
    if (activeTab !== 'chats') return;
    if (!analyticsFrom || !analyticsTo) {
      if (!latestChatDay) return;
      setAnalyticsTo(latestChatDay);
      setAnalyticsFrom(addDaysIso(latestChatDay, -13));
      return;
    }
    loadAnalytics(analyticsFrom, analyticsTo);
  }, [activeTab, analyticsFrom, analyticsTo, latestChatDay, loadAnalytics]);

  const availabilityCacheRef = useRef({});

  const loadAvailability = useCallback(async (start, end) => {
    const cacheKey = `${start}|${end}`;
    const cached = availabilityCacheRef.current[cacheKey];
    if (cached) {
      setAvailability(cached);
      setAvailabilityError('');
      return;
    }
    setIsAvailabilityLoading(true);
    setAvailabilityError('');
    try {
      const response = await axios.get(`${apiBaseUrl}${CHAT_API_PREFIX}/operator_availability`, {
        ...authConfig(),
        params: { forecast_date_from: start, forecast_date_to: end },
      });
      const payload = response?.data?.availability || null;
      if (payload) availabilityCacheRef.current[cacheKey] = payload;
      setAvailability(payload);
    } catch (error) {
      setAvailabilityError(errorText(error, 'Не удалось загрузить доступность чатников'));
    } finally {
      setIsAvailabilityLoading(false);
    }
  }, [apiBaseUrl, authConfig]);

  useEffect(() => {
    if (!forecastStart || !forecastEnd) return;
    loadAvailability(forecastStart, forecastEnd);
  }, [forecastEnd, forecastStart, loadAvailability]);

  const saveSettings = useCallback(async () => {
    if (!settingsDraft) return;
    setIsSaving(true);
    try {
      const response = await axios.put(
        `${apiBaseUrl}${CHAT_API_PREFIX}/settings`,
        {
          target_reply_seconds: settingsDraft.target_reply_seconds,
          target_first_reply_seconds: settingsDraft.target_first_reply_seconds,
          capacity_manual: settingsDraft.capacity_manual,
          shrinkage_coeff: settingsDraft.shrinkage_coeff,
          weekly_hours_per_operator: settingsDraft.weekly_hours_per_operator,
          base_weeks: settingsDraft.base_weeks,
          fte_rounding: settingsDraft.fte_rounding,
          week_start: forecastStart || undefined,
        },
        authConfig(),
      );
      const payload = response?.data || {};
      if (payload?.settings) setSettingsDraft({ ...payload.settings });
      // Витрину из ответа НЕ берём. PUT знает только `week_start` и отвечает расчётом за
      // свои 7 дней прогноза и 45 дней истории, а календари продолжают показывать
      // выбранный период — цифры одного отрезка вставали бы под датами другого.
      // Поэтому перезапрашиваем витрину теми же параметрами, что выбраны сейчас.
      await reload();
      notify('Вводные сохранены, расчёт обновлён', 'success');
    } catch (error) {
      notify(errorText(error, 'Не удалось сохранить'), 'error');
    } finally {
      setIsSaving(false);
    }
  }, [apiBaseUrl, authConfig, forecastStart, notify, reload, settingsDraft]);

  const recalculate = useCallback(async () => {
    setIsRecalculating(true);
    try {
      const response = await axios.post(`${apiBaseUrl}${CHAT_API_PREFIX}/recalculate`, {}, authConfig());
      const payload = response?.data || {};
      notify(`Пересчитано дней: ${formatInt(payload.days)}, часов: ${formatInt(payload.rows)}`, 'success');
      await reload();
    } catch (error) {
      notify(errorText(error, 'Не удалось пересчитать'), 'error');
    } finally {
      setIsRecalculating(false);
    }
  }, [apiBaseUrl, authConfig, notify, reload]);

  const fitFirstReplyCurve = useCallback(async () => {
    setIsFitting(true);
    try {
      const response = await axios.post(`${apiBaseUrl}${CHAT_API_PREFIX}/fit_first_reply`, {}, authConfig());
      const payload = response?.data || {};
      if (!payload.fitted) {
        notify(payload.reason === 'NO_CHAT_DATA'
          ? 'Замерить не по чему: чатов за период нет'
          : `Точек для замера мало: ${formatInt(payload.points)}`, 'error');
      } else if (payload.target_unreachable) {
        notify('Замер выполнен: цель первого ответа на этой кривой недостижима', 'error');
      } else {
        notify(`Кривая замерена по ${formatInt(payload.points)} часам, связь ${formatNumber(payload.correlation, 2)}`, 'success');
      }
      await reload();
    } catch (error) {
      notify(errorText(error, 'Не удалось замерить кривую'), 'error');
    } finally {
      setIsFitting(false);
    }
  }, [apiBaseUrl, authConfig, notify, reload]);

  // ---------------------------------------------------------------------------
  // Производные данные
  // ---------------------------------------------------------------------------

  const forecast = overview?.forecast || null;
  const forecastDays = forecast?.days || [];
  const totals = forecast?.totals || {};
  const settings = overview?.settings || forecast?.settings || {};
  const capacityExplain = overview?.capacity_explain || {};
  const uplift = overview?.uplift || null;
  const actualDays = overview?.actual?.days || [];
  const coveredDays = overview?.covered_days || [];
  const channels = overview?.channels || [];
  const weekdayProfile = overview?.weekday_profile || [];
  const offScaleRates = forecast?.operator_capacity?.off_scale_rates || [];
  const skippedBaseWeeks = forecast?.skipped_base_weeks || [];
  const capacityPerHour = Math.max(0.01, Number(settings.capacity_per_hour || totals.capacity_per_hour || 17));

  const overviewSummary = useMemo(() => {
    const chats = actualDays.reduce((acc, row) => acc + Number(row.chats || 0), 0);
    const inTarget = actualDays.reduce((acc, row) => acc + Number(row.in_target || 0), 0);
    const online = actualDays.reduce((acc, row) => acc + Number(row.actual_online_hours || 0), 0);
    return {
      chats,
      inTarget,
      online,
      days: actualDays.length,
      inTargetShare: chats > 0 ? inTarget / chats : 0,
      needFteHours: chats / capacityPerHour,
    };
  }, [actualDays, capacityPerHour]);

  const historyTrendData = useMemo(() => actualDays.map((row) => ({
    date: formatDateShort(row.date),
    chats: Number(row.chats || 0),
    // Потребность дня — та же формула часа, только сложенная: объём ÷ ёмкость.
    needFte: Number(row.chats || 0) / capacityPerHour,
    actualFte: Number(row.actual_online_hours || 0),
  })), [actualDays, capacityPerHour]);

  const upliftDailyData = useMemo(() => (uplift?.daily || []).map((row) => ({
    date: row.date,
    dateLabel: formatDateShort(row.date),
    forecastChats: Number(row.forecast_chats || 0),
    actualChats: Number(row.actual_chats || 0),
    positiveDeltaChats: Number(row.positive_delta_chats || 0),
    weight: Number(row.weight || 0),
    status: row.status,
    positiveHourShare: Number(row.source_hour_count || 0) > 0
      ? Number(row.positive_hour_count || 0) / Number(row.source_hour_count || 1)
      : 0,
  })), [uplift]);

  const upliftTopHours = useMemo(() => (uplift?.hourly || [])
    .filter((row) => Number(row.weighted_delta_chats || 0) > 0)
    .sort((a, b) => Number(b.weighted_delta_chats || 0) - Number(a.weighted_delta_chats || 0))
    .slice(0, 5), [uplift]);

  const selectedForecastDay = useMemo(() => (
    forecastDays.find((day) => day.forecast_date === selectedForecastDate) || forecastDays[0] || null
  ), [forecastDays, selectedForecastDate]);

  const dayHoursByHour = useMemo(() => {
    const rows = dayDetails?.date === selectedForecastDay?.forecast_date ? (dayDetails?.hours || []) : [];
    return rows.reduce((acc, row) => ({ ...acc, [Number(row.hour)]: row }), {});
  }, [dayDetails, selectedForecastDay]);

  const hasDayActual = Object.keys(dayHoursByHour).length > 0 && Boolean(selectedForecastDay?.has_actual);

  const selectedHourlyData = useMemo(() => (selectedForecastDay?.hourly_forecast || []).map((row) => {
    const actual = dayHoursByHour[Number(row.hour)] || null;
    return {
      hour: hourLabel(row.hour),
      hourNumber: Number(row.hour),
      chats: Number(row.forecast_chats || 0),
      upliftChats: Number(row.incident_uplift_chats || 0),
      fte: Number(row.forecast_fte || 0),
      adjustedFte: Number(row.incident_adjusted_fte ?? row.forecast_fte ?? 0),
      actualChats: actual ? Number(actual.chats || 0) : null,
      actualHours: actual ? Number(actual.actual_online_hours || 0) : null,
      firstReply: actual ? actual.avg_first_reply_seconds : null,
    };
  }), [dayHoursByHour, selectedForecastDay]);

  const selectedPeakHours = useMemo(() => [...(selectedForecastDay?.hourly_forecast || [])]
    .sort((a, b) => Number(b.forecast_fte || 0) - Number(a.forecast_fte || 0))
    .slice(0, 5), [selectedForecastDay]);

  const upliftAvailable = Number(totals.uplift_chats || 0) > 0.01;

  const requiredFte = Number(totals.operators_with_shrinkage || 0);
  const requiredWithUplift = useMemo(() => {
    const perOperator = Number(totals.period_hours_per_operator || 0);
    const shrink = Math.min(Math.max(Number(settings.shrinkage_coeff || 0.9), 0.01), 1);
    if (perOperator <= 0) return requiredFte;
    const hours = Number(totals.forecast_fte_hours || 0) + Number(totals.uplift_fte_hours || 0);
    return hours / perOperator / shrink;
  }, [requiredFte, settings.shrinkage_coeff, totals]);

  // Ручка доступности считает ВСЕ ставки направления, включая 0,5, которых в расчёте
  // чата нет. Из-за этого «Есть сейчас» показывало 13,75 FTE, а «Доступно» рядом —
  // 16,75 FTE: два числа про один штат. Разбивка по ставкам приходит в том же ответе,
  // по ней и пересобираем карточку, а отброшенных показываем отдельной строкой,
  // чтобы люди не исчезали молча.
  const chatAvailability = useMemo(() => {
    const rows = Array.isArray(availability?.periodAvailableOperatorRates)
      ? availability.periodAvailableOperatorRates
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
  }, [availability]);

  const availableFte = chatAvailability
    ? chatAvailability.fte
    : Number(availability?.periodAvailableOperatorFte ?? totals.current_operator_fte ?? 0);
  const availableCount = chatAvailability
    ? chatAvailability.count
    : Number(availability?.periodAvailableOperatorCount || 0);
  const availableTotalCount = chatAvailability
    ? chatAvailability.totalCount
    : Number(availability?.periodOperatorCount || totals.head_count || 0);

  const operatorDetailsForecast = useMemo(() => ({
    ...(availability || {}),
    // Детализация читает те же ключи, что и карточка: разъехаться им нельзя. Строки
    // людей вне ставок чата помечаем незасчитанными — иначе сумма вкладов в модальном
    // окне не сходилась бы с «Доступно» на карточке.
    periodOperatorAvailabilityDetails: (Array.isArray(availability?.periodOperatorAvailabilityDetails)
      ? availability.periodOperatorAvailabilityDetails
      : []).map((operator) => (
      isChatRate(operator?.rate) ? operator : { ...operator, included: false, fteContribution: 0 }
    )),
    periodAvailableOperatorFte: availableFte,
    periodAvailableOperatorCount: availableCount,
    periodAvailableOperatorFteGap: availableFte - requiredFte,
    operatorsWithShrinkage: requiredFte,
    baseOperators: Number(totals.operators || 0),
    currentOperatorFte: Number(totals.current_operator_fte || 0),
  }), [availability, availableCount, availableFte, requiredFte, totals]);

  const analyticsTotals = analytics?.totals || {};
  const analyticsDays = analytics?.days || [];
  const analyticsRiskHours = analytics?.risk_hours || [];
  const analyticsChannels = analytics?.channels || [];

  const analyticsChartData = useMemo(() => analyticsDays.map((row) => {
    const chats = Number(row.chats || 0);
    const inTarget = Number(row.in_target || 0);
    return {
      date: formatDateShort(row.date),
      chats,
      forecastChats: Number(row.forecast_chats || 0),
      inTarget,
      outOfTarget: Math.max(0, chats - inTarget),
      firstReply: row.avg_first_reply_seconds === null ? null : Number(row.avg_first_reply_seconds),
    };
  }), [analyticsDays]);

  const visibleOverviewMetrics = [
    'metricForecastChats',
    'metricForecastFteHours',
    'metricActualFteHours',
    'metricFteDelta',
    'metricInTargetShare',
    'metricCoveredDays',
  ].filter((key) => displayOptions[key]).length;

  const targetFirstSeconds = Number(settings.target_first_reply_seconds || 60);
  const capacityIsManual = settingsDraft?.capacity_manual !== null
    && settingsDraft?.capacity_manual !== undefined
    && settingsDraft?.capacity_manual !== '';

  return (
    <div className="space-y-5 p-4 sm:p-6">
      <OperatorAvailabilityDetailsModal
        open={isOperatorDetailsOpen}
        onClose={() => setIsOperatorDetailsOpen(false)}
        forecast={operatorDetailsForecast}
        isLoading={isAvailabilityLoading}
        error={availabilityError}
      />

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-slate-900">
            <MessageSquare className="h-5 w-5 text-blue-600" />
            Расчет ресурсов · Чат
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-600">
            Считаем от цели по сервису, а не от среднего времени обработки: в чатах оно
            меряет ожидание клиента, а не работу оператора. Потребность — это объём чатов,
            делённый на то, сколько чатов в час держит один человек при цели
            «ответ внутри чата» {describeTarget(settings.target_reply_seconds)}.
          </p>
        </div>
        <button
          type="button"
          onClick={reload}
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

      {offScaleRates.length ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          В чате только ставки 1 и 0,75. В направлении есть люди с другой ставкой —{' '}
          {offScaleRates.map((item) => `${formatNumber(item.rate, 2)}: ${formatInt(item.count)} чел.`).join(', ')}
          {' '}— они не учтены в расчёте и не попадут в график. Похоже на расхождение в карточках.
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
        <button
          type="button"
          onClick={() => setActiveTab('settings')}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
        >
          <ListChecks size={16} />
          Показатели
        </button>
      </div>

      {activeTab === 'overview' ? (
        <div className="space-y-5">
          <SectionCard
            title="Период истории"
            description="За какой отрезок смотрим факт: объём чатов, отработанные чатнико-часы и первый ответ."
            actions={(
              <div className="w-full sm:w-[320px]">
                <CalendarPicker
                  mode="range"
                  label="Период истории"
                  startValue={historyFrom}
                  endValue={historyTo}
                  onRangeChange={applyHistoryPeriod}
                  loadedDates={coveredDays}
                />
              </div>
            )}
          />

          {visibleOverviewMetrics > 0 ? (
            <div className={`grid gap-3 md:grid-cols-2 ${visibleOverviewMetrics >= 5 ? 'xl:grid-cols-6' : 'xl:grid-cols-4'}`}>
              {displayOptions.metricForecastChats ? (
                <StatCard
                  icon={MessageSquare}
                  label="Прогноз чатов"
                  value={formatInt(totals.forecast_chats)}
                  hint={`Период ${formatDate(forecast?.period_start)} — ${formatDate(forecast?.period_end)}`}
                  tone="blue"
                />
              ) : null}
              {displayOptions.metricForecastFteHours ? (
                <StatCard
                  icon={TrendingUp}
                  label="Чатнико-часы прогноза"
                  value={formatNumber(totals.forecast_fte_hours, 1)}
                  hint="Объём ÷ ёмкость, по часам"
                  tone="blue"
                />
              ) : null}
              {displayOptions.metricActualFteHours ? (
                <StatCard
                  icon={Clock3}
                  label="Факт чатнико-часов"
                  value={formatNumber(overviewSummary.online, 1)}
                  hint="Онлайн-сегменты чатников за период истории"
                  tone="emerald"
                />
              ) : null}
              {displayOptions.metricFteDelta ? (
                <StatCard
                  icon={ShieldAlert}
                  label="Разница с прогнозом"
                  value={formatSignedNumber(overviewSummary.online - overviewSummary.needFteHours, 1)}
                  hint="Факт минус потребность за период истории"
                  tone={overviewSummary.online - overviewSummary.needFteHours < -0.5 ? 'rose' : 'emerald'}
                />
              ) : null}
              {displayOptions.metricInTargetShare ? (
                <StatCard
                  icon={Target}
                  label="Первый ответ в цель"
                  value={formatPercent(overviewSummary.inTargetShare)}
                  hint={`Цель ${describeTarget(targetFirstSeconds)} до первой реплики`}
                  tone={overviewSummary.inTargetShare < 0.8 ? 'amber' : 'emerald'}
                />
              ) : null}
              {displayOptions.metricCoveredDays ? (
                <StatCard
                  icon={CalendarDays}
                  label="Дней с чатами"
                  value={formatInt(overviewSummary.days)}
                  hint="В выбранном периоде истории"
                  tone="slate"
                />
              ) : null}
            </div>
          ) : null}

          <SectionCard
            title="Сводка по периоду"
            description="Объём чатов, потребность по модели и фактически отработанные чатнико-часы."
            actions={<span className="text-sm text-slate-500">{formatInt(overviewSummary.days)} дней в истории</span>}
          >
            {historyTrendData.length ? (
              <div className="mt-5 h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={historyTrendData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                    <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                    <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                    <Tooltip formatter={(value, name) => [formatNumber(value, name === 'Чаты' ? 0 : 1), name]} />
                    {displayOptions.trendChats ? (
                      <Bar yAxisId="left" dataKey="chats" name="Чаты" fill="#bfdbfe" radius={[4, 4, 0, 0]} />
                    ) : null}
                    {displayOptions.trendNeedFte ? (
                      <Line yAxisId="right" type="monotone" dataKey="needFte" name="Потребность" stroke="#2563eb" strokeWidth={2} dot={false} />
                    ) : null}
                    {displayOptions.trendActualFte ? (
                      <Line yAxisId="right" type="monotone" dataKey="actualFte" name="Факт" stroke="#059669" strokeWidth={2} dot={false} />
                    ) : null}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <EmptyState
                title="Нет данных для сводки"
                text="За выбранный период чатов не нашлось. Выберите другой отрезок в календаре."
              />
            )}
          </SectionCard>

          <SectionCard
            title="Прирост и выдержка прогноза"
            description="Последние 6 дней до текущего формируют риск, а ближайшие 7 дней показывают уже построенный прирост."
            actions={(
              <div className="shrink-0 text-sm text-slate-500 sm:text-right">
                Источник
                <div className="font-medium text-slate-600">
                  {uplift?.source_dates?.length
                    ? `${formatDate(uplift.source_dates[uplift.source_dates.length - 1])} — ${formatDate(uplift.source_dates[0])}`
                    : '—'}
                </div>
              </div>
            )}
          >
            {uplift?.source_day_count ? (
              <>
                <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                      <span className={`h-1.5 w-1.5 rounded-full ${uplift.daily_summary.overload_day_count > 0 ? 'bg-rose-500' : 'bg-emerald-500'}`} aria-hidden="true" />
                      Выдержка
                    </div>
                    <div className={`mt-2 text-[26px] font-semibold leading-none tabular-nums ${uplift.daily_summary.overload_day_count > 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                      {formatInt(uplift.daily_summary.held_day_count)} / {formatInt(uplift.daily_summary.source_day_count)}
                    </div>
                    <div className="mt-1.5 text-xs text-slate-500">дней без почасового превышения</div>
                  </div>
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Факт − прогноз</div>
                    <div className={`mt-2 text-[26px] font-semibold leading-none tabular-nums ${uplift.daily_summary.total_delta_chats > 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                      {formatSignedNumber(uplift.daily_summary.total_delta_chats, 0)}
                    </div>
                    <div className="mt-1.5 text-xs text-slate-500">
                      {formatInt(uplift.daily_summary.total_actual_chats)} факт · {formatInt(uplift.daily_summary.total_forecast_chats)} прогноз
                    </div>
                  </div>
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Превышение</div>
                    <div className="mt-2 text-[26px] font-semibold leading-none tabular-nums text-rose-600">
                      +{formatInt(uplift.daily_summary.total_positive_delta_chats)}
                    </div>
                    <div className="mt-1.5 text-xs text-slate-500">только часы выше прогноза</div>
                  </div>
                  <div className="rounded-2xl bg-slate-50 p-4">
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Прирост периода</div>
                    <div className="mt-2 text-[26px] font-semibold leading-none tabular-nums text-emerald-600">
                      +{formatInt(totals.uplift_chats)}
                    </div>
                    <div className="mt-1.5 text-xs text-slate-500">
                      чатов · {formatDate(uplift.forecast_window_start)} — {formatDate(uplift.forecast_window_end)}
                    </div>
                  </div>
                  <div className="col-span-2 rounded-2xl bg-slate-50 p-4 md:col-span-1">
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Доп. чатнико-часы</div>
                    <div className="mt-2 text-[26px] font-semibold leading-none tabular-nums text-emerald-600">
                      +{formatNumber(totals.uplift_fte_hours, 1)}
                    </div>
                    <div className="mt-1.5 text-xs text-slate-500">на окно прироста</div>
                  </div>
                </div>

                <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
                  <div className="min-w-0 rounded-2xl bg-slate-50 p-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <ShieldAlert size={16} className="text-slate-400" />
                        Дни, которые сформировали риск
                      </div>
                      <span className="text-xs text-slate-500">ближайшие дни имеют больший вес</span>
                    </div>
                    <div className="h-64">
                      <ResponsiveContainer width="100%" height="100%">
                        <ComposedChart data={upliftDailyData} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
                          <XAxis dataKey="dateLabel" tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} />
                          <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} width={40} />
                          <Tooltip formatter={(value, name) => [formatNumber(value, 0), name]} />
                          <Bar dataKey="forecastChats" name="Прогноз" fill="#bfdbfe" radius={[4, 4, 0, 0]} />
                          <Bar dataKey="positiveDeltaChats" name="Сверх прогноза" fill="#fecdd3" radius={[4, 4, 0, 0]} />
                          <Line type="monotone" dataKey="actualChats" name="Факт" stroke="#0f172a" strokeWidth={2} dot={{ r: 3 }} />
                        </ComposedChart>
                      </ResponsiveContainer>
                    </div>
                  </div>

                  <div className="min-w-0">
                    <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
                      <Clock3 size={16} className="text-slate-400" />
                      Часы прироста
                    </div>
                    <div className="space-y-2.5">
                      {upliftTopHours.length ? upliftTopHours.map((row) => (
                        <div key={row.hour} className="rounded-2xl bg-slate-50 p-3.5">
                          <div className="flex items-center justify-between gap-3">
                            <div className="font-semibold text-slate-900">{hourLabel(row.hour)}</div>
                            <div className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-700">
                              +{formatNumber(row.weighted_delta_chats, 1)} чат.
                            </div>
                          </div>
                          <div className="mt-2.5 grid grid-cols-3 gap-2 text-xs text-slate-500">
                            <span>риск <b className="font-semibold text-emerald-700">{formatPercent(row.growth_ratio, 0)}</b></span>
                            <span>надёжн. <b className="font-semibold text-slate-900">{formatPercent(row.confidence, 0)}</b></span>
                            <span>дней <b className="font-semibold text-slate-900">{formatInt(row.positive_source_count)}/{formatInt(row.source_count)}</b></span>
                          </div>
                        </div>
                      )) : (
                        <div className="rounded-2xl bg-slate-50 p-6 text-center text-sm text-slate-500">
                          За последние дни нет часов, где факт был выше прогноза.
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <EmptyState
                title="Наплыв ещё не считался"
                text="Прирост берётся из сохранённых прошлых прогнозов. Нажмите «Пересчитать» во вкладке «Прогнозы» — после этого дни начнут сравниваться с собственным планом."
                action={(
                  <button
                    type="button"
                    onClick={() => setActiveTab('next_week')}
                    className="inline-flex h-10 items-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700"
                  >
                    <TrendingUp size={16} />
                    Перейти к прогнозам
                  </button>
                )}
              />
            )}
          </SectionCard>

          <div className="grid gap-5 xl:grid-cols-2">
            <SectionCard title="Профиль по дням недели" description="Средний объём и пиковый час по базовым неделям.">
              {weekdayProfile.length ? (
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
                      {weekdayProfile.map((row) => (
                        <tr key={row.weekday} className="border-b border-slate-100">
                          <td className="px-3 py-2 font-medium text-slate-800">{row.short}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{formatInt(row.avg_chats)}</td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {row.peak_hour === null ? '—' : hourLabel(row.peak_hour)}
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
            </SectionCard>

            <SectionCard title="Каналы" description="Откуда приходят обращения за базовые недели.">
              {channels.length ? (
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
                      {channels.map((channel) => (
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
            </SectionCard>
          </div>
        </div>
      ) : null}

      {activeTab === 'next_week' ? (
        <SectionCard
          title="Прогноз чатнико-часов по выбранному периоду"
          description="Для каждого дня берётся среднее того же дня недели по базовым неделям. Ёмкость выведена из цели по сервису."
          actions={(
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => shiftForecastWeek(-1)}
                className="inline-flex h-10 items-center rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
              >
                ← Неделя назад
              </button>
              <button
                type="button"
                onClick={() => shiftForecastWeek(1)}
                className="inline-flex h-10 items-center rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
              >
                Неделя вперёд →
              </button>
              <button
                type="button"
                onClick={recalculate}
                disabled={isRecalculating}
                aria-busy={isRecalculating}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <RefreshCw size={16} className={isRecalculating ? 'animate-spin motion-reduce:animate-none' : ''} aria-hidden="true" />
                {isRecalculating ? 'Пересчитываем…' : 'Пересчитать'}
              </button>
            </div>
          )}
        >
          {(displayOptions.forecastKpiFteHours || displayOptions.forecastKpiOperators) ? (
            <div className="mt-4 grid gap-3 grid-cols-1 xl:grid-cols-4 [&>*]:min-w-0">
              {displayOptions.forecastKpiFteHours ? (
                <div className="xl:col-span-2">
                  <StatCard
                    icon={TrendingUp}
                    label="Чатнико-часы периода"
                    value={formatNumber(totals.forecast_fte_hours, 1)}
                    hint={`${formatInt(totals.period_days)} дн. · ${formatInt(totals.forecast_chats)} чатов`}
                    tone="blue"
                    emphasis="primary"
                    accent
                  />
                </div>
              ) : null}
              {displayOptions.forecastKpiOperators ? (
                <OperatorSummaryCard
                  requiredFte={requiredFte}
                  requiredWithUplift={requiredWithUplift}
                  baseFte={Number(totals.operators || 0)}
                  availableFte={availableFte}
                  currentFte={Number(totals.current_operator_fte || 0)}
                  gap={availableFte - requiredFte}
                  availableCount={availableCount}
                  totalCount={availableTotalCount}
                  partialCount={Number(availability?.periodPartialOperatorCount || 0)}
                  unavailableCount={Number(availability?.periodUnavailableOperatorCount || 0)}
                  excludedCount={Number(chatAvailability?.excludedCount || 0)}
                  excludedFte={Number(chatAvailability?.excludedFte || 0)}
                  onOpen={() => setIsOperatorDetailsOpen(true)}
                />
              ) : null}
            </div>
          ) : null}

          {(displayOptions.forecastKpiCapacity || displayOptions.forecastKpiTarget
            || displayOptions.forecastKpiShrinkage || displayOptions.forecastKpiUplift) ? (
          <div className="mt-3 grid gap-2 grid-cols-2 md:grid-cols-3 xl:grid-cols-4 [&>*]:min-w-0">
            {displayOptions.forecastKpiCapacity ? (
              <StatCard
                icon={Gauge}
                label="Ёмкость"
                value={`${formatNumber(capacityPerHour, 1)} чат/ч`}
                hint={capacityExplain.source === 'manual'
                  ? 'Задана вручную'
                  : capacityExplain.source === 'first_reply'
                    ? 'Связала цель первого ответа'
                    : 'Выведена из «ответа внутри чата»'}
                tone="amber"
                emphasis="compact"
              />
            ) : null}
            {displayOptions.forecastKpiTarget ? (
              <StatCard
                icon={Target}
                label="Цель ответа"
                value={describeTarget(settings.target_reply_seconds)}
                hint={`Первый ответ ${describeTarget(targetFirstSeconds)}`}
                tone="blue"
                emphasis="compact"
              />
            ) : null}
            {displayOptions.forecastKpiShrinkage ? (
              <StatCard
                icon={ShieldAlert}
                label="Усушка"
                value={formatPercent(settings.shrinkage_coeff, 0)}
                hint="Отпуска, больничные, обучение"
                tone="slate"
                emphasis="compact"
              />
            ) : null}
            {displayOptions.forecastKpiUplift ? (
              <StatCard
                icon={TrendingUp}
                label="Возможный прирост"
                value={`+${formatInt(totals.uplift_chats)} чат.`}
                hint={`+${formatNumber(totals.uplift_fte_hours, 1)} чатнико-часов · ${formatInt(uplift?.source_day_count)}/6 дн.`}
                tone="emerald"
                emphasis="compact"
              />
            ) : null}
          </div>
          ) : null}

          <div className="mt-5 grid gap-5 xl:grid-cols-[300px_minmax(0,1fr)]">
            <aside className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <CalendarPicker
                mode="range"
                label="Период прогноза"
                startValue={forecastStart}
                endValue={forecastEnd}
                onRangeChange={applyForecastPeriod}
                loadedDates={coveredDays}
                hint={forecast?.base_week_starts?.length
                  ? `База: ${forecast.base_week_starts.map((item) => formatDateShort(item)).join(', ')}`
                  : undefined}
              />
              {skippedBaseWeeks.length ? (
                <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  <div className="flex items-center gap-1 font-semibold">
                    <AlertTriangle size={13} />
                    Неполные недели пропущены
                  </div>
                  <div className="mt-1 text-slate-600">
                    {skippedBaseWeeks.map((item) => formatDate(item.week_start)).join(', ')}
                  </div>
                </div>
              ) : null}

              <div className="mb-3 mt-5 flex items-center justify-between text-sm font-semibold text-slate-900">
                <span>Выберите день</span>
                <span className="text-[11px] font-medium text-slate-500 tabular-nums">{formatInt(forecastDays.length)} дн.</span>
              </div>
              <div className="space-y-2">
                {(() => {
                  const tomorrowValue = addDaysIso(todayValue, 1);
                  const maxDailyChats = Math.max(1, ...forecastDays.map((day) => Number(day.forecast_chats || 0)));
                  return forecastDays.map((day) => {
                    const isActive = selectedForecastDay?.forecast_date === day.forecast_date;
                    const isToday = day.forecast_date === todayValue;
                    const isTomorrow = day.forecast_date === tomorrowValue;
                    const isPast = day.forecast_date < todayValue;
                    const chatShare = Math.min(100, (Number(day.forecast_chats || 0) / maxDailyChats) * 100);
                    const hasUplift = Number(day.incident_uplift_chats || 0) > 0.01;
                    return (
                      <button
                        key={day.forecast_date}
                        type="button"
                        aria-pressed={isActive}
                        onClick={() => setSelectedForecastDate(day.forecast_date)}
                        className={`group relative w-full overflow-hidden rounded-lg border p-3 pl-4 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 ${
                          isActive
                            ? 'border-blue-400 bg-blue-50/60 shadow-sm'
                            : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
                        }`}
                      >
                        <span className={`pointer-events-none absolute left-0 top-0 h-full w-1 ${day.used_source_count > 1 ? 'bg-emerald-500' : 'bg-amber-400'}`} aria-hidden="true" />
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="font-semibold text-slate-950">{day.short}</span>
                              {isToday ? (
                                <span className="rounded-md bg-blue-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-800">Сегодня</span>
                              ) : isTomorrow ? (
                                <span className="rounded-md bg-sky-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-800">Завтра</span>
                              ) : isPast ? (
                                <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-600">Прошёл</span>
                              ) : null}
                            </div>
                            <div className="text-xs text-slate-500 tabular-nums">{formatDate(day.forecast_date)}</div>
                          </div>
                          <div className="text-right">
                            <div className="text-base font-semibold tabular-nums text-slate-950">{formatNumber(day.forecast_fte_hours, 1)}</div>
                            <div className="text-[10px] uppercase tracking-wide text-slate-500">чатнико-часов</div>
                          </div>
                        </div>

                        <div className="mt-2.5 flex items-center gap-2 text-xs">
                          <span className="inline-flex min-w-0 items-center gap-1 text-slate-600">
                            <MessageSquare size={11} className="shrink-0 text-slate-400" aria-hidden="true" />
                            <b className="text-slate-900 tabular-nums">{formatInt(day.forecast_chats)}</b>
                            <span className="text-slate-400">чат.</span>
                          </span>
                          <span className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-semibold text-slate-700">
                            пик {formatNumber(day.peak_fte, 1)}
                          </span>
                        </div>

                        <div className="mt-2 h-1 overflow-hidden rounded-full bg-slate-100" role="presentation" aria-hidden="true">
                          <div className="h-full rounded-full bg-blue-500/70" style={{ width: `${chatShare}%` }} />
                        </div>

                        {hasUplift ? (
                          <div className="mt-2 flex items-center justify-between gap-2 rounded-md bg-emerald-50 px-2 py-1 text-[11px] text-emerald-800 ring-1 ring-inset ring-emerald-100">
                            <span className="inline-flex items-center gap-1">
                              <TrendingUp size={11} aria-hidden="true" />
                              Возможный прирост
                            </span>
                            <span className="tabular-nums">
                              +{formatInt(day.incident_uplift_chats)} чат. · +{formatNumber(day.incident_uplift_fte_hours, 1)} ч
                            </span>
                          </div>
                        ) : null}

                        {day.has_actual ? (
                          <div className="mt-2 flex items-center justify-between gap-2 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px]">
                            <span className="inline-flex items-center gap-1 text-slate-600">
                              <CheckCircle2 size={11} className="text-emerald-600" aria-hidden="true" />
                              Факт <b className="text-slate-900 tabular-nums">{formatNumber(day.actual_online_hours, 1)}</b>
                            </span>
                            <span className={`tabular-nums font-semibold ${Number(day.actual_delta_fte_hours || 0) < 0 ? 'text-rose-700' : 'text-emerald-700'}`} title="Факт − прогноз">
                              {formatSignedNumber(day.actual_delta_fte_hours, 1)}
                            </span>
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
                          Потребность по часам: {selectedForecastDay.short} · {formatDate(selectedForecastDay.forecast_date)}
                        </h3>
                        <p className="text-sm text-slate-500">
                          Час считается как чаты часа ÷ {formatNumber(capacityPerHour, 1)} чатов в час на человека.
                        </p>
                      </div>
                      <span className={`inline-flex w-fit items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold ${hasDayActual ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
                        {hasDayActual ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
                        {hasDayActual ? 'Факт дня есть' : 'День ещё не прошёл'}
                      </span>
                    </div>

                    <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                      <div className="rounded-lg bg-slate-50 px-3 py-2">
                        <div className="text-xs text-slate-500">Чаты</div>
                        <b className="tabular-nums">{formatInt(selectedForecastDay.forecast_chats)}</b>
                        {hasDayActual ? (
                          <div className="mt-1 text-[11px] font-semibold text-emerald-700">факт {formatInt(selectedForecastDay.actual_chats)}</div>
                        ) : null}
                      </div>
                      <div className="rounded-lg bg-slate-50 px-3 py-2">
                        <div className="text-xs text-slate-500">Чатнико-часы</div>
                        <b className="tabular-nums">{formatNumber(selectedForecastDay.forecast_fte_hours, 1)}</b>
                        {hasDayActual ? (
                          <div className="mt-1 text-[11px] font-semibold text-emerald-700">факт {formatNumber(selectedForecastDay.actual_online_hours, 1)}</div>
                        ) : null}
                      </div>
                      <div className="rounded-lg bg-slate-50 px-3 py-2">
                        <div className="text-xs text-slate-500">Пиковый час</div>
                        <b className="tabular-nums">{selectedPeakHours[0] ? hourLabel(selectedPeakHours[0].hour) : '—'}</b>
                      </div>
                      <div className="rounded-lg bg-slate-50 px-3 py-2">
                        <div className="text-xs text-slate-500">Первый ответ в цель</div>
                        <b className="tabular-nums">
                          {selectedForecastDay.has_actual ? formatPercent(selectedForecastDay.in_target_share) : '—'}
                        </b>
                      </div>
                    </div>

                    <div className="mt-5 h-72">
                      <ResponsiveContainer width="100%" height="100%">
                        <ComposedChart data={selectedHourlyData} margin={{ top: 10, right: 18, left: 0, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                          <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={2} />
                          <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                          <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                          <Tooltip formatter={(value, name) => [formatNumber(value, String(name).includes('часник') ? 2 : 0), name]} />
                          {displayOptions.forecastChartChats ? (
                            <Bar yAxisId="left" dataKey="chats" name="Чаты" stackId="chats" fill="#bfdbfe" radius={displayOptions.forecastChartUplift && upliftAvailable ? [0, 0, 0, 0] : [4, 4, 0, 0]} />
                          ) : null}
                          {displayOptions.forecastChartUplift && upliftAvailable ? (
                            <Bar yAxisId="left" dataKey="upliftChats" name="Прирост" stackId="chats" fill="#bbf7d0" radius={[4, 4, 0, 0]} />
                          ) : null}
                          {displayOptions.forecastChartActualChats && hasDayActual ? (
                            <Bar yAxisId="left" dataKey="actualChats" name="Факт чатов" fill="#34d399" radius={[4, 4, 0, 0]} />
                          ) : null}
                          {displayOptions.forecastChartFte ? (
                            <Line yAxisId="right" type="monotone" dataKey="fte" name="Нужно чатников" stroke="#2563eb" strokeWidth={2} dot={false} />
                          ) : null}
                          {displayOptions.forecastChartAdjustedFte && upliftAvailable ? (
                            <Line yAxisId="right" type="monotone" dataKey="adjustedFte" name="Нужно чатников с приростом" stroke="#059669" strokeWidth={2} strokeDasharray="4 3" dot={false} />
                          ) : null}
                        </ComposedChart>
                      </ResponsiveContainer>
                    </div>
                  </div>

                  <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
                    <div className="overflow-x-auto rounded-lg border border-slate-200">
                      <table className="w-full divide-y divide-slate-200 text-sm tabular-nums">
                        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                          <tr>
                            <th className="px-3 py-3 text-left">Час</th>
                            <th className="px-3 py-3 text-right">Чаты</th>
                            {displayOptions.forecastTableUplift && upliftAvailable ? (
                              <th className="px-3 py-3 text-right">Прирост</th>
                            ) : null}
                            {displayOptions.forecastTableActualChats && hasDayActual ? (
                              <th className="px-3 py-3 text-right">Факт чатов</th>
                            ) : null}
                            <th className="px-3 py-3 text-right">Нужно чатников</th>
                            {displayOptions.forecastTableAdjustedFte && upliftAvailable ? (
                              <th className="px-3 py-3 text-right">С приростом</th>
                            ) : null}
                            {displayOptions.forecastTableActualHours && hasDayActual ? (
                              <th className="px-3 py-3 text-right">Факт чатнико-часов</th>
                            ) : null}
                            {displayOptions.forecastTableFirstReply && hasDayActual ? (
                              <th className="px-3 py-3 text-right">Первый ответ</th>
                            ) : null}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 bg-white">
                          {selectedHourlyData.map((row) => (
                            <tr key={row.hourNumber} className="transition hover:bg-slate-50/60">
                              <td className="px-3 py-2 font-medium text-slate-900">{row.hour}</td>
                              <td className="px-3 py-2 text-right">{formatNumber(row.chats, 1)}</td>
                              {displayOptions.forecastTableUplift && upliftAvailable ? (
                                <td className="px-3 py-2 text-right font-medium text-emerald-700">+{formatNumber(row.upliftChats, 1)}</td>
                              ) : null}
                              {displayOptions.forecastTableActualChats && hasDayActual ? (
                                <td className="px-3 py-2 text-right font-medium text-emerald-700">
                                  {row.actualChats === null ? '—' : formatInt(row.actualChats)}
                                </td>
                              ) : null}
                              <td className="px-3 py-2 text-right font-semibold text-blue-700">{formatNumber(row.fte, 2)}</td>
                              {displayOptions.forecastTableAdjustedFte && upliftAvailable ? (
                                <td className="px-3 py-2 text-right font-semibold text-emerald-700">{formatNumber(row.adjustedFte, 2)}</td>
                              ) : null}
                              {displayOptions.forecastTableActualHours && hasDayActual ? (
                                <td className="px-3 py-2 text-right text-emerald-700">
                                  {row.actualHours === null ? '—' : formatNumber(row.actualHours, 2)}
                                </td>
                              ) : null}
                              {displayOptions.forecastTableFirstReply && hasDayActual ? (
                                <td className="px-3 py-2 text-right text-slate-700">{formatSeconds(row.firstReply)}</td>
                              ) : null}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    <div className="rounded-lg border border-slate-200 bg-white p-4">
                      <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <TrendingUp size={16} aria-hidden="true" />
                        Пиковые часы
                      </div>
                      <div className="mt-4 space-y-3">
                        {(() => {
                          const peakMax = Math.max(1e-6, ...selectedPeakHours.map((row) => Number(row.forecast_fte || 0)));
                          return selectedPeakHours.map((row) => {
                            const barWidth = Math.min(100, Math.max(0, (Number(row.forecast_fte || 0) / peakMax) * 100));
                            return (
                              <div key={row.hour} className="rounded-lg bg-slate-50 p-3">
                                <div className="flex items-center justify-between">
                                  <span className="font-semibold text-slate-900 tabular-nums">{hourLabel(row.hour)}</span>
                                  <span className="rounded-md bg-blue-100 px-2 py-1 text-xs font-semibold text-blue-800 tabular-nums">
                                    {formatNumber(row.forecast_fte, 2)}
                                  </span>
                                </div>
                                <div className="mt-2 text-xs text-slate-500 tabular-nums">Чаты: {formatNumber(row.forecast_chats, 1)}</div>
                                <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
                                  <div className="h-full rounded-full bg-blue-600" style={{ width: `${barWidth}%` }} />
                                </div>
                              </div>
                            );
                          });
                        })()}
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <EmptyState
                  title="Нет прогноза"
                  text="Для выбранного периода не нашлось базовых недель с чатами. Сдвиньте период или выберите другой отрезок."
                />
              )}
            </div>
          </div>
        </SectionCard>
      ) : null}

      {activeTab === 'chats' ? (
        <div className="space-y-5">
          <SectionCard
            title="Аналитика чатов"
            description="«В цель» здесь и везде на вкладке — про ПЕРВЫЙ ответ клиенту, единственную измеримую по нашей базе метрику сервиса."
            actions={(
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
                    loadedDates={coveredDays}
                  />
                </div>
              </div>
            )}
          >
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
                hint={`Цель ${describeTarget(analyticsTotals.target_first_reply_seconds || targetFirstSeconds)}`}
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
                value={formatSeconds(analyticsTotals.avg_first_reply_seconds)}
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
                    Прогноз берётся из сохранённой истории пересчётов. Пока «Пересчитать» не нажимали, столбец прогноза пуст — это не дефект, а незаполненная история.
                  </div>
                ) : null}
              </div>

              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <ShieldAlert size={16} />
                  Топ часов риска
                </div>
                <p className="mt-1 text-xs text-slate-500">Худшая доля НА ОБЪЁМЕ: провалить ночной час с тремя чатами дешевле, чем дневной с сотней.</p>
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
                        <span>ответ: <b className="text-slate-800">{formatSeconds(row.avg_first_reply_seconds)}</b></span>
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
          </SectionCard>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
            <SectionCard title="По дням" description="Объём, первый ответ и отработанные чатнико-часы.">
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
                        <td className="px-3 py-2 text-right">{formatSeconds(row.avg_first_reply_seconds)}</td>
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
            </SectionCard>

            <SectionCard title="Каналы периода" description="Доля обращений по источникам.">
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
            </SectionCard>
          </div>
        </div>
      ) : null}

      {activeTab === 'schedule_planner' ? (
        <ResourceSchedulePlanner
          apiRoot={apiRoot}
          apiPrefix={CHAT_API_PREFIX}
          buildHeaders={buildHeaders}
          selectedWeekStart={forecastStart}
          selectedPeriodEnd={forecastEnd || forecastStart}
          onWeekStartChange={(value) => setForecastStart(value)}
          onPeriodChange={applyForecastPeriod}
          notify={notify}
        />
      ) : null}

      {activeTab === 'settings' ? (
        <div className="space-y-5">
          <SectionCard
            title="Две цели по сервису"
            description="Слева — рычаг расчёта, справа — то, что мы реально меряем по базе. Путать их нельзя."
          >
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
                  onChange={(event) => setSettingsDraft((prev) => ({
                    ...(prev || {}),
                    target_reply_seconds: event.target.value === '' ? '' : Number(event.target.value),
                  }))}
                  className={`${inputClass} mt-3 border-amber-300 bg-white`}
                />
                <span className="mt-2 block text-xs font-medium text-amber-900">
                  Сейчас: {describeTarget(settingsDraft?.target_reply_seconds)}
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
                  onChange={(event) => setSettingsDraft((prev) => ({
                    ...(prev || {}),
                    target_first_reply_seconds: event.target.value === '' ? '' : Number(event.target.value),
                  }))}
                  className={`${inputClass} mt-3`}
                />
                <span className="mt-2 block text-xs font-medium text-slate-600">
                  Сейчас: {describeTarget(settingsDraft?.target_first_reply_seconds)}
                </span>
              </label>
            </div>

            <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <Gauge size={16} className="text-slate-500" />
                    Ёмкость: {formatNumber(capacityExplain.used ?? capacityPerHour, 2)} чатов в час на человека
                  </div>
                  <p className="mt-1 max-w-2xl text-xs leading-snug text-slate-600">
                    {capacityExplain.source === 'manual'
                      ? 'Задана вручную и перебивает вывод из цели.'
                      : capacityExplain.source === 'first_reply'
                        ? 'Связала цель первого ответа — она жёстче «ответа внутри чата».'
                        : 'Выведена из цели «ответ внутри чата» по замеренной кривой.'}
                    {' '}Из цели: {formatNumber(capacityExplain.derived, 2)}
                    {capacityExplain.derived_first_reply
                      ? `; по первому ответу: ${formatNumber(capacityExplain.derived_first_reply, 2)}`
                      : '; кривая первого ответа ещё не замерена'}.
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {capacityIsManual ? (
                    <>
                      <input
                        type="number"
                        step={0.5}
                        min={0.5}
                        max={60}
                        value={settingsDraft?.capacity_manual ?? ''}
                        onChange={(event) => setSettingsDraft((prev) => ({
                          ...(prev || {}),
                          capacity_manual: event.target.value === '' ? '' : Number(event.target.value),
                        }))}
                        className={`${inputClass} w-32`}
                      />
                      <button
                        type="button"
                        onClick={() => setSettingsDraft((prev) => ({ ...(prev || {}), capacity_manual: null }))}
                        className="inline-flex h-10 items-center rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100"
                      >
                        Вернуть вывод из цели
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setSettingsDraft((prev) => ({
                        ...(prev || {}),
                        capacity_manual: Number(capacityExplain.used ?? capacityPerHour),
                      }))}
                      className="inline-flex h-10 items-center rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100"
                    >
                      Задать вручную
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={fitFirstReplyCurve}
                    disabled={isFitting}
                    className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
                  >
                    <RefreshCw size={16} className={isFitting ? 'animate-spin' : ''} />
                    {isFitting ? 'Замеряем…' : 'Замерить кривую первого ответа'}
                  </button>
                </div>
              </div>

              {capacityExplain.first_reply_target_unreachable ? (
                <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
                  <div className="flex items-center gap-1.5 font-semibold">
                    <AlertTriangle size={14} />
                    Цель первого ответа не достигается наращиванием людей
                  </div>
                  <p className="mt-1 text-xs leading-snug">
                    Замер показал: даже в самой разгруженной полосе первый ответ не укладывается
                    в {describeTarget(targetFirstSeconds)}. Причина не в общей нехватке штата, а в
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
          </SectionCard>

          <SectionCard title="Вводные расчёта" description="Пересчёт людей из чатнико-часов.">
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Коэффициент усушки</span>
                <input
                  type="number"
                  step={0.05}
                  min={0.1}
                  max={1}
                  value={settingsDraft?.shrinkage_coeff ?? ''}
                  onChange={(event) => setSettingsDraft((prev) => ({
                    ...(prev || {}),
                    shrinkage_coeff: event.target.value === '' ? '' : Number(event.target.value),
                  }))}
                  className={`${inputClass} mt-1`}
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
                  onChange={(event) => setSettingsDraft((prev) => ({
                    ...(prev || {}),
                    weekly_hours_per_operator: event.target.value === '' ? '' : Number(event.target.value),
                  }))}
                  className={`${inputClass} mt-1`}
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
                  onChange={(event) => setSettingsDraft((prev) => ({
                    ...(prev || {}),
                    base_weeks: event.target.value === '' ? '' : Number(event.target.value),
                  }))}
                  className={`${inputClass} mt-1`}
                />
                <span className="mt-1 block text-[11px] leading-snug text-slate-500">Сколько последних ПОЛНЫХ недель усредняем по дням недели</span>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-slate-600">Округление потребности</span>
                <select
                  value={settingsDraft?.fte_rounding || 'half'}
                  onChange={(event) => setSettingsDraft((prev) => ({ ...(prev || {}), fte_rounding: event.target.value }))}
                  className={`${inputClass} mt-1`}
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
                onClick={saveSettings}
                disabled={isSaving || !settingsDraft}
                className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
              >
                <Save className="h-4 w-4" />
                {isSaving ? 'Сохраняю...' : 'Сохранить и пересчитать'}
              </button>
              <span className="text-xs text-slate-500">
                Ёмкость пересчитается сама: она следует за целью, а не вводится рядом с ней.
              </span>
            </div>
          </SectionCard>

          <SectionCard
            title="Параметры отображения"
            description="Отключайте лишние показатели для быстрых ежедневных сценариев."
            actions={(
              <button
                type="button"
                onClick={() => setDisplayOptions({ ...DEFAULT_DISPLAY_OPTIONS })}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
              >
                <RefreshCw size={16} />
                Сбросить
              </button>
            )}
          >
            <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
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
          </SectionCard>
        </div>
      ) : null}
    </div>
  );
};

export default ResourceChatFteView;
