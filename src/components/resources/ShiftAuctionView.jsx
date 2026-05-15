import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
  CalendarDays,
  CalendarClock,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Gavel,
  History,
  ListChecks,
  Minus,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  ShieldCheck,
  Users,
  Wifi,
  X
} from 'lucide-react';
import { isAdminLikeRole, normalizeRole } from '../../utils/roles';

const normalizeOperatorId = (value) => {
  const id = Number(value);
  return Number.isFinite(id) && id > 0 ? id : null;
};

const normalizeSchedulePlanId = (value) => {
  const id = Number(value);
  return Number.isFinite(id) && id > 0 ? id : null;
};

const isDismissedOperator = (value) => {
  const status = String(value || '').trim().toLowerCase();
  return status === 'fired' || status === 'dismissal' || status === 'dismissed';
};

const normalizeOperators = (operators = [], selectedOperators = []) => {
  const rows = new Map();
  [...(Array.isArray(operators) ? operators : []), ...(Array.isArray(selectedOperators) ? selectedOperators : [])]
    .forEach((operator) => {
      const id = normalizeOperatorId(operator?.id ?? operator?.operator_id);
      if (!id) return;
      const role = normalizeRole(operator?.role || 'operator');
      if (role && role !== 'operator') return;
      rows.set(id, {
        id,
        name: operator?.name || `Оператор #${id}`,
        direction: operator?.direction || operator?.direction_name || '',
        direction_id: operator?.direction_id ?? null,
        supervisor_name: operator?.supervisor_name || '',
        rate: Number(operator?.rate || 1),
        status: operator?.status || ''
      });
    });

  return Array.from(rows.values())
    .filter((operator) => !isDismissedOperator(operator.status))
    .sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'ru'));
};

const toDateTimeInputValue = (value) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
  const pad = (num) => String(num).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

const splitDateTimeInputValue = (value) => {
  const normalized = toDateTimeInputValue(value);
  if (!normalized) return { date: '', time: '' };
  const [date = '', time = ''] = normalized.split('T');
  return { date, time };
};

const addMinutesToDateTimeInputValue = (value, minutes) => {
  const normalized = toDateTimeInputValue(value);
  if (!normalized) return '';
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return '';
  date.setMinutes(date.getMinutes() + Number(minutes || 0));
  return toDateTimeInputValue(date);
};

const formatDateLabel = (value) => {
  if (!value) return 'Дата';
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('ru-RU', { weekday: 'short', day: '2-digit', month: 'short' });
};

const formatDateTimeLabel = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  });
};

const formatAuctionPeriodLabel = (period) => (
  period?.date_from && period?.date_to
    ? `${formatDateLabel(period.date_from)} — ${formatDateLabel(period.date_to)}`
    : 'Неделя не выбрана'
);

const AUCTION_DURATION_PRESETS = [
  { label: '30 мин', minutes: 30 },
  { label: '1 час', minutes: 60 },
  { label: '2 часа', minutes: 120 },
  { label: '4 часа', minutes: 240 }
];

const AUCTION_TIME_PRESETS = ['09:00', '12:00', '15:00', '18:00', '20:00'];
const AUCTION_WEEKDAY_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

const toDateInputValue = (value) => {
  const normalized = toDateTimeInputValue(value);
  return normalized ? normalized.slice(0, 10) : '';
};

const getTodayDateInputValue = () => toDateInputValue(new Date());

const getDateFromInputValue = (value) => {
  if (!value) return null;
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime()) ? null : date;
};

const addDaysToDateInputValue = (value, days) => {
  const date = getDateFromInputValue(value || getTodayDateInputValue());
  if (!date) return getTodayDateInputValue();
  date.setDate(date.getDate() + Number(days || 0));
  return toDateInputValue(date);
};

const shiftCalendarMonth = (value, months) => {
  const date = getDateFromInputValue(value || getTodayDateInputValue()) || new Date();
  date.setDate(1);
  date.setMonth(date.getMonth() + Number(months || 0));
  return toDateInputValue(date);
};

const getCalendarMonthValue = (value) => {
  const date = getDateFromInputValue(value || getTodayDateInputValue()) || new Date();
  date.setDate(1);
  return toDateInputValue(date);
};

const buildCalendarDays = (monthValue) => {
  const monthDate = getDateFromInputValue(monthValue || getTodayDateInputValue()) || new Date();
  monthDate.setDate(1);
  const mondayOffset = (monthDate.getDay() + 6) % 7;
  const cursor = new Date(monthDate);
  cursor.setDate(cursor.getDate() - mondayOffset);
  const today = getTodayDateInputValue();
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(cursor);
    date.setDate(cursor.getDate() + index);
    const value = toDateInputValue(date);
    return {
      value,
      day: date.getDate(),
      isCurrentMonth: date.getMonth() === monthDate.getMonth(),
      isToday: value === today
    };
  });
};

const formatCalendarMonthLabel = (value) => {
  const date = getDateFromInputValue(value);
  if (!date) return '';
  return date.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });
};

const getAuctionDateTimeWithFallback = (value) => {
  const parts = splitDateTimeInputValue(value);
  return {
    date: parts.date || getTodayDateInputValue(),
    time: parts.time || '09:00'
  };
};

const mergeAuctionDateTimeValue = (currentValue, patch) => {
  const current = getAuctionDateTimeWithFallback(currentValue);
  return `${patch.date ?? current.date}T${patch.time ?? current.time}`;
};

const getAuctionWindowMinutes = (startsAt, endsAt) => {
  if (!startsAt || !endsAt) return null;
  const start = new Date(startsAt).getTime();
  const end = new Date(endsAt).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  return Math.round((end - start) / 60000);
};

const formatCountdown = (targetValue, nowMs) => {
  if (!targetValue) return '';
  const target = new Date(targetValue).getTime();
  if (!Number.isFinite(target)) return '';
  const diff = Math.max(0, target - nowMs);
  const totalSeconds = Math.floor(diff / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (num) => String(num).padStart(2, '0');
  return days > 0
    ? `${days} д ${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
    : `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
};

const AUCTION_RATE_GROUPS = [
  { id: 'rate-1', title: 'Ставка 1' },
  { id: 'rate-0.75', title: 'Ставка 0.75' },
  { id: 'rate-0.5', title: 'Ставка 0.5' },
  { id: 'night-20-08', title: 'Ночные 20*08' }
];

const normalizeClockValue = (value) => {
  const raw = String(value || '').trim();
  const match = raw.match(/^(\d{1,2}):(\d{2})/);
  if (!match) return raw;
  return `${String(Number(match[1]) % 24).padStart(2, '0')}:${match[2]}`;
};

const clockToMinutes = (value) => {
  const normalized = normalizeClockValue(value);
  const match = normalized.match(/^(\d{2}):(\d{2})$/);
  if (!match) return 0;
  return Number(match[1]) * 60 + Number(match[2]);
};

const clampNumber = (value, min, max) => Math.max(min, Math.min(max, value));

const mixChannels = (from, to, ratio) => {
  const amount = clampNumber(Number(ratio || 0), 0, 1);
  return from.map((channel, index) => Math.round(channel + (to[index] - channel) * amount));
};

const channelRgb = (channels) => `rgb(${channels[0]}, ${channels[1]}, ${channels[2]})`;

const getAuctionLotStartTone = (lot) => {
  const startMinutes = clockToMinutes(lot?.start_time);
  const visualStartMinutes = startMinutes < 7 * 60 ? startMinutes + 24 * 60 : startMinutes;
  const ratio = clampNumber((visualStartMinutes - (7 * 60)) / (17 * 60), 0, 1);
  const bg = mixChannels([219, 234, 254], [29, 78, 216], ratio);
  const border = mixChannels([147, 197, 253], [30, 64, 175], ratio);
  return {
    backgroundColor: channelRgb(bg),
    borderColor: channelRgb(border),
    color: ratio > 0.38 ? '#ffffff' : '#1e3a8a'
  };
};

const formatRate = (value) => {
  const rate = Number(value);
  if (!Number.isFinite(rate)) return '0';
  return rate.toFixed(2).replace(/\.?0+$/, '');
};

const hoursFormatter = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 1,
  minimumFractionDigits: 0
});

const formatAuctionHours = (minutes) => hoursFormatter.format(Math.max(0, Number(minutes || 0)) / 60);

const AUCTION_STATUS_PERIOD_LABELS = {
  bs: 'Б/С',
  unpaid_leave: 'Б/С',
  sick_leave: 'Больничный',
  annual_leave: 'Отпуск',
  dismissal: 'Увольнение'
};

const getAuctionBlockedDateLabel = (period) => {
  const code = String(period?.status_code || '').trim().toLowerCase();
  return period?.label || AUCTION_STATUS_PERIOD_LABELS[code] || 'Период';
};

