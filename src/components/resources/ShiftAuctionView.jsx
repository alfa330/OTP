import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
  AlertTriangle,
  Bell,
  BookOpen,
  CalendarDays,
  CalendarClock,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Download,
  Flame,
  Gavel,
  Hand,
  History,
  Info,
  ListChecks,
  Minus,
  MousePointerClick,
  PauseCircle,
  PlayCircle,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Redo2,
  Square,
  Table,
  Undo2,
  Users,
  Wifi,
  X
} from 'lucide-react';
import { isAdminLikeRole, isSupervisorRole, normalizeRole } from '../../utils/roles';
import { IosModal, IosBadge, iosCard } from '../ui/ios';

// Стабильный ключ недавнего добора: (lot_id | plan_id | source_schedule_shift_id).
const getPostClaimKey = (claim) => {
  if (!claim) return '';
  const lotId = claim.lot_id != null ? String(claim.lot_id) : '';
  const planId = claim.plan_id != null ? String(claim.plan_id) : '';
  const shiftId = claim.source_schedule_shift_id != null ? String(claim.source_schedule_shift_id) : '';
  return `${lotId}|${planId}|${shiftId}`;
};

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
// Realtime events that carry a full `payload.lot` — these can be applied to a
// single lot in place instead of refetching the whole (heavy) snapshot.
const SHIFT_AUCTION_LOT_PATCH_EVENTS = new Set([
  'lot_claimed',
  'lot_released',
  'lot_post_auction_claimed',
]);
// How long monitor/self snapshot refreshes are coalesced for, so a burst of
// claims can't trigger one heavy snapshot rebuild per event and stampede the DB.
const SHIFT_AUCTION_SNAPSHOT_REFRESH_DEBOUNCE_MS = 2500;
// On a 401 the SSE stream's access token has expired. Refresh it and reconnect
// immediately (instead of looping with the stale token, which produced a storm
// of dropped connections). Capped so a genuinely dead session falls back to the
// normal backoff path instead of spinning.
const SHIFT_AUCTION_SSE_MAX_AUTH_REFRESH = 3;

const isSameRealtimeAuctionLot = (currentLot, incomingLot) => {
  if (!currentLot || !incomingLot) return false;
  if (currentLot.id != null && incomingLot.id != null && String(currentLot.id) === String(incomingLot.id)) {
    return true;
  }
  const currentShiftId = normalizeSchedulePlanId(currentLot.source_schedule_shift_id);
  const incomingShiftId = normalizeSchedulePlanId(incomingLot.source_schedule_shift_id);
  if (!currentShiftId || !incomingShiftId || currentShiftId !== incomingShiftId) return false;
  const currentPlanId = normalizeSchedulePlanId(currentLot.source_schedule_plan_id);
  const incomingPlanId = normalizeSchedulePlanId(incomingLot.source_schedule_plan_id);
  return !currentPlanId || !incomingPlanId || currentPlanId === incomingPlanId;
};

const mergeRealtimeAuctionLot = (currentLot, incomingLot, eventType, payload) => {
  const merged = { ...currentLot, ...incomingLot, _optimistic: false };
  if (eventType !== 'lot_post_auction_claimed') return merged;

  const startTime = incomingLot.claim_start_time || incomingLot.claimed_start_time;
  const endTime = incomingLot.claim_end_time || incomingLot.claimed_end_time;
  const claimedBy = payload?.operator_id ?? incomingLot.claimed_by;
  if (!startTime || !endTime || claimedBy == null) return merged;

  const segment = {
    claimed_by: Number(claimedBy),
    claimed_by_name: payload?.operator_name || incomingLot.claimed_by_name || '',
    start_time: String(startTime).slice(0, 5),
    end_time: String(endTime).slice(0, 5)
  };
  const existingSegments = Array.isArray(currentLot.claim_segments) ? currentLot.claim_segments : [];
  const alreadyPresent = existingSegments.some((item) => (
    Number(item?.claimed_by) === Number(segment.claimed_by)
    && String(item?.start_time || '').slice(0, 5) === segment.start_time
    && String(item?.end_time || '').slice(0, 5) === segment.end_time
  ));
  merged.claim_segments = alreadyPresent ? existingSegments : [...existingSegments, segment];
  return merged;
};

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

const getPeriodMonthValue = (period) => String(period?.date_from || period?.date_to || '').slice(0, 7);

const getCurrentMonthValue = () => getTodayDateInputValue().slice(0, 7);

const shiftMonthValue = (value, months) => {
  const [yearRaw, monthRaw] = String(value || getCurrentMonthValue()).split('-').map(Number);
  const date = new Date(yearRaw || new Date().getFullYear(), (monthRaw || (new Date().getMonth() + 1)) - 1, 1);
  date.setMonth(date.getMonth() + Number(months || 0));
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
};

const formatMonthValueLabel = (value) => {
  const [yearRaw, monthRaw] = String(value || '').split('-').map(Number);
  if (!Number.isFinite(yearRaw) || !Number.isFinite(monthRaw)) return '';
  return new Date(yearRaw, monthRaw - 1, 1).toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });
};

const periodIntersectsMonth = (period, monthValue) => {
  if (!period?.date_from || !period?.date_to || !monthValue) return false;
  const monthStart = `${monthValue}-01`;
  const [yearRaw, monthRaw] = monthValue.split('-').map(Number);
  if (!Number.isFinite(yearRaw) || !Number.isFinite(monthRaw)) return false;
  const monthEndDate = new Date(yearRaw, monthRaw, 0);
  const monthEnd = toDateInputValue(monthEndDate);
  return String(period.date_from) <= monthEnd && String(period.date_to) >= monthStart;
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

const compareDateInputValues = (left, right) => String(left || '').localeCompare(String(right || ''));

const getAuctionTimeDigits = (value) => String(value || '').replace(/\D/g, '').slice(0, 4);

const formatAuctionTimeDigits = (value) => {
  const digits = getAuctionTimeDigits(value);
  if (digits.length <= 1) return digits;
  if (digits.length === 2) return `${digits}:`;
  return `${digits.slice(0, 2)}:${digits.slice(2)}`;
};

const getAuctionTimeDigitIndex = (value, caretPosition) => (
  getAuctionTimeDigits(String(value || '').slice(0, caretPosition)).length
);

const getAuctionTimeCaretPosition = (digitsValue, digitIndex) => {
  const digits = getAuctionTimeDigits(digitsValue);
  const targetDigitIndex = Math.max(0, Math.min(Number(digitIndex) || 0, digits.length));
  if (!targetDigitIndex) return 0;

  const formatted = formatAuctionTimeDigits(digits);
  let seenDigits = 0;
  for (let index = 0; index < formatted.length; index += 1) {
    if (!/\d/.test(formatted[index])) continue;
    seenDigits += 1;
    if (seenDigits === targetDigitIndex) {
      return formatted[index + 1] === ':' ? index + 2 : index + 1;
    }
  }
  return formatted.length;
};

const normalizeAuctionTimeInput = (value) => {
  const raw = String(value || '').trim();
  let hoursValue = '';
  let minutesValue = '';

  const separatedMatch = raw.match(/^(\d{1,2}):(\d{0,2})$/);
  if (separatedMatch) {
    [, hoursValue, minutesValue] = separatedMatch;
    minutesValue ||= '0';
  } else if (/^\d{1,4}$/.test(raw)) {
    if (raw.length <= 2) {
      hoursValue = raw;
      minutesValue = '0';
    } else {
      hoursValue = raw.slice(0, -2);
      minutesValue = raw.slice(-2);
    }
  } else {
    return '';
  }

  const hours = Number(hoursValue);
  const minutes = Number(minutesValue);
  if (!Number.isInteger(hours) || !Number.isInteger(minutes) || hours < 0 || hours > 23 || minutes < 0 || minutes > 59) {
    return '';
  }
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
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
  { id: 'rate-1', title: 'Ставка 1', rate: 1, shiftMinutes: 540 },
  { id: 'rate-0.75', title: 'Ставка 0.75', rate: 0.75, shiftMinutes: 390 },
  { id: 'rate-0.5', title: 'Ставка 0.5', rate: 0.5, shiftMinutes: 240 },
  { id: 'night-20-08', title: 'Ночные 20*08', rate: 1, shiftMinutes: 720, night: true }
];

// Wrap minutes into a 24h "HH:MM" clock value (so 20:00 + 12h → 08:00).
const auctionMinutesToClock = (minutes) => {
  const total = ((Math.round(Number(minutes) || 0) % (24 * 60)) + 24 * 60) % (24 * 60);
  const hh = Math.floor(total / 60);
  const mm = total % 60;
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
};

// End time of a manually-added shift: the length is fixed by the rate group, so the
// supervisor only picks the start. Night group is the fixed 20:00→08:00 window.
const computeAuctionEndTime = (startValue, group) => {
  if (!group) return '';
  if (group.night) return '08:00';
  const duration = Number(group.shiftMinutes);
  if (!Number.isFinite(duration) || duration <= 0) return '';
  return auctionMinutesToClock(clockToMinutes(startValue) + duration);
};

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

const getAuctionLotPostAuctionTone = (lot) => {
  const startMinutes = clockToMinutes(lot?.start_time);
  const visualStartMinutes = startMinutes < 7 * 60 ? startMinutes + 24 * 60 : startMinutes;
  const ratio = clampNumber((visualStartMinutes - (7 * 60)) / (17 * 60), 0, 1);
  const bg = mixChannels([255, 237, 213], [194, 65, 12], ratio);
  const border = mixChannels([253, 186, 116], [154, 52, 18], ratio);
  return {
    backgroundColor: channelRgb(bg),
    borderColor: channelRgb(border),
    color: ratio > 0.38 ? '#ffffff' : '#7c2d12'
  };
};

const getLotStartDateTimeMs = (lot) => {
  if (!lot || !lot.shift_date || !lot.start_time) return null;
  const parts = String(lot.shift_date).split('-');
  if (parts.length !== 3) return null;
  const [y, m, d] = parts.map((part) => Number(part));
  const [hh, mm] = String(lot.start_time).split(':').map((part) => Number(part));
  if (!Number.isFinite(y) || !Number.isFinite(m) || !Number.isFinite(d) || !Number.isFinite(hh) || !Number.isFinite(mm)) return null;
  return new Date(y, m - 1, d, hh, mm, 0, 0).getTime();
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

const getAuctionLotClaimStartTime = (lot) => lot?.claim_start_time || lot?.post_claim_start_time || lot?.claimed_start_time || '';

const getAuctionLotClaimEndTime = (lot) => lot?.claim_end_time || lot?.post_claim_end_time || lot?.claimed_end_time || '';

const getAuctionLotEffectiveStartTime = (lot) => (
  Boolean(lot?.post_auction_claimed) && getAuctionLotClaimStartTime(lot)
    ? getAuctionLotClaimStartTime(lot)
    : lot?.start_time
);

const getAuctionLotEffectiveEndTime = (lot) => (
  Boolean(lot?.post_auction_claimed) && getAuctionLotClaimEndTime(lot)
    ? getAuctionLotClaimEndTime(lot)
    : lot?.end_time
);

const formatAuctionLotEffectiveTimeRangeLabel = (lot) => (
  `${String(getAuctionLotEffectiveStartTime(lot) || '').slice(0, 5)}–${String(getAuctionLotEffectiveEndTime(lot) || '').slice(0, 5)}`
);

const getClockRangeWithinSource = (startTime, endTime, sourceRange) => {
  const start = parseHHMMToMinutes(startTime);
  let end = parseHHMMToMinutes(endTime);
  if (start == null || end == null) return null;
  let adjustedStart = start;
  if (sourceRange && sourceRange[1] > 1440 && adjustedStart < sourceRange[0]) {
    adjustedStart += 1440;
  }
  if (end <= adjustedStart) end += 1440;
  return [adjustedStart, end];
};

const getAuctionLotEffectiveMinuteRange = (lot) => {
  const sourceRange = lotMinuteRange(lot);
  return getClockRangeWithinSource(
    getAuctionLotEffectiveStartTime(lot),
    getAuctionLotEffectiveEndTime(lot),
    sourceRange
  );
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
  const range = getAuctionLotEffectiveMinuteRange(lot);
  return range ? Math.max(0, range[1] - range[0]) : 0;
};

const getAuctionLotBreakMinutes = (lot) => {
  const duration = getAuctionLotDurationMinutes(lot);
  const breaks = Array.isArray(lot?.breaks) ? lot.breaks : [];
  const activeRange = getAuctionLotEffectiveMinuteRange(lot);
  if (!activeRange) return 0;
  const total = breaks.reduce((sum, item) => {
    const start = Number(item?.start || 0);
    let end = Number(item?.end || 0);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return sum;
    if (end <= start) end += 1440;
    return sum + Math.max(0, Math.min(end, activeRange[1]) - Math.max(start, activeRange[0]));
  }, 0);
  return clampNumber(total, 0, duration);
};

const getAuctionLotNetMinutes = (lot) => Math.max(0, getAuctionLotDurationMinutes(lot) - getAuctionLotBreakMinutes(lot));

const getAuctionLotActionKey = (lotOrId) => {
  if (lotOrId && typeof lotOrId === 'object') {
    const raw = lotOrId.id ?? lotOrId.source_schedule_shift_id ?? '';
    return raw === null || raw === undefined ? '' : String(raw);
  }
  return lotOrId === null || lotOrId === undefined ? '' : String(lotOrId);
};

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
  claimingLotIds,
  onClaimLot,
  userId,
  claimBlockReason,
  postAuctionActive = false,
  postAuctionNowMs = 0,
  postClaimingLotIds,
  postAuctionClaimOption,
  onRequestPostAuctionClaim,
  onShowDetail,
  isPartialRemainder = false
}) => {
  if (!lot) return null;

  const isLotClaimed = lot.status === 'claimed';
  const lotClaimedByCurrentUser = Number(lot.claimed_by) === Number(userId);
  // Manually-added shift (supervisor/admin "+"): violet tint + marker so it stands
  // out from auto-seeded lots, and the title shows who added it.
  const isAddedLot = Boolean(lot.added_by);
  const addedToneStyle = { backgroundColor: '#ede9fe', borderColor: '#c4b5fd', color: '#5b21b6' };
  const minRate = Number(lot.rate_min || 0);
  const lotActionKey = getAuctionLotActionKey(lot);
  const isClaiming = claimingLotIds instanceof Set && claimingLotIds.has(lotActionKey);
  const isPostClaiming = postClaimingLotIds instanceof Set && postClaimingLotIds.has(lotActionKey);
  const label = formatAuctionShiftLabel(lot);
  const compactLabel = formatCompactAuctionShiftLabel(lot);
  const breaksLabel = formatAuctionBreaksLabel(lot);
  const netMinutes = getAuctionLotNetMinutes(lot);
  const breakMinutes = getAuctionLotBreakMinutes(lot);
  const isPostClaimedLot = Boolean(lot.post_auction_claimed);
  // A shift was "taken in parts" if it has claim_segments and it isn't a single
  // whole-shift claim: i.e. it's still partially free (available) OR it was split
  // among ≥2 operators (claimed but in pieces). Such cells get a marker.
  const claimSegments = Array.isArray(lot.claim_segments) ? lot.claim_segments : [];
  const takenInParts = claimSegments.length > 0 && (lot.status !== 'claimed' || claimSegments.length > 1);
  const startToneStyle = getAuctionLotStartTone(lot);
  const postAuctionToneStyle = getAuctionLotPostAuctionTone(lot);
  const lotStartMs = getLotStartDateTimeMs(lot);
  const hasStarted = lotStartMs !== null && postAuctionNowMs > 0 && lotStartMs <= postAuctionNowMs;

  // A lot is a post-auction candidate when the phase is active, not yet claimed,
  // hasn't started, and the operator is not a manager.
  const isPostAuctionCandidate = (
    postAuctionActive
    && !canManage
    && (lot.status === 'available' || lot.status === 'cancelled')
    && !isPostClaimedLot
    && !hasStarted
  );
  // Actually takeable only when there is no blocking reason (e.g. time overlap).
  const postAuctionTakeable = isPostAuctionCandidate && !claimBlockReason && (!postAuctionClaimOption || postAuctionClaimOption.canClaim);
  const postAuctionSegment = postAuctionClaimOption?.recommendedSegment || null;
  const postAuctionCellLabel = postAuctionSegment && !postAuctionSegment.isFull
    ? `${postAuctionSegment.start_time}-${postAuctionSegment.end_time}`
    : label;
  const postAuctionCompactLabel = postAuctionSegment && !postAuctionSegment.isFull
    ? `${formatCompactClockValue(postAuctionSegment.start_time)}-${formatCompactClockValue(postAuctionSegment.end_time)}`
    : compactLabel;

  const title = `${label}${minRate ? ` · ставка ${formatRate(minRate)}`
    : ''} · в норму ${formatAuctionHours(netMinutes)} ч${breakMinutes ? ` · перерыв ${formatAuctionHours(breakMinutes)} ч` : ''}${breaksLabel ? ` (${breaksLabel})` : ''}${claimBlockReason ? ` · ${claimBlockReason}` : ''}${lot.claimed_by_name ? ` · ${lot.claimed_by_name}` : ''}${postAuctionTakeable ? ` · доступно после аукциона${postAuctionSegment && !postAuctionSegment.isFull ? `: ${postAuctionSegment.start_time}–${postAuctionSegment.end_time}` : ''}` : ''}${formatPostAuctionClaimTitleSuffix(lot)}${isAddedLot ? ` · добавил ${lot.added_by_name || '—'}` : ''}`;

  if (postAuctionTakeable) {
    return (
      <button
        type="button"
        onClick={() => onRequestPostAuctionClaim && onRequestPostAuctionClaim(lot)}
        disabled={isPostClaiming}
        title={title}
        style={postAuctionToneStyle}
        className="relative flex h-6 w-full min-w-0 items-center justify-center overflow-hidden rounded border px-1 text-[10px] font-semibold tabular-nums transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500 focus-visible:ring-offset-1 disabled:cursor-wait sm:h-8 sm:px-2 sm:text-xs hover:brightness-95"
      >
        <span className="truncate sm:hidden">{isPostClaiming ? '...' : postAuctionCompactLabel}</span>
        <span className="hidden truncate sm:inline">{isPostClaiming ? '...' : postAuctionCellLabel}</span>
        {postAuctionSegment && !postAuctionSegment.isFull ? (
          <span className="absolute inset-x-1 bottom-0.5 h-0.5 rounded-full bg-white/80" />
        ) : null}
        {takenInParts ? (
          <span className="pointer-events-none absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-white ring-1 ring-orange-600" title="Часть смены уже взята другим оператором" />
        ) : null}
      </button>
    );
  }

  // Post-auction candidate that is blocked (e.g. time overlap with existing shift) —
  // render as grey, same as a blocked regular-auction lot.
  if (isPostAuctionCandidate && claimBlockReason) {
    return (
      <div
        title={title}
        className="flex h-6 w-full min-w-0 items-center justify-center overflow-hidden rounded border px-1 text-[10px] font-semibold tabular-nums sm:h-8 sm:px-2 sm:text-xs border-slate-200 bg-slate-50 text-slate-400"
      >
        <span className="truncate sm:hidden">{compactLabel}</span>
        <span className="hidden truncate sm:inline">{label}</span>
      </div>
    );
  }

  if (lot.status === 'available' && !canManage) {
    const blocked = Boolean(claimBlockReason);
    return (
      <button
        type="button"
        onClick={() => onClaimLot(lot.id)}
        disabled={!canClaim || isClaiming || blocked}
        title={title}
        style={blocked ? undefined : (isAddedLot ? addedToneStyle : startToneStyle)}
        className={`relative flex h-6 w-full min-w-0 items-center justify-center overflow-hidden rounded border px-1 text-[10px] font-semibold tabular-nums transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1 disabled:cursor-not-allowed sm:h-8 sm:px-2 sm:text-xs ${
          blocked
            ? 'border-slate-200 bg-slate-50 text-slate-400'
            : 'hover:brightness-95'
        }`}
      >
        <span className="truncate sm:hidden">{isClaiming ? '...' : compactLabel}</span>
        <span className="hidden truncate sm:inline">{isClaiming ? '...' : label}</span>
        {isAddedLot && !blocked ? (
          <span className="pointer-events-none absolute left-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-violet-600 ring-1 ring-white" title="Добавленная смена" />
        ) : null}
      </button>
    );
  }

  let tone;
  if (isLotClaimed) {
    // In post-auction mode all claimed lots become grey — the auction is over
    tone = 'border-slate-200 bg-slate-100 text-slate-400';
  } else if (postAuctionActive && (lot.status === 'available' || lot.status === 'cancelled') && !hasStarted) {
    tone = 'text-orange-900 hover:brightness-95';
  } else if (isAddedLot) {
    // Colour comes from addedToneStyle (violet); don't force white text.
    tone = 'hover:brightness-95';
  } else {
    tone = 'text-white hover:brightness-95';
  }

  const isOpenPostStyle = !isLotClaimed && postAuctionActive && (lot.status === 'available' || lot.status === 'cancelled') && !hasStarted;
  const styleToUse = isLotClaimed
    ? undefined
    : (isOpenPostStyle ? postAuctionToneStyle : (isAddedLot ? addedToneStyle : startToneStyle));

  const detailClickable = canManage && typeof onShowDetail === 'function';
  // Single-lot model: a partially-taken shift stays one lot carrying claim_segments
  // (parts taken by others). An AVAILABLE such lot shows its FREE part.
  let freeRangeLabel = null;
  if (claimSegments.length && !isLotClaimed) {
    const src = lotMinuteRange(lot);
    if (src) {
      const busy = claimSegments
        .map((seg) => getClockRangeWithinSource(seg.start_time, seg.end_time, src))
        .filter(Boolean);
      const free = subtractBusyRanges(src, busy).available;
      if (free.length) {
        freeRangeLabel = free.map((s) => `${minutesToClockLabel(s.start)}-${minutesToClockLabel(s.end)}`).join(' ');
      }
    }
  }
  // Claimed (fully taken) → full shift range, grey. Available + partly taken → free part.
  // A marker is shown whenever the shift was taken IN PARTS (split / partially taken).
  const finalDisplayLabel = freeRangeLabel || label;
  const finalDisplayCompact = freeRangeLabel || compactLabel;
  const finalClassName = `relative flex h-6 w-full min-w-0 items-center justify-center overflow-hidden rounded border px-1 text-[10px] font-semibold tabular-nums sm:h-8 sm:px-2 sm:text-xs ${tone}${detailClickable ? ' cursor-pointer transition hover:brightness-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-1' : ''}`;
  const finalInner = (
    <>
      <span className="truncate sm:hidden">{finalDisplayCompact}</span>
      <span className="hidden truncate sm:inline">{finalDisplayLabel}</span>
      {takenInParts ? (
        <span
          className="pointer-events-none absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-white ring-1 ring-orange-600"
          title={isLotClaimed ? 'Смена разобрана по частям несколькими операторами' : 'Часть смены уже взята другим оператором'}
        />
      ) : null}
      {isAddedLot ? (
        <span
          className="pointer-events-none absolute left-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-violet-600 ring-1 ring-white"
          title={`Добавленная смена · ${lot.added_by_name || '—'}`}
        />
      ) : null}
    </>
  );

  if (detailClickable) {
    return (
      <button
        type="button"
        title={`${title} · нажмите, чтобы посмотреть кто какую часть взял`}
        style={styleToUse}
        className={finalClassName}
        onClick={() => onShowDetail(lot)}
      >
        {finalInner}
      </button>
    );
  }

  return (
    <div title={title} style={styleToUse} className={finalClassName}>
      {finalInner}
    </div>
  );
};

const AuctionRangeCalendar = ({
  startsAt,
  endsAt,
  onStartsAtChange,
  onEndsAtChange
}) => {
  const startParts = splitDateTimeInputValue(startsAt);
  const endParts = splitDateTimeInputValue(endsAt);
  const startDate = startParts.date || '';
  const endDate = endParts.date || '';
  const calendarAnchor = startDate || endDate || getTodayDateInputValue();
  const [visibleMonth, setVisibleMonth] = useState(() => getCalendarMonthValue(calendarAnchor));

  useEffect(() => {
    setVisibleMonth(getCalendarMonthValue(calendarAnchor));
  }, [calendarAnchor]);

  const calendarDays = useMemo(() => buildCalendarDays(visibleMonth), [visibleMonth]);
  const isAwaitingEnd = Boolean(startDate && !endDate);

  const handleDayClick = (dayValue) => {
    if (!startDate || endDate) {
      onStartsAtChange(mergeAuctionDateTimeValue(startsAt, { date: dayValue }));
      onEndsAtChange('');
      return;
    }
    if (compareDateInputValues(dayValue, startDate) < 0) {
      onStartsAtChange(mergeAuctionDateTimeValue(startsAt, { date: dayValue }));
      return;
    }
    onEndsAtChange(mergeAuctionDateTimeValue(endsAt || startsAt, { date: dayValue }));
  };

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 sm:p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-900">Период окна</div>
          <div className="mt-0.5 text-xs text-slate-500">
            {startDate && endDate
              ? `${formatDateLabel(startDate)} — ${formatDateLabel(endDate)}`
              : isAwaitingEnd
                ? 'Выберите дату завершения'
                : 'Выберите дату начала'}
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <span className={`rounded-md px-2.5 py-1 text-xs font-semibold ${startDate ? 'bg-blue-700 text-white' : 'bg-slate-100 text-slate-500'}`}>
            Начало: {startDate ? formatShortDateLabel(startDate) : '—'}
          </span>
          <span className={`rounded-md px-2.5 py-1 text-xs font-semibold ${endDate ? 'bg-blue-100 text-blue-800' : 'bg-slate-100 text-slate-500'}`}>
            Конец: {endDate ? formatShortDateLabel(endDate) : '—'}
          </span>
        </div>
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
            const isStart = day.value === startDate;
            const isEnd = day.value === endDate;
            const isInRange = startDate && endDate
              && compareDateInputValues(day.value, startDate) > 0
              && compareDateInputValues(day.value, endDate) < 0;
            return (
              <button
                key={day.value}
                type="button"
                onClick={() => handleDayClick(day.value)}
                className={`h-9 rounded-md text-xs font-semibold transition ${
                  isStart || isEnd
                    ? 'bg-blue-700 text-white'
                    : isInRange
                      ? 'bg-blue-100 text-blue-800'
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
    </div>
  );
};

const AuctionWeekSelector = ({
  periods = [],
  selectedPlanId,
  activePlanId,
  onSelect,
  disabled = false,
  loading = false,
  error = '',
  previewOnly = false,
}) => {
  const normalizedPeriods = useMemo(
    () => (Array.isArray(periods) ? periods : []).filter((period) => normalizeSchedulePlanId(period?.id)),
    [periods]
  );
  const selectedPeriod = useMemo(
    () => normalizedPeriods.find((period) => Number(period?.id) === Number(selectedPlanId)) || null,
    [normalizedPeriods, selectedPlanId]
  );
  const [visibleMonth, setVisibleMonth] = useState(() => (
    getPeriodMonthValue(selectedPeriod || normalizedPeriods[0]) || getCurrentMonthValue()
  ));

  useEffect(() => {
    const nextMonth = getPeriodMonthValue(selectedPeriod);
    if (nextMonth) setVisibleMonth(nextMonth);
  }, [selectedPeriod]);

  const monthPeriods = useMemo(
    () => normalizedPeriods.filter((period) => periodIntersectsMonth(period, visibleMonth)),
    [normalizedPeriods, visibleMonth]
  );

  if (!normalizedPeriods.length) return null;

  return (
    <section className="rounded-lg border border-slate-200 bg-white px-3 py-3 shadow-sm sm:px-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
            <CalendarDays size={16} className="text-blue-700" />
            Неделя аукциона
          </div>
          <div className="mt-1 text-xs text-slate-500 sm:text-sm">
            {selectedPeriod ? formatAuctionPeriodLabel(selectedPeriod) : 'Выберите неделю'}
            {previewOnly ? ' · просмотр без выбора смен' : ''}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setVisibleMonth((current) => shiftMonthValue(current, -1))}
            className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 transition hover:bg-slate-50"
            title="Предыдущий месяц"
          >
            <ChevronLeft size={16} />
          </button>
          <label className="min-w-[180px]">
            <span className="sr-only">Месяц аукциона</span>
            <input
              type="month"
              value={visibleMonth}
              onChange={(event) => setVisibleMonth(event.target.value || getCurrentMonthValue())}
              className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            />
          </label>
          <button
            type="button"
            onClick={() => setVisibleMonth((current) => shiftMonthValue(current, 1))}
            className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 transition hover:bg-slate-50"
            title="Следующий месяц"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>

      <div className="mt-3 flex min-w-0 gap-2 overflow-x-auto pb-1">
        {monthPeriods.length ? monthPeriods.map((period) => {
          const active = Number(period.id) === Number(selectedPlanId);
          const isCurrent = Number(period.id) === Number(activePlanId);
          return (
            <button
              key={period.id}
              type="button"
              onClick={() => onSelect?.(period)}
              disabled={disabled || loading}
              className={`min-w-[170px] shrink-0 rounded-lg border px-3 py-2 text-left transition disabled:cursor-wait disabled:opacity-60 ${
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
          <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 py-3 text-sm text-slate-500">
            За {formatMonthValueLabel(visibleMonth) || 'выбранный месяц'} недельных планов нет.
          </div>
        )}
      </div>
      {loading ? <div className="mt-2 text-xs text-slate-500">Загружаю неделю...</div> : null}
      {error ? <div className="mt-2 text-xs font-medium text-rose-600">{error}</div> : null}
    </section>
  );
};

