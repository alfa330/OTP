import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  BarChart3,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Eye,
  ListChecks,
  RefreshCw,
  Users,
  X,
} from 'lucide-react';

/*
  Общий визуал раздела «Расчет ресурсов · Чат».

  Это КОПИЯ карточек, календаря и хелперов линии (ResourceFteView.jsx), а не вынос из неё:
  текст того файла читают чужие тесты табло и биллинга, поэтому линию трогать нельзя.
  Отсюда же и правило «Tailwind-классы один в один» — чат обязан выглядеть как линия.
  Подписи при копировании переведены на чатовые: у чата нет ни телефонии, ни выгрузок,
  людей зовём чатниками, а отметка в календаре означает день с обращениями.
*/

// ---------------------------------------------------------------------------
// Даты и числа
// ---------------------------------------------------------------------------

// Дату собираем из локальных getFullYear/getMonth/getDate. Приведение к UTC отдаёт в
// Asia/Almaty (+6) ночью ВЧЕРАШНИЙ день — на этом сдвиг недели уезжал на сутки назад.
export const todayIso = () => {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
};

const toIsoDate = (date) => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

// Единственный разрешённый способ считать даты в чатовом разделе.
export const addDaysIso = (iso, days) => {
  const [year, month, day] = String(iso || todayIso()).split('-').map(Number);
  const date = new Date(year, (month || 1) - 1, day || 1);
  date.setDate(date.getDate() + days);
  return toIsoDate(date);
};

export const parseIsoDate = (iso) => {
  const [year, month, day] = String(iso || '').split('-').map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
};