const formatShortDateLabel = (value) => {
  if (!value) return '';
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return String(value).slice(5);
  const pad = (num) => String(num).padStart(2, '0');
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}`;
};

const isNightAuctionLot = (lot) => (
  normalizeClockValue(lot?.start_time) === '20:00'
  && normalizeClockValue(lot?.end_time) === '08:00'
);

const getAuctionRateGroupId = (lot) => {
  if (isNightAuctionLot(lot)) return 'night-20-08';
  const rate = Number(lot?.rate_min);
  if (!Number.isFinite(rate) || rate <= 0.5) return 'rate-0.5';
  if (rate <= 0.75) return 'rate-0.75';
  return 'rate-1';
};

const formatAuctionShiftLabel = (lot) => {
  if (isNightAuctionLot(lot)) return '20*08';
  const start = normalizeClockValue(lot?.start_time);
  const end = normalizeClockValue(lot?.end_time);
  return `${start}-${end}`;
};

const formatCompactClockValue = (value) => {
  const normalized = normalizeClockValue(value);
  if (!normalized.includes(':')) return normalized;
  const [hourRaw, minuteRaw] = normalized.split(':');
  const hour = String(Number(hourRaw || 0));
  return minuteRaw === '00' ? hour : `${hour}:${minuteRaw}`;
};

const formatAuctionBreakMinute = (value) => {
  const minutes = Number(value || 0);
  if (!Number.isFinite(minutes)) return '';
  const normalized = ((Math.round(minutes) % 1440) + 1440) % 1440;
  return `${String(Math.floor(normalized / 60)).padStart(2, '0')}:${String(normalized % 60).padStart(2, '0')}`;
};

const formatAuctionBreaksLabel = (lot) => {
  const breaks = Array.isArray(lot?.breaks) ? lot.breaks : [];
  const labels = breaks
    .map((item) => {
      const start = formatAuctionBreakMinute(item?.start);
      const end = formatAuctionBreakMinute(item?.end);
      return start && end ? `${start}-${end}` : '';
    })
    .filter(Boolean);
  return labels.length ? labels.join(', ') : '';
};

const getAuctionLotDurationMinutes = (lot) => {
  const startMinutes = clockToMinutes(lot?.start_time);
  const endClockMinutes = clockToMinutes(lot?.end_time);
  const endMinutes = endClockMinutes <= startMinutes ? endClockMinutes + 1440 : endClockMinutes;
  return Math.max(0, endMinutes - startMinutes);
};

const getAuctionLotBreakMinutes = (lot) => {
  const duration = getAuctionLotDurationMinutes(lot);
  const breaks = Array.isArray(lot?.breaks) ? lot.breaks : [];
  const total = breaks.reduce((sum, item) => {
    const start = Number(item?.start || 0);
    let end = Number(item?.end || 0);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return sum;
    if (end <= start) end += 1440;
    return sum + Math.max(0, end - start);
  }, 0);
  return clampNumber(total, 0, duration);
};

const getAuctionLotNetMinutes = (lot) => Math.max(0, getAuctionLotDurationMinutes(lot) - getAuctionLotBreakMinutes(lot));

const getAuctionNormWorkdayCount = (periodDayCount, blockedDayCount = 0) => {
  const totalDays = Math.max(0, Number(periodDayCount || 0));
  const blockedDays = clampNumber(Number(blockedDayCount || 0), 0, totalDays);
  const availableDays = Math.max(0, totalDays - blockedDays);
  if (!availableDays) return 0;
  const dayOffQuota = Math.min(2, totalDays);
  const manualDayOffQuota = Math.max(0, dayOffQuota - blockedDays);
  return Math.max(1, availableDays - manualDayOffQuota);
};

const formatCompactAuctionShiftLabel = (lot) => {
  if (isNightAuctionLot(lot)) return '20*08';
  return `${formatCompactClockValue(lot?.start_time)}-${formatCompactClockValue(lot?.end_time)}`;
};

const AuctionLotCell = ({
  lot,
  canClaim,
  canManage,
  claimingLotId,
  onClaimLot,
  userId,
  claimBlockReason
}) => {
  if (!lot) return null;

  const isLotClaimed = lot.status === 'claimed';
  const lotClaimedByCurrentUser = Number(lot.claimed_by) === Number(userId);
  const minRate = Number(lot.rate_min || 0);
  const isClaiming = Number(claimingLotId) === Number(lot.id);
  const label = formatAuctionShiftLabel(lot);
  const compactLabel = formatCompactAuctionShiftLabel(lot);
  const breaksLabel = formatAuctionBreaksLabel(lot);
  const netMinutes = getAuctionLotNetMinutes(lot);
  const breakMinutes = getAuctionLotBreakMinutes(lot);
  const title = `${label}${minRate ? ` · ставка ${formatRate(minRate)}` : ''} · в норму ${formatAuctionHours(netMinutes)} ч${breakMinutes ? ` · перерыв ${formatAuctionHours(breakMinutes)} ч` : ''}${breaksLabel ? ` (${breaksLabel})` : ''}${claimBlockReason ? ` · ${claimBlockReason}` : ''}${lot.claimed_by_name ? ` · ${lot.claimed_by_name}` : ''}`;
  const startToneStyle = getAuctionLotStartTone(lot);

  if (lot.status === 'available' && !canManage) {
    const blocked = Boolean(claimBlockReason);
    return (
      <button
        type="button"
        onClick={() => onClaimLot(lot.id)}
        disabled={!canClaim || isClaiming || blocked}
        title={title}
        style={blocked ? undefined : startToneStyle}
        className={`flex h-6 w-full min-w-0 items-center justify-center overflow-hidden rounded border px-1 text-[10px] font-semibold tabular-nums transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1 disabled:cursor-not-allowed sm:h-8 sm:px-2 sm:text-xs ${
          blocked
            ? 'border-slate-200 bg-slate-50 text-slate-400'
            : 'hover:brightness-95'
        }`}
      >
        <span className="truncate sm:hidden">{isClaiming ? '...' : compactLabel}</span>
        <span className="hidden truncate sm:inline">{isClaiming ? '...' : label}</span>
      </button>
    );
  }

  const tone = isLotClaimed
    ? (lotClaimedByCurrentUser ? 'border-emerald-600 bg-emerald-600 text-white' : 'border-slate-200 bg-slate-100 text-slate-400')
    : 'text-white hover:brightness-95';

  return (
    <div title={title} style={isLotClaimed ? undefined : startToneStyle} className={`flex h-6 w-full min-w-0 items-center justify-center overflow-hidden rounded border px-1 text-[10px] font-semibold tabular-nums sm:h-8 sm:px-2 sm:text-xs ${tone}`}>
      <span className="truncate sm:hidden">{compactLabel}</span>
      <span className="hidden truncate sm:inline">{label}</span>
    </div>
  );
};

const AuctionDateTimePicker = ({
  label,
  value,
  onChange,
  invalid = false
}) => {
  const rawParts = splitDateTimeInputValue(value);
  const parts = getAuctionDateTimeWithFallback(value);
  const [visibleMonth, setVisibleMonth] = useState(() => getCalendarMonthValue(parts.date));

  useEffect(() => {
    setVisibleMonth(getCalendarMonthValue(parts.date));
  }, [parts.date]);

  const calendarDays = useMemo(() => buildCalendarDays(visibleMonth), [visibleMonth]);
  const selectedDate = rawParts.date || '';
  const selectedTime = rawParts.time || parts.time;
  const quickDates = [
    { label: 'Сегодня', value: getTodayDateInputValue() },
    { label: 'Завтра', value: addDaysToDateInputValue(getTodayDateInputValue(), 1) },
    { label: '+2 дня', value: addDaysToDateInputValue(getTodayDateInputValue(), 2) }
  ];

  return (
    <div className={`min-w-0 rounded-lg border bg-white p-3 sm:p-4 ${invalid ? 'border-rose-300' : 'border-slate-200'}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <span className="block text-sm font-semibold text-slate-900">{label}</span>
          <span className="mt-0.5 block text-xs text-slate-500">
            {value ? formatDateTimeLabel(`${parts.date}T${selectedTime}`) : 'Не задано'}
          </span>
        </div>
        <Clock3 size={16} className="mt-0.5 shrink-0 text-blue-700" />
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {quickDates.map((item) => {
          const active = item.value === selectedDate;
          return (
            <button
              key={item.label}
              type="button"
              onClick={() => onChange(mergeAuctionDateTimeValue(value, { date: item.value }))}
              className={`rounded-md px-2.5 py-1 text-xs font-semibold transition ${
                active
                  ? 'bg-blue-700 text-white'
                  : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
              }`}
            >
              {item.label}
            </button>
          );
        })}
      </div>

      <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-2.5">
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => setVisibleMonth((current) => shiftCalendarMonth(current, -1))}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition hover:bg-white hover:text-slate-950"
            title="Предыдущий месяц"
          >
            <ChevronLeft size={16} />
          </button>
          <span className="text-sm font-semibold capitalize text-slate-800">{formatCalendarMonthLabel(visibleMonth)}</span>
          <button
            type="button"
            onClick={() => setVisibleMonth((current) => shiftCalendarMonth(current, 1))}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition hover:bg-white hover:text-slate-950"
            title="Следующий месяц"
          >
            <ChevronRight size={16} />
          </button>
        </div>
        <div className="mt-2 grid grid-cols-7 gap-1">
          {AUCTION_WEEKDAY_LABELS.map((day) => (
            <span key={day} className="py-1 text-center text-[11px] font-semibold text-slate-500">{day}</span>
          ))}
          {calendarDays.map((day) => {
            const active = day.value === selectedDate;
            return (
              <button
                key={day.value}
                type="button"
                onClick={() => onChange(mergeAuctionDateTimeValue(value, { date: day.value }))}
                className={`h-8 rounded-md text-xs font-semibold transition ${
                  active
                    ? 'bg-blue-700 text-white'
                    : day.isToday
                      ? 'bg-blue-50 text-blue-800 hover:bg-blue-100'
                      : day.isCurrentMonth
                        ? 'text-slate-800 hover:bg-white'
                        : 'text-slate-400 hover:bg-white'
                }`}
              >
                {day.day}
              </button>
            );
          })}
        </div>
      </div>

      <div className="mt-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Время</span>
          <div className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 p-1">
            <button
              type="button"
              onClick={() => onChange(addMinutesToDateTimeInputValue(`${parts.date}T${selectedTime}`, -15))}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition hover:bg-white hover:text-slate-950"
              title="Минус 15 минут"
            >
              <Minus size={14} />
            </button>
            <span className="min-w-[58px] text-center text-sm font-semibold tabular-nums text-slate-950">{selectedTime}</span>
            <button
              type="button"
              onClick={() => onChange(addMinutesToDateTimeInputValue(`${parts.date}T${selectedTime}`, 15))}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition hover:bg-white hover:text-slate-950"
              title="Плюс 15 минут"
            >
              <Plus size={14} />
            </button>
          </div>
        </div>
        <div className="mt-2 grid grid-cols-5 gap-1.5">
          {AUCTION_TIME_PRESETS.map((time) => {
            const active = time === selectedTime;
            return (
              <button
                key={time}
                type="button"
                onClick={() => onChange(mergeAuctionDateTimeValue(value, { time }))}
                className={`h-8 rounded-md text-xs font-semibold tabular-nums transition ${
                  active
                    ? 'bg-blue-700 text-white'
                    : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                }`}
              >
                {time}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
};

const getAuctionRuntimeStatus = (settings, nowMs) => {
  if (!settings?.enabled) return 'disabled';
  const startsAtMs = settings.starts_at ? new Date(settings.starts_at).getTime() : null;
  const endsAtMs = settings.ends_at ? new Date(settings.ends_at).getTime() : null;
  if (Number.isFinite(startsAtMs) && nowMs < startsAtMs) return 'scheduled';
  if (Number.isFinite(endsAtMs) && nowMs >= endsAtMs) return 'closed';
  return 'open';
};

const AuctionCountdownText = React.memo(({ target }) => {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!target) return undefined;
    const timer = window.setInterval(() => setTick((value) => (value + 1) % 1_000_000), 1000);
    return () => window.clearInterval(timer);
  }, [target]);
  if (!target) return null;
  return <>{formatCountdown(target, Date.now())}</>;
});
AuctionCountdownText.displayName = 'AuctionCountdownText';