const AuctionTimeField = ({
  label,
  dateValue,
  value,
  onChange,
  disabled = false,
  invalid = false
}) => {
  const parts = splitDateTimeInputValue(value);
  const fallback = getAuctionDateTimeWithFallback(value || `${dateValue || getTodayDateInputValue()}T09:00`);
  const currentTime = parts.time || fallback.time;
  const timeInputRef = useRef(null);
  const [draftTimeDigits, setDraftTimeDigits] = useState(() => getAuctionTimeDigits(currentTime));
  const draftTime = formatAuctionTimeDigits(draftTimeDigits);
  const normalizedDraftTime = normalizeAuctionTimeInput(draftTime);
  const draftTimeInvalid = Boolean(draftTimeDigits && !normalizedDraftTime);

  useEffect(() => {
    setDraftTimeDigits(getAuctionTimeDigits(currentTime));
  }, [currentTime]);

  const commitTime = () => {
    const normalized = normalizeAuctionTimeInput(draftTime);
    if (!normalized) {
      setDraftTimeDigits(getAuctionTimeDigits(currentTime));
      return;
    }
    onChange(mergeAuctionDateTimeValue(value || `${dateValue}T${normalized}`, { date: dateValue, time: normalized }));
  };

  const restoreCaret = (digits, digitIndex) => {
    window.requestAnimationFrame(() => {
      const input = timeInputRef.current;
      if (!input || document.activeElement !== input) return;
      const caretPosition = getAuctionTimeCaretPosition(digits, digitIndex);
      input.setSelectionRange(caretPosition, caretPosition);
    });
  };

  const handleTimeChange = (event) => {
    const rawValue = event.target.value;
    const rawCaretPosition = event.target.selectionStart ?? rawValue.length;
    const nextDigits = getAuctionTimeDigits(rawValue);
    const nextDigitIndex = Math.min(getAuctionTimeDigitIndex(rawValue, rawCaretPosition), nextDigits.length);
    setDraftTimeDigits(nextDigits);
    restoreCaret(nextDigits, nextDigitIndex);
  };

  const removeDraftDigitAt = (digitIndex, nextDigitIndex) => {
    if (digitIndex < 0 || digitIndex >= draftTimeDigits.length) return;
    const nextDigits = `${draftTimeDigits.slice(0, digitIndex)}${draftTimeDigits.slice(digitIndex + 1)}`;
    setDraftTimeDigits(nextDigits);
    restoreCaret(nextDigits, nextDigitIndex);
  };

  const handleTimeKeyDown = (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      commitTime();
      return;
    }

    const selectionStart = event.currentTarget.selectionStart ?? 0;
    const selectionEnd = event.currentTarget.selectionEnd ?? selectionStart;
    if (selectionStart !== selectionEnd) return;

    if (event.key === 'Backspace' && draftTime[selectionStart - 1] === ':') {
      event.preventDefault();
      const digitIndex = getAuctionTimeDigitIndex(draftTime, selectionStart) - 1;
      removeDraftDigitAt(digitIndex, digitIndex);
      return;
    }

    if (event.key === 'Delete' && draftTime[selectionStart] === ':') {
      event.preventDefault();
      const digitIndex = getAuctionTimeDigitIndex(draftTime, selectionStart);
      removeDraftDigitAt(digitIndex, digitIndex);
    }
  };

  const applyMinuteDelta = (minutes) => {
    if (!dateValue) return;
    onChange(addMinutesToDateTimeInputValue(`${dateValue}T${currentTime}`, minutes));
  };

  return (
    <div className={`rounded-lg border bg-white p-3 ${invalid ? 'border-rose-300' : 'border-slate-200'}`}>
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-slate-900">{label}</div>
          <div className="mt-0.5 text-xs text-slate-500">{dateValue ? formatDateLabel(dateValue) : 'Сначала выберите дату'}</div>
        </div>
        <div className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 p-1">
          <button
            type="button"
            onClick={() => applyMinuteDelta(-15)}
            disabled={disabled}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition hover:bg-white hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-40"
            title="Минус 15 минут"
          >
            <Minus size={14} />
          </button>
          <input
            ref={timeInputRef}
            value={draftTime}
            onChange={handleTimeChange}
            onBlur={commitTime}
            onKeyDown={handleTimeKeyDown}
            disabled={disabled}
            inputMode="numeric"
            maxLength={5}
            placeholder="00:00"
            aria-label={label}
            className={`h-8 w-[72px] rounded-md border bg-white px-2 text-center text-sm font-semibold tabular-nums text-slate-950 outline-none transition focus:ring-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400 ${
              draftTimeInvalid
                ? 'border-rose-300 focus:border-rose-400 focus:ring-rose-100'
                : 'border-transparent focus:border-blue-500 focus:ring-blue-100'
            }`}
          />
          <button
            type="button"
            onClick={() => applyMinuteDelta(15)}
            disabled={disabled}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-600 transition hover:bg-white hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-40"
            title="Плюс 15 минут"
          >
            <Plus size={14} />
          </button>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-5 gap-1.5">
        {AUCTION_TIME_PRESETS.map((time) => {
          const active = time === currentTime;
          return (
            <button
              key={time}
              type="button"
              onClick={() => onChange(mergeAuctionDateTimeValue(value || `${dateValue}T${time}`, { date: dateValue, time }))}
              disabled={disabled}
              className={`h-8 rounded-md text-xs font-semibold tabular-nums transition disabled:cursor-not-allowed disabled:opacity-40 ${
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
  );
};

const getAuctionRuntimeStatus = (settings, nowMs) => {
  if (!settings?.enabled) return 'disabled';
  if (settings.finished_at) return 'closed';
  if (settings.paused_at) return 'paused';
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

const SHIFT_AUCTION_INSTRUCTIONS_VERSION = 'v3';

const StatusPillPreview = ({ tone, icon: Icon, label, detail }) => (
  <span className={`inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-xs font-semibold sm:text-sm ${tone}`}>
    <Icon size={15} />
    <span>{label}</span>
    {detail ? <span className="border-l border-current/30 pl-2 tabular-nums">{detail}</span> : null}
  </span>
);

const LotChipPreview = ({ tone = 'available', label }) => {
  if (tone === 'mine') {
    return (
      <div className="flex h-8 w-20 items-center justify-center rounded border border-emerald-600 bg-emerald-600 px-2 text-xs font-semibold tabular-nums text-white shadow-sm">
        {label}
      </div>
    );
  }
  if (tone === 'taken') {
    return (
      <div className="flex h-8 w-20 items-center justify-center rounded border border-slate-200 bg-slate-100 px-2 text-xs font-semibold tabular-nums text-slate-400 shadow-sm">
        {label}
      </div>
    );
  }
  if (tone === 'blocked') {
    return (
      <div className="flex h-8 w-20 items-center justify-center rounded border border-slate-200 bg-slate-50 px-2 text-xs font-semibold tabular-nums text-slate-400 shadow-sm">
        {label}
      </div>
    );
  }
  const style = tone === 'morning'
    ? { backgroundColor: 'rgb(219, 234, 254)', borderColor: 'rgb(147, 197, 253)', color: '#1e3a8a' }
    : tone === 'midday'
      ? { backgroundColor: 'rgb(123, 175, 240)', borderColor: 'rgb(82, 137, 220)', color: '#0f1d4a' }
      : { backgroundColor: 'rgb(46, 99, 199)', borderColor: 'rgb(30, 64, 175)', color: '#ffffff' };
  return (
    <div style={style} className="flex h-8 w-20 items-center justify-center rounded border px-2 text-xs font-semibold tabular-nums shadow-sm">
      {label}
    </div>
  );
};

const DayBarCellPreview = ({ date, label, sublabel, active = false, tone = 'default' }) => {
  const toneClass = tone === 'shift'
    ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
    : tone === 'off'
      ? 'border-blue-300 bg-blue-50 text-blue-800'
      : tone === 'blocked'
        ? 'border-rose-300 bg-rose-50 text-rose-800'
        : tone === 'admin-full'
          ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
          : tone === 'admin-some'
            ? 'border-blue-300 bg-blue-50 text-blue-800'
            : 'border-slate-200 bg-white text-slate-600';
  return (
    <div className={`flex h-[56px] w-[68px] shrink-0 flex-col items-center justify-center rounded border px-1 py-1.5 text-center ${toneClass} ${active ? 'ring-2 ring-blue-500 ring-offset-1' : ''}`}>
      <span className="block truncate text-[11px] font-semibold leading-4">{date}</span>
      <span className="mt-0.5 block truncate text-[11px] font-bold tabular-nums">{label}</span>
      {sublabel ? <span className="block truncate text-[10px] font-semibold tabular-nums">{sublabel}</span> : null}
    </div>
  );
};

const ButtonPreview = ({ variant = 'primary', icon: Icon, children }) => {
  const cls = variant === 'primary'
    ? 'bg-blue-700 text-white shadow-sm hover:bg-blue-800'
    : variant === 'dark'
      ? 'bg-slate-900 text-white shadow-sm'
      : variant === 'danger'
        ? 'bg-rose-600 text-white shadow-sm'
        : variant === 'success'
          ? 'bg-emerald-600 text-white shadow-sm'
          : 'border border-slate-200 bg-white text-slate-700 shadow-sm';
  return (
    <span className={`inline-flex h-10 items-center gap-2 rounded-lg px-4 text-sm font-semibold ${cls}`}>
      {Icon ? <Icon size={16} /> : null}
      {children}
    </span>
  );
};

const OPERATOR_INSTRUCTION_STEPS = [
  {
    icon: Info,
    title: 'Что такое аукцион смен',
    body: 'Это окно, в котором утверждённые смены распределяются между операторами в реальном времени. Открывается на короткий период — успейте выбрать удобные смены до закрытия.',
    visual: (
      <div className="flex flex-wrap items-center gap-2">
        <StatusPillPreview tone="border-emerald-200 bg-emerald-50 text-emerald-800" icon={ShieldCheck} label="Аукцион открыт" detail="до закрытия 00:12:45" />
        <span className="text-xs text-slate-500">— так выглядит индикатор статуса в шапке.</span>
      </div>
    )
  },
  {
    icon: Clock3,
    title: 'Шаг 1 · Дождитесь открытия',
    body: 'Когда аукцион в статусе «Откроется» — в правом верхнем углу идёт обратный отсчёт. До старта можно зайти и выбрать выходные, но забирать смены ещё нельзя.',
    visual: (
      <div className="flex flex-col items-start gap-2">
        <StatusPillPreview tone="border-blue-200 bg-blue-50 text-blue-800" icon={Clock3} label="Откроется" detail="00:14:32" />
        <span className="text-xs text-slate-500">Цифры обновляются каждую секунду. Когда отсчёт дойдёт до нуля — кнопки смен оживут.</span>
      </div>
    )
  },
  {
    icon: ListChecks,
    title: 'Шаг 2 · Выберите выходные (до 2 дней)',
    body: 'В левой панели «Мои выходные» кликайте на дни, которые хотите оставить свободными. Можно выбрать максимум 2 дня на период. Эти дни выпадут из таблицы — смены на них вы выбирать не будете.',
    visual: (
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
          <ListChecks size={16} className="text-blue-700" /> Мои выходные
        </div>
        <div className="flex flex-wrap gap-1.5">
          <span className="flex min-w-[120px] items-center justify-between gap-2 rounded-md border border-blue-300 bg-blue-50 px-2 py-2 text-sm font-medium text-blue-800">
            <span>пн, 02 июн</span>
            <CheckCircle2 size={16} />
          </span>
          <span className="flex min-w-[120px] items-center justify-between gap-2 rounded-md border border-rose-200 bg-rose-50 px-2 py-2 text-sm font-medium text-rose-700">
            <span>вт, 03 июн</span>
            <span className="text-[11px] font-semibold">Б/С</span>
          </span>
          <span className="flex min-w-[120px] items-center justify-between gap-2 rounded-md border border-slate-200 bg-white px-2 py-2 text-sm font-medium text-slate-700">
            <span>ср, 04 июн</span>
          </span>
          <span className="flex min-w-[120px] items-center justify-between gap-2 rounded-md border border-slate-200 bg-slate-50 px-2 py-2 text-sm font-medium text-slate-400">
            <span>чт, 05 июн</span>
          </span>
        </div>
        <span className="block text-xs text-slate-500">Синий = выбран как выходной, розовый «Б/С» = занят статусным периодом, серый затемнённый = квота уже исчерпана.</span>
      </div>
    ),
    nuances: [
      'Если у вас уже стоит статусный период (отпуск, больничный, Б/С) на дни внутри аукциона — они занимают квоту автоматически.',
      'Если статусные периоды покрыли 2 дня — выбрать дополнительные выходные нельзя.'
    ]
  },
  {
    icon: Hand,
    title: 'Шаг 3 · Заберите смены',
    body: 'В таблице кликните по нужному времени смены. Цвет смены показывает время старта (от голубого утром до тёмно-синего вечером). Ваша смена помечается зелёным, чужая — серым.',
    visual: (
      <div className="space-y-2">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <LotChipPreview tone="morning" label="07-16" />
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-slate-500">утро</span>
          </div>
          <div className="space-y-1">
            <LotChipPreview tone="midday" label="13-22" />
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-slate-500">день</span>
          </div>
          <div className="space-y-1">
            <LotChipPreview tone="evening" label="17-02" />
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-slate-500">вечер</span>
          </div>
          <div className="space-y-1">
            <LotChipPreview tone="mine" label="10-19" />
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-emerald-700">моя</span>
          </div>
          <div className="space-y-1">
            <LotChipPreview tone="taken" label="09-18" />
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-slate-500">чужая</span>
          </div>
          <div className="space-y-1">
            <LotChipPreview tone="blocked" label="11-20" />
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-slate-500">недост.</span>
          </div>
        </div>
        <span className="block text-xs text-slate-500">Клик по доступной кнопке закрепляет смену за вами. Серая «недост.» означает превышение нормы или закрытый день.</span>
      </div>
    ),
    nuances: [
      'На один день — только одна смена.',
      'Сумма часов не должна превышать вашу норму на период (норма видна в правом верхнем углу).',
      'Если смена недоступна по правилам (превысит норму, на этот день уже есть смена и т. п.) — кнопка станет серой с подсказкой.'
    ],
    example: 'Например, при ставке 1.0 и периоде в 7 дней с 1 выходным норма ≈ 48 часов. Если вы уже забрали 40, останется 8 часов, чтобы добрать.'
  },
  {
    icon: Undo2,
    title: 'Шаг 4 · Передумали? Верните смену',
    body: 'В нижней панели дней нажмите на день, где у вас уже стоит смена — появится карточка «Хотите ли вы вернуть эту смену?». После подтверждения смена снова станет доступной остальным операторам.',
    visual: (
      <div className="space-y-3">
        <div>
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Нижняя панель дней</div>
          <div className="flex gap-1.5 overflow-hidden rounded-xl border border-slate-200 bg-white p-2 shadow-sm">
            <DayBarCellPreview date="пн, 02" label="Пусто" />
            <DayBarCellPreview date="вт, 03" label="10-19" sublabel="9 ч" tone="shift" active />
            <DayBarCellPreview date="ср, 04" label="Б/С" tone="blocked" />
            <DayBarCellPreview date="чт, 05" label="Смена" tone="off" />
          </div>
          <div className="mt-1 text-xs text-slate-500">Клик по зелёной ячейке с временем смены → откроется карточка подтверждения.</div>
        </div>
        <div>
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Карточка подтверждения</div>
          <div className="w-full max-w-xs rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="text-sm font-semibold text-slate-950">Хотите ли вы вернуть эту смену?</div>
            <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
              <div className="text-sm font-semibold text-slate-900">вт, 03 июн</div>
              <div className="text-xs text-slate-600">10:00 - 19:00 · 9 ч</div>
            </div>
            <p className="mt-2 text-[11px] leading-5 text-slate-600">Смена снова станет доступной для других операторов.</p>
            <div className="mt-3 flex justify-end gap-2">
              <ButtonPreview variant="outline">Отмена</ButtonPreview>
              <ButtonPreview variant="danger">Вернуть смену</ButtonPreview>
            </div>
          </div>
        </div>
      </div>
    ),
    nuances: [
      'Вернуть можно только пока аукцион ещё открыт.',
      'Если кто-то параллельно её уже забрал — система покажет ошибку, ничего страшного не произойдёт.'
    ]
  },
  {
    icon: Wifi,
    title: 'Реалтайм без обновления страницы',
    body: 'Когда другой оператор забирает или возвращает смену — у вас она моментально меняет статус. Не нужно нажимать F5. Индикатор «Realtime online» в шапке подтверждает связь.',
    visual: (
      <div className="flex flex-wrap items-center gap-2">
        <StatusPillPreview tone="border-emerald-200 bg-emerald-50 text-emerald-700" icon={Wifi} label="Realtime online" />
        <StatusPillPreview tone="border-slate-200 bg-white text-slate-600" icon={Wifi} label="Переподключение..." />
        <StatusPillPreview tone="border-slate-200 bg-white text-slate-600" icon={Wifi} label="Realtime idle" />
      </div>
    )
  },
  {
    icon: Flame,
    title: 'После аукциона: оранжевые смены',
    body: 'Когда аукцион закрыт и админ нажал «Сохранить в графики», оставшиеся свободные смены окрашиваются в оранжевый. Их ещё можно забрать — поштучно и в любой момент, пока смена не началась. Берётся такая смена напрямую в ваши настоящие графики работы.',
    visual: (
      <div className="space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <div style={{ backgroundColor: 'rgb(255, 237, 213)', borderColor: 'rgb(253, 186, 116)', color: '#7c2d12' }} className="flex h-8 w-20 items-center justify-center rounded border px-2 text-xs font-semibold tabular-nums shadow-sm">
              07-16
            </div>
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-orange-700">свободна</span>
          </div>
          <div className="space-y-1">
            <div style={{ backgroundColor: 'rgb(194, 65, 12)', borderColor: 'rgb(154, 52, 18)', color: '#ffffff' }} className="flex h-8 w-20 items-center justify-center rounded border px-2 text-xs font-semibold tabular-nums shadow-sm">
              17-02
            </div>
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-orange-700">свободна</span>
          </div>
          <div className="space-y-1">
            <div className="flex h-8 w-20 items-center justify-center rounded border border-orange-700 bg-orange-600 px-2 text-xs font-semibold tabular-nums text-white shadow-sm">
              10-19
            </div>
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-orange-800">взяли вы</span>
          </div>
        </div>
        <div className="w-full max-w-sm rounded-xl border border-orange-200 bg-white p-4 shadow-sm">
          <div className="text-sm font-semibold text-slate-950">Забрать дополнительную смену?</div>
          <div className="mt-2 rounded-lg border border-orange-200 bg-orange-50 px-3 py-2">
            <div className="text-sm font-semibold text-slate-900">вт, 03 июн</div>
            <div className="text-xs text-slate-700">10:00 - 19:00 · 9 ч</div>
          </div>
          <p className="mt-2 text-[11px] leading-5 text-orange-900">Если возьмёте — вернуть не получится. Смена сразу появится в ваших графиках. Стыкуется с соседней — они объединятся, перерывы пересчитаются.</p>
          <div className="mt-3 flex justify-end gap-2">
            <ButtonPreview variant="outline">Отмена</ButtonPreview>
            <span className="inline-flex h-10 items-center gap-2 rounded-lg bg-orange-600 px-4 text-sm font-semibold text-white shadow-sm">Забрать</span>
          </div>
        </div>
      </div>
    ),
    nuances: [
      'Берётся только смена, которая ещё не началась.',
      'Если смена пересекается по времени с уже стоящей у вас в графиках — система не даст её взять.',
      'Если новая смена стыкуется встык (например 12:00-17:00 и уже есть 17:00-22:00) — они автоматически объединяются в одну, перерывы пересчитываются по правилам направления.',
      'Если стыка нет — для смены посчитаются собственные перерывы по тем же правилам.',
      'Вернуть такую смену нельзя — она уже в реальном графике работы.'
    ]
  },
  {
    icon: MousePointerClick,
    title: 'Можно взять часть смены',
    body: 'Оранжевую смену можно забрать не только целиком, но и частью. Если часть смены уже взял другой оператор, смена остаётся оранжевой и показывает только СВОБОДНУЮ часть, а в углу появляется маркер. Нажмите — откроется окно, где можно выбрать удобный интервал внутри свободной части.',
    visual: (
      <div className="space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <div className="relative flex h-8 w-20 items-center justify-center rounded border border-orange-700 px-2 text-xs font-semibold tabular-nums text-orange-900 shadow-sm" style={{ backgroundColor: 'rgb(255, 237, 213)' }}>
              15-20
              <span className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-white ring-1 ring-orange-600" />
            </div>
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-orange-700">часть занята</span>
          </div>
          <span className="text-xs text-slate-500">Маркер в углу = часть смены уже взяли; вам доступна оставшаяся свободная часть (15:00–20:00).</span>
        </div>
        <div className="w-full max-w-sm rounded-xl border border-orange-200 bg-white p-4 shadow-sm">
          <div className="text-sm font-semibold text-slate-950">Забрать часть смены</div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <span className="block text-xs font-semibold text-slate-700">Начало
              <span className="mt-1 flex h-9 items-center rounded-md border border-slate-200 bg-white px-2 text-sm font-semibold tabular-nums text-slate-900">15:00</span>
            </span>
            <span className="block text-xs font-semibold text-slate-700">Конец
              <span className="mt-1 flex h-9 items-center rounded-md border border-slate-200 bg-white px-2 text-sm font-semibold tabular-nums text-slate-900">20:00</span>
            </span>
          </div>
          <p className="mt-2 text-[11px] leading-5 text-orange-900">Можно выбрать любой интервал внутри свободной части — он сразу попадёт в ваш график.</p>
        </div>
      </div>
    ),
    nuances: [
      'Уже взятая кем-то часть недоступна — выбрать можно только в пределах свободной части.',
      'Если свободных кусков несколько — можно взять любой не пересекающийся.',
      'Когда всю смену разобрали по частям — она становится серой (полностью занята).'
    ]
  },
  {
    icon: AlertTriangle,
    title: 'На что обратить внимание',
    body: 'Несколько частых ситуаций, которые могут сбить с толку.',
    visual: (
      <div className="flex flex-wrap gap-2">
        <StatusPillPreview tone="border-blue-200 bg-blue-50 text-blue-800" icon={Clock3} label="Откроется" detail="00:05:21" />
        <StatusPillPreview tone="border-emerald-200 bg-emerald-50 text-emerald-800" icon={ShieldCheck} label="Аукцион открыт" detail="до закрытия 00:14:32" />
        <StatusPillPreview tone="border-slate-200 bg-slate-100 text-slate-600" icon={Clock3} label="Аукцион закрыт" detail="выбор завершен" />
        <StatusPillPreview tone="border-amber-200 bg-amber-50 text-amber-800" icon={Clock3} label="Аукцион выключен" />
      </div>
    ),
    nuances: [
      'Аукцион выключен — раздел закрыт, кнопки не реагируют. Дождитесь анонса администратора.',
      'Аукцион закрыт — выбор времени прошёл. Можете только смотреть итоги. Если админ нажал «Сохранить в графики» — оставшиеся смены окрасятся в оранжевый и их ещё можно будет забирать.',
      'Норма уже набрана — забрать ещё одну смену в этот период не получится, даже если она доступна.',
      'Закрытый день (отпуск/больничный) — смены на этот день не показываются и забирать их нельзя.'
    ]
  }
];

const ADMIN_INSTRUCTION_STEPS = [
  {
    icon: Info,
    title: 'Что такое тестовый аукцион',
    body: 'Полигон realtime-распределения смен между выбранной группой операторов. Используется для проверки сценария будущего «боевого» аукциона. Все настройки и смены — изолированы от основного графика.',
    visual: (
      <div className="flex flex-wrap items-center gap-2">
        <StatusPillPreview tone="border-emerald-200 bg-emerald-50 text-emerald-800" icon={ShieldCheck} label="Аукцион открыт" detail="до закрытия 00:25:00" />
        <StatusPillPreview tone="border-emerald-200 bg-emerald-50 text-emerald-700" icon={Wifi} label="Realtime online" />
      </div>
    )
  },
  {
    icon: CalendarClock,
    title: 'Шаг 1 · Подготовьте смены через расчёт ресурсов',
    body: 'Перед запуском аукциона смены нужно сгенерировать. Откройте «Расчёт ресурсов» (кнопка в шапке) и проведите штатную генерацию.',
    visual: (
      <div className="flex flex-col items-start gap-2">
        <ButtonPreview variant="dark" icon={CalendarClock}>Генерация графиков</ButtonPreview>
        <span className="text-xs text-slate-500">Кнопка в правом верхнем углу раздела — открывает расчёт ресурсов.</span>
      </div>
    ),
    nuances: [
      'Без сгенерированных смен раздел будет пустым.'
    ]
  },
  {
    icon: Sparkles,
    title: 'Шаг 2 · Создайте тестовые лоты',
    body: 'Нажмите «Создать тестовые смены» в блоке «Тестовый запуск». Система сгенерирует набор смен на ближайшие 7 дней по шаблонам (1.0, 0.75, 0.5 ставки и ночные 20*08). При повторном клике существующие тестовые лоты пересоздаются.',
    visual: (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <ButtonPreview variant="outline" icon={Sparkles}>Создать тестовые смены</ButtonPreview>
          <ButtonPreview variant="primary" icon={Save}>Сохранить</ButtonPreview>
        </div>
        <span className="block text-xs text-slate-500">Кнопки в правом углу блока «Тестовый запуск».</span>
      </div>
    ),
    nuances: [
      'Создание лотов сбрасывает выбранные операторами выходные на тестовом полигоне.',
      'На «боевые» графики это не влияет.'
    ]
  },
  {
    icon: Settings2,
    title: 'Шаг 3 · Настройте окно открытия',
    body: 'Задайте «Старт аукциона» и «Завершение» в формате datetime-local. До старта операторы увидят таймер, после завершения — выбор закрывается. Поле «Текст для тестовой группы» — короткое сообщение, которое участники увидят в шапке.',
    visual: (
      <div className="space-y-2.5">
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <div className="text-xs font-semibold text-slate-800">Старт аукциона</div>
            <div className="mt-1 flex h-10 items-center rounded-lg border border-slate-200 bg-white px-3 text-sm tabular-nums text-slate-700 shadow-sm">05.06.2026, 09:00</div>
          </div>
          <div>
            <div className="text-xs font-semibold text-slate-800">Завершение</div>
            <div className="mt-1 flex h-10 items-center rounded-lg border border-slate-200 bg-white px-3 text-sm tabular-nums text-slate-700 shadow-sm">05.06.2026, 09:30</div>
          </div>
        </div>
        <div>
          <div className="text-xs font-semibold text-slate-800">Текст для тестовой группы</div>
          <div className="mt-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 shadow-sm">Тестовый запуск аукциона смен на июнь. Будьте онлайн в 09:00.</div>
        </div>
      </div>
    ),
    example: 'Пример: старт 05.06 09:00, завершение 05.06 09:30. Это даст 30-минутное окно «гонки» за смены.'
  },
  {
    icon: Users,
    title: 'Шаг 4 · Выберите участников',
    body: 'В списке справа отметьте операторов, которые получат доступ. Поиск помогает быстро найти по имени, направлению или СВ. Только отмеченные операторы увидят раздел.',
    visual: (
      <div className="space-y-2">
        <div className="relative max-w-sm">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <div className="flex h-10 items-center rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-400 shadow-sm">Поиск по оператору, направлению или СВ</div>
        </div>
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center gap-3 border-b border-slate-100 bg-blue-50 px-4 py-2.5">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-blue-700 bg-blue-700 text-white">
              <CheckCircle2 size={12} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold text-slate-900">Иванов Иван Иванович</span>
              <span className="block truncate text-xs text-slate-500">Контакт-центр · ставка 1.00 · Петров П. П.</span>
            </span>
          </div>
          <div className="flex items-center gap-3 px-4 py-2.5">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-slate-300 bg-white"></span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold text-slate-900">Сидоров Сидор Сидорович</span>
              <span className="block truncate text-xs text-slate-500">Чат-менеджер · ставка 0.75 · Петров П. П.</span>
            </span>
          </div>
        </div>
        <span className="block text-xs text-slate-500">Синий чекбокс — оператор включён в тестовую группу.</span>
      </div>
    ),
    nuances: [
      'Если оператор уже уволен — он автоматически не попадёт в группу.',
      'Можно менять состав группы и после старта — новые участники получат доступ сразу.'
    ]
  },
  {
    icon: PlayCircle,
    title: 'Шаг 5 · Включите режим и сохраните',
    body: 'Переключите «Включить тестовый режим» и нажмите «Сохранить». С этого момента выбранные операторы видят раздел и таймер до старта (либо сразу выбирают, если время старта уже прошло).',
    visual: (
      <div className="space-y-3">
        <label className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-3">
          <span>
            <span className="block text-sm font-semibold text-slate-900">Включить тестовый режим</span>
            <span className="block text-xs text-slate-500">Выбранные операторы увидят realtime-полигон.</span>
          </span>
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-blue-700 bg-blue-700">
            <CheckCircle2 size={12} className="text-white" />
          </span>
        </label>
        <ButtonPreview variant="primary" icon={Save}>Сохранить</ButtonPreview>
      </div>
    ),
    nuances: [
      'Выключение режима — мгновенное: операторы потеряют доступ к разделу до нового включения.',
      'Изменения в окнах старта/завершения подхватываются всеми клиентами без перезагрузки.'
    ]
  },
  {
    icon: MousePointerClick,
    title: 'Шаг 6 · Наблюдайте за процессом',
    body: 'В режиме админа таблица показывает все смены и кто их забрал. Нижний бар дней — сводка по каждому дню (закрыто/всего). Realtime обновляет состояние моментально для всех подключённых клиентов.',
    visual: (
      <div className="space-y-3">
        <div>
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Нижний бар (вид администратора)</div>
          <div className="flex gap-1.5 overflow-hidden rounded-xl border border-slate-200 bg-white p-2 shadow-sm">
            <DayBarCellPreview date="пн, 02" label="10/10" tone="admin-full" />
            <DayBarCellPreview date="вт, 03" label="6/10" tone="admin-some" active />
            <DayBarCellPreview date="ср, 04" label="3/10" tone="admin-some" />
            <DayBarCellPreview date="чт, 05" label="0/10" />
          </div>
          <div className="mt-1 text-xs text-slate-500">Зелёная ячейка — все смены дня закрыты, синяя — частично, белая — никто ещё не выбрал.</div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusPillPreview tone="border-emerald-200 bg-emerald-50 text-emerald-700" icon={Wifi} label="Realtime online" />
          <span className="text-xs text-slate-500">Индикатор должен гореть зелёным.</span>
        </div>
      </div>
    ),
    nuances: [
      'Можно открыть раздел в режиме оператора через тестовый аккаунт, чтобы убедиться в корректности UX.',
      'Индикатор «Realtime online» в шапке должен гореть зелёным.'
    ]
  },
  {
    icon: Save,
    title: 'Шаг 7 · Завершите и сохраните в графики',
    body: 'После завершения аукциона нажмите «Сохранить в графики» — все взятые смены попадут в настоящие графики работы операторов с автоматическими перерывами по правилам направления. После сохранения остальные свободные/отменённые смены становятся доступными как «оранжевые» для пост-аукционного добора.',
    visual: (
      <div className="flex flex-wrap items-center gap-2">
        <ButtonPreview variant="danger" icon={Square}>Завершить</ButtonPreview>
        <ButtonPreview variant="success" icon={Save}>Сохранить в графики</ButtonPreview>
      </div>
    ),
    nuances: [
      'Сохранять можно только закрытый аукцион (статус «Аукцион закрыт»).',
      'Сохранение очищает день оператора перед записью смен — старые смены в этих днях замещаются итогами аукциона.',
      'После сохранения раздел переходит в пост-аукционный режим: операторы могут добирать оставшиеся смены сами.'
    ]
  },
  {
    icon: Flame,
    title: 'Шаг 8 · Пост-аукционный режим',
    body: 'После «Сохранить в графики» свободные и отменённые смены окрашиваются в оранжевый. Операторы могут поштучно забирать их — смена сразу пишется в их настоящие графики работы.',
    visual: (
      <div className="space-y-2">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <div style={{ backgroundColor: 'rgb(255, 237, 213)', borderColor: 'rgb(253, 186, 116)', color: '#7c2d12' }} className="flex h-8 w-20 items-center justify-center rounded border px-2 text-xs font-semibold tabular-nums shadow-sm">
              07-16
            </div>
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-orange-700">свободна</span>
          </div>
          <div className="space-y-1">
            <div style={{ backgroundColor: 'rgb(194, 65, 12)', borderColor: 'rgb(154, 52, 18)', color: '#ffffff' }} className="flex h-8 w-20 items-center justify-center rounded border px-2 text-xs font-semibold tabular-nums shadow-sm">
              17-02
            </div>
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-orange-700">свободна</span>
          </div>
          <div className="space-y-1">
            <div className="flex h-8 w-20 items-center justify-center rounded border border-orange-700 bg-orange-600 px-2 text-xs font-semibold tabular-nums text-white shadow-sm">
              10-19
            </div>
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-orange-800">взяли</span>
          </div>
        </div>
        <span className="block text-xs text-slate-500">Чем темнее оранжевый — тем позже начинается смена. Тёмная карточка с белым шрифтом — смена закреплена за оператором в пост-аукционе.</span>
      </div>
    ),
    nuances: [
      'Брать можно только смены, которые ещё не начались.',
      'Проверяется пересечение с реальными сменами оператора в work_shifts — не дадим взять пересекающуюся.',
      'Если смена стыкуется со стоящей у оператора по краю (например 12:00-17:00 встык к 17:00-22:00) — они объединяются в одну, перерывы пересчитываются автоматически.',
      'Вернуть пост-аукционную смену оператор не может — она уже в реальном графике.'
    ]
  },
  {
    icon: Bell,
    title: 'Шаг 9 · Уведомления о пост-аукционных взятиях',
    body: 'В табе «Мониторинг смен» включите тумблер «Получать уведомления о взятии смены» — и вам в Telegram будет приходить сообщение каждый раз, когда оператор забирает оранжевую смену.',
    visual: (
      <label className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 bg-white px-3 py-3 shadow-sm">
        <span>
          <span className="block text-sm font-semibold text-slate-900">Получать уведомления о взятии смены</span>
          <span className="block text-xs text-slate-500">Когда оператор берёт дополнительную смену после окончания аукциона, в Telegram придёт уведомление с данными.</span>
        </span>
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-orange-600 bg-orange-600">
          <CheckCircle2 size={12} className="text-white" />
        </span>
      </label>
    ),
    nuances: [
      'Тумблер персональный — каждый админ управляет своими уведомлениями.',
      'Для доставки сообщения у админа должен быть привязан telegram_id.',
      'В сообщении: ФИО оператора, дата смены, время начала–конца, отметка времени взятия.'
    ]
  },
  {
    icon: Users,
    title: 'Частичный добор: кто какую часть взял',
    body: 'Одну смену операторы могут разобрать по частям. В мониторинге это видно так: полностью разобранная по частям смена — серая, показывает полный интервал и маркер; частично занятая — оранжевая, показывает свободную часть и маркер. Нажмите на ячейку смены — откроется разбивка: кто какую часть взял и что ещё свободно.',
    visual: (
      <div className="space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <div className="relative flex h-8 w-24 items-center justify-center rounded border border-slate-200 bg-slate-100 px-2 text-xs font-semibold tabular-nums text-slate-500 shadow-sm">
              13-20
              <span className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-white ring-1 ring-orange-600" />
            </div>
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-slate-500">занята по частям</span>
          </div>
          <div className="space-y-1">
            <div className="relative flex h-8 w-24 items-center justify-center rounded border border-orange-700 px-2 text-xs font-semibold tabular-nums text-orange-900 shadow-sm" style={{ backgroundColor: 'rgb(255, 237, 213)' }}>
              15-20
              <span className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-white ring-1 ring-orange-600" />
            </div>
            <span className="block text-center text-[10px] font-semibold uppercase tracking-wider text-orange-700">частично свободна</span>
          </div>
        </div>
        <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <div className="text-sm font-semibold text-slate-900">Смена 13:00–20:00</div>
          <div className="mt-2 space-y-1 text-[12px]">
            <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: '#0A84FF' }} /><span className="min-w-0 flex-1 truncate text-slate-800">Сергей</span><span className="shrink-0 tabular-nums text-slate-500">13:00–15:00</span></div>
            <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: '#30D158' }} /><span className="min-w-0 flex-1 truncate text-slate-800">Алия</span><span className="shrink-0 tabular-nums text-slate-500">15:00–20:00</span></div>
          </div>
        </div>
      </div>
    ),
    nuances: [
      'Маркер в углу ячейки = смену разобрали по частям (или часть уже занята).',
      'Клик по ячейке → модалка с таймлайном: кто какую часть взял и что свободно.',
      'В «Таблице смен» часть каждого оператора видна в его ячейке; можно назначить свободную часть оператору или снять конкретную часть.'
    ]
  },
  {
    icon: Download,
    title: 'Отчёт Excel по аукциону',
    body: 'Кнопка «Отчёт Excel» в шапке блока «Тестовый запуск» выгружает сводный отчёт за выбранный период: матрица ФИО × Даты с временем взятых смен, а ниже — матрица неразобранных смен по дням.',
    visual: (
      <ButtonPreview variant="outline" icon={Download}>Отчёт Excel</ButtonPreview>
    ),
    nuances: [
      'Формат времени смен: ЧЧ*ЧЧ для целочасовых (например 07*13), ЧЧ/ММ*ЧЧ — если есть минуты (07/30*13).',
      'Зелёная заливка — смена взята оператором, серая — выходной, жёлтая — свободная, красноватая — отменённая.',
      'Файл называется shift_auction_report_<начало>_<конец>.xlsx.'
    ]
  },
  {
    icon: AlertTriangle,
    title: 'Нюансы и ограничения',
    body: 'Полезно держать в голове при подготовке запуска.',
    visual: (
      <div className="flex flex-wrap gap-2">
        <StatusPillPreview tone="border-blue-200 bg-blue-50 text-blue-800" icon={Clock3} label="Откроется" detail="00:05:21" />
        <StatusPillPreview tone="border-slate-200 bg-slate-100 text-slate-600" icon={Clock3} label="Аукцион закрыт" detail="выбор завершен" />
        <StatusPillPreview tone="border-amber-200 bg-amber-50 text-amber-800" icon={Clock3} label="Аукцион выключен" />
      </div>
    ),
    nuances: [
      'Все правки в тестовых лотах необратимы — пересоздание сбросит выбор операторов.',
      'Аукцион работает на realtime через Server-Sent Events. Если перед сервисом стоит nginx/прокси — должен быть включён keepalive ≥ 60 сек.',
      'Текст уведомления для группы лучше делать коротким — он отображается только в подсказке статус-бара.',
      'Если статусный период оператора (отпуск, больничный) пересекается с днём аукциона — смены на этот день он не увидит.',
      'Пост-аукционный режим включается автоматически после «Сохранить в графики» и работает, пока админ не запустит новый аукцион через «Начать заново».'
    ]
  }
];

const ShiftAuctionInstructionsModal = ({ open, role, canSwitchRole = false, onClose }) => {
  const [viewRole, setViewRole] = useState(role === 'admin' ? 'admin' : 'operator');
  const [currentStep, setCurrentStep] = useState(0);

  useEffect(() => {
    if (open) {
      setViewRole(role === 'admin' ? 'admin' : 'operator');
      setCurrentStep(0);
    }
  }, [open, role]);

  const isAdminView = viewRole === 'admin';
  const steps = isAdminView ? ADMIN_INSTRUCTION_STEPS : OPERATOR_INSTRUCTION_STEPS;
  const totalSteps = steps.length;

  useEffect(() => {
    if (!open) return undefined;
    const handleKey = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        setCurrentStep((step) => Math.min(step + 1, totalSteps - 1));
        return;
      }
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        setCurrentStep((step) => Math.max(step - 1, 0));
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onClose, totalSteps]);

  if (!open) return null;

  const safeStep = Math.min(currentStep, totalSteps - 1);
  const step = steps[safeStep];
  if (!step) return null;
  const StepIcon = step.icon || Info;
  const isFirst = safeStep === 0;
  const isLast = safeStep === totalSteps - 1;
  const progressWidth = `${((safeStep + 1) / totalSteps) * 100}%`;

  const title = isAdminView ? 'Инструкция для администратора' : 'Инструкция для оператора';
  const subtitle = isAdminView
    ? 'Как подготовить, запустить и контролировать тестовый аукцион смен.'
    : 'Как выбрать выходные, забрать и при необходимости вернуть смену.';

  const switchRole = (next) => {
    if (next === viewRole) return;
    setViewRole(next);
    setCurrentStep(0);
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-stretch justify-center bg-slate-900/60 sm:items-center sm:px-6 sm:py-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="shift-auction-instructions-title"
      onClick={onClose}
    >
      <div
        className="flex h-[100dvh] min-h-0 w-full flex-col overflow-hidden bg-white shadow-2xl sm:h-auto sm:max-h-[90vh] sm:max-w-4xl sm:rounded-2xl sm:border sm:border-slate-200"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="shrink-0 border-b border-slate-200 bg-gradient-to-r from-blue-700 to-blue-900 px-4 py-3 text-white sm:px-7 sm:py-5">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-2.5 sm:gap-3">
              <div className="hidden h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white/15 sm:flex">
                <BookOpen size={22} />
              </div>
              <div className="min-w-0">
                <h2 id="shift-auction-instructions-title" className="flex items-center gap-2 text-base font-semibold sm:text-lg">
                  <BookOpen size={18} className="shrink-0 sm:hidden" />
                  <span className="truncate">{title}</span>
                </h2>
                <p className="mt-0.5 hidden text-xs leading-5 text-blue-100 sm:block sm:text-sm">{subtitle}</p>
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Закрыть инструкцию"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-white/80 transition hover:bg-white/15 hover:text-white"
            >
              <X size={18} />
            </button>
          </div>
          {canSwitchRole ? (
            <div className="mt-3 inline-flex max-w-full rounded-lg bg-white/15 p-1 text-xs sm:text-sm">
              <button
                type="button"
                onClick={() => switchRole('operator')}
                className={`min-w-0 flex-1 truncate rounded-md px-3 py-1.5 font-semibold transition ${!isAdminView ? 'bg-white text-blue-800 shadow-sm' : 'text-white/85 hover:bg-white/10 hover:text-white'}`}
              >
                Оператор
              </button>
              <button
                type="button"
                onClick={() => switchRole('admin')}
                className={`min-w-0 flex-1 truncate rounded-md px-3 py-1.5 font-semibold transition ${isAdminView ? 'bg-white text-blue-800 shadow-sm' : 'text-white/85 hover:bg-white/10 hover:text-white'}`}
              >
                Администратор
              </button>
            </div>
          ) : null}
          <div className="mt-3 flex items-center gap-3 sm:mt-4">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-blue-100">
              Шаг {safeStep + 1} из {totalSteps}
            </span>
            <div className="h-1 flex-1 overflow-hidden rounded-full bg-white/15">
              <div
                className="h-full rounded-full bg-white transition-all duration-300 ease-out"
                style={{ width: progressWidth }}
              />
            </div>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain bg-slate-50 px-3 py-4 sm:px-7 sm:py-7">
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-7">
            <div className="flex items-start gap-4 sm:gap-5">
              <div className="hidden h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-blue-50 text-blue-700 sm:flex sm:h-16 sm:w-16">
                <StepIcon size={28} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50 text-blue-700 sm:hidden">
                    <StepIcon size={18} />
                  </div>
                  <span className="inline-flex items-center rounded-full bg-blue-100 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-blue-800">
                    Шаг {currentStep + 1}
                  </span>
                </div>
                <h3 className="mt-2 text-lg font-semibold leading-tight text-slate-950 sm:text-xl">
                  {step.title}
                </h3>
                <p className="mt-3 text-sm leading-7 text-slate-700 sm:text-base sm:leading-8">
                  {step.body}
                </p>
                {step.visual ? (
                  <div className="mt-4 overflow-hidden rounded-xl border border-dashed border-slate-300 bg-gradient-to-br from-slate-50 to-white">
                    <div className="flex items-center gap-1.5 border-b border-dashed border-slate-200 px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                      <MousePointerClick size={13} /> Как это выглядит
                    </div>
                    <div className="px-4 py-4 sm:px-5">
                      {step.visual}
                    </div>
                  </div>
                ) : null}
                {step.example ? (
                  <div className="mt-4 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-amber-200/70 text-amber-900">
                      <Sparkles size={15} />
                    </div>
                    <div className="min-w-0 text-sm leading-6 text-amber-900">
                      <div className="text-[11px] font-semibold uppercase tracking-wider">Пример</div>
                      <div className="mt-0.5">{step.example}</div>
                    </div>
                  </div>
                ) : null}
                {Array.isArray(step.nuances) && step.nuances.length ? (
                  <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
                    <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-slate-600">
                      <Info size={13} /> Важно
                    </div>
                    <ul className="mt-2 space-y-2">
                      {step.nuances.map((nuance) => (
                        <li key={nuance} className="flex items-start gap-2.5 text-sm leading-6 text-slate-700">
                          <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-600" />
                          <span>{nuance}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </div>

        <div className="shrink-0 flex flex-col gap-2 border-t border-slate-200 bg-white px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-3 sm:px-7 sm:py-4">
          <div className="order-2 flex flex-wrap items-center justify-center gap-1.5 sm:order-1 sm:flex-nowrap sm:justify-start">
            {steps.map((s, index) => {
              const isActive = index === safeStep;
              const isPassed = index < safeStep;
              return (
                <button
                  key={s.title}
                  type="button"
                  onClick={() => setCurrentStep(index)}
                  aria-label={`Шаг ${index + 1}: ${s.title}`}
                  aria-current={isActive ? 'true' : undefined}
                  className={`h-2 rounded-full transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 ${
                    isActive
                      ? 'w-6 bg-blue-700'
                      : isPassed
                        ? 'w-2 bg-blue-300 hover:bg-blue-400'
                        : 'w-2 bg-slate-300 hover:bg-slate-400'
                  }`}
                />
              );
            })}
          </div>
          <div className="order-1 flex items-center justify-between gap-2 sm:order-2 sm:justify-end">
            <button
              type="button"
              onClick={() => setCurrentStep((step) => Math.max(step - 1, 0))}
              disabled={isFirst}
              className="inline-flex h-10 flex-1 items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 sm:flex-none sm:px-4"
            >
              <ChevronLeft size={16} />
              Назад
            </button>
            {isLast ? (
              <button
                type="button"
                onClick={onClose}
                className="inline-flex h-10 flex-1 items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-emerald-700 sm:flex-none"
              >
                <CheckCircle2 size={16} />
                Готово
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setCurrentStep((step) => Math.min(step + 1, totalSteps - 1))}
                className="inline-flex h-10 flex-1 items-center justify-center gap-1.5 rounded-lg bg-blue-700 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-800 sm:flex-none"
              >
                Далее
                <ChevronRight size={16} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

const SHIFTS_TABLE_DAY_LABELS = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];
const SHIFTS_TABLE_TIME_FORMATTER = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit' });

const formatShiftsTableDateHeader = (dateText) => {
  if (!dateText) return '';
  const [year, month, day] = String(dateText).split('-').map(Number);
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return dateText;
  const date = new Date(Date.UTC(year, month - 1, day));
  const weekday = SHIFTS_TABLE_DAY_LABELS[(date.getUTCDay() + 6) % 7];
  return `${weekday} ${SHIFTS_TABLE_TIME_FORMATTER.format(date).replace(/\./g, '.')}`;
};

const parseHHMMToMinutes = (text) => {
  const raw = String(text || '').trim();
  if (!raw) return null;
  const match = raw.match(/^(\d{1,2}):(\d{2})/);
  if (!match) return null;
  const hh = Number(match[1]);
  const mm = Number(match[2]);
  if (!Number.isFinite(hh) || !Number.isFinite(mm)) return null;
  return hh * 60 + mm;
};

const lotMinuteRange = (lot) => {
  const sStart = Number(lot?.source_start_minute);
  const sEnd = Number(lot?.source_end_minute);
  if (Number.isFinite(sStart) && Number.isFinite(sEnd) && sEnd > sStart) {
    return [sStart, sEnd];
  }
  const start = parseHHMMToMinutes(lot?.start_time);
  let end = parseHHMMToMinutes(lot?.end_time);
  if (start == null || end == null) return null;
  if (end <= start) end += 1440;
  return [start, end];
};

const lotsOverlap = (a, b) => {
  const ra = lotMinuteRange(a);
  const rb = lotMinuteRange(b);
  if (!ra || !rb) return false;
  return ra[0] < rb[1] && rb[0] < ra[1];
};

const minutesToClockLabel = (minutes) => formatAuctionBreakMinute(minutes);

const rangesOverlap = (left, right) => left[0] < right[1] && right[0] < left[1];

// iOS/macOS system colors, used to tint each operator's claimed segment in the
// admin day-details modal.
const ADMIN_DAY_SEGMENT_COLORS = ['#0A84FF', '#30D158', '#FF9F0A', '#BF5AF2', '#FF375F', '#5AC8FA', '#FFD60A', '#64D2FF'];

// Build the per-shift breakdown (claimed slices + free remainder) for the lots that
// make up ONE original shift, so the admin can see who took which part of it.
const buildAuctionShiftSegments = (lots) => {
  const segments = [];
  (lots || []).forEach((lot) => {
    if (!lot) return;
    const range = lotMinuteRange(lot);
    if (!range) return;
    const claimSegs = Array.isArray(lot.claim_segments) ? lot.claim_segments : [];
    if (claimSegs.length) {
      // Single-lot model: expand the taken parts (per operator) + free remainder.
      const busy = [];
      claimSegs.forEach((seg) => {
        const r = getClockRangeWithinSource(seg.start_time, seg.end_time, range);
        if (!r) return;
        busy.push(r);
        segments.push({
          start: r[0],
          end: r[1],
          claimed: true,
          operatorId: seg.claimed_by != null ? Number(seg.claimed_by) : null,
          operatorName: seg.claimed_by_name || (seg.claimed_by ? `#${seg.claimed_by}` : ''),
          netMinutes: Math.max(0, r[1] - r[0]),
        });
      });
      subtractBusyRanges(range, busy).available.forEach((s) => {
        segments.push({ start: s.start, end: s.end, claimed: false, operatorId: null, operatorName: '', netMinutes: Math.max(0, s.end - s.start) });
      });
    } else if (lot.status === 'claimed') {
      const eff = getAuctionLotEffectiveMinuteRange(lot) || range;
      segments.push({
        start: eff[0],
        end: eff[1],
        claimed: true,
        operatorId: lot.claimed_by != null ? Number(lot.claimed_by) : null,
        operatorName: lot.claimed_by_name || (lot.claimed_by ? `#${lot.claimed_by}` : ''),
        netMinutes: getAuctionLotNetMinutes(lot),
      });
    } else if (lot.status === 'available') {
      segments.push({ start: range[0], end: range[1], claimed: false, operatorId: null, operatorName: '', netMinutes: Math.max(0, range[1] - range[0]) });
    }
  });
  if (!segments.length) return null;
  segments.sort((a, b) => a.start - b.start || a.end - b.end);
  const spanStart = Math.min(...segments.map((s) => s.start));
  const spanEnd = Math.max(...segments.map((s) => s.end));
  const opColor = new Map();
  segments.filter((s) => s.claimed).forEach((s) => {
    const id = s.operatorId ?? `_${opColor.size}`;
    if (!opColor.has(id)) opColor.set(id, opColor.size % ADMIN_DAY_SEGMENT_COLORS.length);
  });
  segments.forEach((s) => { s.colorIdx = s.claimed ? (opColor.get(s.operatorId ?? '') ?? 0) : -1; });
  const freeMinutes = segments
    .filter((s) => !s.claimed)
    .reduce((sum, s) => sum + Math.max(0, s.end - s.start), 0);
  return {
    segments,
    spanStart,
    spanEnd,
    span: Math.max(1, spanEnd - spanStart),
    claimedCount: segments.filter((s) => s.claimed).length,
    operatorCount: opColor.size,
    freeMinutes,
  };
};

