import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Maximize2, Minimize2 } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import FullscreenSheet from '../common/FullscreenSheet';
import { ACTION_NEED_META } from './taskActionNeeds';
import { BOARD_CHUNK_SIZES, DEFAULT_BOARD_CHUNK, normalizeBoardChunk } from './boardQuery';
import { groupTasksByDay } from './boardGrouping';

export { BOARD_COLUMN_QUERY, BOARD_CHUNK_SIZES, DEFAULT_BOARD_CHUNK, boardQueryParams, normalizeBoardChunk } from './boardQuery';

/*
 * Бэклог + канбан + таймлайн раздела «Задачи».
 *
 * Модель намеренно не заводит новых статусов: колонки доски — это существующий
 * жизненный цикл задачи (assigned → in_progress → completed → accepted),
 * а бэклог — отдельный флаг is_backlog поверх статуса `assigned`.
 *
 * Визуально: нейтральная slate-палитра, цвет несёт смысл (просрочка/срок/готово),
 * обычный приоритет не рисуется вовсе — иначе доска превращается в светофор.
 */

/* ─────────────── Константы ─────────────── */

export const BOARD_COLUMNS = [
  { id: 'backlog',  title: 'Бэклог',       caption: 'Ждут очереди' },
  { id: 'todo',     title: 'К выполнению', caption: 'Назначены' },
  { id: 'progress', title: 'В работе',     caption: 'Идут сейчас' },
  { id: 'review',   title: 'На проверке',  caption: 'Ждут приёмки' },
  { id: 'done',     title: 'Готово',       caption: 'Приняты' },
];

const PRIORITY_DOT = {
  critical: { color: '#e11d48', label: 'Критичная' },
  urgent:   { color: '#f59e0b', label: 'Срочная' },
};

const BOARD_PRIORITY_ORDER = { critical: 0, urgent: 1, normal: 2 };

export const BOARD_SORT_OPTIONS = [
  { value: 'freshness', label: 'По свежести' },
  { value: 'importance', label: 'По важности' },
];

const TAG_LABEL = { task: 'Задача', problem: 'Проблема', suggestion: 'Предложение' };

const ESTIMATE_PRESETS = [
  { label: '30 м', minutes: 30 },
  { label: '1 ч',  minutes: 60 },
  { label: '2 ч',  minutes: 120 },
  { label: '4 ч',  minutes: 240 },
  { label: '1 д',  minutes: 480 },
  { label: '3 д',  minutes: 1440 },
];

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/* ─────────────── Утилиты ─────────────── */

export const columnOfTask = (task) => {
  if (task?.is_backlog) return 'backlog';
  switch (task?.status) {
    case 'in_progress':
    case 'returned':
      return 'progress';
    case 'completed':
      return 'review';
    case 'accepted':
      return 'done';
    default:
      return 'todo';
  }
};

const parseDate = (value) => {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

/** Стабильный порядок канбана: новые карточки сверху, важность — с тем же tie-break. */
export const compareBoardTasks = (left, right, sortMode = 'freshness') => {
  if (sortMode === 'importance') {
    const leftPriority = BOARD_PRIORITY_ORDER[left?.priority] ?? BOARD_PRIORITY_ORDER.normal;
    const rightPriority = BOARD_PRIORITY_ORDER[right?.priority] ?? BOARD_PRIORITY_ORDER.normal;
    if (leftPriority !== rightPriority) return leftPriority - rightPriority;
  }

  const leftCreatedAt = parseDate(left?.created_at)?.getTime() || 0;
  const rightCreatedAt = parseDate(right?.created_at)?.getTime() || 0;
  if (leftCreatedAt !== rightCreatedAt) return rightCreatedAt - leftCreatedAt;
  return Number(right?.id || 0) - Number(left?.id || 0);
};

const startOfDay = (date) => {
  const copy = new Date(date);
  copy.setHours(0, 0, 0, 0);
  return copy;
};

const addDays = (date, days) => {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + days);
  return copy;
};

export const formatDurationMinutes = (minutes) => {
  const total = Math.max(0, Math.round(Number(minutes) || 0));
  if (!total) return '—';
  if (total < 60) return `${total} м`;
  const hours = Math.floor(total / 60);
  const restMinutes = total % 60;
  if (hours < 24) return restMinutes ? `${hours} ч ${restMinutes} м` : `${hours} ч`;
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours ? `${days} д ${restHours} ч` : `${days} д`;
};

const formatDurationMs = (ms) => formatDurationMinutes(Math.round((Number(ms) || 0) / MINUTE));

const formatShortDate = (value) => {
  const date = parseDate(value);
  if (!date) return '';
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' });
};