const explainSteps = [
  {
    icon: CalendarClock,
    title: 'Админ утверждает смены',
    text: 'После генерации в расчете ресурсов админ выберет направление, период и время старта аукциона.'
  },
  {
    icon: Clock3,
    title: 'До старта будет таймер',
    text: 'При входе в раздел операторы увидят обратный отсчет до открытия выбора смен.'
  },
  {
    icon: Wifi,
    title: 'Выбор идет в реальном времени',
    text: 'Когда оператор заберет смену, она сразу станет недоступной у остальных без обновления страницы.'
  },
  {
    icon: ListChecks,
    title: 'Можно отметить 2 выходных',
    text: 'Перед выбором смен оператор сможет указать до двух дней периода как выходные, если квоту не заняли статусные периоды.'
  }
];

const ShiftAuctionView = ({ user, operators = [], apiBaseUrl, withAccessTokenHeader, showToast, onOpenResourceGeneration }) => {
  const role = normalizeRole(user?.role);
  const canManage = isAdminLikeRole(role);
  const apiRoot = String(apiBaseUrl || '').replace(/\/+$/, '');
  const showToastRef = useRef(showToast);
  const streamAbortRef = useRef(null);
  const snapshotRequestRef = useRef(false);
  const lastEventIdRef = useRef(0);
  const snapshotEtagRef = useRef('');
  const auctionLayoutRef = useRef(null);
  const auctionTableScrollRef = useRef(null);
  const auctionDateBarScrollRef = useRef(null);
  const auctionScrollSyncRef = useRef({ ignoredNode: null, ignoredLeft: 0 });

  const [settings, setSettings] = useState({
    enabled: false,
    launch_note: '',
    starts_at: null,
    ends_at: null,
    status: 'disabled',
    selected_operator_ids: [],
    selected_operators: [],
    selected_schedule_plan_id: null,
    selected_period: null,
    is_current_user_tester: false
  });
  const [lots, setLots] = useState([]);
  const [myDayOffs, setMyDayOffs] = useState([]);
  const [myBlockedDates, setMyBlockedDates] = useState([]);
  const [lastEventId, setLastEventId] = useState(0);
  const [draftEnabled, setDraftEnabled] = useState(false);
  const [draftNote, setDraftNote] = useState('');
  const [draftStartsAt, setDraftStartsAt] = useState('');
  const [draftEndsAt, setDraftEndsAt] = useState('');
  const [draftSchedulePlanId, setDraftSchedulePlanId] = useState('');
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isRestarting, setIsRestarting] = useState(false);
  const [claimingLotId, setClaimingLotId] = useState(null);
  const [dayOffLoadingDate, setDayOffLoadingDate] = useState('');
  const [connectionState, setConnectionState] = useState('idle');
  const [statusVersion, setStatusVersion] = useState(0);
  const [activeDayDate, setActiveDayDate] = useState('');
  const [isAdminDayDetailsOpen, setIsAdminDayDetailsOpen] = useState(false);
  const [auctionDayColumnPx, setAuctionDayColumnPx] = useState(64);
  const [availablePeriods, setAvailablePeriods] = useState([]);
  const [claimJournal, setClaimJournal] = useState([]);

  useEffect(() => {
    showToastRef.current = showToast;
  }, [showToast]);

  useEffect(() => {
    if (!settings.enabled) return undefined;
    const startsAtMs = settings.starts_at ? new Date(settings.starts_at).getTime() : null;
    const endsAtMs = settings.ends_at ? new Date(settings.ends_at).getTime() : null;
    const now = Date.now();
    let nextBoundary = null;
    if (Number.isFinite(startsAtMs) && now < startsAtMs) nextBoundary = startsAtMs;
    else if (Number.isFinite(endsAtMs) && now < endsAtMs) nextBoundary = endsAtMs;
    if (nextBoundary === null) return undefined;
    const delay = Math.max(500, nextBoundary - now + 50);
    const timer = window.setTimeout(() => setStatusVersion((value) => value + 1), delay);
    return () => window.clearTimeout(timer);
  }, [settings.enabled, settings.starts_at, settings.ends_at, statusVersion]);

  const notify = useCallback((message, type = 'success') => {
    if (typeof showToastRef.current === 'function') showToastRef.current(message, type);
  }, []);

  const buildHeaders = useCallback((extra = {}) => {
    const headers = { ...extra };
    if (user?.id) headers['X-User-Id'] = String(user.id);
    return typeof withAccessTokenHeader === 'function' ? withAccessTokenHeader(headers) : headers;
  }, [user?.id, withAccessTokenHeader]);

  const applySnapshot = useCallback((snapshot) => {
    const safe = snapshot || {};
    const ids = (safe.selected_operator_ids || []).map(normalizeOperatorId).filter(Boolean);
    const periods = Array.isArray(safe.available_periods) ? safe.available_periods : [];
    const selectedSchedulePlanId = normalizeSchedulePlanId(
      safe.selected_schedule_plan_id ?? safe.selected_period?.id
    );

    setSettings({
      enabled: Boolean(safe.enabled),
      launch_note: safe.launch_note || '',
      starts_at: safe.starts_at || null,
      ends_at: safe.ends_at || null,
      status: safe.status || 'disabled',
      selected_operator_ids: ids,
      selected_operators: Array.isArray(safe.selected_operators) ? safe.selected_operators : [],
      selected_schedule_plan_id: selectedSchedulePlanId,
      selected_period: safe.selected_period || null,
      is_current_user_tester: Boolean(safe.is_current_user_tester),
      updated_by_name: safe.updated_by_name || '',
      updated_at: safe.updated_at || null
    });
    setLots(Array.isArray(safe.lots) ? safe.lots : []);
    setMyDayOffs(Array.isArray(safe.my_day_offs) ? safe.my_day_offs.filter(Boolean) : []);
    setMyBlockedDates(Array.isArray(safe.my_blocked_dates) ? safe.my_blocked_dates.filter((item) => (typeof item === 'string' ? item : item?.date)) : []);
    const nextEventId = Number(safe.last_event_id || 0);
    lastEventIdRef.current = nextEventId;
    setLastEventId(nextEventId);
    setDraftEnabled(Boolean(safe.enabled));
    setDraftNote(safe.launch_note || '');
    setDraftStartsAt(toDateTimeInputValue(safe.starts_at));
    setDraftEndsAt(toDateTimeInputValue(safe.ends_at));
    setSelectedIds(new Set(ids));
    setAvailablePeriods(periods);
    setClaimJournal(Array.isArray(safe.claim_journal) ? safe.claim_journal : []);
    setDraftSchedulePlanId((current) => {
      const periodIds = new Set(periods.map((period) => normalizeSchedulePlanId(period?.id)).filter(Boolean));
      const currentId = normalizeSchedulePlanId(current);
      if (currentId && periodIds.has(currentId)) return String(currentId);
      if (selectedSchedulePlanId && periodIds.has(selectedSchedulePlanId)) return String(selectedSchedulePlanId);
      const firstAvailableId = normalizeSchedulePlanId(periods[0]?.id);
      return firstAvailableId ? String(firstAvailableId) : '';
    });
  }, []);

  const fetchSnapshot = useCallback(async ({ silent = false } = {}) => {
    if (!apiRoot || !user?.id) return;
    if (snapshotRequestRef.current) return;
    snapshotRequestRef.current = true;
    if (!silent) setIsLoading(true);
    try {
      const extraHeaders = snapshotEtagRef.current ? { 'If-None-Match': snapshotEtagRef.current } : {};
      const response = await axios.get(`${apiRoot}/api/shift_auction/test_snapshot`, {
        headers: buildHeaders(extraHeaders),
        validateStatus: (status) => (status >= 200 && status < 300) || status === 304
      });
      const etag = response?.headers?.etag || response?.headers?.ETag;
      if (etag) snapshotEtagRef.current = etag;
      if (response?.status !== 304) {
        applySnapshot(response?.data?.snapshot || {});
      }
    } catch (error) {
      if (!silent) notify(error?.response?.data?.error || 'Не удалось загрузить аукцион смен', 'error');
    } finally {
      snapshotRequestRef.current = false;
      if (!silent) setIsLoading(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, notify, user?.id]);

  const handleRealtimeEvent = useCallback((event) => {
    const eventType = String(event?.event_type || '');
    const payload = event?.payload || {};
    if (eventType === 'lot_claimed' && payload.lot?.id) {
      setLots((currentLots) => currentLots.map((lot) => (
        Number(lot.id) === Number(payload.lot.id)
          ? { ...lot, ...payload.lot }
          : lot
      )));
      if (canManage) fetchSnapshot({ silent: true });
      return;
    }

    if ((eventType === 'day_off_selected' || eventType === 'day_off_removed') && Number(payload.operator_id) === Number(user?.id)) {
      setMyDayOffs(Array.isArray(payload.my_day_offs) ? payload.my_day_offs.filter(Boolean) : []);
      return;
    }

    if (eventType === 'day_off_selected' || eventType === 'day_off_removed') {
      return;
    }

    fetchSnapshot({ silent: true });
  }, [canManage, fetchSnapshot, user?.id]);

  useEffect(() => {
    fetchSnapshot();
  }, [fetchSnapshot]);

  const canOpenStream = Boolean(apiRoot && user?.id && (canManage || settings.is_current_user_tester));

  useEffect(() => {
    if (!canOpenStream) return undefined;

    let cancelled = false;
    let currentAbortController = null;
    let reconnectTimer = null;
    let pollTimer = null;

    const stopPolling = () => {
      if (pollTimer) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const startPolling = () => {
      stopPolling();
      pollTimer = window.setInterval(() => {
        if (!cancelled) fetchSnapshot({ silent: true });
      }, 15000);
    };

    const readStream = async () => {
      if (cancelled) return;
      const abortController = new AbortController();
      currentAbortController = abortController;
      streamAbortRef.current?.abort?.();
      streamAbortRef.current = abortController;

      setConnectionState('connecting');
      try {
        const response = await fetch(`${apiRoot}/api/shift_auction/test_events?after=${encodeURIComponent(lastEventIdRef.current || 0)}`, {
          headers: buildHeaders({ Accept: 'text/event-stream' }),
          signal: abortController.signal,
          credentials: 'include'
        });
        if (!response.ok || !response.body) throw new Error('SSE connection failed');
        setConnectionState('online');
        stopPolling();
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (!cancelled) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const chunks = buffer.split('\n\n');
          buffer = chunks.pop() || '';
          for (const chunk of chunks) {
            const dataLine = chunk.split('\n').find((line) => line.startsWith('data: '));
            if (!dataLine) continue;
            try {
              const event = JSON.parse(dataLine.slice(6));
              const eventId = Number(event?.id || 0);
              lastEventIdRef.current = Math.max(lastEventIdRef.current, eventId);
              setLastEventId((current) => Math.max(current, eventId));
              handleRealtimeEvent(event);
            } catch (parseError) {
              console.warn('Failed to parse shift auction event', parseError);
            }
          }
        }
      } catch (error) {
        if (cancelled || error?.name === 'AbortError') return;
        setConnectionState('reconnecting');
        startPolling();
      }

      if (!cancelled) {
        setConnectionState('reconnecting');
        startPolling();
        reconnectTimer = window.setTimeout(() => {
          if (!cancelled) readStream();
        }, 2000);
      }
    };

    const handleVisibilityChange = () => {
      if (cancelled) return;
      if (document.visibilityState === 'visible') {
        fetchSnapshot({ silent: true });
        currentAbortController?.abort?.();
        if (reconnectTimer) {
          window.clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        readStream();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleVisibilityChange);

    readStream();
    return () => {
      cancelled = true;
      stopPolling();
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      currentAbortController?.abort?.();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleVisibilityChange);
    };
  }, [apiRoot, buildHeaders, canOpenStream, fetchSnapshot, handleRealtimeEvent, user?.id]);

  const operatorOptions = useMemo(
    () => normalizeOperators(operators, settings.selected_operators),
    [operators, settings.selected_operators]
  );

  const filteredOperators = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return operatorOptions;
    return operatorOptions.filter((operator) => {
      const haystack = [operator.name, operator.direction, operator.supervisor_name, operator.rate].join(' ').toLowerCase();
      return haystack.includes(normalizedQuery);
    });
  }, [operatorOptions, query]);

  const selectedOperators = useMemo(
    () => operatorOptions.filter((operator) => selectedIds.has(operator.id)),
    [operatorOptions, selectedIds]
  );

  const selectedDraftPeriod = useMemo(
    () => availablePeriods.find((period) => Number(period?.id) === Number(draftSchedulePlanId)) || null,
    [availablePeriods, draftSchedulePlanId]
  );
  const draftRangeInvalid = Boolean(
    draftStartsAt
    && draftEndsAt
    && new Date(draftEndsAt).getTime() <= new Date(draftStartsAt).getTime()
  );
  const draftAuctionWindowMinutes = useMemo(
    () => getAuctionWindowMinutes(draftStartsAt, draftEndsAt),
    [draftEndsAt, draftStartsAt]
  );

  const lotDates = useMemo(
    () => Array.from(new Set((lots || []).map((lot) => lot.shift_date).filter(Boolean))).sort(),
    [lots]
  );

  const myBlockedDateMap = useMemo(() => {
    const map = new Map();
    (myBlockedDates || []).forEach((item) => {
      const date = typeof item === 'string' ? item : item?.date;
      if (!date || map.has(date)) return;
      const period = typeof item === 'string' ? { date, label: 'Период' } : item;
      map.set(date, { ...period, label: getAuctionBlockedDateLabel(period) });
    });
    return map;
  }, [myBlockedDates]);

  const visibleLots = useMemo(() => {
    if (canManage) return lots;
    return lots.filter((lot) => !myDayOffs.includes(lot.shift_date) && !myBlockedDateMap.has(lot.shift_date));
  }, [canManage, lots, myBlockedDateMap, myDayOffs]);

  const auctionTableGroups = useMemo(() => {
    const groupMap = new Map(AUCTION_RATE_GROUPS.map((group) => [
      group.id,
      {
        ...group,
        lotsByDate: new Map(lotDates.map((date) => [date, []])),
        maxRows: 0,
        total: 0,
        claimed: 0,
        available: 0
      }
    ]));

    visibleLots.forEach((lot) => {
      const groupId = getAuctionRateGroupId(lot);
      const group = groupMap.get(groupId) || groupMap.get('rate-0.5');
      if (!group || !lot.shift_date) return;

      if (!group.lotsByDate.has(lot.shift_date)) group.lotsByDate.set(lot.shift_date, []);
      group.lotsByDate.get(lot.shift_date).push(lot);
      group.total += 1;
      if (lot.status === 'claimed') {
        group.claimed += 1;
      } else if (lot.status === 'available') {
        group.available += 1;
      }
    });

    return Array.from(groupMap.values())
      .map((group) => {
        const lotsByDate = new Map();
        let maxRows = 0;
        lotDates.forEach((date) => {
          const sortedLots = [...(group.lotsByDate.get(date) || [])].sort((a, b) => (
            clockToMinutes(a.start_time) - clockToMinutes(b.start_time)
            || clockToMinutes(a.end_time) - clockToMinutes(b.end_time)
            || Number(a.id || 0) - Number(b.id || 0)
          ));
          lotsByDate.set(date, sortedLots);
          maxRows = Math.max(maxRows, sortedLots.length);
        });
        return {
          ...group,
          lotsByDate,
          maxRows,
          rows: Array.from({ length: maxRows }, (_, index) => index)
        };
      })
      .filter((group) => group.rows.length > 0);
  }, [lotDates, visibleLots]);

  const myClaimedLots = useMemo(
    () => lots.filter((lot) => Number(lot.claimed_by) === Number(user?.id)),
    [lots, user?.id]
  );

  const dayOffQuota = useMemo(() => Math.min(2, Math.max(0, lotDates.length)), [lotDates.length]);
  const manualDayOffLimit = useMemo(
    () => Math.max(0, dayOffQuota - Math.min(dayOffQuota, myBlockedDateMap.size)),
    [dayOffQuota, myBlockedDateMap.size]
  );
  const selectedManualDayOffCount = useMemo(
    () => myDayOffs.filter((date) => !myBlockedDateMap.has(date)).length,
    [myBlockedDateMap, myDayOffs]
  );

  const dayNavigationItems = useMemo(() => {
    return lotDates.map((date) => {
      const dayLots = lots.filter((lot) => lot.shift_date === date);
      const claimedLots = dayLots.filter((lot) => lot.status === 'claimed');
      const myClaimed = dayLots.filter((lot) => Number(lot.claimed_by) === Number(user?.id));
      const isDayOff = myDayOffs.includes(date);
      const blockedPeriod = myBlockedDateMap.get(date);
      const availableCount = visibleLots.filter((lot) => lot.shift_date === date && lot.status === 'available').length;
      const lockedCount = dayLots.filter((lot) => lot.status === 'claimed' && Number(lot.claimed_by) !== Number(user?.id)).length;
      let state = 'empty';
      if (blockedPeriod) state = 'blocked';
      else if (isDayOff) state = 'off';
      else if (myClaimed.length > 0) state = 'shift';
      else if (availableCount > 0) state = 'available';
      else if (lockedCount > 0) state = 'locked';
      return {
        date,
        total: dayLots.length,
        claimed: claimedLots.length,
        myClaimed: myClaimed.length,
        myClaimedLot: myClaimed[0] || null,
        available: availableCount,
        locked: lockedCount,
        isDayOff,
        isBlocked: Boolean(blockedPeriod),
        blockedLabel: blockedPeriod ? getAuctionBlockedDateLabel(blockedPeriod) : '',
        blockedPeriod,
        state
      };
    });
  }, [lotDates, lots, myBlockedDateMap, myDayOffs, user?.id, visibleLots]);

  const adminActiveDayClaimGroups = useMemo(() => {
    if (!canManage || !activeDayDate) return [];

    const claimedLotsByGroup = new Map(AUCTION_RATE_GROUPS.map((group) => [group.id, []]));
    lots.forEach((lot) => {
      if (lot?.shift_date !== activeDayDate || lot.status !== 'claimed') return;
      const groupId = getAuctionRateGroupId(lot);
      const groupLots = claimedLotsByGroup.get(groupId) || [];
      groupLots.push(lot);
      claimedLotsByGroup.set(groupId, groupLots);
    });

    return AUCTION_RATE_GROUPS.map((group) => ({
      ...group,
      lots: [...(claimedLotsByGroup.get(group.id) || [])].sort((a, b) => (
        clockToMinutes(a.start_time) - clockToMinutes(b.start_time)
        || clockToMinutes(a.end_time) - clockToMinutes(b.end_time)
        || Number(a.id || 0) - Number(b.id || 0)
      ))
    }));
  }, [activeDayDate, canManage, lots]);

  const adminActiveDayClaimCount = useMemo(
    () => adminActiveDayClaimGroups.reduce((sum, group) => sum + group.lots.length, 0),
    [adminActiveDayClaimGroups]
  );

  useEffect(() => {
    if (!dayNavigationItems.length) {
      setActiveDayDate('');
      setIsAdminDayDetailsOpen(false);
      return;
    }
    setActiveDayDate((current) => (
      current && dayNavigationItems.some((item) => item.date === current)
        ? current
        : dayNavigationItems[0].date
    ));
  }, [dayNavigationItems]);

  const runtimeStatus = useMemo(
    () => getAuctionRuntimeStatus(settings, Date.now()),
    // statusVersion forces re-evaluation when a scheduled/open boundary is crossed
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [settings.enabled, settings.starts_at, settings.ends_at, statusVersion]
  );
  const hasStartCountdown = runtimeStatus === 'scheduled' && Boolean(settings.starts_at);
  const hasCloseCountdown = runtimeStatus === 'open' && Boolean(settings.ends_at);
  const auctionStatusLabel = runtimeStatus === 'scheduled'
    ? 'Откроется'
    : runtimeStatus === 'open'
      ? 'Аукцион открыт'
      : runtimeStatus === 'closed'
        ? 'Аукцион закрыт'
        : 'Аукцион выключен';
  const auctionStatusShortLabel = runtimeStatus === 'scheduled'
    ? 'Старт'
    : runtimeStatus === 'open'
      ? 'Открыт'
      : runtimeStatus === 'closed'
        ? 'Закрыт'
        : 'Выкл.';
  const auctionStatusDetailText = runtimeStatus === 'scheduled'
    ? 'скоро'
    : runtimeStatus === 'open'
      ? (hasCloseCountdown ? 'до закрытия' : 'идет выбор')
      : runtimeStatus === 'closed'
        ? 'выбор завершен'
        : `${settings.selected_operator_ids.length} тест.`;
  const auctionStatusDetail = hasStartCountdown
    ? <AuctionCountdownText target={settings.starts_at} />
    : hasCloseCountdown
      ? <>до закрытия <AuctionCountdownText target={settings.ends_at} /></>
      : auctionStatusDetailText;
  const auctionStatusShortDetail = hasCloseCountdown
    ? <AuctionCountdownText target={settings.ends_at} />
    : hasStartCountdown
      ? <AuctionCountdownText target={settings.starts_at} />
      : auctionStatusDetailText;
  const auctionStatusTone = runtimeStatus === 'open'
    ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
    : runtimeStatus === 'scheduled'
      ? 'border-blue-200 bg-blue-50 text-blue-800'
      : runtimeStatus === 'closed'
        ? 'border-slate-200 bg-slate-100 text-slate-600'
        : 'border-amber-200 bg-amber-50 text-amber-800';

  const isTester = Boolean(settings.enabled && settings.is_current_user_tester);
  const canUseAuction = isTester || canManage;
  const canChoose = isTester && (runtimeStatus === 'scheduled' || runtimeStatus === 'open');
  const canClaim = isTester && runtimeStatus === 'open';
  const userRate = useMemo(() => {
    const directRate = Number(user?.rate);
    if (Number.isFinite(directRate) && directRate > 0) return directRate;
    const snapshotOperator = (settings.selected_operators || []).find((operator) => Number(operator?.id) === Number(user?.id));
    const snapshotRate = Number(snapshotOperator?.rate);
    return Number.isFinite(snapshotRate) && snapshotRate > 0 ? snapshotRate : 1;
  }, [settings.selected_operators, user?.id, user?.rate]);

  const myAuctionWorkload = useMemo(() => {
    const workdayCount = getAuctionNormWorkdayCount(lotDates.length, myBlockedDateMap.size);
    const normMinutes = Math.round(workdayCount * 8 * 60 * userRate);
    const claimedNetMinutes = myClaimedLots.reduce((sum, lot) => sum + getAuctionLotNetMinutes(lot), 0);
    const claimedBreakMinutes = myClaimedLots.reduce((sum, lot) => sum + getAuctionLotBreakMinutes(lot), 0);
    const remainingMinutes = Math.max(0, normMinutes - claimedNetMinutes);
    const overMinutes = Math.max(0, claimedNetMinutes - normMinutes);
    const progress = normMinutes > 0 ? clampNumber((claimedNetMinutes / normMinutes) * 100, 0, 140) : 0;
    return {
      workdayCount,
      normMinutes,
      claimedNetMinutes,
      claimedBreakMinutes,
      remainingMinutes,
      overMinutes,
      progress,
      isComplete: normMinutes > 0 && claimedNetMinutes >= normMinutes - 1
    };
  }, [lotDates.length, myBlockedDateMap.size, myClaimedLots, userRate]);

  const claimBlockReasonByLotId = useMemo(() => {
    const reasons = new Map();
    if (canManage || !isTester) return reasons;
    lots.forEach((lot) => {
      if (!lot || lot.status !== 'available') return;
      const lotId = Number(lot.id);
      if (!Number.isFinite(lotId)) return;
      const blockedPeriod = myBlockedDateMap.get(lot.shift_date);
      if (blockedPeriod) {
        reasons.set(lotId, `День закрыт: ${getAuctionBlockedDateLabel(blockedPeriod)}`);
        return;
      }
      const netMinutes = getAuctionLotNetMinutes(lot);
      if (myAuctionWorkload.normMinutes > 0 && myAuctionWorkload.claimedNetMinutes >= myAuctionWorkload.normMinutes - 1) {
        reasons.set(lotId, 'Норма уже набрана');
        return;
      }
      if (
        myAuctionWorkload.normMinutes > 0
        && myAuctionWorkload.claimedNetMinutes + netMinutes > myAuctionWorkload.normMinutes + 1
      ) {
        reasons.set(lotId, `Превысит норму на ${formatAuctionHours(myAuctionWorkload.claimedNetMinutes + netMinutes - myAuctionWorkload.normMinutes)} ч`);
      }
    });
    return reasons;
  }, [canManage, isTester, lots, myAuctionWorkload, myBlockedDateMap]);

  useEffect(() => {
    if (!canUseAuction || !lotDates.length || typeof window === 'undefined') return undefined;

    const updateAuctionColumnWidth = () => {
      const layoutWidth = auctionLayoutRef.current?.getBoundingClientRect?.().width || window.innerWidth || 0;
      const minColumnWidth = window.matchMedia?.('(min-width: 640px)')?.matches ? 112 : 64;
      const nextColumnWidth = Math.max(minColumnWidth, layoutWidth / Math.max(1, lotDates.length));
      setAuctionDayColumnPx((current) => (
        Math.abs(current - nextColumnWidth) > 0.5 ? nextColumnWidth : current
      ));
    };

    updateAuctionColumnWidth();
    const resizeObserver = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(updateAuctionColumnWidth) : null;
    const layoutNode = auctionLayoutRef.current;
    if (layoutNode && resizeObserver) resizeObserver.observe(layoutNode);
    window.addEventListener('resize', updateAuctionColumnWidth);

    return () => {
      resizeObserver?.disconnect?.();
      window.removeEventListener('resize', updateAuctionColumnWidth);
    };
  }, [canUseAuction, lotDates.length]);

  const auctionDayColumnStyle = useMemo(() => {
    const width = `${auctionDayColumnPx}px`;
    return {
      width,
      minWidth: width,
      maxWidth: width
    };
  }, [auctionDayColumnPx]);

  const auctionTrackStyle = useMemo(() => ({
    width: `${auctionDayColumnPx * Math.max(1, lotDates.length)}px`
  }), [auctionDayColumnPx, lotDates.length]);

  const syncAuctionScroll = useCallback((source) => {
    const dateBar = auctionDateBarScrollRef.current;
    const table = auctionTableScrollRef.current;
    if (!dateBar || !table) return;

    const sourceNode = source === 'dates' ? dateBar : table;
    const targetNode = source === 'dates' ? table : dateBar;
    const syncState = auctionScrollSyncRef.current;
    if (syncState.ignoredNode === sourceNode && Math.abs(sourceNode.scrollLeft - syncState.ignoredLeft) <= 1) {
      syncState.ignoredNode = null;
      return;
    }

    const maxTargetLeft = Math.max(0, targetNode.scrollWidth - targetNode.clientWidth);
    const nextLeft = Math.min(sourceNode.scrollLeft, maxTargetLeft);
    if (Math.abs(targetNode.scrollLeft - nextLeft) > 0.5) {
      syncState.ignoredNode = targetNode;
      syncState.ignoredLeft = nextLeft;
      targetNode.scrollLeft = nextLeft;
    }
  }, []);

  const scrollToDay = useCallback((date) => {
    setActiveDayDate(date);
    if (canManage) setIsAdminDayDetailsOpen(true);
    const dateIndex = lotDates.indexOf(date);
    if (dateIndex < 0) return;

    const table = auctionTableScrollRef.current;
    const bar = auctionDateBarScrollRef.current;

    const dateCell = table?.querySelector('[data-auction-date-cell]');
    const barItem = bar?.querySelector('[data-auction-date-bar-cell]');
    const columnWidth = dateCell?.getBoundingClientRect?.().width
      || barItem?.getBoundingClientRect?.().width
      || dateCell?.offsetWidth
      || barItem?.offsetWidth
      || 64;

    const scrollNodeToDay = (node) => {
      if (!node) return;
      const maxLeft = Math.max(0, node.scrollWidth - node.clientWidth);
      const targetLeft = (dateIndex * columnWidth) - ((node.clientWidth - columnWidth) / 2);
      node.scrollTo({ left: Math.min(Math.max(0, targetLeft), maxLeft), behavior: 'smooth' });
    };

    scrollNodeToDay(table);
    scrollNodeToDay(bar);
  }, [canManage, lotDates]);

  const toggleOperator = useCallback((operatorId) => {
    const id = normalizeOperatorId(operatorId);
    if (!id) return;
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleSave = useCallback(async () => {
    if (!canManage || !apiRoot) return;
    if (draftRangeInvalid) {
      notify('Время завершения должно быть позже старта', 'error');
      return;
    }
    setIsSaving(true);
    try {
      await axios.put(
        `${apiRoot}/api/shift_auction/test_access`,
        {
          enabled: draftEnabled,
          launch_note: draftNote,
          starts_at: draftStartsAt || null,
          ends_at: draftEndsAt || null,
          operator_ids: Array.from(selectedIds)
        },
        { headers: buildHeaders() }
      );
      await fetchSnapshot({ silent: true });
      notify('Настройки тестового аукциона сохранены');
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось сохранить настройки аукциона смен', 'error');
    } finally {
      setIsSaving(false);
    }
  }, [apiRoot, buildHeaders, canManage, draftEnabled, draftEndsAt, draftNote, draftRangeInvalid, draftStartsAt, fetchSnapshot, notify, selectedIds]);

  const handleRestartAuction = useCallback(async () => {
    if (!canManage || !apiRoot) return;
    if (!selectedDraftPeriod?.id) {
      notify('Сначала выберите недельный план для аукциона', 'error');
      return;
    }
    if (!selectedDraftPeriod.can_restart) {
      notify('Прошедшую неделю нельзя запустить заново', 'error');
      return;
    }
    const confirmed = window.confirm(
      `Начать аукцион заново для недели ${formatAuctionPeriodLabel(selectedDraftPeriod)}? Все выбранные смены и выходные будут очищены.`
    );
    if (!confirmed) return;
    setIsRestarting(true);
    try {
      const response = await axios.post(
        `${apiRoot}/api/shift_auction/test_restart`,
        { schedule_plan_id: selectedDraftPeriod.id },
        { headers: buildHeaders({ 'Content-Type': 'application/json' }) }
      );
      applySnapshot(response?.data?.snapshot || {});
      notify(`Аукцион запущен заново: ${formatAuctionPeriodLabel(response?.data?.period || selectedDraftPeriod)}`);
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось начать аукцион заново', 'error');
    } finally {
      setIsRestarting(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, canManage, notify, selectedDraftPeriod]);

  const handleClaimLot = useCallback(async (lotId) => {
    if (!canClaim || !apiRoot) return;
    const blockReason = claimBlockReasonByLotId.get(Number(lotId));
    if (blockReason) {
      notify(blockReason, 'error');
      return;
    }
    setClaimingLotId(lotId);
    try {
      await axios.post(`${apiRoot}/api/shift_auction/test_lots/${lotId}/claim`, {}, { headers: buildHeaders() });
      await fetchSnapshot({ silent: true });
      notify('Смена закреплена за вами');
    } catch (error) {
      await fetchSnapshot({ silent: true });
      notify(error?.response?.data?.error || 'Не удалось забрать смену', 'error');
    } finally {
      setClaimingLotId(null);
    }
  }, [apiRoot, buildHeaders, canClaim, claimBlockReasonByLotId, fetchSnapshot, notify]);

  const toggleDayOff = useCallback(async (date) => {
    if (!canChoose || !apiRoot || !date) return;
    const blockedPeriod = myBlockedDateMap.get(date);
    if (blockedPeriod) {
      notify(`День закрыт: ${getAuctionBlockedDateLabel(blockedPeriod)}`, 'error');
      return;
    }
    const selected = myDayOffs.includes(date);
    if (!selected && selectedManualDayOffCount >= manualDayOffLimit) {
      notify('Лимит выходных уже занят статусными периодами или выбранными выходными', 'error');
      return;
    }
    setDayOffLoadingDate(date);
    try {
      const requestConfig = { headers: buildHeaders(), data: { date } };
      if (selected) {
        await axios.delete(`${apiRoot}/api/shift_auction/test_day_off`, requestConfig);
      } else {
        await axios.post(`${apiRoot}/api/shift_auction/test_day_off`, { date }, { headers: buildHeaders() });
      }
      await fetchSnapshot({ silent: true });
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось изменить выходной', 'error');
    } finally {
      setDayOffLoadingDate('');
    }
  }, [apiRoot, buildHeaders, canChoose, fetchSnapshot, manualDayOffLimit, myBlockedDateMap, myDayOffs, notify, selectedManualDayOffCount]);

  const renderStatusBar = () => {
    const showWorkload = !canManage && canUseAuction;
    const progressWidth = clampNumber(myAuctionWorkload.progress, 0, 100);
    const progressTone = myAuctionWorkload.overMinutes > 0 ? 'bg-rose-500' : myAuctionWorkload.isComplete ? 'bg-emerald-500' : 'bg-blue-600';
    const balanceLabel = myAuctionWorkload.overMinutes > 0
      ? `перебор ${formatAuctionHours(myAuctionWorkload.overMinutes)} ч`
      : `осталось ${formatAuctionHours(myAuctionWorkload.remainingMinutes)} ч`;
    const workloadTitle = showWorkload
      ? ` Набрано ${formatAuctionHours(myAuctionWorkload.claimedNetMinutes)} ч из ${formatAuctionHours(myAuctionWorkload.normMinutes)} ч. Перерывы: ${formatAuctionHours(myAuctionWorkload.claimedBreakMinutes)} ч.`
      : '';
    const title = `${settings.launch_note || `${auctionStatusLabel}: ${auctionStatusDetailText}`}${workloadTitle}`;
    return (
      <div
        title={title}
        className={`rounded-lg border text-xs shadow-lg backdrop-blur ${showWorkload ? 'inline-block w-fit min-w-[214px] max-w-[min(292px,calc(100vw-1rem))] px-2.5 py-1.5 sm:min-w-[230px] sm:max-w-[292px]' : 'inline-flex h-8 max-w-[calc(100vw-1rem)] items-center px-2.5 sm:h-9 sm:max-w-[calc(100vw-1.5rem)] sm:px-3'} ${auctionStatusTone}`}
      >
        <div className="flex min-w-0 items-center gap-1.5">
          {runtimeStatus === 'open' ? <ShieldCheck size={15} className="shrink-0" /> : <Clock3 size={15} className="shrink-0" />}
          <span className="shrink-0 font-semibold sm:hidden">{auctionStatusShortLabel}</span>
          <span className="hidden shrink-0 font-semibold sm:inline">{auctionStatusLabel}</span>
          <span className="min-w-0 truncate border-l border-current/20 pl-1.5 font-semibold tabular-nums sm:hidden">{auctionStatusShortDetail}</span>
          <span className="hidden min-w-0 truncate border-l border-current/20 pl-2 font-semibold tabular-nums sm:inline">{auctionStatusDetail}</span>
        </div>
        {showWorkload ? (
          <div className="mt-1.5 border-t border-current/20 pt-1.5">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="font-semibold text-slate-950">
                  {formatAuctionHours(myAuctionWorkload.claimedNetMinutes)} / {formatAuctionHours(myAuctionWorkload.normMinutes)} ч
                </div>
                <div className="truncate text-[10px] text-slate-600 sm:text-[11px]">
                  {balanceLabel} · перерывы {formatAuctionHours(myAuctionWorkload.claimedBreakMinutes)} ч
                </div>
              </div>
              <div className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold sm:text-[11px] ${myAuctionWorkload.overMinutes > 0 ? 'bg-rose-50 text-rose-700' : myAuctionWorkload.isComplete ? 'bg-emerald-50 text-emerald-700' : 'bg-white/70 text-slate-700'}`}>
                {myAuctionWorkload.isComplete ? 'Норма' : formatAuctionHours(myAuctionWorkload.remainingMinutes)}
              </div>
            </div>
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/60">
              <div className={`h-full rounded-full ${progressTone}`} style={{ width: `${progressWidth}%` }} />
            </div>
          </div>
        ) : null}
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="fixed right-2 top-2 z-40 flex max-w-[calc(100vw-1rem)] justify-end pointer-events-none sm:right-3 sm:top-3 sm:max-w-[calc(100vw-1.5rem)]">
        <div className="pointer-events-auto">
          {renderStatusBar()}
        </div>
      </div>

      <div className="border-b border-slate-200 bg-white px-3 pb-4 pt-24 sm:px-4 sm:pb-5 sm:pt-24 md:px-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-700 sm:h-11 sm:w-11">
              <Gavel size={20} className="sm:h-[22px] sm:w-[22px]" />
            </div>
            <div className="min-w-0">
              <h1 className="text-xl font-semibold text-slate-950 sm:text-2xl">Аукцион смен</h1>
              <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-600 sm:text-sm sm:leading-6">
                Тестовый realtime-раздел для проверки будущего выбора утвержденных смен по направлению.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 lg:pr-[280px]">
            {canManage && typeof onOpenResourceGeneration === 'function' ? (
              <button
                type="button"
                onClick={onOpenResourceGeneration}
                className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-lg bg-slate-900 px-3 text-xs font-semibold text-white shadow-sm transition hover:bg-slate-800 sm:h-10 sm:flex-none sm:px-4 sm:text-sm"
              >
                <CalendarClock size={16} />
                Генерация графиков
              </button>
            ) : null}
            <div className={`inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-lg border px-2.5 text-xs sm:h-10 sm:flex-none sm:px-3 sm:text-sm ${connectionState === 'online' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-slate-200 bg-white text-slate-600'}`}>
              <Wifi size={15} />
              <span className="truncate">{connectionState === 'online' ? 'Realtime online' : connectionState === 'connecting' ? 'Подключение...' : connectionState === 'reconnecting' ? 'Переподключение...' : 'Realtime idle'}</span>
            </div>
            <button
              type="button"
              onClick={() => fetchSnapshot()}
              disabled={isLoading}
              className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 shadow-sm transition hover:bg-slate-100 disabled:cursor-wait disabled:opacity-60 sm:h-10 sm:flex-none sm:px-4 sm:text-sm"
            >
              <RefreshCw size={16} className={isLoading ? 'animate-spin' : ''} />
              Обновить
            </button>
          </div>
        </div>
      </div>

      <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-3 py-4 sm:gap-6 sm:px-4 sm:py-6 md:px-6">
        {!canUseAuction && (
          <section className="grid gap-4 lg:grid-cols-4">
            {explainSteps.map((step) => {
              const Icon = step.icon;
              return (
                <div key={step.title} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                  <Icon size={20} className="text-blue-700" />
                  <h3 className="mt-3 text-sm font-semibold text-slate-950">{step.title}</h3>
                  <p className="mt-2 text-sm leading-5 text-slate-600">{step.text}</p>
                </div>
              );
            })}
          </section>
        )}

        {canUseAuction && (
          <section className={`grid min-w-0 gap-3 ${
            canManage && isAdminDayDetailsOpen
              ? 'xl:grid-cols-[minmax(0,1fr)_320px] xl:items-start xl:gap-5'
              : canManage
                ? ''
                : 'xl:grid-cols-[260px_minmax(0,1fr)] xl:gap-5'
          }`}>
            {!canManage ? (
              <aside className="grid min-w-0 gap-2 xl:block xl:space-y-3">
              <div className="min-w-0 max-w-full overflow-hidden rounded-lg border border-slate-200 bg-white p-3 shadow-sm sm:p-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <ListChecks size={17} className="text-blue-700" />
                  Мои выходные
                </div>
                <p className="mt-1 text-xs text-slate-500 sm:mt-2 sm:text-sm">Можно выбрать до 2 дней периода. Статусные периоды занимают эту квоту.</p>
                <div className="mt-2 flex min-w-0 max-w-full gap-1.5 overflow-x-auto overscroll-x-contain pb-1 xl:block xl:space-y-2 xl:overflow-visible xl:pb-0">
                  {lotDates.length ? lotDates.map((date) => {
                    const active = myDayOffs.includes(date);
                    const blockedPeriod = myBlockedDateMap.get(date);
                    const blockedLabel = blockedPeriod ? getAuctionBlockedDateLabel(blockedPeriod) : '';
                    const quotaReached = !active && selectedManualDayOffCount >= manualDayOffLimit;
                    return (
                      <button
                        key={date}
                        type="button"
                        onClick={() => toggleDayOff(date)}
                        disabled={!canChoose || dayOffLoadingDate === date || Boolean(blockedPeriod) || quotaReached}
                        title={blockedPeriod ? `${formatDateLabel(date)} · ${blockedLabel}` : formatDateLabel(date)}
                        className={`flex min-w-[64px] shrink-0 items-center justify-between gap-1 rounded-md border px-2 py-1.5 text-[11px] transition disabled:cursor-not-allowed disabled:opacity-80 sm:min-w-[112px] sm:py-2 sm:text-sm xl:w-full ${blockedPeriod ? 'border-rose-200 bg-rose-50 text-rose-700' : active ? 'border-blue-300 bg-blue-50 text-blue-800' : quotaReached ? 'border-slate-200 bg-slate-50 text-slate-400' : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'}`}
                      >
                        <span className="shrink-0 sm:hidden">{formatShortDateLabel(date)}</span>
                        <span className="hidden min-w-0 truncate sm:inline">{formatDateLabel(date)}</span>
                        {blockedPeriod ? (
                          <span className="min-w-0 truncate text-[10px] font-semibold sm:text-[11px]">{blockedLabel}</span>
                        ) : active ? <CheckCircle2 size={16} className="shrink-0" /> : null}
                      </button>
                    );
                  }) : (
                    <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 px-3 py-4 text-sm text-slate-500">
                      Тестовые смены еще не созданы.
                    </div>
                  )}
                </div>
              </div>

            </aside>
            ) : null}

            <main className="min-w-0 sm:rounded-lg sm:border sm:border-slate-200 sm:bg-white sm:shadow-sm">
              <div className="hidden border-b border-slate-200 sm:block sm:px-5 sm:py-4">
                <h2 className="text-base font-semibold text-slate-950 sm:text-lg">Доступные смены</h2>
                <p className="mt-1 text-xs text-slate-600 sm:text-sm">
                  {runtimeStatus === 'scheduled'
                    ? <>Аукцион откроется через <AuctionCountdownText target={settings.starts_at} />.</>
                    : runtimeStatus === 'open'
                      ? 'Нажмите “Забрать”, чтобы закрепить смену. У остальных участников она сразу станет недоступной.'
                      : 'Сейчас аукцион закрыт.'}
                </p>
              </div>
              <div className="min-w-0 sm:p-5">
                {auctionTableGroups.length && lotDates.length ? (
                  <div ref={auctionLayoutRef} className="relative min-w-0 max-w-full pb-16 sm:border-y sm:border-slate-200 sm:pb-0">
                    <div
                      ref={auctionTableScrollRef}
                      onScroll={() => syncAuctionScroll('table')}
                      className="max-w-full overflow-x-auto overscroll-x-contain"
                    >
                      <table className="table-fixed border-separate border-spacing-0 text-sm" style={auctionTrackStyle}>
                        <colgroup>
                          {lotDates.map((date) => (
                            <col key={`auction-col-${date}`} style={auctionDayColumnStyle} />
                          ))}
                        </colgroup>
                        <thead>
                          <tr>
                            {lotDates.map((date) => {
                              const dayMeta = dayNavigationItems.find((item) => item.date === date);
                              const isActiveDay = activeDayDate === date;
                              const headerTone = dayMeta?.isBlocked
                                ? 'bg-rose-50 text-rose-800'
                                : isActiveDay ? 'bg-blue-50' : 'bg-slate-50';
                              return (
                                <th
                                  key={date}
                                  data-auction-date-cell
                                  title={dayMeta?.isBlocked ? `${formatDateLabel(date)} · ${dayMeta.blockedLabel}` : formatDateLabel(date)}
                                  onClick={() => scrollToDay(date)}
                                  style={auctionDayColumnStyle}
                                  className={`cursor-pointer border-b border-r border-slate-200 px-1 py-1.5 text-center align-top last:border-r-0 sm:px-2 sm:py-2 ${headerTone}`}
                                >
                                  <div className="text-xs font-semibold tabular-nums text-slate-950">{formatShortDateLabel(date)}</div>
                                  {dayMeta?.isBlocked ? (
                                    <div className="mt-0.5 truncate text-[10px] font-semibold text-rose-700">{dayMeta.blockedLabel}</div>
                                  ) : null}
                                  {!dayMeta?.isBlocked && dayMeta?.isDayOff ? <div className="mt-0.5 text-[10px] font-semibold text-blue-700">вых.</div> : null}
                                </th>
                              );
                            })}
                          </tr>
                        </thead>
                        <tbody>
                          {auctionTableGroups.map((group) => (
                            <React.Fragment key={group.id}>
                              <tr>
                                <td colSpan={lotDates.length} className="border-b border-slate-200 bg-slate-100 px-1.5 py-0.5 sm:px-2 sm:py-1.5">
                                  <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-600 sm:text-xs">{group.title}</div>
                                </td>
                              </tr>
                              {group.rows.map((rowIndex) => (
                                <tr key={`${group.id}-${rowIndex}`} className="group">
                                  {lotDates.map((date) => {
                                    const lot = (group.lotsByDate.get(date) || [])[rowIndex];
                                    const isDayOff = myDayOffs.includes(date);
                                    const isBlocked = myBlockedDateMap.has(date);
                                    const cellTone = isBlocked
                                      ? 'bg-rose-50/50'
                                      : activeDayDate === date ? 'bg-blue-50/40' : 'bg-white';
                                    return (
                                      <td
                                        key={`${group.id}-${rowIndex}-${date}`}
                                        style={auctionDayColumnStyle}
                                        className={`border-b border-r border-slate-200 p-px align-top last:border-r-0 sm:p-1 ${cellTone} group-hover:bg-slate-50`}
                                      >
                                        {lot ? (
                                          <AuctionLotCell
                                            lot={lot}
                                            canClaim={canClaim}
                                            canManage={canManage}
                                            claimingLotId={claimingLotId}
                                            onClaimLot={handleClaimLot}
                                            userId={user?.id}
                                            claimBlockReason={claimBlockReasonByLotId.get(Number(lot.id)) || ''}
                                          />
                                        ) : (
                                          <div className={`h-6 rounded border border-dashed sm:h-8 ${isBlocked ? 'border-rose-100 bg-rose-50/70' : isDayOff ? 'border-blue-100 bg-blue-50/60' : 'border-transparent bg-slate-50/70'}`} />
                                        )}
                                      </td>
                                    );
                                  })}
                                </tr>
                              ))}
                            </React.Fragment>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {dayNavigationItems.length ? (
                      <div className="fixed bottom-2 left-3 right-3 z-30 mt-2 overflow-hidden rounded-xl border border-slate-200 bg-white/95 shadow-2xl backdrop-blur sm:sticky sm:bottom-3 sm:left-auto sm:right-auto">
                        <div
                          ref={auctionDateBarScrollRef}
                          onScroll={() => syncAuctionScroll('dates')}
                          className="overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
                        >
                          <div
                            className="grid items-stretch"
                            style={{
                              ...auctionTrackStyle,
                              gridTemplateColumns: `repeat(${dayNavigationItems.length}, ${auctionDayColumnPx}px)`
                            }}
                          >
                            {dayNavigationItems.map((item) => {
                              const active = activeDayDate === item.date;
                              const tone = canManage
                                ? (item.claimed >= item.total && item.total > 0 ? 'border-emerald-300 bg-emerald-50 text-emerald-800' : item.claimed > 0 ? 'border-blue-300 bg-blue-50 text-blue-800' : 'border-slate-200 bg-white text-slate-600')
                                : item.state === 'shift'
                                  ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                                  : item.state === 'blocked'
                                    ? 'border-rose-300 bg-rose-50 text-rose-800'
                                    : item.state === 'off'
                                      ? 'border-blue-300 bg-blue-50 text-blue-800'
                                      : 'border-slate-200 bg-white text-slate-600';
                              const statusText = canManage
                                ? `${item.claimed}/${item.total}`
                                : item.state === 'shift'
                                  ? 'Смена'
                                  : item.state === 'off'
                                    ? 'Вых.'
                                    : 'Пусто';
                              const finalStatusText = !canManage && item.state === 'blocked'
                                ? item.blockedLabel
                                : !canManage && item.state === 'locked' ? 'Занято' : statusText;
                              const myShiftLabel = !canManage && item.state === 'shift'
                                ? formatCompactAuctionShiftLabel(item.myClaimedLot)
                                : '';
                              const myShiftDuration = !canManage && item.state === 'shift'
                                ? `${formatAuctionHours(getAuctionLotNetMinutes(item.myClaimedLot))} ч`
                                : '';
                              const hoverTone = active ? 'hover:bg-blue-100' : 'hover:bg-slate-50';
                              return (
                                <button
                                  key={item.date}
                                  type="button"
                                  onClick={() => scrollToDay(item.date)}
                                  data-auction-date-bar-cell
                                  aria-current={active ? 'true' : undefined}
                                  className={`h-12 min-w-0 border-r border-slate-200 px-1 py-1 text-center transition-colors last:border-r-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-inset sm:h-[56px] sm:px-2 sm:py-1.5 ${tone} ${hoverTone} ${active ? 'bg-blue-100 text-blue-900' : ''}`}
                                  title={item.isBlocked ? `${formatDateLabel(item.date)} · ${item.blockedLabel}` : formatDateLabel(item.date)}
                                >
                                  <span className="block truncate text-[10px] font-semibold leading-4 sm:text-[11px]">{formatShortDateLabel(item.date)}</span>
                                  {!canManage && item.state === 'shift' ? (
                                    <>
                                      <span className="mt-0.5 block truncate text-[10px] font-bold tabular-nums sm:text-[11px]">{myShiftLabel}</span>
                                      <span className="block truncate text-[10px] font-semibold tabular-nums sm:text-[11px]">{myShiftDuration}</span>
                                    </>
                                  ) : (
                                    <span className="mt-0.5 block truncate text-[10px] font-bold tabular-nums sm:text-[11px]">{finalStatusText}</span>
                                  )}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
                    {lotDates.length
                      ? 'Для выбранных дней сейчас нет доступных смен.'
                      : canManage
                        ? 'Выберите недельный план и начните аукцион заново.'
                        : 'Пока нет доступных смен.'}
                  </div>
                )}
              </div>
            </main>
            {canManage && isAdminDayDetailsOpen && activeDayDate ? (
              <aside className="fixed bottom-[66px] left-3 right-3 z-30 max-h-[55vh] overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xl xl:sticky xl:top-24 xl:bottom-auto xl:left-auto xl:right-auto xl:max-h-[calc(100vh-7rem)] xl:shadow-sm">
                <div className="flex items-start justify-between gap-3 border-b border-slate-200 bg-slate-50 px-3 py-3 sm:px-4">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-slate-950">
                      {formatDateLabel(activeDayDate)}
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500">
                      Взятые смены: {adminActiveDayClaimCount}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsAdminDayDetailsOpen(false)}
                    className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-500 transition hover:bg-white hover:text-slate-800"
                    title="Закрыть"
                  >
                    <X size={16} />
                  </button>
                </div>
                <div className="max-h-[calc(55vh-61px)] overflow-y-auto px-3 py-3 sm:px-4 xl:max-h-[calc(100vh-11rem)]">
                  <div className="space-y-4">
                    {adminActiveDayClaimGroups.map((group) => (
                      <section key={`admin-day-claims-${group.id}`} className="min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs font-semibold uppercase tracking-wide text-slate-600">{group.title}</span>
                          <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-700">{group.lots.length}</span>
                        </div>
                        {group.lots.length ? (
                          <div className="mt-2 overflow-hidden rounded-md border border-slate-200 bg-white">
                            {group.lots.map((lot) => (
                              <div key={`admin-day-claim-${lot.id}`} className="grid grid-cols-[82px_minmax(0,1fr)] items-center gap-2 border-b border-slate-100 px-3 py-2 text-sm last:border-b-0">
                                <span className="font-semibold tabular-nums text-slate-950">{formatAuctionShiftLabel(lot)}</span>
                                <span className="truncate text-slate-700">{lot.claimed_by_name || `#${lot.claimed_by || ''}`}</span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="mt-2 rounded-md border border-dashed border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-500">
                            Нет взятых смен.
                          </div>
                        )}
                      </section>
                    ))}
                  </div>
                </div>
              </aside>
            ) : null}
          </section>
        )}

        {canManage && (
          <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-200 px-3 py-3 sm:px-5 sm:py-4">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <h2 className="text-base font-semibold text-slate-950 sm:text-lg">Запуск аукциона</h2>
                  <p className="mt-1 text-xs text-slate-600 sm:text-sm">
                    Выберите неделю, задайте окно аукциона и управляйте составом участников.
                  </p>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
                  <button
                    type="button"
                    onClick={handleRestartAuction}
                    disabled={isRestarting || !selectedDraftPeriod}
                    className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-wait disabled:opacity-60 sm:h-10 sm:px-4 sm:text-sm"
                  >
                    <RotateCcw size={16} />
                    {isRestarting ? 'Перезапуск...' : 'Начать заново'}
                  </button>
                  <button
                    type="button"
                    onClick={handleSave}
                    disabled={isSaving}
                    className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-blue-700 px-3 text-xs font-semibold text-white transition hover:bg-blue-800 disabled:cursor-wait disabled:bg-blue-400 sm:h-10 sm:px-4 sm:text-sm"
                  >
                    <Save size={16} />
                    {isSaving ? 'Сохранение...' : 'Сохранить'}
                  </button>
                </div>
              </div>
            </div>

            <div className="grid gap-4 p-3 sm:p-5 xl:grid-cols-[minmax(0,1fr)_360px]">
              <div className="space-y-4">
                <label className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-3 sm:px-4">
                  <span>
                    <span className="block text-sm font-semibold text-slate-900">Включить тестовый режим</span>
                    <span className="block text-xs text-slate-500 sm:text-sm">Выбранные операторы увидят realtime-полигон аукциона.</span>
                  </span>
                  <input
                    type="checkbox"
                    checked={draftEnabled}
                    onChange={(event) => setDraftEnabled(event.target.checked)}
                    className="h-5 w-5 rounded border-slate-300 text-blue-700 focus:ring-blue-600"
                  />
                </label>

                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 sm:p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <CalendarDays size={16} className="text-blue-700" />
                        Неделя аукциона
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        Активная: {formatAuctionPeriodLabel(settings.selected_period)}
                      </p>
                    </div>
                    {selectedDraftPeriod ? (
                      <span className="rounded-md bg-white px-2 py-1 text-xs font-semibold text-slate-700">
                        {Number(selectedDraftPeriod.shift_count || 0)} смен
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {availablePeriods.length ? availablePeriods.map((period) => {
                      const active = Number(draftSchedulePlanId) === Number(period.id);
                      const isCurrent = Number(settings.selected_schedule_plan_id) === Number(period.id);
                      return (
                        <button
                          key={period.id}
                          type="button"
                          onClick={() => setDraftSchedulePlanId(String(period.id))}
                          className={`rounded-lg border px-3 py-2 text-left transition ${
                            active
                              ? 'border-blue-500 bg-blue-50 text-blue-900'
                              : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                          }`}
                        >
                          <span className="block text-sm font-semibold">{formatAuctionPeriodLabel(period)}</span>
                          <span className="mt-0.5 block text-xs text-slate-500">
                            {Number(period.shift_count || 0)} смен{isCurrent ? ' · активная' : ''}
                          </span>
                        </button>
                      );
                    }) : (
                      <div className="rounded-lg border border-dashed border-slate-300 bg-white px-3 py-4 text-sm text-slate-500 sm:col-span-2">
                        Нет доступных недельных планов на текущую или будущие недели.
                      </div>
                    )}
                  </div>
                  <p className="mt-3 text-xs text-slate-500">
                    Перезапуск доступен только для полных недель, которые еще не закончились. При перезапуске очищаются все выбранные смены и выходные.
                  </p>
                </div>

                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 sm:p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-slate-900">Окно аукциона</div>
                      <div className="mt-0.5 text-xs text-slate-500">
                        {draftAuctionWindowMinutes
                          ? `Длительность: ${formatAuctionHours(draftAuctionWindowMinutes)} ч`
                          : 'Выберите старт и завершение'}
                      </div>
                    </div>
                    {draftRangeInvalid ? (
                      <span className="rounded-md bg-rose-50 px-2.5 py-1 text-xs font-semibold text-rose-700">
                        Завершение раньше старта
                      </span>
                    ) : null}
                  </div>

                  <div className="mt-3 grid gap-3 xl:grid-cols-2">
                    <AuctionDateTimePicker
                      label="Старт аукциона"
                      value={draftStartsAt}
                      onChange={setDraftStartsAt}
                    />
                    <AuctionDateTimePicker
                      label="Завершение"
                      value={draftEndsAt}
                      onChange={setDraftEndsAt}
                      invalid={draftRangeInvalid}
                    />
                  </div>

                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Завершить через</span>
                    {AUCTION_DURATION_PRESETS.map((preset) => (
                      <button
                        key={preset.label}
                        type="button"
                        onClick={() => setDraftEndsAt(addMinutesToDateTimeInputValue(draftStartsAt, preset.minutes))}
                        disabled={!draftStartsAt}
                        className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="mb-2 block text-sm font-semibold text-slate-800">Текст для тестовой группы</label>
                  <textarea
                    value={draftNote}
                    onChange={(event) => setDraftNote(event.target.value)}
                    rows={3}
                    maxLength={1000}
                    placeholder="Например: Тестовый запуск начнется после проверки генерации смен."
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                  />
                </div>

                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="relative w-full sm:max-w-md">
                    <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="Поиск по оператору, направлению или СВ"
                      className="h-10 w-full rounded-lg border border-slate-200 pl-9 pr-3 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    />
                  </div>
                  <div className="text-sm text-slate-500">
                    Выбрано: <span className="font-semibold text-slate-900">{selectedIds.size}</span>
                  </div>
                </div>

                <div className="max-h-[460px] overflow-auto rounded-lg border border-slate-200">
                  {filteredOperators.length ? (
                    filteredOperators.map((operator) => {
                      const active = selectedIds.has(operator.id);
                      return (
                        <button
                          key={operator.id}
                          type="button"
                          onClick={() => toggleOperator(operator.id)}
                          className={`flex w-full items-center gap-3 border-b border-slate-100 px-4 py-3 text-left transition last:border-b-0 ${active ? 'bg-blue-50' : 'bg-white hover:bg-slate-50'}`}
                        >
                          <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border ${active ? 'border-blue-700 bg-blue-700 text-white' : 'border-slate-300 bg-white'}`}>
                            {active ? <CheckCircle2 size={14} /> : null}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-semibold text-slate-900">{operator.name}</span>
                            <span className="mt-0.5 block truncate text-xs text-slate-500">
                              {operator.direction || 'Без направления'} · ставка {Number(operator.rate || 1).toFixed(2)}
                              {operator.supervisor_name ? ` · ${operator.supervisor_name}` : ''}
                            </span>
                          </span>
                        </button>
                      );
                    })
                  ) : (
                    <div className="px-4 py-8 text-center text-sm text-slate-500">Операторы не найдены.</div>
                  )}
                </div>
              </div>

              <aside className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <Users size={17} className="text-blue-700" />
                  Тестовая группа
                </div>
                <div className="mt-3 space-y-2">
                  {selectedOperators.length ? (
                    selectedOperators.map((operator) => (
                      <div key={operator.id} className="rounded-md border border-slate-200 bg-white px-3 py-2">
                        <div className="truncate text-sm font-semibold text-slate-900">{operator.name}</div>
                        <div className="mt-0.5 truncate text-xs text-slate-500">{operator.direction || 'Без направления'}</div>
                      </div>
                    ))
                  ) : (
                    <p className="rounded-md border border-dashed border-slate-300 bg-white px-3 py-4 text-sm text-slate-500">
                      Пока никто не выбран.
                    </p>
                  )}
                </div>
              </aside>
            </div>
          </section>
        )}

        {canManage && (
          <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-200 px-3 py-3 sm:px-5 sm:py-4">
              <div className="flex items-center gap-2">
                <History size={17} className="text-blue-700" />
                <h2 className="text-base font-semibold text-slate-950 sm:text-lg">Журнал аукционов</h2>
              </div>
              <p className="mt-1 text-xs text-slate-600 sm:text-sm">
                Кто и когда забрал смену. История сохраняется даже после перезапуска недели.
              </p>
            </div>
            <div className="overflow-x-auto">
              {claimJournal.length ? (
                <table className="min-w-full border-separate border-spacing-0 text-sm">
                  <thead>
                    <tr className="bg-slate-50 text-left text-xs font-semibold text-slate-600">
                      <th className="border-b border-slate-200 px-3 py-2 sm:px-5">Время</th>
                      <th className="border-b border-slate-200 px-3 py-2">Оператор</th>
                      <th className="border-b border-slate-200 px-3 py-2">Смена</th>
                      <th className="border-b border-slate-200 px-3 py-2 sm:px-5">Период</th>
                    </tr>
                  </thead>
                  <tbody>
                    {claimJournal.map((entry) => (
                      <tr key={entry.id} className="text-slate-700">
                        <td className="border-b border-slate-100 px-3 py-2 tabular-nums sm:px-5">{formatDateTimeLabel(entry.claimed_at)}</td>
                        <td className="border-b border-slate-100 px-3 py-2 font-medium text-slate-900">{entry.claimed_by_name || `#${entry.claimed_by || ''}`}</td>
                        <td className="border-b border-slate-100 px-3 py-2">
                          {entry.shift_date ? `${formatShortDateLabel(entry.shift_date)} · ${entry.start_time || ''}-${entry.end_time || ''}` : '—'}
                        </td>
                        <td className="border-b border-slate-100 px-3 py-2 sm:px-5">
                          {entry.period_start && entry.period_end
                            ? `${formatShortDateLabel(entry.period_start)} — ${formatShortDateLabel(entry.period_end)}`
                            : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="px-3 py-8 text-center text-sm text-slate-500 sm:px-5">
                  Пока никто не забирал смены.
                </div>
              )}
            </div>
          </section>
        )}
      </div>

    </div>
  );
};

export default ShiftAuctionView;