// --- Post-auction claim (добор) helpers -------------------------------------
// A post-auction claim is "partial" when the operator took only a slice of the
// original shift window (claim range ≠ full lot range). Used to surface partial
// доборы to admins in the monitoring views.
const getPartialClaimMinute = (value) => parseHHMMToMinutes(String(value || '').slice(0, 5));

const isPartialPostAuctionClaim = (lot) => {
  if (!lot || !lot.post_auction_claimed) return false;
  const claimStart = getAuctionLotClaimStartTime(lot);
  const claimEnd = getAuctionLotClaimEndTime(lot);
  if (!claimStart || !claimEnd) return false;
  const claimStartMin = getPartialClaimMinute(claimStart);
  const claimEndMin = getPartialClaimMinute(claimEnd);
  const fullStartMin = getPartialClaimMinute(lot.start_time);
  const fullEndMin = getPartialClaimMinute(lot.end_time);
  if ([claimStartMin, claimEndMin, fullStartMin, fullEndMin].some((value) => value == null)) return false;
  return claimStartMin !== fullStartMin || claimEndMin !== fullEndMin;
};

// Tooltip suffix for cells that already build a `title` string.
const formatPostAuctionClaimTitleSuffix = (lot) => {
  if (!lot || !lot.post_auction_claimed) return '';
  if (isPartialPostAuctionClaim(lot)) {
    return ` · добор: взято ${formatAuctionLotEffectiveTimeRangeLabel(lot)} из ${formatAuctionShiftLabel(lot)}`;
  }
  return ' · добор после аукциона';
};

const PostAuctionClaimBadge = ({ lot, withOriginal = false, className = '' }) => {
  if (!lot || !lot.post_auction_claimed) return null;
  const partial = isPartialPostAuctionClaim(lot);
  const title = partial
    ? `Частичный добор: оператор взял ${formatAuctionLotEffectiveTimeRangeLabel(lot)} из смены ${formatAuctionShiftLabel(lot)}`
    : 'Смена взята после аукциона (добор)';
  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold leading-none ${
        partial ? 'bg-orange-100 text-orange-700' : 'bg-amber-50 text-amber-700'
      } ${className}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {partial ? 'добор · часть' : 'добор'}
      {withOriginal && partial ? (
        <span className="font-normal opacity-80">из {formatAuctionShiftLabel(lot)}</span>
      ) : null}
    </span>
  );
};

const subtractBusyRanges = (sourceRange, busyRanges) => {
  let available = [{ start: sourceRange[0], end: sourceRange[1] }];
  const occupied = [];

  busyRanges
    .map((range) => [
      Math.max(sourceRange[0], Number(range?.[0])),
      Math.min(sourceRange[1], Number(range?.[1]))
    ])
    .filter((range) => Number.isFinite(range[0]) && Number.isFinite(range[1]) && range[1] > range[0])
    .sort((a, b) => a[0] - b[0] || a[1] - b[1])
    .forEach((busy) => {
      occupied.push({ start: busy[0], end: busy[1] });
      const nextAvailable = [];
      available.forEach((segment) => {
        if (busy[1] <= segment.start || busy[0] >= segment.end) {
          nextAvailable.push(segment);
          return;
        }
        if (busy[0] > segment.start) nextAvailable.push({ start: segment.start, end: busy[0] });
        if (busy[1] < segment.end) nextAvailable.push({ start: busy[1], end: segment.end });
      });
      available = nextAvailable;
    });

  return {
    available: available.filter((segment) => segment.end - segment.start >= 15),
    occupied
  };
};

const buildPostAuctionClaimOption = (lot, workShifts = [], claimedLots = []) => {
  const sourceRange = lotMinuteRange(lot);
  if (!lot || !sourceRange) return null;

  const blockers = [
    ...(Array.isArray(workShifts) ? workShifts : []),
    ...((Array.isArray(workShifts) && workShifts.length) ? [] : (Array.isArray(claimedLots) ? claimedLots : []))
  ];
  const busyRanges = [
    ...blockers
      .filter((item) => item && item.shift_date === lot.shift_date)
      .map((item) => getClockRangeWithinSource(
        item.start_time || item.start,
        item.end_time || item.end,
        sourceRange
      ))
      .filter(Boolean)
      .filter((range) => rangesOverlap(sourceRange, range)),
    // Parts of THIS shift already taken by other operators (single-lot model):
    // subtract them so only the free part is offered.
    ...(Array.isArray(lot.claim_segments) ? lot.claim_segments : [])
      .map((seg) => getClockRangeWithinSource(seg.start_time, seg.end_time, sourceRange))
      .filter(Boolean)
      .filter((range) => rangesOverlap(sourceRange, range)),
  ];

  const split = subtractBusyRanges(sourceRange, busyRanges);
  const availableSegments = split.available.map((segment) => ({
    ...segment,
    start_time: minutesToClockLabel(segment.start),
    end_time: minutesToClockLabel(segment.end),
    minutes: segment.end - segment.start,
    isFull: segment.start === sourceRange[0] && segment.end === sourceRange[1]
  }));
  const occupiedSegments = split.occupied.map((segment) => ({
    ...segment,
    start_time: minutesToClockLabel(segment.start),
    end_time: minutesToClockLabel(segment.end),
    minutes: segment.end - segment.start
  }));
  const recommendedSegment = [...availableSegments].sort((a, b) => b.minutes - a.minutes || a.start - b.start)[0] || null;

  return {
    sourceStart: sourceRange[0],
    sourceEnd: sourceRange[1],
    sourceMinutes: sourceRange[1] - sourceRange[0],
    availableSegments,
    occupiedSegments,
    recommendedSegment,
    canClaim: Boolean(recommendedSegment),
    isPartial: Boolean(recommendedSegment && !recommendedSegment.isFull)
  };
};

const getSelectionMinuteRange = (lot, selection) => {
  const sourceRange = lotMinuteRange(lot);
  if (!sourceRange || !selection?.start_time || !selection?.end_time) return null;
  return getClockRangeWithinSource(selection.start_time, selection.end_time, sourceRange);
};

const isSelectionInsideAvailableSegments = (lot, selection, availableSegments = []) => {
  const range = getSelectionMinuteRange(lot, selection);
  if (!range || range[1] <= range[0]) return false;
  return availableSegments.some((segment) => range[0] >= segment.start && range[1] <= segment.end);
};