const formatDateTimeLabel = (value) => {
  const date = parseDate(value);
  if (!date) return '';
  return date.toLocaleString('ru-RU', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
};

const toDatetimeLocalValue = (value) => {
  const date = parseDate(value);
  if (!date) return '';
  const pad = (num) => String(num).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

/** Тон срока: просрочено / горит / спокойно. Задачи в финале сроками не подсвечиваем. */
export const dueTone = (task, now = Date.now()) => {
  const due = parseDate(task?.due_at);
  if (!due) return 'none';
  if (task?.status === 'accepted' || task?.status === 'completed') return 'done';
  const diff = due.getTime() - now;
  if (diff < 0) return 'overdue';
  if (diff < DAY) return 'soon';
  return 'normal';
};

const DUE_TONE_CLASS = {
  overdue: 'bg-rose-50 text-rose-600 ring-rose-100',
  soon:    'bg-amber-50 text-amber-700 ring-amber-100',
  normal:  'bg-slate-100 text-slate-500 ring-transparent',
  done:    'bg-slate-100 text-slate-400 ring-transparent',
  none:    'bg-slate-100 text-slate-400 ring-transparent',
};

const pluralTasks = (count) => {
  const mod10 = Math.abs(count) % 10;
  const mod100 = Math.abs(count) % 100;
  if (mod10 === 1 && mod100 !== 11) return 'задача';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'задачи';
  return 'задач';
};

const initials = (name) => String(name || '?')
  .trim()
  .split(/\s+/)
  .slice(0, 2)
  .map((part) => part[0])
  .join('')
  .toUpperCase() || '?';

/**
 * Чип трудозатрат: «факт / оценка», если отчёты уже есть, иначе просто оценка.
 * Перерасход подсвечиваем — это единственный сигнал, который тут нужен.
 */
export const effortChipOf = (task) => {
  const estimate = Number(task?.estimate_minutes) || 0;
  const spent = Number(task?.spent_minutes) || 0;
  if (!estimate && !spent) return null;
  if (!spent) {
    return { label: formatDurationMinutes(estimate), tone: 'normal', title: 'Оценка' };
  }
  if (!estimate) {
    return { label: formatDurationMinutes(spent), tone: 'normal', title: 'Затрачено по отчётам' };
  }
  return {
    label: `${formatDurationMinutes(spent)} / ${formatDurationMinutes(estimate)}`,
    tone: spent > estimate ? 'soon' : 'normal',
    title: `Затрачено по отчётам ${formatDurationMinutes(spent)} из оценки ${formatDurationMinutes(estimate)}`,
  };
};

const checklistProgress = (task) => {
  const items = Array.isArray(task?.checklist) ? task.checklist : [];
  if (!items.length) return null;
  return { done: items.filter((item) => item?.is_done).length, total: items.length };
};

/** Фактическое начало работ: колонка started_at, иначе первый переход в in_progress. */
const actualStartOf = (task) => {
  const direct = parseDate(task?.started_at);
  if (direct) return direct;
  const history = Array.isArray(task?.history) ? task.history : [];
  const started = history.find((item) => item?.status_code === 'in_progress');
  return parseDate(started?.changed_at);
};

const actualEndOf = (task) => {
  if (task?.status === 'accepted' || task?.status === 'completed') {
    return parseDate(task?.completed_at) || parseDate(task?.updated_at);
  }
  return null;
};

/**
 * Разрешение drag&drop: какая операция стоит за переносом карточки между колонками.
 * Возвращает {type:'status'|'board'|'blocked'|'noop'}.
 */
export const resolveBoardDrop = (task, toColumn, ctx) => {
  const from = columnOfTask(task);
  if (from === toColumn) return { type: 'noop' };

  const assigneeId = Number(task?.assignee?.id || 0);
  const creatorId = Number(task?.creator?.id || 0);
  const requesterId = Number(task?.requested_by?.id || 0);
  const isAssignee = assigneeId === ctx.currentUserId;
  const isCreator = creatorId === ctx.currentUserId;
  const canPlan = isCreator || ctx.isAdmin;
  // Приёмка за поручителем (или постановщиком). Свою работу себе не принимают —
  // кроме своей инициативы, где принимать больше некому.
  const authorityId = requesterId || creatorId;
  const canReview = (authorityId && authorityId === ctx.currentUserId)
    || (!isAssignee && (ctx.isAdmin || isCreator || ctx.isSupervisor));

  if (toColumn === 'backlog') {
    if (task?.status !== 'assigned') return { type: 'blocked', reason: 'В бэклог можно вернуть только не начатую задачу' };
    if (!canPlan) return { type: 'blocked', reason: 'Двигать в бэклог может постановщик или админ' };
    return { type: 'board', patch: { is_backlog: true } };
  }

  if (toColumn === 'todo') {
    if (from !== 'backlog') return { type: 'blocked', reason: 'Вернуть задачу в «К выполнению» нельзя — используйте возврат на доработку' };
    if (!(canPlan || isAssignee)) return { type: 'blocked', reason: 'Нет прав выносить задачу из бэклога' };
    return { type: 'board', patch: { is_backlog: false } };
  }

  if (toColumn === 'progress') {
    if (from === 'review') {
      if (!canReview) return { type: 'blocked', reason: 'Вернуть на доработку может тот, кто поручил задачу' };
      return { type: 'status', action: 'returned' };
    }
    if (from === 'done') {
      if (!canReview) return { type: 'blocked', reason: 'Возобновить задачу может тот, кто поручил её' };
      return { type: 'status', action: 'reopened' };
    }
    if (!isAssignee) return { type: 'blocked', reason: 'Взять задачу в работу может только исполнитель' };
    return { type: 'status', action: 'in_progress' };
  }

  if (toColumn === 'review') {
    if (from !== 'progress') return { type: 'blocked', reason: 'На проверку задача уходит из работы' };
    if (!isAssignee) return { type: 'blocked', reason: 'Отметить выполненной может только исполнитель' };
    return { type: 'status', action: 'completed' };
  }

  if (toColumn === 'done') {
    if (from !== 'review') return { type: 'blocked', reason: 'Принять можно только задачу на проверке' };
    if (!canReview) return { type: 'blocked', reason: 'Итог принимает тот, кто поручил задачу — свою работу себе не принимают' };
    return { type: 'status', action: 'accepted' };
  }

  return { type: 'blocked', reason: 'Недоступный перенос' };
};

/** Новый ранг для позиции index в уже отфильтрованном списке (без перетаскиваемой карточки). */
export const rankForPosition = (listWithoutDragged, index) => {
  const prev = listWithoutDragged[index - 1]?.backlog_rank;
  const next = listWithoutDragged[index]?.backlog_rank;
  const prevNum = Number.isFinite(prev) ? prev : null;
  const nextNum = Number.isFinite(next) ? next : null;
  if (prevNum === null && nextNum === null) return index + 1;
  if (prevNum === null) return nextNum - 1;
  if (nextNum === null) return prevNum + 1;
  if (nextNum - prevNum < 1e-6) return prevNum + 1e-6;
  return (prevNum + nextNum) / 2;
};

/* ─────────────── Атомы ─────────────── */

const SegmentedControl = ({ value, options, onChange, className = '' }) => (
  <div className={`inline-flex rounded-[10px] bg-slate-100 p-[3px] ${className}`}>
    {options.map((option) => {
      const active = option.value === value;
      return (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={`relative rounded-[8px] px-3 py-[5px] text-[12.5px] font-medium transition-all ${
            active
              ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.10)]'
              : 'text-slate-500 hover:text-slate-700'
          }`}
        >
          {option.label}
          {Number.isFinite(option.count) && option.count > 0 && (
            <span className={`ml-1.5 text-[11px] tabular-nums ${active ? 'text-slate-400' : 'text-slate-400'}`}>
              {option.count}
            </span>
          )}
        </button>
      );
    })}
  </div>
);

const Avatar = ({ person, size = 22 }) => {
  const url = (person?.avatar_url || '').trim();
  return (
    <span
      title={person?.name || ''}
      className="inline-grid shrink-0 place-items-center overflow-hidden rounded-full bg-slate-200 font-semibold text-slate-600"
      style={{ width: size, height: size, fontSize: Math.max(9.5, Math.round(size * 0.36 * 10) / 10) }}
    >
      {url
        ? <img src={url} alt="" className="h-full w-full object-cover" loading="lazy" />
        : initials(person?.name)}
    </span>
  );
};

const FlowArrow = () => (
  <svg width="9" height="9" viewBox="0 0 10 10" fill="none" aria-hidden="true" className="shrink-0 text-slate-300">
    <path d="M1.6 5h6.4M5.8 2.6 8.2 5 5.8 7.4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

/**
 * Лица задачи на карточке: «кто поручил → кто исполняет».
 * На доске конкретного сотрудника его собственное лицо не повторяем в каждой карточке —
 * оно уже в шапке доски, а полезен ровно второй участник.
 */
const CardFaces = ({ task, focusPersonId = 0 }) => {
  const creator = task?.creator;
  const assignee = task?.assignee;
  const creatorId = Number(creator?.id || 0);
  const assigneeId = Number(assignee?.id || 0);
  const creatorName = creator?.name || '—';
  const assigneeName = assignee?.name || '—';

  if (creatorId && creatorId === assigneeId) {
    return (
      <span className="flex shrink-0 items-center" title={`Своя инициатива: ${assigneeName}`}>
        <Avatar person={assignee} size={20} />
      </span>
    );
  }

  if (focusPersonId && assigneeId === focusPersonId) {
    return (
      <span className="flex shrink-0 items-center" title={`Поручил: ${creatorName}`}>
        <Avatar person={creator} size={20} />
      </span>
    );
  }

  if (focusPersonId && creatorId === focusPersonId) {
    return (
      <span className="flex shrink-0 items-center gap-0.5" title={`Исполнитель: ${assigneeName}`}>
        <FlowArrow />
        <Avatar person={assignee} size={20} />
      </span>
    );
  }

  return (
    <span className="flex shrink-0 items-center gap-0.5" title={`${creatorName} → ${assigneeName}`}>
      <Avatar person={creator} size={20} />
      <FlowArrow />
      <Avatar person={assignee} size={20} />
    </span>
  );
};

/**
 * Собачка в правом нижнем углу карточки: задача ждёт шага именно этого пользователя.
 * Мигает, пока уведомление не прочитано, — прочитанное больше не дёргает.
 */
const ActionAtMarker = ({ kind }) => {
  const meta = ACTION_NEED_META[kind];
  if (!meta) return null;
  return (
    <span
      className="tb-at-marker"
      style={{ color: meta.dot, background: `${meta.dot}1f` }}
      title={`Ждёт вашего действия: ${meta.label}`}
      aria-label={`Ждёт вашего действия: ${meta.label}`}
    >
      @
    </span>
  );
};

const PriorityDot = ({ priority }) => {
  const meta = PRIORITY_DOT[priority];
  if (!meta) return null;
  return (
    <span
      title={meta.label}
      className="inline-block h-[7px] w-[7px] shrink-0 rounded-full"
      style={{ backgroundColor: meta.color }}
    />
  );
};

const MetaChip = ({ tone = 'slate', title, children }) => (
  <span
    title={title}
    className={`inline-flex items-center gap-1 rounded-md px-1.5 py-[2px] text-[11px] font-medium ring-1 ${
      DUE_TONE_CLASS[tone] || DUE_TONE_CLASS.normal
    }`}
  >
    {children}
  </span>
);

const ClockIcon = () => (
  <svg width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden="true">
    <circle cx="6" cy="6" r="4.6" stroke="currentColor" strokeWidth="1.2" />
    <path d="M6 3.6V6l1.7 1.1" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
  </svg>
);

const FlagIcon = () => (
  <svg width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden="true">
    <path d="M3 10.5V2m0 0h6l-1.3 2L9 6H3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const ReportIcon = () => (
  <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true" className="inline-block align-[-1px]">
    <path d="M3 1.5h4.2L9.5 3.8V10a.5.5 0 0 1-.5.5H3a.5.5 0 0 1-.5-.5V2a.5.5 0 0 1 .5-.5Z" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round" />
    <path d="M4.4 6.3h3.2M4.4 8h2.2" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
  </svg>
);

/** Шапка доски сотрудника: чьё это пространство. Ниже, в карточках, лица уже не дублируются. */
const BoardPersonHeader = ({ person, stats, onReset }) => (
  <div className="flex items-center gap-3 rounded-2xl bg-white px-3.5 py-2.5 ring-1 ring-slate-200/70">
    <span className="rounded-full ring-1 ring-slate-900/5">
      <Avatar person={person} size={38} />
    </span>
    <span className="min-w-0 flex-1">
      <span className="block truncate text-[15px] font-semibold tracking-[-0.01em] text-slate-900">
        {person?.name || 'Сотрудник'}
      </span>
      <span className="mt-[1px] flex flex-wrap items-center gap-x-1.5 text-[11.5px] text-slate-400">
        <span>Открытых: <b className="font-semibold tabular-nums text-slate-500">{stats.open}</b></span>
        {stats.inProgress > 0 && (
          <span>· В работе: <b className="font-semibold tabular-nums text-slate-500">{stats.inProgress}</b></span>
        )}
        {stats.overdue > 0 && (
          <span className="text-rose-500">· Просрочено: <b className="font-semibold tabular-nums">{stats.overdue}</b></span>
        )}
        {stats.delegated > 0 && (
          <span>· Поручено другим: <b className="font-semibold tabular-nums text-slate-500">{stats.delegated}</b></span>
        )}
      </span>
    </span>
    <button
      type="button"
      onClick={onReset}
      className="shrink-0 rounded-xl px-2.5 py-1.5 text-[12.5px] font-medium text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 active:scale-[0.98]"
    >
      Моя доска
    </button>
  </div>
);

/** Пагинация доски: страница приходит с сервера, поэтому листаем запросами, не рендером. */
const BoardPager = ({ from, to, total, page, totalPages, isLoading, onPageChange }) => {
  if (total <= 0) return null;
  const single = totalPages <= 1;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-1">
      <span className="text-[11.5px] text-slate-400">
        {single
          ? `${total} ${pluralTasks(total)}`
          : <>Показаны <b className="font-semibold tabular-nums text-slate-500">{from}–{to}</b> из <b className="font-semibold tabular-nums text-slate-500">{total}</b></>}
        {isLoading && <span className="ml-2 text-slate-300">обновляю…</span>}
      </span>
      {!single && (
        <span className="flex items-center gap-1">
          <button
            type="button"
            disabled={page <= 1 || isLoading}
            onClick={() => onPageChange(page - 1)}
            className="grid h-7 w-7 place-items-center rounded-lg text-[13px] text-slate-500 transition hover:bg-slate-200/70 hover:text-slate-800 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent"
            aria-label="Предыдущая страница"
          >
            ‹
          </button>
          <span className="min-w-[54px] text-center text-[11.5px] tabular-nums text-slate-500">
            {page} / {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages || isLoading}
            onClick={() => onPageChange(page + 1)}
            className="grid h-7 w-7 place-items-center rounded-lg text-[13px] text-slate-500 transition hover:bg-slate-200/70 hover:text-slate-800 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent"
            aria-label="Следующая страница"
          >
            ›
          </button>
        </span>
      )}
    </div>
  );
};

const EmptyState = ({ title, hint }) => (
  <div className="grid place-items-center rounded-2xl border border-dashed border-slate-200 px-6 py-12 text-center">
    <p className="text-[13.5px] font-medium text-slate-600">{title}</p>
    {hint && <p className="mt-1 max-w-sm text-[12px] leading-relaxed text-slate-400">{hint}</p>}
  </div>
);

/* ─────────────── Поповер планирования (оценка + срок) ─────────────── */

const PLAN_POPOVER_WIDTH = 248;

const PlanPopover = ({ task, canPlan, anchorRef, onApply, onClose }) => {
  const [estimate, setEstimate] = useState(task?.estimate_minutes ? String(task.estimate_minutes) : '');
  const [due, setDue] = useState(toDatetimeLocalValue(task?.due_at));
  const [position, setPosition] = useState(null);
  const ref = useRef(null);

  // Поповер живёт в портале: колонки доски скроллятся по горизонтали и обрезали бы его.
  useLayoutEffect(() => {
    const rect = anchorRef?.current?.getBoundingClientRect();
    if (!rect) return;
    const height = ref.current?.offsetHeight || (canPlan ? 300 : 150);
    const left = Math.min(
      Math.max(8, rect.right - PLAN_POPOVER_WIDTH),
      window.innerWidth - PLAN_POPOVER_WIDTH - 8
    );
    const belowTop = rect.bottom + 6;
    const top = belowTop + height > window.innerHeight - 8
      ? Math.max(8, rect.top - height - 6)
      : belowTop;
    setPosition({ left, top });
  }, [anchorRef, canPlan]);

  useEffect(() => {
    const onDocMouseDown = (event) => {
      if (ref.current && !ref.current.contains(event.target)) onClose();
    };
    const onKey = (event) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', onDocMouseDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  const quickDue = (days, hour = 18) => {
    const target = addDays(startOfDay(new Date()), days);
    target.setHours(hour, 0, 0, 0);
    setDue(toDatetimeLocalValue(target));
  };

  const submit = () => {
    const patch = {};
    const nextEstimate = estimate.trim() === '' ? null : Number(estimate);
    if ((task?.estimate_minutes ?? null) !== nextEstimate) patch.estimate_minutes = nextEstimate;
    if (canPlan) {
      const nextDue = due ? new Date(due).toISOString() : null;
      const currentDue = task?.due_at ? new Date(task.due_at).toISOString() : null;
      if (nextDue !== currentDue) patch.due_at = nextDue;
    }
    onApply(patch);
    onClose();
  };

  return createPortal(
    <div
      ref={ref}
      className="fixed z-[95] rounded-2xl bg-white p-3 shadow-[0_12px_40px_rgba(15,23,42,0.16)] ring-1 ring-slate-900/10"
      onClick={(event) => event.stopPropagation()}
      style={{
        fontFamily: APPLE_FONT,
        width: PLAN_POPOVER_WIDTH,
        left: position?.left ?? -9999,
        top: position?.top ?? -9999,
        visibility: position ? 'visible' : 'hidden',
      }}
    >
      <p className="px-0.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Оценка</p>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {ESTIMATE_PRESETS.map((preset) => (
          <button
            key={preset.minutes}
            type="button"
            onClick={() => setEstimate(String(preset.minutes))}
            className={`rounded-lg px-2 py-1 text-[11.5px] font-medium transition ${
              Number(estimate) === preset.minutes
                ? 'bg-slate-900 text-white'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            {preset.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setEstimate('')}
          className="rounded-lg px-2 py-1 text-[11.5px] font-medium text-slate-400 transition hover:bg-slate-100"
        >
          Сброс
        </button>
      </div>

      {canPlan && (
        <>
          <p className="mt-3 px-0.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Дедлайн</p>
          <div className="mt-1.5 flex flex-wrap gap-1">
            <button type="button" onClick={() => quickDue(0)} className="rounded-lg bg-slate-100 px-2 py-1 text-[11.5px] font-medium text-slate-600 transition hover:bg-slate-200">Сегодня</button>
            <button type="button" onClick={() => quickDue(1)} className="rounded-lg bg-slate-100 px-2 py-1 text-[11.5px] font-medium text-slate-600 transition hover:bg-slate-200">Завтра</button>
            <button type="button" onClick={() => quickDue(3)} className="rounded-lg bg-slate-100 px-2 py-1 text-[11.5px] font-medium text-slate-600 transition hover:bg-slate-200">+3 дня</button>
            <button type="button" onClick={() => quickDue(7)} className="rounded-lg bg-slate-100 px-2 py-1 text-[11.5px] font-medium text-slate-600 transition hover:bg-slate-200">+неделя</button>
          </div>
          <input
            type="datetime-local"
            value={due}
            onChange={(event) => setDue(event.target.value)}
            className="mt-1.5 w-full rounded-xl bg-slate-100 px-2.5 py-1.5 text-[12.5px] text-slate-900 outline-none transition focus:bg-white focus:ring-2 focus:ring-blue-500/60"
          />
        </>
      )}

      <div className="mt-3 flex justify-end gap-1.5">
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg px-2.5 py-1.5 text-[12.5px] font-medium text-slate-500 transition hover:bg-slate-100"
        >
          Отмена
        </button>
        <button
          type="button"
          onClick={submit}
          className="rounded-lg bg-blue-600 px-3 py-1.5 text-[12.5px] font-semibold text-white transition hover:bg-blue-700 active:scale-[0.98]"
        >
          Сохранить
        </button>
      </div>
    </div>,
    document.body
  );
};

/* ─────────────── Бэклог ─────────────── */

const BacklogRow = ({
  task,
  index,
  canPlan,
  isDragging,
  isDropBefore,
  isDropAfter,
  onOpen,
  onPromote,
  onApplyPlan,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
}) => {
  const [planOpen, setPlanOpen] = useState(false);
  const planAnchorRef = useRef(null);
  const tone = dueTone(task);
  const effort = effortChipOf(task);

  return (
    <div
      draggable
      onDragStart={(event) => onDragStart(event, task)}
      onDragOver={(event) => onDragOver(event, index)}
      onDrop={(event) => { event.stopPropagation(); onDrop(event, index); }}
      onDragEnd={onDragEnd}
      onClick={() => onOpen(task)}
      className={`group relative flex cursor-pointer items-center gap-3 border-t border-slate-100 px-3 py-2.5 transition first:border-t-0 hover:bg-slate-50/80 ${
        isDragging ? 'opacity-40' : ''
      }`}
    >
      {isDropBefore && <span className="pointer-events-none absolute inset-x-2 -top-px h-0.5 rounded-full bg-blue-500" />}
      {isDropAfter && <span className="pointer-events-none absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-blue-500" />}

      <span className="w-5 shrink-0 text-right text-[11.5px] tabular-nums text-slate-300 group-hover:text-slate-400">
        {index + 1}
      </span>
      <PriorityDot priority={task?.priority} />

      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13.5px] font-medium text-slate-800">{task?.subject}</span>
        <span className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-slate-400">
          <span className="truncate">{TAG_LABEL[task?.tag] || 'Задача'}</span>
          {task?.assignee?.name && <span className="truncate">· {task.assignee.name}</span>}
        </span>
      </span>

      <span className="hidden items-center gap-1.5 sm:flex">
        {effort && (
          <MetaChip tone={effort.tone} title={effort.title}>
            <ClockIcon />
            {effort.label}
          </MetaChip>
        )}
        {task?.due_at && (
          <MetaChip tone={tone} title={`Дедлайн: ${formatDateTimeLabel(task.due_at)}`}>
            <FlagIcon />
            {formatShortDate(task.due_at)}
          </MetaChip>
        )}
      </span>

      <span className="relative flex shrink-0 items-center gap-1">
        <button
          ref={planAnchorRef}
          type="button"
          title="Оценка и срок"
          onClick={(event) => { event.stopPropagation(); setPlanOpen((prev) => !prev); }}
          className="grid h-7 w-7 place-items-center rounded-lg text-slate-400 opacity-0 transition hover:bg-slate-200/70 hover:text-slate-600 focus:opacity-100 group-hover:opacity-100"
        >
          <ClockIcon />
        </button>
        <button
          type="button"
          onClick={(event) => { event.stopPropagation(); onPromote(task); }}
          className="rounded-lg bg-slate-100 px-2.5 py-1 text-[12px] font-medium text-slate-600 transition hover:bg-slate-900 hover:text-white active:scale-[0.98]"
        >
          В работу
        </button>
        {planOpen && (
          <PlanPopover
            task={task}
            canPlan={canPlan(task)}
            anchorRef={planAnchorRef}
            onApply={(patch) => onApplyPlan(task, patch)}
            onClose={() => setPlanOpen(false)}
          />
        )}
      </span>
    </div>
  );
};

const BacklogView = ({ tasks, canPlan, onOpen, onPromote, onApplyPlan, onReorder, onCreate }) => {
  const [draggedId, setDraggedId] = useState(null);
  const [dropIndex, setDropIndex] = useState(null);

  const totalEstimate = useMemo(
    () => tasks.reduce((sum, task) => sum + (Number(task?.estimate_minutes) || 0), 0),
    [tasks]
  );
  const unestimated = useMemo(
    () => tasks.filter((task) => !Number(task?.estimate_minutes)).length,
    [tasks]
  );

  const handleDragStart = (event, task) => {
    setDraggedId(task.id);
    event.dataTransfer.effectAllowed = 'move';
    try { event.dataTransfer.setData('text/plain', String(task.id)); } catch (error) { /* Safari */ }
  };

  const handleDragOver = (event, index) => {
    if (draggedId === null) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    const bounds = event.currentTarget.getBoundingClientRect();
    const after = event.clientY > bounds.top + bounds.height / 2;
    setDropIndex(after ? index + 1 : index);
  };

  const handleDrop = (event) => {
    event.preventDefault();
    const targetIndex = dropIndex;
    const dragged = tasks.find((task) => task.id === draggedId);
    setDraggedId(null);
    setDropIndex(null);
    if (!dragged || targetIndex === null) return;

    const without = tasks.filter((task) => task.id !== dragged.id);
    const currentIndex = tasks.findIndex((task) => task.id === dragged.id);
    const insertAt = targetIndex > currentIndex ? targetIndex - 1 : targetIndex;
    if (insertAt === currentIndex) return;
    onReorder(dragged, rankForPosition(without, insertAt), insertAt);
  };

  const handleDragEnd = () => {
    setDraggedId(null);
    setDropIndex(null);
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <span className="text-[12px] text-slate-500">
            <b className="text-[13px] font-semibold text-slate-800 tabular-nums">{tasks.length}</b> в очереди
          </span>
          {totalEstimate > 0 && (
            <span className="text-[12px] text-slate-400">≈ {formatDurationMinutes(totalEstimate)} работы</span>
          )}
          {unestimated > 0 && (
            <span className="text-[12px] text-slate-400">{unestimated} без оценки</span>
          )}
        </div>
        <button
          type="button"
          onClick={onCreate}
          className="rounded-xl bg-slate-100 px-3 py-1.5 text-[12.5px] font-semibold text-slate-600 transition hover:bg-slate-200 active:scale-[0.98]"
        >
          + В бэклог
        </button>
      </div>

      {tasks.length === 0 ? (
        <EmptyState
          title="Бэклог пуст"
          hint="Сюда складывают задачи, которые взяты в план, но ещё не запущены. Исполнитель уведомление не получает, пока карточка не уйдёт на доску."
        />
      ) : (
        <div
          className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70"
          onDragOver={(event) => { if (draggedId !== null) event.preventDefault(); }}
          onDrop={handleDrop}
        >
          {tasks.map((task, index) => (
            <BacklogRow
              key={task.id}
              task={task}
              index={index}
              canPlan={canPlan}
              isDragging={draggedId === task.id}
              isDropBefore={dropIndex === index && draggedId !== null && draggedId !== task.id}
              isDropAfter={dropIndex === tasks.length && index === tasks.length - 1 && draggedId !== null && draggedId !== task.id}
              onOpen={onOpen}
              onPromote={onPromote}
              onApplyPlan={onApplyPlan}
              onDragStart={handleDragStart}
              onDragOver={handleDragOver}
              onDrop={handleDrop}
              onDragEnd={handleDragEnd}
            />
          ))}
        </div>
      )}

      <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
        Порядок сверху вниз — это приоритет. Перетащите карточку, чтобы изменить очередь.
      </p>
    </div>
  );
};

/* ─────────────── Окно статуса: дни по вертикали, карточки по горизонтали ─────────────── */

const COLUMN_BROWSER_PAGE = 40;

/* Окно статуса живёт НИЖЕ карточки задачи (.tv-overlay 40 / .tv-drawer 50) и её
   модалок: открытая задача перекрывает окно, а закрытая возвращает к нему. */
const COLUMN_BROWSER_Z = 30;

const ColumnBrowser = ({
  column,
  scope,
  sort,
  loadTasks,
  actionNeedOf,
  canPlan,
  isTaskOpen = false,
  onApplyPlan,
  onOpenTask,
  onClose,
}) => {
  const [tasks, setTasks] = useState([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const sentinelRef = useRef(null);
  const requestIdRef = useRef(0);
  const loadingRef = useRef(false);

  const days = useMemo(() => groupTasksByDay(tasks), [tasks]);
  const hasMore = tasks.length < total;

  /* Задачу открываем поверх окна: окно живёт ниже карточки задачи по z-index
     (COLUMN_BROWSER_Z < .tv-drawer), поэтому закрывать его не нужно — закрыл
     задачу и остался там же, где смотрел. */

  const loadPage = useCallback(async (offset) => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setIsLoading(true);
    let result = null;
    try {
      result = await loadTasks({
        scope,
        mode: 'board',
        sort,
        column: column.id,
        limit: COLUMN_BROWSER_PAGE,
        offset,
        withSummary: false,
      });
    } finally {
      loadingRef.current = false;
      if (requestIdRef.current === requestId) setIsLoading(false);
    }
    if (requestIdRef.current !== requestId) return;
    if (!result) {
      setFailed(true);
      return;
    }
    const incoming = Array.isArray(result.tasks) ? result.tasks : [];
    setTotal(Number(result.total || 0));
    setTasks((prev) => (offset === 0
      ? incoming
      // Страницы могли сдвинуться, пока листали, — склеиваем по id.
      : [...new Map([...prev, ...incoming].map((task) => [task.id, task])).values()]));
  }, [loadTasks, scope, sort, column.id]);

  useEffect(() => { loadPage(0); }, [loadPage]);

  // Догрузка по прокрутке: следим за маячком в конце списка, а не за скроллом
  // конкретного контейнера — окно рисуется в общей полноэкранной обёртке.
  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || !hasMore || failed) return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !loadingRef.current) loadPage(tasks.length);
    }, { rootMargin: '240px' });
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasMore, failed, loadPage, tasks.length]);

  return createPortal(
    <FullscreenSheet
      open
      wide
      z={COLUMN_BROWSER_Z}
      // Esc при открытой задаче закрывает задачу, а не оба слоя сразу.
      closeOnEscape={!isTaskOpen}
      icon="fa-layer-group"
      title={column.title}
      subtitle={total > 0
        ? `${tasks.length} из ${total} ${pluralTasks(total)} · Esc чтобы выйти`
        : 'Esc чтобы выйти'}
      onClose={onClose}
    >
      <div className="tb-scope space-y-5" style={{ fontFamily: APPLE_FONT }}>
        {days.length === 0 && !isLoading && (
          <EmptyState title="Пусто" hint={`В колонке «${column.title}» сейчас нет задач.`} />
        )}

        {days.map((day) => (
          <section key={day.key}>
            <header className="mb-2 flex items-baseline gap-2 px-0.5">
              <h4 className="text-[13px] font-semibold text-slate-800">{day.label}</h4>
              <span className="text-[11.5px] tabular-nums text-slate-400">
                {day.tasks.length} {pluralTasks(day.tasks.length)}
              </span>
            </header>
            <div className="-mx-1 flex gap-2.5 overflow-x-auto px-1 pb-2">
              {day.tasks.map((task) => (
                <div key={task.id} className="w-[268px] shrink-0">
                  <BoardCard
                    task={task}
                    canPlan={canPlan}
                    actionNeedOf={actionNeedOf}
                    onOpen={onOpenTask}
                    onApplyPlan={onApplyPlan}
                  />
                </div>
              ))}
            </div>
          </section>
        ))}

        <div ref={sentinelRef} className="h-px" />

        {isLoading && (
          <div className="flex gap-2.5">
            {[0, 1, 2].map((index) => (
              <div key={index} className="h-20 w-[268px] shrink-0 animate-pulse rounded-xl bg-slate-200/70" />
            ))}
          </div>
        )}

        {failed && (
          <p className="px-1 text-[12px] text-rose-500">
            Не удалось загрузить продолжение. Закройте окно и откройте снова.
          </p>
        )}

        {!hasMore && !isLoading && days.length > 0 && (
          <p className="px-1 pb-2 text-[11.5px] text-slate-400">Это все задачи в колонке.</p>
        )}
      </div>
    </FullscreenSheet>,
    document.body
  );
};

/* ─────────────── Канбан ─────────────── */

const BoardCard = ({
  task,
  canPlan,
  focusPersonId = 0,
  isFocused = false,
  isDragging,
  actionNeedOf,
  onOpen,
  onApplyPlan,
  onDragStart,
  onDragEnd,
}) => {
  const [planOpen, setPlanOpen] = useState(false);
  const planAnchorRef = useRef(null);
  const cardRef = useRef(null);
  const tone = dueTone(task);
  const effort = effortChipOf(task);
  const checklist = checklistProgress(task);
  const reportCount = Array.isArray(task?.reports) ? task.reports.length : 0;
  const actionNeed = actionNeedOf?.(task) || null;

  // Переход из уведомления: карточку надо не только подсветить, но и показать —
  // колонки скроллятся и по горизонтали, и по вертикали.
  useEffect(() => {
    if (!isFocused) return;
    cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
  }, [isFocused]);

  const isDraggable = typeof onDragStart === 'function';

  return (
    <div
      ref={cardRef}
      draggable={isDraggable}
      onDragStart={isDraggable ? (event) => onDragStart(event, task) : undefined}
      onDragEnd={onDragEnd}
      onClick={() => onOpen(task)}
      className={`group relative cursor-pointer rounded-xl bg-white p-2.5 ring-1 ring-slate-200/70 transition hover:ring-slate-300 hover:shadow-[0_2px_10px_rgba(15,23,42,0.06)] ${
        isDragging ? 'opacity-40' : ''
      } ${isFocused ? 'tb-card-focus' : ''}`}
    >
      <div className="flex items-start gap-1.5">
        <span className="mt-[6px]"><PriorityDot priority={task?.priority} /></span>
        <p className="min-w-0 flex-1 text-[13px] font-medium leading-snug text-slate-800 line-clamp-2">
          {task?.subject}
        </p>
        <button
          ref={planAnchorRef}
          type="button"
          title="Оценка и срок"
          onClick={(event) => { event.stopPropagation(); setPlanOpen((prev) => !prev); }}
          className="-mr-1 -mt-1 grid h-6 w-6 shrink-0 place-items-center rounded-lg text-slate-300 opacity-0 transition hover:bg-slate-100 hover:text-slate-600 focus:opacity-100 group-hover:opacity-100"
        >
          <ClockIcon />
        </button>
      </div>

      <div className="mt-2 flex items-center gap-1.5">
        <CardFaces task={task} focusPersonId={focusPersonId} />
        <span className="flex flex-1 flex-wrap items-center gap-1">
          {task?.due_at && (
            <MetaChip tone={tone} title={`Дедлайн: ${formatDateTimeLabel(task.due_at)}`}>
              <FlagIcon />
              {formatShortDate(task.due_at)}
            </MetaChip>
          )}
          {effort && (
            <MetaChip tone={effort.tone} title={effort.title}>
              <ClockIcon />
              {effort.label}
            </MetaChip>
          )}
          {checklist && (
            <span className="text-[11px] tabular-nums text-slate-400" title="Чек-лист">
              {checklist.done}/{checklist.total}
            </span>
          )}
          {reportCount > 0 && (
            <span className="text-[11px] tabular-nums text-slate-400" title={`Отчётов о работе: ${reportCount}`}>
              <ReportIcon />
            </span>
          )}
        </span>
        {task?.status === 'returned' && (
          <span className="rounded-md bg-rose-50 px-1.5 py-[2px] text-[10.5px] font-semibold text-rose-600">
            Возврат
          </span>
        )}
        {actionNeed && !actionNeed.seen && <ActionAtMarker kind={actionNeed.kind} />}
      </div>

      {planOpen && (
        <PlanPopover
          task={task}
          canPlan={canPlan(task)}
          anchorRef={planAnchorRef}
          onApply={(patch) => onApplyPlan(task, patch)}
          onClose={() => setPlanOpen(false)}
        />
      )}
    </div>
  );
};

const BoardView = ({
  tasksByColumn,
  canPlan,
  focusPersonId = 0,
  focusTaskId = 0,
  actionNeedOf,
  columnMeta = {},
  onBrowseColumn,
  wipLimit,
  onWipLimitChange,
  onOpen,
  onApplyPlan,
  onDrop,
}) => {
  const [dragged, setDragged] = useState(null);
  const [hoverColumn, setHoverColumn] = useState(null);

  const allowedColumns = useMemo(() => {
    if (!dragged) return null;
    const allowed = new Set();
    BOARD_COLUMNS.forEach((column) => {
      if (dragged.resolve(column.id).type === 'board' || dragged.resolve(column.id).type === 'status') {
        allowed.add(column.id);
      }
    });
    return allowed;
  }, [dragged]);

  const handleDragStart = (event, task, resolve) => {
    setDragged({ task, resolve });
    event.dataTransfer.effectAllowed = 'move';
    try { event.dataTransfer.setData('text/plain', String(task.id)); } catch (error) { /* Safari */ }
  };

  const handleDragEnd = () => {
    setDragged(null);
    setHoverColumn(null);
  };

  const inProgressCount = Number(columnMeta.progress?.total ?? (tasksByColumn.progress?.length || 0));
  const wipExceeded = Number(wipLimit) > 0 && inProgressCount > Number(wipLimit);

  return (
    <div className="-mx-1 overflow-x-auto px-1 pb-1">
      <div className="flex min-w-max gap-2.5">
        {BOARD_COLUMNS.map((column) => {
          const items = tasksByColumn[column.id] || [];
          const meta = columnMeta[column.id] || { total: items.length, hidden: 0, loading: false };
          const isAllowed = !allowedColumns || allowedColumns.has(column.id);
          const isHover = hoverColumn === column.id && isAllowed;
          const isProgress = column.id === 'progress';

          return (
            <section
              key={column.id}
              onDragOver={(event) => {
                if (!dragged) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = isAllowed ? 'move' : 'none';
                setHoverColumn(column.id);
              }}
              onDragLeave={() => setHoverColumn((prev) => (prev === column.id ? null : prev))}
              onDrop={(event) => {
                event.preventDefault();
                const payload = dragged;
                handleDragEnd();
                if (payload) onDrop(payload.task, column.id);
              }}
              className={`flex w-[268px] shrink-0 flex-col rounded-2xl p-2 transition-colors ${
                isHover ? 'bg-blue-50/70 ring-1 ring-blue-200' : 'bg-slate-100/70'
              } ${dragged && !isAllowed ? 'opacity-50' : ''}`}
            >
              <header className="flex items-center justify-between gap-2 px-1.5 pb-2 pt-1">
                <span className="flex items-baseline gap-1.5">
                  <span className="text-[12.5px] font-semibold text-slate-700">{column.title}</span>
                  <span
                    className={`text-[11.5px] tabular-nums ${wipExceeded && isProgress ? 'font-semibold text-amber-600' : 'text-slate-400'}`}
                    title={meta.hidden > 0 ? `Показано ${items.length} из ${meta.total}` : undefined}
                  >
                    {meta.total || items.length}
                    {isProgress && Number(wipLimit) > 0 && `/${wipLimit}`}
                  </span>
                </span>
                {isProgress && (
                  <span className="flex items-center gap-0.5">
                    <button
                      type="button"
                      title="Уменьшить лимит WIP"
                      onClick={() => onWipLimitChange(Math.max(0, Number(wipLimit || 0) - 1))}
                      className="grid h-5 w-5 place-items-center rounded-md text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                    >
                      −
                    </button>
                    <button
                      type="button"
                      title="Увеличить лимит WIP"
                      onClick={() => onWipLimitChange(Number(wipLimit || 0) + 1)}
                      className="grid h-5 w-5 place-items-center rounded-md text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                    >
                      +
                    </button>
                  </span>
                )}
              </header>

              {wipExceeded && isProgress && (
                <p className="mb-1.5 px-1.5 text-[11px] leading-snug text-amber-600">
                  Превышен лимит одновременной работы — закройте начатое, прежде чем брать новое.
                </p>
              )}

              <div className="flex min-h-[72px] flex-col gap-1.5">
                {items.length === 0 ? (
                  <p className="px-1.5 py-3 text-[11.5px] text-slate-400">{column.caption}</p>
                ) : (
                  items.map((entry) => (
                    <BoardCard
                      key={entry.task.id}
                      task={entry.task}
                      canPlan={canPlan}
                      focusPersonId={focusPersonId}
                      isFocused={focusTaskId === entry.task.id}
                      actionNeedOf={actionNeedOf}
                      isDragging={dragged?.task?.id === entry.task.id}
                      onOpen={onOpen}
                      onApplyPlan={onApplyPlan}
                      onDragStart={(event, task) => handleDragStart(event, task, entry.resolve)}
                      onDragEnd={handleDragEnd}
                    />
                  ))
                )}

                {meta.hidden > 0 && (
                  <button
                    type="button"
                    onClick={() => onBrowseColumn?.(column)}
                    className="mt-0.5 rounded-lg border border-dashed border-slate-300 px-2 py-2 text-[11.5px] font-medium text-slate-500 transition hover:border-slate-400 hover:bg-white hover:text-slate-700"
                  >
                    Посмотреть ещё · не показано {meta.hidden}
                  </button>
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
};

/* ─────────────── Таймлайн ─────────────── */

const TIMELINE_RANGES = [
  { value: 'week',    label: 'Неделя',  days: 7 },
  { value: 'month',   label: 'Месяц',   days: 30 },
  { value: 'quarter', label: 'Квартал', days: 90 },
];

const median = (values) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};

/** Плановый и фактический отрезки задачи. Плановый старт по умолчанию — постановка. */
export const timelineSpanOf = (task, now = Date.now()) => {
  const plannedStart = parseDate(task?.planned_start_at) || parseDate(task?.created_at);
  const plannedEnd = parseDate(task?.due_at);
  const actualStart = actualStartOf(task);
  const actualEnd = actualEndOf(task);
  const actualFinish = actualStart ? (actualEnd || new Date(now)) : null;
  return {
    plannedStart,
    plannedEnd,
    actualStart,
    actualEnd,
    actualFinish,
    actualMs: actualStart && actualFinish ? actualFinish.getTime() - actualStart.getTime() : null,
    leadMs: plannedStart && actualEnd ? actualEnd.getTime() - plannedStart.getTime() : null,
  };
};

const TimelineView = ({ tasks, onOpen }) => {
  const [range, setRange] = useState('month');
  const [expanded, setExpanded] = useState(false);
  const now = Date.now();

  const rangeDays = TIMELINE_RANGES.find((item) => item.value === range)?.days || 30;
  const windowStart = useMemo(() => startOfDay(addDays(new Date(now), -Math.round(rangeDays / 3))), [now, rangeDays]);
  const windowEnd = useMemo(() => addDays(windowStart, rangeDays), [windowStart, rangeDays]);
  const windowMs = windowEnd.getTime() - windowStart.getTime();

  const rows = useMemo(() => {
    return tasks
      .map((task) => ({ task, span: timelineSpanOf(task, now) }))
      .filter(({ span }) => {
        const from = span.actualStart || span.plannedStart;
        const to = span.actualFinish || span.plannedEnd || from;
        if (!from || !to) return false;
        return to.getTime() >= windowStart.getTime() && from.getTime() <= windowEnd.getTime();
      })
      .sort((a, b) => {
        const aStart = (a.span.actualStart || a.span.plannedStart)?.getTime() || 0;
        const bStart = (b.span.actualStart || b.span.plannedStart)?.getTime() || 0;
        return aStart - bStart;
      });
  }, [tasks, now, windowStart, windowEnd]);

  const metrics = useMemo(() => {
    const cycleTimes = [];
    const leadTimes = [];
    const estimateRatios = [];
    let onTime = 0;
    let withDue = 0;
    tasks.forEach((task) => {
      const span = timelineSpanOf(task, now);
      if (span.actualStart && span.actualEnd) cycleTimes.push(span.actualEnd.getTime() - span.actualStart.getTime());
      if (span.leadMs !== null) leadTimes.push(span.leadMs);
      if (span.plannedEnd && span.actualEnd) {
        withDue += 1;
        if (span.actualEnd.getTime() <= span.plannedEnd.getTime()) onTime += 1;
      }
      const estimate = Number(task?.estimate_minutes) || 0;
      const spent = Number(task?.spent_minutes) || 0;
      if (estimate > 0 && spent > 0) estimateRatios.push(spent / estimate);
    });
    return {
      cycle: median(cycleTimes),
      lead: median(leadTimes),
      onTimeRate: withDue ? Math.round((onTime / withDue) * 100) : null,
      withDue,
      // Медиана факт/оценка: 100% — оценки честные, больше — систематически недооцениваем.
      estimateAccuracy: estimateRatios.length ? Math.round(median(estimateRatios) * 100) : null,
      estimatedCount: estimateRatios.length,
    };
  }, [tasks, now]);

  const ticks = useMemo(() => {
    const step = rangeDays <= 7 ? 1 : rangeDays <= 30 ? 5 : 15;
    const result = [];
    for (let day = 0; day <= rangeDays; day += step) {
      const date = addDays(windowStart, day);
      result.push({ day, date, left: (day * DAY) / windowMs });
    }
    return result;
  }, [rangeDays, windowStart, windowMs]);

  const nowLeft = (now - windowStart.getTime()) / windowMs;

  const barGeometry = (from, to) => {
    if (!from || !to) return null;
    const startMs = Math.max(from.getTime(), windowStart.getTime());
    const endMs = Math.min(Math.max(to.getTime(), from.getTime() + HOUR), windowEnd.getTime());
    if (endMs <= windowStart.getTime() || startMs >= windowEnd.getTime()) return null;
    const left = ((startMs - windowStart.getTime()) / windowMs) * 100;
    const width = Math.max(0.6, ((endMs - startMs) / windowMs) * 100);
    return { left: `${left}%`, width: `${width}%` };
  };

  const content = (
    <div className="space-y-3" style={{ fontFamily: APPLE_FONT }}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-4">
          <span className="text-[12px] text-slate-500">
            Медианный цикл{' '}
            <b className="text-[13px] font-semibold text-slate-800">
              {metrics.cycle === null ? '—' : formatDurationMs(metrics.cycle)}
            </b>
          </span>
          <span className="text-[12px] text-slate-500">
            От постановки до приёмки{' '}
            <b className="text-[13px] font-semibold text-slate-800">
              {metrics.lead === null ? '—' : formatDurationMs(metrics.lead)}
            </b>
          </span>
          <span className="text-[12px] text-slate-500">
            В срок{' '}
            <b className={`text-[13px] font-semibold ${
              metrics.onTimeRate === null ? 'text-slate-800'
                : metrics.onTimeRate >= 80 ? 'text-emerald-600'
                : metrics.onTimeRate >= 50 ? 'text-amber-600' : 'text-rose-600'
            }`}>
              {metrics.onTimeRate === null ? '—' : `${metrics.onTimeRate}%`}
            </b>
            {metrics.withDue > 0 && <span className="text-slate-400"> из {metrics.withDue}</span>}
          </span>
          {metrics.estimateAccuracy !== null && (
            <span
              className="text-[12px] text-slate-500"
              title="Медиана «факт по отчётам / оценка». Больше 100% — работу систематически недооценивают."
            >
              Факт к оценке{' '}
              <b className={`text-[13px] font-semibold ${
                metrics.estimateAccuracy <= 120 ? 'text-slate-800' : 'text-amber-600'
              }`}>
                {metrics.estimateAccuracy}%
              </b>
              <span className="text-slate-400"> по {metrics.estimatedCount}</span>
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <SegmentedControl
            value={range}
            onChange={setRange}
            options={TIMELINE_RANGES.map(({ value, label }) => ({ value, label }))}
          />
          <button
            type="button"
            onClick={() => setExpanded((prev) => !prev)}
            title={expanded ? 'Свернуть' : 'Развернуть на весь экран'}
            className="grid h-[32px] w-[32px] place-items-center rounded-[10px] bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-95"
          >
            {expanded
              ? <Minimize2 size={14} strokeWidth={2} />
              : <Maximize2 size={14} strokeWidth={2} />}
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title="В этом окне нет задач"
          hint="Таймлайн показывает задачи, у которых есть плановый срок или зафиксировано начало работ."
        />
      ) : (
        <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
          <div className="flex border-b border-slate-100 bg-slate-50/60">
            <div className="w-[190px] shrink-0 px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400 sm:w-[240px]">
              Задача
            </div>
            <div className="relative flex-1">
              {ticks.map((tick) => (
                <span
                  key={tick.day}
                  className="absolute top-1/2 -translate-y-1/2 whitespace-nowrap pl-1 text-[10.5px] tabular-nums text-slate-400"
                  style={{ left: `${tick.left * 100}%` }}
                >
                  {tick.date.toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' })}
                </span>
              ))}
            </div>
          </div>

          <div className={`overflow-y-auto ${expanded ? 'max-h-[calc(100vh-280px)]' : 'max-h-[520px]'}`}>
            {rows.map(({ task, span }) => {
              const planned = barGeometry(span.plannedStart, span.plannedEnd);
              const actual = barGeometry(span.actualStart, span.actualFinish);
              const overdue = span.plannedEnd
                && (span.actualEnd ? span.actualEnd > span.plannedEnd : now > span.plannedEnd.getTime())
                && task.status !== 'accepted';
              const isDone = task.status === 'accepted';

              return (
                <div
                  key={task.id}
                  onClick={() => onOpen(task)}
                  className="flex cursor-pointer border-t border-slate-100 transition first:border-t-0 hover:bg-slate-50/70"
                >
                  <div className="flex w-[190px] shrink-0 items-center gap-2 px-3 py-2 sm:w-[240px]">
                    <PriorityDot priority={task?.priority} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12.5px] font-medium text-slate-800">{task.subject}</span>
                      <span className="block truncate text-[11px] text-slate-400">{task?.assignee?.name || '—'}</span>
                    </span>
                  </div>

                  <div className="relative min-h-[42px] flex-1">
                    {ticks.map((tick) => (
                      <span
                        key={tick.day}
                        className="absolute inset-y-0 w-px bg-slate-100"
                        style={{ left: `${tick.left * 100}%` }}
                      />
                    ))}
                    {nowLeft >= 0 && nowLeft <= 1 && (
                      <span className="absolute inset-y-0 w-px bg-blue-400/70" style={{ left: `${nowLeft * 100}%` }} />
                    )}

                    {planned && (
                      <span
                        className="absolute top-[11px] h-[6px] rounded-full bg-slate-200"
                        style={planned}
                        title={`План: ${formatShortDate(span.plannedStart)} → ${formatShortDate(span.plannedEnd)}`}
                      />
                    )}
                    {actual && (
                      <span
                        className={`absolute top-[21px] h-[8px] rounded-full ${
                          isDone ? 'bg-emerald-400' : overdue ? 'bg-rose-400' : 'bg-slate-700'
                        }`}
                        style={actual}
                        title={`Факт: ${formatDateTimeLabel(span.actualStart)} → ${
                          span.actualEnd ? formatDateTimeLabel(span.actualEnd) : 'идёт'
                        }`}
                      />
                    )}
                    {span.actualMs !== null && actual && (
                      <span
                        className="absolute top-[18px] whitespace-nowrap pl-1.5 text-[10.5px] tabular-nums text-slate-400"
                        style={{ left: `calc(${actual.left} + ${actual.width})` }}
                      >
                        {formatDurationMs(span.actualMs)}
                      </span>
                    )}
                    {!actual && !planned && (
                      <span className="absolute top-[14px] pl-2 text-[11px] text-slate-300">нет дат</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-4 px-1 text-[11.5px] text-slate-400">
        <span className="flex items-center gap-1.5"><span className="h-[6px] w-6 rounded-full bg-slate-200" /> план</span>
        <span className="flex items-center gap-1.5"><span className="h-[8px] w-6 rounded-full bg-slate-700" /> факт</span>
        <span className="flex items-center gap-1.5"><span className="h-[8px] w-6 rounded-full bg-rose-400" /> просрочка</span>
        <span className="flex items-center gap-1.5"><span className="h-[8px] w-6 rounded-full bg-emerald-400" /> принято</span>
      </div>
    </div>
  );

  // Полный экран — портал мимо .tv-root, иначе раздел навязал бы свою типографику.
  if (expanded) {
    return createPortal(
      <FullscreenSheet
        open
        wide
        icon="fa-chart-gantt"
        title="Таймлайн задач"
        subtitle={`${rows.length} ${rows.length === 1 ? 'задача' : 'задач'} в окне · Esc чтобы выйти`}
        onClose={() => setExpanded(false)}
      >
        {content}
      </FullscreenSheet>,
      document.body
    );
  }

  return content;
};

/* ─────────────── Оболочка ─────────────── */

const WIP_STORAGE_KEY = 'otp.tasks.board.wipLimit';
const CHUNK_STORAGE_KEY = 'otp.tasks.board.chunkSize';

const emptyColumnState = () => Object.fromEntries(
  BOARD_COLUMNS.map((column) => [column.id, { tasks: [], total: 0, loading: true }])
);

const TaskBoardWorkspace = ({
  mode,
  people = [],
  loadTasks,
  reloadToken = 0,
  taskPatches = null,
  currentUserId,
  isAdmin,
  isSupervisor,
  focusRequest = null,
  actionNeedOf,
  isTaskOpen = false,
  onOpenTask,
  onStatusAction,
  onBoardUpdate,
  onCreateBacklogItem,
  notify,
}) => {
  const [scope, setScope] = useState('my');
  const [boardSort, setBoardSort] = useState('freshness');
  const [overrides, setOverrides] = useState({});
  const [page, setPage] = useState(1);
  const [chunkSize, setChunkSize] = useState(() => {
    if (typeof window === 'undefined') return DEFAULT_BOARD_CHUNK;
    return normalizeBoardChunk(window.localStorage.getItem(CHUNK_STORAGE_KEY));
  });
  const [pageTasks, setPageTasks] = useState([]);
  const [pageTotal, setPageTotal] = useState(0);
  const [boardSummary, setBoardSummary] = useState(null);
  const [isPageLoading, setIsPageLoading] = useState(true);
  const pageRequestIdRef = useRef(0);
  const [columnState, setColumnState] = useState(emptyColumnState);
  const [columnsResetToken, setColumnsResetToken] = useState(0);
  // Открытая колонка «в полный рост»: дни по вертикали, карточки по горизонтали.
  const [browsedColumn, setBrowsedColumn] = useState(null);
  const columnRequestIdRef = useRef({});
  const columnLoadedKeyRef = useRef({});
  const [wipLimit, setWipLimit] = useState(() => {
    if (typeof window === 'undefined') return 0;
    return Number(window.localStorage.getItem(WIP_STORAGE_KEY) || 0) || 0;
  });

  const isBoardMode = mode === 'board';

  /* Канбан грузится колонками: у каждой свой срез статусов, свой счётчик из базы
     и своя кнопка «Посмотреть ещё». Так остаток «сколько не показано» — честный,
     а не «сколько не попало в общую страницу».
     В колонке всегда ровно одна порция: копить карточки в узкой колонке смысла
     нет, весь хвост смотрят в окне статуса. */
  const fetchColumn = useCallback(async (columnId, { limit }) => {
    if (typeof loadTasks !== 'function') return;
    const requestId = (columnRequestIdRef.current[columnId] || 0) + 1;
    columnRequestIdRef.current[columnId] = requestId;
    setColumnState((prev) => ({ ...prev, [columnId]: { ...prev[columnId], loading: true } }));
    let result = null;
    try {
      result = await loadTasks({
        scope,
        mode: 'board',
        sort: boardSort,
        column: columnId,
        limit,
        offset: 0,
        // Сводка нужна одна на всю доску — её приносит первая колонка.
        withSummary: columnId === BOARD_COLUMNS[0].id,
      });
    } finally {
      // Даже если загрузка сорвалась, колонка обязана выйти из состояния «гружусь»,
      // иначе доска залипает в скелетоне.
      if (columnRequestIdRef.current[columnId] === requestId && !result) {
        setColumnState((prev) => ({
          ...prev,
          [columnId]: { ...prev[columnId], tasks: prev[columnId]?.tasks || [], loading: false },
        }));
      }
    }
    if (columnRequestIdRef.current[columnId] !== requestId || !result) return;
    setColumnState((prev) => ({
      ...prev,
      [columnId]: {
        tasks: Array.isArray(result.tasks) ? result.tasks : [],
        total: Number(result.total || 0),
        loading: false,
      },
    }));
    if (columnId === BOARD_COLUMNS[0].id) setBoardSummary(result.summary || null);
  }, [loadTasks, scope, boardSort]);

  const boardReloadKey = `${scope}|${boardSort}|${reloadToken}|${chunkSize}|${columnsResetToken}`;
  useEffect(() => {
    if (!isBoardMode) return;
    // В колонке всегда ровно одна порция: остальное смотрят в окне статуса.
    BOARD_COLUMNS.forEach((column) => fetchColumn(column.id, { limit: chunkSize }));
    // boardReloadKey собирает все причины перезагрузки колонок.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isBoardMode, boardReloadKey, fetchColumn]);

  /* Сброс обязан заканчиваться загрузкой. Раньше ключ перезагрузки собирался
     только из scope/sort/порции, поэтому повторный выбор того же значения
     обнулял колонки и оставлял доску в вечном скелетоне. */
  const resetColumns = useCallback(() => {
    setColumnState(emptyColumnState());
    setColumnsResetToken((prev) => prev + 1);
  }, []);

  /* Бэклог и таймлайн — один список, им хватает обычной страницы. */
  useEffect(() => {
    if (isBoardMode || typeof loadTasks !== 'function') return undefined;
    const requestId = pageRequestIdRef.current + 1;
    pageRequestIdRef.current = requestId;
    let cancelled = false;
    setIsPageLoading(true);
    loadTasks({ scope, mode, sort: boardSort, limit: chunkSize, offset: (page - 1) * chunkSize })
      .then((result) => {
        if (cancelled || pageRequestIdRef.current !== requestId) return;
        setPageTasks(Array.isArray(result?.tasks) ? result.tasks : []);
        setPageTotal(Number(result?.total || 0));
        setBoardSummary(result?.summary || null);
      })
      .finally(() => {
        if (!cancelled && pageRequestIdRef.current === requestId) setIsPageLoading(false);
      });
    return () => { cancelled = true; };
  }, [isBoardMode, loadTasks, scope, mode, boardSort, page, chunkSize, reloadToken]);

  // Смена вкладки (бэклог/доска/таймлайн) меняет выборку — начинаем сначала.
  // На первом рендере не сбрасываем: колонки и так грузятся с нуля.
  const previousModeRef = useRef(mode);
  useEffect(() => {
    if (previousModeRef.current === mode) return;
    previousModeRef.current = mode;
    setPage(1);
    resetColumns();
  }, [mode, resetColumns]);

  // Повторный выбор того же значения — не работа: ничего не сбрасываем и не грузим.
  const changeScope = useCallback((next) => {
    if (next === scope) return;
    setScope(next);
    setPage(1);
    resetColumns();
  }, [scope, resetColumns]);

  // Смена порядка меняет всю выдачу, а не только видимое — начинаем сначала.
  const changeBoardSort = useCallback((next) => {
    if (next === boardSort) return;
    setBoardSort(next);
    setPage(1);
    resetColumns();
  }, [boardSort, resetColumns]);

  const changeChunkSize = useCallback((next) => {
    const normalized = normalizeBoardChunk(next);
    if (normalized === chunkSize) return;
    setChunkSize(normalized);
    setPage(1);
    resetColumns();
    try { window.localStorage.setItem(CHUNK_STORAGE_KEY, String(normalized)); } catch (error) { /* private mode */ }
  }, [chunkSize, resetColumns]);

  // Страница могла уехать за конец списка (задачи закрыли) — подтягиваем обратно.
  useEffect(() => {
    const lastPage = Math.max(1, Math.ceil(pageTotal / chunkSize));
    if (page > lastPage) setPage(lastPage);
  }, [page, pageTotal, chunkSize]);

  /* Строки канбана — объединение загруженных колонок: перетаскивание и
     оптимистичные правки продолжают работать на общем списке. */
  const boardRows = useMemo(() => {
    if (!isBoardMode) return pageTasks;
    const byId = new Map();
    BOARD_COLUMNS.forEach((column) => {
      (columnState[column.id]?.tasks || []).forEach((task) => byId.set(task.id, task));
    });
    return [...byId.values()];
  }, [isBoardMode, pageTasks, columnState]);

  const columnMeta = useMemo(() => Object.fromEntries(BOARD_COLUMNS.map((column) => {
    const state = columnState[column.id] || {};
    const loaded = (state.tasks || []).length;
    const total = Number(state.total || 0);
    return [column.id, {
      total,
      loaded,
      hidden: Math.max(0, total - loaded),
      loading: Boolean(state.loading),
    }];
  })), [columnState]);

  const isColumnsLoading = useMemo(
    () => BOARD_COLUMNS.some((column) => columnState[column.id]?.loading),
    [columnState]
  );

  // Оптимистичные правки живут ровно до тех пор, пока сервер не подтвердит те же значения.
  useEffect(() => {
    setOverrides((prev) => {
      const keys = Object.keys(prev);
      if (!keys.length) return prev;
      const next = {};
      keys.forEach((key) => {
        const task = boardRows.find((item) => String(item.id) === key);
        if (!task) return;
        const patch = prev[key];
        const settled = Object.entries(patch).every(([field, value]) => task[field] === value);
        if (!settled) next[key] = patch;
      });
      return Object.keys(next).length === keys.length ? prev : next;
    });
  }, [boardRows]);

  const handleWipLimitChange = useCallback((value) => {
    const normalized = Math.max(0, Math.min(99, Number(value) || 0));
    setWipLimit(normalized);
    try { window.localStorage.setItem(WIP_STORAGE_KEY, String(normalized)); } catch (error) { /* private mode */ }
  }, []);

  const effectiveTasks = useMemo(
    () => boardRows.map((task) => {
      const patched = taskPatches?.[task.id] || task;
      return overrides[task.id] ? { ...patched, ...overrides[task.id] } : patched;
    }),
    [boardRows, taskPatches, overrides]
  );

  /* Список досок приходит с сервера: у кого есть задачи, кроме уволенных.
     Собирать его из загруженных карточек нельзя — доска грузится порциями. */
  const boardPeople = useMemo(() => people
    .filter((person) => Number(person?.id || 0) && Number(person.id) !== currentUserId)
    .sort((left, right) => {
      const leftDept = String(left.department || 'я');
      const rightDept = String(right.department || 'я');
      if (leftDept !== rightDept) {
        // Сотрудники без отдела — в конце списка, отдельной группой.
        if (!left.department) return 1;
        if (!right.department) return -1;
        return leftDept.localeCompare(rightDept, 'ru');
      }
      return String(left.name || '').localeCompare(String(right.name || ''), 'ru');
    }), [people, currentUserId]);

  const adminScopeOptions = useMemo(() => [
    { value: 'my', label: 'Моя доска' },
    { value: 'assigned', label: 'Задачи на мне' },
    { value: 'all', label: 'Все доски' },
    ...boardPeople.map((person) => ({
      value: `person:${person.id}`,
      label: person.name,
      groupLabel: person.department || 'Без отдела',
    })),
  ], [boardPeople]);

  // На чьей доске мы стоим: для персональной доски — выбранный сотрудник, иначе сам пользователь.
  // Это лицо в карточках не повторяем — вместо него показываем второго участника задачи.
  const focusPersonId = useMemo(() => {
    if (scope.startsWith('person:')) return Number(scope.slice('person:'.length) || 0);
    if (scope === 'all') return 0;
    return currentUserId;
  }, [scope, currentUserId]);

  const focusPerson = useMemo(() => {
    if (!scope.startsWith('person:')) return null;
    return boardPeople.find((person) => person.id === focusPersonId) || null;
  }, [scope, boardPeople, focusPersonId]);

  // Выборку по доске делает сервер (mine / person_id), клиенту фильтровать нечего.
  const scopedTasks = effectiveTasks;

  /* Счётчики шапки берём из серверной сводки: это итоги всей доски сотрудника,
     а не текущей страницы. */
  const focusStats = useMemo(() => {
    if (!focusPerson || !boardSummary) return null;
    const total = Number(boardSummary.total || 0);
    const accepted = Number(boardSummary.accepted || 0);
    return {
      open: Math.max(0, total - accepted),
      inProgress: Number(boardSummary.in_progress || 0) + Number(boardSummary.returned || 0),
      overdue: Number(boardSummary.overdue || 0),
      delegated: Number(boardSummary.delegated || 0),
    };
  }, [focusPerson, boardSummary]);

  const dropContext = useMemo(
    () => ({ currentUserId, isAdmin, isSupervisor }),
    [currentUserId, isAdmin, isSupervisor]
  );

  const canPlan = useCallback(
    (task) => isAdmin || Number(task?.creator?.id || 0) === currentUserId,
    [isAdmin, currentUserId]
  );

  const backlogTasks = useMemo(
    () => scopedTasks
      .filter((task) => task?.is_backlog)
      .sort((a, b) => {
        const aRank = Number.isFinite(a?.backlog_rank) ? a.backlog_rank : Number.POSITIVE_INFINITY;
        const bRank = Number.isFinite(b?.backlog_rank) ? b.backlog_rank : Number.POSITIVE_INFINITY;
        if (aRank !== bRank) return aRank - bRank;
        return new Date(b?.created_at || 0) - new Date(a?.created_at || 0);
      }),
    [scopedTasks]
  );

  // «Готово» — скользящее окно: принятое больше недели назад уезжает в архив колонки,
  // иначе доска со временем превращается в свалку выполненного.
  const tasksByColumn = useMemo(() => {
    const buckets = Object.fromEntries(BOARD_COLUMNS.map((column) => [column.id, []]));
    scopedTasks.forEach((task) => {
      const columnId = columnOfTask(task);
      if (!buckets[columnId]) return;
      buckets[columnId].push({ task, resolve: (toColumn) => resolveBoardDrop(task, toColumn, dropContext) });
    });
    const compareEntries = (left, right) => compareBoardTasks(left.task, right.task, boardSort);
    BOARD_COLUMNS.forEach((column) => buckets[column.id].sort(compareEntries));
    return buckets;
  }, [scopedTasks, dropContext, boardSort]);

  /* Переход из уведомления. Если карточки нет в загруженной порции — возвращаемся
     на свою доску: уведомления всегда про задачи пользователя, а сортировка по
     свежести держит их сверху. Подсветку гасим по таймеру: она нужна ровно для
     того, чтобы глаз нашёл карточку. */
  const [focusTaskId, setFocusTaskId] = useState(0);
  const focusTokenRef = useRef(0);
  useEffect(() => {
    const token = Number(focusRequest?.token || 0);
    const taskId = Number(focusRequest?.taskId || 0);
    if (!token || !taskId || focusTokenRef.current === token) return;
    focusTokenRef.current = token;
    if (!scopedTasks.some((task) => Number(task?.id || 0) === taskId)) {
      setScope('my');
      setPage(1);
    }
    setFocusTaskId(taskId);
  }, [focusRequest, scopedTasks]);

  useEffect(() => {
    if (!focusTaskId) return undefined;
    const timer = setTimeout(() => setFocusTaskId(0), 4000);
    return () => clearTimeout(timer);
  }, [focusTaskId]);

  const applyBoardPatch = useCallback(async (task, patch, optimistic = null) => {
    if (!patch || !Object.keys(patch).length) return;
    if (optimistic) setOverrides((prev) => ({ ...prev, [task.id]: { ...(prev[task.id] || {}), ...optimistic } }));
    const ok = await onBoardUpdate([{ task_id: task.id, ...patch }]);
    if (!ok && optimistic) {
      setOverrides((prev) => {
        const next = { ...prev };
        delete next[task.id];
        return next;
      });
    }
  }, [onBoardUpdate]);

  const handleDrop = useCallback((task, toColumn) => {
    const decision = resolveBoardDrop(task, toColumn, dropContext);
    if (decision.type === 'noop') return;
    if (decision.type === 'blocked') {
      notify?.(decision.reason, 'error');
      return;
    }
    if (decision.type === 'status') {
      onStatusAction(task, decision.action);
      return;
    }
    applyBoardPatch(task, decision.patch, decision.patch);
  }, [dropContext, notify, onStatusAction, applyBoardPatch]);

  const handleReorder = useCallback((task, rank) => {
    applyBoardPatch(task, { backlog_rank: rank }, { backlog_rank: rank });
  }, [applyBoardPatch]);

  const handlePromote = useCallback((task) => {
    handleDrop(task, 'todo');
  }, [handleDrop]);

  // Без оптимизма: сервер нормализует даты в свою таймзону, локальная ISO-строка
  // никогда не «сойдётся» с ответом и оверрайд залипнет с неверным сроком.
  const handleApplyPlan = useCallback((task, patch) => {
    if (!patch || !Object.keys(patch).length) return;
    applyBoardPatch(task, patch);
  }, [applyBoardPatch]);

  if (isBoardMode ? (isColumnsLoading && !boardRows.length) : (isPageLoading && !pageTasks.length)) {
    return (
      <div className="space-y-2">
        {[0, 1, 2].map((index) => (
          <div key={index} className="h-14 animate-pulse rounded-2xl bg-slate-100" />
        ))}
      </div>
    );
  }

  const scopeOptions = [
    { value: 'my', label: 'Мои' },
    { value: 'assigned', label: 'На мне' },
    { value: 'all', label: 'Все' },
  ];

  const chunkOptions = BOARD_CHUNK_SIZES.map((size) => ({ value: size, label: `по ${size}` }));
  const rangeFrom = pageTotal === 0 ? 0 : (page - 1) * chunkSize + 1;
  const rangeTo = Math.min(pageTotal, (page - 1) * chunkSize + pageTasks.length);
  const totalPages = Math.max(1, Math.ceil(pageTotal / chunkSize));

  return (
    <div className="tb-scope space-y-3" style={{ fontFamily: APPLE_FONT }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-1 flex-wrap items-center gap-2">
          {isAdmin ? (
            <CustomSelect
              className="w-full sm:w-[240px]"
              variant="ios"
              ariaLabel="Выбор доски сотрудника"
              value={scope}
              options={adminScopeOptions}
              onChange={changeScope}
              searchable={adminScopeOptions.length > 8}
              searchPlaceholder="Найти сотрудника…"
            />
          ) : (
            <SegmentedControl value={scope} options={scopeOptions} onChange={changeScope} />
          )}
          {mode === 'board' && (
            <CustomSelect
              className="w-full sm:w-[170px]"
              variant="ios"
              ariaLabel="Сортировка карточек"
              value={boardSort}
              options={BOARD_SORT_OPTIONS}
              onChange={changeBoardSort}
            />
          )}
          <CustomSelect
            className="w-[110px]"
            variant="ios"
            ariaLabel={isBoardMode ? 'Карточек в колонке' : 'Карточек на странице'}
            value={chunkSize}
            options={chunkOptions}
            onChange={changeChunkSize}
          />
        </div>
        {mode === 'board' && (
          <span className="text-[11.5px] text-slate-400">
            Перетащите карточку между колонками, чтобы сменить статус
          </span>
        )}
      </div>

      {focusPerson && focusStats && (
        <BoardPersonHeader person={focusPerson} stats={focusStats} onReset={() => changeScope('my')} />
      )}

      {mode === 'backlog' && (
        <BacklogView
          tasks={backlogTasks}
          canPlan={canPlan}
          onOpen={onOpenTask}
          onPromote={handlePromote}
          onApplyPlan={handleApplyPlan}
          onReorder={handleReorder}
          onCreate={onCreateBacklogItem}
        />
      )}

      {mode === 'board' && (
        <BoardView
          tasksByColumn={tasksByColumn}
          canPlan={canPlan}
          focusPersonId={focusPersonId}
          focusTaskId={focusTaskId}
          actionNeedOf={actionNeedOf}
          columnMeta={columnMeta}
          onBrowseColumn={setBrowsedColumn}
          wipLimit={wipLimit}
          onWipLimitChange={handleWipLimitChange}
          onOpen={onOpenTask}
          onApplyPlan={handleApplyPlan}
          onDrop={handleDrop}
        />
      )}

      {mode === 'timeline' && (
        <TimelineView tasks={scopedTasks} onOpen={onOpenTask} />
      )}

      {browsedColumn && (
        <ColumnBrowser
          column={browsedColumn}
          scope={scope}
          sort={boardSort}
          loadTasks={loadTasks}
          actionNeedOf={actionNeedOf}
          canPlan={canPlan}
          isTaskOpen={isTaskOpen}
          onApplyPlan={handleApplyPlan}
          onOpenTask={onOpenTask}
          onClose={() => setBrowsedColumn(null)}
        />
      )}

      {!isBoardMode && (
        <BoardPager
          from={rangeFrom}
          to={rangeTo}
          total={pageTotal}
          page={page}
          totalPages={totalPages}
          isLoading={isPageLoading}
          onPageChange={setPage}
        />
      )}
    </div>
  );
};

export default TaskBoardWorkspace;