// Календарь всегда рисует 6 недель с понедельника — чтобы сетка не прыгала по месяцам.
export const buildCalendarDays = (monthDate) => {
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

const monthLabel = (date) =>
  new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric' }).format(date);

const daysBetweenInclusive = (startIso, endIso) => {
  const start = parseIsoDate(startIso);
  const end = parseIsoDate(endIso);
  if (!start || !end) return 0;
  return Math.round((end - start) / 86400000) + 1;
};

// Сравниваем ISO-строки, а не Date: лексикографический порядок у 'YYYY-MM-DD' совпадает
// с хронологическим и не зависит от часового пояса.
const isIsoInRange = (iso, startIso, endIso) => {
  if (!iso || !startIso || !endIso) return false;
  return iso >= startIso && iso <= endIso;
};

export const formatDate = (iso) => {
  if (!iso) return '—';
  const [year, month, day] = String(iso).split('-');
  return day && month && year ? `${day}.${month}.${year}` : iso;
};

export const formatDateShort = (iso) => {
  if (!iso) return '—';
  const [, month, day] = String(iso).split('-');
  return day && month ? `${day}.${month}` : iso;
};

export const formatNumber = (value, digits = 1) =>
  new Intl.NumberFormat('ru-RU', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(Number(value || 0));

export const formatSignedNumber = (value, digits = 1) => {
  const number = Number(value || 0);
  const sign = number > 0 ? '+' : '';
  return `${sign}${formatNumber(number, digits)}`;
};

export const formatInt = (value) => new Intl.NumberFormat('ru-RU').format(Math.round(Number(value || 0)));

export const formatPercent = (value, digits = 1) => `${formatNumber(Number(value || 0) * 100, digits)}%`;

// ---------------------------------------------------------------------------
// Карточки и мелкие элементы
// ---------------------------------------------------------------------------

const STATCARD_TONE = {
  blue: { iconBg: 'bg-blue-50 text-blue-700 ring-blue-100', accent: 'bg-blue-500', value: 'text-slate-950' },
  emerald: { iconBg: 'bg-emerald-50 text-emerald-700 ring-emerald-100', accent: 'bg-emerald-500', value: 'text-slate-950' },
  amber: { iconBg: 'bg-amber-50 text-amber-700 ring-amber-100', accent: 'bg-amber-500', value: 'text-slate-950' },
  rose: { iconBg: 'bg-rose-50 text-rose-700 ring-rose-100', accent: 'bg-rose-500', value: 'text-slate-950' },
  slate: { iconBg: 'bg-slate-100 text-slate-700 ring-slate-200', accent: 'bg-slate-400', value: 'text-slate-950' },
};

export const StatCard = ({
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

// Оболочка секции: заголовок слева, действия справа — как на линии.
export const SectionCard = ({ title, description, actions, className = '', children }) => (
  <section className={`rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${className}`}>
    {title || description || actions ? (
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          {title ? <h2 className="text-lg font-semibold text-slate-950">{title}</h2> : null}
          {description ? <p className="text-sm text-slate-500">{description}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    ) : null}
    {children}
  </section>
);

export const EmptyState = ({ title, text, action }) => (
  <div className="flex min-h-[220px] flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center">
    <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-slate-100 text-slate-500">
      <BarChart3 size={22} aria-hidden="true" />
    </div>
    <h3 className="mt-4 text-base font-semibold text-slate-900">{title}</h3>
    <p className="mt-1 max-w-md text-sm text-slate-500">{text}</p>
    {action ? <div className="mt-4">{action}</div> : null}
  </div>
);

export const ToggleSwitch = ({ checked, label, onChange }) => (
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

// ---------------------------------------------------------------------------
// Доступность чатников
// ---------------------------------------------------------------------------

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

// Людей в чат-направлении сотни — таблицу режем страницами, иначе модалка встаёт.
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

export const OperatorStatusChips = ({ statusDays }) => {
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

export const OperatorSummaryCard = ({
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
  excludedCount = 0,
  excludedFte = 0,
  onOpen,
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
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Чатники</p>
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
          <b className={`tabular-nums ${Number(gap || 0) < 0 ? 'text-rose-700' : 'text-emerald-700'}`}>{formatSignedNumber(gap, 2)} FTE</b>
        </div>
        {hasUpliftRequirement ? (
          <div className="rounded-lg bg-emerald-50 px-3 py-2">
            <span className="block text-emerald-700">Разница с приростом</span>
            <b className={`tabular-nums ${upliftGap < 0 ? 'text-rose-700' : 'text-emerald-700'}`}>{formatSignedNumber(upliftGap, 2)} FTE</b>
          </div>
        ) : null}
        <div className="rounded-lg bg-slate-50 px-3 py-2">
          <span className="block text-slate-500">Сотрудники</span>
          <b className="text-slate-900 tabular-nums">{formatInt(availableCount)} / {formatInt(totalCount)}</b>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500 tabular-nums">
        <span>Без усушки: {formatNumber(baseFte, 2)}</span>
        <span>Текущий FTE: {formatNumber(currentFte, 2)}</span>
        <span>Часть периода: {formatInt(partialCount)}</span>
        <span>Не работают: {formatInt(unavailableCount)}</span>
        {Number(excludedCount || 0) > 0 ? (
          // Люди со ставкой вне набора чата отброшены из «Доступно». Прятать их нельзя:
          // иначе карточка молча теряет сотрудников, которые в направлении есть.
          <span className="text-amber-700">
            Вне ставок чата: {formatInt(excludedCount)} чел. (−{formatNumber(excludedFte, 2)} FTE)
          </span>
        ) : null}
      </div>
      <div className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-blue-700">
        <Eye size={14} aria-hidden="true" />
        Детали расчета
      </div>
    </button>
  );
};

export const OperatorAvailabilityDetailsModal = ({ open, onClose, forecast, isLoading = false, error = '' }) => {
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
              <div className="text-sm font-semibold text-slate-950">Чатники в расчете</div>
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
                    <th className="px-4 py-3 text-left font-semibold">Чатник</th>
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
                      <td className="px-4 py-8 text-center text-sm text-slate-500" colSpan={6}>Нет данных по чатникам.</td>
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

// ---------------------------------------------------------------------------
// Календарь
// ---------------------------------------------------------------------------

export const CalendarPicker = ({
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
  // Пока начало периода выбрано, но конец ещё нет, показываем черновик — иначе подсветка
  // диапазона прыгает между старым и новым выбором.
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
              const hasChats = loadedSet.has(iso);
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
                  {hasChats && (
                    <span className={`absolute bottom-1 h-1.5 w-1.5 rounded-full ${isSelected ? 'bg-white' : 'bg-emerald-500'}`} />
                  )}
                </button>
              );
            })}
          </div>

          <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3 text-xs text-slate-500">
            <span className="inline-flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> есть чаты</span>
            {hint ? <span>{hint}</span> : null}
          </div>
        </div>
      )}
    </div>
  );
};
