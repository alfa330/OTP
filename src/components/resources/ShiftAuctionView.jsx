import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
  AlertTriangle,
  Bell,
  BookOpen,
  CalendarCheck,
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
  Lock,
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
  UserCog,
  Users,
  Wifi,
  X
} from 'lucide-react';
import { isAdminLikeRole, isSupervisorRole, normalizeRole } from '../../utils/roles';
import { IosModal, IosBadge, IosToggle, iosCard } from '../ui/ios';
import {
  filterOperationalShiftAuctionOperators,
  getShiftAuctionOperatorStatusLabel,
  isActiveShiftAuctionOperator,
  normalizeShiftAuctionOperatorId as normalizeOperatorId,
  normalizeShiftAuctionOperators,
  shouldHydrateShiftAuctionDraft,
} from './shiftAuctionParticipants';
import { collectMyAuctionDayClaims } from './shiftAuctionDayClaims';
import { mergeRealtimeAuctionLot } from './shiftAuctionRealtimeLots';
import { findAuctionClaimConflict } from './shiftAuctionClaimRules';

// Аукцион идёт на двух направлениях: линия (СЗоВ «Основа») и чат («Чат менеджер»).
// Это два независимых прогона на одном разделе. У СВ и выше вверху есть тумблер,
// оператор же видит ТОЛЬКО своё направление — его присылает сервер полем
// `direction_mode` снапшота, и подменить его из браузера нельзя.
const AUCTION_DIRECTION_LINE = 'line';
const AUCTION_DIRECTION_CHAT = 'chat';
const AUCTION_DIRECTIONS = [
  { key: AUCTION_DIRECTION_LINE, label: 'Линия' },
  { key: AUCTION_DIRECTION_CHAT, label: 'Чат' },
];
const AUCTION_DIRECTION_LABELS = {
  [AUCTION_DIRECTION_LINE]: 'Линия',
  [AUCTION_DIRECTION_CHAT]: 'Чат',
};
// Ключ выбора направления у управляющего: раздел переоткрывается там же, где закрыли.
const AUCTION_DIRECTION_STORAGE_KEY = 'otp_shift_auction_direction_v1';

const normalizeAuctionDirection = (value) => (
  String(value || '').trim().toLowerCase() === AUCTION_DIRECTION_CHAT
    ? AUCTION_DIRECTION_CHAT
    : AUCTION_DIRECTION_LINE
);

const readStoredAuctionDirection = () => {
  if (typeof window === 'undefined') return AUCTION_DIRECTION_LINE;
  try {
    return normalizeAuctionDirection(window.localStorage.getItem(AUCTION_DIRECTION_STORAGE_KEY));
  } catch {
    return AUCTION_DIRECTION_LINE;
  }
};

const storeAuctionDirection = (value) => {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(AUCTION_DIRECTION_STORAGE_KEY, normalizeAuctionDirection(value));
  } catch {
    /* приватный режим — выбор просто не переживёт перезагрузку */
  }
};

// Недельный потолок часов по ставке. Совпадает с серверной нормой
// (рабочие дни × 8 ч × ставка): 1,0 → 40 ч, 0,75 → 30 ч, 0,5 → 20 ч. Нужен только
// для ПОДСКАЗКИ в окне выбора части смены — считает и запрещает всё равно сервер.
const auctionWeeklyHoursForRate = (rate) => {
  const value = Number(rate);
  if (!Number.isFinite(value) || value <= 0) return null;
  return Math.round(value * 40 * 10) / 10;
};

// Стабильный ключ недавнего добора: (lot_id | plan_id | source_schedule_shift_id).
const getPostClaimKey = (claim) => {
  if (!claim) return '';
  const lotId = claim.lot_id != null ? String(claim.lot_id) : '';
  const planId = claim.plan_id != null ? String(claim.plan_id) : '';
  const shiftId = claim.source_schedule_shift_id != null ? String(claim.source_schedule_shift_id) : '';
  return `${lotId}|${planId}|${shiftId}`;
};

// Идентичность добора с точки зрения ОТМЕНЫ: сервер снимает добор по паре
// (plan_id, source_schedule_shift_id), а для синтетических лотов — по lot_id
// (см. handleCancelMyClaim и /api/shift_auction/cancel_post_claim). Один и тот
// же добор не должен попасть в список дважды, даже если запрос когда-нибудь
// вернёт его продублированным с разными lot_id.
const getPostClaimIdentity = (claim) => {
  if (!claim) return '';
  if (claim.plan_id != null && claim.source_schedule_shift_id != null) {
    return `plan:${claim.plan_id}|shift:${claim.source_schedule_shift_id}`;
  }
  return claim.lot_id != null ? `lot:${claim.lot_id}` : '';
};

const normalizeSchedulePlanId = (value) => {
  const id = Number(value);
  return Number.isFinite(id) && id > 0 ? id : null;
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
// Week time groups: the same cap the server enforces, mirrored so the "+ Группа"
// button can simply disappear instead of failing on save.
const AUCTION_TIME_GROUP_LIMIT = 10;
// How far above their norm a "свой график" group may go — mirrors the server.
const AUCTION_SELF_SCHEDULE_EXTRA_MINUTES = 600;
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

const addDaysToDateInputValue = (dateValue, days) => (
  splitDateTimeInputValue(addMinutesToDateTimeInputValue(`${dateValue}T00:00`, days * 24 * 60)).date
);

// Monday-first weekday index, matching AUCTION_WEEKDAY_LABELS.
const getWeekdayIndex = (dateValue) => {
  const parsed = new Date(`${dateValue}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? 0 : (parsed.getDay() + 6) % 7;
};

// Monday-first week around a date — the days a group may be moved to.
const getWeekDatesForDate = (dateValue) => {
  if (!dateValue) return [];
  const anchor = new Date(`${dateValue}T00:00:00`);
  if (Number.isNaN(anchor.getTime())) return [];
  const mondayOffset = (anchor.getDay() + 6) % 7;
  const monday = addDaysToDateInputValue(dateValue, -mondayOffset);
  return Array.from({ length: 7 }, (_, index) => addDaysToDateInputValue(monday, index));
};

// A group carries a day inside the auction week plus its times. The day defaults
// to the main start's, so moving the week (or the main start) moves the groups
// along instead of leaving them on a stale date.
const toDraftTimeGroup = (group) => ({
  key: `saved-${group?.id}`,
  id: normalizeOperatorId(group?.id),
  name: group?.name || '',
  date: splitDateTimeInputValue(group?.starts_at).date,
  startTime: splitDateTimeInputValue(group?.starts_at).time,
  endTime: splitDateTimeInputValue(group?.ends_at).time,
  selfSchedule: Boolean(group?.self_schedule_enabled),
  operatorIds: Array.isArray(group?.operator_ids)
    ? group.operator_ids.map(normalizeOperatorId).filter(Boolean)
    : []
});

// A new group starts on the main day and time: the controls always show some
// value, so an "empty" start would silently disagree with what the manager sees.
const createDraftTimeGroup = (index, date, startTime) => ({
  key: `new-${Date.now().toString(36)}-${index}`,
  id: null,
  name: '',
  date: date || '',
  startTime: startTime || '',
  endTime: '',
  selfSchedule: false,
  operatorIds: []
});

const getTimeGroupWindow = (group, startsAt, endsAt) => {
  const groupDate = group?.date || splitDateTimeInputValue(startsAt).date;
  if (!groupDate || !group?.startTime) return { startsAt: '', endsAt: '' };
  const windowStartsAt = `${groupDate}T${group.startTime}`;
  if (!group.endTime) return { startsAt: windowStartsAt, endsAt: '' };
  // An end at or before the start reads as "next morning" — the same way a night
  // shift does. The resolved window is spelled out in the UI, nothing is hidden.
  const sameDayEnd = `${groupDate}T${group.endTime}`;
  return {
    startsAt: windowStartsAt,
    endsAt: sameDayEnd > windowStartsAt ? sameDayEnd : `${addDaysToDateInputValue(groupDate, 1)}T${group.endTime}`
  };
};

// Datetime-input values are lexicographically ordered, so plain string compares
// are enough here — no Date objects, no timezone surprises.
const getTimeGroupIssue = (group, startsAt, endsAt) => {
  if (!startsAt) return 'Сначала задайте начало аукциона';
  if (!group?.startTime) return 'Укажите старт группы';
  // An explicit end is always resolved to a moment after the start, so only the
  // inherited-end case can leave the group with no window at all.
  const window = getTimeGroupWindow(group, startsAt, endsAt);
  if (window.endsAt) return '';
  if (endsAt && window.startsAt >= endsAt) {
    return 'Группа стартует после общего завершения — задайте ей своё завершение';
  }
  return '';
};

const buildTimeGroupsPayload = (groups, startsAt, endsAt) => (groups || []).map((group) => {
  const window = getTimeGroupWindow(group, startsAt, endsAt);
  return {
    id: group.id || null,
    name: String(group.name || '').trim(),
    starts_at: window.startsAt || null,
    ends_at: window.endsAt || null,
    self_schedule_enabled: Boolean(group.selfSchedule),
    operator_ids: group.operatorIds || []
  };
});

const getTimeGroupTitle = (group, index) => String(group?.name || '').trim() || `Группа ${index + 1}`;

const formatTimeGroupWindowLabel = (group, startsAt, endsAt) => {
  if (!group?.startTime) return 'Старт не задан';
  const window = getTimeGroupWindow(group, startsAt, endsAt);
  const mainStartDate = splitDateTimeInputValue(startsAt).date;
  const groupDate = group.date || mainStartDate;
  const endParts = splitDateTimeInputValue(window.endsAt);
  // The day is shown only when it differs from the auction's own — otherwise it
  // is noise repeated on every group.
  const dayPrefix = groupDate && groupDate !== mainStartDate ? `${formatShortDateLabel(groupDate)} · ` : '';
  const endLabel = group.endTime
    ? `${endParts.date && endParts.date !== groupDate ? `${formatShortDateLabel(endParts.date)} ` : ''}${group.endTime}`
    : `${splitDateTimeInputValue(endsAt).time || '—'} (общее)`;
  return `${dayPrefix}${group.startTime} → ${endLabel}`;
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

// Shift a "свой график" operator gets for a chosen start: the length follows their
// rate, and a full-rate 20:00 start is the 20*08 night shift, exactly as in the grid.
const getSelfScheduleShiftGroup = (rate, startTime) => {
  const value = Number(rate) || 0;
  const bucket = value <= 0.5 ? 0.5 : (value <= 0.75 ? 0.75 : 1);
  if (bucket === 1 && normalizeClockValue(startTime) === '20:00') {
    return AUCTION_RATE_GROUPS.find((group) => group.night) || null;
  }
  return AUCTION_RATE_GROUPS.find((group) => !group.night && group.rate === bucket) || null;
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

// The lot's rate is derived from the shift's clock duration, NOT from the
// rate key stored on the lot — plans sometimes seed e.g. a 9h shift under a
// 0.75-rate slot. Boundaries: <5.5h → 0.5, 5.5–7.5h → 0.75, ≥7.5h → 1.
// Night 20*08 lots are exempt (they keep their dedicated grid group).
const getAuctionLotDurationRate = (lot) => {
  const range = lotMinuteRange(lot);
  if (!range) {
    const fallback = Number(lot?.rate_min);
    if (!Number.isFinite(fallback) || fallback <= 0.5) return 0.5;
    return fallback <= 0.75 ? 0.75 : 1;
  }
  const durationMinutes = Math.max(0, range[1] - range[0]);
  if (durationMinutes < 5.5 * 60) return 0.5;
  if (durationMinutes < 7.5 * 60) return 0.75;
  return 1;
};

const getAuctionRateGroupId = (lot) => {
  if (isNightAuctionLot(lot)) return 'night-20-08';
  const rate = getAuctionLotDurationRate(lot);
  if (rate <= 0.5) return 'rate-0.5';
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

// Смена, взятая НЕ целиком. Два случая: пост-аукционный добор (post_auction_claimed)
// и часть, взятая в ходе аукциона в чате (`partial_claim` у карточки «моей» смены).
// В обоих окно лота шире взятого куска, и считать надо именно по куску.
const isAuctionLotPartiallyClaimed = (lot) => Boolean(lot?.post_auction_claimed || lot?.partial_claim);

const getAuctionLotEffectiveStartTime = (lot) => (
  isAuctionLotPartiallyClaimed(lot) && getAuctionLotClaimStartTime(lot)
    ? getAuctionLotClaimStartTime(lot)
    : lot?.start_time
);

const getAuctionLotEffectiveEndTime = (lot) => (
  isAuctionLotPartiallyClaimed(lot) && getAuctionLotClaimEndTime(lot)
    ? getAuctionLotClaimEndTime(lot)
    : lot?.end_time
);

// «Моя» смена из лота: либо лот целиком мой, либо в нём лежит мой кусок. Часть
// возвращается карточкой с окном лота и границами куска — так её одинаково считают
// и норма, и панель дня, и подписи.
const getMyAuctionClaimEntry = (lot, userId) => {
  if (!lot) return null;
  const myId = Number(userId);
  if (!Number.isFinite(myId)) return null;
  const mySegment = (Array.isArray(lot.claim_segments) ? lot.claim_segments : [])
    .find((seg) => seg && Number(seg.claimed_by) === myId);
  if (mySegment) {
    return {
      ...lot,
      status: 'claimed',
      claimed_by: myId,
      claim_start_time: mySegment.start_time,
      claim_end_time: mySegment.end_time,
      partial_claim: true,
    };
  }
  if (lot.status === 'claimed' && Number(lot.claimed_by) === myId) return lot;
  return null;
};

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

// Ночная смена — ровно 20:00–08:00. Тот же набор, что у сервера
// (Database.SHIFT_AUCTION_NIGHT_START_HHMM / _END_HHMM): две ночи подряд брать
// нельзя, и витрина обязана красить лот серым по тому же правилу, иначе человек
// увидит смену доступной и получит отказ уже кликом.
const AUCTION_NIGHT_START_HHMM = '20:00';
const AUCTION_NIGHT_END_HHMM = '08:00';

const isAuctionNightShift = (startTime, endTime) => (
  String(startTime || '').slice(0, 5) === AUCTION_NIGHT_START_HHMM
  && String(endTime || '').slice(0, 5) === AUCTION_NIGHT_END_HHMM
);

// «2026-09-02» → «02.09»: подпись причины должна читаться, а не быть ISO-датой.
const formatAuctionShortDate = (dateStr) => {
  const parts = String(dateStr || '').slice(0, 10).split('-');
  return parts.length === 3 ? `${parts[2]}.${parts[1]}` : String(dateStr || '');
};

// Соседний день по календарю: ±1 сутки от даты смены.
const auctionAdjacentDates = (dateStr) => {
  const parsed = new Date(`${String(dateStr || '').slice(0, 10)}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return [];
  const shift = (days) => {
    const next = new Date(parsed);
    next.setDate(next.getDate() + days);
    return next.toISOString().slice(0, 10);
  };
  return [shift(-1), shift(1)];
};

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

// То же, но по фактически взятому диапазону: у частичного добора окно лота шире
// взятого, и подпись «9-21» рядом с «4 ч» противоречила бы сама себе.
// Ночная смена сохраняет свою метку — «20*08» узнают по ней, а не по времени.
const formatAuctionClaimLabel = (lot) => {
  if (isNightAuctionLot(lot)) return '20*08';
  const start = normalizeClockValue(getAuctionLotEffectiveStartTime(lot));
  const end = normalizeClockValue(getAuctionLotEffectiveEndTime(lot));
  return `${start}-${end}`;
};

const formatCompactAuctionClaimLabel = (lot) => {
  if (isNightAuctionLot(lot)) return '20*08';
  return `${formatCompactClockValue(getAuctionLotEffectiveStartTime(lot))}-${formatCompactClockValue(getAuctionLotEffectiveEndTime(lot))}`;
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
  // Shift the operator put on the calendar themselves ("свой график"): always
  // claimed, so it only needs a marker telling it apart from auction shifts.
  const isSelfScheduledLot = Boolean(lot.self_scheduled);
  // Tooltip rate must match the grid row: duration-derived (night lots keep their key).
  const minRate = isNightAuctionLot(lot) ? Number(lot.rate_min || 0) : getAuctionLotDurationRate(lot);
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
    : ''} · в норму ${formatAuctionHours(netMinutes)} ч${breakMinutes ? ` · перерыв ${formatAuctionHours(breakMinutes)} ч` : ''}${breaksLabel ? ` (${breaksLabel})` : ''}${claimBlockReason ? ` · ${claimBlockReason}` : ''}${lot.claimed_by_name ? ` · ${lot.claimed_by_name}` : ''}${postAuctionTakeable ? ` · доступно после аукциона${postAuctionSegment && !postAuctionSegment.isFull ? `: ${postAuctionSegment.start_time}–${postAuctionSegment.end_time}` : ''}` : ''}${formatPostAuctionClaimTitleSuffix(lot)}${isAddedLot ? ` · добавил ${lot.added_by_name || '—'}` : ''}${isSelfScheduledLot ? ' · свой график' : ''}`;

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
    // Чужие взятые смены серые — брать их нельзя. Своя остаётся зелёной в любой
    // фазе, иначе оператор не отличит её от чужой: единственной приметой был бы
    // title с именем, а на телефоне наведения нет.
    // Цвет не делим на «добор/не добор»: в предпросмотре опубликованной недели
    // post_auction_claimed выставляется каждой закрытой смене, и обычные смены
    // прошлой недели красились бы доборными. Про добор говорят подсказка и
    // карточка дня.
    tone = lotClaimedByCurrentUser
      ? 'border-emerald-600 bg-emerald-600 text-white'
      : 'border-slate-200 bg-slate-100 text-slate-400';
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
  // Claimed (fully taken) → the range actually taken. Available + partly taken → free part.
  // A marker is shown whenever the shift was taken IN PARTS (split / partially taken).
  // Взятая частью смена подписывается взятым окном, а не исходным: иначе ячейка
  // обещает часы, которых у оператора нет.
  const finalDisplayLabel = freeRangeLabel
    || (isLotClaimed ? formatAuctionClaimLabel(lot) : label);
  const finalDisplayCompact = freeRangeLabel
    || (isLotClaimed ? formatCompactAuctionClaimLabel(lot) : compactLabel);
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
      {isSelfScheduledLot ? (
        <span
          // Белая подложка: на зелёной «своей» смене бирюзовый значок сливался с фоном.
          className="pointer-events-none absolute left-0.5 top-0.5 rounded-full bg-white text-teal-600 ring-1 ring-white"
          title={`Свой график · ${lot.claimed_by_name || '—'}`}
        >
          <CalendarCheck size={9} strokeWidth={3} />
        </span>
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

const getAuctionRuntimeStatus = (settings, nowMs, effectiveStartsAt = undefined, effectiveEndsAt = undefined) => {
  if (!settings?.enabled) return 'disabled';
  if (settings.finished_at) return 'closed';
  if (settings.paused_at) return 'paused';
  // Members of a week time group carry their own window (earlier or later);
  // everyone else (incl. managers) falls back to the main one.
  const startsAtRaw = effectiveStartsAt === undefined ? settings.starts_at : effectiveStartsAt;
  const endsAtRaw = effectiveEndsAt === undefined ? settings.ends_at : effectiveEndsAt;
  const startsAtMs = startsAtRaw ? new Date(startsAtRaw).getTime() : null;
  const endsAtMs = endsAtRaw ? new Date(endsAtRaw).getTime() : null;
  if (Number.isFinite(startsAtMs) && nowMs < startsAtMs) return 'scheduled';
  if (Number.isFinite(endsAtMs) && nowMs >= endsAtMs) return 'closed';
  return 'open';
};

// Имя у внутренней функции, а не только displayName на обёртке memo: React
// строит стек компонентов по type-функции, а displayName обёртки в него не
// попадает — иначе в «Технических деталях» вместо имени будет минифицированный id.
const AuctionCountdownText = React.memo(function AuctionCountdownText({ target }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!target) return undefined;
    const timer = window.setInterval(() => setTick((value) => (value + 1) % 1_000_000), 1000);
    return () => window.clearInterval(timer);
  }, [target]);
  if (!target) return null;
  // Именно <span>, а не <>…</>: фрагмент отдаёт голый текстовый узел, который
  // встроенный переводчик браузера оборачивает в <font>. Дальше React пытается
  // удалить «свой» текстовый узел у прежнего родителя и падает с removeChild.
  return <span>{formatCountdown(target, Date.now())}</span>;
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

const SHIFT_AUCTION_INSTRUCTIONS_VERSION = 'v5';

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

// Статичная копия IosToggle для иллюстраций в инструкции.
const TogglePreview = ({ on = false }) => (
  <span className={`relative inline-flex h-[26px] w-[44px] shrink-0 items-center rounded-full ${on ? 'bg-emerald-500' : 'bg-slate-300'}`}>
    <span className={`inline-block h-[22px] w-[22px] rounded-full bg-white shadow-md ${on ? 'translate-x-[20px]' : 'translate-x-[2px]'}`} />
  </span>
);

// Строка карточки «Режимы» (иконка + название + описание + переключатель).
const ModeRowPreview = ({ icon: Icon, iconClass, title, hint, on }) => (
  <div className="flex items-center justify-between gap-3 px-3 py-2.5">
    <span className="flex min-w-0 items-center gap-2.5">
      <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-xl ${iconClass}`}>
        <Icon size={15} />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold text-slate-900">{title}</span>
        {hint ? <span className="block truncate text-[11px] text-slate-500">{hint}</span> : null}
      </span>
    </span>
    <TogglePreview on={on} />
  </div>
);