const PostAuctionPartialClaimModal = ({
  lot,
  option,
  selection,
  onSelectionChange,
  onClose,
  onConfirm,
  inProgress
}) => {
  if (!lot) return null;

  const safeOption = option || buildPostAuctionClaimOption(lot, [], []);
  const sourceStart = safeOption?.sourceStart ?? 0;
  const sourceEnd = safeOption?.sourceEnd ?? sourceStart + Math.max(1, getAuctionLotDurationMinutes(lot));
  const sourceMinutes = Math.max(1, sourceEnd - sourceStart);
  const selectedRange = getSelectionMinuteRange(lot, selection);
  const selectedMinutes = selectedRange ? Math.max(0, selectedRange[1] - selectedRange[0]) : 0;
  const isValid = Boolean(safeOption?.canClaim && isSelectionInsideAvailableSegments(lot, selection, safeOption.availableSegments));
  const sourceLabel = `${formatAuctionShiftLabel(lot)} · ${formatAuctionHours(getAuctionLotDurationMinutes({ ...lot, post_auction_claimed: false }))} ч`;
  const selectedLabel = selectedMinutes > 0
    ? `${selection.start_time}–${selection.end_time} · ${formatAuctionHours(selectedMinutes)} ч`
    : 'Интервал не выбран';
  const selectedIsPartial = selectedRange && (selectedRange[0] !== sourceStart || selectedRange[1] !== sourceEnd);
  const segmentStyle = (segment) => ({
    left: `${clampNumber(((segment.start - sourceStart) / sourceMinutes) * 100, 0, 100)}%`,
    width: `${clampNumber(((segment.end - segment.start) / sourceMinutes) * 100, 0, 100)}%`
  });

  const applySegment = (segment) => {
    if (!segment) return;
    onSelectionChange({ start_time: segment.start_time, end_time: segment.end_time });
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-900/50 px-4 py-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="post-claim-confirm-title"
      onClick={() => !inProgress && onClose()}
    >
      <div
        className="max-h-[88vh] w-full max-w-2xl overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 bg-slate-50 px-4 py-3 sm:px-5 sm:py-4">
          <div className="min-w-0">
            <h3 id="post-claim-confirm-title" className="text-base font-semibold text-slate-950 sm:text-lg">
              Забрать дополнительную смену
            </h3>
            <div className="mt-0.5 text-xs text-slate-500 sm:text-sm">
              {formatDateLabel(lot.shift_date)} · исходная смена {sourceLabel}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={inProgress}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-500 transition hover:bg-white hover:text-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
            title="Закрыть"
          >
            <X size={16} />
          </button>
        </div>

        <div className="max-h-[calc(88vh-132px)] overflow-y-auto px-4 py-4 sm:px-5">
          <div className="rounded-lg border border-slate-200 bg-white p-3 sm:p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="text-sm font-semibold text-slate-900">Таймлайн смены</div>
              <div className="flex flex-wrap items-center gap-2 text-[11px] font-medium text-slate-600">
                <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm bg-orange-300" />Доступно</span>
                <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm bg-slate-300" />Ваша смена</span>
                <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm bg-blue-600" />Выбрано</span>
              </div>
            </div>

            <div className="relative h-12 rounded-lg border border-slate-200 bg-slate-100">
              {(safeOption?.availableSegments || []).map((segment) => (
                <button
                  key={`available-${segment.start}-${segment.end}`}
                  type="button"
                  onClick={() => applySegment(segment)}
                  className="absolute top-1 h-10 rounded-md bg-orange-300/80 ring-1 ring-orange-400 transition hover:bg-orange-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500"
                  style={segmentStyle(segment)}
                  title={`Доступно ${segment.start_time}–${segment.end_time}`}
                />
              ))}
              {(safeOption?.occupiedSegments || []).map((segment) => (
                <div
                  key={`occupied-${segment.start}-${segment.end}`}
                  className="absolute top-1 h-10 rounded-md border border-slate-300 bg-slate-300"
                  style={{
                    ...segmentStyle(segment),
                    backgroundImage: 'repeating-linear-gradient(45deg, rgba(100,116,139,.42) 0, rgba(100,116,139,.42) 4px, rgba(203,213,225,.9) 4px, rgba(203,213,225,.9) 8px)'
                  }}
                  title={`Занято ${segment.start_time}–${segment.end_time}`}
                />
              ))}
              {selectedRange ? (
                <div
                  className="pointer-events-none absolute top-1 h-10 rounded-md bg-blue-600 shadow-sm ring-2 ring-white"
                  style={segmentStyle({ start: selectedRange[0], end: selectedRange[1] })}
                  title={`Выбрано ${selection.start_time}–${selection.end_time}`}
                />
              ) : null}
            </div>
            <div className="mt-2 flex justify-between text-[11px] font-semibold text-slate-500 tabular-nums">
              <span>{minutesToClockLabel(sourceStart)}</span>
              <span>{minutesToClockLabel(sourceEnd)}</span>
            </div>
          </div>

          {(safeOption?.availableSegments || []).length ? (
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {safeOption.availableSegments.map((segment) => {
                const active = selection?.start_time === segment.start_time && selection?.end_time === segment.end_time;
                return (
                  <button
                    key={`segment-${segment.start}-${segment.end}`}
                    type="button"
                    onClick={() => applySegment(segment)}
                    disabled={inProgress}
                    className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left text-sm transition disabled:cursor-not-allowed disabled:opacity-60 ${
                      active
                        ? 'border-blue-300 bg-blue-50 text-blue-900'
                        : 'border-slate-200 bg-white text-slate-700 hover:border-orange-300 hover:bg-orange-50'
                    }`}
                  >
                    <span className="font-semibold tabular-nums">{segment.start_time}–{segment.end_time}</span>
                    <span className="text-xs font-semibold tabular-nums">{formatAuctionHours(segment.minutes)} ч</span>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-600">
              Для этой смены нет свободного интервала без пересечения с вашим графиком.
            </div>
          )}

          <div className="mt-4 grid gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end sm:p-4">
            <label className="block text-xs font-semibold text-slate-700">
              Начало
              <input
                type="time"
                value={selection?.start_time || ''}
                step="300"
                onChange={(event) => onSelectionChange({ ...selection, start_time: event.target.value })}
                disabled={inProgress}
                className="mt-1 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100 disabled:cursor-not-allowed disabled:opacity-60"
              />
            </label>
            <label className="block text-xs font-semibold text-slate-700">
              Конец
              <input
                type="time"
                value={selection?.end_time || ''}
                step="300"
                onChange={(event) => onSelectionChange({ ...selection, end_time: event.target.value })}
                disabled={inProgress}
                className="mt-1 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100 disabled:cursor-not-allowed disabled:opacity-60"
              />
            </label>
            <div className={`rounded-md px-3 py-2 text-xs font-semibold tabular-nums ${isValid ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>
              {isValid ? selectedLabel : 'Интервал пересекается'}
            </div>
          </div>

          <p className="mt-3 text-xs leading-5 text-slate-600">
            {selectedIsPartial
              ? 'Будет сохранена выбранная часть исходной смены. Если она стыкуется с вашей сменой, график объединится автоматически.'
              : 'Будет сохранена вся дополнительная смена. Если она стыкуется с вашей сменой, график объединится автоматически.'}
          </p>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-200 bg-white px-4 py-3 sm:px-5">
          <button
            type="button"
            onClick={onClose}
            disabled={inProgress}
            className="inline-flex h-9 items-center justify-center rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 sm:text-sm"
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={inProgress || !isValid}
            className="inline-flex h-9 items-center justify-center rounded-lg bg-orange-600 px-3 text-xs font-semibold text-white transition hover:bg-orange-700 disabled:cursor-not-allowed disabled:bg-orange-300 sm:text-sm"
          >
            {inProgress ? 'Забираю...' : 'Забрать'}
          </button>
        </div>
      </div>
    </div>
  );
};

const ShiftAuctionShiftsTable = ({
  operators = [],
  workloads = [],
  lots = [],
  lotDates = [],
  canEdit = false,
  apiRoot = '',
  buildHeaders = null,
  onActionComplete = null,
  notify = null
}) => {
  const workloadById = useMemo(() => {
    const map = new Map();
    (Array.isArray(workloads) ? workloads : []).forEach((w) => {
      if (w && w.operator_id != null) map.set(Number(w.operator_id), w);
    });
    return map;
  }, [workloads]);

  const [selectedCell, setSelectedCell] = useState(null); // { opId, date }
  const [pendingAction, setPendingAction] = useState(null); // { type, lot }
  const [actionLoading, setActionLoading] = useState(false);
  const [undoStack, setUndoStack] = useState([]);
  const [redoStack, setRedoStack] = useState([]);
  const HISTORY_LIMIT = 30;

  const lotsByOperatorDate = useMemo(() => {
    const map = new Map();
    const add = (opId, date, entry) => {
      if (!Number.isFinite(opId) || opId <= 0 || !date) return;
      const key = `${opId}|${date}`;
      const list = map.get(key) || [];
      list.push(entry);
      map.set(key, list);
    };
    (Array.isArray(lots) ? lots : []).forEach((lot) => {
      if (!lot) return;
      const date = lot.shift_date;
      if (!date) return;
      const segs = Array.isArray(lot.claim_segments) ? lot.claim_segments : [];
      if (segs.length) {
        // Single-lot model: each operator's taken part comes from claim_segments.
        segs.forEach((seg, i) => add(Number(seg.claimed_by), date, {
          id: `seg-${lot.id}-c${seg.claimed_by}-${i}`,
          shift_date: date,
          start_time: seg.start_time,
          end_time: seg.end_time,
          breaks: [],
          source_schedule_plan_id: lot.source_schedule_plan_id,
          source_schedule_shift_id: lot.source_schedule_shift_id,
          claimed_by: seg.claimed_by != null ? Number(seg.claimed_by) : null,
          claimed_by_name: seg.claimed_by_name,
          // carry claim times so undo (re-claim) and per-segment unclaim work
          claim_start_time: seg.start_time,
          claim_end_time: seg.end_time,
        }));
        return;
      }
      if (lot.status !== 'claimed') return;
      add(Number(lot.claimed_by), date, lot);
    });
    map.forEach((list) => {
      list.sort((a, b) => String(a?.start_time || '').localeCompare(String(b?.start_time || '')));
    });
    return map;
  }, [lots]);

  const availableLotsByDate = useMemo(() => {
    const map = new Map();
    const add = (date, entry) => {
      const list = map.get(date) || [];
      list.push(entry);
      map.set(date, list);
    };
    (Array.isArray(lots) ? lots : []).forEach((lot) => {
      if (!lot) return;
      const date = lot.shift_date;
      if (!date) return;
      const segs = Array.isArray(lot.claim_segments) ? lot.claim_segments : [];
      if (segs.length) {
        // Partially-taken shift: offer only the FREE part(s).
        const src = lotMinuteRange(lot);
        if (!src) return;
        const busy = segs.map((s) => getClockRangeWithinSource(s.start_time, s.end_time, src)).filter(Boolean);
        subtractBusyRanges(src, busy).available.forEach((gap, i) => add(date, {
          id: `free-${lot.id}-${i}`,
          shift_date: date,
          start_time: minutesToClockLabel(gap.start),
          end_time: minutesToClockLabel(gap.end),
          breaks: [],
          source_schedule_plan_id: lot.source_schedule_plan_id,
          source_schedule_shift_id: lot.source_schedule_shift_id,
          claim_start_time: minutesToClockLabel(gap.start),
          claim_end_time: minutesToClockLabel(gap.end),
        }));
        return;
      }
      if (lot.status !== 'available') return;
      add(date, lot);
    });
    map.forEach((list) => {
      list.sort((a, b) => String(a?.start_time || '').localeCompare(String(b?.start_time || '')));
    });
    return map;
  }, [lots]);

  const rows = useMemo(() => {
    const sortedOperators = [...(Array.isArray(operators) ? operators : [])].sort((a, b) => {
      const dirCmp = String(a?.direction || '').localeCompare(String(b?.direction || ''), 'ru');
      if (dirCmp !== 0) return dirCmp;
      return String(a?.name || '').localeCompare(String(b?.name || ''), 'ru');
    });
    return sortedOperators
      .filter((op) => op && op.id != null)
      .map((op) => {
        const opId = Number(op.id);
        const workload = workloadById.get(opId) || {};
        return { operator: op, opId, workload };
      });
  }, [operators, workloadById]);

  const getNormCellClass = (claimedMinutes, normMinutes) => {
    if (!normMinutes || normMinutes <= 0) return 'bg-slate-100 text-slate-500';
    const pct = (claimedMinutes / normMinutes) * 100;
    if (pct >= 100) return 'bg-emerald-100 text-emerald-900';
    if (pct >= 80) return 'bg-amber-100 text-amber-900';
    return 'bg-orange-100 text-orange-900';
  };

  const formatHours = (minutes) => {
    const m = Math.max(0, Number(minutes) || 0);
    const hours = m / 60;
    return Number.isInteger(hours) ? `${hours}` : hours.toFixed(1);
  };

  const dates = Array.isArray(lotDates) ? lotDates : [];

  const callAdminApi = useCallback(async (endpoint, body) => {
    if (!apiRoot) throw new Error('No API root');
    const headers = typeof buildHeaders === 'function' ? buildHeaders() : {};
    headers['Content-Type'] = 'application/json';
    const response = await fetch(`${apiRoot}${endpoint}`, {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify(body || {})
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = new Error(payload?.error || `HTTP ${response.status}`);
      err.code = payload?.code;
      throw err;
    }
    return payload;
  }, [apiRoot, buildHeaders]);

  const lotApiBody = (lot, extra = {}) => {
    const body = { ...extra };
    if (Number.isFinite(Number(lot.id)) && !String(lot.id).startsWith('preview-')) {
      body.lot_id = Number(lot.id);
    } else {
      body.plan_id = lot.source_schedule_plan_id;
      body.source_schedule_shift_id = lot.source_schedule_shift_id;
      // Target this operator's specific partial claim (a shift may now have several).
      if (lot.claimed_by != null) body.claimed_by = Number(lot.claimed_by);
      // A free-part entry carries the exact slice to assign/unclaim.
      if (lot.claim_start_time && lot.claim_end_time) {
        body.claim_start_time = lot.claim_start_time;
        body.claim_end_time = lot.claim_end_time;
      }
    }
    return body;
  };

  const pushHistory = useCallback((entry) => {
    setUndoStack((prev) => [...prev.slice(-HISTORY_LIMIT + 1), entry]);
    setRedoStack([]);
  }, []);

  const callUnclaim = useCallback(async (lot) => {
    await callAdminApi('/api/shift_auction/admin/unclaim_shift', lotApiBody(lot));
  }, [callAdminApi]);

  const callClaim = useCallback(async (lot, operatorId) => {
    await callAdminApi('/api/shift_auction/admin/claim_shift_for_operator', lotApiBody(lot, { operator_id: operatorId }));
  }, [callAdminApi]);

  const handleUnclaim = useCallback(async (lot) => {
    if (!lot) return;
    const operatorId = Number(lot.claimed_by);
    setActionLoading(true);
    try {
      await callUnclaim(lot);
      pushHistory({ type: 'unclaim', lot: { ...lot }, operatorId });
      if (typeof notify === 'function') notify('Смена снята с оператора');
      setPendingAction(null);
      if (typeof onActionComplete === 'function') await onActionComplete();
    } catch (error) {
      if (typeof notify === 'function') notify(error?.message || 'Не удалось убрать смену', 'error');
    } finally {
      setActionLoading(false);
    }
  }, [callUnclaim, pushHistory, notify, onActionComplete]);

  const handleClaim = useCallback(async (lot, operatorId) => {
    if (!lot || !operatorId) return;
    setActionLoading(true);
    try {
      await callClaim(lot, operatorId);
      // Keep claimed_by so undo (unclaim) targets THIS operator's part precisely.
      pushHistory({ type: 'claim', lot: { ...lot, claimed_by: Number(operatorId) }, operatorId: Number(operatorId) });
      if (typeof notify === 'function') notify('Смена назначена оператору');
      setPendingAction(null);
      if (typeof onActionComplete === 'function') await onActionComplete();
    } catch (error) {
      if (typeof notify === 'function') notify(error?.message || 'Не удалось назначить смену', 'error');
    } finally {
      setActionLoading(false);
    }
  }, [callClaim, pushHistory, notify, onActionComplete]);

  const performUndo = useCallback(async () => {
    if (!canEdit || actionLoading) return;
    const last = undoStack[undoStack.length - 1];
    if (!last) return;
    setActionLoading(true);
    try {
      if (last.type === 'unclaim') {
        await callClaim(last.lot, last.operatorId);
      } else {
        await callUnclaim(last.lot);
      }
      setUndoStack((prev) => prev.slice(0, -1));
      setRedoStack((prev) => [...prev.slice(-HISTORY_LIMIT + 1), last]);
      if (typeof notify === 'function') notify('Действие отменено');
      if (typeof onActionComplete === 'function') await onActionComplete();
    } catch (error) {
      if (typeof notify === 'function') notify(error?.message || 'Не удалось отменить действие', 'error');
    } finally {
      setActionLoading(false);
    }
  }, [canEdit, actionLoading, undoStack, callClaim, callUnclaim, notify, onActionComplete]);

  const performRedo = useCallback(async () => {
    if (!canEdit || actionLoading) return;
    const last = redoStack[redoStack.length - 1];
    if (!last) return;
    setActionLoading(true);
    try {
      if (last.type === 'unclaim') {
        await callUnclaim(last.lot);
      } else {
        await callClaim(last.lot, last.operatorId);
      }
      setRedoStack((prev) => prev.slice(0, -1));
      setUndoStack((prev) => [...prev.slice(-HISTORY_LIMIT + 1), last]);
      if (typeof notify === 'function') notify('Действие повторено');
      if (typeof onActionComplete === 'function') await onActionComplete();
    } catch (error) {
      if (typeof notify === 'function') notify(error?.message || 'Не удалось повторить действие', 'error');
    } finally {
      setActionLoading(false);
    }
  }, [canEdit, actionLoading, redoStack, callClaim, callUnclaim, notify, onActionComplete]);

  useEffect(() => {
    if (!canEdit) return undefined;
    const handler = (event) => {
      const meta = event.ctrlKey || event.metaKey;
      if (!meta) return;
      const code = String(event.code || '');
      const keyLower = String(event.key || '').toLowerCase();
      const isZ = code === 'KeyZ' || keyLower === 'z' || keyLower === 'я';
      const isY = code === 'KeyY' || keyLower === 'y' || keyLower === 'н';
      if (isZ) {
        event.preventDefault();
        if (event.shiftKey) performRedo();
        else performUndo();
      } else if (isY) {
        event.preventDefault();
        performRedo();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [canEdit, performUndo, performRedo]);

  const cellModalData = useMemo(() => {
    if (!selectedCell) return null;
    const { opId, date } = selectedCell;
    const operator = (Array.isArray(operators) ? operators : []).find((op) => Number(op?.id) === Number(opId)) || null;
    if (!operator) return null;
    const workload = workloadById.get(Number(opId)) || {};
    const isDayOff = Array.isArray(workload?.day_off_dates) && workload.day_off_dates.includes(date);
    const claimed = lotsByOperatorDate.get(`${opId}|${date}`) || [];
    const dayAvailable = availableLotsByDate.get(date) || [];
    const compatible = dayAvailable.filter((lot) => !claimed.some((c) => lotsOverlap(lot, c)));
    return { operator, workload, date, claimed, dayAvailable, compatible, isDayOff };
  }, [selectedCell, operators, workloadById, lotsByOperatorDate, availableLotsByDate]);

  if (!rows.length || !dates.length) {
    return (
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-base font-semibold text-slate-950 sm:text-lg">Таблица смен</h2>
        <p className="mt-2 text-sm text-slate-500">
          {!dates.length ? 'Нет смен в выбранной неделе.' : 'Нет операторов-участников.'}
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-200 px-3 py-3 sm:px-5 sm:py-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-slate-950 sm:text-lg">Таблица смен</h2>
            <p className="mt-0.5 text-xs text-slate-600 sm:text-sm">
              Распределение смен по операторам недели. Подсветка нормы:
              <span className="ml-1 inline-flex items-center rounded border border-emerald-300 bg-emerald-50 px-1.5 text-[10px] font-semibold text-emerald-800">100%+</span>
              <span className="ml-1 inline-flex items-center rounded border border-amber-300 bg-amber-50 px-1.5 text-[10px] font-semibold text-amber-800">80–99%</span>
              <span className="ml-1 inline-flex items-center rounded border border-orange-300 bg-orange-50 px-1.5 text-[10px] font-semibold text-orange-800">&lt;80%</span>
            </p>
          </div>
          {canEdit ? (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={performUndo}
                disabled={!undoStack.length || actionLoading}
                title="Отменить (Ctrl/Cmd + Z)"
                className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-300"
              >
                <Undo2 size={13} strokeWidth={2.5} />
                <span>Отменить</span>
                {undoStack.length > 0 ? (
                  <span className="ml-0.5 rounded-full bg-slate-100 px-1.5 text-[10px] tabular-nums">{undoStack.length}</span>
                ) : null}
                <span className="hidden text-[10px] font-medium text-slate-400 sm:inline">⌘Z</span>
              </button>
              <button
                type="button"
                onClick={performRedo}
                disabled={!redoStack.length || actionLoading}
                title="Повторить (Ctrl/Cmd + Y или Ctrl/Cmd + Shift + Z)"
                className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-300"
              >
                <Redo2 size={13} strokeWidth={2.5} />
                <span>Повтор</span>
                {redoStack.length > 0 ? (
                  <span className="ml-0.5 rounded-full bg-slate-100 px-1.5 text-[10px] tabular-nums">{redoStack.length}</span>
                ) : null}
                <span className="hidden text-[10px] font-medium text-slate-400 sm:inline">⌘Y</span>
              </button>
            </div>
          ) : null}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[960px] border-collapse text-xs sm:text-sm">
          <thead className="bg-slate-100 text-slate-700">
            <tr>
              <th className="sticky left-0 z-10 border-b border-r border-slate-300 bg-slate-100 px-3 py-2 text-left font-semibold">ФИО</th>
              <th className="border-b border-r border-slate-300 bg-slate-100 px-3 py-2 text-center font-semibold">Ставка</th>
              <th className="border-b border-r-2 border-slate-300 bg-slate-100 px-3 py-2 text-center font-semibold">Норма</th>
              {dates.map((date, idx) => (
                <th
                  key={`shifts-th-${date}`}
                  className={`border-b border-slate-200 px-2 py-2 text-center font-semibold ${idx > 0 ? 'border-l border-slate-200' : ''}`}
                >
                  {formatShiftsTableDateHeader(date)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ operator, opId, workload }) => {
              const claimedNet = Number(workload?.claimed_net_minutes || 0);
              const norm = Number(workload?.norm_minutes || 0);
              const pct = norm > 0 ? Math.round((claimedNet / norm) * 100) : 0;
              const normCellClass = getNormCellClass(claimedNet, norm);
              return (
                <tr key={`shifts-row-${opId}`} className="border-b border-slate-200">
                  <td className="sticky left-0 z-10 border-r border-slate-200 bg-slate-50 px-3 py-2 align-middle">
                    <div className="font-medium text-slate-900">{operator?.name || `Оператор #${opId}`}</div>
                    <div className="text-[11px] text-slate-500">{operator?.direction || ''}</div>
                  </td>
                  <td className="border-r border-slate-200 bg-slate-50 px-3 py-2 text-center align-middle tabular-nums text-slate-700">
                    {Number(operator?.rate ?? workload?.rate ?? 1).toFixed(2)}
                  </td>
                  <td className={`border-r-2 border-slate-300 px-3 py-2 text-center align-middle tabular-nums font-semibold ${normCellClass}`}>
                    <div className="leading-tight">{formatHours(claimedNet)} / {formatHours(norm)} ч</div>
                    <div className="text-[10px] font-bold opacity-70">{pct}%</div>
                  </td>
                  {dates.map((date, idx) => {
                    const cellLots = lotsByOperatorDate.get(`${opId}|${date}`) || [];
                    const isDayOff = Array.isArray(workload?.day_off_dates) && workload.day_off_dates.includes(date);
                    const interactive = canEdit;
                    return (
                      <td
                        key={`shifts-cell-${opId}-${date}`}
                        onClick={interactive ? () => setSelectedCell({ opId, date }) : undefined}
                        className={`px-2 py-2 align-top transition ${idx > 0 ? 'border-l border-slate-200' : ''} ${
                          interactive ? 'cursor-pointer hover:bg-slate-50' : ''
                        }`}
                      >
                        <div className="flex flex-col gap-1">
                          {isDayOff ? (
                            <span
                              className="inline-flex items-center justify-center rounded border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-[11px] font-medium text-violet-800"
                              title="Оператор выбрал выходной"
                            >
                              Выходной
                            </span>
                          ) : null}
                          {cellLots.length === 0 && !isDayOff ? (
                            <span className="text-[11px] text-slate-300">—</span>
                          ) : (
                            cellLots.map((lot) => (
                              <span
                                key={`shifts-lot-${lot.id ?? `${lot.source_schedule_shift_id || ''}-${lot.start_time}-${lot.end_time}`}`}
                                className="inline-flex items-center justify-center rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-800 tabular-nums"
                                title={formatAuctionShiftLabel(lot)}
                              >
                                {formatAuctionLotEffectiveTimeRangeLabel(lot)}
                              </span>
                            ))
                          )}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {cellModalData ? (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-900/30 px-4 backdrop-blur-md"
          onClick={() => {
            if (actionLoading) return;
            setSelectedCell(null);
            setPendingAction(null);
          }}
          style={{ fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", system-ui, sans-serif' }}
        >
          <div
            className="w-full max-w-xl overflow-hidden rounded-3xl bg-slate-100 shadow-2xl ring-1 ring-slate-900/5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="relative flex items-center justify-between gap-3 border-b border-slate-200/70 bg-white/80 px-6 py-4 backdrop-blur-xl">
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold leading-tight text-slate-900">{cellModalData.operator?.name || 'Оператор'}</div>
                <div className="mt-0.5 text-[12px] text-slate-500">
                  {cellModalData.operator?.direction || ''}
                  {cellModalData.operator?.direction ? ' · ' : ''}
                  Ставка {Number(cellModalData.operator?.rate ?? 1).toFixed(2)} · {formatHours(cellModalData.workload?.claimed_net_minutes || 0)}/{formatHours(cellModalData.workload?.norm_minutes || 0)} ч
                </div>
              </div>
              <div className="flex flex-col items-end gap-1.5">
                <div className="rounded-full bg-slate-100 px-3 py-1 text-[12px] font-semibold text-slate-700">
                  {formatShiftsTableDateHeader(cellModalData.date)}
                </div>
                <button
                  type="button"
                  onClick={() => { if (!actionLoading) { setSelectedCell(null); setPendingAction(null); } }}
                  disabled={actionLoading}
                  className="text-[13px] font-medium text-blue-600 hover:text-blue-700 disabled:opacity-50"
                >
                  Готово
                </button>
              </div>
            </div>

            <div className="max-h-[65vh] space-y-5 overflow-y-auto px-4 py-5">
              {cellModalData.isDayOff ? (
                <section>
                  <div className="mb-2 px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Статус</div>
                  <div className="rounded-2xl bg-white px-3 py-3 ring-1 ring-slate-200/70">
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-[12.5px] font-semibold text-violet-800">
                      Оператор выбрал выходной
                    </span>
                  </div>
                </section>
              ) : null}

              <section>
                <div className="mb-2 px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Взятые смены</div>
                <div className="rounded-2xl bg-white px-3 py-3 ring-1 ring-slate-200/70">
                  {cellModalData.claimed.length === 0 ? (
                    <p className="px-1 py-2 text-[13px] text-slate-400">Нет смен на эту дату</p>
                  ) : (
                    <ul className="flex flex-wrap gap-2">
                      {cellModalData.claimed.map((lot) => {
                        const lotKey = `${lot.id ?? `${lot.source_schedule_shift_id || ''}-${lot.start_time}-${lot.end_time}`}`;
                        const isPending = pendingAction?.type === 'unclaim' && pendingAction?.lot === lot;
                        return (
                          <li
                            key={`claimed-${lotKey}`}
                            className={`flex items-center overflow-hidden rounded-full border transition-all ${
                              isPending
                                ? 'border-rose-200 bg-rose-50/70'
                                : 'border-blue-200/80 bg-blue-50/70'
                            }`}
                          >
                            <span className="px-3 py-1 text-[12.5px] font-semibold text-blue-900 tabular-nums">
                              {formatAuctionLotEffectiveTimeRangeLabel(lot)}
                            </span>
                            {canEdit ? (
                              isPending ? (
                                <>
                                  <button
                                    type="button"
                                    disabled={actionLoading}
                                    onClick={() => handleUnclaim(lot)}
                                    className="flex items-center gap-1 border-l border-rose-200 bg-rose-500 px-3 py-1 text-[12px] font-semibold text-white hover:bg-rose-600 disabled:opacity-50"
                                  >
                                    Убрать
                                  </button>
                                  <button
                                    type="button"
                                    disabled={actionLoading}
                                    onClick={() => setPendingAction(null)}
                                    className="border-l border-rose-200 px-2.5 py-1 text-[12px] font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-800"
                                  >
                                    Отмена
                                  </button>
                                </>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => setPendingAction({ type: 'unclaim', lot })}
                                  title="Убрать смену"
                                  className="flex h-7 w-7 items-center justify-center border-l border-blue-200/80 text-blue-700 transition hover:bg-rose-100 hover:text-rose-600"
                                >
                                  <X size={13} strokeWidth={2.5} />
                                </button>
                              )
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              </section>

              {canEdit ? (
                <>
                  <section>
                    <div className="mb-2 flex items-end justify-between px-3 text-[11px]">
                      <span className="font-semibold uppercase tracking-wider text-slate-500">Можно добавить</span>
                      <span className="font-semibold text-slate-400">{cellModalData.compatible.length}</span>
                    </div>
                    <div className="rounded-2xl bg-white px-3 py-3 ring-1 ring-slate-200/70">
                      {cellModalData.compatible.length === 0 ? (
                        <p className="px-1 py-2 text-[13px] text-slate-400">Нет совместимых смен</p>
                      ) : (
                        <ul className="flex flex-wrap gap-2">
                          {cellModalData.compatible.map((lot) => {
                            const lotKey = `${lot.id ?? `${lot.source_schedule_shift_id || ''}-${lot.start_time}-${lot.end_time}`}`;
                            const isPending = pendingAction?.type === 'claim' && pendingAction?.lot === lot;
                            return (
                              <li
                                key={`compat-${lotKey}`}
                                className={`flex items-center overflow-hidden rounded-full border transition-all ${
                                  isPending
                                    ? 'border-emerald-300 bg-emerald-50'
                                    : 'border-emerald-200/80 bg-emerald-50/70'
                                }`}
                              >
                                <span className="px-3 py-1 text-[12.5px] font-semibold text-emerald-900 tabular-nums">
                                  {formatAuctionLotEffectiveTimeRangeLabel(lot)}
                                </span>
                                {isPending ? (
                                  <>
                                    <button
                                      type="button"
                                      disabled={actionLoading}
                                      onClick={() => handleClaim(lot, cellModalData.operator.id)}
                                      className="border-l border-emerald-300 bg-emerald-500 px-3 py-1 text-[12px] font-semibold text-white hover:bg-emerald-600 disabled:opacity-50"
                                    >
                                      Добавить
                                    </button>
                                    <button
                                      type="button"
                                      disabled={actionLoading}
                                      onClick={() => setPendingAction(null)}
                                      className="border-l border-emerald-300 px-2.5 py-1 text-[12px] font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-800"
                                    >
                                      Отмена
                                    </button>
                                  </>
                                ) : (
                                  <button
                                    type="button"
                                    onClick={() => setPendingAction({ type: 'claim', lot })}
                                    title="Добавить оператору"
                                    className="flex h-7 w-7 items-center justify-center border-l border-emerald-200/80 text-emerald-700 transition hover:bg-emerald-100"
                                  >
                                    <Plus size={13} strokeWidth={2.5} />
                                  </button>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                  </section>

                  <section>
                    <div className="mb-2 flex items-end justify-between px-3 text-[11px]">
                      <span className="font-semibold uppercase tracking-wider text-slate-500">Все нераспределённые этого дня</span>
                      <span className="font-semibold text-slate-400">{cellModalData.dayAvailable.length}</span>
                    </div>
                    <div className="rounded-2xl bg-white px-3 py-3 ring-1 ring-slate-200/70">
                      {cellModalData.dayAvailable.length === 0 ? (
                        <p className="px-1 py-2 text-[13px] text-slate-400">Все смены распределены</p>
                      ) : (
                        <ul className="flex flex-wrap gap-2">
                          {cellModalData.dayAvailable.map((lot) => {
                            const lotKey = `${lot.id ?? `${lot.source_schedule_shift_id || ''}-${lot.start_time}-${lot.end_time}`}`;
                            const overlaps = !cellModalData.compatible.includes(lot);
                            return (
                              <li
                                key={`avail-${lotKey}`}
                                className={`inline-flex items-center rounded-full border px-3 py-1 text-[12.5px] font-semibold tabular-nums ${
                                  overlaps
                                    ? 'border-slate-200 bg-slate-100/70 text-slate-400 line-through decoration-slate-300'
                                    : 'border-emerald-200/80 bg-emerald-50/70 text-emerald-900'
                                }`}
                                title={overlaps ? 'Пересекается с уже взятой сменой' : 'Совместимо'}
                              >
                                {formatAuctionLotEffectiveTimeRangeLabel(lot)}
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                  </section>
                </>
              ) : (
                <section>
                  <div className="rounded-2xl bg-white px-4 py-4 text-[13px] text-slate-500 ring-1 ring-slate-200/70">
                    Только просмотр. Для управления сменами нужна роль администратора или супервайзера.
                  </div>
                </section>
              )}
            </div>

            {actionLoading ? (
              <div className="border-t border-slate-200/70 bg-white/80 px-6 py-2.5 text-center text-[12px] font-medium text-slate-500 backdrop-blur-xl">
                Сохраняем…
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
};

const ShiftAuctionView = ({ user, operators = [], apiBaseUrl, withAccessTokenHeader, showToast, onOpenResourceGeneration, initialPeriod = null, onInitialPeriodApplied = null }) => {
  const role = normalizeRole(user?.role);
  const canManage = isAdminLikeRole(role);
  const canMonitor = canManage || isSupervisorRole(role);
  const apiRoot = String(apiBaseUrl || '').replace(/\/+$/, '');
  const showToastRef = useRef(showToast);
  const streamAbortRef = useRef(null);
  const snapshotRequestRef = useRef(false);
  const lastEventIdRef = useRef(0);
  const lastLocallyPatchedEventIdRef = useRef(0);
  const lastAppliedSnapshotEventIdRef = useRef(0);
  const snapshotEtagRef = useRef('');
  const auctionLayoutRef = useRef(null);
  const auctionTableScrollRef = useRef(null);
  const auctionDateBarScrollRef = useRef(null);
  const auctionScrollSyncRef = useRef({ ignoredNode: null, ignoredLeft: 0 });
  const auctionMutationQueueRef = useRef(Promise.resolve());
  const monitorRefreshTimerRef = useRef(null);
  const snapshotRefreshPendingRef = useRef(false);
  const fetchSnapshotRef = useRef(null);

  const [settings, setSettings] = useState({
    enabled: false,
    launch_note: '',
    starts_at: null,
    ends_at: null,
    paused_at: null,
    finished_at: null,
    status: 'disabled',
    selected_operator_ids: [],
    selected_operators: [],
    selected_schedule_plan_id: null,
    selected_period: null,
    is_current_user_tester: false,
    published_to_work_schedules_at: null,
    published_to_work_schedules_by_name: '',
    topup_started_at: null,
    topup_started_by_name: '',
    post_auction_active: false,
    has_period_history_access: false
  });
  const [isTogglingTopup, setIsTogglingTopup] = useState(false);
  const [lots, setLots] = useState([]);
  const [myDayOffs, setMyDayOffs] = useState([]);
  const [myBlockedDates, setMyBlockedDates] = useState([]);
  const [myWorkShifts, setMyWorkShifts] = useState([]);
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
  const [isControllingAuction, setIsControllingAuction] = useState(false);
  const [isPublishingAuction, setIsPublishingAuction] = useState(false);
  const [isExportingAuctionReport, setIsExportingAuctionReport] = useState(false);
  const [claimingLotIds, setClaimingLotIds] = useState(() => new Set());
  const [releaseConfirmLot, setReleaseConfirmLot] = useState(null);
  const [releaseConfirmOptions, setReleaseConfirmOptions] = useState([]);
  const [releasingLotId, setReleasingLotId] = useState(null);
  const lotsRef = useRef([]);
  const pendingClaimLotIdsRef = useRef(new Set());
  const lastClaimErrorRef = useRef({ message: '', shownAt: 0 });
  const [isInstructionsOpen, setIsInstructionsOpen] = useState(false);
  const [dayOffLoadingDate, setDayOffLoadingDate] = useState('');
  const [connectionState, setConnectionState] = useState('idle');
  const [statusVersion, setStatusVersion] = useState(0);
  const [activeDayDate, setActiveDayDate] = useState('');
  const [isAdminDayDetailsOpen, setIsAdminDayDetailsOpen] = useState(false);
  const [auctionDayColumnPx, setAuctionDayColumnPx] = useState(64);
  const [availablePeriods, setAvailablePeriods] = useState([]);
  const [claimJournal, setClaimJournal] = useState([]);
  const [participantWorkloads, setParticipantWorkloads] = useState([]);
  const [operatorWorkloadFilter, setOperatorWorkloadFilter] = useState('all');
  const [operatorWorkloadQuery, setOperatorWorkloadQuery] = useState('');
  const [monitorTab, setMonitorTab] = useState('monitoring');
  const [drilldownOperatorId, setDrilldownOperatorId] = useState(null);
  const [shiftDetailLot, setShiftDetailLot] = useState(null);
  // Supervisor/admin "add a shift" modal (the "+" button under each rate group).
  const [addShiftTarget, setAddShiftTarget] = useState(null);
  const [addShiftStart, setAddShiftStart] = useState('09:00');
  const [isAddingShift, setIsAddingShift] = useState(false);
  const [journalEntries, setJournalEntries] = useState([]);
  const [journalPage, setJournalPage] = useState(1);
  const [journalPerPage] = useState(50);
  const [journalTotal, setJournalTotal] = useState(0);
  const [journalLoading, setJournalLoading] = useState(false);
  const [journalError, setJournalError] = useState('');
  const [postClaimConfirmLot, setPostClaimConfirmLot] = useState(null);
  const [postClaimSelection, setPostClaimSelection] = useState({ start_time: '', end_time: '' });
  const [postClaimingLotIds, setPostClaimingLotIds] = useState(() => new Set());
  const [notifyPostClaimEnabled, setNotifyPostClaimEnabled] = useState(false);
  const [isSavingNotifyToggle, setIsSavingNotifyToggle] = useState(false);
  const [postAuctionNowMs, setPostAuctionNowMs] = useState(() => Date.now());
  const [myClaimsOpen, setMyClaimsOpen] = useState(false);
  const [myClaims, setMyClaims] = useState([]);
  const [myClaimsLoading, setMyClaimsLoading] = useState(false);
  const [myClaimsError, setMyClaimsError] = useState('');
  const [myClaimsFetchedAt, setMyClaimsFetchedAt] = useState(0);
  const [cancelingClaimKey, setCancelingClaimKey] = useState('');
  const [claimsNowMs, setClaimsNowMs] = useState(() => Date.now());
  const [viewSchedulePlanId, setViewSchedulePlanId] = useState('');
  const [periodPreviewLots, setPeriodPreviewLots] = useState([]);
  const [periodPreviewBlockedDates, setPeriodPreviewBlockedDates] = useState([]);
  const [periodPreviewDayOffs, setPeriodPreviewDayOffs] = useState([]);
  const [periodPreviewWorkShifts, setPeriodPreviewWorkShifts] = useState([]);
  const [periodPreviewOperators, setPeriodPreviewOperators] = useState([]);
  const [periodPreviewParticipantWorkloads, setPeriodPreviewParticipantWorkloads] = useState([]);
  const [periodPreviewPostAuctionActive, setPeriodPreviewPostAuctionActive] = useState(false);
  const [periodPreviewLoading, setPeriodPreviewLoading] = useState(false);
  const [periodPreviewError, setPeriodPreviewError] = useState('');
  const [appliedInitialPeriodKey, setAppliedInitialPeriodKey] = useState('');

  useEffect(() => {
    showToastRef.current = showToast;
  }, [showToast]);

  useEffect(() => {
    lotsRef.current = lots;
  }, [lots]);

  const notifyClaimError = useCallback((message) => {
    if (!message) return;
    const now = Date.now();
    const ref = lastClaimErrorRef.current;
    if (ref.message === message && now - ref.shownAt < 3000) return;
    lastClaimErrorRef.current = { message, shownAt: now };
    if (typeof showToastRef.current === 'function') showToastRef.current(message, 'error');
  }, []);

  const instructionsRole = canMonitor ? 'admin' : 'operator';
  const canSwitchInstructionsRole = canMonitor;
  const instructionsStorageKey = user?.id
    ? `shift_auction_instructions_seen_${SHIFT_AUCTION_INSTRUCTIONS_VERSION}_${instructionsRole}_${user.id}`
    : null;

  useEffect(() => {
    if (!instructionsStorageKey || typeof window === 'undefined') return;
    try {
      if (window.localStorage.getItem(instructionsStorageKey)) return;
      setIsInstructionsOpen(true);
    } catch (_error) {
      setIsInstructionsOpen(true);
    }
  }, [instructionsStorageKey]);

  const closeInstructions = useCallback(() => {
    setIsInstructionsOpen(false);
    if (!instructionsStorageKey || typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(instructionsStorageKey, String(Date.now()));
    } catch (_error) {
      /* ignore quota / privacy mode errors */
    }
  }, [instructionsStorageKey]);

  useEffect(() => {
    if (!settings.enabled) return undefined;
    if (settings.paused_at || settings.finished_at) return undefined;
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
  }, [settings.enabled, settings.ends_at, settings.finished_at, settings.paused_at, settings.starts_at, statusVersion]);

  const notify = useCallback((message, type = 'success') => {
    if (typeof showToastRef.current === 'function') showToastRef.current(message, type);
  }, []);

  const enqueueAuctionMutation = useCallback((task) => {
    const runTask = () => Promise.resolve().then(task);
    const queuedTask = auctionMutationQueueRef.current.then(runTask, runTask);
    auctionMutationQueueRef.current = queuedTask.catch(() => undefined);
    return queuedTask;
  }, []);

  const buildHeaders = useCallback((extra = {}) => {
    const headers = { ...extra };
    if (user?.id) headers['X-User-Id'] = String(user.id);
    return typeof withAccessTokenHeader === 'function' ? withAccessTokenHeader(headers) : headers;
  }, [user?.id, withAccessTokenHeader]);

  const postClaimLot = useCallback(async (lotId) => {
    const response = await axios.post(
      `${apiRoot}/api/shift_auction/test_lots/claim`,
      { lot_id: lotId, action: 'claim' },
      { headers: buildHeaders() }
    );
    return { data: response?.data || {} };
  }, [apiRoot, buildHeaders]);

  const postAuctionClaimLotApi = useCallback(async (lotOrId, selection = {}) => {
    const lot = lotOrId && typeof lotOrId === 'object' ? lotOrId : null;
    const sourceShiftId = normalizeSchedulePlanId(lot?.source_schedule_shift_id);
    const sourcePlanId = normalizeSchedulePlanId(lot?.source_schedule_plan_id);
    const numericLotId = Number(lot ? lot.id : lotOrId);
    const payload = sourceShiftId && sourcePlanId && !Number.isFinite(numericLotId)
      ? { schedule_plan_id: sourcePlanId, source_schedule_shift_id: sourceShiftId }
      : { lot_id: lot ? lot.id : lotOrId };
    if (selection?.start_time && selection?.end_time) {
      payload.claim_start_time = selection.start_time;
      payload.claim_end_time = selection.end_time;
    }
    const response = await axios.post(
      `${apiRoot}/api/shift_auction/post_claim_lot`,
      payload,
      { headers: buildHeaders() }
    );
    return { data: response?.data || {} };
  }, [apiRoot, buildHeaders]);

  const applySnapshot = useCallback((snapshot) => {
    const safe = snapshot || {};
    // A snapshot is built server-side at some event id, but a slow query (e.g.
    // when the DB pool is busy) can make it arrive AFTER newer SSE patches have
    // already advanced the UI. Never let an older snapshot clobber realtime lot
    // state that SSE moved forward — that made shifts visibly "jump" backwards.
    const incomingEventId = Number(safe.last_event_id || 0);
    // Keep the stream cursor (all received events) separate from the local-patch
    // cursor. Admin/settings events intentionally require a snapshot; comparing
    // them to the stream cursor could reject the very snapshot meant to apply
    // them. Only state already patched locally must be protected from rollback.
    const protectedEventId = Math.max(
      lastLocallyPatchedEventIdRef.current,
      lastAppliedSnapshotEventIdRef.current
    );
    const isStaleRealtime = incomingEventId < protectedEventId;
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
      paused_at: safe.paused_at || null,
      finished_at: safe.finished_at || null,
      status: safe.status || 'disabled',
      selected_operator_ids: ids,
      selected_operators: Array.isArray(safe.selected_operators) ? safe.selected_operators : [],
      selected_schedule_plan_id: selectedSchedulePlanId,
      selected_period: safe.selected_period || null,
      is_current_user_tester: Boolean(safe.is_current_user_tester),
      updated_by_name: safe.updated_by_name || '',
      updated_at: safe.updated_at || null,
      published_to_work_schedules_at: safe.published_to_work_schedules_at || null,
      published_to_work_schedules_by_name: safe.published_to_work_schedules_by_name || '',
      topup_started_at: safe.topup_started_at || null,
      topup_started_by_name: safe.topup_started_by_name || '',
      has_period_history_access: Boolean(safe.has_period_history_access),
      post_auction_active: Boolean(safe.post_auction_active)
    });
    setNotifyPostClaimEnabled(Boolean(safe.notify_post_claim_enabled));
    if (!isStaleRealtime) {
      setLots(Array.isArray(safe.lots) ? safe.lots : []);
      setMyDayOffs(Array.isArray(safe.my_day_offs) ? safe.my_day_offs.filter(Boolean) : []);
      setMyBlockedDates(Array.isArray(safe.my_blocked_dates) ? safe.my_blocked_dates.filter((item) => (typeof item === 'string' ? item : item?.date)) : []);
      setMyWorkShifts(Array.isArray(safe.my_work_shifts) ? safe.my_work_shifts : []);
      setClaimJournal(Array.isArray(safe.claim_journal) ? safe.claim_journal : []);
      setParticipantWorkloads(Array.isArray(safe.participant_workloads) ? safe.participant_workloads : []);
      lastAppliedSnapshotEventIdRef.current = Math.max(lastAppliedSnapshotEventIdRef.current, incomingEventId);
      lastEventIdRef.current = Math.max(lastEventIdRef.current, incomingEventId);
      setLastEventId((current) => Math.max(current, incomingEventId));
    }
    setDraftEnabled(Boolean(safe.enabled));
    setDraftNote(safe.launch_note || '');
    setDraftStartsAt(toDateTimeInputValue(safe.starts_at));
    setDraftEndsAt(toDateTimeInputValue(safe.ends_at));
    setSelectedIds(new Set(ids));
    setAvailablePeriods(periods);
    setDraftSchedulePlanId((current) => {
      const restartablePeriods = periods.filter((period) => period?.can_restart !== false);
      const periodIds = new Set(restartablePeriods.map((period) => normalizeSchedulePlanId(period?.id)).filter(Boolean));
      const currentId = normalizeSchedulePlanId(current);
      if (currentId && periodIds.has(currentId)) return String(currentId);
      if (selectedSchedulePlanId && periodIds.has(selectedSchedulePlanId)) return String(selectedSchedulePlanId);
      const firstAvailableId = normalizeSchedulePlanId(restartablePeriods[0]?.id);
      return firstAvailableId ? String(firstAvailableId) : '';
    });
    setViewSchedulePlanId((current) => {
      const periodIds = new Set(periods.map((period) => normalizeSchedulePlanId(period?.id)).filter(Boolean));
      const currentId = normalizeSchedulePlanId(current);
      if (currentId && periodIds.has(currentId)) return String(currentId);
      if (selectedSchedulePlanId && periodIds.has(selectedSchedulePlanId)) return String(selectedSchedulePlanId);
      const firstRestartableId = normalizeSchedulePlanId(periods.find((period) => period?.can_restart !== false)?.id);
      if (firstRestartableId) return String(firstRestartableId);
      const firstAvailableId = normalizeSchedulePlanId(periods[0]?.id);
      return firstAvailableId ? String(firstAvailableId) : '';
    });
  }, []);

  const fetchJournalPage = useCallback(async (page = 1) => {
    if (!apiRoot || !user?.id) return;
    setJournalLoading(true);
    setJournalError('');
    try {
      const response = await axios.get(`${apiRoot}/api/shift_auction/test_journal`, {
        params: { page, per_page: journalPerPage },
        headers: buildHeaders()
      });
      const data = response?.data || {};
      setJournalEntries(Array.isArray(data.entries) ? data.entries : []);
      setJournalTotal(Number(data.total || 0));
      setJournalPage(Number(data.page || page));
    } catch (error) {
      const message = error?.response?.data?.error || 'Не удалось загрузить журнал аукциона';
      setJournalError(message);
    } finally {
      setJournalLoading(false);
    }
  }, [apiRoot, buildHeaders, journalPerPage, user?.id]);

  const fetchSnapshot = useCallback(async ({ silent = false } = {}) => {
    if (!apiRoot || !user?.id) return;
    if (snapshotRequestRef.current) {
      // Do not lose an event-triggered refresh just because another snapshot is
      // in flight. One trailing request is enough to converge to the newest state.
      snapshotRefreshPendingRef.current = true;
      return;
    }
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
      if (snapshotRefreshPendingRef.current) {
        snapshotRefreshPendingRef.current = false;
        window.setTimeout(() => fetchSnapshotRef.current?.({ silent: true }), 0);
      }
    }
  }, [apiRoot, applySnapshot, buildHeaders, notify, user?.id]);

  const fetchPeriodPreview = useCallback(async (schedulePlanId, { signal } = {}) => {
    const normalizedPlanId = normalizeSchedulePlanId(schedulePlanId);
    if (!apiRoot || !user?.id || !normalizedPlanId) return;
    setPeriodPreviewLoading(true);
    setPeriodPreviewError('');
    setPeriodPreviewLots([]);
    setPeriodPreviewBlockedDates([]);
    setPeriodPreviewDayOffs([]);
    setPeriodPreviewWorkShifts([]);
    setPeriodPreviewOperators([]);
    setPeriodPreviewParticipantWorkloads([]);
    setPeriodPreviewPostAuctionActive(false);
    try {
      const response = await axios.get(`${apiRoot}/api/shift_auction/period_preview`, {
        params: { schedule_plan_id: normalizedPlanId },
        headers: buildHeaders(),
        signal
      });
      const preview = response?.data?.preview || {};
      setPeriodPreviewLots(Array.isArray(preview.lots) ? preview.lots : []);
      setPeriodPreviewBlockedDates(Array.isArray(preview.my_blocked_dates) ? preview.my_blocked_dates : []);
      setPeriodPreviewDayOffs(Array.isArray(preview.my_day_offs) ? preview.my_day_offs.filter(Boolean) : []);
      setPeriodPreviewWorkShifts(Array.isArray(preview.my_work_shifts) ? preview.my_work_shifts : []);
      setPeriodPreviewOperators(Array.isArray(preview.selected_operators) ? preview.selected_operators : []);
      setPeriodPreviewParticipantWorkloads(Array.isArray(preview.participant_workloads) ? preview.participant_workloads : []);
      setPeriodPreviewPostAuctionActive(Boolean(preview.post_auction_active));
    } catch (error) {
      if (axios.isCancel?.(error) || error?.code === 'ERR_CANCELED') return;
      setPeriodPreviewLots([]);
      setPeriodPreviewBlockedDates([]);
      setPeriodPreviewDayOffs([]);
      setPeriodPreviewWorkShifts([]);
      setPeriodPreviewOperators([]);
      setPeriodPreviewParticipantWorkloads([]);
      setPeriodPreviewPostAuctionActive(false);
      setPeriodPreviewError(error?.response?.data?.error || 'Не удалось загрузить выбранную неделю');
    } finally {
      if (!signal?.aborted) setPeriodPreviewLoading(false);
    }
  }, [apiRoot, buildHeaders, user?.id]);

  useEffect(() => {
    if (!canMonitor) return;
    if (monitorTab !== 'journal') return;
    fetchJournalPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [monitorTab, canMonitor]);

  const scheduleSnapshotRefresh = useCallback(() => {
    if (monitorRefreshTimerRef.current) return;
    monitorRefreshTimerRef.current = window.setTimeout(() => {
      monitorRefreshTimerRef.current = null;
      fetchSnapshot({ silent: true });
    }, SHIFT_AUCTION_SNAPSHOT_REFRESH_DEBOUNCE_MS);
  }, [fetchSnapshot]);

  const handleRealtimeEvent = useCallback((event) => {
    const eventType = String(event?.event_type || '');
    const payload = event?.payload || {};
    if (SHIFT_AUCTION_LOT_PATCH_EVENTS.has(eventType) && payload.lot?.id) {
      // Apply the single lot from the event payload — instant, zero extra
      // requests. The full snapshot (workload aggregates, journal, my own
      // schedule) is refreshed only when it actually concerns this viewer, and
      // even then it is debounced so a claim storm cannot stampede the DB.
      const eventId = Number(event?.id || 0);
      lastLocallyPatchedEventIdRef.current = Math.max(lastLocallyPatchedEventIdRef.current, eventId);
      const patchLot = (lot) => (
        isSameRealtimeAuctionLot(lot, payload.lot)
          ? mergeRealtimeAuctionLot(lot, payload.lot, eventType, payload)
          : lot
      );
      setLots((currentLots) => currentLots.map(patchLot));
      // Historical post-auction lots use string ids such as `preview-123` and
      // live in a separate collection. Match by id or source plan/shift.
      setPeriodPreviewLots((currentLots) => currentLots.map(patchLot));
      const affectsMe = Number(payload.operator_id) === Number(user?.id)
        || Number(payload.lot.claimed_by) === Number(user?.id);
      if (canMonitor || affectsMe) scheduleSnapshotRefresh();
      return;
    }

    if ((eventType === 'day_off_selected' || eventType === 'day_off_removed') && Number(payload.operator_id) === Number(user?.id)) {
      lastLocallyPatchedEventIdRef.current = Math.max(
        lastLocallyPatchedEventIdRef.current,
        Number(event?.id || 0)
      );
      setMyDayOffs(Array.isArray(payload.my_day_offs) ? payload.my_day_offs.filter(Boolean) : []);
      return;
    }

    if (eventType === 'day_off_selected' || eventType === 'day_off_removed') {
      return;
    }

    fetchSnapshot({ silent: true });
  }, [canMonitor, fetchSnapshot, scheduleSnapshotRefresh, user?.id]);

  const handleRealtimeEventRef = useRef(handleRealtimeEvent);
  const buildHeadersRef = useRef(buildHeaders);
  useEffect(() => { fetchSnapshotRef.current = fetchSnapshot; }, [fetchSnapshot]);
  useEffect(() => { handleRealtimeEventRef.current = handleRealtimeEvent; }, [handleRealtimeEvent]);
  useEffect(() => { buildHeadersRef.current = buildHeaders; }, [buildHeaders]);
  useEffect(() => () => {
    if (monitorRefreshTimerRef.current) {
      window.clearTimeout(monitorRefreshTimerRef.current);
      monitorRefreshTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    fetchSnapshotRef.current?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const canOpenStream = Boolean(apiRoot && user?.id && (canMonitor || settings.is_current_user_tester));

  useEffect(() => {
    if (!canOpenStream) return undefined;

    let cancelled = false;
    let currentAbortController = null;
    let reconnectTimer = null;
    let pollTimer = null;
    let reconnectAttempt = 0;
    let authRefreshAttempts = 0;

    // Refresh the access token by reusing the global axios interceptor's
    // refresh-and-retry (any axios 401 triggers a single shared refresh and
    // persists the rotated token). `/api/auth/me` is the cheapest such call.
    // The SSE stream uses fetch(), which bypasses that interceptor, so it must
    // ask for the refresh explicitly before reconnecting.
    const refreshAuthSession = async () => {
      try {
        await axios.get(`${apiRoot}/api/auth/me`, { headers: buildHeadersRef.current?.() || {} });
        return true;
      } catch (_error) {
        return false;
      }
    };

    const stopPolling = () => {
      if (pollTimer) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const startPolling = () => {
      stopPolling();
      pollTimer = window.setInterval(() => {
        if (!cancelled) fetchSnapshotRef.current?.({ silent: true });
      }, 15000);
    };

    const scheduleReconnect = () => {
      if (cancelled || reconnectTimer) return;
      const delay = Math.min(30000, 2000 * Math.pow(2, Math.min(reconnectAttempt, 4)));
      reconnectAttempt += 1;
      setConnectionState('reconnecting');
      startPolling();
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        if (!cancelled) readStream();
      }, delay);
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
          headers: buildHeadersRef.current?.({ Accept: 'text/event-stream' }) || { Accept: 'text/event-stream' },
          signal: abortController.signal,
          credentials: 'include'
        });
        if (response.status === 401) {
          // Access token expired mid-stream. Refresh once and reconnect right
          // away with the fresh token rather than backing off with the stale
          // one. Capped so a truly dead session falls through to backoff.
          if (!cancelled && authRefreshAttempts < SHIFT_AUCTION_SSE_MAX_AUTH_REFRESH) {
            authRefreshAttempts += 1;
            const refreshed = await refreshAuthSession();
            if (!cancelled && refreshed) {
              reconnectAttempt = 0;
              return readStream();
            }
          }
          throw new Error('SSE auth refresh failed');
        }
        if (!response.ok || !response.body) throw new Error('SSE connection failed');
        const recoveredAfterGap = reconnectAttempt > 0 || authRefreshAttempts > 0;
        setConnectionState('online');
        reconnectAttempt = 0;
        authRefreshAttempts = 0;
        stopPolling();
        // After any gap, resync once so missed state can never linger on screen.
        if (recoveredAfterGap) fetchSnapshotRef.current?.({ silent: true });
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
              handleRealtimeEventRef.current?.(event);
            } catch (parseError) {
              console.warn('Failed to parse shift auction event', parseError);
            }
          }
        }
      } catch (error) {
        if (cancelled || error?.name === 'AbortError') return;
      }

      if (!cancelled) scheduleReconnect();
    };

    const handleVisibilityChange = () => {
      if (cancelled) return;
      if (document.visibilityState === 'visible') {
        fetchSnapshotRef.current?.({ silent: true });
        if (reconnectTimer) {
          window.clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        reconnectAttempt = 0;
        currentAbortController?.abort?.();
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
  }, [apiRoot, canOpenStream, user?.id]);

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
  const restartablePeriods = useMemo(
    () => availablePeriods.filter((period) => period?.can_restart !== false),
    [availablePeriods]
  );
  const selectedFilteredOperatorCount = useMemo(
    () => filteredOperators.reduce((count, operator) => count + (selectedIds.has(operator.id) ? 1 : 0), 0),
    [filteredOperators, selectedIds]
  );
  const allFilteredOperatorsSelected = filteredOperators.length > 0 && selectedFilteredOperatorCount === filteredOperators.length;

  const draftStartsAtParts = useMemo(() => splitDateTimeInputValue(draftStartsAt), [draftStartsAt]);
  const draftEndsAtParts = useMemo(() => splitDateTimeInputValue(draftEndsAt), [draftEndsAt]);
  const selectedDraftPeriod = useMemo(
    () => restartablePeriods.find((period) => Number(period?.id) === Number(draftSchedulePlanId)) || null,
    [restartablePeriods, draftSchedulePlanId]
  );
  const activeSchedulePlanId = normalizeSchedulePlanId(settings.selected_schedule_plan_id ?? settings.selected_period?.id);
  const selectedViewSchedulePlanId = normalizeSchedulePlanId(viewSchedulePlanId) || activeSchedulePlanId;
  const isViewingActivePeriod = !selectedViewSchedulePlanId || (activeSchedulePlanId && selectedViewSchedulePlanId === activeSchedulePlanId);
  const selectedViewPeriod = useMemo(
    () => availablePeriods.find((period) => Number(period?.id) === Number(selectedViewSchedulePlanId)) || settings.selected_period || null,
    [availablePeriods, selectedViewSchedulePlanId, settings.selected_period]
  );
  const monitoredLots = isViewingActivePeriod ? lots : periodPreviewLots;
  const monitoredMyDayOffs = isViewingActivePeriod ? myDayOffs : periodPreviewDayOffs;
  const monitoredMyBlockedDates = isViewingActivePeriod ? myBlockedDates : periodPreviewBlockedDates;
  const monitoredMyWorkShifts = isViewingActivePeriod ? myWorkShifts : periodPreviewWorkShifts;
  const monitoredOperators = isViewingActivePeriod ? settings.selected_operators : periodPreviewOperators;
  const monitoredParticipantWorkloads = isViewingActivePeriod ? participantWorkloads : periodPreviewParticipantWorkloads;
  const selectedViewPostAuctionActive = isViewingActivePeriod
    ? Boolean(settings.post_auction_active)
    : Boolean(periodPreviewPostAuctionActive);
  const draftRangeInvalid = Boolean(
    draftStartsAt
    && draftEndsAt
    && new Date(draftEndsAt).getTime() <= new Date(draftStartsAt).getTime()
  );
  const draftAuctionWindowMinutes = useMemo(
    () => getAuctionWindowMinutes(draftStartsAt, draftEndsAt),
    [draftEndsAt, draftStartsAt]
  );

  const initialPeriodKey = `${initialPeriod?.dateFrom || initialPeriod?.date_from || ''}|${initialPeriod?.dateTo || initialPeriod?.date_to || ''}`;
  useEffect(() => {
    if (!initialPeriodKey || initialPeriodKey === '|' || appliedInitialPeriodKey === initialPeriodKey || !availablePeriods.length) return;
    const [dateFrom, dateTo] = initialPeriodKey.split('|');
    const matchedPeriod = availablePeriods.find((period) => (
      String(period?.date_from || '') === dateFrom
      && String(period?.date_to || '') === dateTo
    ));
    if (!matchedPeriod?.id) {
      setAppliedInitialPeriodKey(initialPeriodKey);
      onInitialPeriodApplied?.();
      return;
    }
    const planId = String(matchedPeriod.id);
    setViewSchedulePlanId(planId);
    if (canManage && matchedPeriod.can_restart !== false) {
      setDraftSchedulePlanId(planId);
      setMonitorTab('settings');
    }
    setAppliedInitialPeriodKey(initialPeriodKey);
    onInitialPeriodApplied?.();
  }, [appliedInitialPeriodKey, availablePeriods, canManage, initialPeriodKey, onInitialPeriodApplied]);

  useEffect(() => {
    if (!selectedViewSchedulePlanId || isViewingActivePeriod) {
      setPeriodPreviewLots([]);
      setPeriodPreviewBlockedDates([]);
      setPeriodPreviewDayOffs([]);
      setPeriodPreviewWorkShifts([]);
      setPeriodPreviewOperators([]);
      setPeriodPreviewParticipantWorkloads([]);
      setPeriodPreviewPostAuctionActive(false);
      setPeriodPreviewError('');
      setPeriodPreviewLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    fetchPeriodPreview(selectedViewSchedulePlanId, { signal: controller.signal });
    return () => controller.abort();
  }, [fetchPeriodPreview, isViewingActivePeriod, selectedViewSchedulePlanId]);

  const lotDates = useMemo(
    () => Array.from(new Set((monitoredLots || []).map((lot) => lot.shift_date).filter(Boolean))).sort(),
    [monitoredLots]
  );

  const myBlockedDateMap = useMemo(() => {
    const map = new Map();
    (monitoredMyBlockedDates || []).forEach((item) => {
      const date = typeof item === 'string' ? item : item?.date;
      if (!date || map.has(date)) return;
      const period = typeof item === 'string' ? { date, label: 'Период' } : item;
      map.set(date, { ...period, label: getAuctionBlockedDateLabel(period) });
    });
    return map;
  }, [monitoredMyBlockedDates]);

  const visibleLots = useMemo(() => {
    // Single-lot model: one lot per shift. Partially-taken shifts carry claim_segments
    // and stay 'available' (the cell shows the free part). No separate remainder lots.
    if (canMonitor) return monitoredLots;
    return monitoredLots.filter((lot) => (
      (selectedViewPostAuctionActive || !monitoredMyDayOffs.includes(lot.shift_date))
      && !myBlockedDateMap.has(lot.shift_date)
    ));
  }, [canMonitor, monitoredLots, monitoredMyDayOffs, myBlockedDateMap, selectedViewPostAuctionActive]);

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
    () => monitoredLots.filter((lot) => lot.status === 'claimed' && Number(lot.claimed_by) === Number(user?.id)),
    [monitoredLots, user?.id]
  );
  const myClaimedDateSet = useMemo(
    () => new Set(
      myClaimedLots
        .filter((lot) => lot.status === 'claimed')
        .map((lot) => lot.shift_date)
        .filter(Boolean)
    ),
    [myClaimedLots]
  );

  const dayOffQuota = useMemo(() => Math.min(2, Math.max(0, lotDates.length)), [lotDates.length]);
  const manualDayOffLimit = useMemo(
    () => Math.max(0, dayOffQuota - Math.min(dayOffQuota, myBlockedDateMap.size)),
    [dayOffQuota, myBlockedDateMap.size]
  );
  const selectedManualDayOffCount = useMemo(
    () => monitoredMyDayOffs.filter((date) => !myBlockedDateMap.has(date)).length,
    [monitoredMyDayOffs, myBlockedDateMap]
  );

  const dayNavigationItems = useMemo(() => {
    return lotDates.map((date) => {
      const dayLots = monitoredLots.filter((lot) => lot.shift_date === date);
      const claimedLots = dayLots.filter((lot) => lot.status === 'claimed');
      const myClaimed = dayLots
        .filter((lot) => lot.status === 'claimed' && Number(lot.claimed_by) === Number(user?.id))
        .sort((a, b) => (
          clockToMinutes(a.start_time) - clockToMinutes(b.start_time)
          || clockToMinutes(a.end_time) - clockToMinutes(b.end_time)
          || Number(a.id || 0) - Number(b.id || 0)
        ));
      const myClaimedNetMinutes = myClaimed.reduce((sum, lot) => sum + getAuctionLotNetMinutes(lot), 0);
      const isDayOff = monitoredMyDayOffs.includes(date);
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
        myClaimedLots: myClaimed,
        myClaimedLot: myClaimed[0] || null,
        myClaimedNetMinutes,
        available: availableCount,
        locked: lockedCount,
        isDayOff,
        isBlocked: Boolean(blockedPeriod),
        blockedLabel: blockedPeriod ? getAuctionBlockedDateLabel(blockedPeriod) : '',
        blockedPeriod,
        state
      };
    });
  }, [lotDates, monitoredLots, monitoredMyDayOffs, myBlockedDateMap, user?.id, visibleLots]);

  // Group the day's lots by their original shift so the admin can see, per shift,
  // who took which part (claimed slices) and what is still free (remainder).
  // Flat, tidy list of shifts taken on the active day (one row per claim).
  const adminActiveDayClaimLots = useMemo(() => {
    if (!canMonitor || !activeDayDate) return [];
    const rows = [];
    (monitoredLots || []).forEach((lot) => {
      if (!lot || lot.shift_date !== activeDayDate) return;
      const claimSegs = Array.isArray(lot.claim_segments) ? lot.claim_segments : [];
      if (claimSegs.length) {
        // Partially-taken shift (single lot): one row per taken part.
        claimSegs.forEach((seg, i) => {
          const sMin = parseHHMMToMinutes(seg.start_time);
          const eMin = parseHHMMToMinutes(seg.end_time);
          const net = (sMin != null && eMin != null) ? Math.max(0, (eMin > sMin ? eMin : eMin + 1440) - sMin) : 0;
          rows.push({
            key: `${lot.id}-cs${i}`,
            start: sMin != null ? sMin : 0,
            timeLabel: `${String(seg.start_time || '').slice(0, 5)}–${String(seg.end_time || '').slice(0, 5)}`,
            operatorName: seg.claimed_by_name || `#${seg.claimed_by || ''}`,
            operatorId: seg.claimed_by != null ? Number(seg.claimed_by) : null,
            netMinutes: net,
            partial: true,
            originalLabel: formatAuctionShiftLabel(lot),
          });
        });
      } else if (lot.status === 'claimed' && lot.claimed_by != null) {
        const range = getAuctionLotEffectiveMinuteRange(lot);
        rows.push({
          key: `${lot.id}`,
          start: range ? range[0] : 0,
          timeLabel: formatAuctionLotEffectiveTimeRangeLabel(lot),
          operatorName: lot.claimed_by_name || `#${lot.claimed_by}`,
          operatorId: Number(lot.claimed_by),
          netMinutes: getAuctionLotNetMinutes(lot),
          partial: isPartialPostAuctionClaim(lot),
          originalLabel: formatAuctionShiftLabel(lot),
        });
      }
    });
    return rows.sort((a, b) => (
      a.start - b.start
      || String(a.operatorName).localeCompare(String(b.operatorName), 'ru')
    ));
  }, [activeDayDate, canMonitor, monitoredLots]);

  const adminActiveDayClaimCount = useMemo(
    () => adminActiveDayClaimLots.length,
    [adminActiveDayClaimLots]
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
    [settings.enabled, settings.ends_at, settings.finished_at, settings.paused_at, settings.starts_at, statusVersion]
  );
  const hasStartCountdown = runtimeStatus === 'scheduled' && Boolean(settings.starts_at);
  const hasCloseCountdown = runtimeStatus === 'open' && Boolean(settings.ends_at);
  const auctionStatusLabel = runtimeStatus === 'scheduled'
    ? 'Откроется'
    : runtimeStatus === 'open'
      ? 'Аукцион открыт'
      : runtimeStatus === 'paused'
        ? 'Аукцион на паузе'
      : runtimeStatus === 'closed'
        ? 'Аукцион закрыт'
        : 'Аукцион выключен';
  const auctionStatusShortLabel = runtimeStatus === 'scheduled'
    ? 'Старт'
    : runtimeStatus === 'open'
      ? 'Открыт'
      : runtimeStatus === 'paused'
        ? 'Пауза'
      : runtimeStatus === 'closed'
        ? 'Закрыт'
        : 'Выкл.';
  const auctionStatusDetailText = runtimeStatus === 'scheduled'
    ? 'скоро'
    : runtimeStatus === 'open'
      ? (hasCloseCountdown ? 'до закрытия' : 'идет выбор')
      : runtimeStatus === 'paused'
        ? 'выбор временно остановлен'
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
      : runtimeStatus === 'paused'
        ? 'border-amber-200 bg-amber-50 text-amber-800'
      : runtimeStatus === 'closed'
        ? 'border-slate-200 bg-slate-100 text-slate-600'
        : 'border-amber-200 bg-amber-50 text-amber-800';

  const isTester = Boolean(settings.enabled && settings.is_current_user_tester);
  const canUseAuction = isTester || canMonitor || Boolean(settings.has_period_history_access);
  const canChoose = isViewingActivePeriod && isTester && (runtimeStatus === 'scheduled' || runtimeStatus === 'open');
  const canClaim = isViewingActivePeriod && isTester && runtimeStatus === 'open';
  const userRate = useMemo(() => {
    const directRate = Number(user?.rate);
    if (Number.isFinite(directRate) && directRate > 0) return directRate;
    const snapshotOperator = (monitoredOperators || []).find((operator) => Number(operator?.id) === Number(user?.id));
    const snapshotRate = Number(snapshotOperator?.rate);
    return Number.isFinite(snapshotRate) && snapshotRate > 0 ? snapshotRate : 1;
  }, [monitoredOperators, user?.id, user?.rate]);

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

  const operatorWorkloadRows = useMemo(() => {
    if (!canMonitor) return [];
    const operatorsById = new Map(
      (monitoredOperators || [])
        .filter((operator) => operator && operator.id != null)
        .map((operator) => [Number(operator.id), operator])
    );
    return (monitoredParticipantWorkloads || [])
      .map((workload) => {
        if (!workload || workload.operator_id == null) return null;
        const operator = operatorsById.get(Number(workload.operator_id)) || {};
        const normMinutes = Number(workload.norm_minutes || 0);
        const claimedNet = Number(workload.claimed_net_minutes || 0);
        const overMinutes = Number(workload.over_minutes || 0);
        const isComplete = Boolean(workload.is_complete);
        const progress = normMinutes > 0
          ? clampNumber((claimedNet / normMinutes) * 100, 0, 140)
          : (claimedNet > 0 ? 100 : 0);
        const status = overMinutes > 0
          ? 'over'
          : isComplete
            ? 'complete'
            : claimedNet > 0
              ? 'partial'
              : 'empty';
        return {
          ...workload,
          name: operator.name || `Оператор #${workload.operator_id}`,
          supervisor_name: operator.supervisor_name || '',
          direction: operator.direction || '',
          progress,
          status
        };
      })
      .filter(Boolean);
  }, [canMonitor, monitoredOperators, monitoredParticipantWorkloads]);

  const operatorWorkloadStats = useMemo(() => {
    const stats = { total: 0, lagging: 0, complete: 0, over: 0, empty: 0 };
    operatorWorkloadRows.forEach((row) => {
      stats.total += 1;
      if (row.status === 'empty') stats.empty += 1;
      else if (row.status === 'partial') stats.lagging += 1;
      else if (row.status === 'complete') stats.complete += 1;
      else if (row.status === 'over') stats.over += 1;
    });
    return stats;
  }, [operatorWorkloadRows]);

  const drilldownData = useMemo(() => {
    if (!drilldownOperatorId) return null;
    const opIdNum = Number(drilldownOperatorId);
    const operator = (monitoredOperators || []).find((op) => Number(op?.id) === opIdNum) || null;
    const workload = (monitoredParticipantWorkloads || []).find((w) => Number(w?.operator_id) === opIdNum) || null;
    const claimedLots = [];
    (monitoredLots || []).forEach((lot) => {
      if (!lot) return;
      const segs = Array.isArray(lot.claim_segments) ? lot.claim_segments : [];
      if (segs.length) {
        // Single-lot model: this operator's partial parts come from claim_segments.
        segs.forEach((seg, i) => {
          if (Number(seg.claimed_by) !== opIdNum) return;
          claimedLots.push({
            ...lot,
            id: `${lot.id}-cs${i}`,
            status: 'claimed',
            post_auction_claimed: true,
            claimed_by: opIdNum,
            // keep lot.start_time/end_time (full shift) so the badge reads "часть из …"
            claim_start_time: seg.start_time,
            claim_end_time: seg.end_time,
            breaks: [],
          });
        });
      } else if (lot.status === 'claimed' && Number(lot.claimed_by) === opIdNum) {
        claimedLots.push(lot);
      }
    });
    claimedLots.sort((a, b) => {
      const dateCmp = String(a.shift_date || '').localeCompare(String(b.shift_date || ''));
      if (dateCmp !== 0) return dateCmp;
      return String(getAuctionLotEffectiveStartTime(a) || '').localeCompare(String(getAuctionLotEffectiveStartTime(b) || ''));
    });
    return {
      operator_id: opIdNum,
      operator,
      workload,
      claimed_lots: claimedLots
    };
  }, [drilldownOperatorId, monitoredLots, monitoredOperators, monitoredParticipantWorkloads]);

  // Breakdown of the clicked shift cell: all lots of the same original shift on the
  // same day (claimed slices + free remainder) → "who took which part of this shift".
  const shiftDetailData = useMemo(() => {
    const clicked = shiftDetailLot;
    if (!clicked) return null;
    const date = clicked.shift_date;
    const sourceId = clicked.source_schedule_shift_id;
    const siblings = (monitoredLots || []).filter((lot) => {
      if (!lot || lot.shift_date !== date) return false;
      return sourceId != null
        ? lot.source_schedule_shift_id === sourceId
        : String(lot.id) === String(clicked.id);
    });
    const breakdown = buildAuctionShiftSegments(siblings);
    if (!breakdown) return null;
    return { date, ...breakdown };
  }, [shiftDetailLot, monitoredLots]);

  const filteredOperatorWorkloads = useMemo(() => {
    const normalizedQuery = operatorWorkloadQuery.trim().toLowerCase();
    return operatorWorkloadRows
      .filter((row) => {
        if (operatorWorkloadFilter === 'lagging' && !(row.status === 'empty' || row.status === 'partial')) return false;
        if (operatorWorkloadFilter === 'complete' && row.status !== 'complete') return false;
        if (operatorWorkloadFilter === 'over' && row.status !== 'over') return false;
        if (operatorWorkloadFilter === 'empty' && row.status !== 'empty') return false;
        if (!normalizedQuery) return true;
        const haystack = [row.name, row.supervisor_name, row.direction]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();
        return haystack.includes(normalizedQuery);
      })
      .sort((a, b) => {
        if (a.progress !== b.progress) return a.progress - b.progress;
        return String(a.name || '').localeCompare(String(b.name || ''), 'ru');
      });
  }, [operatorWorkloadRows, operatorWorkloadFilter, operatorWorkloadQuery]);

  const isTopupActive = Boolean(settings.topup_started_at) && runtimeStatus === 'open';

  const myClaimedLotsByDate = useMemo(() => {
    const map = new Map();
    myClaimedLots.forEach((lot) => {
      if (!lot || !lot.shift_date) return;
      const list = map.get(lot.shift_date) || [];
      list.push(lot);
      map.set(lot.shift_date, list);
    });
    return map;
  }, [myClaimedLots]);

  const myWorkShiftsByDate = useMemo(() => {
    const map = new Map();
    (monitoredMyWorkShifts || []).forEach((shift) => {
      if (!shift || !shift.shift_date) return;
      const list = map.get(shift.shift_date) || [];
      list.push(shift);
      map.set(shift.shift_date, list);
    });
    return map;
  }, [monitoredMyWorkShifts]);

  const postAuctionClaimOptionsByLotId = useMemo(() => {
    const map = new Map();
    if (!selectedViewPostAuctionActive || canMonitor) return map;
    (monitoredLots || []).forEach((lot) => {
      if (!lot || (lot.status !== 'available' && lot.status !== 'cancelled') || Boolean(lot.post_auction_claimed)) return;
      const lotId = getAuctionLotActionKey(lot);
      if (!lotId) return;
      const option = buildPostAuctionClaimOption(
        lot,
        myWorkShiftsByDate.get(lot.shift_date) || [],
        myClaimedLotsByDate.get(lot.shift_date) || []
      );
      if (option) map.set(lotId, option);
    });
    return map;
  }, [canMonitor, monitoredLots, myClaimedLotsByDate, myWorkShiftsByDate, selectedViewPostAuctionActive]);

  const claimBlockReasonByLotId = useMemo(() => {
    const reasons = new Map();
    const canEvaluatePostAuction = selectedViewPostAuctionActive && (isTester || Boolean(settings.has_period_history_access));
    if (canMonitor || (!isTester && !canEvaluatePostAuction)) return reasons;
    if (!isViewingActivePeriod && !canEvaluatePostAuction) return reasons;
    const postAuctionActive = Boolean(selectedViewPostAuctionActive);
    const parseHM = (value) => {
      if (!value || typeof value !== 'string') return null;
      const [h, m] = value.split(':').map(Number);
      if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
      return h * 60 + m;
    };
    const normalizeRange = (startStr, endStr) => {
      const s = parseHM(startStr);
      const e = parseHM(endStr);
      if (s === null || e === null) return null;
      return [s, e > s ? e : e + 24 * 60];
    };
    monitoredLots.forEach((lot) => {
      if (!lot) return;
      // In post-auction mode also process 'cancelled' lots (they can be claimed).
      // Outside post-auction mode only 'available' lots are actionable.
      const isPostAuctionCandidate = postAuctionActive
        && (lot.status === 'available' || lot.status === 'cancelled')
        && !Boolean(lot.post_auction_claimed);
      if (!isPostAuctionCandidate && (!isViewingActivePeriod || lot.status !== 'available')) return;
      const lotId = getAuctionLotActionKey(lot);
      if (!lotId) return;
      const blockedPeriod = myBlockedDateMap.get(lot.shift_date);
      if (blockedPeriod) {
        reasons.set(lotId, `День закрыт: ${getAuctionBlockedDateLabel(blockedPeriod)}`);
        return;
      }
      if (postAuctionActive) {
        const option = postAuctionClaimOptionsByLotId.get(lotId);
        if (option && !option.canClaim) {
          reasons.set(lotId, 'Нет свободного интервала без пересечения');
        }
        return;
      }
      if (isTopupActive) {
        // Top-up mode allows extra shifts on the same date as long as they don't
        // overlap with an already-claimed shift. Skip norm checks.
        const sameDateClaims = myClaimedLotsByDate.get(lot.shift_date) || [];
        if (sameDateClaims.length) {
          const candidateRange = normalizeRange(lot.start_time, lot.end_time);
          if (candidateRange) {
            const conflict = sameDateClaims.find((existing) => {
              const range = normalizeRange(existing.start_time, existing.end_time);
              if (!range) return false;
              return candidateRange[0] < range[1] && range[0] < candidateRange[1];
            });
            if (conflict) {
              reasons.set(lotId, `Пересекается с ${conflict.start_time}–${conflict.end_time}`);
              return;
            }
          }
        }
        return;
      }
      if (lot.shift_date && myClaimedDateSet.has(lot.shift_date)) {
        reasons.set(lotId, 'На этот день уже выбрана смена');
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
  }, [canMonitor, isTester, isTopupActive, isViewingActivePeriod, monitoredLots, myAuctionWorkload, myBlockedDateMap, myClaimedDateSet, myClaimedLotsByDate, postAuctionClaimOptionsByLotId, selectedViewPostAuctionActive, settings.has_period_history_access]);

  useEffect(() => {
    if (!canUseAuction || !lotDates.length || typeof window === 'undefined') return undefined;

    const updateAuctionColumnWidth = () => {
      // Only recompute when the layout container is actually in the DOM —
      // otherwise (e.g. when monitor is on a different tab) we'd fall back to
      // the full window width and inflate the column size, leaving the grid
      // overflowing once it comes back into view.
      const layoutNode = auctionLayoutRef.current;
      if (!layoutNode) return;
      const layoutWidth = layoutNode.getBoundingClientRect?.().width || 0;
      if (layoutWidth <= 0) return;
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
  }, [canUseAuction, lotDates.length, monitorTab]);

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
    if (canMonitor) setIsAdminDayDetailsOpen(true);
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
  }, [canMonitor, lotDates]);

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

  const selectAllFilteredOperators = useCallback(() => {
    if (!filteredOperators.length) return;
    setSelectedIds((current) => {
      const next = new Set(current);
      filteredOperators.forEach((operator) => {
        const id = normalizeOperatorId(operator?.id);
        if (id) next.add(id);
      });
      return next;
    });
  }, [filteredOperators]);

  const clearSelectedOperators = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const handleViewPeriodSelect = useCallback((period) => {
    const id = normalizeSchedulePlanId(period?.id);
    if (!id) return;
    const planId = String(id);
    setViewSchedulePlanId(planId);
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
          schedule_plan_id: selectedDraftPeriod?.id || null,
          operator_ids: Array.from(selectedIds)
        },
        { headers: buildHeaders() }
      );
      await fetchSnapshot({ silent: true });
      notify(selectedDraftPeriod ? `Аукцион сохранен для недели ${formatAuctionPeriodLabel(selectedDraftPeriod)}` : 'Настройки тестового аукциона сохранены');
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось сохранить настройки аукциона смен', 'error');
    } finally {
      setIsSaving(false);
    }
  }, [apiRoot, buildHeaders, canManage, draftEnabled, draftEndsAt, draftNote, draftRangeInvalid, draftSchedulePlanId, draftStartsAt, fetchSnapshot, notify, selectedDraftPeriod, selectedIds]);

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
      `Начать аукцион заново для недели ${formatAuctionPeriodLabel(selectedDraftPeriod)}?\n\n`
      + 'Будут очищены только выбранные смены и выходные этой недели. Прошлые опубликованные периоды не изменятся. '
      + 'Режим добора для нового запуска будет выключен.'
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

  const handleAuctionControl = useCallback(async (action) => {
    if (!canManage || !apiRoot || isControllingAuction) return;
    const actionMessages = {
      pause: 'Аукцион приостановлен',
      resume: 'Аукцион возобновлен',
      finish: 'Аукцион завершен'
    };
    if (action === 'finish') {
      const confirmed = window.confirm('Завершить аукцион сейчас? После этого операторы больше не смогут менять выбор.');
      if (!confirmed) return;
    }
    setIsControllingAuction(true);
    try {
      const response = await axios.post(
        `${apiRoot}/api/shift_auction/test_control`,
        { action },
        { headers: buildHeaders({ 'Content-Type': 'application/json' }) }
      );
      applySnapshot(response?.data?.snapshot || {});
      notify(actionMessages[action] || 'Состояние аукциона обновлено');
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось изменить состояние аукциона', 'error');
    } finally {
      setIsControllingAuction(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, canManage, isControllingAuction, notify]);

  const handleToggleTopup = useCallback(async () => {
    if (!canManage || !apiRoot || isTogglingTopup) return;
    const enable = !settings.topup_started_at;
    if (enable) {
      const confirmed = window.confirm(
        'Перевести аукцион в режим добора смен?\n\n'
        + 'В режиме добора операторы смогут забирать дополнительные смены (даже сверх своей нормы), '
        + 'если они не пересекаются по времени с уже взятыми. Этот момент будет зафиксирован в журнале аукциона.'
      );
      if (!confirmed) return;
    }
    setIsTogglingTopup(true);
    try {
      const response = await axios({
        method: enable ? 'POST' : 'DELETE',
        url: `${apiRoot}/api/shift_auction/test_topup`,
        headers: buildHeaders({ 'Content-Type': 'application/json' })
      });
      applySnapshot(response?.data?.snapshot || {});
      notify(enable ? 'Аукцион переведён в режим добора смен' : 'Режим добора отключён');
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось изменить режим добора', 'error');
    } finally {
      setIsTogglingTopup(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, canManage, isTogglingTopup, notify, settings.topup_started_at]);

  const handlePublishAuction = useCallback(async () => {
    if (!canManage || !apiRoot || isPublishingAuction) return;
    const confirmed = window.confirm(
      'Сохранить итоговые смены и выходные в раздел «Графики работы»? Данные за неделю аукциона у участников будут заменены.'
    );
    if (!confirmed) return;
    setIsPublishingAuction(true);
    try {
      const response = await axios.post(
        `${apiRoot}/api/shift_auction/test_publish`,
        {},
        { headers: buildHeaders({ 'Content-Type': 'application/json' }) }
      );
      applySnapshot(response?.data?.snapshot || {});
      const summary = response?.data?.summary || {};
      notify(`Графики сохранены: ${Number(summary.shifts_saved || 0)} смен, ${Number(summary.days_off_saved || 0)} выходных`);
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось сохранить итоговые графики', 'error');
    } finally {
      setIsPublishingAuction(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, canManage, isPublishingAuction, notify]);

  const openAddShiftModal = useCallback((group, date) => {
    if (!group || !date) return;
    setAddShiftStart(group.night ? '20:00' : '09:00');
    setAddShiftTarget({
      groupId: group.id,
      title: group.title,
      rate: group.rate,
      night: Boolean(group.night),
      shiftMinutes: group.shiftMinutes,
      date
    });
  }, []);

  const handleSubmitAddShift = useCallback(async () => {
    if (!addShiftTarget || !apiRoot || isAddingShift) return;
    const start = addShiftTarget.night ? '20:00' : addShiftStart;
    const end = computeAuctionEndTime(start, addShiftTarget);
    if (!start || !end) {
      notify('Укажите время начала смены', 'error');
      return;
    }
    setIsAddingShift(true);
    try {
      const response = await axios.post(
        `${apiRoot}/api/shift_auction/admin/add_lot`,
        {
          shift_date: addShiftTarget.date,
          start_time: start,
          end_time: end,
          rate_min: addShiftTarget.rate
        },
        { headers: buildHeaders({ 'Content-Type': 'application/json' }) }
      );
      if (response?.data?.snapshot) {
        applySnapshot(response.data.snapshot);
      } else {
        await fetchSnapshot({ silent: true });
      }
      notify(`Смена ${start}–${end} добавлена`);
      setAddShiftTarget(null);
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось добавить смену', 'error');
    } finally {
      setIsAddingShift(false);
    }
  }, [addShiftTarget, addShiftStart, apiRoot, applySnapshot, buildHeaders, fetchSnapshot, isAddingShift, notify]);

  const handleExportAuctionReport = useCallback(async () => {
    if (!canManage || !apiRoot || isExportingAuctionReport) return;
    setIsExportingAuctionReport(true);
    try {
      const response = await axios.get(
        `${apiRoot}/api/shift_auction/test_export_excel`,
        {
          headers: buildHeaders(),
          responseType: 'blob'
        }
      );
      const contentType = response.headers?.['content-type'] || 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
      const blob = new Blob([response.data], { type: contentType });
      const disposition = response.headers?.['content-disposition'] || '';
      const utfFilenameMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      const plainFilenameMatch = disposition.match(/filename="?([^";]+)"?/i);
      const filename = utfFilenameMatch
        ? decodeURIComponent(utfFilenameMatch[1])
        : (plainFilenameMatch?.[1] || 'shift_auction_report.xlsx');

      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      notify('Отчет аукциона выгружен');
    } catch (error) {
      let message = error?.response?.data?.error || 'Не удалось выгрузить отчет аукциона';
      if (error?.response?.data instanceof Blob) {
        try {
          const text = await error.response.data.text();
          const payload = JSON.parse(text);
          message = payload?.error || message;
        } catch (_) {
          // keep fallback message
        }
      }
      notify(message, 'error');
    } finally {
      setIsExportingAuctionReport(false);
    }
  }, [apiRoot, buildHeaders, canManage, isExportingAuctionReport, notify]);

  const handleClaimLot = useCallback(async (lotId) => {
    if (!canClaim || !apiRoot) return;
    const numericId = Number(lotId);
    if (!Number.isFinite(numericId)) return;
    const lotKey = getAuctionLotActionKey(numericId);
    if (pendingClaimLotIdsRef.current.has(lotKey)) return;

    const blockReason = claimBlockReasonByLotId.get(lotKey);
    if (blockReason) {
      notifyClaimError(blockReason);
      return;
    }

    const prevLot = (lotsRef.current || []).find((l) => Number(l?.id) === numericId);
    if (!prevLot || prevLot.status !== 'available') return;

    pendingClaimLotIdsRef.current.add(lotKey);
    setClaimingLotIds((current) => {
      if (current.has(lotKey)) return current;
      const next = new Set(current);
      next.add(lotKey);
      return next;
    });

    setLots((currentLots) => currentLots.map((l) => (
      Number(l.id) === numericId
        ? {
            ...l,
            status: 'claimed',
            claimed_by: Number(user?.id) || l.claimed_by,
            claimed_at: new Date().toISOString(),
            _optimistic: true
          }
        : l
    )));

    try {
      const response = await enqueueAuctionMutation(() => postClaimLot(numericId));
      const serverLot = response?.data?.lot;
      if (serverLot && serverLot.id) {
        setLots((currentLots) => currentLots.map((l) => (
          Number(l.id) === Number(serverLot.id)
            ? { ...l, ...serverLot, _optimistic: false }
            : l
        )));
      }
    } catch (error) {
      const code = error?.response?.data?.code;
      const message = error?.response?.data?.error;

      setLots((currentLots) => currentLots.map((l) => (
        Number(l.id) === numericId && l._optimistic
          ? { ...prevLot, _optimistic: false }
          : l
      )));

      await fetchSnapshot({ silent: true });

      const silentCodes = new Set(['LOT_ALREADY_CLAIMED', 'AUCTION_NOT_OPEN']);
      if (!silentCodes.has(code)) {
        notifyClaimError(message || 'Не удалось забрать смену');
      }
    } finally {
      pendingClaimLotIdsRef.current.delete(lotKey);
      setClaimingLotIds((current) => {
        if (!current.has(lotKey)) return current;
        const next = new Set(current);
        next.delete(lotKey);
        return next;
      });
    }
  }, [apiRoot, canClaim, claimBlockReasonByLotId, enqueueAuctionMutation, fetchSnapshot, notifyClaimError, postClaimLot, user?.id]);

  const handleRequestPostAuctionClaim = useCallback((lot) => {
    if (!lot || !lot.id) return;
    const option = postAuctionClaimOptionsByLotId.get(getAuctionLotActionKey(lot));
    const segment = option?.recommendedSegment || null;
    setPostClaimSelection({
      start_time: segment?.start_time || normalizeClockValue(lot.start_time),
      end_time: segment?.end_time || normalizeClockValue(lot.end_time)
    });
    setPostClaimConfirmLot(lot);
  }, [postAuctionClaimOptionsByLotId]);

  const handleClosePostAuctionClaim = useCallback(() => {
    setPostClaimConfirmLot(null);
    setPostClaimSelection({ start_time: '', end_time: '' });
  }, []);

  const handleConfirmPostAuctionClaim = useCallback(async () => {
    const lot = postClaimConfirmLot;
    if (!lot || !lot.id) return;
    const lotKey = getAuctionLotActionKey(lot);
    if (!lotKey) return;
    if (postClaimingLotIds.has(lotKey)) return;
    const option = postAuctionClaimOptionsByLotId.get(lotKey);
    if (option && !isSelectionInsideAvailableSegments(lot, postClaimSelection, option.availableSegments)) {
      notifyClaimError('Выбранный интервал пересекается с вашим графиком');
      return;
    }

    setPostClaimingLotIds((current) => {
      const next = new Set(current);
      next.add(lotKey);
      return next;
    });

    try {
      const response = await enqueueAuctionMutation(() => postAuctionClaimLotApi(lot, postClaimSelection));
      const serverLot = response?.data?.lot;
      if (serverLot && serverLot.id) {
        const sameLot = (currentLot) => {
          const currentSourceShiftId = normalizeSchedulePlanId(currentLot?.source_schedule_shift_id);
          const currentSourcePlanId = normalizeSchedulePlanId(currentLot?.source_schedule_plan_id);
          const serverSourceShiftId = normalizeSchedulePlanId(serverLot?.source_schedule_shift_id);
          const serverSourcePlanId = normalizeSchedulePlanId(serverLot?.source_schedule_plan_id);
          if (currentSourceShiftId && serverSourceShiftId && currentSourceShiftId === serverSourceShiftId) {
            return !serverSourcePlanId || !currentSourcePlanId || serverSourcePlanId === currentSourcePlanId;
          }
          return getAuctionLotActionKey(currentLot) === getAuctionLotActionKey(serverLot);
        };
        setLots((currentLots) => currentLots.map((l) => (
          sameLot(l) ? { ...l, ...serverLot } : l
        )));
        setPeriodPreviewLots((currentLots) => currentLots.map((l) => (
          sameLot(l) ? { ...l, ...serverLot } : l
        )));
      }
      notify('Смена забрана и сохранена в графики');
      setPostClaimConfirmLot(null);
      setPostClaimSelection({ start_time: '', end_time: '' });
      fetchSnapshot({ silent: true });
      if (!isViewingActivePeriod && selectedViewSchedulePlanId) {
        fetchPeriodPreview(selectedViewSchedulePlanId, {});
      }
    } catch (error) {
      const message = error?.response?.data?.error || 'Не удалось забрать смену';
      notifyClaimError(message);
      fetchSnapshot({ silent: true });
      if (!isViewingActivePeriod && selectedViewSchedulePlanId) {
        fetchPeriodPreview(selectedViewSchedulePlanId, {});
      }
    } finally {
      setPostClaimingLotIds((current) => {
        if (!current.has(lotKey)) return current;
        const next = new Set(current);
        next.delete(lotKey);
        return next;
      });
    }
  }, [
    enqueueAuctionMutation,
    fetchPeriodPreview,
    fetchSnapshot,
    isViewingActivePeriod,
    notify,
    notifyClaimError,
    postAuctionClaimLotApi,
    postAuctionClaimOptionsByLotId,
    postClaimConfirmLot,
    postClaimSelection,
    postClaimingLotIds,
    selectedViewSchedulePlanId
  ]);

  const handleToggleAdminNotify = useCallback(async (nextValue) => {
    if (isSavingNotifyToggle) return;
    setIsSavingNotifyToggle(true);
    const previous = notifyPostClaimEnabled;
    setNotifyPostClaimEnabled(nextValue);
    try {
      await axios.put(
        `${apiRoot}/api/shift_auction/admin_notify_settings`,
        { auction_post_claim_notify_enabled: nextValue },
        { headers: buildHeaders() }
      );
      notify(nextValue ? 'Уведомления включены' : 'Уведомления выключены');
    } catch (error) {
      setNotifyPostClaimEnabled(previous);
      const message = error?.response?.data?.error || 'Не удалось сохранить настройку';
      notify(message, 'error');
    } finally {
      setIsSavingNotifyToggle(false);
    }
  }, [apiRoot, buildHeaders, isSavingNotifyToggle, notify, notifyPostClaimEnabled]);

  useEffect(() => {
    if (!selectedViewPostAuctionActive) return undefined;
    setPostAuctionNowMs(Date.now());
    const interval = window.setInterval(() => setPostAuctionNowMs(Date.now()), 30000);
    return () => window.clearInterval(interval);
  }, [selectedViewPostAuctionActive]);

  const fetchMyClaims = useCallback(async () => {
    if (!apiRoot || !user?.id) return;
    setMyClaimsLoading(true);
    setMyClaimsError('');
    try {
      const response = await axios.get(`${apiRoot}/api/shift_auction/my_post_claims`, {
        headers: buildHeaders()
      });
      setMyClaims(Array.isArray(response?.data?.claims) ? response.data.claims : []);
      setMyClaimsFetchedAt(Date.now());
    } catch (error) {
      setMyClaimsError(error?.response?.data?.error || 'Не удалось загрузить взятые смены');
    } finally {
      setMyClaimsLoading(false);
    }
  }, [apiRoot, buildHeaders, user?.id]);

  const openMyClaims = useCallback(() => {
    setMyClaimsOpen(true);
    fetchMyClaims();
  }, [fetchMyClaims]);

  const handleCancelMyClaim = useCallback(async (claim) => {
    const key = getPostClaimKey(claim);
    if (!key || cancelingClaimKey) return;
    setCancelingClaimKey(key);
    try {
      const payload = (claim.plan_id && claim.source_schedule_shift_id)
        ? { plan_id: claim.plan_id, source_schedule_shift_id: claim.source_schedule_shift_id }
        : { lot_id: claim.lot_id };
      await enqueueAuctionMutation(() => axios.post(
        `${apiRoot}/api/shift_auction/cancel_post_claim`,
        payload,
        { headers: buildHeaders() }
      ));
      notify('Смена отменена и снова доступна для других');
      setMyClaims((current) => current.filter((item) => getPostClaimKey(item) !== key));
      fetchSnapshot({ silent: true });
      if (!isViewingActivePeriod && selectedViewSchedulePlanId) {
        fetchPeriodPreview(selectedViewSchedulePlanId, {});
      }
      fetchMyClaims();
    } catch (error) {
      notifyClaimError(error?.response?.data?.error || 'Не удалось отменить смену');
      fetchMyClaims();
    } finally {
      setCancelingClaimKey('');
    }
  }, [
    apiRoot,
    buildHeaders,
    cancelingClaimKey,
    enqueueAuctionMutation,
    fetchMyClaims,
    fetchPeriodPreview,
    fetchSnapshot,
    isViewingActivePeriod,
    notify,
    notifyClaimError,
    selectedViewSchedulePlanId
  ]);

  // Посекундный тик для обратного отсчёта окна отмены, пока панель открыта.
  useEffect(() => {
    if (!myClaimsOpen) return undefined;
    setClaimsNowMs(Date.now());
    const interval = window.setInterval(() => setClaimsNowMs(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [myClaimsOpen]);

  const openReleaseConfirm = useCallback((lotsToRelease) => {
    const options = (Array.isArray(lotsToRelease) ? lotsToRelease : [lotsToRelease])
      .filter((lot) => lot && lot.id);
    if (!options.length) return;
    setReleaseConfirmOptions(options);
    setReleaseConfirmLot(options[0]);
  }, []);

  const closeReleaseConfirm = useCallback(() => {
    setReleaseConfirmLot(null);
    setReleaseConfirmOptions([]);
  }, []);

  const handleReleaseLot = useCallback(async () => {
    const lot = releaseConfirmLot;
    if (!canClaim || !apiRoot || !lot?.id) return;
    const numericId = Number(lot.id);
    if (!Number.isFinite(numericId)) return;

    const prevLot = (lotsRef.current || []).find((l) => Number(l?.id) === numericId) || lot;

    setReleasingLotId(numericId);
    setLots((currentLots) => currentLots.map((l) => (
      Number(l.id) === numericId
        ? {
            ...l,
            status: 'available',
            claimed_by: null,
            claimed_at: null,
            claimed_by_name: '',
            _optimistic: true
          }
        : l
    )));
    setReleaseConfirmLot(null);
    setReleaseConfirmOptions([]);

    try {
      const response = await enqueueAuctionMutation(() => axios.post(
        `${apiRoot}/api/shift_auction/test_lots/claim`,
        { lot_id: numericId, action: 'release' },
        { headers: buildHeaders() }
      ));
      const serverLot = response?.data?.lot;
      if (serverLot && serverLot.id) {
        setLots((currentLots) => currentLots.map((l) => (
          Number(l.id) === Number(serverLot.id)
            ? { ...l, ...serverLot, _optimistic: false }
            : l
        )));
      }
    } catch (error) {
      const code = error?.response?.data?.code;
      const message = error?.response?.data?.error;

      setLots((currentLots) => currentLots.map((l) => (
        Number(l.id) === numericId && l._optimistic
          ? { ...prevLot, _optimistic: false }
          : l
      )));

      await fetchSnapshot({ silent: true });

      const silentCodes = new Set(['LOT_NOT_CLAIMED', 'LOT_NOT_OWNED', 'AUCTION_NOT_OPEN']);
      if (!silentCodes.has(code)) {
        notifyClaimError(message || 'Не удалось вернуть смену');
      }
    } finally {
      setReleasingLotId(null);
    }
  }, [apiRoot, buildHeaders, canClaim, enqueueAuctionMutation, fetchSnapshot, notifyClaimError, releaseConfirmLot]);

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
        await enqueueAuctionMutation(() => axios.delete(`${apiRoot}/api/shift_auction/test_day_off`, requestConfig));
      } else {
        await enqueueAuctionMutation(() => axios.post(`${apiRoot}/api/shift_auction/test_day_off`, { date }, { headers: buildHeaders() }));
      }
      await fetchSnapshot({ silent: true });
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось изменить выходной', 'error');
    } finally {
      setDayOffLoadingDate('');
    }
  }, [apiRoot, buildHeaders, canChoose, enqueueAuctionMutation, fetchSnapshot, manualDayOffLimit, myBlockedDateMap, myDayOffs, notify, selectedManualDayOffCount]);

  const renderStatusBar = () => {
    const showWorkload = !canMonitor && canUseAuction;
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

  const releaseOptions = releaseConfirmOptions.length
    ? releaseConfirmOptions
    : (releaseConfirmLot ? [releaseConfirmLot] : []);
  const hasMultipleReleaseOptions = releaseOptions.length > 1;

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
              <h1 className="text-xl font-semibold text-slate-950 sm:text-2xl">
                Аукцион смен
                {isTopupActive ? (
                  <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-violet-300 bg-violet-100 px-2 py-0.5 align-middle text-[11px] font-semibold text-violet-800 sm:text-xs">
                    <Plus size={12} />
                    Режим добора
                  </span>
                ) : null}
              </h1>
              <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-600 sm:text-sm sm:leading-6">
                {isTopupActive
                  ? <>Идёт <b className="text-violet-800">добор смен</b>{settings.topup_started_at ? <> с {formatDateTimeLabel(settings.topup_started_at)}</> : null}{settings.topup_started_by_name ? <> · включил {settings.topup_started_by_name}</> : null}. Можно брать дополнительные смены сверх нормы, если они не пересекаются по времени с уже выбранными.</>
                  : 'Тестовый realtime-раздел для проверки будущего выбора утвержденных смен по направлению.'
                }
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
            <button
              type="button"
              onClick={() => setIsInstructionsOpen(true)}
              className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 text-xs font-semibold text-blue-800 shadow-sm transition hover:bg-blue-100 sm:h-10 sm:flex-none sm:px-4 sm:text-sm"
              aria-label="Открыть инструкцию"
            >
              <BookOpen size={16} />
              Инструкция
            </button>
            {!canMonitor && canUseAuction ? (
              <button
                type="button"
                onClick={openMyClaims}
                className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-lg border border-violet-200 bg-violet-50 px-3 text-xs font-semibold text-violet-800 shadow-sm transition hover:bg-violet-100 sm:h-10 sm:flex-none sm:px-4 sm:text-sm"
                aria-label="Мои взятые смены"
              >
                <Hand size={16} />
                Мои доп. смены
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

        {canMonitor && (
          <nav className="inline-flex w-fit max-w-full overflow-x-auto rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
            {[
              canManage ? { id: 'settings', label: 'Настройки', icon: Settings2 } : null,
              { id: 'monitoring', label: 'Мониторинг смен', icon: MousePointerClick },
              { id: 'shifts_table', label: 'Таблица смен', icon: Table },
              { id: 'progress', label: 'Прогресс', icon: Users, badge: operatorWorkloadStats.total > 0 ? operatorWorkloadStats.total : null },
              { id: 'journal', label: 'Журнал', icon: History, badge: journalTotal > 0 ? journalTotal : null }
            ].filter(Boolean).map((tab) => {
              const Icon = tab.icon;
              const active = monitorTab === tab.id;
              return (
                <button
                  type="button"
                  key={`monitor-tab-${tab.id}`}
                  onClick={() => setMonitorTab(tab.id)}
                  className={`inline-flex h-9 items-center gap-2 whitespace-nowrap rounded-md px-3 text-sm font-semibold transition sm:h-10 sm:px-4 ${
                    active
                      ? 'bg-slate-900 text-white shadow-sm'
                      : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                  }`}
                >
                  <Icon size={16} />
                  <span>{tab.label}</span>
                  {tab.badge !== null && tab.badge !== undefined ? (
                    <span className={`rounded px-1.5 text-[10px] font-bold tabular-nums ${
                      active ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-600'
                    }`}>{tab.badge}</span>
                  ) : null}
                </button>
              );
            })}
          </nav>
        )}

        {canUseAuction && (!canMonitor || monitorTab === 'monitoring') && availablePeriods.length ? (
          <AuctionWeekSelector
            periods={availablePeriods}
            selectedPlanId={selectedViewSchedulePlanId}
            activePlanId={activeSchedulePlanId}
            onSelect={handleViewPeriodSelect}
            loading={periodPreviewLoading}
            error={periodPreviewError}
            previewOnly={!isViewingActivePeriod}
          />
        ) : null}

        {canManage && monitorTab === 'monitoring' && (
          <label className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-white px-3 py-3 shadow-sm sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:px-4">
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-slate-900">Получать уведомления о взятии смены</span>
              <span className="block text-xs text-slate-500 sm:text-sm">
                Когда оператор берёт дополнительную смену после окончания аукциона, в Telegram придёт уведомление с данными.
              </span>
            </span>
            <input
              type="checkbox"
              checked={notifyPostClaimEnabled}
              onChange={(event) => handleToggleAdminNotify(event.target.checked)}
              disabled={isSavingNotifyToggle}
              className="h-5 w-5 shrink-0 rounded border-slate-300 text-orange-600 focus:ring-orange-500 disabled:opacity-60"
            />
          </label>
        )}

        {canUseAuction && (!canMonitor || monitorTab === 'monitoring') && (
          <section className={`grid min-w-0 gap-3 ${
            canMonitor
              ? ''
              : 'xl:grid-cols-[260px_minmax(0,1fr)] xl:gap-5'
          }`}>
            {!canMonitor ? (
              <aside className="grid min-w-0 gap-2 xl:block xl:space-y-3">
              <div className="min-w-0 max-w-full overflow-hidden rounded-lg border border-slate-200 bg-white p-3 shadow-sm sm:p-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <ListChecks size={17} className="text-blue-700" />
                  Мои выходные
                </div>
                <p className="mt-1 text-xs text-slate-500 sm:mt-2 sm:text-sm">Можно выбрать до 2 дней периода. Статусные периоды занимают эту квоту.</p>
                <div className="mt-2 flex min-w-0 max-w-full gap-1.5 overflow-x-auto overscroll-x-contain pb-1 xl:block xl:space-y-2 xl:overflow-visible xl:pb-0">
                  {lotDates.length ? lotDates.map((date) => {
                    const active = monitoredMyDayOffs.includes(date);
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
                <h2 className="text-base font-semibold text-slate-950 sm:text-lg">{canMonitor ? 'Мониторинг смен' : 'Доступные смены'}</h2>
                <p className="mt-1 text-xs text-slate-600 sm:text-sm">
                  {canMonitor
                    ? (runtimeStatus === 'scheduled'
                      ? <>Аукцион откроется через <AuctionCountdownText target={settings.starts_at} />.</>
                      : runtimeStatus === 'open'
                        ? 'Realtime-мониторинг показывает все смены и кто их забрал.'
                        : runtimeStatus === 'paused'
                          ? 'Аукцион временно приостановлен.'
                          : 'Сейчас аукцион закрыт.')
                    : (runtimeStatus === 'scheduled'
                      ? <>Аукцион откроется через <AuctionCountdownText target={settings.starts_at} />.</>
                      : runtimeStatus === 'open'
                        ? 'Нажмите “Забрать”, чтобы закрепить смену. У остальных участников она сразу станет недоступной.'
                        : runtimeStatus === 'paused'
                          ? 'Аукцион временно приостановлен администратором.'
                          : 'Сейчас аукцион закрыт.')}
                </p>
              </div>
              <div className="min-w-0 sm:p-5">
                {!isViewingActivePeriod ? (
                  <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800 sm:text-sm">
                    Предпросмотр недели {selectedViewPeriod ? formatAuctionPeriodLabel(selectedViewPeriod) : ''}. Выбор смен доступен только на активной неделе аукциона.
                  </div>
                ) : null}
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
                                    const isDayOff = monitoredMyDayOffs.includes(date);
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
                                            canManage={canMonitor}
                                            claimingLotIds={claimingLotIds}
                                            onClaimLot={handleClaimLot}
                                            userId={user?.id}
                                            claimBlockReason={!isViewingActivePeriod && !selectedViewPostAuctionActive ? 'Выбор доступен только на активной неделе аукциона' : (claimBlockReasonByLotId.get(getAuctionLotActionKey(lot)) || '')}
                                            postAuctionActive={selectedViewPostAuctionActive}
                                            postAuctionNowMs={postAuctionNowMs}
                                            postClaimingLotIds={postClaimingLotIds}
                                            postAuctionClaimOption={postAuctionClaimOptionsByLotId.get(getAuctionLotActionKey(lot))}
                                            onRequestPostAuctionClaim={handleRequestPostAuctionClaim}
                                            onShowDetail={canMonitor ? setShiftDetailLot : undefined}
                                            isPartialRemainder={lot.status === 'available' && Array.isArray(lot.claim_segments) && lot.claim_segments.length > 0}
                                          />
                                        ) : (
                                          <div className={`h-6 rounded border border-dashed sm:h-8 ${isBlocked ? 'border-rose-100 bg-rose-50/70' : isDayOff ? 'border-blue-100 bg-blue-50/60' : 'border-transparent bg-slate-50/70'}`} />
                                        )}
                                      </td>
                                    );
                                  })}
                                </tr>
                              ))}
                              {canMonitor && isViewingActivePeriod && runtimeStatus !== 'closed' && runtimeStatus !== 'disabled' ? (
                                <tr key={`${group.id}-add`}>
                                  {lotDates.map((date) => (
                                    <td
                                      key={`${group.id}-add-${date}`}
                                      style={auctionDayColumnStyle}
                                      className={`border-b border-r border-slate-200 p-px align-top last:border-r-0 sm:p-1 ${activeDayDate === date ? 'bg-blue-50/40' : 'bg-white'}`}
                                    >
                                      <button
                                        type="button"
                                        onClick={() => openAddShiftModal(group, date)}
                                        title={`Добавить смену · ${group.title} · ${formatDateLabel(date)}`}
                                        className="flex h-6 w-full items-center justify-center rounded border border-dashed border-violet-300 bg-violet-50 text-violet-600 transition hover:bg-violet-100 hover:text-violet-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 sm:h-8"
                                      >
                                        <Plus size={14} />
                                      </button>
                                    </td>
                                  ))}
                                </tr>
                              ) : null}
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
                              const tone = canMonitor
                                ? (item.claimed >= item.total && item.total > 0 ? 'border-emerald-300 bg-emerald-50 text-emerald-800' : item.claimed > 0 ? 'border-blue-300 bg-blue-50 text-blue-800' : 'border-slate-200 bg-white text-slate-600')
                                : item.state === 'shift'
                                  ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                                  : item.state === 'blocked'
                                    ? 'border-rose-300 bg-rose-50 text-rose-800'
                                    : item.state === 'off'
                                      ? 'border-blue-300 bg-blue-50 text-blue-800'
                                      : 'border-slate-200 bg-white text-slate-600';
                              const statusText = canMonitor
                                ? `${item.claimed}/${item.total}`
                                : item.state === 'shift'
                                  ? 'Смена'
                                  : item.state === 'off'
                                    ? 'Вых.'
                                    : 'Пусто';
                              const finalStatusText = !canMonitor && item.state === 'blocked'
                                ? item.blockedLabel
                                : !canMonitor && item.state === 'locked' ? 'Занято' : statusText;
                              const myShiftCount = Number(item.myClaimed || 0);
                              const myShiftLabel = !canMonitor && item.state === 'shift'
                                ? (myShiftCount > 1 ? `Смен: ${myShiftCount}` : formatCompactAuctionShiftLabel(item.myClaimedLot))
                                : '';
                              const myShiftDuration = !canMonitor && item.state === 'shift'
                                ? `${formatAuctionHours(item.myClaimedNetMinutes)} ч`
                                : '';
                              const hoverTone = active ? 'hover:bg-blue-100' : 'hover:bg-slate-50';
                              const canReleaseHere = !canMonitor && canClaim && item.state === 'shift' && item.myClaimedLots?.length;
                              const onCellClick = canReleaseHere
                                ? () => openReleaseConfirm(item.myClaimedLots)
                                : () => scrollToDay(item.date);
                              const cellTitle = canReleaseHere
                                ? `${formatDateLabel(item.date)} · ${myShiftCount > 1 ? 'выберите смену для возврата' : 'нажмите, чтобы вернуть смену'}`
                                : item.isBlocked
                                  ? `${formatDateLabel(item.date)} · ${item.blockedLabel}`
                                  : formatDateLabel(item.date);
                              return (
                                <button
                                  key={item.date}
                                  type="button"
                                  onClick={onCellClick}
                                  data-auction-date-bar-cell
                                  aria-current={active ? 'true' : undefined}
                                  className={`h-12 min-w-0 border-r border-slate-200 px-1 py-1 text-center transition-colors last:border-r-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-inset sm:h-[56px] sm:px-2 sm:py-1.5 ${tone} ${hoverTone} ${active ? 'bg-blue-100 text-blue-900' : ''}`}
                                  title={cellTitle}
                                >
                                  <span className="block truncate text-[10px] font-semibold leading-4 sm:text-[11px]">{formatShortDateLabel(item.date)}</span>
                                  {!canMonitor && item.state === 'shift' ? (
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
                    {!isViewingActivePeriod && periodPreviewLoading
                      ? 'Загружаю выбранную неделю...'
                      : lotDates.length
                      ? 'Для выбранных дней сейчас нет доступных смен.'
                      : canManage
                        ? 'Выберите недельный план и начните аукцион заново.'
                        : canMonitor
                          ? 'Аукцион пока не запущен.'
                        : 'Пока нет доступных смен.'}
                  </div>
                )}
              </div>
            </main>
            {canMonitor && isAdminDayDetailsOpen && activeDayDate ? (
              <aside className="fixed inset-x-3 bottom-[66px] z-40 max-h-[58vh] overflow-hidden rounded-2xl bg-white/95 shadow-2xl ring-1 ring-black/5 backdrop-blur-xl xl:inset-x-auto xl:bottom-auto xl:right-3 xl:top-24 xl:w-[360px] xl:max-h-[calc(100vh-7rem)]">
                <div className="flex items-start justify-between gap-3 border-b border-slate-200/70 px-4 py-3.5">
                  <div className="min-w-0">
                    <div className="truncate text-[15px] font-semibold tracking-tight text-slate-900">
                      {formatDateLabel(activeDayDate)}
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500">
                      {adminActiveDayClaimCount ? `Взято смен: ${adminActiveDayClaimCount}` : 'Нет взятых смен'}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsAdminDayDetailsOpen(false)}
                    className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-95"
                    title="Закрыть"
                  >
                    <X size={16} />
                  </button>
                </div>
                <div className="max-h-[calc(58vh-64px)] overflow-y-auto p-3 xl:max-h-[calc(100vh-11rem)]">
                  {adminActiveDayClaimLots.length ? (
                    <ul className="space-y-1.5">
                      {adminActiveDayClaimLots.map((row) => (
                        <li key={`admin-day-claim-${row.key}`}>
                          <button
                            type="button"
                            onClick={() => row.operatorId ? setDrilldownOperatorId(row.operatorId) : null}
                            disabled={!row.operatorId}
                            className="flex w-full items-center gap-3 rounded-xl border border-slate-200/80 bg-white px-3 py-2 text-left shadow-sm transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-default disabled:hover:border-slate-200/80 disabled:hover:bg-white"
                            title="Открыть взятые смены оператора"
                          >
                            <span className="shrink-0 rounded-lg bg-slate-100 px-2 py-1 text-[12px] font-semibold tabular-nums text-slate-700">
                              {row.timeLabel}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[13px] font-medium text-slate-900">{row.operatorName}</span>
                              {row.partial ? (
                                <span className="mt-0.5 inline-flex items-center gap-1 rounded-full bg-orange-100 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-orange-700">
                                  <span className="h-1.5 w-1.5 rounded-full bg-current" />
                                  добор · часть из {row.originalLabel}
                                </span>
                              ) : null}
                            </span>
                            <span className="shrink-0 text-[12px] tabular-nums text-slate-400">{formatAuctionHours(row.netMinutes)} ч</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50/60 px-4 py-10 text-center text-sm text-slate-500">
                      В этот день пока никто не взял смены.
                    </div>
                  )}
                </div>
              </aside>
            ) : null}
          </section>
        )}

        {canMonitor && monitorTab === 'shifts_table' && (
          <ShiftAuctionShiftsTable
            operators={monitoredOperators}
            workloads={monitoredParticipantWorkloads}
            lots={monitoredLots}
            lotDates={lotDates}
            canEdit={canManage}
            apiRoot={apiRoot}
            buildHeaders={buildHeaders}
            notify={notify}
            onActionComplete={async () => {
              if (isViewingActivePeriod) {
                await fetchSnapshot({ silent: true });
              } else if (selectedViewSchedulePlanId) {
                await fetchPeriodPreview(selectedViewSchedulePlanId, {});
              }
            }}
          />
        )}

        {canMonitor && monitorTab === 'progress' && (
          <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-200 px-3 py-3 sm:px-5 sm:py-4">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div className="min-w-0">
                  <h2 className="text-base font-semibold text-slate-950 sm:text-lg">Прогресс операторов</h2>
                  <p className="mt-1 text-xs text-slate-600 sm:text-sm">
                    Норма зависит от ставки оператора и статусных периодов. Отстающие — наверху.
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  {[
                    { id: 'all', label: 'Все', count: operatorWorkloadStats.total },
                    { id: 'lagging', label: 'Отстают', count: operatorWorkloadStats.empty + operatorWorkloadStats.lagging },
                    { id: 'complete', label: 'Норма', count: operatorWorkloadStats.complete },
                    { id: 'over', label: 'Перебор', count: operatorWorkloadStats.over },
                    { id: 'empty', label: 'Пусто', count: operatorWorkloadStats.empty }
                  ].map((chip) => {
                    const active = operatorWorkloadFilter === chip.id;
                    return (
                      <button
                        type="button"
                        key={`op-wk-filter-${chip.id}`}
                        onClick={() => setOperatorWorkloadFilter(chip.id)}
                        className={`inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-semibold transition sm:h-8 sm:px-3 ${
                          active
                            ? 'border-blue-500 bg-blue-50 text-blue-700'
                            : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-800'
                        }`}
                      >
                        <span>{chip.label}</span>
                        <span className={`tabular-nums ${active ? 'text-blue-600' : 'text-slate-400'}`}>{chip.count}</span>
                      </button>
                    );
                  })}
                  <input
                    type="search"
                    value={operatorWorkloadQuery}
                    onChange={(event) => setOperatorWorkloadQuery(event.target.value)}
                    placeholder="Поиск оператора"
                    className="h-7 w-40 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-800 placeholder:text-slate-400 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100 sm:h-8 sm:w-56 sm:text-sm"
                  />
                </div>
              </div>
            </div>
            <div className="px-3 py-3 sm:px-5 sm:py-4">
              {filteredOperatorWorkloads.length === 0 ? (
                <div className="rounded-md border border-dashed border-slate-200 bg-slate-50 px-3 py-6 text-center text-sm text-slate-500">
                  Под фильтр операторов не нашлось.
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
                  {filteredOperatorWorkloads.map((row) => {
                    const progressWidth = clampNumber(row.progress, 0, 100);
                    const progressTone = row.status === 'over'
                      ? 'bg-rose-500'
                      : row.status === 'complete'
                        ? 'bg-emerald-500'
                        : row.status === 'partial'
                          ? 'bg-blue-600'
                          : 'bg-slate-300';
                    const chipClass = row.status === 'over'
                      ? 'bg-rose-50 text-rose-700'
                      : row.status === 'complete'
                        ? 'bg-emerald-50 text-emerald-700'
                        : row.status === 'partial'
                          ? 'bg-blue-50 text-blue-700'
                          : 'bg-slate-100 text-slate-600';
                    const chipText = row.status === 'over'
                      ? `+${formatAuctionHours(row.over_minutes || 0)} ч`
                      : row.status === 'complete'
                        ? 'Норма'
                        : row.status === 'partial'
                          ? `-${formatAuctionHours(row.remaining_minutes || 0)} ч`
                          : 'Пусто';
                    const subtitleParts = [];
                    if (row.supervisor_name) subtitleParts.push(row.supervisor_name);
                    if (row.direction) subtitleParts.push(row.direction);
                    const rateLabel = row.rate && Math.abs(Number(row.rate) - 1) > 0.001
                      ? ` · ст. ${formatRate(row.rate)}`
                      : '';
                    const subtitle = `${subtitleParts.join(' · ')}${rateLabel}`;
                    const meta = [
                      `${row.lots_claimed_count || 0} смен`,
                      row.blocked_days ? `закрыто ${row.blocked_days} дн` : null,
                      row.selected_day_offs ? `вых ${row.selected_day_offs}` : null
                    ].filter(Boolean).join(' · ');
                    return (
                      <button
                        type="button"
                        key={`op-workload-${row.operator_id}`}
                        onClick={() => setDrilldownOperatorId(Number(row.operator_id))}
                        className="w-full rounded-md border border-slate-200 bg-white p-3 text-left transition hover:border-blue-300 hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-200"
                        title="Посмотреть взятые смены оператора"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="truncate text-sm font-semibold text-slate-950">{row.name}</div>
                            {subtitle ? (
                              <div className="mt-0.5 truncate text-[11px] text-slate-500">{subtitle}</div>
                            ) : null}
                          </div>
                          <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold sm:text-[11px] ${chipClass}`}>
                            {chipText}
                          </span>
                        </div>
                        <div className="mt-2 flex items-center justify-between gap-2 text-xs tabular-nums">
                          <span className="font-semibold text-slate-900">
                            {formatAuctionHours(row.claimed_net_minutes || 0)} / {formatAuctionHours(row.norm_minutes || 0)} ч
                          </span>
                          <span className="text-slate-500">{Math.round(row.progress)}%</span>
                        </div>
                        <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-100">
                          <div className={`h-full rounded-full ${progressTone}`} style={{ width: `${progressWidth}%` }} />
                        </div>
                        {meta ? (
                          <div className="mt-2 truncate text-[11px] text-slate-500">{meta}</div>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </section>
        )}

        {canManage && monitorTab === 'settings' && (
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
                  {runtimeStatus === 'open' ? (
                    <button
                      type="button"
                      onClick={() => handleAuctionControl('pause')}
                      disabled={isControllingAuction}
                      className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 text-xs font-semibold text-amber-800 transition hover:bg-amber-100 disabled:cursor-wait disabled:opacity-60 sm:h-10 sm:px-4 sm:text-sm"
                    >
                      <PauseCircle size={16} />
                      Приостановить
                    </button>
                  ) : null}
                  {runtimeStatus === 'paused' ? (
                    <button
                      type="button"
                      onClick={() => handleAuctionControl('resume')}
                      disabled={isControllingAuction}
                      className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 text-xs font-semibold text-emerald-800 transition hover:bg-emerald-100 disabled:cursor-wait disabled:opacity-60 sm:h-10 sm:px-4 sm:text-sm"
                    >
                      <PlayCircle size={16} />
                      Возобновить
                    </button>
                  ) : null}
                  {runtimeStatus === 'open' ? (
                    <button
                      type="button"
                      onClick={handleToggleTopup}
                      disabled={isTogglingTopup}
                      title={settings.topup_started_at
                        ? `Режим добора включён ${formatDateTimeLabel(settings.topup_started_at)}${settings.topup_started_by_name ? ` (${settings.topup_started_by_name})` : ''}`
                        : 'Перевести аукцион в режим добора смен — операторы смогут забирать смены сверх нормы, если они не пересекаются по времени с уже взятыми.'}
                      className={`inline-flex h-9 items-center justify-center gap-2 rounded-lg border px-3 text-xs font-semibold transition disabled:cursor-wait disabled:opacity-60 sm:h-10 sm:px-4 sm:text-sm ${
                        settings.topup_started_at
                          ? 'border-violet-300 bg-violet-100 text-violet-900 hover:bg-violet-200'
                          : 'border-violet-200 bg-violet-50 text-violet-800 hover:bg-violet-100'
                      }`}
                    >
                      <Plus size={16} />
                      {settings.topup_started_at ? 'Отключить добор' : 'Включить добор'}
                    </button>
                  ) : null}
                  {['scheduled', 'open', 'paused'].includes(runtimeStatus) ? (
                    <button
                      type="button"
                      onClick={() => handleAuctionControl('finish')}
                      disabled={isControllingAuction}
                      className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 text-xs font-semibold text-rose-800 transition hover:bg-rose-100 disabled:cursor-wait disabled:opacity-60 sm:h-10 sm:px-4 sm:text-sm"
                    >
                      <Square size={15} />
                      Завершить
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={handleExportAuctionReport}
                    disabled={isExportingAuctionReport || !isViewingActivePeriod || !lots.length}
                    title={!isViewingActivePeriod ? 'Отчет доступен только для активной недели аукциона' : lots.length ? 'Выгрузить Excel-отчет по выбранному периоду аукциона' : 'Нет смен для выгрузки'}
                    className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 text-xs font-semibold text-blue-800 transition hover:bg-blue-100 disabled:cursor-wait disabled:opacity-60 sm:h-10 sm:px-4 sm:text-sm"
                  >
                    <Download size={16} />
                    {isExportingAuctionReport ? 'Выгрузка...' : 'Отчет Excel'}
                  </button>
                  {runtimeStatus === 'closed' ? (
                    <button
                      type="button"
                      onClick={handlePublishAuction}
                      disabled={isPublishingAuction}
                      className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 text-xs font-semibold text-emerald-800 transition hover:bg-emerald-100 disabled:cursor-wait disabled:opacity-60 sm:h-10 sm:px-4 sm:text-sm"
                    >
                      <Save size={16} />
                      {isPublishingAuction ? 'Сохранение...' : 'Сохранить в графики'}
                    </button>
                  ) : null}
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
              {settings.published_to_work_schedules_at ? (
                <p className="mt-3 text-xs text-emerald-700">
                  Итоги сохранены в графики работы {formatDateTimeLabel(settings.published_to_work_schedules_at)}
                  {settings.published_to_work_schedules_by_name ? ` · ${settings.published_to_work_schedules_by_name}` : ''}.
                </p>
              ) : null}
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
                    {restartablePeriods.length ? restartablePeriods.map((period) => {
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

                  <div className="mt-3">
                    <AuctionRangeCalendar
                      startsAt={draftStartsAt}
                      endsAt={draftEndsAt}
                      onStartsAtChange={setDraftStartsAt}
                      onEndsAtChange={setDraftEndsAt}
                    />
                  </div>

                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    <AuctionTimeField
                      label="Начало аукциона"
                      dateValue={draftStartsAtParts.date}
                      value={draftStartsAt}
                      onChange={setDraftStartsAt}
                      disabled={!draftStartsAtParts.date}
                    />
                    <AuctionTimeField
                      label="Завершение аукциона"
                      dateValue={draftEndsAtParts.date}
                      value={draftEndsAt}
                      onChange={setDraftEndsAt}
                      disabled={!draftEndsAtParts.date}
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
                  <div className="relative w-full">
                    <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="Поиск по оператору, направлению или СВ"
                      className="h-10 w-full rounded-lg border border-slate-200 pl-9 pr-3 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    />
                  </div>
                </div>

                <div className="max-h-[460px] overflow-auto rounded-lg border border-slate-200">
                  <div className="sticky top-0 z-10 flex flex-col gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
                    <div className="text-sm text-slate-500">
                      Выбрано: <span className="font-semibold text-slate-900">{selectedIds.size}</span>
                      {query.trim() ? (
                        <span className="ml-2 text-xs">Найдено: <span className="font-semibold text-slate-700">{filteredOperators.length}</span></span>
                      ) : null}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={selectAllFilteredOperators}
                        disabled={!filteredOperators.length || allFilteredOperatorsSelected}
                        className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 text-xs font-semibold text-blue-800 transition hover:bg-blue-100 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-50 disabled:text-slate-400"
                      >
                        <CheckCircle2 size={15} />
                        {query.trim() ? 'Выбрать найденных' : 'Выбрать все'}
                      </button>
                      <button
                        type="button"
                        onClick={clearSelectedOperators}
                        disabled={!selectedIds.size}
                        className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400"
                      >
                        <X size={15} />
                        Снять выбор
                      </button>
                    </div>
                  </div>
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

        {canMonitor && monitorTab === 'journal' && (
          <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="flex flex-col gap-3 border-b border-slate-200 px-3 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5 sm:py-4">
              <div>
                <div className="flex items-center gap-2">
                  <History size={17} className="text-blue-700" />
                  <h2 className="text-base font-semibold text-slate-950 sm:text-lg">Журнал аукциона</h2>
                </div>
                <p className="mt-1 text-xs text-slate-600 sm:text-sm">
                  Кто и когда забрал смену в выбранном недельном периоде.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-slate-500 tabular-nums sm:text-sm">
                  Всего: <b className="text-slate-900">{journalTotal}</b>
                </span>
                <button
                  type="button"
                  onClick={() => fetchJournalPage(journalPage)}
                  disabled={journalLoading}
                  className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-wait disabled:opacity-60 sm:h-9 sm:px-3"
                  title="Обновить"
                >
                  <RefreshCw size={14} className={journalLoading ? 'animate-spin' : ''} />
                  Обновить
                </button>
              </div>
            </div>
            <div className="overflow-x-auto">
              {journalError ? (
                <div className="px-3 py-6 text-center text-sm text-rose-600 sm:px-5">{journalError}</div>
              ) : journalLoading && journalEntries.length === 0 ? (
                <div className="px-3 py-8 text-center text-sm text-slate-500 sm:px-5">Загружаю журнал…</div>
              ) : journalEntries.length ? (
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
                    {journalEntries.map((entry) => (
                      <tr key={entry.id} className="text-slate-700 hover:bg-slate-50/60">
                        <td className="border-b border-slate-100 px-3 py-2 tabular-nums sm:px-5">{formatDateTimeLabel(entry.claimed_at)}</td>
                        <td className="border-b border-slate-100 px-3 py-2 font-medium text-slate-900">
                          <button
                            type="button"
                            onClick={() => entry.claimed_by ? setDrilldownOperatorId(Number(entry.claimed_by)) : null}
                            disabled={!entry.claimed_by}
                            className="text-left hover:text-blue-700 disabled:cursor-default disabled:hover:text-slate-900"
                          >
                            {entry.claimed_by_name || `#${entry.claimed_by || ''}`}
                          </button>
                        </td>
                        <td className="border-b border-slate-100 px-3 py-2">
                          {entry.shift_date ? (
                            <div className="flex flex-col gap-0.5">
                              <span>{`${formatShortDateLabel(entry.shift_date)} · ${entry.start_time || ''}-${entry.end_time || ''}`}</span>
                              {entry.is_post_auction ? (
                                <span
                                  title={entry.is_partial && entry.claim_start_time && entry.claim_end_time
                                    ? `Частичный добор: взято ${entry.claim_start_time}-${entry.claim_end_time} из ${entry.start_time}-${entry.end_time}`
                                    : 'Смена взята после аукциона (добор)'}
                                  className={`inline-flex w-fit items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold leading-none ${entry.is_partial ? 'bg-orange-100 text-orange-700' : 'bg-amber-50 text-amber-700'}`}
                                >
                                  <span className="h-1.5 w-1.5 rounded-full bg-current" />
                                  {entry.is_partial && entry.claim_start_time && entry.claim_end_time
                                    ? `добор · взято ${entry.claim_start_time}-${entry.claim_end_time}`
                                    : 'добор'}
                                </span>
                              ) : null}
                            </div>
                          ) : '—'}
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
            {journalTotal > journalPerPage ? (
              <div className="flex flex-col gap-2 border-t border-slate-200 px-3 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                <div className="text-xs text-slate-500 tabular-nums sm:text-sm">
                  Стр. {journalPage} из {Math.max(1, Math.ceil(journalTotal / journalPerPage))} · показано {journalEntries.length} из {journalTotal}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => fetchJournalPage(Math.max(1, journalPage - 1))}
                    disabled={journalLoading || journalPage <= 1}
                    className="inline-flex h-8 items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 sm:h-9"
                  >
                    <ChevronLeft size={14} />
                    Назад
                  </button>
                  <button
                    type="button"
                    onClick={() => fetchJournalPage(journalPage + 1)}
                    disabled={journalLoading || (journalPage * journalPerPage) >= journalTotal}
                    className="inline-flex h-8 items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 sm:h-9"
                  >
                    Вперёд
                    <ChevronRight size={14} />
                  </button>
                </div>
              </div>
            ) : null}
          </section>
        )}
      </div>

      {shiftDetailData ? (
        <div
          className="fixed inset-0 z-[68] flex items-center justify-center bg-slate-900/40 px-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="shift-detail-title"
          onClick={() => setShiftDetailLot(null)}
        >
          <div
            className="w-full max-w-md overflow-hidden rounded-2xl bg-white/95 shadow-2xl ring-1 ring-black/5 backdrop-blur-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 border-b border-slate-200/70 px-4 py-3.5">
              <div className="min-w-0">
                <h3 id="shift-detail-title" className="truncate text-[15px] font-semibold tracking-tight text-slate-900">
                  Смена {minutesToClockLabel(shiftDetailData.spanStart)}–{minutesToClockLabel(shiftDetailData.spanEnd)}
                </h3>
                <div className="mt-0.5 truncate text-xs text-slate-500">
                  {formatDateLabel(shiftDetailData.date)} · {shiftDetailData.claimedCount} взято
                  {shiftDetailData.freeMinutes > 0 ? ` · свободно ${formatAuctionHours(shiftDetailData.freeMinutes)} ч` : ''}
                </div>
                {shiftDetailLot?.added_by ? (
                  <div className="mt-1 inline-flex items-center gap-1 rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-semibold text-violet-700">
                    <span className="h-1.5 w-1.5 rounded-full bg-violet-600" />
                    Добавил: {shiftDetailLot.added_by_name || '—'}
                  </div>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => setShiftDetailLot(null)}
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-95"
                title="Закрыть"
              >
                <X size={16} />
              </button>
            </div>
            <div className="px-4 py-4">
              <div className="relative h-3 w-full overflow-hidden rounded-full bg-slate-100">
                {shiftDetailData.segments.map((seg, si) => {
                  const left = ((seg.start - shiftDetailData.spanStart) / shiftDetailData.span) * 100;
                  const width = ((seg.end - seg.start) / shiftDetailData.span) * 100;
                  return (
                    <span
                      key={`sd-seg-${si}`}
                      className="absolute inset-y-0 rounded-full ring-1 ring-white"
                      style={{
                        left: `${left}%`,
                        width: `${Math.max(2, width)}%`,
                        backgroundColor: seg.claimed
                          ? ADMIN_DAY_SEGMENT_COLORS[seg.colorIdx % ADMIN_DAY_SEGMENT_COLORS.length]
                          : '#E2E8F0',
                      }}
                      title={seg.claimed
                        ? `${seg.operatorName}: ${minutesToClockLabel(seg.start)}–${minutesToClockLabel(seg.end)}`
                        : `Свободно: ${minutesToClockLabel(seg.start)}–${minutesToClockLabel(seg.end)}`}
                    />
                  );
                })}
              </div>
              <div className="mt-1 flex justify-between text-[10px] tabular-nums text-slate-400">
                <span>{minutesToClockLabel(shiftDetailData.spanStart)}</span>
                <span>{minutesToClockLabel(shiftDetailData.spanEnd)}</span>
              </div>
              <div className="mt-3 space-y-0.5">
                {shiftDetailData.segments.map((seg, si) => (
                  seg.claimed ? (
                    <button
                      key={`sd-leg-${si}`}
                      type="button"
                      onClick={() => seg.operatorId ? setDrilldownOperatorId(seg.operatorId) : null}
                      disabled={!seg.operatorId}
                      className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition hover:bg-slate-50 disabled:hover:bg-transparent"
                      title="Открыть взятые смены оператора"
                    >
                      <span
                        className="h-2.5 w-2.5 shrink-0 rounded-full"
                        style={{ backgroundColor: ADMIN_DAY_SEGMENT_COLORS[seg.colorIdx % ADMIN_DAY_SEGMENT_COLORS.length] }}
                      />
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-800">{seg.operatorName || '—'}</span>
                      <span className="shrink-0 text-[12px] tabular-nums text-slate-500">
                        {minutesToClockLabel(seg.start)}–{minutesToClockLabel(seg.end)}
                      </span>
                      <span className="shrink-0 text-[12px] tabular-nums text-slate-400">{formatAuctionHours(seg.netMinutes)} ч</span>
                    </button>
                  ) : (
                    <div key={`sd-leg-${si}`} className="flex items-center gap-2.5 rounded-lg px-2 py-1.5">
                      <span className="h-2.5 w-2.5 shrink-0 rounded-full border border-slate-300 bg-slate-200" />
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-400">Свободно</span>
                      <span className="shrink-0 text-[12px] tabular-nums text-slate-400">
                        {minutesToClockLabel(seg.start)}–{minutesToClockLabel(seg.end)}
                      </span>
                    </div>
                  )
                ))}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {addShiftTarget ? (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-900/50 px-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="add-shift-title"
          onClick={() => { if (!isAddingShift) setAddShiftTarget(null); }}
        >
          <div
            className="w-full max-w-sm overflow-hidden rounded-2xl bg-white shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 border-b border-slate-200/70 px-4 py-3.5">
              <div className="min-w-0">
                <h3 id="add-shift-title" className="truncate text-[15px] font-semibold tracking-tight text-slate-900">
                  Добавить смену
                </h3>
                <div className="mt-0.5 truncate text-xs text-slate-500">
                  {formatDateLabel(addShiftTarget.date)} · {addShiftTarget.title}
                </div>
              </div>
              <button
                type="button"
                onClick={() => { if (!isAddingShift) setAddShiftTarget(null); }}
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-95"
                title="Закрыть"
              >
                <X size={16} />
              </button>
            </div>
            <div className="space-y-3 px-4 py-4">
              <div className="rounded-lg bg-violet-50 px-3 py-2 text-xs text-violet-700">
                Ставка <span className="font-semibold">{formatRate(addShiftTarget.rate)}</span> · длина смены фиксирована
                {addShiftTarget.shiftMinutes ? ` (${formatAuctionHours(addShiftTarget.shiftMinutes)} ч)` : ''}. Укажите только время начала.
              </div>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Начало</span>
                  <input
                    type="time"
                    value={addShiftTarget.night ? '20:00' : addShiftStart}
                    disabled={addShiftTarget.night || isAddingShift}
                    step={300}
                    onChange={(event) => setAddShiftStart(normalizeClockValue(event.target.value))}
                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm tabular-nums text-slate-900 focus:border-violet-400 focus:outline-none focus:ring-2 focus:ring-violet-100 disabled:bg-slate-50 disabled:text-slate-400"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Конец</span>
                  <input
                    type="text"
                    value={computeAuctionEndTime(addShiftTarget.night ? '20:00' : addShiftStart, addShiftTarget) || '—'}
                    readOnly
                    className="w-full cursor-default rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm tabular-nums text-slate-500"
                  />
                </label>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-slate-200/70 px-4 py-3">
              <button
                type="button"
                onClick={() => setAddShiftTarget(null)}
                disabled={isAddingShift}
                className="inline-flex h-9 items-center rounded-md border border-slate-200 px-3 text-sm font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
              >
                Отмена
              </button>
              <button
                type="button"
                onClick={handleSubmitAddShift}
                disabled={isAddingShift}
                className="inline-flex h-9 items-center gap-1.5 rounded-md bg-violet-600 px-3 text-sm font-semibold text-white transition hover:bg-violet-700 disabled:opacity-60"
              >
                <Plus size={15} />
                {isAddingShift ? 'Добавляю…' : 'Добавить'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {drilldownData ? (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-900/50 px-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="operator-drilldown-title"
          onClick={() => setDrilldownOperatorId(null)}
        >
          <div
            className="max-h-[85vh] w-full max-w-lg overflow-hidden rounded-lg bg-white shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 border-b border-slate-200 bg-slate-50 px-4 py-3 sm:px-5 sm:py-4">
              <div className="min-w-0">
                <h3 id="operator-drilldown-title" className="truncate text-base font-semibold text-slate-950 sm:text-lg">
                  {drilldownData.operator?.name || `Оператор #${drilldownData.operator_id}`}
                </h3>
                <div className="mt-0.5 truncate text-xs text-slate-500 sm:text-sm">
                  {[drilldownData.operator?.supervisor_name, drilldownData.operator?.direction].filter(Boolean).join(' · ') || 'Без направления'}
                  {drilldownData.operator?.rate && Math.abs(Number(drilldownData.operator.rate) - 1) > 0.001
                    ? ` · ставка ${formatRate(drilldownData.operator.rate)}`
                    : ''}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setDrilldownOperatorId(null)}
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-500 transition hover:bg-white hover:text-slate-800"
                title="Закрыть"
              >
                <X size={16} />
              </button>
            </div>
            {drilldownData.workload ? (
              <div className="border-b border-slate-200 px-4 py-3 sm:px-5">
                <div className="flex items-center justify-between gap-2 text-xs tabular-nums sm:text-sm">
                  <span className="font-semibold text-slate-900">
                    {formatAuctionHours(drilldownData.workload.claimed_net_minutes || 0)} / {formatAuctionHours(drilldownData.workload.norm_minutes || 0)} ч
                  </span>
                  <span className="text-slate-500">
                    {drilldownData.workload.lots_claimed_count || 0} смен
                    {drilldownData.workload.over_minutes > 0 ? ` · перебор ${formatAuctionHours(drilldownData.workload.over_minutes)} ч` : ''}
                    {drilldownData.workload.blocked_days ? ` · закрыто ${drilldownData.workload.blocked_days} дн` : ''}
                    {drilldownData.workload.selected_day_offs ? ` · вых ${drilldownData.workload.selected_day_offs}` : ''}
                  </span>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={`h-full rounded-full ${
                      drilldownData.workload.over_minutes > 0
                        ? 'bg-rose-500'
                        : drilldownData.workload.is_complete
                          ? 'bg-emerald-500'
                          : (drilldownData.workload.claimed_net_minutes || 0) > 0
                            ? 'bg-blue-600'
                            : 'bg-slate-300'
                    }`}
                    style={{ width: `${clampNumber(
                      drilldownData.workload.norm_minutes > 0
                        ? (drilldownData.workload.claimed_net_minutes / drilldownData.workload.norm_minutes) * 100
                        : (drilldownData.workload.claimed_net_minutes > 0 ? 100 : 0),
                      0,
                      100
                    )}%` }}
                  />
                </div>
              </div>
            ) : null}
            <div className="max-h-[60vh] overflow-y-auto px-4 py-3 sm:px-5">
              {drilldownData.claimed_lots.length ? (
                <ul className="space-y-1.5">
                  {drilldownData.claimed_lots.map((lot) => {
                    const minutes = getAuctionLotNetMinutes(lot);
                    const breakMinutes = getAuctionLotBreakMinutes(lot);
                    return (
                      <li
                        key={`drilldown-lot-${lot.id}`}
                        className="grid grid-cols-[90px_minmax(0,1fr)_auto] items-center gap-2 rounded-md border border-emerald-100 bg-emerald-50 px-3 py-2 text-sm text-slate-800"
                      >
                        <span className="font-semibold tabular-nums text-slate-900">
                          {lot.shift_date ? formatShortDateLabel(lot.shift_date) : '—'}
                        </span>
                        <span className="flex min-w-0 flex-wrap items-center gap-1 font-medium">
                          <span className="truncate">{formatAuctionLotEffectiveTimeRangeLabel(lot) || '—'}</span>
                          {breakMinutes ? <span className="text-xs font-normal text-slate-500">(перерыв {formatAuctionHours(breakMinutes)} ч)</span> : null}
                          <PostAuctionClaimBadge lot={lot} withOriginal />
                        </span>
                        <span className="text-xs tabular-nums text-emerald-700">
                          {formatAuctionHours(minutes)} ч
                        </span>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <div className="rounded-md border border-dashed border-slate-200 bg-slate-50 px-3 py-6 text-center text-sm text-slate-500">
                  Оператор пока не забрал ни одной смены.
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}

      <ShiftAuctionInstructionsModal
        open={isInstructionsOpen}
        role={instructionsRole}
        canSwitchRole={canSwitchInstructionsRole}
        onClose={closeInstructions}
      />

      {releaseConfirmLot ? (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-900/50 px-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="release-confirm-title"
          onClick={() => releasingLotId === null && closeReleaseConfirm()}
        >
          <div
            className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-5 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <h3 id="release-confirm-title" className="text-base font-semibold text-slate-950">
              {hasMultipleReleaseOptions ? 'Какую смену вернуть?' : 'Хотите ли вы вернуть эту смену?'}
            </h3>
            <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
              <div className="text-sm font-semibold text-slate-900">{formatDateLabel(releaseConfirmLot.shift_date)}</div>
              {hasMultipleReleaseOptions ? (
                <div className="mt-2 space-y-2">
                  {releaseOptions.map((lot) => {
                    const selected = Number(releaseConfirmLot?.id) === Number(lot.id);
                    return (
                      <button
                        key={lot.id}
                        type="button"
                        onClick={() => setReleaseConfirmLot(lot)}
                        disabled={releasingLotId !== null}
                        className={`flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left text-xs transition disabled:cursor-wait disabled:opacity-60 ${selected ? 'border-rose-300 bg-rose-50 text-rose-800' : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50'}`}
                      >
                        <span className="font-semibold tabular-nums">{formatAuctionLotEffectiveTimeRangeLabel(lot)}</span>
                        <span className="shrink-0 font-semibold tabular-nums">{formatAuctionHours(getAuctionLotNetMinutes(lot))} ч</span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="mt-0.5 text-xs text-slate-600">
                  {formatAuctionLotEffectiveTimeRangeLabel(releaseConfirmLot)}
                  {' · '}
                  {formatAuctionHours(getAuctionLotNetMinutes(releaseConfirmLot))} ч
                </div>
              )}
            </div>
            <p className="mt-3 text-xs leading-5 text-slate-600">
              {hasMultipleReleaseOptions
                ? 'Выбранная смена снова станет доступной для других операторов. Остальные смены в этот день останутся у вас.'
                : 'Смена снова станет доступной для других операторов. Это действие нельзя отменить.'}
            </p>
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={closeReleaseConfirm}
                disabled={releasingLotId !== null}
                className="inline-flex h-9 items-center justify-center rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 sm:text-sm"
              >
                Отмена
              </button>
              <button
                type="button"
                onClick={handleReleaseLot}
                disabled={releasingLotId !== null || !releaseConfirmLot}
                className="inline-flex h-9 items-center justify-center rounded-lg bg-rose-600 px-3 text-xs font-semibold text-white transition hover:bg-rose-700 disabled:cursor-wait disabled:bg-rose-400 sm:text-sm"
              >
                {releasingLotId !== null ? 'Возвращаю...' : 'Вернуть смену'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {postClaimConfirmLot ? (
        <PostAuctionPartialClaimModal
          lot={postClaimConfirmLot}
          option={postAuctionClaimOptionsByLotId.get(getAuctionLotActionKey(postClaimConfirmLot))}
          selection={postClaimSelection}
          onSelectionChange={setPostClaimSelection}
          onClose={handleClosePostAuctionClaim}
          onConfirm={handleConfirmPostAuctionClaim}
          inProgress={postClaimingLotIds.has(getAuctionLotActionKey(postClaimConfirmLot))}
        />
      ) : null}

      <IosModal
        open={myClaimsOpen}
        onClose={() => setMyClaimsOpen(false)}
        title="Мои доп. смены"
        subtitle="Недавно взятые дополнительные смены"
        maxWidth="max-w-md"
      >
        <div className="space-y-3">
          <div className="flex items-start gap-2.5 rounded-2xl bg-blue-50 px-3.5 py-3 text-[12.5px] leading-5 text-blue-800 ring-1 ring-blue-100">
            <Info size={16} className="mt-0.5 shrink-0 text-blue-500" />
            <span>Отменить взятую смену можно в течение <b>10 минут</b> после того, как вы её взяли. Позже смена закрепляется в графике — обратитесь к руководителю.</span>
          </div>

          {myClaimsLoading && !myClaims.length ? (
            <div className="flex items-center justify-center gap-2 py-12 text-[13px] text-slate-400">
              <RefreshCw size={15} className="animate-spin" />
              Загрузка…
            </div>
          ) : myClaimsError ? (
            <div className="rounded-2xl bg-rose-50 px-3.5 py-3 text-[13px] text-rose-600 ring-1 ring-rose-100">
              {myClaimsError}
            </div>
          ) : !myClaims.length ? (
            <div className="flex flex-col items-center gap-2 py-12 text-center">
              <div className="grid h-14 w-14 place-items-center rounded-full bg-slate-100 text-slate-400">
                <CalendarDays size={24} />
              </div>
              <div className="text-[14px] font-semibold text-slate-600">Нет недавно взятых смен</div>
              <div className="max-w-[260px] text-[12px] leading-5 text-slate-400">
                Здесь появятся дополнительные смены, которые вы возьмёте, — с возможностью отменить их в первые 10 минут.
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              {myClaims.map((claim) => {
                const key = getPostClaimKey(claim);
                const baseLeftMs = (Number(claim.cancel_seconds_left) || 0) * 1000;
                const elapsedMs = Math.max(0, claimsNowMs - myClaimsFetchedAt);
                const remainingMs = baseLeftMs - elapsedMs;
                const canCancel = remainingMs > 0;
                const busy = cancelingClaimKey === key;
                const totalSec = Math.max(0, Math.ceil(remainingMs / 1000));
                const countdown = `${Math.floor(totalSec / 60)}:${String(totalSec % 60).padStart(2, '0')}`;
                return (
                  <div key={key} className={`${iosCard} p-3.5`}>
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-[14.5px] font-semibold capitalize text-slate-900">
                          {formatDateLabel(claim.shift_date)}
                        </div>
                        <div className="mt-1 flex items-center gap-1.5 text-[13px] text-slate-500">
                          <Clock3 size={14} className="shrink-0 text-slate-400" />
                          <span className="tabular-nums">{claim.start_time}–{claim.end_time}</span>
                        </div>
                      </div>
                      {canCancel ? (
                        <IosBadge tone="amber" className="tabular-nums">
                          <Clock3 size={12} />
                          {countdown}
                        </IosBadge>
                      ) : (
                        <IosBadge tone="green">
                          <CheckCircle2 size={12} />
                          В графике
                        </IosBadge>
                      )}
                    </div>
                    {canCancel ? (
                      <div className="mt-3">
                        <button
                          type="button"
                          onClick={() => handleCancelMyClaim(claim)}
                          disabled={busy}
                          className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-rose-50 px-4 py-2.5 text-[13.5px] font-semibold text-rose-600 ring-1 ring-rose-100 transition-all hover:bg-rose-100 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {busy ? <RefreshCw size={15} className="animate-spin" /> : <Undo2 size={15} />}
                          {busy ? 'Отмена…' : `Отменить · ${countdown}`}
                        </button>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </IosModal>
    </div>
  );
};

export default ShiftAuctionView;
