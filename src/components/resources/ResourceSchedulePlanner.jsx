import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
  GripVertical,
  Plus,
  Redo2,
  RefreshCw,
  RotateCcw,
  SlidersHorizontal,
  Trash2,
  Undo2,
  Wand2,
} from 'lucide-react';

const TEMPLATE_STORAGE_KEY = 'otp_resource_schedule_templates_v1';
const SNAP_MINUTES = 30;
const MIN_SHIFT_MINUTES = 60;
const MAX_SHIFT_END_MINUTES = 32 * 60;

const intFormatter = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 0,
});

const formatNumber = (value, digits = 1) =>
  new Intl.NumberFormat('ru-RU', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(Number(value || 0));

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

const snapMinutes = (value) => Math.round(Number(value || 0) / SNAP_MINUTES) * SNAP_MINUTES;

const roundMathFte = (value) => Math.round(Math.max(0, Number(value || 0)));

const formatTime = (minutes) => {
  const normalized = ((Math.round(Number(minutes || 0)) % 1440) + 1440) % 1440;
  return `${String(Math.floor(normalized / 60)).padStart(2, '0')}:${String(normalized % 60).padStart(2, '0')}`;
};

const parseTemplateTime = (value) => {
  const raw = String(value || '').trim();
  if (!raw) return null;
  const [hourRaw, minuteRaw = '0'] = raw.includes('/') ? raw.split('/') : raw.split(':');
  const hour = Number(hourRaw);
  const minute = Number(minuteRaw || 0);
  if (!Number.isFinite(hour) || !Number.isFinite(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    return null;
  }
  return hour * 60 + minute;
};

const parseTemplateLabel = (label) => {
  const raw = String(label || '').trim();
  if (!raw.includes('*')) return null;
  const [startRaw, endRaw] = raw.split('*');
  const startMinute = parseTemplateTime(startRaw);
  const endClockMinute = parseTemplateTime(endRaw);
  if (startMinute === null || endClockMinute === null) return null;
  let endMinute = endClockMinute;
  if (endMinute <= startMinute) endMinute += 1440;
  if (endMinute - startMinute < MIN_SHIFT_MINUTES) return null;
  return {
    label: raw,
    start: formatTime(startMinute),
    end: formatTime(endMinute),
    startMinute,
    endMinute,
    durationMinutes: endMinute - startMinute,
    overnight: endMinute > 1440,
  };
};

const defaultBreakDurationsForShift = (durationMinutes) => {
  const duration = Number(durationMinutes || 0);
  if (duration >= 5 * 60 && duration < 6 * 60) return [15];
  if (duration >= 6 * 60 && duration < 8 * 60) return [15, 15];
  if (duration >= 8 * 60 && duration < 11 * 60) return [15, 30, 15];
  if (duration >= 11 * 60) return [15, 30, 15, 15];
  return [];
};

const computeDefaultBreaks = (startMinute, endMinute) => {
  const start = Number(startMinute || 0);
  const end = Number(endMinute || 0);
  const duration = end - start;
  if (duration <= 0) return [];

  const snap5 = (value) => Math.round(Number(value || 0) / 5) * 5;
  const durations = defaultBreakDurationsForShift(duration);
  const count = durations.length;
  return durations
    .map((size, index) => {
      const center = start + duration * ((index + 1) / (count + 1));
      const snappedCenter = snap5(center);
      const breakStart = clamp(snap5(snappedCenter - size / 2), start, end);
      const breakEnd = clamp(breakStart + size, start, end);
      return breakEnd > breakStart ? { start: breakStart, end: breakEnd } : null;
    })
    .filter(Boolean);
};

const normalizeTemplateForLocalUse = (template) => {
  const parsed = parseTemplateLabel(template?.label);
  if (!parsed) return null;
  return {
    ...template,
    ...parsed,
    id: String(template?.id || `local-${template?.rate || 1}-${parsed.startMinute}-${parsed.endMinute}-${parsed.label}`),
    rate: Number(template?.rate || 1),
    enabled: template?.enabled !== false,
  };
};

const loadStoredTemplates = () => {
  if (typeof window === 'undefined') return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(TEMPLATE_STORAGE_KEY) || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const storeTemplates = (templates) => {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(TEMPLATE_STORAGE_KEY, JSON.stringify(templates || []));
};

const clonePlannerDays = (days) =>
  (days || []).map((day) => ({
    ...day,
    coverage: (day.coverage || []).map((row) => ({ ...row })),
    shifts: (day.shifts || []).map((shift) => ({
      ...shift,
      breaks: (shift.breaks || []).map((breakItem) => ({ ...breakItem })),
    })),
    stats: day.stats ? { ...day.stats } : day.stats,
  }));

const plannerDaysSignature = (days) => JSON.stringify(
  (days || []).map((day) => ({
    date: day.date,
    shifts: (day.shifts || []).map((shift) => ({
      id: shift.id,
      templateId: shift.templateId,
      rate: shift.rate,
      label: shift.label,
      startMinute: shift.startMinute,
      endMinute: shift.endMinute,
      breaks: shift.breaks || [],
    })),
  })),
);

const coverageTone = (row) => {
  const deficit = Number(row?.deficit || 0);
  const over = Number(row?.over || 0);
  if (deficit > 0.05) return 'border-rose-200 bg-rose-50 text-rose-700';
  if (over > 0.25) return 'border-amber-200 bg-amber-50 text-amber-700';
  if (Number(row?.needed || 0) > 0) return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  return 'border-slate-200 bg-slate-50 text-slate-500';
};

const buildCoverageFromDays = (days) => {
  const dayCount = days.length;
  const target = Array.from({ length: dayCount * 24 }, (_, index) => {
    const dayIndex = Math.floor(index / 24);
    const hour = index % 24;
    const row = days[dayIndex]?.coverage?.[hour] || {};
    const rawNeeded = row.rawNeeded ?? row.needed ?? 0;
    return roundMathFte(rawNeeded);
  });
  const rawTarget = Array.from({ length: dayCount * 24 }, (_, index) => {
    const dayIndex = Math.floor(index / 24);
    const hour = index % 24;
    return Number(days[dayIndex]?.coverage?.[hour]?.rawNeeded || 0);
  });
  const covered = Array.from({ length: dayCount * 24 }, () => 0);

  days.forEach((day, dayIndex) => {
    (day.shifts || []).forEach((shift) => {
      const startAbs = dayIndex * 1440 + Number(shift.startMinute || 0);
      const endAbs = dayIndex * 1440 + Number(shift.endMinute || 0);
      const breaks = Array.isArray(shift.breaks) ? shift.breaks : [];
      for (let hourIndex = 0; hourIndex < covered.length; hourIndex += 1) {
        const hourStart = hourIndex * 60;
        const hourEnd = hourStart + 60;
        const overlap = Math.max(0, Math.min(endAbs, hourEnd) - Math.max(startAbs, hourStart));
        if (overlap <= 0) continue;
        let breakOverlap = 0;
        breaks.forEach((breakItem) => {
          const breakStart = dayIndex * 1440 + Number(breakItem.start || 0);
          const breakEnd = dayIndex * 1440 + Number(breakItem.end || 0);
          breakOverlap += Math.max(0, Math.min(breakEnd, hourEnd) - Math.max(breakStart, hourStart));
        });
        covered[hourIndex] += Math.max(0, overlap - breakOverlap) / 60;
      }
    });
  });

  const nextDays = days.map((day, dayIndex) => {
    const coverage = Array.from({ length: 24 }, (_, hour) => {
      const index = dayIndex * 24 + hour;
      const needed = target[index];
      const currentCovered = Number(covered[index] || 0);
      return {
        hour,
        needed,
        rawNeeded: rawTarget[index],
        covered: currentCovered,
        coveredRounded: roundMathFte(currentCovered),
        deficit: Math.max(0, needed - currentCovered),
        over: Math.max(0, currentCovered - needed),
      };
    });
    const stats = coverage.reduce(
      (acc, row) => {
        acc.neededFteHours += row.needed;
        acc.coveredFteHours += Math.min(row.covered, row.needed);
        acc.deficitFteHours += row.deficit;
        acc.overFteHours += row.over;
        return acc;
      },
      { neededFteHours: 0, coveredFteHours: 0, deficitFteHours: 0, overFteHours: 0 },
    );
    stats.coveragePercent = stats.neededFteHours > 0 ? (stats.coveredFteHours / stats.neededFteHours) * 100 : 0;
    return { ...day, coverage, stats };
  });

  const summary = nextDays.reduce(
    (acc, day) => {
      acc.neededFteHours += Number(day.stats?.neededFteHours || 0);
      acc.coveredFteHours += Number(day.stats?.coveredFteHours || 0);
      acc.deficitFteHours += Number(day.stats?.deficitFteHours || 0);
      acc.overFteHours += Number(day.stats?.overFteHours || 0);
      return acc;
    },
    { neededFteHours: 0, coveredFteHours: 0, deficitFteHours: 0, overFteHours: 0 },
  );
  summary.coveragePercent = summary.neededFteHours > 0 ? (summary.coveredFteHours / summary.neededFteHours) * 100 : 0;
  return { days: nextDays, summary };
};

const ShiftTemplateEditor = ({
  templates,
  selectedTemplateId,
  onTemplatesChange,
  onSelectedTemplateChange,
  onReset,
}) => {
  const updateTemplate = (id, patch) => {
    onTemplatesChange(
      templates.map((template) => (template.id === id ? { ...template, ...patch } : template)),
    );
  };

  const removeTemplate = (id) => {
    const next = templates.filter((template) => template.id !== id);
    onTemplatesChange(next);
    if (selectedTemplateId === id && next[0]) onSelectedTemplateChange(next[0].id);
  };

  const addTemplate = () => {
    const parsed = parseTemplateLabel('9*18');
    const item = {
      id: `local-${Date.now()}`,
      rate: 1,
      label: '9*18',
      enabled: true,
      ...parsed,
    };
    onTemplatesChange([...templates, item]);
    onSelectedTemplateChange(item.id);
  };

  const enabledTemplates = templates.filter((template) => template.enabled !== false);

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
          <SlidersHorizontal size={16} />
          Справочник шаблонов
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={addTemplate}
            className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
          >
            <Plus size={15} />
            Добавить
          </button>
          <button
            type="button"
            onClick={onReset}
            className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
          >
            <RotateCcw size={15} />
            Сброс
          </button>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_220px]">
        <div className="max-h-[340px] overflow-auto rounded-lg border border-slate-200">
          <table className="min-w-[620px] w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2 text-left">Вкл</th>
                <th className="px-3 py-2 text-left">Ставка</th>
                <th className="px-3 py-2 text-left">Шаблон</th>
                <th className="px-3 py-2 text-left">Время</th>
                <th className="px-3 py-2 text-right">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {templates.map((template) => {
                const parsed = parseTemplateLabel(template.label);
                return (
                  <tr key={template.id}>
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        checked={template.enabled !== false}
                        onChange={(event) => updateTemplate(template.id, { enabled: event.target.checked })}
                        className="h-4 w-4 rounded border-slate-300 text-blue-600"
                      />
                    </td>
                    <td className="px-3 py-2">
                      <select
                        value={String(template.rate || 1)}
                        onChange={(event) => updateTemplate(template.id, { rate: Number(event.target.value) })}
                        className="h-9 rounded-lg border border-slate-200 bg-white px-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
                      >
                        <option value="1">1</option>
                        <option value="0.75">0.75</option>
                        <option value="0.5">0.5</option>
                      </select>
                    </td>
                    <td className="px-3 py-2">
                      <input
                        value={template.label || ''}
                        onChange={(event) => {
                          const nextLabel = event.target.value;
                          const nextParsed = parseTemplateLabel(nextLabel);
                          updateTemplate(template.id, {
                            label: nextLabel,
                            ...(nextParsed || {}),
                          });
                        }}
                        className={`h-9 w-full rounded-lg border px-2 text-sm outline-none focus:ring-2 ${
                          parsed ? 'border-slate-200 focus:border-blue-400 focus:ring-blue-100' : 'border-rose-200 bg-rose-50 focus:border-rose-400 focus:ring-rose-100'
                        }`}
                      />
                    </td>
                    <td className="px-3 py-2 text-slate-600">
                      {parsed ? `${parsed.start}-${parsed.end}` : 'Ошибка'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button
                        type="button"
                        onClick={() => removeTemplate(template.id)}
                        className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 transition hover:bg-rose-50 hover:text-rose-700"
                      >
                        <Trash2 size={15} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Быстрое добавление</div>
          <select
            value={selectedTemplateId || enabledTemplates[0]?.id || ''}
            onChange={(event) => onSelectedTemplateChange(event.target.value)}
            className="mt-2 h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
          >
            {enabledTemplates.map((template) => (
              <option key={template.id} value={template.id}>
                {template.rate} · {template.label}
              </option>
            ))}
          </select>
          <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
            {[1, 0.75, 0.5].map((rate) => (
              <div key={rate} className="rounded-lg bg-white px-2 py-2">
                <div className="font-semibold text-slate-900">{rate}</div>
                <div className="text-slate-500">
                  {templates.filter((template) => Number(template.rate) === Number(rate) && template.enabled !== false).length}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

const PlannerDayRow = ({
  day,
  previousDay,
  dayIndex,
  templates,
  selectedTemplateId,
  activeDragId,
  splitPreview,
  timelineRef,
  onShiftPointerDown,
  onDeleteShift,
  onAddShift,
}) => {
  const shifts = day.shifts || [];
  const carryoverShifts = (previousDay?.shifts || [])
    .filter((shift) => Number(shift.endMinute || 0) > 1440)
    .map((shift) => ({
      ...shift,
      carryoverStartMinute: 0,
      carryoverEndMinute: Math.min(1440, Number(shift.endMinute || 0) - 1440),
    }))
    .filter((shift) => Number(shift.carryoverEndMinute || 0) > 0);
  const rowHeight = Math.max(90, (shifts.length + carryoverShifts.length) * 34 + 42);

  return (
    <section
      data-planner-day-index={dayIndex}
      className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-950">{day.short || day.label}</div>
          <div className="text-xs text-slate-500">{day.date}</div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
            <b className="text-slate-900">{formatNumber(day.stats?.coveredFteHours, 1)}</b> / {intFormatter.format(roundMathFte(day.stats?.neededFteHours || 0))} FTE-ч
          </div>
          <button
            type="button"
            onClick={() => onAddShift(dayIndex, selectedTemplateId)}
            disabled={!templates.length}
            className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Plus size={15} />
            Линия
          </button>
        </div>
      </div>

      <div className="mt-4 overflow-x-auto">
        <div className="min-w-[980px]">
          <div
            ref={timelineRef}
            className="relative rounded-lg border border-slate-200 bg-slate-50"
            style={{ height: rowHeight }}
          >
            <div className="absolute inset-0 grid" style={{ gridTemplateColumns: 'repeat(24, minmax(0, 1fr))' }}>
              {Array.from({ length: 24 }, (_, hour) => (
                <div key={hour} className="border-r border-slate-200/80 last:border-r-0">
                  <div className="px-1 pt-1 text-[10px] font-medium text-slate-400">{String(hour).padStart(2, '0')}</div>
                </div>
              ))}
            </div>
            {shifts.map((shift, index) => {
              const start = Number(shift.startMinute || 0);
              const end = Number(shift.endMinute || start + 60);
              const left = clamp((start / 1440) * 100, 0, 100);
              const width = clamp(((Math.min(end, 1440) - start) / 1440) * 100, 1.8, 100 - left);
              const isActive = activeDragId === shift.id;
              return (
                <div
                  key={shift.id}
                  className={`absolute flex h-7 cursor-grab items-center rounded-md border px-1.5 text-xs font-semibold shadow-sm transition ${
                    isActive
                      ? 'z-20 border-slate-900 bg-slate-900 text-white'
                      : 'z-10 border-blue-300 bg-blue-100 text-blue-800 hover:bg-blue-200'
                  }`}
                  style={{
                    top: 32 + index * 34,
                    left: `${left}%`,
                    width: `${width}%`,
                  }}
                  onPointerDown={(event) => onShiftPointerDown(event, dayIndex, shift.id, 'move')}
                  onContextMenu={(event) => event.preventDefault()}
                  title={`${formatTime(start)}-${formatTime(end)} · ${shift.rate} · ${shift.label}`}
                >
                  <button
                    type="button"
                    onPointerDown={(event) => onShiftPointerDown(event, dayIndex, shift.id, 'resize-left')}
                    className="mr-1 h-5 w-2 cursor-ew-resize rounded bg-white/70"
                    aria-label="resize-left"
                  />
                  <GripVertical size={13} className="mr-1 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">
                    {formatTime(start)}-{formatTime(end)} · {shift.rate}
                  </span>
                  {end > 1440 ? <span className="ml-1 shrink-0 rounded bg-white/70 px-1 text-[10px] text-blue-800">+1</span> : null}
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDeleteShift(dayIndex, shift.id);
                    }}
                    onPointerDown={(event) => event.stopPropagation()}
                    className="ml-1 flex h-5 w-5 shrink-0 items-center justify-center rounded text-current hover:bg-white/60"
                    aria-label="delete-shift"
                  >
                    <Trash2 size={12} />
                  </button>
                  <button
                    type="button"
                    onPointerDown={(event) => onShiftPointerDown(event, dayIndex, shift.id, 'resize-right')}
                    className="ml-1 h-5 w-2 cursor-ew-resize rounded bg-white/70"
                    aria-label="resize-right"
                  />
                </div>
              );
            })}
            {carryoverShifts.map((shift, index) => {
              const start = Number(shift.carryoverStartMinute || 0);
              const end = Number(shift.carryoverEndMinute || 0);
              const width = clamp(((end - start) / 1440) * 100, 1.8, 100);
              return (
                <div
                  key={`carryover-${previousDay?.date || 'prev'}-${shift.id}`}
                  className="pointer-events-none absolute z-10 flex h-7 items-center rounded-md border border-amber-300 bg-amber-100 px-2 text-xs font-semibold text-amber-800 shadow-sm"
                  style={{
                    top: 32 + (shifts.length + index) * 34,
                    left: '0%',
                    width: `${width}%`,
                  }}
                  title={`Переход с прошлого дня: ${formatTime(shift.startMinute)}-${formatTime(shift.endMinute)} · ${shift.rate} · ${shift.label}`}
                >
                  <span className="min-w-0 flex-1 truncate">
                    {formatTime(start)}-{formatTime(end)} · с прошлого дня · {shift.rate}
                  </span>
                </div>
              );
            })}
            {splitPreview && Number(splitPreview.dayIndex) === Number(dayIndex) ? (
              <div
                className="pointer-events-none absolute bottom-0 top-0 z-30 border-l-2 border-slate-950"
                style={{ left: `${clamp((Number(splitPreview.minute || 0) / 1440) * 100, 0, 100)}%` }}
              />
            ) : null}
          </div>

          <div className="mt-3 grid gap-1" style={{ gridTemplateColumns: 'repeat(24, minmax(40px, 1fr))' }}>
            {(day.coverage || []).map((row) => (
              <div key={row.hour} className={`rounded-md border px-1 py-1 text-center text-[10px] ${coverageTone(row)}`}>
                <div className="font-semibold">{intFormatter.format(row.coveredRounded ?? roundMathFte(row.covered || 0))}</div>
                <div>{intFormatter.format(row.needed || 0)}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

const PlannerDayCards = ({ days, selectedDayIndex, onSelect }) => (
  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-7">
    {days.map((day, dayIndex) => {
      const active = Number(selectedDayIndex) === Number(dayIndex);
      const deficit = Number(day.stats?.deficitFteHours || 0);
      const coveragePercent = Number(day.stats?.coveragePercent || 0);
      return (
        <button
          key={day.date || dayIndex}
          type="button"
          onClick={() => onSelect(dayIndex)}
          className={`rounded-xl border p-3 text-left shadow-sm transition ${
            active
              ? 'border-slate-900 bg-slate-900 text-white'
              : 'border-slate-200 bg-white text-slate-900 hover:border-slate-300 hover:bg-slate-50'
          }`}
        >
          <div className="flex items-start justify-between gap-2">
            <div>
              <div className={`text-sm font-semibold ${active ? 'text-white' : 'text-slate-950'}`}>{day.short || day.label}</div>
              <div className={`text-xs ${active ? 'text-slate-300' : 'text-slate-500'}`}>{day.date}</div>
            </div>
            <div className={`rounded-md px-2 py-1 text-xs font-semibold ${active ? 'bg-white/15 text-white' : 'bg-slate-100 text-slate-700'}`}>
              {(day.shifts || []).length}
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div>
              <div className={active ? 'text-slate-300' : 'text-slate-500'}>Нужно</div>
              <b>{intFormatter.format(roundMathFte(day.stats?.neededFteHours || 0))}</b>
            </div>
            <div>
              <div className={active ? 'text-slate-300' : 'text-slate-500'}>Дефицит</div>
              <b className={deficit > 0.05 && !active ? 'text-rose-700' : ''}>{formatNumber(deficit, 1)}</b>
            </div>
          </div>
          <div className={`mt-3 h-2 overflow-hidden rounded-full ${active ? 'bg-white/20' : 'bg-slate-200'}`}>
            <div
              className={`h-full rounded-full ${deficit > 0.05 ? 'bg-rose-500' : 'bg-emerald-500'}`}
              style={{ width: `${clamp(coveragePercent, 0, 100)}%` }}
            />
          </div>
        </button>
      );
    })}
  </div>
);

const ResourceSchedulePlanner = ({ apiRoot, buildHeaders, selectedWeekStart, onWeekStartChange, notify }) => {
  const [templates, setTemplates] = useState(() => loadStoredTemplates());
  const [selectedTemplateId, setSelectedTemplateId] = useState('');
  const [plannerDays, setPlannerDays] = useState([]);
  const [serverSummary, setServerSummary] = useState(null);
  const [isTemplatesLoading, setIsTemplatesLoading] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [activeDragId, setActiveDragId] = useState('');
  const [splitPreview, setSplitPreview] = useState(null);
  const [selectedDayIndex, setSelectedDayIndex] = useState(0);
  const [historyPast, setHistoryPast] = useState([]);
  const [historyFuture, setHistoryFuture] = useState([]);
  const timelineRefs = useRef(new Map());
  const plannerDaysRef = useRef(plannerDays);
  const dragStartSnapshotRef = useRef(null);

  const emit = useCallback(
    (message, type = 'success') => {
      if (typeof notify === 'function') notify(message, type);
    },
    [notify],
  );

  const setTimelineRef = useCallback((dayIndex, node) => {
    if (node) timelineRefs.current.set(dayIndex, node);
    else timelineRefs.current.delete(dayIndex);
  }, []);

  useEffect(() => {
    plannerDaysRef.current = plannerDays;
  }, [plannerDays]);

  const pushHistorySnapshot = useCallback((snapshot) => {
    const source = clonePlannerDays(snapshot || plannerDaysRef.current);
    setHistoryPast((current) => {
      const last = current[current.length - 1];
      if (last && plannerDaysSignature(last) === plannerDaysSignature(source)) return current;
      return [...current, source].slice(-60);
    });
    setHistoryFuture([]);
  }, []);

  const undoPlannerChange = useCallback(() => {
    setHistoryPast((current) => {
      if (!current.length) return current;
      const previous = current[current.length - 1];
      setHistoryFuture((future) => [clonePlannerDays(plannerDaysRef.current), ...future].slice(0, 60));
      setPlannerDays(clonePlannerDays(previous));
      return current.slice(0, -1);
    });
  }, []);

  const redoPlannerChange = useCallback(() => {
    setHistoryFuture((current) => {
      if (!current.length) return current;
      const next = current[0];
      setHistoryPast((past) => [...past, clonePlannerDays(plannerDaysRef.current)].slice(-60));
      setPlannerDays(clonePlannerDays(next));
      return current.slice(1);
    });
  }, []);

  const applyTemplates = useCallback((nextTemplates) => {
    const normalized = (nextTemplates || []).map((item, index) => {
      const parsed = normalizeTemplateForLocalUse(item);
      if (parsed) return parsed;
      return {
        ...item,
        id: String(item?.id || `draft-${Date.now()}-${index}`),
        rate: Number(item?.rate || 1),
        enabled: item?.enabled !== false,
      };
    });
    setTemplates(normalized);
    storeTemplates(normalized);
    setSelectedTemplateId((current) => (normalized.some((item) => item.id === current) ? current : normalized[0]?.id || ''));
  }, []);

  const loadDefaultTemplates = useCallback(async () => {
    if (!apiRoot) return;
    setIsTemplatesLoading(true);
    setErrorText('');
    try {
      const response = await axios.get(`${apiRoot}/api/resource_fte/shift_templates`, {
        headers: buildHeaders(),
      });
      applyTemplates(response.data?.templates || []);
    } catch (error) {
      setErrorText(error?.response?.data?.error || 'Не удалось загрузить шаблоны смен');
    } finally {
      setIsTemplatesLoading(false);
    }
  }, [apiRoot, applyTemplates, buildHeaders]);

  useEffect(() => {
    if (!templates.length) {
      loadDefaultTemplates();
    } else if (!selectedTemplateId) {
      setSelectedTemplateId(templates[0]?.id || '');
    }
  }, [loadDefaultTemplates, selectedTemplateId, templates]);

  const generatePreview = useCallback(async () => {
    if (!apiRoot) return;
    setIsGenerating(true);
    setErrorText('');
    try {
      const response = await axios.post(
        `${apiRoot}/api/resource_fte/schedule_preview`,
        {
          week_start: selectedWeekStart,
          templates,
        },
        {
          headers: buildHeaders({ 'Content-Type': 'application/json' }),
        },
      );
      const preview = response.data?.preview || {};
      const nextDays = (preview.days || []).map((day) => ({
        ...day,
        shifts: (day.shifts || []).map((shift) => ({ ...shift, breaks: shift.breaks || [] })),
      }));
      setPlannerDays(nextDays);
      setSelectedDayIndex(0);
      setHistoryPast([]);
      setHistoryFuture([]);
      setServerSummary(preview.summary || null);
      emit('График рассчитан', 'success');
    } catch (error) {
      const message = error?.response?.data?.error || 'Не удалось сгенерировать график';
      setErrorText(message);
      emit(message, 'error');
    } finally {
      setIsGenerating(false);
    }
  }, [apiRoot, buildHeaders, emit, selectedWeekStart, templates]);

  const computed = useMemo(() => buildCoverageFromDays(plannerDays), [plannerDays]);
  const computedDays = computed.days;
  const summary = computed.summary;

  useEffect(() => {
    if (!computedDays.length) {
      setSelectedDayIndex(0);
      return;
    }
    setSelectedDayIndex((current) => clamp(Number(current || 0), 0, computedDays.length - 1));
  }, [computedDays.length]);

  const selectedTemplate = useMemo(
    () => templates.find((template) => template.id === selectedTemplateId) || templates.find((template) => template.enabled !== false) || null,
    [selectedTemplateId, templates],
  );

  useEffect(() => {
    const onKeyDown = (event) => {
      const target = event.target;
      const targetTag = String(target?.tagName || '').toLowerCase();
      if (target?.isContentEditable || ['input', 'textarea', 'select'].includes(targetTag)) return;
      const key = String(event.key || '').toLowerCase();
      const isUndo = (event.ctrlKey || event.metaKey) && key === 'z' && !event.shiftKey;
      const isRedo = (event.ctrlKey || event.metaKey) && (key === 'y' || (key === 'z' && event.shiftKey));
      if (!isUndo && !isRedo) return;
      event.preventDefault();
      if (isUndo) undoPlannerChange();
      else redoPlannerChange();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [redoPlannerChange, undoPlannerChange]);

  const updateShift = useCallback((dayIndex, shiftId, updater) => {
    setPlannerDays((current) =>
      current.map((day, index) => {
        if (index !== dayIndex) return day;
        return {
          ...day,
          shifts: (day.shifts || []).map((shift) => {
            if (shift.id !== shiftId) return shift;
            const next = updater(shift);
            const startMinute = clamp(snapMinutes(next.startMinute), 0, 1439);
            const endMinute = clamp(snapMinutes(next.endMinute), startMinute + MIN_SHIFT_MINUTES, MAX_SHIFT_END_MINUTES);
            return {
              ...shift,
              ...next,
              startMinute,
              endMinute,
              start: formatTime(startMinute),
              end: formatTime(endMinute),
              durationMinutes: endMinute - startMinute,
              overnight: endMinute > 1440,
              breaks: computeDefaultBreaks(startMinute, endMinute),
            };
          }),
        };
      }),
    );
  }, []);

  const moveShiftToDay = useCallback((fromDayIndex, toDayIndex, shiftId) => {
    if (fromDayIndex === toDayIndex || toDayIndex < 0 || toDayIndex >= plannerDays.length) return;
    setPlannerDays((current) => {
      const movingShift = current[fromDayIndex]?.shifts?.find((shift) => shift.id === shiftId);
      if (!movingShift) return current;
      return current.map((day, index) => {
        if (index === fromDayIndex) {
          return { ...day, shifts: (day.shifts || []).filter((shift) => shift.id !== shiftId) };
        }
        if (index === toDayIndex) {
          return { ...day, shifts: [...(day.shifts || []), movingShift] };
        }
        return day;
      });
    });
  }, [plannerDays.length]);

  const handleShiftPointerDown = useCallback(
    (event, dayIndex, shiftId, action) => {
      event.preventDefault();
      event.stopPropagation();
      const timelineNode = timelineRefs.current.get(dayIndex);
      const sourceShift = plannerDays[dayIndex]?.shifts?.find((shift) => shift.id === shiftId);
      if (!timelineNode || !sourceShift) return;
      const rect = timelineNode.getBoundingClientRect();
      const width = Math.max(1, rect.width);
      const originalStart = Number(sourceShift.startMinute || 0);
      const originalEnd = Number(sourceShift.endMinute || originalStart + 60);
      const originalDuration = originalEnd - originalStart;

      if (event.button === 2 && action === 'move') {
        if (originalDuration < MIN_SHIFT_MINUTES * 2) return;
        const splitAtClientX = (clientX) => {
          const rawMinute = ((clientX - rect.left) / width) * 1440;
          return clamp(
            snapMinutes(rawMinute),
            originalStart + MIN_SHIFT_MINUTES,
            originalEnd - MIN_SHIFT_MINUTES,
          );
        };
        const updatePreview = (clientX, clientY) => {
          setSplitPreview({
            dayIndex,
            shiftId,
            minute: splitAtClientX(clientX),
            x: clientX,
            y: clientY,
          });
        };
        updatePreview(event.clientX, event.clientY);

        const onSplitMove = (moveEvent) => {
          updatePreview(moveEvent.clientX, moveEvent.clientY);
        };
        const onSplitUp = (upEvent) => {
          window.removeEventListener('pointermove', onSplitMove);
          window.removeEventListener('pointerup', onSplitUp);
          const splitMinute = splitAtClientX(upEvent.clientX);
          setSplitPreview(null);
          pushHistorySnapshot();
          setPlannerDays((current) =>
            current.map((day, index) => {
              if (index !== dayIndex) return day;
              const nextShifts = [];
              (day.shifts || []).forEach((shift) => {
                if (shift.id !== shiftId) {
                  nextShifts.push(shift);
                  return;
                }
                const leftStart = Number(shift.startMinute || 0);
                const rightEnd = Number(shift.endMinute || leftStart + MIN_SHIFT_MINUTES);
                nextShifts.push({
                  ...shift,
                  id: `${shift.id}-a-${Date.now()}`,
                  endMinute: splitMinute,
                  end: formatTime(splitMinute),
                  durationMinutes: splitMinute - leftStart,
                  overnight: splitMinute > 1440,
                  breaks: computeDefaultBreaks(leftStart, splitMinute),
                });
                nextShifts.push({
                  ...shift,
                  id: `${shift.id}-b-${Date.now()}`,
                  startMinute: splitMinute,
                  start: formatTime(splitMinute),
                  endMinute: rightEnd,
                  end: formatTime(rightEnd),
                  durationMinutes: rightEnd - splitMinute,
                  overnight: rightEnd > 1440,
                  breaks: computeDefaultBreaks(splitMinute, rightEnd),
                });
              });
              return { ...day, shifts: nextShifts };
            }),
          );
        };
        window.addEventListener('pointermove', onSplitMove);
        window.addEventListener('pointerup', onSplitUp);
        return;
      }

      if (event.button !== 0) return;
      const startSnapshot = clonePlannerDays(plannerDaysRef.current);
      dragStartSnapshotRef.current = startSnapshot;
      setActiveDragId(shiftId);

      const onMove = (moveEvent) => {
        const deltaMinutes = snapMinutes(((moveEvent.clientX - event.clientX) / width) * 1440);
        if (action === 'resize-left') {
          updateShift(dayIndex, shiftId, () => ({
            startMinute: clamp(originalStart + deltaMinutes, 0, originalEnd - MIN_SHIFT_MINUTES),
            endMinute: originalEnd,
          }));
          return;
        }
        if (action === 'resize-right') {
          updateShift(dayIndex, shiftId, () => ({
            startMinute: originalStart,
            endMinute: clamp(originalEnd + deltaMinutes, originalStart + MIN_SHIFT_MINUTES, MAX_SHIFT_END_MINUTES),
          }));
          return;
        }
        const nextStart = clamp(originalStart + deltaMinutes, 0, MAX_SHIFT_END_MINUTES - originalDuration);
        updateShift(dayIndex, shiftId, () => ({
          startMinute: nextStart,
          endMinute: nextStart + originalDuration,
        }));
      };

      const onUp = (upEvent) => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        const targetElement = document.elementFromPoint(upEvent.clientX, upEvent.clientY);
        const targetDayNode = targetElement?.closest?.('[data-planner-day-index]');
        const targetDayIndex = Number(targetDayNode?.getAttribute('data-planner-day-index'));
        if (Number.isFinite(targetDayIndex)) {
          moveShiftToDay(dayIndex, targetDayIndex, shiftId);
        }
        const changed =
          plannerDaysSignature(startSnapshot) !== plannerDaysSignature(plannerDaysRef.current) ||
          (Number.isFinite(targetDayIndex) && Number(targetDayIndex) !== Number(dayIndex));
        if (changed) pushHistorySnapshot(startSnapshot);
        dragStartSnapshotRef.current = null;
        setActiveDragId('');
      };

      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    },
    [moveShiftToDay, plannerDays, pushHistorySnapshot, updateShift],
  );

  const deleteShift = useCallback((dayIndex, shiftId) => {
    pushHistorySnapshot();
    setPlannerDays((current) =>
      current.map((day, index) => (
        index === dayIndex
          ? { ...day, shifts: (day.shifts || []).filter((shift) => shift.id !== shiftId) }
          : day
      )),
    );
  }, [pushHistorySnapshot]);

  const addShift = useCallback((dayIndex, templateId) => {
    const template = templates.find((item) => item.id === templateId) || selectedTemplate;
    const localTemplate = normalizeTemplateForLocalUse(template);
    if (!localTemplate) return;
    pushHistorySnapshot();
    const startMinute = localTemplate.startMinute;
    const endMinute = localTemplate.endMinute;
    const shift = {
      id: `manual-${Date.now()}-${Math.round(Math.random() * 10000)}`,
      templateId: localTemplate.id,
      rate: localTemplate.rate,
      label: localTemplate.label,
      start: formatTime(startMinute),
      end: formatTime(endMinute),
      startMinute,
      endMinute,
      durationMinutes: endMinute - startMinute,
      overnight: endMinute > 1440,
      breaks: computeDefaultBreaks(startMinute, endMinute),
    };
    setPlannerDays((current) =>
      current.map((day, index) => (
        index === dayIndex ? { ...day, shifts: [...(day.shifts || []), shift] } : day
      )),
    );
  }, [pushHistorySnapshot, selectedTemplate, templates]);

  const resetTemplates = useCallback(() => {
    if (typeof window !== 'undefined') window.localStorage.removeItem(TEMPLATE_STORAGE_KEY);
    loadDefaultTemplates();
  }, [loadDefaultTemplates]);

  const activeDayIndex = computedDays.length ? clamp(Number(selectedDayIndex || 0), 0, computedDays.length - 1) : 0;

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-slate-950">Генератор графиков по FTE</h2>
            <div className="mt-1 text-sm text-slate-500">Неделя: {selectedWeekStart || '-'}</div>
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              type="date"
              value={selectedWeekStart || ''}
              onChange={(event) => {
                if (typeof onWeekStartChange === 'function') onWeekStartChange(event.target.value);
              }}
              className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            />
            <button
              type="button"
              onClick={undoPlannerChange}
              disabled={!historyPast.length}
              className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
              title="Ctrl+Z"
            >
              <Undo2 size={16} />
            </button>
            <button
              type="button"
              onClick={redoPlannerChange}
              disabled={!historyFuture.length}
              className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
              title="Ctrl+Y"
            >
              <Redo2 size={16} />
            </button>
            <button
              type="button"
              onClick={loadDefaultTemplates}
              disabled={isTemplatesLoading}
              className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw size={16} className={isTemplatesLoading ? 'animate-spin' : ''} />
              Шаблоны
            </button>
            <button
              type="button"
              onClick={generatePreview}
              disabled={isGenerating || !templates.length}
              className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-900 px-4 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
            >
              <Wand2 size={16} />
              {isGenerating ? 'Расчет...' : 'Сгенерировать'}
            </button>
          </div>
        </div>

        {errorText ? (
          <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
            {errorText}
          </div>
        ) : null}

        <div className="mt-4 grid gap-3 md:grid-cols-4">
          <div className="rounded-lg bg-slate-50 px-3 py-2">
            <div className="text-xs text-slate-500">Покрыто</div>
            <b className="text-slate-950">{formatNumber(summary.coveredFteHours, 1)} FTE-ч</b>
          </div>
          <div className="rounded-lg bg-slate-50 px-3 py-2">
            <div className="text-xs text-slate-500">Нужно</div>
            <b className="text-slate-950">{intFormatter.format(roundMathFte(summary.neededFteHours || 0))} FTE-ч</b>
          </div>
          <div className="rounded-lg bg-slate-50 px-3 py-2">
            <div className="text-xs text-slate-500">Дефицит</div>
            <b className="text-rose-700">{formatNumber(summary.deficitFteHours, 1)} FTE-ч</b>
          </div>
          <div className="rounded-lg bg-slate-50 px-3 py-2">
            <div className="text-xs text-slate-500">Закрытие</div>
            <b className="text-emerald-700">{formatNumber(summary.coveragePercent, 1)}%</b>
          </div>
        </div>

        {serverSummary ? (
          <div className="mt-3 text-xs text-slate-500">
            Исходный расчет: {formatNumber(serverSummary.coveragePercent, 1)}% · перепокрытие {formatNumber(serverSummary.overFteHours, 1)} FTE-ч
          </div>
        ) : null}
      </section>

      <ShiftTemplateEditor
        templates={templates}
        selectedTemplateId={selectedTemplateId}
        onTemplatesChange={applyTemplates}
        onSelectedTemplateChange={setSelectedTemplateId}
        onReset={resetTemplates}
      />

      <div className="space-y-4">
        {computedDays.length ? (
          <>
            <PlannerDayCards
              days={computedDays}
              selectedDayIndex={activeDayIndex}
              onSelect={setSelectedDayIndex}
            />
            <PlannerDayRow
              key={computedDays[activeDayIndex]?.date || activeDayIndex}
              day={computedDays[activeDayIndex]}
              previousDay={activeDayIndex > 0 ? computedDays[activeDayIndex - 1] : null}
              dayIndex={activeDayIndex}
              templates={templates.filter((template) => template.enabled !== false)}
              selectedTemplateId={selectedTemplateId}
              activeDragId={activeDragId}
              splitPreview={splitPreview}
              timelineRef={(node) => setTimelineRef(activeDayIndex, node)}
              onShiftPointerDown={handleShiftPointerDown}
              onDeleteShift={deleteShift}
              onAddShift={addShift}
            />
          </>
        ) : (
          <div className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center text-sm text-slate-500">
            Нажмите «Сгенерировать», чтобы построить виртуальные линии смен по прогнозу выбранной недели.
          </div>
        )}
      </div>
      {splitPreview ? (
        <div
          className="pointer-events-none fixed z-50 rounded-md bg-slate-950 px-2 py-1 text-xs font-semibold text-white shadow-lg"
          style={{
            left: Number(splitPreview.x || 0) + 12,
            top: Number(splitPreview.y || 0) + 12,
          }}
        >
          {formatTime(splitPreview.minute)}
        </div>
      ) : null}
    </div>
  );
};

export default ResourceSchedulePlanner;