// Сегмент управления запуском (Пауза / Возобновить / Завершить) для иллюстраций.
const LifecycleSegmentPreview = ({ paused = false }) => (
  <span className="inline-flex items-center gap-1 rounded-2xl bg-slate-100 p-1">
    {paused ? (
      <span className="inline-flex h-8 items-center gap-1.5 rounded-xl bg-white px-3 text-xs font-semibold text-emerald-700 shadow-sm ring-1 ring-slate-200/70">
        <PlayCircle size={15} />
        Возобновить
      </span>
    ) : (
      <span className="inline-flex h-8 items-center gap-1.5 rounded-xl bg-white px-3 text-xs font-semibold text-amber-700 shadow-sm ring-1 ring-slate-200/70">
        <PauseCircle size={15} />
        Пауза
      </span>
    )}
    <span className="inline-flex h-8 items-center gap-1.5 rounded-xl px-3 text-xs font-semibold text-rose-600">
      <Square size={13} />
      Завершить
    </span>
  </span>
);

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
    ),
    nuances: [
      'Если вас включили в группу времени, аукцион откроется для вас в её время — раньше или позже остальных. Таймер и статус уже учитывают это, отдельно ничего считать не нужно.',
      'Название вашей группы видно в шапке рядом с заголовком, там же — время вашего окна.'
    ]
  },
  {
    icon: CalendarCheck,
    title: 'Свой график',
    body: 'Если вашей группе открыт свой график, смены не нужно ловить в таблице: нажмите день в нижней панели, выберите время начала — конец подставится по вашей ставке. Смена сразу станет вашей и появится в общей таблице с отметкой.',
    visual: (
      <div className="space-y-2">
        <div className="inline-flex h-[56px] w-[72px] flex-col items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600">
          <span className="text-[11px] font-semibold">ср, 05</span>
          <span className="mt-0.5 inline-flex items-center gap-0.5 text-[11px] font-bold"><Plus size={11} /> Смена</span>
        </div>
        <span className="block text-xs text-slate-500">Такой день в нижней панели ждёт вашу смену — нажмите на него.</span>
      </div>
    ),
    nuances: [
      'Больше нормы плюс 10 часов поставить нельзя — в окне видно, сколько осталось.',
      'Передумали? Нажмите на день со своей сменой — в карточке дня у неё есть кнопка «Убрать».',
      'На день с выходным или статусным периодом смену поставить нельзя, как и две смены на один день.'
    ]
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
    body: 'Смены в таблице сгруппированы по строкам ставок: «Ставка 1» (9 ч), «Ставка 0.75» (6.5 ч), «Ставка 0.5» (4 ч) и «Ночные 20*08». Кликните по нужному времени. Цвет смены показывает время старта (от голубого утром до тёмно-синего вечером). Ваша смена помечается зелёным, чужая — серым.',
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
    example: 'Например, при ставке 1.0 и неделе из 7 дней (квота 2 выходных) норма = 5 рабочих дней × 8 ч = 40 часов. При ставке 0.5 — 20 часов. Перерывы в норму не входят.'
  },
  {
    icon: Lock,
    title: 'Режим «Только своя ставка»',
    body: 'Администратор может ограничить выбор: тогда вы сможете брать только смены своей ставки. В шапке раздела появится голубой бейдж, а под ним — подсказка с вашей ставкой. Строки чужих ставок останутся видимыми, но смены в них будут серыми с пояснением.',
    visual: (
      <div className="space-y-2.5">
        <span className="inline-flex items-center gap-1 rounded-full border border-sky-300 bg-sky-100 px-2 py-0.5 text-[11px] font-semibold text-sky-800">
          <Lock size={11} />
          Только своя ставка
        </span>
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <span className="w-24 shrink-0 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Ставка 0.75</span>
            <LotChipPreview tone="morning" label="09-15:30" />
            <span className="text-[11px] font-semibold text-emerald-700">ваша ставка — можно брать</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-24 shrink-0 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Ставка 1</span>
            <LotChipPreview tone="blocked" label="08-17" />
            <span className="text-[11px] text-slate-500">«Смена ставки 1 — вам доступны только смены ставки 0.75»</span>
          </div>
        </div>
      </div>
    ),
    nuances: [
      'Ставка смены определяется её длительностью: до 5.5 ч — 0.5, до 7.5 ч — 0.75, длиннее (включая ночные 20*08) — 1.',
      'Если режим выключен — ограничения нет: можно брать смены любой ставки в пределах нормы.',
      'Режим может включиться или выключиться прямо во время аукциона — таблица обновится сразу, без перезагрузки.'
    ]
  },
  {
    icon: Plus,
    title: 'Режим добора',
    body: 'Когда норма у большинства набрана, администратор может включить добор. В шапке появится фиолетовый бейдж «Режим добора» — с этого момента можно брать дополнительные смены сверх нормы, в том числе несколько на один день.',
    visual: (
      <div className="space-y-2">
        <span className="inline-flex items-center gap-1 rounded-full border border-violet-300 bg-violet-100 px-2 py-0.5 text-[11px] font-semibold text-violet-800">
          <Plus size={12} />
          Режим добора
        </span>
        <span className="block text-xs text-slate-500">Единственное ограничение — новая смена не должна пересекаться по времени с уже взятыми в этот день.</span>
      </div>
    ),
    nuances: [
      'Проверка нормы в доборе не действует — часы могут превысить норму.',
      'Пересекающаяся по времени смена всё равно недоступна — кнопка серая с подсказкой.',
      'Если включён режим «Только своя ставка», в доборе тоже можно брать только смены своей ставки.'
    ]
  },
  {
    icon: Undo2,
    title: 'Шаг 4 · Посмотрите свои смены и верните лишнюю',
    body: 'Нажмите на любой день в нижней панели — снизу откроется карточка дня со всеми вашими сменами этого дня: время, часы и пометка «добор», если вы брали только часть смены. Карточка работает и когда аукцион уже закрыт, и на прошлых неделях. Пока аукцион открыт, у каждой смены есть кнопка «Вернуть» — после подтверждения смена снова станет доступной остальным операторам.',
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
          <div className="mt-1 text-xs text-slate-500">Клик по любой ячейке → снизу откроется карточка дня.</div>
        </div>
        <div>
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Карточка дня</div>
          <div className="w-full max-w-xs rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="text-sm font-semibold text-slate-950">вт, 03 июн</div>
            <div className="text-xs text-slate-500">Ваши смены: 1 · 9 ч</div>
            <div className="mt-2 flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-2">
              <span className="rounded-lg bg-emerald-50 px-2 py-1 text-[12px] font-semibold tabular-nums text-emerald-800">10:00–19:00</span>
              <span className="flex-1" />
              <span className="text-[12px] tabular-nums text-slate-400">9 ч</span>
              <span className="rounded-lg border border-rose-200 px-2 py-1 text-[12px] font-semibold text-rose-600">Вернуть</span>
            </div>
          </div>
        </div>
      </div>
    ),
    nuances: [
      'Смотреть свои смены можно всегда — и пока аукцион идёт, и после закрытия, и на прошлой неделе.',
      'Кнопка «Вернуть» есть только пока аукцион ещё открыт.',
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
      'Передумали? Отменить взятую доп. смену можно в течение 10 минут через «Мои доп. смены» (кнопка в шапке). Позже смена закрепляется в графике — только через руководителя.'
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
    icon: CalendarDays,
    title: 'Шаг 2 · Выберите неделю аукциона',
    body: 'В блоке «Неделя аукциона» выберите сохранённый недельный план из «Расчёта ресурсов» — его смены станут лотами аукциона. Доступны только полные недели, которые ещё не закончились. Рядом видно, сколько смен в плане.',
    visual: (
      <div className="space-y-2">
        <div className="grid max-w-sm gap-2">
          <span className="rounded-lg border border-blue-500 bg-blue-50 px-3 py-2 text-left">
            <span className="block text-sm font-semibold text-blue-900">14.07 – 20.07</span>
            <span className="mt-0.5 block text-xs text-slate-500">166 смен · активная</span>
          </span>
          <span className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-left">
            <span className="block text-sm font-semibold text-slate-700">21.07 – 27.07</span>
            <span className="mt-0.5 block text-xs text-slate-500">181 смена</span>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ButtonPreview variant="outline" icon={RotateCcw}>Начать заново</ButtonPreview>
          <span className="text-xs text-slate-500">Перезапускает аукцион выбранной недели с чистого листа.</span>
        </div>
      </div>
    ),
    nuances: [
      'Смена недели или «Начать заново» очищает выбранные операторами смены и выходные этой недели. Прошлые опубликованные периоды не трогаются.',
      'Если планов нет — сначала проведите генерацию в «Расчёте ресурсов» и сохраните недельный график.'
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
    example: 'Пример: старт 05.06 09:00, завершение 05.06 09:30. Это даст 30-минутное окно «гонки» за смены.',
    nuances: [
      'Быстрые пресеты «Завершить через 30 мин / 1 ч / 2 ч...» проставляют время завершения от выбранного старта одним кликом.'
    ]
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
    icon: UserCog,
    title: 'Группы времени',
    body: 'Части участников можно дать своё время аукциона — раньше или позже общего. В блоке «Группы времени» нажмите «Группа», выберите день внутри недели, задайте старт (и при необходимости своё завершение) и отметьте, кто в неё входит. Остальные заходят в общее окно.',
    visual: (
      <div className="max-w-sm space-y-2">
        <div className="overflow-hidden rounded-xl bg-white ring-1 ring-slate-200/70">
          <div className="flex items-center gap-2 px-3 py-2.5">
            <ChevronRight size={15} className="text-slate-400" />
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-slate-900">Наставники</span>
              <span className="mt-0.5 block text-xs tabular-nums text-slate-500">09:00 → 12:00 (общее) · 4 в группе</span>
            </span>
          </div>
        </div>
        <div className="overflow-hidden rounded-xl bg-white ring-1 ring-slate-200/70">
          <div className="flex items-center gap-2 px-3 py-2.5">
            <ChevronRight size={15} className="text-slate-400" />
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-slate-900">Новички</span>
              <span className="mt-0.5 block text-xs tabular-nums text-slate-500">14:00 → 16:00 · 6 в группе</span>
            </span>
          </div>
        </div>
      </div>
    ),
    nuances: [
      'Группа живёт только в своей неделе: на других неделях аукциона она не действует.',
      'День группы выбирается внутри недели общего старта — можно поставить её на другой день, не только на день аукциона.',
      'В группу можно взять любого оператора направления (кроме уволенных) — он не обязан быть заранее отмечен в списке участников: попав в группу, он автоматически становится участником аукциона.',
      'Оператор может быть только в одной группе недели.',
      'Без своего завершения группа закрывается вместе со всеми; если группа стартует позже общего завершения, своё завершение обязательно.',
      'Пауза сдвигает завершение и у групп, а не только у общего окна.'
    ]
  },
  {
    icon: CalendarCheck,
    title: 'Свой график у группы',
    body: 'Тумблер «Свой график» в карточке группы разрешает её участникам не разбирать готовые смены, а ставить свои: они нажимают день в нижней панели и выбирают время начала. Длина смены берётся из их ставки. Потолок жёсткий — норма плюс 10 часов, и он же начинает действовать на обычные смены этих операторов.',
    visual: (
      <div className="max-w-sm space-y-2">
        <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-2.5">
          <span className="min-w-0">
            <span className="block text-sm font-semibold text-slate-900">Свой график</span>
            <span className="mt-0.5 block text-xs text-slate-500">Норма + 10 ч</span>
          </span>
          <TogglePreview on />
        </div>
        <div className="flex items-center gap-2">
          <span className="relative flex h-8 w-20 items-center justify-center rounded border border-slate-200 bg-slate-100 text-xs font-semibold tabular-nums text-slate-400">
            09:00-18:00
            <span className="absolute left-0.5 top-0.5 text-teal-600"><CalendarCheck size={9} strokeWidth={3} /></span>
          </span>
          <span className="text-[11px] text-slate-500">— так своя смена выглядит в общей таблице.</span>
        </div>
      </div>
    ),
    nuances: [
      'Смена появляется сразу закреплённой за оператором и попадает в итоговые графики, как обычная.',
      'Убрать её оператор может кнопкой «Убрать» в карточке дня — смена удаляется, другим она не достаётся.',
      'Выходные, статусные периоды и правило «одна смена в день» действуют и здесь.'
    ]
  },
  {
    icon: PlayCircle,
    title: 'Шаг 5 · Режимы аукциона и сохранение',
    body: 'В карточке «Режимы» три переключателя. «Аукцион включён» — главный: применяется после кнопки «Сохранить» и открывает раздел выбранным операторам. «Режим добора» и «Только своя ставка» действуют мгновенно, без сохранения.',
    visual: (
      <div className="space-y-3">
        <div className="max-w-md divide-y divide-slate-100 rounded-2xl bg-white ring-1 ring-slate-200/70">
          <ModeRowPreview icon={Gavel} iconClass="bg-blue-50 text-blue-600" title="Аукцион включён" hint="Применяется после «Сохранить»" on />
          <ModeRowPreview icon={Plus} iconClass="bg-violet-50 text-violet-600" title="Режим добора" hint="Только при открытом аукционе" />
          <ModeRowPreview icon={Lock} iconClass="bg-sky-50 text-sky-600" title="Только своя ставка" hint="Применяется сразу" />
        </div>
        <ButtonPreview variant="primary" icon={Save}>Сохранить</ButtonPreview>
      </div>
    ),
    nuances: [
      'Выключение «Аукцион включён» (после «Сохранить») — мгновенное: операторы теряют доступ к разделу до нового включения.',
      'Изменения окна старта/завершения и режимов подхватываются всеми клиентами без перезагрузки.'
    ]
  },
  {
    icon: Lock,
    title: 'Режим «Только своя ставка»',
    body: 'Ограничивает операторов сменами их собственной ставки: оператор 0.75 не сможет забрать 9-часовую смену, оператор 1.0 — короткую. Ставка смены определяется её длительностью: до 5.5 ч — 0.5, до 7.5 ч — 0.75, длиннее (включая ночные 20*08) — 1. Включается и выключается в любой момент, у операторов применяется сразу.',
    visual: (
      <div className="space-y-2.5">
        <div className="flex max-w-md items-center justify-between gap-3 rounded-2xl bg-white px-3 py-2.5 ring-1 ring-slate-200/70">
          <span className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-xl bg-sky-50 text-sky-600"><Lock size={15} /></span>
            <span className="text-sm font-semibold text-slate-900">Только своя ставка</span>
          </span>
          <TogglePreview on />
        </div>
        <div className="flex items-center gap-2">
          <LotChipPreview tone="blocked" label="08-17" />
          <span className="text-[11px] text-slate-500">— так оператор 0.75 видит смену ставки 1: серая, с подсказкой почему недоступна.</span>
        </div>
      </div>
    ),
    nuances: [
      'У операторов появляется голубой бейдж «Только своя ставка» и подсказка с их ставкой.',
      'Проверка двойная: и в интерфейсе, и на сервере при попытке взять смену.',
      'Действует и в режиме добора.',
      'На пост-аукционный добор оранжевых смен ограничение не распространяется.'
    ]
  },
  {
    icon: Plus,
    title: 'Режим добора',
    body: 'Когда основная «гонка» прошла и остались свободные смены — включите добор. Операторы смогут брать смены сверх нормы (в том числе несколько на день), лишь бы они не пересекались по времени с уже взятыми. Момент включения фиксируется в журнале.',
    visual: (
      <div className="space-y-2">
        <div className="flex max-w-md items-center justify-between gap-3 rounded-2xl bg-white px-3 py-2.5 ring-1 ring-slate-200/70">
          <span className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-xl bg-violet-50 text-violet-600"><Plus size={15} /></span>
            <span className="text-sm font-semibold text-slate-900">Режим добора</span>
          </span>
          <TogglePreview on />
        </div>
        <span className="inline-flex items-center gap-1 rounded-full border border-violet-300 bg-violet-100 px-2 py-0.5 text-[11px] font-semibold text-violet-800">
          <Plus size={12} />
          Режим добора
        </span>
        <span className="block text-xs text-slate-500">Такой бейдж видят все участники в шапке, пока добор активен.</span>
      </div>
    ),
    nuances: [
      'Включить добор можно только при открытом аукционе; выключить — в любой момент, даже на паузе.',
      'При перезапуске аукциона или смене недели добор автоматически сбрасывается.'
    ]
  },
  {
    icon: PauseCircle,
    title: 'Пауза, возобновление и досрочное завершение',
    body: 'Открытый аукцион можно приостановить — операторы увидят статус «Пауза» и не смогут менять выбор. При возобновлении время завершения автоматически сдвигается на длительность паузы. «Завершить» закрывает аукцион досрочно.',
    visual: (
      <div className="flex flex-col items-start gap-2">
        <LifecycleSegmentPreview />
        <LifecycleSegmentPreview paused />
        <span className="text-xs text-slate-500">Сегмент в шапке блока «Запуск аукциона»: набор кнопок зависит от статуса.</span>
      </div>
    ),
    nuances: [
      'Пауза доступна, пока идёт хотя бы одно окно — общее или групповое.',
      'После «Завершить» операторы больше не могут менять выбор — только просмотр итогов.'
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
      'Индикатор «Realtime online» в шапке должен гореть зелёным.',
      'Кнопка «+» под каждой группой ставки добавляет недостающую смену в конкретный день — такие смены подсвечиваются фиолетовым с пометкой, кто их добавил.'
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
      // Занятые отрезки склеиваем: один и тот же добор приходит и в work_shifts
      // оператора, и в claim_segments лота, поэтому диапазоны дублируются. Без
      // склейки на таймлайне рисуются два одинаковых блока с одинаковым React-key
      // — при обновлении по SSE лишний узел остаётся в DOM «призраком».
      const last = occupied[occupied.length - 1];
      if (last && busy[0] <= last.end) {
        last.end = Math.max(last.end, busy[1]);
      } else {
        occupied.push({ start: busy[0], end: busy[1] });
      }
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
      // Занято ровно то, что человек взял: у ЧАСТИ смены это её границы, а не окно
      // лота. Иначе после 09:00–15:00 из 09:00–21:00 весь день выглядел бы занятым
      // и добрать часы в этот день стало бы нечем.
      .map((item) => getClockRangeWithinSource(
        getAuctionLotEffectiveStartTime(item) || item.start_time || item.start,
        getAuctionLotEffectiveEndTime(item) || item.end_time || item.end,
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

// Одно окно на два случая: пост-аукционный добор и разбор смены по частям в самом
// аукционе (чат). Механика выбора интервала общая, различаются только подписи и
// подсказка про норму — поэтому они и вынесены в пропсы.
const PostAuctionPartialClaimModal = ({
  lot,
  option,
  selection,
  onSelectionChange,
  onClose,
  onConfirm,
  inProgress,
  title = 'Забрать дополнительную смену',
  confirmLabel = 'Забрать',
  inProgressLabel = 'Забираю...',
  footnote = null
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
              {title}
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

          {footnote || (
            <p className="mt-3 text-xs leading-5 text-slate-600">
              {selectedIsPartial
                ? 'Будет сохранена выбранная часть исходной смены. Если она стыкуется с вашей сменой, график объединится автоматически.'
                : 'Будет сохранена вся дополнительная смена. Если она стыкуется с вашей сменой, график объединится автоматически.'}
            </p>
          )}
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
            {inProgress ? inProgressLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
};

/**
 * Карточка одной взятой доп. смены в панели «Мои доп. смены».
 *
 * Обратный отсчёт окна отмены тикает ВНУТРИ карточки. Раньше секунды лежали в
 * состоянии всего раздела (claimsNowMs), поэтому при открытой панели раз в
 * секунду перерисовывалось всё дерево аукциона — сетка лотов на весь период,
 * таблицы мониторинга и т.д. Любая рассинхронизация DOM (например, DOM, который
 * переписал встроенный переводчик браузера) сразу же всплывала именно здесь
 * ошибкой «Failed to execute 'removeChild' on 'Node'».
 *
 * Дедлайн считается один раз от времени ответа сервера (fetchedAtMs +
 * cancel_seconds_left): источник времени — часы БД, а не браузера.
 */
const MyPostClaimRow = React.memo(function MyPostClaimRow({ claim, fetchedAtMs, busy, onCancel }) {
  const baseLeftMs = Math.max(0, (Number(claim?.cancel_seconds_left) || 0) * 1000);
  // Запасное время берём один раз при монтировании: иначе Date.now() в теле
  // рендера сделал бы deadlineMs новым на каждый рендер, а он же — зависимость
  // эффекта ниже (интервал пересоздавался бы бесконечно).
  const mountedAtMsRef = useRef(Date.now());
  const fetchedAtSafeMs = Number(fetchedAtMs) > 0 ? Number(fetchedAtMs) : mountedAtMsRef.current;
  const deadlineMs = fetchedAtSafeMs + baseLeftMs;
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    setNowMs(Date.now());
    if (deadlineMs <= Date.now()) return undefined;
    const interval = window.setInterval(() => {
      const next = Date.now();
      setNowMs(next);
      if (next >= deadlineMs) window.clearInterval(interval);
    }, 1000);
    return () => window.clearInterval(interval);
  }, [deadlineMs]);

  const remainingMs = deadlineMs - nowMs;
  const canCancel = remainingMs > 0;
  const totalSec = Math.max(0, Math.ceil(remainingMs / 1000));
  const countdown = `${Math.floor(totalSec / 60)}:${String(totalSec % 60).padStart(2, '0')}`;

  return (
    <div className={`${iosCard} p-3.5`}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[14.5px] font-semibold capitalize text-slate-900">
            {formatDateLabel(claim?.shift_date)}
          </div>
          <div className="mt-1 flex items-center gap-1.5 text-[13px] text-slate-500">
            <Clock3 size={14} className="shrink-0 text-slate-400" />
            <span className="tabular-nums">{`${claim?.start_time || ''}–${claim?.end_time || ''}`}</span>
          </div>
        </div>
        {canCancel ? (
          <IosBadge key="claim-countdown" tone="amber" className="tabular-nums">
            <Clock3 size={12} />
            <span>{countdown}</span>
          </IosBadge>
        ) : (
          <IosBadge key="claim-locked" tone="green">
            <CheckCircle2 size={12} />
            <span>В графике</span>
          </IosBadge>
        )}
      </div>
      {canCancel ? (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => onCancel?.(claim)}
            disabled={busy}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-rose-50 px-4 py-2.5 text-[13.5px] font-semibold text-rose-600 ring-1 ring-rose-100 transition-all hover:bg-rose-100 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? <RefreshCw size={15} className="animate-spin" /> : <Undo2 size={15} />}
            <span>{busy ? 'Отмена…' : `Отменить · ${countdown}`}</span>
          </button>
        </div>
      ) : null}
    </div>
  );
});
MyPostClaimRow.displayName = 'MyPostClaimRow';

const ShiftAuctionShiftsTable = ({
  operators = [],
  workloads = [],
  lots = [],
  lotDates = [],
  canEdit = false,
  apiRoot = '',
  buildHeaders = null,
  onActionComplete = null,
  notify = null,
  direction = AUCTION_DIRECTION_LINE
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
    const sortedOperators = filterOperationalShiftAuctionOperators(operators, direction).sort((a, b) => {
      const dirCmp = String(a?.direction || '').localeCompare(String(b?.direction || ''), 'ru');
      if (dirCmp !== 0) return dirCmp;
      return String(a?.name || '').localeCompare(String(b?.name || ''), 'ru');
    });
    return sortedOperators
      .map((op) => {
        const opId = Number(op.id);
        const workload = workloadById.get(opId) || {};
        return { operator: op, opId, workload };
      });
  }, [direction, operators, workloadById]);

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
    // Направление уезжает вместе с каждым действием: у сервера это два разных прогона.
    const body = { direction, ...extra };
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
              // The ceiling of a "свой график" operator is their norm plus the
              // allowance, so the sheet does not flag an intended overshoot.
              const norm = Number(workload?.ceiling_minutes || workload?.norm_minutes || 0);
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
                  Ставка {Number(cellModalData.operator?.rate ?? 1).toFixed(2)} · {formatHours(cellModalData.workload?.claimed_net_minutes || 0)}/{formatHours(cellModalData.workload?.ceiling_minutes || cellModalData.workload?.norm_minutes || 0)} ч
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
  const departmentCode = String(user?.department_code ?? user?.departmentCode ?? '').toLowerCase();
  // Супервайзеры СЗоВ управляют аукционом наравне с главой отдела (он — admin).
  const isSzovSupervisor = isSupervisorRole(role) && departmentCode === 'szov';
  const canManage = isAdminLikeRole(role) || isSzovSupervisor;
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
  const auctionDraftDirtyRef = useRef(false);
  const auctionDraftRevisionRef = useRef(0);
  const auctionDraftSavedAtRef = useRef('');

  const [settings, setSettings] = useState({
    enabled: false,
    launch_note: '',
    starts_at: null,
    ends_at: null,
    time_groups: [],
    my_time_group: null,
    my_effective_starts_at: null,
    my_effective_ends_at: null,
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
    rate_lock_enabled: false,
    post_auction_active: false,
    has_period_history_access: false
  });
  const [isTogglingTopup, setIsTogglingTopup] = useState(false);
  const [isTogglingRateLock, setIsTogglingRateLock] = useState(false);
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
  // Week time groups. The ref keeps the groups of every configurable week (the
  // form needs those of whichever week is picked); the draft holds the week being
  // edited. Reloading the draft is driven by an explicit token, so an arriving
  // snapshot can never clobber unsaved edits.
  const [draftTimeGroups, setDraftTimeGroups] = useState([]);
  const [expandedTimeGroupKey, setExpandedTimeGroupKey] = useState('');
  const [timeGroupMemberQuery, setTimeGroupMemberQuery] = useState('');
  const [timeGroupsResetToken, setTimeGroupsResetToken] = useState(0);
  const serverTimeGroupsRef = useRef([]);
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
  // Карточка дня. Админу — кто взял смены, оператору — его собственные.
  const [isDayDetailsOpen, setIsDayDetailsOpen] = useState(false);
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
  // "Свой график": the operator picks the day in the bottom bar, then the start.
  const [selfScheduleDate, setSelfScheduleDate] = useState('');
  const [selfScheduleStart, setSelfScheduleStart] = useState('09:00');
  const [isSelfScheduling, setIsSelfScheduling] = useState(false);
  const [journalEntries, setJournalEntries] = useState([]);
  const [journalPage, setJournalPage] = useState(1);
  const [journalPerPage] = useState(50);
  const [journalTotal, setJournalTotal] = useState(0);
  const [journalLoading, setJournalLoading] = useState(false);
  const [journalError, setJournalError] = useState('');
  // Направление аукциона. Управляющий выбирает тумблером (выбор переживает
  // перезагрузку), оператору его назначает сервер снапшотом.
  const [direction, setDirection] = useState(() => (
    canMonitor ? readStoredAuctionDirection() : AUCTION_DIRECTION_LINE
  ));
  const [canSwitchDirection, setCanSwitchDirection] = useState(false);
  // Чат берёт смену ЧАСТЯМИ прямо в аукционе: клик по свободной смене открывает
  // тот же таймлайн, что и добор, но кладёт кусок в текущий прогон.
  const [partialClaimLot, setPartialClaimLot] = useState(null);
  const [partialClaimSelection, setPartialClaimSelection] = useState({ start_time: '', end_time: '' });
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

  // The window that gates the current viewer: a member of a week time group
  // carries its own start and end (earlier or later) from the server; everyone
  // else (incl. managers) uses the main window.
  const operatorEffectiveStartsAt = settings.my_effective_starts_at || settings.starts_at;
  const operatorEffectiveEndsAt = settings.my_effective_ends_at || settings.ends_at;

  useEffect(() => {
    if (!settings.enabled) return undefined;
    if (settings.paused_at || settings.finished_at) return undefined;
    const startsAtMs = operatorEffectiveStartsAt ? new Date(operatorEffectiveStartsAt).getTime() : null;
    const endsAtMs = operatorEffectiveEndsAt ? new Date(operatorEffectiveEndsAt).getTime() : null;
    const now = Date.now();
    let nextBoundary = null;
    if (Number.isFinite(startsAtMs) && now < startsAtMs) nextBoundary = startsAtMs;
    else if (Number.isFinite(endsAtMs) && now < endsAtMs) nextBoundary = endsAtMs;
    if (nextBoundary === null) return undefined;
    const delay = Math.max(500, nextBoundary - now + 50);
    const timer = window.setTimeout(() => setStatusVersion((value) => value + 1), delay);
    return () => window.clearTimeout(timer);
  }, [settings.enabled, operatorEffectiveEndsAt, settings.finished_at, settings.paused_at, operatorEffectiveStartsAt, statusVersion]);

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

  // Каждый запрос раздела обязан назвать направление: на сервере это два разных
  // прогона на общих таблицах. У оператора сервер всё равно подставит его
  // собственное, но у управляющего параметр — единственный способ узнать, какой
  // именно аукцион он смотрит.
  const withDirection = useCallback(
    (extra = {}) => ({ ...extra, direction }),
    [direction]
  );

  const handleSwitchDirection = useCallback((nextDirection) => {
    const normalized = normalizeAuctionDirection(nextDirection);
    if (normalized === direction) return;
    // Прогоны независимы, поэтому переключение — это ПОЛНЫЙ сброс: иначе на экране
    // чата на секунду оставались бы смены линии, а ETag и курсор событий отдали бы
    // чужой снапшот как «не изменилось».
    setDirection(normalized);
    storeAuctionDirection(normalized);
    snapshotEtagRef.current = '';
    lastEventIdRef.current = 0;
    lastLocallyPatchedEventIdRef.current = 0;
    lastAppliedSnapshotEventIdRef.current = 0;
    auctionDraftDirtyRef.current = false;
    auctionDraftSavedAtRef.current = '';
    serverTimeGroupsRef.current = [];
    setLots([]);
    setJournalEntries([]);
    setJournalTotal(0);
    setViewSchedulePlanId('');
    setPeriodPreviewLots([]);
    // Отмеченные участники — люди ДРУГОГО направления; оставить их значило бы
    // показать чужой состав и отправить его на сохранение.
    setSelectedIds(new Set());
    setQuery('');
    setIsLoading(true);
  }, [direction]);


  const markAuctionDraftDirty = useCallback(() => {
    auctionDraftDirtyRef.current = true;
    auctionDraftRevisionRef.current += 1;
  }, []);

  const updateDraftEnabled = useCallback((value) => {
    markAuctionDraftDirty();
    setDraftEnabled(Boolean(value));
  }, [markAuctionDraftDirty]);

  const updateDraftNote = useCallback((value) => {
    markAuctionDraftDirty();
    setDraftNote(String(value ?? ''));
  }, [markAuctionDraftDirty]);

  const updateDraftStartsAt = useCallback((value) => {
    markAuctionDraftDirty();
    setDraftStartsAt(value || '');
  }, [markAuctionDraftDirty]);

  const updateDraftEndsAt = useCallback((value) => {
    markAuctionDraftDirty();
    setDraftEndsAt(value || '');
  }, [markAuctionDraftDirty]);

  const updateDraftTimeGroups = useCallback((next) => {
    markAuctionDraftDirty();
    setDraftTimeGroups(next);
  }, [markAuctionDraftDirty]);

  const updateDraftSchedulePlanId = useCallback((value) => {
    markAuctionDraftDirty();
    setDraftSchedulePlanId(value == null ? '' : String(value));
  }, [markAuctionDraftDirty]);

  const postClaimLot = useCallback(async (lotId, selection = null) => {
    const body = withDirection({ lot_id: lotId, action: 'claim' });
    // Чат разбирает смену частями прямо в ходе аукциона — интервал едет сюда же.
    if (selection?.start_time && selection?.end_time) {
      body.claim_start_time = selection.start_time;
      body.claim_end_time = selection.end_time;
    }
    const response = await axios.post(
      `${apiRoot}/api/shift_auction/test_lots/claim`,
      body,
      { headers: buildHeaders() }
    );
    return { data: response?.data || {} };
  }, [apiRoot, buildHeaders, withDirection]);

  const postAuctionClaimLotApi = useCallback(async (lotOrId, selection = {}) => {
    const lot = lotOrId && typeof lotOrId === 'object' ? lotOrId : null;
    const sourceShiftId = normalizeSchedulePlanId(lot?.source_schedule_shift_id);
    const sourcePlanId = normalizeSchedulePlanId(lot?.source_schedule_plan_id);
    const numericLotId = Number(lot ? lot.id : lotOrId);
    const payload = sourceShiftId && sourcePlanId && !Number.isFinite(numericLotId)
      ? withDirection({ schedule_plan_id: sourcePlanId, source_schedule_shift_id: sourceShiftId })
      : withDirection({ lot_id: lot ? lot.id : lotOrId });
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
  }, [apiRoot, buildHeaders, withDirection]);

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
    const snapshotUpdatedAt = safe.updated_at || '';
    const shouldHydrateAuctionDraft = shouldHydrateShiftAuctionDraft({
      dirty: auctionDraftDirtyRef.current,
      snapshotUpdatedAt,
      pendingSavedAt: auctionDraftSavedAtRef.current,
    });
    const periods = Array.isArray(safe.available_periods) ? safe.available_periods : [];
    const selectedSchedulePlanId = normalizeSchedulePlanId(
      safe.selected_schedule_plan_id ?? safe.selected_period?.id
    );

    setSettings({
      enabled: Boolean(safe.enabled),
      launch_note: safe.launch_note || '',
      starts_at: safe.starts_at || null,
      ends_at: safe.ends_at || null,
      time_groups: Array.isArray(safe.time_groups) ? safe.time_groups : [],
      my_time_group: safe.my_time_group || null,
      my_effective_starts_at: safe.my_effective_starts_at || null,
      my_effective_ends_at: safe.my_effective_ends_at || null,
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
      rate_lock_enabled: Boolean(safe.rate_lock_enabled),
      has_period_history_access: Boolean(safe.has_period_history_access),
      post_auction_active: Boolean(safe.post_auction_active)
    });
    setNotifyPostClaimEnabled(Boolean(safe.notify_post_claim_enabled));
    // Направление приходит с сервера и здесь — ИСТИНА: у оператора оно выведено из
    // его карточки, а не из тумблера, которого у него нет.
    if (safe.direction_mode) setDirection(normalizeAuctionDirection(safe.direction_mode));
    setCanSwitchDirection(Boolean(safe.can_switch_direction));
    serverTimeGroupsRef.current = Array.isArray(safe.time_groups) ? safe.time_groups : [];
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
    setAvailablePeriods(periods);
    if (shouldHydrateAuctionDraft) {
      setDraftEnabled(Boolean(safe.enabled));
      setDraftNote(safe.launch_note || '');
      setDraftStartsAt(toDateTimeInputValue(safe.starts_at));
      setDraftEndsAt(toDateTimeInputValue(safe.ends_at));
      setSelectedIds(new Set(ids));
      // Groups are re-derived for whichever week the form ends up on (below).
      setTimeGroupsResetToken((token) => token + 1);
      setDraftSchedulePlanId((current) => {
        const restartablePeriods = periods.filter((period) => period?.can_restart !== false);
        const periodIds = new Set(restartablePeriods.map((period) => normalizeSchedulePlanId(period?.id)).filter(Boolean));
        const currentId = normalizeSchedulePlanId(current);
        if (currentId && periodIds.has(currentId)) return String(currentId);
        if (selectedSchedulePlanId && periodIds.has(selectedSchedulePlanId)) return String(selectedSchedulePlanId);
        const firstAvailableId = normalizeSchedulePlanId(restartablePeriods[0]?.id);
        return firstAvailableId ? String(firstAvailableId) : '';
      });
      if (auctionDraftSavedAtRef.current) {
        auctionDraftSavedAtRef.current = '';
      }
    }
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
        params: withDirection({ page, per_page: journalPerPage }),
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
  }, [apiRoot, buildHeaders, journalPerPage, user?.id, withDirection]);

  // Groups belong to a week, so the form always shows the groups of the week it
  // is editing: re-derive them when the picked week changes and when a snapshot
  // is allowed to refresh the draft (the token), never on every snapshot.
  useEffect(() => {
    const planId = normalizeSchedulePlanId(draftSchedulePlanId);
    setDraftTimeGroups(
      (serverTimeGroupsRef.current || [])
        .filter((group) => planId && normalizeSchedulePlanId(group?.plan_id) === planId)
        .map(toDraftTimeGroup)
    );
    setExpandedTimeGroupKey('');
  }, [draftSchedulePlanId, timeGroupsResetToken]);

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
        params: withDirection(),
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
  }, [apiRoot, applySnapshot, buildHeaders, notify, user?.id, withDirection]);

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
        params: withDirection({ schedule_plan_id: normalizedPlanId }),
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
  }, [apiRoot, buildHeaders, user?.id, withDirection]);

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

  // Первая загрузка и перезагрузка после смены направления. Ref, а не сам
  // fetchSnapshot: он пересоздаётся на каждое изменение зависимостей, и эффект
  // с ним в списке дёргал бы запрос кругами.
  useEffect(() => {
    fetchSnapshotRef.current?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [direction]);

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
        const response = await fetch(`${apiRoot}/api/shift_auction/test_events?after=${encodeURIComponent(lastEventIdRef.current || 0)}&direction=${encodeURIComponent(direction)}`, {
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
    // Направление в зависимостях: смена тумблера обязана ПЕРЕОТКРЫТЬ поток, иначе
    // экран чата продолжил бы слушать события линии.
  }, [apiRoot, canOpenStream, direction, user?.id]);

  // Выбирать участников надо из людей СВОЕГО направления: в чат-аукционе — из
  // чатников, а не из операторов линии (их сервер всё равно молча отбросит).
  const operatorOptions = useMemo(
    () => normalizeShiftAuctionOperators(operators, settings.selected_operators, direction),
    [direction, operators, settings.selected_operators]
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
  // Days a group may be moved to: the week around the auction's own start.
  const auctionWeekDates = useMemo(
    () => getWeekDatesForDate(draftStartsAtParts.date),
    [draftStartsAtParts.date]
  );
  // A group takes any operator of the direction, not only those already ticked as
  // participants — joining a group is what puts them into the auction.
  const draftTimeGroupsForSave = draftTimeGroups;
  const timeGroupIssues = useMemo(() => {
    const issues = new Map();
    draftTimeGroups.forEach((group) => {
      const issue = getTimeGroupIssue(group, draftStartsAt, draftEndsAt);
      if (issue) issues.set(group.key, issue);
    });
    return issues;
  }, [draftEndsAt, draftStartsAt, draftTimeGroups]);
  // Which group each participant sits in — drives both the picker (an operator is
  // in one group at a time) and the neutral badge in the participants list.
  const timeGroupByOperatorId = useMemo(() => {
    const map = new Map();
    draftTimeGroups.forEach((group, index) => {
      group.operatorIds.forEach((id) => {
        if (!map.has(id)) map.set(id, { key: group.key, title: getTimeGroupTitle(group, index) });
      });
    });
    return map;
  }, [draftTimeGroups]);
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
  const resolvedMonitoredOperators = useMemo(() => {
    const participantIds = new Set(
      (Array.isArray(monitoredOperators) ? monitoredOperators : [])
        .map((operator) => normalizeOperatorId(operator?.id ?? operator?.operator_id))
        .filter(Boolean)
    );
    const liveParticipants = (Array.isArray(operators) ? operators : [])
      .filter((operator) => participantIds.has(normalizeOperatorId(operator?.id ?? operator?.operator_id)));
    return normalizeShiftAuctionOperators(liveParticipants, monitoredOperators, direction);
  }, [monitoredOperators, operators]);
  const operationalMonitoredOperators = useMemo(
    () => filterOperationalShiftAuctionOperators(resolvedMonitoredOperators, direction),
    [resolvedMonitoredOperators]
  );
  const operationalMonitoredOperatorIds = useMemo(
    () => new Set(operationalMonitoredOperators.map((operator) => Number(operator.id))),
    [operationalMonitoredOperators]
  );
  const operationalMonitoredParticipantWorkloads = useMemo(
    () => (Array.isArray(monitoredParticipantWorkloads) ? monitoredParticipantWorkloads : [])
      .filter((workload) => operationalMonitoredOperatorIds.has(Number(workload?.operator_id))),
    [monitoredParticipantWorkloads, operationalMonitoredOperatorIds]
  );
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

  // Открыли раздел из планировщика чата — переключаемся на его аукцион до того,
  // как применится неделя: иначе список периодов будет от линии, и неделя не найдётся.
  const requestedDirection = initialPeriod?.direction || '';
  useEffect(() => {
    if (!requestedDirection || !canMonitor) return;
    handleSwitchDirection(requestedDirection);
  }, [canMonitor, handleSwitchDirection, requestedDirection]);

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
      updateDraftSchedulePlanId(planId);
      setMonitorTab('settings');
    }
    setAppliedInitialPeriodKey(initialPeriodKey);
    onInitialPeriodApplied?.();
  }, [appliedInitialPeriodKey, availablePeriods, canManage, initialPeriodKey, onInitialPeriodApplied, updateDraftSchedulePlanId]);

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
    () => monitoredLots
      .map((lot) => getMyAuctionClaimEntry(lot, user?.id))
      .filter(Boolean),
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
        .map((lot) => getMyAuctionClaimEntry(lot, user?.id))
        .filter(Boolean)
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

  // Свои смены активного дня — то, что оператор видит в карточке дня.
  // Целиком взятый лот приходит через claimed_by, а взятая в доборе ЧАСТЬ живёт
  // только в lot.claim_segments: сам лот при этом остаётся 'available' с пустым
  // claimed_by, поэтому одного claimed_by мало. Из сегмента собираем
  // синтетический лот — тогда часы и подпись считают те же помощники, что и для
  // обычной смены, вместе с перерывами внутри взятого окна.
  const myActiveDayClaimRows = useMemo(() => {
    if (canMonitor || !activeDayDate) return [];
    const rows = collectMyAuctionDayClaims({
      lots: monitoredLots,
      date: activeDayDate,
      userId: user?.id
    }).map(({ key, claimLot, lot }) => {
      const range = getAuctionLotEffectiveMinuteRange(claimLot);
      return {
        key,
        lot,
        // Окно возврата показываем по МОЕЙ доле, а не по всей смене: у чата
        // 09:00–15:00 из 09:00–21:00 — это шесть часов, а не двенадцать.
        claimLot,
        start: range ? range[0] : 0,
        timeLabel: formatAuctionLotEffectiveTimeRangeLabel(claimLot),
        netMinutes: getAuctionLotNetMinutes(claimLot),
        partial: isPartialPostAuctionClaim(claimLot),
        originalLabel: formatAuctionShiftLabel(claimLot)
      };
    });
    return rows.sort((a, b) => a.start - b.start || String(a.key).localeCompare(String(b.key)));
  }, [activeDayDate, canMonitor, monitoredLots, user?.id]);

  const myActiveDayClaimNetMinutes = useMemo(
    () => myActiveDayClaimRows.reduce((sum, row) => sum + Number(row.netMinutes || 0), 0),
    [myActiveDayClaimRows]
  );

  const activeDayNavigationItem = useMemo(
    () => dayNavigationItems.find((item) => item.date === activeDayDate) || null,
    [activeDayDate, dayNavigationItems]
  );

  useEffect(() => {
    if (!dayNavigationItems.length) {
      setActiveDayDate('');
      setIsDayDetailsOpen(false);
      return;
    }
    const stillThere = activeDayDate && dayNavigationItems.some((item) => item.date === activeDayDate);
    if (!stillThere) {
      // Неделю переключили — открытая карточка уже не про эту дату.
      setActiveDayDate(dayNavigationItems[0].date);
      setIsDayDetailsOpen(false);
    }
  }, [activeDayDate, dayNavigationItems]);

  const runtimeStatus = useMemo(
    () => getAuctionRuntimeStatus(settings, Date.now(), operatorEffectiveStartsAt, operatorEffectiveEndsAt),
    // statusVersion forces re-evaluation when a scheduled/open boundary is crossed
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [settings.enabled, operatorEffectiveEndsAt, settings.finished_at, settings.paused_at, operatorEffectiveStartsAt, statusVersion]
  );
  const hasStartCountdown = runtimeStatus === 'scheduled' && Boolean(operatorEffectiveStartsAt);
  const hasCloseCountdown = runtimeStatus === 'open' && Boolean(operatorEffectiveEndsAt);
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
  // key у каждой ветки: статус-бар висит на экране постоянно и тикает раз в
  // секунду. Без key React переиспользует один и тот же узел и правит текст
  // на месте — на переведённом браузером DOM это заканчивается removeChild.
  const auctionStatusDetail = hasStartCountdown
    ? <AuctionCountdownText key="status-detail-start" target={operatorEffectiveStartsAt} />
    : hasCloseCountdown
      ? <span key="status-detail-close">до закрытия <AuctionCountdownText target={operatorEffectiveEndsAt} /></span>
      : <span key="status-detail-text">{auctionStatusDetailText}</span>;
  const auctionStatusShortDetail = hasCloseCountdown
    ? <AuctionCountdownText key="status-short-close" target={operatorEffectiveEndsAt} />
    : hasStartCountdown
      ? <AuctionCountdownText key="status-short-start" target={operatorEffectiveStartsAt} />
      : <span key="status-short-text">{auctionStatusDetailText}</span>;
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
  // "Свой график" is a property of the viewer's week time group. Its allowance
  // is the ceiling for every shift they take, self-placed or claimed.
  const selfScheduleAllowanceMinutes = settings.my_time_group?.self_schedule_enabled
    ? AUCTION_SELF_SCHEDULE_EXTRA_MINUTES
    : 0;
  const canSelfSchedule = canClaim && !canMonitor && Boolean(settings.my_time_group?.self_schedule_enabled);
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
    // What actually limits the operator: the norm, plus the "свой график"
    // allowance when their group has one. Everyone else: ceiling === norm.
    const ceilingMinutes = normMinutes + selfScheduleAllowanceMinutes;
    const claimedNetMinutes = myClaimedLots.reduce((sum, lot) => sum + getAuctionLotNetMinutes(lot), 0);
    const claimedBreakMinutes = myClaimedLots.reduce((sum, lot) => sum + getAuctionLotBreakMinutes(lot), 0);
    const remainingMinutes = Math.max(0, ceilingMinutes - claimedNetMinutes);
    const overMinutes = Math.max(0, claimedNetMinutes - ceilingMinutes);
    const progress = ceilingMinutes > 0 ? clampNumber((claimedNetMinutes / ceilingMinutes) * 100, 0, 140) : 0;
    return {
      workdayCount,
      normMinutes,
      ceilingMinutes,
      claimedNetMinutes,
      claimedBreakMinutes,
      remainingMinutes,
      overMinutes,
      progress,
      isComplete: ceilingMinutes > 0 && claimedNetMinutes >= ceilingMinutes - 1
    };
  }, [lotDates.length, myBlockedDateMap.size, myClaimedLots, selfScheduleAllowanceMinutes, userRate]);

  // Everything the "своя смена" dialog needs: the shift the chosen start yields,
  // what is still left of the norm+allowance ceiling, and why it may be blocked.
  const selfScheduleGroup = useMemo(
    () => getSelfScheduleShiftGroup(userRate, selfScheduleStart),
    [selfScheduleStart, userRate]
  );
  const selfScheduleEndTime = useMemo(
    () => computeAuctionEndTime(selfScheduleStart, selfScheduleGroup),
    [selfScheduleGroup, selfScheduleStart]
  );
  const selfScheduleRemainingMinutes = myAuctionWorkload.remainingMinutes;
  const selfScheduleIssue = useMemo(() => {
    if (!selfScheduleDate) return '';
    if (!selfScheduleEndTime) return 'Не удалось определить длину смены по вашей ставке';
    const shiftMinutes = Number(selfScheduleGroup?.shiftMinutes || 0);
    if (myAuctionWorkload.normMinutes > 0 && shiftMinutes > selfScheduleRemainingMinutes + 1) {
      return `Не хватает лимита: смена ${formatAuctionHours(shiftMinutes)} ч, осталось ${formatAuctionHours(selfScheduleRemainingMinutes)} ч`;
    }
    const startMs = getLotStartDateTimeMs({ shift_date: selfScheduleDate, start_time: selfScheduleStart });
    if (startMs !== null && startMs <= Date.now()) return 'Это время уже прошло';
    return '';
  }, [
    myAuctionWorkload.normMinutes,
    selfScheduleDate,
    selfScheduleEndTime,
    selfScheduleGroup,
    selfScheduleRemainingMinutes,
    selfScheduleStart
  ]);

  const operatorWorkloadRows = useMemo(() => {
    if (!canMonitor) return [];
    const operatorsById = new Map(
      (operationalMonitoredOperators || [])
        .filter((operator) => operator && operator.id != null)
        .map((operator) => [Number(operator.id), operator])
    );
    return (operationalMonitoredParticipantWorkloads || [])
      .map((workload) => {
        if (!workload || workload.operator_id == null) return null;
        const operator = operatorsById.get(Number(workload.operator_id));
        if (!operator) return null;
        // Progress is measured against what limits the operator: the norm, or the
        // wider ceiling of a "свой график" group.
        const ceilingMinutes = Number(workload.ceiling_minutes || workload.norm_minutes || 0);
        const claimedNet = Number(workload.claimed_net_minutes || 0);
        const overMinutes = Number(workload.over_minutes || 0);
        const isComplete = Boolean(workload.is_complete);
        const progress = ceilingMinutes > 0
          ? clampNumber((claimedNet / ceilingMinutes) * 100, 0, 140)
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
  }, [canMonitor, operationalMonitoredOperators, operationalMonitoredParticipantWorkloads]);

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
    const operator = (operationalMonitoredOperators || []).find((op) => Number(op?.id) === opIdNum) || null;
    if (!operator) return null;
    const workload = (operationalMonitoredParticipantWorkloads || []).find((w) => Number(w?.operator_id) === opIdNum) || null;
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
  }, [drilldownOperatorId, monitoredLots, operationalMonitoredOperators, operationalMonitoredParticipantWorkloads]);

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

  // Даты уже взятых НОЧЕЙ. Ночь — ровно 20:00–08:00 (решение владельца
  // 29.08.2026: «смена 20*08»); вечерние 19:30–02:00 и им подобные ночью не
  // считаются. Держим отдельным набором, чтобы серую подпись у соседней ночи
  // можно было показать до клика, а не ловить ошибкой с сервера.
  // Фактически занятое время оператора: у взятой ЧАСТИ смены это её границы, а не
  // окно лота — иначе после 09:00–15:00 из 09:00–21:00 человек считался бы занятым
  // до девяти вечера и добрать часы в тот же день не смог бы.
  const myClaimedIntervals = useMemo(
    () => myClaimedLots
      .map((lot) => ({
        shift_date: lot?.shift_date,
        start_time: getAuctionLotEffectiveStartTime(lot),
        end_time: getAuctionLotEffectiveEndTime(lot)
      }))
      .filter((item) => item.shift_date && item.start_time && item.end_time),
    [myClaimedLots]
  );

  const myClaimedNightDates = useMemo(
    () => new Set(
      myClaimedLots
        .filter((lot) => isAuctionNightShift(lot?.start_time, lot?.end_time))
        .map((lot) => lot?.shift_date)
        .filter(Boolean)
    ),
    [myClaimedLots]
  );

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

  // Разбор смены на части — правило ЧАТА. У линии смена берётся целиком, как и была.
  const supportsPartialClaim = direction === AUCTION_DIRECTION_CHAT;
  // Недельный потолок по ставке — только для подписи в окне выбора части.
  const weeklyRateHoursLabel = useMemo(() => {
    const hours = auctionWeeklyHoursForRate(userRate);
    return hours === null ? '' : formatRate(hours);
  }, [userRate]);

  // Свободные части смен для текущего прогона: тот же расчёт, что у добора —
  // из окна смены вычитаются уже занятые куски и собственные смены оператора.
  const auctionPartialClaimOptionsByLotId = useMemo(() => {
    const map = new Map();
    if (!supportsPartialClaim || canMonitor || !canClaim) return map;
    (monitoredLots || []).forEach((lot) => {
      if (!lot || lot.status !== 'available') return;
      // Часть можно взять только у смены из недельного плана: занятые куски
      // хранятся парой (план, смена), а у добавленного вручную лота её нет.
      if (!lot.source_schedule_plan_id || !lot.source_schedule_shift_id) return;
      const lotId = getAuctionLotActionKey(lot);
      if (!lotId) return;
      const option = buildPostAuctionClaimOption(
        lot,
        [],
        myClaimedLotsByDate.get(lot.shift_date) || []
      );
      if (option) map.set(lotId, option);
    });
    return map;
  }, [canClaim, canMonitor, monitoredLots, myClaimedLotsByDate, supportsPartialClaim]);

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
      if (settings.rate_lock_enabled) {
        const lotRate = getAuctionLotDurationRate(lot);
        const myRateBucket = userRate <= 0.5 ? 0.5 : (userRate <= 0.75 ? 0.75 : 1);
        if (Math.abs(lotRate - myRateBucket) > 0.001) {
          reasons.set(lotId, `Смена ставки ${formatRate(lotRate)} — вам доступны только смены ставки ${formatRate(myRateBucket)}`);
          return;
        }
      }
      // Две ночи 20:00–08:00 подряд не даём — правило общее с сервером и
      // действует ДО проверки режима добора: в доборе снят лимит «одна смена в
      // день», но не право на отдых между ночами.
      if (isAuctionNightShift(lot.start_time, lot.end_time)) {
        const neighbourNight = auctionAdjacentDates(lot.shift_date)
          .find((date) => myClaimedNightDates.has(date));
        if (neighbourNight) {
          reasons.set(lotId, `Ночь ${formatAuctionShortDate(neighbourNight)} уже ваша — две ночи подряд нельзя`);
          return;
        }
      }
      // День запирается первой сменой не всегда. Лимит снимают два случая:
      //  - режим добора — там сняты и норма, и календарный лимит;
      //  - ЧАТ (решение владельца 29.08.2026) — добрать часы в том же дне можно,
      //    если они не перекрывают уже взятое; норма при этом остаётся, поэтому
      //    ниже мы НЕ выходим и проверки потолка отрабатывают как обычно.
      if (isTopupActive || supportsPartialClaim) {
        // В чате смену берут частью, поэтому пересечение целого окна — ещё не
        // отказ: отказ, когда свободного куска не осталось вовсе. Свободные куски
        // уже посчитаны с вычетом взятого мной и коллегами.
        const partialOption = supportsPartialClaim ? auctionPartialClaimOptionsByLotId.get(lotId) : null;
        if (partialOption) {
          if (!partialOption.canClaim) {
            reasons.set(lotId, 'Нет свободного интервала без пересечения');
            return;
          }
        } else {
          const conflict = findAuctionClaimConflict(myClaimedIntervals, lot);
          if (conflict) {
            reasons.set(lotId, `Пересекается с ${conflict.start_time}–${conflict.end_time}`);
            return;
          }
        }
        // Добор сверх нормы на то и добор — потолок часов ему не считают.
        if (isTopupActive) return;
      } else if (lot.shift_date && myClaimedDateSet.has(lot.shift_date)) {
        reasons.set(lotId, 'На этот день уже выбрана смена');
        return;
      }
      const netMinutes = getAuctionLotNetMinutes(lot);
      // "Свой график" raises the ceiling for every shift these operators take,
      // so the grid must not grey lots out at the plain norm.
      const ceilingMinutes = myAuctionWorkload.ceilingMinutes;
      if (myAuctionWorkload.normMinutes > 0 && myAuctionWorkload.claimedNetMinutes >= ceilingMinutes - 1) {
        reasons.set(lotId, selfScheduleAllowanceMinutes ? 'Лимит часов уже набран' : 'Норма уже набрана');
        return;
      }
      // В чате смену берут частью, поэтому «целиком не влезает в норму» — не отказ:
      // человек доберёт ровно столько часов, сколько осталось. Гасит такой лот
      // только проверка выше, когда норма набрана совсем.
      const canTakePart = supportsPartialClaim
        && Boolean(auctionPartialClaimOptionsByLotId.get(lotId)?.canClaim);
      if (
        !canTakePart
        && myAuctionWorkload.normMinutes > 0
        && myAuctionWorkload.claimedNetMinutes + netMinutes > ceilingMinutes + 1
      ) {
        reasons.set(lotId, `Превысит лимит на ${formatAuctionHours(myAuctionWorkload.claimedNetMinutes + netMinutes - ceilingMinutes)} ч`);
      }
    });
    return reasons;
  }, [auctionPartialClaimOptionsByLotId, canMonitor, isTester, isTopupActive, isViewingActivePeriod, monitoredLots, myAuctionWorkload, myBlockedDateMap, myClaimedDateSet, myClaimedIntervals, postAuctionClaimOptionsByLotId, selectedViewPostAuctionActive, selfScheduleAllowanceMinutes, settings.has_period_history_access, settings.rate_lock_enabled, supportsPartialClaim, userRate]);

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
    setIsDayDetailsOpen(true);
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
  }, [lotDates]);

  const toggleOperator = useCallback((operatorId) => {
    const id = normalizeOperatorId(operatorId);
    if (!id) return;
    markAuctionDraftDirty();
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
        // Dropping a participant also drops them from their time group.
        setDraftTimeGroups((groups) => (
          groups.some((group) => group.operatorIds.includes(id))
            ? groups.map((group) => (
              group.operatorIds.includes(id)
                ? { ...group, operatorIds: group.operatorIds.filter((memberId) => memberId !== id) }
                : group
            ))
            : groups
        ));
      } else {
        next.add(id);
      }
      return next;
    });
  }, [markAuctionDraftDirty]);

  const addDraftTimeGroup = useCallback(() => {
    const mainStart = splitDateTimeInputValue(draftStartsAt);
    setDraftTimeGroups((groups) => {
      if (groups.length >= AUCTION_TIME_GROUP_LIMIT) return groups;
      const group = createDraftTimeGroup(groups.length, mainStart.date, mainStart.time);
      setExpandedTimeGroupKey(group.key);
      setTimeGroupMemberQuery('');
      return [...groups, group];
    });
    markAuctionDraftDirty();
  }, [draftStartsAt, markAuctionDraftDirty]);

  const patchDraftTimeGroup = useCallback((key, patch) => {
    markAuctionDraftDirty();
    setDraftTimeGroups((groups) => groups.map((group) => (group.key === key ? { ...group, ...patch } : group)));
  }, [markAuctionDraftDirty]);

  const removeDraftTimeGroup = useCallback((key) => {
    markAuctionDraftDirty();
    setDraftTimeGroups((groups) => groups.filter((group) => group.key !== key));
    setExpandedTimeGroupKey((current) => (current === key ? '' : current));
  }, [markAuctionDraftDirty]);

  // One operator belongs to at most one group per week, so joining a group also
  // leaves the previous one — the same rule the DB enforces.
  const toggleTimeGroupMember = useCallback((key, operatorId) => {
    const id = normalizeOperatorId(operatorId);
    if (!id) return;
    markAuctionDraftDirty();
    setDraftTimeGroups((groups) => {
      const target = groups.find((group) => group.key === key);
      const isMember = Boolean(target?.operatorIds.includes(id));
      return groups.map((group) => {
        if (group.key === key) {
          return {
            ...group,
            operatorIds: isMember
              ? group.operatorIds.filter((memberId) => memberId !== id)
              : [...group.operatorIds, id]
          };
        }
        if (!isMember && group.operatorIds.includes(id)) {
          return { ...group, operatorIds: group.operatorIds.filter((memberId) => memberId !== id) };
        }
        return group;
      });
    });
    if (!draftTimeGroups.find((group) => group.key === key)?.operatorIds.includes(id)) {
      // Group membership implies participation.
      setSelectedIds((selected) => {
        if (selected.has(id)) return selected;
        const next = new Set(selected);
        next.add(id);
        return next;
      });
    }
  }, [draftTimeGroups, markAuctionDraftDirty]);

  const selectAllFilteredOperators = useCallback(() => {
    if (!filteredOperators.length) return;
    markAuctionDraftDirty();
    setSelectedIds((current) => {
      const next = new Set(current);
      filteredOperators.forEach((operator) => {
        const id = normalizeOperatorId(operator?.id);
        if (id) next.add(id);
      });
      return next;
    });
  }, [filteredOperators, markAuctionDraftDirty]);

  const clearSelectedOperators = useCallback(() => {
    markAuctionDraftDirty();
    setSelectedIds(new Set());
    setDraftTimeGroups((groups) => groups.map((group) => ({ ...group, operatorIds: [] })));
  }, [markAuctionDraftDirty]);

  const openSelfSchedule = useCallback((date) => {
    if (!date) return;
    setSelfScheduleStart('09:00');
    setSelfScheduleDate(date);
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
    if (timeGroupIssues.size) {
      notify(timeGroupIssues.values().next().value, 'error');
      return;
    }
    const draftRevisionAtSave = auctionDraftRevisionRef.current;
    setIsSaving(true);
    try {
      const response = await axios.put(
        `${apiRoot}/api/shift_auction/test_access`,
        {
          direction,
          enabled: draftEnabled,
          launch_note: draftNote,
          starts_at: draftStartsAt || null,
          ends_at: draftEndsAt || null,
          schedule_plan_id: selectedDraftPeriod?.id || null,
          operator_ids: Array.from(selectedIds),
          // Groups are saved for the week being picked; without a week there is
          // nothing to attach them to, so the field is left out entirely.
          ...(selectedDraftPeriod?.id
            ? { time_groups: buildTimeGroupsPayload(draftTimeGroupsForSave, draftStartsAt, draftEndsAt) }
            : {})
        },
        { headers: buildHeaders() }
      );
      const savedAccess = response?.data?.test_access || {};
      const hasNewerUnsavedChanges = auctionDraftRevisionRef.current !== draftRevisionAtSave;
      if (!hasNewerUnsavedChanges) {
        const savedIds = (savedAccess.selected_operator_ids || Array.from(selectedIds))
          .map(normalizeOperatorId)
          .filter(Boolean);
        setSelectedIds(new Set(savedIds));
        auctionDraftDirtyRef.current = false;
        auctionDraftSavedAtRef.current = savedAccess.updated_at || '';
      }
      // A successful PUT changes both settings and the snapshot event cursor.
      // Bypass a now-stale conditional GET and reconcile with the new state.
      snapshotEtagRef.current = '';
      await fetchSnapshot({ silent: true });
      if (hasNewerUnsavedChanges) {
        notify('Настройки на момент нажатия сохранены. Новые изменения в форме ещё не сохранены.', 'warning');
      } else {
        notify(selectedDraftPeriod ? `Аукцион сохранен для недели ${formatAuctionPeriodLabel(selectedDraftPeriod)}` : 'Настройки тестового аукциона сохранены');
      }
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось сохранить настройки аукциона смен', 'error');
    } finally {
      setIsSaving(false);
    }
  }, [apiRoot, buildHeaders, canManage, direction, draftEnabled, draftEndsAt, draftNote, draftRangeInvalid, draftSchedulePlanId, draftStartsAt, draftTimeGroupsForSave, timeGroupIssues, fetchSnapshot, notify, selectedDraftPeriod, selectedIds]);

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
        withDirection({ schedule_plan_id: selectedDraftPeriod.id }),
        { headers: buildHeaders({ 'Content-Type': 'application/json' }) }
      );
      applySnapshot(response?.data?.snapshot || {});
      notify(`Аукцион запущен заново: ${formatAuctionPeriodLabel(response?.data?.period || selectedDraftPeriod)}`);
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось начать аукцион заново', 'error');
    } finally {
      setIsRestarting(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, canManage, notify, selectedDraftPeriod, withDirection]);

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
        withDirection({ action }),
        { headers: buildHeaders({ 'Content-Type': 'application/json' }) }
      );
      applySnapshot(response?.data?.snapshot || {});
      notify(actionMessages[action] || 'Состояние аукциона обновлено');
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось изменить состояние аукциона', 'error');
    } finally {
      setIsControllingAuction(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, canManage, isControllingAuction, notify, withDirection]);

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
        headers: buildHeaders({ 'Content-Type': 'application/json' }),
        data: withDirection()
      });
      applySnapshot(response?.data?.snapshot || {});
      notify(enable ? 'Аукцион переведён в режим добора смен' : 'Режим добора отключён');
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось изменить режим добора', 'error');
    } finally {
      setIsTogglingTopup(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, canManage, isTogglingTopup, notify, settings.topup_started_at, withDirection]);

  const handleToggleRateLock = useCallback(async () => {
    if (!canManage || !apiRoot || isTogglingRateLock) return;
    const enable = !settings.rate_lock_enabled;
    setIsTogglingRateLock(true);
    try {
      const response = await axios({
        method: enable ? 'POST' : 'DELETE',
        url: `${apiRoot}/api/shift_auction/test_rate_lock`,
        headers: buildHeaders({ 'Content-Type': 'application/json' }),
        data: withDirection()
      });
      applySnapshot(response?.data?.snapshot || {});
      notify(enable
        ? 'Теперь операторы могут брать только смены своей ставки'
        : 'Ограничение по ставке снято — доступны смены любой ставки');
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось изменить ограничение по ставке', 'error');
    } finally {
      setIsTogglingRateLock(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, canManage, isTogglingRateLock, notify, settings.rate_lock_enabled, withDirection]);

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
        withDirection(),
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
    const shiftStartMs = getLotStartDateTimeMs({
      shift_date: addShiftTarget.date,
      start_time: start
    });
    if (shiftStartMs === null || shiftStartMs <= Date.now()) {
      notify('Нельзя добавить смену: дата и время начала уже прошли', 'error');
      return;
    }
    setIsAddingShift(true);
    try {
      const response = await axios.post(
        `${apiRoot}/api/shift_auction/admin/add_lot`,
        withDirection({
          shift_date: addShiftTarget.date,
          start_time: start,
          end_time: end,
          rate_min: addShiftTarget.rate
        }),
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
  }, [addShiftTarget, addShiftStart, apiRoot, applySnapshot, buildHeaders, fetchSnapshot, isAddingShift, notify, withDirection]);

  const handleSubmitSelfSchedule = useCallback(async () => {
    if (!selfScheduleDate || !apiRoot || isSelfScheduling) return;
    setIsSelfScheduling(true);
    try {
      const response = await axios.post(
        `${apiRoot}/api/shift_auction/self_schedule`,
        withDirection({ shift_date: selfScheduleDate, start_time: selfScheduleStart }),
        { headers: buildHeaders({ 'Content-Type': 'application/json' }) }
      );
      if (response?.data?.snapshot) {
        applySnapshot(response.data.snapshot);
      } else {
        await fetchSnapshot({ silent: true });
      }
      const lot = response?.data?.lot || {};
      notify(`Смена ${lot.start_time || selfScheduleStart}–${lot.end_time || ''} поставлена`);
      setSelfScheduleDate('');
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось поставить смену', 'error');
    } finally {
      setIsSelfScheduling(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, fetchSnapshot, isSelfScheduling, notify, selfScheduleDate, selfScheduleStart, withDirection]);

  const handleExportAuctionReport = useCallback(async () => {
    if (!canManage || !apiRoot || isExportingAuctionReport) return;
    setIsExportingAuctionReport(true);
    try {
      const response = await axios.get(
        `${apiRoot}/api/shift_auction/test_export_excel`,
        {
          params: withDirection(),
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
  }, [apiRoot, buildHeaders, canManage, isExportingAuctionReport, notify, withDirection]);

  const handleClaimLot = useCallback(async (lotId, selection = null) => {
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

    // В чате смену разбирают по частям: без явно выбранного интервала сначала
    // открываем таймлайн, и только потом уходит запрос.
    if (supportsPartialClaim && !selection) {
      const option = auctionPartialClaimOptionsByLotId.get(lotKey);
      if (option) {
        const segment = option.recommendedSegment;
        if (!segment) {
          notifyClaimError('У этой смены не осталось свободного интервала');
          return;
        }
        setPartialClaimSelection({ start_time: segment.start_time, end_time: segment.end_time });
        setPartialClaimLot(prevLot);
        return;
      }
    }

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
      const response = await enqueueAuctionMutation(() => postClaimLot(numericId, selection));
      const serverLot = response?.data?.lot;
      // Частично взятая смена остаётся свободной для остальных: оптимистичную
      // «мою» отметку в этом случае откатываем и ждём снапшот с сегментами.
      if (serverLot?.lot_still_available) {
        setPartialClaimLot(null);
        setPartialClaimSelection({ start_time: '', end_time: '' });
        await fetchSnapshot({ silent: true });
      } else if (serverLot && serverLot.id) {
        setPartialClaimLot(null);
        setPartialClaimSelection({ start_time: '', end_time: '' });
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
  }, [
    apiRoot,
    auctionPartialClaimOptionsByLotId,
    canClaim,
    claimBlockReasonByLotId,
    enqueueAuctionMutation,
    fetchSnapshot,
    notifyClaimError,
    postClaimLot,
    supportsPartialClaim,
    user?.id
  ]);

  // «Добрать часы»: ближайший по времени свободный кусок, который оператору сейчас
  // не запрещён. Кнопка нужна, чтобы добор не приходилось выискивать глазами по сетке.
  const nextTopupCandidate = useMemo(() => {
    if (!supportsPartialClaim || canMonitor || !canClaim) return null;
    const candidates = [];
    (monitoredLots || []).forEach((lot) => {
      if (!lot || lot.status !== 'available') return;
      const lotKey = getAuctionLotActionKey(lot);
      if (!lotKey || claimBlockReasonByLotId.get(lotKey)) return;
      const option = auctionPartialClaimOptionsByLotId.get(lotKey);
      const segment = option?.recommendedSegment;
      if (!segment) return;
      candidates.push({ lot, segment, date: lot.shift_date || '', start: segment.start });
    });
    candidates.sort((a, b) => String(a.date).localeCompare(String(b.date)) || a.start - b.start);
    return candidates[0] || null;
  }, [auctionPartialClaimOptionsByLotId, canClaim, canMonitor, claimBlockReasonByLotId, monitoredLots, supportsPartialClaim]);

  const handleTopupMoreHours = useCallback(() => {
    if (!nextTopupCandidate) {
      notifyClaimError('Свободных смен, которые можно добрать, сейчас нет');
      return;
    }
    const { lot, segment } = nextTopupCandidate;
    setActiveDayDate(lot.shift_date || '');
    setPartialClaimSelection({ start_time: segment.start_time, end_time: segment.end_time });
    setPartialClaimLot(lot);
  }, [nextTopupCandidate, notifyClaimError]);

  const handleClosePartialClaim = useCallback(() => {
    setPartialClaimLot(null);
    setPartialClaimSelection({ start_time: '', end_time: '' });
  }, []);

  const handleConfirmPartialClaim = useCallback(async () => {
    const lot = partialClaimLot;
    if (!lot?.id) return;
    const option = auctionPartialClaimOptionsByLotId.get(getAuctionLotActionKey(lot));
    if (option && !isSelectionInsideAvailableSegments(lot, partialClaimSelection, option.availableSegments)) {
      notifyClaimError('Выбранный интервал пересекается с занятым временем');
      return;
    }
    await handleClaimLot(lot.id, partialClaimSelection);
  }, [
    auctionPartialClaimOptionsByLotId,
    handleClaimLot,
    notifyClaimError,
    partialClaimLot,
    partialClaimSelection
  ]);

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
        params: withDirection(),
        headers: buildHeaders()
      });
      setMyClaims(Array.isArray(response?.data?.claims) ? response.data.claims : []);
      setMyClaimsFetchedAt(Date.now());
    } catch (error) {
      setMyClaimsError(error?.response?.data?.error || 'Не удалось загрузить взятые смены');
    } finally {
      setMyClaimsLoading(false);
    }
  }, [apiRoot, buildHeaders, user?.id, withDirection]);

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
        withDirection(payload),
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
    selectedViewSchedulePlanId,
    withDirection
  ]);

  // Обратный отсчёт окна отмены тикает внутри MyPostClaimRow — раздел целиком
  // раз в секунду больше не перерисовывается.

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
    // A self-scheduled shift is not handed back to the pool — it disappears.
    const isSelfScheduled = Boolean(prevLot?.self_scheduled);

    // Возврат ЧАСТИ смены (так разбирает смены чат): моя доля живёт строкой в
    // lot.claim_segments, а сам лот мог остаться 'available'. Ни ответ ручки, ни
    // событие сегменты не приносят, поэтому свою долю убираем сами — иначе смена
    // осталась бы «моей» в панели дня до следующего полного снапшота.
    const releasedSegment = lot.partial_claim
      ? {
          start: String(getAuctionLotClaimStartTime(lot) || '').slice(0, 5),
          end: String(getAuctionLotClaimEndTime(lot) || '').slice(0, 5)
        }
      : null;
    const dropMyReleasedSegment = (target) => {
      if (!releasedSegment?.start || !releasedSegment?.end) return target;
      const segments = Array.isArray(target?.claim_segments) ? target.claim_segments : [];
      if (!segments.length) return target;
      return {
        ...target,
        claim_segments: segments.filter((segment) => !(
          Number(segment?.claimed_by) === Number(user?.id)
          && String(segment?.start_time || '').slice(0, 5) === releasedSegment.start
          && String(segment?.end_time || '').slice(0, 5) === releasedSegment.end
        ))
      };
    };

    setReleasingLotId(numericId);
    setLots((currentLots) => (
      isSelfScheduled
        ? currentLots.filter((l) => Number(l.id) !== numericId)
        : currentLots.map((l) => (
          Number(l.id) === numericId
            ? dropMyReleasedSegment({
                ...l,
                status: 'available',
                claimed_by: null,
                claimed_at: null,
                claimed_by_name: '',
                _optimistic: true
              })
            : l
        ))
    ));
    setReleaseConfirmLot(null);
    setReleaseConfirmOptions([]);

    try {
      const response = await enqueueAuctionMutation(() => axios.post(
        `${apiRoot}/api/shift_auction/test_lots/claim`,
        withDirection({ lot_id: numericId, action: 'release' }),
        { headers: buildHeaders() }
      ));
      const serverLot = response?.data?.lot;
      if (response?.data?.removed) {
        setLots((currentLots) => currentLots.filter((l) => Number(l.id) !== numericId));
        await fetchSnapshot({ silent: true });
      } else if (serverLot && serverLot.id) {
        setLots((currentLots) => currentLots.map((l) => (
          Number(l.id) === Number(serverLot.id)
            ? dropMyReleasedSegment({ ...l, ...serverLot, _optimistic: false })
            : l
        )));
      }
    } catch (error) {
      const code = error?.response?.data?.code;
      const message = error?.response?.data?.error;

      setLots((currentLots) => (
        isSelfScheduled
          ? currentLots
          : currentLots.map((l) => (
            Number(l.id) === numericId && l._optimistic
              ? { ...prevLot, _optimistic: false }
              : l
          ))
      ));

      await fetchSnapshot({ silent: true });

      const silentCodes = new Set(['LOT_NOT_CLAIMED', 'LOT_NOT_OWNED', 'AUCTION_NOT_OPEN']);
      if (!silentCodes.has(code)) {
        notifyClaimError(message || 'Не удалось вернуть смену');
      }
    } finally {
      setReleasingLotId(null);
    }
  }, [apiRoot, buildHeaders, canClaim, enqueueAuctionMutation, fetchSnapshot, notifyClaimError, releaseConfirmLot, user?.id, withDirection]);

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
      const requestConfig = { headers: buildHeaders(), data: withDirection({ date }) };
      if (selected) {
        await enqueueAuctionMutation(() => axios.delete(`${apiRoot}/api/shift_auction/test_day_off`, requestConfig));
      } else {
        await enqueueAuctionMutation(() => axios.post(`${apiRoot}/api/shift_auction/test_day_off`, withDirection({ date }), { headers: buildHeaders() }));
      }
      await fetchSnapshot({ silent: true });
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось изменить выходной', 'error');
    } finally {
      setDayOffLoadingDate('');
    }
  }, [apiRoot, buildHeaders, canChoose, enqueueAuctionMutation, fetchSnapshot, manualDayOffLimit, myBlockedDateMap, myDayOffs, notify, selectedManualDayOffCount, withDirection]);

  const renderStatusBar = () => {
    const showWorkload = !canMonitor && canUseAuction;
    const progressWidth = clampNumber(myAuctionWorkload.progress, 0, 100);
    const progressTone = myAuctionWorkload.overMinutes > 0 ? 'bg-rose-500' : myAuctionWorkload.isComplete ? 'bg-emerald-500' : 'bg-blue-600';
    const balanceLabel = myAuctionWorkload.overMinutes > 0
      ? `перебор ${formatAuctionHours(myAuctionWorkload.overMinutes)} ч`
      : `осталось ${formatAuctionHours(myAuctionWorkload.remainingMinutes)} ч`;
    const workloadTitle = showWorkload
      ? ` Набрано ${formatAuctionHours(myAuctionWorkload.claimedNetMinutes)} ч из ${formatAuctionHours(myAuctionWorkload.ceilingMinutes)} ч${selfScheduleAllowanceMinutes ? ` (норма ${formatAuctionHours(myAuctionWorkload.normMinutes)} ч + ${formatAuctionHours(selfScheduleAllowanceMinutes)} ч своего графика)` : ''}. Перерывы: ${formatAuctionHours(myAuctionWorkload.claimedBreakMinutes)} ч.`
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
                  {formatAuctionHours(myAuctionWorkload.claimedNetMinutes)} / {formatAuctionHours(myAuctionWorkload.ceilingMinutes)} ч
                </div>
                <div className="truncate text-[10px] text-slate-600 sm:text-[11px]">
                  {balanceLabel} · перерывы {formatAuctionHours(myAuctionWorkload.claimedBreakMinutes)} ч
                </div>
              </div>
              <div className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold sm:text-[11px] ${myAuctionWorkload.overMinutes > 0 ? 'bg-rose-50 text-rose-700' : myAuctionWorkload.isComplete ? 'bg-emerald-50 text-emerald-700' : 'bg-white/70 text-slate-700'}`}>
                {myAuctionWorkload.isComplete
                  ? (selfScheduleAllowanceMinutes ? 'Лимит' : 'Норма')
                  : formatAuctionHours(myAuctionWorkload.remainingMinutes)}
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

  // Строки панели «Мои доп. смены»: одна карточка на один отменяемый добор.
  // Ключ — идентичность добора при отмене, а не (lot|plan|shift): так один и
  // тот же добор не покажется двумя карточками, если запрос вернёт его дважды
  // с разными lot_id, и React-ключи гарантированно уникальны (дубликат ключа
  // ломает согласование и оставляет в DOM «призрачные» узлы).
  const myClaimRows = useMemo(() => {
    const seen = new Set();
    const rows = [];
    (Array.isArray(myClaims) ? myClaims : []).forEach((claim, index) => {
      const identity = getPostClaimIdentity(claim) || `claim-${index}`;
      if (seen.has(identity)) return;
      seen.add(identity);
      rows.push({ claim, actionKey: getPostClaimKey(claim), reactKey: identity });
    });
    return rows;
  }, [myClaims]);

  const releaseOptions = releaseConfirmOptions.length
    ? releaseConfirmOptions
    : (releaseConfirmLot ? [releaseConfirmLot] : []);
  const hasMultipleReleaseOptions = releaseOptions.length > 1;
  // Возврат из карточки дня — только оператору и только пока аукцион открыт на
  // активной неделе. В превью прошлой недели и после закрытия кнопки нет.
  const canReleaseFromDayPanel = !canMonitor && canClaim;

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
                {/* Направление называем в заголовке всегда — и оператору, у которого
                    тумблера нет: иначе непонятно, чьи смены на экране. */}
                <span className="ml-2 inline-flex items-center rounded-full border border-slate-300 bg-slate-100 px-2 py-0.5 align-middle text-[11px] font-semibold text-slate-700 sm:text-xs">
                  {AUCTION_DIRECTION_LABELS[direction]}
                </span>
                {isTopupActive ? (
                  <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-violet-300 bg-violet-100 px-2 py-0.5 align-middle text-[11px] font-semibold text-violet-800 sm:text-xs">
                    <Plus size={12} />
                    Режим добора
                  </span>
                ) : null}
                {settings.rate_lock_enabled ? (
                  <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-sky-300 bg-sky-100 px-2 py-0.5 align-middle text-[11px] font-semibold text-sky-800 sm:text-xs">
                    <Lock size={11} />
                    Только своя ставка
                  </span>
                ) : null}
                {settings.my_time_group && !canMonitor ? (
                  <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-slate-300 bg-slate-100 px-2 py-0.5 align-middle text-[11px] font-semibold text-slate-700 sm:text-xs">
                    <UserCog size={11} />
                    {settings.my_time_group.name}
                  </span>
                ) : null}
              </h1>
              <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-600 sm:text-sm sm:leading-6">
                {isTopupActive
                  ? <>Идёт <b className="text-violet-800">добор смен</b>{settings.topup_started_at ? <> с {formatDateTimeLabel(settings.topup_started_at)}</> : null}{settings.topup_started_by_name ? <> · включил {settings.topup_started_by_name}</> : null}. Можно брать дополнительные смены сверх нормы, если они не пересекаются по времени с уже выбранными.</>
                  : 'Тестовый realtime-раздел для проверки будущего выбора утвержденных смен по направлению.'
                }
                {settings.rate_lock_enabled && !canMonitor
                  ? <> Сейчас можно брать <b className="text-sky-800">только смены своей ставки ({formatRate(userRate <= 0.5 ? 0.5 : (userRate <= 0.75 ? 0.75 : 1))})</b>.</>
                  : null}
                {settings.my_time_group && !canMonitor
                  ? <> Вы в группе <b className="text-slate-900">«{settings.my_time_group.name}»</b>: выбор смен с {formatDateTimeLabel(operatorEffectiveStartsAt)}{operatorEffectiveEndsAt ? <> до {formatDateTimeLabel(operatorEffectiveEndsAt)}</> : null}.</>
                  : null}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 lg:pr-[280px]">
            {canSwitchDirection ? (
              <div
                role="group"
                aria-label="Направление аукциона"
                className="inline-flex h-9 items-center rounded-lg border border-slate-200 bg-slate-100 p-0.5 sm:h-10"
              >
                {AUCTION_DIRECTIONS.map((item) => {
                  const active = direction === item.key;
                  return (
                    <button
                      key={item.key}
                      type="button"
                      onClick={() => handleSwitchDirection(item.key)}
                      aria-pressed={active}
                      className={`inline-flex h-8 min-w-[64px] items-center justify-center rounded-[7px] px-3 text-xs font-semibold transition sm:h-9 sm:text-sm ${
                        active
                          ? 'bg-white text-slate-900 shadow-sm'
                          : 'text-slate-600 hover:text-slate-900'
                      }`}
                    >
                      {item.label}
                    </button>
                  );
                })}
              </div>
            ) : null}
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
            {supportsPartialClaim && !canMonitor && canUseAuction && canClaim ? (
              <button
                type="button"
                onClick={handleTopupMoreHours}
                disabled={!nextTopupCandidate}
                title={nextTopupCandidate
                  ? `Ближайшая свободная часть: ${formatDateLabel(nextTopupCandidate.lot.shift_date)} ${nextTopupCandidate.segment.start_time}–${nextTopupCandidate.segment.end_time}`
                  : 'Свободных смен, которые можно добрать, сейчас нет'}
                className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 text-xs font-semibold text-emerald-800 shadow-sm transition hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60 sm:h-10 sm:flex-none sm:px-4 sm:text-sm"
                aria-label="Добрать часы"
              >
                <Plus size={16} />
                Добрать часы
              </button>
            ) : null}
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
                      ? (settings.my_time_group
                        ? <>Ваша группа «{settings.my_time_group.name}» заходит через <AuctionCountdownText target={operatorEffectiveStartsAt} />.</>
                        : <>Аукцион откроется через <AuctionCountdownText target={operatorEffectiveStartsAt} />.</>)
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
                              {canMonitor && isViewingActivePeriod && runtimeStatus !== 'disabled' ? (
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
                                ? (myShiftCount > 1 ? `Смен: ${myShiftCount}` : formatCompactAuctionClaimLabel(item.myClaimedLot))
                                : '';
                              const myShiftDuration = !canMonitor && item.state === 'shift'
                                ? `${formatAuctionHours(item.myClaimedNetMinutes)} ч`
                                : '';
                              const hoverTone = active ? 'hover:bg-blue-100' : 'hover:bg-slate-50';
                              // A free day of a "свой график" operator is where they put
                              // their own shift — that beats scrolling to the column.
                              const canSelfScheduleHere = Boolean(
                                canSelfSchedule && item.state !== 'shift' && !item.isBlocked && !item.isDayOff
                              );
                              // Клик по дню открывает карточку дня, а не сразу возврат:
                              // смотреть свои смены нужно и после закрытия аукциона, а
                              // возврат живёт кнопкой внутри карточки.
                              const onCellClick = canSelfScheduleHere
                                ? () => openSelfSchedule(item.date)
                                : () => scrollToDay(item.date);
                              const cellTitle = canSelfScheduleHere
                                ? `${formatDateLabel(item.date)} · нажмите, чтобы поставить свою смену`
                                : item.state === 'shift'
                                  ? `${formatDateLabel(item.date)} · нажмите, чтобы посмотреть свои смены`
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
                                  ) : canSelfScheduleHere ? (
                                    <span className="mt-0.5 flex items-center justify-center gap-0.5 text-[10px] font-bold sm:text-[11px]">
                                      <Plus size={11} className="shrink-0" />
                                      Смена
                                    </span>
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
            {isDayDetailsOpen && activeDayDate ? (
              <aside className="fixed inset-x-3 bottom-[66px] z-40 max-h-[58vh] overflow-hidden rounded-2xl bg-white/95 shadow-2xl ring-1 ring-black/5 backdrop-blur-xl xl:inset-x-auto xl:bottom-auto xl:right-3 xl:top-24 xl:w-[360px] xl:max-h-[calc(100vh-7rem)]">
                <div className="flex items-start justify-between gap-3 border-b border-slate-200/70 px-4 py-3.5">
                  <div className="min-w-0">
                    <div className="truncate text-[15px] font-semibold tracking-tight text-slate-900">
                      {formatDateLabel(activeDayDate)}
                    </div>
                    {canMonitor ? (
                      <div key="day-details-admin-subtitle" className="mt-0.5 text-xs text-slate-500">
                        <span>{adminActiveDayClaimCount ? `Взято смен: ${adminActiveDayClaimCount}` : 'Нет взятых смен'}</span>
                      </div>
                    ) : myActiveDayClaimRows.length ? (
                      <div key="day-details-my-subtitle" className="mt-0.5 text-xs text-slate-500">
                        <span>{`Ваши смены: ${myActiveDayClaimRows.length} · ${formatAuctionHours(myActiveDayClaimNetMinutes)} ч`}</span>
                      </div>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsDayDetailsOpen(false)}
                    className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-95"
                    title="Закрыть"
                  >
                    <X size={16} />
                  </button>
                </div>
                <div className="max-h-[calc(58vh-64px)] overflow-y-auto p-3 xl:max-h-[calc(100vh-11rem)]">
                  {/*
                    У каждой ветки свой key, а голый текст завёрнут в <span>:
                    встроенный переводчик оборачивает текстовые узлы в <font>, и
                    без этого removeChild роняет раздел целиком.
                  */}
                  {canMonitor ? (
                    adminActiveDayClaimLots.length ? (
                      <ul key="admin-day-claims" className="space-y-1.5">
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
                      <div key="admin-day-empty" className="rounded-xl border border-dashed border-slate-200 bg-slate-50/60 px-4 py-10 text-center text-sm text-slate-500">
                        <span>В этот день пока никто не взял смены.</span>
                      </div>
                    )
                  ) : myActiveDayClaimRows.length ? (
                    <ul key="my-day-claims" className="space-y-1.5">
                      {myActiveDayClaimRows.map((row) => {
                        // По статусу лота «мою» смену определять нельзя: лот с
                        // взятой ЧАСТЬЮ остаётся 'available' с пустым claimed_by,
                        // пока в нём есть свободный кусок — иначе остальные его не
                        // увидят. С проверкой status === 'claimed' у чата не
                        // возвращалась ни одна часть. Возвращаемость решает
                        // collectMyAuctionDayClaims: он отдаёт lot только тому,
                        // что release действительно снимет.
                        const releasable = Boolean(
                          canReleaseFromDayPanel
                          && !row.partial
                          && row.lot
                          && Number.isFinite(Number(row.lot.id))
                        );
                        return (
                          <li key={`my-day-claim-${row.key}`}>
                            <div className="flex items-center gap-2.5 rounded-xl border border-slate-200/80 bg-white px-3 py-2 shadow-sm">
                              <span className="shrink-0 rounded-lg bg-emerald-50 px-2 py-1 text-[12px] font-semibold tabular-nums text-emerald-800">
                                {row.timeLabel}
                              </span>
                              <span className="min-w-0 flex-1">
                                {row.partial ? (
                                  <span className="inline-flex items-center gap-1 rounded-full bg-orange-100 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-orange-700">
                                    <span className="h-1.5 w-1.5 rounded-full bg-current" />
                                    добор · часть из {row.originalLabel}
                                  </span>
                                ) : null}
                              </span>
                              <span className="shrink-0 text-[12px] tabular-nums text-slate-400">{formatAuctionHours(row.netMinutes)} ч</span>
                              {releasable ? (
                                <button
                                  type="button"
                                  onClick={() => openReleaseConfirm([row.claimLot || row.lot])}
                                  className="shrink-0 rounded-lg border border-rose-200 bg-white px-2.5 py-1 text-[12px] font-semibold text-rose-600 transition hover:bg-rose-50 active:scale-95"
                                  title={row.lot?.self_scheduled ? 'Убрать свою смену' : 'Вернуть смену в аукцион'}
                                >
                                  {row.lot?.self_scheduled ? 'Убрать' : 'Вернуть'}
                                </button>
                              ) : null}
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  ) : (
                    <div key="my-day-empty" className="rounded-xl border border-dashed border-slate-200 bg-slate-50/60 px-4 py-10 text-center text-sm text-slate-500">
                      <span>
                        {activeDayNavigationItem?.isBlocked
                          ? `${activeDayNavigationItem.blockedLabel} · смен в этот день нет`
                          : activeDayNavigationItem?.isDayOff
                            ? 'Вы отметили этот день выходным'
                            : 'В этот день у вас нет смен'}
                      </span>
                    </div>
                  )}
                </div>
              </aside>
            ) : null}
          </section>
        )}

        {canMonitor && monitorTab === 'shifts_table' && (
          <ShiftAuctionShiftsTable
            operators={operationalMonitoredOperators}
            workloads={operationalMonitoredParticipantWorkloads}
            lots={monitoredLots}
            lotDates={lotDates}
            canEdit={canManage}
            apiRoot={apiRoot}
            buildHeaders={buildHeaders}
            notify={notify}
            direction={direction}
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
                            {formatAuctionHours(row.claimed_net_minutes || 0)} / {formatAuctionHours(row.ceiling_minutes || row.norm_minutes || 0)} ч
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
                <div className="flex flex-wrap items-center gap-2">
                  {['scheduled', 'open', 'paused'].includes(runtimeStatus) ? (
                    <div className="inline-flex items-center gap-1 rounded-2xl bg-slate-100 p-1">
                      {runtimeStatus === 'open' ? (
                        <button
                          type="button"
                          onClick={() => handleAuctionControl('pause')}
                          disabled={isControllingAuction}
                          className="inline-flex h-8 items-center justify-center gap-1.5 rounded-xl bg-white px-3 text-xs font-semibold text-amber-700 shadow-sm ring-1 ring-slate-200/70 transition-all hover:text-amber-800 active:scale-[0.97] disabled:cursor-wait disabled:opacity-50 sm:h-9 sm:px-3.5 sm:text-sm"
                        >
                          <PauseCircle size={16} />
                          Пауза
                        </button>
                      ) : null}
                      {runtimeStatus === 'paused' ? (
                        <button
                          type="button"
                          onClick={() => handleAuctionControl('resume')}
                          disabled={isControllingAuction}
                          className="inline-flex h-8 items-center justify-center gap-1.5 rounded-xl bg-white px-3 text-xs font-semibold text-emerald-700 shadow-sm ring-1 ring-slate-200/70 transition-all hover:text-emerald-800 active:scale-[0.97] disabled:cursor-wait disabled:opacity-50 sm:h-9 sm:px-3.5 sm:text-sm"
                        >
                          <PlayCircle size={16} />
                          Возобновить
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => handleAuctionControl('finish')}
                        disabled={isControllingAuction}
                        className="inline-flex h-8 items-center justify-center gap-1.5 rounded-xl px-3 text-xs font-semibold text-rose-600 transition-all hover:bg-rose-50 active:scale-[0.97] disabled:cursor-wait disabled:opacity-50 sm:h-9 sm:px-3.5 sm:text-sm"
                      >
                        <Square size={14} />
                        Завершить
                      </button>
                    </div>
                  ) : null}
                  <button
                    type="button"
                    onClick={handleExportAuctionReport}
                    disabled={isExportingAuctionReport || !isViewingActivePeriod || !lots.length}
                    title={!isViewingActivePeriod ? 'Отчет доступен только для активной недели аукциона' : lots.length ? 'Выгрузить Excel-отчет по выбранному периоду аукциона' : 'Нет смен для выгрузки'}
                    className="inline-flex h-9 items-center justify-center gap-2 rounded-xl bg-slate-100 px-3.5 text-xs font-semibold text-slate-600 transition-all hover:bg-slate-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 sm:h-10 sm:px-4 sm:text-sm"
                  >
                    <Download size={16} />
                    {isExportingAuctionReport ? 'Выгрузка...' : 'Отчет Excel'}
                  </button>
                  {runtimeStatus === 'closed' ? (
                    <button
                      type="button"
                      onClick={handlePublishAuction}
                      disabled={isPublishingAuction}
                      className="inline-flex h-9 items-center justify-center gap-2 rounded-xl bg-emerald-600 px-3.5 text-xs font-semibold text-white shadow-sm transition-all hover:bg-emerald-700 active:scale-[0.98] disabled:cursor-wait disabled:opacity-50 sm:h-10 sm:px-4 sm:text-sm"
                    >
                      <Save size={16} />
                      {isPublishingAuction ? 'Сохранение...' : 'Сохранить в графики'}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={handleRestartAuction}
                    disabled={isRestarting || !selectedDraftPeriod}
                    className="inline-flex h-9 items-center justify-center gap-2 rounded-xl bg-slate-100 px-3.5 text-xs font-semibold text-slate-600 transition-all hover:bg-slate-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 sm:h-10 sm:px-4 sm:text-sm"
                  >
                    <RotateCcw size={16} />
                    {isRestarting ? 'Перезапуск...' : 'Начать заново'}
                  </button>
                  <button
                    type="button"
                    onClick={handleSave}
                    disabled={isSaving}
                    className="inline-flex h-9 items-center justify-center gap-2 rounded-xl bg-blue-600 px-3.5 text-xs font-semibold text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[0.98] disabled:cursor-wait disabled:opacity-50 sm:h-10 sm:px-4 sm:text-sm"
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
                <div className={`${iosCard} divide-y divide-slate-100`}>
                  <div className="flex items-center justify-between gap-4 px-3 py-3 sm:px-4">
                    <div className="flex min-w-0 items-center gap-3">
                      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-blue-50 text-blue-600">
                        <Gavel size={17} />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-sm font-semibold text-slate-900">Аукцион включён</span>
                        <span className="block text-xs text-slate-500">
                          Выбранные операторы видят аукцион. Применяется после «Сохранить».
                        </span>
                      </span>
                    </div>
                    <IosToggle checked={draftEnabled} onChange={updateDraftEnabled} />
                  </div>
                  <div className="flex items-center justify-between gap-4 px-3 py-3 sm:px-4">
                    <div className="flex min-w-0 items-center gap-3">
                      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-violet-50 text-violet-600">
                        <Plus size={17} />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-sm font-semibold text-slate-900">Режим добора</span>
                        <span className="block text-xs text-slate-500">
                          {settings.topup_started_at
                            ? <>Включён {formatDateTimeLabel(settings.topup_started_at)}{settings.topup_started_by_name ? ` · ${settings.topup_started_by_name}` : ''}. Смены сверх нормы без пересечений по времени.</>
                            : runtimeStatus === 'open'
                              ? 'Разрешить брать смены сверх нормы, если они не пересекаются по времени.'
                              : 'Доступен только при открытом аукционе.'}
                        </span>
                      </span>
                    </div>
                    <IosToggle
                      checked={Boolean(settings.topup_started_at)}
                      onChange={handleToggleTopup}
                      disabled={isTogglingTopup || (runtimeStatus !== 'open' && !settings.topup_started_at)}
                    />
                  </div>
                  <div className="flex items-center justify-between gap-4 px-3 py-3 sm:px-4">
                    <div className="flex min-w-0 items-center gap-3">
                      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-sky-50 text-sky-600">
                        <Lock size={16} />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-sm font-semibold text-slate-900">Только своя ставка</span>
                        <span className="block text-xs text-slate-500">
                          Оператор может брать смены только своей ставки (1 / 0.75 / 0.5). Применяется сразу.
                        </span>
                      </span>
                    </div>
                    <IosToggle
                      checked={Boolean(settings.rate_lock_enabled)}
                      onChange={handleToggleRateLock}
                      disabled={isTogglingRateLock}
                    />
                  </div>
                </div>

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
                          onClick={() => updateDraftSchedulePlanId(period.id)}
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
                      onStartsAtChange={updateDraftStartsAt}
                      onEndsAtChange={updateDraftEndsAt}
                    />
                  </div>

                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    <AuctionTimeField
                      label="Начало аукциона"
                      dateValue={draftStartsAtParts.date}
                      value={draftStartsAt}
                      onChange={updateDraftStartsAt}
                      disabled={!draftStartsAtParts.date}
                    />
                    <AuctionTimeField
                      label="Завершение аукциона"
                      dateValue={draftEndsAtParts.date}
                      value={draftEndsAt}
                      onChange={updateDraftEndsAt}
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
                        onClick={() => updateDraftEndsAt(addMinutesToDateTimeInputValue(draftStartsAt, preset.minutes))}
                        disabled={!draftStartsAt}
                        className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 sm:p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <UserCog size={16} className="text-blue-700" />
                        Группы времени
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        Часть операторов может заходить раньше или позже общего окна. В группу можно взять
                        любого оператора направления — он сразу становится участником аукциона.
                        {selectedDraftPeriod
                          ? <> Группы действуют только на неделю {formatAuctionPeriodLabel(selectedDraftPeriod)}.</>
                          : <> Сначала выберите неделю аукциона.</>}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={addDraftTimeGroup}
                      disabled={!selectedDraftPeriod || draftTimeGroups.length >= AUCTION_TIME_GROUP_LIMIT}
                      className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 transition hover:bg-slate-100 active:scale-[0.98] disabled:cursor-not-allowed disabled:text-slate-400"
                    >
                      <Plus size={15} />
                      Группа
                    </button>
                  </div>

                  {draftTimeGroups.length ? (
                    <div className="mt-3 space-y-2">
                      {draftTimeGroups.map((group, groupIndex) => {
                        const expanded = expandedTimeGroupKey === group.key;
                        const issue = timeGroupIssues.get(group.key) || '';
                        const groupDate = group.date || draftStartsAtParts.date;
                        const memberCount = group.operatorIds.length;
                        const pickerOperators = timeGroupMemberQuery.trim()
                          ? operatorOptions.filter((operator) => (
                            `${operator.name} ${operator.direction || ''} ${operator.supervisor_name || ''}`
                              .toLowerCase()
                              .includes(timeGroupMemberQuery.trim().toLowerCase())
                          ))
                          : operatorOptions;
                        return (
                          <div
                            key={group.key}
                            className={`overflow-hidden rounded-xl bg-white ring-1 ${issue ? 'ring-rose-200' : 'ring-slate-200/70'}`}
                          >
                            <div className="flex items-center gap-2 px-3 py-2.5">
                              <button
                                type="button"
                                onClick={() => {
                                  setExpandedTimeGroupKey(expanded ? '' : group.key);
                                  setTimeGroupMemberQuery('');
                                }}
                                className="flex min-w-0 flex-1 items-center gap-2 text-left"
                              >
                                <ChevronRight
                                  size={16}
                                  className={`shrink-0 text-slate-400 transition-transform ${expanded ? 'rotate-90' : ''}`}
                                />
                                <span className="min-w-0">
                                  <span className="block truncate text-sm font-semibold text-slate-900">
                                    {getTimeGroupTitle(group, groupIndex)}
                                  </span>
                                  <span className="mt-0.5 block truncate text-xs text-slate-500">
                                    <span className="tabular-nums">{formatTimeGroupWindowLabel(group, draftStartsAt, draftEndsAt)}</span>
                                    {' · '}
                                    <span className="tabular-nums">{memberCount}</span> в группе
                                  </span>
                                </span>
                              </button>
                              <button
                                type="button"
                                onClick={() => removeDraftTimeGroup(group.key)}
                                title="Удалить группу"
                                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                              >
                                <X size={15} />
                              </button>
                            </div>

                            {expanded ? (
                              <div className="space-y-3 border-t border-slate-100 px-3 py-3">
                                <input
                                  value={group.name}
                                  onChange={(event) => patchDraftTimeGroup(group.key, { name: event.target.value })}
                                  maxLength={80}
                                  placeholder="Название группы — например, Наставники"
                                  className="h-9 w-full rounded-lg border border-slate-200 px-3 text-sm text-slate-800 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                                />
                                {auctionWeekDates.length ? (
                                  <div>
                                    <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                                      День группы
                                    </div>
                                    <div className="grid grid-cols-7 gap-1">
                                      {auctionWeekDates.map((weekDate) => {
                                        const activeDay = groupDate === weekDate;
                                        const isMainDay = weekDate === draftStartsAtParts.date;
                                        return (
                                          <button
                                            key={weekDate}
                                            type="button"
                                            onClick={() => patchDraftTimeGroup(group.key, { date: weekDate })}
                                            title={`${formatDateLabel(weekDate)}${isMainDay ? ' · день общего старта' : ''}`}
                                            className={`h-11 rounded-lg border text-[11px] font-semibold leading-tight transition active:scale-[0.98] ${
                                              activeDay
                                                ? 'border-blue-500 bg-blue-50 text-blue-900'
                                                : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
                                            }`}
                                          >
                                            <span className="block">{AUCTION_WEEKDAY_LABELS[getWeekdayIndex(weekDate)]}</span>
                                            <span className="block tabular-nums">{weekDate.slice(8)}</span>
                                          </button>
                                        );
                                      })}
                                    </div>
                                  </div>
                                ) : null}
                                <div className="grid gap-3 md:grid-cols-2">
                                  <AuctionTimeField
                                    label="Старт группы"
                                    dateValue={groupDate}
                                    value={group.startTime ? `${groupDate}T${group.startTime}` : ''}
                                    onChange={(value) => patchDraftTimeGroup(group.key, {
                                      startTime: splitDateTimeInputValue(value).time
                                    })}
                                    disabled={!groupDate}
                                    invalid={Boolean(issue)}
                                  />
                                  <div>
                                    <AuctionTimeField
                                      label="Завершение группы"
                                      dateValue={groupDate}
                                      // Without its own end the field shows the inherited common
                                      // one, so what is on screen is always the effective time.
                                      value={group.endTime ? `${groupDate}T${group.endTime}` : (draftEndsAt || '')}
                                      onChange={(value) => patchDraftTimeGroup(group.key, {
                                        endTime: splitDateTimeInputValue(value).time
                                      })}
                                      disabled={!groupDate}
                                    />
                                    <div className="mt-1.5 text-xs text-slate-500">
                                      {group.endTime ? (
                                        <button
                                          type="button"
                                          onClick={() => patchDraftTimeGroup(group.key, { endTime: '' })}
                                          className="inline-flex items-center gap-1 font-semibold text-slate-600 transition hover:text-slate-900"
                                        >
                                          <X size={12} /> Завершать вместе со всеми
                                        </button>
                                      ) : 'Завершение общее — как у остальных участников.'}
                                    </div>
                                  </div>
                                </div>

                                <div className="flex items-center justify-between gap-4 rounded-xl bg-slate-50 px-3 py-2.5">
                                  <div className="min-w-0">
                                    <div className="text-sm font-semibold text-slate-900">Свой график</div>
                                    <div className="mt-0.5 text-xs text-slate-500">
                                      Участники сами ставят смены на дни в нижней панели. Потолок — норма плюс{' '}
                                      {formatAuctionHours(AUCTION_SELF_SCHEDULE_EXTRA_MINUTES)} ч.
                                    </div>
                                  </div>
                                  <IosToggle
                                    checked={Boolean(group.selfSchedule)}
                                    onChange={(checked) => patchDraftTimeGroup(group.key, { selfSchedule: Boolean(checked) })}
                                  />
                                </div>

                                <div>
                                  <div className="mb-1.5 flex items-center justify-between gap-2">
                                    <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                                      Кто в группе
                                    </span>
                                    <span className="text-xs tabular-nums text-slate-500">{memberCount}</span>
                                  </div>
                                  {operatorOptions.length > 8 ? (
                                    <input
                                      value={timeGroupMemberQuery}
                                      onChange={(event) => setTimeGroupMemberQuery(event.target.value)}
                                      placeholder="Поиск по оператору, направлению или СВ"
                                      className="mb-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                                    />
                                  ) : null}
                                  <div className="max-h-56 overflow-auto rounded-lg border border-slate-200">
                                    {pickerOperators.length ? pickerOperators.map((operator) => {
                                      const memberOf = timeGroupByOperatorId.get(operator.id);
                                      const inThisGroup = memberOf?.key === group.key;
                                      const inOtherGroup = Boolean(memberOf) && !inThisGroup;
                                      return (
                                        <button
                                          key={operator.id}
                                          type="button"
                                          onClick={() => toggleTimeGroupMember(group.key, operator.id)}
                                          className={`flex w-full items-center gap-3 border-b border-slate-100 px-3 py-2 text-left transition last:border-b-0 ${
                                            inThisGroup ? 'bg-blue-50' : 'bg-white hover:bg-slate-50'
                                          }`}
                                        >
                                          <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border ${
                                            inThisGroup ? 'border-blue-700 bg-blue-700 text-white' : 'border-slate-300 bg-white'
                                          }`}
                                          >
                                            {inThisGroup ? <CheckCircle2 size={13} /> : null}
                                          </span>
                                          <span className="min-w-0 flex-1">
                                            <span className="block truncate text-sm text-slate-900">{operator.name}</span>
                                            <span className="block truncate text-[11px] text-slate-500">
                                              {operator.direction || 'Без направления'} · ставка {Number(operator.rate || 1).toFixed(2)}
                                              {operator.supervisor_name ? ` · ${operator.supervisor_name}` : ''}
                                            </span>
                                          </span>
                                          {inOtherGroup ? (
                                            <span className="shrink-0 truncate rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-semibold text-slate-500">
                                              {memberOf.title}
                                            </span>
                                          ) : null}
                                        </button>
                                      );
                                    }) : (
                                      <div className="px-3 py-4 text-center text-xs text-slate-500">
                                        {operatorOptions.length ? 'Никого не нашли.' : 'Операторы не найдены.'}
                                      </div>
                                    )}
                                  </div>
                                </div>
                              </div>
                            ) : null}

                            {issue ? (
                              <div className="border-t border-rose-100 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700">
                                {issue}
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="mt-3 rounded-lg border border-dashed border-slate-300 bg-white px-3 py-4 text-sm text-slate-500">
                      Групп нет — все участники стартуют в общее время.
                    </p>
                  )}
                </div>

                <div>
                  <label className="mb-2 block text-sm font-semibold text-slate-800">Текст для тестовой группы</label>
                  <textarea
                    value={draftNote}
                    onChange={(event) => updateDraftNote(event.target.value)}
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
                      const operatorTimeGroup = timeGroupByOperatorId.get(operator.id);
                      const operatorIsWorking = isActiveShiftAuctionOperator(operator);
                      const operatorStatusLabel = getShiftAuctionOperatorStatusLabel(operator.status);
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
                          {!operatorIsWorking && operatorStatusLabel ? (
                            <span className="shrink-0 rounded-md bg-amber-50 px-1.5 py-0.5 text-[11px] font-semibold text-amber-700 ring-1 ring-inset ring-amber-200">
                              {operatorStatusLabel}
                            </span>
                          ) : null}
                          {operatorTimeGroup ? (
                            <span
                              title={`Группа времени: ${operatorTimeGroup.title}`}
                              className="max-w-[8rem] shrink-0 truncate rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-semibold text-slate-600"
                            >
                              {operatorTimeGroup.title}
                            </span>
                          ) : null}
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
                    selectedOperators.map((operator) => {
                      const operatorTimeGroup = timeGroupByOperatorId.get(operator.id);
                      const operatorIsWorking = isActiveShiftAuctionOperator(operator);
                      const operatorStatusLabel = getShiftAuctionOperatorStatusLabel(operator.status);
                      return (
                        <div key={operator.id} className="flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2">
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-semibold text-slate-900">{operator.name}</div>
                            <div className="mt-0.5 truncate text-xs text-slate-500">
                              {operator.direction || 'Без направления'}
                              {!operatorIsWorking && operatorStatusLabel ? ` · ${operatorStatusLabel}` : ''}
                            </div>
                          </div>
                          {operatorTimeGroup ? (
                            <span
                              title={`Группа времени: ${operatorTimeGroup.title}`}
                              className="max-w-[7rem] shrink-0 truncate rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-semibold text-slate-600"
                            >
                              {operatorTimeGroup.title}
                            </span>
                          ) : null}
                        </div>
                      );
                    })
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

      {selfScheduleDate ? (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-900/50 px-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="self-schedule-title"
          onClick={() => { if (!isSelfScheduling) setSelfScheduleDate(''); }}
        >
          <div
            className="w-full max-w-sm overflow-hidden rounded-2xl bg-white shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 border-b border-slate-200/70 px-4 py-3.5">
              <div className="min-w-0">
                <h3 id="self-schedule-title" className="truncate text-[15px] font-semibold tracking-tight text-slate-900">
                  Своя смена
                </h3>
                <div className="mt-0.5 truncate text-xs text-slate-500">{formatDateLabel(selfScheduleDate)}</div>
              </div>
              <button
                type="button"
                onClick={() => { if (!isSelfScheduling) setSelfScheduleDate(''); }}
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-95"
                title="Закрыть"
              >
                <X size={16} />
              </button>
            </div>
            <div className="space-y-3 px-4 py-4">
              <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
                Длина смены — по вашей ставке{selfScheduleGroup?.shiftMinutes ? ` (${formatAuctionHours(selfScheduleGroup.shiftMinutes)} ч)` : ''}.
                Осталось до потолка: <span className="font-semibold tabular-nums">{formatAuctionHours(selfScheduleRemainingMinutes)} ч</span>.
              </div>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Начало</span>
                  <input
                    type="time"
                    value={selfScheduleStart}
                    disabled={isSelfScheduling}
                    step={300}
                    onChange={(event) => setSelfScheduleStart(normalizeClockValue(event.target.value))}
                    className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm tabular-nums text-slate-900 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100 disabled:bg-slate-50 disabled:text-slate-400"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Конец</span>
                  <input
                    type="text"
                    value={selfScheduleEndTime || '—'}
                    readOnly
                    className="w-full cursor-default rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm tabular-nums text-slate-500"
                  />
                </label>
              </div>
              {selfScheduleIssue ? (
                <div className="rounded-lg bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700">{selfScheduleIssue}</div>
              ) : null}
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-slate-200/70 px-4 py-3">
              <button
                type="button"
                onClick={() => setSelfScheduleDate('')}
                disabled={isSelfScheduling}
                className="inline-flex h-9 items-center rounded-md border border-slate-200 px-3 text-sm font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
              >
                Отмена
              </button>
              <button
                type="button"
                onClick={handleSubmitSelfSchedule}
                disabled={isSelfScheduling || Boolean(selfScheduleIssue)}
                className="inline-flex h-9 items-center gap-1.5 rounded-md bg-blue-600 px-3 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
              >
                <Plus size={15} />
                {isSelfScheduling ? 'Ставлю…' : 'Поставить'}
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
                    {formatAuctionHours(drilldownData.workload.claimed_net_minutes || 0)} / {formatAuctionHours(drilldownData.workload.ceiling_minutes || drilldownData.workload.norm_minutes || 0)} ч
                  </span>
                  <span className="text-slate-500">
                    {drilldownData.workload.self_schedule ? 'свой график · ' : ''}
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
                      (drilldownData.workload.ceiling_minutes || drilldownData.workload.norm_minutes) > 0
                        ? (drilldownData.workload.claimed_net_minutes
                          / (drilldownData.workload.ceiling_minutes || drilldownData.workload.norm_minutes)) * 100
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
              {hasMultipleReleaseOptions
                ? 'Какую смену убрать?'
                : releaseConfirmLot.self_scheduled
                  ? 'Убрать свою смену?'
                  : releaseConfirmLot.partial_claim
                    ? 'Хотите ли вы вернуть свою часть смены?'
                    : 'Хотите ли вы вернуть эту смену?'}
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
              {releaseConfirmLot.self_scheduled
                ? 'Смена вашего графика просто исчезнет — другим операторам она не достанется. Поставить её заново можно тем же нажатием на день.'
                : hasMultipleReleaseOptions
                  ? 'Выбранная смена снова станет доступной для других операторов. Остальные смены в этот день останутся у вас.'
                  : releaseConfirmLot.partial_claim
                    ? 'Свободным станет только ваш кусок — части, взятые коллегами в этой смене, останутся у них. Это действие нельзя отменить.'
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
                {releasingLotId !== null
                  ? (releaseConfirmLot.self_scheduled ? 'Убираю...' : 'Возвращаю...')
                  : releaseConfirmLot.self_scheduled
                    ? 'Убрать смену'
                    : releaseConfirmLot.partial_claim ? 'Вернуть часть' : 'Вернуть смену'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {partialClaimLot ? (
        <PostAuctionPartialClaimModal
          lot={partialClaimLot}
          option={auctionPartialClaimOptionsByLotId.get(getAuctionLotActionKey(partialClaimLot))}
          selection={partialClaimSelection}
          onSelectionChange={setPartialClaimSelection}
          onClose={handleClosePartialClaim}
          onConfirm={handleConfirmPartialClaim}
          inProgress={claimingLotIds.has(getAuctionLotActionKey(partialClaimLot))}
          title="Взять смену или её часть"
          confirmLabel="Взять"
          inProgressLabel="Беру..."
          footnote={(
            <p className="mt-3 text-xs leading-5 text-slate-600">
              Можно взять смену целиком или только удобную часть — остальное останется
              свободным для других. Всего за неделю по вашей ставке
              {' '}
              <b className="text-slate-900">
                {weeklyRateHoursLabel ? `${weeklyRateHoursLabel} ч` : 'по норме'}
              </b>
              {myAuctionWorkload.normMinutes > 0 ? (
                <>
                  {' '}· уже набрано <b className="text-slate-900">{formatAuctionHours(myAuctionWorkload.claimedNetMinutes)} ч</b>
                  , осталось <b className="text-slate-900">{formatAuctionHours(myAuctionWorkload.remainingMinutes)} ч</b>
                </>
              ) : null}
              {isTopupActive
                ? '. Идёт добор — часы можно брать сверх нормы.'
                : '. Больше нормы взять нельзя, пока руководитель не включит добор.'}
            </p>
          )}
        />
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

          {/*
            У каждой ветки свой key: иначе React переиспользует один и тот же
            <div> и удаляет его текстовые узлы по отдельности. Если текстовый
            узел уже перенесён чем-то извне (встроенный переводчик Edge/Chrome
            оборачивает текст в <font>, так же ведут себя Grammarly, «Читать
            вслух» и подсветчики), removeChild падает с «The node to be removed
            is not a child of this node» — и ErrorBoundary гасит всё приложение.
            С key React удаляет контейнер целиком, а это безопасно.
            Голый текст по той же причине завёрнут в <span>.
          */}
          {myClaimsLoading && !myClaims.length ? (
            <div key="claims-loading" className="flex items-center justify-center gap-2 py-12 text-[13px] text-slate-400">
              <RefreshCw size={15} className="animate-spin" />
              <span>Загрузка…</span>
            </div>
          ) : myClaimsError ? (
            <div key="claims-error" className="rounded-2xl bg-rose-50 px-3.5 py-3 text-[13px] text-rose-600 ring-1 ring-rose-100">
              <span>{myClaimsError}</span>
            </div>
          ) : !myClaims.length ? (
            <div key="claims-empty" className="flex flex-col items-center gap-2 py-12 text-center">
              <div className="grid h-14 w-14 place-items-center rounded-full bg-slate-100 text-slate-400">
                <CalendarDays size={24} />
              </div>
              <div className="text-[14px] font-semibold text-slate-600">
                <span>Нет недавно взятых смен</span>
              </div>
              <div className="max-w-[260px] text-[12px] leading-5 text-slate-400">
                <span>Здесь появятся дополнительные смены, которые вы возьмёте, — с возможностью отменить их в первые 10 минут.</span>
              </div>
            </div>
          ) : (
            <div key="claims-list" className="space-y-2">
              {myClaimRows.map((row) => (
                <MyPostClaimRow
                  key={row.reactKey}
                  claim={row.claim}
                  fetchedAtMs={myClaimsFetchedAt}
                  busy={Boolean(row.actionKey) && cancelingClaimKey === row.actionKey}
                  onCancel={handleCancelMyClaim}
                />
              ))}
            </div>
          )}
        </div>
      </IosModal>
    </div>
  );
};

export default ShiftAuctionView;
